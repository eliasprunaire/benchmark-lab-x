from pathlib import Path
import unittest

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
        self.assertIn('--pre-release "$PRE_RELEASE"', workflow)
        self.assertIn('SHA256SUMS', workflow)


if __name__ == '__main__':
    unittest.main()
