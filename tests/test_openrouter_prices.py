from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from http.client import IncompleteRead
import io
import json
import unittest
from unittest.mock import Mock, patch

from benchmark import openrouter_prices as prices, runtime, openrouter_preparation as assistant


MODEL = 'z-ai/glm-5.3-flash'
SUMMARY = {'id': MODEL, 'canonical_slug': MODEL + '-20260826', 'context_length': 1000, 'pricing': {'prompt': '0.00000001'}}
ENDPOINT = {'model_id': MODEL, 'provider_name': 'Fixture provider', 'tag': 'fixture/fp8', 'status': 0,
            'pricing': {'prompt': '0.000001', 'completion': '0.000002', 'input_cache_read': '0.0000001'}}


class OpenRouterPricesTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.response = self.http.getresponse.return_value
        self.response.status = 200
        self.response.length = 0
        connection = patch.object(prices, 'HTTPSConnection', return_value=self.http)
        self.connection = connection.start()
        self.addCleanup(connection.stop)

    def responses(self, endpoints=None, summary=None):
        self.raw = [json.dumps({'data': SUMMARY if summary is None else summary}).encode(),
                    json.dumps({'data': {'id': MODEL, 'endpoints': [ENDPOINT] if endpoints is None else endpoints}}).encode()]
        self.response.read.side_effect = self.raw

    def test_runtime_forecast_is_public_read_only_and_provider_specific(self):
        other = {**ENDPOINT, 'provider_name': 'Other fixture', 'tag': 'other',
                 'pricing': {'prompt': '0.000002', 'completion': '0.000003'}}
        self.responses([ENDPOINT, other])
        before = assistant.configuration()
        with redirect_stdout(io.StringIO()) as output, patch.object(runtime, 'Store') as store, \
                patch.object(assistant, 'HTTPSConnection') as inference:
            self.assertEqual(0, runtime.main(['forecast-prices', '--model', MODEL, '--input-tokens', '1000',
                                              '--cached-input-tokens', '200', '--output-tokens', '50']))
        store.assert_not_called()
        inference.assert_not_called()
        self.assertEqual(before, assistant.configuration())
        result = json.loads(output.getvalue())
        first, second = result['endpoints']
        self.assertEqual({'prompt': '0.000800', 'input_cache_read': '0.0000200', 'completion': '0.000100'},
                         first['forecast']['components_usd'])
        self.assertEqual('0.0009200', first['forecast']['token_subtotal_usd'])
        self.assertIsNone(first['rates']['request']['amount'])
        self.assertIsNone(first['forecast']['total_usd'])
        self.assertEqual('USD/input_token', first['rates']['prompt']['unit'])
        self.assertEqual('0.001600', second['forecast']['components_usd']['prompt'])
        self.assertEqual('0.000150', second['forecast']['components_usd']['completion'])
        self.assertIsNone(second['forecast']['components_usd']['input_cache_read'])
        self.assertIsNone(second['forecast']['token_subtotal_usd'])
        self.assertEqual('TOP_PROVIDER_UNIDENTIFIED_NOT_ALL_ENDPOINTS', result['model_summary']['scope'])
        for key, raw in zip(('model', 'endpoints'), self.raw):
            self.assertEqual(sha256(raw).hexdigest(), result['sources'][key]['body_sha256'])
            self.assertIsNotNone(datetime.fromisoformat(result['sources'][key]['retrieved_at']).tzinfo)
        self.assertEqual([('GET', '/api/v1/model/' + MODEL), ('GET', '/api/v1/models/' + MODEL + '/endpoints')],
                         [call.args for call in self.http.request.call_args_list])
        for call in self.http.request.call_args_list:
            self.assertEqual({'headers': {'Accept': 'application/json'}}, call.kwargs)
        self.connection.assert_called_with('openrouter.ai', timeout=20)
        self.assertEqual(2, self.http.close.call_count)

    def test_runtime_prepares_s2_configuration_from_the_public_forecast(self):
        historical = assistant.load_profile(assistant.HISTORICAL_ASSISTANT)
        self.responses([{**ENDPOINT, 'tag': tag, 'provider_name': provider,
                         'supported_parameters': ['temperature', 'top_p', 'reasoning', 'max_tokens', 'response_format']}
                        for tag, provider in assistant.providers(historical).items()],
                       summary={**SUMMARY, 'pricing': {'prompt': '0.000001', 'completion': '0.000002'}})
        with redirect_stdout(io.StringIO()) as output, patch.object(runtime, 'Store') as store:
            self.assertEqual(0, runtime.main(['forecast-prices', '--model', MODEL, '--input-tokens', '1000',
                                              '--output-tokens', '16384', '--preparation-assistant', assistant.HISTORICAL_ASSISTANT]))
        value = json.loads(output.getvalue())['preparation']
        self.assertEqual('0.033768', value['reserve_amount'])
        self.assertEqual(MODEL, value['requested_configuration']['model'])
        self.assertEqual('OpenRouter', value['requested_configuration']['provider'])
        self.assertEqual(assistant.HISTORICAL_ASSISTANT, value['requested_configuration']['profile_id'])
        self.assertEqual(assistant.profile_digest(assistant.HISTORICAL_PROFILE),
                         value['requested_configuration']['profile_sha256'])
        self.assertIn('reservation_estimate', value['requested_configuration'])
        store.assert_not_called()

    def test_forecast_refuses_model_distinct_from_profile_before_http(self):
        self.responses()
        with redirect_stdout(io.StringIO()) as output, patch.object(assistant, 'HTTPSConnection') as inference:
            self.assertEqual(78, runtime.main(['forecast-prices', '--model', 'openrouter/auto',
                                              '--input-tokens', '1000', '--output-tokens', '16384',
                                              '--preparation-assistant', assistant.HISTORICAL_ASSISTANT]))
        self.assertEqual('HOLD', json.loads(output.getvalue())['state'])
        self.http.request.assert_not_called()
        inference.assert_not_called()

    def test_missing_prices_do_not_hide_known_components_and_zero_is_explicit(self):
        self.responses([{**ENDPOINT, 'pricing': {'prompt': '0', 'completion': '0.000002'}}])
        row = prices.forecast(MODEL, 100, 50)['endpoints'][0]
        self.assertEqual('0', row['rates']['prompt']['amount'])
        self.assertIsNone(row['rates']['input_cache_read']['amount'])
        self.assertEqual('0.000100', row['forecast']['token_subtotal_usd'])
        self.responses([{**ENDPOINT, 'pricing': {'prompt': '0.000001'}}])
        row = prices.forecast(MODEL, 100, 50)['endpoints'][0]
        self.assertEqual('0.000100', row['forecast']['components_usd']['prompt'])
        self.assertIsNone(row['forecast']['components_usd']['completion'])
        self.assertIsNone(row['forecast']['token_subtotal_usd'])
        self.responses([{**ENDPOINT, 'pricing': None}])
        self.assertIsNone(prices.forecast(MODEL, 100, 50)['endpoints'][0]['rates']['prompt']['amount'])

    def test_conditional_rates_are_visible_without_applying_an_unresolved_price(self):
        endpoint = deepcopy(ENDPOINT)
        endpoint['pricing']['discount'] = 0.5
        self.responses([endpoint])
        row = prices.forecast(MODEL, 100, 50)['endpoints'][0]
        self.assertEqual('0.000200', row['forecast']['token_subtotal_usd'])
        self.assertTrue(row['forecast']['discount_unresolved'])
        self.assertEqual('API_VALUES_BEFORE_DISCOUNT_APPLICATION', row['forecast']['price_basis'])
        self.assertEqual(0.5, row['pricing_raw']['discount'])
        self.assertEqual(prices.DISCOUNT_SOURCE, row['forecast']['discount_source'])
        self.assertIsNone(row['forecast']['total_usd'])
        endpoint['pricing']['discount'] = 0
        self.responses([endpoint])
        self.assertFalse(prices.forecast(MODEL, 100, 50)['endpoints'][0]['forecast']['discount_unresolved'])
        endpoint['pricing']['overrides'] = [{'min_prompt_tokens': 10, 'prompt': '0.000004'}]
        self.responses([endpoint])
        row = prices.forecast(MODEL, 100, 50)['endpoints'][0]
        self.assertEqual(endpoint['pricing'], row['pricing_raw'])
        self.assertTrue(row['forecast']['conditional_pricing_unresolved'])
        self.assertEqual('0.000500', row['forecast']['token_subtotal_usd'])
        self.assertEqual('0.000004', row['rates']['prompt']['amount'])

    def test_palier_horaire_inconnu_conserve_le_tarif_de_base(self):
        pricing = {'prompt': '0.000001', 'completion': '0.000002', 'overrides': [{
            'utc_days': [1, 2, 3, 4, 5], 'utc_start': '08:00', 'utc_end': '18:00',
            'prompt': '0.000004', 'completion': '0.000008'}]}
        row = prices.price_row(pricing, {'prompt': 100, 'completion': 50})
        self.assertEqual('0.000001', row['rates']['prompt']['amount'])
        self.assertEqual('0.000002', row['rates']['completion']['amount'])
        self.assertTrue(row['forecast']['conditional_pricing_unresolved'])

    def test_bad_identity_quantities_prices_and_responses_are_rejected_without_retry(self):
        for model, inp, out, cache in [('https://example.org', 1, 1, 0), (MODEL, -1, 1, 0),
                                      (MODEL, 1, 1, 2), (MODEL, True, 1, 0), (MODEL, None, 1, 0)]:
            with self.assertRaises(ValueError): prices.forecast(model, inp, out, cache)
        self.http.request.assert_not_called()
        for value in ('-1', 'NaN', 'Infinity', '', True, 0.1):
            self.responses([{**ENDPOINT, 'pricing': {'prompt': value}}])
            with self.assertRaises(ValueError): prices.forecast(MODEL, 1, 1)
        self.responses(summary={**SUMMARY, 'id': 'alias/other'})
        with self.assertRaises(ValueError): prices.forecast(MODEL, 1, 1)
        self.responses([{**ENDPOINT, 'model_id': 'alias/other'}])
        with self.assertRaises(ValueError): prices.forecast(MODEL, 1, 1)
        for status, length, raw in [(302, 0, b'{}'), (429, 0, b'{}'), (200, 17, b'{}'),
                                    (200, 0, b'{"data":{},"data":{}}'), (200, 0, b'{"data":NaN}'),
                                    (200, 0, b'x' * (prices.MAX_RESPONSE_BYTES + 1))]:
            self.http.reset_mock()
            self.response.status, self.response.length = status, length
            self.response.read.side_effect = None
            self.response.read.return_value = raw
            with self.assertRaises(ValueError): prices.forecast(MODEL, 1, 1)
            self.assertEqual(1, self.http.request.call_count)
        for failure in (TimeoutError(), IncompleteRead(b'{}', 10)):
            self.response.read.side_effect = failure
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(78, runtime.main(['forecast-prices', '--model', MODEL, '--input-tokens', '1', '--output-tokens', '1']))
            self.assertEqual('HOLD', json.loads(output.getvalue())['state'])


if __name__ == '__main__':
    unittest.main()
