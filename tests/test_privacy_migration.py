"""Real S6/S7 migrations and quarantined restores using synthetic credentials"""
from base64 import urlsafe_b64encode
from contextlib import closing
from datetime import timedelta
import hmac
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark import evaluation, preparation as p, privacy, privacy_archive as archive
from benchmark import provider_access as access, qualification, runtime, storage
from benchmark.acquisition import campaigns
from tests.test_privacy import NOW, SECRET
from tests.test_provider_access import AccessTransport, KEY
from tests.test_s3_regressions import fixture

OAUTH = 'synthetic-oauth-verifier-for-migration-only'
MIGRATION = 'test-legacy-migration'


def legacy_cipher(value, secret=SECRET):
    """Encode the historical HMAC/XOR format solely to seed an S6 fixture"""
    nonce = bytes(range(16))
    raw = value.encode('utf-8')
    stream = b''.join(hmac.digest(secret, nonce + counter.to_bytes(8, 'big'), 'sha256')
                      for counter in range((len(raw) + 31) // 32))
    cipher = bytes(a ^ b for a, b in zip(raw, stream))
    tag = hmac.digest(secret, b'access-v1' + nonce + cipher, 'sha256')
    return urlsafe_b64encode(nonce + cipher + tag).decode('ascii')


class PrivacyMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.data = self.home / 'private'
        self.now = NOW
        self.enterContext(patch.object(privacy, 'now', side_effect=lambda: self.now))
        self.enterContext(patch.object(access, '_now', side_effect=lambda: self.now))
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))
        self.journal = self.home / 'independent-journal' / 'revocations.jsonl'
        self.enterContext(patch.dict(os.environ, {'BENCHMARK_PRIVACY_JOURNAL': str(self.journal)}))
        session = p.session
        def capture_session(*args, **kwargs):
            result = session(*args, **kwargs)
            self.session_token = result[2]
            return result
        with patch.object(p, 'session', side_effect=capture_session):
            self.sid, self.preview, _ = fixture(self.data)
        qualification.initialize(self.data)
        campaigns.initialize(self.data)
        evaluation.initialize(self.data)
        access.initialize(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        p.close_admission(self.store)
        self.oauth_sid, _, self.oauth_token = p.session(self.store, None, create=True)
        self.seed_access(self.sid, KEY, 'key')
        self.seed_access(self.oauth_sid, OAUTH, 'oauth')
        self.assertEqual('s6', storage._check_schema(self.store._connection))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def seed_access(self, session_id, value, purpose, *, secret=SECRET):
        self.store._connection.execute('INSERT INTO s2_provider_access '
            '(session_id,verifier_cipher,key_cipher,created_at,verified_at,checked_at,status) VALUES (?,?,?,?,?,?,?)',
            (session_id, legacy_cipher(value, secret) if purpose == 'oauth' else None,
             legacy_cipher(value, secret) if purpose == 'key' else None, self.now.isoformat(),
             None if purpose == 'oauth' else self.now.isoformat(),
             None if purpose == 'oauth' else self.now.isoformat(),
             'pending' if purpose == 'oauth' else 'connected'))

    def key_cipher(self, store=None, session_id=None):
        row = (store or self.store)._connection.execute(
            'SELECT key_cipher FROM s2_provider_access WHERE session_id=?', (session_id or self.sid,)).fetchone()
        return None if row is None else row[0]

    def migrate(self, root=None, migration_id=MIGRATION):
        return privacy.migrate(root or self.data, SECRET, migration_id, now=self.now)

    def clocks(self, store=None):
        connection = (store or self.store)._connection
        return {table: connection.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                for table in ('s7_sessions', 's7_dossiers')}

    def assert_quarantined(self, root, store):
        self.assertEqual({'state': 'RESTORED_RECONCILIATION_REQUIRED'},
                         json.loads((root / 'restore.json').read_text()))
        status = runtime.status(root, store)
        self.assertTrue(status['restore_pending'])
        self.assertFalse(status['admission'])

    def test_legacy_key_and_oauth_migrate_without_changing_values_and_bind_all_aad(self):
        before = self.store._connection.execute(
            'SELECT session_id,created_at,verified_at,checked_at,status FROM s2_provider_access ORDER BY session_id').fetchall()
        self.assertEqual('READY', self.migrate()['phase'])
        self.assertEqual('s7', storage._check_schema(self.store._connection))
        self.assertEqual(before, self.store._connection.execute(
            'SELECT session_id,created_at,verified_at,checked_at,status FROM s2_provider_access ORDER BY session_id').fetchall())
        for sid, purpose, expected in ((self.sid, 'key', KEY), (self.oauth_sid, 'oauth', OAUTH)):
            column = 'key_cipher' if purpose == 'key' else 'verifier_cipher'
            cipher = self.store._connection.execute('SELECT ' + column + ' FROM s2_provider_access WHERE session_id=?', (sid,)).fetchone()[0]
            self.assertNotEqual(legacy_cipher(expected), cipher)
            self.assertEqual(expected, access.decrypt(SECRET, cipher, sid, purpose))
            wrong_sid = self.oauth_sid if sid == self.sid else self.sid
            wrong_purpose = 'oauth' if purpose == 'key' else 'key'
            parts = cipher.split(':')
            parts[1] = ('0' if parts[1][0] != '0' else '1') + parts[1][1:]
            for candidate, candidate_sid, candidate_purpose in (
                    (cipher, wrong_sid, purpose), (cipher, sid, wrong_purpose), (':'.join(parts), sid, purpose)):
                with self.subTest(purpose=purpose, sid_changed=candidate_sid != sid,
                                  purpose_changed=candidate_purpose != purpose, id_changed=candidate != cipher):
                    with self.assertRaises(storage.IntegrityError):
                        access.decrypt(SECRET, candidate, candidate_sid, candidate_purpose)
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_wrong_master_rolls_back_to_exact_s6_without_data_or_schema_loss(self):
        before = runtime.hashes(self.data)
        dump = list(self.store._connection.iterdump())
        with self.assertRaises(storage.IntegrityError):
            privacy.migrate(self.data, bytes.fromhex('22' * 32), MIGRATION, now=self.now)
        self.assertEqual('s6', privacy.migration_status(self.store)['layout'])
        self.assertEqual(dump, list(self.store._connection.iterdump()))
        self.assertEqual(before, runtime.hashes(self.data))
        self.assertFalse(privacy.available(self.store._connection))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        self.assertEqual('READY', self.migrate()['phase'])
        self.assertEqual(KEY, access.decrypt(SECRET, self.key_cipher(), self.sid, 'key'))

    def test_interruption_after_commit_closes_reads_and_same_identity_resumes_without_renewal(self):
        def power_loss(store):
            self.assertFalse(store._connection.in_transaction)
            self.assertEqual('MIGRATED', privacy.migration_status(store)['phase'])
            raise OSError('simulated interruption before VACUUM')

        with patch.object(privacy, 'finish_migration', side_effect=power_loss):
            with self.assertRaisesRegex(OSError, 'before VACUUM'):
                self.migrate()
        with closing(storage.Store(self.data)) as reopened:
            status = privacy.migration_status(reopened)
            self.assertEqual('MIGRATED', status['phase'])
            clocks = self.clocks(reopened)
            cipher = self.key_cipher(reopened)
            for read in (lambda: p.session(reopened, self.oauth_token),
                         lambda: p.view(reopened, self.sid, 'fixture'),
                         lambda: access.view(reopened, self.sid, SECRET, refresh=False)):
                with self.assertRaisesRegex(p.Denied, 'PRIVACY_MIGRATION_PENDING'):
                    read()
            with self.assertRaises(storage.ConflictError):
                self.migrate(migration_id='different-migration')
            self.assertEqual(status, privacy.migration_status(reopened))
        self.now += timedelta(days=1)
        resumed = self.migrate()
        self.assertEqual(dict(status, phase='READY'), resumed)
        self.assertEqual(clocks, self.clocks())
        self.assertEqual(cipher, self.key_cipher())
        self.assertEqual('fixture', p.view(self.store, self.sid, 'fixture')['dossier_id'])
        self.assertEqual(self.oauth_sid, p.session(self.store, self.oauth_token)[0])
        self.assertEqual(resumed, self.migrate())
        self.assertEqual(clocks, self.clocks())
        self.assertTrue(self.store.verify_storage()['integrity_ok'])


    def test_backup_before_key_and_contribution_withdrawal_restores_quarantined_and_replays_external_journal(self):
        self.migrate()
        value, token = archive.change_contribution(self.store, self.session_token, 'fixture',
            dict(example_revision=self.preview['revision'], revision=0, enabled=True), now=self.now)
        identity = value['contribution']['id']
        csrf = archive.manager_view(self.store, token, now=self.now)['csrf_token']
        saved = self.home / 'backup-before-withdrawal'
        self.assertEqual('BACKUP_VERIFIED', runtime.backup(self.data, saved)['state'])
        before = runtime.hashes(saved)
        self.now += timedelta(minutes=1)
        access.disconnect(self.store, self.sid, SECRET)
        archive.withdraw(self.store, token, identity, csrf, now=self.now)
        journal = self.journal.read_bytes()
        events = [json.loads(line) for line in journal.splitlines()]
        self.assertEqual({'key', 'contribution'}, {event['kind'] for event in events})
        self.assertFalse(self.journal.is_relative_to(saved))
        self.assertIsNone(self.key_cipher())
        self.assertEqual('withdrawn', archive.manager_view(self.store, token, now=self.now)['contributions'][0]['status'])
        target = self.home / 'restored-before-withdrawal'
        self.assertEqual('RESTORED_ADMISSION_BLOCKED', runtime.restore(saved, target)['state'])
        with closing(storage.Store(target)) as restored:
            self.assert_quarantined(target, restored)
            self.assertEqual(privacy.journal_path(self.store), privacy.journal_path(restored))
            self.assertEqual(0, restored._connection.execute('SELECT count(*) FROM s7_revocations').fetchone()[0])
            # Snapshot contents are inspected only while admission remains closed
            self.assertIsNotNone(self.key_cipher(restored))
            self.assertEqual('active', restored._connection.execute(
                'SELECT status FROM s7_contributions WHERE contribution_id=?', (identity,)).fetchone()[0])
            privacy.replay_revocations(restored)
            self.assertIsNone(self.key_cipher(restored))
            self.assertFalse(access.view(restored, self.sid, SECRET, refresh=False)['connected'])
            expected = restored._connection.execute(
                'SELECT status,revision,payload_json FROM s7_contributions WHERE contribution_id=?', (identity,)).fetchone()
            self.assertEqual(('withdrawn', 2, None), expected)
            self.assertEqual('withdrawn', archive.manager_view(restored, token, now=self.now)['contributions'][0]['status'])
            self.assertEqual({event['event_id'] for event in events},
                {row[0] for row in restored._connection.execute('SELECT event_id FROM s7_revocations')})
            privacy.replay_revocations(restored)
            archive.refresh_contributions(restored, 'fixture')
            self.assertEqual(expected, restored._connection.execute(
                'SELECT status,revision,payload_json FROM s7_contributions WHERE contribution_id=?', (identity,)).fetchone())
            self.assert_quarantined(target, restored)
            self.assertTrue(restored.verify_storage()['integrity_ok'])
        self.assertEqual(journal, self.journal.read_bytes())
        self.assertEqual(before, runtime.hashes(saved))

    def test_replay_uses_session_and_creation_cutoff_not_legacy_reencryption_identity(self):
        other_sid, _, _ = p.session(self.store, None, create=True)
        self.seed_access(other_sid, KEY, 'key')
        saved = self.home / 'legacy-backup'
        self.assertEqual('BACKUP_VERIFIED', runtime.backup(self.data, saved)['state'])
        self.migrate()
        old_cipher = self.key_cipher()
        access.disconnect(self.store, self.sid, SECRET)
        event = json.loads(self.journal.read_text())
        self.assertEqual({'event_id', 'kind', 'identifier', 'at'}, set(event))
        self.assertEqual(('key', self.sid, NOW.isoformat()), (event['kind'], event['identifier'], event['at']))
        self.now += timedelta(seconds=1)
        target = self.home / 'restored-legacy'
        self.assertEqual('RESTORED_ADMISSION_BLOCKED', runtime.restore(saved, target)['state'])
        self.migrate(target, 'restored-legacy-migration')
        with closing(storage.Store(target)) as restored:
            self.assert_quarantined(target, restored)
            migrated = self.key_cipher(restored)
            self.assertNotEqual(old_cipher, migrated)
            self.assertEqual(KEY, access.decrypt(SECRET, migrated, self.sid, 'key'))
            self.assertEqual(NOW.isoformat(), restored._connection.execute(
                'SELECT created_at FROM s2_provider_access WHERE session_id=?', (self.sid,)).fetchone()[0])
            privacy.replay_revocations(restored)
            self.assertIsNone(self.key_cipher(restored))
            self.assertEqual(KEY, access.decrypt(SECRET, self.key_cipher(restored, other_sid), other_sid, 'key'))
            self.assertTrue(restored.verify_storage()['integrity_ok'])
            self.assert_quarantined(target, restored)
        # A fresh restore has no applied marker: the timestamp comparison must run again
        newer_target = self.home / 'restored-with-newer-key'
        runtime.restore(saved, newer_target)
        self.migrate(newer_target, 'newer-key-migration')
        self.now += timedelta(seconds=1)
        with closing(storage.Store(newer_target)) as restored:
            self.assertEqual(0, restored._connection.execute('SELECT count(*) FROM s7_revocations').fetchone()[0])
            access.import_key(restored, self.sid, SECRET, KEY, transport=AccessTransport())
            new_cipher = self.key_cipher(restored)
            created = restored._connection.execute(
                'SELECT created_at FROM s2_provider_access WHERE session_id=?', (self.sid,)).fetchone()[0]
            self.assertGreater(privacy.date(created), privacy.date(event['at']))
            privacy.replay_revocations(restored)
            self.assertEqual(new_cipher, self.key_cipher(restored))
            self.assertEqual(KEY, access.decrypt(SECRET, new_cipher, self.sid, 'key'))
            self.assertEqual([(event['event_id'], event['at'])],
                             restored._connection.execute('SELECT * FROM s7_revocations').fetchall())
            self.assertTrue(restored.verify_storage()['integrity_ok'])
            self.assert_quarantined(newer_target, restored)

    def test_missing_independent_journal_with_applied_markers_fails_closed(self):
        self.migrate()
        access.disconnect(self.store, self.sid, SECRET)
        markers = self.store._connection.execute('SELECT * FROM s7_revocations').fetchall()
        self.assertEqual(1, len(markers))
        missing = self.home / 'missing-journal' / 'revocations.jsonl'
        with patch.dict(os.environ, {'BENCHMARK_PRIVACY_JOURNAL': str(missing)}):
            with self.assertRaisesRegex(storage.IntegrityError, 'Journal de révocation absent'):
                privacy.replay_revocations(self.store)
        self.assertFalse(missing.exists())
        self.assertIsNone(self.key_cipher())
        self.assertEqual(markers, self.store._connection.execute('SELECT * FROM s7_revocations').fetchall())


if __name__ == '__main__':
    unittest.main()
