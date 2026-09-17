"""Validateurs purs partagés : identifiants, empreintes et listes de textes.

Aucun flux privé, aucun stockage ouvert ; seuls les prédicats de forme vivent ici.
"""
from hashlib import sha256
import re

from .storage import _text, _strict_json as encode


def digest(value):
    return sha256(encode(value).encode('utf-8')).hexdigest()


def identifier(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is None:
        raise ValueError('Identifiant invalide')
    return value


def _hash(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('Empreinte invalide')


def _texts(values, label, *, required=False, unique=False):
    if type(values) is not list or (required and not values):
        raise ValueError('Liste requise : ' + label)
    for value in values:
        _text(value, label)
        if not value.strip():
            raise ValueError('Texte vide : ' + label)
    if unique and len(values) != len(set(values)):
        raise ValueError('Identité répétée : ' + label)
