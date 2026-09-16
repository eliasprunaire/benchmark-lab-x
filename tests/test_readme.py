"""Ancrages du README moteur, hors tests de frontière web."""
from pathlib import Path
import unittest

README = Path(__file__).resolve().parents[1] / 'benchmark' / 'README.md'


class ReadmeTests(unittest.TestCase):
    def test_documentation_collecte_ancree_sur_la_commande(self):
        readme = README.read_text()
        self.assertIn('La commande `collect` du prototype envoie `data_collection: "deny"` ; '
                      'les reçus et campagnes antérieurs restent tels quels.', readme)
        self.assertNotIn('Depuis ce correctif', readme)

    def test_estimation_absente_dite_non_estimable(self):
        readme = README.read_text()
        self.assertIn('estimation non estimable', readme)
        self.assertNotIn('non calculable', readme)
