"""Étalonnage du jugement : décisions conservées du juge comparées à des annotations humaines d'un lot réservé

Lecture seule : aucun appel, aucun reçu écrit. Chaque décision vient d'un reçu de jugement déjà conservé,
lu comme le contrôle des témoins (`preparation._witness_decisions`). Le rapport donne des comptes et des
exemples, jamais un seuil ni une garantie
"""
from datetime import date
import json
from typing import NamedTuple

from . import judgment, preparation, storage
from .storage import ConflictError, IntegrityError, _fields, _text, _unique_object
from .validation import digest, identifier

FORMAT = 'benchmark-lab-x/calibration-batch/v1'
REPORT_FORMAT = 'benchmark-lab-x/calibration-report/v1'
STATUSES = ('PASS', 'FAIL', 'INDETERMINE')
TREATMENTS = ('exclude', 'arbitrated')
CATEGORIES = ('accord', 'faux_rejet', 'acceptation_erronee', 'indetermine', 'decision_sur_reference_indeterminee')
LIMITS = (
    'Un accord entre le juge et les annotations sur ce lot ne garantit pas la qualité métier de la méthode.',
    'Les comptes valent pour ce lot et cette configuration de juge ; aucune extrapolation à un autre modèle, '
    'une autre tâche ou une autre version de méthode.',
    'Aucun seuil n’est appliqué : ce rapport ne conclut pas que la méthode suffit.',
    'La règle de désaccord est celle que le lot déclare ; ce rapport ne la décide pas.',
    'L’empreinte du lot réservé ne prouve pas l’antériorité de la séparation ; seule une preuve datée la fonde.',
    'La provenance du juge est déclarée par le lot et non vérifiée ; des reçus simulés ne qualifient pas un modèle réel.',
)
# Reçu de jugement absent ou clos avant tout effet : aucune décision à comparer
_NO_RECEIPT = ('RECONCILIATION_REQUIRED', 'EXECUTION_REQUIRED', 'NOT_SENT', storage.AMBIGUOUS_EXPIRED)


class _Excluded(Exception):
    """Item que son reçu ne permet pas de comparer : code lisible par machine et détail"""


class _Read(NamedTuple):
    identity: dict
    criteria: dict[str, str]
    decided: dict[str, dict]


def reserved_sha256(control):
    """Empreinte à déclarer dans le lot : la partition de contrôle avec ses annotations"""
    return digest(control)


def _annotation(note):
    _fields(note, ('author', 'control_id', 'status', 'justification'), 'annotation')
    for key in ('author', 'control_id', 'justification'):
        _text(note[key], key)
    if note['status'] not in STATUSES:
        raise ValueError('État d’annotation inconnu')


def _arbitration(note):
    _fields(note, ('control_id', 'arbiter', 'status', 'justification'), 'arbitrage')
    for key in ('control_id', 'arbiter', 'justification'):
        _text(note[key], key)
    if note['status'] not in STATUSES:
        raise ValueError('État d’arbitrage inconnu')


def _batch(batch):
    _fields(batch, ('format', 'batch_id', 'judge_provenance', 'disagreement_rule', 'separation',
                    'correction', 'control', 'reserved_sha256'), 'lot d’étalonnage')
    if batch['format'] != FORMAT:
        raise ValueError('Format de lot inconnu')
    identifier(batch['batch_id'])
    provenance = batch['judge_provenance']
    _fields(provenance, ('kind', 'authority'), 'provenance du juge')
    if provenance['kind'] not in ('simulated', 'authorized-real'):
        raise ValueError('Provenance du juge inconnue')
    if provenance['kind'] == 'authorized-real':
        _text(provenance['authority'], 'authority')
    rule = batch['disagreement_rule']
    _fields(rule, ('origin', 'treatment', 'min_annotators'), 'règle de désaccord')
    _text(rule['origin'], 'origin')
    if rule['treatment'] not in TREATMENTS or type(rule['min_annotators']) is not int or rule['min_annotators'] < 1:
        raise ValueError('Règle de désaccord invalide')
    if batch['separation'] is not None:
        _fields(batch['separation'], ('source', 'dated'), 'preuve de séparation')
        _text(batch['separation']['source'], 'source')
        date.fromisoformat(batch['separation']['dated'])
    if type(batch['correction']) is not list or type(batch['control']) is not list:
        raise ValueError('Partitions correction et contrôle requises')
    for dossier in batch['correction']:
        identifier(dossier)
    seen = set()
    for item in batch['control']:
        _fields(item, ('operation_id', 'dossier_id', 'output_sha256', 'annotations', 'arbitrations'), 'item du lot')
        identifier(item['operation_id'])
        identifier(item['dossier_id'])
        if item['operation_id'] in seen:
            raise ValueError('Opération répétée dans le lot')
        seen.add(item['operation_id'])
        if type(item['annotations']) is not list or type(item['arbitrations']) is not list:
            raise ValueError('Annotations et arbitrages en liste')
        for note in item['annotations']:
            _annotation(note)
        for note in item['arbitrations']:
            _arbitration(note)
        if len({(n['author'], n['control_id']) for n in item['annotations']}) != len(item['annotations']):
            raise ValueError('Un auteur annote un contrôle une seule fois')
        if len({n['control_id'] for n in item['arbitrations']}) != len(item['arbitrations']):
            raise ValueError('Un seul arbitrage par contrôle')
    if digest(batch['control']) != batch['reserved_sha256']:
        raise IntegrityError('Lot réservé divergent de son empreinte déclarée')


def _read(store, item) -> _Read:
    """Décision conservée du juge pour un item ; `_Excluded` si le reçu ne permet pas de comparer"""
    try:
        view = judgment.inspect(store, item['operation_id'])
    except (ValueError, KeyError, ConflictError):
        raise _Excluded('OPERATION_INCONNUE', 'Opération absente ou qui n’est pas un jugement lisible') from None
    operation = view['operation']
    review = json.loads(operation['resources'][0], object_pairs_hook=_unique_object)['content']
    if (operation['dossier_id'] != item['dossier_id'] or review['output'] is None
            or review['output']['sha256'] != item['output_sha256']):
        raise _Excluded('LIAISON_DIVERGENTE', 'Dossier ou sortie du lot distincts de ceux du jugement conservé')
    state = view['diagnostic']['state']
    if operation['receipt'] is None or state in _NO_RECEIPT:
        raise _Excluded('SANS_RECU', 'Aucune décision conservée : ' + state)
    config = operation['requested_configuration']
    identity = dict(engine_version=operation['engine_version'],
                    method=dict(id=review['method']['id'], version=review['method']['version']),
                    **{key: config.get(key) for key in ('profile_id', 'profile_sha256', 'model', 'revision',
                                                        'prompt_sha256', 'evidence_rule')})
    criteria = {control: row['id'] for row in review['obligations'] + review['eliminatory_errors']
                for control in row['control_ids']}
    proposal = view['proposal']
    unusable = None if proposal is not None else state
    decided, findings = {}, {}
    try:
        if proposal is not None:
            answer = dict(findings=proposal['report']['findings'], measures=[], limits=[],
                          proposed_verdict=proposal['proposed_verdict'])
            decided = preparation._witness_decisions(
                answer, review, omission_allowed=config.get('evidence_rule') == judgment.EVIDENCE_OMISSION_BINDING)
            for row in proposal['report']['findings']:
                findings.setdefault(row['control_id'], []).append(row['finding'])
    except (ValueError, TypeError, KeyError):
        proposal, unusable = None, 'PROPOSITION_ILLISIBLE'
    if proposal is None:
        decided = {control: 'INDETERMINE' for control in criteria}
    return _Read(identity, criteria, {
        control: dict(status=status, findings=findings.get(control, []), unusable=unusable)
        for control, status in decided.items()})


def _reference(item, control, rule):
    """`(état, résolution)` de la référence d'un contrôle, ou `(None, code)` quand elle n’existe pas"""
    notes = [n for n in item['annotations'] if n['control_id'] == control]
    if not notes:
        return None, 'NON_ANNOTE'
    if len(notes) < rule['min_annotators']:
        return None, 'SOUS_ANNOTE'
    if len({n['status'] for n in notes}) == 1:
        return notes[0]['status'], 'accord'
    arbitration = next((n for n in item['arbitrations'] if n['control_id'] == control), None)
    if rule['treatment'] == 'arbitrated' and arbitration is not None:
        return arbitration['status'], 'arbitrated'
    return None, 'DESACCORD_NON_ARBITRE'


def _category(reference, judged):
    if reference == judged:
        return 'accord'
    if (reference, judged) == ('PASS', 'FAIL'):
        return 'faux_rejet'
    if (reference, judged) == ('FAIL', 'PASS'):
        return 'acceptation_erronee'
    return 'indetermine' if judged == 'INDETERMINE' else 'decision_sur_reference_indeterminee'


def report(store, batch):
    """Rapport d'étalonnage d'un lot annoté, lu sur les reçus de jugement conservés de `store`"""
    _batch(batch)
    rule = batch['disagreement_rule']
    reserved = set(batch['correction'])
    excluded, disagreements, groups, measured_items, pairs = [], [], {}, set(), 0
    contamination = sorted({item['dossier_id'] for item in batch['control'] if item['dossier_id'] in reserved})
    for item in batch['control']:
        where = dict(operation_id=item['operation_id'], dossier_id=item['dossier_id'])
        if item['dossier_id'] in reserved:
            excluded.append(dict(where, control_id=None, reason='CONTAMINATION',
                                 detail='Dossier présent dans la partition de correction'))
            continue
        try:
            read = _read(store, item)
        except _Excluded as error:
            excluded.append(dict(where, control_id=None, reason=error.args[0], detail=error.args[1]))
            continue
        group = groups.setdefault(digest(read.identity), dict(judge=read.identity, items=0, totals=dict.fromkeys(CATEGORIES, 0),
                                                              requirements={}))
        group['items'] += 1
        for control, criterion in read.criteria.items():
            row = group['requirements'].setdefault(criterion, dict(criterion_id=criterion, control_ids=[], measured_pairs=0,
                counts=dict.fromkeys(CATEGORIES, 0), examples={name: [] for name in CATEGORIES[1:]}))
            if control not in row['control_ids']:
                row['control_ids'].append(control)
        unknown = {n['control_id'] for n in item['annotations'] + item['arbitrations']} - set(read.criteria)
        if unknown:
            excluded.append(dict(where, control_id=None, reason='ANNOTATION_HORS_CONTROLES',
                                 detail='Contrôles inconnus du jugement : ' + ', '.join(sorted(unknown))))
            continue
        for control, criterion in read.criteria.items():
            status, resolution = _reference(item, control, rule)
            notes = [dict(author=n['author'], status=n['status'], justification=n['justification'])
                     for n in item['annotations'] if n['control_id'] == control]
            arbitration = next((dict(author=n['arbiter'], status=n['status'], justification=n['justification'])
                                for n in item['arbitrations'] if n['control_id'] == control), None)
            if len({n['status'] for n in notes}) > 1:
                disagreements.append(dict(where, control_id=control, annotations=notes, arbitration=arbitration,
                                          outcome='ARBITRE' if resolution == 'arbitrated' else 'EXCLU'))
            if status is None:
                excluded.append(dict(where, control_id=control, reason=resolution,
                                     detail='Référence absente pour ce contrôle'))
                continue
            judged = read.decided[control]
            category = _category(status, judged['status'])
            row = group['requirements'][criterion]
            row['measured_pairs'] += 1
            row['counts'][category] += 1
            group['totals'][category] += 1
            pairs += 1
            measured_items.add(item['operation_id'])
            if category != 'accord':
                row['examples'][category].append(dict(where, output_sha256=item['output_sha256'], control_id=control,
                    reference=dict(status=status, resolution=resolution, annotations=notes, arbitration=arbitration),
                    judge=dict(status=judged['status'], findings=judged['findings'], unusable=judged['unusable'])))
    annotators = sorted({n['author'] for item in batch['control'] for n in item['annotations']})
    anteriority = 'NON_ETABLIE' if batch['separation'] is None else 'DECLAREE'
    return dict(format=REPORT_FORMAT, batch_id=batch['batch_id'], batch_sha256=digest(batch),
        judge_provenance=batch['judge_provenance'],
        reference=dict(annotators=annotators, disagreement_rule=rule,
                       annotations_sha256=digest([item['annotations'] + item['arbitrations'] for item in batch['control']])),
        separation=dict(reserved_sha256=batch['reserved_sha256'], anteriority=anteriority, proof=batch['separation']),
        coverage=dict(correction_dossiers=len(reserved), control_items=len(batch['control']),
                      measured_items=len(measured_items), measured_pairs=pairs, excluded=excluded),
        contamination=contamination,
        judges=[dict(group, requirements=sorted(group['requirements'].values(), key=lambda row: row['criterion_id']))
                for group in groups.values()],
        disagreements=disagreements, limits=list(LIMITS))
