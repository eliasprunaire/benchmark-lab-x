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
  pieces: [{name: '<script>pièce</script>', text: 'Pièce entière'}], qualification: 'QUALIFIED\nExemple décidable'}],
  campaigns: [{id: 'd1-c1', frozen_at: '2026-09-15T08:30:00Z', models: [{name: 'Modèle', verdict: 'SATISFAIT', cost: {amount: '0.02', currency: 'USD'},
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
from tests.test_privacy_views import example_view, browser_views
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
# Cas lancé : la page du cas et ses contrôles de confidentialité, sans choix de contribution, comme le fait views.render
launched = views.render(example_view(), 'csrf').decode()
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
print(json.dumps({**browser_views(),
                  'reference_search': reference_search, 'reference_notices': reference_notices,
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
    } else if (url.pathname === '/evaly.svg' || url.pathname === '/evaly.ico') {
      res.setHeader('Content-Type', url.pathname.endsWith('.svg') ? 'image/svg+xml' : 'image/vnd.microsoft.icon');
      res.end(readFileSync('benchmark_web/static/' + (url.pathname.endsWith('.svg') ? 'evaly.svg' : 'favicon.ico')));
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
    // Feuille du site chargée : la coupe du titre et la mise en page de la fiche se mesurent
    await page.setContent('<link rel="stylesheet" href="' + origin + '/preparation/style.css"><main data-privacy-history><p role="status" data-privacy-status></p><button data-privacy-action="clear">Effacer</button><button data-privacy-action="enable">Réactiver</button><div data-privacy-list></div></main>');
    await page.evaluate(() => privacy.mountPrivacy());
    assert.equal(await page.locator('[data-privacy-list] img, [data-privacy-list] script').count(), 0);
    assert.match(await page.locator('[data-privacy-list]').textContent(), /Réponse intégrale/);
    assert.match(await page.locator('[data-privacy-list]').textContent(), /Passage vérifié/);
    // Fiche : le besoin en titre, la version et le verdict compté sur la face, le nom Benchmark dans le corps
    const fiche = page.locator('[data-privacy-list] > details.hist-fiche');
    assert.equal(await fiche.locator(':scope > summary .hist-titre').textContent(), record.need);
    assert.match(await fiche.locator(':scope > summary .hist-meta').textContent(), /Version 1 · 1 benchmark/);
    assert.match(await fiche.locator(':scope > summary').textContent(), /Copie en lecture seule/);
    assert.equal(await fiche.locator(':scope > summary .mot[data-ton="ok"]').count(), 1);
    await page.locator('[data-privacy-list] > details > summary').click();
    // Fiche ouverte : titre toujours coupé, demande entière dans le corps, sections nommées, actions en bas
    assert.equal(await fiche.locator(':scope > summary .hist-titre').evaluate(el => getComputedStyle(el).webkitLineClamp), '2');
    assert.equal(await fiche.locator('.hist-demande').textContent(), record.need);
    const sections = await fiche.locator('.hist-corps > details > summary').allTextContents();
    assert.deepEqual(sections.map(text => text.trim()), ['Vos messages (1)', 'Version 1 de l’exempleRéussi', 'Benchmark 1 du 15 septembre 2026 à 08:30:00 UTC']);
    assert.equal(await fiche.locator('.hist-corps > :last-child').getAttribute('class'), 'hist-actions');
    assert.deepEqual(await fiche.locator('.hist-actions > button').allTextContents(), ['Télécharger ce cas (JSON)', 'Effacer et ne plus garder ce cas']);
    assert.match(await fiche.locator('.hist-controle').textContent(), /Exemple décidable/);
    assert.match(await fiche.locator('.hist-modele > summary').textContent(), /Modèle.*Satisfait.*0\.02 USD/);
    assert.doesNotMatch(await fiche.textContent(), /Comparaison|Vérification de l’exemple|QUALIFIED|d1-c1/);
    const download = page.waitForEvent('download');
    await page.getByRole('button', {name: 'Télécharger ce cas (JSON)'}).click();
    assert.equal((await download).suggestedFilename(), 'evaly-d1.json');
    const file = await (await download).path();
    assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), record);
    assert.equal(await page.getByRole('searchbox', {name: 'Filtrer les fiches'}).count(), 0, 'pas de filtre sous sept fiches');
    // Au-delà de six fiches : les six premières, un filtre sans accents et un bouton pour tout afficher
    for (let i = 2; i <= 7; i++) {
      // Copies anciennes : benchmark sans date, aucun message ; la fiche n'affiche pas de section vide
      fixture(1, 'Cas numéro ' + i, {dossier_id: 'd' + i, messages: [], campaigns: [{...fresh().campaigns[0], frozen_at: undefined}]});
      manifest.dossier_id = 'd' + i;
      await page.evaluate(id => historyStore.archive(id, 1), 'd' + i);
    }
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.waitForFunction(() => document.querySelectorAll('[data-privacy-list] > details').length === 7);
    const visibles = () => page.locator('[data-privacy-list] > details:visible').count();
    assert.equal(await visibles(), 6);
    const filtre = page.getByRole('searchbox', {name: 'Filtrer les fiches'});
    await filtre.fill('NUMERO 7');
    assert.equal(await visibles(), 1);
    assert.equal(await page.locator('[data-privacy-list] > details:visible .hist-titre').textContent(), 'Cas numéro 7');
    await filtre.fill('');
    await page.getByRole('button', {name: 'Afficher toutes les fiches (7)'}).click();
    assert.equal(await visibles(), 7);
    const ancienne = page.locator('[data-privacy-list] > details', {hasText: 'Cas numéro 2'});
    assert.deepEqual((await ancienne.locator('.hist-corps > details > summary').allTextContents()).map(text => text.trim()),
      ['Version 1 de l’exempleRéussi', 'Benchmark 1']);
    // Toute suppression se confirme sur place : Annuler ne touche à rien et rend le focus au bouton
    await page.getByRole('button', {name: 'Effacer', exact: true}).click();
    assert.match(await page.getByRole('group', {name: /Effacer toutes les copies/}).textContent(), /ne pourront pas être récupérées/);
    assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Annuler');
    await page.getByRole('button', {name: 'Annuler'}).click();
    assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Effacer');
    assert.equal((await page.evaluate(() => historyStore.list())).length, 7);
    await page.getByRole('button', {name: 'Effacer', exact: true}).click();
    await page.getByRole('button', {name: 'Oui, tout effacer'}).click();
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

// Carte « Historique local » : ses actions et sa liste ne laissent aucun vide quand elles n'ont rien de visible
const affichage = page => page.evaluate(() => ['[data-privacy-history] > .actions', '[data-privacy-list]']
  .map(selector => getComputedStyle(document.querySelector(selector)).display));
test('an opened case is kept in local history by default under CSP, a list writes nothing before a click, and fits mobile', async () => {
  fixture(); posts = [];
  const copie = 'Copie gardée dans ce navigateur. Toute personne qui utilise ce profil de navigateur peut la lire.';
  const context = await browser.newContext({viewport: {width: 1200, height: 900}});
  try {
    const page = await context.newPage(); page.setDefaultTimeout(3000);
    const failures = [];
    page.on('pageerror', error => failures.push(error.message));
    page.on('console', message => {if (/Content Security Policy/.test(message.text())) failures.push(message.text());});
    const databases = () => page.evaluate(async () => (await indexedDB.databases()).map(db => db.name));
    // Historique actif par défaut : la page versionnée d'un cas ouvert en garde une copie, sans action ni envoi
    await page.goto(origin + '/render/example');
    await page.waitForFunction(texte => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent === texte, copie);
    assert.equal(posts.length, 0);
    assert.deepEqual(await databases(), ['bench-x-history']);
    assert.equal(await page.locator('[data-privacy-action="enable-case"]').evaluate(button => button.hidden), true);
    // Contribution retirée : la page d'un cas ne propose plus aucune case de partage
    assert.equal(await page.locator('.privacy-consent, [data-privacy-post="contribution"], input[type="checkbox"]').count(), 0);
    mkdirSync('reports/privacy-browser', {recursive: true});
    await page.goto(origin + '/render/data');
    await page.waitForFunction(() => document.querySelector('[data-privacy-list] > details'));
    // Copies gardées : « Tout effacer » et la liste restent affichés
    assert.equal((await affichage(page)).includes('none'), false, 'actions et copies visibles');
    assert.equal(await page.getByRole('button', {name: 'Tout effacer et arrêter l’historique'}).isVisible(), true);
    await page.locator('[data-privacy-list] > details > summary').click();
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path: 'reports/privacy-browser/data-mobile.png', fullPage: true});
    assert.deepEqual(failures, []);
  } finally {await context.close();}
  // Lignes de Mes données dans un navigateur sans historique : aucune base avant un clic, puis un message visible, réussite ou échec
  const vierge = await browser.newContext({viewport: {width: 1200, height: 900}});
  try {
    const page = await vierge.newPage(); page.setDefaultTimeout(3000);
    const databases = () => page.evaluate(async () => (await indexedDB.databases()).map(db => db.name));
    await page.goto(origin + '/render/privacy-listing');
    const carte = id => page.locator('[data-privacy-controls][data-dossier-id="' + id + '"]');
    await carte('d1').locator('[data-privacy-action="archive"]').waitFor();
    assert.deepEqual(await page.locator('.table-cas [data-privacy-status]').allTextContents(), ['', '']);
    assert.deepEqual(await databases(), []);
    // Historique par défaut, sans copie : seule l'action d'arrêt est visible, aucune liste vide sous le statut
    await page.waitForFunction(() => document.querySelector('[data-privacy-history] > [data-privacy-status]').textContent === 'Aucun cas gardé dans ce navigateur pour l’instant.');
    assert.deepEqual(await affichage(page), ['flex', 'none'], 'état par défaut : arrêt proposé, liste vide masquée');
    // Tablettes : cartes jusqu'à 899 px, table dès 900 px ; ni défilement de la table ni bouton coupé
    for (const width of [700, 768, 899, 900]) {
      await page.setViewportSize({width, height: 900});
      const mesure = await page.evaluate(() => {
        const cadre = document.querySelector('.table-defile');
        const bord = cadre.getBoundingClientRect();
        const coupes = [...cadre.querySelectorAll('a, button')].filter(cible => cible.getClientRects().length).map(cible => cible.getBoundingClientRect())
          .filter(box => box.left < bord.left - 0.5 || box.right > bord.right + 0.5).length;
        return {page: document.documentElement.scrollWidth - innerWidth, table: cadre.scrollWidth - cadre.clientWidth, coupes,
                ligne: getComputedStyle(document.querySelector('.table-cas tbody tr')).display};
      });
      assert.deepEqual(mesure, {page: 0, table: 0, coupes: 0, ligne: width < 900 ? 'block' : 'table-row'}, `${width} px`);
    }
    await page.setViewportSize({width: 1200, height: 900});
    await carte('d1').locator('[data-privacy-action="archive"]').click();
    const statut1 = carte('d1').locator('[data-privacy-status]');
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls][data-dossier-id="d1"] [data-privacy-status]').textContent !== ''
      && !document.querySelector('[data-privacy-controls][data-dossier-id="d1"] [data-privacy-action="archive"]').disabled);
    assert.equal(await statut1.textContent(), copie);
    assert.equal(await statut1.isVisible(), true, 'réussite visible à l’écran');
    assert.deepEqual(await databases(), ['bench-x-history']);
    // d2 n'a pas de copie servie par la fixture : l'échec se lit à l'écran, pas seulement par un lecteur d'écran
    await carte('d2').locator('[data-privacy-action="archive"]').click();
    const statut2 = carte('d2').locator('[data-privacy-status]');
    await page.waitForFunction(() => {
      const texte = document.querySelector('[data-privacy-controls][data-dossier-id="d2"] [data-privacy-status]').textContent;
      return texte !== '' && texte !== 'Copie en cours dans ce navigateur…';
    });
    assert.doesNotMatch(await statut2.textContent(), /Copie gardée/);
    assert.equal(await statut2.isVisible(), true, 'échec visible à l’écran');
  } finally {await vierge.close();}
  // Mes données sans base locale : l'historique s'arrête avant la première copie, sans écrire de cas, puis se reprend
  const avantCopie = await browser.newContext({viewport: {width: 1200, height: 900}});
  try {
    const page = await avantCopie.newPage(); page.setDefaultTimeout(3000);
    const databases = () => page.evaluate(async () => (await indexedDB.databases()).map(db => db.name));
    const statut = texte => page.waitForFunction(attendu => document.querySelector('[data-privacy-history] > [data-privacy-status]').textContent.startsWith(attendu), texte);
    const envois = posts.length;
    await page.goto(origin + '/render/data');
    await statut('Aucun cas gardé dans ce navigateur pour l’instant.');
    assert.deepEqual(await databases(), []);
    const arreter = page.getByRole('button', {name: 'Tout effacer et arrêter l’historique'});
    const reprendre = page.getByRole('button', {name: 'Reprendre l’historique local'});
    assert.equal(await arreter.isVisible(), true, 'arrêt proposé avant la première copie');
    assert.equal(await reprendre.isVisible(), false);
    await arreter.click();
    await page.getByRole('button', {name: 'Oui, tout effacer'}).click();
    await statut('Historique local effacé et en pause. Reprenez-le pour garder de nouvelles copies.');
    // L'arrêt crée seulement l'état désactivé : la base existe, aucun cas n'y est écrit
    assert.deepEqual(await databases(), ['bench-x-history']);
    assert.equal(await page.evaluate(() => new Promise((resolve, reject) => {
      const opening = indexedDB.open('bench-x-history');
      opening.onerror = () => reject(opening.error);
      opening.onsuccess = () => {
        const tx = opening.result.transaction(['archives', 'staging']);
        const counts = ['archives', 'staging'].map(name => new Promise(done => {tx.objectStore(name).count().onsuccess = event => done(event.target.result);}));
        Promise.all(counts).then(([archives, staging]) => {opening.result.close(); resolve(archives + staging);});
      };
    })), 0, 'aucun cas écrit');
    assert.equal(posts.length, envois, 'aucun envoi');
    assert.equal(await reprendre.isVisible(), true, 'reprise disponible après l’arrêt');
    await reprendre.click();
    await statut('Aucun cas gardé dans ce navigateur pour l’instant.');
    assert.equal(await reprendre.isVisible(), false);
  } finally {await avantCopie.close();}
});

test('a case erased from Mes données can be re-enabled, and only Resume lifts a paused local history', async () => {
  fixture(); posts = [];
  const context = await browser.newContext();
  try {
    const page = await context.newPage(); page.setDefaultTimeout(3000);
    const statutCas = texte => page.waitForFunction(debut => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent.startsWith(debut), texte);
    const historique = texte => page.waitForFunction(attendu => document.querySelector('[data-privacy-history] > [data-privacy-status]').textContent.startsWith(attendu), texte);
    await page.goto(origin + '/render/example');
    await statutCas('Copie gardée');
    // Un cas effacé de Mes données reste hors de l'historique ; son bouton le réactive pour lui seul
    await page.goto(origin + '/render/data');
    await page.waitForFunction(() => document.querySelector('[data-privacy-list] > details'));
    await page.locator('[data-privacy-list] > details > summary').click();
    await page.getByRole('button', {name: 'Effacer et ne plus garder ce cas'}).click();
    await page.getByRole('button', {name: 'Oui, effacer ce cas'}).click();
    await page.waitForFunction(() => !document.querySelector('[data-privacy-list]').textContent);
    await page.goto(origin + '/render/example');
    await statutCas('Historique local en pause : ce cas n’est plus gardé dans ce navigateur.');
    const reactiver = page.locator('[data-privacy-action="enable-case"]');
    assert.equal(await reactiver.evaluate(button => button.hidden), false);
    await page.locator('details.privacy-controls > summary').click();
    await reactiver.click();
    await page.waitForFunction(() => document.querySelector('[data-privacy-action="enable-case"]').hidden
      && !document.querySelector('[data-privacy-controls] [data-privacy-action="archive"]').hidden);
    // Arrêt explicite de tout l'historique : il reste arrêté, même après le retour sur la page d'un cas
    await page.goto(origin + '/render/data');
    await page.getByRole('button', {name: 'Tout effacer et arrêter l’historique'}).click();
    await page.getByRole('button', {name: 'Oui, tout effacer'}).click();
    await historique('Historique local effacé et en pause. Reprenez-le pour garder de nouvelles copies.');
    assert.equal(await page.getByRole('button', {name: 'Reprendre l’historique local'}).isVisible(), true);
    assert.deepEqual(await affichage(page), ['flex', 'none'], 'pause : actions visibles, liste vide masquée');
    await page.goto(origin + '/render/example');
    await statutCas('Historique local en pause');
    assert.deepEqual(await page.evaluate(async () => {
      const store = await (await import('/preparation/privacy.js')).openHistory({create: false});
      try {return {state: await store.state(), list: await store.list()};} finally {store.close();}
    }), {state: {enabled: false, chosen: true}, list: []});
    // Seul « Reprendre l’historique local » lève la pause ; le bouton disparaît ensuite
    await page.goto(origin + '/render/data');
    await page.getByRole('button', {name: 'Reprendre l’historique local'}).click();
    await historique('Aucun cas gardé dans ce navigateur pour l’instant.');
    assert.equal(await page.getByRole('button', {name: 'Reprendre l’historique local'}).isVisible(), false);
  } finally {await context.close();}
});

test('withdrawing a contribution keeps local copies active and erasable from Mes données', async () => {
  fixture();
  const context = await browser.newContext();
  try {
    const page = await pageFor(context);
    await page.evaluate(() => historyStore.archive('d1', 1));
    await page.setContent('<details class="confirmation-repliee"><summary>Retirer mon consentement</summary><div class="confirmation"><p>Retirer votre consentement ? La copie de contribution gardée sur le serveur sera effacée.</p><form data-privacy-post="withdraw" action="/preparation/contributions/c1/withdraw"><input type="hidden" name="csrf_token" value="purpose"><button>Oui, retirer mon consentement</button><p data-privacy-status></p></form></div></details>');
    await page.evaluate(() => privacy.mountPrivacy());
    // Le retrait efface la copie de contribution du serveur : rien n'est envoyé avant la confirmation, posée une seule fois
    const avant = posts.length;
    await page.locator('details.confirmation-repliee > summary').click();
    assert.match(await page.locator('details.confirmation-repliee').textContent(), /copie de contribution/);
    assert.equal(await page.getByRole('button', {name: 'Oui, retirer mon consentement'}).count(), 1);
    assert.equal(posts.length, avant);
    await Promise.all([page.waitForEvent('load'), page.getByRole('button', {name: 'Oui, retirer mon consentement'}).click()]);
    assert.deepEqual(posts.at(-1), {path: '/preparation/contributions/c1/withdraw', body: {csrf_token: 'purpose'}});
    await page.goto(origin + '/render/data');
    await page.waitForFunction(() => document.querySelector('[data-privacy-list] > details'));
    assert.match(await page.locator('[data-privacy-status]').first().textContent(), /Cas gardés dans ce navigateur : 1/);
    await page.locator('[data-privacy-list] > details > summary').click();
    await page.getByRole('button', {name: 'Effacer et ne plus garder ce cas'}).click();
    assert.equal(await page.locator('[data-privacy-list] > details').count(), 1, 'rien n’est effacé avant la confirmation');
    await page.getByRole('button', {name: 'Oui, effacer ce cas'}).click();
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
      if (action === 'delete') {
        await page.getByRole('button', {name: 'Confirmer'}).click();
        await page.waitForFunction(() => document.querySelector('[data-privacy-status]').textContent.includes('lors du prochain nettoyage planifié'));
        assert.equal(page.url(), origin + '/');
      } else {
        // La question est dans le balisage du serveur : le script envoie au premier clic, sans seconde question
        await Promise.all([page.waitForEvent('load'), page.getByRole('button', {name: 'Confirmer'}).click()]);
      }
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
    assert.match(status, /La suppression sur le serveur n’est pas confirmée/);
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
      assert.match(status, statusCode === 200 ? /lors du prochain nettoyage planifié/ : /La suppression sur le serveur n’est pas confirmée/);
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
        if (name === 'home') {
          assert.equal(await page.locator('.accroche').evaluate(lead => getComputedStyle(lead).borderLeftStyle), 'none',
            `${where} l’accroche est un paragraphe, sans filet latéral (DESIGN.md)`);
          // Accroche, atouts, actions : enfants directs, empilés sous 860 px, deux colonnes de 861 à 1099 px, dès 1100 px accroche et atouts à parts égales sur une ligne puis actions centrées dessous
          const blocs = () => page.evaluate(() => ['.une-bas .accroche', '.une-bas > .atouts', '.une-bas > .actions'].map(selector => {
            const box = document.querySelector(selector).getBoundingClientRect();
            return {gauche: box.left, droite: box.right, haut: box.top, bas: box.bottom};
          }));
          assert.deepEqual(await page.locator('.une-bas > *').evaluateAll(blocks => blocks.map(block => block.className || block.firstElementChild.className)),
            ['texte-l accroche', 'atouts', 'actions'], `${where} ordre du DOM`);
          // Centres horizontaux du bouton principal et du lien de retour, bord gauche du bouton
          const boutons = () => page.evaluate(() => ['.une-bas .actions .btn', '.une-bas .actions .lien'].map(selector => {
            const box = document.querySelector(selector).getBoundingClientRect();
            return {centre: box.left + box.width / 2, gauche: box.left, haut: box.top, bas: box.bottom};
          }));
          let [accroche, atouts, actions] = await blocs();
          assert.ok(accroche.bas <= atouts.haut && atouts.bas <= actions.haut, `${where} empilés à 390 px`);
          let [bouton, retour] = await boutons();
          assert.ok(Math.abs(bouton.gauche - actions.gauche) <= 1 && retour.haut >= bouton.bas, `${where} 390 px : bouton à gauche, lien dessous`);
          await page.setViewportSize({width: 1000, height: 900});
          [accroche, atouts, actions] = await blocs();
          assert.ok(atouts.haut >= accroche.bas && Math.abs(atouts.gauche - accroche.gauche) <= 1 && actions.gauche >= atouts.droite,
            `${where} 1000 px : accroche puis atouts à gauche, actions à droite`);
          [bouton, retour] = await boutons();
          assert.ok(Math.abs(bouton.gauche - actions.gauche) <= 1 && retour.haut >= bouton.bas, `${where} 1000 px : bouton à gauche, lien dessous`);
          // Dès 1100 px : accroche et atouts à parts égales sur une ligne, puis le bouton et le lien centrés dans un bloc à part, plus aéré
          for (const width of [1100, 1440]) {
            await page.setViewportSize({width, height: 900});
            [accroche, atouts, actions] = await blocs();
            const ligne = await page.evaluate(() => {
              const [a, b] = ['.une-bas > div', '.une-bas > .atouts'].map(s => document.querySelector(s).getBoundingClientRect().width);
              return a / (a + b);
            });
            assert.ok(Math.abs(ligne - 0.5) <= 0.02, `${where} ${width} px : accroche ${ligne.toFixed(3)} de la ligne`);
            assert.ok(atouts.gauche - accroche.droite >= 24 && Math.abs(accroche.haut - atouts.haut) <= 1, `${where} ${width} px : accroche puis atouts`);
            assert.ok(actions.haut - Math.max(accroche.bas, atouts.bas) >= 40, `${where} ${width} px : actions dessous, détachées`);
            [bouton, retour] = await boutons();
            const milieu = (accroche.gauche + atouts.droite) / 2;
            assert.ok(Math.abs(bouton.centre - milieu) <= 1 && Math.abs(retour.centre - milieu) <= 1 && retour.haut >= bouton.bas,
              `${where} ${width} px : bouton (${bouton.centre.toFixed(1)}) et lien (${retour.centre.toFixed(1)}) centrés sur ${milieu.toFixed(1)}, l'un sous l'autre`);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${where} ${width} px débordement`);
          }
          // Le haut du jaune cache les jambages du mot précédent (les « p » de « rapports » sur « tableaux ») sans rogner les lettres hautes
          const fente = await page.evaluate(() => {
            const cadre = document.querySelector('.fente'), mots = [...cadre.querySelectorAll('.defile > span')];
            const style = getComputedStyle(mots[0]), rang = mots[0].getBoundingClientRect().height / Math.cos(1.5 * Math.PI / 180);
            const ctx = document.createElement('canvas').getContext('2d');
            ctx.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
            ctx.fontStretch = 'expanded';
            const ref = ctx.measureText('x'), base = (parseFloat(style.lineHeight) - ref.fontBoundingBoxAscent - ref.fontBoundingBoxDescent) / 2 + ref.fontBoundingBoxAscent;
            const mesure = mots.map(m => ctx.measureText(m.textContent));
            const coupe = getComputedStyle(cadre).clipPath.match(/inset\(([\d.]+)px/);
            return {intrusion: base + Math.max(...mesure.map(m => m.actualBoundingBoxDescent)) - parseFloat(style.lineHeight),
                    lettres: base - Math.max(...mesure.map(m => m.actualBoundingBoxAscent)), coupe: coupe ? parseFloat(coupe[1]) : 0, rang};
          });
          assert.ok(fente.coupe > fente.intrusion && fente.coupe < fente.lettres, `${where} jaune : ${JSON.stringify(fente)}`);
          mkdirSync('reports/privacy-browser', {recursive: true});
          await page.screenshot({path: `reports/privacy-browser/home-1440-${colorScheme}.png`});
          await page.setViewportSize({width: 390, height: 844});
        }
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
      /^\d+ réponses? vérifiées? sur \d+ essais? prévus?$/.test(title.nextElementSibling.textContent))), [true, true]);
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
    // Contribution retirée : la décision principale reste, sans choix de partage
    await page.goto(origin + '/render/example');
    assert.equal(await page.evaluate(() => {
      const validate = [...document.querySelectorAll('#validation button')].find(button => /Oui, c’est le travail à tester/.test(button.textContent));
      return Boolean(validate) && !document.querySelector('[data-privacy-post="contribution"]');
    }), true, 'décision principale sans choix de partage');
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
    // Contribution retirée : aucun autre statut de partage sur la page
    assert.equal(await page.locator('.privacy-consent').count(), 0);
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
    assert.equal(await page.locator('.privacy-consent').count(), 0);
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
      // Une page « Ma clé OpenRouter », un verbe par action
      assert.equal(await page.title(), 'Ma clé OpenRouter · Evaly', state);
      assert.equal(/Gérer ma clé|Ajouter ma clé OpenRouter/.test(await page.textContent('body')), false, state);
      if (state !== 'unavailable')
        assert.equal(await page.locator('aside.cote h2').textContent(), state === 'disconnected' ? 'Ajouter ma clé' : 'Remplacer ma clé', state);
      assert.equal(await page.locator('form[action="/preparation/access/key"]').count(), state === 'unavailable' ? 0 : 1);
      if (state === 'disconnected') assert.equal(await page.locator('input[name="return"]').inputValue(), '/preparation/access');
      if (state === 'connected') {
        const text = await page.textContent('main');
        assert.ok(text.includes('Solde restant') && text.includes('12,50') && !text.includes('selon OpenRouter'));
        assert.ok(text.includes('Plafond de la clé') && text.includes('25'));
        assert.ok(text.includes('Retirer la clé de ce navigateur'));
        // Retirer la clé se confirme, même sans JavaScript : le bouton d'envoi reste replié derrière la question
        const confirmationCle = page.locator('details.confirmation-repliee:visible').first();
        const retrait = confirmationCle.getByRole('button', {name: 'Oui, retirer la clé'});
        assert.equal(await retrait.isVisible(), false);
        await confirmationCle.locator('summary').click();
        assert.equal(await retrait.isVisible(), true);
        assert.match(await confirmationCle.textContent(), /Retirer la clé de ce navigateur \?/);
      }
      if (state === 'invalid') assert.ok((await page.textContent('main')).includes('OpenRouter refuse cette clé'));
      await page.goto(origin + '/render/launch-' + state);
      assert.equal(await page.locator('form[action="/preparation/access/start"]').count(), 0);
      if (state === 'unavailable') {
        assert.equal(await page.locator('form[action="/preparation/access/key"], form[action="/preparation/access/disconnect"]').count(), 0);
        assert.ok((await page.textContent('main')).includes('Impossible d’enregistrer une clé'));
      }
      if (state === 'connected') {
        assert.equal(await page.locator('form[action="/preparation/access/disconnect"]').count(), 1);
      }
    }
    for (const [name, text] of [['estimate-known', '0,125 USD'],
      ['estimate-unknown', 'Réservé pour la vérification des réponses'],
      ['launch-judgment-blocked', 'Vérification des réponses indisponible']]) {
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
      assert.match((await page.locator('.releves').innerText()).replace(/\s+/g, ' '), /SOLDE RESTANT 12,50\s*USD PLAFOND DE LA CLÉ 25\s*USD/i, `${width} relevés du solde`);
      assert.equal(await page.locator('.pile-l > .etat').count(), 1, `${width} état avant le formulaire`);
      assert.ok(layout.side.includes('Vous choisissez chez OpenRouter son plafond de dépense, ou son absence.'), `${width} colonne de la clé`);
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
      if (status === 'BLOCKED') {
        assert.ok(text.includes('Budget insuffisant'));
        // Le lien vers la clé porte le nom de sa page
        assert.equal(await page.locator('main a[href="/preparation/access"]', {hasText: 'Ma clé OpenRouter'}).count(), 1);
        assert.equal(text.includes('Gérer ma clé'), false);
      }
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
        // L'en-tête marque Benchmarks publics sur la racine du répertoire : menu mobile ouvert à 390 px, menu de grand écran à 1440 px
        if (width === 390) await page.locator('.nav-mobile summary').click();
        assert.deepEqual(await page.locator((width === 390 ? '.nav-mobile' : '.nav') + ' a[aria-current="page"]:visible').allTextContents(),
          ['Benchmarks publics'], `${width} entrée de menu du répertoire`);
        await page.goto(origin + '/render/approved-mteb');
        assert.equal(await page.locator('[aria-current="page"]').count(), 0, `${width} aucune entrée de menu sur une fiche`);
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
      assert.equal(await page.locator('main').getByRole('link', {name: 'Décrire mon cas d’usage', exact: true}).count(), 0);
      if (javaScriptEnabled) {
        // Avec JavaScript : la recherche filtre dès 3 caractères, sans rechargement ; un filtre cliqué garde la position
        assert.equal(await page.locator('script').count(), 1);
        await page.evaluate(() => {window.sansRechargement = true;});
        await page.getByLabel('Rechercher un benchmark public', {exact: true}).fill('co');
        await page.waitForTimeout(400);
        assert.equal(new URL(page.url()).searchParams.get('q'), null, 'filtrage sous 3 caractères');
        await page.getByLabel('Rechercher un benchmark public', {exact: true}).fill('corriger');
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
        await page.getByLabel('Rechercher un benchmark public', {exact: true}).fill('corriger');
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
      await page.getByLabel('Rechercher un benchmark public', {exact: true}).fill('introuvable');
      await Promise.all([page.waitForNavigation(), page.keyboard.press('Enter')]);
      assert.match(await page.textContent('main'), /Aucun benchmark public ne correspond à cette recherche/);
      assert.equal(await page.locator('main').getByRole('link', {name: 'Décrire mon cas d’usage', exact: true}).getAttribute('href'), '/preparation');
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
      for (const value of ['TypeSafe', 'Jev', 'formulation', 'Aucun cas d’usage privé',
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
      for (const name of ['MathArena', 'LiveCodeBench', 'SWE-bench', 'MTEB', 'Artificial Analysis, performances API', 'MMLU-Pro', 'GPQA',
                          'Humanity\'s Last Exam', 'ARC-AGI', 'MATH', 'FrontierMath', 'GAIA', 'τ-bench', 'OSWorld'])
        assert.ok(approved.includes(name), name);
      assert.equal(/HELM/i.test(approved), false);
      assert.equal(approved.includes('Ayo'), false);
      assert.ok(approved.includes('La dernière relecture date du 9 novembre 2026.'));
      assert.ok(approved.includes('Ce contrôle est fait à part de la relecture.'));
      assert.equal(await page.locator('main img').count() + await scriptsEtrangers(page, 'main script'), 0);
      assert.equal(await scriptsEtrangers(page), 0);
      const categories = await page.locator('main .segments-liens a[href*="category="]').allTextContents();
      assert.ok(categories.includes('Recherche documentaire'));
      assert.ok(categories.includes('Performances des services'));
      assert.ok(categories.includes('Connaissances et raisonnement'));
      assert.ok(categories.includes('Agents'));
      assert.equal(categories.some(name => /helm/i.test(name)), false);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      // La catégorie la plus longue passe à la ligne : sa puce ne chevauche ni le titre, ni la description, ni la flèche
      for (const width of [390, 768, 861, 1024, 1280, 1440]) {
        await page.setViewportSize({width, height: 844});
        const mesures = await page.evaluate(names => [...document.querySelectorAll('main .g-liste-ext .g-fiche')]
          .filter(card => names.includes(card.querySelector('h3').textContent.trim()))
          .map(card => {
            const r = el => el.getBoundingClientRect();
            const croise = (a, b) => Math.min(a.right, b.right) > Math.max(a.left, b.left) && Math.min(a.bottom, b.bottom) > Math.max(a.top, b.top);
            const puce = r(card.querySelector('.g-fiche-haut .puce'));
            return {nom: card.querySelector('h3').textContent.trim(),
                    chevauche: ['h3', '.g-mesure', '.g-ouvrir'].filter(s => croise(puce, r(card.querySelector(s))))};
          }), ['MMLU-Pro', 'GPQA', 'Humanity\'s Last Exam', 'ARC-AGI']);
        assert.equal(mesures.length, 4, `${width} cartes de la catégorie`);
        assert.deepEqual(mesures.filter(m => m.chevauche.length), [], `${width} puce de catégorie`);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0, `${width} débordement de page`);
      }
      await page.setViewportSize({width: 390, height: 844});
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

test('an existing copied example stays readable without JavaScript, by keyboard at 390 px', async () => {
  for (const javaScriptEnabled of [false, true]) {
    const context = await browser.newContext({javaScriptEnabled, viewport: {width: 390, height: 844}});
    try {
      const page = await context.newPage();
      page.setDefaultTimeout(3000);
      const dialogs = [], errors = [];
      page.on('dialog', dialog => {dialogs.push(dialog.message()); dialog.dismiss();});
      page.on('pageerror', error => errors.push(error.message));
      // Copie créée avant l'alpha : « Exemple source » et son ruban, la préparation reste possible
      await page.goto(origin + '/render/copy-dossier');
      const copied = await page.textContent('main');
      assert.match(copied, /Exemple source/);
      assert.match(copied, /Tableau <script>alert\(1\)<\/script>/);
      assert.match(copied, /Pièce source <script>alert\(1\)<\/script>/);
      assert.match(copied, /Erreur signalée <script>alert\(2\)<\/script>/);
      assert.match(copied, /Aucune qualification n’est publiée pour cet exemple source/);
      assert.match(copied, /la copie reste consultable sans elle/);
      // Copie sans exemple préparé : son état et la consultation sans clé sont dits au premier écran
      assert.match(copied, /Copie d’un exemple publié/);
      assert.equal(/Épreuve source|Copie d’épreuve/.test(copied), false);
      assert.match(copied, /Version 1/);
      assert.equal(copied.includes('Exemple inventé'), false);
      assert.match(copied, /Votre copie est enregistrée\./);
      assert.match(copied, /sans clé ni appel aux modèles/);
      assert.match(copied, /servira à préparer votre exemple/);
      assert.equal(copied.includes('Ajoutez votre clé OpenRouter pour préparer un exemple.'), false);
      assert.match(copied, /aucun appel modèle effectué/);
      await page.reload();
      const reloadedCopy = await page.textContent('main');
      assert.match(reloadedCopy, /Copie d’un exemple publié/);
      assert.match(reloadedCopy, /Exemple source/);
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
    // Un verbe : la page de destination porte déjà « OpenRouter »
    assert.equal(await page.locator('main a[href="/preparation/access"]', {hasText: /^\s*Ajouter ma clé\s*$/}).count(), 1);
    assert.equal(ordering.includes('Ajouter ma clé OpenRouter'), false);
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
        assert.equal(await page.locator('#request-help').innerHTML(),
          '40 caractères au minimum.<br>N’ajoutez aucune donnée personnelle, ni aucun document réel, même anonymisé.');
        assert.equal(await page.locator('.compo-suite').count(), 0);
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
    // Composition S18 : zone principale et colonne de synthèse, l'état reste en tête, sans bloc de partage
    const zones = () => page.evaluate(() => {
      const box = node => node.getBoundingClientRect(), side = box(document.querySelector('.cote')), main = box(document.querySelector('.pile-l, .entretien'));
      const inside = (selector, zone) => Boolean(document.querySelector(selector)?.closest(zone));
      const follows = (a, b) => Boolean(document.querySelector(a).compareDocumentPosition(document.querySelector(b)) & Node.DOCUMENT_POSITION_FOLLOWING);
      return {right: side.left >= main.right, below: side.top >= main.bottom, above: side.bottom <= main.top,
              overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
              reply: inside('textarea#message', '.entretien'), validation: inside('#validation', '.cote'),
              example: inside('#exemple', '.pile-l'), correction: inside('details#corriger', '.pile-l'),
              consent: Boolean(document.querySelector('.privacy-consent, .contribution')), side: document.querySelector('.cote').innerText,
              state: Boolean(document.querySelector('.cote .etat'))};
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
      await page.goto(origin + '/render/case-current');
      layout = await zones();
      assert.equal(layout.overflow, 0, `${width} validation sans débordement`);
      assert.ok(layout.example && layout.correction && layout.validation, `${width} exemple et correction en zone principale, validation en synthèse`);
      assert.match(layout.side, /Oui, c’est le travail à tester/, `${width} bouton de validation en synthèse`);
      assert.equal(layout.consent, false, `${width} aucun bloc de partage`);
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
            contour: style.outlineStyle !== 'none' || style.boxShadow !== 'none',
            // Le champ unique montre son focus par son cadre animé, sans contour bleu autour
            sansBleu: !(el.matches('textarea') && el.closest('.compo-champ')) || style.outlineStyle === 'none'};
        }, cible);
        assert.ok(arret.entier, 'focus hors de l’écran : ' + arret.nom);
        assert.ok(arret.devant, 'focus masqué : ' + arret.nom);
        assert.ok(arret.contour, 'focus sans contour : ' + arret.nom);
        assert.ok(arret.sansBleu, 'contour bleu autour du champ : ' + arret.nom);
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

test('privacy forms preserve purpose tokens and expired states', async () => {
  const context = await browser.newContext({javaScriptEnabled: false});
  try {
    const page = await context.newPage();
    await page.goto(origin + '/render/example');
    const text = await page.textContent('main');
    assert.ok(text.indexOf('Les pièces') < text.indexOf('Oui, c’est le travail à tester'));
    assert.ok(text.includes('Efface la copie de ce navigateur et demande la suppression sur le serveur'));
    // Contribution retirée : ni case, ni texte de partage sur la page d'un cas
    assert.equal(await page.locator('.privacy-consent, [data-privacy-post="contribution"]').count(), 0);
    for (const phrase of ['conserver cet exemple pendant 6 mois', 'Je partage cet exemple avec Evaly', 'ne dépend pas de ce choix'])
      assert.equal(text.includes(phrase), false, phrase);
    for (const phrase of ['copie complète de chaque cas ouvert', 'a besoin de JavaScript', 'sans lui, seule la contribution',
      'active aussi', 'j’active l’historique'])
      assert.equal(text.includes(phrase), false, phrase);
    // La conservation 7 jours / 30 jours s'explique dans Mes données, plus sur la page du cas
    assert.equal(/effacé après 7 jours|effacée après 30 jours/.test(text), false);
    assert.equal(/Clé retirée après|11 jours|conserve votre copie locale/.test(text), false);
    assert.equal(await page.locator('script[src="/preparation/privacy.js"]').count(), 1);
    assert.ok(await page.locator('script:not([src])').count() > 0);
    // Mes cas d'usage avec une session : le repère d'activité, ni liste ni copie des cas
    await page.goto(origin + '/render/listing-privacy');
    assert.equal(await page.locator('[data-privacy-history], [data-privacy-controls], ul.ge-liste').count(), 0);
    const activity = page.locator('[data-privacy-activity]').first();
    assert.equal(await activity.getAttribute('data-dossier-id'), null);
    assert.equal(await activity.getAttribute('data-csrf-token'), 'privacy-token');
    assert.equal(await page.locator('form#nouveau input[name="csrf_token"]').inputValue(), 'csrf');
    // Mes données : la table des cas du serveur, puis les copies du navigateur ; lire la table ne renouvelle rien
    await page.goto(origin + '/render/privacy-listing');
    assert.equal(await page.locator('[data-privacy-history]').count(), 1);
    assert.equal(await page.locator('[data-privacy-activity]').count(), 0);
    assert.equal(await page.locator('[data-privacy-post="contribution"]').count(), 0);
    assert.deepEqual(await page.locator('.table-cas th').evaluateAll(cells => cells.map(cell => [cell.getAttribute('scope'), cell.textContent])),
      [['col', 'Cas d’usage'], ['col', 'Version'], ['col', 'Effacement prévu'], ['col', 'Actions']]);
    assert.deepEqual(await page.locator('.table-cas tbody td:first-child a').evaluateAll(links => links.map(link => [link.getAttribute('href'), link.textContent])),
      [['/preparation/dossiers/d1', 'Trier des notes de réunion'], ['/preparation/dossiers/d2', 'Rédiger des réponses aux clients']]);
    assert.deepEqual(await page.locator('.table-cas tbody td:nth-child(2)').allTextContents(), ['Version 2', 'Version 1']);
    assert.deepEqual(await page.locator('.table-cas tbody td:nth-child(3)').allTextContents(), ['inconnu', 'inconnu']);
    // Chaque ligne garde sa copie (privacy.js) et mène à la confirmation de suppression, avec son adresse de retour
    assert.deepEqual(await page.locator('.table-cas [data-privacy-controls]').evaluateAll(cards => cards.map(card => card.dataset.dossierId)), ['d1', 'd2']);
    assert.deepEqual(await page.locator('.table-cas a.btn-danger').evaluateAll(links => links.map(link => link.getAttribute('href'))),
      ['/preparation/dossiers/d1/delete?retour=/preparation', '/preparation/dossiers/d2/delete?retour=/preparation']);
    assert.equal(await page.locator('[data-privacy-post="delete"]').count(), 0);
    // Sans JavaScript, les boutons de copie restent cachés ; une ligne ne copie jamais au chargement
    assert.equal(await page.locator('.table-cas [data-privacy-action]:visible').count(), 0);
    assert.equal(await page.locator('.table-cas [data-privacy-activity], .table-cas [data-content-version]').count(), 0);
    assert.equal(await page.locator('.table-cas [data-privacy-manual]').count(), 2);
    assert.equal(await page.locator('.ge-vide-cas').count(), 0);
    assert.equal(await page.locator('main a[href="/preparation/catalogue"]').count(), 1);
    assert.equal(await page.locator('.alerte-exp').count(), 0, 'sans échéance connue, aucune alerte');
    await page.goto(origin + '/render/listing-expiring');
    assert.equal(await page.locator('.alerte-exp').count(), 1);
    assert.match(await page.locator('.alerte-exp').textContent(), /Bientôt effacé.*ce cas sera effacé le (lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche) \d{1,2} \p{L}+/u);
    // Tri par échéance décroissante, cas sans échéance en dernier
    assert.deepEqual(await page.locator('.table-cas tbody td:first-child a').allTextContents(), ['Trier des notes de réunion', 'Rédiger des réponses aux clients']);
    assert.equal(await page.locator('.table-cas tr:has(.alerte-exp) td:first-child a').textContent(), 'Trier des notes de réunion');
    assert.match(await page.locator('.table-cas tr:has(.alerte-exp) td[data-label="Effacement prévu"]').textContent(),
      /^(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche) \d{1,2} \p{L}+Bientôt effacé/u);
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
      await page.goto(origin + '/render/case-' + name);
      assert.equal(await page.locator('[data-privacy-post="contribution"]').count(), 0, name);
      // Version prête à valider : la carte invite seulement à relire, sans aucune phrase de partage
      const carte = await page.textContent('main');
      assert.equal(/partage/i.test(carte), false, name);
      if (name === 'current') assert.match(carte, /Version 3 prête à valider\s*Relisez l’exemple, puis validez-le\./);
      // Accès fermé seulement si la session a expiré : le lien vers les copies porte le nom de sa page
      assert.deepEqual((await page.locator('.etat a.lien[href="/preparation/data"]').allTextContents()).map(t => t.trim()),
        name === 'expired' ? ['Mes données'] : [], name);
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
      // Retirer le consentement se confirme, même sans JavaScript : l'envoi reste replié derrière la question
      const repli = page.locator('details.confirmation-repliee');
      assert.equal(await repli.count(), Number(!disabled));
      if (!disabled) {
        const oui = form.getByRole('button', {name: 'Oui, retirer mon consentement'});
        assert.equal(await oui.isVisible(), false);
        await repli.locator('summary', {hasText: 'Retirer mon consentement'}).click();
        assert.equal(await oui.isVisible(), true);
        assert.match(await repli.textContent(), /Retirer votre consentement \?/);
      }
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
    // Sans session ni cas : une seule phrase à la place de la table
    assert.equal(await page.locator('.ge-vide-cas').textContent(), 'Aucun cas d’usage.');
    assert.equal(await page.locator('main table').count(), 0);
    // L'avertissement du navigateur partagé se lit sans rien ouvrir
    const avertissement = page.locator('[data-privacy-history] .ge-avert');
    assert.equal(await avertissement.isVisible(), true);
    assert.equal(await avertissement.evaluate(element => Boolean(element.closest('details'))), false);
    assert.match(await avertissement.textContent(), /Toute personne qui utilise ce profil de navigateur peut aussi les lire/);
    // Sans JavaScript, l'historique local n'est pas lisible : aucune action d'effacement local n'est proposée
    assert.equal(await page.getByRole('button', {name: 'Tout effacer et arrêter l’historique'}).count(), 0);
    assert.equal(await page.getByRole('button', {name: 'Effacer et ne plus garder ce cas'}).count(), 0);
    assert.equal(await page.locator('[data-privacy-list] > details').count(), 0);
    // « Supprimer ce cas d'usage » mène à une page de confirmation ; sans JavaScript, seul son bouton envoie la demande
    await page.goto(origin + '/render/case-delete');
    const suppression = page.locator('form[action="/preparation/dossiers/d1/delete"]');
    assert.equal(await suppression.getAttribute('method'), 'post');
    assert.deepEqual(await suppression.locator('button[type="submit"]').allTextContents(), ['Oui, supprimer définitivement']);
    assert.equal(await suppression.locator('button[type="submit"]').isEnabled(), true);
    assert.equal(await suppression.locator('.annuler a', {hasText: 'Annuler et garder ce cas'}).isVisible(), true);
    assert.match(await page.locator('main').innerText(), /Sans JavaScript, seule la suppression sur le serveur est demandée/);
    // Mes données : conservation expliquée, historique local, puis les liens ; aucune page ne déborde
    const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 844});
      await page.goto(origin + '/render/data');
      const main = await page.locator('main').innerText();
      for (const phrase of ['Vos cas d’usage', 'Aucun cas d’usage.', 'Conservation automatique', 'Un cas d’usage est effacé après 7 jours sans activité',
        'Votre clé OpenRouter est effacée après 30 jours sans activité', 'Historique local de vos cas']) assert.ok(main.includes(phrase), `${width} ${phrase}`);
      assert.ok(main.indexOf('Vos cas d’usage') < main.indexOf('Historique local de vos cas'), `${width} cas avant les copies`);
      assert.ok(main.indexOf('Historique local de vos cas') < main.indexOf('Conservation automatique'), `${width} copies avant la conservation`);
      assert.equal(await page.title(), 'Mes données · Evaly', `${width} titre de Mes données`);
      assert.ok(main.includes('cette page affiche « Bientôt effacé » sur sa ligne'), `${width} échéance lue sur Mes données`);
      assert.equal(/Pour supprimer un cas d’usage avant ce délai/.test(main), false, `${width} conservation sans renvoi`);
      assert.equal(await page.locator('main a[href="/preparation"], main a[href="/preparation/contributions"], main a[href="/confidentialite"]').count(), 0,
        `${width} aucun lien de bas de contenu`);
      assert.equal(await page.locator('.nav [aria-current="page"]').textContent(), 'Mes données', `${width} entrée de menu des copies`);
      assert.equal(await overflow(), 0, `${width} débordement de Mes données`);
      await page.goto(origin + '/render/contribution-none');
      assert.equal(await page.locator('main a[href="/preparation"]', {hasText: 'Revenir à mes cas d’usage'}).count(), 1, `${width} retour aux cas d’usage`);
      await page.goto(origin + '/render/contribution-active-2099');
      const contributions = await page.locator('main').textContent();
      assert.ok(contributions.includes('Contribution du 18 septembre 2026'), `${width} contribution`);
      assert.ok(contributions.includes('arrête seulement la contribution concernée'), `${width} explication du retrait`);
      // Page de retrait sans entrée de navigation : aucun lien du menu ne la marque comme courante
      assert.equal(await page.locator('[aria-current="page"]').count(), 0, `${width} aucune entrée de menu des contributions`);
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
      // Même menu, même pied et même tagline sur chaque page ; plus aucun lien vers les « cas en cours »
      // La page publique d'une publication garde son propre pied légal, hors de cette règle
      if (name !== 'publication-footer') {
        assert.deepEqual(await page.locator('.nav li').allTextContents(), ['Accueil', 'Mes cas d’usage', 'Mes données', 'Benchmarks publics'], name);
        assert.deepEqual(await page.locator('nav[aria-label="Parcours"] a').allTextContents(),
          ['Décrire mon cas d’usage', 'Mes données', 'Benchmarks publics'], name);
        assert.ok((await page.locator('footer .intro').first().textContent()).startsWith('Evaly, le benchmark personnalisé : '), name);
        assert.equal(await page.locator('footer .intro.note').count(), 0, name);
      }
      assert.equal(await page.locator('a', {hasText: 'cas en cours'}).count(), 0, name);
      assert.equal((await page.textContent('body')).includes('page Benchmark'), false, name);
      if (LEGAL.includes(name)) {
        assert.equal(await page.locator('script, [data-privacy-activity], [aria-current], blockquote').count(), 0);
        assert.equal(await page.locator('h1').count(), 1);
        assert.equal(await page.title(), (await page.locator('h1').textContent()) + ' · Evaly');
        assert.equal(/\[\[|Formulation à retenir|N’annoncez pas|aucune route ne les expose|annoncez une fermeture d’accès/.test(await page.textContent('body')), false);
      }
    }
    await page.goto(origin + '/render/confidentialite');
    const privacy = await page.textContent('main');
    for (const term of ['Dernière mise à jour : 9 novembre 2026', 'OpenRouter, Inc., établi aux États-Unis',
      'article 45 du RGPD', 'article 46 du RGPD', 'Nous ne pouvons pas garantir', 'art. 6.1.b', 'art. 6.1.f',
      'portabilité', 'CNIL', '3 place de Fontenoy', 'benchmark_session', 'activé par défaut', 'arrêter, le reprendre ou l’effacer',
      'chaque cas que vous ouvrez', 'votre besoin, vos messages et les révisions', 'que la contribution exclut',
      'retire la copie de contribution conservée sur le serveur', 'ne concerne que la contribution',
      'n’arrête pas l’historique local', 'restent dans ce navigateur', 'N’est plus déposé',
      'Le second n’est plus déposé : il reste lu pour les contributions antérieures']) assert.ok(privacy.includes(term), term);
    assert.equal(/si vous activez/.test(privacy), false);
    // Une contribution déjà enregistrée n'est plus mise à jour par les tâches : le retrait n'a plus de mise à jour à arrêter
    assert.equal(/ses mises à jour/.test(privacy), false, 'mise à jour de contribution annoncée');
    const ligneHistorique = await page.getByRole('row').filter({hasText: 'Historique local dans votre navigateur'}).textContent();
    assert.ok(ligneHistorique.includes('Voir §6 : enregistrement dans votre navigateur, activé par défaut, que vous pouvez arrêter ou effacer'));
    assert.equal(ligneHistorique.includes('6.1.a'), false);
    assert.equal(/désactivé par défaut|deux effets/.test(privacy), false);
    assert.equal(/N’ÉCRIRE QU’APRÈS|N'ÉCRIRE QU'APRÈS/.test(privacy), false);
    // La page des copies s'appelle Mes données (PRD §10) : le texte légal ne change pas, seuls ses liens suivent le nouveau nom
    assert.ok(privacy.includes('page Mes données'));
    assert.equal(/Mes\s+cas d’usage/.test(privacy), false);
    assert.equal(await page.locator('a[href="/preparation/data"]').count() > 0, true);
    await page.goto(origin + '/render/cgu');
    assert.ok((await page.textContent('main')).includes('Dernière mise à jour : 9 novembre 2026'));
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
      // Accueil : le lien de retour aux cas se place sous le bouton principal
      const [cta, retour] = await Promise.all(['.une-bas .actions .btn', '.une-bas .actions .lien'].map(s => page.locator(s).boundingBox()));
      assert.ok(retour.y >= cta.y + cta.height, 'actions de l’accueil l’une sous l’autre');
      assert.equal((await page.locator('.une-bas .actions .lien').textContent()).trim(), 'Mes cas d’usage');
      assert.equal(await page.locator('a[href="/index.html"]').count(), 0);
      assert.ok(text.includes('Un seul manquement suffit'));
      assert.equal(text.includes('Comparaisons publiées'), false);
    }
    await page.goto(origin + '/render/listing');
    assert.equal(await page.locator('.nav a[aria-current="page"]').textContent(), 'Mes cas d’usage');
    assert.equal(await page.title(), 'Mes cas d’usage · Evaly');
    // Mes cas d'usage renvoie aux cas décrits ; la table de Mes données porte leurs liens
    assert.equal(await page.locator('ul.ge-liste, #t-liste').count(), 0);
    assert.equal(await page.locator('.ge-mes-cas p.petit a[href="/preparation/data"]').textContent(), 'Mes données');
    await page.goto(origin + '/render/privacy-listing');
    assert.equal(await page.locator('table a[href="/preparation/dossiers/d1"]').count(), 1);
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
