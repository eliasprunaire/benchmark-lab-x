"""Connexion OpenRouter relayée par le web, sans appel fournisseur."""
from http.client import HTTPConnection
import json
import multiprocessing
from pathlib import Path
import queue
import socket
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode

from benchmark.storage import _strict_json
from benchmark_web import views
from benchmark_web.server import _public_callback_url, serve_web


CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; "
       "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


def _port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class FakeExecutor:
    def __init__(self, path):
        self.path = path
        self.requests = queue.Queue()
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._serve)
        self.callback_result = {'status': 200, 'value': {'connected': True, 'status': 'connected'},
                                'piece': False, 'cookie': None}
        self.start_cookie = None

    def __enter__(self):
        self.worker.start()
        deadline = time.monotonic() + 5
        while not self.path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('Socket fictive absente')
            time.sleep(.01)
        return self

    def __exit__(self, *args):
        self.stop.set()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as wake:
                wake.connect(str(self.path))
        except OSError:
            pass
        self.worker.join(5)

    def _serve(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.path))
            server.listen()
            server.settimeout(.1)
            while not self.stop.is_set():
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    raw = b''
                    while not raw.endswith(b'\n'):
                        part = connection.recv(4096)
                        if not part:
                            break
                        raw += part
                    if not raw:
                        continue
                    request = json.loads(raw)
                    self.requests.put(request)
                    if request['path'] == '/preparation/access' and request['method'] == 'GET':
                        result = {'status': 200, 'value': {'connected': False, 'status': 'disconnected'},
                                  'piece': False, 'cookie': 'session-token'}
                    elif request['path'] == '/preparation' and request['method'] == 'GET':
                        result = {'status': 200, 'value': {'csrf_token': 'csrf', 'availability': {}},
                                  'piece': False, 'cookie': None}
                    elif request['path'] == '/preparation/access/start':
                        result = {'status': 200, 'value': {'authorize_url': 'https://openrouter.ai/auth?fixture=1'},
                                  'piece': False, 'cookie': self.start_cookie}
                    elif request['path'] == '/preparation/access/callback':
                        result = self.callback_result
                    elif (request['method'] == 'POST'
                          and request['path'].endswith('/configurations')):
                        result = {'status': 201, 'value': {'kind': 'configurations'},
                                  'piece': False, 'cookie': None}
                    elif request['method'] == 'POST' and request['path'].endswith('/cap'):
                        result = {'status': 200, 'value': {'kind': 'campaign_launch'},
                                  'piece': False, 'cookie': None}
                    else:
                        result = {'status': 404, 'value': {'error': 'NOT_FOUND'},
                                  'piece': False, 'cookie': None}
                    connection.sendall((_strict_json(result) + '\n').encode())


class AccessViewTests(unittest.TestCase):
    @staticmethod
    def campaign(access):
        return {'kind': 'campaign_launch', 'dossier_id': 'd1', 'access': access,
                'criteria': {'eliminatory_errors': [], 'obligations': [], 'result_expected': 'Résultat'},
                'estimate': None, 'can_launch': False, 'admission_id': None,
                'campaign': {'campaign_id': 'c1', 'version': 1, 'panel': [], 'budget': None,
                             'reserve_amounts': {}, 'cells': [], 'attempts': [],
                             'conditions': {'frozen_at': '2026-09-15T00:00:00Z',
                                            'pi': {'package': 'pi', 'version': '1'}}}}

    def test_trois_etats(self):
        disconnected = views.render(
            {'kind': 'access', 'connected': False, 'status': 'disconnected'}, 'csrf').decode()
        self.assertIn('Compte non connecté', disconnected)
        self.assertIn('Connecter mon compte OpenRouter', disconnected)

        connected = views.render({'kind': 'access', 'connected': True, 'status': 'connected',
                                  'limit_usd': '25', 'limit_remaining_usd': '12.50'}, 'csrf').decode()
        self.assertIn('Compte connecté', connected)
        self.assertIn('Crédit restant : 12.50 USD', connected)
        self.assertIn('Limite du compte : 25 USD', connected)
        self.assertIn('Déconnecter', connected)

        invalid = views.render({'kind': 'access', 'connected': False, 'status': 'invalid',
                                'reason': 'KEY_REJECTED'}, 'csrf').decode()
        self.assertIn('Accès invalide', invalid)
        self.assertIn('Motif : KEY_REJECTED', invalid)

    def test_recapitulatif_connecte_ne_propose_pas_une_nouvelle_connexion(self):
        page = views.render(self.campaign(
            {'status': 'connected', 'limit_remaining_usd': '12.50'}), 'csrf').decode()
        self.assertNotIn('action="/preparation/access/start"', page)
        self.assertIn('action="/preparation/access/disconnect"', page)

    def test_indisponible_ne_propose_aucun_formulaire(self):
        access = views.render({'kind': 'access', 'status': 'unavailable'}, 'csrf').decode()
        summary = views.render(self.campaign({'status': 'unavailable'}), 'csrf').decode()
        for page in (access, summary):
            self.assertIn('Connexion OpenRouter indisponible.', page)
            self.assertNotIn('action="/preparation/access/start"', page)
            self.assertNotIn('action="/preparation/access/disconnect"', page)

    def test_page_configurations_et_selection_courante(self):
        value = {
            'kind': 'configurations', 'dossier_id': 'd1',
            'fetched_at': '2026-09-15T12:00:00+00:00',
            'models': [
                {'id': 'modele-a', 'name': 'Modèle A', 'selected': True,
                 'not_adjustable': False},
                {'id': 'modele-b', 'name': 'Modèle B', 'selected': True,
                 'not_adjustable': True},
            ],
            'current_tier': 'enhanced', 'available_tiers': ['standard', 'enhanced'],
            'configurations': [
                {'model': 'modele-a', 'estimate': {'amount_usd': '1.20'}},
                {'model': 'modele-b', 'estimate': {'amount_usd': '2.30'},
                 'effort_limit': 'not_adjustable'},
            ],
            'current_campaign_id': 'd1-c1', 'estimate_total_usd': '3.50',
            'cap_usd': '50.00', 'cap_source': 'default', 'superseded': [],
            'estimate_under_cap': True, 'assumptions': {},
        }
        page = views.render(value, 'csrf').decode()
        self.assertIn('action="/preparation/dossiers/d1/configurations"', page)
        self.assertEqual(2, page.count('name="models"'))
        self.assertIn('name="tier" value="enhanced" checked', page)
        self.assertIn('palier de raisonnement non réglable', page)
        self.assertIn('Estimation totale : 3.50 USD', page)
        self.assertIn('Plafond : 50.00 USD', page)
        self.assertIn('/campaigns/d1-c1/conditions', page)

    def test_recapitulatif_demandeur_passant_et_bloquant(self):
        base = self.campaign({'status': 'connected', 'limit_remaining_usd': '12.50'})
        base.update(
            checks=[
                {'key': 'example_validated', 'ok': True, 'detail': 'Exemple validé'},
                {'key': 'example_qualified', 'ok': True, 'detail': 'Exemple qualifié'},
                {'key': 'configurations_available', 'ok': True,
                 'detail': 'Tous les modèles sont disponibles'},
                {'key': 'access_connected', 'ok': True,
                 'detail': {'limit_remaining_usd': '12.50', 'limit_usd': '20'}},
                {'key': 'estimate_under_cap', 'ok': True,
                 'detail': 'Estimation totale : 3.50 USD'},
            ],
            launchable=True, cap_usd='50.00', cap_source='default',
            estimate_total_usd='3.50')
        page = views.render(base, 'csrf').decode()
        self.assertIn('✓ Exemple validé', page)
        self.assertIn('Crédit restant : 12.50 USD ; limite du compte : 20 USD', page)
        self.assertIn('action="/preparation/dossiers/d1/campaigns/c1/cap"', page)
        self.assertIn('min="0.10" max="100.00" step="0.01"', page)
        self.assertIn('>Lancer la comparaison</button>', page)

        blocked = dict(base, launchable=False,
                       checks=[dict(check) for check in base['checks']])
        blocked['checks'][2] = {'key': 'configurations_available', 'ok': False,
                                'detail': 'Modèle à choisir de nouveau'}
        page = views.render(blocked, 'csrf').decode()
        self.assertIn('✕ Modèle à choisir de nouveau', page)
        self.assertIn('/preparation/dossiers/d1/configurations', page)
        self.assertNotIn('>Lancer la comparaison</button>', page)


class AccessServerTests(unittest.TestCase):
    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        result = response.status, response.headers, raw
        connection.close()
        return result

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='web-access-')
        root = Path(self.temporary.name)
        public = root / 'public'
        public.mkdir()
        self.executor = FakeExecutor(root / 'executor.sock')
        self.executor.__enter__()
        self.port = _port()
        self.web = multiprocessing.get_context('spawn').Process(
            target=serve_web,
            args=('127.0.0.1', self.port, public, self.executor.path, 'a' * 40,
                  'https://benchmark.example'))
        self.web.start()
        deadline = time.monotonic() + 5
        while True:
            try:
                status, _, _ = self.request('GET', '/healthz')
                if status == 200:
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.02)

    def tearDown(self):
        self.web.terminate()
        self.web.join(5)
        self.executor.__exit__(None, None, None)
        self.temporary.cleanup()

    def test_depart_callback_et_csp(self):
        status, headers, _ = self.request('GET', '/preparation/access')
        self.assertEqual(200, status)
        self.assertEqual(CSP, headers['Content-Security-Policy'])
        session_cookie = headers['Set-Cookie'].split(';', 1)[0]

        body = urlencode({'csrf_token': 'csrf', 'return': '/preparation/dossiers/d1'}).encode()
        status, headers, raw = self.request('POST', '/preparation/access/start', body, {
            'Content-Type': 'application/x-www-form-urlencoded', 'Cookie': session_cookie})
        self.assertEqual(303, status)
        self.assertEqual('https://openrouter.ai/auth?fixture=1', headers['Location'])
        callback_cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.assertNotIn(b'code=', raw)
        start = self.executor.requests.get_nowait()
        home = self.executor.requests.get_nowait()
        request = self.executor.requests.get_nowait()
        self.assertEqual(('/preparation/access', '/preparation', '/preparation/access/start'),
                         (start['path'], home['path'], request['path']))
        self.assertEqual({'csrf_token': 'csrf',
                          'callback_url': 'https://benchmark.example/preparation/access/callback'},
                         request['body'])

        status, headers, raw = self.request(
            'GET', '/preparation/access/callback?code=secret-authorization-code',
            headers={'Cookie': callback_cookie})
        self.assertEqual(303, status)
        self.assertEqual('/preparation/dossiers/d1', headers['Location'])
        rendered_headers = str(headers)
        self.assertNotIn('secret-authorization-code', rendered_headers)
        self.assertNotIn(b'secret-authorization-code', raw)
        callback = self.executor.requests.get_nowait()
        self.assertEqual({'code': 'secret-authorization-code'}, callback['body'])
        self.assertEqual('/preparation/access/callback', callback['path'])

    def test_callback_refuse_rend_erreur_et_efface_son_cookie(self):
        status, headers, _ = self.request('GET', '/preparation/access')
        session_cookie = headers['Set-Cookie'].split(';', 1)[0]
        body = urlencode({'csrf_token': 'csrf', 'return': '/preparation/dossiers/d1'}).encode()
        status, headers, _ = self.request('POST', '/preparation/access/start', body, {
            'Content-Type': 'application/x-www-form-urlencoded', 'Cookie': session_cookie})
        callback_cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.executor.callback_result = {
            'status': 403,
            'value': {'error': 'Échange OpenRouter refusé', 'error_code': 'ACCESS_EXCHANGE_FAILED'},
            'piece': False, 'cookie': None}

        status, headers, raw = self.request(
            'GET', '/preparation/access/callback?code=code-a-ne-pas-rendre',
            headers={'Cookie': callback_cookie})

        self.assertEqual(403, status)
        self.assertIn(b'change OpenRouter refus', raw)
        self.assertNotIn(b'code-a-ne-pas-rendre', raw)
        self.assertNotIn('code-a-ne-pas-rendre', str(headers))
        self.assertNotIn('Location', headers)
        self.assertIn('Max-Age=0', headers['Set-Cookie'])

    def test_refuse_un_retour_exterieur(self):
        body = urlencode({'csrf_token': 'csrf', 'return': 'https://evil.example/preparation'}).encode()
        status, _, _ = self.request('POST', '/preparation/access/start', body, {
            'Content-Type': 'application/x-www-form-urlencoded', 'Cookie': 'benchmark_session=session-token'})
        self.assertEqual(400, status)
        self.assertTrue(self.executor.requests.empty())

    def test_refuse_un_retour_non_normalise_non_ascii_ou_non_texte(self):
        for value, media in (('/preparation/../x', 'application/x-www-form-urlencoded'),
                             ('/preparation/échec', 'application/x-www-form-urlencoded'),
                             (5, 'application/json')):
            body = (urlencode({'csrf_token': 'csrf', 'return': value}).encode()
                    if media.endswith('form-urlencoded') else
                    json.dumps({'csrf_token': 'csrf', 'return': value}).encode())
            status, _, _ = self.request('POST', '/preparation/access/start', body, {
                'Content-Type': media, 'Cookie': 'benchmark_session=session-token'})
            self.assertEqual(400, status, value)

    def test_depart_conserve_le_cookie_de_session_renouvele(self):
        self.executor.start_cookie = 'session-renouvelee'
        body = urlencode({'csrf_token': 'csrf', 'return': '/preparation'}).encode()
        status, headers, _ = self.request('POST', '/preparation/access/start', body, {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': 'benchmark_session=session-token'})
        self.assertEqual(303, status)
        cookies = headers.get_all('Set-Cookie')
        self.assertEqual(2, len(cookies))
        self.assertTrue(any(value.startswith('benchmark_session=session-renouvelee;') for value in cookies))
        self.assertTrue(any(value.startswith('benchmark_access_callback=') for value in cookies))

    def test_callback_navigateur_post_ne_contourne_pas_csrf(self):
        body = urlencode({'code': 'code-direct'}).encode()
        status, _, raw = self.request('POST', '/preparation/access/callback', body, {
            'Content-Type': 'application/x-www-form-urlencoded', 'Cookie': 'benchmark_session=session-token'})
        self.assertEqual(400, status)
        self.assertNotIn(b'code-direct', raw)
        self.assertTrue(self.executor.requests.empty())

    def test_url_publique_requise_pour_activer(self):
        self.assertEqual('https://benchmark.example/preparation/access/callback',
                         _public_callback_url('https://benchmark.example/'))
        with self.assertRaises(ValueError):
            _public_callback_url('http://benchmark.example')

    def test_post_configurations_et_plafond_redirigent(self):
        path = '/preparation/dossiers/d1/configurations'
        body = urlencode([('csrf_token', 'csrf'), ('models', 'modele-a'),
                          ('models', 'modele-b'), ('tier', 'standard')]).encode()
        status, headers, _ = self.request('POST', path, body, {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': 'benchmark_session=session-token'})
        self.assertEqual((303, path), (status, headers['Location']))
        request = self.executor.requests.get_nowait()
        self.assertEqual(['modele-a', 'modele-b'], request['body']['models'])

        cap_path = '/preparation/dossiers/d1/campaigns/d1-c1/cap'
        body = urlencode({'csrf_token': 'csrf', 'cap_usd': '75.00'}).encode()
        status, headers, _ = self.request('POST', cap_path, body, {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': 'benchmark_session=session-token'})
        self.assertEqual((303, '/preparation/dossiers/d1/campaigns/d1-c1/conditions'),
                         (status, headers['Location']))


if __name__ == '__main__':
    unittest.main()
