"""Fictional S5 checks independent of reports and their frozen judge"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from benchmark import campaigns as c, evaluation as e, preparation as prep, qualification as q, runtime, storage
from tests.test_s3_regressions import ACTOR, AUTHORITY, check, fixture, specification
from tests.test_s4_regressions import inputs, manifest, response

RESPONSIBLE = 'responsable-fictif-S5'
EVALUATION_AUTHORITY = {'actor': RESPONSIBLE, 'authority_id': 'TEST_ONLY_EVALUATION_S5'}


def findings(ctx, resources):
    proofs = [dict(piece_id=pid, sha256=sha256(raw).hexdigest(), passage=raw.decode())
              for pid, raw in resources.items()]
    spec = ctx['qualification']['contract']['specification']
    return dict(findings=[dict(criterion_id=criterion['id'], control_id=control,
                              status='PASS', attribution='evidence', finding='Constat fictif sur les pièces conservées', evidence=proofs)
                         for criterion in spec['obligations'] + spec['eliminatory_errors'] for control in criterion['control_ids']],
                measures=[], judgment=dict(mode='local', instructions='Lire les notes fictives et leur sortie',
                    resources_seen=list(resources), assistance_operation_id=None, model_links='INCONNU',
                    disagreements=[], professional_review='ABSENTE'), limits=['Test logiciel fictif'])


class S5Regressions(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='s5-reg-')
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.data = self.home / 'private'
        self.session, self.view, self.reference = fixture(self.data)
        q.initialize(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        candidate = q.draft(self.store, 'fixture', self.view['revision'], specification(self.reference))
        qualified = q.qualify(self.store, candidate['contract_sha256'], reviewer=ACTOR, check=check)
        q.approve(self.store, candidate['contract_sha256'], qualified['qualification_id'], actor=ACTOR, authority=AUTHORITY)
        prep.close_admission(self.store)
        c.initialize(self.data)
        self.store.create_budget('local-comparison', '40', 'TEST')
        self.campaign = c.create(self.store, manifest(candidate))
        c.admit(self.store, 'local-comparison', *inputs(self.campaign))
        c.reserve(self.store, 'local-comparison', 'x', 'intent-x')
        e.initialize(self.data)

    def acquire(self):
        c.execute(self.data, 'intent-x', response)

    def evaluate(self, callback=findings, **kwargs):
        return e.evaluate(self.store, 'local-comparison', 'intent-x', responsible=RESPONSIBLE,
                          authority=EVALUATION_AUTHORITY, check=callback, **kwargs)

    def test_initializers_are_idempotent_and_unknown_schema_is_refused(self):
        self.acquire()
        record = self.evaluate()
        before = self.store._connection.execute('SELECT * FROM sqlite_schema').fetchall()
        for initialize in (storage.initialize_preparation, q.initialize, c.initialize, e.initialize):
            initialize(self.data)
        self.assertEqual(before, self.store._connection.execute('SELECT * FROM sqlite_schema').fetchall())
        self.assertEqual(record, e.inspect(self.store, record['evaluation_id']))
        self.store._connection.execute('CREATE TABLE future_evaluator (id TEXT)')
        with self.assertRaises(storage.SchemaError):
            e.initialize(self.data)

    def test_insert_replace_update_delete_cannot_rewrite_evaluation(self):
        self.acquire()
        self.evaluate()
        connection = self.store._connection
        for table in ('s5_control', 's5_evaluations'):
            columns = [r[1] for r in connection.execute('PRAGMA table_info(' + table + ')')]
            row = connection.execute('SELECT * FROM ' + table).fetchone()
            with self.subTest(table=table):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f'UPDATE {table} SET {columns[0]}={columns[0]}')
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute('DELETE FROM ' + table)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute('INSERT OR REPLACE INTO ' + table + ' VALUES (' + ','.join('?' for _ in columns) + ')', row)
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_earlier_intent_evaluation_survives_later_acquisition_and_correction(self):
        first = self.evaluate()
        self.assertEqual('INDETERMINE', first['verdict'])
        self.assertIsNone(first['output_piece_id'])
        self.acquire()
        self.assertEqual(first, e.inspect(self.store, first['evaluation_id']))
        second = self.evaluate(previous_evaluation_id=first['evaluation_id'])
        self.assertEqual('SATISFAIT', second['verdict'])
        self.assertEqual(first['evaluation_id'], second['previous_evaluation_id'])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_competing_corrections_keep_one_successor(self):
        self.acquire()
        first = self.evaluate()
        barrier = threading.Barrier(2)
        def correct():
            with closing(storage.Store(self.data)) as store:
                barrier.wait(timeout=5)
                try:
                    e.evaluate(store, 'local-comparison', 'intent-x', responsible=RESPONSIBLE,
                               authority=EVALUATION_AUTHORITY, check=findings, previous_evaluation_id=first['evaluation_id'])
                    return 'SAVED'
                except storage.ConflictError:
                    return 'CONFLICT'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(correct) for _ in range(2)]
            self.assertEqual(['CONFLICT', 'SAVED'], sorted(f.result() for f in futures))
        self.assertEqual(2, self.store._connection.execute('SELECT count(*) FROM s5_evaluations').fetchone()[0])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_callback_mutation_of_source_bytes_leaves_no_evaluation(self):
        self.acquire()
        def mutate(ctx, resources):
            report = findings(ctx, resources)
            pid = ctx['attempt']['output_piece_id']
            (self.data / self.store.get_piece(pid)['relative_path']).write_bytes(b'changed')
            return report
        with self.assertRaises(storage.IntegrityError):
            self.evaluate(mutate)
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s5_evaluations').fetchone()[0])

    def test_fabricated_passage_or_reference_only_defect_is_not_a_candidate_failure(self):
        self.acquire()
        def forged(ctx, resources):
            report = findings(ctx, resources)
            report['findings'][0]['evidence'][0]['passage'] = 'Un passage absent des octets'
            return report
        with self.assertRaises(storage.IntegrityError):
            self.evaluate(forged)
        def reference_only(ctx, resources):
            report = findings(ctx, resources)
            report['findings'][0].update(status='FAIL', attribution='reference')
            return report
        record = self.evaluate(reference_only)
        self.assertEqual('INDETERMINE', record['verdict'])

    def test_missing_control_and_unresolved_disagreement_preserve_independent_defect(self):
        self.acquire()
        def defective(ctx, resources):
            report = findings(ctx, resources)
            report['findings'][0].update(status='FAIL', attribution='candidate', finding='Défaut candidat fictif indépendant')
            report['findings'].pop()
            report['judgment']['disagreements'] = [dict(finding='Autre contrôle contesté', arbitration=None)]
            return report
        record = self.evaluate(defective)
        self.assertEqual('NE SATISFAIT PAS', record['verdict'])
        self.assertEqual('INDETERMINE', record['findings'][-1]['status'])
        self.assertTrue(record['findings'][0]['evidence'])

    def test_contradictory_control_does_not_establish_candidate_defect(self):
        self.acquire()
        def disputed(ctx, resources):
            report = findings(ctx, resources)
            report['findings'].append(dict(report['findings'][0], status='FAIL', attribution='candidate'))
            return report
        self.assertEqual('INDETERMINE', self.evaluate(disputed)['verdict'])

    def test_passing_findings_without_output_evidence_name_the_missing_proof(self):
        self.acquire()
        def unproven(ctx, resources):
            report = findings(ctx, resources)
            for finding in report['findings']:
                finding['evidence'] = [proof for proof in finding['evidence'] if proof['piece_id'] != ctx['attempt']['output_piece_id']]
            return report
        record = self.evaluate(unproven)
        self.assertEqual('INDETERMINE', record['verdict'])
        self.assertIn('O1', record['reason'])
        self.assertIn('E1', record['reason'])

    def test_piece_route_refuses_unlinked_judge_piece_even_in_same_dossier(self):
        self.acquire()
        record = self.evaluate()
        self.store.put_piece('fixture', self.view['revision'], 'unrelated-judge', name='Sans lien',
                             role='judge', media_type='text/plain', content=b'Unrelated private proof')
        with self.assertRaises(prep.Denied):
            e.piece_bytes(self.store, self.session, 'fixture', record['evaluation_id'], 'unrelated-judge')
        self.assertEqual(b'  fictional raw output\n', e.piece_bytes(self.store, self.session, 'fixture', record['evaluation_id'], record['output_piece_id']))

    def test_worker_lock_prevents_backup_during_callback(self):
        self.acquire()
        c.stop(self.store, 'local-comparison')
        def inspecting(ctx, resources):
            with self.assertRaises(BlockingIOError):
                runtime.backup(self.data, self.home / 'during')
            return findings(ctx, resources)
        self.evaluate(inspecting)
        self.assertFalse((self.home / 'during').exists())
        runtime.backup(self.data, self.home / 'after')
        self.assertEqual('BACKUP_VERIFIED', runtime.verify_backup(self.home / 'after')['state'])

    def test_new_dossier_revision_keeps_old_verdict_and_qualification_readable(self):
        from tests.test_s2_review_regressions import response_for
        self.acquire()
        first = self.evaluate()
        prep.admit(self.store, dict(authority_id='TEST_ONLY_PREPARATION_S3', budget_id='fictional',
                                   reserve_amount='7', requested_configuration={'model': 'fictional'}))
        oid, _ = prep.submit(self.store, self.session, 'fixture', dict(action_id='new-revision',
            revision=self.view['revision'], kind='correct', message='Modifier les notes fictives'), 'a' * 40, True)
        prep.execute(self.data, oid, lambda op, request: response_for(op))
        self.assertEqual(first, e.inspect(self.store, first['evaluation_id']))
        view = prep.view(self.store, self.session, 'fixture')
        self.assertGreater(view['revision'], self.view['revision'])
        visible = view['campaigns'][0]['evaluations'][0]
        self.assertEqual(first['qualification_id'], visible['qualification']['approval']['qualification_id'])
        self.assertEqual('SATISFAIT', self.evaluate(previous_evaluation_id=first['evaluation_id'])['verdict'])

    def test_incident_with_output_does_not_erase_an_independent_candidate_defect(self):
        def transport(op, request):
            result = response(op, request)
            result['receipt']['result']['incident'] = 'Fictional provider warning after output'
            return result
        c.execute(self.data, 'intent-x', transport)
        def defective(ctx, resources):
            report = findings(ctx, resources)
            report['findings'][0].update(status='FAIL', attribution='candidate')
            return report
        record = self.evaluate(defective)
        self.assertEqual('NE SATISFAIT PAS', record['verdict'])
        self.assertIsNotNone(record['incident'])

    def test_empty_raw_output_is_evidence_and_is_distinct_from_absence(self):
        def transport(op, request):
            result = response(op, request)
            result['receipt']['result']['output'] = ''
            return result
        c.execute(self.data, 'intent-x', transport)
        def empty(ctx, resources):
            report = findings(ctx, resources)
            report['findings'][0].update(status='FAIL', attribution='candidate', finding='Sortie fictive vide, action absente')
            return report
        record = self.evaluate(empty)
        self.assertEqual('NE SATISFAIT PAS', record['verdict'])
        self.assertEqual(sha256(b'').hexdigest(), record['output_sha256'])
        self.assertIsNotNone(record['output_piece_id'])
        self.assertEqual(record, e.inspect(self.store, record['evaluation_id']))

    def test_unreceived_assistance_retains_intention_then_requires_explicit_correction(self):
        self.acquire()
        ctx = dict(campaign=c.inspect(self.store, 'local-comparison'),
                   qualification=q.inspect_contract(self.store, self.campaign['manifest']['contract_sha256']))
        ctx['attempt'] = ctx['campaign']['attempts'][0]
        resources = e._resources(self.store, ctx)
        operation = dict(operation_id='fictitious-judge', phase='judgment', dossier_id='fixture', revision=self.view['revision'],
            authority='TEST_ONLY_JUDGMENT_S5', engine_version='fictional-judge/v1', requested_configuration={'model': 'fictional', 'effort': 'high'},
            resources=[storage._strict_json(dict(instructions='Lire les notes fictives et leur sortie', context_sha256=q.digest(ctx), piece_ids=list(resources))), *resources])
        self.store.create_budget('judgment', '10', 'TEST')
        self.store.reserve_intent(operation, 'judgment', '7')
        self.store.mark_emission_possible(operation['operation_id'])
        self.store.mark_ambiguous(operation['operation_id'], 'Fictional interruption')
        def assisted(ctx, resources):
            report = findings(ctx, resources)
            report['judgment'].update(mode='assisted', assistance_operation_id='fictitious-judge')
            return report
        first = self.evaluate(assisted)
        self.assertEqual('INDETERMINE', first['verdict'])
        self.assertEqual('7', self.store.inspect_budget('judgment')['reserved'])
        self.assertIsNone(first['judgment']['cost']['amount'])
        self.store.record_receipt('fictitious-judge', dict(receipt_id='late', observed_configuration={'model': 'fictional', 'effort': None},
            resources_seen=list(resources), result={'finding': 'Late fictional receipt'}),
            dict(status='KNOWN', amount='4', currency='TEST', source='Fictional late receipt'))
        self.assertEqual(first, e.inspect(self.store, first['evaluation_id']))
        # Inspection never retries or settles a previously unknown cost retroactively
        self.assertEqual(3, len(self.store.inspect_operations()))
        self.assertEqual('4', self.store.inspect_budget('judgment')['spent'])
        second = self.evaluate(assisted, previous_evaluation_id=first['evaluation_id'])
        self.assertEqual('SATISFAIT', second['verdict'])
        self.assertEqual('INCONNU', second['judgment']['observed_configuration']['effort'])
        self.assertEqual('4', second['judgment']['cost']['amount'])
        self.assertEqual(first, e.inspect(self.store, first['evaluation_id']))


if __name__ == '__main__':
    unittest.main()
