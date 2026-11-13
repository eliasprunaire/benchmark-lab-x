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
from urllib.parse import urlencode

from benchmark.storage import _strict_json as encode
from benchmark.preparation import INCIDENT_TEXT, NOT_SENT_TEXT, incident_text
from benchmark.storage import AMBIGUOUS_EXPIRED, AMBIGUOUS_EXPIRED_TEXT
from benchmark.evaluation import criterion_state, states

from .fragments import (FIELD_LABELS, VERDICT_BADGES, field_label, access_summary, badge, bilan, bilan_exemple, bouton, btn, cles, date_lisible_utc, disque, etat, form,
                        harnais, hidden, ic, info_modele, jour_lisible, lien, ligne_modele, listing, montant_lisible, mot, numero_version, personal_key_form,
                        puce, readable_fields, releves, ruban, section, signal, statut, table_cas, tampon, text, tiroir,
                        valeur_mesure)

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
    const retry = element('button', 'Réessayer', {type: 'button', className: 'btn btn-sec'});
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
        const item = document.createElement('li'), label = document.createElement('label');
        const input = document.createElement('input');
        const part = (className, content) => Object.assign(document.createElement('span'), {className, textContent: content});
        label.className = 'mligne';
        input.type = 'checkbox'; input.name = 'models'; input.value = model.id;
        const setting = part('regl', '');
        setting.append(part('puce', model.not_adjustable ? 'Raisonnement fixe' : 'Raisonnement réglable'));
        label.append(input, part('nom', model.name), part('fournisseur', model.id), setting);
        item.append(label);
        choices.querySelector('.mliste').insertBefore(item, choices.querySelector('.mliste').lastElementChild);
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
  // Niveau par modèle : seules les lignes des modèles cochés restent, le tiroir sans ligne se masque
  // Ce script précède le bandeau dans la page : il attend la fin de son analyse
  document.addEventListener('DOMContentLoaded', () => {
    const choices = document.getElementById('model-choices'), bar = document.querySelector('.barre-choix');
    if (!choices || !bar) return;
    const lines = Array.from(document.querySelectorAll('li[data-modele]'));
    const tuning = lines[0]?.closest('details');
    const sync = () => {
      const checked = new Set(Array.from(choices.querySelectorAll('input[name=models]:checked'), input => input.value));
      lines.forEach(line => { line.hidden = !checked.has(line.dataset.modele); });
      if (tuning) tuning.hidden = lines.every(line => line.hidden);
      // Bandeau : nombre de modèles cochés, ou le message et le bouton désactivé sous deux modèles
      const n = checked.size, minimum = n < 2, bouton = bar.querySelector('[data-choix-continuer]');
      bar.querySelector('[data-choix-nombre]').textContent = n;
      bar.querySelector('[data-choix-modeles]').textContent = n > 1 ? 'modèles' : 'modèle';
      bar.querySelector('[data-choix-synthese]').hidden = minimum;
      bar.querySelector('[data-choix-minimum]').hidden = !minimum;
      bouton.disabled = minimum;
      if (minimum) bouton.setAttribute('aria-describedby', 'choix-minimum'); else bouton.removeAttribute('aria-describedby');
    };
    choices.addEventListener('change', sync);
    sync();
  });
})();"""


def render_custom_models(value, csrf, dossier_url, *, detail=False):
    """Réglage « Modèle absent » : vérifier un identifiant OpenRouter par un appel payant, puis l'ajouter à la liste"""
    content = ('<div class="reglage" id="ajout"><div><b>Modèle absent</b><p class="petit">Un appel court et payant vérifie '
               'qu’il répond. Il ne lance pas le benchmark.</p></div><div id="custom-models" class="custom-models">')
    request = value.get('probe_request', {})
    if request.get('error'):
        content += '<p class="petit">' + text(request['error']) + '</p>'
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
            content += '<p><progress aria-label="Vérification du modèle"></progress> <a class="lien" href="' + text(
                dossier_url + '/configurations#custom-models') + '">Voir où en est la vérification</a></p>'
    content += '<div class="custom-model-forms">' + form(csrf, dossier_url + '/custom-models',
        {'action_id': secrets.token_hex(16)},
        '<div class="champ"><label for="custom-slug">Identifiant OpenRouter du modèle</label>'
        '<p class="aide" id="slug-help">Copiez l’identifiant exact du modèle depuis sa page OpenRouter, par exemple <code>openai/gpt-6-astra</code>.</p>'
        '<input id="custom-slug" name="slug" type="text" required maxlength="256" spellcheck="false" '
        'autocapitalize="none" autocomplete="off" aria-describedby="slug-help" placeholder="constructeur/modèle"></div>'
        '<div class="actions">' + bouton('Tester et ajouter', sec=True, icone='plus') + '</div><p role="status" aria-live="polite"></p>')
    content += '</div>'
    if detail:
        content += ('<p class="petit">Pour vérifier que le modèle répond, nous l’appelons une fois, brièvement, avec votre clé. '
                    'Cet appel est payant. Enregistrer ce choix n’appelle aucun modèle.</p>')
    content += '<button class="btn btn-sec" type="button" data-add-slug hidden>Ajouter un autre modèle</button></div></div>'
    return content + '<script>' + CUSTOM_MODELS_SCRIPT + '</script>'


def _readable(record, value):
    """Texte du juge avec les identifiants de critères et d'éléments remplacés par leurs descriptions

    Une description insérée dans une phrase perd son point final : le moteur place le signe final
    """
    spec = record['qualification']['contract']['specification']
    labels = {item['id']: item['description'].removesuffix('.')
              for criterion in spec['obligations'] + spec['eliminatory_errors']
              for item in [criterion] + criterion.get('elements', [])}
    if not labels:
        return value

    def remplacer(match):
        # Après « ? », « ! » ou « … », le point du moteur ferait « ?. » : la description garde son signe
        label = labels[match[1]]
        return label if label.endswith(('?', '!', '…')) else label + match[2]
    pattern = r'(?<!\w)(' + '|'.join(map(re.escape, labels)) + r')(?!\w)(\.?)'
    return re.sub(pattern, remplacer, value)


def _readable_reason(record):
    return _readable(record, record['reason'])


def _failure_reason(record):
    """Motif court d'un NE SATISFAIT PAS : les critères en défaut, entiers, éliminatoires d'abord

    Une obligation composée nomme entre parenthèses ses éléments en défaut
    """
    spec = record['qualification']['contract']['specification']
    failed = {key for key, state in _states(record).items() if state == 'FAIL'}

    def label(c):
        elements = [_insere(e['description']) for e in c.get('elements', []) if (c['id'], e['id']) in failed]
        return _insere(c['description']) + (' (' + ' ; '.join(elements) + ')' if elements else '')
    parts = []
    for criteria, one, many in ((spec['eliminatory_errors'], 'Erreur éliminatoire', 'Erreurs éliminatoires'),
                                (spec['obligations'], 'Exigence non respectée', 'Exigences non respectées')):
        named = [label(c) for c in criteria if any(cid == c['id'] for cid, _ in failed)]
        if named:
            parts.append(_fin((many if len(named) > 1 else one) + ' : ' + ' ; '.join(named)))
    return ' '.join(parts) or _readable_reason(record)


def _insere(description):
    """Description insérée dans une phrase : sans son point final ; « ? », « ! » et « … » restent"""
    return description.removesuffix('.')


def _fin(phrase):
    """Un seul signe final : aucun point après le « ? », le « ! » ou le « … » d'une description insérée"""
    return phrase if phrase.endswith(('.', '?', '!', '…')) else phrase + '.'


def render_evaluations(evaluations, dossier_url):
    """Inert evidence and recorded corrections inside the owner's existing page.

    Rendu complet des évaluations enregistrées ; `render_result` fournit la lecture compacte
    """
    content = '<h4>Verdicts et preuves</h4><p>Vérifications d’un exemple inventé, réponse par réponse. '
    content += 'Chaque verdict vaut pour le modèle tel qu’il a été réglé ici, dans les mêmes conditions que les autres. '
    content += 'Il ne dit rien du modèle en général, ni de sa compétence dans votre métier au-delà de cet exemple.</p>'
    for record in evaluations:
        eid = record['evaluation_id']
        reason = _readable_reason(record)
        label = ('À reprendre : la preuve ne permet pas de conclure' if record['verdict'] == 'INDETERMINE'
                 else VERDICT_BADGES[record['verdict']][2] if record['verdict'] in VERDICT_BADGES
                 else record['verdict'] or 'Vérification à reprendre')
        content += '<section id="evaluation-' + text(eid) + '"><h5>' + text(label) + '</h5>'
        content += '<p class="note">' + text(reason) + '</p><details><summary>Identifiants de cette vérification</summary><p>Cas ' + text(record['case_id'])
        content += ', modèle ' + text(record['configuration_id']) + ', vérification ' + text(eid) + '.</p></details>'
        content += '<p>Vérification réalisée par ' + text(record['responsible']) + ' le ' + text(record['created_at']) + '.</p>'
        previous = record['previous_evaluation_id']
        if previous:
            content += '<details><summary>Vérification précédente</summary><p>Cette vérification corrige <a href="#evaluation-' + text(previous) + '">' + text(previous) + '</a>.</p></details>'
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
                target = ('#proof-' + eid + '-' + link['piece_id']
                          if link['piece_id'] in record.get('proof_contents', {}) else link['href'])
                content += _proof(link['name'], proof['passage'], target)
            content += '</li>'
        content += '</ul><p>Pièces utilisées pour cette vérification :</p><ul>'
        for link in record['proof_links']:
            content += '<li>'
            if link['piece_id'] in record.get('proof_contents', {}):
                content += '<details class="proof-content" id="proof-' + text(eid + '-' + link['piece_id'])
                content += '"><summary>Lire la pièce complète : ' + text(link['name']) + '</summary>'
                content += '<div class="proof-text">' + text(record['proof_contents'][link['piece_id']]) + '</div>'
                content += '<p><a href="' + text(dossier_url) + '">Revenir aux résultats du benchmark avec leurs filtres</a></p></details>'
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
        for label, cost in (('Coût de la réponse du modèle', record['candidate_cost']), ('Coût de la vérification', record['judgment']['cost'])):
            content += '<p>' + label + ' : ' + text('inconnu' if cost is None or cost['status'] == 'UNKNOWN' else cost['amount'] + ' ' + cost['currency'])
            content += ' (source : ' + text(cost['source'] if cost else 'inconnue') + ').</p>'
        content += '<h6>Étalonnage du juge</h6>' + render_calibration(record['calibration'])
        content += '<p>Limites : ' + text('; '.join(record['limits'])) + '</p>'
        for label, value in (('Méthode et contrôle de l’exemple', record['qualification']),
                             ('Déroulé de la vérification : consignes, pièces lues, désaccords et arbitrages', record['judgment']),
                             ('Liens connus entre les modèles qui ont préparé, vérifié et répondu', record['configuration_links']),
                             ('Réglages demandés et constatés, avec leurs sources', {k: record[k] for k in ('requested_configuration', 'observed_configuration', 'observation_sources')}),
                             ('Ce que couvre le coût et comment il est compté', {k: record[k] for k in ('cost_basis', 'aggregation')})):
            content += '<details><summary>' + label + '</summary>' + readable_fields(value) + '</details>'
        content += '<p><a href="' + text(dossier_url) + '">' + ('Revenir aux résultats du benchmark' if '/campaigns/' in dossier_url else 'Revenir au cas d’usage') + '</a></p></section>'
    return content


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


def output_label(configuration, recoveries):
    """Limite de sortie envoyée, borne publiée connue ou non, puis limites des reprises préautorisées"""
    limit = configuration.get('parameters', {}).get('max_tokens')
    parts = ['limite non renseignée' if limit is None else str(limit) + ' jetons']
    if 'output_bound' in configuration:
        bound = configuration['output_bound']
        parts.append('borne publiée inconnue' if bound is None else 'borne publiée : ' + str(bound))
    if recoveries:
        tiers = recoveries.get(configuration['model'], [])
        parts.append('sans reprise' if not tiers else ('reprise : ' if len(tiers) == 1 else 'reprises : ')
                     + ' puis '.join(str(limit) for limit in tiers))
    return ' · '.join(parts)


# Raison de l'absence de conseil, calculée par restitution._recommendation
ABSTENTION_REASONS = {
    'incomplete': 'une partie des réponses prévues n’est pas encore vérifiée.',
    'repeated': 'un modèle compte plus d’un essai sur un même exemple.',
    'recovered': 'une reprise a été demandée pour un modèle.',
    'few': 'moins de deux réponses satisfont l’exemple.',
    'no_quality': 'aucun critère de qualité ne compare les réponses.',
    'unknown': 'la qualité ou le coût d’une réponse qui satisfait l’exemple n’est pas connu.',
    'tradeoff': 'aucune réponse n’est la meilleure sur tous les critères de qualité.',
    'tie': 'plusieurs réponses ont la même qualité et le même coût.',
    'cost_tie': 'plusieurs réponses conformes ont le coût observé le plus bas.',
    'criterion_tie': 'plusieurs réponses ont la meilleure valeur sur le critère choisi et le même coût observé.',
    'disagreement': 'les exemples ne désignent pas le même modèle.',
}


def model_name(configuration, names):
    """Nom commercial du relevé, sinon l'identifiant Openrouter : le même libellé sur chaque écran"""
    return names.get(configuration['model'], configuration['model'])


def priority_label(value, priority):
    """Libellé lisible de la priorité retenue : coût observé ou critère du contrat"""
    if priority == 'cost':
        return 'coût observé'
    column = next((column for column in value['columns'] if column['id'] == priority), None)
    return (column['definition'].get('measure') or column['definition'].get('label') or priority
            if column else priority)


def priority_href(value, query):
    """Retour au conseil par défaut en gardant les autres filtres"""
    params = {key: item for key, item in query.items() if key != 'priority'}
    return value['href'] + ('?' + urlencode(params) if params else '') + '#filters'


def tradeoff_sentence(item, names, panel):
    """Compromis explicite : la réponse retenue est moins bonne qu'une autre réponse conforme

    Deux configurations du même modèle se distinguent par le candidat entier, effort compris
    """
    configuration = item['configuration']
    label = (candidate_label(configuration, names)
             if sum(other['model'] == configuration['model'] for other in panel.values()) > 1
             else model_name(configuration, names))
    return ('Compromis sur « ' + item['measure'] + ' » : la réponse retenue vaut '
            + valeur_mesure(item['value'], item.get('unit')) + ', alors que '
            + label + ' vaut '
            + valeur_mesure(item['best'], item.get('best_unit')) + '.')


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
    'judgment': ('Vérification des réponses', 'Pour information, hors conseil.'),
    'probes': ('Vérification des modèles choisis', 'Pour information, hors conseil.'),
    'preparation': ('Préparation de l’exemple', 'Pour information, hors conseil. '
                    'Elle sert aussi aux autres benchmarks de cet exemple.'),
    'qualification': ('Contrôle de l’exemple', 'Pour information, hors conseil. '
                      'Il sert aussi aux autres benchmarks de cet exemple.'),
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
    summary = ('Coût complet connu : ' if expenses['complete'] else 'Coût complet inconnu · sous-total connu : ') + known
    content = ''
    if not expenses['complete']:
        content += '<p>Au moins une dépense manque : le sous-total ne donne que la somme des dépenses connues, pas le coût complet.'
        awaiting = expenses.get('awaiting_judgment', 0)
        if awaiting:
            content += text(' La vérification de ' + _plural(awaiting, 'réponse') + ' est à venir : son coût n’est pas encore connu.')
        content += '</p>'
    content += ('<p>Chaque envoi est compté une fois : réponses des modèles et leurs reprises, vérification des réponses, '
                'test des modèles ajoutés, préparation et contrôle de l’exemple jusqu’à cette version. '
                'Les montants réservés et estimés sont indiqués à part et ne s’ajoutent pas.</p>')
    for group in expenses['groups']:
        title, note = EXPENSE_GROUPS[group['key']]
        subtotal = montant_lisible(group['known']) + ' ' + expenses['unit']
        content += '<h3>' + text(title + ' : ' + (subtotal if group['complete'] else 'inconnu, sous-total connu ' + subtotal)) + '</h3>'
        content += '<p class="petit">' + text(note) + '</p><ul>'
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
            if item['state'] == 'STOPPED' and not item['recovery']:
                details.append('non envoyé : le benchmark s’est arrêté avant cet essai')
            elif item['state'] in EXPENSE_STATES:
                details.append(EXPENSE_STATES[item['state']])
            if item['recovery'] and item['state'] != 'STOPPED':
                details.append('relance après incident fournisseur' if item['recovery'] == 'INCIDENT'
                               else 'reprise après arrêt pour longueur')
            if item['reserved'] is not None:
                details.append('montant réservé : ' + montant_lisible(item['reserved']) + ' ' + item['reserved_unit'])
            if item['estimate'] is not None:
                details.append('estimation indicative : ' + montant_lisible(item['estimate']) + ' USD')
            content += '<li>' + text((model_name({'model': item['model']}, names) if item['model'] else 'Modèle non renseigné')
                                     + ' : ' + amount) + '<details><summary>Source et référence</summary><p>'
            content += text(' · '.join(details)) + ' · réf. <code>' + text(item['operation_id'][-8:]) + '</code></p></details></li>'
        content += '</ul>'
    return tiroir(summary, content, ident='expenses')


def _tampons_compte(records):
    """Tampons comptés d'un exemple : « 3 satisfont », « 1 ne satisfait pas », « 2 à reprendre »"""
    tampons = ''
    for verdict, ton, one, many in (('SATISFAIT', 'ok', 'satisfait', 'satisfont'),
                                    ('NE SATISFAIT PAS', 'ko', 'ne satisfait pas', 'ne satisfont pas'),
                                    (None, 'att', 'à reprendre', 'à reprendre')):
        count = sum(record['decision']['verdict'] == verdict for record in records)
        if count:
            tampons += tampon(ton, str(count) + ' ' + (one if count == 1 else many))
    return tampons


def _filters_summary(value, query):
    """Ce que le tiroir « Tri et filtres » annonce replié : la priorité, le nombre de filtres, le tri"""
    parts = []
    if query.get('priority'):
        parts.append('Priorité : ' + priority_label(value, query['priority']))
    filters = [key for key in query if key in ('case', 'configuration', 'verdict', 'obligation')]
    if filters:
        parts.append(_plural(len(filters), 'filtre'))
    if query.get('sort'):
        parts.append('tri choisi')
    return ', '.join(parts) or 'Aucun filtre'


def _advice(value, names, panel, number, query):
    """Le conseil, ou son absence motivée, sous forme de ticket ; puis le compromis de la priorité choisie"""
    abstention, choice = value.get('abstention'), value.get('recommendation')
    empty = ''
    if abstention:
        reason = ABSTENTION_REASONS[abstention['reason']]
        sentence = ('Exemple ' + str(number(abstention['case_id'])) + ' : ' + reason if abstention['case_id']
                    else reason[0].upper() + reason[1:])
        marker = mot('Incomplet', 'vig') if abstention['reason'] == 'incomplete' else mot('Sans conseil', 'inc')
        content = ('<section class="ticket ticket-vide" aria-labelledby="no-choice-title"><div><span class="etiq">Conseil</span>'
                   '<h2 id="no-choice-title">Pas de conseil</h2><div class="contexte">' + marker + '</div></div>'
                   '<div class="pourquoi"><div><p>' + text(sentence) + '</p>')
        if abstention.get('choices'):
            content += '<p>' + text(' · '.join('Exemple ' + str(number(item['case_id'])) + ' : '
                                                + candidate_label(item['configuration'], names)
                                                for item in abstention['choices'])) + '</p>'
        if query.get('priority'):
            content += '<p>Priorité choisie : <strong>' + text(priority_label(value, query['priority'])) + '</strong>.</p>'
        content += '</div>'
        if query.get('priority'):
            content += lien(priority_href(value, query), 'Revenir au conseil par défaut', 'retour')
        elif abstention['reason'] == 'incomplete':
            content += lien(value['href'] + '/conditions', 'Voir l’avancement', 'actualiser')
        empty = content + '</section>'
    if not choice or not value['population']:
        return empty, ''
    # Décision D06 d'Ayo (#458) : un coût par exemple, jamais leur somme
    costs = ([('', montant_lisible(choice['amount']), choice['unit'])] if 'cases' not in choice else
             [('Exemple ' + str(number(item['case_id'])) + ' · ', montant_lisible(item['amount']), item['unit']) for item in choice['cases']])
    priority = choice.get('priority')
    kicker = 'Notre conseil' + (' · priorité : ' + priority_label(value, priority) if priority else '')
    content = ('<section class="ticket" aria-labelledby="economic-choice-title"><div><span class="etiq">' + text(kicker) + '</span>'
              '<h2 id="economic-choice-title">' + text(model_name(choice['configuration'], names)) + '</h2>'
              '<div class="contexte">' + puce(effort_label(choice['configuration']))
              + (puce('Priorité choisie : ' + priority_label(value, priority), icone='stylo') if priority else '') + '</div></div>'
              '<div class="cout">' + ''.join('<span class="etiq">' + text(prefix) + 'Coût observé</span><p class="val">' + text(amount)
                                             + '<small>' + text(unit) + '</small></p>' for prefix, amount, unit in costs) + '</div>')
    compromises = []
    if priority:
        compromises = [tradeoff_sentence(item, names, panel) for item in choice.get('compromises', [])]
        if 'cases' in choice:
            compromises += ['Exemple ' + str(number(item['case_id'])) + ' : ' + tradeoff_sentence(trade, names, panel)
                            for item in choice['cases'] for trade in item.get('compromises', [])]
        if priority == 'cost':
            why = ('<strong>Priorité choisie : coût observé.</strong> Nous retenons l’unique réponse conforme la moins coûteuse. '
                   'Une autre réponse peut être meilleure sur un critère de qualité.')
        else:
            why = ('<strong>Priorité choisie : ' + text(priority_label(value, priority)) + '.</strong> '
                   'Nous retenons la meilleure valeur observée sur ce critère, selon le sens fixé par le contrat. '
                   'En cas d’égalité, le coût observé le plus bas départage.')
    elif choice['basis'] in ('quality_then_cost', 'each_case'):
        why = ('Nous retenons d’abord la qualité observée la plus élevée. À qualité égale, le coût observé le plus bas l’emporte.'
               + (' Ce modèle est retenu sur chaque exemple, lu séparément.' if 'cases' in choice else ''))
    else:
        why = ('Les ' + text(choice['count']) + ' réponses qui satisfont l’exemple ont la même qualité observée. '
               'Nous retenons la moins coûteuse.')
    if 'cases' in choice:
        links = ''.join(lien(item['detail_href'], 'Voir sa réponse · exemple ' + str(number(item['case_id'])))
                        for item in choice['cases'] if item.get('detail_href'))
    else:
        links = lien(choice['detail_href'], 'Voir sa réponse') if choice.get('detail_href') else ''
    if compromises:
        links += lien('#t-compromis', 'Voir les compromis')
    elif priority:
        links += lien(priority_href(value, query), 'Revenir au conseil par défaut', 'retour')
    content += '<div class="pourquoi"><p>' + why + '</p>' + links + '</div></section>'
    scope = ('Ce conseil repose sur un seul exemple.' if 'cases' not in choice else
             'Ce conseil repose sur ' + str(len(choice['cases'])) + ' exemples.')
    content += '<p class="petit">' + scope + ' Sur d’autres cas d’usage, le résultat peut changer.</p>'
    tradeoff = ''
    if compromises:
        tradeoff = ('<section class="g-compromis" aria-labelledby="t-compromis"><div class="zone-titre"><h2 id="t-compromis">'
                    'Ce que cette priorité sacrifie</h2></div><ul>'
                    + ''.join('<li>' + mot('Compromis', 'vig') + '<span>' + text(item) + '</span></li>' for item in compromises)
                    + '</ul><p class="petit">Ce conseil ne compare que des réponses qui satisfont toutes les obligations : '
                    'aucune obligation non satisfaite n’est compensée.</p><div class="actions">'
                    + lien(priority_href(value, query), 'Revenir au conseil par défaut', 'retour') + '</div></section>')
    return empty + content, tradeoff


def render_comparison(value):
    base, query = value['href'], value['filter_scope']
    names = value.get('model_names', {})
    multiple_cases = len(value['cases']) > 1
    panel = {configuration['id']: configuration for configuration in value.get('panel', [])}
    repetitions = max((cell.get('repetition', 1) for cell in value['cells']), default=1)
    latest = {record['attempt_id']: record for record in value['history']}
    coverage = value['coverage']

    def number(case_id):
        return next(n for n, case in enumerate(value['cases'], 1) if case['id'] == case_id)

    # Le conseil, ou son absence motivée, se lit avant le bilan (critère K1 du diagnostic)
    advice, tradeoff = _advice(value, names, panel, number, query)

    def pending_reason(item):
        # Le modèle sans réponse exploitable est nommé comme dans le tableau, avec la cause lue dans son reçu
        configuration = panel.get(item.get('configuration_id'))
        if item['state'] not in ('NO_USABLE_RESPONSE', 'NOT_SENT') or configuration is None:
            return item['next_action']
        case = next((v for v in value['cases'] if v['id'] == item.get('case_id')), None) if multiple_cases else None
        prefix = example_label(case, value['cases'].index(case) + 1) + ' : ' if case else ''
        attempt = ', essai ' + str(item['repetition']) if item.get('repetition') else ''
        if item['state'] == 'NOT_SENT':
            return (prefix + model_name(configuration, names) + (attempt + ',' if attempt else '')
                    + ' n’a pas été appelé : le benchmark s’est arrêté '
                    'avant cet essai. Il n’est pas vérifié et n’entre pas dans le benchmark.')
        count = item.get('recoveries', 0)
        # Une cause que le reçu n'établit pas est dite inconnue, jamais devinée
        details = [item.get('cause') or 'cause non établie par le reçu'] + (
            [str(item['attempts']) + ' tentatives'] if item.get('attempts', 0) > 1 else
            [str(count) + (' reprises' if count > 1 else ' reprise')] if count else []) + (
            ['reprise suivante non tentée : ' + item['declined']] if item.get('declined') else [])
        return (prefix + 'Aucune réponse exploitable pour ' + model_name(configuration, names)
                + (' · ' + effort_label(configuration) if sum(c['model'] == configuration['model'] for c in panel.values()) > 1 else '')
                + attempt + ' (' + ' ; '.join(details) + ')'
                + '. ' + ('Cet essai' if attempt else 'Ce modèle') + ' n’est pas vérifié et n’entre pas dans le benchmark.')
    pending_reasons = list(dict.fromkeys('<li>' + mot('Sans réponse', 'vig') + '<div><p>' + text(pending_reason(item)) + '</p>'
                                         + receipt_observations(item) + '</div></li>'
                                         for item in value.get('pending_attempts', [])))
    # Un modèle sans réponse exploitable est terminé : il ne fait pas dire que les essais se sont arrêtés
    open_pending = [item for item in value.get('pending_attempts', []) if item['state'] not in ('NO_USABLE_RESPONSE', 'NOT_SENT')]
    examples = ''
    for case_number, case in enumerate(value['cases'], 1):
        records = [record for record in latest.values() if record['case_id'] == case['id']]
        if records:
            examples += bilan_exemple(example_label(case, case_number),
                                      ' <span class="petit">' + text(_plural(len(records), 'réponse')) + '</span>', _tampons_compte(records))
    signals = ''.join(pending_reasons)
    if multiple_cases or repetitions > 1:
        # Décision d'Ayo du 2026-10-16 : tout résultat sur plusieurs exemples ou plusieurs essais annonce cette limite
        signals += signal(mot('Juge', 'vig'), 'Le juge automatique n’est pas qualifié : ses verdicts n’ont pas été comparés '
                          'à des jugements humains.'
                          + (' Chaque verdict vaut pour un exemple ; les exemples ne s’additionnent pas.' if multiple_cases else '')
                          + (' Chaque essai est affiché séparément ; leur nombre ne mesure pas une fiabilité.' if repetitions > 1 else ''))
    if value.get('stop_reason') and (coverage['not_started'] or open_pending):
        signals += signal(mot('Incomplet', 'vig'), 'Benchmark incomplet : les essais se sont arrêtés avant la fin. '
                          'Vous pouvez lire les réponses déjà reçues.')
    if value['economic_status'] != 'COMPLETE':
        signals += signal(mot('Coûts', 'inc'), 'Comparaison des coûts incomplète : certains coûts observés manquent ou ne sont pas comparables.')
    if not latest:
        received = any(cell['state'] == 'RECEIVED' for cell in value['cells'])
        signals = signal(mot('Verdicts', 'att'), 'Des réponses sont arrivées. Leur verdict n’est pas encore disponible.' if received else
                         'Aucune réponse n’a encore été vérifiée. Le suivi indique où en est le benchmark.') + signals
    dates = sorted(set(date[:10] for date in value.get('acquisition_dates', [])))
    subtitle = (str(coverage['attempted_cells']) + (' essai lancé' if coverage['attempted_cells'] == 1 else ' essais lancés')
                + ' sur ' + str(coverage['planned_cells']))
    if dates:
        subtitle += ' · ' + ('reçues le ' + jour_lisible(dates[0]) if len(dates) == 1 else
                             'reçues du ' + jour_lisible(dates[0]) + ' au ' + jour_lisible(dates[-1]))
    evaluated = coverage['evaluated_attempts']
    summary = bilan('Aucune réponse vérifiée' if not evaluated else _plural(evaluated, 'réponse') + ' vérifiée' + ('s' if evaluated > 1 else ''),
                    subtitle, examples, signals)
    content = '<div class="pile-l">' + advice + tradeoff + summary
    if not value['population']:
        return content + render_expenses(value.get('expenses'), names) + '</div>'
    sort_column = next((column for column in value['columns'] if column['id'] == query.get('sort')), None)
    sort_label = (sort_column['definition'].get('measure', 'Coût observé') if sort_column else 'sans tri')
    quality_columns = [column for column in value['columns'] if 'criterion_id' in column]

    def field(key, label, default, choices, selected, extra=''):
        return ('<div class="champ"><label for="filter-' + key + '">' + text(label) + '</label><select id="filter-' + key
                + '" name="' + key + '"' + extra + '>'
                + ''.join('<option value="' + text(val) + '"' + (' selected' if selected == val else '') + '>' + text(title) + '</option>'
                          for val, title in [('', default)] + choices) + '</select></div>')
    controls = ''
    if value['columns']:
        controls += field('priority', 'Priorité du conseil', 'Conseil par défaut',
                          [('cost', 'Priorité au coût observé')] + [(column['id'], 'Priorité : ' + priority_label(value, column['id']))
                                                                     for column in quality_columns],
                          query.get('priority', ''), ' aria-describedby="priority-help"')
    options = {
        'case': ('Exemple', 'Tous les exemples', [(v['id'], example_label(v, n)) for n, v in enumerate(value['cases'], 1)] if multiple_cases else []),
        'configuration': ('Modèle', 'Tous les modèles', [(v['id'], model_name(v, names) +
                           (' · ' + effort_label(v) if sum(p['model'] == v['model'] for p in value['panel']) > 1 else '')) for v in value['panel']]),
        'verdict': ('Résultat', 'Tous les résultats', [('SATISFAIT', 'Satisfait'), ('NE SATISFAIT PAS', 'Ne satisfait pas'), ('A_REPRENDRE', 'À reprendre')]),
        'sort': ('Trier par', 'Sans tri', [(v['id'], v['definition'].get('measure', 'Coût observé')) for v in value['columns']]),
        'direction': ('Sens du tri', 'Croissant', [('desc', 'Décroissant')]),
        'obligation': ('Exigence à examiner', 'Toutes les exigences', [(v['id'] + ':' + state, short_label(v['description']) + ' : ' + label)
                        for v in value['obligations'] for state, label in
                        (('PASS', 'Respectée'), ('FAIL', 'Non respectée'), ('INDETERMINE', 'Non vérifiable'))]),
    }
    for key, (label, default, choices) in options.items():
        if choices:
            selected = query.get(key, '')
            controls += field(key, label, default, choices, '' if key == 'direction' and selected == 'asc' else selected)
    form_html = ('<form method="get" action="' + text(base) + '#filters"><div class="filtres-grille">' + controls + '</div>'
                 + ('<p class="petit" id="priority-help">Changer la priorité ne modifie ni les verdicts ni les coûts. Le conseil porte sur '
                    'tout le benchmark, même si un filtre masque la réponse conseillée.</p>' if value['columns'] else '')
                 + '<div class="actions">' + bouton('Appliquer', sec=True, icone=None) + lien(base + '#filters', 'Retirer les filtres', 'croix')
                 + '</div></form>')
    shown = len(value['rows'])
    scope = (_plural(shown, 'réponse') + (' affichée' if shown == 1 else ' affichées') + ' sur ' + str(len(value['population']))
             + ' · ' + ('triées par ' + sort_label[:1].lower() + sort_label[1:] if sort_column else sort_label)
             + ((', ordre décroissant' if query.get('direction') == 'desc' else ', ordre croissant') if sort_column else ''))
    content += ('<section aria-labelledby="t-comp"><div class="zone-titre"><h2 id="t-comp">Comparaison des modèles</h2><span class="petit">'
                + text(scope) + '</span></div><div class="filtres">'
                + tiroir('Tri et filtres', form_html, resume=_filters_summary(value, query), ouvert=bool(query), ident='filters') + '</div>')
    if not value['rows']:
        content += '<p class="etat-ligne">Aucune réponse ne correspond à ces filtres. Retirez un filtre pour revoir les autres : rien n’a été supprimé.</p>'
    for case_number, case in enumerate(value['cases'], 1):
        rows = [r for r in value['rows'] if r['case_id'] == case['id']]
        if not rows and (not multiple_cases or query.get('case', case['id']) != case['id']):
            continue
        label = example_label(case, case_number)
        # Un filtre ne retire ni l'exemple ni son dénominateur
        evaluated = len({r['attempt_id'] for r in latest.values() if r['case_id'] == case['id']})
        planned = sum(cell['case_id'] == case['id'] for cell in value['cells'])
        denominator = _plural(evaluated, 'réponse vérifiée') + ' sur ' + _plural(planned, 'essai prévu')
        if not rows:
            # Sans filtre, le dénominateur suffit ; sans aucune réponse affichée, la note générale le dit déjà
            if query and value['rows']:
                content += ('<div class="cas-titre"><h3>' + text(label) + '</h3><span class="petit">' + text(denominator) + '</span></div>'
                            '<p class="etat-ligne">Aucune réponse de cet exemple ne correspond à ces filtres.</p>')
            continue
        lines = []
        for row in rows:
            configuration = row['requested_configuration']
            hints = ''
            if row.get('repetition'):
                hints += '<p class="petit">' + text('Essai ' + str(row['repetition']) + ' sur ' + str(repetitions)) + '</p>'
            if row.get('relaunch'):
                hints += '<p class="petit">' + text('Réponse obtenue à la tentative ' + str(row['relaunch'])
                                                    + ', après un incident fournisseur') + '</p>'
            if row.get('recovery_limit'):
                hints += '<p class="petit">' + text('Reprise après arrêt pour longueur : longueur de réponse de '
                                                    + str(row['recovery_limit']) + ' jetons') + '</p>'
            hints += receipt_observations(row)
            # Les critères qui fondent le verdict, entiers ; l'explication du juge reste dans le détail
            reason = ('Toutes les exigences sont respectées.' if row['verdict'] == 'SATISFAIT' else
                      _failure_reason(row) if row['verdict'] == 'NE SATISFAIT PAS' else 'Le verdict n’est pas encore disponible.')
            measures = ''
            if quality_columns:
                measures = '<ul class="mesures">'
                for column in quality_columns:
                    measure = next(m for m in row['measures'] if m['criterion_id'] == column['criterion_id'])
                    shown_value = valeur_mesure(measure['value'], measure.get('unit')).capitalize()
                    measures += '<li><b>' + text(column['definition']['measure']) + '</b> : ' + text(shown_value)
                    if measure['rank'] is None:
                        measures += ' <span class="nc">non comparable : ' + text(measure['reason']) + '</span>'
                    measures += '</li>'
                measures += '</ul>'
            cost = row['cost']
            cost_html = text('inconnu' if cost['value'] is None else montant_lisible(cost['value']) + ' ' + cost['unit'])
            if cost['value'] is not None and cost['rank'] is None:
                cost_html += ' <span class="nc">non comparable</span>'
            name = model_name(configuration, names)
            link = ('<span class="actions"><a class="lien" data-result href="' + text(row['detail_href']) + '">Preuves<span class="cache"> et détail de '
                    + text(name) + '</span>' + ic('fleche') + '</a>'
                    + info_modele(name, [('Identifiant', configuration['model']), ('Raisonnement', effort_label(configuration)),
                                         ('Longueur de réponse', output_label(configuration, None))]) + '</span>')
            lines.append(ligne_modele('attempt-' + row['attempt_id'], name, effort_label(configuration),
                                      badge(row['verdict']) + '<p class="motif">' + text(reason) + '</p>', measures, cost_html, link,
                                      sous_nom_html=hints, classe='' if row['verdict'] == 'SATISFAIT' else 'out', attrs=' tabindex="-1"'))
        content += table_cas(label, denominator, lines, region=label if multiple_cases else 'Comparaison des modèles')
    content += '</section>'
    conditions = value['conditions']
    # La qualité observée désigne les critères du contrat : leur liste se lit ici, sans survol
    definitions = (value.get('recommendation') or {}).get('quality') or [column['definition'] for column in quality_columns]
    criteria = list(dict.fromkeys(definition.get('measure') or definition.get('label') for definition in definitions
                                  if definition.get('measure') or definition.get('label')))
    method = ('<p>Résultat attendu : ' + text(value['result_expected']) + '</p>'
              '<ul><li><strong>Satisfait</strong> : toutes les exigences sont respectées et aucune erreur éliminatoire n’a été relevée.</li>'
              '<li><strong>Ne satisfait pas</strong> : une exigence n’est pas respectée ou une erreur éliminatoire a été relevée ; la réponse n’est pas utilisable, quel que soit son coût.</li>'
              '<li><strong>À reprendre</strong> : la preuve ne permet pas encore de conclure ; ce n’est pas un échec du modèle.</li>'
              '<li>Lisez d’abord le verdict, puis la qualité observée'
              + (' : elle désigne les critères fixés pour votre cas d’usage et vérifiés dans chaque réponse (' + text(' ; '.join(criteria)) + ')'
                 if criteria else '')
              + '. Le coût observé sert seulement à départager des réponses qui satisfont l’exemple avec la même qualité. Un coût inconnu ne change pas le verdict.</li>'
              '<li>' + text(value['conclusion']['attribution']) + '</li>'
              + ('<li>Limite principale : ' + text(value['conclusion']['limits'][0]) + '</li>' if value['conclusion']['limits'] else '')
              + '<li>Conditions communes : tous les modèles reçoivent la même consigne et les mêmes pièces, et travaillent avec le même harnais, '
              'pi.dev, dans la même version pour tout le benchmark, fixée le ' + text(date_lisible_utc(conditions['frozen_at'])) + '.</li></ul>')
    content += '<div>' + render_expenses(value.get('expenses'), names) + tiroir('Comment lire ces résultats', method, ident='method') + '</div>'
    content += '<p>' + lien(base + '/preview', 'Voir l’aperçu d’une publication', 'oeil') + '</p>'
    content += ('<dialog id="result-dialog" aria-label="Détail et preuves"><div class="result-head">'
                '<button type="button" class="btn btn-sec" data-close autofocus>Fermer</button>'
                '<p class="result-status" role="status"></p></div><div class="result-body"></div></dialog>')
    return content + '</div>'


def niveau_court(niveau):
    """« Faible (low) » devient « Faible » ; un niveau inconnu garde son code"""
    return str(EFFORT_LABELS.get(niveau, niveau)).split(' (')[0]


def lignes_raisonnement(configuration):
    """Réglage de raisonnement en une ou deux lignes : le niveau appliqué, puis la raison d'un écart avec le niveau demandé"""
    if configuration.get('effort_limit') == 'not_adjustable':
        return ['Raisonnement fixe']
    effort = configuration.get('parameters', {}).get('reasoning', {}).get('effort', configuration.get('effort', 'off'))
    if effort == 'off':
        return ['Raisonnement non renseigné']
    if effort == 'on':
        return ['Raisonnement activé', 'Niveau non renseigné']
    niveau = str(EFFORT_LABELS.get(effort, effort))
    lignes = ['Raisonnement ' + niveau[:1].lower() + niveau[1:]]
    requested = configuration.get('effort_requested')
    if requested:
        lignes.append(niveau_court(requested) + ' non accepté par ce modèle')
    return lignes


def contexte_campagne(*, retour, revision=None, besoin=None, extra=''):
    """Puces sous le titre h1 des écrans modèles, lancement et suivi ; `retour` est (texte, adresse) ou None"""
    return ((puce(besoin, plein=True) if besoin else '') + (puce('Version ' + str(revision)) if revision else '')
            + ruban('Exemple inventé') + extra + (lien(retour[1], retour[0], 'retour') if retour else ''))


def contexte_vue(value):
    """Contexte du titre pour les vues configurations, campaign_launch et campaign_models"""
    kind = value.get('kind')
    if kind == 'campaign_models':
        return contexte_campagne(retour=None, besoin=value.get('need'), extra=puce(
            'Exemple validé · v' + str(value['task']['version'])) + puce(_plural(len(value['panel']), 'modèle')))
    dossier_url = '/preparation/dossiers/' + value['dossier_id']
    if kind == 'configurations':
        return contexte_campagne(retour=('Revenir au cas d’usage', dossier_url))
    campaign = value['campaign']
    numero = numero_version(value, campaign.get('task', {}).get('revision'))
    if campaign['attempts']:
        return contexte_campagne(retour=None, revision=numero)
    if 'checks' in value:
        return contexte_campagne(retour=('Modifier la sélection', dossier_url + '/configurations'), revision=numero)
    return contexte_campagne(retour=('Revenir au cas d’usage', dossier_url), revision=numero)


def titre_vue(value):
    """Titre h1 des vues configurations, campaign_launch et campaign_models"""
    kind = value.get('kind')
    if kind == 'configurations':
        return 'Choisissez les modèles à comparer'
    if kind == 'campaign_models':
        return 'Modèles de ce benchmark'
    return 'Suivi du benchmark' if value['campaign']['attempts'] else 'Vérifier, puis lancer'


def render_campaign_models(value):
    names = value.get('model_names', {})
    cards = ''
    for model in value['panel']:
        parameters = model['parameters']
        rows = [('Route', text(model['model']))]
        if model.get('effort_requested'):
            rows += [('Raisonnement demandé', text(model['effort_requested'])), ('Raisonnement appliqué', text(model['effort']))]
        elif model.get('effort_limit') == 'not_adjustable':
            rows.append(('Raisonnement', 'non réglable'))
        else:
            rows.append(('Raisonnement', text(model['effort'])))
        rows.append(('Longueur de réponse', text(str(parameters['max_tokens']) + ' jetons') if 'max_tokens' in parameters else 'par défaut'))
        rest = {key: item for key, item in parameters.items() if key not in ('max_tokens', 'reasoning')}
        rows += [(field_label(key), readable_fields(item)) for key, item in rest.items()]
        cards += ('<li class="carte"><h3>' + text(model_name(model, names)) + '</h3><p class="petit">'
                  + '<br>'.join(text(line) for line in lignes_raisonnement(model)) + '</p>'
                  + tiroir('Réglages utilisés', cles(rows)) + '</li>')
    return ('<div class="pile-l"><p class="texte-l">Voici les modèles et les réglages utilisés. Consulter cette page n’appelle aucun modèle.</p>'
            '<ul class="g-liste-modeles">' + cards + '</ul><div class="actions">' + btn(value['href'], 'Revenir aux résultats') + '</div>'
            '<p class="petit">Pour changer les modèles ou l’exemple, préparez un nouveau benchmark depuis le cas d’usage. '
            'Les résultats actuels sont conservés.</p></div>')


def cadre_haut(html):
    """Encadré d'état posé au-dessus du contenu d'un écran ; rien s'il n'y a pas d'état à dire"""
    return '<div class="g-haut">' + html + '</div>' if html else ''


def _segments(identifiant, nom, choix, courant):
    """Contrôle segmenté de radios natifs liés au formulaire des modèles ; `choix` : (valeur, libellé, code ou '')"""
    return ('<div class="segments" role="radiogroup" aria-labelledby="' + identifiant + '">' + ''.join(
        '<label><input type="radio" form="configurations-form" name="' + nom + '" value="' + text(valeur) + '"'
        + (' checked' if str(courant) == str(valeur) else '') + '>' + text(libelle)
        + ('<small>' + text(code) + '</small>' if code else '') + '</label>' for valeur, libelle, code in choix) + '</div>')


def render_configurations(value, csrf):
    """Choix des modèles, du niveau de raisonnement, des essais et de la longueur de réponse"""
    dossier_url = '/preparation/dossiers/' + value['dossier_id']
    if not value.get('catalogue_available', True):
        return (cadre_haut(etat('vig', 'Indisponible', value['detail'],
                                'Aucun modèle à choisir pour le moment. Ajoutez-en un par son identifiant OpenRouter : le benchmark en demande au moins deux.',
                                lien(dossier_url + '/configurations', 'Actualiser la liste', 'actualiser')))
                + '<div class="reglages">' + render_custom_models(value, csrf, dossier_url, detail=True) + '</div>')
    models = value['models']
    if not models:
        # Liste présente sans modèle utilisable : rendue comme sans liste, un seul encadré qui dit aussi son ancienneté
        mot, phrase = (('Liste ancienne', 'La liste des modèles n’a pas pu être mise à jour depuis plus d’un jour. Actualisez la liste plus tard, '
                        'ou ajoutez un modèle par son identifiant OpenRouter : le benchmark en demande au moins deux.')
                       if value.get('catalogue_stale') else
                       ('Liste vide', 'La liste des modèles est mise à jour une fois par jour. D’ici là, ajoutez un modèle par son identifiant '
                        'OpenRouter : le benchmark en demande au moins deux.'))
        return (cadre_haut(etat('vig', mot, 'Aucun modèle de la liste n’est utilisable pour le moment', phrase,
                                lien(dossier_url + '/configurations', 'Actualiser la liste', 'actualiser')))
                + '<div class="reglages">' + render_custom_models(value, csrf, dossier_url, detail=True) + '</div>')
    chosen = [model for model in models if model['selected']]
    head = ''
    if value.get('catalogue_stale'):
        head = etat('vig', 'Liste ancienne', 'La liste des modèles n’a pas pu être mise à jour',
                    'Vous voyez la dernière liste disponible, qui peut ne plus être à jour.')
    elif value.get('current_campaign_id') and chosen:
        head = etat('ok', 'Sélection prête', _plural(len(chosen), 'modèle sélectionné'),
                    'Vous pouvez ajuster le raisonnement, les essais et la longueur de réponse, puis continuer. '
                    'Enregistrer ce choix n’appelle aucun modèle.')
    tiles = ''
    for model in models:
        tiles += ('<li><label class="mligne"><input type="checkbox" name="models" value="' + text(model['id']) + '"'
                  + (' checked' if model['selected'] else '') + '><span class="nom">' + text(model['name'])
                  + '</span><span class="fournisseur">' + text(model['id']) + '</span><span class="regl">'
                  + puce('Raisonnement fixe' if model['not_adjustable'] else 'Raisonnement réglable') + '</span></label></li>')
    tiles += ('<li><a class="mligne mligne-ajout" href="#ajout">' + ic('plus') + '<span>Ajouter un modèle absent de la liste</span></a></li>')
    choices = ('<form id="configurations-form" method="post" action="' + text(dossier_url + '/configurations') + '">' + hidden('csrf_token', csrf)
               + '<fieldset id="model-choices"><legend class="cache">Modèles à comparer</legend>'
               + ('' if head else '<p class="texte-l">Au moins deux. Enregistrer ce choix n’appelle aucun modèle.</p>')
               + '<ul class="mliste">' + tiles + '</ul></fieldset></form>')
    tiers = value['available_tiers']
    current = value['current_tier']
    tunable = [model for model in models if len(model.get('levels', ())) > 1]
    fixed = [model for model in chosen if model['not_adjustable']]
    modified = sum(bool(model.get('chosen')) for model in tunable)
    per_model = ''.join(
        '<li data-modele="' + text(model['id']) + '"><label for="effort-' + str(index) + '">' + text(model['name']) + '</label><select id="effort-' + str(index)
        + '" form="configurations-form" name="' + text('effort:' + model['id']) + '"><option value="">Comme ci-dessus ('
        + text(niveau_court(current)) + ')</option>' + ''.join(
            '<option value="' + text(level) + '"' + (' selected' if model.get('chosen') == level else '') + '>'
            + text(EFFORT_LABELS.get(level, level)) + '</option>' for level in model['levels']) + '</select></li>'
        for index, model in enumerate(tunable, 1))
    per_model += ''.join('<li data-modele="' + text(model['id']) + '"><span>' + text(model['name']) + '</span>' + puce('Non réglable : comparé tel quel') + '</li>' for model in fixed)
    reasoning = ('<div class="reglage"><div><span class="lbl" id="l-tier"><b>Raisonnement</b></span>'
                 '<p class="petit">Le temps de réflexion accordé au modèle avant qu’il réponde.</p></div><div>'
                 + _segments('l-tier', 'tier', [(tier, niveau_court(tier), tier) for tier in tiers], current)
                 + '<p class="petit">Plus élevé : souvent plus lent et plus cher, sans garantie d’être meilleur. '
                 'Un modèle qui n’a pas ce niveau reçoit le plus proche, affiché dans le récapitulatif. '
                 'Les modèles à niveau fixe sont comparés tels quels.</p>'
                 + (tiroir('Choisir un niveau différent pour un modèle', '<ul class="par-modele">' + per_model + '</ul>',
                           resume=_plural(modified, 'modèle modifié') if modified else '', ouvert=bool(modified)) if per_model else '')
                 + '</div></div>')
    repetitions = value.get('repetitions', 1)
    # Décision d'Ayo du 2026-10-17 : un même nombre d'essais pour chaque modèle et chaque exemple
    attempts = ('<div class="reglage"><div><span class="lbl" id="l-rep"><b>Essais par modèle</b></span>'
                '<p class="petit">Chaque essai est payé et vérifié à part. Le nombre vaut pour chaque modèle et chaque exemple.</p></div><div>'
                + _segments('l-rep', 'repetitions', [(count, '1 essai' if count == 1 else str(count), '')
                                                     for count in range(1, value.get('max_repetitions', 1) + 1)], repetitions)
                + '<p class="petit">Tous les essais partent, quel que soit le résultat des autres. Plusieurs essais montrent '
                'si les réponses d’un modèle varient ; ils ne mesurent pas sa fiabilité. Avec plus d’un essai, aucun conseil n’est donné.</p></div></div>')
    default = str(value.get('default_output_tokens', ''))
    limited = sum(bool(model.get('output_limit')) for model in models)
    limits = ''.join(
        '<li><label for="max-tokens-' + str(index) + '">' + text(model['name'] + ' · ' + (
            'borne publiée : ' + str(model['output_bound']) if model.get('output_bound') else 'borne publiée inconnue')) + '</label>'
        '<input id="max-tokens-' + str(index) + '" form="configurations-form" type="number" inputmode="numeric" min="1" step="1"'
        + ('' if not model.get('output_bound') else ' max="' + str(model['output_bound']) + '"') + ' placeholder="jetons" name="'
        + text('max_tokens:' + model['id']) + '" value="' + text(str(model.get('output_limit') or '')) + '"></li>'
        for index, model in enumerate(models, 1))
    length = ('<div class="reglage"><div><b>Longueur de réponse</b><p class="petit">Par défaut, chaque modèle garde sa limite habituelle'
              + (' (' + text(default) + ' jetons de sortie)' if default else '') + '.</p></div><div>'
              + tiroir('Fixer une limite pour un modèle', '<ul class="par-modele">' + limits + '</ul><p class="petit">Le coût estimé compte une '
                       'réponse qui atteint cette limite. Une limite au-delà de la borne publiée pour le modèle est refusée, jamais réduite.</p>',
                       resume=_plural(limited, 'modèle limité') if limited else '', ouvert=bool(limited)) + '</div></div>')
    # Les deux textes sont rendus : le script les échange selon les cases cochées, le serveur montre le message sous deux modèles
    minimum = len(chosen) < 2
    summary = ('<span data-choix-synthese' + (' hidden' if minimum else '') + '><b data-choix-nombre>' + str(len(chosen))
               + '</b> <span data-choix-modeles>' + ('modèles' if len(chosen) > 1 else 'modèle') + '</span>'
               + '<span class="hors-mobile"> · raisonnement ' + text(niveau_court(current).lower()) + '</span> · <b>'
               + str(repetitions) + '</b> ' + ('essais' if repetitions > 1 else 'essai') + '</span>'
               + '<span id="choix-minimum" data-choix-minimum' + ('' if minimum else ' hidden') + '>Choisissez au moins deux modèles</span>')
    bar = ('<div class="barre-choix"><span class="resume">' + summary + '</span><button class="btn" type="submit" form="configurations-form" '
           'data-choix-continuer>Continuer' + ic('fleche') + '</button></div>')
    return (cadre_haut(head) + choices + '<div class="reglages carte">' + reasoning + attempts + length
            + render_custom_models(value, csrf, dossier_url) + '</div>' + bar)


def missing_evidence(attempt):
    """Ce que le reçu d'un essai n'établit pas, en clair, ou None (#525)"""
    if attempt.get('state') != 'RECEIVED':
        return None
    fields = set(attempt.get('attribution_incident') or ())
    # Le modèle n'est nommé que si son champ manque ou diffère ; sinon la provenance reste générale
    identity = ('le modèle qui a répondu' if fields & {'model', 'revision'} else 'la provenance demandée') if fields else None
    cost = (attempt.get('observed_cost') or {}).get('status') != 'KNOWN'
    if identity and cost:
        return 'Une réponse reçue n’indique ni ' + identity + ' ni son coût.'
    if identity:
        return 'Une réponse reçue n’indique pas ' + identity + '.'
    return 'Le coût d’une réponse reçue n’est pas connu.' if cost else None


# Aspect d'un état du suivi : (ton, mot, icône) du tampon, ou DIRECT pour le repère « Benchmark en cours »
DIRECT = ('direct', 'Benchmark en cours', 'horloge')
# Tampon propre à chaque cause d'arrêt nommée par le fournisseur
INCIDENT_ASPECTS = {'KEY_REJECTED': ('ko', 'Clé refusée', 'croix'), 'CREDIT_EXHAUSTED': ('ko', 'Crédit épuisé', 'croix'),
                    'RATE_LIMITED': ('vig', 'Limite de débit', 'alerte'), 'PROVIDER_ERROR': ('ko', 'Incident', 'alerte')}


def _hors_compte(campaign):
    """Cellules closes et cellules sans réponse exploitable : ni reçues ni comptées"""
    # Modèle resté en incident fournisseur à sa dernière tentative (#537) : sa réponse reçue n'est pas exploitable
    # Comparaison du demandeur : lu dans son évaluation ; campagne opérateur : dans sa projection (#539)
    outcome = campaign.get('judgment') or campaign
    # Appel clos sans réponse confirmée (#537), dans la comparaison ou sa chaîne de relances : ni reçu ni compté
    closed = set(outcome.get('closed_cells', ())) | {
        a.get('cell_id') for a in campaign['attempts'] if a.get('incident') == AMBIGUOUS_EXPIRED}
    return closed, set(outcome.get('unusable_cells', ()))


def _exclue(campaign, cell, closed, unusable):
    """Exclusion définitive de « réponses vérifiées » : cellule close, inexploitable ou non appelée ; un essai à vérifier non clos peut encore l'être"""
    return cell.get('cell_id') in closed | unusable or cell['state'] == 'INTENT_RECORDED' and not campaign.get('admission_open')


def _partielle(campaign):
    """Vérification à lancer d'une comparaison arrêtée (#696) : une réponse exploitable, les autres essais définitivement exclus"""
    judgment = campaign.get('judgment') or {}
    cells = campaign['cells']
    closed, unusable = _hors_compte(campaign)
    return (judgment.get('status') == 'NOT_STARTED' and bool(judgment.get('total')) and bool(cells)
            and not any(judgment.get(key) for key in ('relaunch', 'relaunching', 'recovering'))
            and not all(c['state'] == 'RECEIVED' for c in cells)
            and all(c['state'] == 'RECEIVED' or _exclue(campaign, c, closed, unusable) for c in cells))


def _cause_arret(campaign):
    """Cause globale de l'arrêt en une phrase, ou None (#696)"""
    # Même priorité que les en-têtes d'arrêt de `_suivi`, sans leur suite ; ce bloc n'est pas déplacé (#650 le modifie)
    attempts = campaign['attempts']
    if any(a.get('incident') == 'CONNECTION_FAILED' for a in attempts):
        return NOT_SENT_TEXT
    if any(a.get('incident') == AMBIGUOUS_EXPIRED for a in attempts):
        return 'Un essai est resté sans réponse confirmée et a été clos.'
    incidents = [a for a in attempts if a.get('incident') in INCIDENT_TEXT]
    named = next((a for a in incidents if a['incident'] in ('KEY_REJECTED', 'CREDIT_EXHAUSTED')), incidents[0] if incidents else None)
    if named:
        cell = next((c for c in campaign['cells'] if c.get('cell_id') == named.get('cell_id')), {})
        config = next((p for p in campaign['panel'] if p['id'] == cell.get('configuration_id')), {})
        return incident_text(config.get('channel_id'), named['incident'])
    missing = next(filter(None, map(missing_evidence, sorted(attempts, key=lambda a: a.get('received_at') or '', reverse=True))), None)
    incident = any(a.get('incident') or a.get('attribution_incident') for a in attempts)
    if campaign.get('stop_reason') == 'ACQUISITION_EVIDENCE_INCOMPLETE' and missing and (
            incident or any(c['state'] == 'INTENT_RECORDED' for c in campaign['cells'])):
        return missing
    return 'Un problème technique a arrêté un essai.' if incident else None


def _cause_couloir(campaign, cell, model, closed):
    """Cause d'arrêt propre à un couloir (#696) : essai non appelé, ou motif de la dernière tentative de sa cellule"""
    if cell['state'] == 'INTENT_RECORDED' and not campaign.get('admission_open'):
        return 'Le benchmark s’est arrêté avant cet essai.'
    # Sans `cell_id` commun, aucun motif n'est attribué à la ligne : la cause globale reste dans le détail
    last = next((a for a in reversed(campaign['attempts']) if cell.get('cell_id') and a.get('cell_id') == cell['cell_id']), None)
    if not last:
        return None
    incident = last.get('incident')
    if incident in INCIDENT_TEXT:
        return incident_text((model or {}).get('channel_id'), incident)
    if incident == 'CONNECTION_FAILED':
        return NOT_SENT_TEXT
    if incident == AMBIGUOUS_EXPIRED or cell['cell_id'] in closed:
        return 'Appel clos sans réponse confirmée.'
    return missing_evidence(last)


def _suivi(campaign):
    """Titre, détail et aspect de l'état du suivi : (actif, terminé, titre, détail, aspect), affichage seulement"""
    cells = campaign['cells']
    judgment = campaign.get('judgment')
    if judgment:
        status = judgment['status']
        if status == 'COMPLETE':
            # Couverture partielle : le nombre de modèles sans réponse exploitable reste dit
            return (False, True, 'Vérification des réponses terminée.', ' '.join(filter(None, (judgment.get('reason'), 'Vos résultats sont prêts.'))),
                    ('ok', 'Terminée', 'check'))
        if status == 'BLOCKED':
            return (False, False, 'La vérification des réponses est bloquée.',
                    judgment.get('reason') or 'Elle s’est arrêtée et ne reprendra pas d’elle-même.', ('ko', 'Vérification bloquée', 'croix'))
        if judgment.get('relaunch') and not any(c['state'] in ('EMISSION_POSSIBLE', 'INTENT_RECORDED') for c in cells):
            # Relance d'un appel candidat après un incident fournisseur : cause et délai, rien n'est à faire
            return True, False, judgment['reason'], 'Les autres réponses sont conservées.', ('att', 'Relance prévue', 'horloge')
        if judgment.get('relaunching') and not judgment.get('recovering'):
            return (True, False, 'Un modèle est interrogé de nouveau après un incident fournisseur.',
                    'Sa réponse arrive au fur et à mesure.', ('att', 'Relance en cours', 'horloge'))
        if judgment.get('recovering'):
            count = judgment['recovering']
            return (True, False, str(count) + (' modèles arrêtés' if count > 1 else ' modèle arrêté')
                    + ' par la limite de longueur ' + ('sont relancés' if count > 1 else 'est relancé') + ' avec une longueur de réponse plus haute.',
                    'Les réponses arrivent au fur et à mesure.', ('att', 'Reprise en cours', 'horloge'))
        closed, unusable = _hors_compte(campaign)
        # Vérification en cours : chaque réponse comptée est reçue ; un essai non appelé ou clos n'attend plus rien
        if status == 'RUNNING' and cells and all(c['state'] == 'RECEIVED' or _exclue(campaign, c, closed, unusable) for c in cells):
            return (True, False, 'Les réponses sont vérifiées.', judgment.get('reason') or 'Chaque réponse est vérifiée selon les critères de votre exemple.',
                    ('att', 'Vérification en cours', 'horloge'))
        # Une relance automatique programmée dit sa cause et son délai ; rien n'est à faire
        if status == 'WAITING' and cells and all(c['state'] == 'RECEIVED' for c in cells):
            return (True, False, 'La vérification des réponses attend son tour.', 'Toutes les réponses sont arrivées.',
                    ('inc', 'En attente', 'horloge'))
        if status == 'NOT_STARTED' and cells and all(c['state'] == 'RECEIVED' for c in cells):
            return (False, False, 'La vérification des réponses est à lancer.', ' '.join(filter(None, (
                'Toutes les réponses sont arrivées. Leur vérification n’est pas encore lancée.', judgment.get('reason')))),
                    ('att', 'À lancer', 'horloge'))
        if _partielle(campaign):
            # Comparaison arrêtée (#696) : la vérification des réponses reçues est le titre, la cause de l'arrêt reste dite
            couverture = ('Certains essais n’ont pas été appelés.' if any(
                c['state'] == 'INTENT_RECORDED' and not campaign.get('admission_open') for c in cells)
                else 'Certains essais n’ont pas donné de réponse exploitable.')
            title, suite, aspect = (('La vérification des réponses reçues est à lancer.', 'Vous pouvez lancer la vérification des réponses reçues.',
                                     ('att', 'À lancer', 'horloge')) if judgment.get('can_start') else
                                    ('La vérification des réponses reçues n’est pas lancée.', None, ('inc', 'Non lancée', 'horloge')))
            return False, False, title, ' '.join(filter(None, (_cause_arret(campaign), couverture, suite, judgment.get('reason')))), aspect
    stopped = campaign.get('stop_reason') or campaign.get('restore_pending') or not campaign.get('admission_open')
    # Un modèle encore interrogé garde le suivi actif, même si un autre a déjà rencontré un incident
    if not stopped and any(c['state'] == 'EMISSION_POSSIBLE' for c in cells):
        return True, False, 'Les modèles travaillent.', 'Les réponses arrivent au fur et à mesure. Vous pouvez quitter cette page et revenir.', DIRECT
    if not stopped and any(c['state'] == 'INTENT_RECORDED' for c in cells):
        # Un modèle a déjà répondu : l'autre essai attend son tour
        if any(c['state'] == 'RECEIVED' for c in cells):
            return (True, False, 'Un essai attend son tour.',
                    'Les réponses arrivent au fur et à mesure. Vous pouvez quitter cette page et revenir.', ('inc', 'En file', 'horloge'))
        return True, False, 'Le lancement est enregistré.', 'Les essais n’ont pas encore démarré.', ('inc', 'En file', 'horloge')
    if any(a.get('incident') == 'CONNECTION_FAILED' for a in campaign['attempts']):
        return (False, False, NOT_SENT_TEXT, 'Le benchmark s’est arrêté et rien n’est relancé automatiquement.', ('vig', 'Non envoyé', 'alerte'))
    if any(a.get('incident') == AMBIGUOUS_EXPIRED for a in campaign['attempts']):
        return (False, False, AMBIGUOUS_EXPIRED_TEXT, 'Le benchmark s’est arrêté ; les réponses déjà reçues restent consultables.',
                ('vig', 'Appel clos', 'alerte'))
    # Incident fournisseur reçu (#525) : sa cause est dite, l'arrêt reste celui du contrat financier
    # Un incident de clé l'emporte : depuis #533, une limite de débit sur un autre modèle n'arrête rien
    incidents = [a for a in campaign['attempts'] if a.get('incident') in INCIDENT_TEXT]
    named = next((a for a in incidents if a['incident'] in ('KEY_REJECTED', 'CREDIT_EXHAUSTED')), incidents[0] if incidents else None)
    if named:
        # Texte du canal appelé, comme sur la page de résultats (#537)
        cell = next((c for c in cells if c.get('cell_id') == named.get('cell_id')), {})
        config = next((p for p in campaign['panel'] if p['id'] == cell.get('configuration_id')), {})
        aspect = INCIDENT_ASPECTS[named['incident']]
        official = incident_text(config.get('channel_id'), named['incident']) != INCIDENT_TEXT[named['incident']]
        return (False, False, incident_text(config.get('channel_id'), named['incident']),
                'Le benchmark s’est arrêté ; les réponses déjà reçues restent consultables et rien n’est relancé automatiquement.',
                ('vig', 'Limite officielle', 'alerte') if official else aspect)
    # Preuve d'acquisition incomplète (#525) : le dernier reçu en cause dit ce qui manque, sans cause devinée
    missing = next(filter(None, map(missing_evidence, sorted(
        campaign['attempts'], key=lambda a: a.get('received_at') or '', reverse=True))), None)
    incident = any(a.get('incident') or a.get('attribution_incident') for a in campaign['attempts'])
    # Sans incident, le seul coût inconnu n'est dit que s'il a empêché un essai de partir
    if campaign.get('stop_reason') == 'ACQUISITION_EVIDENCE_INCOMPLETE' and missing and (
            incident or any(c['state'] == 'INTENT_RECORDED' for c in cells)):
        return (False, False, missing, 'Le benchmark s’est arrêté ; les réponses déjà reçues restent consultables et rien n’est relancé automatiquement.',
                ('vig', 'Preuve incomplète', 'alerte'))
    if incident:
        return (False, False, 'Un problème technique est survenu.', 'Le suivi s’est arrêté et rien n’est relancé automatiquement.',
                ('ko', 'Incident', 'alerte'))
    if any(c['state'] == 'AMBIGUOUS' for c in cells) or campaign.get('state') == 'BLOCKED':
        return (False, False, 'Un essai n’a pas pu être confirmé.',
                'Nous ne savons pas si le modèle a répondu. Rien n’est relancé automatiquement.', ('vig', 'À vérifier', 'alerte'))
    # Toutes les réponses reçues est l'issue normale : la fermeture d'admission qui suit ne la masque pas
    if cells and all(c['state'] == 'RECEIVED' for c in cells):
        return (False, True, 'Benchmark terminé.', 'Toutes les réponses sont arrivées. Celles qui n’ont pas encore de verdict attendent leur vérification.',
                ('ok', 'Réponses reçues', 'check'))
    if stopped:
        return (False, False, 'Le benchmark s’est arrêté avant la fin.', 'Vous pouvez lire les réponses déjà reçues.', ('vig', 'Arrêté', 'pause'))
    return (False, False, 'Les essais n’ont pas encore démarré.', 'Actualisez la page dans un moment pour voir s’ils ont commencé.',
            ('inc', 'Non démarré', 'horloge'))


def campaign_followup(campaign):
    """État d’affichage seulement : ne crée aucune autorité ni reprise"""
    active, ready, title, detail, _ = _suivi(campaign)
    return active, ready, ' '.join(filter(None, (title, detail)))


def campaign_status(campaign, dossier_url):
    """Ton, mot, titre, phrase, cible et action d'un benchmark : même lecture sur la page du cas et le suivi"""
    base = dossier_url + '/campaigns/' + campaign['campaign_id']
    if not campaign['attempts']:
        return ('att', 'À faire', 'Benchmark prêt à lancer', 'Vérifiez les modèles et les coûts estimés, puis lancez le benchmark.',
                base + '/conditions', 'Vérifier puis lancer le benchmark')
    if (campaign.get('judgment') or {}).get('can_start'):
        # Le bouton qui lance la vérification payante est sur le suivi : le cas y mène, jamais vers des résultats vides
        return ('att', 'À faire', 'Vérification des réponses à lancer', ('Certaines réponses sont arrivées avant l’arrêt du benchmark.'
                if _partielle(campaign) else 'Les réponses sont arrivées.')
                + ' Leur vérification, payée avec votre clé OpenRouter, se lance depuis le suivi du benchmark.',
                base + '/conditions', 'Lancer la vérification des réponses')
    active, ready, message = campaign_followup(campaign)
    if ready:
        return 'ok', 'Terminé', 'Benchmark terminé', message, base, 'Voir les résultats'
    if active:
        return 'att', 'En cours', 'Benchmark en cours', message, base + '/conditions', 'Suivre le benchmark'
    return 'vig', 'À vérifier', 'Le benchmark demande votre attention', message, base + '/conditions', 'Voir ce qui s’est passé'


def render_campaign_followup(value, csrf):
    campaign = value['campaign']
    base = '/preparation/dossiers/' + value['dossier_id'] + '/campaigns/' + campaign['campaign_id']
    active, ready, title, detail, aspect = _suivi(campaign)
    if aspect == DIRECT:
        marque = '<p class="en-direct">' + text(DIRECT[1]) + '</p>'
    else:
        marque = '<div class="g-statut">' + statut(*aspect) + '</div>'
    cells = campaign['cells']
    states = {'NOT_STARTED': statut('inc', 'Non démarré', 'horloge'), 'INTENT_RECORDED': statut('inc', 'En file', 'horloge'),
              'EMISSION_POSSIBLE': statut('att', 'En cours', 'horloge'), 'RECEIVED': statut('ok', 'Reçue', 'check'),
              'AMBIGUOUS': statut('vig', 'À vérifier', 'alerte')}
    names = value.get('model_names', {})
    models = {item['id']: item for item in campaign['panel']}
    ordered = campaign.get('cases', [])
    # Plusieurs essais d'un modèle : chaque ligne nomme son exemple et son essai
    examples = {case['id']: example_label(case, number) for number, case in enumerate(ordered, 1)} if len(ordered) > 1 else {}
    closed, unusable = _hors_compte(campaign)
    # Segment Vérification : l'état du jugement de la campagne, le même sur chaque couloir compté
    verification = {'RUNNING': 'encours', 'COMPLETE': 'fait', 'BLOCKED': 'bloque'}.get((campaign.get('judgment') or {}).get('status', ''), 'avenir')
    # Cause par couloir : seulement pour la vérification à lancer d'une comparaison arrêtée (#696)
    partielle = _partielle(campaign)
    lanes = ''
    for cell in cells:
        model = models.get(cell['configuration_id'])
        lines = lignes_raisonnement(model) if model else []
        prefix = ' · '.join(filter(None, (examples.get(cell.get('case_id')), 'Essai ' + str(cell['repetition']) if 'repetition' in cell else '')))
        position = {'INTENT_RECORDED': 'p0', 'NOT_STARTED': 'p0', 'RECEIVED': 'p100', 'AMBIGUOUS': 'pm'}.get(cell['state'], '')
        # `position` ne suffit pas : `p0` vaut pour une réponse à venir comme pour une réponse arrêtée
        reponse = {'EMISSION_POSSIBLE': 'encours', 'RECEIVED': 'fait', 'AMBIGUOUS': 'bloque'}.get(cell['state'], 'avenir')
        if cell['state'] == 'RECEIVED' and cell.get('cell_id') in closed:
            mark, position, reponse = statut('vig', 'Non confirmée', 'alerte'), 'pm', 'bloque'
        elif cell['state'] == 'RECEIVED' and cell.get('cell_id') in unusable:
            mark, position, reponse = statut('ko', 'Sans réponse', 'croix'), 'p0', 'bloque'
        elif cell['state'] == 'INTENT_RECORDED' and not campaign.get('admission_open'):
            # Sans admission, une intention n'est jamais émise (#525), comme sur la page de résultats
            mark, position, reponse = statut('inc', 'Non appelé', 'pause'), 'p0', 'bloque'
        else:
            mark = states.get(cell['state'], statut('inc', 'État inconnu', 'question'))
        etats = (reponse, 'sansobjet' if _exclue(campaign, cell, closed, unusable) else verification if reponse == 'fait' else 'avenir')
        cause = _cause_couloir(campaign, cell, model, closed) if partielle else None
        lanes += couloir(model_name(model, names) if model else 'Modèle', ([prefix] if prefix else []) + lines + ([cause] if cause else []),
                         mark, position, etats=etats)
    received = sum(cell['state'] == 'RECEIVED' for cell in cells)
    # Ni l'appel clos ni la cellule sans réponse exploitable ne comptent comme une réponse reçue (#541)
    received -= sum(cell['state'] == 'RECEIVED' and cell.get('cell_id') in closed | unusable for cell in cells)
    counters = ('<div class="compteur" role="status"><p class="val">' + str(received) + '<small>/' + str(len(cells))
                + '</small></p><p class="etiq">réponses reçues</p></div>')
    judgment = campaign.get('judgment')
    if judgment:
        counters += ('<div class="compteur"><p class="val val-eval">' + text(judgment['completed']) + '<small>/' + text(judgment['total'])
                     + '</small></p><p class="etiq">réponses vérifiées</p></div>')
    # La composition reste dans la zone d'état : le script de suivi la remplace entière à chaque mise à jour
    status = ('<div id="campaign-status" aria-live="polite"><div class="direct-tete"><div>' + marque + '<h2 id="t-direct">' + text(title) + '</h2>'
              + ('<p>' + text(detail) + '</p>' if detail else '') + '</div><div class="g-compteurs">' + counters + '</div></div>'
              '<ol class="couloirs' + (' vit' if active else '') + '" aria-label="Modèle par modèle">' + lanes + '</ol></div>')
    refresh = lien(base + '/conditions', 'Actualiser le suivi', 'actualiser')
    note = ''
    if active:
        # Le script de suivi masque le lien et montre le bouton ; sans script, le lien reste la seule action
        foot = ('<div class="direct-pied" id="preparation-progress"><span role="status"></span><span class="actions">'
                '<progress class="cache" aria-label="Benchmark en cours"></progress>' + refresh
                + '<button type="button" class="btn btn-sec" hidden>' + ic('pause') + 'Arrêter la mise à jour</button></span></div>')
    else:
        actions = []
        if judgment and judgment['status'] == 'BLOCKED':
            actions.append(lien('/preparation/access', 'Ma clé OpenRouter', 'cle'))
        # Aucune réponse arrivée : pas de lien vers des résultats vides, l'action principale change (B62)
        ambiguous = any(c['state'] == 'AMBIGUOUS' for c in cells) or campaign.get('state') == 'BLOCKED'
        empty = bool(cells) and received == 0 and all(c['state'] == 'RECEIVED' for c in cells)
        results = not active and (ready or not judgment or judgment['completed'] > 0) and not (received == 0 and (ambiguous or empty))
        if judgment and judgment['can_start']:
            note = ('<p class="g-note"><strong>La vérification des réponses est payée avec votre clé OpenRouter.</strong> '
                    'Les modèles comparés ne sont pas appelés une seconde fois.</p>')
            actions += [refresh, form(csrf, base + '/evaluate', {'confirm': 'yes'}, bouton('Vérifier les réponses reçues'))]
        elif results:
            actions += [refresh, btn(base, 'Voir les résultats')]
        elif empty and not ambiguous:
            actions += [refresh, btn('/preparation', 'Revenir à mes cas d’usage')]
        else:
            actions.append('<a class="btn" href="' + text(base + '/conditions') + '">Actualiser le suivi' + ic('actualiser') + '</a>')
        foot = '<div class="direct-pied"><span></span><span class="actions">' + ''.join(actions) + '</span></div>'
    return ('<div id="campaign-followup"' + (' data-results-href="' + text(base) + '"' if ready else '') + '><section class="direct" aria-labelledby="t-direct">'
            + status + note + foot + '</section></div>')


# Segments d'un couloir : clé, nom et mot de chaque état, lus par les lecteurs d'écran
SEGMENTS = (('reponse', 'Réponse', {'avenir': 'à venir', 'encours': 'en cours', 'fait': 'reçue', 'bloque': 'arrêtée'}),
            ('verification', 'Vérification', {'avenir': 'à venir', 'encours': 'en cours', 'fait': 'terminée', 'bloque': 'bloquée',
                                              'sansobjet': 'sans objet'}))


def couloir(nom, lignes, statut_html, position='', *, etats):
    """Un couloir du suivi : le modèle et son réglage, le segment Réponse avec le disque d'essai, le segment Vérification, puis son statut ;
    `etats` donne l'état des deux segments"""
    coureur = '<span class="coureur' + (' ' + position if position else '') + '"><span class="trace"></span>' + disque() + '</span>'
    segments = ''.join('<span data-segment="' + cle + '" data-etat="' + valeur + '"><span class="cache">' + text(libelle + ' : ' + mots[valeur])
                       + '</span>' + (coureur if cle == 'reponse' else '<span class="trace"></span>') + '</span>'
                       for (cle, libelle, mots), valeur in zip(SEGMENTS, etats))
    return ('<li class="couloir"><span class="couloir-nom">' + text(nom) + ('<small>' + ''.join('<span>' + text(ligne) + '</span>' for ligne in lignes)
            + '</small>' if lignes else '') + '</span><span class="voie">' + segments + '</span>' + statut_html + '</li>')


def example_label(case, number):
    """« Exemple N » : le numéro de l'exemple du cas d'usage, porté par l'identifiant `case-N` de son cas"""
    suffix = case['id'].removeprefix('case-')
    return 'Exemple ' + (suffix if suffix.isdigit() else str(number))


def _coche(ton, libelle, note=''):
    """Ligne de la liste « Avant de lancer » : pastille (ok, ko, vig, inc), contrôle, constat"""
    icone = {'ok': 'check', 'ko': 'croix', 'vig': 'alerte', 'inc': 'question'}[ton]
    return ('<li><span class="coche' + ('' if ton == 'ok' else ' coche-' + ton) + '">' + ic(icone) + '</span><span>'
            + text(libelle) + '</span><span class="petit">' + text(note) + '</span></li>')


def _cout_ligne(montant, inconnu):
    return inconnu if montant is None else montant_lisible(montant) + ' USD'


def render_campaign_launch_requester(value, csrf):
    """Récapitulatif avant dépense : état, liste des contrôles, réglages, critères et pupitre ; puis suivi une fois lancé"""
    campaign = value['campaign']
    if campaign['attempts']:
        return render_campaign_followup(value, csrf)
    dossier_url = '/preparation/dossiers/' + value['dossier_id']
    base = dossier_url + '/campaigns/' + campaign['campaign_id']
    names = value.get('model_names', {})
    panel = campaign['panel']
    access = value.get('access', {})
    cases = campaign.get('cases', [])
    repetitions = max((cell.get('repetition', 1) for cell in campaign['cells']), default=1)
    checks = value['checks']
    failures = [check for check in checks if not check['ok']]
    failed = failures[0] if failures else None
    remaining = _cout_ligne(access.get('limit_remaining_usd'), 'inconnu')
    numero = numero_version(value, campaign.get('task', {}).get('revision'))
    frozen = campaign['conditions']['frozen_at']
    # Prévision des réponses et réservation de la vérification restent deux montants distincts
    rows = ''
    for check in checks:
        key, detail = check['key'], check['detail']
        detail = detail if type(detail) is str else ''
        if check['ok']:
            rows += _coche('ok', *{
                'selection_current': ('Sélection la plus récente', ''),
                'example_validated': ('Exemple validé', 'version ' + str(numero) if numero else ''),
                'example_qualified': ('Contrôle de l’exemple', 'réussi'),
                'configurations_available': (_plural(len(panel), 'modèle choisi'),
                                             str(repetitions) + (' essais chacun' if repetitions > 1 else ' essai chacun')),
                'access_connected': ('Clé OpenRouter connectée', 'crédit restant ' + remaining),
                'estimate_available': ('Estimation des réponses', _cout_ligne(value.get('estimate_total_usd'), 'non estimable')),
                'examples_current': ('Chaque exemple est vérifié', 'à la version choisie'),
                'judgment_available': ('Budget de la vérification des réponses', 'suffisant'),
            }.get(key, (detail, '')))
        elif key == 'example_qualified':
            rows += _coche('ko', 'Contrôle de l’exemple', 'à refaire' if validated(checks) else detail)
            rows += ''.join(_coche('vig', finding['text'], 'relevé par le contrôle') for finding in check.get('findings', []))
        elif key == 'access_connected':
            status = access.get('status')
            rows += _coche('ko' if status == 'invalid' else 'vig' if status == 'unavailable' else 'ko', 'Clé OpenRouter',
                           'refusée' if status == 'invalid' else 'service indisponible' if status == 'unavailable' else 'absente')
        else:
            rows += _coche('ko', {'selection_current': 'Sélection remplacée', 'example_validated': 'Exemple à valider',
                                  'configurations_available': 'Modèle à choisir de nouveau', 'estimate_available': 'Estimation incomplète',
                                  'examples_current': 'Autre exemple modifié',
                                  'judgment_available': 'Budget de la vérification des réponses',
                                  'execution_available': 'Exécution des essais', 'launch_pending': 'Lancement en cours'}.get(key, detail),
                           'sélection invalide' if key == 'configurations_available' else detail)
    rows += _coche('ok', 'Critères gelés', jour_lisible(frozen))
    # Un état dit où l'on en est ; la clé s'ajoute ici même puis revient à ce récapitulatif
    links = {
        'selection_current': (dossier_url + '/campaigns/' + str(failed.get('latest') if failed else '') + '/conditions', 'Voir la dernière sélection'),
        'example_validated': (dossier_url + '#validation', 'Valider l’exemple'),
        'example_qualified': (dossier_url, 'Voir le contrôle de l’exemple'),
        'examples_current': (dossier_url + '/configurations', 'Choisir de nouveau les modèles'),
        'configurations_available': (dossier_url + '/configurations', 'Choisir de nouveau les modèles'),
        'estimate_available': (dossier_url + '/configurations', 'Revoir les modèles choisis'),
        'access_connected': None,
    }
    estimate = value.get('estimate_total_usd')
    if value['launchable']:
        head = etat('ok', 'Prêt', 'Tout est prêt', 'Réponses estimées à ' + _cout_ligne(estimate, 'un montant non estimable')
                    + '. Le montant réservé pour la vérification des réponses est ' + _cout_ligne(value.get('judgment_estimate_usd'), 'inconnu') + '.')
    elif failed is None:
        head = etat('vig', 'Indisponible', 'Le service d’exécution est indisponible',
                    ('Estimation complète : réponses ' + _cout_ligne(estimate, 'non estimable') + ', vérification des réponses '
                     + _cout_ligne(value.get('judgment_estimate_usd'), 'inconnu') + '. ' if value.get('judgment_estimate_usd') is not None else
                     'Estimation incomplète : le montant réservé pour la vérification des réponses est inconnu. ') + 'Réessayez dans quelques minutes.',
                    btn(base + '/conditions', 'Réessayer', icone='actualiser'))
    elif failed['key'] == 'access_connected':
        head = {'invalid': etat('ko', 'Clé refusée', 'Cette clé ne peut pas être utilisée',
                                'OpenRouter refuse cette clé. Remplacez-la.'),
                'unavailable': etat('vig', 'Indisponible', 'Enregistrement de clé indisponible',
                                    'Vous ne pouvez pas enregistrer de clé pour le moment. Réessayez plus tard : aucun modèle n’a été appelé.',
                                    btn(base + '/conditions', 'Réessayer', icone='actualiser'))
                }.get(access.get('status'), etat('att', 'Clé manquante', 'Aucune clé OpenRouter enregistrée',
                                                 'La clé paie les appels aux modèles. Ajoutez-la pour pouvoir lancer.'))
    elif failed['key'] in ('execution_available', 'launch_pending'):
        # Indisponibilité passagère : relire la page suffit, aucune autre action à proposer
        head = etat('vig', 'Indisponible', 'L’exécution des essais est indisponible' if failed['key'] == 'execution_available'
                    else 'Un lancement est déjà en cours d’enregistrement', failed['detail'], lien(base + '/conditions', 'Actualiser', 'actualiser'))
    else:
        href, label = links.get(failed['key']) or (dossier_url + '/configurations', 'Modifier la sélection')
        titre = {'example_qualified': 'L’exemple doit passer le contrôle', 'example_validated': 'L’exemple doit être validé',
                 'selection_current': 'Cette sélection a été remplacée', 'configurations_available': 'Un modèle est à choisir de nouveau',
                 'examples_current': 'Un autre exemple a changé', 'estimate_available': 'Le coût des réponses n’est pas estimable',
                 'judgment_available': 'La vérification des réponses est indisponible'}.get(failed['key'], 'Le lancement est bloqué')
        points = len(failed.get('findings', []))
        texte = ('Le contrôle de l’exemple a relevé ' + _plural(points, 'point') + '. Corrigez-' + ('les' if points > 1 else 'le') + ' avant de lancer.'
                 if failed['key'] == 'example_qualified' and points else failed['detail'] if type(failed['detail']) is str else '')
        head = etat('vig' if failed['key'] in ('estimate_available', 'selection_current') else 'ko',
                    'Remplacée' if failed['key'] == 'selection_current' else 'Bloqué', titre, texte, btn(href, label))
    # Clé : formulaire tant qu'elle manque ; solde et plafond en relevé compact quand elle est connectée
    key = ''
    if access.get('status') == 'connected' and not value['launchable']:
        key = ('<div class="g-cle carte">' + releves([
            ('Solde restant selon OpenRouter',) + ((None, '', '') if access.get('limit_remaining_usd') is None
                                                    else (montant_lisible(access['limit_remaining_usd']), 'USD', '')),
            ('Plafond de la clé',) + ((None, '', '') if access.get('limit_usd') is None
                                       else (montant_lisible(access['limit_usd']), 'USD', ''))], compact=True, icone='cle')
               + personal_key_form(csrf, access, base + '/conditions') + '</div>')
    elif failed is not None and failed['key'] == 'access_connected' and access.get('status') != 'unavailable':
        key = personal_key_form(csrf, access, base + '/conditions', opened=True)
    check = ('<section aria-labelledby="t-check"><div class="zone-titre"><h2 id="t-check">Avant de lancer</h2></div>'
             '<ul class="checklist carte">' + rows + '</ul></section>')
    recoveries = value.get('recovery_limits') or {}
    lines = ''
    for item in panel:
        reasoning = lignes_raisonnement(item)
        level = reasoning[0].removeprefix('Raisonnement ')
        limit = item.get('parameters', {}).get('max_tokens')
        output = [('limite non renseignée' if limit is None else str(limit) + ' jetons')] + [
            part for part in output_label(item, recoveries).split(' · ')[1:]]
        amount = item['estimate']['amount_usd']
        lines += ('<tr><td data-th="Modèle"><span class="modele">' + text(model_name(item, names)) + '</span></td><td data-th="Raisonnement">'
                  + text(level[:1].upper() + level[1:]) + ''.join('<br><span class="petit">' + text(line) + '</span>' for line in reasoning[1:])
                  + '</td><td data-th="Longueur de réponse">' + text(' · '.join(output))
                  + '</td><td data-th="Coût estimé"' + ('' if amount is None else ' class="nb"') + '>'
                  + text(_cout_ligne(amount, 'non estimable')) + '</td></tr>')
    models = ('<section aria-labelledby="t-modeles"><div class="zone-titre"><h2 id="t-modeles">Modèles et réglages</h2></div>'
              '<div class="table-defile"><table class="empile"><thead><tr><th scope="col">Modèle</th><th scope="col">Raisonnement</th>'
              '<th scope="col">Longueur de réponse</th><th scope="col">Coût estimé</th></tr></thead><tbody>' + lines + '</tbody></table></div>')
    if any(recoveries.values()):
        # Reprises préautorisées par ce lancement : leur coût reste distinct de la prévision
        models += ('<p class="petit">' + text(
            'Un modèle arrêté par la limite de longueur est relancé au plus deux fois, sa longueur de réponse doublée '
            'à chaque fois dans la limite de sa route, comme l’indique le tableau ; chaque reprise apparaît sur sa '
            'propre ligne. Coût estimé si chaque modèle était repris jusqu’à sa dernière limite : '
            + _cout_ligne(value.get('recovery_estimate_usd'), 'non estimable') + '.') + '</p>')
    models += '</section>'
    if value.get('examples'):
        items = []
        for example in value.get('examples', []):
            label = 'Exemple ' + str(example['number']) + (' : ' + example['difference'] if example['difference'] else '')
            items.append(label + ('' if example['excluded'] is None else ' · pas inclus : ' + example['excluded']))
        # Plusieurs essais : le paragraphe qui suit dit combien de fois chaque modèle répond
        models += ('<section id="exemples"><div class="zone-titre"><h2>Exemples comparés</h2></div><p class="petit">'
                   + ('Chaque modèle répond une fois à chacun. ' if repetitions == 1 else '')
                   + 'Plusieurs exemples ne prouvent pas qu’ils représentent votre cas d’usage.</p>' + listing(items) + '</section>')
    if repetitions > 1:
        models += '<p class="petit">' + text('Chaque modèle répond ' + str(repetitions) + ' fois à '
                                             + ('chaque exemple' if len(cases) > 1 else 'l’exemple') + '. Chaque essai est une réponse '
                                             'distincte, payée et vérifiée séparément ; tous partent, quel que soit le résultat des autres.') + '</p>'
    if len(cases) > 1 or repetitions > 1:
        labels = {case['id']: example_label(case, number) for number, case in enumerate(cases, 1)}
        columns = (['Exemple'] if len(cases) > 1 else []) + (['Essai'] if repetitions > 1 else []) + ['Modèle', 'Coût estimé']
        planned = ''
        for cell in campaign['cells']:
            values = (([labels[cell['case_id']]] if len(cases) > 1 else [])
                      + (['Essai ' + str(cell['repetition'])] if repetitions > 1 else [])
                      + [model_name(next(item for item in panel if item['id'] == cell['configuration_id']), names),
                         _cout_ligne(cell['estimate']['amount_usd'], 'non estimable')])
            planned += ('<tr>' + ''.join('<td data-th="' + text(title) + '">' + text(item) + '</td>' for title, item in zip(columns, values)) + '</tr>')
        models += ('<section aria-labelledby="t-essais"><div class="zone-titre"><h2 id="t-essais">Essais prévus</h2></div><div class="table-defile">'
                   '<table class="empile" aria-label="Essais prévus"><thead><tr>' + ''.join('<th scope="col">' + text(title) + '</th>' for title in columns)
                   + '</tr></thead><tbody>' + planned + '</tbody></table></div></section>')
    criteria = value['criteria']
    result = criteria['result_expected']

    def puces(entries):
        return '<ul class="liste-simple">' + ''.join('<li>' + text(item['description']) + '</li>' for item in entries) + '</ul>'
    eliminatory = criteria['eliminatory_errors']
    terms = cles([('Résultat attendu', text(result)),
                  ('Obligations', puces(criteria['obligations']) if criteria['obligations'] else 'Aucune'),
                  ('Erreur éliminatoire' if len(eliminatory) == 1 else 'Erreurs éliminatoires', puces(eliminatory) if len(eliminatory) > 1
                   else text(eliminatory[0]['description']) if eliminatory else 'Aucune'),
                  ('Limites', puces([{'description': item} for item in criteria['limits']]) if criteria.get('limits') else 'Aucune déclarée'),
                  ('Date de gel', text(date_lisible_utc(frozen))), ('Harnais', harnais())])
    consigne = ('<section class="consigne">' + ruban('Ce qui sera testé') + '<blockquote>' + text(result) + '</blockquote><p class="petit">'
                + ('Même consigne pour chaque modèle et, pour chaque exemple, mêmes pièces. Chaque verdict vaudra pour un exemple et pour un modèle '
                   'tel qu’il est réglé ici, sans conclure sur le modèle en général ni additionner les exemples.' if len(cases) > 1 else
                   'Même consigne, mêmes pièces pour chaque modèle. Le verdict vaudra pour cet exemple et ces réglages, pas pour le modèle en général.')
                + '</p></section>' + tiroir('Critères et conditions', terms, resume=date_lisible_utc(frozen)))
    left = '<div class="pile-l">' + key + check + models + consigne + '</div>'
    total = [('Réponses, estimation', _cout_ligne(estimate, 'non estimable')),
             ('Réservé pour la vérification des réponses', _cout_ligne(value.get('judgment_estimate_usd'), 'inconnu')),
             ('Crédit restant', remaining if access.get('limit_usd') is None or access.get('limit_remaining_usd') is None
              else montant_lisible(access['limit_remaining_usd']) + ' / ' + montant_lisible(access['limit_usd']) + ' USD')]
    if len(cases) > 1:
        total.insert(0, ('Exemples', str(len(cases))))
    if repetitions > 1 or len(cases) > 1:
        total.insert(0, ('Essais', str(len(campaign['cells'])) + (' (' + str(repetitions) + ' par modèle' + (' et par exemple' if len(cases) > 1 else '') + ')' if repetitions > 1 else '')))
    if value['launchable']:
        # Le bouton est la confirmation : le serveur exige toujours `confirm=yes`
        action = bouton('Lancer le benchmark', xl=True)
        opening, closing = form(csrf, base + '/start', {'manifest_version': campaign['version'], 'frozen_at': frozen, 'confirm': 'yes'},
                                '@@').split('@@')
        opening = opening.replace('<form ', '<form class="pupitre" ', 1)
        lock = ''
    else:
        action, opening, closing = '', '<div class="pupitre">', '</div>'
        detail = failed['detail'] if failed and type(failed['detail']) is str else (
            'ajoutez d’abord votre clé OpenRouter' if failed else 'le service d’exécution est indisponible. Réessayez dans quelques minutes')
        lock = '<p class="g-verrou">' + ic('cadenas') + '<span>' + text('Impossible de lancer pour l’instant : ' + detail + '.') + '</span></p>'
    pupitre = ('<aside aria-label="Récapitulatif avant dépense">' + opening + '<span class="etiq">Récapitulatif avant dépense</span><p class="grand">'
               + str(len(panel)) + '<small>' + ('modèles comparés' if len(panel) > 1 else 'modèle comparé') + '</small></p><dl>'
               + ''.join('<div><dt>' + text(label) + '</dt><dd>' + text(amount) + '</dd></div>' for label, amount in total) + '</dl>' + lock + action
               + '<p>Payé avec votre clé OpenRouter. Ces montants sont estimés : '
               'seuls les appels réellement faits sont facturés.</p>' + closing + '</aside>')
    return cadre_haut(head) + '<div class="depart">' + left + pupitre + '</div>'


def validated(checks):
    """Vrai quand l'exemple est validé : son contrôle seul reste à refaire"""
    return any(check['key'] == 'example_validated' and check['ok'] for check in checks)


def reglages_lisibles(panel, names):
    """Réglages de chaque modèle en mots : raisonnement, longueur de réponse et coût estimé, sans clé ni JSON"""
    return ''.join(
        '<h3>' + text(model_name(item, names)) + '</h3>' + cles([
            ('Raisonnement', '<br>'.join(text(line) for line in lignes_raisonnement(item))),
            ('Longueur de réponse', text(str(item['parameters']['max_tokens']) + ' jetons')
             if 'max_tokens' in item.get('parameters', {}) else 'par défaut'),
            ('Coût estimé', text(_cout_ligne((item.get('estimate') or {}).get('amount_usd'), 'non estimable')))])
        for item in panel)


def render_campaign_launch_operator(value, csrf):
    """Benchmark préparé et admis par l'opérateur, confirmé par le demandeur"""
    campaign = value['campaign']
    if campaign['attempts']:
        return render_campaign_followup(value, csrf)
    base = '/preparation/dossiers/' + value['dossier_id'] + '/campaigns/' + campaign['campaign_id']
    content = '<p class="texte-l">Ce benchmark a été préparé et autorisé par l’équipe Evaly. En confirmant, vous lancez seulement les essais qu’il a autorisés.</p>'
    groups = ''
    if value['criteria']['eliminatory_errors']:
        groups += '<h3>' + badge('NE SATISFAIT PAS') + ' Erreurs éliminatoires</h3>' + listing(item['description'] for item in value['criteria']['eliminatory_errors'])
    groups += '<h3>' + badge('SATISFAIT') + ' Exigences à respecter</h3>' + listing(item['description'] for item in value['criteria']['obligations'])
    content += section('Ce qui sera vérifié', '<p>' + text(value['criteria']['result_expected']) + '</p>' + groups)
    content += section('Modèles et conditions', listing([candidate_label(item, value.get('model_names', {})) for item in campaign['panel']]) +
        cles([('Harnais', harnais()), ('Conditions fixées le', text(date_lisible_utc(campaign['conditions']['frozen_at'])))]) +
        tiroir('Détail des réglages', reglages_lisibles(campaign['panel'], value.get('model_names', {}))))
    access = value.get('access', {'status': 'unavailable'})
    if access.get('status') == 'unavailable':
        access_content = '<p>Impossible d’enregistrer une clé pour le moment.</p>'
    else:
        access_content = ('<p>Clé OpenRouter enregistrée. ' + access_summary(access) + '</p>'
                          if access.get('status') == 'connected' else '')
        access_content += personal_key_form(csrf, access, base + '/conditions', opened=access.get('status') != 'connected')
    content += section('Votre clé OpenRouter', access_content)
    estimate = value['estimate']
    cost = '<p>Coût estimé : ' + text(estimate['amount'] + ' ' + estimate['currency'] if estimate else 'aucune estimation fournie') + '.</p>'
    if estimate:
        cost += '<p>' + text(estimate['assumptions']) + ' Source : ' + text(estimate['source']) + '.</p>'
    budget = campaign['budget']
    if budget and budget['limit'] is not None:
        cost += '<p>Budget autorisé : ' + text(budget['limit']) + ' ' + text(budget['currency']) + '.</p>'
    cost += '<p>Montant mis de côté par essai : ' + text(', '.join(key + ' : ' + amount for key, amount in campaign['reserve_amounts'].items()) if campaign['reserve_amounts'] else 'aucun') + '.</p>'
    cost += '<p class="petit">L’estimation, le montant mis de côté et le coût observé sont trois montants différents. Le montant mis de côté ne garantit pas un plafond de facturation.</p>'
    content += section('Coût', cost)
    if value['can_launch']:
        content += form(csrf, base + '/start', {'manifest_version': campaign['version'],
            'frozen_at': campaign['conditions']['frozen_at'], 'admission_id': value['admission_id']},
            '<label><input type="checkbox" name="confirm" value="yes" required> Je confirme le lancement des essais autorisés ci-dessus.</label>'
            + bouton('Lancer le benchmark'))
    else:
        content += '<p class="petit">' + ('Lancement enregistré. Consultez les essais et leurs résultats ci-dessous.' if campaign['attempts'] else 'Impossible de lancer pour l’instant. L’équipe Evaly doit vérifier les autorisations et s’assurer que les benchmarks peuvent être exécutés.') + '</p>'
    models = {item['id']: candidate_label(item, value.get('model_names', {})) for item in campaign['panel']}
    content += section('Suivi des essais', listing([models.get(cell['configuration_id'], 'Modèle') + ' : ' + {'NOT_STARTED': 'non démarré', 'INTENT_RECORDED': 'en attente', 'EMISSION_POSSIBLE': 'en cours', 'RECEIVED': 'réponse reçue', 'AMBIGUOUS': 'réponse non confirmée, à vérifier'}.get(cell['state'], cell['state']) for cell in campaign['cells']]))
    content += '<p>' + lien(base + '/conditions', 'Actualiser le suivi', 'actualiser') + ' ' + lien(base, 'Voir les résultats') + '</p>'
    return content


def receipt_observations(item):
    """Motif et consommation transmis par essai, de l'origine à la dernière reprise ; une absence reste inconnue"""
    lines = []
    for number, observed in enumerate(item.get('observations', [])):
        usage = observed['usage']
        lines.append(('Essai d’origine' if number == 0 else 'Reprise ' + str(number))
                     + ' · motif de fin transmis : ' + (_finish_label(observed['finish_reason']) if observed['finish_reason'] else 'inconnu')
                     + (' (précision transmise : ' + _finish_label(observed['finish_detail']) + ')' if observed['finish_detail'] else '')
                     + ' · consommation transmise : ' + (', '.join(
                         _usage_label(key) + ' ' + ('inconnue' if amount is None else str(amount) if type(amount) in (int, str) else encode(amount))
                         for key, amount in _dotted_pairs(usage)) if usage else 'inconnue'))
    return '<details><summary>Observations techniques du reçu</summary>' + listing(lines) + '</details>' if lines else ''


USAGE_LABELS: dict[str, str] = {'prompt_tokens': 'entrée', 'input_tokens': 'entrée', 'completion_tokens': 'sortie', 'output_tokens': 'sortie',
                'total_tokens': 'total', 'reasoning_tokens': 'dont raisonnement', 'cached_tokens': 'dont lus en cache',
                'cache_read_input_tokens': 'dont lus en cache', 'cache_creation_input_tokens': 'dont écrits en cache',
                'cost': 'coût'}


# Motifs natifs d'OpenRouter, d'Anthropic et de Responses, comme les lit `recovery.observation`
FINISH_LABELS: dict[str, str] = {
    'stop': 'fin normale', 'end_turn': 'fin normale', 'completed': 'fin normale',
    'length': 'longueur maximale atteinte', 'max_tokens': 'longueur maximale atteinte',
    'max_output_tokens': 'longueur maximale atteinte', 'incomplete': 'réponse incomplète',
    'content_filter': 'filtre de contenu', 'refusal': 'refus du modèle', 'tool_calls': 'appel d’outil',
    'error': 'erreur du fournisseur'}


def _finish_label(reason: str) -> str:
    """Motif de fin en mots ; un motif inconnu reste cité tel que transmis"""
    return FINISH_LABELS.get(reason, '« ' + reason + ' »')


def _usage_label(key: str) -> str:
    """Libellé d'une consommation ; une clé inconnue reste citée telle que transmise"""
    last = key.rsplit('.', 1)[-1]
    return USAGE_LABELS.get(last, '« ' + key + ' »')


def _dotted_pairs(value, prefix=''):
    """Paires `chemin.clé`, valeur d'un objet imbriqué, dans l'ordre transmis"""
    for key, item in value.items():
        if type(item) is dict and item:
            yield from _dotted_pairs(item, prefix + key + '.')
        else:
            yield prefix + key, item


PREVIEW_LENGTH = 200

CRITERION_STATES = {
    'obligation': {'PASS': ('ok', 'Respectée'), 'FAIL': ('ko', 'Non respectée'), 'INDETERMINE': ('inc', 'Non vérifiable')},
    'eliminatory': {'PASS': ('ok', 'Évitée'), 'FAIL': ('ko', 'Commise'), 'INDETERMINE': ('inc', 'Non vérifiable')},
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


def _proof(name, passage, target):
    """Extrait cité, ou omission : la réponse examinée sans passage (null), jamais un faux extrait"""
    content = ('<details><summary>Absence constatée dans la réponse du modèle</summary><p>Aucun extrait cité : '
               'selon le juge, l’élément exigé manque dans cette réponse.</p>' if passage is None else
               '<details><summary>Extrait de ' + text(name) + '</summary><pre>' + text(passage) + '</pre>')
    return content + '<p><a href="' + text(target) + '">Ouvrir la pièce</a></p></details>'


def _criteria_list(record, criteria, kind):
    """Une ligne `.preuve` par critère, suivie de celles de ses éléments quand l'obligation est composée"""
    found = _states(record)
    content = ''
    for criterion in criteria:
        elements = criterion.get('elements', [])
        # Une obligation composée montre l'état et les constats de chacun de ses éléments
        content += _check(record, kind, criterion_state(found, criterion['id']), criterion['description'],
                          [] if elements else [f for f in record['findings'] if f['criterion_id'] == criterion['id']])
        content += ''.join(
            _check(record, kind, found.get((criterion['id'], e['id']), 'INDETERMINE'), e['description'],
                   [f for f in record['findings'] if (f['criterion_id'], f['control_id']) == (criterion['id'], e['id'])])
            for e in elements)
    return content


def _check(record, kind, state, description, findings):
    """Ligne d'état : le tampon porte son mot, puis le critère, puis ses constats entre guillemets et leurs extraits"""
    tone, label = CRITERION_STATES[kind][state]
    content = '<div class="preuve">' + tampon(tone, label) + '<div><p><b>' + text(description) + '</b></p>'
    for finding in findings:
        content += '<blockquote>' + text(_readable(record, finding['finding']))
        if finding['attribution'] not in ('candidate', 'evidence'):
            content += (' <span class="petit">(ce constat provient de la référence de vérification et non de la réponse du modèle)</span>'
                        if finding['attribution'] == 'reference' else
                        ' <span class="petit">(attribué à : ' + text(finding['attribution']) + ')</span>')
        content += '</blockquote>'
        for proof in finding['evidence']:
            link, target = _proof_anchor(record, proof['piece_id'])
            if link is None:
                continue
            content += _proof('la réponse du modèle' if link['piece_id'] == record['output_piece_id'] else link['name'],
                              proof['passage'], target)
    return content + '</div></div>'


CALIBRATION_SENTENCES = {
    'SANS_ETALONNAGE': 'Aucun étalonnage enregistré pour ce juge et cette méthode.',
    'NON_APPLICABLE': 'Un étalonnage existe pour une autre version ou un autre juge ; il ne s’applique pas à ce résultat.',
    'HORS_PERIMETRE': 'Un étalonnage existe pour ce juge et cette méthode, mais son périmètre ne couvre pas ce cas d’usage ; il ne s’applique pas à ce résultat.',
    'SANS_JUGE_ASSISTE': 'Cette vérification n’a pas été faite par un juge assisté : aucun étalonnage de juge ne s’applique.',
    'IDENTITE_ILLISIBLE': 'L’identité du juge de cette évaluation est illisible : aucun étalonnage ne peut lui être rattaché.',
    'FICHES_ILLISIBLES': 'Les fiches d’étalonnage ne sont pas lisibles : aucune ne peut être rattachée à ce résultat.'}


def render_calibration(scope):
    """Portée de l'étalonnage du juge pour ce résultat : une phrase d'état, jamais un badge ni une certification"""
    content = '<div class="calibration">'
    decision = scope['decision']
    if decision is None:
        return content + '<p>' + text(CALIBRATION_SENTENCES[scope['state']]) + '</p></div>'
    dated = 'Décision du ' + jour_lisible(decision['decided_at']) + '.'
    if scope['state'] == 'QUALIFIEE':
        content += '<p>Méthode qualifiée pour le périmètre déclaré : ' + text('« ' + decision['perimeter']['description'] + ' »') + '. Ce cas d’usage fait partie de ce périmètre. ' + text(dated)
        content += ' Cette qualification ne vaut que pour cette version de méthode et ce juge.'
        content += '</p>'
    else:
        content += '<p>Étalonnage examiné : qualification non retenue pour ce juge et cette méthode. ' + text(dated) + '</p>'
        content += '<p>Périmètre examiné : ' + text(decision['perimeter']['description']) + '</p>'
    identity = decision['judge_identity']
    content += '<details><summary>Limites et fiche d’étalonnage</summary>' + listing(decision['limits']) + readable_fields({
        'Décision': decision['decision_id'], 'Autorité': decision['authority'],
        'Méthode': identity['method']['id'] + ' ' + identity['method']['version'],
        'Juge demandé': identity['model'], 'Juge servi': identity['observed'],
        'Lot': decision['batch']['batch_id']}) + '</details>'
    return content + '</div>'


def _excerpt(output):
    """Début de la réponse : les premières lignes, puis « … » quand la suite reste dans la réponse complète"""
    lines = output.strip().splitlines()
    shown = '\n'.join(lines[:7])[:PREVIEW_LENGTH * 2].rstrip()
    return shown + ('\n…' if len(shown) < len(output.strip()) else '')


def _measure_release(measure):
    """Relevé d'une mesure chiffrée avec son unité : inconnue, elle se lit « inconnu », jamais zéro"""
    unit = measure['unit']
    label = measure['definition']['measure']
    if measure['status'] == 'UNKNOWN' or measure['value'] is None:
        return (label, None, '', 'l’un de vos critères')
    shown = valeur_mesure(measure['value'], unit)
    suffix = ' ' + str(unit)
    return (label, shown[:-len(suffix)] if shown.endswith(suffix) else shown, unit if shown.endswith(suffix) else '', 'l’un de vos critères')


def render_result(record, names=None, place=None):
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
    name = model_name(configuration, names or {})
    ton, _, verdict_label = VERDICT_BADGES.get(verdict, ('inc', 'question', str(verdict)))
    contexte = puce(effort_label(configuration)) + (puce(configuration['model']) if name != configuration['model'] else '')
    content = ('<div id="evaluation-' + text(eid) + '" class="pile-l"><section class="verdict-tete carte" aria-labelledby="t-modele-' + text(eid)
               + '"><div><span class="etiq">' + text(place or 'Résultat sur cet exemple') + '</span><h2 id="t-modele-' + text(eid) + '">'
               + text(name) + '</h2><div class="contexte">' + contexte + '</div></div>' + tampon(ton, verdict_label, xl=True) + '</section>')
    cost = record['candidate_cost']
    judged = record['judgment']['cost']
    releve = [('Coût observé', montant_lisible(cost['amount']) if cost and cost['status'] == 'KNOWN' else None,
               cost['currency'] if cost and cost['status'] == 'KNOWN' else '', cost['source'] if cost and cost['status'] == 'KNOWN' else '')]
    releve += [_measure_release(measure) for measure in record['measures'] if str(measure['unit']).lower() not in ('bool', 'boolean', 'booléen')]
    releve.append(('Coût de la vérification', montant_lisible(judged['amount']) if judged['status'] == 'KNOWN' else None,
                   judged['currency'] if judged['status'] == 'KNOWN' else '', 'compté à part'))
    content += releves(releve)
    if verdict is None and record['decision'].get('next_action'):
        content += '<p>' + text(record['decision']['next_action']) + '</p>'
    # Ce que le modèle a produit
    output_id = record['output_piece_id']
    link, target = _proof_anchor(record, output_id) if output_id else (None, None)
    output = record.get('proof_contents', {}).get(output_id) if output_id else None
    answer = '<section class="pile" aria-labelledby="t-rep-' + text(eid) + '"><h2 id="t-rep-' + text(eid) + '">La réponse du modèle</h2>'
    if output is not None:
        answer += '<div class="sortie"><pre>' + text(_excerpt(output)) + '</pre></div>'
        answer += tiroir('Lire la réponse complète', '<div class="sortie"><pre>' + text(output) + '</pre></div>',
                         resume=_plural(len(output), 'caractère'), ident='proof-' + eid + '-' + output_id)
    elif link is not None:
        answer += '<p><a href="' + text(link['href']) + '">' + text(link['name']) + '</a></p>'
    else:
        answer += '<p>Aucune réponse n’a été enregistrée pour ce modèle.</p>'
    answer += '</section>'
    # Pourquoi ce verdict
    found = _states(record)
    obligations = [criterion_state(found, c['id']) for c in spec['obligations']]
    eliminatory = [criterion_state(found, c['id']) for c in spec['eliminatory_errors']]
    summary = [_plural(obligations.count('PASS'), 'exigence') + ' sur ' + str(len(obligations)) + ' ' + _plural(obligations.count('PASS'), 'respectée', number=False)]
    if obligations.count('FAIL'):
        summary.append(_plural(obligations.count('FAIL'), 'non respectée'))
    if obligations.count('INDETERMINE'):
        summary.append(_plural(obligations.count('INDETERMINE'), 'non vérifiable'))
    why = '<section class="carte pile" aria-labelledby="t-preuves-' + text(eid) + '"><h2 id="t-preuves-' + text(eid) + '">Exigences et preuves</h2>'
    why += '<h3>Pourquoi ce verdict</h3><p>' + ', '.join(summary) + '.'
    if eliminatory:
        why += (' Aucune erreur éliminatoire relevée.' if all(state == 'PASS' for state in eliminatory) else
                ' ' + _plural(eliminatory.count('FAIL'), 'erreur éliminatoire relevée') + '.' if 'FAIL' in eliminatory else
                ' Les erreurs éliminatoires n’ont pas toutes pu être vérifiées.')
    why += '</p>'
    reason = _readable_reason(record)
    why += ('<p class="petit">' + text(reason) + '</p>' if len(reason) <= PREVIEW_LENGTH else
            tiroir('Explication du verdict', '<p>' + text(reason) + '</p>'))
    why += _criteria_list(record, spec['obligations'], 'obligation')
    if spec['eliminatory_errors']:
        why += '<h3>Erreurs éliminatoires recherchées</h3>' + _criteria_list(record, spec['eliminatory_errors'], 'eliminatory')
    why += '</section>'
    content += '<div class="colonnes-moitie">' + answer + why + '</div>'
    # Tiroirs : vérification, conditions de l'essai, observations, limites, pièces
    observed = record['observed_configuration'] or {}
    unknown = (None, 'INCONNU', 'UNKNOWN')
    observed_effort = observed.get('effort')
    drawers = tiroir('Vérification de la réponse', cles([
        ('Verdict', text(verdict_label)),
        ('Coût', text(montant_lisible(judged['amount']) + ' ' + judged['currency'] if judged['status'] == 'KNOWN' else 'inconnu'))])
        + '<h3>Étalonnage du juge</h3>' + render_calibration(record['calibration']))
    drawers += tiroir('Conditions de l’essai', cles([
        ('Fournisseur', text(configuration['provider'])), ('Identifiant OpenRouter', '<code>' + text(configuration['model']) + '</code>'),
        ('Raisonnement', text('demandé ' + str(configuration['effort']) + ', observé '
                              + ('inconnu' if observed_effort in unknown else str(observed_effort)))),
        ('Harnais', harnais())]))
    others = [measure for measure in record['measures'] if str(measure['unit']).lower() in ('bool', 'boolean', 'booléen')]
    if others:
        items = ''
        for measure in others:
            items += '<li>' + text(measure['definition']['measure']) + ' : '
            items += 'non observée' if measure['status'] == 'UNKNOWN' else readable_fields(measure['value'])
            items += '</li>'
        drawers += tiroir('Autres observations', '<p class="petit">Elles complètent le verdict sans former une note globale.</p><ul>' + items + '</ul>')
    limits = list(dict.fromkeys(record['limits']))
    if limits:
        drawers += tiroir('Limites de ce résultat', listing(limits))
    references = {piece['id'] for piece in record['qualification']['contract']['reference_pieces']}
    pieces = ''
    for piece in record['proof_links']:
        if piece['piece_id'] == output_id:
            continue
        role = 'Référence réservée à la vérification des réponses' if piece['piece_id'] in references else 'Pièce fournie au modèle'
        pieces += '<li><span class="petit">' + role + '</span> '
        if piece['piece_id'] in record.get('proof_contents', {}):
            pieces += tiroir('Lire la pièce complète : ' + piece['name'],
                             '<div class="sortie"><pre>' + text(record['proof_contents'][piece['piece_id']]) + '</pre></div>',
                             ident='proof-' + eid + '-' + piece['piece_id'])
        else:
            pieces += '<a href="' + text(piece['href']) + '">' + text(piece['name']) + '</a>'
        pieces += '</li>'
    drawers += tiroir('Pièces de l’exemple', '<ul>' + pieces + '</ul>')
    return content + '<div>' + drawers + '</div></div>'


def render_attempt_detail(value):
    """Page privée du détail ; la modale n'en extrait que `#attempt-detail`"""
    history = value['history']
    names = value.get('model_names', {})
    # Exemple et essai de la réponse, dans le fragment que la modale extrait aussi
    where = value['place']
    place = [example_label(where['case'], where['number'])] if where['examples'] > 1 else []
    if history[-1].get('repetition'):
        place.append('Essai ' + str(history[-1]['repetition']) + ' sur ' + str(where['repetitions']))
    content = '<div id="attempt-detail">' + render_result(history[-1], names, ' · '.join(place))
    for record in reversed(history[:-1]):
        content += tiroir('Ancienne vérification, remplacée le ' + date_lisible_utc(record['created_at']), render_result(record, names))
    # Hors de `#attempt-detail` : dans la modale, la page des résultats porte déjà cette portée et cette couverture
    conclusion, coverage = value['conclusion'], value['coverage']
    content += '</div><p class="petit">' + text(conclusion['attribution'] + ' ' + conclusion['limits'][0]) + '</p>'
    return content + '<p class="petit">Benchmark : réponses vérifiées : ' + text(coverage['evaluated_attempts']) + ' · essais lancés : ' + text(
        coverage['attempted_cells']) + ' sur ' + text(coverage['planned_cells']) + '.</p>'


# Motifs d'arrêt posés par l'acquisition, le juge et l'exécuteur ; un motif passé à `runtime.stop` reste cité
STOP_LABELS: dict[str, str] = {
    'ACQUISITION_EVIDENCE_INCOMPLETE': 'preuves de l’appel incomplètes',
    'ACQUISITION_NOT_SENT': 'appel non envoyé',
    'ACQUISITION_RECEIPT_NOT_VERIFIED': 'reçu de l’appel non vérifié',
    'ACQUISITION_STOPPED_BEFORE_EMISSION': 'arrêt avant l’envoi de l’appel',
    'OPERATOR_STOP': 'arrêt par l’équipe Evaly',
    'JUDGMENT_STOPPED': 'vérification des réponses arrêtée',
    'PROCESS_STARTED_ADMISSION_BLOCKED': 'redémarrage du service'}


# Incidents posés par les transports et l'acquisition ; un code inconnu reste cité
INCIDENT_LABELS: dict[str, str] = {
    'KEY_REJECTED': 'clé refusée par le fournisseur', 'CREDIT_EXHAUSTED': 'crédit épuisé',
    'RATE_LIMITED': 'limite de débit atteinte', 'PROVIDER_ERROR': 'erreur du fournisseur',
    'PROVIDER_RESPONSE_INCOMPLETE': 'réponse incomplète du fournisseur',
    'PROVIDER_ROUTE_MISMATCH': 'route du fournisseur différente de celle demandée',
    # Les transports posent ce code aussi quand la réponse ne nomme aucun modèle
    'MODEL_IDENTITY_MISMATCH': 'identité du modèle non confirmée (absente ou différente de celle demandée)',
    'CONTENT_REFUSAL': 'refus du modèle', 'EMPTY_OUTPUT': 'réponse terminée sans texte',
    'ROUTE_ERROR': 'fournisseur indisponible ou limite de débit atteinte',
    'CONNECTION_FAILED': 'connexion impossible', 'HARNESS_ERROR': 'erreur du harnais'}
EMISSION_LABELS: dict[str, str] = {'ESTABLISHED': 'envoyé', 'NOT_SENT': 'pas envoyé', 'UNKNOWN': 'envoi incertain'}


def render_campaign_records(campaigns, url, value):
    """Benchmarks enregistrés d'un cas d'usage, cellules et tentatives comprises"""
    content = '<p class="petit">Journal privé des benchmarks de ce cas, sur un exemple inventé. '
    content += 'Il enregistre ce que chaque appel a produit et coûté. Il ne juge pas les réponses : les verdicts figurent dans les vérifications des réponses.</p>'
    technical = {'NOT_STARTED': 'Pas lancé', 'INTENT_RECORDED': 'Lancement enregistré, appel en attente d’envoi',
                 'EMISSION_POSSIBLE': 'Appel peut-être envoyé, réponse attendue',
                 'AMBIGUOUS': 'Résultat de l’appel inconnu : pas de nouvel essai automatique', 'RECEIVED': 'Réponse et coût enregistrés'}
    for campaign in campaigns:
        task = campaign['task']
        content += '<article><h3>Benchmark ' + text(campaign['campaign_id']) + '</h3>'
        if campaign.get('recovery_of'):
            content += '<p>Reprise de l’essai ' + text(campaign['recovery_of']) + ' après un problème technique. Les réponses et coûts déjà enregistrés sont conservés.</p>'
        if 'evaluations' in campaign:
            content += '<p><a href="' + text(url) + '/campaigns/' + text(campaign['campaign_id']) + '">Voir les résultats de ce benchmark</a></p>'
        numero = numero_version(value, task['revision'])
        content += '<p>Exemple validé ' + text(task['version']) + (', version ' + str(numero) if numero else '') + '.</p>'
        content += '<h4>Modèles et réglages demandés</h4><ul>' + ''.join(
            '<li>' + text(f'{c["id"]} : {c["model"]}, révision {c["revision"]}, fournisseur {c["provider"]}, '
                          f'accès {c["access"]}, canal {c["channel_id"]}, route {c["route"]}, effort {c["effort"]}.')
            + readable_fields({'parameters': c['parameters']}) + '<p>' + text(
                'Observations exigées : ' + ', '.join(FIELD_LABELS.get(str(k), str(k)) for k in c['required_observations'])) + '</p></li>'
            for c in campaign['panel']) + '</ul>'
        conditions = campaign['conditions']
        content += '<h4>Harnais</h4><p>' + harnais(', fixée le ' + jour_lisible(conditions['frozen_at'])) + '</p>'
        content += '<details><summary>Contexte et environnement communs</summary>' + readable_fields(
            {k: conditions[k] for k in ('packages', 'tools', 'skills', 'defaults', 'environment')}) + '</details>'
        content += '<h4>Essais et budget</h4><p>' + (
            'Essais pouvant être lancés : ' + text(', '.join(campaign['allowed_cells'])) + '.' if campaign['admission_open'] else
            'Aucun nouvel essai ne peut être lancé pour ce benchmark.') + '</p>'
        if campaign['restore_pending']:
            content += '<p>Service momentanément indisponible, réessayez plus tard.</p>'
        if campaign['stop_reason']:
            content += '<p>Motif d’arrêt : ' + text(STOP_LABELS.get(campaign['stop_reason'], '« ' + campaign['stop_reason'] + ' »')) + '.</p>'
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
            ', '.join(f'{cell} : {amount}' for cell, amount in campaign['reserve_amounts'].items())
            if campaign['reserve_amounts'] else 'inconnu') + '.</p>'
        content += '<p>Mode de calcul du coût :</p>' + readable_fields(campaign['cost_basis'])
        content += '<p>Le montant mis de côté ne garantit pas un plafond de facturation. La préparation et la vérification des réponses sont comptées séparément.</p>'
        content += '<h4>Essais prévus</h4>' + listing(
            f'{c["cell_id"]} : cas {c["case_id"]}, modèle {c["configuration_id"]}, {technical[c["state"]]}'
            for c in campaign['cells'])
        for attempt in campaign['attempts']:
            content += '<details><summary>Essai ' + text(attempt['operation_id']) + ' : ' + text(technical[attempt['state']]) + '</summary>'
            content += '<p>Exécution ' + text(attempt['execution_id']) + ', essai prévu ' + text(attempt['cell_id']) + '.</p>'
            content += '<p>Lancement enregistré : ' + text(attempt['created_at']) + '. Appel envoyé : ' + text(attempt['emitted_at'] or 'pas envoyé')
            content += '. Réponse reçue : ' + text(attempt['received_at'] or 'inconnue') + '.</p>'
            content += '<p>Justificatif : ' + text(attempt['receipt_id'] or 'aucun') + '. Envoi de l’appel : ' + text(EMISSION_LABELS.get(attempt['emission'], '« ' + str(attempt['emission']) + ' »')) + '.</p>'
            content += '<p>Réglages constatés :</p>' + readable_fields(attempt['observed_configuration'])
            content += '<p>D’où viennent ces constats :</p>' + readable_fields(attempt['observation_sources'])
            cost = attempt['observed_cost']
            content += '<p>Coût observé : ' + text('inconnu' if cost is None or cost['status'] == 'UNKNOWN' else cost['amount'] + ' ' + cost['currency'])
            content += '. Source : ' + text(cost['source'] if cost else 'inconnue') + '.</p>'
            if attempt['incident']:
                content += '<p>Incident technique : ' + text(INCIDENT_LABELS.get(attempt['incident'], '« ' + attempt['incident'] + ' »')) + '.</p>'
            if attempt['attribution_incident']:
                content += '<p>Impossible de confirmer que cette réponse vient du modèle demandé : ' + text(', '.join(field_label(f) for f in attempt['attribution_incident'])) + '.</p>'
            evaluations = [e for e in campaign.get('evaluations', []) if e['attempt_id'] == attempt['operation_id']]
            if evaluations:
                content += render_evaluations(evaluations, url)
            else:
                content += '<p>Cette réponse n’a pas été évaluée.</p>'
            content += '</details>'
        content += '</article>'
    if not campaigns:
        content += '<p>Aucun benchmark lancé pour ce cas d’usage.</p>'
    return tiroir('Historique détaillé des benchmarks', content, resume=_plural(len(campaigns), 'benchmark') if campaigns else 'Aucun benchmark')
