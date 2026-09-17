"""Rendu HTML du parcours privé : formulaires natifs, preuves inertes, gabarit unique.

Ce module ne touche ni au stockage, ni aux secrets, ni aux fournisseurs : il met en
forme les vues structurées renvoyées par l'exécuteur.
"""
from pathlib import Path
import secrets

from benchmark import VERSION
from benchmark.preparation import binding
from benchmark.storage import _strict_json as encode

from .campaign_views import (COMPARISON_FOCUS_SCRIPT, render_attempt_detail, render_campaign_history,
                             render_campaign_launch_operator, render_campaign_launch_requester,
                             render_comparison, render_configurations)
from .fragments import date_lisible_utc, form, icon, listing, section, state_block, text
from .projection import projection_body

TEMPLATE_PATH = Path(__file__).with_name('templates') / 'preparation.html'
STYLESHEET_PATH = Path(__file__).with_name('static') / 'preparation.css'
FONTS_PATH = Path(__file__).with_name('static') / 'fonts'
SOURCE_SHA = ''
PREPARATION_PROGRESS_SCRIPT = """(() => {
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
  }
  async function refresh() {
    if (stopped) return;
    if (!document.hidden) {
      request = new AbortController();
      const timeout = setTimeout(() => request.abort(), 10000);
      try {
        const response = await fetch(link.href, {headers: {Accept: 'text/html'},
          cache: 'no-store', redirect: 'error', signal: request.signal});
        if (!response.ok) throw new Error('unavailable');
        const next = new DOMParser().parseFromString(await response.text(), 'text/html');
        if (!stopped && !next.getElementById('preparation-progress')) {
          location.replace(link.href);
          return;
        }
      } catch {
        if (!stopped) stop('Suivi automatique interrompu. Actualisez pour vérifier l’état du dossier.');
      } finally {
        clearTimeout(timeout);
      }
    }
    if (!stopped) timer = setTimeout(refresh, 4000);
  }
  pause.hidden = false;
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


def page_script(value):
    if preparation_pending(value):
        return PREPARATION_PROGRESS_SCRIPT
    return COMPARISON_FOCUS_SCRIPT if value.get('kind') == 'comparison' else None


BENCHMARK_REFERENCES = {
    'math': (('MathArena', 'https://matharena.ai/', 'Raisonnement mathématique et problèmes de compétition'),),
    'coding': (('LiveCodeBench', 'https://livecodebench.github.io/', 'Exercices de programmation'),
               ('SWE-bench', 'https://www.swebench.com/', 'Résolution de problèmes logiciels dans des dépôts de code')),
}


def render_task_index(task):
    content = '<p><a href="' + text(task['href']) + '">' + text(task['need']) + '</a></p>'
    content += '<p>Cas d’usage ' + text(task['dossier_id']) + '. Consultation privée, sans admission au catalogue public.</p>'
    content += '<p>Révisions : ' + ' · '.join(
        '<a href="' + text(task['href']) + '/revisions/' + str(revision) + '">' + str(revision) + '</a>'
        for revision in task['revisions']) + '.</p>'
    for version in task['versions']:
        content += '<section id="version-' + text(version['version']) + '"><h3>Version d’épreuve '
        content += text(version['version']) + '</h3>'
        content += '<p><a href="' + text(task['href']) + '/revisions/' + str(version['revision']) + '">Ouvrir la révision associée</a></p><ul>'
        for campaign in version['campaigns']:
            content += '<li><a href="' + text(campaign['href']) + '">Comparer la campagne ' + text(campaign['campaign_id']) + '</a></li>'
        content += '</ul>' if version['campaigns'] else '</ul><p>Aucune campagne pour cette version.</p>'
        content += '</section>'
    if not task['versions']:
        content += '<p>Aucune version d’épreuve contractuelle conservée.</p>'
    return content


def personal_key_form(csrf, access):
    connected = access.get('connected', False)
    content = '<details class="corr personal-key"><summary class="button sec">Ajouter ma clé Openrouter</summary><div>'
    if connected:
        content += '<p>Plafond Openrouter : ' + text(access.get('limit_usd') or 'inconnu') + ' USD. Solde annoncé : ' + text(access.get('limit_remaining_usd') or 'inconnu') + ' USD.</p>'
    content += form(csrf, '/preparation/access/key', {'assistance_cap': '20'},
        '<label for="openrouter-key">Clé API Openrouter</label>'
        '<input id="openrouter-key" name="key" type="password" autocomplete="new-password" required maxlength="512" aria-describedby="key-help key-storage">'
        '<p id="key-help">Utilisez une clé dédiée avec un plafond non renouvelable de 50 USD maximum.</p>'
        '<p id="key-storage" class="hint">Votre clé est conservée chiffrée sur notre serveur. '
        'Un cookie de session mémorise votre accès dans ce navigateur pour vos prochaines visites. '
        'Ce cookie sera automatiquement supprimé au bout de 30 jours maximum d’inactivité. '
        'Néanmoins, vous pouvez retirer votre clé depuis cette page si vous préférez.</p>'
        '<button type="submit">Enregistrer la clé</button>')
    if access.get('status') in ('connected', 'invalid'):
        content += form(csrf, '/preparation/access/disconnect', {},
            '<button type="submit" class="sec">Retirer la clé de ce navigateur</button>')
        content += '<p>Terminez la préparation ou qualification en cours avant de changer la clé. Le retrait bloque les nouveaux appels, sans révoquer la clé chez Openrouter ni annuler une comparaison engagée.</p>'
    return content + '</div></details>'


def render(value, csrf, path='/preparation', *, error=False):
    """Native HTML forms, inert evidence and a fixed comparison focus script"""
    def field_attributes(name):
        return f' aria-describedby="{text(name)}-error"' if value.get('error_field') == name else ''

    def field_error(name):
        if value.get('error_field') != name:
            return ''
        return f'<p id="{text(name)}-error" role="alert">{text(value["error"])}</p>'

    state = value.get('availability', {})
    pending = not error and preparation_pending(value)
    can_submit = state.get('can_submit', False)
    disabled = '' if can_submit else ' disabled aria-describedby="availability"'
    s9 = value.get('kind') != 'projection_preview'
    navigation = ''
    title = 'Décrire mon cas d’usage'
    current = {'home': '/', 'publication_unavailable': '/index.html'}.get(value.get('kind'), '/preparation')
    menu = ''.join('<a href="' + href + '"' + (' aria-current="page"' if href == current else '') + '>' + label + '</a>'
                   for href, label in (('/', 'Accueil'), ('/preparation', 'Mes cas d’usage'), ('/index.html', 'Comparaisons publiées')))
    if error:
        title = 'Préparation indisponible' if value.get('unavailable') else 'Action non aboutie'
        submitted = value.get('form')
        attached = (value.get('error_field') if type(submitted) is dict
                    and value.get('error_field') in submitted else None)
        content = '' if attached else '<p role="alert">' + text(value['error']) + '</p>'
        if type(submitted) is dict and 'request' in submitted:
            content += form(csrf, path, {key: submitted[key] for key in ('dossier_id', 'action_id')},
                '<label for="request">Une tâche de votre travail</label>'
                '<textarea id="request" name="request" required minlength="40" maxlength="1500" rows="5"' + field_attributes('request') + '>' + text(submitted['request']) + '</textarea>' + field_error('request') +
                '<label for="useful">Résultat attendu</label><textarea id="useful" name="useful" maxlength="800" rows="3"' + field_attributes('useful') + '>' + text(submitted.get('useful', '')) + '</textarea>' + field_error('useful') +
                '<label for="context">Contexte utile</label><textarea id="context" name="context" maxlength="200" rows="2"' + field_attributes('context') + '>' + text(submitted.get('context', '')) + '</textarea>' + field_error('context') +
                '<div class="website"><label for="website">Site web</label><input id="website" name="website" autocomplete="off" tabindex="-1"></div>'
                '<button type="submit">Corriger et renvoyer</button>')
        elif type(submitted) is dict and 'message' in submitted:
            content += form(csrf, path, {key: submitted[key] for key in ('action_id', 'revision', 'kind')},
                '<label for="message">Votre précision ou correction</label>'
                '<textarea id="message" name="message" required maxlength="1000" rows="4"' + field_attributes('message') + '>' + text(submitted['message']) + '</textarea>' + field_error('message') +
                '<div class="website"><label for="website">Site web</label><input id="website" name="website" autocomplete="off" tabindex="-1"></div>'
                '<button type="submit">Corriger et renvoyer</button>')
        back_class = 'button sec' if type(submitted) is dict and ('request' in submitted or 'message' in submitted) else 'button'
        content += '<p><a class="' + back_class + '" href="/preparation">Retrouver mes cas d’usage</a></p>'
    elif value.get('kind') == 'access':
        title = 'Accès Openrouter'
        status = value['status']
        if status == 'connected':
            content = state_block('done', 'Accès Openrouter', 'Compte connecté',
                '<p>Crédit restant : ' + text(value['limit_remaining_usd'] if value['limit_remaining_usd'] is not None else 'INCONNU')
                + ' USD. Limite du compte : ' + text(value['limit_usd'] if value['limit_usd'] is not None else 'INCONNU') + ' USD.</p>')
            content += form(csrf, '/preparation/access/disconnect', {}, '<button class="sec" type="submit">Déconnecter</button>')
        elif status == 'invalid':
            content = state_block('err', 'Accès Openrouter', 'Accès invalide',
                                  '<p>Motif : ' + text(value.get('reason') or 'INCONNU') + '.</p>')
            content += form(csrf, '/preparation/access/start', {'return': path},
                            '<button type="submit">Reconnecter mon compte Openrouter</button>')
        elif status == 'disconnected':
            content = state_block('action', 'Accès Openrouter', 'Compte non connecté',
                                  '<p>Connectez votre compte pour financer les appels candidats de votre comparaison.</p>')
            content += form(csrf, '/preparation/access/start', {'return': path},
                            '<button type="submit">Connecter mon compte Openrouter</button>')
        else:
            content = state_block('err', 'Accès Openrouter', 'Connexion indisponible',
                                  '<p>Connexion Openrouter indisponible.</p>')
        content += '<p><a class="button' + ('' if status in ('connected', 'unavailable') else ' sec') + '" href="/preparation">Revenir à mes cas d’usage</a></p>'
    elif value.get('kind') == 'configurations':
        title = 'Choisir les configurations'
        content = render_configurations(value, csrf)
    elif value.get('kind') == 'campaign_launch' and 'checks' in value:
        title = 'Vérifier puis lancer la comparaison'
        content = render_campaign_launch_requester(value, csrf)
    elif value.get('kind') == 'campaign_launch':
        title = 'Examiner puis lancer la comparaison'
        content = render_campaign_launch_operator(value, csrf)
    elif value.get('kind') == 'home':
        title = 'Quel modèle pour votre travail ?'
        content = '<div class="hero"><p class="lead" role="status">Décrivez une tâche de votre travail, sans donnée personnelle ni information confidentielle. '
        content += 'Nous préparons avec vous un exemple entièrement inventé, puis les modèles sont comparés dans les mêmes conditions, '
        content += 'sur des critères vérifiables et leur coût observé.</p>'
        content += '<div class="actions"><a class="button" href="/preparation">' + icon('i-pen') + 'Décrire mon cas d’usage</a>'
        content += '<a class="button sec" href="/preparation">Retrouver mes cas d’usage</a></div></div>'
        content += section('Le parcours en quatre étapes', '<div class="tiles">'
            '<div class="tile"><h3>Besoin</h3><p>Vous décrivez la tâche et le résultat utile. L’assistant pose des questions si nécessaire.</p></div>'
            '<div class="tile"><h3>Exemple</h3><p>Une consigne et des pièces inventées vous sont proposées. Vous corrigez jusqu’à ce que l’exemple soit fidèle.</p></div>'
            '<div class="tile"><h3>Validation</h3><p>Vous confirmez le travail à tester. La qualification de l’exemple suit ; aucun candidat n’est lancé et rien n’est publié.</p></div>'
            '<div class="tile"><h3>Comparaison</h3><p>Chaque modèle passe l’épreuve dans les mêmes conditions. Vous lisez les verdicts, les preuves et les coûts.</p></div></div>')
        content += section('Ce qui rend le résultat lisible', '<div class="rule">' + icon('i-scale') + '<span><strong>Le verdict ne fait pas de moyenne.</strong> '
            'Une obligation non prouvée ou une erreur éliminatoire suffit à écarter une configuration, quel que soit le reste.</span></div>'
            '<ul><li>Le verdict porte sur la configuration observée sous des conditions communes, jamais sur le nom du modèle seul.</li>'
            '<li>Le coût est observé sur reçu, pas estimé. Un coût inconnu reste inconnu.</li>'
            '<li>Les pièces sont entièrement inventées : aucun dossier réel, même anonymisé.</li></ul>')
        content += section('Comparaisons publiées', '<p>Seules les restitutions approuvées sont accessibles publiquement. '
            'La validation d’un cas d’usage ne publie rien et ne lance aucun candidat.</p>'
            '<p><a href="/index.html">Ouvrir la comparaison publiée, si disponible</a></p>')
    elif value.get('kind') == 'publication_unavailable':
        title = 'Aucune publication vérifiée disponible'
        content = '<p class="lead">Aucun résultat public vérifié n’est disponible à cette adresse pour le moment.</p>'
        content += '<p>Vos cas d’usage et leurs résultats restent privés. Leur consultation ne publie aucune pièce.</p>'
        content += '<div class="actions"><a class="button" href="/">Revenir à l’accueil</a>'
        content += '<a class="button sec" href="/preparation">Retrouver mes cas d’usage</a></div>'
    elif value.get('kind') == 'catalogue':
        title = 'Versions et comparaisons'
        content = '<p class="lead">Index privé de cette session : chaque cas d’usage validé, ses versions d’épreuve et les comparaisons lancées.</p>'
        content += ''.join('<section><h2>' + text(task['need']) + '</h2>' + render_task_index(task) + '</section>' for task in value['tasks'])
        if not value['tasks']:
            content += '<p>Aucun cas d’usage validé dans cette session.</p>'
    elif value.get('kind') == 'comparison':
        title = value['need']
        content = render_comparison(value) + '<script>' + COMPARISON_FOCUS_SCRIPT + '</script>'
    elif value.get('kind') == 'projection_preview':
        title = 'Aperçu privé · NON APPROUVÉ'
        content = '<p role="status">Aperçu privé · NON APPROUVÉ. Aucune activation ni publication.</p>'
        content += '<p><a href="' + text(value['comparison']['href']) + '">Revenir à la comparaison</a></p>'
        content += '<p>Choisissez les pièces à inclure. Aucune pièce cochée : page et styles seulement. '
        content += 'L’aperçu porte sur la campagne entière, sans les filtres de consultation.</p>'
        content += '<form method="get" action="' + text(value['comparison']['href'] + '/preview') + '">'
        content += '<fieldset><legend>Pièces proposées pour la projection</legend>'
        for piece in value['pieces']:
            pid = piece['piece_id']
            content += '<label><input type="checkbox" name="piece" value="' + text(pid) + '"'
            content += (' checked' if pid in value['selected_links'] else '') + '> ' + text(piece['name']) + ' · ' + text(pid) + '</label>'
        content += '</fieldset><button type="submit">Actualiser l’aperçu</button></form>'
        content += '<p>Cette vue privée reprend le contenu de la projection avec des liens privés vers les seules pièces '
        content += 'sélectionnées. Son habillage n’est pas un fichier approuvé. Le reçu fictif devra porter sur les octets du paquet.</p>'
        content += '<hr>' + projection_body(value['comparison'], value['selected_links'])
    elif value.get('kind') == 'attempt_detail':
        title = 'Détail et preuves du résultat'
        content = render_attempt_detail(value)
    elif 'dossiers' in value:
        title = 'Mes cas d’usage'
        content = personal_key_form(csrf, value.get('personal_access', {})) if value.get('personal_preparation') and path == '/preparation' else ''
        content += '<p class="lead" role="status">Décrivez le travail et le résultat qui vous serait utile. Vous pourrez examiner et corriger l’exemple avant de le valider.</p>'
        dossiers = '<ul class="dossiers">' + ''.join(
            f'<li><a href="/preparation/dossiers/{text(d["dossier_id"])}">{text(d.get("need") or "Cas d’usage " + d["dossier_id"])}</a>'
            f'<small>Révision {d["revision"]}</small><a class="button sec" href="/preparation/dossiers/{text(d["dossier_id"])}">Reprendre</a></li>'
            for d in value['dossiers']) + '</ul><p><a href="/preparation/catalogue">Versions d’épreuve et comparaisons de cette session</a></p>' if value['dossiers'] else (
                '<p>Aucun cas d’usage dans ce navigateur. Commencez par décrire un besoin lorsque les appels sont ouverts.</p>'
                '<p>Si vous en aviez déjà un, vérifiez que vous utilisez le même navigateur et son cookie de session.</p>')
        content += section('Décrire un nouveau cas d’usage', form(csrf, '/preparation/dossiers',
            {'dossier_id': secrets.token_hex(16), 'action_id': secrets.token_hex(16)},
            '<label for="request">Une tâche de votre travail</label><p id="request-help" class="hint">Décrivez le travail et le résultat utile, sans donnée personnelle ni information confidentielle. Aucun dossier réel, même anonymisé.</p>'
            '<textarea id="request" name="request" required minlength="40" maxlength="1500" rows="5" aria-describedby="request-help' + ('"' if can_submit else ' availability" disabled') + '></textarea>'
            '<label for="useful">Résultat attendu</label><textarea id="useful" name="useful" maxlength="800" rows="3"' + disabled + '></textarea>'
            '<label for="context">Contexte utile</label><textarea id="context" name="context" maxlength="200" rows="2"' + disabled + '></textarea>'
            '<div class="website"><label for="website">Site web</label><input id="website" name="website" autocomplete="off" tabindex="-1"></div>',
            form_id='prepare-case')
            + '<button type="submit" form="prepare-case"' + disabled + '>' + icon('i-pen') + 'Préparer cet exemple</button>', 'besoin')
        content += section('Mes cas d’usage dans ce navigateur', dossiers)
    elif value.get('kind') == 'honeypot_ack' or 'operation_id' in value:
        title = 'Demande enregistrée'
        url = ('/preparation' if value.get('kind') == 'honeypot_ack'
               else '/preparation/dossiers/' + value['dossier_id'])
        content = state_block('wait', 'Où j’en suis', 'Préparation en attente', '<p>L’envoi a été enregistré. L’assistant prépare une réponse.</p>',
                              f'<a class="button" href="{text(url)}">Consulter le cas d’usage et son avancement</a>')
    else:
        dossier_id, revision = value['dossier_id'], value['revision']
        url = '/preparation/dossiers/' + dossier_id
        title = 'Est-ce le travail que vous voulez tester ?' if value['package'] else 'Précisons le résultat utile'
        historical = revision != value.get('current_revision', revision)
        referral = value.get('checks', {}).get('out_of_scope')
        editable = not historical and value['stage'] != 'waiting' and not referral
        disabled = '' if can_submit and editable else ' disabled aria-describedby="availability"'
        current_campaigns = [c for c in value.get('campaigns', []) if c['task']['revision'] == revision]
        navigation = '<nav class="steps" aria-label="Étapes de préparation">'
        current_step = 'comparaison' if current_campaigns else 'validation' if value['validation'] else 'exemple' if value['package'] else 'besoin'
        steps = [('besoin', 'Besoin'), ('exemple', 'Exemple'), ('validation', 'Validation')] + ([('comparaison', 'Comparaison')] if current_campaigns else [])
        order = [anchor for anchor, _ in steps]
        for number, (anchor, label) in enumerate(steps, start=1):
            inner = '<span class="n">' + str(number) + '</span>' + label
            if anchor in ('exemple', 'validation') and not value['package']:
                navigation += '<span aria-disabled="true">' + inner + '</span>'
            else:
                done = ' class="done"' if order.index(anchor) < order.index(current_step) else ''
                navigation += '<a href="#' + anchor + '"' + (' aria-current="step"' if anchor == current_step else done) + '>' + inner + '</a>'
        navigation += '<small>Cas d’usage privé · pièces entièrement inventées</small></nav>'
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
        content = '<p class="tag">Cas d’usage inventé · révision ' + text(revision) + '</p>'
        if historical:
            content += '<p class="notice">Révision précédente en lecture seule. Pour modifier ou valider, ouvrez la révision courante.</p>'
        actions = ''
        if historical:
            actions = f'<a class="button" href="{text(url)}">Revenir à la révision courante</a>'
        if pending:
            title = heading
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
        refresh = '' if 'Actualiser cet état' in actions else f'<a href="{text(path)}">Actualiser cet état</a> · '
        content += f'<p class="hint">{refresh}<a href="{text(url)}">Révision courante</a>'
        if revision > 1:
            content += f' · <a href="{text(url)}/revisions/{revision - 1}">Révision précédente</a>'
        content += '</p>'
        if editable and value['package'] is None:
            content += section('Votre réponse', form(csrf, url + '/messages',
                {'action_id': secrets.token_hex(16), 'revision': revision, 'kind': 'clarify'},
                '<label for="message">Votre précision</label><textarea id="message" name="message" rows="3" required maxlength="1000"' + disabled + '></textarea>'
                '<div class="website"><label for="website">Site web</label><input id="website" name="website" autocomplete="off" tabindex="-1"></div>'
                '<button type="submit"' + disabled + '>Envoyer ma réponse</button>'))
        payload = value['payload']
        content += section('Besoin conservé', '<p>' + text(payload['request']) + '</p>', 'besoin')
        if value.get('task_index'):
            content += '<details><summary>Historique du cas d’usage et versions d’épreuve</summary>' + render_task_index(value['task_index']) + '</details>'
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
                '<details class="example-content"><summary>Voir le contenu'
                + (f' {index}' if len(package['pieces']) > 1 else '') + '</summary>'
                + '<div class="example-text">' + text(value['example_contents'][piece['id']]) + '</div></details>'
                for index, piece in enumerate(package['pieces'], start=1)))
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
                'non estimable' if amount is None else amount + ' USD') + \
                '. Tokens utilisés × tarifs du modèle relevés avant appel ; ce montant n’est pas une facture.</p>'
        if value.get('observed_cost'):
            cost = value['observed_cost']
            content += '<p>Coût observé de cette préparation : ' + text(
                'INCONNU' if cost['status'] == 'UNKNOWN' else cost['amount'] + ' ' + cost['currency']) + '. Source : ' + text(cost['source']) + '.</p>'
        else:
            content += '<p>Coût observé : INCONNU en l’absence de reçu de coût.</p>'
        if value.get('cost_reconciliation'):
            proof, cost = value['cost_reconciliation'], value['effective_cost']
            content += '<p>Coût rapproché : ' + text(cost['amount'] + ' ' + cost['currency']) + '. Source : ' + text(
                proof['source']) + ', attestée par ' + text(proof['actor']) + ' le ' + text(proof['observed_at']) + \
                '. Le reçu original reste inchangé.</p>'
        content += '<section id="validation"><h2>Validation du cas d’usage</h2>'
        if value['validation']:
            content += '<p role="status">Votre validation est enregistrée pour ce cas d’usage, cette révision et cet exemple exact.</p>'
            if not current_campaigns and not automatic:
                content += '<p>En attente de préparation des conditions par le responsable.</p>'
        elif package:
            content += '<p role="status">Une nouvelle validation est requise pour l’exemple présenté.</p>'
        elif referral:
            content += '<p>Cette demande hors périmètre ne peut pas être validée ni comparée dans Bench-X.</p>'
        else:
            content += '<p>La validation sera possible lorsqu’un exemple à examiner sera disponible.</p>'
        if editable and package and value['stage'] == 'preview' and value['validation'] is None:
            content += '<p>Cette validation confirme la fidélité de cet exemple à votre besoin. Si la qualification est disponible, ' + ('elle utilise votre clé sur votre enveloppe de préparation' if value.get('personal_preparation') else 'elle est financée par l’opérateur sur l’enveloppe de préparation') + '. Aucun appel candidat ni publication n’est autorisé ici.</p>'
            content += '<div class="actionbar">' + form(csrf, url + '/validation', binding(dossier_id, revision, value['package_sha256']),
                            '<button type="submit">' + icon('i-check') + 'Oui, c’est le travail à tester</button>') + '</div>'
        content += '</section>'
        if current_campaigns:
            content += '<section id="comparaison"><h2>Comparaison</h2>'
            current_campaign = current_campaigns[-1]
            content += '<p><a class="button" href="' + text(url + '/campaigns/' + current_campaign['campaign_id'] + '/conditions') + '">Examiner les conditions et suivre la comparaison courante</a></p>'
            for campaign in reversed(current_campaigns[:-1]):
                content += '<p><a class="button sec" href="' + text(url + '/campaigns/' + campaign['campaign_id'] + '/conditions') + '">Consulter la comparaison du ' + text(date_lisible_utc(campaign['conditions']['frozen_at'])) + '</a></p>'
            content += '</section>'
        if editable and value['package'] is not None:
            content += '<details class="corr"><summary class="button sec">' + icon('i-pen') + 'Préciser ou corriger cet exemple</summary><div>' + form(csrf, url + '/messages',
                {'action_id': secrets.token_hex(16), 'revision': revision},
                '<p>Indiquez ce qui doit changer. Les accords non touchés et les révisions précédentes sont conservés. Une modification de l’exemple demande une nouvelle validation.</p>'
                '<label for="kind">Objet du message</label><select id="kind" name="kind"' + disabled + '>'
                '<option value="clarify">Répondre à la clarification ou confirmer le périmètre</option>'
                '<option value="correct"' + (' selected' if package else '') + '>Modifier cet exemple</option></select>'
                '<label for="message">Votre précision ou correction</label>'
                '<textarea id="message" name="message" rows="4" required maxlength="1000"' + disabled + '></textarea>'
                '<div class="website"><label for="website">Site web</label><input id="website" name="website" autocomplete="off" tabindex="-1"></div>'
                '<button type="submit"' + disabled + '>Envoyer ce message</button>') + '</div></details>'
        qualification = value.get('qualification', {})
        labels = {'PENDING': 'En attente', 'QUALIFIED': 'Contrôles requis prouvés',
                  'BLOCKED': 'Bloquée : référence ou contrôles insuffisamment prouvés',
                  'APPROVED': 'Approuvée par action opérateur locale'}
        if not referral:
            content += '<details><summary>Qualification et approbation de l’épreuve</summary>'
            content += section('Qualification', '<p>' + text(labels.get(
                qualification.get('qualification_status'), 'En attente')) + '</p>')
            if automatic:
                content += '<p>' + text(qualification['summary']) + '</p>'
                content += listing(finding['text'] for finding in qualification.get('findings', []))
            content += section('Approbation', '<p>' + text(labels.get(
                qualification.get('approval_status'), 'En attente')) + '</p>'
                '<p>La validation du besoin, la qualification et l’approbation restent distinctes. '
                'Aucun appel ni publication n’est autorisé par cet état. Les preuves, la référence '
                'et les limites de jugement sont réservées à l’inspection locale du responsable.</p>')
            content += '</details>'
        if value.get('qualified'):
            content += '<p><a class="button' + (' sec' if current_campaigns or historical else '') + '" href="' + text(
                url + '/configurations') + '">Choisir les modèles</a></p>'
        if 'campaigns' in value:
            content += render_campaign_history(value['campaigns'], url)
    if state and s9 and not pending and not value.get('checks', {}).get('out_of_scope'):
        reasons = {
            'access': 'Ajoutez votre clé Openrouter pour préparer un exemple avec votre propre accès.',
            'open': 'Échanges disponibles. Chaque envoi reste vérifié par le serveur avant admission.',
            'closed': 'Appels fermés : aucune admission de préparation ouverte.',
            'unconfigured': 'Appels fermés : aucun assistant configuré pour la préparation.',
            'waiting': 'Nouveaux appels fermés : une préparation est en attente. Actualisez pour consulter son état.',
            'interrupted': 'Appels fermés : préparation interrompue ou suspendue. Une intervention du responsable est nécessaire ; aucun rejeu automatique.',
            'restore': 'Appels fermés : restauration à vérifier par le responsable.',
            'unresolved': 'Appels fermés : effets ou coûts non résolus dans l’enveloppe de préparation.',
            'budget': 'Appels fermés : enveloppe insuffisante pour un nouvel échange.',
            'daily_cap': 'Appels fermés : plafond quotidien de préparation atteint.'}
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
    template = TEMPLATE_PATH.read_text()
    body_class = 's9 comparison' if value.get('kind') == 'comparison' else 's9' if s9 else ''
    version = 'v' + VERSION + ('+' + SOURCE_SHA[:7] if SOURCE_SHA else '')
    return (template.replace('{{title}}', text(title)).replace('{{body_class}}', body_class).replace('{{menu}}', menu)
            .replace('{{navigation}}', navigation).replace('{{layout_class}}', 'layout' if navigation else '')
            .replace('{{version}}', text(version)).replace('{{content}}', content).encode('utf-8'))
