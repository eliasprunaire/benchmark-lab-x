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
// Une obligation composée garde ses éléments séparés, sans séparateur à interpréter
const criterion = value => string(value) || shape(value, {description: string, elements: strings});
const model = m => shape(m, {
  name: string, verdict: v => v === null || string(v),
  cost: c => shape(c, {amount: a => a === null || string(a), currency: string}),
  answer: a => a === null || string(a), evidence: arrayOf(piece)});
// Date du benchmark facultative : les copies gardées avant elle n'en ont pas
const campaign = v => shape(v, {id: string, models: arrayOf(model)}) || shape(v, {id: string, models: arrayOf(model), frozen_at: string});
function validRecord(value) {
  return shape(value, {
    format: v => v === 'bench-x/history/v1', dossier_id: string, content_version: integer,
    need: string, messages: strings,
    revisions: arrayOf(v => shape(v, {number: integer, instruction: string, deliverables: strings,
      criteria: arrayOf(criterion), pieces: arrayOf(piece), qualification: string})),
    campaigns: arrayOf(campaign)
  });
}
function integrity(condition) {
  if (!condition) throw new Error('La copie reçue du serveur est incomplète ou illisible. Aucune nouvelle copie n’a été gardée ; vous pouvez réessayer.');
}
// A silent server is abandoned after 15 s with the same message as an HTTP failure
async function jsonGet(url) {
  try {
    const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: {Accept: 'application/json'}, signal: AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error();
    return await response.json();
  } catch {
    throw new Error('Le serveur n’a pas pu envoyer ce cas. La copie gardée dans ce navigateur n’a pas changé.');
  }
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
    transaction.onabort = () => reject(transaction.error || new Error('L’enregistrement dans ce navigateur a été interrompu. Vous pouvez réessayer.'));
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
      tx.onabort = () => reject(failure || tx.error || new Error('L’enregistrement dans ce navigateur a été interrompu. Vous pouvez réessayer.'));
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
      throw new Error('L’historique local a été effacé ou mis en pause entre-temps, donc cette copie n’a pas été gardée. Réactivez l’historique pour enregistrer de nouveau.');
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
const MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];
// Même forme que date_lisible_utc côté serveur : « 15 septembre 2026 à 08:30:00 UTC » ; null si illisible
function dateLisible(value) {
  const moment = new Date(String(value).replace(/(\.\d{3})\d+/, '$1'));
  if (Number.isNaN(moment.getTime())) return null;
  const deux = n => String(n).padStart(2, '0');
  return moment.getUTCDate() + ' ' + MOIS[moment.getUTCMonth()] + ' ' + moment.getUTCFullYear() + ' à '
    + deux(moment.getUTCHours()) + ':' + deux(moment.getUTCMinutes()) + ':' + deux(moment.getUTCSeconds()) + ' UTC';
}
function mot(texte, ton) {
  const element = node('span', texte);
  element.className = 'mot'; element.dataset.ton = ton;
  return element;
}
// Statuts du serveur traduits avec le vocabulaire des pages du cas ; une valeur inconnue reste lisible telle quelle
const CONTROLE = {QUALIFIED: ['Réussi', 'ok'], BLOCKED: ['Bloqué', 'ko'], PENDING: ['En attente', 'att']};
const VERDICT = {'SATISFAIT': ['Satisfait', 'ok'], 'NE SATISFAIT PAS': ['Ne satisfait pas', 'ko'], 'INDETERMINE': ['Indéterminé', 'inc']};
// Section dépliable du corps d'une fiche, au style du tiroir du site : titre, mots facultatifs, signe plus
function section(titre, parent, extras = [], classe = 'hist-section') {
  const element = node('details'); element.className = 'tiroir ' + classe;
  const summary = node('summary');
  const label = node('span', titre); label.className = 'hist-section-titre';
  const plus = node('span'); plus.className = 'plus'; plus.setAttribute('aria-hidden', 'true');
  summary.append(label);
  if (extras.length) {
    const statuts = node('span'); statuts.className = 'hist-statuts';
    statuts.append(...extras); summary.append(statuts);
  }
  summary.append(plus);
  const corps = node('div'); corps.className = 'hist-section-corps';
  element.append(summary, corps); parent.append(element);
  return corps;
}
function documents(titre, items, parent) {
  if (!items.length) return;
  parent.append(node('h4', titre));
  for (const item of items) section(item.name, parent, [], 'hist-document').append(node('pre', item.text));
}
function liste(titre, items, parent) {
  if (!items.length) return;
  parent.append(node('h4', titre));
  const list = node('ul');
  for (const item of items) {
    if (string(item)) { list.append(node('li', item)); continue; }
    const elements = node('ul');
    item.elements.forEach(element => elements.append(node('li', element)));
    const entry = node('li', item.description);
    entry.append(elements);
    list.append(entry);
  }
  parent.append(list);
}
function renderRecord(record, parent) {
  // La demande entière : le titre de la face reste coupé à deux lignes
  const demande = node('div'); demande.className = 'hist-bloc';
  const texte = node('p', record.need); texte.className = 'hist-demande';
  // Le cadre porte la marge intérieure : la coupe à quatre lignes ne laisse rien dépasser
  const cadre = node('div'); cadre.className = 'hist-demande-cadre'; cadre.append(texte);
  demande.append(node('h3', 'Votre demande'), cadre);
  if (record.need.length > 280) {
    texte.classList.add('replie');
    const lire = node('button', 'Lire la demande entière'); lire.type = 'button'; lire.className = 'lien hist-lire';
    lire.setAttribute('aria-expanded', 'false');
    lire.addEventListener('click', () => {
      const ouvert = texte.classList.toggle('replie') === false;
      lire.textContent = ouvert ? 'Réduire la demande' : 'Lire la demande entière';
      lire.setAttribute('aria-expanded', String(ouvert));
    });
    demande.append(lire);
  }
  parent.append(demande);
  if (record.messages.length) {
    const messages = node('ol'); messages.className = 'hist-messages';
    record.messages.forEach(message => messages.append(node('li', message)));
    section('Vos messages (' + record.messages.length + ')', parent).append(messages);
  }
  for (const revision of record.revisions) {
    const [statut, ...suite] = revision.qualification.split('\n');
    const etat = CONTROLE[statut];
    const corps = section('Version ' + revision.number + ' de l’exemple', parent, etat ? [mot(...etat)] : []);
    if (revision.instruction) {
      const consigne = node('p', revision.instruction); consigne.className = 'hist-consigne';
      corps.append(node('h4', 'Consigne'), consigne);
    }
    liste('Livrables', revision.deliverables, corps);
    liste('Critères', revision.criteria, corps);
    documents('Pièces', revision.pieces, corps);
    const controle = node('div'); controle.className = 'hist-controle';
    controle.append(node('h4', 'Contrôle de l’exemple'));
    for (const ligne of etat ? suite : [revision.qualification]) controle.append(node('p', ligne));
    if (controle.childElementCount > 1) corps.append(controle);
  }
  record.campaigns.forEach((campaign, index) => {
    const date = campaign.frozen_at ? dateLisible(campaign.frozen_at) : null;
    const corps = section('Benchmark ' + (index + 1) + (date ? ' du ' + date : ''), parent);
    for (const model of campaign.models) {
      const cost = model.cost.amount === null ? 'Coût inconnu (' + model.cost.currency + ')'
        : model.cost.amount + ' ' + model.cost.currency;
      const verdict = VERDICT[model.verdict] ?? [model.verdict ?? 'Verdict indisponible', 'inc'];
      const prix = node('span', cost); prix.className = 'hist-cout';
      const result = section(model.name, corps, [mot(...verdict), prix], 'hist-modele');
      result.append(node('h4', 'Réponse'), node('pre', model.answer === null ? 'Aucune réponse conservée' : model.answer));
      documents('Preuves', model.evidence, result);
    }
  });
}
// Face d'une fiche de l'historique : le besoin, la dernière version, les verdicts comptés ; rien ne s'y reprend
function face(record) {
  const summary = node('summary');
  const titre = node('span', record.need || 'Cas ' + record.dossier_id); titre.className = 'hist-titre';
  const count = record.campaigns.length;
  const meta = node('span', 'Version ' + (record.revisions.at(-1)?.number ?? 0) + ' · ' + (count ? count + ' benchmark' + (count > 1 ? 's' : '') : 'aucun benchmark'));
  meta.className = 'hist-meta';
  const mots = node('span'); mots.className = 'mots';
  const verdicts = record.campaigns.flatMap(campaign => campaign.models.map(model => model.verdict));
  for (const [verdict, ton, un, plusieurs] of [['SATISFAIT', 'ok', ' satisfait', ' satisfaits'],
    ['NE SATISFAIT PAS', 'ko', ' non satisfait', ' non satisfaits'], ['INDETERMINE', 'inc', ' indéterminé', ' indéterminés']]) {
    const n = verdicts.filter(value => value === verdict).length;
    if (!n) continue;
    const mot = node('span', n + (n > 1 ? plusieurs : un)); mot.className = 'mot'; mot.dataset.ton = ton;
    mots.append(mot);
  }
  const copie = node('span', 'Copie en lecture seule'); copie.className = 'hist-copie';
  summary.append(titre, meta, mots, copie);
  return summary;
}
const sansAccents = value => value.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
async function post(url, body) {
  const response = await fetch(url, {method: 'POST', credentials: 'same-origin', cache: 'no-store',
    redirect: 'error', keepalive: true, headers: {'Content-Type': 'application/json', Accept: 'application/json'},
    body: JSON.stringify(body)});
  if (!response.ok) throw new Error(response.status === 409
    ? 'Cet exemple ou ce choix a changé entre-temps. Actualisez la page, puis recommencez.'
    : 'Votre demande n’a pas été confirmée. Actualisez la page pour vérifier que votre espace est toujours ouvert.');
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
      const localOutcome = localDeleted ? 'Copie de ce navigateur effacée. ' : 'Effacement de la copie de ce navigateur non confirmé. ';
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
            ? 'Ce cas est supprimé du serveur. Des copies peuvent rester dans ses sauvegardes.'
            : 'Suppression demandée au serveur : l’effacement sera fait lors du prochain nettoyage planifié. La contribution liée à ce cas est retirée.');
          const fields = form.querySelector('fieldset');
          if (localDeleted && fields) fields.disabled = true;
        }
      } catch (error) {
        if (status) status.textContent = action === 'delete'
          ? localOutcome + 'La suppression sur le serveur, contribution comprise, n’est pas confirmée. Vous pouvez réessayer.'
          : error.message || 'Votre demande n’a pas été confirmée. Actualisez la page, puis recommencez.';
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
    if (marker.dataset.dossierId && marker.dataset.campaignId) body.campaign_id = marker.dataset.campaignId;
    try {await post('/preparation/activity', body);}
    catch {
      const status = marker.querySelector('[data-privacy-status]');
      if (status) status.textContent = 'Votre accès n’a pas pu être prolongé. Actualisez la page pour vérifier que votre espace est toujours ouvert.';
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
    status.textContent = 'Ce lien de retour n’est pas valide. Revenez à la page Benchmark.';
    button.disabled = true;
    return;
  }
  const key = 'bench-x-session-opening';
  const blocked = 'Les cookies de ce site semblent bloqués. Autorisez-les, puis choisissez Continuer. La page ne réessaiera pas d’elle-même.';
  const open = async manual => {
    if (pending) return;
    try {
      if (!manual && sessionStorage.getItem(key)) {status.textContent = blocked; return;}
      sessionStorage.setItem(key, '1');
    } catch {
      if (!manual) {status.textContent = 'Votre navigateur bloque le stockage temporaire de ce site. Choisissez Continuer pour ouvrir votre espace vous-même.'; return;}
    }
    pending = true; button.disabled = true;
    status.textContent = 'Ouverture de votre espace…';
    try {
      await post('/preparation/session/open', {});
      // The session cookie is HttpOnly; verify it via the destination, never document.cookie
      const response = await fetch(target.pathname + target.search, {credentials: 'same-origin',
        cache: 'no-store', redirect: 'error', headers: {Accept: 'text/html'}});
      if (!response.ok || !response.headers.get('content-type')?.includes('text/html')) {
        throw new Error('Votre espace n’a pas pu être ouvert. Choisissez Continuer pour réessayer.');
      }
      const next = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (next.querySelector('[data-privacy-bootstrap]')) {status.textContent = blocked; return;}
      location.replace(target.href);
    } catch (error) {
      status.textContent = error.message || 'Impossible d’ouvrir votre espace. Vérifiez que les cookies de ce site sont autorisés, puis choisissez Continuer.';
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
    for (const element of historyRoots) element.querySelector(':scope > [data-privacy-status]').textContent =
      'Ce navigateur ne permet pas d’ouvrir l’historique local. Impossible de vérifier quelles copies y sont gardées.';
    return;
  }
  for (const element of historyRoots) {
    // Its own status only: after launch, the folded consent form carries another one inside this element
    const status = element.querySelector(':scope > [data-privacy-status]');
    const run = async (button, operation) => {
      button.disabled = true;
      try {await operation();}
      catch (error) {
        // A failure must be readable: open the folded panel that holds this status, without moving focus
        if (element instanceof HTMLDetailsElement) element.open = true;
        status.textContent = error.name === 'QuotaExceededError'
        ? 'Ce navigateur manque de place. Votre copie précédente est intacte. Exportez vos cas avant de libérer de la place.'
        : error.message || 'L’action n’a pas abouti dans ce navigateur. Aucune copie n’est confirmée ; vous pouvez réessayer.';
      }
      finally {button.disabled = false;}
    };
    if (element.hasAttribute('data-privacy-history')) {
      const list = element.querySelector('[data-privacy-list]');
      // Six fiches visibles d'abord ; le choix d'afficher les autres survit aux mises à jour de la liste
      const PREMIERES = 6;
      let display = 0, toutes = false;
      const update = async () => {
        const serial = ++display;
        await connect();
        const records = store ? await store.list() : [];
        const state = store ? await store.state() : {enabled: false, chosen: false};
        if (serial !== display) return;
        list.replaceChildren();
        status.textContent = state.enabled ? (records.length ? 'Cas gardés dans ce navigateur : ' + records.length + '.' : 'Aucun cas gardé dans ce navigateur pour l’instant.')
          : !state.chosen && !records.length ? 'Historique local désactivé : rien n’est enregistré dans ce navigateur tant que vous ne l’activez pas.'
          : records.length ? 'Historique local en pause : aucune nouvelle copie n’est enregistrée. Cas encore gardés : ' + records.length + '.'
          : 'Historique local effacé et en pause. Activez-le de nouveau pour garder de nouvelles copies.';
        element.querySelector('[data-privacy-action="enable"]').hidden = state.enabled;
        element.querySelector('[data-privacy-action="clear"]').hidden = !store;
        list.classList.toggle('hist-liste', records.length > 0);
        let filtre, tout;
        if (records.length > PREMIERES) {
          const outils = node('div'); outils.className = 'hist-outils';
          filtre = node('input'); filtre.type = 'search'; filtre.placeholder = 'Filtrer les fiches';
          filtre.setAttribute('aria-label', 'Filtrer les fiches');
          tout = node('button', 'Afficher toutes les fiches (' + records.length + ')'); tout.type = 'button'; tout.className = 'btn btn-sec';
          outils.append(filtre, tout); list.append(outils);
        }
        const fiches = [];
        const montrer = () => {
          const cherche = filtre ? sansAccents(filtre.value.trim()) : '';
          fiches.forEach(([entry, texte], index) => {
            entry.hidden = cherche ? !texte.includes(cherche) : !toutes && index >= PREMIERES;
          });
          if (tout) tout.hidden = toutes || !!cherche;
        };
        for (const record of records) {
          const entry = node('details'); entry.className = 'hist-fiche';
          entry.append(face(record)); list.append(entry);
          fiches.push([entry, sansAccents(record.need || '')]);
          const corps = node('div'); corps.className = 'hist-corps';
          const actions = node('div'); actions.className = 'hist-actions';
          const download = node('button', 'Télécharger ce cas (JSON)'); download.type = 'button'; download.className = 'btn btn-sec';
          download.addEventListener('click', () => run(download, async () => {
            const current = await store.get(record.dossier_id);
            if (!current) throw new Error('Cette copie a été effacée dans un autre onglet.');
            const url = URL.createObjectURL(new Blob([JSON.stringify(current, null, 2)], {type: 'application/json'}));
            const link = node('a');
            link.href = url;
            link.download = 'evaly-' + record.dossier_id.replace(/[^a-z0-9_-]/gi, '_') + '.json';
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 0);
          }));
          const remove = node('button', 'Effacer et ne plus garder ce cas'); remove.type = 'button'; remove.className = 'btn btn-sec';
          remove.addEventListener('click', () => run(remove, async () => {await store.remove(record.dossier_id); await changed();}));
          remove.classList.add('hist-effacer');
          const reference = node('span', 'Enregistrement n° ' + record.content_version); reference.className = 'hist-ref';
          // Actions en bas de la fiche, après tout le contenu lu
          renderRecord(record, corps); actions.append(download, remove, reference); corps.append(actions); entry.append(corps);
        }
        filtre?.addEventListener('input', montrer);
        tout?.addEventListener('click', () => {toutes = true; montrer();});
        montrer();
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
      const active = element.hasAttribute('data-privacy-activity') || element.hasAttribute('data-privacy-manual');
      const update = async () => {
        await connect();
        const state = store ? await store.state(id) : {enabled: false, chosen: false};
        archive.hidden = !active || !state.enabled;
        enable.hidden = !active || !state.chosen || state.enabled;
        // Carte de la liste : message seulement après une action (réussite ou erreur), jamais l'état par défaut sur chaque carte
        if (element.hasAttribute('data-privacy-manual')) return;
        if (!state.chosen) status.textContent = 'Historique local désactivé : ce cas n’est pas gardé dans ce navigateur. Pour l’activer, cochez la case de contribution ou passez par Mes cas d’usage.';
        else if (!state.enabled) status.textContent = 'Historique local en pause : ce cas n’est plus gardé dans ce navigateur. Réactivez-le dans Mes cas d’usage, ou pour ce cas seulement avec le bouton ci-dessous.';
      };
      refreshers.push(update);
      const save = () => run(archive, async () => {
        status.textContent = 'Copie en cours dans ce navigateur…';
        const saved = await store.archive(id, Number(element.dataset.contentVersion || 0));
        status.textContent = saved ? 'Copie gardée dans ce navigateur.' : 'Ce navigateur a déjà la version la plus récente de ce cas.';
        await changed();
      });
      archive.addEventListener('click', save);
      enable.addEventListener('click', () => run(enable, async () => {
        // A per-case action never silently lifts the global pause
        await store.enable(id); await changed();
        if (!(await store.state()).enabled) status.textContent = 'L’historique est réactivé pour ce cas, mais il reste en pause pour tout ce navigateur. Réactivez-le aussi dans Mes cas d’usage.';
      }));
      await update();
      if (element.hasAttribute('data-privacy-activity') && element.hasAttribute('data-content-version') && store && (await store.state(id)).enabled) await save();
    }
  }
  window.addEventListener('focus', () => {void refresh();});
  window.addEventListener('pageshow', event => {if (event.persisted) void refresh();});
}
void mountPrivacy();
