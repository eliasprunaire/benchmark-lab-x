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
from benchmark.preparation import NOT_SENT_TEXT
from benchmark.storage import AMBIGUOUS_EXPIRED, AMBIGUOUS_EXPIRED_TEXT
from benchmark.evaluation import criterion_state, states

from .fragments import (VERDICT_BADGES, valeur_mesure, access_summary, badge, date_lisible_utc, form, hidden, icon, jour_lisible, listing, montant_lisible,
                        personal_key_form, readable_fields, section, state_block, text)

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
  // Avec la modale, le lien ouvre un dialogue : il s'annonce et s'active comme un bouton, Espace compris
  for (const link of document.querySelectorAll('a[data-result]')) link.setAttribute('role', 'button');
  // Comme un bouton natif : Espace ne fait pas défiler et active au relâchement, sinon le relâchement ferme la modale
  for (const type of ['keydown', 'keyup']) document.addEventListener(type, event => {
    const link = event.target.closest?.('a[data-result]');
    if (!link || event.key !== ' ') return;
    event.preventDefault();
    if (type === 'keyup') link.click();
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('[data-result]');
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    opener = link;
    if (!dialog.open) dialog.showModal();
    load(link.getAttribute('href'));
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
    // L'événement arrive après coup : rouverte entre-temps, la modale garde son chargement en cours
    if (dialog.open) return;
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
        label.append(input, ' ' + model.name + (model.not_adjustable ? ' · niveau de raisonnement fixe' : ''));
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
      } else if (request?.pending) target.textContent = 'Vérification du modèle et de votre accès…';
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
    status(target, 'Vérification du modèle et de votre accès…');
    try {
      const response = await fetch(form.action, {method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded', Accept: 'application/json'},
        body: new URLSearchParams(new FormData(form)), redirect: 'error'});
      const value = await response.json();
      if (!response.ok) status(target, value.error || 'La vérification n’a pas abouti.', true);
      else await follow(value.probe_operation_id, target, value);
      form.querySelector('input[name=action_id]').value = crypto.randomUUID();
    } catch {
      status(target, 'Nous ne recevons plus l’avancement de la vérification. Actualisez la page pour voir où elle en est. Aucun appel ne sera relancé.', true);
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
    content = '<details id="custom-models" class="corr custom-models"><summary class="button sec">Ajouter un modèle absent de la liste</summary><div>'
    content += '<p id="slug-help">Copiez l’identifiant exact du modèle depuis sa page OpenRouter, par exemple <code>openai/gpt-6-astra</code>.</p>'
    content += '<p class="hint">Pour vérifier que le modèle répond, nous l’appelons une fois, brièvement, avec votre clé. Cet appel est payant. Il ne lance pas la comparaison.</p>'
    request = value.get('probe_request', {})
    if request.get('error'):
        content += '<p class="note">' + text(request['error']) + '</p>'
    elif request.get('pending') and not any(record['operation_id'] == request['request_id']
                                          for record in value.get('custom_models', [])):
        content += '<p role="status" data-probe-request="' + text(request['request_id']) + '">Vérification du modèle et de votre accès…</p>'
    for record in value.get('custom_models', []):
        pending = (' role="status" data-probe-request="' + text(record['operation_id']) + '"' if record['status'] == 'EMISSION_POSSIBLE' else '')
        content += '<p><code>' + text(record['slug']) + '</code> : <span' + pending + '>' + text(record['detail'])
        cost = record['cost']
        if cost:
            content += (' Coût de cette vérification : ' + text(montant_lisible(cost['amount'])) + ' USD.' if cost['status'] == 'KNOWN'
                        else ' Coût de cette vérification inconnu. Le montant mis de côté pour cet appel, ' + text(montant_lisible(record['reserve_usd'])) + ' USD, reste conservé.')
        content += '</span></p>'
        if record['status'] == 'EMISSION_POSSIBLE':
            content += '<p><progress aria-label="Vérification du modèle"></progress> <a href="' + text(
                dossier_url + '/configurations#custom-models') + '">Voir où en est la vérification</a></p>'
    content += '<div class="custom-model-forms">' + form(csrf, dossier_url + '/custom-models',
        {'action_id': secrets.token_hex(16)},
        '<label for="custom-slug">Identifiant du modèle sur OpenRouter</label>'
        '<input id="custom-slug" name="slug" type="text" required maxlength="256" spellcheck="false" '
        'autocapitalize="none" autocomplete="off" aria-describedby="slug-help" placeholder="constructeur/modèle">'
        '<button class="sec" type="submit">Tester et ajouter</button><p role="status" aria-live="polite"></p>')
    content += '</div><button class="sec" type="button" data-add-slug hidden>Ajouter un autre modèle</button>'
    return content + '</div></details><script>' + CUSTOM_MODELS_SCRIPT + '</script>'


def _readable(record, value):
    """Texte du juge avec les identifiants de critères et d'éléments remplacés par leurs descriptions"""
    spec = record['qualification']['contract']['specification']
    labels = {item['id']: item['description'] for criterion in spec['obligations'] + spec['eliminatory_errors']
              for item in [criterion] + criterion.get('elements', [])}
    if not labels:
        return value
    return re.sub(r'(?<!\w)(' + '|'.join(map(re.escape, labels)) + r')(?!\w)', lambda match: labels[match[0]], value)


def _readable_reason(record):
    return _readable(record, record['reason'])


def _failure_reason(record):
    """Motif court d'un NE SATISFAIT PAS : les critères en défaut, entiers, éliminatoires d'abord

    Une obligation composée nomme entre parenthèses ses éléments en défaut
    """
    spec = record['qualification']['contract']['specification']
    failed = {key for key, state in _states(record).items() if state == 'FAIL'}

    def label(c):
        elements = [e['description'] for e in c.get('elements', []) if (c['id'], e['id']) in failed]
        return c['description'] + (' (' + ' ; '.join(elements) + ')' if elements else '')
    parts = []
    for criteria, one, many in ((spec['eliminatory_errors'], 'Erreur éliminatoire', 'Erreurs éliminatoires'),
                                (spec['obligations'], 'Exigence non respectée', 'Exigences non respectées')):
        named = [label(c) for c in criteria if any(cid == c['id'] for cid, _ in failed)]
        if named:
            parts.append((many if len(named) > 1 else one) + ' : ' + ' ; '.join(named) + '.')
    return ' '.join(parts) or _readable_reason(record)


def render_evaluations(evaluations, dossier_url):
    """Inert evidence and recorded corrections inside the owner's existing page.

    Rendu complet des évaluations enregistrées ; `render_result` fournit la lecture compacte
    """
    content = '<h4>Verdicts et preuves</h4><p>Évaluations d’un exemple inventé, réponse par réponse. '
    content += 'Chaque verdict vaut pour le modèle tel qu’il a été réglé ici, dans les mêmes conditions que les autres. '
    content += 'Il ne dit rien du modèle en général, ni de sa compétence dans votre métier au-delà de cet exemple.</p>'
    for record in evaluations:
        eid = record['evaluation_id']
        reason = _readable_reason(record)
        label = ('À reprendre : la preuve ne permet pas de conclure' if record['verdict'] == 'INDETERMINE'
                 else VERDICT_BADGES[record['verdict']][2] if record['verdict'] in VERDICT_BADGES
                 else record['verdict'] or 'Évaluation à reprendre')
        content += '<section id="evaluation-' + text(eid) + '"><h5>' + text(label) + '</h5>'
        content += '<p class="note">' + text(reason) + '</p><details><summary>Identifiants de cette évaluation</summary><p>Cas ' + text(record['case_id'])
        content += ', modèle ' + text(record['configuration_id']) + ', évaluation ' + text(eid) + '.</p></details>'
        content += '<p>Évaluation réalisée par ' + text(record['responsible']) + ' le ' + text(record['created_at']) + '.</p>'
        previous = record['previous_evaluation_id']
        if previous:
            content += '<details><summary>Évaluation précédente</summary><p>Cette évaluation corrige <a href="#evaluation-' + text(previous) + '">' + text(previous) + '</a>.</p></details>'
        content += '<ul>'
        spec = record['qualification']['contract']['specification']
        for finding in record['findings']:
            content += '<li>' + text(_readable(record, finding['finding']))
            # Le critère et, pour une obligation composée, son élément ; un identifiant de contrôle reste interne
            criterion = next(c for c in spec['obligations'] + spec['eliminatory_errors'] if c['id'] == finding['criterion_id'])
            element = next((e['description'] for e in criterion.get('elements', []) if e['id'] == finding['control_id']), None)
            content += '<details><summary>Détail technique du contrôle</summary><p>' + text(criterion['description'] + (' : ' + element if element else ''))
            content += ' : ' + text(finding['status']) + ', attribution ' + text(finding['attribution'])
            content += '</p></details>'
            for proof in finding['evidence']:
                link = next(p for p in record['proof_links'] if p['piece_id'] == proof['piece_id'])
                content += '<details><summary>Extrait de ' + text(link['name']) + '</summary>'
                content += '<pre>' + text(proof['passage']) + '</pre>'
                target = ('#proof-' + eid + '-' + link['piece_id']
                          if link['piece_id'] in record.get('proof_contents', {}) else link['href'])
                content += '<p><a href="' + text(target) + '">Ouvrir la pièce</a></p></details>'
            content += '</li>'
        content += '</ul><p>Pièces utilisées pour cette évaluation :</p><ul>'
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
        content += '</ul><h6>Observations prévues</h6><ul>'
        for measure in record['measures']:
            content += '<li>' + text(measure['definition']['measure']) + ' : '
            content += readable_fields('INCONNU' if measure['status'] == 'UNKNOWN' else measure['value'])
            content += ('' if measure['unit'] in ('bool', 'boolean', 'booléen') else ' ' + text(measure['unit']))
            content += '<details><summary>Méthode de mesure</summary>' + readable_fields(measure) + '</details></li>'
        content += '</ul><p>Ce résultat n’est pas combiné avec d’autres cas ou d’autres essais.</p>'
        for label, cost in (('Coût de la réponse du modèle', record['candidate_cost']), ('Coût de l’évaluation', record['judgment']['cost'])):
            content += '<p>' + label + ' : ' + text('inconnu' if cost is None or cost['status'] == 'UNKNOWN' else cost['amount'] + ' ' + cost['currency'])
            content += ' (source : ' + text(cost['source'] if cost else 'INCONNU') + ').</p>'
        content += '<p>Limites : ' + text('; '.join(record['limits'])) + '</p>'
        for label, value in (('Méthode et vérification de l’exemple', record['qualification']),
                             ('Déroulé de l’évaluation : consignes, pièces lues, désaccords et arbitrages', record['judgment']),
                             ('Liens connus entre les modèles qui ont préparé, évalué et répondu', record['configuration_links']),
                             ('Réglages demandés et constatés, avec leurs sources', {k: record[k] for k in ('requested_configuration', 'observed_configuration', 'observation_sources')}),
                             ('Ce que couvre le coût et comment il est compté', {k: record[k] for k in ('cost_basis', 'aggregation')})):
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
    # Largeur en attribut SVG : un `style` inline serait bloqué par `style-src 'self'`
    return ('<svg class="costbar" aria-hidden="true"><rect width="' + str(round(100 * float(value) / max(known)))
            + '%" height="100%"/></svg>')


EFFORT_LABELS = {'none': 'Désactivé (none)', 'minimal': 'Minimal (minimal)', 'low': 'Faible (low)',
                 'medium': 'Moyen (medium)', 'high': 'Élevé (high)', 'xhigh': 'Très élevé (xhigh)', 'max': 'Maximal (max)',
                 'standard': 'Fixe'}


def effort_label(configuration):
    if configuration.get('effort_limit') == 'not_adjustable':
        return 'Niveau de raisonnement fixe'
    effort = configuration.get('parameters', {}).get('reasoning', {}).get('effort', configuration.get('effort', 'off'))
    if effort == 'off':
        return 'Niveau de raisonnement non renseigné'
    if effort == 'on':
        return 'Raisonnement activé · niveau non renseigné'
    label = 'Niveau de raisonnement : ' + str(EFFORT_LABELS.get(effort, effort))
    requested = configuration.get('effort_requested')
    if requested:
        label += ' · adapté : ce modèle n’accepte pas ' + str(EFFORT_LABELS.get(requested, requested))
    return label


def model_name(configuration, names):
    """Nom commercial du relevé, sinon l'identifiant Openrouter : le même libellé sur chaque écran"""
    return names.get(configuration['model'], configuration['model'])


def candidate_label(configuration, names):
    """Candidat entier : nom commercial et effort déclaré"""
    return model_name(configuration, names) + ' · ' + effort_label(configuration)


def short_label(value, words=9):
    parts = value.split()
    return value if len(parts) <= words else ' '.join(parts[:words]) + '…'


EXPENSE_GROUPS = {
    'compared': ('Réponses comparées', 'Leur coût est le coût observé du tableau ; lui seul sert au conseil.'),
    'other_attempts': ('Autres envois aux modèles', 'Réponses remplacées par une reprise, inexploitables ou non envoyées. '
                       'Pour information, hors conseil.'),
    'judgment': ('Évaluation des réponses', 'Pour information, hors conseil.'),
    'probes': ('Vérification des modèles choisis', 'Pour information, hors conseil.'),
    'preparation': ('Préparation de l’exemple', 'Pour information, hors conseil. '
                    'Elle sert aussi aux autres comparaisons de cet exemple.'),
    'qualification': ('Contrôle de l’exemple', 'Pour information, hors conseil. '
                      'Il sert aussi aux autres comparaisons de cet exemple.'),
}
EXPENSE_STATES = {'NOT_SENT': 'non envoyé', 'STOPPED': 'reprise arrêtée avant envoi',
                  'PROVIDER_INCIDENT': 'incident du fournisseur', 'AMBIGUOUS_EXPIRED': 'réponse incertaine, close sans relance',
                  'INTENT_RECORDED': 'pas encore envoyé', 'EMISSION_POSSIBLE': 'envoi en cours',
                  'AMBIGUOUS': 'réponse incertaine, en attente'}


def render_expenses(expenses, names):
    """Ce que la comparaison a coûté, phase par phase ; réserves et estimations restent à part

    Chaque ligne donne le modèle et le montant ; provenance, état et référence courte de l'opération
    restent dans son détail
    """
    if not expenses:
        return ''
    known = montant_lisible(expenses['known']) + ' ' + expenses['unit']
    content = '<details id="expenses"><summary>' + text(
        ('Coût complet connu : ' if expenses['complete'] else 'Coût complet inconnu · sous-total connu : ') + known) + '</summary>'
    if not expenses['complete']:
        content += '<p>Au moins une dépense manque : le sous-total ne donne que la somme des dépenses connues, pas le coût complet.'
        awaiting = expenses.get('awaiting_judgment', 0)
        if awaiting:
            content += text(' L’évaluation de ' + _plural(awaiting, 'réponse') + ' est à venir : son coût n’est pas encore connu.')
        content += '</p>'
    content += ('<p>Chaque envoi est compté une fois : réponses des modèles et leurs reprises, évaluation, '
                'vérification des modèles retenus, préparation et contrôle de l’exemple jusqu’à cette version. '
                'Les montants réservés et estimés sont indiqués à part et ne s’ajoutent pas.</p>')
    for group in expenses['groups']:
        title, note = EXPENSE_GROUPS[group['key']]
        subtotal = montant_lisible(group['known']) + ' ' + expenses['unit']
        content += '<h3>' + text(title + ' : ' + (subtotal if group['complete'] else 'inconnu, sous-total connu ' + subtotal)) + '</h3>'
        content += '<p class="hint">' + text(note) + '</p><ul>'
        for item in group['operations']:
            cost = item['cost']
            if cost is not None and cost['status'] == 'KNOWN':
                amount = montant_lisible(cost['amount']) + ' ' + cost['currency']
                amount += '' if item['counted'] else ' · autre unité, non additionné'
            else:
                amount = {'STOPPED': 'aucune dépense', 'INTENT_RECORDED': 'dépense à venir'}.get(item['state'], 'dépense inconnue')
            details = [date_lisible_utc(item['created_at'])]
            if cost is not None:
                details.append('source : ' + cost['source'])
            if item['state'] in EXPENSE_STATES:
                details.append(EXPENSE_STATES[item['state']])
            if item['recovery'] and item['state'] != 'STOPPED':
                details.append('reprise après arrêt pour longueur')
            if item['reserved'] is not None:
                details.append('montant réservé : ' + montant_lisible(item['reserved']) + ' ' + item['reserved_unit'])
            if item['estimate'] is not None:
                details.append('estimation indicative : ' + montant_lisible(item['estimate']) + ' USD')
            content += '<li>' + text((model_name({'model': item['model']}, names) if item['model'] else 'Modèle non renseigné')
                                     + ' : ' + amount) + '<details><summary>Source et référence</summary><p>'
            content += text(' · '.join(details)) + ' · réf. <code>' + text(item['operation_id'][-8:]) + '</code></p></details></li>'
        content += '</ul>'
    return content + '</details>'


def render_comparison(value):
    base, query = value['href'], value['filter_scope']
    names = value.get('model_names', {})
    multiple_cases = len(value['cases']) > 1
    # Un lecteur arrivé par lien direct sait d'abord ce qui est comparé
    content = '<p class="hint">' + text(value['need']) + ' · Résultat attendu : ' + text(value['result_expected'])
    content += ' · Résultats visibles uniquement dans ce navigateur · Exemple validé '
    content += text(value['task']['version']) + ' · <a href="' + text(value['dossier_href']) + '">Revenir au cas d’usage</a></p>'
    content += '<div class="campaign-summary" aria-label="Bilan de la comparaison"><span class="ic">' + icon('i-scale') + '</span>'
    content += '<p class="eyebrow">Bilan de la comparaison</p>'
    latest = {record['attempt_id']: record for record in value['history']}
    for case_number, case in enumerate(value['cases'], 1):
        records = [record for record in latest.values() if record['case_id'] == case['id']]
        if records:
            counts = []
            for verdict, label in (('SATISFAIT', 'Satisfait'), ('NE SATISFAIT PAS', 'Ne satisfait pas'), (None, 'À reprendre')):
                count = sum(record['decision']['verdict'] == verdict for record in records)
                if count:
                    counts.append(label + ' : ' + str(count))
            prefix = '<strong>Cas ' + text(case_number) + '</strong> : ' if multiple_cases else ''
            content += '<p>' + prefix + text(' · '.join(counts)) + '.</p>'
    if not latest:
        received = any(cell['state'] == 'RECEIVED' for cell in value['cells'])
        content += '<p>' + ('Des réponses sont arrivées. Leur verdict n’est pas encore disponible.' if received else
                             'Aucune réponse n’a encore été évaluée. Le suivi indique où en est la comparaison.') + '</p>'
    panel = {configuration['id']: configuration for configuration in value.get('panel', [])}

    def pending_reason(item):
        # Le modèle sans réponse exploitable est nommé comme dans le tableau, avec la cause lue dans son reçu
        configuration = panel.get(item.get('configuration_id'))
        if item['state'] != 'NO_USABLE_RESPONSE' or configuration is None:
            return item['next_action']
        count = item.get('recoveries', 0)
        # Une cause que le reçu n'établit pas est dite inconnue, jamais devinée
        details = [item.get('cause') or 'cause non établie par le reçu'] + (
            [str(count) + (' reprises' if count > 1 else ' reprise')] if count else [])
        return ('Aucune réponse exploitable pour ' + model_name(configuration, names)
                + (' · ' + effort_label(configuration) if sum(c['model'] == configuration['model'] for c in panel.values()) > 1 else '')
                + ' (' + ' ; '.join(details) + ')'
                + '. Ce modèle n’est pas évalué et n’entre pas dans la comparaison.')
    pending_reasons = list(dict.fromkeys('<li>' + text(pending_reason(item)) + receipt_observations(item) + '</li>'
                                         for item in value.get('pending_attempts', [])))
    if pending_reasons:
        content += '<ul>' + ''.join(pending_reasons) + '</ul>'
    # Un modèle sans réponse exploitable est terminé : il ne fait pas dire que les essais se sont arrêtés
    open_pending = [item for item in value.get('pending_attempts', []) if item['state'] != 'NO_USABLE_RESPONSE']
    coverage = value['coverage']
    content += '<p class="note">Réponses évaluées : ' + text(coverage['evaluated_attempts']) + ' · essais lancés : '
    content += text(coverage['attempted_cells']) + ' sur ' + text(coverage['planned_cells']) + '. '
    if value['economic_status'] != 'COMPLETE':
        content += 'Comparaison des coûts incomplète : certains coûts observés manquent ou ne sont pas comparables. '
    content += '</p>'
    if value.get('stop_reason') and (coverage['not_started'] or open_pending):
        content += '<p>Comparaison incomplète : les essais se sont arrêtés avant la fin. Vous pouvez lire les réponses déjà reçues.</p>'
    dates = sorted(set(date[:10] for date in value.get('acquisition_dates', [])))
    if dates:
        content += '<p class="hint">Réponses reçues ' + text('le ' + jour_lisible(dates[0]) if len(dates) == 1 else
                                                          'du ' + jour_lisible(dates[0]) + ' au ' + jour_lisible(dates[-1])) + '.</p>'
    content += '</div>'
    if not value['population']:
        return content + render_expenses(value.get('expenses'), names)
    choice = value.get('recommendation')
    if choice:
        content += '<aside class="economic-choice" aria-labelledby="economic-choice-title">'
        content += '<h2 id="economic-choice-title">Notre conseil</h2><div class="choice-highlight"><div>'
        content += '<p class="choice-model">' + text(model_name(choice['configuration'], names)) + '</p>'
        content += '<p>' + text(effort_label(choice['configuration'])) + '</p></div>'
        content += '<p class="choice-cost">Coût observé<strong>' + text(montant_lisible(choice['amount']) + ' ' + choice['unit']) + '</strong></p></div>'
        if choice['basis'] == 'quality_then_cost':
            criteria = list(dict.fromkeys(definition.get('measure') or definition.get('label')
                                          for definition in choice['quality']
                                          if definition.get('measure') or definition.get('label')))
            definition = ('Ici, la qualité désigne les critères fixés pour votre cas d’usage '
                          'et vérifiés dans chaque réponse' + (text(' : ' + ' ; '.join(criteria)) if criteria else '') + '.')
            content += '<p>Nous retenons d’abord la <span class="quality-term"><span class="quality-help" tabindex="0" '
            content += 'aria-describedby="quality-definition">qualité observée</span>'
            content += '<span id="quality-definition" class="quality-tooltip" role="tooltip">' + definition + '</span></span> la plus élevée. '
            content += 'À qualité égale, le coût observé le plus bas l’emporte.</p>'
        else:
            content += '<p>Les ' + text(choice['count']) + ' réponses qui satisfont l’exemple ont la même qualité observée. Nous retenons la moins coûteuse.</p>'
        content += '<p class="hint">Ce conseil repose sur un seul exemple. Sur d’autres tâches, le résultat peut être différent.</p></aside>'
    sort_column = next((column for column in value['columns'] if column['id'] == query.get('sort')), None)
    sort_label = (sort_column['definition'].get('measure', 'Coût observé') if sort_column else 'sans tri')
    options = {
        'case': ('Cas testé', 'Tous les cas', [(v['id'], 'Cas ' + str(number))
                         for number, v in enumerate(value['cases'], 1)] if multiple_cases else []),
        'configuration': ('Modèle', 'Tous les modèles', [(v['id'], model_name(v, names) +
                           (' · ' + effort_label(v) if sum(p['model'] == v['model'] for p in value['panel']) > 1 else '')) for v in value['panel']]),
        'verdict': ('Résultat', 'Tous les résultats', [('SATISFAIT', 'Satisfait'), ('NE SATISFAIT PAS', 'Ne satisfait pas'), ('A_REPRENDRE', 'À reprendre')]),
        'sort': ('Trier par', 'Sans tri', [(v['id'], v['definition'].get('measure', 'Coût observé')) for v in value['columns']]),
        'direction': ('Sens du tri', 'Croissant', [('desc', 'Décroissant')]),
        'obligation': ('Exigence à examiner', 'Toutes les exigences', [(v['id'] + ':' + state, short_label(v['description']) + ' : ' + label)
                        for v in value['obligations'] for state, label in
                        (('PASS', 'Respectée'), ('FAIL', 'Non respectée'), ('INDETERMINE', 'Non vérifiable'))]),
    }
    content += '<section class="comparison-results"><h2>Comparaison des modèles</h2>'
    content += '<p class="table-hint">Sur petit écran, faites défiler le tableau horizontalement.</p>'
    content += '<details id="filters" class="comparison-filters"' + (' open' if query else '') + '><summary>Trier et filtrer</summary>'
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
    content += '<a class="button sec" href="' + text(base) + '#filters">Retirer les filtres</a></div></form></details>'
    content += '<p class="view-scope note">' + text(len(value['rows'])) + (' réponse affichée sur ' if len(value['rows']) == 1 else ' réponses affichées sur ')
    content += text(len(value['population'])) + ' · ' + text('triées par ' + sort_label[:1].lower() + sort_label[1:] if sort_column else sort_label)
    if sort_column:
        content += ', ordre décroissant' if query.get('direction') == 'desc' else ', ordre croissant'
    content += '.</p>'
    if not value['rows']:
        content += '<p class="note">Aucune réponse ne correspond à ces filtres. Retirez un filtre pour revoir les autres : rien n’a été supprimé.</p>'
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
            content += '<strong>' + text(model_name(row['requested_configuration'], names)) + '</strong>'
            content += '<p class="hint">' + text(effort_label(row['requested_configuration'])) + '</p>'
            if row.get('recovery_limit'):
                content += '<p class="hint">' + text('Reprise après arrêt pour longueur : limite de sortie de '
                                                     + str(row['recovery_limit']) + ' jetons') + '</p>'
            content += receipt_observations(row)
            content += '</th>'
            # Les critères qui fondent le verdict, entiers ; l'explication du juge reste dans le détail
            reason = ('Toutes les exigences sont respectées.' if row['verdict'] == 'SATISFAIT' else
                      _failure_reason(row) if row['verdict'] == 'NE SATISFAIT PAS' else 'Le verdict n’est pas encore disponible.')
            content += '<td>' + badge(row['verdict']) + '<p class="hint">' + text(reason) + '</p></td>'
            if quality_columns:
                content += '<td><ul class="quality-list">'
                for column in quality_columns:
                    measure = next(m for m in row['measures'] if m['criterion_id'] == column['criterion_id'])
                    shown = valeur_mesure(measure['value'], measure.get('unit')).capitalize()
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
            content += '<td><a class="text-link" data-result href="' + text(row['detail_href']) + '">Détail et preuves</a></td></tr>'
        content += '</tbody></table></div>'
    conditions = value['conditions']
    content += '</section>' + render_expenses(value.get('expenses'), names)
    content += '<details id="method"><summary>Comment lire ces résultats</summary>'
    content += '<ul><li><strong>Satisfait</strong> : toutes les exigences sont respectées et aucune erreur éliminatoire n’a été relevée.</li>'
    content += '<li><strong>Ne satisfait pas</strong> : une exigence n’est pas respectée ou une erreur éliminatoire a été relevée ; la réponse n’est pas utilisable, quel que soit son coût.</li>'
    content += '<li><strong>À reprendre</strong> : la preuve ne permet pas encore de conclure ; ce n’est pas un échec du modèle.</li>'
    content += '<li>Lisez d’abord le verdict, puis la qualité observée. Le coût observé sert seulement à départager des réponses qui satisfont l’exemple avec la même qualité. Un coût inconnu ne change pas le verdict.</li>'
    content += '<li>' + text(value['conclusion']['attribution']) + '</li>'
    if value['conclusion']['limits']:
        content += '<li>Limite principale : ' + text(value['conclusion']['limits'][0]) + '</li>'
    content += '<li>Conditions communes : tous les modèles reçoivent la même consigne et les mêmes pièces, et travaillent avec le même outil, '
    content += text(conditions['pi']['package'] + ' ' + conditions['pi']['version'])
    content += ', fixé le ' + text(date_lisible_utc(conditions['frozen_at'])) + '.</li></ul></details>'
    content += '<p><a href="' + text(base + '/preview') + '">Voir l’aperçu d’une publication</a> (rien n’est publié)</p>'
    content += '<dialog id="result-dialog" class="result-dialog" aria-label="Détail et preuves">'
    content += '<div class="result-head"><button type="button" class="sec" data-close autofocus>Fermer</button>'
    content += '<p class="result-status" role="status"></p></div><div class="result-body"></div>'
    content += '</dialog>'
    return content


def render_campaign_models(value):
    content = '<p>Voici les modèles et les réglages utilisés pour cette comparaison. Consulter cette page ne change rien et n’appelle aucun modèle.</p>'
    for model in value['panel']:
        content += section(model_name(model, value.get('model_names', {})), '<p>' + text(effort_label(model)) + '</p>' +
                           '<details><summary>Réglages utilisés</summary>' + readable_fields(model['parameters']) + '</details>')
    content += '<p><a href="' + text(value['href']) + '">Revenir aux résultats</a></p>'
    content += '<p class="hint">Pour changer les modèles ou l’exemple, préparez une nouvelle comparaison depuis le cas d’usage. Les résultats actuels seront conservés.</p>'
    return content


def render_configurations(value, csrf):
    """Choix des modèles et du palier, puis estimation de la sélection courante"""
    dossier_url = '/preparation/dossiers/' + value['dossier_id']
    content = '<p><a href="' + text(dossier_url) + '">Revenir au cas d’usage</a></p>'
    content += '<p class="note">Choisissez au moins deux modèles et un niveau de raisonnement. Enregistrer ce choix n’appelle aucun modèle.</p>'
    if not value.get('catalogue_available', True):
        content += '<p>' + text(value['detail']) + '</p>'
        content += render_custom_models(value, csrf, dossier_url)
    else:
        if value.get('catalogue_stale'):
            content += '<p class="note">La liste des modèles n’a pas pu être mise à jour. Vous voyez la dernière liste disponible, qui peut ne plus être à jour.</p>'
        choices = ''
        for model in value['models']:
            checked = ' checked' if model['selected'] else ''
            choices += '<label><input type="checkbox" name="models" value="' + text(
                model['id']) + '"' + checked + '> ' + text(model['name'])
            if model['not_adjustable']:
                choices += ' · niveau de raisonnement fixe'
            choices += '</label>'
        labels = EFFORT_LABELS
        tiers = '<label for="reasoning-effort">Niveau de raisonnement demandé</label>'
        tiers += '<select id="reasoning-effort" form="configurations-form" name="tier" aria-describedby="reasoning-help">'
        tiers += ''.join('<option value="' + text(tier) + '"' +
                         (' selected' if value['current_tier'] == tier else '') + '>' +
                         text(labels.get(tier, tier)) + '</option>' for tier in value['available_tiers'])
        tiers += '</select><p class="hint" id="reasoning-help">Ce niveau est transmis aux modèles qui le proposent. '
        tiers += 'Un modèle qui ne l’accepte pas reçoit le niveau le plus proche qu’il propose, affiché dans le récapitulatif. Les modèles à niveau fixe sont comparés tels quels. Un niveau plus élevé peut rendre la réponse plus lente et plus chère, sans garantir qu’elle soit meilleure.</p>'
        tunable = [model for model in value['models'] if len(model.get('levels', ())) > 1]
        if tunable:
            tiers += '<details><summary>Ajuster le niveau par modèle</summary><p class="hint">« Automatique » suit le niveau demandé ci-dessus. Seuls les niveaux acceptés par chaque modèle sont proposés.</p>'
            for index, model in enumerate(tunable, 1):
                field = 'effort-' + str(index)
                tiers += ('<label for="' + field + '">' + text(model['name']) + '</label><select id="' + field +
                          '" form="configurations-form" name="' + text('effort:' + model['id']) + '">' +
                          '<option value="">Automatique</option>' +
                          ''.join('<option value="' + text(level) + '"' + (' selected' if model.get('chosen') == level else '') +
                                  '>' + text(labels.get(level, level)) + '</option>' for level in model['levels']) +
                          '</select>')
            tiers += '</details>'
        content += ('<form id="configurations-form" method="post" action="' + text(dossier_url + '/configurations') + '">' +
                    hidden('csrf_token', csrf) + '<fieldset id="model-choices"><legend>Modèles à comparer</legend>' +
                    choices + '</fieldset></form>')
        content += render_custom_models(value, csrf, dossier_url)
        # Le récapitulatif de la sélection suit l'enregistrement : le serveur y redirige
        content += ('<fieldset><legend>Raisonnement</legend>' + tiers +
                    '</fieldset><button form="configurations-form" type="submit">Continuer</button>')
    return content


def campaign_followup(campaign):
    """État d’affichage seulement : ne crée aucune autorité ni reprise"""
    cells = campaign['cells']
    judgment = campaign.get('judgment')
    if judgment:
        status = judgment['status']
        if status == 'COMPLETE':
            # Couverture partielle : le nombre de modèles sans réponse exploitable reste dit
            return False, True, ' '.join(filter(None, ('Évaluation terminée.', judgment.get('reason'), 'Vos résultats sont prêts.')))
        if status == 'BLOCKED':
            return False, False, judgment.get('reason') or 'L’évaluation s’est arrêtée. Elle ne reprendra pas d’elle-même.'
        if judgment.get('recovering'):
            count = judgment['recovering']
            return True, False, (str(count) + (' modèles arrêtés' if count > 1 else ' modèle arrêté')
                                 + ' par la limite de longueur ' + ('sont relancés' if count > 1 else 'est relancé')
                                 + ' avec une limite de sortie plus haute. Les réponses arrivent au fur et à mesure.')
        if status in ('WAITING', 'RUNNING') and cells and all(c['state'] == 'RECEIVED' for c in cells):
            # Une relance automatique programmée dit sa cause et son délai ; rien n'est à faire
            return True, False, ('Toutes les réponses sont arrivées. Leur évaluation attend son tour.' if status == 'WAITING'
                                 else 'Évaluation en cours. ' + judgment['reason'] if judgment.get('reason')
                                 else 'Évaluation en cours : chaque réponse est vérifiée selon les critères de votre exemple.')
        if status == 'NOT_STARTED' and cells and all(c['state'] == 'RECEIVED' for c in cells):
            return False, False, 'Toutes les réponses sont arrivées. Leur évaluation n’est pas encore lancée.'
    stopped = campaign.get('stop_reason') or campaign.get('restore_pending') or not campaign.get('admission_open')
    # Un modèle encore interrogé garde le suivi actif, même si un autre a déjà rencontré un incident
    if not stopped and any(c['state'] == 'EMISSION_POSSIBLE' for c in cells):
        return True, False, 'Les modèles sont interrogés. Les réponses arrivent au fur et à mesure.'
    if not stopped and any(c['state'] == 'INTENT_RECORDED' for c in cells):
        return True, False, 'Votre lancement est enregistré. Les essais n’ont pas encore démarré.'
    if any(a.get('incident') == 'CONNECTION_FAILED' for a in campaign['attempts']):
        return False, False, NOT_SENT_TEXT + ' La comparaison s’est arrêtée et rien n’est relancé automatiquement.'
    if any(a.get('incident') == AMBIGUOUS_EXPIRED for a in campaign['attempts']):
        return False, False, AMBIGUOUS_EXPIRED_TEXT + ' La comparaison s’est arrêtée ; les réponses déjà reçues restent consultables.'
    if any(a.get('incident') or a.get('attribution_incident') for a in campaign['attempts']):
        return False, False, 'Un problème technique est survenu. Le suivi s’est arrêté et rien n’est relancé automatiquement.'
    if any(c['state'] == 'AMBIGUOUS' for c in cells) or campaign.get('state') == 'BLOCKED':
        return False, False, 'Un essai n’a pas pu être confirmé : nous ne savons pas si le modèle a répondu. Rien n’est relancé automatiquement.'
    # Toutes les réponses reçues est l'issue normale : la fermeture d'admission qui suit ne la masque pas
    if cells and all(c['state'] == 'RECEIVED' for c in cells):
        return False, True, 'Toutes les réponses sont arrivées. Celles qui n’ont pas encore de verdict attendent leur évaluation.'
    if stopped:
        return False, False, 'La comparaison s’est arrêtée avant la fin. Vous pouvez lire les réponses déjà reçues.'
    return False, False, 'Les essais n’ont pas encore démarré. Actualisez la page dans un moment pour voir s’ils ont commencé.'


def campaign_status(campaign, dossier_url):
    """Ton, titre, phrase, cible et action d'une comparaison : même lecture sur la page du cas et le suivi"""
    base = dossier_url + '/campaigns/' + campaign['campaign_id']
    if not campaign['attempts']:
        return ('action', 'Comparaison prête à lancer', 'Vérifiez les modèles et les coûts estimés, puis lancez la comparaison.',
                base + '/conditions', 'Vérifier puis lancer la comparaison')
    if (campaign.get('judgment') or {}).get('can_start'):
        # Le bouton qui lance l'évaluation payante est sur le suivi : le cas y mène, jamais vers des résultats vides
        return ('action', 'Évaluation à lancer', 'Les réponses sont arrivées. Leur évaluation, payée avec votre clé '
                'OpenRouter, se lance depuis le suivi de la comparaison.', base + '/conditions', 'Lancer l’évaluation')
    active, ready, message = campaign_followup(campaign)
    if ready:
        return 'done', 'Comparaison terminée', message, base, 'Voir les résultats'
    if active:
        return 'wait', 'Benchmark en cours', message, base + '/conditions', 'Suivre la comparaison'
    return 'warn', 'La comparaison demande votre attention', message, base + '/conditions', 'Voir ce qui s’est passé'


def render_campaign_followup(value, csrf):
    campaign = value['campaign']
    base = '/preparation/dossiers/' + value['dossier_id'] + '/campaigns/' + campaign['campaign_id']
    active, ready, message = campaign_followup(campaign)
    content = '<div id="campaign-followup"' + (' data-results-href="' + text(base) + '"' if ready else '') + '>'
    content += '<div id="campaign-status" aria-live="polite">'
    tone, heading, _, _, _ = campaign_status(campaign, '/preparation/dossiers/' + value['dossier_id'])
    content += state_block(tone, 'Où en est la comparaison', heading, '<p>' + text(message) + '</p>')
    states = {'NOT_STARTED': 'non démarré', 'INTENT_RECORDED': 'en attente de démarrage',
              'EMISSION_POSSIBLE': 'en cours', 'RECEIVED': 'réponse reçue',
              'AMBIGUOUS': 'réponse non confirmée, à vérifier'}
    models = {item['id']: candidate_label(item, value.get('model_names', {})) for item in campaign['panel']}
    content += section('Modèle par modèle', listing(
        models.get(cell['configuration_id'], 'Modèle') + ' : ' + states.get(cell['state'], 'état inconnu')
        for cell in campaign['cells']))
    received = sum(cell['state'] == 'RECEIVED' for cell in campaign['cells'])
    all_received = bool(campaign['cells']) and received == len(campaign['cells'])
    content += '<p>Réponses reçues : ' + str(received) + ' sur ' + str(len(campaign['cells'])) + '.</p>'
    judgment = campaign.get('judgment')
    if judgment:
        content += '<p>Réponses évaluées : ' + text(judgment['completed']) + ' sur ' + text(judgment['total']) + '.</p>'
    content += '</div>'
    if active:
        content += '<div id="preparation-progress"><progress aria-label="Benchmark en cours"></progress>'
        content += '<p class="hint" role="status">Cette page se met à jour seule si JavaScript est activé. Sinon, actualisez-la.</p><div class="actions">'
        content += '<a class="button" href="' + text(base + '/conditions') + '">Actualiser le suivi</a>'
        content += '<button type="button" class="sec" hidden>Arrêter la mise à jour automatique</button></div></div>'
    else:
        content += '<p><a class="button' + (' sec' if all_received else '') + '" href="' + text(base + '/conditions') + '">Actualiser le suivi</a></p>'
    if judgment and judgment['can_start']:
        content += form(csrf, base + '/evaluate', {'confirm': 'yes'},
            '<p>L’évaluation des réponses reçues est payée avec votre clé OpenRouter. Les modèles comparés ne sont pas appelés une seconde fois.</p>'
            '<button type="submit">Évaluer les réponses reçues</button>')
    if not active and (ready or not judgment or judgment['completed'] > 0):
        content += '<p><a class="button' + ('' if all_received else ' sec') + '" href="' + text(base) + '">Voir les résultats</a></p>'
    return content + '</div>'


def render_campaign_launch_requester(value, csrf):
    """Récapitulatif avant dépense : synthèse, bouton, détail ; puis suivi une fois lancé"""
    campaign = value['campaign']
    if campaign['attempts']:
        return render_campaign_followup(value, csrf)
    dossier_url = '/preparation/dossiers/' + value['dossier_id']
    base = dossier_url + '/campaigns/' + campaign['campaign_id']
    names = value.get('model_names', {})
    panel = campaign['panel']
    # Prévision des réponses et réservation de l'évaluation restent deux montants distincts (RULES.md, contrôle de dépense)
    def usd(amount, unknown):
        return unknown if amount is None else montant_lisible(amount) + ' USD'
    content = '<p><a href="' + text(dossier_url) + '">Revenir au cas d’usage</a></p>'
    content += '<p class="synthesis">' + text(
        str(len(panel)) + (' modèles' if len(panel) > 1 else ' modèle')
        + ' · réponses estimées : ' + usd(value.get('estimate_total_usd'), 'non estimable')
        + ' · réservé pour l’évaluation : ' + usd(value.get('judgment_estimate_usd'), 'inconnu')
        + ' · crédit restant : ' + usd(value.get('access', {}).get('limit_remaining_usd'), 'inconnu')) + '</p>'
    if value['launchable']:
        # Le bouton est la confirmation : le serveur exige toujours `confirm=yes`
        content += form(csrf, base + '/start', {
            'manifest_version': campaign['version'],
            'frozen_at': campaign['conditions']['frozen_at'], 'confirm': 'yes'},
            '<button type="submit">Lancer le benchmark</button>')
    content += '<p><a href="' + text(dossier_url + '/configurations') + '">Modifier la sélection</a></p>'
    rows = ''.join('<tr><th scope="row">' + text(model_name(item, names)) + '</th><td>'
                   + text(effort_label(item).removeprefix('Niveau de raisonnement : ')) + '</td><td>'
                   + text(usd(item['estimate']['amount_usd'], 'non estimable')) + '</td></tr>' for item in panel)
    content += ('<div class="table-scroll" role="region" tabindex="0" aria-label="Modèles sélectionnés">'
                '<table class="compact"><thead><tr><th scope="col">Modèle</th><th scope="col">Niveau de raisonnement</th>'
                '<th scope="col">Coût estimé</th></tr></thead><tbody>' + rows + '</tbody></table></div>')
    content += '<p>Tous les appels passent par votre clé OpenRouter. Son plafond est la seule limite de dépense. Les montants affichés ici sont une prévision et une réservation, pas des dépenses facturées.</p>'
    if value.get('recovery_limits'):
        # Reprises préautorisées par ce lancement (RULES.md §9) : leur coût reste distinct de la prévision
        content += '<p>' + text(
            'Un modèle arrêté par la limite de longueur est relancé au plus deux fois, avec '
            + ' puis '.join(str(limit) for limit in value['recovery_limits']) + ' jetons de sortie ; '
            'chaque reprise apparaît sur sa propre ligne. Coût estimé si tous les modèles étaient repris deux fois : '
            + usd(value.get('recovery_estimate_usd'), 'non estimable') + '.') + '</p>'
    content += section('Ce qui sera testé', '<p>' + text(value['criteria']['result_expected']) + '</p>' +
        '<p>Chaque modèle reçoit la même consigne et les mêmes pièces. Le verdict vaudra pour cet exemple et pour chaque modèle tel qu’il est réglé ici, sans conclure sur le modèle en général.</p>' +
        '<details><summary>Détail des critères et des réglages</summary>' + readable_fields(
            {'criteria': value['criteria'], 'conditions': campaign['conditions'], 'panel': panel}) + '</details>')
    failures = [check for check in value['checks'] if not check['ok']]
    check_content = '' if failures else '<p class="note">Tout est prêt.</p>'
    if failures:
        check_content += '<ul>'
        for check in failures:
            detail = check['detail']
            if type(detail) is dict:
                detail = ('Crédit restant sur votre clé : ' + usd(detail.get('limit_remaining_usd'), 'inconnu') +
                          ', pour un plafond de ' + usd(detail.get('limit_usd'), 'inconnu'))
            check_content += '<li>' + text('✕ ' + str(detail))
            if check['key'] == 'example_qualified' and check.get('findings'):
                check_content += '<p>Ce que la vérification de l’exemple a relevé</p>' + listing(
                    finding['text'] for finding in check['findings'])
            check_content += '</li>'
        check_content += '</ul>'
    failed = failures[0] if failures else None
    if failed and not value['launchable']:
        # Chaque contrôle mène à son étape ; la clé, elle, s'ajoute ici même puis revient à ce récapitulatif
        links = {
            'selection_current': (dossier_url + '/campaigns/' + str(failed.get('latest')) + '/conditions',
                                  'Voir la dernière sélection'),
            'example_validated': (dossier_url + '#validation', 'Valider l’exemple'),
            'example_qualified': (dossier_url, 'Voir la vérification de l’exemple'),
            'configurations_available': (dossier_url + '/configurations', 'Changer de modèles'),
            'access_connected': None,
            'estimate_available': (dossier_url + '/configurations', 'Revoir les modèles choisis'),
        }
        detail = failed['detail'] if type(failed['detail']) is str else 'ajoutez d’abord votre clé OpenRouter'
        check_content += '<p class="note">Impossible de lancer pour l’instant : ' + text(detail) + '.'
        if links.get(failed['key']):
            href, label = links[failed['key']]
            check_content += ' <a class="button" href="' + text(href) + '">' + text(label) + '</a>'
        check_content += '</p>'
        if failed['key'] == 'access_connected':
            check_content += personal_key_form(csrf, value.get('access', {}), base + '/conditions', opened=True)
    elif not value['launchable']:
        check_content = '<p class="note">Impossible de lancer pour l’instant : le service d’exécution est indisponible. Réessayez dans quelques minutes.</p>'
    content += section('Avant de lancer', check_content)
    return content


def render_campaign_launch_operator(value, csrf):
    """Comparaison préparée et admise par l'opérateur, confirmée par le demandeur"""
    campaign = value['campaign']
    if campaign['attempts']:
        return render_campaign_followup(value, csrf)
    base = '/preparation/dossiers/' + value['dossier_id'] + '/campaigns/' + campaign['campaign_id']
    content = '<p>Cette comparaison a été préparée et autorisée par l’équipe Bench-X. En confirmant, vous lancez seulement les essais qu’elle a autorisés.</p>'
    content += '<p><a href="' + text('/preparation/dossiers/' + value['dossier_id']) + '">Revenir au cas d’usage</a></p>'
    groups = '<div class="crit">'
    if value['criteria']['eliminatory_errors']:
        groups += '<div class="grp elim"><h3>' + badge('NE SATISFAIT PAS') + 'Erreurs éliminatoires</h3>' + listing(item['description'] for item in value['criteria']['eliminatory_errors']) + '</div>'
    groups += '<div class="grp oblig"><h3>' + badge('SATISFAIT') + 'Exigences à respecter</h3>' + listing(item['description'] for item in value['criteria']['obligations']) + '</div></div>'
    content += section('Ce qui sera vérifié', '<p>' + text(value['criteria']['result_expected']) + '</p>' + groups)
    content += section('Modèles et conditions', listing([candidate_label(item, value.get('model_names', {})) for item in campaign['panel']]) +
        '<p>Outil commun à tous les modèles : Pi ' + text(campaign['conditions']['pi']['package']) + ' ' + text(campaign['conditions']['pi']['version']) +
        '. Conditions fixées le ' + text(date_lisible_utc(campaign['conditions']['frozen_at'])) + '.</p>' +
        '<details><summary>Détail technique des réglages</summary><pre>' + text(encode(dict(panel=campaign['panel'], conditions=campaign['conditions']))) + '</pre></details>')
    access = value.get('access', {'status': 'unavailable'})
    if access.get('status') == 'unavailable':
        access_content = '<p>Impossible d’enregistrer une clé pour le moment.</p>'
    else:
        access_content = ('<p>Clé OpenRouter enregistrée. ' + access_summary(access) + '</p>'
                          if access.get('status') == 'connected' else '')
        access_content += personal_key_form(csrf, access, base + '/conditions', opened=access.get('status') != 'connected')
    content += section('Votre clé OpenRouter', access_content)
    estimate = value['estimate']
    content += '<h2>Coût</h2><p>Coût estimé : ' + text(
        estimate['amount'] + ' ' + estimate['currency'] if estimate else 'aucune estimation fournie') + '.</p>'
    if estimate:
        content += '<p>' + text(estimate['assumptions']) + ' Source : ' + text(estimate['source']) + '.</p>'
    budget = campaign['budget']
    if budget:
        content += '<p>Budget autorisé : ' + text(budget['limit']) + ' ' + text(budget['currency']) + '.</p>'
    content += '<p>Montant mis de côté par essai : ' + text(', '.join(key + ' : ' + amount for key, amount in campaign['reserve_amounts'].items()) if campaign['reserve_amounts'] else 'aucun') + '.</p>'
    content += '<p>L’estimation, le montant mis de côté et le coût observé sont trois montants différents. Le montant mis de côté ne garantit pas un plafond de facturation.</p>'
    if value['can_launch']:
        content += form(csrf, base + '/start', {'manifest_version': campaign['version'],
            'frozen_at': campaign['conditions']['frozen_at'], 'admission_id': value['admission_id']},
            '<label><input type="checkbox" name="confirm" value="yes" required> Je confirme le lancement des essais autorisés ci-dessus.</label><button type="submit">Lancer la comparaison</button>')
    else:
        content += '<p class="note">' + ('Lancement enregistré. Consultez les essais et leurs résultats ci-dessous.' if campaign['attempts'] else 'Impossible de lancer pour l’instant. L’équipe Bench-X doit vérifier les autorisations et s’assurer que les comparaisons peuvent être exécutées.') + '</p>'
    models = {item['id']: candidate_label(item, value.get('model_names', {})) for item in campaign['panel']}
    content += section('Suivi des essais', listing([models.get(cell['configuration_id'], 'Modèle') + ' : ' + {'NOT_STARTED': 'non démarré', 'INTENT_RECORDED': 'en attente', 'EMISSION_POSSIBLE': 'en cours', 'RECEIVED': 'réponse reçue', 'AMBIGUOUS': 'réponse non confirmée, à vérifier'}.get(cell['state'], cell['state']) for cell in campaign['cells']]))
    content += '<p><a href="' + text(base + '/conditions') + '">Actualiser le suivi</a> · <a href="' + text(base) + '">Voir les résultats</a></p>'
    return content


def receipt_observations(item):
    """Motif et consommation transmis par essai, de l'origine à la dernière reprise ; une absence reste inconnue"""
    lines = []
    for number, observed in enumerate(item.get('observations', [])):
        usage = observed['usage']
        lines.append(('Essai d’origine' if number == 0 else 'Reprise ' + str(number))
                     + ' · motif de fin transmis : ' + (observed['finish_reason'] or 'inconnu')
                     + (' (précision transmise : ' + observed['finish_detail'] + ')' if observed['finish_detail'] else '')
                     + ' · consommation transmise : ' + (', '.join(
                         key + ' ' + ('inconnue' if amount is None else str(amount) if type(amount) in (int, str) else encode(amount))
                         for key, amount in _dotted_pairs(usage)) if usage else 'inconnue'))
    return '<details><summary>Observations techniques du reçu</summary>' + listing(lines) + '</details>' if lines else ''


def _dotted_pairs(value, prefix=''):
    """Paires `chemin.clé`, valeur d'un objet imbriqué, dans l'ordre transmis"""
    for key, item in value.items():
        if type(item) is dict and item:
            yield from _dotted_pairs(item, prefix + key + '.')
        else:
            yield prefix + key, item


PREVIEW_LENGTH = 200

CRITERION_STATES = {
    'obligation': {'PASS': ('b-ok', 'i-check', 'Respectée'), 'FAIL': ('b-ko', 'i-cross', 'Non respectée'),
                   'INDETERMINE': ('b-ind', 'i-help', 'Non vérifiable')},
    'eliminatory': {'PASS': ('b-ok', 'i-check', 'Évitée'), 'FAIL': ('b-ko', 'i-cross', 'Commise'),
                    'INDETERMINE': ('b-ind', 'i-help', 'Non vérifiable')},
}

def _plural(count, label, *, number=True):
    """« 2 exigences », « 1 non respectée » : accord simple, « non » invariable"""
    words = ' '.join(word + ('s' if count > 1 and word != 'non' else '') for word in label.split(' '))
    return (str(count) + ' ' if number else '') + words


def _states(record):
    return states(record['findings'], record['output_piece_id'], record['verdict'])


def _proof_anchor(record, piece_id):
    link = next((p for p in record['proof_links'] if p['piece_id'] == piece_id), None)
    if link is None:
        return None, None
    if piece_id in record.get('proof_contents', {}):
        return link, '#proof-' + record['evaluation_id'] + '-' + piece_id
    return link, link['href']


def _criteria_list(record, criteria, kind):
    found = _states(record)
    content = '<ul class="checks">'
    for criterion in criteria:
        elements = criterion.get('elements', [])
        # Une obligation composée montre l'état et les constats de chacun de ses éléments
        nested = '<ul class="checks">' + ''.join(
            _check(record, kind, found.get((criterion['id'], e['id']), 'INDETERMINE'), e['description'],
                   [f for f in record['findings'] if (f['criterion_id'], f['control_id']) == (criterion['id'], e['id'])])
            for e in elements) + '</ul>' if elements else ''
        content += _check(record, kind, criterion_state(found, criterion['id']), criterion['description'],
                          [] if elements else [f for f in record['findings'] if f['criterion_id'] == criterion['id']], nested)
    return content + '</ul>'


def _check(record, kind, state, description, findings, nested=''):
    """Ligne d'état, ses constats, puis `nested` : les éléments d'une obligation composée"""
    tone, name, label = CRITERION_STATES[kind][state]
    content = '<li><span class="badge ' + tone + '">' + icon(name) + label + '</span><span>' + text(description) + '</span>'
    if findings:
        content += '<details><summary>Constats et extraits</summary><ul>'
        for finding in findings:
            content += '<li>' + text(_readable(record, finding['finding']))
            if finding['attribution'] not in ('candidate', 'evidence'):
                content += (' <span class="hint">(ce constat provient de la référence d’évaluation et non de la réponse du modèle)</span>'
                            if finding['attribution'] == 'reference' else
                            ' <span class="hint">(attribué à : ' + text(finding['attribution']) + ')</span>')
            for proof in finding['evidence']:
                link, target = _proof_anchor(record, proof['piece_id'])
                if link is None:
                    continue
                content += '<details><summary>Extrait de ' + text('la réponse du modèle' if link['piece_id'] == record['output_piece_id'] else link['name']) + '</summary><pre>' + text(proof['passage']) + '</pre>'
                content += '<p><a href="' + text(target) + '">Ouvrir la pièce</a></p></details>'
            content += '</li>'
        content += '</ul></details>'
    return content + nested + '</li>'


def render_result(record, names=None):
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
    name = model_name(configuration, names or {})
    content += '<h2>' + text(name) + '</h2><p class="hint">' + text(effort_label(configuration)) + '</p>'
    if name != configuration['model']:
        content += '<details><summary>Identifiant OpenRouter</summary><p><code>' + text(configuration['model']) + '</code></p></details>'
    cost = record['candidate_cost']
    content += '<p class="result-verdict">' + badge(verdict) + ' <span>Coût observé : ' + text(
        'inconnu' if cost is None or cost['status'] != 'KNOWN' else montant_lisible(cost['amount']) + ' ' + cost['currency']) + '</span></p>'
    if verdict is None and record['decision'].get('next_action'):
        content += '<p>' + text(record['decision']['next_action']) + '</p>'
    # Ce que le modèle a produit
    content += '<h3>La réponse du modèle</h3>'
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
        content += '<p>Aucune réponse n’a été enregistrée pour ce modèle.</p>'
    # Pourquoi ce verdict
    content += '<h3>Pourquoi ce verdict</h3>'
    found = _states(record)
    obligations = [criterion_state(found, c['id']) for c in spec['obligations']]
    eliminatory = [criterion_state(found, c['id']) for c in spec['eliminatory_errors']]
    summary = [_plural(obligations.count('PASS'), 'exigence') + ' sur ' + str(len(obligations)) + ' ' + _plural(obligations.count('PASS'), 'respectée', number=False)]
    if obligations.count('FAIL'):
        summary.append(_plural(obligations.count('FAIL'), 'non respectée'))
    if obligations.count('INDETERMINE'):
        summary.append(_plural(obligations.count('INDETERMINE'), 'non vérifiable'))
    content += '<p>' + ', '.join(summary) + '.'
    if eliminatory:
        content += (' Aucune erreur éliminatoire relevée.' if all(state == 'PASS' for state in eliminatory) else
                    ' ' + _plural(eliminatory.count('FAIL'), 'erreur éliminatoire relevée') + '.' if 'FAIL' in eliminatory else
                    ' Les erreurs éliminatoires n’ont pas toutes pu être vérifiées.')
    content += '</p>'
    reason = _readable_reason(record)
    content += ('<p class="hint">' + text(reason) + '</p>' if len(reason) <= PREVIEW_LENGTH else
                '<details><summary>Explication de l’évaluation</summary><p>' + text(reason) + '</p></details>')
    content += '<details><summary>Voir les exigences vérifiées</summary>' + _criteria_list(record, spec['obligations'], 'obligation') + '</details>'
    if spec['eliminatory_errors']:
        content += '<details><summary>Erreurs éliminatoires recherchées</summary>' + _criteria_list(record, spec['eliminatory_errors'], 'eliminatory') + '</details>'
    if record['measures']:
        content += '<details><summary>Autres observations</summary><p class="hint">Elles complètent le verdict sans former une note globale.</p><ul>'
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
        content += '<h3>Limites de ce résultat</h3>' + listing(limits)
    # Pièces
    references = {piece['id'] for piece in record['qualification']['contract']['reference_pieces']}
    content += '<details><summary>Pièces de l’exemple</summary><ul>'
    for piece in record['proof_links']:
        if piece['piece_id'] == output_id:
            continue
        role = 'Référence réservée à l’évaluation' if piece['piece_id'] in references else 'Pièce fournie au modèle'
        content += '<li><span class="hint">' + role + '</span>'
        if piece['piece_id'] in record.get('proof_contents', {}):
            content += '<details class="proof-content" id="proof-' + text(eid + '-' + piece['piece_id']) + '"><summary>Lire la pièce complète : ' + text(piece['name']) + '</summary>'
            content += '<div class="proof-text">' + text(record['proof_contents'][piece['piece_id']]) + '</div></details>'
        else:
            content += ' <a href="' + text(piece['href']) + '">' + text(piece['name']) + '</a>'
        content += '</li>'
    return content + '</ul></details></section>'


def render_attempt_detail(value):
    """Page privée du détail ; la modale n'en extrait que `#attempt-detail`"""
    history = value['history']
    content = '<p><a href="' + text(value['back_href']) + '">Revenir aux résultats</a></p>'
    names = value.get('model_names', {})
    content += '<div id="attempt-detail">' + render_result(history[-1], names)
    for record in reversed(history[:-1]):
        content += '<details><summary>Ancienne évaluation, remplacée le ' + text(date_lisible_utc(record['created_at'])) + '</summary>'
        content += render_result(record, names) + '</details>'
    # Hors de `#attempt-detail` : dans la modale, la page des résultats porte déjà cette portée et cette couverture
    conclusion, coverage = value['conclusion'], value['coverage']
    content += '</div><p class="note">' + text(conclusion['attribution'] + ' ' + conclusion['limits'][0]) + '</p>'
    return content + '<p class="hint">Comparaison : réponses évaluées : ' + text(coverage['evaluated_attempts']) + ' · essais lancés : ' + text(
        coverage['attempted_cells']) + ' sur ' + text(coverage['planned_cells']) + '.</p>'


def render_campaign_records(campaigns, url):
    """Comparaisons enregistrées d'un cas d'usage, cellules et tentatives comprises"""
    content = '<p>Journal privé des comparaisons de ce cas d’usage, sur un exemple inventé. '
    content += 'Il enregistre ce que chaque appel a produit et coûté. Il ne juge pas les réponses : les verdicts figurent dans les évaluations.</p>'
    technical = {'NOT_STARTED': 'Pas lancé', 'INTENT_RECORDED': 'Lancement enregistré, appel en attente d’envoi',
                 'EMISSION_POSSIBLE': 'Appel peut-être envoyé, réponse attendue',
                 'AMBIGUOUS': 'Résultat de l’appel inconnu : pas de nouvel essai automatique', 'RECEIVED': 'Réponse et coût enregistrés'}
    for campaign in campaigns:
        task = campaign['task']
        content += '<article><h3>Comparaison ' + text(campaign['campaign_id']) + '</h3>'
        if campaign.get('recovery_of'):
            content += '<p>Reprise de l’essai ' + text(campaign['recovery_of']) + ' après un problème technique. Les réponses et coûts déjà enregistrés sont conservés.</p>'
        if 'evaluations' in campaign:
            content += '<p><a href="' + text(url) + '/campaigns/' + text(campaign['campaign_id']) + '">Voir les résultats de cette comparaison</a></p>'
        content += '<p>Exemple validé ' + text(task['version']) + ', version ' + text(task['revision']) + '.</p>'
        content += '<h4>Modèles et réglages demandés</h4>' + listing(
            f'{c["id"]} : {c["model"]}, révision {c["revision"]}, fournisseur {c["provider"]}, '
            f'accès {c["access"]}, canal {c["channel_id"]}, route {c["route"]}, effort {c["effort"]}, '
            f'paramètres {encode(c["parameters"])} ; observations exigées : {", ".join(c["required_observations"])}'
            for c in campaign['panel'])
        conditions = campaign['conditions']
        pi = conditions['pi']
        content += '<h4>Conditions communes (Pi)</h4><p>' + text(
            f'{pi["package"]} {pi["version"]} ; état {pi["status"]} ; fixé le {conditions["frozen_at"]}') + '</p>'
        content += '<details><summary>Contexte et environnement communs</summary>' + listing(
            f'{k} : {encode(conditions[k])}' for k in ('packages', 'tools', 'skills', 'defaults', 'environment')) + '</details>'
        content += '<h4>Essais et budget</h4><p>' + (
            'Essais pouvant être lancés : ' + text(', '.join(campaign['allowed_cells'])) + '.' if campaign['admission_open'] else
            'Aucun nouvel essai ne peut être lancé pour cette comparaison.') + '</p>'
        if campaign['restore_pending']:
            content += '<p>Service momentanément indisponible, réessayez plus tard.</p>'
        if campaign['stop_reason']:
            content += '<p>Motif d’arrêt : ' + text(campaign['stop_reason']) + '.</p>'
        budget = campaign['budget']
        if budget:
            content += '<p>' + text(
                f'Coûts connus à ce jour : {budget["spent"]} {budget["currency"]}. '
                f'Montants mis de côté pour des appels : {budget["reserved"]}.') + '</p>'
            if not budget.get('provider_managed'):
                content += '<p>' + text(f'Budget {budget["budget_id"]} : {budget["limit"]} {budget["currency"]}. '
                    f'Reste disponible : {budget["available"] if budget["balance_status"] == "KNOWN" else "inconnu"}.') + '</p>'
        else:
            content += '<p>Budget pas encore fixé.</p>'
        content += '<p>Montant mis de côté par essai : ' + text(
            encode(campaign['reserve_amounts']) if campaign['reserve_amounts'] else 'inconnu') + '.</p>'
        content += '<p>Mode de calcul du coût : ' + text(encode(campaign['cost_basis'])) + '.</p>'
        content += '<p>Le montant mis de côté ne garantit pas un plafond de facturation. La préparation et l’évaluation sont comptées séparément.</p>'
        content += '<h4>Essais prévus</h4>' + listing(
            f'{c["cell_id"]} : cas {c["case_id"]}, modèle {c["configuration_id"]}, {technical[c["state"]]}'
            for c in campaign['cells'])
        for attempt in campaign['attempts']:
            content += '<details><summary>Essai ' + text(attempt['operation_id']) + ' : ' + text(technical[attempt['state']]) + '</summary>'
            content += '<p>Exécution ' + text(attempt['execution_id']) + ', essai prévu ' + text(attempt['cell_id']) + '.</p>'
            content += '<p>Lancement enregistré : ' + text(attempt['created_at']) + '. Appel envoyé : ' + text(attempt['emitted_at'] or 'pas envoyé')
            content += '. Réponse reçue : ' + text(attempt['received_at'] or 'inconnue') + '.</p>'
            content += '<p>Justificatif : ' + text(attempt['receipt_id'] or 'aucun') + '. Envoi de l’appel : ' + text(attempt['emission']) + '.</p>'
            content += '<p>Réglages constatés : ' + text(encode(attempt['observed_configuration'])) + '.</p>'
            content += '<p>D’où viennent ces constats : ' + text(encode(attempt['observation_sources'])) + '.</p>'
            cost = attempt['observed_cost']
            content += '<p>Coût observé : ' + text('INCONNU' if cost is None or cost['status'] == 'UNKNOWN' else cost['amount'] + ' ' + cost['currency'])
            content += '. Source : ' + text(cost['source'] if cost else 'INCONNU') + '.</p>'
            if attempt['incident']:
                content += '<p>Incident technique : ' + text(attempt['incident']) + '.</p>'
            if attempt['attribution_incident']:
                content += '<p>Impossible de confirmer que cette réponse vient du modèle demandé : ' + text(', '.join(attempt['attribution_incident'])) + '.</p>'
            evaluations = [e for e in campaign.get('evaluations', []) if e['attempt_id'] == attempt['operation_id']]
            if evaluations:
                content += render_evaluations(evaluations, url)
            else:
                content += '<p>Cette réponse n’a pas été évaluée.</p>'
            content += '</details>'
        content += '</article>'
    if not campaigns:
        content += '<p>Aucune comparaison lancée pour ce cas d’usage.</p>'
    return '<details><summary>Historique détaillé des comparaisons</summary>' + content + '</details>'
