"""Fictional S6 regressions using S2–S5 primitives, independently of private reports"""
from base64 import b64encode
from copy import deepcopy
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
import multiprocessing
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from benchmark.acquisition import execution
from benchmark.acquisition import campaigns as c
from benchmark import evaluation as e, preparation as p, publications as pub, qualification as q, restitution as r, service, storage, web_api
from benchmark_web import projection, views
from benchmark_web.server import serve_web
from tests.test_s3_regressions import ACTOR, AUTHORITY, check, fixture, specification
from tests.test_s4_regressions import inputs, manifest, response
from tests.test_s5_regressions import RESPONSIBLE, EVALUATION_AUTHORITY, findings

# Empreintes de la page S6, identités et horodatages ramenés à une forme fixe
# Un changement d'octets de présentation sans incrément de PRESENTATION_VERSION échoue
_VOLATILE_PRESENTATION = re.compile(
    rb'\d{4}-\d{2}-\d{2}T[0-9:.+-]+|'
    rb'\b[0-9a-f]{64}\b|'
    rb'\b[0-9a-f]{32}\b|'
    rb'output-[0-9a-f]+'
)
_FIXTURE_PRESENTATION = {
    '6': {
        'index.html': '42cdea5a5ca3d6350405a54f543dbd351f0695d0946e3ac53a5a66f6942c3fcd',
        'style.css': 'e6160575de2d71a327dbc3237cb2779c4ec325faf5361be0a2fbd53d617c3355',
    },
}
# Feuille fictive distincte des octets actifs : aucun actif scellé n'est dupliqué
_PRIOR_STYLESHEET = b':root { color-scheme: light; }\n'


class Markup(HTMLParser):
    def __init__(self, raw):
        super().__init__()
        self.links, self.tags = [], []
        self.feed(raw.decode())

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == 'a':
            self.links.append(dict(attrs).get('href', ''))


def build(data, criterion_ids=('duration', 'present')):
    sid, view, reference = fixture(data)
    q.initialize(data)
    store = storage.Store(data)
    try:
        spec = specification(reference)
        spec['secondary_criteria'] = [dict(id=criterion_ids[0], measure='Durée fictive', proof='Sortie conservée', unit='s', favorable='lower', aggregation=None),
            dict(id=criterion_ids[1], measure='Présence fictive', proof='Sortie conservée', unit='bool', favorable='yes', aggregation=None)]
        candidate = q.draft(store, 'fixture', view['revision'], spec)
        qualified = q.qualify(store, candidate['contract_sha256'], reviewer=ACTOR, check=check)
        q.approve(store, candidate['contract_sha256'], qualified['qualification_id'], actor=ACTOR, authority=AUTHORITY)
        p.close_admission(store)
        c.initialize(data)
        e.initialize(data)
        store.create_budget('comparison', '100', 'TEST')
        m = manifest(candidate, 'comparison')
        names = ['error', 'tie', 'near', 'other', 'missing', 'unstarted']
        m['panel'] = [dict(deepcopy(m['panel'][0]), id=name, model='fictional-' + name) for name in names]
        m['cases'].append(dict(id='distinct', package_sha256=candidate['contract']['package_sha256']))
        m['plan'] = [dict(cell_id=name, case_id='distinct' if name == 'other' else 'notes', configuration_id=name) for name in names]
        m['attempt_policy']['order'] = names
        snapshot = c.create(store, m)
        authority, evidence = inputs(snapshot, cells=names, budget='comparison')
        authority['reserve_amounts'] = {name: '1' for name in names}
        c.admit(store, 'comparison', authority, evidence)
        records = {}
        values = [('error', '0.10001', '9', True), ('tie', '0.10001', '4', True),
                  ('near', '0.10002', True, False), ('other', '0.9', '200', True),
                  ('missing', None, None, True)]
        for name, amount, measure, boolean in values:
            aid = 'attempt-' + name
            c.reserve(store, 'comparison', name, aid)

            def transport(op, request):
                value = response(op, request)
                value['receipt']['result']['output'] = '  <script>candidate()</script>\n  source ' + name + '\n'
                value['cost'].update(status='UNKNOWN' if amount is None else 'KNOWN', amount=amount)
                return value

            execution.execute(data, aid, transport)

            def report(ctx, resources):
                value = findings(ctx, resources)
                if name == 'error':
                    value['findings'][0].update(status='FAIL', attribution='candidate', finding='Action omise')
                pid = ctx['attempt']['output_piece_id']
                proof = [dict(piece_id=pid, sha256=sha256(resources[pid]).hexdigest(), passage=resources[pid].decode())]
                value['measures'] = [dict(criterion_id=criterion_ids[0], value=measure, unit='s', evidence=proof if measure is not None else []),
                                     dict(criterion_id=criterion_ids[1], value=boolean, unit='bool', evidence=proof)]
                return value

            records[name] = e.evaluate(store, 'comparison', aid, responsible=RESPONSIBLE, authority=EVALUATION_AUTHORITY, check=report)
        c.stop(store, 'comparison')
        first = records['error']

        def correction(ctx, resources):
            value = findings(ctx, resources)
            value['findings'][0].update(status='FAIL', attribution='candidate', finding='Action omise, précision conservée')
            value['measures'] = [{k: v[k] for k in ('criterion_id', 'value', 'unit', 'evidence')} for v in first['measures']]
            return value

        records['error'] = e.evaluate(store, 'comparison', 'attempt-error', responsible=RESPONSIBLE,
                                      authority=EVALUATION_AUTHORITY, check=correction, previous_evaluation_id=first['evaluation_id'])
        c.create(store, manifest(candidate, 'empty'))
        store.put_piece('fixture', view['revision'], 'unlinked', name='Pièce privée étrangère', role='judge',
                        media_type='text/plain', content=b'PRIVATE_UNSELECTED')
        token_sid, _, token = p.session(store, None, create=True)
        store._connection.execute('UPDATE s2_dossiers SET session_id=? WHERE dossier_id=?', (token_sid, 'fixture'))
        store._connection.execute('UPDATE s2_validations SET session_id=? WHERE dossier_id=?', (token_sid, 'fixture'))
        return store, token_sid, token, records, first
    except BaseException:
        store.close()
        raise


def executor_with_presentation(data, sock, source):
    """Racine de composition des tests : la projection publique est injectée dans l'exécuteur"""
    service.serve_executor(data, sock, source, presentation=projection)


class S6Regressions(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='s6-reg-')
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.public = self.home / 'public'
        self.public.mkdir()
        self.store, self.sid, self.token, self.records, self.first = build(self.home / 'private')
        self.addCleanup(self.store.close)
        self.before = list(self.store._connection.iterdump())
        self.addCleanup(self.unchanged)
        self.base = '/preparation/dossiers/fixture/campaigns/comparison'

    def unchanged(self):
        self.assertEqual(self.before, list(self.store._connection.iterdump()))

    def compare(self, query=None):
        return r.comparison(self.store, self.sid, 'fixture', 'comparison', query=query)

    def preview(self, pieces=None):
        return r.preview(self.store, self.sid, 'fixture', 'comparison', piece_ids=[] if pieces is None else pieces, presentation=projection)

    def approval(self, bundle):
        return dict(actor='approbateur-fictif-S6', authority_id='TEST_ONLY_PUBLICATION_S6',
                    projection_sha256=bundle['projection_sha256'], catalogue=False)

    def prior_bundle(self, current, manifest, version='3'):
        """Paquet antérieur synthétique, complet et approuvable, aux octets distincts"""
        files = dict(current['files'], **{'style.css': _PRIOR_STYLESHEET})
        labeled = dict(manifest, presentation_version=version,
                       files={name: sha256(raw).hexdigest() for name, raw in files.items()})
        raw = storage._strict_json(labeled).encode()
        return dict(manifest=raw, files=files, projection_sha256=sha256(raw).hexdigest())

    def test_version_de_presentation_liee_aux_octets_de_la_fixture(self):
        current = self.preview()
        manifest = json.loads(current['manifest'])
        version = pub.PRESENTATION_VERSION
        self.assertEqual(version, manifest['presentation_version'])
        self.assertEqual({version}, set(_FIXTURE_PRESENTATION))
        html = current['files']['index.html']
        css = current['files']['style.css']
        self.assertEqual(
            _FIXTURE_PRESENTATION[version],
            {'index.html': sha256(_VOLATILE_PRESENTATION.sub(b'#', html)).hexdigest(),
             'style.css': sha256(css).hexdigest()})
        sections = html.count(b'<section>')
        self.assertEqual(5, sections)
        self.assertEqual(sections + 1, html.count(projection.RESTRICTION_PUBLIQUE.encode()))
        pub.materialize(current, self.approval(current), self.public)
        for name, raw in current['files'].items():
            self.assertEqual(raw, pub.public_bytes(
                self.public, current['projection_sha256'], name))
        readable = tuple(item for item in pub.PRESENTATION_VERSIONS if item != version)
        for old in readable:
            with self.subTest(lire=old):
                labeled = dict(manifest, presentation_version=old)
                raw = storage._strict_json(labeled).encode()
                pub._manifest(raw, sha256(raw).hexdigest())
        unknown = dict(manifest, presentation_version='7')
        raw = storage._strict_json(unknown).encode()
        with self.assertRaisesRegex(ValueError, 'Version de restitution inconnue'):
            pub._manifest(raw, sha256(raw).hexdigest())
        self.assertTrue(readable)

    def test_un_paquet_anterieur_reste_lisible_inchange_apres_activation_courante(self):
        current = self.preview()
        prior = self.prior_bundle(current, json.loads(current['manifest']))
        self.assertNotEqual(current['files']['style.css'], prior['files']['style.css'])
        self.assertNotEqual(current['projection_sha256'], prior['projection_sha256'])
        pub.materialize(prior, self.approval(prior), self.public)
        stored = {name: pub.public_bytes(self.public, prior['projection_sha256'], name)
                  for name in prior['files']}
        self.assertEqual(prior['files'], stored)
        pub.materialize(current, self.approval(current), self.public)
        self.assertEqual(current['projection_sha256'],
                         json.loads((self.public / 'active.json').read_text())['directory'])
        self.assertEqual(prior['files'], {name: pub.public_bytes(self.public, prior['projection_sha256'], name)
                                          for name in prior['files']})
        # Ni requalification, ni réétiquetage, ni nouveau rendu du paquet conservé
        self.assertEqual(prior['manifest'],
                         (self.public / prior['projection_sha256'] / 'publication.json').read_bytes())
        self.assertEqual('3', json.loads(prior['manifest'])['presentation_version'])

    def test_exact_ranks_boolean_scale_corrections_filters_and_empty_campaign(self):
        view = self.compare({'case': 'notes', 'sort': 'cost', 'direction': 'desc'})
        rows = {v['configuration_id']: v for v in view['rows']}
        self.assertEqual([3, 1, 1, None], [v['cost']['rank'] for v in view['rows']])
        self.assertEqual('0.10002', rows['near']['cost']['value'])
        self.assertEqual('NE SATISFAIT PAS', rows['error']['verdict'])
        self.assertEqual(self.records['error']['evaluation_id'], rows['error']['evaluation_id'])
        self.assertIsNone(rows['near']['measures'][0]['rank'])
        self.assertIs(rows['near']['measures'][0]['value'], True)
        self.assertEqual(4, rows['near']['measures'][1]['rank'])
        self.assertEqual(1, rows['tie']['measures'][1]['rank'])
        self.assertEqual({'planned_cells': 6, 'attempted_cells': 5, 'evaluated_attempts': 5, 'decided_attempts': 5, 'not_started': 1}, view['coverage'])
        filtered = self.compare({'case': 'notes', 'verdict': 'SATISFAIT', 'configuration': 'error'})
        self.assertEqual([], filtered['rows'])
        self.assertEqual(view['population'], filtered['population'])
        self.assertEqual(view['coverage'], filtered['coverage'])
        self.assertEqual('INCOMPLETE', filtered['economic_status'])
        self.assertIn('Aucune ligne ne correspond', views.render(filtered, '').decode())
        empty = r.comparison(self.store, self.sid, 'fixture', 'empty')
        self.assertEqual([], empty['rows'])
        self.assertEqual([], empty['population'])
        self.assertEqual('empty', empty['campaign_id'])
        other = self.compare({'case': 'distinct'})['rows'][0]
        self.assertEqual(1, other['cost']['rank'])
        self.assertEqual(1, other['measures'][0]['rank'])

    def test_native_dispatch_context_proofs_and_inert_html(self):
        query = '?case=notes&sort=cost&direction=asc&obligation=O1%3AFAIL'
        code, value, cookie, start = web_api.dispatch(self.store, 'GET', self.base + query, self.token, None, 'a' * 40, False)
        self.assertEqual((200, None, None), (code, cookie, start))
        comparison_html = views.render(value, '')
        markup = Markup(comparison_html)
        self.assertEqual([('script', {})], [(tag, attrs) for tag, attrs in markup.tags if tag == 'script'])
        self.assertFalse(any(k.startswith('on') for _, attrs in markup.tags for k in attrs))
        self.assertEqual(views.COMPARISON_FOCUS_SCRIPT.encode(), comparison_html.split(b'<script>')[1].split(b'</script>')[0])
        self.assertEqual('4JYjiqZNZfBuB599Ij0Xkc4E3182/UxSNQd6+mpeDNM=',
                         b64encode(sha256(views.COMPARISON_FOCUS_SCRIPT.encode()).digest()).decode())
        detail = next(attrs['data-url'] for tag, attrs in markup.tags
                      if tag == 'button' and '/attempts/attempt-error' in attrs.get('data-url', ''))
        code, value, _, _ = web_api.dispatch(self.store, 'GET', detail, self.token, None, 'a' * 40, False)
        raw = views.render(value, '')
        self.assertEqual(200, code)
        self.assertIn(self.first['evaluation_id'].encode(), raw)
        self.assertIn(b'  &lt;script&gt;candidate()&lt;/script&gt;\n  source error\n', raw)
        parsed = Markup(raw)
        self.assertFalse(any(tag == 'script' or any(k.startswith('on') for k in attrs) for tag, attrs in parsed.tags))
        self.assertTrue(raw.startswith(b'<div id="attempt-detail">'))
        for link in parsed.links:
            if '/pieces/' in link:
                code, proof, _, _ = web_api.dispatch(self.store, 'GET', link, self.token, None, 'a' * 40, False)
                self.assertEqual(200, code)
                self.assertEqual(self.store.read_piece(link.rsplit('/', 1)[1]), proof)

    def test_http_authorizes_only_exact_focus_script_on_comparison_html(self):
        bundle = self.preview()
        pub.materialize(bundle, self.approval(bundle), self.public)
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
        for target, args in ((executor_with_presentation, (self.home / 'private', sock, 'a' * 40)),
                             (serve_web, ('127.0.0.1', port, self.public, sock, 'a' * 40))):
            process = context.Process(target=target, args=args,
                                      kwargs={'readiness_clients': ('127.0.0.1',)} if target is serve_web else {})
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
        policy = "default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        paths = [(self.base, 'text/html'), (self.base, 'application/json'),
                 (self.base + '/attempts/attempt-error', 'text/html'),
                 (self.base + '/preview', 'text/html'),
                 ('/publications/' + bundle['projection_sha256'] + '/index.html', 'text/html')]
        row = next(row for row in self.compare()['rows'] if row['attempt_id'] == 'attempt-error')
        paths += [(link['href'], 'text/plain') for link in row['proof_links']]
        for path, accept in paths:
            with self.subTest(path=path, accept=accept):
                headers = {'Accept': accept, 'Cookie': 'benchmark_session=' + self.token}
                with urlopen(Request(base + path, headers=headers), timeout=5) as result:
                    self.assertEqual(200, result.status)
                    raw = result.read()
                    expected = policy
                    if path == self.base and accept == 'text/html':
                        expected += "; script-src 'sha256-4JYjiqZNZfBuB599Ij0Xkc4E3182/UxSNQd6+mpeDNM='; connect-src 'self'"
                        self.assertEqual(1, raw.count(b'<script>'))
                        self.assertNotIn(b'innerHTML', raw)
                        self.assertEqual(1, raw.count(b'<dialog '))
                        self.assertEqual(views.COMPARISON_FOCUS_SCRIPT.encode(), raw.split(b'<script>')[1].split(b'</script>')[0])
                    elif accept == 'text/html':
                        self.assertFalse(any(tag == 'script' for tag, _ in Markup(raw).tags))
                    elif accept == 'text/plain':
                        self.assertTrue(result.headers['Content-Type'].startswith('text/plain'))
                        self.assertEqual(self.store.read_piece(path.rsplit('/', 1)[1]), raw)
                    self.assertEqual(expected, result.headers['Content-Security-Policy'])
                    self.assertEqual('nosniff', result.headers['X-Content-Type-Options'])
                    self.assertEqual('no-store', result.headers['Cache-Control'])

    def test_invalid_filters_private_access_and_catalogue(self):
        for query in ('sort=cost&sort=duration', 'case=unknown', 'sort=O1', 'sort=unknown', 'direction=wrong',
                      'configuration=foreign', 'obligation=O1:wrong', 'obligation=E1:PASS', 'winner=error', 'winner=', 'sort=&sort=cost'):
            with self.subTest(query=query), self.assertRaises(ValueError):
                web_api.dispatch(self.store, 'GET', self.base + '?' + query, self.token, None, 'a' * 40, False)
        for did, cid in (('foreign', 'comparison'), ('fixture', 'foreign')):
            with self.assertRaises(p.Denied):
                r.comparison(self.store, self.sid, did, cid)
        with self.assertRaises(p.Denied):
            r.comparison(self.store, 'foreign', 'fixture', 'comparison')
        with self.assertRaises(p.Denied):
            r.detail(self.store, self.sid, 'fixture', 'empty', 'attempt-error')
        view = r.catalogue(self.store, self.sid)
        self.assertEqual('private', view['visibility'])
        self.assertIs(view['catalogue_admission'], False)
        self.assertEqual({'comparison', 'empty'}, {v['campaign_id'] for v in view['tasks'][0]['versions'][0]['campaigns']})
        self.assertFalse((self.public / 'active.json').exists())

    def test_combined_filter_form_and_empty_selections(self):
        query = 'case=notes&sort=cost&direction=desc&verdict=NE+SATISFAIT+PAS&obligation=O1%3AFAIL&configuration=error'
        code, value, cookie, start = web_api.dispatch(self.store, 'GET', self.base + '?' + query,
                                                     self.token, None, 'a' * 40, None)
        self.assertEqual((200, None, None), (code, cookie, start))
        self.assertEqual(['error'], [row['configuration_id'] for row in value['rows']])
        empty = 'case=&sort=&direction=&verdict=&obligation=&configuration='
        _, cleared, _, _ = web_api.dispatch(self.store, 'GET', self.base + '?' + empty,
                                             self.token, None, 'a' * 40, None)
        self.assertEqual({}, cleared['filter_scope'])
        self.assertEqual(self.compare()['rows'], cleared['rows'])
        self.assertEqual(value['coverage'], cleared['coverage'])

    def test_projection_ciblee_identique_et_limitee_a_la_campagne_demandee(self):
        connection = c.connection_for(self.store)
        complete = c.projection(self.store, connection, 'fixture')
        self.assertEqual(['comparison', 'empty'], [v['campaign_id'] for v in complete])
        inspected, assembled = [], []
        with patch.object(c, '_inspect', side_effect=c._inspect) as inspect, \
                patch.object(c, '_projected', side_effect=c._projected) as assemble:
            targeted = c.projection(self.store, connection, 'fixture', 'comparison')
            inspected = [call.args[2] for call in inspect.call_args_list]
            assembled = [call.args[2] for call in assemble.call_args_list]
        self.assertEqual([v for v in complete if v['campaign_id'] == 'comparison'], targeted)
        self.assertEqual(['comparison'], inspected)
        self.assertEqual(['comparison'], assembled)
        self.assertEqual([], c.projection(self.store, connection, 'fixture', 'inconnue'))
        self.assertEqual([], c.projection(self.store, connection, 'foreign', 'comparison'))

    def test_dossier_courant_et_revision_precedente_partagent_une_projection_ciblee(self):
        from tests.test_s2_review_regressions import response_for

        connection = self.store._connection
        previous = p.owner(connection, self.sid, 'fixture')
        foreign, _, foreign_token = p.session(self.store, None, create=True)
        p.admit(self.store, dict(authority_id='TEST_ONLY_PREPARATION_S3', budget_id='fictional',
                                reserve_amount='7', requested_configuration={'model': 'fictional'}))
        for sid, did, body in (
                (self.sid, 'second', dict(action_id='create', request='Autre dossier fictif')),
                (foreign, 'foreign', dict(action_id='create', request='Dossier privé étranger')),
                (self.sid, 'fixture', dict(action_id='correct', revision=previous,
                                           kind='correct', message='Modifier les notes fictives'))):
            operation, _ = p.submit(self.store, sid, did, body, 'a' * 40, True)
            p.execute(self.home / 'private', operation, lambda op, _: response_for(op))
        self.before = list(connection.iterdump())
        current = p.owner(connection, self.sid, 'fixture')
        catalogue = r.catalogue(self.store, self.sid)
        self.assertEqual(['fixture', 'second'], [task['dossier_id'] for task in catalogue['tasks']])
        index = catalogue['tasks'][0]
        self.assertEqual(current, index['revision'])
        self.assertEqual({'comparison', 'empty'},
                         {campaign['campaign_id'] for campaign in index['versions'][0]['campaigns']})
        for suffix, revision in (('', current), ('/revisions/' + str(previous), previous)):
            with self.subTest(revision=revision):
                expected = dict(p.view(self.store, self.sid, 'fixture', revision),
                                current_revision=current, task_index=index,
                                availability=p.availability(self.store, False))
                transaction, reads = 0, []

                def trace(sql):
                    nonlocal transaction
                    if sql == 'BEGIN':
                        transaction += 1
                    if sql.startswith(('SELECT stage,explanation,package_json',
                                       'SELECT revision FROM s2_revisions')):
                        reads.append(transaction)

                connection.set_trace_callback(trace)
                try:
                    with patch.object(c, 'projection', wraps=c.projection) as project:
                        code, actual, _, _ = web_api.dispatch(
                            self.store, 'GET', '/preparation/dossiers/fixture' + suffix,
                            self.token, None, 'a' * 40, False)
                finally:
                    connection.set_trace_callback(None)
                self.assertEqual(200, code)
                self.assertEqual(expected, actual)
                self.assertEqual(['fixture'], [call.args[2] for call in project.call_args_list])
                self.assertEqual(2, len(reads))
                self.assertEqual(reads[0], reads[1])
                with self.assertRaises(p.Denied):
                    web_api.dispatch(self.store, 'GET', '/preparation/dossiers/fixture' + suffix,
                                     foreign_token, None, 'a' * 40, False)

    def test_lancement_reutilise_son_instantane_sans_projection_globale(self):
        connection = c.connection_for(self.store)
        expected = next(v for v in c.projection(self.store, connection, 'fixture')
                        if v['campaign_id'] == 'comparison')
        inspected, assembled = [], []
        real_inspect, real_projected = c._inspect, c._projected

        def inspect(store, connection, campaign_id):
            snapshot = real_inspect(store, connection, campaign_id)
            inspected.append((campaign_id, snapshot))
            return snapshot

        def assemble(store, connection, campaign_id, snapshot):
            assembled.append((campaign_id, snapshot))
            return real_projected(store, connection, campaign_id, snapshot)

        with patch.object(c, '_inspect', inspect), patch.object(c, '_projected', assemble), \
                patch.object(c, 'projection', side_effect=AssertionError('Projection globale interdite')):
            view = c.launch_view(self.store, self.sid, 'fixture', 'comparison')
        # deux transactions distinctes, donc deux inspections, mais un seul assemblage
        self.assertEqual(['comparison', 'comparison'], [cid for cid, _ in inspected])
        self.assertEqual(1, len(assembled))
        self.assertIs(inspected[-1][1], assembled[0][1])
        self.assertEqual(p.page_view(expected), view['campaign'])

    def test_filtre_obligations_libelles_lisibles_et_valeurs_stables(self):
        value = self.compare()
        value['obligations'][0]['description'] = 'Traiter toutes les demandes présentes dans les pièces et rendre lisible la situation complète'
        for rows in (value['rows'], []):
            with self.subTest(rows=bool(rows)):
                value['rows'] = rows
                page = views.render(value, '').decode()
                for state, label in (('PASS', 'Respectée'), ('FAIL', 'Non respectée'),
                                     ('INDETERMINE', 'Indéterminée')):
                    self.assertIn('value="O1:' + state + '">Traiter toutes les demandes présentes dans les pièces et… : ' +
                                  label + '</option>', page)
                self.assertNotIn('>O1 : PASS</option>', page)

    def test_preview_does_not_copy_unselected_passages_or_mutate_storage(self):
        bundle = self.preview()
        raw = b''.join(bundle['files'].values())
        self.assertNotIn(b'PRIVATE_UNSELECTED', raw)
        self.assertNotIn(b'candidate()', raw)
        self.assertNotIn(b'/preparation/', raw)
        self.assertIn('restreinte'.encode(), raw)
        self.assertIn(('Les descriptions des obligations et des erreurs éliminatoires sont publiées comme libellés. '
                       'La référence de jugement et les preuves de qualification restent privées ; '
                       'ces descriptions seules ne permettent pas de vérifier publiquement la qualification des critères.').encode(), raw)
        self.assertIn('Durée fictive'.encode(), raw)
        self.assertIn('Présence fictive'.encode(), raw)
        self.assertNotIn(b'duration :', raw)
        self.assertNotIn(b'&quot;criterion_id&quot;:&quot;duration&quot;', raw)
        self.assertEqual({'index.html', 'style.css'}, set(bundle['files']))
        with self.assertRaises(p.Denied):
            self.preview(['unlinked'])
        with self.assertRaises(p.Denied):
            r.preview(self.store, 'foreign', 'fixture', 'comparison', piece_ids=[], presentation=projection)
        self.assertFalse((self.public / 'active.json').exists())

    def test_browser_preview_is_private_selected_and_never_activates(self):
        pid = self.records['error']['output_piece_id']
        selected = self.preview([pid])
        pub.materialize(selected, self.approval(selected), self.public)
        before = {str(f.relative_to(self.public)): f.read_bytes() for f in self.public.rglob('*') if f.is_file()}
        for pieces in ([], [pid]):
            path = self.base + '/preview' + ('?' + urlencode([('piece', p) for p in pieces]) if pieces else '')
            code, value, cookie, start = web_api.dispatch(self.store, 'GET', path, self.token, None, 'a' * 40, False, presentation=projection)
            self.assertEqual((200, None, None), (code, cookie, start))
            self.assertEqual('projection_preview', value['kind'])
            bundle = self.preview(pieces)
            self.assertEqual(bundle['projection_sha256'], value['projection_sha256'])
            self.assertEqual(json.loads(bundle['manifest']), value['manifest'])
            raw = views.render(value, '')
            self.assertIn('Aperçu privé · NON APPROUVÉ'.encode(), raw)
            self.assertNotIn(b'candidate()', raw)
            parsed = Markup(raw)
            self.assertFalse(any(tag == 'script' or any(k.startswith('on') for k in attrs) for tag, attrs in parsed.tags))
            links = [link for link in parsed.links if '/pieces/' in link]
            self.assertEqual(set(pieces), {link.rsplit('/', 1)[1] for link in links})
            for link in links:
                self.assertEqual(self.store.read_piece(pid), web_api.dispatch(self.store, 'GET', link, self.token, None, 'a' * 40, False)[1])
            with self.assertRaises(p.Denied):
                web_api.dispatch(self.store, 'GET', path, None, None, 'a' * 40, False, presentation=projection)
            with self.assertRaises(p.Denied):
                r.preview_view(self.store, 'foreign', 'fixture', 'comparison', piece_ids=pieces, presentation=projection)
        for query in ('?piece=unlinked', '?piece=', '?extra=1', '?piece=' + pid + '&piece=' + pid):
            with self.subTest(query=query), self.assertRaises(ValueError):
                web_api.dispatch(self.store, 'GET', self.base + '/preview' + query, self.token, None, 'a' * 40, False, presentation=projection)
        self.assertEqual(before, {str(f.relative_to(self.public)): f.read_bytes() for f in self.public.rglob('*') if f.is_file()})

    def test_exact_approval_and_files_no_partial_activation(self):
        bundle = self.preview()
        authority = self.approval(bundle)
        for fields in ({'actor': 'Ayo'}, {'authority_id': 'real'}, {'catalogue': True}, {'catalogue': 0},
                       {'projection_sha256': '0' * 64}, {'extra': 'not allowed'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                pub.materialize(bundle, dict(authority, **fields), self.public)
            self.assertEqual([], list(self.public.iterdir()))
        changed = deepcopy(bundle)
        changed['files']['index.html'] += b' changed'
        with self.assertRaises(ValueError):
            pub.materialize(changed, authority, self.public)
        pub.materialize(bundle, authority, self.public)
        previous = (self.public / 'active.json').read_bytes()
        with patch.object(pub, '_write', side_effect=OSError('Fictional write failure')):
            with self.assertRaises(OSError):
                pub.materialize(bundle, authority, self.public)
        self.assertEqual(previous, (self.public / 'active.json').read_bytes())
        self.assertFalse(any(p.name.startswith('.s6-') for p in self.public.iterdir()))
        pub.materialize(bundle, authority, self.public)
        self.assertEqual(previous, (self.public / 'active.json').read_bytes())

    def test_public_reader_keeps_identity_and_closes_on_changed_approval_or_bytes(self):
        pid = self.records['error']['output_piece_id']
        bundle = self.preview([pid])
        identity = bundle['projection_sha256']
        pub.materialize(bundle, self.approval(bundle), self.public)
        newer = self.preview()
        pub.materialize(newer, self.approval(newer), self.public)
        for name, raw in bundle['files'].items():
            self.assertEqual(raw, pub.public_bytes(self.public, identity, name))
        with self.assertRaises(ValueError):
            pub.public_bytes(self.public, identity, 'unknown.txt')
        folder = self.public / identity
        approval_raw = (folder / 'approval.json').read_bytes()
        (folder / 'approval.json').write_text('{}')
        with self.assertRaises(ValueError):
            pub.public_bytes(self.public, identity, 'index.html')
        (folder / 'approval.json').write_bytes(approval_raw)
        piece = next(name for name in bundle['files'] if name.startswith('piece-'))
        (folder / piece).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            pub.public_bytes(self.public, identity, piece)
        self.assertEqual(newer['files']['index.html'], pub.public_bytes(self.public, newer['projection_sha256'], 'index.html'))

    def test_symlinks_traversal_and_forged_manifest_are_refused(self):
        bundle = self.preview()
        authority = self.approval(bundle)
        pub.materialize(bundle, authority, self.public)
        identity = bundle['projection_sha256']
        folder = self.public / identity
        for filename in ('index.html', 'publication.json', 'approval.json'):
            original = folder / filename
            moved = folder / (filename + '.original')
            original.rename(moved)
            original.symlink_to(moved)
            with self.assertRaises((OSError, ValueError)):
                pub.public_bytes(self.public, identity, 'index.html')
            original.unlink()
            moved.rename(original)
        linked = self.home / 'linked'
        linked.symlink_to(self.public, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            pub.materialize(bundle, authority, linked)
        with self.assertRaises((OSError, ValueError)):
            pub.public_bytes(linked, identity, 'index.html')
        for name in ('../index.html', '/index.html', 'approval.json', 'piece-%2f.txt'):
            with self.assertRaises(ValueError):
                pub.public_bytes(self.public, identity, name)
        forged = deepcopy(bundle)
        m = json.loads(forged['manifest'])
        m['files']['../escape.txt'] = sha256(b'escape').hexdigest()
        forged['files']['../escape.txt'] = b'escape'
        forged['manifest'] = json.dumps(m).encode()
        forged['projection_sha256'] = sha256(forged['manifest']).hexdigest()
        with self.assertRaises(ValueError):
            pub.materialize(forged, self.approval(forged), self.public)


class CriterionNames(unittest.TestCase):
    def test_cost_and_prefixed_criterion_remain_distinct_from_observed_cost(self):
        with tempfile.TemporaryDirectory(prefix='s6-cost-') as tmp:
            store, sid, _, records, _ = build(Path(tmp).resolve() / 'private', ('cost', 'criterion:cost'))
            try:
                before = list(store._connection.iterdump())
                value = r.comparison(store, sid, 'fixture', 'comparison')
                columns = value['columns']
                self.assertEqual(len(columns), len({col['id'] for col in columns}))
                observed, duration, boolean = columns
                self.assertEqual('cost', observed['id'])
                self.assertEqual('cost', duration['criterion_id'])
                self.assertEqual('criterion:cost', boolean['criterion_id'])
                for column, order in ((observed, ['error', 'tie', 'near', 'missing']),
                                      (duration, ['tie', 'error', 'near', 'missing'])):
                    result = r.comparison(store, sid, 'fixture', 'comparison',
                        query=dict(case='notes', sort=column['id'], direction='asc'))
                    self.assertEqual(order, [row['configuration_id'] for row in result['rows']])
                self.assertEqual('attempt_detail', r.detail(store, sid, 'fixture', 'comparison', 'attempt-error')['kind'])
                self.assertIn(b'index.html', r.preview(store, sid, 'fixture', 'comparison', piece_ids=[], presentation=projection)['manifest'])
                self.assertEqual(before, list(store._connection.iterdump()))
            finally:
                store.close()


class EmptySession(unittest.TestCase):
    def test_catalogue_works_on_s2_without_dossier_or_evaluation_schema(self):
        with tempfile.TemporaryDirectory(prefix='s6-empty-') as tmp:
            data = Path(tmp).resolve() / 'private'
            storage.initialize(data)
            storage.initialize_preparation(data)
            store = storage.Store(data)
            try:
                sid, _, _ = p.session(store, None, create=True)
                self.assertEqual([], r.catalogue(store, sid)['tasks'])
            finally:
                store.close()
