"""La présentation dépend du moteur ; le moteur ne dépend pas de la présentation."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'benchmark'
WEB = ROOT / 'benchmark_web'


class WebBoundaryTests(unittest.TestCase):
    def test_engine_never_imports_the_web_package(self):
        for path in ENGINE.rglob('*.py'):
            source = path.read_text()
            self.assertNotRegex(source, r'^\s*(from|import)\s+benchmark_web\b', path.name)

    def test_engine_holds_no_html_markup_for_the_web_journey(self):
        for name in ('preparation.py', 'restitution.py', 'service.py'):
            source = (ENGINE / name).read_text()
            self.assertNotRegex(source, r'<(html|body|main|section|table|form|details)\b', name)

    def test_web_never_opens_storage_or_providers(self):
        for path in WEB.rglob('*.py'):
            source = path.read_text()
            self.assertNotRegex(source, r'\b(sqlite3|Store\(|storage\.Store|urlopen|OPENROUTER|API_KEY)\b', path.name)
            self.assertNotRegex(source, r'from benchmark\.(pi_|openrouter_|outgoing|recovery|judgment)', path.name)
            self.assertNotIn('code_verifier', source, path.name)
            self.assertNotIn('openrouter.ai', source, path.name)

    def test_composition_root_names_the_presentation_explicitly(self):
        runtime = (ENGINE / 'runtime.py').read_text()
        self.assertIn("'--presentation'", runtime)
        self.assertEqual(1, len(re.findall(r"default='benchmark_web\.projection'", runtime)))


if __name__ == '__main__':
    unittest.main()
