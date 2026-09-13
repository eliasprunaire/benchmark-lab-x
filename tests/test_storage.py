"""S1 internal storage contract and independent, fictional acceptance tests

Extends the canary Store API; no transport is implemented by storage
create_budget(budget_id, limit: decimal string, currency: nonempty str)
inspect_budget(id) -> {budget_id, limit, currency, reserved, spent, available,
                      unknown_cost_operations: [operation_id]}
reserve_intent(operation, budget_id, amount: decimal string) commits atomically
operation = {operation_id, phase, dossier_id, revision, authority,
             engine_version, requested_configuration, resources}
phase in preparation/correction/judgment/acquisition; no campaign required
mark_emission_possible(id): INTENT_RECORDED -> EMISSION_POSSIBLE exactly once
mark_ambiguous(id, reason): EMISSION_POSSIBLE -> AMBIGUOUS, reason preserved
record_receipt(id, receipt, observed_cost): EMISSION_POSSIBLE/AMBIGUOUS -> RECEIVED
receipt = {receipt_id, observed_configuration: dict|None, resources_seen, result}
cost = {status: KNOWN|UNKNOWN, amount: decimal string|None, currency, source}
inspect_operations() returns records with all operation fields, budget_id,
reserved_amount, state, receipt, observed_cost, ambiguity_reason, created_at
Unknown costs retain the reservation and block dependent budget admission;
EMISSION_POSSIBLE/AMBIGUOUS block new intents on that budget; known costs
replace the reservation even on overrun (facts are never discarded)
No overwriting operation/budget/receipt, no automatic replay or reconciliation
ConflictError for invalid transitions/duplicates, BudgetError(ValueError) for
admission refusal; input errors ValueError, missing references KeyError
Money is finite nonnegative decimal text, persisted as text, never float
verify_storage() -> {schema_version: 1, integrity_ok: bool, broken_pieces: [id],
  orphan_files: [relative path], active_operations: [id],
  ambiguous_operations: [id], unknown_cost_operations: [id]}
Inspection is observational; no deletion/repair/resume. Fresh schema version 1
adds operations/budgets/reservations to the two canary tables. Recognize the
exact legacy canary schema without changing it; legacy operation API must
raise SchemaError rather than migrate it. Unknown schemas are never rewritten
Fixtures below are entirely invented; assertions are frozen before Graph
"""
from __future__ import annotations

import copy
from contextlib import closing
from decimal import Decimal
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from benchmark import storage as product

LEGACY_SCHEMA = ('CREATE TABLE dossier_revisions (\n        dossier_id TEXT NOT NULL,\n        revision INTEGER NOT NULL CHECK(revision > 0),\n        payload_json TEXT NOT NULL,\n        PRIMARY KEY (dossier_id, revision)\n    )', "CREATE TABLE pieces (\n        piece_id TEXT PRIMARY KEY NOT NULL,\n        dossier_id TEXT NOT NULL,\n        revision INTEGER NOT NULL,\n        name TEXT NOT NULL,\n        role TEXT NOT NULL CHECK(role IN ('candidate', 'judge')),\n        media_type TEXT NOT NULL,\n        relative_path TEXT UNIQUE NOT NULL,\n        sha256 TEXT NOT NULL,\n        size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),\n        FOREIGN KEY (dossier_id, revision)\n            REFERENCES dossier_revisions (dossier_id, revision)\n    )")

PAYLOAD = {"request": "Suivi fictif Orme", "clarifications": ["Retour prévu"],
           "reformulation": "Préparer un suivi fictif", "validated_assumptions": ["Accord témoin"],
           "fictional_parameters": {"invented": True}, "state": "EN_ATTENTE"}


def operation(ident="op", phase="preparation"):
    return {"operation_id": ident, "phase": phase, "dossier_id": "d", "revision": 1,
            "authority": "GO_FICTIF", "engine_version": "test-s1",
            "requested_configuration": {"model": "fictif", "effort": "fictif"},
            "resources": ["piece-fictive"]}


def cost(amount="2", status="KNOWN"):
    return {"status": status, "amount": amount, "currency": "TEST", "source": "reçu fictif"}


def receipt():
    return {"receipt_id": "receipt-fictif", "observed_configuration": None,
            "resources_seen": ["piece-fictive"], "result": {"fictional": True}}


def file_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def require_budget(observed, *, reserved, spent, available, unknown=()):
    for field, expected in [("reserved", reserved), ("spent", spent), ("available", available)]:
        assert type(observed[field]) is str, field
        assert Decimal(observed[field]) == Decimal(expected), field
    assert sorted(observed["unknown_cost_operations"]) == sorted(unknown)


def require_intent(rows, expected, state):
    assert len(rows) == 1
    row = rows[0]
    for k,v in expected.items():
        assert row[k] == v, k
    assert row["state"] == state
    assert row["budget_id"] == "b" and Decimal(row["reserved_amount"]) == 7
    assert row["receipt"] is None and row["observed_cost"] is None
    assert isinstance(row["created_at"], str) and row["created_at"]


def require_cut(observed):
    assert observed["old_revision"] == PAYLOAD
    assert observed["integrity"] == ["ok"]
    assert observed["foreign_keys"] == []
    assert not observed["broken"]
    assert observed["file_inventory"] == observed["after_inspection"]
    assert set(observed["orphans"]) == set(observed["expected_orphans"])
    if observed["piece_present"]:
        assert observed["piece_bytes"] == b"complete fictional bytes".hex()


def witness_process(barrier, output):
    barrier.wait(timeout=20)
    output.put("observed")


class WitnessQualificationTests(unittest.TestCase):
    """Qualification of observation checkers, not a product implementation"""
    def test_process_barrier_and_observation_channel(self):
        ctx=multiprocessing.get_context("spawn");barrier=ctx.Barrier(2);q=ctx.Queue()
        children=[ctx.Process(target=witness_process,args=(barrier,q)) for _ in range(2)]
        try:
            for child in children:child.start()
            for child in children:child.join(30);self.assertEqual(child.exitcode,0)
            self.assertEqual([q.get(timeout=5),q.get(timeout=5)],["observed","observed"])
        finally:
            for child in children:
                if child.is_alive():child.kill();child.join()
            q.close()

    def test_valid_observations_and_budget_faults(self):
        good={"reserved":"7", "spent":"2", "available":"1", "unknown_cost_operations":["op"]}
        require_budget(good,reserved="7",spent="2",available="1",unknown=["op"])
        for field,bad in [("reserved","0"),("spent","0"),("available","8"),
                          ("reserved",7.0),("unknown_cost_operations",[])]:
            with self.subTest(field=field), self.assertRaises(AssertionError):
                changed=dict(good);changed[field]=bad
                require_budget(changed,reserved="7",spent="2",available="1",unknown=["op"])

    def test_valid_intent_and_attribution_faults(self):
        row={**operation(),"budget_id":"b","reserved_amount":"7","state":"INTENT_RECORDED",
             "receipt":None,"observed_cost":None,"created_at":"2026-01-01T00:00:00Z"}
        require_intent([row],operation(),"INTENT_RECORDED")
        for field,bad in [("phase","acquisition"),("authority",""),("revision",2),
                          ("state","RECEIVED"),("reserved_amount","0"),("created_at",""),
                          ("receipt",{}),("observed_cost",cost()),("requested_configuration",{})]:
            with self.subTest(field=field), self.assertRaises(AssertionError):
                changed=dict(row);changed[field]=bad;require_intent([changed],operation(),"INTENT_RECORDED")
        with self.assertRaises(AssertionError):require_intent([],operation(),"INTENT_RECORDED")

    def test_valid_cut_and_integrity_faults(self):
        good={"old_revision":PAYLOAD,"integrity":["ok"],"foreign_keys":[],"broken":[],
              "file_inventory":{"orphan":"hash"},"after_inspection":{"orphan":"hash"},
              "orphans":["orphan"],"expected_orphans":["orphan"],"piece_present":True,
              "piece_bytes":b"complete fictional bytes".hex()}
        require_cut(good)
        for field,bad in [("old_revision",{}),("integrity",["broken"]),("foreign_keys",["bad"]),
                          ("broken",["p"]),("after_inspection",{}),("orphans",[]),("piece_bytes","00")]:
            with self.subTest(field=field), self.assertRaises(AssertionError):
                changed=dict(good);changed[field]=bad;require_cut(changed)


def reserve_worker(root, ident, barrier, output):
    s=product.Store(Path(root))
    try:
        barrier.wait(timeout=20)
        s.reserve_intent(operation(ident),"b","7")
        output.put("admitted")
    except product.BudgetError:
        output.put("budget_refused")
    finally:s.close()


def transport_worker(root, output):
    s=product.Store(Path(root))
    try:
        # The fake transport is this distinct process reading committed state
        output.put({"ops":s.inspect_operations(),"budget":s.inspect_budget("b")})
    finally:s.close()


def ambiguous_worker(root, counter):
    s=product.Store(Path(root))
    s.mark_emission_possible("op")
    # One fictional external effect, no real network or supplier
    with open(counter,"ab") as f:f.write(b"x");f.flush();os.fsync(f.fileno())
    os._exit(77)


def cut_worker(root, boundary):
    """Kill at ordinary I/O or SQL boundaries; never rely on candidate test hooks"""
    s=product.Store(Path(root))
    connect=product.sqlite3.connect
    class CutConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            upper=" ".join(sql.upper().split())
            target=(upper.startswith("INSERT INTO DOSSIER_REVISIONS") if boundary.startswith("revision") else upper=="COMMIT")
            if target and boundary in ("before_commit","revision_before"):os._exit(77)
            result=super().execute(sql,parameters)
            if target and boundary in ("after_commit","revision_after"):os._exit(77)
            return result
    # Reopen through SQLite's standard connection factory to observe transaction SQL
    s.close()
    product.sqlite3.connect=lambda *a,**kw: connect(*a,**dict(kw,factory=CutConnection))
    s=product.Store(Path(root))
    if boundary.startswith("revision"):
        s.save_dossier("d",2,{**PAYLOAD,"request":"nouvelle révision fictive"})
    else:
        if boundary=="partial_file":
            original=product.os.fdopen
            class Partial:
                def __init__(self,f):self.f=f
                def __enter__(self):return self
                def __exit__(self,*args):self.f.close()
                def fileno(self):return self.f.fileno()
                def write(self,data):
                    self.f.write(data[:3]);self.f.flush();os.fsync(self.f.fileno());os._exit(77)
            product.os.fdopen=lambda fd,mode,*a,**kw:Partial(original(fd,mode,*a,**kw)) if mode=="wb" else original(fd,mode,*a,**kw)
        elif boundary=="published_file":
            original=product.os.link
            def link(*a,**kw):original(*a,**kw);os._exit(77)
            product.os.link=link
        s.put_piece("d",1,"new",name="new",role="candidate",media_type="text/plain",content=b"complete fictional bytes")
    os._exit(78)  # A missing injection point must fail the test


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="s1-tests-")
        self.base=Path(self.tmp.name).resolve();self.root=self.base/"private"
        product.initialize(self.root);self.store=product.Store(self.root)
        self.store.save_dossier("d",1,copy.deepcopy(PAYLOAD))
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def restart(self):
        self.store.close();self.store=product.Store(self.root)
    def budget(self,limit="10"):
        self.store.create_budget("b",limit,"TEST")
    def emit(self,ident="op",phase="preparation",amount="7"):
        self.store.reserve_intent(operation(ident,phase),"b",amount)
        self.store.mark_emission_possible(ident)
    def child(self,fn,*args,exitcode=0):
        ctx=multiprocessing.get_context("spawn");p=ctx.Process(target=fn,args=args)
        p.start();p.join(30)
        if p.is_alive():p.kill();p.join();self.fail("child did not finish")
        self.assertEqual(p.exitcode,exitcode)
    def piece(self):
        return self.store.put_piece("d",1,"p",name="fictive.txt",role="candidate",media_type="text/plain",content=b"original")

    def test_revision_preserves_previous_agreements_and_refuses_replacement(self):
        changed={**PAYLOAD,"request":"nouvelle demande fictive"}
        self.store.save_dossier("d",2,changed);self.restart()
        self.assertEqual(self.store.get_dossier("d",1),PAYLOAD)
        self.assertEqual(self.store.get_dossier("d",2),changed)
        with self.assertRaises(product.ConflictError):self.store.save_dossier("d",1,changed)
        self.assertEqual(self.store.get_dossier("d",1),PAYLOAD)

    def test_intent_and_reservation_committed_before_transport(self):
        self.budget();self.store.reserve_intent(operation(),"b","7")
        ctx=multiprocessing.get_context("spawn");q=ctx.Queue()
        self.child(transport_worker,str(self.root),q);obs=q.get(timeout=5);q.close()
        require_intent(obs["ops"],operation(),"INTENT_RECORDED")
        require_budget(obs["budget"],reserved="7",spent="0",available="3")
        self.store.mark_emission_possible("op");self.restart()
        require_intent(self.store.inspect_operations(),operation(),"EMISSION_POSSIBLE")
        with self.assertRaises(product.ConflictError):self.store.mark_emission_possible("op")
        with self.assertRaises(product.BudgetError):self.store.reserve_intent(operation("dependent"),"b","1")

    def test_concurrent_reservations_do_not_double_allocate(self):
        self.budget();ctx=multiprocessing.get_context("spawn");barrier=ctx.Barrier(2);q=ctx.Queue()
        processes=[ctx.Process(target=reserve_worker,args=(str(self.root),ident,barrier,q)) for ident in ("a","b")]
        try:
            for p in processes:p.start()
            for p in processes:p.join(30);self.assertEqual(p.exitcode,0)
            self.assertCountEqual([q.get(timeout=5),q.get(timeout=5)],["admitted","budget_refused"])
            self.restart();self.assertEqual(len(self.store.inspect_operations()),1)
            require_budget(self.store.inspect_budget("b"),reserved="7",spent="0",available="3")
        finally:
            for p in processes:
                if p.is_alive():p.kill();p.join()
            q.close()

    def test_reservation_and_operation_fail_atomically(self):
        self.budget()
        for op,budget,amount,exc in [(operation(),"absent","7",KeyError),
                                    ({**operation(),"revision":99},"b","7",KeyError),
                                    (operation(),"b","11",product.BudgetError)]:
            with self.assertRaises(exc):self.store.reserve_intent(op,budget,amount)
            self.assertEqual(self.store.inspect_operations(),[])
            require_budget(self.store.inspect_budget("b"),reserved="0",spent="0",available="10")
        self.store.reserve_intent(operation(),"b","7")
        with self.assertRaises(product.ConflictError):self.store.reserve_intent(operation(),"b","1")
        require_budget(self.store.inspect_budget("b"),reserved="7",spent="0",available="3")

    def test_costs_separate_phases_zero_unknown_and_overrun(self):
        self.budget("100")
        phases=["preparation","correction","judgment","acquisition"]
        for i,phase in enumerate(phases):
            self.emit(str(i),phase,"7")
            observed=cost(None,"UNKNOWN") if i==3 else cost(["2","0","8"][i])
            self.store.record_receipt(str(i),receipt(),observed)
        self.restart();rows={x["operation_id"]:x for x in self.store.inspect_operations()}
        self.assertEqual([rows[str(i)]["phase"] for i in range(4)],phases)
        for i in range(4):
            self.assertEqual(rows[str(i)]["state"],"RECEIVED")
            self.assertEqual(rows[str(i)]["receipt"],receipt())
            self.assertEqual(rows[str(i)]["observed_cost"],cost(None,"UNKNOWN") if i==3 else cost(["2","0","8"][i]))
        require_budget(self.store.inspect_budget("b"),reserved="7",spent="10",available="83",unknown=["3"])
        with self.assertRaises(product.BudgetError):self.store.reserve_intent(operation("dependent"),"b","1")
        with self.assertRaises(product.ConflictError):self.store.record_receipt("0",receipt(),cost("0"))

    def test_known_cost_overrun_is_preserved_and_blocks_further_admission(self):
        self.budget();self.emit(amount="7")
        self.store.record_receipt("op",receipt(),cost("11"));self.restart()
        self.assertEqual(self.store.inspect_operations()[0]["observed_cost"],cost("11"))
        require_budget(self.store.inspect_budget("b"),reserved="0",spent="11",available="-1")
        with self.assertRaises(product.BudgetError):self.store.reserve_intent(operation("next"),"b","0")

    def test_exact_money_no_float_rounding_and_persisted_text(self):
        self.budget("0.3")
        for ident,amount in [("a","0.1"),("b","0.2")]:self.store.reserve_intent(operation(ident),"b",amount)
        require_budget(self.store.inspect_budget("b"),reserved="0.3",spent="0",available="0")
        with self.assertRaises(product.BudgetError):self.store.reserve_intent(operation("c"),"b","0.00000000000000000000001")
        with closing(sqlite3.connect(self.root/"metadata.sqlite3")) as db, db:
            names={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            self.assertTrue({"operations","budgets","reservations"} <= names)
            for table in ("budgets","reservations"):
                rows=db.execute('SELECT * FROM '+table).fetchall()
                self.assertFalse(any(type(value) is float for row in rows for value in row))

    def test_invalid_inputs_do_not_write(self):
        self.budget()
        before=file_hashes(self.root)
        for amount in [0.1,True,"NaN","Infinity","-1",None,""]:
            with self.subTest(amount=amount),self.assertRaises(ValueError):self.store.reserve_intent(operation(),"b",amount)
        for field,value in [("phase","unknown"),("authority",""),("requested_configuration",{}),
                            ("engine_version",""),("resources",[1]),("revision",True)]:
            with self.subTest(field=field),self.assertRaises(ValueError):self.store.reserve_intent({**operation(),field:value},"b","1")
        with self.assertRaises(product.ConflictError):self.store.create_budget("b","999","TEST")
        self.assertEqual(file_hashes(self.root),before)

    def test_invalid_receipt_or_transition_cannot_release_reservation(self):
        self.budget();self.store.reserve_intent(operation(),"b","7")
        with self.assertRaises(product.ConflictError):self.store.record_receipt("op",receipt(),cost())
        self.store.mark_emission_possible("op")
        for bad in [cost(None),cost("0","UNKNOWN"),{**cost(),"source":""},
                    {**cost(),"currency":"OTHER"},cost("NaN"),cost(1.2),cost("-1")]:
            with self.subTest(cost=bad),self.assertRaises(ValueError):self.store.record_receipt("op",receipt(),bad)
            require_budget(self.store.inspect_budget("b"),reserved="7",spent="0",available="3")
        self.assertEqual(self.store.inspect_operations()[0]["state"],"EMISSION_POSSIBLE")

    def test_ambiguous_fake_effect_never_replays_after_restart(self):
        self.budget();self.store.reserve_intent(operation(),"b","7");counter=self.base/"effect"
        self.child(ambiguous_worker,str(self.root),str(counter),exitcode=77);self.restart()
        self.assertEqual(counter.read_bytes(),b"x")
        self.assertEqual(self.store.inspect_operations()[0]["state"],"EMISSION_POSSIBLE")
        with self.assertRaises(product.ConflictError):self.store.mark_emission_possible("op")
        self.store.mark_ambiguous("op","faux transport interrompu");self.restart()
        self.assertEqual(self.store.inspect_operations()[0]["ambiguity_reason"],"faux transport interrompu")
        self.assertEqual(self.store.inspect_operations()[0]["state"],"AMBIGUOUS")
        with self.assertRaises(product.ConflictError):self.store.mark_emission_possible("op")
        with self.assertRaises(product.BudgetError):self.store.reserve_intent(operation("dependent"),"b","1")
        require_budget(self.store.inspect_budget("b"),reserved="7",spent="0",available="3")
        self.assertEqual(counter.read_bytes(),b"x")
        self.store.record_receipt("op",receipt(),cost("2"));self.restart()
        require_budget(self.store.inspect_budget("b"),reserved="0",spent="2",available="8")
        self.assertEqual(counter.read_bytes(),b"x")

    def test_inspection_preserves_files_and_lists_active_ambiguous_unknown(self):
        self.budget("100");self.store.reserve_intent(operation("intent"),"b","1")
        self.emit("unknown",amount="1");self.store.record_receipt("unknown",receipt(),cost(None,"UNKNOWN"))
        # Other budgets allow independent operations without reusing the blocked envelope
        self.store.create_budget("b2","10","TEST")
        self.store.reserve_intent(operation("ambiguous"),"b2","2");self.store.mark_emission_possible("ambiguous")
        self.store.mark_ambiguous("ambiguous","fictif")
        self.restart();before=file_hashes(self.root);report=self.store.verify_storage()
        self.assertTrue(report["integrity_ok"]);self.assertEqual(report["schema_version"],1)
        self.assertIn("intent",report["active_operations"])
        self.assertIn("ambiguous",report["ambiguous_operations"])
        self.assertEqual(report["unknown_cost_operations"],["unknown"])
        self.assertEqual(file_hashes(self.root),before)

    def test_bad_piece_references_and_content_refused_without_repair(self):
        meta=self.piece();path=self.root/meta["relative_path"];outside=self.base/"outside";outside.write_bytes(b"secret fictional sentinel")
        for defect in ("altered","truncated","missing","symlink","absolute","parent"):
            with self.subTest(defect=defect):
                if path.is_symlink():path.unlink()
                path.write_bytes(b"original");path.chmod(0o600)
                with closing(sqlite3.connect(self.root/"metadata.sqlite3")) as db, db:db.execute("UPDATE pieces SET relative_path=? WHERE piece_id='p'",(meta["relative_path"],))
                if defect=="altered":path.write_bytes(b"alterate")
                elif defect=="truncated":path.write_bytes(b"or")
                elif defect=="missing":path.unlink()
                elif defect=="symlink":path.unlink();path.symlink_to(outside)
                elif defect in ("absolute","parent"):
                    with closing(sqlite3.connect(self.root/"metadata.sqlite3")) as db, db:db.execute("UPDATE pieces SET relative_path=? WHERE piece_id='p'",(str(outside) if defect=="absolute" else '../outside',))
                before=file_hashes(self.base)
                with self.assertRaises(product.IntegrityError):self.store.read_piece("p")
                report=self.store.verify_storage();self.assertFalse(report["integrity_ok"]);self.assertIn("p",report["broken_pieces"])
                self.assertEqual(file_hashes(self.base),before)
                self.assertEqual(outside.read_bytes(),b"secret fictional sentinel")

    def test_unknown_schema_and_nonempty_zero_preserve_database(self):
        self.store.close()
        for version in (999,0):
            with closing(sqlite3.connect(self.root/"metadata.sqlite3")) as db, db:db.execute(f"PRAGMA user_version={version}")
            before=file_hashes(self.root)
            for call in (product.initialize,product.Store):
                with self.assertRaises(product.SchemaError):call(self.root)
                self.assertEqual(file_hashes(self.root),before)

    def test_legacy_canary_schema_is_readable_without_implicit_migration(self):
        root=self.base/"legacy";root.mkdir(mode=0o700);(root/"pieces").mkdir(mode=0o700)
        dbpath=root/"metadata.sqlite3"
        with closing(sqlite3.connect(dbpath)) as db, db:
            for ddl in LEGACY_SCHEMA:db.execute(ddl)
            db.execute("PRAGMA user_version=1")
            db.execute("INSERT INTO dossier_revisions VALUES (?,?,?)",("d",1,json.dumps(PAYLOAD)))
        dbpath.chmod(0o600);before=file_hashes(root)
        product.initialize(root);legacy=product.Store(root)
        try:
            self.assertEqual(legacy.get_dossier("d",1),PAYLOAD)
            with self.assertRaises(product.SchemaError):legacy.create_budget("b","10","TEST")
        finally:legacy.close()
        self.assertEqual(file_hashes(root),before)

    def test_process_cuts_preserve_revisions_detect_orphans_and_never_reference_partial_bytes(self):
        for boundary in ("partial_file","published_file","before_commit","after_commit","revision_before","revision_after"):
            with self.subTest(boundary=boundary):
                root=self.base/boundary;product.initialize(root);s=product.Store(root);s.save_dossier("d",1,PAYLOAD);s.close()
                self.child(cut_worker,str(root),boundary,exitcode=77)
                s=product.Store(root)
                try:
                    old=s.get_dossier("d",1)
                    try:piece_bytes=s.read_piece("new").hex();present=True
                    except KeyError:piece_bytes=None;present=False
                    with closing(sqlite3.connect(root/"metadata.sqlite3")) as db, db:
                        integrity=[x[0] for x in db.execute("PRAGMA integrity_check")];fks=db.execute("PRAGMA foreign_key_check").fetchall()
                        refs={x[0] for x in db.execute("SELECT relative_path FROM pieces")}
                    expected={f.relative_to(root).as_posix() for f in (root/"pieces").iterdir()}-refs
                    before=file_hashes(root);report=s.verify_storage()
                    require_cut({"old_revision":old,"integrity":integrity,"foreign_keys":fks,"broken":report["broken_pieces"],
                                 "file_inventory":before,"after_inspection":file_hashes(root),"orphans":report["orphan_files"],
                                 "expected_orphans":expected,"piece_present":present,"piece_bytes":piece_bytes})
                    if boundary=="after_commit":self.assertTrue(present)
                    elif not boundary.startswith("revision"):self.assertFalse(present)
                    if boundary=="revision_after":self.assertEqual(s.get_dossier("d",2)["request"],"nouvelle révision fictive")
                    if boundary=="revision_before":
                        with self.assertRaises(KeyError):s.get_dossier("d",2)
                finally:s.close()


if __name__ == "__main__":
    unittest.main()
