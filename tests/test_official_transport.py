"""Native HTTP fixtures under real Pi; no paid provider calls"""
from base64 import b64decode
from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import Mock, patch

from benchmark_lab_x import campaigns as c, evaluation as e, pi_official as native, runtime, pi_openrouter as router, recovery
from tests import test_private_comparison as private
from tests.test_s4_regressions import inputs
from tests.test_s5_regressions import findings


class OfficialTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        private.PiTransportTests.setUpClass()

    def setUp(self):
        self.h = private.PiTransportTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def prepare(self, kind, mutate=None, finish=None, ceiling=False, retry_effort=None, **changes):
        h = self.h
        source_id = 'router-' + kind
        source = deepcopy(c.inspect(h.store, 'pi-offline')['manifest'])
        source.update(campaign_id=source_id, financial_cost_policy='retain_reserve')
        namespace = {'anthropic':'anthropic', 'deepseek':'deepseek', 'zai':'z-ai'}[kind]
        for config in source['panel']:
            config.update(model=namespace + '/model-fixed', revision=namespace + '/model-fixed', effort='low')
            config['parameters']['reasoning'] = dict(effort='low')
        h.store.create_budget(source_id, '1', 'USD')
        snapshot = c.create(h.store, source)
        authority, evidence = inputs(snapshot, cells=['x'], budget=source_id)
        authority['reserve_amounts'] = {'x':'0.1'}
        if ceiling:
            authority['technical_recovery'] = dict(capabilities=dict(id=namespace + '/model-fixed', endpoints=[dict(
                tag='fixture/route', status=0, max_completion_tokens=source['panel'][0]['parameters']['max_tokens'],
                context_length=source['conditions']['defaults']['context_window'],
                supported_parameters=['max_tokens', 'reasoning'], pricing=dict(prompt='0.000001', completion='0.000002'))]))
        c.admit(h.store, source_id, authority, evidence)
        source_oid = source_id + '-attempt'
        c.reserve(h.store, source_id, 'x', source_oid)
        def failed(key, wire, timeout):
            import time
            body = json.loads(h.raw)
            body['model'] = namespace + '/model-fixed'
            body['choices'][0]['finish_reason'] = finish
            body['choices'][0]['message']['content'] = ''
            body['usage']['prompt_tokens'] = 10
            return 200 if finish else 503, {}, json.dumps(body).encode(), True, '2026-09-10T10:00:00+00:00', time.monotonic()
        with patch.object(router.http, 'post', side_effect=failed):
            c.execute(h.data, source_oid, h.transport)
        if retry_effort:
            source = deepcopy(c.inspect(h.store, source_id)['manifest'])
            source_id += '-retry'
            source.update(campaign_id=source_id, recovery_of=source_oid,
                          panel=source['panel'][:1], plan=source['plan'][:1])
            source['attempt_policy']['order'] = ['x']
            source['panel'][0]['parameters']['max_tokens'] *= 2
            source['panel'][0]['parameters']['reasoning']['effort'] = retry_effort
            source['panel'][0]['effort'] = retry_effort
            snapshot = c.create(h.store, source)
            h.store.create_budget(source_id, '1', 'USD')
            authority, evidence = inputs(snapshot, cells=['x'], budget=source_id)
            authority['reserve_amounts'] = {'x':'0.1'}
            c.admit(h.store, source_id, authority, evidence)
            source_oid = source_id + '-attempt'
            c.reserve(h.store, source_id, 'x', source_oid)
            with patch.object(router.http, 'post', side_effect=failed):
                c.execute(h.data, source_oid, h.transport)
        transport = native.PiOfficial('fixture-native-key', h.package, h.node, kind)
        cid = 'official-' + kind
        h.store.create_budget(cid, '1', 'USD')
        manifest = deepcopy(c.inspect(h.store, source_id)['manifest'])
        config = manifest['panel'][0]
        params = dict(max_tokens=64, stream=False)
        if kind == 'anthropic':
            params['output_config'] = dict(effort=retry_effort or 'low')
        else:
            params.update(thinking=dict(type='enabled'), reasoning_effort=retry_effort or 'low')
        config.update(provider=transport.provider, model='model-fixed', revision='model-fixed',
                      channel_id=transport.endpoint, route=transport.endpoint, effort=retry_effort or 'low', parameters=params)
        config.update(changes)
        manifest.update(campaign_id=cid, panel=[config], plan=manifest['plan'][:1],
                        recovery_of=source_oid, official_fallback=dict(route_attempts=[source_oid]))
        manifest['attempt_policy']['order'] = ['x']
        if mutate:
            mutate(manifest)
        snapshot = c.create(h.store, manifest)
        authority, evidence = inputs(snapshot, cells=['x'], budget=cid)
        authority['reserve_amounts'] = {'x':'0.1'}
        c.admit(h.store, cid, authority, evidence)
        oid = cid + '-attempt'
        c.reserve(h.store, cid, 'x', oid)
        return transport, cid, oid

    def response(self, kind, **changes):
        if kind == 'anthropic':
            body = dict(model='model-fixed', role='assistant', stop_reason='end_turn',
                        content=[dict(type='thinking', thinking='Private thought'), dict(type='text', text='Native output')])
        else:
            body = dict(model='model-fixed', choices=[dict(finish_reason='stop',
                        message=dict(role='assistant', content='Native output'))])
        body.update(changes)
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.length = 0
        connection.getresponse.return_value.read.return_value = json.dumps(body).encode()
        return connection

    def test_three_native_formats_preserve_pi_wire_and_unknown_cost(self):
        h = self.h
        for kind in native.CHANNELS:
            with self.subTest(kind=kind):
                transport, cid, oid = self.prepare(kind)
                connection = self.response(kind)
                with patch.object(native, 'HTTPSConnection', return_value=connection):
                    c.execute(h.data, oid, transport)
                attempt = c.inspect(h.store, cid)['attempts'][0]
                receipt = attempt['operation']['receipt']
                observed = receipt['observed_configuration']
                wire = connection.request.call_args.kwargs['body']
                self.assertEqual(1, connection.request.call_count)
                self.assertEqual(sha256(wire).hexdigest(), observed['outgoing']['request_body_sha256'])
                self.assertEqual('Native output', receipt['result']['output'])
                self.assertIsNone(receipt['result']['incident'])
                self.assertTrue(observed['pi']['terminal'])
                self.assertEqual([], attempt['attribution_incident'])
                self.assertEqual('COMPLETE', recovery.observation(attempt)['kind'])
                self.assertNotIn(b'fixture-native-key', wire)
                self.assertNotIn(b'Private thought', wire)
                self.assertEqual('UNKNOWN', attempt['operation']['observed_cost']['status'])
                self.assertEqual('0.1', h.store.inspect_budget(cid)['reserved'])
                context = e._context(h.store, h.store._connection, cid, oid)
                report = findings(context, e._resources(h.store, context))
                record = e.evaluate(h.store, cid, oid, responsible='Native fixture operator',
                    authority=dict(actor='Ayo', authority_id='native-offline-test'), check=lambda ctx, resources:report)
                self.assertEqual('SATISFAIT', record['verdict'])
                self.assertTrue(runtime.verify(h.store)['integrity_ok'])

    def test_identity_mismatch_and_refusal_never_become_success(self):
        h = self.h
        for kind in ('anthropic', 'deepseek'):
            with self.subTest(kind=kind):
                transport, cid, oid = self.prepare(kind)
                connection = self.response(kind, **({'stop_reason':'refusal'} if kind == 'anthropic' else {'model':'foreign-model'}))
                raw = connection.getresponse.return_value.read.return_value
                with patch.object(native, 'HTTPSConnection', return_value=connection):
                    c.execute(h.data, oid, transport)
                attempt = c.inspect(h.store, cid)['attempts'][0]
                receipt = attempt['operation']['receipt']
                self.assertEqual(raw, b64decode(receipt['observed_configuration']['http']['body_base64']))
                self.assertEqual('CONTENT_REFUSAL' if kind == 'anthropic' else 'MODEL_IDENTITY_MISMATCH', receipt['result']['incident'])
                self.assertEqual(1, connection.request.call_count)
                if kind == 'anthropic':
                    self.assertEqual('CONTENT_REFUSAL', recovery.observation(attempt)['kind'])
                else:
                    with self.assertRaises(c.ConflictError):
                        recovery.observation(attempt)

    def test_foreign_endpoint_is_rejected_before_http(self):
        with patch.object(native, 'HTTPSConnection') as connection:
            with self.assertRaisesRegex(ValueError, 'OpenRouter uniquement'):
                self.prepare('zai', channel_id='https://example.org/chat')
        connection.assert_not_called()

    def test_official_admission_requires_exact_identity_and_route_receipt(self):
        with patch.object(native, 'HTTPSConnection') as connection:
            with self.assertRaisesRegex(ValueError, 'aucun alias'):
                self.prepare('deepseek', model='different-alias', revision='different-alias')
            with self.assertRaisesRegex(ValueError, 'Preuve de route antérieure requise'):
                self.prepare('anthropic', mutate=lambda manifest: manifest['official_fallback'].update(
                    route_attempts=['pi-op']))
        connection.assert_not_called()

    def test_truncated_native_output_stays_an_incident(self):
        transport, cid, oid = self.prepare('anthropic')
        connection = self.response('anthropic', stop_reason='max_tokens')
        with patch.object(native, 'HTTPSConnection', return_value=connection):
            c.execute(self.h.data, oid, transport)
        attempt = c.inspect(self.h.store, cid)['attempts'][0]
        self.assertEqual('PROVIDER_RESPONSE_INCOMPLETE', attempt['operation']['receipt']['result']['incident'])
        self.assertEqual('LENGTH', recovery.observation(attempt)['kind'])

    def test_length_requires_proven_exhaustion_before_official_admission(self):
        with self.assertRaisesRegex(ValueError, 'à résoudre'):
            self.prepare('deepseek', finish='length')
        transport, cid, oid = self.prepare('anthropic', finish='length', ceiling=True)
        self.assertEqual('INTENT_RECORDED', c.inspect(self.h.store, cid)['attempts'][0]['state'])

    def test_openrouter_refusal_with_text_cannot_receive_positive_verdict(self):
        h = self.h
        body = json.loads(h.raw)
        body['choices'][0]['message']['refusal'] = 'Explicit refusal'
        h.raw = json.dumps(body).encode()
        attempt = h.execute()
        self.assertEqual('CONTENT_REFUSAL', attempt['operation']['receipt']['result']['incident'])
        context = e._context(h.store, h.store._connection, 'pi-offline', 'pi-intent')
        report = findings(context, e._resources(h.store, context))
        record = e.evaluate(h.store, 'pi-offline', 'pi-intent', responsible='Fixture operator',
            authority=dict(actor='Ayo', authority_id='refusal-test'), check=lambda ctx, resources:report)
        self.assertIsNone(record['verdict'])

    def test_no_progress_only_counts_under_unchanged_reasoning(self):
        with self.assertRaisesRegex(ValueError, 'à résoudre'):
            self.prepare('deepseek', finish='length', retry_effort='high')
        _, cid, _ = self.prepare('anthropic', finish='length', retry_effort='low')
        self.assertEqual('INTENT_RECORDED', c.inspect(self.h.store, cid)['attempts'][0]['state'])
