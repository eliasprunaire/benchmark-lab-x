"""Exécuteur Linux du produit et protocole socket, sans admission ni appel implicite au démarrage.

Contrat de délais du relais. Le budget d'une requête relayée dérive du budget fournisseur :
`RELAY_BUDGET_SECONDS` couvre le pire enchaînement admis par l'exécuteur, l'échange puis la
vérification d'un rappel OpenRouter, augmenté du travail local. Sur la socket Unix il
s'applique en délai total : le budget monotone restant est réparti sur la connexion, l'envoi
et chaque réception de la réponse, qu'un délai d'inactivité ne bornerait pas. `executor_health`
garde le seul budget local : la santé ne doit pas attendre derrière un échange fournisseur.

Limites assumées. L'exécuteur reste sériel et le serveur web mono-thread : une requête lente
retarde les suivantes, `executor_health` peut expirer et `/readyz` répondre 503 pendant qu'un
rappel fournisseur occupe l'exécuteur. Le relais ne borne que son propre côté : la résolution
DNS du fournisseur, faite dans l'exécuteur et non interruptible, consomme le temps de l'appel
sans être majorée ici, donc `RELAY_BUDGET_SECONDS` n'est pas une garantie de durée totale de
bout en bout. Côté exécuteur, la lecture de la requête et l'écriture de la réponse gardent un
délai d'inactivité de 2 secondes, borné en octets mais pas en total : le seul client est le
web local.

Frontière d'erreurs. `executor_result` valide l'enveloppe et le type de ses champs avant tout
acheminement : une enveloppe fautive vaut 400, un refus reste 403 ou 409, une validation de
domaine reste 400, et une défaillance interne (stockage, programmation, entrée-sortie, santé)
répond 500 sans message d'exception, sans trace, sans requête ni secret. Côté client,
`preparation_request` traite une trame ou une réponse d'exécuteur illisible en
`ConnectionError` : c'est une panne de transport, jamais une saisie fautive de l'appelant. La
forme de la réponse y est vérifiée une seule fois, pour tous ses appelants : statut de réponse
finale, valeur objet ou chaîne hexadécimale de pièce, `piece` et `cookie` facultatifs mais
typés. Le web n'a donc plus à se défendre champ par champ, et une réponse hors contrat devient
une indisponibilité annoncée plutôt qu'un corps nul ou une page rompue.
"""
from contextlib import closing
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import signal
import socket
import socketserver
import sqlite3
import stat
import subprocess
import threading
import time

from .storage import ConflictError, BudgetError, IntegrityError, SchemaError, _unique_object

from .provider_access import CALLBACK_BUDGET_SECONDS, READ_CHUNK_BYTES, remaining_budget
from .storage import Store
from .runtime import encode, status, stop, verify


# Travail local d'une requête relayée : connexion, envoi et lecture d'une ligne bornée
LOCAL_BUDGET_SECONDS = 5
RELAY_BUDGET_SECONDS = CALLBACK_BUDGET_SECONDS + LOCAL_BUDGET_SECONDS

BAD_REQUEST_MESSAGE = 'Action non vérifiée. Vérifiez les champs ou consultez le dossier courant.'
CONFLICT_MESSAGE = ('Action refusée : révision périmée, opération en attente ou budget indisponible. '
                    'Consultez le dossier courant.')
# L'effet peut déjà être enregistré quand la défaillance survient : n'annoncer aucun résultat
INTERNAL_MESSAGE = ('Défaillance interne du service : l’état de cette action n’est pas confirmé. '
                    'Consultez le dossier avant tout nouvel envoi.')
PROTOCOL_MESSAGE = 'Réponse d’exécuteur illisible'


def release_identity():
    root = Path(__file__).resolve().parents[1]
    try:
        document = (root / 'release.json').read_text()
    except FileNotFoundError:
        document = None
    except (OSError, UnicodeError) as error:
        raise ValueError('Identité de release invalide') from error
    if document is not None:
        try:
            source = json.loads(document)['source_sha']
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError('Identité de release invalide') from error
        if type(source) is not str or re.fullmatch('[0-9a-f]{40}', source) is None:
            raise ValueError('Identité de release invalide')
        return source
    environment = dict(os.environ)
    environment['GIT_TERMINAL_PROMPT'] = '0'
    try:
        process = subprocess.Popen(
            ['git', '-C', str(root), 'rev-parse', '--show-toplevel', 'HEAD'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True, env=environment)
        try:
            stdout, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            return 'inconnu'
    except OSError:
        return 'inconnu'
    lines = stdout.splitlines()
    if (process.returncode != 0 or len(lines) != 2
            or Path(lines[0]).resolve() != root or re.fullmatch('[0-9a-f]{40}', lines[1]) is None):
        return 'inconnu'
    return lines[1]


def _read_line(connection, limit, deadline):
    """Une ligne bornée en octets et en temps : chaque réception reçoit le budget restant"""
    chunks, size = [], 0
    while size < limit:
        connection.settimeout(remaining_budget(deadline))
        chunk = connection.recv(min(READ_CHUNK_BYTES, limit - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if b'\n' in chunk:
            break
    head, separator, _ = b''.join(chunks).partition(b'\n')
    return head + separator


def executor_health(path):
    deadline = time.monotonic() + LOCAL_BUDGET_SECONDS
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(remaining_budget(deadline))
        connection.connect(str(path))
        connection.settimeout(remaining_budget(deadline))
        connection.sendall(b'health\n')
        raw = _read_line(connection, 4097, deadline)
        if len(raw) > 4096 or not raw.endswith(b'\n'):
            raise ValueError('Réponse de santé invalide')
        result = json.loads(raw)
        if set(result) != {'source_sha', 'storage', 'admission', 'restore_pending', 'operations'}:
            raise ValueError('Réponse de santé invalide')
        return result


def denied_response(error):
    generic = ('Cette action n’est pas autorisée pour votre session. Retrouvez votre dossier '
               'ou demandez au responsable de vérifier son autorisation.')
    if not error.code:
        return {'status': 403, 'value': {'error': generic}}
    messages = {
        'TEXT_TOO_SHORT': 'Ce texte est trop court.',
        'TEXT_TOO_LONG': 'Ce texte est trop long.',
        'PREPARATION_IN_PROGRESS': 'Une préparation est déjà en cours.',
        'TOO_SOON': 'Attendez avant un nouvel envoi.',
        'DAILY_SESSION_LIMIT': 'La limite quotidienne de dossiers est atteinte.',
        'SOURCE_RATE_LIMIT': 'La limite horaire de cette source est atteinte.',
        'SOURCE_MISSING': 'La source de cet envoi est absente ou invalide.',
        'DAILY_CAP': 'Le plafond quotidien de préparation est atteint.',
        'ACCESS_NO_PENDING': 'Aucune autorisation OpenRouter n’est en attente.',
        'ACCESS_EXCHANGE_FAILED': 'OpenRouter a refusé ou interrompu l’autorisation.',
        'ACCESS_REQUIRED': 'Un accès OpenRouter connecté est requis avant le lancement.',
        'NOT_QUALIFIED': 'Ce dossier doit être qualifié avant le lancement.',
        'CONTRACT_MISSING': "Le contrat de comparaison n'est pas encore établi. Terminez la qualification de l'exemple.",
        'STEP_INCOMPLETE': 'Terminez l’étape précédente avant de poursuivre.',
        'example_validated': 'Validez l’exemple présenté avant le lancement.',
        'example_qualified': 'La qualification de l’exemple est requise avant le lancement.',
        'configurations_available': 'Choisissez de nouveau les modèles indisponibles avant le lancement.',
        'access_connected': 'Connectez votre accès OpenRouter avant le lancement.',
        'estimate_under_cap': 'Augmentez le plafond ou choisissez d’autres configurations avant le lancement.',
        'QUALIFICATION_UNAVAILABLE': 'Qualification indisponible',
        'ADMISSION_CLOSED': 'Admission fermée',
    }
    status = 400 if error.code in ('TEXT_TOO_SHORT', 'TEXT_TOO_LONG', 'SOURCE_MISSING') else 403
    result = {'status': status, 'value': {'error': messages.get(error.code, generic),
              'error_code': error.code, 'error_field': error.field}}
    if error.findings is not None:
        result['value']['findings'] = error.findings
    if error.step is not None:
        result['value']['step'] = error.step
    if hasattr(error, 'provider_status'):
        result['value']['provider_status'] = error.provider_status
    return result


def _envelope(raw):
    """Structure et types de l'enveloppe, avant tout acheminement

    Contrat du protocole : `method` et `path` sont des chaînes, `token` une chaîne ou absent,
    `body` un objet ou absent. Ce qui passe ici ne peut plus lever de `KeyError` ni de
    `TypeError` d'acheminement, donc une telle erreur plus loin est une défaillance interne.
    """
    if len(raw) > 1048576 or not raw.endswith(b'\n'):
        raise ValueError('Enveloppe de requête hors limites ou incomplète')
    message = json.loads(raw, object_pairs_hook=_unique_object)
    if type(message) is not dict or set(message) != {'method', 'path', 'token', 'body'}:
        raise ValueError('Enveloppe de requête invalide')
    if (type(message['method']) is not str or type(message['path']) is not str
            or type(message['token']) not in (str, type(None))
            or type(message['body']) not in (dict, type(None))):
        raise ValueError('Champs d’enveloppe de requête invalides')
    return message


def _internal_result(error, code):
    """Journalise un code de diagnostic sûr : ni message d'exception, ni trace, ni requête"""
    logging.getLogger(__name__).error('EXECUTOR_INTERNAL %s %s', code, type(error).__name__)
    return {'status': 500, 'value': {'error': INTERNAL_MESSAGE}}


def executor_result(raw, health, handle):
    """Frontière d'erreurs de l'exécuteur : refus, enveloppe validée, puis défaillance interne"""
    from . import preparation
    if raw == b'health\n':
        try:
            return health()
        except Exception as error:
            # La santé n'a pas d'entrée à mettre en cause : toute rupture y est interne
            return _internal_result(error, 'HEALTH')
    try:
        message = _envelope(raw)
    except (ValueError, TypeError):
        return {'status': 400, 'value': {'error': BAD_REQUEST_MESSAGE}}
    try:
        return handle(message)
    except preparation.Denied as error:
        return denied_response(error)
    except (ConflictError, BudgetError):
        return {'status': 409, 'value': {'error': CONFLICT_MESSAGE}}
    except (IntegrityError, SchemaError, sqlite3.Error) as error:
        return _internal_result(error, 'STORAGE')
    except ValueError:
        # Validation de domaine de `preparation` : la requête est recevable mais fautive
        return {'status': 400, 'value': {'error': BAD_REQUEST_MESSAGE}}
    except Exception as error:
        # Enveloppe déjà validée : `KeyError` ou `TypeError` ici est une erreur de programmation
        return _internal_result(error, 'UNEXPECTED')


def serve_executor(data, socket_path, source, *, transport=None, qualification_transport=None,
                   candidate_transport=None, candidate_transport_factory=None,
                   candidate_identity=None,
                   access_secret=None, access_transport=None, presentation=None):
    data, socket_path = Path(data), Path(socket_path)
    with closing(Store(data)) as store:
        lock_fd = os.open(data / 'executor.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            verify(store)
            from .provider_access import expire
            expire(store)
            stop(data, store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
            if socket_path.exists() or socket_path.is_symlink():
                metadata = socket_path.lstat()
                if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
                    raise ValueError('Socket non détenue par le service')
                socket_path.unlink()

            def health():
                verify(store)
                return {'source_sha': source, 'storage': 'ok', **status(data, store)}

            def handle_message(message):
                from . import preparation, web_api
                code, value, cookie, start = web_api.dispatch(
                    store, message['method'], message['path'], message['token'], message['body'],
                    source, transport, candidate_transport=candidate_transport or candidate_transport_factory,
                    candidate_identity=candidate_identity,
                    qualification_transport=qualification_transport,
                    access_secret=access_secret, access_transport=access_transport,
                    presentation=presentation)
                if isinstance(start, dict):
                    if 'qualification_operation' in start:
                        threading.Thread(target=preparation.execute_qualification,
                                         args=(data, start['qualification_operation'], qualification_transport),
                                         daemon=True).start()
                    else:
                        from .campaigns import execute_launch
                        threading.Thread(target=execute_launch, args=(data, start['candidate_attempts'], candidate_transport),
                                         kwargs={'transport_factory': candidate_transport_factory,
                                                 'access_secret': access_secret,
                                                 'access_transport': access_transport}, daemon=True).start()
                elif start:
                    threading.Thread(target=preparation.execute, args=(data, start, transport), daemon=True).start()
                return {'status': code, 'value': value.hex() if isinstance(value, bytes) else value,
                        'piece': isinstance(value, bytes), 'cookie': cookie}

            class Handler(socketserver.StreamRequestHandler):
                def handle(self):
                    self.connection.settimeout(2)
                    try:
                        raw = self.rfile.readline(1048577)
                    except OSError:
                        return
                    result = executor_result(raw, health, handle_message)
                    try:
                        line = encode(result)
                    except (ValueError, TypeError) as error:
                        line = encode(_internal_result(error, 'ENCODE'))
                    try:
                        self.wfile.write((line + '\n').encode())
                    except OSError:
                        return

            with socketserver.UnixStreamServer(str(socket_path), Handler) as server:
                os.chmod(socket_path, 0o660)
                run(server)
            stop(data, store, 'PROCESS_STOPPED_ADMISSION_BLOCKED', after_process_exit=True)
        finally:
            os.close(lock_fd)


def _relayed_result(result):
    """Contrat de réponse d'exécuteur, vérifié avant tout acheminement vers le web

    `status` est un code de réponse finale, `value` un objet, ou une chaîne hexadécimale quand
    `piece` vaut vrai. `piece` et `cookie` sont facultatifs : une enveloppe de refus ou de
    défaillance les omet et doit rester rendue telle quelle, sans champ ajouté. Ce qui passe ici
    ne peut plus produire de corps nul, de page rompue ni de pièce indécodable côté web
    """
    if type(result) is not dict or type(result.get('status')) is not int:
        return False
    if not 200 <= result['status'] <= 599 or 'value' not in result:
        return False
    if type(result.get('piece', False)) is not bool:
        return False
    if type(result.get('cookie')) not in (str, type(None)):
        return False
    if result.get('piece'):
        return (type(result['value']) is str
                and re.fullmatch('(?:[0-9a-fA-F]{2})*', result['value']) is not None)
    return type(result['value']) is dict


def preparation_request(socket_path, method, path, token, body=None):
    """Relais borné par `RELAY_BUDGET_SECONDS` en délai total ; voir le contrat en tête de module"""
    deadline = time.monotonic() + RELAY_BUDGET_SECONDS
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(remaining_budget(deadline))
        connection.connect(str(socket_path))
        connection.settimeout(remaining_budget(deadline))
        connection.sendall((encode(dict(method=method, path=path, token=token, body=body)) + '\n').encode())
        raw = _read_line(connection, 8388609, deadline)
    # Trame, encodage ou forme illisibles : panne de transport, jamais une saisie de l'appelant
    if len(raw) > 8388608 or not raw.endswith(b'\n'):
        raise ConnectionError(PROTOCOL_MESSAGE)
    try:
        result = json.loads(raw)
    except ValueError:
        raise ConnectionError(PROTOCOL_MESSAGE) from None
    if not _relayed_result(result):
        raise ConnectionError(PROTOCOL_MESSAGE)
    return result


def run(server):
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    previous = {number: signal.signal(number, stop) for number in (signal.SIGTERM, signal.SIGINT)}
    # Une requête de santé locale est bornée à deux secondes pour permettre l'arrêt
    server.timeout = 0.25
    try:
        while not stopping:
            server.handle_request()
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
