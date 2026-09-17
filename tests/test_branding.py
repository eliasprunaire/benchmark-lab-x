"""Nom public de Bench-X et compatibilité des identifiants techniques"""
from pathlib import Path
import unittest

from benchmark import outgoing, publications, storage
from benchmark.transports import openrouter
from benchmark_web import projection


ROOT = Path(__file__).resolve().parents[1]


class BrandingTests(unittest.TestCase):
    def test_bench_x_is_the_public_name_on_documented_and_rendered_surfaces(self):
        readme = (ROOT / 'README.md').read_text()
        template = (ROOT / 'benchmark_web/templates/preparation.html').read_text()
        public_page = projection.public_page({
            'need': 'Besoin fictif', 'task': {'dossier_id': 'd', 'version': 1},
            'campaign_id': 'c', 'result_expected': 'Résultat fictif',
            'conclusion': {'text': 'Conclusion fictive', 'limits': []},
            'coverage': {}, 'population': {}, 'conditions': {}, 'cost_basis': {},
            'economic_status': 'COMPLETE', 'rows': [], 'columns': [],
        }, {}).decode()
        profile = openrouter.load_profile(openrouter.ASSISTANT)

        for surface in (readme, template, public_page, profile['system']):
            with self.subTest(surface=surface[:40]):
                self.assertIn('Bench-X', surface)
                self.assertNotIn('Benchmark Lab-X', surface)
        self.assertIn('by Le Lab-X', readme)

    def test_existing_machine_identifiers_keep_their_compatibility_names(self):
        self.assertTrue(storage.PREPARATION_IDENTITY.startswith('benchmark-lab-x/'))
        self.assertTrue(outgoing.FORMAT.startswith('benchmark-lab-x/'))
        self.assertTrue(publications.SCHEMA.startswith('benchmark-lab-x/'))


if __name__ == '__main__':
    unittest.main()
