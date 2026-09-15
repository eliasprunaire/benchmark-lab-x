from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark import model_catalogue as catalogue, storage


FIXTURE = json.loads(
    (Path(__file__).parent / 'fixtures/openrouter-models.json').read_text(encoding='utf-8'))
NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


class ModelCatalogueTests(unittest.TestCase):
    def store(self, directory):
        root = Path(directory).resolve() / 'private'
        storage.initialize(root)
        storage.initialize_preparation(root)
        return storage.Store(root)

    def fetch(self, calls):
        def fetch(path):
            calls.append(path)
            if path == '/api/v1/models':
                return FIXTURE
            model_id = path.removeprefix('/api/v1/models/').removesuffix('/endpoints')
            tag = 'openai' if model_id == 'openai/gpt-5.6-sol' else 'fixture'
            return {'data': {'id': model_id, 'endpoints': [
                {'model_id': model_id, 'tag': tag}]}}
        return fetch

    def test_fixture_exerce_toutes_les_regles_et_la_vue(self):
        self.assertEqual(60, len(FIXTURE['data']))
        with tempfile.TemporaryDirectory() as directory, closing(self.store(directory)) as store:
            calls = []
            with patch.object(catalogue, '_now', return_value=NOW):
                result = catalogue.refresh(store, self.fetch(calls))
            self.assertEqual(6, len(calls))
            malformed = [model for model in result['models'] if model['excluded'] == 'malformed']
            self.assertEqual({
                'openai/malformed-created',
                'anthropic/malformed-name',
                'google/malformed-architecture',
            }, {model['id'] for model in malformed})
            self.assertNotIn('openai/gpt-5.3-sol:free', [model['id'] for model in result['models']])
            self.assertEqual([
                'google/gemini-3.0-flash:free',
                'openai/gpt-5.6-sol-0902',
                'openai/gpt-5.6-sol',
                'openai/gpt-5.5-sol',
                'x-ai/grok-4-preview',
            ], [model['id'] for model in result['models'] if model['excluded'] != 'malformed'])
            self.assertNotIn('openai/gpt-5.4-sol', [model['id'] for model in result['models']])
            current = next(model for model in result['models']
                           if model['id'] == 'openai/gpt-5.6-sol')
            self.assertEqual('openai/gpt-#-sol', current['family'])
            self.assertEqual('2026-07-09', current['released'])
            self.assertEqual('2.000000', current['input_price_per_million'])
            self.assertEqual('10.00000', current['output_price_per_million'])
            self.assertEqual(['low', 'medium', 'high'], current['reasoning_levels'])
            self.assertEqual(128000, current['max_output_tokens'])
            self.assertIsNone(current['variant'])
            self.assertIsNone(current['excluded'])
            free = next(model for model in result['models'] if model['id'].endswith(':free'))
            self.assertEqual('free', free['variant'])
            self.assertEqual('no_compliant_provider', free['excluded'])
            preview = next(model for model in result['models'] if model['id'].endswith('-preview'))
            self.assertEqual('preview', preview['variant'])
            self.assertFalse(result['stale'])

    def test_cache_24_heures_et_releve_indisponible(self):
        with tempfile.TemporaryDirectory() as directory, closing(self.store(directory)) as store:
            calls = []
            with patch.object(catalogue, '_now', return_value=NOW):
                first = catalogue.refresh(store, self.fetch(calls))
            with patch.object(catalogue, '_now', return_value=NOW + timedelta(hours=23)):
                second = catalogue.refresh(store, lambda path: self.fail('appel réseau inattendu'))
            self.assertEqual(first['fetched_at'], second['fetched_at'])
            self.assertFalse(second['stale'])
            with patch.object(catalogue, '_now', return_value=NOW + timedelta(hours=25)):
                stale = catalogue.refresh(store, lambda path: (_ for _ in ()).throw(OSError('indisponible')))
            self.assertEqual(first['fetched_at'], stale['fetched_at'])
            self.assertTrue(stale['stale'])
            self.assertTrue(stale['models'])
            with patch.object(catalogue, '_now', return_value=NOW + timedelta(hours=26)), \
                    self.assertRaises(RuntimeError):
                catalogue.refresh(store, lambda path: (_ for _ in ()).throw(RuntimeError('défaut interne')))

    def test_famille_sur_quinze_identifiants(self):
        cases = {
            'anthropic/claude-opus-5': 'anthropic/claude-opus-#',
            'openai/gpt-5.6-sol': 'openai/gpt-#-sol',
            'google/gemini-3.0-flash': 'google/gemini-#-flash',
            'x-ai/grok-4': 'x-ai/grok-#',
            'deepseek/deepseek-v4-pro': 'deepseek/deepseek-v#-pro',
            'qwen/qwen3.8-max-0902': 'qwen/qwen#-max',
            'moonshotai/kimi-k3-20260420': 'moonshotai/kimi-k#',
            'mistralai/ministral-8b-2512': 'mistralai/ministral-#b',
            'openai/gpt-5.6-sol:free': 'openai/gpt-#-sol',
            'openai/gpt-5.6-sol:batch': 'openai/gpt-#-sol',
            'z-ai/glm-5.3-flash': 'z-ai/glm-#-flash',
            'meta-llama/llama-4-maverick': 'meta-llama/llama-#-maverick',
            'cohere/command-r7b': 'cohere/command-r#b',
            'amazon/nova-2-lite': 'amazon/nova-#-lite',
            'microsoft/phi-4': 'microsoft/phi-#',
        }
        self.assertEqual(cases, {model_id: catalogue.family(model_id) for model_id in cases})
        self.assertEqual('exp', catalogue._variant('openai/gpt-5-exp'))

    def test_rapport_signale_une_famille_nouvelle_et_un_modele_disparu(self):
        registry = {
            'catalogue': {'makers': ['openai'], 'max_per_family': 3,
                          'max_age_days': 365, 'cache_hours': 24},
            'ancien': {'model': 'openai/ancien-1', 'provider': 'openai'},
        }
        models = [{
            'id': 'openai/nouveau-2', 'name': 'Nouveau', 'created': int(NOW.timestamp()),
            'architecture': {'output_modalities': ['text']},
        }]
        self.assertEqual(
            {'new_families': ['openai/nouveau-#'], 'missing_models': ['openai/ancien-1']},
            catalogue.report(models, registry, NOW))
        self.assertEqual(catalogue.report(FIXTURE['data'], registry, NOW)['missing_models'],
                         ['openai/ancien-1'])


if __name__ == '__main__':
    unittest.main()
