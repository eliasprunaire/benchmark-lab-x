"""Vues de confidentialité sans session, secret ni accès au stockage"""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from .fragments import (ACCESS_REASONS, access_summary, bouton, btn, champ_cle, date_lisible_utc, etat, form, hidden, ic,
                        jour_lisible, lien, MOIS, mot, puce, releves, retrait_cle, ruban, text, tiroir)

PRIVACY_SCRIPT = '<script type="module" src="/preparation/privacy.js"></script>'
JOURS = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
# L'avertissement d'effacement paraît deux jours avant l'échéance
PREAVIS = timedelta(days=2)


def chemin_sur(target, defaut='/preparation'):
    """Chemin relatif sûr, sinon `defaut` : ni adresse absolue, ni `//`, ni barre oblique inverse, ni caractère de contrôle"""
    try:
        parsed = urlsplit(target)
        if (not isinstance(target, str) or not target.startswith('/') or target.startswith('//') or parsed.netloc or parsed.scheme
                or '\\' in target or any(ord(char) < 32 for char in target)):
            return defaut
    except (ValueError, TypeError, AttributeError):
        return defaut
    return target


def active_session(privacy):
    try:
        return (bool(privacy.get('csrf_token')) and datetime.fromisoformat(
            privacy['session_expires_at'].replace('Z', '+00:00')) > datetime.now(timezone.utc))
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def privacy_form(action, csrf, url, fields, body, *, disabled=False, classe=None, enveloppe=True):
    """Formulaire relayé par `privacy.js` ; `enveloppe` entoure le corps d'un fieldset, désactivé si l'accès est fermé"""
    content = ('<fieldset' + (' disabled' if disabled else '') + '>' + body + '</fieldset>') if enveloppe else body
    return form(csrf, url, fields, content, classe=classe).replace('<form ', '<form data-privacy-post="' + action + '" ', 1)


# ——— Mes données (/preparation/data) et historique local ———
def historique_local():
    """Section « Historique local de vos cas » : `privacy.js` remplit le statut et la liste, puis montre les boutons utiles"""
    return ('<section class="carte ge-pile" data-privacy-history aria-labelledby="t-hist"><h2 id="t-hist">Historique local de vos cas</h2>'
            '<p>Votre navigateur peut effacer ces copies sans prévenir, par exemple en navigation privée ou quand il manque de place. '
            'Exportez les cas que vous voulez garder.</p>'
            '<p class="petit ge-avert">' + ic('alerte') + '<span>Toute personne qui utilise ce profil de navigateur peut aussi les lire.</span></p>'
            '<p class="petit" role="status" data-privacy-status>Vos copies s’affichent ici quand JavaScript est activé.</p>'
            '<div class="actions"><button type="button" class="btn btn-sec" data-privacy-action="enable" hidden>Reprendre l’historique local</button>'
            '<button type="button" class="btn btn-sec" data-privacy-action="clear" hidden>Tout effacer et arrêter l’historique</button></div>'
            '<div data-privacy-list></div><noscript><p>Activez JavaScript pour voir et exporter '
            'les cas gardés dans ce navigateur.</p></noscript></section>')


def table_cas(value):
    """Section « Vos cas d’usage » : les cas encore ouverts sur le serveur, l'échéance la plus lointaine d'abord"""
    privacy = value.get('privacy')
    # Tri stable en deux passes : identifiant, puis échéance décroissante ; un cas sans échéance passe en dernier
    cas = sorted(sorted(value.get('dossiers') or [], key=lambda d: d['dossier_id']), key=lambda d: d.get('expires_at') or '', reverse=True)
    if not cas:
        corps = '<p class="ge-vide ge-vide-cas">Aucun cas d’usage.</p>'
    else:
        lignes = ''.join(
            '<tr><td><a href="/preparation/dossiers/' + quote(d['dossier_id'], safe='') + '">' + text(d.get('need') or 'Cas d’usage sans description')
            + '</a></td><td>Version ' + str(d['revision']) + '</td><td data-label="Effacement prévu">'
            + (text(date_echeance(echeance(d))) if echeance(d) else 'inconnu') + alerte_expiration(d) + '</td><td>' + gestion_cas(d, privacy) + '</td></tr>'
            for d in cas)
        corps = ('<div class="table-defile"><table class="table-cas" aria-labelledby="t-cas"><thead><tr><th scope="col">Cas d’usage</th>'
                 '<th scope="col">Version</th><th scope="col">Effacement prévu</th><th scope="col">Actions</th></tr></thead><tbody>' + lignes
                 + '</tbody></table></div><p>' + lien('/preparation/catalogue', 'Exemples validés et benchmarks') + '</p>')
    return '<section class="ge-pile" aria-labelledby="t-cas"><h2 id="t-cas">Vos cas d’usage</h2>' + corps + '</section>'


def render_privacy_page(value, csrf=''):
    conservation = ('<section class="carte ge-pile" aria-labelledby="t-cons"><h2 id="t-cons">Conservation automatique</h2>'
                    '<p>Evaly efface seul ce qui ne sert plus. Vous n’avez rien à faire.</p>'
                    '<ul class="liste-faits"><li><b>Un cas d’usage</b> est effacé après 7 jours sans activité.</li>'
                    '<li><b>Votre clé OpenRouter</b> est effacée après 30 jours sans activité.</li></ul>'
                    '<p>Deux jours avant l’effacement d’un cas, cette page affiche « Bientôt effacé » sur sa ligne.</p></section>')
    # Les cas ouverts d'abord, puis les copies du navigateur, puis les règles de conservation
    return 'Mes données', '<div class="ge-pile">' + table_cas(value) + historique_local() + conservation + '</div>'


# ——— Repère d'activité de Mes cas d'usage, lignes des cas de Mes données, alerte d'effacement ———
def marqueur_activite(privacy, csrf):
    """Repère que `privacy.js` lit pour prolonger l'accès de la session ; absent quand l'accès est fermé"""
    if not privacy or not active_session(privacy):
        return ''
    return '<span data-privacy-activity data-csrf-token="' + text(privacy.get('csrf_token', csrf)) + '" hidden></span>'


def echeance(dossier):
    """Échéance d'effacement d'un cas en UTC, ou None si la vue ne la connaît pas"""
    try:
        return datetime.fromisoformat(dossier['expires_at'].replace('Z', '+00:00')).astimezone(timezone.utc)
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def date_echeance(moment):
    return JOURS[moment.weekday()] + ' ' + str(moment.day) + ' ' + MOIS[moment.month - 1]


def alerte_expiration(dossier, maintenant=None):
    """« Bientôt effacé » : seulement si la vue connaît l'échéance (`expires_at`) et qu'elle tombe dans les deux jours"""
    moment = echeance(dossier)
    maintenant = maintenant or datetime.now(timezone.utc)
    if moment is None or not maintenant < moment <= maintenant + PREAVIS:
        return ''
    return ('<p class="etat-ligne alerte-exp">' + mot('Bientôt effacé', 'vig') + '<span>Sans nouvelle activité, ce cas sera effacé le '
            + text(date_echeance(moment)) + '.</span>' + lien('/preparation/dossiers/' + quote(dossier['dossier_id'], safe=''), 'Le garder') + '</p>')


def adresse_suppression(dossier_id, retour):
    return '/preparation/dossiers/' + quote(dossier_id, safe='') + '/delete?retour=' + quote(retour, safe='/')


def gestion_cas(dossier, privacy):
    """Boutons de gestion d'une ligne de Mes données : garder une copie (`privacy.js`) et supprimer (page de confirmation)"""
    identifiant = dossier['dossier_id']
    attributs = ' data-privacy-controls data-dossier-id="' + text(identifiant) + '"'
    if active_session(privacy or {}):
        # Bouton de copie seulement : aucune copie automatique au chargement, seule la page du cas ouvert copie d'elle-même
        attributs += ' data-privacy-manual'
    titre = dossier.get('need') or 'Cas d’usage sans description'
    # Statut visible, vide par défaut : `privacy.js` n'y écrit que la réussite ou l'échec d'une copie
    return ('<div class="ge-gerer"' + attributs + '><p class="petit statut-copie" role="status" data-privacy-status></p>'
            '<button type="button" class="btn btn-sec btn-s" data-privacy-action="archive" hidden>' + ic('doc') + 'Garder une copie dans ce navigateur</button>'
            '<button type="button" class="btn btn-sec btn-s" data-privacy-action="enable-case" hidden>Réactiver l’historique de ce cas</button>'
            '<a class="btn btn-danger btn-s" href="' + text(adresse_suppression(identifiant, '/preparation')) + '" aria-label="'
            + text('Supprimer le cas d’usage « ' + titre + ' »') + '">' + ic('croix') + 'Supprimer ce cas d’usage</a></div>')


# ——— Suppression d'un cas ———
def zone_suppression(dossier_id, retour, actif):
    texte = ('Efface la copie de ce navigateur et demande la suppression sur le serveur. Une confirmation vous est demandée.' if actif
             else 'Votre accès à ce cas est fermé : la suppression n’est plus possible depuis cette page.')
    action = ('<a class="btn btn-danger btn-s" href="' + text(adresse_suppression(dossier_id, retour)) + '">' + ic('croix') + 'Supprimer ce cas d’usage</a>'
              if actif else '<button class="btn btn-danger btn-s" type="button" disabled>' + ic('croix') + 'Supprimer ce cas d’usage</button>')
    return ('<section class="zone-suppr" aria-labelledby="t-suppr"><div><h2 id="t-suppr">Supprimer ce cas d’usage</h2><p class="petit">'
            + texte + '</p></div>' + action + '</section>')


def render_delete_confirmation(value, csrf):
    """Confirmation avant suppression : (titre, contexte sous le h1, contenu) ; le retour est revalidé ici"""
    dossier = value['dossier']
    privacy = value.get('privacy', {})
    retour = chemin_sur(value.get('return_path'))
    contexte = puce(dossier.get('need') or 'Cas d’usage sans description', plein=True) + puce('Toutes les versions') + ruban('Exemple inventé')
    corps = ('<p class="texte-l">Vous allez supprimer <strong>« ' + text(dossier.get('need') or 'Cas d’usage sans description') + ' »</strong>.</p>'
             '<ul class="liste-faits"><li>La copie gardée dans ce navigateur est effacée.</li>'
             '<li>Evaly demande la suppression de ce cas sur le serveur, contribution comprise si vous en avez fait une.</li>'
             '<li>Des copies peuvent rester dans les sauvegardes du serveur.</li>'
             '<li>Vous ne pourrez pas retrouver ce cas d’usage.</li></ul>'
             '<noscript><p class="petit">Sans JavaScript, seule la suppression sur le serveur est demandée, contribution comprise. '
             'Pour effacer aussi la copie gardée dans ce navigateur, activez JavaScript puis passez par Mes données.</p></noscript>'
             '<p class="petit" role="status" data-privacy-status></p>'
             '<div class="actions"><button class="btn btn-danger" type="submit">' + ic('croix') + 'Oui, supprimer définitivement</button>'
             + '<span class="annuler">' + lien(retour, 'Annuler et garder ce cas', 'retour') + '</span>'
             '<span class="apres-suppression">' + lien('/preparation', 'Revenir à mes cas d’usage', 'retour') + '</span></div>')
    formulaire = privacy_form('delete', privacy.get('csrf_token', csrf), '/preparation/dossiers/' + quote(dossier['dossier_id'], safe='') + '/delete',
                              {}, corps, disabled=not active_session(privacy), classe='carte confirmer')
    return 'Supprimer ce cas d’usage ?', contexte, formulaire


def render_deleted():
    """Bandeau de la page Mes cas d'usage après une suppression sans JavaScript"""
    return etat('ok', 'Supprimé', 'Le cas d’usage a été supprimé',
                'La suppression sur le serveur est demandée. Les copies gardées dans ce navigateur restent : effacez-les dans Mes données.')


def render_privacy_controls(value, csrf, contribution='', path='/preparation'):
    """Pied des pages d'un cas : choix de partage replié, suppression, puis copie de ce cas dans ce navigateur"""
    privacy = value.get('privacy')
    if not privacy or 'dossiers' in value:
        # Mes cas d'usage écrit son propre repère d'activité ; Mes données n'en écrit aucun
        return ''
    dossier = privacy.get('dossier_id')
    if not dossier:
        return marqueur_activite(privacy, csrf)
    csrf = privacy.get('csrf_token', csrf)
    active = active_session(privacy)
    attrs = ' data-privacy-controls data-csrf-token="' + text(csrf) + '" data-dossier-id="' + text(dossier) + '"'
    if active:
        attrs += ' data-privacy-activity'
    if privacy.get('campaign_id'):
        attrs += ' data-campaign-id="' + text(privacy['campaign_id']) + '"'
    if type(privacy.get('content_version')) is int:
        attrs += ' data-content-version="' + str(privacy['content_version']) + '"'
    content = contribution
    if 'stage' in value and (value.get('package') or value.get('source') or value.get('checks', {}).get('out_of_scope')):
        content += zone_suppression(dossier, urlsplit(path).path, active)
    # Le statut et les boutons sont des enfants directs : `privacy.js` les cherche ainsi et ouvre ce dépliant en cas d'échec
    content += ('<details class="tiroir privacy-controls"' + attrs + '><summary><span class="titre">Copie dans ce navigateur</span>'
                '<span class="resume">Historique local</span><span class="plus" aria-hidden="true"></span></summary>'
                '<p class="petit" role="status" data-privacy-status>Vos copies s’affichent ici quand JavaScript est activé.</p>'
                '<div class="tiroir-corps"><div class="actions"><button type="button" class="btn btn-sec btn-s" data-privacy-action="archive" hidden>'
                'Garder une copie dans ce navigateur</button><button type="button" class="btn btn-sec btn-s" data-privacy-action="enable-case" hidden>'
                'Réactiver l’historique de ce cas</button></div></div></details>')
    return content


# ——— Partage d'un exemple ———
def render_contribution(value, csrf):
    privacy = value.get('privacy', {})
    contribution = privacy.get('contribution', {})
    if (not value.get('package') or not privacy.get('dossier_id') or not contribution
            or value.get('revision') != value.get('current_revision', value.get('revision'))
            or contribution.get('example_revision') != value.get('revision')):
        return ''
    enabled = contribution.get('enabled') is True
    inactif = not active_session(privacy)
    arret = ' disabled' if inactif else ''
    plus = tiroir('Ce que ce choix change',
                  '<p>Cocher la case autorise Evaly à conserver cet exemple pendant 6 mois pour améliorer le service. '
                  'Cette autorisation ne permet pas de le publier. Le responsable de ce traitement est Cybrel, la société qui édite Evaly.</p>'
                  '<p>Décocher arrête la contribution. L’historique local de ce navigateur ne dépend pas de ce choix : gérez-le dans '
                  '<a href="/preparation/data">Mes données</a>. Votre choix ne change rien à votre accès. '
                  '<a href="/confidentialite">Politique de confidentialité</a>.</p>')
    corps = ('<fieldset' + arret + '><legend class="cache">Votre choix de partage</legend>'
             '<p class="petit">Evaly le conserve 6 mois pour améliorer le service, sans le publier.</p>'
             '<label class="case"><input type="checkbox" name="enabled" value="true"' + (' checked' if enabled else '') + '>'
             '<span>Je partage cet exemple avec Evaly</span></label></fieldset>'
             '<div class="actions">' + bouton('Enregistrer mon choix', sec=True, icone=None).replace('<button ', '<button' + arret + ' ', 1) + '</div>'
             '<p class="petit" role="status" data-privacy-status>' + ('Vous partagez cet exemple avec Evaly.' if enabled else 'Vous ne partagez pas cet exemple.') + '</p>'
             + plus)
    formulaire = privacy_form('contribution', privacy.get('csrf_token', csrf),
                              '/preparation/dossiers/' + quote(privacy['dossier_id'], safe='') + '/contribution',
                              {key: contribution[key] for key in ('revision', 'example_revision')},
                              tiroir('Partager cet exemple', corps, resume='Facultatif · ' + ('partagé' if enabled else 'non partagé')),
                              classe='contribution', enveloppe=False)
    return '<div class="privacy-consent">' + formulaire + '</div>'


def render_contributions(value):
    etats = {'active': ('ok', 'Active'), 'withdrawn': ('inc', 'Retirée'), 'expired': ('att', 'Expirée')}
    content = ''
    for numero, item in enumerate(value['contributions'], 1):
        status = str(item.get('status', '')).lower()
        depasse = False
        if status == 'active':
            try:
                expiry = datetime.fromisoformat(item['expires_at'].replace('Z', '+00:00'))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry <= datetime.now(timezone.utc):
                    status, depasse = 'expired', True
            except (KeyError, ValueError, TypeError, AttributeError):
                status = 'unknown'
        ton, libelle = etats.get(status, ('inc', 'État inconnu'))
        phrase = 'Conservée jusqu’au ' + date_lisible_utc(item['expires_at']) + '.'
        if depasse:
            phrase += ' La date de conservation est dépassée\xa0: la contribution s’affiche expirée même si elle était notée active.'
        url = '/preparation/contributions/' + quote(item['id'], safe='') + '/withdraw'
        if status == 'active':
            # Le retrait efface la copie gardée sur le serveur : il se confirme, même sans JavaScript, comme le retrait de la clé
            retrait = ('<details class="confirmation-repliee"><summary class="btn btn-sec">Retirer mon consentement</summary>'
                       '<div class="confirmation"><p>Retirer votre consentement ? La copie de contribution gardée sur le serveur sera effacée.</p>'
                       + privacy_form('withdraw', value['csrf_token'], url, {},
                                      '<div class="actions"><button class="btn btn-danger btn-s" type="submit">Oui, retirer mon consentement</button></div>'
                                      '<p class="petit" role="status" data-privacy-status></p>') + '</div></details>')
        else:
            retrait = privacy_form('withdraw', value['csrf_token'], url, {},
                                   bouton('Retirer mon consentement', sec=True, icone=None), disabled=True)
        content += etat(ton, libelle, 'Contribution du ' + jour_lisible(item['created_at']), phrase, retrait, ident='contribution-' + str(numero))
    if not value['contributions']:
        content += etat('inc', 'Sans accès', 'Ce navigateur n’a accès à aucune contribution',
                        'Un navigateur ne voit que les contributions faites depuis lui.', lien('/preparation', 'Revenir à mes cas d’usage', 'retour'))
    content += tiroir('Ce que le retrait change',
                      '<p>Vous pouvez retirer votre consentement sans enregistrer de nouveau votre clé OpenRouter : ce navigateur garde un accès séparé, '
                      'réservé à vos contributions.</p><p>Retirer un consentement arrête seulement la contribution concernée. L’historique local continue, et '
                      'les copies déjà gardées dans ce navigateur restent. Pour le mettre en pause ou les effacer, allez dans '
                      '<a href="/preparation/data">Mes données</a>.</p>')
    return 'Mes contributions', '<div class="ge-pile">' + content + '<p>' + lien('/preparation/data', 'Mes données') + '</p></div>'


# ——— Ouverture de l'espace ———
def render_bootstrap(value):
    demande = value.get('return_path', '/preparation')
    target = chemin_sur(demande)
    sur = target == demande
    phrase = ('Après l’ouverture, vous revenez à la page demandée.' if sur
              else 'L’adresse de retour est refusée : vous arrivez sur la page Mes cas d’usage.')
    carte = etat('ok' if sur else 'vig', 'Adresse de retour sûre' if sur else 'Adresse de retour refusée',
                 'Ouverture de votre espace dans ce navigateur…', corps_html='<p role="status" data-privacy-status>' + text(phrase) + '</p>')
    decision = ('<form class="decision" method="post" action="/preparation/session/open">' + hidden('return_path', urlsplit(target).path)
                + '<span class="etiq">Étape unique</span><h2>Ouvrir mon espace</h2><p>Cette étape ne lance aucun modèle.</p>'
                '<button class="btn btn-xl" type="submit">Continuer' + ic('fleche') + '</button></form>')
    return 'Ouvrir mon espace', ('<section class="ge-ouvre ge-pile" data-privacy-bootstrap data-return-path="' + text(target) + '">' + carte + decision
        + '<p class="petit">Si rien ne se passe, les cookies de ce site sont peut-être bloqués : autorisez-les, puis choisissez Continuer.</p>'
        '<noscript><p>Choisissez Continuer pour ouvrir votre espace.</p></noscript></section>')


# ——— Clé OpenRouter ———
def render_access(value, csrf):
    status = value['status']
    retour = '/preparation/access'
    solde = ''
    if status == 'connected':
        carte = etat('ok', 'Connectée', 'Clé enregistrée')
        solde = access_summary(value, page_cle=True)
    elif status == 'invalid' and value.get('reason') not in ACCESS_REASONS:
        # Ancien motif dont la revérification n'a pas abouti : OpenRouter n'a rien refusé
        carte = etat('att', 'À revérifier', 'Vérification à refaire',
                     'OpenRouter n’a pas répondu. Rechargez cette page pour relancer la vérification.')
        solde = releves([('Solde restant', None, '', 'solde non lu')])
    elif status == 'invalid':
        carte = etat('ko', 'Clé refusée', 'Clé à remplacer', 'Cette clé ne peut pas être utilisée : ' + ACCESS_REASONS[value['reason']] + '.')
        solde = releves([('Solde restant', None, '', 'clé refusée, solde non lu')])
    elif status == 'disconnected':
        carte = etat('inc', 'Aucune clé', 'Aucune clé enregistrée', 'Ajoutez une clé réservée à Evaly. Elle paie les appels aux modèles : '
                     'préparation et contrôle de l’exemple, benchmark, puis vérification des réponses.')
    else:
        carte = etat('ko', 'Indisponible', 'Enregistrement de clé indisponible', 'Vous ne pouvez pas enregistrer de clé pour le moment. '
                     'Réessayez plus tard ; aucun modèle n’a été appelé.', lien(retour, 'Actualiser', 'actualiser'))
    fin = (tiroir('Où va ma clé ?', '<p>Votre clé est chiffrée et conservée sur notre serveur. Un cookie garde votre accès dans ce navigateur '
                  'd’une visite à l’autre. Il est supprimé au plus tard après 30 jours sans activité.</p><p>Vous pouvez retirer la clé ici à tout moment. '
                  'Enregistrer ou retirer la clé ne lance aucun modèle. Retirer la clé ici ne la révoque pas chez OpenRouter.</p>')
           + '<p class="petit">Tous les appels passent par votre clé OpenRouter. Evaly n’ajoute aucune limite de dépense.</p>'
           + '<p>' + lien('/preparation', 'Revenir à mes cas d’usage', 'retour') + '</p>')
    if status == 'unavailable':
        return '<div class="colonnes"><div class="ge-pile">' + carte + fin + '</div></div>'
    retrait = retrait_cle(csrf, retour, page_cle=True) if status in ('connected', 'invalid') else ''
    decision = ('<aside class="cote" aria-label="Clé">' + form(csrf, '/preparation/access/key', {'return': retour},
        '<span class="etiq">Clé OpenRouter</span><h2>' + ('Remplacer ma clé' if status in ('connected', 'invalid') else 'Ajouter ma clé') + '</h2>'
        '<p>Créez sur OpenRouter une clé réservée à Evaly. Vous choisissez chez OpenRouter son plafond de dépense, ou son absence.</p>'
        + champ_cle('Clé API OpenRouter', 'La saisie est masquée.', stockage=False)
        + '<button class="btn btn-xl" type="submit">Enregistrer la clé' + ic('fleche') + '</button>', classe='decision') + '</aside>')
    # Clé absente ou refusée : le formulaire est l'action principale et suit l'état ; clé connectée : il suit la clé et ses relevés
    if status == 'connected':
        return ('<div class="colonnes"><div class="ge-pile pile-l">' + carte + solde + retrait + '</div>' + decision
                + '<div class="ge-pile pile-bas">' + fin + '</div></div>')
    return ('<div class="colonnes"><div class="ge-pile pile-l">' + carte + '</div>' + decision
            + '<div class="ge-pile pile-bas">' + solde + retrait + fin + '</div></div>')


# ——— Erreurs ———
# Statut HTTP : (ton, mot de statut) ; le titre précise les cas qui partagent un statut
ERREURS = {400: ('ko', 'Refusée'), 403: ('ko', 'Refusée'), 404: ('vig', 'Introuvable'), 405: ('vig', 'Non acceptée'),
           409: ('ko', 'Copie refusée'), 500: ('ko', 'Erreur interne'), 503: ('att', 'Indisponible'), 504: ('att', 'Réponse en attente')}
# Titre : (ton, mot de statut) quand le statut seul ne suffit pas
ERREURS_PAR_TITRE = {'Adresse invalide': ('ko', 'Adresse refusée'), 'Session requise': ('vig', 'Session expirée')}


def code_erreur(value):
    code = value.get('http_status')
    return code if code in ERREURS else 503 if value.get('unavailable') else 400


def actions_erreur(value, path='/preparation'):
    """(action principale, autres actions) d'une page d'erreur selon son statut et son titre"""
    code, titre = code_erreur(value), value.get('title')
    accueil = lien('/', 'Retour à l’accueil', 'retour')
    if titre == 'Session requise':
        return btn(path, 'Ouvrir la confirmation de copie'), ()
    if titre in ('Copie impossible', 'Épreuve introuvable', 'Copie refusée'):
        return btn('/epreuves', 'Voir les épreuves'), ()
    if titre == 'Adresse invalide' or code == 405:
        return btn('/', 'Retour à l’accueil', icone='retour'), ()
    if code == 404:
        return btn('/preparation', 'Revenir à mes cas d’usage'), (accueil,)
    if code == 500:
        return btn('/preparation', 'Revenir à mes cas d’usage'), ()
    if code == 503:
        return btn('/preparation', 'Réessayer', icone='actualiser'), (accueil,)
    return btn('/preparation', 'Revenir à mes cas d’usage'), ()


def erreur_grande(value, texte, principal, autres=(), *, mot_texte=None, alerte=True):
    """Grand état d'erreur : code HTTP, mot de statut, une phrase, une action principale ; le code arrive du serveur"""
    code = code_erreur(value)
    ton, defaut = ERREURS_PAR_TITRE.get(value.get('title')) or ERREURS[code]
    if mot_texte:
        ton, defaut = 'ko', mot_texte
    return ('<section class="g-err" data-ton="' + ton + '"><div class="g-err-code" aria-hidden="true"><b>' + str(code) + '</b></div>'
            '<div class="g-err-texte">' + mot(defaut, ton) + '<p' + (' role="alert"' if alerte else '') + '>' + text(texte) + '</p>'
            '<div class="actions">' + principal + ''.join(autres) + '</div></div></section>')
