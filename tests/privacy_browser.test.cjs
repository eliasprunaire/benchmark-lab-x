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
let posts = [], postStatus = 200;
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
from benchmark_web import views
from tests.test_privacy_views import example_view
print(json.dumps({'data': views.render({'kind': 'privacy_data'}, '').decode(),
                  **{path[1:]: views.render({'kind': 'legal', 'path': path}, '').decode()
                     for path in ('/mentions-legales', '/cgu', '/confidentialite')},
                  'example': views.render(example_view(), 'csrf').decode(), 'script': views.STEP_SCRIPT}))
`], {encoding: 'utf8'}));
  server = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    res.setHeader('Cache-Control', 'no-store');
    if (req.method === 'POST') {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const body = req.headers['content-type']?.includes('application/json') ? JSON.parse(raw) : Object.fromEntries(new URLSearchParams(raw));
      posts.push({path: url.pathname, body});
      res.writeHead(postStatus, {'Content-Type': 'application/json'}).end('{}');
    } else if (url.pathname.startsWith('/render/')) {
      const hash = createHash('sha256').update(rendered.script).digest('base64');
      res.setHeader('Content-Type', 'text/html');
      res.setHeader('Content-Security-Policy', `default-src 'none'; script-src 'self' 'sha256-${hash}'; connect-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'`);
      res.end(rendered[url.pathname.split('/').pop()]);
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
    await assert.rejects(page.evaluate(() => historyStore.archive('d1', 2)), /intégrité/i);
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
    await page.getByRole('button', {name: 'Exporter ce cas en JSON'}).click();
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
    await page.waitForFunction(() => document.querySelector('[data-privacy-status]').textContent.includes('actualisez'));
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
    await assert.rejects(page.evaluate(() => historyStore.archive('d1', 2)), /format/);
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
    await page.evaluate(async () => {window.privacy = await import('/preparation/privacy.js'); window.historyStore = await privacy.openHistory();});
    fixture(1); await page.evaluate(() => historyStore.archive('d1', 1));
    fixture(2, randomBytes(2000000).toString('hex'));
    await assert.rejects(page.evaluate(() => historyStore.archive('d1', 2)), /quota/i);
    assert.equal((await page.evaluate(() => historyStore.get('d1'))).content_version, 1);
  } finally {await context.close();}
});

test('rendered pages mount under self plus existing hash CSP, preserve unchecked consent and fit mobile', async () => {
  fixture(); posts = [];
  const context = await browser.newContext({viewport: {width: 1200, height: 900}});
  try {
    const page = await context.newPage(); page.setDefaultTimeout(3000);
    const failures = [];
    page.on('pageerror', error => failures.push(error.message));
    page.on('console', message => {if (/Content Security Policy/.test(message.text())) failures.push(message.text());});
    await page.goto(origin + '/render/example');
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent.includes('complète enregistrée'));
    assert.equal(posts.length, 0);
    assert.equal(await page.getByRole('checkbox').isChecked(), false);
    assert.equal(await page.getByRole('checkbox').isEnabled(), true);
    mkdirSync('reports/privacy-browser', {recursive: true});
    await page.locator('.privacy-consent').screenshot({path: 'reports/privacy-browser/consent-desktop.png'});
    await page.goto(origin + '/render/data');
    await page.waitForFunction(() => document.querySelector('[data-privacy-list] > details'));
    await page.locator('[data-privacy-list] > details > summary').click();
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path: 'reports/privacy-browser/data-mobile.png', fullPage: true});
    assert.deepEqual(failures, []);
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
        await page.waitForFunction(() => document.querySelector('[data-privacy-status]').textContent.includes('nettoyage en attente'));
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
    const page = await context.newPage(); page.setDefaultTimeout(3000);
    await page.goto(origin + '/render/example');
    await page.waitForFunction(() => document.querySelector('[data-privacy-controls] [data-privacy-status]').textContent.includes('complète enregistrée'));
    const observer = await pageFor(context);
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
    assert.deepEqual(observed, {record: null, state: {enabled: false}});
    await page.waitForFunction(() => document.querySelector('form[data-privacy-post="delete"] [data-privacy-status]')?.textContent.includes('non confirmée'));
    const status = await page.locator('form[data-privacy-post="delete"] [data-privacy-status]').textContent();
    assert.match(status, /Copie locale effacée/);
    assert.match(status, /serveur et de la contribution non confirmée/);
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
      await page.waitForFunction(() => document.querySelector('[data-privacy-status]')?.textContent.includes('Suppression locale non confirmée'));
      assert.deepEqual(posts, [{path: '/preparation/dossiers/d1/delete', body: {csrf_token: 'purpose'}}]);
      assert.equal(page.url(), origin + '/');
      const status = await page.locator('[data-privacy-status]').textContent();
      assert.match(status, statusCode === 200 ? /nettoyage en attente/ : /serveur et de la contribution non confirmée/);
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
