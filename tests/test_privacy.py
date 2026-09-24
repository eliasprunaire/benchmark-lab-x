"""Conservation privée, sans appel fournisseur"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import time
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

    def dossier(self, name='private-case', at=NOW):
        with patch.object(privacy, 'now', return_value=at):
            session, _, self.token = preparation.session(self.store, None, create=True)
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

    def test_ecriture_concurrente_apres_l_effet_ne_dement_pas_l_action_enregistree(self):
        import json
        import sqlite3
        from benchmark import service
        self.dossier()
        later = NOW + timedelta(days=3)
        blocker = sqlite3.connect(self.data / 'metadata.sqlite3', isolation_level=None, timeout=0)
        self.addCleanup(blocker.close)
        submit = preparation.submit

        def submit_then_lock(*args, **kwargs):
            # Un autre écrivain prend la base dès que l'effet est commis
            result = submit(*args, **kwargs)
            blocker.execute('BEGIN IMMEDIATE')
            return result

        def handle(message):
            code, value, _, _ = web_api.dispatch(self.store, message['method'], message['path'], message['token'],
                                                 message['body'], 'a' * 40, True)
            return {'status': code, 'value': value}
        with patch.object(privacy, 'now', return_value=later):
            _, csrf, _ = preparation.session(self.store, self.token)
            raw = json.dumps({'method': 'POST', 'path': '/preparation/dossiers/private-case/messages', 'token': self.token,
                              'body': {'csrf_token': csrf, 'action_id': 'clarify', 'revision': 2, 'kind': 'clarify',
                                       'message': 'Préciser le contexte fictif', 'source_sha256': 'c' * 64}}).encode() + b'\n'
            with patch.object(preparation, 'submit', submit_then_lock), \
                    patch.object(preparation, '_now', return_value=datetime.now(timezone.utc) + timedelta(hours=1)):
                try:
                    response = service.executor_result(raw, None, handle)
                finally:
                    if blocker.in_transaction:
                        blocker.execute('ROLLBACK')
        self.assertEqual(202, response['status'], response['value'])
        self.assertEqual(1, self.store._connection.execute(
            "SELECT count(*) FROM s2_actions WHERE dossier_id='private-case' AND action_id='clarify'").fetchone()[0])
        # L'effet et ses échéances partagent la même transaction
        self.assertEqual(((later + privacy.SESSION_LIFETIME).isoformat(), (later + privacy.DOSSIER_LIFETIME).isoformat()),
                         self.store._connection.execute(
                             "SELECT s.expires_at,d.expires_at FROM s7_sessions s JOIN s7_dossiers d USING(session_id) "
                             "WHERE d.dossier_id='private-case'").fetchone())

    def test_lecture_bloquee_apres_l_effet_rend_l_effet_et_son_travail(self):
        import sqlite3
        self.dossier()
        later = NOW + timedelta(days=3)
        blocker = sqlite3.connect(self.data / 'metadata.sqlite3', isolation_level=None, timeout=0)
        self.addCleanup(blocker.close)
        self.store._connection.execute('PRAGMA busy_timeout=50')
        submit = preparation.submit

        def submit_then_lock(*args, **kwargs):
            # Un autre écrivain tient la base en exclusif : même les lectures attendent
            result = submit(*args, **kwargs)
            blocker.execute('BEGIN EXCLUSIVE')
            return result
        with patch.object(privacy, 'now', return_value=later):
            _, csrf, _ = preparation.session(self.store, self.token)
            with patch.object(preparation, 'submit', submit_then_lock), \
                    patch.object(preparation, '_now', return_value=datetime.now(timezone.utc) + timedelta(hours=1)):
                try:
                    code, value, _, work = web_api.dispatch(self.store, 'POST', '/preparation/dossiers/private-case/messages',
                        self.token, {'csrf_token': csrf, 'action_id': 'clarify', 'revision': 2, 'kind': 'clarify',
                                     'message': 'Préciser le contexte fictif', 'source_sha256': 'd' * 64}, 'a' * 40, True)
                finally:
                    if blocker.in_transaction:
                        blocker.execute('ROLLBACK')
        self.assertEqual((202, value['operation_id']), (code, work))
        self.assertNotIn('privacy', value)
        self.assertEqual(1, self.store._connection.execute(
            "SELECT count(*) FROM s2_actions WHERE dossier_id='private-case' AND action_id='clarify'").fetchone()[0])

    def test_executeur_lance_la_preparation_malgre_une_lecture_bloquee_apres_l_effet(self):
        import sqlite3
        from benchmark import service
        self.dossier()
        later = NOW + timedelta(days=3)
        blocker = sqlite3.connect(self.data / 'metadata.sqlite3', isolation_level=None, timeout=0, check_same_thread=False)
        self.addCleanup(blocker.close)
        submit, dispatch, servers, ready = preparation.submit, web_api.dispatch, [], threading.Event()

        def submit_then_lock(*args, **kwargs):
            result = submit(*args, **kwargs)
            blocker.execute('BEGIN EXCLUSIVE')
            return result

        def dispatch_then_release(*args, **kwargs):
            # Verrou rendu avant le lancement du travail : seules les lectures de décoration l'ont subi
            try:
                return dispatch(*args, **kwargs)
            finally:
                if blocker.in_transaction:
                    blocker.execute('ROLLBACK')

        def run(server):
            servers.append(server)
            ready.set()
            server.serve_forever(poll_interval=.01)
        sock = self.data.parent / 'executor.sock'
        with patch.object(privacy, 'now', return_value=later), patch.object(service, 'run', run), \
                patch.object(preparation, 'submit', submit_then_lock), patch.object(web_api, 'dispatch', dispatch_then_release), \
                patch.object(preparation, '_now', return_value=datetime.now(timezone.utc) + timedelta(hours=1)):
            _, csrf, _ = preparation.session(self.store, self.token)
            authority = preparation.admission(self.store)
            worker = threading.Thread(target=service.serve_executor, args=(self.data, sock, 'a' * 40),
                                      kwargs=dict(transport=lambda op, request: response_for(op)))
            worker.start()
            try:
                self.assertTrue(ready.wait(5))
                preparation.admit(self.store, authority)
                response = service.preparation_request(sock, 'POST', '/preparation/dossiers/private-case/messages',
                    self.token, {'csrf_token': csrf, 'action_id': 'clarify', 'revision': 2, 'kind': 'clarify',
                                 'message': 'Préciser le contexte fictif', 'source_sha256': 'e' * 64})
                self.assertEqual(202, response['status'], response['value'])
                operation = response['value']['operation_id']
                deadline = time.monotonic() + 10
                while {o['operation_id']: o['state'] for o in self.store.inspect_operations()}[operation] != 'RECEIVED':
                    self.assertLess(time.monotonic(), deadline, 'Préparation jamais lancée')
                    time.sleep(.02)
            finally:
                if servers:
                    servers[0].shutdown()
                worker.join(10)
                self.assertFalse(worker.is_alive())

    def test_activite_jointe_n_annule_pas_l_ecriture_et_ne_ressuscite_pas_une_echeance(self):
        session, _ = self.dossier()
        connection = self.store._connection
        before = connection.execute("SELECT expires_at,content_version FROM s7_dossiers WHERE dossier_id='private-case'").fetchone()
        # Le dossier expire entre l'autorisation de l'effet et le COMMIT d'une écriture de journalisation
        with patch.object(privacy, 'now', return_value=NOW + privacy.DOSSIER_LIFETIME):
            with web_api._activity_with_effect(self.store, session, 'private-case') as joined:
                with storage._transaction(connection, write=True):
                    connection.execute("UPDATE s7_dossiers SET content_version=content_version+1 WHERE dossier_id='private-case'")
        self.assertEqual([connection], joined)
        self.assertEqual((before[0], before[1] + 1), connection.execute(
            "SELECT expires_at,content_version FROM s7_dossiers WHERE dossier_id='private-case'").fetchone())

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
                self.assertEqual({'purged': [], 'pending': True, 'lock': 'UNAVAILABLE'}, privacy.purge(self.data))
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

    def test_first_boot_reconciliation_works_before_any_revocation(self):
        from hashlib import sha256
        self.assertEqual(b'', privacy.journal_path(self.store).read_bytes())
        with patch.object(privacy, 'boot_identity', return_value='new-boot'):
            self.assertTrue(privacy.boot_pending(self.store._connection))
            self.assertFalse(privacy.reconcile(self.data, sha256(b'').hexdigest())['restore_pending'])

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

    def holding(self, lock):
        """Tient `lock(store)` dans un autre fil jusqu'à `release`, comme une requête ou une tâche"""
        held, release = threading.Event(), threading.Event()
        def holder():
            with closing(storage.Store(self.data)) as other, lock(other):
                held.set()
                release.wait(5)
        thread = threading.Thread(target=holder)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(release.set)
        self.assertTrue(held.wait(5))
        return release

    def test_purge_attend_la_fin_des_requetes_en_cours_dans_sa_fenetre(self):
        from benchmark.runtime import worker_lock
        session, _ = self.dossier()
        release = self.holding(lambda store: worker_lock(store, shared=True))
        with patch.object(privacy, 'now', return_value=NOW):
            privacy.request_delete(self.store, session, 'private-case')
            self.assertEqual({'purged': [], 'pending': True, 'lock': 'UNAVAILABLE'},
                             privacy.purge(self.data, wait=5, drain=0.1))
            threading.Timer(0.2, release.set).start()
            result = privacy.purge(self.data, wait=5, drain=5)
        self.assertEqual((['private-case'], 'ACQUIRED'), (result['purged'], result['lock']))

    def test_purge_attend_la_fin_des_taches_sans_fermer_le_service(self):
        from benchmark.runtime import maintenance_gate
        session, _ = self.dossier()
        release = self.holding(maintenance_gate)
        with patch.object(privacy, 'now', return_value=NOW):
            privacy.request_delete(self.store, session, 'private-case')
            self.assertEqual('UNAVAILABLE', privacy.purge(self.data, wait=0.1)['lock'])
            done = []
            purge = threading.Thread(target=lambda: done.append(privacy.purge(self.data, wait=5, drain=1)))
            purge.start()
            time.sleep(0.2)
            # La purge attend la tâche sans tenir la porte : une requête passe encore
            with closing(storage.Store(self.data)) as other, maintenance_gate(other):
                pass
            release.set()
            purge.join(5)
        self.assertEqual((['private-case'], 'ACQUIRED'), (done[0]['purged'], done[0]['lock']))

    def test_tache_de_fond_attend_la_purge_au_lieu_d_echouer(self):
        from benchmark import service
        from benchmark.runtime import maintenance_gate
        ran = threading.Event()
        release = self.holding(lambda store: maintenance_gate(store, exclusive=True))
        job = threading.Thread(target=service._retention_worker, args=(self.data, None, ran.set))
        job.start()
        self.assertFalse(ran.wait(0.3))
        release.set()
        job.join(5)
        self.assertTrue(ran.is_set())

    def test_purge_lit_les_operations_une_fois_par_passage(self):
        sessions = [self.dossier(f'case-{index}')[0] for index in range(4)]
        original, reads = storage.Store._operations, []
        def counted(store, connection, **kwargs):
            reads.append(1)
            return original(store, connection, **kwargs)
        def pass_reads(indexes):
            for index in indexes:
                privacy.request_delete(self.store, sessions[index], f'case-{index}')
            del reads[:]
            with patch.object(storage.Store, '_operations', counted):
                self.assertEqual(len(indexes), len(privacy.purge(self.data)['purged']))
            return len(reads)
        with patch.object(privacy, 'now', return_value=NOW):
            # Coût linéaire : le nombre de lectures ne suit pas le nombre de dossiers purgés
            self.assertEqual(pass_reads([0]), pass_reads([1, 2, 3]))

    def test_porte_de_maintenance_refuse_les_nouveaux_travaux_pendant_la_purge(self):
        from benchmark.runtime import maintenance_gate, worker_lock
        with closing(storage.Store(self.data)) as other:
            with maintenance_gate(self.store, exclusive=True):
                with self.assertRaises(BlockingIOError), maintenance_gate(other):
                    pass
            with maintenance_gate(other), worker_lock(other, shared=True):
                pass


class PurgeCommandTests(unittest.TestCase):
    """Contrat de sortie de `purge-privacy`, seul point de contact avec l'ordonnanceur"""

    setUp, dossier = PrivacyTests.setUp, PrivacyTests.dossier

    def run_purge(self, at, *extra):
        from contextlib import redirect_stdout
        from io import StringIO
        import json
        from benchmark import runtime
        output = StringIO()
        with patch.object(privacy, 'now', return_value=at), redirect_stdout(output):
            code = runtime.main(['purge-privacy', '--data', str(self.data), *extra])
        lines = output.getvalue().splitlines()
        self.assertEqual(1, len(lines), lines)
        line = json.loads(lines[0])
        self.assertEqual({'event', 'outcome', 'purged_count', 'operator_review_count', 'lock', 'duration_seconds', 'reason'},
                         set(line))
        self.assertIsInstance(line['duration_seconds'], float)
        return code, line

    def pieces(self):
        return sorted(path.name for path in (self.data / 'pieces').iterdir())

    def test_dossier_echu_disparait_actif_reste_et_second_passage_sans_effet(self):
        expired, _ = self.dossier('expired-case')
        before = self.pieces()
        active, _ = self.dossier('active-case', at=NOW + timedelta(days=3))
        active_pieces = sorted(set(self.pieces()) - set(before))
        self.assertTrue(before and active_pieces)
        code, line = self.run_purge(NOW + timedelta(days=7))
        self.assertEqual((0, 'PURGED', 1, 'ACQUIRED', None),
                         (code, line['outcome'], line['purged_count'], line['lock'], line['reason']))
        self.assertEqual(active_pieces, self.pieces())
        with patch.object(privacy, 'now', return_value=NOW + timedelta(days=7)):
            self.assertEqual('active-case', preparation.view(self.store, active, 'active-case')['dossier_id'])
        snapshot = (self.pieces(), (self.data / 'metadata.sqlite3').read_bytes())
        code, line = self.run_purge(NOW + timedelta(days=7))
        self.assertEqual((10, 'NOTHING_TO_PURGE', 0, 'ACQUIRED'), (code, line['outcome'], line['purged_count'], line['lock']))
        self.assertEqual(snapshot, (self.pieces(), (self.data / 'metadata.sqlite3').read_bytes()))

    def test_verrou_non_obtenu_a_son_propre_code(self):
        from benchmark.runtime import maintenance_gate
        self.dossier()
        # Une tâche de fond tient la porte pendant toute la fenêtre
        with closing(storage.Store(self.data)) as other, maintenance_gate(other):
            code, line = self.run_purge(NOW + timedelta(days=7), '--lock-wait', '0')
        self.assertEqual((75, 'LOCK_UNAVAILABLE', 0, 'UNAVAILABLE'), (code, line['outcome'], line['purged_count'], line['lock']))
        self.assertTrue(self.pieces())

    def test_echec_a_son_propre_code_sans_detail_prive(self):
        with patch.object(privacy, 'purge', side_effect=RuntimeError(str(self.data))):
            code, line = self.run_purge(NOW)
        self.assertEqual((78, 'FAILED', 'RuntimeError'), (code, line['outcome'], line['reason']))
        self.assertNotIn(str(self.data), encode_line(line))

    def test_quatre_issues_ont_quatre_codes_distincts(self):
        from benchmark.runtime import PURGE_EXIT_CODES
        self.assertEqual({'PURGED', 'NOTHING_TO_PURGE', 'LOCK_UNAVAILABLE', 'FAILED'}, set(PURGE_EXIT_CODES))
        self.assertEqual(4, len(set(PURGE_EXIT_CODES.values())))


def encode_line(line):
    import json
    return json.dumps(line, ensure_ascii=False)


class ReconcileGateTests(unittest.TestCase):
    setUp = PrivacyTests.setUp

    def test_rapprochement_ne_bute_pas_sur_une_requete_refusee_a_la_porte(self):
        from hashlib import sha256
        from benchmark.runtime import maintenance_gate
        with patch.object(privacy, 'boot_identity', return_value='new-boot'), \
                closing(storage.Store(self.data)) as other, maintenance_gate(other):
            self.assertFalse(privacy.reconcile(self.data, sha256(b'').hexdigest())['restore_pending'])
