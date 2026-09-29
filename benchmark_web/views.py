"""Rendu HTML du parcours privé : formulaires natifs, preuves inertes, gabarit unique.

Ce module ne touche ni au stockage, ni aux secrets, ni aux fournisseurs : il met en
forme les vues structurées renvoyées par l'exécuteur.
"""
from pathlib import Path
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
    '<label for="request">Une tâche de votre travail</label><p id="request-help" class="hint">Décrivez le travail et le résultat '
    'utile, en 40 caractères au moins, sans donnée personnelle ni information confidentielle. Aucun dossier réel, même anonymisé.</p>'
    '<textarea id="request" name="request" required minlength="40" maxlength="1500" rows="5"{request_attrs}>{request}</textarea>{request_error}'
    '<label for="useful">Résultat attendu <span class="hint">(facultatif)</span></label>'
    '<textarea id="useful" name="useful" maxlength="800" rows="3"{useful_attrs}>{useful}</textarea>{useful_error}'
    '<label for="context">Contexte utile <span class="hint">(facultatif)</span></label>'
    '<textarea id="context" name="context" maxlength="200" rows="2"{context_attrs}>{context}</textarea>{context_error}')
PREPARATION_PROGRESS_SCRIPT = """(() => {
  const destination = document.getElementById('campaign-followup')?.dataset?.resultsHref;
  if (destination) { location.replace(destination); return; }
  const panel = document.getElementById('preparation-progress');
  const link = panel.querySelector('a');
  const status = panel.querySelector('[role="status"]');
  const pause = panel.querySelector('button');
  let stopped = false, timer, request;
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
          if (current && updated) current.replaceChildren(...updated.childNodes);
          console.info('FOLLOWUP_ACTIVE', response.status);
        }
      } catch {
        console.error('FOLLOWUP_UNAVAILABLE');
        if (!stopped) stop('Suivi automatique interrompu. Actualisez pour vérifier l’état du dossier.');
      } finally {
        clearTimeout(timeout);
      }
    }
    if (!stopped) timer = setTimeout(refresh, 4000);
  }
  pause.hidden = false;
  link.hidden = true;
  pause.addEventListener('click', () => stop('Suivi automatique suspendu. Actualisez quand vous le souhaitez.'));
  document.addEventListener('input', () => stop('Suivi automatique suspendu pour conserver votre saisie.'), {once: true});
  window.addEventListener('pagehide', () => stop('Suivi suspendu.'), {once: true});
  status.textContent = 'Suivi automatique actif. La consultation ne lance aucun nouvel appel.';
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
    if value.get('kind') == 'configurations' and value.get('personal_preparation'):
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
    content += '<p>Consultation privée, sans admission au catalogue public.</p>'
    content += '<p>Révisions : ' + ' · '.join(
        '<a href="' + text(task['href']) + '/revisions/' + str(revision) + '">' + str(revision) + '</a>'
        for revision in task['revisions']) + '.</p>'
    for version in task['versions']:
        content += '<section id="version-' + text(version['version']) + '"><h3>Version d’épreuve '
        content += text(version['version']) + '</h3>'
        content += '<p><a href="' + text(task['href']) + '/revisions/' + str(version['revision']) + '">Ouvrir la révision associée</a></p><ul>'
        # Le numéro distingue deux comparaisons figées dans la même seconde
        for number, campaign in enumerate(version['campaigns'], 1):
            content += ('<li><a href="' + text(campaign['href']) + '">Comparaison ' + str(number) + ' du '
                        + text(date_lisible_utc(campaign['frozen_at'])) + '</a></li>')
        content += '</ul>' if version['campaigns'] else '</ul><p>Aucune campagne pour cette version.</p>'
        content += '</section>'
    if not task['versions']:
        content += '<p>Aucune version d’épreuve contractuelle conservée.</p>'
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
    base = value['href'] if kind in ('comparison', 'campaign_models', 'attempt_detail') else dossier + '/campaigns/' + campaign['campaign_id'] if campaign else None
    downstream = kind in ('configurations', 'campaign_launch', 'comparison', 'campaign_models', 'attempt_detail')
    example = downstream or bool(value.get('package'))
    models = downstream or value.get('qualified') or bool(campaign)
    results = kind in ('comparison', 'campaign_models', 'attempt_detail') or bool(campaign.get('attempts'))
    current = 5 if kind in ('comparison', 'attempt_detail') or kind == 'campaign_launch' and results else 4 if downstream else 3 if value.get('validation') or value.get('qualified') else 2 if example else 1
    models_href = base + ('/configurations' if results else '/conditions') if base else dossier + '/configurations'
    if not downstream and not results and value.get('qualified') and revision == value.get('current_revision', revision):
        models_href = dossier + '/configurations'
    results_href = base
    if base is not None and campaign.get('judgment') and campaign['judgment']['status'] != 'COMPLETE':
        results_href = base + '/conditions'
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
        title = value.get('title') or ('Préparation indisponible' if value.get('unavailable') else 'Action non aboutie')
        submitted = value.get('form')
        attached = (value.get('error_field') if type(submitted) is dict
                    and value.get('error_field') in submitted else None)
        content = '' if attached else '<p role="alert">' + text(value['error']) + '</p>'
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
        title = 'Ma clé Openrouter'
        status = value['status']
        if status == 'connected':
            content = state_block('done', 'Accès Openrouter', 'Clé enregistrée', '<p>' + access_summary(value) + '</p>')
        elif status == 'invalid':
            content = state_block('err', 'Accès Openrouter', 'Clé à remplacer', '<p>Motif : ' + text(
                ACCESS_REASONS.get(value.get('reason'), value.get('reason') or 'INCONNU')) + '.</p>')
        elif status == 'disconnected':
            content = state_block('action', 'Accès Openrouter', 'Aucune clé enregistrée',
                                  '<p>Ajoutez une clé dédiée : elle finance la préparation, la qualification et la comparaison de vos cas d’usage.</p>')
        else:
            content = state_block('err', 'Accès Openrouter', 'Enregistrement indisponible',
                                  '<p>L’enregistrement de clé est momentanément indisponible. Aucun appel n’est lancé.</p>')
        if status != 'unavailable':
            content += personal_key_form(csrf, value, '/preparation/access', opened=status != 'connected')
        content += '<p><a href="/preparation">Revenir à mes cas d’usage</a></p>'
    elif value.get('kind') == 'configurations':
        title = 'Choisir les configurations'
        content = render_configurations(value, csrf)
    elif value.get('kind') == 'campaign_models':
        title = 'Modèles de cette comparaison'
        content = render_campaign_models(value)
    elif value.get('kind') == 'attempt_detail':
        title = 'Détail et preuves'
        content = render_attempt_detail(value)
    elif value.get('kind') == 'campaign_launch' and 'checks' in value:
        title = 'Suivi de la comparaison' if value['campaign']['attempts'] else 'Vérifier puis lancer la comparaison'
        content = render_campaign_launch_requester(value, csrf)
    elif value.get('kind') == 'campaign_launch':
        title = 'Examiner puis lancer la comparaison'
        content = render_campaign_launch_operator(value, csrf)
    elif value.get('kind') == 'home':
        title = 'Quel modèle pour votre travail ?'
        content = '<div class="hero"><p class="lead note">Décrivez une tâche de votre travail, sans donnée personnelle ni information confidentielle. '
        content += 'Nous préparons avec vous un exemple entièrement inventé, puis les modèles sont comparés dans les mêmes conditions, '
        content += 'sur des critères vérifiables et leur coût observé.</p>'
        content += '<div class="actions"><a class="button" href="/preparation">' + icon('i-pen') + 'Décrire mon cas d’usage</a>'
        content += '<a class="button sec" href="/preparation">Retrouver mes cas d’usage</a></div></div>'
        content += section('Le parcours en cinq étapes', '<ol class="parcours">'
            '<li><strong>Besoin.</strong> Vous décrivez la tâche et le résultat utile. L’assistant pose des questions si nécessaire.</li>'
            '<li><strong>Exemple.</strong> Une consigne et des pièces inventées vous sont proposées. Vous corrigez jusqu’à ce que l’exemple soit fidèle.</li>'
            '<li><strong>Validation.</strong> Vous confirmez le travail à tester. La qualification de l’exemple suit ; aucun candidat n’est lancé et rien n’est publié.</li>'
            '<li><strong>Modèles.</strong> Vous choisissez les modèles et leur niveau de raisonnement, puis lancez la comparaison après avoir vu les coûts estimés.</li>'
            '<li><strong>Résultats.</strong> Chaque modèle a passé l’épreuve dans les mêmes conditions. Vous lisez les verdicts, les preuves et les coûts observés.</li></ol>')
        content += section('Ce qui rend le résultat lisible', '<div class="rule">' + icon('i-scale') + '<span><strong>Chaque exigence compte.</strong> '
            'Une obligation non prouvée ou une erreur éliminatoire suffit à écarter une configuration, quel que soit le reste.</span></div>'
            '<ul><li>Le verdict porte sur la configuration observée sous des conditions communes, jamais sur le nom du modèle seul.</li>'
            '<li>Le coût comparé est observé sur reçu ; les estimations affichées avant lancement sont signalées comme telles. Un coût inconnu reste inconnu.</li>'
            '<li>Les pièces sont entièrement inventées : aucun dossier réel, même anonymisé.</li></ul>')
    elif value.get('kind') == 'catalogue':
        title = 'Versions et comparaisons'
        content = '<p class="lead">Index privé de cette session : chaque cas d’usage validé, ses versions d’épreuve et les comparaisons lancées.</p>'
        content += ''.join('<section><h2>' + text(task['need']) + '</h2>' + render_task_index(task) + '</section>' for task in value['tasks'])
        if not value['tasks']:
            content += '<p>Aucun cas d’usage validé dans cette session.</p>'
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
        content += '<p class="hint">Aucune pièce cochée : seuls les verdicts et leurs motifs apparaissent.</p>'
        for piece in value['pieces']:
            pid = piece['piece_id']
            content += '<label><input type="checkbox" name="piece" value="' + text(pid) + '"'
            content += (' checked' if pid in value['selected_links'] else '') + '> ' + text(piece_name(rows[pid], piece, candidates)) + '</label>'
        content += '</fieldset><button type="submit">Actualiser l’aperçu</button></form>'
        content += '<p class="hint">L’aperçu porte sur toute la comparaison, sans les filtres de consultation.</p>'
        content += '<hr>' + projection_body(comparison, value['selected_links'], level=2)
    elif 'dossiers' in value:
        title = 'Mes cas d’usage'
        access = value.get('personal_access', {})
        if value.get('personal_preparation') and access.get('status') == 'connected':
            content = ('<p class="hint">Clé Openrouter enregistrée. ' + access_summary(access)
                       + ' <a href="/preparation/access">Gérer ma clé</a></p>')
        elif value.get('personal_preparation'):
            content = personal_key_form(csrf, access, '/preparation')
        else:
            content = ''
        content += ('<p class="lead note">Vos cas d’usage restent privés dans ce navigateur. '
                    'Reprenez un cas existant ou décrivez-en un nouveau.</p>' if value['dossiers'] else
                    '<p class="lead note">Décrivez le travail et le résultat qui vous serait utile. '
                    'Vous pourrez examiner et corriger l’exemple avant de le valider.</p>')
        dossiers = '<ul class="dossiers">' + ''.join(
            f'<li><a href="/preparation/dossiers/{text(d["dossier_id"])}">{text(d.get("need") or "Cas d’usage sans description")}</a>'
            f'<small>Révision {d["revision"]}</small></li>'
            for d in value['dossiers']) + '</ul><p><a href="/preparation/catalogue">Versions d’épreuve et comparaisons de cette session</a></p>' if value['dossiers'] else (
                '<p>Aucun cas d’usage dans ce navigateur. Commencez par décrire un besoin lorsque les appels sont ouverts.</p>'
                '<p>Si vous en aviez déjà un, vérifiez que vous utilisez le même navigateur et son cookie de session.</p>')
        if not value.get('personal_preparation'):
            dossiers += '<p><a href="/preparation/access">Ma clé Openrouter</a></p>'
        creation = section('Décrire un nouveau cas d’usage', form(csrf, '/preparation/dossiers',
            {'dossier_id': secrets.token_hex(16), 'action_id': secrets.token_hex(16)},
            REQUEST_FIELDS.format(request='', useful='', context='', request_error='', useful_error='', context_error='', request_attrs=' aria-describedby="request-help' + ('"' if can_submit else ' availability" disabled'),
                                  useful_attrs=disabled, context_attrs=disabled) + HONEYPOT,
            form_id='prepare-case')
            + '<button type="submit" form="prepare-case"' + disabled + '>' + icon('i-pen') + 'Préparer cet exemple</button>', 'besoin')
        listing_section = section('Mes cas d’usage dans ce navigateur', dossiers, 'mes-cas')
        # Un visiteur qui revient cherche d'abord ses cas ; un premier visiteur, le formulaire
        content += listing_section + creation if value['dossiers'] else creation + listing_section
    elif value.get('kind') == 'honeypot_ack' or 'operation_id' in value:
        title = 'Demande enregistrée'
        url = ('/preparation' if value.get('kind') == 'honeypot_ack'
               else '/preparation/dossiers/' + value['dossier_id'])
        content = state_block('wait', 'Où j’en suis', 'Préparation en attente', '<p>L’envoi a été enregistré. L’assistant prépare une réponse.</p>',
                              f'<a class="button" href="{text(url)}">Consulter le cas d’usage et son avancement</a>')
    else:
        dossier_id, revision = value['dossier_id'], value['revision']
        url = '/preparation/dossiers/' + dossier_id
        title = ('Votre cas d’usage' if value['validation'] else 'Est-ce le travail que vous voulez tester ?'
                 if value['package'] else 'Précisons le résultat utile')
        prior_revision = revision != value.get('current_revision', revision)
        snapshot = '/revisions/' in path and any(c['task']['revision'] == revision and c['attempts']
                                                for c in value.get('campaigns', []))
        referral = value.get('checks', {}).get('out_of_scope')
        editable = not prior_revision and not snapshot and value['stage'] != 'waiting' and not referral
        needs_availability = editable
        disabled = '' if can_submit and editable else ' disabled aria-describedby="availability"'
        current_campaigns = [c for c in value.get('campaigns', []) if c['task']['revision'] == revision]
        stages = {'draft': ('unk', 'Brouillon', 'Rien n’a encore été envoyé à l’assistant.'),
                  'waiting': ('wait', 'Préparation en cours', 'L’assistant prépare votre exemple ou les précisions nécessaires.'),
                  'clarification': ('action', 'Une précision est attendue de vous', 'Répondez ci-dessous pour que l’exemple soit préparé.'),
                  'preview': ('action', 'Un exemple est prêt à être examiné', 'Lisez la consigne et les pièces, corrigez si besoin, puis validez.'),
                  'scope_confirmation': ('action', 'Le périmètre est à confirmer',
                      'Bench-X compare des modèles sur un travail concret, avec un résultat attendu et des critères vérifiables. '
                      'Précisez ou confirmez le travail que vous souhaitez comparer. Aucun benchmark ne peut être lancé à cette étape.'),
                  'suspended': ('err', 'Préparation suspendue', 'Une intervention du responsable est nécessaire ; aucun rejeu automatique.')}
        tone, heading, next_step = stages[value['stage']]
        if referral:
            title = 'Demande hors périmètre'
            tone, heading, next_step = ('unk', 'Cette demande est hors du périmètre de Bench-X',
                'Bench-X compare des modèles sur des tâches de travail concrètes. '
                'Ce dossier est arrêté ; aucun benchmark ne sera lancé pour cette demande.')
        qualification = value.get('qualification', {})
        automatic = 'operation_id' in qualification
        if value['validation']:
            tone, heading, next_step = ('done', 'Cas d’usage validé', 'La comparaison est en attente de préparation par le responsable.') if not current_campaigns \
                else ('done', 'Cas d’usage validé', 'Une comparaison est préparée ou lancée : suivez-la à l’étape 4.')
        if value['validation'] and automatic:
            if value.get('qualified'):
                tone, heading, next_step = 'done', 'Exemple qualifié', 'Consultez les constats puis choisissez les modèles à comparer.'
            elif qualification.get('status') == 'BLOCKED':
                tone, heading, next_step = 'err', 'Qualification à reprendre', qualification['summary']
            else:
                tone, heading, next_step = 'wait', 'Qualification en cours', 'Votre validation est enregistrée. L’assistant vérifie la cohérence et les critères de l’exemple.'
        actions = ''
        # Une comparaison existe : l'encadré dit son état et mène à elle, jamais à un nouveau choix de modèles
        if value['validation'] and current_campaigns and not snapshot and not prior_revision:
            tone, heading, next_step, target, label = campaign_status(current_campaigns[-1], url)
            actions = f'<a class="button" href="{text(target)}">{text(label)}</a>'
        elif value['validation'] and value.get('qualified') and not snapshot and not prior_revision:
            actions = f'<a class="button" href="{text(url)}/configurations">Choisir les modèles</a>'
        content = '<p class="tag">Cas d’usage inventé · révision ' + text(revision) + '</p>'
        if snapshot:
            title = 'Exemple utilisé pour la comparaison'
            tone, heading, next_step = 'done', 'Exemple déjà testé', 'Vous consultez la version utilisée. Les résultats sont conservés.'
            content += '<p class="notice">Consultation seule. Une modification de l’exemple crée une nouvelle version à valider.</p>'
        if prior_revision:
            content += '<p class="notice">Révision précédente en lecture seule. Pour modifier ou valider, ouvrez la révision courante.</p>'
        if prior_revision:
            actions = f'<a class="button" href="{text(url)}">Revenir à la révision courante</a>'
        elif snapshot:
            actions = f'<a class="button sec" href="{text(url)}">Préparer une nouvelle comparaison</a>'
        if pending:
            actions = ('<div id="preparation-progress"><progress aria-label="' + text(heading) + '"></progress>'
                       '<p class="hint" role="status">Suivi automatique disponible avec JavaScript. Sinon, actualisez cet état.</p>'
                       '<div class="actions"><a href="' + text(url) + '">Actualiser cet état</a>'
                       '<button type="button" class="sec" hidden>Suspendre le suivi automatique</button></div></div>')
            content += '<div id="availability">' + state_block(tone, 'Où j’en suis', heading,
                '<p>' + text(next_step) + '</p>', actions) + '</div><script>' + PREPARATION_PROGRESS_SCRIPT + '</script>'
        else:
            content += state_block(tone, 'Où j’en suis', heading, '<p>' + text(value['explanation']) + '</p><p class="hint">' + text(next_step) + '</p>', actions)
        if referral:
            references = BENCHMARK_REFERENCES.get(referral, ())
            if references:
                content += section('Consulter des benchmarks spécialisés', '<ul>' + ''.join(
                    '<li><a href="' + href + '" rel="noreferrer">' + label + '</a> : ' + description + '.</li>'
                    for label, href, description in references) + '</ul>')
            content += '<p><a class="button sec" href="/preparation">Décrire un autre cas d’usage</a></p>'
        links = [] if 'Actualiser cet état' in actions else [f'<a href="{text(path)}">Actualiser cet état</a>']
        if path != url:
            links.append(f'<a href="{text(url)}">Révision courante</a>')
        if revision > 1:
            links.append(f'<a href="{text(url)}/revisions/{revision - 1}">Révision précédente</a>')
        if links:
            content += '<p class="hint">' + ' · '.join(links) + '</p>'
        if editable and value['package'] is None:
            content += section('Votre réponse', form(csrf, url + '/messages',
                {'action_id': secrets.token_hex(16), 'revision': revision, 'kind': 'clarify'},
                '<label for="message">Votre précision</label><textarea id="message" name="message" rows="3" required maxlength="1000"' + disabled + '></textarea>' + HONEYPOT +
                '<button type="submit"' + disabled + '>Envoyer ma réponse</button>'))
        payload = value['payload']
        content += section('Besoin conservé', '<p>' + text(payload['request']) + '</p>', 'besoin')
        if value.get('task_index'):
            content += '<details><summary>Révisions du cas d’usage et versions d’épreuve</summary>' + render_task_index(value['task_index']) + '</details>'
        if value.get('message') and 'message' in value['message']:
            content += section('Message à l’origine de cette révision', '<p>' + text(value['message']['message']) + '</p>')
        if payload['clarifications'] or payload['validated_assumptions']:
            agreements = listing(payload['clarifications']) if payload['clarifications'] else ''
            for agreement in payload['validated_assumptions']:
                if type(agreement) is dict and set(agreement) == {'question', 'answer'}:
                    agreements += '<blockquote><p>' + text(agreement['question']) + '</p>'
                    agreements += '<p><strong>Votre accord : </strong>' + text(agreement['answer']) + '</p></blockquote>'
                else:
                    agreements += '<p>' + text(encode(agreement) if type(agreement) is dict else agreement) + '</p>'
            content += section('Précisions et accords conservés', agreements)
        if payload['reformulation']:
            content += section('Reformulation', '<p>' + text(payload['reformulation']) + '</p>')
        if payload['fictional_parameters']:
            content += section('Paramètres entièrement inventés', listing(f'{k} : {v}' for k, v in payload['fictional_parameters'].items()))
        package = value['package']
        if package:
            content += section('Consigne donnée aux modèles', '<p class="consigne">' + text(package['instruction']) + '</p>', 'exemple')
            content += section('Les pièces de l’exemple', '<p class="hint">Ouvrez chaque pièce pour la lire ici, puis refermez-la pour poursuivre.</p>' + ''.join(
                '<details class="example-content"><summary>Lire « ' + text(piece['name']) + ' »</summary>'
                + '<div class="example-text">' + text(value['example_contents'][piece['id']]) + '</div></details>'
                for piece in package['pieces']))
            content += '<div class="two">' + section('Livrables attendus', listing(package['deliverables']))
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
            limits = '<h3>Travail humain restant</h3><p>' + text(package['human_work']) + '</p>'
            if package['acceptable_ambiguities']:
                limits += '<h3>Ambiguïtés recevables</h3>' + listing(package['acceptable_ambiguities'])
            if package['limits']:
                limits += '<h3>Limites de l’exemple</h3>' + listing(package['limits'])
            content += section('Ce qui restera à faire', limits)
            change_labels = {'instruction': 'Consigne', 'deliverables': 'Livrables', 'criteria': 'Critères',
                'acceptable_ambiguities': 'Ambiguïtés recevables', 'pieces': 'Pièces'}
            if value['changes']:
                changes = listing(change_labels.get(change, change) for change in value['changes'])
                for kind, label in (('added', 'Pièces ajoutées'), ('removed', 'Pièces retirées'),
                                    ('modified', 'Pièces modifiées')):
                    names = value['piece_changes'][kind]
                    if names:
                        changes += '<h3>' + label + '</h3>' + listing(names)
                content += section('Changements à relire', changes)
        if 'indicative_cost' in value:
            estimate = value['indicative_cost']
            amount = estimate.get('token_subtotal_usd') if estimate else None
            content += '<p>Estimation indicative de cette préparation : ' + text(
                'non estimable' if amount is None else montant_lisible(amount) + ' USD') + \
                '. Tokens utilisés × tarifs du modèle relevés avant appel ; ce montant n’est pas une facture.</p>'
        if value.get('observed_cost'):
            cost = value['observed_cost']
            content += '<p>Coût observé de cette préparation : ' + text(
                'INCONNU' if cost['status'] == 'UNKNOWN' else montant_lisible(cost['amount']) + ' ' + cost['currency']) + '. Source : ' + text(cost['source']) + '.</p>'
        else:
            content += '<p>Coût observé : INCONNU en l’absence de reçu de coût.</p>'
        if value.get('cost_reconciliation'):
            proof, cost = value['cost_reconciliation'], value['effective_cost']
            content += '<p>Coût rapproché : ' + text(montant_lisible(cost['amount']) + ' ' + cost['currency']) + '. Source : ' + text(
                proof['source']) + ', attestée par ' + text(proof['actor']) + ' le ' + text(proof['observed_at']) + \
                '. Le reçu original reste inchangé.</p>'
        if editable and package is not None:
            content += '<details class="corr"><summary class="button sec">' + icon('i-pen') + 'Préciser ou corriger cet exemple</summary><div>' + form(csrf, url + '/messages',
                {'action_id': secrets.token_hex(16), 'revision': revision},
                '<p>Indiquez ce qui doit changer. Les accords non touchés et les révisions précédentes sont conservés. Une modification de l’exemple demande une nouvelle validation.</p>'
                '<label for="kind">Objet du message</label><select id="kind" name="kind"' + disabled + '>'
                '<option value="clarify">Répondre à la clarification ou confirmer le périmètre</option>'
                '<option value="correct" selected>Modifier cet exemple</option></select>'
                '<label for="message">Votre précision ou correction</label>'
                '<textarea id="message" name="message" rows="4" required maxlength="1000"' + disabled + '></textarea>' + HONEYPOT +
                '<button type="submit"' + disabled + '>Envoyer ce message</button>') + '</div></details>'
        launched = any(c['attempts'] for c in current_campaigns)
        if package:
            content += '<section id="validation"><h2>Validation du cas d’usage</h2>'
            if value['validation']:
                content += '<p class="note">Votre validation est enregistrée pour ce cas d’usage, cette révision et cet exemple exact.</p>'
                if not current_campaigns and not automatic:
                    content += '<p>En attente de préparation des conditions par le responsable.</p>'
            else:
                content += '<p class="note">Une nouvelle validation est requise pour l’exemple présenté.</p>'
            if editable and value['stage'] == 'preview' and value['validation'] is None:
                content += '<p>Cette validation confirme la fidélité de cet exemple à votre besoin. Si la qualification est disponible, ' + ('elle utilise votre clé sur votre enveloppe de préparation' if value.get('personal_preparation') else 'elle est financée par l’opérateur sur l’enveloppe de préparation') + '. Aucun appel candidat ni publication n’est autorisé ici.</p>'
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
                            'prépare une nouvelle comparaison ; les résultats actuels restent conservés.</p>')
            content += '</section>'
        labels = {'PENDING': 'En attente', 'QUALIFIED': 'Contrôles requis prouvés',
                  'BLOCKED': 'Bloquée : référence ou contrôles insuffisamment prouvés',
                  'APPROVED': 'Approuvée par action opérateur locale'}
        if package and not referral:
            if automatic:
                # Parcours public : la qualification automatique suffit, aucune approbation opérateur n'est attendue
                content += '<details><summary>Qualification de l’épreuve</summary>'
                content += section('Qualification', '<p>' + text(labels.get(qualification.get('qualification_status'), 'En attente')) + '</p>'
                                   + '<p>' + text(qualification['summary']) + '</p>'
                                   + listing(finding['text'] for finding in qualification.get('findings', [])))
            else:
                content += '<details><summary>Qualification et approbation de l’épreuve</summary>'
                content += section('Qualification', '<p>' + text(labels.get(
                    qualification.get('qualification_status'), 'En attente')) + '</p>')
                content += section('Approbation', '<p>' + text(labels.get(
                    qualification.get('approval_status'), 'En attente')) + '</p>'
                    '<p>La validation du besoin, la qualification et l’approbation restent distinctes. '
                    'Aucun appel ni publication n’est autorisé par cet état. Les preuves, la référence '
                    'et les limites de jugement sont réservées à l’inspection locale du responsable.</p>')
            content += '</details>'
        if value.get('campaigns'):
            content += render_campaign_records(value['campaigns'], url)
    if state and s9 and not pending and not can_submit and needs_availability and not value.get('checks', {}).get('out_of_scope'):
        reasons = {
            'access': 'Ajoutez votre clé Openrouter pour préparer un exemple avec votre propre accès.',
            'open': 'Échanges disponibles. Chaque envoi reste vérifié par le serveur avant admission.',
            'closed': 'Appels fermés : aucune admission de préparation ouverte.',
            'unconfigured': 'Appels fermés : aucun assistant configuré pour la préparation.',
            'waiting': 'Nouveaux appels fermés : une préparation est en attente. Actualisez pour consulter son état.',
            'interrupted': 'Appels fermés : préparation interrompue ou suspendue. Une intervention du responsable est nécessaire ; aucun rejeu automatique.',
            'restore': 'Appels fermés : restauration à vérifier par le responsable.',
            'unresolved': 'Appels fermés : effets ou coûts non résolus dans l’enveloppe de préparation.',
            'budget': 'Appels fermés : enveloppe insuffisante pour un nouvel échange.'}
        status = '<aside id="availability" class="availability" aria-label="État de la préparation"><p><strong>'
        if value.get('personal_preparation'):
            status += ('Préparation disponible' if can_submit else 'Préparation en attente') + '.</strong></p><p>'
        else:
            status += 'Assistant ' + ('configuré' if state['assistant_configured'] else 'non configuré')
            status += '.</strong> Admission ' + ('ouverte' if state['admission_open'] else 'fermée') + '.</p><p>'
        funding = ('Préparation et qualification utilisent votre clé personnelle' if value.get('personal_preparation')
                   else 'La préparation et la qualification sont financées par l’opérateur')
        status += text(reasons[state['reason']]) + '</p><p class="hint">La consultation ne lance aucun appel. ' + funding + ' ; les appels candidats demandent un lancement distinct.</p></aside>'
        content = status + content
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
