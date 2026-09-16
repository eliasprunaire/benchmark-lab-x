"""Comparaison à la lecture des paquets de préparation conservés"""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from benchmark import preparation, storage
from benchmark_web import views


def response(operation, instruction, pieces):
    return {'receipt': {'receipt_id': 'fictional-' + operation['operation_id'],
        'observed_configuration': {'model': 'fictional'}, 'resources_seen': [],
        'result': {'stage': 'preview', 'explanation': '', 'reformulation': 'Organiser les notes',
            'fictional_parameters': {'atelier': 'inventé'},
            'package': {'candidate': {'instruction': instruction, 'deliverables': ['Liste des actions'],
                'criteria': ['Toutes les actions présentes'], 'acceptable_ambiguities': [],
                'pieces': pieces}, 'internal': {'human_work': 'Relire', 'limits': ['Exemple inventé']},
                'judgment': {'pieces': [{'name': 'reference.txt', 'content': 'Attendu réservé'}]}}}},
        'cost': {'status': 'KNOWN', 'amount': '1', 'currency': 'TEST', 'source': 'Reçu fictif'}}


class S2ReviewTest(unittest.TestCase):
    def test_revision_precedente_aux_noms_ambigus_est_refusee(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            storage.initialize(data)
            storage.initialize_preparation(data)
            with closing(storage.Store(data)) as store:
                previous = {'instruction': 'Classer', 'deliverables': ['Liste'], 'criteria': [],
                            'acceptable_ambiguities': [], 'pieces': [
                                {'name': 'notes.txt', 'sha256': 'a' * 64},
                                {'name': 'notes.txt', 'sha256': 'b' * 64}]}
                encoded = storage._strict_json(previous)
                store._connection.execute(
                    'INSERT INTO dossier_revisions VALUES (?, ?, ?)', ('ambigu', 1, '{}'))
                store._connection.execute(
                    'INSERT INTO s2_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    ('ambigu', 1, 'preview', '', encoded, sha256(encoded.encode()).hexdigest(), '[]', '[]'))
                current = {**previous, 'pieces': [{'name': 'notes.txt', 'sha256': 'c' * 64}]}
                with self.assertRaisesRegex(
                        storage.IntegrityError,
                        '^Noms de pièces ambigus dans la révision précédente$'):
                    preparation._package_changes(store._connection, 'ambigu', 2, current)

    def test_publish_stores_closed_criteria(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            storage.initialize(data)
            storage.initialize_preparation(data)
            with closing(storage.Store(data)) as store:
                store.create_budget('closed', '10', 'TEST')
                preparation.admit(store, {'authority_id': 'TEST_ONLY_CLOSED', 'budget_id': 'closed',
                    'reserve_amount': '1', 'requested_configuration': {'model': 'fictional'}})
                session, _, _ = preparation.session(store, None, create=True)
                operation, _ = preparation.submit(store, session, 'closed',
                    {'action_id': 'create', 'request': 'Vérifier la vue fermée des critères'}, 'test', True)
                received = {}
                expected = {'eliminatory': [], 'obligations': ['Action présente'], 'quality': []}
                def transport(operation, request):
                    received.update(response(operation, 'Organiser les notes',
                        [{'name': 'notes.txt', 'content': 'Action : relire'}]))
                    received['receipt']['result']['package']['candidate']['criteria'] = ['Action présente']
                    return received

                preparation.execute(data, operation, transport)
                stored = json.loads(store._connection.execute(
                    'SELECT package_json FROM s2_revisions WHERE dossier_id=? AND revision=2',
                    ('closed',)).fetchone()[0])
                self.assertEqual(expected, stored['criteria'])
                self.assertEqual(['Action présente'],
                                 received['receipt']['result']['package']['candidate']['criteria'])

    def test_changes_compare_les_paquets_sans_ecriture(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            storage.initialize(data)
            storage.initialize_preparation(data)
            with closing(storage.Store(data)) as store:
                store.create_budget('review', '10', 'TEST')
                preparation.admit(store, {'authority_id': 'LOCAL_FICTIONAL_REVIEW', 'budget_id': 'review',
                    'reserve_amount': '1', 'requested_configuration': {'model': 'fictional'}})
                session, csrf, _ = preparation.session(store, None, create=True)
                operation, _ = preparation.submit(store, session, 'dossier',
                    {'action_id': 'create', 'request': 'Organiser les actions de cet atelier inventé'}, 'test', True)
                preparation.execute(data, operation, lambda op, request: response(op, 'Organiser les notes',
                    [{'name': 'notes.txt', 'content': 'Action : relire'},
                     {'name': 'obsolète.txt', 'content': 'Action : retirer'}]))
                first = preparation.view(store, session, 'dossier')
                self.assertEqual([], first['changes'])
                self.assertEqual({'eliminatory': [], 'obligations': ['Toutes les actions présentes'],
                                  'quality': []}, first['criteria'])
                self.assertEqual('satisfait = aucune faute éliminatoire et toutes les obligations prouvées ; '
                                 'la qualité départage, sans note', first['criteria_rule'])
                stored = json.loads(store._connection.execute(
                    'SELECT package_json FROM s2_revisions WHERE dossier_id=? AND revision=?',
                    ('dossier', first['revision'])).fetchone()[0])
                self.assertEqual({'eliminatory': [], 'obligations': ['Toutes les actions présentes'],
                                  'quality': []}, stored['criteria'])
                duplicate = json.loads(store._connection.execute(
                    'SELECT package_json FROM s2_revisions WHERE dossier_id=? AND revision=?',
                    ('dossier', first['revision'])).fetchone()[0])
                duplicate['pieces'][1]['name'] = duplicate['pieces'][0]['name']
                digest = sha256(storage._strict_json(duplicate).encode()).hexdigest()
                with self.assertRaisesRegex(storage.IntegrityError, 'Nom de pièce répété'):
                    preparation.package_check(store, 'dossier', first['revision'], duplicate, digest)
                removed = next(piece for piece in first['package']['pieces'] if piece['name'] == 'obsolète.txt')
                removed_path = data / store.get_piece(removed['id'])['relative_path']

                operation, _ = preparation.submit(store, session, 'dossier', {'action_id': 'correct',
                    'revision': first['revision'], 'kind': 'correct', 'message': 'Ajouter le compte rendu'},
                    'test', True)
                preparation.execute(data, operation, lambda op, request: response(op, 'Organiser les notes et le compte rendu',
                    [{'name': 'notes.txt', 'content': 'Action : relire et valider'},
                     {'name': 'compte-rendu.txt', 'content': 'Décision : valider'}]))
                removed_path.unlink()

                before = store._connection.total_changes
                current = preparation.view(store, session, 'dossier')
                self.assertEqual(before, store._connection.total_changes)
                self.assertEqual(['instruction', 'pieces'], current['changes'])
                self.assertEqual({'added': ['compte-rendu.txt'], 'removed': ['obsolète.txt'],
                                  'modified': ['notes.txt']}, current['piece_changes'])
                page = views.render(current, csrf).decode()
                for expected in ('Pièces ajoutées', 'compte-rendu.txt', 'Pièces retirées', 'obsolète.txt',
                                 'Pièces modifiées', 'notes.txt'):
                    self.assertIn(expected, page)
                self.assertEqual('[]', store._connection.execute(
                    'SELECT changes_json FROM s2_revisions WHERE dossier_id=? AND revision=?',
                    ('dossier', current['revision'])).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
