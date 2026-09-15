"""Version Pi unique pour le runtime et la CI"""
from pathlib import Path
import unittest

from benchmark.pi_openrouter import VERSION


class PiVersionTests(unittest.TestCase):
    def test_ci_lit_la_version_du_runtime(self):
        workflow = (Path(__file__).parents[1] / '.github/workflows/ci.yml').read_text()
        self.assertNotIn(f'@{VERSION}', workflow)
        self.assertGreaterEqual(workflow.count('from benchmark.pi_openrouter import VERSION'), 2)
        self.assertIn("if: github.event_name == 'schedule'", workflow)
        self.assertIn('npm view @earendil-works/pi-coding-agent version', workflow)


if __name__ == '__main__':
    unittest.main()
