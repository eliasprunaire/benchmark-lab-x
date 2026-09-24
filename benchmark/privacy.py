"""Accès, échéances et suppression des données privées"""
from contextlib import ExitStack, closing, contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys

from . import storage
from .storage import ConflictError, IntegrityError, SchemaError, _transaction


FORMAT = 'bench-x/privacy/v1'
SESSION_LIFETIME = timedelta(days=30)
DOSSIER_LIFETIME = timedelta(days=7)
_TABLES = {
    's7_control': """CREATE TABLE s7_control (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        format_identity TEXT NOT NULL CHECK(format_identity='bench-x/privacy/v1'),
        migration_id TEXT NOT NULL,
        migrated_at TEXT NOT NULL,
        phase TEXT NOT NULL CHECK(phase IN ('MIGRATED','READY')),
        verified_boot TEXT NOT NULL
    )""",
    's7_sessions': """CREATE TABLE s7_sessions (
        session_id TEXT PRIMARY KEY NOT NULL REFERENCES s2_sessions(session_id),
        retention_started_at TEXT NOT NULL,
        last_activity_at TEXT,
        expires_at TEXT NOT NULL
    )""",
    's7_dossiers': """CREATE TABLE s7_dossiers (
        dossier_id TEXT PRIMARY KEY NOT NULL,
        session_id TEXT NOT NULL REFERENCES s2_sessions(session_id),
        retention_started_at TEXT NOT NULL,
        last_activity_at TEXT,
        expires_at TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('ACTIVE','DELETE_REQUESTED','PURGING','PURGED')),
        content_version INTEGER NOT NULL CHECK(content_version>0),
        delete_reason TEXT CHECK(delete_reason IN ('user','expired'))
    )""",
    's7_purge_files': """CREATE TABLE s7_purge_files (
        relative_path TEXT PRIMARY KEY NOT NULL,
        dossier_id TEXT NOT NULL REFERENCES s7_dossiers(dossier_id),
        sha256 TEXT NOT NULL CHECK(length(sha256)=64),
        size_bytes INTEGER NOT NULL CHECK(size_bytes>=0)
    )""",
    's7_retired_operations': """CREATE TABLE s7_retired_operations (
        operation_id TEXT PRIMARY KEY NOT NULL,
        session_id TEXT NOT NULL REFERENCES s2_sessions(session_id),
        dossier_id TEXT NOT NULL REFERENCES s7_dossiers(dossier_id),
        budget_id TEXT NOT NULL REFERENCES budgets(budget_id),
        phase TEXT NOT NULL CHECK(phase IN ('preparation','correction','qualification','judgment','acquisition')),
        state TEXT NOT NULL CHECK(state IN ('INTENT_RECORDED','EMISSION_POSSIBLE','AMBIGUOUS','RECEIVED')),
        reserved_amount TEXT NOT NULL,
        observed_cost_json TEXT,
        provider_managed INTEGER NOT NULL CHECK(provider_managed IN (0,1))
    )""",
    's7_revocations': """CREATE TABLE s7_revocations (
        event_id TEXT PRIMARY KEY NOT NULL,
        applied_at TEXT NOT NULL
    )""",
}


def now():
    return datetime.now(timezone.utc)


def date(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise IntegrityError('Date privée UTC requise')
    return result


def available(connection):
    return connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s7_control'").fetchone() is not None


def boot_identity():
    if sys.platform != 'linux':
        return 'non-linux'
    try:
        identity = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        return identity if re.fullmatch('[0-9a-f-]{36}', identity) else None
    except OSError:
        return None


def boot_pending(connection):
    if not available(connection):
        return False
    identity = boot_identity()
    row = connection.execute('SELECT verified_boot FROM s7_control').fetchone()
    return identity is None or row != (identity,)


def quarantined(store):
    return os.path.lexists(store._root / 'restore.json') or boot_pending(store._connection_checked())


def schema_objects():
    from . import privacy_archive
    return ([('table', name, name, sql) for name, sql in _TABLES.items()]
            + [('index', f'sqlite_autoindex_{name}_1', name, None)
               for name in _TABLES if name != 's7_control']
            + [('trigger', name, table, sql) for name, (table, sql) in version_triggers().items()]
            + privacy_archive.schema_objects())


def version_triggers():
    result = {}
    direct = {'s2_dossiers': 'UPDATE OF current_revision', 's2_qualifications': 'INSERT', 's2_validations': 'INSERT',
              'operations': 'UPDATE OF state,receipt_json,observed_cost_json'}
    for table, action in direct.items():
        name = table + '_privacy_version'
        result[name] = (table, f'CREATE TRIGGER {name} AFTER {action} ON {table} '
                       "BEGIN UPDATE s7_dossiers SET content_version=content_version+1 WHERE dossier_id=NEW.dossier_id; END")
    for table in ('s4_campaigns', 's4_results', 's5_evaluations'):
        name = table + '_privacy_version'
        campaign = ('NEW.campaign_id' if table != 's4_results' else
                    '(SELECT campaign_id FROM s4_attempts WHERE operation_id=NEW.operation_id)')
        target = (f'SELECT c.dossier_id FROM s3_contracts c JOIN s4_campaigns a USING(contract_sha256) WHERE a.campaign_id={campaign} '
                  f'UNION SELECT c.dossier_id FROM s2_comparison_contracts c JOIN s4_campaigns a USING(contract_sha256) WHERE a.campaign_id={campaign}')
        result[name] = (table, f'CREATE TRIGGER {name} AFTER INSERT ON {table} '
                       f'BEGIN UPDATE s7_dossiers SET content_version=content_version+1 WHERE dossier_id IN ({target}); END')
    return result


def extended_schema(previous):
    return [(kind, name, table, deletion_trigger(table) if
             kind == 'trigger' and name.endswith('_delete') and not table.endswith('_control') else sql)
            for kind, name, table, sql in previous] + schema_objects()


def deletion_trigger(table):
    contract = ('SELECT dossier_id FROM s3_contracts WHERE contract_sha256=OLD.contract_sha256 '
                'UNION SELECT dossier_id FROM s2_comparison_contracts WHERE contract_sha256=OLD.contract_sha256')
    campaign = ('SELECT c.dossier_id FROM s3_contracts c JOIN s4_campaigns a USING(contract_sha256) '
                'WHERE a.campaign_id=OLD.campaign_id UNION '
                'SELECT c.dossier_id FROM s2_comparison_contracts c JOIN s4_campaigns a USING(contract_sha256) '
                'WHERE a.campaign_id=OLD.campaign_id')
    if table == 's3_contracts':
        owner = 'SELECT OLD.dossier_id'
    elif table in ('s3_qualifications', 's3_approvals', 's4_campaigns'):
        owner = contract
    elif table in ('s4_emissions', 's4_results'):
        owner = ('SELECT c.dossier_id FROM s3_contracts c JOIN s4_campaigns a USING(contract_sha256) '
                 'JOIN s4_attempts t USING(campaign_id) WHERE t.operation_id=OLD.operation_id UNION '
                 'SELECT c.dossier_id FROM s2_comparison_contracts c JOIN s4_campaigns a USING(contract_sha256) '
                 'JOIN s4_attempts t USING(campaign_id) WHERE t.operation_id=OLD.operation_id')
    else:
        owner = campaign
    return (f'CREATE TRIGGER {table}_delete BEFORE DELETE ON {table} '
            f"WHEN NOT EXISTS (SELECT 1 FROM s7_dossiers WHERE state='PURGING' AND dossier_id IN ({owner})) "
            "BEGIN SELECT RAISE(ABORT, 'immutable evidence outside privacy purge'); END")


def migration_status(store):
    connection = store._connection_checked()
    if not available(connection):
        return {'layout': storage._check_schema(connection), 'migration_id': None}
    row = connection.execute('SELECT migration_id,migrated_at,phase FROM s7_control WHERE singleton=1').fetchone()
    return {'layout': 's7', 'migration_id': row[0], 'migrated_at': row[1], 'phase': row[2],
            'restore_pending': quarantined(store)}


def migrate(data, secret, migration_id, *, now=None):
    from .runtime import worker_lock
    from . import provider_access
    if type(migration_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', migration_id):
        raise ValueError('Identité de migration requise')
    current = now or globals()['now']()
    with closing(storage.Store(Path(data))) as store, worker_lock(store):
        connection = store._connection_checked()
        layout = storage._check_schema(connection)
        if layout == 's7':
            status = migration_status(store)
            if status['migration_id'] != migration_id:
                raise ConflictError('Identité de migration différente')
            finish_migration(store)
            return migration_status(store)
        if layout != 's6':
            raise SchemaError('Migration explicite s6 vers s7 requise')
        proof = store.verify_storage()
        if not proof['integrity_ok'] or proof['orphan_files']:
            raise IntegrityError('Stockage intact et arrêté requis')
        with _transaction(connection, write=True):
            # Le déchiffrement complet précède toute validation de la migration
            for session_id, key, verifier in connection.execute(
                    'SELECT session_id,key_cipher,verifier_cipher FROM s2_provider_access').fetchall():
                values = []
                for value, purpose in ((key, 'key'), (verifier, 'oauth')):
                    if value is not None and value.startswith(provider_access.CIPHER_VERSION + ':'):
                        provider_access.decrypt(secret, value, session_id, purpose)
                    elif value is not None:
                        value = provider_access.reencrypt_legacy(secret, value, session_id, purpose)
                    values.append(value)
                connection.execute('UPDATE s2_provider_access SET key_cipher=?,verifier_cipher=? WHERE session_id=?',
                                   (*values, session_id))
            for sql in _TABLES.values():
                connection.execute(sql)
            from . import privacy_archive
            for sql in privacy_archive.create_statements():
                connection.execute(sql)
            for _, sql in version_triggers().values():
                connection.execute(sql)
            for name, table in connection.execute("SELECT name,tbl_name FROM sqlite_schema WHERE type='trigger' AND name LIKE '%_delete'").fetchall():
                if not table.endswith('_control'):
                    connection.execute(f'DROP TRIGGER {name}')
                    connection.execute(deletion_trigger(table))
            identity = boot_identity()
            if identity is None:
                raise IntegrityError('Identité du démarrage indisponible')
            connection.execute("INSERT INTO s7_control VALUES (1,?,?,?,'MIGRATED',?)",
                               (FORMAT, migration_id, current.isoformat(), identity))
            for (session_id,) in connection.execute('SELECT session_id FROM s2_sessions').fetchall():
                register_session(connection, session_id, current, legacy=True)
            for dossier_id, session_id in connection.execute('SELECT dossier_id,session_id FROM s2_dossiers').fetchall():
                register_dossier(connection, session_id, dossier_id, current, legacy=True)
            storage._check_schema(connection)
        finish_migration(store)
        return migration_status(store)


def finish_migration(store):
    connection = store._connection_checked()
    if connection.execute('SELECT phase FROM s7_control').fetchone() == ('MIGRATED',):
        with journal_writer(store):
            pass
        # VACUUM traite une seule fois les anciennes pages libres avant réouverture
        connection.execute('VACUUM')
        with _transaction(connection, write=True):
            connection.execute("UPDATE s7_control SET phase='READY'")


def register_session(connection, session_id, current=None, *, legacy=False):
    if available(connection):
        current = current or now()
        connection.execute('INSERT INTO s7_sessions VALUES (?,?,?,?)',
                           (session_id, current.isoformat(), None if legacy else current.isoformat(),
                            (current + SESSION_LIFETIME).isoformat()))


def register_dossier(connection, session_id, dossier_id, current=None, *, legacy=False):
    if available(connection):
        current = current or now()
        connection.execute("INSERT INTO s7_dossiers VALUES (?,?,?,?,?,'ACTIVE',1,NULL)",
                           (dossier_id, session_id, current.isoformat(), None if legacy else current.isoformat(),
                            (current + DOSSIER_LIFETIME).isoformat()))


def authorize_session(connection, session_id, current=None):
    from .preparation import Denied
    if not available(connection):
        return
    if boot_pending(connection):
        raise Denied('RESTORE_PENDING')
    if connection.execute('SELECT phase FROM s7_control').fetchone() != ('READY',):
        raise Denied('PRIVACY_MIGRATION_PENDING')
    row = connection.execute('SELECT expires_at FROM s7_sessions WHERE session_id=?', (session_id,)).fetchone()
    if row is None or date(row[0]) <= (current or now()):
        raise Denied('SESSION_EXPIRED')


class Gone(LookupError):
    """Un propriétaire authentifié demande un dossier expiré"""


def authorize_dossier(connection, session_id, dossier_id, current=None):
    from .preparation import Denied
    if not available(connection):
        return
    authorize_session(connection, session_id, current)
    row = connection.execute('SELECT state,expires_at FROM s7_dossiers WHERE dossier_id=? AND session_id=?',
                             (dossier_id, session_id)).fetchone()
    if row is None:
        raise Denied('NOT_FOUND')
    if row[0] != 'ACTIVE' or date(row[1]) <= (current or now()):
        raise Gone('Ce cas d’usage n’est plus accessible sur le serveur')


def activity(store, session_id, dossier_id=None, *, current=None):
    connection = store._connection_checked()
    current = current or now()
    with _transaction(connection, write=True):
        authorize_session(connection, session_id, current)
        if dossier_id is not None:
            authorize_dossier(connection, session_id, dossier_id, current)
            connection.execute('UPDATE s7_dossiers SET last_activity_at=?,expires_at=? WHERE dossier_id=?',
                               (current.isoformat(), (current + DOSSIER_LIFETIME).isoformat(), dossier_id))
        expires = (current + SESSION_LIFETIME).isoformat()
        connection.execute('UPDATE s7_sessions SET last_activity_at=?,expires_at=? WHERE session_id=?',
                           (current.isoformat(), expires, session_id))
    return {'session_expires_at': expires}


def verify(store, connection):
    from . import privacy_archive
    privacy_archive.verify(store, connection)
    if connection.execute('SELECT singleton,format_identity FROM s7_control').fetchall() != [(1, FORMAT)]:
        raise IntegrityError('Identité de confidentialité divergente')
    if connection.execute('SELECT session_id FROM s2_sessions EXCEPT SELECT session_id FROM s7_sessions').fetchone():
        raise IntegrityError('Échéance de session absente')
    if connection.execute('SELECT dossier_id FROM s2_dossiers EXCEPT SELECT dossier_id FROM s7_dossiers').fetchone():
        raise IntegrityError('Échéance de dossier absente')
    for table in ('s7_sessions', 's7_dossiers'):
        for started, active, expires in connection.execute(f'SELECT retention_started_at,last_activity_at,expires_at FROM {table}'):
            if date(expires) < date(started) or active is not None and date(active) > date(expires):
                raise IntegrityError('Échéance privée incohérente')
    pending_files(store, connection)
    for operation in retired_operations(connection):
        storage._money(operation['reserved_amount'])
        cost = operation['observed_cost']
        if cost is not None:
            storage._fields(cost, ('status', 'amount', 'currency', 'source'), 'coût retiré')
            if cost['status'] == 'KNOWN':
                storage._money(cost['amount'])
            elif cost['status'] != 'UNKNOWN' or cost['amount'] is not None:
                raise IntegrityError('Coût retiré invalide')


def operation_allowed(connection, dossier_id):
    if not available(connection):
        return
    if boot_pending(connection):
        raise ConflictError('Rapprochement requis après redémarrage de la machine')
    row = connection.execute('SELECT session_id FROM s7_dossiers WHERE dossier_id=?', (dossier_id,)).fetchone()
    # Les objets opérateur hors parcours privé gardent leur contrat d'origine
    if row:
        authorize_dossier(connection, row[0], dossier_id)


def visible(connection, session_id, dossier_id):
    from .preparation import Denied
    try:
        authorize_dossier(connection, session_id, dossier_id)
        return True
    except (Gone, Denied):
        return False


def journal_path(store):
    configured = os.environ.get('BENCHMARK_PRIVACY_JOURNAL')
    path = Path(configured) if configured else store._root.parent / 'privacy-revocations' / 'revocations.jsonl'
    if not path.is_absolute() or path != path.resolve() or store._root == path.parent or store._root in path.parents:
        raise IntegrityError('Journal de révocation hors stockage restauré requis')
    return path


@contextmanager
def journal_writer(store):
    path = journal_path(store)
    path.parent.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise IntegrityError('Répertoire de révocation non privé')
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise IntegrityError('Journal de révocation non privé')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
        os.fsync(fd)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)


def journal_intent(store, kind, identifier, now=None):
    if kind not in ('dossier', 'contribution', 'key') or type(identifier) is not str or not identifier:
        raise ValueError('Révocation invalide')
    current = now or globals()['now']()
    event = dict(event_id=secrets.token_hex(16), kind=kind, identifier=identifier, at=current.isoformat())
    raw = (storage._strict_json(event) + '\n').encode()
    with journal_writer(store) as fd:
        while raw:
            written = os.write(fd, raw)
            if written <= 0:
                raise OSError('Écriture du journal interrompue')
            raw = raw[written:]
    return event


def request_delete(store, session_id, dossier_id):
    connection = store._connection_checked()
    authorize_session(connection, session_id)
    row = connection.execute('SELECT state FROM s7_dossiers WHERE dossier_id=? AND session_id=?',
                             (dossier_id, session_id)).fetchone()
    if row is None:
        from .preparation import Denied
        raise Denied('NOT_FOUND')
    event = journal_intent(store, 'dossier', dossier_id)
    with _transaction(connection, write=True):
        apply_revocation(connection, event)
    return {'dossier_id': dossier_id, 'status': 'purged' if row[0] == 'PURGED' else 'delete_requested'}


def apply_revocation(connection, event):
    if connection.execute('SELECT 1 FROM s7_revocations WHERE event_id=?', (event['event_id'],)).fetchone():
        return
    if event['kind'] == 'dossier':
        connection.execute("UPDATE s7_dossiers SET state=CASE WHEN state='ACTIVE' THEN 'DELETE_REQUESTED' ELSE state END,"
                           "delete_reason='user' WHERE dossier_id=?", (event['identifier'],))
        from .privacy_archive import remove_for_dossier
        remove_for_dossier(connection, event['identifier'])
    elif event['kind'] == 'contribution':
        connection.execute("UPDATE s7_contributions SET status='withdrawn',payload_json=NULL,revision=revision+1 "
                           "WHERE contribution_id=? AND status!='withdrawn'", (event['identifier'],))
    elif event['kind'] == 'key':
        session_id = event['identifier']
        row = connection.execute('SELECT created_at FROM s2_provider_access WHERE session_id=?', (session_id,)).fetchone()
        # La date couvre aussi les clés d'une sauvegarde antérieure au rechiffrement
        if row and date(row[0]) <= date(event['at']):
            from .provider_access import _delete
            _delete(connection, session_id)
    connection.execute('INSERT INTO s7_revocations VALUES (?,?)', (event['event_id'], event['at']))


def replay_revocations(store):
    path = journal_path(store)
    if not path.exists():
        if store._connection.execute('SELECT 1 FROM s7_revocations LIMIT 1').fetchone():
            raise IntegrityError('Journal de révocation absent')
        return
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise IntegrityError('Journal de révocation invalide')
        with os.fdopen(fd, 'r', encoding='utf-8', closefd=False) as stream:
            fcntl.flock(fd, fcntl.LOCK_SH)
            for line in stream:
                if not line.endswith('\n'):
                    raise IntegrityError('Journal de révocation incomplet')
                event = json.loads(line, object_pairs_hook=storage._unique_object)
                storage._fields(event, ('event_id', 'kind', 'identifier', 'at'), 'révocation')
                date(event['at'])
                if event['kind'] not in ('dossier', 'key', 'contribution'):
                    raise IntegrityError('Révocation inconnue')
                with _transaction(store._connection, write=True):
                    apply_revocation(store._connection, event)
    finally:
        os.close(fd)


def _contains_reference(value, identifiers):
    if isinstance(value, str):
        if value in identifiers:
            return True
        if value.startswith(('{', '[')):
            try:
                return _contains_reference(json.loads(value), identifiers)
            except (ValueError, RecursionError):
                return False
    if isinstance(value, dict):
        return any(_contains_reference(v, identifiers) for v in value.values())
    if isinstance(value, list):
        return any(_contains_reference(v, identifiers) for v in value)
    return False


def _purge_dossier(store, connection, dossier_id, session_id, remaining, manifests):
    """`remaining` et `manifests` sont l'état courant du passage ; le dossier purgé en est retiré"""
    operations = [op for op in remaining if op['dossier_id'] == dossier_id]
    if any(not store._provider_managed_budget(connection, op['budget_id']) for op in operations):
        return False
    contracts = {r[0] for table in ('s3_contracts', 's2_comparison_contracts') for r in connection.execute(
        f'SELECT contract_sha256 FROM {table} WHERE dossier_id=?', (dossier_id,))}
    campaigns = {row[0] for row in connection.execute('SELECT campaign_id,contract_sha256 FROM s4_campaigns') if row[1] in contracts}
    pieces = connection.execute('SELECT piece_id,relative_path,sha256,size_bytes FROM pieces WHERE dossier_id=?', (dossier_id,)).fetchall()
    identifiers = contracts | campaigns | {op['operation_id'] for op in operations} | {p[0] for p in pieces} | {dossier_id}
    for op in remaining:
        if op['dossier_id'] != dossier_id and _contains_reference(op['resources'], identifiers):
            raise IntegrityError('Référence entrante depuis un dossier conservé')
    for campaign_id, manifest in manifests.items():
        if campaign_id not in campaigns and _contains_reference(manifest, identifiers):
            raise IntegrityError('Référence de campagne conservée')
    connection.execute("UPDATE s7_dossiers SET state='PURGING' WHERE dossier_id=?", (dossier_id,))
    for _, path, digest, size in pieces:
        connection.execute('INSERT INTO s7_purge_files VALUES (?,?,?,?)', (path, dossier_id, digest, size))
    for campaign_id in campaigns:
        while True:
            leaves = connection.execute('SELECT evaluation_id FROM s5_evaluations WHERE campaign_id=? AND evaluation_id NOT IN '
                '(SELECT previous_evaluation_id FROM s5_evaluations WHERE previous_evaluation_id IS NOT NULL)', (campaign_id,)).fetchall()
            if not leaves:
                break
            connection.executemany('DELETE FROM s5_evaluations WHERE evaluation_id=?', leaves)
        for table in ('s4_results', 's4_emissions'):
            connection.execute(f'DELETE FROM {table} WHERE operation_id IN (SELECT operation_id FROM s4_attempts WHERE campaign_id=?)', (campaign_id,))
        for table in ('s4_attempts', 's4_status', 's4_caps', 's4_admissions', 's4_cells', 's4_campaigns'):
            connection.execute(f'DELETE FROM {table} WHERE campaign_id=?', (campaign_id,))
    for contract in contracts:
        for table in ('s3_approvals', 's3_qualifications', 's3_contracts', 's2_comparison_contracts'):
            connection.execute(f'DELETE FROM {table} WHERE contract_sha256=?', (contract,))
    for table in ('s2_validations', 's2_qualifications', 's2_actions'):
        connection.execute(f'DELETE FROM {table} WHERE dossier_id=?', (dossier_id,))
    reconciliation = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='cost_reconciliations'").fetchone()
    for op in operations:
        cost = store._effective_cost(connection, op)
        # Les textes de reçu et les ressources ne rejoignent jamais le registre retiré
        retained_cost = None if cost is None else dict(status=cost['status'], amount=cost['amount'], currency=cost['currency'], source='Retained cost metadata')
        connection.execute('INSERT INTO s7_retired_operations VALUES (?,?,?,?,?,?,?,?,1)',
            (op['operation_id'], session_id, dossier_id, op['budget_id'], op['phase'], op['state'], op['reserved_amount'],
             None if retained_cost is None else storage._strict_json(retained_cost)))
        if reconciliation:
            connection.execute('DELETE FROM cost_reconciliations WHERE operation_id=?', (op['operation_id'],))
        connection.execute('DELETE FROM reservations WHERE operation_id=?', (op['operation_id'],))
        connection.execute('DELETE FROM operations WHERE operation_id=?', (op['operation_id'],))
    for table in ('pieces', 's2_revisions', 's2_dossiers', 'dossier_revisions'):
        connection.execute(f'DELETE FROM {table} WHERE dossier_id=?', (dossier_id,))
    connection.execute('DELETE FROM s7_archives WHERE dossier_id=?', (dossier_id,))
    remaining[:] = [op for op in remaining if op['dossier_id'] != dossier_id]
    for campaign_id in campaigns:
        manifests.pop(campaign_id, None)
    return True


def pending_files(store, connection):
    rows = connection.execute('SELECT relative_path,sha256,size_bytes FROM s7_purge_files p JOIN s7_dossiers d USING(dossier_id) WHERE d.state=\'PURGING\'').fetchall()
    if len(rows) != connection.execute('SELECT count(*) FROM s7_purge_files').fetchone()[0]:
        raise IntegrityError('Purge sans dossier correspondant')
    for path, digest, size in rows:
        if not re.fullmatch(r'pieces/[A-Za-z0-9_.-]+', path) or path.rsplit('/', 1)[1] in ('.', '..'):
            raise IntegrityError('Chemin de purge invalide')
        if connection.execute('SELECT 1 FROM pieces WHERE relative_path=?', (path,)).fetchone():
            raise IntegrityError('Pièce encore référencée')
        try:
            fd = os.open(path.split('/')[1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=store._pieces_fd)
        except FileNotFoundError:
            continue
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size != size:
                raise IntegrityError('Fichier de purge divergent')
            hashed = sha256()
            while block := os.read(fd, 65536):
                hashed.update(block)
            if hashed.hexdigest() != digest:
                raise IntegrityError('Contenu de purge divergent')
        finally:
            os.close(fd)
    return [row[0] for row in rows]


def purge(data, *, wait=0.0, drain=0.0, _reconciled_store=None):
    """Purge sous verrou exclusif

    `wait` borne l'attente de la porte : les tâches de fond la tiennent et le service reste ouvert.
    `drain` borne ensuite l'attente des requêtes en cours, porte fermée : c'est la seule attente en 503
    """
    from .runtime import maintenance_gate, worker_lock
    with closing(storage.Store(Path(data))) if _reconciled_store is None else nullcontext(_reconciled_store) as store:
        connection = store._connection_checked()
        if not available(connection):
            raise SchemaError('Initialisation de la confidentialité requise')
        if _reconciled_store is None and quarantined(store):
            return {'purged': [], 'pending': True, 'reason': 'RESTORE_PENDING', 'lock': 'NOT_ATTEMPTED'}
        replay_revocations(store)
        current = now().isoformat()
        with _transaction(connection, write=True):
            connection.execute("UPDATE s7_dossiers SET state='DELETE_REQUESTED',delete_reason='expired' WHERE state='ACTIVE' AND expires_at<=?", (current,))
            from .provider_access import _delete
            for (session_id,) in connection.execute('SELECT session_id FROM s7_sessions WHERE expires_at<=?', (current,)).fetchall():
                _delete(connection, session_id)
            from .privacy_archive import expire_contributions
            expire_contributions(connection)
        with ExitStack() as held:
            try:
                # Le rapprochement tient déjà le verrou exclusif : la porte n'y ajouterait qu'un refus
                if _reconciled_store is None:
                    held.enter_context(maintenance_gate(store, exclusive=True, wait=wait))
                held.enter_context(worker_lock(store, wait=drain))
            except BlockingIOError:
                return {'purged': [], 'pending': True, 'lock': 'UNAVAILABLE'}
            proof = store.verify_storage()
            if not proof['integrity_ok'] or proof['orphan_files']:
                raise IntegrityError('Stockage à rapprocher avant purge')
            deferred = []
            # Le verrou exclusif fige le stockage : une lecture par passage au lieu de deux par dossier
            remaining = store._operations(connection)
            manifests = {campaign_id: json.loads(raw) for campaign_id, raw in
                         connection.execute('SELECT campaign_id,manifest_json FROM s4_campaigns')}
            for dossier_id, session_id in connection.execute("SELECT dossier_id,session_id FROM s7_dossiers WHERE state='DELETE_REQUESTED'").fetchall():
                try:
                    with _transaction(connection, write=True):
                        finished = _purge_dossier(store, connection, dossier_id, session_id, remaining, manifests)
                    if not finished:
                        deferred.append(dossier_id)
                except IntegrityError:
                    deferred.append(dossier_id)
            for path in pending_files(store, connection):
                pieces_fd = store._pieces_fd
                if pieces_fd is None:
                    raise IntegrityError('Répertoire des pièces fermé')
                try:
                    os.unlink(path.split('/')[1], dir_fd=pieces_fd)
                except FileNotFoundError:
                    pass
                os.fsync(pieces_fd)
                with _transaction(connection, write=True):
                    connection.execute('DELETE FROM s7_purge_files WHERE relative_path=?', (path,))
            with _transaction(connection, write=True):
                completed = [row[0] for row in connection.execute("SELECT dossier_id FROM s7_dossiers WHERE state='PURGING' AND dossier_id NOT IN (SELECT dossier_id FROM s7_purge_files)")]
                connection.executemany("UPDATE s7_dossiers SET state='PURGED' WHERE dossier_id=?", [(d,) for d in completed])
                retire_expired_access(connection, date(current))
            return {'purged': completed, 'pending': bool(deferred), 'operator_review': deferred, 'lock': 'ACQUIRED'}


def retired_operations(connection):
    if not available(connection):
        return []
    return [dict(operation_id=oid, budget_id=budget, phase=phase, state=state, reserved_amount=reserve,
                 observed_cost=None if raw is None else json.loads(raw))
            for oid, budget, phase, state, reserve, raw in connection.execute(
                'SELECT operation_id,budget_id,phase,state,reserved_amount,observed_cost_json FROM s7_retired_operations')]


def retire_expired_access(connection, current):
    """Les anciennes identités ne peuvent plus émettre après expiration et purge"""
    sessions = connection.execute('SELECT session_id FROM s7_sessions WHERE expires_at<=? AND session_id NOT IN '
        "(SELECT session_id FROM s7_dossiers WHERE state!='PURGED')", (current.isoformat(),)).fetchall()
    for (session_id,) in sessions:
        connection.execute('DELETE FROM s7_retired_operations WHERE session_id=?', (session_id,))
        connection.execute('DELETE FROM s7_dossiers WHERE session_id=?', (session_id,))
        connection.execute('DELETE FROM s7_sessions WHERE session_id=?', (session_id,))
        connection.execute('DELETE FROM s2_sessions WHERE session_id=?', (session_id,))
    connection.execute("DELETE FROM s7_contributions WHERE status!='active' AND expires_at<=?", (current.isoformat(),))
    connection.execute('DELETE FROM s7_contribution_managers WHERE expires_at<=? AND manager_id NOT IN '
                       '(SELECT manager_id FROM s7_contributions)', (current.isoformat(),))


def reconcile(data, journal_sha256):
    """Rapprochement explicite avec l'empreinte actuelle attestée hors de la VM"""
    from .runtime import stop, worker_lock
    if type(journal_sha256) is not str or not re.fullmatch('[0-9a-f]{64}', journal_sha256):
        raise ValueError('Empreinte externe du journal requise')
    with closing(storage.Store(Path(data))) as store, worker_lock(store):
        path = journal_path(store)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            hashed = sha256()
            while block := os.read(fd, 65536):
                hashed.update(block)
            if hashed.hexdigest() != journal_sha256:
                raise IntegrityError('Journal différent de la preuve externe')
            identity = boot_identity()
            if identity is None:
                raise IntegrityError('Identité du démarrage indisponible')
            stop(data, store, 'PRIVACY_RECONCILED_ADMISSION_CLOSED')
            result = purge(data, _reconciled_store=store)
            if result['pending']:
                raise ConflictError('Purge non terminée ; rapprochement non validé')
            connection = store._connection
            if connection is None:
                raise IntegrityError('Stockage fermé')
            with _transaction(connection, write=True):
                connection.execute('UPDATE s7_control SET verified_boot=?', (identity,))
            # La quarantaine financière restore.json appartient au rapprochement existant
            return migration_status(store)
        finally:
            os.close(fd)
