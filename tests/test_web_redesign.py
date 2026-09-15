"""Habillage du parcours privé : gabarit commun, bloc d'état, badges, polices locales, aucune empreinte affichée"""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark import preparation as prep, restitution as r, storage
from benchmark_web import projection, views
from tests.test_s2_review_regressions import response_for
from tests.test_s6_regressions import build

AVAILABILITY = {'assistant_configured': True, 'admission_open': True, 'can_submit': True, 'reason': 'open'}


class TemplateTests(unittest.TestCase):
    def test_shell_menu_footer_and_version(self):
        with patch.object(views, 'SOURCE_SHA', 'abcdef0123456789'):
            home = views.render({'kind': 'home'}, '').decode()
        self.assertIn('aria-current="page">Accueil</a>', home)
        self.assertIn('href="/preparation"', home)
        self.assertIn('href="/index.html"', home)
        self.assertIn('Le verdict ne fait pas de moyenne', home)
        self.assertIn('<footer class="site">', home)
        self.assertIn('v0.1.0+abcdef0', home)
        self.assertNotIn('sha256', home.lower())
        listing = views.render({'dossiers': [{'dossier_id': 'd1', 'need': 'Trier des notes', 'revision': 2}], 'availability': AVAILABILITY}, 'csrf').decode()
        self.assertIn('aria-current="page">Mes cas d’usage</a>', listing)
        self.assertIn('<ul class="dossiers">', listing)
        self.assertIn('href="/preparation/dossiers/d1"', listing)
        waiting = views.render({'operation_id': 'op', 'dossier_id': 'd1', 'availability': AVAILABILITY}, 'csrf').decode()
        self.assertIn('class="state wait"', waiting)
        honeypot = views.render({'kind': 'honeypot_ack'}, 'csrf').decode()
        for expected in ('<title>Demande enregistrée', 'class="state wait"',
                         'L’envoi a été enregistré', 'Consulter le cas d’usage et son avancement'):
            self.assertIn(expected, waiting)
            self.assertIn(expected, honeypot)

    def test_named_error_is_attached_to_its_field(self):
        page = views.render(
            {'error': 'Ce texte est trop court.', 'error_field': 'request',
             'form': {'dossier_id': 'd', 'action_id': 'a', 'request': 'court',
                      'useful': '', 'context': ''}}, 'csrf', error=True).decode()
        self.assertIn('name="request" required minlength="40" maxlength="1500" rows="5" '
                      'aria-describedby="request-error"', page)
        self.assertIn('<p id="request-error" role="alert">Ce texte est trop court.</p>', page)
        self.assertEqual(1, page.count('Ce texte est trop court.'))

        message = views.render(
            {'error': 'Ce texte est trop long.', 'error_field': 'message',
             'form': {'action_id': 'a', 'revision': 1, 'kind': 'clarify',
                      'message': 'long'}}, 'csrf', error=True).decode()
        self.assertIn('aria-describedby="message-error"', message)
        self.assertIn('<p id="message-error" role="alert">Ce texte est trop long.</p>', message)

    def test_fonts_and_projection_stylesheet_are_local_files(self):
        for name in ('Syne', 'AtkinsonHyperlegibleNext', 'AtkinsonHyperlegibleMono'):
            self.assertTrue((views.FONTS_PATH / (name + '.woff2')).is_file(), name)
        self.assertEqual(Path('projection.css'), Path(projection.STYLESHEET_PATH.name))
        self.assertNotIn(b'@font-face', projection.STYLESHEET_PATH.read_bytes())
        self.assertIn('/preparation/fonts/Syne.woff2', views.STYLESHEET_PATH.read_text())


class DossierPageTests(unittest.TestCase):
    def test_criteria_groups_render_new_and_legacy_packages(self):
        def render(criteria):
            with tempfile.TemporaryDirectory() as temporary:
                data = Path(temporary).resolve() / 'private'
                storage.initialize(data)
                storage.initialize_preparation(data)
                with closing(storage.Store(data)) as store:
                    store.create_budget('criteria', '10', 'TEST')
                    prep.admit(store, dict(authority_id='TEST_ONLY_CRITERIA', budget_id='criteria',
                        reserve_amount='7', requested_configuration={'model': 'fictional'}))
                    session, csrf, _ = prep.session(store, None, create=True)
                    operation, _ = prep.submit(store, session, 'criteria',
                        dict(action_id='create', request='Examiner les critères de cet exemple'), 'test', True)

                    def response(operation, request):
                        result = response_for(operation)
                        result['receipt']['result']['package']['candidate']['criteria'] = criteria
                        return result

                    prep.execute(data, operation, response)
                    return views.render(prep.view(store, session, 'criteria'), csrf).decode()

        rule = ('satisfait = aucune faute éliminatoire et toutes les obligations prouvées ; '
                'la qualité départage, sans note')
        page = render({'eliminatory': ['Erreur bloquante'], 'obligations': ['Action présente'],
                       'quality': [{'label': 'Clarté', 'scale': ['excellent', 'acceptable', 'faible'],
                                    'favorable': 'excellent'}]})
        for expected in ('class="grp elim"', '<h3>Éliminatoires</h3>', 'Erreur bloquante',
                         'class="grp oblig"', '<h3>Obligations</h3>', 'Action présente',
                         'class="grp sec"', '<h3>Qualité</h3>', 'Clarté', rule):
            self.assertIn(expected, page)
        for raw_key in ('<li>eliminatory</li>', '<li>obligations</li>', '<li>quality</li>'):
            self.assertNotIn(raw_key, page)
        self.assertNotIn('<li>faible</li>', page)

        legacy = render(['Toutes les actions présentes'])
        self.assertIn('<h3>Obligations</h3>', legacy)
        self.assertIn('<li>Toutes les actions présentes</li>', legacy)
        self.assertNotIn('<h3>Éliminatoires</h3>', legacy)
        self.assertNotIn('<h3>Qualité</h3>', legacy)
        self.assertIn(rule, legacy)

    def test_state_block_steps_and_hidden_correction_without_digests(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            storage.initialize(data)
            storage.initialize_preparation(data)
            with closing(storage.Store(data)) as store:
                store.create_budget('inline', '100', 'TEST')
                prep.admit(store, dict(authority_id='FICTIONAL_INLINE', budget_id='inline',
                    reserve_amount='7', requested_configuration={'model': 'fictional'}))
                session, csrf, token = prep.session(store, None, create=True)
                operation, _ = prep.submit(store, session, 'inline',
                    dict(action_id='create', request='Examiner des notes inventées'), 'a' * 40, True)
                prep.execute(data, operation, lambda op, request: response_for(op))
                code, view, _, _ = prep.dispatch(store, 'GET', '/preparation/dossiers/inline', token, None, 'a' * 40, None)
                self.assertEqual(200, code)
                page = views.render(view, csrf).decode()
        self.assertIn('class="state action"', page)
        self.assertIn('Où j’en suis', page)
        self.assertIn('aria-current="step"', page)
        self.assertIn('<span class="n">3</span>Validation', page)
        self.assertIn('<details class="corr"><summary class="button sec">', page)
        self.assertIn('Oui, c’est le travail à tester', page)
        self.assertIn('Actualiser cet état', page)
        self.assertIn('<div class="website"><label for="website">Site web</label>', page)
        self.assertNotIn(view['package_sha256'], page.replace('name="package_sha256" value="' + view['package_sha256'] + '"', ''))


class ComparisonPageTests(unittest.TestCase):
    def test_badges_and_cost_bars(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            with patch('socket.socket.connect', side_effect=AssertionError('No network in fixtures')):
                store, sid, _, _, _ = build(data)
            with closing(store):
                page = views.render(r.comparison(store, sid, 'fixture', 'comparison'), '').decode()
        self.assertIn('<span class="badge b-ok">', page)
        self.assertIn('<span class="badge b-ko">', page)
        self.assertIn('class="costbar"', page)
        self.assertIn('Le verdict ne fait pas de moyenne', page)
        self.assertNotIn('SHA-256', page)


if __name__ == '__main__':
    unittest.main()
