"""S2 review regressions with fictional receipts and real local HTTP processes"""
from contextlib import closing
from copy import copy, deepcopy
from html.parser import HTMLParser
import multiprocessing
from pathlib import Path
import socket
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from benchmark import preparation as prep, service, storage
from benchmark_web.server import serve_web
from tests.hermetique.sitecustomize import garder_module as setUpModule  # noqa: F401  réseau local seul, blocage borné


def response_for(operation, *, unknown=False):
    return {'receipt': {'receipt_id': 'fictional-' + operation['operation_id'],
        'observed_configuration': {'model': 'fictional'}, 'resources_seen': [],
        'result': {'stage': 'preview', 'explanation': '', 'reformulation': 'Organiser les notes fictives',
            'fictional_parameters': {'atelier': 'inventé'},
            'package': {'candidate': {'instruction': 'Organiser les notes', 'deliverables': ['Liste des actions'],
                'criteria': ['Toutes les actions présentes'], 'acceptable_ambiguities': [],
                'pieces': [{'name': 'notes.txt', 'content': 'Action : relire'}]},
                'internal': {'human_work': 'Relire', 'limits': ['Exemple fictif']},
                'judgment': {'pieces': [{'name': 'reference.txt', 'content': 'Attendu fictif réservé'}]}}}},
        'cost': {'status': 'UNKNOWN' if unknown else 'KNOWN', 'amount': None if unknown else '3',
                 'currency': 'TEST', 'source': 'Fictional review receipt'}}


class Authorized:
    """Transport factice muni de l'autorité de sa session, comme la clé personnelle en production"""
    def __init__(self, transport, **authority):
        self._transport, self._authority = transport, authority
        self._session_id = authority['authority_id']

    def __call__(self, *args, **kwargs):
        return self._transport(*args, **kwargs)

    def __getattr__(self, name):
        # Copie et pickle interrogent l'objet avant `__init__` : aucun attribut privé n'est délégué
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self._transport, name)

    def authority(self):
        return deepcopy(self._authority)


class SessionAssistant:
    """Assistant factice que l'exécuteur lie à une session par sa clé, comme `OpenRouterPreparation.for_session`"""
    def __init__(self, call, configuration, reserve='1'):
        self._call, self._configuration, self._reserve = call, configuration, reserve
        self._session_id = self._api_key = None
        self.preparation_budget_id = None

    def for_session(self, key, session_id, secret):
        from benchmark.provider_access import preparation_budget_id
        bound = copy(self)
        bound._api_key, bound._session_id = key, session_id
        bound.preparation_budget_id = preparation_budget_id(session_id)
        return bound

    def authority(self):
        if self._session_id is None:
            return None
        return {'authority_id': 'requester:' + self._session_id, 'budget_id': self.preparation_budget_id,
                'reserve_amount': self._reserve, 'requested_configuration': self.quote()}

    def authorized(self, store):
        return self._session_id is not None

    def quote(self):
        return deepcopy(self._configuration)

    configuration = quote

    def controller(self):
        """Juge des témoins lié à la même session, sous sa propre réserve"""
        control = self._call.controller()
        bound = SessionAssistant(control, control.configuration(), control.reserve)
        bound._api_key, bound._session_id = self._api_key, self._session_id
        bound.preparation_budget_id = self.preparation_budget_id
        return bound

    def __call__(self, operation, request):
        return self._call(operation, request)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self._call, name)


class RefreshLink(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None
        self.refresh = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href = dict(attrs).get('href')

    def handle_data(self, data):
        if data == 'Actualiser':
            self.refresh = self.href


class S2ReviewRegressions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s2-fix-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.data = self.home / 'private'
        storage.initialize(self.data)
        storage.initialize_preparation(self.data)
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        self.store.create_budget('review', '100', 'TEST')
        self.authorized = lambda transport=True: Authorized(
            transport, authority_id='LOCAL_FICTIONAL_REVIEW', budget_id='review',
            reserve_amount='7', requested_configuration={'model': 'fictional'})
        self.session, self.csrf, self.token = prep.session(self.store, None, create=True)
        self.body = {'action_id': 'create', 'request': 'Organiser un atelier fictif'}
        self.operation, start = prep.submit(self.store, self.session, 'review-dossier', self.body, 'a' * 40,
                                            self.authorized())
        self.assertTrue(start)

    def test_invalid_application_retains_receipt_cost_and_suspends_atomically(self):
        # Each case gets its own envelope and operation, including late piece rejection
        for unknown in (False, True):
            for invalid in ('missing_criteria', 'empty_criteria', 'invalid_stage'):
                with self.subTest(unknown=unknown, invalid=invalid), tempfile.TemporaryDirectory() as temporary:
                    data = Path(temporary).resolve() / 'private'
                    storage.initialize(data)
                    storage.initialize_preparation(data)
                    with closing(storage.Store(data)) as store:
                        store.create_budget('review', '100', 'TEST')
                        session, _, _ = prep.session(store, None, create=True)
                        operation, _ = prep.submit(store, session, 'd', self.body, 'a' * 40, self.authorized())
                        original_payload = store.get_dossier('d', 1)
                        acquired = []
                        def transport(op, request):
                            response = response_for(op, unknown=unknown)
                            if invalid == 'missing_criteria':
                                del response['receipt']['result']['package']['candidate']['criteria']
                            elif invalid == 'empty_criteria':
                                response['receipt']['result']['package']['candidate']['criteria'] = []
                            else:
                                response['receipt']['result']['stage'] = 'invalid'
                            storage._receipt(response['receipt'], response['cost'])
                            acquired.append(deepcopy(response))
                            return response
                        record = storage.Store._record_receipt
                        atomic_checks = []
                        def observed_record(writer, connection, op, receipt, cost):
                            record(writer, connection, op, receipt, cost)
                            with closing(storage.Store(data)) as reader:
                                self.assertEqual('EMISSION_POSSIBLE', reader.inspect_operations()[0]['state'])
                                self.assertEqual(1, prep.view(reader, session, 'd')['revision'])
                                reader._connection.execute('PRAGMA busy_timeout=0')
                                with self.assertRaises(sqlite3.OperationalError):
                                    prep.submit(reader, session, 'other', self.body, 'a' * 40, self.authorized())
                            atomic_checks.append(True)
                        with patch.object(storage.Store, '_record_receipt', observed_record):
                            prep.execute(data, operation, self.authorized(transport))
                        observed = store.inspect_operations()[0]
                        self.assertEqual(acquired[0]['receipt'], observed['receipt'])
                        self.assertEqual(acquired[0]['cost'], observed['observed_cost'])
                        self.assertEqual('RECEIVED', observed['state'])
                        self.assertEqual([True], atomic_checks)
                        current = prep.view(store, session, 'd')
                        self.assertEqual('suspended', current['stage'])
                        self.assertIsNone(current['package'])
                        self.assertIsNone(current['validation'])
                        self.assertEqual(original_payload, current['payload'])
                        self.assertEqual(original_payload, store.get_dossier('d', 1))
                        self.assertEqual(acquired[0]['cost'], current['observed_cost'])
                        budget = store.inspect_budget('review')
                        self.assertEqual('0' if unknown else '3', budget['spent'])
                        self.assertEqual('7' if unknown else '0', budget['reserved'])
                        with self.assertRaises(storage.ConflictError):
                            prep.validate(store, session, 'd', prep.binding('d', current['revision'], '0' * 64))
                        # La suspension vise ce résultat : une correction explicite reste possible
                        self.assertTrue(prep.submit(store, session, 'd', dict(action_id='next', revision=current['revision'],
                                        kind='correct', message='Corriger'), 'a' * 40, self.authorized())[1])
                        self.assertEqual((operation, False), prep.submit(store, session, 'd', self.body, 'a' * 40,
                                                                         self.authorized()))
                        prep.execute(data, operation, self.authorized(transport))
                        self.assertEqual(1, len(acquired))
                        self.assertTrue(store.verify_storage()['integrity_ok'])
                        self.assertEqual([], store._connection.execute('SELECT * FROM pieces').fetchall())

    def test_invalid_receipt_does_not_become_received(self):
        def transport(operation, request):
            result = response_for(operation)
            del result['receipt']['receipt_id']
            return result
        prep.execute(self.data, self.operation, self.authorized(transport))
        operation = self.store.inspect_operations()[0]
        self.assertEqual('AMBIGUOUS', operation['state'])
        self.assertIsNone(operation['receipt'])
        self.assertIsNone(operation['observed_cost'])
        self.assertEqual(1, prep.view(self.store, self.session, 'review-dossier')['revision'])
        self.assertEqual('7', self.store.inspect_budget('review')['reserved'])

    def test_real_post_validation_then_refresh_get_does_not_revalidate(self):
        prep.execute(self.data, self.operation, self.authorized(lambda operation, request: response_for(operation)))
        view = prep.view(self.store, self.session, 'review-dossier')
        self.assertEqual('preview', view['stage'])
        # La validation passe par les assistants liés à la clé de la session, comme en production
        from benchmark import evaluation, provider_access, qualification
        from benchmark.acquisition import campaigns
        from tests.test_openrouter_qualification import QualificationTransport
        from tests.test_provider_access import AccessTransport, KEY, SECRET
        for module in (qualification, campaigns, evaluation, provider_access):
            module.initialize(self.data)
        provider_access.import_key(self.store, self.session, SECRET, KEY, AccessTransport())
        assistants = dict(
            transport=SessionAssistant(QualificationTransport({}), {'model': 'fictional'}),
            qualification_transport=SessionAssistant(
                QualificationTransport({'qualified': True, 'findings': [], 'summary': 'Exemple qualifié'}),
                {'model': 'qualification/fictive'}),
            access_secret=SECRET, access_transport=AccessTransport())
        public = self.home / 'public'
        public.mkdir()
        sock = self.home / 'executor.sock'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        context = multiprocessing.get_context('spawn')
        children = []
        def stop_children():
            for process in reversed(children):
                if process.is_alive():
                    process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join()
        self.addCleanup(stop_children)
        for target, args in ((service.serve_executor, (self.data, sock, 'a' * 40)),
                             (serve_web, ('127.0.0.1', port, public, sock, 'a' * 40))):
            process = context.Process(target=target, args=args,
                                      kwargs={'readiness_clients': ('127.0.0.1',)} if target is serve_web else assistants)
            process.start()
            children.append(process)
        base = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 5
        while True:
            try:
                with urlopen(base + '/readyz', timeout=2) as result:
                    self.assertEqual(200, result.status)
                break
            except OSError as error:
                if isinstance(error, HTTPError):
                    error.close()
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
        # HTTP protocol fixture only, not a Secure-cookie or HTTPS browser proof
        headers = {'Cookie': 'benchmark_session=' + self.token, 'Content-Type': 'application/x-www-form-urlencoded'}
        body = {**prep.binding('review-dossier', view['revision'], view['package_sha256']), 'csrf_token': self.csrf}
        with urlopen(Request(base + '/preparation/dossiers/review-dossier/validation',
                             data=urlencode(body).encode(), headers=headers), timeout=5) as result:
            self.assertEqual(200, result.status)
            html = result.read().decode()
        self.assertIn('Vous avez validé cet exemple, dans cette version précise.', html)
        parser = RefreshLink()
        parser.feed(html)
        self.assertIsNotNone(parser.refresh)
        before = self.store._connection.execute('SELECT * FROM s2_validations').fetchall()
        self.assertEqual(1, len(before))
        operations = len(self.store.inspect_operations())
        try:
            result = urlopen(Request(base + parser.refresh, headers=headers), timeout=5)
        except HTTPError as error:
            result = error
        with result:
            self.assertEqual(200, result.status)
            self.assertIn('Vous avez validé cet exemple, dans cette version précise.', result.read().decode())
        self.assertEqual('/preparation/dossiers/review-dossier', parser.refresh)
        self.assertEqual(before, self.store._connection.execute('SELECT * FROM s2_validations').fetchall())
        self.assertEqual(operations, len(self.store.inspect_operations()))


if __name__ == '__main__':
    unittest.main()
