"""Motif affiché d'un verdict, calculé depuis le verdict, les constats et le contrat.

Le motif enregistré (`reason`) reste le texte d'archive, que l'audit de démarrage recalcule et
compare. L'affichage ne le relit pas : il reconstruit le texte à la lecture, comme le motif court
d'un « NE SATISFAIT PAS ». La fonction insère elle-même les descriptions ; aucun remplacement
d'identifiants ne doit suivre.
"""
import re

from .evaluation import defects

PROVEN = 'Résultat et obligations prouvés : '
INSUFFICIENT = 'Preuve insuffisante ou contradictoire : '
AVOIDED = 'Erreur éliminatoire évitée : '
AVOIDED_MANY = 'Erreurs éliminatoires évitées : '


def _description(value):
    """Description insérée dans une phrase : sans son point final ; « ? », « ! » et « … » restent"""
    return value.removesuffix('.')


def _labels(spec):
    return {item['id']: _description(item['description'])
            for criterion in spec['obligations'] + spec['eliminatory_errors']
            for item in [criterion] + criterion.get('elements', [])}


def _inserted(value, labels):
    """Remplace les identifiants cités par leur description, sans point final"""
    pattern = r'(?<!\w)(' + '|'.join(map(re.escape, labels)) + r')(?!\w)'
    return re.sub(pattern, lambda match: labels[match[0]], value)


def _point(phrase):
    """Un seul signe final : aucun point après le « ? », le « ! » ou le « … » d'une description"""
    return phrase if phrase.endswith(('.', '?', '!', '…')) else phrase + '.'


def motif(record):
    """Texte affiché du motif : verdict, constats et contrat suffisent, le motif stocké n'est pas lu

    `record` porte `verdict`, `findings`, `output_piece_id` et
    `qualification.contract.specification`.
    """
    spec = record['qualification']['contract']['specification']
    labels = _labels(spec)
    findings = record['findings']
    output = record['output_piece_id']
    if record['verdict'] == 'NE SATISFAIT PAS':
        # Une espace entre deux constats : chacun est une phrase du juge avec sa propre ponctuation
        return ' '.join(labels[f['criterion_id']] + ' : ' + _inserted(f['finding'], labels)
                        for f in defects(findings, output))
    if record['verdict'] == 'SATISFAIT':
        eliminatory = {c['id'] for c in spec['eliminatory_errors']}
        proven = list(dict.fromkeys(f['criterion_id'] for f in findings if f['criterion_id'] not in eliminatory))
        avoided = list(dict.fromkeys(f['criterion_id'] for f in findings if f['criterion_id'] in eliminatory))
        text = _point(PROVEN + ' ; '.join(labels[cid] for cid in proven)) if proven else 'Résultat et obligations prouvés.'
        if avoided:
            text += ' ' + (AVOIDED if len(avoided) == 1 else AVOIDED_MANY) + _point(
                ' ; '.join(labels[cid] for cid in avoided))
        return text
    reason = record['reason']
    if not reason.startswith(INSUFFICIENT):
        # Phrases fixes du moteur : seul le point final manque
        return _point(_inserted(reason, labels))
    insufficient = list(dict.fromkeys(
        f['criterion_id'] for f in findings if f['status'] != 'PASS'
        or f['attribution'] not in ('candidate', 'evidence')
        or not any(proof['piece_id'] == output for proof in f['evidence'])))
    return _point(INSUFFICIENT + ' ; '.join(labels[cid] for cid in insufficient))
