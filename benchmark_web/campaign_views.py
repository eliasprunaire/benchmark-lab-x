"""Rendu HTML des campagnes : configurations, lancement, comparaison, preuves.

Ce module met en forme tout ce qui porte sur une campagne d'un cas d'usage :
le choix des configurations, les deux formes de lancement (demandeur et
opérateur), la comparaison et ses filtres, le détail d'une tentative, les
évaluations et les comparaisons déjà enregistrées.

Il ne possède ni le gabarit de page, ni les erreurs, ni l'accès fournisseur, ni
la préparation : ces domaines restent dans `views`. Il n'importe pas `views`,
pour qu'aucun cycle ne soit possible.
"""
import re
import secrets

from benchmark.storage import _strict_json as encode

from .fragments import (badge, date_lisible_utc, form, hidden, icon, listing, montant_lisible,
                        readable_fields, section, state_block, text)

COMPARISON_FOCUS_SCRIPT = """document.addEventListener('click', event => {
  const link = event.target.closest('tr[id] [data-result]');
  if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  history.replaceState({...history.state, comparisonFocus: link.closest('tr').id}, '');
});
window.addEventListener('pageshow', () => {
  const row = document.getElementById(history.state?.comparisonFocus);
  if (row) row.focus({preventScroll: true});
});
(() => {
  const dialog = document.getElementById('result-dialog');
  if (!dialog || typeof dialog.showModal !== 'function') return;
  const status = dialog.querySelector('.result-status'), body = dialog.querySelector('.result-body');
  let request = null, opener = null;
  const element = (tag, textContent, attrs = {}) => Object.assign(document.createElement(tag), {textContent}, attrs);
  function fail(href) {
    status.textContent = '';
    const alert = element('p', 'Le détail n’a pas pu être chargé. ');
    alert.setAttribute('role', 'alert');
    const retry = element('button', 'Réessayer', {type: 'button', className: 'sec'});
    retry.addEventListener('click', () => load(href));
    alert.append(retry);
    body.replaceChildren(alert);
  }
  function load(href) {
    request?.abort();
    const current = request = new AbortController();
    const timer = setTimeout(() => current.abort(new DOMException('Délai dépassé', 'TimeoutError')), 15000);
    dialog.setAttribute('aria-busy', 'true');
    status.textContent = 'Chargement du détail…';
    const progress = document.createElement('progress');
    progress.setAttribute('aria-hidden', 'true');
    body.replaceChildren(progress);
    fetch(href, {headers: {Accept: 'text/html'}, cache: 'no-store', redirect: 'error', mode: 'same-origin', signal: current.signal})
      .then(response => { if (!response.ok) throw new Error(String(response.status)); return response.text(); })
      .then(html => {
        if (current !== request || !dialog.open) return;
        const part = new DOMParser().parseFromString(html, 'text/html').getElementById('attempt-detail');
        if (!part) throw new Error('fragment');
        body.replaceChildren(...part.childNodes);
        status.textContent = 'Détail chargé.';
      })
      .catch(error => {
        if (current !== request || !dialog.open || error.name === 'AbortError') return;
        fail(href);
      })
      .finally(() => {
        clearTimeout(timer);
        if (current === request) { request = null; dialog.removeAttribute('aria-busy'); }
      });
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('[data-result]');
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    opener = link;
    if (!dialog.open) dialog.showModal();
    load(link.dataset.url);
  });
  dialog.addEventListener('click', event => {
    if (event.target.closest('[data-close]')) { dialog.close(); return; }
    const anchor = event.target.closest('a[href^="#"]');
    if (anchor) {
      const target = document.getElementById(anchor.getAttribute('href').slice(1));
      if (!target || !dialog.contains(target)) return;
      event.preventDefault();
      for (let node = target; node && node !== dialog; node = node.parentElement) {
        if (node.tagName === 'DETAILS') node.open = true;
      }
      target.scrollIntoView({block: 'start'});
      return;
    }
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => {
    request?.abort();
    request = null;
    dialog.removeAttribute('aria-busy');
    status.textContent = '';
    body.replaceChildren();
    opener?.focus();
  });
})();"""

CUSTOM_MODELS_SCRIPT = """(() => {
  const panel = document.getElementById('custom-models');
  const forms = panel.querySelector('.custom-model-forms');
  const template = forms.querySelector('form').cloneNode(true);
  const add = panel.querySelector('[data-add-slug]');
  let busy = false;
  function newRow() {
    const row = template.cloneNode(true);
    const id = 'slug-' + crypto.randomUUID();
    row.querySelector('input[name=slug]').id = id;
    row.querySelector('label').htmlFor = id;
    row.querySelector('input[name=action_id]').value = crypto.randomUUID();
    forms.append(row);
    row.querySelector('input[name=slug]').focus();
  }
  add.hidden = false;
  add.addEventListener('click', newRow);
  function status(target, message, failed = false) {
    target.setAttribute('role', failed ? 'alert' : 'status');
    target.textContent = message;
  }
  function show(target, record, value) {
    const cost = record.cost;
    status(target, record.detail + (cost ? (cost.status === 'KNOWN'
      ? ' Coût signalé : ' + cost.amount + ' USD.' : ' Coût inconnu ; réserve conservée : ' + record.reserve_usd + ' USD.') : ''),
      !record.usable && record.status !== 'EMISSION_POSSIBLE');
    if (record.usable) {
      const model = value.models.find(item => item.id === record.slug);
      const choices = document.getElementById('model-choices');
      if (choices && model && !Array.from(choices.querySelectorAll('input')).some(input => input.value === model.id)) {
        const label = document.createElement('label');
        const input = document.createElement('input');
        input.type = 'checkbox'; input.name = 'models'; input.value = model.id;
        label.append(input, ' ' + model.name + (model.not_adjustable ? ' · palier de raisonnement non réglable' : ''));
        choices.append(label);
      }
    }
  }
  async function follow(operation, target, value) {
    while (true) {
      const request = value.probe_request?.request_id === operation ? value.probe_request : null;
      const record = value.custom_models.find(item => item.operation_id === (request?.operation_id || operation));
      if (request?.error) { status(target, request.error, true); return; }
      if (record) {
        show(target, record, value);
        if (record.status !== 'EMISSION_POSSIBLE') return;
      } else if (request?.pending) target.textContent = 'Vérification du slug et de son accès…';
      else throw new Error('missing');
      await new Promise(resolve => setTimeout(resolve, 3000));
      const response = await fetch(template.action, {headers: {Accept: 'application/json'}, cache: 'no-store',
        redirect: 'error', signal: AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error('unavailable');
      value = await response.json();
    }
  }
  forms.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    const form = event.target, target = form.querySelector('[aria-live]');
    form.querySelector('input[name=slug]').readOnly = true;
    forms.querySelectorAll('button').forEach(button => button.disabled = true);
    add.disabled = true;
    status(target, 'Vérification du slug et de son accès…');
    try {
      const response = await fetch(form.action, {method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded', Accept: 'application/json'},
        body: new URLSearchParams(new FormData(form)), redirect: 'error'});
      const value = await response.json();
      if (!response.ok) status(target, value.error || 'Vérification impossible.', true);
      else await follow(value.probe_operation_id, target, value);
      form.querySelector('input[name=action_id]').value = crypto.randomUUID();
    } catch {
      status(target, 'Suivi interrompu. Actualisez la page pour consulter l’état enregistré ; aucun appel ne sera relancé.', true);
      form.querySelector('button').dataset.uncertain = 'true';
    } finally {
      busy = false; add.disabled = false;
      form.querySelector('input[name=slug]').readOnly = false;
      forms.querySelectorAll('button').forEach(button => button.disabled = button.dataset.uncertain === 'true');
    }
  });
  panel.querySelectorAll('[data-probe-request]').forEach(async target => {
    try {
      const response = await fetch(template.action, {headers: {Accept: 'application/json'}, cache: 'no-store', redirect: 'error'});
      if (!response.ok) throw new Error('unavailable');
      await follow(target.dataset.probeRequest, target, await response.json());
    } catch { target.textContent = 'Suivi interrompu. Actualisez pour consulter l’état enregistré.'; }
  });
})();"""


def render_custom_models(value, csrf, dossier_url):
    content = '<details id="custom-models" class="corr custom-models"><summary class="button sec">Ajouter un slug Openrouter</summary><div>'
    content += '<p id="slug-help">Copiez le slug exact de la fiche Openrouter, par exemple <code>openai/gpt-6-astra</code>.</p>'
    content += '<p class="hint">Un court appel payant avec votre clé vérifie que le modèle répond, sans lancer de benchmark.</p>'
    request = value.get('probe_request', {})
    if request.get('error'):
        content += '<p role="status">' + text(request['error']) + '</p>'
    elif request.get('pending') and not any(record['operation_id'] == request['request_id']
                                          for record in value.get('custom_models', [])):
        content += '<p role="status" data-probe-request="' + text(request['request_id']) + '">Vérification du slug et de son accès…</p>'
    for record in value.get('custom_models', []):
        pending = (' data-probe-request="' + text(record['operation_id']) + '"' if record['status'] == 'EMISSION_POSSIBLE' else '')
        content += '<p><code>' + text(record['slug']) + '</code> : <span role="status"' + pending + '>' + text(record['detail'])
        cost = record['cost']
        if cost:
            content += (' Coût signalé : ' + text(montant_lisible(cost['amount'])) + ' USD.' if cost['status'] == 'KNOWN'
                        else ' Coût inconnu ; réserve conservée : ' + text(montant_lisible(record['reserve_usd'])) + ' USD.')
        content += '</span></p>'
        if record['status'] == 'EMISSION_POSSIBLE':
            content += '<p><progress aria-label="Vérification du modèle"></progress> <a href="' + text(
                dossier_url + '/configurations#custom-models') + '">Actualiser la vérification</a></p>'
    content += '<div class="custom-model-forms">' + form(csrf, dossier_url + '/custom-models',
        {'action_id': secrets.token_hex(16)},
        '<label for="custom-slug">Slug Openrouter</label>'
        '<input id="custom-slug" name="slug" type="text" required maxlength="256" spellcheck="false" '
        'autocapitalize="none" autocomplete="off" aria-describedby="slug-help" placeholder="constructeur/modèle">'
        '<button class="sec" type="submit">Tester et ajouter</button><p role="status" aria-live="polite"></p>')
    content += '</div><button class="sec" type="button" data-add-slug hidden>+ Ajouter une ligne</button>'
    return content + '</div></details><script>' + CUSTOM_MODELS_SCRIPT + '</script>'


def _readable_reason(record):
    """Motif du juge avec les identifiants de critères remplacés par leurs descriptions"""
    spec = record['qualification']['contract']['specification']
    labels = {item['id']: item['description'] for item in spec['obligations'] + spec['eliminatory_errors']}
    if not labels:
        return record['reason']
    return re.sub(r'(?<!\w)(' + '|'.join(map(re.escape, labels)) + r')(?!\w)',
                  lambda match: labels[match[0]], record['reason'])


def render_evaluations(evaluations, dossier_url):
    """Inert evidence and recorded corrections inside the owner's existing page.

    Rendu complet des évaluations enregistrées ; `render_result` fournit la lecture compacte
    """
    content = '<h4>Verdicts et preuves</h4><p>Évaluations fictives, par cas et tentative. '
    content += 'Le verdict porte sur la configuration observée sous les conditions communes ; '
    content += 'il ne prouve ni une propriété du modèle seul ni une compétence métier générale.</p>'
    for record in evaluations:
        eid = record['evaluation_id']
        reason = _readable_reason(record)
        label = ('Évaluation à reprendre (valeur enregistrée : INDETERMINE)' if record['verdict'] == 'INDETERMINE'
                 else record['verdict'] or 'Évaluation à reprendre')
        content += '<section id="evaluation-' + text(eid) + '"><h5>' + text(label) + '</h5>'
        content += '<p role="status">' + text(reason) + '</p><details><summary>Identifiants de cette évaluation</summary><p>Cas ' + text(record['case_id'])
        content += ', configuration ' + text(record['configuration_id']) + ', évaluation ' + text(eid) + '.</p></details>'
        content += '<p>Responsable : ' + text(record['responsible']) + '. Date : ' + text(record['created_at']) + '.</p>'
        previous = record['previous_evaluation_id']
        if previous:
            content += '<details><summary>Évaluation précédente</summary><p>Correction de <a href="#evaluation-' + text(previous) + '">' + text(previous) + '</a>.</p></details>'
        content += '<ul>'
        for finding in record['findings']:
            content += '<li>' + text(finding['finding'])
            content += '<details><summary>Contrôle et attribution</summary><p>' + text(finding['criterion_id'] + ' / ' + finding['control_id'])
            content += ' : ' + text(finding['status']) + ', attribution ' + text(finding['attribution'])
            content += '</p></details>'
            for proof in finding['evidence']:
                link = next(p for p in record['proof_links'] if p['piece_id'] == proof['piece_id'])
                content += '<details><summary>Passage de ' + text(link['name']) + '</summary>'
                content += '<pre>' + text(proof['passage']) + '</pre>'
                target = ('#proof-' + eid + '-' + link['piece_id']
                          if link['piece_id'] in record.get('proof_contents', {}) else link['href'])
                content += '<p><a href="' + text(target) + '">Ouvrir la pièce exacte</a></p></details>'
            content += '</li>'
        content += '</ul><p>Pièces liées à cette évaluation, accessibles dans votre session :</p><ul>'
        for link in record['proof_links']:
            content += '<li>'
            if link['piece_id'] in record.get('proof_contents', {}):
                content += '<details class="proof-content" id="proof-' + text(eid + '-' + link['piece_id'])
                content += '"><summary>Lire la pièce complète : ' + text(link['name']) + '</summary>'
                content += '<div class="proof-text">' + text(record['proof_contents'][link['piece_id']]) + '</div>'
                content += '<p><a href="' + text(dossier_url) + '">Revenir à la comparaison avec ses filtres</a></p></details>'
            else:
                content += '<a href="' + text(link['href']) + '">' + text(link['name']) + '</a>'
            content += '</li>'
        content += '</ul><h6>Mesures prévues</h6><ul>'
        for measure in record['measures']:
            content += '<li>' + text(measure['definition']['measure']) + ' : '
            content += readable_fields('INCONNU' if measure['status'] == 'UNKNOWN' else measure['value'])
            content += ('' if measure['unit'] in ('bool', 'boolean', 'booléen') else ' ' + text(measure['unit']))
            content += '<details><summary>Définition et preuve source</summary>' + readable_fields(measure) + '</details></li>'
        content += '</ul><p>Aucune synthèse de plusieurs cas ou tentatives calculée.</p>'
        for label, cost in (('Dépense candidate', record['candidate_cost']), ('Dépense de jugement', record['judgment']['cost'])):
            content += '<p>' + label + ' : ' + text('INCONNU' if cost is None or cost['status'] == 'UNKNOWN' else cost['amount'] + ' ' + cost['currency'])
            content += '. Source : ' + text(cost['source'] if cost else 'INCONNU') + '.</p>'
        content += '<p>Limites : ' + text('; '.join(record['limits'])) + '</p>'
        for label, value in (('Méthode et qualification du contrat exact', record['qualification']),
                             ('Jugement, consignes, pièces vues, désaccords et arbitrages', record['judgment']),
                             ('Liens connus entre préparation, jugement et candidat', record['configuration_links']),
                             ('Configurations demandée et observée, sources', {k: record[k] for k in ('requested_configuration', 'observed_configuration', 'observation_sources')}),
                             ('Portée du coût et règle d’agrégation', {k: record[k] for k in ('cost_basis', 'aggregation')})):
            content += '<details><summary>' + label + '</summary>' + readable_fields(value) + '</details>'
        content += '<p><a href="' + text(dossier_url) + '">' + ('Revenir à la comparaison' if '/campaigns/' in dossier_url else 'Revenir au cas d’usage') + '</a></p></section>'
    return content


def _numeric(value):
    try:
        return float(value) >= 0
    except (TypeError, ValueError):
        return False


def cost_bar(value, known):
    """Barre proportionnelle au coût le plus élevé connu du même cas ; vide si inconnu"""
    if value is None or not _numeric(value) or not known or max(known) <= 0:
        return '<div class="costbar none" aria-hidden="true"></div>'
    return '<div class="costbar" aria-hidden="true" style="--w:' + str(round(100 * float(value) / max(known))) + '%"></div>'


def effort_label(configuration):
    if configuration.get('effort_limit') == 'not_adjustable':
        return 'Raisonnement non réglable'
    effort = configuration.get('parameters', {}).get('reasoning', {}).get('effort', configuration.get('effort', 'off'))
    if effort == 'off':
        return 'Niveau de raisonnement non renseigné'
    if effort == 'on':
        return 'Raisonnement activé · niveau non renseigné'
    return 'Raisonnement demandé : ' + effort


def short_label(value, words=9):
    parts = value.split()
    return value if len(parts) <= words else ' '.join(parts[:words]) + '…'


def render_comparison(value):
    base, query = value['href'], value['filter_scope']
    multiple_cases = len(value['cases']) > 1
    content = '<nav aria-label="Parcours"><a class="button" href="' + text(value['dossier_href']) + '">Revenir au cas d’usage</a></nav>'
    content += '<p class="hint">Résultats privés · version d’épreuve ' + text(value['task']['version']) + '.</p>'
    content += '<div class="campaign-summary" aria-label="Conclusion de la campagne"><span class="ic">' + icon('i-scale') + '</span>'
    content += '<p class="eyebrow">Bilan de la comparaison</p>'
    latest = {record['attempt_id']: record for record in value['history']}
    for case_number, case in enumerate(value['cases'], 1):
        records = [record for record in latest.values() if record['case_id'] == case['id']]
        if records:
            counts = []
            for verdict, label in (('SATISFAIT', 'conforme'), ('NE SATISFAIT PAS', 'non conforme'), (None, 'à réévaluer')):
                count = sum(record['decision']['verdict'] == verdict for record in records)
                if count:
                    counts.append(str(count) + ' ' + label + ('s' if count > 1 and verdict else ''))
            prefix = '<strong>Cas ' + text(case_number) + '</strong> : ' if multiple_cases else ''
            content += '<p>' + prefix + 'Réponses : ' + text(' · '.join(counts)) + '.</p>'
    if not latest:
        received = any(cell['state'] == 'RECEIVED' for cell in value['cells'])
        content += '<p>' + ('En attente d’évaluation : des réponses ont été reçues, sans verdict disponible.' if received else
                             'Aucun résultat évalué pour cette campagne. Consultez le suivi des essais.') + '</p>'
    pending_reasons = list(dict.fromkeys(item['next_action'] for item in value.get('pending_attempts', [])))
    if pending_reasons:
        content += listing(pending_reasons)
    coverage = value['coverage']
    content += '<p role="status">Réponses évaluées : ' + text(coverage['evaluated_attempts']) + ' · essais lancés : '
    content += text(coverage['attempted_cells']) + ' sur ' + text(coverage['planned_cells']) + '. '
    if value['economic_status'] != 'COMPLETE':
        content += 'Comparaison des coûts incomplète. '
    content += '</p>'
    if value.get('stop_reason') and (coverage['not_started'] or pending_reasons):
        content += '<p>Comparaison incomplète : les essais ont été arrêtés. Les réponses déjà reçues restent consultables.</p>'
    dates = sorted(set(date[:10] for date in value.get('acquisition_dates', [])))
    if dates:
        content += '<p class="hint">Réponses reçues : ' + text(dates[0] if len(dates) == 1 else dates[0] + ' au ' + dates[-1]) + '.</p>'
    content += '</div>'
    if not value['population']:
        return content
    choice = value.get('recommendation')
    if choice:
        content += '<aside class="economic-choice" aria-labelledby="economic-choice-title">'
        content += '<h2 id="economic-choice-title">Notre conseil</h2><div class="choice-highlight"><div>'
        content += '<p class="choice-model">' + text(choice['configuration']['model']) + '</p>'
        content += '<p>' + text(effort_label(choice['configuration'])) + '</p></div>'
        content += '<p class="choice-cost">Coût observé<strong>' + text(montant_lisible(choice['amount']) + ' ' + choice['unit']) + '</strong></p></div>'
        if choice['basis'] == 'quality_and_cost':
            content += '<p>Cette réponse domine les autres sur les qualités et le coût observés pour ce cas.</p>'
        else:
            content += '<p>Qualité observée équivalente ; c’est la moins coûteuse parmi ' + text(choice['count']) + ' réponses conformes.</p>'
        content += '<p class="hint">Un seul exemple ne garantit pas le même résultat sur d’autres tâches.</p></aside>'
    sort_column = next((column for column in value['columns'] if column['id'] == query.get('sort')), None)
    sort_label = (sort_column['definition'].get('measure', 'Coût observé') if sort_column else 'sans tri')
    options = {
        'case': ('Cas d’essai', 'Tous les cas', [(v['id'], 'Cas ' + str(number))
                         for number, v in enumerate(value['cases'], 1)] if multiple_cases else []),
        'configuration': ('Modèle', 'Tous les modèles', [(v['id'], v['model'] +
                           (' · ' + effort_label(v) if sum(p['model'] == v['model'] for p in value['panel']) > 1 else '')) for v in value['panel']]),
        'verdict': ('Résultat', 'Tous les résultats', [('SATISFAIT', 'Satisfait'), ('NE SATISFAIT PAS', 'Ne satisfait pas'), ('A_REPRENDRE', 'À reprendre')]),
        'sort': ('Trier par', 'Sans tri', [(v['id'], v['definition'].get('measure', 'Coût observé')) for v in value['columns']]),
        'direction': ('Sens du tri', 'Croissant', [('desc', 'Décroissant')]),
        'obligation': ('Exigence à examiner', 'Toutes les exigences', [(v['id'] + ':' + state, short_label(v['description']) + ' : ' + label)
                        for v in value['obligations'] for state, label in
                        (('PASS', 'Respectée'), ('FAIL', 'Non respectée'), ('INDETERMINE', 'Indéterminée'))]),
    }
    content += '<section class="comparison-results"><h2>Comparaison des modèles</h2>'
    content += '<p class="table-hint">Sur petit écran, faites défiler le tableau horizontalement.</p>'
    content += '<details id="filters" class="comparison-filters"' + (' open' if query else '') + '><summary>Tris et filtres</summary>'
    content += '<form method="get" action="' + text(base) + '#filters"><div class="filter-grid">'
    advanced = ''
    for key, (label, default, values) in options.items():
        if not values:
            continue
        selected = query.get(key, '')
        if key == 'direction' and selected == 'asc':
            selected = ''
        control = '<div><label for="filter-' + key + '">' + label + '</label><select id="filter-' + key + '" name="' + key + '">'
        for val, title in [('', default)] + values:
            control += '<option value="' + text(val) + '"' + (' selected' if selected == val else '') + '>' + text(title) + '</option>'
        control += '</select></div>'
        if key == 'obligation':
            advanced = '<details class="filter-advanced"' + (' open' if key in query else '') + '><summary>Filtrer par exigence</summary>'
            advanced += '<p class="hint">Pour retrouver les réponses qui respectent ou non une exigence précise.</p>' + control + '</details>'
        else:
            content += control
    content += '</div>' + advanced + '<div class="actions"><button type="submit">Appliquer</button>'
    content += '<a class="button sec" href="' + text(base) + '#filters">Effacer</a></div></form></details>'
    content += '<p class="view-scope" role="status">Résultats affichés : ' + text(len(value['rows'])) + ' sur '
    content += text(len(value['population'])) + ' · ' + text(sort_label)
    if sort_column:
        content += ', décroissant' if query.get('direction') == 'desc' else ', croissant'
    content += '.</p>'
    if not value['rows']:
        content += '<p role="status">Aucune ligne ne correspond aux filtres ; les observations de la campagne restent conservées.</p>'
    for case_number, case in enumerate(value['cases'], 1):
        rows = [r for r in value['rows'] if r['case_id'] == case['id']]
        if not rows:
            continue
        label = 'Cas ' + str(case_number) if multiple_cases else 'Comparaison des modèles'
        if multiple_cases:
            content += '<h3>' + text(label) + '</h3>'
        content += '<div class="table-scroll" role="region" tabindex="0" aria-label="' + text(label) + '">'
        content += '<table><caption>Chaque verdict concerne la réponse obtenue sur cet exemple.</caption><thead><tr>'
        quality_columns = [column for column in value['columns'] if 'criterion_id' in column]
        titles = ['Modèle', 'Résultat'] + (['Qualité observée'] if quality_columns else []) + ['Coût observé', 'Détails']
        for title in titles:
            content += '<th scope="col">' + text(title) + '</th>'
        content += '</tr></thead><tbody>'
        known = [float(r['cost']['value']) for r in rows
                 if r['cost']['value'] is not None and _numeric(r['cost']['value'])]
        for row in rows:
            content += '<tr id="attempt-' + text(row['attempt_id']) + '"' + ('' if row['verdict'] == 'SATISFAIT' else ' class="out"') + ' tabindex="-1"><th scope="row">'
            content += '<strong>' + text(row['requested_configuration']['model']) + '</strong>'
            content += '<p class="hint">' + text(effort_label(row['requested_configuration'])) + '</p></th>'
            reason = {'SATISFAIT': 'Toutes les exigences sont respectées.',
                      'NE SATISFAIT PAS': 'Un critère requis n’est pas respecté.'}.get(row['verdict'], 'Évaluation à compléter.')
            content += '<td>' + badge(row['verdict']) + '<p class="hint">' + reason + '</p></td>'
            if quality_columns:
                content += '<td><ul class="quality-list">'
                for column in quality_columns:
                    measure = next(m for m in row['measures'] if m['criterion_id'] == column['criterion_id'])
                    shown = 'Inconnue' if measure['value'] is None else str(measure['value']).capitalize()
                    content += '<li><strong>' + text(column['definition']['measure']) + '</strong> : ' + text(shown) + '</li>'
                    if measure['rank'] is None:
                        content += '<li class="hint">Non comparable : ' + text(measure['reason']) + '</li>'
                content += '</ul></td>'
            content += '<td>'
            cost = row['cost']
            content += '<span class="source-value">' + text('Inconnu' if cost['value'] is None else montant_lisible(cost['value']) + ' ' + cost['unit']) + '</span>'
            if cost['value'] is not None and cost['rank'] is None:
                content += '<p class="hint">Coût non comparable</p>'
            content += cost_bar(cost['value'], known) + '</td>'
            content += '<td><button type="button" class="text-link" data-result data-url="' + text(row['detail_href']) + '">Détail et preuves</button></td></tr>'
        content += '</tbody></table></div>'
    content += '</section><details id="method"><summary>Comment lire ces résultats</summary>'
    content += '<ul><li><strong>Satisfait</strong> : toutes les exigences sont respectées et aucune erreur éliminatoire n’a été relevée.</li>'
    content += '<li>Comparez le coût des réponses satisfaisantes, puis consultez leurs qualités et limites dans « Détail et preuves ». Un coût inconnu ne change pas le verdict.</li>'
    content += '<li>Les modèles reçoivent les mêmes consignes et pièces. Ces résultats concernent uniquement cet exemple fictif, sans garantir la même qualité sur d’autres tâches.</li></ul></details>'
    content += '<dialog id="result-dialog" class="result-dialog" aria-label="Détail et preuves">'
    content += '<div class="result-head"><button type="button" class="sec" data-close autofocus>Fermer</button>'
    content += '<p class="result-status" role="status"></p></div><div class="result-body"></div>'
    content += '</dialog>'
    return content


def render_campaign_models(value):
    content = '<p>Voici les modèles et réglages retenus pour cette comparaison. Cette consultation ne modifie pas la comparaison et ne lance aucun appel.</p>'
    for model in value['panel']:
        content += section(model['model'], '<p>' + text(effort_label(model)) + '</p>' +
                           '<details><summary>Réglages utilisés</summary>' + readable_fields(model['parameters']) + '</details>')
    content += '<p><a class="button" href="' + text(value['href']) + '">Revenir aux résultats</a></p>'
    content += '<p class="hint">Pour changer les modèles ou l’exemple, préparez une nouvelle comparaison depuis le cas d’usage. Les résultats actuels seront conservés.</p>'
    return content


def render_configurations(value, csrf):
    """Choix des modèles et du palier, puis estimation de la sélection courante"""
    dossier_url = '/preparation/dossiers/' + value['dossier_id']
    content = '<p><a href="' + text(dossier_url) + '">Revenir au cas d’usage</a></p>'
    content += '<p role="status">Choisissez au moins deux modèles et un niveau de raisonnement. Aucun appel candidat ne part à cette étape.</p>'
    if not value.get('catalogue_available', True):
        content += '<p>' + text(value['detail']) + '</p>'
        if value.get('personal_preparation'):
            content += render_custom_models(value, csrf, dossier_url)
    else:
        if value.get('catalogue_stale'):
            content += '<p role="status">Ce relevé a expiré ; son actualisation n’a pas abouti. Le dernier relevé valide reste consultable.</p>'
        choices = ''
        for model in value['models']:
            checked = ' checked' if model['selected'] else ''
            choices += '<label><input type="checkbox" name="models" value="' + text(
                model['id']) + '"' + checked + '> ' + text(model['name'])
            if model['not_adjustable']:
                choices += ' · palier de raisonnement non réglable'
            choices += '</label>'
        labels = {'none': 'none (désactivé)', 'minimal': 'minimal', 'low': 'low (faible)',
                  'medium': 'medium (moyen)', 'high': 'high (élevé)', 'xhigh': 'xhigh', 'max': 'max',
                  'standard': 'Non réglable'}
        tiers = '<label for="reasoning-effort">Niveau de raisonnement demandé</label>'
        tiers += '<select id="reasoning-effort" form="configurations-form" name="tier" aria-describedby="reasoning-help">'
        tiers += ''.join('<option value="' + text(tier) + '"' +
                         (' selected' if value['current_tier'] == tier else '') + '>' +
                         text(labels.get(tier, tier)) + '</option>' for tier in value['available_tiers'])
        tiers += '</select><p class="hint" id="reasoning-help">Le niveau choisi est envoyé aux modèles qui le permettent. '
        tiers += 'Un niveau incompatible est refusé, sans remplacement automatique. Un effort plus élevé peut augmenter le délai et le coût, sans garantir une meilleure réponse.</p>'
        content += ('<form id="configurations-form" method="post" action="' + text(dossier_url + '/configurations') + '">' +
                    hidden('csrf_token', csrf) + '<fieldset id="model-choices"><legend>Modèles à comparer</legend>' +
                    choices + '</fieldset></form>')
        if value.get('personal_preparation'):
            content += render_custom_models(value, csrf, dossier_url)
        content += ('<fieldset><legend>Raisonnement</legend>' + tiers +
                    '</fieldset><button form="configurations-form"' + (' class="sec"' if value['configurations'] else '') +
                    ' type="submit">Enregistrer les configurations</button>')
    if value['configurations']:
        model_names = {model['id']: model['name'] for model in value['models']}
        summary = '<ul>'
        for configuration in value['configurations']:
            amount = configuration['estimate']['amount_usd']
            technical = configuration['model']
            detail = ' · estimation ' + (
                'non estimable' if amount is None else montant_lisible(amount) + ' USD')
            detail += ' · ' + effort_label(configuration)
            summary += '<li>' + text(model_names.get(technical, technical)) + text(detail) + (
                '<details><summary>Identifiant technique</summary><code>' +
                text(technical) + '</code></details></li>')
        summary += '</ul>'
        summary += '<p>Estimation totale : ' + text(
            'non estimable' if value['estimate_total_usd'] is None else
            montant_lisible(value['estimate_total_usd']) + ' USD') + '.</p>'
        summary += '<p><a class="button" href="' + text(
            dossier_url + '/campaigns/' + value['current_campaign_id'] +
            '/conditions') + '">Voir le récapitulatif</a></p>'
        content += section('Sélection courante', summary)
    return content


def campaign_followup(campaign):
    """État d’affichage seulement : ne crée aucune autorité ni reprise"""
    cells = campaign['cells']
    judgment = campaign.get('judgment')
    if judgment:
        status = judgment['status']
        if status == 'COMPLETE':
            return False, True, 'Évaluation terminée. Les résultats sont disponibles.'
        if status == 'BLOCKED':
            return False, False, judgment.get('reason') or 'Évaluation interrompue. Aucune relance automatique.'
        if status in ('WAITING', 'RUNNING') and cells and all(c['state'] == 'RECEIVED' for c in cells):
            return True, False, ('Évaluation en attente. Les réponses sont conservées.' if status == 'WAITING'
                                 else 'Évaluation en cours. Les réponses sont vérifiées selon les critères de cet exemple.')
        if status == 'NOT_STARTED' and cells and all(c['state'] == 'RECEIVED' for c in cells):
            return False, False, 'Réponses conservées. Leur évaluation n’a pas été lancée.'
    if campaign.get('stop_reason') or campaign.get('restore_pending') or not campaign.get('admission_open'):
        return False, False, 'Admission fermée. Suivi automatique arrêté ; les réponses reçues restent consultables.'
    if any(a.get('incident') or a.get('attribution_incident') for a in campaign['attempts']):
        return False, False, 'Incident à vérifier. Suivi automatique arrêté, sans relance.'
    if any(c['state'] == 'AMBIGUOUS' for c in cells) or campaign.get('state') == 'BLOCKED':
        return False, False, 'Vérification requise. Une tentative reste incertaine ; aucune relance automatique.'
    if any(c['state'] == 'EMISSION_POSSIBLE' for c in cells):
        return True, False, 'Comparaison en cours. Les réponses arrivent progressivement.'
    if cells and all(c['state'] == 'RECEIVED' for c in cells):
        return False, True, 'Réponses reçues. En attente d’évaluation pour les réponses sans verdict.'
    if any(c['state'] == 'INTENT_RECORDED' for c in cells):
        return True, False, 'En attente de démarrage. Votre lancement est enregistré.'
    return False, False, 'En attente de démarrage. Aucune activité observée ; actualisez pour vérifier le suivi.'


def render_campaign_followup(value, csrf):
    campaign = value['campaign']
    base = '/preparation/dossiers/' + value['dossier_id'] + '/campaigns/' + campaign['campaign_id']
    active, ready, message = campaign_followup(campaign)
    content = '<div id="campaign-followup"' + (' data-results-href="' + text(base) + '"' if ready else '') + '>'
    content += '<div id="campaign-status" aria-live="polite">'
    content += state_block('wait' if active else 'done' if ready else 'action', 'Suivi de la comparaison',
                           'Lancement enregistré', '<p>' + text(message) + '</p>')
    states = {'NOT_STARTED': 'non démarré', 'INTENT_RECORDED': 'en attente de démarrage',
              'EMISSION_POSSIBLE': 'en cours', 'RECEIVED': 'réponse reçue',
              'AMBIGUOUS': 'état incertain, vérification requise'}
    models = {item['id']: item['model'] for item in campaign['panel']}
    content += section('Suivi des essais', listing(
        models.get(cell['configuration_id'], 'Configuration') + ' : ' + states.get(cell['state'], 'état inconnu')
        for cell in campaign['cells']))
    received = sum(cell['state'] == 'RECEIVED' for cell in campaign['cells'])
    all_received = bool(campaign['cells']) and received == len(campaign['cells'])
    content += '<p>' + str(received) + ' réponse(s) reçue(s) sur ' + str(len(campaign['cells'])) + '.</p>'
    judgment = campaign.get('judgment')
    if judgment:
        content += '<p>' + text(judgment['completed']) + ' évaluation(s) terminée(s) sur ' + text(judgment['total']) + '.</p>'
    content += '</div>'
    if active:
        content += '<div id="preparation-progress"><progress aria-label="Comparaison en cours"></progress>'
        content += '<p class="hint" role="status">Suivi automatique disponible avec JavaScript.</p><div class="actions">'
        content += '<a class="button" href="' + text(base + '/conditions') + '">Actualiser le suivi</a>'
        content += '<button type="button" class="sec" hidden>Suspendre le suivi automatique</button></div></div>'
    else:
        content += '<p><a class="button' + (' sec' if all_received else '') + '" href="' + text(base + '/conditions') + '">Actualiser le suivi</a></p>'
    if judgment and judgment['can_start']:
        content += form(csrf, base + '/evaluate', {'confirm': 'yes'},
            '<p>Votre clé personnelle finance l’évaluation des réponses déjà reçues. Aucun modèle candidat ne sera relancé.</p>'
            '<button type="submit">Évaluer les réponses conservées</button>')
    if not active and (ready or not judgment or judgment['completed'] > 0):
        content += '<p><a class="button' + ('' if all_received else ' sec') + '" href="' + text(base) + '">Comparer les résultats et lire les preuves</a></p>'
    return content + '</div>'


def render_campaign_launch_requester(value, csrf):
    """Contrôles et suivi présentés au demandeur qui lance lui-même"""
    campaign = value['campaign']
    if campaign['attempts']:
        return render_campaign_followup(value, csrf)
    dossier_url = '/preparation/dossiers/' + value['dossier_id']
    base = dossier_url + '/campaigns/' + campaign['campaign_id']
    content = '<p><a href="' + text(dossier_url) + '">Revenir au cas d’usage</a></p>'
    content += section('Ce qui sera testé', '<p>' + text(value['criteria']['result_expected']) + '</p>' +
        listing(item['model'] for item in campaign['panel']) +
        '<p>Chaque modèle reçoit la même consigne et les mêmes pièces. Le verdict reste limité à cet exemple et aux configurations observées.</p>' +
        '<details><summary>Critères et conditions exactes</summary>' + readable_fields(
            {'criteria': value['criteria'], 'conditions': campaign['conditions'], 'panel': campaign['panel']}) + '</details>')
    content += '<p>Tous les appels utilisent votre clé Openrouter et son plafond unique. Les estimations ne sont pas des dépenses facturées.</p>'
    check_content = '<ul>'
    for check in value['checks']:
        detail = check['detail']
        if type(detail) is dict:
            detail = ('Crédit restant : ' + str(detail.get('limit_remaining_usd')
                      if detail.get('limit_remaining_usd') is not None else 'INCONNU') +
                      ' USD ; plafond de la clé : ' + str(detail.get('limit_usd')
                      if detail.get('limit_usd') is not None else 'INCONNU') + ' USD')
        check_content += '<li>' + text(('✓ ' if check['ok'] else '✕ ') + str(detail))
        if check['key'] == 'example_qualified' and check.get('findings'):
            check_content += '<p>Constats de qualification</p>' + listing(
                finding['text'] for finding in check['findings'])
        check_content += '</li>'
    check_content += '</ul>'
    content += section('Contrôles avant lancement', check_content)
    if value.get('judgment_estimate_usd') is not None:
        content += '<p>Évaluation estimée : ' + text(montant_lisible(value['judgment_estimate_usd'])) + ' USD, financée par votre clé personnelle.</p>'
    failed = next((check for check in value['checks'] if not check['ok']), None)
    if value['launchable']:
        content += '<p role="status">Les contrôles sont satisfaits. Vérifiez le travail, les modèles et les coûts estimés avant de confirmer le lancement.</p>'
        content += form(csrf, base + '/start', {
            'manifest_version': campaign['version'],
            'frozen_at': campaign['conditions']['frozen_at']},
            '<label><input type="checkbox" name="confirm" value="yes" required> '
            'Je confirme le lancement de cette comparaison.</label>'
            '<button type="submit">Lancer la comparaison</button>')
    elif failed:
        links = {
            'example_validated': dossier_url + '#validation',
            'example_qualified': dossier_url,
            'configurations_available': dossier_url + '/configurations',
            'access_connected': '/preparation/access',
            'estimate_available': dossier_url + '/configurations',
        }
        content += '<p role="status">Lancement indisponible : ' + text(
            failed['detail'] if type(failed['detail']) is str else
            'connectez votre accès Openrouter') + '.'
        if failed['key'] in links:
            content += ' <a class="button" href="' + text(links[failed['key']]) + '">Compléter cette étape</a>'
        content += '</p>'
    else:
        content += '<p role="status">Lancement indisponible. Le responsable doit vérifier la disponibilité de l’exécution.</p>'
        content += '<p><a class="button" href="' + text(dossier_url) + '">Revenir au cas d’usage</a></p>'
    return content


def render_campaign_launch_operator(value, csrf):
    """Comparaison préparée et admise par l'opérateur, confirmée par le demandeur"""
    campaign = value['campaign']
    if campaign['attempts']:
        return render_campaign_followup(value, csrf)
    base = '/preparation/dossiers/' + value['dossier_id'] + '/campaigns/' + campaign['campaign_id']
    content = '<p>Le responsable prépare et autorise cette comparaison. Votre confirmation déclenche uniquement les essais qu’il a admis.</p>'
    content += '<p><a href="' + text('/preparation/dossiers/' + value['dossier_id']) + '">Revenir au cas d’usage</a></p>'
    groups = '<div class="crit">'
    if value['criteria']['eliminatory_errors']:
        groups += '<div class="grp elim"><h3>' + badge('NE SATISFAIT PAS') + 'Erreurs éliminatoires</h3>' + listing(item['description'] for item in value['criteria']['eliminatory_errors']) + '</div>'
    groups += '<div class="grp oblig"><h3>' + badge('SATISFAIT') + 'Obligations à prouver</h3>' + listing(item['description'] for item in value['criteria']['obligations']) + '</div></div>'
    content += section('Critères fixés', '<p>' + text(value['criteria']['result_expected']) + '</p>' + groups)
    content += section('Modèles et conditions', listing([item['model'] + ' · ' + item['revision'] for item in campaign['panel']]) +
        '<p>Pi : ' + text(campaign['conditions']['pi']['package']) + ' · ' + text(campaign['conditions']['pi']['version']) +
        '. Conditions figées le ' + text(campaign['conditions']['frozen_at']) + '.</p>' +
        '<details><summary>Configurations et conditions exactes</summary><pre>' + text(encode(dict(panel=campaign['panel'], conditions=campaign['conditions']))) + '</pre></details>')
    access = value.get('access', {'status': 'unavailable'})
    status = access.get('status')
    if status == 'connected':
        access_content = '<p>Compte Openrouter connecté. Crédit restant : ' + text(
            access.get('limit_remaining_usd') if access.get('limit_remaining_usd') is not None else 'INCONNU') + ' USD.</p>'
        access_content += form(csrf, '/preparation/access/disconnect', {},
                               '<button type="submit">Déconnecter</button>')
    elif status == 'invalid':
        access_content = '<p>Accès Openrouter invalide : ' + text(access.get('reason') or 'INCONNU') + '.</p>'
        access_content += form(csrf, '/preparation/access/start', {'return': base + '/conditions'},
                               '<button type="submit">Reconnecter mon compte Openrouter</button>')
    elif status == 'disconnected':
        access_content = '<p>Compte Openrouter non connecté.</p>'
        access_content += form(csrf, '/preparation/access/start', {'return': base + '/conditions'},
                               '<button type="submit">Connecter mon compte Openrouter</button>')
    else:
        access_content = '<p>Connexion Openrouter indisponible.</p>'
    content += section('Accès Openrouter', access_content)
    estimate = value['estimate']
    content += '<h2>Coût et autorisation</h2><p>Estimation indicative : ' + text(
        estimate['amount'] + ' ' + estimate['currency'] if estimate else 'non fournie par le responsable') + '.</p>'
    if estimate:
        content += '<p>' + text(estimate['assumptions']) + ' Source : ' + text(estimate['source']) + '.</p>'
    budget = campaign['budget']
    if budget:
        content += '<p>Enveloppe autorisée : ' + text(budget['limit']) + ' ' + text(budget['currency']) + '.</p>'
    content += '<p>Réserves prévues par essai : ' + text(', '.join(key + ' : ' + amount for key, amount in campaign['reserve_amounts'].items()) if campaign['reserve_amounts'] else 'non autorisées') + '.</p>'
    content += '<p>Estimation, réservation et coût observé sont distincts. La réserve ne garantit pas un plafond de facturation.</p>'
    if value['can_launch']:
        content += form(csrf, base + '/start', {'manifest_version': campaign['version'],
            'frozen_at': campaign['conditions']['frozen_at'], 'admission_id': value['admission_id']},
            '<label><input type="checkbox" name="confirm" value="yes" required> Je confirme le lancement des essais autorisés présentés.</label><button type="submit">Lancer la comparaison autorisée</button>')
    else:
        content += '<p role="status">' + ('Lancement enregistré. Consultez les essais et leurs résultats ci-dessous.' if campaign['attempts'] else 'Lancement indisponible. Le responsable doit vérifier les autorisations et la disponibilité de l’exécution.') + '</p>'
    content += section('Suivi des essais', listing([cell['cell_id'] + ' : ' + {'NOT_STARTED': 'non démarré', 'INTENT_RECORDED': 'en attente', 'EMISSION_POSSIBLE': 'en cours', 'RECEIVED': 'réponse reçue, consulter l’évaluation', 'AMBIGUOUS': 'état incertain, vérification requise'}.get(cell['state'], cell['state']) for cell in campaign['cells']]))
    content += '<p><a href="' + text(base + '/conditions') + '">Actualiser le suivi</a> · <a href="' + text(base) + '">Comparer les résultats et lire les preuves</a></p>'
    return content


PREVIEW_LENGTH = 200

CRITERION_STATES = {
    'obligation': {'PASS': ('b-ok', 'i-check', 'Respectée'), 'FAIL': ('b-ko', 'i-cross', 'Non respectée'),
                   'INDETERMINE': ('b-ind', 'i-help', 'Non vérifiable')},
    'eliminatory': {'PASS': ('b-ok', 'i-check', 'Non commise'), 'FAIL': ('b-ko', 'i-cross', 'Commise'),
                    'INDETERMINE': ('b-ind', 'i-help', 'Non vérifiable')},
}

ATTRIBUTIONS = {'reference': 'référence du juge'}


def _plural(count, label, *, number=True):
    """« 2 exigences », « 1 non respectée » : accord simple, « non » invariable"""
    words = ' '.join(word + ('s' if count > 1 and word != 'non' else '') for word in label.split(' '))
    return (str(count) + ' ' if number else '') + words


def _criterion_state(findings):
    """Tous les contrôles PASS ; sinon FAIL dès qu'un contrôle échoue ; sinon non conclu"""
    statuses = {finding['status'] for finding in findings}
    return 'PASS' if statuses == {'PASS'} else 'FAIL' if 'FAIL' in statuses else 'INDETERMINE'


def _proof_anchor(record, piece_id):
    link = next((p for p in record['proof_links'] if p['piece_id'] == piece_id), None)
    if link is None:
        return None, None
    if piece_id in record.get('proof_contents', {}):
        return link, '#proof-' + record['evaluation_id'] + '-' + piece_id
    return link, link['href']


def _criteria_list(record, criteria, kind):
    content = '<ul class="checks">'
    for criterion in criteria:
        findings = [f for f in record['findings'] if f['criterion_id'] == criterion['id']]
        tone, name, label = CRITERION_STATES[kind][_criterion_state(findings)]
        content += '<li><span class="badge ' + tone + '">' + icon(name) + label + '</span><span>' + text(criterion['description']) + '</span>'
        if findings:
            content += '<details><summary>Constats et extraits</summary><ul>'
            for finding in findings:
                content += '<li>' + text(finding['finding'])
                if finding['attribution'] not in ('candidate', 'evidence'):
                    content += ' <span class="hint">(attribué à : ' + text(ATTRIBUTIONS.get(finding['attribution'], finding['attribution'])) + ')</span>'
                for proof in finding['evidence']:
                    link, target = _proof_anchor(record, proof['piece_id'])
                    if link is None:
                        continue
                    content += '<details><summary>Passage de ' + text('la réponse du modèle' if link['piece_id'] == record['output_piece_id'] else link['name']) + '</summary><pre>' + text(proof['passage']) + '</pre>'
                    content += '<p><a href="' + text(target) + '">Ouvrir la pièce</a></p></details>'
                content += '</li>'
            content += '</ul></details>'
        content += '</li>'
    return content + '</ul>'


def render_result(record):
    """Lecture humaine compacte d'une évaluation : fragment partagé par la page directe et la modale.

    Le rendu technique complet reste `render_evaluations` sur les révisions du cas
    d'usage ; ce fragment n'en remplace pas le minimum accessible. Tout texte
    candidat, motif, constat ou mesure passe par `text()` ; aucune pièce n'est
    interprétée.
    """
    eid = record['evaluation_id']
    spec = record['qualification']['contract']['specification']
    configuration = record['requested_configuration']
    verdict = record['decision']['verdict']
    content = '<section class="result" id="evaluation-' + text(eid) + '"><p class="eyebrow">Résultat sur cet exemple</p>'
    content += '<h2>' + text(configuration['model']) + '</h2><p class="hint">' + text(effort_label(configuration)) + '</p>'
    cost = record['candidate_cost']
    content += '<p class="result-verdict">' + badge(verdict) + ' <span>Coût observé : ' + text(
        'inconnu' if cost is None or cost['status'] != 'KNOWN' else montant_lisible(cost['amount']) + ' ' + cost['currency']) + '</span></p>'
    if verdict is None and record['decision'].get('next_action'):
        content += '<p>' + text(record['decision']['next_action']) + '</p>'
    # Ce que le modèle a produit
    content += '<h3>Ce que le modèle a produit</h3>'
    output_id = record['output_piece_id']
    link, target = _proof_anchor(record, output_id) if output_id else (None, None)
    output = record.get('proof_contents', {}).get(output_id) if output_id else None
    if output is not None:
        preview = output[:PREVIEW_LENGTH].strip()
        content += '<p class="hint">Début de la réponse</p>'
        content += '<div class="proof-text preview">' + text(preview) + ('…' if len(output) > PREVIEW_LENGTH else '') + '</div>'
        content += '<details class="proof-content" id="proof-' + text(eid + '-' + output_id) + '"><summary>Lire la réponse complète</summary>'
        content += '<div class="proof-text">' + text(output) + '</div></details>'
    elif link is not None:
        content += '<p><a href="' + text(link['href']) + '">' + text(link['name']) + '</a></p>'
    else:
        content += '<p>Aucune sortie conservée pour cette tentative.</p>'
    # Pourquoi ce verdict
    content += '<h3>Pourquoi ce verdict</h3>'
    states = {criterion['id']: _criterion_state([f for f in record['findings'] if f['criterion_id'] == criterion['id']])
              for criterion in spec['obligations'] + spec['eliminatory_errors']}
    obligations = [states[c['id']] for c in spec['obligations']]
    eliminatory = [states[c['id']] for c in spec['eliminatory_errors']]
    summary = [_plural(obligations.count('PASS'), 'exigence') + ' sur ' + str(len(obligations)) + ' ' + _plural(obligations.count('PASS'), 'respectée', number=False)]
    if obligations.count('FAIL'):
        summary.append(_plural(obligations.count('FAIL'), 'non respectée'))
    if obligations.count('INDETERMINE'):
        summary.append(_plural(obligations.count('INDETERMINE'), 'non vérifiable'))
    content += '<p>' + ', '.join(summary) + '.'
    if eliminatory:
        content += (' Aucune erreur éliminatoire relevée.' if all(state == 'PASS' for state in eliminatory) else
                    ' ' + _plural(eliminatory.count('FAIL'), 'erreur éliminatoire relevée') + '.' if 'FAIL' in eliminatory else
                    ' Contrôle éliminatoire non concluant.')
    content += '</p>'
    reason = _readable_reason(record)
    content += ('<p class="hint">' + text(reason) + '</p>' if len(reason) <= PREVIEW_LENGTH else
                '<details><summary>Explication de l’évaluation</summary><p>' + text(reason) + '</p></details>')
    content += '<details><summary>Voir les exigences vérifiées</summary>' + _criteria_list(record, spec['obligations'], 'obligation') + '</details>'
    if spec['eliminatory_errors']:
        content += '<details><summary>Erreurs éliminatoires contrôlées</summary>' + _criteria_list(record, spec['eliminatory_errors'], 'eliminatory') + '</details>'
    if record['measures']:
        content += '<details><summary>Ce que le juge a observé</summary><p class="hint">Ces observations complètent le verdict ; elles ne forment pas une note globale.</p><ul>'
        for measure in record['measures']:
            content += '<li>' + text(measure['definition']['measure']) + ' : '
            if measure['status'] == 'UNKNOWN':
                content += 'non observée'
            else:
                content += readable_fields(measure['value']) + ('' if measure['unit'] in ('bool', 'boolean', 'booléen') else ' ' + text(measure['unit']))
            content += '</li>'
        content += '</ul></details>'
    # Réserves
    limits = list(dict.fromkeys(record['limits']))
    if limits:
        content += '<h3>Réserves à garder en tête</h3>' + listing(limits)
    # Pièces
    references = {piece['id'] for piece in record['qualification']['contract']['reference_pieces']}
    content += '<details><summary>Pièces de l’exemple</summary><ul>'
    for piece in record['proof_links']:
        if piece['piece_id'] == output_id:
            continue
        role = 'Référence utilisée par le juge' if piece['piece_id'] in references else 'Pièce fournie au modèle'
        content += '<li><span class="hint">' + role + '</span>'
        if piece['piece_id'] in record.get('proof_contents', {}):
            content += '<details class="proof-content" id="proof-' + text(eid + '-' + piece['piece_id']) + '"><summary>Lire la pièce complète : ' + text(piece['name']) + '</summary>'
            content += '<div class="proof-text">' + text(record['proof_contents'][piece['piece_id']]) + '</div></details>'
        else:
            content += ' <a href="' + text(piece['href']) + '">' + text(piece['name']) + '</a>'
        content += '</li>'
    return content + '</ul></details></section>'


def render_attempt_detail(value):
    """Private fragment loaded only by the result modal"""
    history = value['history']
    content = '<div id="attempt-detail">' + render_result(history[-1])
    for record in reversed(history[:-1]):
        content += '<details><summary>Évaluation précédente, remplacée (' + text(date_lisible_utc(record['created_at'])) + ')</summary>'
        content += render_result(record) + '</details>'
    return content + '</div>'


def render_campaign_records(campaigns, url):
    """Comparaisons enregistrées d'un cas d'usage, cellules et tentatives comprises"""
    content = '<p>Suivi privé des comparaisons fictives de ce cas d’usage. '
    content += 'L’acquisition conserve des reçus ; elle ne juge pas le contenu des sorties.</p>'
    technical = {'NOT_STARTED': 'Non lancée : aucune tentative', 'INTENT_RECORDED': 'Intention enregistrée',
                 'EMISSION_POSSIBLE': 'Appel actif ou émission possible, reçu en attente',
                 'AMBIGUOUS': 'Effets inconnus : reprise bloquée', 'RECEIVED': 'Reçu conservé'}
    for campaign in campaigns:
        task = campaign['task']
        content += '<article><h3>Campagne ' + text(campaign['campaign_id']) + '</h3>'
        if campaign.get('recovery_of'):
            content += '<p>Reprise technique de ' + text(campaign['recovery_of']) + '. Les reçus et coûts précédents restent conservés.</p>'
        if 'evaluations' in campaign:
            content += '<p><a href="' + text(url) + '/campaigns/' + text(campaign['campaign_id']) + '">Comparer les observations de cette campagne</a></p>'
        content += '<p>Version d’épreuve ' + text(task['version']) + ', révision ' + text(task['revision']) + '.</p>'
        content += '<h4>Configurations demandées</h4>' + listing(
            f'{c["id"]} : {c["model"]}, révision {c["revision"]}, fournisseur {c["provider"]}, '
            f'accès {c["access"]}, canal {c["channel_id"]}, route {c["route"]}, effort {c["effort"]}, '
            f'paramètres {encode(c["parameters"])} ; observations exigées : {", ".join(c["required_observations"])}'
            for c in campaign['panel'])
        conditions = campaign['conditions']
        pi = conditions['pi']
        content += '<h4>Conditions Pi communes</h4><p>' + text(
            f'{pi["package"]} {pi["version"]} ; état {pi["status"]} ; gel {conditions["frozen_at"]}') + '</p>'
        content += '<details><summary>Contexte et environnement communs</summary>' + listing(
            f'{k} : {encode(conditions[k])}' for k in ('packages', 'tools', 'skills', 'defaults', 'environment')) + '</details>'
        content += '<h4>Autorités et budget</h4><p>' + (
            'Admission opérateur ouverte pour les cellules : ' + text(', '.join(campaign['allowed_cells'])) if campaign['admission_open'] else
            'Admission fermée. Autorités à fournir ou renouveler par l’opérateur : ' + text(', '.join(campaign['missing_authorities']))) + '.</p>'
        if campaign['restore_pending']:
            content += '<p>Restauration à rapprocher : toute nouvelle admission reste bloquée.</p>'
        if campaign['stop_reason']:
            content += '<p>Motif d’arrêt : ' + text(campaign['stop_reason']) + '.</p>'
        budget = campaign['budget']
        if budget:
            content += '<p>' + text(
                f'Sous-total des coûts connus : {budget["spent"]} {budget["currency"]}. '
                f'Réservations conservées : {budget["reserved"]}.') + '</p>'
            if not budget.get('provider_managed'):
                content += '<p>' + text(f'Enveloppe {budget["budget_id"]} : {budget["limit"]} {budget["currency"]}. '
                    f'Solde disponible : {budget["available"] if budget["balance_status"] == "KNOWN" else "INCONNU"}.') + '</p>'
        else:
            content += '<p>Budget prévu : INCONNU, enveloppe à désigner par l’opérateur.</p>'
        content += '<p>Prévisions de réserve par cellule : ' + text(
            encode(campaign['reserve_amounts']) if campaign['reserve_amounts'] else 'INCONNU, autorité attendue') + '.</p>'
        content += '<p>Base de coût : ' + text(encode(campaign['cost_basis'])) + '.</p>'
        content += '<p>La réservation ne prouve pas un plafond de facturation. Préparation et jugement conservent leurs opérations propres.</p>'
        content += '<h4>Cellules prévues</h4>' + listing(
            f'{c["cell_id"]} — cas {c["case_id"]}, configuration {c["configuration_id"]} : {technical[c["state"]]}'
            for c in campaign['cells'])
        for attempt in campaign['attempts']:
            content += '<details><summary>Tentative ' + text(attempt['operation_id']) + ' — ' + text(technical[attempt['state']]) + '</summary>'
            content += '<p>Exécution ' + text(attempt['execution_id']) + ', cellule ' + text(attempt['cell_id']) + '.</p>'
            content += '<p>Intention : ' + text(attempt['created_at']) + '. Émission possible : ' + text(attempt['emitted_at'] or 'Non lancée')
            content += '. Réception : ' + text(attempt['received_at'] or 'INCONNU') + '.</p>'
            content += '<p>Reçu : ' + text(attempt['receipt_id'] or 'Absent') + '. Preuve d’émission : ' + text(attempt['emission']) + '.</p>'
            content += '<p>Configuration observée : ' + text(encode(attempt['observed_configuration'])) + '.</p>'
            content += '<p>Sources des observations : ' + text(encode(attempt['observation_sources'])) + '.</p>'
            cost = attempt['observed_cost']
            content += '<p>Coût observé : ' + text('INCONNU' if cost is None or cost['status'] == 'UNKNOWN' else cost['amount'] + ' ' + cost['currency'])
            content += '. Source : ' + text(cost['source'] if cost else 'INCONNU') + '.</p>'
            if attempt['incident']:
                content += '<p>Incident technique : ' + text(attempt['incident']) + '.</p>'
            if attempt['attribution_incident']:
                content += '<p>Attribution non prouvée : ' + text(', '.join(attempt['attribution_incident'])) + '.</p>'
            evaluations = [e for e in campaign.get('evaluations', []) if e['attempt_id'] == attempt['operation_id']]
            if evaluations:
                content += render_evaluations(evaluations, url)
            else:
                content += '<p>Aucune évaluation conservée. Sortie brute réservée à l’inspection opérateur.</p>'
            content += '</details>'
        content += '</article>'
    if not campaigns:
        content += '<p>Aucune campagne liée à ce dossier.</p>'
    return '<details><summary>Comparaisons enregistrées et détails de ce cas d’usage</summary>' + content + '</details>'
