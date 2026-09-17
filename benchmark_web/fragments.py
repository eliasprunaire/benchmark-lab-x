"""Fragments HTML partagés par les modules de rendu du parcours privé.

Ce module ne connaît ni la préparation ni les campagnes : il n'expose que les
primitives réutilisées par plusieurs vues (échappement, formulaires natifs,
repères visuels, dates, montants, preuves inertes). Il n'importe aucun module
de rendu, pour qu'aucun cycle ne soit possible.
"""
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
    return ('<div class="state ' + tone + '" role="status"><span class="ic">' + icon(names[tone]) + '</span>'
            '<p class="eyebrow">' + escape(eyebrow, quote=True) + '</p><h2>' + escape(heading, quote=True) + '</h2>'
            + body + (('<div class="actions">' + actions + '</div>') if actions else '') + '</div>')


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


def hidden(name, value):
    return f'<input type="hidden" name="{text(name)}" value="{text(value)}">'


def form(csrf, url, fields, content):
    return (f'<form method="post" action="{text(url)}">' + hidden('csrf_token', csrf)
            + ''.join(hidden(k, v) for k, v in fields.items()) + content + '</form>')


def section(title, content, anchor=None):
    target = '' if anchor is None else f' id="{text(anchor)}"'
    return f'<section{target}><h2>{text(title)}</h2>{content}</section>'


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
