"""Version Pi unique pour le runtime et la CI"""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest

from benchmark.pi_openrouter import VERSION


ROOT = Path(__file__).parents[1]


class PiVersionTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / '.github/workflows/ci.yml').read_text()

    def script(self):
        step = self.workflow.split('      - name: Vérifier la dernière version Pi publiée\n', 1)[1]
        script = []
        for line in step.split('        run: |\n', 1)[1].splitlines():
            if line and not line.startswith('          '):
                break
            script.append(line[10:] if line else '')
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
            group = Path(directory) / 'groupe'
            script = 'printf \'%s\' "$$" > "$GROUPE_PROCESSUS_TEST"\n' + self.script()
            try:
                return subprocess.run(['bash', '-e', '-c', script], env=environment | {
                    'GROUPE_PROCESSUS_TEST': str(group)}, capture_output=True, text=True,
                    timeout=30, start_new_session=True, cwd=ROOT)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(int(group.read_text()), signal.SIGKILL)
                except (FileNotFoundError, ProcessLookupError):
                    pass
                raise

    def test_ci_lit_la_version_du_runtime(self):
        self.assertNotIn(f'@{VERSION}', self.workflow)
        self.assertGreaterEqual(self.workflow.count('from benchmark.pi_openrouter import VERSION'), 2)
        job = self.workflow.split('  pi-version:\n', 1)[1]
        self.assertIn("if: github.event_name == 'schedule'", job)
        self.assertIn('timeout-minutes: 5', job)
        self.assertIn('npm view @earendil-works/pi-coding-agent version', self.workflow)

    def test_veille_accepte_la_version_epinglee_et_refuse_un_ecart(self):
        self.assertEqual(0, self.execute(VERSION).returncode)
        mismatch = self.execute('9.9.9')
        self.assertEqual(1, mismatch.returncode)
        self.assertIn('::error::Pi 9.9.9 est publié, mais le produit épingle ' + VERSION,
                      mismatch.stdout)

    def test_chaque_checkout_des_workflows_oublie_les_identifiants(self):
        workflows = ROOT / '.github/workflows'
        for path in workflows.glob('*.yml'):
            lines = path.read_text().splitlines()
            for index, line in enumerate(lines):
                if 'uses: actions/checkout@' not in line:
                    continue
                end = next((position for position in range(index + 1, len(lines))
                            if lines[position].startswith('      - ')), len(lines))
                with self.subTest(workflow=path.name, ligne=index + 1):
                    self.assertIn('persist-credentials: false', '\n'.join(lines[index + 1:end]))


if __name__ == '__main__':
    unittest.main()
