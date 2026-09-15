"""Connexion OpenRouter relayée par le web, sans appel fournisseur."""
from http.client import HTTPConnection
import json
import multiprocessing
from pathlib import Path
import queue
import socket
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode

from benchmark.storage import _strict_json
from benchmark_web import views
from benchmark_web.server import _public_callback_url, serve_web


CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; "
       "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


def _port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class FakeExecutor:
    def __init__(self, path):
        self.path = path
        self.requests = queue.Queue()
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._serve)

    def __enter__(self):
        self.worker.start()
        deadline = time.monotonic() + 5
        while not self.path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('Socket fictive absente')
            time.sleep(.01)
        return self

    def __exit__(self, *args):
        self.stop.set()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as wake:
                wake.connect(str(self.path))
        except OSError:
            pass
        self.worker.join(5)

    def _serve(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.path))
            server.listen()
            server.settimeout(.1)
            while not self.stop.is_set():
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    raw = b''
                    while not raw.endswith(b'\n'):
                        part = connection.recv(4096)
                        if not part:
                            break
                        raw += part
                    if not raw:
                        continue
                    request = json.loads(raw)
                    self.requests.put(request)
                    if request['path'] == '/preparation/access' and request['method'] == 'GET':
                        result = {'status': 200, 'value': {'connected': False, 'status': 'disconnected'},
                                  'piece': False, 'cookie': 'session-token'}
                    elif request['path'] == '/preparation' and request['method'] == 'GET':
                        result = {'status': 200, 'value': {'csrf_token': 'csrf', 'availability': {}},
                                  'piece': False, 'cookie': None}
                    elif request['path'] == '/preparation/access/start':
                        result = {'status': 200, 'value': {'authorize_url': 'https://openrouter.ai/auth?fixture=1'},
                                  'piece': False, 'cookie': None}
                    elif request['path'] == '/preparation/access/callback':
                        result = {'status': 200, 'value': {'connected': True, 'status': 'connected'},
                                  'piece': False, 'cookie': None}
                    else:
                        result = {'status': 404, 'value': {'error': 'NOT_FOUND'},
                                  'piece': False, 'cookie': None}
                    connection.sendall((_strict_json(result) + '\n').encode())


class AccessViewTests(unittest.TestCase):
    def test_trois_etats(self):
        disconnected = views.render(
            {'kind': 'access', 'connected': False, 'status': 'disconnected'}, 'csrf').decode()
        self.assertIn('Compte non connecté', disconnected)
        self.assertIn('Connecter mon compte OpenRouter', disconnected)

        connected = views.render({'kind': 'access', 'connected': True, 'status': 'connected',
                                  'limit_usd': '25', 'limit_remaining_usd': '12.50'}, 'csrf').decode()
        self.assertIn('Compte connecté', connected)
        self.assertIn('Crédit restant : 12.50 USD', connected)
        self.assertIn('Limite du compte : 25 USD', connected)
        self.assertIn('Déconnecter', connected)

        invalid = views.render({'kind': 'access', 'connected': False, 'status': 'invalid',
                                'reason': 'KEY_REJECTED'}, 'csrf').decode()
        self.assertIn('Accès invalide', invalid)
        self.assertIn('Motif : KEY_REJECTED', invalid)


class AccessServerTests(unittest.TestCase):
    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        result = response.status, response.headers, raw
        connection.close()
        return result

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='web-access-')
        root = Path(self.temporary.name)
        public = root / 'public'
        public.mkdir()
        self.executor = FakeExecutor(root / 'executor.sock')
        self.executor.__enter__()
        self.port = _port()
        self.web = multiprocessing.get_context('spawn').Process(
            target=serve_web,
            args=('127.0.0.1', self.port, public, self.executor.path, 'a' * 40,
                  'https://benchmark.example'))
        self.web.start()
        deadline = time.monotonic() + 5
        while True:
            try:
                status, _, _ = self.request('GET', '/healthz')
                if status == 200:
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.02)

    def tearDown(self):
        self.web.terminate()
        self.web.join(5)
        self.executor.__exit__(None, None, None)
        self.temporary.cleanup()

    def test_depart_callback_et_csp(self):
        status, headers, _ = self.request('GET', '/preparation/access')
        self.assertEqual(200, status)
        self.assertEqual(CSP, headers['Content-Security-Policy'])
        session_cookie = headers['Set-Cookie'].split(';', 1)[0]

        body = urlencode({'csrf_token': 'csrf', 'return': '/preparation/dossiers/d1'}).encode()
        status, headers, raw = self.request('POST', '/preparation/access/start', body, {
            'Content-Type': 'application/x-www-form-urlencoded', 'Cookie': session_cookie})
        self.assertEqual(303, status)
        self.assertEqual('https://openrouter.ai/auth?fixture=1', headers['Location'])
        callback_cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.assertNotIn(b'code=', raw)
        start = self.executor.requests.get_nowait()
        home = self.executor.requests.get_nowait()
        request = self.executor.requests.get_nowait()
        self.assertEqual(('/preparation/access', '/preparation', '/preparation/access/start'),
                         (start['path'], home['path'], request['path']))
        self.assertEqual({'csrf_token': 'csrf',
                          'callback_url': 'https://benchmark.example/preparation/access/callback'},
                         request['body'])

        status, headers, raw = self.request(
            'GET', '/preparation/access/callback?code=secret-authorization-code',
            headers={'Cookie': callback_cookie})
        self.assertEqual(303, status)
        self.assertEqual('/preparation/dossiers/d1', headers['Location'])
        rendered_headers = str(headers)
        self.assertNotIn('secret-authorization-code', rendered_headers)
        self.assertNotIn(b'secret-authorization-code', raw)
        callback = self.executor.requests.get_nowait()
        self.assertEqual({'code': 'secret-authorization-code'}, callback['body'])
        self.assertEqual('/preparation/access/callback', callback['path'])

    def test_refuse_un_retour_exterieur(self):
        body = urlencode({'csrf_token': 'csrf', 'return': 'https://evil.example/preparation'}).encode()
        status, _, _ = self.request('POST', '/preparation/access/start', body, {
            'Content-Type': 'application/x-www-form-urlencoded', 'Cookie': 'benchmark_session=session-token'})
        self.assertEqual(400, status)
        self.assertTrue(self.executor.requests.empty())

    def test_callback_navigateur_post_ne_contourne_pas_csrf(self):
        body = urlencode({'code': 'code-direct'}).encode()
        status, _, raw = self.request('POST', '/preparation/access/callback', body, {
            'Content-Type': 'application/x-www-form-urlencoded', 'Cookie': 'benchmark_session=session-token'})
        self.assertEqual(400, status)
        self.assertNotIn(b'code-direct', raw)
        self.assertTrue(self.executor.requests.empty())

    def test_url_publique_requise_pour_activer(self):
        self.assertEqual('https://benchmark.example/preparation/access/callback',
                         _public_callback_url('https://benchmark.example/'))
        with self.assertRaises(ValueError):
            _public_callback_url('http://benchmark.example')


if __name__ == '__main__':
    unittest.main()
