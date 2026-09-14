"""Owner launch uses private admission, never authority supplied by HTTP"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from benchmark import campaigns as c, preparation as p, qualification as q, storage
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
        return dict(manifest_sha256=self.snapshot['manifest_sha256'], admission_id=record['admission_id'], confirm='yes')

    def launch(self, body, sid=None):
        return c.launch(self.store, sid or self.sid, 'fixture', 'local-comparison', body)

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
