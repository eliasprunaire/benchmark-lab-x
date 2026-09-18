"""Accès OpenRouter délégué sans appel réseau réel"""
from contextlib import closing, contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from benchmark.acquisition import execution
from benchmark.acquisition import campaigns
from benchmark import evaluation, preparation, provider_access, qualification, runtime, storage, web_api
from benchmark.transports.openrouter import ENDPOINT
from tests.test_s3_regressions import ACTOR, AUTHORITY, check, fixture, specification
from tests.test_s4_regressions import inputs, manifest, response


SECRET = bytes.fromhex('11' * 32)
KEY = 'sk-or-v1-fixture-secret'


class AccessTransport:
    def __init__(self):
        self.verify_result = (200, json.dumps({
            'limit': 20, 'limit_remaining': 18.5, 'usage': 1.5,
            'is_free_tier': False,
        }).encode())
        self.exchanges = []
        self.verifications = []
        self.observe = None

    def exchange(self, code, verifier):
        if self.observe:
            self.observe('exchange')
        self.exchanges.append((code, verifier))
        return 200, json.dumps({'key': KEY}).encode()

    def verify(self, key):
        if self.observe:
            self.observe('verify')
        self.verifications.append(key)
        if isinstance(self.verify_result, Exception):
            raise self.verify_result
        return self.verify_result


@contextmanager
def http_fixture(respond):
    """Serveur HTTP local d'une seule requête : le test écrit la réponse brute au rythme voulu

    L'échange traverse l'analyse réelle de `http.client` ; une connexion fictive masquerait le
    cycle de vie socket-réponse et la mise en mémoire tampon de `HTTPResponse.read`.
    """
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        server.settimeout(5)

        def serve():
            try:
                with server.accept()[0] as connection:
                    connection.recv(65536)
                    respond(connection)
            except OSError:
                pass

        worker = threading.Thread(target=serve)
        worker.start()
        try:
            yield server.getsockname()[1]
        finally:
            worker.join(10)
            assert not worker.is_alive(), 'serveur de test encore vivant'


class ProviderBudgetTests(unittest.TestCase):
    @staticmethod
    @contextmanager
    def provider(respond, budget=30):
        with http_fixture(respond) as port:
            def connect(host, timeout=None):
                return HTTPConnection('127.0.0.1', port, timeout=timeout)

            with patch.object(provider_access, 'HTTPSConnection', connect), \
                    patch.object(provider_access, 'REQUEST_BUDGET_SECONDS', budget):
                yield

    def test_budget_du_rappel_enchaine_echange_puis_verification(self):
        self.assertEqual(2, provider_access.CALLBACK_REQUESTS)
        self.assertEqual(provider_access.REQUEST_BUDGET_SECONDS * 2,
                         provider_access.CALLBACK_BUDGET_SECONDS)

    def test_reponse_fermante_est_lue_jusqu_a_la_fin(self):
        # Une réponse fermante relâche la socket de la connexion : la lecture doit rester valide
        def respond(connection):
            connection.sendall(b'HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n{}')

        with self.provider(respond):
            self.assertEqual((200, b'{}'), provider_access.OpenRouterAccess().verify(KEY))

    def test_reponse_fermante_en_http_1_1_est_lue_jusqu_a_la_fin(self):
        def respond(connection):
            connection.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\n\r\n{}')

        with self.provider(respond):
            self.assertEqual((200, b'{}'), provider_access.OpenRouterAccess().verify(KEY))

    def test_corps_goutte_a_goutte_coupe_au_budget(self):
        # Sans garde, `HTTPResponse.read` enchaîne les réceptions et dépasse le budget
        def respond(connection):
            connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 40\r\n\r\n')
            for _ in range(40):
                connection.sendall(b'x')
                time.sleep(.05)

        with self.provider(respond, budget=.2):
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                provider_access.OpenRouterAccess().verify(KEY)
            self.assertLess(time.monotonic() - started, 1)

    def test_entetes_goutte_a_goutte_coupent_au_budget(self):
        def respond(connection):
            for part in (b'HTTP/1.1 ', b'200 OK\r\n', b'Content-Type: application/json\r\n',
                         b'Content-Length: 2\r\n', b'\r\n', b'{}'):
                connection.sendall(part)
                time.sleep(.2)

        with self.provider(respond, budget=.2):
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                provider_access.OpenRouterAccess().verify(KEY)
            self.assertLess(time.monotonic() - started, 1)

    def test_lecture_fournisseur_bornee_en_octets(self):
        size = provider_access.MAX_RESPONSE_BYTES + 1

        def respond(connection):
            connection.sendall(('HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n' % size).encode()
                               + b'y' * size)

        with self.provider(respond):
            with self.assertRaisesRegex(ValueError, 'hors limites'):
                provider_access.OpenRouterAccess().verify(KEY)


class ProviderAccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='provider-access-')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name).resolve() / 'private'
        self.session, self.prepared, self.reference = fixture(self.data)
        qualification.initialize(self.data)
        campaigns.initialize(self.data)
        evaluation.initialize(self.data)
        provider_access.initialize(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        self.transport = AccessTransport()

    def test_manual_key_is_verified_encrypted_and_does_not_refill_budget(self):
        self.transport.verify_result = (200, json.dumps({'data': {
            'limit': 50, 'limit_remaining': 49, 'limit_reset': None, 'is_free_tier': False}}).encode())
        value = provider_access.import_key(self.store, self.session, SECRET, KEY, self.transport)
        self.assertTrue(value['connected'])
        self.assertEqual(KEY, provider_access.key_for_session(self.store, self.session, SECRET, self.transport))
        budget_id = provider_access.preparation_budget_id(self.session)
        self.assertEqual('50', self.store.inspect_budget(budget_id)['limit'])
        provider_access.import_key(self.store, self.session, SECRET, KEY, self.transport)
        self.assertEqual('50', self.store.inspect_budget(budget_id)['limit'])
        self.assertNotIn(KEY, json.dumps(value))
        self.assertNotIn(KEY, '\n'.join(self.store._connection.iterdump()))

    def test_personal_ledger_preserves_old_amount_without_enforcing_a_second_cap(self):
        budget_id = provider_access.preparation_budget_id(self.session)
        self.store.create_budget(budget_id, '20', 'USD')
        provider_access.import_key(self.store, self.session, SECRET, KEY, self.transport)
        original = self.store.inspect_operations()[0]
        operation = {key: original[key] for key in storage._OPERATION_KEYS}
        operation['operation_id'] = 'personal-over-old-cap'
        self.store.reserve_intent(operation, budget_id, '25')
        budget = self.store.inspect_budget(budget_id)
        self.assertTrue(budget['provider_managed'])
        self.assertEqual(('20', '25'), (budget['limit'], budget['reserved']))
        self.assertIsNone(budget['available'])
        self.store.create_budget('operator-test', '20', 'USD')
        with self.assertRaises(storage.BudgetError):
            self.store.reserve_intent({**operation, 'operation_id': 'operator-over-cap'}, 'operator-test', '25')

    def test_personal_key_expires_at_thirty_days_and_cannot_be_revived(self):
        started = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
        with patch.object(provider_access, '_now', return_value=started):
            provider_access.import_key(self.store, self.session, SECRET, KEY, self.transport)
        budget = self.store.inspect_budget(provider_access.preparation_budget_id(self.session))
        self.transport.verifications.clear()
        for days in (30, 31):
            with self.assertRaises(preparation.Denied):
                provider_access.key_for_session(self.store, self.session, SECRET,
                                               self.transport, now=started + timedelta(days=days))
        self.assertEqual([], self.transport.verifications)
        self.assertEqual(budget, self.store.inspect_budget(provider_access.preparation_budget_id(self.session)))

    def test_provider_exhaustion_or_revocation_blocks_personal_access(self):
        for status, remaining in ((200, 0), (401, 20)):
            with self.subTest(status=status):
                self.transport.verify_result = (200, json.dumps({'data': {
                    'limit': 50, 'limit_remaining': 20, 'is_free_tier': False}}).encode())
                provider_access.import_key(self.store, self.session, SECRET, KEY, self.transport)
                self.transport.verify_result = (status, json.dumps({'data': {
                    'limit': 50, 'limit_remaining': remaining, 'is_free_tier': False}}).encode())
                with self.assertRaises(preparation.Denied):
                    provider_access.key_for_session(self.store, self.session, SECRET,
                        self.transport, now=provider_access._now() + timedelta(hours=1))

    def test_successful_verification_renews_personal_access(self):
        started = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
        with patch.object(provider_access, '_now', return_value=started):
            provider_access.import_key(self.store, self.session, SECRET, KEY, self.transport)
        visit = started + timedelta(days=29)
        with patch.object(provider_access, '_now', return_value=visit):
            self.assertEqual(KEY, provider_access.key_for_session(
                self.store, self.session, SECRET, self.transport, now=visit))
        next_visit = visit + timedelta(days=29)
        with patch.object(provider_access, '_now', return_value=next_visit):
            self.assertEqual(KEY, provider_access.key_for_session(
                self.store, self.session, SECRET, self.transport, now=next_visit))

    def test_manual_import_rejects_unbounded_or_renewing_keys_and_wrong_csrf(self):
        for changes in ({'limit': None}, {'limit_reset': 'daily'}, {'limit': 51}):
            self.transport.verify_result = (200, json.dumps({'data': {
                'limit': 50, 'limit_remaining': 20, 'is_free_tier': False, **changes}}).encode())
            with self.assertRaises(preparation.Denied):
                provider_access.import_key(self.store, self.session, SECRET, KEY, self.transport)
        session_id, csrf, token = preparation.session(self.store, None, create=True)
        self.transport.verifications.clear()
        with self.assertRaises(preparation.Denied):
            web_api.dispatch(self.store, 'POST', '/preparation/access/key', token,
                {'csrf_token': 'wrong', 'key': KEY}, 'a' * 40, None,
                access_secret=SECRET, access_transport=self.transport, personal_preparation=True)
        self.assertEqual([], self.transport.verifications)
        with self.assertRaises(preparation.Denied):
            provider_access.import_key(self.store, session_id, SECRET, 'sk-ant-wrong-provider', self.transport)
        self.assertEqual([], self.transport.verifications)

    def test_oauth_replacement_cannot_remove_personal_spending_cap(self):
        provider_access.import_key(self.store, self.session, SECRET, KEY, self.transport)
        self.transport.verify_result = (200, json.dumps({'data': {
            'limit': None, 'limit_remaining': None, 'is_free_tier': False}}).encode())
        self.connect()
        with self.assertRaises(preparation.Denied):
            provider_access.key_for_session(self.store, self.session, SECRET, self.transport)

    def test_oauth_without_personal_ledger_also_requires_provider_cap(self):
        self.assertIsNone(self.store._connection.execute('SELECT 1 FROM budgets WHERE budget_id=?',
            (provider_access.preparation_budget_id(self.session),)).fetchone())
        for changes in ({'limit': None}, {'limit_reset': 'daily'}, {'limit_remaining': 0}):
            with self.subTest(changes=changes):
                self.transport.verify_result = (200, json.dumps({'data': {
                    'limit': 50, 'limit_remaining': 20, 'is_free_tier': False, **changes}}).encode())
                self.connect()
                with self.assertRaises(preparation.Denied):
                    provider_access.key_for_session(self.store, self.session, SECRET, self.transport)

    def test_manual_form_and_route_return_only_safe_state(self):
        from benchmark_web.views import render
        _, csrf, token = preparation.session(self.store, None, create=True)
        code, value, _, start = web_api.dispatch(self.store, 'POST', '/preparation/access/key', token,
            {'csrf_token': csrf, 'key': KEY}, 'a' * 40, None,
            access_secret=SECRET, access_transport=self.transport, personal_preparation=True)
        self.assertEqual(200, code)
        self.assertIsNone(start)
        self.assertNotIn(KEY, json.dumps(value))
        html = render({'dossiers': [], 'personal_preparation': True, 'personal_access': value}, csrf).decode()
        self.assertIn('type="password"', html)
        self.assertIn('autocomplete="new-password"', html)
        self.assertIn('Ajouter ma clé Openrouter', html)
        self.assertIn('Retirer la clé', html)
        self.assertNotIn(KEY, html)
        self.assertNotIn('localStorage', html)
        self.assertEqual([], self.transport.exchanges)

    def test_invalid_manual_key_preserves_existing_access(self):
        self.connect()
        self.transport.verify_result = (401, b'{}')
        with self.assertRaises(preparation.Denied):
            provider_access.import_key(self.store, self.session, SECRET, 'sk-or-v1-invalid', self.transport)
        self.assertEqual(KEY, provider_access.key_for_session(self.store, self.session, SECRET, self.transport))

    def connect(self):
        provider_access.start(self.store, self.session, SECRET,
                              'https://example.test/preparation/access/callback')
        return provider_access.callback(self.store, self.session, SECRET, 'authorization-code',
                                        self.transport)

    def test_base_anterieure_a_la_vague_2_refusee_explicitement(self):
        self.store.close()
        with closing(storage.sqlite3.connect(self.data / 'metadata.sqlite3')) as connection:
            connection.execute('PRAGMA foreign_keys=OFF')
            connection.execute('DROP TABLE s2_qualifications')
            connection.execute('ALTER TABLE s2_provider_access DROP COLUMN checked_at')
        with self.assertRaisesRegex(
                storage.SchemaError, '^Base antérieure à la vague 2 : à recréer$'):
            storage.Store(self.data)

    def test_pkce_chiffrement_et_vue_expurgee(self):
        self.assertEqual('E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM', provider_access.challenge(
            'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'))
        result = provider_access.start(self.store, self.session, SECRET,
                                       'http://127.0.0.1:8080/preparation/access/callback')
        query = parse_qs(urlsplit(result['authorize_url']).query)
        cipher = self.store._connection.execute(
            'SELECT verifier_cipher FROM s2_provider_access WHERE session_id=?',
            (self.session,)).fetchone()[0]
        verifier = provider_access.decrypt(SECRET, cipher, self.session, 'oauth')
        self.assertEqual(43, len(verifier))
        self.assertEqual([provider_access.challenge(verifier)], query['code_challenge'])
        self.assertEqual(['S256'], query['code_challenge_method'])
        self.assertNotIn(verifier, result['authorize_url'])
        self.assertEqual(verifier, provider_access.decrypt(SECRET, provider_access.encrypt(SECRET, verifier, self.session, 'oauth'), self.session, 'oauth'))
        damaged = provider_access.encrypt(SECRET, verifier, self.session, 'oauth')[:-2] + 'AA'
        with self.assertRaises(storage.IntegrityError):
            provider_access.decrypt(SECRET, damaged, self.session, 'oauth')

    def test_callback_verification_invalidation_et_panne_transitoire(self):
        accepted = self.transport.verify_result
        connected = self.connect()
        self.assertEqual({'connected': True, 'limit_usd': '20', 'limit_remaining_usd': '18.5',
                          'is_free_tier': False, 'status': 'connected'},
                         {key: connected[key] for key in connected if key != 'verified_at'})
        verified_at = connected['verified_at']
        old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        self.store._connection.execute(
            'UPDATE s2_provider_access SET verified_at=?,checked_at=? WHERE session_id=?',
            (old, old, self.session))
        self.store._connection.commit()
        self.transport.verify_result = OSError('panne transitoire')
        transient = provider_access.view(self.store, self.session, SECRET, self.transport)
        self.assertEqual('connected', transient['status'])
        self.assertEqual(old, transient['verified_at'])
        verification_count = len(self.transport.verifications)
        provider_access.view(self.store, self.session, SECRET, self.transport)
        self.assertEqual(verification_count, len(self.transport.verifications))
        self.store._connection.execute('UPDATE s2_provider_access SET checked_at=? WHERE session_id=?',
                                       (old, self.session))
        self.store._connection.commit()
        for status in (401, 403):
            with self.subTest(status=status):
                self.transport.verify_result = accepted
                self.connect()
                self.store._connection.execute(
                    'UPDATE s2_provider_access SET checked_at=? WHERE session_id=?',
                    (old, self.session))
                self.store._connection.commit()
                self.transport.verify_result = (status, b'{"error":"rejected"}')
                rejected = provider_access.view(self.store, self.session, SECRET, self.transport)
                self.assertEqual('invalid', rejected['status'])
                self.assertEqual('KEY_REJECTED', rejected['reason'])
                self.assertFalse(rejected['connected'])
                key_cipher, reason = self.store._connection.execute(
                    'SELECT key_cipher,status_reason FROM s2_provider_access WHERE session_id=?',
                    (self.session,)).fetchone()
                self.assertEqual(KEY, provider_access.decrypt(SECRET, key_cipher, self.session, 'key'))
                self.assertEqual('KEY_REJECTED', reason)
                verification_count = len(self.transport.verifications)
                self.assertEqual('invalid', provider_access.view(
                    self.store, self.session, SECRET, self.transport)['status'])
                self.assertEqual(verification_count, len(self.transport.verifications))
                with self.assertRaisesRegex(preparation.Denied, 'ACCESS_REQUIRED'):
                    provider_access.key_for_session(self.store, self.session, SECRET, self.transport)
        self.assertNotEqual(verified_at, old)

    def test_secret_change_preserve_acces_et_evenements(self):
        self.connect()
        connection = self.store._connection
        before = list(connection.iterdump())
        calls = len(self.transport.verifications)
        changed_secret = bytes.fromhex('22' * 32)
        value = provider_access.view(self.store, self.session, changed_secret, self.transport)
        self.assertEqual(('unavailable', 'ACCESS_UNAVAILABLE'), (value['status'], value['reason']))
        with self.assertRaisesRegex(preparation.Denied, '^ACCESS_UNAVAILABLE$'):
            provider_access.key_for_session(self.store, self.session, changed_secret, self.transport)
        self.assertEqual(before, list(connection.iterdump()))
        self.assertEqual(calls, len(self.transport.verifications))
        self.assertEqual(KEY, provider_access.key_for_session(self.store, self.session, SECRET, self.transport))

    def test_invalid_or_substituted_cipher_is_unavailable_without_cleanup(self):
        for pending in (False, True):
            for mutation in ('damaged', 'session', 'purpose', 'legacy'):
                with self.subTest(pending=pending, mutation=mutation):
                    if pending:
                        provider_access.start(self.store, self.session, SECRET,
                                              'https://example.test/preparation/access/callback')
                    else:
                        self.connect()
                    column, purpose = ('verifier_cipher', 'oauth') if pending else ('key_cipher', 'key')
                    cipher = provider_access.encrypt(SECRET, KEY,
                        'another-session' if mutation == 'session' else self.session,
                        ('key' if pending else 'oauth') if mutation == 'purpose' else purpose)
                    if mutation == 'damaged':
                        cipher = cipher[:-5] + 'AAAA='
                    if mutation == 'legacy':
                        cipher = ('AAECAwQFBgcICQoLDA0OD_cNulwbX3b_LA2oFwctDiyEuyDEz4dOqCfYJAqIweUk'
                                  '6Vy74ta1xBfiQDE9eWpARQ9kfYsu0TQ=')
                    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
                    self.store._connection.execute(
                        f'UPDATE s2_provider_access SET {column}=?,created_at=?,verified_at=? WHERE session_id=?',
                        (cipher, old, None if pending else old, self.session))
                    self.store._connection.commit()
                    before = list(self.store._connection.iterdump())
                    calls = (len(self.transport.exchanges), len(self.transport.verifications))
                    state = provider_access.view(self.store, self.session, SECRET, self.transport)
                    self.assertEqual('unavailable', state['status'])
                    with self.assertRaisesRegex(preparation.Denied, '^ACCESS_UNAVAILABLE$'):
                        if pending:
                            provider_access.callback(self.store, self.session, SECRET, 'code', self.transport)
                        else:
                            provider_access.key_for_session(self.store, self.session, SECRET, self.transport)
                    self.assertEqual(before, list(self.store._connection.iterdump()))
                    self.assertEqual(calls, (len(self.transport.exchanges), len(self.transport.verifications)))

    def test_status_only_is_pure_metadata_even_when_cipher_is_unreadable(self):
        self.connect()
        expected = provider_access.view(self.store, self.session, SECRET, self.transport, refresh=False)
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        self.store._connection.execute('UPDATE s2_provider_access SET key_cipher=?,checked_at=?',
                                       ('unreadable', old))
        self.store._connection.commit()
        before = list(self.store._connection.iterdump())
        with patch.object(provider_access, 'decrypt', side_effect=AssertionError('décryptage interdit')), \
                patch.object(provider_access.OpenRouterAccess, '_request', side_effect=AssertionError('réseau interdit')):
            self.assertEqual(expected, provider_access.status_only(self.store, self.session))
        self.assertEqual(before, list(self.store._connection.iterdump()))
        self.assertEqual('unavailable', provider_access.view(self.store, self.session, SECRET,
                                                           self.transport, refresh=False)['status'])

    def test_s7_authority_owns_expiry_not_provider_verification_age(self):
        from types import ModuleType
        from benchmark.transports.openrouter import OpenRouterPreparation
        self.connect()
        connection = self.store._connection
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        connection.execute('UPDATE s2_provider_access SET verified_at=?,checked_at=?', (old, old))
        deadline = datetime.now(timezone.utc) + timedelta(days=1)
        expires_at = [deadline]
        connection.commit()
        privacy = ModuleType('benchmark.privacy')
        privacy.available = lambda conn: conn is connection
        calls = []

        def authorize_session(conn, session_id, current=None):
            calls.append(session_id)
            self.assertIs(connection, conn)
            if (current or privacy.now()) >= expires_at[0]:
                raise preparation.Denied('SESSION_EXPIRED')
        privacy.authorize_session = authorize_session
        privacy.now = lambda: datetime.now(timezone.utc)
        with patch.dict('sys.modules', {'benchmark.privacy': privacy}), \
                patch('benchmark.privacy', privacy, create=True):
            before = list(connection.iterdump())
            provider_access.expire(self.store)
            self.assertEqual(before, list(connection.iterdump()))
            self.assertTrue(provider_access.view(self.store, self.session, SECRET,
                                                 self.transport, refresh=False)['connected'])
            self.assertEqual(KEY, provider_access.key_for_session(self.store, self.session, SECRET, self.transport))
            bound = OpenRouterPreparation(None).for_session(KEY, self.session, SECRET)
            self.assertTrue(bound.authorized(self.store))
            self.assertGreaterEqual(len(calls), 3)
            before = list(connection.iterdump())
            self.assertTrue(provider_access.status_only(self.store, self.session)['connected'])
            self.assertEqual(before, list(connection.iterdump()))
            privacy.now = lambda: deadline + timedelta(seconds=1)
            before = list(connection.iterdump())
            calls_before = len(self.transport.verifications)
            metadata = provider_access.status_only(self.store, self.session)
            self.assertFalse(metadata['connected'])
            self.assertEqual('SESSION_EXPIRED', metadata['reason'])
            for action in (lambda: provider_access.view(self.store, self.session, SECRET, self.transport),
                           lambda: provider_access.key_for_session(self.store, self.session, SECRET, self.transport),
                           lambda: bound.authorized(self.store)):
                with self.assertRaisesRegex(preparation.Denied, '^SESSION_EXPIRED$'):
                    action()
            self.assertEqual(before, list(connection.iterdump()))
            self.assertEqual(calls_before, len(self.transport.verifications))

    def test_callback_expire_avant_echange(self):
        old = datetime.now(timezone.utc) - timedelta(days=31)
        provider_access.start(self.store, self.session, SECRET,
                              'https://example.test/preparation/access/callback', now=old)
        with self.assertRaisesRegex(preparation.Denied, 'ACCESS_NO_PENDING'):
            provider_access.callback(self.store, self.session, SECRET, 'code', self.transport)
        self.assertEqual([], self.transport.exchanges)

    def test_callback_secret_change_preserve_pending(self):
        provider_access.start(self.store, self.session, SECRET,
                              'https://example.test/preparation/access/callback')
        before = list(self.store._connection.iterdump())
        wrong_secret = bytes.fromhex('22' * 32)
        value = provider_access.view(self.store, self.session, wrong_secret, self.transport)
        self.assertEqual('unavailable', value['status'])
        with self.assertRaisesRegex(preparation.Denied, '^ACCESS_UNAVAILABLE$'):
            provider_access.callback(self.store, self.session, wrong_secret, 'code', self.transport)
        self.assertEqual([], self.transport.exchanges)
        self.assertEqual(before, list(self.store._connection.iterdump()))
        self.assertTrue(provider_access.callback(
            self.store, self.session, SECRET, 'code', self.transport)['connected'])

    def test_removal_during_oauth_exchange_blocks_following_verification(self):
        provider_access.start(self.store, self.session, SECRET,
                              'https://example.test/preparation/access/callback')
        self.transport.observe = lambda kind: provider_access.disconnect(self.store, self.session, SECRET)
        state = provider_access.callback(self.store, self.session, SECRET, 'code', self.transport)
        self.assertEqual('disconnected', state['status'])
        self.assertEqual([], self.transport.verifications)
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_evenements_expurges_et_effaces_avec_acces(self):
        observed = []
        self.transport.observe = lambda kind: observed.append(self.store._connection.execute(
            'SELECT kind,state FROM s6_access_events ORDER BY rowid DESC LIMIT 1').fetchone())
        self.connect()
        self.assertEqual([('exchange', 'INTENT_RECORDED'), ('verify', 'INTENT_RECORDED')], observed)
        rows = self.store._connection.execute(
            'SELECT state,document_sha256,excerpt FROM s6_access_events ORDER BY rowid').fetchall()
        self.assertEqual(['RECEIVED', 'RECEIVED'], [row[0] for row in rows])
        text = json.dumps(rows, ensure_ascii=False)
        verifier = self.transport.exchanges[0][1]
        self.assertNotIn('sk-or-', text)
        self.assertNotIn(verifier, text)
        self.assertNotIn('authorization-code', text)
        self.assertTrue(all(row[1] and len(row[1]) == 64 and len(row[2]) <= 512 for row in rows))
        with patch.object(preparation, 'session', return_value=(self.session, 'csrf', None)):
            code, value, _, _ = web_api.dispatch(
                self.store, 'GET', '/preparation/access', 'token', None, 'a' * 40, None,
                access_secret=SECRET, access_transport=self.transport)
        socket_view = storage._strict_json(value)
        self.assertEqual(200, code)
        self.assertNotIn('sk-or-', socket_view)
        self.assertNotIn(verifier, socket_view)
        provider_access.disconnect(self.store, self.session, SECRET)
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s6_access_events').fetchone()[0])
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s2_provider_access').fetchone()[0])

    def test_reconnexion_et_expiration_remplacent_toute_la_ligne(self):
        self.connect()
        provider_access.start(self.store, self.session, SECRET,
                              'https://example.test/preparation/access/callback')
        status, key = self.store._connection.execute(
            'SELECT status,key_cipher FROM s2_provider_access WHERE session_id=?',
            (self.session,)).fetchone()
        self.assertEqual(('pending', None), (status, key))
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s6_access_events').fetchone()[0])
        future = datetime.now(timezone.utc) + timedelta(days=31)
        provider_access.expire(self.store, future)
        self.assertEqual('disconnected', provider_access.view(
            self.store, self.session, SECRET, self.transport, now=future)['status'])

    def test_callback_absent_ou_refuse(self):
        with self.assertRaisesRegex(preparation.Denied, 'ACCESS_NO_PENDING'):
            provider_access.callback(self.store, self.session, SECRET, 'code', self.transport)
        provider_access.start(self.store, self.session, SECRET,
                              'https://example.test/preparation/access/callback')
        self.transport.exchange = lambda code, verifier: (400, b'{"key":"sk-or-reflected"}')
        with self.assertRaisesRegex(preparation.Denied, 'ACCESS_EXCHANGE_FAILED') as caught:
            provider_access.callback(self.store, self.session, SECRET, 'refused-code', self.transport)
        self.assertEqual(400, caught.exception.provider_status)
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s2_provider_access').fetchone()[0])
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s6_access_events').fetchone()[0])

    def test_routes_indisponibles_sans_extension_ou_secret(self):
        temporary = tempfile.TemporaryDirectory(prefix='provider-access-s5-')
        self.addCleanup(temporary.cleanup)
        other = Path(temporary.name).resolve() / 'private'
        session, _, _ = fixture(other)
        qualification.initialize(other)
        campaigns.initialize(other)
        evaluation.initialize(other)
        with closing(storage.Store(other)) as store, patch.object(
                preparation, 'session', return_value=(session, 'csrf', None)):
            code, value, _, _ = web_api.dispatch(
                store, 'GET', '/preparation/access', 'token', None, 'a' * 40, None,
                access_secret=SECRET)
            self.assertEqual((503, 'ACCESS_UNAVAILABLE'), (code, value['error_code']))
        with patch.object(preparation, 'session', return_value=(self.session, 'csrf', None)):
            code, value, _, _ = web_api.dispatch(
                self.store, 'GET', '/preparation/access', 'token', None, 'a' * 40, None)
        self.assertEqual(200, code)
        self.assertFalse(value['connected'])
        self.assertEqual('disconnected', value['status'])
        with patch.object(preparation, 'session', return_value=(self.session, 'csrf', None)):
            for path, body in (
                    ('/preparation/access/start', {'callback_url': 'https://example.test/preparation/access/callback'}),
                    ('/preparation/access/callback', {'code': 'code'})):
                code, value, _, _ = web_api.dispatch(
                    self.store, 'POST', path, 'token', (body if path.endswith('/callback') else
                                                       dict(body, csrf_token='csrf')), 'a' * 40, None)
                self.assertEqual((503, 'ACCESS_UNAVAILABLE'), (code, value['error_code']))
            code, value, _, _ = web_api.dispatch(self.store, 'POST', '/preparation/access/disconnect',
                'token', {'csrf_token': 'csrf'}, 'a' * 40, None)
            self.assertEqual((200, 'disconnected'), (code, value['status']))

    def test_secret_invalide_bloque_le_demarrage(self):
        socket_path = self.data.parent / 'executor.sock'
        with patch.dict('os.environ', {'BENCHMARK_ACCESS_SECRET': 'invalide'}, clear=True), \
                patch('benchmark.service.serve_executor') as serve, \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(78, runtime.main(['executor', '--data', str(self.data),
                                               '--socket', str(socket_path)]))
        serve.assert_not_called()
        self.assertNotIn('invalide', output.getvalue())
        self.assertNotIn('invalide', errors.getvalue())

    def test_funding_requester_refuse_avant_reservation(self):
        candidate = qualification.draft(
            self.store, 'fixture', self.prepared['revision'], specification(self.reference))
        qualified = qualification.qualify(
            self.store, candidate['contract_sha256'], reviewer=ACTOR, check=check)
        qualification.approve(self.store, candidate['contract_sha256'],
                              qualified['qualification_id'], actor=ACTOR, authority=AUTHORITY)
        self.store.create_budget('local-comparison', '40', 'TEST')
        value = dict(manifest(candidate), funding='requester')
        for configuration in value['panel']:
            configuration.update(access='API', channel_id=ENDPOINT, route=ENDPOINT,
                                 parameters={'provider': dict(only=['fixture'], order=['fixture'],
                                                              allow_fallbacks=False, require_parameters=True,
                                                              data_collection='deny')})
        campaigns.create(self.store, value)
        snapshot = campaigns.inspect(self.store, 'local-comparison')
        admission = campaigns.admit(self.store, 'local-comparison', *inputs(snapshot), owner_launch=True)
        body = {'manifest_version': snapshot['manifest']['version'],
                'frozen_at': snapshot['manifest']['conditions']['frozen_at'],
                'admission_id': admission['admission_id'], 'confirm': 'yes'}
        with self.assertRaisesRegex(preparation.Denied, 'ACCESS_REQUIRED'):
            campaigns.launch(self.store, self.session, 'fixture', 'local-comparison', body,
                             access_secret=SECRET, access_transport=self.transport)
        self.assertEqual([], campaigns.inspect(self.store, 'local-comparison')['attempts'])
        self.connect()
        attempts = campaigns.launch(self.store, self.session, 'fixture', 'local-comparison', body,
                                    access_secret=SECRET, access_transport=self.transport)
        supplied = []

        def factory(channel_id, api_key):
            supplied.append((channel_id, api_key))
            return response

        original = self.store._connection.execute(
            'SELECT key_cipher FROM s2_provider_access WHERE session_id=?', (self.session,)).fetchone()[0]
        for session, purpose in (('another-session', 'key'), (self.session, 'oauth')):
            cipher = provider_access.encrypt(SECRET, KEY, session, purpose)
            self.store._connection.execute('UPDATE s2_provider_access SET key_cipher=? WHERE session_id=?',
                                           (cipher, self.session))
            self.store._connection.commit()
            before = list(self.store._connection.iterdump())
            with self.assertRaisesRegex(preparation.Denied, '^ACCESS_UNAVAILABLE$'):
                execution.execute(self.data, attempts[0], transport_factory=factory,
                                  access_secret=SECRET, access_transport=self.transport)
            self.assertEqual([], supplied)
            self.assertEqual(before, list(self.store._connection.iterdump()))
        self.store._connection.execute('UPDATE s2_provider_access SET key_cipher=? WHERE session_id=?',
                                       (original, self.session))
        self.store._connection.commit()
        execution.execute_launch(self.data, attempts, transport_factory=factory,
                                 access_secret=SECRET, access_transport=self.transport)
        self.assertEqual([KEY, KEY], [key for _, key in supplied])


if __name__ == '__main__':
    unittest.main()
