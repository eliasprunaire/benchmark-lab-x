"""Independent S2 observation checks, qualified with fictional witnesses

These observations are a proposed test interface, not a product/storage schema
Passing witnesses proves the checks, never the missing S2 HTTP journey
"""
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from benchmark.storage import Store, initialize
from tests.test_storage import PAYLOAD, cost, require_budget


def check(condition, reason):
    if not condition:
        raise AssertionError(reason)


def fingerprint(package):
    return sha256(json.dumps(package, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def require_package(view, opened, expected, private_tokens):
    """opened contains bytes fetched by the observer, not announced filenames"""
    package = view['package']
    check(package == expected, 'package differs from expected instruction/deliverables/pieces')
    check(view['package_sha256'] == fingerprint(package), 'package fingerprint differs')
    check(view['fictional'] is True, 'fictional attribution absent')
    pieces = package['pieces']
    check(bool(pieces), 'no effective pieces')
    check(len({piece['id'] for piece in pieces}) == len(pieces), 'duplicate piece')
    check(set(opened) == {piece['id'] for piece in pieces}, 'announced/opened pieces differ')
    for piece in pieces:
        response = opened[piece['id']]
        check(response['status'] == 200, 'piece cannot be opened')
        raw = response['body']
        check(type(raw) is bytes and len(raw) == piece['size_bytes'], 'piece bytes absent/truncated')
        check(sha256(raw).hexdigest() == piece['sha256'], 'opened piece differs')
        check(response['media_type'] == 'text/plain', 'fictional text piece rendered actively')
    exposed = json.dumps(view, ensure_ascii=False).encode() + b''.join(r['body'] for r in opened.values())
    for token in private_tokens:
        check(token not in exposed, 'private judgment reference exposed')


def require_validation(validation, view):
    check(type(validation['revision']) is int and type(view['revision']) is int, 'revision must be an integer')
    check(validation == {'dossier_id': view['dossier_id'], 'revision': view['revision'],
                         'package_sha256': view['package_sha256']}, 'validation is stale or foreign')


def require_correction(old, new, old_after, unchanged_agreements, expected_changes):
    check(old_after == old, 'previous revision or validation overwritten')
    check(new['dossier_id'] == old['dossier_id'], 'correction changed dossier')
    check(type(new['revision']) is int and new['revision'] > old['revision'], 'no new revision')
    check(new['package_sha256'] != old['package_sha256'], 'changed package retains fingerprint')
    check(new['validation'] is None, 'old agreement validates changed package')
    check(new['payload']['request'] == old['payload']['request'], 'initial request lost')
    check(new['payload']['clarifications'] == old['payload']['clarifications'], 'clarifications lost')
    check(all(a in new['payload']['validated_assumptions'] for a in unchanged_agreements), 'unaffected agreement lost')
    check(new['changes'] == expected_changes, 'changed scope not reported')
    check(new['rechecked'] == expected_changes, 'affected checks not repeated')


def require_denials(responses, before, after, forbidden_tokens):
    expected = {(actor, action) for actor in ('other', 'anonymous')
                for action in ('read', 'correct', 'validate', 'piece', 'judge')}
    check(set(responses) == expected, 'access matrix incomplete')
    for response in responses.values():
        check(response['status'] in (403, 404), 'foreign or anonymous access allowed')
        for token in forbidden_tokens:
            check(token not in response['body'], 'private content in denied response')
    check(before == after, 'denied operation changed private state')


def require_no_replay(observed):
    # Counts are independently observed at the injected transport boundary
    check(observed['effects'] == ['op-fictional'], 'transport replayed or never entered')
    check(observed['operation_ids'] == ['op-fictional'], 'double submission created an operation')
    check(observed['at_transport']['state'] == 'EMISSION_POSSIBLE', 'emission not durable before transport')
    check(observed['at_transport']['reserved_amount'] == '7', 'reserve not durable before transport')
    check(observed['at_transport']['phase'] in ('preparation', 'correction'), 'assistance counted as candidate')
    check(observed['at_transport']['authority'] == 'GO_FICTIF_S2', 'authority inferred from content')
    check(observed['at_transport']['requested_configuration'] == {'model': 'fictional-transport'}, 'requested identity lost')
    check(observed['at_transport']['resources'] == ['piece-fictional'], 'requested resources lost')
    check(observed['after_restart']['state'] == 'AMBIGUOUS', 'ambiguous effect lost')
    check(observed['after_restart']['reserved_amount'] == '7', 'ambiguous reserve released')
    check(observed['after_restart']['ambiguity_reason'] == 'fictional interruption', 'ambiguity reason lost')
    check(observed['read_effect_counts'] == [1, 1, 1], 'preview/refresh/reconnect emits')


def require_unknown_cost(observed):
    check(observed['cost'] == cost(None, 'UNKNOWN'), 'unknown cost replaced or provenance lost')
    check(observed['observed_configuration'] is None, 'unobserved configuration invented')
    require_budget(observed['budget'], reserved='7', spent='0', available='3', unknown=['op-fictional'])
    check(observed['next_status'] in (403, 409, 423), 'dependent call admitted')
    check(observed['effects'] == ['op-fictional'], 'dependent transport entered')
    check(observed['piece_body'] == b'Exemple entierement invente', 'acquired piece unavailable')


def require_admission(observed):
    check(set(observed) == {'maintenance', 'restore_pending', 'budget_exhausted', 'no_authority'}, 'admission matrix incomplete')
    for result in observed.values():
        check(result['status'] in (403, 409, 423), 'blocked operation admitted')
        check(result['effects'] == [], 'transport entered while admission blocked')
        check(result['piece_body'] == b'Exemple entierement invente', 'read blocked with calls')


def require_interview(observed, initial, clarifications, assumptions, stage):
    payload = observed['payload']
    check(payload['request'] == initial, 'request silently narrowed')
    check(payload['clarifications'] == clarifications, 'answers lost or invented')
    check(payload['validated_assumptions'] == assumptions, 'fictional parameter becomes agreement')
    check(payload['state'] == 'EN_ATTENTE', 'need validation becomes contract approval')
    check(observed['stage'] == stage, 'incorrect preparation stage')
    check(observed['candidate_effects'] == [] and observed['publications'] == [], 'preparation creates external authority')
    if stage in ('clarification', 'scope_confirmation', 'suspended'):
        check(bool(observed['explanation']), 'missing targeted question or intelligible limit')
        check(observed['validation'] is None and observed['qualified'] is False, 'unagreed scope declared qualified')


def package_witness():
    raw = b'Exemple entierement invente'
    package = {'instruction': 'Organiser les notes fictives sans executer les actions',
               'deliverables': ['Tableau des actions a relire'],
               'pieces': [{'id': 'piece-fictional', 'name': 'notes.txt',
                           'sha256': sha256(raw).hexdigest(), 'size_bytes': len(raw)}]}
    view = {'dossier_id': 'd-fictional', 'revision': 1, 'package': package,
            'package_sha256': fingerprint(package), 'fictional': True,
            'payload': deepcopy(PAYLOAD), 'validation': None}
    opened = {'piece-fictional': {'status': 200, 'body': raw, 'media_type': 'text/plain'}}
    return view, opened


class JudgeQualificationTests(unittest.TestCase):
    def test_package_and_alternative_accept_actual_bytes(self):
        for raw in (b'Exemple entierement invente', b'Autre exemple fictif recevable'):
            view, opened = package_witness()
            opened['piece-fictional']['body'] = raw
            view['package']['pieces'][0].update(sha256=sha256(raw).hexdigest(), size_bytes=len(raw))
            view['package_sha256'] = fingerprint(view['package'])
            require_package(view, opened, deepcopy(view['package']), [b'JUGE_PRIVE_FICTIF'])

    def test_package_rejects_missing_tampered_summary_and_private_reference(self):
        for defect in ('absent', '404', 'bytes', 'instruction', 'deliverables', 'hash', 'judge', 'judge_in_piece', 'active', 'fictional', 'duplicate'):
            with self.subTest(defect=defect):
                view, opened = package_witness()
                expected = deepcopy(view['package'])
                if defect == 'absent': opened.clear()
                elif defect == '404': opened['piece-fictional']['status'] = 404
                elif defect == 'bytes': opened['piece-fictional']['body'] = b'altered'
                elif defect == 'instruction': view['package']['instruction'] = 'Autre travail'
                elif defect == 'deliverables': view['package']['deliverables'] = []
                elif defect == 'hash': view['package_sha256'] = '0' * 64
                elif defect == 'judge': view['reference'] = 'JUGE_PRIVE_FICTIF'
                elif defect == 'judge_in_piece':
                    raw = b'JUGE_PRIVE_FICTIF'
                    opened['piece-fictional']['body'] = raw
                    view['package']['pieces'][0].update(sha256=sha256(raw).hexdigest(), size_bytes=len(raw))
                    expected = deepcopy(view['package'])
                    view['package_sha256'] = fingerprint(expected)
                elif defect == 'active': opened['piece-fictional']['media_type'] = 'text/html'
                elif defect == 'fictional': view['fictional'] = False
                elif defect == 'duplicate': view['package']['pieces'] *= 2
                with self.assertRaises(AssertionError):
                    require_package(view, opened, expected, [b'JUGE_PRIVE_FICTIF'])

    def test_validation_binds_dossier_revision_and_fingerprint(self):
        view, _ = package_witness()
        valid = {key: view[key] for key in ('dossier_id', 'revision', 'package_sha256')}
        require_validation(valid, view)
        for key, value in (('dossier_id', 'other'), ('revision', 2), ('package_sha256', '0' * 64)):
            with self.subTest(field=key), self.assertRaises(AssertionError):
                require_validation({**valid, key: value}, view)

    def test_correction_keeps_history_and_requires_new_validation(self):
        old, _ = package_witness()
        old['validation'] = {key: old[key] for key in ('dossier_id', 'revision', 'package_sha256')}
        new = deepcopy(old)
        new.update(revision=2, validation=None, changes=['deliverables'], rechecked=['deliverables'])
        new['package']['deliverables'] = ['Liste des actions a relire']
        new['package_sha256'] = fingerprint(new['package'])
        def judge(candidate, history=old):
            require_correction(old, candidate, history, PAYLOAD['validated_assumptions'], ['deliverables'])
        judge(new)
        for defect in ('stale', 'revision', 'hash', 'agreements', 'request', 'answers', 'recheck', 'history'):
            with self.subTest(defect=defect):
                bad = deepcopy(new)
                history = deepcopy(old)
                if defect == 'stale': bad['validation'] = old['validation']
                elif defect == 'revision': bad['revision'] = 1
                elif defect == 'hash': bad['package_sha256'] = old['package_sha256']
                elif defect == 'agreements': bad['payload']['validated_assumptions'] = []
                elif defect == 'request': bad['payload']['request'] = 'reduced'
                elif defect == 'answers': bad['payload']['clarifications'] = []
                elif defect == 'recheck': bad['rechecked'] = []
                elif defect == 'history': history['validation'] = None
                with self.assertRaises(AssertionError): judge(bad, history)

    def test_isolation_checks_every_action_and_no_mutation(self):
        responses = {(actor, action): {'status': 404, 'body': b'NOT_FOUND'}
                     for actor in ('other', 'anonymous')
                     for action in ('read', 'correct', 'validate', 'piece', 'judge')}
        require_denials(responses, b'private-state', b'private-state', [b'PRIVE_FICTIF'])
        for target in responses:
            bad = deepcopy(responses)
            bad[target] = {'status': 200, 'body': b'PRIVE_FICTIF'}
            with self.subTest(target=target), self.assertRaises(AssertionError):
                require_denials(bad, b'private-state', b'private-state', [b'PRIVE_FICTIF'])
        bad = deepcopy(responses)
        bad[('other', 'read')]['body'] = b'PRIVE_FICTIF'
        with self.assertRaises(AssertionError):
            require_denials(bad, b'private-state', b'private-state', [b'PRIVE_FICTIF'])
        bad.pop(('other', 'read'))
        with self.assertRaises(AssertionError):
            require_denials(bad, b'private-state', b'private-state', [b'PRIVE_FICTIF'])
        with self.assertRaises(AssertionError):
            require_denials(responses, b'before', b'changed', [b'PRIVE_FICTIF'])

    def test_call_observations_detect_replay_missing_reserve_and_wrong_phase(self):
        observed = {'effects': ['op-fictional'], 'operation_ids': ['op-fictional'],
                    'at_transport': {'state': 'EMISSION_POSSIBLE', 'reserved_amount': '7',
                                     'phase': 'preparation', 'authority': 'GO_FICTIF_S2',
                                     'requested_configuration': {'model': 'fictional-transport'},
                                     'resources': ['piece-fictional']},
                    'after_restart': {'state': 'AMBIGUOUS', 'reserved_amount': '7', 'ambiguity_reason': 'fictional interruption'},
                    'read_effect_counts': [1, 1, 1]}
        require_no_replay(observed)
        for defect in ('replay', 'double', 'reserve', 'emission', 'phase', 'restart', 'read'):
            with self.subTest(defect=defect):
                bad = deepcopy(observed)
                if defect == 'replay': bad['effects'].append('op-fictional')
                elif defect == 'double': bad['operation_ids'].append('duplicate')
                elif defect == 'reserve': bad['at_transport']['reserved_amount'] = '0'
                elif defect == 'emission': bad['at_transport']['state'] = 'INTENT_RECORDED'
                elif defect == 'phase': bad['at_transport']['phase'] = 'acquisition'
                elif defect == 'restart': bad['after_restart']['state'] = 'RECEIVED'
                elif defect == 'read': bad['read_effect_counts'][-1] = 2
                with self.assertRaises(AssertionError): require_no_replay(bad)

    def test_unknown_cost_keeps_reserve_and_read_access(self):
        observed = {'cost': cost(None, 'UNKNOWN'), 'observed_configuration': None,
                    'budget': {'reserved': '7', 'spent': '0', 'available': '3', 'unknown_cost_operations': ['op-fictional']},
                    'next_status': 409, 'effects': ['op-fictional'], 'piece_body': b'Exemple entierement invente'}
        require_unknown_cost(observed)
        for defect in ('zero', 'reserve', 'admission', 'replay', 'read', 'identity'):
            with self.subTest(defect=defect):
                bad = deepcopy(observed)
                if defect == 'zero': bad['cost'] = cost('0')
                elif defect == 'reserve': bad['budget']['reserved'] = '0'
                elif defect == 'admission': bad['next_status'] = 200
                elif defect == 'replay': bad['effects'].append('dependent')
                elif defect == 'read': bad['piece_body'] = b''
                elif defect == 'identity': bad['observed_configuration'] = {'model': 'assumed'}
                with self.assertRaises(AssertionError): require_unknown_cost(bad)

    def test_maintenance_restore_budget_and_authority_close_transport(self):
        observed = {key: {'status': 423, 'effects': [], 'piece_body': b'Exemple entierement invente'}
                    for key in ('maintenance', 'restore_pending', 'budget_exhausted', 'no_authority')}
        require_admission(observed)
        for key in observed:
            bad = deepcopy(observed)
            bad[key]['effects'] = ['unauthorized']
            with self.subTest(gate=key), self.assertRaises(AssertionError): require_admission(bad)

    def test_scripted_vague_changed_scope_and_unevaluable_need(self):
        for stage in ('clarification', 'scope_confirmation', 'suspended'):
            observed = {'payload': deepcopy(PAYLOAD), 'stage': stage, 'explanation': 'Question ou limite fictive a examiner',
                        'validation': None, 'qualified': False, 'candidate_effects': [], 'publications': []}
            def judge(value):
                require_interview(value, PAYLOAD['request'], PAYLOAD['clarifications'], PAYLOAD['validated_assumptions'], stage)
            judge(observed)
            for defect in ('request', 'invented_agreement', 'silent_scope', 'approved', 'candidate', 'publication'):
                with self.subTest(stage=stage, defect=defect):
                    bad = deepcopy(observed)
                    if defect == 'request': bad['payload']['request'] = 'Reduced need'
                    elif defect == 'invented_agreement': bad['payload']['validated_assumptions'].append('Invented agreement')
                    elif defect == 'silent_scope': bad['explanation'] = ''
                    elif defect == 'approved': bad['qualified'] = True
                    elif defect == 'candidate': bad['candidate_effects'] = ['call']
                    elif defect == 'publication': bad['publications'] = ['published']
                    with self.assertRaises(AssertionError): judge(bad)


class ExistingStorageObservationTests(unittest.TestCase):
    def test_judge_reads_real_s1_bytes_and_detects_missing_piece(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'private'
            initialize(root)
            view, opened = package_witness()
            with closing(Store(root)) as store:
                store.save_dossier('d-fictional', 1, PAYLOAD)
                store.put_piece('d-fictional', 1, 'piece-fictional', name='notes.txt', role='candidate',
                                media_type='text/plain', content=opened['piece-fictional']['body'])
                store.put_piece('d-fictional', 1, 'judge-fictional', name='reference.txt', role='judge',
                                media_type='text/plain', content=b'JUGE_PRIVE_FICTIF')
            with closing(Store(root)) as store:
                opened['piece-fictional']['body'] = store.read_piece('piece-fictional')
                require_package(view, opened, view['package'], [b'JUGE_PRIVE_FICTIF'])
                self.assertEqual(b'JUGE_PRIVE_FICTIF', store.read_piece('judge-fictional'))
                with self.assertRaises(KeyError): store.read_piece('missing-fictional')
                self.assertTrue(store.verify_storage()['integrity_ok'])
            # Store is a trusted internal API: reading judge bytes is not an HTTP access policy


if __name__ == '__main__':
    unittest.main()
