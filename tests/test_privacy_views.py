"""Contrats HTML du volet données, sans accès fournisseur"""
import unittest
from benchmark_web import views
from tests.test_s6_regressions import Markup


def example_view():
    return {'dossier_id': 'd1', 'revision': 1, 'current_revision': 1, 'stage': 'preview',
            'validation': None, 'qualified': False, 'explanation': 'Exemple fictif prêt à examiner',
            'payload': {'request': 'Comparer des comptes rendus fictifs', 'clarifications': [],
                        'validated_assumptions': [], 'reformulation': '', 'fictional_parameters': {}},
            'package': {'instruction': 'Rédiger le compte rendu', 'deliverables': ['Compte rendu'],
                        'pieces': [{'id': 'p1'}], 'human_work': 'Relire', 'acceptable_ambiguities': [], 'limits': []},
            'example_contents': {'p1': 'Une réunion entièrement inventée'}, 'changes': [],
            'criteria': {'eliminatory': [], 'obligations': ['Conserver les décisions'], 'quality': []},
            'criteria_rule': 'Chaque obligation compte', 'package_sha256': 'a' * 64,
            'privacy': {'csrf_token': 'privacy-token', 'session_expires_at': '2099-01-01T00:00:00Z',
                        'dossier_id': 'd1', 'content_version': 1,
                        'contribution': {'enabled': False, 'revision': 0, 'example_revision': 1}}}


class PrivacyViewsTests(unittest.TestCase):
    def test_consent_follows_example_and_existing_inline_script_is_unchanged(self):
        value = example_view()
        page = views.render(value, 'csrf').decode()
        self.assertLess(page.index('Les pièces de l’exemple'), page.index('data-privacy-post="contribution"'))
        self.assertLess(page.index('data-privacy-post="contribution"'), page.index('id="validation"'))
        self.assertEqual(views.page_script(value), views.STEP_SCRIPT)
        self.assertIn('<script>' + views.STEP_SCRIPT + '</script>', page)
        self.assertEqual(page.count('src="/preparation/privacy.js"'), 1)

    def test_public_data_and_notice_need_no_session(self):
        page = views.render({'kind': 'privacy_data'}, '').decode()
        self.assertIn('Mes données', page)
        self.assertIn('data-privacy-history', page)
        self.assertIn('src="/preparation/privacy.js"', page)
        self.assertNotIn('data-privacy-activity', page)
        notice = views.render({'kind': 'privacy_notice'}, '').decode()
        for term in ('Cybrel', 'RSSI', 'contact@cybrel.fr', '7 jours', '30 jours', '6 mois',
                     '11 jours', 'fournisseurs'):
            self.assertIn(term, notice)
        self.assertNotIn('data-privacy-activity', notice)
        self.assertNotIn('copies chiffrées', notice)
        inactive = views.render({'kind': 'privacy_notice', 'privacy_enabled': False}, '').decode()
        self.assertIn('n’est pas encore activée', inactive)
        self.assertNotIn('supprimés du serveur après 7 jours', inactive)

    def test_authenticated_controls_use_metadata_csrf_and_keep_key_form(self):
        metadata = {'csrf_token': 'privacy-token', 'session_expires_at': '2099-01-01T00:00:00Z',
                    'dossier_id': 'd1', 'content_version': 4,
                    'contribution': {'enabled': False, 'revision': 2, 'example_revision': 1}}
        value = {'dossiers': [], 'personal_preparation': True, 'privacy': metadata}
        page = views.render(value, 'key-token').decode()
        self.assertIn('data-privacy-activity', page)
        self.assertIn('data-dossier-id="d1"', page)
        self.assertIn('data-content-version="4"', page)
        self.assertIn('data-csrf-token="privacy-token"', page)
        self.assertIn('name="csrf_token" value="key-token"', page)
        self.assertIn('/preparation/access/key', page)
        self.assertIn('data-privacy-post="delete"', page)
        self.assertNotIn('data-privacy-post="contribution"', page)
        self.assertEqual(page.count('src="/preparation/privacy.js"'), 1)

    def test_contribution_is_unchecked_bound_to_example_and_disabled_when_stale(self):
        from benchmark_web.privacy_views import render_contribution
        value = {'revision': 3, 'current_revision': 3, 'package': {'instruction': 'Example'},
                 'privacy': {'csrf_token': 'purpose', 'session_expires_at': '2099-01-01T00:00:00Z',
                             'dossier_id': 'd1', 'contribution': {'enabled': False, 'revision': 5, 'example_revision': 3}}}
        rendered = render_contribution(value, 'fallback')
        tags = Markup(rendered.encode()).tags
        box = next(attrs for tag, attrs in tags if tag == 'input' and attrs.get('type') == 'checkbox')
        self.assertNotIn('checked', box)
        self.assertNotIn('disabled', box)
        self.assertIn('name="revision" value="5"', rendered)
        self.assertIn('name="example_revision" value="3"', rendered)
        value['privacy']['session_expires_at'] = '2000-01-01T00:00:00Z'
        self.assertIn('<fieldset disabled>', render_contribution(value, ''))
        value['current_revision'] = 4
        self.assertEqual(render_contribution(value, ''), '')

    def test_purpose_cookie_management_and_bootstrap_fallback(self):
        page = views.render({'kind': 'contributions', 'csrf_token': 'purpose-only', 'contributions': [
            {'id': 'c1', 'created_at': '2026-09-18', 'expires_at': '2027-03-18', 'status': 'active'}]}, '').decode()
        self.assertIn('/preparation/contributions/c1/withdraw', page)
        self.assertIn('value="purpose-only"', page)
        self.assertNotIn('data-privacy-activity', page)
        boot = views.render({'kind': 'session_bootstrap', 'return_path': '/preparation?view=1'}, '').decode()
        self.assertIn('data-privacy-bootstrap', boot)
        self.assertIn('action="/preparation/session/open"', boot)
        self.assertIn('Continuer', boot)
        self.assertIn('name="return_path" value="/preparation"', boot)
        self.assertNotIn('name="csrf_token"', boot)
        unsafe = views.render({'kind': 'session_bootstrap', 'return_path': '//evil.test'}, '').decode()
        self.assertNotIn('//evil.test', unsafe)

    def test_delete_copy_notice_and_provider_casing_match_the_plan(self):
        from benchmark_web.privacy_views import render_privacy_controls
        controls = render_privacy_controls(example_view(), 'csrf')
        self.assertIn('Supprimer ce cas d’usage', controls)
        self.assertIn('efface la copie locale', controls)
        self.assertIn('contribution', controls)
        self.assertNotIn('conserve votre copie locale', controls)
        self.assertIn('7 jours d’inactivité', controls)
        notice = views.render({'kind': 'privacy_notice'}, '').decode()
        self.assertIn('7 jours d’inactivité', notice)
        self.assertIn('11 jours après leur suppression du service actif', notice)
        self.assertIn('ne sont pas remises en service', notice)
        self.assertNotIn('effacement physique', notice)
        self.assertIn('Openrouter', notice)
        self.assertNotIn('OpenRouter', notice)

    def test_contributions_have_french_status_and_disable_withdrawn_or_expired(self):
        from benchmark_web.privacy_views import render_contributions
        for status, expiry, label, disabled in (
                ('active', '2099-03-18T00:00:00Z', 'Active', False),
                ('withdrawn', '2099-03-18T00:00:00Z', 'Retirée', True),
                ('expired', '2000-03-18T00:00:00Z', 'Expirée', True),
                ('active', '2000-03-18T00:00:00Z', 'Expirée', True),
                ('UNKNOWN_STATE', '2099-03-18T00:00:00Z', 'État indisponible', True)):
            with self.subTest(status=status, expiry=expiry):
                _, page = render_contributions({'csrf_token': 'purpose', 'contributions': [{
                    'id': 'opaque-identifier', 'created_at': '2026-09-18T12:00:00Z',
                    'expires_at': expiry, 'status': status}]})
                self.assertNotIn('Contribution opaque-identifier', page)
                self.assertIn('18 septembre 2026', page)
                self.assertIn(label, page)
                self.assertNotIn('état : ' + status + '.', page)
                self.assertIn('/preparation/contributions/opaque-identifier/withdraw', page)
                fields = [attrs for tag, attrs in Markup(page.encode()).tags if tag == 'fieldset']
                self.assertEqual('disabled' in fields[0], disabled)


if __name__ == '__main__':
    unittest.main()
