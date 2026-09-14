"""Comparaison à la lecture des paquets de préparation conservés"""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from benchmark import preparation, storage


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
    def test_changes_compare_les_paquets_sans_ecriture(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            storage.initialize(data)
            storage.initialize_preparation(data)
            with closing(storage.Store(data)) as store:
                store.create_budget('review', '10', 'TEST')
                preparation.admit(store, {'authority_id': 'LOCAL_FICTIONAL_REVIEW', 'budget_id': 'review',
                    'reserve_amount': '1', 'requested_configuration': {'model': 'fictional'}})
                session, _, _ = preparation.session(store, None, create=True)
                operation, _ = preparation.submit(store, session, 'dossier',
                    {'action_id': 'create', 'request': 'Organiser les actions de cet atelier inventé'}, 'test', True)
                preparation.execute(data, operation, lambda op, request: response(op, 'Organiser les notes',
                    [{'name': 'notes.txt', 'content': 'Action : relire'},
                     {'name': 'obsolète.txt', 'content': 'Action : retirer'}]))
                first = preparation.view(store, session, 'dossier')
                self.assertEqual([], first['changes'])

                operation, _ = preparation.submit(store, session, 'dossier', {'action_id': 'correct',
                    'revision': first['revision'], 'kind': 'correct', 'message': 'Ajouter le compte rendu'},
                    'test', True)
                preparation.execute(data, operation, lambda op, request: response(op, 'Organiser les notes et le compte rendu',
                    [{'name': 'notes.txt', 'content': 'Action : relire et valider'},
                     {'name': 'compte-rendu.txt', 'content': 'Décision : valider'}]))

                before = store._connection.total_changes
                current = preparation.view(store, session, 'dossier')
                self.assertEqual(before, store._connection.total_changes)
                self.assertEqual(['instruction', 'pieces'], current['changes'])
                self.assertEqual({'added': ['compte-rendu.txt'], 'removed': ['obsolète.txt'],
                                  'modified': ['notes.txt']}, current['piece_changes'])
                self.assertEqual('[]', store._connection.execute(
                    'SELECT changes_json FROM s2_revisions WHERE dossier_id=? AND revision=?',
                    ('dossier', current['revision'])).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
