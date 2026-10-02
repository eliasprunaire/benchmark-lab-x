"""Private requester verdicts derived from immutable judge receipts, without an operator step"""
from contextlib import closing, nullcontext
from copy import deepcopy
from hashlib import sha256
import json
import logging
import math
import os

from . import evaluation as e, judgment, preparation as p, provider_access, storage
from .acquisition import campaigns as c
from .runtime import worker_lock
from .storage import BudgetError, ConflictError, IntegrityError, _money, _transaction
from .transports.openrouter import NotSent
from .validation import digest

FORMAT = 'benchmark-lab-x/automatic-judgment/v1'


def context(store, connection, campaign_id, attempt_id):
    # Campagne, contrat et référence relus une fois par version des données, pas une fois par réponse
    # Les octets des pièces restent relus et vérifiés à l'usage (`e._resources`, `e._review_content`)
    version = storage.data_version(connection) if connection is store._connection and connection.in_transaction else None
    if version is None or store._judgment_contexts[0] != version:
        store._judgment_contexts = (version, {})
    cache = store._judgment_contexts[1]
    if campaign_id not in cache:
        cache[campaign_id] = _campaign_context(store, connection, campaign_id)
    campaign, contract, operation_id = deepcopy(cache[campaign_id])
    attempt = next((a for a in campaign['attempts'] if a['operation_id'] == attempt_id), None)
    if attempt is None:
        raise KeyError(attempt_id)
    return dict(campaign=campaign, attempt=attempt, qualification=dict(contract=contract,
        contract_sha256=campaign['manifest']['contract_sha256'], operation_id=operation_id))


def _campaign_context(store, connection, campaign_id):
    campaign = c._inspect(store, connection, campaign_id)
    if campaign['manifest'].get('funding') != 'requester':
        raise ValueError('Campagne personnelle requise')
    fingerprint = campaign['manifest']['contract_sha256']
    contract = deepcopy(c._comparison_contract(store, connection, fingerprint))
    qualified = p._automatic_qualification(store, connection, contract['dossier_id'], contract['revision'])
    if qualified is None:
        raise IntegrityError('Qualification automatique absente')
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
    return campaign, contract, operation['operation_id']


def authority(connection, ctx):
    row = connection.execute('SELECT session_id FROM s2_dossiers WHERE dossier_id=?',
                             (ctx['campaign']['task']['dossier_id'],)).fetchone()
    return dict(actor='requester', authority_id='requester:' + row[0])


def admission(store, connection):
    # Le jugement relève de la clé du demandeur : seule une restauration à rapprocher le suspend
    if os.path.lexists(store._root / 'restore.json'):
        raise ConflictError('Évaluation fermée pendant la restauration')
    return 'requester'


def family(connection, campaign_id):
    """Comparaison publique et ses reprises techniques : l'utilisateur lit une seule comparaison

    Une campagne opérateur garde ses reprises comme campagnes distinctes, telles qu'elles ont été évaluées
    """
    row = connection.execute('SELECT manifest_json, manifest_sha256 FROM s4_campaigns WHERE campaign_id=?',
                             (campaign_id,)).fetchone()
    if row is None or c.q._decode(*row).get('funding') != 'requester':
        return [campaign_id]
    return [campaign_id] + c._recovery_descendants(connection, campaign_id)


def operations(store, connection, campaign_id):
    ids = {row[0] for row in connection.execute('SELECT operation_id FROM operations WHERE engine_version=?', (FORMAT,))}
    campaigns = set(family(connection, campaign_id))
    return [op for op in store._operations(connection, operation_ids=ids)
            if json.loads(op['resources'][0])['request']['campaign_id'] in campaigns]


def _attempts(store, connection, campaign_id):
    """Tentatives de la campagne et de ses reprises, chacune avec l'identifiant de sa campagne

    Une reprise arrêtée avant tout envoi (clé refusée, par exemple) reste consignée mais n'a pas eu
    lieu : elle n'est ni attendue, ni jugée, ni affichée
    """
    result = []
    for cid in family(connection, campaign_id):
        snapshot = c._inspect(store, connection, cid)
        result += [(cid, attempt) for attempt in snapshot['attempts']
                   if cid == campaign_id or snapshot['admission'] is not None or attempt['state'] != 'INTENT_RECORDED']
    return result


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


def _operation_id(campaign_id, attempt_id, index):
    """Rang 1 : identifiant d'origine, inchangé pour les données existantes ; rang n : n-ième de la série"""
    seed = campaign_id + ':' + attempt_id + ('' if index == 1 else ':' + str(index))
    return 'judge-' + sha256(seed.encode()).hexdigest()[:40]


def _latest(ops):
    """Par réponse jugée : sa dernière opération et la longueur de sa série, lues sur les identifiants"""
    series = {}
    for op in ops:
        request = json.loads(op['resources'][0])['request']
        series.setdefault((request['campaign_id'], request['attempt_id']), {})[op['operation_id']] = op
    result = {}
    for (campaign_id, attempt_id), found in series.items():
        last = found.get(_operation_id(campaign_id, attempt_id, len(found)))
        if last is None:
            raise IntegrityError('Série de jugements incomplète')
        result[attempt_id] = (last, len(found))
    return result


def _due(stop_reason, ops):
    """Relances programmées : {réponse: (dernière opération, longueur de série, échéance)}

    Aucune après l'arrêt de l'évaluation ni tant qu'un jugement de la campagne est en cours ou ambigu
    """
    if stop_reason == 'JUDGMENT_STOPPED' or any(o['state'] != 'RECEIVED' for o in ops):
        return {}
    result = {}
    for attempt_id, (op, length) in _latest(ops).items():
        # Un jugement clos avant envoi, par un redémarrage, se reprend aussi : rien d'autre ne le relancerait
        due = p._retry_due(op, unsent=True)
        if due is not None:
            result[attempt_id] = (op, length, due)
    return result


def preflight(store, session_id, dossier_id, campaign_id, transport, *, check_access=True, count=None):
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
        if count is None:
            # Une réponse déjà jugée compte une fois, quelle que soit la longueur de sa série
            count = len(snapshot['manifest']['plan']) - len(_latest(operations(store, connection, campaign_id)))
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
        c._intact(store)
        connection = e.connection_for(store)
        # Contextes et contenus calculés hors de l'écriture ; sous l'écriture, une version de données
        # inchangée prouve qu'ils sont encore exacts, sinon tout est recalculé
        with store.read_snapshot():
            version = storage.data_version(connection)
            plan = _plan(store, connection, session_id, dossier_id, campaign_id, transport)
        with _transaction(connection, write=True):
            if storage.data_version(connection) != version:
                plan = _plan(store, connection, session_id, dossier_id, campaign_id, transport)
            for request, ctx, content, link in plan:
                judgment._reserve(store, connection, request, transport, automatic=True,
                                  prepared=(ctx, content), link=link)
            return [request['operation_id'] for request, _, _, _ in plan]


def retry(store, operation_id, transport, source=None):
    """Relances échues de la campagne de `operation_id`, réservées comme par un POST de sa session

    `source` : signature commune aux relances ; un jugement porte l'empreinte de son moteur
    """
    connection = e.connection_for(store)
    found = store._operations(connection, operation_ids={operation_id})
    if not found:
        return []
    dossier_id = found[0]['dossier_id']
    session_id = connection.execute('SELECT session_id FROM s2_dossiers WHERE dossier_id=?', (dossier_id,)).fetchone()[0]
    campaign_id = json.loads(found[0]['resources'][0])['request']['campaign_id']
    return reserve_campaign(store, session_id, dossier_id, campaign_id, transport)


def due_retries(store, *, session_id=None, dossier_id=None):
    """Relances d'évaluation à programmer : (dernière opération, cas, session, secondes avant l'échéance)"""
    connection = p.connection_for(store)
    result = []
    with _transaction(connection):
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s4_control'").fetchone():
            return result
        rows = connection.execute(
            'SELECT o.operation_id, d.dossier_id, d.session_id FROM operations o JOIN s2_dossiers d '
            'ON d.dossier_id=o.dossier_id WHERE o.engine_version=? '
            'AND (? IS NULL OR d.session_id=?) AND (? IS NULL OR d.dossier_id=?)',
            (FORMAT, session_id, session_id, dossier_id, dossier_id)).fetchall()
        owners = {row[0]: row[1:] for row in rows}
        # Lu à chaque POST : un regroupement et l'état d'arrêt suffisent, sans relire chaque campagne
        campaigns = {}
        for op in store._operations(connection, operation_ids=set(owners)):
            campaigns.setdefault(json.loads(op['resources'][0])['request']['campaign_id'], []).append(op)
        for campaign_id, ops in sorted(campaigns.items()):
            stop_reason = connection.execute('SELECT stop_reason FROM s4_status WHERE campaign_id=?',
                                             (campaign_id,)).fetchone()[0]
            for op, _, due in _due(stop_reason, ops).values():
                result.append((op['operation_id'], *owners[op['operation_id']],
                               max(0, math.ceil((due - p._now()).total_seconds()))))
    return result


def _plan(store, connection, session_id, dossier_id, campaign_id, transport):
    existing = operations(store, connection, campaign_id)
    p.owner(connection, session_id, dossier_id)
    due = None
    if existing:
        # Une évaluation par réponse retenue ; seule une relance automatique échue en ajoute une à sa série,
        # que la demande vienne d'un minuteur ou d'un POST : les deux calculent le même identifiant
        now = p._now()
        stop_reason = connection.execute('SELECT stop_reason FROM s4_status WHERE campaign_id=?',
                                         (campaign_id,)).fetchone()[0]
        due = {attempt_id: item for attempt_id, item in _due(stop_reason, existing).items() if item[2] <= now}
        if not due:
            return []
    config, _ = preflight(store, session_id, dossier_id, campaign_id, transport,
                          count=None if due is None else len(due))
    attempts = _attempts(store, connection, campaign_id)
    if not attempts or any(a['state'] != 'RECEIVED' for _, a in attempts):
        raise ConflictError('Réponses candidates à rapprocher avant l’évaluation')
    plan = []
    for cid, attempt in attempts:
        # Sortie absente, vide ou blanche : rien à juger, aucun appel payant
        if not c.answered(attempt) or due is not None and attempt['operation_id'] not in due:
            continue
        previous, length, _ = due[attempt['operation_id']] if due is not None else (None, 0, None)
        ctx = context(store, connection, cid, attempt['operation_id'])
        content = e._review_content(store, ctx)
        request = dict(operation_id=_operation_id(cid, attempt['operation_id'], length + 1),
            campaign_id=cid, attempt_id=attempt['operation_id'],
            review_sha256=digest(content), previous_evaluation_id=None,
            authority=authority(connection, ctx), budget_id=provider_access.preparation_budget_id(session_id),
            reserve_amount=config['reserve_usd'], requested_configuration=config)
        link = None if previous is None else p.retry_link(previous, unsent=True)
        # Mêmes contrôles qu'à la réservation, avant toute écriture
        plan.append((request, *judgment._inputs(store, connection, request, automatic=True), link))
    return plan


def execute_campaign(data, operation_ids, transport):
    for index, operation_id in enumerate(operation_ids):
        try:
            judgment.execute(data, operation_id, transport)
        except Exception as error:
            logging.getLogger(__name__).warning('AUTOMATIC_JUDGMENT_STOPPED operation=%s error=%s',
                                               operation_id, type(error).__name__)
            if isinstance(error, NotSent):
                # Connexion impossible : rien n'est parti, la relance automatique reprend ce jugement
                continue
            try:
                with closing(storage.Store(data)) as store:
                    # Refusé avant émission, puis les suivants : clos sans coût, jamais laissés en attente
                    for remaining in operation_ids[index:]:
                        store.close_not_sent(remaining)
                    op = next(o for o in store.inspect_operations() if o['operation_id'] == operation_id)
                    campaign_id = json.loads(op['resources'][0])['request']['campaign_id']
                    c.stop(store, campaign_id, reason='JUDGMENT_STOPPED')
            except Exception as failure:
                # Le démarrage suivant de l'exécuteur close ce qui reste ouvert
                logging.getLogger(__name__).error('AUTOMATIC_JUDGMENT_STOP_FAILED operation=%s error=%s',
                                                  operation_id, type(failure).__name__)
            return


def _completed(store, connection, ops):
    """Autant que `records`, sans relier chaque jugement à son contexte : le suivi n'affiche qu'un compte

    Une proposition présente compte, comme dans `records` ; seule une proposition absente, à récupérer
    depuis son reçu, repasse par la vérification complète. Les résultats, eux, relisent `records`.
    Rend aussi le nombre de reçus sans proposition retenue, même après récupération
    """
    count = unusable = 0
    for op in ops:
        if op['receipt'] is None or storage.not_sent(op):
            continue
        if op['receipt']['result'] is not None:
            count += 1
            continue
        saved, ctx = judgment._bound(store, connection, op)
        if judgment._retained_proposal(store, connection, op, ctx, recover_metadata=True) is None:
            unusable += 1
        else:
            count += 1
    return count, unusable


def status(store, connection, campaign_id, snapshot=None):
    """`total` compte les réponses à évaluer ; `cells`, les modèles prévus, pour montrer la couverture"""
    snapshot = snapshot or c._inspect(store, connection, campaign_id)
    ops = operations(store, connection, campaign_id)
    # Seule la dernière opération de chaque série compte : un jugement relancé reste un seul jugement
    latest = [op for op, _ in _latest(ops).values()]
    # Un jugement est réservé par réponse à évaluer, une fois toutes les réponses reçues, reprises comprises
    family = _attempts(store, connection, campaign_id)
    attempts = [a for _, a in family]
    total = len(latest) if ops else sum(c.answered(a) for a in attempts)
    cells = len(snapshot['manifest']['plan'])
    completed, unusable = _completed(store, connection, latest)
    result = dict(status='NOT_STARTED', total=total, cells=cells, completed=completed, reason=None, can_start=False,
                  # Reprises techniques pas encore reçues : le suivi les dit au lieu d'annoncer toutes les réponses
                  recovering=sum(cid != campaign_id and a['state'] != 'RECEIVED' for cid, a in family))
    due = _due(snapshot['stop_reason'], ops)
    exhausted = [incident for incident in (p._provider_incident(op, unsent=True) for op in latest)
                 if incident in p._RETRIED_INCIDENTS]
    if ops and completed == total:
        # Terminée avant toute autre lecture : une admission fermée après la fin n'est pas une interruption
        result['status'] = 'COMPLETE'
        if cells > total:
            missing = cells - total
            result['reason'] = (f'{missing} modèle{"s" if missing > 1 else ""} sur {cells} '
                                f'{"n’ont" if missing > 1 else "n’a"} pas donné de réponse exploitable : '
                                f'{"ils ne sont pas évalués" if missing > 1 else "il n’est pas évalué"}.')
    elif snapshot['stop_reason'] == 'JUDGMENT_STOPPED':
        unreachable = any(storage.not_sent(o) and (o['receipt']['observed_configuration'] or {}).get('incident')
                          == 'CONNECTION_FAILED' for o in ops)
        closed = any(storage.ambiguous_expired(o) for o in ops)
        result.update(status='BLOCKED', reason=(p.NOT_SENT_TEXT + ' ' if unreachable else '')
                      + (storage.AMBIGUOUS_EXPIRED_TEXT + ' ' if closed else '') + 'Évaluation interrompue. '
                      'Les réponses sont conservées ; aucun appel ne sera relancé automatiquement.')
    elif due:
        # Incident du fournisseur : l'évaluation reprend d'elle-même à la première échéance
        op, _, when = min(due.values(), key=lambda item: item[2])
        reason, retry_in = p.retry_notice(op, when)
        result.update(status='RUNNING', reason=reason, retry_in=retry_in)
    elif exhausted and all(o['state'] == 'RECEIVED' for o in ops):
        # Rien de dû, rien en cours, campagne non arrêtée : seule une série épuisée garde un incident relancé
        result.update(status='BLOCKED', reason=f'L’évaluation n’a pas abouti après {p.RETRY_ATTEMPTS} tentatives '
                      f'automatiques. {p._incident_reason(exhausted[0])} Les réponses et reçus sont conservés.')
    elif any(storage.ambiguous_expired(o) for o in latest):
        result.update(status='BLOCKED', reason=storage.AMBIGUOUS_EXPIRED_TEXT
                      + ' Les réponses et reçus sont conservés ; cette réponse n’est pas évaluée.')
    elif unusable or any(o['state'] == 'AMBIGUOUS' for o in ops):
        result.update(status='BLOCKED', reason='L’évaluation n’a pas fourni de preuves exploitables. Les réponses et reçus sont conservés.')
    elif ops:
        # Jugements restants clos sans envoi, ou campagne arrêtée depuis leur réservation (`judgment.execute`
        # les refuse alors avant émission) : seule une vraie interruption arrive ici
        # Chaque jugement se compare à sa propre campagne, source ou reprise
        current = {}
        for o in ops:
            if o['state'] != 'RECEIVED':
                saved = json.loads(o['resources'][0])
                cid = saved['request']['campaign_id']
                current.setdefault(cid, snapshot if cid == campaign_id else c._inspect(store, connection, cid))
        if all(o['state'] == 'RECEIVED' for o in ops) or any(
                current[json.loads(o['resources'][0])['request']['campaign_id']][key]
                != json.loads(o['resources'][0])['context']['campaign'][key]
                for o in ops if o['state'] != 'RECEIVED' for key in ('admission', 'stop_reason', 'restore_pending')):
            result.update(status='BLOCKED', reason='Évaluation interrompue. Aucun appel ne sera relancé automatiquement.')
        else:
            result['status'] = 'RUNNING'
    elif attempts and all(a['state'] == 'RECEIVED' for a in attempts):
        result['can_start'] = total > 0
        if not result['can_start']:
            result.update(status='BLOCKED', reason='Aucune réponse exploitable à évaluer.')
    elif attempts:
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
