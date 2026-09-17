"""Rendu HTML des campagnes : configurations, lancement, comparaison, preuves.

Ce module met en forme tout ce qui porte sur une campagne d'un cas d'usage :
le choix des configurations, les deux formes de lancement (demandeur et
opérateur), la comparaison et ses filtres, le détail d'une tentative, les
évaluations et l'historique des campagnes.

Il ne possède ni le gabarit de page, ni les erreurs, ni l'accès fournisseur, ni
la préparation : ces domaines restent dans `views`. Il n'importe pas `views`,
pour qu'aucun cycle ne soit possible.
"""
import re
import secrets
from urllib.parse import urlencode

from benchmark.storage import _strict_json as encode

from .fragments import (badge, date_lisible_utc, form, hidden, icon, listing, montant_lisible,
                        readable_fields, section, text)

COMPARISON_FOCUS_SCRIPT = """document.addEventListener('click', event => {
  const link = event.target.closest('tr[id] a[href]');
  if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  history.replaceState({...history.state, comparisonFocus: link.closest('tr').id}, '');
});
window.addEventListener('pageshow', () => {
  const row = document.getElementById(history.state?.comparisonFocus);
  if (row) row.focus({preventScroll: true});
});"""

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


def render_evaluations(evaluations, dossier_url):
    """Inert evidence and correction history inside the owner's existing page"""
    content = '<h4>Verdicts et preuves</h4><p>Évaluations fictives, par cas et tentative. '
    content += 'Le verdict porte sur la configuration observée sous les conditions communes ; '
    content += 'il ne prouve ni une propriété du modèle seul ni une compétence métier générale.</p>'
    for record in evaluations:
        eid = record['evaluation_id']
        spec = record['qualification']['contract']['specification']
        labels = {item['id']: item['description'] for item in spec['obligations'] + spec['eliminatory_errors']}
        reason = re.sub(r'(?<!\w)(' + '|'.join(map(re.escape, labels)) + r')(?!\w)',
                        lambda match: labels[match[0]], record['reason'])
        label = ('Évaluation à reprendre (valeur historique : INDETERMINE)' if record['verdict'] == 'INDETERMINE'
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


def render_comparison(value):
    def data(label, value):
        return '<details><summary>' + text(label) + '</summary>' + readable_fields(value) + '</details>'

    def metric(value):
        source = 'INCONNU' if value['value'] is None else value['value']
        content = '<span class="source-value">' + readable_fields(source)
        content += ('' if value['unit'] in ('bool', 'boolean', 'booléen') else ' ' + text(value['unit'])) + '</span>'
        if value['rank'] is None:
            return content + '<p>Sans rang : ' + text(value['reason']) + '</p>'
        return content + '<p>Rang ' + text(value['rank']) + '</p>'

    base, query = value['href'], value['filter_scope']
    content = '<nav aria-label="Parcours"><a href="/preparation">Mes cas d’usage</a> · '
    content += '<a class="button" href="' + text(value['dossier_href']) + '">Revenir au cas d’usage</a></nav>'
    content += '<p class="hint">Résultats privés · version d’épreuve ' + text(value['task']['version'])
    content += '.</p><details><summary>Identité de la campagne</summary><p>' + text(value['campaign_id']) + '</p></details>'
    content += '<p class="lead">' + text(value['result_expected']) + '</p>'
    content += '<div class="campaign-summary" aria-label="Conclusion de la campagne"><span class="ic">' + icon('i-scale') + '</span>'
    content += '<p class="eyebrow">Le verdict ne fait pas de moyenne</p>'
    latest = {record['attempt_id']: record for record in value['history']}
    # Décompter les verdicts conservés par cas, sans créer de verdict agrégé
    for case_number, case in enumerate(value['cases'], 1):
        records = [record for record in latest.values() if record['case_id'] == case['id']]
        if records:
            counts = [str(sum(record['decision']['verdict'] == verdict for record in records)) + ' ' + label
                      for verdict, label in (('SATISFAIT', 'satisfait(s)'), ('NE SATISFAIT PAS', 'non satisfait(s)'),
                                             (None, 'évaluation(s) à reprendre'))
                      if any(record['decision']['verdict'] == verdict for record in records)]
            content += '<p><strong>Cas ' + text(case_number) + '</strong> : ' + text(' · '.join(counts)) + '.</p>'
    if not latest:
        content += '<p>Aucun résultat évalué pour cette campagne.</p>'
    coverage = value['coverage']
    content += '<p role="status">' + text(coverage['evaluated_attempts']) + ' tentative(s) évaluée(s) · '
    content += text(coverage['attempted_cells']) + ' essai(s) lancé(s) sur ' + text(coverage['planned_cells'])
    content += ' prévu(s) · ' + text(coverage['not_started']) + ' non lancé(s). '
    content += ('Comparaison des coûts complète.' if value['economic_status'] == 'COMPLETE' else 'Comparaison des coûts incomplète.') + '</p>'
    dates = sorted(set(date[:10] for date in value.get('acquisition_dates', [])))
    if dates:
        content += '<p class="hint">Réponses reçues : ' + text(dates[0] if len(dates) == 1 else dates[0] + ' au ' + dates[-1]) + '.</p>'
    content += '<p class="hint">Verdicts par cas et tentative, sans conclusion globale. '
    content += 'Un coût inconnu ne change pas le verdict. <a href="#method">Méthode et limites</a></p></div>'
    content += '<details id="filters" class="comparison-filters"><summary>Tris et filtres'
    content += (' · ' + text(len(query)) + ' sélection(s) active(s)' if query else '') + '</summary>'
    content += '<p id="filter-help">Chaque bouton applique le champ choisi et conserve les autres sélections. '
    content += 'Les filtres changent seulement les lignes visibles. Les rangs et la couverture gardent la population entière.</p>'
    sort_label = next((('Coût observé' if 'criterion_id' not in column else column['definition']['measure'])
                       for column in value['columns'] if column['id'] == query.get('sort')), 'descriptif, sans préférence')
    content += '<p>Ordre actuel : ' + text(sort_label)
    content += (', ' + ('décroissant' if query.get('direction') == 'desc' else 'croissant') if 'sort' in query else '') + '.</p>'
    options = {
        'case': ('Cas', [(v['id'], 'Cas ' + str(number))
                         for number, v in enumerate(value['cases'], 1)]),
        'sort': ('Critère de tri', [(v['id'], 'Coût observé' if 'criterion_id' not in v else v['definition']['measure']) for v in value['columns']]),
        'direction': ('Ordre d’affichage', [('asc', 'Croissant'), ('desc', 'Décroissant')]),
        'verdict': ('Décision ou travail restant', [('SATISFAIT', 'SATISFAIT'), ('NE SATISFAIT PAS', 'NE SATISFAIT PAS'), ('A_REPRENDRE', 'À reprendre')]),
        'obligation': ('Constat par obligation', [(v['id'] + ':' + state, v['description'] + ' : ' + label)
                        for v in value['obligations'] for state, label in
                        (('PASS', 'Respectée'), ('FAIL', 'Non respectée'), ('INDETERMINE', 'Indéterminée'))]),
        'configuration': ('Configuration', [(v['id'], v['model'] + ' · ' + v['id']) for v in value['panel']]),
    }
    if query:
        content += '<ul aria-label="Sélections actives">'
        for key, val in query.items():
            remaining = {k: v for k, v in query.items() if k != key}
            target = base + ('?' + urlencode(remaining) if remaining else '') + '#filters'
            label = next((label for option, label in options[key][1] if option == val), val)
            content += '<li>' + text(options[key][0] + ' : ' + label) + ' · <a href="' + text(target)
            content += '">Enlever ' + text(options[key][0].lower()) + '</a></li>'
        content += '</ul>'
    else:
        content += '<p>Aucun filtre ni tri appliqué.</p>'
    content += '<div class="filter-grid">'
    for key, (label, values) in options.items():
        if not values:
            continue
        content += '<form method="get" action="' + text(base) + '" aria-describedby="filter-help">'
        for k, v in query.items():
            if k != key:
                content += '<input type="hidden" name="' + text(k) + '" value="' + text(v) + '">'
        content += '<label for="filter-' + key + '">' + label + '</label><select id="filter-' + key + '" name="' + key + '">'
        for val, title in values:
            content += '<option value="' + text(val) + '"' + (' selected' if query.get(key) == val else '') + '>' + text(title) + '</option>'
        content += '</select><button type="submit">Appliquer : ' + label.lower() + '</button></form>'
    content += '</div><p><a href="' + text(base) + '#filters">Enlever tous les filtres et le tri</a></p></details>'
    content += '<p class="view-scope">' + text(len(value['rows'])) + ' ligne(s) affichée(s) sur '
    content += text(len(value['population'])) + ' tentatives évaluées · ordre ' + text(sort_label)
    content += (', décroissant' if query.get('direction') == 'desc' else ', croissant') if 'sort' in query else ''
    content += '. Les filtres ne changent pas le bilan de campagne.</p>'
    if not value['rows']:
        content += '<p role="status">' + ('Aucune ligne ne correspond aux filtres ; les observations de la campagne restent conservées.'
                     if value['population'] else 'Aucune tentative évaluée dans cette campagne.') + '</p>'
    for case_number, case in enumerate(value['cases'], 1):
        rows = [r for r in value['rows'] if r['case_id'] == case['id']]
        if not rows:
            continue
        content += '<section class="comparison-results"><h2>Cas ' + text(case_number) + '</h2>'
        content += '<p class="table-hint">Sur petit écran, faites défiler le tableau horizontalement pour lire coûts et preuves.</p>'
        content += '<div class="table-scroll" role="region" tabindex="0" aria-label="Observations du cas ' + text(case_number) + '">'
        content += '<table><caption>Cas ' + text(case_number) + ' · valeurs par tentative, sans agrégation</caption><thead><tr>'
        for title in ('Configuration et tentative', 'Verdict et motif', 'Coût observé', 'Mesures prévues', 'Preuves'):
            content += '<th scope="col">' + title + '</th>'
        content += '</tr></thead><tbody>'
        known = [float(r['cost']['value']) for r in rows if r['cost']['value'] is not None and _numeric(r['cost']['value'])]
        for row in rows:
            content += '<tr id="attempt-' + text(row['attempt_id']) + '"' + ('' if row['verdict'] == 'SATISFAIT' else ' class="out"') + ' tabindex="-1"><th scope="row">'
            content += '<strong>' + text(row['requested_configuration']['model']) + '</strong>'
            content += data('Demandée, observée et sources', {k: row[k] for k in ('requested_configuration', 'observed_configuration', 'observation_sources')}) + '</th>'
            spec = row['qualification']['contract']['specification']
            labels = {item['id']: item['description'] for item in spec['obligations'] + spec['eliminatory_errors']}
            reason = re.sub(r'(?<!\w)(' + '|'.join(map(re.escape, labels)) + r')(?!\w)',
                            lambda match: labels[match[0]], row['reason'])
            content += '<td>' + badge(row['verdict']) + '<p>' + text(reason) + '</p>'
            if row.get('decision', {}).get('next_action'):
                content += '<p>' + text(row['decision']['next_action']) + '</p>'
            if row['incident']:
                content += '<p>Incident : ' + text(row['incident']) + '</p>'
            content += '</td><td>' + metric(row['cost']) + cost_bar(row['cost']['value'], known) + '</td><td>'
            for measure in row['measures']:
                content += '<p>' + text(measure['definition']['measure']) + '</p>' + metric(measure)
            content += '</td><td><a href="' + text(row['detail_href']) + '">Détail et preuves</a></td></tr>'
        content += '</tbody></table></div></section>'
    content += '<details id="method"><summary>Méthode, critères et limites</summary>'
    content += '<p>' + text(value['conclusion']['attribution']) + '</p><p>' + text('; '.join(value['conclusion']['limits'])) + '</p>'
    content += '<p>Travail humain restant : ' + text(value['human_work']) + '</p>'
    content += '<p>Les rangs comparent seulement les valeurs connues d’un même cas. Les égalités sont conservées ; '
    content += 'les valeurs inconnues ou incompatibles restent sans rang. Aucun choix automatique ni total multi-cas.</p>'
    for pending in value.get('pending_attempts', []):
        content += '<p>Tentative ' + text(pending['attempt_id']) + ' : ' + text(pending['next_action']) + '</p>'
    content += '<p>' + badge('SATISFAIT') + ' obligations prouvées. ' + badge('NE SATISFAIT PAS') + ' défaut établi. ' + badge(None) + ' pas encore de verdict métier.</p>'
    for column in value['columns']:
        content += data('Définition, unité, sens favorable et preuve : ' + column['id'], column)
    content += data('Population entière utilisée pour les rangs, conservée après filtrage', value['population'])
    content += data('Cellules prévues et couverture manquante', value['cells'])
    content += data('Conditions communes et date de gel', value['conditions'])
    content += data('Dates de réception', value.get('acquisition_dates', []))
    content += data('Contrat et portée exacte de la conclusion', value['conclusion']['scope'])
    content += data('Base de coût et conversion prévue', value['cost_basis'])
    if value.get('stop_reason'):
        content += '<p>Motif d’arrêt enregistré : ' + text(value['stop_reason']) + '</p>'
    content += '<p><a href="' + text(base + '/preview') + '">Examiner un aperçu privé de la projection</a></p></details>'
    return content


def render_configurations(value, csrf):
    """Choix des modèles et du palier, puis estimation de la sélection courante"""
    dossier_url = '/preparation/dossiers/' + value['dossier_id']
    content = '<p><a href="' + text(dossier_url) + '">Revenir au cas d’usage</a></p>'
    content += '<p role="status">Choisissez au moins deux modèles et un palier de raisonnement. Aucun appel candidat ne part à cette étape.</p>'
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
        tiers = ''.join(
            '<label><input type="radio" form="configurations-form" name="tier" aria-describedby="tier-help-' + tier + '" value="' + tier + '"' +
            (' checked' if value['current_tier'] == tier else '') + '> ' +
            ('Standard' if tier == 'standard' else 'Renforcé') + '</label>' +
            '<p class="hint" id="tier-help-' + tier + '">' +
            ('Le modèle utilise ses réglages habituels, sans demande de raisonnement renforcé.'
             if tier == 'standard' else
             'Demande un raisonnement plus approfondi, lorsque le modèle le permet. '
             'Cela peut allonger l’attente et augmenter le coût, sans garantir une meilleure réponse. '
             'Sans effet sur les modèles indiqués comme non réglables.') + '</p>'
            for tier in value['available_tiers'])
        content += ('<form id="configurations-form" method="post" action="' + text(dossier_url + '/configurations') + '">' +
                    hidden('csrf_token', csrf) + '<fieldset id="model-choices"><legend>Modèles à comparer</legend>' +
                    choices + '</fieldset></form>')
        if value.get('personal_preparation'):
            content += render_custom_models(value, csrf, dossier_url)
        content += ('<fieldset><legend>Palier de raisonnement</legend>' + tiers +
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
            if configuration.get('effort_limit') == 'not_adjustable':
                detail += ' · palier de raisonnement non réglable'
            summary += '<li>' + text(model_names.get(technical, technical)) + text(detail) + (
                '<details><summary>Identifiant technique</summary><code>' +
                text(technical) + '</code></details></li>')
        summary += '</ul>'
        summary += '<p>Estimation totale : ' + text(
            'non estimable' if value['estimate_total_usd'] is None else
            montant_lisible(value['estimate_total_usd']) + ' USD') + '.</p>'
        summary += '<p>Plafond : ' + text(montant_lisible(value['cap_usd'])) + ' USD.</p>'
        summary += '<p><a class="button" href="' + text(
            dossier_url + '/campaigns/' + value['current_campaign_id'] +
            '/conditions') + '">Voir le récapitulatif</a></p>'
        content += section('Sélection courante', summary)
    return content


def render_campaign_launch_requester(value, csrf):
    """Contrôles, plafond et suivi présentés au demandeur qui lance lui-même"""
    campaign = value['campaign']
    dossier_url = '/preparation/dossiers/' + value['dossier_id']
    base = dossier_url + '/campaigns/' + campaign['campaign_id']
    content = '<p><a href="' + text(dossier_url) + '">Revenir au cas d’usage</a></p>'
    content += section('Ce qui sera testé', '<p>' + text(value['criteria']['result_expected']) + '</p>' +
        listing(item['model'] for item in campaign['panel']) +
        '<p>Chaque modèle reçoit la même consigne et les mêmes pièces. Le verdict reste limité à cet exemple et aux configurations observées.</p>' +
        '<details><summary>Critères et conditions exactes</summary>' + readable_fields(
            {'criteria': value['criteria'], 'conditions': campaign['conditions'], 'panel': campaign['panel']}) + '</details>')
    content += '<p>Les appels candidats sont financés par votre accès Openrouter. Estimation, plafond et coût observé sont distincts ; le plafond ne garantit pas une limite absolue de facturation.</p>'
    check_content = '<ul>'
    for check in value['checks']:
        detail = check['detail']
        if type(detail) is dict:
            detail = ('Crédit restant : ' + str(detail.get('limit_remaining_usd')
                      if detail.get('limit_remaining_usd') is not None else 'INCONNU') +
                      ' USD ; limite du compte : ' + str(detail.get('limit_usd')
                      if detail.get('limit_usd') is not None else 'INCONNU') + ' USD')
        check_content += '<li>' + text(('✓ ' if check['ok'] else '✕ ') + str(detail))
        if check['key'] == 'example_qualified' and check.get('findings'):
            check_content += '<p>Constats de qualification</p>' + listing(
                finding['text'] for finding in check['findings'])
        check_content += '</li>'
    check_content += '</ul>'
    content += section('Contrôles avant lancement', check_content)
    content += '<p>Plafond actuel : ' + text(montant_lisible(value['cap_usd'])) + ' USD.</p>'
    content += ('<p>L’arrêt intervient après le paiement de l’appel en cours. '
                'La dépense peut donc dépasser le plafond du montant du dernier appel.</p>')
    if not campaign['attempts']:
        content += section('Modifier le plafond',
            '<form method="post" action="' + text(base + '/cap') + '">' + hidden('csrf_token', csrf) +
            '<label for="cap_usd">Plafond en USD, de 0,10 à 100</label>' +
            '<input id="cap_usd" name="cap_usd" type="number" min="0.10" max="100.00" step="0.01" value="' +
            text(value['cap_usd']) + '" required><button class="sec" type="submit">Modifier le plafond</button></form>')
    failed = next((check for check in value['checks'] if not check['ok']), None)
    if campaign['attempts']:
        received = all(cell['state'] == 'RECEIVED' for cell in campaign['cells'])
        content += '<p role="status">Lancement enregistré. ' + (
            'Toutes les réponses sont reçues ; consultez les évaluations disponibles.' if received else
            'Les essais sont en attente ou en cours ; actualisez pour suivre leur avancement.') + '</p>'
        states = {'NOT_STARTED': 'non démarré', 'INTENT_RECORDED': 'en attente',
                  'EMISSION_POSSIBLE': 'en cours', 'RECEIVED': 'réponse reçue',
                  'AMBIGUOUS': 'état incertain, vérification requise'}
        models = {item['id']: item['model'] for item in campaign['panel']}
        content += section('Suivi des essais', listing(
            models[cell['configuration_id']] + ' : ' + states[cell['state']] for cell in campaign['cells']))
        if not campaign['admission_open']:
            content += '<p>Les nouveaux appels sont fermés. Les réponses reçues restent consultables.</p>'
        content += '<p><a class="button' + (' sec' if received else '') + '" href="' + text(base + '/conditions') + '">Actualiser le suivi</a> '
        content += '<a class="button' + ('' if received else ' sec') + '" href="' + text(base) + '">Comparer les résultats et lire les preuves</a></p>'
    elif value['launchable']:
        content += '<p role="status">Les contrôles sont satisfaits. Vérifiez le travail, les modèles et le plafond avant de confirmer le lancement.</p>'
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
            'estimate_under_cap': dossier_url + '/configurations',
        }
        content += '<p role="status">Lancement indisponible : ' + text(
            failed['detail'] if type(failed['detail']) is str else
            'connectez votre accès Openrouter') + '. <a class="button" href="' + text(
            links[failed['key']]) + '">Compléter cette étape</a></p>'
    else:
        content += '<p role="status">Lancement indisponible. Le responsable doit vérifier la disponibilité de l’exécution.</p>'
        content += '<p><a class="button" href="' + text(dossier_url) + '">Revenir au cas d’usage</a></p>'
    return content


def render_campaign_launch_operator(value, csrf):
    """Comparaison préparée et admise par l'opérateur, confirmée par le demandeur"""
    campaign = value['campaign']
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


def render_attempt_detail(value):
    """Preuves d'une tentative, dernière évaluation en premier"""
    content = '<nav aria-label="Retour"><a class="button" href="' + text(value['back_href']) + '">Revenir à la comparaison avec ses filtres</a> · '
    content += '<a href="/preparation">Mes cas d’usage</a></nav><p class="lead">' + text(value['need']) + '</p>'
    content += '<p>Consultation privée · version d’épreuve ' + text(value['task']['version']) + '.</p>'
    content += '<details><summary>Identité de la campagne</summary><p>' + text(value['campaign_id']) + '</p></details>'
    content += '<p>Les pièces exactes et leurs passages restent inertes. Historique conservé ; la dernière évaluation est affichée en premier.</p>'
    content += render_evaluations(list(reversed(value['history'])), value['back_href'])
    return content


def render_campaign_history(campaigns, url):
    """Historique replié des campagnes d'un cas d'usage, cellules et tentatives comprises"""
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
            content += '<p>' + text(f'Enveloppe {budget["budget_id"]} : {budget["limit"]} {budget["currency"]}. '
                f'Sous-total des coûts connus : {budget["spent"]}. Réservations conservées : {budget["reserved"]}. '
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
    return '<details><summary>Historique et détails des comparaisons de ce cas d’usage</summary>' + content + '</details>'
