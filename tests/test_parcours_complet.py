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
from benchmark import provider_access, qualification, service, storage
from benchmark_web import server, views
from tests.test_configurations import NOW, model
from tests.test_openrouter_qualification import QualificationTransport
from tests.test_provider_access import AccessTransport, KEY, SECRET
from tests.test_s2_review_regressions import SessionAssistant, response_for
from tests.test_s4_regressions import response
from tests.test_s3_regressions import ACTOR, check, specification
from tests.hermetique.sitecustomize import garder_module as setUpModule  # noqa: F401  réseau local seul, blocage borné


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
        # Assistants liés par l'exécuteur à la clé de chaque session, comme en production
        self.assistants = dict(
            transport=SessionAssistant(lambda operation, request: self.prepare(operation, request), {'model': 'factice'}),
            qualification_transport=SessionAssistant(self.qualifier, self.qualifier.configuration()),
            judgment_transport=SessionAssistant(lambda operation, request: self.fail('Jugement non prévu'),
                                                {'model': 'juge/factice', 'reserve_usd': '0.1'}))
        self.bound = None
        with closing(storage.Store(self.data)) as store:
            rows =[model('openai/gpt-5.6-sol', 'openai', ['high']),
                    model('deepseek/deepseek-v4.1-flash', 'deepseek', []),
                    model('mistralai/mistral-small-2603', 'mistral', ['high', 'none'])]
            rows[0][0]['name'], rows[1][0]['name'], rows[2][0]['name'] = 'Modèle A', 'Modèle B', 'Modèle C'
            document = {'models': [row[0] for row in rows],
                        'endpoints': {row[0]['id']: row[1] for row in rows}}
            store._connection.execute(model_catalogue.TABLE_SQL)
            store._connection.execute('INSERT INTO s2_model_catalogue VALUES (?,?)',
                                      (NOW.isoformat(), storage._strict_json(document)))
        test = self

        class Executor(socketserver.StreamRequestHandler):
            # Lecture bornée : `executor.shutdown()` attend la fin du gestionnaire en cours
            timeout = 10

            def handle(self):
                message = json.loads(self.rfile.readline(), object_pairs_hook=storage._unique_object)
                with closing(storage.Store(test.data)) as store:
                    try:
                        code, value, cookie, start, *bound = service.personal_dispatch(
                            store, message, 'a' * 40, candidate_identity=test.identity,
                            candidate_transport=test.candidate, access_secret=SECRET,
                            access_transport=test.access, **test.assistants)
                        if start:
                            # Les travaux lancés partent avec les assistants liés à la session, comme dans l'exécuteur
                            test.bound = bound
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
        self.criteria = None

    def prepare(self, operation, request):
        self.calls.append(('préparation', operation['operation_id']))
        value = response_for(operation)
        value['cost'].update(amount='0.10', currency='USD', source='Reçu simulé S12')
        result = value['receipt']['result']
        if self.preparation_stage == 'clarification':
            result.update(stage='clarification', explanation='Quel format doit prendre la liste des actions ?', package=None)
        else:
            # La question posée avec l'exemple ne doit plus apparaître une fois l'exemple validé
            result['explanation'] = 'Cet exemple correspond-il bien à votre travail ?'
            result['package']['candidate']['instruction'] = 'Relever toutes les actions dans les notes'
            if self.criteria is not None:
                result['package']['candidate']['criteria'] = self.criteria
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
            # Décision du propriétaire : l’équipe Bench-X n’intervient jamais, aucune page ne compte sur elle
            self.assertNotIn('équipe Bench-X', ' '.join(n['text'] for n in page.nodes))
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
        target = page.link('Décrire mon cas')
        page, _, _ = self.request(target)
        # Sans clé, la page propose d'abord de l'enregistrer : rien d'autre ne ferme la préparation
        self.assertIn('Ajoutez votre clé OpenRouter pour préparer un exemple.', page.visible)
        key_form = page.form('/access/key')
        self.request(key_form['action'], key_form['fields'] | {'key': KEY}, status=303)
        page, _, _ = self.request(target)
        self.examine(page, '/preparation', 'besoin', 'Préparer mon exemple')
        self.assertIn('Votre clé OpenRouter est enregistrée.', page.visible)
        self.assertFalse(any(n['attrs'].get('id') == 'availability' for n in page.nodes))
        fields = [n['attrs'].get('name') for n in page.form('/dossiers')['nodes'] if n['tag'] == 'textarea']
        self.assertEqual(['request', 'useful', 'context'], fields)
        page = self.submit(page, '/dossiers', {'request': 'Trop court'}, status=400)
        self.examine(page, '/preparation/dossiers', 'erreur de saisie', 'Corriger et renvoyer')
        self.assertIn('Trop court', page.visible)
        page = self.submit(page, '/dossiers', {'request': 'Transformer des notes de réunion en une liste complète des actions à relire'})
        dossier = page.link('Actualiser')
        self.examine(page, dossier, 'attente', None)
        self.assertEqual(1, page.visible.count('Actualiser'))
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'clarification', 'Envoyer ma réponse')
        self.assertIn('Quel format', page.visible)
        # Sans exemple, rien à valider ni qualifier : ces blocs n'apparaissent pas encore
        self.assertFalse(any(n['attrs'].get('id') in ('validation', 'comparaison') for n in page.nodes))
        self.assertNotIn('Vérification et approbation de l’exemple', ' '.join(n['text'] for n in page.nodes if n['tag'] == 'summary'))
        self.preparation_stage = 'exemple'
        self.submit(page, '/messages', {'message': 'Une liste des actions, sans date inventée'})
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'exemple', 'Oui, c’est le travail à tester')
        self.assertIn('Relever toutes les actions', page.visible)
        self.assertIn('cette vérification est payée avec votre clé OpenRouter', page.visible)
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
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'exemple corrigé', 'Oui, c’est le travail à tester')
        self.assertIn('Tableau des actions avec responsable', page.visible)
        previous, _, _ = self.request(page.link('Version précédente'))
        self.assertNotIn('Tableau des actions avec responsable', previous.visible)
        self.examine(previous, dossier + '/revisions/3', 'révision précédente', 'Ouvrir la version actuelle')
        page, _, _ = self.request(previous.link('Ouvrir la version actuelle'))
        page = self.submit(page, '/validation', {})
        self.examine(page, dossier, 'qualification en attente', None)
        self.assertIn('Vérification de l’exemple en cours', page.visible)
        self.assertEqual(1, page.visible.count('Actualiser'))
        start = self.starts.get_nowait()
        prep.execute_qualification(self.data, start['qualification_operation'], self.bound[1])
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'exemple qualifié', 'Choisir les modèles')
        self.assertIn('Exemple vérifié, prêt à comparer', page.visible)
        self.assertEqual('Votre cas d’usage', next(n['text'] for n in page.nodes if n['tag'] == 'h1'))
        state = next(n for n in page.nodes if 'state' in n['attrs'].get('class', '').split())
        self.assertIn('Choisir les modèles', state['text'])
        # Qualification automatique : aucune approbation opérateur n'est attendue
        self.assertNotIn('Approbation', ' '.join(n['text'] for n in page.nodes))
        self.assertNotIn('L’équipe Bench-X doit maintenant préparer la comparaison', page.visible)
        self.assertTrue(any('Les actions et leur format sont vérifiables' in n['text']
                            for n in page.nodes if n['tag'] == 'details'))
        configurations = page.link('Choisir les modèles')
        page, _, _ = self.request(configurations)
        self.examine(page, configurations, 'choix des configurations', 'Continuer')
        self.assertNotIn('Gammes généralistes retenues', page.visible)
        self.assertNotIn('date d’ajout au catalogue', page.visible)
        self.assertNotIn('Relevé des modèles du', page.visible)
        self.assertNotIn('2026-09-15T12:00:00+00:00', page.visible)
        form = page.form('/configurations')
        # Niveau adapté à chaque modèle : aucun refus, l'adaptation est montrée avant le lancement
        tuning = next(n for n in page.nodes if n['tag'] == 'details' and 'Ajuster le niveau par modèle' in n['text'])
        self.assertIn('Modèle C', tuning['text'])
        self.assertNotIn('Modèle B', tuning['text'])
        # « Continuer » mène droit au récapitulatif de la sélection qui vient d'être enregistrée
        _, headers, _ = self.request(form['action'], form['fields'] | {
            'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash', 'mistralai/mistral-small-2603'],
            'tier': 'low', 'effort:mistralai/mistral-small-2603': 'none'}, status=303)
        self.assertEqual(dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions', headers['Location'])
        # Un niveau visant un modèle non coché est refusé ; un niveau hors de ceux du modèle retombe sur l'adaptation
        self.request(form['action'], form['fields'] | {
            'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'],
            'tier': 'low', 'effort:mistralai/mistral-small-2603': 'high'}, status=400)
        _, headers, _ = self.request(form['action'], form['fields'] | {
            'models': ['openai/gpt-5.6-sol', 'mistralai/mistral-small-2603'],
            'tier': 'low', 'effort:openai/gpt-5.6-sol': 'max'}, status=303)
        self.assertTrue(headers['Location'].endswith('-c2/conditions'))
        page, _, _ = self.request(headers['Location'])
        models = next(n for n in page.nodes if n['tag'] == 'table')
        self.assertEqual(2, models['text'].count('adapté'))
        older = self.submit(self.request(configurations)[0], '/configurations', {
            'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash', 'mistralai/mistral-small-2603'],
            'tier': 'low', 'effort:mistralai/mistral-small-2603': 'none'})
        older_recap = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c3/conditions'
        models = next(n for n in older.nodes if n['tag'] == 'table')
        self.assertEqual(['Modèle', 'Niveau de raisonnement', 'Coût estimé'],
                         [n['text'] for n in older.nodes if n['tag'] == 'th' and n['attrs'].get('scope') == 'col'])
        self.assertIn('Modèle A', models['text'])
        self.assertIn('Élevé (high) · adapté : ce modèle n’accepte pas Faible (low)', models['text'])
        self.assertIn('Niveau de raisonnement fixe', models['text'])
        self.assertIn('Désactivé (none)', models['text'])
        self.assertEqual(1, models['text'].count('adapté'))
        _, _, raw = self.request(form['action'], form['fields'] | {
            'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'],
            'tier': 'high'}, status=201, json_response=True)
        self.assertEqual('configurations', json.loads(raw)['kind'])
        recap = dossier + '/campaigns/' + json.loads(raw)['current_campaign_id'] + '/conditions'
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(0, store._connection.execute('SELECT count(*) FROM s3_contracts').fetchone()[0])
            self.assertEqual(1, store._connection.execute('SELECT count(*) FROM s2_comparison_contracts').fetchone()[0])
        page, _, raw = self.request(configurations)
        self.examine(page, configurations, 'sélection enregistrée', 'Continuer')
        # La sélection n'est plus résumée ici : le récapitulatif suit « Continuer »
        self.assertFalse(any(n['tag'] == 'h2' and 'Votre sélection' in n['text'] for n in page.nodes))
        self.assertNotIn('Vérifier avant de lancer', page.visible)
        self.assertEqual({'openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'},
                         {n['attrs']['value'] for n in page.nodes if n['tag'] == 'input'
                          and n['attrs'].get('name') == 'models' and 'checked' in n['attrs']})
        page, _, raw = self.request(recap)
        self.examine(page, recap, 'prêt à lancer', 'Lancer le benchmark')
        # Synthèse d'abord : prévision des réponses et réservation de l'évaluation restent deux montants
        synthesis = next(n['text'] for n in page.nodes if n['tag'] == 'p' and 'réponses estimées' in n['text'])
        self.assertRegex(synthesis, r'^2 modèles · réponses estimées : [0-9,]+ USD · réservé pour l’évaluation : '
                                    r'[0-9,]+ USD · crédit restant : 18,5 USD$')
        self.assertIn('Tout est prêt.', page.visible)
        self.assertNotIn('✓', page.visible)
        order = [next(i for i, n in enumerate(page.nodes) if test(n)) for test in (
            lambda n: n['tag'] == 'p' and 'réponses estimées' in n['text'],
            lambda n: n['tag'] == 'button' and n['text'] == 'Lancer le benchmark',
            lambda n: n['tag'] == 'a' and n['text'] == 'Modifier la sélection',
            lambda n: n['tag'] == 'table')]
        self.assertEqual(sorted(order), order)
        self.assertEqual(configurations, page.link('Modifier la sélection'))
        self.assertNotIn('button', page.nodes[order[2]]['attrs'].get('class', ''))
        models = next(n for n in page.nodes if n['tag'] == 'table')
        self.assertIn('table-scroll', next(n for n in reversed(page.nodes[:order[3]])
                                           if n['tag'] == 'div')['attrs'].get('class', ''))
        # Les candidats gardent le nom vu au choix, avec leur effort traduit
        self.assertIn('Modèle A', models['text'])
        self.assertIn('Élevé (high)', models['text'])
        self.assertNotIn('openai/gpt-5.6-sol', page.visible)
        self.assertNotIn(KEY.encode(), raw)
        # Le bouton est la confirmation : aucune case à cocher, `confirm` part en champ caché
        start_form = page.form('/start')
        self.assertEqual([], [n['attrs'].get('name') for n in start_form['nodes']
                              if n['tag'] == 'input' and n['attrs'].get('type') != 'hidden'])
        self.assertEqual('yes', start_form['fields']['confirm'])
        # Une sélection remplacée ne se lance plus : sa page ne propose aucun lancement et mène à la dernière
        stale, _, _ = self.request(older_recap)
        self.assertFalse(any(n['tag'] == 'form' and n['attrs'].get('action', '').endswith('/start') for n in stale.nodes))
        self.assertIn('Cette sélection a été remplacée par une plus récente', stale.visible)
        self.assertEqual(recap, stale.link('Voir la dernière sélection'))
        # Le serveur refuse aussi un envoi direct vers l'ancienne sélection
        refused, _, _ = self.request(older_recap.removesuffix('/conditions') + '/start', start_form['fields'], status=403)
        self.examine(refused, older_recap, 'sélection remplacée', 'Retrouver mes cas d’usage')
        self.assertIn('Cette sélection a été remplacée par une plus récente', refused.visible)
        self.assertEqual(recap, refused.link('Voir la dernière sélection'))
        self.assertTrue(self.starts.empty())
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'comparaison préparée', 'Vérifier puis lancer la comparaison')
        self.assertIn('Comparaison prête à lancer', page.visible)
        # Une sélection sans essai : étape Modèles, aucun résultat à ouvrir
        self.assertEqual(('4Modèles', None), self.etape(page))
        self.assertEqual(recap, page.link('Vérifier puis lancer'))
        page, _, _ = self.request(recap)
        self.assertIn('Relever toutes les actions dans les notes', page.visible)
        self.assertIn('réponses estimées', page.visible)
        criteria = next(n for n in page.nodes if n['tag'] == 'details' and
                        'Détail des critères et des réglages' in n['text'])
        for label in ('Critères', 'Obligations', 'Erreurs éliminatoires', 'Résultat attendu',
                      'Modèles comparés', 'Limites', 'Harnais Pi'):
            self.assertIn(label, criteria['text'])
        for key in ('criteria', 'obligations', 'eliminatory_errors', 'result_expected',
                    'panel', 'limits', 'pi'):
            self.assertNotRegex(criteria['text'], rf'\b{re.escape(key)}\b')
        with patch.object(self, 'candidate', None):
            closed, _, _ = self.request(recap)
            self.examine(closed, recap, 'appels candidats fermés', None)
            self.assertIn('Impossible de lancer pour l’instant : Exécution des essais momentanément indisponible', closed.visible)
            self.assertIn('✕ Exécution des essais', closed.visible)
            self.assertNotIn('Lancement enregistré', closed.visible)
            self.assertFalse(any(f['action'].endswith('/start') for f in closed.forms))
        self.submit(page, '/start', {}, status=303)
        attempts = self.starts.get_nowait()['candidate_attempts']
        self.assertEqual(2, len(attempts))
        # Après un lancement, le choix des modèles repart de la sélection lancée, pas d'une plus ancienne
        chosen, _, _ = self.request(configurations)
        self.assertEqual({'openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'},
                         {n['attrs']['value'] for n in chosen.nodes if n['tag'] == 'input'
                          and n['attrs'].get('name') == 'models' and 'checked' in n['attrs']})
        self.assertIn('selected', next(n for n in chosen.nodes if n['tag'] == 'option'
                                       and n['attrs'].get('value') == 'high')['attrs'])
        page, _, _ = self.request(recap)
        self.examine(page, recap, 'essais en attente', 'Actualiser le suivi')
        self.assertEqual('Benchmark en cours', next(n['text'] for n in page.nodes if n['tag'] == 'h1'))
        self.assertIn('en attente', page.visible)
        self.assertNotIn('Comparaison terminée', page.visible)
        self.assertIn('Modèle B · Niveau de raisonnement fixe : en attente de démarrage', page.visible)
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
        # Parcours à clé personnelle : l'évaluation, payée par la clé, reste à lancer tant qu'aucun jugement n'a tourné
        self.examine(page, recap, 'réponses reçues', 'Évaluer les réponses reçues')
        self.assertIn('Toutes les réponses sont arrivées. Leur évaluation n’est pas encore lancée.', page.visible)
        comparison = recap.removesuffix('/conditions')
        empty, _, raw = self.request(comparison)
        self.examine(empty, comparison, 'jugements absents', None)
        self.assertIn('Des réponses sont arrivées. Leur verdict n’est pas encore disponible.', empty.visible)
        # Lien direct : le lecteur sait ce qui est comparé et sous quelles conditions
        self.assertIn('Transformer des notes de réunion', empty.visible)
        self.assertIn('Revenir au cas d’usage', empty.visible)
        # Réponses reçues, évaluation jamais lancée : le cas mène au suivi, seul endroit où elle se lance
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'évaluation à lancer', 'Lancer l’évaluation')
        self.assertIn('Évaluation à lancer', page.visible)
        self.assertNotIn('Comparaison terminée', page.visible)
        self.assertEqual(recap, page.link('Lancer l’évaluation'))
        self.assertEqual(('5Résultats', recap), self.etape(page))
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
            self.assertIn('Vous n’avez encore aucun cas d’usage dans ce navigateur', empty.visible)
            self.assertFalse(any(n['tag'] == 'a' and n['attrs'].get('href') == dossier for n in empty.nodes))
        page, _, _ = self.request('/preparation')
        self.assertIn(dossier, [n['attrs'].get('href') for n in page.nodes])
        self.assertIn('Sans compte, lié à ce navigateur',
                      next(n['text'] for n in page.nodes if n['tag'] == 'footer'))
        page, _, _ = self.request(configurations)
        self.submit(page, '/configurations', {
            'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'],
            'tier': 'high'}, status=303)
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'nouvelle comparaison préparée', 'Vérifier puis lancer la comparaison')
        # Étape Modèles en cours ; l'onglet Résultats garde la comparaison lancée, pas la sélection en attente
        self.assertEqual(('4Modèles', recap), self.etape(page))
        listed = next(n for n in page.nodes if n['attrs'].get('id') == 'comparaison')
        campaign_links = [n for n in page.nodes if n['tag'] == 'a' and not n['details']
                          and n['text'].startswith('Comparaison ')]
        # Les trois sélections adaptées envoyées plus haut comptent parmi les comparaisons préparées
        self.assertEqual(5, len(campaign_links))
        with closing(storage.Store(self.data)) as store:
            previous = campaigns.inspect(store, recap.split('/')[-2])['manifest']
        texts = [n['text'] for n in campaign_links]
        hrefs = [n['attrs']['href'] for n in campaign_links]
        self.assertEqual(len(texts), len(set(texts)))
        self.assertEqual(len(hrefs), len(set(hrefs)))
        # Évaluation encore à lancer : la comparaison mène à son suivi, où se trouve le bouton
        self.assertIn(recap, hrefs)
        # Le rang suit l'identifiant de campagne : les sélections adaptées enregistrées plus haut le précèdent
        rank = previous['campaign_id'].rsplit('-c', 1)[1]
        self.assertIn(f'Comparaison {rank} du ' + views.date_lisible_utc(previous['conditions']['frozen_at']), texts)
        self.assertIn('évaluation à lancer', listed['text'])
        self.assertIn('comparaison prête à lancer', listed['text'])
        # Seul le retrait de sa clé ferme la préparation d'un visiteur ; aucune fermeture globale n'existe
        access, _, _ = self.request('/preparation/access')
        disconnect = access.form('/disconnect')
        self.request(disconnect['action'], disconnect['fields'], status=303)
        page, _, _ = self.request('/preparation')
        self.assertIn('Ajoutez votre clé OpenRouter pour préparer un exemple.', page.visible)
        self.assertNotIn('Consulter cette page ne lance aucun appel', page.visible)
        self.assertNotIn('fermées pour le moment', page.visible)
        self.assertTrue(all('disabled' in n['attrs'] for n in page.form('/dossiers')['nodes']
                            if n['tag'] in ('textarea', 'button')))
        denied = self.submit(page, '/dossiers', {'request': 'Une autre demande assez longue pour passer le contrôle de saisie'}, status=403)
        self.assertIn('Ajoutez votre clé OpenRouter', denied.visible)
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
        entrants, file, vus, pannes, faux_courants, sauts = {}, [('/', None)], set(), [], [], []
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
                # Un lien vers une autre page arrive en haut ; seul le retour à une ligne de résultats vise une ancre
                chemin, _, ancre = cible.partition('#')
                if ancre and chemin and chemin != path and not ancre.startswith('attempt-'):
                    sauts.append((path, cible))
                cible = chemin
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
        self.assertEqual([], sauts)
        self.assertEqual(set(), self.ATTEIGNABLES - set(entrants))

    def test_redemarrage_clot_une_preparation_jamais_envoyee(self):
        from benchmark import runtime
        page, _, _ = self.request('/')
        target = page.link('Décrire mon cas')
        page, _, _ = self.request(target)
        key_form = page.form('/access/key')
        self.request(key_form['action'], key_form['fields'] | {'key': KEY}, status=303)
        page, _, _ = self.request(target)
        page = self.submit(page, '/dossiers', {'request': 'Transformer des notes de réunion en une liste complète des actions à relire'})
        dossier = page.link('Actualiser')
        self.starts.get_nowait()
        # L'exécuteur redémarre avant d'avoir envoyé la préparation : son fil de travail a disparu
        with closing(storage.Store(self.data)) as store:
            runtime.stop(self.data, store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
            operation = store.inspect_operations()[0]
        self.assertEqual(('RECEIVED', {'status': 'NOT_SENT'}, '0'),
                         (operation['state'], operation['receipt']['result'], operation['observed_cost']['amount']))
        page, _, _ = self.request(dossier)
        self.assertNotIn('Préparation en attente', page.visible)
        # La session renvoie sans intervention ; un seul appel part, celui du nouvel envoi
        page, _, _ = self.request(target)
        page = self.submit(page, '/dossiers', {'request': 'Relever les décisions prises pendant la réunion et leurs responsables'})
        second = page.link('Actualiser')
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(second)
        self.examine(page, second, 'clarification après redémarrage', 'Envoyer ma réponse')
        self.assertEqual(1, len(self.calls))
        self.assertNotEqual(operation['operation_id'], self.calls[0][1])

    def test_incident_fournisseur_de_preparation_relance_sans_renvoi(self):
        """429 reçu : la page dit que la relance part d'elle-même, sans bouton ; la relance prépare l'exemple"""
        test = self

        class Assistant:
            """Premier appel limité par le fournisseur, les suivants normaux"""
            def __call__(self, operation, request):
                if test.calls:
                    return test.prepare(operation, request)
                test.calls.append(('préparation', operation['operation_id']))
                return {'receipt': {'receipt_id': 'fixture-' + operation['operation_id'], 'resources_seen': [], 'result': None,
                                    'observed_configuration': {'model': 'factice', 'incident': 'RATE_LIMITED', 'http': {
                                        'received_at': test.clock.isoformat(), 'response_headers': {}}}},
                        'cost': {'status': 'UNKNOWN', 'amount': None, 'currency': 'USD', 'source': 'Reçu simulé'}}

            def prepare(self, operation, request):
                return storage._strict_json(request)
        self.assistants['transport'] = SessionAssistant(Assistant(), {'model': 'factice'})
        page, _, _ = self.request('/')
        target = page.link('Décrire mon cas')
        page, _, _ = self.request(target)
        key_form = page.form('/access/key')
        self.request(key_form['action'], key_form['fields'] | {'key': KEY}, status=303)
        page, _, _ = self.request(target)
        page = self.submit(page, '/dossiers', {'request': 'Transformer des notes de réunion en une liste complète des actions à relire'})
        dossier = page.link('Actualiser')
        operation = self.starts.get_nowait()
        prep.execute(self.data, operation, self.bound[0])
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'relance automatique en attente', None)
        self.assertIn('OpenRouter limite temporairement les demandes. Nouvelle tentative automatique', page.visible)
        self.assertNotIn('Renvoyez', page.visible)
        self.assertFalse([f for f in page.forms if f['action'].endswith('/messages')])
        # Le minuteur de l'exécuteur, à l'échéance : même message, nouvelle opération liée
        self.clock += timedelta(seconds=30)
        with closing(storage.Store(self.data)) as store:
            retried = prep.retry_preparation(store, operation, self.bound[0], 'a' * 40)
        prep.execute(self.data, retried, self.bound[0])
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'clarification après relance', 'Envoyer ma réponse')
        self.assertNotIn('Nouvelle tentative automatique', page.visible)
        self.assertEqual(2, len(self.calls))

    def test_exclusion_par_convention_cachee_bloquee_puis_corrigee(self):
        """Issue #438 : une exclusion que seule la référence justifie bloque la vérification, puis se corrige

        Modes d'échec couverts :
        1. le constat bloquant laisse choisir les modèles, ou son motif n'est pas consultable ;
        2. aucune correction n'est proposée après le blocage ;
        3. la correction réécrit les notes, ou la version corrigée est vérifiée sans nouvelle validation ;
        4. la référence ou un indice part chez les candidats, ou la convention ajoutée n'y part pas
        """
        notes = ('Décision : le budget formation est validé.\n'
                 'Action : Camille relit le devis avant vendredi.\n'
                 'Sujet : déménagement du stock, à reprendre à la prochaine réunion.\n')
        reference = ('Attendus : la décision sur le budget formation et l’action de Camille. Le déménagement '
                     'du stock est exclu : sujet reporté, ni décision ni action.')
        convention = 'Un sujet reporté à une prochaine réunion n’est ni une décision ni une action.'
        consigne = ['Relever les décisions et les actions à mener dans les notes.']
        envois = []

        def preparer(operation, request):
            self.calls.append(('préparation', operation['operation_id']))
            envois.append(request['outgoing'])
            value = response_for(operation)
            value['cost'].update(amount='0.10', currency='USD', source='Reçu simulé #438')
            result = value['receipt']['result']
            result['explanation'] = 'Cet exemple correspond-il bien à votre travail ?'
            result['package']['candidate'].update(instruction=' '.join(consigne),
                deliverables=['Liste des décisions et des actions'], pieces=[{'name': 'notes.txt', 'content': notes}])
            result['package']['judgment']['pieces'] = [{'name': 'reference.txt', 'content': reference}]
            return value
        self.assistants['transport'] = SessionAssistant(preparer, {'model': 'factice'})
        constat = {'kind': 'decidability', 'severity': 'blocking',
                   'text': 'La référence exclut le sujet « à reprendre à la prochaine réunion » selon une convention '
                           'absente du paquet candidat : la consigne demande les décisions et les actions sans dire '
                           'qu’un sujet reporté en est exclu.'}
        self.qualifier.result = {'qualified': False, 'findings': [constat],
                                 'summary': 'Une exclusion repose sur la seule référence de jugement.'}
        page, _, _ = self.request('/')
        target = page.link('Décrire mon cas')
        page, _, _ = self.request(target)
        key_form = page.form('/access/key')
        self.request(key_form['action'], key_form['fields'] | {'key': KEY}, status=303)
        page, _, _ = self.request(target)
        page = self.submit(page, '/dossiers', {'request': 'Relever les décisions et les actions à mener dans mes notes de réunion'})
        dossier = page.link('Actualiser')
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(dossier)
        page = self.submit(page, '/validation', {})
        prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'exemple bloqué', None)
        self.assertIn('Exemple à revoir avant comparaison', page.visible)
        self.assertIn('Une exclusion repose sur la seule référence de jugement.', page.visible)
        self.assertTrue(any(constat['text'] in n['text'] for n in page.nodes if n['tag'] == 'details'))
        self.assertFalse(any(n['tag'] == 'a' and n['text'] == 'Choisir les modèles' for n in page.nodes))
        _, _, raw = self.request(dossier + '/configurations', status=403)
        self.assertIn('Terminez l’étape précédente avant de poursuivre.', raw.decode())
        self.assertIn(constat['text'], Page(raw).visible)
        self.assertNotIn(reference, raw.decode())
        # Le motif reste consultable et la correction possible depuis la page bloquée
        consigne.append(convention)
        self.submit(page, '/messages', {'kind': 'correct',
                                        'message': 'Dire dans la consigne qu’un sujet reporté n’est ni une décision ni une action'})
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'exemple corrigé à valider', 'Oui, c’est le travail à tester')
        self.assertIn('Cet exemple n’est pas encore validé.', page.visible)
        self.assertIn(convention, page.visible)
        # Le préparateur reçoit la version validée et la correction, pas la référence
        self.assertEqual([{'name': 'notes.txt', 'content': notes}], envois[-1]['previous_candidate']['pieces'])
        self.assertIn('sujet reporté', envois[-1]['message'])
        self.assertNotIn(reference, storage._strict_json(envois))
        # Une version corrigée n'est vérifiée qu'après sa propre validation ; l'ancienne reste validée telle quelle
        self.assertEqual(1, len(self.qualifier.calls))
        with closing(storage.Store(self.data)) as store:
            revisions = [row[0] for row in store._connection.execute(
                "SELECT revision FROM pieces WHERE dossier_id=? AND role='candidate' AND name='notes.txt' "
                'ORDER BY revision', (dossier.rsplit('/', 1)[1],))]
            contents = {store.read_piece(row[0]) for row in store._connection.execute(
                "SELECT piece_id FROM pieces WHERE dossier_id=? AND role='candidate'", (dossier.rsplit('/', 1)[1],))}
            validations = store._connection.execute(
                'SELECT v.revision FROM s2_validations v JOIN s2_revisions r USING(dossier_id,revision) '
                'WHERE v.dossier_id=? AND v.package_sha256=r.package_sha256', (dossier.rsplit('/', 1)[1],)).fetchall()
        self.assertEqual(2, len(revisions))
        self.assertEqual({notes.encode()}, contents)
        self.assertEqual([(revisions[0],)], validations)
        self.qualifier.result = {'qualified': True, 'findings': [],
                                 'summary': 'Chaque exclusion découle de la consigne.'}
        self.submit(page, '/validation', {})
        prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
        self.assertIn(convention, self.qualifier.calls[1][1]['outgoing']['instruction'])
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'exemple corrigé vérifié', 'Choisir les modèles')
        page, _, _ = self.request(page.link('Choisir les modèles'))
        page = self.submit(page, '/configurations', {
            'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], 'tier': 'high'})
        self.submit(page, '/start', {})
        transmis = []

        def candidat(operation, request):
            transmis.append(request)
            return self.candidate(operation, request)
        execution.execute_launch(self.data, self.starts.get_nowait()['candidate_attempts'], candidat,
                                 access_secret=SECRET, access_transport=self.access)
        self.assertEqual(2, len(transmis))
        # Chaque candidat reçoit le même paquet : la convention, les notes intactes, rien de la référence
        self.assertEqual(1, len({storage._strict_json(request['outgoing']) for request in transmis}))
        sortant = transmis[0]['outgoing']
        self.assertEqual([{'name': 'notes.txt', 'content': notes}], sortant['pieces'])
        self.assertIn(convention, sortant['instruction'])
        brut = storage._strict_json(transmis)
        for reserve in (reference, 'reference.txt', 'Attendus'):
            self.assertNotIn(reserve, brut)
        artefacts = os.environ.get('BENCHX_E2E_ARTEFACTS')
        if artefacts:
            Path(artefacts).mkdir(parents=True, exist_ok=True)
            Path(artefacts, 'exclusion-convention-cachee.json').write_text(json.dumps(
                {'constat_bloquant': constat, 'verifications': len(self.qualifier.calls),
                 'consigne_transmise': sortant['instruction'],
                 'pieces_transmises': [piece['name'] for piece in sortant['pieces']],
                 'reference_absente_du_paquet_candidat': reference not in brut},
                ensure_ascii=False, indent=2) + '\n')

    def test_contrat_operateur_bloque_montre_ses_constats_au_demandeur(self):
        """Un contrat opérateur bloqué après la préparation de la comparaison : le refus nomme ce qui bloque

        Modes d'échec couverts :
        1. la page de refus ne dit pas pourquoi l'exemple n'est plus prêt ;
        2. un contrôle réussi est présenté comme un motif ;
        3. la preuve d'un contrôle, qui cite la référence de jugement, s'affiche chez le demandeur
        """
        dossier = self.exemple_qualifie()
        page, _, _ = self.request(dossier + '/configurations')
        self.submit(page, '/configurations', {'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'],
                                              'tier': 'high'})
        recap = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions'
        page, _, _ = self.request(recap)
        self.examine(page, recap, 'prêt à lancer', 'Lancer le benchmark')
        start = page.form('/start')
        motif = 'Aucun passage des notes ne prouve l’obligation sur les actions'
        with closing(storage.Store(self.data)) as store:
            dossier_id = dossier.rsplit('/', 1)[1]
            revision, reference = store._connection.execute(
                "SELECT revision, piece_id FROM pieces WHERE dossier_id=? AND role='judge' "
                'ORDER BY revision DESC LIMIT 1', (dossier_id,)).fetchone()
            reserve = store.read_piece(reference).decode()
            candidate = qualification.draft(store, dossier_id, revision, specification(reference))

            def bloque(contract, resources):
                review = check(contract, resources)
                review['checks'][0].update(status='FAIL', finding=motif)
                return review
            self.assertEqual('BLOCKED', qualification.qualify(
                store, candidate['contract_sha256'], reviewer=ACTOR, check=bloque)['status'])
        for path, fields in ((recap, None), (dossier + '/configurations', None),
                             (start['action'], start['fields'])):
            with self.subTest(route=path):
                page, _, raw = self.request(path, fields, status=403)
                self.examine(page, path, 'exemple bloqué par le contrat opérateur', 'Retrouver mes cas d’usage')
                self.assertIn('Terminez l’étape précédente avant de poursuivre.', page.visible)
                self.assertIn('Ce que la vérification de l’exemple a relevé', page.visible)
                self.assertIn(motif, page.visible)
                self.assertNotIn('Omission témoin repérée', raw.decode())
                self.assertNotIn(reserve, raw.decode())
        _, _, raw = self.request(recap, status=403, json_response=True)
        self.assertEqual([{'text': motif}], json.loads(raw)['findings'])

    @staticmethod
    def etape(page):
        """Étape courante de la barre et cible de l'onglet Résultats (None s'il est désactivé)"""
        current = next(n['text'] for n in page.nodes if n['attrs'].get('aria-current') == 'step')
        results = next(n for n in page.nodes if n['tag'] in ('a', 'span') and n['text'] == '5Résultats')
        return current, results['attrs'].get('href')

    def exemple_qualifie(self):
        """Clé, exemple sans question, validation et vérification : le cas est prêt à comparer"""
        page, _, _ = self.request('/')
        target = page.link('Décrire mon cas')
        page, _, _ = self.request(target)
        key_form = page.form('/access/key')
        self.request(key_form['action'], key_form['fields'] | {'key': KEY}, status=303)
        page, _, _ = self.request(target)
        self.preparation_stage = 'exemple'
        page = self.submit(page, '/dossiers', {'request': 'Transformer des notes de réunion en une liste complète des actions à relire'})
        dossier = page.link('Actualiser')
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(dossier)
        self.assertIn('Cet exemple correspond-il bien à votre travail ?', page.visible)
        self.submit(page, '/validation', {})
        prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
        return dossier

    def juge_factice(self, constat=None, mesure=None):
        """Juge OpenRouter réel sur une connexion HTTP simulée : chaque réponse évaluée ne satisfait pas une obligation

        `constat(critère)` remplace ce FAIL uniforme par (statut, attribution, texte du constat) ;
        `mesure` est la valeur rendue pour chaque critère de qualité, sinon aucun n'est mesuré
        """
        from unittest.mock import Mock
        from benchmark.transports import openrouter
        from tests.test_openrouter_preparation import SYNTHETIC_PROFILE, estimate_for
        profile = openrouter.load_profile(str(SYNTHETIC_PROFILE))
        judge = openrouter.OpenRouterJudgment(None, profile)
        judge._quote = openrouter.configuration(estimate_for(profile), profile)
        http = Mock()
        http.getresponse.return_value.status = 200
        http.getresponse.return_value.length = 0
        http.getresponse.return_value.getheader.return_value = None

        def answer(method, path, *, body, headers):
            content = json.loads(json.loads(body)['messages'][1]['content'])
            output = content['output']
            proof = {'piece_id': output['piece_id'], 'sha256': output['sha256'], 'passage': output['content']}
            findings = [dict(zip(('status', 'attribution', 'finding'), constat(x) if constat else
                                 ('FAIL', 'candidate', 'Obligation non satisfaite')),
                             criterion_id=x['id'], control_id=k, evidence=[proof])
                        for x in content['obligations'] + content['eliminatory_errors'] for k in x['control_ids']]
            measures = [dict(criterion_id=x['id'], value=mesure, unit=x['unit'], evidence=[proof])
                        for x in content['secondary_criteria']] if mesure is not None else []
            result = dict(findings=findings, measures=measures, limits=[], proposed_verdict='SATISFAIT')
            document = dict(id='fixture-judge', model=profile['revision'],
                choices=[dict(finish_reason='stop', message=dict(role='assistant', content=storage._strict_json(result)))],
                usage=dict(cost='0.001'), openrouter_metadata=dict(requested=profile['model'],
                endpoints=dict(available=[dict(provider=profile['routes'][0]['provider_name'], selected=True)])))
            http.getresponse.return_value.read.return_value = storage._strict_json(document).encode()
        http.request.side_effect = answer
        self.enterContext(patch.object(openrouter, 'HTTPSConnection', return_value=http))
        self.assistants['judgment_transport'] = judge
        return http

    def test_comparaison_partielle_terminee_apres_redemarrage(self):
        """Production : 1 réponse inutilisable, les autres évaluées, puis redémarrage de l'exécuteur"""
        from benchmark import automatic_judgment as auto, runtime
        judge = self.juge_factice()
        dossier = self.exemple_qualifie()
        page, _, _ = self.request(dossier + '/configurations')
        recap = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions'
        page = self.submit(page, '/configurations', {
            'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash', 'mistralai/mistral-small-2603'],
            'tier': 'low'})
        self.submit(page, '/start', {})
        attempts = self.starts.get_nowait()['candidate_attempts']

        def candidat(operation, request):
            value = self.candidate(operation, request)
            if request['requested_configuration']['model'] == 'deepseek/deepseek-v4.1-flash':
                value['receipt']['result'].update(output=None, incident='PROVIDER_RESPONSE_INCOMPLETE')
                value['cost'].update(status='KNOWN', amount='0.05')
            return value
        execution.execute_launch(self.data, attempts, candidat, access_secret=SECRET, access_transport=self.access)
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'évaluation à lancer', 'Lancer l’évaluation')
        page, _, _ = self.request(page.link('Lancer l’évaluation'))
        self.assertEqual(recap, urlsplit(page.link('Actualiser le suivi')).path)
        self.submit(page, '/evaluate', {})
        ids = self.starts.get_nowait()['judgment_operations']
        # La réponse absente n'est jamais envoyée au juge
        self.assertEqual(2, len(ids))
        auto.execute_campaign(self.data, ids, self.bound[2])
        self.assertEqual(2, judge.request.call_count)
        with closing(storage.Store(self.data)) as store:
            runtime.stop(self.data, store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        comparison = recap.removesuffix('/conditions')
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'comparaison partielle terminée', 'Voir les résultats')
        state = next(n for n in page.nodes if 'state' in n['attrs'].get('class', '').split())
        self.assertIn('Comparaison terminée', state['text'])
        self.assertIn('1 modèle sur 3 n’a pas donné de réponse exploitable', state['text'])
        self.assertNotIn('Cet exemple correspond-il', state['text'])
        self.assertIn('done', state['attrs']['class'])
        for absent in ('demande votre attention', 'interrompue', 'Voir ce qui s’est passé'):
            self.assertNotIn(absent, page.visible)
        self.assertEqual(comparison, page.link('Voir les résultats'))
        self.assertEqual(('5Résultats', comparison), self.etape(page))
        results, _, _ = self.request(comparison)
        self.examine(results, comparison, 'résultats partiels', None)
        self.assertIn('Aucune réponse exploitable pour Modèle B.', results.visible)
        self.assertNotIn('doit être relue', results.visible)
        self.assertNotIn('les essais se sont arrêtés avant la fin', results.visible)
        followup, _, _ = self.request(recap)
        self.assertIn('Réponses évaluées : 2 sur 2', followup.visible)
        # Une nouvelle sélection ne retire pas l'accès aux résultats de la comparaison évaluée
        page, _, _ = self.request(dossier + '/configurations')
        self.submit(page, '/configurations', {'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], 'tier': 'high'})
        page, _, _ = self.request(dossier)
        self.assertEqual(('4Modèles', comparison), self.etape(page))
        self.assertEqual(2, judge.request.call_count)

    def test_motif_du_verdict_nomme_les_exigences_qui_le_fondent(self):
        """Un « Ne satisfait pas » ne s'explique pas par la partie respectée d'une exigence composée

        Modes d'échec couverts :
        1. le tableau coupe le texte du juge et n'en montre que la partie respectée ;
        2. le tableau tait une exigence en défaut, ou nomme un constat FAIL qui n'a pas fondé le verdict ;
        3. le détail marque « Non respectée » une exigence que le verdict n'a pas retenue ;
        4. une cellule sans réponse exploitable n'est ni nommée ni expliquée ;
        5. une réponse coupée par la limite de sortie part au juge : elle ne pourrait qu'échouer ;
        6. un reçu complet de structure inattendue fait échouer la page de résultats
        """
        from base64 import b64encode
        from hashlib import sha256
        from benchmark import automatic_judgment as auto
        tableau = 'Présenter un tableau à trois colonnes avec une ligne par action'
        echeances = 'Indiquer pour chaque action son responsable et son échéance'
        self.criteria = {'eliminatory': ['Inventer une décision absente du compte rendu'],
                         'obligations': [tableau, echeances], 'quality': []}
        explication = ('Le tableau a bien trois colonnes, mais les actions de relecture '
                       'et d’envoi partagent une même ligne.')

        def constat(critere):
            if critere['description'] == tableau:
                return 'FAIL', 'candidate', explication
            if critere['description'] == echeances:
                # Preuve jugée insuffisante par le juge : ce FAIL ne fonde pas le verdict
                return 'FAIL', 'evidence', 'Échéance peut-être absente'
            return 'FAIL', 'candidate', 'Une action de suivi est inventée'
        judge = self.juge_factice(constat)
        dossier = self.exemple_qualifie()
        page, _, _ = self.request(dossier + '/configurations')
        page = self.submit(page, '/configurations', {
            'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash', 'mistralai/mistral-small-2603'],
            'tier': 'low'})
        self.submit(page, '/start', {})
        attempts = self.starts.get_nowait()['candidate_attempts']

        def coupee(value, content, choices=None):
            """Reçu OpenRouter terminé par la limite de sortie, comme le transport Pi le conserve"""
            body = storage._strict_json({'choices': choices or [{'finish_reason': 'length', 'message': {
                'role': 'assistant', 'content': content}}]}).encode()
            value['receipt']['observed_configuration']['http'] = dict(
                status=200, complete=True, credential_redacted=False,
                body_base64=b64encode(body).decode(), body_sha256=sha256(body).hexdigest())
            value['receipt']['result'].update(output=content, incident='PROVIDER_RESPONSE_INCOMPLETE')

        def candidat(operation, request):
            value = self.candidate(operation, request)
            model = request['requested_configuration']['model']
            if model == 'deepseek/deepseek-v4.1-flash':
                # Reçu complet mais de structure inattendue : aucune cause n'est avancée, la page reste lisible
                coupee(value, None, [None])
                value['cost'].update(status='KNOWN', amount='0.05')
            elif model == 'mistralai/mistral-small-2603':
                coupee(value, 'Action : relire | Responsable : Camille')
            return value
        execution.execute_launch(self.data, attempts, candidat, access_secret=SECRET, access_transport=self.access)
        page, _, _ = self.request(dossier)
        page, _, _ = self.request(page.link('Lancer l’évaluation'))
        self.submit(page, '/evaluate', {})
        auto.execute_campaign(self.data, self.starts.get_nowait()['judgment_operations'], self.bound[2])
        # Seule la réponse complète de Modèle A est jugée
        self.assertEqual(1, judge.request.call_count)
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        results, _, raw = self.request(comparison)
        self.examine(results, comparison, 'résultats et motifs', None)
        html = raw.decode()

        def ligne(nom):
            return next(chunk for chunk in html.split('<tr id="attempt-')[1:]
                        if '<strong>' + nom + '</strong>' in chunk).split('</tr>')[0]
        cellule = ligne('Modèle A')
        self.assertIn('Erreur éliminatoire : Inventer une décision absente du compte rendu. '
                      'Exigence non respectée : ' + tableau + '.', cellule)
        self.assertNotIn('Le tableau a bien', cellule)
        self.assertNotIn(echeances, cellule)
        self.assertNotIn('…', cellule)
        self.assertNotIn('<strong>Modèle C</strong>', html.split('<table')[1])
        self.assertIn('Aucune réponse exploitable pour Modèle C (arrêt pour longueur, '
                      'plafond demandé : 4096 jetons de sortie). Ce modèle', html)
        self.assertIn('Aucune réponse exploitable pour Modèle B. Ce modèle', html)
        _, _, raw = self.request(re.search(r'href="([^"]+/attempts/[^"]+)"', cellule)[1].replace('&amp;', '&'))
        text = raw.decode()
        self.assertIn('0 exigence sur 2 respectée, 1 non respectée, 1 non vérifiable. 1 erreur éliminatoire relevée.', text)
        states = re.findall(r'>([^<>]+)</span><span>([^<]+)</span>', text)
        self.assertIn(('Non respectée', tableau), states)
        self.assertIn(('Non vérifiable', echeances), states)
        self.assertIn(explication, text)

    # Formes relevées sur OpenRouter le 2026-10-02 (valeurs inventées)
    TARIFS_PUBLIES = {
        'deepseek/deepseek-v4.1-flash': {
            'prompt': '0.000002', 'completion': '0.00001', 'web_search': '0.01',
            'overrides': [{'utc_start': 0, 'utc_end': 1400, 'prompt': '0.000003', 'completion': '0.000012'},
                          {'utc_start': 1400, 'utc_end': 0, 'prompt': '0.000001', 'completion': '0.000006'}]},
        'mistralai/mistral-small-2603': {
            'prompt': '0.000002', 'completion': '0.00001', 'image': '0.000002', 'audio': '0.000002',
            'input_audio_cache': '0.0000002', 'input_cache_write_1h': '0.000004', 'internal_reasoning': '0.000012',
            'overrides': [{'min_prompt_tokens': 200000, 'prompt': '0.000004', 'completion': '0.000018',
                           'audio': '0.000004'}]}}
    # Majorant attendu de chaque route : (entrée, sortie)
    MAJORANTS = {'openai/gpt-5.6-sol': ('0.000002', '0.00001'),
                 'deepseek/deepseek-v4.1-flash': ('0.000003', '0.000012'),
                 'mistralai/mistral-small-2603': ('0.000004', '0.000018')}

    def lancer_avec_reprises(self, models, comportement):
        """Relevé aux tarifs publiés, lancement public, puis exécution par une fabrique liée à la clé

        `comportement(valeur, modèle, limite, suivi)` modifie la réponse factice ; `suivi` est l'adresse
        du suivi de la comparaison. Renvoie le dossier, le récapitulatif avant lancement, les envois
        (modèle, limite) et les clés reçues par la fabrique
        """
        with closing(storage.Store(self.data)) as store:
            fetched_at, raw = store._connection.execute('SELECT fetched_at, raw_json FROM s2_model_catalogue').fetchone()
            document = json.loads(raw)
            for model_id, detail in document['endpoints'].items():
                for endpoint in detail['endpoints']:
                    endpoint.update(max_completion_tokens=32768, context_length=64000, pricing=self.TARIFS_PUBLIES.get(
                        model_id, {'prompt': '0.000002', 'completion': '0.00001', 'request': '0'}))
            store._connection.execute('UPDATE s2_model_catalogue SET raw_json=? WHERE fetched_at=?',
                                      (storage._strict_json(document), fetched_at))
        dossier = self.exemple_qualifie()
        suivi = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions'
        page, _, _ = self.request(dossier + '/configurations')
        recap = self.submit(page, '/configurations', {'models': models, 'tier': 'low'})
        self.submit(recap, '/start', {})
        attempts = self.starts.get_nowait()['candidate_attempts']
        envois, cles = [], []

        def candidat(operation, request):
            value = self.candidate(operation, request)
            config = request['requested_configuration']
            envois.append((config['model'], config['parameters']['max_tokens']))
            comportement(value, config['model'], config['parameters']['max_tokens'], suivi)
            return value

        def fabrique(channel_id, key):
            cles.append(key)
            return candidat
        execution.execute_launch(self.data, attempts, transport_factory=fabrique,
                                 access_secret=SECRET, access_transport=self.access)
        return dossier, recap, envois, cles

    @staticmethod
    def coupee(value, content, limit):
        """Réponse arrêtée pour longueur, avec la quantité d'entrée que la reprise exige"""
        from base64 import b64encode
        from hashlib import sha256
        body = storage._strict_json({'choices': [{'finish_reason': 'length', 'message': {
            'role': 'assistant', 'content': content}}], 'usage': {'prompt_tokens': 120, 'completion_tokens': limit}}).encode()
        value['receipt']['observed_configuration']['http'] = dict(
            status=200, complete=True, credential_redacted=False,
            body_base64=b64encode(body).decode(), body_sha256=sha256(body).hexdigest())
        value['receipt']['result'].update(output=content, incident='PROVIDER_RESPONSE_INCOMPLETE')

    def evaluer(self, dossier):
        from benchmark import automatic_judgment as auto
        page, _, _ = self.request(dossier)
        page, _, _ = self.request(page.link('Lancer l’évaluation'))
        self.submit(page, '/evaluate', {})
        auto.execute_campaign(self.data, self.starts.get_nowait()['judgment_operations'], self.bound[2])
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        results, _, raw = self.request(comparison)
        self.examine(results, comparison, 'résultats avec reprises', None)
        return raw.decode()

    def test_reponse_coupee_reprise_au_plus_deux_fois(self):
        """Décision d'Ayo du 2026-10-02 : un modèle arrêté pour longueur est repris au plus deux fois

        Modes d'échec couverts :
        1. la reprise ne part pas, ou part sans la clé du demandeur ;
        2. un modèle est repris plus de deux fois, ou un modèle complet est repris ;
        3. la limite relevée change la demande d'un autre modèle ;
        4. la réponse reprise n'est pas jugée, ou s'affiche sans dire qu'elle est une reprise ;
        5. une chaîne de reprises sans réponse affiche une ligne par essai au lieu d'une seule ;
        6. le récapitulatif avant lancement tait les reprises possibles, calcule leur coût estimé sans
           les tarifs majorants figés, ou le présente comme un maximum garanti (décision d'Ayo : l'entrée
           reste une estimation) ;
        7. une route n'est pas reprise parce que son tarif publié porte des composants qui ne
           s'appliquent pas à la requête (recherche web, image, audio), ou des tarifs par tranche,
           par horaire ou de raisonnement, comme ceux des grands fournisseurs ;
        8. pendant la reprise du dernier modèle, le suivi dit que toutes les réponses sont arrivées
        """
        from decimal import Decimal
        from benchmark_web.fragments import montant_lisible
        judge = self.juge_factice()
        suivis = []

        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
                if limit == 4096:
                    # Tout le budget part en raisonnement, sans texte
                    self.coupee(value, None, limit)
            elif model == 'mistralai/mistral-small-2603':
                if limit == 8192:
                    # Reprise du dernier modèle : toutes les cellules sources ont déjà leur réponse
                    suivis.append(self.request(suivi)[0].visible)
                # Coupé à chaque limite, avec une sortie qui progresse
                self.coupee(value, 'Action : relire' + ' | suite' * (limit // 1024), limit)
        dossier, recap, envois, cles = self.lancer_avec_reprises(list(self.MAJORANTS), comportement)
        self.assertEqual([('openai/gpt-5.6-sol', 4096), ('deepseek/deepseek-v4.1-flash', 4096),
                          ('deepseek/deepseek-v4.1-flash', 8192), ('mistralai/mistral-small-2603', 4096),
                          ('mistralai/mistral-small-2603', 8192), ('mistralai/mistral-small-2603', 16384)], envois)
        self.assertEqual([KEY] * len(envois), cles)
        with closing(storage.Store(self.data)) as store:
            panel = campaigns.inspect(store, dossier.rsplit('/', 1)[1] + '-c1')['manifest']['panel']
        maximum = sum(Decimal(self.MAJORANTS[c['model']][0]) * c['estimate']['assumptions']['input_tokens'] * 2
                      + Decimal(self.MAJORANTS[c['model']][1]) * (8192 + 16384) for c in panel)
        self.assertIn('arrêté par la limite de longueur est relancé au plus deux fois', recap.visible)
        self.assertIn('Coût estimé si tous les modèles étaient repris deux fois : '
                      + montant_lisible(str(maximum)) + ' USD', recap.visible)
        self.assertNotIn('maximal', recap.visible)
        self.assertEqual(1, len(suivis))
        self.assertIn('relancé avec une limite de sortie plus haute', suivis[0])
        self.assertNotIn('Toutes les réponses sont arrivées', suivis[0])
        html = self.evaluer(dossier)
        # Modèle A et la reprise de Modèle B ; les réponses coupées de Modèle C ne partent jamais au juge
        self.assertEqual(2, judge.request.call_count)
        rows = html.split('<tbody>')[1].split('</tbody>')[0]
        self.assertEqual(2, rows.count('<tr id="attempt-'))
        reprise = next(chunk for chunk in rows.split('<tr id="attempt-')[1:] if '<strong>Modèle B</strong>' in chunk)
        self.assertIn('Reprise après arrêt pour longueur : limite de sortie de 8192 jetons', reprise)
        self.assertNotIn('Aucune réponse exploitable pour Modèle B', html)
        self.assertEqual(1, html.count('Aucune réponse exploitable pour Modèle C'))
        self.assertIn('Aucune réponse exploitable pour Modèle C (arrêt pour longueur, plafond demandé : '
                      '16384 jetons de sortie ; 2 reprises).', html)
        self.assertNotIn('Notre conseil', html)
        self.assertNotIn('rien n’est relancé automatiquement', html)

    def test_reprise_reussie_sans_conseil(self):
        """Décision d'Ayo du 2026-10-02 : pas de conseil sur une comparaison qui compte une reprise

        Mode d'échec : chaque modèle a une réponse satisfaisante, dont une après reprise, et le
        conseil est donné comme si chaque configuration n'avait qu'une tentative
        """
        # Critère de qualité mesuré et coûts connus : sans la règle, un conseil serait donné
        self.criteria = {'eliminatory': [], 'obligations': ['Toutes les actions présentes'], 'quality': [
            {'label': 'Clarté du tableau', 'scale': ['excellent', 'acceptable', 'faible'], 'favorable': 'excellent'}]}
        self.juge_factice(lambda critere: ('PASS', 'candidate', 'Exigence respectée'), mesure='excellent')

        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
                if limit == 4096:
                    self.coupee(value, None, limit)
        dossier, _, envois, _ = self.lancer_avec_reprises(
            ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], comportement)
        self.assertEqual(3, len(envois))
        html = self.evaluer(dossier)
        self.assertEqual(2, html.split('<tbody>')[1].count('<tr id="attempt-'))
        self.assertNotIn('Aucune réponse exploitable', html)
        self.assertNotIn('Notre conseil', html)

    def test_reprise_refusee_ne_bloque_pas_la_comparaison(self):
        """Une reprise refusée avant envoi (clé révoquée, par exemple) ne reste pas en attente

        Modes d'échec : l'erreur est ignorée sans trace, la reprise réservée reste en attente
        d'envoi et bloque l'évaluation des autres modèles, ou la comparaison entière s'arrête
        """
        judge = self.juge_factice()

        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
                self.coupee(value, None, limit)
                # Clé déconnectée pendant cet appel : la reprise est refusée avant tout envoi
                with closing(storage.Store(self.data)) as store:
                    for (session_id,) in store._connection.execute('SELECT session_id FROM s2_provider_access').fetchall():
                        provider_access.disconnect(store, session_id, SECRET)
        with self.assertLogs('benchmark.acquisition.execution', 'WARNING') as logs:
            dossier, _, envois, _ = self.lancer_avec_reprises(
                ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], comportement)
        self.assertEqual([('openai/gpt-5.6-sol', 4096), ('deepseek/deepseek-v4.1-flash', 4096)], envois)
        self.assertTrue(any('RECOVERY_STOPPED' in line and 'Denied' in line for line in logs.output), logs.output)
        # L'utilisateur reconnecte sa clé : l'évaluation des autres modèles n'attend pas la reprise refusée
        page, _, _ = self.request(self.request('/')[0].link('Décrire mon cas'))
        key_form = page.form('/access/key')
        self.request(key_form['action'], key_form['fields'] | {'key': KEY}, status=303)
        html = self.evaluer(dossier)
        self.assertEqual(1, judge.request.call_count)
        # La reprise n'a pas eu lieu : la ligne dit la cause de la tentative source
        self.assertIn('Aucune réponse exploitable pour Modèle B (arrêt pour longueur, plafond demandé : 4096 '
                      'jetons de sortie).', html)
        self.assertEqual(1, html.count('Aucune réponse exploitable pour Modèle B'))

    def niveaux_envoyes(self, campaign_id):
        with closing(storage.Store(self.data)) as store:
            return [(c['model'], c['effort'], c.get('effort_requested'), c.get('effort_choice'))
                    for c in campaigns.inspect(store, campaign_id)['manifest']['panel']]

    def test_relecture_du_niveau_choisi_par_modele(self):
        dossier = self.exemple_qualifie()
        configurations = dossier + '/configurations'
        prefix = dossier.rsplit('/', 1)[1]
        page, _, _ = self.request(configurations)
        form = page.form('/configurations')
        # Un niveau hors de l'échelle connue est refusé, pas remplacé
        self.request(form['action'], form['fields'] | {
            'models': ['mistralai/mistral-small-2603', 'openai/gpt-5.6-sol'],
            'tier': 'low', 'effort:mistralai/mistral-small-2603': 'turbo'}, status=400)
        # Choix explicite en tête de sélection ; le second modèle suit le palier commun
        self.request(form['action'], form['fields'] | {
            'models': ['mistralai/mistral-small-2603', 'openai/gpt-5.6-sol'],
            'tier': 'low', 'effort:mistralai/mistral-small-2603': 'high'}, status=303)
        sent = self.niveaux_envoyes(prefix + '-c1')
        self.assertEqual([('mistralai/mistral-small-2603', 'high', None, 'explicit'),
                          ('openai/gpt-5.6-sol', 'high', 'low', None)], sent)
        page, _, _ = self.request(configurations)
        # Relecture exacte : palier commun et choix par modèle, puis renvoi du formulaire tel quel
        fields, select = {'models': []}, None
        for node in page.nodes:
            if node['tag'] == 'select':
                select = node['attrs']['name']
            elif (node['tag'] == 'option' and 'selected' in node['attrs'] and node['attrs']['value']
                  and (select == 'tier' or select.startswith('effort:'))):
                fields[select] = node['attrs']['value']
            elif node['tag'] == 'input' and node['attrs'].get('name') == 'models' and 'checked' in node['attrs']:
                fields['models'].append(node['attrs']['value'])
        self.assertEqual('low', fields['tier'])
        self.assertEqual('high', fields['effort:mistralai/mistral-small-2603'])
        fields['models'].sort(key=['mistralai/mistral-small-2603', 'openai/gpt-5.6-sol'].index)
        self.request(form['action'], page.form('/configurations')['fields'] | fields, status=303)
        self.assertEqual(sent, self.niveaux_envoyes(prefix + '-c2'))
        # Un choix que le modèle n'accepte pas reste visible comme demande, à côté du niveau adapté
        self.request(form['action'], form['fields'] | {
            'models': ['openai/gpt-5.6-sol', 'mistralai/mistral-small-2603'],
            'tier': 'low', 'effort:openai/gpt-5.6-sol': 'max'}, status=303)
        self.assertEqual(('openai/gpt-5.6-sol', 'high', 'max', 'explicit'), self.niveaux_envoyes(prefix + '-c3')[0])
        # Seul l'appel de préparation de l'exemple a eu lieu
        self.assertEqual(1, len(self.calls))

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
        self.assertIn('OpenRouter n’a pas pu vérifier cette clé. Votre clé précédente reste en place.', refused.visible)
        self.assertNotIn(KEY.encode(), raw)
        _, headers, raw = self.request(form['action'], form['fields'] | {'key': KEY}, status=303)
        self.assertEqual('/preparation/access', headers['Location'])
        page, _, raw = self.request(headers['Location'])
        self.examine(page, '/preparation/access', 'clé enregistrée', None)
        self.assertIn('Solde restant selon OpenRouter : 18,5 USD. Plafond de la clé : 20 USD.', page.visible)
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
