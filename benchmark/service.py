"""Exécuteur Linux du produit et protocole socket, sans admission ni appel implicite au démarrage.

Contrat de délais du relais. Le budget d'une requête relayée dérive du budget fournisseur :
`RELAY_BUDGET_SECONDS` couvre le pire enchaînement admis par l'exécuteur, l'échange puis la
vérification d'un rappel OpenRouter, augmenté du travail local. Sur la socket Unix il
s'applique en délai total : le budget monotone restant est réparti sur la connexion, l'envoi
et chaque réception de la réponse, qu'un délai d'inactivité ne bornerait pas. `executor_health`
garde le seul budget local : la santé ne doit pas attendre derrière un échange fournisseur.

Limites assumées. L'exécuteur traite plusieurs requêtes à la fois, mais sa concurrence est
bornée : `EXECUTOR_WORKERS` fils de travail, chacun avec son propre `Store` ouvert une seule fois
au démarrage. Quand tous travaillent, la connexion suivante est fermée aussitôt plutôt que mise en
attente derrière `RELAY_BUDGET_SECONDS` ; `executor_health` peut alors échouer et `/readyz`
répondre 503. Les écritures restent sérialisées par SQLite : une écriture concurrente attend le
délai d'occupation de 5 secondes, puis échoue en `SQLITE_BUSY` plutôt que d'attendre davantage, et
la frontière la rend en 500. `preparation.submit` construit le corps sortant dans sa transaction
d'écriture (`transport.prepare`, local, sans réseau) ; l'appel fournisseur part ensuite dans
`preparation.execute`, hors de cette transaction. Le relais ne borne que son propre côté : la résolution
DNS du fournisseur, faite dans l'exécuteur et non interruptible, consomme le temps de l'appel
sans être majorée ici, donc `RELAY_BUDGET_SECONDS` n'est pas une garantie de durée totale de
bout en bout. Côté exécuteur, la lecture de la requête et l'écriture de la réponse gardent un
délai d'inactivité de 2 secondes, borné en octets mais pas en total : le seul client est le
web local.

Frontière d'erreurs. `executor_result` valide l'enveloppe et le type de ses champs avant tout
acheminement : une enveloppe fautive vaut 400, un refus reste 403 ou 409, une validation de
domaine reste 400, et une défaillance interne (stockage, programmation, entrée-sortie, santé)
répond 500 sans message d'exception, sans trace, sans requête ni secret. Un verrou de stockage occupé
n'y fait pas exception : `SQLITE_BUSY` reste un 500, parce qu'une requête enchaîne parfois plusieurs
transactions d'écriture, comme `provider_access.import_key` (intention, puis clé), et que la dernière
peut se refuser alors que la première est commise. Annoncer un refus y perdrait un effet déjà engagé.
L'activité, elle, est écrite dans la transaction de l'effet (`web_api._activity_with_effect`). Le verrou
de travail, lui, se prend avant tout acheminement : quand une maintenance (purge, sauvegarde) le tient
ou que la purge a fermé sa porte, la requête répond 503 `MAINTENANCE` sans avoir été traitée. Côté client,
`preparation_request` traite une trame ou une réponse d'exécuteur illisible en
`ConnectionError` : c'est une panne de transport, jamais une saisie fautive de l'appelant. La
forme de la réponse y est vérifiée une seule fois, pour tous ses appelants : statut de réponse
finale, valeur objet ou chaîne hexadécimale de pièce, `piece` et `cookie` facultatifs mais
typés. Le web n'a donc plus à se défendre champ par champ, et une réponse hors contrat devient
une indisponibilité annoncée plutôt qu'un corps nul ou une page rompue.
"""
from copy import copy
from contextlib import ExitStack, closing
from concurrent.futures import Future
from http.client import HTTPException
import fcntl
import json
import logging
import os
from pathlib import Path
import queue
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
from .privacy import Gone
from .storage import Store
from .runtime import encode, status, stop, verify


# Travail local d'une requête relayée : connexion, envoi et lecture d'une ligne bornée
LOCAL_BUDGET_SECONDS = 5
RELAY_BUDGET_SECONDS = CALLBACK_BUDGET_SECONDS + LOCAL_BUDGET_SECONDS

# Concurrence de production : un Store par fil, ouvert une fois et gardé jusqu'à l'arrêt. Cible de
# BX-27, 50 visiteurs simultanés et le plus lent sous 5 s. Relevé Linux : 56 fils suffisent, 64
# gardent une marge ; la latence suit le nombre de requêtes admises, pas le nombre de fils
EXECUTOR_WORKERS = 64
# La file d'écoute du noyau suit la concurrence : une rafale attend l'acceptation, jamais un refus
EXECUTOR_BACKLOG_FACTOR = 8
EXECUTOR_BACKLOG_MINIMUM = 64
EXECUTOR_START_SECONDS = 30
# Un fil de travail par Store ; chaque connexion SQLite appartient au fil qui l'a créée
_worker_store = threading.local()

BAD_REQUEST_MESSAGE = 'Action non vérifiée. Vérifiez les champs ou consultez le dossier courant.'
CONFLICT_MESSAGE = ('Action refusée : révision périmée, opération en attente ou budget indisponible. '
                    'Consultez le dossier courant.')
# L'effet peut déjà être enregistré quand la défaillance survient : n'annoncer aucun résultat
INTERNAL_MESSAGE = ('Défaillance interne du service : l’état de cette action n’est pas confirmé. '
                    'Consultez le dossier avant tout nouvel envoi.')
PROTOCOL_MESSAGE = 'Réponse d’exécuteur illisible'
MAINTENANCE_RESULT = {'status': 503, 'value': {
    'error': 'Service en maintenance : cette action n’a pas été traitée et aucune donnée n’a été modifiée. '
             'Réessayez dans quelques minutes.', 'error_code': 'MAINTENANCE', 'unavailable': True}}


def executor_workers():
    """Concurrence de l'exécuteur, ajustable sans toucher au code pour une mesure de charge"""
    raw = os.environ.get('BENCHMARK_EXECUTOR_WORKERS')
    if raw is None:
        return EXECUTOR_WORKERS
    if re.fullmatch('[1-9][0-9]{0,2}', raw) is None:
        raise ValueError('Concurrence d’exécuteur invalide')
    return int(raw)


class BoundedUnixServer(socketserver.UnixStreamServer):
    """Concurrence bornée : la boucle d'acceptation confie la connexion à un fil libre

    Le nombre de fils borne le travail simultané. Quand tous travaillent, la connexion est fermée
    sans réponse : le relais le lit comme une indisponibilité de transport et rend 503 tout de
    suite, au lieu d'attendre le budget de relais. `run` reste inchangée, donc le serveur web garde
    exactement la boucle d'aujourd'hui
    """

    def __init__(self, socket_path, handler, *, data, workers):
        self.request_queue_size = max(EXECUTOR_BACKLOG_MINIMUM, EXECUTOR_BACKLOG_FACTOR * workers)
        super().__init__(socket_path, handler)
        self._jobs = queue.SimpleQueue()
        self._free = threading.Semaphore(workers)
        self._stopped = False
        self._opened = queue.SimpleQueue()
        # Démons : une jointure bornée honore la requête en cours sans retenir le processus à jamais
        self._workers = [threading.Thread(target=self._work, args=(data,), name=f'executor-{index}',
                                          daemon=True) for index in range(workers)]
        try:
            for worker in self._workers:
                worker.start()
            for _ in self._workers:
                # Un Store qui ne s'ouvre pas rend la main tout de suite : pas d'attente du délai
                failure = self._opened.get(timeout=EXECUTOR_START_SECONDS)
                if failure is not None:
                    logging.getLogger(__name__).error('EXECUTOR_WORKER_UNAVAILABLE %s', failure)
                    raise IntegrityError('Fil de travail de l’exécuteur indisponible')
        except BaseException:
            self.server_close()
            raise

    def _work(self, data):
        try:
            store = Store(data)
        except BaseException as error:
            self._opened.put(type(error).__name__)
            raise
        # Une place perdue ne doit pas promettre une capacité absente au reste de la boucle
        with closing(store):
            _worker_store.store = store
            self._opened.put(None)
            while True:
                # Un fil qui disparaît laisserait sa place au sémaphore : la boucle survit à tout
                try:
                    job = self._jobs.get()
                    if job is None:
                        return
                    try:
                        try:
                            self.finish_request(*job)
                        except Exception:
                            self.handle_error(*job)
                    finally:
                        self.shutdown_request(job[0])
                        self._free.release()
                except Exception as error:
                    logging.getLogger(__name__).error('EXECUTOR_WORKER_RECOVERED %s',
                                                      type(error).__name__)

    def process_request(self, request, client_address):
        if not self._free.acquire(blocking=False):
            # Capacité saturée : fermeture immédiate, jamais une attente derrière le budget de relais
            self.shutdown_request(request)
            return
        self._jobs.put((request, client_address))

    def stop_workers(self):
        """Arrêt propre : la requête en cours se termine dans son budget, puis chaque Store est fermé

        Idempotent : `serve_executor` arrête le pool avant de fermer l'admission, et `server_close`
        repasse ici à la sortie du bloc. Sans ce garde, un fil déjà coincé ferait attendre deux
        budgets de relais au lieu d'un, en retenant `executor.lock` d'autant
        """
        if self._stopped:
            return
        self._stopped = True
        for _ in self._workers:
            self._jobs.put(None)
        deadline = time.monotonic() + RELAY_BUDGET_SECONDS
        for worker in self._workers:
            if worker.ident is None:
                continue
            worker.join(max(0.0, deadline - time.monotonic()))
            if worker.is_alive():
                logging.getLogger(__name__).error('EXECUTOR_WORKER_STUCK %s', worker.name)

    def server_close(self):
        # Filet : un abandon avant la boucle passe par ici, sinon `serve_executor` a déjà arrêté
        self.stop_workers()
        super().server_close()


def release_metadata():
    root = Path(__file__).resolve().parents[1]
    try:
        document = (root / 'release.json').read_text()
    except FileNotFoundError:
        document = None
    except (OSError, UnicodeError) as error:
        raise ValueError('Identité de release invalide') from error
    if document is not None:
        try:
            release = json.loads(document)
            source = release['source_sha']
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError('Identité de release invalide') from error
        if type(source) is not str or re.fullmatch('[0-9a-f]{40}', source) is None:
            raise ValueError('Identité de release invalide')
        version = release.get('version')
        if version is not None and (type(version) is not str or not re.fullmatch(
                r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?',
                version)):
            raise ValueError('Identité de release invalide')
        return {'source_sha': source, 'version': version}
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
            return {'source_sha': 'inconnu', 'version': None}
    except OSError:
        return {'source_sha': 'inconnu', 'version': None}
    lines = stdout.splitlines()
    if (process.returncode != 0 or len(lines) != 2
            or Path(lines[0]).resolve() != root or re.fullmatch('[0-9a-f]{40}', lines[1]) is None):
        return {'source_sha': 'inconnu', 'version': None}
    return {'source_sha': lines[1], 'version': None}


def release_identity():
    return release_metadata()['source_sha']


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
        expected = {'source_sha', 'storage', 'admission', 'restore_pending', 'operations'}
        if set(result) not in (expected, expected | {'version'}):
            raise ValueError('Réponse de santé invalide')
        return result


def denied_response(error):
    generic = ('Cette action n’est pas autorisée pour votre session. Retrouvez votre dossier '
               'ou demandez au responsable de vérifier son autorisation.')
    if error.code == 'NOT_FOUND':
        return {'status': 404, 'value': {'error': 'Ressource inaccessible', 'error_code': 'NOT_FOUND'}}
    if not error.code:
        return {'status': 403, 'value': {'error': generic}}
    messages = {
        'SESSION_EXPIRED': 'Votre accès au serveur a expiré. Vos copies locales restent consultables dans Mes données.',
        'RESTORE_PENDING': 'Accès temporairement fermé : une restauration doit être vérifiée.',
        'CONTRIBUTION_SENSITIVE_DATA': 'Un contenu potentiellement sensible empêche cette contribution. Votre benchmark reste accessible.',
        'TEXT_TOO_SHORT': 'Ce texte est trop court.',
        'TEXT_TOO_LONG': 'Ce texte est trop long.',
        'PREPARATION_IN_PROGRESS': 'Une préparation est déjà en cours.',
        'TOO_SOON': 'Attendez avant un nouvel envoi.',
        'SOURCE_RATE_LIMIT': 'La limite horaire de cette source est atteinte.',
        'SOURCE_MISSING': 'La source de cet envoi est absente ou invalide.',
        'ACCESS_KEY_REJECTED': 'Cette clé Openrouter n’a pas pu être vérifiée. La clé précédente est conservée.',
        'ACCESS_CAP_REQUIRED': 'Utilisez une clé Openrouter avec un plafond non renouvelable de 50 USD maximum et un solde disponible.',
        'ACCESS_NO_PENDING': 'Aucune autorisation Openrouter n’est en attente.',
        'ACCESS_EXCHANGE_FAILED': 'Openrouter a refusé ou interrompu l’autorisation.',
        'ACCESS_REQUIRED': 'Un accès Openrouter connecté est requis avant le lancement.',
        'NOT_QUALIFIED': 'Ce dossier doit être qualifié avant le lancement.',
        'CONTRACT_MISSING': "Le contrat de comparaison n'est pas encore établi. Terminez la qualification de l'exemple.",
        'STEP_INCOMPLETE': 'Terminez l’étape précédente avant de poursuivre.',
        'OUT_OF_SCOPE': 'Cette demande est hors du périmètre de Bench-X. Décrivez un autre cas d’usage pour continuer.',
        'example_validated': 'Validez l’exemple présenté avant le lancement.',
        'example_qualified': 'La qualification de l’exemple est requise avant le lancement.',
        'configurations_available': 'Choisissez de nouveau les modèles indisponibles avant le lancement.',
        'access_connected': 'Connectez votre accès Openrouter avant le lancement.',
        'estimate_available': 'Les coûts doivent pouvoir être estimés avant le lancement.',
        'QUALIFICATION_UNAVAILABLE': 'Qualification indisponible',
        'ADMISSION_CLOSED': 'Admission fermée',
        'PROBE_SLUG_INVALID': 'Copiez le slug exact de la fiche Openrouter, au format constructeur/modèle. Les URL et routeurs automatiques ne sont pas acceptés.',
        'PROBE_UNAVAILABLE': 'La vérification de modèles est indisponible.',
        'PROBE_CLOSED': 'Les nouveaux appels sont fermés. Aucun test de modèle n’a été lancé.',
        'PROBE_MODEL_UNAVAILABLE': 'Slug introuvable, modèle substitué ou endpoint texte incompatible. Aucun appel payant n’a été lancé. Vérifiez la fiche Openrouter.',
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
    if type(message) is not dict or not {'method', 'path', 'token', 'body'} <= set(message) or set(message) - {'method', 'path', 'token', 'body', 'management_token'}:
        raise ValueError('Enveloppe de requête invalide')
    if (type(message['method']) is not str or type(message['path']) is not str
            or type(message['token']) not in (str, type(None))
            or type(message['body']) not in (dict, type(None))
            or type(message.get('management_token')) not in (str, type(None))):
        raise ValueError('Champs d’enveloppe de requête invalides')
    return message


def _decoration_skipped(message, error):
    """Après un POST traité, une lecture de décoration illisible ne dément pas l'effet rendu par `dispatch`"""
    if message['method'] != 'POST':
        raise error
    logging.getLogger(__name__).warning('EXECUTOR_DECORATION_SKIPPED %s', type(error).__name__)


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
    except Gone:
        return {'status': 410, 'value': {'error': 'Ce cas d’usage n’est plus accessible sur le serveur.', 'error_code': 'DOSSIER_EXPIRED'}}
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


def personal_transports(store, token, preparation_transport, qualification_transport, secret, access_transport):
    from . import preparation, provider_access
    if secret is None or not provider_access.available(store):
        return None, None
    try:
        session_id, _, _ = preparation.session(store, token)
        budget_id = provider_access.preparation_budget_id(session_id)
        if not store._connection.execute('SELECT 1 FROM budgets WHERE budget_id=?', (budget_id,)).fetchone():
            return None, None
        key = provider_access.key_for_session(store, session_id, secret, access_transport)
    except preparation.Denied:
        return None, None
    return tuple(t.for_session(key, session_id, secret) if t is not None else None
                 for t in (preparation_transport, qualification_transport))


def needs_personal_transport(method, path):
    return method == 'POST' and (path == '/preparation/dossiers' or re.fullmatch(
        r'/preparation/dossiers/[A-Za-z0-9_-]+/(?:messages|validation|campaigns/[A-Za-z0-9_-]+/(?:start|evaluate))', path) is not None)


def personal_read_profiles(store, token, *profiles):
    """Les pages lisent la configuration sans charger de clé ni initialiser un transport"""
    from . import preparation, provider_access
    try:
        session_id, _, _ = preparation.session(store, token)
        if not provider_access.status_only(store, session_id)['connected']:
            return (None,) * len(profiles)
    except preparation.Denied:
        return (None,) * len(profiles)
    result = []
    for profile in profiles:
        bound = copy(profile) if profile is not None else None
        if bound is not None:
            bound._api_key = None
            bound._session_id = session_id
            bound.preparation_budget_id = provider_access.preparation_budget_id(session_id)
        result.append(bound)
    return tuple(result)


def _refresh_catalogue(data, stopping, fetch):
    from . import model_catalogue

    def fetch_unless_stopping(path):
        if stopping.is_set():
            raise InterruptedError('CATALOGUE_STOPPED')
        result = fetch(path)
        if stopping.is_set():
            raise InterruptedError('CATALOGUE_STOPPED')
        return result

    try:
        with closing(Store(data)) as store:
            if not store._connection_checked().execute(
                    "SELECT 1 FROM sqlite_schema WHERE name='s2_control'").fetchone():
                return
            while not stopping.is_set():
                try:
                    result = model_catalogue.refresh(store, fetch_unless_stopping)
                    if result['stale'] and not stopping.is_set():
                        logging.getLogger(__name__).warning('CATALOGUE_STALE')
                except (SchemaError, IntegrityError):
                    raise
                except (OSError, HTTPException, ValueError):
                    if not stopping.is_set():
                        logging.getLogger(__name__).warning('CATALOGUE_UNAVAILABLE')
                # Le cache décide du renouvellement ; une panne distante est réessayée à l'heure
                stopping.wait(3600)
    except Exception as error:
        logging.getLogger(__name__).error('CATALOGUE_INTERNAL %s', type(error).__name__)


def _probe_worker(future, data, request, fetch, secret, access_transport, transport):
    from . import model_probes, preparation
    from .runtime import maintenance_gate
    try:
        with closing(Store(data)) as store, maintenance_gate(store, wait=None):
            operation_id = model_probes.run(data, request['session_id'], request['dossier_id'],
                request['body'], fetch, secret, access_transport, transport)
        result = {'operation_id': operation_id}
    except preparation.Denied as error:
        result = {'error': denied_response(error)['value']['error']}
    except (BudgetError, ConflictError):
        result = {'error': 'Vérification refusée : budget disponible insuffisant ou opération déjà en cours.'}
    except Exception as error:
        result = {'error': _internal_result(error, 'MODEL_PROBE')['value']['error']}
    future.set_result(result)


def _campaign_worker(data, start, candidate_transport, factory, secret, access_transport, judge):
    from . import automatic_judgment as auto
    from .acquisition import campaigns, execution
    execution.execute_launch(data, start['candidate_attempts'], candidate_transport,
                             transport_factory=factory, access_secret=secret, access_transport=access_transport)
    if 'judgment_campaign' not in start:
        return
    try:
        with closing(Store(data)) as store:
            snapshot = campaigns.inspect(store, start['judgment_campaign'])
            if snapshot['admission'] is None:
                return
            ids = auto.reserve_campaign(store, start['session_id'], start['dossier_id'],
                                        start['judgment_campaign'], judge)
            if not ids and not any(a['output_piece_id'] for a in snapshot['attempts']):
                campaigns.stop(store, start['judgment_campaign'], reason='JUDGMENT_STOPPED')
        auto.execute_campaign(data, ids, judge)
    except Exception as error:
        with closing(Store(data)) as store:
            campaigns.stop(store, start['judgment_campaign'], reason='JUDGMENT_STOPPED')
        logging.getLogger(__name__).error('AUTOMATIC_JUDGMENT_STOPPED error=%s', type(error).__name__)


def _retention_worker(data, dossier_id, function, *args):
    from . import privacy, privacy_archive
    from .runtime import maintenance_gate, worker_lock
    # Porte tenue jusqu'à la fin : une purge attend la tâche, la tâche attend une purge commencée
    # Attente libre : la purge ne garde la porte que le temps de vider et purger, et un flock meurt avec son processus
    with closing(Store(data)) as store, maintenance_gate(store, wait=None), \
            worker_lock(store, shared=True):
        try:
            function(*args)
        finally:
            if dossier_id and privacy.available(store._connection):
                try:
                    privacy_archive.refresh_contributions(store, dossier_id)
                except Exception as error:
                    logging.getLogger(__name__).warning('CONTRIBUTION_UPDATE_PENDING error=%s', type(error).__name__)


def serve_executor(data, socket_path, source, *, version=None, transport=None, qualification_transport=None,
                   candidate_transport=None, candidate_transport_factory=None,
                   candidate_identity=None, judgment_transport=None,
                   access_secret=None, access_transport=None, presentation=None, personal_preparation=False,
                   catalogue_fetch=None, model_probe_transport=None):
    data, socket_path = Path(data), Path(socket_path)
    with closing(Store(data)) as store:
        lock_fd = os.open(data / 'executor.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            verify(store)
            from . import privacy
            if privacy.available(store._connection) and not privacy.quarantined(store):
                privacy.replay_revocations(store)
            from .provider_access import expire
            expire(store)
            stop(data, store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
            if socket_path.exists() or socket_path.is_symlink():
                metadata = socket_path.lstat()
                if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
                    raise ValueError('Socket non détenue par le service')
                socket_path.unlink()

            def health():
                # source_sha vient de l'exécuteur ; status() porte restauration, opérations et le contrôle de schéma
                # L'intégrité complète reste au démarrage et dans `runtime verify`
                # Le Store du fil : la connexion SQLite appartient au fil qui l'a créée
                health = {'source_sha': source, 'storage': 'ok', **status(data, _worker_store.store)}
                if version is not None:
                    health['version'] = version
                return health

            # Registre partagé par tous les fils de travail : ses lectures et écritures sont gardées
            probe_jobs, probe_guard = {}, threading.Lock()

            def handle_message(message):
                from .runtime import maintenance_gate, worker_lock
                worker = _worker_store.store
                with ExitStack() as held:
                    try:
                        with maintenance_gate(worker):
                            held.enter_context(worker_lock(worker, shared=True))
                    except BlockingIOError:
                        # Refus avant tout acheminement : rien n'a été écrit pour cette requête
                        return MAINTENANCE_RESULT
                    return handle_locked(worker, message)

            def handle_locked(store, message):
                from . import preparation, provider_access, web_api
                active_transport, active_qualification = transport, qualification_transport
                active_judgment = None
                if personal_preparation:
                    if needs_personal_transport(message['method'], message['path']):
                        active_transport, active_qualification = personal_transports(
                            store, message['token'], transport, qualification_transport, access_secret, access_transport)
                        if active_transport is not None and judgment_transport is not None:
                            active_judgment = judgment_transport.for_session(active_transport._api_key,
                                active_transport._session_id, access_secret)
                    else:
                        active_transport, active_qualification, active_judgment = personal_read_profiles(
                            store, message['token'], transport, qualification_transport, judgment_transport)
                code, value, cookie, start = web_api.dispatch(
                    store, message['method'], message['path'], message['token'], message['body'],
                    source, active_transport, candidate_transport=candidate_transport or candidate_transport_factory,
                    candidate_identity=candidate_identity,
                    qualification_transport=active_qualification,
                    judgment_transport=active_judgment,
                    access_secret=access_secret, access_transport=access_transport,
                    presentation=presentation, personal_preparation=personal_preparation,
                    management_token=message.get('management_token'))
                if personal_preparation and isinstance(value, dict):
                    value['personal_preparation'] = True
                    if 'availability' in value and active_transport is None:
                        value['availability'].update(can_submit=False, reason='access',
                                                     assistant_configured=transport is not None)
                    if code < 400 and value.get('kind') not in ('session_bootstrap', 'privacy_data', 'contributions'):
                        try:
                            session_id, _, _ = preparation.session(store, cookie or message['token'])
                            value['personal_access'] = provider_access.status_only(store, session_id)
                        except preparation.Denied:
                            pass
                        except (sqlite3.Error, SchemaError) as error:
                            _decoration_skipped(message, error)
                if isinstance(start, dict):
                    if 'model_probe' in start:
                        with probe_guard:
                            job = probe_jobs.get(start['session_id'])
                            if (job is not None and job['request_id'] == start['model_probe']
                                    and job['slug'] != start['body']['slug'].strip()):
                                raise ConflictError('Identité déjà utilisée avec un autre slug')
                            if job is None or job['request_id'] != start['model_probe']:
                                if any(not item['future'].done() for item in probe_jobs.values()):
                                    raise preparation.Denied('PREPARATION_IN_PROGRESS')
                                # Un seul résultat gratuit courant par session ; les appels restent dans le registre
                                job = dict(request_id=start['model_probe'], dossier_id=start['dossier_id'],
                                           slug=start['body']['slug'].strip(), future=Future())
                                probe_jobs[start['session_id']] = job
                                threading.Thread(target=_probe_worker, args=(job['future'], data, start,
                                    catalogue_fetch, access_secret, access_transport, model_probe_transport),
                                    daemon=True).start()
                    elif 'qualification_operation' in start:
                        threading.Thread(target=_retention_worker,
                                         args=(data, value.get('dossier_id'), preparation.execute_qualification,
                                               data, start['qualification_operation'], active_qualification),
                                         daemon=True).start()
                    elif 'judgment_operations' in start:
                        from .automatic_judgment import execute_campaign
                        threading.Thread(target=_retention_worker,
                            args=(data, value.get('dossier_id'), execute_campaign, data, start['judgment_operations'], active_judgment), daemon=True).start()
                    else:
                        threading.Thread(target=_retention_worker, args=(data, value.get('dossier_id'), _campaign_worker, data, start, candidate_transport,
                            candidate_transport_factory, access_secret, access_transport, active_judgment), daemon=True).start()
                elif start:
                    threading.Thread(target=_retention_worker, args=(data, value.get('dossier_id'), preparation.execute, data, start, active_transport), daemon=True).start()
                if isinstance(value, dict) and value.get('kind') == 'configurations':
                    try:
                        session_id, _, _ = preparation.session(store, message['token'])
                    except (sqlite3.Error, SchemaError) as error:
                        _decoration_skipped(message, error)
                        session_id = None
                    with probe_guard:
                        job = probe_jobs.get(session_id)
                    if job is not None and job['dossier_id'] == value['dossier_id']:
                        value['probe_request'] = {key: job[key] for key in ('request_id', 'slug')}
                        value['probe_request'].update(job['future'].result() if job['future'].done()
                                                      else {'pending': True})
                management_cookie = value.pop('_management_cookie', None) if isinstance(value, dict) else None
                result = {'status': code, 'value': value.hex() if isinstance(value, bytes) else value,
                          'piece': isinstance(value, bytes), 'cookie': cookie}
                if management_cookie:
                    result['management_cookie'] = management_cookie
                return result

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

            with BoundedUnixServer(str(socket_path), Handler, data=data,
                                   workers=executor_workers()) as server:
                os.chmod(socket_path, 0o660)
                stopping = threading.Event()
                catalogue_worker = None
                if catalogue_fetch is not None:
                    # Démon : une récupération distante bloquée ne retient pas la sortie du processus
                    catalogue_worker = threading.Thread(target=_refresh_catalogue,
                        args=(data, stopping, catalogue_fetch), name='model-catalogue', daemon=True)
                    catalogue_worker.start()
                try:
                    run(server)
                finally:
                    stopping.set()
                    server.stop_workers()
                    from .preparation import close_admission
                    close_admission(store)
                    if catalogue_worker is not None:
                        # `fetch_unless_stopping` empêche d'écrire un relevé récupéré après `stopping` ; le budget
                        # local couvre une écriture déjà lancée, qui tient dans une transaction : interrompue, SQLite
                        # l'annule. Le délai de 20 s de `fetch_public` n'est qu'une inactivité, pas un total
                        catalogue_worker.join(LOCAL_BUDGET_SECONDS)
                        if catalogue_worker.is_alive():
                            logging.getLogger(__name__).error('EXECUTOR_WORKER_STUCK %s', catalogue_worker.name)
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
    manager = result.get('management_cookie')
    if manager is not None and (type(manager) is not dict or set(manager) != {'token', 'expires_at'}
            or type(manager['token']) is not str or re.fullmatch('[0-9a-f]{64}', manager['token']) is None
            or type(manager['expires_at']) is not str):
        return False
    if result.get('piece'):
        return (type(result['value']) is str
                and re.fullmatch('(?:[0-9a-fA-F]{2})*', result['value']) is not None)
    return type(result['value']) is dict


def preparation_request(socket_path, method, path, token, body=None, *, management_token=None):
    """Relais borné par `RELAY_BUDGET_SECONDS` en délai total ; voir le contrat en tête de module"""
    deadline = time.monotonic() + RELAY_BUDGET_SECONDS
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(remaining_budget(deadline))
        connection.connect(str(socket_path))
        connection.settimeout(remaining_budget(deadline))
        message = dict(method=method, path=path, token=token, body=body)
        if management_token is not None:
            message['management_token'] = management_token
        connection.sendall((encode(message) + '\n').encode())
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
