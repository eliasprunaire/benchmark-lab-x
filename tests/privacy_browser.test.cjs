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
from tests.test_privacy_views import example_view
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
print(json.dumps({'data': views.render({'kind': 'privacy_data'}, '').decode(), 'launched': launched,
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
                  'progress_script': views.PREPARATION_PROGRESS_SCRIPT}))
`], {encoding: 'utf8'}));
  server = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    res.setHeader('Cache-Control', 'no-store');
    if (req.method === 'POST') {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const body = req.headers['content-type']?.includes('application/json') ? JSON.parse(raw) : Object.fromEntries(new URLSearchParams(raw));
      posts.push({path: url.pathname, body});
      res.writeHead(postStatus, {'Content-Type': 'application/json'}).end('{}');
    } else if (url.pathname === '/preparation/dossiers/d1/campaigns/c1/conditions') {
      // Réponses du suivi imposées par le test : un statut d'erreur ou le nom d'une page rendue
      const reply = followupReplies.shift() ?? 'followup';
      if (typeof reply === 'number') {res.writeHead(reply).end(); return;}
      res.setHeader('Content-Type', 'text/html'); res.end(rendered[reply]);
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

test('benchmark followup survives an isolated failure, stops after three in a row, then opens the results', async () => {
  const context = await browser.newContext();
  try {
    const failures = [];
    let page;
    const start = async () => {
      followupReplies = [];
      page = await context.newPage(); page.setDefaultTimeout(5000);
      page.on('pageerror', error => failures.push(error.message));
      page.on('console', message => {if (/Content Security Policy/.test(message.text())) failures.push(message.text());});
      await page.clock.install();
      await page.goto(origin + '/render/followup');
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
