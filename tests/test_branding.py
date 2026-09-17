"""Nom public de Bench-X et compatibilité des identifiants techniques"""
from pathlib import Path
from http.client import HTTPConnection
import tempfile
import threading
import unittest
from unittest.mock import patch

from benchmark import outgoing, publications, storage
from benchmark.transports import openrouter
from benchmark_web import projection
from benchmark_web.server import serve_web


ROOT = Path(__file__).resolve().parents[1]


class BrandingTests(unittest.TestCase):
    def test_brand_assets_are_served_without_executor_or_private_storage(self):
        def check(server):
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                for method in ('GET', 'HEAD'):
                    for path, media, filename in (
                            ('/bench-x.svg', 'image/svg+xml', 'bench-x.svg'),
                            ('/favicon.ico', 'image/vnd.microsoft.icon', 'favicon.ico')):
                        with self.subTest(method=method, path=path):
                            connection = HTTPConnection(*server.server_address, timeout=2)
                            try:
                                connection.request(method, path)
                                response = connection.getresponse()
                                expected = (ROOT / 'benchmark_web/static' / filename).read_bytes()
                                self.assertEqual(response.status, 200)
                                self.assertEqual(response.getheader('Content-Type'), media)
                                self.assertEqual(int(response.getheader('Content-Length')), len(expected))
                                self.assertEqual(response.read(), expected if method == 'GET' else b'')
                            finally:
                                connection.close()
            finally:
                server.shutdown()
                thread.join()

        with tempfile.TemporaryDirectory() as directory, patch('benchmark_web.server.run', check):
            serve_web('127.0.0.1', 0, directory, directory + '/absent.sock', 'test')

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
