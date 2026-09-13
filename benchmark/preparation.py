"""Private fictional preparation on S1 with an operator-injected transport."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import hmac
import json
import os
import re
import secrets
from urllib.parse import urlsplit, parse_qsl

from .storage import (Store, SchemaError, IntegrityError, ConflictError, BudgetError,
                      _transaction, _strict_json as encode, _fields, _text,
                      _identity, _money, _sum_money, _unique_object, _payload_json)


class Denied(ValueError):
    pass


def identifier(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is None:
        raise ValueError('Identifiant invalide')
    return value


def connection_for(store):
    connection = store._s1_connection()
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s2_control'").fetchone():
        raise SchemaError('Initialisation explicite S2 requise')
    return connection


def admission(store, connection=None):
    connection = connection if connection is not None else connection_for(store)
    raw = connection.execute('SELECT admission_json FROM s2_control WHERE singleton=1').fetchone()[0]
    if raw is None:
        return None
    result = json.loads(raw, object_pairs_hook=_unique_object)
    check_authority(result)
    return result


def availability(store, transport):
    """Read-only projection of preparation gates, without configuration or secrets"""
    connection = connection_for(store)
    with _transaction(connection):
        authority = admission(store, connection)
        configured = bool(transport)
        reason = 'open'
        pending = connection.execute(
            "SELECT o.state FROM s2_actions a JOIN operations o USING(operation_id) "
            "WHERE o.state != 'RECEIVED'").fetchall()
        if os.path.lexists(store._root / 'restore.json'):
            reason = 'restore'
        elif any(row[0] == 'AMBIGUOUS' for row in pending):
            reason = 'interrupted'
        elif pending:
            reason = 'waiting' if authority and configured else 'interrupted'
        elif authority is None:
            reason = 'closed'
        elif not configured:
            reason = 'unconfigured'
        else:
            operations = store._operations(connection)
            budget = store._budget(connection, authority['budget_id'], operations)
            if (store._blocking_costs(operations, budget, 'preparation') or any(
                    row['budget_id'] == authority['budget_id'] and row['state'] in ('EMISSION_POSSIBLE', 'AMBIGUOUS')
                    for row in operations)):
                reason = 'unresolved'
            elif _money(authority['reserve_amount']) > Decimal(budget['available']):
                reason = 'budget'
        return {'assistant_configured': configured, 'admission_open': authority is not None,
                'can_submit': reason == 'open', 'reason': reason}


def check_authority(value):
    _fields(value, ('authority_id', 'budget_id', 'reserve_amount', 'requested_configuration'), 'admission')
    _text(value['authority_id'], 'authority_id')
    _text(value['budget_id'], 'budget_id')
    _money(value['reserve_amount'])
    if type(value['requested_configuration']) is not dict or not value['requested_configuration']:
        raise ValueError('Configuration requise')
    encode(value)


def close_admission(store):
    connection = store._s1_connection()
    if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s2_control'").fetchone():
        with _transaction(connection, write=True):
            connection.execute('UPDATE s2_control SET admission_json=NULL WHERE singleton=1 AND admission_json IS NOT NULL')


def admit(store, authority):
    check_authority(authority)
    connection = connection_for(store)
    with _transaction(connection, write=True):
        if os.path.lexists(store._root / 'restore.json'):
            raise Denied('Restauration à rapprocher')
        store._budget(connection, authority['budget_id'], store._operations(connection))
        connection.execute('UPDATE s2_control SET admission_json=? WHERE singleton=1', (encode(authority),))


def session(store, token, *, create=False):
    connection = connection_for(store)
    raw = None
    if type(token) is str and re.fullmatch('[0-9a-f]{64}', token):
        raw = bytes.fromhex(token)
        row = connection.execute('SELECT session_id FROM s2_sessions WHERE token_sha256=?',
                                 (sha256(raw).hexdigest(),)).fetchone()
        if row:
            return row[0], sha256(b'csrf:' + raw).hexdigest(), token
    if not create:
        raise Denied('Session requise')
    raw = secrets.token_bytes(32)
    session_id = secrets.token_hex(16)
    with _transaction(connection, write=True):
        connection.execute('INSERT INTO s2_sessions VALUES (?, ?)', (session_id, sha256(raw).hexdigest()))
    return session_id, sha256(b'csrf:' + raw).hexdigest(), raw.hex()


def owner(connection, session_id, dossier_id):
    identifier(dossier_id)
    row = connection.execute('SELECT current_revision FROM s2_dossiers WHERE dossier_id=? AND session_id=?',
                             (dossier_id, session_id)).fetchone()
    if not row:
        raise Denied('Dossier inaccessible')
    return row[0]


def binding(dossier_id, revision, digest):
    return {'dossier_id': dossier_id, 'revision': revision, 'package_sha256': digest}


def package_check(store, dossier_id, revision, package, digest):
    if sha256(encode(package).encode()).hexdigest() != digest:
        raise IntegrityError('Empreinte du paquet divergente')
    _fields(package, ('instruction', 'deliverables', 'criteria', 'acceptable_ambiguities',
                      'human_work', 'limits', 'pieces') + (('outgoing_format',) if 'outgoing_format' in package else ()), 'package')
    if 'outgoing_format' in package:
        from .outgoing import FORMAT
        if package['outgoing_format'] != FORMAT:
            raise IntegrityError('Format sortant inconnu')
    for field in ('instruction', 'human_work'):
        _text(package[field], field)
    for field in ('deliverables', 'criteria', 'acceptable_ambiguities', 'limits'):
        if type(package[field]) is not list:
            raise IntegrityError('Liste de présentation requise')
        for text in package[field]:
            _text(text, field)
    if not package['deliverables'] or not package['criteria'] or not package['pieces']:
        raise IntegrityError('Paquet incomplet')
    ids = set()
    for piece in package['pieces']:
        _fields(piece, ('id', 'name', 'sha256', 'size_bytes'), 'piece')
        identifier(piece['id'])
        if piece['id'] in ids:
            raise IntegrityError('Pièce répétée')
        ids.add(piece['id'])
        meta = store.get_piece(piece['id'])
        if (meta['dossier_id'], meta['revision'], meta['role']) != (dossier_id, revision, 'candidate'):
            raise IntegrityError('Pièce étrangère ou réservée')
        if piece != {key: meta['piece_id'] if key == 'id' else meta[key] for key in piece}:
            raise IntegrityError('Métadonnées divergentes')
        store.read_piece(piece['id'])
    actual = {row[0] for row in store._connection.execute(
        "SELECT piece_id FROM pieces WHERE dossier_id=? AND revision=? AND role='candidate'",
        (dossier_id, revision))}
    if actual != ids:
        raise IntegrityError('Pièces candidates hors du paquet')
    return sorted(ids)


def view(store, session_id, dossier_id, revision=None):
    connection = connection_for(store)
    with store.read_snapshot() as connection:
        current = owner(connection, session_id, dossier_id)
        if revision is None:
            revision = current
        _identity(dossier_id, revision)
        row = connection.execute('SELECT stage,explanation,package_json,package_sha256,changes_json,checks_json '
                                 'FROM s2_revisions WHERE dossier_id=? AND revision=?', (dossier_id, revision)).fetchone()
        if not row:
            raise Denied('Révision inaccessible')
        stage, explanation, raw, digest, changes, checks = row
        package = None if raw is None else json.loads(raw)
        if package is not None:
            package_check(store, dossier_id, revision, package, digest)
        validated = connection.execute('SELECT 1 FROM s2_validations WHERE dossier_id=? AND revision=? '
                                       'AND package_sha256=? AND session_id=?',
                                       (dossier_id, revision, digest, session_id)).fetchone()
        result = dict(dossier_id=dossier_id, revision=revision, payload=store.get_dossier(dossier_id, revision),
                      stage=stage, explanation=explanation, package=package, package_sha256=digest,
                      fictional=True, validation=binding(dossier_id, revision, digest) if validated else None,
                      qualified=False, changes=json.loads(changes), checks=json.loads(checks))
        result['example_contents'] = {piece['id']: store.read_piece(piece['id']).decode('utf-8')
                                      for piece in (package or {}).get('pieces', [])}
        result['rechecked'] = result['checks'].get('fields', [])
        completed = connection.execute('SELECT a.request_json,o.observed_cost_json,o.operation_id FROM s2_actions a '
                                       'JOIN operations o USING(operation_id) WHERE a.dossier_id=? '
                                       'AND a.input_revision=? AND o.state=?',
                                       (dossier_id, revision - 1, 'RECEIVED')).fetchone()
        result['message'] = None if completed is None else json.loads(completed[0])
        result['observed_cost'] = None if completed is None else json.loads(completed[1])
        if completed is not None:
            operation = store._operation_for_update(connection, completed[2], ('RECEIVED',))
            observed = operation['receipt']['observed_configuration'] or {}
            if 'indicative_cost' in observed:
                result['indicative_cost'] = observed['indicative_cost']
            reconciliation = store._reconciliation(connection, operation)
            result['effective_cost'] = store._effective_cost(connection, operation)
            result['cost_reconciliation'] = None if reconciliation is None else {
                'sha256': reconciliation['sha256'], 'actor': reconciliation['proof']['actor'],
                'source': reconciliation['proof']['source']['name'],
                'observed_at': reconciliation['proof']['source']['observed_at']}
        # Historic views are stable; only the current view projects an unfinished action
        if revision == current:
            pending = connection.execute('SELECT o.state FROM s2_actions a JOIN operations o USING(operation_id) '
                                         'WHERE a.dossier_id=? AND a.input_revision=? AND o.state!=?',
                                         (dossier_id, revision, 'RECEIVED')).fetchall()
            if pending:
                ambiguous = any(r[0] == 'AMBIGUOUS' for r in pending)
                blocked_intent = (any(r[0] == 'INTENT_RECORDED' for r in pending)
                                  and (admission(store, connection) is None or os.path.lexists(store._root / 'restore.json')))
                result['stage'] = 'suspended' if ambiguous or blocked_intent else 'waiting'
                result['explanation'] = (
                    'Effets inconnus : préparation suspendue, aucun rejeu autorisé.' if ambiguous else
                    'Admission fermée : intention conservée sans émission ni reprise automatique.' if blocked_intent else
                    'Préparation en attente. Actualisez pour consulter son avancement ; aucun appel ne sera relancé.')
                result['validation'] = None
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s3_control'").fetchone():
            from .qualification import projection
            result['qualification'] = projection(store, connection, dossier_id, revision,
                                                  eligible=result['validation'] is not None)
            result['qualified'] = result['qualification']['status'] in ('QUALIFIED', 'APPROVED')
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s4_control'").fetchone():
            from .campaigns import projection
            result['campaigns'] = projection(store, connection, dossier_id)
            if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s5_control'").fetchone():
                from .evaluation import projection as evaluations
                for campaign in result['campaigns']:
                    campaign['evaluations'] = evaluations(store, connection, dossier_id, campaign['campaign_id'])
        return result


def piece_bytes(store, session_id, dossier_id, revision, piece_id):
    identifier(piece_id)
    current = view(store, session_id, dossier_id, revision)
    if current['package'] is None or piece_id not in {p['id'] for p in current['package']['pieces']}:
        raise Denied('Pièce inaccessible')
    return store.read_piece(piece_id)


def _usd_budget(reserved_amount, requested, budget):
    if 'reserve_usd' not in requested:
        return
    if (budget['currency'] != 'USD' or _money(budget['limit']) > Decimal('100')
            or (requested['reserve_usd'] is not None
                and _money(reserved_amount) < _money(requested['reserve_usd']))):
        raise ValueError('Configuration ou réservation OpenRouter divergente')


def _closed_preparation_request(request):
    from . import outgoing
    return dict(outgoing_format=outgoing.FORMAT, outgoing=outgoing.preparation(request))


def _closed_preparation_operation(operation, *, conserved_wire=None, state=None):
    value = dict(operation_id=operation['operation_id'], phase=operation['phase'],
                 requested_configuration=deepcopy(operation['requested_configuration']))
    resolved = operation['state'] if state is None else state
    if resolved == 'EMISSION_POSSIBLE':
        value['state'] = resolved
    if conserved_wire is not None:
        value['conserved_wire'] = conserved_wire
    return value


def submit(store, session_id, dossier_id, body, source, transport):
    connection = connection_for(store)
    identifier(dossier_id)
    identifier(body['action_id'])
    create = 'request' in body
    kind = 'create' if create else body['kind']
    if kind not in ('create', 'clarify', 'correct'):
        raise ValueError('Action inconnue')
    message = body['request'] if create else body['message']
    _text(message, 'message')
    if not message.strip():
        raise ValueError('Message vide')
    if not create:
        _identity(dossier_id, body['revision'])
    request_json = encode(body)
    with _transaction(connection, write=True):
        existing = connection.execute('SELECT session_id,current_revision FROM s2_dossiers WHERE dossier_id=?',
                                      (dossier_id,)).fetchone()
        if existing and existing[0] != session_id:
            raise Denied('Dossier inaccessible')
        if not create and not existing:
            raise Denied('Dossier inaccessible')
        if existing:
            previous = connection.execute('SELECT request_json,operation_id FROM s2_actions WHERE dossier_id=? AND action_id=?',
                                          (dossier_id, body['action_id'])).fetchone()
            if previous:
                if previous[0] != request_json:
                    raise ConflictError('Identité déjà utilisée avec un autre contenu')
                return previous[1], False
            if create:
                raise ConflictError('Dossier déjà créé')
            if body['revision'] != existing[1]:
                raise ConflictError('Révision périmée')
        authority = admission(store, connection)
        if not authority or not transport or os.path.lexists(store._root / 'restore.json'):
            raise Denied('Admission fermée ou transport absent')
        # S2 admits one effect at a time; no restart drains a durable queue
        if connection.execute("SELECT 1 FROM s2_actions a JOIN operations o USING(operation_id) "
                              "WHERE o.state != 'RECEIVED' LIMIT 1").fetchone():
            raise ConflictError('Préparation active ou suspendue')
        budget = store._budget(connection, authority['budget_id'], store._operations(connection))
        _usd_budget(authority['reserve_amount'], authority['requested_configuration'], budget)
        revision = existing[1] if existing else 1
        if create:
            payload = dict(request=message, clarifications=[], reformulation='', validated_assumptions=[],
                           fictional_parameters={}, state='EN_ATTENTE')
            store.save_dossier(dossier_id, revision, payload)
            connection.execute('INSERT INTO s2_dossiers VALUES (?,?,?)', (dossier_id, session_id, revision))
            connection.execute('INSERT INTO s2_revisions VALUES (?,?,?, ?,NULL,NULL,?,?)',
                               (dossier_id, revision, 'draft', '', '[]', '{}'))
        operation_id = secrets.token_hex(16)
        context = store.get_dossier(dossier_id, revision)
        row = connection.execute('SELECT stage,explanation,package_json,package_sha256 FROM s2_revisions WHERE dossier_id=? AND revision=?',
                                 (dossier_id, revision)).fetchone()
        # Exact context lives in immutable operation resources, distinct from the candidate package
        request = dict(message=message, kind=kind, payload=context, stage=row[0], explanation=row[1],
                       package=None if row[2] is None else json.loads(row[2]))
        request['pieces_seen'] = []
        if request['package'] is not None:
            package_check(store, dossier_id, revision, request['package'], row[3])
        for piece in (request['package'] or {}).get('pieces', []):
            request['pieces_seen'].append({'id': piece['id'], 'name': piece['name'], 'role': 'candidate', 'sha256': piece['sha256'],
                                           'content': store.read_piece(piece['id']).decode('utf-8')})
        resources = [encode(request)]
        operation = dict(operation_id=operation_id, phase='correction' if kind == 'correct' else 'preparation',
                         dossier_id=dossier_id, revision=revision, authority=authority['authority_id'],
                         engine_version=source, requested_configuration=authority['requested_configuration'], resources=resources)
        store._reserve_intent(connection, operation, authority['budget_id'], authority['reserve_amount'])
        if callable(getattr(transport, 'prepare', None)):
            # A refused body rolls back the dossier, intention and reserve together
            operation = store._operation_for_update(connection, operation_id, ('INTENT_RECORDED',))
            wire = transport.prepare(_closed_preparation_operation(operation), _closed_preparation_request(request))
            _text(wire, 'prepared request')
            operation['resources'].append(wire)
            connection.execute('UPDATE operations SET resources_json=? WHERE operation_id=?',
                               (encode(operation['resources']), operation_id))
        connection.execute('INSERT INTO s2_actions VALUES (?,?,?,?,?,?)',
                           (dossier_id, body['action_id'], revision, kind, request_json, operation_id))
        return operation_id, True


def validate(store, session_id, dossier_id, body):
    _identity(dossier_id, body['revision'])
    if body['dossier_id'] != dossier_id:
        raise ConflictError('Dossier divergent')
    if type(body['package_sha256']) is not str or re.fullmatch('[0-9a-f]{64}', body['package_sha256']) is None:
        raise ValueError('Empreinte invalide')
    connection = connection_for(store)
    with _transaction(connection, write=True):
        revision = owner(connection, session_id, dossier_id)
        if revision != body['revision']:
            raise ConflictError('Révision périmée')
        if connection.execute("SELECT 1 FROM s2_actions a JOIN operations o USING(operation_id) "
                              "WHERE a.dossier_id=? AND o.state!='RECEIVED'", (dossier_id,)).fetchone():
            raise ConflictError('Préparation inachevée')
        stage, raw, digest = connection.execute('SELECT stage,package_json,package_sha256 FROM s2_revisions '
                                               'WHERE dossier_id=? AND revision=?', (dossier_id, revision)).fetchone()
        if stage != 'preview' or raw is None or digest != body['package_sha256']:
            raise ConflictError('Paquet divergent ou non validable')
        package_check(store, dossier_id, revision, json.loads(raw), digest)
        connection.execute('INSERT OR IGNORE INTO s2_validations VALUES (?,?,?,?,?)',
                           (dossier_id, revision, digest, session_id, datetime.now(timezone.utc).isoformat()))
        return binding(dossier_id, revision, digest)


def execute(data, operation_id, transport):
    """Only the submitting executor starts this work, never inspection or startup."""
    with closing(Store(data)) as store:
        connection = connection_for(store)
        emitted = False
        try:
            proof = store.verify_storage()
            if not proof['integrity_ok'] or proof['orphan_files']:
                close_admission(store)
                return
            with _transaction(connection, write=True):
                operation = store._operation_for_update(connection, operation_id, ('INTENT_RECORDED',))
                authority = admission(store, connection)
                if (transport is None or authority is None or os.path.lexists(store._root / 'restore.json')
                        or authority != dict(authority_id=operation['authority'], budget_id=operation['budget_id'],
                                             reserve_amount=operation['reserved_amount'],
                                             requested_configuration=operation['requested_configuration'])):
                    return
                budget = store._budget(connection, operation['budget_id'], store._operations(connection))
                _usd_budget(operation['reserved_amount'], operation['requested_configuration'], budget)
                if store._blocking_costs(store._operations(connection), budget, operation['phase']) or _sum_money((_money(budget['reserved']), _money(budget['spent']))) > _money(budget['limit']):
                    return
                if any(row['operation_id'] != operation_id and row['budget_id'] == operation['budget_id']
                       and row['state'] in ('EMISSION_POSSIBLE', 'AMBIGUOUS') for row in store._operations(connection)):
                    return
                request = json.loads(operation['resources'][0])
                closed_request = _closed_preparation_request(request)
                closed_operation = _closed_preparation_operation(operation)
                if callable(getattr(transport, 'prepare', None)):
                    prepared = transport.prepare(deepcopy(closed_operation), deepcopy(closed_request))
                    if len(operation['resources']) != 2 or prepared != operation['resources'][1]:
                        raise ConflictError('Contenu sortant modifié depuis la réservation')
                connection.execute("UPDATE operations SET state='EMISSION_POSSIBLE' WHERE operation_id=?", (operation_id,))
            emitted = True
            operation['state'] = 'EMISSION_POSSIBLE'
            request = json.loads(operation['resources'][0])
            closed_request = _closed_preparation_request(request)
            response = transport(_closed_preparation_operation(operation, conserved_wire=(operation['resources'][1] if len(operation['resources']) > 1 else None)),
                                 deepcopy(closed_request))
            _fields(response, ('receipt', 'cost'), 'transport response')
            try:
                publish(store, operation, request, response)
            except Exception:
                # Publication rolled back; keep the original receipt with an unusable revision
                # RECEIVED and closed admission become visible in the same commit
                with _transaction(connection, write=True):
                    dossier_id, before = operation['dossier_id'], operation['revision']
                    current = connection.execute('SELECT current_revision FROM s2_dossiers WHERE dossier_id=?',
                                                 (dossier_id,)).fetchone()[0]
                    if current != before:
                        raise ConflictError('Révision changée pendant la préparation')
                    revision = before + 1
                    store._record_receipt(connection, operation_id, response['receipt'], response['cost'])
                    store.save_dossier(dossier_id, revision, request['payload'])
                    connection.execute('INSERT INTO s2_revisions VALUES (?,?,?,?,NULL,NULL,?,?)',
                                       (dossier_id, revision, 'suspended',
                                        'Résultat reçu non utilisable : préparation suspendue. '
                                        'Reçu et coût conservés ; aucune reprise automatique.',
                                        encode(['stage', 'explanation']), encode({'result_verified': False})))
                    connection.execute('UPDATE s2_dossiers SET current_revision=? WHERE dossier_id=?',
                                       (revision, dossier_id))
                    connection.execute('UPDATE s2_control SET admission_json=NULL WHERE singleton=1')
        except Exception:
            # Never log request/response/exception text, which may contain private data
            if emitted:
                store.mark_ambiguous(operation_id, 'PREPARATION_RESULT_NOT_VERIFIED')
            else:
                close_admission(store)


def publish(store, operation, request, response):
    result = response['receipt']['result']
    _fields(result, ('stage', 'explanation', 'reformulation', 'fictional_parameters', 'package'), 'preparation result')
    stage = result['stage']
    if stage not in ('clarification', 'preview', 'scope_confirmation', 'suspended'):
        raise ValueError('État inconnu')
    if type(result['explanation']) is not str or (stage != 'preview' and not result['explanation']):
        raise ValueError('Explication requise')
    if (stage == 'preview') != (result['package'] is not None):
        raise ValueError('Paquet incohérent avec l’état')
    dossier_id, before = operation['dossier_id'], operation['revision']
    payload = deepcopy(request['payload'])
    if request['kind'] == 'clarify':
        payload['clarifications'].append(request['message'])
        # The answer remains attributed user input, never an assistant-invented agreement
        payload['validated_assumptions'].append({'question': request['explanation'], 'answer': request['message']})
    payload['reformulation'] = result['reformulation']
    payload['fictional_parameters'] = result['fictional_parameters']
    _payload_json(payload)
    generated = None
    if result['package'] is not None:
        from . import outgoing
        generated = outgoing.closed_generation(result['package'])
    connection = connection_for(store)
    with _transaction(connection, write=True):
        current = connection.execute('SELECT current_revision FROM s2_dossiers WHERE dossier_id=?', (dossier_id,)).fetchone()[0]
        if current != before:
            raise ConflictError('Révision changée pendant la préparation')
        revision = before + 1
        store.save_dossier(dossier_id, revision, payload)
        package, digest, checks = None, None, {}
        if generated is not None:
            from .outgoing import FORMAT
            package = dict(instruction=generated['candidate']['instruction'],
                           deliverables=list(generated['candidate']['deliverables']),
                           criteria=list(generated['candidate']['criteria']),
                           acceptable_ambiguities=list(generated['candidate']['acceptable_ambiguities']),
                           human_work=generated['internal']['human_work'],
                           limits=list(generated['internal']['limits']), outgoing_format=FORMAT, pieces=[])
            checked = []
            for role, items in (('candidate', generated['candidate']['pieces']),
                                ('judge', generated['judgment']['pieces'])):
                for piece in items:
                    raw = piece['content'].encode('utf-8')
                    meta = store._put_piece(connection, dossier_id, revision, secrets.token_hex(16),
                                           name=piece['name'], role=role, media_type='text/plain; charset=utf-8', content=raw)
                    if store.read_piece(meta['piece_id']) != raw:
                        raise IntegrityError('Pièce divergente')
                    checked.append({'id': meta['piece_id'], 'sha256': meta['sha256']})
                    if role == 'candidate':
                        package['pieces'].append(dict(id=meta['piece_id'], name=meta['name'],
                                                      sha256=meta['sha256'], size_bytes=meta['size_bytes']))
            digest = sha256(encode(package).encode()).hexdigest()
            package_check(store, dossier_id, revision, package, digest)
            # Do not expose judge piece identities in the requester projection
            checks = {'method': 'stored-bytes-sha256-and-package-structure/v1', 'package_sha256': digest,
                      'pieces': [item for item in checked if item['id'] in {p['id'] for p in package['pieces']}],
                      'reference_bytes_verified': True, 'reference_qualification': 'NON VÉRIFIÉ'}
        old_package = request['package'] or {}
        changes = [key for key in (package or {}) if (package or {})[key] != old_package.get(key)]
        if package is None:
            changes = ['stage', 'explanation']
        checks['fields'] = changes
        connection.execute('INSERT INTO s2_revisions VALUES (?,?,?,?,?,?,?,?)',
                           (dossier_id, revision, stage, result['explanation'], None if package is None else encode(package),
                            digest, encode(changes), encode(checks)))
        if (operation['requested_configuration'].get('provider') == 'OpenRouter'
                and type(response['receipt']['observed_configuration']) is dict):
            from .openrouter_prices import indication
            observed = response['receipt']['observed_configuration']
            consumption = observed.get('consumption')
            observed['indicative_cost'] = indication(
                operation['requested_configuration'].get('reservation_estimate'),
                consumption.get('usage') if type(consumption) is dict else None)
        store._record_receipt(connection, operation['operation_id'], response['receipt'], response['cost'])
        connection.execute('UPDATE s2_dossiers SET current_revision=? WHERE dossier_id=?', (revision, dossier_id))


def verify_preparation(store, connection):
    """Check the S2 joins and actual package bytes in the caller's snapshot."""
    admission(store, connection)
    for dossier_id, session_id, current in connection.execute('SELECT * FROM s2_dossiers').fetchall():
        identifier(dossier_id)
        if not connection.execute('SELECT 1 FROM s2_revisions WHERE dossier_id=? AND revision=?', (dossier_id, current)).fetchone():
            raise IntegrityError('Pointeur S2 sans révision publiée')
    for dossier_id, revision, stage, explanation, raw, digest, changes, checks in connection.execute('SELECT * FROM s2_revisions').fetchall():
        if not connection.execute('SELECT 1 FROM s2_dossiers WHERE dossier_id=?', (dossier_id,)).fetchone():
            raise IntegrityError('Révision sans propriétaire')
        if type(json.loads(changes)) is not list or type(json.loads(checks)) is not dict:
            raise IntegrityError('Contrôles de révision invalides')
        if raw is not None:
            package_check(store, dossier_id, revision, json.loads(raw), digest)
    for dossier_id, revision, digest, session_id, date in connection.execute('SELECT * FROM s2_validations').fetchall():
        if not connection.execute('SELECT 1 FROM s2_dossiers d JOIN s2_revisions r USING(dossier_id) '
                                  'WHERE d.dossier_id=? AND d.session_id=? AND r.revision=? AND r.package_sha256=? AND r.stage=?',
                                  (dossier_id, session_id, revision, digest, 'preview')).fetchone():
            raise IntegrityError('Validation étrangère ou divergente')
    for dossier_id, action_id, revision, kind, raw, operation_id in connection.execute('SELECT * FROM s2_actions').fetchall():
        identifier(action_id)
        if not connection.execute('SELECT 1 FROM operations WHERE operation_id=? AND dossier_id=? AND revision=? '
                                  'AND phase=?', (operation_id, dossier_id, revision,
                                                'correction' if kind == 'correct' else 'preparation')).fetchone():
            raise IntegrityError('Action sans intention attribuée')
        encode(json.loads(raw, object_pairs_hook=_unique_object))


def dispatch(store, method, path, token, body, source, transport, *, candidate_transport=None, presentation=None):
    """Executor-side authorization: HTTP fields can never claim an operator role."""
    if method == 'GET' and path == '/preparation':
        session_id, csrf, token = session(store, token, create=True)
        rows = connection_for(store).execute('SELECT dossier_id,current_revision FROM s2_dossiers WHERE session_id=? ORDER BY dossier_id',
                                            (session_id,)).fetchall()
        return 200, {'csrf_token': csrf, 'availability': availability(store, transport),
                     'dossiers': [{'dossier_id': d, 'revision': r,
                                   'need': store.get_dossier(d, r)['request']} for d, r in rows]}, token, None
    session_id, csrf, _ = session(store, token)
    if method == 'GET':
        from . import restitution
        parsed = urlsplit(path)
        preview_route = re.fullmatch(
            r'/preparation/dossiers/([A-Za-z0-9_-]{1,128})/campaigns/([A-Za-z0-9_-]{1,128})/preview', parsed.path)
        if preview_route:
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, errors='strict')
            if parsed.scheme or parsed.netloc or parsed.fragment or any(key != 'piece' for key, _ in pairs):
                raise ValueError('Sélection de pièces requise sur un chemin local')
            value = restitution.preview_view(store, session_id, *preview_route.groups(), piece_ids=[pid for _, pid in pairs],
                                             presentation=presentation)
            return 200, value, None, None
        comparison_route = re.fullmatch(
            r'/preparation/dossiers/([A-Za-z0-9_-]{1,128})/campaigns/([A-Za-z0-9_-]{1,128})'
            r'(?:/attempts/([A-Za-z0-9_-]{1,128}))?', parsed.path)
        if comparison_route:
            if parsed.scheme or parsed.netloc or parsed.fragment:
                raise ValueError('Chemin local requis')
            dossier_id, campaign_id, attempt_id = comparison_route.groups()
            query = restitution.query_parameters(parsed.query)
            if attempt_id is None:
                value = restitution.comparison(store, session_id, dossier_id, campaign_id, query=query)
            else:
                value = restitution.detail(store, session_id, dossier_id, campaign_id, attempt_id, query=query)
            return 200, value, None, None
        if path == '/preparation/catalogue':
            return 200, restitution.catalogue(store, session_id), None, None
    if method == 'POST':
        if type(body) is not dict:
            raise ValueError('Formulaire requis')
        supplied = body.get('csrf_token')
        if type(supplied) is not str or not hmac.compare_digest(supplied.encode(), csrf.encode()):
            raise Denied('Protection CSRF requise')
        body = {key: value for key, value in body.items() if key != 'csrf_token'}
    launch_route = re.fullmatch(r'/preparation/dossiers/([A-Za-z0-9_-]{1,128})/campaigns/([A-Za-z0-9_-]{1,128})/(conditions|start)', path)
    if launch_route:
        from . import campaigns
        dossier_id, campaign_id, action = launch_route.groups()
        if method == 'POST' and action == 'start':
            if not callable(candidate_transport):
                raise Denied('Acquisition indisponible')
            attempts = campaigns.launch(store, session_id, dossier_id, campaign_id, body)
            value = campaigns.launch_view(store, session_id, dossier_id, campaign_id)
            value['can_launch'] = False
            return 202, value, None, {'candidate_attempts': attempts} if attempts else None
        if method == 'GET' and action == 'conditions':
            value = campaigns.launch_view(store, session_id, dossier_id, campaign_id)
            value['can_launch'] = value['can_launch'] and callable(candidate_transport)
            return 200, value, None, None
        raise Denied('Action inaccessible')
    if method == 'POST' and path == '/preparation/dossiers':
        _fields(body, ('dossier_id', 'action_id', 'request'), 'create')
        operation_id, start = submit(store, session_id, body['dossier_id'], body, source, transport)
        return 202, {'operation_id': operation_id, 'dossier_id': body['dossier_id']}, None, operation_id if start else None
    proof = re.fullmatch(r'/preparation/dossiers/([A-Za-z0-9_-]{1,128})/evaluations/([A-Za-z0-9_-]{1,128})/pieces/([A-Za-z0-9_-]{1,128})', path)
    if method == 'GET' and proof:
        from .evaluation import piece_bytes as evaluation_piece
        return 200, evaluation_piece(store, session_id, *proof.groups()), None, None
    match = re.fullmatch(r'/preparation/dossiers/([A-Za-z0-9_-]{1,128})(?:/(messages|validation)|/revisions/([1-9][0-9]*)(?:/pieces/([A-Za-z0-9_-]{1,128}))?)?', path)
    if not match:
        raise Denied('Ressource inaccessible')
    dossier_id, action, revision, piece_id = match.groups()
    owner(connection_for(store), session_id, dossier_id)
    revision = None if revision is None else int(revision)
    if method == 'GET' and action is None:
        if piece_id:
            return 200, piece_bytes(store, session_id, dossier_id, revision, piece_id), None, None
        result = view(store, session_id, dossier_id, revision)
        result['current_revision'] = owner(connection_for(store), session_id, dossier_id)
        result['availability'] = availability(store, transport)
        from .restitution import catalogue
        result['task_index'] = next(t for t in catalogue(store, session_id)['tasks'] if t['dossier_id'] == dossier_id)
        # The CSRF token travels independently in HTML rendering through the web's session query
        return 200, result, None, None
    if method == 'POST' and action == 'messages':
        _fields(body, ('action_id', 'revision', 'kind', 'message'), 'message')
        operation_id, start = submit(store, session_id, dossier_id, body, source, transport)
        return 202, {'operation_id': operation_id, 'dossier_id': dossier_id}, None, operation_id if start else None
    if method == 'POST' and action == 'validation':
        _fields(body, ('dossier_id', 'revision', 'package_sha256'), 'validation')
        return 200, validate(store, session_id, dossier_id, body), None, None
    raise Denied('Action inaccessible')
