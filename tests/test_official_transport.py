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
    OPENROUTER_MODELS = {
        'deepseek': 'deepseek/deepseek-v4.1-flash',
        'openai': 'openai/gpt-5.6-sol',
        'moonshot': 'moonshotai/kimi-k3',
        'dashscope': 'qwen/qwen3.8-max-0902',
        'tokenhub': 'tencent/hy4-preview',
    }
    NATIVE_MODELS = {
        'deepseek': 'deepseek-flash',
        'openai': 'gpt-5.6-sol',
        'moonshot': 'kimi-k3',
        'dashscope': 'qwen3.8-max-0902',
        'tokenhub': 'hy4-preview',
    }
    DASHSCOPE_BASE_URL = 'https://workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1'
    TOKENHUB_BASE_URL = 'https://tokenhub-intl.tencentmaas.com'

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
        namespace = {'anthropic':'anthropic', 'deepseek':'deepseek', 'zai':'z-ai'}.get(kind)
        openrouter_model = self.OPENROUTER_MODELS[kind] if kind in self.OPENROUTER_MODELS else namespace + '/model-fixed'
        native_model = self.NATIVE_MODELS.get(kind, 'model-fixed')
        effort = 'max' if kind == 'moonshot' else 'low'
        for config in source['panel']:
            config.update(model=openrouter_model, revision=openrouter_model, effort=effort)
            config['parameters']['reasoning'] = dict(effort=effort)
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
            body['model'] = openrouter_model
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
        base_url = self.DASHSCOPE_BASE_URL if kind == 'dashscope' else (
            self.TOKENHUB_BASE_URL if kind == 'tokenhub' else None)
        transport = native.PiOfficial('fixture-native-key', h.package, h.node, kind, base_url)
        cid = 'official-' + kind
        h.store.create_budget(cid, '1', 'USD')
        manifest = deepcopy(c.inspect(h.store, source_id)['manifest'])
        config = manifest['panel'][0]
        params = {('max_output_tokens' if kind in ('openai', 'dashscope') else 'max_tokens'): 64,
                  'stream': False}
        if kind == 'anthropic':
            params['output_config'] = dict(effort=retry_effort or effort)
        elif kind in ('openai', 'dashscope'):
            params['reasoning'] = dict(effort=retry_effort or effort)
        elif kind in ('moonshot', 'tokenhub'):
            params['reasoning_effort'] = retry_effort or effort
            if kind == 'tokenhub':
                params['thinking'] = dict(type='enabled')
        else:
            params.update(thinking=dict(type='enabled'), reasoning_effort=retry_effort or 'low')
        config.update(provider=transport.provider, model=native_model, revision=native_model,
                      channel_id=transport.endpoint, route=transport.endpoint, effort=retry_effort or effort, parameters=params)
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
        elif kind in ('openai', 'dashscope'):
            body = dict(model=self.NATIVE_MODELS[kind], status='completed', output=[dict(
                type='message', role='assistant', status='completed',
                content=[dict(type='output_text', text='Native output')])])
        else:
            body = dict(model=self.NATIVE_MODELS.get(kind, 'model-fixed'), choices=[dict(finish_reason='stop',
                        message=dict(role='assistant', content='Native output'))])
        body.update(changes)
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.length = 0
        connection.getresponse.return_value.read.return_value = json.dumps(body).encode()
        return connection

    def test_preauthorized_official_fallback_runs_without_user_selection(self):
        h = self.h
        source = deepcopy(c.inspect(h.store, 'pi-offline')['manifest'])
        source.update(campaign_id='automatic-official', financial_cost_policy='retain_reserve')
        config = source['panel'][0]
        config.update(model='z-ai/model-fixed', revision='z-ai/model-fixed', effort='low')
        config['parameters']['max_tokens'] = 32
        config['parameters'].update(
            provider=dict(only=['fixture/route'], order=['fixture/route'],
                          allow_fallbacks=False, require_parameters=True),
            reasoning=dict(effort='low'))
        h.store.create_budget(source['campaign_id'], '2', 'USD')
        snapshot = c.create(h.store, source)
        authority, evidence = inputs(snapshot, cells=['x'], budget=source['campaign_id'])
        authority['reserve_amounts'] = {'x': '0.1'}
        official = deepcopy(config)
        transport = native.PiOfficial('fixture-native-key', h.package, h.node, 'zai')
        official.update(
            provider=transport.provider, model='model-fixed', revision='model-fixed',
            channel_id=transport.endpoint, route=transport.endpoint,
            parameters=dict(max_tokens=64, stream=False,
                            thinking=dict(type='enabled'), reasoning_effort='low'))
        official_channel = {
            'available': True, 'revision': official['revision'],
            'channel_id': official['channel_id'], 'route': official['route'],
            'proof': 'Authenticated model catalogue witness',
        }
        authority['technical_recovery'] = {
            'capabilities': {
                'id': config['model'],
                'endpoints': [{
                    'tag': 'fixture/route', 'status': 0,
                    'max_completion_tokens': 64, 'context_length': 65536,
                    'supported_parameters': ['max_tokens', 'temperature', 'reasoning'],
                    'pricing': {'prompt': '0.000001', 'completion': '0.000002'},
                }],
            },
            'official_fallbacks': {
                config['id']: {
                    'configuration': official, 'channel': official_channel,
                    'reserve_amount': '0.2',
                },
            },
        }
        c.admit(h.store, source['campaign_id'], authority, evidence)
        c.reserve(h.store, source['campaign_id'], 'x', 'automatic-source')
        channels = []

        def factory(channel_id):
            channels.append(channel_id)
            return h.transport if channel_id == config['channel_id'] else transport

        router_calls = []

        def truncated(key, wire, timeout):
            import time
            router_calls.append(wire)
            body = json.loads(h.raw)
            body.update(model=config['model'])
            body['choices'][0].update(finish_reason='length')
            body['choices'][0]['message']['content'] = 'partial'
            body['usage']['prompt_tokens'] = 10
            return 200, {}, json.dumps(body).encode(), True, '2026-09-10T10:00:00+00:00', time.monotonic()

        with patch.object(router.http, 'post', side_effect=truncated), \
                patch.object(native, 'HTTPSConnection',
                             return_value=self.response('zai')):
            c.execute(h.data, 'automatic-source', transport_factory=factory)

        campaigns = c.list_campaigns(h.store)
        recovered = next(item for item in campaigns if item['manifest'].get('official_fallback'))
        openrouter_child = next(item for item in campaigns
                                if item['manifest'].get('recovery_of') == 'automatic-source')
        self.assertEqual(2, len(router_calls))
        self.assertEqual([openrouter_child['attempts'][0]['operation_id']],
                         recovered['manifest']['official_fallback']['route_attempts'])
        self.assertEqual('automatic-source', openrouter_child['manifest']['recovery_of'])
        self.assertEqual(transport.endpoint, recovered['manifest']['panel'][0]['channel_id'])
        self.assertEqual('RECEIVED', recovered['attempts'][0]['state'])
        self.assertEqual([config['channel_id'], config['channel_id'], transport.endpoint], channels)
        before = sum(len(item['attempts']) for item in campaigns)
        recovery.continue_preauthorized(h.data, recovered['attempts'][0]['operation_id'],
                                       transport_factory=factory)
        self.assertEqual(before, sum(len(item['attempts']) for item in c.list_campaigns(h.store)))

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

    def test_dashscope_base_url_must_be_an_official_alibaba_endpoint(self):
        for value in ('http://dashscope-us.aliyuncs.com/compatible-mode/v1',
                      'https://example.org/compatible-mode/v1',
                      'https://dashscope-us.aliyuncs.com/v1',
                      'https://dashscope-us.aliyuncs.com/compatible-mode/v1/chat/completions'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'HTTPS officiel Alibaba'):
                native.resolve_channel('dashscope', value)
        provider, host, path, _ = native.resolve_channel('dashscope', self.DASHSCOPE_BASE_URL)
        self.assertEqual('Alibaba Cloud Model Studio', provider)
        self.assertEqual('/compatible-mode/v1/responses', path)
        self.assertEqual('workspace.ap-southeast-1.maas.aliyuncs.com', host)

    def test_tokenhub_base_url_is_explicitly_regional(self):
        for base in ('https://tokenhub.tencentmaas.com',
                     'https://tokenhub-intl.tencentmaas.com'):
            provider, host, path, _ = native.resolve_channel('tokenhub', base)
            self.assertEqual('Tencent TokenHub', provider)
            self.assertEqual(base.removeprefix('https://'), host)
            self.assertEqual('/v1/chat/completions', path)
            endpoint = base + path
            self.assertEqual('tokenhub', native.kind_for_endpoint(endpoint))
        for value in ('', 'http://tokenhub-intl.tencentmaas.com',
                      'https://example.org', 'https://tokenhub.tencentmaas.com/v1'):
            with self.subTest(value=value), self.assertRaisesRegex(
                    ValueError, 'HTTPS officiel Tencent'):
                native.resolve_channel('tokenhub', value)

    def test_kimi_rejects_any_effort_other_than_max_before_http(self):
        transport = native.PiOfficial('fixture-native-key', self.h.package, self.h.node, 'moonshot')
        config = dict(provider=transport.provider, model='kimi-k3', revision='kimi-k3', access='API',
                      channel_id=transport.endpoint, route=transport.endpoint, effort='low',
                      parameters=dict(max_tokens=64, stream=False, reasoning_effort='low'))
        with self.assertRaisesRegex(ValueError, 'reasoning_effort=max'):
            transport._payload(config, [dict(role='system', content='system'), dict(role='user', content='prompt')])

    def test_explicit_native_identity_map_refuses_other_substitutions(self):
        self.assertEqual('deepseek-flash', native.native_identity('deepseek', 'deepseek/deepseek-v4.1-flash'))
        self.assertEqual('gpt-6-astra', native.native_identity('openai', 'openai/gpt-6-astra'))
        self.assertEqual('qwen3.8-max', native.native_identity('dashscope', 'qwen/qwen3.8-max'))
        with self.assertRaisesRegex(ValueError, 'aucun alias'):
            native.native_identity('deepseek', 'deepseek/deepseek-v4-flash-0731')
        with self.assertRaisesRegex(ValueError, 'aucun alias'):
            native.native_identity('moonshot', 'moonshotai/another-model')

    def test_provider_efforts_are_not_silently_remapped(self):
        cases = [
            ('openai', dict(max_output_tokens=64, stream=False, reasoning=dict(effort='minimal'))),
            ('dashscope', dict(max_output_tokens=64, stream=False, reasoning=dict(effort='high'))),
            ('tokenhub', dict(max_tokens=64, stream=False, reasoning_effort='medium',
                              thinking=dict(type='enabled'))),
        ]
        for kind, parameters in cases:
            with self.subTest(kind=kind):
                base_url = self.DASHSCOPE_BASE_URL if kind == 'dashscope' else (
                    self.TOKENHUB_BASE_URL if kind == 'tokenhub' else None)
                transport = native.PiOfficial('fixture-native-key', self.h.package, self.h.node, kind, base_url)
                config = dict(provider=transport.provider, model=self.NATIVE_MODELS[kind],
                              revision=self.NATIVE_MODELS[kind], access='API',
                              channel_id=transport.endpoint, route=transport.endpoint,
                              effort=parameters.get('reasoning_effort', parameters.get('reasoning', {}).get('effort')),
                              parameters=parameters)
                with self.assertRaisesRegex(ValueError, 'Effort natif|Raisonnement natif'):
                    transport._payload(config, [dict(role='system', content='system'),
                                                dict(role='user', content='prompt')])

    def test_openai_content_filter_is_not_recoverable_as_length(self):
        transport, cid, oid = self.prepare('openai')
        connection = self.response('openai', status='incomplete',
                                   incomplete_details={'reason': 'content_filter'})
        with patch.object(native, 'HTTPSConnection', return_value=connection):
            c.execute(self.h.data, oid, transport)
        attempt = c.inspect(self.h.store, cid)['attempts'][0]
        self.assertEqual('CONTENT_REFUSAL', recovery.observation(attempt)['kind'])

    def test_numeric_sampling_parameters_are_validated_for_all_applicable_transports(self):
        for kind in ('deepseek', 'zai', 'dashscope', 'tokenhub'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'numérique invalide'):
                transport, _, _ = self.prepare(kind)
                token_field = 'max_output_tokens' if kind == 'dashscope' else 'max_tokens'
                parameters = {token_field: 64, 'stream': False, 'temperature': 'wrong'}
                if kind == 'dashscope':
                    parameters['reasoning'] = {'effort': 'low'}
                else:
                    parameters.update(thinking={'type': 'enabled'}, reasoning_effort='low')
                transport._payload(dict(provider=transport.provider, model=self.NATIVE_MODELS.get(kind, 'model-fixed'),
                    revision=self.NATIVE_MODELS.get(kind, 'model-fixed'), access='API',
                    channel_id=transport.endpoint, route=transport.endpoint, effort='low',
                    parameters=parameters), [dict(role='system', content='system'),
                                             dict(role='user', content='prompt')])

    def test_missing_official_key_is_rejected_before_http(self):
        with patch.object(native, 'HTTPSConnection') as connection, self.assertRaisesRegex(ValueError, 'Clé API officielle'):
            native.PiOfficial('', self.h.package, self.h.node, 'tokenhub',
                              self.TOKENHUB_BASE_URL)
        connection.assert_not_called()
