"""Rendu HTML du parcours privé : formulaires natifs, preuves inertes, gabarit unique.

Ce module ne touche ni au stockage, ni aux secrets, ni aux fournisseurs : il met en
forme les vues structurées renvoyées par l'exécuteur.
"""
from pathlib import Path
import re
import secrets
from urllib.parse import urlencode, urlsplit

from benchmark.preparation import CONTEXT_MAX, REQUEST_MAX, REQUEST_MIN, USEFUL_MAX, binding
from benchmark.storage import _strict_json as encode

from .campaign_views import (COMPARISON_FOCUS_SCRIPT, campaign_status, CUSTOM_MODELS_SCRIPT, render_attempt_detail, render_campaign_records,
                             render_campaign_launch_operator, render_campaign_launch_requester,
                             render_comparison, render_configurations, render_campaign_models, campaign_followup, contexte_vue, titre_vue)
from .fragments import (ETAPES, bouton, btn, bulle_assistant, bulle_vous, cadre, champ, cles, consigne,
                        critere_ligne, criteres_actifs, date_lisible_utc, disque, erreur_champ, etat, feuille, fil, form, ic, icon, jour_lisible,
                        lien, listing, montant_lisible, mot, notice_ligne, piste, puce, releves, ruban, section,
                        segments_liens, state_block, tampon, tete, text, tiroir)
from .projection import (projection_body)
from .results_views import (contexte_apercu, contexte_resultats, render_preview)
from .legal_views import LEGAL_PAGES, legal_layout
from .privacy_views import (PRIVACY_SCRIPT, actions_erreur, active_session, erreur_grande,
                            marqueur_activite, render_access, render_bootstrap, render_contributions,
                            render_delete_confirmation, render_deleted, render_privacy_controls, render_privacy_page)

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
    '/': 'Evaly, le benchmark personnalisé. Décrivez un cas d’usage de votre travail : Evaly prépare avec vous un exemple entièrement inventé, puis '
         'compare les modèles dans les mêmes conditions, coût observé compris.',
    **{path: description for path, (_, description, _) in LEGAL_PAGES.items()},
}
# Menu principal (A14) : Mes cas d'usage = saisie et cas en cours, Mes données = copies gardées par le navigateur ; Benchmarks publics au pied puis dans l'en-tête (unité suivante)
MENU = (('/', 'Accueil'), ('/preparation', 'Mes cas d’usage'), ('/preparation/data', 'Mes données'))
# Piège à robots : `hidden` le retire du rendu et de l'arbre d'accessibilité même sans feuille de style
HONEYPOT = ('<div class="website" hidden aria-hidden="true"><label for="website">Site web</label>'
            '<input id="website" name="website" autocomplete="off" tabindex="-1"></div>')
# Exemples qui défilent dans le champ vide : 76 caractères au plus pour tenir sur une ligne dès 768 px (B24)
IDEES = ('Je reçois des notes de réunion en vrac. Je veux la liste des actions.',
         'Je réponds à des clients mécontents. Je veux un brouillon de réponse poli.',
         'Je compare des devis fournisseurs. Je veux un tableau des prix et délais.',
         'Je relis des contrats de prestation. Je veux la liste des clauses à risque.')


def composeur(csrf, action, fields, *, request='', useful='', context='', bloque=False, erreur=None,
              envoi='Préparer mon exemple', titre_niveau='h1'):
    """Champ unique « Décrivez votre cas d’usage », résultat attendu et contexte dans un tiroir ; mêmes noms envoyés

    `bloque` : False, True (indisponible) ou la raison écrite sous le champ ; `erreur` : (nom du champ, message) ou None
    """
    en_erreur, message = erreur or (None, '')
    desactive = ' disabled' if bloque else ''
    decrit = 'request-help' + (' request-error' if en_erreur == 'request' else '') + (' availability' if bloque else '')
    raison = bloque if isinstance(bloque, str) else 'Préparation indisponible pour le moment.'
    idees = ''.join('<span>' + text(idee) + '</span>' for idee in IDEES + IDEES[:1])
    contenu = (
        '<' + titre_niveau + ' class="compo-titre"><label for="request">Décrivez votre cas d’usage</label></' + titre_niveau + '>'
        '<p class="compo-sous">Une ou deux phrases suffisent : ce que vous faites, et ce que vous attendez d’une IA.</p>'
        '<div class="compo-champ"><textarea id="request" name="request" rows="3" required minlength="' + str(REQUEST_MIN)
        + '" maxlength="' + str(REQUEST_MAX) + '" placeholder=" " aria-describedby="' + decrit + '"'
        + (' aria-invalid="true"' if en_erreur == 'request' else '') + desactive + '>' + text(request) + '</textarea>'
        '<span class="compo-idees" aria-hidden="true"><span class="defile">' + idees + '</span></span>'
        '<div class="compo-pied"><p class="aide" id="request-help">' + str(REQUEST_MIN) + ' caractères au minimum.<br>N’ajoutez aucune donnée personnelle, '
        'ni aucun document réel, même anonymisé.</p><button class="btn compo-envoi" type="submit"' + desactive + '>' + text(envoi) + ic('fleche') + '</button></div></div>'
        + (erreur_champ('request', message) if en_erreur == 'request' else '')
        # Sous le champ, seulement la raison d'un envoi refusé : `aria-describedby` du champ la désigne
        + ('<p class="compo-suite petit" id="availability">' + text(raison) + '</p>' if bloque else '')
        + tiroir('Préciser le résultat attendu ou le contexte',
                 champ('Résultat attendu', 'useful', ident='useful', maxlength=USEFUL_MAX, valeur=useful, desactive=bool(bloque),
                       aide='Par exemple : un tableau, une liste d’actions, un brouillon de courriel.',
                       erreur=message if en_erreur == 'useful' else '')
                 + champ('Contexte', 'context', ident='context', maxlength=CONTEXT_MAX, valeur=context, desactive=bool(bloque),
                         aide='Ce qu’un modèle doit savoir pour bien faire le travail.',
                         erreur=message if en_erreur == 'context' else ''),
                 resume='facultatif', ouvert=bool(useful or context or en_erreur in ('useful', 'context')))
        + HONEYPOT)
    return form(csrf, action, fields, contenu, form_id='nouveau', classe='compo')


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
    const progress = panel.querySelector('progress');
    if (progress) progress.hidden = true;
    document.querySelectorAll('.tourne').forEach(node => node.classList.remove('tourne'));
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
        return '<p>Aucun témoin n’a été contrôlé pour cette version : elle a été contrôlée avant les contrôles par témoins.</p>'

    def decision(row, status):
        if status is None:
            return 'non décidé'
        if row['eliminatory']:
            return {'PASS': 'faute absente', 'FAIL': 'faute présente'}.get(status, 'indéterminé')
        return {'PASS': 'satisfait', 'FAIL': 'non satisfait'}.get(status, 'indéterminé')
    content = ('<h3>Témoins contrôlés</h3><p class="petit">Un témoin est une réponse inventée, recevable ou fautive, soumise '
               'au contrôle qui jugera les modèles. Ce résultat dit ce que le contrôle a décidé sur ce témoin avec cette '
               'référence ; il ne prouve pas que l’attendu est juste.</p>')
    for witness in qualification['witnesses']:
        cost = witness['cost']
        content += ('<h4>' + text(WITNESS_KINDS[witness['kind']]) + ' : ' + text(WITNESS_STATES[witness['state']]) + '</h4>'
                    + '<p>Attendu justifié : ' + text(witness['justification']) + '</p>'
                    + listing(row['description'] + ' : attendu ' + decision(row, row['expected']) + ', décidé '
                              + decision(row, row['decided']) for row in witness['expected'])
                    + '<p class="petit">Contrôlé sur la version ' + text(str(witness['revision'])) + ' de l’exemple, avec sa référence'
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
  const items = [...document.querySelectorAll('.piste li')];
  const initial = document.querySelector('.piste li[aria-current]');
  function selectStep() {
    const section = ['#besoin', '#exemple', '#validation'].includes(location.hash);
    items.forEach(item => {
      const link = item.querySelector('a');
      const selected = section ? link !== null && new URL(link.href).hash === location.hash : item === initial;
      if (selected) item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
    });
  }
  window.addEventListener('hashchange', selectStep);
  selectStep();
})();"""


FILTRES_SCRIPT = """(() => {
  // Benchmarks publics : un filtre cliqué ou une recherche tapée remplace le contenu, la page garde sa position
  // Sans script, les filtres restent des liens et la recherche un formulaire GET
  const SEUIL = 3;
  const formulaire = () => document.querySelector('main form.g-recherche');
  const champ = () => formulaire()?.querySelector('input[name="q"]');
  let requete;
  async function charger(cible, ajouter) {
    requete?.abort();
    requete = new AbortController();
    const reponse = await fetch(cible, {headers: {Accept: 'text/html'}, cache: 'no-store', redirect: 'error', signal: requete.signal});
    if (!reponse.ok) throw new Error(String(reponse.status));
    const suivant = new DOMParser().parseFromString(await reponse.text(), 'text/html').querySelector('main');
    if (!suivant) throw new Error('page sans contenu');
    const saisie = champ(), valeur = saisie?.value, focus = document.activeElement === saisie, curseur = saisie?.selectionStart;
    document.querySelector('main').replaceChildren(...suivant.childNodes);
    history[ajouter ? 'pushState' : 'replaceState']({filtres: true}, '', cible);
    const neuf = champ();
    if (neuf && valeur !== undefined) {
      neuf.value = valeur;
      if (focus) { neuf.focus({preventScroll: true}); neuf.setSelectionRange(curseur, curseur); }
    }
  }
  // Recherche tapée : à partir de SEUIL caractères, le serveur filtre avec les filtres déjà choisis
  let minuterie;
  document.addEventListener('input', event => {
    if (!event.target.matches('main form.g-recherche input[name="q"]')) return;
    clearTimeout(minuterie);
    minuterie = setTimeout(() => {
      const form = formulaire(), q = event.target.value.trim();
      const cible = new URL(form.action);
      for (const [cle, valeur] of new FormData(form)) if (cle !== 'q' && valeur) cible.searchParams.set(cle, valeur);
      if (q.length >= SEUIL) cible.searchParams.set('q', q);
      else if (!new URL(location.href).searchParams.get('q')) return;
      if (cible.href === location.href) return;
      charger(cible.href, false).catch(error => { if (error.name !== 'AbortError') console.error('FILTRES_INDISPONIBLES'); });
    }, 300);
  });
  // Filtre cliqué : seul le contenu change ; en cas d'échec, navigation ordinaire
  document.addEventListener('click', event => {
    const lien = event.target.closest('main .segments-liens a');
    if (!lien || event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const groupe = lien.closest('[aria-labelledby]')?.getAttribute('aria-labelledby');
    const cible = new URL(lien.href), q = champ()?.value.trim() ?? '';
    if (q.length >= SEUIL) cible.searchParams.set('q', q); else cible.searchParams.delete('q');
    charger(cible.href, true)
      .then(() => document.querySelector('main [aria-labelledby="' + groupe + '"] a[aria-current]')?.focus({preventScroll: true}))
      .catch(error => { if (error.name !== 'AbortError') location.assign(cible.href); });
  });
  window.addEventListener('popstate', () => location.reload());
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
    # Seul le catalogue des benchmarks publics a des filtres ; ses fiches restent sans script
    if value.get('kind') == 'references' and not value.get('reference_id'):
        return FILTRES_SCRIPT
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
                  'Elle ne doit pas devenir un résultat observé Evaly. Le classement général n’est pas repris.',
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
                  'Leur indice global n’entre pas dans Evaly. La date de source concerne la méthode. '
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


EDITORIAL_STATUS = {'ABSENT': 'Qualification absente', 'QUALIFIED': 'Qualification réussie',
                    'BLOCKED': 'Qualification bloquée'}
# Aide sémantique : le web ne connaît pas l'état du service (il vit dans l'exécuteur) ; le lien reste masqué tant que
# `serve_web` ne l'a pas activé, comme la désactivation par défaut du PRD
SEMANTIC_HELP_ENABLED = False
INCONNU = '<span class="g-inconnu">inconnu</span>'
CAUTION = ('Des benchmarks reconnus, publiés par d’autres. Utiles pour un travail générique, comme les mathématiques ou le code. '
           'Ils ne prouvent pas la réussite sur votre cas d’usage.')


def version_lisible(version_id):
    """« version-1 » devient « Version 1 », « version-longue » devient « Version longue » ; l'identifiant reste dans le reçu technique"""
    mots = str(version_id).replace('-', ' ').replace('_', ' ').strip()
    return mots[:1].upper() + mots[1:]


def valeur_ou_inconnu(value):
    """Valeur échappée ; absente : « inconnu »"""
    return text(jour_lisible(value)) if value else INCONNU


def reference_mots(reference):
    """Mots d'état d'une fiche : vérification éditoriale et disponibilité de la source"""
    verifiee = reference_verified(reference)
    disponible = reference.get('availability') == 'available'
    return (mot('Vérifiée', 'ok') if verifiee else mot('Vérification insuffisante', 'vig'),
            mot('Source disponible', 'ok') if disponible else mot('Source retirée', 'ko'))


def reference_adresse(**criteria):
    """Adresse du répertoire avec les critères non vides"""
    query = urlencode([(key, value) for key, value in criteria.items() if value])
    return '/evaluations-externes' + ('?' + query if query else '')


def carte_reference(identity, reference):
    verification, disponibilite = reference_mots(reference)
    categorie = puce(REFERENCE_CATEGORIES.get(reference.get('category'), 'Catégorie inconnue'))
    return ('<li class="g-fiche"><div class="g-fiche-haut">' + categorie + '</div><h3><a href="/evaluations-externes/' + text(identity) + '">'
            + text(reference['name']) + '</a></h3><p class="g-mesure"><span class="etiq">Travail mesuré</span>' + text(reference.get('work') or 'Inconnu')
            + '</p><div class="g-fiche-mots">' + verification + disponibilite + '</div><span class="g-ouvrir" aria-hidden="true">' + ic('fleche') + '</span></li>')


def selection_tenue():
    """« Comment cette sélection est tenue » : la date de relecture est la plus récente des fiches vérifiées"""
    dates = [reference['checked_at'] for reference in BENCHMARK_REFERENCES.values()
             if reference.get('availability') == 'available' and reference_verified(reference)]
    relecture = ' La dernière relecture date du ' + jour_lisible(max(dates)) + '.' if dates else ''
    return tiroir('Comment cette sélection est tenue',
                  '<p>Evaly choisit et tient à jour cette liste. Chaque fiche est relue avant d’être affichée.' + relecture + '</p>'
                  '<dl class="g-lire">'
                  '<div><dt>Ce que la relecture vérifie</dt><dd>Trois points : qui publie le benchmark public, ce qu’il mesure, et ce qu’il ne mesure pas.</dd></div>'
                  '<div><dt>Vérifiée</dt><dd>Les trois points sont établis.</dd></div>'
                  '<div><dt>Vérification insuffisante</dt><dd>Au moins un point reste inconnu. La fiche reste consultable, mais ce n’est pas une recommandation.</dd></div>'
                  '<div><dt>Source disponible</dt><dd>La page officielle du benchmark public est accessible depuis la fiche. Ce contrôle est fait à part de la relecture.</dd></div>'
                  '<div><dt>Ce que la liste ne fait pas</dt><dd>Elle n’est pas complète. Elle ne classe pas les benchmarks publics entre eux et ne donne aucune note globale.</dd></div>'
                  '</dl><p>Un bon résultat sur un benchmark public ne prouve pas qu’un modèle réussira votre cas d’usage. '
                  'Pour le savoir, comparez les modèles sur votre propre exemple.</p>')


def render_reference_notice(identity, reference):
    """Fiche d'un benchmark public : état de la vérification, ce qu'il mesure, ses limites, la source officielle"""
    url = reference_url(reference)
    verification, disponibilite = reference_mots(reference)
    retour = fil(('/evaluations-externes', 'Tous les benchmarks publics'))
    if reference.get('availability') != 'available':
        carte = etat('ko', 'Source retirée', 'Cette source n’est plus disponible',
                     'Le lien n’est plus proposé. Vérification insuffisante : cette source n’est pas une recommandation.')
    elif reference_verified(reference):
        carte = etat('ok', 'Vérifiée', 'Vérification éditoriale effectuée',
                     'Le ' + jour_lisible(reference['checked_at']) + ' : origine, objet et limites. Le lien est contrôlé à part.')
    else:
        carte = etat('vig', 'Vérification insuffisante', 'Cette source n’est pas une recommandation',
                     'Origine, objet ou limites ne sont pas établis : plusieurs champs restent inconnus.')
    if reference.get('availability') == 'available' and url:
        action = ('<div class="decision"><span class="etiq">Source officielle</span><h2>Les résultats détaillés restent chez la source.</h2>'
                  '<p class="g-url">' + text(url) + '</p><a class="btn" href="' + text(url) + '" rel="noreferrer noopener">Ouvrir la source officielle'
                  + ic('lien') + '</a></div>')
        suite = lien('/preparation', 'Décrire mon cas d’usage')
    else:
        action = ('<div class="carte carte-douce"><span class="etiq">' + ('Adresse retirée' if reference.get('availability') != 'available' else 'Lien indisponible')
                  + '</span><p class="g-url">' + (text(url) if url else 'Inconnue')
                  + '</p><p class="petit">' + ('Adresse indiquée à titre d’archive, sans lien.' if reference.get('availability') != 'available'
                                               else 'Lien officiel indisponible.') + '</p></div>')
        suite = btn('/preparation', 'Décrire mon cas d’usage')
    infos = cles([('Version', valeur_ou_inconnu(reference.get('version'))), ('Date de la source', valeur_ou_inconnu(reference.get('published_at'))),
                  ('Vérification éditoriale', valeur_ou_inconnu(reference.get('checked_at')))])
    corps = ('<div class="cadre g-pile">' + carte + '</div><div class="cadre"><div class="colonnes g-fiche-page"><div class="pile-l">'
             '<section aria-labelledby="g-mesure"><span class="etiq">Elle mesure</span><h2 id="g-mesure" class="g-h">' + text(reference.get('work') or 'Inconnu')
             + '</h2><p class="texte-l">' + (text(reference['scope']) if reference.get('scope') else 'Portée : ' + INCONNU) + '</p></section>'
             '<section aria-labelledby="g-limites" class="carte g-limites"><div class="g-limites-tete">' + ic('alerte')
             + '<h2 id="g-limites">Ses limites</h2></div><p>' + (text(reference['limits']) if reference.get('limits') else INCONNU) + '</p></section>'
             '<p class="petit">' + text(CAUTION) + '</p><div class="actions">' + suite + '</div></div>'
             '<aside class="cote" aria-label="Source et dates"><div class="carte">' + infos + '</div>' + action + '</aside></div></div>')
    contexte = puce(REFERENCE_CATEGORIES.get(reference.get('category'), 'Catégorie inconnue')) + verification + disponibilite
    return reference['name'], corps, dict(piste_html=retour, contexte_html=contexte, cadre_propre=True)


def render_references(value):
    """Répertoire des benchmarks publics ou fiche d'un benchmark ; renvoie (titre, contenu, en-tête)"""
    if value.get('reference_id'):
        return render_reference_notice(value['reference_id'], BENCHMARK_REFERENCES[value['reference_id']])
    available = {identity: reference for identity, reference in BENCHMARK_REFERENCES.items()
                 if reference.get('availability') == 'available'}
    categories = {reference['category']: REFERENCE_CATEGORIES.get(reference['category'], reference['category'])
                  for reference in available.values()}
    query, category, verification = (str(value.get(key) or '') for key in ('q', 'category', 'verification'))
    selection = (segments_liens('Travail mesuré', 'f-cat', [('', 'Tous')] + list(categories.items()), category,
                                lambda choice: reference_adresse(q=query, category=choice, verification=verification))
                 + segments_liens('Vérification éditoriale', 'f-verif', [('', 'Toutes')] + list(REFERENCE_VERIFICATIONS.items()), verification,
                                  lambda choice: reference_adresse(q=query, category=category, verification=choice)))
    formulaire = ('<form class="g-recherche" method="get" action="/evaluations-externes" role="search"><div class="g-ligne">'
                  + champ('Rechercher un benchmark public', 'q', zone=False, type_='search', ident='q', valeur=query) + bouton('Rechercher', icone='loupe') + '</div>'
                  '<input type="hidden" name="category" value="' + text(category) + '"><input type="hidden" name="verification" value="' + text(verification)
                  + '">' + selection + (('<div class="actions">' + lien('/evaluations-externes/aide', 'Demander une aide sémantique facultative', 'question') + '</div>')
                                        if SEMANTIC_HELP_ENABLED else '') + '</form>')
    actifs = criteres_actifs(([('Recherche : ' + query)] if query else []) + (['Travail mesuré : ' + categories.get(category, category)] if category else [])
                             + (['Vérification : ' + REFERENCE_VERIFICATIONS.get(verification, verification)] if verification else []), '/evaluations-externes')
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
    if not available:
        corps = etat('inc', 'Répertoire vide', 'Aucun benchmark public disponible', 'Le répertoire ne contient encore aucune fiche.')
    elif not found:
        corps = (etat('vig', 'Aucun résultat', 'Aucun benchmark public ne correspond à cette recherche',
                      'Recherche : « ' + query + ' ». Modifiez la recherche ou les filtres.') if query else
                 etat('vig', 'Aucun résultat', 'Aucun benchmark public ne correspond à ces filtres', 'Changez un filtre, ou effacez-les.'))
    else:
        compte = str(len(found)) + (' benchmarks publics' if len(found) > 1 else ' benchmark public') + (' sur ' + str(len(available)) if len(found) != len(available) else '')
        corps = ('<h2 class="cache">Résultats</h2><p class="g-compte" role="status" data-un="benchmark public" data-plusieurs="benchmarks publics">' + compte
                 + '</p><ul class="g-grille g-liste-ext">' + ''.join(carte_reference(identity, reference) for identity, reference in found) + '</ul>')
    # Seuls une recherche sans résultat et un répertoire vide proposent de décrire un besoin
    suite = '<div class="actions">' + lien('/preparation', 'Décrire mon cas d’usage') + '</div>' if not found else ''
    return 'Benchmarks publics', ('<div class="cadre g-pile">' + formulaire + actifs + corps + selection_tenue() + suite + '</div>'), dict(
        sous_titre_html='<p class="texte-l g-sous">' + text(CAUTION) + '</p>', cadre_propre=True)


HELP_LABELS = {'correspond': ('Correspondance forte', 'ok'), 'partiel': ('Correspondance partielle', 'att')}
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
HELP_TEXTE = (
    'Cette aide facultative compare votre formulation au travail mesuré par les notices publiques '
    'admissibles. Elle utilise TypeSafe et son modèle Jev. Elle ne remplace pas la recherche simple : '
    'celle-ci reste disponible sans cette aide.',
    'Données transmises : la formulation que vous soumettez et les informations publiques utiles des '
    'notices retenues. Aucun cas d’usage privé, contribution, référence de jugement, historique, clé ni cookie n’est transmis.',
    'TypeSafe traite ces données selon son traitement standard. Evaly ne promet ni absence de '
    'conservation, ni localisation exclusivement européenne, ni absence d’entraînement. N’écrivez aucune '
    'information personnelle ou confidentielle : cette consigne ne garantit pas leur absence.',
    'Evaly finance cette aide sur une enveloppe limitée, distincte des clés OpenRouter personnelles. '
    'Seuls des reçus techniques et comptables sont conservés, sans le texte recherché. Une probabilité ne '
    'prouve ni adéquation, ni qualification, et cette aide ne lance aucun benchmark.')


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


def help_form(value, action='/evaluations-externes/aide', simple='/evaluations-externes'):
    submission_id = value.get('submission_id')
    if type(submission_id) is not str or re.fullmatch('[0-9a-f]{32}', submission_id) is None:
        return ''
    formulation = value.get('formulation')
    formulation = formulation if type(formulation) is str else ''
    return ('<form method="post" action="' + text(action) + '">'
            '<input type="hidden" name="submission_id" value="' + text(submission_id) + '">'
            + champ('Formulation à comparer', 'formulation', grand=True, ident='formulation', valeur=formulation,
                    aide='Décrivez le travail recherché, sans information personnelle ni confidentielle. '
                         'Une demande peut contenir plusieurs travaux.')
            + HONEYPOT + '<div class="actions g-bas">' + bouton('Demander l’aide', icone='question') + lien(simple, 'Recherche simple', 'loupe')
            + '</div></form>')


def render_help_receipt(receipt):
    """Reçu technique commun, sans texte recherché ni identifiant brut de notice"""
    if not receipt:
        return ''
    cost = receipt.get('cost') or {}
    notices = receipt.get('notice_labels')
    if notices is None:
        notices = receipt.get('notices') or []
    return tiroir('Reçu technique de cette demande', cles([
        ('Usage', text(receipt.get('usage'))), ('Version des questions', text(receipt.get('questions_version'))),
        ('Modèle demandé', text(receipt.get('model_requested'))), ('Modèle observé', text(receipt.get('model_observed') or 'Inconnu')),
        ('Notices appréciées', text(', '.join(notices) or 'Aucune')),
        ('Jetons d’entrée', text(receipt.get('input_tokens') if receipt.get('input_tokens') is not None else 'Inconnus')),
        ('Jetons de sortie', text(receipt.get('output_tokens') if receipt.get('output_tokens') is not None else 'Inconnus')),
        ('Coût calculé', text('inconnu' if cost.get('status') != 'KNOWN' else montant_lisible(cost.get('amount')) + ' ' + str(cost.get('currency')))),
        ('Base du coût', text(cost.get('source') or 'Inconnue'))])
        + '<p class="petit">Un coût calculé depuis les jetons reçus et le tarif daté reste '
        'distinct d’une facture. Un coût inconnu conserve sa réserve.</p>')


def render_aide(value):
    """Aide sémantique facultative des Benchmarks publics ; renvoie (titre, contenu, en-tête)"""
    stage = value.get('stage')
    base = '/evaluations-externes'
    simple = (base, 'Recherche simple')
    texte_complet = tiroir('Texte complet : données, fournisseur et coût', ''.join('<p>' + text(paragraphe) + '</p>' for paragraphe in HELP_TEXTE))
    etats = ''
    if stage == 'pending':
        etats = etat('att', 'En cours', 'Aide en cours', 'La demande est enregistrée. Cette page ne relance aucun appel ; actualisez-la dans un instant.')
    elif stage == 'unavailable':
        etats = etat('vig', 'Indisponible', 'Aide indisponible', HELP_REASONS.get(value.get('reason'), 'L’aide n’est pas disponible.'))
    elif stage == 'none':
        etats = etat('inc', 'Aucun résultat', 'Aucune suggestion', corps_html=(
            '<p role="status">Aucune notice ne correspond à cette formulation. La recherche simple reste disponible.</p>'))
    elif stage == 'suggestions':
        count = len(value.get('suggestions') or [])
        etats = etat('ok', 'Résultat', str(count) + (' suggestions' if count > 1 else ' suggestion'), 'Chaque appréciation ne prouve ni adéquation ni qualification.')
    if value.get('dropped'):
        etats += etat('vig', 'Suggestion retirée', 'Une suggestion a été retirée',
                      'Sa notice n’est plus disponible ou sa vérification éditoriale ne suffit plus.', ident='etat-retiree')
    suggestions = ''
    if stage == 'suggestions':
        cartes = ''
        for suggestion in value.get('suggestions') or []:
            libelle, ton = HELP_LABELS.get(suggestion.get('label'), ('Correspondance', 'inc'))
            carte = carte_reference(suggestion['id'], suggestion.get('reference') or {})
            cartes += carte.replace('<li class="g-fiche">', '<li class="g-fiche"><div class="g-corr">' + mot(libelle, ton) + '</div>', 1)
        suggestions = ('<section><h2>Suggestions</h2><p class="texte-l">Chaque notice est appréciée à part. Les correspondances '
                       'reposent sur des critères fermés et les champs publiés. Aucune suggestion ne qualifie une source.</p>'
                       + ('<ul class="g-grille">' + cartes + '</ul>' if cartes else '<p>Aucune suggestion.</p>') + '</section>')
    formulaire = help_form(value, base + '/aide', base) if stage in ('form', 'unavailable') else ''
    cote = ''
    if formulaire:
        cote = ('<aside class="cote" aria-label="Ce qui est transmis"><div class="carte"><span class="etiq">Ce qui part</span>'
                '<ul class="g-liste"><li>Votre formulation et les informations publiques des notices retenues.</li>'
                '<li>Jamais : cas d’usage privé, contribution, historique, clé ni cookie.</li></ul></div>'
                '<div class="carte carte-douce">' + ic('cadenas') + '<p class="petit">TypeSafe ne promet ni absence de conservation, '
                'ni localisation européenne, ni absence d’entraînement. N’écrivez rien de personnel ni de confidentiel.</p></div></aside>')
    liens = ('<div class="actions">' + lien(base + '/aide', 'Nouvelle demande', 'actualiser') + lien(simple[0], simple[1], 'loupe')
             + lien('/preparation', 'Décrire mon cas d’usage') + '</div>')
    sous = '<p class="texte-l g-sous">Elle compare votre phrase au travail mesuré. Elle ne remplace pas la recherche simple.</p>' if formulaire else ''
    corps = ('<div class="cadre g-pile">' + etats + suggestions + '</div>' if etats or suggestions else '')
    corps += ('<div class="cadre"><div class="colonnes"><div class="pile">' + formulaire + texte_complet + '</div>' + cote + '</div></div>'
              if formulaire else '<div class="cadre g-pile">' + render_help_receipt(value.get('receipt')) + texte_complet + liens + '</div>')
    return 'Aide sémantique facultative', corps, dict(
        piste_html=fil((base, 'Benchmarks publics')), contexte_html=puce('Facultative') + puce('Sans compte ni clé'),
        sous_titre_html=sous, cadre_propre=True)


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
    cles = [cle for cle, _ in ETAPES]
    return piste(cles[current - 1], {cle: href for cle, href in zip(cles, targets) if href is not None})


MOTS_ACCUEIL = ('votre travail', 'vos notes', 'vos devis', 'vos rapports', 'vos tableaux', 'votre travail')
COULOIRS_ACCUEIL = (('Modèle A', 'raisonnement élevé', 'ok'), ('Modèle B', 'raisonnement faible', 'ok'),
                    ('Modèle C', 'raisonnement fixe', 'ko'))
ETAPES_ACCUEIL = (('Besoin', 'Vous décrivez votre cas d’usage en quelques phrases. L’assistant vous pose une question si besoin.'),
                  ('Exemple', 'L’assistant crée un exemple réaliste : une consigne et des documents inventés.'),
                  ('Validation', 'Vous corrigez l’exemple si besoin, puis vous le validez.'),
                  ('Modèles', 'Vous choisissez les modèles à comparer et voyez le coût estimé avant de lancer.'),
                  ('Résultats', 'Pour chaque modèle : son verdict, ses preuves et son coût observé.'))


def render_home():
    """Accueil : accroche animée, banc d'essai de démonstration (modèles fictifs), cinq étapes, règle du verdict"""
    # La mesure empile tous les mots : la fente prend la largeur du plus long
    fente = ('<span class="fente" aria-hidden="true"><span class="mesure">' + ''.join('<span>' + m + '</span>' for m in sorted(set(MOTS_ACCUEIL)))
             + '</span><span class="defile">' + ''.join('<span>' + m + '</span>' for m in MOTS_ACCUEIL) + '</span></span>')
    couloirs = ''.join(
        '<li class="couloir"><span class="couloir-nom">' + nom + '<small>' + raisonnement + '</small></span>'
        '<span class="voie"><span class="coureur"><span class="trace"></span>' + disque() + '</span></span>' + tampon(verdict) + '</li>'
        for nom, raisonnement, verdict in COULOIRS_ACCUEIL)
    stations = ''.join('<li><span class="n">0' + str(numero) + '</span><h3>' + nom + '</h3><p>' + phrase + '</p></li>'
                       for numero, (nom, phrase) in enumerate(ETAPES_ACCUEIL, 1))
    return (
        '<section class="une"><div class="cadre">'
        '<h1><span class="ligne">Quel modèle d’IA</span><span class="ligne">pour <span class="colle">' + fente
        + '<span class="cache">votre travail</span>&nbsp;?</span></span></h1><div class="une-bas"><div>'
        '<p class="texte-l accroche">Le bon modèle d’IA dépend de votre cas d’usage. Evaly le soumet à plusieurs modèles, dans les mêmes conditions, '
        'et vous montre lequel réussit vraiment.</p></div>'
        '<ul class="atouts" aria-label="Ce qu’Evaly vous apporte"><li>' + ic('cible') + '<span><b>Votre cas d’usage</b>, pas un classement générique</span></li>'
        '<li>' + ic('check') + '<span><b>Un verdict prouvé</b> pour chaque modèle</span></li>'
        '<li>' + ic('horloge') + '<span><b>Le coût observé</b> de chaque réponse</span></li></ul>'
        '<div class="actions">' + btn('/preparation', 'Décrire mon cas d’usage', xl=True, avant='stylo')
        + lien('/preparation', 'Mes cas d’usage') + '</div></div>'
        '<figure class="banc" aria-labelledby="banc-legende"><div class="banc-tete"><span><b>●</b> Banc d’essai · démonstration</span>'
        '<span class="exemple">' + ic('doc') + 'Exemple : notes de réunion inventées</span></div><ol class="couloirs course">' + couloirs + '</ol>'
        '<figcaption class="banc-pied" id="banc-legende"><span><strong>Même consigne, mêmes pièces, mêmes conditions.</strong> Un verdict par modèle.</span>'
        '<span>Aucune note globale, aucun classement universel.</span></figcaption></figure></div></section>'
        '<section class="zone"><div class="cadre"><div class="zone-titre"><span class="etiq">Le parcours</span>'
        '<h2>De votre cas d’usage au verdict, en cinq étapes</h2></div><ol class="stations">' + stations + '</ol></div></section>'
        '<section class="zone"><div class="cadre"><div class="regle"><div><span class="etiq">Comment lire un verdict</span>'
        '<h2>Un seul manquement suffit</h2><p class="texte-l">Une exigence non prouvée, ou une erreur éliminatoire, écarte le modèle. Le reste n’y change rien.</p></div>'
        '<div><ul class="controle" aria-label="Exemple de contrôle d’une réponse">'
        '<li><span class="ok">' + ic('check') + '</span>Toutes les actions sont retrouvées</li>'
        '<li><span class="ok">' + ic('check') + '</span>Chaque action a un responsable</li>'
        '<li><span class="ko">' + ic('croix') + '</span><span><span class="barre">Échéance inventée</span> : erreur éliminatoire</span></li>'
        '<li><span class="ok">' + ic('check') + '</span>Format demandé respecté</li>' + tampon('ko', xl=True) + '</ul></div></div>'
        '<ul class="faits"><li><strong>Un verdict pour ce test précis</strong>Le verdict concerne le modèle avec les réglages de votre test. '
        'Il ne juge pas ce modèle en général.</li>'
        '<li><strong>Un coût réellement observé</strong>Evaly relève le coût de chaque réponse sur le justificatif d’OpenRouter. '
        'S’il manque, le coût est affiché « inconnu ».</li>'
        '<li><strong>Aucun document réel</strong>Les documents de l’exemple sont entièrement inventés. '
        'Vous ne transmettez aucun dossier réel, même anonymisé.</li></ul></div></section>')


def disponibilite_cas(raison):
    """Carte d'état et raison écrite sous le champ quand l'envoi est refusé : (HTML de la carte, bloque)"""
    actualiser = btn('/preparation', 'Actualiser', sec=True, icone=None, avant='actualiser')
    return {
        'access': (etat('vig', 'Clé requise', 'Ajoutez votre clé OpenRouter', 'Sans clé, l’assistant ne peut pas préparer d’exemple.',
                        btn('/preparation/access', 'Ajouter ma clé', sec=True, avant='cle')),
                   'Ajoutez d’abord votre clé OpenRouter.'),
        'restore': (etat('ko', 'Indisponible', 'Service momentanément indisponible', 'Réessayez plus tard. Vos cas restent en place.', actualiser), True),
        'interrupted': (etat('inc', 'Résultat inconnu', 'Une préparation a été interrompue',
                             'Nous ne savons pas si le modèle a répondu. Rien n’est relancé ; cette opération sera close automatiquement.'),
                        'Nouvelle préparation possible une fois l’opération close.'),
        'unconfigured': (etat('ko', 'Indisponible', 'Préparation indisponible', 'Aucun assistant n’est en service pour le moment.'), True),
        'waiting': (etat('att', 'En cours', 'Votre préparation précédente est en cours', 'Actualisez la page pour voir où elle en est.', actualiser),
                    'Nouvelle préparation possible quand la précédente est terminée.'),
        'unresolved': (etat('vig', 'Envois fermés', 'Un appel précédent n’est pas résolu',
                            'Le résultat ou le coût d’un appel précédent n’est pas encore connu.'), True),
    }.get(raison, ('', True))


def render_cases(value, csrf):
    """Page Benchmark (/preparation) : état du service, champ unique de description, renvoi vers Mes données, bande de la clé"""
    state = value.get('availability', {})
    carte, bloque = ('', False) if state.get('can_submit', False) else disponibilite_cas(state.get('reason'))
    if value.get('deleted'):
        carte = render_deleted() + carte
    creation = composeur(csrf, '/preparation/dossiers', {'dossier_id': secrets.token_hex(16), 'action_id': secrets.token_hex(16)}, bloque=bloque)
    bande = ('<div class="ge-bande">' + ic('cle') + '<p>La clé OpenRouter paie les appels aux modèles.</p>'
             + lien('/preparation/access', 'Ma clé OpenRouter') + '</div>')
    # Les cas et leurs copies se lisent dans Mes données ; `value['dossiers']` reste dans la charge utile sans être lu ici
    return ('<section class="compo-zone"><div class="cadre">' + carte + creation + '</div></section>'
            '<div class="cadre ge-pile ge-mes-cas">' + marqueur_activite(value.get('privacy'), csrf)
            + '<p class="petit">Retrouvez vos cas d’usage déjà décrits dans <a href="/preparation/data">Mes données</a>.</p>' + bande + '</div>')


def render_task_index(task):
    """Exemples validés d'un cas d'usage et leurs benchmarks, visibles par vous seul"""
    content = '<p class="petit">Visible par vous seul : rien n’est publié dans le catalogue public.</p>'
    content += '<p class="petit">Versions : ' + ' · '.join(
        '<a href="' + text(task['href']) + '/revisions/' + str(revision) + '">' + str(revision) + '</a>'
        for revision in task['revisions']) + '.</p>'
    for version in task['versions']:
        # Le numéro distingue deux benchmarks figés dans la même seconde
        items = ''.join('<li><span>Benchmark ' + str(number) + ' du ' + text(date_lisible_utc(campaign['frozen_at'])) + '</span>'
                        + lien(campaign['href'], 'Voir les résultats') + '</li>' for number, campaign in enumerate(version['campaigns'], 1))
        content += ('<div id="version-' + text(version['version']) + '"><h3>Exemple validé ' + text(version['version']) + '</h3><ul>'
                    + (items or '<li><span>Aucun benchmark lancé avec cet exemple.</span></li>') + '</ul><div class="actions">'
                    + lien(task['href'] + '/revisions/' + str(version['revision']), 'Voir cet exemple') + '</div></div>')
    if not task['versions']:
        content += '<p>Aucun exemple validé pour l’instant.</p>'
    return content


def render_catalogue(value):
    """Exemples validés et benchmarks de tous les cas d'usage de ce navigateur"""
    corps = ''.join('<article class="ge-cat"><h2><a href="' + text(task['href']) + '">' + text(task['need']) + '</a></h2>' + render_task_index(task) + '</article>'
                    for task in value['tasks'])
    if not value['tasks']:
        corps = '<p>Aucun cas d’usage validé pour l’instant. Validez un exemple pour le retrouver ici.</p>'
    corps = '<div class="ge-pile">' + corps + '<p>' + lien('/preparation', 'Revenir à mes cas d’usage', 'retour') + '</p></div>'
    return 'Exemples validés et benchmarks', corps, dict(sous_titre_html=(
        '<p class="texte-l">Visible par vous seul, dans ce navigateur. Pour chaque cas validé, retrouvez ses exemples et ses benchmarks.</p>'))


def render_attente(value):
    """Demande enregistrée : l'envoi est pris en compte, l'assistant prépare sa réponse"""
    url = '/preparation' if value.get('kind') == 'honeypot_ack' else '/preparation/dossiers/' + value['dossier_id']
    lignes = (('coche', 'check', 'Envoi enregistré', mot('Fait', 'ok')), ('coche coche-att', 'horloge', 'L’assistant prépare sa réponse', mot('En cours', 'att')),
              ('coche coche-vide', 'question', 'Une question, ou votre exemple', mot('À venir', 'inc')))
    liste = ''.join('<li><span class="' + classe + '">' + ic(icone) + '</span><span>' + text(libelle) + '</span>' + statut + '</li>'
                    for classe, icone, libelle, statut in lignes)
    corps = ('<div class="gb-seul gb-cote">' + etat('att', 'Enregistrée', 'L’assistant prépare sa réponse',
             'Votre envoi est bien enregistré. Vous pouvez quitter cette page et revenir plus tard.', btn(url, 'Suivre mon cas d’usage'),
             marque_html=disque('disque-l tourne')) + '<ul class="checklist carte" aria-label="Étapes de la préparation">' + liste + '</ul></div>')
    return 'Demande enregistrée', corps, dict(piste_html=piste('besoin'))


def render_source_copy(source, notice):
    """Exemple source conservé dans le dossier, distinct du besoin personnel : titre, travail, version, cas, limites, pièces"""
    absente = source.get('qualification_status') in (None, 'ABSENT') or source.get('qualification_status') not in EDITORIAL_STATUS
    statut = EDITORIAL_STATUS.get(source.get('qualification_status'), 'Qualification absente')
    phrase = ('Aucune qualification n’est publiée pour cet exemple source. Votre exemple est contrôlé à part : son contrat et sa référence sont '
              'construits puis contrôlés pour votre cas d’usage.' if absente else
              'Sa qualification publiée ne qualifie pas ce cas d’usage : votre exemple est contrôlé à part ; son contrat et sa référence sont '
              'construits puis contrôlés pour votre cas d’usage.')
    groupes = ''
    if source.get('cases'):
        groupes += '<h3>Cas de l’exemple source</h3>' + listing(source['cases'])
    if source.get('limits'):
        groupes += '<h3>Limites de l’exemple source</h3>' + listing(source['limits'])
    groupes += '<h3>Pièces</h3><ul class="pieces">' + ''.join(
        feuille(piece['label'], '', piece['text'], 'source-' + str(index)) for index, piece in enumerate(source.get('pieces', []), 1)) + '</ul>'
    contenu = ('<section class="carte ge-pile" id="source" aria-labelledby="t-src"><h2 id="t-src">Exemple source</h2>'
               + cles([('Titre', text(source['title'])), ('Travail évalué', text(source['work'])), ('Version source', text(version_lisible(source['version_id']))),
                       ('Qualification', text(statut))])
               + '<p class="petit">L’exemple source est conservé avec sa version et son empreinte. ' + phrase + '</p>'
               + tiroir('Cas, limites et pièces conservées', '<div class="ge-src">' + groupes + '</div>'))
    if notice.get('withdrawn'):
        contenu += notice_ligne('Cette version n’est plus disponible au catalogue. Votre copie et ses pièces restent conservées dans ce cas d’usage.')
    if notice.get('unavailable'):
        contenu += notice_ligne('L’état et les signalements de la source sont momentanément indisponibles.')
    if notice.get('reports'):
        contenu += tiroir('Signalement éditorial', ''.join(
            '<p>' + text(report['summary']) + '</p><p class="petit">Attribué à la version source le ' + text(date_lisible_utc(report['reported_at']))
            + '. Ce signalement ne corrige pas les pièces ni les preuves déjà conservées.</p>' for report in notice['reports']))
    return contenu + '</section>'


# ——— Besoin, exemple, validation, révision, hors champ ———
# Raisons pour lesquelles l'envoi est refusé : écrites au-dessus de la conversation et liées au champ désactivé par `availability`
RAISONS_ENVOI = {
    'access': ('Ajoutez votre clé OpenRouter pour préparer un exemple.', 'alerte'),
    'unconfigured': ('Préparation indisponible : aucun assistant n’est en service pour le moment.', 'alerte'),
    'waiting': ('Votre préparation précédente est en cours. Actualisez la page pour voir où elle en est.', 'horloge'),
    'interrupted': ('Nous ne savons pas si le modèle a répondu. Rien n’est relancé ; cette opération sera close automatiquement.', 'alerte'),
    'restore': ('Service momentanément indisponible, réessayez plus tard.', 'alerte'),
    'unresolved': ('Envois fermés : le résultat ou le coût d’un appel précédent n’est pas encore connu.', 'alerte')}
# Ancien ton du produit : (ton, mot de statut) ; le mot double la couleur
MOTS_TON = {'action': ('att', 'À faire'), 'wait': ('att', 'En cours'), 'warn': ('vig', 'À vérifier'), 'err': ('ko', 'À revoir'),
            'done': ('ok', 'Terminé'), 'unk': ('inc', 'Inconnu')}
CONTROLE = {'QUALIFIED': 'Réussi', 'BLOCKED': 'Bloqué', 'PENDING': 'En attente'}
# Contrôle de l'exemple : état connu de l'exemple avant et après validation
TEXTE_CONTROLE_ATTENTE = ('Après votre validation, l’assistant contrôle que la consigne, les pièces et les critères permettent de juger '
                          'les réponses des modèles. Ce contrôle n’a pas encore eu lieu pour cette version.')
TEXTE_CONTROLE_REUSSI = ('L’assistant a contrôlé cette version : la consigne, les pièces et les critères permettent de juger '
                         'les réponses des modèles.')


def cout_preparation(value):
    """Coût de la préparation en relevé compact : estimé, observé ou corrigé, jamais deviné"""
    lignes, notes = [], ''
    if 'indicative_cost' in value:
        estimate = value['indicative_cost']
        amount = estimate.get('token_subtotal_usd') if estimate else None
        lignes.append(('Coût estimé de la préparation', None if amount is None else montant_lisible(amount), 'USD',
                       'non estimable' if amount is None else 'estimation, pas une facture'))
        notes = '<p class="petit">Calcul de l’estimation : tokens utilisés × tarifs du modèle relevés avant l’appel.</p>'
    if value.get('observed_cost'):
        cost = value['observed_cost']
        inconnu = cost['status'] == 'UNKNOWN'
        lignes.append(('Coût observé de la préparation', None if inconnu else montant_lisible(cost['amount']), '' if inconnu else cost['currency'],
                       'source : ' + str(cost['source'])))
    elif value.get('operations') == 0:
        # Zéro opération : aucune dépense à rapprocher, pas même une dépense inconnue
        lignes.append(('Coût de la préparation', 'aucun', '', 'aucun appel modèle effectué'))
    else:
        lignes.append(('Coût de la préparation', None, '', 'aucun justificatif reçu'))
    if value.get('cost_reconciliation'):
        proof, cost = value['cost_reconciliation'], value['effective_cost']
        lignes.append(('Coût corrigé après vérification', montant_lisible(cost['amount']), cost['currency'],
                       'source : ' + str(proof['source']) + ', confirmée par ' + str(proof['actor']) + ' le ' + str(proof['observed_at'])))
        notes += '<p class="petit">Le justificatif d’origine reste inchangé.</p>'
    return releves(lignes, compact=True) + notes


def formulaire_reponse(csrf, url, revision, *, desactive, aide, decrit='', libelle='Votre réponse à l’assistant', envoi='Envoyer ma réponse',
                       minlength=None):
    """Champ de réponse de la conversation : un champ, un bouton, la raison du refus liée au champ désactivé"""
    attribut = ' disabled aria-describedby="availability"' if desactive else ''
    return form(csrf, url + '/messages', {'action_id': secrets.token_hex(16), 'revision': revision, 'kind': 'clarify'},
                '<label class="cache" for="message">' + text(libelle) + '</label><textarea id="message" name="message" rows="3" required'
                + (' minlength="' + str(minlength) + '"' if minlength else '') + ' maxlength="1000" placeholder="Votre réponse…"' + attribut + '></textarea>'
                + HONEYPOT + '<div class="actions"><span class="petit">' + text(aide) + '</span>'
                + bouton(envoi, sec=desactive).replace('type="submit"', 'type="submit"' + (' disabled' if desactive else '')) + '</div>',
                classe='reponse')


def tiroir_correction(csrf, url, revision, disabled, *, ouvert=False):
    """« Préciser ou corriger cet exemple » : le bloc jaune de la validation envoie ce formulaire quand le tiroir est ouvert"""
    corps = form(csrf, url + '/messages', {'action_id': secrets.token_hex(16), 'revision': revision},
                 '<p class="petit">Ce qui est convenu reste acquis. Si l’exemple change, vous le validerez de nouveau. '
                 'Les versions précédentes restent consultables.</p>'
                 '<div class="champ"><label for="kind">Votre message sert à</label><select id="kind" name="kind"' + disabled + '>'
                 '<option value="clarify">Répondre à l’assistant ou confirmer le travail</option>'
                 '<option value="correct" selected>Modifier cet exemple</option></select></div>'
                 + champ('Votre précision ou correction', 'message', ident='message', maxlength=1000, desactive=bool(disabled), aide='')
                 + HONEYPOT + '<div class="actions envoi">' + bouton('Envoyer ce message', sec=True).replace(
                     'type="submit"', 'type="submit"' + disabled) + '</div>', form_id='form-corriger')
    return tiroir('Préciser ou corriger cet exemple', corps, resume='Versions précédentes conservées', ouvert=ouvert, ident='corriger')


MODE_CORRIGER = ('<div class="mode-corriger"><div class="mc-correct"><h2>Corriger l’exemple</h2>'
                 '<p>L’assistant lit votre message et prépare une nouvelle version. Vous la relirez avant de la valider.</p>'
                 + bouton('Corriger ce travail', xl=True).replace('type="submit"', 'type="submit" form="form-corriger"') + '</div>'
                 '<div class="mc-clarify"><h2>Répondre à l’assistant</h2>'
                 '<p>L’assistant lit votre réponse et reprend la préparation de l’exemple.</p>'
                 + bouton('Envoyer ma réponse', xl=True).replace('type="submit"', 'type="submit" form="form-corriger"') + '</div></div>')


def decision_validation(csrf, url, value, disabled, estimation_html):
    """Bloc jaune de la validation : valider l'exemple, ou (tiroir de correction ouvert) envoyer la correction"""
    return form(csrf, url + '/validation', binding(value['dossier_id'], value['revision'], value['package_sha256']),
                '<span class="etiq">Étape 3 sur 5</span><div class="mode-valider" id="validation"><h2>Valider l’exemple</h2>'
                '<p>Vous confirmez que cet exemple représente bien votre besoin.</p>'
                + bouton('Oui, c’est le travail à tester', xl=True).replace('type="submit"', 'type="submit"' + disabled) + estimation_html + '</div>'
                + MODE_CORRIGER, classe='decision')


def estimation_controle(value):
    """Annonce de dépense avant validation : deux réserves distinctes, jamais additionnées (DESIGN.md)"""
    estimate = value.get('qualification_estimate')
    if not estimate:
        return ''
    return ('<p class="petit">Estimation avant consommation : une réserve est bloquée sur votre crédit avant chaque appel. '
            'Pour le contrôle de l’exemple : au plus ' + text(montant_lisible(estimate['check_usd']))
            + ' USD réservé. Puis, pour le contrôle de chaque témoin, ' + text(str(estimate['witness_limit']))
            + ' au plus : au plus ' + text(montant_lisible(estimate['witness_usd']))
            + ' USD réservé par témoin. Un témoin est une réponse inventée, recevable ou fautive, soumise au '
            'contrôle qui jugera les modèles. Le coût observé est relevé ensuite sur le justificatif de chaque appel.</p>')


def critere_obligation(item):
    """Une obligation, composée ou non : ses éléments sont jugés chacun séparément"""
    if type(item) is str:
        return text(item)
    return text(item['description']) + listing(item['elements'])


def bloc_criteres(value):
    """Critères de réussite : obligations, erreurs éliminatoires et qualité en lignes de genre, puis la règle"""
    criteria = value['criteria']
    lignes = ''.join(critere_ligne(critere_obligation(item), 'Obligation') for item in criteria['obligations'])
    lignes += ''.join(critere_ligne(text(item), 'Erreur éliminatoire', interdit=True) for item in criteria['eliminatory'])
    lignes += ''.join(critere_ligne(text(item['label']), 'Qualité') for item in criteria['quality'])
    return ('<section class="pile" aria-labelledby="t-criteres"><div class="zone-titre"><h2 id="t-criteres">Critères de réussite</h2>'
            '<span class="petit">Chaque obligation compte.</span></div><div class="carte"><p class="etiq">Ce que le modèle doit rendre</p>'
            + ''.join('<p class="texte-l">' + text(item) + '</p>' for item in value['package']['deliverables'])
            + '<ul class="criteres">' + lignes + '</ul><p class="regle-verdict"><span>Règle</span> <span>' + text(value['criteria_rule'])
            + '</span></p></div></section>')


def bloc_dossier(value):
    """Consigne, pièces, critères, limites, changements : l'exemple lisible en entier"""
    package = value['package']
    pieces = ''.join(feuille(piece['name'], '', value['example_contents'][piece['id']], 'piece-' + str(index))
                     for index, piece in enumerate(package['pieces'], 1))
    contenu = consigne(package['instruction'], ident='exemple')
    contenu += ('<section class="pile" aria-labelledby="t-pieces"><div class="zone-titre"><h2 id="t-pieces">Les pièces</h2><span class="petit">'
                + str(len(package['pieces'])) + (' documents inventés' if len(package['pieces']) > 1 else ' document inventé')
                + '</span></div><ul class="pieces">' + pieces + '</ul></section>')
    contenu += bloc_criteres(value)
    limites = '<h3>Ce qui restera à faire par une personne</h3><p>' + text(package['human_work']) + '</p>'
    if package['acceptable_ambiguities']:
        limites += '<h3>Ambiguïtés acceptées</h3>' + listing(package['acceptable_ambiguities'])
    if package['limits']:
        limites += '<h3>Limites de l’exemple</h3>' + listing(package['limits'])
    contenu += tiroir('Limites et travail restant', limites)
    labels = {'instruction': 'Consigne', 'deliverables': 'Livrables', 'criteria': 'Critères',
              'acceptable_ambiguities': 'Ambiguïtés acceptées', 'pieces': 'Pièces'}
    if value['changes']:
        changes = listing(labels.get(change, change) for change in value['changes'])
        for kind, label in (('added', 'Pièces ajoutées'), ('removed', 'Pièces retirées'), ('modified', 'Pièces modifiées')):
            names = value['piece_changes'][kind]
            if names:
                changes += '<h3>' + label + '</h3>' + listing(names)
        contenu += tiroir('Ce qui a changé depuis la version précédente', changes)
    return contenu


def bloc_precisions(value):
    """Besoin, précisions et éléments inventés : tiroir sous l'exemple, jamais perdus"""
    payload = value['payload']
    contenu = ''
    if value.get('message') and 'message' in value['message'] and not value.get('source'):
        contenu += '<h3>Votre message à l’origine de cette version</h3><p>' + text(value['message']['message']) + '</p>'
    if payload['clarifications'] or payload['validated_assumptions']:
        accords = listing(payload['clarifications']) if payload['clarifications'] else ''
        for agreement in payload['validated_assumptions']:
            if type(agreement) is dict and set(agreement) == {'question', 'answer'}:
                accords += ('<blockquote><p>' + text(agreement['question']) + '</p><p><strong>Votre accord : </strong>'
                            + text(agreement['answer']) + '</p></blockquote>')
            else:
                accords += '<p>' + text(encode(agreement) if type(agreement) is dict else agreement) + '</p>'
        contenu += '<h3>Précisions et points convenus</h3>' + accords
    if payload['reformulation']:
        contenu += '<h3>Votre besoin reformulé par l’assistant</h3><p>' + text(payload['reformulation']) + '</p>'
    if payload['fictional_parameters']:
        contenu += '<h3>Éléments inventés pour l’exemple</h3>' + listing(f'{k} : {v}' for k, v in payload['fictional_parameters'].items())
    return tiroir('Votre besoin et vos précisions', '<p>' + text(payload['request']) + '</p>' + contenu, ident='besoin') if payload['request'] or contenu else ''


def controle_exemple(value, qualification, automatic, referral):
    """Tiroir « Contrôle de l'exemple » : ce que le contrôle a relevé, ou pourquoi il n'a pas eu lieu"""
    if not value['package'] or referral:
        return ''
    reussi = qualification.get('qualification_status') == 'QUALIFIED' or qualification.get('status') == 'QUALIFIED'
    if not value['validation'] and not qualification:
        return tiroir('Contrôle de l’exemple', '<p>' + TEXTE_CONTROLE_ATTENTE + '</p>', resume='En attente')
    corps = '<p>' + TEXTE_CONTROLE_REUSSI + '</p>' if reussi else ''
    if automatic:
        resume = 'Impossible pour le moment' if qualification.get('cause') else CONTROLE.get(qualification.get('qualification_status', qualification.get('status')), 'En attente')
        # L'encadré d'état porte déjà la remarque d'un contrôle bloqué ou impossible
        dans_encadre = not reussi and qualification.get('status') == 'BLOCKED' or bool(qualification.get('cause'))
        corps += ('' if dans_encadre else '<p>Remarque du contrôle : ' + text(qualification['summary']) + '</p>')
        corps += (listing(finding['text'] for finding in qualification.get('findings', [])) + render_witnesses(qualification))
    else:
        resume = CONTROLE.get(qualification.get('qualification_status'), 'En attente')
        if qualification.get('findings'):
            corps += '<p>Bloqué. Ce que le contrôle de l’exemple a relevé :</p>' + listing(finding['text'] for finding in qualification['findings'])
    return tiroir('Contrôle de l’exemple', corps, resume=resume)


def ligne_exemples(value, stages, cases):
    """Exemples de ce cas d'usage : l'exemple courant, les autres, et ce qui les distingue"""
    def case_state(case):
        if case['drifted']:
            return 'contrat d’une ancienne version de l’exemple 1'
        if case['validated']:
            return 'exemple contrôlé' if case['qualified'] else 'validé, contrôle en attente ou à revoir'
        return 'à valider' if case['stage'] == 'preview' else stages[case['stage']][2].lower()
    drifted = any(case['drifted'] for case in cases)
    corps = ('<ul class="g-liste">' + ''.join(
        '<li>' + ('Exemple ' + str(case['number']) + ' (cette page)' if case['current'] else
                  '<a href="/preparation/dossiers/' + text(case['dossier_id']) + '">Exemple ' + str(case['number']) + '</a>')
        + ' : ' + text(case_state(case)) + '.' + (' Ce qui le distingue : ' + text(case['difference']) if case['difference'] else '') + '</li>'
        for case in cases) + '</ul>'
        + ('<p class="petit">Le contrat de l’exemple 1 a changé depuis la création de certains exemples : '
           'ces exemples ne suivent plus la même consigne. Ajoutez-les de nouveau depuis l’exemple 1.' if drifted else
           '<p class="petit">Tous les exemples suivent la même consigne, les mêmes livrables et les mêmes critères.')
        + ' Plusieurs exemples ne prouvent pas qu’ils représentent votre travail : chacun reste inventé, validé et '
        'contrôlé séparément.</p>')
    return section('Exemples de ce cas d’usage', corps, 'cas')


def ajouter_exemple(csrf, dossier_id, disabled):
    return tiroir('Ajouter un autre exemple à ce cas d’usage', form(
        csrf, '/preparation/dossiers', {'dossier_id': secrets.token_hex(16), 'action_id': secrets.token_hex(16), 'case_of': dossier_id},
        '<p class="petit" id="case-context-help">Le nouvel exemple reprend la consigne, les livrables, les critères et les '
        'ambiguïtés acceptables de cette version. L’assistant prépare d’autres pièces et un autre résultat de '
        'référence, à valider puis contrôler séparément. Cette préparation est payée avec votre clé OpenRouter. '
        'Stabilisez cet exemple avant d’en ajouter d’autres : une correction ultérieure de sa consigne ne se '
        'reporte pas aux exemples déjà créés.</p>'
        + champ('Ce qui distingue cet exemple, par exemple la difficulté qu’il couvre', 'context', ident='case-context', maxlength=CONTEXT_MAX,
                desactive=bool(disabled)).replace('<textarea ', '<textarea required aria-describedby="case-context-help" ', 1)
        + '<div class="actions">' + bouton('Préparer cet exemple', sec=True).replace('type="submit"', 'type="submit"' + disabled) + '</div>'),
        resume='Facultatif')


def liste_benchmarks(value, url, current_campaigns):
    """Benchmarks lancés avec cet exemple, le plus récent d'abord, et le journal détaillé en tiroir"""
    items = ''
    for number, campaign in reversed(list(enumerate(current_campaigns, 1))):
        ton, heading, _, target, _ = campaign_status(campaign, url)
        items += ('<li><a href="' + text(target) + '">Benchmark ' + str(number) + ' du '
                  + text(date_lisible_utc(campaign['conditions']['frozen_at'])) + '</a>' + mot(heading, MOTS_TON[ton][0]) + '</li>')
    return ('<section id="comparaison" aria-labelledby="t-comps"><div class="zone-titre"><h2 id="t-comps">Benchmarks de cet exemple</h2></div>'
            '<ul class="gb-hist carte">' + items + '</ul></section>')


def hors_champ(value, referral, request, benchmarks, bas):
    """Cas arrêté, hors du champ d'Evaly : les benchmarks publics utiles, en direct, sans rien transmettre du besoin"""
    liste = ''
    for identity, reference in benchmarks:
        url = reference_url(reference)
        verification = mot('Vérifiée', 'ok') if reference_verified(reference) else mot('Vérification insuffisante', 'vig')
        source = ('<a class="btn btn-s" href="' + text(url) + '" rel="noreferrer noopener" target="_blank">Ouvrir ' + text(reference['name']) + ic('lien') + '</a>'
                  if url else '<span class="petit">Source indisponible</span>')
        liste += ('<li class="gb-bench"><div><h3>' + text(reference['name']) + '</h3><p>' + text(reference['work']) + '</p>' + verification
                  + '</div><div class="actions">' + source + lien('/evaluations-externes/' + identity, 'Lire la fiche') + '</div></li>')
    if liste:
        titre = 'Ce travail est déjà mesuré par des benchmarks publics'
        intro = ('Votre demande porte sur une compétence générale, pas sur un travail précis. Des benchmarks publics la mesurent déjà sur des '
                 'milliers d’exercices : leurs classements vous répondront mieux qu’un exemple unique.')
        liste = '<ul class="gb-benchs">' + liste + '</ul>'
    else:
        titre = 'Evaly compare des modèles sur un travail précis'
        intro = ('Votre demande ne décrit pas un travail précis. Aucun benchmark public ne correspond directement. '
                 'Décrivez un cas d’usage réel de votre travail : ce que vous recevez, et ce que vous voulez obtenir.')
    autre = btn('/preparation', 'Décrire un cas d’usage précis', sec=bool(liste), avant='stylo', icone=None)
    return ('<div class="cadre gb-hc"><div class="entretien">' + (bulle_vous(request) if request else '') + '</div>'
            '<section class="gb-hc-rep" aria-labelledby="t-hc">' + mot('Hors champ', 'inc') + '<h2 id="t-hc">' + titre + '</h2><p class="texte-l">' + intro + '</p>'
            + liste + '<div class="actions">' + autre + lien('/evaluations-externes', 'Tous les benchmarks publics') + '</div>'
            '<p class="petit">Ce cas d’usage est arrêté : aucun benchmark ne sera lancé. Votre demande n’est transmise à aucun de ces sites.</p></section>'
            + ('<div class="pile">' + bas + '</div>' if bas else '') + '</div>')


def render_dossier(value, csrf, path, *, pending, can_submit, navigation):
    """Page d'un cas d'usage : besoin, exemple, validation, révision ou hors champ ; renvoie (titre, contenu, en-tête, état)"""
    dossier_id, revision = value['dossier_id'], value['revision']
    url = '/preparation/dossiers/' + dossier_id
    source, source_notice = value.get('source'), value.get('source_notice') or {}
    package, stage = value['package'], value['stage']
    prior_revision = revision != value.get('current_revision', revision)
    snapshot = '/revisions/' in path and any(c['task']['revision'] == revision and c['attempts'] for c in value.get('campaigns', []))
    referral = value.get('checks', {}).get('out_of_scope')
    editable = not prior_revision and not snapshot and stage != 'waiting' and not referral
    disabled = '' if can_submit and editable else ' disabled aria-describedby="availability"'
    current_campaigns = [c for c in value.get('campaigns', []) if c['task']['revision'] == revision]
    launched = any(c['attempts'] for c in current_campaigns)
    qualification = value.get('qualification', {})
    automatic = 'operation_id' in qualification
    explanation = value.get('explanation') or ''
    suite = ('Renvoyez votre message pour relancer la préparation.' if can_submit
             else 'Vous pourrez renvoyer votre message quand l’envoi sera de nouveau possible.')
    stages = {'draft': ('inc', 'Brouillon', 'Rien n’a encore été envoyé à l’assistant', ''),
              'waiting': ('att', 'En cours', 'Préparation en cours',
                          'L’assistant prépare votre exemple, ou une question s’il lui manque une information. Vous pouvez quitter cette page.'),
              'clarification': ('att', 'Question', 'L’assistant a une question',
                                'Répondez : l’exemple sera préparé ensuite. Vous pouvez quitter la page et revenir.'),
              'preview': ('att', 'À relire', 'Votre exemple est prêt', 'Lisez la consigne et les pièces, corrigez si besoin, puis validez.'),
              'scope_confirmation': ('att', 'Question', 'Précisez le travail à comparer',
                                     'Evaly compare des modèles sur un travail concret, avec un résultat attendu et des critères vérifiables. '
                                     'Aucun benchmark ne se lance à cette étape.'),
              # La cause vient de l'explication ; l'envoi reste ouvert sauf motif de disponibilité affiché à part
              'suspended': ('ko', 'Arrêtée', 'Préparation arrêtée', (explanation + ' ' if explanation else '') + suite)}
    ton, mot_, titre, texte = stages[stage]
    if source and stage == 'draft':
        ton, mot_, titre, texte = ('ok', 'Copie enregistrée', 'Votre copie est enregistrée',
                                   'Vous la consultez sans clé ni appel aux modèles. La clé OpenRouter sert à préparer votre exemple.')
    action = ''
    if value['validation']:
        bloque = qualification.get('status') == 'BLOCKED' and not qualification.get('cause')
        if automatic and value.get('qualified') or not automatic and value.get('qualified') and not current_campaigns:
            ton, mot_, titre, texte = ('ok', 'Contrôlé', 'Exemple contrôlé, prêt à comparer',
                                       'Lisez les remarques du contrôle, puis choisissez les modèles à comparer.' if automatic else 'Choisissez les modèles à comparer.')
        elif current_campaigns:
            ton, mot_, titre, texte = ('ok', 'Lancé', 'Benchmark lancé', 'Un benchmark utilise déjà cet exemple. Suivez-le jusqu’aux résultats.')
        elif not automatic:
            ton, mot_, titre, texte = ('inc', 'Non démarré', 'Contrôle de l’exemple non démarré',
                                       'Le contrôle de l’exemple n’a pas pu démarrer. Pour le relancer, corrigez l’exemple, puis validez-le de nouveau.')
        elif qualification.get('status') == 'BLOCKED' and qualification.get('cause'):
            # Incident chez le fournisseur ou clé : l'exemple n'est pas en cause
            ton, mot_, titre, texte = 'inc', 'Impossible', 'Contrôle de l’exemple impossible pour le moment', qualification['summary']
        elif bloque:
            ton, mot_, titre, texte = ('ko', 'À revoir', 'Exemple à revoir avant le benchmark',
                                       'Remarque du contrôle : ' + qualification['summary'].rstrip('.') + '. Corrigez l’exemple, puis validez-le de nouveau.')
        elif qualification.get('cause'):
            ton, mot_, titre, texte = 'att', 'En attente', 'Contrôle de l’exemple en attente', qualification['summary']
        else:
            ton, mot_, titre, texte = ('att', 'En cours', 'Contrôle de l’exemple en cours',
                                       'Votre validation est enregistrée. L’assistant contrôle que l’exemple est cohérent et que ses critères peuvent être contrôlés.')
    # Contrat opérateur bloqué avant tout lancement : l'exemple est à revoir, aucun benchmark ne se lance
    operator_blocked = (value['validation'] and not automatic and qualification.get('qualification_status') == 'BLOCKED' and not launched)
    if operator_blocked:
        ton, mot_, titre, texte = ('ko', 'À revoir', 'Exemple à revoir avant le benchmark',
                                   'Le détail du contrôle, plus bas, dit ce qui bloque. Corrigez l’exemple, puis validez-le de nouveau.')
    corriger = ton in ('ko',) and mot_ == 'À revoir' or mot_ == 'Non démarré'
    if corriger and package:
        action = btn('#corriger', 'Corriger l’exemple', icone='stylo')
    # Un benchmark existe : l'encadré dit son état et mène à lui, jamais à un nouveau choix de modèles
    if value['validation'] and current_campaigns and not snapshot and not prior_revision and not operator_blocked:
        ancien, titre, texte, cible, libelle = campaign_status(current_campaigns[-1], url)
        ton, mot_ = MOTS_TON[ancien]
        action = btn(cible, libelle)
    elif value['validation'] and value.get('qualified') and not snapshot and not prior_revision:
        action = btn(url + '/configurations', 'Choisir les modèles')
    h1 = ('Votre cas d’usage' if value['validation'] else 'Est-ce le travail à tester ?' if package else 'Votre cas d’usage privé' if source
          else 'Précisons votre besoin')
    if launched and not snapshot and package:
        h1 = 'Exemple validé, benchmark lancé'
    notices = ''
    if snapshot:
        h1 = 'Exemple utilisé pour le benchmark'
        ton, mot_, titre, texte = ('ok', 'Déjà testé', 'Exemple déjà testé',
                                   'Vous consultez l’exemple tel qu’il a été testé. Ses résultats restent disponibles.')
        notices += notice_ligne('Lecture seule. Si vous modifiez l’exemple, vos changements formeront une nouvelle version, à valider de nouveau.', 'cadenas')
        resultats = [campaign_status(c, url)[3] for c in current_campaigns if c['attempts']]
        action = btn(url, 'Nouveau benchmark') + (lien(resultats[-1], 'Revenir aux résultats', 'retour') if resultats else '')
    elif prior_revision:
        ton, mot_, titre = 'vig', 'Lecture seule', 'Ancienne version'
        texte = ('Cet exemple a été remplacé par une version plus récente.' if package or stage != 'waiting'
                 else 'La préparation continue dans la version actuelle. Celle-ci ne peut ni être modifiée ni être validée.')
        action = btn(url, 'Ouvrir la version actuelle')
        if not referral:
            notices += notice_ligne('Ancienne version, en lecture seule. Pour modifier ou valider, ouvrez la version actuelle.', 'cadenas')
    if value.get('notice'):
        texte = value['notice'] + ' ' + texte
    # Raison pour laquelle l'envoi est refusé : au-dessus de la conversation, liée au champ désactivé
    etat_service = value.get('availability', {})
    raison = RAISONS_ENVOI.get(etat_service.get('reason'))
    if (etat_service and not pending and not can_submit and editable and raison and not referral and not prior_revision):
        phrase = raison[0]
        if etat_service['reason'] == 'access' and source and package is None:
            phrase = ('Votre copie est enregistrée. Vous pouvez la consulter sans clé ni appel aux modèles ; '
                      'la clé OpenRouter servira à préparer votre exemple.')
        notices = '<p class="etat-ligne gb-notice" id="availability">' + ic(raison[1]) + '<span>' + text(phrase) + '</span></p>' + notices
    request = value['payload']['request']
    privacy_html = render_privacy_controls(value, csrf)
    partage = ''
    bas = ''
    if value.get('task_index'):
        bas += tiroir('Historique des versions et des benchmarks', render_task_index(value['task_index']))
    cases = value.get('cases', [])
    if current_campaigns and package:
        bas += liste_benchmarks(value, url, current_campaigns)
    if value.get('campaigns') and package:
        bas += tiroir('Historique détaillé des benchmarks', render_campaign_records(value['campaigns'], url),
                      resume='Benchmark ' + str(len(current_campaigns)) if current_campaigns else '')
    if cases and package:
        bas += ligne_exemples(value, stages, cases)
    if (package and value['validation'] and (not cases or cases[0]['current']) and not snapshot and not prior_revision and not referral):
        bas += ajouter_exemple(csrf, dossier_id, disabled)
    bas += controle_exemple(value, qualification, automatic, referral) if package else ''
    bas += privacy_html + partage
    # Une fois la vue écrite, le tail de `render` n'ajoute plus les contrôles de confidentialité de cette page
    sortie = dict(bas_place=True)
    ancre = lien(path, 'Actualiser', 'actualiser') if not pending else ''
    precedente = lien(url + '/revisions/' + str(revision - 1), 'Version précédente') if revision > 1 else ''
    liens = '<div class="actions">' + ancre + precedente + '</div>' if ancre or precedente else ''
    if referral:
        benchmarks = [(identity, reference) for identity, reference in BENCHMARK_REFERENCES.items()
                      if reference['category'] == referral and reference.get('availability') == 'available']
        contenu = hors_champ(value, referral, request, benchmarks, bas)
        return 'Hors du champ d’Evaly', contenu, dict(piste_html=navigation, contexte_html=puce('Version ' + str(revision)), cadre_propre=True), sortie
    if pending:
        marque = disque('disque-l tourne')
        # Le script de suivi relit cette adresse et masque la barre `progress` quand il s'arrête
        suivi = ('<div class="actions" id="preparation-progress"><progress class="cache" aria-label="' + text(titre) + '"></progress>'
                 + lien('/preparation/dossiers/' + value['dossier_id'], 'Actualiser', 'actualiser')
                 + '<button class="btn btn-sec" type="button" hidden>' + ic('pause') + 'Arrêter la mise à jour automatique</button>'
                 '<p class="cache" role="status"></p></div>')
        # `availability` nomme la raison du refus d'envoi liée au champ désactivé
        carte = '<div id="availability">' + etat(ton, mot_, titre, texte, marque_html=marque, corps_html=suivi) + '</div>'
    else:
        carte = etat(ton, mot_, titre, texte, action) if titre else ''
    cout = cout_preparation(value)
    nom = request if len(request) <= 60 else request[:59].rstrip() + '…'
    contexte = (puce(nom, plein=True) if request else '') + puce('Version ' + str(revision)) + ruban('Copie d’un exemple publié' if source and package is None else 'Exemple inventé')
    avant = ' cote-avant' if package and value['validation'] or (value['validation'] and not request) else ''
    if package is None:
        # Conversation : le besoin, la question de l'assistant, puis le champ de réponse
        gauche = notices
        if source:
            gauche += render_source_copy(source, source_notice)
        else:
            gauche += (bulle_vous(request) if request else '<p class="petit">Votre besoin personnel n’est pas encore décrit.</p>')
        if stage in ('clarification', 'scope_confirmation') and explanation and not source:
            gauche += bulle_assistant(explanation, 'L’assistant demande')
        elif prior_revision and stage == 'preview':
            gauche += bulle_assistant('Votre exemple est prêt. Lisez la consigne et les pièces, corrigez si besoin, puis validez.')
        elif prior_revision and stage == 'waiting':
            gauche += bulle_assistant('L’assistant prépare votre exemple, ou une question s’il lui manque une information.', 'Exemple attendu')
        terminé = value['validation'] and qualification.get('status') in ('BLOCKED', 'QUALIFIED')
        if editable and not source and not terminé:
            if value['validation']:
                aide = 'L’envoi sera de nouveau possible quand le contrôle de l’exemple se termine.'
            elif not can_submit:
                aide = {'waiting': 'L’envoi sera de nouveau possible quand la préparation précédente se termine.',
                        'access': 'L’envoi sera possible une fois votre clé OpenRouter ajoutée.'}.get(
                            etat_service.get('reason'), 'L’envoi sera de nouveau possible quand le service répond.')
            else:
                aide = 'Votre message relance la préparation.' if stage == 'suspended' else 'L’exemple est préparé dès votre réponse.'
            gauche += formulaire_reponse(csrf, url, revision, desactive=not can_submit or bool(value['validation']), aide=aide)
        if source and editable:
            decision = form(csrf, url + '/messages', {'action_id': secrets.token_hex(16), 'revision': revision, 'kind': 'clarify'},
                            '<span class="etiq">Votre besoin</span><h2>Décrivez votre besoin</h2><p>Votre besoin personnel n’est pas encore décrit. '
                            'Dites ce que vous voulez comparer et ce qui diffère de l’exemple source. La notice et les pièces conservées ne changent pas.</p>'
                            + champ('Votre besoin personnel', 'message', ident='message', maxlength=1000, desactive=not can_submit or not editable,
                                    aide='')
                            .replace('<textarea ', '<textarea required minlength="' + str(REQUEST_MIN) + '" ', 1)
                            + HONEYPOT + bouton('Préparer mon exemple', xl=True).replace('type="submit"', 'type="submit"' + ('' if can_submit else ' disabled'))
                            + '<p class="effet">' + ic('cle') + '<span>La préparation est payée avec votre clé OpenRouter. '
                            + ('La' if can_submit else '<a href="/preparation/access">Ajoutez votre clé</a> : la') + ' copie reste consultable sans elle.</span></p>',
                            classe='decision')
            droite = decision + cout
            haut = '<div class="ge-pile">' + carte + '</div>'
        else:
            droite = carte + cout + liens
            haut = ''
        corps = (haut + '<div class="colonnes"><div class="entretien">' + gauche + '</div><aside class="cote gb-cote' + avant + '" aria-label="Où en est la préparation">'
                 + droite + '</aside>' + ('<div class="pile pile-bas">' + bas + '</div>' if bas else '') + '</div>')
        entete = dict(piste_html=navigation, contexte_html=contexte)
        if stage == 'waiting' or pending:
            corps += '<script>' + PREPARATION_PROGRESS_SCRIPT + '</script>' if pending else ''
        return h1, corps, entete, sortie
    # Exemple : consigne, pièces, critères et correction à gauche, décision ou état à droite
    gauche = notices + (bulle_assistant(explanation) if explanation and not value['validation'] else '') + bloc_dossier(value)
    avant_validation = editable and stage == 'preview' and value['validation'] is None
    if editable:
        gauche += tiroir_correction(csrf, url, revision, disabled, ouvert=corriger)
    gauche += bloc_precisions(value) if True else ''
    if source:
        gauche = render_source_copy(source, source_notice) + gauche
    haut = ''
    if avant_validation:
        decision = decision_validation(csrf, url, value, disabled, estimation_controle(value))
        # Une version déjà remplacée ou un accès fermé s'annonce au-dessus des colonnes
        if revision > 1:
            haut = '<div class="gb-cote">' + etat('att', 'À valider', 'Version ' + str(revision) + ' prête à valider',
                                                'Votre choix de partage vaut pour cette version. Relisez l’exemple, puis validez-le.') + '</div>'
        droite = decision + cout + liens
        avant = ''
    elif prior_revision and not value['validation'] and stage == 'preview':
        droite = ('<div class="carte gb-ancienne"><span class="etiq">Étape 3 sur 5</span><h2>Valider l’exemple</h2>'
                  '<p>Cet exemple n’est pas encore validé. Ouvrez la version actuelle pour le valider.</p></div>' + cout + liens)
        haut = '<div class="gb-cote">' + carte + '</div>'
    else:
        droite = carte + cout + liens
        avant = ' cote-avant'
    if value.get('privacy') and not active_session(value['privacy']) and package:
        haut = '<div class="gb-cote">' + etat('vig', 'Accès fermé', 'Votre accès à ce cas est fermé',
                                            'Si vous en avez gardé une copie dans ce navigateur, vous pouvez encore la lire dans Mes données.',
                                            lien('/preparation/data', 'Mes données')) + '</div>' + haut
    corps = (haut + '<div class="colonnes"><div class="pile-l">' + gauche + '</div><aside class="cote gb-cote' + avant + '"'
             # La décision porte déjà l'ancre `validation` de la piste ; sinon la colonne d'état la porte
             + ('' if avant_validation else ' id="validation"') + ' aria-label="'
             + ('Décision' if avant_validation else 'État de l’exemple') + '">' + droite + '</aside>'
             + ('<div class="pile pile-bas">' + bas + '</div>' if bas else '') + '</div>')
    if pending:
        corps += '<script>' + PREPARATION_PROGRESS_SCRIPT + '</script>'
    contexte += ''.join(puce('Exemple ' + str(case['number'])) for case in cases if case['current']) if snapshot and cases else ''
    return h1, corps, dict(piste_html=navigation, contexte_html=contexte), sortie



def render(value, csrf, path='/preparation', *, error=False, status=None):
    """Native HTML forms, inert evidence and a fixed comparison focus script"""
    def field_attributes(name):
        return f' aria-describedby="{text(name)}-error"' if value.get('error_field') == name else ''

    def field_error(name):
        if value.get('error_field') != name:
            return ''
        return erreur_champ(name, value['error'])

    state = value.get('availability', {})
    bas_place = False
    pending = not error and preparation_pending(value)
    can_submit = state.get('can_submit', False)
    s9 = value.get('kind') != 'projection_preview'
    navigation = '' if error else preparation_steps(value)
    title = 'Décrire mon cas d’usage'
    # `contexte` : puces sous le titre h1 ; `plein` : la vue écrit son propre h1 et ses propres bandes pleine largeur
    contexte, plein = '', False
    # En-tête propre à la vue (fil de retour, puces, sous-titre) ; `cadre_propre` : la vue écrit déjà ses colonnes centrées
    entete = {}
    # `aria-current` seulement sur l'entrée qui est la page affichée, jamais sur une page descendante ni d'erreur
    current = None if error else {'home': '/', 'privacy_data': '/preparation/data', 'contributions': '/preparation/data',
                                 'references': None,
                                 'references_help': None}.get(
        value.get('kind'), '/preparation' if 'dossiers' in value and path == '/preparation' else None)
    menu = ''.join('<li><a href="' + href + '"' + (' aria-current="page"' if href == current else '') + '>' + label + '</a></li>'
                   for href, label in MENU)
    if error:
        # Le serveur seul connaît le statut HTTP : la page d'erreur le montre
        decrit = dict(value, http_status=status) if status else value
        title = value.get('title') or ('Service momentanément indisponible' if value.get('unavailable')
                                       else 'Page introuvable' if status == 404 else 'Votre action n’a pas abouti')
        submitted = value.get('form')
        attached = (value.get('error_field') if type(submitted) is dict
                    and value.get('error_field') in submitted else None)
        content = ''
        if value.get('step') == 'example_qualified' and value.get('findings'):
            content += '<p>Ce que le contrôle de l’exemple a relevé</p>' + listing(
                finding['text'] for finding in value['findings'])
        principal, autres = actions_erreur(decrit)
        message, phrase, corrigeable = value['error'], None, False
        if type(submitted) is dict and 'request' in submitted:
            corrigeable = True
            phrase = 'Un champ n’est pas valide. Votre saisie est conservée : corrigez-la, puis renvoyez.' if attached else None
            content += '<div class="g-err-compo">' + composeur(csrf, path, {key: submitted[key] for key in ('dossier_id', 'action_id')},
                request=submitted['request'], useful=submitted.get('useful', ''), context=submitted.get('context', ''),
                erreur=(value['error_field'], value['error']) if value.get('error_field') in ('request', 'useful', 'context') else None,
                envoi='Corriger et renvoyer', titre_niveau='h2') + '</div>'
        elif type(submitted) is dict and 'message' in submitted:
            corrigeable = True
            phrase = 'Votre message n’a pas été envoyé. Votre saisie est conservée : corrigez-la, puis renvoyez.' if attached else None
            content += form(csrf, path, {key: str(submitted.get(key, '')) for key in ('action_id', 'revision')}
                            | {'kind': str(submitted.get('kind', 'clarify'))},
                '<div class="champ"><label for="message">Votre précision ou correction</label>'
                '<textarea id="message" name="message" required maxlength="1000" rows="4"' + field_attributes('message')
                + (' aria-invalid="true"' if attached else '') + '>' + text(submitted['message']) + '</textarea>'
                + field_error('message') + '</div>' + HONEYPOT + '<div class="actions">' + bouton('Corriger et renvoyer')
                + lien('/preparation', 'Revenir à mes cas d’usage') + '</div>', classe='g-err-form')
        latest = value.get('step') if value.get('error_code') == 'SELECTION_SUPERSEDED' else None
        if latest and '/campaigns/' in path and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', str(latest)):
            autres += (lien(path.split('/campaigns/', 1)[0] + '/campaigns/' + latest + '/conditions', 'Voir la dernière sélection'),)
        if value.get('error_code') == 'OUTPUT_LIMIT_ABOVE_BOUND' and path.endswith('/configurations'):
            principal, autres = btn(path, 'Revenir au choix des modèles'), (lien('/preparation', 'Revenir à mes cas d’usage'),) + autres
        # Attente expirée d'un lancement : l'état enregistré se lit sur son suivi, sans rien renvoyer
        followup = (re.fullmatch(r'(/preparation/dossiers/[A-Za-z0-9_-]{1,128}/campaigns/[A-Za-z0-9_-]{1,128})/(?:start|evaluate)', path)
                    if value.get('error_code') == 'RESPONSE_TIMEOUT' else None)
        if followup:
            principal, autres = btn(followup[1] + '/conditions', 'Consulter l’état du lancement'), (lien('/preparation', 'Revenir à mes cas d’usage'),) + autres
        # Un formulaire corrigeable est la suite de la page : le retour aux cas passe en lien discret
        if corrigeable:
            principal, autres = '', ()
            if type(submitted) is dict and 'request' in submitted:
                content += '<p>' + lien('/preparation', 'Revenir à mes cas d’usage') + '</p>'
        content = erreur_grande(decrit, phrase or message, principal, autres, mot_texte='Texte à corriger' if phrase else None,
                                alerte=not attached) + content
    elif value.get('kind') == 'case_delete':
        title, contexte, content = render_delete_confirmation(value, csrf)
    elif value.get('kind') == 'contributions':
        title, content = render_contributions(value)
    elif value.get('kind') == 'session_bootstrap':
        title, content = render_bootstrap(value)
    elif value.get('kind') == 'legal':
        title, _, content = LEGAL_PAGES[value['path']]
        contexte, content = legal_layout(content)
    elif value.get('kind') == 'references':
        title, content, entete = render_references(value)
    elif value.get('kind') == 'references_help':
        title, content, entete = render_aide(value)
    elif value.get('kind') == 'privacy_data':
        title, content = render_privacy_page(value, csrf)
    elif value.get('kind') == 'access':
        title = 'Ma clé OpenRouter'
        content = render_access(value, csrf)
    elif value.get('kind') == 'configurations':
        title, contexte = titre_vue(value), contexte_vue(value)
        content = render_configurations(value, csrf)
    elif value.get('kind') == 'campaign_models':
        title, contexte = titre_vue(value), contexte_vue(value)
        content = render_campaign_models(value)
    elif value.get('kind') == 'attempt_detail':
        title = 'Détail et preuves'
        contexte = contexte_resultats(value, retour=(value['back_href'], 'Revenir aux résultats', 'retour'))
        content = render_attempt_detail(value)
    elif value.get('kind') == 'campaign_launch' and 'checks' in value:
        title, contexte = titre_vue(value), contexte_vue(value)
        content = render_campaign_launch_requester(value, csrf)
    elif value.get('kind') == 'campaign_launch':
        title, contexte = titre_vue(value), contexte_vue(value)
        content = render_campaign_launch_operator(value, csrf)
    elif value.get('kind') == 'home':
        title = 'Quel modèle d’IA pour votre travail ?'
        content, plein = render_home(), True
    elif value.get('kind') == 'catalogue':
        title, content, entete = render_catalogue(value)
    elif value.get('kind') == 'comparison':
        title = 'Résultats'
        contexte = contexte_resultats(value, retour=(value['dossier_href'], 'Revenir au cas d’usage', 'retour'))
        content = render_comparison(value) + '<script>' + COMPARISON_FOCUS_SCRIPT + '</script>'
    elif value.get('kind') == 'projection_preview':
        title = 'Aperçu d’une publication'
        contexte = contexte_apercu(value)
        content = render_preview(value, projection_body(value['comparison'], value['selected_links'], level=2))
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
        content, plein = render_cases(value, csrf), True
    elif value.get('kind') == 'honeypot_ack' or 'operation_id' in value:
        title, content, entete = render_attente(value)
    else:
        title, content, entete, sortie = render_dossier(value, csrf, path, pending=pending, can_submit=can_submit, navigation=navigation)
        bas_place = sortie['bas_place']
    # Le reste s'ajoute après la vue : scripts, historique local, contrôles de confidentialité
    principal, content = content, ''
    script = page_script(value) if not error else None
    if script is not None and value.get('kind') == 'campaign_launch' and value['campaign']['attempts']:
        content += '<script>' + script + '</script>'
    if script in (STEP_SCRIPT, FILTRES_SCRIPT):
        content += '<script>' + script + '</script>'
    if not error:
        if value.get('kind') == 'home':
            content += '<span data-privacy-home hidden></span>'
        if not bas_place:
            content += render_privacy_controls(value, csrf, path)
        if value.get('privacy') or value.get('kind') in ('home', 'privacy_data', 'contributions', 'session_bootstrap'):
            content += PRIVACY_SCRIPT
    # Une vue `plein` écrit son h1 et ses bandes ; les autres reçoivent l'en-tête commun et la colonne centrée
    if plein:
        principal += cadre(content) if content else ''
        tete_html = ''
    else:
        principal = principal + content if entete.pop('cadre_propre', False) else cadre(principal + content)
        tete_html = tete(title, **{'piste_html': navigation, 'contexte_html': contexte, **entete})
    template = TEMPLATE or TEMPLATE_PATH.read_text()
    body_class = ('s9 comparison' if value.get('kind') == 'comparison' else 's9' if s9 else '')
    if not error and value.get('kind') in ('references', 'references_help'):
        body_class = 'g-cat'
    if value.get('kind') == 'legal':
        body_class = 'g-cat g-page-legale'
    # Offre de source AGPL §13 : un numéro et un lien vers l'arbre du commit seulement sous identité de release,
    # construite depuis ce commit ; un checkout peut être modifié ou non poussé, il renvoie au dépôt
    identity = '<span class="identite">' + text(('Version : v' + RELEASE_VERSION + ' (' + SOURCE_SHA[:7] + ')') if SOURCE_SHA and RELEASE_VERSION
                                                else 'Révision : ' + SOURCE_SHA[:7] if SOURCE_SHA else 'Version : inconnue') + '</span>'
    identity += ('<a class="source" href="' + text(REPOSITORY_URL + ('/tree/' + SOURCE_SHA if SOURCE_SHA and RELEASE_VERSION else ''))
                 + '">' + icon('i-github') + 'Code source</a>')
    page = None if error else {'home': '/', 'legal': value.get('path')}.get(value.get('kind'))
    head = ''
    if page in PUBLIC_PAGES:
        head = '<meta name="description" content="' + text(PUBLIC_PAGES[page]) + '">'
        if PUBLIC_URL:
            head += '<link rel="canonical" href="' + text(PUBLIC_URL + page) + '">'
    # Une seule passe : un marqueur écrit dans le contenu ou le titre n'est jamais substitué à son tour
    parts = {'title': text(title), 'body_class': body_class, 'menu': menu, 'identity': identity, 'head': head,
             'tete': tete_html, 'content': principal}
    return re.sub(r'\{\{(\w+)\}\}', lambda marker: parts[marker[1]], template).encode('utf-8')
