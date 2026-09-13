"""Processus Linux du produit, sans admission ni appel implicite au démarrage."""
from base64 import b64encode
from contextlib import closing
import fcntl
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
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
from http.cookies import SimpleCookie, CookieError
from urllib.parse import parse_qs

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


def serve_executor(data, socket_path, source, *, transport=None, candidate_transport=None, candidate_transport_factory=None):
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
                                    source, transport, candidate_transport=candidate_transport or candidate_transport_factory)
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


def serve_web(address, port, public, socket_path, source):
    public = Path(public)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'Benchmark'
        sys_version = ''

        def setup(self):
            self.request.settimeout(2)
            super().setup()

        def log_message(self, *args):
            # Les URL peuvent contenir une saisie privée ; ne pas les journaliser
            pass

        def respond(self, code, value, media_type='application/json', headers=None, *, script=None):
            raw = value if isinstance(value, bytes) else encode(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', media_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            policy = "default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            if script is not None:
                policy += "; script-src 'sha256-" + b64encode(sha256(script.encode()).digest()).decode() + "'"
            self.send_header('Content-Security-Policy', policy)
            self.send_header('Referrer-Policy', 'no-referrer')
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(raw)

        def preparation(self):
            from . import preparation
            if self.path == '/preparation/style.css' and self.command in ('GET', 'HEAD'):
                self.respond(200, Path(__file__).with_name('preparation.css').read_bytes(), 'text/css; charset=utf-8')
                return
            wants_json = 'application/json' in self.headers.get('Accept', '')
            try:
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
                cookie = cookies.get('benchmark_session')
                token = cookie.value if cookie else None
                body = None
                if self.command == 'POST':
                    length = self.headers.get('Content-Length', '')
                    if not length.isdecimal() or not 0 < int(length) <= 524288 or self.headers.get('Transfer-Encoding'):
                        raise ValueError('Corps invalide')
                    raw = self.rfile.read(int(length))
                    if len(raw) != int(length):
                        raise ValueError('Corps incomplet')
                    media = self.headers.get_content_type()
                    if media == 'application/json':
                        body = json.loads(raw, object_pairs_hook=_unique_object)
                    elif media == 'application/x-www-form-urlencoded':
                        values = parse_qs(raw.decode('utf-8'), keep_blank_values=True, strict_parsing=True)
                        if any(len(v) != 1 for v in values.values()):
                            raise ValueError('Champ répété')
                        body = {k: v[0] for k, v in values.items()}
                        if 'revision' in body:
                            if not re.fullmatch('[1-9][0-9]*', body['revision']):
                                raise ValueError('Révision invalide')
                            body['revision'] = int(body['revision'])
                    else:
                        raise ValueError('Type de formulaire inconnu')
                result = preparation_request(socket_path, 'GET' if self.command == 'HEAD' else self.command,
                                             self.path, token, body)
                headers = {}
                if result.get('cookie'):
                    token = result['cookie']
                    headers['Set-Cookie'] = ('benchmark_session=' + token + '; HttpOnly; Secure; SameSite=Strict; Path=/preparation')
                if self.command == 'POST' and self.path.endswith('/start') and result['status'] < 400 and not wants_json:
                    headers['Location'] = self.path[:-5] + 'conditions'
                    self.respond(303, b'', 'text/html; charset=utf-8', headers)
                    return
                if result.get('piece'):
                    headers['Content-Disposition'] = 'inline; filename="piece.txt"'
                    self.respond(result['status'], bytes.fromhex(result['value']), 'text/plain; charset=utf-8', headers)
                elif wants_json:
                    self.respond(result['status'], result['value'], headers=headers)
                else:
                    csrf = ''
                    view_path = self.path
                    if result['status'] < 400:
                        home = preparation_request(socket_path, 'GET', '/preparation', token)
                        csrf = home['value']['csrf_token']
                        if 'operation_id' in result['value']:
                            result['value']['availability'] = home['value']['availability']
                        if self.command == 'POST' and self.path.endswith('/validation'):
                            target = '/preparation/dossiers/' + result['value']['dossier_id']
                            result = preparation_request(socket_path, 'GET', target, token)
                            view_path = target
                    page = preparation.render(result['value'], csrf, view_path, error=result['status'] >= 400)
                    script = preparation.COMPARISON_FOCUS_SCRIPT if result['value'].get('kind') == 'comparison' else None
                    self.respond(result['status'], page, 'text/html; charset=utf-8', headers, script=script)
            except (ValueError, TypeError, KeyError, CookieError):
                value = {'error': 'Formulaire invalide. Aucun nouvel appel admis.'}
                self.respond(400, value if wants_json else preparation.render(value, '', error=True),
                             'application/json' if wants_json else 'text/html; charset=utf-8')
            except OSError:
                value = {'error': 'Service temporairement indisponible : l’état de votre demande ne peut pas être vérifié. '
                         'Aucune nouvelle soumission disponible. Consultez le dossier avant tout nouvel envoi ; '
                         'un envoi précédent peut avoir été enregistré.', 'unavailable': True}
                self.respond(503, value if wants_json else preparation.render(value, '', error=True),
                             'application/json' if wants_json else 'text/html; charset=utf-8')

        def do_POST(self):
            if self.path == '/preparation' or self.path.startswith('/preparation/'):
                self.preparation()
            else:
                self.respond(404, {'error': 'NOT_FOUND'})

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if self.path == '/preparation' or self.path.startswith('/preparation/'):
                self.preparation()
                return
            if self.path == '/':
                from . import preparation
                self.respond(200, preparation.render({'kind': 'home'}, ''), 'text/html; charset=utf-8')
                return
            if self.path == '/healthz':
                self.respond(200, {'web': 'ok', 'source_sha': source})
                return
            if self.path == '/readyz':
                try:
                    health = executor_health(socket_path)
                    ready = health['source_sha'] == source and health['storage'] == 'ok'
                    self.respond(200 if ready else 503, {'web': 'ok', 'executor': 'ok' if ready else 'unavailable', 'storage': 'ok' if ready else 'unavailable', 'source_sha': source})
                except (OSError, ValueError):
                    self.respond(503, {'web': 'ok', 'executor': 'unavailable', 'storage': 'unknown', 'source_sha': source})
                return
            if self.path.startswith('/publications/'):
                from . import restitution
                match = re.fullmatch(r'/publications/([0-9a-f]{64})/([A-Za-z0-9_-]+\.(?:html|css|txt))', self.path)
                if not match:
                    self.respond(404, {'error': 'NOT_FOUND'})
                    return
                identity, name = match.groups()
                try:
                    raw = restitution.public_bytes(public, identity, name)
                    media = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
                             '.txt': 'text/plain; charset=utf-8'}[Path(name).suffix]
                    self.respond(200, raw, media, {'X-Benchmark-Publication': 'APPROVED_FICTIONAL_S6',
                                                 'X-Benchmark-Projection-SHA256': identity})
                except (OSError, ValueError, KeyError, TypeError):
                    self.respond(404, {'error': 'NO_VERIFIED_PUBLICATION'})
                return
            # Seuls les fichiers d'une projection approuvée sont consultables
            name = self.path.removeprefix('/')
            if not re.fullmatch(r'[a-zA-Z0-9_-]+\.(html|css|png|jpg|txt|json)', name):
                self.respond(404, {'error': 'NOT_FOUND'})
                return
            try:
                if (public / 'active.json').is_symlink():
                    raise ValueError('Pointeur lié interdit')
                publication = json.loads((public / 'active.json').read_text())['directory']
                if not isinstance(publication, str) or not re.fullmatch('[0-9a-f]{64}', publication):
                    raise ValueError('Projection invalide')
                resolved = public / publication
                if resolved.is_symlink():
                    raise ValueError('Projection liée interdite')
                if (resolved / 'publication.json').is_symlink():
                    raise ValueError('Manifeste lié interdit')
                manifest_bytes = (resolved / 'publication.json').read_bytes()
                if sha256(manifest_bytes).hexdigest() != publication:
                    raise ValueError('Manifeste public altéré')
                manifest = json.loads(manifest_bytes)
                from . import restitution
                if manifest.get('schema_version') == restitution.SCHEMA:
                    restitution.public_bytes(public, publication, name)
                    self.respond(303, b'', 'text/plain; charset=utf-8',
                                 {'Location': '/publications/' + publication + '/' + name})
                    return
                expected = manifest['files'][name]
                path = resolved / name
                if path.is_symlink() or not path.is_file():
                    raise ValueError('Pièce publique invalide')
                raw = path.read_bytes()
                if sha256(raw).hexdigest() != expected:
                    raise ValueError('Projection altérée')
                media_type = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.txt': 'text/plain; charset=utf-8', '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg'}[path.suffix]
                self.respond(200, raw, media_type)
            except (OSError, ValueError, KeyError):
                if name == 'index.html' and 'text/html' in self.headers.get('Accept', '') and 'application/json' not in self.headers.get('Accept', ''):
                    from . import preparation
                    self.respond(404, preparation.render({'kind': 'publication_unavailable'}, ''), 'text/html; charset=utf-8')
                else:
                    self.respond(404, {'error': 'NO_VERIFIED_PUBLICATION'})

    # Le proxy termine TLS ; le pare-feu réserve ce port aux deux proxys
    with HTTPServer((address, port), Handler) as server:
        run(server)
