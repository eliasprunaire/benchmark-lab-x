"""Qualification automatisée S17, sans appel réseau réel"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark import openrouter_qualification as assistant
from benchmark import campaigns, preparation as prep, storage
from tests.test_s2_review_regressions import response_for


KEY = 'fixture-s17-key-not-a-credential'


class QualificationTransport:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def configuration(self):
        return {'model': 'qualification/fictive', 'revision': 'qualification/fictive-v1'}

    def prepare(self, operation, request):
        return storage._strict_json({'operation': operation['operation_id'], 'request': request})

    def __call__(self, operation, request):
        self.calls.append((deepcopy(operation), deepcopy(request)))
        return {'receipt': {'receipt_id': 'qualification-' + operation['operation_id'],
                            'observed_configuration': {'model': 'qualification/fictive-v1'},
                            'resources_seen': [operation['conserved_wire']],
                            'result': deepcopy(self.result)},
                'cost': {'status': 'KNOWN', 'amount': '0.15', 'currency': 'USD',
                         'source': 'Reçu synthétique S17'}}


class OpenRouterQualificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s17-qualification-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name).resolve() / 'private'
        storage.initialize(self.data)
        storage.initialize_preparation(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        self.store.create_budget('preparation', '100', 'USD')
        prep.admit(self.store, dict(authority_id='TEST_ONLY_S17', budget_id='preparation',
                                   reserve_amount='1', requested_configuration={'model': 'preparation/fictive'}))
        self.session, self.csrf, self.token = prep.session(self.store, None, create=True)
        operation_id, _ = prep.submit(self.store, self.session, 'dossier',
            {'action_id': 'create', 'request': 'Organiser les actions de cet atelier entièrement inventé'},
            'a' * 40, True)
        response = response_for({'operation_id': operation_id})
        response['cost'].update(amount='0.10', currency='USD')
        prep.execute(self.data, operation_id, lambda *_: response)
        self.preview = prep.view(self.store, self.session, 'dossier')

    def validate(self, result):
        transport = QualificationTransport(result)
        binding, operation_id, start = prep.validate_and_qualify(
            self.store, self.session, 'dossier',
            prep.binding('dossier', self.preview['revision'], self.preview['package_sha256']),
            'b' * 40, transport)
        self.assertEqual(self.preview['revision'], binding['revision'])
        self.assertTrue(start)
        return transport, operation_id

    def test_profile_frozen_and_strict_answer(self):
        transport = assistant.OpenRouterQualification(KEY)
        configuration = transport.configuration()
        self.assertEqual('anthropic/claude-fable-5.1', configuration['model'])
        self.assertEqual({'effort': 'medium'}, configuration['parameters']['reasoning'])
        valid = {'qualified': True, 'findings': [], 'summary': 'Paquet cohérent'}
        self.assertEqual(valid, transport.validate_answer(valid, {'role': 'assistant'}))
        for invalid in (
                {'qualified': True, 'findings': [{'kind': 'coherence', 'severity': 'blocking', 'text': 'Écart'}], 'summary': 'Erreur'},
                {'qualified': False, 'findings': [], 'summary': 'Incohérent'},
                {'qualified': True, 'findings': [], 'summary': 'OK', 'authority': 'Ayo'}):
            with self.assertRaises(ValueError):
                transport.validate_answer(invalid, {'role': 'assistant'})
        _, operation_id, _ = prep.validate_and_qualify(
            self.store, self.session, 'dossier',
            prep.binding('dossier', self.preview['revision'], self.preview['package_sha256']),
            'b' * 40, transport)
        wire = json.loads(next(row for row in self.store.inspect_operations()
                               if row['operation_id'] == operation_id)['resources'][1])
        self.assertEqual(configuration['model'], wire['model'])
        self.assertEqual(transport._profile['system'], wire['messages'][0]['content'])
        self.assertNotIn(KEY, storage._strict_json(wire))

    def test_validation_reserves_then_executes_outside_request(self):
        result = {'qualified': True, 'findings': [{'kind': 'fiction', 'severity': 'note',
                                                   'text': 'Noms entièrement inventés'}],
                  'summary': 'Exemple qualifié'}
        transport, operation_id = self.validate(result)
        operation = next(row for row in self.store.inspect_operations()
                         if row['operation_id'] == operation_id)
        self.assertEqual(('qualification', 'INTENT_RECORDED'), (operation['phase'], operation['state']))
        self.assertEqual('1', self.store.inspect_budget('preparation')['reserved'])
        self.assertEqual([], transport.calls)
        prep.execute_qualification(self.data, operation_id, transport)
        view = prep.view(self.store, self.session, 'dossier')
        self.assertTrue(view['qualified'])
        self.assertEqual(result['findings'], view['qualification']['findings'])
        self.assertEqual('0.15', view['qualification']['cost_usd'])
        self.assertNotIn(KEY, storage._strict_json(view))

    def test_blocking_finding_keeps_revision_not_qualified(self):
        result = {'qualified': False, 'findings': [{'kind': 'leak', 'severity': 'blocking',
                                                    'text': 'La référence fuit dans la consigne'}],
                  'summary': 'Correction requise'}
        transport, operation_id = self.validate(result)
        prep.execute_qualification(self.data, operation_id, transport)
        view = prep.view(self.store, self.session, 'dossier')
        self.assertFalse(view['qualified'])
        self.assertEqual(result['findings'], view['qualification']['findings'])

    def test_qualification_input_contains_only_requested_case_material(self):
        _, operation_id = self.validate({'qualified': True, 'findings': [], 'summary': 'OK'})
        operation = next(row for row in self.store.inspect_operations()
                         if row['operation_id'] == operation_id)
        content = json.loads(operation['resources'][0])
        self.assertEqual({'instruction', 'deliverables', 'criteria', 'acceptable_ambiguities',
                          'candidate_pieces', 'judgment_reference', 'reformulated_need', 'clarifications'},
                         set(content))
        self.assertNotIn(KEY, storage._strict_json(content))
        self.assertNotIn(str(self.data), storage._strict_json(content))

    def test_invalid_output_is_received_but_never_qualifies(self):
        transport, operation_id = self.validate(
            {'qualified': True, 'findings': [], 'summary': 'OK', 'authority': 'modèle'})
        prep.execute_qualification(self.data, operation_id, transport)
        operation = next(row for row in self.store.inspect_operations()
                         if row['operation_id'] == operation_id)
        self.assertEqual('RECEIVED', operation['state'])
        self.assertEqual(0, self.store._connection.execute(
            'SELECT count(*) FROM s2_qualifications').fetchone()[0])
        view = prep.view(self.store, self.session, 'dossier')
        self.assertFalse(view['qualified'])
        self.assertEqual('BLOCKED', view['qualification']['status'])

    def test_launch_routes_return_not_qualified_with_findings(self):
        finding = {'kind': 'decidability', 'severity': 'blocking',
                   'text': 'Une obligation ne peut pas être décidée à partir des pièces'}
        transport, operation_id = self.validate(
            {'qualified': False, 'findings': [finding], 'summary': 'Correction requise'})
        prep.execute_qualification(self.data, operation_id, transport)
        snapshot = {'task': {'dossier_id': 'dossier', 'revision': self.preview['revision']}}
        path = '/preparation/dossiers/dossier/campaigns/campagne/conditions'
        with patch.object(campaigns, 'inspect', return_value=snapshot):
            with self.assertRaises(prep.Denied) as refused:
                prep.dispatch(self.store, 'GET', path, self.token, None, 'b' * 40, True)
            self.assertEqual(('NOT_QUALIFIED', [finding]),
                             (refused.exception.code, refused.exception.findings))
            with self.assertRaises(prep.Denied) as refused:
                prep.dispatch(self.store, 'POST', path.replace('/conditions', '/start'), self.token,
                              {'csrf_token': self.csrf}, 'b' * 40, True, candidate_transport=lambda *_: None)
            self.assertEqual('NOT_QUALIFIED', refused.exception.code)


if __name__ == '__main__':
    unittest.main()
