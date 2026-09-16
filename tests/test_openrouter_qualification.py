"""Qualification automatisée S17, sans appel réseau réel"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from benchmark import openrouter_qualification as assistant
from benchmark import campaigns, preparation as prep, qualification, service, storage, web_api
from tests.test_s2_review_regressions import response_for
from tests.test_s3_regressions import ACTOR, AUTHORITY, check, specification


KEY = 'fixture-s17-key-not-a-credential'


def qualify_fixture(data, store, session, dossier_id, preview):
    store.create_budget('qualification', '100', 'USD')
    prep.admit(store, dict(authority_id='TEST_ONLY_QUALIFICATION', budget_id='qualification',
                          reserve_amount='1', requested_configuration={'model': 'préparation/factice'}))
    transport = QualificationTransport({'qualified': True, 'findings': [], 'summary': 'Exemple qualifié'})
    _, operation_id, _ = prep.validate_and_qualify(
        store, session, dossier_id, prep.binding(dossier_id, preview['revision'], preview['package_sha256']),
        'a' * 40, transport)
    prep.execute_qualification(data, operation_id, transport)
    return operation_id


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


class SlowQualificationTransport(QualificationTransport):
    def __init__(self, result):
        super().__init__(result)
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, operation, request):
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError('Qualification factice non libérée')
        return super().__call__(operation, request)


class OpenRouterQualificationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))
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
        response['receipt']['result']['package']['candidate']['criteria'] = {
            'eliminatory': ['Ne pas inventer une action'],
            'obligations': ['Toutes les actions présentes', 'Responsables conservés'],
            'quality': [{'label': 'Clarté', 'scale': ['excellent', 'acceptable', 'faible'],
                         'favorable': 'excellent'}]}
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
        self.assertEqual(valid, prep._qualification_result(valid))
        for invalid in (
                {'qualified': True, 'findings': [{'kind': 'coherence', 'severity': 'blocking', 'text': 'Écart'}], 'summary': 'Erreur'},
                {'qualified': False, 'findings': [], 'summary': 'Incohérent'},
                {'qualified': True, 'findings': [], 'summary': 'OK', 'authority': 'Ayo'}):
            with self.assertRaises(ValueError):
                prep._qualification_result(invalid)
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

    def test_contrat_de_comparaison_derive_du_paquet_et_du_recu(self):
        transport, operation_id = self.validate({'qualified': True, 'findings': [], 'summary': 'Exemple qualifié'})
        prep.execute_qualification(self.data, operation_id, transport)
        contract = campaigns._current_contract(self.store, self.store._connection, 'dossier')
        package = json.loads(self.store._connection.execute(
            'SELECT package_json FROM s2_revisions WHERE dossier_id=? AND revision=?',
            ('dossier', self.preview['revision'])).fetchone()[0])
        self.assertEqual(package, contract['package'])
        self.assertEqual(self.preview['package_sha256'], contract['package_sha256'])
        self.assertEqual(('dossier', self.preview['revision'], 1, []),
                         (contract['dossier_id'], contract['revision'], contract['version'], contract['reference_pieces']))
        self.assertEqual({
            'result_expected': package['instruction'],
            'obligations': [{'id': 'O1', 'description': 'Toutes les actions présentes'},
                            {'id': 'O2', 'description': 'Responsables conservés'}],
            'eliminatory_errors': [{'id': 'E1', 'description': 'Ne pas inventer une action'}],
            'secondary_criteria': [{'id': 'Q1', **package['criteria']['quality'][0]}],
            'limits': package['acceptable_ambiguities'],
            'cost_basis': {'scope': 'Une tentative par cellule', 'attempts': 'Sans reprise',
                           'unit': 'USD', 'conversion': None}}, contract['specification'])
        operation = next(row for row in self.store.inspect_operations() if row['operation_id'] == operation_id)
        raw, authority = self.store._connection.execute(
            'SELECT contract_json, authority_json FROM s2_comparison_contracts').fetchone()
        self.assertEqual(contract, json.loads(raw))
        self.assertEqual({'authority_id': 'assistant:qualification/fictive', 'operation_id': operation_id,
                          'receipt_sha256': qualification.digest(operation['receipt'])}, json.loads(authority))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        with self.assertRaisesRegex(prep.Denied, 'CONTRACT_MISSING') as refused:
            campaigns._current_contract(self.store, self.store._connection, 'absent')
        self.assertEqual("Le contrat de comparaison n'est pas encore établi. Terminez la qualification de l'exemple.",
                         service.denied_response(refused.exception)['value']['error'])

    def test_contrat_operateur_prioritaire_et_inchange(self):
        qualification.initialize(self.data)
        transport, operation_id = self.validate({'qualified': True, 'findings': [], 'summary': 'Exemple qualifié'})
        reference = self.store._connection.execute("SELECT piece_id FROM pieces WHERE role='judge'").fetchone()[0]
        candidate = qualification.draft(self.store, 'dossier', self.preview['revision'], specification(reference))
        receipt = qualification.qualify(self.store, candidate['contract_sha256'], reviewer=ACTOR, check=check)
        qualification.approve(self.store, candidate['contract_sha256'], receipt['qualification_id'],
                              actor=ACTOR, authority=AUTHORITY)
        before = qualification.inspect_contract(self.store, candidate['contract_sha256'])
        prep.execute_qualification(self.data, operation_id, transport)
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s2_comparison_contracts').fetchone()[0])
        self.assertEqual(before, qualification.inspect_contract(self.store, candidate['contract_sha256']))
        self.assertEqual(candidate['contract'], campaigns._current_contract(self.store, self.store._connection, 'dossier'))

    def test_validation_du_contrat_reduit_et_integrite_de_son_autorite(self):
        transport, operation_id = self.validate({'qualified': True, 'findings': [], 'summary': 'Exemple qualifié'})
        prep.execute_qualification(self.data, operation_id, transport)
        contract = campaigns._current_contract(self.store, self.store._connection, 'dossier')
        for change in ('cle', 'identifiant', 'type', 'qualite'):
            spec = deepcopy(contract['specification'])
            if change == 'cle':
                spec['method'] = {}
            elif change == 'identifiant':
                spec['obligations'][1]['id'] = 'O1'
            elif change == 'type':
                spec['obligations'][0]['description'] = None
            else:
                spec['secondary_criteria'] *= 3
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'QUALITY_LIMIT' if change == 'qualite' else ''):
                campaigns._comparison_specification(spec)
        fingerprint = qualification.digest(contract)
        self.store._connection.execute("UPDATE s2_comparison_contracts SET authority_json='{}'")
        with self.assertRaises(ValueError):
            campaigns._approved(self.store, self.store._connection, fingerprint)
        with self.assertRaises(ValueError):
            self.store.verify_storage()

    def test_contrat_et_qualification_sont_atomiques(self):
        transport, operation_id = self.validate({'qualified': True, 'findings': [], 'summary': 'Exemple qualifié'})
        original = campaigns._record_comparison_contract
        def interrupted(store, connection, operation):
            original(store, connection, operation)
            raise storage.IntegrityError('Écriture interrompue')
        with patch.object(campaigns, '_record_comparison_contract', side_effect=interrupted):
            prep.execute_qualification(self.data, operation_id, transport)
        for table in ('s2_qualifications', 's2_comparison_contracts'):
            self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM ' + table).fetchone()[0])
        operation = next(row for row in self.store.inspect_operations() if row['operation_id'] == operation_id)
        self.assertEqual('AMBIGUOUS', operation['state'])

    def test_anciennes_bases_refusees_avec_le_message_de_recreation(self):
        qualification.initialize(self.data)
        campaigns.initialize(self.data)
        for previous in ('s2', 's4'):
            with self.subTest(previous=previous):
                connection = sqlite3.connect(':memory:')
                self.addCleanup(connection.close)
                self.store._connection.backup(connection)
                if previous == 's2':
                    connection.execute('DROP TABLE s2_comparison_contracts')
                else:
                    connection.execute('DROP TABLE s4_campaigns')
                    connection.execute(campaigns._TABLES['s4_campaigns'].replace(
                        'contract_sha256 TEXT NOT NULL,',
                        'contract_sha256 TEXT NOT NULL REFERENCES s3_contracts(contract_sha256),'))
                with self.assertRaisesRegex(storage.SchemaError, 'Base antérieure à la vague 2 : à recréer'):
                    storage._check_schema(connection)

    def test_blocking_finding_keeps_revision_not_qualified(self):
        result = {'qualified': False, 'findings': [{'kind': 'leak', 'severity': 'blocking',
                                                    'text': 'La référence fuit dans la consigne'}],
                  'summary': 'Correction requise'}
        transport, operation_id = self.validate(result)
        prep.execute_qualification(self.data, operation_id, transport)
        view = prep.view(self.store, self.session, 'dossier')
        self.assertFalse(view['qualified'])
        self.assertEqual(result['findings'], view['qualification']['findings'])
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s2_comparison_contracts').fetchone()[0])

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
        with patch.object(campaigns, 'inspect', return_value=snapshot), \
                patch.object(campaigns, 'connection_for') as connection:
            connection.return_value.execute.return_value.fetchone.return_value = (1,)
            with self.assertRaises(prep.Denied) as refused:
                web_api.dispatch(self.store, 'GET', path, self.token, None, 'b' * 40, True)
            self.assertEqual(('NOT_QUALIFIED', [finding]),
                             (refused.exception.code, refused.exception.findings))
            with self.assertRaises(prep.Denied) as refused:
                web_api.dispatch(self.store, 'POST', path.replace('/conditions', '/start'), self.token,
                                 {'csrf_token': self.csrf}, 'b' * 40, True, candidate_transport=lambda *_: None)
            self.assertEqual('NOT_QUALIFIED', refused.exception.code)

    def test_double_validation_ne_relance_pas_et_garde_admission_ouverte(self):
        transport = SlowQualificationTransport(
            {'qualified': True, 'findings': [], 'summary': 'Paquet cohérent'})
        path = '/preparation/dossiers/dossier/validation'
        body = {**prep.binding('dossier', self.preview['revision'], self.preview['package_sha256']),
                'csrf_token': self.csrf}
        first = web_api.dispatch(self.store, 'POST', path, self.token, body, 'b' * 40, True,
                                 qualification_transport=transport)
        second = web_api.dispatch(self.store, 'POST', path, self.token, body, 'b' * 40, True,
                                  qualification_transport=transport)
        workers = [threading.Thread(target=prep.execute_qualification,
                                    args=(self.data, start['qualification_operation'], transport))
                   for start in (first[3], second[3]) if start]
        workers[0].start()
        self.assertTrue(transport.entered.wait(2))
        for worker in workers[1:]:
            worker.start()
            worker.join(2)
        observed = prep.availability(self.store, True)
        transport.release.set()
        for worker in workers:
            worker.join(2)
        self.assertTrue(observed['admission_open'])
        self.assertEqual(first[1]['operation_id'], second[1]['operation_id'])
        self.assertIsNone(second[3])
        self.assertEqual(1, len(transport.calls))

    def test_conflit_initial_execute_ne_ferme_pas_admission(self):
        _, operation_id = self.validate({'qualified': True, 'findings': [], 'summary': 'OK'})
        with patch.object(storage.Store, '_operation_for_update',
                          side_effect=storage.ConflictError('État déjà avancé')):
            prep.execute_qualification(self.data, operation_id,
                                       QualificationTransport({'qualified': True, 'findings': [], 'summary': 'OK'}))
        self.assertIsNotNone(prep.admission(self.store))

    def test_erreurs_de_qualification_exposent_un_code_et_un_texte_francais(self):
        errors = []
        try:
            prep.validate_and_qualify(self.store, self.session, 'dossier',
                                      prep.binding('dossier', self.preview['revision'], self.preview['package_sha256']),
                                      'b' * 40, None)
        except prep.Denied as error:
            errors.append(error)
        prep.close_admission(self.store)
        try:
            prep.validate_and_qualify(self.store, self.session, 'dossier',
                                      prep.binding('dossier', self.preview['revision'], self.preview['package_sha256']),
                                      'b' * 40, QualificationTransport(
                                          {'qualified': True, 'findings': [], 'summary': 'OK'}))
        except prep.Denied as error:
            errors.append(error)
        responses = [service.denied_response(error) for error in errors]
        self.assertEqual(['QUALIFICATION_UNAVAILABLE', 'ADMISSION_CLOSED'],
                         [response['value']['error_code'] for response in responses])
        self.assertEqual(['Qualification indisponible', 'Admission fermée'],
                         [response['value']['error'] for response in responses])


if __name__ == '__main__':
    unittest.main()
