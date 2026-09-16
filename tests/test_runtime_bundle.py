import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from benchmark import service
from tools.build_runtime import build


class RuntimeBundleTests(unittest.TestCase):
    def test_identite_absente_se_replie_sur_git_et_invalide_est_refusee(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = root / 'benchmark'
            package.mkdir()
            module = package / 'service.py'
            module.write_text('')
            subprocess.run(['git', '-C', str(root), 'init'], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(root), 'add', 'benchmark/service.py'], check=True)
            tree = subprocess.check_output(['git', '-C', str(root), 'write-tree']).decode().strip()
            commit = subprocess.check_output(
                ['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@invalid',
                 'commit-tree', tree, '-m', 'Fixture contrôlée']).decode().strip()
            subprocess.run(['git', '-C', str(root), 'update-ref', 'HEAD', commit], check=True)
            with patch.object(service, '__file__', str(module)):
                self.assertEqual(commit, service.release_identity())
                for source in (42, 'pas-hexadécimal'):
                    with self.subTest(source=source):
                        (root / 'release.json').write_text(json.dumps({'source_sha': source}))
                        with self.assertRaisesRegex(ValueError, '^Identité de release invalide$'):
                            service.release_identity()

    def test_build_is_commit_bound_reproducible_and_runtime_initializes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = root / 'source'
            repo.mkdir()
            package = repo / 'benchmark'
            package.mkdir()
            source_package = Path(__file__).resolve().parents[1] / 'benchmark'
            for name in ('__init__.py', 'model_catalog.py', 'model_catalogue.py', 'models.toml', 'storage.py', 'preparation.py', 'runtime.py', 'service.py', 'openrouter_preparation.py', 'openrouter_qualification.py', 'openrouter_prices.py', 'outgoing.py', 'preparation.profile.json', 'preparation-fallback.profile.json', 'qualification.profile.json', 'glm-5.3-flash.profile.json', 'benchmark-runtime'):
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
            self.assertEqual(commit, manifest['source_sha'])
            self.assertIn('benchmark/models.toml', manifest['files'])
            for name, expected in manifest['files'].items():
                self.assertEqual(expected, hashlib.sha256((unpacked / name).read_bytes()).hexdigest())
            self.assertEqual(0o755, (unpacked / 'benchmark/benchmark-runtime').stat().st_mode & 0o777)
            result = subprocess.run([sys.executable, str(unpacked / 'benchmark/benchmark-runtime'), 'initialize', '--data', str(root / 'private')], cwd=root, check=True, capture_output=True, text=True)
            self.assertEqual('INITIALIZED_ADMISSION_BLOCKED', json.loads(result.stdout)['state'])
            result = subprocess.run([sys.executable, str(unpacked / 'benchmark/benchmark-runtime'), 'verify', '--data', str(root / 'private')], cwd=root, check=True, capture_output=True, text=True)
            self.assertTrue(json.loads(result.stdout)['integrity_ok'])
            subprocess.run([sys.executable, '-c', 'from benchmark.service import serve_executor; from benchmark_web.server import serve_web'], cwd=unpacked, check=True)
            subprocess.run([sys.executable, '-c', 'from benchmark.openrouter_prices import forecast; from benchmark.openrouter_preparation import configuration; assert configuration()["model"] == "openai/gpt-6-astra"'], cwd=unpacked, check=True)
            subprocess.run([sys.executable, '-c', 'from benchmark.openrouter_qualification import OpenRouterQualification; assert OpenRouterQualification("fixture").configuration()["model"] == "anthropic/claude-fable-5.1"'], cwd=unpacked, check=True)
            # La configuration active du catalogue doit se charger depuis l'archive,
            # sans aucun models.toml à la racine du dépôt source
            subprocess.run([sys.executable, '-c',
                            'from pathlib import Path; from benchmark import model_catalogue; '
                            'assert model_catalogue.CONFIG_PATH == Path("benchmark/models.toml").resolve(), model_catalogue.CONFIG_PATH; '
                            'settings = model_catalogue._settings(model_catalogue._registry()); '
                            'assert (settings["max_per_family"], settings["max_age_days"], settings["cache_hours"]) == (3, 365, 24); '
                            'assert len(settings["makers"]) == 16; '
                            'assert (len(settings["baseline_families"]), len(settings["baseline_models"])) == (81, 139); '
                            'assert model_catalogue.tiers() == {"deepseek": {"enhanced": {"enabled": True}}}'],
                           cwd=unpacked, check=True)
            # Un commit sans configuration de catalogue ne produit pas d'archive
            git('rm', '--cached', '--quiet', 'benchmark/models.toml')
            stripped = git('-c', 'user.name=Test', '-c', 'user.email=test@invalid', 'commit-tree', git('write-tree'), '-m', 'Sans configuration de catalogue')
            with self.assertRaisesRegex(ValueError, '^Interfaces runtime absentes du commit$'):
                build(repo, stripped, root / 'stripped.tar.gz')
            self.assertFalse((root / 'stripped.tar.gz').exists())
            subprocess.run([sys.executable, '-c',
                            'from benchmark import VERSION; from benchmark.service import release_identity; '
                            'assert VERSION == "0.1.0"; assert release_identity() == "' + commit + '"'],
                           cwd=unpacked, check=True)
            (unpacked / 'release.json').unlink()
            subprocess.run([sys.executable, '-c',
                            'from benchmark.service import release_identity; assert release_identity() == "inconnu"'],
                           cwd=unpacked, check=True)
            subprocess.run(['git', '-C', str(root), 'init'], check=True, capture_output=True)
            (root / 'foreign.txt').write_text('Dépôt étranger')
            subprocess.run(['git', '-C', str(root), 'add', 'foreign.txt'], check=True)
            foreign_tree = subprocess.check_output(['git', '-C', str(root), 'write-tree']).decode().strip()
            foreign = subprocess.check_output(
                ['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@invalid',
                 'commit-tree', foreign_tree, '-m', 'Foreign fixture']).decode().strip()
            subprocess.run(['git', '-C', str(root), 'update-ref', 'HEAD', foreign], check=True)
            subprocess.run([sys.executable, '-c',
                            'from benchmark.service import release_identity; assert release_identity() == "inconnu"'],
                           cwd=unpacked, check=True)
            subprocess.run(['git', '-C', str(unpacked), 'init'], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(unpacked), 'add', 'benchmark', 'benchmark_web'], check=True)
            tree = subprocess.check_output(['git', '-C', str(unpacked), 'write-tree']).decode().strip()
            fallback = subprocess.check_output(
                ['git', '-C', str(unpacked), '-c', 'user.name=Test', '-c', 'user.email=test@invalid',
                 'commit-tree', tree, '-m', 'Fallback fixture']).decode().strip()
            subprocess.run(['git', '-C', str(unpacked), 'update-ref', 'HEAD', fallback], check=True)
            subprocess.run([sys.executable, '-c',
                            'from benchmark.service import release_identity; assert release_identity() == "' + fallback + '"'],
                           cwd=unpacked, check=True)
            (unpacked / 'release.json').write_text('{')
            subprocess.run([sys.executable, '-c',
                            'from benchmark.service import release_identity; '
                            'exec("try:\\n release_identity()\\nexcept ValueError:\\n pass\\nelse:\\n raise AssertionError")'],
                           cwd=unpacked, check=True)


if __name__ == '__main__':
    unittest.main()
