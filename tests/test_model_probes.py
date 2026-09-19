"""Vérifications de slugs : aucun réseau ni appel candidat dans ces tests"""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

from benchmark import evaluation, model_probes, preparation, provider_access, service, storage
from benchmark.acquisition import campaigns
from tests import test_configurations as configurations_fixture
from tests.test_configurations import model
from tests.test_provider_access import SECRET, KEY, AccessTransport


SLUG = 'outside/new-generalist'
SOCKET_CONNECT = socket.socket.connect


class ModelProbeTests(unittest.TestCase):
    def setUp(self):
        configurations_fixture.ConfigurationsTests.setUp(self)
        evaluation.initialize(self.data)
        provider_access.initialize(self.data)
        self.access = AccessTransport()
        provider_access.import_key(self.store, self.session, SECRET, KEY, self.access)
        self.summary, self.endpoints = model(SLUG, 'outside', ['low', 'high'])
        self.summary['canonical_slug'] = SLUG + '-20260917'
        self.endpoints['endpoints'][0].update(
            pricing=self.summary['pricing'], context_length=64000,
            max_completion_tokens=8192, supported_parameters=['max_tokens', 'reasoning'])
        self.fetches, self.calls = [], []

    def fetch(self, path):
        self.fetches.append(path)
        if path == '/api/v1/model/' + SLUG:
            return {'data': deepcopy(self.summary)}
        if path == '/api/v1/models/' + SLUG + '/endpoints':
            return {'data': deepcopy(self.endpoints)}
        raise ValueError('unknown model')

    def submit(self, slug=SLUG, action='first'):
        return model_probes.submit(self.store, self.session, 'fixture',
            {'slug': slug, 'action_id': action}, self.fetch, SECRET, self.access)

    def post(self, key, wire, **kwargs):
        self.calls.append((key, json.loads(wire)))
        return (200, {}, json.dumps(self.response).encode(), True, '2026-09-17T20:00:00Z', 0)

    def respond(self, operation_id, key, **changes):
        self.response = {'id': 'gen-fixture', 'model': self.summary['canonical_slug'],
            'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'OK'}}],
            'usage': {'cost': 0.0004}}
        self.response.update(changes)
        model_probes.execute(self.data, operation_id, key, self.post)

    def test_slug_exact_test_unique_persistant_et_utilisable_dans_un_panel(self):
        operation_id, key = self.submit()
        self.assertEqual(KEY, key)
        operation = next(o for o in self.store.inspect_operations() if o['operation_id'] == operation_id)
        self.assertEqual('EMISSION_POSSIBLE', operation['state'])
        self.assertEqual(provider_access.preparation_budget_id(self.session), operation['budget_id'])
        self.assertEqual((operation_id, None), self.submit())
        self.respond(operation_id, key)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(SLUG, self.calls[0][1]['model'])
        self.assertFalse(self.calls[0][1]['provider']['allow_fallbacks'])
        self.assertNotIn('fixture', self.calls[0][1]['messages'][0]['content'])
        view = model_probes.view(self.store, self.session, 'fixture')
        self.assertEqual('RESPONDED', view[0]['status'])
        self.assertEqual('0.0004', view[0]['cost']['amount'])
        self.assertNotIn(KEY, storage._strict_json(view))
        self.assertEqual((operation_id, None), self.submit(action='double-click'))
        prepared = campaigns.prepare_configurations(self.store, self.session, 'fixture',
            {'models': [SLUG, 'openai/gpt-5.6-sol'], 'tier': 'high'}, self.identity)
        custom = prepared['configurations'][0]
        self.assertEqual(SLUG, custom['model'])
        self.assertEqual(operation_id, custom['estimate']['probe_operation_id'])
        self.assertEqual('high', custom['parameters']['reasoning']['effort'])
        self.assertEqual(1, len(self.calls))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_formulaire_slug_et_resultat_prives_sans_appel_candidat(self):
        from benchmark_web.views import render, page_script
        value = campaigns.configurations_view(self.store, self.session, 'fixture')
        value['personal_preparation'] = True
        page = render(value, 'csrf-test').decode()
        self.assertIn('Slug Openrouter', page)
        self.assertIn('Tester et ajouter', page)
        self.assertIn('Ajouter une ligne', page)
        self.assertIn('appel payant', page)
        self.assertIn('/custom-models', page)
        self.assertIn(page_script(value), page)
        from tests.test_parcours_complet import Page
        document = Page(page.encode())
        disclosure = next(n for n in document.nodes if n['attrs'].get('id') == 'custom-models')
        self.assertEqual('details', disclosure['tag'])
        self.assertNotIn('open', disclosure['attrs'])
        self.assertIn('Ajouter un slug Openrouter', disclosure['text'])
        self.assertLess(page.index('Modèles à comparer'), page.index('id="custom-models"'))
        self.assertLess(page.index('id="custom-models"'), page.index('Niveau de raisonnement demandé'))
        selection = document.form('/configurations')
        self.assertEqual('csrf-test', selection['fields']['csrf_token'])
        self.assertFalse(any(n['tag'] == 'form' for n in selection['nodes']))
        self.assertTrue(any(n['attrs'].get('name') == 'models' for n in selection['nodes']))
        tiers = [n for n in document.nodes if n['attrs'].get('name') == 'tier']
        self.assertTrue(tiers)
        self.assertTrue(all(n['attrs'].get('form') == 'configurations-form' for n in tiers))
        save = next(n for n in document.nodes if n['tag'] == 'button' and 'Enregistrer les configurations' in n['text'])
        self.assertEqual('configurations-form', save['attrs'].get('form'))

    def test_refus_avant_depense_et_isolation(self):
        for invalid in ('GPT 6', 'https://openrouter.ai/openai/gpt-6-astra', '../x', 'openrouter/auto'):
            with self.subTest(slug=invalid), self.assertRaises(preparation.Denied):
                self.submit(slug=invalid)
        self.assertEqual([], self.fetches)
        with self.assertRaises(preparation.Denied):
            model_probes.submit(self.store, 'another-session', 'fixture',
                {'slug': SLUG, 'action_id': 'other'}, self.fetch, SECRET, self.access)
        self.endpoints['id'] = 'outside/substituted'
        with self.assertRaises(preparation.Denied):
            self.submit()
        self.assertFalse(any(o['engine_version'] == model_probes.ENGINE
                             for o in self.store.inspect_operations()))

    def test_reponse_vide_refus_troncature_et_autre_modele_ne_sont_pas_un_succes(self):
        for index, changes in enumerate((
                {'choices': [{'finish_reason': 'stop', 'message': {'content': ''}}]},
                {'choices': [{'finish_reason': 'stop', 'message': {'content': 'No', 'refusal': 'No'}}]},
                {'choices': [{'finish_reason': 'length', 'message': {'content': 'OK'}}]},
                {'choices': [{'finish_reason': 'stop', 'message': {'content': 'I cannot comply.'}}]},
                {'model': 'outside/other'})):
            operation_id, key = self.submit(action='failure-' + str(index))
            self.respond(operation_id, key, **changes)
            self.assertEqual('UNCONFIRMED', model_probes.view(self.store, self.session, 'fixture')[0]['status'])
            self.assertNotIn(SLUG, {m['id'] for m in model_probes.selection(
                self.store, self.session, 'fixture')['models']})
        self.assertEqual(5, len(self.calls))

    def test_ancien_budget_ignore_mais_admission_requise(self):
        budget_id = provider_access.preparation_budget_id(self.session)
        self.store._connection.execute('UPDATE budgets SET limit_amount=? WHERE budget_id=?', ('0', budget_id))
        operation_id, key = self.submit()
        self.respond(operation_id, key)
        self.assertTrue(any(o['engine_version'] == model_probes.ENGINE for o in self.store.inspect_operations()))
        preparation.close_admission(self.store)
        with self.assertRaises(preparation.Denied):
            self.submit(slug='outside/another', action='closed')

    def test_interruption_sans_retry_et_cout_inconnu_conserve_sa_reserve(self):
        operation_id, key = self.submit()
        with patch.object(model_probes, 'post', side_effect=TimeoutError):
            model_probes.execute(self.data, operation_id, key)
        self.assertEqual('AMBIGUOUS', model_probes.view(self.store, self.session, 'fixture')[0]['status'])
        self.assertEqual((operation_id, None), self.submit())
        with self.assertRaises(preparation.Denied):
            self.submit(action='retry')

    def test_fermeture_avant_le_worker_ne_part_pas_chez_le_fournisseur(self):
        operation_id, key = self.submit()
        preparation.close_admission(self.store)
        with patch.object(model_probes, 'post') as network:
            model_probes.execute(self.data, operation_id, key)
        network.assert_not_called()
        record = model_probes.view(self.store, self.session, 'fixture')[0]
        self.assertEqual('NOT_SENT', record['status'])
        self.assertEqual('0', record['cost']['amount'])

    def test_choisit_un_endpoint_compatible_avant_tout_appel(self):
        incompatible = deepcopy(self.endpoints['endpoints'][0])
        incompatible.update(tag='aaa', supported_parameters=['max_completion_tokens'])
        self.endpoints['endpoints'].insert(0, incompatible)
        operation_id, key = self.submit()
        self.respond(operation_id, key)
        self.assertEqual(['outside'], self.calls[0][1]['provider']['only'])

    def test_sante_et_double_clic_pendant_les_metadonnees_lentes(self):
        token = 'ab' * 32
        self.store._connection.execute('UPDATE s2_sessions SET token_sha256=? WHERE session_id=?',
            (sha256(bytes.fromhex(token)).hexdigest(), self.session))
        _, csrf, _ = preparation.session(self.store, token)
        authority = preparation.admission(self.store)
        ready, fetching, release = threading.Event(), threading.Event(), threading.Event()
        servers = []
        sock = self.data.parent / 'probe.sock'

        def run(server):
            servers.append(server)
            ready.set()
            server.serve_forever(poll_interval=.01)

        def fetch(path):
            if path.startswith('/api/v1/model/'):
                fetching.set()
                if not release.wait(5):
                    raise TimeoutError('fixture blocked')
            return self.fetch(path)

        self.response = {'id': 'gen-fixture', 'model': SLUG, 'usage': {'cost': 0.0004},
            'choices': [{'finish_reason': 'stop', 'message': {'content': 'OK'}}]}
        with patch('socket.socket.connect', SOCKET_CONNECT), patch.object(service, 'run', run), \
                patch.object(service, '_refresh_catalogue'):
            worker = threading.Thread(target=service.serve_executor, args=(self.data, sock, 'a' * 40),
                kwargs=dict(candidate_identity=self.identity, personal_preparation=True,
                    access_secret=SECRET, access_transport=self.access,
                    catalogue_fetch=fetch, model_probe_transport=self.post))
            worker.start()
            try:
                self.assertTrue(ready.wait(5))
                preparation.admit(self.store, authority)
                path = '/preparation/dossiers/fixture/custom-models'
                body = {'slug': SLUG, 'action_id': 'browser-action', 'csrf_token': csrf}
                first = service.preparation_request(sock, 'POST', path, token, body)
                self.assertEqual(202, first['status'])
                self.assertTrue(fetching.wait(2))
                self.assertEqual('ok', service.executor_health(sock)['storage'])
                self.assertEqual([], self.calls)
                second = service.preparation_request(sock, 'POST', path, token, body)
                self.assertEqual(first['value']['probe_operation_id'], second['value']['probe_operation_id'])
                collision = service.preparation_request(sock, 'POST', path, token, {**body, 'slug': 'outside/other'})
                self.assertEqual(409, collision['status'])
                bad = service.preparation_request(sock, 'POST', path, token, {**body, 'csrf_token': 'wrong'})
                self.assertEqual(403, bad['status'])
                release.set()
                deadline = time.monotonic() + 5
                while True:
                    current = service.preparation_request(sock, 'GET', path, token, None)
                    if current['value']['custom_models'] and current['value']['custom_models'][0]['status'] == 'RESPONDED':
                        break
                    self.assertLess(time.monotonic(), deadline, current)
                    time.sleep(.01)
                self.assertEqual(1, len(self.calls))
                self.assertEqual(1, len([m for m in current['value']['models'] if m['id'] == SLUG]))
                self.assertNotIn(KEY, storage._strict_json(current))
            finally:
                release.set()
                if servers:
                    servers[0].shutdown()
                worker.join(5)
                self.assertFalse(worker.is_alive())

    def test_reponse_ne_conserve_pas_une_cle_echappee_en_json(self):
        operation_id, key = self.submit()
        raw = json.dumps({'model': SLUG, 'choices': [{'finish_reason': 'stop',
            'message': {'content': KEY}}]}).replace('sk-or', '\\u0073k-or').encode()
        transport = lambda *a, **kw: (200, {}, raw, True, '2026-09-17T20:00:00Z', 0)
        model_probes.execute(self.data, operation_id, key, transport)
        record = next(o for o in self.store.inspect_operations() if o['operation_id'] == operation_id)
        self.assertNotIn(KEY, storage._strict_json(record))
        self.assertEqual('UNCONFIRMED', record['receipt']['result']['status'])

    def test_cout_inconnu_et_changement_de_cle_ne_recreent_pas_de_budget(self):
        operation_id, key = self.submit()
        self.respond(operation_id, key, usage={})
        budget_id = provider_access.preparation_budget_id(self.session)
        before = self.store.inspect_budget(budget_id)
        self.assertNotEqual('20', before['available'])
        provider_access.import_key(self.store, self.session, SECRET, KEY + '-replaced', self.access)
        self.assertEqual(before, self.store.inspect_budget(budget_id))
        self.assertEqual('EXPIRED', model_probes.view(self.store, self.session, 'fixture')[0]['status'])
        self.assertNotIn(SLUG, {m['id'] for m in model_probes.selection(self.store, self.session, 'fixture')['models']})

    def test_le_lancement_refuse_une_verification_personnelle_expiree(self):
        operation_id, key = self.submit()
        self.respond(operation_id, key)
        prepared = campaigns.prepare_configurations(self.store, self.session, 'fixture',
            {'models': [SLUG, 'openai/gpt-5.6-sol'], 'tier': 'low'}, self.identity)
        campaign = campaigns.inspect(self.store, prepared['current_campaign_id'])
        checked_at = prepared['configurations'][0]['estimate']['fetched_at']
        with patch.object(model_probes.model_catalogue, '_now',
                          return_value=datetime.fromisoformat(checked_at) + timedelta(hours=25)):
            checks = campaigns._requester_checks(self.store, self.store._connection, campaign, self.session, {})
            self.assertFalse(next(c['ok'] for c in checks if c['key'] == 'configurations_available'))
        self.assertEqual(1, len(self.calls))


if __name__ == '__main__':
    unittest.main()
