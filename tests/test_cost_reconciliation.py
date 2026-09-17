"""Offline operator evidence; no model call or real billing record"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from benchmark import storage, preparation as prep, runtime, qualification, evaluation
from benchmark.acquisition import campaigns
from benchmark_web import views
from benchmark.transports import openrouter as assistant
from tests.test_openrouter_preparation import ESTIMATE, RESERVE, KEY, NEED, PROFILE, http_body, result


class CostReconciliationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='reconciliation-fixture-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.data = self.home / 'private'
        storage.initialize(self.data)
        storage.initialize_preparation(self.data)
        qualification.initialize(self.data)
        campaigns.initialize(self.data)
        evaluation.initialize(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        self.store.create_budget('fixture', '100', 'USD')
        self.authority = dict(authority_id='FIXTURE_ONLY', budget_id='fixture', reserve_amount=RESERVE,
                              requested_configuration=assistant.configuration(ESTIMATE))
        prep.admit(self.store, self.authority)
        self.session, self.csrf, self.token = prep.session(self.store, None, create=True)
        self.transport = assistant.OpenRouterPreparation(KEY)
        self.http = Mock()
        self.response = self.http.getresponse.return_value
        self.response.status, self.response.length = 429, 0
        self.response.read.return_value = b'{"error":{"code":429,"message":"fictional upstream unavailable"}}'
        self.response.getheader.return_value = None
        patched = patch.object(assistant, 'HTTPSConnection', return_value=self.http)
        patched.start()
        self.addCleanup(patched.stop)
        self.body = dict(action_id='original', request=NEED)

    def receive(self, generation=None):
        self.response.getheader.side_effect = {'X-Generation-Id': generation}.get
        self.operation_id, started = prep.submit(self.store, self.session, 'd', self.body, 'fixture-source', self.transport)
        self.assertTrue(started)
        prep.execute(self.data, self.operation_id, self.transport)
        self.original = self.store.inspect_operations()[0]
        self.assertEqual('UNKNOWN', self.original['observed_cost']['status'])
        self.assertEqual('suspended', prep.view(self.store, self.session, 'd')['stage'])
        self.assertIsNone(prep.admission(self.store))

    def proof(self, amount='0.012345678901234567890123456789', native=False):
        document = ('Fictional OpenRouter billing export. PRIVATE DOCUMENT SENTINEL. '
                    'An operator links this line to the fictional generation and account. Debit: ' + amount + ' USD.')
        if native:
            document = json.dumps({'data': {'id': 'gen-fixture', 'model': ESTIMATE['canonical_slug'], 'total_cost': amount}})
        return dict(format_identity=storage.RECONCILIATION_IDENTITY, operation_id=self.operation_id,
                    budget_id='fixture', receipt_sha256=sha256(storage._strict_json(self.original['receipt']).encode()).hexdigest(),
                    actor='fixture-operator', authority_id='FIXTURE_RECONCILIATION_ONLY', account_reference='fixture-account',
                    generation_id='gen-fixture', model=PROFILE['model'],
                    cost=dict(status='KNOWN', amount=amount, currency='USD', source='Fictional OpenRouter billing evidence'),
                    source=dict(kind='openrouter_generation' if native else 'operator_attested_openrouter_record',
                                http_status=200 if native else None, name='Fictional private billing export',
                                observed_at=datetime.now(timezone.utc).isoformat(), document=document,
                                sha256=sha256(document.encode()).hexdigest(), excerpt=document),
                    correlation='The trusted fixture operator attributes this account and generation to the exact operation and receipt; no key stored')

    def test_append_only_cost_and_explicit_new_action_keep_the_original_unknown_receipt(self):
        self.receive()
        self.store.initialize_reconciliation()
        proof = self.proof()
        before = deepcopy(self.store.inspect_operations())
        receipt = self.store.reconcile_cost(proof)
        self.assertEqual(receipt, self.store.reconcile_cost(deepcopy(proof)))
        self.assertEqual(before, self.store.inspect_operations())
        budget = self.store.inspect_budget('fixture')
        self.assertEqual(('100', '0', proof['cost']['amount'], []),
                         (budget['limit'], budget['reserved'], budget['spent'], budget['unknown_cost_operations']))
        self.assertEqual([], self.store.verify_storage()['unknown_cost_operations'])
        view = prep.view(self.store, self.session, 'd')
        self.assertEqual('UNKNOWN', view['observed_cost']['status'])
        self.assertEqual(proof['cost'], view['effective_cost'])
        self.assertEqual('suspended', view['stage'])
        self.assertNotIn('PRIVATE DOCUMENT SENTINEL', storage._strict_json(view))
        self.assertNotIn('fixture-account', storage._strict_json(view))
        self.assertNotIn('preparation_budget', view)
        rendered = views.render(view, self.csrf).decode()
        self.assertIn('Coût rapproché', rendered)
        self.assertIn('INCONNU', rendered)
        self.assertIsNone(prep.admission(self.store))
        self.assertEqual((self.operation_id, False), prep.submit(self.store, self.session, 'd', self.body, 'fixture-source', self.transport))
        message = dict(action_id='separate-action', revision=view['revision'], kind='clarify', message='Continuer le dossier fictif')
        with self.assertRaises(prep.Denied):
            prep.submit(self.store, self.session, 'd', message, 'fixture-source', self.transport)
        self.assertEqual(1, self.http.request.call_count)
        prep.admit(self.store, self.authority)
        self.response.status = 200
        self.response.getheader.side_effect = lambda name: None
        self.response.read.return_value = http_body(result('clarification'))
        next_id, started = prep.submit(self.store, self.session, 'd', message, 'fixture-source', self.transport)
        self.assertTrue(started)
        self.assertNotEqual(self.operation_id, next_id)
        prep.execute(self.data, next_id, self.transport)
        self.assertEqual(2, self.http.request.call_count)
        self.assertEqual('clarification', prep.view(self.store, self.session, 'd')['stage'])
        self.assertEqual(self.original, next(op for op in self.store.inspect_operations() if op['operation_id'] == self.operation_id))

    def test_generation_evidence_accepts_native_canonical_shape_but_never_404_or_429_as_zero(self):
        self.receive('gen-fixture')
        self.store.initialize_reconciliation()
        proof = self.proof('0', native=True)
        bad = []
        for status in (404, 429):
            value = deepcopy(proof); value['source']['http_status'] = status; bad.append(value)
        for data in ({'id': 'gen-fixture', 'model': ESTIMATE['canonical_slug']},
                     {'id': 'gen-fixture', 'model': PROFILE['model'] + '-20260827', 'total_cost': 0},
                     {'id': 'gen-other', 'model': PROFILE['model'], 'total_cost': 0},
                     {'id': 'gen-fixture', 'model': PROFILE['model'], 'total_cost': True},
                     {'id': 'gen-fixture', 'model': None, 'total_cost': 0},
                     {'id': 'gen-fixture', 'model': PROFILE['model'], 'total_cost': '100'}):
            value = deepcopy(proof); document = json.dumps({'data': data})
            value['source'].update(document=document, excerpt=document, sha256=sha256(document.encode()).hexdigest()); bad.append(value)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError): self.store.reconcile_cost(value)
            self.assertEqual([self.operation_id], self.store.inspect_budget('fixture')['unknown_cost_operations'])
        self.store.reconcile_cost(proof)
        self.assertEqual('0', self.store.inspect_cost(self.operation_id)['effective_cost']['amount'])
        self.assertEqual('UNKNOWN', self.store.inspect_operations()[0]['observed_cost']['status'])

    def test_bad_attribution_source_money_and_conflicting_proof_cannot_release_or_rewrite(self):
        self.receive('gen-fixture')
        self.store.initialize_reconciliation()
        proof = self.proof()
        for path, value in [(('budget_id',), 'foreign'), (('receipt_sha256',), '0' * 64),
                (('generation_id',), 'gen-other'), (('model',), 'other/model'), (('actor',), ''),
                (('source', 'sha256'), '0' * 64), (('source', 'excerpt'), 'absent from document'),
                (('source', 'document'), ''), (('source', 'observed_at'), '2020-01-01T00:00:00'),
                (('cost', 'currency'), 'EUR'), (('cost', 'amount'), '-1'), (('cost', 'amount'), 'NaN'),
                (('cost', 'amount'), 'Infinity'), (('cost', 'amount'), None), (('cost', 'amount'), True)]:
            bad = deepcopy(proof); target = bad if len(path) == 1 else bad[path[0]]; target[path[-1]] = value
            with self.subTest(path=path, value=value), self.assertRaises(ValueError): self.store.reconcile_cost(bad)
            self.assertEqual(RESERVE, self.store.inspect_budget('fixture')['reserved'])
        self.store.reconcile_cost(proof)
        conflicting = deepcopy(proof); conflicting['cost']['amount'] = '1'
        with self.assertRaises(storage.ConflictError): self.store.reconcile_cost(conflicting)
        self.assertEqual(proof['cost'], self.store.inspect_cost(self.operation_id)['effective_cost'])
        connection = self.store._connection
        connection.execute('UPDATE cost_reconciliations SET proof_sha256=?', ('0' * 64,))
        with self.assertRaises(storage.IntegrityError): self.store.verify_storage()
        self.assertEqual(self.original, self.store.inspect_operations()[0])

    def test_explicit_s5_extension_and_backup_restore_preserve_proofs_and_restore_gate(self):
        self.assertEqual('s5', storage._check_schema(self.store._connection))
        self.assertIsNone(self.store.verify_storage()['cost_reconciliation_format'])
        with self.assertRaises(storage.ConflictError): self.store.initialize_reconciliation()
        self.receive()
        before = runtime.backup(self.data, self.home / 'before')
        self.assertIsNone(before['cost_reconciliation_format'])
        self.store.initialize_reconciliation()
        proof = self.proof()
        self.store.reconcile_cost(proof)
        self.assertEqual('s5', storage._check_schema(self.store._connection))
        self.assertEqual(1, self.store._connection.execute('PRAGMA user_version').fetchone()[0])
        self.assertEqual(storage.RECONCILIATION_IDENTITY, self.store.verify_storage()['cost_reconciliation_format'])
        self.assertEqual(proof, self.store.inspect_cost(self.operation_id)['reconciliation']['proof'])
        runtime.backup(self.data, self.home / 'after')
        restored = self.home / 'restored'
        runtime.restore(self.home / 'after', restored)
        with closing(storage.Store(restored)) as other:
            self.assertEqual(self.original, other.inspect_operations()[0])
            self.assertEqual(proof['cost'], other.inspect_cost(self.operation_id)['effective_cost'])
            self.assertTrue(runtime.status(restored, other)['restore_pending'])
            with self.assertRaises(ValueError): prep.admit(other, self.authority)
        restored_before = self.home / 'restored-before'
        runtime.restore(self.home / 'before', restored_before)
        with closing(storage.Store(restored_before)) as other:
            self.assertIsNone(other.verify_storage()['cost_reconciliation_format'])
            self.assertEqual('UNKNOWN', other.inspect_cost(self.operation_id)['effective_cost']['status'])
        self.assertEqual(1, self.http.request.call_count)

    def test_concurrent_idempotence_and_private_cli_keep_other_unknown_costs_visible(self):
        self.receive()
        proof = self.proof('101')
        with self.assertRaises(storage.SchemaError): self.store.reconcile_cost(proof)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, runtime.main(['initialize-reconciliation', '--data', str(self.data)]))
        def apply():
            with closing(storage.Store(self.data)) as store: return store.reconcile_cost(proof)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: apply(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual('101', self.store.inspect_budget('fixture')['spent'])
        self.assertEqual('-1', self.store.inspect_budget('fixture')['available'])
        path = self.home / 'identity.json'; path.write_text(json.dumps({'operation_id': self.operation_id})); path.chmod(0o600)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, runtime.main(['inspect-cost', '--data', str(self.data), '--authority', str(path)]))
        self.assertEqual('UNKNOWN', json.loads(output.getvalue())['observed_cost']['status'])
        self.assertEqual('101', json.loads(output.getvalue())['effective_cost']['amount'])
        prep.admit(self.store, self.authority)
        with self.assertRaises(storage.BudgetError):
            prep.submit(self.store, self.session, 'd', dict(action_id='over', revision=2, kind='clarify', message='Fictif'), 'source', self.transport)
        self.assertEqual(1, self.http.request.call_count)


if __name__ == '__main__':
    unittest.main()
