"""Connexion Openrouter relayée par le web, sans appel fournisseur."""
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import json
import multiprocessing
from pathlib import Path
import queue
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from benchmark.storage import _strict_json
from benchmark_web import views
from benchmark_web.server import _public_callback_url, serve_web
from tests.test_s6_regressions import Markup


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
        self.home_value = {'csrf_token': 'csrf', 'availability': {}}
        self.raw_response = None
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
                    if self.raw_response is not None:
                        # Trame brute imposée par le test : le web doit y voir une panne
                        connection.sendall(self.raw_response)
                        continue
                    if request['path'] == '/preparation/access' and request['method'] == 'GET':
                        result = {'status': 200, 'value': {'connected': False, 'status': 'disconnected'},
                                  'piece': False, 'cookie': 'session-token'}
                    elif request['path'] == '/preparation' and request['method'] == 'GET':
                        result = {'status': 200, 'value': self.home_value,
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
                    elif request['method'] == 'POST' and request['path'].endswith(('/start', '/evaluate')):
                        result = {'status': 202, 'value': {'kind': 'campaign_launch'},
                                  'piece': False, 'cookie': None}
                    else:
                        result = {'status': 404, 'value': {'error': 'NOT_FOUND'},
                                  'piece': False, 'cookie': None}
                    connection.sendall((_strict_json(result) + '\n').encode())


class AccessViewTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))

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
        self.assertIn('Connecter mon compte Openrouter', disconnected)

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
            self.assertIn('Connexion Openrouter indisponible.', page)
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
            'current_tier': 'high', 'available_tiers': ['low', 'medium', 'high'],
            'configurations': [
                {'model': 'modele-a', 'estimate': {'amount_usd': '1.20'}},
                {'model': 'modele-b', 'estimate': {'amount_usd': '2.30'},
                 'effort_limit': 'not_adjustable'},
            ],
            'current_campaign_id': 'd1-c1', 'estimate_total_usd': '3.50',
            'cap_usd': '50.00', 'cap_source': 'default', 'superseded': [],
            'estimate_available': True, 'assumptions': {},
        }
        page = views.render(value, 'csrf').decode()
        self.assertIn('action="/preparation/dossiers/d1/configurations"', page)
        self.assertEqual(2, page.count('name="models"'))
        markup = Markup(page.encode())
        options = {attrs['value']: attrs for tag, attrs in markup.tags if tag == 'option'}
        self.assertIn('selected', options['high'])
        self.assertNotIn('selected', options['low'])
        self.assertTrue(any(tag == 'select' and attrs.get('name') == 'tier'
                            and attrs.get('aria-describedby') == 'reasoning-help'
                            for tag, attrs in markup.tags))
        self.assertIn('Un niveau incompatible est refusé', page)
        self.assertIn('sans garantir une meilleure réponse', page)
        self.assertIn('palier de raisonnement non réglable', page)
        for technical in ('modele-a', 'modele-b'):
            self.assertIn('<details><summary>Identifiant technique</summary><code>' +
                          technical + '</code></details>', page)
        self.assertIn('Estimation totale : 3,50 USD', page)
        self.assertNotIn('Plafond :', page)
        self.assertIn('estimation 1,20 USD', page)
        self.assertIn('estimation 2,30 USD', page)
        self.assertIn('/campaigns/d1-c1/conditions', page)

    def test_page_configurations_sans_releve(self):
        page = views.render({
            'kind': 'configurations', 'dossier_id': 'd1',
            'catalogue_available': False, 'models': [],
            'detail': 'Relevé de modèles indisponible', 'configurations': [],
        }, 'csrf').decode()
        self.assertIn('Relevé de modèles indisponible', page)
        self.assertNotIn('<form', page)

    def test_judgment_estimate_is_displayed_before_launch_without_inventing_a_price(self):
        value = self.campaign({'status': 'connected'})
        value.update(checks=[], launchable=False, cap_usd='30.00', judgment_estimate_usd='0.125')
        page = views.render(value, 'csrf').decode()
        self.assertIn('Évaluation estimée : 0,125 USD', page)
        self.assertIn('financée par votre clé personnelle', page)
        value.pop('judgment_estimate_usd')
        self.assertNotIn('Évaluation estimée', views.render(value, 'csrf').decode())

    def test_judgment_preflight_failure_remains_readable(self):
        value = self.campaign({'status': 'connected'})
        value.update(checks=[{'key': 'judgment_available', 'ok': False,
                              'detail': 'Budget insuffisant pour l’évaluation'}],
                     launchable=False, cap_usd='30.00')
        page = views.render(value, 'csrf').decode()
        self.assertIn('Budget insuffisant pour l’évaluation', page)
        self.assertNotIn('>Lancer la comparaison</button>', page)

    def test_no_local_cap_form_or_script_before_launch(self):
        value = self.campaign({'status': 'connected'})
        value.update(checks=[], launchable=False, cap_usd='50.00')
        page = views.render(value, 'csrf').decode()
        self.assertNotIn('/cap"', page)
        self.assertNotIn('Modifier le plafond', page)
        self.assertIsNone(views.page_script(value))

    def test_recapitulatif_demandeur_passant_et_bloquant(self):
        base = self.campaign({'status': 'connected', 'limit_remaining_usd': '12.50'})
        base.update(
            checks=[
                {'key': 'example_validated', 'ok': True, 'detail': 'Exemple validé'},
                {'key': 'example_qualified', 'ok': True, 'detail': 'Exemple qualifié',
                 'findings': [{'kind': 'cohérence', 'severity': 'note',
                               'text': 'Quantité à confirmer'}]},
                {'key': 'configurations_available', 'ok': True,
                 'detail': 'Tous les modèles sont disponibles'},
                {'key': 'access_connected', 'ok': True,
                 'detail': {'limit_remaining_usd': '12.50', 'limit_usd': '20'}},
                {'key': 'estimate_available', 'ok': True,
                 'detail': 'Estimation totale : 3.50 USD'},
            ],
            launchable=True, cap_usd='50.00', cap_source='default',
            estimate_total_usd='3.50')
        page = views.render(base, 'csrf').decode()
        self.assertIn('✓ Exemple validé', page)
        self.assertIn('Constats de qualification', page)
        self.assertIn('Quantité à confirmer', page)
        self.assertIn('Crédit restant : 12.50 USD ; plafond de la clé : 20 USD', page)
        self.assertNotIn('action="/preparation/dossiers/d1/campaigns/c1/cap"', page)
        self.assertNotIn('L’arrêt intervient après le paiement de l’appel en cours.', page)
        self.assertNotIn('La dépense peut donc dépasser le plafond du montant du dernier appel.', page)
        self.assertIn('>Lancer la comparaison</button>', page)
        parsed = Markup(page.encode())
        for tag, attrs in parsed.tags:
            if tag == 'form':
                self.assertEqual('post', attrs['method'])
                self.assertTrue(attrs['action'].startswith('/preparation/'))

        blocked = dict(base, launchable=False,
                       checks=[dict(check) for check in base['checks']])
        blocked['checks'][2] = {'key': 'configurations_available', 'ok': False,
                                'detail': 'Modèle à choisir de nouveau'}
        page = views.render(blocked, 'csrf').decode()
        self.assertIn('✕ Modèle à choisir de nouveau', page)
        self.assertIn('/preparation/dossiers/d1/configurations', page)
        self.assertNotIn('>Lancer la comparaison</button>', page)

    def test_campaign_followup_only_polls_active_work_and_keeps_received_distinct(self):
        for state, admission, incident, active, terminal, message in (
            ('EMISSION_POSSIBLE', True, None, True, False, 'Comparaison en cours'),
            ('INTENT_RECORDED', True, None, True, False, 'En attente de démarrage'),
            ('EMISSION_POSSIBLE', False, None, False, False, 'Admission fermée'),
            ('AMBIGUOUS', True, None, False, False, 'Vérification requise'),
            ('RECEIVED', True, 'LENGTH', False, False, 'Incident'),
            ('RECEIVED', True, None, False, True, 'Réponses reçues'),
        ):
            with self.subTest(state=state, admission=admission, incident=incident):
                value = self.campaign({'status': 'connected'})
                value.update(checks=[], launchable=False, cap_usd='50.00')
                value['campaign'].update(
                    task={'revision': 2}, admission_open=admission,
                    attempts=[{'state': state, 'incident': incident}],
                    cells=[{'configuration_id': 'x', 'state': state}],
                    panel=[{'id': 'x', 'model': 'Modèle A'}])
                page = views.render(value, 'csrf').decode()
                self.assertEqual(active, 'id="preparation-progress"' in page)
                self.assertEqual(terminal, 'data-results-href=' in page)
                self.assertIn(message, page)
                self.assertNotIn('action=', page)
                if terminal:
                    self.assertIn('En attente d’évaluation', page)
                if active or terminal:
                    self.assertIn('<script>' + views.page_script(value) + '</script>', page)
                if active:
                    self.assertNotIn('Comparer les résultats et lire les preuves</a>', page)
                nav = page.split('<nav class="steps"', 1)[1].split('</nav>', 1)[0]
                self.assertIn('/preparation/dossiers/d1/revisions/2#exemple', nav)
                self.assertIn('/preparation/dossiers/d1/campaigns/c1', nav)

    def test_automatic_judgment_keeps_followup_until_verdicts_are_complete(self):
        for status, active, ready in (('NOT_STARTED', False, False),
                                      ('WAITING', True, False), ('RUNNING', True, False),
                                      ('COMPLETE', False, True), ('BLOCKED', False, False)):
            with self.subTest(status=status):
                value = self.campaign({'status': 'connected'})
                value.update(checks=[], launchable=False, cap_usd='30.00')
                value['campaign'].update(
                    admission_open=True, attempts=[{'state': 'RECEIVED'}],
                    cells=[{'configuration_id': 'x', 'state': 'RECEIVED'}],
                    panel=[{'id': 'x', 'model': 'Modèle A'}],
                    judgment={'status': status, 'total': 1, 'completed': int(ready),
                              'reason': 'Budget insuffisant' if status == 'BLOCKED' else None,
                              'can_start': status == 'NOT_STARTED'})
                page = views.render(value, 'csrf').decode()
                self.assertEqual(active, 'id="preparation-progress"' in page)
                self.assertEqual(ready, 'data-results-href=' in page)
                self.assertIn('1 réponse(s) reçue(s) sur 1', page)
                self.assertIn(str(int(ready)) + ' évaluation(s) terminée(s) sur 1', page)
                self.assertEqual(status == 'NOT_STARTED',
                                 'action="/preparation/dossiers/d1/campaigns/c1/evaluate"' in page)
                if status == 'NOT_STARTED':
                    self.assertIn('Évaluer les réponses conservées', page)
                    self.assertIn('name="confirm" value="yes"', page)
                    self.assertIn('name="csrf_token" value="csrf"', page)
                if status == 'BLOCKED':
                    self.assertIn('Budget insuffisant', page)
                    self.assertNotIn('id="preparation-progress"', page)
                if not ready:
                    self.assertNotIn('Comparer les résultats et lire les preuves</a>', page)
                self.assertEqual(active or ready, views.page_script(value) is not None)

    def test_ancien_plafond_absent_apres_lancement(self):
        value = self.campaign({'status': 'connected', 'limit_remaining_usd': '12.50'})
        value.update(
            checks=[
                {'key': 'example_validated', 'ok': True, 'detail': 'Exemple validé'},
                {'key': 'example_qualified', 'ok': True, 'detail': 'Exemple qualifié'},
                {'key': 'configurations_available', 'ok': True,
                 'detail': 'Tous les modèles sont disponibles'},
                {'key': 'access_connected', 'ok': True,
                 'detail': {'limit_remaining_usd': '12.50', 'limit_usd': '20'}},
                {'key': 'estimate_available', 'ok': True,
                 'detail': 'Estimation totale : 3,50 USD'},
            ],
            launchable=False, cap_usd='50.00')
        value['campaign'] = dict(
            value['campaign'], attempts=[{'state': 'INTENT_RECORDED'}],
            cells=[{'configuration_id': 'x', 'state': 'INTENT_RECORDED'}],
            panel=[{'id': 'x', 'model': 'Modèle A'}], admission_open=True)
        page = views.render(value, 'csrf').decode()
        self.assertNotIn('La dépense peut donc dépasser le plafond du montant du dernier appel.', page)
        self.assertNotIn('id="cap_usd"', page)
        self.assertIn('Lancement enregistré', page)

    def test_dates_lisibles_distinctes_dans_la_meme_minute(self):
        first = views.date_lisible_utc('2026-09-16T12:00:01+00:00')
        second = views.date_lisible_utc('2026-09-16T12:00:02+00:00')
        self.assertEqual('16 septembre 2026 à 12:00:01 UTC', first)
        self.assertEqual('16 septembre 2026 à 12:00:02 UTC', second)
        self.assertEqual('15 septembre 2026 à 12:00:00 UTC',
                         views.date_lisible_utc('2026-09-15T12:00:00+00:00'))


class AccessServerTests(unittest.TestCase):
    def test_preparation_posts_redirect_only_successful_html_to_dossier(self):
        for path, code in (('/preparation/dossiers', 202),
                           ('/preparation/dossiers/d1/messages', 202),
                           ('/preparation/dossiers/d1/validation', 202),
                           ('/preparation/dossiers/d1/validation', 200)):
            value = {'dossier_id': 'd1', 'operation_id': 'op'}
            self.executor.raw_response = (_strict_json({'status': code, 'value': value}) + '\n').encode()
            body = urlencode({'csrf_token': 'csrf'}).encode()
            for accept in ('text/html', 'application/json'):
                with self.subTest(path=path, code=code, accept=accept):
                    status, headers, raw = self.request('POST', path, body, {
                        'Content-Type': 'application/x-www-form-urlencoded', 'Accept': accept})
                    self.assertEqual(code if accept == 'application/json' else 303, status)
                    if accept == 'application/json':
                        self.assertEqual(value, json.loads(raw))
                    else:
                        self.assertEqual('/preparation/dossiers/d1', headers['Location'])
            self.executor.raw_response = b'{"status":403,"value":{"error":"Refus"}}\n'
            status, headers, _ = self.request('POST', path, body, {
                'Content-Type': 'application/x-www-form-urlencoded'})
            self.assertEqual(403, status)
            self.assertIsNone(headers['Location'])

    def test_campaign_csp_matches_exact_cap_or_read_only_followup_script(self):
        from base64 import b64encode
        from hashlib import sha256
        value = AccessViewTests.campaign({'status': 'connected'})
        value.update(checks=[], launchable=False, cap_usd='50.00', csrf_token='csrf')
        for state in (None, 'EMISSION_POSSIBLE', 'RECEIVED', 'INTENT_RECORDED'):
            with self.subTest(state=state):
                if state:
                    value['campaign'].update(admission_open=True,
                        attempts=[{'state': state}], cells=[{'state': state, 'configuration_id': 'x'}],
                        panel=[{'id': 'x', 'model': 'Modèle fictif'}])
                self.executor.raw_response = json.dumps({'status': 200, 'value': value,
                    'piece': False, 'cookie': None}).encode() + b'\n'
                status, headers, raw = self.request('GET', '/preparation/dossiers/d1/campaigns/c1/conditions')
                self.assertEqual(200, status)
                script = views.page_script(value)
                if state is None:
                    self.assertIsNone(script)
                expected = CSP
                if script:
                    expected += "; script-src 'sha256-" + b64encode(sha256(script.encode()).digest()).decode() + "'"
                    if state:
                        expected += "; connect-src 'self'"
                    self.assertEqual(script.encode(), raw.split(b'<script>')[1].split(b'</script>')[0])
                else:
                    self.assertNotIn(b'<script>', raw)
                self.assertEqual(expected, headers['Content-Security-Policy'])

    def test_cookie_persistant_non_renouvele_par_une_lecture(self):
        _, headers, _ = self.request('GET', '/preparation/access')
        cookie = SimpleCookie(headers['Set-Cookie'])['benchmark_session']
        self.assertEqual('2592000', cookie['max-age'])
        self.assertTrue(cookie['httponly'])
        self.assertTrue(cookie['secure'])
        self.assertEqual('Strict', cookie['samesite'])
        self.assertEqual('/preparation', cookie['path'])
        self.assertEqual('', cookie['domain'])
        request_headers = {'Cookie': 'benchmark_session=' + cookie.value,
                           'Accept': 'application/json'}
        _, renewed, _ = self.request('GET', '/preparation', headers=request_headers)
        self.assertIsNone(renewed.get('Set-Cookie'))
        _, refused, _ = self.request('GET', '/preparation/unknown', headers=request_headers)
        self.assertIsNone(refused.get('Set-Cookie'))

    def test_personal_key_is_never_reflected_and_success_redirects(self):
        key = 'sk-or-v1-private-fixture'
        body = urlencode({'csrf_token': 'csrf', 'key': key}).encode()
        for code in (200, 403):
            self.executor.raw_response = json.dumps({'status': code,
                'value': {'error': 'Clé refusée'} if code == 403 else {'connected': True},
                'piece': False, 'cookie': None}).encode() + b'\n'
            status, headers, raw = self.request('POST', '/preparation/access/key', body,
                {'Content-Type': 'application/x-www-form-urlencoded',
                 'Cookie': 'benchmark_session=session-token'})
            self.assertEqual(303 if code == 200 else 403, status)
            self.assertNotIn(key.encode(), raw)
            self.assertNotIn(key, str(headers))
            if code == 200:
                self.assertEqual('/preparation', headers['Location'])
                self.assertEqual('2592000', SimpleCookie(headers['Set-Cookie'])['benchmark_session']['max-age'])

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
        callback_cookie = next(value.split(';', 1)[0] for value in headers.get_all('Set-Cookie')
                               if value.startswith('benchmark_access_callback='))
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
        persistent = next(value for value in headers.get_all('Set-Cookie')
                          if value.startswith('benchmark_session='))
        self.assertEqual('2592000', SimpleCookie(persistent)['benchmark_session']['max-age'])
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
        callback_cookie = next(value.split(';', 1)[0] for value in headers.get_all('Set-Cookie')
                               if value.startswith('benchmark_access_callback='))
        self.executor.callback_result = {
            'status': 403,
            'value': {'error': 'Échange Openrouter refusé', 'error_code': 'ACCESS_EXCHANGE_FAILED'},
            'piece': False, 'cookie': None}

        status, headers, raw = self.request(
            'GET', '/preparation/access/callback?code=code-a-ne-pas-rendre',
            headers={'Cookie': callback_cookie})

        self.assertEqual(403, status)
        self.assertIn(b'change Openrouter refus', raw)
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

    def test_trame_d_executeur_illisible_annonce_une_panne_et_non_un_formulaire(self):
        # La réponse est déjà partie quand la trame se révèle illisible : rien n'est non admis
        for wire in (b'not-json\n', b'{"status":200}\n', b'{"status":200,"value":{}}'):
            self.executor.raw_response = wire
            status, _, raw = self.request('GET', '/preparation/access', headers={
                'Accept': 'application/json'})
            self.assertEqual(503, status, wire)
            self.assertTrue(json.loads(raw)['unavailable'])
            self.assertNotIn('Formulaire invalide', raw.decode())
            self.assertNotIn('Aucun nouvel appel admis', raw.decode())

            status, _, raw = self.request('GET', '/preparation/access')
            self.assertEqual(503, status, wire)
            self.assertNotIn('Formulaire invalide', raw.decode())

    def test_valeur_d_executeur_hors_contrat_annonce_une_panne_et_non_un_succes(self):
        # Sans contrôle central, la vue JSON rendait 200 avec un corps nul et la page HTML rompait
        for wire in (b'{"status":200,"value":null,"piece":false,"cookie":null}\n',
                     b'{"status":200,"value":[1,2],"piece":false,"cookie":null}\n',
                     b'{"status":200,"value":"texte","piece":false,"cookie":null}\n',
                     b'{"status":200,"value":"zz","piece":true,"cookie":null}\n',
                     b'{"status":200,"value":null,"piece":true,"cookie":null}\n',
                     b'{"status":200,"value":{},"piece":"true","cookie":null}\n',
                     b'{"status":200,"value":{},"piece":false,"cookie":5}\n',
                     b'{"status":100,"value":{},"piece":false,"cookie":null}\n',
                     b'{"status":700,"value":{},"piece":false,"cookie":null}\n'):
            self.executor.raw_response = wire
            status, _, raw = self.request('GET', '/preparation/access', headers={
                'Accept': 'application/json'})
            self.assertEqual(503, status, wire)
            self.assertTrue(json.loads(raw)['unavailable'], wire)

            # Sans panne annoncée, la page HTML tomberait sur une connexion coupée sans réponse
            status, _, raw = self.request('GET', '/preparation/access')
            self.assertEqual(503, status, wire)
            self.assertNotIn('Formulaire invalide', raw.decode())

    def test_piece_valide_et_enveloppe_d_erreur_restent_servies(self):
        self.executor.raw_response = (b'{"status":200,"value":"48656c6c6f","piece":true,'
                                      b'"cookie":null}\n')
        status, headers, raw = self.request('GET', '/preparation/dossiers/d1/piece')
        self.assertEqual((200, b'Hello'), (status, raw))
        self.assertEqual('inline; filename="piece.txt"', headers['Content-Disposition'])

        # Une enveloppe de refus omet `piece` et `cookie` : elle doit rester rendue telle quelle
        self.executor.raw_response = b'{"status":403,"value":{"error":"Motif interdit"}}\n'
        status, _, raw = self.request('GET', '/preparation/access', headers={
            'Accept': 'application/json'})
        self.assertEqual((403, {'error': 'Motif interdit'}), (status, json.loads(raw)))

    def test_defaillance_apres_relais_ne_se_presente_pas_comme_un_formulaire_invalide(self):
        # Une réponse d'exécuteur incomplète est un défaut de protocole, pas une saisie fautive
        self.executor.home_value = {'availability': {}}
        status, _, raw = self.request('GET', '/preparation/access',
                                      headers={'Cookie': 'benchmark_session=session-token'})
        self.assertEqual(500, status)
        self.assertIn('Défaillance interne du service', raw.decode())
        self.assertNotIn('Formulaire invalide', raw.decode())

        status, _, raw = self.request('GET', '/preparation/access', headers={
            'Cookie': 'benchmark_session=session-token', 'Accept': 'application/json'})
        self.assertEqual(200, status, 'la vue JSON ne dépend pas du jeton CSRF de la page')

    def test_evaluation_post_redirects_to_read_only_followup(self):
        path = '/preparation/dossiers/d1/campaigns/d1-c1/evaluate'
        body = urlencode({'csrf_token': 'csrf', 'confirm': 'yes'}).encode()
        status, headers, _ = self.request('POST', path, body, {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': 'benchmark_session=session-token'})
        self.assertEqual((303, path.removesuffix('/evaluate') + '/conditions'),
                         (status, headers.get('Location')))
        request = self.executor.requests.get_nowait()
        self.assertEqual(path, request['path'])
        self.assertEqual({'csrf_token': 'csrf', 'confirm': 'yes'}, request['body'])
        self.assertTrue(self.executor.requests.empty())
        status, headers, _ = self.request('POST', path, body, {
            'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json',
            'Cookie': 'benchmark_session=session-token'})
        self.assertEqual(202, status)
        self.assertNotIn('Location', headers)

    def test_url_publique_requise_pour_activer(self):
        self.assertEqual('https://benchmark.example/preparation/access/callback',
                         _public_callback_url('https://benchmark.example/'))
        with self.assertRaises(ValueError):
            _public_callback_url('http://benchmark.example')

    def test_post_configurations_et_lancement_redirigent(self):
        path = '/preparation/dossiers/d1/configurations'
        body = urlencode([('csrf_token', 'csrf'), ('models', 'modele-a'),
                          ('models', 'modele-b'), ('tier', 'standard')]).encode()
        status, headers, _ = self.request('POST', path, body, {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': 'benchmark_session=session-token'})
        self.assertEqual((303, path), (status, headers['Location']))
        request = self.executor.requests.get_nowait()
        self.assertEqual(['modele-a', 'modele-b'], request['body']['models'])

        start_path = '/preparation/dossiers/d1/campaigns/d1-c1/start'
        body = urlencode({'csrf_token': 'csrf', 'manifest_version': '1',
                          'frozen_at': '2026-09-15T00:00:00Z', 'confirm': 'yes'}).encode()
        status, headers, _ = self.request('POST', start_path, body, {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': 'benchmark_session=session-token'})
        self.assertEqual((303, '/preparation/dossiers/d1/campaigns/d1-c1/conditions'),
                         (status, headers['Location']))
        request = self.executor.requests.get_nowait()
        self.assertEqual(1, request['body']['manifest_version'])

        invalid = urlencode({'csrf_token': 'csrf', 'manifest_version': 'abc',
                             'frozen_at': '2026-09-15T00:00:00Z', 'confirm': 'yes'}).encode()
        status, _, _ = self.request('POST', start_path, invalid, {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': 'benchmark_session=session-token'})
        self.assertEqual(400, status)
        self.assertTrue(self.executor.requests.empty())


if __name__ == '__main__':
    unittest.main()
