"""Private fictional preparation on S1 with an operator-injected transport."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
import re
import secrets
import unicodedata

from .storage import (Store, SchemaError, IntegrityError, ConflictError, BudgetError,
                      _transaction, _strict_json as encode, _fields, _text,
                      _identity, _money, _sum_money, _unique_object, _payload_json)
from .validation import identifier


REQUEST_MIN = 40
REQUEST_MAX = 1_500
USEFUL_MAX = 800
CONTEXT_MAX = 200
MESSAGE_MAX = 1_000
SESSION_INTERVAL = timedelta(seconds=30)
SESSION_DAILY_DOSSIERS = 2
PREPARATION_DAILY_CAP_USD = Decimal('20')
SOURCE_HOURLY_MAX = 20
SOURCE_RATE_WINDOW = timedelta(hours=1)
_SOURCE_ACCEPTED = {}
_CHECK_CODES = frozenset({
    'example_validated', 'example_qualified', 'configurations_available',
    'access_connected', 'estimate_under_cap',
})


class Denied(ValueError):
    def __init__(self, message, field=None, findings=None, step=None):
        super().__init__(message)
        self.code = message if re.fullmatch(r'[A-Z_]+', message) or message in _CHECK_CODES else None
        self.field = field
        self.findings = findings
        self.step = step


def _now():
    return datetime.now(timezone.utc)


def _normalized_text(value, field, minimum, maximum):
    if type(value) is not str:
        raise ValueError('Texte requis')
    value = unicodedata.normalize('NFC', value.replace('\r\n', '\n').replace('\r', '\n'))
    value = ''.join(character for character in value
                    if character in ('\n', '\t') or unicodedata.category(character) != 'Cc').rstrip(' ')
    if len(value) < minimum:
        raise Denied('TEXT_TOO_SHORT', field)
    if len(value) > maximum:
        raise Denied('TEXT_TOO_LONG', field)
    return value


def _normalized_submission(body, create):
    required = {'dossier_id', 'action_id', 'request'} if create else {'action_id', 'revision', 'kind', 'message'}
    optional = {'useful', 'context'} if create else set()
    source_sha256 = body.get('source_sha256') if type(body) is dict else None
    if type(source_sha256) is not str or re.fullmatch(r'[0-9a-f]{64}', source_sha256) is None:
        raise Denied('SOURCE_MISSING', 'source_sha256')
    if not required <= body.keys() or not body.keys() <= required | optional | {'source_sha256'}:
        raise ValueError('Formulaire invalide')
    result = {key: value for key, value in body.items() if key != 'source_sha256'}
    if create:
        result['request'] = _normalized_text(result['request'], 'request', REQUEST_MIN, REQUEST_MAX)
        for field, maximum in (('useful', USEFUL_MAX), ('context', CONTEXT_MAX)):
            result[field] = _normalized_text(result.get(field, ''), field, 0, maximum)
    else:
        result['message'] = _normalized_text(result['message'], 'message', 1, MESSAGE_MAX)
    return result, source_sha256


def _source_limit(source_sha256, now):
    threshold = now - SOURCE_RATE_WINDOW
    # Balayage global en mémoire, à partitionner seulement si le débit le justifie
    for key in tuple(_SOURCE_ACCEPTED):
        retained = [date for date in _SOURCE_ACCEPTED[key] if date > threshold]
        if retained:
            _SOURCE_ACCEPTED[key] = retained
        else:
            del _SOURCE_ACCEPTED[key]
    accepted = _SOURCE_ACCEPTED.get(source_sha256, [])
    if len(accepted) >= SOURCE_HOURLY_MAX:
        raise Denied('SOURCE_RATE_LIMIT')
    _SOURCE_ACCEPTED[source_sha256] = accepted


def _daily_preparation_reserved(connection, now):
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
    amounts = connection.execute(
        "SELECT r.amount FROM operations o JOIN reservations r USING(operation_id) "
        "JOIN budgets b USING(budget_id) WHERE o.phase IN ('preparation','correction','qualification') "
        "AND o.created_at>=? AND b.currency='USD'", (start,)).fetchall()
    return _sum_money(_money(row[0]) for row in amounts)


def _submission_limits(connection, session_id, create, now, authority):
    if connection.execute(
            "SELECT 1 FROM operations o JOIN s2_dossiers d USING(dossier_id) "
            "WHERE d.session_id=? AND o.phase IN ('preparation','correction','qualification') "
            "AND o.state!='RECEIVED' LIMIT 1",
            (session_id,)).fetchone():
        raise Denied('PREPARATION_IN_PROGRESS')
    rows = connection.execute(
        "SELECT o.created_at,a.kind FROM s2_actions a JOIN s2_dossiers d USING(dossier_id) "
        "JOIN operations o USING(operation_id) WHERE d.session_id=? ORDER BY o.created_at DESC",
        (session_id,)).fetchall()
    dates = [(datetime.fromisoformat(created), kind) for created, kind in rows]
    if dates and now - dates[0][0] < SESSION_INTERVAL:
        raise Denied('TOO_SOON')
    if create:
        today = now.date()
        if sum(kind == 'create' and created.astimezone(timezone.utc).date() == today
               for created, kind in dates) >= SESSION_DAILY_DOSSIERS:
            raise Denied('DAILY_SESSION_LIMIT')
    if (_daily_preparation_reserved(connection, now) + _money(authority['reserve_amount'])
            > PREPARATION_DAILY_CAP_USD):
        raise Denied('DAILY_CAP')


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
            "SELECT state FROM operations WHERE phase IN ('preparation','correction','qualification') "
            "AND state != 'RECEIVED'").fetchall()
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
            elif (_daily_preparation_reserved(connection, _now()) + _money(authority['reserve_amount'])
                  > PREPARATION_DAILY_CAP_USD):
                reason = 'daily_cap'
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


def page_view(value):
    """Retirer les empreintes des projections destinées aux pages"""
    if type(value) is dict:
        return {key: page_view(item) for key, item in value.items()
                if key != 'sha256' and key not in ('fingerprint', 'digest') and not key.endswith('_sha256')}
    if type(value) is list:
        return [page_view(item) for item in value]
    return deepcopy(value)


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
    for field in ('deliverables', 'acceptable_ambiguities', 'limits'):
        if type(package[field]) is not list:
            raise IntegrityError('Liste de présentation requise')
        for text in package[field]:
            _text(text, field)
    from .outgoing import criteria
    try:
        checked_criteria = criteria(package['criteria'])
    except ValueError as error:
        raise IntegrityError('Critères du paquet invalides') from error
    has_criteria = any(checked_criteria.values())
    if not package['deliverables'] or not has_criteria or not package['pieces']:
        raise IntegrityError('Paquet incomplet')
    ids, names = set(), set()
    for piece in package['pieces']:
        _fields(piece, ('id', 'name', 'sha256', 'size_bytes'), 'piece')
        identifier(piece['id'])
        if piece['id'] in ids:
            raise IntegrityError('Pièce répétée')
        ids.add(piece['id'])
        _text(piece['name'], 'name')
        if piece['name'] in names:
            raise IntegrityError('Nom de pièce répété')
        names.add(piece['name'])
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


def _package_changes(connection, dossier_id, revision, package):
    empty_pieces = {'added': [], 'removed': [], 'modified': []}
    if revision == 1 or package is None:
        return [], empty_pieces
    row = connection.execute(
        'SELECT package_json,package_sha256 FROM s2_revisions WHERE dossier_id=? AND revision=?',
        (dossier_id, revision - 1)).fetchone()
    if row is None or row[0] is None:
        return [], empty_pieces
    previous = json.loads(row[0], object_pairs_hook=_unique_object)
    if sha256(encode(previous).encode()).hexdigest() != row[1]:
        raise IntegrityError('Empreinte du paquet précédent divergente')
    changes = [field for field in ('instruction', 'deliverables', 'criteria', 'acceptable_ambiguities')
               if package[field] != previous[field]]
    old_pieces = {piece['name']: piece['sha256'] for piece in previous['pieces']}
    if len(previous['pieces']) != len(old_pieces):
        raise IntegrityError('Noms de pièces ambigus dans la révision précédente')
    new_pieces = {piece['name']: piece['sha256'] for piece in package['pieces']}
    piece_changes = {
        'added': [name for name in new_pieces if name not in old_pieces],
        'removed': [name for name in old_pieces if name not in new_pieces],
        'modified': [name for name in new_pieces
                     if name in old_pieces and new_pieces[name] != old_pieces[name]],
    }
    if any(piece_changes.values()):
        changes.append('pieces')
    return changes, piece_changes


def _automatic_qualification(store, connection, dossier_id, revision):
    validated = connection.execute(
        'SELECT 1 FROM s2_validations v JOIN s2_revisions r USING(dossier_id,revision) '
        'JOIN s2_dossiers d USING(dossier_id) WHERE v.dossier_id=? AND v.revision=? '
        'AND v.package_sha256=r.package_sha256 AND v.session_id=d.session_id',
        (dossier_id, revision)).fetchone()
    row = connection.execute('SELECT operation_id,qualified,findings_json,summary,model,cost_usd,created_at '
                             'FROM s2_qualifications WHERE dossier_id=? AND revision=?',
                             (dossier_id, revision)).fetchone()
    if row is None:
        operation = next((item for item in store._operations(connection)
                          if (item['dossier_id'], item['revision'], item['phase']) ==
                             (dossier_id, revision, 'qualification')), None)
        if operation is None:
            return None
        if validated is None:
            raise IntegrityError('Qualification sans validation du besoin')
        failed = operation['state'] in ('AMBIGUOUS', 'RECEIVED')
        cost = (operation['observed_cost']['amount'] if operation['state'] == 'RECEIVED'
                and operation['observed_cost']['status'] == 'KNOWN' else None)
        status = 'BLOCKED' if failed else 'PENDING'
        return dict(operation_id=operation['operation_id'], qualified=False, findings=[],
                    summary=('Résultat de qualification reçu non utilisable' if operation['state'] == 'RECEIVED'
                             else 'Effets de qualification inconnus' if operation['state'] == 'AMBIGUOUS'
                             else 'Qualification en attente'),
                    model=operation['requested_configuration'].get('model'), cost_usd=cost,
                    created_at=operation['created_at'], status=status,
                    qualification_status=status, approval_status='PENDING')
    operation_id, qualified, raw, summary, model, cost, created = row
    if validated is None:
        raise IntegrityError('Qualification sans validation du besoin')
    findings = json.loads(raw, object_pairs_hook=_unique_object)
    result = _qualification_result({'qualified': bool(qualified), 'findings': findings, 'summary': summary})
    operation = store._operation_for_update(connection, operation_id, ('RECEIVED',))
    if ((operation['dossier_id'], operation['revision'], operation['phase']) !=
            (dossier_id, revision, 'qualification') or operation['requested_configuration'].get('model') != model):
        raise IntegrityError('Qualification automatisée étrangère')
    observed = operation['observed_cost']
    expected_cost = observed['amount'] if observed['status'] == 'KNOWN' else None
    if cost != expected_cost:
        raise IntegrityError('Coût de qualification divergent')
    status = 'QUALIFIED' if result['qualified'] else 'BLOCKED'
    return dict(operation_id=operation_id, qualified=result['qualified'], findings=findings,
                summary=summary, model=model, cost_usd=cost, created_at=created,
                status=status, qualification_status=status, approval_status='PENDING')


def require_qualification(store, connection, dossier_id, revision):
    if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s3_control'").fetchone():
        row = connection.execute('SELECT contract_sha256 FROM s3_contracts WHERE dossier_id=? AND revision=? '
                                 'ORDER BY version DESC LIMIT 1', (dossier_id, revision)).fetchone()
        if row:
            from .qualification import projection
            state = projection(store, connection, dossier_id, revision, eligible=True)
            if state['status'] in ('QUALIFIED', 'APPROVED'):
                return
            raise Denied('NOT_QUALIFIED', findings=[])
    qualification = _automatic_qualification(store, connection, dossier_id, revision)
    if qualification is None or not qualification['qualified']:
        raise Denied('NOT_QUALIFIED', findings=[] if qualification is None else qualification['findings'])


def require_requester_steps(store, connection, session_id, dossier_id, revision):
    row = connection.execute(
        'SELECT r.package_sha256 FROM s2_revisions r JOIN s2_dossiers d USING(dossier_id) '
        'WHERE r.dossier_id=? AND r.revision=? AND d.session_id=?',
        (dossier_id, revision, session_id)).fetchone()
    validated = row and row[0] and connection.execute(
        'SELECT 1 FROM s2_validations WHERE dossier_id=? AND revision=? '
        'AND package_sha256=? AND session_id=?',
        (dossier_id, revision, row[0], session_id)).fetchone()
    if not validated:
        raise Denied('STEP_INCOMPLETE', step='example_validated')
    try:
        require_qualification(store, connection, dossier_id, revision)
    except Denied as error:
        if error.code != 'NOT_QUALIFIED':
            raise
        raise Denied('STEP_INCOMPLETE', step='example_qualified') from None


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
        stage, explanation, raw, digest, _, checks = row
        package = None if raw is None else json.loads(raw, object_pairs_hook=_unique_object)
        if package is not None:
            package_check(store, dossier_id, revision, package, digest)
        changes, piece_changes = _package_changes(connection, dossier_id, revision, package)
        validated = connection.execute('SELECT 1 FROM s2_validations WHERE dossier_id=? AND revision=? '
                                       'AND package_sha256=? AND session_id=?',
                                       (dossier_id, revision, digest, session_id)).fetchone()
        result = dict(dossier_id=dossier_id, revision=revision, payload=store.get_dossier(dossier_id, revision),
                      stage=stage, explanation=explanation, package=package, package_sha256=digest,
                      fictional=True, validation=binding(dossier_id, revision, digest) if validated else None,
                      qualified=False, changes=changes, piece_changes=piece_changes,
                      checks=json.loads(checks, object_pairs_hook=_unique_object))
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
        contract = (connection.execute('SELECT 1 FROM s3_contracts WHERE dossier_id=? AND revision=?',
                                       (dossier_id, revision)).fetchone()
                    if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s3_control'").fetchone() else None)
        if contract:
            from .qualification import projection
            result['qualification'] = projection(store, connection, dossier_id, revision,
                                                  eligible=result['validation'] is not None)
            result['qualified'] = result['qualification']['status'] in ('QUALIFIED', 'APPROVED')
        else:
            result['qualification'] = _automatic_qualification(store, connection, dossier_id, revision)
            if result['qualification'] is None:
                result['qualification'] = dict(status='PENDING', qualification_status='PENDING',
                                               approval_status='PENDING', qualified=False)
            result['qualified'] = bool(result['qualification'] and result['qualification']['qualified'])
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s4_control'").fetchone():
            from .campaigns import projection
            result['campaigns'] = projection(store, connection, dossier_id)
            if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s5_control'").fetchone():
                from .evaluation import projection as evaluations
                for campaign in result['campaigns']:
                    campaign['evaluations'] = evaluations(store, connection, dossier_id, campaign['campaign_id'])
        projected = page_view(result)
        if projected['package'] is not None:
            from .outgoing import criteria
            projected['criteria'] = criteria(projected['package']['criteria'])
            projected['criteria_rule'] = (
                'satisfait = aucune faute éliminatoire et toutes les obligations prouvées ; '
                'la qualité départage, sans note')
        projected['package_sha256'] = digest
        return projected


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


def submit(store, session_id, dossier_id, body, source, transport, *, enforce_limits=False,
           source_sha256=None):
    connection = connection_for(store)
    now = _now() if enforce_limits else None
    identifier(dossier_id)
    identifier(body['action_id'])
    create = 'request' in body
    kind = 'create' if create else body['kind']
    if kind not in ('create', 'clarify', 'correct'):
        raise ValueError('Action inconnue')
    message = body['request'] if create else body['message']
    if create:
        if body.get('useful'):
            message += '\n\nRésultat attendu :\n' + body['useful']
        if body.get('context'):
            message += '\n\nContexte :\n' + body['context']
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
        if enforce_limits:
            _submission_limits(connection, session_id, create, now, authority)
        # S2 admits one effect at a time; no restart drains a durable queue
        if connection.execute(
                "SELECT 1 FROM operations WHERE phase IN ('preparation','correction','qualification') "
                "AND state != 'RECEIVED' LIMIT 1").fetchone():
            raise Denied('PREPARATION_IN_PROGRESS')
        if enforce_limits:
            _source_limit(source_sha256, now)
        budget = store._budget(connection, authority['budget_id'], store._operations(connection))
        _usd_budget(authority['reserve_amount'], authority['requested_configuration'], budget)
        revision = existing[1] if existing else 1
        if create:
            payload = dict(request=body['request'], clarifications=[], reformulation='', validated_assumptions=[],
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
        store._reserve_intent(connection, operation, authority['budget_id'], authority['reserve_amount'],
                              created_at=now)
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
        if enforce_limits:
            _SOURCE_ACCEPTED[source_sha256].append(now)
        return operation_id, True


def _validate(store, connection, session_id, dossier_id, body):
    _identity(dossier_id, body['revision'])
    if body['dossier_id'] != dossier_id:
        raise ConflictError('Dossier divergent')
    if type(body['package_sha256']) is not str or re.fullmatch('[0-9a-f]{64}', body['package_sha256']) is None:
        raise ValueError('Empreinte invalide')
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


def validate(store, session_id, dossier_id, body):
    connection = connection_for(store)
    with _transaction(connection, write=True):
        return _validate(store, connection, session_id, dossier_id, body)


def _qualification_input(store, connection, dossier_id, revision):
    row = connection.execute('SELECT package_json,package_sha256 FROM s2_revisions '
                             'WHERE dossier_id=? AND revision=?', (dossier_id, revision)).fetchone()
    package = json.loads(row[0], object_pairs_hook=_unique_object)
    package_check(store, dossier_id, revision, package, row[1])
    payload = store.get_dossier(dossier_id, revision)
    from .outgoing import criteria
    candidates = [{'name': piece['name'], 'content': store.read_piece(piece['id']).decode('utf-8')}
                  for piece in package['pieces']]
    references = [{'name': name, 'content': store.read_piece(piece_id).decode('utf-8')}
                  for piece_id, name in connection.execute(
                      "SELECT piece_id,name FROM pieces WHERE dossier_id=? AND revision=? "
                      "AND role='judge' ORDER BY piece_id", (dossier_id, revision))]
    if not references:
        raise IntegrityError('Référence de jugement absente')
    return dict(instruction=package['instruction'], deliverables=package['deliverables'],
                criteria=criteria(package['criteria']),
                acceptable_ambiguities=package['acceptable_ambiguities'], candidate_pieces=candidates,
                judgment_reference=references, reformulated_need=payload['reformulation'],
                clarifications=payload['clarifications'])


def validate_and_qualify(store, session_id, dossier_id, body, source, transport):
    if transport is None or not callable(getattr(transport, 'configuration', None)):
        raise Denied('QUALIFICATION_UNAVAILABLE')
    _text(source, 'source')
    connection = connection_for(store)
    with _transaction(connection, write=True):
        result = _validate(store, connection, session_id, dossier_id, body)
        revision = result['revision']
        existing = connection.execute(
            "SELECT operation_id,state FROM operations WHERE dossier_id=? AND revision=? AND phase='qualification'",
            (dossier_id, revision)).fetchone()
        if existing:
            return result, existing[0], False
        authority = admission(store, connection)
        if authority is None or os.path.lexists(store._root / 'restore.json'):
            raise Denied('ADMISSION_CLOSED')
        if connection.execute(
                "SELECT 1 FROM operations WHERE phase IN ('preparation','correction','qualification') "
                "AND state!='RECEIVED' LIMIT 1").fetchone():
            raise Denied('PREPARATION_IN_PROGRESS')
        if (_daily_preparation_reserved(connection, _now()) + _money(authority['reserve_amount'])
                > PREPARATION_DAILY_CAP_USD):
            raise Denied('DAILY_CAP')
        request = _qualification_input(store, connection, dossier_id, revision)
        operation_id = secrets.token_hex(16)
        configuration = transport.configuration()
        operation = dict(operation_id=operation_id, phase='qualification', dossier_id=dossier_id,
                         revision=revision, authority=authority['authority_id'], engine_version=source,
                         requested_configuration=configuration, resources=[])
        from .outgoing import FORMAT
        closed = dict(outgoing_format=FORMAT, outgoing=request)
        wire = transport.prepare(deepcopy(operation), deepcopy(closed))
        _text(wire, 'qualification préparée')
        operation['resources'] = [encode(request), wire]
        budget = store._budget(connection, authority['budget_id'], store._operations(connection))
        if budget['currency'] != 'USD':
            raise BudgetError('Enveloppe USD de préparation requise')
        store._reserve_intent(connection, operation, authority['budget_id'], authority['reserve_amount'])
        return result, operation_id, True


def _qualification_result(result):
    _fields(result, ('qualified', 'findings', 'summary'), 'qualification automatisée')
    if type(result['qualified']) is not bool or type(result['findings']) is not list:
        raise ValueError('Qualification automatisée invalide')
    _text(result['summary'], 'summary')
    if not result['summary'].strip():
        raise ValueError('Résumé de qualification requis')
    for finding in result['findings']:
        _fields(finding, ('kind', 'severity', 'text'), 'constat de qualification')
        if finding['kind'] not in ('coherence', 'fiction', 'decidability', 'leak') or finding['severity'] not in ('blocking', 'note'):
            raise ValueError('Constat de qualification invalide')
        _text(finding['text'], 'text')
        if not finding['text'].strip():
            raise ValueError('Texte de constat requis')
    if result['qualified'] != all(row['severity'] != 'blocking' for row in result['findings']):
        raise ValueError('Verdict de qualification divergent')
    return result


def execute_qualification(data, operation_id, transport):
    with closing(Store(data)) as store:
        connection = connection_for(store)
        emitted = False
        try:
            proof = store.verify_storage()
            if not proof['integrity_ok'] or proof['orphan_files']:
                close_admission(store)
                return
            with _transaction(connection, write=True):
                try:
                    operation = store._operation_for_update(connection, operation_id, ('INTENT_RECORDED',))
                except ConflictError:
                    return
                authority = admission(store, connection)
                if (transport is None or authority is None or operation['phase'] != 'qualification'
                        or os.path.lexists(store._root / 'restore.json')
                        or (operation['authority'], operation['budget_id'], operation['reserved_amount']) !=
                           (authority['authority_id'], authority['budget_id'], authority['reserve_amount'])
                        or operation['requested_configuration'] != transport.configuration()):
                    return
                request = _qualification_input(store, connection, operation['dossier_id'], operation['revision'])
                if encode(request) != operation['resources'][0]:
                    raise ConflictError('Entrée de qualification modifiée depuis la réservation')
                from .outgoing import FORMAT
                closed = dict(outgoing_format=FORMAT, outgoing=request)
                wire = transport.prepare(deepcopy(operation), deepcopy(closed))
                if len(operation['resources']) != 2 or wire != operation['resources'][1]:
                    raise ConflictError('Contenu de qualification modifié depuis la réservation')
                budget = store._budget(connection, operation['budget_id'], store._operations(connection))
                if (budget['currency'] != 'USD' or store._blocking_costs(
                        store._operations(connection), budget, 'qualification')):
                    return
                connection.execute("UPDATE operations SET state='EMISSION_POSSIBLE' WHERE operation_id=?", (operation_id,))
            emitted = True
            operation['state'] = 'EMISSION_POSSIBLE'
            operation['conserved_wire'] = wire
            response = transport(deepcopy(operation), deepcopy(closed))
            _fields(response, ('receipt', 'cost'), 'réponse de qualification')
            result = response['receipt']['result']
            try:
                result = _qualification_result(result)
            except (ValueError, TypeError, KeyError):
                result = None
            with _transaction(connection, write=True):
                store._record_receipt(connection, operation_id, response['receipt'], response['cost'])
                if result is not None:
                    cost = response['cost']['amount'] if response['cost']['status'] == 'KNOWN' else None
                    connection.execute('INSERT INTO s2_qualifications VALUES (?,?,?,?,?,?,?,?,?)',
                        (operation['dossier_id'], operation['revision'], operation_id, int(result['qualified']),
                         encode(result['findings']), result['summary'], operation['requested_configuration']['model'],
                         cost, datetime.now(timezone.utc).isoformat()))
                    if result['qualified']:
                        from .campaigns import _record_comparison_contract
                        _record_comparison_contract(store, connection, operation)
        except Exception:
            if emitted:
                store.mark_ambiguous(operation_id, 'QUALIFICATION_RESULT_NOT_VERIFIED')
            else:
                close_admission(store)


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
                    previous_checks = json.loads(connection.execute(
                        'SELECT checks_json FROM s2_revisions WHERE dossier_id=? AND revision=?',
                        (dossier_id, before)).fetchone()[0])
                    scope_count = previous_checks.get('scope_confirmation_count', 0)
                    if type(scope_count) is not int or scope_count < 0:
                        raise IntegrityError('Compteur de confirmation de périmètre invalide')
                    store._record_receipt(connection, operation_id, response['receipt'], response['cost'])
                    store.save_dossier(dossier_id, revision, request['payload'])
                    connection.execute('INSERT INTO s2_revisions VALUES (?,?,?,?,NULL,NULL,?,?)',
                                       (dossier_id, revision, 'suspended',
                                        'Résultat reçu non utilisable : préparation suspendue. '
                                        'Reçu et coût conservés ; aucune reprise automatique.',
                                        encode([]), encode({'result_verified': False,
                                                            'scope_confirmation_count': scope_count})))
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
    explanation = result['explanation']
    if stage not in ('clarification', 'preview', 'scope_confirmation', 'suspended'):
        raise ValueError('État inconnu')
    if type(result['explanation']) is not str or (stage != 'preview' and not result['explanation']):
        raise ValueError('Explication requise')
    if (stage == 'preview') != (result['package'] is not None):
        raise ValueError('Paquet incohérent avec l’état')
    candidate = (result['package'] or {}).get('candidate', {})
    invalid_candidate = type(candidate) is not dict
    exposed = [] if invalid_candidate else [candidate.get('instruction', '')]
    if not invalid_candidate:
        exposed.extend(piece.get('content', '') for piece in candidate.get('pieces', [])
                       if type(piece) is dict)
    forbidden = any(type(text) is str and re.search(
        r'\b(?:ficti(?:f|fs|ve|ves)|inventé(?:e|s|es)?)\b', text, re.IGNORECASE) for text in exposed)
    if invalid_candidate or forbidden:
        observed = response['receipt'].get('observed_configuration')
        if observed is None:
            response['receipt']['observed_configuration'] = {'incident': 'FORMAT_ERROR'}
        elif type(observed) is dict:
            observed['incident'] = 'FORMAT_ERROR'
        raise ValueError('Paquet candidat invalide' if invalid_candidate
                         else 'Marqueur de fiction interdit dans le paquet candidat')
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
        previous_checks = json.loads(connection.execute(
            'SELECT checks_json FROM s2_revisions WHERE dossier_id=? AND revision=?',
            (dossier_id, before)).fetchone()[0])
        scope_count = previous_checks.get('scope_confirmation_count', 0)
        if type(scope_count) is not int or scope_count < 0:
            raise IntegrityError('Compteur de confirmation de périmètre invalide')
        if stage == 'scope_confirmation':
            scope_count += 1
            if scope_count == 2:
                stage = 'clarification'
            elif scope_count >= 3:
                stage = 'suspended'
                explanation = 'SCOPE_LOOP : ' + explanation
        revision = before + 1
        store.save_dossier(dossier_id, revision, payload)
        package, digest, checks = None, None, {}
        if generated is not None:
            from .outgoing import FORMAT
            package = dict(instruction=generated['candidate']['instruction'],
                           deliverables=list(generated['candidate']['deliverables']),
                           criteria=deepcopy(generated['candidate']['criteria']),
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
        rechecked = [key for key in (package or {}) if (package or {})[key] != old_package.get(key)]
        if package is None:
            rechecked = ['stage', 'explanation']
        checks['fields'] = rechecked
        checks['scope_confirmation_count'] = scope_count
        connection.execute('INSERT INTO s2_revisions VALUES (?,?,?,?,?,?,?,?)',
                           (dossier_id, revision, stage, explanation, None if package is None else encode(package),
                            digest, encode([]), encode(checks)))
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
    for dossier_id, revision in connection.execute('SELECT dossier_id,revision FROM s2_qualifications').fetchall():
        _automatic_qualification(store, connection, dossier_id, revision)
    from .campaigns import _comparison_contract
    for (fingerprint,) in connection.execute('SELECT contract_sha256 FROM s2_comparison_contracts').fetchall():
        _comparison_contract(store, connection, fingerprint)
    for dossier_id, action_id, revision, kind, raw, operation_id in connection.execute('SELECT * FROM s2_actions').fetchall():
        identifier(action_id)
        if not connection.execute('SELECT 1 FROM operations WHERE operation_id=? AND dossier_id=? AND revision=? '
                                  'AND phase=?', (operation_id, dossier_id, revision,
                                                'correction' if kind == 'correct' else 'preparation')).fetchone():
            raise IntegrityError('Action sans intention attribuée')
        encode(json.loads(raw, object_pairs_hook=_unique_object))
