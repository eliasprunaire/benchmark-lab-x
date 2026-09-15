"""Version Pi unique pour le runtime et la CI"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from benchmark.pi_openrouter import VERSION


class PiVersionTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (Path(__file__).parents[1] / '.github/workflows/ci.yml').read_text()

    def script(self):
        step = self.workflow.split('      - name: Vérifier la dernière version Pi publiée\n', 1)[1]
        script = []
        for line in step.split('        run: |\n', 1)[1].splitlines():
            if not line.startswith('          '):
                break
            script.append(line[10:])
        return '\n'.join(script)

    def execute(self, version):
        with tempfile.TemporaryDirectory() as directory:
            npm = Path(directory) / 'npm'
            npm.write_text("#!/bin/sh\nprintf '%s\\n' \"$FAKE_NPM_VERSION\"\n")
            npm.chmod(0o755)
            environment = os.environ | {
                'FAKE_NPM_VERSION': version,
                'PATH': directory + os.pathsep + os.environ['PATH'],
            }
            return subprocess.run(['bash', '-e', '-c', self.script()], env=environment,
                                  capture_output=True, text=True)

    def test_ci_lit_la_version_du_runtime(self):
        self.assertNotIn(f'@{VERSION}', self.workflow)
        self.assertGreaterEqual(self.workflow.count('from benchmark.pi_openrouter import VERSION'), 2)
        self.assertIn("if: github.event_name == 'schedule'", self.workflow)
        self.assertIn('timeout-minutes: 5', self.workflow)
        self.assertIn('npm view @earendil-works/pi-coding-agent version', self.workflow)

    def test_veille_accepte_la_version_epinglee_et_refuse_un_ecart(self):
        self.assertEqual(0, self.execute(VERSION).returncode)
        mismatch = self.execute('9.9.9')
        self.assertEqual(1, mismatch.returncode)
        self.assertIn('::error::Pi 9.9.9 est publié, mais le produit épingle ' + VERSION,
                      mismatch.stdout)


if __name__ == '__main__':
    unittest.main()
