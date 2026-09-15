from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark import campaigns, model_catalogue, pi_openrouter, preparation, qualification, storage
from tests.test_s3_regressions import ACTOR, AUTHORITY, check, fixture, specification


NOW = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)


def model(model_id, maker, efforts=None, prompt='0.000002', completion='0.00001'):
    value = {
        'id': model_id, 'name': model_id, 'created': int(NOW.timestamp()),
        'architecture': {'output_modalities': ['text']},
        'pricing': {'prompt': prompt, 'completion': completion},
        'top_provider': {'max_completion_tokens': 8192}, 'context_length': 64000,
    }
    if efforts is not None:
        value['reasoning'] = {'supported_efforts': efforts}
    return value, {'id': model_id, 'endpoints': [{'model_id': model_id, 'tag': maker}]}


class ConfigurationsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='configurations-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name).resolve() / 'private'
        self.session, self.preview, reference = fixture(self.data)
        qualification.initialize(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        candidate = qualification.draft(
            self.store, 'fixture', self.preview['revision'], specification(reference))
        qualified = qualification.qualify(
            self.store, candidate['contract_sha256'], reviewer=ACTOR, check=check)
        qualification.approve(
            self.store, candidate['contract_sha256'], qualified['qualification_id'],
            actor=ACTOR, authority=AUTHORITY)
        campaigns.initialize(self.data)
        rows = [
            model('openai/gpt-5.6-sol', 'openai', ['low', 'medium', 'high']),
            model('deepseek/deepseek-v4.1-flash', 'deepseek', []),
            model('mistralai/mistral-medium-3-5', 'mistral', ['low']),
            model('openai/gpt-5.5-sol', 'absent', ['high']),
        ]
        document = {'models': [row[0] for row in rows],
                    'endpoints': {row[0]['id']: row[1] for row in rows}}
        self.store._connection.execute(model_catalogue.TABLE_SQL)
        self.store._connection.execute(
            'INSERT INTO s2_model_catalogue VALUES (?,?)',
            (NOW.isoformat(), storage._strict_json(document)))
        self.identity = {
            'package': '@earendil-works/pi-coding-agent', 'version': '0.85.1',
            'sha256': '1' * 64, 'bridge_sha256': '2' * 64,
            'node_version': 'v24.0.0', 'node_sha256': '3' * 64,
            'scope': 'Fixture Pi locale',
        }

    def prepare(self, models, tier='standard'):
        with patch.object(model_catalogue, '_now', return_value=NOW):
            return campaigns.prepare_configurations(
                self.store, self.session, 'fixture', {'models': models, 'tier': tier}, self.identity)

    def test_refuse_moins_de_deux_modeles_et_modele_exclu(self):
        with self.assertRaisesRegex(ValueError, 'Au moins deux modèles'):
            self.prepare(['openai/gpt-5.6-sol'])
        with self.assertRaisesRegex(ValueError, 'exclu'):
            self.prepare(['openai/gpt-5.6-sol', 'openai/gpt-5.5-sol'])

    def test_resout_standard_high_tiers_et_non_reglable_sans_low(self):
        standard = self.prepare(
            ['openai/gpt-5.6-sol', 'mistralai/mistral-medium-3-5'])
        self.assertEqual(['off', 'off'], [item['effort'] for item in standard['configurations']])
        self.assertTrue(all('reasoning' not in item['parameters']
                            for item in standard['configurations']))

        enhanced = self.prepare(
            ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], 'enhanced')
        by_model = {item['model']: item for item in enhanced['configurations']}
        self.assertEqual({'effort': 'high'},
                         by_model['openai/gpt-5.6-sol']['parameters']['reasoning'])
        self.assertEqual({'enabled': True},
                         by_model['deepseek/deepseek-v4.1-flash']['parameters']['reasoning'])
        self.assertEqual('on', by_model['deepseek/deepseek-v4.1-flash']['effort'])
        self.assertNotIn('low', enhanced['available_tiers'])

        fixed = self.prepare(
            ['mistralai/mistral-medium-3-5', 'openai/gpt-5.6-sol'], 'enhanced')
        unadjustable = next(item for item in fixed['configurations']
                            if item['model'] == 'mistralai/mistral-medium-3-5')
        self.assertEqual('not_adjustable', unadjustable['effort_limit'])
        self.assertNotIn('reasoning', unadjustable['parameters'])

        transport = object.__new__(pi_openrouter.PiOpenRouter)
        payload = transport._payload(by_model['deepseek/deepseek-v4.1-flash'], [])
        self.assertEqual({'enabled': True}, payload['reasoning'])

    def test_estimation_reproductible_et_remplacement_append_only(self):
        first = self.prepare(
            ['openai/gpt-5.6-sol', 'mistralai/mistral-medium-3-5'])
        second = self.prepare(
            ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], 'enhanced')
        self.assertEqual('fixture-c2', second['current_campaign_id'])
        self.assertEqual(['fixture-c1'], second['superseded'])
        self.assertEqual(2, self.store._connection.execute(
            'SELECT count(*) FROM s4_campaigns').fetchone()[0])
        self.assertEqual(NOW.isoformat(), second['fetched_at'])
        self.assertEqual(1500, second['assumptions']['harness_input_tokens'])
        self.assertEqual(4096, second['assumptions']['output_tokens'])
        amounts = [Decimal(item['estimate']['amount_usd'])
                   for item in second['configurations']]
        self.assertEqual(str(sum(amounts)), second['estimate_total_usd'])
        self.assertEqual('5.00', second['cap_usd'])
        self.assertTrue(second['estimate_under_cap'])
        self.assertEqual('fixture-c1', first['current_campaign_id'])

    def test_route_refuse_pi_indisponible(self):
        token = 'token'
        body = {'csrf_token': 'csrf', 'models': ['openai/gpt-5.6-sol',
                                                 'mistralai/mistral-medium-3-5'],
                'tier': 'standard'}
        with patch.object(preparation, 'session', return_value=(self.session, 'csrf', token)):
            code, view, _, _ = preparation.dispatch(
                self.store, 'POST', '/preparation/dossiers/fixture/configurations',
                token, deepcopy(body), 'a' * 40, True)
        self.assertEqual(503, code)
        self.assertEqual('CANDIDATE_PI_UNAVAILABLE', view['error_code'])
        with patch.object(preparation, 'session', return_value=(self.session, 'csrf', token)), \
                patch.object(model_catalogue, '_now', return_value=NOW):
            code, created, _, _ = preparation.dispatch(
                self.store, 'POST', '/preparation/dossiers/fixture/configurations',
                token, deepcopy(body), 'a' * 40, True, candidate_identity=self.identity)
            get_code, current, _, _ = preparation.dispatch(
                self.store, 'GET', '/preparation/dossiers/fixture/configurations',
                token, None, 'a' * 40, True, candidate_identity=self.identity)
        self.assertEqual(201, code)
        self.assertEqual('fixture-c1', created['current_campaign_id'])
        self.assertEqual(200, get_code)
        self.assertEqual(created, current)


if __name__ == '__main__':
    unittest.main()
