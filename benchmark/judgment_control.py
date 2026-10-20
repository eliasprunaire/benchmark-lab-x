"""Contrôle d'une réponse construite : même juge, sans reçu candidat ni référence qualifiée.

La réponse reste une pièce de la liaison. Son rôle `controle-construit` n'entre pas dans la table `pieces`.
"""
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
import logging

from . import evaluation as e
from . import outgoing, preparation as prep, storage
from .acquisition.campaigns import _intact, _present
from .runtime import worker_lock
from .storage import BudgetError, ConflictError, IntegrityError, _fields, _money, _strict_json as encode, _text, _transaction
from .transports.openrouter import INCIDENT_TEXT, NotSent
from .validation import digest as value_digest, identifier

FORMAT = 'benchmark-lab-x/judgment-control/v1'
PROVENANCE = 'reponse-construite-de-controle'
STOP = 'JUDGMENT_STOPPED'
REQUEST_FIELDS = ('operation_id', 'control_id', 'dossier_id', 'revision', 'budget_id',
                  'reserve_amount', 'requested_configuration', 'provenance', 'authority',
                  'contrat', 'compte_rendu', 'reponse', 'reference')
CONTRACT_FIELDS = ('instruction', 'deliverables', 'criteria', 'acceptable_ambiguities', 'specification')
BINDING_FIELDS = ('provenance', 'control_id', 'review_sha256', 'pieces', 'content', 'request', 'reference_qualifiee')
_KINDS = ('compte_rendu', 'reponse', 'reference')
_REFUSED_AUTHORITY = ('Ayo', 'TEST_ONLY')


def _authority(value):
    _text(value, 'authority')
    if value in _REFUSED_AUTHORITY or value.startswith('requester:') or value.startswith('TEST_ONLY'):
        raise ValueError('Autorité de contrôle requise')
    return value


def _pieces(request):
    pieces = []
    for kind in _KINDS:
        piece = outgoing.evidence_piece(request[kind])
        if piece['name'].startswith('Sortie brute'):
            raise ValueError('Nom de sortie candidate interdit')
        pieces.append(dict(piece, kind=kind, role='controle-construit'))
    if len({piece['piece_id'] for piece in pieces}) != len(pieces):
        raise ValueError('Identifiants de pièces distincts requis')
    return pieces


def _review(request, pieces):
    """Vue fermée du juge, reconstruite depuis les pièces liées, hors matrice"""
    from .automatic_judgment import review_specification
    from .judgment import local_criteria
    contrat = request['contrat']
    _fields(contrat, CONTRACT_FIELDS, 'contrat de contrôle')
    spec = contrat['specification']
    if type(spec) is not dict:
        raise ValueError('Spécification de contrôle requise')
    local = local_criteria(spec)
    reviewed = review_specification(spec)
    indexed = {piece['kind']: piece for piece in pieces}

    def visible(piece):
        return {key: piece[key] for key in ('piece_id', 'name', 'sha256', 'content')}

    package = {key: contrat[key] for key in ('instruction', 'deliverables', 'criteria', 'acceptable_ambiguities')}
    content = e.review_view(package, reviewed, [visible(indexed['compte_rendu'])],
                            [visible(indexed['reference'])], visible(indexed['reponse']))
    return content, local


def _request(value):
    value = deepcopy(value)
    _fields(value, REQUEST_FIELDS, 'contrôle')
    for key in ('operation_id', 'control_id', 'budget_id'):
        identifier(value[key])
    identifier(value['dossier_id'])
    if type(value['revision']) is not int:
        raise ValueError('Révision de contrôle requise')
    if value['provenance'] != PROVENANCE:
        raise ValueError('Provenance de contrôle requise')
    _authority(value['authority'])
    _money(value['reserve_amount'])
    if type(value['requested_configuration']) is not dict:
        raise ValueError('Configuration de contrôle requise')
    pieces = _pieces(value)
    content, local = _review(value, pieces)
    return value, pieces, content, local


def _binding(operation):
    if (operation['engine_version'] != FORMAT or operation['phase'] != 'judgment'
            or len(operation['resources']) not in (2, 3)):
        raise ValueError('Intention de contrôle requise')
    saved = json.loads(operation['resources'][0], object_pairs_hook=storage._unique_object)
    _fields(saved, BINDING_FIELDS, 'liaison de contrôle')
    if saved['reference_qualifiee'] is not False or saved['provenance'] != PROVENANCE:
        raise IntegrityError('Référence qualifiée interdite')
    pieces = saved['pieces']
    if type(pieces) is not list or [piece.get('kind') for piece in pieces] != list(_KINDS):
        raise IntegrityError('Pièces de contrôle divergentes')
    checked = []
    for piece in pieces:
        _fields(piece, ('piece_id', 'name', 'sha256', 'content', 'kind', 'role'), 'pièce de contrôle')
        if piece['role'] != 'controle-construit':
            raise IntegrityError('Rôle de contrôle divergent')
        checked.append(dict(outgoing.evidence_piece(
            {key: piece[key] for key in ('piece_id', 'name', 'sha256', 'content')}), kind=piece['kind'], role=piece['role']))
    request = saved['request']
    if (request['operation_id'] != operation['operation_id'] or request['control_id'] != saved['control_id']
            or request['budget_id'] != operation['budget_id'] or request['reserve_amount'] != operation['reserved_amount']
            or request['authority'] != operation['authority']
            or (request['dossier_id'], request['revision']) != (operation['dossier_id'], operation['revision'])
            or request['requested_configuration'] != operation['requested_configuration']
            or request['provenance'] != PROVENANCE):
        raise IntegrityError('Liaison de contrôle divergente')
    content, _ = _review(request, checked)
    if content != saved['content'] or value_digest(content) != saved['review_sha256']:
        raise IntegrityError('Projection ou sortie divergente')
    return dict(saved, pieces=checked, content=content)


def _stopped(store, connection, control_id, operation_id=None):
    for operation in store._operations(connection):
        if operation['engine_version'] != FORMAT or operation['operation_id'] == operation_id:
            continue
        result = (operation['receipt'] or {}).get('result') if operation['receipt'] else None
        if type(result) is dict and result.get('control_stop') == STOP:
            saved = json.loads(operation['resources'][0])
            if saved.get('control_id') == control_id:
                return True
    return False


def _gate(store, connection, request, operation_id=None, *, reserved=False):
    """Réserve et effets inconnus avant tout `prepare`"""
    from .automatic_judgment import guard_budget
    guard_budget(store, connection, request['budget_id'])
    config = request['requested_configuration']
    operations = store._operations(connection)
    budget = store._budget(connection, request['budget_id'], operations)
    reserve = _money(request['reserve_amount'])
    if (budget['currency'] != 'USD' or reserve <= 0 or 'reserve_usd' not in config
            or reserve != _money(config['reserve_usd'])):
        raise BudgetError('Réserve USD liée au profil requise')
    if (store._blocking_costs(operations, budget, 'judgment')
            or any(op['operation_id'] != operation_id and op['budget_id'] == request['budget_id']
                   and op['state'] in ('EMISSION_POSSIBLE', 'AMBIGUOUS') for op in operations)):
        raise BudgetError('unresolved effects or costs block this envelope')
    if not budget['provider_managed']:
        available = _money(budget['available'])
        if (available < 0) or (not reserved and reserve > available):
            raise BudgetError('insufficient available budget')


def _normalize(answer, pieces, content, local, evidence_rule):
    _fields(answer, ('findings', 'measures', 'limits', 'proposed_verdict'), 'judgment proposal')
    if answer['proposed_verdict'] not in ('SATISFAIT', 'NE SATISFAIT PAS', 'INDETERMINE'):
        raise ValueError('Verdict proposé inconnu')
    resources = {piece['piece_id']: piece['content'].encode('utf-8') for piece in pieces}
    output_id = next(piece['piece_id'] for piece in pieces if piece['kind'] == 'reponse')
    sources = {piece['piece_id'] for piece in pieces if piece['kind'] == 'compte_rendu'}
    criteria = {row['id']: row for row in content['obligations'] + content['eliminatory_errors']}
    findings = deepcopy(answer['findings'])
    if type(findings) is not list:
        raise ValueError('Constats requis')
    allowed = evidence_rule == 'server-evidence/v2'
    for finding in findings:
        _fields(finding, ('criterion_id', 'control_id', 'status', 'attribution', 'finding', 'evidence'), 'finding')
        if (finding['criterion_id'] not in criteria
                or finding['control_id'] not in criteria[finding['criterion_id']]['control_ids']):
            raise ValueError('Critère ou contrôle non déclaré')
        if finding['status'] not in ('PASS', 'FAIL', 'INDETERMINE'):
            raise ValueError('État de constat inconnu')
        _present(finding['attribution'], 'attribution')
        _present(finding['finding'], 'finding')
        if type(finding['evidence']) is not list:
            raise ValueError('Pièces de preuve requises')
        for proof in finding['evidence']:
            if type(proof) is not dict or not {'piece_id', 'passage'} <= proof.keys() <= {'piece_id', 'passage', 'sha256'}:
                raise ValueError('citation')
            identifier(proof['piece_id'])
            raw = resources.get(proof['piece_id'])
            if raw is None:
                raise IntegrityError('Pièce de citation étrangère')
            proof['sha256'] = sha256(raw).hexdigest()
        e.finding_evidence(finding, resources, output_id, sources, omission_allowed=allowed)
        if finding['criterion_id'] in local and finding['status'] != 'INDETERMINE':
            finding.update(status='INDETERMINE', attribution='evidence', evidence=[],
                           finding='Contrôle local requis : données exclues de la projection')
    planned = {row['id'] for row in content['secondary_criteria']}
    measures = deepcopy(answer['measures'])
    if type(measures) is not list or type(answer['limits']) is not list:
        raise ValueError('Mesures prévues requises')
    if measures and not planned:
        raise ValueError('Mesure étrangère au contrat')
    seen = set()
    for measure in measures:
        _fields(measure, ('criterion_id', 'value', 'unit', 'evidence'), 'measure')
        if measure['criterion_id'] not in planned or measure['criterion_id'] in seen:
            raise ValueError('Mesure étrangère au contrat')
        seen.add(measure['criterion_id'])
    return dict(findings=findings, measures=measures, limits=list(answer['limits']),
                proposed_verdict=answer['proposed_verdict'])


def _covered(content, findings):
    covered = [dict(finding) for finding in findings]
    pairs = {(finding['criterion_id'], finding['control_id']) for finding in covered}
    for criterion in content['obligations'] + content['eliminatory_errors']:
        for control in criterion['control_ids']:
            if (criterion['id'], control) not in pairs:
                covered.append(dict(criterion_id=criterion['id'], control_id=control, status='INDETERMINE',
                                     attribution='evidence', finding='Contrôle prévu sans constat conservé', evidence=[]))
    return covered


def _verdict(content, findings, output_id):
    """Même lecture que l'évaluation, sans exiger une émission candidate"""
    covered = _covered(content, findings)
    if e.defects(covered, output_id):
        return 'NE SATISFAIT PAS'
    if (covered and all(finding['status'] == 'PASS' and finding['attribution'] in ('candidate', 'evidence')
                        and any(proof['piece_id'] == output_id for proof in finding['evidence'])
                        for finding in covered)):
        return 'SATISFAIT'
    return 'INDETERMINE'


def _close_stopped(store, operation_id):
    """Clôt une intention encore ouverte sans la rendre relançable"""
    connection = store._s1_connection()
    with _transaction(connection, write=True):
        if not connection.execute("UPDATE operations SET state='EMISSION_POSSIBLE' WHERE operation_id=? AND state='INTENT_RECORDED'",
                                  (operation_id,)).rowcount:
            return False
        currency = connection.execute('SELECT b.currency FROM reservations r JOIN budgets b USING(budget_id) '
                                      'WHERE r.operation_id=?', (operation_id,)).fetchone()[0]
        store._record_receipt(connection, operation_id, dict(
            receipt_id='control-stop-' + operation_id,
            observed_configuration=dict(incident=STOP, observed_at=prep._now().isoformat()),
            resources_seen=[], result={'status': 'NOT_SENT', 'control_stop': STOP}),
            dict(status='KNOWN', amount='0', currency=currency, source='Contrôle arrêté avant envoi : aucun appel'))
    return True


def reserve_control(store, request, transport, *, link=None):
    request, pieces, content, _ = _request(request)
    if link is not None:
        _text(link, 'lien de relance')
    with worker_lock(store, shared=True):
        _intact(store)
        connection = store._s1_connection()
        with _transaction(connection, write=True):
            if _stopped(store, connection, request['control_id']):
                raise ConflictError('Contrôle arrêté')
            _gate(store, connection, request)
            operation = dict(operation_id=request['operation_id'], dossier_id=request['dossier_id'],
                             revision=request['revision'], phase='judgment', authority=request['authority'],
                             engine_version=FORMAT, requested_configuration=request['requested_configuration'],
                             resources=[])
            outgoing_request = dict(outgoing_format=outgoing.FORMAT, outgoing=content)
            wire = transport.prepare(deepcopy(operation), outgoing_request)
            saved = dict(provenance=PROVENANCE, control_id=request['control_id'], review_sha256=value_digest(content),
                         pieces=pieces, content=content, request=request, reference_qualifiee=False)
            operation['resources'] = [encode(saved), wire] + ([link] if link is not None else [])
            store._reserve_intent(connection, operation, request['budget_id'], request['reserve_amount'],
                                  created_at=prep._now())
    return inspect_control(store, request['operation_id'])


def execute_control(data, operation_id, transport):
    with closing(storage.Store(data)) as store, worker_lock(store, shared=True):
        _intact(store)
        connection = store._s1_connection()
        with _transaction(connection, write=True):
            operation = store._operation_for_update(connection, operation_id, ('INTENT_RECORDED',))
            saved = _binding(operation)
            if _stopped(store, connection, saved['control_id'], operation_id):
                raise ConflictError('Contrôle arrêté')
            _gate(store, connection, saved['request'], operation_id, reserved=True)
            outgoing_request = dict(outgoing_format=outgoing.FORMAT, outgoing=saved['content'])
            wire = transport.prepare(deepcopy(operation), deepcopy(outgoing_request))
            if wire != operation['resources'][1]:
                raise IntegrityError('Profil ou octets modifiés')
            connection.execute("UPDATE operations SET state='EMISSION_POSSIBLE' WHERE operation_id=?", (operation_id,))
        try:
            evidence_rule = operation['requested_configuration'].get('evidence_rule')
            operation = dict(operation, state='EMISSION_POSSIBLE', conserved_wire=wire)
            logging.getLogger(__name__).info('CONTROL_EMITTING operation=%s', operation_id)
            response = transport(deepcopy(operation), deepcopy(outgoing_request))
            _fields(response, ('receipt', 'cost'), 'judgment response')
            receipt = response['receipt']
            try:
                _, local = _review(saved['request'], saved['pieces'])
                proposal = _normalize(receipt['result'], saved['pieces'], saved['content'], local,
                                      evidence_rule)
            except (ValueError, KeyError, TypeError, IntegrityError):
                proposal = None
                if receipt['observed_configuration']['incident'] not in INCIDENT_TEXT:
                    receipt['observed_configuration']['incident'] = 'UNUSABLE_JUDGMENT_PROPOSAL'
            receipt['result'] = proposal
            storage.retry_locked(lambda: store.record_receipt(operation_id, receipt, response['cost']))
        except BaseException as error:
            logging.getLogger(__name__).error('CONTROL_STOPPED operation=%s error=%s', operation_id, type(error).__name__)
            current = storage.retry_locked(lambda: next(
                row for row in store.inspect_operations() if row['operation_id'] == operation_id))
            if current['state'] == 'EMISSION_POSSIBLE':
                prep._interrupted(store, operation_id, error, 'JUDGMENT_EFFECTS_UNKNOWN')
            raise


def inspect_control(store, operation_id):
    _intact(store)
    connection = store._s1_connection()
    with _transaction(connection):
        operation = store._operation_for_update(connection, operation_id,
                    ('INTENT_RECORDED', 'EMISSION_POSSIBLE', 'AMBIGUOUS', 'RECEIVED'))
        saved = _binding(operation)
        config = operation['requested_configuration']
        receipt = operation['receipt']
        result = None if receipt is None else receipt['result']
        observed = (receipt or {}).get('observed_configuration') or {}
        incident = observed.get('incident') if type(observed) is dict else None
        output_id = next(piece['piece_id'] for piece in saved['pieces'] if piece['kind'] == 'reponse')
        proposal = result if type(result) is dict and 'findings' in result else None
        verdict = None
        if proposal is not None and not incident and operation['state'] == 'RECEIVED':
            verdict = _verdict(saved['content'], proposal['findings'], output_id)
        return dict(operation_id=operation['operation_id'], control_id=saved['control_id'],
                    provenance=saved['provenance'], state=operation['state'], review_sha256=saved['review_sha256'],
                    profile_sha256=config.get('profile_sha256'), prompt_sha256=config.get('prompt_sha256'),
                    model=config.get('model'), revision=config.get('revision'),
                    pieces=[dict(piece_id=piece['piece_id'], name=piece['name'], sha256=piece['sha256'],
                                 kind=piece['kind'], role=piece['role']) for piece in saved['pieces']],
                    proposal=proposal, verdict=verdict, incident=incident, cost=operation['observed_cost'],
                    reserve_amount=operation['reserved_amount'], reference_qualifiee=saved['reference_qualifiee'])


def resume_control(store, operation_id, transport):
    """Réserve la tentative suivante, sans l'envoyer"""
    with worker_lock(store, shared=True):
        _intact(store)
        connection = store._s1_connection()
        with _transaction(connection):
            operation = store._operation_for_update(connection, operation_id, ('RECEIVED',))
            saved = _binding(operation)
            if _stopped(store, connection, saved['control_id'], operation_id):
                raise ConflictError('Contrôle arrêté')
            due = prep._retry_due(operation, unsent=True)
            if due is None or due > prep._now():
                return None
            link = prep.retry_link(operation, unsent=True)
            attempt = json.loads(link)['attempt']
            next_id = operation_id + '-r' + str(attempt)
            if len(next_id) > 128:
                raise ValueError('Identifiant de relance trop long')
            request = deepcopy(saved['request'])
            request['operation_id'] = next_id
        reserve_control(store, request, transport, link=link)
        return next_id


def _blocking_incident(data, operation_id):
    """Incident enregistré hors relance autorisée"""
    with closing(storage.Store(data)) as store:
        incident = inspect_control(store, operation_id)['incident']
    if incident and incident not in prep._RETRIED_INCIDENTS:
        return incident
    return None


def execute_series(data, operation_ids, transport):
    """Envoie la série. Un incident hors relance, ou une erreur autre qu'une connexion impossible, arrête la suite"""
    for index, operation_id in enumerate(operation_ids):
        try:
            execute_control(data, operation_id, transport)
        except NotSent:
            continue
        except Exception as error:
            logging.getLogger(__name__).warning('CONTROL_SERIES_STOPPED operation=%s error=%s',
                                               operation_id, type(error).__name__)
            with closing(storage.Store(data)) as store:
                for remaining in operation_ids[index:]:
                    _close_stopped(store, remaining)
            return dict(stopped=True, error=type(error).__name__)
        incident = _blocking_incident(data, operation_id)
        if incident is None:
            continue
        # Incident enregistré hors relance autorisée : l'appel suivant ne part pas
        logging.getLogger(__name__).warning('CONTROL_SERIES_STOPPED operation=%s error=%s',
                                           operation_id, incident)
        with closing(storage.Store(data)) as store:
            for remaining in operation_ids[index + 1:]:
                _close_stopped(store, remaining)
        return dict(stopped=True, error=incident)
    return dict(stopped=False, error=None)
