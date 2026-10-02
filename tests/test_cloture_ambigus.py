"""Clôture automatique des opérations ambiguës : horloge simulée, HTTP simulé, aucun bouton

Modes d'échec couverts : ambigu de moins de 15 minutes débloqué trop tôt ; ambigu de 15 minutes ou plus
jamais clos (démarrage, POST, minuteur) ; coût remis à zéro ou réserve libérée ; appel relancé pour
l'opération close (double paiement) ; session toujours bloquée (préparation, vérification, jugement,
candidat) ; émission possible laissée par un plantage hors du chemin ; double clôture par un minuteur et
un POST concurrents ; texte absent, bouton ou renvoi à l'équipe ; restauration qui clôt des appels dont
l'envoi après sauvegarde n'est pas prouvé
"""
from contextlib import closing
from datetime import datetime, timedelta
import json
import multiprocessing
import threading
import time
import unittest
from unittest.mock import patch

from benchmark import model_probes, preparation as prep, runtime, service, storage, web_api
from benchmark.acquisition import campaigns, execution
from benchmark_web import views
from tests import test_automatic_judgment as judgment_fixture
from tests import test_model_probes as probes_fixture
from tests import test_openrouter_preparation as fixture
from tests.test_openrouter_preparation import CLARIFICATION, NEED
from tests.hermetique.sitecustomize import garder_module as setUpModule  # noqa: F401  réseau local seul, blocage borné

CLOSED_TEXT = 'Nous ne savons pas si le modèle a répondu ; l’opération a été close. Vous pouvez continuer.'
FIFTEEN = timedelta(minutes=15)


def operation(store, operation_id):
    return next(row for row in store.inspect_operations() if row['operation_id'] == operation_id)


class Preparation(unittest.TestCase):
    setUp = fixture.OpenRouterPreparationTests.setUp
    submit = fixture.OpenRouterPreparationTests.submit
    execute = fixture.OpenRouterPreparationTests.execute
    personal_transport = fixture.OpenRouterPreparationTests.personal_transport
    clock = fixture.OpenRouterPreparationTests.clock
    succeed = fixture.OpenRouterPreparationTests.succeed
    fail_with = fixture.OpenRouterPreparationTests.fail_with

    def ambiguous(self):
        self.http.request.side_effect = ConnectionResetError(54, 'Connection reset by peer')
        op, _ = self.execute()
        self.assertEqual('AMBIGUOUS', op['state'])
        self.http.request.side_effect = None
        return op

    def page(self):
        value = prep.view(self.store, self.session, 'd')
        value['availability'] = prep.availability(self.store, self.transport, self.session)
        return views.render(value, 'csrf', '/preparation/dossiers/d').decode()

    def test_moins_de_15_minutes_reste_bloquant(self):
        op = self.ambiguous()
        created = datetime.fromisoformat(op['created_at'])
        self.assertEqual([], self.store.close_expired_ambiguous(created + FIFTEEN - timedelta(seconds=1)))
        self.assertEqual('AMBIGUOUS', operation(self.store, op['operation_id'])['state'])
        self.assertEqual('interrupted', prep.availability(self.store, self.transport, self.session)['reason'])
        self.assertEqual([op['operation_id']], self.store.close_expired_ambiguous(created + FIFTEEN))

    def test_15_minutes_cloture_garde_le_cout_inconnu_et_la_reserve(self):
        op = self.ambiguous()
        before = self.store.inspect_budget('fixture')
        self.assertEqual(fixture.RESERVE, before['reserved'])
        now = datetime.fromisoformat(op['created_at']) + FIFTEEN
        self.assertEqual([op['operation_id']], self.store.close_expired_ambiguous(now))
        closed = operation(self.store, op['operation_id'])
        observed = closed['receipt']['observed_configuration']
        self.assertEqual(('RECEIVED', 'AMBIGUOUS_EXPIRED', 'UNKNOWN', None, None),
                         (closed['state'], observed['incident'], closed['observed_cost']['status'],
                          closed['observed_cost']['amount'], closed['receipt']['result']))
        self.assertEqual(op['ambiguity_reason'], observed['reason'])
        after = self.store.inspect_budget('fixture')
        self.assertEqual((fixture.RESERVE, '0', [op['operation_id']]),
                         (after['reserved'], after['spent'], after['unknown_cost_operations']))
        self.assertEqual('open', prep.availability(self.store, self.transport, self.session)['reason'])
        self.assertEqual(1, self.http.request.call_count)
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        # Idempotente : une seconde clôture ne trouve plus rien
        self.assertEqual([], self.store.close_expired_ambiguous(now + FIFTEEN))

    def test_session_debloquee_nouvelle_preparation_sans_relance_de_l_ancienne(self):
        op = self.ambiguous()
        self.store.close_expired_ambiguous(datetime.fromisoformat(op['created_at']) + FIFTEEN)
        self.succeed()
        following, _ = self.execute(action_id='suite', revision=1, kind='clarify', message=CLARIFICATION)
        self.assertEqual('RECEIVED', following['state'])
        self.assertEqual(2, self.http.request.call_count)
        self.assertEqual(1, len([o for o in self.store.inspect_operations() if o['operation_id'] == op['operation_id']]))
        self.assertIsNone(prep.retry_preparation(self.store, op['operation_id'], self.transport, 'a' * 40))
        self.assertEqual([], list(prep.due_preparation_retries(self.store)))
        self.assertEqual(2, self.http.request.call_count)

    def test_texte_visible_sans_bouton_ni_equipe(self):
        op = self.ambiguous()
        self.store.close_expired_ambiguous(datetime.fromisoformat(op['created_at']) + FIFTEEN)
        view = prep.view(self.store, self.session, 'd')
        self.assertEqual(CLOSED_TEXT, view['notice'])
        page = self.page()
        self.assertIn(CLOSED_TEXT, page)
        self.assertNotIn('équipe', page.lower())
        self.assertNotIn('Relancer', page)

    def test_post_clot_avant_de_refuser(self):
        advance = self.clock()
        op = self.ambiguous()
        # Date d'intention lue sur l'horloge réelle, à moins d'une seconde de l'horloge simulée
        advance(898)
        body = dict(csrf_token=self.csrf, action_id='trop-tot', revision=1, kind='clarify', message=CLARIFICATION,
                    source_sha256='a' * 64)
        with self.assertRaises(prep.Denied):
            web_api.dispatch(self.store, 'POST', '/preparation/dossiers/d/messages', self.token, body, 'a' * 40, self.transport)
        self.assertEqual('AMBIGUOUS', operation(self.store, op['operation_id'])['state'])
        advance(3)
        self.succeed()
        body['action_id'] = 'a-temps'
        self.assertEqual(202, web_api.dispatch(self.store, 'POST', '/preparation/dossiers/d/messages', self.token, body,
                                               'a' * 40, self.transport)[0])
        self.assertEqual('RECEIVED', operation(self.store, op['operation_id'])['state'])

    def test_minuteur_programme_a_l_echeance_puis_clot(self):
        advance = self.clock()
        op = self.ambiguous()
        advance(600)
        timers = {}
        retries = dict(preparation=(self.transport, None, None, 'a' * 40, timers))
        with patch.object(service.threading, 'Timer') as timer:
            service._resume_retries(self.store, self.data, retries)
        self.assertAlmostEqual(300, timer.call_args.args[0], delta=1.5)
        self.assertEqual('AMBIGUOUS', operation(self.store, op['operation_id'])['state'])
        advance(302)
        with patch.object(service.threading, 'Timer'):
            timer.call_args.args[1](*timer.call_args.kwargs['args'])
        self.assertEqual('RECEIVED', operation(self.store, op['operation_id'])['state'])
        self.assertEqual({}, timers)
        self.assertEqual(1, self.http.request.call_count)

    def test_demarrage_clot_un_ambigu_echu_sans_minuteur(self):
        advance = self.clock()
        op = self.ambiguous()
        advance(902)
        timers = {}
        with patch.object(service.threading, 'Timer') as timer:
            service._resume_retries(self.store, self.data, dict(preparation=(self.transport, None, None, 'a' * 40, timers)))
        timer.assert_not_called()
        self.assertEqual('RECEIVED', operation(self.store, op['operation_id'])['state'])

    def test_emission_possible_apres_plantage_suit_le_meme_chemin(self):
        pending = self.submit()
        self.store.mark_emission_possible(pending)
        now = datetime.fromisoformat(operation(self.store, pending)['created_at']) + FIFTEEN
        # Un appel peut encore être en cours dans ce processus : seul un ambigu est clos
        self.assertEqual([], self.store.close_expired_ambiguous(now))
        self.assertEqual('EMISSION_POSSIBLE', operation(self.store, pending)['state'])
        runtime.stop(self.data, self.store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        self.assertEqual('AMBIGUOUS', operation(self.store, pending)['state'])
        self.assertIn(pending, self.store.close_expired_ambiguous(now))
        closed = operation(self.store, pending)
        self.assertEqual(('RECEIVED', 'UNKNOWN'), (closed['state'], closed['observed_cost']['status']))
        self.assertEqual('open', prep.availability(self.store, self.transport, self.session)['reason'])

    def test_clotures_concurrentes_une_seule_part(self):
        op = self.ambiguous()
        now = datetime.fromisoformat(op['created_at']) + FIFTEEN
        found, barrier = [], threading.Barrier(2)

        def close():
            with closing(storage.Store(self.data)) as store:
                barrier.wait(5)
                found.append(store.close_expired_ambiguous(now))
        threads = [threading.Thread(target=close) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual([[], [op['operation_id']]], sorted(found))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_restauration_ne_clot_rien(self):
        op = self.ambiguous()
        (self.data / 'restore.json').write_text('{}')
        self.assertEqual([], self.store.close_expired_ambiguous(datetime.fromisoformat(op['created_at']) + 10 * FIFTEEN))
        self.assertEqual('AMBIGUOUS', operation(self.store, op['operation_id'])['state'])

    def test_executeur_tue_puis_redemarre_apres_15_minutes_clot_l_operation(self):
        context = multiprocessing.get_context('spawn')
        entered, clock = context.Event(), context.Value('d', time.time())
        sock = self.data.parent / 'executor.sock'
        children = []

        def cleanup():
            for process in children:
                if process.is_alive():
                    process.kill()
                process.join(5)
        self.addCleanup(cleanup)

        def start(gate=None):
            process = context.Process(target=fixture.executor_process, args=(self.data, sock, gate, clock))
            process.start()
            children.append(process)
            deadline = time.monotonic() + 10
            while True:
                try:
                    service.executor_health(sock)
                    return process
                except OSError:
                    if time.monotonic() > deadline:
                        self.fail('executor not ready')
                    time.sleep(.02)
        self.personal_transport()
        first = start(entered)
        self.assertEqual(202, service.preparation_request(sock, 'POST', '/preparation/dossiers', self.token,
            dict(csrf_token=self.csrf, dossier_id='d', action_id='plante', request=NEED, source_sha256='a' * 64))['status'])
        self.assertTrue(entered.wait(10))
        self.assertEqual('EMISSION_POSSIBLE', self.store.inspect_operations()[0]['state'])
        first.kill()
        first.join(5)
        clock.value += 14 * 60
        start()
        self.assertEqual('AMBIGUOUS', self.store.inspect_operations()[0]['state'])
        clock.value += 2 * 60
        # Redémarrage suivant : l'échéance est passée, l'opération est close au démarrage
        children[-1].kill()
        children[-1].join(5)
        start()
        closed = self.store.inspect_operations()[0]
        self.assertEqual(('RECEIVED', 'AMBIGUOUS_EXPIRED', 'UNKNOWN'),
                         (closed['state'], closed['receipt']['observed_configuration']['incident'],
                          closed['observed_cost']['status']))


class Evaluation(unittest.TestCase):
    """Jugements automatiques et tentatives candidates"""
    setUp = judgment_fixture.AutomaticJudgment.setUp
    acquire = judgment_fixture.AutomaticJudgment.acquire
    answer = judgment_fixture.AutomaticJudgment.answer

    def expire(self, ids):
        now = max(datetime.fromisoformat(o['created_at']) for o in self.store.inspect_operations()) + FIFTEEN
        return self.store.close_expired_ambiguous(now)

    def test_jugement_ambigu_clos_deblocke_l_evaluation_sans_rejouer(self):
        from benchmark import automatic_judgment as auto
        self.acquire()
        ids = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        self.http.request.side_effect = OSError('connexion coupée')
        auto.execute_campaign(self.data, ids, self.transport)
        self.assertEqual(1, self.http.request.call_count)
        with self.assertRaises(storage.BudgetError):
            auto.preflight(self.store, self.sid, 'fixture', self.cid, self.transport)
        self.assertEqual([ids[0]], self.expire(ids))
        closed = operation(self.store, ids[0])
        self.assertEqual(('RECEIVED', 'AMBIGUOUS_EXPIRED', 'UNKNOWN'),
                         (closed['state'], closed['receipt']['observed_configuration']['incident'],
                          closed['observed_cost']['status']))
        auto.guard_budget(self.store, self.store._connection, self.budget, campaign_id='nouvelle-comparaison')
        auto.preflight(self.store, self.sid, 'fixture', self.cid, self.transport)
        self.assertEqual(1, len([o for o in self.store.inspect_operations()
                                 if o['resources'] and o['phase'] == 'judgment'
                                 and json.loads(o['resources'][0])['request']['attempt_id']
                                 == json.loads(closed['resources'][0])['request']['attempt_id']]))
        self.assertEqual(CLOSED_TEXT, auto.status(self.store, self.store._connection, self.cid)['reason'][:len(CLOSED_TEXT)])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        # Le reste de l'évaluation (réponse jamais envoyée) reprend ; l'ambigu n'est jamais rejoué
        self.http.request.side_effect = self.answer
        calls = self.http.request.call_count
        following = auto.reserve_campaign(self.store, self.sid, 'fixture', self.cid, self.transport)
        auto.execute_campaign(self.data, following, self.transport)
        self.assertNotIn(ids[0], following)
        self.assertEqual(calls + len(following), self.http.request.call_count)

    def test_tentative_candidate_ambigue_close_deblocke_la_session(self):
        f = self.fixture
        ids = campaigns.launch(self.store, self.sid, 'fixture', self.cid, f.body(),
                               access_secret=SECRET, access_transport=f.access)
        calls = []

        def broken(op, request):
            calls.append(op['operation_id'])
            raise RuntimeError('reçu non vérifié')
        execution.execute_launch(self.data, ids, broken, access_secret=SECRET, access_transport=f.access)
        ambiguous = [o for o in self.store.inspect_operations() if o['state'] == 'AMBIGUOUS']
        self.assertEqual(calls, [o['operation_id'] for o in ambiguous])
        before = campaigns.inspect(self.store, self.cid)
        self.assertEqual('BLOCKED', before['state'])
        self.assertEqual(calls, self.expire(ids))
        after = campaigns.inspect(self.store, self.cid)
        attempt = next(a for a in after['attempts'] if a['operation_id'] == calls[0])
        self.assertEqual(('RECEIVED', 'AMBIGUOUS_EXPIRED', 'UNKNOWN'),
                         (attempt['state'], attempt['operation']['receipt']['result']['incident'],
                          attempt['operation']['observed_cost']['status']))
        self.assertEqual(1, len(calls))
        self.assertFalse(any(o['state'] in ('AMBIGUOUS', 'EMISSION_POSSIBLE') for o in self.store.inspect_operations()))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])
        authority = after['admissions'][-1]['authority']
        # L'enveloppe de la session n'est plus bloquée par l'attente de ce candidat
        budget = self.store.inspect_budget(authority['budget_id'])
        self.assertEqual([calls[0]], budget['unknown_cost_operations'])
        self.assertEqual(1, len(calls))


class ModelProbe(unittest.TestCase):
    """Vérification d'un modèle personnalisé : ambiguë, close après 15 minutes, jamais rejouée"""
    setUp = probes_fixture.ModelProbeTests.setUp
    fetch = probes_fixture.ModelProbeTests.fetch
    submit = probes_fixture.ModelProbeTests.submit
    post = probes_fixture.ModelProbeTests.post
    respond = probes_fixture.ModelProbeTests.respond

    def test_verification_ambigue_close_debloque_sans_rejouer(self):
        operation_id, key = self.submit()
        with patch.object(model_probes, 'post', side_effect=TimeoutError):
            model_probes.execute(self.data, operation_id, key)
        self.assertEqual('AMBIGUOUS', model_probes.view(self.store, self.session, 'fixture')[0]['status'])
        with self.assertRaises(prep.Denied):
            self.submit(action='retry')
        created = datetime.fromisoformat(operation(self.store, operation_id)['created_at'])
        self.assertEqual([], self.store.close_expired_ambiguous(created + FIFTEEN - timedelta(seconds=1)))
        self.assertEqual([operation_id], self.store.close_expired_ambiguous(created + FIFTEEN))
        closed = operation(self.store, operation_id)
        self.assertEqual(('RECEIVED', 'AMBIGUOUS_EXPIRED', 'UNKNOWN'),
                         (closed['state'], closed['receipt']['observed_configuration']['incident'],
                          closed['observed_cost']['status']))
        record = model_probes.view(self.store, self.session, 'fixture')[0]
        self.assertEqual((storage.AMBIGUOUS_EXPIRED, CLOSED_TEXT, False),
                         (record['status'], record['detail'], record['usable']))
        self.assertNotIn(probes_fixture.SLUG, [m['id'] for m in model_probes.selection(self.store, self.session, 'fixture')['models']])
        # Session débloquée : une nouvelle vérification part, l'opération close n'est jamais renvoyée
        retry_id, retry_key = self.submit(action='retry')
        self.assertNotEqual(operation_id, retry_id)
        self.respond(retry_id, retry_key)
        self.assertEqual(1, len(self.calls))
        self.assertIn(probes_fixture.SLUG, [m['id'] for m in model_probes.selection(self.store, self.session, 'fixture')['models']])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])


SECRET = judgment_fixture.SECRET

if __name__ == '__main__':
    unittest.main()
