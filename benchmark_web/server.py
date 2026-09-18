"""Serveur HTTP public : formulaires, cookies, rendu HTML et projections approuvées.

Il consomme les vues structurées de l'exécuteur par socket Unix et n'accède ni au
stockage, ni aux secrets, ni aux fournisseurs.
"""
from base64 import b64encode, urlsafe_b64decode, urlsafe_b64encode
from hashlib import sha256
from html import escape
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import logging
from pathlib import Path
import posixpath
import re
import secrets
from urllib.parse import parse_qs, urlsplit

from benchmark.runtime import encode
from benchmark.service import executor_health, preparation_request, run
from benchmark.storage import _unique_object

from . import views


def _session_cookie(token):
    return ('benchmark_session=' + token
            + '; HttpOnly; Secure; SameSite=Strict; Path=/preparation; Max-Age=2592000')


def _source_fingerprint(headers, client_address, salt):
    value = headers.get('X-Real-IP')
    if value is None:
        forwarded = headers.get('X-Forwarded-For')
        value = forwarded.split(',', 1)[0] if forwarded is not None else client_address[0]
    try:
        address = ipaddress.ip_address(value.strip())
        if address.version == 6:
            address = ipaddress.ip_network(str(address) + '/64', strict=False).network_address
        normalized = str(address)
    except (AttributeError, ValueError):
        normalized = 'invalide'
    return sha256(salt + b'\n' + normalized.encode()).hexdigest()


def _public_callback_url(public_url):
    if public_url is None:
        return None
    parsed = urlsplit(public_url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError('Origine publique HTTPS invalide')
    return public_url.rstrip('/') + '/preparation/access/callback'


def _return_path(value):
    if type(value) is not str or not value.isascii() or '\\' in value:
        raise ValueError('Chemin de retour invalide')
    parsed = urlsplit(value)
    if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
            or posixpath.normpath(parsed.path) != parsed.path
            or not (parsed.path == '/preparation' or parsed.path.startswith('/preparation/'))):
        raise ValueError('Chemin de retour invalide')
    return parsed.path


def _failure_document(message):
    """Page de repli sans rendu : ne pas redemander la page au composant qui vient d'échouer"""
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Erreur</title>'
            '</head><body><main><h1>Erreur</h1><p>' + escape(message)
            + '</p></main></body></html>').encode()


def _callback_cookie(token, return_path):
    return urlsafe_b64encode(encode([token, return_path]).encode()).decode().rstrip('=')


def _callback_state(value):
    raw = urlsafe_b64decode(value + '=' * (-len(value) % 4))
    token, return_path = json.loads(raw, object_pairs_hook=_unique_object)
    if type(token) is not str or not token:
        raise ValueError('Session de retour invalide')
    return token, _return_path(return_path)


def serve_web(address, port, public, socket_path, source, public_url=None):
    public = Path(public)
    callback_url = _public_callback_url(public_url)
    views.SOURCE_SHA = '' if source == 'inconnu' else source or ''
    source_salt = secrets.token_bytes(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'Bench-X'
        sys_version = ''

        def setup(self):
            self.request.settimeout(2)
            super().setup()

        def log_message(self, format, *args):
            # Les URL peuvent contenir une saisie privée ; ne pas les journaliser
            pass

        def respond(self, code, value, media_type='application/json', headers=None, *, script=None):
            raw = value if isinstance(value, bytes) else encode(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', media_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            policy = "default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            if script is not None:
                policy += "; script-src 'sha256-" + b64encode(sha256(script.encode()).digest()).decode() + "'"
                if script in (views.PREPARATION_PROGRESS_SCRIPT, views.CUSTOM_MODELS_SCRIPT, views.COMPARISON_FOCUS_SCRIPT):
                    policy += "; connect-src 'self'"
            self.send_header('Content-Security-Policy', policy)
            self.send_header('Referrer-Policy', 'no-referrer')
            for name, value in (headers.items() if type(headers) is dict else headers or ()):
                self.send_header(name, value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(raw)

        def preparation(self):
            if self.path == '/preparation/style.css' and self.command in ('GET', 'HEAD'):
                self.respond(200, views.STYLESHEET_PATH.read_bytes(), 'text/css; charset=utf-8')
                return
            font = re.fullmatch(r'/preparation/fonts/([A-Za-z]+)\.woff2', self.path)
            if font and self.command in ('GET', 'HEAD'):
                try:
                    self.respond(200, (views.FONTS_PATH / (font.group(1) + '.woff2')).read_bytes(), 'font/woff2')
                except OSError:
                    self.respond(404, {'error': 'NOT_FOUND'})
                return
            wants_json = 'application/json' in self.headers.get('Accept', '')
            # Après le relais, une erreur ne vient plus du formulaire mais du rendu ou du protocole
            relayed = False
            try:
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
                cookie = cookies.get('benchmark_session')
                token = cookie.value if cookie else None
                if self.command == 'GET' and self.path.startswith('/preparation/access/callback'):
                    parsed = urlsplit(self.path)
                    values = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                    if parsed.path != '/preparation/access/callback' or set(values) != {'code'} or len(values['code']) != 1 or not values['code'][0]:
                        raise ValueError('Retour Openrouter invalide')
                    state = cookies.get('benchmark_access_callback')
                    if state is None:
                        raise ValueError('Session de retour absente')
                    callback_token, return_path = _callback_state(state.value)
                    result = preparation_request(socket_path, 'POST', parsed.path, callback_token,
                                                 {'code': values['code'][0]})
                    relayed = True
                    expired = ('Set-Cookie',
                               'benchmark_access_callback=; HttpOnly; Secure; SameSite=Lax; '
                               'Path=/preparation/access/callback; Max-Age=0')
                    if result['status'] >= 400:
                        self.respond(result['status'], views.render(result['value'], '', error=True),
                                     'text/html; charset=utf-8', [expired])
                        return
                    self.respond(303, b'', 'text/html; charset=utf-8',
                                 [('Location', return_path), expired,
                                  ('Set-Cookie', _session_cookie(callback_token))])
                    return
                if self.path == '/preparation/access/callback':
                    raise ValueError('Callback Openrouter réservé au retour GET')
                body = None
                return_path = None
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
                        configurations = re.fullmatch(
                            r'/preparation/dossiers/[A-Za-z0-9_-]{1,128}/configurations',
                            self.path)
                        if any(len(v) != 1 and not (configurations and k == 'models')
                               for k, v in values.items()):
                            raise ValueError('Champ répété')
                        form_body: dict[str, str | list[str] | int] = {
                            k: (v if configurations and k == 'models' else v[0])
                            for k, v in values.items()}
                        if 'revision' in form_body:
                            revision = values['revision'][0]
                            if not re.fullmatch('[1-9][0-9]*', revision):
                                raise ValueError('Révision invalide')
                            form_body['revision'] = int(revision)
                        if 'manifest_version' in form_body:
                            manifest_version = values['manifest_version'][0]
                            if not re.fullmatch('[1-9][0-9]*', manifest_version):
                                raise ValueError('Version de manifeste invalide')
                            form_body['manifest_version'] = int(manifest_version)
                        body = form_body
                    else:
                        raise ValueError('Type de formulaire inconnu')
                    if self.path == '/preparation/access/start':
                        if callback_url is None:
                            value = {'kind': 'access', 'connected': False, 'status': 'unavailable',
                                     'error': 'Connexion Openrouter indisponible : URL publique non configurée.'}
                            self.respond(503, value if wants_json else views.render(value, body.get('csrf_token', ''), error=True),
                                         'application/json' if wants_json else 'text/html; charset=utf-8')
                            return
                        return_path = _return_path(body.pop('return'))
                        body['callback_url'] = callback_url
                    submission = (self.path == '/preparation/dossiers' or
                                  re.fullmatch(r'/preparation/dossiers/[A-Za-z0-9_-]{1,128}/messages', self.path))
                    if submission and body.get('website'):
                        value = {'kind': 'honeypot_ack'}
                        self.respond(200, value if wants_json else views.render(value, ''),
                                     'application/json' if wants_json else 'text/html; charset=utf-8')
                        return
                    if submission:
                        body = dict(body)
                        body.pop('website', None)
                        body['source_sha256'] = _source_fingerprint(self.headers, self.client_address, source_salt)
                result = preparation_request(socket_path, 'GET' if self.command == 'HEAD' else self.command,
                                             self.path, token, body)
                relayed = True
                headers = {}
                if result.get('cookie'):
                    token = result['cookie']
                if result['status'] < 400 and token:
                    headers['Set-Cookie'] = _session_cookie(token)
                if (self.command == 'POST' and result['status'] < 400 and not wants_json
                        and (self.path == '/preparation/dossiers' or re.fullmatch(
                            r'/preparation/dossiers/[A-Za-z0-9_-]{1,128}/(?:messages|validation)', self.path))):
                    dossier_id = result['value']['dossier_id']
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', dossier_id):
                        raise ValueError('Dossier de retour invalide')
                    headers['Location'] = '/preparation/dossiers/' + dossier_id
                    self.respond(303, b'', 'text/html; charset=utf-8', headers)
                    return
                if return_path is not None and result['status'] < 400:
                    response_headers = list(headers.items())
                    response_headers += [
                        ('Location', result['value']['authorize_url']),
                        ('Set-Cookie', 'benchmark_access_callback=' + _callback_cookie(token, return_path)
                         + '; HttpOnly; Secure; SameSite=Lax; Path=/preparation/access/callback')]
                    self.respond(303, b'', 'text/html; charset=utf-8', response_headers)
                    return
                if self.command == 'POST' and self.path == '/preparation/access/key' and result['status'] < 400 and not wants_json:
                    headers['Location'] = '/preparation'
                    self.respond(303, b'', 'text/html; charset=utf-8', headers)
                    return
                if self.command == 'POST' and self.path == '/preparation/access/disconnect' and result['status'] < 400:
                    headers['Location'] = '/preparation/access'
                    self.respond(303, b'', 'text/html; charset=utf-8', headers)
                    return
                if (self.command == 'POST' and self.path.endswith(('/configurations', '/custom-models'))
                        and result['status'] < 400 and not wants_json):
                    headers['Location'] = (self.path.removesuffix('/custom-models') + '/configurations#custom-models'
                                           if self.path.endswith('/custom-models') else self.path)
                    self.respond(303, b'', 'text/html; charset=utf-8', headers)
                    return
                if self.command == 'POST' and self.path.endswith(('/start', '/evaluate')) and result['status'] < 400 and not wants_json:
                    headers['Location'] = self.path.rsplit('/', 1)[0] + '/conditions'
                    self.respond(303, b'', 'text/html; charset=utf-8', headers)
                    return
                if result.get('piece'):
                    headers['Content-Disposition'] = 'inline; filename="piece.txt"'
                    self.respond(result['status'], bytes.fromhex(result['value']), 'text/plain; charset=utf-8', headers)
                elif wants_json:
                    self.respond(result['status'], result['value'], headers=headers)
                else:
                    csrf = body.get('csrf_token', '') if type(body) is dict else ''
                    view_path = self.path
                    if result['status'] < 400:
                        home = preparation_request(socket_path, 'GET', '/preparation', token)
                        csrf = home['value']['csrf_token']
                        if self.path == '/preparation/access':
                            result['value']['kind'] = 'access'
                        if 'operation_id' in result['value']:
                            result['value']['availability'] = home['value']['availability']
                    elif self.command == 'POST' and type(body) is dict:
                        result['value']['form'] = {key: value for key, value in body.items()
                                                   if key not in ('csrf_token', 'source_sha256', 'website', 'key')}
                    page = views.render(result['value'], csrf, view_path, error=result['status'] >= 400)
                    script = views.page_script(result['value']) if result['status'] < 400 else None
                    self.respond(result['status'], page, 'text/html; charset=utf-8', headers, script=script)
            except (ValueError, TypeError, KeyError, CookieError) as error:
                if relayed:
                    logging.getLogger(__name__).error('WEB_INTERNAL RENDER %s', type(error).__name__)
                    value = {'error': 'Défaillance interne du service : cette page n’a pas pu être '
                             'construite. L’état de votre demande n’est pas confirmé ; consultez le '
                             'dossier avant tout nouvel envoi.'}
                    if wants_json:
                        self.respond(500, value)
                        return
                    try:
                        page = views.render(value, '', error=True)
                    except Exception:
                        page = _failure_document(value['error'])
                    self.respond(500, page, 'text/html; charset=utf-8')
                    return
                value = {'error': 'Formulaire invalide. Aucun nouvel appel admis.'}
                self.respond(400, value if wants_json else views.render(value, '', error=True),
                             'application/json' if wants_json else 'text/html; charset=utf-8')
            except OSError:
                value = {'error': 'Service temporairement indisponible : l’état de votre demande ne peut pas être vérifié. '
                         'Aucune nouvelle soumission disponible. Consultez le dossier avant tout nouvel envoi ; '
                         'un envoi précédent peut avoir été enregistré.', 'unavailable': True}
                self.respond(503, value if wants_json else views.render(value, '', error=True),
                             'application/json' if wants_json else 'text/html; charset=utf-8')

        def do_POST(self):
            if self.path == '/preparation' or self.path.startswith('/preparation/'):
                self.preparation()
            else:
                self.respond(404, {'error': 'NOT_FOUND'})

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            from benchmark import publications
            assets = {'/bench-x.svg': ('bench-x.svg', 'image/svg+xml'),
                      '/favicon.ico': ('favicon.ico', 'image/vnd.microsoft.icon')}
            if self.path in assets:
                name, media = assets[self.path]
                self.respond(200, (Path(__file__).parent / 'static' / name).read_bytes(), media)
                return
            if self.path == '/preparation' or self.path.startswith('/preparation/'):
                self.preparation()
                return
            if self.path == '/':
                self.respond(200, views.render({'kind': 'home'}, ''), 'text/html; charset=utf-8')
                return
            if self.path == '/healthz':
                self.respond(200, {'web': 'ok', 'source_sha': source})
                return
            if self.path == '/readyz':
                try:
                    health = executor_health(socket_path)
                    ready = (source != 'inconnu' and health['source_sha'] == source
                             and health['storage'] == 'ok')
                    self.respond(200 if ready else 503, {'web': 'ok', 'executor': 'ok' if ready else 'unavailable', 'storage': 'ok' if ready else 'unavailable', 'source_sha': source})
                except (OSError, ValueError):
                    self.respond(503, {'web': 'ok', 'executor': 'unavailable', 'storage': 'unknown', 'source_sha': source})
                return
            if self.path.startswith('/publications/'):
                match = re.fullmatch(r'/publications/([0-9a-f]{64})/([A-Za-z0-9_-]+\.(?:html|css|txt))', self.path)
                if not match:
                    self.respond(404, {'error': 'NOT_FOUND'})
                    return
                identity, name = match.groups()
                try:
                    raw = publications.public_bytes(public, identity, name)
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
                if manifest.get('schema_version') == publications.SCHEMA:
                    publications.public_bytes(public, publication, name)
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
                    self.respond(404, views.render({'kind': 'publication_unavailable'}, ''), 'text/html; charset=utf-8')
                else:
                    self.respond(404, {'error': 'NO_VERIFIED_PUBLICATION'})

    # Le proxy termine TLS ; le pare-feu réserve ce port aux deux proxys
    with HTTPServer((address, port), Handler) as server:
        run(server)
