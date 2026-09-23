"""Habillage du parcours privé : gabarit commun, bloc d'état, badges, polices locales, aucune empreinte affichée"""
from contextlib import closing
import inspect
from pathlib import Path
import re
import shutil
import subprocess
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

    def test_personal_key_follows_availability_before_description_without_nesting_forms(self):
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
        self.assertLess(page.index('</aside>'), page.index('Ajouter ma clé Openrouter'))
        self.assertLess(page.index('Enregistrer la clé'), page.index('Décrivez le travail et le résultat qui vous serait utile'))
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

    def test_personal_key_is_only_on_preparation_home(self):
        for path, value in (
                ('/preparation/dossiers', {'operation_id': 'op', 'dossier_id': 'd1'}),
                ('/preparation/access', {'kind': 'access', 'status': 'disconnected'})):
            with self.subTest(path=path):
                page = views.render(dict(value, personal_preparation=True), 'csrf', path).decode()
                self.assertNotIn('Ajouter ma clé Openrouter', page)
                self.assertNotIn('id="openrouter-key"', page)

    def test_chaque_motif_de_disponibilite_a_son_libelle(self):
        motifs = set(re.findall(r"reason = '(\w+)'", inspect.getsource(prep.availability)))
        self.assertNotIn('daily_cap', motifs)
        for motif in motifs:
            page = views.render({'dossiers': [], 'availability': dict(
                AVAILABILITY, can_submit=motif == 'open', reason=motif)}, 'csrf').decode()
            aside = re.search(r'<aside id="availability".*?</aside>', page, re.S).group()
            self.assertRegex(aside, r'<p>[^<]{20,}</p>', motif)

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
        self.assertIn('Chaque exigence compte', home)
        self.assertIn('<footer class="site">', home)
        # Sans identité de release : la révision seule, jamais le numéro cible (RULES) ; un checkout modifié
        # ou un commit non poussé ne doit pas renvoyer vers un arbre qui n'est pas le code servi (AGPL §13)
        self.assertIn('<div class="bottom"><span>Révision : abcdef0</span>'
                      '<a href="https://github.com/eliasprunaire/benchmark-lab-x">Code source</a></div>', home)
        self.assertNotIn('/tree/', home)
        self.assertNotIn('v0.1.0', home)
        with patch.object(views, 'SOURCE_SHA', 'abcdef0123456789'), patch.object(views, 'RELEASE_VERSION', '0.2.0'):
            released = views.render({'kind': 'home'}, '').decode()
        self.assertIn('<span>Version : v0.2.0 (abcdef0)</span>'
                      '<a href="https://github.com/eliasprunaire/benchmark-lab-x/tree/abcdef0123456789">Code source</a>', released)
        with patch.object(views, 'SOURCE_SHA', ''), patch.object(views, 'RELEASE_VERSION', '0.2.0'):
            unknown = views.render({'kind': 'home'}, '').decode()
        self.assertIn('<div class="bottom"><a href="https://github.com/eliasprunaire/benchmark-lab-x">Code source</a></div>', unknown)
        self.assertNotIn('v0.2.0', unknown)
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

    def test_qualified_example_stays_on_validation_until_models_are_opened(self):
        nav = views.preparation_steps(dict(dossier_id='d1', revision=3, current_revision=3,
            package={'instruction':'Exemple'}, validation={'revision':3}, qualified=True))
        self.assertIn('aria-current="step"><span class="n">3</span>Validation', nav)
        self.assertNotIn('aria-current="step"><span class="n">4</span>Modèles', nav)
        self.assertIn('/preparation/dossiers/d1/configurations', nav)

    def test_five_steps_preserve_revision_and_disable_future_steps(self):
        value = dict(dossier_id='d1', revision=2, current_revision=3, stage='preview',
                     package=None, validation=None, qualified=False, explanation='Exemple attendu',
                     payload=dict(request='Besoin fictif', clarifications=[], validated_assumptions=[],
                                  reformulation='', fictional_parameters={}))
        page = views.render(value, 'csrf').decode()
        nav = page.split('<nav class="steps"', 1)[1].split('</nav>', 1)[0]
        for number, label in enumerate(('Besoin', 'Exemple', 'Validation', 'Modèles', 'Résultats'), 1):
            self.assertIn(f'<span class="n">{number}</span>{label}', nav)
        self.assertEqual(4, nav.count('aria-disabled="true"'))
        self.assertIn('/preparation/dossiers/d1/revisions/2#besoin', nav)

    def test_scope_confirmation_explains_why_benchmark_is_unavailable(self):
        page = views.render({
            'dossier_id': 'd1', 'revision': 2, 'stage': 'scope_confirmation',
            'package': None, 'validation': None, 'qualified': False,
            'explanation': 'Je ne peux pas parcourir votre ordinateur. Acceptez-vous un exemple avec des pièces textuelles inventées ?',
            'payload': {'request': 'Je voudrais organiser mes factures pour mon comptable.',
                        'clarifications': [], 'validated_assumptions': [],
                        'reformulation': '', 'fictional_parameters': {}},
            'availability': AVAILABILITY, 'personal_preparation': True},
            'csrf', '/preparation/dossiers/d1').decode()
        self.assertIn('Le périmètre est à confirmer', page)
        self.assertIn('Aucun benchmark ne peut être lancé à cette étape.', page)
        self.assertNotIn('href="#exemple"', page)
        self.assertNotIn('href="#validation"', page)
        self.assertNotIn('action="/preparation/dossiers/d1/validation"', page)
        self.assertNotIn('Choisir les modèles', page)
        self.assertNotIn('Ajouter ma clé Openrouter', page)

    def test_out_of_scope_has_fixed_referrals_and_no_continuation(self):
        links = {'math': 'https://matharena.ai/',
                 'coding': 'https://livecodebench.github.io/', 'other': None}
        for category, link in links.items():
            with self.subTest(category=category):
                page = views.render({
                    'dossier_id': 'd1', 'revision': 2, 'stage': 'suspended',
                    'checks': {'out_of_scope': category}, 'package': None,
                    'validation': None, 'qualified': False,
                    'explanation': 'Il s’agit d’un test générique, sans tâche de travail à comparer.',
                    'payload': {'request': 'Un exercice générique', 'clarifications': [],
                                'validated_assumptions': [], 'reformulation': '',
                                'fictional_parameters': {}}, 'availability': AVAILABILITY},
                    'csrf', '/preparation/dossiers/d1').decode()
                self.assertIn('Cette demande est hors du périmètre de Bench-X', page)
                self.assertNotIn('n’est pas encore', page)
                self.assertNotIn('Envoyer ma réponse', page)
                self.assertNotIn('action="/preparation/dossiers/d1/validation"', page)
                self.assertNotIn('Choisir les modèles', page)
                self.assertNotIn('intervention du responsable', page)
                self.assertNotIn('id="availability"', page)
                self.assertIn('Décrire un autre cas d’usage', page)
                if link:
                    self.assertIn('href="' + link + '"', page)
                if category == 'math':
                    self.assertNotIn('https://livecodebench.github.io/', page)

    def test_criteria_groups_render_versioned_packages(self):
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

        single_group = render(['Toutes les actions présentes'])
        self.assertIn('<h3>Obligations</h3>', single_group)
        self.assertIn('<li>Toutes les actions présentes</li>', single_group)
        self.assertNotIn('<h3>Éliminatoires</h3>', single_group)
        self.assertNotIn('<h3>Qualité</h3>', single_group)
        self.assertIn(rule, single_group)
        self.assertIn('<h1>Est-ce le travail que vous voulez tester ?</h1>', single_group)

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
    @unittest.skipUnless(shutil.which('node'), 'Node requis pour exécuter le suivi navigateur')
    def test_progress_script_reads_only_and_stops_on_completion_error_or_input(self):
        program = r'''
const vm = require('node:vm'), assert = require('node:assert/strict');
const script = require('node:fs').readFileSync(0, 'utf8');
(async () => {
  for (const outcome of ['waiting', 'finished', 'error', 'input', 'pause', 'campaign-waiting', 'campaign-finished', 'campaign-stopped']) {
    const timers = new Map(), events = {}, requests = [], navigations = [], logs = [];
    const updates = [], campaignStatus = {replaceChildren: (...nodes) => updates.push(nodes)};
    const campaign = outcome.startsWith('campaign-');
    const waiting = outcome === 'waiting' || outcome === 'campaign-waiting';
    const status = {}, progress = {}, pause = {addEventListener: (_, fn) => events.pause = fn};
    const link = {href: 'https://fixture.invalid/preparation/dossiers/d1'};
    const nodes = {'a': link, '[role="status"]': status, 'button': pause, 'progress': progress};
    const panel = {querySelector: name => nodes[name]};
    let serial = 0;
    vm.runInNewContext(script, {
      console: {info: (...args) => logs.push(args), error: (...args) => logs.push(args)},
      document: {hidden: false, getElementById: id => id === 'preparation-progress' ? panel : id === 'campaign-status' && campaign ? campaignStatus : null,
                 addEventListener: (event, fn) => events[event] = fn},
      window: {addEventListener: (event, fn) => events[event] = fn},
      location: {replace: url => navigations.push(url)}, AbortController,
      setTimeout: fn => {timers.set(++serial, fn); return serial;},
      clearTimeout: id => timers.delete(id),
      DOMParser: class {parseFromString() {return {getElementById: id => {
        if (id === 'preparation-progress') return waiting ? panel : null;
        if (id === 'campaign-status' && campaign) return {childNodes: ['Deux réponses reçues']};
        if (id === 'campaign-followup' && campaign) return {dataset: outcome === 'campaign-finished' ? {resultsHref: '/results'} : {}};
        return null;
      }};}},
      fetch: async (url, options) => {requests.push({url, options});
        return {status: outcome === 'error' ? 503 : 200, ok: outcome !== 'error', text: async () => '<html></html>'};}
    });
    if (outcome === 'input' || outcome === 'pause') events[outcome]();
    else {const [id, task] = timers.entries().next().value; timers.delete(id); await task();}
    assert.equal(requests.length, ['input', 'pause'].includes(outcome) ? 0 : 1);
    for (const {url, options} of requests) {
      assert.equal(url, link.href); assert.equal(options.method, undefined);
      assert.equal(options.body, undefined); assert.equal(options.redirect, 'error');
    }
    assert.deepEqual(navigations, outcome === 'campaign-finished' ? ['/results'] : ['finished', 'campaign-stopped'].includes(outcome) ? [link.href] : []);
    assert.equal(updates.length, outcome === 'campaign-waiting' ? 1 : 0);
    for (const entry of logs) {
      assert.match(entry[0], /^FOLLOWUP_(ACTIVE|COMPLETE|HTTP_ERROR|UNAVAILABLE)$/);
      assert(entry.length === 1 || entry.length === 2 && [200, 503].includes(entry[1]));
    }
    assert.equal(timers.size, waiting ? 1 : 0);
    if (['error', 'input', 'pause'].includes(outcome)) {
      assert.equal(progress.hidden, true); assert.equal(pause.hidden, true);
      assert.match(status.textContent, /interrompu|suspendu/);
    }
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
        subprocess.run(['node', '-e', program], input=views.PREPARATION_PROGRESS_SCRIPT,
                       text=True, check=True, capture_output=True)

    def test_progress_only_for_current_pending_work(self):
        value = dict(dossier_id='d1', revision=1, current_revision=1, stage='waiting',
                     package=None, validation=None, qualified=False, explanation='Ancien texte',
                     payload=dict(request='Trier les factures fictives', clarifications=[],
                                  validated_assumptions=[], reformulation='', fictional_parameters={}),
                     availability={**AVAILABILITY, 'reason': 'waiting', 'can_submit': False})
        for stage, qualification, prior_revision, pending in (
                ('waiting', {}, False, True), ('waiting', {}, True, False),
                ('clarification', {}, False, False), ('suspended', {}, False, False),
                ('preview', {'operation_id': 'op', 'status': 'PENDING'}, False, True),
                ('preview', {'operation_id': 'op', 'status': 'BLOCKED'}, False, False),
                ('preview', {'operation_id': 'op', 'status': 'QUALIFIED'}, False, False)):
            with self.subTest(stage=stage, qualification=qualification, prior_revision=prior_revision):
                view = {**value, 'stage': stage, 'current_revision': 2 if prior_revision else 1,
                        'qualification': {**qualification, 'summary': 'Contrôle', 'findings': []},
                        'validation': {'validated_at': '2026-09-17'} if qualification else None,
                        'qualified': qualification.get('status') == 'QUALIFIED'}
                page = views.render(view, 'csrf', '/preparation/dossiers/d1').decode()
                self.assertEqual(pending, '<progress ' in page)
                self.assertEqual(pending, 'id="preparation-progress"' in page)
                if pending:
                    self.assertEqual(1, page.count('id="availability"'))
                    self.assertNotIn('<aside id="availability"', page)
                    self.assertIn('Actualiser cet état', page)
                    self.assertNotIn('<progress value=', page)
                    self.assertIn('Suivi automatique', page)

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
        self.assertIn('Chaque exigence compte', page)
        self.assertNotIn('SHA-256', page)

    def test_libelles_du_filtre_correspondent_aux_titres_des_cas(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            with patch('socket.socket.connect', side_effect=AssertionError('No network')):
                store, sid, _, _, _ = build(data)
            with closing(store):
                page = views.render(r.comparison(store, sid, 'fixture', 'comparison'), '').decode()
        options = dict(re.findall(r'<option value="([^"]+)"(?: selected)?>(Cas [0-9]+)</option>', page))
        titles = set(re.findall(r'<h3>(Cas [0-9]+)</h3>', page))
        self.assertEqual(1, page.count('<h2>Comparaison des modèles</h2>'))
        self.assertLess(page.index('<h2>Comparaison des modèles</h2>'), page.index('id="filters"'))
        self.assertEqual({'notes': 'Cas 1', 'distinct': 'Cas 2'}, options)
        self.assertEqual(set(options.values()), titles)


if __name__ == '__main__':
    unittest.main()
