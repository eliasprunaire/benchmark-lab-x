from pathlib import Path
import unittest
from unittest.mock import patch

from tools import release


class ReleaseTests(unittest.TestCase):
    def test_conventional_commit_precedence_and_zero_major_policy(self):
        self.assertEqual('patch', release.classify('fix(web): corriger la vue'))
        self.assertEqual('minor', release.classify('feat: ajouter la comparaison'))
        self.assertIsNone(release.classify('feat(ci): modifier le pipeline'))
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


if __name__ == '__main__':
    unittest.main()
