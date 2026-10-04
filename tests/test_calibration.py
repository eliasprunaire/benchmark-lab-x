"""Étalonnage du jugement : lot fictif annoté, reçus conservés, aucun appel fournisseur"""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import stat
import unittest
from unittest.mock import patch

from benchmark import calibration, evaluation, judgment, runtime, storage
from benchmark.validation import digest
from tests import test_s14_acceptance as acceptance

AUTHORS = ('lectrice-a', 'lecteur-b')
RULE_ORIGIN = 'TEST_ONLY_SYNTHETIC_RULE'


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))
        self.h = acceptance.S14Acceptance()
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        h = self.h
        review = evaluation.prepare_review(h.store, 'local-comparison', 'intent-x')['content']
        self.output = review['output']
        self.proofs = deepcopy(h.answer['findings'][0]['evidence'])
        self.judge('j-agree')
        self.judge('j-reject', source='FAIL')
        self.judge('j-accept')
        self.judge('j-indet', source='INDETERMINE')
        self.judge('j-disagree')
        self.judge('j-bind')
        self.judge('j-unusable', unusable=True)
        judgment.reserve(h.store, h.request('j-pending'), h.transport)
        self.calls = h.http.request.call_count
        self.operations = deepcopy(h.store.inspect_operations())

    def judge(self, oid, unusable=False, **statuses):
        h = self.h
        for finding in h.answer['findings']:
            status = statuses.get(finding['control_id'], 'PASS')
            only_output = [p for p in self.proofs if p['piece_id'] == self.output['piece_id']]
            finding.update(status=status, attribution='candidate', finding=f'Constat {status} fictif',
                           evidence=[] if status == 'INDETERMINE' else only_output if status == 'FAIL' else self.proofs)
        h.set_response()
        if unusable:
            document = json.loads(h.http.getresponse.return_value.read.return_value)
            document['choices'][0]['message']['content'] = ''
            h.http.getresponse.return_value.read.return_value = storage._strict_json(document).encode()
        h.execute(oid)

    def item(self, oid, source='PASS', defect='PASS', dossier='fixture', output=None):
        def notes(control, status):
            statuses = status if type(status) is tuple else (status,) * len(AUTHORS)
            return [dict(author=author, control_id=control, status=value, justification=f'{author} lit {control}')
                    for author, value in zip(AUTHORS, statuses)]
        return dict(operation_id=oid, dossier_id=dossier, output_sha256=output or self.output['sha256'],
                    annotations=notes('source', source) + notes('defect', defect), arbitrations=[])

    def lot(self, items, **changes):
        batch = dict(format=calibration.FORMAT, batch_id='lot-fictif',
                     judge_provenance=dict(kind='simulated', authority=None),
                     disagreement_rule=dict(origin=RULE_ORIGIN, treatment='exclude', min_annotators=2),
                     separation=None, correction=['autre-dossier'], control=items)
        batch.update(changes)
        batch.setdefault('reserved_sha256', digest(batch['control']))
        return batch

    def full_lot(self, **changes):
        return self.lot([
            self.item('j-agree'),
            self.item('j-reject', source='PASS'),
            self.item('j-accept', defect='FAIL'),
            self.item('j-indet'),
            self.item('j-disagree', source=('PASS', 'FAIL')),
            self.item('j-bind', output='0' * 64),
            self.item('j-unusable'),
            self.item('j-pending'),
            self.item('absente'),
            self.item('ghost-op', dossier='ghost')], correction=['ghost'], **changes)

    def requirement(self, report, criterion):
        group, = report['judges']
        return next(row for row in group['requirements'] if row['criterion_id'] == criterion)

    def test_report_reads_false_rejections_false_acceptances_and_undecided(self):
        report = calibration.report(self.h.store, self.full_lot())
        group, = report['judges']
        self.assertEqual('benchmark-lab-x/judgment/v1', group['judge']['engine_version'])
        self.assertEqual(dict(id='fictional-note', version='1'), group['judge']['method'])
        self.assertEqual(self.h.profile['revision'], group['judge']['revision'])
        o1 = self.requirement(report, 'O1')
        self.assertEqual(['source'], o1['control_ids'])
        self.assertEqual(dict(accord=2, faux_rejet=1, acceptation_erronee=0, indetermine=2,
                              decision_sur_reference_indeterminee=0), o1['counts'])
        self.assertEqual(5, o1['measured_pairs'])
        e1 = self.requirement(report, 'E1')
        self.assertEqual(dict(accord=4, faux_rejet=0, acceptation_erronee=1, indetermine=1,
                              decision_sur_reference_indeterminee=0), e1['counts'])
        rejected, = o1['examples']['faux_rejet']
        self.assertEqual(('j-reject', 'fixture', 'source'), (rejected['operation_id'], rejected['dossier_id'], rejected['control_id']))
        self.assertEqual('PASS', rejected['reference']['status'])
        self.assertEqual(sorted(AUTHORS), sorted(row['author'] for row in rejected['reference']['annotations']))
        self.assertTrue(all(row['justification'] for row in rejected['reference']['annotations']))
        self.assertEqual('FAIL', rejected['judge']['status'])
        self.assertEqual(['Constat FAIL fictif'], rejected['judge']['findings'])
        accepted, = e1['examples']['acceptation_erronee']
        self.assertEqual(('j-accept', 'FAIL', 'PASS'), (accepted['operation_id'], accepted['reference']['status'], accepted['judge']['status']))
        undecided = {row['operation_id']: row for row in o1['examples']['indetermine']}
        self.assertEqual({'j-indet', 'j-unusable'}, set(undecided))
        self.assertIsNone(undecided['j-indet']['judge']['unusable'])
        self.assertIsNotNone(undecided['j-unusable']['judge']['unusable'])
        self.assertEqual(self.output['sha256'], rejected['output_sha256'])

    def test_coverage_names_every_exclusion_and_contamination(self):
        report = calibration.report(self.h.store, self.full_lot())
        coverage = report['coverage']
        self.assertEqual((1, 10, 6, 11), (coverage['correction_dossiers'], coverage['control_items'],
                                           coverage['measured_items'], coverage['measured_pairs']))
        reasons = {(row['operation_id'], row['control_id']): row['reason'] for row in coverage['excluded']}
        self.assertEqual({('j-disagree', 'source'): 'DESACCORD_NON_ARBITRE', ('j-bind', None): 'LIAISON_DIVERGENTE',
                          ('j-pending', None): 'SANS_RECU', ('absente', None): 'OPERATION_INCONNUE',
                          ('ghost-op', None): 'CONTAMINATION'}, reasons)
        self.assertEqual(['ghost'], report['contamination'])
        disagreement, = report['disagreements']
        self.assertEqual(('j-disagree', 'source', 'EXCLU'), (disagreement['operation_id'], disagreement['control_id'], disagreement['outcome']))
        self.assertEqual({'PASS', 'FAIL'}, {row['status'] for row in disagreement['annotations']})

    def test_contaminated_real_dossier_leaves_nothing_measured(self):
        report = calibration.report(self.h.store, self.lot(
            [self.item('j-agree'), self.item('j-reject')], correction=['fixture']))
        self.assertEqual(['fixture'], report['contamination'])
        self.assertEqual(0, report['coverage']['measured_pairs'])
        self.assertEqual([], report['judges'])
        self.assertEqual({'CONTAMINATION'}, {row['reason'] for row in report['coverage']['excluded']})

    def test_report_names_method_reference_judge_and_refuses_agreement_as_guarantee(self):
        report = calibration.report(self.h.store, self.full_lot())
        self.assertEqual('lot-fictif', report['batch_id'])
        self.assertEqual(digest(self.full_lot()), report['batch_sha256'])
        self.assertEqual(sorted(AUTHORS), report['reference']['annotators'])
        self.assertEqual(RULE_ORIGIN, report['reference']['disagreement_rule']['origin'])
        self.assertEqual('simulated', report['judge_provenance']['kind'])
        self.assertEqual(digest(self.full_lot()['control']), report['separation']['reserved_sha256'])
        self.assertEqual('NON_ETABLIE', report['separation']['anteriority'])
        def keys(value):
            if type(value) is dict:
                return set(value) | {k for child in value.values() for k in keys(child)}
            return {k for child in value for k in keys(child)} if type(value) is list else set()
        self.assertFalse({'rate', 'taux', 'percent', 'threshold', 'qualified'} & keys(report))
        text = json.dumps(report, ensure_ascii=False)
        self.assertIn('ne garantit pas', text)
        self.assertIn('Aucun seuil', text)
        proven = calibration.report(self.h.store, self.full_lot(separation=dict(source='Registre daté des lots', dated='2026-10-01')))
        self.assertEqual('DECLAREE', proven['separation']['anteriority'])
        self.assertIn('ne prouve pas', json.dumps(proven, ensure_ascii=False))

    def test_reserved_batch_must_match_its_declared_fingerprint(self):
        batch = self.full_lot()
        batch['control'][0]['annotations'][0]['status'] = 'FAIL'
        with self.assertRaises(storage.IntegrityError):
            calibration.report(self.h.store, batch)

    def test_disagreement_rule_is_explicit_and_arbitration_is_not_a_consensus(self):
        batch = self.full_lot()
        del batch['disagreement_rule']
        with self.assertRaises(ValueError):
            calibration.report(self.h.store, batch)
        items = [self.item('j-disagree', source=('PASS', 'FAIL'))]
        items[0]['arbitrations'] = [dict(control_id='source', arbiter='arbitre-c', status='FAIL', justification='Passage absent')]
        rule = dict(origin=RULE_ORIGIN, treatment='arbitrated', min_annotators=2)
        report = calibration.report(self.h.store, self.lot(items, disagreement_rule=rule))
        row, = report['disagreements']
        self.assertEqual('ARBITRE', row['outcome'])
        self.assertEqual(dict(author='arbitre-c', status='FAIL', justification='Passage absent'), row['arbitration'])
        o1 = self.requirement(report, 'O1')
        self.assertEqual((0, 1), (o1['counts']['accord'], o1['counts']['acceptation_erronee']))
        self.assertEqual('FAIL', o1['examples']['acceptation_erronee'][0]['reference']['status'])
        self.assertEqual('arbitrated', o1['examples']['acceptation_erronee'][0]['reference']['resolution'])
        self.assertEqual(1, o1['arbitrated_pairs'])
        # Même désaccord sous la règle d'exclusion : aucun arbitrage n'est lu
        items[0]['arbitrations'] = []
        strict = calibration.report(self.h.store, self.lot(items))
        self.assertEqual('DESACCORD_NON_ARBITRE', strict['coverage']['excluded'][0]['reason'])
        # Sous-annoté : moins d'annotateurs que la règle ne l'exige
        single = self.item('j-agree')
        single['annotations'] = [row for row in single['annotations'] if row['author'] == AUTHORS[0]]
        report = calibration.report(self.h.store, self.lot([single]))
        self.assertEqual({'SOUS_ANNOTE'}, {row['reason'] for row in report['coverage']['excluded']})

    def test_malformed_batches_are_refused_before_any_receipt_is_read(self):
        def twice(batch):
            batch['control'].append(deepcopy(batch['control'][0]))
        def silent_real(batch):
            batch['judge_provenance'] = dict(kind='authorized-real', authority=None)
        def unknown_treatment(batch):
            batch['disagreement_rule']['treatment'] = 'majority'
        def nobody(batch):
            batch['disagreement_rule']['min_annotators'] = 0
        def undated(batch):
            batch['separation'] = dict(source='Registre', dated='hier')
        def repeated_author(batch):
            batch['control'][0]['annotations'].append(deepcopy(batch['control'][0]['annotations'][0]))
        def test_rule_on_real(batch):
            batch['judge_provenance'] = dict(kind='authorized-real', authority='Décision documentée')
        def future(batch):
            batch['format'] = 'benchmark-lab-x/calibration-batch/v2'
        def unjustified(batch):
            batch['control'][0]['annotations'][0]['justification'] = ''
        for name, change in dict(twice=twice, silent_real=silent_real, test_rule_on_real=test_rule_on_real, unknown_treatment=unknown_treatment, nobody=nobody,
                                 undated=undated, repeated_author=repeated_author, future=future, unjustified=unjustified).items():
            with self.subTest(name):
                batch = self.lot([self.item('j-agree')])
                change(batch)
                batch['reserved_sha256'] = digest(batch['control'])
                with patch.object(calibration.judgment, 'inspect', side_effect=AssertionError('receipt read')):
                    with self.assertRaises(ValueError):
                        calibration.report(self.h.store, batch)

    def test_unknown_and_missing_annotations_are_excluded_not_guessed(self):
        stray = self.item('j-agree')
        stray['annotations'].append(dict(author='lectrice-a', control_id='inconnu', status='PASS', justification='Hors contrat'))
        partial = self.item('j-reject')
        partial['annotations'] = [row for row in partial['annotations'] if row['control_id'] == 'source']
        report = calibration.report(self.h.store, self.lot([stray, partial]))
        reasons = {(row['operation_id'], row['control_id']): row['reason'] for row in report['coverage']['excluded']}
        self.assertEqual({('j-agree', None): 'ANNOTATION_HORS_CONTROLES', ('j-reject', 'defect'): 'NON_ANNOTE'}, reasons)
        self.assertEqual(1, report['coverage']['measured_pairs'])
        self.assertEqual([1], [group['items'] for group in report['judges']])
        none = calibration.report(self.h.store, self.lot([stray]))
        self.assertEqual(0, none['coverage']['measured_items'])
        self.assertEqual([0], [group['items'] for group in none['judges']])

    def test_two_judge_configurations_are_never_pooled(self):
        other = deepcopy(self.h.profile)
        other['system'] += ' autre consigne'
        self.h.configure(other)
        self.judge('j-second', source='FAIL')
        report = calibration.report(self.h.store, self.lot([self.item('j-agree'), self.item('j-second')]))
        first, second = report['judges']
        self.assertNotEqual(first['judge']['prompt_sha256'], second['judge']['prompt_sha256'])
        self.assertEqual([1, 1], [first['items'], second['items']])
        self.assertEqual([0, 1], [first['totals']['faux_rejet'], second['totals']['faux_rejet']])

    def test_command_replays_retained_receipts_offline_and_writes_nothing(self):
        path = self.h.fixture.home / 'lot.json'
        previous = os.umask(0o077)
        self.addCleanup(os.umask, previous)
        with open(path, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            json.dump(self.full_lot(), stream)
        self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

        def run():
            with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()) as output:
                code = runtime.main(['calibrate-judgment', '--data', str(self.h.data), '--authority', str(path)])
            self.assertEqual(0, code, output.getvalue())
            return output.getvalue()
        first, second = run(), run()
        self.assertEqual(first, second)
        self.assertEqual(calibration.report(self.h.store, self.full_lot()), json.loads(first))
        self.assertEqual(self.calls, self.h.http.request.call_count)
        self.assertEqual(self.operations, self.h.store.inspect_operations())
        self.assertEqual(0, self.h.count())
        self.assertTrue(self.h.store.verify_storage()['integrity_ok'])
        artefacts = os.environ.get('BENCHX_E2E_ARTEFACTS')
        if artefacts:
            Path(artefacts).mkdir(parents=True, exist_ok=True)
            Path(artefacts, 'etalonnage-juge.json').write_text(first, encoding='utf-8')

    def test_command_refuses_a_batch_file_open_to_other_users(self):
        path = self.h.fixture.home / 'ouvert.json'
        path.write_text(json.dumps(self.full_lot()))
        path.chmod(0o644)
        with redirect_stdout(io.StringIO()) as output:
            code = runtime.main(['calibrate-judgment', '--data', str(self.h.data), '--authority', str(path)])
        self.assertEqual((78, dict(state='HOLD', reason='OPERATION_NOT_VERIFIED')), (code, json.loads(output.getvalue())))


if __name__ == '__main__':
    unittest.main()
