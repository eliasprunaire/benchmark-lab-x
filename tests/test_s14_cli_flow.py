"""Synthetic S14 CLI flow with retained evidence and an operator decision"""
from contextlib import closing, redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io
import json
import os
import stat
import unittest
from unittest.mock import patch

from benchmark import runtime, storage
from tests import test_s14_acceptance as acceptance


class S14CLIFlow(unittest.TestCase):
    def test_assisted_proposal_requires_operator_decision_and_survives_reopen(self):
        h = acceptance.S14Acceptance()
        self.addCleanup(h.doCleanups)
        h.setUp()
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)

        def private_json(name, value):
            path = h.fixture.home / name
            with open(path, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
                json.dump(value, stream)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            return path

        profile = private_json('cli-profile.json', h.profile)

        def cli(action, request, *, assisted=False, expected_code=0):
            path = private_json(action + '.json', request)
            args = [action, '--data', str(h.data), '--authority', str(path)]
            environment = {}
            if assisted:
                args += ['--judgment-profile', str(profile)]
                environment['OPENROUTER_API_KEY'] = acceptance.KEY
            with patch.dict(os.environ, environment, clear=True), redirect_stdout(io.StringIO()) as output:
                if not assisted:
                    self.assertNotIn('OPENROUTER_API_KEY', os.environ)
                code = runtime.main(args)
            self.assertEqual(code, expected_code, output.getvalue())
            return json.loads(output.getvalue())

        identity = dict(campaign_id='local-comparison', attempt_id='intent-x')
        review = cli('prepare-review', identity)
        self.assertEqual(review['binding'], dict(**identity, previous_evaluation_id=None))
        self.assertEqual(review['content_sha256'],
                         sha256(storage._strict_json(review['content']).encode()).hexdigest())
        h.http.request.assert_not_called()
        self.assertEqual(h.count(), 0)

        request = dict(**review['binding'], operation_id='s14-judge',
                       review_sha256=review['content_sha256'],
                       authority=dict(actor='Ayo', authority_id='S14_SYNTHETIC_JUDGMENT_ONLY'),
                       budget_id='s14-budget', reserve_amount=h.reserve_amount,
                       requested_configuration=deepcopy(h.configuration))
        reserved = cli('reserve-judgment', request, assisted=True)
        binding = dict(**review['binding'], review_sha256=review['content_sha256'])
        self.assertEqual(reserved['binding'], binding)
        self.assertEqual(reserved['operation']['state'], 'INTENT_RECORDED')
        self.assertIsNone(reserved['proposal'])
        h.http.request.assert_not_called()
        self.assertEqual(h.count(), 0)

        h.answer['findings'][0].update(status='FAIL', attribution='candidate')
        h.set_response()
        operation = dict(operation_id=request['operation_id'])
        executed = cli('execute-judgment', operation, assisted=True)
        self.assertEqual(h.http.request.call_count, 1)
        self.assertEqual(h.http.getresponse.call_count, 1)
        self.assertEqual(executed['operation']['state'], 'RECEIVED')
        self.assertEqual(executed['proposal']['proposed_verdict'], 'SATISFAIT')
        self.assertEqual(h.count(), 0)

        inspected = cli('inspect-judgment', operation)
        self.assertEqual(inspected, executed)
        self.assertEqual(inspected['binding'], binding)
        report = inspected['proposal']['report']
        self.assertEqual(report['judgment']['mode'], 'assisted')
        self.assertEqual(report['judgment']['assistance_operation_id'], operation['operation_id'])
        self.assertEqual(report['findings'][0], h.answer['findings'][0])
        self.assertTrue(report['findings'][0]['evidence'])
        for finding in report['findings']:
            for proof in finding['evidence']:
                self.assertIn(proof['piece_id'], report['judgment']['resources_seen'])
                raw = h.store.read_piece(proof['piece_id'])
                self.assertEqual(proof['sha256'], sha256(raw).hexdigest())
                self.assertTrue(proof['passage'])
                self.assertIn(proof['passage'].encode(), raw)
        wire = h.http.request.call_args.kwargs['body']
        self.assertEqual(json.loads(json.loads(wire)['messages'][1]['content']), review['content'])
        self.assertEqual(inspected['operation']['receipt']['observed_configuration']['outgoing']
                         ['request_body_sha256'], sha256(wire).hexdigest())
        self.assertEqual(h.count(), 0)
        self.assertEqual(h.http.request.call_count, 1)

        decision = dict(**review['binding'], responsible='Responsable témoin S14',
                        authority=dict(actor='model', authority_id='S14_SYNTHETIC_OWNER_DECISION'),
                        report=deepcopy(report))
        refused = cli('evaluate-attempt', decision, expected_code=78)
        self.assertEqual(refused, dict(state='HOLD', reason='OPERATION_NOT_VERIFIED'))
        self.assertEqual(h.count(), 0)
        self.assertEqual(h.http.request.call_count, 1)

        decision['authority']['actor'] = 'Ayo'
        record = cli('evaluate-attempt', decision)
        self.assertEqual(record['verdict'], 'NE SATISFAIT PAS')
        self.assertEqual(record['previous_evaluation_id'], review['binding']['previous_evaluation_id'])
        self.assertEqual(record['judgment']['operation'], inspected['operation'])
        self.assertEqual(record['judgment']['cost']['amount'], '0.0002')
        self.assertEqual(h.count(), 1)
        self.assertEqual(h.http.request.call_count, 1)

        h.store.close()
        retained = cli('inspect-evaluation', dict(evaluation_id=record['evaluation_id']))
        self.assertEqual(retained, record)
        with closing(storage.Store(h.data)) as reopened:
            self.assertEqual(reopened._connection.execute(
                'SELECT evaluation_id FROM s5_evaluations').fetchall(), [(record['evaluation_id'],)])
            self.assertTrue(reopened.verify_storage()['integrity_ok'])
        self.assertEqual(h.http.request.call_count, 1)
        self.assertEqual(h.http.getresponse.call_count, 1)
