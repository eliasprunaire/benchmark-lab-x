import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

from tools.build_runtime import build


class RuntimeBundleTests(unittest.TestCase):
    def test_build_is_commit_bound_reproducible_and_runtime_initializes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = root / 'source'
            repo.mkdir()
            package = repo / 'benchmark'
            package.mkdir()
            source_package = Path(__file__).resolve().parents[1] / 'benchmark'
            for name in ('__init__.py', 'model_catalog.py', 'storage.py', 'preparation.py', 'runtime.py', 'service.py', 'openrouter_preparation.py', 'openrouter_prices.py', 'outgoing.py', 'glm-5.3-flash.profile.json', 'benchmark-runtime'):
                shutil.copy2(source_package / name, package / name)
            web = repo / 'benchmark_web'
            source_web = source_package.parent / 'benchmark_web'
            for name in ('__init__.py', 'server.py', 'views.py', 'projection.py', 'templates/preparation.html', 'static/preparation.css'):
                (web / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_web / name, web / name)
            (repo / 'tools').mkdir()
            shutil.copyfile(source_package.parent / 'tools/build_runtime.py', repo / 'tools/build_runtime.py')
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init')
            git('add', 'benchmark', 'benchmark_web', 'tools/build_runtime.py')
            commit = git('-c', 'user.name=Test', '-c', 'user.email=test@invalid', 'commit-tree', git('write-tree'), '-m', 'Controlled runtime fixture')
            first = build(repo, commit, root / 'first.tar.gz')
            (package / 'storage.py').write_text('Invalid uncommitted content')
            second = build(repo, commit, root / 'second.tar.gz')
            self.assertEqual(first, second)
            unpacked = root / 'release'
            unpacked.mkdir()
            with tarfile.open(root / 'first.tar.gz') as archive:
                archive.extractall(unpacked, filter='data')
            manifest = json.loads((unpacked / 'release.json').read_text())
            for name, expected in manifest['files'].items():
                self.assertEqual(expected, hashlib.sha256((unpacked / name).read_bytes()).hexdigest())
            self.assertEqual(0o755, (unpacked / 'benchmark/benchmark-runtime').stat().st_mode & 0o777)
            result = subprocess.run([sys.executable, str(unpacked / 'benchmark/benchmark-runtime'), 'initialize', '--data', str(root / 'private')], cwd=root, check=True, capture_output=True, text=True)
            self.assertEqual('INITIALIZED_ADMISSION_BLOCKED', json.loads(result.stdout)['state'])
            result = subprocess.run([sys.executable, str(unpacked / 'benchmark/benchmark-runtime'), 'verify', '--data', str(root / 'private')], cwd=root, check=True, capture_output=True, text=True)
            self.assertTrue(json.loads(result.stdout)['integrity_ok'])
            subprocess.run([sys.executable, '-c', 'from benchmark.service import serve_executor; from benchmark_web.server import serve_web'], cwd=unpacked, check=True)
            subprocess.run([sys.executable, '-c', 'from benchmark.openrouter_prices import forecast; from benchmark.openrouter_preparation import configuration; assert configuration()["model"] == "z-ai/glm-5.3-flash"'], cwd=unpacked, check=True)


if __name__ == '__main__':
    unittest.main()
