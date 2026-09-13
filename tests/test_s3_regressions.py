"""S3 persistence and admission regressions, using only fictional local inputs"""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from benchmark import preparation as prep, qualification as q, storage
from tests.test_s2_review_regressions import response_for

ACTOR = 'responsable-fictif-S3'
AUTHORITY = {'actor': ACTOR, 'authority_id': 'TEST_ONLY_APPROVAL_S3'}


def fixture(data):
    storage.initialize(data)
    storage.initialize_preparation(data)
    with closing(storage.Store(data)) as store:
        store.create_budget('fictional', '100', 'TEST')
        prep.admit(store, dict(authority_id='TEST_ONLY_PREPARATION_S3', budget_id='fictional',
                               reserve_amount='7', requested_configuration={'model': 'fictional'}))
        session, _, _ = prep.session(store, None, create=True)
        op, _ = prep.submit(store, session, 'fixture', {'action_id': 'create', 'request': 'Organiser des notes fictives'}, 'a' * 40, True)
    prep.execute(data, op, lambda op, request: response_for(op))
    with closing(storage.Store(data)) as store:
        view = prep.view(store, session, 'fixture')
        prep.validate(store, session, 'fixture', prep.binding('fixture', view['revision'], view['package_sha256']))
        reference = store._connection.execute("SELECT piece_id FROM pieces WHERE role='judge'").fetchone()[0]
    return session, view, reference


def specification(reference):
    return dict(result_expected='Retrouver une action dans les notes fictives',
        obligations=[dict(id='O1', description='Action présente', use='Suivre les actions',
                          tolerance='Reformulation permise', control_ids=['source'])],
        eliminatory_errors=[dict(id='E1', description='Action omise', control_ids=['defect'])],
        reference_piece_ids=[reference],
        method=dict(id='fictional-note', version='1', control_ids=['source', 'defect'],
                    expected_evidence='Texte effectivement conservé', responsible_role='responsable de campagne'),
        witnesses={'defect': 'Omission fictive'}, secondary_criteria=[], aggregation=None,
        local_criterion_ids=[],
        cost_basis=dict(scope='Par tentative, assistance séparée', attempts='Tentatives autorisées', unit='TEST', conversion=None),
        exposure='Pièce réservée ; test entièrement fictif', professional_review='ABSENTE',
        limits=['Contrôle local fictif des notes seulement'])


def check(contract, resources):
    facts = resources[contract['package']['pieces'][0]['id']]
    reference = resources[contract['reference_pieces'][0]['id']]
    return dict(checks=[dict(control_id='source', status='PASS' if b'relire' in facts and b'Attendu fictif' in reference else 'FAIL',
                             finding='Lecture de la note et de la référence fictives',
                             proof={'facts': facts.decode(), 'reference': reference.decode()}),
                        dict(control_id='defect', status='PASS', finding='Omission témoin repérée',
                             proof={'witness': contract['specification']['witnesses']['defect']})],
                limits=contract['specification']['limits'], professional_review='ABSENTE', assistance=None, disagreements=[])


class S3Regressions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s3-reg-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name).resolve() / 'private'
        self.session, self.view, self.reference = fixture(self.data)
        q.initialize(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        self.spec = specification(self.reference)
        self.candidate = q.draft(self.store, 'fixture', self.view['revision'], self.spec)
        self.fingerprint = self.candidate['contract_sha256']

    def test_new_contract_requires_explicit_local_criteria(self):
        spec = dict(self.spec)
        spec.pop('local_criterion_ids')
        with self.assertRaisesRegex(ValueError, 'preuve locale explicites'):
            q.draft(self.store, 'fixture', self.view['revision'], spec)
        q._specification(spec, legacy=True)

    def qualify(self, checker=check):
        return q.qualify(self.store, self.fingerprint, reviewer=ACTOR, check=checker)

    def approve(self, receipt):
        return q.approve(self.store, self.fingerprint, receipt['qualification_id'], actor=ACTOR, authority=AUTHORITY)

    def test_later_blocked_review_prevents_approval_with_earlier_pass(self):
        earlier = self.qualify()
        def blocked(contract, resources):
            review = check(contract, resources)
            review['checks'][0]['status'] = 'INDETERMINE'
            return review
        self.assertEqual('BLOCKED', self.qualify(blocked)['status'])
        with self.assertRaises(ValueError):
            self.approve(earlier)
        self.assertEqual('BLOCKED', prep.view(self.store, self.session, 'fixture')['qualification']['status'])

    def test_approved_contract_and_evidence_reject_sql_update_delete_and_replace(self):
        self.approve(self.qualify())
        frozen = q.inspect_contract(self.store, self.fingerprint)
        connection = self.store._connection
        for table in ('s3_contracts', 's3_qualifications', 's3_approvals'):
            with self.subTest(table=table):
                rows = connection.execute('SELECT * FROM ' + table).fetchall()
                columns = [r[1] for r in connection.execute('PRAGMA table_info(' + table + ')')]
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f'UPDATE {table} SET {columns[0]}={columns[0]}')
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute('DELETE FROM ' + table)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute('INSERT OR REPLACE INTO ' + table + ' VALUES (' + ','.join('?' for _ in columns) + ')', rows[0])
        with self.assertRaises(ValueError):
            self.qualify()
        self.assertEqual(frozen, q.inspect_contract(self.store, self.fingerprint))

    def test_callback_mutations_do_not_change_frozen_input(self):
        original = deepcopy(self.candidate['contract'])
        def mutate(contract, resources):
            receipt = check(contract, resources)
            contract['specification']['method']['control_ids'].clear()
            contract['package']['instruction'] = 'Mutation sans autorité'
            resources.clear()
            return receipt
        self.assertEqual('QUALIFIED', self.qualify(mutate)['status'])
        self.assertEqual(original, q.inspect_contract(self.store, self.fingerprint)['contract'])

    def test_changed_bytes_during_callback_roll_back_evidence(self):
        path = self.data / self.store.get_piece(self.reference)['relative_path']
        def tamper(contract, resources):
            receipt = check(contract, resources)
            path.write_bytes(b'Alteration fictive')
            return receipt
        with self.assertRaises(ValueError):
            self.qualify(tamper)
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s3_qualifications').fetchone()[0])
        view = prep.view(self.store, self.session, 'fixture')
        self.assertFalse(view['qualified'])
        self.assertEqual('BLOCKED', view['qualification']['status'])
        self.assertNotIn(self.reference, json.dumps(view))

    def test_pending_correction_blocks_old_approval_and_visible_eligibility(self):
        receipt = self.qualify()
        self.approve(receipt)
        frozen = q.inspect_contract(self.store, self.fingerprint)
        prep.submit(self.store, self.session, 'fixture', dict(action_id='edit', revision=self.view['revision'],
                    kind='correct', message='Modifier les notes fictives'), 'a' * 40, True)
        with self.assertRaises(ValueError):
            self.approve(receipt)
        view = prep.view(self.store, self.session, 'fixture')
        self.assertIsNone(view['validation'])
        self.assertFalse(view['qualified'])
        self.assertEqual(frozen, q.inspect_contract(self.store, self.fingerprint))

    def test_incomplete_review_and_boolean_proof_cannot_qualify(self):
        for defect in ('boolean', 'unresolved', 'undeclared', 'no-attribution', 'lost-limit', 'review-mismatch'):
            def invalid(contract, resources):
                review = check(contract, resources)
                if defect == 'boolean':
                    review['checks'][0]['proof'] = True
                elif defect == 'unresolved':
                    review['disagreements'] = [{'finding': 'Désaccord fictif sans arbitrage'}]
                elif defect == 'undeclared':
                    review['checks'][0]['control_id'] = 'not-declared'
                elif defect == 'lost-limit':
                    review['limits'] = []
                elif defect == 'review-mismatch':
                    review['professional_review'] = dict(author=ACTOR, phase='fictive', scope='fictif', proof='non déclarée')
                else:
                    review.pop('professional_review')
                return review
            with self.subTest(defect=defect):
                try:
                    receipt = self.qualify(invalid)
                except ValueError:
                    continue
                self.assertEqual('BLOCKED', receipt['status'])
                with self.assertRaises(ValueError):
                    self.approve(receipt)

    def test_failed_callback_leaves_no_evidence_or_operation(self):
        before = self.store.inspect_operations()
        def fail(contract, resources):
            raise RuntimeError('Échec local fictif')
        with self.assertRaises(RuntimeError):
            self.qualify(fail)
        snapshot = q.inspect_contract(self.store, self.fingerprint)
        self.assertEqual([], snapshot['qualifications'])
        self.assertIsNone(snapshot['approval'])
        self.assertEqual(before, self.store.inspect_operations())

    def test_storage_open_does_not_initialize_extension(self):
        with tempfile.TemporaryDirectory(prefix='s3-no-migration-') as directory:
            data = Path(directory).resolve() / 'private'
            session, view, reference = fixture(data)
            with closing(storage.Store(data)) as store:
                tables = store._connection.execute("SELECT name FROM sqlite_schema").fetchall()
                with self.assertRaises(storage.SchemaError):
                    q.draft(store, 'fixture', view['revision'], specification(reference))
                prep.view(store, session, 'fixture')
                self.assertEqual(tables, store._connection.execute("SELECT name FROM sqlite_schema").fetchall())

    def test_another_writer_cannot_change_revision_during_qualification(self):
        def competing(contract, resources):
            with closing(storage.Store(self.data)) as other:
                other._connection.execute('PRAGMA busy_timeout=0')
                with self.assertRaises(sqlite3.OperationalError):
                    prep.submit(other, self.session, 'fixture', dict(action_id='race', revision=self.view['revision'],
                                kind='correct', message='Correction fictive concurrente'), 'a' * 40, True)
            return check(contract, resources)
        self.approve(self.qualify(competing))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])


if __name__ == '__main__':
    unittest.main()
