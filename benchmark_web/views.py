"""Rendu HTML du parcours privé : formulaires natifs, preuves inertes, gabarit unique.

Ce module ne touche ni au stockage, ni aux secrets, ni aux fournisseurs : il met en
forme les vues structurées renvoyées par l'exécuteur.
"""
from pathlib import Path
import re
import secrets
from urllib.parse import urlencode, urlsplit

from benchmark.preparation import CONTEXT_MAX, REQUEST_MIN, binding
from benchmark.storage import _strict_json as encode

from .campaign_views import (COMPARISON_FOCUS_SCRIPT, campaign_status, CUSTOM_MODELS_SCRIPT, render_attempt_detail, render_campaign_records,
                             render_campaign_launch_operator, render_campaign_launch_requester,
                             render_comparison, render_configurations, render_campaign_models, campaign_followup)
from .fragments import (ACCESS_REASONS, access_summary, date_lisible_utc, form, icon, lisible_en_liste, listing,
                        montant_lisible, personal_key_form, readout, section, state_block, text, workbench)
from .projection import candidate_names, piece_name, projection_body
from .results_views import render_results
from .legal_views import LEGAL_PAGES, legal_layout
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


WITNESS_KINDS = {'alternative': 'Alternative valable', 'defect': 'Défaut ciblé'}
WITNESS_STATES = {'CONFIRMED': 'Décision conforme à l’attendu', 'CONTRADICTED': 'Décision contraire à l’attendu',
                  'UNPROVEN': 'Décision sans preuve exploitable', 'PENDING': 'Contrôle en cours',
                  'NOT_RUN': 'Contrôle non exécuté', 'STALE': 'Preuve d’une autre version, non retenue'}


def render_witnesses(qualification):
    """Témoins de la vérification, sans identifiant interne : ce que le contrôle a décidé, pas l'exactitude de l'attendu"""
    if 'witnesses' not in qualification:
        return ''
    if qualification['witnesses'] is None:
        return '<p>Aucun témoin n’a été contrôlé pour cette version : elle a été vérifiée avant les contrôles par témoins.</p>'

    def decision(row, status):
        if status is None:
            return 'non décidé'
        if row['eliminatory']:
            return {'PASS': 'faute absente', 'FAIL': 'faute présente'}.get(status, 'indéterminé')
        return {'PASS': 'satisfait', 'FAIL': 'non satisfait'}.get(status, 'indéterminé')
    content = ('<h3>Témoins contrôlés</h3><p class="hint">Un témoin est une réponse inventée, recevable ou fautive, soumise '
               'au contrôle qui jugera les modèles. Ce résultat dit ce que le contrôle a décidé sur ce témoin avec cette '
               'référence ; il ne prouve pas que l’attendu est juste.</p>')
    for witness in qualification['witnesses']:
        cost = witness['cost']
        content += ('<h4>' + text(WITNESS_KINDS[witness['kind']]) + ' : ' + text(WITNESS_STATES[witness['state']]) + '</h4>'
                    + '<p>Attendu justifié : ' + text(witness['justification']) + '</p>'
                    + listing(row['description'] + ' : attendu ' + decision(row, row['expected']) + ', décidé '
                              + decision(row, row['decided']) for row in witness['expected'])
                    + '<p class="hint">Contrôlé sur la version ' + text(str(witness['revision'])) + ' de l’exemple, avec sa référence'
                    + ('' if cost is None else ' · coût observé : ' + text(
                        'inconnu' if cost['status'] != 'KNOWN' else montant_lisible(cost['amount']) + ' ' + cost['currency']))
                    + '.</p><details><summary>Lire la réponse témoin</summary><div class="example-text">'
                    + text(witness['output']) + '</div></details>')
    return content


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
    # Notices approuvées : MathArena, LiveCodeBench, SWE-bench, MTEB, Artificial Analysis API
    # Maintenance : Ayo ; HELM reste hors du répertoire ; vérification documentaire : 2026-10-22
    'matharena': {
        'name': 'MathArena', 'url': 'https://matharena.ai/', 'category': 'math',
        'work': 'Résoudre des problèmes mathématiques, notamment issus de compétitions',
        'scope': 'Résultats par compétition et problème ; sorties consultables sur le site',
        'limits': 'Ces problèmes ne représentent pas tous les besoins mathématiques professionnels. '
                  'La synthèse « expected performance » combine observations et estimations. '
                  'Elle ne doit pas devenir un résultat observé Bench-X. Le classement général n’est pas repris.',
        'version': 'Propre à chaque compétition ou série ; aucune version unique retenue',
        'checked_at': '2026-10-22', 'availability': 'available', 'verification': 'verified'},
    'livecodebench': {
        'name': 'LiveCodeBench', 'url': 'https://livecodebench.github.io/', 'category': 'coding',
        'work': 'Génération de code, réparation, prédiction de résultats et exécution sur des exercices',
        'scope': 'Problèmes de concours provenant notamment de LeetCode, AtCoder et Codeforces',
        'limits': 'Un exercice ne représente pas une modification complète d’application. '
                  'Les périodes permettent des comparaisons temporelles. '
                  'Elles ne garantissent pas seules l’absence de contamination. '
                  'La fraîcheur de chaque tableau n’est pas certifiée.',
        'version': 'Version unique inconnue ; préciser le scénario et la période du tableau consulté',
        'published_at': '2024 pour l’article initial ; exemples anciens distincts des versions successives',
        'checked_at': '2026-10-22', 'availability': 'available', 'verification': 'verified'},
    'swe-bench': {
        'name': 'SWE-bench', 'url': 'https://www.swebench.com/', 'category': 'coding',
        'work': 'Résoudre des problèmes logiciels issus de dépôts',
        'scope': 'Plusieurs variantes, dont Full, Lite, Verified, Multilingual et Multimodal',
        'limits': 'La variante, l’environnement et l’agent influencent le résultat. Le nom du modèle ne suffit pas. '
                  'Le site distingue les résultats exécutés ou vérifiés par son équipe. '
                  'Les résultats des variantes ne sont pas fusionnés.',
        'version': 'Nom de la variante à conserver ; aucune version unique retenue',
        'checked_at': '2026-10-22', 'availability': 'available', 'verification': 'verified'},
    'mteb': {
        'name': 'MTEB', 'url': 'https://github.com/embeddings-benchmark/mteb', 'category': 'retrieval',
        'work': 'Évaluer des représentations vectorielles et des systèmes de recherche',
        'scope': 'Tâches, langues et modalités multiples ; documentation et classement liés depuis le dépôt',
        'limits': 'Un résultat de recherche ne prouve pas la qualité d’une réponse générée à partir des documents. '
                  'Il faut comparer les mêmes tâches et les mêmes langues. '
                  'Une moyenne générale ne représente pas automatiquement le corpus du visiteur. '
                  'Le fonctionnement interactif du classement n’a pas été essayé.',
        'version': 'Dépend de la tâche, des données et du protocole ; aucune version globale retenue',
        'checked_at': '2026-10-22', 'availability': 'available', 'verification': 'verified'},
    'artificial-analysis-api': {
        'name': 'Artificial Analysis, performances API',
        'url': 'https://artificialanalysis.ai/methodology/performance-benchmarking', 'category': 'api',
        'work': 'Délai avant réception, vitesse de sortie et temps de réponse des services API',
        'scope': 'Charges et conditions de concurrence définies ; mesures agrégées sur des fenêtres temporelles',
        'limits': 'La localisation du serveur et la longueur des échanges influencent les mesures. '
                  'Les résultats ne garantissent ni délai contractuel ni performance depuis le poste du visiteur. '
                  'Leur indice global n’entre pas dans Bench-X. La date de source concerne la méthode. '
                  'Les résultats évoluent séparément.',
        'version': '2.2.0', 'published_at': '2026-03-02',
        'checked_at': '2026-10-22', 'availability': 'available', 'verification': 'verified'},
}
REFERENCE_CATEGORIES = {
    'math': 'Mathématiques', 'coding': 'Programmation',
    'retrieval': 'Recherche documentaire', 'api': 'Performances des services'}
REFERENCE_VERIFICATIONS = {'verified': 'Vérifiée', 'unverified': 'Vérification insuffisante'}


def reference_verified(reference):
    return reference.get('verification') == 'verified' and bool(reference.get('checked_at'))


def reference_url(reference):
    url = reference.get('url') or ''
    # urlsplit découpe une URL mais ne la valide pas ; le navigateur ne reçoit que des liens HTTPS sans authentification
    if not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == '\\' for c in url):
        return None
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port is not None and parsed.port != 443
                or not re.fullmatch(r'[A-Za-z0-9.-]+', parsed.hostname)):
            return None
    except ValueError:
        return None
    return url


def reference_notice(reference):
    url = reference_url(reference)
    content = '<dl>' + ''.join('<dt>' + label + '</dt><dd>' + text(reference.get(key) or 'Inconnue') + '</dd>'
        for key, label in (('work', 'Travail mesuré'), ('scope', 'Portée'), ('limits', 'Limites'),
                           ('version', 'Version'), ('published_at', 'Date de la source'),
                           ('checked_at', 'Date de vérification éditoriale'))) + '</dl>'
    if not reference_verified(reference):
        content += '<p class="notice">Vérification insuffisante. Cette source n’est pas une recommandation.</p>'
    else:
        content += '<p>Vérification éditoriale effectuée.</p>'
    if reference.get('availability') != 'available':
        content += '<p class="notice">Source retirée : indisponible.</p><p>Source officielle : ' + text(url or 'Inconnue') + '</p>'
    elif not url:
        content += '<p class="notice">Lien officiel indisponible.</p><p>Source officielle : Inconnue.</p>'
    else:
        content += '<p>Source disponible.</p><p>Source officielle : <a href="' + text(url) + '" rel="noreferrer noopener">' + text(url) + '</a></p>'
    return content


def editorial_report(report):
    """Signalement éditorial attribué à la version, jamais une réécriture de ses octets"""
    if not report:
        return ''
    return state_block('warn', 'Signalement éditorial', 'Erreur signalée sur cette version',
                       '<p>' + text(report['summary']) + '</p><p class="hint">Attribué à la version source le '
                       + text(date_lisible_utc(report['reported_at'])) + '. Ce signalement ne corrige pas les pièces '
                       'ni les preuves déjà conservées.</p>')


EDITORIAL_STATUS = {'ABSENT': 'Qualification absente', 'QUALIFIED': 'Qualification réussie',
                    'BLOCKED': 'Qualification bloquée'}
EDITORIAL_FILTERS = (('entree', 'Entrées'), ('langue', 'Langue'), ('outil', 'Outil'),
                     ('difficulte', 'Difficulté'), ('qualification', 'Qualification'))


def editorial_criteria_query(criteria):
    """Chaîne de requête stable des seuls critères actifs"""
    criteria = criteria or {}
    keys = ('q',) + tuple(key for key, _ in EDITORIAL_FILTERS)
    return urlencode([(key, criteria.get(key, '')) for key in keys if criteria.get(key)])


def editorial_characteristic(entry, key):
    """Valeur déclarée, absence explicite ou inconnue, sans substitution entre champs"""
    value = entry.get(key)
    if key in ('inputs', 'tools'):
        if value is None:
            return 'Inconnues' if key == 'inputs' else 'Inconnus'
        if not value:
            return 'Aucune entrée déclarée' if key == 'inputs' else 'Aucun outil déclaré'
        return ', '.join(value)
    if value is None:
        return 'Inconnue' if key in ('language', 'difficulty') else 'Inconnu'
    return value


def editorial_characteristics(entry):
    """Les huit informations restent distinctes, y compris la qualification et les résultats"""
    return (('Travail évalué', entry.get('work') or 'Inconnu'),
            ('Résultat attendu', editorial_characteristic(entry, 'expected_result')),
            ('Entrées', editorial_characteristic(entry, 'inputs')),
            ('Langue', editorial_characteristic(entry, 'language')),
            ('Outils', editorial_characteristic(entry, 'tools')),
            ('Difficulté', editorial_characteristic(entry, 'difficulty')),
            ('Version', entry.get('version_id') or 'Inconnue'),
            ('Qualification', EDITORIAL_STATUS.get(entry.get('qualification_status'), 'Qualification absente')),
            ('Résultats publiés', 'Résultats publiés' if entry.get('results') else 'Aucun résultat publié'))


def editorial_fields(entry):
    return '<dl class="editorial-characteristics">' + ''.join(
        '<dt>' + text(label) + '</dt><dd>' + text(value) + '</dd>'
        for label, value in editorial_characteristics(entry)) + '</dl>'


def render_editorial(value):
    need = '<p><a href="/preparation">Décrire mon cas d’usage</a></p>'
    criteria = value.get('criteria') or {}
    query = editorial_criteria_query(criteria)
    back_href = '/epreuves' + ('?' + query if query else '')
    back = '<p>' + (('<a href="' + text(back_href) + '">Retour aux résultats</a> · ') if query else '')
    back += '<a href="/epreuves">Toutes les épreuves</a></p>'
    if value.get('withdrawn'):
        content = ('<p class="note">Cette version est retirée. Son contenu et ses pièces ne sont plus disponibles.</p>'
                   '<p>Aucune nouvelle reprise de cette version n’est admise.</p>')
        content += editorial_report(value.get('report'))
        return 'Version retirée', back + content + need
    if not value.get('version_id'):
        content = ('<p>La recherche porte sur le texte des notices publiques. Elle est déterministe et ne classe '
                   'pas les épreuves par proximité sémantique.</p>'
                   '<p>Les caractéristiques sont déclarées avec chaque version. Une information absente reste '
                   'inconnue ; elle ne prouve ni absence d’outil ni qualification.</p>')
        filters = value.get('filters') or {}

        def active_unavailable(key):
            active = criteria.get(key)
            return bool(active) and active not in filters.get(key, [])

        search = ('<form method="get" action="/epreuves"><label for="q">Rechercher une épreuve</label>'
                  '<input type="text" id="q" name="q" maxlength="200" value="' + text(criteria.get('q', '')) + '">')
        for key, label in EDITORIAL_FILTERS:
            search += ('<label for="' + key + '">' + label + '</label><select id="' + key + '" name="' + key
                       + '"><option value="">Tous</option>')
            proposed = list(filters.get(key, []))
            options = proposed + ([criteria[key]] if active_unavailable(key) else [])
            for option in options:
                shown = str(EDITORIAL_STATUS.get(option, option) if key == 'qualification' else option)
                if active_unavailable(key) and option not in proposed:
                    shown += ' (indisponible)'
                search += ('<option value="' + text(option) + '"'
                           + (' selected' if criteria.get(key) == option else '') + '>' + text(shown) + '</option>')
            search += '</select>'
        search += '<button type="submit">Rechercher</button></form>'
        search += '<p><a href="/epreuves">Effacer la recherche et les filtres</a></p>'
        search += '<p><a href="/epreuves/aide">Demander une aide sémantique facultative</a></p>'
        if value.get('searched'):
            content += '<section><h2>Critères actifs</h2><dl class="active-criteria">'
            for key, label in (('q', 'Recherche'),) + EDITORIAL_FILTERS:
                if criteria.get(key):
                    shown = str(EDITORIAL_STATUS.get(criteria[key], criteria[key])
                                if key == 'qualification' else criteria[key])
                    if key != 'q' and active_unavailable(key):
                        shown += ' (indisponible)'
                    content += '<dt>' + text(label) + '</dt><dd>' + text(shown) + '</dd>'
            content += '</dl>'
            if any(active_unavailable(key) for key, _ in EDITORIAL_FILTERS):
                content += ('<p class="hint">Une valeur marquée indisponible ne correspond à aucune version '
                            'publique disponible. Elle reste active jusqu’à sa modification ou son effacement.</p>')
            content += '</section>'
        for entry in value.get('versions', []):
            href = '/epreuves/' + entry['task_id'] + '/' + entry['version_id']
            if query:
                href += '?' + query
            content += ('<section><h2><a href="' + text(href) + '">' + text(entry['title']) + '</a></h2>'
                        + editorial_fields(entry) + '</section>')
        if not value.get('total'):
            content += '<p>Aucune épreuve publiée.</p>'
        elif not value.get('versions'):
            content += ('<p>Aucune épreuve ne correspond à cette recherche. Modifiez ou effacez les critères.</p>')
        # La recherche se lit avant la liste qu'elle filtre
        return 'Épreuves', workbench(content, search, 'Recherche et filtres', side_first=True) + need
    status = EDITORIAL_STATUS.get(value.get('qualification_status'), 'Qualification absente')
    content = editorial_fields(value)
    content += '<p class="note">' + text(status) + '.</p>'
    if value.get('qualification_status') == 'ABSENT':
        content += '<p>Aucune preuve de qualification n’est publiée.</p>'
    else:
        content += '<p>Les preuves de qualification ne font pas partie de cette publication.</p>'
    campaigns = value.get('results') or []
    if campaigns:
        content += ('<section><h2>Résultats publiés</h2><p class="hint">Chaque campagne est une restitution '
                    'approuvée séparément. Elle ne fusionne pas ses résultats avec une autre campagne.</p><ul>')
        for campaign in campaigns:
            href = ('/epreuves/' + text(value['task_id']) + '/' + text(value['version_id'])
                    + '/campagnes/' + text(campaign['campaign_id']))
            shown = ('Campagne du ' + date_lisible_utc(campaign['frozen_at']) + ' · ' + campaign['pi']['package']
                     + ' ' + campaign['pi']['version'] + ' · '
                     + str(campaign['coverage']['evaluated_attempts'])
                     + (' réponse évaluée' if campaign['coverage']['evaluated_attempts'] == 1 else ' réponses évaluées')
                     + ' sur ' + str(campaign['coverage']['planned_cells'])
                     + (' essai prévu' if campaign['coverage']['planned_cells'] == 1 else ' essais prévus') + ' · coûts '
                     + ('complets' if campaign['economic_status'] == 'COMPLETE' else 'incomplets'))
            content += '<li><a href="' + href + '">' + text(shown) + '</a></li>'
        content += '</ul>'
        if len({(campaign.get('contract_sha256'), campaign.get('cases'),
                 tuple(sorted((campaign.get('environment') or {}).items())),
                 campaign['pi']['package'], campaign['pi']['version'], campaign['unit'])
                for campaign in campaigns}) > 1:
            content += ('<p class="note">Ces campagnes n’ont pas le même contrat, périmètre d’exemples, environnement, '
                        'harnais ou unité de coût : leurs résultats ne se comparent pas directement.</p>')
        content += '</section>'
    else:
        content += '<p class="note">Aucun résultat publié.</p>'
    content += ('<p>Cette publication ne qualifie pas l’épreuve et n’autorise aucune exécution. '
                'La difficulté déclarée ne prouve aucune capacité mesurée.</p>')
    content += editorial_report(value.get('report'))
    content += '<h2>Cas</h2><ul>' + ''.join('<li>' + text(case) + '</li>' for case in value['cases']) + '</ul>'
    content += '<h2>Limites</h2><ul>' + ''.join('<li>' + text(limit) + '</li>' for limit in value['limits']) + '</ul>'
    content += '<h2>Pièces</h2>'
    base = '/epreuves/' + text(value['task_id']) + '/' + text(value['version_id'])
    for piece in value['pieces']:
        content += ('<section><h3><a href="' + base + '/pieces/' + text(piece['id']) + '">' + text(piece['label']) + '</a></h3>'
                    '<pre class="proof-content">' + text(piece['text']) + '</pre></section>')
    copy_base = '/preparation/copies/' + text(value['task_id']) + '/' + text(value['version_id'])
    copy = section('Préparer mon dossier privé',
                   '<p>Cette copie conserve exactement la notice et les pièces ci-dessus. Elle ne demande ni clé '
                   'ni appel aux modèles : vous décrirez ensuite votre besoin et ce qui diffère de cette épreuve.</p>'
                   '<p><a class="button" href="' + copy_base + '">Créer mon dossier privé</a></p>')
    # La copie annonce les pièces « ci-dessus » : sur téléphone, sa colonne suit la zone principale
    return value['title'], back + workbench(content, copy, 'Préparer mon dossier privé') + need


def render_references(value):
    back = '<p><a href="/evaluations-externes">Toutes les évaluations externes</a></p>'
    need = '<p><a href="/preparation">Décrire mon cas d’usage</a></p>'
    introduction = '<p>Ces évaluations externes ne prouvent pas la réussite sur votre besoin particulier.</p>'
    if value.get('reference_id'):
        reference = BENCHMARK_REFERENCES[value['reference_id']]
        return reference['name'], back + workbench(reference_notice(reference), introduction + need, 'À savoir')
    content = (introduction
               + '<p>Ayo est responsable de la maintenance de cette sélection.</p>'
               + '<p>La vérification documentaire du 22 octobre 2026 couvre l’origine, l’objet et les limites. '
               + 'La disponibilité d’un lien reste distincte de cette vérification.</p>')
    available = {identity: reference for identity, reference in BENCHMARK_REFERENCES.items()
                 if reference.get('availability') == 'available'}
    categories = {reference['category']: REFERENCE_CATEGORIES.get(reference['category'], reference['category'])
                  for reference in available.values()}
    query, category, verification = (value.get(key, '') for key in ('q', 'category', 'verification'))
    search = ('<form method="get" action="/evaluations-externes"><label for="q">Rechercher une évaluation</label>'
              '<input type="text" id="q" name="q" value="' + text(query) + '">')
    for key, label, choices in (('category', 'Travail mesuré', categories),
                                 ('verification', 'Vérification éditoriale', REFERENCE_VERIFICATIONS)):
        search += '<label for="' + key + '">' + label + '</label><select id="' + key + '" name="' + key + '"><option value="">Tous</option>'
        search += ''.join('<option value="' + text(choice) + '"' + (' selected' if value.get(key) == choice else '')
                          + '>' + text(name) + '</option>' for choice, name in choices.items()) + '</select>'
    search += '<button type="submit">Rechercher</button></form><p><a href="/evaluations-externes">Effacer la recherche et les filtres</a></p>'
    search += '<p><a href="/evaluations-externes/aide">Demander une aide sémantique facultative</a></p>'
    found = []
    for identity, reference in available.items():
        if category and reference['category'] != category:
            continue
        if verification and reference_verified(reference) != (verification == 'verified'):
            continue
        searchable = ' '.join(str(part or '') for key, part in reference.items() if key != 'url').casefold()
        searchable += ' ' + categories[reference['category']].casefold()
        if all(term in searchable for term in query.casefold().split()):
            found.append((identity, reference))
    for identity, reference in found:
        content += ('<section><h2><a href="/evaluations-externes/' + text(identity) + '">'
                    + text(reference['name']) + '</a></h2>' + reference_notice(reference) + '</section>')
    if not available:
        content += '<p>Aucune évaluation externe disponible.</p>'
    elif not found:
        content += '<p>Aucune évaluation ne correspond à cette recherche. Modifiez la recherche ou les filtres.</p>'
    # La recherche se lit avant la liste qu'elle filtre
    return 'Évaluations externes', (workbench(content, search, 'Recherche et filtres', side_first=True)
                                    + need + '<p><a href="/">Accueil</a></p>')


HELP_LABELS = {'correspond': 'Correspondance forte', 'partiel': 'Correspondance partielle'}
HELP_REASONS = {
    'DISABLED': 'Le service d’aide est désactivé. La recherche simple reste disponible.',
    'FINANCE': 'Aucune preuve financière n’est établie pour cette aide. Aucun appel n’a été émis.',
    'BUDGET': 'L’enveloppe commune de cette aide ne permet plus cet envoi. Aucun appel n’a été émis.',
    'RATE': 'Trop de demandes simultanées ou trop rapprochées. Réessayez dans un instant. Aucun appel n’a été émis.',
    'CONTEXT': 'La formulation et les notices dépassent les limites de contexte. Raccourcissez la formulation. '
               'Aucun appel n’a été émis.',
    'TIMEOUT': 'L’aide n’a pas répondu dans le délai de dix secondes. Aucun nouvel essai n’est lancé automatiquement.',
    'RATE_LIMITED': 'TypeSafe limite temporairement les demandes. Aucun nouvel essai n’est lancé automatiquement.',
    'OVERLOADED': 'TypeSafe est temporairement surchargé. Aucun nouvel essai n’est lancé automatiquement.',
    'KEY_REJECTED': 'L’accès à TypeSafe est refusé. Aucun nouvel essai n’est lancé automatiquement.',
    'PROVIDER_ERROR': 'TypeSafe n’a pas pu répondre. Aucun nouvel essai n’est lancé automatiquement.',
    'INVALID_RESPONSE': 'La réponse de TypeSafe est inexploitable. Aucune suggestion n’en est tirée.',
    'INTERRUPTED': 'L’aide a été interrompue. Elle ne sera pas relancée.',
    'RECORD_FAILED': 'Le reçu de l’aide n’a pas pu être conservé. Aucune suggestion n’est affichée.',
    'CONFLICT': 'Cette demande ne correspond pas au reçu conservé.',
    'RESTORE_PENDING': 'Les données sont en attente de vérification après restauration. '
                       'Aucun appel n’a été émis.',
    'INVALID_REQUEST': 'La demande est incomplète. Corrigez-la et renvoyez-la.',
}
HELP_INTRO = (
    '<p>Cette aide facultative compare votre formulation au travail mesuré par les notices publiques '
    'admissibles. Elle utilise TypeSafe et son modèle Jev. Elle ne remplace pas la recherche simple : '
    'celle-ci reste disponible sans cette aide.</p>'
    '<div class="note"><p><strong>Données transmises :</strong> la formulation que vous soumettez et les '
    'informations publiques utiles des notices retenues. Aucun dossier privé, contribution, référence de '
    'jugement, historique, clé ni cookie n’est transmis.</p>'
    '<p>TypeSafe traite ces données selon son traitement standard. Bench-X ne promet ni absence de '
    'conservation, ni localisation exclusivement européenne, ni absence d’entraînement. N’écrivez aucune '
    'information personnelle ou confidentielle : cette consigne ne garantit pas leur absence.</p>'
    '<p>Bench-X finance cette aide sur une enveloppe limitée, distincte des clés OpenRouter personnelles. '
    'Seuls des reçus techniques et comptables sont conservés, sans le texte recherché. Une probabilité ne '
    'prouve ni adéquation, ni qualification, et cette aide ne lance aucune campagne.</p></div>')


def help_notices():
    """Champs publics utiles, sans URL ni indicateur éditorial, pour la seule aide facultative"""
    notices = []
    for identity, reference in BENCHMARK_REFERENCES.items():
        if reference.get('availability') != 'available' or not reference_verified(reference):
            continue
        item = {'id': identity}
        for key in ('name', 'category', 'work', 'scope', 'limits', 'version'):
            value = reference.get(key)
            if isinstance(value, str) and value:
                item[key] = value
        item['name'] = reference.get('name') or 'Inconnue'
        notices.append(item)
    return notices


def help_form(value, action='/evaluations-externes/aide'):
    submission_id = value.get('submission_id')
    if type(submission_id) is not str or re.fullmatch('[0-9a-f]{32}', submission_id) is None:
        return ''
    formulation = value.get('formulation')
    formulation = formulation if type(formulation) is str else ''
    return ('<form method="post" action="' + text(action) + '">'
            '<input type="hidden" name="submission_id" value="' + text(submission_id) + '">'
            '<label for="formulation">Formulation à comparer</label>'
            '<p id="formulation-help" class="hint">Décrivez le travail recherché, sans information '
            'personnelle ni confidentielle. Une demande peut contenir plusieurs travaux.</p>'
            '<textarea id="formulation" name="formulation" required rows="5" '
            'aria-describedby="formulation-help">' + text(formulation) + '</textarea>'
            + HONEYPOT +
            '<button type="submit">Demander l’aide</button></form>')


def render_help_receipt(receipt):
    """Reçu technique commun, sans texte recherché ni identifiant brut de notice"""
    if not receipt:
        return ''
    cost = receipt.get('cost') or {}
    notices = receipt.get('notice_labels')
    if notices is None:
        notices = receipt.get('notices') or []
    return ('<details><summary>Reçu technique de cette demande</summary><dl>'
            '<dt>Usage</dt><dd>' + text(receipt.get('usage')) + '</dd>'
            '<dt>Version des questions</dt><dd>' + text(receipt.get('questions_version')) + '</dd>'
            '<dt>Modèle demandé</dt><dd>' + text(receipt.get('model_requested')) + '</dd>'
            '<dt>Modèle observé</dt><dd>' + text(receipt.get('model_observed') or 'Inconnu') + '</dd>'
            '<dt>Notices appréciées</dt><dd>' + text(', '.join(notices) or 'Aucune') + '</dd>'
            '<dt>Jetons d’entrée</dt><dd>' + text(receipt.get('input_tokens') if receipt.get('input_tokens') is not None else 'Inconnus') + '</dd>'
            '<dt>Jetons de sortie</dt><dd>' + text(receipt.get('output_tokens') if receipt.get('output_tokens') is not None else 'Inconnus') + '</dd>'
            '<dt>Coût calculé</dt><dd>' + text(
                'inconnu' if cost.get('status') != 'KNOWN'
                else montant_lisible(cost.get('amount')) + ' ' + str(cost.get('currency'))) + '</dd>'
            '<dt>Base du coût</dt><dd>' + text(cost.get('source') or 'Inconnue') + '</dd>'
            '</dl><p class="hint">Un coût calculé depuis les jetons reçus et le tarif daté reste '
            'distinct d’une facture. Un coût inconnu conserve sa réserve.</p></details>')


def render_semantic_help(value):
    stage = value.get('stage')
    content = HELP_INTRO
    if stage in ('form', 'unavailable'):
        content += help_form(value)
    if stage == 'suggestions':
        content += '<section><h2>Suggestions</h2>'
        content += ('<p class="hint">Chaque notice est appréciée indépendamment. Les correspondances '
                    'reposent sur des critères fermés et les champs publiés. Aucune suggestion ne '
                    'qualifie une source.</p>')
        suggestions = value.get('suggestions') or []
        for suggestion in suggestions:
            reference = suggestion.get('reference') or {}
            label = HELP_LABELS.get(suggestion.get('label'), 'Correspondance')
            content += ('<section><h2><a href="/evaluations-externes/' + text(suggestion['id']) + '">'
                        + text(reference.get('name') or 'Inconnue') + '</a></h2>'
                        + '<p class="note">' + text(label) + '. Cette appréciation ne prouve ni adéquation '
                        'ni qualification.</p>' + reference_notice(reference) + '</section>')
        if not suggestions:
            content += '<p>Aucune suggestion.</p>'
        content += '</section>'
    elif stage == 'none':
        content += ('<p role="status">Aucune notice ne correspond à cette formulation. '
                    'La recherche simple reste disponible.</p>')
    if value.get('dropped'):
        content += ('<p class="hint">Une suggestion a été retirée : sa notice n’est plus disponible '
                    'ou sa vérification éditoriale ne suffit plus.</p>')
    elif stage == 'pending':
        content += state_block('wait', 'Aide sémantique', 'Aide en cours',
                               '<p>La demande est enregistrée. Cette page ne relance aucun appel ; '
                               'actualisez-la dans un instant.</p>')
    elif stage == 'unavailable':
        content += state_block('unk', 'Aide sémantique', 'Aide indisponible',
                               '<p>' + text(HELP_REASONS.get(value.get('reason'), 'L’aide n’est pas disponible.'))
                               + '</p>')
    content += render_help_receipt(value.get('receipt'))
    content += ('<p><a href="/evaluations-externes/aide">Nouvelle demande</a> · '
                '<a href="/evaluations-externes">Recherche simple</a> · '
                '<a href="/preparation">Décrire mon cas d’usage</a></p>')
    return 'Aide sémantique facultative', content


def semantic_help_view(value):
    """Projection de l'aide : les notices retirées ou non vérifiées disparaissent avant affichage"""
    status = value.get('status')
    stage = {'suggestions': 'suggestions', 'none': 'none',
             'unavailable': 'unavailable', 'pending': 'pending'}.get(status, 'unavailable')
    suggestions, dropped = [], False
    for suggestion in value.get('suggestions') or []:
        reference = BENCHMARK_REFERENCES.get(suggestion.get('notice_id'))
        if (not reference or reference.get('availability') != 'available'
                or not reference_verified(reference)):
            dropped = True
            continue
        suggestions.append({'id': suggestion['notice_id'], 'label': suggestion.get('label'),
                            'reference': reference})
    if stage == 'suggestions' and not suggestions:
        stage = 'none'
    return {'kind': 'references_help', 'stage': stage, 'reason': value.get('reason'),
            'receipt': value.get('receipt'), 'suggestions': suggestions,
            'dropped': dropped}


def editorial_help_notices(versions):
    """Champs publics utiles à l’aide catalogue, sans qualification, résultat ni caractéristique inventée"""
    notices = []
    for entry in versions:
        identity = entry.get('package_sha256')
        if type(identity) is not str or re.fullmatch(r'[0-9a-f]{64}', identity) is None:
            continue
        item = {'id': identity, 'name': entry.get('title') or 'Inconnue',
                'work': entry.get('work') or 'Inconnu', 'version': entry.get('version_id')}
        scope = entry.get('expected_result')
        if type(scope) is str and scope:
            item['scope'] = scope
        if entry.get('limits'):
            item['limits'] = '\n'.join(entry['limits'])
        notices.append(item)
    return notices


def editorial_help_view(value, versions):
    """Projection de l’aide catalogue : chaque suggestion est revérifiée contre le catalogue courant"""
    available = {entry['package_sha256']: entry for entry in versions
                 if type(entry.get('package_sha256')) is str}
    status = value.get('status')
    stage = {'suggestions': 'suggestions', 'none': 'none',
             'unavailable': 'unavailable', 'pending': 'pending'}.get(status, 'unavailable')
    suggestions, dropped = [], False
    for suggestion in value.get('suggestions') or []:
        entry = available.get(suggestion.get('notice_id'))
        if entry is None:
            dropped = True
            continue
        suggestions.append({'id': suggestion['notice_id'], 'label': suggestion.get('label'),
                            'entry': entry})
    if stage == 'suggestions' and not suggestions:
        stage = 'none'
    receipt = value.get('receipt')
    if type(receipt) is dict:
        receipt = dict(receipt)
        receipt['notice_labels'] = [
            available[identity]['task_id'] + ' / ' + available[identity]['version_id']
            if identity in available else 'Version retirée ou indisponible'
            for identity in (receipt.get('notices') or [])]
    return {'kind': 'editorial_help', 'stage': stage, 'reason': value.get('reason'),
            'receipt': receipt, 'suggestions': suggestions, 'dropped': dropped}


def render_editorial_help(value):
    stage = value.get('stage')
    content = HELP_INTRO
    if stage in ('form', 'unavailable'):
        content += help_form(value, '/epreuves/aide')
    if stage == 'suggestions':
        content += '<section><h2>Suggestions</h2>'
        content += ('<p class="hint">Chaque version est appréciée indépendamment. Les correspondances '
                    'reposent sur des critères fermés et les champs publics. Aucune suggestion ne '
                    'qualifie une épreuve.</p>')
        for suggestion in value.get('suggestions') or []:
            entry = suggestion.get('entry') or {}
            label = HELP_LABELS.get(suggestion.get('label'), 'Correspondance')
            href = ('/epreuves/' + text(entry.get('task_id', '')) + '/'
                    + text(entry.get('version_id', '')))
            content += ('<section><h2><a href="' + href + '">' + text(entry.get('title') or 'Inconnue')
                        + '</a></h2><p class="note">' + text(label) + '. Cette appréciation ne prouve ni '
                        'adéquation ni qualification.</p>' + editorial_fields(entry) + '</section>')
        if not value.get('suggestions'):
            content += '<p>Aucune suggestion.</p>'
        content += '</section>'
    elif stage == 'none':
        content += ('<p role="status">Aucune épreuve ne correspond à cette formulation. '
                    'La recherche simple reste disponible.</p>')
    if value.get('dropped'):
        content += ('<p class="hint">Une suggestion a été retirée : sa version n’est plus disponible '
                    'ou son paquet ne correspond plus à son empreinte.</p>')
    elif stage == 'pending':
        content += state_block('wait', 'Aide sémantique', 'Aide en cours',
                               '<p>La demande est enregistrée. Cette page ne relance aucun appel ; '
                               'actualisez-la dans un instant.</p>')
    elif stage == 'unavailable':
        content += state_block('unk', 'Aide sémantique', 'Aide indisponible',
                               '<p>' + text(HELP_REASONS.get(value.get('reason'), 'L’aide n’est pas disponible.'))
                               + '</p>')
    content += render_help_receipt(value.get('receipt'))
    content += ('<p><a href="/epreuves/aide">Nouvelle demande</a> · '
                '<a href="/epreuves">Recherche simple</a> · '
                '<a href="/preparation">Décrire mon cas d’usage</a></p>')
    return 'Aide sémantique facultative', content


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


def render_source_copy(source, notice, *, future=False):
    """Notice et pièces exactes conservées dans le dossier, distinctes du besoin personnel"""
    labels = {'ABSENT': 'Qualification absente', 'QUALIFIED': 'Qualification réussie', 'BLOCKED': 'Qualification bloquée'}
    status = labels.get(source.get('qualification_status'), 'Qualification absente')
    content = ('<p class="note">Cette épreuve publiée ' + ('sera conservée' if future else 'est conservée')
               + ' dans votre dossier avec sa version et son '
               'empreinte. Sa qualification publiée ne qualifie pas ce dossier : le contrat et la référence de '
               'votre nouvel exemple seront construits puis vérifiés séparément.</p>'
               '<dl><dt>Titre</dt><dd>' + text(source['title']) + '</dd>'
               '<dt>Travail évalué</dt><dd>' + text(source['work']) + '</dd>'
               '<dt>Version source</dt><dd>' + text(source['version_id']) + '</dd>'
               '<dt>Qualification publiée</dt><dd>' + text(status) + '</dd></dl>')
    if source.get('cases'):
        content += '<h3>Cas de l’épreuve source</h3>' + listing(source['cases'])
    if source.get('limits'):
        content += '<h3>Limites de l’épreuve source</h3>' + listing(source['limits'])
    content += '<h3>Pièces ' + ('copiées' if future else 'conservées') + '</h3>'
    for piece in source.get('pieces', []):
        content += ('<details class="example-content"><summary>Lire « ' + text(piece['label']) + ' »</summary>'
                    '<div class="example-text">' + text(piece['text']) + '</div></details>')
    if notice.get('withdrawn'):
        content += ('<p class="notice">Cette version n’est plus disponible au catalogue. Votre copie et ses pièces '
                    'restent conservées dans ce dossier.</p>')
    if notice.get('unavailable'):
        content += '<p class="notice">L’état et les signalements de la source sont momentanément indisponibles.</p>'
    for report in notice.get('reports', []):
        content += editorial_report(report)
    return section('Épreuve source', content, 'source')


def render_copy_source(value, csrf):
    """Confirmation de la copie sous le chemin privé, avec le jeton de la session existante"""
    source = value['source']
    path = '/preparation/copies/' + text(source['task_id']) + '/' + text(source['version_id'])
    side = ('<p class="note">Cette copie conservera exactement la notice et les pièces ci-dessous. '
            'Aucune clé ni aucun appel aux modèles n’est nécessaire ; vous décrirez ensuite votre besoin.</p>')
    side += section('Créer le dossier privé', form(
        csrf, path, {'copy': '1'},
        '<p>Le dossier restera privé dans ce navigateur. Sa qualification publiée ne qualifie pas le nouveau dossier.</p>'
        '<button type="submit">Créer mon dossier privé</button>'))
    side += ('<p><a href="/epreuves/' + text(source['task_id']) + '/' + text(source['version_id'])
             + '">Retour à l’épreuve</a></p>')
    # L'épreuve copiée en zone principale ; ce que coûte la copie et l'action en synthèse
    content = workbench(render_source_copy(source, value.get('source_notice') or {}, future=True), side, 'Créer le dossier privé',
                        side_first=True)
    return 'Créer mon dossier privé', content


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
    current = None if error else {'home': '/', 'privacy_data': '/preparation/data',
                                 'references': '/evaluations-externes' if not value.get('reference_id') else None,
                                 'references_help': None,
                                 'editorial': '/epreuves' if not value.get('version_id') else None,
                                 'editorial_help': None,
                                 'copy_source': '/preparation'}.get(
        value.get('kind'), '/preparation' if 'dossiers' in value and path == '/preparation' else None)
    menu = ''.join('<a href="' + href + '"' + (' aria-current="page"' if href == current else '') + '>' + label + '</a>'
                   for href, label in (('/', 'Accueil'), ('/evaluations-externes', 'Évaluations externes'),
                                       ('/epreuves', 'Épreuves'),
                                       ('/preparation', 'Mes cas d’usage'), ('/preparation/data', 'Mes données')))
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
            content += form(csrf, path, {key: str(submitted.get(key, '')) for key in ('action_id', 'revision')}
                            | {'kind': str(submitted.get('kind', 'clarify'))},
                '<label for="message">Votre précision ou correction</label>'
                '<textarea id="message" name="message" required maxlength="1000" rows="4"' + field_attributes('message') + '>' + text(submitted['message']) + '</textarea>' + field_error('message') +
                HONEYPOT +
                '<button type="submit">Corriger et renvoyer</button>')
        latest = value.get('step') if value.get('error_code') == 'SELECTION_SUPERSEDED' else None
        if latest and '/campaigns/' in path and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', str(latest)):
            content += ('<p><a href="' + text(path.split('/campaigns/', 1)[0] + '/campaigns/' + latest + '/conditions')
                        + '">Voir la dernière sélection</a></p>')
        if value.get('error_code') == 'OUTPUT_LIMIT_ABOVE_BOUND' and path.endswith('/configurations'):
            content += '<p><a class="button" href="' + text(path) + '">Revenir au choix des modèles</a></p>'
        # Attente expirée d'un lancement : l'état enregistré se lit sur son suivi, sans rien renvoyer
        followup = (re.fullmatch(r'(/preparation/dossiers/[A-Za-z0-9_-]{1,128}/campaigns/[A-Za-z0-9_-]{1,128})/(?:start|evaluate)', path)
                    if value.get('error_code') == 'RESPONSE_TIMEOUT' else None)
        if followup:
            content += '<p><a class="button" href="' + text(followup[1] + '/conditions') + '">Consulter l’état du lancement</a></p>'
        back_class = ('button sec' if type(submitted) is dict and ('request' in submitted or 'message' in submitted)
                      or value.get('error_code') == 'OUTPUT_LIMIT_ABOVE_BOUND' or followup else 'button')
        content += '<p><a class="' + back_class + '" href="/preparation">Retrouver mes cas d’usage</a></p>'
    elif value.get('kind') == 'contributions':
        title, content = render_contributions(value)
    elif value.get('kind') == 'session_bootstrap':
        title, content = render_bootstrap(value)
    elif value.get('kind') == 'legal':
        title, _, content = LEGAL_PAGES[value['path']]
        content = legal_layout(content)
    elif value.get('kind') == 'references':
        title, content = render_references(value)
    elif value.get('kind') == 'references_help':
        title, content = render_semantic_help(value)
    elif value.get('kind') == 'editorial':
        title, content = render_editorial(value)
    elif value.get('kind') == 'editorial_help':
        title, content = render_editorial_help(value)
    elif value.get('kind') == 'results':
        title, content = render_results(value)
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
        if status == 'connected' and value.get('limit_remaining_usd') is not None:
            content += readout('Solde restant selon OpenRouter', montant_lisible(value['limit_remaining_usd']) + ' USD')
        forms = personal_key_form(csrf, value, '/preparation/access', opened=status != 'connected') if status != 'unavailable' else ''
        content += workbench(forms, section('Dépense', '<p>Tous les appels passent par votre clé OpenRouter. '
                                                       'Son plafond est la seule limite de dépense.</p>'), 'Dépense')
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
    elif value.get('kind') == 'copy_source':
        title, content = render_copy_source(value, csrf)
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
        choice = '<form method="get" action="' + text(comparison['href'] + '/preview') + '">'
        choice += '<fieldset><legend>Pièces que la publication montrerait</legend>'
        choice += '<p class="hint">Si vous ne cochez aucune pièce, seuls les verdicts et leurs motifs apparaissent.</p>'
        for piece in value['pieces']:
            pid = piece['piece_id']
            choice += '<label><input type="checkbox" name="piece" value="' + text(pid) + '"'
            choice += (' checked' if pid in value['selected_links'] else '') + '> ' + text(piece_name(rows[pid], piece, candidates)) + '</label>'
        choice += '</fieldset><button type="submit">Actualiser l’aperçu</button></form>'
        choice += '<p class="hint">L’aperçu montre toute la comparaison : les filtres des résultats ne s’appliquent pas ici.</p>'
        # Le choix des pièces se lit avant le corps qu'il modifie
        content += workbench(projection_body(comparison, value['selected_links'], level=2), choice,
                             'Pièces à montrer', side_first=True)
    elif value.get('kind') == 'constructed_response':
        title = 'Réponse construite'
        declaree = 'producteur' in value
        phrase = ('Cette sortie construite déclare son producteur, sa date, sa source et son empreinte. '
                  if declaree else
                  'Cette sortie construite ne déclare pas de producteur, de date ni de source. ')
        content = state_block('unk', 'Sortie construite', 'Réponse fournie pour le contrôle',
            '<p>' + phrase + 'Elle n’est pas un reçu candidat. Ce contrôle ne qualifie pas le juge.</p>')
        rows = '<dt>Nom</dt><dd>' + text(value['name']) + '</dd>'
        if declaree:
            rows += ('<dt>Producteur</dt><dd>' + text(value['producteur']) + '</dd>'
                     '<dt>Date</dt><dd>' + text(date_lisible_utc(value['date'])) + '</dd>'
                     '<dt>Source</dt><dd>' + text(value['source']) + '</dd>')
        rows += '<dt>Empreinte</dt><dd><code>' + text(value['empreinte']) + '</code></dd>'
        content += section('Renseignements de la sortie construite', '<dl class="kv">' + rows + '</dl>')
        content += '<p><a href="' + text(value['dossier_href']) + '">Revenir au cas d’usage</a></p>'
    elif 'dossiers' in value:
        title = 'Mes cas d’usage'
        access = value.get('personal_access', {})
        if access.get('status') == 'connected':
            key = ('<p class="hint">Votre clé OpenRouter est enregistrée. ' + access_summary(access)
                   + ' <a href="/preparation/access">Gérer ma clé</a></p>')
        else:
            key = personal_key_form(csrf, access, '/preparation')
        lead = ('<p class="lead note">Vos cas d’usage restent privés dans ce navigateur. '
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
        # La clé se lit avant le formulaire qu'elle débloque
        creation = workbench(lead + creation, key, 'Ma clé OpenRouter', side_first=True)
        # Un visiteur qui revient cherche d'abord ses cas ; un premier visiteur, le formulaire
        content = listing_section + creation if value['dossiers'] else creation + listing_section
    elif value.get('kind') == 'honeypot_ack' or 'operation_id' in value:
        title = 'Demande enregistrée'
        url = ('/preparation' if value.get('kind') == 'honeypot_ack'
               else '/preparation/dossiers/' + value['dossier_id'])
        content = state_block('wait', 'Où j’en suis', 'L’assistant prépare sa réponse', '<p>Votre envoi est bien enregistré. Vous pouvez suivre l’avancement depuis la page de votre cas d’usage.</p>',
                              f'<a class="button" href="{text(url)}">Suivre mon cas d’usage</a>')
    else:
        dossier_id, revision = value['dossier_id'], value['revision']
        url = '/preparation/dossiers/' + dossier_id
        source = value.get('source')
        source_notice = value.get('source_notice') or {}
        title = ('Votre cas d’usage' if value['validation'] else 'Est-ce le travail que vous voulez tester ?'
                 if value['package'] else 'Votre dossier privé' if source else 'Précisons votre besoin')
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
        if source and value['stage'] == 'draft':
            tone, heading, next_step = ('action', 'Copie enregistrée',
                'Décrivez votre besoin et ce qui diffère de l’épreuve source pour préparer votre exemple.')
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
        # Contrat opérateur bloqué avant tout lancement : l'exemple est à revoir, aucune comparaison ne se lance
        operator_blocked = (value['validation'] and not automatic and qualification.get('qualification_status') == 'BLOCKED'
                            and not any(c['attempts'] for c in current_campaigns))
        if operator_blocked:
            tone, heading, next_step = ('err', 'Exemple à revoir avant comparaison',
                                        'Le détail de la vérification, plus bas, dit ce qui bloque. '
                                        'Corrigez l’exemple, puis validez-le de nouveau.')
        actions = ''
        # Une comparaison existe : l'encadré dit son état et mène à elle, jamais à un nouveau choix de modèles
        if value['validation'] and current_campaigns and not snapshot and not prior_revision and not operator_blocked:
            tone, heading, next_step, target, label = campaign_status(current_campaigns[-1], url)
            actions = f'<a class="button" href="{text(target)}">{text(label)}</a>'
        elif value['validation'] and value.get('qualified') and not snapshot and not prior_revision:
            actions = f'<a class="button" href="{text(url)}/configurations">Choisir les modèles</a>'
        example_mark = 'Copie d’épreuve' if source and value['package'] is None else 'Exemple inventé'
        content = '<p class="tag">' + text(example_mark + ' · version ' + str(revision)) + '</p>'
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
            references = [(identity, reference) for identity, reference in BENCHMARK_REFERENCES.items()
                          if reference['category'] == referral and reference.get('availability') == 'available']
            if references:
                content += section('Où comparer ce type de tâche', '<ul>' + ''.join(
                    '<li><a href="/evaluations-externes/' + text(identity) + '" rel="noreferrer">'
                    + text(reference['name']) + '</a> : ' + text(reference['work']) + '.</li>'
                    for identity, reference in references) + '</ul>')
            content += '<p><a href="/evaluations-externes" rel="noreferrer">Consulter les évaluations externes</a></p>'
            content += '<p><a class="button sec" href="/preparation">Décrire un autre cas d’usage</a></p>'
        links = [] if '>Actualiser</a>' in actions else [f'<a href="{text(path)}">Actualiser</a>']
        if path != url:
            links.append(f'<a href="{text(url)}">Version actuelle</a>')
        if revision > 1:
            links.append(f'<a href="{text(url)}/revisions/{revision - 1}">Version précédente</a>')
        if links:
            content += '<p class="hint">' + ' · '.join(links) + '</p>'
        main_start = len(content)
        payload = value['payload']
        if source:
            content += render_source_copy(source, source_notice)
        content += section('Votre besoin', ('<p>' + text(payload['request']) + '</p>') if payload['request']
                           else '<p>Votre besoin personnel n’est pas encore décrit.</p>', 'besoin')
        if editable and value['package'] is None:
            if source and not payload['request']:
                need_disabled = '' if can_submit and editable else ' disabled'
                content += section('Décrivez votre besoin', form(csrf, url + '/messages',
                    {'action_id': secrets.token_hex(16), 'revision': revision, 'kind': 'clarify'},
                    '<p id="source-need-help" class="hint">Dites ce que vous voulez comparer et ce qui diffère de '
                    'l’épreuve source. La notice et les pièces conservées ne changent pas. La préparation est payée '
                    'avec votre clé OpenRouter.</p>'
                    + ('' if can_submit else '<p class="hint"><a href="/preparation/access">Ajoutez votre clé OpenRouter</a> '
                       'pour préparer cet exemple. La copie reste consultable sans clé.</p>') +
                    '<label for="message">Votre besoin personnel</label>'
                    '<textarea id="message" name="message" rows="4" required minlength="' + str(REQUEST_MIN)
                    + '" maxlength="1000" aria-describedby="source-need-help"' + need_disabled + '></textarea>' + HONEYPOT +
                    '<button type="submit"' + need_disabled + '>Préparer mon exemple</button>'))
            else:
                content += section('Votre réponse', form(csrf, url + '/messages',
                    {'action_id': secrets.token_hex(16), 'revision': revision, 'kind': 'clarify'},
                    '<label for="message">Votre réponse à l’assistant</label><textarea id="message" name="message" rows="3" required maxlength="1000"' + disabled + '></textarea>' + HONEYPOT +
                    '<button type="submit"' + disabled + '>Envoyer ma réponse</button>'))
        if value.get('task_index'):
            content += '<details><summary>Historique des versions et des comparaisons</summary>' + render_task_index(value['task_index']) + '</details>'
        if value.get('message') and 'message' in value['message'] and not source:
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
                    if key == 'obligations':
                        # Une obligation composée montre ses éléments, jugés chacun séparément
                        body = '<ul>' + ''.join('<li>' + (text(item) if type(item) is str else text(item['description'])
                                                + listing(item['elements'])) + '</li>' for item in items) + '</ul>'
                    else:
                        body = listing(item['label'] for item in items) if key == 'quality' else listing(items)
                    groups += '<div class="grp ' + tone + '"><h3>' + group_title + '</h3>' + body + '</div>'
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
        cost_start = len(content)
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
        elif value.get('operations') == 0:
            # Zéro opération : aucune dépense à rapprocher, pas même une dépense inconnue
            content += '<p>Aucun appel modèle effectué pour ce dossier.</p>'
        else:
            content += '<p>Coût observé : inconnu, faute de justificatif de coût reçu pour cette préparation.</p>'
        if value.get('cost_reconciliation'):
            proof, cost = value['cost_reconciliation'], value['effective_cost']
            content += '<p>Coût corrigé après vérification : ' + text(montant_lisible(cost['amount']) + ' ' + cost['currency']) + '. Source : ' + text(
                proof['source']) + ', confirmée par ' + text(proof['actor']) + ' le ' + text(proof['observed_at']) + \
                '. Le justificatif d’origine reste inchangé.</p>'
        content, side = content[:cost_start], section('Coût de la préparation', content[cost_start:])
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
            validation = '<section id="validation"><h2>Valider l’exemple</h2>'
            if value['validation']:
                validation += '<p class="note">Vous avez validé cet exemple, dans cette version précise.</p>'
            else:
                validation += '<p class="note">Cet exemple n’est pas encore validé. Relisez-le, puis validez-le pour passer à la suite.</p>'
            if editable and value['stage'] == 'preview' and value['validation'] is None:
                validation += '<p>En validant, vous confirmez que cet exemple correspond à votre besoin. L’exemple est ensuite vérifié automatiquement, si ce service est disponible : cette vérification est payée avec votre clé OpenRouter. Valider ne lance aucun des modèles à comparer et ne publie rien.</p>'
                estimate = value.get('qualification_estimate')
                if estimate:
                    # Deux réserves distinctes, jamais additionnées (DESIGN.md, récapitulatif avant dépense)
                    validation += ('<p>Estimation avant consommation : une réserve est bloquée sur votre crédit avant chaque appel. '
                                'Pour la vérification de l’exemple : au plus ' + text(montant_lisible(estimate['check_usd']))
                                + ' USD réservé. Puis, pour le contrôle de chaque témoin, ' + text(str(estimate['witness_limit']))
                                + ' au plus : au plus ' + text(montant_lisible(estimate['witness_usd']))
                                + ' USD réservé par témoin. Un témoin est une réponse inventée, recevable ou fautive, soumise au '
                                'contrôle qui jugera les modèles. Le coût observé est relevé ensuite sur le justificatif de chaque appel.</p>')
                validation += '<div class="actionbar">' + form(csrf, url + '/validation', binding(dossier_id, revision, value['package_sha256']),
                                '<button type="submit">' + icon('i-check') + 'Oui, c’est le travail à tester</button>') + '</div>'
            validation += '</section>'
            side = validation + side
        content = content[:main_start] + workbench(content[main_start:], side, 'Valider l’exemple' if package else 'Coût de la préparation')
        if package:
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
        cases = value.get('cases', [])
        if cases:
            def case_state(case):
                if case['drifted']:
                    return 'contrat d’une ancienne version de l’exemple 1'
                if case['validated']:
                    return 'exemple vérifié' if case['qualified'] else 'validé, vérification en attente ou à revoir'
                return 'à valider' if case['stage'] == 'preview' else stages[case['stage']][1].lower()
            drifted = any(case['drifted'] for case in cases)
            content += section('Exemples de cette tâche', '<ul>' + ''.join(
                '<li>' + ('Exemple ' + str(case['number']) + ' (cette page)' if case['current'] else
                          '<a href="/preparation/dossiers/' + text(case['dossier_id']) + '">Exemple ' + str(case['number']) + '</a>')
                + ' : ' + text(case_state(case)) + '.'
                + (' Ce qui le distingue : ' + text(case['difference']) if case['difference'] else '') + '</li>'
                for case in cases) + '</ul>'
                + ('<p class="note">Le contrat de l’exemple 1 a changé depuis la création de certains exemples : '
                   'ces exemples ne suivent plus la même consigne. Ajoutez-les de nouveau depuis l’exemple 1.' if drifted else
                   '<p class="note">Tous les exemples suivent la même consigne, les mêmes livrables et les mêmes critères.')
                + ' Plusieurs exemples ne prouvent pas qu’ils représentent votre travail : chacun reste inventé, validé et '
                'vérifié séparément.</p>', 'cas')
        if value['validation'] and (not cases or cases[0]['current']) and not snapshot and not prior_revision and not referral:
            content += ('<details><summary class="button sec">' + icon('i-pen') + 'Ajouter un autre exemple à cette tâche</summary><div>'
                        '<p id="case-context-help">Le nouvel exemple reprend la consigne, les livrables, les critères et les '
                        'ambiguïtés acceptables de cette version. L’assistant prépare d’autres pièces et un autre résultat de '
                        'référence, à valider puis vérifier séparément. Cette préparation est payée avec votre clé OpenRouter. '
                        'Stabilisez cet exemple avant d’en ajouter d’autres : une correction ultérieure de sa consigne ne se '
                        'reporte pas aux exemples déjà créés.</p>' + form(csrf, '/preparation/dossiers',
                {'dossier_id': secrets.token_hex(16), 'action_id': secrets.token_hex(16), 'case_of': dossier_id},
                '<label for="case-context">Ce qui distingue cet exemple, par exemple la difficulté qu’il couvre</label>'
                '<textarea id="case-context" name="context" required maxlength="' + str(CONTEXT_MAX)
                + '" rows="2" aria-describedby="case-context-help"' + disabled + '></textarea>'
                '<button type="submit"' + disabled + '>Préparer cet exemple</button>') + '</div></details>')
        labels = {'PENDING': 'En attente', 'QUALIFIED': 'Vérification réussie',
                  'BLOCKED': 'Bloquée : la réponse de référence ou les contrôles ne sont pas assez établis'}
        if package and not referral:
            if automatic:
                # Parcours public : la qualification automatique suffit, aucune approbation opérateur n'est attendue
                content += '<details><summary>Détail de la vérification de l’exemple</summary>'
                content += section('Vérification', '<p>' + text('Vérification impossible pour le moment' if qualification.get('cause')
                                                                 else labels.get(qualification.get('qualification_status'), 'En attente')) + '</p>'
                                   + '<p>' + text(qualification['summary']) + '</p>'
                                   + listing(finding['text'] for finding in qualification.get('findings', []))
                                   + render_witnesses(qualification))
            else:
                content += '<details><summary>Vérification de l’exemple</summary>'
                # Contrat opérateur bloqué : ses constats remplacent le libellé générique, comme sur une page de refus
                content += section('Vérification', ('<p>Bloquée. Ce que la vérification de l’exemple a relevé :</p>' + listing(
                    finding['text'] for finding in qualification['findings'])) if qualification.get('findings')
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
        reason = reasons[state['reason']]
        if state['reason'] == 'access' and value.get('source') and value.get('package') is None:
            # Copie sans exemple préparé : la lecture n'exige ni clé ni appel, la clé sert ensuite
            reason = ('Votre copie est enregistrée. Vous pouvez la consulter sans clé ni appel aux modèles ; '
                      'la clé OpenRouter servira à préparer votre exemple.')
        content = ('<aside id="availability" class="availability" aria-label="État de la préparation"><p>'
                   + text(reason) + '</p></aside>' + content)
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
            .replace('{{identity}}', identity).replace('{{head}}', head).replace('{{content}}', lisible_en_liste(content)).encode('utf-8'))
