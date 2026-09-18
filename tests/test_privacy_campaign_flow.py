"""Parcours privé S7 complet, vrais gardes et reçus synthétiques sans réseau"""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from benchmark import (automatic_judgment as auto, judgment, model_catalogue,
                       preparation as prep, privacy, privacy_archive as archive,
                       provider_access, restitution, service, storage)
from benchmark.acquisition import campaigns, execution
from benchmark.transports import openrouter
from tests.test_configurations import model
from tests.test_openrouter_preparation import (CORRECTION, NEED, NOTES, REFERENCE, SYNTHETIC_PROFILE,
                                             estimate_for, result)
from tests.test_privacy import NOW, initialize
from tests.test_provider_access import AccessTransport, KEY, SECRET
from tests.test_s4_regressions import response


class PrivacyCampaignFlow(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))
        self.enterContext(patch.object(privacy, 'now', return_value=NOW))
        self.enterContext(patch.object(model_catalogue, '_now', return_value=NOW))
        temporary = tempfile.TemporaryDirectory(prefix='privacy-campaign-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name).resolve() / 'private'
        initialize(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        self.sid, _, self.token = prep.session(self.store, None, create=True)
        self.access = AccessTransport()
        provider_access.import_key(self.store, self.sid, SECRET, KEY, self.access)
        self.profile = openrouter.load_profile(str(SYNTHETIC_PROFILE))
        self.config = openrouter.configuration(estimate_for(self.profile), self.profile)
        self.budget = provider_access.preparation_budget_id(self.sid)
        prep.admit(self.store, dict(authority_id='SYNTHETIC_PERSONAL', budget_id=self.budget,
            reserve_amount=self.config['reserve_usd'], requested_configuration=self.config))
        self.preparer = self.bound(openrouter.OpenRouterPreparation)
        self.qualifier = self.bound(openrouter.OpenRouterQualification)
        self.judge = self.bound(openrouter.OpenRouterJudgment)
        self.http = Mock()
        self.http.getresponse.return_value.status = 200
        self.http.getresponse.return_value.length = 0
        self.http.getresponse.return_value.getheader.return_value = None
        self.http.request.side_effect = self.answer
        self.enterContext(patch.object(openrouter, 'HTTPSConnection', return_value=self.http))
        self.stage = 'preparation'
        self.unknown_judgment = False
        self.unknown_candidate = False
        self.boundary = lambda: None
        self.candidate_calls = []
        rows = [model('openai/gpt-5.6-sol', 'openai', ['low', 'high']),
                model('deepseek/deepseek-v4.1-flash', 'deepseek', [])]
        document = dict(models=[row[0] for row in rows],
                        endpoints={row[0]['id']: row[1] for row in rows})
        def fetch(path):
            value = document['models'] if path == '/api/v1/models' else document['endpoints'][
                path.removeprefix('/api/v1/models/').removesuffix('/endpoints')]
            return {'data': value}
        model_catalogue.refresh(self.store, fetch)

    def bound(self, transport_type):
        transport = transport_type(None, self.profile).for_session(KEY, self.sid, SECRET)
        transport._quote = self.config
        return transport

    def answer(self, method, path, *, body, headers):
        self.boundary()
        if self.stage == 'preparation':
            answer = result()
            answer['package']['candidate']['criteria'] = {
                'eliminatory': [], 'obligations': ['Toutes les actions présentes'],
                'quality': [{'label': 'Clarté', 'scale': ['excellent', 'acceptable', 'faible'],
                             'favorable': 'excellent'}]}
        elif self.stage == 'qualification':
            answer = dict(qualified=True, findings=[], summary='Exemple synthétique qualifié')
        else:
            review = json.loads(json.loads(body)['messages'][1]['content'])
            output = review['output']
            proof = {key: output[key] for key in ('piece_id', 'sha256')}
            proof['passage'] = output['content']
            findings = [dict(criterion_id=criterion['id'], control_id=control,
                status='FAIL', attribution='candidate', finding='Action absente', evidence=[proof])
                for criterion in review['obligations'] + review['eliminatory_errors']
                for control in criterion['control_ids']]
            answer = dict(findings=findings, measures=[], limits=[], proposed_verdict='SATISFAIT')
        document = dict(id='synthetic-' + self.stage, model=self.profile['revision'],
            choices=[dict(finish_reason='stop', message=dict(role='assistant', content=storage._strict_json(answer)))],
            usage={} if self.stage == 'judgment' and self.unknown_judgment else dict(cost='0.001'),
            openrouter_metadata=dict(requested=self.profile['model'], endpoints=dict(available=[
                dict(provider=self.profile['routes'][0]['provider_name'], selected=True)])))
        self.http.getresponse.return_value.read.return_value = storage._strict_json(document).encode()

    def prepare(self, dossier):
        self.stage = 'preparation'
        operation, start = prep.submit(self.store, self.sid, dossier,
            dict(action_id='create', request=NEED), 'a' * 40, self.preparer)
        self.assertTrue(start)
        prep.execute(self.data, operation, self.preparer)
        self.assertEqual('RECEIVED', self.operation(operation)['state'])
        self.assertEqual(self.budget, self.operation(operation)['budget_id'])
        return operation

    def reserve_qualification(self, dossier):
        self.stage = 'qualification'
        preview = prep.view(self.store, self.sid, dossier)
        _, operation, _ = prep.validate_and_qualify(self.store, self.sid, dossier,
            prep.binding(dossier, preview['revision'], preview['package_sha256']), 'a' * 40, self.qualifier)
        return operation

    def qualify(self, dossier):
        operation = self.reserve_qualification(dossier)
        prep.execute_qualification(self.data, operation, self.qualifier)
        self.assertTrue(prep.view(self.store, self.sid, dossier)['qualified'])

    def campaign(self, dossier):
        identity = dict(package='@earendil-works/pi-coding-agent', version='0.85.1',
            sha256='1' * 64, bridge_sha256='2' * 64, node_version='v24.0.0',
            node_sha256='3' * 64, scope='Fixture locale sans exécution Pi')
        current = campaigns.prepare_configurations(self.store, self.sid, dossier,
            dict(models=['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], tier='standard'), identity)
        cid = current['current_campaign_id']
        snapshot = campaigns.inspect(self.store, cid)
        body = dict(manifest_version=snapshot['manifest']['version'],
                    frozen_at=snapshot['manifest']['conditions']['frozen_at'], confirm='yes')
        attempts = campaigns.launch(self.store, self.sid, dossier, cid, dict(body),
            access_secret=SECRET, access_transport=self.access)
        self.assertEqual(2, len(attempts))
        body['admission_id'] = campaigns.inspect(self.store, cid)['admission']['admission_id']
        return cid, attempts, body

    def candidate(self, operation, request):
        self.candidate_calls.append(operation['operation_id'])
        self.boundary()
        value = response(operation, request)
        value['cost'].update(currency='USD', amount=None if self.unknown_candidate else '0.002',
                             status='UNKNOWN' if self.unknown_candidate else 'KNOWN')
        return value

    def acquire(self, attempts):
        execution.execute_launch(self.data, attempts, self.candidate,
                                 access_secret=SECRET, access_transport=self.access)

    def operation(self, identity):
        return next(op for op in self.store.inspect_operations() if op['operation_id'] == identity)

    def record(self, dossier, manifest):
        parts = []
        for part in range((manifest['items'][0]['length'] + archive.CHUNK_BYTES - 1) // archive.CHUNK_BYTES):
            item = archive.archive_item(self.store, self.sid, dossier, manifest['snapshot_id'], part)
            parts.append(bytes.fromhex(item['hex']))
        raw = b''.join(parts)
        self.assertEqual(manifest['items'][0]['length'], len(raw))
        self.assertEqual(manifest['items'][0]['sha256'], sha256(raw).hexdigest())
        return json.loads(raw)

    def contribute(self, dossier):
        view = prep.view(self.store, self.sid, dossier)
        return archive.change_contribution(self.store, self.token, dossier,
            dict(example_revision=view['revision'], revision=0, enabled=True))

    def corpus(self, dossier):
        row = self.store._connection.execute('SELECT payload_json FROM s7_contributions WHERE dossier_id=?',
                                             (dossier,)).fetchone()
        return None if row is None else json.loads(row[0])

    def test_personal_flow_archives_refresh_and_full_purge_preserve_other_dossier(self):
        self.prepare('erase')
        preview = prep.view(self.store, self.sid, 'erase')
        correction, _ = prep.submit(self.store, self.sid, 'erase',
            dict(action_id='correction', revision=preview['revision'], kind='correct', message=CORRECTION),
            'a' * 40, self.preparer)
        prep.execute(self.data, correction, self.preparer)
        self.qualify('erase')
        consent, _ = self.contribute('erase')
        initial = archive.archive_manifest(self.store, self.sid, 'erase')
        initial_record = self.record('erase', initial)
        self.assertEqual(NEED, initial_record['need'])
        self.assertIn(CORRECTION, initial_record['messages'])
        self.assertEqual(prep.view(self.store, self.sid, 'erase', include_history=True)['task_index']['revisions'],
                         [revision['number'] for revision in initial_record['revisions']])
        self.assertEqual(NOTES, initial_record['revisions'][-1]['pieces'][0]['text'])
        self.assertIn('QUALIFIED', initial_record['revisions'][-1]['qualification'])
        self.assertEqual([], initial_record['campaigns'])
        cid, attempts, body = self.campaign('erase')
        self.stage = 'judgment'
        start = dict(candidate_attempts=attempts, judgment_campaign=cid, session_id=self.sid, dossier_id='erase')
        service._retention_worker(self.data, 'erase', service._campaign_worker,
            self.data, start, self.candidate, None, SECRET, self.access, self.judge)
        compared = restitution.comparison(self.store, self.sid, 'erase', cid)
        self.assertEqual(2, len(compared['rows']))
        self.assertEqual({'NE SATISFAIT PAS'}, {row['verdict'] for row in compared['rows']})
        self.assertEqual('COMPLETE', auto.status(self.store, self.store._connection, cid)['status'])
        final = archive.archive_manifest(self.store, self.sid, 'erase')
        record = self.record('erase', final)
        self.assertNotEqual(initial['snapshot_id'], final['snapshot_id'])
        self.assertEqual(initial_record, self.record('erase', initial))
        models = record['campaigns'][0]['models']
        self.assertEqual(2, len(models))
        self.assertTrue(all(row['answer'] == '  fictional raw output\n' for row in models))
        self.assertTrue(all(row['verdict'] == 'NE SATISFAIT PAS' for row in models))
        self.assertEqual([dict(amount='0.002', currency='USD')] * 2, [row['cost'] for row in models])
        self.assertEqual(record['campaigns'], self.corpus('erase')['campaigns'])
        metadata = archive.contribution_view(self.store, self.sid, 'erase')['contribution']
        self.assertGreater(metadata['revision'], consent['contribution']['revision'])
        self.assertEqual(consent['contribution']['expires_at'], metadata['expires_at'])
        self.assertNotIn(REFERENCE, storage._strict_json(record))
        self.assertNotIn(KEY, storage._strict_json(record))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store._connection.execute('UPDATE s7_archives SET record=record WHERE snapshot_id=?',
                                           (initial['snapshot_id'],))
        foreign, _, _ = prep.session(self.store, None, create=True)
        with self.assertRaises(prep.Denied):
            archive.archive_manifest(self.store, foreign, 'erase')
        self.assertEqual([], campaigns.launch(self.store, self.sid, 'erase', cid, body,
            access_secret=SECRET, access_transport=self.access))
        self.prepare('keep')
        self.qualify('keep')
        keep_cid, keep_attempts, _ = self.campaign('keep')
        self.stage = 'judgment'
        service._retention_worker(self.data, 'keep', service._campaign_worker,
            self.data, dict(candidate_attempts=keep_attempts, judgment_campaign=keep_cid,
                            session_id=self.sid, dossier_id='keep'),
            self.candidate, None, SECRET, self.access, self.judge)
        self.assertEqual('COMPLETE', auto.status(self.store, self.store._connection, keep_cid)['status'])
        keep_campaign = campaigns.inspect(self.store, keep_cid)
        keep_comparison = restitution.comparison(self.store, self.sid, 'keep', keep_cid)
        keep_operations = [op for op in self.store.inspect_operations() if op['dossier_id'] == 'keep']
        keep_manifest = archive.archive_manifest(self.store, self.sid, 'keep')
        self.contribute('keep')
        before_keep = prep.view(self.store, self.sid, 'keep', include_history=True)
        keep_record, keep_corpus = self.record('keep', keep_manifest), self.corpus('keep')
        budgets = {bid: self.store.inspect_budget(bid) for bid in (self.budget, cid, keep_cid)}
        operations = [op for op in self.store.inspect_operations() if op['dossier_id'] == 'erase']
        paths = [self.data / row[0] for row in self.store._connection.execute(
            'SELECT relative_path FROM pieces WHERE dossier_id=?', ('erase',))]
        self.assertTrue(paths)
        privacy.request_delete(self.store, self.sid, 'erase')
        self.assertIsNone(self.corpus('erase'))
        self.assertEqual(['erase'], privacy.purge(self.data)['purged'])
        self.assertFalse(any(path.exists() for path in paths))
        self.assertEqual(before_keep, prep.view(self.store, self.sid, 'keep', include_history=True))
        self.assertEqual(keep_record, self.record('keep', keep_manifest))
        self.assertEqual(keep_corpus, self.corpus('keep'))
        self.assertEqual(keep_campaign, campaigns.inspect(self.store, keep_cid))
        self.assertEqual(keep_comparison, restitution.comparison(self.store, self.sid, 'keep', keep_cid))
        self.assertEqual(keep_operations, self.store.inspect_operations())
        self.assertEqual(budgets, {bid: self.store.inspect_budget(bid) for bid in budgets})
        self.assertEqual({op['operation_id'] for op in operations},
                         {op['operation_id'] for op in privacy.retired_operations(self.store._connection)})
        for table in ('s4_campaigns', 's4_cells', 's4_attempts', 's4_status', 's4_caps', 's4_admissions', 's5_evaluations'):
            self.assertEqual(0, self.store._connection.execute(
                f'SELECT count(*) FROM {table} WHERE campaign_id=?', (cid,)).fetchone()[0], table)
        for operation in operations:
            for table in ('s4_emissions', 's4_results', 'reservations'):
                self.assertEqual(0, self.store._connection.execute(
                    f'SELECT count(*) FROM {table} WHERE operation_id=?',
                    (operation['operation_id'],)).fetchone()[0], table)
        for table in ('dossier_revisions', 's2_dossiers', 's2_revisions', 's2_actions', 's2_validations',
                      's2_qualifications', 's2_comparison_contracts', 'pieces', 'operations', 's7_archives'):
            self.assertEqual(0, self.store._connection.execute(
                f'SELECT count(*) FROM {table} WHERE dossier_id=?', ('erase',)).fetchone()[0], table)
        with self.assertRaises(privacy.Gone):
            self.record('erase', initial)
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        self.assertEqual([], privacy.purge(self.data)['purged'])

    def test_archive_and_corpus_keep_received_answers_while_judgment_is_pending(self):
        self.prepare('pending')
        self.qualify('pending')
        consent, _ = self.contribute('pending')
        cid, attempts, _ = self.campaign('pending')
        service._retention_worker(self.data, 'pending', self.acquire, attempts)
        manifest = archive.archive_manifest(self.store, self.sid, 'pending')
        pending = self.record('pending', manifest)
        models = pending['campaigns'][0]['models']
        self.assertEqual(2, len(models))
        self.assertTrue(all(row['verdict'] is None for row in models))
        self.assertTrue(all(row['answer'] == '  fictional raw output\n' for row in models))
        self.assertEqual(pending['campaigns'], self.corpus('pending')['campaigns'])
        self.stage = 'judgment'
        ids = auto.reserve_campaign(self.store, self.sid, 'pending', cid, self.judge)
        service._retention_worker(self.data, 'pending', auto.execute_campaign, self.data, ids, self.judge)
        self.assertEqual(2, len(restitution.comparison(self.store, self.sid, 'pending', cid)['rows']))
        completed = self.record('pending', archive.archive_manifest(self.store, self.sid, 'pending'))
        for destination, value in (('archive', completed), ('corpus', self.corpus('pending'))):
            with self.subTest(destination=destination):
                self.assertEqual(['NE SATISFAIT PAS'] * 2,
                                 [row['verdict'] for row in value['campaigns'][0]['models']])
        self.assertEqual(pending, self.record('pending', manifest))
        metadata = archive.contribution_view(self.store, self.sid, 'pending')['contribution']
        self.assertEqual(consent['contribution']['expires_at'], metadata['expires_at'])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def unknown_cost_survives_purge(self, phase):
        self.prepare('erase')
        self.qualify('erase')
        self.prepare('keep')
        cid, attempts, body = self.campaign('erase')
        if phase == 'candidate':
            self.unknown_candidate = True
        self.acquire(attempts)
        if phase == 'judgment':
            self.stage = 'judgment'
            self.unknown_judgment = True
            ids = auto.reserve_campaign(self.store, self.sid, 'erase', cid, self.judge)
            auto.execute_campaign(self.data, ids, self.judge)
            self.assertEqual(1, len(restitution.comparison(self.store, self.sid, 'erase', cid)['rows']))
            self.assertEqual('BLOCKED', auto.status(self.store, self.store._connection, cid)['status'])
        else:
            self.assertEqual(1, len(self.candidate_calls))
            self.assertEqual('INTENT_RECORDED', self.operation(attempts[1])['state'])
        operation = next(op for op in self.store.inspect_operations()
                         if op['observed_cost'] and op['observed_cost']['status'] == 'UNKNOWN')
        budgets = {bid: self.store.inspect_budget(bid) for bid in (self.budget, cid)}
        self.assertIn(operation['operation_id'], budgets[operation['budget_id']]['unknown_cost_operations'])
        before_calls = (len(self.candidate_calls), self.http.request.call_count)
        self.acquire(attempts)
        self.assertEqual(before_calls, (len(self.candidate_calls), self.http.request.call_count))
        manifest = archive.archive_manifest(self.store, self.sid, 'erase')
        self.assertTrue(any(row['answer'] for row in self.record('erase', manifest)['campaigns'][0]['models']))
        privacy.request_delete(self.store, self.sid, 'erase')
        self.assertEqual(['erase'], privacy.purge(self.data)['purged'])
        with closing(storage.Store(self.data)) as reopened:
            self.assertEqual(budgets, {bid: reopened.inspect_budget(bid) for bid in budgets})
            retired = next(op for op in privacy.retired_operations(reopened._connection)
                           if op['operation_id'] == operation['operation_id'])
            self.assertEqual('RECEIVED', retired['state'])
            self.assertEqual('UNKNOWN', retired['observed_cost']['status'])
            self.assertIsNone(retired['observed_cost']['amount'])
            self.assertTrue(reopened.verify_storage()['integrity_ok'])
            with self.assertRaises(privacy.Gone):
                campaigns.launch(reopened, self.sid, 'erase', cid, body,
                    access_secret=SECRET, access_transport=self.access)
            # Reusing a retired identity on a live dossier must also fail before emission
            replay = {key: operation[key] for key in storage._OPERATION_KEYS}
            replay.update(dossier_id='keep', revision=prep.view(reopened, self.sid, 'keep')['revision'])
            with self.assertRaisesRegex(storage.ConflictError, 'operation identity already exists'):
                reopened.reserve_intent(replay, operation['budget_id'], operation['reserved_amount'])
        self.assertEqual(before_calls, (len(self.candidate_calls), self.http.request.call_count))

    def test_unknown_candidate_cost_and_no_replay_survive_purge_and_reopen(self):
        self.unknown_cost_survives_purge('candidate')

    def test_unknown_judgment_cost_and_no_replay_survive_purge_and_reopen(self):
        self.unknown_cost_survives_purge('judgment')

    def deletion_during_worker(self, phase):
        self.prepare('erase')
        if phase == 'qualification':
            operation = self.reserve_qualification('erase')
            run = lambda: prep.execute_qualification(self.data, operation, self.qualifier)
        else:
            self.qualify('erase')
            cid, attempts, _ = self.campaign('erase')
            if phase == 'candidate':
                operation = attempts[0]
                run = lambda: execution.execute(self.data, operation, self.candidate,
                    access_secret=SECRET, access_transport=self.access)
                next_call = lambda: execution.execute(self.data, attempts[1], self.candidate,
                    access_secret=SECRET, access_transport=self.access)
            else:
                self.acquire(attempts)
                self.stage = 'judgment'
                ids = auto.reserve_campaign(self.store, self.sid, 'erase', cid, self.judge)
                operation = ids[0]
                run = lambda: judgment.execute(self.data, operation, self.judge)
                next_call = lambda: judgment.execute(self.data, ids[1], self.judge)
        entered, release = threading.Event(), threading.Event()
        errors = []
        def boundary():
            entered.set()
            if not release.wait(10):
                raise TimeoutError('Reçu synthétique non libéré')
        def worker():
            try:
                run()
            except BaseException as error:
                errors.append(error)
        self.boundary = boundary
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(10))
            self.assertEqual('EMISSION_POSSIBLE', self.operation(operation)['state'])
            privacy.request_delete(self.store, self.sid, 'erase')
            self.assertEqual({'purged': [], 'pending': True}, privacy.purge(self.data))
            with self.assertRaises(privacy.Gone):
                prep.view(self.store, self.sid, 'erase')
        finally:
            release.set()
            thread.join(10)
            self.boundary = lambda: None
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual('RECEIVED', self.operation(operation)['state'])
        self.assertIsNotNone(self.operation(operation)['receipt'])
        before_calls = (len(self.candidate_calls), self.http.request.call_count)
        if phase != 'qualification':
            with self.assertRaises(privacy.Gone):
                next_call()
        else:
            with self.assertRaises(privacy.Gone):
                self.reserve_qualification('erase')
        self.assertEqual(before_calls, (len(self.candidate_calls), self.http.request.call_count))
        before = self.store.inspect_budget(self.operation(operation)['budget_id'])
        self.assertEqual(['erase'], privacy.purge(self.data)['purged'])
        self.assertEqual(before, self.store.inspect_budget(before['budget_id']))
        self.assertEqual('PURGED', self.store._connection.execute(
            'SELECT state FROM s7_dossiers WHERE dossier_id=?', ('erase',)).fetchone()[0])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_delete_during_qualification_retains_receipt_then_purges(self):
        self.deletion_during_worker('qualification')

    def test_delete_during_candidate_retains_receipt_and_blocks_next_emission(self):
        self.deletion_during_worker('candidate')

    def test_delete_during_judgment_retains_receipt_and_blocks_next_emission(self):
        self.deletion_during_worker('judgment')

    def test_operator_funded_s6_campaign_is_excluded_from_automatic_purge(self):
        from tests.test_campaign_launch import CampaignLaunch
        legacy = CampaignLaunch()
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        from benchmark import evaluation
        evaluation.initialize(legacy.data)
        provider_access.initialize(legacy.data)
        privacy.migrate(legacy.data, SECRET, 'operator-exclusion', now=NOW)
        before = legacy.store.inspect_operations()
        snapshot = campaigns.inspect(legacy.store, 'local-comparison')
        privacy.request_delete(legacy.store, legacy.sid, 'fixture')
        report = privacy.purge(legacy.data)
        self.assertEqual([], report['purged'])
        self.assertEqual(['fixture'], report['operator_review'])
        self.assertTrue(report['pending'])
        self.assertEqual(before, legacy.store.inspect_operations())
        self.assertEqual(snapshot, campaigns.inspect(legacy.store, 'local-comparison'))
        self.assertTrue(legacy.store.verify_storage()['integrity_ok'])
