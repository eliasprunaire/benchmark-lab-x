"""Recette HTTP locale sur données contrôlées, sans participant ni appel fournisseur"""
from contextlib import closing
from datetime import timedelta
from html.parser import HTMLParser
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import json
from pathlib import Path
import queue
import re
import socket
import socketserver
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit

from benchmark import campaigns, evaluation, model_catalogue, preparation as prep
from benchmark import provider_access, qualification, service, storage
from benchmark_web import server, views
from tests.test_configurations import NOW, model
from tests.test_openrouter_qualification import QualificationTransport
from tests.test_provider_access import AccessTransport, KEY, SECRET
from tests.test_s2_review_regressions import response_for


class Page(HTMLParser):
    """Lire les formulaires, le texte hors dépliants et l'ordre natif de tabulation"""
    def __init__(self, raw):
        super().__init__()
        self.stack, self.nodes, self.forms = [], [], []
        self.feed(raw.decode())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        node = {'tag': tag, 'attrs': attrs, 'text': '',
                'details': any(n['tag'] == 'details' for n in self.stack),
                'hidden': any(n['tag'] in ('head', 'script', 'svg') or 'hidden' in n['attrs']
                              for n in self.stack),
                'main': any(n['tag'] == 'main' for n in self.stack)}
        self.nodes.append(node)
        if tag == 'form':
            self.forms.append({'action': attrs['action'], 'fields': {}, 'nodes': []})
        if self.forms and any(n['tag'] == 'form' for n in self.stack):
            self.forms[-1]['nodes'].append(node)
            if tag == 'input' and attrs.get('type') == 'hidden':
                self.forms[-1]['fields'][attrs['name']] = attrs.get('value', '')
        if tag not in ('input', 'meta', 'link', 'br', 'hr', 'img', 'use', 'path', 'rect'):
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node['text'] += data
        if self.stack and not any(n['tag'] in ('details', 'head', 'script', 'svg')
                                  or 'hidden' in n['attrs'] for n in self.stack):
            self.nodes.append({'tag': '#text', 'attrs': {}, 'text': data,
                               'details': False, 'hidden': False, 'main': False})

    @property
    def visible(self):
        return ' '.join(n['text'] for n in self.nodes if n['tag'] == '#text')

    def link(self, label):
        return next(n['attrs']['href'] for n in self.nodes
                    if n['tag'] == 'a' and label in n['text'])

    def form(self, suffix):
        return next(f for f in self.forms if f['action'].endswith(suffix))


class ParcoursComplet(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s12-', dir=Path(__file__).resolve().parents[2])
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.data, self.sock = root / 'private', root / 'executor.sock'
        storage.initialize(self.data)
        storage.initialize_preparation(self.data)
        qualification.initialize(self.data)
        campaigns.initialize(self.data)
        evaluation.initialize(self.data)
        provider_access.initialize(self.data)
        self.starts = queue.Queue()
        self.calls = []
        self.clock = NOW
        self.enterContext(patch.object(prep, '_now', side_effect=lambda: self.clock))
        self.enterContext(patch.object(model_catalogue, '_now', return_value=NOW))
        self.enterContext(patch.dict(prep._SOURCE_ACCEPTED, {}, clear=True))
        self.access = AccessTransport()
        self.qualifier = QualificationTransport({
            'qualified': True, 'findings': [], 'summary': 'Les actions et leur format sont vérifiables'})
        self.identity = {'package': 'pi', 'version': '0.85.1', 'sha256': '1' * 64,
                         'bridge_sha256': '2' * 64, 'node_sha256': '3' * 64,
                         'node_version': 'v24.0.0', 'scope': 'Identité factice de recette'}
        with closing(storage.Store(self.data)) as store:
            store.create_budget('recette', '100', 'USD')
            prep.admit(store, {'authority_id': 'TEST_ONLY_S12', 'budget_id': 'recette',
                              'reserve_amount': '1', 'requested_configuration': {'model': 'factice'}})
            rows = [model('openai/gpt-5.6-sol', 'openai', ['high']),
                    model('deepseek/deepseek-v4.1-flash', 'deepseek', [])]
            rows[0][0]['name'], rows[1][0]['name'] = 'Modèle A', 'Modèle B'
            document = {'models': [row[0] for row in rows],
                        'endpoints': {row[0]['id']: row[1] for row in rows}}
            store._connection.execute(model_catalogue.TABLE_SQL)
            store._connection.execute('INSERT INTO s2_model_catalogue VALUES (?,?)',
                                      (NOW.isoformat(), storage._strict_json(document)))
        test = self

        class Executor(socketserver.StreamRequestHandler):
            def handle(self):
                message = json.loads(self.rfile.readline(), object_pairs_hook=storage._unique_object)
                with closing(storage.Store(test.data)) as store:
                    try:
                        code, value, cookie, start = prep.dispatch(
                            store, message['method'], message['path'], message['token'], message['body'],
                            'a' * 40, test.prepare, qualification_transport=test.qualifier,
                            candidate_identity=test.identity, candidate_transport=test.candidate,
                            access_secret=SECRET, access_transport=test.access)
                        if start:
                            test.starts.put(start)
                        result = {'status': code, 'value': value.hex() if isinstance(value, bytes) else value,
                                  'piece': isinstance(value, bytes), 'cookie': cookie}
                    except prep.Denied as error:
                        result = service.denied_response(error)
                    except (storage.ConflictError, storage.BudgetError):
                        result = {'status': 409, 'value': {'error': 'Action refusée : consultez le dossier courant.'}}
                    except (ValueError, KeyError, TypeError):
                        result = {'status': 400, 'value': {'error': 'Action non vérifiée. Vérifiez les champs ou consultez le dossier courant.'}}
                self.wfile.write((storage._strict_json(result) + '\n').encode())

        executor = socketserver.UnixStreamServer(str(self.sock), Executor)
        self.addCleanup(executor.server_close)
        thread = threading.Thread(target=executor.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(executor.shutdown)
        ready = threading.Event()
        def run(web):
            self.web = web
            ready.set()
            web.serve_forever(poll_interval=.01)
        self.enterContext(patch.object(server, 'run', run))
        self.enterContext(patch.object(views, 'SOURCE_SHA', views.SOURCE_SHA))
        web_thread = threading.Thread(target=server.serve_web,
            args=('127.0.0.1', 0, root, self.sock, 'a' * 40, 'https://recette.example'))
        web_thread.start()
        self.assertTrue(ready.wait(3), 'Serveur local absent')
        self.addCleanup(web_thread.join, 3)
        self.addCleanup(self.web.shutdown)
        self.address = self.web.server_address
        original_connect = socket.socket.connect
        def connect(connection, address):
            if address not in (self.address, str(self.sock)):
                raise AssertionError('No network')
            return original_connect(connection, address)
        self.enterContext(patch('socket.socket.connect', new=connect))
        self.enterContext(patch('socket.socket.connect_ex', side_effect=AssertionError('No network')))
        self.cookies = SimpleCookie()
        self.preparation_stage = 'clarification'

    def prepare(self, operation, request):
        self.calls.append(('préparation', operation['operation_id']))
        value = response_for(operation)
        value['cost'].update(amount='0.10', currency='USD', source='Reçu simulé S12')
        result = value['receipt']['result']
        if self.preparation_stage == 'clarification':
            result.update(stage='clarification', explanation='Quel format doit prendre la liste des actions ?', package=None)
        else:
            result['package']['candidate']['instruction'] = 'Relever toutes les actions dans les notes'
            if self.preparation_stage == 'correction':
                result['package']['candidate']['deliverables'] = ['Tableau des actions avec responsable']
        return value

    def candidate(self, operation, request):
        raise AssertionError('Aucun candidat attendu avant le contrat approuvé')

    def request(self, path, fields=None, *, cookies=None, status=200):
        self.clock += timedelta(minutes=1)
        headers = {'Cookie': (self.cookies if cookies is None else cookies).output(header='', sep=';').strip()}
        body = None if fields is None else urlencode(fields, doseq=True).encode()
        if body is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        with closing(HTTPConnection(*self.address, timeout=3)) as connection:
            connection.request('GET' if fields is None else 'POST', urlsplit(path).path +
                               ('?' + urlsplit(path).query if urlsplit(path).query else ''), body, headers)
            response = connection.getresponse()
            raw = response.read()
            self.assertEqual(status, response.status, (path, raw.decode()[:200]))
            for cookie in response.headers.get_all('Set-Cookie', []):
                if cookies is None:
                    self.cookies.load(cookie)
            return Page(raw), response.headers, raw

    def submit(self, page, suffix, values, status=202):
        form = page.form(suffix)
        return self.request(form['action'], form['fields'] | values, status=status)[0]

    def examine(self, page, route, state, primary):
        with self.subTest(route=route, état=state):
            headings = [n for n in page.nodes if n['tag'] == 'h1']
            self.assertEqual(1, len(headings))
            self.assertTrue(headings[0]['text'].strip())
            actions = [n for n in page.nodes if n['main'] and not n['details']
                       and (n['tag'] == 'button' or n['tag'] == 'a' and 'button' in n['attrs'].get('class', '').split())
                       and 'sec' not in n['attrs'].get('class', '').split()]
            self.assertEqual([primary], [n['text'].strip() for n in actions])
            self.assertTrue(any(n['attrs'].get('role') in ('status', 'alert') and n['text'].strip()
                                for n in page.nodes), 'Phrase d’état absente')
            self.assertNotRegex(page.visible, r'\b[0-9a-f]{32,64}\b|\b(?:configuration|cell|attempt|case)-\d+\b|python -m|package_sha256')
            self.assertNotIn('v0.1.0+aaaaaaa', page.visible)
            self.assertNotIn(KEY, page.visible)
            focus = [n for n in page.nodes if not n['hidden'] and not n['details']
                     and 'disabled' not in n['attrs'] and n['attrs'].get('type') != 'hidden'
                     and n['attrs'].get('tabindex') != '-1'
                     and (n['tag'] in ('a', 'button', 'input', 'select', 'textarea', 'summary')
                          or n['attrs'].get('tabindex') == '0')]
            self.assertEqual('#main', focus[0]['attrs'].get('href'))
            self.assertTrue(all(int(n['attrs'].get('tabindex', '0')) <= 0 for n in page.nodes))

    def test_parcours_jusqua_la_rupture_du_contrat(self):
        page, _, _ = self.request('/')
        self.examine(page, '/', 'accueil', 'Décrire mon cas d’usage')
        page, _, _ = self.request(page.link('Décrire mon cas'))
        self.examine(page, '/preparation', 'besoin', 'Préparer cet exemple')
        fields = [n['attrs'].get('name') for n in page.form('/dossiers')['nodes'] if n['tag'] == 'textarea']
        self.assertEqual(['request', 'useful', 'context'], fields)
        page = self.submit(page, '/dossiers', {'request': 'Trop court'}, status=400)
        self.examine(page, '/preparation/dossiers', 'erreur de saisie', 'Corriger et renvoyer')
        self.assertIn('Trop court', page.visible)
        page = self.submit(page, '/dossiers', {'request': 'Transformer des notes de réunion en une liste complète des actions à relire'})
        dossier = page.link('Consulter le cas')
        self.examine(page, '/preparation/dossiers', 'envoi enregistré', 'Consulter le cas d’usage et son avancement')
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'attente', 'Actualiser cet état')
        prep.execute(self.data, self.starts.get_nowait(), self.prepare)
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'clarification', 'Envoyer ma réponse')
        self.assertIn('Quel format', page.visible)
        self.preparation_stage = 'exemple'
        self.submit(page, '/messages', {'message': 'Une liste des actions, sans date inventée'})
        prep.execute(self.data, self.starts.get_nowait(), self.prepare)
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'exemple', 'Oui, c’est le travail à tester')
        self.assertIn('Relever toutes les actions', page.visible)
        self.assertIn('financée par l’opérateur', page.visible)
        correction = page.form('/messages')
        self.assertEqual(['kind', 'message'], [n['attrs'].get('name') for n in correction['nodes']
                                             if n['tag'] in ('select', 'textarea')])
        self.assertTrue(any(n['tag'] == 'details' and n['attrs'].get('class') == 'corr'
                            and 'open' not in n['attrs'] for n in page.nodes))
        self.assertFalse(any('Attendu fictif réservé' in n['text'] for n in page.nodes))
        self.preparation_stage = 'correction'
        self.submit(page, '/messages', {'kind': 'correct', 'message': 'Présenter un tableau avec le responsable de chaque action'})
        prep.execute(self.data, self.starts.get_nowait(), self.prepare)
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'exemple corrigé', 'Oui, c’est le travail à tester')
        self.assertIn('Tableau des actions avec responsable', page.visible)
        previous, _, _ = self.request(page.link('Révision précédente'))
        self.assertNotIn('Tableau des actions avec responsable', previous.visible)
        self.examine(previous, dossier + '/revisions/3', 'retour historique', 'Revenir à la révision courante')
        page, _, _ = self.request(previous.link('Revenir à la révision courante'))
        page = self.submit(page, '/validation', {}, status=200)
        self.examine(page, dossier, 'qualification en attente', 'Actualiser cet état')
        self.assertIn('Qualification en attente', page.visible)
        start = self.starts.get_nowait()
        prep.execute_qualification(self.data, start['qualification_operation'], self.qualifier)
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'exemple qualifié', 'Choisir les modèles')
        self.assertIn('Exemple qualifié', page.visible)
        self.assertNotIn('en attente de préparation par le responsable', page.visible)
        self.assertTrue(any('Les actions et leur format sont vérifiables' in n['text']
                            for n in page.nodes if n['tag'] == 'details'))
        configurations = page.link('Choisir les modèles')
        page, _, _ = self.request(configurations)
        self.examine(page, configurations, 'choix des configurations', 'Enregistrer les configurations')
        page = self.submit(page, '/configurations', {'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], 'tier': 'standard'}, status=400)
        self.assertIn('Action non vérifiée', page.visible)
        self.examine(page, configurations, 'contrat S3 absent', 'Retrouver mes cas d’usage')
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(0, store._connection.execute('SELECT count(*) FROM s3_contracts').fetchone()[0])
            with self.assertRaisesRegex(ValueError, 'Contrat qualifié et approuvé requis'):
                campaigns._current_contract(store, store._connection, dossier.rsplit('/', 1)[1])
            self.assertEqual(0, store._connection.execute('SELECT count(*) FROM s4_campaigns').fetchone()[0])
        for cookies in (SimpleCookie(), SimpleCookie('benchmark_session=' + 'f' * 64)):
            lost, _, raw = self.request(dossier, cookies=cookies, status=403)
            self.examine(lost, dossier, 'accès refusé', 'Retrouver mes cas d’usage')
            self.assertNotIn(b'Transformer des notes', raw)
            self.assertNotIn(b'Attendu fictif', raw)
            empty, _, _ = self.request('/preparation', cookies=cookies)
            self.assertIn('Aucun cas d’usage dans ce navigateur', empty.visible)
            self.assertFalse(any(n['tag'] == 'a' and n['attrs'].get('href') == dossier for n in empty.nodes))
        page, _, _ = self.request('/preparation')
        self.assertIn(dossier, [n['attrs'].get('href') for n in page.nodes])
        self.assertIn('Perdre ou effacer le cookie fait perdre l’accès',
                      next(n['text'] for n in page.nodes if n['tag'] == 'footer'))
        with closing(storage.Store(self.data)) as store:
            prep.close_admission(store)
        page, _, _ = self.request('/preparation')
        self.examine(page, '/preparation', 'appels fermés', 'Préparer cet exemple')
        self.assertIn('Appels fermés', page.visible)
        self.assertTrue(all('disabled' in n['attrs'] for n in page.form('/dossiers')['nodes']
                            if n['tag'] in ('textarea', 'button')))
        denied = self.submit(page, '/dossiers', {'request': 'Une autre demande assez longue pour passer le contrôle de saisie'}, status=403)
        self.assertIn('Cette action n’est pas autorisée', denied.visible)
        page, _, _ = self.request(dossier)
        self.assertIn('Tableau des actions avec responsable', page.visible)
        self.assertEqual(3, len(self.calls))
        self.assertEqual(1, len(self.qualifier.calls))

    def test_acces_openrouter_factice_et_retours(self):
        self.request('/preparation')
        page, _, _ = self.request('/preparation/access')
        self.examine(page, '/preparation/access', 'déconnecté', 'Connecter mon compte OpenRouter')
        form = page.form('/access/start')
        _, headers, _ = self.request(form['action'], form['fields'], status=303)
        self.assertEqual('openrouter.ai', urlsplit(headers['Location']).hostname)
        with patch.object(self.access, 'exchange', return_value=(403, b'{}')):
            refused, _, raw = self.request('/preparation/access/callback?code=code-factice-refuse', status=403)
        self.examine(refused, '/preparation/access/callback', 'autorisation refusée', 'Retrouver mes cas d’usage')
        self.assertNotIn(b'code-factice-refuse', raw)
        page, _, _ = self.request('/preparation/access')
        form = page.form('/access/start')
        self.request(form['action'], form['fields'], status=303)
        _, headers, raw = self.request('/preparation/access/callback?code=code-factice-accepte', status=303)
        self.assertNotIn(b'code-factice-accepte', raw)
        self.assertEqual('/preparation/access', headers['Location'])
        page, _, raw = self.request(headers['Location'])
        self.examine(page, '/preparation/access', 'connecté', 'Revenir à mes cas d’usage')
        self.assertIn('Crédit restant : 18.5 USD', page.visible)
        self.assertNotIn(KEY.encode(), raw)
        form = page.form('/disconnect')
        self.request(form['action'], form['fields'], status=303)
        page, _, _ = self.request('/preparation/access')
        self.examine(page, '/preparation/access', 'déconnexion', 'Connecter mon compte OpenRouter')
        self.assertEqual([], self.calls)
        self.assertEqual([], self.qualifier.calls)

    def test_css_petit_ecran_et_garde_reseau(self):
        _, headers, raw = self.request('/preparation/style.css')
        self.assertIn('text/css', headers['Content-Type'])
        css = raw.decode()
        small = css.split('@media (max-width: 40rem) {', 1)[1].split('@media', 1)[0]
        for rule in ('.two, .tiles { grid-template-columns: 1fr; }',
                     'footer.site .cols { grid-template-columns: 1fr; }',
                     'header.site { align-items: flex-start; flex-direction: column; }'):
            self.assertIn(rule, small)
        self.assertIn('.table-scroll { overflow-x: auto; }', css)
        self.assertIn(':focus-visible { outline: 3px solid var(--focus)', css)
        self.assertIn('white-space: normal;', css)
        with socket.socket() as connection, self.assertRaisesRegex(AssertionError, '^No network$'):
            connection.connect(('192.0.2.1', 443))
        with socket.socket() as connection, self.assertRaisesRegex(AssertionError, '^No network$'):
            connection.connect_ex(('192.0.2.1', 443))


if __name__ == '__main__':
    unittest.main()
