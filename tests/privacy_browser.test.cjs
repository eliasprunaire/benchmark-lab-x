// Run: PLAYWRIGHT_MODULE=/path/to/playwright/index.js node --test tests/privacy_browser.test.cjs
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const {createServer} = require('node:http');
const {readFileSync, mkdirSync} = require('node:fs');
const {execFileSync} = require('node:child_process');
const {createHash, randomBytes} = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server, browser, origin, rendered;
// Scripts autres que celui des filtres des catalogues, constante du serveur : un contenu hostile n'en crée jamais
const scriptsEtrangers = (page, zone = 'script') => page.locator(zone)
  .evaluateAll((scripts, permis) => scripts.filter(script => script.src || script.textContent !== permis).length, rendered.filtres_script);
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
    reference_queries = [('', '', ''), ('corriger', '', ''), ('corriger', 'coding', ''), ('corriger', 'coding', 'verified'), ('introuvable', '', ''),
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
    editorial_queries = ({}, {'q': 'note'}, {'q': 'note', 'langue': 'Français'}, {'q': 'note', 'langue': 'Français', 'outil': 'Tableur'},
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
                  'published-results-repeated': views.render(results_view(repeated=True), '').decode(),
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
                  'progress_script': views.PREPARATION_PROGRESS_SCRIPT, 'filtres_script': views.FILTRES_SCRIPT}))
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
      // Comme le serveur Python : le script des filtres, autorisé par son empreinte, relit ces pages
      res.setHeader('Content-Security-Policy', `default-src 'none'; script-src 'sha256-${createHash('sha256').update(rendered.filtres_script).digest('base64')}'; connect-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'`);
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
      // Comme le serveur Python : le script des filtres, autorisé par son empreinte, relit ces pages
      res.setHeader('Content-Security-Policy', `default-src 'none'; script-src 'sha256-${createHash('sha256').update(rendered.filtres_script).digest('base64')}'; connect-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'`);
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
    // Replié par défaut : le statut « Facultatif · non partagé » se lit sans l'ouvrir
    assert.match(await page.locator('.privacy-consent summary').first().textContent(), /Partager cet exemple\s*Facultatif · non partagé/);
    await openConsent(page);
    assert.equal(await page.getByRole('checkbox').isChecked(), false);
    assert.equal(await page.getByRole('checkbox').isEnabled(), true);
    mkdirSync('reports/privacy-browser', {recursive: true});
    await page.locator('.privacy-consent').screenshot({path: 'reports/privacy-browser/consent-desktop.png'});
    // Saving the unchecked box is a choice against both effects
    await Promise.all([page.waitForEvent('load'), page.getByRole('button', {name: 'Enregistrer mon choix'}).click()]);
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent.includes('désactivé'));
    assert.deepEqual(await databases(), []);
    await openConsent(page);
    await page.getByRole('checkbox').check();
    await Promise.all([page.waitForEvent('load'), page.getByRole('button', {name: 'Enregistrer mon choix'}).click()]);
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent.includes('Copie gardée dans ce navigateur.'));
    assert.deepEqual(posts.filter(post => post.path.endsWith('/contribution')).map(post => [post.path, post.body.enabled]), [
      ['/preparation/dossiers/d1/contribution', false], ['/preparation/dossiers/d1/contribution', true]]);
    assert.deepEqual(await databases(), ['bench-x-history']);
    // Cartes de Mes cas d'usage : aucun message par défaut, puis un message visible après la copie, réussie ou non
    await page.goto(origin + '/render/privacy-listing');
    const carte = id => page.locator('.ge-cas [data-dossier-id="' + id + '"]');
    await carte('d1').locator('[data-privacy-action="archive"]').waitFor();
    assert.deepEqual(await page.locator('.ge-cas [data-privacy-status]').allTextContents(), ['', '']);
    await carte('d1').locator('[data-privacy-action="archive"]').click();
    const statut1 = carte('d1').locator('[data-privacy-status]');
    await page.waitForFunction(() => document.querySelector('.ge-cas [data-dossier-id="d1"] [data-privacy-status]').textContent !== ''
      && !document.querySelector('.ge-cas [data-dossier-id="d1"] [data-privacy-action="archive"]').disabled);
    assert.match(await statut1.textContent(), /Copie gardée dans ce navigateur|déjà la version la plus récente/);
    assert.equal(await statut1.isVisible(), true, 'réussite visible à l’écran');
    // d2 n'a pas de copie servie par la fixture : l'échec se lit à l'écran, pas seulement par un lecteur d'écran
    await carte('d2').locator('[data-privacy-action="archive"]').click();
    const statut2 = carte('d2').locator('[data-privacy-status]');
    await page.waitForFunction(() => {
      const texte = document.querySelector('.ge-cas [data-dossier-id="d2"] [data-privacy-status]').textContent;
      return texte !== '' && texte !== 'Copie en cours dans ce navigateur…';
    });
    assert.doesNotMatch(await statut2.textContent(), /Copie gardée/);
    assert.equal(await statut2.isVisible(), true, 'échec visible à l’écran');
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
    await openConsent(page);
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
    await openConsent(page);
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
    // La suppression passe par sa page de confirmation, ouverte avec la copie déjà gardée
    await page.goto(origin + '/render/case-delete');
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent.includes('version la plus récente'));
    fixture(2); mode = 'delay';
    const waiting = new Promise(resolve => {arrived = resolve;});
    pending = observer.evaluate(() => historyStore.archive('d1', 2).catch(error => error.message));
    await waiting;
    let observed;
    await page.route('**/preparation/dossiers/d1/delete', async route => {
      observed = await observer.evaluate(async () => ({record: await historyStore.get('d1'), state: await historyStore.state('d1')}));
      await route.fulfill({status: 503, contentType: 'application/json', body: '{}'});
    });
    const deletionResponse = page.waitForResponse(response => response.url().endsWith('/d1/delete'));
    await page.locator('form[data-privacy-post="delete"] button').click();
    await deletionResponse;
    assert.deepEqual(observed, {record: null, state: {enabled: false, chosen: true}});
    await page.waitForFunction(() => document.querySelector('form[data-privacy-post="delete"] [data-privacy-status]')?.textContent.includes('n’est pas confirmée'));
    const status = await page.locator('form[data-privacy-post="delete"] [data-privacy-status]').textContent();
    assert.match(status, /Copie de ce navigateur effacée/);
    assert.match(status, /La suppression sur le serveur, contribution comprise, n’est pas confirmée/);
    assert.equal(page.url(), origin + '/render/case-delete');
    // L'échec du serveur laisse le formulaire actif : annuler comme réessayer restent possibles
    assert.equal(await page.locator('.annuler').isVisible(), true);
    assert.equal(await page.locator('form[data-privacy-post="delete"] button[type="submit"]').isDisabled(), false);
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
// Le choix de partage est replié dans son tiroir : sa case et son bouton ne se lisent qu'ouvert
const openConsent = page => page.locator('.privacy-consent summary').first().click();
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
          // Pages légales : sommaire ouvert, à gauche du texte et collant sous l'en-tête sur ordinateur, avant le texte sur téléphone
          const toc = await page.evaluate(() => {
            const side = document.querySelector('main .g-toc'), main = document.querySelector('main .g-texte');
            if (!side || !main) return null;
            const a = side.getBoundingClientRect(), b = main.getBoundingClientRect();
            const links = [...side.querySelectorAll('nav[aria-label="Sommaire de la page"] a[href^="#"]')];
            const targets = links.map(link => main.querySelector(link.getAttribute('href') + ' > h2'));
            return {left: a.right <= b.left, above: a.bottom <= b.top, headings: side.querySelectorAll('h1, h2, h3').length,
                    links: links.length, sections: main.querySelectorAll('section > h2').length,
                    targeted: targets.every(Boolean), visible: links.filter(link => link.checkVisibility()).length,
                    open: Boolean(side.querySelector('details[open]')), position: getComputedStyle(side).position};
          });
          assert.ok(toc, `${where} page légale sans composition`);
          assert.ok(toc.links > 0 && toc.links === toc.sections && toc.targeted, `${where} sommaire incomplet : ` + JSON.stringify(toc));
          assert.equal(toc.headings, 0, `${where} titre dans le sommaire`);
          assert.equal(toc.open, true, `${where} sommaire replié par défaut`);
          assert.ok(viewport.width === 390 ? toc.above && toc.visible === toc.links : toc.left && toc.visible === toc.links,
                    `${where} position ou ouverture du sommaire : ` + JSON.stringify(toc));
          if (viewport.width > 390) {
            assert.equal(toc.position, 'sticky', `${where} sommaire non collant`);
            // Sous l'en-tête fixe (jusqu'à 78 px) : un sommaire collé à 20 px passerait dessous
            const stuck = await page.evaluate(() => {
              if (document.documentElement.scrollHeight < innerHeight + 900) return null;
              scrollTo(0, 600);
              const side = document.querySelector('main .g-toc').getBoundingClientRect();
              const header = document.querySelector('header.haut').getBoundingClientRect();
              return {top: Math.round(side.top), headerBottom: Math.round(header.bottom), bottom: Math.round(side.bottom), height: innerHeight};
            });
            if (stuck) assert.ok(stuck.top >= stuck.headerBottom && stuck.bottom <= stuck.height, `${where} sommaire collé sous l'en-tête : ` + JSON.stringify(stuck));
          }
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
      const tuning = page.locator('details', {hasText: 'Choisir un niveau différent pour un modèle'});
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
        if (name === 'home')
          assert.equal(await page.locator('.accroche').evaluate(lead => getComputedStyle(lead).borderLeftStyle), 'none',
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
    await page.getByRole('link', {name: /Preuves/}).first().click();
    await page.waitForURL(origin + rendered.detail_href);
    assert.equal(await page.getAttribute('html', 'lang'), 'fr');
    assert.equal(await page.locator('h1').textContent(), 'Détail et preuves');
    assert.equal(await page.evaluate(() => [...document.styleSheets].some(sheet =>
      sheet.href?.endsWith('/preparation/style.css') && sheet.cssRules.length > 0)), true);
    assert.equal(await page.locator('#attempt-detail').isVisible(), true);
    assert.match(await page.locator('#attempt-detail').textContent(), /Pourquoi ce verdict/);
    assert.equal(await page.getByRole('link', {name: 'Revenir aux résultats'}).getAttribute('href'), rendered.back_href);
    assert.match(await page.locator('nav.piste [aria-current="step"]').textContent(), /Résultats/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'débordement à 390 px');
    mkdirSync('reports/privacy-browser', {recursive: true});
    await page.screenshot({path: 'reports/privacy-browser/attempt-detail-nojs-mobile.png', fullPage: true});
    // Le verdict et le coût se lisent avant la réponse ; la réponse et ses preuves sont côte à côte sur ordinateur, l'une sous l'autre sur téléphone
    for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 900});
      const layout = await page.evaluate(`((root = document) => {
      const head = root.querySelector('.verdict-tete'), figures = root.querySelector('.releves');
      const [answer, proofs] = [...root.querySelectorAll('.colonnes-moitie > section')];
      if (!head || !figures || !answer || !proofs) return null;
      const h = head.getBoundingClientRect(), f = figures.getBoundingClientRect(), a = answer.getBoundingClientRect(), p = proofs.getBoundingClientRect();
      return {headFirst: h.bottom <= f.top, figuresFirst: f.bottom <= a.top, right: p.left >= a.right, below: p.top >= a.bottom, figures: figures.innerText, proofs: proofs.innerText};
    })(document.getElementById('attempt-detail'))`);
      assert.ok(layout, `${width} détail sans composition`);
      assert.ok(layout.headFirst && layout.figuresFirst, `${width} ordre du verdict, des chiffres et de la réponse`);
      assert.match(layout.figures, /coût observé/i, `${width} coût hors des chiffres`);
      assert.match(layout.proofs, /Pourquoi ce verdict/, `${width} preuves hors de leur colonne`);
      assert.ok(width === 1440 ? layout.right : layout.below, `${width} position des preuves du détail`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0, `${width} débordement du détail`);
    }
  } finally {await off.close();}
  const on = await browser.newContext();
  try {
    const page = watch(await on.newPage());
    await page.goto(origin + '/render/comparison');
    // Aucune barre de coût : le montant se lit en chiffres
    assert.equal(await page.locator('.costbar').count(), 0, 'barre de coût présente');
    await page.getByRole('button', {name: /Preuves/}).first().focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#result-dialog .result-status').textContent === 'Détail chargé.');
    assert.equal(page.url(), origin + '/render/comparison');
    assert.equal(await page.evaluate(() => document.getElementById('result-dialog').open), true);
    assert.match(await page.locator('#result-dialog .result-body').textContent(), /Pourquoi ce verdict/);
    // Dans la fenêtre, la réponse et les preuves restent en une colonne
    const modal = await page.evaluate(`((root = document) => {
      const [answer, proofs] = [...root.querySelectorAll('.colonnes-moitie > section')];
      if (!answer || !proofs) return null;
      return {below: proofs.getBoundingClientRect().top >= answer.getBoundingClientRect().bottom};
    })(document.querySelector('#result-dialog .result-body'))`);
    assert.ok(modal && modal.below, 'fenêtre de détail en deux colonnes');
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
    await page.getByRole('link', {name: /Preuves/}).first().click();
    await page.waitForURL(origin + rendered.detail_href);
    assert.equal(await page.locator('h1').textContent(), 'Détail et preuves');
    await page.goBack();
    await page.waitForFunction(() => document.activeElement?.id === 'attempt-long');
  } finally {await legacy.close();}
  assert.deepEqual(failures, []);
});

test('results and publication preview stay readable at 390 px: whole words, stacked rows, no cost bars, method definition', async () => {
  const failures = [];
  const context = await browser.newContext({viewport: {width: 390, height: 844}, hasTouch: true, isMobile: true});
  try {
    const page = await context.newPage(); page.setDefaultTimeout(3000);
    page.on('pageerror', error => failures.push(error.message));
    page.on('console', message => {if (/Content Security Policy/.test(message.text())) failures.push(message.text());});
    await page.goto(origin + '/render/results');
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'débordement à 390 px');
    // K1 : le conseil et sa raison se lisent au premier écran, avant le bilan
    const advice = () => page.evaluate(() => {
      const ticket = document.querySelector('.ticket');
      return {bottom: ticket.getBoundingClientRect().bottom + scrollY, title: ticket.querySelector('h2').getBoundingClientRect().bottom + scrollY,
              first: Boolean(ticket.compareDocumentPosition(document.querySelector('section[aria-labelledby="t-bilan"]')) & Node.DOCUMENT_POSITION_FOLLOWING)};
    });
    for (const [width, height] of [[390, 844], [1440, 900]]) {
      await page.setViewportSize({width, height});
      await page.goto(origin + '/render/results');
      await page.evaluate(() => document.fonts.ready);
      const k1 = await advice();
      assert.ok(k1.first, `${width} conseil après le bilan`);
      // Sur téléphone, la piste et le titre de la maquette occupent le haut : le titre du ticket se lit au premier écran, le ticket entier sur ordinateur
      assert.ok((width === 1440 ? k1.bottom : k1.title) <= height, `${width} conseil sous le premier écran : ${k1.bottom}`);
    }
    // Chaque ticket de conseil tient au premier écran sur ordinateur ; le compromis d'une priorité se lit sans dépliant, sur téléphone aussi
    for (const [width, height] of [[390, 844], [1440, 900]]) {
      await page.setViewportSize({width, height});
      for (const state of ['results', 'results_priority']) {
        await page.goto(origin + '/render/' + state);
        await page.evaluate(() => document.fonts.ready);
        // Le premier ticket : l'état « results » en porte deux, la fixture ajoutant un conseil à une absence de conseil
        const bottom = await page.$eval('.ticket', ticket => ticket.getBoundingClientRect().bottom + scrollY);
        if (width === 1440) assert.ok(bottom <= height, `${state} conseil sous le premier écran : ${bottom}`);
      }
      await page.goto(origin + '/render/results_priority');
      assert.equal(await page.locator('section.g-compromis').isVisible(), true, `${width} compromis masqué`);
      assert.match(await page.locator('section.g-compromis').innerText(), /Compromis sur/);
    }
    await page.setViewportSize({width: 390, height: 844});
    await page.goto(origin + '/render/results');
    await page.evaluate(() => document.fonts.ready);
    // Aucun mot coupé : chaque cellule offre au moins la largeur de son mot visible le plus long
    const cut = await page.$$eval('.table-defile td', cells => cells.flatMap(cell => {
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
    // À 390 px, chaque ligne s'empile : aucun tableau ne défile horizontalement, aucune barre de coût
    assert.equal(await page.$$eval('.table-defile', regions => regions.every(region => region.scrollWidth <= region.clientWidth)), true, 'tableau défilant à 390 px');
    assert.equal(await page.locator('.costbar').count(), 0, 'barre de coût présente');
    // Verdict et exigence en défaut, entière ; l'explication du juge reste dans le détail
    assert.match((await page.locator('table').allInnerTexts()).join(' '), /Exigence non respectée : Action présente <img src=x onerror=alert\(1\)>\./);
    assert.equal(await page.locator('table img').count(), 0, 'motif du juge interprété comme HTML');
    // Une mesure booléenne se lit Oui ou Non, jamais True ou False
    assert.doesNotMatch((await page.locator('table').allInnerTexts()).join(' '), /\b(True|False)\b/);
    // Pied de page : le lien « Code source » reste dans la ligne du bas, avec son icône, aligné à gauche sur téléphone (maquette)
    const bottom = await page.$eval('footer .pied-bas', row => {
      const source = row.querySelector('a.source');
      return {inside: source.getBoundingClientRect().left >= row.getBoundingClientRect().left && source.getBoundingClientRect().right <= row.getBoundingClientRect().right,
              icon: Boolean(source.querySelector('svg[aria-hidden="true"]'))};
    });
    assert.deepEqual(bottom, {inside: true, icon: true});
    const method = await page.locator('#method').textContent();
    assert.match(method, /Résultat attendu :/);
    assert.match(method, /Le verdict vaut pour chaque modèle tel qu’il a été réglé et appelé ici/);
    assert.match(method, /la même consigne et les mêmes pièces, et travaillent avec le même harnais/);
    // La définition de la qualité se lit dans le tiroir de lecture, sans survol ni toucher
    assert.match(method, /critères fixés pour votre cas d’usage et vérifiés dans chaque réponse \(Durée fictive/);
    mkdirSync('reports/privacy-browser', {recursive: true});
    // Deux exemples : chaque tableau garde son titre et son dénominateur, et le clavier atteint chacun
    assert.deepEqual(await page.$$eval('.cas-titre h3', titles => titles.map(title =>
      /^\d+ réponses? évaluées? sur \d+ essais? prévus?$/.test(title.nextElementSibling.textContent))), [true, true]);
    const reached = new Set();
    for (let press = 0; press < 120 && reached.size < 2; press++) {
      await page.keyboard.press('Tab');
      const region = await page.evaluate(() => document.activeElement.matches('.table-defile[role="region"]')
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
    // Le choix des pièces se lit avant la page du lecteur, sur téléphone comme sur ordinateur
    for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 844});
      const layout = await page.evaluate(() => {
        const form = document.querySelector('main form'), sheet = document.querySelector('main .g-feuillet');
        if (!form || !sheet) return null;
        return {above: form.getBoundingClientRect().bottom <= sheet.getBoundingClientRect().top, form: Boolean(form.querySelector('fieldset')),
                body: sheet.querySelectorAll('h2').length, overflow: document.documentElement.scrollWidth - innerWidth};
      });
      assert.ok(layout && layout.form && layout.body > 0, `${width} aperçu sans composition : ` + JSON.stringify(layout));
      assert.ok(layout.above, `${width} position du choix des pièces`);
      assert.equal(layout.overflow, 0, `${width} débordement de l’aperçu`);
    }
    await page.setViewportSize({width: 390, height: 844});
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
    assert.match(await page.locator('#filters > summary').innerText(), /Tri et filtres/);
    assert.equal(await select.inputValue(), 'duration');
    assert.deepEqual(await page.locator('#filter-priority option').evaluateAll(options => options.map(option => option.value)),
                     ['', 'cost', 'duration', 'present']);
    assert.match(await page.locator('label[for="filter-priority"]').innerText(), /Priorité du conseil/);
    // La priorité reste visible ; son compromis se lit sous le ticket, sans dépliant ; les libellés du contrat restent du texte
    const advice = page.locator('section.ticket');
    const tradeoff = page.locator('section.g-compromis');
    assert.match(await advice.innerText(), /Priorité choisie/);
    assert.match(await tradeoff.innerText(), /Compromis sur/);
    assert.equal(await advice.locator('img, script').count() + await tradeoff.locator('img, script').count(), 0, 'compromis interprété comme HTML');
    assert.equal(await page.locator('#filter-priority img, #filter-priority script').count(), 0,
                 'libellé de critère interprété comme HTML');
    assert.match(await tradeoff.textContent(), /<img src=x onerror=alert\(1\)>/);
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
    // Sans JavaScript, le compromis est déjà lisible
    assert.match(await page.locator('section.g-compromis').innerText(), /Compromis sur/);
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
      assert.match(main, /2\s*MODÈLES COMPARÉS/i, colorScheme);
      for (const row of [/Réponses, estimation\s+non estimable/, /Réservé pour la vérification des réponses\s+0,30 USD/, /Crédit restant\s+18,5 \/ 20 USD/])
        assert.match(main, row, colorScheme);
      assert.equal(await page.locator('main input[type="checkbox"]').count(), 0, `${colorScheme} case à cocher`);
      assert.equal(await page.locator('main button:not(.btn-sec)').count(), 1, `${colorScheme} un seul bouton principal`);
      assert.equal(await page.locator('form[action$="/start"] input[name="confirm"]').getAttribute('value'), 'yes');
      assert.equal(await page.evaluate(() => {
        const nodes = [[...document.querySelectorAll('main a')].find(a => a.textContent === 'Modifier la sélection'),
                       document.querySelector('.table-defile table'), document.querySelector('form.pupitre'),
                       document.querySelector('form[action$="/start"] button')];
        return nodes.every(Boolean) && nodes.every((node, i) => !i || nodes[i - 1].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING);
      }), true, `${colorScheme} ordre lien, tableau, récapitulatif, bouton`);
      assert.match(await page.locator('.table-defile table').innerText(), /Faible non accepté par ce modèle/);
      // K5 : les nombres du tableau s'alignent en chiffres de largeur fixe
      assert.match(await page.locator('.table-defile td.nb').first().evaluate(cell => getComputedStyle(cell).fontVariantNumeric),
                   /tabular-nums/, `${colorScheme} chiffres du lancement`);
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
    assert.equal(await page.locator('h1').textContent(), 'Suivi du benchmark');
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
    assert.equal(await page.locator('h1').textContent(), 'Suivi du benchmark');
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
      if (action === 'pause') {
        // K7 : la cible d'arrêt garde 44 px de haut
        const stop = page.getByRole('button', {name: 'Arrêter la mise à jour automatique'});
        assert.ok((await stop.boundingBox()).height >= 44, 'arrêt du suivi sous 44 px');
        await stop.click();
      }
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
    // La mise à jour réécrit la zone d'état : la composition est remplacée entière, sans doublon
    await start();
    assert.equal((await page.locator('#campaign-status').textContent()).includes('réponses vérifiées'), false);
    await tick(3000, 'mixed-EMISSION_POSSIBLE-WAITING', 'FOLLOWUP_ACTIVE');
    assert.deepEqual(await page.evaluate(() => [document.querySelectorAll('.direct-tete').length,
      document.querySelectorAll('#campaign-status > .direct-tete').length]), [1, 1]);
    assert.match(await page.locator('#campaign-status').textContent(), /0\/2\s*réponses vérifiées/);
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
        assert.ok(text.includes('Solde restant') && text.includes('12,50') && text.includes('selon OpenRouter'));
        assert.ok(text.includes('Plafond de la clé') && text.includes('25'));
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
    for (const [name, text] of [['estimate-known', '0,125 USD'],
      ['estimate-unknown', 'Réservé pour la vérification des réponses'],
      ['launch-judgment-blocked', 'Budget insuffisant pour l’évaluation']]) {
      await page.goto(origin + '/render/' + name);
      assert.ok((await page.textContent('main')).includes(text), name);
      assert.equal(await page.getByRole('button', {name: 'Lancer le benchmark', exact: true}).count(), 0);
      assert.equal(await scriptsEtrangers(page), 0);
      assert.equal(await page.locator('[id="cap_usd"], form[action$="/cap"]').count(), 0);
    }
    await page.goto(origin + '/render/estimate-known');
    assert.ok((await page.textContent('main')).includes('le service d’exécution est indisponible. Réessayez dans quelques minutes.'));
    for (const state of ['ready', 'blocked']) {
      await page.goto(origin + '/render/launch-' + state);
      const text = await page.textContent('main');
      assert.equal(text.includes('Tout est prêt'), state === 'ready');
      assert.equal(text.includes('✓'), false);
      assert.equal(await page.getByRole('button', {name: 'Lancer le benchmark', exact: true}).count(), state === 'ready' ? 1 : 0);
      assert.equal(await page.locator('form[action$="/cap"], input[name="confirm"][required]').count(), 0);
      assert.equal(await page.getByRole('link', {name: 'Modifier la sélection'}).getAttribute('href'), '/preparation/dossiers/d1/configurations');
      if (state === 'ready') {
        assert.match(await page.locator('.pupitre').innerText(), /Crédit restant\s+12,50 USD/);
        assert.ok(text.includes('Réponses estimées à 3,50 USD'));
        assert.equal(await page.locator('input[name="confirm"]').getAttribute('type'), 'hidden');
        assert.equal(await page.locator('input[name="confirm"]').inputValue(), 'yes');
      } else {
        for (const phrase of ['Modèle à choisir de nouveau', 'relevé par le contrôle', 'Quantité à confirmer'])
          assert.ok(text.includes(phrase), phrase);
      }
      assert.ok((await page.locator('form').evaluateAll(forms => forms.map(f => [f.method, f.getAttribute('action')]))).every(
        ([method, action]) => method === 'post' && action.startsWith('/preparation/')));
      assert.equal(/après le paiement|dépasser le plafond|Modifier le plafond/.test(text), false);
    }
    await page.goto(origin + '/render/configurations-selected');
    assert.equal(await page.locator('form#configurations-form').getAttribute('action'), '/preparation/dossiers/d1/configurations');
    assert.equal(await page.locator('input[name="models"]:checked').count(), 2);
    assert.equal(await page.locator('input[name="tier"]:checked').getAttribute('value'), 'high');
    assert.equal(await page.locator('input[name="tier"]:checked').getAttribute('form'), 'configurations-form');
    const effort = page.locator('select[name="effort:modele-a"]');
    assert.equal(await effort.getAttribute('form'), 'configurations-form');
    assert.equal(await effort.inputValue(), 'xhigh');
    assert.equal(await page.locator('select[name="effort:modele-b"]').count(), 0);
    const selection = await page.textContent('main');
    for (const phrase of ['reçoit le plus proche', 'Choisir un niveau différent pour un modèle',
      'sans garantie d’être meilleur', 'Raisonnement fixe']) assert.ok(selection.includes(phrase), phrase);
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
    // Composition S18 : l'état d'abord, puis la zone principale et la colonne de synthèse, à droite sur ordinateur
    const zones = () => page.evaluate(() => {
      const box = selector => document.querySelector(selector).getBoundingClientRect();
      const side = box('.cote'), main = box('.pile-l');
      return {right: side.left >= main.right, below: side.top >= main.bottom, above: side.bottom <= main.top,
              overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
              side: document.querySelector('.cote').innerText};
    });
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      await page.goto(origin + '/render/access-connected');
      let layout = await zones();
      assert.match((await page.locator('.releves').innerText()).replace(/\s+/g, ' '), /SOLDE RESTANT 12,50\s*USD selon OpenRouter PLAFOND DE LA CLÉ 25\s*USD/i, `${width} relevés du solde`);
      assert.equal(await page.locator('.pile-l > .etat').count(), 1, `${width} état avant le formulaire`);
      assert.ok(layout.side.includes('Ce plafond est la seule limite de dépense.'), `${width} colonne de la clé`);
      assert.ok(width === 1440 ? layout.right : layout.below, `${width} colonne de la clé`);
      assert.equal(layout.overflow, 0, `${width} débordement de la clé`);
      await page.goto(origin + '/render/configurations-selected');
      assert.ok((await page.textContent('main')).includes('Enregistrer ce choix n’appelle aucun modèle.'), `${width} aucun appel`);
      assert.equal(await page.locator('.barre-choix button[form="configurations-form"]').innerText(), 'Continuer');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0, `${width} débordement des modèles`);
      await page.goto(origin + '/render/launch-ready');
      // Le récapitulatif porte les montants et le seul bouton ; le tableau reste dans la colonne de lecture
      const pupitre = await page.locator('form.pupitre').innerText();
      for (const phrase of [/RÉPONSES, ESTIMATION\s+3,50 USD/i, /Lancer le benchmark/])
        assert.match(pupitre, phrase, `${width}`);
      assert.equal(await page.locator('.pile-l table').count(), 1);
      const place = await page.evaluate(() => {
        const box = selector => document.querySelector(selector).getBoundingClientRect();
        const side = box('.pupitre'), table = box('.pile-l table'), checklist = box('.checklist');
        return {right: side.left >= box('.pile-l').right, between: side.top >= checklist.bottom && side.bottom <= table.top + 1,
                overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth};
      });
      // Sur une colonne, le récapitulatif et son bouton suivent la liste des contrôles, avant le tableau
      assert.ok(width === 1440 ? place.right : place.between, `${width} colonne du lancement`);
      assert.equal(place.overflow, 0, `${width} débordement du lancement`);
      await page.goto(origin + '/render/launch-blocked');
      const blocked = await page.locator('.pupitre').innerText();
      assert.ok(blocked.includes('Impossible de lancer pour l’instant'));
      assert.equal(blocked.includes('Lancer le benchmark'), false);
    }
  } finally {await context.close();}
});

test('followup distinguishes acquisition, incidents and judgment without reopening launch', async () => {
  const context = await browser.newContext({javaScriptEnabled: false});
  try {
    const page = await context.newPage();
    for (const [state, active, terminal, message] of [
      ['running', true, false, 'Benchmark en cours'], ['queued', true, false, 'Les essais n’ont pas encore démarré'],
      ['stopped', false, false, 'Le benchmark s’est arrêté avant la fin'],
      ['ambiguous', false, false, 'Un essai n’a pas pu être confirmé'],
      ['incident', false, false, 'Un problème technique est survenu'], ['received', false, true, 'Réponses reçues'],
      // #525 : un incident fournisseur non relançable nommé, jamais présenté comme un problème technique anonyme
      ['credit-exhausted', false, false, ['Crédit OpenRouter épuisé pour votre clé : rechargez-le ou relevez son plafond.', 'Le benchmark s’est arrêté']],
      // #537 : l'incident est attribué au canal appelé, jamais à OpenRouter par défaut
      ['official-limited', false, false, ['L’API officielle du fournisseur limite temporairement les demandes.', 'Le benchmark s’est arrêté']],
      // #539 : campagne opérateur, le modèle épuisé n'est pas présenté comme une réponse reçue
      ['operator-exhausted', false, false, ['Modèle A', 'Raisonnement non renseigné', 'Sans réponse']]]) {
      await page.goto(origin + '/render/followup-' + state);
      const text = await page.textContent('main');
      for (const part of [message].flat()) assert.ok(text.includes(part), state + ' ' + part);
      assert.equal(await page.locator('#preparation-progress').count(), Number(active), state);
      assert.equal(await page.locator('[data-results-href]').count(), Number(terminal), state);
      assert.equal(await page.locator('main form, #cap_usd').count(), 0);
      assert.equal(await page.locator('script').count() > 0, active || terminal);
      assert.equal(await page.locator('nav.piste a[href="/preparation/dossiers/d1/revisions/2"]').count() > 0, true);
      assert.equal(await page.locator('nav.piste a[href="#exemple"]').count(), 0);
      if (terminal) assert.ok(text.includes('Celles qui n’ont pas encore de verdict attendent leur vérification.'));
      // #541 : la ligne de compte ne présente pas le modèle épuisé comme une réponse reçue
      if (state === 'operator-exhausted') assert.match(text, /0\/1\s*réponses reçues/, text);
    }
    for (const status of ['NOT_STARTED', 'WAITING', 'RUNNING', 'COMPLETE', 'BLOCKED']) {
      await page.goto(origin + '/render/judgment-' + status);
      const active = ['WAITING', 'RUNNING'].includes(status), ready = status === 'COMPLETE';
      const text = await page.textContent('main');
      assert.equal(await page.locator('#preparation-progress').count(), Number(active));
      assert.equal(await page.locator('[data-results-href]').count(), Number(ready));
      assert.match(text, /1\/1\s*réponses reçues/);
      assert.match(text, new RegExp(`${Number(ready)}/1\\s*réponses vérifiées`));
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
      assert.ok((await page.title()).includes('Suivi du benchmark'));
      assert.equal(await page.locator('script').count() > 0, active);
    }
    // Composition S18 : état en tête, liste des modèles en zone principale, compteurs en synthèse, actions hors de la zone réécrite
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      for (const view of ['followup-running', 'followup-credit-exhausted', 'judgment-NOT_STARTED', 'followup-received']) {
        await page.goto(origin + '/render/' + view);
        const layout = await page.evaluate(() => {
          const status = document.getElementById('campaign-status');
          return {overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                  head: Boolean(status.querySelector('.direct-tete h2')), list: status.querySelector('ol.couloirs')?.getAttribute('aria-label'),
                  lanes: status.querySelectorAll('.couloir').length, counter: status.querySelector('.compteur')?.textContent,
                  actionsInside: status.querySelectorAll('#preparation-progress, form, a.btn, a.lien, button').length};
        });
        assert.equal(layout.overflow, 0, `${width} ${view} sans débordement`);
        assert.ok(layout.head, `${width} ${view} titre d'état en tête`);
        assert.equal(layout.list, 'Modèle par modèle', `${width} ${view} liste des couloirs`);
        assert.ok(layout.lanes > 0, `${width} ${view} un couloir par modèle`);
        assert.match(layout.counter, /\d+\/\d+\s*réponses reçues/, `${width} ${view} compteur`);
        assert.equal(layout.actionsInside, 0, `${width} ${view} actions hors de la zone réécrite`);
      }
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
      await page.getByRole('navigation', {name: 'Parcours'}).getByRole('link', {name: 'Benchmarks publics'}).click();
      assert.equal(await page.locator('main h3').count(), 3);
      // La recherche et les filtres se lisent avant la liste ; la fiche garde la source en synthèse
      const zones = () => page.evaluate(() => {
        const side = document.querySelector('main .cote'), main = document.querySelector('main .pile-l');
        if (!side || !main) return null;
        const a = side.getBoundingClientRect(), b = main.getBoundingClientRect();
        return {right: a.left >= b.right, below: a.top >= b.bottom, side: side.innerText, main: main.innerText,
                overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth};
      });
      const search = () => page.evaluate(() => {
        const form = document.querySelector('main form[role="search"]'), list = document.querySelector('main .g-grille');
        return {above: form.getBoundingClientRect().bottom <= list.getBoundingClientRect().top, field: Boolean(form.querySelector('#q')),
                overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth};
      });
      for (const width of [390, 1440]) {
        await page.setViewportSize({width, height: 844});
        await page.goto(origin + '/evaluations-externes');
        const found = await search();
        assert.ok(found.field && found.above, `${width} recherche des benchmarks avant la liste`);
        assert.equal(found.overflow, 0, `${width} débordement de la liste des benchmarks`);
        await page.goto(origin + '/render/approved-mteb');
        const layout = await zones();
        assert.ok(layout, `${width} fiche sans composition`);
        assert.match(layout.main, /elle mesure/i, `${width} notice hors zone principale`);
        assert.match(layout.main, /ne prouvent pas la réussite sur votre cas d’usage/, `${width} rappel hors zone principale`);
        assert.match(layout.main, /Décrire mon cas d’usage/, `${width} lien du besoin hors zone principale`);
        assert.match(layout.side, /source officielle/i, `${width} source hors synthèse`);
        assert.ok(width === 1440 ? layout.right : layout.below, `${width} position de la synthèse de la fiche`);
        assert.equal(layout.overflow, 0, `${width} débordement de la fiche`);
      }
      await page.setViewportSize({width: 390, height: 844});
      await page.goto(origin + '/evaluations-externes');
      assert.equal(await page.locator('input[name="category"]').inputValue(), '');
      assert.equal(await page.locator('input[name="verification"]').inputValue(), '');
      assert.equal(await page.locator('main img').count() + await scriptsEtrangers(page, 'main script'), 0);
      assert.equal(await scriptsEtrangers(page), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      // Le lien « Décrire mon cas d’usage » n'existe que dans les états vides
      assert.equal(await page.getByRole('link', {name: 'Décrire mon cas d’usage', exact: true}).count(), 0);
      if (javaScriptEnabled) {
        // Avec JavaScript : la recherche filtre dès 3 caractères, sans rechargement ; un filtre cliqué garde la position
        assert.equal(await page.locator('script').count(), 1);
        await page.evaluate(() => {window.sansRechargement = true;});
        await page.getByLabel('Rechercher un benchmark').fill('co');
        await page.waitForTimeout(400);
        assert.equal(new URL(page.url()).searchParams.get('q'), null, 'filtrage sous 3 caractères');
        await page.getByLabel('Rechercher un benchmark').fill('corriger');
        await page.waitForFunction(() => new URL(location.href).searchParams.get('q') === 'corriger');
        // Le contenu affiché est celui que le serveur rend pour cette recherche
        const attendu = (rendered.reference_search[JSON.stringify(['corriger', '', ''])].split('<main')[1].match(/<h3/g) || []).length;
        await page.waitForFunction(n => document.querySelectorAll('main h3').length === n && /corriger/.test(document.querySelector('main').textContent), attendu);
        assert.equal(await page.locator('#q').inputValue(), 'corriger');
        assert.equal(await page.evaluate(() => document.activeElement?.id), 'q', 'la saisie garde le focus');
        await page.evaluate(() => window.scrollTo(0, 300));
        const avant = await page.evaluate(() => scrollY);
        assert.ok(avant > 0, 'page trop courte pour mesurer la position');
        // Les filtres s'appliquent au clic et gardent le texte cherché dans l'adresse
        await page.getByRole('link', {name: 'Programmation', exact: true}).click();
        await page.waitForFunction(() => new URL(location.href).searchParams.get('category') === 'coding');
        assert.equal(new URL(page.url()).searchParams.get('q'), 'corriger');
        assert.equal(await page.evaluate(() => scrollY), avant, 'la page remonte au clic sur un filtre');
        await page.getByRole('link', {name: 'Vérifiée', exact: true}).click();
        await page.waitForFunction(() => new URL(location.href).searchParams.get('verification') === 'verified');
        assert.equal(await page.evaluate(() => window.sansRechargement), true, 'page rechargée');
      } else {
        // Sans JavaScript : recherche native, puis filtres-liens qui gardent le texte cherché dans l'adresse
        await page.getByLabel('Rechercher un benchmark').fill('corriger');
        await Promise.all([page.waitForNavigation(), page.keyboard.press('Enter')]);
        await page.getByRole('link', {name: 'Programmation', exact: true}).click();
        assert.equal(new URL(page.url()).searchParams.get('q'), 'corriger');
        await page.getByRole('link', {name: 'Vérifiée', exact: true}).click();
      }
      assert.deepEqual([...new URL(page.url()).searchParams.entries()].sort(),
        [['category', 'coding'], ['q', 'corriger'], ['verification', 'verified']]);
      assert.equal(await page.locator('main h3').count(), 1);
      assert.equal(await page.locator('#q').inputValue(), 'corriger');
      assert.equal(await page.locator('input[name="category"]').inputValue(), 'coding');
      assert.equal(await page.locator('input[name="verification"]').inputValue(), 'verified');
      assert.deepEqual(await page.locator('main .segments-liens a[aria-current]').allTextContents().then(texts => texts.sort()), ['Programmation', 'Vérifiée']);
      await page.getByRole('link', {name: 'Effacer la recherche et les filtres'}).click();
      assert.equal(await page.locator('main h3').count(), 3);
      assert.equal(await page.getByRole('link', {name: 'Effacer la recherche et les filtres'}).count(), 0);
      await page.getByLabel('Rechercher un benchmark').fill('introuvable');
      await Promise.all([page.waitForNavigation(), page.keyboard.press('Enter')]);
      assert.match(await page.textContent('main'), /Aucun benchmark ne correspond/);
      assert.equal(await page.getByRole('link', {name: 'Décrire mon cas d’usage', exact: true}).getAttribute('href'), '/preparation');
      await page.goto(origin + '/evaluations-externes?q=' + encodeURIComponent('<script>alert(2)</script>'));
      assert.equal(await page.locator('#q').inputValue(), '<script>alert(2)</script>');
      assert.equal(await scriptsEtrangers(page, 'main script'), 0);
      await page.goto(origin + '/render/reference-empty');
      assert.match(await page.textContent('main'), /Aucun benchmark public disponible/);
      await page.goto(origin + '/evaluations-externes/atelier-inconnu');
      assert.match(await page.textContent('main'), /Vérification insuffisante/);
      assert.match(await page.textContent('main'), /inconnu/);
      await page.goto(origin + '/evaluations-externes/atelier-retire');
      assert.match(await page.textContent('main'), /Source retirée/);
      assert.equal(await page.locator('main a[href^="https:"]').count(), 0);
      await page.goto(origin + '/render/reference-referral');
      const target = page.locator('main a[href="/evaluations-externes/atelier-code"]');
      // Le lien officiel du benchmark s'ouvre sans référent ni lien avec la page d'origine
      assert.deepEqual((await page.locator('main a[href="https://code.example/evaluation"]').getAttribute('rel')).split(/\s+/).sort(), ['noopener', 'noreferrer']);
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
      assert.equal(await page.locator('main img').count() + await scriptsEtrangers(page, 'main script'), 0);
      assert.equal(await scriptsEtrangers(page), 0);
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
      assert.ok(await page.locator('main a[href="/evaluations-externes"]').count() >= 1);
      assert.equal(await page.locator('main img').count() + await scriptsEtrangers(page, 'main script'), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({path: 'reports/462/suggestions-mobile-' + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      await page.goto(origin + '/render/reference_help_unavailable');
      assert.match(await page.textContent('main'), /Aide indisponible/);
      assert.match(await page.textContent('main'), /désactivé/);
      assert.ok(await page.locator('main a[href="/evaluations-externes"]').count() >= 1);
      await page.goto(origin + '/render/approved-index');
      const approved = await page.textContent('main');
      for (const name of ['MathArena', 'LiveCodeBench', 'SWE-bench', 'MTEB', 'Artificial Analysis, performances API'])
        assert.ok(approved.includes(name), name);
      assert.equal(/HELM/i.test(approved), false);
      assert.equal(approved.includes('Ayo'), false);
      assert.ok(approved.includes('22 octobre 2026'));
      assert.ok(approved.includes('Ce contrôle est fait à part de la relecture.'));
      assert.equal(await page.locator('main img').count() + await scriptsEtrangers(page, 'main script'), 0);
      assert.equal(await scriptsEtrangers(page), 0);
      const categories = await page.locator('main .segments-liens a[href*="category="]').allTextContents();
      assert.ok(categories.includes('Recherche documentaire'));
      assert.ok(categories.includes('Performances des services'));
      assert.equal(categories.some(name => /helm/i.test(name)), false);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.goto(origin + '/render/approved-retrieval');
      assert.equal(await page.locator('main h3').count(), 1);
      assert.match(await page.textContent('main'), /MTEB/);
      assert.equal((await page.textContent('main')).includes('MathArena'), false);
      assert.equal(await page.locator('input[name="category"]').inputValue(), 'retrieval');
      await page.goto(origin + '/render/approved-api');
      assert.match(await page.textContent('main'), /Artificial Analysis, performances API/);
      assert.equal((await page.textContent('main')).includes('MTEB'), false);
      assert.equal(await page.locator('input[name="category"]').inputValue(), 'api');
      assert.equal(await page.locator('#q').inputValue(), 'API');
      await page.goto(origin + '/render/approved-matharena');
      const matharena = await page.textContent('main');
      assert.match(matharena, /inconnu/);
      assert.match(matharena, /22 octobre 2026/);
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
      assert.match(await page.textContent('main'), /inconnu/);
      await page.goto(origin + '/render/approved-mteb');
      assert.match(await page.textContent('main'), /n’a pas été essayé/);
      assert.equal(await page.locator('main a[href="https://github.com/embeddings-benchmark/mteb"]').count(), 1);
      await page.goto(origin + '/render/approved-artificial-analysis-api');
      const apiNotice = await page.textContent('main');
      assert.match(apiNotice, /2\.2\.0/);
      assert.match(apiNotice, /2 mars 2026/);
      assert.match(apiNotice, /Leur indice global n’entre pas dans Evaly/);
      assert.equal(apiNotice.includes('%'), false);
      assert.equal(await scriptsEtrangers(page), 0);
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
      // À 390 px, le menu est le dépliant natif « Menu »
      await page.locator('.nav-mobile > summary').click();
      await page.locator('.nav-mobile').getByRole('link', {name: 'Épreuves', exact: true}).click();
      assert.match(await page.textContent('main'), /Tableau <script>alert\(1\)<\/script>/);
      // La recherche se lit avant la liste ; sur la fiche, la copie reste en synthèse à droite ou sous les pièces
      const zones = () => page.evaluate(() => {
        const side = document.querySelector('main .cote'), main = document.querySelector('main .pile-l');
        if (!side || !main) return null;
        const a = side.getBoundingClientRect(), b = main.getBoundingClientRect();
        return {right: a.left >= b.right, below: a.top >= b.bottom, side: side.innerText, main: main.innerText,
                overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth};
      });
      for (const width of [390, 1440]) {
        await page.setViewportSize({width, height: 844});
        await page.goto(origin + '/epreuves');
        const found = await page.evaluate(() => {
          const form = document.querySelector('main form[role="search"]'), list = document.querySelector('main .g-grille');
          return {above: form.getBoundingClientRect().bottom <= list.getBoundingClientRect().top, field: Boolean(form.querySelector('#q')),
                  text: list.innerText, overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth};
        });
        assert.ok(found.field && found.above, `${width} recherche des épreuves avant la liste`);
        assert.match(found.text, /Tableau <script>alert\(1\)<\/script>/, `${width} épreuves dans la liste`);
        assert.equal(found.overflow, 0, `${width} débordement de la liste des épreuves`);
        await page.goto(origin + '/epreuves/tache-fictive/version-1');
        const layout = await zones();
        assert.ok(layout, `${width} fiche d'épreuve sans composition`);
        assert.match(layout.main, /Pièces/, `${width} pièces hors zone principale`);
        assert.match(layout.side, /Créer mon dossier privé/, `${width} copie hors synthèse`);
        assert.ok(width === 1440 ? layout.right : layout.below, `${width} position de la copie`);
        assert.equal(layout.overflow, 0, `${width} débordement de la fiche d'épreuve`);
      }
      await page.setViewportSize({width: 390, height: 844});
      await page.goto(origin + '/epreuves');
      assert.equal(await page.locator('main img').count() + await scriptsEtrangers(page, 'main script'), 0);
      assert.equal(await scriptsEtrangers(page), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      // Recherche native, puis filtres appliqués au clic qui gardent le texte cherché ; critères visibles et effaçables, sans JavaScript
      const searchField = page.getByLabel('Rechercher une épreuve');
      await searchField.focus();
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      await searchField.fill('note');
      await Promise.all([page.waitForNavigation(), page.keyboard.press('Enter')]);
      assert.match(page.url(), /q=note/);
      await page.getByRole('link', {name: 'Français', exact: true}).click();
      await page.waitForFunction(() => new URL(location.href).searchParams.get('langue') === 'Français');
      await page.getByRole('link', {name: 'Tableur', exact: true}).click();
      await page.waitForFunction(() => new URL(location.href).searchParams.get('outil') === 'Tableur');
      assert.equal(new URL(page.url()).searchParams.get('q'), 'note');
      assert.equal(new URL(page.url()).searchParams.get('langue'), 'Français');
      assert.equal(new URL(page.url()).searchParams.get('outil'), 'Tableur');
      assert.equal(await page.locator('main [aria-label="Critères actifs"]').count(), 1);
      const filtered = await page.textContent('main');
      assert.match(filtered, /Français/);
      assert.match(filtered, /Tableur/);
      assert.equal(filtered.includes('Tableau <script>alert(1)</script>'), false);
      assert.equal(filtered.includes('Note <script>alert(4)</script>'), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.getByRole('link', {name: 'Note <script>alert(4)</script>'}).click();
      assert.equal(await page.locator('h1').textContent(), 'Note <script>alert(4)</script>');
      assert.match(await page.textContent('main'), /Retour aux résultats/);
      await page.getByRole('link', {name: 'Retour aux résultats'}).click();
      assert.equal(await page.locator('main [aria-label="Critères actifs"]').count(), 1);
      await page.getByRole('link', {name: 'Effacer la recherche et les filtres'}).click();
      assert.equal(new URL(page.url()).search, '');
      assert.equal(await page.getByLabel('Rechercher une épreuve').inputValue(), '');
      assert.match(await page.textContent('main'), /Tableau <script>alert\(1\)<\/script>/);
      await page.getByLabel('Rechercher une épreuve').fill('introuvable');
      await Promise.all([page.waitForNavigation(), page.keyboard.press('Enter')]);
      const withoutResult = await page.textContent('main');
      assert.match(withoutResult, /Aucune épreuve ne correspond à cette recherche/);
      // Seul un catalogue vide propose de décrire un besoin
      assert.equal(await page.getByRole('link', {name: 'Décrire mon cas d’usage', exact: true}).count(), 0);
      mkdirSync('reports/464', {recursive: true});
      await page.screenshot({path: 'reports/464/recherche-mobile-'
        + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      // Une valeur longue proposée reste utilisable : son lien porte la valeur entière
      await page.goto(origin + '/epreuves');
      await page.locator('main .segments-liens a[href*="outil="]').filter({hasText: 'Tableur de contrôle'}).click();
      await page.waitForFunction(() => new URL(location.href).searchParams.has('outil'));
      const longResult = await page.textContent('main');
      assert.match(longResult, /Version longue/);
      assert.equal(await page.locator('main [aria-label="Critères actifs"]').count(), 1);
      assert.equal(new URL(page.url()).searchParams.get('outil'), rendered['editorial-long-tool']);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      // Un filtre devenu indisponible reste actif lors d'une nouvelle soumission
      await page.goto(origin + '/render/editorial-unavailable');
      assert.equal(await page.locator('input[name="outil"]').inputValue(), 'Tableur retiré');
      assert.match(await page.locator('main .segments-liens a[aria-current]').filter({hasText: 'Tableur retiré'}).textContent(), /indisponible/);
      await page.getByLabel('Rechercher une épreuve').fill('note');
      await Promise.all([page.waitForNavigation(), page.keyboard.press('Enter')]);
      const retained = await page.textContent('main');
      assert.equal(await page.locator('main [aria-label="Critères actifs"]').count(), 1);
      assert.match(retained, /Tableur retiré \(indisponible\)/);
      assert.match(retained, /Aucune épreuve ne correspond à cette recherche/);
      assert.equal(retained.includes('Version au tableur retiré'), false);
      assert.equal(new URL(page.url()).searchParams.get('outil'), 'Tableur retiré');
      assert.equal(await page.locator('input[name="outil"]').inputValue(), 'Tableur retiré');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({path: 'reports/464/filtre-indisponible-mobile-'
        + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      await page.getByRole('link', {name: 'Effacer la recherche et les filtres'}).click();
      assert.equal(await page.locator('input[name="outil"]').count(), 0);
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
      assert.equal(await scriptsEtrangers(page, 'main script'), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      // La pièce se lit dans sa feuille : on l'atteint au clavier, on l'ouvre sans JavaScript
      let focused = false;
      for (let press = 0; press < 40 && !focused; press++) {
        await page.keyboard.press('Tab');
        focused = await page.evaluate(() => document.activeElement?.matches('.feuille summary'));
      }
      assert.ok(focused, 'feuille de pièce non atteinte au clavier');
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      mkdirSync('reports/463', {recursive: true});
      await page.screenshot({path: 'reports/463/epreuve-mobile-' + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      await page.keyboard.press('Enter');
      assert.match(await page.locator('.feuille details[open] pre').innerText(), /Pièce <script>alert\(1\)<\/script>/);
      await page.goto(origin + '/epreuves/tache-fictive/version-1');
      const copyLink = page.getByRole('link', {name: 'Créer mon dossier privé'});
      await copyLink.focus();
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      assert.equal(await scriptsEtrangers(page, 'main script'), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.goto(origin + '/render/copy-confirmation');
      const confirmation = await page.textContent('main');
      assert.match(confirmation, /Épreuve source/);
      assert.match(confirmation, /Tableau <script>alert\(1\)<\/script>/);
      assert.match(confirmation, /Erreur signalée <script>alert\(2\)<\/script>/);
      assert.match(confirmation, /pièces copiées/);
      assert.match(confirmation, /Créer mon dossier privé/);
      assert.equal(await scriptsEtrangers(page, 'main script'), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const copyButton = page.getByRole('button', {name: 'Créer mon dossier privé'});
      await copyButton.focus();
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle), 'none');
      await page.screenshot({path: 'reports/465/confirmation-mobile-' + (javaScriptEnabled ? 'js' : 'nojs') + '.png', fullPage: true});
      for (const width of [390, 1440]) {
        await page.setViewportSize({width, height: 844});
        const layout = await page.evaluate(`((root = document) => {
      const side = root.querySelector('.cote'), main = root.querySelector('.pile-l');
      if (!side || !main) return null;
      const a = side.getBoundingClientRect(), b = main.getBoundingClientRect();
      return {right: a.left >= b.right, above: a.bottom <= b.top, below: a.top >= b.bottom, side: side.innerText, main: main.innerText};
    })()`);
        assert.ok(layout, `${width} copie sans composition`);
        assert.match(layout.main, /Épreuve source/, `${width} épreuve hors zone principale`);
        assert.match(await page.locator('header.tete').innerText(), /Aucune clé ni aucun appel aux modèles/, `${width} conditions sous le titre`);
        assert.match(layout.side, /Créer mon dossier privé/, `${width} bouton hors zone principale`);
        assert.ok(width === 1440 ? layout.right : layout.below, `${width} position de la décision de la copie`);
      }
      await page.setViewportSize({width: 390, height: 844});
      await page.goto(origin + '/render/copy-dossier');
      const copied = await page.textContent('main');
      assert.match(copied, /Épreuve source/);
      assert.match(copied, /Tableau <script>alert\(1\)<\/script>/);
      assert.match(copied, /Pièce source <script>alert\(1\)<\/script>/);
      assert.match(copied, /Erreur signalée <script>alert\(2\)<\/script>/);
      assert.match(copied, /Aucune qualification n’est publiée pour cette épreuve/);
      assert.match(copied, /la copie reste consultable sans elle/);
      // Copie sans exemple préparé : son état et la consultation sans clé sont dits au premier écran
      assert.match(copied, /Copie d’épreuve/);
      assert.match(copied, /Version 1/);
      assert.equal(copied.includes('Exemple inventé'), false);
      assert.match(copied, /Votre copie est enregistrée\./);
      assert.match(copied, /sans clé ni appel aux modèles/);
      assert.match(copied, /servira à préparer votre exemple/);
      assert.equal(copied.includes('Ajoutez votre clé OpenRouter pour préparer un exemple.'), false);
      assert.match(copied, /aucun appel modèle effectué/);
      await page.reload();
      const reloadedCopy = await page.textContent('main');
      assert.match(reloadedCopy, /Copie d’épreuve/);
      assert.match(reloadedCopy, /Épreuve source/);
      assert.match(reloadedCopy, /Pièce source <script>alert\(1\)<\/script>/);
      assert.match(reloadedCopy, /Qualification absente/);
      assert.match(reloadedCopy, /aucun appel modèle effectué/);
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
      assert.equal(await scriptsEtrangers(page, 'main script'), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.goto(origin + '/render/editorial-help-result');
      const helpResult = await page.textContent('main');
      assert.match(helpResult, /Correspondance forte/);
      assert.match(helpResult, /Correspondance partielle/);
      assert.match(helpResult, /ne prouve ni adéquation ni qualification/);
      assert.equal(await page.locator('main a[href="/epreuves/tache-fictive/version-1"]').count(), 1);
      assert.equal(await page.locator('main a[href="/epreuves/tache-fictive/version-2"]').count(), 1);
      assert.equal(await scriptsEtrangers(page, 'main script'), 0);
      // La suggestion s'atteint au clavier ; son cadre porte l'anneau de focus
      let suggestionReached = false;
      for (let press = 0; press < 80 && !suggestionReached; press++) {
        await page.keyboard.press('Tab');
        suggestionReached = await page.evaluate(() => document.activeElement?.getAttribute('href') === '/epreuves/tache-fictive/version-2');
      }
      assert.ok(suggestionReached, 'suggestion non atteinte au clavier');
      assert.notEqual(await page.evaluate(() => getComputedStyle(document.activeElement.closest('.g-fiche')).outlineStyle), 'none');
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
      assert.equal(await scriptsEtrangers(page, 'main script'), 0);
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
      assert.equal(await scriptsEtrangers(page), 0);
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
      // Deux essais du même modèle sur le même exemple : chaque lien « Preuves » mène au détail de son essai
      await page.goto(origin + '/render/published-results-repeated');
      const cibles = await page.locator('main a.lien[href^="#"]').filter({hasText: 'Preuves'}).evaluateAll(links => links.map(link => link.getAttribute('href')));
      assert.equal(cibles.length, 2);
      assert.equal(new Set(cibles).size, 2, 'ancre de preuve partagée entre deux essais');
      for (const cible of cibles) assert.equal(await page.locator(cible).count(), 1, cible);
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
      assert.match(main, /2 essais lancés sur 2/);
      assert.equal(await page.locator('main img').count() + await scriptsEtrangers(page, 'main script'), 0);
      assert.equal(await scriptsEtrangers(page), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      // Une seule colonne : bilan, comparaison, conditions communes, lecture, puis ce que la publication permet de vérifier
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: 844});
        const layout = await page.evaluate(() => {
          const main = document.querySelector('main');
          const at = selector => main.querySelector(selector)?.getBoundingClientRect().top ?? null;
          return {table: Boolean(main.querySelector('table.comparatif')), text: main.innerText,
                  order: [at('section[aria-labelledby="t-bilan"]'), at('section[aria-labelledby="t-comp"]'), at('section[aria-labelledby="t-cond"]'),
                          at('#method'), at('.g-pied-pub')],
                  overflow: document.documentElement.scrollWidth - innerWidth};
        });
        assert.ok(layout.table, `${width} résultats publiés sans tableau`);
        for (const heading of [/Conditions communes/, /Ce que cette publication permet de vérifier/, /Comment lire ces résultats/, /Harnais/i])
          assert.match(layout.text, heading, `${width} ${heading} absent`);
        assert.ok(layout.order.every((top, index) => top !== null && (index === 0 || top > layout.order[index - 1])), `${width} ordre des blocs publiés : ` + layout.order);
        assert.equal(layout.overflow, 0, `${width} débordement des résultats publiés`);
      }
      // Filtres natifs visibles, un seul tableau à une ligne sur la vue filtrée
      assert.equal(await page.locator('#filter-configuration').count(), 1);
      await page.goto(origin + '/render/published-results-filtered');
      const filtered = await page.textContent('main');
      assert.match(filtered, /1 réponse affichée sur 2/);
      assert.equal(await page.locator('table.comparatif tbody tr').count(), 1);
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
      await page.locator('section[aria-labelledby="t-det"] summary').first().click();
      // Détail ouvert : le nom du modèle garde des mots entiers, jamais une lettre par ligne ; aucun tableau ne défile à 390 px
      const column = await page.evaluate(() => {
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
        const name = document.querySelector('table.comparatif tbody .modele');
        return {name: splitWords(name.firstChild), characters: name.firstChild.length,
                scroll: [...document.querySelectorAll('.table-defile:has(table.comparatif)')].every(region => region.scrollWidth <= region.clientWidth)};
      });
      assert.deepEqual(column.name, [], 'nom du modèle coupé entre deux lettres : ' + JSON.stringify(column));
      assert.ok(column.scroll, 'un tableau défile horizontalement à 390 px : ' + JSON.stringify(column));
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
      if (reason !== 'open') assert.ok((await page.locator('#availability').textContent()).length >= 20, reason);
      assert.equal(await page.locator('form form').count(), 0);
    }
    await page.goto(origin + '/render/availability-access');
    assert.equal(await page.locator('a').first().getAttribute('href'), '#main');
    assert.equal(await page.locator('main').getAttribute('tabindex'), '-1');
    assert.equal(await page.locator('#request').getAttribute('aria-describedby'), 'request-help availability');
    assert.equal(await page.locator('form#nouveau button[type="submit"]').count(), 1);
    assert.equal(await page.locator('form#nouveau #request').count(), 1);
    // La clé s'ajoute sur sa page : l'état de la clé précède le champ, et la page ne porte plus de formulaire de clé
    const ordering = await page.textContent('main');
    assert.ok(ordering.indexOf('Ajoutez votre clé OpenRouter') < ordering.indexOf('Décrivez votre cas d’usage'));
    assert.equal(await page.locator('#openrouter-key').count(), 0);
    assert.equal(await page.locator('main a[href="/preparation/access"]').count() >= 2, true);
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
        assert.ok((await page.textContent('main')).includes('40 caractères au moins'));
      }
    }
    await page.goto(origin + '/render/prior-example');
    assert.equal(await page.locator('nav.piste span.st').count(), 4);
    for (const text of ['01Besoin', '02Exemple', '03Validation', '04Modèles', '05Résultats'])
      assert.ok((await page.locator('nav.piste').textContent()).includes(text));
    assert.equal(await page.locator('nav.piste a[href="#besoin"]').count(), 1);
    await page.goto(origin + '/render/example-QUALIFIED');
    assert.ok((await page.locator('nav.piste [aria-current="step"]').textContent()).includes('Validation'));
    assert.equal(await page.locator('nav.piste a[href$="/configurations"]').count(), 1);
    await page.goto(origin + '/render/scope');
    assert.ok((await page.textContent('main')).includes('Précisez le travail à comparer'));
    assert.ok((await page.textContent('main')).includes('Aucun benchmark ne se lance à cette étape.'));
    assert.equal((await page.textContent('main')).includes('Choisir les modèles'), false);
    assert.equal(await page.locator('a[href="#exemple"], a[href="#validation"], form[action$="/validation"], #openrouter-key').count(), 0);
    for (const category of ['math', 'coding', 'other']) {
      await page.goto(origin + '/render/out-of-scope-' + category);
      const text = await page.textContent('main');
      assert.ok(text.includes(category === 'other' ? 'Evaly compare des modèles sur un travail précis' : 'Ce travail est déjà mesuré par des benchmarks publics'));
      assert.ok(text.includes('Décrire un cas d’usage précis'));
      assert.equal(/Envoyer ma réponse|Choisir les modèles|équipe (Bench-X|Evaly)|n’est pas encore/.test(text), false);
      assert.equal(await page.locator('form[action$="/validation"], #availability').count(), 0);
      assert.equal(await page.locator('a[href="/evaluations-externes/matharena"]').count(), Number(category === 'math'));
      assert.ok(await page.locator('main a[href="/evaluations-externes"]').count() >= 1);
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
    // K7 : à 390 px, menu, étapes et liens du pied de page offrent 44 px de haut (décision du 1er novembre 2026)
    await page.setViewportSize({width: 390, height: 844});
    await page.goto(origin + '/render/example-PENDING');
    const small = await page.$$eval('header.haut nav a, .piste a, footer.pied a',
      links => links.filter(link => link.getClientRects().length && link.getBoundingClientRect().height < 44)
        .map(link => link.textContent.trim() + ' ' + Math.round(link.getBoundingClientRect().height)));
    assert.deepEqual(small, [], 'cibles sous 44 px');
    assert.ok((await page.textContent('main')).includes('Le contrôle de l’exemple n’a pas pu démarrer.'));
    assert.equal((await page.textContent('main')).includes('Approbation'), false);
    await page.goto(origin + '/render/example-BLOCKED');
    assert.ok((await page.textContent('main')).includes('Exemple à revoir avant le benchmark'));
    assert.equal(await page.locator('img[src="x"]').count(), 0);
    assert.equal((await page.textContent('main')).split('<img src=x onerror="alert(1)">Correction requise').length, 2);
    await page.goto(origin + '/render/stopped-history');
    assert.ok((await page.textContent('main')).includes('Aucun nouvel essai ne peut être lancé pour ce benchmark.'));
    assert.ok((await page.textContent('main')).includes('Service momentanément indisponible'));
    for (const [stage, status, prior, pending] of [['waiting', 'none', false, true], ['waiting', 'none', true, false],
      ['clarification', 'none', false, false], ['suspended', 'none', false, false], ['preview', 'PENDING', false, true],
      ['preview', 'BLOCKED', false, false], ['preview', 'QUALIFIED', false, false]]) {
      await page.goto(origin + `/render/progress-${stage}-${status}-${prior ? 'True' : 'False'}`);
      assert.equal(await page.locator('#preparation-progress').count(), Number(pending));
      assert.equal(await page.locator('.etat .disque.tourne').count(), Number(pending));
      if (pending) {
        assert.equal(await page.locator('#availability').count(), 1);
        assert.equal(await page.locator('aside#availability').count(), 0);
        // La barre `progress` sert au script de suivi ; à l'écran, seul le disque tourne
        assert.equal(await page.locator('#preparation-progress progress.cache').count(), 1);
        // Sans JavaScript, aucune phrase ne promet une mise à jour automatique ; le bouton d'arrêt reste masqué
        assert.equal((await page.locator('main').innerText()).includes('se met à jour d’elle-même'), false);
        assert.equal(await page.locator('#preparation-progress button').isHidden(), true);
      }
    }
    // Composition S18 : zone principale et colonne de synthèse, l'état reste en tête et le consentement suit la validation
    const zones = () => page.evaluate(() => {
      const box = node => node.getBoundingClientRect(), side = box(document.querySelector('.cote')), main = box(document.querySelector('.pile-l, .entretien'));
      const inside = (selector, zone) => Boolean(document.querySelector(selector)?.closest(zone));
      const follows = (a, b) => Boolean(document.querySelector(a).compareDocumentPosition(document.querySelector(b)) & Node.DOCUMENT_POSITION_FOLLOWING);
      return {right: side.left >= main.right, below: side.top >= main.bottom, above: side.bottom <= main.top,
              overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
              reply: inside('textarea#message', '.entretien'), validation: inside('#validation', '.cote'),
              example: inside('#exemple', '.pile-l'), correction: inside('details#corriger', '.pile-l'),
              consent: inside('.privacy-consent, .contribution', '.pile-bas'), side: document.querySelector('.cote').innerText,
              state: Boolean(document.querySelector('.cote .etat')),
              consentAfter: document.querySelector('.privacy-consent, .contribution') ? follows('#validation', '.privacy-consent, .contribution') : null};
    });
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      await page.goto(origin + '/render/availability-access');
      // Mes cas d'usage : l'état, puis le champ unique, puis les cas et la bande de la clé, sur une seule colonne
      const cas = await page.evaluate(() => {
        const follows = (a, b) => Boolean(document.querySelector(a).compareDocumentPosition(document.querySelector(b)) & Node.DOCUMENT_POSITION_FOLLOWING);
        return {overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                ordre: follows('.compo-zone .etat', 'form#nouveau') && follows('form#nouveau', '.ge-mes-cas'),
                deuxColonnes: document.querySelectorAll('.colonnes').length, bande: document.querySelector('.ge-bande a').getAttribute('href')};
      });
      assert.equal(cas.overflow, 0, `${width} besoin sans débordement`);
      assert.ok(cas.ordre, `${width} état, champ, puis cas`);
      assert.deepEqual([cas.deuxColonnes, cas.bande], [0, '/preparation/access'], `${width} bande de la clé`);
      let layout;
      await page.goto(origin + '/render/progress-clarification-none-False');
      layout = await zones();
      assert.equal(layout.overflow, 0, `${width} préparation sans débordement`);
      assert.ok(layout.reply && layout.state, `${width} réponse dans la conversation, état en synthèse`);
      assert.match(layout.side, /Coût de la préparation/, `${width} coût en synthèse`);
      assert.ok(width === 1440 ? layout.right : layout.below, `${width} position de la synthèse de préparation`);
      await page.goto(origin + '/render/consent-current');
      layout = await zones();
      assert.equal(layout.overflow, 0, `${width} validation sans débordement`);
      assert.ok(layout.example && layout.correction && layout.validation, `${width} exemple et correction en zone principale, validation en synthèse`);
      assert.match(layout.side, /Oui, c’est le travail à tester/, `${width} bouton de validation en synthèse`);
      assert.ok(layout.consent && layout.consentAfter, `${width} partage dans les blocs du bas, après la validation`);
      assert.ok(width === 1440 ? layout.right : layout.below, `${width} position de la synthèse de validation`);
    }
    // Parcours clavier de la préparation à 390 px, sans souris ni JavaScript : saisie, question de l'assistant, aperçu,
    // correction. Chaque arrêt du focus est visible dans l'écran, entier en largeur, non masqué par l'en-tête fixe
    await page.setViewportSize({width: 390, height: 844});
    const tabulerJusqua = async (cible, {retour = false, max = 80} = {}) => {
      for (let i = 0; i < max; i++) {
        await page.keyboard.press(retour ? 'Shift+Tab' : 'Tab');
        const arret = await page.evaluate(selecteur => {
          // Le champ unique et la réponse à l'assistant montrent leur focus sur leur cadre, pas sur la zone de texte
          const el = document.activeElement, r = el.getBoundingClientRect(), style = getComputedStyle(el.matches('textarea') && el.closest('.compo-champ, .reponse') || el);
          const dessus = document.elementFromPoint(r.left + r.width / 2, r.top + Math.min(r.height / 2, 20));
          return {cible: el.matches(selecteur), nom: el.tagName + '#' + el.id + ' ' + (el.textContent || '').trim().slice(0, 40)
              + ' [' + [r.left, r.top, r.right, r.bottom].map(Math.round) + ' / ' + innerWidth + '×' + innerHeight + ']',
            // Visible et entier en largeur ; un champ haut n'est montré qu'en partie, comme le fait le navigateur
            entier: r.width > 0 && r.left >= -1 && r.right <= innerWidth + 1 && r.bottom > 0 && r.top < innerHeight - 16,
            devant: !!dessus && (el === dessus || el.contains(dessus) || dessus.contains(el)),
            contour: style.outlineStyle !== 'none' || style.boxShadow !== 'none'};
        }, cible);
        assert.ok(arret.entier, 'focus hors de l’écran : ' + arret.nom);
        assert.ok(arret.devant, 'focus masqué : ' + arret.nom);
        assert.ok(arret.contour, 'focus sans contour : ' + arret.nom);
        if (arret.cible) return;
      }
      assert.fail('cible jamais atteinte au clavier : ' + cible);
    };
    // Saisie : le besoin, puis les précisions facultatives dans leur tiroir
    await page.goto(origin + '/render/availability-open');
    await tabulerJusqua('#request');
    await page.keyboard.type('Je reçois des notes de réunion en vrac et je veux un compte rendu clair.');
    await tabulerJusqua('details:has(#useful) > summary');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('details:has(#useful)').evaluate(d => d.open), true, 'tiroir des précisions ouvert au clavier');
    await tabulerJusqua('#useful');
    await page.keyboard.type('Une liste des décisions.');
    // Le bouton d'envoi précède le tiroir : retour en arrière au clavier
    await tabulerJusqua('form#nouveau button[type="submit"]', {retour: true});
    // Question de l'assistant : la réponse puis son envoi
    await page.goto(origin + '/render/question');
    await tabulerJusqua('#message');
    await page.keyboard.type('Les notes viennent d’une réunion d’équipe.');
    await tabulerJusqua('button[type="submit"]:not([disabled])');
    assert.match(await page.evaluate(() => document.activeElement.textContent), /Envoyer ma réponse/);
    // Aperçu : une pièce s'ouvre et se lit au clavier
    await page.goto(origin + '/render/apercu');
    await tabulerJusqua('#piece-1 > summary');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#piece-1').evaluate(d => d.open), true, 'pièce ouverte au clavier');
    // Correction : le tiroir s'ouvre, le message se tape, puis le bouton jaune devient « Corriger ce travail »
    await tabulerJusqua('#corriger > summary');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#corriger').evaluate(d => d.open), true, 'tiroir de correction ouvert au clavier');
    await tabulerJusqua('#corriger #message');
    await page.keyboard.type('Ajouter une action sans échéance.');
    await tabulerJusqua('button[form="form-corriger"]');
    assert.match(await page.evaluate(() => document.activeElement.textContent), /Corriger ce travail/);
  } finally {await context.close();}
});

test('privacy forms preserve consent scope, purpose tokens and expired states', async () => {
  const context = await browser.newContext({javaScriptEnabled: false});
  try {
    const page = await context.newPage();
    await page.goto(origin + '/render/example');
    const text = await page.textContent('main');
    assert.ok(text.indexOf('Les pièces') < text.indexOf('Oui, c’est le travail à tester'));
    assert.ok(text.indexOf('Oui, c’est le travail à tester') < text.indexOf('conserver cet exemple pendant 6 mois'));
    for (const phrase of ['historique local', 'besoin, messages et versions compris', 'copie complète de chaque cas ouvert',
      'a besoin de JavaScript', 'sans lui, seule la contribution', 'Cybrel', 'sans le publier',
      'Efface la copie de ce navigateur et demande la suppression sur le serveur'])
      assert.ok(text.includes(phrase), phrase);
    // La conservation 7 jours / 30 jours s'explique dans Mes données, plus sur la page du cas
    assert.equal(/effacé après 7 jours|effacée après 30 jours/.test(text), false);
    assert.equal(/Clé retirée après|11 jours|conserve votre copie locale/.test(text), false);
    assert.equal(await page.locator('script[src="/preparation/privacy.js"]').count(), 1);
    assert.ok(await page.locator('script:not([src])').count() > 0);
    await page.goto(origin + '/render/privacy-listing');
    // Le repère de session précède les cartes : `privacy.js` lit le premier, qui ne désigne aucun cas
    const activity = page.locator('[data-privacy-activity]').first();
    assert.equal(await activity.getAttribute('data-dossier-id'), null);
    assert.equal(await activity.getAttribute('data-csrf-token'), 'privacy-token');
    assert.equal(await page.locator('form#nouveau input[name="csrf_token"]').inputValue(), 'csrf');
    assert.equal(await page.locator('[data-privacy-post="contribution"]').count(), 0);
    // Chaque carte garde sa copie (privacy.js) et mène à la confirmation de suppression, avec son adresse de retour
    assert.deepEqual(await page.locator('.ge-cas [data-privacy-controls]').evaluateAll(cards => cards.map(card => card.dataset.dossierId)), ['d1', 'd2']);
    assert.deepEqual(await page.locator('.ge-cas a.btn-danger').evaluateAll(links => links.map(link => link.getAttribute('href'))),
      ['/preparation/dossiers/d1/delete?retour=/preparation', '/preparation/dossiers/d2/delete?retour=/preparation']);
    assert.equal(await page.locator('[data-privacy-post="delete"]').count(), 0);
    // Le consentement couvre le cas ouvert : une carte de la liste montre le bouton de copie sans copier au chargement
    assert.equal(await page.locator('.ge-cas [data-privacy-activity], .ge-cas [data-content-version]').count(), 0);
    assert.equal(await page.locator('.ge-cas [data-privacy-manual]').count(), 2);
    assert.equal(await page.locator('.alerte-exp').count(), 0, 'sans échéance connue, aucune alerte');
    await page.goto(origin + '/render/listing-expiring');
    assert.equal(await page.locator('.alerte-exp').count(), 1);
    assert.match(await page.locator('.alerte-exp').textContent(), /Bientôt effacé.*ce cas sera effacé le (lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche) \d{1,2} \p{L}+/u);
    assert.equal(await page.locator('.ge-cas:has(.alerte-exp) h3').textContent(), 'Trier des notes de réunion');
    await page.goto(origin + '/render/listing-deleted');
    assert.match(await page.locator('.etat').first().textContent(), /Le cas d’usage a été supprimé/);
    await page.goto(origin + '/render/case-delete');
    assert.equal(await page.locator('[data-privacy-post="delete"]').count(), 1);
    assert.equal(await page.locator('form.confirmer input[name="csrf_token"]').inputValue(), 'privacy-token');
    assert.equal(await page.locator('.annuler a').getAttribute('href'), '/preparation/dossiers/d1');
    assert.equal(await page.locator('form.confirmer button[type="submit"]').isDisabled(), false);
    await page.goto(origin + '/render/case-delete-closed');
    assert.equal(await page.locator('form.confirmer button[type="submit"]').isDisabled(), true);
    assert.equal(await page.locator('.annuler a').count(), 1);
    assert.equal(await page.locator('.annuler a').getAttribute('href'), '/preparation', 'adresse de retour refusée : Mes cas d’usage');
    // Formulaire désactivé (accès fermé ou cas déjà effacé) : l'annulation n'a plus d'objet, le retour à Mes cas d'usage la remplace
    assert.equal(await page.locator('.annuler').isVisible(), false);
    assert.equal(await page.locator('.apres-suppression a').isVisible(), true);
    assert.equal((await page.content()).includes('//evil.test'), false);
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
    // Mes données : conservation expliquée, historique local, puis les liens ; aucune page ne déborde
    const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 844});
      await page.goto(origin + '/render/data');
      const main = await page.locator('main').innerText();
      for (const phrase of ['Conservation automatique', 'Un cas d’usage est effacé après 7 jours sans activité', 'Votre clé OpenRouter est effacée après 30 jours sans activité',
        'Vos cas gardés dans ce navigateur', 'Mes cas d’usage et ma clé', 'Mes contributions', 'Confidentialité']) assert.ok(main.includes(phrase), `${width} ${phrase}`);
      assert.equal(await overflow(), 0, `${width} débordement de Mes données`);
      await page.goto(origin + '/render/contribution-active-2099');
      const contributions = await page.locator('main').textContent();
      assert.ok(contributions.includes('Contribution du 18 septembre 2026'), `${width} contribution`);
      assert.ok(contributions.includes('arrête seulement la contribution concernée'), `${width} explication du retrait`);
      assert.equal(await page.locator('.nav [aria-current="page"]').textContent(), 'Mes données', `${width} entrée de menu des contributions`);
      assert.equal(await overflow(), 0, `${width} débordement des contributions`);
    }
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
        assert.equal(await page.title(), (await page.locator('h1').textContent()) + ' · Evaly');
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
      assert.equal(await page.locator('.nav a[aria-current="page"]').textContent(), 'Accueil');
      assert.equal(await page.locator('a[href="/index.html"]').count(), 0);
      assert.ok(text.includes('Un seul manquement suffit'));
      assert.equal(text.includes('Comparaisons publiées'), false);
    }
    await page.goto(origin + '/render/listing');
    assert.equal(await page.locator('.nav a[aria-current="page"]').textContent(), 'Mes cas d’usage');
    assert.equal(await page.locator('ul.ge-liste a[href="/preparation/dossiers/d1"]').count(), 1);
    for (const name of ['waiting', 'honeypot']) {
      await page.goto(origin + '/render/' + name);
      assert.ok((await page.title()).startsWith('Demande enregistrée'));
      assert.equal(await page.locator('.etat[data-ton="att"]').count(), 1);
      assert.ok((await page.textContent('main')).includes('Votre envoi est bien enregistré'));
      assert.ok((await page.textContent('main')).includes('Suivre mon cas d’usage'));
      assert.equal(await page.locator('#openrouter-key').count(), 0);
    }
  } finally {await context.close();}
});
