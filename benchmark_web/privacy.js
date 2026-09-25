// Native browser history; no credentials or arbitrary server fields are persisted
const RAW_CHUNK = 1024 * 1024;
const integer = value => Number.isSafeInteger(value) && value >= 0;
const string = value => typeof value === 'string';
const strings = value => Array.isArray(value) && value.every(string);
const shape = (value, fields) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === Object.keys(fields).length
  && Object.entries(fields).every(([key, check]) => Object.hasOwn(value, key) && check(value[key]));
const arrayOf = check => value => Array.isArray(value) && value.every(check);
const piece = value => shape(value, {name: string, text: string});
function validRecord(value) {
  return shape(value, {
    format: v => v === 'bench-x/history/v1', dossier_id: string, content_version: integer,
    need: string, messages: strings,
    revisions: arrayOf(v => shape(v, {number: integer, instruction: string, deliverables: strings,
      criteria: strings, pieces: arrayOf(piece), qualification: string})),
    campaigns: arrayOf(v => shape(v, {id: string, models: arrayOf(m => shape(m, {
      name: string, verdict: v => v === null || string(v),
      cost: c => shape(c, {amount: a => a === null || string(a), currency: string}),
      answer: a => a === null || string(a), evidence: arrayOf(piece)}))}))
  });
}
function integrity(condition) {
  if (!condition) throw new Error('Archive refusée : intégrité ou format invalide.');
}
async function jsonGet(url) {
  const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    headers: {Accept: 'application/json'}});
  if (!response.ok) throw new Error('Archive indisponible sur le serveur. La copie locale reste inchangée.');
  return response.json();
}
function requestValue(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
function completed(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error || new Error('Écriture locale interrompue.'));
    transaction.onerror = () => {}; // Abort reports the final transaction outcome
  });
}
// Without create, a missing database stays missing: opening it must not write before a choice
export async function openHistory({create = true} = {}) {
  const opening = indexedDB.open('bench-x-history', 1);
  opening.onupgradeneeded = event => {
    if (!create && event.oldVersion === 0) {opening.transaction.abort(); return;}
    for (const name of ['archives', 'staging', 'control']) opening.result.createObjectStore(name, {keyPath: 'id'});
  };
  let db;
  try {db = await requestValue(opening);}
  catch (error) {
    if (!create && error?.name === 'AbortError') return null;
    throw error;
  }
  db.onversionchange = () => db.close();
  async function read(store, key) {
    const tx = db.transaction(store, 'readonly');
    const done = completed(tx);
    const [result] = await Promise.all([requestValue(key === undefined
      ? tx.objectStore(store).getAll() : tx.objectStore(store).get(key)), done]);
    return result;
  }
  async function write(store, action) {
    const tx = db.transaction(store, 'readwrite');
    const done = completed(tx);
    action(tx.objectStore(store));
    await done;
  }
  // Every publish, tombstone and reactivation shares this transaction scope across tabs
  function controlled(id, action) {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(['control', 'archives', 'staging'], 'readwrite');
      const controls = tx.objectStore('control');
      let result, failure;
      tx.oncomplete = () => resolve(result);
      tx.onabort = () => reject(failure || tx.error || new Error('Écriture locale interrompue.'));
      tx.onerror = () => {};
      controls.get('').onsuccess = globalEvent => {
        const global = globalEvent.target.result || {id: '', generation: 0, enabled: false};
        controls.get(id).onsuccess = localEvent => {
          const local = localEvent.target.result || {id, generation: 0, enabled: true, version: -1};
          try {result = action(tx, global, local);}
          catch (error) {failure = error; tx.abort();}
        };
      };
    });
  }
  function check(global, local, ticket) {
    if (!global.enabled || !local.enabled || ticket && (
      ticket.global !== global.generation || ticket.local !== local.generation)) {
      throw new Error('Historique effacé ou suspendu. Enregistrement annulé ; réactivation explicite requise.');
    }
  }
  async function change(id, enabled) {
    await controlled(id, (tx, global, local) => {
      const control = id ? local : global;
      tx.objectStore('control').put({...control, enabled, generation: control.generation + 1});
      if (!enabled) {
        if (id) {
          tx.objectStore('archives').delete(id);
          tx.objectStore('staging').openCursor().onsuccess = event => {
            const cursor = event.target.result;
            if (cursor) {
              if (cursor.value.dossier_id === id) cursor.delete();
              cursor.continue();
            }
          };
        } else {
          tx.objectStore('archives').clear();
          tx.objectStore('staging').clear();
        }
      }
    });
  }
  return {
    close: () => db.close(),
    async state(id = '') {
      return controlled(id, (tx, global, local) => ({enabled: global.enabled && local.enabled, chosen: global.generation > 0}));
    },
    clear: () => change('', false),
    remove: id => change(id, false),
    enable: (id = '') => change(id, true),
    async list() {return (await read('archives')).filter(row => row.complete).map(row => row.record);},
    async get(id) {const row = await read('archives', id); return row?.complete ? row.record : null;},
    async archive(id, minimumVersion = 0) {
      integrity(string(id) && id.length > 0 && !['.', '..'].includes(id) && integer(minimumVersion));
      const stageId = crypto.randomUUID();
      try {
        const ticket = await controlled(id, (tx, global, local) => {
          check(global, local);
          tx.objectStore('staging').put({id: stageId, dossier_id: id});
          return {global: global.generation, local: local.generation};
        });
        const base = '/preparation/dossiers/' + encodeURIComponent(id) + '/archive';
        const manifest = await jsonGet(base);
        integrity(manifest.format === 'bench-x/archive/v1' && manifest.dossier_id === id
          && integer(manifest.content_version) && manifest.content_version >= minimumVersion
          && string(manifest.snapshot_id) && manifest.snapshot_id.length > 0
          && Array.isArray(manifest.items) && manifest.items.length === 1);
        const item = manifest.items[0];
        integrity(item.item_id === 'record' && integer(item.length) && item.length > 0
          && typeof item.sha256 === 'string' && /^[0-9a-f]{64}$/.test(item.sha256));
        const bytes = new Uint8Array(item.length);
        const parts = Math.ceil(item.length / RAW_CHUNK);
        for (let part = 0; part < parts; part++) {
          const chunk = await jsonGet(base + '/items/record?snapshot=' + encodeURIComponent(manifest.snapshot_id) + '&part=' + part);
          const length = Math.min(RAW_CHUNK, item.length - part * RAW_CHUNK);
          integrity(chunk.snapshot_id === manifest.snapshot_id && chunk.item_id === 'record'
            && chunk.part === part && chunk.total_parts === parts && string(chunk.hex)
            && chunk.hex.length === length * 2 && /^[0-9a-f]+$/i.test(chunk.hex));
          for (let n = 0; n < length; n++) bytes[part * RAW_CHUNK + n] = parseInt(chunk.hex.slice(n * 2, n * 2 + 2), 16);
        }
        const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
          byte => byte.toString(16).padStart(2, '0')).join('');
        integrity(hash === item.sha256);
        let record;
        try {record = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));}
        catch {integrity(false);}
        integrity(validRecord(record) && record.dossier_id === id && record.content_version === manifest.content_version);
        await controlled(id, (tx, global, local) => {
          check(global, local, ticket);
          tx.objectStore('staging').put({id: stageId, dossier_id: id, record});
        });
        let published = false;
        await controlled(id, (tx, global, local) => {
          check(global, local, ticket);
          tx.objectStore('archives').get(id).onsuccess = event => {
            if (record.content_version < local.version || event.target.result &&
                record.content_version <= event.target.result.record.content_version) return;
            tx.objectStore('staging').get(stageId).onsuccess = staged => {
              if (!staged.target.result?.record) {tx.abort(); return;}
              tx.objectStore('archives').put({id, complete: true, record: staged.target.result.record});
              tx.objectStore('control').put({...local, version: record.content_version});
              tx.objectStore('staging').delete(stageId);
              published = true;
            };
          };
        });
        return published;
      } finally {
        await write('staging', store => store.delete(stageId));
      }
    }
  };
}

function node(tag, value) {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = value;
  return element;
}
function details(label, parent) {
  const element = node('details');
  element.append(node('summary', label));
  parent.append(element);
  return element;
}
function pieces(label, items, parent) {
  const group = details(label, parent);
  for (const item of items) details(item.name, group).append(node('pre', item.text));
}
function renderRecord(record, parent) {
  parent.append(node('p', 'Version locale ' + record.content_version), node('h3', 'Besoin'), node('p', record.need));
  const messages = details('Messages', parent);
  record.messages.forEach(message => messages.append(node('p', message)));
  for (const revision of record.revisions) {
    const group = details('Révision ' + revision.number, parent);
    group.append(node('h3', 'Consigne'), node('pre', revision.instruction));
    for (const [label, items] of [['Livrables', revision.deliverables], ['Critères', revision.criteria]]) {
      group.append(node('h3', label));
      const list = node('ul');
      items.forEach(item => list.append(node('li', item)));
      group.append(list);
    }
    pieces('Pièces', revision.pieces, group);
    group.append(node('p', 'Qualification : ' + revision.qualification));
  }
  for (const campaign of record.campaigns) {
    const group = details('Comparaison ' + campaign.id, parent);
    for (const model of campaign.models) {
      const cost = model.cost.amount === null ? 'Coût inconnu (' + model.cost.currency + ')'
        : model.cost.amount + ' ' + model.cost.currency;
      const result = details(model.name + ' · ' + (model.verdict ?? 'Verdict indisponible') + ' · ' + cost, group);
      result.append(node('h3', 'Réponse'), node('pre', model.answer === null ? 'Aucune réponse conservée' : model.answer));
      pieces('Preuves', model.evidence, result);
    }
  }
}
async function post(url, body) {
  const response = await fetch(url, {method: 'POST', credentials: 'same-origin', cache: 'no-store',
    redirect: 'error', keepalive: true, headers: {'Content-Type': 'application/json', Accept: 'application/json'},
    body: JSON.stringify(body)});
  if (!response.ok) throw new Error(response.status === 409
    ? 'Ce choix ou cet exemple a changé ; actualisez la page avant de recommencer.'
    : 'Action non confirmée. Vérifiez votre accès puis actualisez la page.');
  return response;
}
// One consent decision with two derived effects; separating them changes this function only
const consentEffects = checked => ({contribution: checked, localHistory: checked});
const mounted = new WeakSet();
const activityMounted = new WeakSet();
function mountForms(root) {
  for (const form of root.querySelectorAll('form[data-privacy-post]')) {
    if (mounted.has(form)) continue;
    mounted.add(form);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (form.dataset.busy) return;
      const action = form.dataset.privacyPost;
      const url = new URL(form.action, location.href);
      if (url.origin !== location.origin || !/^\/preparation\/(dossiers\/[^/]+\/(delete|contribution)|contributions\/[^/]+\/withdraw)$/.test(url.pathname)) return;
      const fields = new FormData(form);
      const body = {csrf_token: fields.get('csrf_token')};
      let effects;
      if (action === 'contribution') {
        effects = consentEffects(form.elements.enabled.checked);
        body.enabled = effects.contribution;
        body.revision = Number(fields.get('revision'));
        body.example_revision = Number(fields.get('example_revision'));
        if (!integer(body.revision) || !integer(body.example_revision)) return;
      }
      const status = form.querySelector('[data-privacy-status]') || form.parentElement.querySelector('[data-privacy-status]');
      form.dataset.busy = 'true';
      const button = event.submitter;
      if (button) button.disabled = true;
      let localDeleted = false;
      if (action === 'delete') {
        let history;
        try {
          history = await openHistory({create: false});
          if (history) await history.remove(decodeURIComponent(url.pathname.split('/')[3]));
          localDeleted = true;
        } catch {} finally {history?.close();}
        if (localDeleted && typeof BroadcastChannel === 'function') {
          try {
            const channel = new BroadcastChannel('bench-x-history');
            channel.postMessage('changed'); channel.close();
          } catch {}
        }
      }
      const localOutcome = localDeleted ? 'Copie locale effacée. ' : 'Suppression locale non confirmée. ';
      try {
        const response = await post(url.pathname, body);
        if (effects?.localHistory) {
          let history;
          try {
            history = await openHistory();
            await history.enable();
            await history.enable(decodeURIComponent(url.pathname.split('/')[3]));
          } catch {} finally {history?.close();}
        }
        if (action !== 'delete') location.reload();
        else {
          const result = await response.json();
          if (status) status.textContent = localOutcome + (result.status === 'purged'
            ? 'Suppression des données actives du serveur terminée.'
            : 'Suppression demandée au serveur ; nettoyage en attente. La contribution associée est retirée.');
          const fields = form.querySelector('fieldset');
          if (localDeleted && fields) fields.disabled = true;
        }
      } catch (error) {
        if (status) status.textContent = action === 'delete'
          ? localOutcome + 'Suppression sur le serveur et de la contribution non confirmée. Vous pouvez réessayer.'
          : error.message || 'Action non confirmée. Actualisez avant de recommencer.';
      } finally {
        delete form.dataset.busy;
        if (button) button.disabled = false;
      }
    });
  }
}
function mountActivity(root) {
  const marker = root.querySelector('[data-privacy-activity]');
  if (!marker || activityMounted.has(marker)) return;
  activityMounted.add(marker);
  let pending = false;
  const activity = async event => {
    if (!event.isTrusted || document.visibilityState !== 'visible' || pending
        || !(event.target instanceof Element) || !event.target.getClientRects().length
        || getComputedStyle(event.target).visibility !== 'visible') return;
    pending = true;
    const body = {csrf_token: marker.dataset.csrfToken};
    if (marker.dataset.dossierId) body.dossier_id = marker.dataset.dossierId;
    try {await post('/preparation/activity', body);}
    catch {
      const status = marker.querySelector('[data-privacy-status]');
      if (status) status.textContent = 'Prolongation de l’accès non confirmée. Actualisez pour vérifier votre session.';
    } finally {pending = false;}
  };
  root.addEventListener('input', activity);
  root.addEventListener('click', activity);
}
function mountBootstrap(root) {
  const shell = root.querySelector('[data-privacy-bootstrap]');
  if (!shell || mounted.has(shell)) return;
  mounted.add(shell);
  const status = shell.querySelector('[data-privacy-status]');
  const form = shell.querySelector('form');
  const button = form.querySelector('button');
  let target, pending = false;
  try {
    const path = shell.dataset.returnPath;
    if (!path.startsWith('/') || path.startsWith('//') || /[\\\x00-\x1f]/.test(path)) throw new Error();
    target = new URL(path, location.origin);
    if (target.origin !== location.origin || target.pathname === '/preparation/session/open') throw new Error();
  } catch {
    status.textContent = 'Destination invalide. Revenez à Mes cas d’usage.';
    button.disabled = true;
    return;
  }
  const key = 'bench-x-session-opening';
  const blocked = 'Les cookies de ce site semblent bloqués. Autorisez-les puis choisissez Continuer. Aucun nouvel essai automatique.';
  const open = async manual => {
    if (pending) return;
    try {
      if (!manual && sessionStorage.getItem(key)) {status.textContent = blocked; return;}
      sessionStorage.setItem(key, '1');
    } catch {
      if (!manual) {status.textContent = 'Le stockage du navigateur est indisponible. Choisissez Continuer pour essayer explicitement.'; return;}
    }
    pending = true; button.disabled = true;
    status.textContent = 'Ouverture de votre accès…';
    try {
      await post('/preparation/session/open', {});
      // The session cookie is HttpOnly; verify it via the destination, never document.cookie
      const response = await fetch(target.pathname + target.search, {credentials: 'same-origin',
        cache: 'no-store', redirect: 'error', headers: {Accept: 'text/html'}});
      if (!response.ok || !response.headers.get('content-type')?.includes('text/html')) {
        throw new Error('Accès non confirmé. Choisissez Continuer pour réessayer.');
      }
      const next = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (next.querySelector('[data-privacy-bootstrap]')) {status.textContent = blocked; return;}
      location.replace(target.href);
    } catch (error) {
      status.textContent = error.message || 'Ouverture impossible. Vérifiez les cookies puis choisissez Continuer.';
    } finally {pending = false; button.disabled = false;}
  };
  form.addEventListener('submit', event => {event.preventDefault(); void open(true);});
  void open(false);
}
export async function mountPrivacy(root = document) {
  const home = root.querySelector('[data-privacy-home]');
  if (home && !mounted.has(home)) {
    mounted.add(home);
    try {
      const state = await jsonGet('/preparation/activity');
      if (state.active === true && typeof state.csrf_token === 'string') {
        home.setAttribute('data-privacy-activity', '');
        home.dataset.csrfToken = state.csrf_token;
      }
    } catch {}
  }
  mountForms(root);
  mountBootstrap(root);
  mountActivity(root);
  if (root.querySelector('[data-privacy-activity]')) {
    try {sessionStorage.removeItem('bench-x-session-opening');} catch {}
  }
  const historyRoots = [...root.querySelectorAll('[data-privacy-history], [data-privacy-controls][data-dossier-id]')]
    .filter(element => !mounted.has(element));
  if (!historyRoots.length) return;
  let store;
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('bench-x-history') : null;
  const refreshers = [];
  const refresh = () => Promise.all(refreshers.map(update => update()));
  if (channel) channel.onmessage = () => {void refresh();};
  const changed = async () => {channel?.postMessage('changed'); await refresh();};
  for (const element of historyRoots) mounted.add(element);
  // A missing database means no choice yet; reconnect on refresh in case another tab made one
  let connecting;
  const connect = async () => {
    if (store) return store;
    connecting ??= openHistory({create: false}).finally(() => {connecting = undefined;});
    return store ??= await connecting;
  };
  try {await connect();}
  catch {
    for (const element of historyRoots) element.querySelector('[data-privacy-status]').textContent =
      'Historique local indisponible dans ce navigateur. Aucune copie locale confirmée.';
    return;
  }
  for (const element of historyRoots) {
    const status = element.querySelector('[data-privacy-status]');
    const run = async (button, operation) => {
      button.disabled = true;
      try {await operation();}
      catch (error) {status.textContent = error.name === 'QuotaExceededError'
        ? 'Espace local insuffisant. La copie précédente reste intacte ; exportez vos cas avant de libérer de la place.'
        : error.message || 'Action locale impossible. Aucune copie confirmée.';}
      finally {button.disabled = false;}
    };
    if (element.hasAttribute('data-privacy-history')) {
      const list = element.querySelector('[data-privacy-list]');
      let display = 0;
      const update = async () => {
        const serial = ++display;
        await connect();
        const records = store ? await store.list() : [];
        const state = store ? await store.state() : {enabled: false, chosen: false};
        if (serial !== display) return;
        list.replaceChildren();
        status.textContent = state.enabled ? (records.length ? 'Copies locales complètes : ' + records.length + '.' : 'Aucune copie locale complète.')
          : !state.chosen && !records.length ? 'Historique local désactivé : rien n’est enregistré dans ce navigateur tant que vous ne l’activez pas.'
          : records.length ? 'Historique local suspendu : aucune nouvelle copie. Copies conservées : ' + records.length + '.'
          : 'Historique local effacé et suspendu. Réactivez-le explicitement pour enregistrer de nouvelles copies.';
        element.querySelector('[data-privacy-action="enable"]').hidden = state.enabled;
        element.querySelector('[data-privacy-action="clear"]').hidden = !store;
        for (const record of records) {
          const entry = details(record.need || 'Cas ' + record.dossier_id, list);
          const actions = node('div'); actions.className = 'actions';
          const download = node('button', 'Exporter ce cas en JSON'); download.type = 'button';
          download.addEventListener('click', () => run(download, async () => {
            const current = await store.get(record.dossier_id);
            if (!current) throw new Error('Cette copie a été effacée dans un autre onglet.');
            const url = URL.createObjectURL(new Blob([JSON.stringify(current, null, 2)], {type: 'application/json'}));
            const link = node('a');
            link.href = url;
            link.download = 'bench-x-' + record.dossier_id.replace(/[^a-z0-9_-]/gi, '_') + '.json';
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 0);
          }));
          const remove = node('button', 'Effacer cette copie locale'); remove.type = 'button'; remove.className = 'sec';
          remove.addEventListener('click', () => run(remove, async () => {await store.remove(record.dossier_id); await changed();}));
          actions.append(download, remove); entry.append(actions); renderRecord(record, entry);
        }
      };
      refreshers.push(update);
      for (const [action, operation] of [['clear', () => store?.clear()],
        ['enable', async () => {if (!await connect()) store = await openHistory(); await store.enable();}]]) {
        const button = element.querySelector('[data-privacy-action="' + action + '"]');
        button.addEventListener('click', () => run(button, async () => {await operation(); await changed();}));
      }
      await update();
    } else {
      const id = element.dataset.dossierId;
      const archive = element.querySelector('[data-privacy-action="archive"]');
      const enable = element.querySelector('[data-privacy-action="enable-case"]');
      const active = element.hasAttribute('data-privacy-activity');
      const update = async () => {
        await connect();
        const state = store ? await store.state(id) : {enabled: false, chosen: false};
        archive.hidden = !active || !state.enabled;
        enable.hidden = !active || !state.chosen || state.enabled;
        if (!state.chosen) status.textContent = 'Historique local désactivé : aucune nouvelle copie n’est enregistrée. Il s’active avec la case de contribution ou depuis Mes données.';
        else if (!state.enabled) status.textContent = 'Historique suspendu. Réactivez-le explicitement dans Mes données ou pour ce cas.';
      };
      refreshers.push(update);
      const save = () => run(archive, async () => {
        status.textContent = 'Enregistrement de la copie locale…';
        const saved = await store.archive(id, Number(element.dataset.contentVersion));
        status.textContent = saved ? 'Copie locale complète enregistrée.' : 'La copie locale est déjà aussi récente ou plus récente.';
        await changed();
      });
      archive.addEventListener('click', save);
      enable.addEventListener('click', () => run(enable, async () => {
        // A per-case action never silently lifts the global pause
        await store.enable(id); await changed();
        if (!(await store.state()).enabled) status.textContent = 'Réactivez aussi l’historique global depuis Mes données.';
      }));
      await update();
      if (active && element.hasAttribute('data-content-version') && store && (await store.state(id)).enabled) await save();
    }
  }
  window.addEventListener('focus', () => {void refresh();});
  window.addEventListener('pageshow', event => {if (event.persisted) void refresh();});
}
void mountPrivacy();
