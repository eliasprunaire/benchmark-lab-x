"""Private requester verdicts derived from immutable judge receipts, without an operator step"""
from contextlib import closing, nullcontext
from copy import deepcopy
from hashlib import sha256
import json
import logging
import os

from . import evaluation as e, judgment, preparation as p, provider_access, storage
from .acquisition import campaigns as c
from .runtime import worker_lock, verify
from .storage import BudgetError, ConflictError, IntegrityError, _money, _transaction
from .validation import digest

FORMAT = 'benchmark-lab-x/automatic-judgment/v1'


def context(store, connection, campaign_id, attempt_id):
    campaign = c._inspect(store, connection, campaign_id)
    if campaign['manifest'].get('funding') != 'requester':
        raise ValueError('Campagne personnelle requise')
    fingerprint = campaign['manifest']['contract_sha256']
    contract = deepcopy(c._comparison_contract(store, connection, fingerprint))
    qualified = p._automatic_qualification(store, connection, contract['dossier_id'], contract['revision'])
    operation = store._operation_for_update(connection, qualified['operation_id'], ('RECEIVED',))
    references = json.loads(operation['resources'][0])['judgment_reference']
    outputs = {r[0] for r in connection.execute('SELECT output_piece_id FROM s4_results WHERE output_piece_id IS NOT NULL')}
    pieces = [store.get_piece(row[0]) for row in connection.execute(
        "SELECT piece_id FROM pieces WHERE dossier_id=? AND revision=? AND role='judge'",
        (contract['dossier_id'], contract['revision'])) if row[0] not in outputs]
    for reference in references:
        matches = [piece for piece in pieces if piece['name'] == reference['name']
                   and store.read_piece(piece['piece_id']).decode('utf-8') == reference['content']]
        if len(matches) != 1:
            raise IntegrityError('Référence qualifiée absente ou ambiguë')
        contract['reference_pieces'].append(dict(id=matches[0]['piece_id'], name=reference['name']))
    if not references:
        raise IntegrityError('Référence qualifiée requise')
    # Adapt the frozen web criteria; neither the contract nor its qualification is rewritten
    spec = contract['specification']
    for criterion in spec['obligations'] + spec['eliminatory_errors']:
        criterion['control_ids'] = [criterion['id']]
    for criterion in spec['obligations']:
        criterion['tolerance'] = 'Selon le critère et les ambiguïtés acceptables déclarées'
    for criterion in spec['secondary_criteria']:
        criterion.update(measure=criterion['label'], proof='Passages de la sortie cités exactement',
                         unit='descriptif', aggregation='Aucune agrégation')
    spec['method'] = dict(id=FORMAT, version='1',
        control_ids=[x['id'] for x in spec['obligations'] + spec['eliminatory_errors']],
        expected_evidence='Citations exactes de la sortie et des pièces ; absence de preuve : INDETERMINE',
        responsible_role='Évaluation automatique Bench-X')
    spec['aggregation'] = 'Aucune moyenne ; obligations et erreurs éliminatoires par tentative'
    spec['local_criterion_ids'] = []
    attempt = next((a for a in campaign['attempts'] if a['operation_id'] == attempt_id), None)
    if attempt is None:
        raise KeyError(attempt_id)
    return dict(campaign=campaign, attempt=attempt, qualification=dict(contract=contract,
        contract_sha256=fingerprint, operation_id=operation['operation_id']))


def authority(connection, ctx):
    row = connection.execute('SELECT session_id FROM s2_dossiers WHERE dossier_id=?',
                             (ctx['campaign']['task']['dossier_id'],)).fetchone()
    return dict(actor='requester', authority_id='requester:' + row[0])


def admission(store, connection):
    current = p.admission(store, connection)
    if current is None or os.path.lexists(store._root / 'restore.json'):
        raise ConflictError('Évaluation fermée pendant la maintenance ou la restauration')
    return current['authority_id']


def operations(store, connection, campaign_id):
    ids = {row[0] for row in connection.execute('SELECT operation_id FROM operations WHERE engine_version=?', (FORMAT,))}
    return [op for op in store._operations(connection, operation_ids=ids)
            if json.loads(op['resources'][0])['request']['campaign_id'] == campaign_id]


def guard_budget(store, connection, budget_id, *, campaign_id=None):
    """Keep one launch's assistance envelope available until its judge intents are reserved"""
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s4_status'").fetchone():
        return
    rows = connection.execute('SELECT a.campaign_id,a.record_json FROM s4_admissions a '
        'JOIN s4_status s ON s.admission_id=a.admission_id').fetchall()
    for cid, raw in rows:
        grant = json.loads(raw)['authority'].get('automatic_judgment')
        if (grant and grant['budget_id'] == budget_id and cid != campaign_id
                and not operations(store, connection, cid)):
            raise BudgetError('Enveloppe personnelle réservée à l’évaluation de la comparaison en cours')


def preflight(store, session_id, dossier_id, campaign_id, transport, *, check_access=True):
    if transport is None or getattr(transport, '_session_id', None) != session_id:
        raise p.Denied('ACCESS_REQUIRED')
    if check_access and not transport.authorized(store):
        raise p.Denied('ACCESS_REQUIRED')
    if not check_access and not provider_access.status_only(store, session_id)['connected']:
        raise p.Denied('ACCESS_REQUIRED')
    config = transport.quote()
    connection = e.connection_for(store)
    with nullcontext() if connection.in_transaction else _transaction(connection):
        p.owner(connection, session_id, dossier_id)
        admission(store, connection)
        snapshot = c._inspect(store, connection, campaign_id)
        if snapshot['task']['dossier_id'] != dossier_id or snapshot['manifest'].get('funding') != 'requester':
            raise p.Denied('Campagne inaccessible')
        c._comparison_contract(store, connection, snapshot['manifest']['contract_sha256'])
        completed = len(operations(store, connection, campaign_id))
        count = len(snapshot['manifest']['plan']) - completed
        budget_id = provider_access.preparation_budget_id(session_id)
        guard_budget(store, connection, budget_id, campaign_id=campaign_id)
        grant = (snapshot['admission'] or {}).get('authority', {}).get('automatic_judgment')
        if grant and grant != dict(budget_id=budget_id, configuration=config):
            raise ConflictError('Profil de jugement différent du lancement autorisé')
        all_operations = store._operations(connection)
        budget = store._budget(connection, budget_id, all_operations)
        total = _money(config['reserve_usd']) * count
        if (budget['currency'] != 'USD' or not budget['provider_managed'] and total > _money(budget['available'])
                or store._blocking_costs(all_operations, budget, 'judgment')
                or any(o['budget_id'] == budget_id and o['state'] != 'RECEIVED'
                       for o in all_operations)):
            raise BudgetError('Budget personnel insuffisant ou coût non résolu pour l’évaluation')
        return config, str(total)


def reserve_campaign(store, session_id, dossier_id, campaign_id, transport):
    with worker_lock(store, shared=True):
        verify(store)
        connection = e.connection_for(store)
        with _transaction(connection, write=True):
            existing = operations(store, connection, campaign_id)
            p.owner(connection, session_id, dossier_id)
            if existing:
                # One automatic evaluation per retained response; POST and process restarts never retry
                return []
            config, _ = preflight(store, session_id, dossier_id, campaign_id, transport)
            snapshot = c._inspect(store, connection, campaign_id)
            if not snapshot['attempts'] or any(a['state'] != 'RECEIVED' for a in snapshot['attempts']):
                raise ConflictError('Réponses candidates à rapprocher avant l’évaluation')
            ids = []
            for attempt in snapshot['attempts']:
                if attempt['output_piece_id'] is None:
                    continue
                ctx = context(store, connection, campaign_id, attempt['operation_id'])
                content = e._review_content(store, ctx)
                oid = 'judge-' + sha256((campaign_id + ':' + attempt['operation_id']).encode()).hexdigest()[:40]
                request = dict(operation_id=oid, campaign_id=campaign_id, attempt_id=attempt['operation_id'],
                    review_sha256=digest(content), previous_evaluation_id=None,
                    authority=authority(connection, ctx), budget_id=provider_access.preparation_budget_id(session_id),
                    reserve_amount=config['reserve_usd'], requested_configuration=config)
                judgment._reserve(store, connection, request, transport, automatic=True)
                ids.append(oid)
            return ids


def execute_campaign(data, operation_ids, transport):
    for operation_id in operation_ids:
        try:
            judgment.execute(data, operation_id, transport)
        except Exception as error:
            with closing(storage.Store(data)) as store:
                op = next(o for o in store.inspect_operations() if o['operation_id'] == operation_id)
                campaign_id = json.loads(op['resources'][0])['request']['campaign_id']
                c.stop(store, campaign_id, reason='JUDGMENT_STOPPED')
            logging.getLogger(__name__).warning('AUTOMATIC_JUDGMENT_STOPPED operation=%s error=%s',
                                               operation_id, type(error).__name__)
            return


def status(store, connection, campaign_id):
    snapshot = c._inspect(store, connection, campaign_id)
    ops = operations(store, connection, campaign_id)
    total = len(snapshot['manifest']['plan'])
    completed = len(records(store, connection, campaign_id))
    result = dict(status='NOT_STARTED', total=total, completed=completed, reason=None, can_start=False)
    if completed == total:
        result['status'] = 'COMPLETE'
    elif snapshot['stop_reason'] == 'JUDGMENT_STOPPED':
        result.update(status='BLOCKED', reason='Évaluation interrompue. Les réponses sont conservées ; aucun appel ne sera relancé automatiquement.')
    elif any(o['state'] == 'AMBIGUOUS' or o['receipt'] is not None and o['receipt']['result'] is None for o in ops):
        result.update(status='BLOCKED', reason='L’évaluation n’a pas fourni de preuves exploitables. Les réponses et reçus sont conservés.')
    elif ops:
        saved = json.loads(ops[0]['resources'][0])['context']['campaign']
        if p.admission(store, connection) is None or any(snapshot[key] != saved[key]
                for key in ('admission', 'stop_reason', 'restore_pending')):
            result.update(status='BLOCKED', reason='Évaluation interrompue. Aucun appel ne sera relancé automatiquement.')
        else:
            if all(o['state'] == 'RECEIVED' for o in ops):
                result.update(status='BLOCKED', reason='Évaluation partielle terminée : une réponse candidate manque. Les résultats disponibles restent consultables.')
            else:
                result['status'] = 'RUNNING'
    elif snapshot['attempts'] and all(a['state'] == 'RECEIVED' for a in snapshot['attempts']):
        result['can_start'] = any(a['output_piece_id'] for a in snapshot['attempts'])
        if not result['can_start']:
            result.update(status='BLOCKED', reason='Aucune réponse exploitable à évaluer.')
    elif snapshot['attempts']:
        result['status'] = 'WAITING'
    return result


def records(store, connection, campaign_id):
    result = []
    for op in operations(store, connection, campaign_id):
        saved, ctx = judgment._bound(store, connection, op)
        proposal = judgment._retained_proposal(store, connection, op, ctx, recover_metadata=True)
        if proposal is None:
            continue
        result.append(e._record(store, connection, saved['context'], proposal['report'], evaluation_id=op['operation_id'],
            created_at=op['created_at'], engine_source=saved['engine_source_sha256'],
            previous_evaluation_id=None, source_operation=op, responsible='Évaluation automatique Bench-X',
            authority=saved['request']['authority'], record_format=FORMAT))
    return result
