"""Parcours HTTP local sur données contrôlées, sans participant ni appel fournisseur"""
from contextlib import closing
from datetime import timedelta
from html.parser import HTMLParser
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import json
import os
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

from benchmark.acquisition import execution
from benchmark.acquisition import campaigns
from benchmark import evaluation, model_catalogue, preparation as prep
from benchmark import provider_access, qualification, service, storage, web_api
from benchmark_web import server, views
from tests.test_configurations import NOW, model
from tests.test_openrouter_qualification import QualificationTransport
from tests.test_provider_access import AccessTransport, KEY, SECRET
from tests.test_s2_review_regressions import response_for
from tests.test_s4_regressions import response


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
        temporary = tempfile.TemporaryDirectory(prefix='s12-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
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
                         'node_version': 'v24.0.0', 'scope': 'Identité factice de fixture'}
        with closing(storage.Store(self.data)) as store:
            store.create_budget('fixture', '100', 'USD')
            prep.admit(store, {'authority_id': 'TEST_ONLY_S12', 'budget_id': 'fixture',
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
                        code, value, cookie, start = web_api.dispatch(
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
        self.enterContext(patch.object(views, 'RELEASE_VERSION', views.RELEASE_VERSION))
        web_thread = threading.Thread(target=server.serve_web,
            args=('127.0.0.1', 0, root, self.sock, 'a' * 40, 'https://fixture.example'))
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
        self.calls.append(('candidat', operation['operation_id']))
        value = response(operation, request)
        second = request['requested_configuration']['model'] == 'deepseek/deepseek-v4.1-flash'
        value['receipt']['result']['output'] = ('Action omise' if second else
            'Action : relire | Responsable : Camille\n<script>contenu inerte</script>\n' + 'Note de fixture\n' * 80)
        value['cost'].update(status='UNKNOWN' if second else 'KNOWN', amount=None if second else '0.10',
                             currency='USD', source='Reçu candidat simulé S12')
        return value

    def request(self, path, fields=None, *, cookies=None, status=200, json_response=False):
        self.clock += timedelta(minutes=1)
        headers = {'Cookie': (self.cookies if cookies is None else cookies).output(header='', sep=';').strip()}
        body = None if fields is None else urlencode(fields, doseq=True).encode()
        if body is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if json_response:
            headers['Accept'] = 'application/json'
            if fields is not None:
                body = storage._strict_json(fields).encode()
                headers['Content-Type'] = 'application/json'
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

    def submit(self, page, suffix, values, status=303):
        form = page.form(suffix)
        result, headers, _ = self.request(form['action'], form['fields'] | values, status=status)
        return self.request(headers['Location'])[0] if status == 303 else result

    def examine(self, page, route, state, primary):
        with self.subTest(route=route, état=state):
            headings = [n for n in page.nodes if n['tag'] == 'h1']
            self.assertEqual(1, len(headings))
            self.assertTrue(headings[0]['text'].strip())
            actions = [n for n in page.nodes if n['main'] and not n['details']
                       and (n['tag'] == 'button' or n['tag'] == 'a' and 'button' in n['attrs'].get('class', '').split())
                       and 'sec' not in n['attrs'].get('class', '').split()]
            self.assertEqual([] if primary is None else [primary], [n['text'].strip() for n in actions])
            # Une phrase d'état statique est un encadré `note` ou un bloc `state` ; `role` reste réservé aux zones mises à jour (BX-22)
            self.assertTrue(any((n['attrs'].get('role') in ('status', 'alert')
                                 or {'note', 'state'} & set((n['attrs'].get('class') or '').split())) and n['text'].strip()
                                for n in page.nodes), 'Phrase d’état absente')
            self.assertNotRegex(page.visible, r'\b[0-9a-f]{32,64}\b|\b(?:configuration|cell|attempt|case)-\d+\b|\b(?:O1|E1)\b|python -m|package_sha256')
            self.assertIn('Révision : aaaaaaa', page.visible)
            self.assertNotIn('v0.1.0', page.visible)
            self.assertNotIn(KEY, page.visible)
            focus = [n for n in page.nodes if not n['hidden'] and not n['details']
                     and 'disabled' not in n['attrs'] and n['attrs'].get('type') != 'hidden'
                     and n['attrs'].get('tabindex') != '-1'
                     and (n['tag'] in ('a', 'button', 'input', 'select', 'textarea', 'summary')
                          or n['attrs'].get('tabindex') == '0')]
            self.assertEqual('#main', focus[0]['attrs'].get('href'))
            self.assertTrue(all(int(n['attrs'].get('tabindex', '0')) <= 0 for n in page.nodes))

    def test_parcours_complet(self):
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
        dossier = page.link('Actualiser cet état')
        self.examine(page, dossier, 'attente', None)
        self.assertEqual(1, page.visible.count('Actualiser cet état'))
        prep.execute(self.data, self.starts.get_nowait(), self.prepare)
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'clarification', 'Envoyer ma réponse')
        self.assertIn('Quel format', page.visible)
        # Sans exemple, rien à valider ni qualifier : ces blocs n'apparaissent pas encore
        self.assertFalse(any(n['attrs'].get('id') in ('validation', 'comparaison') for n in page.nodes))
        self.assertNotIn('Qualification et approbation', ' '.join(n['text'] for n in page.nodes if n['tag'] == 'summary'))
        self.preparation_stage = 'exemple'
        self.submit(page, '/messages', {'message': 'Une liste des actions, sans date inventée'})
        prep.execute(self.data, self.starts.get_nowait(), self.prepare)
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'exemple', 'Oui, c’est le travail à tester')
        self.assertIn('Relever toutes les actions', page.visible)
        self.assertIn('financée par l’opérateur', page.visible)
        position = {key: next(i for i, n in enumerate(page.nodes) if test(n)) for key, test in (
            ('correction', lambda n: n['tag'] == 'details' and n['attrs'].get('class') == 'corr'),
            ('validation', lambda n: n['attrs'].get('id') == 'validation'))}
        # « Corrigez si besoin, puis validez » ; l'ordre du consentement est vérifié dans tests/privacy_browser.test.cjs
        self.assertLess(position['correction'], position['validation'])
        self.assertTrue(any(n['tag'] == 'summary' and n['text'].startswith('Lire « ') for n in page.nodes))
        self.assertFalse(any(n['tag'] == 'summary' and 'Voir le contenu' in n['text'] for n in page.nodes))
        # Admission ouverte : aucun encadré de disponibilité
        self.assertFalse(any(n['attrs'].get('id') == 'availability' for n in page.nodes))
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
        self.examine(previous, dossier + '/revisions/3', 'révision précédente', 'Revenir à la révision courante')
        page, _, _ = self.request(previous.link('Revenir à la révision courante'))
        page = self.submit(page, '/validation', {})
        self.examine(page, dossier, 'qualification en attente', None)
        self.assertIn('Qualification en cours', page.visible)
        self.assertEqual(1, page.visible.count('Actualiser cet état'))
        start = self.starts.get_nowait()
        prep.execute_qualification(self.data, start['qualification_operation'], self.qualifier)
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'exemple qualifié', 'Choisir les modèles')
        self.assertIn('Exemple qualifié', page.visible)
        self.assertEqual('Votre cas d’usage', next(n['text'] for n in page.nodes if n['tag'] == 'h1'))
        state = next(n for n in page.nodes if 'state' in n['attrs'].get('class', '').split())
        self.assertIn('Choisir les modèles', state['text'])
        # Qualification automatique : aucune approbation opérateur n'est attendue
        self.assertNotIn('Approbation', ' '.join(n['text'] for n in page.nodes))
        self.assertNotIn('en attente de préparation par le responsable', page.visible)
        self.assertTrue(any('Les actions et leur format sont vérifiables' in n['text']
                            for n in page.nodes if n['tag'] == 'details'))
        configurations = page.link('Choisir les modèles')
        page, _, _ = self.request(configurations)
        self.examine(page, configurations, 'choix des configurations', 'Enregistrer les configurations')
        self.assertNotIn('Gammes généralistes retenues', page.visible)
        self.assertNotIn('date d’ajout au catalogue', page.visible)
        self.assertNotIn('Relevé des modèles du', page.visible)
        self.assertNotIn('2026-09-15T12:00:00+00:00', page.visible)
        form = page.form('/configurations')
        _, _, raw = self.request(form['action'], form['fields'] | {
            'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'],
            'tier': 'high'}, status=201, json_response=True)
        self.assertEqual('configurations', json.loads(raw)['kind'])
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(0, store._connection.execute('SELECT count(*) FROM s3_contracts').fetchone()[0])
            self.assertEqual(1, store._connection.execute('SELECT count(*) FROM s2_comparison_contracts').fetchone()[0])
        page, _, raw = self.request(configurations)
        self.examine(page, configurations, 'sélection enregistrée', 'Voir le récapitulatif')
        selection = next(n for n in page.nodes if n['tag'] == 'section' and 'Sélection courante' in n['text'])
        self.assertIn('Modèle A', selection['text'])
        self.assertIn('Modèle B', selection['text'])
        self.assertIn('openai/gpt-5.6-sol', selection['text'])
        self.assertIn(b'<summary>Identifiant technique</summary><code>openai/gpt-5.6-sol</code>', raw)
        recap = page.link('Voir le récapitulatif')
        page, _, _ = self.request(recap)
        self.examine(page, recap, 'accès requis', None)
        self.assertIn('Lancement indisponible : ajoutez votre clé Openrouter', page.visible)
        # Les candidats gardent le nom vu au choix, avec leur effort traduit
        self.assertIn('Modèle A · Raisonnement demandé : high (élevé)', page.visible)
        self.assertNotIn('openai/gpt-5.6-sol', page.visible)
        key_form = page.form('/access/key')
        self.assertEqual(recap, key_form['fields']['return'])
        _, headers, raw = self.request(key_form['action'], key_form['fields'] | {'key': KEY}, status=303)
        self.assertEqual(recap, headers['Location'])
        page, _, raw = self.request(recap)
        self.examine(page, recap, 'prêt à lancer', 'Lancer la comparaison')
        self.assertNotIn(KEY.encode(), raw)
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'comparaison préparée', 'Vérifier puis lancer la comparaison')
        self.assertIn('Comparaison prête à lancer', page.visible)
        page, _, _ = self.request(page.link('Vérifier puis lancer'))
        self.assertIn('Relever toutes les actions dans les notes', page.visible)
        self.assertIn('Estimation', page.visible)
        criteria = next(n for n in page.nodes if n['tag'] == 'details' and
                        'Critères et conditions exactes' in n['text'])
        for label in ('Critères', 'Obligations', 'Erreurs éliminatoires', 'Résultat attendu',
                      'Modèles comparés', 'Limites', 'Harnais Pi'):
            self.assertIn(label, criteria['text'])
        for key in ('criteria', 'obligations', 'eliminatory_errors', 'result_expected',
                    'panel', 'limits', 'pi'):
            self.assertNotRegex(criteria['text'], rf'\b{re.escape(key)}\b')
        with patch.object(self, 'candidate', None):
            closed, _, _ = self.request(recap)
            self.examine(closed, recap, 'appels candidats fermés', None)
            self.assertIn('Lancement indisponible : Exécution des essais momentanément indisponible', closed.visible)
            self.assertIn('✕ Exécution des essais', closed.visible)
            self.assertNotIn('Lancement enregistré', closed.visible)
            self.assertFalse(any(f['action'].endswith('/start') for f in closed.forms))
        start_form = page.form('/start')
        self.assertEqual(['confirm'], [n['attrs'].get('name') for n in start_form['nodes']
                                      if n['tag'] == 'input' and n['attrs'].get('type') != 'hidden'])
        self.submit(page, '/start', {'confirm': 'yes'}, status=303)
        attempts = self.starts.get_nowait()['candidate_attempts']
        self.assertEqual(2, len(attempts))
        page, _, _ = self.request(recap)
        self.examine(page, recap, 'essais en attente', 'Actualiser le suivi')
        self.assertIn('en attente', page.visible)
        self.assertNotIn('Comparaison terminée', page.visible)
        self.assertIn('Modèle B · Raisonnement non réglable : en attente de démarrage', page.visible)
        self.assertFalse(any(f['action'].endswith('/cap') for f in page.forms))
        self.assertEqual(3, len(self.calls))
        execution.execute_launch(self.data, attempts[:1], self.candidate,
                                 access_secret=SECRET, access_transport=self.access)
        page, _, _ = self.request(recap)
        self.examine(page, recap, 'réception partielle', 'Actualiser le suivi')
        self.assertIn('réponse reçue', page.visible)
        self.assertIn('en attente', page.visible)
        execution.execute_launch(self.data, attempts[1:], self.candidate,
                                 access_secret=SECRET, access_transport=self.access)
        page, _, _ = self.request(recap)
        self.examine(page, recap, 'réponses reçues', 'Comparer les résultats et lire les preuves')
        self.assertIn('Comparaison terminée', page.visible)
        comparison = page.link('Comparer les résultats')
        empty, _, raw = self.request(comparison)
        self.examine(empty, comparison, 'jugements absents', None)
        self.assertIn('En attente d’évaluation', empty.visible)
        # Lien direct : le lecteur sait ce qui est comparé et sous quelles conditions
        self.assertIn('Transformer des notes de réunion', empty.visible)
        self.assertIn('Revenir au cas d’usage', empty.visible)
        # Le cas d'usage dit que la comparaison existe et mène à ses résultats
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'comparaison terminée', 'Voir les résultats')
        self.assertIn('Comparaison terminée', page.visible)
        self.assertEqual(comparison, page.link('Voir les résultats'))
        self.assertNotIn('choisissez les modèles', page.visible)
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(0, store._connection.execute('SELECT count(*) FROM s5_evaluations').fetchone()[0])
        _, headers, _ = self.request('/preparation', cookies=SimpleCookie())
        foreign = SimpleCookie(headers['Set-Cookie'])
        for private_path in (recap, comparison):
            _, _, raw = self.request(private_path, cookies=foreign, status=403)
            self.assertNotIn(b'Note de fixture', raw)
            self.assertNotIn(b'Transformer des notes', raw)
        self.assertEqual(5, len(self.calls))
        self.assertTrue(self.starts.empty())
        for cookies in (SimpleCookie(), SimpleCookie('benchmark_session=' + 'f' * 64), foreign):
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
        page, _, _ = self.request(configurations)
        self.submit(page, '/configurations', {
            'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'],
            'tier': 'high'}, status=303)
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'nouvelle comparaison préparée', 'Vérifier puis lancer la comparaison')
        listed = next(n for n in page.nodes if n['attrs'].get('id') == 'comparaison')
        campaign_links = [n for n in page.nodes if n['tag'] == 'a' and not n['details']
                          and n['text'].startswith('Comparaison ')]
        self.assertEqual(2, len(campaign_links))
        with closing(storage.Store(self.data)) as store:
            previous = campaigns.inspect(store, recap.split('/')[-2])['manifest']
        texts = [n['text'] for n in campaign_links]
        hrefs = [n['attrs']['href'] for n in campaign_links]
        self.assertEqual(len(texts), len(set(texts)))
        self.assertEqual(len(hrefs), len(set(hrefs)))
        self.assertIn(comparison, hrefs)
        self.assertIn('Comparaison 1 du ' + views.date_lisible_utc(previous['conditions']['frozen_at']), texts)
        self.assertIn('comparaison terminée', listed['text'])
        self.assertIn('comparaison prête à lancer', listed['text'])
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
        self.assertEqual(5, len(self.calls))
        self.assertEqual(1, len(self.qualifier.calls))
        self.verifier_atteignabilite()
        _, headers, _ = self.request(configurations.removesuffix('/configurations') + '/custom-models', status=303)
        self.assertEqual(configurations + '#custom-models', headers['Location'])
        # Une pièce d'exemple se lit en ligne seulement : sa version brute n'est plus servie
        self.request(dossier + '/revisions/1/pieces/' + 'a' * 32, status=403)

    # Pages HTML qu'un lecteur doit pouvoir atteindre depuis l'accueil, en suivant seulement des liens visibles
    # Hors liste : routes POST ou JSON, ancienne adresse redirigée ; le détail d'une tentative,
    # les pièces d'évaluation et l'aperçu de projection exigent une évaluation, absente de ce parcours (l'aperçu est
    # relié depuis une comparaison évaluée dans tests/test_s10_regressions.py) ; les contributions exigent la migration
    # de confidentialité, absente de cette fixture (leur page est couverte par tests/test_privacy_http.py)
    ATTEIGNABLES = frozenset((
        '/', '/mentions-legales', '/cgu', '/confidentialite',
        '/preparation', '/preparation/data', '/preparation/catalogue',
        '/preparation/access', '/preparation/dossiers/<id>', '/preparation/dossiers/<id>/revisions/<n>',
        '/preparation/dossiers/<id>/configurations',
        '/preparation/dossiers/<id>/campaigns/<id>', '/preparation/dossiers/<id>/campaigns/<id>/conditions',
        '/preparation/dossiers/<id>/campaigns/<id>/configurations'))

    def verifier_atteignabilite(self):
        """Parcourir les liens et formulaires GET depuis l'accueil ; l'artefact liste chaque route et ses liens entrants"""
        entrants, file, vus, pannes, faux_courants = {}, [('/', None)], set(), [], []
        while file:
            path, source = file.pop(0)
            route = server.canonical_route(path)
            entrants.setdefault(route, set()).add(source and server.canonical_route(source))
            if path in vus:
                continue
            vus.add(path)
            self.assertLess(len(vus), 400, 'Parcours sans fin')
            headers = {'Cookie': self.cookies.output(header='', sep=';').strip()}
            with closing(HTTPConnection(*self.address, timeout=3)) as connection:
                connection.request('GET', path, headers=headers)
                response = connection.getresponse()
                raw = response.read()
            if route == '/preparation/contributions':
                continue
            if route == '<inconnu>' or response.status >= 400:
                pannes.append((source, path, response.status))
                continue
            if not response.headers.get('Content-Type', '').startswith('text/html'):
                continue
            page = Page(raw)
            courants = [n['attrs']['href'] for n in page.nodes if n['attrs'].get('aria-current') == 'page']
            if courants not in ([], [urlsplit(path).path]):
                faux_courants.append((route, courants))
            cibles = [n['attrs'].get('href', '') for n in page.nodes if n['tag'] == 'a']
            cibles += [n['attrs']['action'] for n in page.nodes
                       if n['tag'] == 'form' and n['attrs'].get('method', '').lower() == 'get']
            for cible in cibles:
                cible = cible.split('#', 1)[0]
                if cible.startswith('/') and not cible.startswith('//') and cible not in server._RESOURCES:
                    file.append((cible, path))
        artefacts = os.environ.get('BENCHX_E2E_ARTEFACTS')
        if artefacts:
            Path(artefacts).mkdir(parents=True, exist_ok=True)
            Path(artefacts, 'atteignabilite.json').write_text(json.dumps(
                {'pages_visitees': len(vus), 'pannes': pannes, 'menu_faux': sorted(set(r for r, _ in faux_courants)),
                 'manquantes': sorted(self.ATTEIGNABLES - set(entrants)), 'routes': {
                    route: sorted(s for s in sources if s) for route, sources in sorted(entrants.items())}},
                ensure_ascii=False, indent=2) + '\n')
        self.assertEqual([], pannes)
        self.assertEqual([], faux_courants)
        self.assertEqual(set(), self.ATTEIGNABLES - set(entrants))

    def test_acces_openrouter_factice_et_retours(self):
        self.request('/preparation')
        page, _, _ = self.request('/preparation/access')
        self.examine(page, '/preparation/access', 'aucune clé', None)
        self.assertIn('Aucune clé enregistrée', page.visible)
        # Un seul mécanisme : la clé saisie ; plus aucune autorisation déléguée
        self.assertFalse(any(f['action'].endswith(('/access/start', '/access/callback')) for f in page.forms))
        self.request('/preparation/access/callback?code=code-factice', status=403)
        form = page.form('/access/key')
        self.assertEqual('/preparation/access', form['fields']['return'])
        with patch.object(self.access, 'verify_result', (401, b'{}')):
            refused, _, raw = self.request(form['action'], form['fields'] | {'key': KEY}, status=403)
        self.examine(refused, '/preparation/access/key', 'clé refusée', 'Retrouver mes cas d’usage')
        self.assertIn('Cette clé Openrouter n’a pas pu être vérifiée', refused.visible)
        self.assertNotIn(KEY.encode(), raw)
        _, headers, raw = self.request(form['action'], form['fields'] | {'key': KEY}, status=303)
        self.assertEqual('/preparation/access', headers['Location'])
        page, _, raw = self.request(headers['Location'])
        self.examine(page, '/preparation/access', 'clé enregistrée', None)
        self.assertIn('Solde annoncé : 18,5 USD. Plafond de la clé : 20 USD.', page.visible)
        self.assertNotIn(KEY.encode(), raw)
        form = page.form('/disconnect')
        _, headers, _ = self.request(form['action'], form['fields'], status=303)
        self.assertEqual('/preparation/access', headers['Location'])
        page, _, _ = self.request('/preparation/access')
        self.examine(page, '/preparation/access', 'clé retirée', None)
        self.assertIn('Aucune clé enregistrée', page.visible)
        self.assertEqual([], self.calls)
        self.assertEqual([], self.qualifier.calls)

    def test_css_petit_ecran_et_garde_reseau(self):
        _, headers, raw = self.request('/preparation/style.css')
        self.assertIn('text/css', headers['Content-Type'])
        css = raw.decode()
        small = css.split('@media (max-width: 40rem) {', 1)[1].split('@media', 1)[0]
        for rule in ('.two { grid-template-columns: 1fr; }',
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
