"""Integrated example reading with fictional data and existing ownership guards"""
from contextlib import closing
from html import escape
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark import preparation as prep, storage, web_api
from benchmark_web import views
from tests.test_s2_review_regressions import response_for
from tests.test_s6_regressions import Markup


class InlineExampleTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))

    def test_exact_inert_content_without_download_or_emission(self):
        content = '\nNotes de test\n<script>alert("test")</script>\n& fin'
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary).resolve() / 'private'
            storage.initialize(data)
            storage.initialize_preparation(data)
            with closing(storage.Store(data)) as store:
                store.create_budget('inline', '100', 'TEST')
                prep.admit(store, dict(authority_id='FICTIONAL_INLINE', budget_id='inline',
                    reserve_amount='7', requested_configuration={'model': 'fictional'}))
                session, csrf, token = prep.session(store, None, create=True)
                operation, _ = prep.submit(store, session, 'inline',
                    dict(action_id='create', request='Examiner des notes inventées'), 'a' * 40, True)

                def transport(op, request):
                    result = response_for(op)
                    result['receipt']['result']['package']['candidate']['pieces'][0]['content'] = content
                    return result

                prep.execute(data, operation, transport)
                before = store.inspect_operations()
                code, view, _, start = web_api.dispatch(store, 'GET', '/preparation/dossiers/inline',
                                                    token, None, 'a' * 40, None)
                self.assertEqual(200, code)
                self.assertIsNone(start)
                self.assertEqual([content], list(view['example_contents'].values()))
                page = views.render(view, csrf).decode()
                self.assertIn('<details class="example-content"><summary>Voir le contenu</summary>', page)
                self.assertIn('<div class="example-text">' + escape(content, quote=True) + '</div>', page)
                self.assertNotIn('<script>', page)
                self.assertNotIn('/pieces/', page)
                self.assertNotIn('notes.txt', page)
                self.assertNotIn('Attendu fictif réservé', page)
                parsed = Markup(page.encode())
                for kind in ('corr', 'example-content'):
                    details = [attrs for tag, attrs in parsed.tags
                               if tag == 'details' and attrs.get('class') == kind]
                    self.assertTrue(details)
                    self.assertTrue(all('open' not in attrs for attrs in details))
                self.assertIn('tabindex="-1"', page)
                self.assertLess(page.index('id="exemple"'), page.index('id="validation"'))
                self.assertLess(page.index('id="validation"'), page.index('class="corr"'))
                self.assertTrue(any(tag == 'label' and attrs.get('for') == 'message'
                                    for tag, attrs in parsed.tags))
                self.assertEqual(before, store.inspect_operations())
                other, _, other_token = prep.session(store, None, create=True)
                with self.assertRaises(prep.Denied):
                    web_api.dispatch(store, 'GET', '/preparation/dossiers/inline', other_token, None, 'a' * 40, None)
                judge = store._connection.execute("SELECT piece_id FROM pieces WHERE role='judge'").fetchone()[0]
                with self.assertRaises(prep.Denied):
                    prep.piece_bytes(store, session, 'inline', view['revision'], judge)
                with self.assertRaises(prep.Denied):
                    prep.piece_bytes(store, other, 'inline', view['revision'], view['package']['pieces'][0]['id'])

                operation, _ = prep.submit(store, session, 'inline', dict(action_id='correct',
                    revision=view['revision'], kind='correct', message='Changer les notes'), 'a' * 40, True)
                prep.execute(data, operation, lambda op, request: response_for(op))
                current = prep.view(store, session, 'inline')
                historical = prep.view(store, session, 'inline', view['revision'])
                self.assertEqual([content], list(historical['example_contents'].values()))
                self.assertEqual(['Action : relire'], list(current['example_contents'].values()))
                self.assertIsNone(current['validation'])
