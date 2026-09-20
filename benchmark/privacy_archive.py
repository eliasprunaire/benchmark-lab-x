"""Closed owner archives and consent-bound copies, independent of dossier retention

The S7 lifecycle and withdrawal journal belong to privacy; this module never
initializes or migrates storage. Archive parts are zero-based and at most 1 MiB.
"""
from calendar import monthrange
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import re
import secrets
from typing import cast

from . import preparation as p, restitution
from .storage import (ConflictError, IntegrityError, SchemaError, _strict_json as encode, _transaction,
                      _fields, _money, _unique_object, _expected_schema)
from .validation import identifier

CHUNK_BYTES = 1024 * 1024
NOTICE_VERSION = 'bench-x/privacy-notice/v1'
_TABLES = {
    's7_archives': """CREATE TABLE s7_archives (
        snapshot_id TEXT PRIMARY KEY NOT NULL,
        dossier_id TEXT NOT NULL REFERENCES s7_dossiers(dossier_id),
        content_version INTEGER NOT NULL CHECK(content_version > 0),
        record BLOB NOT NULL CHECK(typeof(record) = 'blob' AND length(record) > 0),
        sha256 TEXT NOT NULL CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
        UNIQUE(dossier_id, content_version)
    )""",
    's7_contribution_managers': """CREATE TABLE s7_contribution_managers (
        manager_id TEXT PRIMARY KEY NOT NULL,
        token_sha256 TEXT NOT NULL UNIQUE
            CHECK(length(token_sha256) = 64 AND token_sha256 NOT GLOB '*[^0-9a-f]*'),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL CHECK(expires_at > created_at)
    )""",
    's7_contributions': """CREATE TABLE s7_contributions (
        contribution_id TEXT PRIMARY KEY NOT NULL,
        manager_id TEXT NOT NULL REFERENCES s7_contribution_managers(manager_id),
        dossier_id TEXT NOT NULL,
        example_revision INTEGER NOT NULL CHECK(example_revision > 0),
        revision INTEGER NOT NULL CHECK(revision > 0),
        content_version INTEGER NOT NULL CHECK(content_version > 0),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL CHECK(expires_at > created_at),
        status TEXT NOT NULL CHECK(status IN ('active', 'withdrawn', 'expired')),
        payload_json TEXT,
        notice_version TEXT NOT NULL CHECK(notice_version='bench-x/privacy-notice/v1'),
        CHECK((status = 'active') = (payload_json IS NOT NULL)),
        UNIQUE(dossier_id, example_revision, revision)
    )""",
}
_INDEXES = {
    's7_archives_dossier': ('s7_archives',
        'CREATE INDEX s7_archives_dossier ON s7_archives(dossier_id)'),
    's7_contributions_active': ('s7_contributions',
        "CREATE UNIQUE INDEX s7_contributions_active ON s7_contributions(dossier_id, example_revision) WHERE status='active'"),
    's7_contributions_manager': ('s7_contributions',
        'CREATE INDEX s7_contributions_manager ON s7_contributions(manager_id)'),
    's7_contributions_expiry': ('s7_contributions',
        'CREATE INDEX s7_contributions_expiry ON s7_contributions(status, expires_at)'),
}

_TRIGGERS = {
    's7_archives_immutable': ('s7_archives', """CREATE TRIGGER s7_archives_immutable
        BEFORE UPDATE ON s7_archives BEGIN SELECT RAISE(ABORT, 'immutable archive'); END"""),
    's7_archives_no_replace': ('s7_archives', """CREATE TRIGGER s7_archives_no_replace
        BEFORE INSERT ON s7_archives WHEN EXISTS (
            SELECT 1 FROM s7_archives WHERE snapshot_id=NEW.snapshot_id
            OR (dossier_id=NEW.dossier_id AND content_version=NEW.content_version))
        BEGIN SELECT RAISE(ABORT, 'immutable archive'); END"""),
}


def create_statements():
    return (list(_TABLES.values()) + [sql for _, sql in _INDEXES.values()]
            + [sql for _, sql in _TRIGGERS.values()])


def schema_objects():
    return ([('table', name, name, sql) for name, sql in _TABLES.items()]
            + [('index', f'sqlite_autoindex_{name}_{number}', name, None)
               for name, count in (('s7_archives', 2), ('s7_contribution_managers', 2), ('s7_contributions', 2))
               for number in range(1, count + 1)]
            + [('index', name, table, sql) for name, (table, sql) in _INDEXES.items()]
            + [('trigger', name, table, sql) for name, (table, sql) in _TRIGGERS.items()])


def _authorize(connection, session_id, dossier_id):
    from . import privacy
    if not privacy.available(connection):
        raise SchemaError('Initialisation explicite S7 requise')
    privacy.authorize_dossier(connection, session_id, dossier_id)
    row = connection.execute('SELECT content_version FROM s7_dossiers WHERE dossier_id=? AND session_id=?',
                             (dossier_id, session_id)).fetchone()
    if row is None:
        raise p.Denied('Dossier inaccessible')
    return row[0]


_SECRET = re.compile(
    r'\b(?:sk-(?:or-v1-)?[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|'
    r'github_pat_[A-Za-z0-9_]{8,}|AKIA[A-Z0-9]{16}|'
    r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b|'
    r'(?i:\b(?:bearer|api[_ -]?key|access[_ -]?token|password|secret|token)\s*[:= ]\s*\S+)')


def _text(value):
    if type(value) is not str:
        raise IntegrityError('Texte de projection invalide')
    return _SECRET.sub('[contenu retiré]', value)



def _qualification(value):
    lines = [_text(value['status'])]
    if value.get('summary'):
        lines.append(_text(value['summary']))
    for finding in value.get('findings', []):
        lines.append(_text(finding['kind']) + ' / ' + _text(finding['severity']) + ' : ' + _text(finding['text']))
    return '\n'.join(lines)


def _revision(view):
    package = view['package']
    criteria = [] if package is None else package['criteria']
    if isinstance(criteria, dict):
        criteria = (criteria['eliminatory'] + criteria['obligations']
                    + [item['label'] for item in criteria['quality']])
    return dict(number=view['revision'], instruction='' if package is None else _text(package['instruction']),
        deliverables=[] if package is None else [_text(x) for x in package['deliverables']],
        criteria=[_text(x) for x in criteria],
        pieces=[] if package is None else [dict(name=_text(piece['name']),
            text=_text(view['example_contents'][piece['id']])) for piece in package['pieces']],
        qualification=_qualification(view['qualification']))



def _pending_model(store, session_id, dossier_id, campaign, comparison, attempt_id):
    attempt = next(item for item in campaign['attempts'] if item['operation_id'] == attempt_id)
    cell = next(item for item in comparison['cells'] if item['cell_id'] == attempt['cell_id'])
    configuration = next(item for item in comparison['panel'] if item['id'] == cell['configuration_id'])
    with store.read_snapshot() as connection:
        _authorize(connection, session_id, dossier_id)
        row = connection.execute(
            'SELECT r.output_piece_id FROM s4_attempts a JOIN operations o USING(operation_id) '
            'LEFT JOIN s4_results r USING(operation_id) '
            'WHERE a.operation_id=? AND a.campaign_id=? AND o.dossier_id=?',
            (attempt_id, campaign['campaign_id'], dossier_id)).fetchone()
        if row is None:
            raise p.Denied('Tentative inaccessible')
        # Only the output explicitly linked by S4 is read, never a judge-role inventory
        answer = None if row[0] is None else _text(store.read_piece(row[0]).decode('utf-8'))
    cost = attempt['observed_cost']
    return dict(name=_text(configuration['model']), verdict=None,
        cost=dict(amount=None if cost is None or cost['status'] != 'KNOWN' else cost['amount'],
                  currency=_text(comparison['cost_basis']['unit'] if cost is None else cost['currency'])),
        answer=answer, evidence=[_configuration('Configuration demandée', configuration)])


def _configuration(name, value):
    allowed = ('provider', 'model', 'revision', 'access', 'route', 'channel_id', 'reasoning_effort', 'effort')
    result = {key: value[key] for key in allowed if key in value}
    parameters = value.get('parameters', {})
    if isinstance(parameters, dict):
        kept = {key: parameters[key] for key in ('temperature', 'top_p', 'max_tokens', 'stream') if key in parameters}
        for key, fields in (('reasoning', ('effort', 'max_tokens', 'enabled', 'exclude')),
                            ('provider', ('only', 'order', 'allow_fallbacks', 'require_parameters', 'data_collection')),
                            ('response_format', ('type',))):
            if isinstance(parameters.get(key), dict):
                kept[key] = {field: parameters[key][field] for field in fields if field in parameters[key]}
        if kept:
            result['parameters'] = kept
    return dict(name=name, text=_text(encode(result)))


def _campaigns(store, session_id, dossier_id, campaigns):
    result: list[dict] = []
    for campaign in campaigns:
        cid = campaign['campaign_id']
        comparison = restitution.comparison(store, session_id, dossier_id, cid)
        models: list[dict] = []
        for row in comparison['rows']:
            detail = restitution.detail(store, session_id, dossier_id, cid, row['attempt_id'])
            history = cast(list[dict], detail['history'])
            record = next(x for x in history if x['evaluation_id'] == row['evaluation_id'])
            candidate_ids = {piece['id'] for piece in record['qualification']['contract']['package']['pieces']}
            output_id = record['output_piece_id']
            # Judge references can appear in proof_links but never in this allowlist
            evidence: list[dict] = [
                dict(name=_text(link['name']), text=_text(record['proof_contents'][link['piece_id']]))
                for link in record['proof_links'] if link['piece_id'] in candidate_ids]
            evidence += [_configuration('Configuration demandée', row['requested_configuration']),
                         _configuration('Configuration observée', row.get('observed_configuration') or {}),
                         dict(name='Motif du résultat', text=_text(row['reason']))]
            for measure in row['measures']:
                evidence.append(dict(name=_text(measure['definition']['measure']),
                    text=_text(str(measure['value'])) + ' ' + _text(measure['unit'])))
            models.append(dict(name=_text(row['requested_configuration']['model']), verdict=row['verdict'],
                cost=dict(amount=row['cost']['value'], currency=_text(row['cost']['unit'])),
                answer=None if output_id is None else _text(record['proof_contents'][output_id]), evidence=evidence))
        seen = {row['attempt_id'] for row in comparison['rows']}
        for pending in comparison['pending_attempts']:
            if pending['attempt_id'] not in seen:
                models.append(_pending_model(store, session_id, dossier_id, campaign, comparison, pending['attempt_id']))
                seen.add(pending['attempt_id'])
        result.append(dict(id=cid, models=models))
    return result


def _owner_record(store, session_id, dossier_id, version):
    current = p.view(store, session_id, dossier_id, include_history=True)
    views = [current if number == current['revision'] else p.view(store, session_id, dossier_id, number)
             for number in current['task_index']['revisions']]
    messages = [_text(view['message']['message']) for view in views
                if view.get('message') and type(view['message'].get('message')) is str
                and view['message']['message']]
    return dict(format='bench-x/history/v1', dossier_id=dossier_id, content_version=version,
        need=_text(current['payload']['request']), messages=messages, revisions=[_revision(view) for view in views],
        campaigns=_campaigns(store, session_id, dossier_id, current.get('campaigns', [])))


def _manifest(dossier_id, version, row):
    snapshot, length, digest = row
    return dict(format='bench-x/archive/v1', dossier_id=dossier_id, content_version=version,
                snapshot_id=snapshot, items=[dict(item_id='record', length=length, sha256=digest)])


def archive_manifest(store, session_id, dossier_id):
    connection = store._connection_checked()
    with _transaction(connection):
        version = _authorize(connection, session_id, dossier_id)
        existing = connection.execute('SELECT snapshot_id,length(record),sha256 FROM s7_archives '
            'WHERE dossier_id=? AND content_version=?', (dossier_id, version)).fetchone()
        if existing:
            return _manifest(dossier_id, version, existing)
    record = encode(_owner_record(store, session_id, dossier_id, version)).encode('utf-8')
    digest = sha256(record).hexdigest()
    with _transaction(connection, write=True):
        if _authorize(connection, session_id, dossier_id) != version:
            raise ConflictError('Contenu modifié pendant l’archivage')
        existing = connection.execute('SELECT snapshot_id,length(record),sha256 FROM s7_archives '
            'WHERE dossier_id=? AND content_version=?', (dossier_id, version)).fetchone()
        if existing:
            return _manifest(dossier_id, version, existing)
        snapshot = secrets.token_hex(32)
        connection.execute('INSERT INTO s7_archives VALUES (?, ?, ?, ?, ?)',
                           (snapshot, dossier_id, version, record, digest))
        return _manifest(dossier_id, version, (snapshot, len(record), digest))


def archive_item(store, session_id, dossier_id, snapshot_id, part):
    identifier(snapshot_id)
    if type(part) is not int or part < 0:
        raise ValueError('Partie invalide')
    connection = store._connection_checked()
    with _transaction(connection):
        _authorize(connection, session_id, dossier_id)
        row = connection.execute('SELECT length(record) FROM s7_archives WHERE snapshot_id=? AND dossier_id=?',
                                 (snapshot_id, dossier_id)).fetchone()
        if row is None:
            raise p.Denied('Archive inaccessible')
        total = (row[0] + CHUNK_BYTES - 1) // CHUNK_BYTES
        if part >= total:
            raise ValueError('Partie invalide')
        raw = connection.execute('SELECT substr(record,?,?) FROM s7_archives WHERE snapshot_id=?',
                                 (part * CHUNK_BYTES + 1, CHUNK_BYTES, snapshot_id)).fetchone()[0]
        return dict(snapshot_id=snapshot_id, item_id='record', part=part, total_parts=total, hex=raw.hex())


def _now(value=None):
    if value is None:
        from . import privacy
        value = privacy.now()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Date UTC explicite requise')
    return value.astimezone(timezone.utc)


def _six_months(value):
    month = value.month + 5
    year, month = value.year + month // 12, month % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


# ponytail: motifs locaux conservateurs, pas une garantie d'anonymat ; revue si couverture insuffisante
_PERSONAL = re.compile(
    r'\b[^\s@]+@[^\s@]+\.[^\s@]+|'
    r'(?<!\w)(?:\+\d[\d ().-]{7,}\d|0[1-9](?:[ .-]?\d{2}){4})(?!\w)|'
    r'\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}\b|'
    r'\b[12][ -]?\d{2}[ -]?(?:0[1-9]|1[0-2])[ -]?(?:\d{2}|2[AB])[ -]?\d{3}[ -]?\d{3}[ -]?\d{2}\b|'
    r'(?i:\b(?:nom|prénom|name|surname|iban|numéro de sécurité sociale)\s*[:=]\s*\S+)')


def _sensitive(value):
    if type(value) is str:
        return bool(_PERSONAL.search(value) or _SECRET.search(value) or '[contenu retiré]' in value)
    if type(value) is dict:
        return any(_sensitive(item) for item in value.values())
    if type(value) is list:
        return any(_sensitive(item) for item in value)
    return False



def _contribution_sensitive(value):
    return (_sensitive(value['example']) or any(
        _sensitive([model['answer'], model['evidence']])
        for campaign in value['campaigns'] for model in campaign['models']))


def _contribution_payload(store, session_id, dossier_id, example_revision):
    view = p.view(store, session_id, dossier_id)
    if view['revision'] != example_revision:
        raise ConflictError('Version de l’exemple modifiée')
    if view['package'] is None:
        raise p.Denied('Exemple requis pour contribuer')
    campaigns = [campaign for campaign in view.get('campaigns', [])
                 if campaign['task']['revision'] == example_revision]
    value = dict(format='bench-x/contribution/v1', example=_revision(view),
                 campaigns=_campaigns(store, session_id, dossier_id, campaigns))
    raw = encode(value)
    if _contribution_sensitive(value):
        raise p.Denied('CONTRIBUTION_SENSITIVE_DATA')
    return raw


_CONTRIBUTION_COLUMNS = ('contribution_id', 'manager_id', 'dossier_id', 'example_revision', 'revision',
                         'content_version', 'created_at', 'expires_at', 'status')


def _contribution(connection, dossier_id, example_revision):
    row = connection.execute('SELECT ' + ','.join(_CONTRIBUTION_COLUMNS) +
        ' FROM s7_contributions WHERE dossier_id=? AND example_revision=? ORDER BY revision DESC LIMIT 1',
        (dossier_id, example_revision)).fetchone()
    return None if row is None else dict(zip(_CONTRIBUTION_COLUMNS, row))


def _metadata(row):
    return dict(id=row['contribution_id'], revision=row['revision'], example_revision=row['example_revision'],
                created_at=row['created_at'], expires_at=row['expires_at'], status=row['status'])


def _token(token):
    if type(token) is not str or re.fullmatch('[0-9a-f]{64}', token) is None:
        raise p.Denied('Gestion des contributions inaccessible')
    return bytes.fromhex(token)


def _manager(connection, token, now):
    raw = _token(token)
    row = connection.execute('SELECT manager_id,expires_at FROM s7_contribution_managers WHERE token_sha256=?',
                             (sha256(raw).hexdigest(),)).fetchone()
    if row is None or datetime.fromisoformat(row[1]) <= now:
        raise p.Denied('Gestion des contributions inaccessible')
    return row[0]


def _csrf(token):
    return sha256(b'contribution-withdraw:' + _token(token)).hexdigest()


def _active(row, revision, now, *, reconsent=False):
    if row is not None and (row['status'] != 'active' or datetime.fromisoformat(row['expires_at']) <= now):
        if not (reconsent and row['status'] == 'withdrawn'):
            raise p.Denied('Contribution retirée ou expirée')
    if (0 if row is None else row['revision']) != revision:
        raise ConflictError('Révision de contribution modifiée')


def change_contribution(store, session_token, dossier_id, body, manager_token=None, now=None):
    """Return (metadata, new token or None); enabled opt-in uses the latest revision CAS"""
    if (type(body) is not dict or set(body) != {'example_revision', 'revision', 'enabled'}
            or type(body['example_revision']) is not int or body['example_revision'] < 1
            or type(body['revision']) is not int or body['revision'] < 0
            or type(body['enabled']) is not bool):
        raise ValueError('Consentement et révisions explicites requis')
    requested_now = now
    now = _now(now)
    session_id, _, _ = p.session(store, session_token)
    connection = store._connection_checked()
    with _transaction(connection):
        version = _authorize(connection, session_id, dossier_id)
        current = p.owner(connection, session_id, dossier_id)
        if current != body['example_revision']:
            raise ConflictError('Version de l’exemple modifiée')
        existing = _contribution(connection, dossier_id, current)
        _active(existing, body['revision'], now, reconsent=body['enabled'])
    if not body['enabled'] and existing is None:
        return dict(kind='contribution', contribution=None), None
    payload = _contribution_payload(store, session_id, dossier_id, current) if body['enabled'] else None
    privacy = None
    event = None
    if not body['enabled']:
        from . import privacy
        if existing is None:
            raise IntegrityError('Contribution à retirer absente')
        event = privacy.journal_intent(store, 'contribution', existing['contribution_id'], now=now)
    new_token = None
    manager_id = None
    with _transaction(connection, write=True):
        now = _now(requested_now)
        expiry = _six_months(now).isoformat()
        if _authorize(connection, session_id, dossier_id) != version or p.owner(connection, session_id, dossier_id) != current:
            raise ConflictError('Contenu modifié pendant le consentement')
        existing = _contribution(connection, dossier_id, current)
        _active(existing, body['revision'], now, reconsent=body['enabled'])
        if body['enabled']:
            manager_expiry = existing['expires_at'] if existing and existing['status'] == 'active' else expiry
            try:
                manager_id = _manager(connection, manager_token, now)
            except p.Denied:
                # Deux premiers consentements sans cookie doivent émettre le même accès
                new_token = sha256(b'contribution-manager:' + _token(session_token)).hexdigest()
                digest = sha256(bytes.fromhex(new_token)).hexdigest()
                row = connection.execute('SELECT manager_id FROM s7_contribution_managers WHERE token_sha256=?', (digest,)).fetchone()
                manager_id = row[0] if row else secrets.token_hex(16)
                if row is None:
                    connection.execute('INSERT INTO s7_contribution_managers VALUES (?, ?, ?, ?)',
                                       (manager_id, digest, now.isoformat(), manager_expiry))
            connection.execute('UPDATE s7_contribution_managers SET expires_at=max(expires_at,?) WHERE manager_id=?',
                               (manager_expiry, manager_id))
        if not body['enabled']:
            if privacy is None or event is None:
                raise IntegrityError('Retrait non préparé')
            privacy.apply_revocation(connection, event)
        elif existing is not None and existing['status'] == 'active':
            if manager_id is None:
                raise IntegrityError('Gestionnaire de contribution absent')
            connection.execute('UPDATE s7_contributions SET revision=revision+1,content_version=?,status=?,payload_json=?,manager_id=? '
                "WHERE contribution_id=? AND revision=? AND status='active'",
                (version, 'active', payload, manager_id,
                 existing['contribution_id'], body['revision']))
        else:
            if manager_id is None:
                raise IntegrityError('Gestionnaire de contribution absent')
            connection.execute('INSERT INTO s7_contributions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (secrets.token_hex(16), manager_id, dossier_id, current, body['revision'] + 1, version, now.isoformat(), expiry, 'active', payload, NOTICE_VERSION))
        return dict(kind='contribution', contribution=_metadata(_contribution(connection, dossier_id, current))), new_token


def manager_view(store, token, now=None):
    now = _now(now)
    connection = store._connection_checked()
    with _transaction(connection):
        manager = _manager(connection, token, now)
        rows = connection.execute('SELECT contribution_id,created_at,expires_at,status FROM s7_contributions '
                                  'WHERE manager_id=? ORDER BY created_at,contribution_id', (manager,)).fetchall()
        return dict(kind='contributions', csrf_token=_csrf(token), contributions=[
            dict(id=identity, created_at=created, expires_at=expiry,
                 status='expired' if status == 'active' and datetime.fromisoformat(expiry) <= now else status)
            for identity, created, expiry, status in rows])


def withdraw(store, token, contribution_id, csrf, now=None):
    """Withdraw a copy without needing the source session or dossier to survive"""
    from . import privacy
    now = _now(now)
    identifier(contribution_id)
    if type(csrf) is not str or not hmac.compare_digest(csrf.encode(), _csrf(token).encode()):
        raise p.Denied('CSRF de retrait invalide')
    connection = store._connection_checked()
    with _transaction(connection):
        manager = _manager(connection, token, now)
        row = connection.execute('SELECT status FROM s7_contributions WHERE contribution_id=? AND manager_id=?',
                                 (contribution_id, manager)).fetchone()
        if row is None:
            raise p.Denied('Contribution inaccessible')
    if row[0] != 'withdrawn':
        event = privacy.journal_intent(store, 'contribution', contribution_id, now=now)
        with _transaction(connection, write=True):
            manager = _manager(connection, token, now)
            privacy.apply_revocation(connection, event)
    return manager_view(store, token, now=now)


def refresh_contributions(store, dossier_id):
    """Refresh only the currently consented example after a job, never extend expiry"""
    from . import privacy
    now = _now()
    connection = store._connection_checked()
    with _transaction(connection):
        row = connection.execute('SELECT session_id FROM s7_dossiers WHERE dossier_id=?', (dossier_id,)).fetchone()
        if row is None:
            return
        session_id = row[0]
        try:
            version = _authorize(connection, session_id, dossier_id)
        except privacy.Gone:
            return
        current = p.owner(connection, session_id, dossier_id)
        contribution = _contribution(connection, dossier_id, current)
        if (contribution is None or contribution['status'] != 'active'
                or datetime.fromisoformat(contribution['expires_at']) <= now
                or contribution['content_version'] == version):
            return
        _active(contribution, contribution['revision'], now)
    payload = _contribution_payload(store, session_id, dossier_id, current)
    with _transaction(connection, write=True):
        now = _now()
        if _authorize(connection, session_id, dossier_id) != version or p.owner(connection, session_id, dossier_id) != current:
            raise ConflictError('Contenu modifié pendant la contribution')
        existing = _contribution(connection, dossier_id, current)
        _active(existing, contribution['revision'], now)
        connection.execute('UPDATE s7_contributions SET payload_json=?,content_version=?,revision=revision+1 '
                           'WHERE contribution_id=? AND revision=?',
                           (payload, version, contribution['contribution_id'], contribution['revision']))


def expire_contributions(connection, now=None):
    """Clear expired copies inside the caller's maintenance transaction"""
    connection.execute("UPDATE s7_contributions SET status='expired',payload_json=NULL,revision=revision+1 "
                       "WHERE status='active' AND expires_at<=?", (_now(now).isoformat(),))


def remove_for_dossier(connection, dossier_id):
    """Purge corpus copies in the user deletion-request transaction, never on expiry"""
    identifier(dossier_id)
    connection.execute('DELETE FROM s7_contributions WHERE dossier_id=?', (dossier_id,))


def contribution_view(store, session_id, dossier_id, now=None):
    """Owner metadata for the current example, without inferring legacy consent"""
    now = _now(now)
    connection = store._connection_checked()
    with _transaction(connection):
        _authorize(connection, session_id, dossier_id)
        row = _contribution(connection, dossier_id, p.owner(connection, session_id, dossier_id))
        value = None if row is None else _metadata(row)
        if value and value['status'] == 'active' and datetime.fromisoformat(value['expires_at']) <= now:
            value['status'] = 'expired'
        return dict(kind='contribution', contribution=value)


def _date(value):
    if type(value) is not str:
        raise ValueError('Date textuelle requise')
    date = _now(datetime.fromisoformat(value))
    if date.isoformat() != value:
        raise ValueError('Date UTC non canonique')
    return date


def _positive(value):
    if type(value) is not int or value < 1:
        raise ValueError('Version positive requise')


def _texts(values):
    if type(values) is not list or any(type(value) is not str or _SECRET.search(value) for value in values):
        raise ValueError('Textes fermés requis')


def _pieces(values):
    if type(values) is not list:
        raise ValueError('Pièces requises')
    for value in values:
        _fields(value, ('name', 'text'), 'piece')
        _texts([value['name'], value['text']])


def _check_revision(value):
    _fields(value, ('number', 'instruction', 'deliverables', 'criteria', 'pieces', 'qualification'), 'example')
    _positive(value['number'])
    _texts([value['instruction'], value['qualification']])
    _texts(value['deliverables'])
    _texts(value['criteria'])
    _pieces(value['pieces'])


def _check_campaigns(values):
    if type(values) is not list:
        raise ValueError('Campagnes requises')
    for value in values:
        _fields(value, ('id', 'models'), 'campaign')
        identifier(value['id'])
        if type(value['models']) is not list:
            raise ValueError('Modèles requis')
        for model in value['models']:
            _fields(model, ('name', 'verdict', 'cost', 'answer', 'evidence'), 'model')
            _texts([model['name']])
            if model['verdict'] not in (None, 'SATISFAIT', 'NE SATISFAIT PAS', 'INDETERMINE'):
                raise ValueError('Verdict invalide')
            _fields(model['cost'], ('amount', 'currency'), 'cost')
            if model['cost']['amount'] is not None:
                _money(model['cost']['amount'])
            _texts([model['cost']['currency']])
            if model['answer'] is not None:
                _texts([model['answer']])
            _pieces(model['evidence'])


def verify(store, connection):
    """Validate exact owned schema, copy lifetimes and closed persisted records"""
    objects = [row for row in connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema')
               if row[2] in _TABLES]
    if _expected_schema(tuple(objects)) != _expected_schema(tuple(schema_objects())):
        raise SchemaError('Schéma des archives ou contributions divergent')
    try:
        for snapshot, dossier, version, raw, digest in connection.execute('SELECT * FROM s7_archives'):
            identifier(snapshot)
            identifier(dossier)
            _positive(version)
            lifecycle = connection.execute('SELECT content_version FROM s7_dossiers WHERE dossier_id=?', (dossier,)).fetchone()
            if lifecycle is None or version > lifecycle[0] or type(raw) is not bytes or sha256(raw).hexdigest() != digest:
                raise ValueError('Archive divergente')
            value = json.loads(raw, object_pairs_hook=_unique_object)
            _fields(value, ('format', 'dossier_id', 'content_version', 'need', 'messages', 'revisions', 'campaigns'), 'archive')
            if (value['format'] != 'bench-x/history/v1' or value['dossier_id'] != dossier
                    or type(value['content_version']) is not int or value['content_version'] != version):
                raise ValueError('Identité d’archive divergente')
            _texts([value['need']])
            _texts(value['messages'])
            if type(value['revisions']) is not list or not value['revisions']:
                raise ValueError('Historique requis')
            numbers = []
            for revision in value['revisions']:
                _check_revision(revision)
                numbers.append(revision['number'])
            if numbers != sorted(set(numbers)):
                raise ValueError('Historique divergent')
            _check_campaigns(value['campaigns'])
        managers = {}
        for identity, digest, created, expiry in connection.execute('SELECT * FROM s7_contribution_managers'):
            identifier(identity)
            if type(digest) is not str or re.fullmatch('[0-9a-f]{64}', digest) is None or _date(expiry) <= _date(created):
                raise ValueError('Gestionnaire divergent')
            managers[identity] = (_date(created), _date(expiry))
        for row in connection.execute('SELECT * FROM s7_contributions'):
            identity, manager, dossier, example, revision, version, created, expiry, status, raw, notice = row
            identifier(identity)
            identifier(dossier)
            for number in (example, revision, version):
                _positive(number)
            if (notice != NOTICE_VERSION or manager not in managers or _six_months(_date(created)) != _date(expiry)
                    or not _date(created) < _date(expiry) <= managers[manager][1]
                    or status not in ('active', 'withdrawn', 'expired') or (status == 'active') != (raw is not None)):
                raise ValueError('Consentement divergent')
            if raw is None:
                continue
            if type(raw) is not str:
                raise ValueError('Copie textuelle requise')
            value = json.loads(raw, object_pairs_hook=_unique_object)
            _fields(value, ('format', 'example', 'campaigns'), 'contribution')
            if value['format'] != 'bench-x/contribution/v1' or value['example']['number'] != example:
                raise ValueError('Version de contribution divergente')
            _check_revision(value['example'])
            _check_campaigns(value['campaigns'])
            if _contribution_sensitive(value):
                raise ValueError('Contenu sensible dans la contribution')
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError) as error:
        raise IntegrityError('Archive ou contribution altérée') from error
