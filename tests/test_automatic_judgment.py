"""Automatic private verdicts on retained requester responses; no real network"""
from contextlib import closing
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from benchmark import evaluation, preparation, provider_access, restitution, service, storage, web_api
from benchmark.acquisition import campaigns, execution
from benchmark.transports import openrouter
from benchmark_web import views
from tests import test_campaign_launch as campaign_fixture
from tests.test_openrouter_preparation import SYNTHETIC_PROFILE, estimate_for
from tests.test_provider_access import SECRET
from tests.test_s4_regressions import response


class AutomaticJudgment(unittest.TestCase):
    def setUp(self):
        self.fixture = campaign_fixture.RequesterCampaignLaunch()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.connect()
        f = self.fixture
        self.store, self.data, self.sid, self.cid = f.store, f.data, f.sid, f.campaign_id
        self.budget = provider_access.preparation_budget_id(self.sid)
        self.store.create_budget(self.budget, '20', 'USD')
        profile = openrouter.load_profile(str(SYNTHETIC_PROFILE))
        self.transport = openrouter.OpenRouterJudgment(None, profile).for_session(
            provider_access.key_for_session(self.store, self.sid, SECRET, f.access), self.sid, SECRET)
        self.transport._quote = openrouter.configuration(estimate_for(profile), profile)
        self.http = Mock()
        self.http.getresponse.return_value.status = 200
        self.http.getresponse.return_value.length = 0
        self.http.getresponse.return_value.getheader.return_value = None
        self.enterContext(patch.object(openrouter, 'HTTPSConnection', return_value=self.http))
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

    def dispatch(self, method, suffix, body=None):
        with patch.object(preparation, 'session', return_value=(self.sid, 'csrf', 'token')):
            return web_api.dispatch(self.store, method,
                '/preparation/dossiers/fixture/campaigns/' + self.cid + suffix, 'token', body, 'a' * 40, True,
                candidate_transport=response, judgment_transport=self.transport, personal_preparation=True,
                access_secret=SECRET, access_transport=self.fixture.access)

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
            self.assertIn('NE SATISFAIT PAS', views.render(detail, 'csrf').decode())
        self.assertEqual(2, self.http.request.call_count)

    def test_budget_refuses_before_any_candidate_or_judge_and_is_not_renewed(self):
        self.transport._quote['reserve_usd'] = '11'
        with self.assertRaises(storage.BudgetError):
            self.dispatch('POST', '/start', dict(self.fixture.body(), csrf_token='csrf'))
        self.assertEqual([], campaigns.inspect(self.store, self.cid)['attempts'])
        self.assertEqual('20', self.store.inspect_budget(self.budget)['limit'])
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
        self.assertEqual(before, self.store.inspect_operations())
        records = auto.records(self.store, self.store._connection, self.cid)
        self.assertTrue(records[0]['judgment']['evidence_binding']['recovered_from_receipt'])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        self.assertEqual(2, self.http.request.call_count)

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
        self.assertEqual(('BLOCKED', 1, 2), (progress['status'], progress['completed'], progress['total']))
        view = campaigns.launch_view(self.store, self.sid, 'fixture', self.cid)
        page = views.render(view, 'csrf').decode()
        self.assertIn('Comparer les résultats et lire les preuves', page)
        self.assertNotIn('id="preparation-progress"', page)

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


if __name__ == '__main__':
    unittest.main()
