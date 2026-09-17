"""Final simulated HTTP bodies expose only the role's declared content"""
from base64 import b64decode
from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from benchmark.acquisition import execution
from benchmark.acquisition import campaigns as c
from benchmark import outgoing as out, storage, preparation as prep, evaluation as evaluation
from tests import test_openrouter_preparation as prep_fixture
from tests.test_openrouter_preparation import result, http_body, assistant, NOTES, KEY, NEED, REFERENCE
from tests import test_private_comparison as comparison_fixture
from tests.test_s4_regressions import response as fictional_response


class Projection(unittest.TestCase):
    def setUp(self):
        self.package=dict(instruction='Instruction utile\n',deliverables=['Livrable'],criteria=['Critère'],
            acceptable_ambiguities=['Ambiguïté'],limits=['CANARY_LIMITS'],human_work='CANARY_HUMAN',
            future_field={'nested':'CANARY_FUTURE'},pieces=[dict(id='internal-id',name='notes.txt',sha256=sha256(b'Notes\r\n').hexdigest())])
        self.pieces=[dict(id='internal-id',role='candidate',content='Notes\r\n')]

    def test_closed_candidate_and_nested_fail_closed(self):
        content=out.candidate(self.package,self.pieces)
        raw=storage._strict_json(content)
        self.assertNotIn('CANARY',raw);self.assertNotIn('internal-id',raw)
        self.assertEqual('Notes\r\n',content['pieces'][0]['content'])
        for field in ['criteria','acceptable_ambiguities','deliverables']:
            p=deepcopy(self.package);p[field]=[{'text':'useful','internal':'CANARY_NESTED'}]
            with self.assertRaises(ValueError):out.candidate(p,self.pieces)
        for field,value in [('role','judge'),('content','changed')]:
            pieces=deepcopy(self.pieces);pieces[0][field]=value
            with self.assertRaises(storage.IntegrityError):out.candidate(self.package,pieces)
        for name in ['/private/notes.txt','../notes.txt','notes\\secret','..']:
            p=deepcopy(self.package);p['pieces'][0]['name']=name
            with self.assertRaises(storage.IntegrityError):out.candidate(p,self.pieces)

    def test_preparation_internal_and_nested_content(self):
        req=dict(message='Précision',kind='correct',payload=dict(request='Demande',reformulation='Besoin',
            clarifications=['Accord'],validated_assumptions=['Hypothèse'],fictional_parameters={'Lieu':'Inventé'},state='CANARY_STATE',future='CANARY_PAYLOAD'),
            stage='CANARY_STAGE',explanation='CANARY_EXPLANATION',package=self.package,pieces_seen=self.pieces,
            reference='CANARY_JUDGE',campaigns=['CANARY_CAMPAIGN'],future='CANARY_REQUEST')
        req['payload']['validated_assumptions']=[dict(question='Question',answer='Accord',internal='CANARY_NESTED')]
        closed=out.preparation(req)
        self.assertNotIn('CANARY',storage._strict_json(closed))
        self.assertNotIn('kind',closed)
        for key,value in [('clarifications',[{'internal':'CANARY'}]),('validated_assumptions',[{'internal':'CANARY'}]),
                          ('fictional_parameters',{'location':{'internal':'CANARY'}})]:
            bad=deepcopy(req);bad['payload'][key]=value
            with self.assertRaises(ValueError):out.preparation(bad)


class PreparationHTTP(unittest.TestCase):
    def test_create_clarify_correct_final_http(self):
        f=prep_fixture.OpenRouterPreparationTests();f.setUp();self.addCleanup(f.doCleanups)
        for i,stage in enumerate(['clarification','preview','preview']):
            response=result(stage)
            if response['package']:
                response['package']['internal']['limits']=['CANARY_LIMITS']
                response['package']['internal']['human_work']='CANARY_HUMAN'
                response['package']['judgment']['pieces'][0]['content']='CANARY_JUDGE'
            f.http.getresponse.return_value.read.return_value=http_body(response)
            fields={} if i==0 else dict(action_id='step'+str(i),revision=i+1,kind='clarify' if i==1 else 'correct',message='Texte utile')
            op,view=f.execute(**fields)
            body=f.http.request.call_args.kwargs['body']
            self.assertNotIn(b'CANARY',body);self.assertNotIn(KEY.encode(),body);self.assertNotIn(b'"kind"',body)
            wire=json.loads(body);content=json.loads(wire['messages'][1]['content'])
            self.assertEqual({'request','message','reformulation','clarifications','validated_assumptions','fictional_parameters','previous_candidate'},set(content))
            self.assertNotIn('kind',content)
            if i==2:
                self.assertEqual(NOTES,content['previous_candidate']['pieces'][0]['content'])
                self.assertEqual(['notes.txt'],[p['name'] for p in content['previous_candidate']['pieces']])
            observed=op['receipt']['observed_configuration']['outgoing']
            self.assertEqual(sha256(body).hexdigest(),observed['request_body_sha256'])
            self.assertEqual(out.FORMAT,observed['outgoing_format'])


    def test_queued_preparation_from_old_prompt_never_marks_emission(self):
        f=prep_fixture.OpenRouterPreparationTests();f.setUp();self.addCleanup(f.doCleanups)
        oid=f.submit()
        other=assistant.frozen_profile()
        other['system']=other['system']+' Different format'
        changed=assistant.OpenRouterPreparation(KEY, other)
        prep.execute(f.data,oid,changed)
        op=next(x for x in f.store.inspect_operations() if x['operation_id']==oid)
        self.assertEqual('INTENT_RECORDED',op['state'])
        f.http.request.assert_not_called()


def _dump(value):
    return json.dumps(value, ensure_ascii=False)


class Spy:
    def __init__(self, inner=None):
        self.calls=[]
        self.inner=inner
    def prepare(self, *args, **kwargs):
        self.calls.append(('prepare', args, kwargs))
        if self.inner is not None:
            return self.inner.prepare(*args, **kwargs)
    def __call__(self, *args, **kwargs):
        self.calls.append(('call', args, kwargs))
        if self.inner is not None:
            return self.inner(*args, **kwargs)
        return fictional_response(*args, **kwargs)


class TransportBoundary(unittest.TestCase):
    def test_spy_adapter_receives_no_internal_markers(self):
        f=comparison_fixture.PrivateEvaluationTests();f.setUp();self.addCleanup(f.doCleanups)
        store, data = f.store, f.data
        package=f.fixture.view['package']
        judge=f.fixture.reference
        markers=[package['human_work'], *package['limits'], store.read_piece(judge).decode(),
                 *[p['id'] for p in package['pieces']], judge, str(data),
                 'budget_id', 'dossier_id', '"campaign_id"', 'INTENT_RECORDED', 'EN_ATTENTE']
        spy=Spy()
        c.reserve(store,'local-comparison','y','intent-y')
        execution.execute(data,'intent-y',spy)
        seen=_dump(spy.calls)
        for marker in markers:
            self.assertNotIn(marker, seen)
        self.assertEqual(out.FORMAT, spy.calls[0][1][1]['outgoing_format'])

    def test_candidate_http_body_matches_closed_outgoing(self):
        comparison_fixture.PiTransportTests.setUpClass()
        f=comparison_fixture.PiTransportTests();f.setUp();self.addCleanup(f.doCleanups)
        attempt=f.execute()
        raw=storage._strict_json(f.wire)
        self.assertNotIn('CANARY', raw)
        self.assertEqual(storage._strict_json(execution._transport_view(json.loads(
            f.store._connection.execute('SELECT request_json FROM s4_attempts WHERE operation_id=?',
                                        ('pi-intent',)).fetchone()[0]))['outgoing']),
                         f.wire['messages'][1]['content'])
        obs=attempt['operation']['receipt']['observed_configuration']
        self.assertEqual(sha256(raw.encode()).hexdigest(), obs['outgoing']['request_body_sha256'])
        self.assertTrue(obs['pi']['terminal'])
        with self.assertRaises(ValueError):
            f.transport.prepare({}, {'outgoing_format':'legacy'})

    def test_fictional_non_pi_transport_uses_outgoing_v1(self):
        f=comparison_fixture.PrivateEvaluationTests();f.setUp();self.addCleanup(f.doCleanups)
        seen={'format': None}
        def transport(operation, request):
            seen['format']=request['outgoing_format']
            self.assertEqual({'instruction','deliverables','criteria','acceptable_ambiguities','pieces'},
                             set(request['outgoing']))
            self.assertEqual(['notes.txt'], [p['name'] for p in request['outgoing']['pieces']])
            self.assertNotIn('package', request)
            self.assertNotIn('pieces', request)
            return fictional_response(operation, request)
        c.reserve(f.store,'local-comparison','y','intent-y')
        execution.execute(f.data,'intent-y',transport)
        self.assertEqual(out.FORMAT, seen['format'])
        self.assertEqual('RECEIVED', c.inspect(f.store,'local-comparison')['attempts'][-1]['state'])

    def test_changed_transport_engine_identity_blocks_before_transport(self):
        from tests.test_s4_regressions import S4Regressions
        f=S4Regressions();f.setUp();self.addCleanup(f.doCleanups)
        for name in ('transports/pi.py','transports/pi_bridge.mjs','transports/openrouter.py','outgoing.py','acquisition/recovery.py','acquisition/execution.py','web_api.py','validation.py'):
            self.assertIn(name, c._engine())
        f.admit(); f.reserve()
        spy=Spy()
        for name in ('transports/pi.py','web_api.py'):
            with self.subTest(source=name):
                broken=dict(c._engine()); broken[name]='0'*64
                with patch.object(c,'_engine',return_value=broken):
                    with self.assertRaises(storage.ConflictError):
                        execution.execute(f.data,'intent-x',spy)
        self.assertEqual([], spy.calls)

    def test_body_change_between_prepare_and_emit_blocks_before_network(self):
        comparison_fixture.PiTransportTests.setUpClass()
        f=comparison_fixture.PiTransportTests();f.setUp();self.addCleanup(f.doCleanups)
        original=f.transport.prepare
        def prepare(operation, request):
            original(operation, request)
            f.transport._wire_bytes = f.transport._wire_bytes.replace('fixture', 'mutated')
        with patch.object(f.transport,'prepare',side_effect=prepare), patch.object(comparison_fixture.pi.http,'post') as http:
            execution.execute(f.data,'pi-intent',f.transport)
            http.assert_not_called()
        self.assertEqual('AMBIGUOUS', c.inspect(f.store,'pi-offline')['attempts'][0]['state'])


class GenerationStructure(unittest.TestCase):
    def test_structured_preview_persists_candidate_internal_and_judgment(self):
        f=prep_fixture.OpenRouterPreparationTests();f.setUp();self.addCleanup(f.doCleanups)
        op,view=f.execute()
        self.assertEqual('preview',view['stage'])
        self.assertEqual('Relire et confirmer les inconnues',view['package']['human_work'])
        self.assertEqual(['Exercice fictif unique, sans action externe'],view['package']['limits'])
        self.assertEqual(NOTES.encode(),f.store.read_piece(view['package']['pieces'][0]['id']))
        judge=f.store._connection.execute("SELECT piece_id FROM pieces WHERE role='judge'").fetchone()[0]
        self.assertEqual(REFERENCE.encode(),f.store.read_piece(judge))
        self.assertEqual(NOTES,op['receipt']['result']['package']['candidate']['pieces'][0]['content'])
        self.assertNotIn('role',storage._strict_json(op['receipt']['result']['package']))

    def test_mixed_role_or_extra_key_refused_before_persistence(self):
        f=prep_fixture.OpenRouterPreparationTests();f.setUp();self.addCleanup(f.doCleanups)
        for defect in ('role','extra'):
            with self.subTest(defect=defect):
                prep.admit(f.store,f.authority)
                broken=result()
                if defect=='role':
                    broken['package']['candidate']['pieces'][0]['role']='judge'
                else:
                    broken['package']['future']='CANARY'
                f.http.getresponse.return_value.read.return_value=http_body(broken)
                dossier='mix-'+defect
                oid,_=prep.submit(f.store,f.session,dossier,dict(action_id='create',request=NEED),'a'*40,f.transport)
                prep.execute(f.data,oid,f.transport)
                view=prep.view(f.store,f.session,dossier)
                op=next(row for row in f.store.inspect_operations() if row['operation_id']==oid)
                self.assertEqual('suspended',view['stage'])
                self.assertIsNone(view['package'])
                self.assertEqual([],f.store.verify_storage()['orphan_files'])
                self.assertEqual(http_body(broken),b64decode(op['receipt']['observed_configuration']['http']['body_base64']))


class ReviewProjection(unittest.TestCase):
    def test_local_export_separate_from_review(self):
        f=comparison_fixture.PrivateEvaluationTests();f.setUp();self.addCleanup(f.doCleanups)
        before=f.store.inspect_operations()
        local=evaluation.prepare_report(f.store,'local-comparison','intent-x')
        review=evaluation.prepare_review(f.store,'local-comparison','intent-x')
        self.assertEqual(before,f.store.inspect_operations())
        self.assertIn('attempt',local);self.assertNotIn('attempt',review['content'])
        self.assertEqual({'task','result_expected','obligations','eliminatory_errors','method','secondary_criteria','limits','output','references'},set(review['content']))
        raw=storage._strict_json(review['content'])
        self.assertNotIn('requested_configuration',raw)
        self.assertNotIn('budget_id',raw)
        self.assertNotIn('campaign_id',raw)
        self.assertNotIn('session',raw)
        self.assertNotIn(str(f.data),raw)
        self.assertNotIn('candidate_pieces',raw)
        package=f.fixture.view['package']
        self.assertNotIn(package['human_work'],raw)
        for limit in package['limits']:
            self.assertNotIn(limit,raw)
        self.assertEqual(package['instruction'],review['content']['task']['instruction'])
        self.assertEqual(package['deliverables'],review['content']['task']['deliverables'])
        self.assertEqual(out.criteria(package['criteria']), review['content']['task']['criteria'])
        self.assertEqual(package['acceptable_ambiguities'],review['content']['task']['acceptable_ambiguities'])
        self.assertEqual(['Contrôle local fictif des notes seulement'],review['content']['limits'])
        self.assertEqual('local-comparison',review['binding']['campaign_id'])
        self.assertEqual('intent-x',review['binding']['attempt_id'])
        with self.assertRaises((KeyError,ValueError)):
            evaluation.prepare_review(f.store,'foreign','intent-x')

    def test_submit_report_from_prepare_review_binding_and_local_authority(self):
        f=comparison_fixture.PrivateEvaluationTests();f.setUp();self.addCleanup(f.doCleanups)
        review=evaluation.prepare_review(f.store,'local-comparison','intent-x')
        content=review['content']; binding=review['binding']
        package=f.fixture.view['package']
        self.assertEqual(package['instruction'],content['task']['instruction'])
        self.assertEqual(package['deliverables'],content['task']['deliverables'])
        self.assertEqual(out.criteria(package['criteria']), content['task']['criteria'])
        self.assertEqual(package['acceptable_ambiguities'],content['task']['acceptable_ambiguities'])
        attempt=c.inspect(f.store,'local-comparison')['attempts'][0]
        self.assertEqual(attempt['output_piece_id'],content['output']['piece_id'])
        seen=[p['piece_id'] for p in content['task']['pieces']]
        seen.append(content['output']['piece_id'])
        seen.extend(p['piece_id'] for p in content['references'])
        passage='fictional raw output'
        self.assertIn(passage,content['output']['content'])
        output_proof=dict(piece_id=content['output']['piece_id'],sha256=content['output']['sha256'],passage=passage)
        findings=[]
        for item in content['obligations']:
            for control in item['control_ids']:
                findings.append(dict(criterion_id=item['id'],control_id=control,status='PASS',attribution='candidate',
                                     finding='Constat sur la sortie candidate',evidence=[output_proof]))
        for item in content['eliminatory_errors']:
            for control in item['control_ids']:
                findings.append(dict(criterion_id=item['id'],control_id=control,status='PASS',attribution='evidence',
                                     finding='Contrôle éliminatoire sur la sortie',evidence=[output_proof]))
        report=dict(findings=findings,measures=[],
                    judgment=dict(mode='human',instructions=content['method']['expected_evidence'],
                                  resources_seen=seen,assistance_operation_id=None,model_links='INCONNU',
                                  disagreements=[],professional_review='ABSENTE'),
                    limits=list(content['limits']))
        request=dict(campaign_id=binding['campaign_id'],attempt_id=binding['attempt_id'],
                     previous_evaluation_id=binding['previous_evaluation_id'],
                     responsible='Opérateur du test logiciel',
                     authority=dict(actor='Ayo',authority_id='offline-review-from-closed'),
                     report=report)
        broken_hash=deepcopy(request)
        broken_hash['report']['findings'][0]['evidence'][0]['sha256']='0'*64
        with self.assertRaises(storage.IntegrityError):
            evaluation.submit_report(f.store,broken_hash)
        broken_id=deepcopy(request)
        broken_id['report']['findings'][0]['evidence'][0]['piece_id']='foreign-output'
        with self.assertRaises((ValueError,storage.IntegrityError)):
            evaluation.submit_report(f.store,broken_id)
        record=evaluation.submit_report(f.store,request)
        self.assertEqual('SATISFAIT',record['verdict'])
        self.assertEqual(record,evaluation.inspect(f.store,record['evaluation_id']))


if __name__=='__main__':unittest.main()
