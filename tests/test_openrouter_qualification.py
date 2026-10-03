"""Qualification automatisée S17, sans appel réseau réel"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from benchmark.transports import openrouter as assistant
from benchmark.acquisition import campaigns
from benchmark import preparation as prep, qualification, service, storage, web_api
from tests.test_s2_review_regressions import Authorized, response_for
from tests.test_s3_regressions import ACTOR, AUTHORITY, check, specification


KEY = 'fixture-s17-key-not-a-credential'


def qualify_fixture(data, store, session, dossier_id, preview):
    store.create_budget('qualification', '100', 'USD')
    transport = QualificationTransport({'qualified': True, 'findings': [], 'summary': 'Exemple qualifié'})
    transport.granted = dict(authority_id='TEST_ONLY_QUALIFICATION', budget_id='qualification',
                             reserve_amount='1', requested_configuration={'model': 'préparation/factice'})
    _, operation_id, _ = prep.validate_and_qualify(
        store, session, dossier_id, prep.binding(dossier_id, preview['revision'], preview['package_sha256']),
        'a' * 40, transport)
    prep.execute_qualification(data, operation_id, transport)
    return operation_id


def default_witnesses(request):
    """Un défaut ciblé sur le premier contrôle du paquet, quand le test n'en décrit aucun"""
    control = request['outgoing']['controls'][0]['id']
    return [{'kind': 'defect', 'output': 'Réponse témoin sans le contenu attendu.',
             'expected': [{'control_id': control, 'status': 'FAIL'}],
             'justification': 'La référence attend ce contenu : son absence est le défaut ciblé.'}]


class QualificationTransport:
    # Autorité de la session à laquelle l'exécuteur lie l'assistant ; None : session sans clé
    granted = dict(authority_id='TEST_ONLY_S17', budget_id='preparation',
                   reserve_amount='1', requested_configuration={'model': 'preparation/fictive'})
    # Un résultat sans témoins en reçoit un par défaut ; False : rendu tel quel
    auto_witnesses = True

    def __init__(self, result):
        self.result = result
        self.calls = []
        self.last = None

    def authority(self):
        return deepcopy(self.granted)

    def configuration(self):
        return {'model': 'qualification/fictive', 'revision': 'qualification/fictive-v1'}

    def controller(self):
        if not hasattr(self, '_controller'):
            self._controller = WitnessControl(self)
        return self._controller

    def prepare(self, operation, request):
        return storage._strict_json({'operation': operation['operation_id'], 'request': request})

    def __call__(self, operation, request):
        self.calls.append((deepcopy(operation), deepcopy(request)))
        result = deepcopy(self.result)
        if self.auto_witnesses and type(result) is dict and 'qualified' in result and 'witnesses' not in result:
            result['witnesses'] = default_witnesses(request)
        self.last = result
        return {'receipt': {'receipt_id': 'qualification-' + operation['operation_id'],
                            'observed_configuration': {'model': 'qualification/fictive-v1'},
                            'resources_seen': [operation['conserved_wire']],
                            'result': deepcopy(result)},
                'cost': {'status': 'KNOWN', 'amount': '0.15', 'currency': 'USD',
                         'source': 'Reçu synthétique S17'}}


class WitnessControl:
    """Juge factice des témoins : il décide ce que l'attendu prévoit, sauf décision imposée par type de témoin

    `decisions[kind][control_id]` vaut (statut, passage cité) ; un passage absent de la réponse témoin
    n'est pas une preuve exploitable
    """
    def __init__(self, qualification, reserve='0.5'):
        self.qualification, self.reserve = qualification, reserve
        self.decisions = {}
        self.calls = []

    def authority(self):
        return dict(deepcopy(self.qualification.granted), reserve_amount=self.reserve)

    def configuration(self):
        return {'model': 'juge/fictif', 'revision': 'juge/fictif-v1'}

    def prepare(self, operation, request):
        return storage._strict_json({'operation': operation['operation_id'], 'request': request})

    def __call__(self, operation, request):
        self.calls.append((deepcopy(operation), deepcopy(request)))
        review = request['outgoing']
        output = review['output']
        witness = next(w for w in self.qualification.last['witnesses'] if w['output'] == output['content'])
        expected = {row['control_id']: row['status'] for row in witness['expected']}
        forced = self.decisions.get(witness['kind'], {})
        findings = [dict(criterion_id=criterion['id'], control_id=control, status=status, attribution='candidate',
                         finding='Constat du juge factice', evidence=[dict(piece_id=output['piece_id'], passage=passage)])
                    for criterion in review['obligations'] + review['eliminatory_errors']
                    for control in criterion['control_ids']
                    for status, passage in [forced.get(control, (expected.get(control, 'PASS'), output['content']))]]
        return {'receipt': {'receipt_id': 'temoin-' + operation['operation_id'],
                            'observed_configuration': {'model': 'juge/fictif-v1'},
                            'resources_seen': [operation['conserved_wire']],
                            'result': dict(findings=findings, measures=[], limits=[], proposed_verdict='INDETERMINE')},
                'cost': {'status': 'KNOWN', 'amount': '0.05', 'currency': 'USD',
                         'source': 'Reçu synthétique de témoin'}}


class ScriptedQualification(QualificationTransport):
    """Chaque appel suit le script : statut HTTP d'incident, exception, ou None pour la réponse normale"""
    INCIDENTS = {401: 'KEY_REJECTED', 402: 'CREDIT_EXHAUSTED', 429: 'RATE_LIMITED'}

    def __init__(self, script, result=None):
        super().__init__(result or {'qualified': True, 'findings': [], 'summary': 'Exemple qualifié'})
        self.script = list(script)

    def __call__(self, operation, request):
        step = self.script.pop(0) if self.script else None
        if step is None:
            return super().__call__(operation, request)
        self.calls.append((deepcopy(operation), deepcopy(request)))
        if isinstance(step, Exception):
            raise step
        status, headers = step if type(step) is tuple else (step, {})
        # Forme rendue par OpenRouterPreparation.__call__ pour une réponse d'erreur reçue
        return {'receipt': {'receipt_id': 'q-' + operation['operation_id'],
                            'observed_configuration': {
                                'incident': self.INCIDENTS.get(status, 'PROVIDER_ERROR'),
                                'http': {'status': status, 'response_headers': headers,
                                         'received_at': prep._now().isoformat()}},
                            'resources_seen': [], 'result': None},
                'cost': {'status': 'UNKNOWN', 'amount': None, 'currency': 'USD', 'source': 'coût INCONNU'}}


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
        self.session, self.csrf, self.token = prep.session(self.store, None, create=True)
        operation_id, _ = prep.submit(self.store, self.session, 'dossier',
            {'action_id': 'create', 'request': 'Organiser les actions de cet atelier entièrement inventé'},
            'a' * 40, Authorized(True, **QualificationTransport.granted))
        response = response_for({'operation_id': operation_id})
        response['receipt']['result']['package']['candidate']['criteria'] = {
            'eliminatory': ['Ne pas inventer une action'],
            'obligations': ['Toutes les actions présentes', 'Responsables conservés'],
            'quality': [{'label': 'Clarté', 'scale': ['excellent', 'acceptable', 'faible'],
                         'favorable': 'excellent'}]}
        response['cost'].update(amount='0.10', currency='USD')
        with self.assertLogs('benchmark.preparation', level='INFO') as journal:
            prep.execute(self.data, operation_id, Authorized(lambda *_: response, **QualificationTransport.granted))
        self.assertIn('PREPARATION_EMITTING', journal.output[0])
        self.assertIn('PREPARATION_RECEIVED', journal.output[-1])
        self.assertNotIn(response['receipt']['result']['reformulation'], '\n'.join(journal.output))
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

    def test_qualification_quote_is_frozen_before_serving_requests(self):
        from tests.test_openrouter_preparation import estimate_for
        transport = assistant.OpenRouterQualification(KEY)
        estimate = estimate_for(transport._profile)
        with patch('benchmark.transports.prices.read_public', return_value=({'context_length': 1000000}, {})), \
                patch('benchmark.transports.prices.forecast', return_value=estimate) as forecast:
            first = transport.quote()
            first['reserve_usd'] = '0'
            self.assertEqual('0.2131072', transport.quote()['reserve_usd'])
            forecast.assert_called_once()

    def test_closed_qualification_intent_is_blocked_without_retry(self):
        transport, operation_id = self.validate(
            {'qualified': True, 'findings': [], 'summary': 'Contrôles prouvés'})
        self.assertEqual('PENDING', prep.view(self.store, self.session, 'dossier')['qualification']['status'])
        transport.granted = None
        with self.assertLogs('benchmark.preparation', level='WARNING') as journal:
            prep.execute_qualification(self.data, operation_id, transport)
        self.assertIn('QUALIFICATION_BLOCKED', journal.output[-1])
        for _ in range(2):
            view = prep.view(self.store, self.session, 'dossier')
            self.assertEqual('BLOCKED', view['qualification']['status'])
            self.assertIn('sans émission', view['qualification']['summary'])
        self.assertEqual([], transport.calls)

    def test_qualification_rejected_before_emission_leaves_no_endless_wait(self):
        transport, operation_id = self.validate(
            {'qualified': True, 'findings': [], 'summary': 'Contrôles prouvés'})
        with patch.object(transport, 'configuration', return_value={'model': 'profil-modifié'}):
            prep.execute_qualification(self.data, operation_id, transport)
        self.assertEqual([], transport.calls)
        self.assertEqual('BLOCKED', prep.view(self.store, self.session, 'dossier')['qualification']['status'])

    def test_profile_frozen_and_strict_answer(self):
        transport = assistant.OpenRouterQualification(KEY)
        from tests.test_openrouter_preparation import estimate_for
        quoted = assistant.configuration(estimate_for(transport._profile), transport._profile)
        self.enterContext(patch.object(transport, 'quote', return_value=quoted))
        self.enterContext(patch.object(transport, 'authority', return_value=deepcopy(QualificationTransport.granted)))
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

    def test_qualification_reserves_its_own_quote(self):
        transport = QualificationTransport({'qualified': True, 'findings': [], 'summary': 'OK'})
        transport.quote = lambda: {**transport.configuration(), 'reserve_usd': '10.8192'}
        _, operation_id, _ = prep.validate_and_qualify(
            self.store, self.session, 'dossier',
            prep.binding('dossier', self.preview['revision'], self.preview['package_sha256']),
            'b' * 40, transport)
        self.assertEqual('10.8192', self.store.inspect_budget('preparation')['reserved'])
        operation = next(row for row in self.store.inspect_operations() if row['operation_id'] == operation_id)
        self.assertEqual('10.8192', operation['requested_configuration']['reserve_usd'])
        prep.execute_qualification(self.data, operation_id, transport)
        self.assertEqual(1, len(transport.calls))
        self.assertTrue(prep.view(self.store, self.session, 'dossier')['qualified'])

    def test_qualification_has_no_additional_daily_cap(self):
        transport = QualificationTransport({'qualified': True, 'findings': [], 'summary': 'OK'})
        transport.quote = lambda: {**transport.configuration(), 'reserve_usd': '50'}
        prep.validate_and_qualify(
            self.store, self.session, 'dossier',
            prep.binding('dossier', self.preview['revision'], self.preview['package_sha256']),
            'b' * 40, transport)
        self.assertEqual('50', self.store.inspect_budget('preparation')['reserved'])
        self.assertEqual([], transport.calls)

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
        self.assertEqual("Les conditions de la comparaison ne sont pas encore fixées. Attendez la fin de la vérification de l’exemple.",
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
        # Le contrat suit le reçu du dernier témoin confirmé : l'un ne s'écrit jamais sans l'autre
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s2_comparison_contracts').fetchone()[0])
        states = {row['operation_id']: row['state'] for row in self.store.inspect_operations()
                  if row['phase'] == 'qualification'}
        self.assertEqual('RECEIVED', states.pop(operation_id))
        self.assertEqual(['AMBIGUOUS'], list(states.values()))
        self.assertFalse(prep.view(self.store, self.session, 'dossier')['qualified'])

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
                          'candidate_pieces', 'judgment_reference', 'reformulated_need', 'clarifications', 'controls'},
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

    def test_contrat_operateur_bloque_refuse_avec_ses_constats_sans_preuve(self):
        """Contrat opérateur bloqué : le refus porte le texte des contrôles non réussis, jamais leurs preuves

        Modes d'échec couverts :
        1. le refus ne porte aucun constat (findings vide) ;
        2. un contrôle réussi est présenté comme un motif de refus ;
        3. la preuve du contrôle, qui contient la référence de jugement, franchit la frontière du demandeur
        """
        qualification.initialize(self.data)
        transport, operation_id = self.validate({'qualified': True, 'findings': [], 'summary': 'Exemple qualifié'})
        prep.execute_qualification(self.data, operation_id, transport)
        reference = self.store._connection.execute("SELECT piece_id FROM pieces WHERE role='judge'").fetchone()[0]
        candidate = qualification.draft(self.store, 'dossier', self.preview['revision'], specification(reference))
        motif = 'Obligation O1 : aucun passage des notes ne la prouve'

        def bloque(contract, resources):
            review = check(contract, resources)
            review['checks'][0].update(status='FAIL', finding=motif)
            return review
        receipt = qualification.qualify(self.store, candidate['contract_sha256'], reviewer=ACTOR, check=bloque)
        self.assertEqual('BLOCKED', receipt['status'])
        snapshot = {'task': {'dossier_id': 'dossier', 'revision': self.preview['revision']}}
        path = '/preparation/dossiers/dossier/campaigns/campagne/conditions'
        with patch.object(campaigns, 'inspect', return_value=snapshot), \
                patch.object(campaigns, 'connection_for') as connection:
            connection.return_value.execute.return_value.fetchone.return_value = (1,)
            with self.assertRaises(prep.Denied) as refused:
                web_api.dispatch(self.store, 'GET', path, self.token, None, 'b' * 40, True)
        affiche = 'Obligation « Action présente » : aucun passage des notes ne la prouve'
        self.assertEqual(('NOT_QUALIFIED', [{'text': affiche}]),
                         (refused.exception.code, refused.exception.findings))
        sortie = storage._strict_json(service.denied_response(refused.exception))
        self.assertIn(affiche, sortie)
        self.assertNotIn(self.store.read_piece(reference).decode(), sortie)
        self.assertNotIn('Omission témoin repérée', sortie)

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
        observed = prep.availability(self.store, transport)
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
        self.assertTrue(prep.availability(self.store, QualificationTransport({}))['admission_open'])

    def test_erreurs_de_qualification_exposent_un_code_et_un_texte_francais(self):
        errors = []
        try:
            prep.validate_and_qualify(self.store, self.session, 'dossier',
                                      prep.binding('dossier', self.preview['revision'], self.preview['package_sha256']),
                                      'b' * 40, None)
        except prep.Denied as error:
            errors.append(error)
        unbound = QualificationTransport({'qualified': True, 'findings': [], 'summary': 'OK'})
        unbound.granted = None
        try:
            prep.validate_and_qualify(self.store, self.session, 'dossier',
                                      prep.binding('dossier', self.preview['revision'], self.preview['package_sha256']),
                                      'b' * 40, unbound)
        except prep.Denied as error:
            errors.append(error)
        responses = [service.denied_response(error) for error in errors]
        self.assertEqual(['QUALIFICATION_UNAVAILABLE', 'ADMISSION_CLOSED'],
                         [response['value']['error_code'] for response in responses])
        self.assertEqual(['La vérification de l’exemple est indisponible pour le moment. Réessayez plus tard.',
                          'Ajoutez ou vérifiez votre clé OpenRouter pour continuer. Aucun appel n’a été lancé.'],
                         [response['value']['error'] for response in responses])

    def operation(self, operation_id):
        return next(o for o in self.store.inspect_operations() if o['operation_id'] == operation_id)

    def assert_not_sent(self, operation_id):
        operation = self.operation(operation_id)
        self.assertEqual(('RECEIVED', {'status': 'NOT_SENT'}, 'KNOWN', '0'),
                         (operation['state'], operation['receipt']['result'],
                          operation['observed_cost']['status'], operation['observed_cost']['amount']))

    def test_stockage_non_verifie_clot_la_verification_et_la_session_peut_relancer(self):
        transport, operation_id = self.validate({'qualified': True, 'findings': [], 'summary': 'OK'})
        # Contrôle par tâche : un fichier orphelin suffit à refuser l'émission
        with patch.object(storage.Store, 'verify_task', return_value=['pieces/orphelin.bin']):
            prep.execute_qualification(self.data, operation_id, transport)
        self.assert_not_sent(operation_id)
        self.assertEqual([], transport.calls)
        retry, second = self.validate({'qualified': True, 'findings': [], 'summary': 'OK'})
        self.assertNotEqual(operation_id, second)
        prep.execute_qualification(self.data, second, retry)
        self.assertEqual(1, len(retry.calls))
        self.assertEqual('QUALIFIED', prep.view(self.store, self.session, 'dossier')['qualification']['status'])

    def test_redemarrage_clot_la_verification_jamais_emise_sauf_restauration(self):
        from benchmark import runtime
        transport, operation_id = self.validate({'qualified': True, 'findings': [], 'summary': 'OK'})
        # Une base restaurée ne prouve pas l'absence d'envoi après la sauvegarde : l'intention reste
        marker = self.data / 'restore.json'
        os.close(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        marker.write_text('{"state":"RESTORED_RECONCILIATION_REQUIRED"}')
        runtime.stop(self.data, self.store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        self.assertEqual('INTENT_RECORDED', self.operation(operation_id)['state'])
        marker.unlink()
        runtime.stop(self.data, self.store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        self.assert_not_sent(operation_id)
        prep.execute_qualification(self.data, operation_id, transport)
        self.assertEqual([], transport.calls)

    def test_recu_verrouille_quelques_secondes_enregistre_sans_second_appel(self):
        held = threading.Event()

        def reader():
            # Une lecture longue garde son verrou partagé au-delà du délai d'attente SQLite de 5 s
            with closing(sqlite3.connect(self.data / 'metadata.sqlite3')) as other:
                other.execute('BEGIN')
                other.execute('SELECT count(*) FROM operations').fetchone()
                held.set()
                time.sleep(7)
                other.execute('COMMIT')

        class Locking(QualificationTransport):
            def __call__(inner, operation, request):
                threading.Thread(target=reader).start()
                held.wait(2)
                return super().__call__(operation, request)

        transport = Locking({'qualified': True, 'findings': [], 'summary': 'OK'})
        _, operation_id, _ = prep.validate_and_qualify(
            self.store, self.session, 'dossier',
            prep.binding('dossier', self.preview['revision'], self.preview['package_sha256']), 'b' * 40, transport)
        started = time.monotonic()
        prep.execute_qualification(self.data, operation_id, transport)
        self.assertGreater(time.monotonic() - started, 5)
        self.assertEqual(1, len(transport.calls))
        operation = self.operation(operation_id)
        self.assertEqual(('RECEIVED', '0.15'), (operation['state'], operation['observed_cost']['amount']))
        self.assertEqual('QUALIFIED', prep.view(self.store, self.session, 'dossier')['qualification']['status'])
        self.assertEqual([], self.store.verify_storage()['orphan_files'])

    def test_verrou_persistant_rend_ambigu_sans_reemission(self):
        transport, operation_id = self.validate({'qualified': True, 'findings': [], 'summary': 'OK'})
        locked = sqlite3.OperationalError('database is locked')
        locked.sqlite_errorcode = sqlite3.SQLITE_BUSY
        with patch.object(storage, 'LOCK_RETRY_DELAYS', (0, 0)), \
                patch.object(storage.Store, '_record_receipt', side_effect=locked) as record:
            prep.execute_qualification(self.data, operation_id, transport)
        self.assertEqual(3, record.call_count)
        self.assertEqual('AMBIGUOUS', self.operation(operation_id)['state'])
        prep.execute_qualification(self.data, operation_id, transport)
        self.assertEqual(1, len(transport.calls))

    def scripted(self, script, execute=True):
        self.clock = [datetime(2026, 10, 2, 12, tzinfo=timezone.utc)]
        self.enterContext(patch.object(prep, '_now', side_effect=lambda: self.clock[0]))
        transport = ScriptedQualification(script)
        _, operation_id, start = self.revalidate(transport)
        self.assertTrue(start)
        if execute:
            prep.execute_qualification(self.data, operation_id, transport)
        return transport, operation_id

    def revalidate(self, transport):
        return prep.validate_and_qualify(
            self.store, self.session, 'dossier',
            prep.binding('dossier', self.preview['revision'], self.preview['package_sha256']), 'b' * 40, transport)

    def delay(self, operation_id):
        return next((d for o, _, _, d in prep.due_qualification_retries(self.store) if o == operation_id), None)

    def page(self):
        from benchmark_web import views
        value = prep.view(self.store, self.session, 'dossier')
        value['availability'] = {'assistant_configured': True, 'admission_open': True, 'can_submit': True, 'reason': 'open'}
        return value['qualification'], views.render(value, 'csrf', '/preparation/dossiers/dossier').decode()

    def assert_no_manual_retry(self, page, operation_id, transport):
        self.assertNotIn('Relancer', page)
        self.assertNotIn('dossier/validation', page)
        self.assertNotIn('Exemple à revoir', page)
        # Une nouvelle validation de la même version ne relance rien
        self.assertEqual((operation_id, False), self.revalidate(transport)[1:])

    def test_incident_fournisseur_relance_seule_puis_exemple_verifie(self):
        transport, first = self.scripted([503])
        qualification, page = self.page()
        self.assertEqual(('PENDING', 'provider', 30), (qualification['status'], qualification['cause'], qualification['retry_in']))
        self.assertIn('Nouvelle tentative automatique dans 30 s', page)
        self.assert_no_manual_retry(page, first, transport)
        # Jamais avant l'échéance, et une seule tentative liée par opération échouée
        self.assertEqual(30, self.delay(first))
        self.assertIsNone(prep.retry_qualification(self.store, first, transport, 'b' * 40))
        self.clock[0] += timedelta(seconds=30)
        second = prep.retry_qualification(self.store, first, transport, 'b' * 40)
        self.assertIsNotNone(second)
        self.assertIsNone(prep.retry_qualification(self.store, first, transport, 'b' * 40))
        self.assertEqual({'retry_of': first, 'attempt': 2}, json.loads(self.operation(second)['resources'][2]))
        prep.execute_qualification(self.data, second, transport)
        view = prep.view(self.store, self.session, 'dossier')
        self.assertTrue(view['qualified'])
        self.assertEqual('QUALIFIED', view['qualification']['status'])
        self.assertEqual(2, len(transport.calls))
        self.assertEqual([], prep.due_qualification_retries(self.store))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_cinq_tentatives_au_plus_selon_le_calendrier_puis_message_sans_bouton(self):
        transport, operation_id = self.scripted([503, 502, 500, 429, 504])
        for delay in (30, 120, 600, 1800):
            self.assertEqual(delay, self.delay(operation_id))
            self.clock[0] += timedelta(seconds=delay - 1)
            self.assertIsNone(prep.retry_qualification(self.store, operation_id, transport, 'b' * 40))
            self.clock[0] += timedelta(seconds=1)
            operation_id = prep.retry_qualification(self.store, operation_id, transport, 'b' * 40)
            prep.execute_qualification(self.data, operation_id, transport)
        self.assertEqual(5, len(transport.calls))
        self.assertEqual(5, json.loads(self.operation(operation_id)['resources'][2])['attempt'])
        qualification, page = self.page()
        self.assertEqual(('BLOCKED', 'provider'), (qualification['status'], qualification['cause']))
        self.assertIn('Vérification impossible pour le moment chez le fournisseur. L’exemple n’est pas en cause.', page)
        self.assertNotIn('la réponse de référence ou les contrôles ne sont pas assez établis', page)
        self.assert_no_manual_retry(page, operation_id, transport)
        self.assertIsNone(self.delay(operation_id))
        self.clock[0] += timedelta(days=1)
        self.assertIsNone(prep.retry_qualification(self.store, operation_id, transport, 'b' * 40))
        self.assertEqual(5, len(transport.calls))

    def test_retry_after_plus_long_que_le_delai_est_respecte(self):
        transport, operation_id = self.scripted([(429, {'Retry-After': '300'})])
        self.assertEqual(300, self.delay(operation_id))
        self.clock[0] += timedelta(seconds=299)
        self.assertIsNone(prep.retry_qualification(self.store, operation_id, transport, 'b' * 40))
        self.clock[0] += timedelta(seconds=1)
        self.assertIsNotNone(prep.retry_qualification(self.store, operation_id, transport, 'b' * 40))

    def test_connexion_impossible_relancee_sans_cout(self):
        transport, operation_id = self.scripted([assistant.NotSent('Connexion à OpenRouter impossible')])
        self.assert_not_sent(operation_id)
        self.assertEqual('CONNECTION_FAILED', self.operation(operation_id)['receipt']['observed_configuration']['incident'])
        qualification, page = self.page()
        self.assertIn('Impossible de joindre OpenRouter, rien n’a été envoyé ni facturé', page)
        self.assert_no_manual_retry(page, operation_id, transport)
        self.assertEqual(30, self.delay(operation_id))
        self.clock[0] += timedelta(seconds=30)
        second = prep.retry_qualification(self.store, operation_id, transport, 'b' * 40)
        prep.execute_qualification(self.data, second, transport)
        self.assertEqual('QUALIFIED', prep.view(self.store, self.session, 'dossier')['qualification']['status'])

    def test_aucune_relance_apres_effet_ambigu(self):
        transport, operation_id = self.scripted([RuntimeError('coupure après envoi')])
        self.assertEqual('AMBIGUOUS', self.operation(operation_id)['state'])
        self.assertIsNone(self.delay(operation_id))
        self.clock[0] += timedelta(hours=1)
        self.assertIsNone(prep.retry_qualification(self.store, operation_id, transport, 'b' * 40))
        self.assertEqual((operation_id, False), self.revalidate(transport)[1:])
        self.assertEqual(1, len(transport.calls))

    def test_cle_refusee_ou_credit_epuise_sans_relance(self):
        for status, word in ((401, 'clé'), (402, 'crédit')):
            with self.subTest(status=status):
                self.setUp()
                transport, operation_id = self.scripted([status])
                qualification, page = self.page()
                self.assertEqual(('BLOCKED', 'key'), (qualification['status'], qualification['cause']))
                self.assertIn(word, qualification['summary'])
                self.assert_no_manual_retry(page, operation_id, transport)
                self.assertIsNone(self.delay(operation_id))

    def retry_context(self, transport):
        timers = {}
        return (transport, None, None, 'b' * 40, timers), timers

    def test_service_programme_la_relance_sans_action_de_l_utilisateur(self):
        transport, operation_id = self.scripted([503], execute=False)
        with patch.object(service.threading, 'Timer') as timer:
            retry, timers = self.retry_context(transport)
            service._qualification_worker(self.data, 'dossier', operation_id, transport, retry)
            self.assertEqual(30, timer.call_args.args[0])
            timer.return_value.start.assert_called_once()
            self.assertEqual({operation_id: timer.return_value}, timers)
            self.clock[0] += timedelta(seconds=30)
            function, arguments = timer.call_args.args[1], timer.call_args.kwargs['args']
            function(*arguments)
        self.assertEqual('QUALIFIED', prep.view(self.store, self.session, 'dossier')['qualification']['status'])
        self.assertEqual(2, len(transport.calls))
        self.assertEqual({}, timers)

    def test_redemarrage_reprogramme_les_relances_dues(self):
        from benchmark import runtime
        transport, first = self.scripted([503])
        # Redémarrage : le minuteur en mémoire est perdu, le démarrage le reprogramme
        with patch.object(service.threading, 'Timer') as timer:
            retry, timers = self.retry_context(transport)
            service._resume_qualification_retries(self.store, self.data, retry)
            service._resume_qualification_retries(self.store, self.data, retry, session_id=self.session)
            self.assertEqual(1, timer.call_count)
            self.assertEqual(30, timer.call_args.args[0])
        # Relance créée puis processus arrêté avant envoi : close sans coût, elle reprend sans consommer de tentative
        self.clock[0] += timedelta(seconds=30)
        second = prep.retry_qualification(self.store, first, transport, 'b' * 40)
        runtime.stop(self.data, self.store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        self.assert_not_sent(second)
        qualification, page = self.page()
        self.assertEqual('PENDING', qualification['status'])
        self.assertNotIn('Exemple à revoir', page)
        self.clock[0] += timedelta(days=1)
        with patch.object(service.threading, 'Timer') as timer:
            retry, timers = self.retry_context(transport)
            service._resume_qualification_retries(self.store, self.data, retry)
            self.assertEqual(0, timer.call_args.args[0])
            timer.call_args.args[1](*timer.call_args.kwargs['args'])
        third = prep._latest_qualification(self.store, self.store._connection, 'dossier', self.preview['revision'])
        self.assertEqual({'retry_of': second, 'attempt': 2}, json.loads(third['resources'][2]))
        self.assertEqual('QUALIFIED', prep.view(self.store, self.session, 'dossier')['qualification']['status'])
        self.assertEqual(2, len(transport.calls))


if __name__ == '__main__':
    unittest.main()
