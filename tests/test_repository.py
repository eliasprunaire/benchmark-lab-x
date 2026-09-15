"""Vérifications des fichiers fondamentaux du dépôt"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryTest(unittest.TestCase):
    def test_agpl_license_is_present(self):
        license_path = ROOT / "LICENSE"

        self.assertTrue(license_path.is_file())
        self.assertEqual(
            license_path.read_text(encoding="utf-8").splitlines()[0].strip(),
            "GNU AFFERO GENERAL PUBLIC LICENSE",
        )
        self.assertFalse(license_path.read_bytes().endswith(b"\n\n"))


if __name__ == "__main__":
    unittest.main()
