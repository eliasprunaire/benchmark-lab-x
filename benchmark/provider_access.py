"""Accès OpenRouter délégué, chiffré et lié à une session S2"""
from base64 import urlsafe_b64decode, urlsafe_b64encode
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from http.client import HTTPSConnection, IncompleteRead
import hmac
import json
import re
import secrets
from urllib.parse import urlencode, urlsplit

from . import storage
from .storage import IntegrityError, SchemaError, _strict_json as encode, _transaction


FORMAT_IDENTITY = 'benchmark-lab-x/provider-access/v1'
AUTHORIZE_URL = 'https://openrouter.ai/auth'
HOST = 'openrouter.ai'
EXCHANGE_PATH = '/api/v1/auth/keys'
VERIFY_PATH = '/api/v1/key'
REFRESH_INTERVAL = timedelta(minutes=10)
EXPIRATION = timedelta(days=30)
MAX_RESPONSE_BYTES = 1024 * 1024

_TABLES = {
    's2_provider_access': """CREATE TABLE s2_provider_access (
        session_id TEXT PRIMARY KEY NOT NULL REFERENCES s2_sessions(session_id),
        verifier_cipher TEXT,
        key_cipher TEXT,
        created_at TEXT NOT NULL,
        verified_at TEXT,
        checked_at TEXT,
        limit_usd TEXT,
        limit_remaining_usd TEXT,
        is_free_tier INTEGER NOT NULL DEFAULT 0 CHECK(is_free_tier IN (0, 1)),
        status TEXT NOT NULL CHECK(status IN ('pending', 'connected', 'invalid')),
        status_reason TEXT,
        CHECK((status = 'pending') = (verifier_cipher IS NOT NULL)),
        CHECK((status != 'pending') = (key_cipher IS NOT NULL)),
        CHECK((status = 'invalid') = (status_reason IS NOT NULL))
    )""",
    's6_access_events': """CREATE TABLE s6_access_events (
        event_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES s2_sessions(session_id),
        kind TEXT NOT NULL CHECK(kind IN ('exchange', 'verify')),
        state TEXT NOT NULL CHECK(state IN ('INTENT_RECORDED', 'RECEIVED', 'FAILED')),
        created_at TEXT NOT NULL,
        observed_at TEXT,
        http_status INTEGER,
        document_sha256 TEXT,
        excerpt TEXT,
        CHECK((state = 'INTENT_RECORDED') = (observed_at IS NULL))
    )""",
    's6_control': """CREATE TABLE s6_control (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        format_identity TEXT NOT NULL CHECK(format_identity = 'benchmark-lab-x/provider-access/v1')
    )""",
}


def schema_objects():
    return ([('table', name, name, sql) for name, sql in _TABLES.items()]
            + [('index', f'sqlite_autoindex_{name}_1', name, None)
               for name in ('s2_provider_access', 's6_access_events')])


def initialize(data):
    """Étend explicitement une base S5 intacte"""
    from .runtime import worker_lock
    with closing(storage.Store(data)) as store, worker_lock(store):
        proof = store.verify_storage()
        if not proof['integrity_ok'] or proof['orphan_files']:
            raise IntegrityError('Stockage incomplet ou altéré')
        connection = store._connection_checked()
        with _transaction(connection, write=True):
            layout = storage._check_schema(connection)
            if layout == 's6':
                return
            if layout != 's5':
                raise SchemaError('Extension explicite sur une base S5 requise')
            for sql in _TABLES.values():
                connection.execute(sql)
            connection.execute('INSERT INTO s6_control VALUES (1, ?)', (FORMAT_IDENTITY,))
            storage._check_schema(connection)


def available(store):
    connection = store._connection_checked()
    return connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s6_control'").fetchone() is not None


def parse_secret(value):
    if value in (None, ''):
        return None
    if type(value) is not str or re.fullmatch(r'[0-9a-fA-F]{64}', value) is None:
        raise ValueError('BENCHMARK_ACCESS_SECRET doit contenir 32 octets hexadécimaux')
    return bytes.fromhex(value)


def _stream(secret, nonce, size):
    output = bytearray()
    counter = 0
    while len(output) < size:
        output.extend(hmac.digest(secret, nonce + counter.to_bytes(8, 'big'), 'sha256'))
        counter += 1
    return output[:size]


def encrypt(secret, value):
    if type(secret) is not bytes or len(secret) != 32 or type(value) is not str:
        raise ValueError('Secret et texte valides requis')
    raw = value.encode('utf-8')
    nonce = secrets.token_bytes(16)
    cipher = bytes(a ^ b for a, b in zip(raw, _stream(secret, nonce, len(raw))))
    tag = hmac.digest(secret, b'access-v1' + nonce + cipher, 'sha256')
    return urlsafe_b64encode(nonce + cipher + tag).decode('ascii')


def decrypt(secret, value):
    if type(secret) is not bytes or len(secret) != 32 or type(value) is not str:
        raise ValueError('Secret et chiffré valides requis')
    try:
        raw = urlsafe_b64decode(value.encode('ascii'))
    except (ValueError, UnicodeError) as error:
        raise IntegrityError('Accès chiffré invalide') from error
    if len(raw) < 48:
        raise IntegrityError('Accès chiffré invalide')
    nonce, cipher, tag = raw[:16], raw[16:-32], raw[-32:]
    expected = hmac.digest(secret, b'access-v1' + nonce + cipher, 'sha256')
    if not hmac.compare_digest(tag, expected):
        raise IntegrityError('Étiquette d’accès invalide')
    try:
        return bytes(a ^ b for a, b in zip(cipher, _stream(secret, nonce, len(cipher)))).decode('utf-8')
    except UnicodeError as error:
        raise IntegrityError('Accès chiffré invalide') from error


def challenge(verifier):
    return urlsafe_b64encode(sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')


def _now():
    return datetime.now(timezone.utc)


def _date(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Date avec fuseau requise')
    return parsed


def _delete(connection, session_id):
    connection.execute('DELETE FROM s6_access_events WHERE session_id=?', (session_id,))
    connection.execute('DELETE FROM s2_provider_access WHERE session_id=?', (session_id,))


def expire(store, now=None):
    if not available(store):
        return
    now = now or _now()
    connection = store._connection_checked()
    with _transaction(connection, write=True):
        sessions = [row[0] for row in connection.execute(
            'SELECT session_id FROM s2_provider_access WHERE COALESCE(verified_at, created_at)<?',
            ((now - EXPIRATION).isoformat(),)).fetchall()]
        for session_id in sessions:
            _delete(connection, session_id)


def _callback_url(value):
    if type(value) is not str:
        raise ValueError('URL de rappel requise')
    parsed = urlsplit(value)
    local = parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
    if ((parsed.scheme != 'https' and not local) or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != '/preparation/access/callback'):
        raise ValueError('URL de rappel HTTPS requise')
    return value


def start(store, session_id, secret, callback_url, now=None):
    if secret is None or not available(store):
        return None
    callback_url = _callback_url(callback_url)
    now = now or _now()
    verifier = secrets.token_urlsafe(32)
    connection = store._connection_checked()
    with _transaction(connection, write=True):
        _delete(connection, session_id)
        connection.execute('INSERT INTO s2_provider_access '
                           '(session_id,verifier_cipher,key_cipher,created_at,verified_at,checked_at,limit_usd,limit_remaining_usd,is_free_tier,status,status_reason) '
                           "VALUES (?,?,NULL,?,NULL,NULL,NULL,NULL,0,'pending',NULL)",
                           (session_id, encrypt(secret, verifier), now.isoformat()))
    query = urlencode({'callback_url': callback_url, 'code_challenge': challenge(verifier),
                       'code_challenge_method': 'S256'})
    return {'authorize_url': AUTHORIZE_URL + '?' + query}


def _event_intent(connection, session_id, kind, now):
    event_id = secrets.token_hex(16)
    connection.execute('INSERT INTO s6_access_events VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL)',
                       (event_id, session_id, kind, 'INTENT_RECORDED', now.isoformat()))
    return event_id


def _redacted(raw, sensitive=()):
    safe = raw
    try:
        document = json.loads(raw, object_pairs_hook=storage._unique_object, parse_float=str)
        if type(document) is dict and 'key' in document:
            document['key'] = '[expurgé]'
        safe = encode(document).encode('utf-8')
    except (ValueError, TypeError, UnicodeError):
        pass
    for value in sensitive:
        if type(value) is str and value:
            safe = safe.replace(value.encode('utf-8'), b'[expurg\xc3\xa9]')
    text = safe.decode('utf-8', errors='replace')
    return sha256(safe).hexdigest(), text[:512]


def _event_result(connection, event_id, state, now, status=None, raw=None, sensitive=()):
    digest = excerpt = None
    if raw is not None:
        digest, excerpt = _redacted(raw, sensitive)
    connection.execute('UPDATE s6_access_events SET state=?,observed_at=?,http_status=?,document_sha256=?,excerpt=? '
                       "WHERE event_id=? AND state='INTENT_RECORDED'",
                       (state, now.isoformat(), status, digest, excerpt, event_id))


def _money(value):
    if value is None:
        return None
    if type(value) not in (str, int, float):
        raise ValueError('Montant fournisseur invalide')
    try:
        amount = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('Montant fournisseur invalide') from error
    if not amount.is_finite() or amount < 0:
        raise ValueError('Montant fournisseur invalide')
    return str(amount)


def _decode(raw):
    value = json.loads(raw, object_pairs_hook=storage._unique_object, parse_float=str)
    encode(value)
    if type(value) is not dict:
        raise ValueError('Réponse fournisseur invalide')
    return value


def _credential(value):
    if (type(value) is not str or not value or not value.isascii()
            or any(character.isspace() or ord(character) < 32 for character in value)):
        raise ValueError('Clé fournisseur invalide')
    return value


class OpenRouterAccess:
    def _request(self, method, path, *, key=None, body=None):
        connection = HTTPSConnection(HOST, timeout=30)
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        if key is not None:
            headers['Authorization'] = 'Bearer ' + key
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            try:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            except IncompleteRead as error:
                raw = error.partial
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError('Réponse fournisseur hors limites')
            return response.status, raw
        finally:
            connection.close()

    def exchange(self, code, verifier):
        body = encode({'code': code, 'code_verifier': verifier, 'code_challenge_method': 'S256'})
        return self._request('POST', EXCHANGE_PATH, body=body)

    def verify(self, key):
        return self._request('GET', VERIFY_PATH, key=_credential(key))


def _verify(store, session_id, transport, key, now):
    connection = store._connection_checked()
    with _transaction(connection, write=True):
        event_id = _event_intent(connection, session_id, 'verify', now)
    try:
        status, raw = transport.verify(key)
    except Exception:
        observed = _now()
        with _transaction(connection, write=True):
            _event_result(connection, event_id, 'FAILED', observed, sensitive=(key,))
            connection.execute('UPDATE s2_provider_access SET checked_at=? WHERE session_id=?',
                               (observed.isoformat(), session_id))
        return
    state = 'RECEIVED'
    update = None
    if status == 200:
        try:
            document = _decode(raw)
            if type(document['is_free_tier']) is not bool:
                raise ValueError('Statut de palier invalide')
            update = (_money(document.get('limit')),
                      _money(document.get('limit_remaining')), int(document['is_free_tier']))
        except (ValueError, KeyError, TypeError):
            state = 'FAILED'
    with _transaction(connection, write=True):
        observed = _now()
        _event_result(connection, event_id, state, observed, status, raw, (key,))
        if update is not None:
            connection.execute("UPDATE s2_provider_access SET verified_at=?,checked_at=?,limit_usd=?,limit_remaining_usd=?,is_free_tier=?,status='connected',status_reason=NULL WHERE session_id=?",
                               (observed.isoformat(), observed.isoformat(), *update, session_id))
        elif status in (401, 403):
            connection.execute("UPDATE s2_provider_access SET checked_at=?,status='invalid',status_reason='KEY_REJECTED' WHERE session_id=?",
                               (observed.isoformat(), session_id))
        else:
            connection.execute('UPDATE s2_provider_access SET checked_at=? WHERE session_id=?',
                               (observed.isoformat(), session_id))


def callback(store, session_id, secret, code, transport=None, now=None):
    if secret is None or not available(store):
        return None
    if type(code) is not str or not code:
        raise ValueError('Code d’autorisation requis')
    transport = transport or OpenRouterAccess()
    now = now or _now()
    expire(store, now)
    connection = store._connection_checked()
    row = connection.execute("SELECT verifier_cipher FROM s2_provider_access WHERE session_id=? AND status='pending'",
                             (session_id,)).fetchone()
    if row is None:
        from .preparation import Denied
        raise Denied('ACCESS_NO_PENDING')
    verifier = decrypt(secret, row[0])
    with _transaction(connection, write=True):
        event_id = _event_intent(connection, session_id, 'exchange', now)
    try:
        status, raw = transport.exchange(code, verifier)
    except Exception:
        status, raw = None, None
    document = None
    if status == 200 and raw is not None:
        try:
            document = _decode(raw)
            key = _credential(document['key'])
        except (ValueError, KeyError, TypeError):
            document = None
    with _transaction(connection, write=True):
        _event_result(connection, event_id, 'RECEIVED' if raw is not None else 'FAILED', _now(), status, raw,
                      (code, verifier, document.get('key') if document else None))
        if document is None:
            _delete(connection, session_id)
        else:
            connection.execute("UPDATE s2_provider_access SET verifier_cipher=NULL,key_cipher=?,status='connected',status_reason=NULL WHERE session_id=?",
                               (encrypt(secret, document['key']), session_id))
    if document is None:
        from .preparation import Denied
        error = Denied('ACCESS_EXCHANGE_FAILED')
        error.provider_status = status
        raise error
    _verify(store, session_id, transport, document['key'], now)
    return view(store, session_id, secret, transport, now=now, refresh=False)


def _row_view(row, reason=None):
    if row is None:
        value = {'connected': False, 'verified_at': None, 'limit_usd': None,
                 'limit_remaining_usd': None, 'is_free_tier': False, 'status': 'disconnected'}
        if reason is not None:
            value['reason'] = reason
        return value
    return {'connected': row[0] == 'connected', 'verified_at': row[2], 'limit_usd': row[3],
            'limit_remaining_usd': row[4], 'is_free_tier': bool(row[5]), 'status': row[0]}


def view(store, session_id, secret, transport=None, now=None, *, refresh=True):
    if secret is None or not available(store):
        return {'connected': False, 'status': 'unavailable'}
    transport = transport or OpenRouterAccess()
    now = now or _now()
    expire(store, now)
    connection = store._connection_checked()
    row = connection.execute('SELECT status,key_cipher,verified_at,limit_usd,limit_remaining_usd,is_free_tier,checked_at '
                             'FROM s2_provider_access WHERE session_id=?', (session_id,)).fetchone()
    if row and row[0] in ('connected', 'invalid'):
        try:
            key = decrypt(secret, row[1])
        except IntegrityError:
            with _transaction(connection, write=True):
                _delete(connection, session_id)
            return _row_view(None, 'SECRET_CHANGED')
    if (refresh and row and row[0] in ('connected', 'invalid')
            and (row[6] is None or now - _date(row[6]) > REFRESH_INTERVAL)):
        _verify(store, session_id, transport, key, now)
        row = connection.execute('SELECT status,key_cipher,verified_at,limit_usd,limit_remaining_usd,is_free_tier,checked_at '
                                 'FROM s2_provider_access WHERE session_id=?', (session_id,)).fetchone()
    return _row_view(row)


def key_for_session(store, session_id, secret, transport=None, now=None):
    state = view(store, session_id, secret, transport, now)
    if not state.get('connected'):
        from .preparation import Denied
        raise Denied('ACCESS_REQUIRED')
    row = store._connection_checked().execute(
        'SELECT status,key_cipher FROM s2_provider_access WHERE session_id=?', (session_id,)).fetchone()
    if row is None or row[0] != 'connected':
        from .preparation import Denied
        raise Denied('ACCESS_REQUIRED')
    return decrypt(secret, row[1])


def disconnect(store, session_id, secret):
    if secret is None or not available(store):
        return None
    connection = store._connection_checked()
    with _transaction(connection, write=True):
        _delete(connection, session_id)
    return _row_view(None)


def verify_provider_access(store, connection):
    for row in connection.execute('SELECT session_id,verifier_cipher,key_cipher,created_at,verified_at,checked_at,status,status_reason FROM s2_provider_access'):
        session_id, verifier, key, created, verified, checked, status, reason = row
        if not connection.execute('SELECT 1 FROM s2_sessions WHERE session_id=?', (session_id,)).fetchone():
            raise IntegrityError('Accès sans session')
        _date(created)
        if verified is not None:
            _date(verified)
        if checked is not None:
            _date(checked)
        if (status == 'pending') != (verifier is not None) or (status != 'pending') != (key is not None):
            raise IntegrityError('État d’accès divergent')
        if (status == 'invalid') != (reason is not None):
            raise IntegrityError('Motif d’accès divergent')
    for event_id, session_id, state, created, observed, digest, excerpt in connection.execute(
            'SELECT event_id,session_id,state,created_at,observed_at,document_sha256,excerpt FROM s6_access_events'):
        if not connection.execute('SELECT 1 FROM s2_provider_access WHERE session_id=?',
                                  (session_id,)).fetchone():
            raise IntegrityError('Événement sans accès')
        _date(created)
        if (state == 'INTENT_RECORDED') != (observed is None):
            raise IntegrityError('Chronologie d’accès divergente')
        if observed is not None:
            _date(observed)
        if digest is not None and re.fullmatch(r'[0-9a-f]{64}', digest) is None:
            raise IntegrityError('Empreinte d’accès invalide')
        if excerpt is not None and len(excerpt) > 512:
            raise IntegrityError('Extrait d’accès hors limites')
