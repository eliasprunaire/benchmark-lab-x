"""Fragments HTML partagés par les modules de rendu du parcours privé.

Ce module ne connaît ni la préparation ni les campagnes : il n'expose que les
primitives réutilisées par plusieurs vues (échappement, formulaires natifs,
repères visuels, dates, montants, preuves inertes). Il n'importe aucun module
de rendu, pour qu'aucun cycle ne soit possible.
"""
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape

MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin',
        'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')

VERDICT_BADGES = {'SATISFAIT': ('b-ok', 'i-check', 'Satisfait'), 'NE SATISFAIT PAS': ('b-ko', 'i-cross', 'Ne satisfait pas'),
                  None: ('b-ind', 'i-help', 'À reprendre')}


def text(value):
    """Valeur échappée pour le texte comme pour les attributs"""
    return escape(str(value), quote=True)


def icon(name):
    return '<svg class="ico" aria-hidden="true"><use href="#' + name + '"/></svg>'


def badge(verdict):
    """Verdict d'une tentative sous forme de badge lisible sans couleur"""
    tone, name, label = VERDICT_BADGES.get(verdict, ('b-unk', 'i-help', str(verdict)))
    return '<span class="badge ' + tone + '">' + icon(name) + escape(label, quote=True) + '</span>'


def state_block(tone, eyebrow, heading, body, actions=''):
    """Bloc « Où j'en suis » : une icône, une ligne d'état, la prochaine action"""
    names = {'action': 'i-pen', 'wait': 'i-clock', 'warn': 'i-alert', 'err': 'i-alert', 'done': 'i-check', 'unk': 'i-help'}
    return ('<div class="state ' + tone + '"><span class="ic">' + icon(names[tone]) + '</span>'
            '<p class="eyebrow">' + escape(eyebrow, quote=True) + '</p><h2>' + escape(heading, quote=True) + '</h2>'
            + body + (('<div class="actions">' + actions + '</div>') if actions else '') + '</div>')


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


def form(csrf, url, fields, content, *, form_id=None):
    identity = '' if form_id is None else f' id="{text(form_id)}"'
    return (f'<form{identity} method="post" action="{text(url)}">' + hidden('csrf_token', csrf)
            + ''.join(hidden(k, v) for k, v in fields.items()) + content + '</form>')


def section(title, content, anchor=None):
    target = '' if anchor is None else f' id="{text(anchor)}"'
    return f'<section{target}><h2>{text(title)}</h2>{content}</section>'


def workbench(main, side, label, *, side_first=False):
    """Zone principale et colonne de synthèse ; `side_first` quand la synthèse doit se lire avant le détail"""
    aside = '<aside class="wb-side" aria-label="' + text(label) + '">' + side + '</aside>'
    body = '<div class="wb-main">' + main + '</div>'
    return ('<div class="workbench' + (' side-first' if side_first else '') + '">'
            + (aside + body if side_first else body + aside) + '</div>')


def readout(label, value):
    """Relevé en grand d'un chiffre déjà connu, toujours nommé"""
    return '<p class="readout"><span>' + text(label) + '</span><strong>' + text(value) + '</strong></p>'


def listing(values):
    return '<ul>' + ''.join(f'<li>{text(v)}</li>' for v in values) + '</ul>'


def readable_fields(value):
    """Present structured evidence as inert, labelled fields"""
    labels = {'requested_configuration': 'Configuration demandée', 'observed_configuration': 'Configuration observée',
              'observation_sources': 'Sources des observations', 'model': 'Modèle', 'provider': 'Fournisseur',
              'revision': 'Révision', 'access': 'Accès', 'channel_id': 'Canal', 'route': 'Route',
              'effort': 'Effort de raisonnement', 'parameters': 'Paramètres', 'max_tokens': 'Limite de tokens',
              'temperature': 'Température', 'source': 'Source', 'unit': 'Unité', 'value': 'Valeur',
              'measure': 'Mesure', 'proof': 'Preuve', 'favorable': 'Sens favorable', 'aggregation': 'Agrégation',
              'scope': 'Périmètre', 'attempts': 'Tentatives', 'conversion': 'Conversion', 'frozen_at': 'Date de gel',
              'environment': 'Environnement', 'package': 'Paquet', 'version': 'Version', 'status': 'État',
              'criteria': 'Critères', 'obligations': 'Obligations',
              'eliminatory_errors': 'Erreurs éliminatoires', 'result_expected': 'Résultat attendu',
              'panel': 'Modèles comparés', 'limits': 'Limites', 'pi': 'Harnais Pi',
              'conditions': 'Conditions'}
    if isinstance(value, dict):
        return ('<dl class="evidence-fields">' + ''.join(
            '<dt>' + text(labels.get(key, key.replace('_', ' '))) + '</dt><dd>' + readable_fields(item) + '</dd>'
            for key, item in value.items()) + '</dl>') if value else '<span>Non renseigné</span>'
    if isinstance(value, list):
        return ('<ul>' + ''.join('<li>' + readable_fields(item) + '</li>' for item in value) + '</ul>') if value else '<span>Aucun élément déclaré</span>'
    if value is None:
        value = 'Non renseigné'
    elif type(value) is bool:
        value = 'Oui' if value else 'Non'
    return '<span class="verbatim">' + escape(str(value), quote=True) + '</span>'


ACCESS_REASONS = {
    'ACCESS_CAP_REQUIRED': 'elle doit avoir un plafond non renouvelable de 50 USD maximum et un solde disponible',
    'KEY_REJECTED': 'OpenRouter refuse cette clé',
    'SESSION_EXPIRED': 'votre session dans ce navigateur a expiré',
}


def access_summary(access):
    """Solde et plafond annoncés par Openrouter, jamais la clé"""
    def amount(key):
        return 'inconnu' if access.get(key) is None else montant_lisible(access[key])
    return 'Solde restant selon OpenRouter : ' + text(amount('limit_remaining_usd')) + ' USD. Plafond de la clé : ' + text(amount('limit_usd')) + ' USD.'


def personal_key_form(csrf, access, back, *, opened=False):
    """Seul moyen de fournir un accès Openrouter : la clé saisie, puis retour à `back`"""
    status = access.get('status')
    summary = 'Remplacer ma clé OpenRouter' if status == 'connected' else 'Ajouter ma clé OpenRouter'
    content = ('<details class="corr personal-key"' + (' open' if opened else '') + '><summary class="button sec">'
               + summary + '</summary><div>')
    content += form(csrf, '/preparation/access/key', {'return': back},
        '<label for="openrouter-key">Clé API OpenRouter</label>'
        '<input id="openrouter-key" name="key" type="password" autocomplete="new-password" required maxlength="512" aria-describedby="key-help key-storage">'
        '<p id="key-help">Créez sur OpenRouter une clé réservée à Bench-X, avec un plafond non renouvelable de 50 USD maximum. Ce plafond est la seule limite de dépense.</p>'
        '<p id="key-storage" class="hint">Votre clé est chiffrée et conservée sur notre serveur. '
        'Un cookie garde votre accès dans ce navigateur d’une visite à l’autre ; il est supprimé au plus tard après 30 jours sans activité. '
        'Vous pouvez aussi retirer la clé ici à tout moment. '
        'Enregistrer ou retirer la clé ne lance aucun modèle.</p>'
        '<button type="submit">Enregistrer la clé</button>')
    content += '</div></details>'
    if status in ('connected', 'invalid'):
        content += form(csrf, '/preparation/access/disconnect', {'return': back},
            '<button type="submit" class="sec">Retirer la clé de ce navigateur</button>')
        content += ('<p class="hint">Attendez la fin d’une préparation ou d’une vérification en cours avant de changer de clé. '
                    'Une fois la clé retirée, Bench-X ne lance plus aucun nouvel appel. La clé reste active chez OpenRouter, et une comparaison déjà lancée n’est pas annulée.</p>')
    return content


_TABLE_TAG = re.compile(r'<(/?)(table|thead|tbody|tr|th|td)\b([^>]*)>')


def lisible_en_liste(html):
    """Rôles explicites et en-tête par cellule, posés une fois pour tous les tableaux de la page

    Sur téléphone, la CSS affiche chaque ligne en liste ; les rôles gardent le sens de tableau
    pour les lecteurs d'écran, et `data-entete` redonne l'en-tête de colonne à chaque cellule
    """
    out, pos, tables = [], 0, []
    for tag in _TABLE_TAG.finditer(html):
        out.append(html[pos:tag.start()])
        pos = tag.end()
        closing, name, attrs = tag[1], tag[2], tag[3]
        if closing:
            if name == 'table' and tables:
                tables.pop()
            elif name == 'tr' and tables:
                tables[-1]['col'] = 0
            elif name == 'th' and tables and tables[-1]['head_start'] is not None:
                # Le texte de l'en-tête sert d'étiquette aux cellules de sa colonne, balises retirées
                tables[-1]['heads'].append(re.sub(r'<[^>]+>', '', html[tables[-1]['head_start']:tag.start()]).strip())
                tables[-1]['head_start'] = None
            out.append(tag[0])
            continue
        if name == 'table':
            tables.append({'heads': [], 'col': 0, 'in_head': False, 'head_start': None})
            role = 'table'
        elif name in ('thead', 'tbody'):
            if tables:
                tables[-1]['in_head'] = name == 'thead'
            role = 'rowgroup'
        elif name == 'tr':
            role = 'row'
        else:
            current = tables[-1] if tables else None
            extra = ''
            if name == 'th' and current and current['in_head']:
                role = 'columnheader'
                current['head_start'] = tag.end()
            else:
                role = 'rowheader' if name == 'th' else 'cell'
                heads = current['heads'] if current else []
                if current and current['col'] < len(heads) and heads[current['col']]:
                    extra = ' data-entete="' + heads[current['col']] + '"'
            if current:
                current['col'] += 1
            out.append('<' + name + attrs + ' role="' + role + '"' + extra + '>')
            continue
        out.append('<' + name + attrs + ' role="' + role + '"' + '>')
    out.append(html[pos:])
    return ''.join(out)


def chain(items, classes='', end=''):
    """Éléments déjà échappés d'une chaîne « A · B · C » : liste `ul.meta` au-delà du seuil, sinon paragraphe

    Le seuil est de deux séparateurs, d'un seul pour un bilan (`note`, `hint`, `synthesis`) ;
    `end` est la ponctuation finale, rattachée au dernier élément
    """
    needed = 1 if {'note', 'hint', 'synthesis'} & set(classes.split()) else 2
    items = [*items[:-1], items[-1] + end]
    if len(items) - 1 < needed:
        return '<p' + (' class="' + classes + '"' if classes else '') + '>' + ' · '.join(items) + '</p>'
    return '<ul class="meta' + (' ' + classes if classes else '') + '">' + ''.join('<li>' + item + '</li>' for item in items) + '</ul>'


def single_choice(field, name, label, options, selected, *, form='', describedby='', segments=True):
    """Choix unique : boutons radio de même nom pour 2 à 4 options courtes, sinon menu natif

    `options` : couples (valeur, intitulé). L'option choisie est cochée ; sans correspondance, la première.
    """
    values = [value for value, _ in options]
    attrs = (' form="' + text(form) + '"' if form else '')
    if segments and 2 <= len(options) <= 4 and sum(len(title) for _, title in options) <= 64:
        chosen = selected if selected in values else values[0]
        return ('<fieldset class="segments"' + (' aria-describedby="' + text(describedby) + '"' if describedby else '')
                + '><legend>' + text(label) + '</legend>' + ''.join(
                    '<label><input type="radio" name="' + text(name) + '" value="' + text(value) + '"' + attrs
                    + (' checked' if value == chosen else '') + '> ' + text(title) + '</label>' for value, title in options)
                + '</fieldset>')
    return ('<label for="' + text(field) + '">' + text(label) + '</label><select id="' + text(field) + '" name="' + text(name) + '"'
            + attrs + (' aria-describedby="' + text(describedby) + '"' if describedby else '') + '>'
            + ''.join('<option value="' + text(value) + '"' + (' selected' if value == selected else '') + '>' + text(title)
                      + '</option>' for value, title in options) + '</select>')
