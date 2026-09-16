"""Owner launch uses private admission, never authority supplied by HTTP"""
import ast
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark import (campaigns as c, evaluation, judgment, model_catalogue,
                       preparation as p, provider_access, qualification as q, restitution, storage)
from benchmark_web import views
from benchmark_web import projection
from tests.test_openrouter_qualification import qualify_fixture
from tests.test_s2_review_regressions import response_for
from tests.test_s5_regressions import RESPONSIBLE, EVALUATION_AUTHORITY, findings
from tests.test_configurations import NOW, model
from tests.test_provider_access import AccessTransport, SECRET
from tests.test_s3_regressions import fixture, specification, check, ACTOR, AUTHORITY
from tests.test_s4_regressions import inputs, manifest, response


class CampaignLaunch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name).resolve() / 'private'
        self.sid, view, ref = fixture(self.data)
        q.initialize(self.data)
        c.initialize(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        candidate = q.draft(self.store, 'fixture', view['revision'], specification(ref))
        qualified = q.qualify(self.store, candidate['contract_sha256'], reviewer=ACTOR, check=check)
        q.approve(self.store, candidate['contract_sha256'], qualified['qualification_id'], actor=ACTOR, authority=AUTHORITY)
        self.store.create_budget('local-comparison', '40', 'TEST')
        c.create(self.store, manifest(candidate))
        self.snapshot = c.inspect(self.store, 'local-comparison')

    def admit(self, owner=True):
        record = c.admit(self.store, 'local-comparison', *inputs(self.snapshot), owner_launch=owner)
        return dict(manifest_version=self.snapshot['manifest']['version'],
                    frozen_at=self.snapshot['manifest']['conditions']['frozen_at'],
                    admission_id=record['admission_id'], confirm='yes')

    def launch(self, body, sid=None):
        return c.launch(self.store, sid or self.sid, 'fixture', 'local-comparison', body)

    def test_route_ne_masque_pas_une_keyerror_interne(self):
        with patch.object(p, 'session', return_value=(self.sid, 'csrf', 'token')), \
                patch.object(c, 'inspect', side_effect=KeyError('intégrité interne')), \
                self.assertRaisesRegex(KeyError, 'intégrité interne'):
            p.dispatch(self.store, 'GET',
                       '/preparation/dossiers/fixture/campaigns/local-comparison/conditions',
                       'token', None, 'a' * 40, True)

    def test_legacy_and_extra_http_authority_are_denied(self):
        body = self.admit(False)
        with self.assertRaises(p.Denied):
            self.launch(body)
        self.assertFalse(c.launch_view(self.store, self.sid, 'fixture', 'local-comparison')['can_launch'])
        with self.assertRaises(ValueError):
            self.launch(dict(body, authority='Ayo'))
        self.assertEqual([], c.inspect(self.store, 'local-comparison')['attempts'])

    def test_owner_binding_stale_confirmation_and_no_judge_disclosure(self):
        body = self.admit()
        other, _, _ = p.session(self.store, None, create=True)
        with self.assertRaises(p.Denied):
            self.launch(body, other)
        with self.assertRaises(storage.ConflictError):
            self.launch(dict(body, admission_id='stale'))
        with self.assertRaises(ValueError):
            self.launch(dict(body, confirm='no'))
        projection = c.launch_view(self.store, self.sid, 'fixture', 'local-comparison')
        self.assertNotIn('reference_piece_ids', projection['criteria'])
        self.assertNotIn('witnesses', projection['criteria'])
        self.assertTrue(projection['can_launch'])

    def test_concurrent_confirmation_emits_each_cell_once(self):
        body = self.admit()
        def submit():
            with closing(storage.Store(self.data)) as store:
                return c.launch(store, self.sid, 'fixture', 'local-comparison', body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual([0, 2], sorted(map(len, replies)))
        calls = []
        def transport(op, request):
            calls.append(op['operation_id'])
            return response(op, request)
        c.execute_launch(self.data, next(r for r in replies if r), transport)
        self.assertEqual(2, len(calls))
        self.assertEqual([], self.launch(body))
        self.assertEqual(['RECEIVED'] * 2, [a['state'] for a in c.inspect(self.store, 'local-comparison')['attempts']])

    def test_page_confirmation_refuses_stale_conditions_then_launches(self):
        body = self.admit()
        page = views.render(c.launch_view(self.store, self.sid, 'fixture', 'local-comparison'), 'csrf').decode()
        self.assertIn(f'name="manifest_version" value="{body["manifest_version"]}"', page)
        self.assertIn(f'name="frozen_at" value="{body["frozen_at"]}"', page)
        self.assertNotIn('manifest_sha256', page)
        for field, value in (('manifest_version', body['manifest_version'] + 1),
                             ('frozen_at', '2020-01-01T00:00:00+00:00')):
            with self.subTest(field=field), self.assertRaises(storage.ConflictError):
                self.launch(dict(body, **{field: value}))
            self.assertEqual([], c.inspect(self.store, 'local-comparison')['attempts'])
        self.assertEqual(2, len(self.launch(body)))

    def test_reservations_roll_back_together(self):
        from unittest.mock import patch
        body = self.admit()
        original = c._reserve
        def fail_second(store, connection, snapshot, cell, aid):
            if cell == 'y':
                raise storage.BudgetError('fixture')
            return original(store, connection, snapshot, cell, aid)
        with patch.object(c, '_reserve', fail_second), self.assertRaises(storage.BudgetError):
            self.launch(body)
        self.assertEqual([], c.inspect(self.store, 'local-comparison')['attempts'])
        self.assertEqual('0', self.store.inspect_budget('local-comparison')['reserved'])

    def test_stop_before_emission_and_unknown_cost_stop_next_cell(self):
        body = self.admit()
        attempts = self.launch(body)
        c.stop(self.store, 'local-comparison')
        calls = []
        c.execute_launch(self.data, attempts, lambda *args: calls.append(args))
        self.assertEqual([], calls)
        with self.assertRaises(p.Denied):
            self.launch(body)

    def test_unknown_cost_closes_admission_before_next_cell(self):
        attempts = self.launch(self.admit())
        calls = []
        def transport(op, request):
            calls.append(op['operation_id'])
            result = response(op, request)
            result['cost'].update(status='UNKNOWN', amount=None)
            return result
        c.execute_launch(self.data, attempts, transport)
        self.assertEqual(1, len(calls))
        self.assertIsNone(c.inspect(self.store, 'local-comparison')['admission'])

    def test_restore_blocks_launch(self):
        body = self.admit()
        (self.data / 'restore.json').write_text('{}')
        with self.assertRaises((ValueError, storage.ConflictError)):
            self.launch(body)

    def test_changed_package_requires_new_validation(self):
        from tests.test_s2_review_regressions import response_for
        body = self.admit()
        view = p.view(self.store, self.sid, 'fixture')
        op, _ = p.submit(self.store, self.sid, 'fixture', dict(action_id='change', revision=view['revision'], kind='correct', message='Nouvelle consigne'), 'a'*40, True)
        def changed(operation, request):
            value = response_for(operation)
            value['receipt']['result']['package']['candidate']['instruction'] = 'Consigne révisée'
            return value
        p.execute(self.data, op, changed)
        with self.assertRaises((ValueError, storage.ConflictError)):
            self.launch(body)
        self.assertEqual([], c.inspect(self.store, 'local-comparison')['attempts'])

    def test_dispatch_enforces_csrf_and_candidate_transport(self):
        from unittest.mock import patch
        body = self.admit()
        path = '/preparation/dossiers/fixture/campaigns/local-comparison/start'
        with patch.object(p, 'session', return_value=(self.sid, 'csrf', None)):
            with self.assertRaises(p.Denied):
                p.dispatch(self.store, 'POST', path, 'token', body, 'a'*40, True, candidate_transport=response)
            with self.assertRaises(p.Denied):
                p.dispatch(self.store, 'POST', path, 'token', dict(body, csrf_token='csrf'), 'a'*40, True)
            result = p.dispatch(self.store, 'POST', path, 'token', dict(body, csrf_token='csrf'), 'a'*40, True, candidate_transport=response)
            self.assertEqual(202, result[0])
            self.assertEqual(2, len(result[3]['candidate_attempts']))
            repeated = p.dispatch(self.store, 'POST', path, 'token', dict(body, csrf_token='csrf'), 'a'*40, True, candidate_transport=response)
            self.assertIsNone(repeated[3])


class RequesterCampaignLaunch(unittest.TestCase):
    def setUp(self):
        network = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        network.start()
        self.addCleanup(network.stop)
        temporary = tempfile.TemporaryDirectory(prefix='requester-launch-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name).resolve() / 'private'
        def with_quality(operation):
            value = response_for(operation)
            value['receipt']['result']['package']['candidate']['criteria'] = {
                'eliminatory': [], 'obligations': ['Toutes les actions présentes'],
                'quality': [{'label': 'Clarté', 'scale': ['excellent', 'acceptable', 'faible'],
                             'favorable': 'excellent'}]}
            return value
        with patch('tests.test_s3_regressions.response_for', side_effect=with_quality):
            self.sid, preview, reference = fixture(self.data)
        self.revision = preview['revision']
        q.initialize(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        qualify_fixture(self.data, self.store, self.sid, 'fixture', preview)
        c.initialize(self.data)
        evaluation.initialize(self.data)
        provider_access.initialize(self.data)
        rows = [
            model('openai/gpt-5.6-sol', 'openai', ['low', 'high'],
                  prompt='0.0000001', completion='0.0000001'),
            model('deepseek/deepseek-v4.1-flash', 'deepseek', [],
                  prompt='0.0000001', completion='0.0000001'),
        ]
        document = {'models': [row[0] for row in rows],
                    'endpoints': {row[0]['id']: row[1] for row in rows}}
        self.store._connection.execute(model_catalogue.TABLE_SQL)
        self.store._connection.execute(
            'INSERT INTO s2_model_catalogue VALUES (?,?)',
            (NOW.isoformat(), storage._strict_json(document)))
        identity = {'package': '@earendil-works/pi-coding-agent', 'version': '0.85.1',
                    'sha256': '1' * 64, 'bridge_sha256': '2' * 64,
                    'node_version': 'v24.0.0', 'node_sha256': '3' * 64,
                    'scope': 'Fixture Pi locale'}
        with patch.object(model_catalogue, '_now', return_value=NOW):
            current = c.prepare_configurations(
                self.store, self.sid, 'fixture',
                {'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'],
                 'tier': 'standard'}, identity)
        self.campaign_id = current['current_campaign_id']
        self.access = AccessTransport()

    def connect(self):
        provider_access.start(self.store, self.sid, SECRET,
                              'https://example.test/preparation/access/callback')
        provider_access.callback(self.store, self.sid, SECRET, 'code', self.access)

    def test_restitution_sans_colonne_qualitative_et_refus_du_jugement_expert(self):
        self.connect()
        attempts = c.launch(self.store, self.sid, 'fixture', self.campaign_id,
                            self.body(), access_secret=SECRET, access_transport=self.access)
        def received(operation, request):
            value = response(operation, request)
            value['cost'].update(currency='USD', amount='0.001')
            return value
        c.execute_launch(self.data, attempts, received, access_secret=SECRET, access_transport=self.access)
        result = restitution.comparison(self.store, self.sid, 'fixture', self.campaign_id)
        self.assertEqual(['cost'], [column['id'] for column in result['columns']])
        self.assertEqual([], result['rows'])
        self.assertEqual(2, len(result['pending_attempts']))
        self.assertIn('Aucune tentative évaluée', views.render(result, 'csrf').decode())
        contract = c._current_contract(self.store, self.store._connection, 'fixture')
        self.assertEqual('Clarté', projection._libelles_criteres({'qualification': {'contract': contract}})['Q1'])
        message = 'Contrat de comparaison non évaluable par le jugement expert'
        with self.assertRaisesRegex(ValueError, message):
            evaluation.evaluate(self.store, self.campaign_id, attempts[0], responsible=RESPONSIBLE,
                                authority=EVALUATION_AUTHORITY, check=findings)
        request = dict(operation_id='jugement', campaign_id=self.campaign_id, attempt_id=attempts[0],
                       review_sha256='a' * 64, previous_evaluation_id=None,
                       authority={'actor': 'Ayo', 'authority_id': 'revue-locale'}, budget_id='qualification',
                       reserve_amount='1', requested_configuration={})
        with self.assertRaisesRegex(ValueError, message):
            judgment.reserve(self.store, request, None)
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s5_evaluations').fetchone()[0])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def body(self):
        snapshot = c.inspect(self.store, self.campaign_id)
        return {'manifest_version': snapshot['manifest']['version'],
                'frozen_at': snapshot['manifest']['conditions']['frozen_at'],
                'confirm': 'yes'}

    def test_recapitulatif_plafond_admission_et_gel(self):
        self.connect()
        summary = c.launch_view(self.store, self.sid, 'fixture', self.campaign_id,
                                access_secret=SECRET, access_transport=self.access)
        self.assertEqual(['example_validated', 'example_qualified',
                          'configurations_available', 'access_connected',
                          'estimate_under_cap'], [check['key'] for check in summary['checks']])
        self.assertTrue(summary['launchable'])
        self.assertEqual({'limit_remaining_usd': '18.5', 'limit_usd': '20'},
                         summary['checks'][3]['detail'])
        total = c._estimate_total(c.inspect(self.store, self.campaign_id))
        self.assertEqual('Estimation totale : ' + format(total, 'f').replace('.', ',') +
                         ' USD pour un plafond de 50,00 USD', summary['checks'][4]['detail'])
        for invalid in ('0.09', '100.01', '1.001', '1e1', 1):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    ValueError, 'Plafond hors bornes : 0,10 à 100 USD'):
                c.set_cap(self.store, self.sid, 'fixture', self.campaign_id,
                          {'cap_usd': invalid})
        changed = c.set_cap(self.store, self.sid, 'fixture', self.campaign_id,
                            {'cap_usd': '100'})
        self.assertEqual(('100.00', 'requester'),
                         (changed['cap_usd'], changed['cap_source']))
        attempts = c.launch(self.store, self.sid, 'fixture', self.campaign_id,
                            self.body(), access_secret=SECRET,
                            access_transport=self.access)
        snapshot = c.inspect(self.store, self.campaign_id)
        self.assertEqual(2, len(attempts))
        self.assertEqual('requester:' + self.sid,
                         snapshot['admission']['authority']['authority_id'])
        self.assertEqual(('100.00', 'USD'),
                         (snapshot['budget']['limit'], snapshot['budget']['currency']))
        with self.assertRaisesRegex(storage.ConflictError, 'Plafond figé au lancement'):
            c.set_cap(self.store, self.sid, 'fixture', self.campaign_id,
                      {'cap_usd': '10'})

    def test_estimation_affichee_exacte_pres_de_la_limite(self):
        snapshot = c.inspect(self.store, self.campaign_id)
        for amount, allowed in (('24.99999', True), ('25.00000', True), ('25.00001', False)):
            with self.subTest(amount=amount):
                snapshot['manifest']['panel'][0]['estimate']['amount_usd'] = amount
                snapshot['manifest']['panel'][1]['estimate']['amount_usd'] = '25.00000'
                with patch.object(c, '_requester_campaigns', return_value=[snapshot]):
                    selection = c.configurations_view(self.store, self.sid, 'fixture')
                checks = c._requester_checks(self.store, self.store._connection, snapshot,
                                            self.sid, {'status': 'connected'})
                budget = next(check for check in checks if check['key'] == 'estimate_under_cap')
                total = format(Decimal(amount) + Decimal('25.00000'), 'f')
                self.assertEqual(allowed, selection['estimate_under_cap'])
                self.assertEqual(allowed, budget['ok'])
                self.assertEqual(total, selection['estimate_total_usd'])
                self.assertIn(total + ' USD', views.render(selection, 'csrf').decode())
                self.assertEqual('Estimation totale : ' + total.replace('.', ',') +
                                 ' USD pour un plafond de 50,00 USD', budget['detail'])

    def test_cles_controles_alignees_entre_moteur_erreurs_et_liens(self):
        summary = c.launch_view(self.store, self.sid, 'fixture', self.campaign_id)
        keys = [check['key'] for check in summary['checks']]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), p._CHECK_CODES)
        tree = ast.parse(Path(views.__file__).read_text())
        links = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'links'
                         for target in node.targets) and isinstance(node.value, ast.Dict)]
        self.assertEqual(1, len(links))
        self.assertEqual(set(keys), {ast.literal_eval(key) for key in links[0].keys})

    def test_montants_absents_non_estimables_sur_les_pages(self):
        snapshot = c.inspect(self.store, self.campaign_id)
        snapshot['manifest']['panel'][0]['estimate']['amount_usd'] = None
        with patch.object(c, '_requester_campaigns', return_value=[snapshot]):
            selection = c.configurations_view(self.store, self.sid, 'fixture')
        page = views.render(selection, 'csrf').decode()
        self.assertIn('estimation non estimable', page)
        self.assertIn('Estimation totale : non estimable', page)
        checks = c._requester_checks(self.store, self.store._connection, snapshot, self.sid, {})
        self.assertEqual('Estimation totale : non estimable', checks[-1]['detail'])
        preview = p.view(self.store, self.sid, 'fixture')
        preview['indicative_cost'] = None
        self.assertIn('Estimation indicative de cette préparation : non estimable',
                      views.render(preview, 'csrf').decode())

    def test_chaque_controle_bloque_avec_sa_cle(self):
        with self.assertRaises(p.Denied) as disconnected:
            c.launch(self.store, self.sid, 'fixture', self.campaign_id,
                     self.body(), access_secret=SECRET,
                     access_transport=self.access)
        self.assertEqual('access_connected', disconnected.exception.code)
        self.connect()
        keys = ['example_validated', 'example_qualified', 'configurations_available',
                'access_connected', 'estimate_under_cap']
        for key in keys:
            checks = [{'key': current, 'ok': current != key, 'detail': current}
                      for current in keys]
            with self.subTest(key=key), patch.object(c, '_requester_checks',
                                                    return_value=checks), \
                    self.assertRaises(p.Denied) as caught:
                c.launch(self.store, self.sid, 'fixture', self.campaign_id,
                         self.body(), access_secret=SECRET,
                         access_transport=self.access)
            self.assertEqual(key, caught.exception.code)

    def test_manifeste_sans_deny_est_refuse_a_l_admission(self):
        manifest = deepcopy(c.inspect(self.store, self.campaign_id)['manifest'])
        self.assertTrue(all(config['parameters']['provider']['data_collection'] == 'deny'
                            for config in manifest['panel']))
        for value in (None, 'allow'):
            denied = deepcopy(manifest)
            denied['campaign_id'] = 'sans-deny-' + str(value)
            for config in denied['panel']:
                if value is None:
                    del config['parameters']['provider']['data_collection']
                else:
                    config['parameters']['provider']['data_collection'] = value
            with self.subTest(value=value), \
                    self.assertRaisesRegex(ValueError, 'DATA_COLLECTION_REQUIRED'):
                c.create(self.store, denied)

    def test_required_observations_refuse_data_collection(self):
        manifest = deepcopy(c.inspect(self.store, self.campaign_id)['manifest'])
        manifest['campaign_id'] = 'observations-invalides'
        manifest['panel'][0]['required_observations'].append('data_collection')
        with self.assertRaisesRegex(ValueError, 'required_observations invalide'):
            c.create(self.store, manifest)

    def test_manifeste_historique_reste_lisible_mais_inadmissible(self):
        manifest = deepcopy(c.inspect(self.store, self.campaign_id)['manifest'])
        manifest['campaign_id'] = 'campagne-historique-sans-deny'
        for config in manifest['panel']:
            del config['parameters']['provider']['data_collection']
        connection = self.store._connection
        with storage._transaction(connection, write=True):
            connection.execute('INSERT INTO s4_campaigns VALUES (?,?,?,?)', (
                manifest['campaign_id'], manifest['contract_sha256'], c.encode(manifest),
                q.digest(manifest)))
            for cell in manifest['plan']:
                connection.execute('INSERT INTO s4_cells VALUES (?,?,?,?)', (
                    manifest['campaign_id'], cell['cell_id'], cell['case_id'],
                    cell['configuration_id']))
            connection.execute('INSERT INTO s4_status VALUES (?,NULL,NULL,NULL)',
                               (manifest['campaign_id'],))
            connection.execute('INSERT INTO s4_caps VALUES (?,?,?)',
                               (manifest['campaign_id'], str(c.DEFAULT_CAP_USD), 'default'))
        snapshot = c.inspect(self.store, manifest['campaign_id'])
        self.assertEqual('PREPARED', snapshot['state'])
        with self.assertRaisesRegex(ValueError, 'DATA_COLLECTION_REQUIRED'):
            c.admit(self.store, manifest['campaign_id'], {}, {})
        with self.assertRaisesRegex(ValueError, 'DATA_COLLECTION_REQUIRED'):
            c.reserve(self.store, manifest['campaign_id'], manifest['plan'][0]['cell_id'],
                      'tentative-historique-refusee')

    def test_ordre_des_etapes(self):
        with tempfile.TemporaryDirectory(prefix='requester-step-') as directory:
            data = Path(directory).resolve() / 'private'
            session_id, _, _ = fixture(data)
            q.initialize(data)
            c.initialize(data)
            with closing(storage.Store(data)) as store:
                token = 'token'
                configurations_path = '/preparation/dossiers/fixture/configurations'
                with patch.object(p, 'session',
                                  return_value=(session_id, 'csrf', token)), \
                        patch.object(p, 'require_qualification',
                                     side_effect=p.Denied('NOT_QUALIFIED')), \
                        self.assertRaises(p.Denied) as missing_qualification:
                    p.dispatch(store, 'GET', configurations_path, token, None,
                               'a' * 40, True, candidate_identity={})
                self.assertEqual(
                    ('STEP_INCOMPLETE', 'example_qualified'),
                    (missing_qualification.exception.code,
                     missing_qualification.exception.step))

                store._connection.execute('DELETE FROM s2_validations')
                with patch.object(p, 'session',
                                  return_value=(session_id, 'csrf', token)), \
                        self.assertRaises(p.Denied) as missing_validation:
                    p.dispatch(store, 'GET', configurations_path, token, None,
                               'a' * 40, True, candidate_identity={})
                self.assertEqual(
                    ('STEP_INCOMPLETE', 'example_validated'),
                    (missing_validation.exception.code,
                     missing_validation.exception.step))

                routes = (
                    ('GET', 'conditions', None),
                    ('POST', 'cap', {'csrf_token': 'csrf', 'cap_usd': '10'}),
                    ('POST', 'start', {'csrf_token': 'csrf', 'confirm': 'yes'}),
                )
                for method, action, body in routes:
                    path = ('/preparation/dossiers/fixture/campaigns/'
                            'fixture-c1/' + action)
                    with self.subTest(action=action, campaign='attendue'), \
                            patch.object(p, 'session',
                                         return_value=(session_id, 'csrf', token)), \
                            self.assertRaises(p.Denied) as missing_configurations:
                        p.dispatch(store, method, path, token, body, 'a' * 40, True,
                                   candidate_transport=response)
                    self.assertEqual(
                        ('STEP_INCOMPLETE', 'configurations'),
                        (missing_configurations.exception.code,
                         missing_configurations.exception.step))
                for method, action, body in routes:
                    path = ('/preparation/dossiers/fixture/campaigns/'
                            'fixture-x1/' + action)
                    with self.subTest(action=action, campaign='hors-forme'), \
                            patch.object(p, 'session',
                                         return_value=(session_id, 'csrf', token)), \
                            self.assertRaises(p.Denied) as unknown_campaign:
                        p.dispatch(store, method, path, token, body, 'a' * 40, True,
                                   candidate_transport=response)
                    self.assertEqual(
                        ('Ressource inaccessible', None),
                        (str(unknown_campaign.exception),
                         unknown_campaign.exception.step))

    def test_gel_date_refuse_les_conditions_perimees(self):
        self.connect()
        body = self.body()
        stale = (
            dict(body, frozen_at='2026-09-14T00:00:00+00:00'),
            dict(body, manifest_version=body['manifest_version'] + 1),
        )
        for invalid in stale:
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    storage.ConflictError, 'Conditions périmées'):
                c.launch(self.store, self.sid, 'fixture', self.campaign_id,
                         invalid, access_secret=SECRET,
                         access_transport=self.access)
            self.assertEqual([], c.inspect(self.store, self.campaign_id)['attempts'])

    def test_arret_au_plafond_et_incident_fournisseur_distinct(self):
        self.connect()
        c.set_cap(self.store, self.sid, 'fixture', self.campaign_id, {'cap_usd': '0.10'})
        attempts = c.launch(self.store, self.sid, 'fixture', self.campaign_id,
                            self.body(), access_secret=SECRET,
                            access_transport=self.access)
        calls = []

        def transport(operation, request):
            calls.append(operation['operation_id'])
            result = response(operation, request)
            result['receipt']['result']['incident'] = 'CONTENT_REFUSAL'
            result['cost'].update(amount='0.06', currency='USD')
            return result

        c.execute_launch(self.data, attempts, transport, access_secret=SECRET,
                         access_transport=self.access)
        snapshot = c.inspect(self.store, self.campaign_id)
        self.assertEqual(2, len(calls))
        self.assertEqual('CAP_REACHED', snapshot['stop_reason'])
        self.assertIsNone(snapshot['admission'])

        second = c.prepare_configurations(
            self.store, self.sid, 'fixture',
            {'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'],
             'tier': 'standard'},
            {'package': '@earendil-works/pi-coding-agent', 'version': '0.85.1',
             'sha256': '1' * 64, 'bridge_sha256': '2' * 64,
             'node_version': 'v24.0.0', 'node_sha256': '3' * 64,
             'scope': 'Fixture Pi locale'})
        second_id = second['current_campaign_id']
        c.set_cap(self.store, self.sid, 'fixture', second_id, {'cap_usd': '0.10'})
        second_attempts = c.launch(self.store, self.sid, 'fixture', second_id,
                                   self.body_for(second_id), access_secret=SECRET,
                                   access_transport=self.access)

        def limited(operation, request):
            result = transport(operation, request)
            result['receipt']['result'].update(incident='OPENROUTER_LIMIT',
                                               emission='UNKNOWN', output=None)
            return result

        c.execute_launch(self.data, second_attempts, limited, access_secret=SECRET,
                         access_transport=self.access)
        self.assertEqual('ACQUISITION_EVIDENCE_INCOMPLETE',
                         c.inspect(self.store, second_id)['stop_reason'])

    def body_for(self, campaign_id):
        snapshot = c.inspect(self.store, campaign_id)
        return {'manifest_version': snapshot['manifest']['version'],
                'frozen_at': snapshot['manifest']['conditions']['frozen_at'],
                'confirm': 'yes'}


if __name__ == '__main__':
    unittest.main()

class BackendReadiness(CampaignLaunch):
    def test_retained_cost_allows_next_reservation_without_releasing_money(self):
        m = dict(self.snapshot['manifest'], campaign_id='new-policy', financial_cost_policy='retain_reserve')
        c.create(self.store, m)
        snap = c.inspect(self.store, 'new-policy')
        c.admit(self.store, 'new-policy', *inputs(snap, budget='local-comparison'))
        c.reserve(self.store, 'new-policy', 'x', 'new-x')
        def unknown(op, request):
            result = response(op, request)
            result['cost'].update(status='UNKNOWN', amount=None)
            return result
        c.execute(self.data, 'new-x', unknown)
        self.assertIsNotNone(c.inspect(self.store, 'new-policy')['admission'])
        self.assertEqual('7', self.store.inspect_budget('local-comparison')['reserved'])
        c.reserve(self.store, 'new-policy', 'y', 'new-y')
        c.execute(self.data, 'new-y', response)
        budget = self.store.inspect_budget('local-comparison')
        self.assertEqual('7', budget['reserved'])
        self.assertEqual(['new-x'], budget['unknown_cost_operations'])
        self.assertEqual('RECEIVED', c.inspect(self.store, 'new-policy')['attempts'][-1]['state'])
        with self.assertRaises(storage.BudgetError):
            self.admit()

    def test_retained_cost_does_not_allow_ambiguous_emission(self):
        m = dict(self.snapshot['manifest'], campaign_id='new-policy', financial_cost_policy='retain_reserve')
        c.create(self.store, m)
        snap = c.inspect(self.store, 'new-policy')
        c.admit(self.store, 'new-policy', *inputs(snap, budget='local-comparison'))
        c.reserve(self.store, 'new-policy', 'x', 'new-x')
        def broken(op, request):
            raise OSError('fixture interruption')
        c.execute(self.data, 'new-x', broken)
        self.assertIsNone(c.inspect(self.store, 'new-policy')['admission'])
        with self.assertRaises(ValueError):
            c.reserve(self.store, 'new-policy', 'y', 'new-y')

    def test_runtime_loads_private_factory_without_emission(self):
        from unittest.mock import patch
        from benchmark import runtime
        with patch('benchmark.service.serve_executor') as server, \
             patch('benchmark.service.release_identity', return_value='a'*40), \
             patch('benchmark.pi_openrouter.identity'), \
             patch('benchmark.pi_openrouter.PiOpenRouter') as bridge, \
             patch.dict('os.environ', {'OPENROUTER_API_KEY':'fixture-not-a-real-key'}):
            result = runtime.main(['executor','--data',str(self.data),'--socket',str(self.data/'sock'),
                                   '--candidate-pi','--pi-package','/fixture/pi','--node','/fixture/node'])
            self.assertEqual(0, result)
            factory = server.call_args.kwargs['candidate_transport_factory']
            factory(); factory()
            self.assertEqual(3, bridge.call_count)
            bridge.return_value.assert_not_called()
