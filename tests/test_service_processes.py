"""Preuve avec deux vrais processus et une socket locale, sans fournisseur."""
from collections import Counter
from contextlib import closing, contextmanager
from email.message import Message
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
import ipaddress
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from benchmark import preparation, provider_access, service
from benchmark.service import denied_response, executor_health, preparation_request, serve_executor
from benchmark.storage import BudgetError, ConflictError, IntegrityError, Store, initialize, _strict_json
from benchmark_web.server import _source_fingerprint, serve_web
from tests.test_storage import PAYLOAD, operation
from tests import test_model_catalogue as catalogue_fixture


def catalogue_executor(data, sock, fetching, release, completed):
    from benchmark import model_catalogue
    calls = []
    fixture_fetch = catalogue_fixture.ModelCatalogueTests().fetch(calls)

    def fetch(path):
        if path == '/api/v1/models':
            fetching.set()
            if not release.wait(5):
                raise TimeoutError('Relevé factice non libéré')
        return fixture_fetch(path)

    with patch.object(model_catalogue, '_now', return_value=catalogue_fixture.NOW):
        serve_executor(data, sock, 'a' * 40, catalogue_fetch=fetch)
    completed.put(calls)


@contextmanager
def fake_executor(path, respond):
    """Exécuteur fictif d'une seule requête : le test décide du rythme de la réponse"""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
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
            yield
        finally:
            worker.join(5)
            Path(path).unlink(missing_ok=True)


@contextmanager
def service_lance(data, source='a' * 40):
    """Web et exécuteur réels, prêts quand les deux sondes répondent"""
    public, sock = data.parent / 'public', data.parent / 'x.sock'
    public.mkdir()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    context = multiprocessing.get_context('spawn')
    children = [context.Process(target=serve_executor, args=(data, sock, source)),
                context.Process(target=serve_web, args=('127.0.0.1', port, public, sock, source),
                                kwargs={'readiness_clients': ('127.0.0.1',)})]
    for child in children:
        child.start()
    base = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 8
        while True:
            try:
                if executor_health(sock)['storage'] == 'ok':
                    with urlopen(base + '/healthz', timeout=2) as response:
                        response.read()
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        yield base
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(5)


class ServiceProcessesTests(unittest.TestCase):
    def test_web_liveness_responds_while_deep_readiness_waits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            public, sock = root / 'public', root / 'executor.sock'
            public.mkdir()
            started, release = threading.Event(), threading.Event()

            def delayed_health(connection):
                started.set()
                release.wait(3)
                connection.sendall((_strict_json({'source_sha': 'a' * 40, 'storage': 'ok',
                    'admission': False, 'restore_pending': False, 'operations': {}}) + '\n').encode())

            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            context = multiprocessing.get_context('spawn')
            web = context.Process(target=serve_web,
                                  args=('127.0.0.1', port, public, sock, 'a' * 40),
                                  kwargs={'readiness_clients': ('127.0.0.1',)})
            with fake_executor(sock, delayed_health):
                web.start()
                try:
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            with urlopen(f'http://127.0.0.1:{port}/healthz', timeout=1):
                                break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise
                            time.sleep(.02)
                    waiting = threading.Thread(target=lambda: urlopen(
                        f'http://127.0.0.1:{port}/readyz', timeout=4).read())
                    waiting.start()
                    self.assertTrue(started.wait(2))
                    with urlopen(f'http://127.0.0.1:{port}/healthz', timeout=1) as response:
                        self.assertEqual(200, response.status)
                    release.set()
                    waiting.join(4)
                    self.assertFalse(waiting.is_alive())
                finally:
                    release.set()
                    if web.is_alive():
                        web.terminate()
                    web.join(5)

    def test_catalogue_outage_waits_before_retry_and_stops_between_requests(self):
        from benchmark import model_catalogue
        from benchmark.storage import initialize_preparation
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory).resolve() / 'private'
            initialize(data)
            initialize_preparation(data)
            stopping = threading.Event()
            delays, calls = [], []
            fixture_fetch = catalogue_fixture.ModelCatalogueTests().fetch(calls)
            attempts = 0

            def fetch(path):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError('Indisponibilité fictive')
                return fixture_fetch(path)

            def wait(seconds):
                delays.append(seconds)
                if len(delays) == 2:
                    stopping.set()

            with patch.object(model_catalogue, '_now', return_value=catalogue_fixture.NOW), \
                    patch.object(stopping, 'wait', side_effect=wait), \
                    self.assertLogs('benchmark.service', level='WARNING') as logs:
                service._refresh_catalogue(data, stopping, fetch)
            self.assertEqual([3600, 3600], delays)
            self.assertEqual(['WARNING:benchmark.service:CATALOGUE_UNAVAILABLE'], logs.output)
            with closing(Store(data)) as store:
                self.assertTrue(model_catalogue.selection(store)['models'])

            stopping.clear()
            calls.clear()
            def stop_during_fetch(path):
                stopping.set()
                return fixture_fetch(path)
            with patch.object(model_catalogue, '_now', return_value=catalogue_fixture.NOW.replace(day=16)):
                service._refresh_catalogue(data, stopping, stop_during_fetch)
            self.assertEqual(['/api/v1/models'], calls)
            with closing(Store(data)) as store:
                self.assertEqual(catalogue_fixture.NOW.isoformat(), model_catalogue.selection(store)['fetched_at'])

    def test_catalogue_initializes_without_blocking_health_and_stops_cleanly(self):
        from benchmark import model_catalogue
        with tempfile.TemporaryDirectory() as directory:
            data, sock = Path(directory).resolve() / 'private', Path(directory).resolve() / 'executor.sock'
            initialize(data)
            from benchmark.storage import initialize_preparation
            initialize_preparation(data)
            context = multiprocessing.get_context('spawn')
            fetching, release, completed = context.Event(), context.Event(), context.Queue()
            child = context.Process(target=catalogue_executor, args=(data, sock, fetching, release, completed))
            child.start()
            try:
                self.assertTrue(fetching.wait(5))
                self.assertEqual('ok', executor_health(sock)['storage'])
                with closing(Store(data)) as store:
                    with self.assertRaises(LookupError):
                        model_catalogue.selection(store)
                    release.set()
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            result = model_catalogue.selection(store)
                            break
                        except LookupError:
                            if time.monotonic() >= deadline:
                                raise
                            time.sleep(.01)
                    self.assertTrue(result['models'])
                self.assertEqual('ok', executor_health(sock)['storage'])
                child.terminate()
                child.join(5)
                self.assertEqual(0, child.exitcode)
                calls = completed.get(timeout=1)
                self.assertEqual(1, calls.count('/api/v1/models'))
                self.assertEqual(6, len(calls))
            finally:
                release.set()
                if child.is_alive():
                    child.kill()
                child.join(5)

    def test_refus_inconnu_reste_generique(self):
        generic = ('Cette action n’est pas autorisée pour votre session. Retrouvez votre dossier '
                   'ou demandez au responsable de vérifier son autorisation.')
        with patch('socket.socket.connect', side_effect=AssertionError('No network')):
            responses = [denied_response(preparation.Denied(reason))
                         for reason in ('Motif', 'FUTUR')]
        self.assertEqual([403, 403], [response['status'] for response in responses])
        self.assertEqual([generic, generic],
                         [response['value']['error'] for response in responses])

    def test_deux_identites_inconnues_ne_sont_pas_pretes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            data, public, sock = root / 'private', root / 'public', root / 'executor.sock'
            public.mkdir()
            initialize(data)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            context = multiprocessing.get_context('spawn')
            children = [context.Process(target=serve_executor, args=(data, sock, 'inconnu')),
                        context.Process(target=serve_web,
                                        args=('127.0.0.1', port, public, sock, 'inconnu'),
                                        kwargs={'readiness_clients': ('127.0.0.1',)})]
            try:
                for child in children:
                    child.start()
                deadline = time.monotonic() + 5
                while True:
                    try:
                        health = executor_health(sock)
                        if health['storage'] == 'ok':
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.02)
                while True:
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/readyz', timeout=2) as response:
                            status = response.status
                            body = json.load(response)
                        break
                    except HTTPError as error:
                        status = error.code
                        body = json.load(error)
                        error.close()
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.02)
                self.assertEqual(503, status)
                self.assertEqual('inconnu', body['source_sha'])
            finally:
                for child in children:
                    if child.is_alive():
                        child.terminate()
                    child.join(5)

    def test_web_source_fingerprint_precedence_and_invalid_bucket(self):
        salt = b's' * 32
        proxies = frozenset({ipaddress.ip_address('127.0.0.1')})
        headers = Message()
        headers['X-Real-IP'] = '2001:db8:1:2::9'
        headers['X-Forwarded-For'] = '192.0.2.1, 192.0.2.2'
        first = _source_fingerprint(headers, ('127.0.0.1', 1), salt, proxies)
        same_prefix = Message()
        same_prefix['X-Real-IP'] = '2001:db8:1:2::ffff'
        self.assertEqual(first, _source_fingerprint(same_prefix, ('127.0.0.1', 1), salt, proxies))
        other = Message()
        other['X-Real-IP'] = '2001:db8:1:3::1'
        self.assertNotEqual(first, _source_fingerprint(other, ('127.0.0.1', 1), salt, proxies))
        forwarded = Message()
        forwarded['X-Forwarded-For'] = '192.0.2.1, 192.0.2.2'
        direct = Message()
        direct['X-Real-IP'] = '192.0.2.1'
        self.assertEqual(_source_fingerprint(forwarded, ('127.0.0.1', 1), salt, proxies),
                         _source_fingerprint(direct, ('127.0.0.1', 1), salt, proxies))
        invalid = Message()
        invalid['X-Real-IP'] = 'illisible'
        another_invalid = Message()
        another_invalid['X-Real-IP'] = ''
        self.assertEqual(_source_fingerprint(invalid, ('127.0.0.1', 1), salt, proxies),
                         _source_fingerprint(another_invalid, ('127.0.0.1', 1), salt, proxies))

    def test_web_source_fingerprint_ignores_proxy_headers_from_untrusted_peer(self):
        salt = b's' * 32
        proxies = frozenset({ipaddress.ip_address('192.0.2.30')})
        peer = ('198.51.100.7', 1)
        direct = _source_fingerprint(Message(), peer, salt, proxies)
        for name, value in (('X-Real-IP', '203.0.113.1'), ('X-Real-IP', '203.0.113.2'),
                            ('X-Forwarded-For', '203.0.113.3, 192.0.2.30'), ('X-Real-IP', 'illisible')):
            forged = Message()
            forged[name] = value
            self.assertEqual(direct, _source_fingerprint(forged, peer, salt, proxies))
            self.assertEqual(direct, _source_fingerprint(forged, peer, salt, frozenset()))
        relayed = Message()
        relayed['X-Real-IP'] = '198.51.100.7'
        # Le proxy peut arriver en adresse IPv4 mappée : c'est la même machine
        self.assertEqual(direct, _source_fingerprint(relayed, ('::ffff:192.0.2.30', 1), salt, proxies))
        self.assertNotEqual(direct, _source_fingerprint(Message(), ('192.0.2.30', 1), salt, proxies))

    def test_honeypot_acknowledges_without_executor_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = root / 'public'
            public.mkdir()
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            worker = multiprocessing.get_context('spawn').Process(
                target=serve_web,
                args=('127.0.0.1', port, public, root / 'absent.sock', 'a' * 40))
            worker.start()
            self.addCleanup(lambda: worker.is_alive() and worker.terminate())
            request = Request(f'http://127.0.0.1:{port}/preparation/dossiers',
                              data=urlencode({'website': 'robot.example'}).encode(),
                              headers={'Content-Type': 'application/x-www-form-urlencoded'})
            deadline = time.monotonic() + 5
            while True:
                try:
                    with urlopen(request, timeout=2) as response:
                        self.assertEqual(200, response.status)
                        page = response.read().decode()
                        self.assertIn('<title>Demande enregistrée', page)
                        self.assertIn('L’envoi a été enregistré', page)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.02)
            worker.terminate()
            worker.join(5)

    def test_private_read_can_finish_after_two_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'executor.sock'
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                server.listen(1)
                server.settimeout(5)

                def respond():
                    with server.accept()[0] as connection:
                        connection.recv(4096)
                        time.sleep(2.1)
                        try:
                            connection.sendall(b'{"status":200,"value":{}}\n')
                        except BrokenPipeError:
                            pass

                worker = threading.Thread(target=respond)
                worker.start()
                try:
                    self.assertEqual(200, preparation_request(path, 'GET', '/preparation', None)['status'])
                finally:
                    worker.join(5)
                self.assertFalse(worker.is_alive())

    def test_budget_de_relais_derive_du_budget_fournisseur(self):
        # Un rappel enchaîne échange puis vérification : le relais doit couvrir les deux
        self.assertEqual(provider_access.CALLBACK_BUDGET_SECONDS + service.LOCAL_BUDGET_SECONDS,
                         service.RELAY_BUDGET_SECONDS)
        self.assertGreater(service.RELAY_BUDGET_SECONDS, provider_access.CALLBACK_BUDGET_SECONDS)

    def test_relais_tolere_un_echange_lent_puis_expire_hors_budget(self):
        def slow(connection):
            time.sleep(.3)
            connection.sendall(b'{"status":200,"value":{}}\n')

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'executor.sock'
            with patch.object(service, 'RELAY_BUDGET_SECONDS', 1.5), fake_executor(path, slow):
                self.assertEqual(200, preparation_request(path, 'GET', '/preparation', None)['status'])
            with patch.object(service, 'RELAY_BUDGET_SECONDS', .1), fake_executor(path, slow):
                started = time.monotonic()
                with self.assertRaises(OSError):
                    preparation_request(path, 'GET', '/preparation', None)
                self.assertLess(time.monotonic() - started, 1)

    def test_le_budget_de_relais_borne_un_total_et_non_une_inactivite(self):
        def drip(connection):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                connection.sendall(b'x')
                time.sleep(.05)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'executor.sock'
            with patch.object(service, 'RELAY_BUDGET_SECONDS', .4), fake_executor(path, drip):
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    preparation_request(path, 'GET', '/preparation', None)
                self.assertLess(time.monotonic() - started, 2)

    def test_reponse_d_executeur_illisible_est_une_panne_de_transport(self):
        # Une trame fautive n'est pas une saisie fautive : l'appelant doit voir une panne
        wires = (b'not-json\n', b'{"status":200,"value":{}}', b'[1,2]\n',
                 b'{"value":{}}\n', b'{"status":"200","value":{}}\n', b'{"status":200}\n',
                 # Valeur ordinaire non objet : le web la rendrait en 200 nul ou romprait la page
                 b'{"status":200,"value":null}\n', b'{"status":200,"value":[1,2]}\n',
                 b'{"status":200,"value":"texte"}\n', b'{"status":200,"value":7}\n',
                 # Pièce annoncée sans chaîne hexadécimale exploitable
                 b'{"status":200,"value":null,"piece":true}\n',
                 b'{"status":200,"value":"zz","piece":true}\n',
                 b'{"status":200,"value":"abc","piece":true}\n',
                 b'{"status":200,"value":"61 62","piece":true}\n',
                 b'{"status":200,"value":{},"piece":true}\n',
                 # Champs facultatifs mal typés
                 b'{"status":200,"value":{},"piece":"true"}\n',
                 b'{"status":200,"value":{},"piece":null}\n',
                 b'{"status":200,"value":{},"cookie":5}\n',
                 b'{"status":200,"value":{},"cookie":{"a":1}}\n',
                 # Statut hors de la plage des réponses finales
                 b'{"status":100,"value":{}}\n', b'{"status":0,"value":{}}\n',
                 b'{"status":-200,"value":{}}\n', b'{"status":600,"value":{}}\n',
                 b'{"status":999,"value":{}}\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'executor.sock'
            for wire in wires:
                with fake_executor(path, lambda connection: connection.sendall(wire)):
                    with self.assertRaises(ConnectionError, msg=wire):
                        preparation_request(path, 'GET', '/preparation', None)

    def test_reponse_d_executeur_conforme_est_rendue_telle_quelle(self):
        # Les champs facultatifs omis par une enveloppe d'erreur le restent après validation
        wires = (
            b'{"status":404,"value":{"error":"NOT_FOUND"},"piece":false,"cookie":null}\n',
            b'{"status":403,"value":{"error":"Motif","error_code":"TOO_SOON"}}\n',
            b'{"status":500,"value":{"error":"Panne"}}\n',
            b'{"status":200,"value":"48656c6c6f","piece":true,"cookie":null}\n',
            b'{"status":200,"value":"48656C6C6F","piece":true}\n',
            b'{"status":200,"value":"","piece":true}\n',
            b'{"status":201,"value":{"kind":"configurations"},"cookie":"jeton"}\n',
            b'{"status":599,"value":{"error":"Panne"}}\n')
        expected = (
            {'status': 404, 'value': {'error': 'NOT_FOUND'}, 'piece': False, 'cookie': None},
            {'status': 403, 'value': {'error': 'Motif', 'error_code': 'TOO_SOON'}},
            {'status': 500, 'value': {'error': 'Panne'}},
            {'status': 200, 'value': '48656c6c6f', 'piece': True, 'cookie': None},
            {'status': 200, 'value': '48656C6C6F', 'piece': True},
            {'status': 200, 'value': '', 'piece': True},
            {'status': 201, 'value': {'kind': 'configurations'}, 'cookie': 'jeton'},
            {'status': 599, 'value': {'error': 'Panne'}})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'executor.sock'
            for wire, result in zip(wires, expected):
                with fake_executor(path, lambda connection: connection.sendall(wire)):
                    self.assertEqual(result, preparation_request(path, 'GET', '/preparation', None),
                                     msg=wire)

    def test_frontiere_separe_refus_validation_et_defaillance_interne(self):
        canary = 'sk-or-canari-a-ne-jamais-sortir'
        envelope = b'{"method":"GET","path":"/preparation","token":null,"body":null}\n'
        health = {'source_sha': 'a' * 40, 'storage': 'ok', 'admission': False,
                  'restore_pending': False, 'operations': {}}

        def raising(error):
            def handle(message):
                raise error
            return handle

        def refused(raw, error):
            return service.executor_result(raw, lambda: health, raising(error))

        self.assertEqual(health, service.executor_result(b'health\n', lambda: health, raising(
            AssertionError('jamais appelé'))))
        self.assertEqual(403, refused(envelope, preparation.Denied('Motif'))['status'])
        self.assertEqual(400, refused(envelope, preparation.Denied('TEXT_TOO_SHORT'))['status'])
        for error in (ConflictError(canary), BudgetError(canary)):
            self.assertEqual(409, refused(envelope, error)['status'])
        # Validation de domaine : la requête est recevable, son contenu non
        result = refused(envelope, ValueError(canary))
        self.assertEqual(400, result['status'])
        self.assertEqual(service.BAD_REQUEST_MESSAGE, result['value']['error'])
        self.assertNotIn(canary, _strict_json(result))
        # Enveloppe : structure, puis type de chaque champ du contrat
        for raw in (b'{"method":"GET"}\n', b'ceci n\'est pas du json\n', b'[1,2]\n', b'\n',
                    b'{"method":"GET","path":"/p","token":null,"body":null}',
                    b'{"method":1,"path":"/p","token":null,"body":null}\n',
                    b'{"method":"GET","path":["/p"],"token":null,"body":null}\n',
                    b'{"method":"GET","path":"/p","token":7,"body":null}\n',
                    b'{"method":"GET","path":"/p","token":null,"body":"texte"}\n',
                    b'{"method":"GET","path":"/p","token":null,"body":[1]}\n'):
            result = refused(raw, AssertionError('jamais appelé'))
            self.assertEqual(400, result['status'], raw)
            self.assertEqual(service.BAD_REQUEST_MESSAGE, result['value']['error'])
        # Après une enveloppe valide, une erreur de programmation n'est plus une entrée fautive
        for error, code in ((sqlite3.OperationalError(canary), 'STORAGE'),
                            (IntegrityError(canary), 'STORAGE'),
                            (OSError(canary), 'UNEXPECTED'),
                            (KeyError(canary), 'UNEXPECTED'),
                            (TypeError(canary), 'UNEXPECTED'),
                            (AttributeError(canary), 'UNEXPECTED')):
            with self.assertLogs('benchmark.service', level='ERROR') as logs:
                result = refused(envelope, error)
            self.assertEqual(500, result['status'])
            self.assertEqual(service.INTERNAL_MESSAGE, result['value']['error'])
            self.assertNotIn(canary, _strict_json(result))
            self.assertNotIn(canary, '\n'.join(logs.output))
            self.assertIn('EXECUTOR_INTERNAL ' + code + ' ' + type(error).__name__, logs.output[0])
        # Le contrôle de santé n'a pas d'entrée à mettre en cause : toute rupture y est interne
        for error in (ValueError(canary), KeyError(canary), sqlite3.OperationalError(canary)):
            def failing():
                raise error

            with self.assertLogs('benchmark.service', level='ERROR') as logs:
                result = service.executor_result(b'health\n', failing, raising(
                    AssertionError('jamais appelé')))
            self.assertEqual(500, result['status'])
            self.assertEqual(service.INTERNAL_MESSAGE, result['value']['error'])
            self.assertNotIn(canary, _strict_json(result))
            self.assertNotIn(canary, '\n'.join(logs.output))
            self.assertIn('EXECUTOR_INTERNAL HEALTH ' + type(error).__name__, logs.output[0])

    def test_message_interne_n_affirme_aucun_resultat(self):
        # L'effet peut être enregistré avant la défaillance : n'annoncer qu'un état non confirmé
        self.assertNotIn('abouti', service.INTERNAL_MESSAGE)
        self.assertIn('n’est pas confirmé', service.INTERNAL_MESSAGE)
        self.assertIn('Consultez le dossier', service.INTERNAL_MESSAGE)

    def test_enveloppe_malformee_recoit_un_refus_explicite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            data, sock = root / 'private', root / 'executor.sock'
            initialize(data)
            child = multiprocessing.get_context('spawn').Process(
                target=serve_executor, args=(data, sock, 'a' * 40))
            child.start()
            try:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        executor_health(sock)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.02)
                for raw in (b'{"method":"GET"}\n', b'ceci n\'est pas du json\n', b'[1,2]\n'):
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                        connection.settimeout(5)
                        connection.connect(str(sock))
                        connection.sendall(raw)
                        with connection.makefile('rb') as stream:
                            answer = json.loads(stream.readline())
                    self.assertEqual(400, answer['status'], raw)
                    self.assertEqual(service.BAD_REQUEST_MESSAGE, answer['value']['error'])
                self.assertEqual('ok', executor_health(sock)['storage'])
            finally:
                child.terminate()
                child.join(5)

    def test_health_restart_and_private_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'release'
            root.mkdir()
            shutil.copytree(Path(__file__).resolve().parents[1] / 'benchmark', root / 'benchmark')
            shutil.copytree(Path(__file__).resolve().parents[1] / 'benchmark_web', root / 'benchmark_web')
            (root / 'release.json').write_text(json.dumps({'source_sha': 'a' * 40}))
            data, public = root.parent / 'private', root.parent / 'public'
            public.mkdir()
            initialize(data)
            with closing(Store(data)) as store:
                store.save_dossier('d', 1, {**PAYLOAD, 'request': 'must never be public'})
                store.create_budget('test', '1', 'TEST')
                store.reserve_intent(operation('attempt'), 'test', '1')
                store.mark_emission_possible('attempt')
            sock = root / 'executor.sock'
            command = [sys.executable, '-B', '-m', 'benchmark.runtime']
            children = []
            try:
                executor = subprocess.Popen(command + ['executor', '--data', str(data), '--socket', str(sock)], cwd=root)
                children.append(executor)
                deadline = time.monotonic() + 5
                while True:
                    try:
                        health = executor_health(sock)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.02)
                self.assertFalse(health['admission'])
                self.assertEqual({'AMBIGUOUS': 1}, health['operations'])
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', 0))
                    port = probe.getsockname()[1]
                web = subprocess.Popen(command + ['web', '--public', str(public), '--socket', str(sock), '--port', str(port),
                                                  '--readyz-client', '127.0.0.1'], cwd=root)
                children.append(web)
                base = f'http://127.0.0.1:{port}'
                deadline = time.monotonic() + 5
                while True:
                    try:
                        with urlopen(base + '/readyz', timeout=2) as response:
                            self.assertEqual('ok', json.load(response)['storage'])
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.02)
                for path, code in [('/private/metadata.sqlite3', 404), ('/../private/metadata.sqlite3', 404), ('/index.html', 404)]:
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(base + path, timeout=2)
                    self.assertEqual(code, rejected.exception.code)
                    self.assertNotIn(b'must never be public', rejected.exception.read())
                    rejected.exception.close()
                def check_home():
                    with urlopen(base + '/', timeout=2) as response:
                        self.assertEqual(200, response.status)
                        self.assertIn('text/html', response.headers['Content-Type'])
                        self.assertEqual('no-store', response.headers['Cache-Control'])
                        self.assertEqual('nosniff', response.headers['X-Content-Type-Options'])
                        self.assertIsNone(response.headers.get('Set-Cookie'))
                        home = response.read()
                        self.assertIn(b'href="/preparation"', home)
                        self.assertIn(b'href="/index.html"', home)
                        self.assertNotIn(b'must never be public', home)
                    return home

                home = check_home()
                page = b'<!doctype html><title>Approved fixture</title>'
                manifest = json.dumps({'files': {'index.html': sha256(page).hexdigest()}}).encode()
                publication = sha256(manifest).hexdigest()
                projection = public / publication
                projection.mkdir()
                (projection / 'publication.json').write_bytes(manifest)
                (projection / 'index.html').write_bytes(page)
                (public / 'active.json').write_text(json.dumps({'directory': publication}))
                with urlopen(base + '/index.html', timeout=2) as response:
                    self.assertEqual(page, response.read())
                self.assertEqual(home, check_home())
                (projection / 'index.html').write_bytes(b'tampered')
                with self.assertRaises(HTTPError) as corrupt:
                    urlopen(base + '/index.html', timeout=2)
                self.assertEqual(404, corrupt.exception.code)
                corrupt.exception.close()
                self.assertEqual(home, check_home())
                executor.terminate()
                self.assertEqual(0, executor.wait(timeout=5))
                with self.assertRaises(HTTPError) as unavailable:
                    urlopen(base + '/readyz', timeout=2)
                self.assertEqual(503, unavailable.exception.code)
                unavailable.exception.close()
                with urlopen(base + '/healthz', timeout=2) as response:
                    self.assertEqual('ok', json.load(response)['web'])
                self.assertEqual(home, check_home())
                restarted = subprocess.Popen(command + ['executor', '--data', str(data), '--socket', str(sock)], cwd=root)
                children.append(restarted)
                deadline = time.monotonic() + 5
                while True:
                    try:
                        health = executor_health(sock)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.02)
                self.assertEqual({'AMBIGUOUS': 1}, health['operations'])
                self.assertFalse(health['admission'])
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=5)

    def test_readyz_avec_pieces_reste_du_meme_ordre_que_healthz(self):
        """`/readyz` reste du même ordre de grandeur que `/healthz` sur un stockage qui contient une pièce

        Les durées sont en millisecondes. Cinq appels, puis la médiane. Le plancher de 1 ms
        évite qu'un `/healthz` sous la milliseconde exige une sonde plus rapide que
        l'aller-retour local. La sonde doit aussi rester sous le cinquième de `verify` :
        un `/healthz` lent ne peut pas masquer le retour du re-hachage.
        """
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory).resolve() / 'private'
            initialize(data)
            with closing(Store(data)) as store:
                store.save_dossier('d', 1, PAYLOAD)
                store.put_piece('d', 1, 'piece', name='fictif.txt', role='candidate',
                                media_type='text/plain', content=b'fictif-x' * (8 * 1024 * 1024))
            with service_lance(data) as base:
                for path in ('/healthz', '/readyz'):
                    with urlopen(base + path, timeout=2) as response:
                        self.assertEqual(200, response.status)
                        response.read()
                samples = {}
                for path in ('/healthz', '/readyz'):
                    taken = []
                    for _ in range(5):
                        started = time.perf_counter()
                        with urlopen(base + path, timeout=2) as response:
                            self.assertEqual(200, response.status)
                            response.read()
                        taken.append((time.perf_counter() - started) * 1000)
                    samples[path] = taken
            from benchmark.runtime import verify
            with closing(Store(data)) as store:
                started = time.perf_counter()
                verify(store)
                verify_ms = (time.perf_counter() - started) * 1000
            health_ms = sorted(samples['/healthz'])[2]
            ready_ms = sorted(samples['/readyz'])[2]
            detail = (f"healthz {health_ms:.2f} ms {[f'{item:.2f}' for item in samples['/healthz']]}, "
                      f"readyz {ready_ms:.2f} ms {[f'{item:.2f}' for item in samples['/readyz']]}, "
                      f"verify {verify_ms:.2f} ms")
            self.assertLess(ready_ms, max(health_ms, 1.0) * 10, detail)
            self.assertLess(ready_ms * 5, verify_ms, detail)

    def test_restauration_en_attente_laisse_readyz_indisponible(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory).resolve() / 'private'
            initialize(data)
            with service_lance(data) as base:
                marker = data / 'restore.json'
                marker.write_text(json.dumps({'state': 'RESTORED_RECONCILIATION_REQUIRED'}), encoding='utf-8')
                marker.chmod(0o600)
                with self.assertRaises(HTTPError) as unavailable:
                    urlopen(base + '/readyz', timeout=2)
                self.assertEqual(503, unavailable.exception.code)
                self.assertEqual('unavailable', json.load(unavailable.exception)['storage'])
                unavailable.exception.close()
                with urlopen(base + '/healthz', timeout=2) as response:
                    self.assertEqual(200, response.status)

    def test_schema_incompatible_laisse_readyz_indisponible(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory).resolve() / 'private'
            initialize(data)
            with service_lance(data) as base:
                with closing(sqlite3.connect(data / 'metadata.sqlite3')) as connection:
                    connection.execute('CREATE TABLE sonde_bruit (marqueur INTEGER)')
                    connection.commit()
                with self.assertRaises(HTTPError) as unavailable:
                    urlopen(base + '/readyz', timeout=2)
                self.assertEqual(503, unavailable.exception.code)
                self.assertEqual('unknown', json.load(unavailable.exception)['storage'])
                unavailable.exception.close()
                with urlopen(base + '/healthz', timeout=2) as response:
                    self.assertEqual(200, response.status)
                with closing(sqlite3.connect(data / 'metadata.sqlite3')) as connection:
                    connection.execute('DROP TABLE sonde_bruit')
                    connection.commit()

    def test_piece_alteree_laisse_la_sonde_prete(self):
        """Une pièce altérée après le démarrage ne retire pas le sens « prêt » : la sonde ne re-hache pas"""
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory).resolve() / 'private'
            initialize(data)
            with closing(Store(data)) as store:
                store.save_dossier('d', 1, PAYLOAD)
                meta = store.put_piece('d', 1, 'piece', name='fictif.txt', role='candidate',
                                       media_type='text/plain', content=b'fictif')
            with service_lance(data) as base:
                with (data / meta['relative_path']).open('r+b') as stream:
                    stream.write(b'y')
                with urlopen(base + '/readyz', timeout=2) as response:
                    self.assertEqual(200, response.status)
                    self.assertEqual('ok', json.load(response)['storage'])


def _established(port, timeout, deadline):
    """Connexion TCP ouverte avant la mesure, avec reprise du seul refus hors périmètre

    La file d'acceptation du serveur HTTP n'est pas l'objet de cette Issue, et son débordement
    ne se signale pas de la même manière sous Linux et sous macOS. L'ouvrir avant la barrière
    sort ce bruit de la mesure : les requêtes partent ensuite réellement en même temps, et une
    indisponibilité de l'exécuteur arrive en 503, jamais en erreur de connexion
    """
    retries = 0
    while True:
        connection = HTTPConnection('127.0.0.1', port, timeout=timeout)
        try:
            connection.connect()
            return connection, retries
        except OSError:
            connection.close()
            if time.monotonic() >= deadline:
                raise
            retries += 1
            time.sleep(0.01)


def _concurrent_profile(port, path, headers, count, *, timeout=30):
    """Relevé d'un profil de charge : statuts obtenus, reprises TCP et requête la plus lente"""
    start_line = threading.Barrier(count)
    codes, retries, elapsed, guard = [], [], [], threading.Lock()
    deadline = time.monotonic() + timeout

    def once(index):
        # Ouvertures étalées : la file d'acceptation du serveur HTTP n'est pas l'objet de la mesure
        time.sleep(index * 0.005)
        connection = retried = None
        began = time.monotonic()
        try:
            connection, retried = _established(port, timeout, deadline)
            start_line.wait(timeout)
            began = time.monotonic()
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            response.read()
            code = response.status
        except (OSError, HTTPException, threading.BrokenBarrierError) as error:
            code = type(error).__name__
        finally:
            if connection is not None:
                connection.close()
        with guard:
            codes.append(code)
            retries.append(retried or 0)
            elapsed.append(time.monotonic() - began)

    threads = [threading.Thread(target=once, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout + 10)
    return Counter(codes), sum(retries), max(elapsed, default=0.0)


@contextmanager
def _loaded_stack(workers):
    """Exécuteur et serveur web réels sur un stockage d'essai, concurrence fixée par le test"""
    from tests.test_privacy import initialize as initialize_storage
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        data, public = root / 'private', root / 'public'
        public.mkdir()
        initialize_storage(data)
        with closing(Store(data)) as store:
            _, _, token = preparation.session(store, None, create=True)
        # Chemin de socket court : AF_UNIX échoue au-delà d'une centaine d'octets
        socket_root = Path(tempfile.mkdtemp())
        sock = socket_root / 'x.sock'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        repository = Path(__file__).resolve().parents[1]
        command = [sys.executable, '-B', '-m', 'benchmark.runtime']
        environment = dict(os.environ, BENCHMARK_EXECUTOR_WORKERS=str(workers))
        children = []
        try:
            children.append(subprocess.Popen(command + ['executor', '--data', str(data), '--socket', str(sock)],
                                             cwd=repository, env=environment, start_new_session=True))
            deadline = time.monotonic() + 30
            while True:
                try:
                    executor_health(sock)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.02)
            children.append(subprocess.Popen(command + ['web', '--public', str(public), '--socket', str(sock),
                                                        '--port', str(port), '--readyz-client', '127.0.0.1'],
                                             cwd=repository, env=environment, start_new_session=True))
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urlopen(f'http://127.0.0.1:{port}/readyz', timeout=2):
                        break
                except HTTPError as error:
                    error.close()
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.02)
            yield port, token
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
                        child.wait()
            shutil.rmtree(socket_root, ignore_errors=True)


class ExecutorConcurrencyTests(unittest.TestCase):
    """Charge du parcours privé : cible de 50 requêtes simultanées par profil (BX-01)"""

    TARGET = 50

    def test_parcours_prive_tient_cinquante_visiteurs_simultanes(self):
        with _loaded_stack(workers=64) as (port, token):
            session = {'Cookie': 'benchmark_session=' + token}
            activity = dict(session, Accept='application/json')
            observed = {}
            for label, path, headers in (('avec cookie', '/preparation', session),
                                         ('sans cookie', '/preparation', {}),
                                         ('activite', '/preparation/activity', activity)):
                codes, retries, slowest = _concurrent_profile(port, path, headers, self.TARGET)
                observed[label] = {'statuts': dict(codes), 'reprises_tcp': retries,
                                   'plus_lente_s': round(slowest, 3)}
            print('\nBX-01 profils :', json.dumps(observed, ensure_ascii=False))
            for label, mesure in observed.items():
                self.assertEqual({200: self.TARGET}, mesure['statuts'], label)

    def test_au_dela_de_la_capacite_l_echec_est_immediat(self):
        """Un seul fil admis : le surplus est refusé tout de suite, pas mis en attente

        Le nombre de succès est rattaché à la concurrence configurée. Sans cela le test passerait
        aussi sur l'exécuteur sériel d'avant, qui refusait le surplus par débordement de la file
        d'écoute et servait autant de requêtes
        """
        with _loaded_stack(workers=1) as (port, token):
            codes, retries, slowest = _concurrent_profile(
                port, '/preparation', {'Cookie': 'benchmark_session=' + token}, self.TARGET)
            print('\nBX-01 saturation :', json.dumps(
                {'statuts': dict(codes), 'reprises_tcp': retries, 'plus_lente_s': round(slowest, 3)},
                ensure_ascii=False))
            self.assertEqual(set(), set(codes) - {200, 503})
            self.assertGreater(codes[503], 0, codes)
            # Un `Counter` rend 0 sur une clé absente : borner par le haut seul laissait passer 50 × 503
            self.assertGreaterEqual(codes[200], 1, codes)
            self.assertLessEqual(codes[200], 2, codes)
            self.assertLess(slowest, 1)
