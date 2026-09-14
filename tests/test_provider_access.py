"""Accès OpenRouter délégué sans appel réseau réel"""
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
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

    def test_pkce_chiffrement_et_vue_expurgee(self):
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
        connected = self.connect()
        self.assertEqual({'connected': True, 'limit_usd': '20', 'limit_remaining_usd': '18.5',
                          'is_free_tier': False, 'status': 'connected'},
                         {key: connected[key] for key in connected if key != 'verified_at'})
        verified_at = connected['verified_at']
        old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        self.store._connection.execute('UPDATE s2_provider_access SET verified_at=? WHERE session_id=?',
                                       (old, self.session))
        self.store._connection.commit()
        self.transport.verify_result = OSError('panne transitoire')
        transient = provider_access.view(self.store, self.session, SECRET, self.transport)
        self.assertEqual('connected', transient['status'])
        self.assertEqual(old, transient['verified_at'])
        self.transport.verify_result = (401, b'{"error":"rejected"}')
        rejected = provider_access.view(self.store, self.session, SECRET, self.transport)
        self.assertEqual('invalid', rejected['status'])
        self.assertFalse(rejected['connected'])
        key_cipher, reason = self.store._connection.execute(
            'SELECT key_cipher,status_reason FROM s2_provider_access WHERE session_id=?',
            (self.session,)).fetchone()
        self.assertEqual(KEY, provider_access.decrypt(SECRET, key_cipher))
        self.assertEqual('KEY_REJECTED', reason)
        self.assertNotEqual(verified_at, old)

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
        self.assertEqual({'connected': False, 'status': 'unavailable'}, value)
        with patch.object(preparation, 'session', return_value=(self.session, 'csrf', None)):
            for path, body in (
                    ('/preparation/access/start', {'callback_url': 'https://example.test/preparation/access/callback'}),
                    ('/preparation/access/callback', {'code': 'code'}),
                    ('/preparation/access/disconnect', {})):
                code, value, _, _ = preparation.dispatch(
                    self.store, 'POST', path, 'token', dict(body, csrf_token='csrf'), 'a' * 40, None)
                self.assertEqual((503, 'ACCESS_UNAVAILABLE'), (code, value['error_code']))

    def test_secret_invalide_bloque_le_demarrage(self):
        socket_path = self.data.parent / 'executor.sock'
        with patch.dict('os.environ', {'BENCHMARK_ACCESS_SECRET': 'invalide'}, clear=True), \
                patch('benchmark.service.serve_executor') as serve, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(78, runtime.main(['executor', '--data', str(self.data),
                                               '--socket', str(socket_path)]))
        serve.assert_not_called()
        self.assertNotIn('invalide', output.getvalue())

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
            configuration.update(access='API', channel_id=ENDPOINT, route=ENDPOINT)
        campaigns.create(self.store, value)
        snapshot = campaigns.inspect(self.store, 'local-comparison')
        admission = campaigns.admit(self.store, 'local-comparison', *inputs(snapshot), owner_launch=True)
        body = {'manifest_sha256': snapshot['manifest_sha256'],
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
