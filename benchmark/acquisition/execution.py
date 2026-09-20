"""Émettre les tentatives admises et leurs seules reprises préautorisées"""
from collections.abc import Callable
from contextlib import closing
from copy import deepcopy
import hmac
import json
import logging
import os
import secrets
import sqlite3

from . import campaigns as c, recovery as r
from .. import storage
from ..validation import digest as value_digest
from ..storage import (Store, BudgetError, ConflictError, IntegrityError,
                       _fields, _transaction)


def _transport_view(request) -> dict:
    from .. import outgoing
    if request.get('outgoing_format') != outgoing.FORMAT:
        raise ValueError('Ancien format sortant : nouvelle version de tâche requise')
    content = request['outgoing']
    outgoing_view = outgoing.closed_candidate(dict(
        instruction=content['instruction'], deliverables=list(content['deliverables']),
        criteria=deepcopy(content['criteria']), acceptable_ambiguities=list(content['acceptable_ambiguities']),
        pieces=[dict(name=piece['name'], content=piece['content']) for piece in content['pieces']]))
    config = request['requested_configuration']
    conditions = request['conditions']
    defaults = conditions.get('defaults') or {}
    environment = conditions.get('environment') or {}
    pi = conditions['pi']
    return dict(
        outgoing_format=outgoing.FORMAT,
        outgoing=outgoing_view,
        requested_configuration=dict(
            provider=config['provider'], model=config['model'], revision=config['revision'],
            access=config['access'], channel_id=config['channel_id'], route=config['route'],
            parameters=deepcopy(config['parameters']), effort=config['effort'],
            required_observations=list(config['required_observations'])),
        conditions=dict(
            pi=dict(package=pi['package'], version=pi['version'], sha256=pi['sha256']),
            packages=list(conditions['packages']), tools=list(conditions['tools']),
            skills=list(conditions['skills']), context_sha256=conditions['context_sha256'],
            defaults={key: defaults[key] for key in ('system_prompt', 'timeout_seconds', 'context_window',
                                                     'max_output_tokens', 'defaults_source') if key in defaults},
            environment={key: environment[key] for key in ('node_version', 'node_sha256', 'bridge_sha256') if key in environment},
            frozen_at=conditions['frozen_at']))


def _transport_operation(operation):
    value = dict(operation_id=operation['operation_id'], phase=operation['phase'])
    if operation.get('state') == 'EMISSION_POSSIBLE':
        value['state'] = 'EMISSION_POSSIBLE'
    return value


def execute_launch(data, attempts, transport=None, *, transport_factory=None,
                   access_secret=None, access_transport=None):
    for attempt_id in attempts:
        try:
            execute(data, attempt_id, transport, transport_factory=transport_factory,
                    access_secret=access_secret, access_transport=access_transport)
        except (ValueError, ConflictError, BudgetError, IntegrityError) as error:
            # An interruption leaves the remaining intentions for private inspection
            logging.getLogger(__name__).warning('ACQUISITION_STOPPED operation=%s error=%s',
                                                attempt_id, type(error).__name__)
            with closing(Store(data)) as store:
                connection = c.connection_for(store)
                with _transaction(connection, write=True):
                    row = connection.execute(
                        'SELECT campaign_id FROM s4_attempts JOIN operations USING(operation_id) '
                        'JOIN s4_status USING(campaign_id) WHERE operation_id=? '
                        "AND state='INTENT_RECORDED' AND s4_status.admission_id IS NOT NULL", (attempt_id,)).fetchone()
                    if row:
                        c._stop(connection, row[0], 'ACQUISITION_STOPPED_BEFORE_EMISSION')
            break


def execute(data, attempt_id, transport: Callable[..., dict] | None = None, *,
            transport_factory: Callable[..., Callable[..., dict]] | None = None,
            access_secret=None, access_transport=None):
    """One explicit worker, one durable boundary, one callback; never an implicit retry."""
    if not callable(transport) and not callable(transport_factory):
        raise ValueError('Transport injecté par le lanceur de confiance requis')
    from ..runtime import worker_lock
    received = False
    with closing(storage.Store(data)) as store, worker_lock(store, shared=True):
        c._intact(store)
        connection = c.connection_for(store)
        requester_key = None
        session_id = None
        with _transaction(connection):
            row = connection.execute('SELECT campaign_id FROM s4_attempts WHERE operation_id=?',
                                     (attempt_id,)).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            preview = c._inspect(store, connection, row[0])
            if preview['manifest'].get('funding', 'operator') == 'requester':
                session_id = connection.execute('SELECT session_id FROM s2_dossiers WHERE dossier_id=?',
                                                (preview['task']['dossier_id'],)).fetchone()[0]
        if session_id is not None:
            from ..provider_access import key_for_session
            requester_key = key_for_session(store, session_id, access_secret, access_transport)
        with _transaction(connection, write=True):
            row = connection.execute('SELECT campaign_id FROM s4_attempts WHERE operation_id=?', (attempt_id,)).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            snapshot = c._inspect(store, connection, row[0])
            attempt = next(a for a in snapshot['attempts'] if a['operation_id'] == attempt_id)
            admission = snapshot['admission']
            if attempt['state'] != 'INTENT_RECORDED' or admission is None or attempt['cell_id'] not in admission['authority']['allowed_cells']:
                raise ConflictError('Tentative non admise ou déjà émise')
            c._eligible(store, connection, snapshot, admission['authority'], admission['evidence'])
            if attempt['engine_source'] != c._engine():
                raise ConflictError('Source moteur modifiée depuis la réservation')
            order = snapshot['manifest']['attempt_policy']['order']
            states = {c['cell_id']: c['state'] for c in snapshot['cells']}
            if any(states[cid] != 'RECEIVED' for cid in order[:order.index(attempt['cell_id'])]):
                raise ConflictError('Ordre de tentative non respecté')
            raw = connection.execute('SELECT request_json FROM s4_attempts WHERE operation_id=?', (attempt_id,)).fetchone()[0]
            request = json.loads(raw)
            closed_request = _transport_view(request)
            closed_operation = _transport_operation(attempt['operation'])
            if transport_factory is not None:
                if snapshot['manifest'].get('funding', 'operator') == 'requester':
                    from ..provider_access import authorize_session, decrypt
                    authorize_session(connection, session_id)
                    row = connection.execute("SELECT key_cipher FROM s2_provider_access WHERE session_id=? AND status='connected'",
                                             (session_id,)).fetchone()
                    from ..preparation import Denied
                    if row is None or requester_key is None:
                        raise Denied('ACCESS_REQUIRED')
                    try:
                        current_key = decrypt(access_secret, row[0], session_id, 'key')
                    except IntegrityError:
                        raise Denied('ACCESS_UNAVAILABLE') from None
                    if not hmac.compare_digest(current_key.encode(), requester_key.encode()):
                        raise Denied('ACCESS_REQUIRED')
                    transport = transport_factory(
                        closed_request['requested_configuration']['channel_id'], requester_key)
                else:
                    transport = transport_factory(
                        closed_request['requested_configuration']['channel_id'])
            if not callable(transport):
                raise ValueError('Transport injecté par le lanceur de confiance requis')
            if hasattr(transport, 'prepare'):
                getattr(transport, 'prepare')(deepcopy(closed_operation), deepcopy(closed_request))
            connection.execute('INSERT INTO s4_emissions VALUES (?,?,?)', (attempt_id, admission['admission_id'], c._now()))
            # Same S1 transition as mark_emission_possible, in the transaction that
            # also freezes the admission actually used by this worker
            operation = store._operation_for_update(connection, attempt_id, ('INTENT_RECORDED',))
            connection.execute("UPDATE operations SET state='EMISSION_POSSIBLE' WHERE operation_id=?", (attempt_id,))
        operation['state'] = 'EMISSION_POSSIBLE'
        logging.getLogger(__name__).info('ACQUISITION_EMITTING operation=%s', attempt_id)
        try:
            response = deepcopy(transport(_transport_operation(operation), deepcopy(closed_request)))
            _fields(response, ('receipt', 'cost'), 'transport response')
            receipt, cost = response['receipt'], response['cost']
            receipt['resources_seen'] = [p['id'] for p in request['pieces']]
            c._result(receipt, cost)
            with _transaction(connection, write=True):
                # A stop during the callback must not discard the late receipt
                store._record_receipt(connection, attempt_id, receipt, cost)
                output = receipt['result']['output']
                output_id = None
                if output is not None:
                    output_id = 'output-' + secrets.token_hex(16)
                    store._put_piece(connection, operation['dossier_id'], operation['revision'], output_id,
                                     name='Sortie brute ' + attempt_id, role='judge', media_type='text/plain; charset=utf-8', content=output.encode('utf-8'))
                connection.execute('INSERT INTO s4_results VALUES (?,?,?,?,?)',
                                   (attempt_id, output_id, value_digest(receipt), value_digest(cost), c._now()))
                incomplete = (c._attribution(receipt, request['requested_configuration'])
                              or (cost['status'] == 'UNKNOWN'
                                  and snapshot['manifest'].get('financial_cost_policy') != 'retain_reserve')
                              or receipt['result']['emission'] != 'ESTABLISHED')
                if incomplete:
                    connection.execute('UPDATE s4_status SET admission_id=NULL, stop_reason=?, stopped_at=? WHERE campaign_id=?',
                                       ('ACQUISITION_EVIDENCE_INCOMPLETE', c._now(), snapshot['manifest']['campaign_id']))
            received = True
            logging.getLogger(__name__).info('ACQUISITION_RECEIVED operation=%s usable=%s cost=%s',
                attempt_id, output is not None and not incomplete, cost['status'])
        except Exception as error:
            # Exception text can contain private bytes. Preserve a fixed technical
            # reason; neither an unusable response nor an exception settles cost
            with _transaction(connection, write=True):
                op = store._operation_for_update(connection, attempt_id, ('EMISSION_POSSIBLE', 'AMBIGUOUS'))
                if op['state'] == 'EMISSION_POSSIBLE':
                    connection.execute("UPDATE operations SET state='AMBIGUOUS', ambiguity_reason=? WHERE operation_id=?",
                                       ('ACQUISITION_RECEIPT_NOT_VERIFIED', attempt_id))
                connection.execute('UPDATE s4_status SET admission_id=NULL, stop_reason=?, stopped_at=? WHERE campaign_id=?',
                                   ('ACQUISITION_RECEIPT_NOT_VERIFIED', c._now(), snapshot['manifest']['campaign_id']))
            logging.getLogger(__name__).error('ACQUISITION_AMBIGUOUS operation=%s error=%s',
                                              attempt_id, type(error).__name__)
    if received:
        continue_preauthorized(data, attempt_id, transport,
                               transport_factory=transport_factory)


def _derive_authority(owner_record, snapshot, operation_id, reserve_amount, budget_id):
    owner = owner_record['authority']
    cell = snapshot['manifest']['plan'][0]['cell_id']
    return dict(
        actor=owner['actor'], authority_id=owner['authority_id'], purpose='start',
        manifest_sha256=snapshot['manifest_sha256'], execution_authority=owner['execution_authority'],
        candidate_authority=owner['candidate_authority'], budget_authority=owner['budget_authority'],
        budget_id=budget_id, allowed_cells=[cell], reserve_amounts={cell: reserve_amount},
        derived_from=dict(admission_id=owner_record['admission_id'], operation_id=operation_id,
                          manifest_sha256=owner['manifest_sha256']))


def _derive_evidence(owner_record, snapshot, grant):
    source = owner_record['evidence']
    config = snapshot['manifest']['panel'][0]
    if 'official_fallback' in snapshot['manifest']:
        channel = r._official_grant(grant, config['id'])['channel']
    else:
        channel = deepcopy(source['channels'][config['id']])
        channel['route'] = config['route']
    return dict(pi_sha256=source['pi_sha256'], context_sha256=source['context_sha256'],
                channels={config['id']: channel}, confinement=deepcopy(source['confinement']))


def _next_preauthorized_attempt(store, operation_id, *, allow_official=False):
    c._intact(store)
    connection = c.connection_for(store)
    try:
        with _transaction(connection, write=True):
            snapshot, attempt = r.parent(store, connection, operation_id)
            if (attempt['state'] != 'RECEIVED'
                    or snapshot['admission'] is None
                    or os.path.lexists(store._root / 'restore.json')):
                return None
            owner, grant = r._owner_grant(store, connection, snapshot)
            if grant is None:
                return None
            try:
                observed = r.observation(attempt)
            except (ValueError, ConflictError, IntegrityError):
                return None
            if observed['kind'] not in r._RECOVERABLE:
                return None
            try:
                proposal = r._automatic_proposal(store, connection, operation_id, grant,
                                               allow_official=allow_official)
            except (ValueError, BudgetError):
                return None
            cid = proposal['manifest']['campaign_id']
            try:
                c._create(store, connection, deepcopy(proposal['manifest']))
            except ConflictError:
                existing = c._inspect(store, connection, cid)
                if existing['manifest_sha256'] != value_digest(proposal['manifest']):
                    return None
            snap = c._inspect(store, connection, cid)
            if snap['admission'] is None:
                if snap['admissions']:
                    return None
                authority = _derive_authority(owner, snap, operation_id,
                                              proposal['reserve_amount'], proposal['budget_id'])
                evidence = _derive_evidence(owner, snap, grant)
                c._admit(store, connection, cid, authority, evidence)
                snap = c._inspect(store, connection, cid)
            admission = snap['admission']
            if admission is None:
                raise ConflictError('Admission dérivée absente')
            cell = snap['manifest']['plan'][0]['cell_id']
            existing_attempt = next((row for row in snap['attempts'] if row['cell_id'] == cell), None)
            if existing_attempt is not None:
                if existing_attempt['state'] != 'INTENT_RECORDED':
                    return None
                return existing_attempt['operation_id']
            oid = 'recovery-' + value_digest([cid, cell, admission['admission_id']])[:40]
            try:
                c._reserve(store, connection, snap, cell, oid)
            except sqlite3.IntegrityError:
                snap = c._inspect(store, connection, cid)
                existing_attempt = next((row for row in snap['attempts'] if row['cell_id'] == cell), None)
                if existing_attempt and existing_attempt['state'] == 'INTENT_RECORDED':
                    return existing_attempt['operation_id']
                raise
            return oid
    except (ValueError, KeyError, ConflictError, BudgetError, IntegrityError, sqlite3.IntegrityError):
        return None


def continue_preauthorized(data, operation_id, transport=None, *, transport_factory=None):
    """Create, admit, reserve and execute the next frozen recovery, or stop"""
    try:
        with closing(Store(data)) as store:
            nxt = _next_preauthorized_attempt(store, operation_id,
                                              allow_official=transport_factory is not None)
        if nxt is None:
            return
        if transport_factory is None:
            execute(data, nxt, transport)
        else:
            execute(data, nxt, transport_factory=transport_factory)
    except (ValueError, KeyError, ConflictError, BudgetError, IntegrityError):
        return
