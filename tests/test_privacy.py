"""Conservation privée, sans appel fournisseur"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from benchmark import evaluation, preparation, privacy, provider_access, qualification, storage
from benchmark.acquisition import campaigns
from benchmark import web_api
from tests.test_s2_review_regressions import response_for


NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
SECRET = bytes.fromhex('11' * 32)


def initialize(data):
    storage.initialize(data)
    storage.initialize_preparation(data)
    qualification.initialize(data)
    campaigns.initialize(data)
    evaluation.initialize(data)
    provider_access.initialize(data)
    return privacy.migrate(data, SECRET, 'test-migration', now=NOW)


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name).resolve() / 'private'
        initialize(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)

    def test_session_expire_cote_serveur_meme_si_cookie_encore_present(self):
        with patch.object(privacy, 'now', return_value=NOW):
            session, csrf, token = preparation.session(self.store, None, create=True)
        with patch.object(privacy, 'now', return_value=NOW + timedelta(days=29)):
            self.assertEqual(session, preparation.session(self.store, token)[0])
        with patch.object(privacy, 'now', return_value=NOW + timedelta(days=30)):
            with self.assertRaises(preparation.Denied):
                preparation.session(self.store, token)

    def test_lecture_ne_renouvelle_pas_mais_activite_renouvelle_identite_stable(self):
        with patch.object(privacy, 'now', return_value=NOW):
            session, csrf, token = preparation.session(self.store, None, create=True)
        with patch.object(privacy, 'now', return_value=NOW + timedelta(days=20)):
            self.assertEqual(session, preparation.session(self.store, token)[0])
            privacy.activity(self.store, session)
        with patch.object(privacy, 'now', return_value=NOW + timedelta(days=40)):
            self.assertEqual((session, csrf, token), preparation.session(self.store, token))
        with patch.object(privacy, 'now', return_value=NOW + timedelta(days=50)):
            with self.assertRaises(preparation.Denied):
                preparation.session(self.store, token)

    def test_migration_repetee_ne_renouvelle_pas_les_dates(self):
        before = privacy.migration_status(self.store)
        with closing(storage.Store(self.data)) as other:
            self.assertEqual('s7', storage._check_schema(other._connection))
        self.assertEqual(before, privacy.migrate(self.data, SECRET, 'test-migration', now=NOW + timedelta(days=2)))
        with self.assertRaises(storage.ConflictError):
            privacy.migrate(self.data, SECRET, 'other-migration', now=NOW)
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def dossier(self, name='private-case'):
        with patch.object(privacy, 'now', return_value=NOW):
            session, _, _ = preparation.session(self.store, None, create=True)
            budget = 'personal-preparation-' + session
            self.store.create_budget(budget, '100', 'TEST')
            preparation.admit(self.store, dict(authority_id='TEST_ONLY', budget_id=budget,
                reserve_amount='7', requested_configuration={'model': 'fictional'}))
            operation, _ = preparation.submit(self.store, session, name,
                {'action_id': 'create', 'request': 'Organiser des notes entièrement fictives'}, 'a' * 40, True)
            preparation.execute(self.data, operation, lambda op, request: response_for(op))
        return session, operation

    def test_suppression_retire_textes_et_pieces_sans_recreer_credit(self):
        session, operation = self.dossier()
        before = self.store.inspect_budget('personal-preparation-' + session)
        with patch.object(privacy, 'now', return_value=NOW):
            privacy.request_delete(self.store, session, 'private-case')
            with self.assertRaises(privacy.Gone):
                preparation.view(self.store, session, 'private-case')
            result = privacy.purge(self.data)
        self.assertEqual(['private-case'], result['purged'])
        self.assertEqual([], list((self.data / 'pieces').iterdir()))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        self.assertEqual(before, self.store.inspect_budget('personal-preparation-' + session))
        self.assertNotIn(b'Organiser des notes', (self.data / 'metadata.sqlite3').read_bytes())
        with patch.object(privacy, 'now', return_value=NOW):
            self.assertEqual([], privacy.purge(self.data)['purged'])

    def test_expiration_dossier_n_attend_pas_la_purge(self):
        session, _ = self.dossier()
        with patch.object(privacy, 'now', return_value=NOW + timedelta(days=7)):
            with self.assertRaises(privacy.Gone):
                preparation.view(self.store, session, 'private-case')

    def test_premier_get_n_ecrase_pas_un_cookie_absent_sur_navigation_externe(self):
        status, value, cookie, work = web_api.dispatch(self.store, 'GET', '/preparation', None,
                                                     None, 'a' * 40, None)
        self.assertEqual((200, 'session_bootstrap', None, None), (status, value['kind'], cookie, work))
        with patch.object(privacy, 'now', return_value=NOW):
            status, value, token, work = web_api.dispatch(self.store, 'POST', '/preparation/session/open',
                None, {}, 'a' * 40, None)
            self.assertEqual(200, status)
            self.assertTrue(token)
            other = web_api.dispatch(self.store, 'POST', '/preparation/session/open', token, {}, 'a' * 40, None)
            self.assertIsNone(other[2])
            self.assertEqual(value['csrf_token'], other[1]['csrf_token'])

    def test_historique_local_accessible_sans_session_et_activite_protegee(self):
        status, value, cookie, _ = web_api.dispatch(self.store, 'GET', '/preparation/data', None, None, 'a' * 40, None)
        self.assertEqual((200, 'privacy_data', None), (status, value['kind'], cookie))
        with patch.object(privacy, 'now', return_value=NOW):
            session, csrf, token = preparation.session(self.store, None, create=True)
            with self.assertRaises(preparation.Denied):
                web_api.dispatch(self.store, 'POST', '/preparation/activity', token, {}, 'a' * 40, None)
            status, value, cookie, _ = web_api.dispatch(self.store, 'POST', '/preparation/activity', token,
                {'csrf_token': csrf}, 'a' * 40, None)
            self.assertEqual((200, token), (status, cookie))

    def test_purge_attend_le_recu_en_vol_et_ne_ressuscite_pas_le_dossier(self):
        entered, released = threading.Event(), threading.Event()
        with patch.object(privacy, 'now', return_value=NOW):
            session, _, _ = preparation.session(self.store, None, create=True)
            budget = 'personal-preparation-' + session
            self.store.create_budget(budget, '100', 'TEST')
            preparation.admit(self.store, dict(authority_id='TEST_ONLY', budget_id=budget,
                reserve_amount='7', requested_configuration={'model': 'fictional'}))
            operation, _ = preparation.submit(self.store, session, 'in-flight',
                {'action_id': 'create', 'request': 'Organiser des notes entièrement fictives'}, 'a' * 40, True)
            def response(op, request):
                entered.set()
                if not released.wait(5):
                    raise TimeoutError('Test interrompu')
                return response_for(op)
            worker = threading.Thread(target=preparation.execute, args=(self.data, operation, response))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                privacy.request_delete(self.store, session, 'in-flight')
                self.assertEqual({'purged': [], 'pending': True}, privacy.purge(self.data))
            finally:
                released.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            with self.assertRaises(privacy.Gone):
                preparation.view(self.store, session, 'in-flight')
            self.assertEqual(['in-flight'], privacy.purge(self.data)['purged'])

    def test_reprise_apres_suppression_du_fichier_avant_son_acquittement(self):
        session, _ = self.dossier()
        with patch.object(privacy, 'now', return_value=NOW):
            privacy.request_delete(self.store, session, 'private-case')
            unlink = privacy.os.unlink
            def interrupted(*args, **kwargs):
                unlink(*args, **kwargs)
                raise OSError('Coupure simulée après unlink')
            with patch.object(privacy.os, 'unlink', side_effect=interrupted), self.assertRaises(OSError):
                privacy.purge(self.data)
            self.assertTrue(self.store.verify_storage()['integrity_ok'])
            self.assertEqual(['private-case'], privacy.purge(self.data)['purged'])

    def test_journal_indisponible_ne_confirme_pas_une_suppression(self):
        session, _ = self.dossier()
        with patch.object(privacy, 'now', return_value=NOW), \
                patch.object(privacy, 'journal_intent', side_effect=OSError('indisponible')):
            with self.assertRaises(OSError):
                privacy.request_delete(self.store, session, 'private-case')
            self.assertEqual('private-case', preparation.view(self.store, session, 'private-case')['dossier_id'])

    def test_boot_change_requires_external_journal_and_does_not_reopen_admission(self):
        from hashlib import sha256
        from benchmark import runtime
        session, _ = self.dossier()
        with patch.object(privacy, 'now', return_value=NOW):
            event = privacy.journal_intent(self.store, 'dossier', 'private-case')
            proof = sha256(privacy.journal_path(self.store).read_bytes()).hexdigest()
            with patch.object(privacy, 'boot_identity', return_value='new-boot'):
                self.assertTrue(runtime.status(self.data, self.store)['restore_pending'])
                self.assertEqual('RESTORE_PENDING', privacy.purge(self.data)['reason'])
                self.assertEqual(('ACTIVE',), self.store._connection.execute('SELECT state FROM s7_dossiers').fetchone())
                for path in ('/preparation', '/preparation/contributions'):
                    self.assertEqual(503, web_api.dispatch(self.store, 'GET', path, None, None, 'a'*40, None)[0])
                self.assertTrue(privacy.migrate(self.data, SECRET, 'test-migration')['restore_pending'])
                with self.assertRaises(storage.IntegrityError):
                    privacy.reconcile(self.data, '0'*64)
                self.assertTrue(privacy.boot_pending(self.store._connection))
                result = privacy.reconcile(self.data, proof)
                self.assertFalse(result['restore_pending'])
                self.assertFalse(runtime.status(self.data, self.store)['admission'])
                self.assertEqual(('PURGED',), self.store._connection.execute('SELECT state FROM s7_dossiers').fetchone())

    def test_expired_access_releases_only_retired_technical_markers(self):
        session, _ = self.dossier()
        with patch.object(privacy, 'now', return_value=NOW):
            privacy.request_delete(self.store, session, 'private-case')
            privacy.purge(self.data)
            self.assertTrue(privacy.retired_operations(self.store._connection))
        with patch.object(privacy, 'now', return_value=NOW+timedelta(days=31)):
            privacy.purge(self.data)
        self.assertEqual([], privacy.retired_operations(self.store._connection))
        self.assertEqual([], self.store._connection.execute('SELECT * FROM s2_sessions').fetchall())
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_nested_worker_lock_keeps_outer_exclusion(self):
        from benchmark.runtime import worker_lock
        with worker_lock(self.store, shared=True):
            with worker_lock(self.store, shared=True):
                pass
            with closing(storage.Store(self.data)) as other:
                with self.assertRaises(BlockingIOError), worker_lock(other):
                    pass
