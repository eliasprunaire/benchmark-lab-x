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
import threading

from .storage import ConflictError, BudgetError, _unique_object

from .storage import Store
from .runtime import encode, status, stop, verify


def release_identity():
    manifest = json.loads((Path(__file__).resolve().parents[1] / 'release.json').read_text())
    source = manifest['source_sha']
    if not re.fullmatch('[0-9a-f]{40}', source):
        raise ValueError('Identité de release invalide')
    return source


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


def serve_executor(data, socket_path, source, *, transport=None, candidate_transport=None, candidate_transport_factory=None,
                   presentation=None):
    data, socket_path = Path(data), Path(socket_path)
    with closing(Store(data)) as store:
        lock_fd = os.open(data / 'executor.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            verify(store)
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
                                    presentation=presentation)
                                if isinstance(start, dict):
                                    from .campaigns import execute_launch
                                    threading.Thread(target=execute_launch, args=(data, start['candidate_attempts'], candidate_transport),
                                                     kwargs={'transport_factory': candidate_transport_factory}, daemon=True).start()
                                elif start:
                                    threading.Thread(target=preparation.execute, args=(data, start, transport), daemon=True).start()
                                result = {'status': code, 'value': value.hex() if isinstance(value, bytes) else value,
                                          'piece': isinstance(value, bytes), 'cookie': cookie}
                            except preparation.Denied:
                                result = {'status': 403, 'value': {'error': 'Cette action n’est pas autorisée pour votre session. Retrouvez votre dossier ou demandez au responsable de vérifier son autorisation.'}}
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
