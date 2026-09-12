"""Owner acceptance for S14, HTTP simulations only, immutable during GET"""
from base64 import b64decode
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import Mock, patch

from benchmark_lab_x import evaluation, outgoing, storage
from benchmark_lab_x import openrouter_preparation as profiles
from tests import test_s5_regressions as s5
from tests.test_openrouter_preparation import estimate_for, SYNTHETIC_PROFILE

try:
    from benchmark_lab_x import judgment, openrouter_judgment
except ImportError:
    judgment = openrouter_judgment = None

KEY = 'fixture-s14-key-not-a-credential'


class S14Acceptance(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(judgment, 'S14: judgment module is not implemented')
        self.assertIsNotNone(openrouter_judgment, 'S14: OpenRouter judgment transport is not implemented')
        self.fixture = s5.S5Regressions()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.acquire()
        self.store, self.data = self.fixture.store, self.fixture.data
        self.store.create_budget('s14-budget', '100', 'USD')
        self.profile = profiles.load_profile(str(SYNTHETIC_PROFILE))
        self.profile['system'] = 'S14 synthetic reviewer: propose findings, measures, limits and proposed_verdict as JSON'
        self.configure(self.profile)
        self.http = Mock()
        self.http.getresponse.return_value.status = 200
        self.http.getresponse.return_value.length = 0
        self.http.getresponse.return_value.getheader.return_value = None
        for module in (profiles, openrouter_judgment):
            p = patch.object(module, 'HTTPSConnection', return_value=self.http, create=True)
            p.start(); self.addCleanup(p.stop)
        ctx = evaluation._context(self.store, evaluation.connection_for(self.store), 'local-comparison', 'intent-x')
        report = s5.findings(ctx, evaluation._resources(self.store, ctx))
        self.answer = {k: report[k] for k in ('findings', 'measures', 'limits')}
        self.answer['proposed_verdict'] = 'SATISFAIT'
        self.set_response()

    def configure(self, profile):
        self.profile = deepcopy(profile)
        estimate = estimate_for(profile)
        self.configuration = profiles.configuration(estimate, profile)
        self.reserve_amount = profiles.reservation(estimate, profile)
        self.transport = openrouter_judgment.OpenRouterJudgment(KEY, profile)

    def set_response(self, *, answer=None, cost='0.0002', finish='stop', **updates):
        doc = dict(id='s14-http-fixture', model=self.profile['revision'],
            choices=[dict(finish_reason=finish, message=dict(role='assistant', content=storage._strict_json(
                self.answer if answer is None else answer)))],
            usage={'cost': cost} if cost is not None else {},
            openrouter_metadata=dict(requested=self.profile['model'],
                endpoints=dict(available=[dict(provider=self.profile['routes'][0]['provider_name'], selected=True)])))
        doc.update(updates)
        raw = storage._strict_json(doc).encode()
        self.http.getresponse.return_value.read.return_value = raw
        return raw

    def request(self, oid='s14-judge'):
        review = evaluation.prepare_review(self.store, 'local-comparison', 'intent-x')
        return dict(operation_id=oid, campaign_id='local-comparison', attempt_id='intent-x',
            review_sha256=review['content_sha256'],
            previous_evaluation_id=review['binding']['previous_evaluation_id'],
            authority=dict(actor='Ayo', authority_id='S14_SYNTHETIC_JUDGMENT_ONLY'),
            budget_id='s14-budget', reserve_amount=self.reserve_amount,
            requested_configuration=deepcopy(self.configuration))

    def execute(self, oid='s14-judge'):
        judgment.reserve(self.store, self.request(oid), self.transport)
        judgment.execute(self.data, oid, self.transport)
        return judgment.inspect(self.store, oid)

    def submit(self, view):
        binding = view['binding']
        return evaluation.submit_report(self.store, dict(
            campaign_id=binding['campaign_id'], attempt_id=binding['attempt_id'],
            previous_evaluation_id=binding['previous_evaluation_id'], responsible='Responsable témoin S14',
            authority=dict(actor='Ayo', authority_id='S14_SYNTHETIC_OWNER_DECISION'),
            report=deepcopy(view['proposal']['report'])))

    def count(self):
        return self.store._connection.execute('SELECT count(*) FROM s5_evaluations').fetchone()[0]

    def test_closed_wire_two_profiles_durable_before_http(self):
        for index, profile in enumerate((deepcopy(self.profile), profiles.frozen_profile())):
            profile['system'] = 'S14 reviewer system ' + str(index)
            self.configure(profile); self.set_response()
            oid = 's14-profile-' + str(index)
            request = self.request(oid)
            request['authority']['authority_id'] = 'INTERNAL_AUTHORITY_CANARY'
            self.store.put_piece('fixture', self.fixture.view['revision'], 'unrelated-'+str(index),
                name='internal.txt', role='judge', media_type='text/plain', content=b'UNRELATED_JUDGE_CANARY')
            def at_http(method, path, *, body, headers):
                with closing(storage.Store(self.data)) as reader:
                    op = next(x for x in reader.inspect_operations() if x['operation_id'] == oid)
                    self.assertEqual(op['state'], 'EMISSION_POSSIBLE')
                    self.assertIn(body.decode(), op['resources'])
                    self.assertEqual(op['phase'], 'judgment')
                sent = json.loads(body)
                self.assertEqual(sent['model'], profile['model'])
                self.assertEqual({k: sent[k] for k in profile['parameters']}, profile['parameters'])
                self.assertEqual(sent['messages'][0], {'role':'system','content':profile['system']})
                expected = evaluation.prepare_review(self.store,'local-comparison','intent-x')['content']
                self.assertEqual(json.loads(sent['messages'][1]['content']), expected)
                for canary in ('INTERNAL_AUTHORITY_CANARY','UNRELATED_JUDGE_CANARY',str(self.data),
                               's14-budget','local-comparison','reservation_estimate','authority_id','transport_log'):
                    self.assertNotIn(canary.encode(), body)
            self.http.request.side_effect = at_http
            judgment.reserve(self.store, request, self.transport)
            judgment.execute(self.data, oid, self.transport)
            view = judgment.inspect(self.store, oid)
            self.assertIsNotNone(view['proposal'])
            wire = self.http.request.call_args.kwargs['body']
            proof = view['operation']['receipt']['observed_configuration']['outgoing']
            self.assertEqual(proof['request_body_sha256'], sha256(wire).hexdigest())
            self.assertEqual(proof['outgoing_format'], outgoing.FORMAT)
        self.assertEqual(self.count(), 0)

    def test_bad_admission_does_not_reserve_or_call(self):
        before = self.store.inspect_operations()
        for kind in ('hash','attempt','configuration','budget','authority','reserve','predecessor'):
            with self.subTest(kind=kind):
                req = self.request('bad-'+kind)
                if kind == 'hash': req['review_sha256'] = '0'*64
                elif kind == 'attempt': req['attempt_id'] = 'foreign'
                elif kind == 'configuration': req['requested_configuration']['model'] = 'foreign/model'
                elif kind == 'budget': req['budget_id'] = 'absent'
                elif kind == 'authority': req['authority']['actor'] = 'model'
                elif kind == 'reserve': req['reserve_amount'] = '0'
                else: req['previous_evaluation_id'] = 'foreign'
                with self.assertRaises((ValueError,KeyError,storage.IntegrityError,storage.ConflictError,storage.BudgetError)):
                    judgment.reserve(self.store,req,self.transport)
                self.assertEqual(before,self.store.inspect_operations())
        self.http.request.assert_not_called()

    def test_source_and_profile_drift_refuse_before_http(self):
        judgment.reserve(self.store,self.request(),self.transport)
        p = evaluation.prepare_review(self.store,'local-comparison','intent-x')['content']['output']['piece_id']
        file = self.data/self.store.get_piece(p)['relative_path']
        file.write_bytes(b'changed output')
        try: judgment.execute(self.data,'s14-judge',self.transport)
        except (ValueError,storage.IntegrityError,storage.ConflictError): pass
        self.http.request.assert_not_called()
        self.assertEqual(self.count(),0)

    def test_model_verdict_cannot_create_or_override_official_verdict(self):
        self.answer['findings'][0].update(status='FAIL',attribution='candidate')
        self.set_response()
        view = self.execute()
        self.assertEqual(self.count(),0)
        self.assertEqual(view['proposal']['proposed_verdict'],'SATISFAIT')
        self.assertEqual(view['proposal']['report']['judgment']['mode'],'assisted')
        record = self.submit(view)
        self.assertEqual(record['verdict'],'NE SATISFAIT PAS')
        self.assertEqual(record['judgment']['operation']['operation_id'],'s14-judge')
        self.assertEqual(record['judgment']['cost']['amount'],'0.0002')
        self.assertEqual(evaluation.inspect(self.store,record['evaluation_id']),record)

    def test_unusable_response_keeps_receipt_cost_without_proposal(self):
        for index, kind in enumerate(('empty','length','refusal','json','extra-authority','wrong-model','tool','false-proof')):
            with self.subTest(kind=kind):
                self.set_response()
                doc = json.loads(self.http.getresponse.return_value.read.return_value)
                msg = doc['choices'][0]['message']
                if kind=='empty': msg['content']=''
                elif kind=='length': doc['choices'][0]['finish_reason']='length'
                elif kind=='refusal': msg['refusal']='Refused';msg['content']=''
                elif kind=='json': msg['content']='not json'
                elif kind=='wrong-model': doc['model']='foreign/model'
                elif kind=='tool': msg['tool_calls']=[{'id':'unexpected'}]
                else:
                    answer=deepcopy(self.answer)
                    if kind=='extra-authority': answer['authority']={'actor':'Ayo'}
                    else: answer['findings'][0]['evidence'][0]['passage']='ABSENT_PASSAGE'
                    msg['content']=storage._strict_json(answer)
                raw=storage._strict_json(doc).encode()
                self.http.getresponse.return_value.read.return_value=raw
                view=self.execute('unusable-'+str(index))
                self.assertIsNone(view['proposal'])
                self.assertEqual(view['operation']['state'],'RECEIVED')
                self.assertEqual(view['operation']['observed_cost']['amount'],'0.0002')
                observed=view['operation']['receipt']['observed_configuration']
                self.assertEqual(b64decode(observed['http']['body_base64']),raw)
                self.assertIn('diagnostic', view)
                if kind == 'false-proof':
                    self.assertEqual(view['diagnostic']['state'], 'EVIDENCE_REVIEW_REQUIRED')
                if kind == 'refusal':
                    self.assertEqual(view['diagnostic']['state'], 'REFUSAL_REVIEW_REQUIRED')
        self.assertEqual(self.http.request.call_count,8)
        self.assertEqual(self.count(),0)

    def test_unknown_cost_and_timeout_block_dependent_replay(self):
        self.set_response(cost=None)
        view=self.execute()
        self.assertEqual(view['operation']['observed_cost']['status'],'UNKNOWN')
        self.assertEqual(self.store.inspect_budget('s14-budget')['reserved'],self.reserve_amount)
        with self.assertRaises((ValueError,storage.BudgetError,storage.ConflictError)):
            judgment.reserve(self.store,self.request('blocked'),self.transport)
        self.assertEqual(self.http.request.call_count,1)

    def test_interrupted_judgment_does_not_replay(self):
        judgment.reserve(self.store,self.request(),self.transport)
        self.http.getresponse.side_effect=TimeoutError('synthetic timeout')
        for _ in range(2):
            try: judgment.execute(self.data,'s14-judge',self.transport)
            except (TimeoutError,ValueError,storage.ConflictError): pass
        op=next(x for x in self.store.inspect_operations() if x['operation_id']=='s14-judge')
        self.assertEqual(op['state'],'AMBIGUOUS')
        self.assertEqual(self.http.request.call_count,1)
        self.assertEqual(self.store.inspect_budget('s14-budget')['reserved'],self.reserve_amount)
        self.assertEqual(self.count(),0)

    def test_history_and_correction_require_latest_predecessor(self):
        historical=self.fixture.evaluate()
        first=self.submit(self.execute())
        self.assertEqual(evaluation.inspect(self.store,historical['evaluation_id']),historical)
        view=self.execute('correction')
        second=self.submit(view)
        self.assertEqual(second['previous_evaluation_id'],first['evaluation_id'])
        with self.assertRaises(storage.ConflictError): self.submit(view)
        self.assertEqual(evaluation.inspect(self.store,first['evaluation_id']),first)
        self.assertTrue(self.store.verify_storage()['integrity_ok'])


if __name__=='__main__': unittest.main()
