"""Fragments HTML partagés par les modules de rendu du parcours privé.

Ce module ne connaît ni la préparation ni les campagnes : il n'expose que les
primitives réutilisées par plusieurs vues (échappement, formulaires natifs,
repères visuels, dates, montants, preuves inertes). Il n'importe aucun module
de rendu, pour qu'aucun cycle ne soit possible.
"""
import zlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape

MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin',
        'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')


def text(value):
    """Valeur échappée pour le texte comme pour les attributs"""
    return escape(str(value), quote=True)


# Anciens identifiants du sprite, repris par `icon` sous le nom du kit
ICONES_ANCIENNES: dict[str, str] = {'i-cross': 'i-croix', 'i-help': 'i-question', 'i-clock': 'i-horloge', 'i-pen': 'i-stylo', 'i-alert': 'i-alerte'}


def ic(nom):
    """Icône du sprite du gabarit, par son nom sans préfixe (« fleche », « check », « croix »…)"""
    return '<svg class="ic" aria-hidden="true"><use href="#i-' + text(nom) + '"/></svg>'


def icon(name: str):
    """Icône du sprite par son identifiant avec préfixe (« i-check ») ; les anciens noms restent acceptés"""
    return ic(ICONES_ANCIENNES.get(name, name).removeprefix('i-'))


def cadre(corps_html):
    """Colonne centrée de la page : tout contenu hors bandes pleine largeur y est placé"""
    return '<div class="cadre">' + corps_html + '</div>'


def disque(classe=''):
    """Disque d'essai, repère du sujet testé"""
    return '<span class="disque' + (' ' + text(classe) if classe else '') + '" aria-hidden="true"></span>'


ETAPES = (('besoin', 'Besoin'), ('exemple', 'Exemple'), ('validation', 'Validation'),
          ('modeles', 'Modèles'), ('resultats', 'Résultats'))


def piste(courante, liens=None):
    """Les cinq stations du cas d'usage ; `courante` est une clé de ETAPES, `liens` associe une clé à son adresse (la courante aussi)"""
    liens = liens or {}
    courante_index = [cle for cle, _ in ETAPES].index(courante)
    elements = []
    for index, (cle, libelle) in enumerate(ETAPES):
        numero = '<span class="n">0' + str(index + 1) + '</span><span class="l">' + text(libelle) + '</span>'
        if index == courante_index:
            corps = disque() + numero + '<span class="cache"> (étape en cours)</span>'
            attributs = ' aria-current="step"'
        else:
            corps = numero + ('<span class="cache"> (franchie)</span>' if index < courante_index else '')
            attributs = ' class="fait"' if index < courante_index else ''
        elements.append('<li' + attributs + '>' + ('<a href="' + text(liens[cle]) + '">' + corps + '</a>' if cle in liens
                                                     else '<span class="st">' + corps + '</span>') + '</li>')
    return '<nav class="piste" aria-label="Étapes du cas d’usage"><ol>' + ''.join(elements) + '</ol></nav>'


def tete(titre, *, piste_html='', contexte_html='', sous_titre_html=''):
    """En-tête d'écran : piste, titre h1, puces de contexte ; le gabarit l'écrit déjà pour chaque page"""
    contexte = '<div class="contexte">' + contexte_html + '</div>' if contexte_html else ''
    return ('<div class="cadre">' + piste_html + '<header class="tete"><h1>' + text(titre) + '</h1>'
            + sous_titre_html + contexte + '</header></div>')


def puce(texte, plein=False, icone: str | None = None):
    """Puce de contexte (nom du cas, version) ; `plein` l'inverse"""
    return ('<span class="puce' + (' puce-plein' if plein else '') + '">'
            + (ic(icone) if icone else '') + text(texte) + '</span>')


def ruban(texte='Inventé'):
    """Ruban incliné qui marque une pièce ou un exemple inventé"""
    return '<span class="ruban">' + text(texte) + '</span>'


TONS = {'ok': 'check', 'ko': 'croix', 'att': 'horloge', 'vig': 'alerte', 'inc': 'question'}


def mot(texte, ton='inc'):
    """Mot de statut avec son icône ; le ton (ok, ko, att, vig, inc) double toujours la couleur par un mot"""
    return '<span class="mot" data-ton="' + ton + '">' + ic(TONS[ton]) + text(texte) + '</span>'


def etat(ton, mot_texte, titre, texte='', actions_html='', marque_html=None, *, corps_html='', ident=None):
    """Encadré d'état unique d'un écran : pastille, mot de statut, titre h2, une phrase, actions ; `corps_html` s'ajoute après la phrase"""
    ident = ident or 'etat-' + format(zlib.crc32((mot_texte + titre).encode()), 'x')
    repere = marque_html if marque_html is not None else '<span class="pastille">' + ic(TONS[ton]) + '</span>'
    return ('<section class="etat" data-ton="' + ton + '" aria-labelledby="' + ident + '">' + repere
            + mot(mot_texte, ton) + '<h2 id="' + ident + '">' + text(titre) + '</h2>'
            + ('<p>' + text(texte) + '</p>' if texte else '') + corps_html
            + ('<div class="actions">' + actions_html + '</div>' if actions_html else '') + '</section>')


VERDICTS = {'ok': ('Satisfait', 'check'), 'ko': ('Ne satisfait pas', 'croix'),
            'att': ('À reprendre', 'actualiser'), 'vig': ('Non vérifié', 'horloge'), 'inc': ('Inconnu', 'question')}


def tampon(ton, texte=None, *, xl=False, anime=''):
    """Tampon de verdict (ok, ko, att, vig, inc) ; `texte` remplace le libellé du ton, `anime` ajoute la frappe (« d1 » à « d5 »)"""
    libelle, nom = VERDICTS[ton]
    return ('<span class="tampon tampon-' + ton + (' tampon-xl' if xl else '') + (' frappe ' + anime if anime else '') + '">'
            + ic(nom) + text(texte or libelle) + '</span>')


# Verdict de tentative enregistré : (ton du tampon, icône, libellé)
VERDICT_BADGES = {'SATISFAIT': ('ok', 'check', 'Satisfait'), 'NE SATISFAIT PAS': ('ko', 'croix', 'Ne satisfait pas'),
                  None: ('att', 'actualiser', 'À reprendre')}


def badge(verdict):
    """Verdict d'une tentative sous forme de tampon lisible sans couleur ; un verdict inconnu garde son texte"""
    ton, _, libelle = VERDICT_BADGES.get(verdict, ('inc', 'question', str(verdict)))
    return tampon(ton, libelle)


def _classe_btn(sec, xl):
    return 'btn' + (' btn-sec' if sec else '') + (' btn-xl' if xl else '')


def btn(href, texte, *, sec=False, xl=False, icone: str | None = 'fleche', avant: str | None = None):
    """Lien en forme de bouton ; `sec` secondaire, `xl` grand, `avant` icône avant le texte, `icone=None` sans flèche"""
    return ('<a class="' + _classe_btn(sec, xl) + '" href="' + text(href) + '">' + (ic(avant) if avant else '')
            + text(texte) + (ic(icone) if icone else '') + '</a>')


def bouton(texte, *, sec=False, xl=False, icone: str | None = 'fleche', name=None, value=None):
    """Bouton d'envoi de formulaire, mêmes variantes que `btn`"""
    attributs = (' name="' + text(name) + '"' if name else '') + (' value="' + text(value) + '"' if value else '')
    return ('<button class="' + _classe_btn(sec, xl) + '" type="submit"' + attributs + '>' + text(texte)
            + (ic(icone) if icone else '') + '</button>')


def lien(href, texte, icone: str | None = 'fleche'):
    """Lien d'action discret avec flèche (ou autre icône, ou aucune)"""
    return '<a class="lien" href="' + text(href) + '">' + text(texte) + (ic(icone) if icone else '') + '</a>'


def tiroir(titre, corps_html, *, resume='', ouvert=False, ident=''):
    """Dépliant natif details ; `resume` donne le statut lisible replié, `ouvert` l'ouvre"""
    return ('<details class="tiroir"' + (' open' if ouvert else '') + (' id="' + text(ident) + '"' if ident else '')
            + '><summary><span class="titre">' + text(titre) + '</span>'
            + ('<span class="resume">' + text(resume) + '</span>' if resume else '')
            + '<span class="plus" aria-hidden="true"></span></summary><div class="tiroir-corps">' + corps_html + '</div></details>')


def releves(items, compact=False, icone: str = 'doc'):
    """Relevés chiffrés ; items : (étiquette, valeur, unité, note), valeur None affiche « inconnu » ; `compact` : une ligne par relevé"""
    sortie = []
    for libelle, valeur, unite, note in items:
        if valeur is None:
            dd, classe = 'inconnu', ' releve-texte'
        else:
            dd, classe = text(valeur) + ('<small>' + text(unite) + '</small>' if unite else ''), ''
        note_html = '<span class="note">' + text(note) + '</span>' if note else ''
        if compact:
            sortie.append('<div class="releve' + classe + '"><dt>' + ic(icone) + text(libelle)
                          + '</dt><dd><span class="val">' + dd + '</span>' + note_html + '</dd></div>')
        else:
            sortie.append('<div class="releve' + classe + '"><dt>' + text(libelle) + '</dt><dd>' + dd + '</dd>' + note_html + '</div>')
    return '<dl class="releves' + (' compact' if compact else '') + '">' + ''.join(sortie) + '</dl>'


def cles(paires):
    """Liste de définitions étiquette / valeur ; la valeur est du HTML déjà échappé"""
    return '<dl class="cles">' + ''.join('<dt>' + text(cle) + '</dt><dd>' + valeur + '</dd>' for cle, valeur in paires) + '</dl>'


def champ(libelle, nom, *, aide='', zone=True, grand=False, valeur='', type_='text', ident=None, placeholder='',
          maxlength=None, erreur='', desactive=False):
    """Champ étiqueté ; aide et message d'erreur (role alert, id `ident`-error) liés par aria-describedby ; zone de texte par défaut"""
    ident = ident or 'c-' + nom
    decrit = ([ident + '-aide'] if aide else []) + ([ident + '-error'] if erreur else [])
    aide_html = '<p class="aide" id="' + text(ident) + '-aide">' + text(aide) + '</p>' if aide else ''
    erreur_html = erreur_champ(ident, erreur) if erreur else ''
    attributs = ((' aria-describedby="' + text(' '.join(decrit)) + '"' if decrit else '')
                 + (' placeholder="' + text(placeholder) + '"' if placeholder else '')
                 + (' maxlength="' + str(maxlength) + '"' if maxlength else '')
                 + (' aria-invalid="true"' if erreur else '') + (' disabled' if desactive else ''))
    if zone:
        controle = ('<textarea id="' + text(ident) + '" name="' + text(nom) + '"' + attributs
                    + (' class="grand"' if grand else '') + '>' + text(valeur) + '</textarea>')
    else:
        controle = ('<input id="' + text(ident) + '" name="' + text(nom) + '" type="' + text(type_) + '"' + attributs
                    + (' value="' + text(valeur) + '"' if valeur else '') + '>')
    return '<div class="champ"><label for="' + text(ident) + '">' + text(libelle) + '</label>' + aide_html + controle + erreur_html + '</div>'


def erreur_champ(ident, message):
    """Message d'erreur d'un champ, annoncé aux lecteurs d'écran ; l'id `ident`-error se lie par aria-describedby"""
    return '<p class="g-erreur-champ" id="' + text(ident) + '-error" role="alert">' + ic('alerte') + text(message) + '</p>'


def zone(corps_html, *, titre='', etiquette='', droite_html='', classe=''):
    """Bande pleine largeur avec son cadre ; titre h2, étiquette en capitales, `droite_html` à droite du titre"""
    entete = ''
    if titre or etiquette:
        entete = ('<div class="zone-titre">' + ('<span class="etiq">' + text(etiquette) + '</span>' if etiquette else '')
                  + ('<h2>' + text(titre) + '</h2>' if titre else '') + droite_html + '</div>')
    return '<section class="zone ' + text(classe) + '"><div class="cadre">' + entete + corps_html + '</div></section>'


def harnais(suite=''):
    """Valeur « Harnais » : lien pi.dev et info-bulle sur le lancement à vide, sans numéro de version"""
    debut, _, dernier = ('même version pour tout le benchmark' + suite).rpartition(' ')
    # le dernier mot et le « i » restent sur la même ligne
    return ('<span class="harnais"><a href="https://pi.dev" rel="noreferrer noopener">pi.dev</a>, '
            + text(debut) + ' <span class="lie">' + text(dernier)
            + '<details class="info info-texte"><summary aria-label="Ce que contient le harnais pi.dev">'
            '<span class="i" aria-hidden="true">i</span></summary><div class="bulle-info"><p><strong>Lancé à vide.</strong> '
            'Evaly démarre pi.dev sans aucun outil, aucune extension, aucune skill et aucune configuration personnelle. '
            'Chaque modèle reçoit seulement la consigne et l’exemple, dans les mêmes conditions.</p></div></details></span></span>')


ETATS_ANCIENS = {'action': 'att', 'wait': 'att', 'warn': 'vig', 'err': 'ko', 'done': 'ok', 'unk': 'inc'}


def state_block(tone, eyebrow, heading, body, actions=''):
    """Bloc « Où j'en suis » (ton action, wait, warn, err, done, unk) rendu par `etat` ; `body` est du HTML"""
    return etat(ETATS_ANCIENS[tone], eyebrow, heading, actions_html=actions, corps_html=body)


def jour_lisible(value):
    """« 28 septembre 2026 » depuis une date ISO ; valeur brute si illisible"""
    try:
        moment = datetime.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    return f'{moment.day} {MOIS[moment.month - 1]} {moment.year}'


def date_lisible_utc(value):
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return str(value)
    return f'{moment.day} {MOIS[moment.month - 1]} {moment.year} à {moment:%H:%M:%S} UTC'


def montant_lisible(value):
    if value is None:
        return 'non estimable'
    try:
        return format(Decimal(str(value)), 'f').replace('.', ',')
    except (InvalidOperation, ArithmeticError, ValueError):
        return str(value)


def valeur_mesure(value, unit):
    """Valeur de mesure lisible : Oui/Non pour un booléen, nombre suivi de son unité, texte tel quel"""
    if value is None:
        return 'Inconnue'
    if type(value) is bool:
        return 'Oui' if value else 'Non'
    # Une mesure enregistrée en texte numérique garde son unité, comme un nombre
    if type(value) is str:
        try:
            number = Decimal(value.strip())
        except (InvalidOperation, ArithmeticError, ValueError):
            number = None
        if number is not None and number.is_finite():
            value = number
    if type(value) in (int, float, Decimal):
        amount = Decimal(str(value))
        if str(unit or '').strip().lower() in ('s', 'sec', 'seconde', 'secondes') and amount >= 60:
            minutes, seconds = divmod(amount, 60)
            return str(int(minutes)) + ' min' + ('' if seconds == 0 else ' ' + montant_lisible(seconds) + ' s')
        descriptive = str(unit or '').strip().lower() in ('', 'descriptif', 'descriptive', 'texte', 'text', 'description')
        return montant_lisible(value) + ('' if descriptive else ' ' + str(unit))
    return str(value)


def hidden(name, value):
    return f'<input type="hidden" name="{text(name)}" value="{text(value)}">'


def form(csrf, url, fields, content, *, form_id=None, classe=None):
    identity = ('' if form_id is None else f' id="{text(form_id)}"') + ('' if classe is None else f' class="{text(classe)}"')
    return (f'<form{identity} method="post" action="{text(url)}">' + hidden('csrf_token', csrf)
            + ''.join(hidden(k, v) for k, v in fields.items()) + content + '</form>')


def section(title, content, anchor=None):
    """Section d'une colonne : titre h2 et contenu, ancre facultative"""
    target = '' if anchor is None else f' id="{text(anchor)}"'
    return f'<section class="pile"{target}><div class="zone-titre"><h2>{text(title)}</h2></div>{content}</section>'


def workbench(main, side, label, *, side_first=False):
    """Colonne principale et colonne de synthèse collante ; `side_first` met la synthèse avant le détail dans le HTML (ordre de lecture)"""
    aside = '<aside class="cote' + (' cote-avant' if side_first else '') + '" aria-label="' + text(label) + '">' + side + '</aside>'
    body = '<div class="pile-l">' + main + '</div>'
    return '<div class="colonnes">' + (aside + body if side_first else body + aside) + '</div>'


def readout(label, value):
    """Relevé en grand d'un chiffre déjà connu, toujours nommé"""
    return releves([(label, value, '', '')])


def listing(values):
    return '<ul>' + ''.join(f'<li>{text(v)}</li>' for v in values) + '</ul>'


FIELD_LABELS: dict[str, str] = {'requested_configuration': 'Configuration demandée', 'observed_configuration': 'Configuration observée',
              'observation_sources': 'Sources des observations', 'model': 'Modèle', 'provider': 'Fournisseur',
              'revision': 'Révision', 'access': 'Accès', 'channel_id': 'Canal', 'route': 'Route',
              'effort': 'Effort de raisonnement', 'parameters': 'Paramètres', 'max_tokens': 'Limite de tokens',
              'temperature': 'Température', 'source': 'Source', 'unit': 'Unité', 'value': 'Valeur',
              'measure': 'Mesure', 'proof': 'Preuve', 'favorable': 'Sens favorable', 'aggregation': 'Agrégation',
              'scope': 'Périmètre', 'attempts': 'Tentatives', 'conversion': 'Conversion', 'frozen_at': 'Date de gel',
              'environment': 'Environnement', 'package': 'Paquet', 'version': 'Version', 'status': 'État',
              'criteria': 'Critères', 'obligations': 'Obligations',
              'eliminatory_errors': 'Erreurs éliminatoires', 'result_expected': 'Résultat attendu',
              'panel': 'Modèles comparés', 'limits': 'Limites', 'pi': 'Harnais',
              'conditions': 'Conditions', 'data_collection': 'Collecte des données', 'allow_fallbacks': 'Repli autorisé',
              'only': 'Fournisseurs autorisés', 'order': 'Ordre des fournisseurs', 'require_parameters': 'Paramètres exigés',
              'reasoning': 'Raisonnement', 'enabled': 'Activé', 'exclude': 'Exclu de la réponse',
              'context_window': 'Fenêtre de contexte', 'defaults': 'Réglages par défaut',
              'defaults_source': 'Origine des réglages par défaut', 'max_output_tokens': 'Limite de sortie',
              'node_version': 'Version de Node', 'packages': 'Paquets', 'skills': 'Compétences', 'tools': 'Outils',
              'system_prompt': 'Consigne système', 'timeout_seconds': 'Délai maximal en secondes'}


# Valeurs codées du moteur ; toute autre valeur reste affichée telle quelle
VALUE_LABELS: dict[str, str] = {'INCONNU': 'inconnu', 'UNKNOWN': 'inconnu',
                                'DEFAULT_MAX_OUTPUT_TOKENS': 'limite par défaut d’Evaly'}


def field_label(key: str) -> str:
    """Libellé d'une clé ; une clé sans libellé reste citée telle quelle"""
    return FIELD_LABELS.get(key, '« ' + key + ' »')


def readable_fields(value):
    """Present structured evidence as inert, labelled fields"""
    if isinstance(value, dict):
        return ('<dl class="evidence-fields">' + ''.join(
            '<dt>' + text(field_label(key)) + '</dt><dd>' + readable_fields(item) + '</dd>'
            for key, item in value.items()) + '</dl>') if value else '<span>Non renseigné</span>'
    if isinstance(value, list):
        return ('<ul>' + ''.join('<li>' + readable_fields(item) + '</li>' for item in value) + '</ul>') if value else '<span>Aucun élément déclaré</span>'
    if value is None:
        value = 'Non renseigné'
    elif type(value) is bool:
        value = 'Oui' if value else 'Non'
    elif type(value) is str:
        value = VALUE_LABELS.get(value, value)
    return '<span class="verbatim">' + escape(str(value), quote=True) + '</span>'


ACCESS_REASONS = {
    'ACCESS_CAP_REQUIRED': 'elle doit avoir un plafond non renouvelable de 50 USD maximum et un solde disponible',
    'KEY_REJECTED': 'OpenRouter refuse cette clé',
    'SESSION_EXPIRED': 'votre session dans ce navigateur a expiré',
}


def access_summary(access, *, page_cle=False):
    """Solde et plafond annoncés par OpenRouter en relevés, jamais la clé ; une valeur absente se lit « inconnu »"""
    def montant(libelle, cle, note=''):
        valeur = access.get(cle)
        return (libelle, None if valeur is None else montant_lisible(valeur), 'USD', note)
    solde = montant('Solde restant', 'limit_remaining_usd', 'selon OpenRouter') if page_cle else montant('Solde restant selon OpenRouter', 'limit_remaining_usd')
    return releves([solde, montant('Plafond de la clé', 'limit_usd')], compact=not page_cle, icone='cle')


def champ_cle(libelle, aide='', *, stockage=True):
    """Champ de la clé OpenRouter : saisie masquée, jamais mémorisée ni préremplie par le navigateur ; `stockage` lie la note `key-storage`"""
    decrit = (['key-help'] if aide else []) + (['key-storage'] if stockage else [])
    return ('<div class="champ"><label for="openrouter-key">' + text(libelle) + '</label>'
            + ('<p class="aide" id="key-help">' + text(aide) + '</p>' if aide else '')
            + '<input id="openrouter-key" name="key" type="password" autocomplete="new-password" required maxlength="512"'
            + (' aria-describedby="' + ' '.join(decrit) + '"' if decrit else '') + '></div>')


CLE_STOCKAGE = ('La clé est chiffrée et conservée sur notre serveur. Un cookie garde votre accès dans ce navigateur, '
                'jusqu’à 30 jours sans activité. Enregistrer ou retirer la clé ne lance aucun modèle.')
CLE_REMPLACEMENT = ('Attendez la fin d’une préparation ou d’un contrôle de l’exemple en cours avant de changer de clé. '
                    'Retirer la clé n’annule pas un benchmark déjà lancé, et la clé reste active chez OpenRouter.')
CLE_RETRAIT = ('Attendez la fin d’une préparation ou d’un contrôle de l’exemple en cours avant de changer de clé. '
               'Une fois la clé retirée, Evaly ne lance plus aucun nouvel appel. La clé reste active chez OpenRouter, '
               'et un benchmark déjà lancé n’est pas annulé.')


def retrait_cle(csrf, back, *, page_cle=False):
    """« Retirer la clé de ce navigateur » se confirme, même sans JavaScript : le bouton d'envoi est replié derrière la question ;
    sur la page de la clé, sa mise en garde le suit"""
    confirmation = ('<details class="confirmation-repliee"><summary class="btn btn-sec">' + (ic('poubelle') if not page_cle else '')
                    + 'Retirer la clé de ce navigateur</summary><div class="confirmation"><p>Retirer la clé de ce navigateur ? '
                    'Evaly ne lancera plus aucun appel tant que vous n’en enregistrez pas une autre.</p>'
                    + form(csrf, '/preparation/access/disconnect', {'return': back},
                           '<div class="actions"><button class="btn btn-danger btn-s" type="submit">Oui, retirer la clé</button></div>') + '</div></details>')
    return confirmation + ('<p class="petit">' + text(CLE_RETRAIT) + '</p>' if page_cle else '')


def personal_key_form(csrf, access, back, *, opened=False):
    """Seul moyen de fournir un accès OpenRouter : la clé saisie, puis retour à `back`"""
    status = access.get('status')
    if status == 'connected':
        envoi = form(csrf, '/preparation/access/key', {'return': back},
                     champ_cle('Nouvelle clé API OpenRouter', stockage=False) + '<p class="petit">' + text(CLE_REMPLACEMENT) + '</p>'
                     '<div class="actions">' + bouton('Enregistrer la nouvelle clé', sec=True, icone='cle') + '</div>')
        return ('<div class="g-cle carte">' + access_summary(access)
                + tiroir('Remplacer ou retirer la clé', envoi + '<hr class="sep">' + retrait_cle(csrf, back), ouvert=opened) + '</div>')
    envoi = form(csrf, '/preparation/access/key', {'return': back},
                 champ_cle('Clé API OpenRouter', 'Clé réservée à Evaly, avec un plafond non renouvelable de 50 USD maximum. '
                           'Ce plafond est la seule limite de dépense.') + '<p class="petit" id="key-storage">' + text(CLE_STOCKAGE) + '</p>'
                 '<div class="actions">' + bouton('Enregistrer la nouvelle clé' if status == 'invalid' else 'Enregistrer la clé', icone='cle') + '</div>',
                 classe='g-cle carte')
    return envoi + retrait_cle(csrf, back) if status == 'invalid' else envoi


# ——— Lot campagne ———
def statut(ton, texte, icone):
    """Tampon de statut libre (couloirs et en-tête du suivi) : ton, mot et icône choisis par l'écran"""
    return '<span class="tampon tampon-' + ton + '">' + ic(icone) + text(texte) + '</span>'


# ——— Lot résultats : contexte du cas, tableau comparatif, signaux ———

def contexte_cas(besoin, version, *autres_html):
    """Puces sous le titre d'un écran de résultats : cas (tronqué), version de l'exemple, ruban, puces ajoutées"""
    mots = str(besoin).split()
    court = besoin if len(mots) <= 8 else ' '.join(mots[:8]) + '…'
    return puce(court, plein=True) + puce(version) + ruban('Exemple inventé') + ''.join(autres_html)


def signal(mot_html, texte_html):
    """Ligne d'un bloc `signaux` : un statut avec son mot, puis sa phrase"""
    return '<li>' + mot_html + '<span>' + texte_html + '</span></li>'


def info_modele(nom, rubriques):
    """Info-bulle « i » des conditions de test d'un modèle ; `rubriques` : (étiquette, valeur) en texte brut"""
    corps = ''.join('<dt>' + text(cle) + '</dt><dd>' + text(valeur) + '</dd>' for cle, valeur in rubriques)
    return ('<details class="info"><summary aria-label="Conditions de test de ' + text(nom)
            + '"><span class="i" aria-hidden="true">i</span></summary><div class="bulle-info"><dl>' + corps + '</dl></div></details>')


def ligne_modele(ident, nom, effort, resultat_html, mesures_html, cout_html, detail_html, *, sous_nom_html='', classe='', attrs=''):
    """Ligne du tableau comparatif : modèle, résultat, qualité observée, coût observé, preuves ; chaque cellule porte son `data-th`"""
    ligne_id = ' id="' + text(ident) + '"' if ident else ''
    return ('<tr' + ligne_id + (' class="' + text(classe) + '"' if classe else '') + attrs + '>'
            '<td data-th="Modèle"><span class="modele">' + text(nom) + '<small>' + text(effort) + '</small></span>' + sous_nom_html + '</td>'
            '<td data-th="Résultat">' + resultat_html + '</td>'
            '<td data-th="Qualité observée">' + mesures_html + '</td>'
            '<td data-th="Coût observé" class="nb">' + cout_html + '</td>'
            '<td data-th="Détails">' + detail_html + '</td></tr>')


def table_cas(titre, sous_titre, lignes_html, *, region):
    """Titre d'un exemple, puis son tableau comparatif ; la région défilable reste atteignable au clavier"""
    return ('<div class="cas-titre"><h3>' + text(titre) + '</h3><span class="petit">' + text(sous_titre) + '</span></div>'
            '<div class="table-defile" role="region" tabindex="0" aria-label="' + text(region) + '"><table class="empile comparatif">'
            '<caption class="cache">' + text(region) + ' : verdict, qualité et coût par modèle</caption>'
            '<colgroup><col class="c-mod"><col class="c-res"><col class="c-qua"><col class="c-cout"><col class="c-det"></colgroup>'
            '<thead><tr><th scope="col">Modèle</th><th scope="col">Résultat</th><th scope="col">Qualité observée</th>'
            '<th scope="col">Coût observé</th><th scope="col"><span class="cache">Détails</span></th></tr></thead>'
            '<tbody>' + ''.join(lignes_html) + '</tbody></table></div>')


def bilan(titre, sous_titre, exemples_html, signaux_html='', *, ident='t-bilan'):
    """Bilan d'un benchmark : titre, un bloc par exemple (tampons comptés), puis les signaux de limites"""
    return ('<section aria-labelledby="' + ident + '"><div class="zone-titre"><span class="etiq">Bilan</span><h2 id="' + ident + '">'
            + text(titre) + '</h2><span class="petit">' + text(sous_titre) + '</span></div>'
            + ('<div class="bilan">' + exemples_html + '</div>' if exemples_html else '')
            + ('<ul class="signaux" aria-label="Limites de ce benchmark">' + signaux_html + '</ul>' if signaux_html else '') + '</section>')


def bilan_exemple(titre, nombre_html, tampons_html):
    """Carte d'un exemple dans le bilan : titre, nombre de réponses, tampons comptés"""
    return '<div class="bilan-ex"><h3>' + text(titre) + nombre_html + '</h3><div class="compte">' + tampons_html + '</div></div>'


# ——— Lot besoin ———
def bulle_vous(texte, qui='Votre besoin'):
    """Message de la personne dans la conversation avec l'assistant"""
    return ('<div class="bulle bulle-vous"><div><p class="bulle-qui">' + text(qui) + '</p><div class="bulle-texte">' + text(texte)
            + '</div></div><span class="avatar" aria-hidden="true">VOUS</span></div>')


def bulle_assistant(texte, qui='L’assistant écrit'):
    """Message de l'assistant dans la conversation"""
    return ('<div class="bulle bulle-question"><span class="avatar" aria-hidden="true">EV</span><div><p class="bulle-qui">'
            + text(qui) + '</p><div class="bulle-texte">' + text(texte) + '</div></div></div>')


def notice_ligne(texte, icone='alerte'):
    """Ligne d'avertissement au-dessus de la conversation"""
    return '<p class="etat-ligne gb-notice">' + ic(icone) + '<span>' + text(texte) + '</span></p>'


def fil(*liens):
    """Fil de retour : (adresse, libellé) ; le premier lien porte la flèche de retour"""
    items = ''.join('<li>' + lien(href, libelle, 'retour' if index == 0 else None) + '</li>' for index, (href, libelle) in enumerate(liens))
    return '<nav class="g-fil" aria-label="Retour"><ul>' + items + '</ul></nav>'


def segments_liens(legende, ident, options, choisi, cible):
    """Filtre appliqué au clic : chaque option est un lien vers la liste filtrée, sans script ; `options` : (valeur, libellé)"""
    liens = ''.join('<a href="' + text(cible(valeur)) + '"' + (' aria-current="true"' if valeur == choisi else '')
                    + (' title="' + text(libelle) + '"' if len(libelle) > 32 else '') + '>' + text(libelle) + '</a>'
                    for valeur, libelle in options)
    return ('<div class="g-seg" role="group" aria-labelledby="' + text(ident) + '"><p class="g-seg-titre" id="' + text(ident) + '">'
            + text(legende) + '</p><div class="segments segments-liens">' + liens + '</div></div>')


def criteres_actifs(items, effacer):
    """Critères de recherche actifs en puces, avec le lien qui les efface ; vide sans critère"""
    if not items:
        return ''
    puces = ''.join('<li>' + puce(item, plein=True) + '</li>' for item in items)
    return '<div class="g-actifs"><ul aria-label="Critères actifs">' + puces + '</ul>' + lien(effacer, 'Effacer la recherche et les filtres', 'croix') + '</div>'


def feuille(nom, description, texte, ident):
    """Pièce de l'exemple : feuille repliée, texte inerte"""
    return ('<li class="feuille">' + ruban('Pièce inventée') + '<details id="' + text(ident) + '"><summary><span class="nom">' + ic('doc')
            + ' ' + text(nom) + '</span><span>' + text(description) + '</span><span class="lignes" aria-hidden="true"><i></i><i></i><i></i><i></i></span>'
            '<span class="ouvrir">Lire la pièce ' + ic('fleche') + '</span></summary><pre class="example-text">' + text(texte) + '</pre></details></li>')


def consigne(texte, etiquette='Consigne donnée aux modèles', ident=None):
    """Consigne donnée aux modèles, dans son cadre à ruban"""
    return ('<section class="pile"' + (' id="' + text(ident) + '"' if ident else '') + '><div class="consigne">' + ruban(etiquette)
            + '<blockquote>' + text(texte) + '</blockquote></div></section>')


def critere_ligne(libelle, genre, interdit=False):
    """Une ligne de critère : icône, libellé, genre (Obligation, Erreur éliminatoire, Qualité)"""
    return ('<li><span class="type' + (' type-ko' if interdit else '') + '" aria-hidden="true">' + ic('interdit' if interdit else 'cible')
            + '</span><span>' + libelle + '</span><span class="genre">' + text(genre) + '</span></li>')
