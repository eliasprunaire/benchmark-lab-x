"""Fictional S4 regressions independent of the sealed acceptance report."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
import multiprocessing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from benchmark import campaigns as c, preparation as prep, qualification as q, runtime, storage
from benchmark_web import views
from tests.test_s3_regressions import ACTOR, AUTHORITY, check, fixture, specification


def independent_acquisition(data, entered, release):
    def transport(operation, request):
        entered.touch()
        deadline = time.monotonic() + 15
        while not release.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('Fictional worker timeout')
            time.sleep(.02)
        return response(operation, request)
    c.execute(data, 'intent-x', transport)


def manifest(candidate, name='local-comparison'):
    return dict(campaign_id=name, version=1, contract_sha256=candidate['contract_sha256'],
        cases=[dict(id='notes', package_sha256=candidate['contract']['package_sha256'])],
        panel=[dict(id=ident, provider='fictional-provider', model='fictional-'+ident, revision='fixed-1',
                    access='direct', channel_id='fixture-channel-'+ident, route='fixture-route',
                    parameters={}, effort='requested', required_observations=['revision', 'channel_id']) for ident in ('x','y')],
        conditions=dict(pi=dict(package='fictional-pi', version='local-1', sha256='1'*64, status='configured', proof='Local fixture bytes'),
                        packages=[], tools=[], skills=[], context_sha256='2'*64, defaults={},
                        environment={'fictional':True}, frozen_at='2026-09-07T00:00:00Z'),
        plan=[dict(cell_id=ident, case_id='notes', configuration_id=ident) for ident in ('x','y')],
        attempt_policy=dict(retries=False, order=['x','y'], reason='Declared local order'),
        cost_basis=candidate['contract']['specification']['cost_basis'])


def inputs(snapshot, purpose='start', cells=None, budget='local-comparison'):
    m=snapshot['manifest']
    authority=dict(actor='Ayo', authority_id='TEST_ONLY_'+purpose, purpose=purpose,
                   manifest_sha256=snapshot['manifest_sha256'], execution_authority='TEST_ONLY_EXECUTION',
                   candidate_authority='TEST_ONLY_CALLS', budget_authority='TEST_ONLY_BUDGET',
                   budget_id=budget, allowed_cells=cells or ['x','y'], reserve_amounts={'x':'7','y':'7'})
    evidence=dict(pi_sha256=m['conditions']['pi']['sha256'], context_sha256=m['conditions']['context_sha256'],
                  channels={p['id']:dict(available=True, revision=p['revision'], channel_id=p['channel_id'],
                            route=p['route'], proof='Local channel witness') for p in m['panel']},
                  confinement=dict(code_execution=False, proof='No executable tools installed'))
    return authority, evidence


def response(operation, request):
    config=request['requested_configuration']
    return dict(receipt=dict(receipt_id='r-'+operation['operation_id'],
                observed_configuration=dict(revision=config['revision'], channel_id=config['channel_id'],
                                            sources={'revision':'fixture response', 'channel_id':'fixture transport'}),
                resources_seen=[p['name'] for p in request.get('outgoing', {}).get('pieces', [])],
                result=dict(output='  fictional raw output\n', incident=None, emission='ESTABLISHED')),
                cost=dict(status='KNOWN',amount='2',currency='TEST',source='Fictional acquisition receipt'))


class S4Regressions(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='s4-reg-')
        self.addCleanup(temporary.cleanup)
        self.home=Path(temporary.name).resolve()
        self.data=self.home/'private'
        self.session,self.view,self.reference=fixture(self.data)
        q.initialize(self.data)
        self.store=storage.Store(self.data)
        self.addCleanup(self.store.close)
        self.candidate=q.draft(self.store,'fixture',self.view['revision'],specification(self.reference))
        receipt=q.qualify(self.store,self.candidate['contract_sha256'],reviewer=ACTOR,check=check)
        q.approve(self.store,self.candidate['contract_sha256'],receipt['qualification_id'],actor=ACTOR,authority=AUTHORITY)
        prep.close_admission(self.store)
        self.before=self.store.inspect_operations()
        c.initialize(self.data)
        self.store.create_budget('local-comparison','40','TEST')
        self.snapshot=c.create(self.store,manifest(self.candidate))

    def admit(self, purpose='start', cells=None):
        return c.admit(self.store,'local-comparison',*inputs(self.snapshot,purpose,cells))

    def reserve(self, cell='x', attempt='intent-x'):
        return c.reserve(self.store,'local-comparison',cell,attempt)

    def worker_survives_service(self, killed):
        from benchmark.service import executor_health, serve_executor
        context = multiprocessing.get_context('spawn')
        entered, release = self.home / 'entered', self.home / 'release'
        socket = self.home / 'executor.sock'
        processes = []

        def start_service():
            process = context.Process(target=serve_executor, args=(self.data, socket, 'a' * 40))
            processes.append(process)
            process.start()
            deadline = time.monotonic() + 5
            while True:
                try:
                    executor_health(socket)
                    return process
                except OSError:
                    self.assertTrue(process.is_alive())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.02)

        try:
            executor = start_service()
            self.admit(); self.reserve()
            worker = context.Process(target=independent_acquisition, args=(self.data, entered, release))
            processes.append(worker)
            worker.start()
            deadline = time.monotonic() + 5
            while not entered.exists():
                self.assertTrue(worker.is_alive())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            for restart in (True, False):
                executor.terminate(); executor.join(5)
                self.assertEqual(0, executor.exitcode)
                self.assertTrue(worker.is_alive())
                self.assertEqual('EMISSION_POSSIBLE', c.inspect(self.store, 'local-comparison')['attempts'][0]['state'])
                self.assertEqual(78, self.cli('quiescence')[0])
                with self.assertRaises((ValueError, OSError)):
                    runtime.backup(self.data, self.home / 'premature-backup')
                self.assertFalse((self.home / 'premature-backup').exists())
                if restart:
                    executor = start_service()
                    self.assertEqual('EMISSION_POSSIBLE', c.inspect(self.store, 'local-comparison')['attempts'][0]['state'])
            if killed:
                worker.kill(); worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(78, self.cli('quiescence')[0])
                runtime.stop(self.data, self.store, 'WORKER_EXIT_CHECKED', after_process_exit=True)
                expected = 'AMBIGUOUS'
                self.assertEqual('7', self.store.inspect_budget('local-comparison')['reserved'])
            else:
                release.touch(); worker.join(5)
                self.assertEqual(0, worker.exitcode)
                expected = 'RECEIVED'
                attempt = c.inspect(self.store, 'local-comparison')['attempts'][0]
                self.assertEqual(b'  fictional raw output\n', self.store.read_piece(attempt['output_piece_id']))
            self.assertEqual(expected, c.inspect(self.store, 'local-comparison')['attempts'][0]['state'])
            self.assertEqual(0, self.cli('quiescence')[0])
            self.assertEqual('BACKUP_VERIFIED', runtime.backup(self.data, self.home / 'backup')['state'])
            self.assertTrue(self.store.verify_storage()['integrity_ok'])
        finally:
            release.touch()
            for process in processes:
                if process.is_alive():
                    process.terminate(); process.join(5)
                if process.is_alive():
                    process.kill(); process.join(5)

    def test_service_restart_keeps_worker_active_until_late_receipt(self):
        self.worker_survives_service(killed=False)

    def test_service_restart_keeps_worker_active_until_confirmed_exit(self):
        self.worker_survives_service(killed=True)

    def test_original_preparation_cost_and_receipt_are_preserved(self):
        # The S3 fixture uses response_for(), whose sourced preparation cost is 3
        self.assertEqual('3',self.store.inspect_budget('fictional')['spent'])
        self.admit(); self.reserve(); c.execute(self.data,'intent-x',response)
        self.assertEqual(self.before,[o for o in self.store.inspect_operations() if o['phase']=='preparation'])
        self.assertEqual('3',self.store.inspect_budget('fictional')['spent'])
        self.assertEqual('2',self.store.inspect_budget('local-comparison')['spent'])

    def test_manifest_and_authority_history_reject_update_delete_replace(self):
        self.admit(); self.reserve(); c.execute(self.data,'intent-x',response)
        connection=self.store._connection
        for table in ('s4_control','s4_campaigns','s4_cells','s4_admissions','s4_attempts','s4_emissions','s4_results'):
            rows=connection.execute('SELECT * FROM '+table).fetchall()
            column=connection.execute('PRAGMA table_info('+table+')').fetchone()[1]
            with self.subTest(table=table):
                for sql,params in ((f'UPDATE {table} SET {column}={column}',()),
                                   ('DELETE FROM '+table,()),
                                   ('INSERT OR REPLACE INTO '+table+' VALUES ('+','.join('?' for _ in rows[0])+')',rows[0])):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(sql,params)
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_concurrent_reservations_record_only_one_intent_and_reserve(self):
        self.admit()
        barrier=threading.Barrier(2)
        def reserve(ident):
            with closing(storage.Store(self.data)) as store:
                barrier.wait(timeout=5)
                try:
                    c.reserve(store,'local-comparison','x',ident)
                    return True
                except ValueError:
                    return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(reserve,i) for i in ('one','two')]
            results=[f.result(timeout=10) for f in futures]
        self.assertEqual([False,True],sorted(results))
        self.assertEqual('7',self.store.inspect_budget('local-comparison')['reserved'])
        self.assertEqual(1,len(c.inspect(self.store,'local-comparison')['attempts']))

    def test_callback_sees_durable_links_and_cannot_mutate_frozen_request(self):
        self.admit(); self.reserve()
        def mutate(op,request):
            with closing(storage.Store(self.data)) as other:
                snapshot=c.inspect(other,'local-comparison')
                self.assertEqual('EMISSION_POSSIBLE',snapshot['attempts'][0]['state'])
                self.assertTrue(snapshot['attempts'][0]['emission_admission_id'])
                self.assertEqual('7',snapshot['budget']['reserved'])
            self.assertNotIn(self.reference,json.dumps(request))
            out=response(op,request)
            request['outgoing']['pieces'].clear()
            return out
        c.execute(self.data,'intent-x',mutate)
        self.assertEqual(self.snapshot['manifest'],c.inspect(self.store,'local-comparison')['manifest'])
        self.assertTrue(self.store.verify_storage()['integrity_ok'])

    def test_no_transport_and_reading_never_emit_or_resume(self):
        self.admit(); self.reserve()
        with self.assertRaises(ValueError): c.execute(self.data,'intent-x')
        before=self.store.inspect_operations()
        with closing(storage.Store(self.data)) as reopened:
            c.inspect(reopened,'local-comparison'); c.list_campaigns(reopened)
            prep.view(reopened,self.session,'fixture'); runtime.status(self.data,reopened)
        self.assertEqual(before,self.store.inspect_operations())
        runtime.stop(self.data,self.store,'RESTART',after_process_exit=True)
        with self.assertRaises(ValueError): c.execute(self.data,'intent-x',response)
        self.admit('resume',['x']); c.execute(self.data,'intent-x',response)
        snapshot=c.inspect(self.store,'local-comparison')
        self.assertNotEqual(snapshot['attempts'][0]['admission_id'],snapshot['attempts'][0]['emission_admission_id'])
        self.assertEqual(2,len(snapshot['admissions']))

    def test_declared_order_checked_before_emission(self):
        self.admit(); self.reserve('y','intent-y')
        with self.assertRaises(ValueError): c.execute(self.data,'intent-y',response)
        self.assertEqual('INTENT_RECORDED',c.inspect(self.store,'local-comparison')['attempts'][0]['state'])
        self.reserve(); c.execute(self.data,'intent-x',response); c.execute(self.data,'intent-y',response)
        self.assertEqual(['RECEIVED','RECEIVED'],[x['state'] for x in c.inspect(self.store,'local-comparison')['cells']])

    def test_malformed_response_keeps_unknown_effects_and_reserve(self):
        self.admit(); self.reserve()
        c.execute(self.data,'intent-x',lambda op,request:{'cost':{'status':'KNOWN','amount':'0'}})
        snapshot=c.inspect(self.store,'local-comparison')
        self.assertEqual('AMBIGUOUS',snapshot['attempts'][0]['state'])
        self.assertEqual('7',snapshot['budget']['reserved'])
        self.assertIsNone(snapshot['attempts'][0]['operation']['observed_cost'])
        with self.assertRaises(ValueError): self.admit('resume',['y'])
        view=prep.view(self.store,self.session,'fixture')['campaigns'][0]
        self.assertEqual('INCONNU',view['budget']['balance_status'])
        self.assertIsNone(view['budget']['available'])

    def test_required_source_and_unknown_emission_block_next_call(self):
        self.admit(); self.reserve()
        def unproven(op,request):
            out=response(op,request)
            out['receipt']['observed_configuration']['sources'].clear()
            out['receipt']['result']['emission']='UNKNOWN'
            return out
        c.execute(self.data,'intent-x',unproven)
        with self.assertRaises(ValueError): self.reserve('y','intent-y')
        attempt=c.inspect(self.store,'local-comparison')['attempts'][0]
        self.assertEqual(['revision','channel_id'],attempt['attribution_incident'])
        self.assertEqual('2',attempt['operation']['observed_cost']['amount'])

    def test_pending_s2_change_blocks_acquisition_without_rewriting_manifest(self):
        self.admit(); self.reserve()
        prep.admit(self.store,dict(authority_id='TEST_ONLY_EDIT',budget_id='fictional',reserve_amount='7',requested_configuration={'model':'fictional'}))
        prep.submit(self.store,self.session,'fixture',dict(action_id='edit',revision=self.view['revision'],kind='correct',message='Modifier les notes fictives'),'a'*40,True)
        with self.assertRaises(ValueError): c.execute(self.data,'intent-x',response)
        self.assertEqual(self.snapshot['manifest'],c.inspect(self.store,'local-comparison')['manifest'])

    def test_shared_envelope_unknown_cost_blocks_already_reserved_other_campaign(self):
        self.admit(); self.reserve()
        second=c.create(self.store,manifest(self.candidate,'second'))
        c.admit(self.store,'second',*inputs(second))
        c.reserve(self.store,'second','x','second-x')
        def unknown(op,request):
            out=response(op,request); out['cost'].update(status='UNKNOWN',amount=None); return out
        c.execute(self.data,'intent-x',unknown)
        with self.assertRaises(ValueError): c.execute(self.data,'second-x',response)
        self.assertEqual('14',self.store.inspect_budget('local-comparison')['reserved'])

    def test_storage_checks_operation_join_and_receipt_hash(self):
        self.admit(); self.reserve(); c.execute(self.data,'intent-x',response)
        raw=self.store._connection.execute("SELECT receipt_json FROM operations WHERE operation_id='intent-x'").fetchone()[0]
        changed=json.loads(raw); changed['resources_seen']=[]
        self.store._connection.execute("UPDATE operations SET receipt_json=? WHERE operation_id='intent-x'",(storage._strict_json(changed),))
        with self.assertRaises(ValueError): self.store.verify_storage()
        with self.assertRaises(ValueError): c.inspect(self.store,'local-comparison')

    def test_shared_budget_cannot_bypass_attribution_incident_with_new_manifest(self):
        self.admit(); self.reserve()
        def mismatch(op,request):
            out=response(op,request)
            out['receipt']['observed_configuration']['revision']='unrequested'
            return out
        c.execute(self.data,'intent-x',mismatch)
        second=c.create(self.store,manifest(self.candidate,'second'))
        with self.assertRaises(ValueError): c.admit(self.store,'second',*inputs(second))
        self.assertEqual('2',self.store.inspect_budget('local-comparison')['spent'])

    def test_output_never_enters_session_projection_or_candidate_pieces(self):
        self.admit(); self.reserve(); c.execute(self.data,'intent-x',response)
        view=prep.view(self.store,self.session,'fixture')
        raw=json.dumps(view)
        self.assertNotIn('fictional raw output',raw)
        self.assertNotIn(self.reference,raw)
        attempt=c.inspect(self.store,'local-comparison')['attempts'][0]
        self.assertNotIn(attempt['output_piece_id'],raw)
        self.assertEqual(b'  fictional raw output\n',self.store.read_piece(attempt['output_piece_id']))
        with self.assertRaises(ValueError): prep.piece_bytes(self.store,self.session,'fixture',self.view['revision'],attempt['output_piece_id'])
        expected = deepcopy(self.candidate['contract']['package'])
        for piece in expected['pieces']:
            del piece['sha256']
        self.assertEqual(expected,view['package'])
        self.assertIn('INCONNU',views.render(view,'csrf').decode())

    def test_foreign_actor_extra_authority_fields_and_executable_tools_refused(self):
        authority,evidence=inputs(self.snapshot)
        for mutation in ({**authority,'actor':'public-user'},{**authority,'publication':True}):
            with self.assertRaises(ValueError): c.admit(self.store,'local-comparison',mutation,evidence)
        evidence['confinement']['code_execution']=True
        with self.assertRaises(ValueError): c.admit(self.store,'local-comparison',authority,evidence)
        self.assertEqual([],c.inspect(self.store,'local-comparison')['attempts'])

    def test_health_consumer_accepts_s4_status_before_during_and_after_admission(self):
        from benchmark.service import executor_health
        # Exercise the unchanged health decoder with the actual producer's JSON
        # Only socket I/O is replaced; no socket permission is needed for this
        # wire-contract regression. Real process tests remain separate evidence
        for phase, expected in (('prepared', False), ('admitted', True), ('stopped', False)):
            with self.subTest(phase=phase):
                if phase == 'admitted':
                    self.admit()
                elif phase == 'stopped':
                    runtime.stop(self.data, self.store, 'TEST_HEALTH_STOP')
                payload = {'source_sha': 'a' * 40, 'storage': 'ok',
                           **runtime.status(self.data, self.store)}
                with patch('benchmark.service.socket.socket') as socket:
                    connection = socket.return_value.__enter__.return_value
                    connection.recv.side_effect = [(runtime.encode(payload) + '\n').encode(), b'']
                    observed = executor_health(self.home / 'unused.sock')
                self.assertEqual(expected, observed['admission'])
                self.assertEqual('ok', observed['storage'])
                self.assertFalse(observed['restore_pending'])
                self.assertEqual({'RECEIVED': 1}, observed['operations'])

    def cli(self,action,request=None,private=True):
        args=[sys.executable,'-B','-m','benchmark.runtime',action,'--data',str(self.data)]
        if request is not None:
            path=self.home/'operator.json'; path.write_text(json.dumps(request)); path.chmod(0o600 if private else 0o644)
            args+=['--authority',str(path)]
        result=subprocess.run(args,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
        return result.returncode,json.loads(result.stdout)

    def test_cli_lifecycle_strict_private_input_and_maintenance(self):
        self.assertEqual(0,self.cli('initialize-campaigns')[0])
        second=manifest(self.candidate,'cli-campaign')
        code,snapshot=self.cli('create-campaign',{'manifest':second})
        self.assertEqual(0,code)
        authority,evidence=inputs(snapshot)
        request=dict(campaign_id='cli-campaign',authority=authority,evidence=evidence)
        self.assertEqual(78,self.cli('admit-campaign',request,private=False)[0])
        self.assertEqual(0,self.cli('admit-campaign',request)[0])
        self.assertTrue(runtime.status(self.data,self.store)['admission'])
        with self.assertRaises(ValueError): runtime.backup(self.data,self.home/'not-quiescent')
        self.assertEqual(0,self.cli('maintenance')[0])
        self.assertFalse(runtime.status(self.data,self.store)['admission'])
        self.assertEqual(78,self.cli('admit-campaign',request)[0])
        request['authority']['purpose']='resume'
        self.assertEqual(0,self.cli('resume-campaign',request)[0])
        self.assertEqual(0,self.cli('stop-campaign',dict(campaign_id='cli-campaign',reason='TEST_ONLY_STOP'))[0])
        self.assertEqual(78,self.cli('inspect-campaign',dict(campaign_id='cli-campaign',actor='Ayo'))[0])
        self.assertEqual([],c.inspect(self.store,'cli-campaign')['attempts'])


if __name__=='__main__':
    unittest.main()
