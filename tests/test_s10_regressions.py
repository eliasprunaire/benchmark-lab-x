"""Private, inert proof reading using only fictional local acquisitions."""
from html import escape
from copy import deepcopy
import json
import multiprocessing
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from benchmark.acquisition import execution
from benchmark.acquisition import campaigns as c
from benchmark import evaluation as e, preparation as p, publications as pub, qualification as q, restitution as r, web_api
from benchmark_web import campaign_views, fragments, projection, views
from benchmark_web.server import serve_web
from tests.test_s4_regressions import inputs, manifest, response
from tests.test_s5_regressions import EVALUATION_AUTHORITY, RESPONSIBLE, findings
from tests.test_s6_regressions import Markup, build


class S10ProofTests(unittest.TestCase):
    def test_compact_results_and_one_filter_form(self):
        value = r.comparison(self.store, self.sid, 'fixture', 'proof')
        value['stop_reason'] = 'maintenance'
        value['coverage']['not_started'] = 0
        value['pending_attempts'] = []
        page = views.render(value, '').decode()
        parsed = Markup(page.encode())
        self.assertEqual(1, sum(tag == 'form' for tag, _ in parsed.tags))
        self.assertEqual(1, page.count('>Appliquer</button>'))
        self.assertIn('>Effacer</a>', page)
        self.assertIn('value="" selected', page)
        self.assertNotIn('name="case"', page)
        self.assertNotIn('<p class="lead">', page)
        self.assertNotIn('Cas 1', page)
        self.assertNotIn('les essais ont été arrêtés', page)
        value['coverage']['not_started'] = 1
        self.assertIn('les essais ont été arrêtés', views.render(value, '').decode())
        table = page.split('<table>', 1)[1].split('</table>', 1)[0]
        self.assertNotIn('Demandée, observée et sources', table)
        self.assertNotIn('Sans rang', table)
        self.assertIn('Détail et preuves', table)
        self.assertEqual(4, sum(tag == 'th' and attrs.get('scope') == 'col' for tag, attrs in parsed.tags))

    def test_campaign_models_are_private_frozen_and_read_only(self):
        value = r.comparison(self.store, self.sid, 'fixture', 'proof')
        url = value['href'] + '/configurations'
        with patch('socket.socket.connect', side_effect=AssertionError('No network')):
            code, models, cookie, start = web_api.dispatch(self.store, 'GET', url, self.token, None, 'a' * 40, None)
        self.assertEqual((200, None, None), (code, cookie, start))
        self.assertEqual(value['panel'], models['panel'])
        page = views.render(models, '').decode()
        self.assertIn('<h1>Modèles de cette comparaison</h1>', page)
        self.assertIn('aria-current="step"><span class="n">4</span>Modèles', page)
        self.assertNotIn('<form', page)
        self.assertNotIn('<script>', page)
        self.assertIn(value['href'], Markup(page.encode()).links)
        with self.assertRaises(p.Denied):
            web_api.dispatch(self.store, 'GET', url, None, None, 'a' * 40, None)

    def test_return_to_tested_revision_is_read_only_and_tracks_section(self):
        comparison = r.comparison(self.store, self.sid, 'fixture', 'comparison')
        path = comparison['dossier_href']
        _, value, _, start = web_api.dispatch(self.store, 'GET', path, self.token, None, 'a' * 40, None)
        self.assertIsNone(start)
        self.assertEqual(['comparison'], [campaign['campaign_id'] for campaign in value['campaigns']])
        page = views.render(value, '', path).decode()
        self.assertIn('Consultation seule', page)
        self.assertNotIn('<form', page)
        self.assertNotIn('>Choisir les modèles</a>', page)
        self.assertIn('<script>' + views.STEP_SCRIPT + '</script>', page)
        self.assertEqual(views.STEP_SCRIPT, views.page_script(value))
        for anchor in ('besoin', 'exemple', 'validation'):
            self.assertIn('id="' + anchor + '"', page)
        self.assertIn('>Préparer une nouvelle comparaison</a>', page)
        self.assertIn(comparison['href'] + '/configurations', Markup(page.encode()).links)
        for suffix in ('campaign=foreign', 'campaign=comparison&campaign=proof', 'campaign=', 'unknown=comparison'):
            with self.subTest(suffix=suffix), self.assertRaises((p.Denied, ValueError)):
                web_api.dispatch(self.store, 'GET', path.split('?')[0] + '?' + suffix, self.token, None, 'a' * 40, None)

    def test_historical_unstarted_campaign_and_non_comparable_sorted_measure(self):
        value = p.view(self.store, self.sid, 'fixture')
        value['campaigns'] = [campaign for campaign in value['campaigns'] if campaign['campaign_id'] == 'empty']
        value['current_revision'] = value['revision'] + 1
        steps = views.preparation_steps(value)
        self.assertIn('/campaigns/empty/conditions', steps)
        self.assertNotIn('/dossiers/fixture/configurations', steps)
        page = views.render(r.comparison(self.store, self.sid, 'fixture', 'comparison',
                                         query={'sort': 'duration', 'configuration': 'near'}), '').decode()
        self.assertIn('Non comparable', page)
        self.assertIn('Valeur non interprétable sur l’échelle déclarée', page)
        self.assertNotIn('>Oui</span> s', page)

    def test_retour_de_preuve_et_table_accessibles(self):
        with patch('socket.socket.connect', side_effect=AssertionError('No network')):
            detail = r.detail(self.store, self.sid, 'fixture', 'proof', 'long',
                              query={'case': 'notes', 'sort': 'cost', 'direction': 'desc'})
            comparison = r.comparison(self.store, self.sid, 'fixture', 'proof',
                                      query=detail['filter_scope'])
            page = views.render(comparison, '').decode()
            proof = Markup(views.render(detail, ''))
        self.assertIn(detail['back_href'], proof.links)
        self.assertTrue(detail['back_href'].endswith('#attempt-long'))
        parsed = Markup(page.encode())
        row = next(attrs for tag, attrs in parsed.tags if attrs.get('id') == 'attempt-long')
        self.assertEqual('-1', row['tabindex'])
        region = next(attrs for tag, attrs in parsed.tags if attrs.get('class') == 'table-scroll')
        self.assertEqual(('region', '0', 'Comparaison des modèles'),
                         (region['role'], region['tabindex'], region['aria-label']))
        self.assertEqual(4, sum(tag == 'th' and attrs.get('scope') == 'col' for tag, attrs in parsed.tags))
        self.assertEqual(1, page.count('<script>'))
        self.assertIn('<script>' + views.COMPARISON_FOCUS_SCRIPT + '</script>', page)
        self.assertFalse(any(tag == 'script' for tag, attrs in proof.tags))
        for verdict, label in (('SATISFAIT', 'Satisfait'), ('NE SATISFAIT PAS', 'Ne satisfait pas'), (None, 'À reprendre')):
            self.assertIn(label, fragments.badge(verdict))

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='s10-proof-')
        cls.addClassCleanup(temporary.cleanup)
        data = Path(temporary.name).resolve() / 'private'
        with patch('socket.socket.connect', side_effect=AssertionError('No network in fixtures')):
            cls.store, cls.sid, cls.token, records, _ = build(data)
            cls.addClassCleanup(cls.store.close)
            contract = q.inspect_contract(cls.store, records['tie']['contract_sha256'])
            campaign = c.create(cls.store, manifest(contract, 'proof'))
            cls.store.create_budget('proof', '40', 'TEST')
            c.admit(cls.store, 'proof', *inputs(campaign, budget='proof'))
            c.reserve(cls.store, 'proof', 'x', 'long')
            cls.output = ('  Action fictive : relire\n<script>candidate()</script>\n'
                          '<img src=x onerror="candidate()"> &\n' + 'Ligne inventée\n' * 240 + 'FIN\n')

            def transport(operation, request):
                value = response(operation, request)
                value['receipt']['result']['output'] = cls.output
                return value

            execution.execute(data, 'long', transport)
            e.evaluate(cls.store, 'proof', 'long', responsible=RESPONSIBLE,
                       authority=EVALUATION_AUTHORITY, check=findings)

    def setUp(self):
        before = list(self.store._connection.iterdump())
        self.addCleanup(lambda: self.assertEqual(before, list(self.store._connection.iterdump())))

    def test_complete_html_and_raw_proofs_preserve_context(self):
        query = dict(case='notes', sort='cost', direction='desc')
        with patch.object(e, '_pieces_bytes', wraps=e._pieces_bytes) as guarded:
            detail = r.detail(self.store, self.sid, 'fixture', 'proof', 'long', query=query)
        record = detail['history'][-1]
        self.assertEqual(1, guarded.call_count)
        self.assertEqual(self.output, record['proof_contents'][record['output_piece_id']])
        page = views.render(detail, '')
        self.assertIn(('<div class="proof-text">' + escape(self.output, quote=True) + '</div>').encode(), page)
        parsed = Markup(page)
        self.assertFalse(any(tag == 'script' or any(k.startswith('on') for k in attrs)
                             for tag, attrs in parsed.tags))
        self.assertEqual([{'src': '/bench-x.svg', 'width': '32', 'height': '32', 'alt': ''}] * 2,
                         [attrs for tag, attrs in parsed.tags if tag == 'img'])
        self.assertIn(detail['back_href'], parsed.links)
        self.assertTrue(detail['back_href'].endswith('#attempt-long'))
        self.assertEqual(query, detail['filter_scope'])
        self.assertTrue(any(tag == 'details' and attrs.get('class') == 'proof-content' for tag, attrs in parsed.tags))
        for link in record['proof_links']:
            self.assertNotIn(link['href'], parsed.links)
            anchor = 'proof-' + record['evaluation_id'] + '-' + link['piece_id']
            self.assertTrue(any(attrs.get('id') == anchor for _, attrs in parsed.tags))
            code, raw, cookie, start = web_api.dispatch(self.store, 'GET', link['href'], self.token, None, 'a' * 40, None)
            self.assertEqual((200, None, None), (code, cookie, start))
            self.assertEqual(raw.decode(), record['proof_contents'][link['piece_id']])
        self.assertNotIn(b'PRIVATE_UNSELECTED', page)

    def test_owner_guard_precedes_loading_and_comparison_stays_light(self):
        with patch.object(e, '_pieces_bytes', wraps=e._pieces_bytes) as guarded:
            with self.assertRaises(p.Denied):
                r.detail(self.store, 'foreign', 'fixture', 'proof', 'long')
            guarded.assert_not_called()
            comparison = r.comparison(self.store, self.sid, 'fixture', 'proof')
            guarded.assert_not_called()
        self.assertTrue(all('proof_contents' not in record for record in comparison['history']))
        row = comparison['rows'][0]
        with self.assertRaises(p.Denied):
            e.piece_bytes(self.store, self.sid, 'fixture', row['evaluation_id'], 'unlinked')

    def test_detail_reads_proofs_in_the_same_snapshot(self):
        read = e._pieces_bytes

        def coherent_read(*args):
            self.assertTrue(self.store._connection.in_transaction)
            return read(*args)

        with patch.object(e, '_pieces_bytes', side_effect=coherent_read):
            detail = r.detail(self.store, self.sid, 'fixture', 'proof', 'long')
        self.assertEqual(self.output, detail['history'][-1]['proof_contents'][
            detail['history'][-1]['output_piece_id']])

    def test_result_dialog_economic_help_and_compact_fragment(self):
        value = r.comparison(self.store, self.sid, 'fixture', 'proof')
        row = value['rows'][0]
        page = views.render(value, '').decode()
        self.assertNotIn('coût est votre priorité', page)
        value['economic_choice'] = dict(configuration=row['requested_configuration'], count=2,
                                        amount='0.00113885', unit='USD', detail_href=row['detail_href'])
        page = views.render(value, '').decode()
        parsed = Markup(page.encode())
        self.assertEqual(1, sum(tag == 'dialog' for tag, _ in parsed.tags))
        self.assertEqual(1, sum(tag == 'form' for tag, _ in parsed.tags))
        self.assertEqual([row['detail_href']] * 2,
                         [attrs['href'] for tag, attrs in parsed.tags if tag == 'a' and 'data-result' in attrs])
        self.assertLess(page.index('coût est votre priorité'), page.index('id="filters"'))
        self.assertIn('<h2 id="economic-choice-title">Si le coût est votre priorité</h2>', page)
        self.assertIn('<p class="choice-model">' + row['requested_configuration']['model'] + '</p>', page)
        self.assertIn(campaign_views.effort_label(row['requested_configuration']), page)
        self.assertIn('La moins coûteuse parmi 2 réponses conformes sur cet exemple.', page)
        self.assertIn('<strong>0,00113885 USD</strong>', page)
        self.assertIn('>Détails et réserves</a>', page)
        for word in ('recommand', 'meilleur', 'innerHTML', 'gagnant'):
            self.assertNotIn(word, page.lower())
        self.assertFalse(any(k.startswith('on') for _, attrs in parsed.tags for k in attrs))
        self.assertIn('data-result', views.COMPARISON_FOCUS_SCRIPT)
        detail = r.detail(self.store, self.sid, 'fixture', 'proof', 'long', query={'sort': 'cost'})
        raw = views.render(detail, '').decode()
        self.assertEqual(1, raw.count('id="attempt-detail"'))
        fragment = raw.split('id="attempt-detail"', 1)[1]
        self.assertNotIn(detail['need'], fragment)
        for absent in ('evidence-fields', 'Identifiants', 'Responsable :', 'Dépense de jugement'):
            self.assertNotIn(absent, fragment)
        for present in ('Résultat sur cet exemple', 'Coût observé', 'Début de la réponse', 'Lire la réponse complète',
                        'Pourquoi ce verdict', 'Réserves à garder en tête', 'Ce que le juge a observé', 'Pièces de l’exemple'):
            self.assertIn(present, fragment)
        self.assertIn(escape(self.output[:200].strip(), quote=True) + '…', fragment)
        self.assertLess(fragment.index('Début de la réponse'), fragment.index('Lire la réponse complète'))
        self.assertIn('id="evaluation-' + detail['history'][-1]['evaluation_id'] + '"', fragment)

    def test_results_filters_stay_with_the_table(self):
        for query in ({}, {'verdict': 'SATISFAIT'}, {'verdict': 'NE SATISFAIT PAS'}):
            value = r.comparison(self.store, self.sid, 'fixture', 'proof', query=query)
            page = views.render(value, '').decode()
            self.assertEqual(1, page.count('<h2>Comparaison des modèles</h2>'))
            self.assertLess(page.index('<h2>Comparaison des modèles</h2>'), page.index('id="filters"'))
            self.assertLess(page.index('id="filters"'), page.index('Résultats affichés :'))
            if value['rows']:
                self.assertLess(page.index('Résultats affichés :'), page.index('<table>'))
                self.assertNotIn('<h2>', page[page.index('id="filters"'):page.index('<table>')])
            else:
                self.assertIn('Aucune ligne ne correspond aux filtres', page)

    def test_summary_keeps_full_population_and_readable_fields_without_changing_evidence(self):
        value = r.comparison(self.store, self.sid, 'fixture', 'comparison')
        before = deepcopy(value)
        page = views.render(value, '').decode()
        filtered = views.render(r.comparison(self.store, self.sid, 'fixture', 'comparison',
                                        query={'verdict': 'NE SATISFAIT PAS', 'sort': 'cost'}), '').decode()
        summary = lambda html: html.split('aria-label="Conclusion de la campagne">', 1)[1].split('</div>', 1)[0]
        self.assertEqual(summary(page), summary(filtered))
        self.assertIn('Résultats affichés : 5 sur 5 · sans tri.', page)
        self.assertIn('Résultats affichés : 1 sur 5 · Coût observé, croissant.', filtered)
        self.assertIn('1 non conforme', summary(page))
        self.assertIn('3 conformes', summary(page))
        self.assertIn('Comparaison des coûts incomplète', summary(page))
        self.assertIn('body class="s9 comparison"', page)
        self.assertIn('<h1>Résultats</h1>', page)
        self.assertLess(page.index('<table>'), page.index('id="method"'))
        self.assertNotIn('open', next(attrs for tag, attrs in Markup(page.encode()).tags if attrs.get('id') == 'filters'))
        # Le rendu technique complet reste sur l'historique du cas d'usage ; la page directe garde la lecture humaine
        _, dossier, _, _ = web_api.dispatch(self.store, 'GET', value['dossier_href'], self.token, None, 'a' * 40, None)
        history = views.render(dossier, '', value['dossier_href']).decode()
        self.assertIn('Configuration demandée', history)
        self.assertIn('Configuration observée', history)
        detail = views.render(r.detail(self.store, self.sid, 'fixture', 'comparison', 'attempt-error'), '').decode()
        self.assertNotIn('Configuration observée', detail)
        self.assertNotIn('&quot;observed_configuration&quot;', page)
        self.assertNotIn('True bool', page)
        self.assertIn('>Oui</span>', detail)
        self.assertEqual(before, value)
        hostile = fragments.readable_fields({'parameters': {'<img src=x onerror=alert(1)>': '<script>bad()</script>'}})
        self.assertNotIn('<script>', hostile)
        self.assertFalse(any(tag == 'img' for tag, _ in Markup(hostile.encode()).tags))

    def test_results_method_is_concise_and_received_without_verdict_is_pending(self):
        value = r.comparison(self.store, self.sid, 'fixture', 'comparison')
        evaluated_page = views.render(value, '').decode()
        value.update(history=[], rows=[], population=[], acquisition_dates=['2026-09-18'])
        value['coverage']['evaluated_attempts'] = 0
        page = views.render(value, '').decode()
        self.assertIn('<h1>Résultats</h1>', page)
        self.assertIn('En attente d’évaluation', page)
        self.assertNotIn('Identité de la campagne', page)
        self.assertNotIn('href="#method">Méthode et limites', page)
        self.assertNotIn('id="filters"', page)
        self.assertNotIn('id="method"', page)
        method = evaluated_page.split('<details id="method">', 1)[1].split('</details>', 1)[0]
        self.assertNotIn('Travail humain restant', method)
        self.assertIn('mêmes consignes et pièces', method)
        self.assertNotIn('<dl', method)
        self.assertNotIn('Population entière utilisée', method)
        self.assertNotIn('Contrat et portée exacte', method)
        main = page.split('<main', 1)[1].split('</main>', 1)[0]
        self.assertNotIn('>Mes cas d’usage</a>', main)
        nav = page.split('<nav class="steps"', 1)[1].split('</nav>', 1)[0]
        self.assertIn(value['dossier_href'] + '#validation', nav)
        self.assertIn(value['href'] + '/configurations', nav)
        self.assertIn('aria-current="step"><span class="n">5</span>Résultats', nav)

    def test_unconfigured_judgment_is_visible_in_empty_results_without_model_failure(self):
        value = r.comparison(self.store, self.sid, 'fixture', 'comparison')
        value.update(history=[], rows=[], population=[], pending_attempts=[{
            'attempt_id': 'private-attempt', 'state': 'JUDGMENT_NOT_CONFIGURED',
            'next_action': 'Réponse reçue. Le jugement de ce parcours n’est pas encore raccordé.'}])
        value['coverage']['evaluated_attempts'] = 0
        page = views.render(value, '').decode()
        summary = page.split('aria-label="Conclusion de la campagne">', 1)[1].split('</div>', 1)[0]
        self.assertIn('En attente d’évaluation', summary)
        self.assertIn('Le jugement de ce parcours n’est pas encore raccordé.', summary)
        self.assertNotIn('private-attempt', summary)
        self.assertNotIn('échec', summary.lower())
        self.assertNotIn('terminé', summary.lower())

    def test_publication_empty_html_and_verified_bytes_keep_the_same_boundary(self):
        with tempfile.TemporaryDirectory(prefix='s10-web-') as tmp:
            public = Path(tmp).resolve()
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            process = multiprocessing.get_context('spawn').Process(target=serve_web,
                args=('127.0.0.1', port, public, public / 'absent.sock', 'a' * 40))
            process.start()
            try:
                base = f'http://127.0.0.1:{port}'
                deadline = time.monotonic() + 5
                while True:
                    try:
                        with urlopen(base + '/healthz', timeout=2):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.02)
                for accept, method in [('text/html', 'GET'), ('application/json', 'GET'), ('text/html', 'HEAD')]:
                    with self.assertRaises(HTTPError) as denied:
                        urlopen(Request(base + '/index.html', headers={'Accept': accept}, method=method), timeout=2)
                    with denied.exception as response:
                        self.assertEqual(404, response.code)
                        self.assertEqual('no-store', response.headers['Cache-Control'])
                        raw = response.read()
                        if method == 'HEAD':
                            self.assertEqual(b'', raw)
                        elif accept == 'text/html':
                            self.assertIn('text/html', response.headers['Content-Type'])
                            self.assertIn('Aucune publication vérifiée disponible'.encode(), raw)
                            self.assertIn(b'href="/preparation"', raw)
                            self.assertNotIn(b'PRIVATE_UNSELECTED', raw)
                        else:
                            self.assertEqual({'error': 'NO_VERIFIED_PUBLICATION'}, json.loads(raw))
                self.assertFalse((public / 'active.json').exists())
                bundle = r.preview(self.store, self.sid, 'fixture', 'comparison', piece_ids=[], presentation=projection)
                pub.materialize(bundle, dict(actor='approbateur-fictif-S6', authority_id='TEST_ONLY_PUBLICATION_S6',
                    projection_sha256=bundle['projection_sha256'], catalogue=False), public)
                with urlopen(Request(base + '/index.html', headers={'Accept': 'text/html'}), timeout=2) as response:
                    self.assertEqual(bundle['files']['index.html'], response.read())
                    self.assertIn('/publications/' + bundle['projection_sha256'], response.url)
                (public / bundle['projection_sha256'] / 'index.html').write_bytes(b'UNVERIFIED_BYTES')
                with self.assertRaises(HTTPError) as denied:
                    urlopen(Request(base + '/index.html', headers={'Accept': 'text/html'}), timeout=2)
                with denied.exception as response:
                    self.assertEqual(404, response.code)
                    self.assertNotIn(b'UNVERIFIED_BYTES', response.read())
            finally:
                if process.is_alive():
                    process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join()
