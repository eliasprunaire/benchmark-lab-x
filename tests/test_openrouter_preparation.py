"""HTTP simulations only; usage fixtures are not evidence of provider access."""
from base64 import b64decode
from contextlib import closing, nullcontext, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import multiprocessing
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from benchmark import preparation as prep, runtime, service, storage
from benchmark_web import views
from benchmark_web.server import serve_web
from benchmark import openrouter_preparation as assistant


KEY = 'fixture-key-never-a-credential'
ESTIMATE = {'channel': 'OpenRouter', 'model_id': assistant.MODEL, 'context_length': 1050000,
            'canonical_slug': assistant.DEFAULT_PROFILE['revision'],
            'assumptions': {'input_tokens': 1050000, 'cached_input_tokens': 0, 'output_tokens': 16384},
            'sources': {key: {'url': 'https://openrouter.ai/api/v1/' + path,
                              'retrieved_at': '2026-09-10T00:00:00+00:00', 'body_sha256': 'b' * 64}
                        for key, path in [('model', 'model/' + assistant.MODEL),
                                          ('endpoints', 'models/' + assistant.MODEL + '/endpoints')]},
            'model_summary': {'pricing_raw': {'prompt': '0.0000002', 'completion': '0.0000008'}},
            'endpoints': [{'model_id': assistant.MODEL, 'tag': tag, 'provider_name': provider,
                           'supported_parameters': ['temperature', 'top_p', 'reasoning', 'max_tokens', 'response_format'],
                           'pricing_raw': {'prompt': '0.0000002', 'completion': '0.0000008'}}
                          for tag, provider in assistant.PROVIDERS.items()]}
RESERVE = assistant.reservation(ESTIMATE)
ROUTE = {'requested': assistant.MODEL, 'strategy': 'direct', 'attempt': 1,
         'endpoints': {'available': [{'provider': next(iter(assistant.PROVIDERS.values())),
                                      'model': assistant.MODEL, 'selected': True}]}}
SYNTHETIC_PROFILE = Path(__file__).resolve().parent / 'fixtures' / 'synthetic-preparation.profile.json'


def estimate_for(profile):
    model = profile['model']
    canonical = profile['revision']
    return {'channel': 'OpenRouter', 'model_id': model, 'context_length': 1000000, 'canonical_slug': canonical,
            'assumptions': {'input_tokens': 1000000, 'cached_input_tokens': 0,
                            'output_tokens': profile['parameters']['max_tokens']},
            'sources': {key: {'url': 'https://openrouter.ai/api/v1/' + path,
                              'retrieved_at': '2026-09-10T00:00:00+00:00', 'body_sha256': 'b' * 64}
                        for key, path in [('model', 'model/' + model),
                                          ('endpoints', 'models/' + model + '/endpoints')]},
            'model_summary': {'pricing_raw': {'prompt': '0.0000002', 'completion': '0.0000008'}},
            'endpoints': [{'model_id': model, 'tag': route['tag'], 'provider_name': route['provider_name'],
                           'supported_parameters': list(profile['required_capabilities']),
                           'pricing_raw': {'prompt': '0.0000002', 'completion': '0.0000008'}}
                          for route in profile['routes']]}

NEED = 'Je passe trop de temps à retrouver ce qui a été décidé en réunion et qui doit faire quoi. Je voudrais comparer des modèles pour m’aider.'
CLARIFICATION = 'Association entièrement fictive organisant un événement ; notes françaises ; décisions, actions, responsables, échéances et informations à confirmer.'
CORRECTION = 'Garde les mêmes notes et distingue clairement les propositions des décisions validées. Pour les responsables absents, indique à confirmer.'
NOTES = ('Association Les Lanternes, réunion du 3 octobre 2027.\n'
         'P1 : Mila propose un concert ; aucune décision prise.\n'
         'D1 : Le comité valide un atelier le 20 novembre.\n'
         'A1 : Noé prépare les affiches. Échéance initiale : 10 octobre.\n'
         'D2 : Échéance des affiches reportée au 14 octobre.\n'
         'A2 : Réserver les tables avant le 18 octobre ; responsable non désigné.\n')
REFERENCE = ('P1 : concert proposé, non validé. D1 : atelier validé. '
             'A1 et D2 : Noé, affiches, échéance 14 octobre, remplace le 10. '
             'A2 : tables, 18 octobre, responsable à confirmer. '
             'Toute reformulation fidèle est recevable. Ne pas deviner le responsable.')


def result(stage='preview'):
    return {'stage': stage,
            'explanation': {'clarification': 'Quelles notes et quel relevé souhaitez-vous comparer ?',
                            'preview': 'Exemple fictif à relire.',
                            'scope_confirmation': 'Je ne peux pas envoyer de rappels. Souhaitez-vous préparer leur texte ?',
                            'suspended': 'Préparation suspendue.'}[stage],
            'reformulation': 'Extraire les décisions et actions des notes françaises.',
            'fictional_parameters': {'association': 'Les Lanternes, inventée'},
            'package': {'candidate': {'instruction': 'Relever décisions, propositions, actions, responsables et échéances ; signaler les inconnues.',
                        'deliverables': ['Relevé structuré'], 'criteria': ['Respect des notes'],
                        'acceptable_ambiguities': ['Reformulations fidèles'],
                        'pieces': [{'name': 'notes.txt', 'content': NOTES}]},
                        'internal': {'human_work': 'Relire et confirmer les inconnues',
                                     'limits': ['Exercice fictif unique, sans action externe']},
                        'judgment': {'pieces': [{'name': 'reference.txt', 'content': REFERENCE}]}}
                       if stage == 'preview' else None}


def http_body(value=None, **updates):
    return storage._strict_json({'id': 'fixture-provider-id', 'model': assistant.MODEL,
        'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
                    'content': storage._strict_json(value if value is not None else result())}}],
        'openrouter_metadata': ROUTE, 'usage': {'cost': 0.000202, 'prompt_tokens': 1000, 'completion_tokens': 200, 'total_tokens': 1200,
                  'prompt_tokens_details': {'cached_tokens': 400}}, **updates}).encode()


def executor_process(data, sock, entered=None, clock=None):
    connection = Mock()
    connection.getresponse.return_value.status = 200
    connection.getresponse.return_value.length = 0
    connection.getresponse.return_value.read.return_value = http_body(result('clarification'), usage=None)
    if entered is not None:
        def interrupted_response():
            entered.set()
            while True:
                time.sleep(1)
        connection.getresponse.side_effect = interrupted_response
    time_patch = (patch.object(prep, '_now', side_effect=lambda: datetime.fromtimestamp(clock.value, timezone.utc))
                  if clock is not None else nullcontext())
    with time_patch, patch.dict(os.environ, {'OPENROUTER_API_KEY': KEY}), patch.object(assistant, 'HTTPSConnection', return_value=connection), \
            patch.object(service, 'release_identity', return_value='a' * 40):
        runtime.main(['executor', '--data', str(data), '--socket', str(sock),
                      '--preparation-assistant', assistant.ASSISTANT])


class OpenRouterPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='openrouter-fixture-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.data = self.home / 'private'
        storage.initialize(self.data)
        storage.initialize_preparation(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        self.store.create_budget('fixture', '100', 'USD')
        self.authority = dict(authority_id='FICTIONAL_HTTP_ONLY', budget_id='fixture',
                              reserve_amount=RESERVE, requested_configuration=assistant.configuration(ESTIMATE))
        prep.admit(self.store, self.authority)
        self.session, self.csrf, self.token = prep.session(self.store, None, create=True)
        self.transport = assistant.OpenRouterPreparation(KEY)
        self.http = Mock()
        self.http.getresponse.return_value.status = 200
        self.http.getresponse.return_value.length = 0
        self.raw = http_body()
        self.http.getresponse.return_value.read.return_value = self.raw
        patched = patch.object(assistant, 'HTTPSConnection', return_value=self.http)
        self.connection = patched.start()
        self.addCleanup(patched.stop)

    def submit(self, **fields):
        body = fields or dict(action_id='create', request=NEED)
        return prep.submit(self.store, self.session, 'd', body, 'a' * 40, self.transport)[0]

    def execute(self, **fields):
        operation = self.submit(**fields)
        prep.execute(self.data, operation, self.transport)
        return next(op for op in self.store.inspect_operations() if op['operation_id'] == operation), prep.view(self.store, self.session, 'd')

    def test_exact_wire_is_durable_before_http_and_unknown_cost_keeps_reserve(self):
        def at_request(method, path, *, body, headers):
            with closing(storage.Store(self.data)) as reader:
                operation = reader.inspect_operations()[0]
                self.assertEqual('EMISSION_POSSIBLE', operation['state'])
                self.assertEqual(body, operation['resources'][1].encode())
                self.assertEqual(RESERVE, reader.inspect_budget('fixture')['reserved'])
            self.assertEqual(('POST', assistant.PATH), (method, path))
            self.assertEqual('Bearer ' + KEY, headers['Authorization'])
            self.assertNotIn(KEY.encode(), body)
            sent = json.loads(body)
            self.assertEqual(assistant.MODEL, sent['model'])
            self.assertEqual(assistant.PARAMETERS, {k: sent[k] for k in assistant.PARAMETERS})
            self.assertNotIn('tools', sent)
            self.assertNotIn('thinking', sent)
            self.assertNotIn('request_id', sent)
            self.assertEqual({'effort': 'medium'}, sent['reasoning'])
            self.assertEqual(16384, sent['max_tokens'])
            self.assertNotIn('max_tokens', sent['reasoning'])
            self.assertFalse(sent['provider']['allow_fallbacks'])
            self.assertEqual(list(assistant.PROVIDERS), sent['provider']['only'])
            self.assertEqual(sent['provider']['only'], sent['provider']['order'])
            self.assertTrue(sent['provider']['require_parameters'])
            self.assertEqual('enabled', headers['X-OpenRouter-Metadata'])
        self.http.request.side_effect = at_request
        operation_id = self.submit()
        accepted = self.store.inspect_operations()[0]
        self.assertEqual('INTENT_RECORDED', accepted['state'])
        self.assertEqual(2, len(accepted['resources']))
        self.http.request.assert_not_called()
        prep.execute(self.data, operation_id, self.transport)
        operation = self.store.inspect_operations()[0]
        view = prep.view(self.store, self.session, 'd')
        observed = operation['receipt']['observed_configuration']
        self.assertEqual(self.raw, b64decode(observed['http']['body_base64']))
        self.assertEqual(assistant.MODEL, observed['model'])
        self.assertIsNone(observed['parameters'])
        self.assertEqual(ROUTE, observed['route'])
        self.assertEqual('OpenAI', observed['provider'])
        self.assertEqual('OpenRouter', operation['requested_configuration']['provider'])
        self.assertEqual('0.000202', observed['consumption']['amount'])
        self.assertFalse(observed['consumption']['invoice'])
        self.assertEqual('KNOWN', operation['observed_cost']['status'])
        self.assertEqual('preview', view['stage'])
        self.assertEqual([NOTES.encode()], [prep.piece_bytes(self.store, self.session, 'd', view['revision'], p['id'])
                                          for p in view['package']['pieces']])
        self.assertNotIn(REFERENCE, views.render(view, self.csrf).decode())
        self.assertEqual('0', self.store.inspect_budget('fixture')['reserved'])
        self.http.request.side_effect = None
        self.http.getresponse.return_value.read.return_value = http_body(usage=None)
        unknown, later = self.execute(action_id='unknown', revision=2, kind='correct', message=CORRECTION)
        budget = self.store.inspect_budget('fixture')
        self.assertEqual(RESERVE, budget['reserved'])
        self.assertEqual('0.000202', budget['spent'])
        self.assertEqual([unknown['operation_id']], budget['unknown_cost_operations'])
        self.submit(action_id='next', revision=later['revision'], kind='clarify', message=CLARIFICATION)
        self.assertEqual([unknown['operation_id']], self.store.inspect_budget('fixture')['unknown_cost_operations'])
        prep.execute(self.data, unknown['operation_id'], self.transport)
        self.assertEqual(2, self.http.request.call_count)
        self.assertEqual(2, self.connection.call_count)
        self.connection.assert_called_with(assistant.HOST, timeout=assistant.TIMEOUT_SECONDS)
        self.assertNotIn(KEY, storage._strict_json(operation))

    def test_full_scenario_same_budget_preserves_notes_agreements_and_validation(self):
        self.http.getresponse.return_value.read.return_value = http_body(result('clarification'))
        _, clarified = self.execute()
        self.assertEqual('clarification', clarified['stage'])
        self.assertIn('Quelles notes', clarified['explanation'])
        self.http.getresponse.return_value.read.return_value = http_body()
        _, before = self.execute(action_id='clarify', revision=2, kind='clarify', message=CLARIFICATION)
        prep.validate(self.store, self.session, 'd', prep.binding('d', 3, before['package_sha256']))
        changed = result()
        changed['package']['candidate']['criteria'].append('Propositions séparées des décisions ; responsable absent à confirmer')
        self.http.getresponse.return_value.read.return_value = http_body(changed)
        operation, after = self.execute(action_id='correct', revision=3, kind='correct', message=CORRECTION)
        request = json.loads(operation['resources'][0])
        self.assertEqual(NOTES, request['pieces_seen'][0]['content'])
        self.assertNotIn(REFERENCE, operation['resources'][1])
        self.assertEqual(before['payload']['validated_assumptions'], after['payload']['validated_assumptions'])
        self.assertEqual(NEED, after['payload']['request'])
        self.assertEqual(NOTES.encode(), self.store.read_piece(after['package']['pieces'][0]['id']))
        self.assertIsNone(after['validation'])
        self.assertIsNotNone(prep.view(self.store, self.session, 'd', 3)['validation'])
        self.assertFalse(after['qualified'])
        prep.validate(self.store, self.session, 'd', prep.binding('d', 4, after['package_sha256']))
        self.http.getresponse.return_value.read.return_value = http_body(result('scope_confirmation'))
        operation, view = self.execute(action_id='limit', revision=4, kind='clarify', message='Envoie aussi les rappels aux participants.')
        self.assertEqual('scope_confirmation', view['stage'])
        self.assertIn('ne peux pas envoyer', view['explanation'])
        self.assertIsNone(view['package'])
        self.assertEqual(4, self.http.request.call_count)
        self.assertEqual('RECEIVED', operation['state'])
        self.assertEqual('0.000808', self.store.inspect_budget('fixture')['spent'])
        self.assertEqual('0', self.store.inspect_budget('fixture')['reserved'])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_bad_output_keeps_raw_receipt_without_orphan_piece(self):
        broken = result()
        broken['package']['candidate']['pieces'][0]['role'] = 'judge'
        self.http.getresponse.return_value.read.return_value = http_body(broken)
        operation, view = self.execute()
        self.assertEqual('RECEIVED', operation['state'])
        self.assertEqual('suspended', view['stage'])
        self.assertIsNone(prep.admission(self.store))
        self.assertEqual(http_body(broken), b64decode(operation['receipt']['observed_configuration']['http']['body_base64']))
        self.assertEqual([], self.store.verify_storage()['orphan_files'])

    def test_only_extra_null_root_fields_are_ignored_before_strict_publication(self):
        valid = result()
        variants = [(dict(valid, package_note=None, authority=None), True),
                    (dict(result('clarification'), package_note=None), True)]
        for value in ('instruction', False, 0, '', [], {}):
            variants.append((dict(valid, package_note=value), False))
        for field in valid:
            missing = deepcopy(valid)
            del missing[field]
            variants.append((dict(missing, package_note=None), False))
            if field != 'package':
                variants.append((dict(valid, **{field: None}, package_note=None), False))
        variants.append((dict(valid, package=None, package_note=None), False))
        for path in ('package', 'piece'):
            nested = deepcopy(valid)
            target = nested['package'] if path == 'package' else nested['package']['candidate']['pieces'][0]
            target['package_note'] = None
            variants.append((dict(nested, package_note=None), False))
        variants.extend([(dict(valid, stage='invalid', package_note=None), False), ([], False)])
        for index, (value, accepted) in enumerate(variants):
            with self.subTest(index=index):
                prep.admit(self.store, self.authority)
                dossier = 'null-field-' + str(index)
                raw = http_body(value)
                self.http.getresponse.return_value.read.return_value = raw
                op_id, started = prep.submit(self.store, self.session, dossier,
                    dict(action_id='create', request=NEED), 'a' * 40, self.transport)
                self.assertTrue(started)
                prep.execute(self.data, op_id, self.transport)
                view = prep.view(self.store, self.session, dossier)
                op = next(row for row in self.store.inspect_operations() if row['operation_id'] == op_id)
                self.assertEqual(value['stage'] if accepted else 'suspended', view['stage'])
                self.assertEqual(raw, b64decode(op['receipt']['observed_configuration']['http']['body_base64']))
                self.assertEqual('KNOWN', op['observed_cost']['status'])
                self.assertIsNone(view['validation'])
                self.assertFalse(view['qualified'])
                if accepted:
                    expected = {key: value[key] for key in valid}
                    self.assertEqual(expected, op['receipt']['result'])
                    if value['package']:
                        piece = view['package']['pieces'][0]
                        self.assertEqual(NOTES.encode(), prep.piece_bytes(self.store, self.session, dossier, view['revision'], piece['id']))
                else:
                    self.assertIsNone(prep.admission(self.store))
                    self.assertIsNone(view['package'])
        self.assertEqual(len(variants), self.http.request.call_count)
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_empty_length_response_preserves_reasoning_usage_without_retry(self):
        usage = {'prompt_tokens': 1000, 'completion_tokens': 8192,
                 'completion_tokens_details': {'reasoning_tokens': 8155}, 'cost': '0.004'}
        raw = http_body(choices=[{'finish_reason': 'length',
                        'message': {'role': 'assistant', 'content': ''}}], usage=usage)
        self.http.getresponse.return_value.read.return_value = raw
        operation, view = self.execute()
        self.assertEqual('suspended', view['stage'])
        self.assertIsNone(prep.admission(self.store))
        self.assertEqual('RECEIVED', operation['state'])
        self.assertEqual('0.004', operation['observed_cost']['amount'])
        observed = operation['receipt']['observed_configuration']
        self.assertEqual(usage, observed['consumption']['usage'])
        self.assertEqual(raw, b64decode(observed['http']['body_base64']))
        self.assertNotIn('indicative_cost', view)
        prep.execute(self.data, operation['operation_id'], self.transport)
        self.assertEqual(1, self.http.request.call_count)

    def test_truncation_wrong_model_tools_non_json_and_http_errors_are_not_retried(self):
        operation_id = self.submit()
        operation = self.store.inspect_operations()[0]
        request = json.loads(operation['resources'][0])
        closed = prep._closed_preparation_request(request)
        closed_op = prep._closed_preparation_operation(operation, conserved_wire=operation['resources'][1],
                                                      state='EMISSION_POSSIBLE')
        wire = self.transport.prepare(prep._closed_preparation_operation(operation), closed)
        self.assertEqual(wire, operation['resources'][1])
        choices = [{'finish_reason': 'length', 'message': {'role': 'assistant', 'content': '{}'}}]
        tools = [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{}', 'tool_calls': [{'type': 'function'}]}}]
        for status, raw in [(200, b'not json'), (200, b'{"usage":NaN}'), (200, b'{"model":"x","model":"y"}'), (200, http_body(model='glm-5.3')),
                            (200, http_body(choices=choices)), (200, http_body(choices=tools)),
                            (429, b'{"error":"rate limit"}'), (302, b'redirect'),
                            (200, b' ' * (assistant.MAX_RESPONSE_BYTES + 1))]:
            with self.subTest(status=status, raw_size=len(raw)):
                self.http.reset_mock()
                self.http.getresponse.return_value.status = status
                self.http.getresponse.return_value.read.return_value = raw
                response = self.transport(closed_op, closed)
                self.assertIsNone(response['receipt']['result'])
                self.assertEqual('KNOWN' if raw in (http_body(choices=choices), http_body(choices=tools), http_body(model='glm-5.3')) else 'UNKNOWN', response['cost']['status'])
                self.assertEqual(1, self.http.request.call_count)

        self.http.getresponse.return_value.status = 200
        self.http.getresponse.return_value.length = 17
        self.http.getresponse.return_value.read.return_value = http_body()
        response = self.transport(closed_op, closed)
        self.assertEqual('UNKNOWN', response['cost']['status'])
        self.assertIsNone(response['receipt']['result'])
        self.assertFalse(response['receipt']['observed_configuration']['http']['complete'])

    def test_reported_cost_is_exact_and_missing_cost_has_no_tariff_fallback(self):
        for usage in [None, {}, {'cost': None}, {'cost': -1}, {'cost': True}, {'cost': 'NaN'},
                      {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}]:
            measured = assistant.consumption({'usage': usage})
            self.assertIsNone(measured['amount'])
            self.assertEqual('UNKNOWN', measured['status'])
        self.assertEqual('0', assistant.consumption({'usage': {'cost': 0}})['amount'])
        self.assertEqual('0.123456789012345678901', assistant.consumption({'usage': {'cost': '0.123456789012345678901'}})['amount'])
        # A reported debit is independent from optional token breakdowns and upstream cost
        measured = assistant.consumption({'usage': {'cost': '0.02', 'cost_details': {'upstream_inference_cost': 99}}})
        self.assertEqual('0.02', measured['amount'])
        self.assertEqual('USD', measured['currency'])
        self.http.getresponse.return_value.read.return_value = http_body(openrouter_metadata=None)
        operation, _ = self.execute()
        observed = operation['receipt']['observed_configuration']
        self.assertIsNone(observed['route'])
        self.assertIsNone(observed['provider'])
        self.assertEqual('KNOWN', operation['observed_cost']['status'])

    def test_timeout_keeps_intention_reserve_and_no_replay_after_restart(self):
        self.http.getresponse.side_effect = TimeoutError('private provider error ' + KEY)
        operation, view = self.execute()
        self.assertEqual('AMBIGUOUS', operation['state'])
        self.assertEqual(2, len(operation['resources']))
        self.assertEqual('suspended', view['stage'])
        runtime.stop(self.data, self.store, 'FIXTURE_RESTART', after_process_exit=True)
        prep.execute(self.data, operation['operation_id'], self.transport)
        self.assertEqual(1, self.http.request.call_count)
        self.assertEqual(RESERVE, self.store.inspect_budget('fixture')['reserved'])
        self.assertNotIn(KEY, storage._strict_json(self.store.inspect_operations()))

    def test_configuration_budget_and_size_refuse_before_emission(self):
        closed = prep._closed_preparation_request(dict(
            message='x', kind='create', payload=dict(request='x', reformulation='', clarifications=[],
                                                     validated_assumptions=[], fictional_parameters={}), package=None))
        for change in ['model', 'size', 'format', 'profile', 'revision', 'parameters', 'system',
                       'routes', 'timeout', 'reserve', 'capabilities', 'listing']:
            with self.subTest(change=change):
                operation = {'operation_id': 'fixture', 'requested_configuration': assistant.configuration(ESTIMATE),
                             'phase': 'preparation', 'state': 'INTENT_RECORDED'}
                request = deepcopy(closed)
                requested = operation['requested_configuration']
                if change == 'model': requested['model'] = 'glm-5.3'
                if change == 'size': request['outgoing']['message'] = 'x' * assistant.MAX_REQUEST_BYTES
                if change == 'format': request = dict(outgoing_format='legacy', outgoing=request['outgoing'])
                if change == 'profile': requested['profile_sha256'] = '0' * 64
                if change == 'revision': requested['revision'] = assistant.MODEL + '-20991231'
                if change == 'parameters': requested['parameters']['temperature'] = 0
                if change == 'system': requested['prompt_sha256'] = '0' * 64
                if change == 'routes': requested['routes'] = [{'tag': 'outside/fp8', 'provider_name': 'Outside'}]
                if change == 'timeout': requested['timeout_seconds'] = 1
                if change == 'reserve': requested['reserve_usd'] = '1'
                if change == 'capabilities':
                    requested['reservation_estimate']['endpoints'][0]['supported_parameters'] = ['temperature']
                if change == 'listing': requested['reservation_estimate']['model_id'] = 'openrouter/auto'
                with self.assertRaises(ValueError):
                    self.transport.prepare(operation, request)
        self.authority['requested_configuration']['model'] = 'glm-5.3'
        prep.admit(self.store, self.authority)
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual([], self.store.inspect_operations())
        self.assertEqual('0', self.store.inspect_budget('fixture')['reserved'])
        self.assertEqual(self.authority, prep.admission(self.store))
        self.http.request.assert_not_called()

    def test_usd_envelope_refuses_before_http(self):
        self.store.create_budget('other', '100', 'TEST')
        self.store.create_budget('wide', '101', 'USD')
        cases = [('reserve', dict(reserve_amount='0')),
                 ('currency', dict(budget_id='other')),
                 ('limit', dict(budget_id='wide'))]
        for name, change in cases:
            with self.subTest(change=name):
                prep.close_admission(self.store)
                prep.admit(self.store, dict(self.authority, **change))
                self.http.request.reset_mock()
                with self.assertRaises(ValueError):
                    self.submit(action_id='case-' + name, request=NEED)
                self.http.request.assert_not_called()
                self.assertEqual([], self.store.inspect_operations())

    def test_divergent_package_fingerprint_refuses_before_prepare(self):
        _, view = self.execute()
        raw, digest = self.store._connection.execute(
            'SELECT package_json, package_sha256 FROM s2_revisions WHERE dossier_id=? AND revision=?',
            ('d', view['revision'])).fetchone()
        package = json.loads(raw)
        package['instruction'] = 'Paquet altéré'
        self.store._connection.execute(
            'UPDATE s2_revisions SET package_json=? WHERE dossier_id=? AND revision=?',
            (storage._strict_json(package), 'd', view['revision']))
        self.assertEqual(digest, self.store._connection.execute(
            'SELECT package_sha256 FROM s2_revisions WHERE dossier_id=? AND revision=?',
            ('d', view['revision'])).fetchone()[0])
        prepared = []
        original = self.transport.prepare
        self.transport.prepare = lambda *args, **kwargs: prepared.append(True) or original(*args, **kwargs)
        self.http.request.reset_mock()
        with self.assertRaises(storage.IntegrityError):
            self.submit(action_id='tamper', revision=view['revision'], kind='correct', message=CORRECTION)
        self.assertEqual([], prepared)
        self.http.request.assert_not_called()

    def test_reservation_uses_model_prices_without_provider_price_requirements(self):
        estimate = deepcopy(ESTIMATE)
        for endpoint in estimate['endpoints']:
            endpoint['pricing_raw'] = None
        self.assertEqual(RESERVE, assistant.reservation(estimate))
        estimate['model_summary']['pricing_raw'] = None
        self.assertIsNone(assistant.reservation(estimate))
        self.authority['requested_configuration'] = assistant.configuration(estimate)
        prep.admit(self.store, self.authority)
        operation, view = self.execute()
        self.assertEqual('preview', view['stage'])
        self.assertIsNone(view['indicative_cost'])
        self.assertEqual(RESERVE, operation['reserved_amount'])
        self.assertEqual('KNOWN', operation['observed_cost']['status'])


    def test_native_fallback_after_429_is_accepted_without_another_http_call(self):
        self.http.getresponse.return_value.read.return_value = http_body(openrouter_metadata={**ROUTE, 'strategy': 'fallback', 'attempt': 2,
            'attempts': [{'provider': 'CoreWeave', 'model': assistant.MODEL, 'status': 429},
                         {'provider': 'Modal', 'model': assistant.MODEL, 'status': 200}]})
        operation, view = self.execute()
        self.assertEqual('preview', view['stage'])
        self.assertEqual(2, operation['receipt']['observed_configuration']['route']['attempt'])
        self.assertEqual('KNOWN', operation['observed_cost']['status'])
        self.assertIsNotNone(prep.admission(self.store))
        self.assertEqual(1, self.http.request.call_count)

    def test_canonical_model_is_attributed_but_another_revision_or_endpoint_is_rejected(self):
        canonical = ESTIMATE['canonical_slug']
        route = deepcopy(ROUTE)
        route['attempt'] = 4
        route['endpoints']['available'][0]['model'] = canonical
        self.http.getresponse.return_value.read.return_value = http_body(model=canonical, openrouter_metadata=route)
        operation, view = self.execute()
        self.assertEqual('preview', view['stage'])
        self.assertEqual(canonical, operation['receipt']['observed_configuration']['model'])
        self.assertIsNotNone(operation['receipt']['observed_configuration']['routing_limit'])
        for index, updates in enumerate([{'model': assistant.MODEL + '-20260827'},
                {'openrouter_metadata': {**ROUTE, 'attempts': [{'provider': 'Modal', 'model': assistant.MODEL + '-20260827'}]}},
                {'openrouter_metadata': {**ROUTE, 'attempts': [{'provider': 'Modal', 'tag': 'outside/fp8', 'model': assistant.MODEL}]}}]):
            prep.admit(self.store, self.authority)
            self.http.getresponse.return_value.read.return_value = http_body(**updates)
            operation, view = self.execute(action_id='different-' + str(index), revision=view['revision'], kind='correct', message=CORRECTION)
            self.assertEqual('suspended', view['stage'])
            self.assertEqual('KNOWN', operation['observed_cost']['status'])
        self.assertEqual(4, self.http.request.call_count)

    def test_unmapped_provider_name_is_unknown_not_an_invented_alias(self):
        route = deepcopy(ROUTE)
        route['endpoints']['available'][0]['provider'] = 'NovitaAI'
        self.http.getresponse.return_value.read.return_value = http_body(openrouter_metadata=route)
        operation, view = self.execute()
        self.assertEqual('preview', view['stage'])
        observed = operation['receipt']['observed_configuration']
        self.assertIsNone(observed['provider'])
        self.assertEqual('NovitaAI', observed['route']['endpoints']['available'][0]['provider'])

    def test_429_retains_only_safe_identifiers_and_no_cost_or_retry_is_invented(self):
        response = self.http.getresponse.return_value
        response.status = 429
        response.read.return_value = b'{"error":{"code":429,"message":"fixture upstream pool busy"}}'
        response.getheader.side_effect = {'X-Generation-Id': 'gen-fixture-error', 'Retry-After': '30'}.get
        operation, view = self.execute()
        observed = operation['receipt']['observed_configuration']
        self.assertEqual('gen-fixture-error', observed['generation_id'])
        self.assertEqual({'X-Generation-Id': 'gen-fixture-error', 'Retry-After': '30'}, observed['http']['response_headers'])
        self.assertEqual('UNKNOWN', operation['observed_cost']['status'])
        self.assertEqual('suspended', view['stage'])
        self.assertEqual(RESERVE, self.store.inspect_budget('fixture')['reserved'])
        self.assertEqual(1, self.http.request.call_count)
        self.assertEqual({'X-Generation-Id', 'Retry-After'}, {c.args[0] for c in response.getheader.call_args_list})

    def test_key_absent_default_transport_and_reflected_key(self):
        with patch.dict(os.environ, {'ZAI_API_KEY': KEY}, clear=True), patch.object(service, 'serve_executor') as executor, \
                patch.object(service, 'release_identity', return_value='a' * 40), redirect_stdout(io.StringIO()):
            arguments = ['executor', '--data', str(self.data), '--socket', str(self.home / 'executor.sock')]
            self.assertEqual(78, runtime.main(arguments + ['--preparation-assistant', assistant.ASSISTANT]))
            executor.assert_not_called()
            self.http.request.assert_not_called()
            self.assertEqual(0, runtime.main(arguments))
            self.assertIsNone(executor.call_args.kwargs['transport'])
        reflected = result()
        reflected['explanation'] = KEY
        self.http.getresponse.return_value.read.return_value = http_body(reflected)
        operation, view = self.execute()
        self.assertNotIn(KEY, storage._strict_json(operation))
        self.assertTrue(operation['receipt']['observed_configuration']['http']['credential_redacted'])
        self.assertEqual('suspended', view['stage'])

    def test_runtime_web_executor_mapping_and_web_cannot_select_transport(self):
        public = self.home / 'public'
        public.mkdir()
        sock = self.home / 'executor.sock'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        context = multiprocessing.get_context('spawn')
        children = []
        def stop_children():
            for process in reversed(children):
                if process.is_alive(): process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join()
        self.addCleanup(stop_children)
        clock = context.Value('d', time.time())
        for target, args in [(executor_process, (self.data, sock, None, clock)),
                             (serve_web, ('127.0.0.1', port, public, sock, 'a' * 40))]:
            process = context.Process(target=target, args=args)
            process.start()
            children.append(process)
        base = f'http://127.0.0.1:{port}'
        def call(path, body=None):
            headers = {'Accept': 'application/json', 'Cookie': 'benchmark_session=' + self.token}
            if body is not None: headers['Content-Type'] = 'application/json'
            try:
                response = urlopen(Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=headers), timeout=2)
            except HTTPError as error:
                response = error
            with response:
                return response.status, json.loads(response.read())
        deadline = time.monotonic() + 10
        while True:
            try:
                if call('/readyz')[0] == 200: break
            except OSError: pass
            if time.monotonic() > deadline: self.fail('local processes not ready')
            time.sleep(.02)
        self.assertIsNone(prep.admission(self.store))
        prep.admit(self.store, self.authority)
        body = dict(csrf_token=self.csrf, dossier_id='d', action_id='http', request=NEED)
        missing_source = service.preparation_request(sock, 'POST', '/preparation/dossiers', self.token, body)
        self.assertEqual((400, 'SOURCE_MISSING'),
                         (missing_source['status'], missing_source['value']['error_code']))
        self.assertEqual(400, call('/preparation/dossiers', {
            **body, 'dossier_id': 'too-large', 'action_id': 'large', 'request': 'x' * 65536})[0])
        self.assertEqual([], self.store.inspect_operations())
        self.assertEqual('0', self.store.inspect_budget('fixture')['reserved'])
        self.assertEqual(self.authority, prep.admission(self.store))
        self.assertEqual([], self.store._connection.execute('SELECT * FROM s2_actions').fetchall())
        self.assertEqual([], self.store._connection.execute('SELECT * FROM s2_dossiers').fetchall())
        self.assertEqual(400, call('/preparation/dossiers', {**body, 'model': 'other'})[0])
        code, accepted = call('/preparation/dossiers', body)
        self.assertEqual(202, code)
        deadline = time.monotonic() + 5
        while True:
            code, view = call('/preparation/dossiers/d')
            if view['stage'] == 'clarification': break
            if time.monotonic() > deadline: self.fail('simulated response not published')
            time.sleep(.02)
        self.assertIn('Quelles notes', view['explanation'])
        self.assertEqual('UNKNOWN', view['observed_cost']['status'])
        self.assertEqual(202, call('/preparation/dossiers', body)[0])
        self.assertEqual(1, len(self.store.inspect_operations()))
        clock.value += 30
        code, _ = call('/preparation/dossiers/d/messages', dict(csrf_token=self.csrf, action_id='next', revision=2,
                                                              kind='clarify', message=CLARIFICATION))
        self.assertEqual(202, code)

    def test_indicative_model_cost_is_distinct_from_charge_and_missing_data(self):
        usage = {'prompt_tokens': 1000, 'completion_tokens': 200,
                 'completion_tokens_details': {'reasoning_tokens': 150},
                 'prompt_tokens_details': {'cached_tokens': 400}, 'cost': '0.009'}
        self.http.getresponse.return_value.read.return_value = http_body(usage=usage)
        operation, view = self.execute()
        estimate = view['indicative_cost']
        self.assertEqual('0.0003600', estimate['token_subtotal_usd'])
        self.assertEqual({'prompt': 1000, 'completion': 200}, estimate['quantities'])
        self.assertEqual({key: value for key, value in ESTIMATE['sources']['model'].items()
                          if key != 'body_sha256'}, estimate['source'])
        self.assertEqual('0.009', operation['observed_cost']['amount'])
        self.assertEqual('0.009', self.store.inspect_budget('fixture')['spent'])
        page = views.render(view, self.csrf).decode()
        self.assertIn('Estimation indicative', page)
        self.assertIn('ce montant n’est pas une facture', page)
        self.assertIn('0.0003600 USD', page)
        self.assertIn('0.009 USD', page)
        for invalid in (None, {}, {'prompt_tokens': 0},
                        {'prompt_tokens': False, 'completion_tokens': 1},
                        {'prompt_tokens': -1, 'completion_tokens': 1},
                        {'prompt_tokens': '1000', 'completion_tokens': 1}):
            self.assertIsNone(assistant.openrouter_prices.indication(ESTIMATE, invalid))
        for pricing in (None, {}, {'prompt': '0'}, {'prompt': '-1', 'completion': '0'},
                        {'prompt': 'NaN', 'completion': '0'}):
            estimate = deepcopy(ESTIMATE)
            estimate['model_summary']['pricing_raw'] = pricing
            self.assertIsNone(assistant.openrouter_prices.indication(estimate, usage))
        empty = assistant.openrouter_prices.indication(ESTIMATE, {'prompt_tokens': 0, 'completion_tokens': 0})
        self.assertEqual('0', empty['token_subtotal_usd'])

    def test_received_unknowns_allow_new_exchanges_without_rewriting_or_replay(self):
        response = self.http.getresponse.return_value
        response.status = 429
        response.read.return_value = b'{"error":{"code":429}}'
        original, view = self.execute()
        frozen = storage._strict_json(original)
        self.assertEqual('suspended', view['stage'])
        self.assertNotIn('indicative_cost', view)
        self.assertEqual('UNKNOWN', original['observed_cost']['status'])
        self.assertIsNone(prep.admission(self.store))
        with self.assertRaises(prep.Denied):
            self.submit(action_id='closed', revision=2, kind='clarify', message=CLARIFICATION)
        prep.admit(self.store, self.authority)
        response.status = 200
        response.read.return_value = http_body(result('clarification'), usage={'prompt_tokens': 1000, 'completion_tokens': 200})
        second, view = self.execute(action_id='new', revision=2, kind='clarify', message=CLARIFICATION)
        self.assertEqual('UNKNOWN', second['observed_cost']['status'])
        self.assertEqual('0.0003600', view['indicative_cost']['token_subtotal_usd'])
        response.read.return_value = http_body(usage=None)
        third, view = self.execute(action_id='correct', revision=3, kind='correct', message=CORRECTION)
        self.assertEqual('preview', view['stage'])
        self.assertIsNone(view['indicative_cost'])
        budget = self.store.inspect_budget('fixture')
        self.assertEqual(storage._sum_money([storage._money(RESERVE)] * 3), storage._money(budget['reserved']))
        self.assertEqual('0', budget['spent'])
        self.assertEqual({original['operation_id'], second['operation_id'], third['operation_id']}, set(budget['unknown_cost_operations']))
        self.assertEqual(frozen, storage._strict_json(next(row for row in self.store.inspect_operations() if row['operation_id'] == original['operation_id'])))
        self.assertEqual((original['operation_id'], False), prep.submit(self.store, self.session, 'd',
                         dict(action_id='create', request=NEED), 'a' * 40, self.transport))
        prep.execute(self.data, original['operation_id'], self.transport)
        self.assertEqual(3, self.http.request.call_count)
        # The shared reservation path stays strict for candidate and judgment work
        candidate = {key: third[key] for key in storage._OPERATION_KEYS}
        candidate.update(operation_id='candidate-fixture', phase='acquisition')
        with self.assertRaises(storage.BudgetError):
            self.store.reserve_intent(candidate, 'fixture', RESERVE)
        candidate.update(operation_id='judgment-fixture', phase='judgment')
        with self.assertRaises(storage.BudgetError):
            self.store.reserve_intent(candidate, 'fixture', RESERVE)
        candidate.update(operation_id='other-budget', phase='preparation')
        self.store.create_budget('separate-fixture', RESERVE, 'USD')
        self.store.reserve_intent(candidate, 'separate-fixture', RESERVE)
        candidate['operation_id'] = 'exhausted-fixture'
        with self.assertRaises(storage.BudgetError):
            self.store.reserve_intent(candidate, 'separate-fixture', RESERVE)
        self.assertEqual(budget, self.store.inspect_budget('fixture'))

    def test_ambiguous_effects_block_reservation_and_emission_after_unknown(self):
        self.http.getresponse.return_value.read.return_value = http_body(usage=None)
        original, _ = self.execute()
        pending = self.submit(action_id='pending', revision=2, kind='correct', message=CORRECTION)
        extra = {key: original[key] for key in storage._OPERATION_KEYS}
        extra.update(operation_id='separate-effect', dossier_id='other-dossier')
        self.store.save_dossier('other-dossier', 1, self.store.get_dossier('d', 1))
        self.store.reserve_intent(extra, 'fixture', RESERVE)
        self.store.mark_emission_possible(extra['operation_id'])
        for state in ('EMISSION_POSSIBLE', 'AMBIGUOUS'):
            if state == 'AMBIGUOUS':
                self.store.mark_ambiguous(extra['operation_id'], 'FICTIONAL_INTERRUPTION')
            another = {**extra, 'operation_id': 'another-effect'}
            with self.assertRaises(storage.BudgetError):
                self.store.reserve_intent(another, 'fixture', RESERVE)
            prep.execute(self.data, pending, self.transport)
            self.assertEqual(1, self.http.request.call_count)
            operation = next(row for row in self.store.inspect_operations() if row['operation_id'] == pending)
            self.assertEqual('INTENT_RECORDED', operation['state'])
        self.assertEqual('UNKNOWN', next(row for row in self.store.inspect_operations() if row['operation_id'] == original['operation_id'])['observed_cost']['status'])

    def test_null_historical_observation_remains_readable_and_has_no_estimate(self):
        operation = self.submit()
        receipt = {'receipt_id': 'fictional-null-observation', 'observed_configuration': None,
                   'resources_seen': [], 'result': result('clarification')}
        cost = {'status': 'UNKNOWN', 'amount': None, 'currency': 'USD', 'source': 'Fictional historical receipt'}
        prep.execute(self.data, operation, lambda op, request: {'receipt': receipt, 'cost': cost})
        view = prep.view(self.store, self.session, 'd')
        self.assertEqual('clarification', view['stage'])
        self.assertNotIn('indicative_cost', view)
        self.assertIsNone(self.store.inspect_operations()[0]['receipt']['observed_configuration'])
        self.assertIn('Coût observé', views.render(view, self.csrf).decode())
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_killed_executor_restarts_closed_with_durable_ambiguous_request(self):
        context = multiprocessing.get_context('spawn')
        entered = context.Event()
        sock = self.home / 'executor.sock'
        children = []
        def cleanup():
            for process in children:
                if process.is_alive(): process.kill()
                process.join(5)
        self.addCleanup(cleanup)
        def start(gate=None):
            process = context.Process(target=executor_process, args=(self.data, sock, gate))
            process.start()
            children.append(process)
            deadline = time.monotonic() + 5
            while True:
                try:
                    service.executor_health(sock)
                    return process
                except OSError:
                    if time.monotonic() > deadline: self.fail('executor not ready')
                    time.sleep(.02)
        first = start(entered)
        prep.admit(self.store, self.authority)
        accepted = service.preparation_request(sock, 'POST', '/preparation/dossiers', self.token,
            dict(csrf_token=self.csrf, dossier_id='d', action_id='interrupted', request=NEED,
                 source_sha256='a' * 64))
        self.assertEqual(202, accepted['status'])
        self.assertTrue(entered.wait(5))
        pending = self.store.inspect_operations()[0]
        self.assertEqual('EMISSION_POSSIBLE', pending['state'])
        first.kill()
        first.join(5)
        start()
        after = self.store.inspect_operations()[0]
        self.assertEqual('AMBIGUOUS', after['state'])
        self.assertEqual(pending['resources'], after['resources'])
        self.assertEqual(2, len(after['resources']))
        self.assertIsNone(after['receipt'])
        self.assertIsNone(prep.admission(self.store))
        self.assertEqual(RESERVE, self.store.inspect_budget('fixture')['reserved'])
        self.assertEqual('suspended', prep.view(self.store, self.session, 'd')['stage'])

    def test_two_profiles_drive_exact_simulated_http_bytes(self):
        historical = assistant.load_profile(assistant.ASSISTANT)
        synthetic = assistant.load_profile(str(SYNTHETIC_PROFILE))
        self.assertNotEqual(historical['model'], synthetic['model'])
        self.assertNotEqual(historical['system'], synthetic['system'])
        sent_models = []
        for profile, dossier in ((historical, 'glm-profile'), (synthetic, 'synthetic-profile')):
            estimate = estimate_for(profile)
            prep.admit(self.store, dict(authority_id='FICTIONAL_HTTP_ONLY', budget_id='fixture',
                                        reserve_amount=assistant.reservation(estimate, profile),
                                        requested_configuration=assistant.configuration(estimate, profile)))
            transport = assistant.OpenRouterPreparation(KEY, deepcopy(profile))
            route = {'requested': profile['model'], 'strategy': 'direct', 'attempt': 1,
                     'endpoints': {'available': [{'provider': profile['routes'][0]['provider_name'],
                                                  'model': profile['model'], 'selected': True}]}}
            self.http.getresponse.return_value.read.return_value = http_body(
                result('clarification'), model=profile['model'], openrouter_metadata=route)
            operation_id, started = prep.submit(self.store, self.session, dossier,
                dict(action_id='create', request=NEED), 'a' * 40, transport)
            self.assertTrue(started)
            prep.execute(self.data, operation_id, transport)
            body = self.http.request.call_args.kwargs['body']
            sent = json.loads(body)
            self.assertEqual(profile['model'], sent['model'])
            self.assertEqual(profile['parameters'], {key: sent[key] for key in profile['parameters']})
            for key in ('temperature', 'top_p', 'reasoning', 'response_format'):
                if key in profile['parameters']:
                    self.assertEqual(profile['parameters'][key], sent[key])
                else:
                    self.assertNotIn(key, sent)
            self.assertEqual(profile['system'], sent['messages'][0]['content'])
            self.assertEqual([route['tag'] for route in profile['routes']], sent['provider']['only'])
            self.assertNotIn(str(SYNTHETIC_PROFILE), body.decode())
            self.assertNotIn('profile_sha256', body.decode())
            self.assertNotIn(KEY, body.decode())
            configured = assistant.configuration(estimate, profile)
            self.assertEqual(profile['profile_id'], configured['profile_id'])
            self.assertEqual(assistant.profile_digest(profile), configured['profile_sha256'])
            self.assertNotIn('path', configured)
            sent_models.append(sent['model'])
            self.http.request.reset_mock()
        self.assertEqual([historical['model'], synthetic['model']], sent_models)

    def test_story_14_loads_production_profiles_and_keeps_glm_historical(self):
        preparation_profile = assistant.load_profile(assistant.ASSISTANT)
        qualification_profile = assistant.load_profile(str(
            Path(assistant.__file__).with_name('qualification.profile.json')))
        historical = assistant.load_profile(assistant.HISTORICAL_ASSISTANT)
        self.assertEqual('openai/gpt-6-astra', preparation_profile['model'])
        self.assertEqual({'effort': 'medium'}, preparation_profile['parameters']['reasoning'])
        self.assertEqual('anthropic/claude-fable-5.1', qualification_profile['model'])
        self.assertEqual({'effort': 'medium'}, qualification_profile['parameters']['reasoning'])
        self.assertEqual('z-ai/glm-5.3-flash', historical['model'])
        self.assertEqual(preparation_profile, assistant.frozen_profile())

    def test_story_14_stops_repeated_scope_confirmations(self):
        for expected, action_id in (('scope_confirmation', 'create'),
                                    ('clarification', 'scope-2'),
                                    ('suspended', 'scope-3')):
            self.http.getresponse.return_value.read.return_value = http_body(result('scope_confirmation'))
            if action_id == 'create':
                _, view = self.execute()
            else:
                current = prep.view(self.store, self.session, 'd')['revision']
                _, view = self.execute(action_id=action_id, revision=current, kind='clarify',
                                       message='Le périmètre textuel proposé me convient.')
            self.assertEqual(expected, view['stage'])
        self.assertEqual(3, view['checks']['scope_confirmation_count'])
        self.assertTrue(view['explanation'].startswith('SCOPE_LOOP :'))

    def test_story_14_rejects_fiction_markers_from_candidate_content(self):
        for dossier, field, marker in (('bad-instruction', 'instruction', 'Dossier fictif à analyser'),
                                       ('bad-piece', 'piece', 'Organisation inventée')):
            with self.subTest(field=field):
                value = result()
                if field == 'instruction':
                    value['package']['candidate']['instruction'] = marker
                else:
                    value['package']['candidate']['pieces'][0]['content'] = marker
                prep.admit(self.store, self.authority)
                self.http.getresponse.return_value.read.return_value = http_body(value)
                operation_id, _ = prep.submit(self.store, self.session, dossier,
                    dict(action_id='create', request=NEED), 'a' * 40, self.transport)
                prep.execute(self.data, operation_id, self.transport)
                operation = next(item for item in self.store.inspect_operations()
                                 if item['operation_id'] == operation_id)
                view = prep.view(self.store, self.session, dossier)
                self.assertEqual('RECEIVED', operation['state'])
                self.assertEqual('FORMAT_ERROR', operation['receipt']['observed_configuration']['incident'])
                self.assertEqual('suspended', view['stage'])
                self.assertIsNone(view['package'])

    def test_profile_object_and_file_mutations_do_not_change_prepared_bytes(self):
        source = self.home / 'synthetic.profile.json'
        source.write_bytes(SYNTHETIC_PROFILE.read_bytes())
        loaded = assistant.load_profile(str(source))
        estimate = estimate_for(loaded)
        operation = {'operation_id': 'fixture', 'requested_configuration': assistant.configuration(estimate, loaded),
                     'phase': 'preparation', 'state': 'INTENT_RECORDED'}
        request = prep._closed_preparation_request(dict(
            message='x', kind='create', payload=dict(request='x', reformulation='', clarifications=[],
                                                     validated_assumptions=[], fictional_parameters={}), package=None))
        from_object = assistant.OpenRouterPreparation(KEY, loaded)
        from_file = assistant.OpenRouterPreparation(KEY, str(source))
        first_object = from_object.prepare(deepcopy(operation), deepcopy(request))
        first_file = from_file.prepare(deepcopy(operation), deepcopy(request))
        self.assertEqual(first_object, first_file)
        loaded['model'] = 'fixture/mutated-prep'
        loaded['system'] = 'message système muté'
        loaded['parameters']['max_tokens'] = 2
        source.write_text(source.read_text().replace('synthetic-prep', 'mutated-prep'))
        self.assertEqual(first_object, from_object.prepare(deepcopy(operation), deepcopy(request)))
        self.assertEqual(first_file, from_file.prepare(deepcopy(operation), deepcopy(request)))
        self.assertIn('fixture/synthetic-prep', first_object)
        self.assertNotIn('mutated', first_object)
        self.assertNotIn(str(source), first_object)
        self.assertEqual('fixture/synthetic-prep', json.loads(first_object)['model'])
        self.http.request.assert_not_called()

    def test_invalid_profile_and_missing_path_refuse_before_key_and_executor(self):
        sock = self.home / 'executor.sock'
        arguments = ['executor', '--data', str(self.data), '--socket', str(sock)]
        missing = str(self.home / 'absent.profile.json')
        unknown = self.home / 'unknown-field.profile.json'
        invalid = self.home / 'invalid-type.profile.json'
        duplicate = self.home / 'duplicate.profile.json'
        empty_routes = self.home / 'empty-routes.profile.json'
        document = json.loads(SYNTHETIC_PROFILE.read_text())
        unknown.write_text(json.dumps(dict(document, future_field='no'), ensure_ascii=False))
        broken = dict(document)
        broken['timeout_seconds'] = '30'
        invalid.write_text(json.dumps(broken, ensure_ascii=False))
        empty = dict(document, routes=[])
        empty['parameters'] = dict(document['parameters'], provider=dict(
            document['parameters']['provider'], only=[], order=[]))
        empty_routes.write_text(json.dumps(empty, ensure_ascii=False))
        duplicate.write_text(SYNTHETIC_PROFILE.read_text().replace(
            '"profile_id": "synthetic-prep"', '"profile_id": "synthetic-prep", "profile_id": "dup"', 1))
        for value in (missing, str(unknown), str(invalid), str(duplicate), str(empty_routes), ''):
            with self.subTest(value=value):
                with patch.dict(os.environ, {'OPENROUTER_API_KEY': KEY}, clear=False), \
                        patch.object(service, 'serve_executor') as executor, \
                        patch.object(service, 'release_identity', return_value='a' * 40), \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(78, runtime.main(arguments + ['--preparation-assistant', value]))
                    executor.assert_not_called()
                    self.assertEqual(KEY, os.environ.get('OPENROUTER_API_KEY'))
                    self.http.request.assert_not_called()
        alias = assistant.load_profile(assistant.ASSISTANT)
        from_file = assistant.load_profile(str(Path(assistant.__file__).with_name(assistant.DEFAULT_PROFILE_NAME)))
        self.assertEqual(alias, from_file)
        self.assertEqual(assistant.MODEL, alias['model'])
        self.assertEqual(assistant.SYSTEM_PROMPT, alias['system'])
        self.assertEqual(assistant.PARAMETERS, alias['parameters'])

    def test_synthetic_profile_omits_optional_parameters_from_simulated_http(self):
        profile = assistant.load_profile(str(SYNTHETIC_PROFILE))
        for key in ('temperature', 'top_p', 'reasoning', 'response_format'):
            self.assertNotIn(key, profile['parameters'])
        self.assertEqual(['max_tokens'], profile['required_capabilities'])
        extra_capability = json.loads(SYNTHETIC_PROFILE.read_text())
        extra_capability['required_capabilities'] = ['max_tokens', 'temperature']
        with self.assertRaises(ValueError):
            assistant.frozen_profile(extra_capability)
        estimate = estimate_for(profile)
        prep.admit(self.store, dict(authority_id='FICTIONAL_HTTP_ONLY', budget_id='fixture',
                                    reserve_amount=assistant.reservation(estimate, profile),
                                    requested_configuration=assistant.configuration(estimate, profile)))
        transport = assistant.OpenRouterPreparation(KEY, deepcopy(profile))
        route = {'requested': profile['model'], 'strategy': 'direct', 'attempt': 1,
                 'endpoints': {'available': [{'provider': profile['routes'][0]['provider_name'],
                                              'model': profile['model'], 'selected': True}]}}
        self.http.getresponse.return_value.read.return_value = http_body(
            result('clarification'), model=profile['model'], openrouter_metadata=route)
        operation_id, started = prep.submit(self.store, self.session, 'synthetic-optional',
            dict(action_id='create', request=NEED), 'a' * 40, transport)
        self.assertTrue(started)
        prep.execute(self.data, operation_id, transport)
        sent = json.loads(self.http.request.call_args.kwargs['body'])
        for key in ('temperature', 'top_p', 'reasoning', 'response_format'):
            self.assertNotIn(key, sent)
        self.assertEqual(1024, sent['max_tokens'])
        self.assertIs(False, sent['stream'])
        self.assertEqual(['fixture/fp8'], sent['provider']['only'])
        self.assertEqual(profile['system'], sent['messages'][0]['content'])

    def test_glm_profile_keeps_historical_system_parameters_and_http_bytes(self):
        historical = assistant.load_profile(assistant.HISTORICAL_ASSISTANT)
        self.assertEqual(historical['system'], assistant.HISTORICAL_PROFILE['system'])
        self.assertEqual({
            'temperature': 1, 'top_p': 0.95, 'reasoning': {'effort': 'low'},
            'provider': {'only': ['modal/fp8', 'coreweave/fp8', 'novita/fp8'],
                         'order': ['modal/fp8', 'coreweave/fp8', 'novita/fp8'],
                         'allow_fallbacks': True, 'require_parameters': True},
            'max_tokens': 16384, 'stream': False, 'response_format': {'type': 'json_object'},
        }, historical['parameters'])
        request = prep._closed_preparation_request(dict(
            message='x', kind='create', payload=dict(request='x', reformulation='', clarifications=[],
                                                     validated_assumptions=[], fictional_parameters={}), package=None))
        estimate = estimate_for(historical)
        operation = {'operation_id': 'fixture', 'requested_configuration': assistant.configuration(estimate, historical),
                     'phase': 'preparation', 'state': 'INTENT_RECORDED'}
        wire = assistant.OpenRouterPreparation(KEY, historical).prepare(operation, request)
        sent = json.loads(wire)
        self.assertEqual('3b593371788005829e4e83df9b783df6e7f4a807bc3a46da22f15a0bfd64ed1c',
                         sha256(wire.encode()).hexdigest())
        self.assertEqual(historical['system'], sent['messages'][0]['content'])
        self.assertEqual(historical['parameters'], {key: sent[key] for key in historical['parameters']})
        for key in ('temperature', 'top_p', 'reasoning', 'response_format'):
            self.assertIn(key, sent)
        self.http.request.assert_not_called()

    def test_profile_accepts_only_model_and_single_revision(self):
        loaded = assistant.load_profile(str(SYNTHETIC_PROFILE))
        configured = assistant.configuration(estimate_for(loaded), loaded)
        self.assertEqual('fixture/synthetic-prep', loaded['model'])
        self.assertEqual('fixture/synthetic-prep-rev', loaded['revision'])
        self.assertEqual(['fixture/synthetic-prep', 'fixture/synthetic-prep-rev'], configured['model_identities'])
        self.assertEqual(loaded['revision'], configured['revision'])
        self.assertNotIn('authorized_identities', loaded)
        glm = assistant.load_profile(assistant.HISTORICAL_ASSISTANT)
        self.assertEqual('z-ai/glm-5.3-flash', glm['model'])
        self.assertEqual('z-ai/glm-5.3-flash-20260826', glm['revision'])
        self.assertEqual([glm['model'], glm['revision']], assistant.configuration(estimate_for(glm), glm)['model_identities'])
        document = json.loads(SYNTHETIC_PROFILE.read_text())
        old_list = dict(document)
        del old_list['revision']
        old_list['authorized_identities'] = [document['model'], document['revision'], document['model'] + '-other']
        extra = dict(document, authorized_identities=[document['model'], document['revision']])
        listed = dict(document, revision=[document['model'], document['revision'], document['model'] + '-other'])
        same = dict(document, revision=document['model'])
        for invalid in (old_list, extra, listed):
            with self.subTest(invalid=invalid.get('revision', invalid.get('authorized_identities'))):
                with self.assertRaises(ValueError):
                    assistant.frozen_profile(invalid)
        equal = assistant.frozen_profile(same)
        self.assertEqual([equal['model']], assistant.configuration(profile=equal)['model_identities'])
        self.assertEqual(equal['model'], equal['revision'])

    def test_canonical_slug_must_equal_unique_revision_before_http(self):
        frozen = assistant.frozen_profile()
        self.assertNotEqual(frozen['revision'], frozen['model'])
        absent = {key: value for key, value in ESTIMATE.items() if key != 'canonical_slug'}
        for estimate in (dict(ESTIMATE, canonical_slug=frozen['model']),
                         absent,
                         dict(ESTIMATE, canonical_slug=''),
                         dict(ESTIMATE, canonical_slug=frozen['revision'] + '-other')):
            with self.subTest(canonical=estimate.get('canonical_slug', '<absent>')):
                with self.assertRaises(ValueError):
                    assistant.reservation(estimate)
                with self.assertRaises(ValueError):
                    assistant.configuration(estimate)
                self.http.request.assert_not_called()
        self.assertEqual(RESERVE, assistant.reservation(ESTIMATE))
        self.assertEqual(frozen['revision'], assistant.configuration(ESTIMATE)['revision'])

    def test_third_response_identity_is_unusable_with_receipt_and_cost_kept(self):
        configured = assistant.configuration(ESTIMATE)
        self.assertEqual(2, len(configured['model_identities']))
        third = configured['model'] + '-20260827'
        self.assertNotIn(third, configured['model_identities'])
        raw = http_body(model=third)
        self.http.getresponse.return_value.read.return_value = raw
        operation, view = self.execute()
        self.assertEqual('suspended', view['stage'])
        self.assertIsNone(operation['receipt']['result'])
        self.assertEqual(raw, b64decode(operation['receipt']['observed_configuration']['http']['body_base64']))
        self.assertEqual('KNOWN', operation['observed_cost']['status'])
        self.assertEqual('0.000202', operation['observed_cost']['amount'])
        self.assertEqual(1, self.http.request.call_count)

    def test_transport_limit_ceilings_cannot_be_raised(self):
        document = json.loads(SYNTHETIC_PROFILE.read_text())
        assistant.frozen_profile(dict(document, max_request_bytes=65536, max_response_bytes=2097152,
                                      timeout_seconds=120))
        assistant.frozen_profile(document)
        historical = assistant.load_profile(assistant.ASSISTANT)
        self.assertEqual(65536, historical['max_request_bytes'])
        self.assertEqual(2097152, historical['max_response_bytes'])
        self.assertEqual(120, historical['timeout_seconds'])
        self.assertEqual((65536, 2097152, 120),
                         (assistant.MAX_REQUEST_BYTES, assistant.MAX_RESPONSE_BYTES, assistant.TIMEOUT_SECONDS))
        sock = self.home / 'executor.sock'
        arguments = ['executor', '--data', str(self.data), '--socket', str(sock)]
        for index, invalid in enumerate((
                dict(document, max_request_bytes=65537),
                dict(document, max_response_bytes=2097153),
                dict(document, timeout_seconds=121))):
            with self.subTest(index=index):
                path = self.home / ('overflow-' + str(index) + '.profile.json')
                path.write_text(json.dumps(invalid, ensure_ascii=False))
                with self.assertRaises(ValueError):
                    assistant.load_profile(str(path))
                with patch.dict(os.environ, {'OPENROUTER_API_KEY': KEY}, clear=False), \
                        patch.object(service, 'serve_executor') as executor, \
                        patch.object(service, 'release_identity', return_value='a' * 40), \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(78, runtime.main(arguments + ['--preparation-assistant', str(path)]))
                    executor.assert_not_called()
                    self.assertEqual(KEY, os.environ.get('OPENROUTER_API_KEY'))
                    self.http.request.assert_not_called()
        self.assertFalse(hasattr(assistant.OpenRouterPreparation(KEY), '_profile_sha256'))


if __name__ == '__main__':
    unittest.main()
