"""Technical recovery preserves the task, money and every earlier receipt"""
from base64 import b64encode
from contextlib import closing, contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import threading
import unittest

from benchmark_lab_x import campaigns as c, qualification as q, recovery as r, storage
from tests.test_s3_regressions import fixture, specification, check, ACTOR, AUTHORITY
from tests.test_s4_regressions import manifest, inputs, response


class Recovery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name).resolve() / 'private'
        self.sid, view, ref = fixture(self.data)
        q.initialize(self.data); c.initialize(self.data)
        self.store = storage.Store(self.data); self.addCleanup(self.store.close)
        spec = specification(ref); spec['cost_basis']['unit'] = 'USD'
        draft = q.draft(self.store, 'fixture', view['revision'], spec)
        qualified = q.qualify(self.store, draft['contract_sha256'], reviewer=ACTOR, check=check)
        q.approve(self.store, draft['contract_sha256'], qualified['qualification_id'], actor=ACTOR, authority=AUTHORITY)
        m = manifest(draft); m['financial_cost_policy'] = 'retain_reserve'
        m['conditions']['defaults'] = {'context_window': 10000}
        for p in m['panel']:
            p.update(model='fixture/'+p['id'], revision='rev-'+p['id'],
                     parameters={'max_tokens':100,'provider':{'only':['one','two'],'order':['one','two'],
                     'allow_fallbacks':True,'require_parameters':True}})
        self.store.create_budget('local-comparison','40','USD')
        c.create(self.store,m)
        self.admit('local-comparison')
        self.caps = dict(id='fixture/x', endpoints=[dict(tag=t,status=0,max_completion_tokens=1000,
             context_length=10000,supported_parameters=['max_tokens'],
             pricing=dict(prompt='0.000001',completion='0.000002')) for t in ['one','two']])

    def admit(self, cid, grant=False, owner=False, capabilities=None):
        snap=c.inspect(self.store,cid)
        a,e=inputs(snap,cells=[p['cell_id'] for p in snap['manifest']['plan']])
        a['reserve_amounts']={k:v for k,v in a['reserve_amounts'].items() if k in a['allowed_cells']}
        if grant:
            a['technical_recovery']=dict(capabilities=deepcopy(capabilities if capabilities is not None else self.caps))
        return c.admit(self.store,cid,a,e,owner_launch=owner)

    def granted(self, name='preauthorized', capabilities=None, only=None):
        m=deepcopy(c.inspect(self.store,'local-comparison')['manifest'])
        m['campaign_id']=name
        if only is not None:
            for panel in m['panel']:
                panel['parameters']['provider'].update(only=list(only),order=list(only))
        c.create(self.store,m)
        caps=deepcopy(capabilities if capabilities is not None else self.caps)
        if only is not None:
            caps['endpoints']=[e for e in caps['endpoints'] if e['tag'] in only]
        self.admit(name,grant=True,capabilities=caps)
        return name

    def build_response(self, op, request, finish='length', output='', unknown=False,
                       status=200, incident=None, native=None, refusal=None, route='one', terminal=None):
        x=response(op,request);x['cost']['currency']='USD'
        if unknown: x['cost'].update(status='UNKNOWN',amount=None,source='Coût financier absent ; réserve conservée')
        if incident is None:
            x['receipt']['result'].update(output=output,incident=None if finish=='stop' else 'PROVIDER_RESPONSE_INCOMPLETE')
        else:
            x['receipt']['result'].update(output=output,incident=incident)
        message=dict(content=output)
        if refusal is not None: message['refusal']=refusal
        choice=dict(finish_reason=finish,message=message)
        if native is not None: choice['native_finish_reason']=native
        raw=json.dumps(dict(choices=[choice],usage=dict(prompt_tokens=50))).encode()
        observed=x['receipt']['observed_configuration']
        observed.update(provider='One',route=route,http=dict(status=status,complete=True,
              credential_redacted=False,body_base64=b64encode(raw).decode(),body_sha256=sha256(raw).hexdigest(),received_at='2026-09-11T00:00:00Z'))
        if terminal is not None: observed['pi']=dict(terminal=terminal)
        return x

    def sequence(self, *steps):
        calls=[]
        def transport(op,request):
            spec=steps[len(calls)] if len(calls)<len(steps) else steps[-1]
            calls.append(dict(operation_id=op['operation_id'],outgoing=request.get('outgoing'),
                              request=deepcopy(request['requested_configuration'])))
            return self.build_response(op,request,**spec)
        return transport,calls

    def ident(self, config=None, **changes):
        panel=config or c.inspect(self.store,'local-comparison')['manifest']['panel'][0]
        value=dict(provider=panel['provider'],model=panel['model'],revision=panel['revision'],
                   access=panel['access'],channel_id=panel['channel_id'],outgoing_format=r.outgoing.FORMAT)
        value.update(changes)
        return value

    def emit(self, cid='local-comparison', cell='x', oid='first', finish='length', output='', unknown=False,
             status=200, incident=None, native=None, refusal=None, route='one', terminal=None):
        c.reserve(self.store,cid,cell,oid)
        c.execute(self.data,oid,lambda op,request: self.build_response(
            op,request,finish=finish,output=output,unknown=unknown,status=status,
            incident=incident,native=native,refusal=refusal,route=route,terminal=terminal))

    def test_length_link_idempotence_profile_and_unchanged_source(self):
        self.emit()
        before=c.inspect(self.store,'local-comparison')
        proposal=r.propose(self.store,'first',self.caps)
        self.assertEqual(200,proposal['manifest']['panel'][0]['parameters']['max_tokens'])
        self.assertIsNone(r.profile(self.store,self.ident()))
        with self.assertRaises(ValueError):
            r.profile(self.store,dict(model='fixture/x'))
        cid=proposal['manifest']['campaign_id'];c.create(self.store,proposal['manifest'])
        with self.assertRaises(storage.ConflictError):c.create(self.store,proposal['manifest'])
        self.admit(cid);self.emit(cid=cid,oid='second',finish='stop',output='Complete')
        reqs=[json.loads(self.store._connection.execute('SELECT request_json FROM s4_attempts WHERE operation_id=?',(oid,)).fetchone()[0]) for oid in ['first','second']]
        self.assertEqual(reqs[0]['outgoing'],reqs[1]['outgoing'])
        self.assertNotEqual(reqs[0]['requested_configuration']['parameters']['max_tokens'],
                            reqs[1]['requested_configuration']['parameters']['max_tokens'])
        after=c.inspect(self.store,'local-comparison')
        self.assertEqual({k:v for k,v in before.items() if k != 'budget'},
                         {k:v for k,v in after.items() if k != 'budget'})
        with closing(storage.Store(self.data)) as reopened:
            profile=r.profile(reopened,self.ident())
            self.assertEqual('second',profile['operation_id'])
            self.assertEqual(200,profile['parameters']['max_tokens'])
            self.assertEqual('rev-x',profile['revision'])
            self.assertNotEqual(profile['model'],profile['revision'])
            self.assertIsNone(r.profile(reopened,self.ident(revision='other-rev')))
            self.assertIsNone(r.profile(reopened,self.ident(provider='other-provider')))
            self.assertIsNone(r.profile(reopened,self.ident(access='API')))
            self.assertIsNone(r.profile(reopened,self.ident(channel_id='other-channel')))
            dumped=storage._strict_json(profile)
            self.assertNotIn('Complete',dumped)
            self.assertNotIn('body_base64',dumped)
            self.assertNotIn('choices',dumped)
            next_config=r.starting_configuration(reopened,before['manifest']['panel'][0],content_format=r.outgoing.FORMAT)
            self.assertEqual(200,next_config['configuration']['parameters']['max_tokens'])
            self.assertEqual('second',next_config['profile']['operation_id'])
            self.assertIsNone(r.starting_configuration(reopened,before['manifest']['panel'][0])['profile'])
        with self.assertRaises(storage.ConflictError):c.reserve(self.store,cid,'x','third')

    def test_refusal_no_progress_and_limit_stop(self):
        self.emit(finish='content_filter')
        with self.assertRaises(ValueError):r.propose(self.store,'first',self.caps)
        self.emit(cell='y',oid='refusal',finish='content_filter')
        self.assertIsNone(r.profile(self.store,self.ident()))

    def test_no_progress_stops_after_larger_allocation(self):
        self.emit()
        proposal=r.propose(self.store,'first',self.caps);cid=proposal['manifest']['campaign_id']
        c.create(self.store,proposal['manifest']);self.admit(cid);self.emit(cid=cid,oid='second')
        with self.assertRaisesRegex(ValueError,'progression'):r.propose(self.store,'second',self.caps)

    def test_budget_retains_unknown_sibling_and_limits(self):
        self.emit();self.emit(cell='y',oid='unknown',finish='content_filter',unknown=True)
        proposal=r.propose(self.store,'first',self.caps)
        cid=proposal['manifest']['campaign_id'];c.create(self.store,proposal['manifest']);self.admit(cid)
        self.emit(cid=cid,oid='second',finish='stop',output='complete')
        self.assertEqual('7',self.store.inspect_budget('local-comparison')['reserved'])
        caps=deepcopy(self.caps)
        for e in caps['endpoints']:e['max_completion_tokens']=100
        with self.assertRaisesRegex(ValueError,'Limite'):r.propose(self.store,'first',caps)
        for e in caps['endpoints']:e['max_completion_tokens']=1000;e['pricing']['completion']='100'
        with self.assertRaises(storage.BudgetError):r.propose(self.store,'first',caps)

    def test_route_recovery_stays_within_original_endpoints(self):
        self.emit(status=503,finish=None)
        proposal=r.propose(self.store,'first',self.caps)
        self.assertEqual(['two'],proposal['manifest']['panel'][0]['parameters']['provider']['only'])
        bad=deepcopy(proposal['manifest']);bad['panel'][0]['parameters']['provider']['only']=['foreign']
        with self.assertRaises(ValueError):c.create(self.store,bad)

    def test_route_error_without_usage_has_conservative_reserve_and_diagnostic(self):
        self.emit(status=503, finish=None)
        snapshot, attempt = r.parent(self.store, self.store._connection, 'first')
        observed = r.observation(attempt)
        observed['usage'] = {}
        proposal = r.derive_child(snapshot['manifest'], attempt, observed, self.caps, budget_id='local-comparison')
        self.assertEqual(['two'], proposal['manifest']['panel'][0]['parameters']['provider']['only'])
        self.assertEqual('0.010200', proposal['reserve_amount'])
        self.assertEqual({}, observed['usage'])
        before = self.store.inspect_operations()
        diagnostic = r.diagnose(self.store, 'first')
        self.assertEqual('ROUTE_ERROR', diagnostic['kind'])
        self.assertFalse(diagnostic['automatic'])
        self.assertEqual(before, self.store.inspect_operations())
        observed['route'] = None
        with self.assertRaisesRegex(ValueError, 'Endpoint fautif non attribué'):
            r.derive_child(snapshot['manifest'], attempt, observed, self.caps, budget_id='local-comparison')

    def test_route_error_then_success_keeps_incident_and_learned_route(self):
        source=c.inspect(self.store,'local-comparison')['manifest']['panel'][0]
        self.emit(status=503,finish=None)
        proposal=r.propose(self.store,'first',self.caps)
        self.assertEqual(['two'],proposal['manifest']['panel'][0]['parameters']['provider']['only'])
        cid=proposal['manifest']['campaign_id'];c.create(self.store,proposal['manifest']);self.admit(cid)
        self.emit(cid=cid,oid='second',finish='stop',output='Complete',route='two')
        reqs=[json.loads(self.store._connection.execute('SELECT request_json FROM s4_attempts WHERE operation_id=?',(oid,)).fetchone()[0]) for oid in ['first','second']]
        self.assertEqual(reqs[0]['outgoing'],reqs[1]['outgoing'])
        learned=r.starting_configuration(self.store,source,content_format=r.outgoing.FORMAT)
        self.assertEqual(['two'],learned['configuration']['parameters']['provider']['only'])
        self.assertEqual(proposal['manifest']['panel'][0]['route'],learned['configuration']['route'])
        kinds=[step['kind'] for step in learned['profile']['recovery_chain']]
        self.assertEqual(['ROUTE_ERROR','COMPLETE'],kinds)

    def test_empty_output_proposes_remaining_endpoint(self):
        self.emit(finish='stop',output='')
        proposal=r.propose(self.store,'first',self.caps)
        self.assertEqual(['two'],proposal['manifest']['panel'][0]['parameters']['provider']['only'])
        self.assertEqual(100,proposal['manifest']['panel'][0]['parameters']['max_tokens'])

    def test_each_terminal_or_unrecoverable_case_refuses_proposal(self):
        for fields in [dict(finish='content_filter'),
                       dict(finish='stop',output='x',native='refusal'),
                       dict(finish='stop',output='x',refusal='refused'),
                       dict(finish='stop',output='ok',incident='HARNESS_ERROR'),
                       dict(finish='stop',output='ok',incident='MODEL_IDENTITY_MISMATCH'),
                       dict(status=503,finish=None,route=None),
                       dict(finish='stop',output='ok',terminal=False)]:
            with self.subTest(fields=fields), tempfile.TemporaryDirectory() as tmp:
                data=Path(tmp).resolve()/'private'
                sid,view,ref=fixture(data)
                q.initialize(data);c.initialize(data)
                with closing(storage.Store(data)) as store:
                    spec=specification(ref);spec['cost_basis']['unit']='USD'
                    draft=q.draft(store,'fixture',view['revision'],spec)
                    qualified=q.qualify(store,draft['contract_sha256'],reviewer=ACTOR,check=check)
                    q.approve(store,draft['contract_sha256'],qualified['qualification_id'],actor=ACTOR,authority=AUTHORITY)
                    m=manifest(draft);m['financial_cost_policy']='retain_reserve'
                    m['conditions']['defaults']={'context_window':10000}
                    for p in m['panel']:
                        p.update(model='fixture/'+p['id'],revision='rev-'+p['id'],
                                 parameters={'max_tokens':100,'provider':{'only':['one','two'],'order':['one','two'],
                                 'allow_fallbacks':True,'require_parameters':True}})
                    store.create_budget('local-comparison','40','USD')
                    c.create(store,m)
                    snap=c.inspect(store,'local-comparison')
                    a,e=inputs(snap,cells=[p['cell_id'] for p in snap['manifest']['plan']])
                    a['reserve_amounts']={k:v for k,v in a['reserve_amounts'].items() if k in a['allowed_cells']}
                    c.admit(store,'local-comparison',a,e)
                    previous=self.store; previous_data=self.data
                    self.store=store; self.data=data
                    try:
                        self.emit(**fields)
                        with self.assertRaises(ValueError):
                            r.propose(store,'first',self.caps)
                    finally:
                        self.store=previous; self.data=previous_data

    def test_unknown_cost_profile_keeps_reserve_and_source(self):
        self.emit(finish='stop',output='Complete',unknown=True)
        profile=r.profile(self.store,self.ident())
        self.assertEqual('UNKNOWN',profile['cost']['status'])
        self.assertIsNone(profile['cost']['amount'])
        self.assertIn('réserve',profile['cost']['source'])
        self.assertEqual('7',self.store.inspect_budget('local-comparison')['reserved'])
        dumped=storage._strict_json(profile)
        self.assertNotIn('Complete',dumped)
        self.assertNotIn('body_base64',dumped)

    def _requests(self, *oids):
        return [json.loads(self.store._connection.execute(
            'SELECT request_json FROM s4_attempts WHERE operation_id=?',(oid,)).fetchone()[0]) for oid in oids]

    def _recovery(self, source):
        return next(s for s in c.list_campaigns(self.store) if s['manifest'].get('recovery_of')==source)

    def derived_authority(self, child_cid, owner_cid, source_oid, reserve=None):
        child=c.inspect(self.store,child_cid)
        owner=next(a for a in c.inspect(self.store,owner_cid)['admissions'] if a['authority']['purpose']=='start')
        a,e=inputs(child,cells=[p['cell_id'] for p in child['manifest']['plan']])
        a['reserve_amounts']={k:v for k,v in a['reserve_amounts'].items() if k in a['allowed_cells']}
        if reserve is not None:
            a['reserve_amounts']={child['manifest']['plan'][0]['cell_id']:reserve}
        for key in ('actor','authority_id','execution_authority','candidate_authority','budget_authority','budget_id'):
            a[key]=owner['authority'][key]
        a['derived_from']=dict(admission_id=owner['admission_id'],operation_id=source_oid,
                               manifest_sha256=owner['authority']['manifest_sha256'])
        return a,e

    def forge_recovery(self, source_oid, mutate):
        proposal=r.propose(self.store,source_oid,self.caps)
        forged=deepcopy(proposal['manifest'])
        mutate(forged)
        c.create(self.store,forged)
        return proposal,forged

    def test_preauthorized_length_auto_completes_same_messages(self):
        cid=self.granted()
        owner=c.inspect(self.store,cid)
        c.reserve(self.store,cid,'x','first')
        transport,calls=self.sequence(dict(finish='length',output='partial'),
                                      dict(finish='stop',output='Complete'))
        c.execute(self.data,'first',transport)
        self.assertEqual(2,len(calls))
        self.assertEqual(calls[0]['outgoing'],calls[1]['outgoing'])
        self.assertEqual(100,calls[0]['request']['parameters']['max_tokens'])
        self.assertEqual(200,calls[1]['request']['parameters']['max_tokens'])
        recovered=self._recovery('first')
        self.assertEqual('first',recovered['manifest']['recovery_of'])
        reqs=self._requests('first',calls[1]['operation_id'])
        self.assertEqual(reqs[0]['outgoing'],reqs[1]['outgoing'])
        self.assertNotEqual(reqs[0]['requested_configuration']['parameters']['max_tokens'],
                            reqs[1]['requested_configuration']['parameters']['max_tokens'])
        self.assertEqual(2,len({a['operation_id'] for s in c.list_campaigns(self.store) for a in s['attempts']}))
        derived=recovered['admissions'][0]['authority']
        self.assertEqual(owner['admissions'][0]['admission_id'],derived['derived_from']['admission_id'])
        self.assertEqual(owner['manifest_sha256'],derived['derived_from']['manifest_sha256'])
        self.assertNotEqual(derived['manifest_sha256'],derived['derived_from']['manifest_sha256'])
        self.assertNotIn('technical_recovery',derived)
        self.assertEqual(owner['admissions'][0]['authority']['authority_id'],derived['authority_id'])
        profile=r.profile(self.store,self.ident(recovered['manifest']['panel'][0]))
        self.assertEqual(calls[1]['operation_id'],profile['operation_id'])
        self.assertEqual(200,profile['parameters']['max_tokens'])
        self.assertIsNone(r.profile(self.store,self.ident(recovered['manifest']['panel'][0],revision='other-rev')))

    def test_preauthorized_route_error_excludes_faulty_route(self):
        cid=self.granted()
        c.reserve(self.store,cid,'x','first')
        transport,calls=self.sequence(dict(status=503,finish=None,route='one'),
                                      dict(finish='stop',output='Complete',route='two'))
        c.execute(self.data,'first',transport)
        self.assertEqual(2,len(calls))
        self.assertEqual(['one','two'],calls[0]['request']['parameters']['provider']['only'])
        self.assertEqual(['two'],calls[1]['request']['parameters']['provider']['only'])
        self.assertEqual(calls[0]['outgoing'],calls[1]['outgoing'])
        recovered=self._recovery('first')
        kinds=[step['kind'] for step in r.profile(self.store,self.ident(recovered['manifest']['panel'][0]))['recovery_chain']]
        self.assertEqual(['ROUTE_ERROR','COMPLETE'],kinds)
        self.assertEqual('first',recovered['manifest']['recovery_of'])
        self.assertEqual(calls[1]['operation_id'],recovered['attempts'][0]['operation_id'])
        self.assertEqual('KNOWN',recovered['attempts'][0]['operation']['observed_cost']['status'])

    def test_preauthorized_empty_output_excludes_faulty_route(self):
        cid=self.granted('empty-output')
        c.reserve(self.store,cid,'x','empty')
        transport,calls=self.sequence(dict(finish='stop',output='',route='one'),
                                      dict(finish='stop',output='Complete',route='two'))
        c.execute(self.data,'empty',transport)
        self.assertEqual(2,len(calls))
        self.assertEqual(['two'],calls[1]['request']['parameters']['provider']['only'])
        self.assertEqual(100,calls[1]['request']['parameters']['max_tokens'])
        self.assertEqual(calls[0]['outgoing'],calls[1]['outgoing'])
        self.assertEqual('empty',self._recovery('empty')['manifest']['recovery_of'])

    def test_without_preauthorization_propose_does_not_launch(self):
        before=len(c.list_campaigns(self.store))
        self.emit()
        self.assertEqual(before,len(c.list_campaigns(self.store)))
        self.assertEqual(['first'],[a['operation_id'] for a in c.inspect(self.store,'local-comparison')['attempts']])
        proposal=r.propose(self.store,'first',self.caps)
        self.assertEqual(200,proposal['manifest']['panel'][0]['parameters']['max_tokens'])
        self.assertEqual(before,len(c.list_campaigns(self.store)))

    def test_preauthorized_stops_without_new_emission(self):
        cases=[
            dict(fields=dict(finish='content_filter'),calls=1),
            dict(fields=dict(finish='stop',output='ok',incident='HARNESS_ERROR'),calls=1),
            dict(fields=dict(finish='stop',output='ok',incident='MODEL_IDENTITY_MISMATCH'),calls=1),
            dict(fields=dict(status=503,finish=None,route=None),calls=1),
            dict(fields=dict(finish='stop',output='ok',terminal=False),calls=1),
            dict(fields=dict(finish='length',output='same'),second=dict(finish='length',output='same'),calls=2),
            dict(only=['one'],fields=dict(status=503,finish=None,route='one'),calls=1),
            dict(pricing='100',fields=dict(finish='length',output='partial'),calls=1),
        ]
        for i,case in enumerate(cases):
            with self.subTest(case=case):
                caps=deepcopy(self.caps)
                if 'pricing' in case:
                    for endpoint in caps['endpoints']:
                        endpoint['pricing']['completion']=case['pricing']
                name='stop-'+str(i)
                cid=self.granted(name,capabilities=caps,only=case.get('only'))
                c.reserve(self.store,cid,'x','first-'+name)
                steps=[case['fields']]
                if 'second' in case:
                    steps.append(case['second'])
                transport,calls=self.sequence(*steps)
                c.execute(self.data,'first-'+name,transport)
                self.assertEqual(case['calls'],len(calls))
                if case['calls']==1:
                    self.assertIsNone(next((s for s in c.list_campaigns(self.store)
                                            if s['manifest'].get('recovery_of')=='first-'+name and s['attempts']),None))

    def test_preauthorized_ambiguous_receipt_does_not_emit_again(self):
        cid=self.granted('ambiguous')
        c.reserve(self.store,cid,'x','first')
        calls=[]
        def broken(op,request):
            calls.append(op['operation_id'])
            raise OSError('fixture interruption')
        c.execute(self.data,'first',broken)
        self.assertEqual(1,len(calls))
        self.assertEqual('AMBIGUOUS',c.inspect(self.store,cid)['attempts'][0]['state'])
        self.assertIsNone(next((s for s in c.list_campaigns(self.store) if s['manifest'].get('recovery_of')=='first'),None))

    def test_process_repeat_and_second_click_do_not_reemit(self):
        m=deepcopy(c.inspect(self.store,'local-comparison')['manifest'])
        m['campaign_id']='clickable'
        c.create(self.store,m)
        record=self.admit('clickable',grant=True,owner=True)
        body=dict(manifest_sha256=c.inspect(self.store,'clickable')['manifest_sha256'],
                  admission_id=record['admission_id'],confirm='yes')
        attempts=c.launch(self.store,self.sid,'fixture','clickable',body)
        self.assertTrue(attempts)
        transport,calls=self.sequence(dict(finish='length',output='partial'),
                                      dict(finish='stop',output='Complete'),
                                      dict(finish='stop',output='Complete'))
        c.execute_launch(self.data,attempts,transport)
        emitted=len(calls)
        self.assertGreaterEqual(emitted,2)
        self.assertEqual([],c.launch(self.store,self.sid,'fixture','clickable',body))
        with self.assertRaises(storage.ConflictError):
            c.execute(self.data,attempts[0],transport)
        self.assertEqual(emitted,len(calls))
        r.continue_preauthorized(self.data,attempts[0],transport)
        self.assertEqual(emitted,len(calls))

    def test_preauthorized_outgoing_boundary_and_frozen_capabilities(self):
        cid=self.granted('boundary')
        frozen=deepcopy(self.caps)
        self.caps['endpoints']=[]
        c.reserve(self.store,cid,'x','first')
        inner,calls=self.sequence(dict(finish='length',output='partial'),
                                  dict(finish='stop',output='Complete'))
        seen=[]
        def transport(op,request):
            seen.append(deepcopy(request))
            return inner(op,request)
        c.execute(self.data,'first',transport)
        self.assertEqual(2,len(calls))
        dumped=json.dumps(seen,ensure_ascii=False)
        self.assertNotIn('technical_recovery',dumped)
        self.assertNotIn('derived_from',dumped)
        self.assertNotIn('budget_id',dumped)
        self.assertNotIn('"campaign_id"',dumped)
        self.assertNotIn(str(self.data),dumped)
        self.assertNotIn('TEST_ONLY',dumped)
        self.assertNotIn('reference_piece',dumped)
        self.assertEqual(calls[0]['outgoing'],calls[1]['outgoing'])
        profile=r.profile(self.store,self.ident(c.inspect(self.store,cid)['manifest']['panel'][0]))
        self.assertEqual('COMPLETE',profile['recovery_chain'][-1]['kind'])
        self.assertEqual(frozen['endpoints'][0]['tag'],calls[1]['request']['parameters']['provider']['only'][0])

    def test_derived_admission_rejects_unapproved_max_tokens_and_effort(self):
        cid=self.granted('forged-tokens')
        self.emit(cid=cid,oid='first')
        def mutate(manifest):
            manifest['campaign_id']='forged-tokens-child'
            manifest['panel'][0]['parameters']['max_tokens']=999999
            manifest['panel'][0]['effort']='unapproved-effort'
        proposal,forged=self.forge_recovery('first',mutate)
        self.assertEqual(999999,forged['panel'][0]['parameters']['max_tokens'])
        self.assertEqual('unapproved-effort',forged['panel'][0]['effort'])
        self.assertNotEqual(proposal['manifest']['panel'][0]['parameters']['max_tokens'],999999)
        a,e=self.derived_authority('forged-tokens-child',cid,'first',reserve=proposal['reserve_amount'])
        with self.assertRaises((ValueError,storage.IntegrityError)):
            c.admit(self.store,'forged-tokens-child',a,e)
        self.assertEqual([],c.inspect(self.store,'forged-tokens-child')['attempts'])
        self.assertEqual([],c.inspect(self.store,'forged-tokens-child')['admissions'])

    def test_derived_admission_rejects_unapproved_route_and_endpoints(self):
        cid=self.granted('forged-route')
        self.emit(cid=cid,oid='first')
        def mutate(manifest):
            manifest['campaign_id']='forged-route-child'
            manifest['panel'][0]['parameters']['provider'].update(only=['one'],order=['one'])
            manifest['panel'][0]['route']='OpenRouter ordered endpoints: one'
        proposal,forged=self.forge_recovery('first',mutate)
        self.assertEqual(['one'],forged['panel'][0]['parameters']['provider']['only'])
        self.assertNotEqual(proposal['manifest']['panel'][0]['route'],forged['panel'][0]['route'])
        a,e=self.derived_authority('forged-route-child',cid,'first',reserve=proposal['reserve_amount'])
        with self.assertRaises((ValueError,storage.IntegrityError)):
            c.admit(self.store,'forged-route-child',a,e)
        self.assertEqual([],c.inspect(self.store,'forged-route-child')['attempts'])

    def test_exact_derived_manifest_is_admitted_and_executed(self):
        cid=self.granted('exact-derived')
        c.reserve(self.store,cid,'x','first')
        transport,calls=self.sequence(dict(finish='length',output='partial'),
                                      dict(finish='stop',output='Complete'))
        c.execute(self.data,'first',transport)
        proposal=r.propose(self.store,'first',self.caps)
        recovered=self._recovery('first')
        self.assertEqual(q.digest(proposal['manifest']),q.digest(recovered['manifest']))
        self.assertEqual(proposal['reserve_amount'],recovered['admissions'][0]['authority']['reserve_amounts']['x'])
        self.assertEqual(proposal['budget_id'],recovered['admissions'][0]['authority']['budget_id'])
        self.assertEqual(2,len(calls))
        self.assertEqual('RECEIVED',recovered['attempts'][0]['state'])
        self.assertEqual(proposal['manifest']['panel'][0]['parameters']['max_tokens'],
                         recovered['attempts'][0]['operation']['requested_configuration']['parameters']['max_tokens'])

    def test_operator_stop_during_callback_keeps_receipt_without_child(self):
        cid=self.granted('stopped-during')
        c.reserve(self.store,cid,'x','first')
        calls=[]
        def transport(op,request):
            calls.append(op['operation_id'])
            c.stop(self.store,cid)
            return self.build_response(op,request,finish='length',output='partial')
        c.execute(self.data,'first',transport)
        self.assertEqual(1,len(calls))
        snap=c.inspect(self.store,cid)
        self.assertEqual('STOPPED',snap['state'])
        self.assertIsNone(snap['admission'])
        self.assertEqual('RECEIVED',snap['attempts'][0]['state'])
        self.assertEqual('partial',snap['attempts'][0]['operation']['receipt']['result']['output'])
        self.assertIsNone(next((s for s in c.list_campaigns(self.store) if s['manifest'].get('recovery_of')=='first'),None))

    def test_admission_close_during_callback_shares_stop_path(self):
        cid=self.granted('closed-during')
        c.reserve(self.store,cid,'x','first')
        calls=[]
        def transport(op,request):
            calls.append(op['operation_id'])
            c.close_admission(self.store,'MAINTENANCE')
            return self.build_response(op,request,finish='length',output='partial')
        c.execute(self.data,'first',transport)
        self.assertEqual(1,len(calls))
        snap=c.inspect(self.store,cid)
        self.assertIsNone(snap['admission'])
        self.assertEqual('RECEIVED',snap['attempts'][0]['state'])
        self.assertIsNone(next((s for s in c.list_campaigns(self.store) if s['manifest'].get('recovery_of')=='first'),None))

    def _suspend_continue(self):
        original=r.continue_preauthorized
        r.continue_preauthorized=lambda *a,**k: None
        return original

    def _restore_continue(self, original):
        r.continue_preauthorized=original

    def _received_source(self, name, **spec):
        cid=self.granted(name)
        c.reserve(self.store,cid,'x','first')
        transport,calls=self.sequence(dict(finish='length',output='partial',**spec),
                                      dict(finish='stop',output='Complete'))
        original=self._suspend_continue()
        try:
            c.execute(self.data,'first',transport)
        finally:
            self._restore_continue(original)
        self.assertEqual(1,len(calls))
        self.assertEqual('RECEIVED',c.inspect(self.store,cid)['attempts'][0]['state'])
        return cid,transport,calls

    def _commit_control_before_child_prepare(self, apply_control, transport):
        entering_write=threading.Event()
        lock_held=threading.Event()
        real_c,real_r=c._transaction,r._transaction

        @contextmanager
        def wrapped(connection, *, write=False):
            if write:
                entering_write.set()
            with real_c(connection, write=write):
                yield

        def controller():
            with closing(storage.Store(self.data)) as other:
                conn=c.connection_for(other)
                conn.execute('BEGIN IMMEDIATE')
                lock_held.set()
                self.assertTrue(entering_write.wait(timeout=5))
                apply_control(conn)
                conn.execute('COMMIT')

        def recoverer():
            self.assertTrue(lock_held.wait(timeout=5))
            try:
                c._transaction=wrapped
                r._transaction=wrapped
                r.continue_preauthorized(self.data,'first',transport)
            finally:
                c._transaction=real_c
                r._transaction=real_r

        threads=[threading.Thread(target=controller), threading.Thread(target=recoverer)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())

    def test_operator_stop_committed_before_child_prepare_emits_nothing(self):
        cid,transport,calls=self._received_source('stop-before-prepare')
        def apply_control(conn):
            conn.execute('UPDATE s4_status SET admission_id=NULL, stop_reason=?, stopped_at=? WHERE campaign_id=?',
                         ('OPERATOR_STOP',c._now(),cid))
        self._commit_control_before_child_prepare(apply_control,transport)
        self.assertEqual(1,len(calls))
        source=c.inspect(self.store,cid)
        self.assertEqual('STOPPED',source['state'])
        self.assertIsNone(source['admission'])
        self.assertEqual('RECEIVED',source['attempts'][0]['state'])
        self.assertEqual('partial',source['attempts'][0]['operation']['receipt']['result']['output'])
        self.assertIsNone(next((s for s in c.list_campaigns(self.store)
                                if s['manifest'].get('recovery_of')=='first'),None))

    def test_source_stop_after_child_prepare_refuses_child_transport(self):
        cid=self.granted('prepared-then-stop')
        c.reserve(self.store,cid,'x','first')
        real_execute=c.execute
        def execute_child_after_source_stop(data,oid,transport=None):
            if oid!='first':
                with closing(storage.Store(self.data)) as other:
                    c.stop(other,cid)
            return real_execute(data,oid,transport)
        transport,calls=self.sequence(dict(finish='length',output='partial'),
                                      dict(finish='stop',output='Complete'))
        try:
            c.execute=execute_child_after_source_stop
            real_execute(self.data,'first',transport)
        finally:
            c.execute=real_execute
        self.assertEqual(1,len(calls))
        source=c.inspect(self.store,cid)
        self.assertEqual('STOPPED',source['state'])
        self.assertIsNone(source['admission'])
        self.assertEqual('RECEIVED',source['attempts'][0]['state'])
        child=self._recovery('first')
        self.assertIsNone(child['admission'])
        self.assertEqual(['INTENT_RECORDED'],[a['state'] for a in child['attempts']])
        self.assertEqual(child['manifest'],c.inspect(self.store,child['manifest']['campaign_id'])['manifest'])

    def test_maintenance_close_before_child_prepare_emits_nothing(self):
        cid,transport,calls=self._received_source('close-before-prepare')
        def apply_control(conn):
            conn.execute('UPDATE s4_status SET admission_id=NULL, stop_reason=?, stopped_at=? WHERE admission_id IS NOT NULL',
                         ('MAINTENANCE',c._now()))
        self._commit_control_before_child_prepare(apply_control,transport)
        self.assertEqual(1,len(calls))
        source=c.inspect(self.store,cid)
        self.assertIsNone(source['admission'])
        self.assertEqual('RECEIVED',source['attempts'][0]['state'])
        self.assertIsNone(next((s for s in c.list_campaigns(self.store)
                                if s['manifest'].get('recovery_of')=='first'),None))

    def test_concurrent_continuations_keep_one_child_intent_and_emission(self):
        cid,transport,calls=self._received_source('concurrent-continue')
        ready1,ready2,go=threading.Event(),threading.Event(),threading.Event()
        errors=[]
        def run(ready):
            ready.set()
            self.assertTrue(go.wait(timeout=5))
            try:
                r.continue_preauthorized(self.data,'first',transport)
            except Exception as exc:
                errors.append(exc)
        threads=[threading.Thread(target=run,args=(ready1,)),
                 threading.Thread(target=run,args=(ready2,))]
        for thread in threads:
            thread.start()
        self.assertTrue(ready1.wait(timeout=5) and ready2.wait(timeout=5))
        go.set()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual([],errors)
        children=[s for s in c.list_campaigns(self.store) if s['manifest'].get('recovery_of')=='first']
        self.assertEqual(1,len(children))
        self.assertEqual(1,len(children[0]['admissions']))
        self.assertEqual(1,len(children[0]['attempts']))
        self.assertEqual(2,len(calls))
        self.assertEqual('RECEIVED',children[0]['attempts'][0]['state'])
        self.assertIsNotNone(c.inspect(self.store,cid)['admission'])

    def test_admitted_source_still_runs_automatic_second_call(self):
        cid=self.granted('still-admitted')
        c.reserve(self.store,cid,'x','first')
        transport,calls=self.sequence(dict(finish='length',output='partial'),
                                      dict(finish='stop',output='Complete'))
        c.execute(self.data,'first',transport)
        self.assertEqual(2,len(calls))
        child=self._recovery('first')
        self.assertEqual('RECEIVED',child['attempts'][0]['state'])
        self.assertIsNotNone(c.inspect(self.store,cid)['admission'])

    def test_orphan_piece_blocks_preauthorized_child_write(self):
        cid,transport,calls=self._received_source('orphan-blocks')
        before=self.store.inspect_budget('local-comparison')
        orphan=self.data/'pieces'/'orphan-untracked.txt'
        orphan.write_bytes(b'untracked')
        r.continue_preauthorized(self.data,'first',transport)
        self.assertEqual(1,len(calls))
        try:
            campaigns=c.list_campaigns(self.store)
            source=c.inspect(self.store,cid)
            budget=self.store.inspect_budget('local-comparison')
        except storage.IntegrityError:
            orphan.unlink()
            campaigns=c.list_campaigns(self.store)
            source=c.inspect(self.store,cid)
            budget=self.store.inspect_budget('local-comparison')
        children=[s for s in campaigns if s['manifest'].get('recovery_of')=='first']
        self.assertEqual([],children)
        self.assertEqual([], [a for s in children for a in s['admissions']])
        self.assertEqual([], [a for s in children for a in s['attempts']])
        self.assertEqual(before['reserved'],budget['reserved'])
        self.assertEqual('RECEIVED',source['attempts'][0]['state'])
        self.assertEqual('partial',source['attempts'][0]['operation']['receipt']['result']['output'])


if __name__ == '__main__':
    unittest.main()
