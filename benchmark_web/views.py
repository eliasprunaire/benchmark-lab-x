"""Rendu HTML du parcours privé : formulaires natifs, preuves inertes, gabarit unique.

Ce module ne touche ni au stockage, ni aux secrets, ni aux fournisseurs : il met en
forme les vues structurées renvoyées par l'exécuteur.
"""
from pathlib import Path
import re
import secrets

from benchmark.preparation import binding
from benchmark.storage import _strict_json as encode

from .campaign_views import (COMPARISON_FOCUS_SCRIPT, campaign_status, CUSTOM_MODELS_SCRIPT, render_attempt_detail, render_campaign_records,
                             render_campaign_launch_operator, render_campaign_launch_requester,
                             render_comparison, render_configurations, render_campaign_models, campaign_followup)
from .fragments import (ACCESS_REASONS, access_summary, date_lisible_utc, form, icon, listing, montant_lisible,
                        personal_key_form, section, state_block, text)
from .projection import candidate_names, piece_name, projection_body
from .legal_views import LEGAL_PAGES
from .privacy_views import (PRIVACY_SCRIPT, render_privacy_page, render_privacy_controls,
                            render_contribution, render_contributions, render_bootstrap)

TEMPLATE_PATH = Path(__file__).with_name('templates') / 'preparation.html'
STYLESHEET_PATH = Path(__file__).with_name('static') / 'preparation.css'
FONTS_PATH = Path(__file__).with_name('static') / 'fonts'
# Fixé par `serve_web` sous identité de release : sans lui, chaque rendu relit le gabarit
TEMPLATE = None
SOURCE_SHA = ''
# Fixé par `serve_web` depuis `release.json` : sans lui, le pied de page ne donne que la révision
RELEASE_VERSION = None
REPOSITORY_URL = 'https://github.com/eliasprunaire/benchmark-lab-x'
# Fixé par `serve_web` depuis `--public-url` : sans origine publique, aucune adresse canonique ni plan du site
PUBLIC_URL = None
# Pages indexables et leur description ; une page légale y entre par `LEGAL_PAGES` (BX-21)
PUBLIC_PAGES = {
    '/': 'Décrivez une tâche de votre travail : Bench-X prépare avec vous un exemple entièrement inventé, puis '
         'compare les modèles dans les mêmes conditions, coût observé compris.',
    **{path: description for path, (_, description, _) in LEGAL_PAGES.items()},
}
# Piège à robots : `hidden` le retire du rendu et de l'arbre d'accessibilité même sans feuille de style
HONEYPOT = ('<div class="website" hidden aria-hidden="true"><label for="website">Site web</label>'
            '<input id="website" name="website" autocomplete="off" tabindex="-1"></div>')
REQUEST_FIELDS = (
    '<label for="request">Votre tâche</label><p id="request-help" class="hint">Dites ce que vous faites et ce que vous attendez du modèle, '
    'en 40 caractères au moins. N’indiquez ni donnée personnelle ni information confidentielle, et ne collez aucun document réel, même anonymisé.</p>'
    '<textarea id="request" name="request" required minlength="40" maxlength="1500" rows="5"{request_attrs}>{request}</textarea>{request_error}'
    '<label for="useful">Résultat attendu <span class="hint">(facultatif)</span></label>'
    '<textarea id="useful" name="useful" maxlength="800" rows="3"{useful_attrs}>{useful}</textarea>{useful_error}'
    '<label for="context">Contexte <span class="hint">(facultatif)</span></label>'
    '<textarea id="context" name="context" maxlength="200" rows="2"{context_attrs}>{context}</textarea>{context_error}')
PREPARATION_PROGRESS_SCRIPT = """(() => {
  const destination = document.getElementById('campaign-followup')?.dataset?.resultsHref;
  if (destination) { location.replace(destination); return; }
  const panel = document.getElementById('preparation-progress');
  const link = panel.querySelector('a');
  const status = panel.querySelector('[role="status"]');
  const pause = panel.querySelector('button');
  // Un échec isolé ne coupe pas le suivi : réessai à 8 s puis 16 s (plafond 30 s), arrêt au troisième échec consécutif
  let stopped = false, timer, request, failures = 0;
  function stop(message) {
    stopped = true;
    clearTimeout(timer);
    request?.abort();
    status.textContent = message;
    panel.querySelector('progress').hidden = true;
    pause.hidden = true;
    link.hidden = false;
  }
  async function refresh() {
    if (stopped) return;
    let delay = 4000;
    if (!document.hidden) {
      request = new AbortController();
      const timeout = setTimeout(() => request.abort(), 10000);
      try {
        const response = await fetch(link.href, {headers: {Accept: 'text/html'},
          cache: 'no-store', redirect: 'error', signal: request.signal});
        if (!response.ok) {
          console.error('FOLLOWUP_HTTP_ERROR', response.status);
          throw new Error('unavailable');
        }
        const next = new DOMParser().parseFromString(await response.text(), 'text/html');
        if (!stopped && !next.getElementById('preparation-progress')) {
          const followup = next.getElementById('campaign-followup');
          console.info('FOLLOWUP_COMPLETE', response.status);
          location.replace(followup?.dataset?.resultsHref || link.href);
          return;
        }
        if (!stopped) {
          const current = document.getElementById('campaign-status');
          const updated = next.getElementById('campaign-status');
          // Zone `aria-live` : la réécrire à l'identique la ferait annoncer de nouveau
          if (current && updated && current.innerHTML !== updated.innerHTML) current.replaceChildren(...updated.childNodes);
          failures = 0;
          console.info('FOLLOWUP_ACTIVE', response.status);
        }
      } catch {
        failures += 1;
        console.error('FOLLOWUP_UNAVAILABLE', failures);
        if (failures >= 3 && !stopped) stop('La mise à jour automatique s’est interrompue. Actualisez la page pour voir où en est votre cas d’usage.');
        delay = Math.min(4000 * 2 ** failures, 30000);
      } finally {
        clearTimeout(timeout);
      }
    }
    if (!stopped) timer = setTimeout(refresh, delay);
  }
  pause.hidden = false;
  link.hidden = true;
  pause.addEventListener('click', () => stop('Mise à jour automatique arrêtée. Actualisez la page quand vous le souhaitez.'));
  document.addEventListener('input', () => stop('Mise à jour automatique arrêtée pour ne pas effacer ce que vous écrivez.'), {once: true});
  window.addEventListener('pagehide', () => stop('Mise à jour arrêtée.'), {once: true});
  status.textContent = 'Cette page se met à jour d’elle-même. La consulter ne lance aucun nouvel appel aux modèles.';
  timer = setTimeout(refresh, 4000);
})();"""


def preparation_pending(value):
    qualification = value.get('qualification', {})
    return (value.get('stage') in ('waiting', 'preview')
            and value.get('revision') == value.get('current_revision', value.get('revision'))
            and not value.get('checks', {}).get('out_of_scope')
            and (value['stage'] == 'waiting' or bool(value.get('validation'))
                 and 'operation_id' in qualification and qualification.get('status') == 'PENDING'))


STEP_SCRIPT = """(() => {
  const initial = document.querySelector('.steps [aria-current]');
  function selectStep() {
    const section = ['#besoin', '#exemple', '#validation'].includes(location.hash);
    document.querySelectorAll('.steps a').forEach(link => {
      const selected = section ? new URL(link.href).hash === location.hash : link === initial;
      if (selected) link.setAttribute('aria-current', 'step');
      else link.removeAttribute('aria-current');
    });
  }
  window.addEventListener('hashchange', selectStep);
  selectStep();
})();"""


def page_script(value):
    if value.get('kind') == 'campaign_launch' and value['campaign']['attempts']:
        active, ready, _ = campaign_followup(value['campaign'])
        return PREPARATION_PROGRESS_SCRIPT if active or ready else None
    if preparation_pending(value):
        return PREPARATION_PROGRESS_SCRIPT
    if value.get('kind') == 'configurations':
        return CUSTOM_MODELS_SCRIPT
    if 'revision' in value:
        return STEP_SCRIPT
    return COMPARISON_FOCUS_SCRIPT if value.get('kind') == 'comparison' else None


BENCHMARK_REFERENCES = {
    'math': (('MathArena', 'https://matharena.ai/', 'Raisonnement mathématique et problèmes de compétition'),),
    'coding': (('LiveCodeBench', 'https://livecodebench.github.io/', 'Exercices de programmation'),
               ('SWE-bench', 'https://www.swebench.com/', 'Résolution de problèmes logiciels dans des dépôts de code')),
}


def render_task_index(task):
    content = '<p><a href="' + text(task['href']) + '">' + text(task['need']) + '</a></p>'
    content += '<p>Visible par vous seul : rien n’est publié dans le catalogue public.</p>'
    content += '<p>Versions : ' + ' · '.join(
        '<a href="' + text(task['href']) + '/revisions/' + str(revision) + '">' + str(revision) + '</a>'
        for revision in task['revisions']) + '.</p>'
    for version in task['versions']:
        content += '<section id="version-' + text(version['version']) + '"><h3>Exemple validé '
        content += text(version['version']) + '</h3>'
        content += '<p><a href="' + text(task['href']) + '/revisions/' + str(version['revision']) + '">Voir cet exemple</a></p><ul>'
        # Le numéro distingue deux comparaisons figées dans la même seconde
        for number, campaign in enumerate(version['campaigns'], 1):
            content += ('<li><a href="' + text(campaign['href']) + '">Comparaison ' + str(number) + ' du '
                        + text(date_lisible_utc(campaign['frozen_at'])) + '</a></li>')
        content += '</ul>' if version['campaigns'] else '</ul><p>Aucune comparaison lancée avec cet exemple.</p>'
        content += '</section>'
    if not task['versions']:
        content += '<p>Aucun exemple validé pour l’instant.</p>'
    return content


def preparation_steps(value):
    kind = value.get('kind')
    if kind not in ('configurations', 'campaign_launch', 'comparison', 'campaign_models', 'attempt_detail') and 'revision' not in value:
        return ''
    dossier = '/preparation/dossiers/' + (value.get('dossier_id') or value['task']['dossier_id'])
    campaign = value.get('campaign', {})
    revision = campaign.get('task', {}).get('revision', value.get('revision'))
    reference = value.get('dossier_href') or (dossier + '/revisions/' + str(revision) if revision else dossier)
    campaigns = [c for c in value.get('campaigns', []) if c['task']['revision'] == revision]
    if not campaign and campaigns:
        campaign = campaigns[-1]
    # Résultats : la dernière comparaison lancée, même si une sélection plus récente attend son lancement
    launched = campaign if campaign.get('attempts') else next((c for c in reversed(campaigns) if c['attempts']), {})
    base = value['href'] if kind in ('comparison', 'campaign_models', 'attempt_detail') else dossier + '/campaigns/' + campaign['campaign_id'] if campaign else None
    downstream = kind in ('configurations', 'campaign_launch', 'comparison', 'campaign_models', 'attempt_detail')
    example = downstream or bool(value.get('package'))
    models = downstream or value.get('qualified') or bool(campaign)
    ran = kind in ('comparison', 'campaign_models', 'attempt_detail') or bool(campaign.get('attempts'))
    results = ran or bool(launched)
    current = (5 if kind in ('comparison', 'attempt_detail') or kind == 'campaign_launch' and ran else 4 if downstream
               else 5 if ran else 4 if campaign else 3 if value.get('validation') or value.get('qualified') else 2 if example else 1)
    models_href = base + ('/configurations' if ran else '/conditions') if base else dossier + '/configurations'
    if not downstream and not ran and value.get('qualified') and revision == value.get('current_revision', revision):
        models_href = dossier + '/configurations'
    results_href = base
    if launched:
        # Le suivi seulement tant qu'aucune évaluation n'est lisible ; les résultats dès la première
        results_href = dossier + '/campaigns/' + launched['campaign_id']
        active, ready, _ = campaign_followup(launched)
        judgment = launched.get('judgment')
        if active or not ready and judgment and not judgment['completed']:
            results_href += '/conditions'
    # Sur la page du cas, les trois premières étapes sont un sommaire ; depuis une autre page, elles mènent
    # au haut du cas, où l'encadré d'état dit où l'on en est, jamais au milieu d'une section
    sections = [('' if downstream else '#') + anchor for anchor in ('besoin', 'exemple', 'validation')]
    targets = [reference if downstream else sections[0], (reference if downstream else sections[1]) if example else None,
               (reference if downstream else sections[2]) if example else None,
               models_href if models else None,
               (value['href'] if kind in ('comparison', 'attempt_detail') else results_href) if results else None]
    content = '<nav class="steps" aria-label="Étapes de préparation">'
    for number, (label, href) in enumerate(zip(('Besoin', 'Exemple', 'Validation', 'Modèles', 'Résultats'), targets), 1):
        inner = '<span class="n">' + str(number) + '</span>' + label
        if href is None:
            content += '<span aria-disabled="true">' + inner + '</span>'
        else:
            state = ' aria-current="step"' if number == current else ' class="done"' if number < current else ''
            content += '<a href="' + text(href) + '"' + state + '>' + inner + '</a>'
    return content + '<small>Cas d’usage privé · pièces entièrement inventées</small></nav>'


def render(value, csrf, path='/preparation', *, error=False):
    """Native HTML forms, inert evidence and a fixed comparison focus script"""
    def field_attributes(name):
        return f' aria-describedby="{text(name)}-error"' if value.get('error_field') == name else ''

    def field_error(name):
        if value.get('error_field') != name:
            return ''
        return f'<p id="{text(name)}-error" role="alert">{text(value["error"])}</p>'

    state = value.get('availability', {})
    needs_availability = 'dossiers' in value
    folded_contribution = ''
    pending = not error and preparation_pending(value)
    can_submit = state.get('can_submit', False)
    disabled = '' if can_submit else ' disabled aria-describedby="availability"'
    s9 = value.get('kind') != 'projection_preview'
    navigation = '' if error else preparation_steps(value)
    title = 'Décrire mon cas d’usage'
    # `aria-current` seulement sur l'entrée qui est la page affichée, jamais sur une page descendante ni d'erreur
    current = None if error else {'home': '/', 'privacy_data': '/preparation/data'}.get(
        value.get('kind'), '/preparation' if 'dossiers' in value and path == '/preparation' else None)
    menu = ''.join('<a href="' + href + '"' + (' aria-current="page"' if href == current else '') + '>' + label + '</a>'
                   for href, label in (('/', 'Accueil'), ('/preparation', 'Mes cas d’usage'), ('/preparation/data', 'Mes données')))
    if error:
        title = value.get('title') or ('Service momentanément indisponible' if value.get('unavailable') else 'Votre action n’a pas abouti')
        submitted = value.get('form')
        attached = (value.get('error_field') if type(submitted) is dict
                    and value.get('error_field') in submitted else None)
        content = '' if attached else '<p role="alert">' + text(value['error']) + '</p>'
        if value.get('step') == 'example_qualified' and value.get('findings'):
            content += '<p>Ce que la vérification de l’exemple a relevé</p>' + listing(
                finding['text'] for finding in value['findings'])
        if type(submitted) is dict and 'request' in submitted:
            content += form(csrf, path, {key: submitted[key] for key in ('dossier_id', 'action_id')},
                REQUEST_FIELDS.format(
                    request=text(submitted['request']), useful=text(submitted.get('useful', '')), context=text(submitted.get('context', '')),
                    request_attrs=' aria-describedby="request-help' + (' request-error' if value.get('error_field') == 'request' else '') + '"',
                    useful_attrs=field_attributes('useful'), context_attrs=field_attributes('context'),
                    request_error=field_error('request'), useful_error=field_error('useful'), context_error=field_error('context')) +
                HONEYPOT +
                '<button type="submit">Corriger et renvoyer</button>')
        elif type(submitted) is dict and 'message' in submitted:
            content += form(csrf, path, {key: submitted[key] for key in ('action_id', 'revision', 'kind')},
                '<label for="message">Votre précision ou correction</label>'
                '<textarea id="message" name="message" required maxlength="1000" rows="4"' + field_attributes('message') + '>' + text(submitted['message']) + '</textarea>' + field_error('message') +
                HONEYPOT +
                '<button type="submit">Corriger et renvoyer</button>')
        latest = value.get('step') if value.get('error_code') == 'SELECTION_SUPERSEDED' else None
        if latest and '/campaigns/' in path and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', str(latest)):
            content += ('<p><a href="' + text(path.split('/campaigns/', 1)[0] + '/campaigns/' + latest + '/conditions')
                        + '">Voir la dernière sélection</a></p>')
        back_class = 'button sec' if type(submitted) is dict and ('request' in submitted or 'message' in submitted) else 'button'
        content += '<p><a class="' + back_class + '" href="/preparation">Retrouver mes cas d’usage</a></p>'
    elif value.get('kind') == 'contributions':
        title, content = render_contributions(value)
    elif value.get('kind') == 'session_bootstrap':
        title, content = render_bootstrap(value)
    elif value.get('kind') == 'legal':
        title, _, content = LEGAL_PAGES[value['path']]
    elif value.get('kind') == 'privacy_data':
        title, content = render_privacy_page(value, csrf)
    elif value.get('kind') == 'access':
        title = 'Ma clé OpenRouter'
        status = value['status']
        if status == 'connected':
            content = state_block('done', 'Clé OpenRouter', 'Clé enregistrée', '<p>' + access_summary(value) + '</p>')
        elif status == 'invalid':
            content = state_block('err', 'Clé OpenRouter', 'Clé à remplacer', '<p>Cette clé ne peut pas être utilisée : ' + text(
                ACCESS_REASONS.get(value.get('reason'), value.get('reason') or 'raison inconnue')) + '.</p>')
        elif status == 'disconnected':
            content = state_block('action', 'Clé OpenRouter', 'Aucune clé enregistrée',
                                  '<p>Ajoutez une clé réservée à Bench-X. Elle paie les appels aux modèles : préparation de l’exemple, vérification, puis comparaison.</p>')
        else:
            content = state_block('err', 'Clé OpenRouter', 'Enregistrement de clé indisponible',
                                  '<p>Vous ne pouvez pas enregistrer de clé pour le moment. Réessayez plus tard ; aucun modèle n’a été appelé.</p>')
        if status != 'unavailable':
            content += personal_key_form(csrf, value, '/preparation/access', opened=status != 'connected')
        content += '<p><a href="/preparation">Revenir à mes cas d’usage</a></p>'
    elif value.get('kind') == 'configurations':
        title = 'Choisissez les modèles à comparer'
        content = render_configurations(value, csrf)
    elif value.get('kind') == 'campaign_models':
        title = 'Modèles de cette comparaison'
        content = render_campaign_models(value)
    elif value.get('kind') == 'attempt_detail':
        title = 'Détail et preuves'
        content = render_attempt_detail(value)
    elif value.get('kind') == 'campaign_launch' and 'checks' in value:
        title = ('Vérifier puis lancer la comparaison' if not value['campaign']['attempts'] else
                 'Benchmark en cours' if campaign_followup(value['campaign'])[0] else 'Suivi de la comparaison')
        content = render_campaign_launch_requester(value, csrf)
    elif value.get('kind') == 'campaign_launch':
        title = 'Vérifier puis lancer la comparaison'
        content = render_campaign_launch_operator(value, csrf)
    elif value.get('kind') == 'home':
        title = 'Quel modèle d’IA pour votre travail ?'
        content = '<div class="hero"><p class="lead note">Décrivez une tâche de votre travail. '
        content += 'Nous en tirons avec vous un exemple entièrement inventé, que plusieurs modèles traitent rigoureusement dans les mêmes conditions. '
        content += 'Vous voyez lesquels satisfont vos critères, preuves à l’appui, et ce que chacun a réellement coûté. N’indiquez aucune donnée personnelle ni information confidentielle.</p>'
        content += '<div class="actions"><a class="button" href="/preparation">' + icon('i-pen') + 'Décrire mon cas d’usage</a>'
        content += '<a class="button sec" href="/preparation">Retrouver mes cas d’usage</a></div></div>'
        content += section('Cinq étapes jusqu’au verdict', '<ol class="parcours">'
            '<li><strong>Besoin.</strong> Vous décrivez la tâche et le résultat qui vous serait utile. S’il manque une information, l’assistant vous pose une question.</li>'
            '<li><strong>Exemple.</strong> L’assistant rédige une consigne et des documents de travail inventés, appelés pièces. Vous les corrigez jusqu’à ce qu’ils ressemblent à votre travail réel.</li>'
            '<li><strong>Validation.</strong> Vous confirmez que c’est bien le travail à tester. Bench-X vérifie ensuite automatiquement que l’exemple est cohérent et que ses critères peuvent être contrôlés. Aucun modèle n’est encore comparé et rien n’est publié.</li>'
            '<li><strong>Modèles.</strong> Vous choisissez les modèles et leur niveau de raisonnement. Vous voyez le coût estimé, puis vous décidez de lancer la comparaison.</li>'
            '<li><strong>Résultats.</strong> Chaque modèle a traité le même exemple dans les mêmes conditions. Pour chacun, vous lisez le verdict, les preuves et le coût observé.</li></ol>')
        content += section('Comment lire un verdict', '<div class="rule">' + icon('i-scale') + '<span><strong>Un seul manquement suffit.</strong> '
            'Si le respect d’une exigence n’est pas prouvé, ou si une erreur éliminatoire apparaît, le modèle testé est écarté, quel que soit le reste.</span></div>'
            '<ul><li>Le verdict vaut pour le modèle tel qu’il a été testé ici, avec ses réglages et dans ces conditions. Il ne juge pas le modèle en général.</li>'
            '<li>Le coût comparé est celui relevé sur le justificatif renvoyé après chaque appel. Avant le lancement, vous voyez une estimation, signalée comme telle. Si un coût n’a pas pu être relevé, il est affiché comme inconnu.</li>'
            '<li>Les pièces sont entièrement inventées : aucun dossier réel, même anonymisé.</li></ul>')
    elif value.get('kind') == 'catalogue':
        title = 'Exemples validés et comparaisons'
        content = '<p class="lead">Visible par vous seul, dans ce navigateur. Pour chaque cas d’usage validé, vous retrouvez ses exemples validés et les comparaisons lancées.</p>'
        content += ''.join('<section><h2>' + text(task['need']) + '</h2>' + render_task_index(task) + '</section>' for task in value['tasks'])
        if not value['tasks']:
            content += '<p>Aucun cas d’usage validé pour l’instant. Validez un exemple pour le retrouver ici.</p>'
        content += '<p><a href="/preparation">Revenir à mes cas d’usage</a></p>'
    elif value.get('kind') == 'comparison':
        title = 'Résultats'
        content = render_comparison(value) + '<script>' + COMPARISON_FOCUS_SCRIPT + '</script>'
    elif value.get('kind') == 'projection_preview':
        title = 'Aperçu d’une publication'
        comparison = value['comparison']
        candidates = candidate_names(comparison)
        rows = {link['piece_id']: row for row in comparison['rows'] for link in row['proof_links']}
        content = ('<p class="note">Aperçu privé, non approuvé : rien n’est publié. Cette page montre ce qu’un lecteur '
                   'verrait si cette comparaison était publiée.</p>')
        content += '<p><a href="' + text(comparison['href']) + '">Revenir aux résultats</a></p>'
        content += '<form method="get" action="' + text(comparison['href'] + '/preview') + '">'
        content += '<fieldset><legend>Pièces que la publication montrerait</legend>'
        content += '<p class="hint">Si vous ne cochez aucune pièce, seuls les verdicts et leurs motifs apparaissent.</p>'
        for piece in value['pieces']:
            pid = piece['piece_id']
            content += '<label><input type="checkbox" name="piece" value="' + text(pid) + '"'
            content += (' checked' if pid in value['selected_links'] else '') + '> ' + text(piece_name(rows[pid], piece, candidates)) + '</label>'
        content += '</fieldset><button type="submit">Actualiser l’aperçu</button></form>'
        content += '<p class="hint">L’aperçu montre toute la comparaison : les filtres des résultats ne s’appliquent pas ici.</p>'
        content += '<hr>' + projection_body(comparison, value['selected_links'], level=2)
    elif 'dossiers' in value:
        title = 'Mes cas d’usage'
        access = value.get('personal_access', {})
        if access.get('status') == 'connected':
            content = ('<p class="hint">Votre clé OpenRouter est enregistrée. ' + access_summary(access)
                       + ' <a href="/preparation/access">Gérer ma clé</a></p>')
        else:
            content = personal_key_form(csrf, access, '/preparation')
        content += ('<p class="lead note">Vos cas d’usage restent privés dans ce navigateur. '
                    'Reprenez un cas existant ou décrivez-en un nouveau.</p>' if value['dossiers'] else
                    '<p class="lead note">Décrivez une tâche de votre travail et le résultat qui vous aiderait. '
                    'L’assistant en tire un exemple inventé, que vous pourrez relire et corriger avant de le valider.</p>')
        dossiers = '<ul class="dossiers">' + ''.join(
            f'<li><a href="/preparation/dossiers/{text(d["dossier_id"])}">{text(d.get("need") or "Cas d’usage sans description")}</a>'
            f'<small>Version {d["revision"]}</small></li>'
            for d in value['dossiers']) + '</ul><p><a href="/preparation/catalogue">Exemples validés et comparaisons</a></p>' if value['dossiers'] else (
                '<p>Vous n’avez encore aucun cas d’usage dans ce navigateur. Décrivez une tâche ci-dessus pour commencer, dès que la préparation est disponible.</p>'
                '<p>Vous en aviez déjà un ? Vos cas d’usage sont liés au navigateur où vous les avez créés. Ouvrez Bench-X dans ce navigateur-là ; si ses cookies ont été effacés, les cas ne peuvent plus être retrouvés.</p>')
        creation = section('Décrire un nouveau cas d’usage', form(csrf, '/preparation/dossiers',
            {'dossier_id': secrets.token_hex(16), 'action_id': secrets.token_hex(16)},
            REQUEST_FIELDS.format(request='', useful='', context='', request_error='', useful_error='', context_error='', request_attrs=' aria-describedby="request-help' + ('"' if can_submit else ' availability" disabled'),
                                  useful_attrs=disabled, context_attrs=disabled) + HONEYPOT,
            form_id='prepare-case')
            + '<button type="submit" form="prepare-case"' + disabled + '>' + icon('i-pen') + 'Préparer mon exemple</button>', 'besoin')
        listing_section = section('Enregistrés dans ce navigateur', dossiers, 'mes-cas')
        # Un visiteur qui revient cherche d'abord ses cas ; un premier visiteur, le formulaire
        content += listing_section + creation if value['dossiers'] else creation + listing_section
    elif value.get('kind') == 'honeypot_ack' or 'operation_id' in value:
        title = 'Demande enregistrée'
        url = ('/preparation' if value.get('kind') == 'honeypot_ack'
               else '/preparation/dossiers/' + value['dossier_id'])
        content = state_block('wait', 'Où j’en suis', 'L’assistant prépare sa réponse', '<p>Votre envoi est bien enregistré. Vous pouvez suivre l’avancement depuis la page de votre cas d’usage.</p>',
                              f'<a class="button" href="{text(url)}">Suivre mon cas d’usage</a>')
    else:
        dossier_id, revision = value['dossier_id'], value['revision']
        url = '/preparation/dossiers/' + dossier_id
        title = ('Votre cas d’usage' if value['validation'] else 'Est-ce le travail que vous voulez tester ?'
                 if value['package'] else 'Précisons votre besoin')
        prior_revision = revision != value.get('current_revision', revision)
        snapshot = '/revisions/' in path and any(c['task']['revision'] == revision and c['attempts']
                                                for c in value.get('campaigns', []))
        referral = value.get('checks', {}).get('out_of_scope')
        editable = not prior_revision and not snapshot and value['stage'] != 'waiting' and not referral
        needs_availability = editable
        disabled = '' if can_submit and editable else ' disabled aria-describedby="availability"'
        current_campaigns = [c for c in value.get('campaigns', []) if c['task']['revision'] == revision]
        stages = {'draft': ('unk', 'Brouillon', 'Rien n’a encore été envoyé à l’assistant.'),
                  'waiting': ('wait', 'Préparation en cours', 'L’assistant prépare votre exemple, ou une question s’il lui manque une information. Vous pouvez quitter cette page et revenir plus tard.'),
                  'clarification': ('action', 'L’assistant a une question', 'Répondez ci-dessous : l’exemple sera préparé ensuite.'),
                  'preview': ('action', 'Votre exemple est prêt', 'Lisez la consigne et les pièces, corrigez si besoin, puis validez.'),
                  'scope_confirmation': ('action', 'Précisez le travail à comparer',
                      'Bench-X compare des modèles sur un travail concret, avec un résultat attendu et des critères que l’on peut vérifier. '
                      'Précisez ou confirmez le travail que vous voulez comparer. Aucune comparaison ne peut être lancée à cette étape.'),
                  # La cause est dans l'explication ; l'envoi reste ouvert sauf motif de disponibilité affiché à part
                  'suspended': ('err', 'Préparation arrêtée', 'Renvoyez votre message pour relancer la préparation.' if can_submit
                                else 'Vous pourrez renvoyer votre message quand l’envoi sera de nouveau possible.')}
        tone, heading, next_step = stages[value['stage']]
        if referral:
            title = 'Tâche hors du champ de Bench-X'
            tone, heading, next_step = ('unk', 'Bench-X ne peut pas comparer cette tâche',
                'Bench-X compare des modèles sur des tâches de travail concrètes. '
                'Ce cas d’usage est donc arrêté et aucune comparaison ne sera lancée pour lui.')
        qualification = value.get('qualification', {})
        automatic = 'operation_id' in qualification
        if value['validation']:
            # Sans vérification automatique : l'exemple n'attend personne, il se relance par une correction
            tone, heading, next_step = (('done', 'Exemple vérifié, prêt à comparer', 'Choisissez les modèles à comparer.') if value.get('qualified')
                                        else ('unk', 'Cas d’usage validé', 'La vérification de l’exemple n’a pas pu démarrer. '
                                              'Pour la relancer, précisez ou corrigez l’exemple, puis validez-le de nouveau.')) if not current_campaigns \
                else ('done', 'Cas d’usage validé', 'Une comparaison est prête ou déjà lancée. Suivez-la à l’étape Modèles.')
        if value['validation'] and automatic:
            if value.get('qualified'):
                tone, heading, next_step = 'done', 'Exemple vérifié, prêt à comparer', 'Lisez les remarques de la vérification, puis choisissez les modèles à comparer.'
            elif qualification.get('status') == 'BLOCKED' and qualification.get('cause'):
                # Incident chez le fournisseur ou clé : l'exemple n'est pas en cause
                tone, heading, next_step = 'unk', 'Vérification impossible pour le moment', qualification['summary']
            elif qualification.get('status') == 'BLOCKED':
                tone, heading, next_step = 'err', 'Exemple à revoir avant comparaison', qualification['summary']
            elif qualification.get('cause'):
                tone, heading, next_step = 'wait', 'Vérification de l’exemple en attente', qualification['summary']
            else:
                tone, heading, next_step = 'wait', 'Vérification de l’exemple en cours', 'Votre validation est enregistrée. L’assistant vérifie que l’exemple est cohérent et que ses critères peuvent être contrôlés.'
        actions = ''
        # Une comparaison existe : l'encadré dit son état et mène à elle, jamais à un nouveau choix de modèles
        if value['validation'] and current_campaigns and not snapshot and not prior_revision:
            tone, heading, next_step, target, label = campaign_status(current_campaigns[-1], url)
            actions = f'<a class="button" href="{text(target)}">{text(label)}</a>'
        elif value['validation'] and value.get('qualified') and not snapshot and not prior_revision:
            actions = f'<a class="button" href="{text(url)}/configurations">Choisir les modèles</a>'
        content = '<p class="tag">Exemple inventé · version ' + text(revision) + '</p>'
        if snapshot:
            title = 'Exemple utilisé pour la comparaison'
            tone, heading, next_step = 'done', 'Exemple déjà testé', 'Vous consultez l’exemple tel qu’il a été testé. Ses résultats restent disponibles.'
            content += '<p class="notice">Lecture seule. Si vous modifiez l’exemple, vos changements formeront une nouvelle version, à valider de nouveau.</p>'
        if prior_revision:
            content += '<p class="notice">Ancienne version, en lecture seule. Pour modifier ou valider, ouvrez la version actuelle.</p>'
        if prior_revision:
            actions = f'<a class="button" href="{text(url)}">Ouvrir la version actuelle</a>'
        elif snapshot:
            actions = f'<a class="button sec" href="{text(url)}">Préparer une nouvelle comparaison</a>'
        if pending:
            actions = ('<div id="preparation-progress"><progress aria-label="' + text(heading) + '"></progress>'
                       '<p class="hint" role="status">Cette page se met à jour d’elle-même si JavaScript est activé. Sinon, actualisez-la de temps en temps.</p>'
                       '<div class="actions"><a href="' + text(url) + '">Actualiser</a>'
                       '<button type="button" class="sec" hidden>Arrêter la mise à jour automatique</button></div></div>')
            # Relance automatique programmée : sa cause et son délai, sans rien à renvoyer
            notice = '<p class="notice">' + text(value['notice']) + '</p>' if value.get('notice') else ''
            content += '<div id="availability">' + state_block(tone, 'Où j’en suis', heading,
                notice + '<p>' + text(next_step) + '</p>', actions) + '</div><script>' + PREPARATION_PROGRESS_SCRIPT + '</script>'
        else:
            # L'explication de l'assistant (question sur l'exemple) ne vaut que tant que l'exemple n'est pas validé
            body = '<p>' + text(next_step) + '</p>' if value['validation'] else (
                '<p>' + text(value['explanation']) + '</p><p class="hint">' + text(next_step) + '</p>')
            if value.get('notice'):
                body = '<p class="notice">' + text(value['notice']) + '</p>' + body
            content += state_block(tone, 'Où j’en suis', heading, body, actions)
        if referral:
            references = BENCHMARK_REFERENCES.get(referral, ())
            if references:
                content += section('Où comparer ce type de tâche', '<ul>' + ''.join(
                    '<li><a href="' + href + '" rel="noreferrer">' + label + '</a> : ' + description + '.</li>'
                    for label, href, description in references) + '</ul>')
            content += '<p><a class="button sec" href="/preparation">Décrire un autre cas d’usage</a></p>'
        links = [] if '>Actualiser</a>' in actions else [f'<a href="{text(path)}">Actualiser</a>']
        if path != url:
            links.append(f'<a href="{text(url)}">Version actuelle</a>')
        if revision > 1:
            links.append(f'<a href="{text(url)}/revisions/{revision - 1}">Version précédente</a>')
        if links:
            content += '<p class="hint">' + ' · '.join(links) + '</p>'
        if editable and value['package'] is None:
            content += section('Votre réponse', form(csrf, url + '/messages',
                {'action_id': secrets.token_hex(16), 'revision': revision, 'kind': 'clarify'},
                '<label for="message">Votre réponse à l’assistant</label><textarea id="message" name="message" rows="3" required maxlength="1000"' + disabled + '></textarea>' + HONEYPOT +
                '<button type="submit"' + disabled + '>Envoyer ma réponse</button>'))
        payload = value['payload']
        content += section('Votre besoin', '<p>' + text(payload['request']) + '</p>', 'besoin')
        if value.get('task_index'):
            content += '<details><summary>Historique des versions et des comparaisons</summary>' + render_task_index(value['task_index']) + '</details>'
        if value.get('message') and 'message' in value['message']:
            content += section('Votre message à l’origine de cette version', '<p>' + text(value['message']['message']) + '</p>')
        if payload['clarifications'] or payload['validated_assumptions']:
            agreements = listing(payload['clarifications']) if payload['clarifications'] else ''
            for agreement in payload['validated_assumptions']:
                if type(agreement) is dict and set(agreement) == {'question', 'answer'}:
                    agreements += '<blockquote><p>' + text(agreement['question']) + '</p>'
                    agreements += '<p><strong>Votre accord : </strong>' + text(agreement['answer']) + '</p></blockquote>'
                else:
                    agreements += '<p>' + text(encode(agreement) if type(agreement) is dict else agreement) + '</p>'
            content += section('Précisions et points convenus', agreements)
        if payload['reformulation']:
            content += section('Votre besoin reformulé par l’assistant', '<p>' + text(payload['reformulation']) + '</p>')
        if payload['fictional_parameters']:
            content += section('Éléments inventés pour l’exemple', listing(f'{k} : {v}' for k, v in payload['fictional_parameters'].items()))
        package = value['package']
        if package:
            content += section('Consigne donnée aux modèles', '<p class="consigne">' + text(package['instruction']) + '</p>', 'exemple')
            content += section('Les pièces de l’exemple', '<p class="hint">Ouvrez une pièce pour la lire ici. Refermez-la pour revenir à la suite.</p>' + ''.join(
                '<details class="example-content"><summary>Lire « ' + text(piece['name']) + ' »</summary>'
                + '<div class="example-text">' + text(value['example_contents'][piece['id']]) + '</div></details>'
                for piece in package['pieces']))
            content += '<div class="two">' + section('Ce que le modèle doit rendre', listing(package['deliverables']))
            criteria = value['criteria']
            groups = ''
            for key, tone, group_title in (('eliminatory', 'elim', 'Éliminatoires'),
                                     ('obligations', 'oblig', 'Obligations'),
                                     ('quality', 'sec', 'Qualité')):
                items = criteria[key]
                if items:
                    labels = (item['label'] for item in items) if key == 'quality' else items
                    groups += '<div class="grp ' + tone + '"><h3>' + group_title + '</h3>' + listing(labels) + '</div>'
            groups = '<div class="crit">' + groups + '</div><p class="rule"><span>Règle</span><span>' \
                     + text(value['criteria_rule']) + '</span></p>'
            content += section('Critères de réussite', groups) + '</div>'
            limits = '<h3>Ce qui restera à faire par une personne</h3><p>' + text(package['human_work']) + '</p>'
            if package['acceptable_ambiguities']:
                limits += '<h3>Ambiguïtés acceptées</h3>' + listing(package['acceptable_ambiguities'])
            if package['limits']:
                limits += '<h3>Limites de l’exemple</h3>' + listing(package['limits'])
            content += section('Limites et travail restant', limits)
            change_labels = {'instruction': 'Consigne', 'deliverables': 'Livrables', 'criteria': 'Critères',
                'acceptable_ambiguities': 'Ambiguïtés acceptées', 'pieces': 'Pièces'}
            if value['changes']:
                changes = listing(change_labels.get(change, change) for change in value['changes'])
                for kind, label in (('added', 'Pièces ajoutées'), ('removed', 'Pièces retirées'),
                                    ('modified', 'Pièces modifiées')):
                    names = value['piece_changes'][kind]
                    if names:
                        changes += '<h3>' + label + '</h3>' + listing(names)
                content += section('Ce qui a changé depuis la version précédente', changes)
        if 'indicative_cost' in value:
            estimate = value['indicative_cost']
            amount = estimate.get('token_subtotal_usd') if estimate else None
            content += '<p>Coût estimé de cette préparation : ' + text(
                'non estimable' if amount is None else montant_lisible(amount) + ' USD') + \
                '. Calcul : tokens utilisés × tarifs du modèle relevés avant l’appel. C’est une estimation, pas une facture.</p>'
        if value.get('observed_cost'):
            cost = value['observed_cost']
            content += '<p>Coût observé de cette préparation : ' + text(
                'inconnu' if cost['status'] == 'UNKNOWN' else montant_lisible(cost['amount']) + ' ' + cost['currency']) + '. Source : ' + text(cost['source']) + '.</p>'
        else:
            content += '<p>Coût observé : inconnu, faute de justificatif de coût reçu pour cette préparation.</p>'
        if value.get('cost_reconciliation'):
            proof, cost = value['cost_reconciliation'], value['effective_cost']
            content += '<p>Coût corrigé après vérification : ' + text(montant_lisible(cost['amount']) + ' ' + cost['currency']) + '. Source : ' + text(
                proof['source']) + ', confirmée par ' + text(proof['actor']) + ' le ' + text(proof['observed_at']) + \
                '. Le justificatif d’origine reste inchangé.</p>'
        if editable and package is not None:
            content += '<details class="corr"><summary class="button sec">' + icon('i-pen') + 'Préciser ou corriger cet exemple</summary><div>' + form(csrf, url + '/messages',
                {'action_id': secrets.token_hex(16), 'revision': revision},
                '<p>Dites ce qui doit changer. Ce que vous avez déjà convenu reste acquis, et les versions précédentes restent consultables. Si l’exemple change, vous devrez le valider de nouveau.</p>'
                '<label for="kind">Votre message sert à</label><select id="kind" name="kind"' + disabled + '>'
                '<option value="clarify">Répondre à l’assistant ou confirmer le travail à tester</option>'
                '<option value="correct" selected>Modifier cet exemple</option></select>'
                '<label for="message">Votre précision ou correction</label>'
                '<textarea id="message" name="message" rows="4" required maxlength="1000"' + disabled + '></textarea>' + HONEYPOT +
                '<button type="submit"' + disabled + '>Envoyer ce message</button>') + '</div></details>'
        launched = any(c['attempts'] for c in current_campaigns)
        if package:
            content += '<section id="validation"><h2>Valider l’exemple</h2>'
            if value['validation']:
                content += '<p class="note">Vous avez validé cet exemple, dans cette version précise.</p>'
            else:
                content += '<p class="note">Cet exemple n’est pas encore validé. Relisez-le, puis validez-le pour passer à la suite.</p>'
            if editable and value['stage'] == 'preview' and value['validation'] is None:
                content += '<p>En validant, vous confirmez que cet exemple correspond à votre besoin. L’exemple est ensuite vérifié automatiquement, si ce service est disponible : cette vérification est payée avec votre clé OpenRouter. Valider ne lance aucun des modèles à comparer et ne publie rien.</p>'
                content += '<div class="actionbar">' + form(csrf, url + '/validation', binding(dossier_id, revision, value['package_sha256']),
                                '<button type="submit">' + icon('i-check') + 'Oui, c’est le travail à tester</button>') + '</div>'
            content += '</section>'
            # Le consentement suit la décision principale ; une fois la comparaison lancée, il se replie dans Mes données
            if launched:
                folded_contribution = render_contribution(value, csrf)
            else:
                content += render_contribution(value, csrf)
        if current_campaigns:
            content += '<section id="comparaison"><h2>Comparaisons de cet exemple</h2><ul>'
            for number, campaign in reversed(list(enumerate(current_campaigns, 1))):
                _, heading_, _, target, _ = campaign_status(campaign, url)
                content += ('<li><a href="' + text(target) + '">Comparaison ' + str(number) + ' du '
                            + text(date_lisible_utc(campaign['conditions']['frozen_at']))
                            + '</a> : ' + text(heading_[0].lower() + heading_[1:]) + '.</li>')
            content += '</ul>'
            if value.get('qualified') and not snapshot and not prior_revision:
                content += ('<p><a href="' + text(url) + '/configurations">Choisir d’autres modèles</a> : '
                            'une nouvelle comparaison sera préparée, et les résultats actuels restent disponibles.</p>')
            content += '</section>'
        labels = {'PENDING': 'En attente', 'QUALIFIED': 'Vérification réussie',
                  'BLOCKED': 'Bloquée : la réponse de référence ou les contrôles ne sont pas assez établis'}
        if package and not referral:
            if automatic:
                # Parcours public : la qualification automatique suffit, aucune approbation opérateur n'est attendue
                content += '<details><summary>Détail de la vérification de l’exemple</summary>'
                content += section('Vérification', '<p>' + text('Vérification impossible pour le moment' if qualification.get('cause')
                                                                 else labels.get(qualification.get('qualification_status'), 'En attente')) + '</p>'
                                   + '<p>' + text(qualification['summary']) + '</p>'
                                   + listing(finding['text'] for finding in qualification.get('findings', [])))
            else:
                content += '<details><summary>Vérification de l’exemple</summary>'
                # Contrat opérateur bloqué : ses constats remplacent le libellé générique, comme sur une page de refus
                content += section('Vérification', '<p>Bloquée. Ce que la vérification de l’exemple a relevé :</p>' + listing(
                    finding['text'] for finding in qualification['findings']) if qualification.get('findings')
                    else '<p>' + text(labels.get(qualification.get('qualification_status'), 'En attente')) + '</p>')
            content += '</details>'
        if value.get('campaigns'):
            content += render_campaign_records(value['campaigns'], url)
    if state and s9 and not pending and not can_submit and needs_availability and not value.get('checks', {}).get('out_of_scope'):
        reasons = {
            'access': 'Ajoutez votre clé OpenRouter pour préparer un exemple.',
            'open': 'Vous pouvez envoyer votre demande. Chaque envoi est contrôlé avant d’être traité.',
            'unconfigured': 'Préparation indisponible : aucun assistant n’est en service pour le moment.',
            'waiting': 'Votre préparation précédente est en cours. Actualisez la page pour voir où elle en est.',
            'interrupted': 'Nous ne savons pas si le modèle a répondu. Rien n’est relancé ; cette opération sera close automatiquement.',
            'restore': 'Service momentanément indisponible, réessayez plus tard.',
            'unresolved': 'Envois fermés : le résultat ou le coût d’un appel précédent n’est pas encore connu.'}
        # L'encadré porte l'état seul (DESIGN.md) : le motif suffit, sans titre ni note de financement
        content = ('<aside id="availability" class="availability" aria-label="État de la préparation"><p>'
                   + text(reasons[state['reason']]) + '</p></aside>' + content)
    script = page_script(value) if not error else None
    if script is not None and value.get('kind') == 'campaign_launch' and value['campaign']['attempts']:
        content += '<script>' + script + '</script>'
    if script == STEP_SCRIPT:
        content += '<script>' + STEP_SCRIPT + '</script>'
    if not error:
        if value.get('kind') == 'home':
            content += '<span data-privacy-home hidden></span>'
        if 'dossiers' in value and value.get('privacy'):
            content += render_privacy_page({'kind': 'privacy_data'}, preparation=True)[1]
        content += render_privacy_controls(value, csrf, folded_contribution)
        if value.get('privacy') or value.get('kind') in ('home', 'privacy_data', 'contributions', 'session_bootstrap'):
            content += PRIVACY_SCRIPT
    template = TEMPLATE or TEMPLATE_PATH.read_text()
    body_class = 's9 comparison' if value.get('kind') == 'comparison' else 's9' if s9 else ''
    # Offre de source AGPL §13 : un numéro et un lien vers l'arbre du commit seulement sous identité de release,
    # construite depuis ce commit ; un checkout peut être modifié ou non poussé, il renvoie au dépôt
    identity = ''
    if SOURCE_SHA:
        identity = '<span>' + text(('Version : v' + RELEASE_VERSION + ' (' + SOURCE_SHA[:7] + ')') if RELEASE_VERSION
                                   else 'Révision : ' + SOURCE_SHA[:7]) + '</span>'
    identity += ('<a class="source" href="' + text(REPOSITORY_URL + ('/tree/' + SOURCE_SHA if SOURCE_SHA and RELEASE_VERSION else ''))
                 + '">' + icon('i-github') + 'Code source</a>')
    page = None if error else {'home': '/', 'legal': value.get('path')}.get(value.get('kind'))
    head = ''
    if page in PUBLIC_PAGES:
        head = '<meta name="description" content="' + text(PUBLIC_PAGES[page]) + '">'
        if PUBLIC_URL:
            head += '<link rel="canonical" href="' + text(PUBLIC_URL + page) + '">'
    return (template.replace('{{title}}', text(title)).replace('{{body_class}}', body_class).replace('{{menu}}', menu)
            .replace('{{navigation}}', navigation).replace('{{layout_class}}', 'layout' if navigation else '')
            .replace('{{identity}}', identity).replace('{{head}}', head).replace('{{content}}', content).encode('utf-8'))
