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

from benchmark import (campaigns as c, evaluation as e, preparation as p, publications as pub,
                       qualification as q, restitution as r, web_api)
from benchmark_web import fragments, projection, views
from benchmark_web.server import serve_web
from tests.test_s4_regressions import inputs, manifest, response
from tests.test_s5_regressions import EVALUATION_AUTHORITY, RESPONSIBLE, findings
from tests.test_s6_regressions import Markup, build


class S10ProofTests(unittest.TestCase):
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
        self.assertEqual(('region', '0', 'Observations du cas 1'),
                         (region['role'], region['tabindex'], region['aria-label']))
        self.assertEqual(5, sum(tag == 'th' and attrs.get('scope') == 'col' for tag, attrs in parsed.tags))
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

            c.execute(data, 'long', transport)
            e.evaluate(cls.store, 'proof', 'long', responsible=RESPONSIBLE,
                       authority=EVALUATION_AUTHORITY, check=findings)

    def setUp(self):
        before = list(self.store._connection.iterdump())
        self.addCleanup(lambda: self.assertEqual(before, list(self.store._connection.iterdump())))

    def test_complete_html_and_raw_proofs_preserve_context(self):
        query = dict(case='notes', sort='cost', direction='desc')
        with patch.object(e, 'piece_bytes', wraps=e.piece_bytes) as guarded:
            detail = r.detail(self.store, self.sid, 'fixture', 'proof', 'long', query=query)
        record = detail['history'][-1]
        self.assertEqual(len(record['proof_links']), guarded.call_count)
        self.assertEqual(self.output, record['proof_contents'][record['output_piece_id']])
        page = views.render(detail, '')
        self.assertIn(('<div class="proof-text">' + escape(self.output, quote=True) + '</div>').encode(), page)
        parsed = Markup(page)
        self.assertFalse(any(tag in ('script', 'img') or any(k.startswith('on') for k in attrs)
                             for tag, attrs in parsed.tags))
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
        with patch.object(e, 'piece_bytes', wraps=e.piece_bytes) as guarded:
            with self.assertRaises(p.Denied):
                r.detail(self.store, 'foreign', 'fixture', 'proof', 'long')
            guarded.assert_not_called()
            comparison = r.comparison(self.store, self.sid, 'fixture', 'proof')
            guarded.assert_not_called()
        self.assertTrue(all('proof_contents' not in record for record in comparison['history']))
        row = comparison['rows'][0]
        with self.assertRaises(p.Denied):
            e.piece_bytes(self.store, self.sid, 'fixture', row['evaluation_id'], 'unlinked')

    def test_summary_keeps_full_population_and_readable_fields_without_changing_evidence(self):
        value = r.comparison(self.store, self.sid, 'fixture', 'comparison')
        before = deepcopy(value)
        page = views.render(value, '').decode()
        filtered = views.render(r.comparison(self.store, self.sid, 'fixture', 'comparison',
                                        query={'verdict': 'NE SATISFAIT PAS', 'sort': 'cost'}), '').decode()
        summary = lambda html: html.split('aria-label="Conclusion de la campagne">', 1)[1].split('</div>', 1)[0]
        self.assertEqual(summary(page), summary(filtered))
        self.assertIn('5 ligne(s) affichée(s) sur 5 tentatives évaluées · ordre descriptif, sans préférence.', page)
        self.assertIn('1 ligne(s) affichée(s) sur 5 tentatives évaluées · ordre Coût observé, croissant.', filtered)
        self.assertIn('1 non satisfait(s)', summary(page))
        self.assertIn('3 satisfait(s)', summary(page))
        self.assertIn('Comparaison des coûts incomplète', summary(page))
        self.assertIn('body class="s9 comparison"', page)
        self.assertIn('<h1>Organiser des notes fictives</h1>', page)
        self.assertLess(page.index('<table>'), page.index('id="method"'))
        self.assertNotIn('open', next(attrs for tag, attrs in Markup(page.encode()).tags if attrs.get('id') == 'filters'))
        self.assertIn('Configuration demandée', page)
        self.assertIn('Configuration observée', page)
        self.assertNotIn('&quot;observed_configuration&quot;', page)
        self.assertNotIn('True bool', page)
        self.assertIn('>Oui</span>', page)
        self.assertEqual(before, value)
        hostile = fragments.readable_fields({'parameters': {'<img src=x onerror=alert(1)>': '<script>bad()</script>'}})
        self.assertNotIn('<script>', hostile)
        self.assertFalse(any(tag == 'img' for tag, _ in Markup(hostile.encode()).tags))

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
