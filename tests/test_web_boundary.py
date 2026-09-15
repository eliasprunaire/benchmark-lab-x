"""La présentation dépend du moteur ; le moteur ne dépend pas de la présentation."""
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from benchmark import campaigns, preparation, restitution
from benchmark_web import views
from tests.test_s6_regressions import build

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'benchmark'
WEB = ROOT / 'benchmark_web'


class PageContent(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fragments = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        validation_hash = (tag == 'input' and attributes.get('type') == 'hidden'
                           and attributes.get('name') == 'package_sha256')
        self.fragments.append(tag)
        for name, value in attrs:
            self.fragments.append(name)
            if value is not None and not (validation_hash and name == 'value'):
                self.fragments.append(value)

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        self.fragments.append(tag)

    def handle_data(self, data):
        self.fragments.append(data)

    def handle_comment(self, data):
        self.fragments.append(data)


class WebBoundaryTests(unittest.TestCase):
    def assert_no_fingerprint_field(self, value, allowed=()):
        if type(value) is dict:
            for key, item in value.items():
                if key not in allowed:
                    self.assertFalse(key == 'sha256' or key in ('fingerprint', 'digest') or key.endswith('_sha256'), key)
                self.assert_no_fingerprint_field(item, allowed)
        elif type(value) is list:
            for item in value:
                self.assert_no_fingerprint_field(item, allowed)

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

    def test_page_views_expose_no_fingerprint_except_validation_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            with patch('socket.socket.connect', side_effect=AssertionError('No network')):
                store, session_id, _, _, _ = build(data)
            with closing(store):
                values = [
                    preparation.view(store, session_id, 'fixture'),
                    campaigns.launch_view(store, session_id, 'fixture', 'comparison'),
                    restitution.comparison(store, session_id, 'fixture', 'comparison'),
                    restitution.detail(store, session_id, 'fixture', 'comparison', 'attempt-error'),
                ]
                self.assert_no_fingerprint_field(values[0], {'package_sha256'})
                for value in values[1:]:
                    self.assert_no_fingerprint_field(value)
                pages = [views.render(value, '').decode() for value in values]
        for page in pages:
            content = PageContent()
            content.feed(page)
            self.assertIsNone(re.search(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])',
                                        '\n'.join(content.fragments), re.IGNORECASE))


if __name__ == '__main__':
    unittest.main()
