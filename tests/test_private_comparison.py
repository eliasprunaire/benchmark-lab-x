"""Private operator flow, real Pi with simulated HTTP; never a provider call"""
from base64 import b64decode
from contextlib import closing, redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from benchmark_lab_x import campaigns as c, evaluation as e, pi_openrouter as pi, runtime, storage
from tests.test_s4_regressions import inputs, manifest, response
from tests import test_s5_regressions as s5
from tests.test_s5_regressions import findings


def pi_installation():
    configured = os.environ.get('BENCHMARK_TEST_PI_PACKAGE')
    executable = shutil.which('pi')
    package = Path(configured) if configured else Path(executable).resolve().parents[2] if executable else None
    node = shutil.which('node')
    return package, node


class PrivateEvaluationTests(unittest.TestCase):
    def setUp(self):
        # Reuse the complete S3/S4 fixture; no second business fixture schema
        self.fixture = s5.S5Regressions()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.acquire()
        self.store = self.fixture.store
        self.data = self.fixture.data

    def test_campaign_budget_includes_non_candidate_operations(self):
        operation = dict(operation_id='separate-judgment', phase='judgment', dossier_id='fixture',
                         revision=self.fixture.view['revision'], authority='GO_FICTIF',
                         engine_version='test-s1', requested_configuration={'model':'fictif'}, resources=[])
        self.store.reserve_intent(operation, 'local-comparison', '3')
        expected = self.store.inspect_budget('local-comparison')
        self.assertEqual(expected['reserved'], '3')
        self.assertEqual(c.inspect(self.store, 'local-comparison')['budget'], expected)
        self.assertTrue(runtime.verify(self.store)['integrity_ok'])


    def request(self, status='PASS'):
        prepared = e.prepare_report(self.store, 'local-comparison', 'intent-x')
        ctx = e._context(self.store, e.connection_for(self.store), 'local-comparison', 'intent-x')
        report = findings(ctx, e._resources(self.store, ctx))
        report['judgment']['mode'] = 'human'
        report['findings'][0].update(status=status, attribution='candidate')
        return dict(campaign_id='local-comparison', attempt_id='intent-x', responsible='Opérateur du test logiciel',
                    authority=dict(actor='Ayo', authority_id='offline-review-witness'),
                    report=report, previous_evaluation_id=prepared['previous_evaluation_id'])

    def test_real_responsibility_preserves_fictional_history_and_correction(self):
        old = self.fixture.evaluate()
        request = self.request()
        record = e.submit_report(self.store, request)
        self.assertEqual('SATISFAIT', record['verdict'])
        self.assertEqual(request['responsible'], record['responsible'])
        self.assertEqual(request['authority']['authority_id'], record['authority_id'])
        self.assertEqual('Ayo', record['authority_actor'])
        self.assertEqual(old, e.inspect(self.store, old['evaluation_id']))
        request = self.request('FAIL')
        corrected = e.submit_report(self.store, request)
        self.assertEqual('NE SATISFAIT PAS', corrected['verdict'])
        self.assertEqual(record, e.inspect(self.store, record['evaluation_id']))
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_operator_authority_and_exact_evidence_are_required(self):
        for change in ('passage', 'piece', 'authority', 'unapproved', 'assisted'):
            with self.subTest(change=change):
                req = self.request()
                if change == 'passage':
                    req['report']['findings'][0]['evidence'][0]['passage'] = 'Invented proof'
                elif change == 'piece':
                    req['report']['findings'][0]['evidence'][0]['piece_id'] = 'foreign'
                elif change == 'authority':
                    req['authority']['actor'] = 'candidate'
                elif change == 'unapproved':
                    req['authority'] = None
                else:
                    req['report']['judgment']['mode'] = 'assisted'
                with self.assertRaises((ValueError, TypeError)):
                    e.submit_report(self.store, req)
        self.assertEqual(0, self.store._connection.execute('SELECT count(*) FROM s5_evaluations').fetchone()[0])

    def test_missing_findings_require_review_without_official_verdict(self):
        req = self.request()
        req['report']['findings'] = []
        req['report']['limits'].append('Relecture interrompue : conserver ce travail')
        req['report']['judgment']['disagreements'].append(dict(finding='Contrôle à arbitrer', arbitration=None))
        record = e.submit_report(self.store, req)
        self.assertIsNone(record['verdict'])
        self.assertTrue(record['evaluation_id'])
        self.assertEqual('REVIEW_REQUIRED', record['state'])
        self.assertEqual(record, e.inspect(self.store, record['evaluation_id']))
        self.assertEqual(req['report']['judgment']['disagreements'], record['judgment']['disagreements'])
        self.assertIn('Relecture interrompue : conserver ce travail', record['limits'])
        self.assertEqual(1, self.store._connection.execute('SELECT count(*) FROM s5_evaluations').fetchone()[0])
        from benchmark_lab_x import restitution
        comparison = restitution.comparison(self.store, self.fixture.session, 'fixture', 'local-comparison')
        self.assertEqual(['intent-x'], [a['attempt_id'] for a in comparison['pending_attempts']])
        completed = e.submit_report(self.store, self.request())
        self.assertEqual('SATISFAIT', completed['verdict'])
        self.assertEqual(record['evaluation_id'], completed['previous_evaluation_id'])
        self.assertEqual(completed, e.inspect(self.store, completed['evaluation_id']))

    def test_attempt_status_cli_without_emission_or_verdict(self):
        path = self.fixture.home / 'status.json'
        path.write_text(json.dumps(dict(campaign_id='local-comparison', attempt_id='intent-x')))
        path.chmod(0o600)
        before = self.store.inspect_operations()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, runtime.main(['inspect-attempt-status', '--data', str(self.data), '--authority', str(path)]))
        status = json.loads(out.getvalue())
        self.assertIsNone(status['verdict'])
        self.assertTrue(status['next_action'])
        self.assertEqual(before, self.store.inspect_operations())

    def test_private_operator_cli_and_existing_projection(self):
        root = self.fixture.home
        command = root / 'review.json'
        command.write_text(json.dumps(dict(campaign_id='local-comparison', attempt_id='intent-x')))
        command.chmod(0o600)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, runtime.main(['prepare-evaluation', '--data', str(self.data), '--authority', str(command)]))
        self.assertTrue(all(x['status'] == 'INDETERMINE' for x in json.loads(out.getvalue())['report']['findings']))
        command.write_text(json.dumps(self.request()))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, runtime.main(['evaluate-attempt', '--data', str(self.data), '--authority', str(command)]))
        record = json.loads(out.getvalue())
        projected = e.projection(self.store, self.store._connection, 'fixture', 'local-comparison')
        self.assertEqual(record['verdict'], projected[0]['verdict'])
        self.assertNotIn('authorization', projected[0])
        self.assertNotIn('approved_report', projected[0])
        self.assertTrue(projected[0]['proof_links'])


class PiTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package, cls.node = pi_installation()
        if cls.package is None or cls.node is None:
            raise unittest.SkipTest('Pi and Node absent; set BENCHMARK_TEST_PI_PACKAGE for the real offline harness proof')
        cls.identity = pi.identity(cls.package, cls.node)

    def setUp(self):
        self.fixture = s5.S5Regressions()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.data = self.fixture.data
        # The simulated fixture remains; add a separate campaign with actual Pi identity
        # This fixture declares TEST in its frozen cost basis: use a fresh qualified USD fixture
        from tests.test_s3_regressions import fixture, specification, check, ACTOR, AUTHORITY
        from benchmark_lab_x import preparation as prep, qualification as q
        self.realdata = self.fixture.home / 'pi-private'
        session, view, reference = fixture(self.realdata)
        self.session = session
        q.initialize(self.realdata)
        realstore = storage.Store(self.realdata)
        self.addCleanup(realstore.close)
        spec = specification(reference)
        spec['cost_basis']['unit'] = 'USD'
        draft = q.draft(realstore, 'fixture', view['revision'], spec)
        qualified = q.qualify(realstore, draft['contract_sha256'], reviewer=ACTOR, check=check)
        q.approve(realstore, draft['contract_sha256'], qualified['qualification_id'], actor=ACTOR, authority=AUTHORITY)
        prep.close_admission(realstore)
        c.initialize(self.realdata)
        e.initialize(self.realdata)
        realstore.create_budget('pi-offline', '1', 'USD')
        value = manifest(draft, 'pi-offline')
        for config in value['panel']:
            config.update(provider='Fictional provider', model='fixture/model-fixed', revision='fixture/model-fixed',
                access='API', channel_id=pi.http.ENDPOINT, route='fixture/route', effort='off',
                parameters=dict(max_tokens=64, temperature=0, provider=dict(only=['fixture/route'], order=['fixture/route'],
                    allow_fallbacks=False, require_parameters=True)))
        value['panel'][1].update(model='fixture/model-other', revision='fixture/model-other')
        value['conditions'].update(pi={k:self.identity[k] for k in ('package','version','sha256')} | dict(status='configured', proof='Real local module fingerprint'),
            context_sha256=sha256(pi.system_context('Contexte commun fictif').encode()).hexdigest(),
            defaults=dict(system_prompt='Contexte commun fictif', timeout_seconds=10, context_window=4096),
            environment={k:self.identity[k] for k in ('node_version','node_sha256','bridge_sha256')})
        campaign = c.create(realstore, value)
        authority, evidence = inputs(campaign, budget='pi-offline')
        authority['reserve_amounts'] = {'x':'0.1','y':'0.1'}
        c.admit(realstore, 'pi-offline', authority, evidence)
        c.reserve(realstore, 'pi-offline', 'x', 'pi-intent')
        self.store, self.data = realstore, self.realdata
        self.transport = pi.PiOpenRouter('fixture-key-not-a-credential', self.package, self.node)
        self.raw = json.dumps(dict(model='fixture/model-fixed', choices=[dict(finish_reason='stop',
            message=dict(role='assistant',content='  Sortie fictive inchangée\n'))],
            openrouter_metadata=dict(endpoints=dict(available=[dict(selected=True, provider='Fictional provider',tag='fixture/route')])),
            usage=dict(cost='0.00001'))).encode()

    def http(self, key, wire, timeout):
        self.wire = json.loads(wire)
        return 200, {}, self.raw, True, '2026-09-10T10:00:00+00:00', time.monotonic()

    def execute(self):
        with patch.object(pi.http, 'post', side_effect=self.http) as http:
            c.execute(self.data, 'pi-intent', self.transport)
            self.assertEqual(1, http.call_count)
        return c.inspect(self.store, 'pi-offline')['attempts'][0]

    def test_real_pi_fictional_http_to_native_evaluation_and_restoration(self):
        before = len(self.store.inspect_operations())
        attempt = self.execute()
        self.assertEqual('RECEIVED', attempt['state'])
        self.assertEqual('  Sortie fictive inchangée\n'.encode(), self.store.read_piece(attempt['output_piece_id']))
        observed = attempt['operation']['receipt']['observed_configuration']
        self.assertTrue(observed['pi']['terminal'])
        self.assertEqual(self.raw, b64decode(observed['http']['body_base64']))

        self.assertNotIn('reference', self.wire['messages'][1]['content'])
        self.assertEqual(pi.system_context('Contexte commun fictif'), self.wire['messages'][0]['content'])
        self.assertEqual(before, len(self.store.inspect_operations()))
        with patch.object(pi.http, 'post') as http, self.assertRaises(storage.ConflictError):
            c.execute(self.data, 'pi-intent', self.transport)
        http.assert_not_called()
        ctx = e._context(self.store, self.store._connection, 'pi-offline', 'pi-intent')
        report = findings(ctx, e._resources(self.store, ctx))
        authority = dict(actor='Ayo', authority_id='offline-verdict-proof')
        evaluated = e.evaluate(self.store, 'pi-offline', 'pi-intent', responsible='Contrôleur du test logiciel',
            authority=authority, check=lambda ctx, resources:report)
        self.assertEqual('SATISFAIT', evaluated['verdict'])
        from benchmark_lab_x import restitution
        comparison = restitution.comparison(self.store, self.session, 'fixture', 'pi-offline')
        self.assertIn('SATISFAIT', storage._strict_json(comparison))
        runtime.stop(self.data, self.store, 'END_OFFLINE_PROOF')
        backup = self.fixture.home / 'backup-pi'
        restored = self.fixture.home / 'restored-pi'
        runtime.backup(self.data, backup)
        runtime.restore(backup, restored)
        with closing(storage.Store(restored)) as store:
            self.assertEqual(evaluated, e.inspect(store, evaluated['evaluation_id']))
            self.assertFalse(runtime.status(restored, store)['admission'])

    def test_http_error_without_model_is_not_an_identity_contradiction(self):
        from benchmark_lab_x import recovery
        raw = b'{"error":{"code":429,"message":"Rate limited"}}'
        with patch.object(pi.http, 'post', return_value=(429, {}, raw, True,
                '2026-09-10T10:00:00+00:00', time.monotonic())):
            c.execute(self.data, 'pi-intent', self.transport)
        attempt = c.inspect(self.store, 'pi-offline')['attempts'][0]
        self.assertEqual('ROUTE_ERROR', recovery.observation(attempt)['kind'])
        self.assertIsNone(attempt['operation']['receipt']['observed_configuration']['model'])
        self.assertEqual('UNKNOWN', attempt['operation']['observed_cost']['status'])
        self.assertTrue(runtime.verify(self.store)['integrity_ok'])

    def test_success_without_model_still_cannot_be_attributed(self):
        document = json.loads(self.raw)
        del document['model']
        self.raw = json.dumps(document).encode()
        attempt = self.execute()
        self.assertEqual('MODEL_IDENTITY_MISMATCH', attempt['operation']['receipt']['result']['incident'])

    def test_two_models_share_context_and_keep_their_verdicts_when_sorted(self):
        from benchmark_lab_x import restitution
        first_context = None
        for cell, attempt_id in (('x', 'pi-intent'), ('y', 'pi-other')):
            if cell == 'y':
                c.reserve(self.store, 'pi-offline', cell, attempt_id)
                changed = json.loads(self.raw)
                changed['model'] = 'fixture/model-other'
                changed['usage']['cost'] = '0.000001'
                changed['choices'][0]['message']['content'] = 'Autre sortie fictive'
                self.raw = json.dumps(changed).encode()
            with patch.object(pi.http, 'post', side_effect=self.http) as post:
                c.execute(self.data, attempt_id, self.transport)
            self.assertEqual(1, post.call_count)
            context = self.wire['messages']
            if first_context is None:
                first_context = context
            self.assertEqual(first_context, context)
            ctx = e._context(self.store, self.store._connection, 'pi-offline', attempt_id)
            report = findings(ctx, e._resources(self.store, ctx))
            if cell == 'y':
                report['findings'][0].update(status='FAIL', attribution='candidate')
            e.evaluate(self.store, 'pi-offline', attempt_id, responsible='Contrôleur du test logiciel',
                authority=dict(actor='Ayo', authority_id='offline-comparison-proof'), check=lambda ctx, resources:report)
        value = restitution.comparison(self.store, self.session, 'fixture', 'pi-offline', query={'sort':'cost'})
        self.assertEqual('COMPLETE', value['economic_status'])
        self.assertEqual(2, value['coverage']['evaluated_attempts'])
        self.assertEqual(['NE SATISFAIT PAS', 'SATISFAIT'], [row['verdict'] for row in value['rows']])
        self.assertEqual(['fixture/model-other', 'fixture/model-fixed'], [row['observed_configuration']['model'] for row in value['rows']])

    def test_drift_refuses_before_emission(self):
        with patch.object(pi, 'identity', return_value={**self.identity, 'sha256':'0'*64}), patch.object(pi.http, 'post') as http:
            with self.assertRaises(ValueError):
                c.execute(self.data, 'pi-intent', self.transport)
        http.assert_not_called()
        self.assertEqual('INTENT_RECORDED', c.inspect(self.store, 'pi-offline')['attempts'][0]['state'])

    def test_unusable_response_keeps_raw_and_unknown_reserve_without_retry(self):
        self.raw = json.dumps(dict(model='fixture/model-fixed', choices=[dict(finish_reason='length',
            message=dict(role='assistant',content='partial'))])).encode()
        attempt = self.execute()
        self.assertEqual('RECEIVED', attempt['state'])
        self.assertTrue(attempt['operation']['receipt']['observed_configuration']['pi']['terminal'])
        self.assertEqual('UNKNOWN', attempt['operation']['observed_cost']['status'])
        self.assertEqual('0.1', self.store.inspect_budget('pi-offline')['reserved'])
        self.assertFalse(c.inspect(self.store,'pi-offline')['admission'])

    def test_late_pi_failure_preserves_http_receipt_and_cost(self):
        read = pi._read_line
        def fail_terminal(process, timeout):
            event = read(process, timeout)
            if event['type'] == 'done':
                raise ValueError('Fictional terminal failure')
            return event
        with patch.object(pi, '_read_line', side_effect=fail_terminal):
            attempt = self.execute()
        receipt = attempt['operation']['receipt']
        self.assertEqual('RECEIVED', attempt['state'])
        self.assertEqual('HARNESS_ERROR', receipt['result']['incident'])
        self.assertEqual(self.raw, b64decode(receipt['observed_configuration']['http']['body_base64']))
        self.assertEqual('KNOWN', attempt['operation']['observed_cost']['status'])

    def test_reflected_credential_and_changed_context_cannot_be_accepted(self):
        request = json.loads(self.store._connection.execute(
            'SELECT request_json FROM s4_attempts WHERE operation_id=?', ('pi-intent',)).fetchone()[0])
        operation = next(o for o in self.store.inspect_operations() if o['operation_id'] == 'pi-intent')
        self.transport.prepare(c._transport_operation(operation), c._transport_view(request))
        clean = json.loads(self.raw)
        reflected = deepcopy(clean)
        reflected['choices'][0]['message']['content'] = self.transport._key
        changed = deepcopy(clean)
        changed['openrouter_metadata']['pipeline'] = [{'type':'response_healing'}]
        for raw in (json.dumps(reflected).encode(), json.dumps(reflected, ensure_ascii=True).replace('fixture-key', '\\u0066ixture-key').encode(), json.dumps(changed).encode()):
            self.raw = raw
            with patch.object(pi.http, 'post', side_effect=self.http):
                result = self.transport._exchange(operation, request)
            self.assertIsNotNone(result['receipt']['result']['incident'])
            self.assertNotIn(self.transport._key, storage._strict_json(result))
        self.assertEqual('INTENT_RECORDED', c.inspect(self.store, 'pi-offline')['attempts'][0]['state'])

    def test_cli_executes_only_named_admitted_attempt(self):
        request = self.fixture.home / 'execute.json'
        request.write_text(json.dumps(dict(campaign_id='pi-offline',attempt_id='pi-intent')))
        request.chmod(0o600)
        output=io.StringIO()
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture-key-not-a-credential'}), patch.object(pi.http,'post',side_effect=self.http) as http, redirect_stdout(output):
            code=runtime.main(['execute-candidate','--data',str(self.data),'--authority',str(request),
                '--pi-package',str(self.package),'--node',self.node])
        self.assertEqual(0,code)
        self.assertEqual('RECEIVED',json.loads(output.getvalue())['state'])
        self.assertEqual(1,http.call_count)


if __name__ == '__main__':
    unittest.main()
