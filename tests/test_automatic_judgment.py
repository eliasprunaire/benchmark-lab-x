"""Automatic private verdicts on retained requester responses; no real network"""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from benchmark import evaluation, judgment, preparation, provider_access, restitution, service, storage, web_api
from benchmark.acquisition import campaigns, execution
from benchmark.transports import openrouter
from benchmark_web import views
from tests import test_campaign_launch as campaign_fixture
from tests.test_openrouter_preparation import SYNTHETIC_PROFILE, estimate_for
from tests.test_provider_access import KEY, SECRET
from tests.test_s4_regressions import response
from tests.hermetique.sitecustomize import garder_module as setUpModule  # noqa: F401  réseau local seul, blocage borné


class AutomaticJudgment(unittest.TestCase):
    def setUp(self):
        self.fixture = campaign_fixture.RequesterCampaignLaunch()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.connect()
        f = self.fixture
        self.store, self.data, self.sid, self.cid = f.store, f.data, f.sid, f.campaign_id
        # La clé enregistrée ouvre elle-même l'enveloppe personnelle, à son plafond de 20 USD
        self.budget = provider_access.preparation_budget_id(self.sid)
        profile = openrouter.load_profile(str(SYNTHETIC_PROFILE))
        self.transport = openrouter.OpenRouterJudgment(None, profile).for_session(
            provider_access.key_for_session(self.store, self.sid, SECRET, f.access), self.sid, SECRET)
        self.transport._quote = openrouter.configuration(estimate_for(profile), profile)
        self.http = Mock()
        self.http.getresponse.return_value.status = 200
        self.http.getresponse.return_value.length = 0
        self.http.getresponse.return_value.getheader.return_value = None
        self.enterContext(patch.object(openrouter, 'HTTPSConnection', return_value=self.http))
        # Une clé refusée (401/402) est revérifiée : par le transport factice, jamais chez OpenRouter
        self.enterContext(patch.object(provider_access, 'OpenRouterAccess', return_value=f.access))
        self.http.request.side_effect = self.answer
        self.response_update = lambda document: None
        self.candidate_update = lambda op, value: None

    def acquire(self):
        f = self.fixture
        ids = campaigns.launch(self.store, self.sid, 'fixture', self.cid, f.body(),
                               access_secret=SECRET, access_transport=f.access)
        def received(op, request):
            value = response(op, request)
            value['cost'].update(currency='USD', amount='0.001')
            self.candidate_update(op, value)
            return value
        execution.execute_launch(self.data, ids, received, access_secret=SECRET, access_transport=f.access)
        return ids

    def answer(self, method, path, *, body, headers):
        content = json.loads(json.loads(body)['messages'][1]['content'])
        output = content['output']
        proof = {k: output[k] for k in ('piece_id', 'sha256')}
        proof['passage'] = output['content']
        findings = [dict(criterion_id=x['id'], control_id=k, status='FAIL', attribution='candidate',
                         finding='Obligation non satisfaite', evidence=[proof])
                    for x in content['obligations'] + content['eliminatory_errors'] for k in x['control_ids']]
        result = dict(findings=findings, measures=[], limits=[], proposed_verdict='SATISFAIT')
        profile = self.transport._profile
        document = dict(id='fixture-judge', model=profile['revision'],
            choices=[dict(finish_reason='stop', message=dict(role='assistant', content=storage._strict_json(result)))],
            usage=dict(cost='0.001'), openrouter_metadata=dict(requested=profile['model'],
            endpoints=dict(available=[dict(provider=profile['routes'][0]['provider_name'], selected=True)])))
        self.response_update(document)
        self.http.getresponse.return_value.read.return_value = storage._strict_json(document).encode()

    def test_received_responses_become_private_verdicts_without_operator_submission(self):
        from benchmark import automatic_judgment as auto
        self.acquire()
        # A legitimate later deployment closed candidate admission; never reopen it
        campaigns.stop(self.store, self.cid)
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual(2, self.http.request.call_count)

    def test_public_contract_keeps_one_control_per_criterion_for_recorded_judgments(self):
        # Le contexte adapté est revérifié octet pour octet à chaque relecture d'un jugement enregistré :
        # changer l'adaptation d'un contrat existant rendrait ses verdicts illisibles
        from benchmark import automatic_judgment as auto
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        auto.execute_campaign(self.data, ids, self.transport)
        for call in self.http.request.call_args_list:
            content = json.loads(json.loads(call.kwargs['body'])['messages'][1]['content'])
            criteria = content['obligations'] + content['eliminatory_errors']
            self.assertEqual([[x['id']] for x in criteria], [x['control_ids'] for x in criteria])
            self.assertEqual(auto.FORMAT, content['method']['id'])
        with self.store.read_snapshot() as connection:
            self.assertEqual(2, len(auto.records(self.store, connection, self.cid)))

    def dispatch(self, method, suffix, body=None):
        with patch.object(preparation, 'session', return_value=(self.sid, 'csrf', 'token')):
            return web_api.dispatch(self.store, method,
                '/preparation/dossiers/fixture/campaigns/' + self.cid + suffix, 'token', body, 'a' * 40, True,
                candidate_transport=response, judgment_transport=self.transport,
                access_secret=SECRET, access_transport=self.fixture.access)

    def test_economic_help_uses_complete_results_despite_display_filters(self):
        from benchmark import automatic_judgment as auto
        amounts = iter(['0.2', '0.01'])
        self.candidate_update = lambda op, value: value['cost'].update(amount=next(amounts))

        def satisfied(document):
            answer = json.loads(document['choices'][0]['message']['content'])
            for finding in answer['findings']:
                finding['status'] = 'PASS'
            answer['measures'] = [dict(criterion_id='Q1', value='excellent', unit='descriptif',
                                       evidence=answer['findings'][0]['evidence'])]
            document['choices'][0]['message']['content'] = storage._strict_json(answer)

        self.response_update = satisfied
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        auto.execute_campaign(self.data, ids, self.transport)
        before = deepcopy(self.store.inspect_operations())
        value = restitution.comparison(self.store, self.sid, 'fixture', self.cid)
        choice = value['recommendation']
        self.assertEqual('0.01', choice['amount'])
        self.assertEqual(2, choice['count'])
        expensive = next(row for row in value['rows'] if row['cost']['value'] == '0.2')
        filtered = restitution.comparison(self.store, self.sid, 'fixture', self.cid,
                                         query={'configuration': expensive['configuration_id']})
        self.assertEqual([expensive['attempt_id']], [row['attempt_id'] for row in filtered['rows']])
        self.assertEqual('equal_quality_cost', choice['basis'])
        self.assertEqual(choice['configuration'], filtered['recommendation']['configuration'])
        self.assertEqual(choice['amount'], filtered['recommendation']['amount'])
        self.assertEqual(before, self.store.inspect_operations())
        self.assertEqual(2, self.http.request.call_count)

    def test_normal_launch_runs_acquisition_then_judgment_and_get_never_emits(self):
        first = self.dispatch('GET', '/conditions')
        self.assertTrue(first[1]['launchable'])
        self.assertIsNotNone(first[1]['judgment_estimate_usd'])
        self.http.request.assert_not_called()
        start = self.dispatch('POST', '/start', dict(self.fixture.body(), csrf_token='csrf'))[3]
        def received(op, request):
            value = response(op, request)
            value['cost'].update(currency='USD', amount='0.001')
            return value
        service._campaign_worker(self.data, start, received, None, SECRET, self.fixture.access, self.transport)
        self.assertEqual(2, self.http.request.call_count)
        for _ in range(2):
            self.assertEqual('COMPLETE', self.dispatch('GET', '/conditions')[1]['campaign']['judgment']['status'])
            results = self.dispatch('GET', '')[1]
            self.assertEqual(2, len(results['rows']))
            self.assertIn('NE SATISFAIT PAS', views.render(results, 'csrf').decode())
            detail = restitution.detail(self.store, self.sid, 'fixture', self.cid, results['rows'][0]['attempt_id'])
            self.assertIn('Ne satisfait pas', views.render(detail, 'csrf').decode())
        self.assertEqual(2, self.http.request.call_count)

    def test_old_local_budget_does_not_block_judgment_and_is_not_reset(self):
        self.transport._quote['reserve_usd'] = '11'
        self.dispatch('POST', '/start', dict(self.fixture.body(), csrf_token='csrf'))
        self.assertEqual(2, len(campaigns.inspect(self.store, self.cid)['attempts']))
        budget = self.store.inspect_budget(self.budget)
        self.assertEqual('20', budget['limit'])
        self.assertTrue(budget['provider_managed'])
        self.assertIsNone(budget['available'])
        self.http.request.assert_not_called()

    def test_server_binds_citations_and_reports_bad_hash_without_losing_results(self):
        from benchmark import automatic_judgment as auto, judgment
        def citations(document):
            answer = json.loads(document['choices'][0]['message']['content'])
            for index, finding in enumerate(answer['findings']):
                proof = finding['evidence'][0]
                if index == 0:
                    proof['sha256'] = 'bad-copy'
                else:
                    proof.pop('sha256')
            document['choices'][0]['message']['content'] = storage._strict_json(answer)
        self.response_update = citations
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        with self.assertLogs('benchmark.judgment', level='WARNING') as logged:
            auto.execute_campaign(self.data, ids, self.transport)
        self.assertIn('JUDGMENT_EVIDENCE_METADATA_CORRECTED', '\n'.join(logged.output))
        self.assertNotIn('bad-copy', '\n'.join(logged.output))
        self.assertEqual('COMPLETE', auto.status(self.store, self.store._connection, self.cid)['status'])
        rows = restitution.comparison(self.store, self.sid, 'fixture', self.cid)['rows']
        self.assertEqual(['NE SATISFAIT PAS'] * 2, [r['verdict'] for r in rows])
        view = judgment.inspect(self.store, ids[0])
        self.assertTrue(view['proposal']['evidence_binding']['warnings'])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        self.assertEqual(2, self.http.request.call_count)

    def test_old_bad_hash_is_recovered_from_receipt_without_rewriting_it_or_calling(self):
        from benchmark import automatic_judgment as auto, judgment
        def bad_hash(document):
            answer = json.loads(document['choices'][0]['message']['content'])
            answer['findings'][0]['evidence'][0]['sha256'] = '0' * 64
            document['choices'][0]['message']['content'] = storage._strict_json(answer)
        self.response_update = bad_hash
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        original = judgment._proposal
        def old_reader(*args, **kwargs):
            return original(*args, bind_evidence=False)
        with patch.object(judgment, '_proposal', side_effect=old_reader):
            auto.execute_campaign(self.data, ids, self.transport)
        before = deepcopy(self.store.inspect_operations())
        self.assertIsNone(next(o for o in before if o['operation_id'] == ids[0])['receipt']['result'])
        for _ in range(2):
            rows = restitution.comparison(self.store, self.sid, 'fixture', self.cid)['rows']
            self.assertEqual(['NE SATISFAIT PAS'] * 2, [r['verdict'] for r in rows])
            self.assertEqual('COMPLETE', auto.status(self.store, self.store._connection, self.cid)['status'])
            detail = restitution.detail(self.store, self.sid, 'fixture', self.cid, rows[0]['attempt_id'])
            self.assertIn('Ne satisfait pas', views.render(detail, 'csrf').decode())
            other, _, _ = preparation.session(self.store, None, create=True)
            proof = rows[0]['proof_links'][0]
            with self.assertRaises(preparation.Denied):
                evaluation.piece_bytes(self.store, other, 'fixture', ids[0], proof['piece_id'])
        self.assertEqual(before, self.store.inspect_operations())
        records = auto.records(self.store, self.store._connection, self.cid)
        self.assertTrue(records[0]['judgment']['evidence_binding']['recovered_from_receipt'])
        # Le compte léger du suivi suit `records`, proposition récupérée depuis son reçu comprise
        self.assertEqual(len(records), auto.status(self.store, self.store._connection, self.cid)['completed'])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        self.assertEqual(2, self.http.request.call_count)

    def test_malformed_message_stays_a_consultable_incident(self):
        from benchmark import automatic_judgment as auto
        self.response_update = lambda doc: doc['choices'][0].update(message=None)
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual([], restitution.comparison(self.store, self.sid, 'fixture', self.cid)['rows'])
        self.assertEqual('BLOCKED', auto.status(self.store, self.store._connection, self.cid)['status'])
        self.assertEqual(len(auto.records(self.store, self.store._connection, self.cid)),
                         auto.status(self.store, self.store._connection, self.cid)['completed'])

    def test_encoded_reflected_key_is_redacted_even_when_judgment_format_is_invalid(self):
        from benchmark import automatic_judgment as auto
        def reflected(doc):
            answer = json.loads(doc['choices'][0]['message']['content'])
            answer['proposed_verdict'] = 'invalid'
            answer['limits'] = [KEY]
            doc['choices'][0]['message']['content'] = json.dumps(answer).replace(
                KEY, ''.join('\\u%04x' % ord(char) for char in KEY))
        self.response_update = reflected
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        auto.execute_campaign(self.data, ids, self.transport)
        # Un jugement clos avant envoi n'a aucune réponse à masquer
        received = [op for op in self.store.inspect_operations()
                    if op['operation_id'] in ids and op['receipt'] and not storage.not_sent(op)]
        self.assertTrue(received)
        for op in received:
            self.assertTrue(op['receipt']['observed_configuration']['http']['credential_redacted'])

    def test_missing_or_foreign_passages_remain_unusable_despite_hash_binding(self):
        from benchmark import automatic_judgment as auto
        def invalid(document):
            answer = json.loads(document['choices'][0]['message']['content'])
            proof = answer['findings'][0]['evidence'][0]
            proof['sha256'] = 'bad-copy'
            proof['passage'] = 'This passage was never present in the candidate response'
            document['choices'][0]['message']['content'] = storage._strict_json(answer)
        self.response_update = invalid
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual([], restitution.comparison(self.store, self.sid, 'fixture', self.cid)['rows'])
        self.assertEqual('BLOCKED', auto.status(self.store, self.store._connection, self.cid)['status'])

    def test_metadata_recovery_never_accepts_a_foreign_provider(self):
        from benchmark import automatic_judgment as auto
        def invalid(document):
            answer = json.loads(document['choices'][0]['message']['content'])
            answer['findings'][0]['evidence'][0]['sha256'] = '0' * 64
            document['choices'][0]['message']['content'] = storage._strict_json(answer)
            document['openrouter_metadata']['endpoints']['available'][0]['provider'] = 'Foreign provider'
        self.response_update = invalid
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual([], restitution.comparison(self.store, self.sid, 'fixture', self.cid)['rows'])
        self.assertEqual('BLOCKED', auto.status(self.store, self.store._connection, self.cid)['status'])

    def test_metadata_recovery_never_accepts_a_foreign_piece(self):
        from benchmark import automatic_judgment as auto
        def invalid(document):
            answer = json.loads(document['choices'][0]['message']['content'])
            answer['findings'][0]['evidence'][0].update(piece_id='foreign-piece', sha256='0' * 64)
            document['choices'][0]['message']['content'] = storage._strict_json(answer)
        self.response_update = invalid
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual([], restitution.comparison(self.store, self.sid, 'fixture', self.cid)['rows'])
        self.assertEqual('BLOCKED', auto.status(self.store, self.store._connection, self.cid)['status'])

    def test_other_assistance_cannot_consume_judge_envelope_during_acquisition(self):
        from benchmark import automatic_judgment as auto
        start = self.dispatch('POST', '/start', dict(self.fixture.body(), csrf_token='csrf'))[3]
        op = next(o for o in self.store.inspect_operations() if o['operation_id'] in start['candidate_attempts'])
        intent = {k: op[k] for k in storage._OPERATION_KEYS}
        intent.update(operation_id='other-assistance', phase='preparation', engine_version='fixture', resources=[])
        with self.assertRaises(storage.BudgetError):
            self.store.reserve_intent(intent, self.budget, '1')
        with self.assertRaises(storage.BudgetError):
            auto.guard_budget(self.store, self.store._connection, self.budget, campaign_id='another-campaign')
        campaigns.stop(self.store, self.cid)
        auto.guard_budget(self.store, self.store._connection, self.budget)
        self.assertEqual('20', self.store.inspect_budget(self.budget)['limit'])

    def test_missing_candidate_output_ends_with_access_to_partial_results(self):
        from benchmark import automatic_judgment as auto
        calls = []
        def missing(op, value):
            calls.append(op['operation_id'])
            if len(calls) == 2:
                value['receipt']['result'].update(output=None, incident='EMPTY_OUTPUT')
        self.candidate_update = missing
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        auto.execute_campaign(self.data, ids, self.transport)
        progress = auto.status(self.store, self.store._connection, self.cid)
        # Seule la réponse exploitable est à évaluer : l'évaluation est terminée, la couverture reste partielle
        self.assertEqual(('COMPLETE', 1, 1, 2), (progress['status'], progress['completed'], progress['total'], progress['cells']))
        self.assertEqual(len(auto.records(self.store, self.store._connection, self.cid)), progress['completed'])
        # Redémarrage après la fin : l'admission fermée ne transforme pas une évaluation terminée en interruption
        from benchmark import runtime
        runtime.stop(self.data, self.store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        self.assertEqual('COMPLETE', auto.status(self.store, self.store._connection, self.cid)['status'])
        view = campaigns.launch_view(self.store, self.sid, 'fixture', self.cid)
        page = views.render(view, 'csrf').decode()
        self.assertIn('Voir les résultats</a>', page)
        self.assertNotIn('id="preparation-progress"', page)
        self.assertNotIn('Évaluation interrompue', page)
        self.assertIn('1 modèle sur 2 n’a pas donné de réponse exploitable', page)
        value = restitution.comparison(self.store, self.sid, 'fixture', self.cid)
        self.assertEqual(['NO_USABLE_RESPONSE'], [p['state'] for p in value['pending_attempts']])
        # Couverture incomplète (RULES §6) : aucun conseil ni comparaison économique complète
        self.assertIsNone(value['recommendation'])
        self.assertEqual('INCOMPLETE', value['economic_status'])
        results = views.render(value, 'csrf').decode()
        self.assertIn('Aucune réponse exploitable pour deepseek/deepseek-v4.1-flash.', results)
        self.assertNotIn('doit être relue', results)
        self.assertNotIn('les essais se sont arrêtés avant la fin', results)

    def test_empty_candidate_output_is_never_sent_to_the_judge(self):
        from benchmark import automatic_judgment as auto
        calls = []
        def blank(op, value):
            calls.append(op['operation_id'])
            if len(calls) == 2:
                value['receipt']['result']['output'] = ' \n\t '
        self.candidate_update = blank
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        self.assertEqual(1, len(ids))
        auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual(1, self.http.request.call_count)
        progress = auto.status(self.store, self.store._connection, self.cid)
        self.assertEqual(('COMPLETE', 1, 1, 2), (progress['status'], progress['completed'], progress['total'], progress['cells']))

    def test_revoked_key_after_reservation_blocks_and_cannot_retry(self):
        from benchmark import automatic_judgment as auto
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        provider_access.disconnect(self.store, self.sid, SECRET)
        auto.execute_campaign(self.data, ids, self.transport)
        self.http.request.assert_not_called()
        self.assertEqual('BLOCKED', auto.status(self.store, self.store._connection, self.cid)['status'])
        self.assertEqual([], auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport))

    def test_worker_does_not_close_judgments_already_reserved_by_the_same_owner(self):
        from benchmark import automatic_judgment as auto
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        service._campaign_worker(self.data, dict(candidate_attempts=[], judgment_campaign=self.cid,
            session_id=self.sid, dossier_id='fixture'), response, None, SECRET, self.fixture.access, self.transport)
        self.assertIsNotNone(campaigns.inspect(self.store, self.cid)['admission'])
        auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual('COMPLETE', auto.status(self.store, self.store._connection, self.cid)['status'])

    def test_unknown_cost_stops_second_call_and_keeps_first_verdict(self):
        from benchmark import automatic_judgment as auto
        self.acquire()
        self.response_update = lambda doc: doc.update(usage={})
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual(1, self.http.request.call_count)
        self.assertEqual(1, len(restitution.comparison(self.store, self.sid, 'fixture', self.cid)['rows']))
        self.assertEqual('BLOCKED', auto.status(self.store, self.store._connection, self.cid)['status'])

    def test_unusable_judgment_is_not_a_model_failure_or_an_endless_wait(self):
        from benchmark import automatic_judgment as auto
        self.acquire()
        self.response_update = lambda doc: doc['choices'][0].update(finish_reason='length')
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual([], restitution.comparison(self.store, self.sid, 'fixture', self.cid)['rows'])
        self.assertEqual('BLOCKED', auto.status(self.store, self.store._connection, self.cid)['status'])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def judgments(self, ids):
        return {o['operation_id']: o for o in self.store.inspect_operations() if o['operation_id'] in ids}

    def assert_reopened(self, ids, status='BLOCKED'):
        """Jugements clos sans envoi : la session peut préparer une nouvelle comparaison

        `RUNNING` : clôture sans faute de la session (redémarrage, connexion impossible), relancée d'office
        """
        from benchmark import automatic_judgment as auto
        for op in self.judgments(ids).values():
            self.assertEqual(('RECEIVED', {'status': 'NOT_SENT'}, '0'),
                             (op['state'], op['receipt']['result'], op['observed_cost']['amount']))
        progress = auto.status(self.store, self.store._connection, self.cid)
        self.assertEqual((status, 0), (progress['status'], progress['completed']))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        self.assertEqual([], auto.records(self.store, self.store._connection, self.cid))
        self.assertEqual('NOT_SENT', judgment.inspect(self.store, ids[0])['diagnostic']['state'])
        # Gardes du lancement d'une nouvelle comparaison sur la même enveloppe personnelle
        auto.guard_budget(self.store, self.store._connection, self.budget, campaign_id='nouvelle-comparaison')
        auto.preflight(self.store, self.sid, 'fixture', self.cid, self.transport)
        self.http.request.assert_not_called()

    def test_redemarrage_clot_les_jugements_jamais_emis(self):
        from benchmark import automatic_judgment as auto, runtime
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        runtime.stop(self.data, self.store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        # Jamais émis : ces intentions closes ne partent plus ; la relance automatique en crée de nouvelles
        self.assert_reopened(ids, 'RUNNING')
        self.assertIn('Nouvelle tentative automatique dès que possible',
                      auto.status(self.store, self.store._connection, self.cid)['reason'])
        auto.execute_campaign(self.data, ids, self.transport)
        self.http.request.assert_not_called()

    def test_premier_jugement_refuse_avant_emission_clot_les_suivants(self):
        from benchmark import automatic_judgment as auto
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        with patch.object(type(self.transport), 'authorized', return_value=False):
            auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual('JUDGMENT_STOPPED', campaigns.inspect(self.store, self.cid)['stop_reason'])
        self.assert_reopened(ids)

    def test_premier_jugement_en_erreur_apres_emission_clot_les_suivants(self):
        from benchmark import automatic_judgment as auto
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        self.http.request.side_effect = OSError('connexion coupée')
        auto.execute_campaign(self.data, ids, self.transport)
        states = self.judgments(ids)
        # Effets inconnus : jamais rejoué ; la suivante n'est jamais partie
        self.assertEqual('AMBIGUOUS', states[ids[0]]['state'])
        self.assertEqual(('RECEIVED', {'status': 'NOT_SENT'}), (states[ids[1]]['state'], states[ids[1]]['receipt']['result']))
        self.assertEqual(1, self.http.request.call_count)
        self.assertEqual('BLOCKED', auto.status(self.store, self.store._connection, self.cid)['status'])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_verrou_persistant_du_recu_de_jugement_rend_ambigu_sans_reemission(self):
        from benchmark import automatic_judgment as auto
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        locked = sqlite3.OperationalError('database is locked')
        locked.sqlite_errorcode = sqlite3.SQLITE_BUSY
        with patch.object(storage, 'LOCK_RETRY_DELAYS', (0, 0)), \
                patch.object(storage.Store, 'record_receipt', side_effect=locked) as record:
            auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual(3, record.call_count)
        self.assertEqual('AMBIGUOUS', self.judgments(ids)[ids[0]]['state'])
        self.assertEqual(1, self.http.request.call_count)

    def test_erreur_sqlite_pendant_l_acquisition_arrete_la_campagne(self):
        f = self.fixture
        ids = campaigns.launch(self.store, self.sid, 'fixture', self.cid, f.body(),
                               access_secret=SECRET, access_transport=f.access)
        with patch.object(execution, 'execute', side_effect=sqlite3.OperationalError('database is locked')), \
                self.assertLogs('benchmark.acquisition.execution', level='WARNING') as journal:
            execution.execute_launch(self.data, ids, response, access_secret=SECRET, access_transport=f.access)
        self.assertIn('OperationalError', '\n'.join(journal.output))
        snapshot = campaigns.inspect(self.store, self.cid)
        self.assertIsNone(snapshot['admission'])
        self.assertEqual('ACQUISITION_STOPPED_BEFORE_EMISSION', snapshot['stop_reason'])
        view = campaigns.launch_view(self.store, self.sid, 'fixture', self.cid)
        self.assertNotIn('Benchmark en cours', views.render(view, 'csrf').decode())

    def test_fil_de_campagne_survit_a_une_erreur_inattendue(self):
        f = self.fixture
        start = self.dispatch('POST', '/start', dict(f.body(), csrf_token='csrf'))[3]
        with patch.object(execution, 'execute_launch', side_effect=sqlite3.OperationalError('database is locked')), \
                self.assertLogs('benchmark.service', level='ERROR') as journal:
            service._campaign_worker(self.data, start, response, None, SECRET, f.access, self.transport)
        self.assertIn('OperationalError', '\n'.join(journal.output))
        snapshot = campaigns.inspect(self.store, self.cid)
        self.assertIsNone(snapshot['admission'])
        self.assertIsNotNone(snapshot['stop_reason'])
        from benchmark import automatic_judgment as auto
        auto.guard_budget(self.store, self.store._connection, self.budget, campaign_id='nouvelle-comparaison')

    def test_annulation_apres_creation_de_piece_ne_laisse_aucun_octet_orphelin(self):
        f = self.fixture
        ids = campaigns.launch(self.store, self.sid, 'fixture', self.cid, f.body(),
                               access_secret=SECRET, access_transport=f.access)
        # La pièce de sortie est écrite dans la transaction du reçu, puis celle-ci est annulée
        with patch.object(campaigns, '_attribution', side_effect=RuntimeError('annulation fictive')):
            execution.execute_launch(self.data, ids[:1], response, access_secret=SECRET, access_transport=f.access)
        self.assertEqual([], self.store.verify_storage()['orphan_files'])
        campaigns._intact(self.store)
        self.assertEqual('AMBIGUOUS', self.judgments(ids)[ids[0]]['state'])

    def test_owned_post_and_proof_boundaries(self):
        from benchmark import automatic_judgment as auto
        self.acquire()
        with self.assertRaises(preparation.Denied):
            self.dispatch('POST', '/evaluate', dict(confirm='yes'))
        other, _, _ = preparation.session(self.store, None, create=True)
        with self.assertRaises(preparation.Denied):
            auto.reserve_campaign(self.store, other, 'fixture', self.cid, self.transport)
        ids = self.dispatch('POST', '/evaluate', dict(confirm='yes', csrf_token='csrf'))[3]['judgment_operations']
        auto.execute_campaign(self.data, ids, self.transport)
        link = restitution.comparison(self.store, self.sid, 'fixture', self.cid)['rows'][0]['proof_links'][0]
        with self.assertRaises(preparation.Denied):
            evaluation.piece_bytes(self.store, other, 'fixture', ids[0], link['piece_id'])
        with self.assertRaises(preparation.Denied):
            evaluation.piece_bytes(self.store, self.sid, 'fixture', ids[0], 'unrelated')
        with closing(storage.Store(self.data)) as reader:
            value = restitution.comparison(reader, self.sid, 'fixture', self.cid)
            self.assertEqual(['NE SATISFAIT PAS'] * 2, [r['verdict'] for r in value['rows']])
            self.assertEqual('COMPLETE', auto.status(reader, reader._connection, self.cid)['status'])
            self.assertEqual([], auto.reserve_campaign(reader, self.sid, 'fixture', self.cid, self.transport))
            self.assertEqual(0, reader._connection.execute('SELECT count(*) FROM s5_evaluations').fetchone()[0])
            for row in value['rows']:
                for link in row['proof_links']:
                    self.assertTrue(evaluation.piece_bytes(reader, self.sid, 'fixture', row['evaluation_id'], link['piece_id']))
            self.assertTrue(reader.verify_storage()['integrity_ok'])
        self.assertEqual(2, self.http.request.call_count)

    def test_connexion_impossible_au_jugement_clot_sans_cout_et_le_dit(self):
        import socket
        from benchmark import automatic_judgment as auto
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        self.http.connect.side_effect = socket.gaierror(8, 'nodename nor servname provided')
        auto.execute_campaign(self.data, ids, self.transport)
        self.http.request.assert_not_called()
        self.assert_reopened(ids, 'RUNNING')
        self.assertEqual('CONNECTION_FAILED', self.judgments(ids)[ids[0]]['receipt']['observed_configuration']['incident'])
        self.assertFalse(any(o['state'] == 'AMBIGUOUS' for o in self.store.inspect_operations()))
        reason = auto.status(self.store, self.store._connection, self.cid)['reason']
        self.assertIn('Impossible de joindre OpenRouter, rien n’a été envoyé ni facturé', reason)
        # La session reste utilisable : la même enveloppe admet une nouvelle comparaison
        auto.guard_budget(self.store, self.store._connection, self.budget, campaign_id='nouvelle-comparaison')

    def test_connexion_impossible_pour_un_candidat_clot_la_tentative_sans_cout(self):
        f = self.fixture
        ids = campaigns.launch(self.store, self.sid, 'fixture', self.cid, f.body(),
                               access_secret=SECRET, access_transport=f.access)
        calls = []

        def unreachable(op, request):
            calls.append(op['operation_id'])
            raise openrouter.NotSent('Connexion à OpenRouter impossible')
        execution.execute_launch(self.data, ids, unreachable, access_secret=SECRET, access_transport=f.access)
        self.assertEqual(1, len(calls))
        snapshot = campaigns.inspect(self.store, self.cid)
        attempt = next(a for a in snapshot['attempts'] if a['operation_id'] == calls[0])
        receipt, cost = attempt['operation']['receipt'], attempt['operation']['observed_cost']
        self.assertEqual(('RECEIVED', 'CONNECTION_FAILED', 'NOT_SENT', 'KNOWN', '0'),
                         (attempt['state'], receipt['result']['incident'], receipt['result']['emission'],
                          cost['status'], cost['amount']))
        self.assertIsNone(attempt['output_piece_id'])
        self.assertEqual('ACQUISITION_NOT_SENT', snapshot['stop_reason'])
        self.assertFalse(any(o['state'] in ('AMBIGUOUS', 'EMISSION_POSSIBLE') for o in self.store.inspect_operations()))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        from benchmark_web import campaign_views
        _, _, message = campaign_views.campaign_followup(campaigns.projection(self.store, self.store._connection, 'fixture')[-1])
        self.assertIn('Impossible de joindre OpenRouter, rien n’a été envoyé ni facturé', message)


class AutomaticJudgmentRetries(unittest.TestCase):
    """Relance automatique bornée de l'évaluation : horloge simulée, HTTP simulé, aucun bouton

    Modes d'échec couverts : relance jamais faite, faite trop tôt, au-delà de cinq tentatives, après un effet
    ambigu ou une clé refusée ; Retry-After ignoré ; minuteur perdu au redémarrage ; double réservation
    par un minuteur et un POST concurrents ; identifiant de rang 1 modifié ; jugement relancé compté deux
    fois ou absent des résultats ; bouton de relance affiché
    """
    acquire, answer, dispatch = AutomaticJudgment.acquire, AutomaticJudgment.answer, AutomaticJudgment.dispatch

    def setUp(self):
        AutomaticJudgment.setUp(self)
        from datetime import datetime, timezone
        # Origine fixe et passée : une date écrite avec l'horloge réelle au lieu de `_now` se voit
        self.now = [datetime(2026, 10, 2, 12, tzinfo=timezone.utc)]
        now = self.now

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now[0]
        self.enterContext(patch.object(preparation, '_now', side_effect=lambda: now[0]))
        self.enterContext(patch.object(openrouter, 'datetime', Clock))
        self.steps = []
        self.http.connect.side_effect = self.connect
        self.http.request.side_effect = self.scripted

    def connect(self):
        import socket
        if self.steps and self.steps[0] == 'unreachable':
            self.steps.pop(0)
            raise socket.gaierror(8, 'nodename nor servname provided')

    def scripted(self, method, path, *, body, headers):
        """Chaque appel suit le script : None, réponse normale ; statut HTTP, avec en-têtes ; exception"""
        step = self.steps.pop(0) if self.steps else None
        response = self.http.getresponse.return_value
        if step is None:
            response.status, response.getheader.side_effect = 200, None
            return self.answer(method, path, body=body, headers=headers)
        if isinstance(step, Exception):
            raise step
        status, extra = step if type(step) is tuple else (step, {})
        response.status, response.getheader.side_effect = status, extra.get
        response.read.return_value = storage._strict_json({'error': {'code': status}}).encode()

    def advance(self, seconds):
        from datetime import timedelta
        self.now[0] += timedelta(seconds=seconds)

    def retry_context(self):
        timers = {}
        return (self.transport, None, None, 'a' * 40, timers), timers

    def launch(self, steps):
        """Réponses reçues, puis évaluation lancée par le fil de campagne, minuteurs simulés"""
        self.steps = list(steps)
        self.acquire()
        retry, timers = self.retry_context()
        with patch.object(service.threading, 'Timer') as timer:
            service._campaign_worker(self.data, dict(candidate_attempts=[], judgment_campaign=self.cid,
                session_id=self.sid, dossier_id='fixture'), response, None, SECRET, self.fixture.access,
                self.transport, retry)
        return retry, timers, timer

    def due(self):
        from benchmark import automatic_judgment as auto
        return {o: d for o, _, _, d in auto.due_retries(self.store)}

    def progress(self):
        from benchmark import automatic_judgment as auto
        return auto.status(self.store, self.store._connection, self.cid)

    def page(self):
        return views.render(campaigns.launch_view(self.store, self.sid, 'fixture', self.cid), 'csrf').decode()

    def assert_no_manual_retry(self, page):
        self.assertNotIn('/evaluate', page)
        self.assertNotIn('Relancer', page)
        self.assertNotIn('Évaluer les réponses reçues', page)

    def series(self, attempt_id):
        return sorted((o for o in self.store.inspect_operations() if o['phase'] == 'judgment'
                       and json.loads(o['resources'][0])['request']['attempt_id'] == attempt_id),
                      key=lambda o: o['created_at'])

    def test_jugement_503_puis_succes_automatique_sans_action(self):
        from hashlib import sha256
        retry, timers, timer = self.launch([503])
        self.assertEqual(2, self.http.request.call_count)
        first, = timers
        attempt_id = json.loads(next(o for o in self.store.inspect_operations()
                                     if o['operation_id'] == first)['resources'][0])['request']['attempt_id']
        # Rang 1 : identifiant inchangé, compatible avec les données déjà enregistrées
        self.assertEqual('judge-' + sha256((self.cid + ':' + attempt_id).encode()).hexdigest()[:40], first)
        self.assertEqual(30, timer.call_args.args[0])
        self.assertEqual({first: timer.return_value}, timers)
        progress = self.progress()
        self.assertEqual(('RUNNING', 1, 2), (progress['status'], progress['completed'], progress['total']))
        page = self.page()
        self.assertIn('Nouvelle tentative automatique dans 30 s', page)
        self.assert_no_manual_retry(page)
        self.advance(30)
        with patch.object(service.threading, 'Timer'):
            timer.call_args.args[1](*timer.call_args.kwargs['args'])
        self.assertEqual(3, self.http.request.call_count)
        second = self.series(attempt_id)[-1]
        self.assertEqual('judge-' + sha256((self.cid + ':' + attempt_id + ':2').encode()).hexdigest()[:40],
                         second['operation_id'])
        self.assertEqual({'retry_of': first, 'attempt': 2}, json.loads(second['resources'][2]))
        progress = self.progress()
        self.assertEqual(('COMPLETE', 2, 2), (progress['status'], progress['completed'], progress['total']))
        # Restitué comme un jugement normal : une ligne par réponse, sans trace de l'échec
        rows = restitution.comparison(self.store, self.sid, 'fixture', self.cid)['rows']
        self.assertEqual(['NE SATISFAIT PAS'] * 2, [r['verdict'] for r in rows])
        self.assertEqual(2, len(auto_records(self.store, self.cid)))
        self.assertEqual({}, self.due())
        self.assertEqual({}, timers)
        self.assertNotEqual('JUDGMENT_STOPPED', campaigns.inspect(self.store, self.cid)['stop_reason'])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_cinq_echecs_puis_message_final_sans_autre_relance(self):
        from benchmark import automatic_judgment as auto
        self.launch([503, None, 502, 500, 429, 504])
        first = next(iter(self.due()))
        attempt_id = json.loads(next(o for o in self.store.inspect_operations()
                                     if o['operation_id'] == first)['resources'][0])['request']['attempt_id']
        for delay in (30, 120, 600, 1800):
            last = self.series(attempt_id)[-1]['operation_id']
            self.assertEqual({last: delay}, self.due())
            self.advance(delay - 1)
            self.assertEqual([], auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport))
            self.advance(1)
            ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
            self.assertEqual(1, len(ids))
            auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual(6, self.http.request.call_count)
        self.assertEqual(5, json.loads(self.series(attempt_id)[-1]['resources'][2])['attempt'])
        self.assertEqual({}, self.due())
        self.advance(86400)
        self.assertEqual([], auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport))
        self.assertEqual(6, self.http.request.call_count)
        progress = self.progress()
        self.assertEqual(('BLOCKED', 1, 2), (progress['status'], progress['completed'], progress['total']))
        self.assertIn('après 5 tentatives automatiques', progress['reason'])
        page = self.page()
        self.assertIn('après 5 tentatives automatiques', page)
        self.assertNotIn('Nouvelle tentative automatique', page)
        self.assertNotIn('équipe', page)
        self.assert_no_manual_retry(page)
        self.assertEqual(1, len(restitution.comparison(self.store, self.sid, 'fixture', self.cid)['rows']))

    def test_aucune_relance_apres_effet_ambigu(self):
        from benchmark import automatic_judgment as auto
        self.launch([503, OSError('connexion coupée après envoi')])
        self.assertTrue(any(o['state'] == 'AMBIGUOUS' for o in self.store.inspect_operations()))
        self.assertEqual({}, self.due())
        self.advance(3600)
        self.assertEqual([], auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport))
        self.assertEqual(2, self.http.request.call_count)
        self.assertNotIn('Nouvelle tentative automatique', self.page())

    def test_cle_refusee_ou_credit_epuise_jamais_relances(self):
        from benchmark import automatic_judgment as auto
        for status in (401, 402):
            with self.subTest(status=status):
                self.setUp()
                self.launch([status])
                self.assertEqual({}, self.due())
                self.advance(3600)
                self.assertEqual([], auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport))
                self.assertEqual(1, self.http.request.call_count)

    def test_retry_after_plus_long_respecte(self):
        from benchmark import automatic_judgment as auto
        self.launch([(429, {'Retry-After': '300'})])
        self.assertEqual([300], list(self.due().values()))
        self.advance(299)
        self.assertEqual([], auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport))
        self.advance(1)
        self.assertEqual(1, len(auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)))

    def test_redemarrage_pendant_l_attente_reprend_la_relance(self):
        from benchmark import automatic_judgment as auto, runtime
        retry, _, _ = self.launch([503])
        first = next(iter(self.due()))
        # Arrêt puis démarrage : le minuteur en mémoire est perdu, le démarrage le reprogramme
        runtime.stop(self.data, self.store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        retry, timers = self.retry_context()
        with patch.object(service.threading, 'Timer') as timer:
            service._resume_retries(self.store, self.data, dict(judgment=retry))
            self.assertEqual(30, timer.call_args.args[0])
            self.assertEqual([first], list(timers))
        # Relance réservée puis processus arrêté avant envoi : close sans coût, reprise sans consommer de rang
        self.advance(30)
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        runtime.stop(self.data, self.store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        self.assertEqual({'status': 'NOT_SENT'}, self.series(json.loads(next(
            o for o in self.store.inspect_operations() if o['operation_id'] == ids[0])['resources'][0])
            ['request']['attempt_id'])[-1]['receipt']['result'])
        self.assertEqual('RUNNING', self.progress()['status'])
        retry, timers = self.retry_context()
        with patch.object(service.threading, 'Timer') as timer:
            service._resume_retries(self.store, self.data, dict(judgment=retry))
            self.assertEqual(0, timer.call_args.args[0])
            with patch.object(service.threading, 'Timer'):
                timer.call_args.args[1](*timer.call_args.kwargs['args'])
        third = next(o for o in self.store.inspect_operations() if o['operation_id'] not in ids
                     and o['resources'][2:] and json.loads(o['resources'][2])['retry_of'] == ids[0])
        self.assertEqual({'retry_of': ids[0], 'attempt': 2}, json.loads(third['resources'][2]))
        self.assertEqual('COMPLETE', self.progress()['status'])
        self.assertEqual(3, self.http.request.call_count)

    def test_minuteur_et_post_concurrents_une_seule_operation(self):
        from benchmark import automatic_judgment as auto, judgment
        _, _, timer = self.launch([503])
        self.advance(30)
        # Deux calculs faits avant toute écriture donnent le même identifiant : seul le premier passe
        plans = [auto._plan(self.store, self.store._connection, self.sid, 'fixture', self.cid, self.transport)
                 for _ in range(2)]
        self.assertEqual(plans[0][0][0]['operation_id'], plans[1][0][0]['operation_id'])
        connection = evaluation.connection_for(self.store)
        with storage._transaction(connection, write=True):
            request, ctx, content, link = plans[0][0]
            judgment._reserve(self.store, connection, request, self.transport, automatic=True,
                              prepared=(ctx, content), link=link)
        with self.assertRaises((storage.ConflictError, storage.IntegrityError, storage.BudgetError)):
            with storage._transaction(connection, write=True):
                request, ctx, content, link = plans[1][0]
                judgment._reserve(self.store, connection, request, self.transport, automatic=True,
                                  prepared=(ctx, content), link=link)
        # Le POST et le minuteur suivants ne trouvent plus rien de dû
        self.assertEqual([], self.dispatch('POST', '/evaluate', dict(confirm='yes', csrf_token='csrf'))[3] or [])
        with patch.object(service.threading, 'Timer'):
            timer.call_args.args[1](*timer.call_args.kwargs['args'])
        self.assertEqual(3, len([o for o in self.store.inspect_operations() if o['phase'] == 'judgment']))

    def test_connexion_impossible_relancee_apres_attente(self):
        retry, timers, timer = self.launch(['unreachable'])
        self.assertEqual(1, self.http.request.call_count)
        progress = self.progress()
        self.assertEqual('RUNNING', progress['status'])
        self.assertIn('Impossible de joindre OpenRouter, rien n’a été envoyé ni facturé', progress['reason'])
        self.assertIn('Nouvelle tentative automatique dans 30 s', progress['reason'])
        self.advance(30)
        with patch.object(service.threading, 'Timer'):
            timer.call_args.args[1](*timer.call_args.kwargs['args'])
        self.assertEqual('COMPLETE', self.progress()['status'])
        self.assertEqual(2, self.http.request.call_count)


def auto_records(store, campaign_id):
    from benchmark import automatic_judgment as auto
    return auto.records(store, store._connection, campaign_id)


if __name__ == '__main__':
    unittest.main()
