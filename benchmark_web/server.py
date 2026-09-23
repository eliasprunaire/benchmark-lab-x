"""Serveur HTTP public : formulaires, cookies, rendu HTML et projections approuvées.

Il consomme les vues structurées de l'exécuteur par socket Unix et n'accède ni au
stockage, ni aux secrets, ni aux fournisseurs.
"""
from base64 import b64encode, urlsafe_b64decode, urlsafe_b64encode
from hashlib import sha256
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
from pathlib import Path
import posixpath
import re
import secrets
from time import monotonic
from urllib.parse import parse_qs, urlsplit

from benchmark.runtime import encode
from benchmark.service import executor_health, preparation_request, run
from benchmark.storage import _unique_object

from . import views


_ROUTE_MARKERS = {'<id>': r'[A-Za-z0-9_-]{1,128}', '<n>': r'[1-9][0-9]*',
                  '<projection>': r'[0-9a-f]{64}', '<police>': r'[A-Za-z]+',
                  '<piece>': r'[A-Za-z0-9_-]+\.(?:html|css|txt)',
                  '<fichier>': r'[A-Za-z0-9_-]+\.(?:html|css|png|jpg|txt|json)'}

# Motifs servis, essayés dans l'ordre : un chemin absent de cette liste se journalise `<inconnu>`
_ROUTE_PATTERNS = (
    '/', '/healthz', '/readyz', '/bench-x.svg', '/favicon.ico',
    '/preparation', '/preparation/privacy.js', '/preparation/style.css',
    '/preparation/fonts/<police>.woff2',
    '/preparation/data', '/preparation/privacy', '/preparation/activity', '/preparation/catalogue',
    '/preparation/session/open',
    '/preparation/access', '/preparation/access/start', '/preparation/access/key',
    '/preparation/access/callback', '/preparation/access/disconnect',
    '/preparation/contributions', '/preparation/contributions/<id>/withdraw',
    '/preparation/dossiers', '/preparation/dossiers/<id>',
    '/preparation/dossiers/<id>/messages', '/preparation/dossiers/<id>/validation',
    '/preparation/dossiers/<id>/configurations', '/preparation/dossiers/<id>/custom-models',
    '/preparation/dossiers/<id>/contribution', '/preparation/dossiers/<id>/delete',
    '/preparation/dossiers/<id>/archive', '/preparation/dossiers/<id>/archive/items/record',
    '/preparation/dossiers/<id>/revisions/<n>',
    '/preparation/dossiers/<id>/revisions/<n>/pieces/<id>',
    '/preparation/dossiers/<id>/campaigns/<id>',
    '/preparation/dossiers/<id>/campaigns/<id>/conditions',
    '/preparation/dossiers/<id>/campaigns/<id>/start',
    '/preparation/dossiers/<id>/campaigns/<id>/evaluate',
    '/preparation/dossiers/<id>/campaigns/<id>/preview',
    '/preparation/dossiers/<id>/campaigns/<id>/configurations',
    '/preparation/dossiers/<id>/campaigns/<id>/attempts/<id>',
    '/preparation/dossiers/<id>/evaluations/<id>/pieces/<id>',
    '/publications/<projection>/<piece>',
    '/<fichier>',
)
_ROUTES = tuple((re.compile(re.sub('<[a-z]+>', lambda marker: _ROUTE_MARKERS[marker[0]], re.escape(pattern))), pattern)
                for pattern in _ROUTE_PATTERNS)
_METHODS = frozenset(('GET', 'HEAD', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH', 'TRACE', 'CONNECT'))


def canonical_route(path):
    """Motif de route journalisable : le motif servi, jamais le chemin concret ni sa chaîne de requête"""
    try:
        parsed = urlsplit(path)
    except ValueError:
        return '<inconnu>'
    # Une URL en forme absolue n'est servie par aucune route : la journaliser comme locale masquerait un test de proxy
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return '<inconnu>'
    target = parsed.path
    for expression, pattern in _ROUTES:
        if expression.fullmatch(target):
            return pattern
    return '<inconnu>'


def _session_cookie(token):
    return ('benchmark_session=' + token
            + '; HttpOnly; Secure; SameSite=Strict; Path=/preparation; Max-Age=2592000')


def _management_cookie(value):
    if type(value) is not dict or set(value) != {'token', 'expires_at'} or not re.fullmatch('[0-9a-f]{64}', value['token']):
        raise ValueError('Accès de contribution invalide')
    expires = datetime.fromisoformat(value['expires_at'])
    if expires.tzinfo is None:
        raise ValueError('Échéance de contribution invalide')
    return ('benchmark_contributions=' + value['token'] + '; HttpOnly; Secure; SameSite=Strict; Path=/preparation; Expires='
            + format_datetime(expires.astimezone(timezone.utc), usegmt=True))


def _source_fingerprint(headers, client_address, salt, trusted_proxies):
    value = client_address[0]
    # Un en-tête de proxy se forge : ne le croire que venant d'un proxy déclaré
    if _address(value) in trusted_proxies:
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


def _address(value):
    address = ipaddress.ip_address(value)
    return getattr(address, 'ipv4_mapped', None) or address


def _addresses(values):
    return frozenset(_address(value) for value in values)


def serve_web(address, port, public, socket_path, source, public_url=None, *, version=None,
              trusted_proxies=(), readiness_clients=()):
    """Sert le public jusqu'à interruption.

    Le journal d'accès est émis au niveau INFO et n'est pas configuré ici : l'appelant
    règle `logging`, comme le fait `runtime.main`, sinon les lignes disparaissent.
    """
    public = Path(public)
    callback_url = _public_callback_url(public_url)
    # Deux questions distinctes : qui relaie le public (BX-15) et qui supervise ; vides, rien n'est ouvert
    trusted_proxies, readiness_clients = _addresses(trusted_proxies), _addresses(readiness_clients)
    if trusted_proxies & readiness_clients:
        raise ValueError('Un proxy de confiance ne peut pas voir /readyz : tout le trafic public porte son adresse')
    views.SOURCE_SHA = '' if source == 'inconnu' else source or ''
    source_salt = secrets.token_bytes(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'Bench-X'
        sys_version = ''

        def setup(self):
            self.request.settimeout(2)
            super().setup()

        def log_message(self, format, *args):
            # Les gabarits de la bibliothèque standard reprennent la ligne de requête brute : les taire
            pass

        def log_error(self, format, *args):
            # Seul le délai expiré de `handle_one_request` passe une exception : aucune réponse, donc aucune ligne sans ceci
            # Pas de plafond : une ligne mal formée en produit déjà une sans attendre les 2 s
            if args and isinstance(args[0], TimeoutError):
                self.log_request('<abandon>')

        def parse_request(self):
            self.received_at = monotonic()
            return super().parse_request()

        def log_request(self, code='-', size='-'):
            """Une ligne par requête : méthode, motif de route, statut, durée. Ni URL concrète, ni corps"""
            started = getattr(self, 'received_at', None)
            method = getattr(self, 'command', None)
            logging.getLogger(__name__).info(
                'WEB_ACCESS %s %s %s %dms', method if method in _METHODS else '<inconnu>',
                canonical_route(getattr(self, 'path', '') or ''),
                int(code) if isinstance(code, int) else '<abandon>' if code == '<abandon>' else '<inconnu>',
                0 if started is None else round((monotonic() - started) * 1000))

        def respond(self, code, value, media_type='application/json', headers=None, *, script=None):
            raw = value if isinstance(value, bytes) else encode(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', media_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            policy = "default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            privacy_script = media_type.startswith('text/html') and b'src="/preparation/privacy.js"' in raw
            if script is not None:
                policy += "; script-src 'sha256-" + b64encode(sha256(script.encode()).digest()).decode() + "'"
                if privacy_script:
                    policy += " 'self'"
                if script in (views.PREPARATION_PROGRESS_SCRIPT, views.CUSTOM_MODELS_SCRIPT, views.COMPARISON_FOCUS_SCRIPT):
                    policy += "; connect-src 'self'"
            elif privacy_script:
                policy += "; script-src 'self'"
            if privacy_script and 'connect-src' not in policy:
                policy += "; connect-src 'self'"
            self.send_header('Content-Security-Policy', policy)
            self.send_header('Referrer-Policy', 'no-referrer')
            for name, value in (headers.items() if type(headers) is dict else headers or ()):
                self.send_header(name, value)
            try:
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(raw)
            except OSError:
                # La ligne est déjà partie : un `except OSError` appelant enverrait un second statut sur le fil
                self.close_connection = True

        def error_page(self, code, title, message, headers=None):
            """Toute erreur emprunte respond() : en-têtes de sécurité et gabarit français"""
            # Ici la page est le défaut, le tableau de BX-04 la demande en curl ; `not_found` garde le contrat JSON
            if 'application/json' in self.headers.get('Accept', ''):
                self.respond(code, {'error': message}, headers=headers)
                return
            try:
                page = views.render({'error': message, 'title': title}, '', error=True)
            except Exception:
                page = _failure_document(message)
            self.respond(code, page, 'text/html; charset=utf-8', headers)

        def not_found(self):
            # Le contrat JSON garde le code NOT_FOUND ; seul un navigateur reçoit la page
            accept = self.headers.get('Accept', '')
            if 'text/html' in accept and 'application/json' not in accept:
                self.error_page(404, 'Page introuvable',
                                'Cette adresse n’existe pas sur ce service. Vérifiez le lien ou revenez à l’accueil.')
            else:
                self.respond(404, {'error': 'NOT_FOUND'})

        def send_error(self, code, message=None, explain=None):
            """Sans cette surcharge, les verbes non servis reçoivent la page anglaise de la bibliothèque standard, sans en-tête de sécurité"""
            self.close_connection = True
            headers = {'Connection': 'close'}
            if code in (405, 501):
                headers['Allow'] = 'GET, HEAD, POST'
                self.error_page(405, 'Méthode non autorisée',
                                'Cette méthode n’est pas admise sur ce service. '
                                'Seules la consultation et l’envoi de formulaire le sont.', headers)
                return
            # Ligne de requête refusée avant les en-têtes : aucun navigateur à servir, et `self.headers` peut manquer
            self.respond(code, {'error': 'Cette requête n’a pas pu être traitée.'}, headers=headers)

        def preparation(self):
            if self.path == '/preparation/privacy.js' and self.command in ('GET', 'HEAD'):
                self.respond(200, (Path(__file__).parent / 'privacy.js').read_bytes(), 'text/javascript; charset=utf-8')
                return
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
                manager = cookies.get('benchmark_contributions')
                management_token = manager.value if manager else None
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
                    try:
                        raw = self.rfile.read(int(length))
                    except TimeoutError:
                        # L'appelant s'est tu : le `except OSError` plus bas en ferait une panne de l'exécuteur
                        self.log_request('<abandon>')
                        self.close_connection = True
                        return
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
                            if not re.fullmatch('0|[1-9][0-9]*' if self.path.endswith('/contribution') else '[1-9][0-9]*', revision):
                                raise ValueError('Révision invalide')
                            form_body['revision'] = int(revision)
                        if self.path.endswith('/contribution'):
                            revision = values.get('example_revision', [''])[0]
                            if not re.fullmatch('[1-9][0-9]*', revision) or form_body.get('enabled') not in (None, 'true'):
                                raise ValueError('Choix de contribution invalide')
                            form_body['example_revision'] = int(revision)
                            form_body['enabled'] = form_body.get('enabled') == 'true'
                        if 'manifest_version' in form_body:
                            manifest_version = values['manifest_version'][0]
                            if not re.fullmatch('[1-9][0-9]*', manifest_version):
                                raise ValueError('Version de manifeste invalide')
                            form_body['manifest_version'] = int(manifest_version)
                        body = form_body
                    else:
                        raise ValueError('Type de formulaire inconnu')
                    if self.path == '/preparation/session/open':
                        expected = public_url.rstrip('/') if public_url else f'http://{address}:{port}'
                        if (self.headers.get('Origin') != expected or
                                self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none')):
                            raise ValueError('Origine de session invalide')
                        if 'return_path' in body:
                            return_path = _return_path(body.pop('return_path'))
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
                        body['source_sha256'] = _source_fingerprint(self.headers, self.client_address, source_salt, trusted_proxies)
                result = preparation_request(socket_path, 'GET' if self.command == 'HEAD' else self.command,
                                             self.path, token, body, management_token=management_token)
                relayed = True
                headers = {}
                if result.get('cookie'):
                    token = result['cookie']
                if result['status'] < 400 and token and (result.get('cookie') or self.command == 'POST'
                        and not self.path.startswith('/preparation/contributions')
                        and self.path != '/preparation/session/open' and not self.path.endswith('/delete')):
                    headers['Set-Cookie'] = _session_cookie(token)
                if result.get('management_cookie'):
                    existing = [('Set-Cookie', headers.pop('Set-Cookie'))] if 'Set-Cookie' in headers else []
                    management_headers = existing + [('Set-Cookie', _management_cookie(result['management_cookie']))]
                else:
                    management_headers = []
                if self.command == 'POST' and result['status'] < 400 and self.path.endswith(('/contribution', '/withdraw', '/delete')):
                    if not wants_json:
                        target = ('/preparation/data' if self.path.endswith('/delete') else
                                  '/preparation/contributions' if self.path.endswith('/withdraw') else self.path.rsplit('/', 1)[0])
                        self.respond(303, b'', 'text/html; charset=utf-8', list(headers.items()) + management_headers + [('Location', target)])
                    else:
                        self.respond(result['status'], result['value'], headers=list(headers.items()) + management_headers)
                    return
                if self.path == '/preparation/session/open' and result['status'] < 400 and not wants_json:
                    headers['Location'] = return_path or '/preparation'
                    self.respond(303, b'', 'text/html; charset=utf-8', headers)
                    return
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
                    if result['status'] < 400 and result['value'].get('kind') not in ('privacy_data', 'privacy_notice', 'session_bootstrap', 'contributions'):
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
                # Le retour Openrouter est le seul GET qui relaie un envoi : il garde le message d'incertitude
                read_only = self.command in ('GET', 'HEAD') and not self.path.startswith('/preparation/access/callback')
                value = {'error': 'Service temporairement indisponible : cette page ne peut pas être affichée '
                         'pour le moment. Aucune donnée n’a été modifiée ; réessayez dans un instant.'
                         if read_only else
                         'Service temporairement indisponible : l’état de votre demande ne peut pas être vérifié. '
                         'Aucune nouvelle soumission disponible. Consultez le dossier avant tout nouvel envoi ; '
                         'un envoi précédent peut avoir été enregistré.', 'unavailable': True}
                self.respond(503, value if wants_json else views.render(value, '', error=True),
                             'application/json' if wants_json else 'text/html; charset=utf-8')

        def do_POST(self):
            if self.path == '/preparation' or self.path.startswith('/preparation/'):
                self.preparation()
            else:
                self.not_found()

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
                self.respond(200, {'web': 'ok'})
                return
            if self.path == '/readyz':
                # Le pair TCP seul : un en-tête de proxy se forge
                if _address(self.client_address[0]) not in readiness_clients:
                    self.not_found()
                    return
                try:
                    health = executor_health(socket_path)
                    ready = (source != 'inconnu' and health['source_sha'] == source
                             and (version is None or health.get('version') == version)
                             and health['storage'] == 'ok' and not health['restore_pending'])
                    body = {'web': 'ok', 'executor': 'ok' if ready else 'unavailable',
                            'storage': 'ok' if ready else 'unavailable', 'source_sha': source}
                    if version is not None:
                        body['version'] = version
                    self.respond(200 if ready else 503, body)
                except (OSError, ValueError):
                    body = {'web': 'ok', 'executor': 'unavailable', 'storage': 'unknown', 'source_sha': source}
                    if version is not None:
                        body['version'] = version
                    self.respond(503, body)
                return
            if self.path.startswith('/publications/'):
                match = re.fullmatch(r'/publications/([0-9a-f]{64})/([A-Za-z0-9_-]+\.(?:html|css|txt))', self.path)
                if not match:
                    self.not_found()
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
                self.not_found()
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

    # Le proxy termine TLS ; ses en-têtes ne comptent que depuis une adresse --trusted-proxy
    with ThreadingHTTPServer((address, port), Handler) as server:
        run(server)
