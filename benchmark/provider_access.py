"""Accès OpenRouter délégué, chiffré et lié à une session S2

Budget d'un échange fournisseur. Le chronomètre monotone part avant la résolution DNS et
vise `REQUEST_BUDGET_SECONDS`. La résolution DNS et l'établissement de la connexion ne sont
pas interruptibles : ils consomment ce budget et peuvent le dépasser, et le reste est vérifié
dès la socket établie, avant l'envoi HTTP. À partir de là une garde coupe la socket à
l'échéance, donc l'envoi, les en-têtes et le corps sont bornés même si le fournisseur répond
goutte à goutte ; un simple délai d'inactivité par réception ne les bornerait pas. Seule cette
phase postérieure à la connexion est bornée : la résolution DNS n'a pas de délai propre et un
hôte à plusieurs adresses enchaîne autant de tentatives de connexion, donc aucune durée totale
d'échange n'est garantie ici. Un rappel enchaîne l'échange puis la vérification, d'où
`CALLBACK_BUDGET_SECONDS` dont le relais de `service` dérive son propre délai. Aucun réessai
n'est ajouté : un budget épuisé est un échec observé, enregistré comme tel.
"""
from base64 import b64decode, urlsafe_b64encode
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from http.client import HTTPException, HTTPSConnection, IncompleteRead
import hmac
import json
import re
import secrets
import socket
import threading
import time
from urllib.parse import urlencode, urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
READ_CHUNK_BYTES = 65536
# Budget visé d'un échange fournisseur : la résolution DNS et la connexion le consomment sans
# être interruptibles, la garde de socket borne tout ce qui suit
REQUEST_BUDGET_SECONDS = 30
# Un rappel enchaîne l'échange puis la vérification sur la même requête entrante
CALLBACK_REQUESTS = 2
CALLBACK_BUDGET_SECONDS = REQUEST_BUDGET_SECONDS * CALLBACK_REQUESTS

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
            if layout in ('s6', 's7'):
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


def _privacy(connection):
    from . import privacy
    return privacy if privacy.available(connection) else None


def authorize_session(connection, session_id, current=None):
    """Délègue les dates S7 sans introduire de politique de session dans S6"""
    privacy = _privacy(connection)
    if privacy is not None:
        privacy.authorize_session(connection, session_id, current=current)


def parse_secret(value):
    if value in (None, ''):
        return None
    if type(value) is not str or re.fullmatch(r'[0-9a-fA-F]{64}', value) is None:
        raise ValueError('BENCHMARK_ACCESS_SECRET doit contenir 32 octets hexadécimaux')
    return bytes.fromhex(value)


CIPHER_VERSION = 'aes256gcm-v1'


def _cipher_context(secret, session_id, purpose):
    if type(secret) is not bytes or len(secret) != 32:
        raise ValueError('Secret de 32 octets requis')
    if type(session_id) is not str or not session_id or purpose not in ('key', 'oauth'):
        raise ValueError('Session et usage key/oauth requis')


def _base64(value):
    try:
        raw = b64decode(value.encode('ascii'), altchars=b'-_', validate=True)
        if urlsafe_b64encode(raw).decode('ascii') != value:
            raise ValueError('Base64 non canonique')
        return raw
    except (ValueError, UnicodeError) as error:
        raise IntegrityError('Accès chiffré invalide') from error


def _envelope(value):
    if type(value) is not str:
        raise IntegrityError('Accès chiffré invalide')
    parts = value.split(':')
    if (len(parts) != 3 or parts[0] != CIPHER_VERSION
            or re.fullmatch(r'[0-9a-f]{32}', parts[1]) is None):
        raise IntegrityError('Version ou enveloppe d’accès invalide')
    raw = _base64(parts[2])
    if len(raw) < 28:
        raise IntegrityError('Accès chiffré invalide')
    return parts[1], raw[:12], raw[12:]


def validate(cipher):
    """Valide le wrapper AEAD v1 sans secret ; ne prouve pas son authenticité

    Retourne None ou lève IntegrityError, y compris pour une enveloppe legacy
    """
    _envelope(cipher)


def _aad(session_id, credential_id, purpose):
    return encode([CIPHER_VERSION, session_id, credential_id, purpose]).encode('utf-8')


def encrypt(secret, value, session_id, purpose):
    """Nouvelle écriture AES-256-GCM liée à la session et à l'usage key/oauth"""
    _cipher_context(secret, session_id, purpose)
    if type(value) is not str:
        raise ValueError('Texte requis')
    credential_id, nonce = secrets.token_hex(16), secrets.token_bytes(12)
    cipher = AESGCM(secret).encrypt(nonce, value.encode('utf-8'),
                                    _aad(session_id, credential_id, purpose))
    return ':'.join((CIPHER_VERSION, credential_id,
                     urlsafe_b64encode(nonce + cipher).decode('ascii')))


def decrypt(secret, value, session_id, purpose):
    """Lecture AEAD uniquement ; aucune migration implicite"""
    _cipher_context(secret, session_id, purpose)
    credential_id, nonce, cipher = _envelope(value)
    try:
        return AESGCM(secret).decrypt(nonce, cipher,
            _aad(session_id, credential_id, purpose)).decode('utf-8')
    except (InvalidTag, UnicodeError) as error:
        raise IntegrityError('Accès chiffré indisponible') from error


def reencrypt_legacy(secret, cipher, session_id, purpose):
    """Migration pure HMAC/XOR vers AEAD ; l'appelant porte la liaison legacy

    Aucun accès au stockage, aucune écriture legacy, aucun fallback en lecture
    """
    _cipher_context(secret, session_id, purpose)
    if type(cipher) is not str:
        raise IntegrityError('Accès legacy invalide')
    raw = _base64(cipher)
    if len(raw) < 48:
        raise IntegrityError('Accès legacy invalide')
    nonce, ciphertext, tag = raw[:16], raw[16:-32], raw[-32:]
    if not hmac.compare_digest(tag, hmac.digest(secret, b'access-v1' + nonce + ciphertext, 'sha256')):
        raise IntegrityError('Accès legacy indisponible')
    stream = b''.join(hmac.digest(secret, nonce + counter.to_bytes(8, 'big'), 'sha256')
                      for counter in range((len(ciphertext) + 31) // 32))
    try:
        plaintext = bytes(a ^ b for a, b in zip(ciphertext, stream)).decode('utf-8')
    except UnicodeError as error:
        raise IntegrityError('Accès legacy invalide') from error
    return encrypt(secret, plaintext, session_id, purpose)


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
    connection = store._connection_checked()
    if _privacy(connection) is not None:
        return
    now = now or _now()
    with _transaction(connection, write=True):
        sessions = [row[0] for row in connection.execute(
            'SELECT session_id FROM s2_provider_access WHERE COALESCE(verified_at, created_at)<=?',
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
    verifier = secrets.token_urlsafe(32)
    connection = store._connection_checked()
    authorize_session(connection, session_id, current=now)
    now = now or _now()
    with _transaction(connection, write=True):
        _no_preparation_in_progress(connection, session_id)
        _delete(connection, session_id)
        connection.execute('INSERT INTO s2_provider_access '
                           '(session_id,verifier_cipher,key_cipher,created_at,verified_at,checked_at,limit_usd,limit_remaining_usd,is_free_tier,status,status_reason) '
                           "VALUES (?,?,NULL,?,NULL,NULL,NULL,NULL,0,'pending',NULL)",
                           (session_id, encrypt(secret, verifier, session_id, 'oauth'), now.isoformat()))
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


def remaining_budget(deadline):
    """Reste d'un budget monotone ; épuisé, l'appelant échoue au lieu d'attendre encore"""
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError('Budget de délai épuisé')
    return left


@contextmanager
def _deadline_guard(sock, deadline):
    """Coupe la socket à l'échéance : un délai par réception ne borne pas une réponse goutte à goutte

    La socket est arrêtée, jamais fermée : `http.client` et l'appelant gardent un objet valide et
    voient une fin de flux. Le minuteur est annulé puis récolté à la sortie, y compris en erreur.
    """
    def cut():
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    timer = threading.Timer(max(deadline - time.monotonic(), 0), cut)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        timer.join()


def _read_bounded(response, sock, deadline, limit=MAX_RESPONSE_BYTES):
    """Lecture bornée en octets et en temps ; la garde termine une réception qui déborde"""
    chunks, size = [], 0
    while size <= limit and not response.isclosed():
        # La réponse close a relâché la socket : ne plus la régler, seulement sortir
        sock.settimeout(remaining_budget(deadline))
        try:
            chunk = response.read(min(READ_CHUNK_BYTES, limit + 1 - size))
        except IncompleteRead as error:
            chunks.append(error.partial)
            size += len(error.partial)
            break
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if size > limit:
        raise ValueError('Réponse fournisseur hors limites')
    return b''.join(chunks)


class OpenRouterAccess:
    def _request(self, method, path, *, key=None, body=None):
        deadline = time.monotonic() + REQUEST_BUDGET_SECONDS
        connection = HTTPSConnection(HOST, timeout=REQUEST_BUDGET_SECONDS)
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        if key is not None:
            headers['Authorization'] = 'Bearer ' + key
        try:
            # La résolution DNS et la connexion ne sont pas interruptibles : le budget restant
            # est vérifié une fois la socket établie, avant d'émettre la requête
            connection.connect()
            sock = connection.sock
            sock.settimeout(remaining_budget(deadline))
            with _deadline_guard(sock, deadline):
                try:
                    connection.request(method, path, body=body, headers=headers)
                    response = connection.getresponse()
                    # La socket est retenue ici : une réponse fermante la relâche de `connection`
                    raw = _read_bounded(response, sock, deadline)
                except (OSError, HTTPException):
                    # Coupée par la garde, la rupture est un budget épuisé et non un défaut réseau
                    remaining_budget(deadline)
                    raise
            remaining_budget(deadline)
            return response.status, raw
        finally:
            connection.close()

    def exchange(self, code, verifier):
        body = encode({'code': code, 'code_verifier': verifier, 'code_challenge_method': 'S256'})
        return self._request('POST', EXCHANGE_PATH, body=body)

    def verify(self, key):
        return self._request('GET', VERIFY_PATH, key=_credential(key))


def _no_preparation_in_progress(connection, session_id):
    if connection.execute(
            "SELECT 1 FROM operations o JOIN s2_dossiers d USING(dossier_id) "
            "WHERE d.session_id=? AND o.phase IN ('preparation','correction','qualification') "
            "AND o.state!='RECEIVED' LIMIT 1", (session_id,)).fetchone():
        from .preparation import Denied
        raise Denied('PREPARATION_IN_PROGRESS')


def preparation_budget_id(session_id):
    return 'personal-preparation-' + session_id


def _key_details(raw):
    document = _decode(raw)
    document = document.get('data', document)
    if type(document) is not dict or type(document.get('is_free_tier')) is not bool:
        raise ValueError('Métadonnées de clé invalides')
    return document


def _bounded_key_amounts(document):
    limit, remaining = _money(document.get('limit')), _money(document.get('limit_remaining'))
    if (limit is None or remaining is None or Decimal(limit) > 50
            or not 0 < Decimal(remaining) <= Decimal(limit) or document.get('limit_reset') is not None):
        raise ValueError('Plafond personnel requis')
    return limit, remaining


def import_key(store, session_id, secret, key, transport=None):
    from .preparation import Denied
    if secret is None or not available(store):
        raise Denied('ACCESS_UNAVAILABLE')
    if type(key) is not str or len(key) > 512 or not key.startswith('sk-or-v1-'):
        raise Denied('ACCESS_KEY_REJECTED')
    _credential(key)
    connection = store._connection_checked()
    authorize_session(connection, session_id)
    now = _now()
    with _transaction(connection, write=True):
        _no_preparation_in_progress(connection, session_id)
        event_id = _event_intent(connection, session_id, 'verify', now)
    try:
        status, raw = (transport or OpenRouterAccess()).verify(key)
        if status != 200:
            raise ValueError('Clé refusée')
        document = _key_details(raw)
        try:
            limit, remaining = _bounded_key_amounts(document)
        except ValueError:
            raise Denied('ACCESS_CAP_REQUIRED') from None
    except Exception as error:
        with _transaction(connection, write=True):
            _event_result(connection, event_id, 'FAILED', _now(), sensitive=(key,))
        if isinstance(error, Denied):
            raise
        raise Denied('ACCESS_KEY_REJECTED') from None
    budget_id = preparation_budget_id(session_id)
    if not connection.execute('SELECT 1 FROM budgets WHERE budget_id=?', (budget_id,)).fetchone():
        store.create_budget(budget_id, limit, 'USD')
    with _transaction(connection, write=True):
        _no_preparation_in_progress(connection, session_id)
        _event_result(connection, event_id, 'RECEIVED', _now(), status, raw, (key,))
        connection.execute('INSERT INTO s2_provider_access '
            '(session_id,verifier_cipher,key_cipher,created_at,verified_at,checked_at,limit_usd,limit_remaining_usd,is_free_tier,status,status_reason) '
            "VALUES (?,NULL,?,?,?,?,?,?,?,'connected',NULL) "
            'ON CONFLICT(session_id) DO UPDATE SET verifier_cipher=NULL,key_cipher=excluded.key_cipher,'
            'created_at=excluded.created_at,verified_at=excluded.verified_at,checked_at=excluded.checked_at,'
            "limit_usd=excluded.limit_usd,limit_remaining_usd=excluded.limit_remaining_usd,is_free_tier=excluded.is_free_tier,status='connected',status_reason=NULL",
            (session_id, encrypt(secret, key, session_id, 'key'), now.isoformat(), now.isoformat(), now.isoformat(),
             limit, remaining, int(document['is_free_tier'])))
    return view(store, session_id, secret, transport, refresh=False)


def _verify(store, session_id, transport, key, now):
    connection = store._connection_checked()
    with _transaction(connection, write=True):
        authorize_session(connection, session_id)
        if not connection.execute("SELECT 1 FROM s2_provider_access WHERE session_id=? AND status!='pending'",
                                  (session_id,)).fetchone():
            return
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
    invalid_reason = None
    if status == 200:
        try:
            document = _key_details(raw)
            if type(document['is_free_tier']) is not bool:
                raise ValueError('Statut de palier invalide')
            amounts = _bounded_key_amounts(document)
            update = (*amounts, int(document['is_free_tier']))
        except (ValueError, KeyError, TypeError):
            state = 'FAILED'
            invalid_reason = 'ACCESS_CAP_REQUIRED'
    with _transaction(connection, write=True):
        observed = _now()
        _event_result(connection, event_id, state, observed, status, raw, (key,))
        if update is not None:
            connection.execute("UPDATE s2_provider_access SET verified_at=?,checked_at=?,limit_usd=?,limit_remaining_usd=?,is_free_tier=?,status='connected',status_reason=NULL WHERE session_id=?",
                               (observed.isoformat(), observed.isoformat(), *update, session_id))
        elif invalid_reason is not None:
            connection.execute("UPDATE s2_provider_access SET checked_at=?,status='invalid',status_reason=? WHERE session_id=?",
                               (observed.isoformat(), invalid_reason, session_id))
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
    connection = store._connection_checked()
    authorize_session(connection, session_id, current=now)
    now = now or _now()
    row = connection.execute("SELECT verifier_cipher FROM s2_provider_access WHERE session_id=? AND status='pending'",
                             (session_id,)).fetchone()
    from .preparation import Denied
    if row is None:
        raise Denied('ACCESS_NO_PENDING')
    try:
        verifier = decrypt(secret, row[0], session_id, 'oauth')
    except IntegrityError:
        raise Denied('ACCESS_UNAVAILABLE') from None
    expire(store, now)
    if not connection.execute("SELECT 1 FROM s2_provider_access WHERE session_id=? AND status='pending'",
                              (session_id,)).fetchone():
        raise Denied('ACCESS_NO_PENDING')
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
            _credential(document['key'])
        except (ValueError, KeyError, TypeError):
            document = None
    with _transaction(connection, write=True):
        _event_result(connection, event_id, 'RECEIVED' if raw is not None else 'FAILED', _now(), status, raw,
                      (code, verifier, document.get('key') if document else None))
        if document is None:
            _delete(connection, session_id)
        else:
            connection.execute("UPDATE s2_provider_access SET verifier_cipher=NULL,key_cipher=?,status='connected',status_reason=NULL WHERE session_id=?",
                               (encrypt(secret, document['key'], session_id, 'key'), session_id))
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
    value = {'connected': row[0] == 'connected', 'verified_at': row[2], 'limit_usd': row[3],
             'limit_remaining_usd': row[4], 'is_free_tier': bool(row[5]), 'status': row[0]}
    if row[0] == 'invalid':
        value['reason'] = row[7]
    if row[0] == 'connected' and (row[3] is None or row[4] is None
            or not 0 < Decimal(row[4]) <= Decimal(row[3]) <= 50):
        value.update(connected=False, status='invalid', reason='ACCESS_CAP_REQUIRED')
    return value


def status_only(store, session_id, now=None):
    """Métadonnées seules : aucune preuve de déchiffrement ni de validité fournisseur"""
    if not available(store):
        return {'connected': False, 'status': 'unavailable'}
    connection = store._connection_checked()
    row = connection.execute(
        'SELECT status,NULL,verified_at,limit_usd,limit_remaining_usd,is_free_tier,checked_at,status_reason '
        'FROM s2_provider_access WHERE session_id=?', (session_id,)).fetchone()
    value = _row_view(row)
    from .preparation import Denied
    try:
        authorize_session(connection, session_id, current=now)
    except Denied as error:
        if error.code != 'SESSION_EXPIRED':
            raise
        value.update(connected=False, status='unavailable', reason='SESSION_EXPIRED')
    return value


def view(store, session_id, secret, transport=None, now=None, *, refresh=True):
    if secret is None or not available(store):
        return {'connected': False, 'status': 'unavailable'}
    transport = transport or OpenRouterAccess()
    connection = store._connection_checked()
    authorize_session(connection, session_id, current=now)
    now = now or _now()
    query = ('SELECT status,key_cipher,verified_at,limit_usd,limit_remaining_usd,is_free_tier,'
             'checked_at,status_reason,verifier_cipher FROM s2_provider_access WHERE session_id=?')
    row = connection.execute(query, (session_id,)).fetchone()
    key = None
    if row:
        try:
            if row[0] == 'pending':
                decrypt(secret, row[8], session_id, 'oauth')
            else:
                key = decrypt(secret, row[1], session_id, 'key')
        except IntegrityError:
            return {'connected': False, 'status': 'unavailable', 'reason': 'ACCESS_UNAVAILABLE'}
    expire(store, now)
    row = connection.execute(query, (session_id,)).fetchone()
    if row and key is not None:
        if refresh and (row[6] is None or now - _date(row[6]) > REFRESH_INTERVAL):
            _verify(store, session_id, transport, key, now)
            row = connection.execute(query, (session_id,)).fetchone()
    return _row_view(row)


def key_for_session(store, session_id, secret, transport=None, now=None):
    from .preparation import Denied
    state = view(store, session_id, secret, transport, now)
    if not state.get('connected'):
        raise Denied('ACCESS_UNAVAILABLE' if state['status'] == 'unavailable' else 'ACCESS_REQUIRED')
    row = store._connection_checked().execute(
        'SELECT status,key_cipher FROM s2_provider_access WHERE session_id=?', (session_id,)).fetchone()
    if row is None or row[0] != 'connected':
        raise Denied('ACCESS_REQUIRED')
    try:
        return decrypt(secret, row[1], session_id, 'key')
    except IntegrityError:
        raise Denied('ACCESS_UNAVAILABLE') from None


def disconnect(store, session_id, secret):
    if not available(store):
        return None
    connection = store._connection_checked()
    authorize_session(connection, session_id)
    privacy = _privacy(connection)
    with _transaction(connection, write=True):
        if privacy is not None:
            event = privacy.journal_intent(store, 'key', session_id)
            privacy.apply_revocation(connection, event)
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
