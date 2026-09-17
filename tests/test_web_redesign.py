"""Habillage du parcours privé : gabarit commun, bloc d'état, badges, polices locales, aucune empreinte affichée"""
from contextlib import closing
import inspect
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from benchmark import preparation as prep, restitution as r, storage, web_api
from benchmark_web import projection, views
from tests.test_s2_review_regressions import response_for
from tests.test_s6_regressions import Markup, build

AVAILABILITY = {'assistant_configured': True, 'admission_open': True, 'can_submit': True, 'reason': 'open'}


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))

    def test_personal_key_follows_context_without_nesting_forms(self):
        from html.parser import HTMLParser
        class Forms(HTMLParser):
            depth = 0
            nested = False
            def handle_starttag(self, tag, attrs):
                if tag == 'form':
                    self.nested |= self.depth > 0
                    self.depth += 1
            def handle_endtag(self, tag):
                if tag == 'form':
                    self.depth -= 1
        page = views.render({'dossiers': [], 'availability': AVAILABILITY,
                             'personal_preparation': True}, 'csrf').decode()
        forms = Forms()
        forms.feed(page)
        self.assertFalse(forms.nested)
        self.assertLess(page.index('id="context"'), page.index('Ma clé OpenRouter'))
        self.assertLess(page.index('Ma clé OpenRouter'), page.index('Préparer cet exemple'))
        parsed = Markup(page.encode())
        button = next(attrs for tag, attrs in parsed.tags if tag == 'button' and attrs.get('form'))
        self.assertEqual('prepare-case', button['form'])
        self.assertTrue(any(tag == 'form' and attrs.get('id') == button['form'] for tag, attrs in parsed.tags))
        self.assertNotIn('Préparation et qualification : plafond local', page)

    def test_evitement_et_aide_du_formulaire_indisponible(self):
        page = views.render({'dossiers': [], 'availability': dict(
            AVAILABILITY, can_submit=False, reason='closed')}, 'csrf')
        parsed = Markup(page)
        self.assertEqual('#main', parsed.links[0])
        main = next(attrs for tag, attrs in parsed.tags if tag == 'main')
        self.assertEqual({'id': 'main', 'tabindex': '-1'}, main)
        request = next(attrs for tag, attrs in parsed.tags if attrs.get('id') == 'request')
        self.assertEqual('request-help availability', request['aria-describedby'])
        ids = [attrs['id'] for _, attrs in parsed.tags if 'id' in attrs]
        self.assertEqual(len(ids), len(set(ids)))
        for tag, attrs in parsed.tags:
            if 'aria-describedby' in attrs:
                self.assertTrue(set(attrs['aria-describedby'].split()) <= set(ids))
            self.assertLessEqual(int(attrs.get('tabindex', '0')), 0)
        css = views.STYLESHEET_PATH.read_text()
        self.assertRegex(css, r'svg\[hidden\]\s*\{\s*display:\s*none;\s*\}')
        self.assertRegex(css, r'body\s*\{[^}]*overflow-wrap:\s*break-word;')
        self.assertNotRegex(css, r'body\s*\{[^}]*overflow-wrap:\s*anywhere;')
        self.assertIn(':focus-visible { outline: 3px solid var(--focus)', css)

    def test_chaque_motif_de_disponibilite_a_son_libelle(self):
        motifs = set(re.findall(r"reason = '(\w+)'", inspect.getsource(prep.availability)))
        self.assertIn('daily_cap', motifs)
        for motif in motifs:
            page = views.render({'dossiers': [], 'availability': dict(
                AVAILABILITY, can_submit=motif == 'open', reason=motif)}, 'csrf').decode()
            aside = re.search(r'<aside id="availability".*?</aside>', page, re.S).group()
            self.assertRegex(aside, r'<p>[^<]{20,}</p>', motif)
        cap = views.render({'dossiers': [], 'availability': dict(
            AVAILABILITY, can_submit=False, reason='daily_cap')}, 'csrf').decode()
        self.assertIn('plafond quotidien de préparation', cap)

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

        text_pairs = [(fg, bg) for fg in ('ink', 'ink-2', 'muted', 'accent-ink')
                      for bg in ('paper', 'surface', 'soft')]
        text_pairs += [(tone, tone + '-soft') for tone in ('warm', 'ok', 'ko', 'warn', 'unk', 'wait')]
        text_pairs += [('on-btn', 'btn'), ('on-btn', 'btn-hover'), ('surface', 'accent')]
        for name, colors in (('clair', themes[0]), ('sombre', themes[0] | themes[1])):
            with self.subTest(theme=name, état='survol'):
                low, high = sorted((luminance(colors['btn']), luminance(colors['btn-hover'])))
                self.assertGreaterEqual((high + .05) / (low + .05), 1.4)
            pairs = [(fg, bg, 4.5) for fg, bg in text_pairs]
            pairs += [(fg, bg, 3) for fg in ('focus', 'line-2') for bg in ('paper', 'surface')]
            for fg, bg, minimum in pairs:
                with self.subTest(theme=name, texte=fg, fond=bg):
                    low, high = sorted((luminance(colors[fg]), luminance(colors[bg])))
                    self.assertGreaterEqual((high + .05) / (low + .05), minimum)

    def test_shell_menu_footer_and_version(self):
        with patch.object(views, 'SOURCE_SHA', 'abcdef0123456789'):
            home = views.render({'kind': 'home'}, '').decode()
        self.assertIn('aria-current="page">Accueil</a>', home)
        self.assertIn('href="/preparation"', home)
        self.assertIn('href="/index.html"', home)
        self.assertIn('Le verdict ne fait pas de moyenne', home)
        self.assertIn('<footer class="site">', home)
        self.assertIn('v0.1.0+abcdef0', home)
        self.assertIn('<div class="bottom"><span>Version : v0.1.0+abcdef0</span></div>', home)
        self.assertNotIn('Version du site', home)
        self.assertNotIn('Aucune ressource externe chargée.', home)
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
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))

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
        self.assertIn('<h1>Est-ce le travail que vous voulez tester ?</h1>', page)
        self.assertIn('<title>Est-ce le travail que vous voulez tester ?', page)

        legacy = render(['Toutes les actions présentes'])
        self.assertIn('<h3>Obligations</h3>', legacy)
        self.assertIn('<li>Toutes les actions présentes</li>', legacy)
        self.assertNotIn('<h3>Éliminatoires</h3>', legacy)
        self.assertNotIn('<h3>Qualité</h3>', legacy)
        self.assertIn(rule, legacy)
        self.assertIn('<h1>Est-ce le travail que vous voulez tester ?</h1>', legacy)

    def test_resume_de_qualification_bloquee_reste_du_texte(self):
        summary = '<img src=x onerror="alert(1)">Correction requise'
        page = views.render({
            'dossier_id': 'd1', 'revision': 1, 'stage': 'preview', 'package': None,
            'validation': {'validated_at': '2026-09-16T08:00:00Z'}, 'qualified': False,
            'qualification': {'operation_id': 'op', 'status': 'BLOCKED', 'summary': summary,
                              'findings': [], 'qualification_status': 'BLOCKED',
                              'approval_status': 'PENDING'},
            'explanation': 'Contrôle automatique de l’exemple',
            'payload': {'request': 'Trier des notes inventées', 'clarifications': [],
                        'validated_assumptions': [], 'reformulation': '',
                        'fictional_parameters': {}},
            'availability': AVAILABILITY}, 'csrf').decode()
        self.assertIn('Qualification à reprendre', page)
        self.assertNotIn('<img src=x', page)
        self.assertEqual(2, page.count(
            '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;Correction requise'))

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
                code, view, _, _ = web_api.dispatch(store, 'GET', '/preparation/dossiers/inline', token, None, 'a' * 40, None)
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

    def test_libelles_du_filtre_correspondent_aux_titres_des_cas(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            with patch('socket.socket.connect', side_effect=AssertionError('No network')):
                store, sid, _, _, _ = build(data)
            with closing(store):
                page = views.render(r.comparison(store, sid, 'fixture', 'comparison'), '').decode()
        options = dict(re.findall(r'<option value="([^"]+)"(?: selected)?>(Cas [0-9]+)</option>', page))
        titles = set(re.findall(r'<h2>(Cas [0-9]+)</h2>', page))
        self.assertEqual({'notes': 'Cas 1', 'distinct': 'Cas 2'}, options)
        self.assertEqual(set(options.values()), titles)


if __name__ == '__main__':
    unittest.main()
