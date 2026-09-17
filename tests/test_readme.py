"""Ancrages du README moteur, hors tests de frontière web."""
from pathlib import Path
import unittest

README = Path(__file__).resolve().parents[1] / 'benchmark' / 'README.md'


class ReadmeTests(unittest.TestCase):
    def test_estimation_absente_dite_non_estimable(self):
        readme = README.read_text()
        self.assertIn('estimation non estimable', readme)
        self.assertNotIn('non calculable', readme)
