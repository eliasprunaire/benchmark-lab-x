"""Retirement blocks new calls without rewriting historical operations"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark import storage, preparation, model_catalog
from benchmark.transports import prices as openrouter_prices
from benchmark_web import views
from tests.test_storage import operation, PAYLOAD


class ModelRetirementTests(unittest.TestCase):
    def test_catalog_replacement_without_sitewide_notice(self):
        page = views.render(dict(error='Fictional error'), 'csrf', error=True).decode()
        self.assertNotIn('DeepSeek V4 Flash 0731 est retiré', page)
        model_catalog.require_current(dict(model='deepseek-flash'))
        model_catalog.require_current(dict(model=model_catalog.DEEPSEEK_REPLACEMENT))

    def test_retired_models_block_before_forecast_network(self):
        with patch.object(openrouter_prices, 'read_public') as network:
            for model in ('deepseek/deepseek-v4-flash-0731', 'deepseek-v4-flash-0731', 'deepseek/deepseek-v4-flash'):
                with self.subTest(model=model), self.assertRaisesRegex(ValueError, 'V4.1 Flash'):
                    openrouter_prices.forecast(model, 10, 10)
        network.assert_not_called()

    def test_old_intent_stays_readable_but_cannot_emit_or_reserve_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'private'
            storage.initialize(root)
            store = storage.Store(root)
            self.addCleanup(store.close)
            store.save_dossier('d', 1, PAYLOAD)
            store.create_budget('b', '10', 'TEST')
            old = operation()
            old['requested_configuration']['model'] = 'deepseek/deepseek-v4-flash-0731'
            with patch.object(storage, 'require_current'):
                store.reserve_intent(old, 'b', '1')
            before = store.inspect_operations()
            with self.assertRaisesRegex(ValueError, 'V4.1 Flash'):
                store.mark_emission_possible('op')
            old['operation_id'] = 'new'
            with self.assertRaisesRegex(ValueError, 'V4.1 Flash'):
                store.reserve_intent(old, 'b', '1')
            self.assertEqual(before, store.inspect_operations())
            self.assertEqual('1', store.inspect_budget('b')['reserved'])
