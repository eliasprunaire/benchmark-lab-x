"""Habillage du parcours privé : gabarit commun, bloc d'état, badges, polices locales, aucune empreinte affichée"""
from contextlib import closing
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from benchmark import preparation as prep, restitution as r, storage, web_api
from benchmark_web import projection, views
from tests.test_s2_review_regressions import Authorized, response_for
from tests.test_s6_regressions import build
from tests.test_web_access import setUpModule  # noqa: F401  contrôle global des pages rendues

# Polices chargées par la feuille, servies en local avec leur licence OFL
FONTS = ('MonaSans', 'GeistMono', 'Silkscreen')
# Polices encore servies et nommées par les mentions légales, sans être chargées par la feuille
FONTS_NOMMEES = ('HubotSans', 'HostGrotesk')


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))


    def test_contrastes_des_deux_themes(self):
        css = views.STYLESHEET_PATH.read_text()
        blocks = re.findall(r':root\s*\{([^}]+)\}', css)
        themes = [dict(re.findall(r'--([\w-]+):\s*(#[0-9a-fA-F]{6})', block))
                  for block in blocks]
        self.assertEqual(2, len(themes))

        def luminance(color):
            channels = [int(color[n:n + 2], 16) / 255 for n in (1, 3, 5)]
            return sum(weight * (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4)
                       for weight, v in zip((.2126, .7152, .0722), channels))

        # Paires de reports/refonte-c/contrastes.md : texte sur fond, 4,5:1 au moins dans chaque thème
        text_pairs = [('encre', 'papier'), ('encre', 'carte'), ('encre-2', 'papier'), ('discret', 'papier'),
                      ('discret', 'carte'), ('discret', 'creux'), ('ok', 'carte'), ('ko', 'carte'),
                      ('sur-signal', 'signal'), ('sur-ruban', 'ruban'), ('nuit-2', 'nuit'), ('sur-nuit', 'nuit'),
                      ('signal', 'nuit'), ('focus', 'papier')]
        text_pairs += [(tone, tone + '-fond') for tone in ('ok', 'ko', 'att', 'vig', 'inc')]
        for name, colors in (('clair', themes[0]), ('sombre', themes[0] | themes[1])):
            for fg, bg in text_pairs:
                with self.subTest(theme=name, texte=fg, fond=bg):
                    low, high = sorted((luminance(colors[fg]), luminance(colors[bg])))
                    self.assertGreaterEqual((high + .05) / (low + .05), 4.5)


    def test_fonts_and_projection_stylesheet_are_local_files(self):
        for name in FONTS + FONTS_NOMMEES:
            self.assertTrue((views.FONTS_PATH / (name + '.woff2')).is_file(), name)
            self.assertTrue((views.FONTS_PATH / ('OFL-' + name + '.txt')).is_file(), name)
        for name in FONTS:
            self.assertIn('/preparation/fonts/' + name + '.woff2', views.STYLESHEET_PATH.read_text())
        # Toute police de la feuille est servie par la route des polices
        for name in re.findall(r'url\("/preparation/fonts/([A-Za-z]+)\.woff2"\)', views.STYLESHEET_PATH.read_text()):
            self.assertIn(name, FONTS)
        self.assertEqual(Path('projection.css'), Path(projection.STYLESHEET_PATH.name))
        self.assertNotIn(b'@font-face', projection.STYLESHEET_PATH.read_bytes())
        # La page publiée reprend les couleurs du parcours, en clair comme en sombre
        # Seul le premier bloc :root de chaque thème compte : une règle plus loin peut redéfinir une variable localement
        palette = lambda css: [dict(re.findall(r'--([a-z0-9-]+):\s*(#[0-9a-f]{6})', re.search(r':root\s*\{([^}]*)\}', block)[1]))
                               for block in re.split(r'@media\s*\(prefers-color-scheme:\s*dark\)', css)[:2]]
        published, private = palette(projection.STYLESHEET_PATH.read_text()), palette(views.STYLESHEET_PATH.read_text())
        for shade, (mine, theirs) in zip(('clair', 'sombre'), zip(published, private)):
            self.assertTrue(mine, shade)
            self.assertLessEqual(mine.items(), theirs.items(), shade)
        for retired in ('Syne', 'AtkinsonHyperlegibleNext', 'AtkinsonHyperlegibleMono'):
            self.assertFalse((views.FONTS_PATH / (retired + '.woff2')).exists(), retired)
        # Les mentions légales nomment exactement les polices servies
        from benchmark_web import legal_views
        for family in ('Mona Sans', 'Hubot Sans', 'Host Grotesk', 'Geist Mono', 'Silkscreen'):
            self.assertIn(family, legal_views.MENTIONS_LEGALES)
        self.assertNotIn('Atkinson', legal_views.MENTIONS_LEGALES)


class DossierPageTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))


    def test_criteria_groups_render_versioned_packages(self):
        def render(criteria):
            with tempfile.TemporaryDirectory() as temporary:
                data = Path(temporary).resolve() / 'private'
                storage.initialize(data)
                storage.initialize_preparation(data)
                with closing(storage.Store(data)) as store:
                    store.create_budget('criteria', '10', 'TEST')
                    granted = lambda transport=True: Authorized(transport, authority_id='TEST_ONLY_CRITERIA',
                        budget_id='criteria', reserve_amount='7', requested_configuration={'model': 'fictional'})
                    session, csrf, _ = prep.session(store, None, create=True)
                    operation, _ = prep.submit(store, session, 'criteria',
                        dict(action_id='create', request='Examiner les critères de cet exemple'), 'test', granted())

                    def response(operation, request):
                        result = response_for(operation)
                        result['receipt']['result']['package']['candidate']['criteria'] = criteria
                        return result

                    prep.execute(data, operation, granted(response))
                    return views.render(prep.view(store, session, 'criteria'), csrf).decode()

        rule = ('satisfait = aucune faute éliminatoire et toutes les obligations prouvées ; '
                'la qualité départage, sans note')
        page = render({'eliminatory': ['Erreur bloquante'], 'obligations': ['Action présente'],
                       'quality': [{'label': 'Clarté', 'scale': ['excellent', 'acceptable', 'faible'],
                                    'favorable': 'excellent'}]})
        for expected in ('<ul class="criteres">', '<span class="genre">Erreur éliminatoire</span>', 'Erreur bloquante',
                         '<span class="genre">Obligation</span>', 'Action présente',
                         '<span class="genre">Qualité</span>', 'Clarté', rule):
            self.assertIn(expected, page)
        for raw_key in ('<li>eliminatory</li>', '<li>obligations</li>', '<li>quality</li>'):
            self.assertNotIn(raw_key, page)
        self.assertNotIn('<li>faible</li>', page)
        self.assertIn('<h1>Est-ce le travail à tester ?</h1>', page)
        self.assertIn('<title>Est-ce le travail à tester ?', page)

        single_group = render(['Toutes les actions présentes'])
        self.assertIn('<span class="genre">Obligation</span>', single_group)
        self.assertIn('Toutes les actions présentes', single_group)
        self.assertNotIn('<span class="genre">Erreur éliminatoire</span>', single_group)
        self.assertNotIn('<span class="genre">Qualité</span>', single_group)
        self.assertIn(rule, single_group)
        self.assertIn('<h1>Est-ce le travail à tester ?</h1>', single_group)


    def test_state_block_steps_and_hidden_correction_without_digests(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            storage.initialize(data)
            storage.initialize_preparation(data)
            with closing(storage.Store(data)) as store:
                store.create_budget('inline', '100', 'TEST')
                granted = lambda transport=True: Authorized(transport, authority_id='FICTIONAL_INLINE',
                    budget_id='inline', reserve_amount='7', requested_configuration={'model': 'fictional'})
                session, csrf, token = prep.session(store, None, create=True)
                operation, _ = prep.submit(store, session, 'inline',
                    dict(action_id='create', request='Examiner des notes inventées'), 'a' * 40, granted())
                prep.execute(data, operation, granted(lambda op, request: response_for(op)))
                code, view, _, _ = web_api.dispatch(store, 'GET', '/preparation/dossiers/inline', token, None, 'a' * 40, None)
                self.assertEqual(200, code)
                page = views.render(view, csrf).decode()
        # Avant validation, le bloc jaune porte la décision ; le tiroir de correction est celui que ce bloc commande
        self.assertIn('<form class="decision" method="post"', page)
        self.assertIn('<details class="tiroir" id="corriger">', page)
        self.assertIn('<form id="form-corriger" method="post"', page)
        self.assertIn('aria-current="step"', page)
        self.assertIn('<span class="n">03</span><span class="l">Validation</span>', page)
        self.assertIn('Oui, c’est le travail à tester', page)
        self.assertIn('type="submit" form="form-corriger"', page)
        self.assertIn('>Actualiser<svg', page)
        self.assertIn('<div class="website" hidden aria-hidden="true"><label for="website">Site web</label>', page)
        self.assertNotIn(view['package_sha256'], page.replace('name="package_sha256" value="' + view['package_sha256'] + '"', ''))


class ComparisonPageTests(unittest.TestCase):


    def test_badges_without_cost_bars(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            with patch('socket.socket.connect', side_effect=AssertionError('No network in fixtures')):
                store, sid, _, _, _ = build(data)
            with closing(store):
                page = views.render(r.comparison(store, sid, 'fixture', 'comparison'), '').decode()
        self.assertIn('<span class="tampon tampon-ok">', page)
        self.assertIn('<span class="tampon tampon-ko">', page)
        # Pas de barres de coût : le montant se lit en chiffres
        self.assertNotIn('costbar', page)
        self.assertIn('<nav aria-label="Informations légales"><h2>Informations légales</h2>', page)
        # Pied commun : tagline, puis le parcours avec un seul appel à l'action
        self.assertIn('<p class="intro">Evaly, le benchmark personnalisé : ', page)
        self.assertIn('<li><a href="/preparation">Décrire mon cas d’usage</a></li><li class="pied-sep"><a href="/preparation/data">Mes données</a></li>', page)
        self.assertNotIn('SHA-256', page)

    def test_libelles_du_filtre_correspondent_aux_titres_des_cas(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            with patch('socket.socket.connect', side_effect=AssertionError('No network')):
                store, sid, _, _, _ = build(data)
            with closing(store):
                page = views.render(r.comparison(store, sid, 'fixture', 'comparison'), '').decode()
        options = dict(re.findall(r'<option value="([^"]+)"(?: selected)?>(Exemple [0-9]+)</option>', page))
        titles = set(re.findall(r'<h3>(Exemple [0-9]+)</h3>', page))
        self.assertEqual(1, page.count('<h2 id="t-comp">Comparaison des modèles</h2>'))
        self.assertLess(page.index('<h2 id="t-comp">Comparaison des modèles</h2>'), page.index('id="filters"'))
        self.assertEqual({'notes': 'Exemple 1', 'distinct': 'Exemple 2'}, options)
        self.assertEqual(set(options.values()), titles)



if __name__ == '__main__':
    unittest.main()
