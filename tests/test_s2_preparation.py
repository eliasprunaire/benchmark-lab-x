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
from tests.test_storage import PAYLOAD


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
