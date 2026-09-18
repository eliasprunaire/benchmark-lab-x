"""Owner archives and independent contributions, on real S7 with synthetic source data"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from benchmark import preparation as p, privacy, provider_access, storage
from benchmark.acquisition import campaigns as c, execution
from tests.test_s4_regressions import inputs, response
from tests.test_s2_review_regressions import response_for
from benchmark import privacy_archive as archive
from tests.test_s6_regressions import build


class PrivacyArchiveTests(unittest.TestCase):
    def test_configuration_projection_preserves_effective_settings_without_secrets(self):
        configuration = {'model': 'fictional', 'route': 'openrouter', 'channel_id': 'api',
            'parameters': {'temperature': 0.4, 'max_tokens': 500, 'reasoning': {'effort': 'high'},
                           'api_key': 'PRIVATE'}, 'authorization': 'PRIVATE'}
        value = json.loads(archive._configuration('Demandée', configuration)['text'])
        self.assertEqual({'model': 'fictional', 'route': 'openrouter', 'channel_id': 'api',
            'parameters': {'temperature': 0.4, 'max_tokens': 500, 'reasoning': {'effort': 'high'}}}, value)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name).resolve() / 'private'
        self.now = datetime(2026, 8, 31, 12, 30, tzinfo=timezone.utc)
        self.enterContext(patch.object(privacy, 'now', side_effect=lambda: self.now))
        self.enterContext(patch.dict(os.environ, {
            'BENCHMARK_PRIVACY_JOURNAL': str(self.data.parent / 'privacy-revocations' / 'revocations.jsonl')}))
        self.network = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.store, self.sid, self.session_token, _, _ = build(self.data)
        self.addCleanup(self.store.close)
        provider_access.initialize(self.data)
        privacy.migrate(self.data, bytes.fromhex('11' * 32), 'privacy-archive-tests', now=self.now)
        self.connection = self.store._connection_checked()
        self.assertEqual('s7', privacy.migration_status(self.store)['layout'])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        journal_intent = privacy.journal_intent

        def record_intent(store, kind, identity, now=None):
            self.assertFalse(store._connection.in_transaction)
            return journal_intent(store, kind, identity, now=now)

        self.enterContext(patch.object(privacy, 'journal_intent', side_effect=record_intent))

    @property
    def events(self):
        path = privacy.journal_path(self.store)
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    @property
    def journal(self):
        return [(event['kind'], event['identifier']) for event in self.events]

    def advance_to(self, current):
        current = current.astimezone(timezone.utc)
        self.assertGreaterEqual(current, self.now)
        while self.now < current:
            self.now = min(current, self.now + privacy.DOSSIER_LIFETIME - timedelta(seconds=1))
            privacy.activity(self.store, self.sid, 'fixture')

    def record(self, manifest):
        parts = []
        part = 0
        while True:
            item = archive.archive_item(self.store, self.sid, 'fixture', manifest['snapshot_id'], part)
            self.assertEqual({'snapshot_id', 'item_id', 'part', 'total_parts', 'hex'}, set(item))
            raw = bytes.fromhex(item['hex'])
            self.assertLessEqual(len(raw), 1024 * 1024)
            parts.append(raw)
            part += 1
            if part == item['total_parts']:
                break
        raw = b''.join(parts)
        self.assertEqual(manifest['items'][0]['length'], len(raw))
        self.assertEqual(manifest['items'][0]['sha256'], sha256(raw).hexdigest())
        return json.loads(raw)

    def test_owner_archive_is_frozen_and_excludes_judge_and_operational_data(self):
        manifest = archive.archive_manifest(self.store, self.sid, 'fixture')
        self.assertEqual('bench-x/archive/v1', manifest['format'])
        record = self.record(manifest)
        self.assertEqual({'format', 'dossier_id', 'content_version', 'need', 'messages',
                          'revisions', 'campaigns'}, set(record))
        self.assertEqual('Organiser des notes fictives', record['need'])
        self.assertEqual('Action : relire', record['revisions'][-1]['pieces'][0]['text'])
        model = record['campaigns'][0]['models'][0]
        self.assertEqual('NE SATISFAIT PAS', model['verdict'])
        self.assertIn('source error', model['answer'])
        raw = json.dumps(record)
        for forbidden in ('Attendu fictif réservé', 'PRIVATE_UNSELECTED', self.session_token,
                          'requested_configuration', 'operation_provenance', 'authority_id', 'receipt'):
            self.assertNotIn(forbidden, raw)
        with patch.object(p, 'view', side_effect=AssertionError('Snapshot must be persisted')):
            self.assertEqual(manifest, archive.archive_manifest(self.store, self.sid, 'fixture'))
            self.assertEqual(record, self.record(manifest))
        self.connection.execute('UPDATE s7_dossiers SET content_version=2')
        newer = archive.archive_manifest(self.store, self.sid, 'fixture')
        self.assertNotEqual(manifest['snapshot_id'], newer['snapshot_id'])
        self.assertEqual(record, self.record(manifest))
        self.assertEqual(2, self.record(newer)['content_version'])
        with self.assertRaises(p.Denied):
            archive.archive_manifest(self.store, 'foreign', 'fixture')
        self.now += privacy.DOSSIER_LIFETIME
        with self.assertRaises(privacy.Gone):
            self.record(manifest)


    def consent(self, revision=0, **changes):
        body = dict(example_revision=p.view(self.store, self.sid, 'fixture')['revision'],
                    revision=revision, enabled=True)
        body.update(changes)
        return body

    def test_explicit_consent_creates_an_independent_copy_and_hashed_manager(self):
        dossier = 'copy-source'
        budget = provider_access.preparation_budget_id(self.sid)
        self.store.create_budget(budget, '100', 'TEST')
        p.admit(self.store, dict(authority_id='TEST_ONLY_COPY', budget_id=budget,
            reserve_amount='7', requested_configuration={'model': 'fictional'}))
        operation, _ = p.submit(self.store, self.sid, dossier,
            dict(action_id='copy-create', request='Organiser des notes fictives'), 'a' * 40, True)
        p.execute(self.data, operation, lambda op, request: response_for(op))
        body = dict(example_revision=p.view(self.store, self.sid, dossier)['revision'], revision=0, enabled=True)
        value, token = archive.change_contribution(self.store, self.session_token, dossier, body, now=self.now)
        contribution = value['contribution']
        self.assertEqual('active', contribution['status'])
        self.assertEqual(value, archive.contribution_view(self.store, self.sid, dossier, now=self.now))
        self.assertEqual(1, contribution['revision'])
        self.assertEqual('2027-02-28T12:30:00+00:00', contribution['expires_at'])
        self.assertEqual({'kind', 'contribution'}, set(value))
        self.assertNotIn(token, json.dumps(value))
        manager = archive.manager_view(self.store, token, now=self.now)
        self.assertEqual({'kind', 'csrf_token', 'contributions'}, set(manager))
        self.assertEqual('contributions', manager['kind'])
        self.assertEqual({'id', 'created_at', 'expires_at', 'status'}, set(manager['contributions'][0]))
        stored = json.loads(self.connection.execute('SELECT payload_json FROM s7_contributions').fetchone()[0])
        self.assertEqual({'format', 'example', 'campaigns'}, set(stored))
        self.assertEqual('bench-x/contribution/v1', stored['format'])
        self.assertIn('Action : relire', json.dumps(stored, ensure_ascii=False))
        for forbidden in ('Organiser des notes fictives', 'Attendu fictif réservé', 'PRIVATE_UNSELECTED',
                          'need', 'messages', 'clarifications', 'request', 'session_id', token):
            self.assertNotIn(forbidden, json.dumps(stored, ensure_ascii=False))
        self.assertNotIn(token, str(self.connection.execute('SELECT * FROM s7_contribution_managers').fetchall()))
        self.now += privacy.DOSSIER_LIFETIME
        with self.assertRaises(privacy.Gone):
            p.view(self.store, self.sid, dossier)
        self.assertEqual(manager, archive.manager_view(self.store, token, now=self.now))
        self.assertIn(dossier, privacy.purge(self.data)['purged'])
        self.assertIsNone(self.connection.execute('SELECT 1 FROM s2_dossiers WHERE dossier_id=?', (dossier,)).fetchone())
        self.assertIsNone(self.connection.execute('SELECT 1 FROM pieces WHERE dossier_id=?', (dossier,)).fetchone())
        self.assertEqual(manager, archive.manager_view(self.store, token, now=self.now))
        archive.verify(self.store, self.connection)
        with self.assertRaises(p.Denied):
            archive.manager_view(self.store, '0' * 64, now=self.now)


    def test_updates_use_cas_without_renewing_expiry_and_require_current_example(self):
        body = self.consent()
        value, token = archive.change_contribution(self.store, self.session_token, 'fixture', body, now=self.now)
        expiry = value['contribution']['expires_at']
        later = datetime(2026, 9, 30, tzinfo=timezone.utc)
        self.advance_to(later)
        body['revision'] = 1
        updated, new_token = archive.change_contribution(self.store, self.session_token, 'fixture', body,
                                                         manager_token=token, now=later)
        self.assertIsNone(new_token)
        self.assertEqual(2, updated['contribution']['revision'])
        self.assertEqual(expiry, updated['contribution']['expires_at'])
        with self.assertRaises(storage.ConflictError):
            archive.change_contribution(self.store, self.session_token, 'fixture', body, now=later)
        body.update(revision=2, example_revision=body['example_revision'] + 1)
        with self.assertRaises(storage.ConflictError):
            archive.change_contribution(self.store, self.session_token, 'fixture', body, now=later)
        body['example_revision'] -= 1
        self.assertEqual(expiry, archive.manager_view(self.store, token, now=self.now)['contributions'][0]['expires_at'])
        self.advance_to(datetime(2027, 2, 28, 12, 30, tzinfo=timezone.utc))
        with self.assertRaisesRegex(p.Denied, 'Contribution retirée ou expirée'):
            archive.change_contribution(self.store, self.session_token, 'fixture', body, now=self.now)

    def test_withdrawal_requires_scoped_token_and_csrf_and_precedes_database_mutation(self):
        body = self.consent()
        value, token = archive.change_contribution(self.store, self.session_token, 'fixture', body, now=self.now)
        identity = value['contribution']['id']
        csrf = archive.manager_view(self.store, token, now=self.now)['csrf_token']
        for bad_token, bad_csrf, bad_id in ((token, 'bad', identity), ('0' * 64, csrf, identity),
                                          (token, csrf, 'foreign')):
            with self.subTest(token=bad_token == token, csrf=bad_csrf == csrf, identity=bad_id == identity):
                with self.assertRaises(p.Denied):
                    archive.withdraw(self.store, bad_token, bad_id, bad_csrf, now=self.now)
        self.assertEqual([], self.journal)
        import benchmark.privacy as privacy
        with patch.object(privacy, 'journal_intent', side_effect=OSError('journal unavailable')):
            with self.assertRaises(OSError):
                archive.withdraw(self.store, token, identity, csrf, now=self.now)
        self.assertEqual('active', archive.manager_view(self.store, token, now=self.now)['contributions'][0]['status'])
        result = archive.withdraw(self.store, token, identity, csrf, now=self.now)
        self.assertEqual('withdrawn', result['contributions'][0]['status'])
        self.assertEqual([('contribution', identity)], self.journal)
        self.assertEqual([(self.events[0]['event_id'], self.events[0]['at'])],
                         self.connection.execute('SELECT * FROM s7_revocations').fetchall())
        self.assertIsNone(self.connection.execute('SELECT payload_json FROM s7_contributions').fetchone()[0])
        self.assertEqual(result, archive.withdraw(self.store, token, identity, csrf, now=self.now))
        with self.assertRaises(storage.ConflictError):
            archive.change_contribution(self.store, self.session_token, 'fixture', dict(body, revision=1), now=self.now)
        archive.refresh_contributions(self.store, 'fixture')
        self.assertIsNone(self.connection.execute('SELECT payload_json FROM s7_contributions').fetchone()[0])


    def test_expiration_clears_payload_and_user_deletion_purges_copies(self):
        value, token = archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        self.advance_to(datetime(2027, 2, 28, 12, 30, tzinfo=timezone.utc))
        with storage._transaction(self.connection, write=True):
            archive.expire_contributions(self.connection, now=self.now)
        self.assertEqual(('expired', None), self.connection.execute(
            'SELECT status,payload_json FROM s7_contributions').fetchone())
        with self.assertRaises(p.Denied):
            archive.manager_view(self.store, token, now=datetime(2027, 2, 28, 12, 30, tzinfo=timezone.utc))
        self.assertEqual('delete_requested', privacy.request_delete(self.store, self.sid, 'fixture')['status'])
        self.assertEqual(0, self.connection.execute('SELECT count(*) FROM s7_contributions').fetchone()[0])

    def test_refresh_never_infers_consent_and_stops_on_changed_example(self):
        archive.refresh_contributions(self.store, 'fixture')
        self.assertEqual(0, self.connection.execute('SELECT count(*) FROM s7_contributions').fetchone()[0])
        value, token = archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        self.connection.execute('UPDATE s7_dossiers SET content_version=2')
        archive.refresh_contributions(self.store, 'fixture')
        row = self.connection.execute('SELECT revision,content_version,expires_at,payload_json FROM s7_contributions').fetchone()
        self.assertEqual((2, 2, value['contribution']['expires_at']), row[:3])
        archive.refresh_contributions(self.store, 'fixture')
        self.assertEqual(row, self.connection.execute('SELECT revision,content_version,expires_at,payload_json FROM s7_contributions').fetchone())
        view = p.view(self.store, self.sid, 'fixture')
        p.admit(self.store, dict(authority_id='TEST_ONLY_REVISION', budget_id='fictional',
            reserve_amount='7', requested_configuration={'model': 'fictional'}))
        operation, _ = p.submit(self.store, self.sid, 'fixture', dict(action_id='new-example',
            revision=view['revision'], kind='correct', message='Préciser la consigne'), 'a' * 40, True)
        p.execute(self.data, operation, lambda op, request: response_for(op))
        self.assertEqual(view['revision'] + 1, p.view(self.store, self.sid, 'fixture')['revision'])
        archive.refresh_contributions(self.store, 'fixture')
        self.assertEqual(row, self.connection.execute('SELECT revision,content_version,expires_at,payload_json FROM s7_contributions').fetchone())

    def test_owner_can_remove_consent_and_missing_consent_never_creates_a_copy(self):
        body = self.consent(enabled=False)
        self.assertEqual(({'kind': 'contribution', 'contribution': None}, None),
            archive.change_contribution(self.store, self.session_token, 'fixture', body, now=self.now))
        for invalid in ({}, dict(body, enabled='true'), dict(body, revision=True),
                        dict(body, legacy_enabled=True)):
            with self.assertRaises(ValueError):
                archive.change_contribution(self.store, self.session_token, 'fixture', invalid, now=self.now)
        value, token = archive.change_contribution(self.store, self.session_token, 'fixture', dict(body, enabled=True), now=self.now)
        value, new_token = archive.change_contribution(self.store, self.session_token, 'fixture', dict(body, revision=1), now=self.now)
        self.assertEqual('withdrawn', value['contribution']['status'])
        self.assertIsNone(new_token)
        self.assertEqual([('contribution', value['contribution']['id'])], self.journal)
        self.assertEqual([(self.events[0]['event_id'], self.events[0]['at'])],
                         self.connection.execute('SELECT * FROM s7_revocations').fetchall())


    def test_storage_verifier_checks_exact_schema_and_closed_payloads(self):
        manifest = archive.archive_manifest(self.store, self.sid, 'fixture')
        archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        archive.verify(self.store, self.connection)
        self.connection.execute('DROP INDEX s7_contributions_expiry')
        with self.assertRaises(storage.SchemaError):
            archive.verify(self.store, self.connection)
        self.connection.execute('CREATE INDEX s7_contributions_expiry ON s7_contributions(status, expires_at)')
        original = self.connection.execute('SELECT payload_json FROM s7_contributions').fetchone()[0]
        value = json.loads(original)
        value['need'] = 'Private synthetic input'
        self.connection.execute('UPDATE s7_contributions SET payload_json=?', (json.dumps(value),))
        with self.assertRaises(storage.IntegrityError):
            archive.verify(self.store, self.connection)
        self.connection.execute('UPDATE s7_contributions SET payload_json=?', (original,))
        archive.verify(self.store, self.connection)
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("UPDATE s7_archives SET record=x'00'")
        snapshot = self.connection.execute('SELECT * FROM s7_archives').fetchone()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute('INSERT OR REPLACE INTO s7_archives VALUES (?, ?, ?, ?, ?)', snapshot)


    def test_large_record_is_streamed_without_a_total_size_cap_and_rejects_bad_parts(self):
        original = p.view
        large = 'z' * (17 * 1024 * 1024 + 13)

        def enlarged(*args, **kwargs):
            value = original(*args, **kwargs)
            if value['package']:
                identity = value['package']['pieces'][0]['id']
                value['example_contents'][identity] = large
            return value

        with patch.object(p, 'view', side_effect=enlarged):
            manifest = archive.archive_manifest(self.store, self.sid, 'fixture')
        self.assertGreater(manifest['items'][0]['length'], 17 * 1024 * 1024)
        self.assertEqual(large, self.record(manifest)['revisions'][-1]['pieces'][0]['text'])
        for part in (-1, True, '0', 10**100):
            with self.subTest(part=part), self.assertRaises(ValueError):
                archive.archive_item(self.store, self.sid, 'fixture', manifest['snapshot_id'], part)
        with self.assertRaises(p.Denied):
            archive.archive_item(self.store, self.sid, 'fixture', 'foreign', 0)

    def test_contribution_rejects_obvious_personal_data_and_secrets_in_allowed_text(self):
        original = p.view
        for sensitive in ('Nom : Jean Dupont', 'Prénom: Jean', 'contact@example.test', '+33 6 12 34 56 78',
                          'FR76 3000 6000 0112 3456 7890 189', '1 84 12 76 451 089 46', 'sk-or-v1-synthetic_private_key', 'api_key=synthetic-secret'):
            def contaminated(*args, **kwargs):
                value = original(*args, **kwargs)
                if value['package']:
                    identity = value['package']['pieces'][0]['id']
                    value['example_contents'][identity] += '\n' + sensitive
                return value
            with self.subTest(text=sensitive), patch.object(p, 'view', side_effect=contaminated):
                with self.assertRaisesRegex(p.Denied, 'CONTRIBUTION_SENSITIVE_DATA'):
                    archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        self.assertEqual(0, self.connection.execute('SELECT count(*) FROM s7_contributions').fetchone()[0])

    def test_concurrent_content_change_or_withdrawal_cannot_commit_a_stale_copy(self):
        original = p.view

        def change_version(*args, **kwargs):
            value = original(*args, **kwargs)
            self.connection.execute('UPDATE s7_dossiers SET content_version=content_version+1')
            return value

        with patch.object(p, 'view', side_effect=change_version), self.assertRaises(storage.ConflictError):
            archive.archive_manifest(self.store, self.sid, 'fixture')
        self.assertEqual(0, self.connection.execute('SELECT count(*) FROM s7_archives').fetchone()[0])
        value, token = archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        csrf = archive.manager_view(self.store, token, now=self.now)['csrf_token']
        self.connection.execute('UPDATE s7_dossiers SET content_version=content_version+1')

        def retire(*args, **kwargs):
            value_for_owner = original(*args, **kwargs)
            archive.withdraw(self.store, token, value['contribution']['id'], csrf, now=self.now)
            return value_for_owner

        with patch.object(p, 'view', side_effect=retire), self.assertRaises(p.Denied):
            archive.refresh_contributions(self.store, 'fixture')
        self.assertEqual(('withdrawn', None), self.connection.execute('SELECT status,payload_json FROM s7_contributions').fetchone())


    def test_expiry_during_projection_refuses_a_late_payload_update(self):
        archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        self.advance_to(datetime(2027, 2, 28, 12, 29, 59, tzinfo=timezone.utc))
        body = self.consent(revision=1)
        original = p.view

        def expires_during_read(*args, **kwargs):
            value = original(*args, **kwargs)
            self.now = datetime(2027, 2, 28, 12, 30, tzinfo=timezone.utc)
            return value

        with patch.object(p, 'view', side_effect=expires_during_read), self.assertRaisesRegex(p.Denied, 'Contribution retirée ou expirée'):
            archive.change_contribution(self.store, self.session_token, 'fixture', body)
        self.assertEqual(1, self.connection.execute('SELECT revision FROM s7_contributions').fetchone()[0])

    def test_reaffirming_consent_can_use_a_newer_manager_without_extending_expiry(self):
        first, _ = archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        self.now += timedelta(days=1)
        token = '55' * 32
        self.connection.execute('INSERT INTO s7_contribution_managers VALUES (?,?,?,?)',
            ('new-manager', sha256(bytes.fromhex(token)).hexdigest(), self.now.isoformat(),
             first['contribution']['expires_at']))
        updated, _ = archive.change_contribution(self.store, self.session_token, 'fixture',
            self.consent(revision=1), manager_token=token, now=self.now)
        self.assertEqual(first['contribution']['expires_at'], updated['contribution']['expires_at'])
        self.assertEqual(first['contribution']['created_at'], updated['contribution']['created_at'])
        self.assertEqual(1, len(archive.manager_view(self.store, token, now=self.now)['contributions']))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])


    def test_owner_archive_removes_recognizable_secrets_and_remains_verifiable(self):
        original = p.view
        secret = 'sk-or-v1-synthetic_private_key'

        def contaminated(*args, **kwargs):
            value = original(*args, **kwargs)
            value['payload']['request'] += ' ' + secret
            return value

        with patch.object(p, 'view', side_effect=contaminated):
            manifest = archive.archive_manifest(self.store, self.sid, 'fixture')
        self.assertNotIn(secret, self.record(manifest)['need'])
        archive.verify(self.store, self.connection)


    def test_calendar_expiry_uses_utc_and_leap_year(self):
        created = datetime(2027, 8, 31, 12, 30, tzinfo=timezone(timedelta(hours=2)))
        self.advance_to(created)
        value, token = archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=created)
        self.assertEqual('2027-08-31T10:30:00+00:00', value['contribution']['created_at'])
        self.assertEqual('2028-02-29T10:30:00+00:00', value['contribution']['expires_at'])
        archive.verify(self.store, self.connection)


    def test_fictional_organizations_and_fixed_model_names_are_not_personal_data(self):
        original = p.view
        original_comparison = archive.restitution.comparison

        def fictional(*args, **kwargs):
            value = original(*args, **kwargs)
            if value['package']:
                value['package']['instruction'] += ' pour Atelier Brumélis'
            return value

        def named(*args, **kwargs):
            value = original_comparison(*args, **kwargs)
            if value['rows']:
                value['rows'][0]['requested_configuration']['model'] = 'Claude Fable'
            return value

        with patch.object(p, 'view', side_effect=fictional), patch.object(archive.restitution, 'comparison', side_effect=named):
            archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        raw = self.connection.execute('SELECT payload_json FROM s7_contributions').fetchone()[0]
        self.assertIn('Atelier Brumélis', raw)
        self.assertIn('Claude Fable', raw)
        archive.verify(self.store, self.connection)


    def test_visible_qualification_summary_and_findings_are_copied_without_private_fields(self):
        original = p.view

        def qualified(*args, **kwargs):
            value = original(*args, **kwargs)
            value['qualification'].update(summary='Critères vérifiables',
                findings=[dict(kind='coherence', severity='note', text='Consigne cohérente',
                               reference='PRIVATE_QUALIFICATION_REFERENCE')],
                reference='PRIVATE_QUALIFICATION_REFERENCE', operation='PRIVATE_QUALIFICATION_LOG')
            return value

        with patch.object(p, 'view', side_effect=qualified):
            manifest = archive.archive_manifest(self.store, self.sid, 'fixture')
            archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        record = self.record(manifest)
        payload = json.loads(self.connection.execute('SELECT payload_json FROM s7_contributions').fetchone()[0])
        for value in (record['revisions'][-1], payload['example']):
            self.assertIsInstance(value['qualification'], str)
            self.assertIn('Critères vérifiables', value['qualification'])
            self.assertIn('coherence / note : Consigne cohérente', value['qualification'])
        self.assertNotIn('PRIVATE_QUALIFICATION', json.dumps([record, payload]))
        archive.verify(self.store, self.connection)


    def test_pending_answers_survive_judgment_progress_or_failure_without_judge_references(self):
        self.store.create_budget('pending-budget', '100', 'TEST')
        c.admit(self.store, 'empty', *inputs(c.inspect(self.store, 'empty'), budget='pending-budget'))
        c.reserve(self.store, 'empty', 'x', 'pending-x')
        execution.execute(self.data, 'pending-x', response)
        c.reserve(self.store, 'empty', 'y', 'pending-y')
        first = archive.archive_manifest(self.store, self.sid, 'fixture')
        values = next(row['models'] for row in self.record(first)['campaigns'] if row['id'] == 'empty')
        self.assertEqual(2, len(values))
        self.assertEqual('  fictional raw output\n', values[0]['answer'])
        self.assertEqual(dict(amount='2', currency='TEST'), values[0]['cost'])
        self.assertIsNone(values[0]['verdict'])
        self.assertEqual(['Configuration demandée'], [item['name'] for item in values[0]['evidence']])
        self.assertIsNone(values[1]['answer'])
        self.assertIsNone(values[1]['cost']['amount'])
        c.stop(self.store, 'empty', reason='JUDGMENT_STOPPED')
        self.connection.execute('UPDATE s7_dossiers SET content_version=content_version+1')
        stopped = self.record(archive.archive_manifest(self.store, self.sid, 'fixture'))
        self.assertEqual(values, next(row['models'] for row in stopped['campaigns'] if row['id'] == 'empty'))
        self.assertNotIn('Attendu fictif réservé', json.dumps(stopped, ensure_ascii=False))
        archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        copy = json.loads(self.connection.execute('SELECT payload_json FROM s7_contributions').fetchone()[0])
        self.assertEqual(values, next(row['models'] for row in copy['campaigns'] if row['id'] == 'empty'))
        archive.verify(self.store, self.connection)


    def test_explicit_reconsent_creates_a_new_copy_with_latest_revision_cas(self):
        first, token = archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        old = first['contribution']
        csrf = archive.manager_view(self.store, token, now=self.now)['csrf_token']
        archive.withdraw(self.store, token, old['id'], csrf, now=self.now)
        latest = archive.contribution_view(self.store, self.sid, 'fixture', now=self.now)['contribution']
        self.assertEqual(2, latest['revision'])
        with self.assertRaises(storage.ConflictError):
            archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(revision=1), now=self.now)
        later = datetime(2026, 9, 30, 12, 30, tzinfo=timezone.utc)
        self.advance_to(later)
        renewed, issued = archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(revision=2),
                                                     manager_token=token, now=later)
        new = renewed['contribution']
        self.assertIsNone(issued)
        self.assertNotEqual(old['id'], new['id'])
        self.assertEqual(3, new['revision'])
        self.assertEqual('2027-03-30T12:30:00+00:00', new['expires_at'])
        self.assertEqual(('withdrawn', old['expires_at'], None), self.connection.execute(
            'SELECT status,expires_at,payload_json FROM s7_contributions WHERE contribution_id=?', (old['id'],)).fetchone())
        self.assertEqual(new, archive.contribution_view(self.store, self.sid, 'fixture', now=later)['contribution'])
        archive.withdraw(self.store, token, old['id'], csrf, now=later)
        self.assertEqual(new, archive.contribution_view(self.store, self.sid, 'fixture', now=later)['contribution'])
        active = list(self.connection.execute('SELECT * FROM s7_contributions WHERE contribution_id=?', (new['id'],)).fetchone())
        active[0], active[4] = 'duplicate-active', 4
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute('INSERT INTO s7_contributions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', active)
        archive.verify(self.store, self.connection)

    def test_refresh_started_before_withdrawal_cannot_write_into_a_new_consent(self):
        first, token = archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        old = first['contribution']
        csrf = archive.manager_view(self.store, token, now=self.now)['csrf_token']
        self.connection.execute('UPDATE s7_dossiers SET content_version=2')
        original = p.view

        def replace_consent(*args, **kwargs):
            value = original(*args, **kwargs)
            with patch.object(p, 'view', original):
                archive.withdraw(self.store, token, old['id'], csrf, now=self.now)
                archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(revision=2),
                                            manager_token=token, now=self.now)
            value['package']['instruction'] = 'STALE_REFRESH'
            return value

        with patch.object(p, 'view', side_effect=replace_consent), self.assertRaises(storage.ConflictError):
            archive.refresh_contributions(self.store, 'fixture')
        rows = self.connection.execute('SELECT contribution_id,revision,status,payload_json FROM s7_contributions ORDER BY revision').fetchall()
        self.assertEqual((old['id'], 2, 'withdrawn', None), rows[0])
        self.assertEqual((3, 'active'), rows[1][1:3])
        self.assertNotIn('STALE_REFRESH', rows[1][3])
        archive.verify(self.store, self.connection)


    def test_both_withdrawals_roll_back_payload_and_marker_together_and_allow_journal_replay(self):
        import benchmark.privacy as privacy
        value, token = archive.change_contribution(self.store, self.session_token, 'fixture', self.consent(), now=self.now)
        identity = value['contribution']['id']
        csrf = archive.manager_view(self.store, token, now=self.now)['csrf_token']
        before = self.connection.execute('SELECT status,revision,payload_json FROM s7_contributions').fetchone()
        self.connection.execute("CREATE TEMP TRIGGER reject_revocation_marker BEFORE INSERT ON s7_revocations "
                                "BEGIN SELECT RAISE(ABORT, 'marker unavailable'); END")
        for route in ('manager', 'owner'):
            with self.subTest(route=route):
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'marker unavailable'):
                    if route == 'manager':
                        archive.withdraw(self.store, token, identity, csrf, now=self.now)
                    else:
                        archive.change_contribution(self.store, self.session_token, 'fixture',
                                                    self.consent(revision=1, enabled=False), now=self.now)
                self.assertEqual(before, self.connection.execute('SELECT status,revision,payload_json FROM s7_contributions').fetchone())
                self.assertEqual([], self.connection.execute('SELECT * FROM s7_revocations').fetchall())
        self.assertEqual(2, len(self.events))
        self.connection.execute('DROP TRIGGER reject_revocation_marker')
        for event in self.events + self.events:
            with storage._transaction(self.connection, write=True):
                privacy.apply_revocation(self.connection, event)
        self.assertEqual(('withdrawn', 2, None), self.connection.execute('SELECT status,revision,payload_json FROM s7_contributions').fetchone())
        self.assertEqual(2, self.connection.execute('SELECT count(*) FROM s7_revocations').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
