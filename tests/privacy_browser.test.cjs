// Run: PLAYWRIGHT_MODULE=/path/to/playwright/index.js node --test tests/privacy_browser.test.cjs
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const {createServer} = require('node:http');
const {readFileSync, mkdirSync} = require('node:fs');
const {execFileSync} = require('node:child_process');
const {createHash, randomBytes} = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server, browser, origin, rendered;
let record, manifest, chunks, mode = 'normal', release, arrived;
let posts = [], postStatus = 200, followupReplies = [];
const fresh = (version = 1) => ({format: 'bench-x/history/v1', dossier_id: 'd1', content_version: version,
  need: 'Comparer <img src=x onerror=alert(1)>', messages: ['Précision'], revisions: [{number: 1,
  instruction: 'Consigne', deliverables: ['Document'], criteria: ['Exactitude'],
  pieces: [{name: '<script>pièce</script>', text: 'Pièce entière'}], qualification: 'Qualifiée'}],
  campaigns: [{id: 'c1', models: [{name: 'Modèle', verdict: 'SATISFAIT', cost: {amount: '0.02', currency: 'USD'},
  answer: 'Réponse intégrale', evidence: [{name: 'Preuve', text: 'Passage vérifié'}]}]}]});
function fixture(version = 1, need, extra = {}) {
  record = {...fresh(version), ...extra};
  if (need) record.need = need;
  const bytes = Buffer.from(JSON.stringify(record));
  chunks = [];
  for (let offset = 0; offset < bytes.length; offset += 1048576) chunks.push(bytes.subarray(offset, offset + 1048576).toString('hex'));
  manifest = {format: 'bench-x/archive/v1', dossier_id: 'd1', content_version: version, snapshot_id: 's' + version,
    items: [{item_id: 'record', length: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex')}]};
}
before(async () => {
  rendered = JSON.parse(execFileSync('uv', ['run', '--with-requirements', 'benchmark/requirements.txt', '--with', 'requests', '--with', 'mpmath==1.3.0', 'python', '-c', `
import json
from benchmark import restitution
from benchmark_web import projection, views
from benchmark_web.server import RESPONSE_TIMEOUT
from tests.test_privacy_views import example_view, browser_views, results_view
from tests.test_s10_regressions import S10ProofTests as proof
proof.setUpClass()
try:
    comparison = restitution.comparison(proof.store, proof.sid, 'fixture', 'proof')
    detail = restitution.detail(proof.store, proof.sid, 'fixture', 'proof', 'long')
    attempt = {'comparison': views.render(comparison, '').decode(), 'detail': views.render(detail, '').decode(),
               'detail_href': comparison['rows'][0]['detail_href'], 'back_href': detail['back_href']}
    # Cinq réponses dont une non conforme ; la recommandation est ajoutée à la vue seule, pour observer l'infobulle
    results = restitution.comparison(proof.store, proof.sid, 'fixture', 'comparison')
    results['recommendation'] = {'configuration': results['panel'][1], 'amount': '0.10001', 'unit': 'TEST',
                                 'basis': 'quality_then_cost', 'quality': [{'measure': 'Durée fictive'}], 'count': 3}
    # Le motif vient d'un modèle juge : il reste du texte, jamais du balisage
    failed = next(row for row in results['rows'] if row['verdict'] == 'NE SATISFAIT PAS')
    failed['reason'] += ' <img src=x onerror=alert(1)>'
    # Le tableau nomme les exigences en défaut : leur libellé vient aussi d'un modèle
    for item in failed['qualification']['contract']['specification']['obligations']:
        item['description'] += ' <img src=x onerror=alert(1)>'
    attempt['results'] = views.render(results, '').decode()
    # Priorité explicite : le formulaire existant, le compromis et l'échappement des libellés du contrat
    priority = restitution.comparison(proof.store, proof.sid, 'fixture', 'comparison',
                                      query={'priority': 'duration'})
    priority['abstention'] = None
    priority['recommendation'] = {
        'configuration': priority['panel'][1], 'amount': '0.10001', 'unit': 'TEST',
        'basis': 'priority_criterion', 'priority': 'duration',
        'quality': [{'measure': 'Durée fictive'}], 'count': 3,
        'compromises': [dict(measure='Précision <img src=x onerror=alert(1)>', value='acceptable',
                             unit='descriptif', best='excellent', best_unit='descriptif',
                             configuration=priority['panel'][0])],
        'detail_href': priority['rows'][0]['detail_href']}
    duration = next(column for column in priority['columns'] if column['id'] == 'duration')
    duration['definition']['measure'] = 'Durée <script>alert(4)</script>'
    attempt['results_priority'] = views.render(priority, '').decode()
    attempt['preview'] = views.render(restitution.preview_view(proof.store, proof.sid, 'fixture', 'comparison',
                                                               piece_ids=[], presentation=projection), '').decode()
finally:
    proof.doClassCleanups()
# Récapitulatif avant dépense et suivi d'un benchmark, rendus comme les autres pages
panel = [{'id': 'configuration-1', 'model': 'mistralai/mistral-medium-3-5', 'effort': 'high', 'effort_requested': 'low',
          'parameters': {'reasoning': {'effort': 'high'}}, 'estimate': {'amount_usd': '0.0123'}},
         {'id': 'configuration-2', 'model': 'deepseek/deepseek-v4.1-flash', 'effort': 'off', 'effort_limit': 'not_adjustable',
          'parameters': {}, 'estimate': {'amount_usd': None}}]
names = {'mistralai/mistral-medium-3-5': 'Mistral Medium 3.5', 'deepseek/deepseek-v4.1-flash': 'DeepSeek V4.1 Flash'}
def campaign(state=None):
    cells = [] if state is None else [{'configuration_id': c['id'], 'state': state} for c in panel]
    return {'campaign_id': 'c1', 'version': 1, 'panel': panel, 'budget': None, 'reserve_amounts': {},
            'cells': cells, 'attempts': [{'state': state, 'incident': None} for _ in cells], 'admission_open': True,
            'task': {'revision': 2}, 'conditions': {'frozen_at': '2026-09-15T00:00:00Z', 'pi': {'package': 'pi', 'version': '0.85.1'}}}
def launch(state=None):
    return {'kind': 'campaign_launch', 'dossier_id': 'd1', 'campaign': campaign(state), 'model_names': names,
            'criteria': {'result_expected': 'Une liste complète des actions', 'obligations': [], 'eliminatory_errors': [], 'limits': []},
            'checks': [{'key': 'example_validated', 'ok': True, 'detail': 'Exemple validé'}],
            'launchable': state is None, 'judgment_estimate_usd': '0.30', 'estimate_total_usd': None,
            'access': {'status': 'connected', 'limit_remaining_usd': '18.5', 'limit_usd': '20'}}
# Cas lancé : le consentement est replié dans « Vos données pour ce cas », comme le fait views.render
from benchmark_web.privacy_views import render_contribution, render_privacy_controls
case = example_view()
example_page = views.render(case, 'csrf').decode()
consent, controls = render_contribution(case, 'csrf'), render_privacy_controls(case, 'csrf')
assert consent in example_page and controls in example_page
launched = example_page.replace(consent, '').replace(controls, render_privacy_controls(case, 'csrf', consent))
# Les notices inventées restent dans les fixtures de test
from pathlib import Path
from unittest.mock import patch
with patch.object(views, 'BENCHMARK_REFERENCES', json.loads(Path('tests/fixtures/external-references.json').read_text())):
    reference_queries = [('', '', ''), ('corriger', 'coding', 'verified'), ('introuvable', '', ''),
                         ('<script>alert(2)</script>', '', '')]
    reference_search = {json.dumps(list(query), separators=(',', ':')):
        views.render(dict(kind='references', **dict(zip(('q', 'category', 'verification'), query))), '').decode()
        for query in reference_queries}
    reference_notices = {identity: views.render(dict(kind='references', reference_id=identity), '').decode()
                         for identity in views.BENCHMARK_REFERENCES}
    referral = example_view()
    referral.update(stage='suspended', checks=dict(out_of_scope='coding'), package=None, validation=None)
    referral['payload']['request'] = 'Besoin privé fictif à ne pas transmettre'
    reference_referral = views.render(referral, '').decode()
    reference_help = views.render(dict(kind='references_help', stage='form',
                                       submission_id='a' * 32), '').decode()
    reference_help_result = views.render(dict(
        kind='references_help', stage='suggestions',
        suggestions=[{'id': 'atelier-code', 'label': 'correspond',
                      'reference': views.BENCHMARK_REFERENCES['atelier-code']},
                     {'id': 'atelier-maths', 'label': 'partiel',
                      'reference': views.BENCHMARK_REFERENCES['atelier-maths']}],
        receipt={'usage': 'repertoire', 'questions_version': 'repertoire-v1',
                 'model_requested': 'jev-1.13.0', 'model_observed': 'jev-1.13.0',
                 'notices': ['atelier-code', 'atelier-maths'], 'input_tokens': 120,
                 'cost': {'status': 'KNOWN', 'amount': '0.000005', 'currency': 'USD',
                          'source': 'Calcul local : jetons d’entrée reçus × tarif daté ; hors facture'}}),
        '').decode()
    reference_help_unavailable = views.render(dict(
        kind='references_help', stage='unavailable', reason='DISABLED'), '').decode()
with patch.object(views, 'BENCHMARK_REFERENCES', {}):
    reference_empty = views.render(dict(kind='references'), '').decode()
approved_ids = ('matharena', 'livecodebench', 'swe-bench', 'mteb', 'artificial-analysis-api')
approved = {'approved-index': views.render(dict(kind='references'), '').decode(),
            'approved-retrieval': views.render(dict(kind='references', category='retrieval'), '').decode(),
            'approved-api': views.render(dict(kind='references', category='api', q='API'), '').decode()}
for identity in approved_ids:
    approved['approved-' + identity] = views.render(dict(kind='references', reference_id=identity), '').decode()
from hashlib import sha256
from benchmark import editorial, storage
import tempfile
def editorial_bundle(version, title, body, *, characteristics=None, schema=None):
    raw_piece = body.encode()
    filename = 'piece-consigne.txt'
    manifest = {'schema_version': schema or editorial.SCHEMA, 'task_id': 'tache-fictive', 'version_id': version,
                'title': title, 'work': 'Préparer un tableau fictif', 'cases': ['Cinq débits inventés'],
                'limits': ['Ne certifie aucune comptabilité'], 'qualification_status': 'ABSENT', 'results_published': False,
                'pieces': {'consigne': {'file': filename, 'sha256': sha256(raw_piece).hexdigest(), 'label': 'Consigne'}}}
    if characteristics is not None:
        manifest.update(characteristics)
    encoded = storage._strict_json(manifest).encode()
    identity = sha256(encoded).hexdigest()
    return {'manifest': encoded, 'package_sha256': identity, 'files': {filename: raw_piece}}, editorial.fixture_approval(identity)
editorial_title = 'Tableau <script>alert(1)</script>'
editorial_piece = 'Pièce <script>alert(1)</script>'
editorial_long_tool = 'Tableur ' + 'de contrôle ' * 16 + 'o'
assert len(editorial_long_tool) == 201
editorial_search_fields = ('q', 'entree', 'langue', 'outil', 'difficulte', 'qualification')
with tempfile.TemporaryDirectory() as editorial_dir:
    editorial_dir = str(Path(editorial_dir).resolve())
    packed, packed_approval = editorial_bundle('version-1', editorial_title, editorial_piece)
    editorial.fixture_publish(editorial_dir, packed, packed_approval)
    editorial.fixture_report(editorial_dir, 'tache-fictive', 'version-1', dict(
        actor='approbateur-fictif-epreuve', authority_id='FIXTURE_EDITORIAL_TRIAL', scope='editorial-report',
        package_sha256=packed['package_sha256'], task_id='tache-fictive', version_id='version-1',
        summary='Erreur de fixture <script>alert(3)</script>', reported_at='2026-10-23T10:00:00+00:00'))
    packed_two, packed_two_approval = editorial_bundle(
        'version-2', 'Note <script>alert(4)</script>', 'Pièce de la note',
        characteristics={'expected_result': 'Note de synthèse', 'inputs': ['Textes fournis'],
                         'language': 'Français', 'tools': ['Tableur'], 'difficulty': 'Facile'})
    editorial.fixture_publish(editorial_dir, packed_two, packed_two_approval)
    packed_long, packed_long_approval = editorial_bundle(
        'version-longue', 'Version longue', 'Pièce de la version longue',
        characteristics={'expected_result': 'Note longue', 'inputs': ['Textes fournis'],
                         'language': 'Français', 'tools': [editorial_long_tool], 'difficulty': 'Facile'})
    editorial.fixture_publish(editorial_dir, packed_long, packed_long_approval)
    editorial_queries = ({}, {'q': 'note'}, {'q': 'note', 'langue': 'Français', 'outil': 'Tableur'},
                         {'q': 'introuvable'}, {'outil': editorial_long_tool})
    editorial_search = {
        json.dumps([query.get(key, '') for key in editorial_search_fields], ensure_ascii=False, separators=(',', ':')):
            views.render(editorial.fixture_search(editorial_dir, query), '').decode()
        for query in editorial_queries}
    editorial_pages = {
        '/epreuves': views.render(editorial.fixture_catalog(editorial_dir), '').decode(),
        '/epreuves/tache-fictive/version-1': views.render(
            editorial.fixture_version(editorial_dir, 'tache-fictive', 'version-1'), '').decode(),
        '/epreuves/tache-fictive/version-2': views.render(
            editorial.fixture_version(editorial_dir, 'tache-fictive', 'version-2'), '').decode()}
    editorial_help_versions = editorial.fixture_catalog(editorial_dir)['versions']
    editorial_help_form = views.render({'kind': 'editorial_help', 'stage': 'form',
                                        'submission_id': 'a' * 32}, '').decode()
    editorial_help_value = {
        'status': 'suggestions',
        'suggestions': [{'notice_id': packed['package_sha256'], 'label': 'correspond'},
                        {'notice_id': packed_two['package_sha256'], 'label': 'partiel'}],
        'receipt': {'usage': 'epreuves', 'questions_version': 'epreuves-v1',
                    'model_requested': 'jev-1.13.0', 'model_observed': 'jev-1.13.0',
                    'notices': [packed['package_sha256'], packed_two['package_sha256']],
                    'input_tokens': 120,
                    'cost': {'status': 'KNOWN', 'amount': '0.000005', 'currency': 'USD',
                             'source': 'Calcul local : jetons d’entrée reçus × tarif daté ; hors facture'}}}
    editorial_help_result = views.render(
        views.editorial_help_view(editorial_help_value, editorial_help_versions), '').decode()
    editorial_version_search = {
        '/epreuves/tache-fictive/version-2|' + json.dumps(['note', '', '', '', '', ''], ensure_ascii=False, separators=(',', ':')):
            views.render(dict(editorial.fixture_version(editorial_dir, 'tache-fictive', 'version-2'),
                              criteria={'q': 'note'}), '').decode(),
        '/epreuves/tache-fictive/version-2|' + json.dumps(['note', '', 'Français', 'Tableur', '', ''], ensure_ascii=False, separators=(',', ':')):
            views.render(dict(editorial.fixture_version(editorial_dir, 'tache-fictive', 'version-2'),
                              criteria={'q': 'note', 'langue': 'Français', 'outil': 'Tableur'}), '').decode()}
    editorial.fixture_withdraw(editorial_dir, 'tache-fictive', 'version-1',
                               editorial.fixture_withdrawal(packed['package_sha256'], 'tache-fictive', 'version-1'))
    editorial_withdrawn = views.render(editorial.fixture_version(editorial_dir, 'tache-fictive', 'version-1'), '').decode()
    editorial_help_withdrawn = views.render(
        views.editorial_help_view(editorial_help_value,
                                  editorial.fixture_catalog(editorial_dir)['versions']), '').decode()
with tempfile.TemporaryDirectory() as unavailable_dir:
    unavailable_dir = str(Path(unavailable_dir).resolve())
    packed_retired, packed_retired_approval = editorial_bundle(
        'version-tableur-retire', 'Version au tableur retiré', 'Pièce du tableur retiré',
        characteristics={'language': 'Français', 'tools': ['Tableur retiré']})
    editorial.fixture_publish(unavailable_dir, packed_retired, packed_retired_approval)
    packed_available, packed_available_approval = editorial_bundle(
        'version-disponible', 'Note disponible', 'Pièce de la note disponible',
        characteristics={'expected_result': 'Note disponible', 'language': 'Français', 'tools': ['Tableur']})
    editorial.fixture_publish(unavailable_dir, packed_available, packed_available_approval)
    editorial.fixture_withdraw(unavailable_dir, 'tache-fictive', 'version-tableur-retire',
                               editorial.fixture_withdrawal(packed_retired['package_sha256'],
                                                            'tache-fictive', 'version-tableur-retire'))
    editorial_unavailable = views.render(
        editorial.fixture_search(unavailable_dir, {'outil': 'Tableur retiré'}), '').decode()
    editorial_search[json.dumps(['note', '', '', 'Tableur retiré', '', ''], ensure_ascii=False,
                                separators=(',', ':'))] = views.render(
        editorial.fixture_search(unavailable_dir, {'q': 'note', 'outil': 'Tableur retiré'}), '').decode()
with tempfile.TemporaryDirectory() as empty_dir:
    editorial_empty = views.render(editorial.catalog_view(str(Path(empty_dir).resolve())), '').decode()
print(json.dumps({**browser_views(), 'published-results': views.render(results_view(), '').decode(),
                  'published-results-filtered': views.render(results_view(filtered=True), '').decode(),
                  'published-results-withdrawn': views.render(results_view(withdrawn=True), '').decode(),
                  'published-results-trial-withdrawn': views.render(results_view(unavailable='trial-withdrawn'), '').decode(),
                  'reference_search': reference_search, 'reference_notices': reference_notices,
                  'editorial_pages': editorial_pages, 'editorial-search': editorial_search,
                  'editorial-version-search': editorial_version_search,
                  'editorial-help': editorial_help_form,
                  'editorial-help-result': editorial_help_result,
                  'editorial-help-withdrawn': editorial_help_withdrawn,
                  'editorial-empty': editorial_empty,
                  'editorial-withdrawn': editorial_withdrawn, 'editorial_piece': editorial_piece,
                  'editorial-unavailable': editorial_unavailable, 'editorial-long-tool': editorial_long_tool,
                  'reference-referral': reference_referral, 'reference-empty': reference_empty,
                  'reference_help': reference_help, 'reference_help_result': reference_help_result,
                  'reference_help_unavailable': reference_help_unavailable, **approved, 'data': views.render({'kind': 'privacy_data'}, '').decode(), 'launched': launched,
                  **{path[1:]: views.render({'kind': 'legal', 'path': path}, '').decode()
                     for path in ('/mentions-legales', '/cgu', '/confidentialite')},
                  'example': views.render(example_view(), 'csrf').decode(), 'script': views.STEP_SCRIPT,
                  'home': views.render({'kind': 'home'}, '').decode(),
                  'dossiers': views.render({'dossiers': []}, 'csrf').decode(),
                  'configurations': views.render({
                      'kind': 'configurations', 'dossier_id': 'd1', 'fetched_at': '2026-09-15T12:00:00+00:00',
                      'models': [{'id': 'mistralai/mistral-medium-3-5', 'name': 'Mistral Medium 3.5', 'selected': True,
                                  'not_adjustable': False, 'levels': ['none', 'high'], 'chosen': ''},
                                 {'id': 'deepseek/deepseek-v4.1-flash', 'name': 'DeepSeek V4.1 Flash', 'selected': True,
                                  'not_adjustable': True, 'levels': [], 'chosen': ''}],
                      'current_tier': 'low', 'available_tiers': ['low', 'high'], 'configurations': [],
                      'current_campaign_id': None, 'estimate_total_usd': None, 'superseded': [],
                      'estimate_available': False, 'assumptions': None, 'custom_models': []}, 'csrf').decode(),
                  'comparison_script': views.COMPARISON_FOCUS_SCRIPT, **attempt,
                  'launch': views.render(launch(), 'csrf').decode(),
                  'followup': views.render(launch('EMISSION_POSSIBLE'), 'csrf').decode(),
                  'followup_done': views.render(launch('RECEIVED'), 'csrf').decode(),
                  'launch_timeout': views.render(dict(RESPONSE_TIMEOUT), '', '/preparation/dossiers/d1/campaigns/c1/start',
                                                 error=True).decode(),
                  'progress_script': views.PREPARATION_PROGRESS_SCRIPT}))
`], {encoding: 'utf8', maxBuffer: 8 * 1024 * 1024}));
  server = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    res.setHeader('Cache-Control', 'no-store');
    if (req.method === 'POST' && url.pathname !== '/evaluations-externes/aide') {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const body = req.headers['content-type']?.includes('application/json') ? JSON.parse(raw) : Object.fromEntries(new URLSearchParams(raw));
      posts.push({path: url.pathname, body});
      res.writeHead(postStatus, {'Content-Type': 'application/json'}).end('{}');
    } else if (['/preparation/dossiers/d1/campaigns/c1/conditions', '/preparation/dossiers/d1'].includes(url.pathname)) {
      // Réponses du suivi imposées par le test : un statut d'erreur ou le nom d'une page rendue
      const reply = followupReplies.shift() ?? 'followup';
      if (typeof reply === 'number') {res.writeHead(reply).end(); return;}
      res.setHeader('Content-Type', 'text/html'); res.end(rendered[reply]);
    } else if (url.pathname === '/epreuves' || url.pathname.startsWith('/epreuves/')) {
      // HTML de la fixture isolée : la preuve HTTP du lecteur de production reste dans le serveur Python
      if (url.pathname.endsWith('/pieces/output-a')) {
        res.setHeader('Content-Type', 'text/plain; charset=utf-8');
        res.setHeader('X-Content-Type-Options', 'nosniff');
        res.end('Réponse publique de la fixture');
        return;
      }
      if (url.pathname.endsWith('/pieces/consigne')) {
        res.setHeader('Content-Type', 'text/plain; charset=utf-8');
        res.setHeader('X-Content-Type-Options', 'nosniff');
        res.setHeader('Content-Security-Policy', "default-src 'none'; base-uri 'none'; form-action 'self'");
        res.end(rendered.editorial_piece);
        return;
      }
      const fields = ['q', 'entree', 'langue', 'outil', 'difficulte', 'qualification']
        .map(key => url.searchParams.get(key) || '');
      const searchKey = JSON.stringify(fields);
      const html = url.pathname === '/epreuves' && url.search
        ? rendered['editorial-search'][searchKey]
        : (rendered['editorial-version-search'][url.pathname + '|' + searchKey]
           ?? rendered.editorial_pages[url.pathname]);
      if (!html) {res.writeHead(404).end('introuvable'); return;}
      res.setHeader('Content-Type', 'text/html');
      res.setHeader('Content-Security-Policy', "default-src 'none'; style-src 'self'; font-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'");
      res.end(html);
    } else if (url.pathname === '/evaluations-externes/aide') {
      if (req.method === 'POST') {
        req.on('data', () => {});
        req.on('end', () => {res.writeHead(303, {Location: '/evaluations-externes/aide/result'}); res.end();});
        return;
      }
      res.setHeader('Content-Type', 'text/html');
      res.setHeader('Content-Security-Policy', "default-src 'none'; style-src 'self'; font-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'");
      res.end(rendered.reference_help);
    } else if (url.pathname === '/evaluations-externes/aide/result') {
      res.setHeader('Content-Type', 'text/html');
      res.setHeader('Content-Security-Policy', "default-src 'none'; style-src 'self'; font-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'");
      res.end(rendered.reference_help_result);
    } else if (url.pathname === '/evaluations-externes' || url.pathname.startsWith('/evaluations-externes/')) {
      // Pages de views.render, comme /render : cette fixture ne remplace pas la preuve du serveur Python
      const html = url.pathname === '/evaluations-externes'
        ? rendered.reference_search[JSON.stringify(['q', 'category', 'verification'].map(key => url.searchParams.get(key) || ''))]
        : rendered.reference_notices[url.pathname.split('/').pop()];
      res.setHeader('Content-Type', 'text/html');
      res.setHeader('Content-Security-Policy', "default-src 'none'; style-src 'self'; font-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'");
      res.end(html);
    } else if (url.pathname.startsWith('/render/') || url.pathname === rendered.detail_href) {
      const hashes = [rendered.script, rendered.comparison_script, rendered.progress_script]
        .map(script => `'sha256-${createHash('sha256').update(script).digest('base64')}'`).join(' ');
      res.setHeader('Content-Type', 'text/html');
      res.setHeader('Content-Security-Policy', `default-src 'none'; script-src 'self' ${hashes}; connect-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'`);
      res.end(url.pathname === rendered.detail_href ? rendered.detail : rendered[url.pathname.split('/').pop()]);
    } else if (url.pathname === '/preparation/style.css') {
      res.setHeader('Content-Type', 'text/css'); res.end(readFileSync('benchmark_web/static/preparation.css'));
    } else if (/^\/preparation\/fonts\/[A-Za-z]+\.woff2$/.test(url.pathname)) {
      res.setHeader('Content-Type', 'font/woff2'); res.end(readFileSync('benchmark_web/static/fonts/' + url.pathname.split('/').pop()));
    } else if (url.pathname === '/bench-x.svg' || url.pathname === '/favicon.ico') {
      res.setHeader('Content-Type', url.pathname.endsWith('.svg') ? 'image/svg+xml' : 'image/vnd.microsoft.icon');
      res.end(readFileSync('benchmark_web/static' + url.pathname));
    } else if (url.pathname === '/preparation/privacy.js') {
      try { res.setHeader('Content-Type', 'text/javascript'); res.end(readFileSync('benchmark_web/privacy.js')); }
      catch {res.writeHead(404).end();}
    } else if (url.pathname.endsWith('/archive')) {
      if (mode === 'archiveDown') {res.writeHead(503).end(); return;}
      if (mode === 'archiveHang') {await new Promise(resolve => {release = resolve;}); res.writeHead(503).end(); return;}
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(manifest));
    } else if (url.pathname.endsWith('/items/record')) {
      const part = Number(url.searchParams.get('part'));
      const body = {snapshot_id: manifest.snapshot_id, item_id: 'record', part, total_parts: chunks.length, hex: chunks[part]};
      if (mode === 'corrupt') body.hex = '00' + body.hex.slice(2);
      if (mode === 'wrongSnapshot') body.snapshot_id = 'other-snapshot';
      if (mode === 'wrongPart') body.part++;
      if (mode === 'expired') {res.writeHead(410).end(); return;}
      if (mode === 'delay') { arrived(); await new Promise(resolve => {release = resolve;}); }
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(body));
    } else if (url.pathname === '/blocked') {
      res.setHeader('Content-Type', 'text/html');
      res.end('<section data-privacy-bootstrap data-return-path="/blocked"></section>');
    } else {
      res.setHeader('Content-Type', 'text/html');
      res.end('<!doctype html><html lang="fr"><title>Test local</title><main></main></html>');
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = 'http://127.0.0.1:' + server.address().port;
  browser = await chromium.launch({headless: true});
});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});
async function pageFor(context) {
  const page = await context.newPage();
  page.setDefaultTimeout(3000);
  await page.goto(origin);
  await page.evaluate(async () => {
    window.privacy = await import('/preparation/privacy.js');
    window.historyStore = await privacy.openHistory();
    await historyStore.enable();
  });
  return page;
}
test('only complete verified archives are readable; JSON export retains the whole record', async () => {
  fixture();
  const context = await browser.newContext();
  try {
    const page = await pageFor(context);
    assert.deepEqual(await page.evaluate(() => historyStore.list()), []);
    await page.evaluate(() => historyStore.archive('d1', 1));
    assert.deepEqual(await page.evaluate(() => historyStore.get('d1')), record);
    fixture(2); mode = 'corrupt';
    await assert.rejects(page.evaluate(() => historyStore.archive('d1', 2)), /incomplète ou illisible/i);
    assert.equal((await page.evaluate(() => historyStore.get('d1'))).content_version, 1);
  } finally {mode = 'normal'; await context.close();}
});

test('two tabs cannot overwrite a newer version or resurrect a cleared history', async () => {
  const context = await browser.newContext();
  try {
    const first = await pageFor(context), second = await pageFor(context);
    fixture(1); mode = 'delay';
    const waiting = new Promise(resolve => {arrived = resolve;});
    const old = first.evaluate(() => historyStore.archive('d1', 1));
    await waiting;
    assert.deepEqual(await second.evaluate(() => historyStore.list()), []);
    fixture(2); mode = 'normal';
    await second.evaluate(() => historyStore.archive('d1', 2));
    release();
    assert.equal(await old, false);
    assert.equal((await first.evaluate(() => historyStore.get('d1'))).content_version, 2);
    fixture(3); mode = 'delay';
    const delayed = new Promise(resolve => {arrived = resolve;});
    const pending = first.evaluate(() => historyStore.archive('d1', 3).catch(e => e.message));
    await delayed;
    await second.evaluate(() => historyStore.clear());
    await second.evaluate(() => historyStore.enable());
    release();
    assert.match(await pending, /effac|suspend|annul/i);
    assert.deepEqual(await first.evaluate(() => historyStore.list()), []);
    mode = 'normal';
    await second.evaluate(() => historyStore.archive('d1', 3));
    await first.evaluate(() => historyStore.remove('d1'));
    await assert.rejects(second.evaluate(() => historyStore.archive('d1', 3)), /effac|suspend/i);
    await second.evaluate(() => historyStore.enable('d1'));
    await second.evaluate(() => historyStore.archive('d1', 3));
    fixture(1);
    assert.equal(await first.evaluate(() => historyStore.archive('d1', 1)), false);
    assert.equal((await first.evaluate(() => historyStore.get('d1'))).content_version, 3);
  } finally {mode = 'normal'; release?.(); await context.close();}
});

test('public local history renders inert text and exports a complete case after server access expires', async () => {
  fixture();
  const context = await browser.newContext({acceptDownloads: true});
  try {
    const page = await pageFor(context);
    await page.evaluate(() => historyStore.archive('d1', 1));
    await page.setContent('<main data-privacy-history><p role="status" data-privacy-status></p><button data-privacy-action="clear">Effacer</button><button data-privacy-action="enable">Réactiver</button><div data-privacy-list></div></main>');
    await page.evaluate(() => privacy.mountPrivacy());
    assert.equal(await page.locator('[data-privacy-list] img, [data-privacy-list] script').count(), 0);
    assert.match(await page.locator('[data-privacy-list]').textContent(), /Réponse intégrale/);
    assert.match(await page.locator('[data-privacy-list]').textContent(), /Passage vérifié/);
    await page.locator('[data-privacy-list] > details > summary').click();
    const download = page.waitForEvent('download');
    await page.getByRole('button', {name: 'Télécharger ce cas (JSON)'}).click();
    const file = await (await download).path();
    assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), record);
    await page.getByRole('button', {name: 'Effacer', exact: true}).click();
    await page.waitForFunction(() => !document.querySelector('[data-privacy-list]').textContent);
    assert.equal((await page.evaluate(() => historyStore.state())).enabled, false);
  } finally {await context.close();}
});


test('activity requires a trusted visible interaction and never transmits entered text', async () => {
  posts = [];
  const context = await browser.newContext();
  try {
    const page = await pageFor(context);
    await page.setContent('<div data-privacy-activity data-csrf-token="csrf" data-dossier-id="d1"></div><label>Message<input id="message"></label><button id="click">Lire</button>');
    await page.evaluate(() => privacy.mountPrivacy());
    assert.equal(posts.length, 0);
    await page.evaluate(() => document.querySelector('#click').click());
    await page.evaluate(() => document.querySelector('#message').dispatchEvent(new Event('input', {bubbles: true})));
    assert.equal(posts.length, 0);
    const response = page.waitForResponse(r => r.url().endsWith('/preparation/activity'));
    await page.locator('#message').fill('secret text must not be sent');
    await response;
    assert.deepEqual(posts, [{path: '/preparation/activity', body: {csrf_token: 'csrf', dossier_id: 'd1'}}]);
    // Issue #454 : une page de la comparaison la nomme, pour renouveler aussi ses autres exemples
    posts = [];
    const compared = await pageFor(context);
    await compared.setContent('<div data-privacy-activity data-csrf-token="csrf" data-dossier-id="d1" data-campaign-id="d1-c1"></div><button id="read">Lire</button>');
    await compared.evaluate(() => privacy.mountPrivacy());
    const named = compared.waitForResponse(r => r.url().endsWith('/preparation/activity'));
    await compared.locator('#read').click();
    await named;
    assert.deepEqual(posts, [{path: '/preparation/activity', body: {csrf_token: 'csrf', dossier_id: 'd1', campaign_id: 'd1-c1'}}]);
  } finally {await context.close();}
});

test('consent POST sends booleans and CAS revisions; conflict never claims success', async () => {
  posts = []; postStatus = 409;
  const context = await browser.newContext();
  try {
    const page = await pageFor(context);
    await page.setContent('<form data-privacy-post="contribution" action="/preparation/dossiers/d1/contribution" method="post"><input name="csrf_token" type="hidden" value="purpose"><input name="revision" type="hidden" value="5"><input name="example_revision" type="hidden" value="3"><label><input type="checkbox" name="enabled">Contribuer</label><button>Enregistrer</button><p data-privacy-status role="status"></p></form>');
    await page.evaluate(() => privacy.mountPrivacy());
    await page.getByRole('checkbox').check();
    await page.getByRole('button', {name: 'Enregistrer'}).click();
    await page.waitForFunction(() => document.querySelector('[data-privacy-status]').textContent.includes('Actualisez la page'));
    assert.deepEqual(posts, [{path: '/preparation/dossiers/d1/contribution', body: {
      csrf_token: 'purpose', enabled: true, revision: 5, example_revision: 3}}]);
  } finally {postStatus = 200; await context.close();}
});


test('bootstrap detects blocked cookies without navigating in a loop and keeps explicit Continue', async () => {
  posts = [];
  const context = await browser.newContext();
  try {
    const page = await pageFor(context);
    await page.setContent('<section data-privacy-bootstrap data-return-path="/blocked"><p data-privacy-status role="status"></p><form action="/preparation/session/open" method="post"><button>Continuer</button></form></section>');
    await page.evaluate(() => privacy.mountPrivacy());
    await page.waitForFunction(() => document.querySelector('[data-privacy-status]').textContent.includes('cookies'));
    assert.deepEqual(posts, [{path: '/preparation/session/open', body: {}}]);
    assert.equal(page.url(), origin + '/');
    await page.getByRole('button', {name: 'Continuer'}).click();
    await page.waitForFunction(() => document.querySelector('[data-privacy-status]').textContent.includes('cookies'));
    assert.equal(posts.length, 2);
  } finally {await context.close();}
});


test('chunk boundaries, snapshot identity, schema and HTTP expiry preserve the previous complete copy', async () => {
  const context = await browser.newContext();
  try {
    const page = await pageFor(context);
    fixture(1, 'é'.repeat(600000));
    await page.evaluate(() => historyStore.archive('d1', 1));
    assert.equal(await page.evaluate(async () => (await historyStore.get('d1')).need.length), 600000);
    for (const failure of ['wrongSnapshot', 'wrongPart', 'expired']) {
      fixture(2); mode = failure;
      await assert.rejects(page.evaluate(() => historyStore.archive('d1', 2)));
      assert.equal((await page.evaluate(() => historyStore.get('d1'))).content_version, 1);
    }
    mode = 'normal'; fixture(2, undefined, {key: 'NEVER_STORE_THIS'});
    await assert.rejects(page.evaluate(() => historyStore.archive('d1', 2)), /incomplète ou illisible/);
    assert.equal(await page.evaluate(async () => JSON.stringify(await historyStore.list()).includes('NEVER_STORE_THIS')), false);
  } finally {mode = 'normal'; await context.close();}
});

test('a browser quota failure cannot publish a partial new version', async () => {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await page.goto(origin);
    const cdp = await context.newCDPSession(page);
    await cdp.send('Storage.overrideQuotaForOrigin', {origin, quotaSize: 65536});
    await page.evaluate(async () => {window.privacy = await import('/preparation/privacy.js'); window.historyStore = await privacy.openHistory(); await historyStore.enable();});
    fixture(1); await page.evaluate(() => historyStore.archive('d1', 1));
    fixture(2, randomBytes(2000000).toString('hex'));
    await assert.rejects(page.evaluate(() => historyStore.archive('d1', 2)), /quota/i);
    assert.equal((await page.evaluate(() => historyStore.get('d1'))).content_version, 1);
  } finally {await context.close();}
});

test('rendered pages write nothing before the single choice, then checking it stores the copy under CSP and fits mobile', async () => {
  fixture(); posts = [];
  const context = await browser.newContext({viewport: {width: 1200, height: 900}});
  try {
    const page = await context.newPage(); page.setDefaultTimeout(3000);
    const failures = [];
    page.on('pageerror', error => failures.push(error.message));
    page.on('console', message => {if (/Content Security Policy/.test(message.text())) failures.push(message.text());});
    const databases = () => page.evaluate(async () => (await indexedDB.databases()).map(db => db.name));
    await page.goto(origin + '/render/example');
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent.includes('désactivé'));
    assert.equal(posts.length, 0);
    assert.deepEqual(await databases(), []);
    assert.equal(await page.locator('[data-privacy-action="archive"]').isHidden(), true);
    assert.equal(await page.locator('[data-privacy-action="enable-case"]').isHidden(), true);
    assert.equal(await page.getByRole('checkbox').isChecked(), false);
    assert.equal(await page.getByRole('checkbox').isEnabled(), true);
    mkdirSync('reports/privacy-browser', {recursive: true});
    await page.locator('.privacy-consent').screenshot({path: 'reports/privacy-browser/consent-desktop.png'});
    // Saving the unchecked box is a choice against both effects
    await Promise.all([page.waitForEvent('load'), page.getByRole('button', {name: 'Enregistrer mon choix'}).click()]);
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent.includes('désactivé'));
    assert.deepEqual(await databases(), []);
    await page.getByRole('checkbox').check();
    await Promise.all([page.waitForEvent('load'), page.getByRole('button', {name: 'Enregistrer mon choix'}).click()]);
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent.includes('Copie gardée dans ce navigateur.'));
    assert.deepEqual(posts.filter(post => post.path.endsWith('/contribution')).map(post => [post.path, post.body.enabled]), [
      ['/preparation/dossiers/d1/contribution', false], ['/preparation/dossiers/d1/contribution', true]]);
    assert.deepEqual(await databases(), ['bench-x-history']);
    await page.goto(origin + '/render/data');
    await page.waitForFunction(() => document.querySelector('[data-privacy-list] > details'));
    await page.locator('[data-privacy-list] > details > summary').click();
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path: 'reports/privacy-browser/data-mobile.png', fullPage: true});
    assert.deepEqual(failures, []);
  } finally {await context.close();}
});

test('the box state sent to the server is the one that decides local history', async () => {
  const context = await browser.newContext();
  try {
    const page = await context.newPage(); page.setDefaultTimeout(3000);
    await page.goto(origin + '/render/example');
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent.includes('désactivé'));
    let held;
    await page.route('**/preparation/dossiers/d1/contribution', route => {held = route;});
    await page.getByRole('button', {name: 'Enregistrer mon choix'}).click();
    await page.waitForFunction(() => document.querySelector('.privacy-consent form').dataset.busy);
    await page.getByRole('checkbox').check();
    assert.equal(held.request().postDataJSON().enabled, false);
    await Promise.all([page.waitForEvent('load'), held.fulfill({status: 200, contentType: 'application/json', body: '{}'})]);
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent.includes('désactivé'));
    assert.deepEqual(await page.evaluate(async () => (await indexedDB.databases()).map(db => db.name)), []);
  } finally {await context.close();}
});

test('withdrawing a contribution keeps local copies active and erasable from Mes données', async () => {
  fixture();
  const context = await browser.newContext();
  try {
    const page = await pageFor(context);
    await page.evaluate(() => historyStore.archive('d1', 1));
    await page.setContent('<form data-privacy-post="withdraw" action="/preparation/contributions/c1/withdraw"><input type="hidden" name="csrf_token" value="purpose"><button>Retirer</button><p data-privacy-status></p></form>');
    await page.evaluate(() => privacy.mountPrivacy());
    await Promise.all([page.waitForEvent('load'), page.getByRole('button', {name: 'Retirer'}).click()]);
    assert.deepEqual(posts.at(-1), {path: '/preparation/contributions/c1/withdraw', body: {csrf_token: 'purpose'}});
    await page.goto(origin + '/render/data');
    await page.waitForFunction(() => document.querySelector('[data-privacy-list] > details'));
    assert.match(await page.locator('[data-privacy-status]').first().textContent(), /Cas gardés dans ce navigateur : 1/);
    await page.locator('[data-privacy-list] > details > summary').click();
    await page.getByRole('button', {name: 'Effacer et ne plus garder ce cas'}).click();
    await page.waitForFunction(() => !document.querySelector('[data-privacy-list]').textContent);
    const state = await page.evaluate(async () => {
      const store = await (await import('/preparation/privacy.js')).openHistory({create: false});
      return {record: await store.get('d1'), global: await store.state()};
    });
    assert.deepEqual(state, {record: null, global: {enabled: true, chosen: true}});
  } finally {await context.close();}
});


test('server deletion and withdrawal send only their purpose CSRF and never archive credentials', async () => {
  const context = await browser.newContext();
  try {
    for (const [action, path] of [['delete', '/preparation/dossiers/d1/delete'], ['withdraw', '/preparation/contributions/c1/withdraw']]) {
      posts = [];
      const page = await pageFor(context);
      await page.setContent(`<form data-privacy-post="${action}" action="${path}"><input type="hidden" name="csrf_token" value="purpose-${action}"><button>Confirmer</button><p data-privacy-status></p></form>`);
      await page.evaluate(() => privacy.mountPrivacy());
      await page.getByRole('button', {name: 'Confirmer'}).click();
      if (action === 'delete') {
        await page.waitForFunction(() => document.querySelector('[data-privacy-status]').textContent.includes('lors du prochain nettoyage planifié'));
        assert.equal(page.url(), origin + '/');
      } else await page.waitForURL(origin + '/');
      assert.deepEqual(posts, [{path, body: {csrf_token: 'purpose-' + action}}]);
      await page.close();
    }
  } finally {await context.close();}
});

test('bootstrap success navigates once to the validated same-origin target', async () => {
  posts = [];
  const context = await browser.newContext();
  try {
    const page = await pageFor(context);
    await page.setContent('<section data-privacy-bootstrap data-return-path="/ready?view=1"><p data-privacy-status></p><form><button>Continuer</button></form></section>');
    await page.evaluate(() => privacy.mountPrivacy());
    await page.waitForURL('**/ready?view=1');
    assert.deepEqual(posts, [{path: '/preparation/session/open', body: {}}]);
  } finally {await context.close();}
});

test('native consent form remains usable without JavaScript, with no preselected agreement', async () => {
  posts = [];
  const context = await browser.newContext({javaScriptEnabled: false});
  try {
    const page = await context.newPage();
    await page.goto(origin + '/render/example');
    assert.equal(await page.getByRole('checkbox').isChecked(), false);
    assert.match(await page.locator('.privacy-consent').textContent(), /besoin de JavaScript ; sans lui, seule la contribution/i);
    await page.getByRole('checkbox').check();
    await page.getByRole('button', {name: 'Enregistrer mon choix'}).click();
    assert.deepEqual(posts, [{path: '/preparation/dossiers/d1/contribution', body: {
      csrf_token: 'privacy-token', revision: '0', example_revision: '1', enabled: 'true'}}]);
  } finally {await context.close();}
});

test('structured costs and nullable answers stay readable and retain unknown values in JSON', async () => {
  fixture(1, undefined, {campaigns: [{id: 'c1', models: [
    {name: 'Connu', verdict: 'SATISFAIT', cost: {amount: '0.02', currency: 'USD'}, answer: 'Réponse', evidence: []},
    {name: 'Inconnu', verdict: null, cost: {amount: null, currency: 'USD'}, answer: null, evidence: []}
  ]}]});
  const context = await browser.newContext();
  try {
    const page = await pageFor(context);
    await page.evaluate(() => historyStore.archive('d1', 1));
    await page.setContent('<main data-privacy-history><p data-privacy-status></p><button data-privacy-action="clear">Effacer</button><button data-privacy-action="enable">Réactiver</button><div data-privacy-list></div></main>');
    await page.evaluate(() => privacy.mountPrivacy());
    const text = await page.locator('[data-privacy-list]').textContent();
    assert.match(text, /0.02 USD/);
    assert.match(text, /Coût inconnu/);
    assert.match(text, /Aucune réponse conservée/);
    assert.match(text, /Verdict indisponible/);
    assert.equal(text.includes('[object Object]'), false);
    assert.deepEqual(await page.evaluate(() => historyStore.get('d1')), record);
  } finally {await context.close();}
});

test('a composite obligation keeps its elements apart, whatever punctuation they contain', async () => {
  const composite = {description: 'Lister : les actions ; avec échéance', elements: ['Forme ; une ligne', 'Échéance : <b>exacte</b>']};
  fixture(1, undefined, {revisions: [{...fresh().revisions[0], criteria: ['Exactitude', composite]}]});
  const context = await browser.newContext({acceptDownloads: true});
  try {
    const page = await pageFor(context);
    await page.evaluate(() => historyStore.archive('d1', 1));
    await page.setContent('<main data-privacy-history><p data-privacy-status></p><button data-privacy-action="clear">Effacer</button><button data-privacy-action="enable">Réactiver</button><div data-privacy-list></div></main>');
    await page.evaluate(() => privacy.mountPrivacy());
    const item = page.locator('[data-privacy-list] li', {hasText: composite.description}).first();
    assert.deepEqual(await item.locator('li').allTextContents(), composite.elements);
    assert.equal(await page.locator('[data-privacy-list] b').count(), 0);
    assert.equal((await page.locator('[data-privacy-list]').textContent()).includes('[object Object]'), false);
    assert.deepEqual(await page.evaluate(() => historyStore.get('d1')), record);
    await page.locator('[data-privacy-list] > details > summary').first().click();
    const download = page.waitForEvent('download');
    await page.getByRole('button', {name: 'Télécharger ce cas (JSON)'}).click();
    assert.deepEqual(JSON.parse(readFileSync(await (await download).path(), 'utf8')), record);
  } finally {await context.close();}
});

test('case deletion tombstones locally before POST and retains explicit failure without resurrection', async () => {
  fixture();
  const context = await browser.newContext();
  let pending;
  try {
    const observer = await pageFor(context);
    const page = await context.newPage(); page.setDefaultTimeout(3000);
    await page.goto(origin + '/render/example');
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent.includes('Copie gardée dans ce navigateur.'));
    fixture(2); mode = 'delay';
    const waiting = new Promise(resolve => {arrived = resolve;});
    pending = observer.evaluate(() => historyStore.archive('d1', 2).catch(error => error.message));
    await waiting;
    let observed;
    await page.route('**/preparation/dossiers/d1/delete', async route => {
      observed = await observer.evaluate(async () => ({record: await historyStore.get('d1'), state: await historyStore.state('d1')}));
      await route.fulfill({status: 503, contentType: 'application/json', body: '{}'});
    });
    await page.locator('.privacy-controls > summary').click();
    const deletionResponse = page.waitForResponse(response => response.url().endsWith('/d1/delete'));
    await page.locator('form[data-privacy-post="delete"] button').click();
    await deletionResponse;
    assert.deepEqual(observed, {record: null, state: {enabled: false, chosen: true}});
    await page.waitForFunction(() => document.querySelector('form[data-privacy-post="delete"] [data-privacy-status]')?.textContent.includes('n’est pas confirmée'));
    const status = await page.locator('form[data-privacy-post="delete"] [data-privacy-status]').textContent();
    assert.match(status, /Copie de ce navigateur effacée/);
    assert.match(status, /La suppression sur le serveur, contribution comprise, n’est pas confirmée/);
    assert.equal(page.url(), origin + '/render/example');
    release();
    assert.match(await pending, /effac|suspend|annul/i);
    assert.equal(await observer.evaluate(() => historyStore.get('d1')), null);
  } finally {mode = 'normal'; release?.(); await pending; await context.close();}
});

test('case deletion still requests server purge when IndexedDB is unavailable and preserves both outcomes', async () => {
  for (const statusCode of [200, 503]) {
    posts = []; postStatus = statusCode;
    const context = await browser.newContext();
    try {
      const page = await pageFor(context);
      await page.setContent('<form data-privacy-post="delete" action="/preparation/dossiers/d1/delete"><input type="hidden" name="csrf_token" value="purpose"><button>Supprimer</button><p data-privacy-status></p></form>');
      // Simulate an absent browser capability; the other scenarios use native IndexedDB
      await page.evaluate(() => Object.defineProperty(window, 'indexedDB', {value: undefined}));
      await page.evaluate(() => privacy.mountPrivacy());
      await page.getByRole('button', {name: 'Supprimer'}).click();
      await page.waitForFunction(() => document.querySelector('[data-privacy-status]')?.textContent.includes('Effacement de la copie de ce navigateur non confirmé'));
      assert.deepEqual(posts, [{path: '/preparation/dossiers/d1/delete', body: {csrf_token: 'purpose'}}]);
      assert.equal(page.url(), origin + '/');
      const status = await page.locator('[data-privacy-status]').textContent();
      assert.match(status, statusCode === 200 ? /lors du prochain nettoyage planifié/ : /La suppression sur le serveur, contribution comprise, n’est pas confirmée/);
    } finally {postStatus = 200; await context.close();}
  }
});


const LEGAL = ['mentions-legales', 'cgu', 'confidentialite'];
// WCAG 2.x : 4.5:1, 3:1 pour le grand texte ; fond pris au premier ancêtre opaque
function contrastFailures() {
  const channels = color => color.match(/[\d.]+/g).map(Number);
  const luminance = rgb => rgb.slice(0, 3).map(v => v / 255)
    .map(v => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
    .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
  const background = element => {
    for (; element; element = element.parentElement) {
      const color = channels(getComputedStyle(element).backgroundColor);
      if (color.length < 4 || color[3] > 0) return color;
    }
    return [255, 255, 255];
  };
  const failures = [];
  for (const element of document.body.querySelectorAll('*')) {
    if (![...element.childNodes].some(node => node.nodeType === 3 && node.textContent.trim())) continue;
    const style = getComputedStyle(element);
    if (style.visibility === 'hidden' || !element.getClientRects().length) continue;
    const [a, b] = [luminance(channels(style.color)), luminance(background(element))];
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    const size = parseFloat(style.fontSize);
    const large = size >= 24 || size >= 18.66 && Number(style.fontWeight) >= 700;
    if (ratio < (large ? 3 : 4.5)) failures.push(element.tagName + ' ' + ratio.toFixed(2) + ' ' + element.textContent.trim().slice(0, 40));
  }
  return failures;
}

test('legal pages keep CSP, French headings, visible focus, AA contrast and fit 390 px in both themes', async () => {
  for (const colorScheme of ['light', 'dark']) {
    for (const viewport of [{width: 1200, height: 900}, {width: 390, height: 844}]) {
      const context = await browser.newContext({viewport, colorScheme});
      try {
        const page = await context.newPage(); page.setDefaultTimeout(3000);
        const failures = [];
        page.on('pageerror', error => failures.push(error.message));
        page.on('console', message => {if (/Content Security Policy/.test(message.text())) failures.push(message.text());});
        for (const name of LEGAL) {
          const where = `${name} ${colorScheme} ${viewport.width}`;
          await page.goto(origin + '/render/' + name);
          await page.evaluate(() => document.fonts.ready);
          assert.equal(await page.getAttribute('html', 'lang'), 'fr', where);
          const levels = await page.$$eval('h1, h2, h3, h4', headings => headings.map(h => Number(h.tagName[1])));
          assert.equal(levels.filter(level => level === 1).length, 1, where);
          assert.equal(levels[0], 1, where);
          levels.forEach((level, index) => assert.ok(!index || level <= levels[index - 1] + 1, `${where} saut de titre`));
          for (const href of ['/mentions-legales', '/cgu', '/confidentialite'])
            assert.equal(await page.locator(`footer nav[aria-label="Informations légales"] a[href="${href}"]`).count(), 1, `${where} ${href}`);
          assert.deepEqual(await page.evaluate(contrastFailures), [], where);
          let inMain = false;
          for (let press = 0; press < 40 && !inMain; press++) {
            await page.keyboard.press('Tab');
            inMain = await page.evaluate(() => !!document.activeElement.closest('main'));
          }
          assert.ok(inMain, `${where} aucun élément focalisable dans le contenu`);
          const outline = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
          assert.notEqual(outline, 'none', `${where} focus invisible`);
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${where} débordement`);
          if (viewport.width === 390) {
            mkdirSync('reports/privacy-browser', {recursive: true});
            await page.screenshot({path: `reports/privacy-browser/legal-${name}-${colorScheme}-mobile.png`, fullPage: true});
          }
        }
        assert.deepEqual(failures, []);
      } finally {await context.close();}
    }
  }
});

test('per-model reasoning tuning opens without JavaScript, stays in the form and fits 390 px', async () => {
  for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({viewport: {width: 390, height: 844}, colorScheme, javaScriptEnabled: false});
    try {
      const page = await context.newPage(); page.setDefaultTimeout(3000);
      await page.goto(origin + '/render/configurations');
      const tuning = page.locator('details', {hasText: 'Ajuster le niveau par modèle'});
      assert.equal(await tuning.count(), 1, colorScheme);
      const select = page.locator('select[name="effort:mistralai/mistral-medium-3-5"]');
      assert.equal(await select.isVisible(), false, `${colorScheme} dépliant fermé par défaut`);
      await tuning.locator('summary').click();
      assert.equal(await select.isVisible(), true, colorScheme);
      assert.equal(await select.getAttribute('form'), 'configurations-form', colorScheme);
      assert.deepEqual(await select.locator('option').evaluateAll(options => options.map(option => option.value)), ['', 'none', 'high']);
      assert.equal(await page.locator('label[for="' + await select.getAttribute('id') + '"]').textContent(), 'Mistral Medium 3.5');
      assert.equal(await page.locator('select[name="effort:deepseek/deepseek-v4.1-flash"]').count(), 0, `${colorScheme} niveau fixe sans réglage`);
      await select.focus();
      assert.notEqual(await select.evaluate(element => getComputedStyle(element).outlineStyle), 'none', `${colorScheme} focus invisible`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${colorScheme} débordement`);
      assert.deepEqual(await page.evaluate(contrastFailures), [], colorScheme);
      mkdirSync('reports/privacy-browser', {recursive: true});
      await page.screenshot({path: `reports/privacy-browser/configurations-tuning-${colorScheme}-mobile.png`, fullPage: true});
    } finally {await context.close();}
  }
});

// Zones dont un script réécrit le texte après chargement ; tout autre `role="status"` est un abus de sémantique
const LIVE_STATUS = '[data-privacy-status], #preparation-progress [role="status"], .result-status, [data-probe-request], .custom-model-forms [role="status"]';
test('pages expose no decorative SVG, keep status roles for live zones and hide the honeypot without stylesheet', async () => {
  for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({viewport: {width: 390, height: 844}, colorScheme});
    try {
      const page = await context.newPage(); page.setDefaultTimeout(3000);
      for (const name of ['home', 'dossiers', 'example', 'comparison']) {
        const where = `${name} ${colorScheme}`;
        await page.goto(origin + '/render/' + name);
        await page.evaluate(() => document.fonts.ready);
        assert.deepEqual(await page.$$eval('svg', svgs => svgs.filter(svg => !svg.closest('[aria-hidden="true"]'))
          .map(svg => svg.outerHTML)), [], `${where} SVG exposé`);
        assert.deepEqual(await page.$$eval('[role="status"]', (zones, live) => zones.filter(zone => !zone.matches(live))
          .map(zone => zone.textContent.trim().slice(0, 60)), LIVE_STATUS), [], `${where} role="status" statique`);
        assert.deepEqual(await page.evaluate(contrastFailures), [], where);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${where} débordement`);
        if (name !== 'example' && name !== 'comparison')
          assert.equal(await page.locator('.lead').evaluate(lead => getComputedStyle(lead).borderLeftStyle), 'none',
            `${where} l’accroche est un paragraphe, sans filet latéral (DESIGN.md)`);
      }
      await page.route('**/preparation/style.css', route => route.abort());
      for (const name of ['dossiers', 'example']) {
        await page.goto(origin + '/render/' + name);
        const trap = page.locator('#website');
        assert.equal(await trap.count(), 1, name);
        assert.equal(await trap.isVisible(), false, `${name} honeypot visible sans feuille de style`);
        assert.equal(await page.getByRole('textbox', {name: 'Site web'}).count(), 0, `${name} honeypot exposé`);
        for (let press = 0; press < 60; press++) {
          await page.keyboard.press('Tab');
          assert.notEqual(await page.evaluate(() => document.activeElement.id), 'website', `${name} honeypot focalisable`);
        }
      }
      await page.unroute('**/preparation/style.css');
    } finally {await context.close();}
  }
});

test('attempt proofs open a complete page without JavaScript and the modal with it, focus returned', async () => {
  const failures = [];
  const watch = page => {
    page.setDefaultTimeout(3000);
    page.on('pageerror', error => failures.push(error.message));
    page.on('console', message => {if (/Content Security Policy/.test(message.text())) failures.push(message.text());});
    return page;
  };
  const off = await browser.newContext({javaScriptEnabled: false, viewport: {width: 390, height: 844}});
  try {
    const page = watch(await off.newPage());
    await page.goto(origin + '/render/comparison');
    await page.getByRole('link', {name: 'Détail et preuves'}).click();
    await page.waitForURL(origin + rendered.detail_href);
    assert.equal(await page.getAttribute('html', 'lang'), 'fr');
    assert.equal(await page.locator('h1').textContent(), 'Détail et preuves');
    assert.equal(await page.evaluate(() => [...document.styleSheets].some(sheet =>
      sheet.href?.endsWith('/preparation/style.css') && sheet.cssRules.length > 0)), true);
    assert.equal(await page.locator('#attempt-detail').isVisible(), true);
    assert.match(await page.locator('#attempt-detail').textContent(), /Pourquoi ce verdict/);
    assert.equal(await page.getByRole('link', {name: 'Revenir aux résultats'}).getAttribute('href'), rendered.back_href);
    assert.match(await page.locator('nav.steps [aria-current="step"]').textContent(), /Résultats/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'débordement à 390 px');
    mkdirSync('reports/privacy-browser', {recursive: true});
    await page.screenshot({path: 'reports/privacy-browser/attempt-detail-nojs-mobile.png', fullPage: true});
  } finally {await off.close();}
  const on = await browser.newContext();
  try {
    const page = watch(await on.newPage());
    await page.goto(origin + '/render/comparison');
    // Seule tentative, donc la plus chère : la barre de coût est pleine, peinte et distincte de sa piste
    const bar = await page.$eval('.costbar', track => {
      const rect = track.querySelector('rect');
      const box = rect?.getBoundingClientRect();
      return {track: track.getBoundingClientRect().width, width: box?.width ?? 0, height: box?.height ?? 0,
              fill: rect ? getComputedStyle(rect).fill : 'none', background: getComputedStyle(track).backgroundColor};
    });
    assert.ok(bar.width > 0 && bar.height > 0, 'barre de coût sans surface');
    assert.equal(bar.width, bar.track);
    assert.doesNotMatch(bar.fill, /^(none|transparent|rgba\(.*, 0\))$/, 'barre de coût sans remplissage');
    assert.notEqual(bar.fill, bar.background, 'barre de coût confondue avec sa piste');
    await page.getByRole('button', {name: 'Détail et preuves'}).focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#result-dialog .result-status').textContent === 'Détail chargé.');
    assert.equal(page.url(), origin + '/render/comparison');
    assert.equal(await page.evaluate(() => document.getElementById('result-dialog').open), true);
    assert.match(await page.locator('#result-dialog .result-body').textContent(), /Pourquoi ce verdict/);
    assert.equal(await page.evaluate(() => document.activeElement.matches('#result-dialog [data-close]')), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => document.getElementById('result-dialog').open), false);
    assert.equal(await page.evaluate(() => document.activeElement.matches('a[data-result]')), true, 'focus non rendu au lien');
    // Le focus rendu au lien peut déjà avoir fait défiler la page : seul compte le défilement causé par Espace
    const before = await page.evaluate(() => scrollY);
    await page.keyboard.press('Space');
    await page.waitForFunction(() => document.querySelector('#result-dialog .result-status').textContent === 'Détail chargé.');
    assert.equal(await page.evaluate(() => scrollY), before, 'Espace a fait défiler la page');
    await page.getByRole('button', {name: 'Fermer'}).click();
    assert.equal(await page.evaluate(() => document.activeElement.matches('a[data-result]')), true, 'focus non rendu après Fermer');
  } finally {await on.close();}
  const legacy = await browser.newContext();
  try {
    const page = watch(await legacy.newPage());
    await page.addInitScript(() => {delete HTMLDialogElement.prototype.showModal;});
    await page.goto(origin + '/render/comparison');
    await page.getByRole('link', {name: 'Détail et preuves'}).click();
    await page.waitForURL(origin + rendered.detail_href);
    assert.equal(await page.locator('h1').textContent(), 'Détail et preuves');
    await page.goBack();
    await page.waitForFunction(() => document.activeElement?.id === 'attempt-long');
  } finally {await legacy.close();}
  assert.deepEqual(failures, []);
});

test('results and publication preview stay readable at 390 px: whole words, pinned model, neutral bars, touch help', async () => {
  const failures = [];
  const context = await browser.newContext({viewport: {width: 390, height: 844}, hasTouch: true, isMobile: true});
  try {
    const page = await context.newPage(); page.setDefaultTimeout(3000);
    page.on('pageerror', error => failures.push(error.message));
    page.on('console', message => {if (/Content Security Policy/.test(message.text())) failures.push(message.text());});
    await page.goto(origin + '/render/results');
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'débordement à 390 px');
    // Aucun mot coupé : chaque cellule offre au moins la largeur de son mot visible le plus long
    const cut = await page.$$eval('.table-scroll td, .table-scroll th', cells => cells.flatMap(cell => {
      const probe = document.createElement('span');
      Object.assign(probe.style, {whiteSpace: 'nowrap', position: 'absolute', visibility: 'hidden'});
      cell.append(probe);
      const style = getComputedStyle(cell);
      const room = cell.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const words = cell.innerText.split(/\s+/).filter(Boolean).filter(word => {
        probe.textContent = word;
        return probe.getBoundingClientRect().width > room + 1;
      });
      probe.remove();
      return words;
    }));
    assert.deepEqual(cut, [], 'mots coupés dans le tableau');
    // Tableau défilé jusqu'au coût : le nom du candidat reste à gauche
    await page.$eval('.table-scroll', scroller => {scroller.scrollLeft = scroller.scrollWidth;});
    assert.equal(await page.$eval('.table-scroll tbody th', th =>
      Math.abs(th.getBoundingClientRect().left - th.closest('.table-scroll').getBoundingClientRect().left) < 1), true,
      'colonne Modèle non épinglée');
    await page.$eval('.table-scroll', scroller => {scroller.scrollLeft = 0;});
    // Une barre de coût est une donnée : jamais la couleur d'accent réservée à l'interaction
    const accent = await page.evaluate(() => {
      const probe = document.createElement('i');
      probe.style.color = 'var(--accent)'; document.body.append(probe);
      const color = getComputedStyle(probe).color; probe.remove(); return color;
    });
    const fills = await page.$$eval('.costbar rect', rects => rects.map(rect => getComputedStyle(rect).fill));
    assert.ok(fills.length > 1, 'barres de coût absentes');
    assert.ok(fills.every(fill => fill !== accent), 'barre de coût en couleur d’accent');
    // Verdict et exigence en défaut, entière ; l'explication du juge reste dans le détail
    assert.match((await page.locator('table').allInnerTexts()).join(' '), /Exigence non respectée : Action présente <img src=x onerror=alert\(1\)>\./);
    assert.equal(await page.locator('table img').count(), 0, 'motif du juge interprété comme HTML');
    // Une mesure booléenne se lit Oui ou Non, jamais True ou False
    assert.doesNotMatch((await page.locator('table').allInnerTexts()).join(' '), /\b(True|False)\b/);
    // Pied de page : liens légaux en colonne, Code source à droite de la version
    const bottom = await page.$eval('footer .bottom', row => {
      const source = row.querySelector('a.source');
      return {right: Math.round(row.getBoundingClientRect().right - source.getBoundingClientRect().right),
              icon: Boolean(source.querySelector('svg[aria-hidden="true"]'))};
    });
    assert.deepEqual(bottom, {right: 0, icon: true});
    assert.match(await page.locator('main').innerText(), /Résultat attendu :/);
    const method = await page.locator('#method').textContent();
    assert.match(method, /Le verdict vaut pour chaque modèle tel qu’il a été réglé et appelé ici/);
    assert.match(method, /la même consigne et les mêmes pièces, et travaillent avec le même outil/);
    // Au toucher, la définition de la qualité s'affiche et tient dans l'écran
    await page.locator('.quality-help').tap();
    const tooltip = await page.$eval('.quality-tooltip', tip => ({visible: getComputedStyle(tip).visibility === 'visible',
      left: tip.getBoundingClientRect().left, right: tip.getBoundingClientRect().right}));
    assert.equal(tooltip.visible, true, 'infobulle absente au toucher');
    assert.ok(tooltip.left >= 0 && tooltip.right <= 390, 'infobulle hors écran ' + JSON.stringify(tooltip));
    mkdirSync('reports/privacy-browser', {recursive: true});
    // Deux exemples : chaque tableau garde son titre et son dénominateur, et le clavier atteint chacun
    assert.deepEqual(await page.$$eval('.comparison-results h3', titles => titles.map(title =>
      /^\d+ réponses? évaluées? sur \d+ essais? prévus?$/.test(title.nextElementSibling.textContent))), [true, true]);
    const reached = new Set();
    for (let press = 0; press < 120 && reached.size < 2; press++) {
      await page.keyboard.press('Tab');
      const region = await page.evaluate(() => document.activeElement.matches('.table-scroll[role="region"]')
        ? document.activeElement.getAttribute('aria-label') : null);
      if (region) reached.add(region);
    }
    assert.equal(reached.size, 2, 'tableau d’un exemple hors d’atteinte au clavier');
    await page.screenshot({path: 'reports/privacy-browser/results-mobile.png', fullPage: true});
    await page.goto(origin + '/render/preview');
    assert.equal(await page.locator('h1').count(), 1, 'aperçu à plusieurs titres principaux');
    assert.equal(await page.locator('pre').count(), 0, 'JSON brut dans l’aperçu');
    // La référence du juge reste réservée à l'évaluation : jamais proposée à la publication
    assert.doesNotMatch(await page.locator('fieldset').innerText(), /reference\.txt/);
    assert.doesNotMatch(await page.locator('main').innerText(), /\b[0-9a-f]{32,64}\b|\bPASS\b|\bFAIL\b/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'aperçu déborde à 390 px');
    await page.screenshot({path: 'reports/privacy-browser/preview-mobile.png', fullPage: true});
    // Le consentement suit la décision principale
    await page.goto(origin + '/render/example');
    assert.equal(await page.evaluate(() => {
      const validate = [...document.querySelectorAll('#validation button')].find(button => /Oui, c’est le travail à tester/.test(button.textContent));
      const consent = document.querySelector('[data-privacy-post="contribution"]');
      return Boolean(validate && consent && validate.compareDocumentPosition(consent) & Node.DOCUMENT_POSITION_FOLLOWING);
    }), true, 'consentement avant la validation');
  } finally {await context.close();}
  const off = await browser.newContext({javaScriptEnabled: false});
  try {
    // Sans JavaScript, Mes données ne promet pas un chargement qui n'arrivera jamais
    const page = await off.newPage(); page.setDefaultTimeout(3000);
    await page.goto(origin + '/render/data');
    assert.doesNotMatch(await page.locator('main').innerText(), /Chargement/);
  } finally {await off.close();}
  assert.deepEqual(failures, []);
});

test('priority advice stays native, escaped, keyboard reachable and readable at 390 px', async () => {
  const failures = [];
  const context = await browser.newContext({viewport: {width: 390, height: 844}, hasTouch: true, isMobile: true});
  try {
    const page = await context.newPage(); page.setDefaultTimeout(3000);
    page.on('pageerror', error => failures.push(error.message));
    page.on('console', message => {if (/Content Security Policy/.test(message.text())) failures.push(message.text());});
    await page.goto(origin + '/render/results_priority');
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'débordement à 390 px');
    const select = page.locator('#filter-priority');
    assert.equal(await select.count(), 1, 'priorité absente du formulaire');
    assert.match(await page.locator('#filters > summary').innerText(), /Conseil/);
    assert.equal(await select.inputValue(), 'duration');
    assert.deepEqual(await page.locator('#filter-priority option').evaluateAll(options => options.map(option => option.value)),
                     ['', 'cost', 'duration', 'present']);
    assert.match(await page.locator('label[for="filter-priority"]').innerText(), /Priorité du conseil/);
    // La priorité et son compromis restent visibles ; les libellés du contrat restent du texte
    const advice = page.locator('.economic-choice');
    assert.match(await advice.innerText(), /Priorité choisie/);
    assert.match(await advice.innerText(), /Compromis sur/);
    assert.equal(await advice.locator('img, script').count(), 0, 'compromis interprété comme HTML');
    assert.equal(await page.locator('#filter-priority img, #filter-priority script').count(), 0,
                 'libellé de critère interprété comme HTML');
    assert.match(await advice.innerText(), /<img src=x onerror=alert\(1\)>/);
    assert.match(await select.textContent(), /<script>alert\(4\)<\/script>/);
    // GET natif : le contrôle et le bouton vivent dans le formulaire existant
    const form = page.locator('form[action$="#filters"]');
    assert.equal(await form.getAttribute('method'), 'get');
    assert.equal(await form.locator('select#filter-priority, button[type="submit"]').count(), 2);
    // Clavier : la priorité puis le bouton sont atteignables dans l'ordre natif
    const reached = [];
    for (let press = 0; press < 120 && reached.length < 2; press++) {
      await page.keyboard.press('Tab');
      const target = await page.evaluate(() => document.activeElement?.id === 'filter-priority' ? 'filter-priority'
        : document.activeElement?.textContent?.trim() === 'Appliquer' ? 'Appliquer' : '');
      if (target && !reached.includes(target)) reached.push(target);
    }
    assert.deepEqual(reached, ['filter-priority', 'Appliquer']);
    mkdirSync('reports/privacy-browser', {recursive: true});
    await page.screenshot({path: 'reports/privacy-browser/priority-mobile.png', fullPage: true});
  } finally {await context.close();}
  // Sans JavaScript, le choix reste lisible et soumissible
  const off = await browser.newContext({javaScriptEnabled: false, viewport: {width: 390, height: 844}});
  try {
    const page = await off.newPage(); page.setDefaultTimeout(3000);
    await page.goto(origin + '/render/results_priority');
    assert.equal(await page.locator('#filter-priority').count(), 1);
    assert.equal(await page.locator('#filter-priority').inputValue(), 'duration');
    assert.equal(await page.locator('form[action$="#filters"] button[type="submit"]').isVisible(), true);
    assert.match(await page.locator('.economic-choice').innerText(), /Compromis sur/);
  } finally {await off.close();}
  assert.deepEqual(failures, []);
});

test('pre-launch summary: synthesis, single launch button as confirmation, models table, 390 px in both themes', async () => {
  for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({viewport: {width: 390, height: 844}, colorScheme});
    try {
      const page = await context.newPage(); page.setDefaultTimeout(3000);
      const failures = [];
      page.on('pageerror', error => failures.push(error.message));
      page.on('console', message => {if (/Content Security Policy/.test(message.text())) failures.push(message.text());});
      await page.goto(origin + '/render/launch');
      await page.evaluate(() => document.fonts.ready);
      const main = await page.locator('main').innerText();
      // Prévision et réservation affichées séparément, jamais additionnées
      assert.match(main, /2 modèles · réponses estimées : non estimable · réservé pour l’évaluation : 0,30 USD · crédit restant : 18,5 USD/, colorScheme);
      assert.equal(await page.locator('main input[type="checkbox"]').count(), 0, `${colorScheme} case à cocher`);
      assert.equal(await page.locator('main button:not(.sec)').count(), 1, `${colorScheme} un seul bouton principal`);
      assert.equal(await page.locator('form[action$="/start"] input[name="confirm"]').getAttribute('value'), 'yes');
      assert.equal(await page.evaluate(() => {
        const synthesis = [...document.querySelectorAll('main p')].find(p => p.textContent.includes('réponses estimées'));
        const nodes = [synthesis, document.querySelector('form[action$="/start"] button'),
                       [...document.querySelectorAll('main a')].find(a => a.textContent === 'Modifier la sélection'),
                       document.querySelector('.table-scroll table')];
        return nodes.every(Boolean) && nodes.every((node, i) => !i || nodes[i - 1].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING);
      }), true, `${colorScheme} ordre synthèse, bouton, lien, tableau`);
      assert.match(await page.locator('.table-scroll table').innerText(), /adapté/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${colorScheme} débordement`);
      assert.deepEqual(await page.evaluate(contrastFailures), [], colorScheme);
      let focused = false;
      for (let press = 0; press < 60 && !focused; press++) {
        await page.keyboard.press('Tab');
        focused = await page.evaluate(() => document.activeElement.textContent === 'Lancer le benchmark');
      }
      assert.ok(focused, `${colorScheme} bouton inatteignable au clavier`);
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none', `${colorScheme} focus invisible`);
      mkdirSync('reports/privacy-browser', {recursive: true});
      await page.screenshot({path: `reports/privacy-browser/launch-summary-${colorScheme}-mobile.png`, fullPage: true});
      assert.deepEqual(failures, []);
    } finally {await context.close();}
  }
});

test('an expired launch wait claims neither failure nor success and only offers to consult the campaign', async () => {
  const context = await browser.newContext({viewport: {width: 390, height: 844}});
  try {
    const page = await context.newPage(); page.setDefaultTimeout(3000);
    const failures = [];
    page.on('pageerror', error => failures.push(error.message));
    const sent = posts.length;
    followupReplies = ['followup'];
    await page.goto(origin + '/render/launch_timeout');
    assert.equal(await page.locator('h1').textContent(), 'Réponse non reçue à temps');
    const alert = await page.getByRole('alert').textContent();
    assert.match(alert, /rien n’indique qu’il a échoué/);
    assert.doesNotMatch(await page.locator('main').innerText(), /indisponible|n’a pas abouti|Lancement enregistré/);
    assert.equal(await page.locator('main form').count(), 0, 'aucun renvoi proposé');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'débordement');
    // La consultation est une lecture : elle ouvre le suivi de la même campagne sans rien envoyer
    await page.getByRole('link', {name: 'Consulter l’état du lancement'}).click();
    await page.waitForURL(origin + '/preparation/dossiers/d1/campaigns/c1/conditions');
    assert.equal(await page.locator('h1').textContent(), 'Benchmark en cours');
    assert.equal(posts.length, sent, 'consultation émise en POST');
    assert.deepEqual(failures, []);
  } finally {await context.close();}
});

test('benchmark followup survives an isolated failure, stops after three in a row, then opens the results', async () => {
  const context = await browser.newContext();
  try {
    const failures = [];
    let page;
    const requests = [];
    const start = async (view = 'followup') => {
      await page?.close();
      followupReplies = [];
      requests.length = 0;
      page = await context.newPage(); page.setDefaultTimeout(5000);
      page.on('request', request => {if (request.resourceType() === 'fetch') requests.push(request);});
      page.on('pageerror', error => failures.push(error.message));
      page.on('console', message => {if (/Content Security Policy/.test(message.text())) failures.push(message.text());});
      await page.clock.install();
      await page.goto(origin + '/render/' + view);
      // Horloge arrêtée juste après le chargement : seuls les délais avancés ici déclenchent un rafraîchissement
      await page.clock.pauseAt(await page.evaluate(() => Date.now()) + 1000);
    };
    const tick = async (delay, reply, marker) => {
      followupReplies.push(reply);
      const seen = page.waitForEvent('console', message => message.text().startsWith(marker));
      await page.clock.runFor(delay);
      await seen;
    };
    const running = () => page.locator('#preparation-progress progress').isVisible();
    await start();
    assert.equal(await page.locator('h1').textContent(), 'Benchmark en cours');
    // Une zone `aria-live` réécrite à l'identique serait annoncée de nouveau : rien n'est remplacé sans changement
    await page.evaluate(() => {
      window.statusMutations = 0;
      new MutationObserver(records => {window.statusMutations += records.length;})
        .observe(document.getElementById('campaign-status'), {childList: true, subtree: true, characterData: true});
    });
    await tick(3000, 503, 'FOLLOWUP_UNAVAILABLE');
    assert.equal(await running(), true, 'suivi arrêté par un échec isolé');
    // Réessai après 8 s ; un succès remet le compte d'échecs à zéro
    await tick(8000, 'followup', 'FOLLOWUP_ACTIVE');
    assert.equal(await page.evaluate(() => window.statusMutations), 0, 'statut remplacé sans changement');
    await tick(4000, 503, 'FOLLOWUP_UNAVAILABLE');
    await tick(8000, 503, 'FOLLOWUP_UNAVAILABLE');
    assert.equal(await running(), true, 'suivi arrêté après deux échecs');
    await tick(16000, 503, 'FOLLOWUP_UNAVAILABLE');
    assert.equal(await running(), false, 'suivi actif après trois échecs consécutifs');
    assert.match(await page.locator('#preparation-progress [role="status"]').textContent(), /La mise à jour automatique s’est interrompue/);
    assert.equal(await page.getByRole('link', {name: 'Actualiser le suivi'}).isVisible(), true);
    // Plus aucun travail en cours : la page bascule vers les résultats
    await start();
    await tick(3000, 'followup_done', 'FOLLOWUP_COMPLETE');
    await page.waitForURL(origin + '/preparation/dossiers/d1/campaigns/c1');
    // Préparation : lecture seule, arrêt sur saisie ou pause, puis navigation quand le travail se termine
    for (const action of ['input', 'pause', 'complete']) {
      await start('progress-waiting-none-False');
      if (action === 'input') await page.locator('body').dispatchEvent('input');
      if (action === 'pause') await page.getByRole('button', {name: 'Arrêter la mise à jour automatique'}).click();
      if (action !== 'complete') {
        await page.clock.runFor(30000);
        assert.equal(requests.length, 0);
        assert.equal(await running(), false);
        assert.equal(await page.locator('#preparation-progress button').isVisible(), false);
        assert.match(await page.locator('#preparation-progress [role="status"]').textContent(), /arrêtée/);
      } else {
        await tick(3000, 'progress-waiting-none-False', 'FOLLOWUP_ACTIVE');
        assert.equal(await running(), true);
        await tick(4000, 'progress-clarification-none-False', 'FOLLOWUP_COMPLETE');
        await page.waitForURL(origin + '/preparation/dossiers/d1');
        assert.equal(requests.length, 2);
        for (const request of requests) {
          assert.equal(request.method(), 'GET');
          assert.equal(request.postData(), null);
          assert.equal(request.url(), origin + '/preparation/dossiers/d1');
        }
      }
    }
    await start();
    await tick(3000, 'followup-stopped', 'FOLLOWUP_COMPLETE');
    await page.waitForURL(origin + '/preparation/dossiers/d1/campaigns/c1/conditions');
    assert.deepEqual(failures, []);
  } finally {followupReplies = []; await context.close();}
});

test('after launch, archive errors reach the case archive status, visible at 390 px without moving focus', async () => {
  fixture();
  const context = await browser.newContext({viewport: {width: 390, height: 844}});
  try {
    const page = await pageFor(context);
    const failures = [];
    page.on('pageerror', error => failures.push(error.message));
    mode = 'archiveDown';
    await page.goto(origin + '/render/launched');
    const archiveStatus = page.locator('[data-privacy-controls] > [data-privacy-status]');
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] > [data-privacy-status]')
      .textContent.includes('Le serveur n’a pas pu envoyer ce cas'));
    // Le statut du consentement replié garde son propre message
    assert.equal(await page.locator('.privacy-consent [data-privacy-status]').textContent(), 'Vous ne partagez pas cet exemple.');
    assert.equal(await archiveStatus.isVisible(), true);
    assert.equal(await page.evaluate(() => document.activeElement === document.body), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(failures, []);
  } finally {mode = 'normal'; await context.close();}
});

test('a silent archive server is abandoned after 15 s with the existing message', async () => {
  fixture();
  const context = await browser.newContext();
  try {
    const page = await pageFor(context);
    // Délai réel raccourci dans la page ; la durée demandée par le script est relevée
    await page.addInitScript(() => {
      const timeout = AbortSignal.timeout.bind(AbortSignal);
      window.requestedTimeouts = [];
      AbortSignal.timeout = delay => {window.requestedTimeouts.push(delay); return timeout(50);};
    });
    mode = 'archiveHang';
    await page.goto(origin + '/render/launched');
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] > [data-privacy-status]')
      .textContent.includes('Le serveur n’a pas pu envoyer ce cas'));
    assert.equal(await page.evaluate(() => window.requestedTimeouts.every(delay => delay === 15000)
      && window.requestedTimeouts.length >= 1), true);
  } finally {mode = 'normal'; release?.(); await context.close();}
});

test('without JavaScript the case archive status keeps its static text', async () => {
  const context = await browser.newContext({javaScriptEnabled: false});
  try {
    const page = await context.newPage(); page.setDefaultTimeout(3000);
    await page.goto(origin + '/render/launched');
    assert.equal(await page.locator('[data-privacy-controls] > [data-privacy-status]').textContent(),
      'Vos copies s’affichent ici quand JavaScript est activé.');
    assert.equal(await page.locator('.privacy-consent [data-privacy-status]').textContent(), 'Vous ne partagez pas cet exemple.');
  } finally {await context.close();}
});

test('access and launch screens preserve available actions, estimates and refusal reasons', async () => {
  const context = await browser.newContext({javaScriptEnabled: false});
  try {
    const page = await context.newPage();
    for (const [state, label] of [['disconnected', 'Aucune clé enregistrée'], ['connected', 'Clé enregistrée'],
      ['invalid', 'Clé à remplacer'], ['unavailable', 'Enregistrement de clé indisponible']]) {
      await page.goto(origin + '/render/access-' + state);
      assert.ok((await page.textContent('main')).includes(label), state);
      assert.equal(await page.locator('form[action="/preparation/access/key"]').count(), state === 'unavailable' ? 0 : 1);
      if (state === 'disconnected') assert.equal(await page.locator('input[name="return"]').inputValue(), '/preparation/access');
      if (state === 'connected') {
        const text = await page.textContent('main');
        assert.ok(text.includes('Solde restant selon OpenRouter : 12,50 USD'));
        assert.ok(text.includes('Plafond de la clé : 25 USD'));
        assert.ok(text.includes('Retirer la clé de ce navigateur'));
      }
      if (state === 'invalid') assert.ok((await page.textContent('main')).includes('OpenRouter refuse cette clé'));
      await page.goto(origin + '/render/launch-' + state);
      assert.equal(await page.locator('form[action="/preparation/access/start"]').count(), 0);
      if (state === 'unavailable') {
        assert.equal(await page.locator('form[action="/preparation/access/key"], form[action="/preparation/access/disconnect"]').count(), 0);
        assert.ok((await page.textContent('main')).includes('Impossible d’enregistrer une clé'));
      }
      if (state === 'connected') assert.equal(await page.locator('form[action="/preparation/access/disconnect"]').count(), 1);
    }
    for (const [name, text] of [['estimate-known', 'réservé pour l’évaluation : 0,125 USD'],
      ['estimate-unknown', 'réservé pour l’évaluation : inconnu'],
      ['launch-judgment-blocked', 'Budget insuffisant pour l’évaluation']]) {
      await page.goto(origin + '/render/' + name);
      assert.ok((await page.textContent('main')).includes(text), name);
      assert.equal(await page.getByRole('button', {name: 'Lancer le benchmark', exact: true}).count(), 0);
      assert.equal(await page.locator('script').count(), 0);
      assert.equal(await page.locator('[id="cap_usd"], form[action$="/cap"]').count(), 0);
    }
    await page.goto(origin + '/render/estimate-known');
    assert.ok((await page.textContent('main')).includes('le service d’exécution est indisponible. Réessayez dans quelques minutes.'));
    for (const state of ['ready', 'blocked']) {
      await page.goto(origin + '/render/launch-' + state);
      const text = await page.textContent('main');
      assert.equal(text.includes('Tout est prêt.'), state === 'ready');
      assert.equal(text.includes('✓'), false);
      assert.equal(await page.getByRole('button', {name: 'Lancer le benchmark', exact: true}).count(), state === 'ready' ? 1 : 0);
      assert.equal(await page.locator('form[action$="/cap"], input[name="confirm"][required]').count(), 0);
      assert.equal(await page.getByRole('link', {name: 'Modifier la sélection'}).getAttribute('href'), '/preparation/dossiers/d1/configurations');
      if (state === 'ready') {
        assert.ok(text.includes('crédit restant : 12,50 USD'));
        assert.ok(text.includes('réponses estimées : 3,50 USD'));
        assert.equal(await page.locator('input[name="confirm"]').getAttribute('type'), 'hidden');
        assert.equal(await page.locator('input[name="confirm"]').inputValue(), 'yes');
      } else {
        for (const phrase of ['Modèle à choisir de nouveau', 'Ce que la vérification de l’exemple a relevé', 'Quantité à confirmer'])
          assert.ok(text.includes(phrase), phrase);
      }
      assert.ok((await page.locator('form').evaluateAll(forms => forms.map(f => [f.method, f.getAttribute('action')]))).every(
        ([method, action]) => method === 'post' && action.startsWith('/preparation/')));
      assert.equal(/après le paiement|dépasser le plafond|Modifier le plafond/.test(text), false);
    }
    await page.goto(origin + '/render/configurations-selected');
    assert.equal(await page.locator('form#configurations-form').getAttribute('action'), '/preparation/dossiers/d1/configurations');
    assert.equal(await page.locator('input[name="models"]:checked').count(), 2);
    assert.equal(await page.locator('select[name="tier"]').inputValue(), 'high');
    assert.equal(await page.locator('select[name="tier"]').getAttribute('aria-describedby'), 'reasoning-help');
    const effort = page.locator('select[name="effort:modele-a"]');
    assert.equal(await effort.getAttribute('form'), 'configurations-form');
    assert.equal(await effort.inputValue(), 'xhigh');
    assert.equal(await page.locator('select[name="effort:modele-b"]').count(), 0);
    const selection = await page.textContent('main');
    for (const phrase of ['reçoit le niveau le plus proche qu’il propose', 'Ajuster le niveau par modèle',
      'sans garantir qu’elle soit meilleure', 'niveau de raisonnement fixe']) assert.ok(selection.includes(phrase), phrase);
    assert.equal(/Votre sélection|Coût total estimé|Plafond :/.test(selection), false);
    assert.equal(await page.locator('a[href$="/campaigns/d1-c1/conditions"]').count(), 0);
    assert.equal(await page.getByRole('button', {name: 'Continuer', exact: true}).count(), 1);
    for (const [name, text] of [['16-01', '16 septembre 2026 à 12:00:01 UTC'],
      ['16-02', '16 septembre 2026 à 12:00:02 UTC'], ['15-00', '15 septembre 2026 à 12:00:00 UTC']]) {
      await page.goto(origin + '/render/date-' + name);
      assert.ok((await page.textContent('main')).includes(text), text);
    }
    await page.goto(origin + '/render/configurations-empty');
    assert.ok((await page.textContent('main')).includes('Relevé de modèles indisponible'));
    assert.equal(await page.locator('#configurations-form').count(), 0);
  } finally {await context.close();}
});

test('followup distinguishes acquisition, incidents and judgment without reopening launch', async () => {
  const context = await browser.newContext({javaScriptEnabled: false});
  try {
    const page = await context.newPage();
    for (const [state, active, terminal, message] of [
      ['running', true, false, 'Benchmark en cours'], ['queued', true, false, 'Les essais n’ont pas encore démarré'],
      ['stopped', false, false, 'La comparaison s’est arrêtée avant la fin'],
      ['ambiguous', false, false, 'Un essai n’a pas pu être confirmé'],
      ['incident', false, false, 'Un problème technique est survenu'], ['received', false, true, 'Réponses reçues'],
      // #525 : un incident fournisseur non relançable nommé, jamais présenté comme un problème technique anonyme
      ['credit-exhausted', false, false, 'Crédit OpenRouter épuisé pour votre clé : rechargez-le ou relevez son plafond. La comparaison s’est arrêtée'],
      // #537 : l'incident est attribué au canal appelé, jamais à OpenRouter par défaut
      ['official-limited', false, false, 'L’API officielle du fournisseur limite temporairement les demandes. La comparaison s’est arrêtée'],
      // #539 : campagne opérateur, le modèle épuisé n'est pas présenté comme une réponse reçue
      ['operator-exhausted', false, false, 'Modèle A · Niveau de raisonnement non renseigné : pas de réponse exploitable']]) {
      await page.goto(origin + '/render/followup-' + state);
      const text = await page.textContent('main');
      assert.ok(text.includes(message), state);
      assert.equal(await page.locator('#preparation-progress').count(), Number(active), state);
      assert.equal(await page.locator('[data-results-href]').count(), Number(terminal), state);
      assert.equal(await page.locator('main form, #cap_usd').count(), 0);
      assert.equal(await page.locator('script').count() > 0, active || terminal);
      assert.equal(await page.locator('nav.steps a[href="/preparation/dossiers/d1/revisions/2"]').count() > 0, true);
      assert.equal(await page.locator('nav.steps a[href="#exemple"]').count(), 0);
      if (terminal) assert.ok(text.includes('Celles qui n’ont pas encore de verdict attendent leur évaluation.'));
      // #541 : la ligne de compte ne présente pas le modèle épuisé comme une réponse reçue
      if (state === 'operator-exhausted') assert.ok(text.includes('Réponses reçues : 0 sur 1.'), text);
    }
    for (const status of ['NOT_STARTED', 'WAITING', 'RUNNING', 'COMPLETE', 'BLOCKED']) {
      await page.goto(origin + '/render/judgment-' + status);
      const active = ['WAITING', 'RUNNING'].includes(status), ready = status === 'COMPLETE';
      const text = await page.textContent('main');
      assert.equal(await page.locator('#preparation-progress').count(), Number(active));
      assert.equal(await page.locator('[data-results-href]').count(), Number(ready));
      assert.ok(text.includes('Réponses reçues : 1 sur 1'));
      assert.ok(text.includes(`Réponses évaluées : ${Number(ready)} sur 1`));
      assert.equal(await page.locator('form[action$="/evaluate"]').count(), Number(status === 'NOT_STARTED'));
      assert.equal(await page.locator('script').count() > 0, active || ready);
      if (status === 'NOT_STARTED') {
        assert.equal(await page.locator('input[name="confirm"]').inputValue(), 'yes');
        assert.equal(await page.locator('input[name="csrf_token"]').inputValue(), 'csrf');
      }
      if (status === 'BLOCKED') assert.ok(text.includes('Budget insuffisant'));
    }
    for (const [state, judgment, active] of [['EMISSION_POSSIBLE', 'None', true], ['INTENT_RECORDED', 'None', true],
      ['EMISSION_POSSIBLE', 'WAITING', true], ['RECEIVED', 'None', false], ['RECEIVED', 'BLOCKED', false]]) {
      await page.goto(origin + '/render/mixed-' + state + '-' + judgment);
      assert.equal(await page.locator('#preparation-progress').count(), Number(active));
      assert.equal((await page.title()).includes('Benchmark en cours'), active);
      assert.equal(await page.locator('script').count() > 0, active);
    }
  } finally {await context.close();}
});

test('external references remain readable, searchable and safe without JavaScript, by keyboard at 390 px', async () => {
  for (const javaScriptEnabled of [false, true]) {
    const context = await browser.newContext({javaScriptEnabled, viewport: {width: 390, height: 844}});
    try {
      const page = await context.newPage(); page.setDefaultTimeout(3000);
      const external = [], dialogs = [], errors = [];
      page.on('dialog', dialog => {dialogs.push(dialog.message()); dialog.dismiss();});
      page.on('pageerror', error => errors.push(error.message));
      await context.route('https://code.example/**', route => {
        external.push({url: route.request().url(), referer: route.request().headers().referer});
        return route.fulfill({status: 200, contentType: 'text/html', body: '<h1>Source fictive</h1>'});
      });
      await page.goto(origin + '/render/home');
      await page.getByRole('navigation', {name: 'Navigation principale'}).getByRole('link', {name: 'Évaluations externes'}).click();
      assert.equal(await page.locator('main h2').count(), 3);
      assert.equal(await page.locator('#category').inputValue(), '');
      assert.equal(await page.locator('#verification').inputValue(), '');
      assert.equal(await page.locator('main script, main img').count(), 0);
      assert.equal(await page.locator('script').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.getByLabel('Rechercher une évaluation').fill('corriger');
      await page.getByLabel('Travail mesuré', {exact: true}).selectOption('coding');
      await page.getByLabel('Vérification éditoriale', {exact: true}).selectOption('verified');
      await page.getByRole('button', {name: 'Rechercher', exact: true}).click();
      assert.equal(await page.locator('main h2').count(), 1);
      assert.equal(await page.locator('#q').inputValue(), 'corriger');
      assert.equal(await page.locator('#category').inputValue(), 'coding');
      assert.equal(await page.locator('#verification').inputValue(), 'verified');
      await page.getByRole('link', {name: 'Effacer la recherche et les filtres'}).click();
      assert.equal(await page.locator('main h2').count(), 3);
      await page.getByLabel('Rechercher une évaluation').fill('introuvable');
      await page.getByRole('button', {name: 'Rechercher', exact: true}).click();
      assert.match(await page.textContent('main'), /Aucune évaluation ne correspond/);
      assert.equal(await page.getByRole('link', {name: 'Décrire mon cas d’usage', exact: true}).getAttribute('href'), '/preparation');
      await page.goto(origin + '/evaluations-externes?q=' + encodeURIComponent('<script>alert(2)</script>'));
      assert.equal(await page.locator('#q').inputValue(), '<script>alert(2)</script>');
      assert.equal(await page.locator('main script').count(), 0);
      await page.goto(origin + '/render/reference-empty');
      assert.match(await page.textContent('main'), /Aucune évaluation externe disponible/);
      await page.goto(origin + '/evaluations-externes/atelier-inconnu');
      assert.match(await page.textContent('main'), /Vérification insuffisante/);
      assert.match(await page.textContent('main'), /Inconnue/);
      await page.goto(origin + '/evaluations-externes/atelier-retire');
      assert.match(await page.textContent('main'), /Source retirée/);
      assert.equal(await page.locator('main a[href^="https:"]').count(), 0);
      await page.goto(origin + '/render/reference-referral');
      const target = page.locator('main a[href="/evaluations-externes/atelier-code"]');
      assert.equal(await target.getAttribute('rel'), 'noreferrer');
      await target.click();
      assert.equal(await page.locator('main h1').textContent(), 'Atelier Code fictif <script>alert(1)</script>');
      assert.equal((await page.textContent('main')).includes('Besoin privé'), false);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      // Tabulation native : atteindre la source officielle, puis suivre le lien au clavier
      let focused = false;
      for (let press = 0; press < 30 && !focused; press++) {
        await page.keyboard.press('Tab');
        focused = await page.evaluate(() => document.activeElement.getAttribute('href') === 'https://code.example/evaluation');
      }
      assert.ok(focused);
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      mkdirSync('reports/461', {recursive: true});
      await page.screenshot({path: 'reports/461/notice-mobile-' + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      await page.keyboard.press('Enter');
      await page.waitForURL('https://code.example/evaluation');
      assert.deepEqual(external, [{url: 'https://code.example/evaluation', referer: undefined}]);
      assert.deepEqual(dialogs, []);
      assert.deepEqual(errors, []);
      // Aide sémantique : information avant envoi, clavier, 390 px et fonctionnement sans JavaScript
      await page.goto(origin + '/evaluations-externes/aide');
      const help = await page.textContent('main');
      for (const value of ['TypeSafe', 'Jev', 'formulation', 'Aucun dossier privé',
                           'aucune information personnelle ou confidentielle',
                           'reste disponible', 'Demander l’aide'])
        assert.ok(help.includes(value), value);
      assert.equal(await page.locator('main script, main img').count(), 0);
      assert.equal(await page.locator('script').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const formulationField = page.getByLabel('Formulation à comparer');
      await formulationField.fill('Corriger un programme fictif et résoudre un calcul inventé');
      assert.equal(await formulationField.inputValue(), 'Corriger un programme fictif et résoudre un calcul inventé');
      await formulationField.focus();
      let submitFocused = false;
      for (let press = 0; press < 5 && !submitFocused; press++) {
        await page.keyboard.press('Tab');
        submitFocused = await page.evaluate(() => document.activeElement.textContent.trim() === 'Demander l’aide');
      }
      assert.ok(submitFocused, 'bouton de l’aide inaccessible au clavier');
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      mkdirSync('reports/462', {recursive: true});
      await page.screenshot({path: 'reports/462/aide-mobile-' + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      await page.keyboard.press('Enter');
      await page.waitForURL('**/evaluations-externes/aide/result');
      const suggestions = await page.textContent('main');
      assert.match(suggestions, /Correspondance forte/);
      assert.match(suggestions, /Correspondance partielle/);
      assert.match(suggestions, /Atelier Code fictif/);
      assert.match(suggestions, /Atelier Maths fictif/);
      assert.match(suggestions, /ne prouve ni adéquation/);
      assert.equal(await page.locator('main details summary').filter({hasText: 'Reçu technique'}).count(), 1);
      assert.equal(await page.locator('main a[href="/evaluations-externes"]').count(), 1);
      assert.equal(await page.locator('main script, main img').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({path: 'reports/462/suggestions-mobile-' + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      await page.goto(origin + '/render/reference_help_unavailable');
      assert.match(await page.textContent('main'), /Aide indisponible/);
      assert.match(await page.textContent('main'), /désactivé/);
      assert.equal(await page.locator('main a[href="/evaluations-externes"]').count(), 1);
      await page.goto(origin + '/render/approved-index');
      const approved = await page.textContent('main');
      for (const name of ['MathArena', 'LiveCodeBench', 'SWE-bench', 'MTEB', 'Artificial Analysis, performances API'])
        assert.ok(approved.includes(name), name);
      assert.equal(/HELM/i.test(approved), false);
      assert.ok(approved.includes('Ayo est responsable de la maintenance de cette sélection.'));
      assert.ok(approved.includes('22 octobre 2026'));
      assert.ok(approved.includes('La disponibilité d’un lien reste distincte de cette vérification.'));
      assert.equal(await page.locator('main script, main img').count(), 0);
      assert.equal(await page.locator('script').count(), 0);
      assert.equal(await page.locator('#category option[value="retrieval"]').textContent(), 'Recherche documentaire');
      assert.equal(await page.locator('#category option[value="api"]').textContent(), 'Performances des services');
      assert.equal(await page.locator('#category option').evaluateAll(nodes => nodes.some(node => /helm/i.test(node.value + node.textContent))), false);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.goto(origin + '/render/approved-retrieval');
      assert.equal(await page.locator('main h2').count(), 1);
      assert.match(await page.textContent('main'), /MTEB/);
      assert.equal((await page.textContent('main')).includes('MathArena'), false);
      assert.equal(await page.locator('#category').inputValue(), 'retrieval');
      await page.goto(origin + '/render/approved-api');
      assert.match(await page.textContent('main'), /Artificial Analysis, performances API/);
      assert.match(await page.textContent('main'), /2\.2\.0/);
      assert.equal((await page.textContent('main')).includes('MTEB'), false);
      assert.equal(await page.locator('#category').inputValue(), 'api');
      assert.equal(await page.locator('#q').inputValue(), 'API');
      await page.goto(origin + '/render/approved-matharena');
      const matharena = await page.textContent('main');
      assert.match(matharena, /Inconnue/);
      assert.match(matharena, /2026-10-22/);
      assert.match(matharena, /aucune version unique retenue/);
      assert.match(matharena, /expected performance/);
      assert.equal(matharena.includes('%'), false);
      const mathLink = page.locator('main a[href="https://matharena.ai/"]');
      assert.ok((await mathLink.getAttribute('rel')).split(/\s+/).includes('noreferrer'));
      assert.ok((await mathLink.getAttribute('rel')).split(/\s+/).includes('noopener'));
      await page.goto(origin + '/render/approved-livecodebench');
      assert.match(await page.textContent('main'), /Version unique inconnue/);
      assert.match(await page.textContent('main'), /n’est pas certifiée/);
      await page.goto(origin + '/render/approved-swe-bench');
      assert.match(await page.textContent('main'), /Multimodal/);
      assert.match(await page.textContent('main'), /Inconnue/);
      await page.goto(origin + '/render/approved-mteb');
      assert.match(await page.textContent('main'), /n’a pas été essayé/);
      assert.equal(await page.locator('main a[href="https://github.com/embeddings-benchmark/mteb"]').count(), 1);
      await page.goto(origin + '/render/approved-artificial-analysis-api');
      const apiNotice = await page.textContent('main');
      assert.match(apiNotice, /2026-03-02/);
      assert.match(apiNotice, /Leur indice global n’entre pas dans Bench-X/);
      assert.equal(apiNotice.includes('%'), false);
      assert.equal(await page.locator('script').count(), 0);
      assert.deepEqual(dialogs, []);
      assert.deepEqual(errors, []);
      assert.deepEqual(await context.cookies(), []);
    } finally {await context.close();}
  }
});

test('an editorial trial stays readable without JavaScript, by keyboard at 390 px', async () => {
  for (const javaScriptEnabled of [false, true]) {
    const context = await browser.newContext({javaScriptEnabled, viewport: {width: 390, height: 844}});
    try {
      const page = await context.newPage();
      page.setDefaultTimeout(3000);
      const dialogs = [], errors = [];
      page.on('dialog', dialog => {dialogs.push(dialog.message()); dialog.dismiss();});
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(origin + '/render/home');
      await page.getByRole('navigation', {name: 'Navigation principale'}).getByRole('link', {name: 'Épreuves', exact: true}).click();
      assert.match(await page.textContent('main'), /Tableau <script>alert\(1\)<\/script>/);
      assert.equal(await page.locator('main script, main img').count(), 0);
      assert.equal(await page.locator('script').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      // Recherche native, filtres combinés, critères visibles et effaçables, sans JavaScript
      const searchField = page.getByLabel('Rechercher une épreuve');
      await searchField.focus();
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      await searchField.fill('note');
      await page.getByLabel('Langue').selectOption('Français');
      await page.getByLabel('Outil').selectOption('Tableur');
      await page.getByRole('button', {name: 'Rechercher'}).click();
      assert.match(page.url(), /q=note/);
      const filtered = await page.textContent('main');
      assert.match(filtered, /Critères actifs/);
      assert.match(filtered, /Français/);
      assert.match(filtered, /Tableur/);
      assert.equal(filtered.includes('Tableau <script>alert(1)</script>'), false);
      assert.equal(filtered.includes('Note <script>alert(4)</script>'), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.getByRole('link', {name: 'Note <script>alert(4)</script>'}).click();
      assert.equal(await page.locator('h1').textContent(), 'Note <script>alert(4)</script>');
      assert.match(await page.textContent('main'), /Retour aux résultats/);
      await page.getByRole('link', {name: 'Retour aux résultats'}).click();
      assert.match(await page.textContent('main'), /Critères actifs/);
      await page.getByRole('link', {name: 'Effacer la recherche et les filtres'}).click();
      assert.equal(new URL(page.url()).search, '');
      assert.equal(await page.getByLabel('Rechercher une épreuve').inputValue(), '');
      assert.match(await page.textContent('main'), /Tableau <script>alert\(1\)<\/script>/);
      await page.getByLabel('Rechercher une épreuve').fill('introuvable');
      await page.getByRole('button', {name: 'Rechercher'}).click();
      const withoutResult = await page.textContent('main');
      assert.match(withoutResult, /Aucune épreuve ne correspond à cette recherche/);
      assert.equal(await page.getByRole('link', {name: 'Décrire mon cas d’usage', exact: true})
        .getAttribute('href'), '/preparation');
      mkdirSync('reports/464', {recursive: true});
      await page.screenshot({path: 'reports/464/recherche-mobile-'
        + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      // Une valeur longue proposée reste sélectionnable et utilisable
      await page.goto(origin + '/epreuves');
      await page.getByLabel('Outil').selectOption(rendered['editorial-long-tool']);
      await page.getByRole('button', {name: 'Rechercher'}).click();
      const longResult = await page.textContent('main');
      assert.match(longResult, /Version longue/);
      assert.match(longResult, /Critères actifs/);
      assert.equal(new URL(page.url()).searchParams.get('outil'), rendered['editorial-long-tool']);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      // Un filtre devenu indisponible reste sélectionné lors d'une nouvelle soumission
      await page.goto(origin + '/render/editorial-unavailable');
      assert.equal(await page.getByLabel('Outil').inputValue(), 'Tableur retiré');
      assert.match(await page.locator('option[value="Tableur retiré"]').textContent(), /indisponible/);
      await page.getByLabel('Rechercher une épreuve').fill('note');
      await page.getByRole('button', {name: 'Rechercher'}).click();
      const retained = await page.textContent('main');
      assert.match(retained, /Critères actifs/);
      assert.match(retained, /Tableur retiré \(indisponible\)/);
      assert.match(retained, /Aucune épreuve ne correspond à cette recherche/);
      assert.equal(retained.includes('Version au tableur retiré'), false);
      assert.equal(new URL(page.url()).searchParams.get('outil'), 'Tableur retiré');
      assert.equal(await page.getByLabel('Outil').inputValue(), 'Tableur retiré');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({path: 'reports/464/filtre-indisponible-mobile-'
        + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      await page.getByRole('link', {name: 'Effacer la recherche et les filtres'}).click();
      assert.equal(await page.getByLabel('Outil').inputValue(), '');
      const cleared = await page.textContent('main');
      assert.match(cleared, /Note <script>alert\(4\)<\/script>/);
      assert.equal(cleared.includes('Version au tableur retiré'), false);
      await page.goto(origin + '/epreuves');
      await page.getByRole('link', {name: 'Tableau <script>alert(1)</script>'}).click();
      assert.equal(await page.locator('h1').textContent(), 'Tableau <script>alert(1)</script>');
      const text = await page.textContent('main');
      assert.match(text, /Qualification absente/);
      assert.match(text, /Aucune preuve de qualification/);
      assert.match(text, /Aucun résultat publié/);
      assert.match(text, /Cinq débits inventés/);
      assert.match(text, /Ne certifie aucune comptabilité/);
      assert.match(text, /Pièce <script>alert\(1\)<\/script>/);
      assert.match(text, /Erreur de fixture <script>alert\(3\)<\/script>/);
      assert.match(text, /Signalement éditorial/);
      assert.equal(text.includes('Qualification réussie'), false);
      assert.equal(await page.locator('main script').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      let focused = false;
      for (let press = 0; press < 40 && !focused; press++) {
        await page.keyboard.press('Tab');
        focused = await page.evaluate(() => document.activeElement?.getAttribute('href')?.endsWith('/pieces/consigne'));
      }
      assert.ok(focused, 'lien de pièce non atteint au clavier');
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      mkdirSync('reports/463', {recursive: true});
      await page.screenshot({path: 'reports/463/epreuve-mobile-' + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      await page.keyboard.press('Enter');
      assert.match(await page.locator('body').innerText(), /Pièce <script>alert\(1\)<\/script>/);
      await page.goto(origin + '/epreuves/tache-fictive/version-1');
      const copyLink = page.getByRole('link', {name: 'Créer mon dossier privé'});
      await copyLink.focus();
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      assert.equal(await page.locator('main script').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.goto(origin + '/render/copy-confirmation');
      const confirmation = await page.textContent('main');
      assert.match(confirmation, /Épreuve source/);
      assert.match(confirmation, /Tableau <script>alert\(1\)<\/script>/);
      assert.match(confirmation, /Erreur signalée <script>alert\(2\)<\/script>/);
      assert.match(confirmation, /Pièces copiées/);
      assert.match(confirmation, /Créer mon dossier privé/);
      assert.equal(await page.locator('main script').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const copyButton = page.getByRole('button', {name: 'Créer mon dossier privé'});
      await copyButton.focus();
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      await page.screenshot({path: 'reports/465/confirmation-mobile-' + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      await page.goto(origin + '/render/copy-dossier');
      const copied = await page.textContent('main');
      assert.match(copied, /Épreuve source/);
      assert.match(copied, /Tableau <script>alert\(1\)<\/script>/);
      assert.match(copied, /Pièce source <script>alert\(1\)<\/script>/);
      assert.match(copied, /Erreur signalée <script>alert\(2\)<\/script>/);
      assert.match(copied, /Sa qualification publiée ne qualifie pas ce dossier/);
      assert.match(copied, /La copie reste consultable sans clé/);
      assert.equal(await page.locator('script[src*="privacy.js"]').count(), 1);
      assert.equal(await page.locator('main script:not([src])').count(), 1);
      assert.equal(await page.locator('main img, main script[src*="alert"]').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      let sourceFocused = false;
      for (let press = 0; press < 40 && !sourceFocused; press++) {
        await page.keyboard.press('Tab');
        sourceFocused = await page.evaluate(() => document.activeElement?.tagName === 'SUMMARY');
      }
      assert.ok(sourceFocused, 'pièce source non atteinte au clavier');
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      mkdirSync('reports/465', {recursive: true});
      await page.screenshot({path: 'reports/465/copie-mobile-' + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      await page.goto(origin + '/render/editorial-help');
      const helpForm = await page.textContent('main');
      assert.match(helpForm, /TypeSafe/);
      assert.match(helpForm, /Données transmises/);
      assert.match(helpForm, /Recherche simple/);
      const formulation = page.getByLabel('Formulation à comparer');
      await formulation.focus();
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      assert.equal(await page.locator('main script').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.goto(origin + '/render/editorial-help-result');
      const helpResult = await page.textContent('main');
      assert.match(helpResult, /Correspondance forte/);
      assert.match(helpResult, /Correspondance partielle/);
      assert.match(helpResult, /Cette appréciation ne prouve ni adéquation ni qualification/);
      assert.match(helpResult, /Résultat attendu/);
      assert.equal(await page.locator('main a[href="/epreuves/tache-fictive/version-1"]').count(), 1);
      assert.equal(await page.locator('main a[href="/epreuves/tache-fictive/version-2"]').count(), 1);
      assert.equal(await page.locator('main script').count(), 0);
      const suggestion = page.locator('main a[href="/epreuves/tache-fictive/version-2"]');
      await suggestion.focus();
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      mkdirSync('reports/467', {recursive: true});
      await page.screenshot({path: 'reports/467/aide-catalogue-mobile-'
        + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      await page.goto(origin + '/render/editorial-help-withdrawn');
      const withdrawnHelp = await page.textContent('main');
      assert.match(withdrawnHelp, /Une suggestion a été retirée/);
      assert.equal(withdrawnHelp.includes('Tableau <script>alert(1)</script>'), false);
      assert.match(withdrawnHelp, /Note <script>alert\(4\)<\/script>/);
      assert.equal(await page.locator('main a[href="/epreuves/tache-fictive/version-1"]').count(), 0);
      assert.equal(await page.locator('main script').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.goto(origin + '/render/editorial-empty');
      assert.match(await page.textContent('main'), /Aucune épreuve publiée/);
      assert.equal(await page.getByRole('link', {name: 'Décrire mon cas d’usage', exact: true}).getAttribute('href'), '/preparation');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.goto(origin + '/render/editorial-withdrawn');
      const withdrawn = await page.textContent('main');
      assert.match(withdrawn, /Cette version est retirée/);
      assert.match(withdrawn, /Aucune nouvelle reprise/);
      assert.match(withdrawn, /Erreur de fixture <script>alert\(3\)<\/script>/);
      assert.equal(withdrawn.includes('Pièce <script>'), false);
      assert.equal(withdrawn.includes('Tableau'), false);
      assert.equal(await page.locator('main a[href*="/pieces/"]').count(), 0);
      assert.equal(await page.locator('script').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(dialogs, []);
      assert.deepEqual(errors, []);
    } finally {await context.close();}
  }
});

test('published results stay readable without JavaScript, by keyboard at 390 px', async () => {
  for (const javaScriptEnabled of [false, true]) {
    const context = await browser.newContext({javaScriptEnabled, viewport: {width: 390, height: 844}});
    try {
      const page = await context.newPage();
      page.setDefaultTimeout(3000);
      const dialogs = [], errors = [];
      page.on('dialog', dialog => {dialogs.push(dialog.message()); dialog.dismiss();});
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(origin + '/render/published-results');
      const main = await page.textContent('main');
      assert.match(main, /Tableau <script>alert\(1\)<\/script>/);
      assert.match(main, /Modèle <script>alert\(1\)<\/script>/);
      assert.match(main, /Préparer un tableau par opération/);
      assert.match(main, /Satisfait/);
      assert.match(main, /Ne satisfait pas/);
      assert.match(main, /Coût observé inconnu pour 1 réponse/);
      assert.match(main, /pièce non publiée/i);
      assert.match(main, /un filtre ne change ni les verdicts ni la population/i);
      assert.match(main, /Réponses évaluées : 2 · essais lancés : 2 sur 2/);
      assert.equal(await page.locator('main script, main img').count(), 0);
      assert.equal(await page.locator('script').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      // Filtres natifs visibles, un seul tableau à une ligne sur la vue filtrée
      assert.equal(await page.locator('#filter-configuration').count(), 1);
      await page.goto(origin + '/render/published-results-filtered');
      const filtered = await page.textContent('main');
      assert.match(filtered, /1 réponse affichée sur 2/);
      assert.equal(await page.locator('.comparison-results > .table-scroll > table > tbody > tr').count(), 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.goto(origin + '/render/published-results');
      // Un dépliant puis la preuve publiée restent utilisables au clavier, sans JavaScript
      let focused = false;
      for (let press = 0; press < 60 && !focused; press++) {
        await page.keyboard.press('Tab');
        focused = await page.evaluate(() => document.activeElement?.tagName === 'SUMMARY');
      }
      assert.ok(focused, 'dépliant non atteint au clavier');
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      await page.getByText('Détail et preuves').first().click();
      // Détail ouvert : la colonne Modèle garde des mots entiers, jamais une lettre par ligne
      const column = await page.evaluate(() => {
        const table = document.querySelector('section.comparison-results > .table-scroll > table');
        const splitWords = node => {
          const range = document.createRange(), lines = [];
          for (let index = 0; index < node.length; index++) {
            range.setStart(node, index);
            range.setEnd(node, index + 1);
            const top = Math.round(range.getBoundingClientRect().top);
            const line = lines.find(item => Math.abs(item.top - top) < 2);
            if (line) line.last = index;
            else lines.push({top, first: index, last: index});
          }
          return lines.slice(1).filter(line => /\p{L}/u.test(node.data[line.first - 1] ?? '')
            && /\p{L}/u.test(node.data[line.first] ?? ''))
            .map(line => node.data.slice(line.first, line.last + 1));
        };
        const header = table.querySelector('thead th');
        const name = table.querySelector('tbody th strong');
        return {head: splitWords(header.firstChild), name: splitWords(name.firstChild),
                width: Math.round(table.querySelector('tbody th').getBoundingClientRect().width),
                characters: name.firstChild.length, scroll: table.parentElement.scrollWidth};
      });
      assert.deepEqual(column.head, [], 'en-tête de la colonne Modèle coupé entre deux lettres : ' + JSON.stringify(column));
      assert.deepEqual(column.name, [], 'nom du modèle coupé entre deux lettres : ' + JSON.stringify(column));
      assert.ok(column.scroll > 390, 'le tableau ne défile plus horizontalement : ' + JSON.stringify(column));
      let proofFocused = false;
      for (let press = 0; press < 40 && !proofFocused; press++) {
        await page.keyboard.press('Tab');
        proofFocused = await page.evaluate(() => document.activeElement?.getAttribute('href')?.endsWith('/pieces/output-a'));
      }
      assert.ok(proofFocused, 'preuve publiée non atteinte au clavier');
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      const proof = page.getByRole('link', {name: /Réponse de Modèle/});
      mkdirSync('reports/466', {recursive: true});
      await page.screenshot({path: 'reports/466/resultats-mobile-' + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      await proof.click();
      assert.match(await page.locator('body').innerText(), /Réponse publique de la fixture/);
      // États de retrait : aucun contenu retiré, retour vers l’épreuve et le catalogue
      await page.goto(origin + '/render/published-results-withdrawn');
      const withdrawn = await page.textContent('main');
      assert.match(withdrawn, /Cette restitution est retirée/);
      assert.equal(withdrawn.includes('Modèle <script>'), false);
      assert.match(withdrawn, /Retour à l’épreuve/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.goto(origin + '/render/published-results-trial-withdrawn');
      const trialWithdrawn = await page.textContent('main');
      assert.match(trialWithdrawn, /Cette épreuve est retirée/);
      assert.equal(trialWithdrawn.includes('Tableau'), false);
      assert.deepEqual(dialogs, []);
      assert.deepEqual(errors, []);
    } finally {await context.close();}
  }
});

test('preparation screens preserve navigation, accessible forms and recoverable states', async () => {
  const context = await browser.newContext({javaScriptEnabled: false});
  try {
    const page = await context.newPage();
    for (const reason of rendered['availability-reasons']) {
      await page.goto(origin + '/render/availability-' + reason);
      assert.equal(await page.locator('#availability').count(), Number(reason !== 'open'), reason);
      if (reason !== 'open') assert.ok((await page.locator('#availability p').first().textContent()).length >= 20, reason);
      assert.equal(await page.locator('form form').count(), 0);
    }
    await page.goto(origin + '/render/availability-access');
    assert.equal(await page.locator('a').first().getAttribute('href'), '#main');
    assert.equal(await page.locator('main').getAttribute('tabindex'), '-1');
    assert.equal(await page.locator('#request').getAttribute('aria-describedby'), 'request-help availability');
    assert.equal(await page.locator('button[form]').getAttribute('form'), 'prepare-case');
    assert.equal(await page.locator('#prepare-case').count(), 1);
    const ordering = await page.textContent('main');
    assert.ok(ordering.indexOf('Enregistrer la clé') < ordering.indexOf('Décrivez une tâche de votre travail'));
    assert.ok(await page.locator('#availability').evaluate(node => Boolean(node.compareDocumentPosition(document.getElementById('openrouter-key')) & Node.DOCUMENT_POSITION_FOLLOWING)));
    assert.deepEqual(await page.evaluate(() => {
      const nodes = [...document.querySelectorAll('[id]')], ids = nodes.map(n => n.id);
      return [ids.length === new Set(ids).size,
        [...document.querySelectorAll('[aria-describedby]')].every(n => n.getAttribute('aria-describedby').split(' ').every(id => ids.includes(id))),
        [...document.querySelectorAll('[tabindex]')].every(n => n.tabIndex <= 0), getComputedStyle(document.body).overflowWrap];
    }), [true, true, true, 'break-word']);
    for (const [name, field, text] of [['request-error', 'request', 'Ce texte est trop court.'], ['message-error', 'message', 'Ce texte est trop long.']]) {
      await page.goto(origin + '/render/' + name);
      assert.ok((await page.locator(`[name="${field}"]`).getAttribute('aria-describedby')).includes(field + '-error'));
      assert.equal(await page.locator('#' + field + '-error').getAttribute('role'), 'alert');
      assert.equal(await page.locator('#' + field + '-error').textContent(), text);
      assert.equal((await page.textContent('main')).split(text).length, 2);
      if (field === 'request') {
        assert.equal(await page.locator('#request').getAttribute('minlength'), '40');
        assert.equal(await page.locator('#request').getAttribute('maxlength'), '1500');
        assert.equal(await page.locator('#request').getAttribute('required'), '');
        assert.ok((await page.textContent('main')).includes('en 40 caractères au moins'));
      }
    }
    await page.goto(origin + '/render/prior-example');
    assert.equal(await page.locator('nav.steps [aria-disabled="true"]').count(), 4);
    for (const text of ['1Besoin', '2Exemple', '3Validation', '4Modèles', '5Résultats'])
      assert.ok((await page.locator('nav.steps').textContent()).includes(text));
    assert.equal(await page.locator('nav.steps a[href="#besoin"]').count(), 1);
    await page.goto(origin + '/render/example-QUALIFIED');
    assert.ok((await page.locator('nav.steps [aria-current="step"]').textContent()).includes('Validation'));
    assert.equal(await page.locator('nav.steps a[href$="/configurations"]').count(), 1);
    await page.goto(origin + '/render/scope');
    assert.ok((await page.textContent('main')).includes('Précisez le travail à comparer'));
    assert.ok((await page.textContent('main')).includes('Aucune comparaison ne peut être lancée à cette étape.'));
    assert.equal((await page.textContent('main')).includes('Choisir les modèles'), false);
    assert.equal(await page.locator('a[href="#exemple"], a[href="#validation"], form[action$="/validation"], #openrouter-key').count(), 0);
    for (const category of ['math', 'coding', 'other']) {
      await page.goto(origin + '/render/out-of-scope-' + category);
      const text = await page.textContent('main');
      assert.ok(text.includes('Bench-X ne peut pas comparer cette tâche'));
      assert.ok(text.includes('Décrire un autre cas d’usage'));
      assert.equal(/Envoyer ma réponse|Choisir les modèles|équipe Bench-X|n’est pas encore/.test(text), false);
      assert.equal(await page.locator('form[action$="/validation"], #availability').count(), 0);
      assert.equal(await page.locator('a[href="/evaluations-externes/matharena"]').count(), Number(category === 'math'));
      assert.equal(await page.locator('main a[href="/evaluations-externes"]').count(), 1);
      assert.equal(await page.locator('a[href="/evaluations-externes/livecodebench"]').count(), Number(category === 'coding'));
    }
    for (const reason of ['open', 'interrupted', 'restore']) {
      await page.goto(origin + '/render/suspended-' + reason);
      const text = await page.textContent('main');
      assert.equal(text.includes('Renvoyez votre message'), reason === 'open');
      assert.equal(await page.getByRole('button', {name: 'Envoyer ma réponse', exact: true}).isDisabled(), reason !== 'open');
      assert.equal(text.includes('équipe'), false);
      if (reason === 'open') assert.ok(text.includes('Incident chez OpenRouter ou chez le fournisseur du modèle.'));
      if (reason === 'interrupted') assert.ok(text.includes('cette opération sera close automatiquement'));
      if (reason === 'restore') assert.ok(text.includes('Service momentanément indisponible'));
    }
    await page.goto(origin + '/render/example-PENDING');
    assert.ok((await page.textContent('main')).includes('La vérification de l’exemple n’a pas pu démarrer.'));
    assert.equal((await page.textContent('main')).includes('Approbation'), false);
    await page.goto(origin + '/render/example-BLOCKED');
    assert.ok((await page.textContent('main')).includes('Exemple à revoir avant comparaison'));
    assert.equal(await page.locator('img[src="x"]').count(), 0);
    assert.equal((await page.textContent('main')).split('<img src=x onerror="alert(1)">Correction requise').length, 2);
    await page.goto(origin + '/render/stopped-history');
    assert.ok((await page.textContent('main')).includes('Aucun nouvel essai ne peut être lancé pour cette comparaison.'));
    assert.ok((await page.textContent('main')).includes('Service momentanément indisponible'));
    for (const [stage, status, prior, pending] of [['waiting', 'none', false, true], ['waiting', 'none', true, false],
      ['clarification', 'none', false, false], ['suspended', 'none', false, false], ['preview', 'PENDING', false, true],
      ['preview', 'BLOCKED', false, false], ['preview', 'QUALIFIED', false, false]]) {
      await page.goto(origin + `/render/progress-${stage}-${status}-${prior ? 'True' : 'False'}`);
      assert.equal(await page.locator('#preparation-progress').count(), Number(pending));
      assert.equal(await page.locator('progress').count(), Number(pending));
      if (pending) {
        assert.equal(await page.locator('#availability').count(), 1);
        assert.equal(await page.locator('aside#availability, progress[value]').count(), 0);
        assert.ok((await page.textContent('main')).includes('Cette page se met à jour d’elle-même si JavaScript est activé'));
      }
    }
  } finally {await context.close();}
});

test('privacy forms preserve consent scope, purpose tokens and expired states', async () => {
  const context = await browser.newContext({javaScriptEnabled: false});
  try {
    const page = await context.newPage();
    await page.goto(origin + '/render/example');
    const text = await page.textContent('main');
    assert.ok(text.indexOf('Les pièces de l’exemple') < text.indexOf('Oui, c’est le travail à tester'));
    assert.ok(text.indexOf('Oui, c’est le travail à tester') < text.indexOf('conserver cet exemple pendant 6 mois'));
    for (const phrase of ['historique local', 'votre besoin, vos messages et les versions successives',
      'Cette copie contient plus que la contribution', 'a besoin de JavaScript', 'sans lui, seule la contribution',
      'efface la copie de ce cas gardée dans ce navigateur', 'vos cas ne sont plus accessibles après 7 jours', 'Votre clé ne l’est plus après 30 jours'])
      assert.ok(text.includes(phrase), phrase);
    assert.equal(/Clé retirée après|11 jours|conserve votre copie locale/.test(text), false);
    assert.equal(await page.locator('script[src="/preparation/privacy.js"]').count(), 1);
    assert.ok(await page.locator('script:not([src])').count() > 0);
    await page.goto(origin + '/render/privacy-listing');
    const activity = page.locator('[data-privacy-activity]');
    assert.equal(await activity.getAttribute('data-dossier-id'), 'd1');
    assert.equal(await activity.getAttribute('data-content-version'), '4');
    assert.equal(await activity.getAttribute('data-csrf-token'), 'privacy-token');
    assert.equal(await page.locator('form[action="/preparation/access/key"] input[name="csrf_token"]').inputValue(), 'csrf');
    assert.equal(await page.locator('[data-privacy-post="contribution"]').count(), 0);
    assert.equal(await page.locator('[data-privacy-post="delete"]').count(), 1);
    for (const name of ['current', 'expired', 'prior']) {
      await page.goto(origin + '/render/consent-' + name);
      const box = page.locator('[data-privacy-post="contribution"] input[type="checkbox"]');
      assert.equal(await box.count(), Number(name !== 'prior'));
      if (name === 'prior') continue;
      assert.equal(await box.isChecked(), false);
      assert.equal(await box.isDisabled(), name === 'expired');
      assert.equal(await page.locator('[data-privacy-post="contribution"] input[name="revision"]').inputValue(), '5');
      assert.equal(await page.locator('[data-privacy-post="contribution"] input[name="example_revision"]').inputValue(), '3');
    }
    for (const [state, year, label, disabled] of [['active', '2099', 'Active', false], ['withdrawn', '2099', 'Retirée', true],
      ['expired', '2000', 'Expirée', true], ['active', '2000', 'Expirée', true], ['UNKNOWN_STATE', '2099', 'État inconnu', true]]) {
      await page.goto(origin + `/render/contribution-${state}-${year}`);
      const text = await page.textContent('main');
      for (const phrase of [label, '18 septembre 2026', 'arrête seulement la contribution concernée',
        'L’historique local continue', 'les copies déjà gardées dans ce navigateur restent']) assert.ok(text.includes(phrase), phrase);
      assert.equal(text.includes('Contribution opaque-identifier'), false);
      assert.equal(await page.locator('[data-privacy-activity]').count(), 0);
      const form = page.locator('form[action="/preparation/contributions/opaque-identifier/withdraw"]');
      assert.equal(await form.locator('button').isDisabled(), disabled);
      assert.equal(await form.locator('input[name="csrf_token"]').inputValue(), 'purpose-only');
    }
    for (const name of ['safe', 'unsafe']) {
      await page.goto(origin + '/render/bootstrap-' + name);
      assert.equal(await page.locator('form[action="/preparation/session/open"]').count(), 1);
      assert.equal(await page.locator('input[name="csrf_token"]').count(), 0);
      assert.equal(await page.locator('input[name="return_path"]').inputValue(), '/preparation');
      assert.equal((await page.content()).includes('//evil.test'), false);
    }
    await page.goto(origin + '/render/data');
    assert.equal(await page.locator('[data-privacy-history]').count(), 1);
    assert.equal(await page.locator('[data-privacy-activity]').count(), 0);
  } finally {await context.close();}
});

test('public and private pages retain legal information and exact release identity', async () => {
  const context = await browser.newContext({javaScriptEnabled: false});
  try {
    const page = await context.newPage();
    for (const name of [...LEGAL, 'home', 'data', 'listing', 'example', 'request-error', 'publication-footer']) {
      await page.goto(origin + '/render/' + name);
      for (const href of ['/mentions-legales', '/cgu', '/confidentialite'])
        assert.equal(await page.locator(`footer a[href="${href}"]`).count() > 0, true, name + href);
      assert.equal(await page.locator('a[href="/preparation/privacy"]').count(), 0);
      if (LEGAL.includes(name)) {
        assert.equal(await page.locator('script, [data-privacy-activity], [aria-current], blockquote').count(), 0);
        assert.equal(await page.locator('h1').count(), 1);
        assert.equal(await page.title(), (await page.locator('h1').textContent()) + ' · Bench-X');
        assert.equal(/\[\[|Formulation à retenir|N’annoncez pas|aucune route ne les expose|annoncez une fermeture d’accès/.test(await page.textContent('body')), false);
      }
    }
    await page.goto(origin + '/render/confidentialite');
    const privacy = await page.textContent('main');
    for (const term of ['Dernière mise à jour : 29 septembre 2026', 'OpenRouter, Inc., établi aux États-Unis',
      'article 45 du RGPD', 'article 46 du RGPD', 'Nous ne pouvons pas garantir', 'art. 6.1.b', 'art. 6.1.f',
      'portabilité', 'CNIL', '3 place de Fontenoy', 'benchmark_session', 'désactivé par défaut', 'deux effets',
      'contribution pour cet exemple', 'votre besoin, vos messages et les révisions', 'que la contribution exclut',
      'retire la copie de contribution conservée sur le serveur', 'ne concerne que la contribution',
      'n’arrête pas l’historique local', 'restent dans ce navigateur']) assert.ok(privacy.includes(term), term);
    assert.ok((await page.getByRole('row').filter({hasText: 'Historique local dans votre navigateur'}).textContent()).includes('Votre consentement (art. 6.1.a)'));
    assert.equal(/N’ÉCRIRE QU’APRÈS|N'ÉCRIRE QU'APRÈS/.test(privacy), false);
    assert.equal(await page.locator('a[href="/preparation/data"]').count() > 0, true);
    await page.goto(origin + '/render/cgu');
    assert.ok((await page.textContent('main')).includes('Dernière mise à jour : 29 septembre 2026'));
    await page.goto(origin + '/render/mentions-legales');
    assert.ok((await page.textContent('main')).includes('AGPL-3.0-only'));
    for (const [name, label, target] of [['checkout', 'Révision : abcdef0', ''],
      ['release', 'Version : v0.2.0 (abcdef0)', '/tree/abcdef0123456789'], ['unknown', null, '']]) {
      await page.goto(origin + '/render/identity-' + name);
      const text = await page.textContent('body');
      assert.equal(await page.locator('a.source').getAttribute('href'), 'https://github.com/eliasprunaire/benchmark-lab-x' + target);
      if (label) assert.ok(text.includes(label));
      else assert.equal(text.includes('v0.2.0'), false);
      assert.equal(/v0.1.0|Version du site|Aucune ressource externe chargée|sha256/.test(text), false);
      assert.equal(await page.locator('a[aria-current="page"]').textContent(), 'Accueil');
      assert.equal(await page.locator('a[href="/index.html"]').count(), 0);
      assert.ok(text.includes('Un seul manquement suffit'));
      assert.equal(text.includes('Comparaisons publiées'), false);
    }
    await page.goto(origin + '/render/listing');
    assert.equal(await page.locator('a[aria-current="page"]').textContent(), 'Mes cas d’usage');
    assert.equal(await page.locator('ul.dossiers a[href="/preparation/dossiers/d1"]').count(), 1);
    for (const name of ['waiting', 'honeypot']) {
      await page.goto(origin + '/render/' + name);
      assert.ok((await page.title()).startsWith('Demande enregistrée'));
      assert.equal(await page.locator('.state.wait').count(), 1);
      assert.ok((await page.textContent('main')).includes('Votre envoi est bien enregistré'));
      assert.ok((await page.textContent('main')).includes('Suivre mon cas d’usage'));
      assert.equal(await page.locator('#openrouter-key').count(), 0);
    }
  } finally {await context.close();}
});
