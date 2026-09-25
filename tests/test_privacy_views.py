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

    def test_public_data_needs_no_session(self):
        page = views.render({'kind': 'privacy_data'}, '').decode()
        self.assertIn('Mes données', page)
        self.assertIn('data-privacy-history', page)
        self.assertIn('src="/preparation/privacy.js"', page)
        self.assertNotIn('data-privacy-activity', page)

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
        self.assertEqual(1, len([attrs for tag, attrs in tags if attrs.get('type') == 'checkbox']))
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

    def test_delete_copy_and_retention_match_the_plan(self):
        from benchmark_web.privacy_views import render_privacy_controls
        controls = render_privacy_controls(example_view(), 'csrf')
        self.assertIn('Supprimer ce cas d’usage', controls)
        self.assertIn('efface la copie locale', controls)
        self.assertIn('contribution', controls)
        self.assertNotIn('conserve votre copie locale', controls)
        self.assertIn('Accès aux cas fermé après 7 jours d’inactivité', controls)
        self.assertIn('Accès à la clé fermé après 30 jours d’inactivité', controls)
        self.assertNotIn('Clé retirée après', controls)
        self.assertNotIn('11 jours', controls)

    def test_single_box_states_both_effects_and_the_javascript_limit(self):
        from benchmark_web.privacy_views import render_contribution
        rendered = render_contribution(example_view(), 'csrf')
        for term in ('conserver cet exemple pendant 6 mois', 'historique local', 'besoin, mes messages et les révisions',
                     'que la contribution exclut', 'nécessite JavaScript', 'sans JavaScript, seule la contribution',
                     'Mes données'):
            self.assertIn(term, rendered)

    def test_withdrawal_states_that_local_history_is_kept(self):
        from benchmark_web.privacy_views import render_contributions
        _, page = render_contributions({'csrf_token': 'purpose', 'contributions': []})
        for term in ('arrête la contribution concernée seulement', 'historique local reste actif',
                     'ne sont pas effacées', 'Mes données'):
            self.assertIn(term, page)

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


LEGAL_PAGES = {'/mentions-legales': 'Mentions légales', '/cgu': 'Conditions générales d’utilisation',
               '/confidentialite': 'Politique de confidentialité'}
LEGAL_FOOTER = ('<a href="/mentions-legales">Mentions légales</a>', '<a href="/cgu">Conditions d’utilisation</a>',
                '<a href="/confidentialite">Confidentialité</a>')


class LegalViewsTests(unittest.TestCase):
    """BX-08 : trois pages publiques, gabarit commun, marqueurs laissés à Ayo"""

    def test_each_legal_page_uses_the_site_template_without_script_or_session(self):
        for path, title in LEGAL_PAGES.items():
            with self.subTest(path=path):
                page = views.render({'kind': 'legal', 'path': path}, '').decode()
                self.assertIn('<html lang="fr">', page)
                self.assertIn('<title>' + title + ' — Bench-X</title>', page)
                self.assertEqual(1, page.count('<h1>'))
                self.assertIn('<h1>' + title + '</h1>', page)
                self.assertNotIn('<script', page)
                self.assertNotIn('data-privacy', page)
                self.assertNotIn('aria-current', page)

    def test_every_rendered_page_links_the_three_legal_pages_from_its_footer(self):
        values = [({'kind': 'home'}, False), ({'kind': 'privacy_data'}, False), ({'dossiers': []}, False),
                  (example_view(), False),
                  ({'error': 'Échec', 'title': 'Page introuvable'}, True)]
        values += [({'kind': 'legal', 'path': path}, False) for path in LEGAL_PAGES]
        for value, error in values:
            with self.subTest(value=value.get('kind') or value.get('title') or 'dossier'):
                page = views.render(value, 'csrf', error=error).decode()
                footer = page[page.index('<footer'):]
                for link in LEGAL_FOOTER:
                    self.assertIn(link, footer)
                self.assertNotIn('/preparation/privacy"', page)

    def test_markers_are_filled_with_the_decided_date_and_transfer_basis(self):
        pages = {path: views.render({'kind': 'legal', 'path': path}, '').decode() for path in LEGAL_PAGES}
        for path, page in pages.items():
            self.assertNotIn('[[', page, path)
        for path in ('/cgu', '/confidentialite'):
            self.assertIn('Dernière mise à jour : 29 septembre 2026', pages[path])
        for term in ('OpenRouter, Inc., établi aux États-Unis', 'article 45 du RGPD', 'article 46 du RGPD',
                     'Nous ne pouvons pas garantir'):
            self.assertIn(term, pages['/confidentialite'])

    def test_rights_and_complaint_are_stated(self):
        privacy = views.render({'kind': 'legal', 'path': '/confidentialite'}, '').decode()
        for term in ('art. 6.1.b', 'art. 6.1.f', 'portabilité', 'CNIL', '3 place de Fontenoy',
                     '<code>benchmark_session</code>', 'OpenRouter'):
            self.assertIn(term, privacy)
        legal = views.render({'kind': 'legal', 'path': '/mentions-legales'}, '').decode()
        self.assertIn('AGPL-3.0-only', legal)
        self.assertIn('href="https://github.com/eliasprunaire/benchmark-lab-x"', legal)

    def test_local_history_is_described_as_delivered_by_bx_10(self):
        privacy = views.render({'kind': 'legal', 'path': '/confidentialite'}, '').decode()
        self.assertNotIn('N’ÉCRIRE QU’APRÈS', privacy)
        self.assertNotIn("N'ÉCRIRE QU'APRÈS", privacy)
        row = privacy[privacy.index('Historique local dans votre navigateur'):]
        self.assertIn('Votre consentement (art. 6.1.a)', row[:row.index('</tr>')])
        for term in ('désactivé par défaut', 'deux effets', 'contribution pour cet exemple',
                     'votre besoin, vos messages et les révisions', 'que la contribution exclut',
                     'retire la copie de contribution conservée sur le serveur', 'ne concerne que la contribution',
                     'n’arrête pas l’historique local', 'restent dans ce navigateur',
                     '<a href="/preparation/data">Mes données</a>'):
            self.assertIn(term, privacy)

    def test_editorial_notes_are_not_published(self):
        pages = ''.join(views.render({'kind': 'legal', 'path': path}, '').decode() for path in LEGAL_PAGES)
        for note in ('aucune route ne les expose', 'Formulation à retenir', 'N’annoncez pas',
                     'annoncez une fermeture d’accès', '<blockquote'):
            self.assertNotIn(note, pages)

    def test_future_publications_link_the_three_legal_pages(self):
        from benchmark_web import projection
        page = projection.public_page({
            'need': 'Besoin fictif', 'task': {'dossier_id': 'd', 'version': 1},
            'campaign_id': 'c', 'result_expected': 'Résultat fictif',
            'conclusion': {'text': 'Conclusion fictive', 'limits': []},
            'coverage': {}, 'population': {}, 'conditions': {}, 'cost_basis': {},
            'economic_status': 'COMPLETE', 'rows': [], 'columns': [],
        }, {}).decode()
        footer = page[page.index('</main>'):]
        for link in LEGAL_FOOTER:
            self.assertIn(link, footer)
