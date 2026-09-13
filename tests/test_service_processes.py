"""Preuve avec deux vrais processus et une socket locale, sans fournisseur."""
from contextlib import closing
from hashlib import sha256
import json
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
from urllib.request import urlopen

from benchmark_lab_x.service import executor_health, preparation_request
from benchmark_lab_x.storage import Store, initialize
from tests.test_storage import PAYLOAD, operation


class ServiceProcessesTests(unittest.TestCase):
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
            shutil.copytree(Path(__file__).resolve().parents[1] / 'benchmark_lab_x', root / 'benchmark_lab_x')
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
            command = [sys.executable, '-B', '-m', 'benchmark_lab_x.runtime']
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
