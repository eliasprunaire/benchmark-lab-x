from contextlib import closing
from copy import deepcopy
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

    def fetch(self, calls, statuses=None):
        def fetch(path):
            calls.append(path)
            if path == '/api/v1/models':
                return FIXTURE
            model_id = path.removeprefix('/api/v1/models/').removesuffix('/endpoints')
            tag = 'openai' if model_id == 'openai/gpt-5.6-sol' else 'fixture'
            endpoint = {'model_id': model_id, 'tag': tag, 'status': 0}
            if statuses is not None and model_id in statuses:
                endpoint['status'] = statuses[model_id]
            return {'data': {'id': model_id, 'endpoints': [endpoint]}}
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
            self.assertIsNone(free['excluded'])
            preview = next(model for model in result['models'] if model['id'].endswith('-preview'))
            self.assertEqual('preview', preview['variant'])
            self.assertIsNone(preview['excluded'])
            self.assertTrue(all(model['excluded'] is None for model in result['models']
                                if model['excluded'] != 'malformed'))
            self.assertFalse(result['stale'])

    def test_recent_generalist_ranges_without_specialists_or_variant_duplicates(self):
        rows = [dict(id=model_id, name=model_id, created=int((NOW - timedelta(days=days)).timestamp()),
                     architecture={'output_modalities': ['text']}) for model_id, days in (
            ('google/gemini-3.8-flash', 1), ('google/gemini-3.8-flash:free', 0),
            ('google/gemini-3.7-flash', 2), ('google/gemini-3.5-flash-lite', 3),
            ('google/gemini-3.1-pro-preview', 100), ('google/gemini-2.5-pro', 500),
            ('google/gemma-4-31b-it', 0), ('google/gemini-3.1-pro-preview-customtools', 0),
            ('nvidia/nemotron-3.5-content-safety', 0),
            ('nvidia/nemotron-3.5-lightning', 1), ('nvidia/nemotron-3-ultra-550b-a55b', 2),
            ('nvidia/nemotron-3-super-120b-a12b', 3),
            ('minimax/minimax-m3', 1), ('minimax/minimax-m2.7', 2),
            ('minimax/minimax-m2.5', 3), ('minimax/minimax-m2-her', 0),
            ('moonshotai/kimi-k3', 1), ('moonshotai/kimi-k2.7-code', 0),
            ('unknown/recent-1', 0))]
        def fetch(path):
            if path == '/api/v1/models':
                return {'data': rows}
            model_id = path.removeprefix('/api/v1/models/').removesuffix('/endpoints')
            return {'data': {'id': model_id, 'endpoints': [{'tag': 'fixture', 'status': 0}]}}
        with tempfile.TemporaryDirectory() as directory, closing(self.store(directory)) as store, \
                patch.object(catalogue, '_now', return_value=NOW):
            result = catalogue.refresh(store, fetch)
        self.assertEqual({'google/gemini-3.8-flash', 'google/gemini-3.5-flash-lite',
                          'google/gemini-3.1-pro-preview', 'nvidia/nemotron-3.5-lightning',
                          'nvidia/nemotron-3-ultra-550b-a55b', 'nvidia/nemotron-3-super-120b-a12b',
                          'minimax/minimax-m3', 'minimax/minimax-m2.7', 'minimax/minimax-m2.5',
                          'moonshotai/kimi-k3'},
                         {m['id'] for m in result['models'] if m['excluded'] is None})

    def test_configuration_active_livree_avec_le_paquet(self):
        self.assertEqual(Path(catalogue.__file__).resolve().parent / 'models.toml',
                         catalogue.CONFIG_PATH)
        settings = catalogue._settings(catalogue._registry())
        self.assertEqual(16, len(settings['makers']))
        self.assertEqual((3, 365, 24), (settings['max_per_maker'],
                                        settings['max_age_days'], settings['cache_hours']))
        self.assertEqual(83, len(settings['baseline_families']))
        self.assertEqual(139, len(settings['baseline_models']))
        self.assertEqual({'deepseek': {'enhanced': {'enabled': True}}}, catalogue.tiers())
        # Le registre d'alias historique est retiré : aucune source concurrente à la racine
        self.assertFalse((Path(__file__).resolve().parents[1] / 'models.toml').exists())

    def test_flagship_priorities_and_meta_namespaces_share_one_maker_quota(self):
        rows = [dict(id=model_id, name=model_id, created=int((NOW - timedelta(days=days)).timestamp()),
                     architecture={'output_modalities': ['text']}) for model_id, days in (
            ('openai/gpt-6-astra', 1), ('openai/gpt-6-astra-pro', 1),
            ('openai/gpt-5.6-luna', 2), ('openai/gpt-5.6-terra', 3),
            ('openai/gpt-5.6-sol', 4), ('meta/muse-spark-1.3', 1),
            ('meta/muse-spark-1.3-contributor', 0), ('meta/muse-glimmer-30b', 2),
            ('meta-llama/llama-4-maverick', 3), ('meta-llama/llama-4-scout', 4))]
        def fetch(path):
            if path == '/api/v1/models':
                return {'data': rows}
            model_id = path.removeprefix('/api/v1/models/').removesuffix('/endpoints')
            return {'data': {'id': model_id, 'endpoints': [{'tag': 'fixture', 'status': 0}]}}
        with tempfile.TemporaryDirectory() as directory, closing(self.store(directory)) as store, \
                patch.object(catalogue, '_now', return_value=NOW):
            result = catalogue.refresh(store, fetch)
        self.assertEqual({'openai/gpt-6-astra', 'openai/gpt-5.6-sol', 'openai/gpt-5.6-terra',
                          'meta/muse-spark-1.3', 'meta/muse-glimmer-30b', 'meta-llama/llama-4-maverick'},
                         {m['id'] for m in result['models']})
        self.assertEqual(3, sum(m['maker'] == 'meta' for m in result['models']))

    def test_statut_endpoint_exclut_seulement_un_nombre_negatif(self):
        cases = ((0, None), (1, None), (None, None), ('inconnu', None), (True, None), (-1, 'no_available_endpoint'))
        for status, excluded in cases:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory, \
                    closing(self.store(directory)) as store, \
                    patch.object(catalogue, '_now', return_value=NOW):
                result = catalogue.refresh(
                    store, self.fetch([], {'x-ai/grok-4-preview': status}))
                preview = next(model for model in result['models'] if model['id'].endswith('-preview'))
                self.assertEqual(excluded, preview['excluded'])

    def test_fournisseurs_exclus_du_catalogue(self):
        registry = tempfile.NamedTemporaryFile('w', suffix='.toml', delete=False)
        with registry:
            registry.write('''[catalogue]
makers = ["openai", "anthropic", "google", "x-ai"]
max_per_maker = 2
max_age_days = 365
cache_hours = 24
excluded_providers = ["fixture"]
''')
        self.addCleanup(Path(registry.name).unlink)
        with tempfile.TemporaryDirectory() as directory, closing(self.store(directory)) as store:
            with patch.object(catalogue, '_now', return_value=NOW), \
                    patch.object(catalogue, 'CONFIG_PATH', Path(registry.name)):
                result = catalogue.refresh(store, self.fetch([]))
        selectable = next(model for model in result['models'] if model['id'] == 'openai/gpt-5.6-sol')
        self.assertIsNone(selectable['excluded'])
        excluded = {model['id'] for model in result['models']
                    if model['excluded'] == 'provider_excluded'}
        self.assertEqual({'google/gemini-3.0-flash:free', 'openai/gpt-5.6-sol-0902',
                          'x-ai/grok-4-preview'}, excluded)

    def test_excluded_providers_invalide(self):
        registry = tempfile.NamedTemporaryFile('w', suffix='.toml', delete=False)
        with registry:
            registry.write('''[catalogue]
makers = ["openai"]
max_per_maker = 2
max_age_days = 365
cache_hours = 24
excluded_providers = "fixture"
''')
        self.addCleanup(Path(registry.name).unlink)
        with patch.object(catalogue, 'CONFIG_PATH', Path(registry.name)):
            self.assertRaises(ValueError, catalogue._settings, catalogue._registry())

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

    def test_invalid_refresh_preserves_the_last_usable_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, closing(self.store(directory)) as store:
            with patch.object(catalogue, '_now', return_value=NOW):
                first = catalogue.refresh(store, self.fetch([]))
            broken = deepcopy(FIXTURE)
            next(model for model in broken['data'] if model['id'] == 'openai/gpt-5.6-sol')['context_length'] = 0
            fetch = self.fetch([])
            with patch.object(catalogue, '_now', return_value=NOW + timedelta(hours=24)):
                result = catalogue.refresh(store, lambda path: broken if path == '/api/v1/models' else fetch(path))
                self.assertTrue(result['stale'])
                self.assertEqual(first['fetched_at'], result['fetched_at'])
                self.assertEqual(first['models'], catalogue.selection(store)['models'])
                renewed = catalogue.refresh(store, self.fetch([]))
                self.assertFalse(renewed['stale'])
                self.assertNotEqual(first['fetched_at'], renewed['fetched_at'])

    def test_endpoint_alias_is_excluded_without_hiding_verified_models(self):
        with tempfile.TemporaryDirectory() as directory, closing(self.store(directory)) as store:
            fixture_fetch = self.fetch([])
            def fetch(path):
                result = fixture_fetch(path)
                if path == '/api/v1/models/x-ai/grok-4-preview/endpoints':
                    result['data']['id'] = 'x-ai/grok-4'
                return result
            with patch.object(catalogue, '_now', return_value=NOW):
                result = catalogue.refresh(store, fetch)
                stored = catalogue.selection(store)
            self.assertEqual(result, stored)
            alias = next(m for m in stored['models'] if m['id'] == 'x-ai/grok-4-preview')
            self.assertEqual('endpoint_identity_mismatch', alias['excluded'])
            self.assertIsNone(alias['route'])
            self.assertNotIn('x-ai/grok-4', [m['id'] for m in stored['models']])
            self.assertIn('openai/gpt-5.6-sol', [m['id'] for m in stored['models'] if m['excluded'] is None])

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
        self.assertEqual('preview:free', catalogue._variant('openai/gpt-5-preview:free'))
        self.assertEqual('exp:free', catalogue._variant('openai/gpt-5-exp:free'))

    def test_identifiant_refuse_les_segments_de_chemin(self):
        self.assertEqual('openai/modele_test', catalogue._model_id({'id': 'openai/modele_test'}))
        for model_id in ('./modele', '../modele', 'openai/.', 'openai/..'):
            with self.subTest(model_id=model_id):
                self.assertIsNone(catalogue._model_id({'id': model_id}))

    def test_rapport_signale_une_famille_nouvelle_et_un_modele_disparu(self):
        registry = {
            'catalogue': {'makers': ['openai'], 'max_per_maker': 2,
                          'max_age_days': 365, 'cache_hours': 24,
                          'baseline_models': ['openai/ancien-1', 'openai/ancien-2'],
                          'baseline_fetched_at': '2026-09-01T00:00:00Z'},
            'ancien': {'model': 'openai/ancien-1', 'provider': 'openai'},
        }
        models = [{
            'id': 'openai/nouveau-2', 'name': 'Nouveau', 'created': int(NOW.timestamp()),
            'architecture': {'output_modalities': ['text']},
        }, {
            'id': 'openai/ancien-2', 'name': 'Ancien', 'created': 'invalide',
            'architecture': {'output_modalities': ['text']},
        }]
        self.assertEqual(
            {'new_families': ['openai/nouveau-#'], 'missing_models': ['openai/ancien-1'],
             'malformed_models': ['openai/ancien-2']},
            catalogue.report(models, registry, NOW))
        self.assertEqual(catalogue.report(FIXTURE['data'], registry, NOW)['missing_models'],
                         ['openai/ancien-1', 'openai/ancien-2'])


if __name__ == '__main__':
    unittest.main()
