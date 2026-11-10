from pathlib import Path
import unittest
from unittest.mock import patch

from tools import release


class ReleaseTests(unittest.TestCase):
    def test_conventional_commit_precedence_and_zero_major_policy(self):
        self.assertEqual('patch', release.classify('fix(web): corriger la vue'))
        self.assertEqual('minor', release.classify('feat: ajouter la comparaison'))
        self.assertIsNone(release.classify('feat(ci): modifier le pipeline'))
        # Un refactor ou une optimisation fusionnés partent en production comme un correctif
        self.assertEqual('patch', release.classify('refactor(privacy): retirer un code mort'))
        self.assertEqual('patch', release.classify('perf: accélérer la page'))
        self.assertIsNone(release.classify('chore: ranger le dépôt'))
        self.assertEqual('breaking', release.classify('fix!: retirer une option'))
        self.assertEqual('breaking', release.classify('fix: changer le contrat', 'BREAKING CHANGE: option retiree'))
        self.assertEqual('0.2.0', release.bump('0.1.9', 'breaking'))
        self.assertEqual('0.10.0', release.bump('0.9.9', 'breaking'))
        self.assertEqual('0.2.0-alpha.1', release.with_pre_release('0.2.0', 'alpha.1'))

    def test_existing_tag_on_head_is_reusable_after_partial_release(self):
        with patch.object(release, 'git', side_effect=['a' * 40, 'v0.2.0-alpha.1']):
            decision = release.decision(Path('.'), 'HEAD')
        self.assertEqual('0.2.0-alpha.1', decision['version'])
        self.assertEqual('existing', decision['level'])

    def test_explicit_bootstrap_can_create_first_alpha_without_product_commit(self):
        with patch.object(release, 'git', side_effect=['a' * 40, 'b' * 40, 'b' * 40]), \
             patch.object(release, 'tag_at', return_value=None), \
             patch.object(release, 'latest_tag', return_value=None), \
             patch.object(release, 'source_version', return_value='0.1.0'), \
             patch.object(release, 'messages', return_value=[]), \
             patch.object(release, 'next_level', return_value=None):
            decision = release.decision(
                Path('.'), 'HEAD', pre_release='alpha.1',
                bootstrap_pre_release=True)
        self.assertEqual('0.2.0-alpha.1', decision['version'])
        self.assertEqual('minor', decision['level'])

    def test_non_product_commits_do_not_release(self):
        self.assertIsNone(release.next_level([('docs: mettre a jour le runbook', '')]))
        self.assertIsNone(release.next_level([('feat(ci): construire le runtime', '')]))
        self.assertEqual('minor', release.next_level([
            ('fix: corriger un bug', ''), ('feat: ajouter une option', ''),
        ]))

    def test_release_workflow_is_serialized_and_disabled_until_activation(self):
        workflow = Path('.github/workflows/release.yml').read_text()
        self.assertIn("vars.BENCHMARK_RELEASE_ENABLED == 'true'", workflow)
        self.assertIn('group: benchmark-release-main', workflow)
        self.assertIn('contents: write', workflow)
        self.assertIn('gh release create', workflow)
        self.assertIn('--version "$VERSION"', workflow)
        self.assertIn('--pre-release "$pre_release"', workflow)
        self.assertIn('--bootstrap-pre-release', workflow)
        self.assertIn('SHA256SUMS', workflow)
        self.assertIn('actions/upload-artifact@', workflow)
        self.assertIn('actions/download-artifact@', workflow)
        self.assertIn('BENCHMARK_DEPLOY_ENABLED', workflow)
        self.assertIn('runs-on: [bench-deploy]', workflow)
        self.assertNotIn('runs-on: [self-hosted,', workflow)
        self.assertIn('StrictHostKeyChecking=yes', workflow)
        self.assertIn('benchmark-delivery@10.10.0.33', workflow)
        self.assertIn('benchmark-release identity', workflow)
        self.assertNotIn('useradmin@10.10.0.33', workflow)
        self.assertIn('isPrerelease', workflow)
        self.assertIn('main_sha="$(git rev-parse origin/main^{commit})"', workflow)
        self.assertNotIn('cp "$receipt" "$RUNNER_TEMP/build-receipt.json"', workflow)

    def test_env_example_matches_the_variables_read_by_the_code(self):
        import os
        import re
        from benchmark import service
        text = Path('.env.example').read_text()
        # Blocs séparés par une ligne vide : (commentaire, noms affectés)
        blocks = []
        for block in text.split('\n\n'):
            lines = block.strip().splitlines()
            comment = ' '.join(line.lstrip('# ') for line in lines if line.startswith('#'))
            names = [line.split('=', 1)[0] for line in lines if not line.startswith('#') and '=' in line]
            blocks.append((comment, names))
        def block(name):
            return next(comment for comment, names in blocks if name in names)
        # Aucune valeur de clé, aucun chemin Vault
        self.assertEqual([], [line for line in text.splitlines() if not line.startswith('#') and '=' in line and line.split('=', 1)[1]])
        self.assertNotIn('secret/', text)
        self.assertRegex(block('TYPESAFE_API_KEY'), r'vault_env injecte JEV_API_KEY.*le runtime ne lit pas ce nom')
        grok = next(names for _, names in blocks if 'GROK_API_KEY' in names)
        self.assertEqual(['GROK_API_KEY', 'MINIMAX_API_KEY', 'MISTRAL_API_KEY'], grok)
        self.assertRegex(block('GROK_API_KEY'), r'[Ii]njectés par benchmark\.vault_env.*non lus par le runtime')
        self.assertEqual(['MIMO_API_KEY'], next(names for _, names in blocks if 'MIMO_API_KEY' in names))
        self.assertRegex(block('MIMO_API_KEY'), r'[Nn]i injecté par benchmark\.vault_env ni lu par le runtime')
        # Variables d'exploitation citées en commentaire : une valeur vide copiée dans .env bloquerait l'exécuteur
        for name in ('BENCHMARK_PRIVACY_JOURNAL', 'BENCHMARK_EXECUTOR_WORKERS', 'VAULT_BIN', 'BENCHMARK_ENV_FILE'):
            with self.subTest(name=name):
                self.assertRegex(text, r'(?m)^# ' + name + '=$')
                self.assertNotRegex(text, r'(?m)^' + name + '=')
        with patch.dict(os.environ, {'BENCHMARK_EXECUTOR_WORKERS': ''}), self.assertRaises(ValueError):
            service.executor_workers()
        for name, default in (('BENCHMARK_PRIVACY_JOURNAL', 'privacy-revocations/revocations.jsonl'),
                              ('BENCHMARK_EXECUTOR_WORKERS', '64'), ('VAULT_BIN', 'vault'), ('BENCHMARK_ENV_FILE', './.env')):
            with self.subTest(default=name):
                comment = re.search(r'(?m)^# (?!' + name + '=)(.*)\n# ' + name + '=$', text)
                self.assertIsNotNone(comment)
                self.assertIn(default, comment.group(1))


    def test_release_notes_use_real_newlines(self):
        import re
        import subprocess
        workflow = Path('.github/workflows/release.yml').read_text()
        # Bash laisse « \n » en deux caractères entre guillemets doubles : gh publierait ces caractères
        self.assertEqual([], [notes for notes in re.findall(r'--notes "([^"]*)"', workflow) if '\\n' in notes])
        lines = [line.strip() for line in workflow.splitlines() if line.strip().startswith('notes="$(printf ')]
        self.assertEqual(1, len(lines))
        self.assertIn('--notes "$notes"', workflow)
        body = subprocess.run(['bash', '-c', lines[0] + '\nprintf %s "$notes"'], capture_output=True, text=True, check=True,
                              env={'VERSION': '0.0.0-test', 'SOURCE_SHA': 'a' * 40}).stdout
        self.assertEqual(['Evaly 0.0.0-test', '', 'Commit source: ' + 'a' * 40,
                          'La provenance est vérifiée par le constructeur et le contrôleur.'], body.splitlines())
        self.assertNotIn('\\n', body)


if __name__ == '__main__':
    unittest.main()
