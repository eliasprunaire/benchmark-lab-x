"""Parcours HTTP local sur données contrôlées, sans participant ni appel fournisseur"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import queue
import re
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit

from benchmark.acquisition import execution
from benchmark.acquisition import campaigns
from benchmark import editorial, evaluation, model_catalogue, preparation as prep, public_results
from benchmark import provider_access, qualification, semantic_help, service, storage
from benchmark_web import projection, server, views
from tests.test_configurations import NOW, model
from tests.test_openrouter_qualification import QualificationTransport
from tests.test_provider_access import AccessTransport, KEY, SECRET
from tests.test_s2_review_regressions import SessionAssistant, response_for
from tests.test_s4_regressions import inputs, manifest, response
from tests.test_s3_regressions import ACTOR, AUTHORITY, check, specification
from tests.test_s5_regressions import EVALUATION_AUTHORITY, RESPONSIBLE, findings
from tests.hermetique.sitecustomize import garder_module as setUpModule  # noqa: F401  réseau local seul, blocage borné

# Lectures complètes du registre tolérées pour un lancement : une par transaction, pas une par contrôle (#528)
LAUNCH_REGISTRY_READS = 10


class Page(HTMLParser):
    """Lire les formulaires, le texte hors dépliants et l'ordre natif de tabulation"""
    def __init__(self, raw):
        super().__init__()
        self.stack, self.nodes, self.forms = [], [], []
        self.feed(raw.decode())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        # Une liste `ul.meta` remplace une chaîne « A · B · C » : la lecture du texte rend le séparateur entre éléments
        liste = next((n for n in reversed(self.stack) if n['tag'] == 'ul'), None)
        if tag == 'li' and liste and 'meta' in (liste['attrs'].get('class') or '').split():
            if liste.get('items'):
                for noeud in self.stack:
                    noeud['text'] += ' · '
                if not any(n['tag'] in ('details', 'head', 'script', 'svg') or 'hidden' in n['attrs'] for n in self.stack):
                    self.nodes.append({'tag': '#text', 'attrs': {}, 'text': '·', 'details': False, 'hidden': False, 'main': False})
            liste['items'] = liste.get('items', 0) + 1
        node = {'tag': tag, 'attrs': attrs, 'text': '',
                'details': any(n['tag'] == 'details' for n in self.stack),
                'fieldset': any(n['tag'] == 'fieldset' for n in self.stack),
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

    def depliant(self, summary):
        """Texte complet d'un dépliant, repéré par le début de son résumé"""
        return next(n['text'] for n in self.nodes if n['tag'] == 'details' and n['text'].lstrip().startswith(summary))

    def link(self, label):
        return next(n['attrs']['href'] for n in self.nodes
                    if n['tag'] == 'a' and label in n['text'])

    def form(self, suffix):
        return next(f for f in self.forms if f['action'].endswith(suffix))

    def choices(self, name):
        """Options natives d'un choix, dans l'ordre du document"""
        index = next(index for index, node in enumerate(self.nodes)
                     if node['tag'] == 'select' and node['attrs'].get('name') == name)
        choices = []
        for node in self.nodes[index + 1:]:
            if node['tag'] == 'select':
                break
            if node['tag'] == 'option':
                choices.append(node)
        return choices

    def options(self, name):
        """Valeurs proposées par un choix natif, dans l'ordre du document"""
        return [node['attrs']['value'] for node in self.choices(name)]

    def form_values(self, suffix):
        """Valeurs réellement soumises par les contrôles natifs, option sélectionnée comprise"""
        form = self.form(suffix)
        values = {}
        for index, node in enumerate(form['nodes']):
            name = node['attrs'].get('name')
            if not name:
                continue
            if node['tag'] == 'input':
                values[name] = node['attrs'].get('value', '')
            elif node['tag'] == 'select':
                options = []
                for after in form['nodes'][index + 1:]:
                    if after['tag'] == 'select':
                        break
                    if after['tag'] == 'option':
                        options.append(after)
                chosen = next((option for option in options if 'selected' in option['attrs']),
                              options[0] if options else None)
                values[name] = '' if chosen is None else chosen['attrs'].get('value', '')
        return values

    def field(self, label):
        """Texte du premier couple dt/dd portant ce libellé"""
        index = next(index for index, node in enumerate(self.nodes)
                     if node['tag'] == 'dt' and node['text'].strip() == label)
        return next(node['text'].strip() for node in self.nodes[index + 1:] if node['tag'] == 'dd')


TYPESAFE_PROBABILITIES = {
    'correspond': {'correspond': 0.9, 'partiel': 0.05, 'sans_correspondance': 0.05},
    'partiel': {'correspond': 0.3, 'partiel': 0.6, 'sans_correspondance': 0.1},
    'sans_correspondance': {'correspond': 0.05, 'partiel': 0.05, 'sans_correspondance': 0.9},
}


def typesafe_document(wire, labels=None, *, model='jev-1.13.0', input_tokens=120,
                      output_tokens=12):
    """Réponse TypeSafe fictive, construite sur les questions réellement envoyées"""
    questions = list(json.loads(wire)['questions'])
    labels = labels or ['sans_correspondance'] * len(questions)
    answers = {}
    for question, label in zip(questions, labels):
        probabilities = TYPESAFE_PROBABILITIES[label]
        answers[question] = {'type': 'choice', 'choice': label, 'confidence': 0.8,
                             'probabilities': dict(probabilities)}
    return storage._strict_json({'model': model, 'answers': answers,
                                 'usage': {'input_tokens': input_tokens,
                                           'output_tokens': output_tokens}}).encode()


class TypeSafeFixture:
    def __init__(self, handler=None):
        self.wires = []
        self.handler = handler

    def post(self, wire):
        self.wires.append(wire)
        if self.handler is not None:
            return self.handler(wire)
        return {'status': 200, 'body': typesafe_document(wire), 'complete': True}


class ParcoursComplet(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s12-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.public = root
        self.data, self.sock = root / 'private', root / 'executor.sock'
        storage.initialize(self.data)
        storage.initialize_preparation(self.data)
        qualification.initialize(self.data)
        campaigns.initialize(self.data)
        evaluation.initialize(self.data)
        provider_access.initialize(self.data)
        semantic_help.initialize(self.data)
        self.starts = queue.Queue()
        self.calls = []
        self.clock = NOW
        self.enterContext(patch.object(prep, '_now', side_effect=lambda: self.clock))
        self.enterContext(patch.object(semantic_help, '_now', side_effect=lambda: self.clock))
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
        # Aide sémantique : désactivée par défaut ; un test l'active avec un transport fictif
        self.semantic = (None, None)
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
                            access_transport=test.access, presentation=projection,
                            semantic_config=test.semantic[0], semantic_transport=test.semantic[1],
                            **test.assistants)
                        if start:
                            # Les travaux lancés partent avec les assistants liés à la session, comme dans l'exécuteur
                            test.bound = bound
                            test.starts.put(start)
                        result = {'status': code, 'value': value.hex() if isinstance(value, bytes) else value,
                                  'piece': isinstance(value, bytes), 'cookie': cookie}
                    except prep.Denied as error:
                        result = service.denied_response(error)
                    except service.Gone:
                        result = {'status': 410, 'value': {
                            'error': 'Ce cas d’usage n’est plus disponible sur le serveur. '
                                     'Vos copies gardées dans ce navigateur restent dans Mes données.',
                            'error_code': 'DOSSIER_EXPIRED'}}
                    except (storage.ConflictError, storage.BudgetError):
                        result = {'status': 409, 'value': {'error': 'Action refusée : consultez le dossier courant.'}}
                    except (ValueError, KeyError, TypeError):
                        result = {'status': 400, 'value': {'error': 'Action non vérifiée. Vérifiez les champs ou consultez le dossier courant.'}}
                try:
                    self.wfile.write((storage._strict_json(result) + '\n').encode())
                except BrokenPipeError:
                    # Le web a cessé d'attendre : l'effet reste enregistré, comme dans l'exécuteur
                    pass

        # Une requête par fil, comme `BoundedUnixServer` : un lancement retenu ne bloque pas le suivi (#524)
        executor = socketserver.ThreadingUnixStreamServer(str(self.sock), Executor)
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
        self.judged = []
        self.judge_systems = []

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

    @staticmethod
    def cookie_header(jar, path):
        """En-tête Cookie selon la portée réelle des cookies, comme un navigateur"""
        target = urlsplit(path).path
        values = []
        for morsel in jar.values():
            scope = morsel['path'] or '/'
            if scope == '/' or target == scope or target.startswith(scope.rstrip('/') + '/'):
                values.append(morsel.key + '=' + morsel.value)
        return '; '.join(values)

    def request(self, path, fields=None, *, cookies=None, status=200, json_response=False,
                extra_headers=None):
        self.clock += timedelta(minutes=1)
        headers = dict(extra_headers or {})
        cookie_header = self.cookie_header(self.cookies if cookies is None else cookies, path)
        if cookie_header:
            headers['Cookie'] = cookie_header
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

    def enable_semantic(self, transport=None, *, enabled=('repertoire',), tariff='10',
                        threshold='0.5'):
        self.semantic = (semantic_help.configure({
            'format': semantic_help.PROFILE_FORMAT,
            'tariff': None if tariff is None else {
                'input_usd_per_million_tokens': tariff, 'currency': 'USD',
                'effective_at': '2026-10-24', 'source': 'https://fixture.example/tarif'},
            'usages': {usage: {'enabled': usage in enabled,
                               'suggestion_threshold': threshold if usage in enabled else None}
                       for usage in semantic_help.USAGES}}), transport or TypeSafeFixture())
        return self.semantic[1]

    def help_submit(self, formulation, *, status=303, submission_id=None):
        page, _, _ = self.request('/evaluations-externes/aide')
        form = page.form('/evaluations-externes/aide')
        fields = dict(form['fields'])
        if submission_id is not None:
            fields['submission_id'] = submission_id
        result, headers, _ = self.request(form['action'], fields | {'formulation': formulation},
                                          status=status)
        if status == 303:
            return self.request(headers['Location'])[0], headers['Location']
        return result, None

    def editorial_help_submit(self, formulation, *, status=303, submission_id=None):
        page, _, _ = self.request('/epreuves/aide')
        form = page.form('/epreuves/aide')
        fields = dict(form['fields'])
        if submission_id is not None:
            fields['submission_id'] = submission_id
        result, headers, _ = self.request(form['action'], fields | {'formulation': formulation},
                                          status=status)
        if status == 303:
            return self.request(headers['Location'])[0], headers['Location']
        return result, None

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
            # Une chaîne « A · B · C » se lit en liste, un élément par ligne ; hors tableau, un bilan compte dès un séparateur
            cellules = [n['text'] for n in page.nodes if n['tag'] in ('td', 'th')]
            for noeud in page.nodes:
                if noeud['tag'] == 'p' and not any(noeud['text'] in cellule for cellule in cellules):
                    bilan = {'note', 'hint', 'synthesis'} & set((noeud['attrs'].get('class') or '').split())
                    self.assertLess(noeud['text'].count(' · '), 1 if bilan else 2, noeud['text'][:80])
            # Une aide de plus de 140 caractères dans un formulaire se replie ; son id reste la cible de aria-describedby
            longues = [noeud['text'].strip()[:60] for noeud in page.nodes
                       if noeud['tag'] in ('p', 'small') and not noeud['details'] and len(noeud['text'].strip()) > 140
                       and 'hint' in (noeud['attrs'].get('class') or '').split() + [noeud['tag']]
                       and (noeud['fieldset'] or any(noeud is n for formulaire in page.forms for n in formulaire['nodes']))]
            self.assertEqual([], longues)
            # Un tableau se lit en liste sur téléphone : rôles explicites et en-tête de colonne par cellule
            self.assertNotIn('faites défiler', page.visible)
            for noeud in page.nodes:
                if noeud['tag'] == 'table':
                    self.assertEqual('table', noeud['attrs'].get('role'))
                elif noeud['tag'] == 'td':
                    self.assertEqual('cell', noeud['attrs'].get('role'))
                    self.assertTrue(noeud['attrs'].get('data-entete'), noeud['text'][:40])
                elif noeud['tag'] == 'th':
                    colonne = noeud['attrs'].get('scope') == 'col'
                    self.assertEqual('columnheader' if colonne else 'rowheader', noeud['attrs'].get('role'))
                    self.assertEqual(colonne, not noeud['attrs'].get('data-entete'))
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

    def test_repertoire_externe_public(self):
        references = json.loads((Path(__file__).parent / 'fixtures/external-references.json').read_text())
        # Le répertoire ne doit même pas relayer une lecture à l’exécuteur privé
        with patch.object(views, 'BENCHMARK_REFERENCES', references), \
                patch.object(server, 'preparation_request', side_effect=AssertionError('Relais privé interdit')):
            before = {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
            home, _, _ = self.request('/')
            self.assertTrue(any(n['tag'] == 'a' and n['text'] == 'Évaluations externes' for n in home.nodes))
            target = home.link('Évaluations externes')
            page, headers, raw = self.request(target)
            self.assertEqual('/evaluations-externes', target)
            self.assertIsNone(headers.get('Set-Cookie'))
            self.assertEqual('no-referrer', headers['Referrer-Policy'])
            self.assertEqual(raw, self.request(target, cookies=SimpleCookie('benchmark_session=invalid'))[2])
            self.assertEqual('/preparation', page.link('Décrire mon cas'))
            self.assertEqual(3, len([n for n in page.nodes if n['tag'] == 'h2' and n['main']]))
            notice, _, _ = self.request(page.link('Atelier Code'))
            for value in ('Petits programmes inventés', 'Ne mesure pas la maintenance', 'fixture-1',
                          '2026-01-10', '2026-02-03', 'Corriger un programme fictif'):
                self.assertIn(value, notice.visible)
            self.assertIn('Source disponible', notice.visible)
            self.assertIn('ne prouvent pas la réussite sur votre besoin particulier', notice.visible)
            self.assertIn('<script>alert(1)</script>', notice.visible)
            self.assertNotIn(b'<script>alert(1)</script>', self.request(target)[2])
            external = next(n for n in notice.nodes if n['tag'] == 'a' and n['attrs']['href'].startswith('https://code.example'))
            self.assertIn('noreferrer', external['attrs']['rel'].split())
            form = page.form('/evaluations-externes')
            self.assertTrue(all('selected' not in n['attrs'] or not n['attrs'].get('value')
                                for n in form['nodes'] if n['tag'] == 'option'))
            filtered, _, _ = self.request(form['action'] + '?' + urlencode(
                {'q': 'CORRIGER', 'category': 'coding', 'verification': 'verified'}))
            self.assertEqual(['Atelier Code fictif <script>alert(1)</script>'],
                             [n['text'] for n in filtered.nodes if n['tag'] == 'h2' and n['main']])
            self.assertEqual({'coding', 'verified'}, {n['attrs']['value'] for n in filtered.nodes
                             if n['tag'] == 'option' and 'selected' in n['attrs']})
            self.assertEqual(raw, self.request(filtered.link('Effacer'))[2])
            empty, _, _ = self.request(target + '?q=introuvable')
            self.assertIn('Aucune évaluation ne correspond', empty.visible)
            self.assertEqual('/preparation', empty.link('Décrire mon cas'))
            unknown, _, _ = self.request(target + '/atelier-inconnu')
            self.assertIn('Inconnue', unknown.visible)
            self.assertIn('Vérification insuffisante', unknown.visible)
            retired, _, _ = self.request(target + '/atelier-retire')
            self.assertIn('Source retirée', retired.visible)
            self.assertNotIn('https://retire.example/', [n['attrs'].get('href') for n in retired.nodes])
            self.request(target + '/absente', status=404)
            for query in ('category=absente', 'verification=best', 'q=a&q=b', 'need=prive', 'q=%FF'):
                self.request(target + '?' + query, status=400)
            injection = '<script>alert(2)</script>'
            escaped, _, escaped_raw = self.request(target + '?' + urlencode({'q': injection}))
            self.assertNotIn(injection.encode(), escaped_raw)
            self.assertEqual(injection, next(n['attrs']['value'] for n in escaped.nodes if n['attrs'].get('name') == 'q'))
            for unsafe in ('javascript:alert(1)', '//evil.example/', 'data:text/html,x',
                           'https://user:password@evil.example/', 'https://evil.example:abc/',
                           'https://good.example\\@evil.example/', 'https://good.example/\nscript'):
                with self.subTest(url=unsafe), patch.dict(references['atelier-inconnu'], url=unsafe):
                    unsafe_page, _, _ = self.request(target + '/atelier-inconnu')
                    self.assertIn('Lien officiel indisponible', unsafe_page.visible)
                    self.assertFalse(any(n['attrs'].get('href') == unsafe for n in unsafe_page.nodes))
                with self.subTest(retired_url=unsafe), patch.dict(references['atelier-retire'], url=unsafe):
                    retired_page, _, _ = self.request(target + '/atelier-retire')
                    self.assertIn('Source retirée', retired_page.visible)
                    self.assertNotIn(unsafe, retired_page.visible)
            with patch.object(views, 'BENCHMARK_REFERENCES', {}):
                unavailable, _, _ = self.request(target)
                self.assertIn('Aucune évaluation externe disponible', unavailable.visible)
                self.assertEqual('/preparation', unavailable.link('Décrire mon cas'))
            # L'aide annonce TypeSafe et les données transmises avant tout envoi ; la lecture ne relaie rien
            help_page, help_headers, help_raw = self.request('/evaluations-externes/aide')
            self.assertIsNone(help_headers.get('Set-Cookie'))
            self.assertEqual('no-referrer', help_headers['Referrer-Policy'])
            for value in ('TypeSafe', 'Jev', 'formulation', 'Aucun dossier privé',
                          'aucune information personnelle ou confidentielle',
                          'reste disponible', 'Demander l’aide'):
                self.assertIn(value, help_page.visible)
            help_form = help_page.form('/evaluations-externes/aide')
            self.assertRegex(help_form['fields']['submission_id'], r'^[0-9a-f]{32}$')
            self.assertEqual(['formulation'],
                             [n['attrs'].get('name') for n in help_form['nodes'] if n['tag'] == 'textarea'])
            self.assertNotIn(b'<script', help_raw)
            self.assertEqual([], self.calls)
            self.assertTrue(self.starts.empty())
            self.assertEqual({}, dict(self.cookies))
            self.assertEqual(before, {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()})
        # Service désactivé : la soumission ne résout aucun secret et n'émet aucun appel
        disabled, _, _ = self.request('/evaluations-externes/aide',
                                      {'submission_id': 'a' * 32, 'formulation': 'Corriger un programme'},
                                      status=200)
        self.assertIn('Aide indisponible', disabled.visible)
        self.assertIn('désactivé', disabled.visible)
        self.assertEqual([], self.calls)
        self.assertTrue(self.starts.empty())
        with patch.object(server, 'preparation_request', side_effect=AssertionError('Relais privé interdit')):
            before = {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
            index, headers, raw = self.request('/evaluations-externes')
            self.assertIsNone(headers.get('Set-Cookie'))
            self.assertEqual('no-referrer', headers['Referrer-Policy'])
            self.assertNotIn(b'<script', raw)
            self.assertEqual(raw, self.request('/evaluations-externes', cookies=SimpleCookie('benchmark_session=invalid'))[2])
            self.assertNotIn(KEY, index.visible)
            self.assertNotIn('HELM', index.visible)
            self.assertNotIn('helm', raw.decode().casefold())
            self.assertIn('ne prouvent pas la réussite sur votre besoin particulier', index.visible)
            self.assertIn('Ayo est responsable de la maintenance de cette sélection.', index.visible)
            self.assertIn('La vérification documentaire du 22 octobre 2026 couvre l’origine, l’objet et les limites.', index.visible)
            self.assertIn('La disponibilité d’un lien reste distincte de cette vérification.', index.visible)
            self.assertEqual(
                ['matharena', 'livecodebench', 'swe-bench', 'mteb', 'artificial-analysis-api'],
                list(views.BENCHMARK_REFERENCES))
            self.assertEqual(
                {'math': 'Mathématiques', 'coding': 'Programmation',
                 'retrieval': 'Recherche documentaire', 'api': 'Performances des services'},
                views.REFERENCE_CATEGORIES)
            self.assertEqual(
                ['MathArena', 'LiveCodeBench', 'SWE-bench', 'MTEB', 'Artificial Analysis, performances API'],
                [n['text'] for n in index.nodes if n['tag'] == 'h2' and n['main']])
            options = {n['attrs'].get('value'): n['text'] for n in index.nodes if n['tag'] == 'option'}
            self.assertEqual('Recherche documentaire', options['retrieval'])
            self.assertEqual('Performances des services', options['api'])
            self.assertNotIn('helm', {key.casefold() for key in options if key})
            notices = {
                'matharena': (
                    'https://matharena.ai/',
                    'Résoudre des problèmes mathématiques, notamment issus de compétitions',
                    'Résultats par compétition et problème',
                    'ne représentent pas tous les besoins mathématiques professionnels',
                    'expected performance',
                    'aucune version unique retenue',
                    'Inconnue',
                    '2026-10-22',
                    'Le classement général n’est pas repris',
                ),
                'livecodebench': (
                    'https://livecodebench.github.io/',
                    'Génération de code, réparation, prédiction de résultats et exécution sur des exercices',
                    'LeetCode, AtCoder et Codeforces',
                    'ne représente pas une modification complète d’application',
                    'Version unique inconnue',
                    '2024 pour l’article initial',
                    'n’est pas certifiée',
                    '2026-10-22',
                ),
                'swe-bench': (
                    'https://www.swebench.com/',
                    'Résoudre des problèmes logiciels issus de dépôts',
                    'Full, Lite, Verified, Multilingual et Multimodal',
                    'Le nom du modèle ne suffit pas',
                    'aucune version unique retenue',
                    'Inconnue',
                    '2026-10-22',
                    'ne sont pas fusionnés',
                ),
                'mteb': (
                    'https://github.com/embeddings-benchmark/mteb',
                    'Évaluer des représentations vectorielles et des systèmes de recherche',
                    'Tâches, langues et modalités multiples',
                    'ne prouve pas la qualité d’une réponse générée',
                    'aucune version globale retenue',
                    'Inconnue',
                    'n’a pas été essayé',
                    '2026-10-22',
                ),
                'artificial-analysis-api': (
                    'https://artificialanalysis.ai/methodology/performance-benchmarking',
                    'Délai avant réception, vitesse de sortie et temps de réponse des services API',
                    'mesures agrégées sur des fenêtres temporelles',
                    'Leur indice global n’entre pas dans Bench-X',
                    '2.2.0',
                    '2026-03-02',
                    'Les résultats évoluent séparément',
                    '2026-10-22',
                ),
            }
            for identity, phrases in notices.items():
                notice, notice_headers, notice_raw = self.request('/evaluations-externes/' + identity)
                self.assertIsNone(notice_headers.get('Set-Cookie'))
                self.assertEqual('no-referrer', notice_headers['Referrer-Policy'])
                self.assertNotIn(b'<script', notice_raw)
                self.assertNotIn(KEY, notice.visible)
                self.assertNotIn('Besoin privé', notice.visible)
                self.assertNotIn('%', notice.visible)
                self.assertIn('Vérification éditoriale effectuée', notice.visible)
                self.assertNotIn('Vérification insuffisante', notice.visible)
                self.assertIn('Source disponible', notice.visible)
                self.assertIn('ne prouvent pas la réussite sur votre besoin particulier', notice.visible)
                for phrase in phrases:
                    self.assertIn(phrase, notice.visible, identity)
                official = next(n for n in notice.nodes if n['tag'] == 'a' and n['attrs'].get('href') == phrases[0])
                self.assertEqual({'noreferrer', 'noopener'}, set(official['attrs']['rel'].split()))
            def titles(page):
                return [n['text'] for n in page.nodes if n['tag'] == 'h2' and n['main']]
            retrieval, _, _ = self.request('/evaluations-externes?category=retrieval')
            self.assertEqual(['MTEB'], titles(retrieval))
            api_page, _, _ = self.request('/evaluations-externes?' + urlencode({'category': 'api', 'q': 'délai'}))
            self.assertEqual(['Artificial Analysis, performances API'], titles(api_page))
            self.assertEqual(['LiveCodeBench', 'SWE-bench'], titles(self.request('/evaluations-externes?category=coding')[0]))
            self.assertEqual(['MathArena'], titles(self.request('/evaluations-externes?category=math')[0]))
            self.assertEqual(5, len(titles(self.request('/evaluations-externes?verification=verified')[0])))
            self.assertIn('Aucune évaluation ne correspond', self.request('/evaluations-externes?verification=unverified')[0].visible)
            self.assertIn('Aucune évaluation ne correspond', self.request('/evaluations-externes?' + urlencode({'q': 'HELM'}))[0].visible)
            self.assertEqual(['MTEB'], titles(self.request('/evaluations-externes?' + urlencode({'q': 'vectorielles'}))[0]))
            self.assertEqual('/preparation', index.link('Décrire mon cas'))
            self.request('/evaluations-externes/helm', status=404)
            self.request('/evaluations-externes/helm-classic', status=404)
            self.request('/evaluations-externes?category=helm', status=400)
            self.assertEqual([], self.calls)
            self.assertTrue(self.starts.empty())
            self.assertEqual({}, dict(self.cookies))
            self.assertEqual(before, {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()})

    def test_epreuve_editoriale_publique(self):
        task, version, other = 'tache-fictive', 'version-1', 'version-2'
        title = 'Tableau <script>alert(1)</script>'
        work, case, limit = ('Préparer un tableau par opération', 'Cinq débits de janvier 2027', 'Ne certifie aucune comptabilité')
        piece = 'Relève les opérations inventées <script>piece</script>'
        label = 'Consigne'

        def bundle(version_id, *, title=title, piece=piece, pieces=None, qualification='ABSENT', results_published=False, extra=None, drop=()):
            pieces = pieces or {'consigne': (label, piece)}
            files, declared = {}, {}
            for pid, (name, body) in pieces.items():
                filename = 'piece-' + pid + '.txt'
                raw = body.encode()
                files[filename] = raw
                declared[pid] = {'file': filename, 'sha256': sha256(raw).hexdigest(), 'label': name}
            manifest = {'schema_version': editorial.SCHEMA, 'task_id': task, 'version_id': version_id,
                        'title': title, 'work': work, 'cases': [case], 'limits': [limit],
                        'qualification_status': qualification, 'results_published': results_published, 'pieces': declared}
            if extra:
                manifest.update(extra)
            for key in drop:
                manifest.pop(key)
            raw = storage._strict_json(manifest).encode()
            identity = sha256(raw).hexdigest()
            return {'manifest': raw, 'package_sha256': identity, 'files': dict(files)}, editorial.fixture_approval(identity)

        def refused(trial, approval, message, operation=editorial.publish):
            with self.assertRaises(ValueError) as raised:
                operation(self.public, trial, approval)
            self.assertEqual(message, str(raised.exception))

        def designated(identity):
            return dict(actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=identity, scope='editorial-trial')

        def designated_withdrawal(identity, version_id):
            return dict(actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=identity,
                        scope='editorial-withdrawal', task_id=task, version_id=version_id)

        with patch.object(server, 'preparation_request', side_effect=AssertionError('Relais privé interdit')):
            before = {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
            home, _, _ = self.request('/')
            self.assertEqual('/epreuves', home.link('Épreuves'))
            empty, headers, raw = self.request('/epreuves')
            self.assertIsNone(headers.get('Set-Cookie'))
            self.assertEqual('no-referrer', headers['Referrer-Policy'])
            self.assertNotIn(b'<script', raw)
            self.assertIn('Aucune épreuve publiée', empty.visible)
            self.assertEqual('/preparation', empty.link('Décrire mon cas d’usage'))
            empty_search, _, _ = self.request('/epreuves?q=tableau')
            self.assertIn('Aucune épreuve publiée', empty_search.visible)
            self.assertIn('Critères actifs', empty_search.visible)
            self.assertEqual('/epreuves', empty_search.link('Effacer la recherche et les filtres'))
            self.assertEqual('/preparation', empty_search.link('Décrire mon cas d’usage'))
            self.assertEqual([], empty_search.options('langue')[1:])
            self.assertEqual(['/epreuves'], [n['attrs']['href'] for n in empty.nodes if n['attrs'].get('aria-current') == 'page'])
            self.assertEqual(raw, self.request('/epreuves', cookies=SimpleCookie('benchmark_session=invalid'))[2])
            leaked, _, leaked_raw = self.request('/epreuves?besoin=prive', status=400)
            self.assertNotIn('prive', leaked.visible)
            self.assertNotIn(b'prive', leaked_raw)
            trial, approval = bundle(version)
            refused(trial, approval, 'Autorité éditoriale refusée')
            self.assertFalse((self.public / 'editorial-trials').exists())
            refused(trial, None, 'Autorité absente')
            refused(trial, {}, 'Autorité absente')
            refused(trial, dict(actor='approbateur-fictif-S6', authority_id='TEST_ONLY_PUBLICATION_S6',
                                projection_sha256=trial['package_sha256'], catalogue=False), 'Autorité éditoriale refusée')
            refused(trial, dict(actor='Ayo', authority_id='Ayo', package_sha256=trial['package_sha256'],
                                scope='editorial-trial'), 'Autorité éditoriale refusée')
            refused(trial, dict(actor='Ayo', authority_id='TEST_ONLY_EDITORIAL_TRIAL', package_sha256=trial['package_sha256'],
                                scope='editorial-trial'), 'Autorité éditoriale refusée')
            refused(trial, dict(approval, package_sha256='f' * 64), 'Approbation visant d’autres octets')
            held, _ = bundle(version)
            held['files'].pop('piece-consigne.txt')
            refused(held, designated(held['package_sha256']), 'Paquet incomplet')
            self.assertFalse((self.public / 'editorial-trials').exists())
            self.assertFalse((self.public / 'editorial-fixture').exists())
            missing, missing_approval = bundle(version)
            missing['files'].pop('piece-consigne.txt')
            refused(missing, missing_approval, 'Paquet incomplet', editorial.fixture_publish)
            altered, altered_approval = bundle(version)
            altered['files']['piece-consigne.txt'] = b'octets alteres'
            refused(altered, altered_approval, 'Octets divergents', editorial.fixture_publish)
            forbidden, forbidden_approval = bundle(version, extra={
                'judge_reference': 'passage réservé au juge', 'private': 'besoin privé', 'secret': 'clef',
                'journal': 'journal brut', 'contribution': 'copie non autorisée'})
            refused(forbidden, forbidden_approval, 'Sélection publique fermée', editorial.fixture_publish)
            role_manifest = json.loads(trial['manifest'])
            role_manifest['pieces']['consigne']['role'] = 'judge'
            role_raw = storage._strict_json(role_manifest).encode()
            refused({'manifest': role_raw, 'package_sha256': sha256(role_raw).hexdigest(), 'files': trial['files']},
                    editorial.fixture_approval(sha256(role_raw).hexdigest()), 'Sélection publique fermée',
                    editorial.fixture_publish)
            refused(*bundle(version, results_published=True), 'Sélection publique fermée', editorial.fixture_publish)
            self.assertFalse((self.public / 'editorial-trials').exists())
            self.assertFalse((self.public / 'editorial-fixture').exists())
            with tempfile.TemporaryDirectory(prefix='epreuve-source-') as source:
                source_piece = Path(source) / 'piece-consigne.txt'
                source_piece.write_bytes(piece.encode())
                trial['files']['piece-consigne.txt'] = source_piece.read_bytes()
                editorial.fixture_publish(self.public, trial, approval)
            self.assertFalse(Path(source).exists())
            self.assertFalse((self.public / 'editorial-trials').exists())
            piece_url = '/epreuves/' + task + '/' + version + '/pieces/consigne'
            catalog, headers, raw = self.request('/epreuves')
            hidden, piece_headers, hidden_raw = self.request(piece_url, status=404)
            absent, absent_headers, absent_raw = self.request('/epreuves/' + task + '/' + version, status=404)
            self.assertIsNone(headers.get('Set-Cookie'))
            self.assertIsNone(piece_headers.get('Set-Cookie'))
            self.assertIsNone(absent_headers.get('Set-Cookie'))
            self.assertEqual('no-referrer', headers['Referrer-Policy'])
            self.assertEqual(raw, self.request('/epreuves', cookies=SimpleCookie('benchmark_session=invalid'))[2])
            self.assertNotIn(b'<script', raw)
            self.assertNotIn(title.encode(), raw)
            self.assertNotIn(piece.encode(), hidden_raw)
            self.assertNotIn(piece.encode(), absent_raw)
            self.assertNotIn(title, catalog.visible)
            self.assertNotIn(piece, hidden.visible)
            self.assertNotIn(title, absent.visible)
            self.assertIn('Aucune épreuve publiée', catalog.visible)
            self.assertNotIn(KEY, catalog.visible)
            self.assertEqual([], editorial.catalog_view(self.public)['versions'])
            with self.assertRaises(ValueError) as raised:
                editorial.reprise_admission(self.public, task, version)
            self.assertEqual('Version publiée requise', str(raised.exception))
            notice = editorial.fixture_version(self.public, task, version)
            self.assertEqual(title, notice['title'])
            self.assertEqual(work, notice['work'])
            self.assertEqual([case], notice['cases'])
            self.assertEqual([limit], notice['limits'])
            self.assertEqual(version, notice['version_id'])
            self.assertEqual('ABSENT', notice['qualification_status'])
            self.assertEqual([(label, piece)], [(item['label'], item['text']) for item in notice['pieces']])
            self.assertFalse(notice['withdrawn'])
            self.assertEqual(piece.encode(), editorial.fixture_piece(self.public, task, version, 'consigne'))
            stored = self.public / 'editorial-fixture' / task / version
            manifest_bytes = (stored / 'manifest.json').read_bytes()
            piece_bytes = (stored / 'piece-consigne.txt').read_bytes()
            self.assertEqual(piece.encode(), piece_bytes)
            deposited = self.public / 'editorial-trials' / task / version
            deposited.mkdir(parents=True)
            for item in stored.iterdir():
                (deposited / item.name).write_bytes(item.read_bytes())
            self.assertEqual([], editorial.catalog_view(self.public)['versions'])
            deposited_page, deposited_headers, deposited_raw = self.request('/epreuves/' + task + '/' + version, status=404)
            _, deposited_piece_headers, deposited_piece = self.request(piece_url, status=404)
            self.assertIsNone(deposited_headers.get('Set-Cookie'))
            self.assertIsNone(deposited_piece_headers.get('Set-Cookie'))
            self.assertNotIn(piece.encode(), deposited_raw)
            self.assertNotIn(piece.encode(), deposited_piece)
            self.assertNotIn(title, deposited_page.visible)
            self.assertNotIn(work, deposited_page.visible)
            self.assertIn('Aucune épreuve publiée', self.request('/epreuves')[0].visible)
            with self.assertRaises(ValueError) as raised:
                editorial.reprise_admission(self.public, task, version)
            self.assertEqual('Autorité éditoriale refusée', str(raised.exception))
            with self.assertRaises(ValueError) as raised:
                editorial.withdraw(self.public, task, version, editorial.fixture_withdrawal(
                    trial['package_sha256'], task, version))
            self.assertEqual('Autorité éditoriale refusée', str(raised.exception))
            self.assertEqual(manifest_bytes, (deposited / 'manifest.json').read_bytes())
            placed = self.public / 'editorial-trials'
            for child in sorted(placed.rglob('*'), key=lambda path: len(path.parts), reverse=True):
                child.rmdir() if child.is_dir() else child.unlink()
            placed.rmdir()
            self.assertIn('Aucune épreuve publiée', self.request('/epreuves')[0].visible)
            with tempfile.TemporaryDirectory(prefix='epreuve-source-') as source:
                source_piece = Path(source) / 'piece-consigne.txt'
                source_piece.write_bytes(piece.encode())
                published = {'manifest': trial['manifest'], 'package_sha256': trial['package_sha256'],
                             'files': {'piece-consigne.txt': source_piece.read_bytes()}}
                editorial.publish(self.public, published, designated(published['package_sha256']))
            self.assertFalse(Path(source).exists())
            page, headers, raw = self.request('/epreuves/' + task + '/' + version)
            _, piece_headers, piece_raw = self.request(piece_url)
            self.assertIsNone(headers.get('Set-Cookie'))
            self.assertIsNone(piece_headers.get('Set-Cookie'))
            self.assertEqual('no-referrer', headers['Referrer-Policy'])
            self.assertEqual('text/plain; charset=utf-8', piece_headers['Content-Type'])
            self.assertEqual(piece.encode(), piece_raw)
            self.assertEqual(raw, self.request('/epreuves/' + task + '/' + version,
                                               cookies=SimpleCookie('benchmark_session=invalid'))[2])
            self.assertNotIn(b'<script', raw)
            self.assertNotIn(title.encode(), raw)
            for value in (title, work, case, limit, 'Version ' + version, label, piece,
                          'Qualification absente', 'Aucune preuve de qualification n’est publiée',
                          'Aucun résultat publié', 'n’autorise aucune exécution'):
                self.assertIn(value, page.visible)
            self.assertNotIn('Qualification réussie', page.visible)
            self.assertNotIn(KEY, page.visible)
            self.assertEqual([], [n['attrs']['href'] for n in page.nodes if n['attrs'].get('aria-current') == 'page'])
            self.assertEqual('/preparation', page.link('Décrire mon cas d’usage'))
            self.assertEqual('/epreuves', page.link('Toutes les épreuves'))
            catalog, _, _ = self.request('/epreuves')
            self.assertIn(title, catalog.visible)
            self.assertNotIn('Aucune épreuve publiée', catalog.visible)
            stored = self.public / 'editorial-trials' / task / version
            manifest_bytes = (stored / 'manifest.json').read_bytes()
            piece_bytes = (stored / 'piece-consigne.txt').read_bytes()
            self.assertEqual(piece.encode(), piece_bytes)
            blocked, _ = bundle('version-interdite', extra={'journal': 'journal brut'})
            refused(blocked, designated(blocked['package_sha256']), 'Sélection publique fermée')
            self.assertFalse((self.public / 'editorial-trials' / task / 'version-interdite').exists())
            self.assertEqual(raw, self.request('/epreuves/' + task + '/' + version)[2])
            self.assertEqual(manifest_bytes, (stored / 'manifest.json').read_bytes())
            replacement, _ = bundle(version, title='Tableau remplacé')
            refused(replacement, designated(replacement['package_sha256']), 'Version existante divergente')
            self.assertEqual(manifest_bytes, (stored / 'manifest.json').read_bytes())
            revised, _ = bundle(other, title='Tableau révisé', piece='Deuxième version des opérations')
            editorial.publish(self.public, revised, designated(revised['package_sha256']))
            self.assertEqual(raw, self.request('/epreuves/' + task + '/' + version)[2])
            self.assertEqual(piece.encode(), self.request(piece_url)[2])
            both, _, _ = self.request('/epreuves')
            self.assertIn(title, both.visible)
            self.assertIn('Tableau révisé', both.visible)
            # La version est une des caractéristiques repliées de chaque entrée
            versions = [n['text'] for n in both.nodes if n['tag'] == 'dd']
            self.assertIn(version, versions)
            self.assertIn(other, versions)
            self.assertEqual(trial['package_sha256'], editorial.reprise_admission(self.public, task, version)['package_sha256'])
            self.assertEqual(before, {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()})
            refused_withdrawal = dict(actor='approbateur-fictif-S6', authority_id='TEST_ONLY_PUBLICATION_S6',
                                      package_sha256=trial['package_sha256'], scope='editorial-withdrawal',
                                      task_id=task, version_id=version)
            with self.assertRaises(ValueError) as raised:
                editorial.withdraw(self.public, task, version, refused_withdrawal)
            self.assertEqual('Autorité éditoriale refusée', str(raised.exception))
            with self.assertRaises(ValueError) as raised:
                editorial.withdraw(self.public, task, version, dict(
                    designated_withdrawal(trial['package_sha256'], version), package_sha256='a' * 64))
            self.assertEqual('Approbation visant d’autres octets', str(raised.exception))
            self.assertEqual(raw, self.request('/epreuves/' + task + '/' + version)[2])
            second_raw = self.request('/epreuves/' + task + '/' + other)[2]
            editorial.withdraw(self.public, task, version, designated_withdrawal(trial['package_sha256'], version))
            self.assertEqual(manifest_bytes, (stored / 'manifest.json').read_bytes())
            self.assertEqual(piece_bytes, (stored / 'piece-consigne.txt').read_bytes())
            withdrawn, withdrawn_headers, withdrawn_raw = self.request('/epreuves/' + task + '/' + version)
            self.assertIsNone(withdrawn_headers.get('Set-Cookie'))
            self.assertIn('Cette version est retirée', withdrawn.visible)
            self.assertIn('ne sont plus disponibles', withdrawn.visible)
            self.assertIn('Aucune nouvelle reprise de cette version n’est admise', withdrawn.visible)
            self.assertNotIn(title, withdrawn.visible)
            self.assertNotIn(piece, withdrawn.visible)
            self.assertNotIn(work, withdrawn.visible)
            self.assertNotIn(b'<script', withdrawn_raw)
            hidden, _, hidden_raw = self.request(piece_url, status=404)
            self.assertNotIn(piece.encode(), hidden_raw)
            self.assertNotIn(piece, hidden.visible)
            self.assertEqual(second_raw, self.request('/epreuves/' + task + '/' + other)[2])
            self.assertEqual('Deuxième version des opérations'.encode(),
                             self.request('/epreuves/' + task + '/' + other + '/pieces/consigne')[2])
            self.assertEqual('text/plain; charset=utf-8',
                             self.request('/epreuves/' + task + '/' + other + '/pieces/consigne')[1]['Content-Type'])
            remaining, _, _ = self.request('/epreuves')
            self.assertNotIn(title, remaining.visible)
            self.assertNotIn(piece, remaining.visible)
            self.assertIn('Tableau révisé', remaining.visible)
            self.assertNotIn('Version ' + version, remaining.visible)
            with self.assertRaises(ValueError) as raised:
                editorial.reprise_admission(self.public, task, version)
            self.assertEqual('Nouvelle reprise refusée', str(raised.exception))
            self.assertEqual(revised['package_sha256'], editorial.reprise_admission(self.public, task, other)['package_sha256'])
            reactivated, _ = bundle(version, title='Tableau réactivé')
            refused(reactivated, designated(reactivated['package_sha256']), 'Version retirée')
            self.assertIn('Cette version est retirée', self.request('/epreuves/' + task + '/' + version)[0].visible)
            self.assertEqual(second_raw, self.request('/epreuves/' + task + '/' + other)[2])
            self.assertNotIn(piece.encode(), self.request(piece_url, status=404)[2])
            self.request('/publications/' + 'a' * 64 + '/index.html', status=404)
            self.assertFalse((self.public / 'active.json').exists())
            self.assertEqual([], self.calls)
            self.assertTrue(self.starts.empty())
            self.assertEqual({}, dict(self.cookies))
            self.assertEqual(before, {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()})

    def test_recherche_epreuves_editoriales(self):
        """#464 : recherche déterministe, filtres déclarés, versions exactes et critères conservés"""
        task = 'tache-fictive'
        calc_characteristics = {'expected_result': 'Tableau des débits', 'inputs': ['Textes fournis'],
                                'language': 'Français', 'tools': ['Tableur'], 'difficulty': 'Facile'}
        redac_characteristics = {'expected_result': 'Note de synthèse', 'inputs': ['Bibliothèque figée'],
                                 'language': 'Anglais', 'tools': [], 'difficulty': 'Difficile'}
        calc = self.publier_epreuve('version-calcul', title='Tableau des débits',
                                    work='Préparer un tableau par opération',
                                    characteristics=calc_characteristics)
        redac = self.publier_epreuve('version-redaction', title='Note de synthèse',
                                     work='Rédiger une note de synthèse', qualification='QUALIFIED',
                                     characteristics=redac_characteristics)
        old = self.publier_epreuve('version-ancienne', title='Ancienne épreuve', schema=editorial.SCHEMA_V1)
        enriched = self.publier_epreuve('version-enrichie', title='Ancienne épreuve enrichie',
                                        characteristics={'language': 'Français'})
        retired = self.publier_epreuve('version-retiree', title='Épreuve retirée',
                                       characteristics={'language': 'Langue retirée', 'tools': ['Outil retiré']})
        editorial.withdraw(self.public, task, 'version-retiree', dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=retired['package_sha256'],
            scope='editorial-withdrawal', task_id=task, version_id='version-retiree'))
        # Autorité fictive et paquet déposé hors de son répertoire : jamais candidats publics
        fixture = self.construire_epreuve('version-fictive', title='Épreuve fictive',
                                          characteristics={'language': 'Langue fictive', 'tools': ['Outil fictif']})
        editorial.fixture_publish(self.public, fixture, editorial.fixture_approval(fixture['package_sha256']))
        deposited = self.public / 'editorial-trials' / task / 'version-fictive'
        deposited.mkdir(parents=True)
        for item in (self.public / 'editorial-fixture' / task / 'version-fictive').iterdir():
            (deposited / item.name).write_bytes(item.read_bytes())
        # Paquet malformé dans le répertoire public : illisible, donc jamais candidat
        invalid = self.public / 'editorial-trials' / task / 'version-invalide'
        invalid.mkdir()
        (invalid / 'manifest.json').write_text('{"language":"Langue invalide"}')
        # Dossiers privés et contributions voisins : aucune lecture pour servir la recherche
        private_bundle = self.construire_epreuve('version-privee', title='Épreuve privée',
                                                 characteristics={'language': 'Langue privée'})
        editorial.publish(self.data, private_bundle, dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=private_bundle['package_sha256'],
            scope='editorial-trial'))
        contributed = self.construire_epreuve('version-contribuee', title='Contribution voisine',
                                              characteristics={'language': 'Langue contribuée'})
        contribution = self.public / 'contributions' / task / 'version-contribuee'
        contribution.mkdir(parents=True)
        (contribution / 'manifest.json').write_bytes(contributed['manifest'])
        for name, raw in contributed['files'].items():
            (contribution / name).write_bytes(raw)
        # Enrichir une version existante exige une nouvelle version approuvée
        attempt = self.construire_epreuve('version-ancienne', title='Ancienne épreuve',
                                          characteristics={'language': 'Français'})
        with self.assertRaises(ValueError) as raised:
            editorial.publish(self.public, attempt, dict(
                actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=attempt['package_sha256'],
                scope='editorial-trial'))
        self.assertEqual('Version existante divergente', str(raised.exception))
        old_folder = self.public / 'editorial-trials' / task / 'version-ancienne'
        old_manifest = (old_folder / 'manifest.json').read_bytes()
        old_piece = (old_folder / 'piece-consigne.txt').read_bytes()
        self.assertEqual(old['package_sha256'], sha256(old_manifest).hexdigest())

        def query_path(fields):
            return '/epreuves?' + urlencode([(key, fields[key]) for key in editorial.SEARCH_PARAMETERS
                                             if fields.get(key)])

        def search(fields):
            return self.request(query_path(fields))[0]

        with patch.object(server, 'preparation_request', side_effect=AssertionError('Relais privé interdit')):
            before_public = {str(path.relative_to(self.public)): path.read_bytes()
                             for path in self.public.rglob('*') if path.is_file()}
            before_private = {str(path.relative_to(self.data)): path.read_bytes()
                              for path in self.data.rglob('*') if path.is_file()}
            with closing(storage.Store(self.data)) as store:
                before_operations = store._connection.execute('SELECT count(*) FROM operations').fetchone()[0]

            catalog, catalog_headers, catalog_raw = self.request('/epreuves')
            self.assertIsNone(catalog_headers.get('Set-Cookie'))
            self.assertEqual('no-referrer', catalog_headers['Referrer-Policy'])
            self.assertNotIn(b'<script', catalog_raw)
            # La liste montre quatre caractéristiques par épreuve ; les cinq autres se replient dans chaque entrée
            for value in ('Tableau des débits', 'Note de synthèse', 'Ancienne épreuve', 'Ancienne épreuve enrichie',
                          'Travail évalué', 'Résultat attendu', 'Qualification', 'Résultats publiés', 'Aucun résultat publié'):
                self.assertIn(value, catalog.visible)
            autres = ' '.join(n['text'] for n in catalog.nodes if n['tag'] == 'details' and 'Autres caractéristiques' in n['text'])
            for value in ('Version', 'version-calcul', 'version-redaction', 'version-ancienne',
                          'version-enrichie', 'Entrées', 'Langue', 'Outils', 'Difficulté',
                          'Textes fournis', 'Bibliothèque figée', 'Français', 'Anglais', 'Tableur',
                          'Aucun outil déclaré'):
                self.assertIn(value, autres)
            for value in ('Entrées', 'Langue', 'Outils', 'Difficulté'):
                self.assertNotIn(value, catalog.visible)
            self.assertNotIn('Épreuve retirée', catalog.visible)
            self.assertNotIn('Épreuve fictive', catalog.visible)
            self.assertNotIn('Épreuve privée', catalog.visible)
            self.assertNotIn('Contribution voisine', catalog.visible)
            # Les filtres se replient sous « Affiner la recherche », fermés sans filtre actif ; le champ de recherche reste visible
            affiner = next(n for n in catalog.nodes if n['tag'] == 'details' and n['text'].lstrip().startswith('Affiner la recherche'))
            self.assertNotIn('open', affiner['attrs'])
            self.assertIn('Langue', affiner['text'])
            self.assertFalse(next(n for n in catalog.nodes if n['attrs'].get('name') == 'q')['details'])
            self.assertNotIn('Langue invalide', catalog.visible)
            self.assertNotIn('Langue privée', catalog.visible)
            self.assertNotIn('Langue contribuée', catalog.visible)
            self.assertEqual(['', 'Anglais', 'Français'], catalog.options('langue'))
            self.assertEqual(['', 'Tableur'], catalog.options('outil'))
            self.assertEqual(['', 'Bibliothèque figée', 'Textes fournis'], catalog.options('entree'))
            self.assertEqual(['', 'Difficile', 'Facile'], catalog.options('difficulte'))
            self.assertEqual(['', 'ABSENT', 'QUALIFIED'], catalog.options('qualification'))
            self.assertNotIn('Langue fictive', catalog.options('langue'))
            self.assertNotIn('Outil fictif', catalog.options('outil'))
            self.assertNotIn('Langue retirée', catalog.options('langue'))
            self.assertNotIn('Outil retiré', catalog.options('outil'))
            for absent in ('Langue invalide', 'Langue privée', 'Langue contribuée'):
                self.assertNotIn(absent, catalog.options('langue'))

            # L’aide facultative part du catalogue sans relais privé ni texte dans l’adresse
            help_link = catalog.link('Demander une aide sémantique facultative')
            self.assertEqual('/epreuves/aide', help_link)
            help_page, help_headers, help_raw = self.request(help_link)
            self.assertIsNone(help_headers.get('Set-Cookie'))
            self.assertNotIn(b'<script', help_raw)
            self.assertEqual('/epreuves/aide', help_page.form('/epreuves/aide')['action'])
            self.assertIn('TypeSafe', help_page.visible)
            self.request('/epreuves/aide?q=formule-privee', status=400)

            # Chaque caractéristique reste distincte sur la notice exacte
            calc_page = self.request('/epreuves/' + task + '/version-calcul')[0]
            self.assertEqual('Tableau des débits', calc_page.field('Résultat attendu'))
            self.assertEqual('Textes fournis', calc_page.field('Entrées'))
            self.assertEqual('Français', calc_page.field('Langue'))
            self.assertEqual('Tableur', calc_page.field('Outils'))
            self.assertEqual('Facile', calc_page.field('Difficulté'))
            self.assertEqual('version-calcul', calc_page.field('Version'))
            self.assertEqual('Qualification absente', calc_page.field('Qualification'))
            self.assertEqual('Aucun résultat publié', calc_page.field('Résultats publiés'))
            redac_page = self.request('/epreuves/' + task + '/version-redaction')[0]
            self.assertEqual('Aucun outil déclaré', redac_page.field('Outils'))
            self.assertEqual('Qualification réussie', redac_page.field('Qualification'))
            self.assertEqual('Aucun résultat publié', redac_page.field('Résultats publiés'))
            old_page = self.request('/epreuves/' + task + '/version-ancienne')[0]
            self.assertEqual('Inconnu', old_page.field('Résultat attendu'))
            self.assertEqual('Inconnues', old_page.field('Entrées'))
            self.assertEqual('Inconnue', old_page.field('Langue'))
            self.assertEqual('Inconnus', old_page.field('Outils'))
            self.assertEqual('Inconnue', old_page.field('Difficulté'))
            self.assertEqual('version-ancienne', old_page.field('Version'))
            self.assertEqual('Qualification absente', old_page.field('Qualification'))
            self.assertEqual('Aucun résultat publié', old_page.field('Résultats publiés'))
            enriched_page = self.request('/epreuves/' + task + '/version-enrichie')[0]
            self.assertEqual('Français', enriched_page.field('Langue'))
            self.assertEqual('Inconnu', enriched_page.field('Résultat attendu'))
            self.assertEqual('Inconnus', enriched_page.field('Outils'))
            self.assertEqual('version-enrichie', enriched_page.field('Version'))

            # Recherche seule : tous les termes, aucun classement, critères visibles et effaçables
            found = search({'q': 'textes fournis'})
            self.assertIn('Critères actifs', found.visible)
            self.assertIn('textes fournis', found.visible)
            self.assertIn('Tableau des débits', found.visible)
            self.assertNotIn('Note de synthèse', found.visible)
            self.assertNotIn('Ancienne épreuve', found.visible)
            self.assertEqual('/epreuves', found.link('Effacer la recherche et les filtres'))
            self.assertEqual(['', 'Anglais', 'Français'], found.options('langue'))
            and_terms = search({'q': 'tableau textes'})
            self.assertIn('Tableau des débits', and_terms.visible)
            self.assertNotIn('Note de synthèse', and_terms.visible)
            # Le texte des pièces n'alimente ni la recherche ni les caractéristiques
            piece_only = search({'q': 'relève'})
            self.assertIn('Aucune épreuve ne correspond à cette recherche', piece_only.visible)
            missing_terms = search({'q': 'textes synthèse'})
            self.assertIn('Aucune épreuve ne correspond à cette recherche', missing_terms.visible)
            self.assertNotIn('Aucune épreuve publiée', missing_terms.visible)
            self.assertEqual('/epreuves', missing_terms.link('Effacer la recherche et les filtres'))
            self.assertEqual('/preparation', missing_terms.link('Décrire mon cas d’usage'))

            # Filtres explicites, combinés et valeurs déclarées uniquement
            for fields, present, absent in (
                    ({'entree': 'Bibliothèque figée'}, 'Note de synthèse', 'Tableau des débits'),
                    ({'langue': 'Anglais'}, 'Note de synthèse', 'Tableau des débits'),
                    ({'outil': 'Tableur'}, 'Tableau des débits', 'Note de synthèse'),
                    ({'difficulte': 'Difficile'}, 'Note de synthèse', 'Tableau des débits'),
                    ({'qualification': 'QUALIFIED'}, 'Note de synthèse', 'Tableau des débits'),
                    ({'qualification': 'ABSENT'}, 'Tableau des débits', 'Note de synthèse')):
                filtered = search(fields)
                self.assertIn(present, filtered.visible, fields)
                self.assertNotIn(absent, filtered.visible, fields)
                self.assertIn('Critères actifs', filtered.visible, fields)
            combined_fields = {'q': 'tableau textes', 'entree': 'Textes fournis', 'langue': 'Français',
                               'outil': 'Tableur', 'difficulte': 'Facile'}
            combined = search(combined_fields)
            self.assertIn('Tableau des débits', combined.visible)
            self.assertNotIn('Note de synthèse', combined.visible)
            mismatch = search({'langue': 'Français', 'qualification': 'QUALIFIED'})
            self.assertIn('Aucune épreuve ne correspond à cette recherche', mismatch.visible)
            unknown_value = search({'langue': 'Klingon'})
            self.assertIn('Aucune épreuve ne correspond à cette recherche', unknown_value.visible)
            unavailable = next(node for node in unknown_value.choices('langue')
                               if node['attrs']['value'] == 'Klingon')
            self.assertIn('selected', unavailable['attrs'])
            self.assertIn('indisponible', unavailable['text'])

            # Ouverture exacte, critères conservés, copie toujours filiation de la version choisie
            result_link = combined.link('Tableau des débits')
            self.assertEqual('/epreuves/' + task + '/version-calcul?' + urlencode(
                [(key, combined_fields[key]) for key in editorial.SEARCH_PARAMETERS if combined_fields.get(key)]),
                result_link)
            notice = self.request(result_link)[0]
            self.assertEqual(query_path(combined_fields), notice.link('Retour aux résultats'))
            self.assertEqual('/epreuves', notice.link('Toutes les épreuves'))
            self.assertEqual('/preparation/copies/' + task + '/version-calcul', notice.link('Créer mon dossier privé'))
            self.assertEqual('Textes fournis', notice.field('Entrées'))
            source = editorial.copy_bundle(self.public, task, 'version-calcul')
            self.assertEqual(calc['package_sha256'], source['package_sha256'])
            copied = json.loads(source['manifest'])
            self.assertEqual('Tableau des débits', copied['expected_result'])
            self.assertEqual(['Tableur'], copied['tools'])

            # Paramètres et contenus hostiles : refus ou échappement, jamais d'exécution
            for path in ('/epreuves?besoin=prive', '/epreuves?q=a&q=b', '/epreuves?q=' + 'a' * 201,
                         '/epreuves?qualification=INVALIDE', '/epreuves?q=%ff', '/epreuves?q=%00',
                         '/epreuves/' + task + '/version-calcul?x=1',
                         '/epreuves/' + task + '/version-calcul?q=a&q=b'):
                rejected, _, rejected_raw = self.request(path, status=400)
                self.assertNotIn(b'<script', rejected_raw, path)
                self.assertNotIn('prive', rejected.visible, path)
            hostile_path = query_path({'q': '<script>alert(9)</script>'})
            hostile, _, hostile_raw = self.request(hostile_path)
            self.assertNotIn(b'<script', hostile_raw)
            self.assertIn('<script>alert(9)</script>', hostile.visible)

            # Retrait : absent des résultats et des valeurs proposées
            after_withdrawal, _, _ = self.request('/epreuves')
            self.assertNotIn('Épreuve retirée', after_withdrawal.visible)
            self.assertNotIn('Langue retirée', after_withdrawal.options('langue'))
            self.assertNotIn('Outil retiré', after_withdrawal.options('outil'))
            retired_search = search({'langue': 'Langue retirée'})
            self.assertIn('Aucune épreuve ne correspond à cette recherche', retired_search.visible)
            self.assertNotIn('Épreuve retirée', retired_search.visible)
            self.request('/epreuves/' + task + '/version-retiree', status=200)

            # Aucune clé, session, écriture ni appel : les octets publiés restent identiques
            self.assertEqual([], self.calls)
            self.assertTrue(self.starts.empty())
            self.assertEqual({}, dict(self.cookies))
            with closing(storage.Store(self.data)) as store:
                self.assertEqual(before_operations,
                                 store._connection.execute('SELECT count(*) FROM operations').fetchone()[0])
            self.assertEqual(old_manifest, (old_folder / 'manifest.json').read_bytes())
            self.assertEqual(old_piece, (old_folder / 'piece-consigne.txt').read_bytes())
            self.assertEqual(old['package_sha256'], sha256((old_folder / 'manifest.json').read_bytes()).hexdigest())
            self.assertEqual(enriched['package_sha256'], sha256(
                (self.public / 'editorial-trials' / task / 'version-enrichie' / 'manifest.json').read_bytes()).hexdigest())
            self.assertEqual(before_public, {str(path.relative_to(self.public)): path.read_bytes()
                                             for path in self.public.rglob('*') if path.is_file()})
            self.assertEqual(before_private, {str(path.relative_to(self.data)): path.read_bytes()
                                              for path in self.data.rglob('*') if path.is_file()})

    def test_recherche_conserve_un_filtre_indisponible(self):
        """#464 : un filtre actif reste soumis jusqu'à sa modification ou son effacement volontaire"""
        task = 'tache-fictive'
        tableur = self.publier_epreuve('version-tableur', title='Tableau au tableur',
                                       characteristics={'tools': ['Tableur'], 'language': 'Français'})
        self.publier_epreuve('version-autre', title='Note sans tableur',
                             characteristics={'tools': [], 'language': 'Anglais'})
        path = '/epreuves?' + urlencode({'outil': 'Tableur'})
        filtered, _, _ = self.request(path)
        self.assertIn('Tableau au tableur', filtered.visible)
        self.assertNotIn('Note sans tableur', filtered.visible)
        self.assertEqual('Tableur', filtered.form_values('/epreuves')['outil'])
        # Un filtre actif garde le dépliant ouvert
        self.assertIn('open', next(n for n in filtered.nodes if n['tag'] == 'details'
                                   and n['text'].lstrip().startswith('Affiner la recherche'))['attrs'])
        editorial.withdraw(self.public, task, 'version-tableur', dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=tableur['package_sha256'],
            scope='editorial-withdrawal', task_id=task, version_id='version-tableur'))
        retained, _, _ = self.request(path)
        self.assertIn('Aucune épreuve ne correspond à cette recherche', retained.visible)
        self.assertNotIn('Tableau au tableur', retained.visible)
        self.assertIn('Tableur (indisponible)', retained.visible)
        choice = next(node for node in retained.choices('outil') if node['attrs']['value'] == 'Tableur')
        self.assertIn('selected', choice['attrs'])
        self.assertIn('indisponible', choice['text'])
        fields = retained.form_values('/epreuves')
        self.assertEqual('Tableur', fields['outil'])
        self.assertNotIn('Tableur', self.request('/epreuves')[0].options('outil'))
        # Modifier le texte recherché soumet réellement le filtre conservé
        fields['q'] = 'note'
        changed, _, _ = self.request('/epreuves?' + urlencode(fields))
        self.assertIn('Critères actifs', changed.visible)
        self.assertIn('Tableur (indisponible)', changed.visible)
        self.assertIn('Aucune épreuve ne correspond à cette recherche', changed.visible)
        self.assertNotIn('Note sans tableur', changed.visible)
        self.assertEqual('Tableur', changed.form_values('/epreuves')['outil'])
        # Effacement volontaire : le filtre et la recherche disparaissent ensemble
        cleared, _, _ = self.request(changed.link('Effacer la recherche et les filtres'))
        self.assertNotIn('Critères actifs', cleared.visible)
        self.assertEqual('', cleared.form_values('/epreuves')['outil'])
        self.assertIn('Note sans tableur', cleared.visible)
        self.assertNotIn('Tableau au tableur', cleared.visible)

    def test_recherche_caracteristique_longue_utilisable(self):
        """#464 : toute valeur proposée par le formulaire reste utilisable par la recherche"""
        long_tool = 'Tableur ' + 'de contrôle ' * 16 + 'o'
        self.assertEqual(201, len(long_tool))
        self.publier_epreuve('version-longue', title='Version longue',
                             characteristics={'tools': [long_tool], 'language': 'Français'})
        page, _, _ = self.request('/epreuves')
        self.assertIn(long_tool, page.options('outil'))
        self.assertIn(long_tool, [n['text'] for n in page.nodes if n['tag'] == 'dd'])
        fields = page.form_values('/epreuves')
        self.assertEqual('', fields['outil'])
        fields['outil'] = long_tool
        result, _, _ = self.request('/epreuves?' + urlencode(fields))
        self.assertIn('Version longue', result.visible)
        self.assertIn('Critères actifs', result.visible)
        self.assertIn(long_tool, result.visible)
        self.assertIn(long_tool, result.options('outil'))
        self.assertEqual(long_tool, result.form_values('/epreuves')['outil'])
        self.request('/epreuves?q=' + 'a' * 201, status=400)

    def construire_epreuve(self, version='version-1', *, qualification='ABSENT', title='Tableau des opérations',
                           work='Préparer un tableau par opération', cases=('Cinq débits de janvier 2027',),
                           limits=('Ne certifie aucune comptabilité',),
                           pieces=None, task='tache-fictive', characteristics=None, schema=None):
        """Paquet éditorial complet, avant toute approbation"""
        pieces = pieces or {'consigne': ('Consigne', 'Relève les opérations inventées')}
        files, declared = {}, {}
        for pid, (label, body) in pieces.items():
            filename = 'piece-' + pid + '.txt'
            raw = body.encode()
            files[filename] = raw
            declared[pid] = {'file': filename, 'sha256': sha256(raw).hexdigest(), 'label': label}
        manifest = {'schema_version': schema or editorial.SCHEMA, 'task_id': task, 'version_id': version,
                    'title': title, 'work': work, 'cases': list(cases), 'limits': list(limits),
                    'qualification_status': qualification, 'results_published': False, 'pieces': declared}
        if characteristics is not None:
            manifest.update(characteristics)
        raw = storage._strict_json(manifest).encode()
        identity = sha256(raw).hexdigest()
        return {'manifest': raw, 'package_sha256': identity, 'files': files}

    def publier_epreuve(self, version='version-1', *, qualification='ABSENT', title='Tableau des opérations',
                        work='Préparer un tableau par opération', cases=('Cinq débits de janvier 2027',),
                        limits=('Ne certifie aucune comptabilité',),
                        pieces=None, task='tache-fictive', characteristics=None, schema=None):
        """Épreuve de production approuvée par l’autorité éditoriale de test"""
        bundle = self.construire_epreuve(version, qualification=qualification, title=title, work=work,
                                         cases=cases, limits=limits, pieces=pieces, task=task,
                                         characteristics=characteristics, schema=schema)
        editorial.publish(self.public, bundle, dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=bundle['package_sha256'],
            scope='editorial-trial'))
        return bundle

    def ajouter_cle(self):
        page, _, _ = self.request('/preparation')
        form = page.form('/access/key')
        self.request(form['action'], form['fields'] | {'key': KEY}, status=303)

    def copie_epreuve(self, version='version-1', *, status=303, task='tache-fictive'):
        page, _, _ = self.request('/epreuves/' + task + '/' + version)
        confirmation, _, _ = self.request(page.link('Créer mon dossier privé'))
        form = next(item for item in confirmation.forms if '/copies/' in item['action'])
        result, headers, raw = self.request(form['action'], form['fields'], status=status)
        return result, headers, raw

    def copie_preparee(self, *, qualification='ABSENT', version='version-1',
                       title='Tableau des opérations', work='Préparer un tableau par opération'):
        """Copie sans clé, puis accès personnel et première préparation du besoin personnel"""
        task = 'tache-fictive'
        bundle = self.publier_epreuve(version, qualification=qualification, title=title, work=work)
        _, headers, _ = self.copie_epreuve(version, task=task)
        dossier = headers['Location']
        self.ajouter_cle()
        page, _, _ = self.request(dossier)
        self.preparation_stage = 'exemple'
        page = self.submit(page, '/messages', {
            'message': 'Comparer des notes de réunion et produire la liste des actions à relire'})
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(dossier)
        return dossier, page, bundle

    def test_copie_epreuve_sans_cle_et_filiation(self):
        """#465 : copie exacte sans clé, filiation conservée et accès personnel avant tout appel"""
        task, version = 'tache-fictive', 'version-1'
        bundle = self.publier_epreuve(version, qualification='QUALIFIED')
        public, public_headers, public_raw = self.request('/epreuves/' + task + '/' + version)
        self.assertIsNone(public_headers.get('Set-Cookie'))
        self.assertEqual('no-referrer', public_headers['Referrer-Policy'])
        self.assertIn("form-action 'self'", public_headers['Content-Security-Policy'])
        self.assertIn('Créer mon dossier privé', public.visible)
        self.assertIn('Qualification réussie', public.visible)
        link = public.link('Créer mon dossier privé')
        self.assertEqual('/preparation/copies/' + task + '/' + version, link)
        # Le cookie privé n’est pas envoyé à la page publique, mais l’est au chemin de confirmation
        self.assertNotIn('benchmark_session', self.cookie_header(self.cookies, '/epreuves/' + task + '/' + version))
        confirmation, confirm_headers, _ = self.request(link)
        self.assertTrue(confirm_headers.get('Set-Cookie'))
        self.assertIn('benchmark_session', self.cookie_header(self.cookies, link))
        session = self.cookies['benchmark_session'].value
        self.assertIn('Épreuve source', confirmation.visible)
        form = next(item for item in confirmation.forms if '/copies/' in item['action'])
        self.assertIn('csrf_token', form['fields'])
        self.assertEqual('1', form['fields']['copy'])
        self.assertEqual([], self.calls)
        self.assertTrue(self.starts.empty())
        with closing(storage.Store(self.data)) as store:
            before = store._connection.execute('SELECT count(*) FROM operations').fetchone()[0]
        page, headers, raw = self.request(form['action'], form['fields'], status=303)
        self.assertEqual(session, self.cookies['benchmark_session'].value)
        self.assertEqual('no-store', headers['Cache-Control'])
        dossier = headers['Location']
        self.assertRegex(dossier, r'^/preparation/dossiers/[A-Za-z0-9_-]{1,128}$')
        # Aucun appel, coût, campagne ni qualification importée
        self.assertEqual([], self.calls)
        self.assertTrue(self.starts.empty())
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(before, store._connection.execute('SELECT count(*) FROM operations').fetchone()[0])
            dossier_id = dossier.rsplit('/', 1)[1]
            source = json.loads(store._connection.execute(
                'SELECT checks_json FROM s2_revisions WHERE dossier_id=? AND revision=1',
                (dossier_id,)).fetchone()[0])['source']
            self.assertEqual(bundle['package_sha256'], sha256(source['manifest'].encode()).hexdigest())
            self.assertEqual(sha256('Relève les opérations inventées'.encode()).hexdigest(),
                             json.loads(source['manifest'])['pieces']['consigne']['sha256'])
            self.assertEqual('Relève les opérations inventées', source['pieces'][0]['text'])
            self.assertEqual(0, store._connection.execute('SELECT count(*) FROM s2_validations').fetchone()[0])
        page, _, _ = self.request(dossier)
        for value in ('Épreuve source', 'Tableau des opérations', 'Préparer un tableau par opération',
                      'Version source', version, 'Qualification réussie', 'Cinq débits de janvier 2027',
                      'Ne certifie aucune comptabilité', 'Votre besoin personnel n’est pas encore décrit',
                      'La copie reste consultable sans clé'):
            self.assertIn(value, page.visible)
        # Avant préparation, la page dit son état de copie et non un exemple déjà préparé
        self.assertIn('Copie d’épreuve, version 1', page.visible)
        self.assertNotIn('Exemple inventé', page.visible)
        self.assertIn('Votre copie est enregistrée.', page.visible)
        self.assertIn('sans clé ni appel aux modèles', page.visible)
        self.assertIn('servira à préparer votre exemple', page.visible)
        self.assertNotIn('Ajoutez votre clé OpenRouter pour préparer un exemple.', page.visible)
        self.assertTrue(any('Relève les opérations inventées' in n['text'] for n in page.nodes
                            if n['tag'] == 'div' and 'example-text' in n['attrs'].get('class', '')))
        self.assertNotIn('Choisir les modèles', page.visible)
        self.assertNotIn('Exemple vérifié, prêt à comparer', page.visible)
        need = page.form('/messages')
        self.assertEqual(['message'], [n['attrs'].get('name') for n in need['nodes'] if n['tag'] == 'textarea'])
        self.assertIn('Ajoutez votre clé OpenRouter', page.visible)
        # Zéro opération : la page nomme l’absence d’appel au lieu de suggérer une dépense non rapprochée
        self.assertIn('Aucun appel modèle effectué pour ce dossier.', page.visible)
        self.assertNotIn('justificatif', page.visible)
        self.assertNotIn('Coût observé', page.visible)
        # La copie reste refusée sans clé : aucun appel ni dossier partiel
        self.request(dossier + '/messages', need['fields'] | {'message': 'Un besoin assez long pour être préparé'},
                     status=403)
        self.assertEqual([], self.calls)
        # Rechargement sans clé et sans besoin envoyé : même état, aucun appel fictif déclenché
        reloaded, _, _ = self.request(dossier)
        self.assertIn('Aucun appel modèle effectué pour ce dossier.', reloaded.visible)
        self.assertNotIn('justificatif', reloaded.visible)
        # Le rechargement garde la source, la qualification publiée et les pièces copiées
        self.assertIn('Épreuve source', reloaded.visible)
        self.assertIn('Copie d’épreuve, version 1', reloaded.visible)
        self.assertIn('Qualification réussie', reloaded.visible)
        self.assertTrue(any('Relève les opérations inventées' in n['text'] for n in reloaded.nodes
                            if n['tag'] == 'div' and 'example-text' in n['attrs'].get('class', '')))
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(0, store._connection.execute(
                'SELECT count(*) FROM operations WHERE dossier_id=?', (dossier_id,)).fetchone()[0])
        self.assertEqual([], self.calls)
        self.assertTrue(self.starts.empty())
        # Renvoi de la même demande : même dossier, aucune révision ni opération de plus
        again, again_headers, _ = self.request(form['action'], form['fields'], status=303)
        self.assertEqual(dossier, again_headers['Location'])
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(1, store._connection.execute('SELECT count(*) FROM s2_dossiers').fetchone()[0])
            self.assertEqual(1, store._connection.execute('SELECT count(*) FROM s2_revisions').fetchone()[0])
            self.assertEqual(0, store._connection.execute('SELECT count(*) FROM s2_actions').fetchone()[0])
        # Isolation : une autre session ne voit ni le dossier ni la source
        _, foreign_headers, _ = self.request('/preparation', cookies=SimpleCookie())
        foreign = SimpleCookie(foreign_headers['Set-Cookie'])
        denied, _, denied_raw = self.request(dossier, cookies=foreign, status=403)
        self.assertNotIn(b'Tableau des op', denied_raw)
        self.assertIn('Rouvrez votre cas d’usage', denied.visible)
        # La page publique n’a pas de session et reste identique pour un cookie invalide
        self.assertEqual(public_raw, self.request('/epreuves/' + task + '/' + version,
                                                  cookies=SimpleCookie('benchmark_session=invalid'))[2])
        self.assertEqual([], self.calls)
        self.assertTrue(self.starts.empty())

    def test_copie_conserve_la_session_existante(self):
        """#465 : la copie depuis le chemin privé ne remplace pas la session qui portait les anciens dossiers"""
        old_dossier, old_page = self.exemple()
        self.assertIn('Transformer des notes de réunion', old_page.visible)
        session = self.cookies['benchmark_session'].value
        calls = list(self.calls)
        task, version = 'tache-fictive', 'version-1'
        self.publier_epreuve(version)
        public, _, _ = self.request('/epreuves/' + task + '/' + version)
        self.assertNotIn('benchmark_session', self.cookie_header(
            self.cookies, '/epreuves/' + task + '/' + version))
        link = public.link('Créer mon dossier privé')
        self.assertIn('benchmark_session', self.cookie_header(self.cookies, link))
        confirmation, _, _ = self.request(link)
        self.assertEqual(session, self.cookies['benchmark_session'].value)
        self.assertIn('Épreuve source', confirmation.visible)
        form = next(item for item in confirmation.forms if '/copies/' in item['action'])
        _, headers, _ = self.request(form['action'], form['fields'], status=303)
        dossier = headers['Location']
        self.assertEqual(session, self.cookies['benchmark_session'].value)
        self.assertEqual(calls, self.calls)
        self.assertTrue(self.starts.empty())
        # L’ancien dossier reste accessible avec la même session
        previous, _, _ = self.request(old_dossier)
        self.assertIn('Transformer des notes de réunion', previous.visible)
        # Renvoi de la confirmation : même dossier, aucune copie de plus
        again, again_headers, _ = self.request(form['action'], form['fields'], status=303)
        self.assertEqual(dossier, again_headers['Location'])
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(2, store._connection.execute('SELECT count(*) FROM s2_dossiers').fetchone()[0])
        # Une autre session ne voit ni l’ancien dossier ni la copie
        _, foreign_headers, _ = self.request('/preparation', cookies=SimpleCookie())
        foreign = SimpleCookie(foreign_headers['Set-Cookie'])
        for private in (old_dossier, dossier):
            denied, _, denied_raw = self.request(private, cookies=foreign, status=403)
            self.assertNotIn(b'Transformer des notes', denied_raw)
            self.assertNotIn(b'Tableau des op', denied_raw)
        self.assertEqual(session, self.cookies['benchmark_session'].value)
        self.request(old_dossier)
        self.request(dossier)

    def test_copie_session_privacy_bootstrap(self):
        """#465 : sous confidentialité S7, la confirmation ouvre la session par le mécanisme existant"""
        from benchmark import privacy
        task, version = 'tache-fictive', 'version-1'
        self.publier_epreuve(version)
        privacy.migrate(self.data, SECRET, 'test-465-bootstrap')
        public, _, _ = self.request('/epreuves/' + task + '/' + version)
        link = public.link('Créer mon dossier privé')
        bootstrap, _, _ = self.request(link)
        self.assertIn('Ouvrir mon espace', bootstrap.visible)
        form = bootstrap.form('/session/open')
        self.assertEqual(link, form['fields']['return_path'])
        _, headers, _ = self.request(form['action'], form['fields'], status=303, extra_headers={
            'Origin': 'https://fixture.example', 'Sec-Fetch-Site': 'same-origin'})
        self.assertEqual(link, headers['Location'])
        confirmation, _, _ = self.request(link)
        self.assertIn('Épreuve source', confirmation.visible)
        form = next(item for item in confirmation.forms if '/copies/' in item['action'])
        _, headers, _ = self.request(form['action'], form['fields'], status=303)
        dossier = headers['Location']
        page, _, _ = self.request(dossier)
        self.assertIn('Épreuve source', page.visible)
        self.assertEqual([], self.calls)
        self.assertTrue(self.starts.empty())

    def test_copie_epreuve_refus_invalides_et_doublon(self):
        """#465 : source retirée, altérée, incomplète ou substituée ; aucune copie partielle"""
        task, version = 'tache-fictive', 'version-1'
        bundle = self.publier_epreuve(version)
        _, headers, _ = self.copie_epreuve(version)
        dossier = headers['Location']
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(1, store._connection.execute('SELECT count(*) FROM s2_dossiers').fetchone()[0])
        # Session et jeton d’une version encore disponible, sans créer sa copie
        self.publier_epreuve('version-6')
        confirmation, _, _ = self.request('/preparation/copies/' + task + '/version-6')
        form = next(item for item in confirmation.forms if '/copies/' in item['action'])
        csrf = form['fields']['csrf_token']
        # Anciennes routes de copie directe : aucune ne relaie une source fournie par le client
        self.request('/epreuves/' + task + '/' + version + '/copie', {'copy': '1'}, status=404)
        source = editorial.copy_bundle(self.public, task, version)
        for extra in ({}, {'csrf_token': csrf}):
            self.request('/preparation/copies', {
                'dossier_id': 'a' * 32, 'action_id': 'b' * 32, 'source': source} | extra, status=404)
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(1, store._connection.execute('SELECT count(*) FROM s2_dossiers').fetchone()[0])
        # Contrôle commun : jeton absent ou invalide, origine étrangère
        copy_path = '/preparation/copies/' + task + '/version-6'
        self.request(copy_path, {'copy': '1'}, status=400)
        self.request(copy_path, {'csrf_token': 'a' * 64, 'copy': '1'}, status=403)
        for fields in ({'csrf_token': csrf, 'copy': '1', 'task_id': 'autre'},
                       {'csrf_token': csrf, 'copy': '1', 'source': source},
                       {'csrf_token': csrf, 'copy': '0'}):
            self.request(copy_path, fields, status=400)
        self.request(copy_path + '?x=1', form['fields'], status=400)
        self.request(copy_path, form['fields'], status=403, extra_headers={'Origin': 'https://evil.example'})
        self.request(copy_path, form['fields'], status=403,
                     extra_headers={'Sec-Fetch-Site': 'cross-site'})
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(1, store._connection.execute('SELECT count(*) FROM s2_dossiers').fetchone()[0])
        # Version non publiée et autorité de fixture : introuvables pour le lecteur de production
        self.request('/preparation/copies/' + task + '/absente', form['fields'], status=404)
        raw_piece = b'Version de fixture'
        manifest = {'schema_version': editorial.SCHEMA, 'task_id': task, 'version_id': 'fixture-1',
                    'title': 'Fixture', 'work': 'Fixture', 'cases': ['Fixture'], 'limits': ['Fixture'],
                    'qualification_status': 'ABSENT', 'results_published': False,
                    'pieces': {'consigne': {'file': 'piece-consigne.txt',
                                            'sha256': sha256(raw_piece).hexdigest(), 'label': 'Consigne'}}}
        raw = storage._strict_json(manifest).encode()
        fixture_approval = editorial.fixture_approval(sha256(raw).hexdigest())
        fixture = {'manifest': raw, 'package_sha256': sha256(raw).hexdigest(),
                   'files': {'piece-consigne.txt': raw_piece}}
        editorial.fixture_publish(self.public, fixture, fixture_approval)
        self.request('/preparation/copies/' + task + '/fixture-1', form['fields'], status=404)
        # Retrait avant admission : refus explicite, aucune copie
        self.publier_epreuve('version-2')
        editorial.withdraw(self.public, task, 'version-2', dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=editorial.reprise_admission(
                self.public, task, 'version-2')['package_sha256'],
            scope='editorial-withdrawal', task_id=task, version_id='version-2'))
        refused, _, refused_raw = self.request(
            '/preparation/copies/' + task + '/version-2', form['fields'], status=409)
        self.assertIn('Cette version est retirée', refused.visible)
        self.assertNotIn('Relève les op'.encode(), refused_raw)
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(1, store._connection.execute('SELECT count(*) FROM s2_dossiers').fetchone()[0])
        # Paquet altéré, incomplet ou d’une autre autorité : refus avant écriture
        for other, mutate in (('version-3', 'bytes'), ('version-4', 'missing'), ('version-5', 'authority')):
            self.publier_epreuve(other)
            folder = self.public / 'editorial-trials' / task / other
            piece = folder / 'piece-consigne.txt'
            if mutate == 'bytes':
                piece.write_bytes(b'Autres octets')
            elif mutate == 'missing':
                piece.unlink()
            else:
                approval = json.loads((folder / 'approval.json').read_text())
                approval['actor'] = 'approbateur-fictif-S6'
                (folder / 'approval.json').write_text(storage._strict_json(approval))
            refused, _, _ = self.request('/preparation/copies/' + task + '/' + other,
                                         form['fields'], status=409)
            self.assertIn('Aucun dossier n’a été créé', refused.visible)
            with closing(storage.Store(self.data)) as store:
                self.assertEqual(1, store._connection.execute('SELECT count(*) FROM s2_dossiers').fetchone()[0])
        # Parcours autorisé : même session, même jeton, aucun appel ni coût
        _, success_headers, _ = self.request(copy_path, form['fields'], status=303)
        copied = success_headers['Location']
        self.assertEqual([], self.calls)
        self.assertTrue(self.starts.empty())
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(2, store._connection.execute('SELECT count(*) FROM s2_dossiers').fetchone()[0])
            self.assertEqual(0, store._connection.execute('SELECT count(*) FROM operations').fetchone()[0])
        again, again_headers, _ = self.request(copy_path, form['fields'], status=303)
        self.assertEqual(copied, again_headers['Location'])
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(2, store._connection.execute('SELECT count(*) FROM s2_dossiers').fetchone()[0])
        # Retrait de la première version : sa copie antérieure reste consultable
        editorial.withdraw(self.public, task, version, dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=bundle['package_sha256'],
            scope='editorial-withdrawal', task_id=task, version_id=version))
        self.request('/preparation/copies', {
            'csrf_token': csrf, 'dossier_id': 'c' * 32, 'action_id': 'd' * 32, 'source': source}, status=404)
        self.request('/preparation/copies/' + task + '/' + version, form['fields'], status=409)
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(2, store._connection.execute('SELECT count(*) FROM s2_dossiers').fetchone()[0])
        private, _, private_raw = self.request(dossier)
        self.assertIn('Épreuve source', private.visible)
        self.assertIn('n’est plus disponible au catalogue', private.visible)
        self.assertTrue(any('Relève les opérations inventées' in n['text'] for n in private.nodes
                            if n['tag'] == 'div' and 'example-text' in n['attrs'].get('class', '')))
        self.assertNotIn(b'<script>alert', private_raw)
        self.assertNotIn(b'<script>piece', private_raw)
        # Le retrait et la copie se linéarisent sous verrou : une copie commencée avant le retrait aboutit
        import fcntl
        self.publier_epreuve('version-7')
        folder = self.public / 'editorial-trials' / task / 'version-7'
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        fcntl.flock(fd, fcntl.LOCK_EX)
        result = []
        thread = threading.Thread(target=lambda: result.append(editorial.copy_bundle(self.public, task, 'version-7')))
        thread.start()
        thread.join(.2)
        self.assertTrue(thread.is_alive(), 'La copie a ignoré le verrou de retrait')
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual('version-7', result[0]['version_id'])
        editorial.withdraw(self.public, task, 'version-7', dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=result[0]['package_sha256'],
            scope='editorial-withdrawal', task_id=task, version_id='version-7'))
        with self.assertRaises(ValueError) as raised:
            editorial.copy_bundle(self.public, task, 'version-7')
        self.assertEqual('Nouvelle reprise refusée', str(raised.exception))

    def test_copie_epreuve_qualification_refusee_bloque_le_lancement(self):
        """#465 : la qualification publiée ne dispense pas la copie ; un refus bloque le lancement"""
        dossier, page, bundle = self.copie_preparee(qualification='QUALIFIED')
        self.assertIn('Qualification réussie', page.visible)
        self.assertNotIn('Choisir les modèles', page.visible)
        constat = {'kind': 'decidability', 'severity': 'blocking',
                   'text': 'La référence ne permet pas de vérifier l’obligation annoncée.'}
        self.qualifier.result = {'qualified': False, 'findings': [constat],
                                 'summary': 'La référence de la copie ne permet pas cette vérification.'}
        self.submit(page, '/validation', {})
        prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
        page, _, _ = self.request(dossier)
        self.assertIn('Exemple à revoir avant comparaison', page.visible)
        self.assertIn('La référence de la copie ne permet pas cette vérification.', page.visible)
        self.assertTrue(any(constat['text'] in n['text'] for n in page.nodes if n['tag'] == 'details'))
        self.assertFalse(any(n['tag'] == 'a' and n['text'] == 'Choisir les modèles' for n in page.nodes))
        _, _, raw = self.request(dossier + '/configurations', status=403)
        self.assertIn('Terminez l’étape précédente avant de poursuivre.', raw.decode())
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(0, store._connection.execute("SELECT count(*) FROM s4_campaigns").fetchone()[0])
        self.assertEqual([call for call in self.calls if call[0] == 'candidat'], [])
        self.assertTrue(self.starts.empty())

    def copie_qualifiee(self, version='version-1', title='Tableau des opérations',
                        work='Préparer un tableau par opération'):
        """#465 : copie publiée, besoin personnel, précision, correction et qualification propre"""
        dossier, page, bundle = self.copie_preparee(qualification='QUALIFIED', version=version,
                                                    title=title, work=work)
        self.assertNotIn('Choisir les modèles', page.visible)
        # Une précision puis une correction conservent le besoin personnel et les accords non touchés
        page = self.submit(page, '/messages', {'kind': 'clarify',
            'message': 'Conserver les décisions et les actions de chaque note.'})
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(dossier)
        page = self.submit(page, '/messages', {'kind': 'correct',
            'message': 'Présenter aussi le responsable de chaque action.'})
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        with closing(storage.Store(self.data)) as store:
            dossier_id = dossier.rsplit('/', 1)[1]
            current = store._connection.execute(
                'SELECT current_revision FROM s2_dossiers WHERE dossier_id=?', (dossier_id,)).fetchone()[0]
            payload = store.get_dossier(dossier_id, current)
            self.assertEqual('Comparer des notes de réunion et produire la liste des actions à relire',
                             payload['request'])
            self.assertIn('Conserver les décisions et les actions de chaque note.', payload['clarifications'])
            self.assertTrue(payload['validated_assumptions'])
            source = json.loads(store._connection.execute(
                'SELECT checks_json FROM s2_revisions WHERE dossier_id=? AND revision=1',
                (dossier_id,)).fetchone()[0])['source']
            self.assertEqual(bundle['package_sha256'], source['package_sha256'])
        page, _, _ = self.request(dossier)
        self.submit(page, '/validation', {})
        prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
        self.assertEqual(1, len(self.qualifier.calls))
        return dossier

    def test_copie_epreuve_jusqu_aux_resultats_prives(self):
        """#465 : copie, besoin personnel, qualification propre, lancement et résultats privés"""
        dossier, html = self.comparer(['Clarté du tableau'], lambda critere, sortie: 'excellent',
                                      qualifier=self.copie_qualifiee)
        resultats = Page(html.encode())
        self.assertIn('Modèle A', resultats.visible)
        self.assertIn('Modèle C', resultats.visible)
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        page, _, _ = self.request(dossier)
        self.assertIn('Épreuve source', page.visible)
        self.assertIn('Qualification réussie', page.visible)
        # Un dossier préparé garde sa marque d’exemple inventé, pas celle d’une simple copie
        self.assertRegex(page.visible, r'Exemple inventé, version \d')
        self.assertNotIn('Exemple inventé ·', page.visible)
        self.assertNotIn('Copie d’épreuve', page.visible)
        # Le coût observé d’une opération reçue reste affiché tel quel, jamais remplacé par un zéro
        self.assertIn('Coût observé de cette préparation : 0,10 USD', page.visible)
        self.assertNotIn('Aucun appel modèle effectué pour ce dossier.', page.visible)
        self.assertTrue(any('Relève les opérations inventées' in n['text'] for n in page.nodes
                            if n['tag'] == 'div' and 'example-text' in n['attrs'].get('class', '')))
        # La comparaison appartient au nouveau dossier, pas à la source
        with closing(storage.Store(self.data)) as store:
            campaign_id = comparison.rsplit('/', 1)[1]
            snapshot = campaigns.inspect(store, campaign_id)
            self.assertEqual(dossier.rsplit('/', 1)[1], snapshot['task']['dossier_id'])
        # Signalement attribué à la version source : visible dans la copie, pas dans l’autre version
        identity = self.publier_epreuve('version-2')['package_sha256']
        editorial.report(self.public, 'tache-fictive', 'version-2', dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', scope='editorial-report',
            package_sha256=identity, task_id='tache-fictive', version_id='version-2',
            summary='Erreur propre à la version 2.', reported_at='2026-10-23T10:00:00+00:00'))
        page, _, _ = self.request(dossier)
        self.assertNotIn('Erreur propre à la version 2.', page.visible)
        # Le signalement vise l’identité de la source déjà copiée
        with closing(storage.Store(self.data)) as store:
            source = json.loads(store._connection.execute(
                'SELECT checks_json FROM s2_revisions WHERE dossier_id=? AND revision=1',
                (dossier.rsplit('/', 1)[1],)).fetchone()[0])['source']
        stored = self.public / 'editorial-trials' / 'tache-fictive' / 'version-1'
        before = {path.name: path.read_bytes() for path in stored.iterdir() if path.name != 'report.json'}
        editorial.report(self.public, 'tache-fictive', 'version-1', dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', scope='editorial-report',
            package_sha256=source['package_sha256'], task_id='tache-fictive', version_id='version-1',
            summary='Erreur connue de la version copiée.', reported_at='2026-10-23T10:01:00+00:00'))
        self.assertEqual(before, {path.name: path.read_bytes() for path in stored.iterdir()
                                  if path.name != 'report.json'})
        page, _, _ = self.request(dossier)
        self.assertIn('Erreur connue de la version copiée.', page.visible)
        self.assertNotIn('Erreur propre à la version 2.', page.visible)
        editorial.report(self.public, 'tache-fictive', 'version-1', dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', scope='editorial-report',
            package_sha256=source['package_sha256'], task_id='tache-fictive', version_id='version-1',
            summary='Erreur connue de la version copiée.', reported_at='2026-10-23T10:01:00+00:00'))
        with self.assertRaises(ValueError) as raised:
            editorial.report(self.public, 'tache-fictive', 'version-1', dict(
                actor='Ayo', authority_id='EDITORIAL_TRIAL', scope='editorial-report',
                package_sha256=source['package_sha256'], task_id='tache-fictive', version_id='version-1',
                summary='Autre signalement.', reported_at='2026-10-23T10:02:00+00:00'))
        self.assertEqual('Signalement déjà conservé', str(raised.exception))
        # Retrait après copie : avertissement, pièces conservées, résultats antérieurs toujours lisibles
        editorial.withdraw(self.public, 'tache-fictive', 'version-1', dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', scope='editorial-withdrawal',
            package_sha256=source['package_sha256'], task_id='tache-fictive', version_id='version-1'))
        page, _, _ = self.request(dossier)
        self.assertIn('n’est plus disponible au catalogue', page.visible)
        self.assertIn('Erreur connue de la version copiée.', page.visible)
        self.assertTrue(any('Relève les opérations inventées' in n['text'] for n in page.nodes
                            if n['tag'] == 'div' and 'example-text' in n['attrs'].get('class', '')))
        self.request(comparison, status=200)

    def test_copie_epreuve_cout_inconnu_reste_inconnu(self):
        """#315 : une opération reçue au coût inconnu garde cette inconnue, sans zéro ni absence d’appel"""
        def cout_inconnu(operation, request):
            value = self.prepare(operation, request)
            value['cost'].update(status='UNKNOWN', amount=None, currency='USD', source='Reçu simulé sans coût')
            return value
        self.assistants['transport'] = SessionAssistant(cout_inconnu, {'model': 'factice'})
        dossier, page, _ = self.copie_preparee()
        self.assertIn('Coût observé de cette préparation : inconnu. Source : Reçu simulé sans coût.', page.visible)
        self.assertNotIn('Coût observé de cette préparation : 0', page.visible)
        self.assertNotIn('Aucun appel modèle effectué pour ce dossier.', page.visible)
        reloaded, _, _ = self.request(dossier)
        self.assertIn('Coût observé de cette préparation : inconnu.', reloaded.visible)
        self.assertNotIn('Coût observé de cette préparation : 0', reloaded.visible)
        with closing(storage.Store(self.data)) as store:
            operations = store.inspect_operations()
            self.assertEqual(['UNKNOWN'], [operation['observed_cost']['status'] for operation in operations])
            self.assertEqual([None], [operation['observed_cost']['amount'] for operation in operations])

    def test_resultats_publics_epreuve(self):
        """#466 : restitution approuvée, lecture publique autonome, preuves et retraits"""
        from benchmark import automatic_judgment as auto, restitution
        task, version = 'tache-fictive', 'version-1'
        title = 'Tableau <script>alert(1)</script>'
        work = 'Préparer un tableau par opération'

        def comportement(value, model):
            observed = value['receipt']['observed_configuration']
            if model == 'openai/gpt-5.6-sol':
                observed['provider'] = 'fournisseur-observe'
                observed['sources'] = dict(observed.get('sources') or {},
                                           provider='HTTP response JSON /openrouter_metadata')
            if model == 'mistralai/mistral-small-2603':
                value['cost'].update(status='UNKNOWN', amount=None)

        def ajuste(vue, findings):
            # Un modèle échoue : sa ligne et son coût inconnu restent publics, sans sélection favorable
            if vue['output']['content'] == self.SORTIE_C:
                return [dict(finding, status='FAIL', attribution='candidate',
                             finding='Une action annoncée par la consigne manque au tableau.') for finding in findings]
            return findings

        dossier, _ = self.comparer(['Clarté du tableau'], lambda critere, sortie: 'excellent',
                                   ajuste=ajuste, comportement=comportement,
                                   qualifier=lambda: self.copie_qualifiee(title=title, work=work))
        trial = editorial.reprise_admission(self.public, task, version)
        dossier_id = dossier.rsplit('/', 1)[1]
        first_id, second_id, partial_id = dossier_id + '-c1', dossier_id + '-c2', dossier_id + '-c3'
        first = '/epreuves/' + task + '/' + version + '/campagnes/' + first_id
        second = '/epreuves/' + task + '/' + version + '/campagnes/' + second_id
        partial = '/epreuves/' + task + '/' + version + '/campagnes/' + partial_id
        # Deuxième campagne de la même épreuve, aux deux mêmes modèles et coûts connus
        page, _, _ = self.request(dossier + '/configurations')
        recap = self.submit(page, '/configurations', {
            'models': ['openai/gpt-5.6-sol', 'mistralai/mistral-small-2603'], 'tier': 'low'})
        self.submit(recap, '/start', {})
        execution.execute_launch(
            self.data, self.starts.get_nowait()['candidate_attempts'],
            transport_factory=lambda channel, key: self.candidate, access_secret=SECRET,
            access_transport=self.access)
        page, _, _ = self.request(dossier)
        page, _, _ = self.request(page.link('Lancer l’évaluation'))
        self.submit(page, '/evaluate', {})
        auto.execute_campaign(self.data, self.starts.get_nowait()['judgment_operations'], self.bound[2])
        # Troisième campagne partielle : deux réponses vides, coût connu et coût inconnu
        page, _, _ = self.request(dossier + '/configurations')
        recap = self.submit(page, '/configurations', {
            'models': ['openai/gpt-5.6-sol', 'mistralai/mistral-small-2603',
                       'deepseek/deepseek-v4.1-flash'], 'tier': 'low'})

        def partiel(operation, request):
            value = self.candidate(operation, request)
            model = request['requested_configuration']['model']
            if model in ('deepseek/deepseek-v4.1-flash', 'mistralai/mistral-small-2603'):
                value['receipt']['result']['output'] = ''
            if model == 'mistralai/mistral-small-2603':
                value['cost'].update(status='KNOWN', amount='0.10')
            elif model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='UNKNOWN', amount=None)
            return value

        self.submit(recap, '/start', {})
        execution.execute_launch(
            self.data, self.starts.get_nowait()['candidate_attempts'],
            transport_factory=lambda channel, key: partiel, access_secret=SECRET,
            access_transport=self.access)
        page, _, _ = self.request(dossier)
        page, _, _ = self.request(page.link('Lancer l’évaluation'))
        self.submit(page, '/evaluate', {})
        auto.execute_campaign(self.data, self.starts.get_nowait()['judgment_operations'], self.bound[2])
        # Quatrième campagne seulement préparée : jamais publiée
        page, _, _ = self.request(dossier + '/configurations')
        self.submit(page, '/configurations', {
            'models': ['deepseek/deepseek-v4.1-flash', 'mistralai/mistral-small-2603'], 'tier': 'low'})
        third_id = dossier_id + '-c4'
        third = '/epreuves/' + task + '/' + version + '/campagnes/' + third_id
        self.assertTrue(self.starts.empty())

        with closing(storage.Store(self.data)) as store:
            session = store._connection.execute(
                'SELECT session_id FROM s2_dossiers WHERE dossier_id=?', (dossier_id,)).fetchone()[0]
            revision = store._connection.execute(
                'SELECT current_revision FROM s2_dossiers WHERE dossier_id=?', (dossier_id,)).fetchone()[0]
            private = store.get_dossier(dossier_id, revision)
            need = private['request']
            reformulation = private['reformulation'] if isinstance(private.get('reformulation'), str) else \
                json.dumps(private.get('reformulation'), ensure_ascii=False, sort_keys=True)
            comparison = restitution.comparison(store, session, dossier_id, first_id)
            second_comparison = restitution.comparison(store, session, dossier_id, second_id)
            partial_comparison = restitution.comparison(store, session, dossier_id, partial_id)
            outputs = sorted({row['output_piece_id'] for row in comparison['rows'] if row['output_piece_id']})
            second_outputs = sorted({row['output_piece_id'] for row in second_comparison['rows']
                                     if row['output_piece_id']})
            partial_outputs = sorted({row['output_piece_id'] for row in partial_comparison['rows']
                                      if row['output_piece_id']})
            self.assertEqual(1, len(partial_comparison['rows']))
            self.assertEqual(2, len(partial_comparison['pending_attempts']))
            judge = sorted({link['piece_id'] for row in comparison['rows'] for link in row['proof_links']
                            if store.get_piece(link['piece_id'])['role'] == 'judge'
                            and link['piece_id'] not in outputs})
            self.assertTrue(outputs)
            self.assertTrue(judge)
            selected, unselected = outputs[:1], outputs[1:]
            selected_text = store.read_piece(selected[0])
            judge_text = store.read_piece(judge[0]).decode()
        with closing(storage.Store(self.data)) as store:
            one = restitution.results_bundle(store, dossier_id, first_id, piece_ids=selected,
                                             task_id=task, version_id=version)
            two = restitution.results_bundle(store, dossier_id, second_id, piece_ids=second_outputs,
                                             task_id=task, version_id=version)
            three = restitution.results_bundle(store, dossier_id, partial_id, piece_ids=partial_outputs,
                                               task_id=task, version_id=version)
            with self.assertRaises(ValueError) as raised:
                restitution.results_bundle(store, dossier_id, first_id, piece_ids=judge[:1],
                                           task_id=task, version_id=version)
            self.assertEqual('Pièce non liée à la restitution ou réservée à l’évaluation', str(raised.exception))
            with self.assertRaises(ValueError) as raised:
                restitution.results_bundle(store, dossier_id, first_id, piece_ids=selected,
                                           task_id=task, version_id='version-2')
            self.assertEqual('Rattachement éditorial divergent', str(raised.exception))
            with self.assertRaises(prep.Denied) as raised:
                restitution.results_bundle(store, dossier_id, dossier_id + '-c99', piece_ids=selected,
                                           task_id=task, version_id=version)
            self.assertEqual('Campagne inaccessible', str(raised.exception))

        def refused(bundle, approved, message, operation=public_results.publish):
            with self.assertRaises(ValueError) as raised:
                operation(self.public, bundle, approved)
            self.assertEqual(message, str(raised.exception))

        # Autorités distinctes, paquets incomplets ou altérés : rien n’est activé
        self.assertFalse((self.public / 'published-results').exists())
        refused(two, None, 'Autorité absente')
        refused(two, {}, 'Autorité absente')
        refused(two, dict(actor='approbateur-fictif-S6', authority_id='TEST_ONLY_PUBLICATION_S6',
                          projection_sha256=two['projection_sha256'], catalogue=False),
                'Autorité de publication refusée')
        refused(two, dict(actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=two['projection_sha256'],
                          scope='editorial-trial'), 'Autorité de publication refusée')
        refused(two, public_results.fixture_approval(two['projection_sha256']), 'Autorité de publication refusée')
        refused(two, dict(public_results.approval(two['projection_sha256']), package_sha256='f' * 64),
                'Approbation visant d’autres octets')
        refused({'manifest': two['manifest'] + b' ', 'files': two['files'],
                 'projection_sha256': two['projection_sha256']}, public_results.approval(two['projection_sha256']),
                'Manifeste divergent')
        refused({'manifest': two['manifest'], 'files': {}, 'projection_sha256': two['projection_sha256']},
                public_results.approval(two['projection_sha256']), 'Paquet incomplet')
        altered_files = dict(two['files'])
        altered_files[sorted(altered_files)[0]] = b'octets alteres'
        refused({'manifest': two['manifest'], 'files': altered_files, 'projection_sha256': two['projection_sha256']},
                public_results.approval(two['projection_sha256']), 'Octets divergents')
        extra = dict(two['files'], **{'piece-etrangere.txt': b'piece etrangere'})
        refused({'manifest': two['manifest'], 'files': extra, 'projection_sha256': two['projection_sha256']},
                public_results.approval(two['projection_sha256']), 'Sélection publique fermée')
        changed = json.loads(two['manifest'])
        changed['task']['package_sha256'] = 'f' * 64
        raw = storage._strict_json(changed).encode()
        rebased = {'manifest': raw, 'files': two['files'], 'projection_sha256': sha256(raw).hexdigest()}
        refused(rebased, public_results.approval(rebased['projection_sha256']), 'Rattachement divergent')
        self.assertFalse((self.public / 'published-results').exists())

        self.assertEqual(selected, list(json.loads(one['manifest'])['pieces']))
        self.assertEqual(second_outputs, list(json.loads(two['manifest'])['pieces']))
        # Publication autorisée : trois restitutions distinctes de la même épreuve
        public_results.publish(self.public, one, public_results.approval(one['projection_sha256']))
        public_results.publish(self.public, two, public_results.approval(two['projection_sha256']))
        public_results.publish(self.public, three, public_results.approval(three['projection_sha256']))
        public_results.publish(self.public, one, public_results.approval(one['projection_sha256']))
        divergent = json.loads(one['manifest'])
        divergent['work'] = 'Autre travail'
        raw = storage._strict_json(divergent).encode()
        with self.assertRaises(ValueError) as raised:
            public_results.publish(self.public, {'manifest': raw, 'files': one['files'],
                                                'projection_sha256': sha256(raw).hexdigest()},
                                   public_results.approval(sha256(raw).hexdigest()))
        self.assertEqual('Restitution existante divergente', str(raised.exception))
        trial_files = {path.relative_to(self.public / 'editorial-trials'): path.read_bytes()
                       for path in (self.public / 'editorial-trials').rglob('*') if path.is_file()}
        # Une lecture divergente est refusée : pièce altérée puis manifeste altéré, sans contenu servi
        stored = self.public / 'published-results' / task / version
        piece_path = stored / first_id / ('piece-' + selected[0] + '.txt')
        original_piece = piece_path.read_bytes()
        piece_path.write_bytes(b'octets alteres')
        self.request(first, status=404)
        self.request(first + '/pieces/' + selected[0], status=404)
        piece_path.write_bytes(original_piece)
        manifest_path = stored / first_id / 'manifest.json'
        original_manifest = manifest_path.read_bytes()
        manifest_path.write_bytes(original_manifest + b' ')
        self.request(first, status=404)
        manifest_path.write_bytes(original_manifest)
        self.request(first)
        self.assertEqual({first_id, second_id, partial_id},
                         {campaign['campaign_id'] for campaign in
                          public_results.published(self.public, task, version)})

        page, _, raw = self.request('/epreuves/' + task + '/' + version)
        self.assertIn('Résultats publiés', page.visible)
        links = [node['attrs']['href'] for node in page.nodes
                 if node['tag'] == 'a' and 'Campagne du' in node['text']]
        self.assertEqual([first, second, partial], links)
        self.assertNotIn(b'<script', raw)
        self.assertNotIn(need.encode(), raw)
        self.assertNotIn(KEY.encode(), raw)
        catalog, _, _ = self.request('/epreuves')
        self.assertIn(title, catalog.visible)
        self.assertEqual('Résultats publiés', catalog.field('Résultats publiés'))
        self.assertNotIn('Aucun résultat publié', catalog.visible)

        page, headers, raw = self.request(first, cookies=SimpleCookie())
        self.assertIsNone(headers.get('Set-Cookie'))
        self.assertEqual('no-referrer', headers['Referrer-Policy'])
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertIn("form-action 'self'", headers['Content-Security-Policy'])
        self.assertNotIn(b'<script', raw)
        main = next(node for node in page.nodes if node['tag'] == 'main')['text']
        for value in (title, work, 'Modèle A', 'Modèle C', 'Satisfait', 'Ne satisfait pas',
                      'Une action annoncée par la consigne manque au tableau.',
                      'Réponses évaluées : 2 · essais lancés : 2 sur 2',
                      'Comparaison des coûts incomplète', 'Coût observé inconnu pour 1 réponse',
                      'Conditions communes', 'Campagne du', 'Toutes les épreuves'):
            self.assertIn(value, page.visible)
        for private_value in (need, reformulation or '', KEY, judge_text, 'operation_id', 'body_base64',
                             'receipt', '/preparation/dossiers/'):
            if private_value:
                self.assertNotIn(private_value, main)
        self.assertIn('un filtre ne change ni les verdicts ni la population', page.visible.lower())
        self.assertIn('pièce non publiée', main.lower())
        # Configuration demandée, observée et sources : différence et inconnue explicites
        page_raw = raw.decode()
        self.assertIn('fournisseur-observe', page_raw)
        self.assertRegex(page_raw, r'Fournisseur</th><td[^>]*>OpenRouter</td><td[^>]*>fournisseur-observe</td>')
        self.assertRegex(page_raw, r'Observations inconnues :[^<]*Effort de raisonnement')

        piece_url = first + '/pieces/' + selected[0]
        _, piece_headers, piece_raw = self.request(piece_url, cookies=SimpleCookie())
        self.assertEqual('text/plain; charset=utf-8', piece_headers['Content-Type'])
        self.assertEqual(selected_text, piece_raw)
        with self.assertRaises(public_results.NotFound):
            public_results.piece_bytes(self.public, task, version, first_id, unselected[0])
        for pid in unselected + judge:
            self.request(first + '/pieces/' + pid, status=404)
        # Aucune autre campagne privée n’est exposée
        self.request(third, status=404)
        self.assertNotIn(third_id, raw.decode())

        self.assertEqual(2, raw.count(b'<tr id="reponse-'))
        self.assertEqual(1, self.request(first + '?configuration=1')[2].count(b'<tr id="reponse-'))
        self.assertEqual(1, self.request(first + '?verdict=SATISFAIT')[2].count(b'<tr id="reponse-'))
        self.assertEqual(1, self.request(first + '?verdict=NE%20SATISFAIT%20PAS')[2].count(b'<tr id="reponse-'))
        self.assertIn('Aucune réponse ne correspond à ces filtres',
                      self.request(first + '?verdict=A_REPRENDRE')[0].visible)
        self.request(first + '?sort=cost')
        self.request(first + '?sort=cost&direction=desc')
        obligation = json.loads(one['manifest'])['obligations'][0]
        self.request(first + '?obligation=' + obligation['id'] + ':PASS')
        self.request(first + '?sort=inconnu', status=400)
        self.request(first + '?sort=cost&sort=cost', status=400)
        rejected, _, rejected_raw = self.request(first + '?besoin=prive', status=400)
        self.assertNotIn(b'prive', rejected_raw)
        self.assertNotIn('prive', rejected.visible)

        page, _, second_raw = self.request(second)
        self.assertIn('Campagne du', page.visible)
        self.assertEqual({first, partial}, {node['attrs']['href'] for node in page.nodes
                                           if node['tag'] == 'a' and 'Campagne du' in node['text']})
        self.assertEqual(2, second_raw.count(b'<tr id="reponse-'))
        self.assertIn('coûts complets', page.visible.lower())
        page, _, partial_raw = self.request(partial)
        self.assertIn('Réponses évaluées : 1 · essais lancés : 3 sur 3', page.visible)
        self.assertIn('Réponses en attente ou incidents', page.visible)
        self.assertIn('Aucune réponse exploitable', page.visible)
        self.assertIn('Coût observé : 0,10 USD', page.visible)
        self.assertIn('Coût observé : Inconnu', page.visible)
        self.assertIn('Comparaison des coûts incomplète', page.visible)
        self.assertEqual(1, partial_raw.count(b'<tr id="reponse-'))
        self.assertEqual({first, second}, {node['attrs']['href'] for node in page.nodes
                                           if node['tag'] == 'a' and 'Campagne du' in node['text']})

        # Lecture autonome : le stockage privé source a disparu et le relais privé est interdit
        moved = self.data.with_name(self.data.name + '-absent')
        try:
            with patch.object(server, 'preparation_request', side_effect=AssertionError('Relais privé interdit')):
                self.data.rename(moved)
                try:
                    self.request(first)
                    self.request(piece_url)
                    self.request('/epreuves/' + task + '/' + version)
                finally:
                    moved.rename(self.data)
        finally:
            if moved.exists() and not self.data.exists():
                moved.rename(self.data)

        # Anciens manifestes éditoriaux inchangés par l’ajout de résultats
        self.assertEqual(trial_files, {path.relative_to(self.public / 'editorial-trials'): path.read_bytes()
                                       for path in (self.public / 'editorial-trials').rglob('*') if path.is_file()})
        manifest_bytes = (stored / first_id / 'manifest.json').read_bytes()
        second_manifest = (stored / second_id / 'manifest.json').read_bytes()
        piece_bytes = (stored / first_id / ('piece-' + selected[0] + '.txt')).read_bytes()

        # Retrait d’une restitution : l’épreuve et l’autre restitution restent disponibles
        with self.assertRaises(ValueError) as raised:
            public_results.withdraw(self.public, task, version, first_id, public_results.fixture_withdrawal(
                one['projection_sha256'], task, version, first_id))
        self.assertEqual('Autorité de publication refusée', str(raised.exception))
        with self.assertRaises(ValueError) as raised:
            public_results.withdraw(self.public, task, version, first_id, dict(
                public_results.withdrawal(one['projection_sha256'], task, version, first_id),
                package_sha256='a' * 64))
        self.assertEqual('Approbation visant d’autres octets', str(raised.exception))
        decision = public_results.withdrawal(one['projection_sha256'], task, version, first_id)
        public_results.withdraw(self.public, task, version, first_id, decision)
        public_results.withdraw(self.public, task, version, first_id, decision)
        withdrawn, _, withdrawn_raw = self.request(first)
        self.assertIn('Cette restitution est retirée', withdrawn.visible)
        self.assertNotIn('Modèle A', withdrawn.visible)
        self.assertNotIn(b'<script', withdrawn_raw)
        self.request(piece_url, status=404)
        self.request(first + '/pieces/' + 'a' * 32, status=404)
        self.assertIn('Campagne du', self.request(second)[0].visible)
        trial_page = self.request('/epreuves/' + task + '/' + version)[0]
        self.assertIn(title, trial_page.visible)
        self.assertNotIn(first, [node['attrs']['href'] for node in trial_page.nodes if node['tag'] == 'a'])
        self.assertEqual(manifest_bytes, (stored / first_id / 'manifest.json').read_bytes())
        self.assertEqual(piece_bytes, (stored / first_id / ('piece-' + selected[0] + '.txt')).read_bytes())
        self.assertEqual({second_id, partial_id}, {campaign['campaign_id']
                                                   for campaign in public_results.published(self.public, task, version)})

        # Retrait de l’épreuve : toutes ses restitutions deviennent inaccessibles, octets conservés
        editorial.withdraw(self.public, task, version, dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=trial['package_sha256'],
            scope='editorial-withdrawal', task_id=task, version_id=version))
        self.assertIn('Cette version est retirée',
                      self.request('/epreuves/' + task + '/' + version)[0].visible)
        for campaign in (first, second, partial):
            page, _, raw = self.request(campaign)
            self.assertIn('Cette épreuve est retirée', page.visible)
            self.assertNotIn('Modèle A', page.visible)
            self.assertNotIn(b'<script', raw)
            for pid in (selected[0], second_outputs[0], partial_outputs[0]):
                self.request(campaign + '/pieces/' + pid, status=404)
        self.assertEqual([], public_results.published(self.public, task, version))
        with self.assertRaises(ValueError) as raised:
            public_results.publish(self.public, two, public_results.approval(two['projection_sha256']))
        self.assertEqual('Épreuve retirée', str(raised.exception))
        self.assertEqual(manifest_bytes, (stored / first_id / 'manifest.json').read_bytes())
        self.assertEqual(second_manifest, (stored / second_id / 'manifest.json').read_bytes())

    def test_tri_numerique_resultats_publics(self):
        """#466 : le tri public suit le sens annoncé, les égalités et les inconnues"""
        from benchmark import restitution
        task, version = 'tache-fictive', 'version-1'
        dossier, page, _ = self.copie_preparee()
        dossier_id = dossier.rsplit('/', 1)[1]
        self.submit(page, '/validation', {})
        with closing(storage.Store(self.data)) as store:
            revision = store._connection.execute(
                'SELECT current_revision FROM s2_dossiers WHERE dossier_id=?', (dossier_id,)).fetchone()[0]
            reference = store._connection.execute(
                "SELECT piece_id FROM pieces WHERE dossier_id=? AND revision=? AND role='judge'",
                (dossier_id, revision)).fetchone()[0]
            spec = specification(reference)
            spec['secondary_criteria'] = [
                {'id': 'C1', 'measure': 'Nombre d’actions', 'unit': 'actions',
                 'proof': 'Chaque action du tableau, recomptée', 'favorable': 'higher',
                 'aggregation': None},
                {'id': 'C2', 'measure': 'Points vérifiés', 'unit': 'points',
                 'proof': 'Passages vérifiés dans le tableau', 'favorable': 'higher',
                 'aggregation': None}]
            contract = qualification.draft(store, dossier_id, revision, spec)
            receipt = qualification.qualify(store, contract['contract_sha256'], reviewer=ACTOR, check=check)
            qualification.approve(store, contract['contract_sha256'], receipt['qualification_id'],
                                  actor=ACTOR, authority=AUTHORITY)
            campaign = manifest(contract, 'numeric')
            campaign['panel'] = [dict(campaign['panel'][0], id=ident, channel_id='fixture-channel-' + ident,
                                      model='fictional-' + ident)
                                 for ident in ('x', 'y', 'z')]
            campaign['plan'] = [dict(cell_id=ident, case_id='notes', configuration_id=ident)
                                for ident in ('x', 'y', 'z')]
            campaign['attempt_policy'] = dict(retries=False, order=['x', 'y', 'z'], reason='Declared local order')
            campaign = campaigns.create(store, campaign)
            store.create_budget('local-comparison', '40', 'TEST')
            authority, evidence = inputs(campaign, cells=['x', 'y', 'z'])
            authority['reserve_amounts']['z'] = '7'
            campaigns.admit(store, 'numeric', authority, evidence)
            values = {'x': (2, 1), 'y': (2, 1), 'z': (10, 'INCONNU')}

            def report(ctx, resources):
                value = findings(ctx, resources)
                output = ctx['attempt']['output_piece_id']
                proof = [dict(piece_id=output, sha256=sha256(resources[output]).hexdigest(),
                              passage=resources[output].decode())]
                first, second = values[resources[output].decode().removeprefix('Sortie ')]
                value['measures'] = [dict(criterion_id='C1', value=first, unit='actions', evidence=proof),
                                     dict(criterion_id='C2', value=second, unit='points', evidence=proof)]
                return value

            def transport(operation, request):
                value = response(operation, request)
                value['receipt']['result']['output'] = 'Sortie ' + request['requested_configuration']['channel_id'].removeprefix('fixture-channel-')
                value['cost'].update(status='KNOWN', amount='2', currency='TEST')
                return value

            for ident in ('x', 'y', 'z'):
                attempts = campaigns.reserve(store, 'numeric', ident, 'attempt-' + ident)
                execution.execute(self.data, attempts['operation_id'], transport)
                evaluation.evaluate(store, 'numeric', attempts['operation_id'], responsible=RESPONSIBLE,
                                    authority=EVALUATION_AUTHORITY, check=report)
            campaigns.stop(store, 'numeric')
        campaign_id = 'numeric'
        url = '/epreuves/' + task + '/' + version + '/campagnes/' + campaign_id
        with closing(storage.Store(self.data)) as store:
            session = store._connection.execute(
                'SELECT session_id FROM s2_dossiers WHERE dossier_id=?', (dossier_id,)).fetchone()[0]
            comparison = restitution.comparison(store, session, dossier_id, campaign_id)
            self.assertEqual(3, len(comparison['rows']))
            outputs = sorted({row['output_piece_id'] for row in comparison['rows'] if row['output_piece_id']})
            self.assertEqual(3, len(outputs))
            bundle = restitution.results_bundle(store, dossier_id, campaign_id, piece_ids=outputs,
                                                task_id=task, version_id=version)
        public_results.publish(self.public, bundle, public_results.approval(bundle['projection_sha256']))
        manifest_doc = json.loads(bundle['manifest'])
        numeric = next(column['id'] for column in manifest_doc['columns'] if column['label'] == 'Nombre d’actions')
        precision = next(column['id'] for column in manifest_doc['columns'] if column['label'] == 'Points vérifiés')

        def order(extra):
            raw = self.request(url + extra)[2]
            return [int(number) for number in re.findall(rb'<tr id="reponse-(\d+)-1"', raw)]

        # Valeurs 2, 2 et 10 : croissant suit les valeurs, décroissant les inverse, l'égalité reste stable
        self.assertEqual([1, 2, 3], order('?sort=' + numeric))
        self.assertEqual([3, 1, 2], order('?sort=' + numeric + '&direction=desc'))
        # Valeurs 1, 1 et inconnue : l'inconnue reste sans rang, dans les deux sens
        self.assertEqual([1, 2, 3], order('?sort=' + precision))
        self.assertEqual([1, 2, 3], order('?sort=' + precision + '&direction=desc'))
        page = self.request(url + '?sort=' + precision)[0]
        self.assertIn('Non comparable : Mesure non relevée ou sans preuve', page.visible)

    def test_copie_epreuve_expiration_et_purge(self):
        """#465 : la copie et sa source suivent les règles privées de conservation, sans réactivation"""
        from benchmark import privacy
        task, version = 'tache-fictive', 'version-1'
        self.publier_epreuve(version)
        _, headers, _ = self.copie_epreuve(version)
        dossier = headers['Location']
        dossier_id = dossier.rsplit('/', 1)[1]
        token = self.cookies['benchmark_session'].value
        privacy.migrate(self.data, SECRET, 'test-465')
        with closing(storage.Store(self.data)) as store:
            session_id, _, _ = prep.session(store, token)
            current = privacy.now()
            store._connection.execute(
                'UPDATE s7_dossiers SET retention_started_at=?,last_activity_at=?,expires_at=? WHERE dossier_id=?',
                ((current - timedelta(days=10)).isoformat(), (current - timedelta(days=9)).isoformat(),
                 (current - timedelta(days=8)).isoformat(), dossier_id))
        expired, _, expired_raw = self.request(dossier, status=410)
        self.assertIn('n’est plus disponible sur le serveur', expired.visible)
        self.assertNotIn('Relève les opérations inventées'.encode(), expired_raw)
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(1, store._connection.execute(
                'SELECT count(*) FROM s2_revisions WHERE dossier_id=?', (dossier_id,)).fetchone()[0])
            privacy.request_delete(store, session_id, dossier_id)
        purged = privacy.purge(self.data, wait=1, drain=1)
        self.assertIn(dossier_id, purged['purged'])
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(0, store._connection.execute(
                'SELECT count(*) FROM s2_revisions WHERE dossier_id=?', (dossier_id,)).fetchone()[0])
            self.assertEqual(0, store._connection.execute(
                'SELECT count(*) FROM s2_dossiers WHERE dossier_id=?', (dossier_id,)).fetchone()[0])
            self.assertNotIn('Relève les opérations inventées', ' '.join(
                row[0] for row in store._connection.execute('SELECT checks_json FROM s2_revisions').fetchall()))
        late, _, late_raw = self.request(dossier, status=410)
        self.assertNotIn('Relève les opérations inventées'.encode(), late_raw)
        # La source publique reste indépendante : sa suppression privée ne réécrit pas le catalogue
        public, _, _ = self.request('/epreuves/' + task + '/' + version)
        self.assertIn('Tableau des opérations', public.visible)

    def test_repertoire_renvoi_preparation(self):
        references = json.loads((Path(__file__).parent / 'fixtures/external-references.json').read_text())
        def suspended(operation, request):
            value = response_for(operation)
            value['cost'].update(amount='0.10', currency='USD', source='Reçu simulé')
            value['receipt']['result'].update(stage='suspended', package=None, out_of_scope='coding',
                                              explanation='Épreuve générale, sans tâche de travail concrète')
            return value
        with patch.object(views, 'BENCHMARK_REFERENCES', references), patch.object(self, 'prepare', suspended):
            page, _, _ = self.request('/preparation')
            self.submit(page, '/access/key', {'key': KEY})
            page, _, _ = self.request('/preparation')
            page = self.submit(page, '/dossiers', {'request': 'Besoin privé fictif : une épreuve générale de programmation'})
            dossier = page.link('Actualiser')
            prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
            page, _, _ = self.request(dossier)
            self.assertIn('Bench-X ne peut pas comparer cette tâche', page.visible)
            self.assertEqual('/evaluations-externes', page.link('Évaluations externes'))
            target = page.link('Atelier Code')
            direct = self.request('/evaluations-externes/atelier-code')[2]
            notice, headers, raw = self.request(target)
            self.assertEqual(direct, raw)
            self.assertEqual('no-referrer', headers['Referrer-Policy'])
            self.assertNotIn('Besoin privé', notice.visible)
            self.assertNotIn('Atelier Retiré', page.visible)
            self.assertEqual('/preparation', notice.link('Décrire mon cas'))
            # L'aide reste séparée du renvoi : elle ne reçoit ni le besoin privé ni sa trace
            aid_page, _, aid_raw = self.request('/evaluations-externes/aide')
            self.assertNotIn('Besoin privé', aid_page.visible)
            self.assertNotIn(b'Besoin priv', aid_raw)
            self.assertEqual('/preparation', aid_page.link('Décrire mon cas d’usage'))
            index_page, _, _ = self.request('/evaluations-externes')
            self.assertIn('/evaluations-externes/aide',
                          [node['attrs'].get('href') for node in index_page.nodes])
            # Un retrait est immédiatement reflété dans les renvois, sans autre liste à maintenir
            with patch.dict(references['atelier-code'], availability='withdrawn'):
                page, _, _ = self.request(dossier)
                self.assertNotIn('Atelier Code', page.visible)
                self.assertIn('Source retirée', self.request(target)[0].visible)

    def semantic_data(self):
        temporary = tempfile.TemporaryDirectory(prefix='semantic-')
        self.addCleanup(temporary.cleanup)
        data = Path(temporary.name).resolve() / 'private'
        storage.initialize(data)
        storage.initialize_preparation(data)
        semantic_help.initialize(data)
        return data

    def test_aide_semantique_repertoire(self):
        references = json.loads((Path(__file__).parent / 'fixtures/external-references.json').read_text())
        formulation = 'Corriger un programme fictif et résoudre un calcul inventé'
        def two_suggestions(wire):
            return {'status': 200, 'body': typesafe_document(wire, ['correspond', 'partiel']),
                    'complete': True}
        with patch.object(views, 'BENCHMARK_REFERENCES', references):
            transport = self.enable_semantic(TypeSafeFixture(two_suggestions))
            page, path = self.help_submit(formulation)
            self.assertIn('Correspondance forte', page.visible)
            self.assertIn('Correspondance partielle', page.visible)
            self.assertIn('Atelier Code fictif', page.visible)
            self.assertIn('Atelier Maths fictif', page.visible)
            self.assertIn('ne prouve ni adéquation', page.visible)
            self.assertTrue(any(n['tag'] == 'summary' and 'Reçu technique' in n['text']
                            for n in page.nodes))
            self.assertEqual(1, len(transport.wires))
            document = json.loads(transport.wires[0])
            self.assertEqual({'model', 'state', 'questions'}, set(document))
            self.assertEqual('jev-1.13.0', document['model'])
            self.assertEqual(formulation, document['state']['formulation'])
            self.assertEqual(['n1', 'n2'], [notice['ref'] for notice in document['state']['notices']])
            for notice in document['state']['notices']:
                self.assertNotIn('id', notice)
                self.assertIn('name', notice)
                self.assertIn('work', notice)
                for key in ('url', 'checked_at', 'verification', 'availability', 'published_at'):
                    self.assertNotIn(key, notice)
            wire = transport.wires[0]
            for absent in ('https://code.example', 'https://maths.example', KEY,
                           'benchmark_session', 'dossier', 'Référence de jugement'):
                self.assertNotIn(absent, wire)
            self.assertEqual(2, len(document['questions']))
            self.assertTrue(all(question['type'] == 'choice'
                                for question in document['questions'].values()))
            # Aucun texte recherché ni URL de notice ne subsiste dans les fichiers durables
            for stored in self.data.rglob('*'):
                if stored.is_file():
                    payload = stored.read_bytes()
                    self.assertNotIn(formulation.encode(), payload, stored.name)
                    self.assertNotIn(b'https://code.example', payload, stored.name)
            # Une notice retirée après émission disparaît avant affichage et ouverture
            with patch.dict(references['atelier-code'], {'availability': 'withdrawn'}):
                removed, _, _ = self.request(path)
                self.assertNotIn('Atelier Code fictif', removed.visible)
                self.assertIn('Une suggestion a été retirée', removed.visible)
                self.assertNotIn('/evaluations-externes/atelier-code',
                                 [node['attrs'].get('href') for node in removed.nodes])
                self.assertIn('Source retirée',
                              self.request('/evaluations-externes/atelier-code')[0].visible)
            # Aucune correspondance reste distinct d'une aide indisponible
            self.enable_semantic(TypeSafeFixture())
            none, _ = self.help_submit('Un besoin sans rapport avec le répertoire')
            self.assertIn('Aucune notice ne correspond', none.visible)
            self.assertNotIn('Aide indisponible', none.visible)
            # Réponses invalides et incidents : aucune suggestion inventée, aucun nouvel appel
            def mutated(change, *, status=200, complete=True):
                def handler(wire):
                    document = json.loads(typesafe_document(wire))
                    change(document)
                    return {'status': status, 'body': json.dumps(document).encode(),
                            'complete': complete}
                return handler
            cases = {
                'modele divergent': (mutated(lambda value: value.update(model='jev-1.14.0')), 'inexploitable'),
                'reponse partielle': (mutated(lambda value: value['answers'].pop('n2')), 'inexploitable'),
                'identifiant inconnu': (mutated(lambda value: value['answers'].update(
                    inconnue=value['answers']['n1'])), 'inexploitable'),
                'structure invalide': (mutated(lambda value: value['answers']['n1'].pop('probabilities')),
                                       'inexploitable'),
                'probabilite non finie': (mutated(lambda value: value['answers']['n1']['probabilities'].update(
                    partiel=float('nan'))), 'inexploitable'),
                'probabilites incoherentes': (mutated(lambda value: value['answers']['n1']['probabilities'].update(
                    correspond=0.9, partiel=0.9, sans_correspondance=0.9)), 'inexploitable'),
                'confiance hors bornes': (mutated(lambda value: value['answers']['n1'].update(
                    confidence=2)), 'inexploitable'),
                'corps incomplet': (mutated(lambda value: None, complete=False), 'inexploitable'),
                'choix en liste': (mutated(lambda value: value['answers']['n1'].update(choice=[])),
                                   'inexploitable'),
                'choix en objet': (mutated(lambda value: value['answers']['n1'].update(choice={})),
                                   'inexploitable'),
                'limite de debit': (mutated(lambda value: None, status=429), 'limite temporairement'),
                'surcharge': (mutated(lambda value: None, status=529), 'surchargé'),
            }
            for name, (handler, expected) in cases.items():
                with self.subTest(cas=name):
                    transport = self.enable_semantic(TypeSafeFixture(handler))
                    failed, failed_path = self.help_submit('Corriger un programme fictif')
                    self.assertIn('Aide indisponible', failed.visible)
                    self.assertIn(expected, failed.visible)
                    self.assertNotIn('Correspondance forte', failed.visible)
                    calls = len(transport.wires)
                    request_id = failed_path.rsplit('/', 1)[1]
                    receipt_store = storage.Store(self.data)
                    try:
                        receipt_code, receipt_value = semantic_help.receipt(
                            receipt_store, request_id)
                    finally:
                        receipt_store.close()
                    expected_reason = {'limite de debit': 'RATE_LIMITED',
                                       'surcharge': 'OVERLOADED'}.get(
                                           name, 'INVALID_RESPONSE')
                    self.assertEqual(200, receipt_code)
                    self.assertEqual(('unavailable', expected_reason),
                                     (receipt_value['status'], receipt_value['reason']))
                    self.assertEqual('UNKNOWN', receipt_value['receipt']['cost']['status'])
                    inspect_store = storage.Store(self.data)
                    try:
                        self.assertNotEqual('0', semantic_help.inspect(
                            inspect_store)['budget']['reserved'])
                    finally:
                        inspect_store.close()
                    again, again_path = self.help_submit(
                        'Corriger un programme fictif',
                        submission_id=request_id)
                    self.assertEqual(failed_path, again_path)
                    self.assertEqual(calls, len(transport.wires))
            # Le délai ne rejoue rien non plus
            def timeout(_):
                raise TimeoutError('délai fictif')
            transport = self.enable_semantic(TypeSafeFixture(timeout))
            failed, failed_path = self.help_submit('Corriger un programme fictif')
            self.assertIn('délai de dix secondes', failed.visible)
            self.assertEqual(1, len(transport.wires))
            # Un répertoire sans notice admissible rend une réponse valide sans suggestion, sans appel
            empty_transport = self.enable_semantic(TypeSafeFixture())
            with patch.object(views, 'BENCHMARK_REFERENCES', {}):
                empty, _, _ = self.request('/evaluations-externes/aide',
                                           {'submission_id': 'b' * 32,
                                            'formulation': 'Corriger un programme fictif'}, status=200)
                self.assertIn('Aucune notice ne correspond', empty.visible)
                self.assertNotIn('Aide indisponible', empty.visible)
            self.assertEqual([], empty_transport.wires)
            # Protections HTTP et absence de texte dans l'adresse ou le corps des pages
            _, headers, raw = self.request('/evaluations-externes/aide')
            self.assertEqual('no-store', headers['Cache-Control'])
            self.assertEqual('nosniff', headers['X-Content-Type-Options'])
            self.assertEqual('no-referrer', headers['Referrer-Policy'])
            self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
            self.assertNotIn(b'<script', raw)
            self.request('/evaluations-externes/aide?q=prive', status=400)
            self.request('/evaluations-externes/aide/' + 'f' * 32, status=404)
            cross = self.request('/evaluations-externes/aide',
                                 {'submission_id': 'a' * 32, 'formulation': 'Corriger un programme'},
                                 status=400,
                                 extra_headers={'Origin': 'https://evil.example',
                                                'Sec-Fetch-Site': 'cross-site'})
            self.assertIn('incomplète', cross[0].visible)
            injection = '<script>alert(9)</script>'
            self.enable_semantic(tariff=None)
            form_page, _, _ = self.request('/evaluations-externes/aide')
            form = form_page.form('/evaluations-externes/aide')
            escaped, _, escaped_raw = self.request(
                form['action'], form['fields'] | {'formulation': injection}, status=200)
            self.assertIn(injection, escaped.visible)
            self.assertNotIn(injection.encode(), escaped_raw)
            self.assertIn(b'&lt;script&gt;', escaped_raw)

    def test_aide_semantique_finance_limites_et_redemarrage(self):
        data = self.semantic_data()
        notices = [{'id': 'code', 'name': 'Code fictif', 'category': 'coding',
                    'work': 'Corriger un programme fictif', 'version': 'fixture-1'},
                   {'id': 'maths', 'name': 'Maths fictif', 'category': 'math',
                    'work': 'Résoudre un calcul fictif', 'version': 'fixture-2'}]
        def body(request_id, formulation='Corriger un programme fictif'):
            return {'submission_id': request_id, 'formulation': formulation, 'notices': notices}
        def success(wire):
            return {'status': 200, 'body': typesafe_document(wire, ['correspond', 'partiel'],
                                                             input_tokens=1000),
                    'complete': True}
        store = storage.Store(data)
        self.addCleanup(store.close)
        # Un tarif absent bloque avant tout envoi et ne réserve rien
        no_tariff = semantic_help.configure({
            'format': semantic_help.PROFILE_FORMAT, 'tariff': None,
            'usages': {'repertoire': {'enabled': True, 'suggestion_threshold': '0.5'},
                       'epreuves': {'enabled': False, 'suggestion_threshold': None}}})
        transport = TypeSafeFixture(success)
        code, value = semantic_help.handle(store, body('1' * 32), transport, no_tariff,
                                           usage='repertoire')
        self.assertEqual((503, 'FINANCE', 0, []),
                         (code, value['reason'], len(transport.wires),
                          semantic_help.inspect(store)['requests']))
        # Une base restaurée n'admet aucune émission avant vérification
        marker = store._root / 'restore.json'
        marker.write_text('{"state": "RESTORED_RECONCILIATION_REQUIRED"}')
        try:
            code, value = semantic_help.handle(store, body('0' * 32), transport, no_tariff,
                                               usage='repertoire')
        finally:
            marker.unlink()
        self.assertEqual((503, 'RESTORE_PENDING', 0),
                         (code, value['reason'], len(transport.wires)))
        # Le dépassement de contexte est refusé sans troncature ni appel
        config = semantic_help.configure({
            'format': semantic_help.PROFILE_FORMAT,
            'tariff': {'input_usd_per_million_tokens': '10', 'currency': 'USD',
                       'effective_at': '2026-10-24', 'source': 'https://fixture.example/tarif'},
            'usages': {'repertoire': {'enabled': True, 'suggestion_threshold': '0.5'},
                       'epreuves': {'enabled': True, 'suggestion_threshold': '0.5'}}})
        for length, label in ((40000, 'question'), (70000, 'requête')):
            with self.subTest(limite=label):
                try:
                    semantic_help.handle(store, body('2' * 32, 'x' * length), transport, config,
                                         usage='repertoire')
                except semantic_help.Unavailable as error:
                    self.assertEqual(('CONTEXT', 413), (error.reason, error.code))
                else:
                    self.fail('dépassement de contexte accepté')
        self.assertEqual([], semantic_help.inspect(store)['requests'])
        # Enveloppe commune : la consommation de l'un bloque l'autre usage
        budget_config = semantic_help.configure({
            'format': semantic_help.PROFILE_FORMAT,
            'tariff': {'input_usd_per_million_tokens': '500', 'currency': 'USD',
                       'effective_at': '2026-10-24', 'source': 'https://fixture.example/tarif'},
            'usages': {'repertoire': {'enabled': True, 'suggestion_threshold': '0.5'},
                       'epreuves': {'enabled': True, 'suggestion_threshold': '0.5'}}})
        transport = TypeSafeFixture(success)
        code, value = semantic_help.handle(store, body('3' * 32), transport, budget_config,
                                           usage='repertoire')
        self.assertEqual((200, 'suggestions'), (code, value['status']))
        state = semantic_help.inspect(store)
        self.assertEqual('0.5', state['budget']['spent'])
        self.clock += timedelta(seconds=2)
        try:
            semantic_help.handle(store, body('4' * 32), transport, budget_config, usage='epreuves')
        except semantic_help.Unavailable as error:
            self.assertEqual(('BUDGET', 429), (error.reason, error.code))
        else:
            self.fail('enveloppe partagée non appliquée')
        self.assertEqual(1, len(transport.wires))
        self.assertEqual(1, len(semantic_help.inspect(store)['requests']))
        # Le second usage fictif réutilise la même intégration et ses propres questions versionnées
        catalogue_data = self.semantic_data()
        catalogue_store = storage.Store(catalogue_data)
        self.addCleanup(catalogue_store.close)
        catalogue_transport = TypeSafeFixture(success)
        code, value = semantic_help.handle(
            catalogue_store, body('e' * 32), catalogue_transport, budget_config, usage='epreuves')
        self.assertEqual((200, 'suggestions', 'epreuves-v1'),
                         (code, value['status'], value['receipt']['questions_version']))
        self.assertEqual('epreuves-v1', value['receipt']['questions_version'])
        self.assertIn('épreuve éditoriale',
                      json.loads(catalogue_transport.wires[0])['questions']['n1']['instructions']['question'])

    def test_aide_semantique_incident_concurrence_et_redemarrage(self):
        data = self.semantic_data()
        notices = [{'id': 'code', 'name': 'Code fictif', 'work': 'Corriger un programme fictif'}]
        def body(request_id, formulation='Corriger un programme fictif'):
            return {'submission_id': request_id, 'formulation': formulation, 'notices': notices}
        tight_config = semantic_help.configure({
            'format': semantic_help.PROFILE_FORMAT,
            'tariff': {'input_usd_per_million_tokens': '900', 'currency': 'USD',
                       'effective_at': '2026-10-24', 'source': 'https://fixture.example/tarif'},
            'usages': {'repertoire': {'enabled': True, 'suggestion_threshold': '0.5'},
                       'epreuves': {'enabled': False, 'suggestion_threshold': None}}})
        concurrent_config = semantic_help.configure({
            'format': semantic_help.PROFILE_FORMAT,
            'tariff': {'input_usd_per_million_tokens': '10', 'currency': 'USD',
                       'effective_at': '2026-10-24', 'source': 'https://fixture.example/tarif'},
            'usages': {'repertoire': {'enabled': True, 'suggestion_threshold': '0.5'},
                       'epreuves': {'enabled': True, 'suggestion_threshold': '0.5'}}})
        def success(wire):
            return {'status': 200, 'body': typesafe_document(wire, ['correspond'],
                                                             input_tokens=1000), 'complete': True}
        # Un incident 529 laisse un coût inconnu et conserve sa réserve
        store = storage.Store(data)
        self.addCleanup(store.close)
        incident = TypeSafeFixture(lambda _: {'status': 529, 'body': b'', 'complete': True})
        code, value = semantic_help.handle(store, body('5' * 32), incident, tight_config,
                                           usage='repertoire')
        self.assertEqual((200, 'unavailable', 'OVERLOADED'),
                         (code, value['status'], value['reason']))
        self.assertEqual('UNKNOWN', value['receipt']['cost']['status'])
        state = semantic_help.inspect(store)
        self.assertEqual('UNKNOWN', state['requests'][0]['cost_status'])
        self.assertNotEqual('0', state['budget']['reserved'])
        self.assertEqual('0', state['budget']['spent'])
        self.assertEqual('OVERLOADED', semantic_help.receipt(store, '5' * 32)[1]['reason'])
        self.clock += timedelta(seconds=2)
        try:
            semantic_help.handle(store, body('6' * 32), TypeSafeFixture(success), tight_config,
                                 usage='repertoire')
        except semantic_help.Unavailable as error:
            self.assertEqual('BUDGET', error.reason)
        else:
            self.fail('réserve inconnue libérée')
        self.assertEqual(1, len(semantic_help.inspect(store)['requests']))
        # Une réponse tardive termine la demande en TIMEOUT, sans suggestion, et garde sa réserve
        self.assertEqual(10, semantic_help.TIMEOUT_SECONDS)
        late_data = self.semantic_data()
        late_store = storage.Store(late_data)
        self.addCleanup(late_store.close)
        late_entered = [threading.Event(), threading.Event()]
        late_finished = threading.Event()
        late_calls = []
        finished = {'count': 0}
        finished_lock = threading.Lock()
        def late(wire):
            late_calls.append(wire)
            late_entered[len(late_calls) - 1].set()
            time.sleep(0.6)
            with finished_lock:
                finished['count'] += 1
                if finished['count'] == 2:
                    late_finished.set()
            return success(wire)
        late_transport = TypeSafeFixture(late)
        late_results = []
        def late_submit(request_id):
            thread_store = storage.Store(late_data)
            try:
                late_results.append(semantic_help.handle(
                    thread_store, body(request_id), late_transport, concurrent_config,
                    usage='repertoire'))
            finally:
                thread_store.close()
        with patch.object(semantic_help, 'TIMEOUT_SECONDS', 0.15):
            first_late = threading.Thread(target=late_submit, args=('d' * 32,))
            first_late.start()
            self.assertTrue(late_entered[0].wait(3))
            self.clock += timedelta(seconds=2)
            second_late = threading.Thread(target=late_submit, args=('e' * 32,))
            second_late.start()
            self.assertTrue(late_entered[1].wait(3))
            first_late.join(3)
            second_late.join(3)
            self.assertEqual([('unavailable', 'TIMEOUT')] * 2,
                             [(result[1]['status'], result[1]['reason'])
                              for result in late_results])
            self.assertEqual([[], []], [result[1]['suggestions'] for result in late_results])
            self.assertEqual(2, len(late_calls))
            late_state = semantic_help.inspect(late_store)
            self.assertEqual(['RECEIVED', 'RECEIVED'],
                             [row['state'] for row in late_state['requests']])
            self.assertEqual(['UNKNOWN', 'UNKNOWN'],
                             [row['cost_status'] for row in late_state['requests']])
            self.assertNotEqual('0', late_state['budget']['reserved'])
            # Les deux appels tardifs comptent encore dans la limite commune
            self.clock += timedelta(seconds=2)
            third_transport = TypeSafeFixture(success)
            try:
                semantic_help.handle(late_store, body('f' * 32), third_transport,
                                     concurrent_config, usage='repertoire')
            except semantic_help.Unavailable as error:
                self.assertEqual(('RATE', 429), (error.reason, error.code))
            else:
                self.fail('appel tardif ignoré dans la limite de concurrence')
            self.assertEqual([], third_transport.wires)
            self.assertTrue(late_finished.wait(2))
            # Les places se libèrent une fois les appels abandonnés terminés
            fourth = None
            for _ in range(20):
                self.clock += timedelta(seconds=2)
                try:
                    fourth = semantic_help.handle(late_store, body('1' * 32),
                                                  TypeSafeFixture(success), concurrent_config,
                                                  usage='repertoire')
                    break
                except semantic_help.Unavailable as error:
                    self.assertEqual('RATE', error.reason)
                    time.sleep(0.05)
            self.assertIsNotNone(fourth)
            self.assertEqual((200, 'suggestions'), (fourth[0], fourth[1]['status']))
            time.sleep(0.1)
        # Deux soumissions concurrentes de la même demande n'émettent qu'une fois
        duplicate_data = self.semantic_data()
        duplicate_entered = threading.Event()
        duplicate_release = threading.Event()
        duplicate_calls = []
        def duplicate(wire):
            duplicate_calls.append(wire)
            duplicate_entered.set()
            duplicate_release.wait(5)
            return success(wire)
        duplicate_transport = TypeSafeFixture(duplicate)
        duplicate_results = []
        def duplicate_submit():
            thread_store = storage.Store(duplicate_data)
            try:
                duplicate_results.append(semantic_help.handle(
                    thread_store, body('d' * 32), duplicate_transport, concurrent_config,
                    usage='repertoire'))
            finally:
                thread_store.close()
        duplicate_first = threading.Thread(target=duplicate_submit)
        duplicate_first.start()
        self.assertTrue(duplicate_entered.wait(3))
        duplicate_second = threading.Thread(target=duplicate_submit)
        duplicate_second.start()
        duplicate_release.set()
        duplicate_first.join(5)
        duplicate_second.join(5)
        self.assertFalse(duplicate_first.is_alive() or duplicate_second.is_alive())
        self.assertEqual(1, len(duplicate_calls))
        self.assertEqual([200, 200], [result[0] for result in duplicate_results])
        # Deux émissions simultanées au plus, une nouvelle par seconde ; l'autre usage partage la limite
        concurrent_data = self.semantic_data()
        concurrent_store = storage.Store(concurrent_data)
        self.addCleanup(concurrent_store.close)
        entered = [threading.Event(), threading.Event()]
        release = threading.Event()
        active = []
        clock = {'value': self.clock}
        patcher = patch.object(semantic_help, '_now', side_effect=lambda: clock['value'])
        patcher.start()
        self.addCleanup(patcher.stop)
        base = clock['value']
        def blocking(wire):
            active.append(wire)
            entered[len(active) - 1].set()
            release.wait(5)
            return success(wire)
        blocking_transport = TypeSafeFixture(blocking)
        results = []
        def submit(request_id):
            thread_store = storage.Store(concurrent_data)
            try:
                results.append(semantic_help.handle(thread_store, body(request_id),
                                                    blocking_transport, concurrent_config,
                                                    usage='repertoire'))
            finally:
                thread_store.close()
        first = threading.Thread(target=submit, args=('7' * 32,))
        first.start()
        self.assertTrue(entered[0].wait(3))
        clock['value'] = base + timedelta(seconds=2)
        second = threading.Thread(target=submit, args=('8' * 32,))
        second.start()
        self.assertTrue(entered[1].wait(3))
        clock['value'] = base + timedelta(seconds=4)
        refused = None
        try:
            semantic_help.handle(concurrent_store, body('9' * 32), TypeSafeFixture(success),
                                 concurrent_config, usage='epreuves')
        except semantic_help.Unavailable as error:
            refused = error
        finally:
            release.set()
        self.assertIsNotNone(refused, 'troisième émission simultanée acceptée')
        self.assertEqual(('RATE', 429), (refused.reason, refused.code))
        first.join(5)
        second.join(5)
        self.assertEqual([200, 200], [result[0] for result in results])
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(2, len(active))
        # Une nouvelle émission immédiate après la seconde est refusée par la fréquence
        clock['value'] = base + timedelta(seconds=2, milliseconds=100)
        try:
            semantic_help.handle(concurrent_store, body('a' * 32), TypeSafeFixture(success),
                                 concurrent_config, usage='repertoire')
        except semantic_help.Unavailable as error:
            self.assertEqual(('RATE', 429), (error.reason, error.code))
        else:
            self.fail('fréquence partagée non appliquée')
        # Interruption en vol : clôture au démarrage, reçu conservé, aucune réémission
        restart_data = self.semantic_data()
        restart_config = semantic_help.configure({
            'format': semantic_help.PROFILE_FORMAT,
            'tariff': {'input_usd_per_million_tokens': '10', 'currency': 'USD',
                       'effective_at': '2026-10-24', 'source': 'https://fixture.example/tarif'},
            'usages': {'repertoire': {'enabled': True, 'suggestion_threshold': '0.5'},
                       'epreuves': {'enabled': False, 'suggestion_threshold': None}}})
        lane = threading.Event()
        hold = threading.Event()
        calls = []
        def hanging(wire):
            calls.append(wire)
            lane.set()
            hold.wait(5)
            return success(wire)
        hanging_transport = TypeSafeFixture(hanging)
        restart_store = storage.Store(restart_data)
        self.addCleanup(restart_store.close)
        outcome = []
        def emit():
            thread_store = storage.Store(restart_data)
            try:
                outcome.append(semantic_help.handle(thread_store, body('b' * 32),
                                                   hanging_transport, restart_config,
                                                   usage='repertoire'))
            finally:
                thread_store.close()
        worker = threading.Thread(target=emit)
        worker.start()
        self.assertTrue(lane.wait(3))
        startup = storage.Store(restart_data)
        self.addCleanup(startup.close)
        self.assertEqual(1, semantic_help.close_interrupted(startup))
        hold.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(calls))
        self.assertEqual(503, outcome[0][0])
        again = semantic_help.handle(restart_store, body('b' * 32), hanging_transport,
                                     restart_config, usage='repertoire')
        self.assertEqual(200, again[0])
        self.assertEqual(('unavailable', 'INTERRUPTED'),
                         (again[1]['status'], again[1]['reason']))
        self.assertEqual(1, len(calls))
        # Une nouvelle demande volontaire après incident garde le reçu précédent
        clock['value'] += timedelta(seconds=2)
        self.clock += timedelta(seconds=2)
        fresh = semantic_help.handle(restart_store, body('c' * 32), TypeSafeFixture(success),
                                     restart_config, usage='repertoire')
        self.assertEqual((200, 'suggestions'), (fresh[0], fresh[1]['status']))
        self.assertEqual(2, len(semantic_help.inspect(restart_store)['requests']))

    def test_aide_semantique_epreuves_desactivee(self):
        """#467 : service désactivé, la recherche, les filtres, l’ouverture et la copie restent disponibles"""
        task = 'tache-fictive'
        self.publier_epreuve('version-calcul', title='Tableau des débits',
                             characteristics={'language': 'Français', 'tools': ['Tableur']})
        transport = TypeSafeFixture()
        # L’exécuteur reçoit un transport fictif mais aucune configuration : aucune émission ne peut partir
        self.semantic = (None, transport)

        found, _, _ = self.request('/epreuves?' + urlencode({'q': 'tableau', 'langue': 'Français'}))
        self.assertIn('Tableau des débits', found.visible)
        self.assertIn('Critères actifs', found.visible)
        opened = self.request('/epreuves/' + task + '/version-calcul')[0]
        self.assertIn('Tableau des débits', opened.visible)
        self.assertIn('Créer mon dossier privé', opened.visible)

        disabled, _ = self.editorial_help_submit('Lister les mouvements mensuels d’un compte fictif',
                                                  status=200)
        self.assertIn('Aide indisponible', disabled.visible)
        self.assertIn('désactivé', disabled.visible)
        self.assertIn('Recherche simple', disabled.visible)
        self.assertEqual([], transport.wires)
        self.assertEqual([], self.access.verifications)
        self.assertEqual([], self.calls)
        with closing(storage.Store(self.data)) as store:
            self.assertEqual([], semantic_help.inspect(store)['requests'])

        # La recherche et la copie restent disponibles après le refus de l’aide
        again = self.request('/epreuves?' + urlencode({'q': 'tableau'}))[0]
        self.assertIn('Tableau des débits', again.visible)
        result, headers, _ = self.copie_epreuve('version-calcul')
        self.assertRegex(headers['Location'], r'^/preparation/dossiers/[A-Za-z0-9_-]{1,128}$')
        self.assertEqual([], transport.wires)
        self.assertEqual([], self.access.verifications)
        self.assertEqual([], self.calls)
        self.assertTrue(self.starts.empty())

    def test_aide_semantique_epreuves(self):
        """#467 : formulation différente, suggestions publiques exactes, retrait et copie"""
        task = 'tache-fictive'
        formulation = 'Lister les mouvements mensuels d’un compte fictif'
        calcul = self.publier_epreuve(
            'version-calcul', title='Tableau des débits', work='Préparer un tableau par opération',
            characteristics={'expected_result': 'Tableau des débits', 'inputs': ['Textes fournis'],
                             'language': 'Français', 'tools': ['Tableur'], 'difficulty': 'Facile'})
        redaction = self.publier_epreuve(
            'version-redaction', title='Note de synthèse', work='Rédiger une note de synthèse',
            qualification='QUALIFIED',
            characteristics={'expected_result': 'Note de synthèse', 'inputs': ['Bibliothèque figée'],
                             'language': 'Anglais', 'tools': [], 'difficulty': 'Difficile'})
        old = self.publier_epreuve('version-ancienne', title='Ancienne épreuve', schema=editorial.SCHEMA_V1)
        retired = self.publier_epreuve('version-retiree', title='Épreuve retirée',
                                       characteristics={'language': 'Langue retirée'})
        editorial.withdraw(self.public, task, 'version-retiree', dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=retired['package_sha256'],
            scope='editorial-withdrawal', task_id=task, version_id='version-retiree'))
        # Autorité fictive déposée hors de son répertoire : jamais candidate
        fixture = self.construire_epreuve('version-fictive', title='Épreuve fictive',
                                          characteristics={'language': 'Langue fictive'})
        editorial.fixture_publish(self.public, fixture, editorial.fixture_approval(fixture['package_sha256']))
        deposited = self.public / 'editorial-trials' / task / 'version-fictive'
        deposited.mkdir(parents=True)
        for item in (self.public / 'editorial-fixture' / task / 'version-fictive').iterdir():
            (deposited / item.name).write_bytes(item.read_bytes())
        # Paquet malformé et paquet altéré : illisibles ou divergents, donc jamais candidats
        invalid = self.public / 'editorial-trials' / task / 'version-invalide'
        invalid.mkdir()
        (invalid / 'manifest.json').write_text('{"language":"Langue invalide"}')
        altered = self.publier_epreuve('version-alteree', title='Épreuve altérée',
                                       characteristics={'language': 'Langue altérée'})
        altered_piece = self.public / 'editorial-trials' / task / 'version-alteree' / 'piece-consigne.txt'
        altered_piece.write_text('Octets altérés')
        # Dossier privé et contribution voisine : jamais lus pour servir l’aide
        private_bundle = self.construire_epreuve('version-privee', title='Épreuve privée',
                                                 characteristics={'language': 'Langue privée'})
        editorial.publish(self.data, private_bundle, dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=private_bundle['package_sha256'],
            scope='editorial-trial'))
        contributed = self.construire_epreuve('version-contribuee', title='Contribution voisine',
                                              characteristics={'language': 'Langue contribuée'})
        contribution = self.public / 'contributions' / task / 'version-contribuee'
        contribution.mkdir(parents=True)
        (contribution / 'manifest.json').write_bytes(contributed['manifest'])
        for name, raw in contributed['files'].items():
            (contribution / name).write_bytes(raw)

        before_public = {str(path.relative_to(self.public / 'editorial-trials')): path.read_bytes()
                         for path in (self.public / 'editorial-trials').rglob('*') if path.is_file()}

        def suggest(wire):
            document = json.loads(wire)
            labels = [{'Préparer un tableau par opération': 'correspond',
                       'Rédiger une note de synthèse': 'partiel'}.get(
                           notice['work'], 'sans_correspondance')
                      for notice in document['state']['notices']]
            return {'status': 200, 'body': typesafe_document(wire, labels), 'complete': True}

        transport = self.enable_semantic(TypeSafeFixture(suggest), enabled=('epreuves',))
        page, path = self.editorial_help_submit(formulation)
        self.assertRegex(path, r'^/epreuves/aide/resultat/[0-9a-f]{32}$')
        self.assertNotIn(formulation, path)
        self.assertIn('Tableau des débits', page.visible)
        self.assertIn('Correspondance forte', page.visible)
        self.assertIn('Note de synthèse', page.visible)
        self.assertIn('Correspondance partielle', page.visible)
        self.assertIn('Cette appréciation ne prouve ni adéquation ni qualification', page.visible)
        self.assertIn('Tableau des débits', page.visible)
        self.assertNotIn('Aide indisponible', page.visible)
        self.assertNotIn('0.9', page.visible)
        self.assertNotIn('0.6', page.visible)
        self.assertNotRegex(page.visible, r'\b[0-9a-f]{32,64}\b')
        for absent in ('Épreuve retirée', 'Épreuve fictive', 'Épreuve altérée', 'Épreuve privée',
                       'Contribution voisine', 'Langue retirée', 'Langue fictive', 'Langue altérée',
                       'Langue privée', 'Langue contribuée', 'Langue invalide'):
            self.assertNotIn(absent, page.visible)
        self.assertEqual('/epreuves', page.link('Recherche simple'))
        self.assertEqual('/preparation', page.link('Décrire mon cas d’usage'))
        self.assertEqual([], page.forms)
        self.assertEqual('/epreuves/tache-fictive/version-calcul', page.link('Tableau des débits'))
        rendered = ' '.join(node['text'] for node in page.nodes if not node['hidden'])
        self.assertIn('epreuves', rendered)
        self.assertIn('epreuves-v1', rendered)
        self.assertIn('tache-fictive / version-calcul', rendered)

        self.assertEqual(1, len(transport.wires))
        document = json.loads(transport.wires[0])
        self.assertEqual({'model', 'state', 'questions'}, set(document))
        self.assertEqual('jev-1.13.0', document['model'])
        self.assertEqual(formulation, document['state']['formulation'])
        notices = document['state']['notices']
        self.assertEqual(3, len(notices))
        self.assertEqual({'Tableau des débits', 'Note de synthèse', 'Ancienne épreuve'},
                         {notice['name'] for notice in notices})
        for notice in notices:
            self.assertNotIn('id', notice)
            self.assertLessEqual(set(notice),
                                 {'ref', 'name', 'work', 'scope', 'limits', 'version'})
            for absent in ('category', 'expected_result', 'inputs', 'language', 'tools',
                           'difficulty', 'task_id', 'version_id', 'package_sha256',
                           'qualification_status', 'results_published', 'cases'):
                self.assertNotIn(absent, notice)
        calcul_notice = next(notice for notice in notices if notice['name'] == 'Tableau des débits')
        self.assertEqual('Tableau des débits', calcul_notice['scope'])
        self.assertEqual('Préparer un tableau par opération', calcul_notice['work'])
        self.assertEqual('Ne certifie aucune comptabilité', calcul_notice['limits'])
        self.assertEqual('version-calcul', calcul_notice['version'])
        wire = transport.wires[0]
        for absent in ('Épreuve retirée', 'Langue retirée', 'Épreuve fictive', 'Langue fictive',
                       'Épreuve altérée', 'Langue altérée', 'Épreuve privée', 'Langue privée',
                       'Contribution voisine', 'Langue contribuée', 'Langue invalide',
                       'version-invalide', 'version-fictive', 'version-privee', 'version-retiree',
                       'version-alteree', KEY, SECRET.hex(), 'benchmark_session',
                       AUTHORITY['authority_id'], ACTOR, 'Référence de jugement'):
            self.assertNotIn(absent, wire)

        request_id = path.rsplit('/', 1)[1]
        with closing(storage.Store(self.data)) as store:
            code, value = semantic_help.receipt(store, request_id)
        self.assertEqual(200, code)
        self.assertEqual(('epreuves', 'epreuves-v1'),
                         (value['receipt']['usage'], value['receipt']['questions_version']))
        self.assertNotIn(formulation, json.dumps(value))
        for stored in list(self.data.rglob('*')) + list(self.public.rglob('*')):
            if stored.is_file():
                self.assertNotIn(formulation.encode(), stored.read_bytes(), stored)
        # Aucune correspondance reste distincte d’une aide indisponible et ne force aucun choix
        none_transport = self.enable_semantic(TypeSafeFixture(), enabled=('epreuves',))
        none_page, none_path = self.editorial_help_submit('Un besoin sans rapport avec les épreuves')
        self.assertIn('Aucune épreuve ne correspond', none_page.visible)
        self.assertNotIn('Aide indisponible', none_page.visible)
        self.assertEqual('/epreuves', none_page.link('Recherche simple'))
        self.assertEqual('/preparation', none_page.link('Décrire mon cas d’usage'))
        self.assertEqual(1, len(none_transport.wires))
        self.assertRegex(none_path, r'^/epreuves/aide/resultat/[0-9a-f]{32}$')

        # La copie part de la suggestion exacte et conserve version et empreinte
        version_page = self.request(page.link('Tableau des débits'))[0]
        self.assertEqual('/preparation/copies/tache-fictive/version-calcul',
                         version_page.link('Créer mon dossier privé'))
        confirmation = self.request(version_page.link('Créer mon dossier privé'))[0]
        copy_form = next(item for item in confirmation.forms if '/copies/' in item['action'])
        with closing(storage.Store(self.data)) as store:
            before_operations = store._connection.execute(
                'SELECT count(*) FROM operations').fetchone()[0]
        _, copy_headers, _ = self.request(copy_form['action'], copy_form['fields'], status=303)
        dossier = copy_headers['Location']
        with closing(storage.Store(self.data)) as store:
            source = json.loads(store._connection.execute(
                'SELECT checks_json FROM s2_revisions WHERE dossier_id=? AND revision=1',
                (dossier.rsplit('/', 1)[1],)).fetchone()[0])['source']
            self.assertEqual(('tache-fictive', 'version-calcul'),
                             (source['task_id'], source['version_id']))
            self.assertEqual(calcul['package_sha256'],
                             sha256(source['manifest'].encode()).hexdigest())
            self.assertEqual(0, store._connection.execute(
                'SELECT count(*) FROM s2_validations').fetchone()[0])
            self.assertEqual(before_operations, store._connection.execute(
                'SELECT count(*) FROM operations').fetchone()[0])
        copied_page = self.request(dossier)[0]
        self.assertIn('Qualification absente', copied_page.visible)
        self.assertIn('Sa qualification publiée ne qualifie pas ce dossier', copied_page.visible)
        self.assertIn('version-calcul', copied_page.visible)
        need = copied_page.form('/messages')
        self.request(dossier + '/messages',
                     need['fields'] | {'message': 'Un besoin assez long pour être préparé'},
                     status=403)
        self.assertEqual([], self.calls)
        self.assertEqual([], self.access.verifications)

        # Retrait après soumission : affichage, ouverture et copie revérifient l’état
        calc_folder = self.public / 'editorial-trials' / task / 'version-calcul'
        calc_manifest = (calc_folder / 'manifest.json').read_bytes()
        calc_piece = (calc_folder / 'piece-consigne.txt').read_bytes()
        editorial.withdraw(self.public, task, 'version-calcul', dict(
            actor='Ayo', authority_id='EDITORIAL_TRIAL', package_sha256=calcul['package_sha256'],
            scope='editorial-withdrawal', task_id=task, version_id='version-calcul'))
        removed = self.request(path)[0]
        self.assertIn('Une suggestion a été retirée', removed.visible)
        self.assertNotIn('Tableau des débits', removed.visible)
        self.assertIn('Note de synthèse', removed.visible)
        self.assertNotIn('version-calcul', removed.visible)
        self.assertNotIn('/epreuves/tache-fictive/version-calcul',
                         [node['attrs'].get('href') for node in removed.nodes])
        opened = self.request('/epreuves/' + task + '/version-calcul')[0]
        self.assertIn('Cette version est retirée', opened.visible)
        self.assertNotIn('Tableau des débits', opened.visible)
        self.assertNotIn('Relève les opérations inventées', opened.visible)
        refused_copy = self.request('/preparation/copies/' + task + '/version-calcul', status=409)[0]
        self.assertIn('Cette version est retirée', refused_copy.visible)
        refused_post = self.request(
            '/preparation/copies/' + task + '/version-calcul',
            {'csrf_token': 'a' * 64, 'copy': '1'}, status=409)[0]
        self.assertIn('Cette version est retirée', refused_post.visible)
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(1, store._connection.execute(
                'SELECT count(*) FROM s2_dossiers').fetchone()[0])
        self.assertEqual(calc_manifest, (calc_folder / 'manifest.json').read_bytes())
        self.assertEqual(calc_piece, (calc_folder / 'piece-consigne.txt').read_bytes())
        after_public = {str(path.relative_to(self.public / 'editorial-trials')): path.read_bytes()
                        for path in (self.public / 'editorial-trials').rglob('*') if path.is_file()}
        self.assertEqual({key: raw for key, raw in before_public.items()
                          if not key.endswith('withdrawal.json')},
                         {key: raw for key, raw in after_public.items()
                          if not key.endswith('withdrawal.json')})
        self.assertIn('Cette version n’est plus disponible au catalogue',
                      self.request(dossier)[0].visible)
        self.assertEqual([], self.calls)

        # Protections HTTP et absence de texte recherché dans l’adresse et les pages
        form_page, headers, raw = self.request('/epreuves/aide')
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertEqual('nosniff', headers['X-Content-Type-Options'])
        self.assertEqual('no-referrer', headers['Referrer-Policy'])
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertIn("form-action 'self'", headers['Content-Security-Policy'])
        self.assertNotIn(b'<script', raw)
        self.assertIsNone(headers.get('Set-Cookie'))
        self.assertEqual('/epreuves/aide', form_page.form('/epreuves/aide')['action'])
        self.request('/epreuves/aide?q=prive', status=400)
        cross = self.request('/epreuves/aide',
                             {'submission_id': 'a' * 32, 'formulation': 'Formulation fictive'},
                             status=400,
                             extra_headers={'Origin': 'https://evil.example',
                                            'Sec-Fetch-Site': 'cross-site'})
        self.assertIn('incomplète', cross[0].visible)
        self.request('/epreuves/aide',
                     {'submission_id': 'a' * 32, 'formulation': 'Formulation fictive',
                      'inconnu': '1'}, status=400)
        wires = len(none_transport.wires)
        honeypot = self.request('/epreuves/aide',
                                {'submission_id': 'a' * 32, 'formulation': 'Formulation fictive',
                                 'website': 'robot'})[0]
        self.assertIn('Aucune épreuve ne correspond', honeypot.visible)
        self.assertEqual(wires, len(none_transport.wires))
        injection = '<script>alert(9)</script>'
        escaped_transport = self.enable_semantic(tariff=None, enabled=('epreuves',))
        escaped, _, escaped_raw = self.request(
            '/epreuves/aide',
            {'submission_id': 'b' * 32, 'formulation': injection}, status=200)
        self.assertIn(injection, escaped.visible)
        self.assertNotIn(injection.encode(), escaped_raw)
        self.assertIn(b'&lt;script&gt;', escaped_raw)
        self.assertEqual([], escaped_transport.wires)

    def test_aide_semantique_epreuves_budget_partage(self):
        """#467 : le catalogue utilise l’enveloppe commune et ses limites, sans budget séparé"""
        self.publier_epreuve('version-calcul', title='Tableau des débits',
                             work='Préparer un tableau par opération')

        def success(wire):
            return {'status': 200, 'body': typesafe_document(wire, ['correspond'],
                                                              input_tokens=2000),
                    'complete': True}

        transport = self.enable_semantic(TypeSafeFixture(success),
                                         enabled=('repertoire', 'epreuves'), tariff='450')
        page, path = self.editorial_help_submit('Lister les mouvements mensuels d’un compte fictif')
        self.assertIn('Correspondance forte', page.visible)
        self.assertEqual(1, len(transport.wires))
        with closing(storage.Store(self.data)) as store:
            state = semantic_help.inspect(store)
            self.assertEqual('epreuves', state['requests'][0]['usage'])
            self.assertEqual('0.9', state['budget']['spent'])
            self.assertEqual(1, len(state['requests']))
        references = json.loads((Path(__file__).parent / 'fixtures/external-references.json').read_text())
        with patch.object(views, 'BENCHMARK_REFERENCES', references):
            blocked, _ = self.help_submit('Corriger un programme fictif', status=200)
        self.assertIn('Aide indisponible', blocked.visible)
        self.assertIn('enveloppe commune', blocked.visible)
        self.assertEqual(1, len(transport.wires))
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(1, len(semantic_help.inspect(store)['requests']))

    def test_aide_semantique_epreuves_refus_sans_rejeu(self):
        """#467 : réponse invalide, panne et délai suivent les refus communs, sans suggestion tardive"""
        self.publier_epreuve('version-calcul', title='Tableau des débits',
                             work='Préparer un tableau par opération')
        formulation = 'Lister les mouvements mensuels d’un compte fictif'

        def mutated(change, *, status=200, complete=True):
            def handler(wire):
                document = json.loads(typesafe_document(wire))
                change(document)
                return {'status': status, 'body': json.dumps(document).encode(),
                        'complete': complete}
            return handler

        def failure(handler, expected, reason):
            transport = self.enable_semantic(TypeSafeFixture(handler), enabled=('epreuves',))
            page, path = self.editorial_help_submit(formulation)
            self.assertIn('Aide indisponible', page.visible)
            self.assertIn(expected, page.visible)
            self.assertNotIn('Correspondance forte', page.visible)
            request_id = path.rsplit('/', 1)[1]
            with closing(storage.Store(self.data)) as store:
                code, value = semantic_help.receipt(store, request_id)
            self.assertEqual((200, 'unavailable', reason),
                             (code, value['status'], value['reason']))
            self.assertEqual(1, len(transport.wires))
            again, again_path = self.editorial_help_submit(
                formulation, submission_id=request_id)
            self.assertEqual(path, again_path)
            self.assertEqual(1, len(transport.wires))
            self.assertIn('Aide indisponible', again.visible)

        failure(mutated(lambda value: value.update(model='jev-1.14.0')),
                'inexploitable', 'INVALID_RESPONSE')
        failure(lambda _: {'status': 529, 'body': b'', 'complete': True},
                'surchargé', 'OVERLOADED')

        def late(wire):
            time.sleep(0.4)
            return {'status': 200, 'body': typesafe_document(wire, ['correspond']),
                    'complete': True}

        transport = self.enable_semantic(TypeSafeFixture(late), enabled=('epreuves',))
        with patch.object(semantic_help, 'TIMEOUT_SECONDS', 0.2):
            page, path = self.editorial_help_submit(formulation)
            self.assertIn('Aide indisponible', page.visible)
            self.assertIn('délai de dix secondes', page.visible)
            self.assertNotIn('Correspondance forte', page.visible)
            self.assertEqual(1, len(transport.wires))
            time.sleep(0.5)
            late_page = self.request(path)[0]
            self.assertIn('délai de dix secondes', late_page.visible)
            self.assertNotIn('Correspondance forte', late_page.visible)
            request_id = path.rsplit('/', 1)[1]
            again, again_path = self.editorial_help_submit(
                formulation, submission_id=request_id)
            self.assertEqual(path, again_path)
            self.assertEqual(1, len(transport.wires))
        with closing(storage.Store(self.data)) as store:
            final = semantic_help.receipt(store, request_id)[1]
        self.assertEqual(('unavailable', 'TIMEOUT'), (final['status'], final['reason']))

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
        # L'aide longue du champ se replie sous « Conseils de saisie » ; son id reste la cible de aria-describedby
        self.assertIn('en 40 caractères au moins', page.depliant('Conseils de saisie'))
        self.assertNotIn('en 40 caractères au moins', page.visible)
        self.assertIn('request-help', next(n for n in page.nodes if n['attrs'].get('id') == 'request')['attrs']['aria-describedby'])
        self.assertTrue(any(n['attrs'].get('id') == 'request-help' and n['details'] for n in page.nodes))
        self.assertIn('Votre clé OpenRouter est enregistrée.', page.visible)
        self.assertFalse(any(n['attrs'].get('id') == 'availability' for n in page.nodes))
        fields = [n['attrs'].get('name') for n in page.form('/dossiers')['nodes'] if n['tag'] == 'textarea']
        self.assertEqual(['request', 'useful', 'context'], fields)
        page = self.submit(page, '/dossiers', {'request': 'Trop court'}, status=400)
        self.examine(page, '/preparation/dossiers', 'erreur de saisie', 'Corriger et renvoyer')
        self.assertIn('Trop court', page.visible)
        page = self.submit(page, '/dossiers', {'request': 'Transformer des notes de réunion sur le code et les mathématiques en une liste complète des actions à relire'})
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
        self.assertNotIn('Bench-X ne peut pas comparer cette tâche', page.visible)
        self.assertIn('cette vérification est payée avec votre clé OpenRouter', page.visible)
        position = {key: next(i for i, n in enumerate(page.nodes) if test(n)) for key, test in (
            ('correction', lambda n: n['tag'] == 'details' and n['attrs'].get('class') == 'corr'),
            ('validation', lambda n: n['attrs'].get('id') == 'validation'))}
        # R10 : la synthèse et le bouton précèdent le contenu, donc la correction repliée ; le consentement est vérifié dans tests/privacy_browser.test.cjs
        self.assertLess(position['validation'], position['correction'])
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
        # L'aide longue d'un réglage se replie sous le nom de ce réglage ; le groupe reste lié à elle
        self.assertIn('plus proche qu’il propose', page.depliant('Ce que change « Niveau de raisonnement demandé »'))
        self.assertIn('Ce que change « Essais par modèle et par exemple »', ' '.join(n['text'] for n in page.nodes if n['tag'] == 'summary'))
        self.assertNotIn('plus proche qu’il propose', page.visible)
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
        self.assertEqual(['Modèle', 'Niveau de raisonnement', 'Limite de sortie', 'Coût estimé'],
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
        synthesis = next(n['text'] for n in page.nodes if n['tag'] == 'ul' and 'meta' in n['attrs']['class'] and 'réponses estimées' in n['text'])
        self.assertRegex(synthesis, r'^2 modèles · réponses estimées : [0-9,]+ USD · réservé pour l’évaluation : '
                                    r'[0-9,]+ USD · crédit restant : 18,5 USD$')
        self.assertIn('Tout est prêt.', page.visible)
        self.assertNotIn('✓', page.visible)
        order = [next(i for i, n in enumerate(page.nodes) if test(n)) for test in (
            lambda n: n['tag'] == 'ul' and 'meta' in n['attrs']['class'] and 'réponses estimées' in n['text'],
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
        # Refus avant admission : aucune admission, aucun essai, aucune émission
        with closing(storage.Store(self.data)) as store:
            self.assertEqual((0, 0, 0), tuple(store._connection.execute(
                f'SELECT count(*) FROM {table}').fetchone()[0] for table in ('s4_admissions', 's4_attempts', 's4_emissions')))
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
        # #517 : l'admission répond après l'échéance du relais. L'attente expire, la campagne reste à consulter
        campaign_id = recap.split('/campaigns/', 1)[1].split('/', 1)[0]
        # Un portail par lancement retenu, ouverts l'un après l'autre : le premier admis n'est pas concurrencé
        admit, gates, entered, lock, callers, finished = (campaigns.launch, (threading.Event(), threading.Event()),
                                                          threading.Event(), threading.Lock(), [], queue.Queue())

        def late_launch(*args, **kwargs):
            with lock:
                index = len(callers)
                callers.append(threading.get_ident())
            entered.set()
            self.assertTrue(gates[index].wait(5), 'Admission jamais libérée')
            try:
                return admit(*args, **kwargs)
            finally:
                finished.put(index)
        start_form = page.form('/start')

        def counts():
            with closing(storage.Store(self.data)) as store:
                return tuple(store._connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                             for table in ('s4_admissions', 's4_attempts', 's4_emissions'))
        # #528 : chaque lecture du registre hors snapshot revalidait toutes les opérations, 0,6 s en production.
        # Comptées par fil : chaque requête de l'exécuteur a le sien
        validated = []
        validate = storage._operation
        with patch.object(service, 'RELAY_BUDGET_SECONDS', .3), patch.object(campaigns, 'launch', late_launch), \
                patch.object(storage, '_operation', lambda record: validated.append(threading.get_ident()) or validate(record)):
            waiting, _, _ = self.request(start_form['action'], start_form['fields'], status=504)
            self.assertTrue(entered.wait(5), 'Lancement jamais retenu')
            # #524 : entre l'expiration et l'admission tardive, le suivi se lit sans proposer de second lancement,
            # et un envoi direct n'admet rien
            interval, _, _ = self.request(recap)
            self.assertFalse(any(f['action'].endswith('/start') for f in interval.forms))
            self.assertIn('Impossible de lancer pour l’instant : un lancement de cette comparaison est déjà en cours '
                          'd’enregistrement ; actualisez la page dans un moment.', interval.visible)
            self.assertEqual((0, 0, 0), counts())
            again, _, _ = self.request(start_form['action'], start_form['fields'], status=504)
            self.assertEqual('Réponse non reçue à temps', next(n['text'] for n in again.nodes if n['tag'] == 'h1'))
            self.assertEqual(2, len(callers))
            gates[0].set()
            self.assertEqual(0, finished.get(timeout=5))
            attempts = self.starts.get(timeout=5)['candidate_attempts']
            # Le second lancement retenu reprend après l'admission du premier : il n'admet rien
            gates[1].set()
            self.assertEqual(1, finished.get(timeout=5))
        self.assertEqual((1, 2, 0), counts())
        with closing(storage.Store(self.data)) as store:
            registry = store._connection.execute('SELECT count(*) FROM operations').fetchone()[0]
        reads = validated.count(callers[0])
        self.assertLessEqual(reads, LAUNCH_REGISTRY_READS * registry, (reads, registry))
        self.examine(waiting, start_form['action'], 'attente expirée', 'Consulter l’état du lancement')
        self.assertEqual('Réponse non reçue à temps', next(n['text'] for n in waiting.nodes if n['tag'] == 'h1'))
        self.assertIn('rien n’indique qu’il a échoué', waiting.visible)
        for claim in ('n’a pas abouti', 'indisponible', 'Lancement enregistré', 'Aucun appel'):
            self.assertNotIn(claim, waiting.visible)
        self.assertEqual(recap, waiting.link('Consulter l’état du lancement'))
        self.assertEqual(2, len(attempts))

        def persisted():
            with closing(storage.Store(self.data)) as store:
                snapshot = campaigns.inspect(store, campaign_id)
                return ([a['admission_id'] for a in snapshot['admissions']],
                        sorted(a['operation_id'] for a in snapshot['attempts']),
                        sorted(o['operation_id'] for o in store._operations(store._connection)),
                        store._connection.execute('SELECT count(*) FROM s4_emissions').fetchone()[0])
        effects = persisted()
        self.assertEqual(1, len(effects[0]))
        self.assertEqual(sorted(attempts), effects[1])
        self.assertEqual(0, effects[3])
        # Consulter puis renvoyer le même lancement : ni émission, ni intention, ni seconde campagne
        for _ in range(2):
            consulted, _, _ = self.request(recap)
            self.assertEqual('Benchmark en cours', next(n['text'] for n in consulted.nodes if n['tag'] == 'h1'))
            self.assertFalse(any(f['action'].endswith('/start') for f in consulted.forms))
        # Lancement déjà admis : le formulaire de lancement initial n'a plus la forme d'un lancement à accepter
        self.request(start_form['action'], start_form['fields'], status=400)
        self.assertEqual(effects, persisted())
        self.assertTrue(self.starts.empty())
        self.assertEqual(3, len(self.calls))
        # Après un lancement, le choix des modèles repart de la sélection lancée, pas d'une plus ancienne
        chosen, _, _ = self.request(configurations)
        self.assertEqual({'openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'},
                         {n['attrs']['value'] for n in chosen.nodes if n['tag'] == 'input'
                          and n['attrs'].get('name') == 'models' and 'checked' in n['attrs']})
        self.assertIn('checked', next(n for n in chosen.nodes if n['tag'] == 'input' and n['attrs'].get('type') == 'radio'
                                      and n['attrs'].get('name') == 'tier' and n['attrs'].get('value') == 'high')['attrs'])
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
        '/', '/epreuves', '/mentions-legales', '/cgu', '/confidentialite',
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

    def rejouer_relances(self, dossier_id):
        """Exécute les seconds contrôles dus (#492, piste 3), comme le fait le fil de relance de l'exécuteur"""
        while True:
            with closing(storage.Store(self.data)) as store:
                due = prep.due_qualification_retries(store, dossier_id=dossier_id)
                following = prep.retry_qualification(store, due[0][0], self.bound[1], 'a' * 40) if due else None
            if following is None:
                return
            prep.execute_qualification(self.data, following, self.bound[1])

    def test_temoins_controles_qualifient_la_version_exacte(self):
        """Issue #445 : une alternative valable et un défaut ciblé, contrôlés sur les octets de chaque version

        Modes d'échec couverts :
        1. la vérification consomme sans estimation annoncée avant la validation ;
        2. une réussite sans preuve exploitable, un témoin contredit ou une réponse sans témoin qualifie ;
        3. la preuve d'une autre version qualifie la version courante, ou une correction garde la qualification ;
        4. les contrôles ne sont pas refaits sur les octets finalement soumis, ou les preuves antérieures disparaissent ;
        5. un témoin, sa justification ou la référence part chez les candidats ;
        6. le coût des contrôles sort de la vérification de l'exemple ou de l'autorité de préparation ;
        7. la page présente le résultat d'un témoin comme la preuve que l'attendu est juste
        """
        notes = ('Décision : le budget formation est validé.\n'
                 'Action : Camille relit le devis avant vendredi.\n')
        # La référence suit la consigne : l'échéance n'est exigée qu'une fois la consigne corrigée (#509)
        echeance = 'Garder l’échéance de chaque action.'
        reference = ('Attendus : l’action de Camille, relire le devis avant vendredi. '
                     'Toute formulation qui garde la personne et l’action est recevable.')
        reference_echeance = ('Attendus : l’action de Camille, relire le devis avant vendredi. '
                              'Toute formulation qui garde la personne, l’action et l’échéance avant vendredi '
                              'est recevable.')
        consigne = ['Relever les actions à mener dans les notes, avec leur responsable.']
        criteres = {'eliminatory': ['Ajouter une action absente des notes'],
                    'obligations': ['Relever chaque action avec son responsable'], 'quality': []}

        def preparer(operation, request):
            self.calls.append(('préparation', operation['operation_id']))
            value = response_for(operation)
            value['cost'].update(amount='0.10', currency='USD', source='Reçu simulé #445')
            result = value['receipt']['result']
            result['explanation'] = 'Cet exemple correspond-il bien à votre travail ?'
            result['package']['candidate'].update(instruction=' '.join(consigne), criteria=criteres,
                deliverables=['Liste des actions'], pieces=[{'name': 'notes.txt', 'content': notes}])
            result['package']['judgment']['pieces'] = [
                {'name': 'reference.txt', 'content': reference_echeance if echeance in consigne else reference}]
            return value
        self.assistants['transport'] = SessionAssistant(preparer, {'model': 'factice'})
        alternative = {'kind': 'alternative', 'output': 'Camille : relire le devis, avant vendredi.',
                       'expected': [{'control_id': 'O1', 'status': 'PASS'}, {'control_id': 'E1', 'status': 'PASS'}],
                       'justification': 'La réponse garde la personne, l’action et l’échéance avant vendredi ; '
                                        'ce tableau sans intitulé reste recevable.'}
        defaut = {'kind': 'defect', 'output': 'Budget formation validé ; aucune action relevée.',
                  'expected': [{'control_id': 'O1', 'status': 'FAIL'}],
                  'justification': 'La référence attend l’action de Camille : son omission est le défaut ciblé.'}
        self.qualifier.result = {'qualified': True, 'findings': [], 'summary': 'Les actions sont vérifiables',
                                 'witnesses': [alternative, defaut]}
        juge = self.qualifier.controller()
        page, _, _ = self.request('/')
        target = page.link('Décrire mon cas')
        page, _, _ = self.request(target)
        key_form = page.form('/access/key')
        self.request(key_form['action'], key_form['fields'] | {'key': KEY}, status=303)
        page, _, _ = self.request(target)
        page = self.submit(page, '/dossiers', {'request': 'Relever les actions à mener dans mes notes de réunion'})
        dossier = page.link('Actualiser')
        dossier_id = dossier.rsplit('/', 1)[1]
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(dossier)
        # 1. Avant toute consommation : la vérification et chaque témoin annoncent leur réserve, séparément
        validation = next(n['text'] for n in page.nodes if n['attrs'].get('id') == 'validation')
        self.assertIn('vérification de l’exemple : au plus 1 USD réservé', validation)
        self.assertIn('contrôle de chaque témoin, 4 au plus : au plus 0,5 USD réservé par témoin', validation)
        self.assertEqual([], juge.calls)
        versions = []

        def verifier(decisions):
            juge.decisions = decisions
            page, _, _ = self.request(dossier)
            self.submit(page, '/validation', {})
            prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
            self.rejouer_relances(dossier_id)
            page, _, raw = self.request(dossier)
            versions.append(re.search(r'version (\d+)', page.visible).group(1))
            return page, raw

        def corriger(ajout):
            consigne.append(ajout)
            page, _, _ = self.request(dossier)
            self.submit(page, '/messages', {'kind': 'correct', 'message': 'Préciser la consigne : ' + ajout})
            prep.execute(self.data, self.starts.get_nowait(), self.bound[0])

        def detail(page):
            return next(n['text'] for n in page.nodes if n['tag'] == 'details'
                        and 'Détail de la vérification de l’exemple' in n['text'])

        # 2. Réussite déclarée sans citation de la réponse témoin : non qualifiée
        page, _ = verifier({'alternative': {'O1': ('PASS', 'Passage absent de la réponse témoin')}})
        self.examine(page, dossier, 'témoin sans preuve exploitable', None)
        # #492, critère 4 : la réponse du juge, rejetée deux fois, n'est pas un verdict sur l'exemple
        self.assertIn('Vérification impossible pour le moment', page.visible)
        self.assertIn('restée inexploitable après une relance', page.visible)
        self.assertIn('Décision sans preuve exploitable', detail(page))
        self.assertFalse(any(n['tag'] == 'a' and n['text'] == 'Choisir les modèles' for n in page.nodes))
        self.request(dossier + '/configurations', status=403)
        # Le qualificateur fixe ses attentes sur les contrôles réellement appliqués
        self.assertEqual([{'id': 'O1', 'description': 'Relever chaque action avec son responsable'},
                          {'id': 'E1', 'description': 'Ajouter une action absente des notes'}],
                         self.qualifier.calls[-1][1]['outgoing']['controls'])
        # 2. Défaut non détecté par le contrôle : témoin contredit
        corriger('Une action sans responsable reste à relever.')
        page, _ = verifier({'defect': {'O1': ('PASS', defaut['output'])}})
        self.assertIn('Exemple à revoir avant comparaison', page.visible)
        self.assertIn('Décision contraire à l’attendu', detail(page))
        # 2. Réponse qui se dit qualifiée sans aucun témoin : inexploitable, aucun contrôle consommé
        corriger('Une action par ligne.')
        self.qualifier.auto_witnesses = False
        self.qualifier.result = {'qualified': True, 'findings': [], 'summary': 'Exemple qualifié sans témoin'}
        controles = len(juge.calls)
        page, _ = verifier({})
        self.assertIn('Exemple à revoir avant comparaison', page.visible)
        self.assertIn('Résultat de qualification reçu non utilisable', page.visible)
        self.assertEqual(controles, len(juge.calls))
        # Témoins décidés comme attendu, preuve à l'appui : version qualifiée, résultats liés à elle
        corriger(echeance)
        self.qualifier.result = {'qualified': True, 'findings': [], 'summary': 'Les actions sont vérifiables',
                                 'witnesses': [alternative, defaut]}
        page, raw = verifier({})
        # #509 : le contrôle reçoit la référence de sa version, sans l'échéance avant la correction, avec elle après
        def references_vues(appels):
            return {piece['content'] for _, request in appels for piece in request['outgoing']['references']}
        self.assertEqual({reference}, references_vues(juge.calls[:controles]))
        self.assertEqual({reference_echeance}, references_vues(juge.calls[controles:]))
        self.examine(page, dossier, 'témoins confirmés', 'Choisir les modèles')
        self.assertIn('Exemple vérifié, prêt à comparer', page.visible)
        verification = detail(page)
        self.assertEqual(2, verification.count('Décision conforme à l’attendu'))
        self.assertIn('Alternative valable', verification)
        self.assertIn('Défaut ciblé', verification)
        self.assertIn(alternative['justification'], verification)
        self.assertIn('Relever chaque action avec son responsable : attendu satisfait, décidé satisfait', verification)
        self.assertIn('Relever chaque action avec son responsable : attendu non satisfait, décidé non satisfait', verification)
        self.assertIn('Ajouter une action absente des notes : attendu faute absente, décidé faute absente', verification)
        self.assertIn('Contrôlé sur la version ' + versions[-1] + ' de l’exemple', verification)
        # 7. Ce qu'un témoin prouve, et ce qu'il ne prouve pas
        self.assertIn('Ce résultat dit ce que le contrôle a décidé sur ce témoin avec cette référence ; '
                      'il ne prouve pas que l’attendu est juste.', verification)
        # 3. Les preuves d'une version antérieure restent consultables et ne qualifient qu'elle-même
        ancienne, _, _ = self.request(dossier + '/revisions/' + versions[1])
        self.assertIn('Décision contraire à l’attendu', detail(ancienne))
        self.assertFalse(any(n['tag'] == 'a' and n['text'] == 'Choisir les modèles' for n in ancienne.nodes))
        # 3 et 4. Correction après qualification : nouvelle version non qualifiée, contrôles refaits sur ses octets
        corriger('Écrire le responsable avant l’action.')
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'version corrigée à valider', 'Oui, c’est le travail à tester')
        self.assertFalse(any(n['tag'] == 'a' and n['text'] == 'Choisir les modèles' for n in page.nodes))
        qualifiee, _, _ = self.request(dossier + '/revisions/' + versions[-1])
        self.assertEqual(2, detail(qualifiee).count('Décision conforme à l’attendu'))
        controles = len(juge.calls)
        page, _ = verifier({})
        self.assertIn('Exemple vérifié, prêt à comparer', page.visible)
        refaits = [request['outgoing'] for _, request in juge.calls[controles:]]
        self.assertEqual(2, len(refaits))
        with closing(storage.Store(self.data)) as store:
            row = store._connection.execute(
                'SELECT package_json FROM s2_revisions WHERE dossier_id=? AND revision=?',
                (dossier_id, int(versions[-1]))).fetchone()
            final = json.loads(row[0])
            for review in refaits:
                self.assertEqual(final['instruction'], review['task']['instruction'])
                self.assertEqual([piece['id'] for piece in final['pieces']],
                                 [piece['piece_id'] for piece in review['task']['pieces']])
                self.assertEqual(['reference.txt'], [piece['name'] for piece in review['references']])
            # 6. Contrôles payés sous l'autorité de préparation et rangés dans la vérification de l'exemple
            qualifications = [op for op in store._operations(store._connection)
                              if op['dossier_id'] == dossier_id and op['phase'] == 'qualification']
            controls = [op for op in qualifications if op['requested_configuration'].get('model') == 'juge/fictif']
            # 8 contrôles et le second contrôle de l'alternative rejetée à l'étape 2 (#492, piste 3)
            self.assertEqual(9, len(controls))
            self.assertEqual(1, len({(op['authority'], op['budget_id']) for op in qualifications}))
            self.assertTrue(all(op['reserved_amount'] == '0.5' and op['observed_cost']['amount'] == '0.05'
                                for op in controls))
            from benchmark import restitution
            groups = {group['key']: {item['operation_id'] for item in group['operations']}
                      for group in restitution._expenses(store, store._connection, dossier_id, int(versions[-1]),
                                                         [dossier_id + '-c0'], set(), set(), set(), 0, 'USD')['groups']}
            self.assertLessEqual({op['operation_id'] for op in controls}, groups['qualification'])
        # 5. Les candidats reçoivent le paquet seul : ni témoin, ni justification, ni référence
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
        brut = storage._strict_json(transmis)
        for reserve in (reference, reference_echeance, 'reference.txt', alternative['output'], defaut['output'],
                        alternative['justification'], defaut['justification']):
            self.assertNotIn(reserve, brut)
        artefacts = os.environ.get('BENCHX_E2E_ARTEFACTS')
        if artefacts:
            Path(artefacts).mkdir(parents=True, exist_ok=True)
            Path(artefacts, 'temoins-controles.json').write_text(json.dumps(
                {'versions': versions, 'controles_de_temoins': len(juge.calls),
                 'controles_refaits_sur_la_version_finale': len(refaits),
                 'temoins_absents_du_paquet_candidat': all(w['output'] not in brut for w in (alternative, defaut))},
                ensure_ascii=False, indent=2) + '\n')

    def test_deux_cas_d_une_meme_tache_qualifies_jusqu_a_leur_validation(self):
        """Issue #453 : un second cas reprend le contrat validé du premier, avec ses pièces, sa référence et sa qualification

        Modes d'échec couverts :
        1. un cas s'ajoute à une version non validée, ou depuis un cas qui n'est pas le premier ;
        2. le préparateur ne reçoit pas le contrat hérité, ou un paquet au contrat différent est accepté ;
        3. le second cas réutilise les pièces ou la référence du premier, ou se qualifie sur celles-ci ;
        4. la correction d'un cas retire la validation ou la qualification de l'autre ;
        5. la page présente plusieurs cas comme une preuve de représentativité, ou tait ce qui distingue un cas ;
        6. la page d'un cas seul affiche une liste de cas ;
        7. un refus de contrat ne dit pas sa cause, ou une correction ne rappelle pas le contrat hérité ;
        8. le premier cas change de contrat sans que les autres cas le signalent ;
        9. des livrables changés passent pour le même contrat
        """
        consigne = 'Relever les actions à mener dans les notes, avec leur responsable.'
        criteres = {'eliminatory': ['Ajouter une action absente des notes'],
                    'obligations': ['Relever chaque action avec son responsable'], 'quality': []}
        notes = {1: 'Action : Camille relit le devis avant vendredi.\n',
                 2: 'Action : envoyer le compte rendu, sans responsable désigné.\n'}
        references = {1: 'Attendus : l’action de Camille, relire le devis avant vendredi.',
                      2: 'Attendus : l’envoi du compte rendu, signalé sans responsable.'}
        messages, divergence = [], []

        def preparer(operation, request):
            self.calls.append(('préparation', operation['operation_id']))
            message = request['outgoing']['message']
            messages.append(message)
            cas = 2 if 'sans responsable' in message else 1
            value = response_for(operation)
            value['cost'].update(amount='0.10', currency='USD', source='Reçu simulé #453')
            result = value['receipt']['result']
            result['explanation'] = 'Cet exemple correspond-il bien à votre travail ?'
            result['package']['candidate'].update(
                instruction=consigne + (' Ajouter les dates.' if 'consigne' in divergence else ''), criteria=criteres,
                deliverables=['Liste des actions'] + (['Tableau des dates'] if 'livrables' in divergence else []),
                pieces=[{'name': 'notes.txt', 'content': notes[cas]}])
            result['package']['judgment']['pieces'] = [{'name': 'reference.txt', 'content': references[cas]}]
            return value
        self.assistants['transport'] = SessionAssistant(preparer, {'model': 'factice'})

        def ajouter(page, contexte):
            form = next(f for f in page.forms if f['action'] == '/preparation/dossiers' and 'case_of' in f['fields'])
            self.request(form['action'], form['fields'] | {'context': contexte}, status=303)
            prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
            return '/preparation/dossiers/' + form['fields']['dossier_id']

        def qualifier(dossier):
            page, _, _ = self.request(dossier)
            self.submit(page, '/validation', {})
            prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
            self.rejouer_relances(dossier.rsplit('/', 1)[1])
            return self.request(dossier)[0]

        def liste(page):
            return next(n['text'] for n in page.nodes if n['attrs'].get('id') == 'cas')

        page, _, _ = self.request('/')
        target = page.link('Décrire mon cas')
        page, _, _ = self.request(target)
        key_form = page.form('/access/key')
        self.request(key_form['action'], key_form['fields'] | {'key': KEY}, status=303)
        page, _, _ = self.request(target)
        page = self.submit(page, '/dossiers', {'request': 'Relever les actions à mener dans mes notes de réunion'})
        premier = page.link('Actualiser')
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(premier)
        # 1. Version non validée : aucun ajout proposé, et un envoi direct est refusé sans appel
        self.assertFalse(any('case_of' in f['fields'] for f in page.forms))
        appels = len(messages)
        creation = self.request('/preparation')[0].form('/preparation/dossiers')
        self.request(creation['action'], {'csrf_token': creation['fields']['csrf_token'], 'dossier_id': 'b' * 32,
                                          'action_id': 'c' * 32, 'case_of': premier.rsplit('/', 1)[1],
                                          'context': 'Une action sans responsable'}, status=403)
        self.assertEqual(appels, len(messages))
        page = qualifier(premier)
        self.examine(page, premier, 'premier cas qualifié', 'Choisir les modèles')
        # 6. Un seul cas : le parcours est inchangé, sans liste de cas
        self.assertFalse(any(n['attrs'].get('id') == 'cas' for n in page.nodes))
        # 2. Contrat différent : le paquet est refusé, la préparation du nouveau cas est suspendue
        divergence.append('consigne')
        refuse = ajouter(page, 'Une action sans responsable, pour vérifier qu’elle est signalée')
        self.assertIn(consigne, messages[-1])
        self.assertIn('Relever chaque action avec son responsable', messages[-1])
        page, _, _ = self.request(refuse)
        self.assertIn('Préparation arrêtée', page.visible)
        # 7. La cause du refus est dite
        self.assertIn('communs aux exemples de la tâche', page.visible)
        refus_explique = 'Résultat refusé' in page.visible
        self.assertFalse(any(n['attrs'].get('id') == 'validation' for n in page.nodes))
        # 9. Mêmes consigne et critères, livrables changés : le contrat diffère, le paquet est refusé
        divergence[:] = ['livrables']
        page, _, _ = self.request(premier)
        page, _, _ = self.request(ajouter(page, 'Une action sans responsable, pour vérifier qu’elle est signalée'))
        self.assertIn('communs aux exemples de la tâche', page.visible)
        divergence.clear()
        page, _, _ = self.request(premier)
        second = ajouter(page, 'Une action sans responsable, pour vérifier qu’elle est signalée')
        page, _, raw = self.request(second)
        self.examine(page, second, 'second cas prêt', 'Oui, c’est le travail à tester')
        self.assertIn(consigne, page.visible)
        # Pièces repliées dans leur dépliant : le contenu se lit dans la page servie
        self.assertIn('sans responsable désigné', raw.decode())
        self.assertNotIn('Camille relit le devis', raw.decode())
        # 1. Un cas s'ajoute seulement depuis le premier cas de la tâche
        self.assertFalse(any('case_of' in f['fields'] for f in page.forms))
        page = qualifier(second)
        self.examine(page, second, 'second cas qualifié', 'Choisir les modèles')
        # 3. La vérification du second cas lit sa propre référence, jamais celle du premier
        verification = storage._strict_json(self.qualifier.calls[-1][1])
        self.assertIn(references[2], verification)
        self.assertNotIn(references[1], verification)
        with closing(storage.Store(self.data)) as store:
            juges = {dossier: [row[0] for row in store._connection.execute(
                "SELECT sha256 FROM pieces WHERE dossier_id=? AND role='judge' ORDER BY revision",
                (dossier.rsplit('/', 1)[1],))] for dossier in (premier, second)}
        self.assertFalse(set(juges[premier]) & set(juges[second]))
        # 5. Chaque cas liste les autres ; la diversité n'est pas présentée comme représentative
        for dossier in (premier, second):
            page, _, _ = self.request(dossier)
            cas = liste(page)
            self.assertEqual(2, cas.count('exemple vérifié'))
            self.assertIn('préparation arrêtée', cas)
            self.assertIn('ne prouvent pas qu’ils représentent votre travail', cas)
            self.assertIn('Une action sans responsable, pour vérifier qu’elle est signalée', cas)
        # 4. Corriger le second cas retire seulement sa validation ; le premier reste prêt à comparer
        page, _, _ = self.request(second)
        self.submit(page, '/messages', {'kind': 'correct', 'message': 'Ajouter une seconde action sans responsable'})
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        # 7. La correction rappelle au préparateur le contrat hérité
        self.assertIn(consigne, messages[-1])
        page, _, _ = self.request(second)
        self.examine(page, second, 'second cas corrigé', 'Oui, c’est le travail à tester')
        self.assertIn(consigne, page.visible)
        page, _, _ = self.request(premier)
        self.examine(page, premier, 'premier cas inchangé', 'Choisir les modèles')
        self.assertEqual(1, liste(page).count('exemple vérifié'))
        premier_reste_qualifie = 'Exemple vérifié, prêt à comparer' in page.visible
        # 8. Le premier cas change de consigne : le second cas le signale, la page ne dit plus « même consigne »
        divergence.append('consigne')
        self.submit(page, '/messages', {'kind': 'correct', 'message': 'Demander aussi les dates'})
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(second)
        cas = liste(page)
        self.assertIn('contrat d’une ancienne version de l’exemple 1', cas)
        self.assertNotIn('Tous les exemples suivent la même consigne', cas)
        cas_crees = len(re.findall(r'Exemple \d', cas))
        artefacts = os.environ.get('BENCHX_E2E_ARTEFACTS')
        if artefacts:
            Path(artefacts).mkdir(parents=True, exist_ok=True)
            Path(artefacts, 'deux-cas.json').write_text(json.dumps(
                {'cas_crees': cas_crees, 'cas_qualifies_avant_corrections': 2,
                 'contrat_divergent_refuse_avec_sa_cause': refus_explique,
                 'references_partagees': len(set(juges[premier]) & set(juges[second])),
                 'premier_cas_reste_qualifie_apres_correction_du_second': premier_reste_qualifie,
                 'changement_du_premier_cas_signale': 'ancienne version de l’exemple 1' in cas},
                ensure_ascii=False, indent=2) + '\n')

    NOTES_454 = {1: 'Action : Camille relit le devis avant vendredi.\n',
                 2: 'Action : envoyer le compte rendu aux participants, sans responsable désigné.\n',
                 3: 'Action : Inès réserve la salle.\n'}
    REFERENCES_454 = {1: 'Attendus : l’action de Camille, relire le devis avant vendredi.',
                      2: 'Attendus : l’envoi du compte rendu, signalé sans responsable.',
                      3: 'Attendus : la réservation de la salle par Inès.'}

    def deux_exemples_454(self):
        """Clé, premier exemple et second exemple de la même tâche, chacun avec ses notes et sa référence, vérifiés

        Rend le premier et le second exemple, et `ajouter(page, contexte)` qui ajoute un autre exemple sans le valider
        """
        consigne = 'Relever les actions à mener dans les notes, avec leur responsable.'
        criteres = {'eliminatory': ['Ajouter une action absente des notes'],
                    'obligations': ['Relever chaque action avec son responsable'], 'quality': []}
        notes, references = self.NOTES_454, self.REFERENCES_454

        def preparer(operation, request):
            self.calls.append(('préparation', operation['operation_id']))
            message = request['outgoing']['message']
            cas = 3 if 'salle' in message else 2 if 'sans responsable' in message else 1
            value = response_for(operation)
            value['cost'].update(amount='0.10', currency='USD', source='Reçu simulé #454')
            result = value['receipt']['result']
            result['explanation'] = 'Cet exemple correspond-il bien à votre travail ?'
            result['package']['candidate'].update(instruction=consigne, criteria=criteres,
                                                  deliverables=['Liste des actions'],
                                                  pieces=[{'name': 'notes.txt', 'content': notes[cas]}])
            result['package']['judgment']['pieces'] = [{'name': 'reference.txt', 'content': references[cas]}]
            return value
        self.assistants['transport'] = SessionAssistant(preparer, {'model': 'factice'})

        def ajouter(page, contexte):
            form = next(f for f in page.forms if f['action'] == '/preparation/dossiers' and 'case_of' in f['fields'])
            self.request(form['action'], form['fields'] | {'context': contexte}, status=303)
            prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
            return '/preparation/dossiers/' + form['fields']['dossier_id']
        page, _, _ = self.request('/')
        target = page.link('Décrire mon cas')
        page, _, _ = self.request(target)
        key_form = page.form('/access/key')
        self.request(key_form['action'], key_form['fields'] | {'key': KEY}, status=303)
        page, _, _ = self.request(target)
        page = self.submit(page, '/dossiers', {'request': 'Relever les actions à mener dans mes notes de réunion'})
        premier = page.link('Actualiser')
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page = self.qualifier_454(premier)
        second = ajouter(page, 'Une action sans responsable, pour vérifier qu’elle est signalée')
        self.qualifier_454(second)
        return premier, second, ajouter

    def qualifier_454(self, dossier):
        page, _, _ = self.request(dossier)
        self.submit(page, '/validation', {})
        prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
        return self.request(dossier)[0]

    def test_plusieurs_exemples_lances_puis_lus_par_exemple(self):
        """Issue #454 : le lancement confirmé exécute une cellule par exemple et par modèle, puis restitue par exemple

        Modes d'échec couverts :
        1. le récapitulatif tait les exemples, les essais ou leur estimation, ou compte un exemple non vérifié ;
        2. un essai reçoit les pièces d'un autre exemple, ou son jugement lit la référence d'un autre exemple ;
        3. sortie, verdict, coût ou incident rattachés au mauvais exemple ou au mauvais modèle ;
        4. un arrêt partiel perd la couverture prévue ou observée, ou une reprise rejoue un essai reçu ou ambigu ;
        5. un conseil économique agrège plusieurs exemples, ou les résultats taisent que le juge n'est pas qualifié
        """
        from benchmark import automatic_judgment as auto, runtime
        notes, references = self.NOTES_454, self.REFERENCES_454
        qualifier = self.qualifier_454

        def cas_de(texte):
            return next(numero for numero, contenu in notes.items() if contenu.strip() in texte)

        # Le juge satisfait une réponse qui reprend l'action et son responsable, sinon il la fait échouer
        def ajuste(contenu, constats):
            satisfait = 'Responsable' in contenu['output']['content']
            return [dict(constat, status='PASS' if satisfait else 'FAIL',
                         finding='Action relevée' if satisfait else 'Action omise') for constat in constats]
        juge = self.juge_factice(ajuste=ajuste)
        premier, second, ajouter = self.deux_exemples_454()
        # Un troisième exemple préparé mais jamais validé n'entre pas dans la comparaison
        page, _, _ = self.request(premier)
        ajouter(page, 'Une salle à réserver, exemple laissé sans validation')
        page, _, _ = self.request(premier + '/configurations')
        page = self.submit(page, '/configurations', {
            'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash', 'mistralai/mistral-small-2603'],
            'tier': 'low'})
        recap = premier + '/campaigns/' + premier.rsplit('/', 1)[1] + '-c1/conditions'
        # 1. Panel, exemples, réglages, essais et leur estimation, avant toute confirmation
        self.examine(page, recap, 'récapitulatif de deux exemples', 'Lancer le benchmark')
        synthese = next(n['text'] for n in page.nodes if n['tag'] == 'ul' and 'meta' in n['attrs']['class'] and 'réponses estimées' in n['text'])
        self.assertRegex(synthese, r'^3 modèles · 2 exemples · 6 essais · réponses estimées : [0-9,]+ USD · '
                                   r'réservé pour l’évaluation : [0-9,]+ USD · crédit restant : [0-9,]+ USD$')
        exemples = next(n['text'] for n in page.nodes if n['attrs'].get('id') == 'exemples')
        self.assertIn('Exemple 1', exemples)
        self.assertIn('Exemple 2 : Une action sans responsable, pour vérifier qu’elle est signalée', exemples)
        self.assertIn('Exemple 3 : Une salle à réserver, exemple laissé sans validation', exemples)
        self.assertIn('pas inclus', exemples)
        essais = next(n for n in page.nodes if n['tag'] == 'table' and n['attrs'].get('aria-label') == 'Essais prévus')
        lignes = [n['text'] for n in page.nodes if n['tag'] == 'tr' and n['text'] in essais['text']][1:]
        self.assertEqual(6, len(lignes))
        self.assertEqual(3, sum(ligne.startswith('Exemple 1') for ligne in lignes))
        self.assertEqual(3, sum(ligne.startswith('Exemple 2') for ligne in lignes))
        self.assertTrue(all('USD' in ligne for ligne in lignes))
        self.assertTrue(any(n['tag'] == 'details' and 'Détail des critères et des réglages' in n['text'] for n in page.nodes))
        with closing(storage.Store(self.data)) as store:
            manifeste = campaigns.inspect(store, recap.split('/')[-2])['manifest']
        # Chaque essai porte l'estimation de son exemple ; celle d'un modèle est la somme de ses essais
        entrees = {cell['case_id']: cell['estimate']['assumptions']['input_tokens'] for cell in manifeste['plan']}
        self.assertEqual(2, len(set(entrees.values())))
        for configuration in manifeste['panel']:
            montants = [storage._money(cell['estimate']['amount_usd']) for cell in manifeste['plan']
                        if cell['configuration_id'] == configuration['id']]
            self.assertEqual(storage._money(configuration['estimate']['amount_usd']), sum(montants))
        # 1. Un exemple corrigé après ce choix ferme le lancement, sans appel ; un nouveau choix le reprend
        page, _, _ = self.request(second)
        self.submit(page, '/messages', {'kind': 'correct', 'message': 'Préciser que l’action sans responsable vise les participants'})
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(recap)
        self.assertFalse(any(f['action'].endswith('/start') for f in page.forms))
        self.assertIn('Un autre exemple a changé depuis ce choix', page.visible)
        self.assertEqual(premier + '/configurations', page.link('Choisir de nouveau les modèles'))
        refuse, _, _ = self.request(recap.removesuffix('/conditions') + '/start', {
            'csrf_token': next(f['fields']['csrf_token'] for f in self.request(premier)[0].forms if 'csrf_token' in f['fields']),
            'manifest_version': manifeste['version'], 'frozen_at': manifeste['conditions']['frozen_at'],
            'confirm': 'yes'}, status=403)
        self.assertIn('Un autre exemple a changé depuis ce choix', refuse.visible)
        self.assertTrue(self.starts.empty())
        # 1. Un choix fait pendant que l'exemple 2 attend sa validation ne compte que l'exemple 1, et le dit
        page, _, _ = self.request(premier + '/configurations')
        seul = self.submit(page, '/configurations', {'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'],
                                                     'tier': 'low'})
        exemples = next(n['text'] for n in seul.nodes if n['attrs'].get('id') == 'exemples')
        self.assertIn('Exemple 2 : Une action sans responsable, pour vérifier qu’elle est signalée · pas inclus : pas encore validé', exemples)
        self.assertNotIn('exemples · ', next(n['text'] for n in seul.nodes if n['tag'] == 'ul' and 'meta' in n['attrs']['class'] and 'réponses estimées' in n['text']))
        qualifier(second)
        page, _, _ = self.request(premier + '/configurations')
        page = self.submit(page, '/configurations', {
            'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash', 'mistralai/mistral-small-2603'],
            'tier': 'low'})
        recap = recap.replace('-c1/', '-c3/')
        self.examine(page, recap, 'nouveau récapitulatif de deux exemples', 'Lancer le benchmark')
        with closing(storage.Store(self.data)) as store:
            manifeste = campaigns.inspect(store, recap.split('/')[-2])['manifest']
        self.submit(page, '/start', {}, status=303)
        attempts = self.starts.get_nowait()['candidate_attempts']
        self.assertEqual(6, len(attempts))
        recus = []

        def candidat(operation, request):
            # Seules les pièces réellement envoyées disent à quel exemple l'essai répond
            cas = cas_de(request['outgoing']['pieces'][0]['content'])
            modele = request['requested_configuration']['model']
            recus.append((cas, modele))
            if (cas, modele) == (2, 'deepseek/deepseek-v4.1-flash'):
                raise RuntimeError('Réponse perdue après émission')
            value = response(operation, request)
            omise = modele == 'deepseek/deepseek-v4.1-flash'
            value['receipt']['result']['output'] = ('Action omise' if omise else
                                                    'Action : ' + notes[cas].strip() + ' | Responsable : relevé')
            value['cost'].update(status='KNOWN', amount=f'0.{cas}{1 + omise}', currency='USD', source='Reçu candidat #454')
            return value
        execution.execute_launch(self.data, attempts, candidat, access_secret=SECRET, access_transport=self.access)
        # 2. Chaque essai a reçu les pièces de son propre exemple, dans l'ordre du plan
        modeles = ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash', 'mistralai/mistral-small-2603']
        self.assertEqual([(1, m) for m in modeles] + [(2, m) for m in modeles[:2]], recus)
        # 4. Un effet ambigu arrête la comparaison ; un redémarrage ne rejoue ni l'ambigu ni ce qui est reçu
        with closing(storage.Store(self.data)) as store:
            runtime.stop(self.data, store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        execution.execute_launch(self.data, attempts, candidat, access_secret=SECRET, access_transport=self.access)
        self.assertEqual(5, len(recus))
        with closing(storage.Store(self.data)) as store:
            etats = {cell['cell_id']: cell['state'] for cell in campaigns.inspect(store, recap.split('/')[-2])['cells']}
        # L'essai qui suit l'ambigu garde son intention : il ne partira plus
        self.assertEqual(['RECEIVED'] * 4 + ['AMBIGUOUS', 'INTENT_RECORDED'],
                         [etats[cell['cell_id']] for cell in manifeste['plan']])
        # Couverture de l'arrêt : chaque essai garde son état, sous son exemple, sans lancement possible
        suivi, _, _ = self.request(recap)
        self.assertFalse(any(f['action'].endswith('/start') for f in suivi.forms))
        self.assertEqual(4, suivi.visible.count('réponse reçue'))
        # Quinze minutes après l'envoi, l'ambigu est clos sans relance ; l'évaluation s'ouvre alors
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(1, len(store.close_expired_ambiguous(datetime.now(timezone.utc) + timedelta(minutes=16))))
        execution.execute_launch(self.data, attempts, candidat, access_secret=SECRET, access_transport=self.access)
        self.assertEqual(5, len(recus))
        # Issue #458 : avant toute évaluation, la comparaison dit déjà pourquoi elle ne conseille pas
        avant, _, _ = self.request(recap.removesuffix('/conditions'))
        self.assertIn('Exemple 1 : toutes les réponses prévues ne sont pas évaluées.', avant.visible)
        # 4. L'essai jamais parti ne retient pas l'évaluation des réponses déjà reçues
        page, _, _ = self.request(recap)
        self.submit(page, '/evaluate', {})
        ids = self.starts.get_nowait()['judgment_operations']
        self.assertEqual(4, len(ids))
        auto.execute_campaign(self.data, ids, self.bound[2])
        self.assertEqual(5, len(recus))
        # 2. Le juge de chaque réponse lit les pièces et la référence de son exemple, jamais celles d'un autre
        self.assertEqual(4, juge.request.call_count)
        jugés = []
        for vue in self.judged:
            cas = cas_de(vue['task']['pieces'][0]['content'])
            if 'Responsable' in vue['output']['content']:
                self.assertEqual(cas, cas_de(vue['output']['content']))
            texte = storage._strict_json(vue['references'])
            self.assertIn(references[cas], texte)
            self.assertFalse(any(references[autre] in texte for autre in references if autre != cas))
            jugés.append(cas)
        self.assertEqual([1, 1, 1, 2], sorted(jugés))
        comparison = recap.removesuffix('/conditions')
        results, _, raw = self.request(comparison)
        self.examine(results, comparison, 'résultats de deux exemples', None)
        # 3. Chaque verdict, coût et incident sous son exemple et son modèle
        sections = raw.decode().split('<h3>Exemple ')
        self.assertEqual(3, len(sections))
        premier_tableau, second_tableau = (Page(part.encode()).visible for part in sections[1:])
        self.assertIn('Modèle A', premier_tableau)
        self.assertIn('0,11 USD', premier_tableau)
        self.assertIn('0,12 USD', premier_tableau)
        self.assertIn('0,21 USD', second_tableau)
        self.assertNotIn('0,12 USD', second_tableau)
        _, _, raw = self.request(comparison, json_response=True)
        lignes = {(row['case_id'], row['requested_configuration']['model']): row
                  for row in json.loads(raw)['rows']}
        self.assertEqual({('case-1', m) for m in modeles} | {('case-2', modeles[0])}, set(lignes))
        self.assertEqual('SATISFAIT', lignes[('case-2', 'openai/gpt-5.6-sol')]['verdict'])
        # Le détail d'une réponse de l'exemple 2 montre ses pièces et sa réponse, jamais celles de l'exemple 1
        detail_href = Page(sections[2].encode()).link('Détail et preuves')
        detail, _, raw = self.request(detail_href)
        self.examine(detail, detail_href, 'détail d’une réponse de l’exemple 2', None)
        self.assertIn(notes[2].strip(), raw.decode())
        self.assertNotIn(notes[1].strip(), raw.decode())
        # Issue #456 : le détail nomme l'exemple de la réponse, même ouvert dans la modale
        self.assertIn('Exemple 2', next(n['text'] for n in detail.nodes if n['attrs'].get('id') == 'attempt-detail'))
        self.assertEqual('NE SATISFAIT PAS', lignes[('case-1', 'deepseek/deepseek-v4.1-flash')]['verdict'])
        # Issue #456 : un second exemple n'est pas un essai, et son coût se classe sur son seul exemple
        self.assertNotRegex(results.visible, r'Essai \d sur')
        self.assertFalse(any('repetition' in row for row in lignes.values()))
        self.assertEqual(1, lignes[('case-2', 'openai/gpt-5.6-sol')]['cost']['rank'])
        # 4. Couverture prévue et observée : la réponse perdue et l'essai jamais parti restent visibles
        self.assertIn('Exemple 2 : Aucune réponse exploitable pour Modèle B', results.visible)
        self.assertNotIn('Modèle C', second_tableau)
        self.assertRegex(results.visible, r'essais lancés : 5 sur 6')
        self.assertIn('Exemple 2 : Modèle C n’a pas été appelé : la comparaison s’est arrêtée avant cet essai.', results.visible)
        self.assertNotIn('doit être relue', results.visible)
        # 5. Aucun conseil sur plusieurs exemples ; la limite du juge est dite avec les verdicts
        self.assertNotIn('Notre conseil', results.visible)
        # Issue #458 : l'absence de conseil nomme l'exemple incomplet
        self.assertIn('Exemple 2 : toutes les réponses prévues ne sont pas évaluées.', results.visible)
        self.assertIn('Le juge automatique n’est pas qualifié', results.visible)
        self.sans_promesse(results)
        # Issue #456 : un filtre garde chaque exemple, son dénominateur, ses essais manquants et l'échelle de ses coûts
        sous_filtre, echelles = {}, {}
        for filtre in ('', 'verdict=NE+SATISFAIT+PAS', 'configuration=configuration-1'):
            page, _, raw = self.request(comparison + ('?' + filtre if filtre else ''))
            # Modèle A coûte 0,11 USD sur l'exemple 1, dont la réponse la plus chère coûte 0,12 USD
            echelles[filtre] = '<rect width="92%"' in raw.decode().split('<h3>Exemple ')[1]
            parties = [Page(part.encode()).visible for part in raw.decode().split('<h3>Exemple ')]
            self.assertEqual(3, len(parties))
            self.assertIn('3 réponses évaluées sur 3 essais prévus', parties[1])
            self.assertIn('1 réponse évaluée sur 3 essais prévus', parties[2])
            self.assertIn('Exemple 2 : Modèle C n’a pas été appelé', page.visible)
            self.assertRegex(page.visible, r'essais lancés : 5 sur 6')
            self.assertNotIn('Notre conseil', page.visible)
            self.assertIn('Exemple 2 : toutes les réponses prévues ne sont pas évaluées.', page.visible)
            self.assertIn('Le juge automatique n’est pas qualifié', page.visible)
            sous_filtre[filtre] = [(part.count('Détail et preuves'),
                                    'Aucune réponse de cet exemple ne correspond à ces filtres.' in part)
                                   for part in parties[1:]]
        self.assertEqual([(3, False), (1, False)], sous_filtre[''])
        self.assertEqual([(1, False), (0, True)], sous_filtre['verdict=NE+SATISFAIT+PAS'])
        self.assertEqual([(1, False), (1, False)], sous_filtre['configuration=configuration-1'])
        self.assertTrue(echelles[''] and echelles['configuration=configuration-1'])
        # Choisir un exemple n'affiche que lui
        _, _, raw = self.request(comparison + '?case=case-2')
        self.assertEqual(2, len(raw.decode().split('<h3>Exemple ')))
        artefacts = os.environ.get('BENCHX_E2E_ARTEFACTS')
        if artefacts:
            Path(artefacts).mkdir(parents=True, exist_ok=True)
            Path(artefacts, 'plusieurs-exemples.json').write_text(json.dumps(
                {'essais_prevus': len(manifeste['plan']), 'exemples_inclus': len(manifeste['cases']),
                 'essais_envoyes_par_exemple': {str(cas): sum(c == cas for c, _ in recus) for cas in (1, 2)},
                 'etats_apres_arret': [etats[cell['cell_id']] for cell in manifeste['plan']],
                 'appels_candidats_apres_redemarrage': len(recus) - 5,
                 'jugements': juge.request.call_count,
                 'references_etrangeres_lues_par_le_juge': 0,
                 'conseil_affiche': 'Notre conseil' in results.visible,
                 'raison_sans_conseil': json.loads(self.request(comparison, json_response=True)[2])['abstention'],
                 'reponses_affichees_par_exemple_sous_filtre': {filtre: [n for n, _ in comptes]
                                                                for filtre, comptes in sous_filtre.items()}},
                ensure_ascii=False, indent=2) + '\n')

    def test_reprise_pour_longueur_sur_son_propre_exemple(self):
        """Issue #454 : un essai coupé pour longueur est repris avec les pièces de son exemple

        Modes d'échec couverts :
        1. la reprise d'un essai de l'exemple 2 envoie les pièces de l'exemple 1, ou l'inverse ;
        2. une reprise part plus d'une fois pour un même essai, ou n'est pas reprise sur son exemple ;
        3. la réponse reprise est jugée ou affichée sous un autre exemple
        """
        self.juge_factice()

        def comportement(value, model, limit, suivi):
            if model == 'mistralai/mistral-small-2603' and limit == 4096:
                self.coupee(value, 'Action : relire', limit)
        premier, recap, envois, _ = self.lancer_avec_reprises(
            ['openai/gpt-5.6-sol', 'mistralai/mistral-small-2603'], comportement,
            qualifier=lambda: self.deux_exemples_454()[0])
        self.assertIn('2 exemples · 4 essais', recap.visible)
        tour = [('openai/gpt-5.6-sol', 4096), ('mistralai/mistral-small-2603', 4096), ('mistralai/mistral-small-2603', 8192)]
        self.assertEqual(tour + tour, envois)
        with closing(storage.Store(self.data)) as store:
            reprises = [json.loads(row[0]) for row in store._connection.execute(
                'SELECT a.request_json FROM s4_attempts a JOIN s4_campaigns c USING(campaign_id) '
                "WHERE json_extract(c.manifest_json, '$.recovery_of') IS NOT NULL ORDER BY a.rowid")]
        # 1. et 2. Une reprise par essai coupé, chacune avec les pièces de son exemple
        self.assertEqual([('case-1', self.NOTES_454[1]), ('case-2', self.NOTES_454[2])],
                         [(r['case_id'], r['pieces'][0]['content']) for r in reprises])
        raw = self.evaluer(premier)
        # 3. Chaque exemple garde ses deux modèles, la reprise comprise, sous son propre titre
        sections = raw.split('<h3>Exemple ')
        self.assertEqual(3, len(sections))
        for section in sections[1:]:
            self.assertIn('Modèle A', Page(section.encode()).visible)
            self.assertIn('Modèle C', Page(section.encode()).visible)

    def test_essais_repetes_annonces_puis_tous_visibles(self):
        """Issue #455 : le demandeur choisit 1 à 3 essais par modèle, tous annoncés, tous lancés, tous restitués

        Modes d'échec couverts :
        1. un nombre d'essais hors de 1 à 3 est accepté, ou le récapitulatif tait le nombre, les essais ou leur estimation ;
        2. le nombre d'essais lancés dépend du résultat des premiers, ou un essai défavorable disparaît des résultats ;
        3. un arrêt perd le plan ou les observations, ou un redémarrage rejoue un essai reçu ou ambigu ;
        4. deux essais du même modèle se confondent dans le suivi, les résultats ou les essais manquants ;
        5. un conseil ou un taux de fiabilité est déduit de plusieurs essais
        """
        from benchmark import automatic_judgment as auto, runtime
        modeles = ['openai/gpt-5.6-sol', 'mistralai/mistral-small-2603']
        # Le juge satisfait une réponse qui nomme le responsable de l'action
        juge = self.juge_factice(ajuste=lambda contenu, constats: [
            dict(constat, status='PASS' if 'Responsable' in contenu['output']['content'] else 'FAIL')
            for constat in constats])
        dossier = self.exemple_qualifie()
        page, _, _ = self.request(dossier + '/configurations')
        # 1. Hors de 1 à 3 : refusé, sans comparaison créée
        for refuse in ('0', '4', 'deux'):
            self.request(dossier + '/configurations', page.form(dossier + '/configurations')['fields']
                         | {'models': modeles, 'tier': 'low', 'repetitions': refuse}, status=400)
        recap = self.submit(page, '/configurations', {'models': modeles, 'tier': 'low', 'repetitions': '3'})
        suivi = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions'
        self.examine(recap, suivi, 'récapitulatif de trois essais par modèle', 'Lancer le benchmark')
        synthese = next(n['text'] for n in recap.nodes if n['tag'] == 'ul' and 'meta' in n['attrs']['class'] and 'réponses estimées' in n['text'])
        self.assertRegex(synthese, r'^2 modèles · 3 essais par modèle · 6 essais · réponses estimées : [0-9,]+ USD · ')
        self.assertIn('Chaque modèle répond 3 fois à l’exemple', recap.visible)
        essais = next(n for n in recap.nodes if n['tag'] == 'table' and n['attrs'].get('aria-label') == 'Essais prévus')
        lignes = [n['text'] for n in recap.nodes if n['tag'] == 'tr' and n['text'] in essais['text']][1:]
        self.assertEqual(['Essai 1', 'Essai 1', 'Essai 2', 'Essai 2', 'Essai 3', 'Essai 3'], [l[:7] for l in lignes])
        self.assertTrue(all('USD' in ligne for ligne in lignes))
        with closing(storage.Store(self.data)) as store:
            manifeste = campaigns.inspect(store, suivi.split('/')[-2])['manifest']
        # Essai 1 de chaque modèle avant tout essai 2 : un arrêt laisse la même couverture à chacun
        self.assertEqual([(1, 'configuration-1'), (1, 'configuration-2'), (2, 'configuration-1'),
                          (2, 'configuration-2'), (3, 'configuration-1'), (3, 'configuration-2')],
                         [(cell['repetition'], cell['configuration_id']) for cell in manifeste['plan']])
        for configuration in manifeste['panel']:
            montants = [storage._money(cell['estimate']['amount_usd']) for cell in manifeste['plan']
                        if cell['configuration_id'] == configuration['id']]
            self.assertEqual(3, len(montants))
            self.assertEqual(storage._money(configuration['estimate']['amount_usd']), sum(montants))
        # Un plan qui omet tous les essais d'un modèle est refusé, même si l'autre en a trois
        incomplet = dict(manifeste, campaign_id=manifeste['campaign_id'] + 'x',
                         plan=[cell for cell in manifeste['plan'] if cell['configuration_id'] == 'configuration-1'])
        incomplet['attempt_policy'] = dict(manifeste['attempt_policy'], order=[cell['cell_id'] for cell in incomplet['plan']])
        with closing(storage.Store(self.data)) as store, self.assertRaisesRegex(ValueError, 'Essais prévus inégaux'):
            campaigns.create(store, incomplet)
        # Le choix se relit sur la page des modèles
        _, _, raw = self.request(dossier + '/configurations')
        # Trois choix courts : des boutons radio de même nom, le troisième coché
        self.assertRegex(raw.decode(), r'<fieldset class="segments"[^>]*><legend>Essais par modèle et par exemple</legend>'
                                       r'(?:<label><input type="radio" name="repetitions" value="[12]" form="configurations-form"> [^<]*</label>){2}'
                                       r'<label><input type="radio" name="repetitions" value="3" form="configurations-form" checked> 3 essais</label>')
        self.assertNotIn('<select id="repetitions"', raw.decode())
        self.submit(recap, '/start', {}, status=303)
        attempts = self.starts.get_nowait()['candidate_attempts']
        self.assertEqual(6, len(attempts))
        recus = []

        def candidat(operation, request):
            modele = request['requested_configuration']['model']
            recus.append(modele)
            essai = recus.count(modele)
            if (modele, essai) == (modeles[0], 3):
                raise RuntimeError('Réponse perdue après émission')
            value = response(operation, request)
            # 2. Le premier essai de Mistral échoue, le second réussit : aucun des deux ne remplace l'autre
            value['receipt']['result']['output'] = ('Action omise' if (modele, essai) == (modeles[1], 1) else
                                                    'Action : relire | Responsable : Camille')
            value['cost'].update(status='KNOWN', amount=f'0.{essai}{modeles.index(modele) + 1}', currency='USD',
                                 source='Reçu candidat #455')
            return value
        execution.execute_launch(self.data, attempts, candidat, access_secret=SECRET, access_transport=self.access)
        self.assertEqual(modeles * 2 + modeles[:1], recus)
        # 3. L'essai ambigu arrête la comparaison ; un redémarrage ne rejoue rien
        with closing(storage.Store(self.data)) as store:
            runtime.stop(self.data, store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        execution.execute_launch(self.data, attempts, candidat, access_secret=SECRET, access_transport=self.access)
        self.assertEqual(5, len(recus))
        with closing(storage.Store(self.data)) as store:
            etats = {cell['cell_id']: cell['state'] for cell in campaigns.inspect(store, suivi.split('/')[-2])['cells']}
        self.assertEqual(['RECEIVED'] * 4 + ['AMBIGUOUS', 'INTENT_RECORDED'],
                         [etats[cell['cell_id']] for cell in manifeste['plan']])
        # 4. Le suivi nomme chaque essai de chaque modèle
        page, _, _ = self.request(suivi)
        lignes = [n['text'] for n in page.nodes if n['tag'] == 'li' and ' · essai ' in n['text']]
        self.assertEqual([('Modèle A', '1', 'réponse reçue'), ('Modèle C', '1', 'réponse reçue'),
                          ('Modèle A', '2', 'réponse reçue'), ('Modèle C', '2', 'réponse reçue'),
                          ('Modèle A', '3', 'réponse non confirmée, à vérifier'),
                          # Issue #525 : la comparaison arrêtée n'enverra plus cet essai
                          ('Modèle C', '3', 'non appelé : la comparaison s’est arrêtée avant cet essai')],
                         [(ligne[:8], *re.search(r' · essai (\d) : (.*)$', ligne).groups()) for ligne in lignes])
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(1, len(store.close_expired_ambiguous(datetime.now(timezone.utc) + timedelta(minutes=16))))
        page, _, _ = self.request(suivi)
        self.submit(page, '/evaluate', {})
        auto.execute_campaign(self.data, self.starts.get_nowait()['judgment_operations'], self.bound[2])
        self.assertEqual(4, juge.request.call_count)
        self.assertEqual(5, len(recus))
        comparison = suivi.removesuffix('/conditions')
        results, _, raw = self.request(comparison)
        self.examine(results, comparison, 'résultats de trois essais par modèle', None)
        _, _, donnees = self.request(comparison, json_response=True)
        verdicts = {(row['requested_configuration']['model'], row['repetition']): row['verdict']
                    for row in json.loads(donnees)['rows']}
        # 2. Chaque essai reçu a sa ligne et son verdict, l'échec du premier essai de Mistral compris
        self.assertEqual({(modeles[0], 1): 'SATISFAIT', (modeles[0], 2): 'SATISFAIT',
                          (modeles[1], 1): 'NE SATISFAIT PAS', (modeles[1], 2): 'SATISFAIT'}, verdicts)
        self.assertEqual(4, raw.decode().count('<tr id="attempt-'))
        for numero in (1, 2):
            self.assertEqual(2, results.visible.count('Essai ' + str(numero) + ' sur 3'))
        # 4. Les deux essais manquants restent visibles, chacun sous son numéro
        self.assertIn('Aucune réponse exploitable pour Modèle A, essai 3', results.visible)
        self.assertIn('Modèle C, essai 3, n’a pas été appelé', results.visible)
        self.assertRegex(results.visible, r'essais lancés : 5 sur 6')
        # 5. Ni conseil, ni taux : les essais se lisent un par un
        self.assertNotIn('Notre conseil', results.visible)
        self.assertIn('Le juge automatique n’est pas qualifié', results.visible)
        self.assertIn('Chaque essai est affiché séparément ; leur nombre ne mesure pas une fiabilité.', results.visible)
        self.assertNotIn('%', results.visible)
        self.sans_promesse(results)
        # Issue #456 : filtré sur Mistral, ses deux essais restent séparés et numérotés, sans conseil ni taux
        filtre, _, raw = self.request(comparison + '?configuration=configuration-2')
        self.assertEqual(2, raw.decode().count('<tr id="attempt-'))
        for attendu in ('Essai 1 sur 3', 'Essai 2 sur 3', 'Modèle C, essai 3, n’a pas été appelé', 'essais lancés : 5 sur 6',
                        'Chaque essai est affiché séparément ; leur nombre ne mesure pas une fiabilité.'):
            self.assertIn(attendu, filtre.visible)
        self.assertNotIn('Notre conseil', filtre.visible)
        # Le détail d'un essai, page ou modale, nomme son numéro
        href = next(row['detail_href'] for row in json.loads(donnees)['rows']
                    if (row['requested_configuration']['model'], row['repetition']) == (modeles[1], 2))
        detail, _, _ = self.request(href)
        self.assertIn('Essai 2 sur 3', next(n['text'] for n in detail.nodes if n['attrs'].get('id') == 'attempt-detail'))
        artefacts = os.environ.get('BENCHX_E2E_ARTEFACTS')
        if artefacts:
            Path(artefacts).mkdir(parents=True, exist_ok=True)
            Path(artefacts, 'essais-repetes.json').write_text(json.dumps(
                {'essais_par_modele': 3, 'essais_prevus': len(manifeste['plan']),
                 'refus_hors_borne': ['0', '4', 'deux'], 'envois': len(recus),
                 'etats_apres_arret': [etats[cell['cell_id']] for cell in manifeste['plan']],
                 'appels_candidats_apres_redemarrage': len(recus) - 5, 'jugements': juge.request.call_count,
                 'verdicts': {f'{modele} essai {essai}': verdict for (modele, essai), verdict in verdicts.items()},
                 'conseil_affiche': 'Notre conseil' in results.visible},
                ensure_ascii=False, indent=2) + '\n')

    def test_reprise_pour_longueur_d_un_essai_repete(self):
        """Issue #455 : la reprise technique vise un essai prévu, sans devenir un essai de plus

        Modes d'échec couverts :
        1. la reprise de l'essai 2 vise l'essai 1, ou un essai non coupé est repris ;
        2. la reprise compte comme un essai prévu, ou l'essai 2 perd son numéro une fois repris ;
        3. le coût de l'envoi coupé se confond avec celui de la réponse reprise
        """
        self.juge_factice()
        envois_mistral = []

        def comportement(value, model, limit, suivi):
            if model == 'mistralai/mistral-small-2603':
                envois_mistral.append(limit)
                value['cost'].update(status='KNOWN', amount=f'0.0{len(envois_mistral)}')
                if len(envois_mistral) == 2:
                    self.coupee(value, 'Action : relire', limit)
        dossier, recap, envois, _ = self.lancer_avec_reprises(
            ['openai/gpt-5.6-sol', 'mistralai/mistral-small-2603'], comportement, repetitions=2)
        self.assertIn('2 modèles · 2 essais par modèle · 4 essais', recap.visible)
        self.assertEqual([('openai/gpt-5.6-sol', 4096), ('mistralai/mistral-small-2603', 4096)] * 2
                         + [('mistralai/mistral-small-2603', 8192)], envois)
        with closing(storage.Store(self.data)) as store:
            reprise, = [json.loads(row[0]) for row in store._connection.execute(
                "SELECT manifest_json FROM s4_campaigns WHERE json_extract(manifest_json, '$.recovery_of') IS NOT NULL")]
            source = store._connection.execute('SELECT cell_id FROM s4_attempts WHERE operation_id=?',
                                               (reprise['recovery_of'],)).fetchone()[0]
            racine = campaigns.inspect(store, dossier.rsplit('/', 1)[1] + '-c1')['manifest']
        # 1. et 2. La reprise vise le deuxième essai de Mistral et garde son numéro
        cellule = next(cell for cell in racine['plan'] if cell['cell_id'] == source)
        self.assertEqual((2, 'configuration-2'), (cellule['repetition'], cellule['configuration_id']))
        self.assertEqual([cellule], reprise['plan'])
        html = self.evaluer(dossier)
        self.assertEqual(4, html.count('<tr id="attempt-'))
        lignes = {Page(ligne.encode()).visible for ligne in re.findall(r'<tr id="attempt-.*?</tr>', html, re.S)}
        reprises = [ligne for ligne in lignes if 'Reprise après arrêt pour longueur' in ligne]
        self.assertEqual(1, len(reprises))
        self.assertIn('Essai 2 sur 2', reprises[0])
        self.assertIn('0,03 USD', reprises[0])
        self.assertIn('Réponses évaluées : 4 · essais lancés : 4 sur 4.', Page(html.encode()).visible)
        # 3. L'envoi coupé reste dans les autres envois, avec son propre coût
        _, phases = self.depenses(html)
        self.assertTrue(any('0,02' in item for item in phases['Autres envois aux modèles']))
        self.assertFalse(any('0,02' in item for item in phases['Réponses comparées']))

    def test_temoins_relances_effet_ambigu_et_preuves_etrangeres(self):
        """Issue #445, après revue indépendante : ce qui ne doit ni qualifier ni consommer

        Modes d'échec couverts :
        1. une réponse sans alternative valable qualifie, ou consomme un contrôle ;
        2. un constat sur un critère étranger, ou citant une pièce étrangère, prouve une décision ;
        3. après un incident reçu sur un témoin, rien ne relance son contrôle et la version reste bloquée ;
        4. après un effet ambigu, les témoins suivants partent quand même, ou une relance est programmée
        """
        consigne = ['Relever toutes les actions dans les notes']

        def preparer(operation, request):
            value = self.prepare(operation, request)
            value['receipt']['result']['package']['candidate']['instruction'] = ' '.join(consigne)
            return value
        self.assistants['transport'] = SessionAssistant(preparer, {'model': 'factice'})
        self.preparation_stage = 'exemple'
        juge = self.qualifier.controller()
        page, _, _ = self.request('/')
        target = page.link('Décrire mon cas')
        page, _, _ = self.request(target)
        key_form = page.form('/access/key')
        self.request(key_form['action'], key_form['fields'] | {'key': KEY}, status=303)
        page, _, _ = self.request(target)
        page = self.submit(page, '/dossiers', {'request': 'Transformer des notes de réunion en une liste complète des actions à relire'})
        dossier = page.link('Actualiser')
        dossier_id = dossier.rsplit('/', 1)[1]
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])

        def verifier():
            page, _, _ = self.request(dossier)
            self.submit(page, '/validation', {})
            prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
            return self.request(dossier)[0]

        def corriger(ajout):
            consigne.append(ajout)
            page, _, _ = self.request(dossier)
            self.submit(page, '/messages', {'kind': 'correct', 'message': 'Préciser la consigne : ' + ajout})
            prep.execute(self.data, self.starts.get_nowait(), self.bound[0])

        def detail(page):
            return next(n['text'] for n in page.nodes if n['tag'] == 'details'
                        and 'Détail de la vérification de l’exemple' in n['text'])

        def retries():
            with closing(storage.Store(self.data)) as store:
                return prep.due_qualification_retries(store, dossier_id=dossier_id)
        # 1. Un défaut seul, sans alternative valable : réponse inexploitable, aucun contrôle consommé
        self.qualifier.result = {'qualified': True, 'findings': [], 'summary': 'Défaut seul', 'witnesses': [
            {'kind': 'defect', 'output': 'Aucune action.', 'expected': [{'control_id': 'O1', 'status': 'FAIL'}],
             'justification': 'La référence attend les actions : leur absence est le défaut ciblé.'}]}
        page = verifier()
        self.assertIn('Résultat de qualification reçu non utilisable', page.visible)
        self.assertEqual([], juge.calls)
        # 2. Critère étranger sur l'alternative, pièce étrangère sur le défaut : aucune décision prouvée
        corriger('Une action par ligne.')
        self.qualifier.result = {'qualified': True, 'findings': [], 'summary': 'Exemple vérifiable'}
        critere_etranger = lambda findings: [dict(row, criterion_id='E9') for row in findings]
        # Second contrôle de l'alternative (#492, piste 3) : même rejet ; le défaut n'est pas relancé, la version est bloquée
        juge.script = [critere_etranger,
                       lambda findings: [dict(row, evidence=[{'piece_id': 'piece-inventee', 'passage': 'Aucune'}])
                                         for row in findings],
                       critere_etranger]
        verifier()
        self.rejouer_relances(dossier_id)
        page = self.request(dossier)[0]
        # #492, critère 4 : la réponse du juge, rejetée deux fois, n'est pas un verdict sur l'exemple
        self.assertIn('Vérification impossible pour le moment', page.visible)
        self.assertIn('restée inexploitable après une relance', page.visible)
        self.assertEqual(2, detail(page).count('Décision sans preuve exploitable'))
        self.assertEqual([], retries())
        # 3. Incident reçu sur l'alternative : relance automatique annoncée, puis qualification sans correction
        corriger('Garder le responsable de chaque action.')
        juge.script = [429]
        page = verifier()
        self.examine(page, dossier, 'contrôle de témoin en relance', None)
        self.assertIn('Vérification de l’exemple en attente', page.visible)
        self.assertIn('Nouvelle tentative automatique', page.visible)
        due = retries()
        self.assertEqual(1, len(due))
        with closing(storage.Store(self.data)) as store:
            following = prep.retry_qualification(store, due[0][0], self.bound[1], 'a' * 40)
            link = json.loads(store._operations(store._connection, operation_ids={following})[0]['resources'][2])
        self.assertEqual({'retry_of': due[0][0], 'attempt': 2}, link)
        prep.execute_qualification(self.data, following, self.bound[1])
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'témoins confirmés après relance', 'Choisir les modèles')
        self.assertEqual(2, detail(page).count('Décision conforme à l’attendu'))
        self.assertEqual([], retries())
        # 4. Effet ambigu sur le premier témoin : le second ne part pas, aucune relance n'est programmée
        corriger('Écrire l’échéance quand elle est connue.')
        juge.script = [RuntimeError('Réponse perdue après émission')]
        calls = len(juge.calls)
        page = verifier()
        self.assertEqual(calls + 1, len(juge.calls))
        self.assertIn('Vérification impossible pour le moment', page.visible)
        self.assertFalse(any(n['tag'] == 'a' and n['text'] == 'Choisir les modèles' for n in page.nodes))
        self.assertEqual([], retries())
        with closing(storage.Store(self.data)) as store:
            states = sorted(op['state'] for op in store._operations(store._connection)
                            if op['dossier_id'] == dossier_id and op['engine_version'] == prep.WITNESS_FORMAT
                            and op['revision'] == max(o['revision'] for o in store._operations(store._connection)
                                                      if o['dossier_id'] == dossier_id))
        self.assertEqual(['AMBIGUOUS', 'RECEIVED'], states)

    def test_contrat_operateur_bloque_montre_ses_constats_au_demandeur(self):
        """Un contrat opérateur bloqué après la préparation de la comparaison : le dossier et le refus nomment ce qui bloque

        Modes d'échec couverts :
        1. la page du dossier ou de refus ne dit pas pourquoi l'exemple n'est plus prêt ;
        2. un contrôle réussi est présenté comme un motif ;
        3. la preuve d'un contrôle, qui cite la référence de jugement, s'affiche chez le demandeur ;
        4. un identifiant interne (critère, pièce) cité par l'opérateur s'affiche tel quel
        """
        dossier = self.exemple_qualifie()
        page, _, _ = self.request(dossier + '/configurations')
        self.submit(page, '/configurations', {'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'],
                                              'tier': 'high'})
        recap = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions'
        page, _, _ = self.request(recap)
        self.examine(page, recap, 'prêt à lancer', 'Lancer le benchmark')
        start = page.form('/start')
        notes, reserve = self.bloquer_par_contrat_operateur(dossier)
        # Identifiants de critère, de pièce et de contrôle masqués ; « source » employé comme mot reste intact
        affiche = ('Contrôle « Action présente » en échec : « Action présente » non prouvée, la pièce (identifiant masqué) '
                   'ne montre aucune action ; contrôles « Action omise » et « Action présente » à revoir. '
                   'La source des notes reste lisible.')
        page, _, raw = self.request(dossier)
        # Comparaison préparée mais pas lancée : l'encadré dit que l'exemple est à revoir, sans proposer de lancer
        self.examine(page, dossier, 'dossier bloqué par le contrat opérateur', None)
        state = next(n for n in page.nodes if 'state' in n['attrs'].get('class', '').split())
        self.assertIn('Exemple à revoir avant comparaison', state['text'])
        self.assertNotIn('Vérifier puis lancer', page.visible)
        verification = next(n['text'] for n in page.nodes if n['tag'] == 'details'
                            and 'Vérification de l’exemple' in n['text'])
        self.assertIn('Ce que la vérification de l’exemple a relevé', verification)
        self.assertIn(affiche, verification)
        self.assertNotIn('ne sont pas assez établis', verification)
        for absent in (notes, 'Omission témoin repérée', reserve):
            self.assertNotIn(absent, raw.decode())
        for path, fields in ((recap, None), (dossier + '/configurations', None),
                             (start['action'], start['fields'])):
            with self.subTest(route=path):
                page, _, raw = self.request(path, fields, status=403)
                self.examine(page, path, 'exemple bloqué par le contrat opérateur', 'Retrouver mes cas d’usage')
                self.assertIn('Terminez l’étape précédente avant de poursuivre.', page.visible)
                self.assertIn('Ce que la vérification de l’exemple a relevé', page.visible)
                self.assertIn(affiche, page.visible)
                self.assertNotIn(notes, raw.decode())
                self.assertNotIn('Omission témoin repérée', raw.decode())
                self.assertNotIn(reserve, raw.decode())
        _, _, raw = self.request(recap, status=403, json_response=True)
        self.assertEqual([{'text': affiche}], json.loads(raw)['findings'])

    def bloquer_par_contrat_operateur(self, dossier):
        """Contrat opérateur de la dernière version, bloqué par un contrôle qui cite un critère et une pièce"""
        with closing(storage.Store(self.data)) as store:
            dossier_id = dossier.rsplit('/', 1)[1]
            revision, reference = store._connection.execute(
                "SELECT revision, piece_id FROM pieces WHERE dossier_id=? AND role='judge' "
                'ORDER BY revision DESC LIMIT 1', (dossier_id,)).fetchone()
            notes = store._connection.execute(
                "SELECT piece_id FROM pieces WHERE dossier_id=? AND revision=? AND role='candidate'",
                (dossier_id, revision)).fetchone()[0]
            candidate = qualification.draft(store, dossier_id, revision, specification(reference))

            def bloque(contract, resources):
                review = check(contract, resources)
                review['checks'][0].update(status='FAIL', finding=(
                    f'Contrôle source en échec : O1 non prouvée, la pièce {notes} ne montre aucune action ; '
                    'contrôles `defect` et source à revoir. La source des notes reste lisible.'))
                return review
            self.assertEqual('BLOCKED', qualification.qualify(
                store, candidate['contract_sha256'], reviewer=ACTOR, check=bloque)['status'])
            return notes, store.read_piece(reference).decode()

    def test_contrat_operateur_bloque_avant_toute_comparaison(self):
        """Sans comparaison préparée, l'encadré ne prétend pas que la vérification n'a pas pu démarrer"""
        dossier = self.exemple_qualifie()
        self.bloquer_par_contrat_operateur(dossier)
        page, _, _ = self.request(dossier)
        self.examine(page, dossier, 'exemple bloqué sans comparaison', None)
        state = next(n for n in page.nodes if 'state' in n['attrs'].get('class', '').split())
        self.assertIn('Exemple à revoir avant comparaison', state['text'])
        self.assertNotIn('n’a pas pu démarrer', page.visible)
        self.assertFalse(any(n['tag'] == 'a' and n['text'] == 'Choisir les modèles' for n in page.nodes))
        # La correction reste proposée depuis la page
        self.assertTrue(page.form('/messages'))

    def test_lancement_refuse_tant_que_l_exemple_n_est_pas_verifie(self):
        """Exemple validé, vérification pas encore faite : le choix des modèles est refusé, rien ne part"""
        dossier, page = self.exemple()
        # L'aperçu de l'exemple annonce son caractère inventé, sans promesse
        self.assertIn('Exemple inventé', page.visible)
        self.sans_promesse(page)
        self.submit(page, '/validation', {})
        qualification = self.starts.get_nowait()['qualification_operation']
        page, _, _ = self.request(dossier)
        self.assertFalse(any(n['tag'] == 'a' and n['text'] == 'Choisir les modèles' for n in page.nodes))
        _, _, raw = self.request(dossier + '/configurations', status=403)
        self.assertIn('Terminez l’étape précédente avant de poursuivre.', raw.decode())
        # La voie qui crée la comparaison refuse aussi, avec un jeton de formulaire valide
        jeton = next(f['fields']['csrf_token'] for f in page.forms if 'csrf_token' in f['fields'])
        _, _, raw = self.request(dossier + '/configurations', {'csrf_token': jeton, 'models': ['openai/gpt-5.6-sol',
                                 'mistralai/mistral-small-2603'], 'tier': 'low'}, status=403)
        self.assertIn('Terminez l’étape précédente avant de poursuivre.', raw.decode())
        self.assertTrue(self.starts.empty())
        self.assertEqual([], [call for call in self.calls if call[0] == 'candidat'])
        # La vérification terminée ouvre le choix des modèles
        prep.execute_qualification(self.data, qualification, self.bound[1])
        self.request(dossier + '/configurations')

    @staticmethod
    def etape(page):
        """Étape courante de la barre et cible de l'onglet Résultats (None s'il est désactivé)"""
        current = next(n['text'] for n in page.nodes if n['attrs'].get('aria-current') == 'step')
        results = next(n for n in page.nodes if n['tag'] in ('a', 'span') and n['text'] == '5Résultats')
        return current, results['attrs'].get('href')

    def exemple_qualifie(self):
        """Clé, exemple sans question, validation et vérification : le cas est prêt à comparer"""
        dossier, page = self.exemple()
        self.assertIn('Cet exemple correspond-il bien à votre travail ?', page.visible)
        self.submit(page, '/validation', {})
        prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
        return dossier

    def exemple(self):
        """Clé et exemple préparé sans question : le dossier et sa page, avant validation"""
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
        return dossier, page

    def juge_factice(self, constat=None, mesure=None, ajuste=None, *, statut=200, cout='0.001', contenu=None, profil=None, cle=None):
        """Juge OpenRouter réel sur une connexion HTTP simulée : chaque réponse évaluée ne satisfait pas une obligation

        `constat(critère)` remplace ce FAIL uniforme par (statut, attribution, texte du constat) ; pour une
        obligation composée, il reçoit l'élément que vise le contrôle ;
        `mesure` est la valeur rendue pour chaque critère de qualité, sinon aucun n'est mesuré ;
        appelable, elle reçoit le critère et la réponse évaluée ;
        `ajuste(vue du juge, constats)` réécrit les constats d'une réponse avant l'envoi.
        `statut`, `cout` et `contenu` restent modifiables via `self.juge_etat` pour un contrôle fictif :
        `cout` None omet l'usage, `contenu` remplace la proposition
        """
        from unittest.mock import Mock
        from benchmark.transports import openrouter
        from tests.test_openrouter_preparation import SYNTHETIC_PROFILE, estimate_for
        # Règle de preuve du profil réel du juge : omission prouvable
        if profil is None:
            profile = openrouter.load_profile(str(SYNTHETIC_PROFILE)) | {'evidence_rule': 'server-evidence/v2'}
        else:
            profile = openrouter.load_profile(str(profil))
        judge = openrouter.OpenRouterJudgment(None, profile)
        if cle is not None:
            judge._api_key = cle
        judge._quote = openrouter.configuration(estimate_for(profile), profile)
        http = Mock()
        http.getresponse.return_value.status = 200
        http.getresponse.return_value.length = 0
        http.getresponse.return_value.getheader.return_value = None
        etat = {'statut': statut, 'cout': cout, 'contenu': contenu}
        self.juge_etat = etat

        def answer(method, path, *, body, headers):
            http.getresponse.return_value.status = etat['statut']
            content = json.loads(json.loads(body)['messages'][1]['content'])
            self.judged.append(content)
            self.judge_systems.append(json.loads(body)['messages'][0]['content'])
            output = content['output']
            proof = {'piece_id': output['piece_id'], 'sha256': output['sha256'], 'passage': output['content']}

            def cible(x, k):
                return next((e for e in x.get('elements', []) if e['id'] == k), x)
            findings = [dict(zip(('status', 'attribution', 'finding'), constat(cible(x, k)) if constat else
                                 ('FAIL', 'candidate', 'Obligation non satisfaite')),
                             criterion_id=x['id'], control_id=k, evidence=[proof])
                        for x in content['obligations'] + content['eliminatory_errors'] for k in x['control_ids']]
            measures = [dict(criterion_id=x['id'], value=mesure(x, output['content']) if callable(mesure) else mesure,
                             unit=x['unit'], evidence=[proof])
                        for x in content['secondary_criteria']] if mesure is not None else []
            if ajuste:
                findings = ajuste(content, findings)
            result = dict(findings=findings, measures=measures, limits=[], proposed_verdict='SATISFAIT')
            raw = etat['contenu'] if etat['contenu'] is not None else storage._strict_json(result)
            document = dict(id='fixture-judge', model=profile['revision'],
                choices=[dict(finish_reason='stop', message=dict(role='assistant', content=raw))],
                openrouter_metadata=dict(requested=profile['model'],
                endpoints=dict(available=[dict(provider=profile['routes'][0]['provider_name'], selected=True)])))
            if etat['cout'] is not None:
                document['usage'] = dict(cost=etat['cout'])
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
        results, _, raw = self.request(comparison)
        self.examine(results, comparison, 'résultats partiels', None)
        self.assertIn('Aucune réponse exploitable pour Modèle B (cause non établie par le reçu).', results.visible)
        # Issue #442 : un reçu sans corps HTTP ne fait inventer ni motif ni consommation
        self.assertIn('Essai d’origine · motif de fin transmis : inconnu · consommation transmise : inconnue', raw.decode())
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

        def coupee(value, content, choices=None, usage=None):
            """Reçu OpenRouter terminé par la limite de sortie, comme le transport Pi le conserve"""
            body = storage._strict_json({'choices': choices or [{'finish_reason': 'length', 'message': {
                'role': 'assistant', 'content': content}}]} | ({'usage': usage} if usage else {})).encode()
            value['receipt']['observed_configuration']['http'] = dict(
                status=200, complete=True, credential_redacted=False,
                body_base64=b64encode(body).decode(), body_sha256=sha256(body).hexdigest())
            value['receipt']['result'].update(output=content, incident='PROVIDER_RESPONSE_INCOMPLETE')

        def candidat(operation, request):
            value = self.candidate(operation, request)
            model = request['requested_configuration']['model']
            if model == 'deepseek/deepseek-v4.1-flash':
                # Reçu complet mais de structure inattendue : aucune cause n'est avancée, la page reste lisible
                coupee(value, None, [None], {'prompt_tokens': 30, 'completion_tokens': 12})
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
        self.assertIn('Aucune réponse exploitable pour Modèle B (cause non établie par le reçu). Ce modèle', html)
        # Issue #442 : un choix mal formé n'efface pas la consommation que le corps intact transmet
        self.assertIn('Essai d’origine · motif de fin transmis : inconnu · consommation transmise : '
                      'completion_tokens 12, prompt_tokens 30', html)
        _, _, raw = self.request(re.search(r'href="([^"]+/attempts/[^"]+)"', cellule)[1].replace('&amp;', '&'))
        text = raw.decode()
        self.assertIn('0 exigence sur 2 respectée, 1 non respectée, 1 non vérifiable. 1 erreur éliminatoire relevée.', text)
        states = re.findall(r'>([^<>]+)</span><span>([^<]+)</span>', text)
        self.assertIn(('Non respectée', tableau), states)
        self.assertIn(('Non vérifiable', echeances), states)
        self.assertIn(explication, text)

    def test_obligation_composee_jugee_element_par_element(self):
        """Une obligation composée déclarée par la préparation se juge élément par élément

        Modes d'échec couverts :
        1. l'aperçu montre un objet brut au lieu de l'obligation et de ses éléments ;
        2. le juge ne reçoit qu'un contrôle pour l'obligation composée, ou sans la description des éléments ;
        3. un constat scripté de format respecté et un constat scripté d'échéance inventée donnent autre chose
           que « Ne satisfait pas » (le juge est scripté : le scénario ne prouve pas la détection d'une date inventée) ;
        4. le motif nomme l'obligation sans l'élément en défaut ;
        5. le détail ne montre pas l'état de chaque élément, ou compte les éléments comme des exigences ;
        6. le filtre « Respectée » retient une réponse dont un élément est en défaut ;
        7. un identifiant d'élément apparaît au lecteur ;
        8. l'historique privé aplatit l'obligation en un texte dont les séparateurs se confondent avec ceux des
           descriptions ;
        9. une obligation simple change de contrôle à côté d'une obligation composée ;
        10. un élément qui cite un coût rend l'obligation entière non vérifiable (contrôle local supposé)
        """
        from benchmark import automatic_judgment as auto, privacy_archive
        composee = 'Lister chaque action avec son échéance'
        forme = 'Une ligne par action et son coût, au format « Action : … | Responsable : … »'
        echeance = 'L’échéance de chaque action reprise des notes'
        simple = 'Nommer le responsable de chaque action'
        self.criteria = {'eliminatory': ['Inventer une décision absente du compte rendu'],
                         'obligations': [{'description': composee, 'elements': [forme, echeance]}, simple],
                         'quality': []}
        # Le juge reçoit les identifiants d'éléments et peut les citer : le lecteur lit la description
        defaut = 'O1_2 non respecté : 12 mars inventé, absent des notes'

        def constat(cible):
            if cible['description'] == echeance:
                return 'FAIL', 'candidate', defaut
            return 'PASS', 'candidate', 'Respecté dans la réponse'
        judge = self.juge_factice(constat)
        dossier = self.exemple_qualifie()
        page, _, _ = self.request(dossier)
        for value in (composee, forme, echeance):
            self.assertIn(value, page.visible)
        self.assertNotIn("'elements'", page.visible)
        page, _, _ = self.request(dossier + '/configurations')
        page = self.submit(page, '/configurations', {'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'],
                                                     'tier': 'low'})
        self.submit(page, '/start', {})
        execution.execute_launch(self.data, self.starts.get_nowait()['candidate_attempts'], self.candidate,
                                 access_secret=SECRET, access_transport=self.access)
        page, _, _ = self.request(dossier)
        page, _, _ = self.request(page.link('Lancer l’évaluation'))
        self.submit(page, '/evaluate', {})
        auto.execute_campaign(self.data, self.starts.get_nowait()['judgment_operations'], self.bound[2])
        self.assertEqual(2, judge.request.call_count)
        obligations = self.judged[0]['obligations']
        self.assertEqual([['O1_1', 'O1_2'], ['O2']], [x['control_ids'] for x in obligations])
        self.assertEqual([dict(id='O1_1', description=forme), dict(id='O1_2', description=echeance)],
                         obligations[0]['elements'])
        self.assertNotIn('elements', obligations[1])
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        results, _, raw = self.request(comparison)
        self.examine(results, comparison, 'obligation composée', None)
        self.assertNotIn('O1_', results.visible)
        cellule = next(chunk for chunk in raw.decode().split('<tr id="attempt-')[1:]
                       if '<strong>Modèle A</strong>' in chunk).split('</tr>')[0]
        self.assertIn('Exigence non respectée : ' + composee + ' (' + echeance + ').', cellule)

        def modeles(page):
            return [n['text'] for n in page.nodes if n['tag'] == 'strong']
        self.assertNotIn('Modèle A', modeles(self.request(comparison + '?obligation=O1%3APASS')[0]))
        self.assertIn('Modèle A', modeles(self.request(comparison + '?obligation=O1%3AFAIL')[0]))
        detail, _, raw = self.request(re.search(r'href="([^"]+/attempts/[^"]+)"', cellule)[1].replace('&amp;', '&'))
        text = raw.decode()
        self.assertNotIn('O1_', detail.visible)
        self.assertIn('1 exigence sur 2 respectée, 1 non respectée.', text)
        states = re.findall(r'>([^<>]+)</span><span>([^<]+)</span>', text)
        for state in (('Non respectée', composee), ('Respectée', forme), ('Non respectée', echeance), ('Respectée', simple)):
            self.assertIn(state, states)
        self.assertIn(echeance + ' non respecté : 12 mars inventé, absent des notes', text)
        self.assertNotIn('O1_', text)
        with closing(storage.Store(self.data)) as store:
            session = store._connection.execute('SELECT session_id FROM s2_dossiers').fetchone()[0]
            view = prep.view(store, session, dossier.rsplit('/', 1)[1])
        revision = privacy_archive._revision(view)
        privacy_archive._check_revision(revision)
        self.assertIn({'description': composee, 'elements': [forme, echeance]}, revision['criteria'])
        self.assertIn(simple, revision['criteria'])

    def test_omission_prouvee_sans_fausse_citation(self):
        """Issue #446 : une omission se prouve par la pièce candidate qui porte l'élément, sans citer la sortie à tort

        Modes d'échec couverts :
        1. un défaut ciblé par omission ne peut pas être confirmé sur son témoin sans fausse citation de la réponse,
           ou l'est sous un juge à qui la règle n'a pas été annoncée (ancienne réponse relue autrement) ;
        2. une omission prouvée par la pièce candidate et la sortie examinée ne fonde pas « Ne satisfait pas » ;
        3. une autre formulation valable de l'élément est rejetée ;
        4. le détail montre « None » ou un faux extrait de la réponse au lieu de l'absence constatée ;
        5. le passage de la pièce candidate, l'exigence visible ou le lien vers la réponse examinée disparaissent ;
        6. la règle d'omission n'est pas nommée par une version distincte, ou change la lecture d'un constat cité
        """
        from benchmark import automatic_judgment as auto
        notes = ('Décision : le budget formation est validé.\n'
                 'Action : Camille relit le devis avant vendredi.\n')
        passage = 'Action : Camille relit le devis avant vendredi.'
        exigence = 'Relever chaque action avec son responsable'
        criteres = {'eliminatory': ['Ajouter une action absente des notes'], 'obligations': [exigence], 'quality': []}
        constat = 'L’action de Camille, relire le devis avant vendredi, manque dans la réponse.'
        alternative = 'Camille : relire le devis, avant vendredi.'
        omise = 'Décision : budget formation validé.'

        def preparer(operation, request):
            self.calls.append(('préparation', operation['operation_id']))
            value = response_for(operation)
            value['cost'].update(amount='0.10', currency='USD', source='Reçu simulé #446')
            result = value['receipt']['result']
            result['explanation'] = 'Cet exemple correspond-il bien à votre travail ?'
            result['package']['candidate'].update(instruction='Relever les actions à mener dans les notes.',
                criteria=criteres, deliverables=['Liste des actions'], pieces=[{'name': 'notes.txt', 'content': notes}])
            result['package']['judgment']['pieces'] = [{'name': 'reference.txt', 'content':
                'Attendus : l’action de Camille. Toute formulation qui garde la personne et l’action est recevable.'}]
            return value
        self.prepare = preparer

        def omission(vue, findings):
            """Le juge marque la sortie examinée sans la citer et cite le passage des notes qui porte l'élément"""
            notes_id = next(p['piece_id'] for p in vue['task']['pieces'] if p['name'] == 'notes.txt')
            return [dict(row, status='FAIL', attribution='candidate', finding=constat, evidence=[
                        {'piece_id': vue['output']['piece_id'], 'passage': None},
                        {'piece_id': notes_id, 'passage': passage}]) if row['criterion_id'] == 'O1' else row
                    for row in findings]
        # 1. Témoin de défaut par omission, contrôlé avec la même règle que les réponses des modèles
        temoin = {'kind': 'defect', 'output': omise, 'expected': [{'control_id': 'O1', 'status': 'FAIL'}],
                  'justification': 'Les notes portent l’action de Camille : son omission est le défaut ciblé.'}
        self.qualifier.result = {'qualified': True, 'findings': [], 'summary': 'Les actions sont vérifiables',
                                 'witnesses': [{'kind': 'alternative', 'output': alternative,
                                                'expected': [{'control_id': 'O1', 'status': 'PASS'},
                                                             {'control_id': 'E1', 'status': 'PASS'}],
                                                'justification': 'Toute formulation qui garde la personne et l’action.'},
                                               temoin]}
        controle = self.qualifier.controller()

        def temoin_omis(findings):
            vue = controle.calls[-1][1]['outgoing']
            return omission(vue, findings) if vue['output']['content'] == omise else findings
        # Juge à qui la règle d'omission n'est pas annoncée : sa réponse se lit comme avant elle, sans preuve
        controle.evidence_rule = None
        # Troisième réponse : second contrôle du défaut (#492, piste 3), rejeté comme le premier
        controle.script = [temoin_omis, temoin_omis, temoin_omis]
        dossier, page = self.exemple()
        self.submit(page, '/validation', {})
        prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
        self.rejouer_relances(dossier.rsplit('/', 1)[1])
        page, _, _ = self.request(dossier)
        # #492, critère 4 : la réponse du juge, rejetée deux fois, n'est pas un verdict sur l'exemple
        self.assertIn('Vérification impossible pour le moment', page.visible)
        self.assertIn('restée inexploitable après une relance', page.visible)
        # Même témoin, même absence, sous la règle annoncée : défaut confirmé
        controle.evidence_rule = 'server-evidence/v2'
        controle.script = [temoin_omis, temoin_omis]
        self.submit(page, '/messages', {'kind': 'correct', 'message': 'Garder les notes telles quelles'})
        prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
        page, _, _ = self.request(dossier)
        self.submit(page, '/validation', {})
        prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
        page, _, _ = self.request(dossier)
        self.assertIn('Exemple vérifié, prêt à comparer', page.visible)
        verification = next(n['text'] for n in page.nodes if n['tag'] == 'details'
                            and 'Détail de la vérification de l’exemple' in n['text'])
        self.assertIn(exigence + ' : attendu non satisfait, décidé non satisfait', verification)
        # 2 et 3. Deux réponses : une autre formulation valable, une omission
        page, _, _ = self.request(page.link('Choisir les modèles'))
        page = self.submit(page, '/configurations', {'models': ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'],
                                                     'tier': 'low'})
        self.submit(page, '/start', {})

        def candidat(operation, request):
            value = self.candidate(operation, request)
            value['receipt']['result']['output'] = (
                omise if request['requested_configuration']['model'] == 'deepseek/deepseek-v4.1-flash' else alternative)
            return value
        execution.execute_launch(self.data, self.starts.get_nowait()['candidate_attempts'], candidat,
                                 access_secret=SECRET, access_transport=self.access)
        judge = self.juge_factice(lambda cible: ('PASS', 'candidate', 'Respecté dans la réponse'),
                                  ajuste=lambda vue, findings: omission(vue, findings)
                                  if vue['output']['content'] == omise else findings)
        page, _, _ = self.request(dossier)
        page, _, _ = self.request(page.link('Lancer l’évaluation'))
        self.submit(page, '/evaluate', {})
        auto.execute_campaign(self.data, self.starts.get_nowait()['judgment_operations'], self.bound[2])
        self.assertEqual(2, judge.request.call_count)
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        results, _, raw = self.request(comparison)
        self.examine(results, comparison, 'omission prouvée', None)

        def ligne(nom):
            return next(chunk for chunk in raw.decode().split('<tr id="attempt-')[1:]
                        if '<strong>' + nom + '</strong>' in chunk).split('</tr>')[0]
        self.assertIn('Satisfait', ligne('Modèle A'))
        self.assertNotIn('Ne satisfait pas', ligne('Modèle A'))
        self.assertIn('Exigence non respectée : ' + exigence + '.', ligne('Modèle B'))
        # 4 et 5. Le détail relie l'exigence visible, le passage des notes, la réponse examinée et le constat
        detail, _, raw = self.request(re.search(r'href="([^"]+/attempts/[^"]+)"', ligne('Modèle B'))[1].replace('&amp;', '&'))
        text = raw.decode()
        self.assertIn(('Non respectée', exigence), re.findall(r'>([^<>]+)</span><span>([^<]+)</span>', text))
        self.assertIn(constat, text)
        self.assertIn('Extrait de notes.txt</summary><pre>' + passage + '</pre>', text)
        self.assertIn('Absence constatée dans la réponse du modèle', text)
        self.assertNotIn('<pre>None</pre>', text)
        self.assertNotIn('<pre></pre>', text)
        # 6. Version de la règle portée par l'évaluation qui s'en sert, seulement par elle
        with closing(storage.Store(self.data)) as store:
            records = {r['output_sha256']: r for r in auto.records(store, store._connection,
                                                                   dossier.rsplit('/', 1)[1] + '-c1')}
        from hashlib import sha256
        omis, valable = records[sha256(omise.encode()).hexdigest()], records[sha256(alternative.encode()).hexdigest()]
        self.assertEqual(('NE SATISFAIT PAS', 'SATISFAIT'), (omis['verdict'], valable['verdict']))
        preuve = next(f for f in omis['findings'] if f['criterion_id'] == 'O1')['evidence']
        self.assertEqual([None, passage], [p['passage'] for p in preuve])
        self.assertEqual(omis['output_piece_id'], preuve[0]['piece_id'])
        self.assertEqual('server-evidence/v2', omis['judgment']['evidence_binding']['version'])
        self.assertEqual('server-evidence/v1', valable['judgment']['evidence_binding']['version'])
        artefacts = os.environ.get('BENCHX_E2E_ARTEFACTS')
        if artefacts:
            Path(artefacts).mkdir(parents=True, exist_ok=True)
            Path(artefacts, 'omission-prouvee.json').write_text(json.dumps(
                {'verdicts': {'omission': omis['verdict'], 'autre_formulation': valable['verdict']},
                 'preuve_omission': preuve, 'regle': omis['judgment']['evidence_binding']['version']},
                ensure_ascii=False, indent=2) + '\n')

    def test_evaluation_terminee_sous_le_profil_autorise_apres_deploiement(self):
        """Lancement ancien, déploiement (maintenance, quiescence) sous un autre profil du juge, jugement sous le profil autorisé

        Modes d'échec couverts :
        1. la maintenance du déploiement ferme l'admission et l'évaluation part en silence sous le nouveau profil ;
        2. un profil autorisé ni courant ni retenu est remplacé en silence par le profil courant ;
        3. le profil autorisé retenu par le déploiement ne sert pas à réserver l'évaluation ;
        4. une évaluation déjà réservée ne peut plus partir après un second déploiement sans profil retenu
        """
        from contextlib import redirect_stdout
        from copy import copy
        from io import StringIO
        from benchmark import automatic_judgment as auto, runtime
        from benchmark.transports import openrouter
        from tests.test_openrouter_preparation import estimate_for
        self.juge_factice(lambda cible: ('PASS', 'candidate', 'Respecté dans la réponse'))
        autorise = self.assistants['judgment_transport']._profile
        dossier = self.exemple_qualifie()
        page, _, _ = self.request(dossier + '/configurations')
        page = self.submit(page, '/configurations', {'models': ['openai/gpt-5.6-sol', 'mistralai/mistral-small-2603'],
                                                     'tier': 'low'})
        self.submit(page, '/start', {})
        execution.execute_launch(self.data, self.starts.get_nowait()['candidate_attempts'], self.candidate,
                                 access_secret=SECRET, access_transport=self.access)
        campaign_id = dossier.rsplit('/', 1)[1] + '-c1'
        # Déploiement : les deux actions que le contrôleur appelle sur l'ancienne version, dans cet ordre
        for action in ('maintenance', 'quiescence'):
            with redirect_stdout(StringIO()):
                self.assertEqual(0, runtime.main([action, '--data', str(self.data)]), action)
        with closing(storage.Store(self.data)) as store:
            snapshot = campaigns.inspect(store, campaign_id)
        self.assertEqual((None, 'MAINTENANCE'), (snapshot['admission'], snapshot['stop_reason']))
        grant = snapshot['admissions'][-1]['authority']['automatic_judgment']['configuration']
        nouveau = dict(autorise, system=autorise['system'] + ' Consigne d’un déploiement ultérieur.')

        def deploiement(retenus):
            judge = openrouter.OpenRouterJudgment(None, nouveau, retained=retenus)
            judge._quote = openrouter.configuration(estimate_for(nouveau), nouveau)
            self.assistants['judgment_transport'] = judge
        # 1 et 2. Profil autorisé introuvable après la maintenance : refus explicite, rien n'est réservé ni envoyé
        deploiement(())
        page, _, _ = self.request(dossier)
        page, _, _ = self.request(page.link('Lancer l’évaluation'))
        self.submit(page, '/evaluate', {}, status=409)
        self.assertTrue(self.starts.empty())
        self.assertEqual([], self.judge_systems)
        # 3. Profil autorisé retenu par le déploiement : réservé sous lui, configuration du lancement intacte
        deploiement([autorise])
        self.submit(page, '/evaluate', {})
        ids = self.starts.get_nowait()['judgment_operations']
        with closing(storage.Store(self.data)) as store:
            reservees = [op for op in store.inspect_operations() if op['operation_id'] in ids]
        self.assertEqual(2, len(reservees))
        self.assertTrue(all(op['requested_configuration'] == grant for op in reservees))
        # 4. Second déploiement qui retire le profil retenu : l'évaluation réservée part sous ses propres octets
        juge = copy(self.bound[2])
        juge._retained = []
        auto.execute_campaign(self.data, ids, juge)
        self.assertEqual([autorise['system']] * 2, self.judge_systems)
        results, _, _ = self.request(dossier + '/campaigns/' + campaign_id)
        self.examine(results, dossier + '/campaigns/' + campaign_id, 'évaluée sous le profil autorisé', None)
        self.assertIn('Satisfait', results.visible)

    def test_obligation_composee_au_dela_du_plafond_refusee(self):
        """Une obligation de plus de cinq éléments n'entre pas dans l'exemple : aucun juge sans borne"""
        from benchmark import outgoing
        self.criteria = {'eliminatory': [], 'quality': [], 'obligations': [
            {'description': 'Lister les actions', 'elements': [f'Élément {i}' for i in range(1, outgoing.ELEMENT_LIMIT + 2)]}]}
        dossier, page = self.exemple()
        self.assertIn('Résultat reçu non utilisable', page.visible)
        self.assertNotIn('Cet exemple correspond-il bien à votre travail ?', page.visible)
        self.criteria['obligations'][0]['elements'].pop()
        self.assertEqual(outgoing.ELEMENT_LIMIT, len(outgoing.criteria(self.criteria)['obligations'][0]['elements']))

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

    def lancer_avec_reprises(self, models, comportement, qualifier=None, repetitions=None):
        """Relevé aux tarifs publiés, lancement public, puis exécution par une fabrique liée à la clé

        `comportement(valeur, modèle, limite, suivi)` modifie la réponse factice ; `suivi` est l'adresse
        du suivi de la comparaison ; `qualifier()` remplace `exemple_qualifie` ; `repetitions` : essais par
        modèle choisis sur la page des modèles. Renvoie le dossier, le
        récapitulatif avant lancement, les envois (modèle, limite) et les clés reçues par la fabrique
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
        dossier = (qualifier or self.exemple_qualifie)()
        suivi = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions'
        page, _, _ = self.request(dossier + '/configurations')
        recap = self.submit(page, '/configurations', {'models': models, 'tier': 'low'}
                            | ({'repetitions': str(repetitions)} if repetitions else {}))
        self.submit(recap, '/start', {})
        self.start = self.starts.get_nowait()
        attempts = self.start['candidate_attempts']
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
        # Les relances programmées partent par la même fabrique, comme dans l'exécuteur
        self.fabrique = fabrique
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
        self.assertIn('Coût estimé si chaque modèle était repris jusqu’à sa dernière limite : '
                      + montant_lisible(str(maximum)) + ' USD', recap.visible)
        self.assertNotIn('maximal', recap.visible)
        self.assertEqual(1, len(suivis))
        self.assertIn('relancé avec une limite de sortie plus haute', suivis[0])
        self.assertNotIn('Toutes les réponses sont arrivées', suivis[0])
        html = self.evaluer(dossier)
        # Modèle A et la reprise de Modèle B ; les réponses coupées de Modèle C ne partent jamais au juge
        self.assertEqual(2, judge.request.call_count)
        # Revue PR #540 : la dernière reprise encore coupée n'est pas présentée comme une réponse reçue
        page, _, _ = self.request(dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions')
        bloc = page.visible.split('Modèle par modèle')[1].split('Réponses reçues :')[0]
        self.assertTrue(bloc.split('Modèle C')[1].strip().endswith(': pas de réponse exploitable'), bloc)
        self.assertTrue(bloc.split('Modèle B')[1].split('Modèle C')[0].strip().endswith(': réponse reçue'), bloc)
        # Issue #541 : la ligne de compte ne la compte pas non plus comme une réponse reçue
        self.assertIn('Réponses reçues : 2 sur 3.', page.visible)
        rows = html.split('<tbody role="rowgroup">')[1].split('</tbody>')[0]
        self.assertEqual(2, rows.count('<tr id="attempt-'))
        reprise = next(chunk for chunk in rows.split('<tr id="attempt-')[1:] if '<strong>Modèle B</strong>' in chunk)
        self.assertIn('Reprise après arrêt pour longueur : limite de sortie de 8192 jetons', reprise)
        # Issue #442 : la reprise réussie garde consultables les observations de l'essai coupé
        self.assertIn('Essai d’origine · motif de fin transmis : length · consommation transmise : '
                      'completion_tokens 4096, prompt_tokens 120', reprise)
        self.assertIn('Reprise 1 · motif de fin transmis : inconnu · consommation transmise : inconnue', reprise)
        self.assertNotIn('Aucune réponse exploitable pour Modèle B', html)
        self.assertEqual(1, html.count('Aucune réponse exploitable pour Modèle C'))
        self.assertIn('Aucune réponse exploitable pour Modèle C (arrêt pour longueur, plafond demandé : '
                      '16384 jetons de sortie ; 2 reprises).', html)
        self.assertNotIn('Notre conseil', html)
        self.assertNotIn('rien n’est relancé automatiquement', html)
        # Issue #543 : la projection des évaluations inspecte chaque campagne de la chaîne une seule fois
        dossier_id = dossier.rsplit('/', 1)[1]
        with closing(storage.Store(self.data)) as store, store.read_snapshot() as connection, \
                patch.object(campaigns, '_load', wraps=campaigns._load) as loaded:
            evaluation.projection(store, connection, dossier_id, dossier_id + '-c1')
        loads = [call.args[2] for call in loaded.call_args_list]
        # La comparaison et la reprise de Modèle B : une inspection chacune
        self.assertEqual([1, 1], sorted(map(loads.count, set(loads))))

    def test_reponse_coupee_sans_progression_nomme_l_arret_des_reprises(self):
        """Issue #526 : condition établie de N5 (#517), la reprise rend une réponse coupée plus courte

        Le parcours nominal à trois émissions reste `test_reponse_coupee_reprise_au_plus_deux_fois`.
        Modes d'échec couverts :
        1. la seconde reprise part malgré l'absence de progression, ou la première ne part pas ;
        2. le lecteur voit « 1 reprise » sans le motif qui interdit la suivante ;
        3. une continuation concurrente ou un redémarrage de l'exécuteur émet une tentative de plus ;
        4. consultations, continuation ou redémarrage changent identités, reçus, coûts ou filiation
        """
        from benchmark import runtime
        judge = self.juge_factice()

        def comportement(value, model, limit, suivi):
            if model == 'mistralai/mistral-small-2603':
                value['cost'].update(status='KNOWN', amount='0.05')
                # Comme Qwen 3.8 Flash dans N5 : 706 caractères à 4 096 jetons, 523 à 8 192
                self.coupee(value, 'x' * (706 if limit == 4096 else 523), limit)
        dossier, _, envois, _ = self.lancer_avec_reprises(
            ['openai/gpt-5.6-sol', 'mistralai/mistral-small-2603'], comportement)
        self.assertEqual([('openai/gpt-5.6-sol', 4096), ('mistralai/mistral-small-2603', 4096),
                          ('mistralai/mistral-small-2603', 8192)], envois)
        source_id = dossier.rsplit('/', 1)[1] + '-c1'

        def persisted():
            with closing(storage.Store(self.data)) as store:
                connection = store._connection
                chain = [(cid, campaigns.inspect(store, cid)['manifest'].get('recovery_of'))
                         for (cid,) in connection.execute('SELECT campaign_id FROM s4_campaigns ORDER BY rowid').fetchall()]
                operations = [(op['operation_id'], op['state'], op['requested_configuration']['parameters']['max_tokens'],
                               storage._strict_json(op['receipt']), storage._strict_json(op['observed_cost']))
                              for op in store.inspect_operations() if op['phase'] == 'acquisition']
                return chain, operations, connection.execute('SELECT count(*) FROM s4_emissions').fetchone()[0]
        effects = persisted()
        chain, operations, emissions = effects
        self.assertEqual(3, emissions)
        # Filiation : une seule reprise, liée au reçu coupé à 4 096 jetons ; aucune intention à 16 384
        source = next(op for op in operations if op[2] == 4096 and 'x' * 706 in op[3])
        self.assertEqual([(source_id, None), (chain[1][0], source[0])], chain)
        self.assertEqual([4096, 4096, 8192], sorted(op[2] for op in operations))
        self.assertEqual({'RECEIVED'}, {op[1] for op in operations})
        recovery_id = next(op[0] for op in operations if op[2] == 8192)
        # Continuations concurrentes de l'essai coupé et de sa reprise, puis redémarrage de l'exécuteur
        for operation_id in (source[0], recovery_id):
            execution.continue_preauthorized(self.data, operation_id, transport_factory=self.fabrique,
                                             access_secret=SECRET, access_transport=self.access)
        with closing(storage.Store(self.data)) as store:
            runtime.stop(self.data, store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        self.clock += timedelta(hours=1)
        self.assertEqual([], self.relancer_par_l_executeur())
        self.assertEqual(3, len(envois))
        self.assertEqual(effects, persisted())
        html = self.evaluer(dossier)
        # Seule la réponse exploitable de Modèle A part au juge
        self.assertEqual(1, judge.request.call_count)
        ligne = html.split('Aucune réponse exploitable pour Modèle C')[1].split('. Ce modèle')[0]
        self.assertEqual(' (arrêt pour longueur, plafond demandé : 8192 jetons de sortie ; 1 reprise ; reprise '
                         'suivante non tentée : la reprise n’a pas allongé la réponse coupée)', ligne)
        page, _, _ = self.request(dossier + '/campaigns/' + source_id + '/conditions')
        self.assertIn('Réponses reçues : 1 sur 2.', page.visible)
        self.assertEqual(3, len(envois))
        self.assertEqual(effects[:2], persisted()[:2])

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
        self.assertEqual(2, html.split('<tbody role="rowgroup">')[1].count('<tr id="attempt-'))
        self.assertNotIn('Aucune réponse exploitable', html)
        self.assertNotIn('Notre conseil', html)
        self.assertIn('Une reprise a été demandée pour un modèle.', html)
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        priorite, _, _ = self.request(comparison + '?priority=cost')
        self.assertNotIn('Notre conseil', priorite.visible)
        self.assertIn('Une reprise a été demandée pour un modèle.', priorite.visible)

    def depenses(self, html):
        """Récapitulatif des dépenses de la comparaison : texte entier et lignes de chaque phase"""
        bloc = html.split('<details id="expenses">')[1].split('<details id="method">')[0].rsplit('</details>', 1)[0]
        # La source d'une dépense est une liste `ul.meta` : la ligne se relit avec ses éléments séparés par « · »
        corps_lus = lambda corps: re.sub(r'<ul class="meta">(.*?)</ul>', lambda m: m[1].replace('</li><li>', ' · ').replace('<li>', '').replace('</li>', ''), corps, flags=re.S)
        phases = {titre.split(' : ')[0]: re.findall(r'<li>(.*?)</li>', corps_lus(corps), re.S)
                  for titre, corps in re.findall(r'<h3>(.*?)</h3>(.*?)(?=<h3>|$)', bloc, re.S)}
        return Page(('<details>' + bloc + '</details>').encode()).nodes[0]['text'], phases

    def operations_du_dossier(self):
        """Oracle : une seule comparaison sur un seul dossier, toutes ses opérations lui reviennent"""
        from decimal import Decimal
        with closing(storage.Store(self.data)) as store:
            operations = store.inspect_operations()
        self.assertEqual(1, len({op['dossier_id'] for op in operations}))
        connus = [Decimal(op['observed_cost']['amount']) for op in operations
                  if op['observed_cost'] and op['observed_cost']['status'] == 'KNOWN']
        return operations, sum(connus, Decimal(0))

    def test_cout_complet_relie_chaque_operation_une_seule_fois(self):
        """Issue #441 : le lecteur voit ce que la comparaison a coûté, phase par phase

        Modes d'échec couverts :
        1. une phase (préparation, contrôle de l'exemple, évaluation) manque au récapitulatif ;
        2. une tentative remplacée par une reprise est masquée, ou comptée deux fois ;
        3. le total additionne des montants réservés ou estimés aux dépenses observées ;
        4. les envois hors du tableau sont présentés comme base du conseil ;
        5. le coût observé de chaque réponse change de base dans le tableau
        """
        from benchmark_web.fragments import montant_lisible
        self.juge_factice()

        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
                if limit == 4096:
                    self.coupee(value, None, limit)
            elif model == 'mistralai/mistral-small-2603':
                self.coupee(value, 'Action : relire' + ' | suite' * (limit // 1024), limit)
        dossier, _, envois, _ = self.lancer_avec_reprises(list(self.MAJORANTS), comportement)
        html = self.evaluer(dossier)
        operations, total = self.operations_du_dossier()
        self.assertTrue(all(op['observed_cost']['status'] == 'KNOWN' for op in operations))
        texte, phases = self.depenses(html)
        self.assertIn('Coût complet connu : ' + montant_lisible(str(total)) + ' USD', texte)
        self.assertNotIn('sous-total', texte)
        # Chaque opération du dossier une seule fois, rangée dans sa phase et reconnaissable à sa référence
        self.assertEqual(len(operations), sum(len(lignes) for lignes in phases.values()))
        self.assertEqual(sorted(op['operation_id'][-8:] for op in operations),
                         sorted(re.findall(r'réf\. <code>([^<]+)</code>', html)))
        par_phase = {}
        for op in operations:
            par_phase[op['phase']] = par_phase.get(op['phase'], 0) + 1
        self.assertEqual(len(envois), par_phase['acquisition'])
        self.assertEqual(2, len(phases['Réponses comparées']))
        self.assertEqual(len(envois) - 2, len(phases['Autres envois aux modèles']))
        self.assertEqual(2, par_phase['judgment'])
        self.assertEqual(2, len(phases['Évaluation des réponses']))
        self.assertEqual(par_phase['preparation'] + par_phase.get('correction', 0),
                         len(phases['Préparation de l’exemple']) + len(phases.get('Vérification des modèles choisis', [])))
        self.assertEqual(par_phase['qualification'], len(phases['Contrôle de l’exemple']))
        # Trois reprises : celle de Modèle B est comparée, les deux de Modèle C restent visibles à part
        self.assertEqual([1, 2], [sum('reprise' in ligne for ligne in phases[nom])
                                  for nom in ('Réponses comparées', 'Autres envois aux modèles')])
        # Seules les réponses du tableau portent la base du conseil
        self.assertIn('Leur coût est le coût observé du tableau', texte)
        self.assertEqual(len(phases) - 1, texte.count('Pour information, hors conseil'))
        self.assertNotRegex(texte, r'(?i)par mois|heure de travail|plafond|traceur')
        # Le tableau garde le coût de chaque réponse, sans les autres phases
        lignes = html.split('<tbody role="rowgroup">')[1].split('</tbody>')[0]
        self.assertEqual(['0,05 USD', '0,10 USD'], sorted(re.findall(r'<span class="source-value">(.*?)</span>', lignes)))

    def test_cout_complet_inconnu_annonce_un_sous_total(self):
        """Issue #441 : une dépense nécessaire manque, le total reste inconnu

        Modes d'échec : le sous-total des montants connus est annoncé comme coût complet, la dépense
        manquante est comptée zéro ou remplacée par sa réserve, ou disparaît du récapitulatif
        """
        from benchmark_web.fragments import montant_lisible

        def comportement(value, model):
            if model == 'mistralai/mistral-small-2603':
                value['cost'].update(status='UNKNOWN', amount=None)
        _, html = self.comparer(['Clarté du tableau'],
                                lambda critere, sortie: 'acceptable' if sortie == self.SORTIE_C else 'excellent',
                                comportement=comportement)
        operations, connu = self.operations_du_dossier()
        texte, phases = self.depenses(html)
        self.assertIn('Coût complet inconnu · sous-total connu : ' + montant_lisible(str(connu)) + ' USD', texte)
        self.assertNotIn('Coût complet connu', texte)
        self.assertIn('le sous-total ne donne que la somme des dépenses connues', texte)
        self.assertEqual(len(operations), sum(len(lignes) for lignes in phases.values()))
        inconnue = next(ligne for ligne in phases['Réponses comparées'] if 'Modèle C' in ligne)
        reserve = next(op['reserved_amount'] for op in operations if op['observed_cost']['status'] == 'UNKNOWN')
        self.assertIn('dépense inconnue', inconnue)
        self.assertIn('montant réservé : ' + montant_lisible(reserve) + ' USD', inconnue)

    SORTIE_C = 'Action : relire | Responsable : Camille'
    # Formulations de promesse, pas les mots seuls : « la fiabilité générale n'est pas garantie » reste permis
    PROMESSE = r'(?i)meilleur (modèle|rapport qualité)|plus fiable|fiable en général|fiabilité (assurée|garantie)|garantit (la|une) (fiabilité|réussite)'

    def sans_promesse(self, page):
        """Tout le texte principal, dépliants compris, sans promesse de meilleur modèle ni de fiabilité générale"""
        self.assertNotRegex(next(n for n in page.nodes if n['tag'] == 'main')['text'], self.PROMESSE)

    def comparer(self, qualites, mesure, *, constat=None, ajuste=None, comportement=None,
                 models=('openai/gpt-5.6-sol', 'mistralai/mistral-small-2603'), repetitions=None,
                 qualifier=None, criteria=None):
        """Comparaison sans reprise de modèles satisfaisants, coûts connus sauf `comportement(valeur, modèle)`

        `mesure(critère, sortie)` rend la valeur du juge pour chaque critère de qualité nommé dans
        `qualites` ; la réponse de mistral-small se reconnaît à sa sortie `SORTIE_C`
        """
        self.criteria = criteria or {'eliminatory': [], 'obligations': ['Toutes les actions présentes'], 'quality': [
            {'label': label, 'scale': ['excellent', 'acceptable', 'faible'], 'favorable': 'excellent'} for label in qualites]}
        self.juge_factice(constat or (lambda critere: ('PASS', 'candidate', 'Exigence respectée')),
                          mesure=lambda critere, sortie: mesure(critere['measure'], sortie), ajuste=ajuste)

        def reponse(value, model, limit, suivi):
            if model == 'mistralai/mistral-small-2603':
                value['receipt']['result']['output'] = self.SORTIE_C
            if comportement:
                comportement(value, model)
        dossier, _, envois, _ = self.lancer_avec_reprises(list(models), reponse, qualifier=qualifier,
                                                          repetitions=repetitions)
        self.assertEqual(len(models) * (repetitions or 1), len(envois))
        return dossier, self.evaluer(dossier)

    def test_conseil_retient_la_qualite_puis_le_cout_sur_toute_la_comparaison(self):
        """Témoin des abstentions : conditions réunies, le conseil est donné et la page reste située

        Modes d'échec couverts :
        1. aucun conseil alors que ses conditions sont réunies, ce qui rendrait les abstentions muettes ;
        2. le coût l'emporte sur une qualité observée meilleure ;
        3. un filtre d'affichage change le conseil ;
        4. le détail d'une réponse, ouvert seul, ne dit plus que le verdict porte sur la configuration
           observée, sur un exemple inventé ;
        5. le résultat ou le détail promet un meilleur modèle ou une fiabilité générale
        """
        from benchmark.restitution import ATTRIBUTION, LIMIT

        def comportement(value, model):
            # La meilleure qualité coûte plus cher
            if model == 'openai/gpt-5.6-sol':
                value['cost'].update(amount='0.30')
        dossier, html = self.comparer(['Clarté du tableau'],
                                      lambda critere, sortie: 'acceptable' if sortie == self.SORTIE_C else 'excellent',
                                      comportement=comportement)
        conseil = Page(html.split('<aside class="economic-choice"')[1].split('</aside>')[0].encode())
        self.assertIn('Modèle A', conseil.visible)
        self.assertIn('Nous retenons d’abord la', conseil.visible)
        self.assertIn('À qualité égale, le coût observé le plus bas l’emporte.', conseil.visible)
        # Sur téléphone, la portée du conseil se replie sous « Détail du conseil » (critère K1)
        self.assertIn('Ce conseil repose sur un seul exemple.', conseil.depliant('Détail du conseil'))
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        _, _, raw = self.request(comparison, json_response=True)
        self.assertCountEqual([('excellent', 1), ('acceptable', 2)], [
            (row['measures'][0]['value'], row['measures'][0]['rank']) for row in json.loads(raw)['rows']])
        options = re.search(r'name="configuration">(.*?)</select>', html).group(1)
        filtre = re.findall(r'<option value="([^"]+)">Modèle C', options)[0]
        filtree, _, raw = self.request(comparison + '?configuration=' + filtre)
        self.assertIn('1 réponse affichée sur 2', filtree.visible)
        self.assertEqual(html.split('<aside class="economic-choice"')[1].split('</aside>')[0],
                         raw.decode().split('<aside class="economic-choice"')[1].split('</aside>')[0])
        page, _, _ = self.request(filtree.link('Détail et preuves'))
        self.examine(page, comparison, 'détail ouvert seul', None)
        self.assertIn('Modèle C', page.visible)
        # La portée du verdict est longue : elle se replie, alors que la limite de l'exemple reste visible
        self.assertNotIn(ATTRIBUTION, page.visible)
        self.assertIn(ATTRIBUTION, page.depliant('Portée de ce verdict'))
        self.assertIn(LIMIT, page.visible)
        self.assertIn('Comparaison : réponses évaluées : 2 · essais lancés : 2 sur 2.', page.visible)
        resultats = Page(html.encode())
        self.assertIn(ATTRIBUTION, next(n for n in resultats.nodes if n['attrs'].get('id') == 'method')['text'])
        self.assertIn('Réponses évaluées : 2 · essais lancés : 2 sur 2.', resultats.visible)
        apercu, _, _ = self.request(comparison + '/preview')
        self.assertIn(ATTRIBUTION, apercu.visible)
        self.assertIn(LIMIT, apercu.visible)
        self.assertIn('Réponses évaluées : 2 · essais lancés : 2 sur 2.', apercu.visible)
        for vue in (resultats, page, apercu):
            self.sans_promesse(vue)

    def test_portee_de_l_etalonnage_dans_le_detail_d_un_resultat(self):
        """Issue #450 : le détail d'un résultat dit à quelle identité de juge une fiche d'étalonnage s'applique

        Modes d'échec couverts :
        1. aucune fiche : le détail promet une qualification ou affiche un badge ;
        2. une fiche d'une autre version de méthode ou d'un autre juge servi s'applique en silence ;
        3. une décision « non qualifiée » se lit comme une qualification ;
        4. la fiche expose des annotations, justifications ou désaccords privés ;
        5. la lecture écrit ou réévalue : une opération ou une évaluation change ;
        6. une fiche mal formée est acceptée (champ en trop, statut inconnu, limites vides, date non canonique) ;
           deux fiches pour la même identité de juge sont départagées en silence ;
        7. un résultat sans juge assisté ou à identité illisible reçoit une fiche ou casse la page ;
        8. une fiche qualifie un dossier que son périmètre ne liste pas, ou le périmètre d'une fiche mal formée
           (liste vide, doublon, identifiant invalide, texte seul) est accepté
        """
        from benchmark import automatic_judgment as auto, calibration
        dossier, html = self.comparer(['Clarté du tableau'], lambda critere, sortie: 'excellent')
        detail = Page(html.encode()).link('Détail et preuves')
        with closing(storage.Store(self.data)) as store:
            juges = [op for op in store.inspect_operations() if op['engine_version'] == auto.FORMAT]
            avant = storage._strict_json(store.inspect_operations())
        identite = calibration.judge_identity(juges[0])
        dossier_id = juges[0]['dossier_id']

        def fiche(**changes):
            return dict(format=calibration.DECISION_FORMAT, decision_id='decision-fictive', decided_at='2026-10-04',
                        authority='Décision fictive de test', status='QUALIFIED', judge_identity=identite,
                        batch=dict(batch_id='lot-fictif', batch_sha256='a' * 64),
                        perimeter=dict(description='Exemples fictifs de test uniquement', dossier_ids=[dossier_id]),
                        limits=['Fiche fictive : aucune qualification d’un modèle réel']) | changes

        def lire(*fiches):
            with tempfile.TemporaryDirectory() as dossier_fiches:
                for index, valeur in enumerate(fiches):
                    (Path(dossier_fiches) / f'fiche-{index}.json').write_text(json.dumps(valeur), encoding='utf-8')
                with patch.object(calibration, 'DECISIONS_DIR', Path(dossier_fiches)):
                    page, _, brut = self.request(detail)
            self.sans_promesse(page)
            return page, brut.decode()

        autre_methode = dict(identite, method=dict(identite['method'], version='autre-version'))
        autre_juge = dict(identite, observed=dict(identite['observed'], provider='autre-fournisseur'))
        sans, _ = lire()
        self.assertIn('Aucun étalonnage enregistré pour ce juge et cette méthode.', sans.visible)
        self.assertNotRegex(sans.visible, r'(?i)certifi|qualifiée pour')
        qualifiee, brut = lire(fiche())
        self.assertIn('Méthode qualifiée pour le périmètre déclaré', qualifiee.visible)
        self.assertIn('Exemples fictifs de test uniquement', qualifiee.visible)
        self.assertIn('4 octobre 2026', qualifiee.visible)
        self.assertIn('Ce dossier fait partie de ce périmètre.', qualifiee.visible)
        self.assertNotIn('pas recoupé', qualifiee.visible)
        for autre in (autre_methode, autre_juge):
            page, _ = lire(fiche(judge_identity=autre))
            self.assertIn('Un étalonnage existe pour une autre version ou un autre juge ; il ne s’applique pas à ce résultat.',
                          page.visible)
            self.assertNotIn('Exemples fictifs de test uniquement', page.visible)
            self.assertNotIn('Méthode qualifiée', page.visible)
        for hors in ('autre-dossier', 'fixture'):
            page, _ = lire(fiche(perimeter=dict(description='Périmètre d’un autre dossier', dossier_ids=[hors])))
            self.assertIn('mais son périmètre ne couvre pas ce dossier ; il ne s’applique pas à ce résultat.', page.visible)
            self.assertNotIn('Périmètre d’un autre dossier', page.visible)
            self.assertNotIn('Méthode qualifiée', page.visible)
        refusee, _ = lire(fiche(status='NOT_QUALIFIED'))
        self.assertIn('Étalonnage examiné : qualification non retenue', refusee.visible)
        self.assertNotIn('Méthode qualifiée', refusee.visible)
        bloc = next(n for n in Page(brut.encode()).nodes if n['attrs'].get('class') == 'calibration')['text']
        self.assertIn('decision-fictive', bloc)
        self.assertIn('Fiche fictive : aucune qualification d’un modèle réel', bloc)
        self.assertNotRegex(bloc, r'(?i)annotation|justification|disagreement|désaccord|exemple de sortie')
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(avant, storage._strict_json(store.inspect_operations()))
        # Une fiche illisible ne casse pas la page et ne s'applique à rien
        with tempfile.TemporaryDirectory() as dossier_fiches:
            (Path(dossier_fiches) / 'fiche.json').write_text('{', encoding='utf-8')
            with patch.object(calibration, 'DECISIONS_DIR', Path(dossier_fiches)):
                illisible, _, _ = self.request(detail)
        self.assertIn('Les fiches d’étalonnage ne sont pas lisibles', illisible.visible)
        # Fiches mal formées ou ambiguës : refusées à la lecture, jamais ignorées ni départagées
        autre = fiche(decision_id='decision-bis')
        perimetre = lambda **valeurs: fiche(perimeter=dict(description='Périmètre', dossier_ids=[dossier_id]) | valeurs)
        for mal_formee in ((fiche(annotations=[]),), (fiche(status='QUALIFIEE'),), (fiche(limits=[]),),
                           (fiche(decided_at='4 octobre'),), (fiche(decided_at='20261004'),),
                           (fiche(decided_at='2026-W40-7'),), (fiche(batch=dict(batch_id='lot', batch_sha256='abc')),),
                           (fiche(judge_identity=dict(identite, extra=1)),), (fiche(format='autre'),),
                           (fiche(), autre), (fiche(), dict(autre, decision_id='decision-fictive')),
                           (fiche(perimeter='Texte seul'),), (perimetre(dossier_ids=[]),),
                           (perimetre(dossier_ids=[dossier_id, dossier_id]),), (perimetre(dossier_ids=['pas valide!']),),
                           (perimetre(dossier_ids=dossier_id),), (perimetre(description=''),),
                           (fiche(perimeter=dict(description='Périmètre')),)):
            with self.subTest(fiches=mal_formee), tempfile.TemporaryDirectory() as dossier_fiches:
                for index, valeur in enumerate(mal_formee):
                    (Path(dossier_fiches) / f'fiche-{index}.json').write_text(json.dumps(valeur), encoding='utf-8')
                with patch.object(calibration, 'DECISIONS_DIR', Path(dossier_fiches)), self.assertRaises(ValueError):
                    calibration.load_decisions()
        # Sans juge assisté ou identité illisible : état distinct, aucune fiche appliquée
        with tempfile.TemporaryDirectory() as dossier_fiches:
            (Path(dossier_fiches) / 'fiche.json').write_text(json.dumps(fiche()), encoding='utf-8')
            with patch.object(calibration, 'DECISIONS_DIR', Path(dossier_fiches)):
                self.assertEqual('SANS_JUGE_ASSISTE', calibration.scope(None)['state'])
                self.assertEqual('IDENTITE_ILLISIBLE', calibration.scope(dict(juges[0], resources=['{}']))['state'])
                self.assertEqual('QUALIFIEE', calibration.scope(juges[0])['state'])

    def test_conseil_departage_par_le_cout_a_qualite_egale(self):
        def comportement(value, model):
            if model == 'openai/gpt-5.6-sol':
                value['cost'].update(amount='0.30')
        _, html = self.comparer(['Clarté du tableau'], lambda critere, sortie: 'excellent', comportement=comportement)
        conseil = Page(html.split('<aside class="economic-choice"')[1].split('</aside>')[0].encode())
        self.assertIn('Modèle C', conseil.visible)
        self.assertIn('Les 2 réponses qui satisfont l’exemple ont la même qualité observée. Nous retenons la moins coûteuse.',
                      conseil.visible)
        # Issue #441 : le coût complet s'affiche à côté du conseil sans changer sa base
        self.assertIn('Coûtobservé0,10USD', conseil.visible.replace(' ', ''))
        self.assertIn('Coût complet connu', self.depenses(html)[0])

    def test_priorite_explicite_departage_les_compromis(self):
        """Issue #326 : le lecteur choisit une priorité explicite, visible et réversible

        Modes d'échec couverts :
        1. le conseil par défaut change, ou une priorité effacée ne revient pas au défaut ;
        2. le coût écarte une qualité meilleure sans montrer le compromis secondaire ;
        3. le critère choisi ignore son sens favorable, ou un autre critère départage implicitement ;
        4. un filtre masque la ligne conseillée et le conseil est recalculé sur la population filtrée ;
        5. une priorité inconnue ou un critère absent est remplacé en silence par le défaut ;
        6. la consultation modifie les preuves, coûts, verdicts ou appelle un fournisseur ;
        7. le parcours privé s'ouvre sans session, ou la restitution publique accepte cette priorité
        """
        def mesure(critere, sortie):
            if critere == 'Clarté du tableau':
                return 'acceptable' if sortie == self.SORTIE_C else 'excellent'
            return 'excellent' if sortie == self.SORTIE_C else 'acceptable'

        def comportement(value, model):
            if model == 'openai/gpt-5.6-sol':
                value['cost'].update(amount='0.30')

        dossier, defaut = self.comparer(['Clarté du tableau', 'Concision du tableau'], mesure,
                                        comportement=comportement)
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        with closing(storage.Store(self.data)) as store:
            operations = store.inspect_operations()
            database = list(store._connection.iterdump())
        appels = list(self.calls)
        # Sans priorité : le compromis croisé reste non départagé
        self.assertIn('Pas de conseil', defaut)
        self.assertIn('Aucune réponse n’est la meilleure sur tous les critères de qualité.', defaut)
        _, _, raw = self.request(comparison, json_response=True)
        initial = json.loads(raw)
        self.assertIsNone(initial['priority'])
        self.assertIsNone(initial['recommendation'])
        self.assertEqual('tradeoff', initial['abstention']['reason'])
        quality = [column for column in initial['columns'] if 'criterion_id' in column]
        self.assertEqual(['Clarté du tableau', 'Concision du tableau'],
                         [column['definition']['measure'] for column in quality])
        clarte, concision = quality
        # Priorité au coût : la réponse conforme la moins chère, avec le compromis de clarté
        cout, _, raw = self.request(comparison + '?priority=cost')
        self.assertIn('Notre conseil', cout.visible)
        self.assertIn('Modèle C', cout.visible)
        self.assertIn('Priorité choisie : coût observé.', cout.visible)
        # Le compromis et sa règle restent dans la page, repliés sous « Détail du conseil » (critère K1)
        self.assertIn('Compromis sur « Clarté du tableau » : la réponse retenue vaut acceptable, '
                      'alors que Modèle A vaut excellent.', cout.depliant('Détail du conseil'))
        self.assertIn('aucune obligation non satisfaite n’est compensée', cout.depliant('Détail du conseil'))
        self.assertIn('Revenir au conseil par défaut', cout.visible)
        self.assertIn('Coûtobservé0,10USD', cout.visible.replace(' ', ''))
        _, _, raw = self.request(comparison + '?priority=cost', json_response=True)
        cout_json = json.loads(raw)
        self.assertEqual(('cost', 'priority_cost', 'mistralai/mistral-small-2603', '0.10', 'USD'), (
            cout_json['priority'], cout_json['recommendation']['basis'],
            cout_json['recommendation']['configuration']['model'],
            cout_json['recommendation']['amount'], cout_json['recommendation']['unit']))
        self.assertEqual('Clarté du tableau', cout_json['recommendation']['compromises'][0]['measure'])
        # Priorité à la clarté : le sens favorable du contrat, puis le coût seulement pour ses égalités
        clarte_page, _, _ = self.request(comparison + '?priority=' + clarte['id'])
        self.assertIn('Notre conseil', clarte_page.visible)
        self.assertIn('Modèle A', clarte_page.visible)
        self.assertIn('Priorité choisie : Clarté du tableau.', clarte_page.visible)
        self.assertIn('Compromis sur « Concision du tableau » : la réponse retenue vaut acceptable, '
                      'alors que Modèle C vaut excellent.', clarte_page.depliant('Détail du conseil'))
        self.assertIn('Coûtobservé0,30USD', clarte_page.visible.replace(' ', ''))
        _, _, raw = self.request(comparison + '?priority=' + clarte['id'], json_response=True)
        self.assertEqual(('priority_criterion', 'openai/gpt-5.6-sol'),
                         (json.loads(raw)['recommendation']['basis'],
                          json.loads(raw)['recommendation']['configuration']['model']))
        # Priorité à la concision : l'autre réponse l'emporte, sans départage par la clarté
        concision_page, _, _ = self.request(comparison + '?priority=' + concision['id'])
        self.assertIn('Modèle C', concision_page.visible)
        self.assertIn('Priorité choisie : Concision du tableau.', concision_page.visible)
        self.assertIn('Compromis sur « Clarté du tableau » : la réponse retenue vaut acceptable, '
                      'alors que Modèle A vaut excellent.', concision_page.depliant('Détail du conseil'))
        # Effacement : le défaut revient sans substitution implicite
        efface, _, raw = self.request(comparison + '?priority=')
        self.assertIn('Pas de conseil', efface.visible)
        self.assertIn('Aucune réponse n’est la meilleure sur tous les critères de qualité.', efface.visible)
        self.assertRegex(raw.decode().split('<select id="filter-priority"', 1)[1].split('</select>', 1)[0],
                         r'<option value="" selected>Conseil par défaut</option>')
        # Le lien de retour garde le défaut, et le formulaire garde la priorité pendant la consultation
        retour = cout.link('Revenir au conseil par défaut')
        self.assertEqual(comparison + '#filters', retour)
        self.assertIn('Pas de conseil', self.request(retour)[0].visible)
        options = re.search(r'name="configuration">(.*?)</select>', defaut).group(1)
        filtre = re.findall(r'<option value="([^"]+)">Modèle A', options)[0]
        filtree, _, filtree_raw = self.request(comparison + '?priority=cost&configuration=' + filtre)
        self.assertIn('1 réponse affichée sur 2', filtree.visible)
        self.assertIn('Notre conseil', filtree.visible)
        self.assertIn('Modèle C', filtree.visible)
        self.assertIn('Modèle A', filtree_raw.decode().split('<tbody role="rowgroup">')[1].split('</tbody>')[0])
        self.assertNotIn('Modèle C</strong>', filtree_raw.decode().split('<tbody role="rowgroup">')[1].split('</tbody>')[0])
        self.assertEqual({'priority': 'cost', 'configuration': filtre},
                         json.loads(self.request(comparison + '?priority=cost&configuration=' + filtre,
                                                 json_response=True)[2])['filter_scope'])
        detail_href = filtree.link('Détail et preuves')
        attempt_id = re.search(r'/attempts/([^?]+)', detail_href).group(1)
        detail = self.request(detail_href)[0]
        self.assertEqual(comparison + '?priority=cost&configuration=' + filtre + '#attempt-' + attempt_id,
                         detail.link('Revenir aux résultats'))
        # Priorité invalide ou critère absent : refus explicite, jamais le conseil par défaut
        for query in ('inconnu', 'Q3', 'case', 'cost&priority=cost'):
            with self.subTest(query=query):
                self.request(comparison + '?priority=' + query, status=400)
        # Le parcours privé et la restitution publique restent fermés à ce paramètre
        self.request(comparison + '?priority=cost', cookies=SimpleCookie(), status=403)
        with self.assertRaises(ValueError):
            public_results.query_parameters('priority=cost')
        # La consultation n'écrit rien, n'émet aucun appel et ne lance aucun travail
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(operations, store.inspect_operations())
            self.assertEqual(database, list(store._connection.iterdump()))
        self.assertEqual(appels, self.calls)
        self.assertTrue(self.starts.empty())

        # Deux configurations du même modèle, efforts low puis high : le compromis nomme celle
        # qui obtient la meilleure qualité
        from tests.test_s3_regressions import fixture
        _, view, _ = fixture(self.data)
        with closing(storage.Store(self.data)) as store:
            reference = store._connection.execute(
                "SELECT piece_id FROM pieces WHERE dossier_id='fixture' AND revision=? AND role='judge'",
                (view['revision'],)).fetchone()[0]
            sid, _, token = prep.session(store, None, create=True)
            for table in ('s2_dossiers', 's2_validations'):
                store._connection.execute(f'UPDATE {table} SET session_id=? WHERE dossier_id=?', (sid, 'fixture'))
            cookies = SimpleCookie('benchmark_session=' + token)
            spec = specification(reference)
            spec['secondary_criteria'] = [dict(id='clarte', measure='Éléments vérifiés', proof='Sortie conservée',
                                               unit='éléments', favorable='higher', aggregation=None)]
            candidate = qualification.draft(store, 'fixture', view['revision'], spec)
            qualified = qualification.qualify(store, candidate['contract_sha256'], reviewer=ACTOR, check=check)
            qualification.approve(store, candidate['contract_sha256'], qualified['qualification_id'],
                                  actor=ACTOR, authority=AUTHORITY)
            store.create_budget('efforts-distincts', '40', 'TEST')
            value = manifest(candidate, 'efforts-distincts')
            shared = dict(value['panel'][0], id='x-low', model='openai/gpt-5.6-sol', effort='low',
                          channel_id='fixture-channel-x-low', parameters={'reasoning': {'effort': 'low'}})
            high = dict(shared, id='x-high', effort='high', channel_id='fixture-channel-x-high',
                        parameters={'reasoning': {'effort': 'high'}})
            value['panel'] = [shared, high, dict(value['panel'][1], id='y', channel_id='fixture-channel-y')]
            cells = ['x-low', 'x-high', 'y']
            value['plan'] = [dict(cell_id=cell, case_id='notes', configuration_id=cell) for cell in cells]
            value['attempt_policy'] = dict(retries=False, order=cells, reason='Declared local order')
            snapshot = campaigns.create(store, value)
            authority, evidence = inputs(snapshot, cells=cells, budget='efforts-distincts')
            authority['reserve_amounts'] = {cell: '7' for cell in cells}
            campaigns.admit(store, 'efforts-distincts', authority, evidence)
            costs = {'fixture-channel-x-low': '0.10', 'fixture-channel-y': '0.20', 'fixture-channel-x-high': '0.30'}
            clarity = {'x-low': '1', 'y': '1', 'x-high': '2'}

            def report(ctx, resources):
                configuration = next(cell['configuration_id'] for cell in ctx['campaign']['cells']
                                     if cell['cell_id'] == ctx['attempt']['cell_id'])
                piece = ctx['attempt']['output_piece_id']
                proof = [dict(piece_id=piece, sha256=sha256(resources[piece]).hexdigest(),
                              passage=resources[piece].decode())]
                result = findings(ctx, resources)
                result['measures'] = [dict(criterion_id=definition['id'], value=clarity[configuration],
                                           unit=definition['unit'], evidence=proof)
                                      for definition in ctx['qualification']['contract']['specification']['secondary_criteria']]
                return result

            def transport(operation, request):
                channel = request['requested_configuration']['channel_id']
                value = response(operation, request)
                value['receipt']['result']['output'] = 'Action : relire | Responsable : Camille · ' + channel
                value['cost'].update(status='KNOWN', amount=costs[channel], currency='TEST')
                return value

            for cell in cells:
                attempts = campaigns.reserve(store, 'efforts-distincts', cell, 'attempt-' + cell)
                execution.execute(self.data, attempts['operation_id'], transport)
                evaluation.evaluate(store, 'efforts-distincts', attempts['operation_id'], responsible=RESPONSIBLE,
                                    authority=EVALUATION_AUTHORITY, check=report)
            campaigns.stop(store, 'efforts-distincts')
        ambiguous, _, ambiguous_raw = self.request('/preparation/dossiers/fixture/campaigns/efforts-distincts?priority=cost',
                                                   cookies=cookies)
        self.assertIn('Notre conseil', ambiguous.visible)
        self.assertIn('Modèle A', ambiguous.visible)
        tradeoff = re.search(r'<p class="priority-tradeoff">(.*?)</p>', ambiguous_raw.decode()).group(1)
        self.assertIn('Modèle A · Niveau de raisonnement : Élevé (high) vaut', tradeoff)
        self.assertNotIn('alors que Modèle A vaut', tradeoff)
        self.assertNotIn('Faible (low)', tradeoff)

    def test_conseil_exige_un_cas_complet_et_plusieurs_reponses_satisfaisantes(self):
        """Le vrai serveur restitue les preuves sans les modifier

        Échecs couverts : conseil avec plusieurs cas, cellule non lancée ou une seule réponse
        satisfaisante ; consultation qui modifie les observations ; sens numérique inversé, précision
        perdue, coût préféré à la qualité, compromis de sens opposés ou égalité départagée arbitrairement

        Issue #458 (décision D06 d'Ayo) : sur plusieurs exemples, le conseil exige que chaque exemple
        désigne la même configuration, sans somme des coûts ; chaque absence de conseil dit sa raison
        """
        from copy import deepcopy
        from hashlib import sha256
        from benchmark import evaluation, preparation, qualification
        from tests.test_s3_regressions import ACTOR, AUTHORITY, check, fixture, specification
        from tests.test_s4_regressions import inputs, manifest
        from tests.test_s5_regressions import RESPONSIBLE, EVALUATION_AUTHORITY, findings

        _, view, reference = fixture(self.data)
        with closing(storage.Store(self.data)) as store:
            sid, _, token = preparation.session(store, None, create=True)
            for table in ('s2_dossiers', 's2_validations'):
                store._connection.execute(f'UPDATE {table} SET session_id=? WHERE dossier_id=?', (sid, 'fixture'))
            cookies = SimpleCookie('benchmark_session=' + token)
            for directions, scenarios in (
                    (('higher',), ('complete', 'multi-case', 'multi-agree', 'multi-disagree', 'unstarted', 'pending',
                                    'single-eligible')),
                    (('lower',), ('complete', 'equal-quality', 'equal-final', 'precise')),
                    (('higher', 'lower'), ('complete', 'tradeoff', 'multi-tradeoff'))):
                spec = specification(reference)
                spec['secondary_criteria'] = [dict(id=direction, measure='Quantité fictive' if direction == 'higher' else 'Durée fictive',
                    proof='Sortie conservée', unit='éléments' if direction == 'higher' else 's', favorable=direction, aggregation=None)
                    for direction in directions]
                candidate = qualification.draft(store, 'fixture', view['revision'], spec)
                qualified = qualification.qualify(store, candidate['contract_sha256'], reviewer=ACTOR, check=check)
                qualification.approve(store, candidate['contract_sha256'], qualified['qualification_id'], actor=ACTOR, authority=AUTHORITY)
                for scenario in scenarios:
                    with self.subTest(directions=directions, scenario=scenario):
                        campaign_id = "-".join(directions) + "-" + scenario
                        store.create_budget(campaign_id, '100', 'TEST')
                        m = manifest(candidate, campaign_id)
                        if scenario == 'multi-case':
                            m['cases'].append(dict(id='other', package_sha256=candidate['contract']['package_sha256']))
                            m['plan'][1]['case_id'] = 'other'
                        # Deux exemples, chacun avec les deux modèles
                        both = scenario in ('multi-agree', 'multi-disagree', 'multi-tradeoff')
                        if both:
                            m['cases'].append(dict(id='other', package_sha256=candidate['contract']['package_sha256']))
                            m['plan'] += [dict(cell_id=ident + '2', case_id='other', configuration_id=ident) for ident in ('x', 'y')]
                            m['attempt_policy']['order'] += ['x2', 'y2']
                        if scenario in ('unstarted', 'pending'):
                            m['panel'].append(dict(deepcopy(m['panel'][0]), id='z'))
                            m['plan'].append(dict(cell_id='z', case_id='notes', configuration_id='z'))
                            m['attempt_policy']['order'].append('z')
                        snapshot = campaigns.create(store, m)
                        cells = ['x', 'y', 'z'] if scenario == 'pending' else ['x', 'y', 'x2', 'y2'] if both else ['x', 'y']
                        authority, evidence = inputs(snapshot, budget=campaign_id, cells=cells)
                        authority['reserve_amounts'] = {cell: '7' for cell in cells}
                        campaigns.admit(store, campaign_id, authority, evidence)
                        for cell in cells:
                            attempt = campaign_id + '-' + cell
                            campaigns.reserve(store, campaign_id, cell, attempt)
                            def candidate_response(operation, request):
                                value = response(operation, request)
                                value['cost']['amount'] = '3' if cell == 'x' and scenario != 'equal-final' else '2'
                                return value

                            execution.execute(self.data, attempt, candidate_response)
                            if cell == 'z':
                                continue

                            def report(ctx, resources):
                                result = findings(ctx, resources)
                                if scenario == 'single-eligible' and cell == 'y':
                                    result['findings'][0].update(status='FAIL', attribution='candidate', finding='Action omise')
                                piece = ctx['attempt']['output_piece_id']
                                result['measures'] = []
                                for definition in spec['secondary_criteria']:
                                    # multi-tradeoff croise les qualités sur l'exemple 2 seulement
                                    crossed = scenario == 'tradeoff' or (scenario == 'multi-tradeoff' and cell.endswith('2'))
                                    values = ('2', '1') if definition['favorable'] == 'higher' or crossed else ('1', '2')
                                    if scenario in ('equal-quality', 'equal-final'):
                                        values = ('1', '1')
                                    if scenario == 'precise':
                                        values = ('1.0000000000000000000000000001', '1.0000000000000000000000000002')
                                    # L'exemple 2 de multi-disagree inverse les qualités : y y devient meilleur
                                    worse = cell.startswith('y') != (scenario == 'multi-disagree' and cell.endswith('2'))
                                    result['measures'].append(dict(criterion_id=definition['id'], value=values[worse],
                                        unit=definition['unit'], evidence=[dict(piece_id=piece, sha256=sha256(resources[piece]).hexdigest(),
                                                                             passage=resources[piece].decode())]))
                                return result

                            evaluation.evaluate(store, campaign_id, attempt, responsible=RESPONSIBLE,
                                                authority=EVALUATION_AUTHORITY, check=report)
                        before = store.inspect_operations()
                        path = '/preparation/dossiers/fixture/campaigns/' + campaign_id
                        _, _, raw = self.request(path, cookies=cookies, json_response=True)
                        result = json.loads(raw)
                        page, _, _ = self.request(path, cookies=cookies)
                        self.assertEqual(before, store.inspect_operations())
                        advised = scenario in ('complete', 'precise', 'equal-quality', 'multi-agree')
                        self.assertEqual(advised, 'Notre conseil' in page.visible)
                        self.assertEqual(not advised, 'Pas de conseil' in page.visible)
                        if scenario == 'multi-agree':
                            # Un coût par exemple, jamais leur somme
                            choice = result['recommendation']
                            self.assertEqual('fictional-x', choice['configuration']['model'])
                            self.assertNotIn('amount', choice)
                            self.assertEqual([('notes', '3', 'quality_then_cost'), ('other', '2', 'quality_then_cost')],
                                             [(c['case_id'], c['amount'], c['basis']) for c in choice['cases']])
                            aside = next(n for n in page.nodes if n['attrs'].get('class') == 'economic-choice')['text']
                            self.assertIn('Exemple 1 : 3 TEST', aside)
                            self.assertIn('Exemple 2 : 2 TEST', aside)
                            self.assertIn('Ce conseil repose sur 2 exemples', aside)
                            self.assertIsNone(result['abstention'])
                        elif advised:
                            winner = 'y' if scenario == 'equal-quality' else 'x'
                            basis = 'equal_quality_cost' if scenario == 'equal-quality' else 'quality_then_cost'
                            amount = '2' if winner == 'y' else '3'
                            self.assertIn('fictional-' + winner, next(n for n in page.nodes if n['attrs'].get('class') == 'economic-choice')['text'])
                            self.assertEqual(('fictional-' + winner, basis, amount, 'TEST', 2), (
                                result['recommendation']['configuration']['model'], result['recommendation']['basis'],
                                result['recommendation']['amount'], result['recommendation']['unit'], result['recommendation']['count']))
                        else:
                            self.assertIsNone(result['recommendation'])
                            # Raison et exemple nommés : multi-case n'a qu'une réponse sur chaque exemple
                            expected = {'multi-case': ('few', 'notes'), 'multi-disagree': ('disagreement', None),
                                        'unstarted': ('incomplete', None), 'pending': ('incomplete', None),
                                        'single-eligible': ('few', None), 'equal-final': ('tie', None),
                                        'tradeoff': ('tradeoff', None), 'multi-tradeoff': ('tradeoff', 'other')}[scenario]
                            self.assertEqual(expected, (result['abstention']['reason'], result['abstention']['case_id']))
                            if scenario == 'multi-disagree':
                                self.assertEqual([('notes', 'fictional-x'), ('other', 'fictional-y')],
                                                 [(c['case_id'], c['configuration']['model'])
                                                  for c in result['abstention']['choices']])
                                self.assertIn('Les exemples ne désignent pas le même modèle', page.visible)
                            if scenario == 'multi-case':
                                self.assertIn('Exemple 1 : moins de deux réponses satisfont l’exemple.', page.visible)
                            if scenario == 'multi-tradeoff':
                                self.assertIn('Exemple 2 : aucune réponse n’est la meilleure sur tous les critères de qualité.',
                                              page.visible)
                        # La même priorité s'applique séparément à chaque cas, sans somme ni moyenne
                        priorite = None
                        if directions == ('lower',) and scenario == 'complete':
                            priorite = ('complete', path + '?priority=lower')
                        elif directions == ('lower',) and scenario == 'equal-quality':
                            priorite = ('equal-quality', path + '?priority=lower')
                        elif directions == ('lower',) and scenario == 'equal-final':
                            priorite = ('equal-final', path + '?priority=lower')
                        elif scenario == 'multi-agree':
                            priorite = ('agree', path + '?priority=higher')
                        elif scenario == 'multi-disagree':
                            priorite = ('disagree', path + '?priority=higher')
                        elif scenario in ('multi-case', 'single-eligible'):
                            priorite = ('few', path + '?priority=cost')
                        elif scenario in ('unstarted', 'pending'):
                            priorite = ('incomplete', path + '?priority=cost')
                        if priorite:
                            kind, priorite_path = priorite
                            _, _, priorite_raw = self.request(priorite_path, cookies=cookies, json_response=True)
                            choix = json.loads(priorite_raw)
                            priorite_page, _, _ = self.request(priorite_path, cookies=cookies)
                            self.assertEqual(before, store.inspect_operations())
                            if kind == 'complete':
                                # Le critère décroissant l'emporte sur un coût plus bas
                                self.assertEqual(('fictional-x', 'priority_criterion', '3'),
                                                 (choix['recommendation']['configuration']['model'],
                                                  choix['recommendation']['basis'], choix['recommendation']['amount']))
                                self.assertIn('Priorité choisie : Durée fictive.', priorite_page.visible)
                            elif kind == 'equal-quality':
                                # Valeurs égales sur le critère : le coût départage seulement cette égalité
                                self.assertEqual(('fictional-y', 'priority_criterion', '2'),
                                                 (choix['recommendation']['configuration']['model'],
                                                  choix['recommendation']['basis'], choix['recommendation']['amount']))
                            elif kind == 'equal-final':
                                # Même valeur et même coût : aucun autre critère ne départage
                                self.assertIsNone(choix['recommendation'])
                                self.assertEqual(('criterion_tie', None),
                                                 (choix['abstention']['reason'], choix['abstention']['case_id']))
                                self.assertIn('Plusieurs réponses ont la meilleure valeur sur le critère choisi '
                                              'et le même coût observé.', priorite_page.visible)
                            elif kind == 'agree':
                                self.assertEqual('fictional-x', choix['recommendation']['configuration']['model'])
                                self.assertNotIn('amount', choix['recommendation'])
                                self.assertEqual([('notes', '3', 'priority_criterion'),
                                                  ('other', '2', 'priority_criterion')],
                                                 [(item['case_id'], item['amount'], item['basis'])
                                                  for item in choix['recommendation']['cases']])
                                self.assertIn('Ce conseil repose sur 2 exemples', priorite_page.depliant('Détail du conseil'))
                            elif kind == 'disagree':
                                self.assertIsNone(choix['recommendation'])
                                self.assertEqual('disagreement', choix['abstention']['reason'])
                                self.assertEqual([('notes', 'fictional-x'), ('other', 'fictional-y')],
                                                 [(item['case_id'], item['configuration']['model'])
                                                  for item in choix['abstention']['choices']])
                                self.assertIn('Les exemples ne désignent pas le même modèle', priorite_page.visible)
                            elif kind == 'few':
                                self.assertIsNone(choix['recommendation'])
                                self.assertEqual(('few', 'notes' if scenario == 'multi-case' else None),
                                                 (choix['abstention']['reason'], choix['abstention']['case_id']))
                            elif kind == 'incomplete':
                                self.assertIsNone(choix['recommendation'])
                                self.assertEqual(('incomplete', None),
                                                 (choix['abstention']['reason'], choix['abstention']['case_id']))
                        if scenario == 'multi-disagree':
                            # Le coût est appliqué cas par cas : l'exemple 2 départage deux coûts égaux
                            _, _, priorite_raw = self.request(path + '?priority=cost', cookies=cookies,
                                                              json_response=True)
                            choix = json.loads(priorite_raw)
                            self.assertIsNone(choix['recommendation'])
                            self.assertEqual(('cost_tie', 'other'),
                                             (choix['abstention']['reason'], choix['abstention']['case_id']))

            # Un critère non ordonnable du contrat ne peut pas fonder une priorité
            spec = specification(reference)
            spec['secondary_criteria'] = [
                dict(id='duration', measure='Durée fictive', proof='Sortie conservée', unit='s',
                     favorable='lower', aggregation=None),
                dict(id='comment', measure='Commentaire fictif', proof='Sortie conservée', unit='descriptif',
                     favorable='lower', aggregation=None),
            ]
            candidate = qualification.draft(store, 'fixture', view['revision'], spec)
            qualified = qualification.qualify(store, candidate['contract_sha256'], reviewer=ACTOR, check=check)
            qualification.approve(store, candidate['contract_sha256'], qualified['qualification_id'],
                                  actor=ACTOR, authority=AUTHORITY)
            campaign_id = 'non-orderable'
            store.create_budget(campaign_id, '100', 'TEST')
            m = manifest(candidate, campaign_id)
            snapshot = campaigns.create(store, m)
            authority, evidence = inputs(snapshot, budget=campaign_id)
            authority['reserve_amounts'] = {'x': '7', 'y': '7'}
            campaigns.admit(store, campaign_id, authority, evidence)
            for cell in ('x', 'y'):
                attempt = campaign_id + '-' + cell
                campaigns.reserve(store, campaign_id, cell, attempt)
                execution.execute(self.data, attempt, response)

                def report(ctx, resources):
                    result = findings(ctx, resources)
                    piece = ctx['attempt']['output_piece_id']
                    result['measures'] = [
                        dict(criterion_id='duration', value='1', unit='s',
                             evidence=[dict(piece_id=piece, sha256=sha256(resources[piece]).hexdigest(),
                                            passage=resources[piece].decode())]),
                        dict(criterion_id='comment', value='lisible', unit='descriptif',
                             evidence=[dict(piece_id=piece, sha256=sha256(resources[piece]).hexdigest(),
                                            passage=resources[piece].decode())]),
                    ]
                    return result

                evaluation.evaluate(store, campaign_id, attempt, responsible=RESPONSIBLE,
                                    authority=EVALUATION_AUTHORITY, check=report)
            path = '/preparation/dossiers/fixture/campaigns/' + campaign_id
            self.request(path + '?priority=duration', cookies=cookies)
            self.request(path + '?priority=comment', cookies=cookies, status=400)

    def test_pas_de_conseil_avec_une_reponse_absente(self):
        """Deux réponses satisfaisantes départageables ; la troisième n'a pas de réponse exploitable

        La priorité au coût ne conseille jamais la réponse non conforme la moins chère.
        """
        def comportement(value, model):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['receipt']['result'].update(output=None, incident='PROVIDER_RESPONSE_INCOMPLETE')
                value['cost'].update(status='KNOWN', amount='0.05')
            elif model == 'mistralai/mistral-small-2603':
                value['cost'].update(amount='0.20')
        dossier, html = self.comparer(['Clarté du tableau'],
                                      lambda critere, sortie: 'acceptable' if sortie == self.SORTIE_C else 'excellent',
                                      comportement=comportement,
                                      models=('openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash',
                                              'mistralai/mistral-small-2603'))
        self.assertIn('Satisfait : 2', html)
        self.assertIn('Aucune réponse exploitable pour Modèle B', html)
        self.assertNotIn('Notre conseil', html)
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        priorite, _, _ = self.request(comparison + '?priority=cost')
        self.assertNotIn('Notre conseil', priorite.visible)
        self.assertIn('Toutes les réponses prévues ne sont pas évaluées.', priorite.visible)

    def test_priorite_au_cout_ne_conseille_jamais_une_reponse_non_conforme(self):
        """La réponse non conforme la moins chère reste éliminatoire, même avec la priorité au coût"""
        def ajuste(content, findings):
            if content['output']['content'].strip() == 'Action omise':
                return [dict(finding, status='FAIL', attribution='candidate', finding='Action omise')
                        for finding in findings]
            return findings

        def comportement(value, model):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
            elif model == 'mistralai/mistral-small-2603':
                value['cost'].update(amount='0.20')

        dossier, html = self.comparer(['Clarté du tableau'],
                                      lambda critere, sortie: 'excellent' if sortie == self.SORTIE_C else 'acceptable',
                                      ajuste=ajuste, comportement=comportement,
                                      models=('openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash',
                                              'mistralai/mistral-small-2603'))
        self.assertIn('Ne satisfait pas : 1', html)
        # Par défaut, la meilleure clarté l'emporte malgré son coût
        defaut = Page(html.split('<aside class="economic-choice"')[1].split('</aside>')[0].encode())
        self.assertIn('Modèle C', defaut.visible)
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        _, _, raw = self.request(comparison + '?priority=cost')
        aside = Page(raw.decode().split('<aside class="economic-choice" aria-labelledby="economic-choice-title"')[1]
                     .split('</aside>')[0].encode())
        detail = aside.depliant('Détail du conseil')
        self.assertIn('Modèle A', aside.visible)
        self.assertNotIn('Modèle B', aside.visible + detail)
        self.assertIn('Compromis sur « Clarté du tableau » : la réponse retenue vaut acceptable, '
                      'alors que Modèle C vaut excellent.', detail)

    def test_pas_de_conseil_avec_plusieurs_essais(self):
        """Issue #455 : les conditions du conseil départagé par le coût, avec deux essais par modèle

        Mode d'échec : le conseil est donné alors que chaque modèle compte plusieurs tentatives (RULES.md §8)
        """
        def comportement(value, model):
            if model == 'openai/gpt-5.6-sol':
                value['cost'].update(amount='0.30')
        dossier, html = self.comparer(['Clarté du tableau'], lambda critere, sortie: 'excellent',
                                      comportement=comportement, repetitions=2)
        self.assertEqual(4, html.count('<tr id="attempt-'))
        self.assertNotIn('Notre conseil', html)
        self.assertIn('Un modèle compte plus d’un essai sur un même exemple.', html)
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        priorite, _, _ = self.request(comparison + '?priority=cost')
        self.assertNotIn('Notre conseil', priorite.visible)
        self.assertIn('Un modèle compte plus d’un essai sur un même exemple.', priorite.visible)

    def test_pas_de_conseil_a_egalite_finale(self):
        dossier, html = self.comparer(['Clarté du tableau'], lambda critere, sortie: 'excellent')
        self.assertEqual(2, html.count('<tr id="attempt-'))
        self.assertNotIn('class="out"', html)
        self.assertNotIn('Notre conseil', html)
        self.assertIn('Plusieurs réponses ont la même qualité et le même coût.', html)
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        cout, _, _ = self.request(comparison + '?priority=cost')
        self.assertNotIn('Notre conseil', cout.visible)
        self.assertIn('Plusieurs réponses conformes ont le coût observé le plus bas.', cout.visible)
        self.assertIn('Priorité choisie', cout.visible)
        self.assertIn('coût observé', cout.visible)
        _, _, raw = self.request(comparison, json_response=True)
        criterion = next(column['id'] for column in json.loads(raw)['columns'] if 'criterion_id' in column)
        critere, _, _ = self.request(comparison + '?priority=' + criterion)
        self.assertNotIn('Notre conseil', critere.visible)
        self.assertIn('Plusieurs réponses ont la meilleure valeur sur le critère choisi et le même coût observé.',
                      critere.visible)

    def test_pas_de_conseil_avec_un_cout_inconnu(self):
        def comportement(value, model):
            if model == 'mistralai/mistral-small-2603':
                value['cost'].update(status='UNKNOWN', amount=None)
        dossier, html = self.comparer(['Clarté du tableau'],
                                      lambda critere, sortie: 'acceptable' if sortie == self.SORTIE_C else 'excellent',
                                      comportement=comportement)
        self.assertNotIn('class="out"', html)
        self.assertIn('Comparaison des coûts incomplète', html)
        self.assertNotIn('Notre conseil', html)
        self.assertIn('La qualité ou le coût d’une réponse qui satisfait l’exemple n’est pas connu.', html)
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        _, _, raw = self.request(comparison, json_response=True)
        criterion = next(column['id'] for column in json.loads(raw)['columns'] if 'criterion_id' in column)
        for priorite in ('cost', criterion):
            with self.subTest(priorite=priorite):
                page, _, _ = self.request(comparison + '?priority=' + priorite)
                self.assertNotIn('Notre conseil', page.visible)
                self.assertIn('La qualité ou le coût d’une réponse qui satisfait l’exemple n’est pas connu.',
                              page.visible)

    def test_pas_de_conseil_sans_critere_secondaire_meme_avec_priorite(self):
        """L'absence de critère secondaire interdit le conseil, y compris pour la priorité au coût"""
        dossier, html = self.comparer([], lambda critere, sortie: 'excellent')
        self.assertNotIn('Notre conseil', html)
        self.assertIn('Aucun critère de qualité ne compare les réponses.', html)
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        priorite, _, _ = self.request(comparison + '?priority=cost')
        self.assertNotIn('Notre conseil', priorite.visible)
        self.assertIn('Aucun critère de qualité ne compare les réponses.', priorite.visible)

    def test_pas_de_conseil_quand_les_qualites_se_croisent(self):
        def mesure(critere, sortie):
            return 'excellent' if (critere == 'Clarté du tableau') == (sortie != self.SORTIE_C) else 'acceptable'

        def comportement(value, model):
            # Coûts distincts : seule l'incomparabilité des qualités empêche le conseil
            if model == 'openai/gpt-5.6-sol':
                value['cost'].update(amount='0.30')
        _, html = self.comparer(['Clarté du tableau', 'Concision'], mesure, comportement=comportement)
        self.assertNotIn('class="out"', html)
        self.assertEqual(2, html.count('Excellent'))
        self.assertNotIn('Notre conseil', html)

    def test_pas_de_conseil_avant_un_jugement_complet(self):
        """Deux réponses satisfaisantes départageables, la troisième reste à reprendre faute de preuve"""
        def constat(critere):
            if self.judged[-1]['output']['content'] == self.SORTIE_C:
                return 'INDETERMINE', 'evidence', 'Preuve insuffisante'
            return 'PASS', 'candidate', 'Exigence respectée'

        def comportement(value, model):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
        _, html = self.comparer(['Clarté du tableau'],
                                lambda critere, sortie: 'excellent' if '<script>' in sortie else 'acceptable',
                                constat=constat, comportement=comportement,
                                models=('openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash', 'mistralai/mistral-small-2603'))
        self.assertIn('À reprendre : 1', html)
        self.assertIn('Satisfait : 2', html)
        self.assertNotIn('Notre conseil', html)

    def test_sortie_vide_ou_coupee_expliquee_par_le_recu(self):
        """Issue #442 : une sortie vide ou coupée s'explique par ce que le reçu atteste, rien de plus

        Modes d'échec couverts :
        1. une sortie vide sans motif de fin devient « arrêt pour longueur » ou « raisonnement épuisé »
           parce que la consommation montre des jetons de raisonnement ;
        2. une cause que le reçu n'établit pas est tue au lieu d'être dite inconnue ;
        3. le motif de fin et la consommation transmis ne sont consultables nulle part, ou une valeur
           absente du reçu reçoit une valeur par défaut ;
        4. une reprise perd le lien avec son essai d'origine, ou seule la dernière tentative est détaillée ;
        5. une réponse coupée part au juge ou reçoit un verdict « Ne satisfait pas » ;
        6. consulter les résultats ou le dossier relance un appel candidat
        """
        from base64 import b64encode
        from hashlib import sha256
        judge = self.juge_factice()

        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
                # Sortie vide, aucun motif de fin ; la consommation montre du raisonnement sans le prouver épuisé
                body = storage._strict_json({'choices': [{'message': {'role': 'assistant', 'content': ''}}], 'usage': {
                    'prompt_tokens': 120, 'completion_tokens': limit,
                    'completion_tokens_details': {'reasoning_tokens': limit}}}).encode()
                value['receipt']['observed_configuration']['http'] = dict(
                    status=200, complete=True, credential_redacted=False,
                    body_base64=b64encode(body).decode(), body_sha256=sha256(body).hexdigest())
                value['receipt']['result'].update(output='', incident='PROVIDER_RESPONSE_INCOMPLETE')
            elif model == 'mistralai/mistral-small-2603':
                self.coupee(value, 'Action : relire' + ' | suite' * (limit // 1024), limit)
        dossier, _, envois, _ = self.lancer_avec_reprises(list(self.MAJORANTS), comportement)
        self.assertEqual([('openai/gpt-5.6-sol', 4096), ('deepseek/deepseek-v4.1-flash', 4096),
                          ('mistralai/mistral-small-2603', 4096), ('mistralai/mistral-small-2603', 8192),
                          ('mistralai/mistral-small-2603', 16384)], envois)
        html = self.evaluer(dossier)
        self.assertEqual(1, judge.request.call_count)
        rows = html.split('<tbody role="rowgroup">')[1].split('</tbody>')[0]
        self.assertNotIn('<strong>Modèle B</strong>', rows)
        self.assertNotIn('<strong>Modèle C</strong>', rows)
        ligne_b = next(li for li in html.split('<li>') if li.startswith('Aucune réponse exploitable pour Modèle B'))
        self.assertIn('Aucune réponse exploitable pour Modèle B (cause non établie par le reçu). Ce modèle', ligne_b)
        self.assertNotIn('longueur', ligne_b)
        self.assertNotIn('épuisé', html)
        # Clés dans l'ordre du corps conservé
        self.assertIn('Essai d’origine · motif de fin transmis : inconnu · consommation transmise : completion_tokens 4096, '
                      'completion_tokens_details.reasoning_tokens 4096, prompt_tokens 120', html)
        self.assertIn('Aucune réponse exploitable pour Modèle C (arrêt pour longueur, plafond demandé : '
                      '16384 jetons de sortie ; 2 reprises).', html)
        for numero, (essai, limite) in enumerate((('Essai d’origine', 4096), ('Reprise 1', 8192), ('Reprise 2', 16384))):
            self.assertIn(essai + ' · motif de fin transmis : length · consommation transmise : completion_tokens '
                          + str(limite) + ', prompt_tokens 120', html, numero)
        # Lire les résultats et le dossier n'envoie rien à aucun modèle
        appels = list(self.calls)
        self.request(dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1')
        self.request(dossier)
        self.assertEqual(appels, self.calls)
        self.assertEqual(1, judge.request.call_count)
        artefacts = os.environ.get('BENCHX_E2E_ARTEFACTS')
        if artefacts:
            Path(artefacts).mkdir(parents=True, exist_ok=True)
            Path(artefacts, 'sortie-vide-ou-coupee.json').write_text(json.dumps(
                {'envois': envois, 'jugements': judge.request.call_count, 'appels_apres_consultation': len(self.calls) - len(appels),
                 'lignes': [re.sub(r'<[^>]+>', ' ', li).split('  ')[0].strip() for li in html.split('<li>')
                            if li.startswith(('Aucune réponse exploitable', 'Essai d’origine', 'Reprise '))]},
                ensure_ascii=False, indent=2) + '\n')

    def test_attribution_contradictoire_garde_ses_observations(self):
        """Issue #442 : un reçu d'une autre identité garde son motif et sa consommation, sans cause devinée

        Modes d'échec couverts :
        1. l'identité contredite efface le motif et la consommation que le corps intact transmet ;
        2. une cause est avancée alors que le reçu n'est pas attribuable au modèle demandé ;
        3. la réponse d'une autre identité part au juge ou entre dans le tableau ;
        4. l'arrêt de la campagne empêche d'évaluer les réponses déjà reçues
        """
        from base64 import b64encode
        from hashlib import sha256
        judge = self.juge_factice()

        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
            elif model == 'mistralai/mistral-small-2603':
                # Le fournisseur répond complètement, mais sous une autre identité que celle demandée
                body = storage._strict_json({'model': 'autre/modele', 'choices': [{'finish_reason': 'stop', 'message': {
                    'role': 'assistant', 'content': 'Action : relire'}}],
                    'usage': {'prompt_tokens': 120, 'completion_tokens': 40}}).encode()
                observed = value['receipt']['observed_configuration']
                observed.update(revision='autre/modele', http=dict(
                    status=200, complete=True, credential_redacted=False,
                    body_base64=b64encode(body).decode(), body_sha256=sha256(body).hexdigest()))
                value['receipt']['result'].update(output='Action : relire', incident='MODEL_IDENTITY_MISMATCH')
        dossier, _, envois, _ = self.lancer_avec_reprises(list(self.MAJORANTS), comportement)
        self.assertEqual([('openai/gpt-5.6-sol', 4096), ('deepseek/deepseek-v4.1-flash', 4096),
                          ('mistralai/mistral-small-2603', 4096)], envois)
        html = self.evaluer(dossier)
        self.assertEqual(2, judge.request.call_count)
        rows = html.split('<tbody role="rowgroup">')[1].split('</tbody>')[0]
        self.assertNotIn('<strong>Modèle C</strong>', rows)
        self.assertIn('Aucune réponse exploitable pour Modèle C (cause non établie par le reçu). Ce modèle', html)
        self.assertIn('Essai d’origine · motif de fin transmis : stop · consommation transmise : '
                      'completion_tokens 40, prompt_tokens 120', html)

    def test_reponse_sans_identite_ni_cout_arrete_et_garde_les_preuves(self):
        """Issue #525 : condition établie de N4 (#517), un corps d'erreur 502 reçu sous HTTP 200

        Le reçu n'a ni modèle, ni sortie, ni coût : l'attribution échoue et le coût reste inconnu sous la
        politique `require_observed` du parcours public. La garde arrête la comparaison.
        Modes d'échec couverts :
        1. sortie, reçu, réserve ou identités perdus après l'arrêt ;
        2. un essai jamais envoyé présenté comme en attente ou comme une dépense à venir ;
        3. l'arrêt dit « problème technique » sans nommer la preuve qui manque ;
        4. le coût inconnu compté zéro, le sous-total connu présenté comme complet, ou un conseil donné ;
        5. une consultation ou un redémarrage de l'exécuteur émet un essai de plus
        """
        from benchmark import runtime
        from benchmark_web.fragments import montant_lisible
        judge = self.juge_factice()

        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                # Comme Grok 4.7 dans N4 : corps {"error": {"code": 502}} sous HTTP 200, sans modèle
                self.incident_fournisseur(value, 502)
                value['receipt']['observed_configuration']['http']['status'] = 200
                value['receipt']['result']['incident'] = 'MODEL_IDENTITY_MISMATCH'
        dossier, _, envois, _ = self.lancer_avec_reprises(list(self.MAJORANTS), comportement)
        self.assertEqual([('openai/gpt-5.6-sol', 4096), ('deepseek/deepseek-v4.1-flash', 4096)], envois)
        campaign_id = dossier.rsplit('/', 1)[1] + '-c1'
        suivi = dossier + '/campaigns/' + campaign_id + '/conditions'

        def persisted():
            with closing(storage.Store(self.data)) as store:
                snapshot = campaigns.inspect(store, campaign_id)
                connection = store._connection
                return (snapshot['stop_reason'], snapshot['manifest_sha256'],
                        [(a['operation_id'], a['state'], storage._strict_json(a['operation']['receipt']),
                          storage._strict_json(a['operation']['observed_cost']), a['operation']['reserved_amount'])
                         for a in snapshot['attempts']],
                        connection.execute('SELECT operation_id, output_piece_id FROM s4_results ORDER BY rowid').fetchall(),
                        connection.execute('SELECT count(*) FROM s4_emissions').fetchone()[0],
                        snapshot['admissions'][0]['authority']['reserve_amounts'])
        effets = persisted()
        arret, _, tentatives, resultats, emissions, reserves = effets
        self.assertEqual('ACQUISITION_EVIDENCE_INCOMPLETE', arret)
        self.assertEqual(['RECEIVED', 'RECEIVED', 'INTENT_RECORDED'], [t[1] for t in tentatives])
        self.assertEqual(2, emissions)
        # Sortie de Modèle A conservée ; le reçu de Modèle B n'a aucune sortie
        self.assertEqual([tentatives[0][0], tentatives[1][0]], [r[0] for r in resultats])
        self.assertIsNotNone(resultats[0][1])
        self.assertIsNone(resultats[1][1])
        recu, cout = json.loads(tentatives[1][2]), json.loads(tentatives[1][3])
        self.assertEqual(('MODEL_IDENTITY_MISMATCH', None, 200), (recu['result']['incident'], recu['result']['output'],
                                                                  recu['observed_configuration']['http']['status']))
        # Coût inconnu, jamais zéro ; chaque réserve reste celle de l'admission
        self.assertEqual(('UNKNOWN', None), (cout['status'], cout['amount']))
        self.assertEqual(sorted(reserves.values()), sorted(t[4] for t in tentatives))
        # Consultations, puis redémarrage de l'exécuteur : aucun essai de plus, preuves inchangées
        for _ in range(2):
            page, _, _ = self.request(suivi)
            self.assertIn('Une réponse reçue n’indique ni le modèle qui a répondu ni son coût. La comparaison '
                          's’est arrêtée', page.visible)
            self.assertNotIn('problème technique', page.visible)
            bloc = page.visible.split('Modèle par modèle')[1].split('Réponses reçues :')[0]
            self.assertEqual(['réponse reçue', 'pas de réponse exploitable',
                              'non appelé : la comparaison s’est arrêtée avant cet essai'],
                             [ligne.rsplit(' : ', 1)[-1].strip() if 'non appelé' not in ligne
                              else ligne[ligne.index('non appelé'):].strip() for ligne in bloc.split('Modèle ')[1:]])
            self.assertIn('Réponses reçues : 1 sur 3.', page.visible)
        with closing(storage.Store(self.data)) as store:
            runtime.stop(self.data, store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        self.clock += timedelta(hours=1)
        self.assertEqual([], self.relancer_par_l_executeur())
        self.assertEqual(effets, persisted())
        _, _, raw = self.request(dossier + '/campaigns/' + campaign_id)
        texte, phases = self.depenses(raw.decode())
        _, connu = self.operations_du_dossier()
        self.assertIn('Coût complet inconnu · sous-total connu : ' + montant_lisible(str(connu)) + ' USD', texte)
        self.assertIn('le sous-total ne donne que la somme des dépenses connues', texte)
        lignes = phases['Autres envois aux modèles']
        self.assertTrue(lignes[1].startswith('Modèle B : dépense inconnue'), lignes[1])
        self.assertIn('montant réservé : ' + montant_lisible(reserves['cell-2']) + ' USD', lignes[1])
        self.assertTrue(lignes[2].startswith('Modèle C : aucune dépense'), lignes[2])
        self.assertIn('non envoyé : la comparaison s’est arrêtée avant cet essai', lignes[2])
        self.assertNotIn('dépense à venir', texte)
        # La réponse exploitable de Modèle A est seule évaluée ; la couverture prévue reste distincte
        html = self.evaluer(dossier)
        self.assertEqual(1, judge.request.call_count)
        self.assertIn('Aucune réponse exploitable pour Modèle B (cause non établie par le reçu)', html)
        self.assertIn('Modèle C n’a pas été appelé : la comparaison s’est arrêtée avant cet essai.', html)
        self.assertIn('essais lancés : 2 sur 3', html)
        self.assertIn('Comparaison des coûts incomplète', html)
        self.assertNotIn('Notre conseil', html)
        self.assertEqual(2, len(envois))
        self.assertEqual(effets, persisted())

    def test_cout_inconnu_seul_arrete_et_le_dit(self):
        """Issue #525 : un coût inconnu sans incident arrête la comparaison sous `require_observed`

        Mode d'échec : le suivi dit seulement que la comparaison s'est arrêtée, sans la preuve qui manque
        """
        def comportement(value, model, limit, suivi):
            if model == 'openai/gpt-5.6-sol':
                value['cost'].update(status='UNKNOWN', amount=None)
        dossier, _, envois, _ = self.lancer_avec_reprises(list(self.MAJORANTS), comportement)
        self.assertEqual([('openai/gpt-5.6-sol', 4096)], envois)
        page, _, _ = self.request(dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions')
        self.assertIn('Le coût d’une réponse reçue n’est pas connu. La comparaison s’est arrêtée', page.visible)

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
        # Issue #458 : la raison reste vraie quand la reprise n'a pas eu lieu
        self.assertIn('Une reprise a été demandée pour un modèle.', html)
        # Issue #441 : la reprise refusée ne partira jamais, elle ne laisse aucune dépense en suspens
        texte, phases = self.depenses(html)
        self.assertIn('Coût complet connu', texte)
        self.assertEqual(1, sum('Modèle B : aucune dépense' in ligne and 'reprise arrêtée avant envoi' in ligne
                                for ligne in phases['Autres envois aux modèles']))

    def incident_fournisseur(self, value, status, retry_after=None):
        """Reçu Pi d'un incident fournisseur : corps complet, sans modèle ni sortie, coût inconnu"""
        from base64 import b64encode
        from hashlib import sha256
        from benchmark.transports.openrouter import incident_for
        body = storage._strict_json({'error': {'code': status, 'message': 'Incident simulé'}}).encode()
        observed = value['receipt']['observed_configuration']
        observed['revision'] = None
        observed['sources'] = {key: text for key, text in observed['sources'].items() if key != 'revision'}
        observed['http'] = dict(status=status, complete=True, credential_redacted=False,
                                received_at=self.clock.isoformat(),
                                response_headers={'Retry-After': retry_after} if retry_after else {},
                                body_base64=b64encode(body).decode(), body_sha256=sha256(body).hexdigest())
        value['receipt']['result'].update(output=None, incident=incident_for(status))
        value['cost'].update(status='UNKNOWN', amount=None)

    def relancer(self):
        """Minuteur de l'exécuteur : chaque relance due est réservée puis émise ; renvoie les délais restants"""
        with closing(storage.Store(self.data)) as store:
            dues = execution.due_retries(store)
            suivantes = [execution.retry(store, operation_id) for operation_id, *_ in dues]
        for suivante in suivantes:
            if suivante:
                execution.execute(self.data, suivante, transport_factory=self.fabrique,
                                  access_secret=SECRET, access_transport=self.access)
        return [delay for *_, delay in dues]

    def test_incident_fournisseur_candidat_relance_d_office(self):
        """Issue #533 : un 429 ou un 5xx reçu par un candidat est relancé d'office, sans arrêter la comparaison

        Modes d'échec couverts :
        1. l'incident ferme l'admission et le modèle suivant n'est jamais interrogé ;
        2. la relance part avant son échéance, ou ignore un `Retry-After` plus long que son délai ;
        3. un redémarrage perd la relance due, ou la duplique ;
        4. l'évaluation part pendant qu'une relance est programmée, ou juge une tentative en incident ;
        5. le suivi tait la relance programmée, sa cause ou son délai ;
        6. la relance, même demande, retire « Notre conseil » comme une reprise pour longueur (décision d'Ayo) ;
        7. un redémarrage entre réservation et envoi perd la relance réservée, ou l'émet deux fois (#535) ;
        8. après un redémarrage, la relance réussie n'entraîne pas l'évaluation automatique, ou rouvre la comparaison (#535)
        """
        from benchmark import runtime
        # Critère de qualité mesuré et coûts connus : sans relance comptée comme reprise, un conseil est donné
        self.criteria = {'eliminatory': [], 'obligations': ['Toutes les actions présentes'], 'quality': [
            {'label': 'Clarté du tableau', 'scale': ['excellent', 'acceptable', 'faible'], 'favorable': 'excellent'}]}
        judge = self.juge_factice(lambda critere: ('PASS', 'candidate', 'Exigence respectée'), mesure='excellent')
        script, pendant = [429, 503, 503], []

        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
            elif script:
                status = script.pop(0)
                self.incident_fournisseur(value, status, retry_after='600' if status == 429 else None)
            else:
                # Pendant l'appel de la dernière relance, le suivi dit sa vraie cause
                pendant.append(self.request(suivi)[0].visible)
        dossier, _, envois, cles = self.lancer_avec_reprises(
            ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], comportement)
        A, B = ('openai/gpt-5.6-sol', 4096), ('deepseek/deepseek-v4.1-flash', 4096)
        self.assertEqual([A, B], envois)
        suivi = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions'
        # Chaque page lue avance l'horloge d'une minute : le Retry-After de 600 s l'emporte sur les 30 s
        page, _, _ = self.request(suivi)
        self.assertIn('OpenRouter limite temporairement les demandes. Nouvelle tentative automatique dans 9 min. '
                      'Les autres réponses sont conservées.', page.visible)
        self.assertNotIn('La comparaison s’est arrêtée', page.visible)
        self.assertNotIn('Évaluer les réponses reçues', page.visible)
        page, _, _ = self.request(dossier)
        self.assertNotIn('Lancer l’évaluation', page.visible)
        # Les résultats ne déclarent pas sans réponse un modèle dont la relance est programmée
        _, _, raw = self.request(suivi.removesuffix('/conditions'))
        self.assertIn('Relance automatique programmée après un incident fournisseur.', raw.decode())
        self.assertNotIn('Aucune réponse exploitable', raw.decode())
        # Avant l'échéance, rien ne part
        self.assertEqual([420], self.relancer())
        self.assertEqual([A, B], envois)
        self.clock += timedelta(seconds=420)
        self.relancer()
        self.assertEqual([A, B, A], envois)
        page, _, _ = self.request(suivi)
        self.assertIn('Incident chez OpenRouter ou chez le fournisseur du modèle. Nouvelle tentative automatique dans 1 min',
                      page.visible)
        # Redémarrage de l'exécuteur : la relance due part une seule fois
        with closing(storage.Store(self.data)) as store:
            runtime.stop(self.data, store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        self.clock += timedelta(seconds=60)
        self.relancer()
        # Aucun doublon : seule la relance suivante est programmée, à 10 min
        self.assertEqual([600], self.relancer())
        self.assertEqual([A, B, A, A], envois)
        # Issue #535 : redémarrage après la réservation de la relance suivante, avant son envoi
        self.clock += timedelta(seconds=600)
        with closing(storage.Store(self.data)) as store:
            (source, *_), = execution.due_retries(store)
            reservee = execution.retry(store, source)
            self.assertIsNotNone(reservee)
            runtime.stop(self.data, store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        self.assertEqual([A, B, A, A], envois)
        # Profil du juge changé au redémarrage : l'évaluation reste sous celui autorisé au lancement (revue PR #536)
        from benchmark import automatic_judgment as auto
        from benchmark.transports import openrouter
        from tests.test_openrouter_preparation import estimate_for
        autorise = self.assistants['judgment_transport']._profile
        nouveau = dict(autorise, system=autorise['system'] + ' Consigne d’un redémarrage ultérieur.')
        self.assistants['judgment_transport'] = openrouter.OpenRouterJudgment(None, nouveau, retained=[autorise])
        self.assistants['judgment_transport']._quote = openrouter.configuration(estimate_for(nouveau), nouveau)
        # Le minuteur de l'exécuteur émet la relance réservée une seule fois, puis lance l'évaluation sans action
        self.assertEqual([0], self.relancer_par_l_executeur())
        self.assertEqual([], self.relancer_par_l_executeur())
        self.assertEqual([A, B, A, A, A], envois)
        self.assertEqual([KEY] * len(envois), cles)
        self.assertEqual(1, len(pendant))
        self.assertIn('Un modèle est interrogé de nouveau après un incident fournisseur.', pendant[0])
        self.assertNotIn('longueur', pendant[0])
        # Seule la dernière tentative de Modèle A est évaluée ; la comparaison arrêtée par le redémarrage le reste
        self.assertEqual(2, judge.request.call_count)
        with closing(storage.Store(self.data)) as store:
            snapshot = campaigns.inspect(store, self.start['judgment_campaign'])
            self.assertEqual('PROCESS_STARTED_ADMISSION_BLOCKED', snapshot['stop_reason'])
            jugements = [op['requested_configuration'] for op in store.inspect_operations()
                         if op['engine_version'] == auto.FORMAT]
        self.assertEqual([snapshot['admissions'][-1]['authority']['automatic_judgment']['configuration']] * 2, jugements)
        _, _, raw = self.request(suivi.removesuffix('/conditions'))
        html = raw.decode()
        self.assertEqual(2, html.split('<tbody role="rowgroup">')[1].count('<tr id="attempt-'))
        self.assertNotIn('Aucune réponse exploitable', html)
        self.assertIn('Réponse obtenue à la tentative 4, après un incident fournisseur', html)
        self.assertIn('Notre conseil', html)
        self.assertNotIn('Une reprise a été demandée', html)
        # L'exception #533 vaut aussi pour une priorité explicite : la relance renvoie la même demande
        priorite, _, _ = self.request(suivi.removesuffix('/conditions') + '?priority=cost')
        self.assertIn('Notre conseil', priorite.visible)
        self.assertIn('Priorité choisie : coût observé.', priorite.visible)
        self.assertNotIn('Reprise après arrêt pour longueur', html)
        texte, phases = self.depenses(html)
        # Essai d'origine et deux premières relances, remplacés : seules les relances portent ce libellé
        self.assertEqual(3, len(phases['Autres envois aux modèles']))
        self.assertEqual(2, sum('relance après incident fournisseur' in ligne for ligne in phases['Autres envois aux modèles']))
        artefacts = os.environ.get('BENCHX_E2E_ARTEFACTS')
        if artefacts:
            Path(artefacts).mkdir(parents=True, exist_ok=True)
            Path(artefacts, 'relance-incident-candidat.json').write_text(json.dumps(
                {'envois': envois, 'jugements': judge.request.call_count,
                 'lignes_du_tableau': html.split('<tbody role="rowgroup">')[1].count('<tr id="attempt-'),
                 'autres_envois': [re.sub(r'<[^>]+>', ' ', ligne).split('  ')[0].strip()
                                   for ligne in phases['Autres envois aux modèles']]},
                ensure_ascii=False, indent=2) + '\n')

    def relancer_par_l_executeur(self):
        """Minuteurs de l'exécuteur à l'échéance : relance, minuteur suivant, puis évaluation automatique"""
        timers = {}
        retry = ((self.fabrique, self.assistants['judgment_transport']), SECRET, self.access, 'a' * 40, timers)
        with closing(storage.Store(self.data)) as store:
            dues = execution.due_retries(store)
        # Minuteurs simulés, comme dans les tests de l'évaluation : seule l'échéance relue ici fait partir une relance
        with patch.object(service.threading, 'Timer'):
            for operation_id, dossier, session, _ in dues:
                service._retry('candidate', self.data, dossier, session, operation_id, retry)
        # Issue #539 : aucun minuteur réel ne part de lui-même pendant le test, dans un ordre variable ;
        # ce contrôle échoue si les minuteurs ne sont plus simulés ci-dessus
        self.assertEqual([], [t for t in timers.values() if isinstance(t, threading.Thread) and t.ident is not None])
        # Les minuteurs suivants sont relus ici à leur échéance, jamais laissés courir après le test
        for timer in timers.values():
            timer.cancel()
        return [delay for *_, delay in dues]

    def test_incident_fournisseur_candidat_borne_a_cinq_tentatives(self):
        """Issue #533 : au-delà de cinq tentatives, la cellule s'arrête avec sa cause nommée

        Modes d'échec couverts :
        1. la relance continue au-delà de cinq tentatives ;
        2. la cellule épuisée n'a pas de cause nommée, ou retient l'évaluation des autres modèles ;
        3. l'évaluation automatique lancée avec la comparaison l'arrête pendant qu'une relance attend ;
        4. après la dernière relance, l'évaluation automatique ne part pas
        """
        judge = self.juge_factice()

        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
            else:
                self.incident_fournisseur(value, 429)
        dossier, _, envois, _ = self.lancer_avec_reprises(
            ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], comportement)
        # Suite du fil de lancement : l'évaluation automatique attend la relance, sans arrêter la comparaison
        service._judge_comparison(self.data, self.start, self.bound[2], None)
        self.assertEqual(0, judge.request.call_count)
        for delay in (30, 120, 600, 1800):
            self.clock += timedelta(seconds=delay)
            self.relancer_par_l_executeur()
        self.clock += timedelta(hours=1)
        self.assertEqual([], self.relancer_par_l_executeur())
        self.assertEqual(5, sum(model == 'openai/gpt-5.6-sol' for model, _ in envois))
        self.assertEqual(6, len(envois))
        # La dernière relance a lancé l'évaluation de la seule réponse exploitable
        self.assertEqual(1, judge.request.call_count)
        comparison = dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1'
        page, _, _ = self.request(comparison + '/conditions')
        self.assertNotIn('Nouvelle tentative automatique', page.visible)
        self.assertIn('Évaluation terminée.', page.visible)
        # Issue #535 : le suivi nomme la cause et le nombre de tentatives, comme la page de résultats
        self.assertIn('1 modèle sur 2 n’a pas donné de réponse exploitable (OpenRouter limite temporairement les '
                      'demandes ; 5 tentatives) : il n’est pas évalué.', page.visible)
        # Issue #537 : le bloc « Modèle par modèle » ne dit pas « réponse reçue » pour le modèle épuisé
        bloc = page.visible.split('Modèle par modèle')[1].split('Réponses reçues :')[0]
        ligne_a, ligne_b = bloc.split('Modèle B')
        self.assertTrue(ligne_a.strip().endswith(': pas de réponse exploitable'), ligne_a)
        self.assertTrue(ligne_b.strip().endswith(': réponse reçue'), ligne_b)
        results, _, raw = self.request(comparison)
        self.examine(results, comparison, 'résultats après relances épuisées', None)
        html = raw.decode()
        self.assertIn('Aucune réponse exploitable pour Modèle A (OpenRouter limite temporairement les demandes ; '
                      '5 tentatives).', html)

    def test_relance_ambigue_au_redemarrage_jamais_rejouee(self):
        """Issue #535 : une relance partie quand l'exécuteur redémarre reste ambiguë et n'est jamais rejouée"""
        from benchmark import runtime

        class Plantage(BaseException):
            """Fin brutale du processus : aucun gestionnaire d'erreur ne s'exécute"""
        script = [429]

        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
            elif script:
                self.incident_fournisseur(value, script.pop(0))
            else:
                raise Plantage
        dossier, _, envois, _ = self.lancer_avec_reprises(
            ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], comportement)
        self.clock += timedelta(seconds=30)
        with self.assertRaises(Plantage):
            self.relancer_par_l_executeur()
        # Le démarrage suivant rend ambiguë la relance partie, sous le seul arrêt du redémarrage
        with closing(storage.Store(self.data)) as store:
            runtime.stop(self.data, store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
            self.assertEqual(['AMBIGUOUS'], [op['state'] for op in store.inspect_operations()
                                             if op['operation_id'].startswith('recovery-')])
        self.assertEqual(3, len(envois))
        self.clock += timedelta(hours=1)
        self.assertEqual([], self.relancer_par_l_executeur())
        self.assertEqual(3, len(envois))
        # Issue #537 : la relance close sans réponse confirmée n'est ni « réponse reçue » ni comptée comme reçue
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(1, len(store.close_expired_ambiguous(datetime.now(timezone.utc) + timedelta(minutes=16))))
        page, _, _ = self.request(dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions')
        bloc = page.visible.split('Modèle par modèle')[1].split('Réponses reçues :')[0]
        ligne_a, ligne_b = bloc.split('Modèle B')
        self.assertTrue(ligne_a.strip().endswith(': réponse non confirmée, appel clos'), ligne_a)
        self.assertTrue(ligne_b.strip().endswith(': réponse reçue'), ligne_b)
        self.assertIn('Réponses reçues : 1 sur 2.', page.visible)

    def test_autre_appel_ambigu_au_redemarrage_la_relance_n_attend_pas(self):
        """Issue #537 : un autre appel resté ambigu au redémarrage interdit la relance, qui n'est pas attendue sans fin

        Modes d'échec couverts :
        1. le suivi annonce « Nouvelle tentative automatique dès que possible » sans fin ;
        2. la relance part, ou l'appel ambigu est rejoué ;
        3. le suivi tait pourquoi la relance n'a pas lieu ;
        4. l'évaluation de la réponse reçue ne peut jamais partir
        """
        from benchmark import runtime

        class Plantage(BaseException):
            """Fin brutale du processus pendant l'appel de Modèle C"""
        envois, suivis = [], []

        def comportement(value, model, limit, suivi):
            envois.append(model)
            suivis.append(suivi)
            if model == 'openai/gpt-5.6-sol':
                self.incident_fournisseur(value, 429)
            elif model == 'mistralai/mistral-small-2603':
                raise Plantage
            else:
                value['cost'].update(status='KNOWN', amount='0.05')
        with self.assertRaises(Plantage):
            self.lancer_avec_reprises(
                ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash', 'mistralai/mistral-small-2603'], comportement)

        def effets():
            """Issue #524 : campagnes et filiations (manifestes), admissions, intentions et émissions persistées"""
            with closing(storage.Store(self.data)) as store:
                tables = {'s4_campaigns': 'campaign_id, manifest_sha256', 's4_admissions': 'admission_id, campaign_id',
                          's4_attempts': 'operation_id, campaign_id, cell_id', 's4_emissions': '*'}
                return {table: store._connection.execute(f'SELECT {columns} FROM {table} ORDER BY rowid').fetchall()
                        for table, columns in tables.items()}, sorted(
                    op['state'] for op in store.inspect_operations() if op['phase'] == 'acquisition')
        # Issue #524 : l'appel de Modèle C est peut-être parti ; consulter puis redémarrer ne rejoue rien
        avant, etats = effets()
        self.assertEqual(3, len(avant['s4_emissions']))
        self.assertEqual(['EMISSION_POSSIBLE', 'RECEIVED', 'RECEIVED'], etats)
        self.request(suivis[0])
        self.assertEqual((avant, etats), effets())
        with closing(storage.Store(self.data)) as store:
            runtime.stop(self.data, store, 'PROCESS_STARTED_ADMISSION_BLOCKED', after_process_exit=True)
        self.assertEqual((avant, ['AMBIGUOUS', 'RECEIVED', 'RECEIVED']), effets())
        self.clock += timedelta(seconds=30)
        self.assertEqual([], self.relancer_par_l_executeur())
        page, _, _ = self.request(suivis[0])
        self.assertEqual(avant, effets()[0])
        self.assertNotIn('Nouvelle tentative automatique', page.visible)
        # L'appel ambigu est clos sans rejeu ; la réponse reçue de Modèle B peut être évaluée
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(1, len(store.close_expired_ambiguous(datetime.now(timezone.utc) + timedelta(minutes=16))))
        self.clock += timedelta(hours=1)
        self.assertEqual([], self.relancer_par_l_executeur())
        self.assertEqual(3, len(envois))
        page, _, _ = self.request(suivis[0])
        self.assertNotIn('Nouvelle tentative automatique', page.visible)
        self.assertIn('relance impossible : un autre appel n’a pas de réponse confirmée', page.visible)
        # L'appel clos sans réponse confirmée n'est ni « réponse reçue » ni compté parmi les réponses reçues
        bloc = page.visible.split('Modèle par modèle')[1].split('Réponses reçues :')[0]
        self.assertTrue(bloc.split('Modèle C')[1].strip().endswith(': réponse non confirmée, appel clos'), bloc)
        # Issue #541 : Modèle A, sans réponse exploitable, n'est pas compté non plus
        self.assertTrue(bloc.split('Modèle A')[1].split('Modèle B')[0].strip().endswith(': pas de réponse exploitable'), bloc)
        self.assertIn('Réponses reçues : 1 sur 3.', page.visible)
        page, _, _ = self.request(suivis[0].split('/campaigns/')[0])
        self.assertIn('Lancer l’évaluation', page.visible)
        self.assertEqual(avant, effets()[0])

    def test_tous_les_modeles_epuises_le_suivi_nomme_la_cause(self):
        """Issue #535 : sans aucune réponse exploitable après cinq tentatives, le suivi dit la cause et leur nombre"""
        def comportement(value, model, limit, suivi):
            self.incident_fournisseur(value, 503)
        dossier, _, envois, _ = self.lancer_avec_reprises(
            ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], comportement)
        # Minuteur de l'exécuteur : la dernière relance passe par l'évaluation automatique, sans rien à évaluer
        for delay in (30, 120, 600, 1800):
            self.clock += timedelta(seconds=delay)
            self.relancer_par_l_executeur()
        self.assertEqual(10, len(envois))
        # Revue PR #538 : aucune évaluation tentée, donc aucun arrêt d'évaluation qui masquerait la cause
        with closing(storage.Store(self.data)) as store:
            self.assertIsNone(campaigns.inspect(store, self.start['judgment_campaign'])['stop_reason'])
        page, _, _ = self.request(dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions')
        self.assertIn('Aucune réponse exploitable à évaluer (Incident chez OpenRouter ou chez le fournisseur du '
                      'modèle ; 5 tentatives).', page.visible)

    def test_relance_due_pendant_qu_un_autre_modele_repond(self):
        """Issue #533 : une relance due pendant l'appel d'un autre modèle attend sa fin, puis part et fait évaluer

        Modes d'échec couverts :
        1. la relance part pendant un autre appel du même budget, ou évalue avant sa réponse ;
        2. l'évaluation lancée avec la comparaison l'arrête parce qu'une relance attend ;
        3. la relance différée n'est jamais reprogrammée, ou ne lance pas l'évaluation
        """
        judge = self.juge_factice()
        script, pendant = [429], []

        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
                # Modèle B répond lentement : la relance de Modèle A arrive à échéance pendant son appel
                self.clock += timedelta(seconds=30)
                pendant.append(self.relancer_par_l_executeur())
            elif script:
                self.incident_fournisseur(value, script.pop(0))
        dossier, _, envois, _ = self.lancer_avec_reprises(
            ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], comportement)
        self.assertEqual([[0]], pendant)
        self.assertEqual(['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], [model for model, _ in envois])
        # Suite du fil de lancement : rien n'est évalué, la comparaison reste admise
        service._judge_comparison(self.data, self.start, self.bound[2], None)
        self.assertEqual(0, judge.request.call_count)
        self.relancer_par_l_executeur()
        self.assertEqual(['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash', 'openai/gpt-5.6-sol'],
                         [model for model, _ in envois])
        self.assertEqual(2, judge.request.call_count)
        with closing(storage.Store(self.data)) as store:
            self.assertIsNone(campaigns.inspect(store, self.start['judgment_campaign'])['stop_reason'])

    def test_limite_de_debit_sans_preautorisation_garde_l_arret(self):
        """Issue #533 : une comparaison lancée sans relance préautorisée garde l'arrêt nommé de #525"""
        def sans_relance(store, panel):
            grant = original(store, panel)
            grant.pop('incident_retries')
            return grant
        original = campaigns._requester_recovery

        def comportement(value, model, limit, suivi):
            self.incident_fournisseur(value, 429)
        with patch.object(campaigns, '_requester_recovery', sans_relance):
            dossier, _, envois, _ = self.lancer_avec_reprises(
                ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], comportement)
        self.assertEqual([('openai/gpt-5.6-sol', 4096)], envois)
        self.clock += timedelta(hours=1)
        self.assertEqual([], self.relancer())
        page, _, _ = self.request(dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions')
        self.assertIn('OpenRouter limite temporairement les demandes. La comparaison s’est arrêtée', page.visible)

    def test_plafond_de_cinq_tentatives_compte_les_reprises_pour_longueur(self):
        """Issue #533 : cinq tentatives au plus par cellule, reprises pour longueur comprises (revue de la PR #534)"""
        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
            elif limit < 16384:
                self.coupee(value, 'Action : relire' + ' | suite' * (limit // 1024), limit)
            else:
                self.incident_fournisseur(value, 429)
        dossier, _, envois, _ = self.lancer_avec_reprises(
            ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], comportement)
        for delay in (30, 120, 600, 1800):
            self.clock += timedelta(seconds=delay)
            self.relancer()
        self.assertEqual([4096, 8192, 16384, 16384, 16384],
                         [limit for model, limit in envois if model == 'openai/gpt-5.6-sol'])

    def test_limite_de_debit_relancee_sans_capacites_figees(self):
        """Issue #533 : un modèle absent du relevé conservé est relancé ; seule la reprise pour longueur exige ses capacités"""
        from benchmark.acquisition import recovery
        script = [429]

        def comportement(value, model, limit, suivi):
            if model == 'deepseek/deepseek-v4.1-flash':
                value['cost'].update(status='KNOWN', amount='0.05')
            elif script:
                self.incident_fournisseur(value, script.pop(0))
        # Aucune capacité figée : comme pour un modèle ajouté par vérification, hors du relevé partagé
        with patch.object(recovery, '_capability', side_effect=ValueError('absent du relevé')):
            dossier, _, envois, _ = self.lancer_avec_reprises(
                ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash'], comportement)
        self.clock += timedelta(seconds=30)
        self.relancer()
        self.assertEqual(['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash', 'openai/gpt-5.6-sol'],
                         [model for model, _ in envois])

    def test_credit_epuise_d_un_candidat_arrete_la_comparaison(self):
        """Issue #533 : un incident non relançable garde l'arrêt et son message nommé (#532)

        Modes d'échec couverts :
        1. un crédit épuisé est relancé, ou laisse interroger les modèles suivants ;
        2. la limite de débit reçue avant lui masque le crédit épuisé, seule cause sur laquelle agir ;
        3. la relance programmée de la limite de débit part malgré l'arrêt de la comparaison
        """
        def comportement(value, model, limit, suivi):
            self.incident_fournisseur(value, 429 if model == 'openai/gpt-5.6-sol' else 402)
        # La revérification de la clé interroge OpenRouter : hors périmètre de ce parcours sans réseau
        with patch.object(provider_access, 'recheck'):
            dossier, _, envois, _ = self.lancer_avec_reprises(
                ['openai/gpt-5.6-sol', 'deepseek/deepseek-v4.1-flash', 'mistralai/mistral-small-2603'], comportement)
        self.assertEqual([('openai/gpt-5.6-sol', 4096), ('deepseek/deepseek-v4.1-flash', 4096)], envois)
        self.clock += timedelta(hours=1)
        self.assertEqual([], self.relancer())
        self.assertEqual(2, len(envois))
        page, _, _ = self.request(dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1/conditions')
        self.assertIn('Crédit OpenRouter épuisé pour votre clé : rechargez-le ou relevez son plafond. '
                      'La comparaison s’est arrêtée', page.visible)

    def test_cout_complet_inconnu_tant_qu_un_envoi_attend(self):
        """Issue #441 : un envoi réservé mais pas encore parti peut encore coûter

        Mode d'échec : la page affichée entre le lancement et l'exécution annonce un coût complet
        connu alors que les réponses des modèles restent à payer
        """
        with patch.object(execution, 'execute_launch'):
            dossier, _, envois, _ = self.lancer_avec_reprises(['openai/gpt-5.6-sol', 'mistralai/mistral-small-2603'], None)
        self.assertEqual([], envois)
        _, _, raw = self.request(dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1')
        texte, phases = self.depenses(raw.decode())
        self.assertIn('Coût complet inconnu · sous-total connu', texte)
        self.assertIn('dépense à venir', phases['Autres envois aux modèles'][0])
        self.assertIn('pas encore envoyé', phases['Autres envois aux modèles'][0])
        self.assertIn('montant réservé', phases['Autres envois aux modèles'][0])

    def test_cout_complet_inconnu_avant_l_evaluation(self):
        """Issue #441 : l'évaluation des réponses reçues reste à payer

        Mode d'échec : toutes les réponses sont arrivées avec un coût connu, l'évaluation n'est pas encore
        réservée, et le récapitulatif annonce un coût complet connu
        """
        dossier, _, envois, _ = self.lancer_avec_reprises(
            ['openai/gpt-5.6-sol', 'mistralai/mistral-small-2603'], lambda *arguments: None)
        self.assertEqual(2, len(envois))
        _, _, raw = self.request(dossier + '/campaigns/' + dossier.rsplit('/', 1)[1] + '-c1')
        texte, phases = self.depenses(raw.decode())
        self.assertNotIn('Évaluation des réponses', phases)
        self.assertIn('Coût complet inconnu · sous-total connu', texte)
        self.assertIn('L’évaluation de 2 réponses est à venir : son coût n’est pas encore connu.', texte)

    def sonde(self, dossier, slug, action):
        """Vérification réelle d'un modèle hors catalogue, réponse simulée au coût connu de 0.0004 USD"""
        from copy import deepcopy
        from benchmark import model_probes
        summary, endpoints = model(slug, 'outside', ['low', 'high'])
        summary['canonical_slug'] = slug + '-20260917'
        endpoints['endpoints'][0].update(pricing=summary['pricing'], context_length=64000, max_completion_tokens=8192,
                                         supported_parameters=['max_tokens', 'reasoning'])
        documents = {'/api/v1/model/' + slug: summary, '/api/v1/models/' + slug + '/endpoints': endpoints}

        def post(key, wire, **kwargs):
            body = {'id': 'gen-fixture', 'model': summary['canonical_slug'], 'usage': {'cost': 0.0004},
                    'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'OK'}}]}
            return 200, {}, json.dumps(body).encode(), True, '2026-09-17T20:00:00Z', 0
        with closing(storage.Store(self.data)) as store:
            session = store._connection.execute('SELECT session_id FROM s2_dossiers').fetchone()[0]
            operation_id, key = model_probes.submit(store, session, dossier.rsplit('/', 1)[1],
                {'slug': slug, 'action_id': action}, lambda path: {'data': deepcopy(documents[path])}, SECRET, self.access)
        model_probes.execute(self.data, operation_id, key, post)
        return operation_id

    def test_cout_complet_compte_la_verification_des_modeles_retenus(self):
        """Issue #441 : une vérification de modèle compte si la comparaison s'en sert

        Modes d'échec : la vérification d'un modèle retenu, faite avant une correction de l'exemple,
        disparaît du coût complet ; celle d'un modèle vérifié puis écarté y est comptée
        """
        retenu, ecarte = 'outside/new-generalist', 'outside/other-generalist'
        sondes = {}

        def qualifier():
            dossier, page = self.exemple()
            sondes[retenu] = self.sonde(dossier, retenu, 'retenu')
            self.preparation_stage = 'correction'
            self.submit(page, '/messages', {'kind': 'correct', 'message': 'Présenter un tableau avec le responsable de chaque action'})
            prep.execute(self.data, self.starts.get_nowait(), self.bound[0])
            page, _, _ = self.request(dossier)
            sondes[ecarte] = self.sonde(dossier, ecarte, 'ecarte')
            self.submit(page, '/validation', {})
            prep.execute_qualification(self.data, self.starts.get_nowait()['qualification_operation'], self.bound[1])
            return dossier
        self.juge_factice()
        dossier, _, envois, _ = self.lancer_avec_reprises([retenu, 'openai/gpt-5.6-sol'], lambda *arguments: None,
                                                          qualifier=qualifier)
        self.assertEqual({retenu, 'openai/gpt-5.6-sol'}, {model for model, _ in envois})
        html = self.evaluer(dossier)
        operations, _ = self.operations_du_dossier()
        revisions = {op['operation_id']: op['revision'] for op in operations}
        self.assertLess(revisions[sondes[retenu]], revisions[sondes[ecarte]])
        texte, phases = self.depenses(html)
        self.assertEqual(1, len(phases['Vérification des modèles choisis']))
        self.assertIn('0,0004 USD', phases['Vérification des modèles choisis'][0])
        self.assertIn('réf. <code>' + sondes[retenu][-8:], phases['Vérification des modèles choisis'][0])
        self.assertNotIn(sondes[ecarte][-8:], html)
        self.assertEqual(len(operations) - 1, sum(len(lignes) for lignes in phases.values()))

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
        # Niveau commun et niveau par modèle : des boutons radio de même nom, l'option choisie cochée
        reglages = [n for n in page.nodes if n['tag'] == 'input' and n['attrs'].get('type') == 'radio']
        self.assertEqual({'tier', 'effort:mistralai/mistral-small-2603', 'repetitions'}, {n['attrs']['name'] for n in reglages})
        self.assertTrue(all(n['attrs'].get('form') == 'configurations-form' for n in reglages))
        self.assertFalse([n for n in page.nodes if n['tag'] == 'select'])
        self.assertTrue(any(n['tag'] == 'fieldset' and n['attrs'].get('class') == 'segments'
                            and n['attrs'].get('aria-describedby') == 'reasoning-help' for n in page.nodes))
        fields = {'models': []}
        for node in page.nodes:
            if (node['tag'] == 'input' and node['attrs'].get('type') == 'radio' and 'checked' in node['attrs']
                    and node['attrs']['value'] and node['attrs']['name'] != 'repetitions'):
                fields[node['attrs']['name']] = node['attrs']['value']
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

    def test_limite_de_sortie_declaree_par_configuration(self):
        """Issue #448 : chaque configuration porte sa limite de sortie, connue avant le lancement

        Modes d'échec :
        1. le transport reçoit 4096 au lieu de la limite déclarée pour ce modèle ;
        2. l'estimation reste calculée sur 4096 jetons de sortie ;
        3. une demande au-delà de la borne publiée par la route est ramenée en silence à cette borne ;
        4. une borne absente du relevé devient un nombre, ou refuse la demande alors que le contrat
           n'exige pas cette preuve ;
        5. une valeur nulle ou non entière est acceptée ;
        6. la limite, sa borne et ses reprises ne sont pas lisibles avant le lancement, ou le
           formulaire ne relit pas la limite déclarée ;
        7. les reprises annoncent 8192 puis 16384 jetons quelle que soit la limite déclarée ;
        8. une nouvelle sélection après résultat réécrit la configuration lancée ;
        9. le niveau de raisonnement choisi par modèle est perdu ;
        10. décocher un modèle dont la limite est pré-remplie par la relecture refuse tout le formulaire ;
        11. un refus de limite ne nomme ni le modèle ni sa borne, ou ne ramène pas au choix des modèles,
            y compris quand le champ vide garde 4096 jetons au-delà d'une route qui publie moins
        """
        from decimal import Decimal
        with closing(storage.Store(self.data)) as store:
            fetched_at, raw = store._connection.execute('SELECT fetched_at, raw_json FROM s2_model_catalogue').fetchone()
            document = json.loads(raw)
            bornes = {'openai/gpt-5.6-sol': 16000, 'deepseek/deepseek-v4.1-flash': None,
                      'mistralai/mistral-small-2603': 32768}
            for model_id, detail in document['endpoints'].items():
                for endpoint in detail['endpoints']:
                    endpoint.update(context_length=64000, pricing={'prompt': '0.000002', 'completion': '0.00001'})
                    if bornes[model_id] is not None:
                        endpoint['max_completion_tokens'] = bornes[model_id]
            # Modèle B : ni la route ni le modèle ne publient de borne de complétion
            next(m for m in document['models'] if m['id'] == 'deepseek/deepseek-v4.1-flash').pop('top_provider')
            store._connection.execute('UPDATE s2_model_catalogue SET raw_json=? WHERE fetched_at=?',
                                      (storage._strict_json(document), fetched_at))
        dossier = self.exemple_qualifie()
        prefix = dossier.rsplit('/', 1)[1]
        configurations = dossier + '/configurations'
        page, _, _ = self.request(configurations)
        self.examine(page, configurations, 'limites de sortie', 'Continuer')
        champs = {n['attrs']['name']: n['attrs'] for n in page.nodes
                  if n['tag'] == 'input' and n['attrs'].get('name', '').startswith('max_tokens:')}
        self.assertEqual({'max_tokens:' + model_id for model_id in bornes}, set(champs))
        self.assertEqual('16000', champs['max_tokens:openai/gpt-5.6-sol']['max'])
        self.assertNotIn('max', champs['max_tokens:deepseek/deepseek-v4.1-flash'])
        limites = next(n for n in page.nodes if n['tag'] == 'details' and 'Ajuster la limite de sortie' in n['text'])
        self.assertIn('Modèle B · borne publiée inconnue', limites['text'])
        self.assertIn('Modèle A · borne publiée : 16000', limites['text'])
        form = page.form('/configurations')
        choix = {'models': list(bornes), 'tier': 'low', 'effort:mistralai/mistral-small-2603': 'none'}
        for refus in ({'max_tokens:openai/gpt-5.6-sol': '0'}, {'max_tokens:openai/gpt-5.6-sol': '12k'},
                      {'max_tokens:openai/gpt-5.6-sol': '-5'}):
            self.request(form['action'], form['fields'] | choix | refus, status=400)
        refus, _, _ = self.request(form['action'], form['fields'] | choix | {
            'max_tokens:openai/gpt-5.6-sol': '16001'}, status=400)
        self.assertIn('Modèle A : la limite de sortie de 16001 jetons dépasse sa borne publiée : 16000 jetons au plus. '
                      'Indiquez une limite plus basse dans « Ajuster la limite de sortie par modèle ». '
                      'Rien n’a été enregistré.', refus.visible)
        self.assertEqual(configurations, refus.link('Revenir au choix des modèles'))
        refus, _, _ = self.request(form['action'], form['fields'] | choix | {
            'max_tokens:deepseek/deepseek-v4.1-flash': '64000'}, status=400)
        self.assertIn('Modèle B : la limite de sortie de 64000 jetons dépasse ce que sa fenêtre de contexte laisse '
                      'après cet exemple', refus.visible)
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(0, store._connection.execute('SELECT count(*) FROM s4_campaigns').fetchone()[0])
        recap = self.submit(page, '/configurations', choix | {
            'max_tokens:openai/gpt-5.6-sol': '12000', 'max_tokens:deepseek/deepseek-v4.1-flash': '6000',
            'max_tokens:mistralai/mistral-small-2603': ''})
        with closing(storage.Store(self.data)) as store:
            panel = campaigns.inspect(store, prefix + '-c1')['manifest']['panel']
        self.assertEqual([12000, 6000, 4096], [c['parameters']['max_tokens'] for c in panel])
        self.assertEqual([12000, 6000, 4096], [c['estimate']['assumptions']['output_tokens'] for c in panel])
        self.assertEqual([16000, None, 32768], [c['output_bound'] for c in panel])
        self.assertEqual('none', panel[2]['effort'])
        for config in panel:
            attendu = (Decimal('0.000002') * config['estimate']['assumptions']['input_tokens']
                       + Decimal('0.00001') * config['parameters']['max_tokens'])
            self.assertEqual(attendu, Decimal(config['estimate']['amount_usd']))
        self.assertEqual(['Modèle', 'Niveau de raisonnement', 'Limite de sortie', 'Coût estimé'],
                         [n['text'] for n in recap.nodes if n['tag'] == 'th' and n['attrs'].get('scope') == 'col'])
        lignes = {lettre: next(n['text'] for n in recap.nodes if n['tag'] == 'tr' and 'Modèle ' + lettre in n['text'])
                  for lettre in 'ABC'}
        self.assertIn('12000 jetons · borne publiée : 16000 · reprise : 16000', lignes['A'])
        self.assertIn('6000 jetons · borne publiée inconnue · sans reprise', lignes['B'])
        self.assertIn('4096 jetons · borne publiée : 32768 · reprises : 8192 puis 16384', lignes['C'])
        reprises = sum(Decimal('0.000002') * c['estimate']['assumptions']['input_tokens'] * len(paliers)
                       + Decimal('0.00001') * sum(paliers)
                       for c, paliers in ((panel[0], [16000]), (panel[2], [8192, 16384])))
        from benchmark_web.fragments import montant_lisible
        self.assertIn('Coût estimé si chaque modèle était repris jusqu’à sa dernière limite : '
                      + montant_lisible(str(reprises)) + ' USD', recap.visible)
        self.assertNotIn('8192 puis 16384 jetons de sortie', recap.visible)
        # Relecture : le formulaire rend les limites déclarées, vide pour la limite par défaut
        page, _, _ = self.request(configurations)
        valeurs = {n['attrs']['name']: n['attrs'].get('value', '') for n in page.nodes
                   if n['tag'] == 'input' and n['attrs'].get('name', '').startswith('max_tokens:')}
        self.assertEqual({'max_tokens:openai/gpt-5.6-sol': '12000', 'max_tokens:deepseek/deepseek-v4.1-flash': '6000',
                          'max_tokens:mistralai/mistral-small-2603': ''}, valeurs)
        self.submit(recap, '/start', {})
        attempts = self.starts.get_nowait()['candidate_attempts']
        envois = []

        def fabrique(channel_id, key):
            def candidat(operation, request):
                config = request['requested_configuration']
                envois.append((config['model'], config['parameters']['max_tokens']))
                value = self.candidate(operation, request)
                # Coût connu : un coût inconnu fermerait l'admission avant le dernier modèle
                value['cost'].update(status='KNOWN', amount='0.05')
                return value
            return candidat
        execution.execute_launch(self.data, attempts, transport_factory=fabrique,
                                 access_secret=SECRET, access_transport=self.access)
        self.assertEqual([('openai/gpt-5.6-sol', 12000), ('deepseek/deepseek-v4.1-flash', 6000),
                          ('mistralai/mistral-small-2603', 4096)], envois)
        # Retrouvable après résultat : les réglages utilisés portent la limite envoyée
        reglages, _, _ = self.request(dossier + '/campaigns/' + prefix + '-c1/configurations')
        sections = [n['text'] for n in reglages.nodes if n['tag'] == 'section']
        self.assertIn('12000', next(t for t in sections if 'Modèle A' in t))
        self.assertIn('Limite de tokens', next(t for t in sections if 'Modèle A' in t))
        self.assertIn('6000', next(t for t in sections if 'Modèle B' in t))
        # Modèle A décoché : sa limite pré-remplie part avec le formulaire, comme dans un navigateur, et reste ignorée
        page, _, _ = self.request(configurations)
        self.submit(page, '/configurations', valeurs | {'models': list(bornes)[1:], 'tier': 'low'})
        with closing(storage.Store(self.data)) as store:
            sans_a = campaigns.inspect(store, prefix + '-c2')['manifest']['panel']
        self.assertEqual([('deepseek/deepseek-v4.1-flash', 6000), ('mistralai/mistral-small-2603', 4096)],
                         [(c['model'], c['parameters']['max_tokens']) for c in sans_a])
        # Après résultat, une autre limite ouvre une nouvelle comparaison ; la configuration lancée reste
        page, _, _ = self.request(configurations)
        self.submit(page, '/configurations', choix | {'max_tokens:openai/gpt-5.6-sol': '8000'})
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(panel, campaigns.inspect(store, prefix + '-c1')['manifest']['panel'])
            suivante = campaigns.inspect(store, prefix + '-c3')['manifest']['panel']
        self.assertEqual([8000, 4096, 4096], [c['parameters']['max_tokens'] for c in suivante])
        # Route de Modèle C publiée sous 4096 : le champ vide garde 4096, refusé avec le modèle et sa borne
        with closing(storage.Store(self.data)) as store:
            document = json.loads(store._connection.execute('SELECT raw_json FROM s2_model_catalogue').fetchone()[0])
            document['endpoints']['mistralai/mistral-small-2603']['endpoints'][0]['max_completion_tokens'] = 2048
            store._connection.execute('UPDATE s2_model_catalogue SET raw_json=?', (storage._strict_json(document),))
        page, _, _ = self.request(configurations)
        refus, _, _ = self.request(form['action'], page.form('/configurations')['fields'] | choix, status=400)
        self.assertIn('Modèle C : la limite de sortie de 4096 jetons dépasse sa borne publiée : 2048 jetons au plus.',
                      refus.visible)
        self.submit(page, '/configurations', choix | {'max_tokens:mistralai/mistral-small-2603': '2048'})
        with closing(storage.Store(self.data)) as store:
            self.assertEqual(2048, campaigns.inspect(store, prefix + '-c4')['manifest']['panel'][2]['parameters']['max_tokens'])
        self.assertEqual(len(envois), sum(call[0] == 'candidat' for call in self.calls))

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


    def test_controle_juge_reponse_construite(self):
        """Contrôle d'une réponse construite : transports fictifs, aucun fournisseur"""
        from decimal import Decimal
        from hashlib import sha256
        from benchmark import judgment, runtime
        from benchmark.transports import openrouter
        from benchmark.validation import digest

        class Horloge(datetime):
            @classmethod
            def now(cls, tz=None):
                return self.clock

        self.enterContext(patch.object(openrouter, 'datetime', Horloge))
        dossier, _page = self.exemple()
        dossier_id = dossier.rsplit('/', 1)[1]
        with closing(storage.Store(self.data)) as store:
            revision = store._connection.execute(
                'SELECT MAX(revision) FROM dossier_revisions WHERE dossier_id=?', (dossier_id,)).fetchone()[0]
            avant = store._connection.execute('SELECT COUNT(*) FROM s5_evaluations').fetchone()[0]
        self.assertIsInstance(revision, int)
        note = 'Décision retenue : atelier validé.'
        sortie = 'Décision retenue : atelier validé.'
        reference = 'Éléments synthétiques hors décision retenue FIXTURE-REF-549'
        marqueur = 'MARQUEUR_MATRICE_NON_ENVOYE'

        def empreinte(contenu):
            return sha256(contenu.encode()).hexdigest()

        def piece(identifiant, nom, contenu):
            return dict(piece_id=identifiant, name=nom, sha256=empreinte(contenu), content=contenu)

        def contrat(locaux=None, secondaires=None):
            spec = dict(result_expected='Restituer la décision retenue de la note fictive',
                        obligations=[dict(id='O1', description='Restituer la décision retenue')],
                        eliminatory_errors=[dict(id='E1', description='Inventer une décision')],
                        secondary_criteria=[] if secondaires is None else secondaires,
                        limits=['Fixture synthétique'])
            if locaux is not None:
                spec['local_criterion_ids'] = locaux
            return dict(instruction='Extraire la décision retenue de la note fictive',
                        deliverables=['Une ligne pour la décision retenue'],
                        criteria=['Restituer la décision retenue'], acceptable_ambiguities=[], specification=spec)

        def transport_courant():
            return self.assistants['judgment_transport']

        def surveiller(transport):
            appels = []
            origine = transport.prepare

            def prepare(operation, request, api_key=None):
                appels.append(operation['operation_id'])
                return origine(operation, request, api_key=api_key)

            transport.prepare = prepare
            return appels

        def budget(identifiant, limite):
            with closing(storage.Store(self.data)) as store:
                store.create_budget(identifiant, limite, 'USD')

        origine_attendue = dict(producteur='Opérateur fixture', date='2026-10-21T08:15:00+00:00',
                                source='Pièce synthétique du parcours')

        def demande(transport, operation_id, control_id, budget_id, reponse, locaux=None, secondaires=None):
            return dict(operation_id=operation_id, control_id=control_id, dossier_id=dossier_id, revision=revision,
                        budget_id=budget_id, reserve_amount=transport._quote['reserve_usd'],
                        requested_configuration=transport._quote, provenance='reponse-construite-de-controle',
                        authority='requester-controle', origine=origine_attendue, contrat=contrat(locaux, secondaires),
                        compte_rendu=piece('cr-c1', 'compte-rendu.md', note),
                        reponse=piece('rep-c1', 'reponse.md', reponse),
                        reference=piece('ref-c1', 'reference.md', reference))

        def ouvrir():
            return closing(storage.Store(self.data))

        with self.subTest('réponse construite acceptée'):
            http = self.juge_factice(constat=lambda _critere: ('PASS', 'candidate', 'Contrôle couvert'),
                                     cle='fixture-control-key')
            transport = transport_courant()
            appels = surveiller(transport)
            budget('controle-ok', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-accepte', 'ctrl-accepte', 'controle-ok', sortie), transport)
            self.assertEqual(1, len(appels))
            with ouvrir() as store:
                reserve = judgment.inspect_control(store, 'c-accepte')
                brut = json.loads(store._connection.execute(
                    'SELECT resources_json FROM operations WHERE operation_id=?', ('c-accepte',)).fetchone()[0])
            self.assertEqual(origine_attendue, reserve['origine'])
            self.assertEqual(origine_attendue, json.loads(brut[0])['request']['origine'])
            judgment.execute_control(self.data, 'c-accepte', transport)
            self.assertEqual(1, http.request.call_count)
            with ouvrir() as store:
                vu = judgment.inspect_control(store, 'c-accepte')
                operation = next(row for row in store.inspect_operations() if row['operation_id'] == 'c-accepte')
                self.assertEqual('benchmark-lab-x/judgment-control/v1', operation['engine_version'])
                self.assertIsNone(store._connection.execute('SELECT 1 FROM s4_results WHERE operation_id=?', ('c-accepte',)).fetchone())
                self.assertIsNone(store._connection.execute("SELECT 1 FROM pieces WHERE role='controle-construit'").fetchone())
                self.assertIsNone(store._connection.execute('SELECT 1 FROM pieces WHERE piece_id=?', ('rep-c1',)).fetchone())
                self.assertEqual(avant, store._connection.execute('SELECT COUNT(*) FROM s5_evaluations').fetchone()[0])
                runtime.verify(store)
            self.assertEqual('reponse-construite-de-controle', vu['provenance'])
            self.assertEqual(origine_attendue, vu['origine'])
            self.assertFalse(vu['reference_qualifiee'])
            self.assertEqual('SATISFAIT', vu['verdict'])
            self.assertEqual('SATISFAIT', vu['proposal']['proposed_verdict'])
            self.assertEqual('RECEIVED', vu['state'])
            self.assertIsNone(vu['incident'])
            self.assertEqual(transport._quote['model'], vu['model'])
            self.assertEqual(transport._quote['revision'], vu['revision'])
            self.assertEqual(transport._quote['profile_sha256'], vu['profile_sha256'])
            self.assertEqual(transport._quote['prompt_sha256'], vu['prompt_sha256'])
            self.assertEqual(transport._quote['reserve_usd'], vu['reserve_amount'])
            self.assertEqual(empreinte(sortie), next(row['sha256'] for row in vu['pieces'] if row['kind'] == 'reponse'))
            self.assertTrue(all(row['role'] == 'controle-construit' for row in vu['pieces']))
            corps = storage._strict_json(self.judged[-1])
            self.assertNotIn(marqueur, corps)
            self.assertNotIn('verdict_attendu', corps)
            self.assertIn(reference, corps)
            self.assertNotIn('emission', vu['proposal'])
            self.assertNotIn('Opérateur fixture', corps)
            self.assertNotIn('Pièce synthétique du parcours', corps)
            page, _, _ = self.request(dossier + '/controles/c-accepte')
            self.assertIn('Réponse construite', page.visible)
            self.assertIn('sortie construite', page.visible)
            self.assertIn(origine_attendue['producteur'], page.visible)
            self.assertIn('21 octobre 2026 à 08:15:00 UTC', page.visible)
            self.assertIn(origine_attendue['source'], page.visible)
            self.assertIn(empreinte(sortie), page.visible)
            self.assertIn('Elle n’est pas un reçu candidat.', page.visible)
            self.assertIn('Ce contrôle ne qualifie pas le juge.', page.visible)
            cas, _, _ = self.request(dossier)
            self.assertNotIn('Sortie construite', cas.visible)
            with self.assertRaises(storage.ConflictError):
                judgment.execute_control(self.data, 'c-accepte', transport)
            self.assertEqual(1, http.request.call_count)

        with self.subTest('pièce altérée rejetée avant envoi'):
            http = self.juge_factice(cle='fixture-control-key')
            transport = transport_courant()
            appels = surveiller(transport)
            budget('controle-altere', '5')
            requete = demande(transport, 'c-altere', 'ctrl-altere', 'controle-altere', sortie)
            requete['reponse'] = dict(requete['reponse'], content=sortie + 'x')
            with ouvrir() as store:
                with self.assertRaisesRegex(storage.IntegrityError, 'Empreinte de pièce divergente'):
                    judgment.reserve_control(store, requete, transport)
                self.assertIsNone(store._connection.execute('SELECT 1 FROM operations WHERE operation_id=?', ('c-altere',)).fetchone())
            self.assertEqual([], appels)
            self.assertEqual(0, http.request.call_count)

        with self.subTest('nom de sortie candidate refusé'):
            transport = transport_courant()
            appels = surveiller(transport)
            requete = demande(transport, 'c-brute', 'ctrl-brute', 'controle-altere', sortie)
            requete['reponse'] = piece('rep-c1', 'Sortie brute c-brute', sortie)
            with ouvrir() as store, self.assertRaises(ValueError):
                judgment.reserve_control(store, requete, transport)
            self.assertEqual([], appels)

        with self.subTest('provenance obligatoire absente ou invalide'):
            http = self.juge_factice(cle='fixture-control-key')
            transport = transport_courant()
            appels = surveiller(transport)
            budget('controle-provenance', '5')
            base = demande(transport, 'c-prov', 'ctrl-prov', 'controle-provenance', sortie)

            def refuser(requete):
                with ouvrir() as store:
                    with self.assertRaises((ValueError, storage.IntegrityError)):
                        judgment.reserve_control(store, requete, transport)
                    self.assertIsNone(store._connection.execute(
                        'SELECT 1 FROM operations WHERE operation_id=?', (requete['operation_id'],)).fetchone())

            for champ in ('producteur', 'date', 'source'):
                partielle = dict(base['origine'])
                del partielle[champ]
                refuser(dict(base, operation_id='c-sans-' + champ, control_id='ctrl-sans-' + champ, origine=partielle))
            sans = dict(base)
            del sans['origine']
            refuser(dict(sans, operation_id='c-sans-origine', control_id='ctrl-sans-origine'))
            for nom, partielle in (
                    ('date', dict(base['origine'], date='demain')),
                    ('producteur', dict(base['origine'], producteur='')),
                    ('source', dict(base['origine'], source='')),
                    ('fuseau', dict(base['origine'], date='2026-10-21T08:15:00'))):
                refuser(dict(base, operation_id='c-inv-' + nom, control_id='ctrl-inv-' + nom, origine=partielle))
            sans_empreinte = dict(base, operation_id='c-sans-empreinte', control_id='ctrl-sans-empreinte')
            reponse = dict(sans_empreinte['reponse'])
            del reponse['sha256']
            sans_empreinte['reponse'] = reponse
            refuser(sans_empreinte)
            self.assertEqual([], appels)
            self.assertEqual(0, http.request.call_count)

        with self.subTest('budget insuffisant bloque l’envoi'):
            transport = transport_courant()
            appels = surveiller(transport)
            budget('controle-court', '0.01')
            with ouvrir() as store, self.assertRaises(storage.BudgetError):
                judgment.reserve_control(store, demande(transport, 'c-court', 'ctrl-court', 'controle-court', sortie), transport)
            self.assertEqual([], appels)
            self.assertEqual(0, http.request.call_count)

        with self.subTest('verdict proposé sans défaut prouvé'):
            http = self.juge_factice(cle='fixture-control-key')
            transport = transport_courant()
            budget('controle-defaut', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-defaut', 'ctrl-defaut', 'controle-defaut', sortie), transport)
            judgment.execute_control(self.data, 'c-defaut', transport)
            with ouvrir() as store:
                vu = judgment.inspect_control(store, 'c-defaut')
            self.assertEqual('NE SATISFAIT PAS', vu['verdict'])
            self.assertEqual('SATISFAIT', vu['proposal']['proposed_verdict'])
            self.assertEqual(1, http.request.call_count)

        with self.subTest('contrôle absent ou indéterminé'):
            http = self.juge_factice(constat=lambda _critere: ('INDETERMINE', 'evidence', 'Preuve insuffisante'),
                                     cle='fixture-control-key')
            transport = transport_courant()
            budget('controle-indet', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-indet', 'ctrl-indet', 'controle-indet', sortie), transport)
            judgment.execute_control(self.data, 'c-indet', transport)
            with ouvrir() as store:
                vu = judgment.inspect_control(store, 'c-indet')
            self.assertEqual('INDETERMINE', vu['verdict'])
            self.assertNotEqual('SATISFAIT', vu['verdict'])

            http = self.juge_factice(constat=lambda _critere: ('PASS', 'candidate', 'Contrôle couvert'),
                                     ajuste=lambda _vue, constats: constats[:-1], cle='fixture-control-key')
            transport = transport_courant()
            budget('controle-trou', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-trou', 'ctrl-trou', 'controle-trou', sortie), transport)
            judgment.execute_control(self.data, 'c-trou', transport)
            with ouvrir() as store:
                vu = judgment.inspect_control(store, 'c-trou')
            self.assertEqual('INDETERMINE', vu['verdict'])

            http = self.juge_factice(constat=lambda _critere: ('PASS', 'candidate', 'Sans preuve'),
                                     ajuste=lambda _vue, constats: [dict(constat, evidence=[]) for constat in constats],
                                     cle='fixture-control-key')
            transport = transport_courant()
            budget('controle-preuve', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-preuve', 'ctrl-preuve', 'controle-preuve', sortie), transport)
            judgment.execute_control(self.data, 'c-preuve', transport)
            with ouvrir() as store:
                vu = judgment.inspect_control(store, 'c-preuve')
            self.assertIsNone(vu['verdict'])
            self.assertEqual('UNUSABLE_JUDGMENT_PROPOSAL', vu['incident'])
            self.assertIsNone(vu['proposal'])

            http = self.juge_factice(constat=lambda _critere: ('PASS', 'candidate', 'Contrôle couvert'),
                                     cle='fixture-control-key')
            transport = transport_courant()
            budget('controle-local', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(
                    transport, 'c-local', 'ctrl-local', 'controle-local', sortie, locaux=['O1']), transport)
            judgment.execute_control(self.data, 'c-local', transport)
            with ouvrir() as store:
                vu = judgment.inspect_control(store, 'c-local')
            self.assertEqual('INDETERMINE', vu['verdict'])
            self.assertEqual('INDETERMINE', next(row['status'] for row in vu['proposal']['findings'] if row['criterion_id'] == 'O1'))

        with self.subTest('incident conservé sans verdict fabriqué'):
            http = self.juge_factice(contenu='{}', cle='fixture-control-key')
            transport = transport_courant()
            budget('controle-incident', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-incident', 'ctrl-incident', 'controle-incident', sortie), transport)
            judgment.execute_control(self.data, 'c-incident', transport)
            with ouvrir() as store:
                vu = judgment.inspect_control(store, 'c-incident')
            self.assertEqual('UNUSABLE_JUDGMENT_PROPOSAL', vu['incident'])
            self.assertIsNone(vu['verdict'])
            self.assertIsNone(vu['proposal'])
            self.assertEqual('RECEIVED', vu['state'])
            self.assertEqual(1, http.request.call_count)

        with self.subTest('coût inconnu conserve la réserve'):
            http = self.juge_factice(constat=lambda _critere: ('PASS', 'candidate', 'Contrôle couvert'),
                                     cout=None, cle='fixture-control-key')
            transport = transport_courant()
            appels = surveiller(transport)
            budget('controle-inconnu', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-inconnu', 'ctrl-inconnu', 'controle-inconnu', sortie), transport)
            judgment.execute_control(self.data, 'c-inconnu', transport)
            with ouvrir() as store:
                vu = judgment.inspect_control(store, 'c-inconnu')
                enveloppe = store.inspect_budget('controle-inconnu')
            self.assertEqual('UNKNOWN', vu['cost']['status'])
            self.assertIsNone(vu['cost']['amount'])
            self.assertEqual(Decimal(0), Decimal(enveloppe['spent']))
            self.assertEqual(Decimal(transport._quote['reserve_usd']), Decimal(enveloppe['reserved']))
            deja = len(appels)
            with ouvrir() as store, self.assertRaises(storage.BudgetError):
                judgment.reserve_control(store, demande(transport, 'c-inconnu-2', 'ctrl-inconnu', 'controle-inconnu', sortie), transport)
            self.assertEqual(deja, len(appels))
            self.assertEqual(1, http.request.call_count)

        with self.subTest('reprises bornées sans double émission'):
            http = self.juge_factice(statut=429, cle='fixture-control-key')
            transport = transport_courant()
            budget('controle-relance', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-relance', 'ctrl-relance', 'controle-relance', sortie), transport)
            courant = 'c-relance'
            for attente in (30, 120, 600, 1800):
                judgment.execute_control(self.data, courant, transport)
                with ouvrir() as store:
                    self.assertIsNone(judgment.resume_control(store, courant, transport))
                    self.clock += timedelta(seconds=attente - 1)
                    self.assertIsNone(judgment.resume_control(store, courant, transport))
                    self.clock += timedelta(seconds=1)
                    suivant = judgment.resume_control(store, courant, transport)
                self.assertIsNotNone(suivant)
                courant = suivant
            judgment.execute_control(self.data, courant, transport)
            self.clock += timedelta(seconds=100000)
            with ouvrir() as store:
                self.assertIsNone(judgment.resume_control(store, courant, transport))
                serie = [row for row in store.inspect_operations()
                         if row['engine_version'] == 'benchmark-lab-x/judgment-control/v1'
                         and json.loads(row['resources'][0])['control_id'] == 'ctrl-relance']
            self.assertEqual(5, len(serie))
            self.assertEqual(5, http.request.call_count)
            self.assertTrue(all(row['receipt']['observed_configuration']['incident'] == 'RATE_LIMITED' for row in serie))
            self.assertTrue(all(json.loads(row['resources'][0])['request']['origine'] == origine_attendue for row in serie))

        with self.subTest('arrêt empêche les appels suivants'):
            http = self.juge_factice(constat=lambda _critere: ('PASS', 'candidate', 'Contrôle couvert'),
                                     cle='fixture-control-key')
            transport = transport_courant()
            appels = surveiller(transport)
            budget('controle-arret', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-arret-1', 'ctrl-arret', 'controle-arret', sortie), transport)
                judgment.reserve_control(store, demande(transport, 'c-arret-2', 'ctrl-arret', 'controle-arret', sortie), transport)
            origine = http.request.side_effect
            http.request.side_effect = OSError('apres connexion')
            try:
                resultat = judgment.execute_series(self.data, ['c-arret-1', 'c-arret-2'], transport)
            finally:
                http.request.side_effect = origine
            self.assertEqual(dict(stopped=True, error='OSError'), resultat)
            with ouvrir() as store:
                premier = judgment.inspect_control(store, 'c-arret-1')
                second = next(row for row in store.inspect_operations() if row['operation_id'] == 'c-arret-2')
            self.assertEqual('AMBIGUOUS', premier['state'])
            self.assertIsNone(premier['verdict'])
            self.assertEqual({'status': 'NOT_SENT', 'control_stop': 'JUDGMENT_STOPPED'}, second['receipt']['result'])
            deja = len(appels)
            with ouvrir() as store, self.assertRaisesRegex(storage.ConflictError, 'Contrôle arrêté'):
                judgment.reserve_control(store, demande(transport, 'c-arret-3', 'ctrl-arret', 'controle-arret', sortie), transport)
            self.assertEqual(deja, len(appels))

        with self.subTest('identifiant de modèle divergent arrête la série'):
            http = self.juge_factice(constat=lambda _critere: ('PASS', 'candidate', 'Contrôle couvert'),
                                     cle='fixture-control-key')
            transport = transport_courant()
            budget('controle-modele', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-modele-1', 'ctrl-modele', 'controle-modele', sortie), transport)
                judgment.reserve_control(store, demande(transport, 'c-modele-2', 'ctrl-modele', 'controle-modele', sortie), transport)
            origine = http.request.side_effect

            def autre_modele(*args, **kwargs):
                origine(*args, **kwargs)
                document = json.loads(http.getresponse.return_value.read.return_value)
                document['model'] = 'autre/modele-non-demande'
                http.getresponse.return_value.read.return_value = storage._strict_json(document).encode()

            http.request.side_effect = autre_modele
            resultat = judgment.execute_series(self.data, ['c-modele-1', 'c-modele-2'], transport)
            self.assertEqual(dict(stopped=True, error='UNUSABLE_JUDGMENT_PROPOSAL'), resultat)
            self.assertEqual(1, http.request.call_count)
            with ouvrir() as store:
                premier = judgment.inspect_control(store, 'c-modele-1')
                second = next(row for row in store.inspect_operations() if row['operation_id'] == 'c-modele-2')
            self.assertEqual('RECEIVED', premier['state'])
            self.assertEqual('UNUSABLE_JUDGMENT_PROPOSAL', premier['incident'])
            self.assertIsNone(premier['verdict'])
            self.assertEqual('KNOWN', premier['cost']['status'])
            self.assertEqual(Decimal('0.001'), Decimal(premier['cost']['amount']))
            self.assertEqual({'status': 'NOT_SENT', 'control_stop': 'JUDGMENT_STOPPED'}, second['receipt']['result'])
            self.assertEqual('KNOWN', second['observed_cost']['status'])
            self.assertEqual(Decimal('0'), Decimal(second['observed_cost']['amount']))

        with self.subTest('incident relançable laisse partir l’appel suivant'):
            http = self.juge_factice(statut=429, cle='fixture-control-key')
            transport = transport_courant()
            budget('controle-suite', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-suite-1', 'ctrl-suite', 'controle-suite', sortie), transport)
                judgment.reserve_control(store, demande(transport, 'c-suite-2', 'ctrl-suite-2', 'controle-suite', sortie), transport)
            resultat = judgment.execute_series(self.data, ['c-suite-1', 'c-suite-2'], transport)
            self.assertEqual(dict(stopped=False, error=None), resultat)
            self.assertEqual(2, http.request.call_count)
            with ouvrir() as store:
                premier = judgment.inspect_control(store, 'c-suite-1')
                second = judgment.inspect_control(store, 'c-suite-2')
            self.assertEqual('RATE_LIMITED', premier['incident'])
            self.assertEqual('RATE_LIMITED', second['incident'])

        with self.subTest('connexion impossible n’arrête pas la série'):
            http = self.juge_factice(constat=lambda _critere: ('PASS', 'candidate', 'Contrôle couvert'),
                                     cle='fixture-control-key')
            transport = transport_courant()
            budget('controle-muet', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-muet-1', 'ctrl-muet', 'controle-muet', sortie), transport)
                judgment.reserve_control(store, demande(transport, 'c-muet-2', 'ctrl-muet', 'controle-muet', sortie), transport)
            http.connect.side_effect = [OSError('dns'), None]
            resultat = judgment.execute_series(self.data, ['c-muet-1', 'c-muet-2'], transport)
            self.assertEqual(dict(stopped=False, error=None), resultat)
            with ouvrir() as store:
                premier = next(row for row in store.inspect_operations() if row['operation_id'] == 'c-muet-1')
                second = judgment.inspect_control(store, 'c-muet-2')
            self.assertEqual({'status': 'NOT_SENT'}, premier['receipt']['result'])
            self.assertEqual('CONNECTION_FAILED', premier['receipt']['observed_configuration']['incident'])
            self.assertEqual('SATISFAIT', second['verdict'])
            self.assertEqual(1, http.request.call_count)

        with self.subTest('nouvelle version de pièce'):
            http = self.juge_factice(profil=openrouter.AUTOMATIC_JUDGMENT_PROFILE,
                                     constat=lambda _critere: ('PASS', 'candidate', 'Contrôle couvert'),
                                     cle='fixture-control-key')
            transport = transport_courant()
            appels = surveiller(transport)
            budget('controle-version', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-version-1', 'ctrl-version', 'controle-version', sortie), transport)
            judgment.execute_control(self.data, 'c-version-1', transport)
            with ouvrir() as store:
                vu = judgment.inspect_control(store, 'c-version-1')
                premiere = next(row for row in store.inspect_operations() if row['operation_id'] == 'c-version-1')
                figee = premiere['resources'][0]
            self.assertEqual('anthropic/claude-fable-5.1', vu['model'])
            self.assertEqual('anthropic/claude-fable-5.1-20260831', vu['revision'])
            self.assertEqual(transport._quote['profile_sha256'], vu['profile_sha256'])
            self.assertEqual(transport._quote['prompt_sha256'], vu['prompt_sha256'])
            self.assertEqual('SATISFAIT', vu['verdict'])
            suivante = sortie + ' v2'
            with ouvrir() as store:
                judgment.reserve_control(store, demande(transport, 'c-version-2', 'ctrl-version-2', 'controle-version', suivante), transport)
                vue = judgment.inspect_control(store, 'c-version-2')
                encore = next(row for row in store.inspect_operations() if row['operation_id'] == 'c-version-1')
            self.assertNotEqual(vu['review_sha256'], vue['review_sha256'])
            self.assertEqual(empreinte(suivante), next(row['sha256'] for row in vue['pieces'] if row['kind'] == 'reponse'))
            self.assertEqual(figee, encore['resources'][0])
            requete = demande(transport, 'c-version-3', 'ctrl-version-3', 'controle-version', suivante)
            requete['reponse'] = dict(requete['reponse'], content=suivante + 'x')
            deja = len(appels)
            with ouvrir() as store:
                with self.assertRaisesRegex(storage.IntegrityError, 'Empreinte de pièce divergente'):
                    judgment.reserve_control(store, requete, transport)
                inchangee = next(row for row in store.inspect_operations() if row['operation_id'] == 'c-version-1')
            self.assertEqual(deja, len(appels))
            self.assertEqual(figee, inchangee['resources'][0])
            self.assertEqual(1, http.request.call_count)

        def critere_secondaire():
            return dict(id='Q1', label='Clarté de la décision', favorable='excellent')

        def fige(operation_id):
            with ouvrir() as store:
                return store._connection.execute(
                    'SELECT receipt_json, observed_cost_json FROM operations WHERE operation_id=?',
                    (operation_id,)).fetchone()

        with self.subTest('mesure secondaire invalide rend la proposition inexploitable'):
            def executer(operation_id, control_id, budget_id, altère=None):
                http = self.juge_factice(constat=lambda _critere: ('PASS', 'candidate', 'Contrôle couvert'),
                                         mesure='atelier validé', cle='fixture-control-key')
                transport = transport_courant()
                budget(budget_id, '5')
                with ouvrir() as store:
                    judgment.reserve_control(store, demande(
                        transport, operation_id, control_id, budget_id, sortie,
                        secondaires=[critere_secondaire()]), transport)
                if altère is not None:
                    origine = http.request.side_effect

                    def reponse_altérée(*args, **kwargs):
                        origine(*args, **kwargs)
                        document = json.loads(http.getresponse.return_value.read.return_value)
                        proposition = json.loads(document['choices'][0]['message']['content'])
                        proposition['measures'] = [altère(proposition['measures'][0])]
                        document['choices'][0]['message']['content'] = storage._strict_json(proposition)
                        http.getresponse.return_value.read.return_value = storage._strict_json(document).encode()

                    http.request.side_effect = reponse_altérée
                judgment.execute_control(self.data, operation_id, transport)
                with ouvrir() as store:
                    return judgment.inspect_control(store, operation_id)

            valide = executer('c-mesure-ok', 'ctrl-mesure-ok', 'controle-mesure-ok')
            self.assertIsNone(valide['incident'])
            self.assertEqual('SATISFAIT', valide['verdict'])
            self.assertEqual('atelier validé', valide['proposal']['measures'][0]['value'])
            self.assertEqual('descriptif', valide['proposal']['measures'][0]['unit'])
            self.assertEqual(sortie, valide['proposal']['measures'][0]['evidence'][0]['passage'])
            inconnue = executer('c-mesure-vide', 'ctrl-mesure-vide', 'controle-mesure-vide',
                                lambda mesure: dict(mesure, value=None, evidence=[]))
            self.assertIsNone(inconnue['incident'])
            self.assertEqual([dict(criterion_id='Q1', value=None, unit='descriptif', evidence=[])],
                             inconnue['proposal']['measures'])
            for nom, suffixe, altère in (
                    ('unité', 'unite', lambda mesure: dict(mesure, unit='secondes')),
                    ('valeur', 'valeur', lambda mesure: dict(mesure, value={'libre': 1})),
                    ('preuve', 'preuve', lambda mesure: dict(mesure, evidence=[dict(
                        mesure['evidence'][0], passage='PASSAGE_ABSENT')]))):
                vu = executer('c-mesure-' + suffixe, 'ctrl-mesure-' + suffixe, 'controle-mesure-' + suffixe, altère)
                self.assertEqual('UNUSABLE_JUDGMENT_PROPOSAL', vu['incident'], nom)
                self.assertIsNone(vu['proposal'], nom)
                self.assertIsNone(vu['verdict'], nom)

        with self.subTest('contrôle persisté altéré refusé par la vérification'):
            def controle_valide(operation_id, budget_id):
                self.juge_factice(constat=lambda _critere: ('PASS', 'candidate', 'Contrôle couvert'),
                                  cle='fixture-control-key')
                transport = transport_courant()
                budget(budget_id, '5')
                with ouvrir() as store:
                    judgment.reserve_control(store, demande(
                        transport, operation_id, 'ctrl-' + operation_id, budget_id, sortie), transport)
                judgment.execute_control(self.data, operation_id, transport)

            def colonne(operation_id, nom):
                with ouvrir() as store:
                    return store._connection.execute(
                        f'SELECT {nom} FROM operations WHERE operation_id=?', (operation_id,)).fetchone()[0]

            def ecrire(operation_id, nom, valeur):
                with ouvrir() as store:
                    store._connection.execute(
                        f'UPDATE operations SET {nom}=? WHERE operation_id=?', (valeur, operation_id))

            controle_valide('c-audit-preuve', 'controle-audit-preuve')
            controle_valide('c-audit-liaison', 'controle-audit-liaison')
            with ouvrir() as store:
                runtime.verify(store)
            preuve = colonne('c-audit-preuve', 'receipt_json')
            recu = json.loads(preuve)
            passage = recu['result']['findings'][0]['evidence'][0]['passage']
            self.assertIn(passage, sortie)
            recu['result']['findings'][0]['evidence'][0]['passage'] = 'PASSAGE_ETRANGER'
            ecrire('c-audit-preuve', 'receipt_json', storage._strict_json(recu))
            with self.assertRaises(storage.IntegrityError):
                with ouvrir() as store:
                    runtime.verify(store)
            ecrire('c-audit-preuve', 'receipt_json', preuve)
            with ouvrir() as store:
                runtime.verify(store)
            liaison = colonne('c-audit-liaison', 'resources_json')
            ressources = json.loads(liaison)
            sauvee = json.loads(ressources[0])
            sauvee['review_sha256'] = '0' * 64
            ressources[0] = storage._strict_json(sauvee)
            ecrire('c-audit-liaison', 'resources_json', storage._strict_json(ressources))
            with self.assertRaises(storage.IntegrityError):
                with ouvrir() as store:
                    runtime.verify(store)
            ecrire('c-audit-liaison', 'resources_json', liaison)
            with ouvrir() as store:
                runtime.verify(store)
            controle_valide('c-audit-origine', 'controle-audit-origine')
            liaison = colonne('c-audit-origine', 'resources_json')
            ressources = json.loads(liaison)
            sauvee = json.loads(ressources[0])
            sauvee['request']['origine']['date'] = 'demain'
            ressources[0] = storage._strict_json(sauvee)
            ecrire('c-audit-origine', 'resources_json', storage._strict_json(ressources))
            with self.assertRaises(storage.IntegrityError):
                with ouvrir() as store:
                    runtime.verify(store)
            ecrire('c-audit-origine', 'resources_json', liaison)
            with ouvrir() as store:
                runtime.verify(store)
            for nom, valeur in (
                    ('producteur', 'Autre opérateur'),
                    ('date', '2026-11-02T09:00:00+00:00'),
                    ('source', 'Autre pièce synthétique')):
                ressources = json.loads(liaison)
                sauvee = json.loads(ressources[0])
                sauvee['request']['origine'][nom] = valeur
                ressources[0] = storage._strict_json(sauvee)
                ecrire('c-audit-origine', 'resources_json', storage._strict_json(ressources))
                with self.assertRaises(storage.IntegrityError, msg=nom):
                    with ouvrir() as store:
                        runtime.verify(store)
                ecrire('c-audit-origine', 'resources_json', liaison)
            ressources = json.loads(liaison)
            sauvee = json.loads(ressources[0])
            sauvee['request']['origine']['date'] = '2026-11-02T09:00:00+00:00'
            sauvee['review_sha256'] = digest(sauvee['content'])
            ressources[0] = storage._strict_json(sauvee)
            ecrire('c-audit-origine', 'resources_json', storage._strict_json(ressources))
            with self.assertRaises(storage.IntegrityError):
                with ouvrir() as store:
                    runtime.verify(store)
            ecrire('c-audit-origine', 'resources_json', liaison)
            ressources = json.loads(liaison)
            sauvee = json.loads(ressources[0])
            del sauvee['request']['origine']
            ressources[0] = storage._strict_json(sauvee)
            ecrire('c-audit-origine', 'resources_json', storage._strict_json(ressources))
            with self.assertRaises(storage.IntegrityError):
                with ouvrir() as store:
                    runtime.verify(store)
            ecrire('c-audit-origine', 'resources_json', liaison)
            with ouvrir() as store:
                runtime.verify(store)
                intact = judgment.inspect_control(store, 'c-audit-origine')
            self.assertEqual(origine_attendue, intact['origine'])
            page, _, _ = self.request(dossier + '/controles/c-audit-origine')
            self.assertIn(origine_attendue['producteur'], page.visible)
            self.assertIn('21 octobre 2026 à 08:15:00 UTC', page.visible)
            self.assertIn(origine_attendue['source'], page.visible)

            controle_valide('c-ancien', 'controle-ancien')
            liaison = colonne('c-ancien', 'resources_json')
            ressources = json.loads(liaison)
            sauvee = json.loads(ressources[0])
            del sauvee['request']['origine']
            sauvee['review_sha256'] = digest(sauvee['content'])
            ressources[0] = storage._strict_json(sauvee)
            ecrire('c-ancien', 'resources_json', storage._strict_json(ressources))
            with ouvrir() as store:
                runtime.verify(store)
                ancien = judgment.inspect_control(store, 'c-ancien')
            self.assertIsNone(ancien['origine'])
            self.assertEqual('SATISFAIT', ancien['verdict'])
            self.assertNotIn('producteur', storage._strict_json(ancien))
            page, _, _ = self.request(dossier + '/controles/c-ancien')
            self.assertIn('Réponse construite', page.visible)
            self.assertIn('ne déclare pas de producteur', page.visible)
            self.assertIn(empreinte(sortie), page.visible)
            self.assertNotIn('Producteur', page.visible)
            self.assertNotIn(origine_attendue['producteur'], page.visible)
            self.assertNotIn('21 octobre 2026 à 08:15:00 UTC', page.visible)

        with self.subTest('incident du dernier appel empêche une nouvelle réservation'):
            def mauvais_modele(http):
                origine = http.request.side_effect

                def reponse(*args, **kwargs):
                    origine(*args, **kwargs)
                    document = json.loads(http.getresponse.return_value.read.return_value)
                    document['model'] = 'autre/modele-non-demande'
                    http.getresponse.return_value.read.return_value = storage._strict_json(document).encode()

                http.request.side_effect = reponse

            http = self.juge_factice(constat=lambda _critere: ('PASS', 'candidate', 'Contrôle couvert'),
                                     cle='fixture-control-key')
            transport = transport_courant()
            budget('controle-stop-seul', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(
                    transport, 'c-stop-seul', 'ctrl-stop-seul', 'controle-stop-seul', sortie), transport)
            mauvais_modele(http)
            resultat = judgment.execute_series(self.data, ['c-stop-seul'], transport)
            self.assertEqual(dict(stopped=True, error='UNUSABLE_JUDGMENT_PROPOSAL'), resultat)
            conserve = fige('c-stop-seul')
            with ouvrir() as store:
                seul = judgment.inspect_control(store, 'c-stop-seul')
            self.assertEqual('RECEIVED', seul['state'])
            self.assertEqual('UNUSABLE_JUDGMENT_PROPOSAL', seul['incident'])
            self.assertEqual('KNOWN', seul['cost']['status'])
            self.assertEqual(Decimal('0.001'), Decimal(seul['cost']['amount']))
            with ouvrir() as store, self.assertRaisesRegex(storage.ConflictError, 'Contrôle arrêté'):
                judgment.reserve_control(store, demande(
                    transport, 'c-stop-seul-2', 'ctrl-stop-seul', 'controle-stop-seul', sortie), transport)
            self.assertEqual(conserve, fige('c-stop-seul'))
            with ouvrir() as store:
                self.assertIsNone(store._connection.execute(
                    'SELECT 1 FROM operations WHERE operation_id=?', ('c-stop-seul-2',)).fetchone())

            http = self.juge_factice(constat=lambda _critere: ('PASS', 'candidate', 'Contrôle couvert'),
                                     cle='fixture-control-key')
            transport = transport_courant()
            budget('controle-stop-fin', '5')
            with ouvrir() as store:
                judgment.reserve_control(store, demande(
                    transport, 'c-stop-fin-1', 'ctrl-stop-fin', 'controle-stop-fin', sortie), transport)
                judgment.reserve_control(store, demande(
                    transport, 'c-stop-fin-2', 'ctrl-stop-fin', 'controle-stop-fin', sortie), transport)
            origine = http.request.side_effect

            def second_mauvais(*args, **kwargs):
                origine(*args, **kwargs)
                if http.request.call_count < 2:
                    return
                document = json.loads(http.getresponse.return_value.read.return_value)
                document['model'] = 'autre/modele-non-demande'
                http.getresponse.return_value.read.return_value = storage._strict_json(document).encode()

            http.request.side_effect = second_mauvais
            resultat = judgment.execute_series(self.data, ['c-stop-fin-1', 'c-stop-fin-2'], transport)
            self.assertEqual(dict(stopped=True, error='UNUSABLE_JUDGMENT_PROPOSAL'), resultat)
            self.assertEqual(2, http.request.call_count)
            premier_fige = fige('c-stop-fin-1')
            second_fige = fige('c-stop-fin-2')
            with ouvrir() as store:
                premier = judgment.inspect_control(store, 'c-stop-fin-1')
                second = judgment.inspect_control(store, 'c-stop-fin-2')
            self.assertEqual('SATISFAIT', premier['verdict'])
            self.assertEqual('KNOWN', premier['cost']['status'])
            self.assertEqual(Decimal('0.001'), Decimal(premier['cost']['amount']))
            self.assertEqual('RECEIVED', second['state'])
            self.assertEqual('UNUSABLE_JUDGMENT_PROPOSAL', second['incident'])
            self.assertEqual('KNOWN', second['cost']['status'])
            self.assertEqual(Decimal('0.001'), Decimal(second['cost']['amount']))
            with ouvrir() as store, self.assertRaisesRegex(storage.ConflictError, 'Contrôle arrêté'):
                judgment.reserve_control(store, demande(
                    transport, 'c-stop-fin-3', 'ctrl-stop-fin', 'controle-stop-fin', sortie), transport)
            self.assertEqual(premier_fige, fige('c-stop-fin-1'))
            self.assertEqual(second_fige, fige('c-stop-fin-2'))

    def test_lanceur_vault_injecte_les_cles_dans_le_fils_seulement(self):
        # Vault est remplacé par un binaire factice : le lanceur pose les clés dans le fils seulement
        racine = Path(__file__).resolve().parents[1]
        factice = self.public / 'vault'
        factice.write_text(f'#!{sys.executable}\n'
                           'import os, sys\n'
                           'champ = [a for a in sys.argv if a.startswith("-field=")][0].split("=", 1)[1]\n'
                           'print("" if champ == os.environ["CHAMP_VIDE"] else "factice-" + champ)\n')
        factice.chmod(0o755)
        fichier_env = self.public / '.env'
        fichier_env.write_text('DASHSCOPE_BASE_URL=https://dash.example\nOPENROUTER_API_KEY=ignoree\n')
        marque = self.public / 'fils-lance'

        def lancer(champ_vide, commande):
            environnement = dict(os.environ, OPENROUTER_API_KEY='agent-fictif', VAULT_BIN=str(factice),
                                 BENCHMARK_ENV_FILE=str(fichier_env), CHAMP_VIDE=champ_vide)
            processus = subprocess.Popen([sys.executable, '-m', 'benchmark.vault_env', '--', *commande],
                                         cwd=racine, env=environnement, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                sortie, erreur = processus.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(processus.pid), signal.SIGKILL)
                processus.communicate()
                raise
            return processus.returncode, sortie, erreur

        enfant = [sys.executable, '-c',
                  'import json, os; print(json.dumps({k: os.environ.get(k) for k in '
                  '["OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_BASE_URL"]}))']
        code, sortie, erreur = lancer('aucun', enfant)
        self.assertEqual(0, code, erreur)
        observe = json.loads(sortie)
        self.assertEqual('factice-openrouter_benchmark', observe['OPENROUTER_API_KEY'])
        self.assertEqual('factice-deepseek_benchmark', observe['DEEPSEEK_API_KEY'])
        self.assertEqual('https://dash.example', observe['DASHSCOPE_BASE_URL'])

        # Un champ Vault vide arrête le lanceur avant le fils
        enfant_marque = [sys.executable, '-c', f'open({str(marque)!r}, "w").close()']
        code, _, erreur = lancer('mistral_benchmark', enfant_marque)
        self.assertNotEqual(0, code)
        self.assertIn('mistral_benchmark', erreur)
        self.assertFalse(marque.exists(), 'le fils ne doit pas se lancer')


if __name__ == '__main__':
    unittest.main()
