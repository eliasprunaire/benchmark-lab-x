"""Private S1 dossiers, pieces and durable operation/budget records.

Emission is a caller responsibility, after mark_emission_possible commits.
Opening or inspecting storage never retries an operation or releases a reserve.
Schema 1 recognizes the canary, integrated S1 and explicit S2–S5 extensions.
Each extension has its own structure identity; no implicit migration is performed.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext, MAX_EMAX, MIN_EMIN
from functools import cache, lru_cache
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat

from .model_catalog import require_current


SCHEMA_VERSION = 1
PREPARATION_IDENTITY = "benchmark-lab-x/preparation/v1"
RECONCILIATION_IDENTITY = "benchmark-lab-x/cost-reconciliation/v1"
_RECONCILIATION_SCHEMA = """CREATE TABLE cost_reconciliations (
    operation_id TEXT PRIMARY KEY NOT NULL REFERENCES operations(operation_id),
    proof_json TEXT NOT NULL,
    proof_sha256 TEXT NOT NULL CHECK(length(proof_sha256) = 64)
)"""
_S2_SCHEMA = (
    """CREATE TABLE s2_sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    token_sha256 TEXT UNIQUE NOT NULL CHECK(length(token_sha256) = 64)
)""",
    """CREATE TABLE s2_dossiers (
    dossier_id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES s2_sessions(session_id),
    current_revision INTEGER NOT NULL,
    FOREIGN KEY(dossier_id, current_revision) REFERENCES dossier_revisions(dossier_id, revision)
)""",
    """CREATE TABLE s2_revisions (
    dossier_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('draft', 'clarification', 'preview', 'scope_confirmation', 'suspended')),
    explanation TEXT NOT NULL,
    package_json TEXT,
    package_sha256 TEXT,
    changes_json TEXT NOT NULL,
    checks_json TEXT NOT NULL,
    PRIMARY KEY(dossier_id, revision),
    FOREIGN KEY(dossier_id, revision) REFERENCES dossier_revisions(dossier_id, revision),
    CHECK((package_json IS NULL AND package_sha256 IS NULL) OR
          (package_json IS NOT NULL AND length(package_sha256) = 64))
)""",
    """CREATE TABLE s2_actions (
    dossier_id TEXT NOT NULL REFERENCES s2_dossiers(dossier_id),
    action_id TEXT NOT NULL,
    input_revision INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('create', 'clarify', 'correct')),
    request_json TEXT NOT NULL,
    operation_id TEXT UNIQUE NOT NULL REFERENCES operations(operation_id),
    PRIMARY KEY(dossier_id, action_id),
    FOREIGN KEY(dossier_id, input_revision) REFERENCES dossier_revisions(dossier_id, revision)
)""",
    """CREATE TABLE s2_validations (
    dossier_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    package_sha256 TEXT NOT NULL CHECK(length(package_sha256) = 64),
    session_id TEXT NOT NULL REFERENCES s2_sessions(session_id),
    validated_at TEXT NOT NULL,
    PRIMARY KEY(dossier_id, revision, package_sha256),
    FOREIGN KEY(dossier_id, revision) REFERENCES s2_revisions(dossier_id, revision)
)""",
    """CREATE TABLE s2_control (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    format_identity TEXT NOT NULL CHECK(format_identity = 'benchmark-lab-x/preparation/v1'),
    admission_json TEXT
)""",
)



class SchemaError(ValueError):
    """The existing database is not a supported S1 schema"""


class IntegrityError(ValueError):
    """Stored data, a private path or piece bytes cannot be trusted"""


class ConflictError(ValueError):
    """An existing identity or file would be overwritten"""


class BudgetError(ValueError):
    """The envelope cannot admit another intent with the available evidence"""


_SCHEMA = (
    """CREATE TABLE dossier_revisions (
        dossier_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0),
        payload_json TEXT NOT NULL,
        PRIMARY KEY (dossier_id, revision)
    )""",
    """CREATE TABLE pieces (
        piece_id TEXT PRIMARY KEY NOT NULL,
        dossier_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('candidate', 'judge')),
        media_type TEXT NOT NULL,
        relative_path TEXT UNIQUE NOT NULL,
        sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
        FOREIGN KEY (dossier_id, revision)
            REFERENCES dossier_revisions (dossier_id, revision)
    )""",
)
_PAYLOAD_KEYS = {
    "request", "clarifications", "reformulation", "validated_assumptions",
    "fictional_parameters", "state",
}
_PIECE_COLUMNS = (
    "piece_id", "dossier_id", "revision", "name", "role", "media_type",
    "relative_path", "sha256", "size_bytes",
)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_S1_SCHEMA = (
    """CREATE TABLE budgets (
        budget_id TEXT PRIMARY KEY NOT NULL,
        limit_amount TEXT NOT NULL,
        currency TEXT NOT NULL
    )""",
    """CREATE TABLE operations (
        operation_id TEXT PRIMARY KEY NOT NULL,
        phase TEXT NOT NULL CHECK(phase IN
            ('preparation', 'correction', 'judgment', 'acquisition')),
        dossier_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        authority TEXT NOT NULL,
        engine_version TEXT NOT NULL,
        requested_configuration_json TEXT NOT NULL,
        resources_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN
            ('INTENT_RECORDED', 'EMISSION_POSSIBLE', 'AMBIGUOUS', 'RECEIVED')),
        receipt_json TEXT,
        observed_cost_json TEXT,
        ambiguity_reason TEXT,
        created_at TEXT NOT NULL,
        CHECK((state = 'RECEIVED' AND receipt_json IS NOT NULL
                AND observed_cost_json IS NOT NULL)
            OR (state != 'RECEIVED' AND receipt_json IS NULL
                AND observed_cost_json IS NULL)),
        CHECK(state != 'AMBIGUOUS' OR ambiguity_reason IS NOT NULL),
        FOREIGN KEY (dossier_id, revision)
            REFERENCES dossier_revisions (dossier_id, revision)
    )""",
    """CREATE TABLE reservations (
        operation_id TEXT PRIMARY KEY NOT NULL,
        budget_id TEXT NOT NULL,
        amount TEXT NOT NULL,
        FOREIGN KEY (operation_id) REFERENCES operations (operation_id),
        FOREIGN KEY (budget_id) REFERENCES budgets (budget_id)
    )""",
)
_OPERATION_KEYS = (
    'operation_id', 'phase', 'dossier_id', 'revision', 'authority',
    'engine_version', 'requested_configuration', 'resources',
)
_OPERATION_COLUMNS = (
    'operation_id', 'phase', 'dossier_id', 'revision', 'authority',
    'engine_version', 'requested_configuration_json', 'resources_json',
    'state', 'receipt_json', 'observed_cost_json', 'ambiguity_reason', 'created_at',
)


def _money(value):
    if type(value) is not str or re.fullmatch(
            r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value) is None:
        raise ValueError('money must be decimal text')
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError('invalid decimal amount') from error
    if not amount.is_finite() or amount < 0:
        raise ValueError('money must be finite and nonnegative')
    return amount


def _sum_money(values):
    """Sum exactly, including amounts wider than the ambient Decimal context."""
    values = list(values)
    if not values:
        return Decimal(0)
    lowest = min(value.as_tuple().exponent for value in values)
    highest = max(value.adjusted() for value in values)
    with localcontext() as context:
        context.prec = max(1, highest - lowest + len(str(len(values))) + 2)
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        return sum(values, Decimal(0))


def _strict_json(value):
    try:
        _json_value(value)
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(',', ':'))
        raw.encode('utf-8', errors='strict')
        return raw
    except (TypeError, UnicodeError, RecursionError) as error:
        raise ValueError('strict UTF-8 JSON required') from error


def _fields(value, keys, label):
    if type(value) is not dict or value.keys() != set(keys):
        raise ValueError(f'{label} must contain exactly its S1 fields')


def _resources(value):
    if type(value) is not list:
        raise ValueError('resources must be a list')
    for resource in value:
        _text(resource, 'resource')


def _operation(value):
    _fields(value, _OPERATION_KEYS, 'operation')
    _identity(value['dossier_id'], value['revision'])
    for key in ('operation_id', 'authority', 'engine_version'):
        _text(value[key], key)
    if value['phase'] not in ('preparation', 'correction', 'judgment', 'acquisition'):
        raise ValueError('unsupported operation phase')
    if type(value['requested_configuration']) is not dict or not value['requested_configuration']:
        raise ValueError('requested_configuration must be a nonempty object')
    _resources(value['resources'])
    _strict_json(value)


def _receipt(value, cost):
    _fields(value, ('receipt_id', 'observed_configuration', 'resources_seen', 'result'), 'receipt')
    _text(value['receipt_id'], 'receipt_id')
    if value['observed_configuration'] is not None and type(value['observed_configuration']) is not dict:
        raise ValueError('observed_configuration must be an object or explicit null')
    _resources(value['resources_seen'])
    _fields(cost, ('status', 'amount', 'currency', 'source'), 'cost')
    for key in ('currency', 'source'):
        _text(cost[key], key)
    if cost['status'] == 'KNOWN':
        _money(cost['amount'])
    elif cost['status'] != 'UNKNOWN' or cost['amount'] is not None:
        raise ValueError('unknown cost requires UNKNOWN and null amount')
    return _strict_json(value), _strict_json(cost)


@contextmanager
def _transaction(connection, *, write=False):
    connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield
        connection.execute('COMMIT')
    except BaseException:
        if connection.in_transaction:
            connection.execute('ROLLBACK')
        raise


def _text(value, label):
    if type(value) is not str or not value or "\0" in value:
        raise ValueError(f"{label} must be nonempty text without NUL")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError(f"{label} must be UTF-8 text") from error


def _identity(dossier_id, revision):
    _text(dossier_id, "dossier_id")
    if type(revision) is not int or not 0 < revision <= 2**63 - 1:
        raise ValueError("revision must be a positive SQLite integer")


def _json_value(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for child in value:
            _json_value(child)
        return
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings")
            _json_value(child)
        return
    raise ValueError("only strict JSON values are supported")


def _payload_json(payload):
    if type(payload) is not dict or payload.keys() != _PAYLOAD_KEYS:
        raise ValueError("payload must contain exactly the six S1 fields")
    for key in ("request", "reformulation"):
        if type(payload[key]) is not str:
            raise ValueError(f"{key} must be text")
    for key in ("clarifications", "validated_assumptions"):
        if type(payload[key]) is not list:
            raise ValueError(f"{key} must be a list")
    if type(payload["fictional_parameters"]) is not dict:
        raise ValueError("fictional_parameters must be an object")
    if payload["state"] != "EN_ATTENTE":
        raise ValueError("only EN_ATTENTE is supported")
    try:
        _json_value(payload)
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(",", ":"))
        raw.encode("utf-8", errors="strict")
    except (TypeError, UnicodeError, RecursionError) as error:
        raise ValueError("payload must be strict UTF-8 JSON") from error
    return raw


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _private(info, directory=False):
    expected = 0o700 if directory else 0o600
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (not correct_type or stat.S_IMODE(info.st_mode) != expected
            or info.st_uid != os.getuid() or (not directory and info.st_nlink != 1)):
        raise IntegrityError("private owner, ordinary type and permissions required")


def _root_path(root):
    root = Path(root)
    if not root.is_absolute() or ".." in root.parts:
        raise IntegrityError("an explicit absolute private root is required")
    source = Path(__file__).resolve().parent.parent
    if root.is_relative_to(source) or "releases" in root.parts:
        raise IntegrityError("private storage must be outside sources and releases")
    for path in (*reversed(root.parents), root):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise IntegrityError("root components must be directories without links")
        if (path / ".git").exists() or (path / "benchmark_lab_x/storage.py").exists():
            raise IntegrityError("private storage must be outside product sources")
    return root


def _open_directory(path):
    try:
        fd = os.open(path, _DIRECTORY_FLAGS)
        try:
            _private(os.fstat(fd), directory=True)
        except BaseException:
            os.close(fd)
            raise
        return fd
    except OSError as error:
        raise IntegrityError("private directory missing or unsafe") from error


def _make_directory(path):
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    fd = _open_directory(path)
    os.close(fd)


def _database_files(root):
    for name in ("metadata.sqlite3", "metadata.sqlite3-journal",
                 "metadata.sqlite3-wal", "metadata.sqlite3-shm"):
        try:
            info = (root / name).lstat()
        except FileNotFoundError:
            if name == "metadata.sqlite3":
                raise SchemaError("initialize the database explicitly first")
            continue
        _private(info)
    return (root / "metadata.sqlite3").stat()


def _connect(root, mode):
    before = _database_files(root)
    connection = None
    try:
        connection = sqlite3.connect(
            (root / "metadata.sqlite3").as_uri() + f"?mode={mode}",
            uri=True, isolation_level=None,
        )
        after = _database_files(root)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise IntegrityError("database identity changed during opening")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise SchemaError("SQLite foreign keys unavailable")
        return connection
    except BaseException:
        if connection is not None:
            connection.close()
        raise


@cache
def _expected_schema(objects):
    return tuple(sorted((kind, name, table, ' '.join(sql.split()) if sql else None)
                        for kind, name, table, sql in objects))


@lru_cache(maxsize=1)
def _observed_schema(objects):
    # Only the latest complete schema value is retained; SQLite is still read each time
    return tuple(sorted((kind, name, table, ' '.join(sql.split()) if sql else None)
                        for kind, name, table, sql in objects))


def _check_schema(connection, allow_empty=False, *, check_data=True):
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY name"
        ).fetchall()
        if version == 0 and not rows and allow_empty:
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise SchemaError("S1 requires the standard DELETE journal")
            return False
        if version != SCHEMA_VERSION:
            raise SchemaError("unsupported storage schema version")
        reconciliation = [row for row in rows if row[2] == 'cost_reconciliations']
        if reconciliation:
            objects = [('table', 'cost_reconciliations', 'cost_reconciliations', _RECONCILIATION_SCHEMA),
                       ('index', 'sqlite_autoindex_cost_reconciliations_1', 'cost_reconciliations', None)]
            normalize = lambda values: sorted((a, b, c, ' '.join(d.split()) if d else None) for a, b, c, d in values)
            if normalize(reconciliation) != normalize(objects):
                raise SchemaError('unsupported cost reconciliation structure')
            rows = [row for row in rows if row[2] != 'cost_reconciliations']
        # Compare all schema objects, including constraints and automatic indexes
        expected = [
            ("table", "dossier_revisions", "dossier_revisions", _SCHEMA[0]),
            ("table", "pieces", "pieces", _SCHEMA[1]),
            ("index", "sqlite_autoindex_dossier_revisions_1", "dossier_revisions", None),
            ("index", "sqlite_autoindex_pieces_1", "pieces", None),
            ("index", "sqlite_autoindex_pieces_2", "pieces", None),
        ]
        extended = expected + [
            ("table", name, name, statement)
            for name, statement in zip(('budgets', 'operations', 'reservations'), _S1_SCHEMA)
        ] + [
            ("index", f"sqlite_autoindex_{name}_1", name, None)
            for name in ('budgets', 'operations', 'reservations')
        ]
        s2 = extended + [("table", name, name, statement)
                         for name, statement in zip(
                             ('s2_sessions', 's2_dossiers', 's2_revisions', 's2_actions',
                              's2_validations', 's2_control'), _S2_SCHEMA)]
        s2 += [("index", f"sqlite_autoindex_{name}_{number}", name, None)
               for name, count in (('s2_sessions', 2), ('s2_dossiers', 1),
                                   ('s2_revisions', 1), ('s2_actions', 2),
                                   ('s2_validations', 1))
               for number in range(1, count + 1)]
        s3 = s4 = s5 = None
        if any(name == 's3_control' for _, name, _, _ in rows):
            from .qualification import schema_objects
            s3 = s2 + schema_objects()
        if s3 is not None and any(name == 's4_control' for _, name, _, _ in rows):
            from .campaigns import schema_objects
            s4 = s3 + schema_objects()
        if s4 is not None and any(name == 's5_control' for _, name, _, _ in rows):
            from .evaluation import schema_objects
            s5 = s4 + schema_objects()
        actual = _observed_schema(tuple(rows))
        layout = ('canary' if actual == _expected_schema(tuple(expected)) else
                  's1' if actual == _expected_schema(tuple(extended)) else
                  's2' if actual == _expected_schema(tuple(s2)) else
                  's3' if s3 is not None and actual == _expected_schema(tuple(s3)) else
                  's4' if s4 is not None and actual == _expected_schema(tuple(s4)) else
                  's5' if s5 is not None and actual == _expected_schema(tuple(s5)) else None)
        if layout in ('s2', 's3', 's4', 's5') and connection.execute(
                'SELECT singleton, format_identity FROM s2_control').fetchall() != [(1, PREPARATION_IDENTITY)]:
            raise SchemaError('unsupported preparation identity')
        if layout in ('s3', 's4', 's5'):
            from .qualification import FORMAT_IDENTITY
            if connection.execute('SELECT * FROM s3_control').fetchall() != [(1, FORMAT_IDENTITY)]:
                raise SchemaError('unsupported qualification identity')
        if layout in ('s4', 's5'):
            from .campaigns import FORMAT_IDENTITY
            if connection.execute('SELECT * FROM s4_control').fetchall() != [(1, FORMAT_IDENTITY)]:
                raise SchemaError('unsupported campaigns identity')
        if layout == 's5':
            from .evaluation import FORMAT_IDENTITY
            if connection.execute('SELECT * FROM s5_control').fetchall() != [(1, FORMAT_IDENTITY)]:
                raise SchemaError('unsupported evaluations identity')
        if layout is None or (reconciliation and layout == 'canary'):
            raise SchemaError("unsupported storage schema structure")
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise SchemaError("S1 requires the standard DELETE journal")
        if check_data:
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise SchemaError("damaged SQLite database")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise IntegrityError("broken dossier/piece reference")
        return layout
    except sqlite3.DatabaseError as error:
        raise SchemaError("unreadable storage schema") from error


def initialize(root: Path) -> None:
    """Create schema 1 explicitly, without migrating or replacing existing data"""
    root = _root_path(root)
    _make_directory(root)
    root_fd = _open_directory(root)
    try:
        database = root / "metadata.sqlite3"
        if os.path.lexists(database):
            connection = _connect(root, "ro")
            try:
                ready = _check_schema(connection, allow_empty=True)
            finally:
                connection.close()
            if ready:
                pieces_fd = _open_directory(root / "pieces")
                os.close(pieces_fd)
                return
        else:
            fd = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.fsync(fd)
            finally:
                os.close(fd)
        _make_directory(root / "pieces")
        connection = _connect(root, "rw")
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            if not _check_schema(connection, allow_empty=True):
                for statement in _SCHEMA + _S1_SCHEMA:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        os.fsync(root_fd)
        parent_fd = os.open(root.parent, _DIRECTORY_FLAGS)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        os.close(root_fd)


def initialize_preparation(root: Path) -> None:
    """Extend only a fresh integrated S1 database, never migrate business data."""
    store = Store(root)
    try:
        connection = store._connection_checked()
        with _transaction(connection, write=True):
            layout = _check_schema(connection)
            if layout in ('s2', 's3', 's4', 's5'):
                return
            if layout != 's1' or os.listdir(store._pieces_fd) or any(connection.execute(
                    'SELECT 1 FROM ' + table + ' LIMIT 1').fetchone()
                    for table in ('dossier_revisions', 'pieces', 'budgets', 'operations', 'reservations')):
                raise SchemaError('preparation requires an empty integrated S1 database')
            for statement in _S2_SCHEMA:
                connection.execute(statement)
            connection.execute('INSERT INTO s2_control VALUES (1, ?, NULL)', (PREPARATION_IDENTITY,))
            _check_schema(connection)
    finally:
        store.close()


class Store:
    """Open only compatible existing storage; callers must close it explicitly"""

    def __init__(self, root: Path):
        self._connection = None
        self._verified_read_changes = None
        self._verified_operations = None
        self._root_fd = self._pieces_fd = None
        self._root = _root_path(root)
        try:
            self._root_fd = _open_directory(self._root)
            check = _connect(self._root, "ro")
            try:
                _check_schema(check)
            finally:
                check.close()
            self._pieces_fd = _open_directory(self._root / "pieces")
            self._connection = _connect(self._root, "rw")
            _check_schema(self._connection)
            self._connection.execute("PRAGMA synchronous=FULL")
            self._database_identity = _database_files(self._root)
        except BaseException:
            self.close()
            raise

    def _connection_checked(self):
        if self._connection is None:
            raise ValueError("store is closed")
        _root_path(self._root)
        for path, fd in ((self._root, self._root_fd),
                         (self._root / "pieces", self._pieces_fd)):
            try:
                current = path.lstat()
            except OSError as error:
                raise IntegrityError("private directory is missing") from error
            _private(current, directory=True)
            opened = os.fstat(fd)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise IntegrityError("private directory identity changed")
        current = _database_files(self._root)
        if ((current.st_dev, current.st_ino)
                != (self._database_identity.st_dev, self._database_identity.st_ino)):
            raise IntegrityError("database identity changed")
        # A full verification already checked this unchanged SQLite read snapshot
        # Paths, schema identities and piece bytes remain checked on every read
        check_data = (self._verified_read_changes is None
                      or not self._connection.in_transaction
                      or self._connection.total_changes != self._verified_read_changes)
        _check_schema(self._connection, check_data=check_data)
        return self._connection

    def _s1_connection(self):
        connection = self._connection_checked()
        if not connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='operations'"
        ).fetchone():
            raise SchemaError('operation APIs require the extended S1 schema')
        return connection

    def create_budget(self, budget_id: str, limit: str, currency: str) -> None:
        _text(budget_id, 'budget_id')
        _text(currency, 'currency')
        _money(limit)
        connection = self._s1_connection()
        try:
            connection.execute('INSERT INTO budgets VALUES (?, ?, ?)', (budget_id, limit, currency))
        except sqlite3.IntegrityError as error:
            raise ConflictError('budget identity already exists') from error

    def _operations(self, connection, *, operation_ids=None):
        if operation_ids is not None:
            operation_ids = frozenset(operation_ids)
        snapshot = (connection is self._connection and connection.in_transaction
                    and self._verified_read_changes is not None
                    and connection.total_changes == self._verified_read_changes)
        if snapshot and self._verified_operations is not None:
            return deepcopy([record for record in self._verified_operations
                             if operation_ids is None or record['operation_id'] in operation_ids])
        rows = connection.execute(
            'SELECT ' + ', '.join('o.' + column for column in _OPERATION_COLUMNS)
            + ', r.budget_id, r.amount, b.currency FROM operations o '
            'LEFT JOIN reservations r ON r.operation_id=o.operation_id '
            'LEFT JOIN budgets b ON b.budget_id=r.budget_id ORDER BY o.operation_id'
        ).fetchall()
        records = []
        for row in rows:
            record = dict(zip(_OPERATION_COLUMNS + ('budget_id', 'reserved_amount', 'currency'), row))
            try:
                for key in ('requested_configuration', 'resources', 'receipt', 'observed_cost'):
                    raw = record.pop(key + '_json')
                    if raw is not None and type(raw) is not str:
                        raise ValueError('stored JSON must be text')
                    record[key] = None if raw is None else json.loads(raw, object_pairs_hook=_unique_object)
                _operation({key: record[key] for key in _OPERATION_KEYS})
                _text(record['budget_id'], 'budget_id')
                _money(record['reserved_amount'])
                _text(record['created_at'], 'created_at')
                currency = record.pop('currency')
                _text(currency, 'currency')
                state = record['state']
                reason = record['ambiguity_reason']
                if reason is not None:
                    _text(reason, 'ambiguity_reason')
                if state == 'AMBIGUOUS' and reason is None:
                    raise ValueError('missing ambiguity reason')
                if state in ('INTENT_RECORDED', 'EMISSION_POSSIBLE') and reason is not None:
                    raise ValueError('unexpected ambiguity reason')
                if state == 'RECEIVED':
                    _receipt(record['receipt'], record['observed_cost'])
                    if record['observed_cost']['currency'] != currency:
                        raise ValueError('stored cost currency differs from budget')
                elif (state not in ('INTENT_RECORDED', 'EMISSION_POSSIBLE', 'AMBIGUOUS')
                      or record['receipt'] is not None or record['observed_cost'] is not None):
                    raise ValueError('invalid operation state or receipt')
            except (ValueError, TypeError, RecursionError) as error:
                raise IntegrityError('invalid stored operation or reservation') from error
            records.append(record)
        if snapshot:
            self._verified_operations = deepcopy(records)
        return [record for record in records
                if operation_ids is None or record['operation_id'] in operation_ids]

    def inspect_operations(self) -> list[dict]:
        return self._operations(self._s1_connection())

    def _reconciliation(self, connection, operation):
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='cost_reconciliations'").fetchone():
            return None
        row = connection.execute('SELECT proof_json,proof_sha256 FROM cost_reconciliations WHERE operation_id=?',
                                 (operation['operation_id'],)).fetchone()
        if row is None:
            return None
        try:
            proof = json.loads(row[0], object_pairs_hook=_unique_object)
            if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
                raise ValueError('reconciliation fingerprint differs')
            self._validate_reconciliation(connection, operation, proof)
        except (ValueError, KeyError, TypeError) as error:
            raise IntegrityError('invalid cost reconciliation evidence') from error
        return {'proof': proof, 'sha256': row[1]}

    def _validate_reconciliation(self, connection, operation, proof):
        _fields(proof, ('format_identity', 'operation_id', 'budget_id', 'receipt_sha256', 'actor',
                        'authority_id', 'account_reference', 'generation_id', 'model', 'cost', 'source',
                        'correlation'), 'cost reconciliation')
        for key in ('actor', 'authority_id', 'account_reference', 'generation_id', 'model', 'correlation'):
            _text(proof[key], key)
        if (proof['format_identity'] != RECONCILIATION_IDENTITY
                or operation['state'] != 'RECEIVED' or operation['observed_cost']['status'] != 'UNKNOWN'
                or operation['phase'] not in ('preparation', 'correction')
                or operation['requested_configuration'].get('provider') != 'OpenRouter'
                or proof['model'] != operation['requested_configuration'].get('model')
                or proof['operation_id'] != operation['operation_id'] or proof['budget_id'] != operation['budget_id']
                or proof['receipt_sha256'] != hashlib.sha256(_strict_json(operation['receipt']).encode()).hexdigest()
                or re.fullmatch(r'gen-[A-Za-z0-9_-]{1,200}', proof['generation_id']) is None):
            raise ValueError('reconciliation attribution differs')
        observed = operation['receipt']['observed_configuration'] or {}
        generation = observed.get('generation_id')
        if generation is not None and generation != proof['generation_id']:
            raise ValueError('generation identity differs from receipt')
        http = observed.get('http')
        headers = http.get('response_headers') if type(http) is dict else None
        header = headers.get('X-Generation-Id') if type(headers) is dict else None
        if header is not None and header != proof['generation_id']:
            raise ValueError('generation identity differs from header')
        _receipt(operation['receipt'], proof['cost'])
        currency = connection.execute('SELECT currency FROM budgets WHERE budget_id=?', (proof['budget_id'],)).fetchone()[0]
        if proof['cost']['status'] != 'KNOWN' or proof['cost']['currency'] != currency or currency != 'USD':
            raise ValueError('explicit USD cost required')
        source = proof['source']
        _fields(source, ('kind', 'http_status', 'name', 'observed_at', 'document', 'sha256', 'excerpt'), 'external source')
        for key in ('name', 'document', 'excerpt'):
            _text(source[key], key)
        _text(source['observed_at'], 'observed_at')
        date = datetime.fromisoformat(source['observed_at'])
        if date.tzinfo is None or date < datetime.fromisoformat(operation['created_at']):
            raise ValueError('dated evidence after the operation required')
        if (source['sha256'] != hashlib.sha256(source['document'].encode()).hexdigest()
                or source['excerpt'] not in source['document']):
            raise ValueError('source document and exact excerpt required')
        if source['kind'] == 'openrouter_generation':
            if type(source['http_status']) is not int or source['http_status'] != 200:
                raise ValueError('successful generation evidence required; an error proves no amount')
            document = json.loads(source['document'], object_pairs_hook=_unique_object, parse_float=str)
            data = document.get('data') if type(document) is dict else None
            requested = operation['requested_configuration']
            canonical = (requested.get('reservation_estimate') or {}).get('canonical_slug')
            if (type(data) is not dict or data.get('id') != proof['generation_id']
                    or data.get('model') not in [model for model in (proof['model'], canonical) if type(model) is str]
                    or type(data.get('total_cost')) not in (str, int)
                    or _money(str(data['total_cost'])) != _money(proof['cost']['amount'])):
                raise ValueError('generation identity or charged amount differs')
        elif source['kind'] != 'operator_attested_openrouter_record' or source['http_status'] is not None:
            raise ValueError('attributed operator record or successful generation evidence required')
        if len(_strict_json(proof).encode()) > 2 * 1024 * 1024:
            raise ValueError('cost evidence exceeds the private response size limit')

    def _effective_cost(self, connection, operation):
        reconciliation = self._reconciliation(connection, operation)
        return operation['observed_cost'] if reconciliation is None else reconciliation['proof']['cost']

    def inspect_cost(self, operation_id):
        connection = self._s1_connection()
        with _transaction(connection):
            operation = self._operation_for_update(connection, operation_id, ('RECEIVED',))
            reconciliation = self._reconciliation(connection, operation)
            return {'operation_id': operation_id, 'observed_cost': operation['observed_cost'],
                    'effective_cost': self._effective_cost(connection, operation), 'reconciliation': reconciliation,
                    'budget': self._budget(connection, operation['budget_id'], self._operations(connection))}

    def _reconciliation_maintenance(self, connection):
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if (connection.execute("SELECT 1 FROM operations WHERE state='EMISSION_POSSIBLE' LIMIT 1").fetchone()
                or ('s2_control' in tables and connection.execute('SELECT 1 FROM s2_control WHERE admission_json IS NOT NULL').fetchone())
                or ('s4_status' in tables and connection.execute('SELECT 1 FROM s4_status WHERE admission_id IS NOT NULL LIMIT 1').fetchone())):
            raise ConflictError('maintenance and quiescence required for reconciliation')

    def initialize_reconciliation(self):
        connection = self._s1_connection()
        with _transaction(connection, write=True):
            self._reconciliation_maintenance(connection)
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='cost_reconciliations'").fetchone():
                connection.execute(_RECONCILIATION_SCHEMA)
            _check_schema(connection)
        return {'format_identity': RECONCILIATION_IDENTITY}

    def reconcile_cost(self, proof):
        if type(proof) is not dict or type(proof.get('operation_id')) is not str:
            raise ValueError('cost reconciliation operation identity required')
        connection = self._s1_connection()
        with _transaction(connection, write=True):
            self._reconciliation_maintenance(connection)
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='cost_reconciliations'").fetchone():
                raise SchemaError('explicit reconciliation initialization required')
            operation = self._operation_for_update(connection, proof['operation_id'], ('RECEIVED',))
            self._validate_reconciliation(connection, operation, proof)
            previous = self._reconciliation(connection, operation)
            if previous is not None:
                if previous['proof'] != proof:
                    raise ConflictError('cost reconciliation is immutable')
                return previous
            raw = _strict_json(proof)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            connection.execute('INSERT INTO cost_reconciliations VALUES (?,?,?)',
                               (operation['operation_id'], raw, digest))
            return {'proof': proof, 'sha256': digest}

    def _budget(self, connection, budget_id, operations=None):
        if operations is None:
            snapshot = (connection is self._connection and connection.in_transaction
                        and self._verified_read_changes is not None
                        and connection.total_changes == self._verified_read_changes)
            # This aggregation only reads the private cache and never returns its records
            operations = (self._verified_operations if snapshot and self._verified_operations is not None
                          else self._operations(connection))
        row = connection.execute(
            'SELECT limit_amount, currency FROM budgets WHERE budget_id=?', (budget_id,)
        ).fetchone()
        if row is None:
            raise KeyError(budget_id)
        limit, currency = row
        try:
            ceiling = _money(limit)
            _text(currency, 'currency')
        except ValueError as error:
            raise IntegrityError('invalid stored budget') from error
        reserves, costs, unknown = [], [], []
        for operation in operations:
            if operation['budget_id'] != budget_id:
                continue
            cost = self._effective_cost(connection, operation)
            if cost is not None and cost['status'] == 'KNOWN':
                costs.append(_money(cost['amount']))
            else:
                reserves.append(_money(operation['reserved_amount']))
                if cost is not None:
                    unknown.append(operation['operation_id'])
        reserved, spent = _sum_money(reserves), _sum_money(costs)
        available = _sum_money((ceiling, reserved.copy_negate(), spent.copy_negate()))
        return {'budget_id': budget_id, 'limit': limit, 'currency': currency,
                'reserved': str(reserved), 'spent': str(spent), 'available': str(available),
                'unknown_cost_operations': unknown}

    def _blocking_costs(self, operations, budget, phase):
        # Received preparation costs remain unknown and reserved without blocking the next exchange
        return [row['operation_id'] for row in operations
                if row['operation_id'] in budget['unknown_cost_operations']
                and not (phase in ('preparation', 'correction')
                         and row['phase'] in ('preparation', 'correction') and row['state'] == 'RECEIVED')]

    def inspect_budget(self, budget_id: str) -> dict:
        _text(budget_id, 'budget_id')
        connection = self._s1_connection()
        with _transaction(connection):
            return self._budget(connection, budget_id, self._operations(connection))

    def reserve_intent(self, operation: dict, budget_id: str, amount: str) -> None:
        connection = self._s1_connection()
        with _transaction(connection, write=True):
            self._reserve_intent(connection, operation, budget_id, amount)

    def _reserve_intent(self, connection, operation, budget_id, amount, *, retained_cost_ids=()):
        """Shared reservation body; caller owns the enclosing transaction."""
        _operation(operation)
        require_current(operation['requested_configuration'])
        _text(budget_id, 'budget_id')
        requested = _money(amount)
        values = [operation[key] for key in _OPERATION_KEYS]
        values[6] = _strict_json(operation['requested_configuration'])
        values[7] = _strict_json(operation['resources'])
        operations = self._operations(connection)
        if any(row['operation_id'] == operation['operation_id'] for row in operations):
            raise ConflictError('operation identity already exists')
        budget = self._budget(connection, budget_id, operations)
        if not connection.execute(
            'SELECT 1 FROM dossier_revisions WHERE dossier_id=? AND revision=?',
            (operation['dossier_id'], operation['revision']),
        ).fetchone():
            raise KeyError((operation['dossier_id'], operation['revision']))
        if (set(self._blocking_costs(operations, budget, operation['phase'])) - set(retained_cost_ids) or any(
                row['budget_id'] == budget_id and row['state'] in ('EMISSION_POSSIBLE', 'AMBIGUOUS')
                for row in operations)):
            raise BudgetError('unresolved effects or costs block this envelope')
        if requested > Decimal(budget['available']):
            raise BudgetError('insufficient available budget')
        connection.execute(
            'INSERT INTO operations (' + ', '.join(_OPERATION_COLUMNS) + ') '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (*values, 'INTENT_RECORDED', None, None, None,
             datetime.now(timezone.utc).isoformat()),
        )
        # Keep the original reserve as evidence even after a known settlement
        connection.execute('INSERT INTO reservations VALUES (?, ?, ?)',
                           (operation['operation_id'], budget_id, amount))

    def _operation_for_update(self, connection, operation_id, allowed):
        for record in self._operations(connection, operation_ids={operation_id}):
            if record['operation_id'] == operation_id:
                if record['state'] not in allowed:
                    raise ConflictError('operation transition is not permitted')
                return record
        raise KeyError(operation_id)

    def mark_emission_possible(self, operation_id: str) -> None:
        """Commit once before the caller can enter its transport boundary."""
        _text(operation_id, 'operation_id')
        connection = self._s1_connection()
        with _transaction(connection, write=True):
            operation = self._operation_for_update(connection, operation_id, ('INTENT_RECORDED',))
            require_current(operation['requested_configuration'])
            connection.execute("UPDATE operations SET state='EMISSION_POSSIBLE' WHERE operation_id=?",
                               (operation_id,))

    def mark_ambiguous(self, operation_id: str, reason: str) -> None:
        _text(operation_id, 'operation_id')
        _text(reason, 'ambiguity_reason')
        connection = self._s1_connection()
        with _transaction(connection, write=True):
            self._operation_for_update(connection, operation_id, ('EMISSION_POSSIBLE',))
            connection.execute(
                "UPDATE operations SET state='AMBIGUOUS', ambiguity_reason=? WHERE operation_id=?",
                (reason, operation_id),
            )

    def record_receipt(self, operation_id: str, receipt: dict, observed_cost: dict) -> None:
        connection = self._s1_connection()
        with _transaction(connection, write=True):
            self._record_receipt(connection, operation_id, receipt, observed_cost)

    def _record_receipt(self, connection, operation_id, receipt, observed_cost):
        _text(operation_id, 'operation_id')
        receipt_json, cost_json = _receipt(receipt, observed_cost)
        operation = self._operation_for_update(
            connection, operation_id, ('EMISSION_POSSIBLE', 'AMBIGUOUS'))
        currency = connection.execute('SELECT currency FROM budgets WHERE budget_id=?',
                                      (operation['budget_id'],)).fetchone()[0]
        if observed_cost['currency'] != currency:
            raise ValueError('observed cost currency must match its envelope')
        # A sourced fact is retained even when it exceeds the reserve or limit
        # Receipt identities are scoped to their operation, never overwritten
        connection.execute(
            "UPDATE operations SET state='RECEIVED', receipt_json=?, observed_cost_json=? "
            'WHERE operation_id=?', (receipt_json, cost_json, operation_id),
        )

    def verify_storage(self) -> dict:
        """Inspect a coherent metadata snapshot and private files without repair."""
        connection = self._connection_checked()
        with _transaction(connection):
            layout = _check_schema(connection)
            previous = self._verified_read_changes
            previous_operations = self._verified_operations
            self._verified_read_changes = connection.total_changes
            self._verified_operations = None
            try:
                intact = connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
                for dossier_id, revision in connection.execute(
                        'SELECT dossier_id, revision FROM dossier_revisions').fetchall():
                    try:
                        self.get_dossier(dossier_id, revision)
                    except IntegrityError:
                        intact = False
                broken, references = [], set()
                for piece_id, relative_path in connection.execute(
                        'SELECT piece_id, relative_path FROM pieces ORDER BY piece_id').fetchall():
                    references.add(relative_path)
                    try:
                        self.read_piece(piece_id)
                    except IntegrityError:
                        broken.append(piece_id)
                # Inventory names only: do not follow links or remove partial/orphan bytes
                orphans = sorted('pieces/' + name for name in os.listdir(self._pieces_fd)
                                 if 'pieces/' + name not in references)
                operations = self._operations(connection) if layout in ('s1', 's2', 's3', 's4', 's5') else []
                if layout in ('s1', 's2', 's3', 's4', 's5'):
                    for (budget_id,) in connection.execute('SELECT budget_id FROM budgets').fetchall():
                        self._budget(connection, budget_id, operations)
                if layout in ('s2', 's3', 's4', 's5'):
                    from .preparation import verify_preparation
                    verify_preparation(self, connection)
                if layout in ('s3', 's4', 's5'):
                    from .qualification import verify_qualification
                    verify_qualification(self, connection)
                if layout in ('s4', 's5'):
                    from .campaigns import verify_campaigns
                    verify_campaigns(self, connection)
                if layout == 's5':
                    from .evaluation import verify_evaluations
                    verify_evaluations(self, connection)
                    from .judgment import verify_judgments
                    verify_judgments(self, connection)
                return {
                    'schema_version': SCHEMA_VERSION, 'integrity_ok': intact and not broken,
                    'cost_reconciliation_format': RECONCILIATION_IDENTITY if connection.execute(
                        "SELECT 1 FROM sqlite_schema WHERE name='cost_reconciliations'").fetchone() else None,
                    'broken_pieces': broken, 'orphan_files': orphans,
                    'active_operations': [row['operation_id'] for row in operations
                                          if row['state'] != 'RECEIVED'],
                    'ambiguous_operations': [row['operation_id'] for row in operations
                                             if row['state'] in ('EMISSION_POSSIBLE', 'AMBIGUOUS')],
                    'unknown_cost_operations': [row['operation_id'] for row in operations
                        if self._effective_cost(connection, row) is not None and self._effective_cost(connection, row)['status'] == 'UNKNOWN'],
                }
            finally:
                self._verified_read_changes = previous
                self._verified_operations = previous_operations


    def save_dossier(self, dossier_id: str, revision: int, payload: dict) -> None:
        _identity(dossier_id, revision)
        raw = _payload_json(payload)
        connection = self._connection_checked()
        try:
            connection.execute(
                "INSERT INTO dossier_revisions (dossier_id, revision, payload_json) VALUES (?, ?, ?)",
                (dossier_id, revision, raw),
            )
        except sqlite3.IntegrityError as error:
            raise ConflictError("dossier revision already exists") from error

    def get_dossier(self, dossier_id: str, revision: int) -> dict:
        _identity(dossier_id, revision)
        row = self._connection_checked().execute(
            "SELECT payload_json FROM dossier_revisions WHERE dossier_id=? AND revision=?",
            (dossier_id, revision),
        ).fetchone()
        if row is None:
            raise KeyError((dossier_id, revision))
        try:
            if type(row[0]) is not str:
                raise ValueError("stored dossier must be UTF-8 JSON text")
            payload = json.loads(row[0], object_pairs_hook=_unique_object)
            _payload_json(payload)
            return payload
        except (ValueError, TypeError, RecursionError) as error:
            raise IntegrityError("invalid stored dossier JSON") from error

    def put_piece(self, dossier_id: str, revision: int, piece_id: str, *,
                  name: str, role: str, media_type: str, content: bytes) -> dict:
        connection = self._connection_checked()
        with _transaction(connection, write=True):
            return self._put_piece(connection, dossier_id, revision, piece_id,
                                   name=name, role=role, media_type=media_type, content=content)

    def _put_piece(self, connection, dossier_id, revision, piece_id, *,
                   name, role, media_type, content):
        # Failed commits retain orphan bytes for S1 integrity inspection
        _identity(dossier_id, revision)
        for label, value in (("piece_id", piece_id), ("name", name), ("media_type", media_type)):
            _text(value, label)
        if role not in ("candidate", "judge") or type(role) is not str:
            raise ValueError("role must be candidate or judge")
        if type(content) is not bytes:
            raise ValueError("content must be bytes")
        if connection.execute("SELECT 1 FROM pieces WHERE piece_id=?", (piece_id,)).fetchone():
            raise ConflictError("piece identity already exists")
        if not connection.execute(
            "SELECT 1 FROM dossier_revisions WHERE dossier_id=? AND revision=?",
            (dossier_id, revision),
        ).fetchone():
            raise KeyError((dossier_id, revision))
        filename = secrets.token_hex(16) + ".bin"
        temporary = "." + filename + ".tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self._pieces_fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, filename, src_dir_fd=self._pieces_fd,
                        dst_dir_fd=self._pieces_fd, follow_symlinks=False)
            except FileExistsError as error:
                raise ConflictError("piece file already exists") from error
        finally:
            os.unlink(temporary, dir_fd=self._pieces_fd)
        os.fsync(self._pieces_fd)
        meta = dict(zip(_PIECE_COLUMNS, (
            piece_id, dossier_id, revision, name, role, media_type,
            "pieces/" + filename, hashlib.sha256(content).hexdigest(), len(content),
        )))
        connection.execute(
            "INSERT INTO pieces (" + ", ".join(_PIECE_COLUMNS) + ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(meta.values()),
        )
        return meta

    def get_piece(self, piece_id: str) -> dict:
        _text(piece_id, "piece_id")
        row = self._connection_checked().execute(
            "SELECT " + ", ".join(_PIECE_COLUMNS) + " FROM pieces WHERE piece_id=?", (piece_id,),
        ).fetchone()
        if row is None:
            raise KeyError(piece_id)
        meta = dict(zip(_PIECE_COLUMNS, row))
        try:
            _identity(meta["dossier_id"], meta["revision"])
            for field in ("piece_id", "name", "media_type", "relative_path"):
                _text(meta[field], field)
            if meta["role"] not in ("candidate", "judge"):
                raise ValueError("invalid stored role")
            ref = meta["relative_path"]
            parts = ref.split("/")
            if (len(parts) != 2 or parts[0] != "pieces" or parts[1] in ("", ".", "..")
                    or "\\" in ref):
                raise ValueError("piece reference outside pieces")
            digest = meta["sha256"]
            if (type(digest) is not str or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)):
                raise ValueError("invalid SHA-256")
            if type(meta["size_bytes"]) is not int or meta["size_bytes"] < 0:
                raise ValueError("invalid piece size")
        except ValueError as error:
            raise IntegrityError("invalid stored piece metadata") from error
        return meta

    def read_piece(self, piece_id: str) -> bytes:
        meta = self.get_piece(piece_id)
        try:
            fd = os.open(meta["relative_path"].split("/")[1],
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._pieces_fd)
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                _private(before)
                if before.st_size != meta["size_bytes"]:
                    raise IntegrityError("piece size mismatch")
                raw = stream.read()
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise IntegrityError("piece changed during reading")
        except OSError as error:
            raise IntegrityError("piece file missing or unsafe") from error
        if len(raw) != meta["size_bytes"] or hashlib.sha256(raw).hexdigest() != meta["sha256"]:
            raise IntegrityError("piece size or SHA-256 mismatch")
        return raw

    def close(self) -> None:
        try:
            if self._connection is not None:
                self._connection.close()
        finally:
            self._connection = None
            for attribute in ("_pieces_fd", "_root_fd"):
                fd = getattr(self, attribute)
                if fd is not None:
                    os.close(fd)
                    setattr(self, attribute, None)
