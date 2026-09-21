"""Bornes d'admission de la préparation, sans appel réseau"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from benchmark import preparation as prep, storage, web_api
from tests.test_s2_review_regressions import response_for


class PreparationLimitTests(unittest.TestCase):
    def setUp(self):
        prep._SOURCE_ACCEPTED.clear()

    def fixture(self, reserve='7'):
        temporary = tempfile.TemporaryDirectory(prefix='preparation-limits-')
        self.addCleanup(temporary.cleanup)
        data = Path(temporary.name).resolve() / 'private'
        storage.initialize(data)
        storage.initialize_preparation(data)
        store = storage.Store(data)
        self.addCleanup(store.close)
        store.create_budget('preparation', '100', 'USD')
        prep.admit(store, dict(authority_id='TEST_LIMITS', budget_id='preparation',
                               reserve_amount=reserve, requested_configuration={'model': 'fictional'}))
        session, csrf, token = prep.session(store, None, create=True)
        return data, store, session, csrf, token

    def create(self, store, token, csrf, dossier, text, source='a' * 64, **fields):
        return web_api.dispatch(store, 'POST', '/preparation/dossiers', token,
                                {'csrf_token': csrf, 'dossier_id': dossier, 'action_id': dossier,
                                 'request': text, 'source_sha256': source, **fields}, 'a' * 40, True)

    def receive(self, data, operation):
        def transport(value, request):
            result = response_for(value)
            result['cost']['currency'] = 'USD'
            return result
        prep.execute(data, operation, transport)

    def state(self, store):
        connection = store._connection
        return tuple(connection.execute(f'SELECT * FROM {table}').fetchall()
                     for table in ('dossier_revisions', 's2_dossiers', 's2_actions',
                                   'operations', 'reservations'))

    def test_exact_text_bounds_and_normalization(self):
        for field, maximum in (('request', prep.REQUEST_MAX), ('useful', prep.USEFUL_MAX),
                               ('context', prep.CONTEXT_MAX), ('message', prep.MESSAGE_MAX)):
            minimum = prep.REQUEST_MIN if field == 'request' else 1 if field == 'message' else 0
            self.assertEqual('x' * minimum, prep._normalized_text('x' * minimum, field, minimum, maximum))
            self.assertEqual('x' * maximum, prep._normalized_text('x' * maximum, field, minimum, maximum))
            if minimum:
                with self.assertRaises(prep.Denied) as caught:
                    prep._normalized_text('x' * (minimum - 1), field, minimum, maximum)
                self.assertEqual('TEXT_TOO_SHORT', caught.exception.code)
            with self.assertRaises(prep.Denied) as caught:
                prep._normalized_text('x' * (maximum + 1), field, minimum, maximum)
            self.assertEqual('TEXT_TOO_LONG', caught.exception.code)
        self.assertEqual('é\nA\tB \t\n',
                         prep._normalized_text('e\u0301\r\nA\x00\tB \t\n   ', 'request', 1, 20))

    def test_refused_text_creates_nothing(self):
        _, store, _, csrf, token = self.fixture()
        before = self.state(store)
        with self.assertRaises(prep.Denied) as caught:
            self.create(store, token, csrf, 'short', 'x' * 39)
        self.assertEqual(('TEXT_TOO_SHORT', 'request'), (caught.exception.code, caught.exception.field))
        self.assertEqual(before, self.state(store))

    def test_missing_or_invalid_source_creates_nothing(self):
        _, store, _, csrf, token = self.fixture()
        base = {'csrf_token': csrf, 'dossier_id': 'source', 'action_id': 'source',
                'request': 'x' * 40}
        for source in (None, 'g' * 64, 'a' * 63):
            body = dict(base)
            if source is not None:
                body['source_sha256'] = source
            before = self.state(store)
            with self.subTest(source=source), self.assertRaises(prep.Denied) as caught:
                web_api.dispatch(store, 'POST', '/preparation/dossiers', token, body, 'a' * 40, True)
            self.assertEqual(('SOURCE_MISSING', 'source_sha256'),
                             (caught.exception.code, caught.exception.field))
            self.assertEqual(before, self.state(store))

    def test_session_in_progress_and_interval(self):
        data, store, _, csrf, token = self.fixture()
        start = datetime(2026, 9, 14, 8, tzinfo=timezone.utc)
        with patch.object(prep, '_now', return_value=start):
            _, first, _, operation = self.create(store, token, csrf, 'first', 'x' * 40)
        with patch.object(prep, '_now', return_value=start + timedelta(minutes=1)):
            with self.assertRaises(prep.Denied) as caught:
                self.create(store, token, csrf, 'pending', 'x' * 40)
        self.assertEqual('PREPARATION_IN_PROGRESS', caught.exception.code)
        self.receive(data, operation)
        with patch.object(prep, '_now', return_value=start + timedelta(seconds=29)):
            with self.assertRaises(prep.Denied) as caught:
                self.create(store, token, csrf, 'soon', 'x' * 40)
        self.assertEqual('TOO_SOON', caught.exception.code)
        with patch.object(prep, '_now', return_value=start + timedelta(seconds=30)):
            self.assertEqual(202, self.create(store, token, csrf, 'second', 'x' * 40)[0])

    def test_global_operation_blocks_another_session(self):
        _, store, _, csrf_a, token_a = self.fixture()
        _, csrf_b, token_b = prep.session(store, None, create=True)
        start = datetime(2026, 9, 14, 8, tzinfo=timezone.utc)
        with patch.object(prep, '_now', return_value=start):
            self.assertEqual(202, self.create(store, token_a, csrf_a, 'a', 'x' * 40)[0])
            with self.assertRaises(prep.Denied) as caught:
                self.create(store, token_b, csrf_b, 'b', 'x' * 40, source='b' * 64)
            availability = prep.availability(store, True)
        self.assertEqual('PREPARATION_IN_PROGRESS', caught.exception.code)
        self.assertIsNone(store._connection.execute(
            'SELECT 1 FROM s2_actions WHERE dossier_id=?', ('b',)).fetchone())
        self.assertEqual('waiting', availability['reason'])

    def test_more_than_two_daily_dossiers_within_budget(self):
        data, store, _, csrf, token = self.fixture(reserve='5')
        start = datetime(2026, 9, 14, 8, tzinfo=timezone.utc)
        for index, seconds in ((1, 0), (2, 30), (3, 60)):
            with patch.object(prep, '_now', return_value=start + timedelta(seconds=seconds)):
                code, _, _, operation = self.create(store, token, csrf, f'd{index}', 'x' * 40)
            self.assertEqual(202, code)
            self.receive(data, operation)
        self.assertEqual(3, len(store.inspect_operations()))

    def test_too_soon_does_not_consume_source_window(self):
        data, store, _, csrf, token = self.fixture(reserve='0')
        start = datetime(2026, 9, 14, 8, tzinfo=timezone.utc)
        source = 'c' * 64
        prep._SOURCE_ACCEPTED[source] = [start - timedelta(minutes=1)] * 18
        with patch.object(prep, '_now', return_value=start):
            operation = self.create(store, token, csrf, 'first', 'x' * 40, source=source)[3]
        self.receive(data, operation)
        with patch.object(prep, '_now', return_value=start + timedelta(seconds=1)):
            with self.assertRaises(prep.Denied) as caught:
                self.create(store, token, csrf, 'soon', 'x' * 40, source=source)
        self.assertEqual('TOO_SOON', caught.exception.code)
        with patch.object(prep, '_now', return_value=start + timedelta(seconds=30)):
            self.assertEqual(202, self.create(store, token, csrf, 'second', 'x' * 40,
                                              source=source)[0])

    def test_useful_and_context_are_sent_after_request(self):
        _, store, _, csrf, token = self.fixture(reserve='0')
        captured = {}

        class Transport:
            def prepare(self, operation, request):
                captured.update(request['outgoing'])
                return 'requête préparée'

        request = 'x' * 40
        code, _, _, _ = web_api.dispatch(
            store, 'POST', '/preparation/dossiers', token,
            {'csrf_token': csrf, 'dossier_id': 'details', 'action_id': 'details',
             'request': request, 'useful': 'Une synthèse actionnable',
             'context': 'Équipe francophone', 'source_sha256': 'd' * 64},
            'a' * 40, Transport())
        self.assertEqual(202, code)
        self.assertEqual(request + '\n\nRésultat attendu :\nUne synthèse actionnable'
                         '\n\nContexte :\nÉquipe francophone', captured['message'])
        self.assertEqual(request, captured['request'])

    def test_daily_history_does_not_create_an_additional_financial_cap(self):
        data, store, _, csrf, token = self.fixture(reserve='26')
        operation = self.create(store, token, csrf, 'first', 'x' * 40)[3]
        self.receive(data, operation)
        _, csrf2, token2 = prep.session(store, None, create=True)
        self.assertTrue(prep.availability(store, True)['can_submit'])
        code, _, _, operation = self.create(store, token2, csrf2, 'second', 'x' * 40)
        self.assertEqual(202, code)
        self.receive(data, operation)

    def test_source_limit_is_independent_and_sliding(self):
        data, store, _, _, _ = self.fixture(reserve='0')
        start = datetime(2026, 9, 14, 8, tzinfo=timezone.utc)
        source_a, source_b = 'a' * 64, 'b' * 64
        for index in range(20):
            _, csrf, token = prep.session(store, None, create=True)
            with patch.object(prep, '_now', return_value=start + timedelta(seconds=30 * index)):
                operation = self.create(store, token, csrf, f'a{index}', 'x' * 40,
                                        source=source_a)[3]
            self.receive(data, operation)
        _, csrf, token = prep.session(store, None, create=True)
        with patch.object(prep, '_now', return_value=start + timedelta(minutes=10)):
            with self.assertRaises(prep.Denied) as caught:
                self.create(store, token, csrf, 'blocked', 'x' * 40, source=source_a)
            operation = self.create(store, token, csrf, 'other-source', 'x' * 40,
                                    source=source_b)[3]
        self.assertEqual('SOURCE_RATE_LIMIT', caught.exception.code)
        self.receive(data, operation)
        _, csrf, token = prep.session(store, None, create=True)
        with patch.object(prep, '_now', return_value=start + timedelta(hours=1)):
            operation = self.create(store, token, csrf, 'released', 'x' * 40,
                                    source=source_a)[3]
        self.assertIsNotNone(operation)
        stored = store._connection.execute('SELECT request_json FROM s2_actions WHERE dossier_id=?',
                                           ('released',)).fetchone()[0]
        self.assertNotIn('source_sha256', stored)

    def parallel_submissions(self, data, store, source, count, now):
        """`count` soumissions réellement simultanées, un `Store` par fil comme dans l'exécuteur"""
        identities = [prep.session(store, None, create=True) for _ in range(count)]
        outcomes, guard = [], threading.Lock()

        def submit(index):
            _, csrf, token = identities[index]
            with closing(storage.Store(data)) as own:
                try:
                    code = web_api.dispatch(own, 'POST', '/preparation/dossiers', token,
                                            {'csrf_token': csrf, 'dossier_id': f'c{index}',
                                             'action_id': f'c{index}', 'request': 'x' * 40,
                                             'source_sha256': source}, 'a' * 40, True)[0]
                except prep.Denied as denied:
                    code = denied.code or 'DENIED'
                except Exception as error:
                    code = type(error).__name__
            with guard:
                outcomes.append(code)

        with patch.object(prep, '_now', return_value=now):
            threads = [threading.Thread(target=submit, args=(index,)) for index in range(count)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
        self.assertEqual(count, len(outcomes))
        return outcomes

    def test_le_plafond_par_source_atteint_refuse_toutes_les_soumissions_paralleles(self):
        """Le plafond horaire par source reste effectif sous concurrence (BX-02)

        Le registre `_SOURCE_ACCEPTED` est un dictionnaire de module partagé par tous les fils de
        travail. Rempli au plafond, aucune soumission simultanée ne doit passer, et le balayage
        concurrent de la fenêtre glissante ne doit pas laisser le registre incohérent
        """
        data, store, _, _, _ = self.fixture(reserve='0')
        now = datetime(2026, 9, 21, 9, tzinfo=timezone.utc)
        source = 'c' * 64
        prep._SOURCE_ACCEPTED[source] = [now] * prep.SOURCE_HOURLY_MAX
        outcomes = self.parallel_submissions(data, store, source, 24, now)
        self.assertEqual({'SOURCE_RATE_LIMIT'}, set(outcomes))
        self.assertEqual(prep.SOURCE_HOURLY_MAX, len(prep._SOURCE_ACCEPTED[source]))

    def test_une_place_restante_n_admet_qu_un_seul_effet_simultane(self):
        """Une place sous le plafond : une seule soumission aboutit, les autres sont refusées

        Ce test ne prouve pas le plafond par source : le garde S2 « un seul effet à la fois »
        refuse les perdantes avant que `_source_limit` soit atteint, et le test reste vert si on
        retire le plafond. C'est `test_le_plafond_par_source_atteint_refuse_toutes_les_soumissions_paralleles`
        qui porte le critère de BX-02, et qui échoue sans le plafond. Le refus opérant est figé ici
        pour que personne ne relise ce vert comme une preuve du plafond
        """
        data, store, _, _, _ = self.fixture(reserve='0')
        now = datetime(2026, 9, 21, 9, tzinfo=timezone.utc)
        source = 'd' * 64
        prep._SOURCE_ACCEPTED[source] = [now] * (prep.SOURCE_HOURLY_MAX - 1)
        outcomes = self.parallel_submissions(data, store, source, 24, now)
        self.assertEqual(1, outcomes.count(202), outcomes)
        # Un refus de verrou SQLite rendrait le vert vide : seul le refus de domaine est admis
        self.assertEqual({202, 'PREPARATION_IN_PROGRESS'}, set(outcomes), outcomes)
        self.assertEqual(prep.SOURCE_HOURLY_MAX, len(prep._SOURCE_ACCEPTED[source]))


if __name__ == '__main__':
    unittest.main()
