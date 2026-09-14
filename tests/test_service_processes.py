"""Preuve avec deux vrais processus et une socket locale, sans fournisseur."""
from contextlib import closing
from email.message import Message
from hashlib import sha256
import json
import multiprocessing
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from benchmark.service import executor_health, preparation_request
from benchmark.storage import Store, initialize
from benchmark_web.server import _source_fingerprint, serve_web
from tests.test_storage import PAYLOAD, operation


class ServiceProcessesTests(unittest.TestCase):
    def test_web_source_fingerprint_precedence_and_invalid_bucket(self):
        salt = b's' * 32
        headers = Message()
        headers['X-Real-IP'] = '2001:db8:1:2::9'
        headers['X-Forwarded-For'] = '192.0.2.1, 192.0.2.2'
        first = _source_fingerprint(headers, ('127.0.0.1', 1), salt)
        same_prefix = Message()
        same_prefix['X-Real-IP'] = '2001:db8:1:2::ffff'
        self.assertEqual(first, _source_fingerprint(same_prefix, ('127.0.0.1', 1), salt))
        other = Message()
        other['X-Real-IP'] = '2001:db8:1:3::1'
        self.assertNotEqual(first, _source_fingerprint(other, ('127.0.0.1', 1), salt))
        forwarded = Message()
        forwarded['X-Forwarded-For'] = '192.0.2.1, 192.0.2.2'
        direct = Message()
        direct['X-Real-IP'] = '192.0.2.1'
        self.assertEqual(_source_fingerprint(forwarded, ('127.0.0.1', 1), salt),
                         _source_fingerprint(direct, ('127.0.0.1', 1), salt))
        invalid = Message()
        invalid['X-Real-IP'] = 'illisible'
        another_invalid = Message()
        another_invalid['X-Real-IP'] = ''
        self.assertEqual(_source_fingerprint(invalid, ('127.0.0.1', 1), salt),
                         _source_fingerprint(another_invalid, ('127.0.0.1', 1), salt))

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
                        self.assertIn('Votre demande a bien été reçue', response.read().decode())
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
                web = subprocess.Popen(command + ['web', '--public', str(public), '--socket', str(sock), '--port', str(port)], cwd=root)
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
