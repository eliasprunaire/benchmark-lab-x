"""Étalonnage du jugement : décisions conservées du juge comparées à des annotations humaines d'un lot réservé

Lecture seule : aucun appel, aucun reçu écrit. Chaque décision vient d'un reçu de jugement déjà conservé,
lu comme le contrôle des témoins (`preparation._witness_decisions`). Le rapport donne des comptes et des
exemples, jamais un seuil ni une garantie
"""
from datetime import date
import json
from pathlib import Path
from typing import NamedTuple

from . import judgment, preparation, storage
from .storage import ConflictError, IntegrityError, _fields, _text, _unique_object
from .validation import _hash, _texts, digest, identifier

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
DECISION_FORMAT = 'benchmark-lab-x/calibration-decision/v1'
DECISION_STATUSES = ('QUALIFIED', 'NOT_QUALIFIED')
# Fiches déclarées par l'opérateur : une décision datée par fichier, aucune écriture par le produit
DECISIONS_DIR = Path(__file__).resolve().parent / 'calibration_decisions'
_IDENTITY_KEYS = ('engine_version', 'method', 'profile_id', 'profile_sha256', 'model', 'revision', 'prompt_sha256',
                  'evidence_rule', 'observed')
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
    _fields(rule, ('origin', 'treatment', 'min_annotators', 'synthetic'), 'règle de désaccord')
    _text(rule['origin'], 'origin')
    if type(rule['synthetic']) is not bool:
        raise ValueError('Nature synthétique de la règle à déclarer')
    if provenance['kind'] == 'authorized-real' and rule['synthetic']:
        raise ValueError('Une règle synthétique ne décide pas les désaccords d’un lot réel')
    if rule['treatment'] not in TREATMENTS or type(rule['min_annotators']) is not int or rule['min_annotators'] < 1:
        raise ValueError('Règle de désaccord invalide')
    if batch['separation'] is not None:
        _fields(batch['separation'], ('source', 'dated'), 'preuve de séparation')
        for key in ('source', 'dated'):
            _text(batch['separation'][key], key)
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


def _review(operation):
    return json.loads(operation['resources'][0], object_pairs_hook=_unique_object)['content']


def judge_identity(operation, review=None):
    """Identité d'un juge : version de méthode, configuration demandée et servie ; une fiche ne vaut que pour elle"""
    review = _review(operation) if review is None else review
    config = operation['requested_configuration']
    observed = (operation['receipt'] or {}).get('observed_configuration') or {}
    # Configuration observée dans l'identité : deux routes réellement servies ne se mélangent pas
    return dict(engine_version=operation['engine_version'],
                method=dict(id=review['method']['id'], version=review['method']['version']),
                **{key: config.get(key) for key in ('profile_id', 'profile_sha256', 'model', 'revision',
                                                    'prompt_sha256', 'evidence_rule')},
                observed=dict(model=observed.get('model'), provider=observed.get('provider')))


def _decision(decision):
    _fields(decision, ('format', 'decision_id', 'decided_at', 'authority', 'status', 'judge_identity', 'batch',
                       'perimeter', 'limits'), 'décision d’étalonnage')
    if decision['format'] != DECISION_FORMAT:
        raise ValueError('Format de décision inconnu')
    identifier(decision['decision_id'])
    _text(decision['decided_at'], 'decided_at')
    # Date canonique AAAA-MM-JJ : une forme que `fromisoformat` accepte sans être lisible ensuite est refusée
    if date.fromisoformat(decision['decided_at']).isoformat() != decision['decided_at']:
        raise ValueError('Date de décision attendue sous la forme AAAA-MM-JJ')
    for key in ('authority', 'perimeter'):
        _text(decision[key], key)
    if decision['status'] not in DECISION_STATUSES:
        raise ValueError('État de décision inconnu')
    identity = decision['judge_identity']
    _fields(identity, _IDENTITY_KEYS, 'identité du juge')
    _fields(identity['method'], ('id', 'version'), 'méthode')
    _fields(identity['observed'], ('model', 'provider'), 'configuration observée')
    _fields(decision['batch'], ('batch_id', 'batch_sha256'), 'lot de la décision')
    identifier(decision['batch']['batch_id'])
    _hash(decision['batch']['batch_sha256'])
    _texts(decision['limits'], 'limits', required=True)


def load_decisions():
    """Décisions déclarées dans `DECISIONS_DIR`, validées ; une fiche mal formée refuse la lecture"""
    decisions = []
    for path in sorted(DECISIONS_DIR.glob('*.json')) if DECISIONS_DIR.is_dir() else []:
        decision = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
        _decision(decision)
        decisions.append(decision)
    if len({d['decision_id'] for d in decisions}) != len(decisions):
        raise ValueError('Décision répétée')
    # Aucune règle de remplacement : deux fiches pour la même identité de juge sont ambiguës
    if len({digest(d['judge_identity']) for d in decisions}) != len(decisions):
        raise ValueError('Plusieurs décisions pour la même identité de juge')
    return decisions


def scope(operation):
    """Portée de l'étalonnage pour le jugement d'un résultat ; lecture seule, rien n'est écrit ni réévalué

    Une fiche s'applique si et seulement si l'identité du juge est identique : une autre version ne l'hérite pas
    """
    if operation is None:
        return dict(state='SANS_JUGE_ASSISTE', decision=None)
    try:
        identity = judge_identity(operation)
    except (ValueError, KeyError, TypeError, IndexError):
        return dict(state='IDENTITE_ILLISIBLE', decision=None)
    try:
        decisions = load_decisions()
    except (ValueError, OSError):
        return dict(state='FICHES_ILLISIBLES', decision=None)
    decision = next((d for d in decisions if d['judge_identity'] == identity), None)
    if decision is not None:
        return dict(state='QUALIFIEE' if decision['status'] == 'QUALIFIED' else 'NON_QUALIFIEE', decision=decision)
    other = any(d['judge_identity']['method']['id'] == identity['method']['id'] for d in decisions)
    return dict(state='NON_APPLICABLE' if other else 'SANS_ETALONNAGE', decision=None)


def _read(store, item) -> _Read:
    """Décision conservée du juge pour un item ; `_Excluded` si le reçu ne permet pas de comparer"""
    try:
        view = judgment.inspect(store, item['operation_id'])
    except (ValueError, KeyError, ConflictError):
        raise _Excluded('OPERATION_INCONNUE', 'Opération absente ou qui n’est pas un jugement lisible') from None
    operation = view['operation']
    review = _review(operation)
    if (operation['dossier_id'] != item['dossier_id'] or review['output'] is None
            or review['output']['sha256'] != item['output_sha256']):
        raise _Excluded('LIAISON_DIVERGENTE', 'Dossier ou sortie du lot distincts de ceux du jugement conservé')
    state = view['diagnostic']['state']
    if operation['receipt'] is None or state in _NO_RECEIPT:
        raise _Excluded('SANS_RECU', 'Aucune décision conservée : ' + state)
    config = operation['requested_configuration']
    identity = judge_identity(operation, review)
    criteria = {control: row['id'] for row in review['obligations'] + review['eliminatory_errors']
                for control in row['control_ids']}
    if len(criteria) != sum(len(row['control_ids']) for row in review['obligations'] + review['eliminatory_errors']):
        raise _Excluded('CONTROLE_PARTAGE', 'Un contrôle sert plusieurs critères : lecture par exigence ambiguë')
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


def _reference(notes, arbitration, rule):
    """`(état, résolution)` de la référence d'un contrôle, ou `(None, code)` quand elle n’existe pas"""
    if not notes:
        return None, 'NON_ANNOTE'
    if len(notes) < rule['min_annotators']:
        return None, 'SOUS_ANNOTE'
    if len({n['status'] for n in notes}) == 1:
        return notes[0]['status'], 'accord'
    if rule['treatment'] == 'arbitrated' and arbitration is not None:
        return arbitration['status'], 'arbitrated'
    return None, 'DESACCORD_NON_ARBITRE'


def _category(reference, judged, unusable):
    """Un reçu inexploitable n'a rien décidé : jamais un accord, même face à une référence indéterminée"""
    if unusable is not None:
        return 'indetermine'
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
        for control, criterion in read.criteria.items():
            row = group['requirements'].setdefault(criterion, dict(criterion_id=criterion, control_ids=[], measured_pairs=0, arbitrated_pairs=0,
                counts=dict.fromkeys(CATEGORIES, 0), examples={name: [] for name in CATEGORIES[1:]}))
            if control not in row['control_ids']:
                row['control_ids'].append(control)
        unknown = {n['control_id'] for n in item['annotations'] + item['arbitrations']} - set(read.criteria)
        if unknown:
            excluded.append(dict(where, control_id=None, reason='ANNOTATION_HORS_CONTROLES',
                                 detail='Contrôles inconnus du jugement : ' + ', '.join(sorted(unknown))))
            continue
        notes_by_control, item_pairs = {}, 0
        for n in item['annotations']:
            notes_by_control.setdefault(n['control_id'], []).append(
                dict(author=n['author'], status=n['status'], justification=n['justification']))
        arbitrations = {n['control_id']: dict(author=n['arbiter'], status=n['status'], justification=n['justification'])
                        for n in item['arbitrations']}
        for control, criterion in read.criteria.items():
            notes, arbitration = notes_by_control.get(control, []), arbitrations.get(control)
            status, resolution = _reference(notes, arbitration, rule)
            if len({n['status'] for n in notes}) > 1:
                disagreements.append(dict(where, control_id=control, annotations=notes, arbitration=arbitration,
                                          outcome='ARBITRE' if resolution == 'arbitrated' else 'EXCLU'))
            if status is None:
                excluded.append(dict(where, control_id=control, reason=resolution,
                                     detail='Référence absente pour ce contrôle'))
                continue
            judged = read.decided[control]
            category = _category(status, judged['status'], judged['unusable'])
            row = group['requirements'][criterion]
            row['measured_pairs'] += 1
            row['arbitrated_pairs'] += resolution == 'arbitrated'
            item_pairs += 1
            group['items'] += item_pairs == 1
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
