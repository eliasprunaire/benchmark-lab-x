"""Version Pi unique pour le runtime et la CI"""
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from benchmark.transports.pi import VERSION


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
            proc = subprocess.Popen(['bash', '-e', '-c', self.script()], env=environment,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    start_new_session=True, cwd=ROOT)
            try:
                stdout, stderr = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
                raise
            return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)

    def test_timeout_tue_le_groupe_et_recolte_sans_fichier(self):
        expired = subprocess.TimeoutExpired('bash', 30)
        proc = Mock(pid=1234)
        proc.communicate.side_effect = [expired, ('', '')]
        events = Mock()
        with patch.object(subprocess, 'Popen', return_value=proc) as popen, \
                patch.object(subprocess, 'run', side_effect=expired), \
                patch.object(os, 'getpgid', return_value=1234) as getpgid, \
                patch.object(os, 'killpg') as killpg:
            events.attach_mock(proc.communicate, 'communicate')
            events.attach_mock(killpg, 'killpg')
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                self.execute(VERSION)
            self.assertIs(expired, raised.exception)
            getpgid.assert_called_once_with(proc.pid)
            killpg.assert_called_once_with(1234, signal.SIGKILL)
            self.assertEqual(['communicate', 'killpg', 'communicate'],
                             [call[0] for call in events.mock_calls])
            self.assertEqual([(), ()],
                             [call.args for call in proc.communicate.call_args_list])
            self.assertEqual({'timeout': 30}, proc.communicate.call_args_list[0].kwargs)
            self.assertEqual({}, proc.communicate.call_args_list[1].kwargs)
            self.assertTrue(popen.call_args.kwargs['start_new_session'])

    def job(self):
        return re.split(r'^  [\w-]+:', self.workflow.split('  pi-version:\n', 1)[1],
                        maxsplit=1, flags=re.MULTILINE)[0]

    def test_job_s_arrete_au_nom_avec_tiret(self):
        self.workflow = '  pi-version:\n    timeout-minutes: 5\n'
        expected = self.job()
        self.workflow += '  autre-job:\n    timeout-minutes: 99\n'
        self.assertEqual(expected, self.job())

    def test_ci_lit_la_version_du_runtime(self):
        self.assertNotIn(f'@{VERSION}', self.workflow)
        self.assertGreaterEqual(self.workflow.count('from benchmark.transports.pi import VERSION'), 2)
        job = self.job()
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
        for path in workflows.glob('*.y*ml'):
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
