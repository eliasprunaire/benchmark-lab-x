"""Exécuteur Linux du produit et protocole socket, sans admission ni appel implicite au démarrage."""
from contextlib import closing
import fcntl
import json
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

from .storage import ConflictError, BudgetError, _unique_object

from .storage import Store
from .runtime import encode, status, stop, verify


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


def executor_health(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(path))
        connection.sendall(b'health\n')
        with connection.makefile('rb') as stream:
            raw = stream.readline(4097)
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

            class Handler(socketserver.StreamRequestHandler):
                def handle(self):
                    self.connection.settimeout(2)
                    try:
                        raw = self.rfile.readline(1048577)
                        if raw == b'health\n':
                            verify(store)
                            result = {'source_sha': source, 'storage': 'ok', **status(data, store)}
                        else:
                            from . import preparation
                            if len(raw) > 1048576 or not raw.endswith(b'\n'):
                                return
                            message = json.loads(raw, object_pairs_hook=_unique_object)
                            if set(message) != {'method', 'path', 'token', 'body'}:
                                return
                            try:
                                code, value, cookie, start = preparation.dispatch(
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
                                result = {'status': code, 'value': value.hex() if isinstance(value, bytes) else value,
                                          'piece': isinstance(value, bytes), 'cookie': cookie}
                            except preparation.Denied as error:
                                result = denied_response(error)
                            except (ConflictError, BudgetError):
                                result = {'status': 409, 'value': {'error': 'Action refusée : révision périmée, opération en attente ou budget indisponible. Consultez le dossier courant.'}}
                            except (ValueError, KeyError, TypeError, sqlite3.Error):
                                result = {'status': 400, 'value': {'error': 'Action non vérifiée. Vérifiez les champs ou consultez le dossier courant.'}}
                        self.wfile.write((encode(result) + '\n').encode())
                    except (OSError, ValueError, sqlite3.Error):
                        return

            with socketserver.UnixStreamServer(str(socket_path), Handler) as server:
                os.chmod(socket_path, 0o660)
                run(server)
            stop(data, store, 'PROCESS_STOPPED_ADMISSION_BLOCKED', after_process_exit=True)
        finally:
            os.close(lock_fd)


def preparation_request(socket_path, method, path, token, body=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(socket_path))
        connection.sendall((encode(dict(method=method, path=path, token=token, body=body)) + '\n').encode())
        with connection.makefile('rb') as stream:
            raw = stream.readline(8388609)
        if len(raw) > 8388608 or not raw.endswith(b'\n'):
            raise ValueError('Réponse de préparation invalide')
        return json.loads(raw)


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
