"""HTTP S7 réel : exécuteur + web séparés, fixtures synthétiques, réseau local seul.

Exécution : uv run --with-requirements benchmark/requirements.txt --with requests
--with mpmath==1.3.0 python -m unittest -v tests.test_privacy_http
Les cookies Secure sont transportés explicitement sur le HTTP loopback du test ;
ceci ne prétend pas vérifier leur politique dans un navigateur HTTPS.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import json
import multiprocessing
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from benchmark import preparation, privacy, provider_access, service, storage
from benchmark_web.server import serve_web
from tests.test_privacy import initialize, NOW, SECRET
from tests.test_s2_review_regressions import response_for

SOURCE = 'a' * 40
PUBLIC_ORIGIN = 'https://bench-x.example'


def _now(clock):
    return datetime.fromtimestamp(clock.value, timezone.utc)


@contextmanager
def _local_network_only(blocked):
    connect, connect_ex, getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def check(host):
        if host not in ('127.0.0.1', '::1', 'localhost'):
            with blocked.get_lock():
                blocked.value += 1
            raise AssertionError('Réseau non local interdit dans ce test')

    def local_connect(sock, address):
        if sock.family != socket.AF_UNIX:
            check(address[0])
        return connect(sock, address)

    def local_connect_ex(sock, address):
        if sock.family != socket.AF_UNIX:
            check(address[0])
        return connect_ex(sock, address)

    def local_lookup(host, *args, **kwargs):
        check(host)
        return getaddrinfo(host, *args, **kwargs)

    with patch.object(socket.socket, 'connect', local_connect), \
            patch.object(socket.socket, 'connect_ex', local_connect_ex), \
            patch.object(socket, 'getaddrinfo', local_lookup):
        yield


def _executor(data, sock, clock, provider_calls, blocked, journal):
    def forbidden_provider(*args, **kwargs):
        with provider_calls.get_lock():
            provider_calls.value += 1
        raise AssertionError('Aucun appel fournisseur autorisé par ce test HTTP')

    with _local_network_only(blocked), patch.object(privacy, 'now', lambda: _now(clock)), \
            patch.object(provider_access, '_now', lambda: _now(clock)), \
            patch.dict(os.environ, {'BENCHMARK_PRIVACY_JOURNAL': journal}):
        service.serve_executor(data, sock, SOURCE, access_secret=SECRET,
            personal_preparation=True, transport=forbidden_provider,
            qualification_transport=forbidden_provider, candidate_transport=forbidden_provider,
            judgment_transport=forbidden_provider, access_transport=forbidden_provider,
            model_probe_transport=forbidden_provider)


def _web(port, public, sock, blocked):
    with _local_network_only(blocked):
        serve_web('127.0.0.1', port, public, sock, SOURCE, public_url=PUBLIC_ORIGIN)


class PrivacyHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bx-http-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.data = self.root / 'private'
        self.sock = self.root / 'executor.sock'
        public = self.root / 'public'
        public.mkdir()
        context = multiprocessing.get_context('spawn')
        self.clock = context.Value('d', NOW.timestamp())
        self.provider_calls = context.Value('i', 0)
        self.blocked = context.Value('i', 0)
        self.addCleanup(lambda: self.assertEqual(0, self.provider_calls.value, 'Appel fournisseur inattendu'))
        self.addCleanup(lambda: self.assertEqual(0, self.blocked.value, 'Tentative de réseau externe'))
        self.enterContext(_local_network_only(self.blocked))
        self.enterContext(patch.object(privacy, 'now', lambda: _now(self.clock)))
        self.enterContext(patch.object(provider_access, '_now', lambda: _now(self.clock)))
        journal = str(self.root / 'revocations' / 'revocations.jsonl')
        self.enterContext(patch.dict(os.environ, {'BENCHMARK_PRIVACY_JOURNAL': journal}))
        self.assertEqual('s7', initialize(self.data)['layout'])
        self.store = storage.Store(self.data)
        self.addCleanup(self.store.close)
        self.children = []
        self.addCleanup(self.stop_children)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            self.port = listener.getsockname()[1]
        for target, args in (
                (_executor, (self.data, self.sock, self.clock, self.provider_calls, self.blocked, journal)),
                (_web, (self.port, public, self.sock, self.blocked))):
            process = context.Process(target=target, args=args)
            process.start()
            self.children.append(process)
        deadline = time.monotonic() + 5
        while True:
            self.assertTrue(all(process.is_alive() for process in self.children), 'Un service s’est arrêté au démarrage')
            try:
                code, _, _ = self.request('GET', '/readyz')
                if code == 200:
                    break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                self.fail('Les deux services ne sont pas prêts')
            time.sleep(.02)
        self.assertEqual(2, len({process.pid for process in self.children}))

    def stop_children(self):
        for process in reversed(self.children):
            if process.is_alive():
                process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
                self.fail('Service non arrêté après SIGTERM')

    def advance(self, days):
        self.clock.value = (NOW + timedelta(days=days)).timestamp()

    def request(self, method, path, *, cookies=None, body=None, native=False,
                origin=PUBLIC_ORIGIN, fetch_site=None):
        headers = {'Accept': 'text/html' if native else 'application/json'}
        if cookies:
            headers['Cookie'] = '; '.join(name + '=' + value for name, value in cookies.items())
        raw = None
        if method == 'POST':
            raw = urlencode(body).encode() if native else json.dumps(body).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if native else 'application/json'
            if origin is not None:
                headers['Origin'] = origin
            if fetch_site is not None:
                headers['Sec-Fetch-Site'] = fetch_site
        connection = HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            connection.request(method, path, raw, headers)
            response = connection.getresponse()
            content = response.read()
            value = (json.loads(content) if content and response.getheader('Content-Type', '').startswith('application/json')
                     else content.decode())
            return response.status, response.headers, value
        finally:
            connection.close()

    @staticmethod
    def cookie_values(headers):
        values = {}
        for header in headers.get_all('Set-Cookie', []):
            values.update({key: morsel.value for key, morsel in SimpleCookie(header).items()})
        return values

    def new_session(self):
        code, headers, value = self.request('POST', '/preparation/session/open', body={})
        self.assertEqual(200, code, value)
        cookies = self.cookie_values(headers)
        self.assertEqual({'benchmark_session'}, set(cookies))
        session, csrf, _ = preparation.session(self.store, cookies['benchmark_session'])
        self.assertEqual(csrf, value['csrf_token'])
        return {'id': session, 'csrf': csrf, 'cookies': cookies}

    def seed_case(self, owner, dossier='case-a'):
        budget = 'personal-preparation-' + owner['id']
        if not self.store._connection.execute('SELECT 1 FROM budgets WHERE budget_id=?', (budget,)).fetchone():
            self.store.create_budget(budget, '100', 'TEST')
        preparation.admit(self.store, dict(authority_id='TEST_ONLY', budget_id=budget,
            reserve_amount='7', requested_configuration={'model': 'fictional'}))
        operation, _ = preparation.submit(self.store, owner['id'], dossier,
            {'action_id': 'create', 'request': 'Organiser des notes entièrement fictives'}, SOURCE, True)
        preparation.execute(self.data, operation, lambda op, request: response_for(op))
        view = preparation.view(self.store, owner['id'], dossier)
        self.assertIsNotNone(view['package'])
        return view

    def consent(self, owner, dossier='case-a'):
        view = preparation.view(self.store, owner['id'], dossier)
        code, headers, value = self.request('POST', '/preparation/dossiers/' + dossier + '/contribution',
            cookies=owner['cookies'], native=True, body={'csrf_token': owner['csrf'], 'enabled': 'true',
                'revision': '0', 'example_revision': str(view['revision'])})
        self.assertEqual(303, code, value)
        cookies = {**owner['cookies'], **self.cookie_values(headers)}
        code, _, manager = self.request('GET', '/preparation/contributions', cookies=cookies)
        self.assertEqual(200, code, manager)
        self.assertEqual(1, len(manager['contributions']))
        return headers, cookies, manager

    def dates(self):
        return {table: self.store._connection.execute(query).fetchall() for table, query in (
            ('sessions', 'SELECT session_id,last_activity_at,expires_at FROM s7_sessions ORDER BY session_id'),
            ('dossiers', 'SELECT dossier_id,last_activity_at,expires_at FROM s7_dossiers ORDER BY dossier_id'),
            ('managers', 'SELECT manager_id,created_at,expires_at FROM s7_contribution_managers ORDER BY manager_id'),
            ('contributions', 'SELECT contribution_id,created_at,expires_at FROM s7_contributions ORDER BY contribution_id'))}

    def test_bootstrap_origin_new_reused_session_and_public_shells(self):
        for path, kind in (('/preparation', 'session_bootstrap'), ('/preparation/data', 'privacy_data'),
                           ('/preparation/privacy', 'privacy_notice')):
            code, headers, value = self.request('GET', path)
            self.assertEqual((200, kind), (code, value['kind']))
            self.assertFalse(headers.get_all('Set-Cookie'))
            code, headers, page = self.request('GET', path, native=True)
            self.assertEqual(200, code)
            self.assertIn('<main', page)
            self.assertFalse(headers.get_all('Set-Cookie'))
        for origin, fetch_site in ((None, None), ('https://foreign.example', None),
                                   (PUBLIC_ORIGIN + '/', None), (PUBLIC_ORIGIN, 'cross-site')):
            with self.subTest(origin=origin, fetch_site=fetch_site):
                code, headers, _ = self.request('POST', '/preparation/session/open', body={},
                                                origin=origin, fetch_site=fetch_site)
                self.assertEqual(400, code)
                self.assertFalse(headers.get_all('Set-Cookie'))
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s2_sessions').fetchone()[0])
        owner = self.new_session()
        before = self.dates()
        self.advance(1)
        for fetch_site in (None, 'same-origin', 'none'):
            code, headers, value = self.request('POST', '/preparation/session/open',
                cookies=owner['cookies'], body={}, fetch_site=fetch_site)
            self.assertEqual((200, owner['csrf']), (code, value['csrf_token']))
            self.assertEqual({}, self.cookie_values(headers))
            self.assertEqual(before, self.dates())
        self.assertEqual(1, self.store._connection.execute('SELECT count(*) FROM s2_sessions').fetchone()[0])
        code, headers, _ = self.request('POST', '/preparation/session/open', cookies=owner['cookies'],
            native=True, body={'return_path': '/preparation/data'})
        self.assertEqual((303, '/preparation/data'), (code, headers['Location']))
        code, _, _ = self.request('POST', '/preparation/session/open', native=True,
                                  body={'return_path': '/preparation?unsafe=1'})
        self.assertEqual(400, code)

    def test_expired_archives_distinguish_owner_410_from_foreign_or_expired_session_404(self):
        owner = self.new_session()
        self.seed_case(owner)
        stranger = self.new_session()
        path = '/preparation/dossiers/case-a/archive'
        code, _, manifest = self.request('GET', path, cookies=owner['cookies'])
        self.assertEqual(200, code, manifest)
        item = path + '/items/record?' + urlencode({'snapshot': manifest['snapshot_id'], 'part': 0})
        self.assertEqual(200, self.request('GET', item, cookies=owner['cookies'])[0])
        for candidate in (None, stranger['cookies']):
            for route in (path, item):
                code, headers, value = self.request('GET', route, cookies=candidate)
                self.assertEqual((404, 'NOT_FOUND'), (code, value['error_code']))
                self.assertFalse(headers.get_all('Set-Cookie'))
        self.advance(7)
        for route in (path, item):
            code, headers, value = self.request('GET', route, cookies=owner['cookies'])
            self.assertEqual((410, 'DOSSIER_EXPIRED'), (code, value['error_code']))
            self.assertFalse(headers.get_all('Set-Cookie'))
            self.assertEqual(404, self.request('GET', route, cookies=stranger['cookies'])[0])
        self.advance(30)
        for route in (path, item):
            code, headers, value = self.request('GET', route, cookies=owner['cookies'])
            self.assertEqual((404, 'NOT_FOUND'), (code, value['error_code']))
            self.assertFalse(headers.get_all('Set-Cookie'))
        code, headers, value = self.request('GET', '/preparation', cookies=owner['cookies'])
        self.assertEqual((200, 'session_bootstrap'), (code, value['kind']))
        self.assertFalse(headers.get_all('Set-Cookie'))

    def test_native_consent_revision_zero_emits_two_distinct_cookies_and_unchecked_means_false(self):
        owner = self.new_session()
        view = self.seed_case(owner)
        headers, cookies, manager = self.consent(owner)
        cookie_headers = headers.get_all('Set-Cookie')
        self.assertEqual(2, len(cookie_headers))
        names = []
        for header in cookie_headers:
            parsed = SimpleCookie(header)
            self.assertEqual(1, len(parsed))
            name, value = next(iter(parsed.items()))
            names.append(name)
            self.assertTrue(value['secure'])
            self.assertTrue(value['httponly'])
            self.assertEqual('Strict', value['samesite'])
            self.assertEqual('/preparation', value['path'])
            if name == 'benchmark_session':
                self.assertEqual('2592000', value['max-age'])
            else:
                self.assertEqual(datetime(2027, 3, 18, 12, tzinfo=timezone.utc), parsedate_to_datetime(value['expires']))
        self.assertEqual({'benchmark_session', 'benchmark_contributions'}, set(names))
        code, _, value = self.request('GET', '/preparation/dossiers/case-a', cookies=cookies)
        self.assertEqual(200, code, value)
        consent = value['privacy']['contribution']
        self.assertIs(consent['enabled'], True)
        self.assertEqual(1, consent['revision'])
        code, headers, value = self.request('POST', '/preparation/dossiers/case-a/contribution',
            cookies=cookies, native=True, body={'csrf_token': owner['csrf'],
                'revision': str(consent['revision']), 'example_revision': str(view['revision'])})
        self.assertEqual(303, code, value)
        self.assertNotIn('benchmark_contributions', self.cookie_values(headers))
        code, _, value = self.request('GET', '/preparation/contributions', cookies=cookies)
        self.assertEqual((200, 'withdrawn'), (code, value['contributions'][0]['status']))

    def test_management_csrf_stays_scoped_and_works_after_main_session_expiry(self):
        owner = self.new_session()
        self.seed_case(owner)
        _, cookies, manager = self.consent(owner)
        other = self.new_session()
        self.seed_case(other, 'case-b')
        _, other_cookies, other_manager = self.consent(other, 'case-b')
        self.assertNotEqual(owner['csrf'], manager['csrf_token'])
        code, _, _ = self.request('POST', '/preparation/activity', cookies=cookies,
                                  body={'csrf_token': manager['csrf_token']})
        self.assertEqual(403, code)
        self.advance(31)
        management_only = {'benchmark_contributions': cookies['benchmark_contributions']}
        before = self.dates()
        code, headers, value = self.request('GET', '/preparation/contributions', cookies=management_only)
        self.assertEqual((200, manager['csrf_token']), (code, value['csrf_token']))
        self.assertFalse(headers.get_all('Set-Cookie'))
        code, headers, page = self.request('GET', '/preparation/contributions', cookies=cookies, native=True)
        self.assertEqual(200, code)
        self.assertIn('value="' + manager['csrf_token'] + '"', page)
        self.assertFalse(headers.get_all('Set-Cookie'))
        self.assertEqual(before, self.dates())
        own_path = '/preparation/contributions/' + manager['contributions'][0]['id'] + '/withdraw'
        other_path = '/preparation/contributions/' + other_manager['contributions'][0]['id'] + '/withdraw'
        for route, sent_cookies, csrf in ((own_path, management_only, owner['csrf']),
                                         (own_path, other_cookies, manager['csrf_token']),
                                         (other_path, management_only, manager['csrf_token'])):
            code, headers, _ = self.request('POST', route, cookies=sent_cookies, body={'csrf_token': csrf})
            self.assertEqual(403, code)
            self.assertFalse(headers.get_all('Set-Cookie'))
        code, headers, value = self.request('POST', own_path, cookies=management_only,
            body={'csrf_token': manager['csrf_token']}, native=True)
        self.assertEqual((303, '/preparation/contributions'), (code, headers['Location']))
        self.assertFalse(headers.get_all('Set-Cookie'))
        self.assertEqual('withdrawn', self.request('GET', '/preparation/contributions', cookies=management_only)[2]['contributions'][0]['status'])
        self.assertEqual('active', self.request('GET', '/preparation/contributions', cookies=other_cookies)[2]['contributions'][0]['status'])

    def test_concurrent_first_consents_and_stale_management_cookie_keep_one_access(self):
        owner = self.new_session()
        views = {name: self.seed_case(owner, name) for name in ('case-a', 'case-b')}
        issued = []
        for name, view in views.items():
            code, headers, value = self.request('POST', '/preparation/dossiers/' + name + '/contribution',
                cookies={**owner['cookies'], 'benchmark_contributions': '1' * 64},
                body={'csrf_token': owner['csrf'], 'enabled': True, 'revision': 0,
                      'example_revision': view['revision']})
            self.assertEqual(200, code, value)
            issued.append(self.cookie_values(headers)['benchmark_contributions'])
        self.assertEqual(issued[0], issued[1])
        cookies = {'benchmark_contributions': issued[-1]}
        self.advance(31)
        code, _, value = self.request('GET', '/preparation/contributions', cookies=cookies)
        self.assertEqual(200, code)
        self.assertEqual(2, len(value['contributions']))
        for contribution in value['contributions']:
            code, _, _ = self.request('POST', '/preparation/contributions/' + contribution['id'] + '/withdraw',
                cookies=cookies, body={'csrf_token': value['csrf_token']})
            self.assertEqual(200, code)

    def test_reads_do_not_extend_dates_or_cookies_but_activity_post_does(self):
        owner = self.new_session()
        self.seed_case(owner)
        _, cookies, _ = self.consent(owner)
        path = '/preparation/dossiers/case-a'
        code, _, manifest = self.request('GET', path + '/archive', cookies=cookies)
        self.assertEqual(200, code, manifest)
        item = path + '/archive/items/record?' + urlencode({'snapshot': manifest['snapshot_id'], 'part': 0})
        before = self.dates()
        self.advance(2)
        for route, native in (('/preparation', False), ('/preparation', True), (path, False), (path, True),
                              (path + '/archive', False), (item, False), ('/preparation/contributions', False),
                              ('/preparation/contributions', True), ('/preparation/data', True), ('/preparation/privacy', True)):
            with self.subTest(route=route, html=native):
                code, headers, value = self.request('GET', route, cookies=cookies, native=native)
                self.assertEqual(200, code, value)
                self.assertFalse(headers.get_all('Set-Cookie'))
                self.assertEqual(before, self.dates())
        code, headers, value = self.request('POST', '/preparation/activity', cookies=cookies,
            body={'csrf_token': owner['csrf'], 'dossier_id': 'case-a'})
        self.assertEqual(200, code, value)
        self.assertEqual(owner['cookies'], self.cookie_values(headers))
        after = self.dates()
        self.assertEqual((NOW + timedelta(days=32)).isoformat(), after['sessions'][0][2])
        self.assertEqual((NOW + timedelta(days=9)).isoformat(), after['dossiers'][0][2])
        self.assertEqual(before['managers'], after['managers'])
        self.assertEqual(before['contributions'], after['contributions'])

    def test_delete_then_purge_is_idempotent_and_preserves_spent_personal_budget(self):
        owner = self.new_session()
        self.seed_case(owner)
        _, cookies, _ = self.consent(owner)
        before = self.store.inspect_budget('personal-preparation-' + owner['id'])
        path = '/preparation/dossiers/case-a'
        code, headers, value = self.request('POST', path + '/delete', cookies=cookies, body={'csrf_token': owner['csrf']})
        self.assertEqual((202, 'delete_requested'), (code, value['status']))
        self.assertFalse(headers.get_all('Set-Cookie'))
        self.assertEqual([], self.request('GET', '/preparation/contributions', cookies=cookies)[2]['contributions'])
        self.assertEqual(410, self.request('GET', path + '/archive', cookies=cookies)[0])
        self.assertEqual(['case-a'], privacy.purge(self.data)['purged'])
        code, headers, value = self.request('POST', path + '/delete', cookies=cookies, body={'csrf_token': owner['csrf']})
        self.assertEqual((202, 'purged'), (code, value['status']))
        self.assertFalse(headers.get_all('Set-Cookie'))
        self.assertEqual(before, self.store.inspect_budget('personal-preparation-' + owner['id']))
        self.assertEqual([], list((self.data / 'pieces').iterdir()))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_delete_pending_receipt_does_not_start_another_provider_call(self):
        owner = self.new_session()
        view = self.seed_case(owner)
        _, cookies, _ = self.consent(owner)
        operation, _ = preparation.submit(self.store, owner['id'], 'case-a', {
            'action_id': 'correction', 'revision': view['revision'], 'kind': 'correct',
            'message': 'Ajouter une action entièrement fictive'}, SOURCE, True)
        entered, released = threading.Event(), threading.Event()
        calls = []

        def delayed_response(op, request):
            calls.append(op['operation_id'])
            entered.set()
            if not released.wait(5):
                raise TimeoutError('Reçu synthétique non libéré')
            return response_for(op)

        worker = threading.Thread(target=preparation.execute, args=(self.data, operation, delayed_response))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            path = '/preparation/dossiers/case-a'
            code, headers, value = self.request('POST', path + '/delete', cookies=cookies, body={'csrf_token': owner['csrf']})
            self.assertEqual((202, 'delete_requested'), (code, value['status']))
            self.assertFalse(headers.get_all('Set-Cookie'))
            self.assertEqual({'purged': [], 'pending': True}, privacy.purge(self.data))
            self.assertEqual(410, self.request('GET', path + '/archive', cookies=cookies)[0])
            self.assertEqual([], self.request('GET', '/preparation/contributions', cookies=cookies)[2]['contributions'])
            self.assertEqual([operation], calls)
        finally:
            released.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual([operation], calls)
        self.assertEqual(['case-a'], privacy.purge(self.data)['purged'])
        code, _, value = self.request('POST', path + '/delete', cookies=cookies, body={'csrf_token': owner['csrf']})
        self.assertEqual((202, 'purged'), (code, value['status']))
        self.assertEqual(0, self.provider_calls.value)


if __name__ == '__main__':
    unittest.main()
