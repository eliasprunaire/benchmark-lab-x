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

from benchmark import campaigns, evaluation, preparation, provider_access, qualification, runtime, storage
from benchmark.openrouter_preparation import ENDPOINT
from tests.test_s3_regressions import ACTOR, AUTHORITY, check, fixture, specification
from tests.test_s4_regressions import inputs, manifest, response


SECRET = bytes.fromhex('11' * 32)
KEY = 'sk-or-fixture-secret'


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
        verifier = provider_access.decrypt(SECRET, cipher)
        self.assertEqual(43, len(verifier))
        self.assertEqual([provider_access.challenge(verifier)], query['code_challenge'])
        self.assertEqual(['S256'], query['code_challenge_method'])
        self.assertNotIn(verifier, result['authorize_url'])
        self.assertEqual(verifier, provider_access.decrypt(SECRET, provider_access.encrypt(SECRET, verifier)))
        damaged = provider_access.encrypt(SECRET, verifier)[:-2] + 'AA'
        with self.assertRaises(storage.IntegrityError):
            provider_access.decrypt(SECRET, damaged)

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
                self.assertEqual(KEY, provider_access.decrypt(SECRET, key_cipher))
                self.assertEqual('KEY_REJECTED', reason)
                verification_count = len(self.transport.verifications)
                self.assertEqual('invalid', provider_access.view(
                    self.store, self.session, SECRET, self.transport)['status'])
                self.assertEqual(verification_count, len(self.transport.verifications))
                with self.assertRaisesRegex(preparation.Denied, 'ACCESS_REQUIRED'):
                    provider_access.key_for_session(self.store, self.session, SECRET, self.transport)
        self.assertNotEqual(verified_at, old)

    def test_secret_change_efface_acces_et_evenements(self):
        self.connect()
        changed_secret = bytes.fromhex('22' * 32)
        value = provider_access.view(self.store, self.session, changed_secret, self.transport)
        self.assertEqual(('disconnected', 'SECRET_CHANGED'), (value['status'], value['reason']))
        self.assertEqual(0, self.store._connection.execute(
            'SELECT count(*) FROM s2_provider_access').fetchone()[0])
        self.assertEqual(0, self.store._connection.execute(
            'SELECT count(*) FROM s6_access_events').fetchone()[0])

    def test_callback_expire_avant_echange(self):
        old = datetime.now(timezone.utc) - timedelta(days=31)
        provider_access.start(self.store, self.session, SECRET,
                              'https://example.test/preparation/access/callback', now=old)
        with self.assertRaisesRegex(preparation.Denied, 'ACCESS_NO_PENDING'):
            provider_access.callback(self.store, self.session, SECRET, 'code', self.transport)
        self.assertEqual([], self.transport.exchanges)

    def test_callback_secret_change_efface_pending(self):
        provider_access.start(self.store, self.session, SECRET,
                              'https://example.test/preparation/access/callback')
        with self.assertRaisesRegex(preparation.Denied, '^ACCESS_NO_PENDING$') as caught:
            provider_access.callback(self.store, self.session, bytes.fromhex('22' * 32),
                                     'code', self.transport)
        self.assertEqual('ACCESS_NO_PENDING', caught.exception.code)
        self.assertEqual([], self.transport.exchanges)
        self.assertEqual(0, self.store._connection.execute(
            'SELECT count(*) FROM s2_provider_access').fetchone()[0])

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
            code, value, _, _ = preparation.dispatch(
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
            code, value, _, _ = preparation.dispatch(
                store, 'GET', '/preparation/access', 'token', None, 'a' * 40, None,
                access_secret=SECRET)
            self.assertEqual((503, 'ACCESS_UNAVAILABLE'), (code, value['error_code']))
        with patch.object(preparation, 'session', return_value=(self.session, 'csrf', None)):
            code, value, _, _ = preparation.dispatch(
                self.store, 'GET', '/preparation/access', 'token', None, 'a' * 40, None)
        self.assertEqual(503, code)
        self.assertEqual({'connected': False, 'status': 'unavailable',
                          'error_code': 'ACCESS_UNAVAILABLE'}, value)
        with patch.object(preparation, 'session', return_value=(self.session, 'csrf', None)):
            for path, body in (
                    ('/preparation/access/start', {'callback_url': 'https://example.test/preparation/access/callback'}),
                    ('/preparation/access/callback', {'code': 'code'}),
                    ('/preparation/access/disconnect', {})):
                code, value, _, _ = preparation.dispatch(
                    self.store, 'POST', path, 'token', (body if path.endswith('/callback') else
                                                       dict(body, csrf_token='csrf')), 'a' * 40, None)
                self.assertEqual((503, 'ACCESS_UNAVAILABLE'), (code, value['error_code']))

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

        campaigns.execute_launch(self.data, attempts, transport_factory=factory,
                                 access_secret=SECRET, access_transport=self.transport)
        self.assertEqual([KEY, KEY], [key for _, key in supplied])


if __name__ == '__main__':
    unittest.main()
