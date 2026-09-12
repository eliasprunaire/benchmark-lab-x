"""Additional private CLI, lifecycle and concurrency checks; no provider calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from copy import deepcopy
import io
import json
import os
import threading
import unittest
from unittest.mock import patch

from benchmark_lab_x import evaluation, judgment, runtime, storage
from benchmark_lab_x.openrouter_judgment import OpenRouterJudgment
from tests import test_s14_acceptance as acceptance

KEY = acceptance.KEY


class JudgmentTests(unittest.TestCase):
    def setUp(self):
        self.h = acceptance.S14Acceptance()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def test_profile_change_after_reservation(self):
        h = self.h
        judgment.reserve(h.store, h.request(), h.transport)
        profile = deepcopy(h.profile)
        profile['system'] += ' changed'
        with self.assertRaises(ValueError):
            judgment.execute(h.data, 's14-judge', OpenRouterJudgment(KEY, profile))
        h.http.request.assert_not_called()
        judgment.execute(h.data, 's14-judge', h.transport)
        self.assertEqual(h.http.request.call_count, 1)

    def test_new_judge_work_remains_visible_after_a_decision(self):
        from benchmark_lab_x import restitution
        h = self.h
        first = h.submit(h.execute())
        second = h.execute('second-review')
        status = evaluation.attempt_status(h.store, 'local-comparison', 'intent-x')
        self.assertEqual('SATISFAIT', status['verdict'])
        self.assertTrue(status['judgment']['review_pending'])
        view = restitution.comparison(h.store, h.fixture.session, 'fixture', 'local-comparison')
        self.assertEqual('second-review', view['pending_attempts'][0]['operation_id'])
        self.assertEqual(first, evaluation.inspect(h.store, first['evaluation_id']))
        final = h.submit(second)
        self.assertEqual(first['evaluation_id'], final['previous_evaluation_id'])
        view = restitution.comparison(h.store, h.fixture.session, 'fixture', 'local-comparison')
        self.assertEqual([], view['pending_attempts'])

    def test_competing_execution_has_one_emission(self):
        h = self.h
        judgment.reserve(h.store, h.request(), h.transport)
        barrier = threading.Barrier(2)
        def run():
            barrier.wait(timeout=5)
            try:
                judgment.execute(h.data, 's14-judge', OpenRouterJudgment(KEY, h.profile))
                return 'received'
            except storage.ConflictError:
                return 'conflict'
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertCountEqual(results, ['received', 'conflict'])
        self.assertEqual(h.http.request.call_count, 1)
        self.assertEqual(h.count(), 0)

    def test_competing_corrections_have_one_successor(self):
        h = self.h
        first = h.submit(h.execute())
        view = h.execute('correction')
        barrier = threading.Barrier(2)
        def run():
            with closing(storage.Store(h.data)) as store:
                barrier.wait(timeout=5)
                try:
                    return evaluation.submit_report(store, dict(
                        campaign_id='local-comparison', attempt_id='intent-x',
                        previous_evaluation_id=first['evaluation_id'], responsible='Reviewer',
                        authority=dict(actor='Ayo', authority_id='LOCAL_DECISION'),
                        report=view['proposal']['report']))['previous_evaluation_id']
                except storage.ConflictError:
                    return 'conflict'
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertCountEqual(results, [first['evaluation_id'], 'conflict'])
        self.assertEqual(h.count(), 2)

    def test_maintenance_and_restore_block_pending_emission(self):
        h = self.h
        judgment.reserve(h.store, h.request(), h.transport)
        marker = h.data / 'restore.json'
        marker.write_text('{"state":"RESTORED_RECONCILIATION_REQUIRED"}')
        with self.assertRaises(storage.ConflictError):
            judgment.execute(h.data, 's14-judge', h.transport)
        marker.unlink()
        runtime.stop(h.data, h.store, 'maintenance')
        with self.assertRaises(storage.ConflictError):
            judgment.execute(h.data, 's14-judge', h.transport)
        with self.assertRaises(storage.ConflictError):
            judgment.reserve(h.store, h.request('new'), h.transport)
        self.assertIsNone(judgment.inspect(h.store, 's14-judge')['proposal'])
        h.http.request.assert_not_called()

    def test_late_receipt_after_stop_is_retained_without_rewriting_history(self):
        h = self.h
        first = h.fixture.evaluate()
        def late(*args, **kwargs):
            runtime.stop(h.data, h.store, 'maintenance')
            h.store.mark_ambiguous('s14-judge', 'worker exited')
        h.http.request.side_effect = late
        view = h.execute()
        self.assertEqual(view['operation']['state'], 'RECEIVED')
        self.assertIsNotNone(view['proposal'])
        self.assertEqual(evaluation.inspect(h.store, first['evaluation_id']), first)
        self.assertEqual(h.count(), 1)
        with self.assertRaises(storage.ConflictError):
            judgment.execute(h.data, 's14-judge', h.transport)
        self.assertEqual(h.http.request.call_count, 1)

    def test_backup_lock_refuses_before_reservation(self):
        h = self.h
        with closing(storage.Store(h.data)) as locked, runtime.worker_lock(locked):
            with self.assertRaises(BlockingIOError):
                judgment.reserve(h.store, h.request(), h.transport)
        h.http.request.assert_not_called()
        self.assertFalse(any(x['phase'] == 'judgment' for x in h.store.inspect_operations()))
        judgment.reserve(h.store, h.request(), h.transport)

    def test_cli_explicit_profile_and_read_without_key_or_replay(self):
        h = self.h
        request = h.fixture.home / 'request.json'
        profile = h.fixture.home / 'profile.json'
        profile.write_text(storage._strict_json(h.profile)); profile.chmod(0o600)
        request.write_text(storage._strict_json(h.request())); request.chmod(0o600)
        def call(action, *, selected=True, key=True):
            args = [action, '--data', str(h.data), '--authority', str(request)]
            if selected:
                args += ['--judgment-profile', str(profile)]
            with patch.dict(os.environ, {'OPENROUTER_API_KEY': KEY} if key else {}, clear=True), redirect_stdout(io.StringIO()) as output:
                code = runtime.main(args)
            return code, json.loads(output.getvalue())
        self.assertNotEqual(call('reserve-judgment', selected=False)[0], 0)
        self.assertEqual(call('reserve-judgment')[0], 0)
        h.http.request.assert_not_called()
        request.write_text('{"operation_id":"s14-judge"}')
        self.assertEqual(call('execute-judgment')[0], 0)
        code, view = call('inspect-judgment', selected=False, key=False)
        self.assertEqual(code, 0)
        self.assertIsNotNone(view['proposal'])
        self.assertEqual(h.http.request.call_count, 1)

    def test_refusal_with_valid_json_is_not_a_proposal(self):
        h = self.h
        doc = json.loads(h.http.getresponse.return_value.read.return_value)
        doc['choices'][0]['message']['refusal'] = 'refused'
        h.http.getresponse.return_value.read.return_value = storage._strict_json(doc).encode()
        view = h.execute()
        self.assertIsNone(view['proposal'])
        self.assertEqual(view['operation']['observed_cost']['amount'], '0.0002')

    def test_false_operator_passage_refused(self):
        h = self.h
        view = h.execute()
        view['proposal']['report']['findings'][0]['evidence'][0]['passage'] = 'missing passage'
        with self.assertRaises(storage.IntegrityError):
            h.submit(view)
        self.assertEqual(h.count(), 0)

    def test_operational_cost_is_never_proved_by_output_passages(self):
        from tests import test_s5_regressions as s5
        original = s5.specification
        def cost_spec(reference):
            spec = original(reference)
            spec['obligations'][0]['description'] = 'Coût observé inférieur au plafond'
            return spec
        with patch.object(s5, 'specification', cost_spec):
            h = acceptance.S14Acceptance()
            h.setUp()
        self.addCleanup(h.doCleanups)
        view = h.execute()
        finding = view['proposal']['report']['findings'][0]
        self.assertEqual(finding['status'], 'INDETERMINE')
        pending = h.submit(view)
        self.assertIsNone(pending['verdict'])
        self.assertEqual('REVIEW_REQUIRED', pending['state'])
        self.assertEqual(h.count(), 1)
        next_view = h.execute('local-cost-correction')
        next_view['proposal']['report']['findings'][0] = deepcopy(h.answer['findings'][0])
        with self.assertRaisesRegex(ValueError, 'Preuve opérationnelle'):
            h.submit(next_view)
        self.assertEqual(h.count(), 1)

    def test_null_extra_field_cannot_disappear_during_response_parsing(self):
        h = self.h
        answer = deepcopy(h.answer)
        answer['authority'] = None
        h.set_response(answer=answer)
        self.assertIsNone(h.execute()['proposal'])

    def test_received_proposal_is_still_readable_after_campaign_stop(self):
        h = self.h
        view = h.execute()
        record = h.submit(view)
        runtime.stop(h.data, h.store, 'maintenance')
        self.assertEqual(judgment.inspect(h.store, 's14-judge'), view)
        self.assertEqual(evaluation.inspect(h.store, record['evaluation_id']), record)
        self.assertTrue(h.store.verify_storage()['integrity_ok'])

    def test_unknown_judgment_cost_cannot_be_bypassed_with_another_budget(self):
        h = self.h
        h.set_response(cost=None)
        h.execute()
        h.store.create_budget('other-budget', '100', 'USD')
        request = h.request('dependent')
        request['budget_id'] = 'other-budget'
        with self.assertRaisesRegex(storage.BudgetError, 'dépendant'):
            judgment.reserve(h.store, request, h.transport)
        self.assertEqual(h.http.request.call_count, 1)
        self.assertEqual(h.store.inspect_budget('other-budget')['reserved'], '0')

    def test_divergent_provider_retains_cost_but_no_proposal(self):
        h = self.h
        h.set_response(openrouter_metadata=dict(endpoints=dict(available=[
            dict(provider='Foreign provider', selected=True)])))
        view = h.execute()
        self.assertIsNone(view['proposal'])
        self.assertEqual(view['operation']['observed_cost']['amount'], '0.0002')
        self.assertEqual(view['operation']['receipt']['observed_configuration']['incident'],
                         'UNUSABLE_JUDGMENT_PROPOSAL')

    def test_receipt_and_proposal_integrity_checks_detect_representative_defects(self):
        h = self.h
        view = h.execute()
        connection = evaluation.connection_for(h.store)
        _, ctx = judgment._bound(h.store, connection, view['operation'])
        self.assertEqual(judgment._retained_proposal(h.store, connection, view['operation'], ctx),
                         view['proposal'])
        for kind in ('hash', 'proposal', 'wire'):
            operation = deepcopy(view['operation'])
            if kind == 'hash':
                operation['receipt']['observed_configuration']['http']['body_sha256'] = '0' * 64
            elif kind == 'proposal':
                operation['receipt']['result']['report']['findings'][0]['status'] = 'FAIL'
            else:
                operation['receipt']['resources_seen'] = ['foreign wire']
            with self.subTest(kind=kind), self.assertRaises(storage.IntegrityError):
                judgment._retained_proposal(h.store, connection, operation, ctx)
