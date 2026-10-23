"""Serveur HTTP public : formulaires, cookies et rendu HTML.

Il consomme les vues structurées de l'exécuteur par socket Unix et n'accède ni au
stockage, ni aux secrets, ni aux fournisseurs.
"""
from base64 import b64encode
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

from benchmark import editorial
from benchmark.runtime import encode
from benchmark.service import executor_health, preparation_request, run
from benchmark.storage import _unique_object

from . import views


_ROUTE_MARKERS = {'<id>': r'[A-Za-z0-9_-]{1,128}', '<n>': r'[1-9][0-9]*', '<police>': r'[A-Za-z]+'}

# Motifs servis, essayés dans l'ordre : un chemin absent de cette liste se journalise `<inconnu>`
_ROUTE_PATTERNS = (
    '/', '/healthz', '/readyz', '/bench-x.svg', '/favicon.ico', '/robots.txt', '/sitemap.xml',
    '/evaluations-externes', '/evaluations-externes/<id>',
    '/epreuves', '/epreuves/<id>/<id>', '/epreuves/<id>/<id>/pieces/<id>',
    '/mentions-legales', '/cgu', '/confidentialite',
    '/preparation', '/preparation/privacy.js', '/preparation/style.css',
    '/preparation/fonts/<police>.woff2', '/preparation/fonts/OFL-<police>.txt',
    '/preparation/data', '/preparation/privacy', '/preparation/activity', '/preparation/catalogue',
    '/preparation/session/open', '/preparation/copies/<id>/<id>',
    '/preparation/access', '/preparation/access/key', '/preparation/access/disconnect',
    '/preparation/contributions', '/preparation/contributions/<id>/withdraw',
    '/preparation/dossiers', '/preparation/dossiers/<id>',
    '/preparation/dossiers/<id>/messages', '/preparation/dossiers/<id>/validation',
    '/preparation/dossiers/<id>/configurations', '/preparation/dossiers/<id>/custom-models',
    '/preparation/dossiers/<id>/contribution', '/preparation/dossiers/<id>/delete',
    '/preparation/dossiers/<id>/archive', '/preparation/dossiers/<id>/archive/items/record',
    '/preparation/dossiers/<id>/revisions/<n>',
    '/preparation/dossiers/<id>/campaigns/<id>',
    '/preparation/dossiers/<id>/campaigns/<id>/conditions',
    '/preparation/dossiers/<id>/campaigns/<id>/start',
    '/preparation/dossiers/<id>/campaigns/<id>/evaluate',
    '/preparation/dossiers/<id>/campaigns/<id>/preview',
    '/preparation/dossiers/<id>/campaigns/<id>/configurations',
    '/preparation/dossiers/<id>/campaigns/<id>/attempts/<id>',
    '/preparation/dossiers/<id>/evaluations/<id>/pieces/<id>',
)
_ROUTES = tuple((re.compile(re.sub('<[a-z]+>', lambda marker: _ROUTE_MARKERS[marker[0]], re.escape(pattern))), pattern)
                for pattern in _ROUTE_PATTERNS)
# Ressources invariantes : servies par ETag, jamais sous `no-store` ; les licences OFL accompagnent leurs polices
_RESOURCES = {
    '/bench-x.svg': (Path(__file__).with_name('static') / 'bench-x.svg', 'image/svg+xml'),
    '/favicon.ico': (Path(__file__).with_name('static') / 'favicon.ico', 'image/vnd.microsoft.icon'),
    '/preparation/privacy.js': (Path(__file__).with_name('privacy.js'), 'text/javascript; charset=utf-8'),
    '/preparation/style.css': (views.STYLESHEET_PATH, 'text/css; charset=utf-8'),
    **{'/preparation/fonts/' + path.name: (path, 'font/woff2') for path in views.FONTS_PATH.glob('*.woff2')},
    **{'/preparation/fonts/' + path.name: (path, 'text/plain; charset=utf-8') for path in views.FONTS_PATH.glob('OFL-*.txt')},
}
_METHODS = frozenset(('GET', 'HEAD', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH', 'TRACE', 'CONNECT'))
# Envoi dont l'exécuteur n'a pas répondu dans le délai du relais, qu'il traite peut-être encore (#517) :
# ni échec, ni indisponibilité, la page mène à l'état enregistré, jamais à un renvoi
RESPONSE_TIMEOUT = {'title': 'Réponse non reçue à temps', 'error_code': 'RESPONSE_TIMEOUT',
                    'error': 'Bench-X n’a pas répondu à temps, mais rien n’indique qu’il a échoué : votre envoi est '
                             'peut-être encore en cours de traitement. Consultez son état avant de renvoyer quoi que ce soit.'}


def _load_resource(path, media_type):
    raw = path.read_bytes()
    return raw, media_type, '"' + sha256(raw).hexdigest() + '"'


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
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Une erreur est survenue</title>'
            '</head><body><main><h1>Une erreur est survenue</h1><p>' + escape(message)
            + '</p></main></body></html>').encode()


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
    `public` reçoit les épreuves éditoriales. Les projections fictives S6 ne sont
    pas servies tant qu'aucune restitution réelle n'est approuvée (BX-12).
    """
    # Deux questions distinctes : qui relaie le public (BX-15) et qui supervise ; vides, rien n'est ouvert
    trusted_proxies, readiness_clients = _addresses(trusted_proxies), _addresses(readiness_clients)
    if trusted_proxies & readiness_clients:
        raise ValueError('Un proxy de confiance ne peut pas voir /readyz : tout le trafic public porte son adresse')
    views.SOURCE_SHA = '' if source == 'inconnu' else source or ''
    views.RELEASE_VERSION = version
    origin = public_url.rstrip('/') if public_url else None
    views.PUBLIC_URL = origin
    source_salt = secrets.token_bytes(32)
    # Sous identité de release, lus une fois : un changement demande un redémarrage. Un checkout relit à chaque
    # requête, pour qu'une modification du gabarit ou d'une ressource se voie sans redémarrer
    resources = {path: _load_resource(*entry) for path, entry in _RESOURCES.items()} if version else None
    views.TEMPLATE = views.TEMPLATE_PATH.read_text() if version else None

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

        def respond(self, code, value, media_type='application/json', headers=None, *, script=None, cache='no-store'):
            raw = value if isinstance(value, bytes) else encode(value).encode()
            self.send_response(code)
            # Un 304 ne décrit pas de contenu : une longueur nulle remplacerait celle de la copie en cache
            if code != 304:
                self.send_header('Content-Type', media_type)
                self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', cache)
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
            # Espace privé et erreurs seulement : les icônes restent lisibles pour l'affichage dans les résultats
            if code >= 400 or (getattr(self, 'path', None) or '').startswith('/preparation'):
                self.send_header('X-Robots-Tag', 'noindex')
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
                                'Cette adresse n’existe pas sur ce service. Vérifiez le lien ou retrouvez vos cas d’usage.')
            else:
                self.respond(404, {'error': 'NOT_FOUND'})

        def send_error(self, code, message=None, explain=None):
            """Sans cette surcharge, les verbes non servis reçoivent la page anglaise de la bibliothèque standard, sans en-tête de sécurité"""
            self.close_connection = True
            headers = {'Connection': 'close'}
            if code in (405, 501):
                headers['Allow'] = 'GET, HEAD, POST'
                self.error_page(405, 'Requête non acceptée',
                                'Ce type de requête n’est pas accepté. '
                                'Bench-X permet seulement de consulter des pages et d’envoyer des formulaires.', headers)
                return
            # Ligne de requête refusée avant les en-têtes : aucun navigateur à servir, et `self.headers` peut manquer
            self.respond(code, {'error': 'Cette requête n’a pas pu être traitée.'}, headers=headers)

        def copy_from_editorial(self):
            """Confirmer puis créer une copie privée depuis une version publiée, sans clé ni appel modèle"""
            reference_path = self.path.split('?', 1)[0]
            match = re.fullmatch(
                r'/preparation/copies/([A-Za-z0-9_-]{1,128})/([A-Za-z0-9_-]{1,128})', reference_path)
            if urlsplit(self.path).query or match is None:
                self.error_page(400, 'Adresse invalide', 'Cette page ne prend aucun paramètre.')
                return
            task_id, version_id = match.groups()
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            cookie = cookies.get('benchmark_session')
            token = cookie.value if cookie else None
            if self.command in ('GET', 'HEAD'):
                return self.copy_confirmation(task_id, version_id, token, reference_path)
            if self.command != 'POST':
                self.error_page(405, 'Requête non acceptée', 'Cette page ne prend qu’une confirmation.')
                return
            expected = public_url.rstrip('/') if public_url else f'http://{address}:{port}'
            if self.headers.get('Origin') not in (None, expected) or \
                    self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
                self.error_page(403, 'Copie refusée', 'Cette demande ne vient pas de Bench-X.')
                return
            try:
                length = self.headers.get('Content-Length', '')
                if not length.isdecimal() or not 0 < int(length) <= 4096 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Corps invalide')
                try:
                    raw = self.rfile.read(int(length))
                except TimeoutError:
                    self.log_request('<abandon>')
                    self.close_connection = True
                    return
                if len(raw) != int(length) or self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                    raise ValueError('Corps invalide')
                values = parse_qs(raw.decode('utf-8'), keep_blank_values=True, strict_parsing=True)
                if any(len(items) != 1 for items in values.values()):
                    raise ValueError('Champ répété')
                body = {key: items[0] for key, items in values.items()}
                if set(body) != {'csrf_token', 'copy'} or body['copy'] != '1' or \
                        re.fullmatch(r'[0-9a-f]{64}', body['csrf_token']) is None:
                    raise ValueError('Formulaire invalide')
            except (ValueError, UnicodeError):
                self.error_page(400, 'Copie impossible',
                                'Ce formulaire ne peut pas être traité. Rechargez la confirmation, puis réessayez.')
                return
            if token is None:
                self.error_page(403, 'Session requise',
                                'Votre session a expiré. Ouvrez de nouveau la confirmation de copie.')
                return
            source = self._copy_source(task_id, version_id)
            if source is None:
                return
            dossier_id = secrets.token_hex(16)
            try:
                result = preparation_request(socket_path, 'POST', '/preparation/copies', token, {
                    'csrf_token': body['csrf_token'], 'dossier_id': dossier_id,
                    'action_id': secrets.token_hex(16), 'source': source})
            except OSError:
                self.error_page(503, 'Service momentanément indisponible',
                                'La copie n’a pas pu être enregistrée. Réessayez dans un instant.')
                return
            if result['status'] >= 400:
                message = (result['value'].get('error') if isinstance(result['value'], dict)
                           else 'La copie n’a pas pu être créée.')
                self.error_page(result['status'], 'Copie impossible', message)
                return
            created_id = result['value'].get('dossier_id') if isinstance(result['value'], dict) else dossier_id
            if not isinstance(created_id, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', created_id) is None:
                created_id = dossier_id
            headers = {'Location': '/preparation/dossiers/' + created_id}
            if result.get('cookie'):
                headers['Set-Cookie'] = _session_cookie(result['cookie'])
            self.respond(303, b'', 'text/html; charset=utf-8', headers)

        def _copy_source(self, task_id, version_id):
            """Source admise depuis le paquet public, jamais depuis le corps de la requête"""
            try:
                return editorial.copy_bundle(public, task_id, version_id)
            except editorial.NotFound:
                self.error_page(404, 'Épreuve introuvable',
                                'Cette version publiée n’existe plus. Aucun dossier n’a été créé.')
            except ValueError as error:
                messages = {
                    'Nouvelle reprise refusée': 'Cette version est retirée : aucune nouvelle copie n’est admise.',
                    'Autorité éditoriale refusée': 'La version publiée n’a pas une autorité valide. Aucun dossier n’a été créé.',
                    'Approbation visant d’autres octets': 'La version publiée ne correspond plus à son approbation. Aucun dossier n’a été créé.',
                    'Paquet incomplet': 'Le paquet publié est incomplet. Aucun dossier n’a été créé.',
                    'Octets divergents': 'Le paquet publié ne correspond plus à ses empreintes. Aucun dossier n’a été créé.',
                    'Manifeste divergent': 'Le paquet publié ne correspond plus à ses empreintes. Aucun dossier n’a été créé.',
                    'Sélection publique fermée': 'Le paquet publié contient un contenu non autorisé. Aucun dossier n’a été créé.',
                    'Pièce illisible': 'Le paquet publié contient une pièce illisible. Aucun dossier n’a été créé.',
                    'Identité divergente': 'La version publiée ne correspond plus à son identité. Aucun dossier n’a été créé.',
                }
                self.error_page(409, 'Copie impossible', messages.get(
                    str(error), 'La copie n’a pas pu être créée. Aucun dossier n’a été modifié.'))
            return None

        def copy_confirmation(self, task_id, version_id, token, path):
            """Créer ou réutiliser la session privée sous le chemin du cookie, puis confirmer"""
            source = self._copy_source(task_id, version_id)
            if source is None:
                return
            try:
                result = preparation_request(socket_path, 'GET', '/preparation', token)
            except OSError:
                self.error_page(503, 'Service momentanément indisponible',
                                'La confirmation de copie ne peut pas s’afficher. Réessayez dans un instant.')
                return
            if result['status'] >= 400:
                message = (result['value'].get('error') if isinstance(result['value'], dict)
                           else 'La confirmation de copie ne peut pas s’afficher.')
                self.error_page(result['status'], 'Copie impossible', message)
                return
            if isinstance(result['value'], dict) and result['value'].get('kind') == 'session_bootstrap':
                # Première visite sous confidentialité S7 : le mécanisme existant ouvre la session
                page = views.render(dict(result['value'], return_path=path), '', path)
                headers = {}
                if result.get('cookie'):
                    headers['Set-Cookie'] = _session_cookie(result['cookie'])
                self.respond(200, page, 'text/html; charset=utf-8', headers)
                return
            csrf = result['value'].get('csrf_token') if isinstance(result['value'], dict) else None
            if not isinstance(csrf, str) or re.fullmatch(r'[0-9a-f]{64}', csrf) is None:
                self.error_page(503, 'Service momentanément indisponible',
                                'La confirmation de copie ne peut pas s’afficher. Réessayez dans un instant.')
                return
            try:
                notice = editorial.source_notice(public, task_id, version_id, source)
            except (editorial.NotFound, ValueError, KeyError, TypeError):
                notice = {'withdrawn': None, 'reports': [], 'unavailable': True}
            page = views.render({'kind': 'copy_source', 'source': source, 'source_notice': notice},
                                csrf, path)
            headers = {}
            if result.get('cookie'):
                headers['Set-Cookie'] = _session_cookie(result['cookie'])
            self.respond(200, page, 'text/html; charset=utf-8', headers)

        def preparation(self):
            if re.fullmatch(r'/preparation/fonts/(OFL-)?[A-Za-z]+\.(woff2|txt)', self.path) and self.command in ('GET', 'HEAD'):
                self.respond(404, {'error': 'NOT_FOUND'})
                return
            wants_json = 'application/json' in self.headers.get('Accept', '')
            if (self.command in ('GET', 'HEAD') and not wants_json
                    and re.fullmatch(r'/preparation/dossiers/[A-Za-z0-9_-]{1,128}/custom-models', self.path)):
                # Adresse d'envoi et de suivi JSON des slugs : en HTML, la page est celle des configurations
                self.respond(303, b'', 'text/html; charset=utf-8',
                             {'Location': self.path.removesuffix('/custom-models') + '/configurations#custom-models'})
                return
            # Après le relais, une erreur ne vient plus du formulaire mais du rendu ou du protocole
            relayed = False
            try:
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
                cookie = cookies.get('benchmark_session')
                token = cookie.value if cookie else None
                manager = cookies.get('benchmark_contributions')
                management_token = manager.value if manager else None
                body = None
                return_path = access_return = None
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
                        form_body: dict[str, str | list[str] | int | dict[str, str] | dict[str, int]] = {
                            k: (v if configurations and k == 'models' else v[0])
                            for k, v in values.items()}
                        if configurations:
                            # Dépliant par modèle : `effort:<slug>` vide laisse le niveau adapté automatiquement
                            efforts: dict[str, str] = {}
                            for key in [key for key in form_body if key.startswith('effort:')]:
                                if level := values[key][0]:
                                    efforts[key.removeprefix('effort:')] = level
                                del form_body[key]
                            if efforts:
                                form_body['efforts'] = efforts
                            # `max_tokens:<slug>` vide garde la limite de sortie par défaut ; celui d'un modèle
                            # décoché reste pré-rempli par la relecture et part avec le formulaire : il est ignoré
                            limits: dict[str, int] = {}
                            for key in [key for key in form_body if key.startswith('max_tokens:')]:
                                if (limit := values[key][0]) and key.removeprefix('max_tokens:') in values.get('models', []):
                                    if not re.fullmatch('[1-9][0-9]{0,8}', limit):
                                        raise ValueError('Limite de sortie invalide')
                                    limits[key.removeprefix('max_tokens:')] = int(limit)
                                del form_body[key]
                            if limits:
                                form_body['output_limits'] = limits
                            if 'repetitions' in form_body:
                                if not re.fullmatch('[1-9]', values['repetitions'][0]):
                                    raise ValueError('Nombre d’essais invalide')
                                form_body['repetitions'] = int(values['repetitions'][0])
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
                    if self.path in ('/preparation/access/key', '/preparation/access/disconnect') and 'return' in body:
                        # Retour vers la page d'où la clé est ajoutée ou retirée, par exemple le lancement
                        access_return = _return_path(body.pop('return'))
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
                value = result.get('value')
                if type(value) is dict and type(value.get('source')) is dict:
                    source = value['source']
                    try:
                        value['source_notice'] = editorial.source_notice(
                            public, source['task_id'], source['version_id'], source)
                    except (editorial.NotFound, ValueError, KeyError, TypeError):
                        value['source_notice'] = {'withdrawn': None, 'reports': [], 'unavailable': True}
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
                if self.command == 'POST' and self.path == '/preparation/access/key' and result['status'] < 400 and not wants_json:
                    headers['Location'] = access_return or '/preparation/access'
                    self.respond(303, b'', 'text/html; charset=utf-8', headers)
                    return
                if self.command == 'POST' and self.path == '/preparation/access/disconnect' and result['status'] < 400:
                    headers['Location'] = access_return or '/preparation/access'
                    self.respond(303, b'', 'text/html; charset=utf-8', headers)
                    return
                if (self.command == 'POST' and self.path.endswith(('/configurations', '/custom-models'))
                        and result['status'] < 400 and not wants_json):
                    if self.path.endswith('/custom-models'):
                        headers['Location'] = self.path.removesuffix('/custom-models') + '/configurations#custom-models'
                    else:
                        # « Continuer » mène au récapitulatif de la sélection que l'exécuteur vient de créer
                        campaign_id = result['value'].get('current_campaign_id')
                        if campaign_id is None:
                            # Aucune sélection prête : retour au choix des modèles, qui montre l'état
                            headers['Location'] = self.path
                        elif type(campaign_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', campaign_id):
                            raise ValueError('Sélection de retour invalide')
                        else:
                            headers['Location'] = self.path.removesuffix('/configurations') + '/campaigns/' + campaign_id + '/conditions'
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
                    if result['status'] < 400 and result['value'].get('kind') not in ('privacy_data', 'session_bootstrap', 'contributions'):
                        if 'csrf_token' in result['value'] and ('operation_id' not in result['value']
                                                                or 'availability' in result['value']):
                            # Jeton joint par l'exécuteur, disponibilité déjà calculée comme pour l'accueil :
                            # pas de second relais
                            csrf = result['value']['csrf_token']
                        else:
                            home = preparation_request(socket_path, 'GET', '/preparation', token)
                            csrf = home['value']['csrf_token']
                            if 'operation_id' in result['value']:
                                result['value']['availability'] = home['value']['availability']
                        if self.path == '/preparation/access':
                            result['value']['kind'] = 'access'
                    elif self.command == 'POST' and type(body) is dict:
                        result['value']['form'] = {key: value for key, value in body.items()
                                                   if key not in ('csrf_token', 'source_sha256', 'website', 'key')}
                    page = views.render(result['value'], csrf, view_path, error=result['status'] >= 400)
                    script = views.page_script(result['value']) if result['status'] < 400 else None
                    self.respond(result['status'], page, 'text/html; charset=utf-8', headers, script=script)
            except (ValueError, TypeError, KeyError, CookieError) as error:
                if relayed:
                    logging.getLogger(__name__).error('WEB_INTERNAL RENDER %s', type(error).__name__)
                    value = {'error': 'Une erreur interne a empêché d’afficher cette page. Votre demande a peut-être '
                             'été enregistrée : ouvrez votre cas d’usage avant de renvoyer '
                             'quoi que ce soit.'}
                    if wants_json:
                        self.respond(500, value)
                        return
                    try:
                        page = views.render(value, '', error=True)
                    except Exception:
                        page = _failure_document(value['error'])
                    self.respond(500, page, 'text/html; charset=utf-8')
                    return
                value = {'error': 'Ce formulaire n’a pas pu être traité. Rechargez la page, puis réessayez. Aucun appel n’a été lancé.'}
                self.respond(400, value if wants_json else views.render(value, '', error=True),
                             'application/json' if wants_json else 'text/html; charset=utf-8')
            except OSError as error:
                read_only = self.command in ('GET', 'HEAD')
                if isinstance(error, TimeoutError) and not read_only:
                    value = dict(RESPONSE_TIMEOUT)
                    self.respond(504, value if wants_json else views.render(value, '', urlsplit(self.path).path, error=True),
                                 'application/json' if wants_json else 'text/html; charset=utf-8')
                    return
                # L'archive s'enregistre à sa première demande : après un délai, rien ne prouve le contraire
                writing_read = urlsplit(self.path).path.endswith('/archive')
                value = {'error': 'Bench-X est momentanément indisponible. Cette page ne peut pas s’afficher. '
                         'Réessayez dans un instant.'
                         if read_only and writing_read else
                         'Bench-X est momentanément indisponible. Cette page ne peut pas s’afficher, '
                         'mais rien n’a été modifié. Réessayez dans un instant.'
                         if read_only else
                         'Bench-X est momentanément indisponible et ne peut pas confirmer votre envoi. '
                         'Il a peut-être été enregistré : ouvrez votre cas d’usage avant de renvoyer quoi que ce soit.', 'unavailable': True}
                self.respond(503, value if wants_json else views.render(value, '', error=True),
                             'application/json' if wants_json else 'text/html; charset=utf-8')

        def do_POST(self):
            path = self.path.split('?', 1)[0]
            if path == '/preparation/copies' or path == '/preparation/copies/':
                self.not_found()
            elif path.startswith('/preparation/copies/'):
                self.copy_from_editorial()
            elif self.path == '/preparation' or self.path.startswith('/preparation/'):
                self.preparation()
            else:
                self.not_found()

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            reference_path = self.path.split('?', 1)[0]
            if reference_path == '/evaluations-externes' or reference_path.startswith('/evaluations-externes/'):
                identity = reference_path.removeprefix('/evaluations-externes').removeprefix('/')
                if identity and identity not in views.BENCHMARK_REFERENCES:
                    self.not_found()
                    return
                try:
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, errors='strict', max_num_fields=3)
                    if (query and identity or set(query) - {'q', 'category', 'verification'}
                            or any(len(values) != 1 for values in query.values())):
                        raise ValueError('Recherche invalide')
                    fields = {key: values[0] for key, values in query.items()}
                    categories = {reference['category'] for reference in views.BENCHMARK_REFERENCES.values()
                                  if reference.get('availability') == 'available'}
                    if (fields.get('category', '') not in categories | {''}
                            or fields.get('verification', '') not in set(views.REFERENCE_VERIFICATIONS) | {''}):
                        raise ValueError('Filtre invalide')
                except ValueError:
                    self.error_page(400, 'Recherche invalide', 'Modifiez votre recherche ou effacez les filtres.')
                    return
                value = {'kind': 'references', 'reference_id': identity, **fields}
                self.respond(200, views.render(value, '', self.path), 'text/html; charset=utf-8')
                return
            if reference_path == '/epreuves' or reference_path.startswith('/epreuves/'):
                if urlsplit(self.path).query:
                    self.error_page(400, 'Adresse invalide', 'Cette page ne prend aucun paramètre.')
                    return
                try:
                    if reference_path == '/epreuves':
                        kind, payload = 'page', editorial.catalog_view(public)
                    else:
                        version_path = re.fullmatch(r'/epreuves/([A-Za-z0-9_-]{1,128})/([A-Za-z0-9_-]{1,128})', reference_path)
                        piece = re.fullmatch(
                            r'/epreuves/([A-Za-z0-9_-]{1,128})/([A-Za-z0-9_-]{1,128})/pieces/([A-Za-z0-9_-]{1,128})',
                            reference_path)
                        if version_path:
                            kind, payload = 'page', editorial.version_view(public, *version_path.groups())
                        elif piece:
                            kind, payload = 'piece', editorial.piece_bytes(public, *piece.groups())
                        else:
                            self.not_found()
                            return
                except editorial.NotFound:
                    self.not_found()
                    return
                except ValueError:
                    self.not_found()
                    return
                if kind == 'piece':
                    self.respond(200, payload, 'text/plain; charset=utf-8')
                else:
                    self.respond(200, views.render(payload, '', reference_path), 'text/html; charset=utf-8')
                return
            if self.path in _RESOURCES:
                raw, media, etag = resources[self.path] if resources else _load_resource(*_RESOURCES[self.path])
                # `no-cache` garde la copie et la revalide : juste après un déploiement, sans URL à empreinte
                matched = etag in (tag.strip().removeprefix('W/') for tag in self.headers.get('If-None-Match', '').split(','))
                self.respond(304 if matched else 200, b'' if matched else raw, media, {'ETag': etag}, cache='no-cache')
                return
            if self.path == '/preparation/privacy':
                # Ancienne notice du parcours privé, remplacée par la politique publique (BX-08)
                self.respond(301, b'', 'text/html; charset=utf-8', {'Location': '/confidentialite'})
                return
            if reference_path == '/preparation/copies' or reference_path == '/preparation/copies/':
                self.not_found()
                return
            if reference_path.startswith('/preparation/copies/'):
                self.copy_from_editorial()
                return
            if self.path == '/preparation' or self.path.startswith('/preparation/'):
                self.preparation()
                return
            if self.path in views.LEGAL_PAGES:
                self.respond(200, views.render({'kind': 'legal', 'path': self.path}, ''), 'text/html; charset=utf-8')
                return
            if self.path == '/':
                self.respond(200, views.render({'kind': 'home'}, ''), 'text/html; charset=utf-8')
                return
            if self.path == '/robots.txt':
                # /preparation reste lisible : un Disallow y cacherait le noindex, l'adresse resterait indexable sans contenu
                rules = 'User-agent: *\nDisallow: /publications\n'
                if origin:
                    rules += '\nSitemap: ' + origin + '/sitemap.xml\n'
                self.respond(200, rules.encode(), 'text/plain; charset=utf-8')
                return
            if self.path == '/sitemap.xml' and origin:
                self.respond(200, ('<?xml version="1.0" encoding="UTF-8"?>\n'
                                   '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                   + ''.join('<url><loc>' + escape(origin + path) + '</loc></url>' for path in views.PUBLIC_PAGES)
                                   + '</urlset>\n').encode(), 'application/xml; charset=utf-8')
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
            self.not_found()

    # Le proxy termine TLS ; ses en-têtes ne comptent que depuis une adresse --trusted-proxy
    with ThreadingHTTPServer((address, port), Handler) as server:
        run(server)
