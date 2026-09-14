from __future__ import annotations

import hashlib
import json
import os
import platform
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from . import __main__ as demo


GOOD_OUTPUT = """## Décisions
Le 20 février 2026, le devis est signé et le montant final est confirmé.
## Montant
4 850 € HT remplace 4 200 € HT.
## Échéances
Les maquettes passent du 13 mars 2026 au 20 mars 2026, révision approuvée. Mise en ligne visée le 3 avril 2026. Acompte de 40 % prévu le 21 février 2026.
## Points ouverts
L’inclusion de l’hébergement et du nom de domaine la première année, ou leur budget séparé, reste inconnue."""


def _start_supervisor(case, fixture, variant="good", inherited_ignore=False, extra_env=None):
    run = fixture.prepared(variant)
    authority, _ = fixture.s9(run)
    temporary = fixture.root / f"tmp-{run.name}"
    temporary.mkdir()
    parent, child = socket.socketpair()
    env = {
        **os.environ,
        "TMPDIR": str(temporary),
        "BENCHMARK_LAB_X_TEST_SOCKET_FD": str(child.fileno()),
        "BENCHMARK_LAB_X_TEST_REPO_ROOT": str(fixture.root),
        **({"BENCHMARK_LAB_X_TEST_WORKER_IGNORES_SIGTERM": "1"} if variant == "ignore-term" else {}),
        **(extra_env or {}),
    }
    process = subprocess.Popen(
        [sys.executable, "-B", "-m", "benchmark", "collect", "--run-dir", str(run), "--authority", str(authority)],
        cwd=demo.PACKAGE_DIR.parent, env=env, pass_fds=(child.fileno(),),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        preexec_fn=(lambda: signal.signal(signal.SIGTERM, signal.SIG_IGN)) if inherited_ignore else None,
    )
    child.close()
    parent.settimeout(5)
    stream = parent.makefile("rb", buffering=0)
    case.addCleanup(parent.close)
    case.addCleanup(stream.close)
    case.addCleanup(lambda: process.poll() is None and process.kill())
    return run, temporary, process, parent, stream


def _until(case, stream, milestone):
    events = []
    while not events or not events[-1].startswith(milestone):
        line = stream.readline()
        case.assertTrue(line, f"jalon absent: {milestone}; événements: {events}")
        events.append(line.decode().strip())
    return events


def _assert_dead(case, pids):
    for pid in pids:
        with case.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


def _event_pids(events, milestone):
    fields = next(event.split() for event in events if event.startswith(milestone))
    return [int(value) for value in fields[1:]]


def _watch_exits(case, pids):
    queue = select.kqueue()
    case.addCleanup(queue.close)
    changes = [select.kevent(pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT, fflags=select.KQ_NOTE_EXIT) for pid in pids]
    queue.control(changes, 0, 0)
    return queue


def _assert_exit_events(case, queue, pids):
    pending = set(pids)
    deadline = time.monotonic() + 5
    while pending:
        remaining = deadline - time.monotonic()
        case.assertGreater(remaining, 0, f"événement de sortie causal absent: {sorted(pending)}")
        events = queue.control(None, len(pending), remaining)
        case.assertTrue(events, f"événement de sortie causal absent: {sorted(pending)}")
        for event in events:
            case.assertIn(event.ident, pending)
            case.assertEqual(event.filter, select.KQ_FILTER_PROC)
            case.assertTrue(event.fflags & select.KQ_NOTE_EXIT)
            case.assertFalse(event.flags & select.KQ_EV_ERROR)
            pending.remove(event.ident)


class DemoTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "runs").mkdir(mode=0o700)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "switch", "-q", "-c", "test-fixture"], check=True)
        subprocess.run(["git", "-C", self.root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-q", "--allow-empty", "-m", "test"], check=True)
        self.old_key = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "fausse-cle-en-memoire"
        self.counter = 0

    def tearDown(self):
        if self.old_key is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = self.old_key
        self.temp.cleanup()

    def write_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=True))
        return path

    def fake_pi(self, version="0.84.4", variant="good"):
        path = self.root / f"pi-{version}-{variant}"
        code = f'''#!/usr/bin/env python3
import json, os, signal, subprocess, sys, time
def event(message):
    fd = os.environ.get("BENCHMARK_LAB_X_TEST_SOCKET_FD")
    if fd is not None: os.write(int(fd), (message + "\\n").encode())
if sys.argv[1:] == ["--version"]:
    print({version!r})
    raise SystemExit(0)
if {variant!r} == "migrating":
    agent_dir = os.environ["PI_CODING_AGENT_DIR"]
    if not all(os.path.isfile(os.path.join(agent_dir, name)) for name in ["settings.json", "models.json"]): raise SystemExit(3)
    os.makedirs(os.path.join(agent_dir, "sessions", "fixture"), exist_ok=True)
    for name in os.listdir(agent_dir):
        if not name.endswith(".jsonl"): continue
        source = os.path.join(agent_dir, name)
        with open(source) as stream: header = json.loads(stream.readline())
        if header.get("type") != "session" or not header.get("cwd"): continue
        safe = "--" + header["cwd"].lstrip("/").replace("/", "-").replace(":", "-") + "--"
        destination = os.path.join(agent_dir, "sessions", safe)
        os.makedirs(destination, exist_ok=True)
        os.rename(source, os.path.join(destination, name))
model = sys.argv[sys.argv.index("--model") + 1]
print(json.dumps(sys.argv[1:]), file=sys.stderr)
cost = {{"openai/gpt-5.6-sol": 0.10, "anthropic/claude-fable-5": 0.12, "x-ai/grok-4.6": 0.08}}[model]
observed = model
content = [{{"type": "text", "text": {GOOD_OUTPUT!r}}}]
if {variant!r} == "unknown-cost": cost = None
if {variant!r} == "unknown-last" and model == "x-ai/grok-4.6": cost = None
if {variant!r} == "zero-cost": cost = 0.0
if {variant!r} == "negative": cost = -0.01
if {variant!r} == "nan": cost = float("nan")
if {variant!r} == "infinity": cost = float("inf")
if {variant!r} == "over": cost = 0.51
if {variant!r} == "drift": observed = "wrong/model"
if {variant!r} == "nontext": content = [{{"type": "image", "data": "x"}}]
if {variant!r} == "retry": print(json.dumps({{"type": "retry"}}))
if {variant!r} in {{"timeout", "ignore-term", "linger", "detached-ignore-term"}}:
    if {variant!r} in {{"ignore-term", "detached-ignore-term"}}: signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print(os.getpid(), file=open("pi.pid", "w"))
    os.chmod("pi.pid", 0o600)
    child_code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)" if {variant!r} in {{"ignore-term", "linger", "detached-ignore-term"}} else "import time; time.sleep(60)"
    child = subprocess.Popen([sys.executable, "-c", child_code], **({{"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}} if {variant!r} in {{"linger", "detached-ignore-term"}} else {{}}))
    print(child.pid, file=open("child.pid", "w"))
    os.chmod("child.pid", 0o600)
    event(f"CANDIDATE_PIDS {{os.getpid()}} {{child.pid}}")
    if {variant!r} != "linger": time.sleep(60)
message = {{"role":"assistant","provider":"openrouter","model":observed,"responseModel":observed,"stopReason":"stop","usage":{{"cost":{{"total":cost}}}},"content":content}}
if {variant!r} == "structured": message["provider"] = {{"name": "openrouter"}}; message["stopReason"] = ["stop"]
if {variant!r} == "migrating": print(json.dumps({{"type":"session","cwd":os.getcwd()}}))
print(json.dumps({{"type":"message_end","message":message}}))
raise SystemExit(7 if {variant!r} == "nonzero" else 0)
'''
        path.write_text(code)
        path.chmod(0o700)
        return path

    def prepared(self, variant="good"):
        self.counter += 1
        run = self.root / "runs" / f"run-{variant}-{self.counter}"
        demo.prepare(run, self.fake_pi(variant=variant), self.root)
        return run

    def s9(self, run, forecasts=None):
        seal = json.loads((run / "seal.json").read_text())
        value = {
            "schema": "benchmark-lab-x-s9-authorization-1",
            "effect": "candidate_calls_and_spend_s9",
            "authority_id": "TEMOIN-TEMPORAIRE-S9",
            "run": f"runs/{run.name}",
            "seal_sha256": demo._sha(run / "seal.json"),
            "contract_sha256": seal["sources"]["campaign.json"],
            "panel_sha256": seal["artifacts"]["panel.json"],
            "pi": {"binary_sha256": seal["pi"]["sha256"], "version": "0.84.4", "settings_sha256": seal["artifacts"]["settings.json"], "models_sha256": seal["artifacts"]["models.json"]},
            "budget": {"currency": "USD", "cap": 0.5, "price_date": "2026-09-02", "price_source": "TEMOIN TEMPORAIRE", "forecasts": forecasts or {"C1": 0.1, "C2": 0.1, "C3": 0.1}},
        }
        return self.write_json(f"s9-{run.name}.json", value), value

    def collected(self, variant="good"):
        run = self.prepared(variant)
        auth, _ = self.s9(run)
        demo.collect(run, auth, self.root)
        return run

    def reviewed(self, variant="good"):
        run = self.collected(variant)
        demo.review(run, self.root)
        return run

    def decisions(self, run, verdict="SATISFAIT", unsafe=""):
        dossier = json.loads((run / "review.json").read_text())
        findings = {key: {"finding": f"constat {key} {unsafe}", "evidence": "blind-copy"} for key in ["O1", "O2", "O3", "O4", "O5", "O6", "E1", "E2", "E3"]}
        value = {
            "schema": "benchmark-lab-x-decisions-1",
            "review_sha256": demo._sha(run / "review.json"),
            "accepted": True,
            "decisions": [
                {"blind_id": case["blind_id"], "verdict": verdict, "reason": f"motif {unsafe}", "role": "responsable de campagne", "findings": findings, **({"secondary": {"S1": "excellent", "S2": "acceptable"}} if verdict == "SATISFAIT" else {})}
                for case in dossier["cases"]
            ],
        }
        path = self.write_json(f"decisions-{run.name}.json", value)
        return path, value

    def s10(self, run, decisions_path):
        value = {
            "schema": "benchmark-lab-x-s10-authorization-1",
            "effect": "product_execution_and_acceptance_s10",
            "authority_id": "TEMOIN-TEMPORAIRE-S10-DISTINCT",
            "run": f"runs/{run.name}",
            "seal_sha256": demo._sha(run / "seal.json"),
            "review_sha256": demo._sha(run / "review.json"),
            "decisions_sha256": demo._sha(decisions_path),
        }
        return self.write_json(f"s10-{run.name}.json", value), value

    def built(self, unsafe=""):
        run = self.reviewed()
        decisions, _ = self.decisions(run, unsafe=unsafe)
        authority, _ = self.s10(run, decisions)
        demo.build(run, decisions, authority, self.root)
        return run

    def test_01_mail_g0_panel_and_routes(self):
        mail = (demo.PACKAGE_DIR / "mail-thread.md").read_bytes()
        self.assertEqual(hashlib.sha256(mail).hexdigest(), "493fd10e74c937056cfeb532c2531087944fab0d471aef12f8b8a6a77f365cb6")
        campaign = demo._campaign()
        self.assertEqual([item["id"] for item in campaign["obligations"]], ["O1", "O2", "O3", "O4", "O5", "O6"])
        self.assertEqual(campaign["verdicts"], ["SATISFAIT", "NE SATISFAIT PAS", "INDETERMINE"])
        self.assertEqual([item["model"] for item in campaign["panel"]], ["openai/gpt-5.6-sol", "anthropic/claude-fable-5", "x-ai/grok-4.6"])
        self.assertEqual([item["upstream"] for item in campaign["panel"]], ["openai", "anthropic", "xai"])

    def test_02_paths_symlinks_and_private_modes(self):
        pi = self.fake_pi()
        with self.assertRaises(ValueError):
            demo.prepare(self.root / "elsewhere" / "x", pi, self.root)
        with self.assertRaises(ValueError):
            demo.prepare(self.root / "runs", pi, self.root)
        target = self.root / "outside"
        target.mkdir()
        (self.root / "runs" / "linked").symlink_to(target, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            demo.prepare(self.root / "runs" / "linked", pi, self.root)
        run = self.prepared()
        self.assertEqual(stat.S_IMODE(run.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in run.iterdir()))
        (run / "panel.json").chmod(0o644)
        auth, _ = self.s9(run)
        with self.assertRaises(ValueError):
            demo.collect(run, auth, self.root)

    def test_03_pi_absent_unknown_or_wrong_version(self):
        with self.assertRaises(ValueError):
            demo.prepare(self.root / "runs" / "absent", self.root / "missing", self.root)
        with self.assertRaises(ValueError):
            demo.prepare(self.root / "runs" / "wrong", self.fake_pi("0.84.3"), self.root)
        with self.assertRaises(ValueError):
            demo.prepare(self.root / "runs" / "unknown", self.fake_pi("inconnue"), self.root)

    def test_04_effective_pi_configuration_and_argv(self):
        run = self.collected()
        models = json.loads((run / "models.json").read_text())
        overrides = models["providers"]["openrouter"]["modelOverrides"]
        self.assertEqual(set(overrides), {"openai/gpt-5.6-sol", "anthropic/claude-fable-5", "x-ai/grok-4.6"})
        self.assertTrue(all(item["maxTokens"] == 2048 for item in overrides.values()))
        self.assertTrue(all(item["compat"]["maxTokensField"] == "max_tokens" for item in overrides.values()))
        routes = {model: item["compat"]["openRouterRouting"] for model, item in overrides.items()}
        self.assertEqual({model: route["only"] for model, route in routes.items()}, {"openai/gpt-5.6-sol": ["openai"], "anthropic/claude-fable-5": ["anthropic"], "x-ai/grok-4.6": ["xai"]})
        self.assertTrue(all(route["allow_fallbacks"] is False and route["require_parameters"] is True and route["data_collection"] == "allow" for route in routes.values()))
        settings = json.loads((run / "settings.json").read_text())
        self.assertEqual(settings["retry"], {"enabled": False, "maxRetries": 0, "provider": {"maxRetries": 0, "timeoutMs": 300000}})
        self.assertFalse(settings["compaction"]["enabled"])
        argv = json.loads((run / "C1.stderr.txt").read_text())
        self.assertNotIn("--max-tokens", argv)
        for option in ["--no-session", "--no-tools", "--no-extensions", "--no-skills", "--no-context-files", "--no-approve"]:
            self.assertIn(option, argv)
        self.assertEqual(argv[-2], "--")
        self.assertTrue(argv[-1].startswith("---\n"))

    def test_04b_collect_isolates_pi_state_from_raw_evidence(self):
        run = self.collected("migrating")
        self.assertEqual(json.loads((run / "collection.json").read_text())["stop_reason"], "COMPLETE")
        self.assertTrue(all((run / f"{config_id}.stdout.jsonl").is_file() for config_id in ["C1", "C2", "C3"]))
        self.assertFalse((run / "sessions").exists())
        self.assertTrue(all((run / name).stat().st_ino != (run / "pi-agent" / name).stat().st_ino for name in ["settings.json", "models.json"]))
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in (run / "pi-agent").rglob("*") if path.is_dir()))

    def test_05_s9_rejects_hash_forecast_and_cap_drift(self):
        for field, replacement in [("seal_sha256", "0" * 64), ("contract_sha256", "0" * 64), ("panel_sha256", "0" * 64)]:
            run = self.prepared(field)
            path, value = self.s9(run)
            value[field] = replacement
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                demo.collect(run, path, self.root)
        run = self.prepared("forecast")
        path, _ = self.s9(run, {"C1": 0.3, "C2": 0.3, "C3": 0.0})
        with self.assertRaises(ValueError):
            demo.collect(run, path, self.root)
        run = self.prepared("cap")
        path, value = self.s9(run)
        value["budget"]["cap"] = 0.51
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            demo.collect(run, path, self.root)

    def test_06_unknown_invalid_over_budget_and_route_stop(self):
        for variant in ["unknown-cost", "negative", "nan", "infinity", "over", "drift", "retry"]:
            with self.subTest(variant=variant):
                run = self.prepared(variant)
                auth, _ = self.s9(run)
                collection = demo.collect(run, auth, self.root)
                self.assertEqual(len(collection["receipts"]), 1)
                self.assertNotEqual(collection["stop_reason"], "COMPLETE")
                self.assertFalse((run / "C2.started.json").exists())

    def test_07_attempt_marker_survives_timeout_and_kills_group(self):
        run = self.prepared("timeout")
        auth, _ = self.s9(run)
        with mock.patch.object(demo, "TIMEOUT_SECONDS", 0.2):
            collection = demo.collect(run, auth, self.root)
        self.assertTrue((run / "C1.started.json").exists())
        self.assertIn("TIMEOUT", json.loads((run / "C1.receipt.json").read_text())["incident"])
        self.assertEqual(len(collection["receipts"]), 1)
        pid = int((run / "child.pid").read_text())
        time.sleep(0.05)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        with self.assertRaises(FileExistsError):
            demo.collect(run, auth, self.root)

    def test_08_receipts_and_collection_are_collect_derived_and_tamper_evident(self):
        run = self.collected()
        receipt = run / "C1.receipt.json"
        value = json.loads(receipt.read_text())
        value["cost"] = 0.0
        receipt.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            demo.review(run, self.root)

    def test_09_review_is_permuted_blind_and_sealed(self):
        run = self.collected()
        collection = json.loads((run / "collection.json").read_text())
        dossier = demo.review(run, self.root)
        self.assertEqual(len(dossier["checklist"]["obligations"]), 6)
        self.assertEqual(len(dossier["checklist"]["fatal_errors"]), 3)
        serialized = json.dumps(dossier)
        self.assertEqual(set(dossier), {"schema", "review_map_sha256", "cases", "checklist", "created_at"})
        self.assertTrue(all(set(case) == {"blind_id", "copy", "copy_sha256"} for case in dossier["cases"]))
        for receipt in collection["receipts"]:
            self.assertNotIn(receipt["sha256"], serialized)
            self.assertNotIn(json.loads((run / receipt["path"]).read_text())["final_text_sha256"], serialized)
        for model in ["openai/gpt-5.6-sol", "anthropic/claude-fable-5", "x-ai/grok-4.6"]:
            self.assertNotIn(model, serialized)
        copy = run / dossier["cases"][0]["copy"]
        copy.write_text("altéré")
        decisions, _ = self.decisions(run)
        authority, _ = self.s10(run, decisions)
        with self.assertRaises(ValueError):
            demo.build(run, decisions, authority, self.root)

    def test_10_decisions_acceptance_s10_and_attributable_satisfied(self):
        run = self.reviewed()
        decisions, value = self.decisions(run)
        value["accepted"] = False
        decisions.write_text(json.dumps(value))
        authority, _ = self.s10(run, decisions)
        with self.assertRaises(ValueError):
            demo.build(run, decisions, authority, self.root)
        run = self.reviewed("nonzero")
        decisions, _ = self.decisions(run)
        authority, _ = self.s10(run, decisions)
        with self.assertRaises(ValueError):
            demo.build(run, decisions, authority, self.root)
        run = self.reviewed()
        decisions, _ = self.decisions(run)
        authority, value = self.s10(run, decisions)
        value["authority_id"] = "TEMOIN-TEMPORAIRE-S9"
        authority.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            demo.build(run, decisions, authority, self.root)

    def test_11_immutable_final_artifacts_and_show_bytes(self):
        run = self.built()
        before = {name: (run / name).read_bytes() for name in ["results.json", "index.html", "final-seal.json"]}
        with self.assertRaises(FileExistsError):
            demo.collect(run, self.root / "unused", self.root)
        with mock.patch("subprocess.run") as opened:
            opened.return_value.returncode = 0
            demo.show(run, self.root)
            opened.assert_called_once_with(["open", str(run / "index.html")], check=False)
        self.assertEqual(before, {name: (run / name).read_bytes() for name in before})
        decisions = run / "decisions.json"
        authority = run / "authorization-s10.json"
        with self.assertRaises(FileExistsError):
            demo.build(run, decisions, authority, self.root)

    def test_12_economy_provenance_and_autonomous_escaped_html(self):
        run = self.built("<script>alert(1)</script>")
        results = json.loads((run / "results.json").read_text())
        self.assertEqual(results["economy"]["status"], "COMPLETE")
        self.assertEqual(len(results["economy"]["least_expensive"]), 1)
        self.assertEqual(set(results["fingerprints"]), {"contract", "panel", "seal", "collection", "review", "decisions", "authority_s9", "authority_s10"})
        page = (run / "index.html").read_text()
        self.assertEqual(page.count('data-step="'), 2)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        for forbidden in ["fetch(", "http://", "https://", "Atlas Direct", GOOD_OUTPUT, "responseModel", "{&quot;", "{&#x27;"]:
            self.assertNotIn(forbidden, page)
        for required in ["@media", "focus-visible", "non communiqué", "route", "effort", "Empreintes", "limite", 'class="bar"', "Vérifier cette restitution"]:
            self.assertIn(required.lower(), page.lower())

    def test_12b_human_ui_maps_models_states_costs_and_secondary_criteria(self):
        run = self.built()
        results = json.loads((run / "results.json").read_text())
        failed = results["configurations"][1]
        failed["verdict"] = "NE SATISFAIT PAS"
        failed["secondary"] = None
        results["economy"] = {
            "status": "COMPLETE",
            "known_costs": {
                results["configurations"][0]["blind_id"]: results["configurations"][0]["cost"],
                results["configurations"][2]["blind_id"]: results["configurations"][2]["cost"],
            },
            "least_expensive": [results["configurations"][0]["blind_id"]],
            "benefits": {},
        }
        results["configurations"].reverse()
        page = demo._render_html(results).decode()
        self.assertLess(page.index('<span class="config-id">C1</span>'), page.index('<span class="config-id">C2</span>'))
        self.assertIn("C1 · openai/gpt-5.6-sol", page)
        self.assertIn("verdict--success", page)
        self.assertIn("verdict--failure", page)
        self.assertIn("Clarté des décisions et des statuts", page)
        self.assertIn("Rigueur sur les montants et les échéances", page)
        self.assertIn("Toutes configurations : dépense observée", page)
        self.assertIn("Hors recommandation : dépense observée", page)
        self.assertIn("Dépense observée, exclue de la recommandation", page)
        self.assertIn("Prise en compte dans la recommandation", page)
        self.assertIn("Aucun incident constaté", page)
        self.assertNotIn("D-8D6C6B58B0DA", page)
        self.assertNotIn("AUCUN", page)
        self.assertNotIn("sans objet", page)

    def test_12o_duration_units_preserve_the_exact_limit(self):
        cases = [
            (0, "0 s"), (1, "1 s"), (59, "59 s"), (60, "1 min"),
            (61, "1 min 1 s"), (300, "5 min"), (3599, "59 min 59 s"),
            (3600, "1 h"), (3661, "1 h 1 min 1 s"), (7200, "2 h"),
            (None, "Non communiqué"), ("INCONNU", "Non communiqué"),
            (-1, "Non communiqué"), (True, "Non communiqué"),
        ]
        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(demo._human_duration(seconds), expected)
        run = self.built()
        raw = (run / "results.json").read_bytes()
        results = json.loads(raw)
        page = demo._render_html(results).decode()
        self.assertIn("<dt>Durée maximale</dt><dd>5 min</dd>", page)
        self.assertNotIn("<dd>300 secondes</dd>", page)
        self.assertEqual(results["conditions"]["requested"]["timeout_seconds"], 300)
        self.assertEqual((run / "results.json").read_bytes(), raw)

    def test_12c_presentation_run_preserves_the_sealed_source(self):
        source = self.built()
        before = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
        target = self.root / "runs" / "ui-r1"
        result = demo.present(source, target, self.root)
        self.assertEqual(result["source_run"], f"runs/{source.name}")
        self.assertEqual(result["run"], "runs/ui-r1")
        self.assertEqual(before, {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()})
        self.assertEqual((target / "results.json").read_bytes(), (source / "results.json").read_bytes())
        self.assertEqual(demo._validate_presentation(target, "runs/ui-r1", self.root), target / "index.html")
        with mock.patch("subprocess.run") as opened:
            opened.return_value.returncode = 0
            demo.show(target, self.root)
            opened.assert_called_once_with(["open", str(target / "index.html")], check=False)
        with self.assertRaises(FileExistsError):
            demo.present(source, target, self.root)

    def test_12d_render_scales_without_special_case_for_fifteen_configurations(self):
        run = self.built()
        results = json.loads((run / "results.json").read_text())
        prototype = results["configurations"][0]
        configurations = []
        panel = []
        costs = {}
        for index in range(1, 16):
            item = json.loads(json.dumps(prototype))
            item["blind_id"] = f"D-{index:012X}"
            item["requested"]["id"] = f"C{index}"
            item["requested"]["model"] = f"provider/model-{index}"
            item["observed"]["model"] = f"provider/model-{index}"
            item["cost"] = index / 1000
            configurations.append(item)
            panel.append(item["requested"])
            costs[item["blind_id"]] = item["cost"]
        results["configurations"] = configurations
        results["contract"]["panel"] = panel
        results["economy"] = {"status": "COMPLETE", "known_costs": costs, "least_expensive": [configurations[0]["blind_id"]], "benefits": {}}
        page = demo._render_html(results).decode()
        self.assertEqual(page.count('<article class="result '), 15)
        self.assertIn("C15", page)
        self.assertIn("provider/model-15", page)
        self.assertEqual(page.count('data-step="'), 2)

    def fifteen(self, results, verdicts, unknown_cost=None):
        prototype, failed = results["configurations"][0], json.loads(json.dumps(results["configurations"][0]))
        failed["secondary"] = None
        configurations, panel = [], []
        for index, verdict in enumerate(verdicts, 1):
            item = json.loads(json.dumps(prototype if verdict == "SATISFAIT" else failed))
            item["blind_id"] = f"D-{index:012X}"
            item["verdict"] = verdict
            item["reason"] = f"motif synthétique {index}"
            item["requested"]["id"] = f"C{index}"
            item["requested"]["model"] = f"fournisseur-{index}/modele-synthetique-{index}"
            item["observed"]["model"] = item["requested"]["model"]
            item["cost"] = "INCONNU" if index == unknown_cost else index / 1000
            configurations.append(item)
            panel.append(item["requested"])
        results["configurations"] = list(reversed(configurations))
        results["contract"]["panel"] = panel
        satisfied = [item for item in configurations if item["verdict"] == "SATISFAIT"]
        known = {item["blind_id"]: item["cost"] for item in satisfied if item["cost"] != "INCONNU"}
        if unknown_cost and any(item["verdict"] == "SATISFAIT" and item["cost"] == "INCONNU" for item in configurations):
            results["economy"] = {"status": "INCOMPLETE", "known_costs": known, "least_expensive": [], "benefits": {}}
        else:
            results["economy"] = {"status": "COMPLETE", "known_costs": known, "least_expensive": [satisfied[0]["blind_id"]], "benefits": {}}
        return configurations

    def test_12e_fifteen_configurations_keep_two_readable_steps(self):
        run = self.built()
        results = json.loads((run / "results.json").read_text())
        verdicts = ["SATISFAIT", "NE SATISFAIT PAS", "SATISFAIT", "INDETERMINE"] * 3 + ["SATISFAIT", "SATISFAIT", "NE SATISFAIT PAS"]
        configurations = self.fifteen(results, verdicts)
        page = demo._render_html(results).decode()
        self.assertIn("<title>Synthèse d’un fil de courriels de devis · Salle de décision", page)
        self.assertIn("<h1>Synthèse d’un fil de courriels de devis</h1>", page)
        self.assertEqual(page.count('data-step="'), 2)
        step1 = page[page.index('data-step="1"'):page.index('data-step="2"')]
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        verify = page[page.index('id="verify"'):]
        positions = [step1.index(f'<span class="config-id">C{index}</span>') for index in range(1, 16)]
        self.assertEqual(positions, sorted(positions))
        for index, item in enumerate(configurations, 1):
            self.assertEqual(step1.count(f"<summary>Examiner le résultat C{index}</summary>"), 1)
            self.assertIn(item["reason"], step1)
            self.assertIn(item["verdict"], step1)
            self.assertIn(f"C{index} · fournisseur-{index}/modele-synthetique-{index}", step2)
            self.assertIn(f"<h4>C{index} · fournisseur-{index}/modele-synthetique-{index}</h4>", verify)
        self.assertEqual(step1.count("<summary>"), 15)
        self.assertNotIn("USD", step1)
        self.assertNotIn("Coût", step1)
        self.assertEqual(step2.count("Prise en compte dans la recommandation"), 8)
        self.assertEqual(step2.count("Dépense observée, exclue de la recommandation"), 7)
        self.assertEqual(step2.count('class="bar"'), 15)
        self.assertEqual(step2.count("<li class=\"excluded\">"), 7)
        self.assertIn("La configuration la moins chère parmi celles qui satisfont le contrat est C1 · fournisseur-1/modele-synthetique-1", step2)
        self.assertIn("0,0150 USD", step2)
        self.assertIn("Hors recommandation : dépense observée", step2)
        self.assertIn("Configurations admissibles : dépense observée", step2)
        main_path = page[:page.index('id="verify"')]
        for forbidden in ["Darwin", "arm64", "Python", "sha256", results["conditions"]["observed"]["git_head"], configurations[0]["fingerprints"]["receipt"]]:
            self.assertNotIn(forbidden, main_path)
        for required in ["Darwin", "arm64", results["conditions"]["observed"]["git_head"], configurations[14]["fingerprints"]["raw_jsonl"]]:
            self.assertIn(required, verify)
        self.assertNotIn("<meter", page)
        self.assertNotIn("title=", page)

    def test_12f_unknown_admissible_cost_keeps_incomplete_conclusion_and_shows_every_cost(self):
        run = self.built()
        results = json.loads((run / "results.json").read_text())
        verdicts = ["SATISFAIT"] * 13 + ["SATISFAIT", "NE SATISFAIT PAS"]
        self.fifteen(results, verdicts, unknown_cost=14)
        page = demo._render_html(results).decode()
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertIn("Conclusion économique INCOMPLETE", step2)
        self.assertNotIn("la moins chère", step2)
        self.assertEqual(step2.count("Non communiqué"), 1)
        self.assertIn("Coût non communiqué : la recommandation reste incomplète", step2)
        self.assertEqual(step2.count("Coût connu ; recommandation suspendue (conclusion INCOMPLETE)"), 13)
        self.assertEqual(step2.count('class="bar bar--none"'), 1)
        self.assertIn("Toutes configurations : dépense connue", step2)
        self.assertIn("1 coût non communiqué sur 15", step2)
        self.assertIn("Configurations admissibles : dépense connue", step2)
        self.assertIn("1 coût non communiqué sur 14", step2)
        self.assertIn("Hors recommandation : dépense observée", step2)
        self.assertNotIn("dépense totale", step2.lower())
        self.assertIn("1 coût non communiqué sans barre", step2)
        # coût inconnu d'une configuration non admissible : visible, exclue, sans effet sur la conclusion
        results["configurations"][-1]["cost"] = "INCONNU"
        results["configurations"][-1]["verdict"] = "NE SATISFAIT PAS"
        page = demo._render_html(results).decode()
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertIn("Coût non communiqué, exclue de la recommandation", step2)
        self.assertIn("Hors recommandation : dépense connue", step2)
        self.assertIn("1 coût non communiqué sur 2", step2)
        self.assertIn("2 coûts non communiqués sur 15", step2)
        self.assertIn("2 coûts non communiqués sans barre", step2)
        # plusieurs coûts inconnus admissibles
        for item in results["configurations"][2:5]:
            item["cost"] = "INCONNU"
        page = demo._render_html(results).decode()
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertEqual(step2.count("Coût non communiqué : la recommandation reste incomplète"), 4)
        self.assertIn("4 coûts non communiqués sur 13", step2)
        self.assertIn("5 coûts non communiqués sur 15", step2)
        # aucun coût connu : ni barre, ni maximum fictif, ni somme
        for item in results["configurations"]:
            item["cost"] = "INCONNU"
        results["economy"] = {"status": "INCOMPLETE", "known_costs": {}, "least_expensive": [], "benefits": {}}
        page = demo._render_html(results).decode()
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertNotIn('class="bar', step2)
        self.assertNotIn("0,0000 USD", step2)
        self.assertNotIn("--w:", step2)
        self.assertIn("Aucun coût communiqué : pas d’échelle commune ni de barre.", step2)
        self.assertEqual(step2.count("Aucun coût communiqué</strong>"), 3)
        self.assertEqual(step2.count("Non communiqué"), 15)

    def test_12h_full_journey_last_candidate_satisfied_with_unknown_cost(self):
        run = self.prepared("unknown-last")
        auth, _ = self.s9(run)
        collection = demo.collect(run, auth, self.root)
        self.assertEqual(collection["stop_reason"], "COUT_OU_BUDGET_INCONNU")
        self.assertEqual(collection["spent"], "INCONNU")
        self.assertEqual([item["executed"] for item in collection["matrix"]], [True, True, True])
        self.assertEqual(json.loads((run / "C3.receipt.json").read_text())["cost"], "INCONNU")
        demo.review(run, self.root)
        decisions, _ = self.decisions(run)
        authority, _ = self.s10(run, decisions)
        results = demo.build(run, decisions, authority, self.root)
        costs = {item["requested"]["id"]: item["cost"] for item in results["configurations"]}
        self.assertEqual(costs["C3"], "INCONNU")
        self.assertEqual([item["verdict"] for item in results["configurations"]], ["SATISFAIT"] * 3)
        self.assertEqual(results["economy"]["status"], "INCOMPLETE")
        self.assertEqual(results["economy"]["least_expensive"], [])
        self.assertEqual(len(results["economy"]["known_costs"]), 2)
        page = (run / "index.html").read_text()
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertIn("Conclusion économique INCOMPLETE", step2)
        self.assertNotIn("la moins chère", step2)
        self.assertIn("C3 · x-ai/grok-4.6", step2)
        self.assertIn("Coût non communiqué : la recommandation reste incomplète", step2)
        self.assertEqual(step2.count("Coût connu ; recommandation suspendue (conclusion INCOMPLETE)"), 2)
        self.assertIn("1 coût non communiqué sur 3", step2)
        self.assertIn("coût non communiqué par le canal observé", page)
        self.assertNotIn("COUT_INCONNU_OU_INVALIDE", page)
        self.assertNotIn("0,0000 USD", step2)

    def test_12g_brief_is_frozen_by_campaign_or_falls_back_to_task_id(self):
        run = self.built()
        results = json.loads((run / "results.json").read_text())
        results["contract"]["task_id"] = "tache-future"
        results["task"] = "tache-future"
        page = demo._render_html(results).decode()
        self.assertIn("<h1>tache-future</h1>", page)
        self.assertIn("Non documenté : aucun brief figé avant exécution.", page)
        self.assertIn("Cette tâche n’a pas de brief figé avant exécution", page)
        results["contract"]["brief"] = {"title": "Titre <figé>", "context": "Situation figée", "objective": "Objectif figé", "decision": "Décision figée"}
        page = demo._render_html(results).decode()
        self.assertIn("<h1>Titre &lt;figé&gt;</h1>", page)
        for required in ["Situation figée", "Objectif figé", "Décision figée", "<dt>Résultat attendu</dt><dd>Une synthèse fidèle du fil historique selon le contrat G0.</dd>"]:
            self.assertIn(required, page)
        self.assertNotIn("Non documenté", page)
        self.assertNotIn("rédigé après la campagne", page)
        for bad in [{"title": "x"}, {**results["contract"]["brief"], "context": " "}, {**results["contract"]["brief"], "extra": "y"}, {**results["contract"]["brief"], "decision": 3}, {**results["contract"]["brief"], "expected": "remplacement du contrat"}]:
            results["contract"]["brief"] = bad
            with self.assertRaises(ValueError):
                demo._render_html(results)
        results["contract"]["brief"] = None
        results["contract"]["task_id"] = "quote-thread-summary"
        page = demo._render_html(results).decode()
        self.assertIn("Ce brief de présentation a été rédigé après la campagne", page)
        self.assertEqual(page.count("Synthèse d’un fil de courriels de devis"), 2)

    def test_12i_zero_cost_is_a_communicated_cost(self):
        run = self.prepared("zero-cost")
        auth, _ = self.s9(run)
        collection = demo.collect(run, auth, self.root)
        self.assertEqual(collection["stop_reason"], "COMPLETE")
        self.assertEqual(collection["spent"], 0.0)
        demo.review(run, self.root)
        decisions, _ = self.decisions(run)
        authority, _ = self.s10(run, decisions)
        results = demo.build(run, decisions, authority, self.root)
        self.assertEqual(results["economy"]["status"], "COMPLETE")
        self.assertEqual(len(results["economy"]["least_expensive"]), 3)
        page = (run / "index.html").read_text()
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertIn("co-moins-chères", step2)
        self.assertNotIn("Aucun coût communiqué", step2)
        self.assertNotIn("Non communiqué", step2)
        self.assertEqual(step2.count("0,0000 USD"), 3 + 2)
        self.assertIn("Hors recommandation</span><strong>Aucune configuration</strong>", step2)
        self.assertEqual(step2.count('style="--w:0.0%"'), 3)
        self.assertIn("Tous les coûts communiqués sont nuls : barres vides, sans échelle.", step2)
        self.assertIn("Toutes configurations : dépense observée", step2)
        # coût nul parmi des coûts positifs : barre vide à l'échelle du maximum réel
        results["configurations"][1]["cost"] = 0.01
        results["economy"]["known_costs"][results["configurations"][1]["blind_id"]] = 0.01
        results["economy"]["least_expensive"] = [results["configurations"][0]["blind_id"], results["configurations"][2]["blind_id"]]
        page = demo._render_html(results).decode()
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertIn("coût connu le plus élevé (0,0100 USD)", step2)
        self.assertEqual(step2.count('style="--w:0.0%"'), 2)
        self.assertEqual(step2.count('style="--w:100.0%"'), 1)
        # coût nul avec un coût inconnu : le nul reste communiqué, l'inconnu reste sans barre
        results["configurations"][2]["cost"] = "INCONNU"
        results["economy"] = {"status": "INCOMPLETE", "known_costs": {}, "least_expensive": [], "benefits": {}}
        page = demo._render_html(results).decode()
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertEqual(step2.count("Non communiqué"), 1)
        self.assertEqual(step2.count('class="bar bar--none"'), 1)
        self.assertIn("1 coût non communiqué sans barre", step2)

    def test_12j_structured_provider_observations_never_reach_the_page(self):
        expected = {"id": "C1", "provider": "openrouter", "model": "openai/gpt-5.6-sol", "upstream": "openai", "thinking": "high"}
        message = {"role": "assistant", "provider": {"name": "openrouter"}, "model": ["openai/gpt-5.6-sol"], "responseModel": 42, "stopReason": {"reason": "stop"}, "usage": {"cost": {"total": 0.1}}, "content": [{"type": "text", "text": "x"}]}
        parsed = demo._attempt_result((json.dumps({"type": "message_end", "message": message}) + "\n").encode(), expected)
        self.assertEqual(parsed["observed"], {"provider": "INCONNU", "model": "INCONNU", "responseModel": "INCONNU", "stopReason": "INCONNU"})
        self.assertIn("OBSERVATION_NON_TEXTUELLE", parsed["incident"])
        self.assertIn("IDENTITE_DIVERGENTE", parsed["incident"])
        message["provider"], message["model"], message["responseModel"], message["stopReason"] = "openrouter", "openai/gpt-5.6-sol", " ", "stop"
        parsed = demo._attempt_result((json.dumps({"type": "message_end", "message": message}) + "\n").encode(), expected)
        self.assertEqual(parsed["observed"]["responseModel"], "INCONNU")
        self.assertEqual(parsed["incident"], "OBSERVATION_NON_TEXTUELLE")
        run = self.prepared("structured")
        auth, _ = self.s9(run)
        collection = demo.collect(run, auth, self.root)
        receipt = json.loads((run / "C1.receipt.json").read_text())
        self.assertEqual(receipt["observed"]["provider"], "INCONNU")
        self.assertEqual(receipt["observed"]["stopReason"], "INCONNU")
        self.assertIn("OBSERVATION_NON_TEXTUELLE", receipt["incident"])
        self.assertEqual(collection["stop_reason"], "PROVENANCE_OU_ROUTE_DIVERGENTE")
        self.assertFalse(demo._safe_satisfied(receipt))
        built = self.built()
        results = json.loads((built / "index.html").exists() and (built / "results.json").read_text())
        item = results["configurations"][0]
        item["observed"].update({"provider": "INCONNU", "model": "INCONNU", "responseModel": "INCONNU", "stopReason": "INCONNU"})
        item["unknowns"] = ["provider", "model", "responseModel", "stopReason", "route", "effort"]
        item["incident"] = "OBSERVATION_NON_TEXTUELLE;IDENTITE_DIVERGENTE"
        item["verdict"], item["secondary"] = "INDETERMINE", None
        results["economy"] = {"status": "COMPLETE", "known_costs": {}, "least_expensive": [], "benefits": {}}
        page = demo._render_html(results).decode()
        for forbidden in ["{&#x27;", "[&#x27;", "{&quot;", "[&quot;", "INCONNU", "OBSERVATION_NON_TEXTUELLE", "IDENTITE_DIVERGENTE", "stopReason", "responseModel"]:
            self.assertNotIn(forbidden, page)
        for required in ["Fournisseur non communiqué par le canal observé", "Modèle non communiqué par le canal observé", "Motif d’arrêt non communiqué par le canal observé", "une observation reçue n’était pas un texte et a été remplacée par une absence", "identité observée divergente de la demande"]:
            self.assertIn(required, page)
        self.assertEqual(page.count("<dt>Fournisseur</dt><dd>Non communiqué</dd>"), 1)

    def test_12k_every_engine_incident_has_a_human_label(self):
        run = self.built()
        results = json.loads((run / "results.json").read_text())
        engine_tokens = {
            "JSONL_INVALIDE: JSON invalide (JSONL ligne 1): Expecting value": "flux de réponse illisible",
            "TOURS_ASSISTANT_INVALIDES": "nombre de tours de réponse invalide",
            "RETRY_DETECTE": "nouvelle tentative détectée",
            "IDENTITE_DIVERGENTE": "identité observée divergente de la demande",
            "RESPONSE_MODEL_DIVERGENT": "modèle de réponse divergent",
            "ARRET_NON_FINAL": "arrêt non final de la réponse",
            "SORTIE_NON_TEXTUELLE": "sortie non textuelle",
            "COUT_INCONNU_OU_INVALIDE": "coût non communiqué par le canal observé",
            "OBSERVATION_NON_TEXTUELLE": "une observation reçue n’était pas un texte",
            "SIGTERM_GROUPE_TUE": "campagne interrompue par le superviseur pendant l’exécution",
            "TIMEOUT_GROUPE_TUE": "durée maximale dépassée, exécution arrêtée",
            "INTERRUPTION_GROUPE_TUE": "exécution interrompue par le dispositif",
            "ERREUR_FOURNISSE": "erreur du fournisseur",
            "PROCESS_START_ERROR": "démarrage du harnais impossible",
        }
        source = demo.PACKAGE_DIR.joinpath("__main__.py").read_text()
        for token in ["JSONL_INVALIDE", "TOURS_ASSISTANT_INVALIDES", "RETRY_DETECTE", "IDENTITE_DIVERGENTE", "RESPONSE_MODEL_DIVERGENT", "ARRET_NON_FINAL", "SORTIE_NON_TEXTUELLE", "COUT_INCONNU_OU_INVALIDE", "OBSERVATION_NON_TEXTUELLE", "SIGTERM_GROUPE_TUE", "TIMEOUT_GROUPE_TUE", "INTERRUPTION_GROUPE_TUE", "ERREUR_FOURNISSE", "PROCESS_START_ERROR"]:
            self.assertIn(f'"{token}', source)
            self.assertIn(token, demo.INCIDENT_LABELS)
        item = results["configurations"][1]
        item["verdict"], item["secondary"] = "INDETERMINE", None
        results["economy"] = {"status": "COMPLETE", "known_costs": {results["configurations"][0]["blind_id"]: results["configurations"][0]["cost"]}, "least_expensive": [results["configurations"][0]["blind_id"]], "benefits": {}}
        for token, label in engine_tokens.items():
            item["incident"] = f"{token};COUT_INCONNU_OU_INVALIDE"
            page = demo._render_html(results).decode()
            self.assertIn(label, page)
            head = token.split(":")[0]
            self.assertNotIn(head, page)
            if head.lower().replace("_", " ") != label:
                self.assertNotIn(head.lower().replace("_", " "), page)
            self.assertNotIn("Expecting value", page)
        item["incident"] = "JETON_FUTUR_INCONNU: détail interne"
        page = demo._render_html(results).decode()
        self.assertIn("incident technique non répertorié", page)
        self.assertNotIn("JETON_FUTUR_INCONNU", page)
        self.assertNotIn("jeton futur inconnu", page)
        self.assertNotIn("détail interne", page)

    def test_12l_ledger_cell_labels_stay_in_the_accessibility_tree_on_desktop(self):
        run = self.built()
        page = (run / "index.html").read_text()
        style = page[page.index("<style>"):page.index("</style>")]
        desktop, mobile = style.split("@media(max-width:760px)")
        desktop_rule = desktop[desktop.index(".ledger .cell-label{"):]
        desktop_rule = desktop_rule[:desktop_rule.index("}")]
        self.assertNotIn("display:none", desktop_rule)
        for required in ["position:absolute", "width:1px", "height:1px", "overflow:hidden", "clip:rect(0 0 0 0)"]:
            self.assertIn(required, desktop_rule)
        self.assertIn(".ledger .cell-label{position:static;width:auto;height:auto;overflow:visible;clip:auto;clip-path:none;white-space:normal;display:block}", mobile)
        self.assertNotIn("display:none", desktop.split(".ledger-head{")[1].split("}")[0])
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertEqual(step2.count('<span class="cell-label">Configuration</span>'), 3)
        self.assertEqual(step2.count('<span class="cell-label">Statut économique</span>'), 3)
        self.assertNotIn("<script", page)
        self.assertNotIn("tabindex", page)

    def test_12n_invalid_streams_still_carry_four_unknown_observations(self):
        expected = {"id": "C1", "provider": "openrouter", "model": "openai/gpt-5.6-sol", "upstream": "openai", "thinking": "high"}
        unknown = {"provider": "INCONNU", "model": "INCONNU", "responseModel": "INCONNU", "stopReason": "INCONNU"}
        invalid = demo._attempt_result(b'{"type": "message_end", "message": ', expected)
        self.assertTrue(invalid["incident"].startswith("JSONL_INVALIDE"))
        self.assertEqual(invalid["observed"], unknown)
        for message in ["texte", ["liste"], 7, None]:
            parsed = demo._attempt_result((json.dumps({"type": "message_end", "message": message}) + "\n").encode(), expected)
            self.assertEqual(parsed["incident"], "TOURS_ASSISTANT_INVALIDES")
            self.assertEqual(parsed["observed"], unknown)
        run = self.built()
        results = json.loads((run / "results.json").read_text())
        for incident in ["JSONL_INVALIDE: JSON invalide (JSONL ligne 1): Expecting value", "TOURS_ASSISTANT_INVALIDES"]:
            item = results["configurations"][1]
            item["observed"] = {**unknown, "route": "INCONNU", "effort": "INCONNU"}
            item["unknowns"] = ["provider", "model", "responseModel", "stopReason", "route", "effort"]
            item["incident"], item["verdict"], item["secondary"], item["cost"] = incident, "INDETERMINE", None, "INCONNU"
            results["economy"] = {"status": "COMPLETE", "known_costs": {results["configurations"][0]["blind_id"]: results["configurations"][0]["cost"]}, "least_expensive": [results["configurations"][0]["blind_id"]], "benefits": {}}
            page = demo._render_html(results).decode()
            for forbidden in ["None", "{}", "[]", "INCONNU", "JSONL_INVALIDE", "TOURS_ASSISTANT_INVALIDES", "Expecting value", "{&#x27;", "[&#x27;", "{&quot;", "[&quot;"]:
                self.assertNotIn(forbidden, page)
            for required in ["<dt>Fournisseur</dt><dd>Non communiqué</dd>", "<dt>Modèle</dt><dd>Non communiqué</dd>", "Fournisseur non communiqué par le canal observé", "Modèle non communiqué par le canal observé", "Modèle de réponse non communiqué par le canal observé", "Motif d’arrêt non communiqué par le canal observé"]:
                self.assertIn(required, page)
            self.assertIn("flux de réponse illisible" if incident.startswith("JSONL") else "nombre de tours de réponse invalide", page)

    def test_12m_historical_brief_is_declared_unproven(self):
        run = self.built()
        page = (run / "index.html").read_text()
        self.assertIn("n’est relié à aucune empreinte de source", page)
        self.assertIn("il n’est pas prouvé par les artefacts de la campagne", page)
        self.assertNotIn("à partir de la tâche et du fil source", page)
        readme = demo.PACKAGE_DIR.joinpath("README.md").read_text()
        self.assertIn("brief de présentation propre, rédigé après coup", readme)
        self.assertIn("toute autre tâche retombe sur son identifiant", readme)

    def test_13_key_barrier_is_reusable_and_leaves_no_s9_artifact(self):
        for missing in [None, ""]:
            with self.subTest(key=repr(missing)):
                run = self.prepared()
                auth, _ = self.s9(run)
                if missing is None:
                    os.environ.pop("OPENROUTER_API_KEY", None)
                else:
                    os.environ["OPENROUTER_API_KEY"] = missing
                with mock.patch.object(demo.subprocess, "Popen") as popen, self.assertRaises(ValueError):
                    demo.collect(run, auth, self.root)
                popen.assert_not_called()
                self.assertFalse(any(run.glob("authorization-s9.json")))
                self.assertFalse(any(run.glob("*.started.json")))
                self.assertFalse(any(run.glob("*.stdout.jsonl")))
                self.assertFalse((run / "collection.json").exists())
                os.environ["OPENROUTER_API_KEY"] = "fausse-cle-en-memoire"
                self.assertEqual(demo.collect(run, auth, self.root)["stop_reason"], "COMPLETE")
                self.assertNotIn(b"fausse-cle-en-memoire", b"".join(path.read_bytes() for path in run.rglob("*") if path.is_file()))

    def test_14_environment_head_and_single_child_environment_are_sealed(self):
        run = self.prepared()
        seal = json.loads((run / "seal.json").read_text())
        self.assertEqual(set(seal["public_environment"]), {"python", "system", "system_version", "architecture", "git_head"})
        self.assertEqual(seal["public_environment_sha256"], demo._value_sha(seal["public_environment"]))
        auth, _ = self.s9(run)
        real_environment = demo._public_environment(self.root)
        calls = 0
        def drift(_root):
            nonlocal calls
            calls += 1
            return {**real_environment, **({"git_head": "f" * 40} if calls >= 3 else {})}
        with mock.patch.object(demo, "_public_environment", side_effect=drift):
            collection = demo.collect(run, auth, self.root)
        self.assertTrue(collection["matrix"][0]["executed"])
        self.assertFalse(collection["matrix"][1]["executed"])
        self.assertFalse(collection["matrix"][2]["executed"])
        self.assertFalse((run / "review").exists())
        with self.assertRaises(ValueError):
            demo.review(run, self.root)
        with self.assertRaises(ValueError):
            demo.build(run, self.root / "none", self.root / "none", self.root)

        run = self.prepared()
        auth, _ = self.s9(run)
        environments = []
        real_popen = demo.subprocess.Popen
        def capture(*args, **kwargs):
            if "env" in kwargs and "--model" in args[0]:
                environments.append(kwargs["env"])
            return real_popen(*args, **kwargs)
        with mock.patch.object(demo.subprocess, "Popen", side_effect=capture):
            demo.collect(run, auth, self.root)
        self.assertEqual(len(environments), 3)
        self.assertTrue(all(item is environments[0] for item in environments))

    def test_15_pi_jsonl_text_blocks_and_forbidden_blocks(self):
        expected = demo._campaign()["panel"][0]
        def result(content):
            message = {"role": "assistant", "provider": expected["provider"], "model": expected["model"], "responseModel": expected["model"], "stopReason": "stop", "usage": {"cost": {"total": 0.1}}, "content": content}
            return demo._attempt_result((json.dumps({"type": "message_end", "message": message}) + "\n").encode(), expected)
        parsed = result([{"type": "thinking", "thinking": "secret"}, {"type": "text", "text": "A"}, {"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "B"}])
        self.assertEqual(parsed["output"], "AB")
        self.assertEqual(parsed["final_text_sha256"], hashlib.sha256(b"AB").hexdigest())
        for content in [
            [{"type": "thinking", "thinking": "only"}],
            [{"type": "text", "text": "A"}, {"type": "toolCall", "name": "x"}],
            [{"type": "unknown", "value": "x"}],
        ]:
            with self.subTest(content=content):
                parsed = result(content)
                self.assertEqual(parsed["output"], "INCONNU")
                self.assertIsNone(parsed["final_text_sha256"])
                self.assertIn("SORTIE_NON_TEXTUELLE", parsed["incident"])

    def test_16_total_matrix_partial_gate_and_provider_error_completion(self):
        run = self.prepared("drift")
        auth, _ = self.s9(run)
        collection = demo.collect(run, auth, self.root)
        self.assertEqual([item["config_id"] for item in collection["matrix"]], ["C1", "C2", "C3"])
        self.assertEqual([item["executed"] for item in collection["matrix"]], [True, False, False])
        with self.assertRaises(ValueError):
            demo.review(run, self.root)
        self.assertFalse((run / "review").exists())
        self.assertFalse((run / "review.json").exists())

        run = self.collected("nonzero")
        collection = json.loads((run / "collection.json").read_text())
        self.assertTrue(all(item["executed"] for item in collection["matrix"]))
        self.assertEqual(len(collection["receipts"]), 3)
        self.assertTrue(all("ERREUR_FOURNISSE" in json.loads((run / item["path"]).read_text())["incident"] for item in collection["receipts"]))
        self.assertEqual(len(demo.review(run, self.root)["cases"]), 3)

    def test_17_private_review_map_tamper_identical_outputs_and_identity_shuffle(self):
        run = self.collected()
        with mock.patch.object(demo.secrets.SystemRandom, "shuffle", lambda _self, _order: None):
            dossier = demo.review(run, self.root)
        copies = [(run / case["copy"]).read_bytes() for case in dossier["cases"]]
        self.assertEqual(len(set(copies)), 3)
        self.assertTrue(all(copy.endswith(GOOD_OUTPUT.encode()) for copy in copies))
        review_map = json.loads((run / "review-map.json").read_text())
        self.assertEqual({item["blind_id"] for item in review_map["cases"]}, {item["blind_id"] for item in dossier["cases"]})
        self.assertEqual(len({item["receipt_sha256"] for item in review_map["cases"]}), 3)
        decisions, _ = self.decisions(run)
        authority, _ = self.s10(run, decisions)
        demo.build(run, decisions, authority, self.root)

        run = self.reviewed()
        table = run / "review-map.json"
        value = json.loads(table.read_text())
        value["cases"][0]["receipt_sha256"] = "0" * 64
        table.write_text(json.dumps(value))
        decisions, _ = self.decisions(run)
        authority, _ = self.s10(run, decisions)
        with self.assertRaises(ValueError):
            demo.build(run, decisions, authority, self.root)

    def test_18_sigterm_kills_pi_and_child_and_closes_collection(self):
        run, temporary, process, sock, stream = _start_supervisor(self, self, "timeout")
        events = _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events += _until(self, stream, "CANDIDATE_PIDS")
        pids = _event_pids(events, "CANDIDATE_PIDS")
        exits = _watch_exits(self, pids)
        os.kill(process.pid, signal.SIGTERM)
        _until(self, stream, "INTERRUPTED_ARBITRATED")
        _assert_exit_events(self, exits, pids)
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], "collect interrompu par SIGTERM")
        collection = json.loads((run / "collection.json").read_text())
        self.assertEqual(collection["stop_reason"], "INTERRUPTION_SIGTERM")
        self.assertEqual([item["executed"] for item in collection["matrix"]], [True, False, False])
        receipt = json.loads((run / "C1.receipt.json").read_text())
        self.assertIn("SIGTERM", receipt["incident"])
        self.assertFalse(receipt["retry"])
        self.assertFalse((run / "C2.started.json").exists())
        _assert_dead(self, pids)
        self.assertFalse(list(temporary.glob("benchmark-lab-x-collect-*")))
        self.assertFalse(list(run.glob(".collection-*.tmp")))
        with self.assertRaises(FileExistsError):
            demo.collect(run, self.root / "unused", self.root)

    def test_18_programmatic_collect_does_not_take_signal_ownership(self):
        run = self.prepared()
        auth, _ = self.s9(run)
        previous = signal.getsignal(signal.SIGTERM)
        self.assertEqual(demo.collect(run, auth, self.root)["stop_reason"], "COMPLETE")
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_19_help_and_offline_commands_never_launch_candidate(self):
        completed = subprocess.run([sys.executable, "-B", "-m", "benchmark", "--help"], cwd=demo.PACKAGE_DIR.parent, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0)
        for command in ["prepare", "collect", "review", "build", "present", "show"]:
            self.assertIn(command, completed.stdout)
        run = self.reviewed()
        decisions, _ = self.decisions(run)
        authority, _ = self.s10(run, decisions)
        review_only = self.collected()
        candidate_calls = []
        real_popen = demo.subprocess.Popen
        def capture(*args, **kwargs):
            if "--model" in args[0]:
                candidate_calls.append(args[0])
            return real_popen(*args, **kwargs)
        with mock.patch.object(demo.subprocess, "Popen", side_effect=capture):
            demo.review(review_only, self.root)
            demo.build(run, decisions, authority, self.root)
            with mock.patch.object(demo.subprocess, "run") as opened:
                opened.return_value.returncode = 0
                demo.show(run, self.root)
        self.assertEqual(candidate_calls, [])

    def test_20_complete_provenance_and_page_excludes_private_material(self):
        run = self.built()
        results = json.loads((run / "results.json").read_text())
        self.assertEqual(set(results["conditions"]["observed"]), {"pi_version", "python", "system", "system_version", "architecture", "git_head"})
        self.assertTrue({"seal", "collection"} <= set(results["fingerprints"]))
        for item in results["configurations"]:
            self.assertEqual(set(item["fingerprints"]), {"raw_jsonl", "final_text", "receipt", "blind_copy"})
        page = (run / "index.html").read_text()
        self.assertEqual(page.count('data-step="'), 2)
        for forbidden in ["fausse-cle-en-memoire", (run / "prompt.txt").read_text(), GOOD_OUTPUT, "stdout.jsonl", "review-map.json", "fetch(", "XMLHttpRequest", "WebSocket", "http://", "https://"]:
            self.assertNotIn(forbidden, page)
        for required in ["Résultat attendu", "6 obligations", "3 erreurs éliminatoires", "0,50 USD", "min-width:0", "overflow-x:hidden", "focus-visible"]:
            self.assertIn(required, page)


class IsolatedSupervisorSigtermTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(platform.system(), "Darwin", "la preuve d’acceptation exige macOS")
        self.fixture = DemoTest()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def _start(self, variant="good"):
        run = self.fixture.prepared(variant)
        authority, _ = self.fixture.s9(run)
        temporary = self.fixture.root / "tmp"
        temporary.mkdir()
        parent, child = socket.socketpair()
        env = {
            **os.environ,
            "TMPDIR": str(temporary),
            "BENCHMARK_LAB_X_TEST_SOCKET_FD": str(child.fileno()),
            "BENCHMARK_LAB_X_TEST_REPO_ROOT": str(self.fixture.root),
        }
        process = subprocess.Popen(
            [sys.executable, "-B", "-m", "benchmark", "collect", "--run-dir", str(run), "--authority", str(authority)],
            cwd=demo.PACKAGE_DIR.parent, env=env, pass_fds=(child.fileno(),),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        child.close()
        parent.settimeout(5)
        stream = parent.makefile("rb", buffering=0)
        self.addCleanup(parent.close)
        self.addCleanup(stream.close)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return run, temporary, process, parent, stream

    def _until(self, stream, milestone):
        events = []
        while not events or not events[-1].startswith(milestone):
            line = stream.readline()
            self.assertTrue(line, f"jalon absent: {milestone}")
            events.append(line.decode().strip())
        return events

    def _assert_dead(self, pids):
        for pid in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_sigterm_after_private_validation_publishes_only_interruption(self):
        run, temporary, process, sock, stream = self._start()
        events = self._until(stream, "OWNED")
        sock.sendall(b"G")
        events += self._until(stream, "BEFORE_COMPLETE_LINK")
        os.kill(process.pid, signal.SIGTERM)
        events += self._until(stream, "INTERRUPTED_ARBITRATED")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], "collect interrompu par SIGTERM")
        self.assertEqual(events[0], "OWNED")
        collection = json.loads((run / "collection.json").read_text())
        self.assertEqual(collection["stop_reason"], "INTERRUPTION_SIGTERM")
        self.assertNotIn("COMPLETE", stdout)
        self.assertFalse(list(temporary.glob("benchmark-lab-x-collect-*")))
        self.assertFalse(list(run.glob(".collection-*.tmp")))

    def test_sigterm_stops_active_worker_group_without_retry(self):
        run, temporary, process, sock, stream = self._start("timeout")
        events = self._until(stream, "OWNED")
        sock.sendall(b"G")
        events += self._until(stream, "CANDIDATE_PIDS")
        pids = _event_pids(events, "CANDIDATE_PIDS")
        os.kill(process.pid, signal.SIGTERM)
        self._until(stream, "INTERRUPTED_ARBITRATED")
        self._until(stream, "BEFORE_COMPLETE_LINK")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], "collect interrompu par SIGTERM")
        collection = json.loads((run / "collection.json").read_text())
        self.assertEqual(collection["stop_reason"], "INTERRUPTION_SIGTERM")
        self.assertEqual([item["executed"] for item in collection["matrix"]], [True, False, False])
        self.assertFalse(json.loads((run / "C1.receipt.json").read_text())["retry"])
        self.assertFalse(any(run.glob("C[23].*")))
        self._assert_dead(pids)
        self.assertFalse(list(temporary.glob("benchmark-lab-x-collect-*")))

    def test_normal_supervisor_publishes_one_complete_collection(self):
        run, temporary, process, sock, stream = self._start()
        events = self._until(stream, "OWNED")
        sock.sendall(b"G")
        events += self._until(stream, "BEFORE_COMPLETE_LINK")
        sock.sendall(b"G")
        events += self._until(stream, "COMPLETE_LINKED")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["stop_reason"], "COMPLETE")
        self.assertEqual(stderr, "")
        self.assertEqual(events[0], "OWNED")
        self.assertEqual(len(list(run.glob("collection.json"))), 1)
        self.assertEqual(json.loads((run / "collection.json").read_text())["stop_reason"], "COMPLETE")
        self.assertFalse(list(temporary.glob("benchmark-lab-x-collect-*")))


class FailClosedSupervisorSigtermTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(platform.system(), "Darwin", "la preuve d’acceptation exige macOS")
        self.fixture = DemoTest()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def _start(self, variant="good", inherited_ignore=False):
        return _start_supervisor(self, self.fixture, variant, inherited_ignore)

    def test_ignored_worker_sigterm_still_publishes_interruption(self):
        run, temporary, process, sock, stream = self._start("ignore-term")
        events = _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events += _until(self, stream, "CANDIDATE_PIDS")
        pids = _event_pids(events, "CANDIDATE_PIDS")
        os.kill(process.pid, signal.SIGTERM)
        _until(self, stream, "INTERRUPTED_ARBITRATED")
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], "collect interrompu par SIGTERM")
        self.assertEqual(json.loads((run / "collection.json").read_text())["stop_reason"], "INTERRUPTION_SIGTERM")
        _assert_dead(self, pids)
        self.assertFalse(list(temporary.glob("benchmark-lab-x-collect-*")))

    def test_sigterm_after_owned_is_arbitrated_before_worker_start(self):
        run, temporary, process, sock, stream = self._start()
        events = _until(self, stream, "OWNED")
        os.kill(process.pid, signal.SIGTERM)
        events += _until(self, stream, "INTERRUPTED_ARBITRATED")
        sock.sendall(b"G")
        events += _until(self, stream, "BEFORE_COMPLETE_LINK")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], "collect interrompu par SIGTERM")
        self.assertEqual(events[:5], ["OWNED", "INTERRUPTED_SEEN", "PGID_QUIESCENT", "PGID_GONE", "INTERRUPTED_ARBITRATED"])
        collection = json.loads((run / "collection.json").read_text())
        self.assertEqual(collection["stop_reason"], "INTERRUPTION_SIGTERM")
        self.assertEqual([item["executed"] for item in collection["matrix"]], [False, False, False])
        self.assertFalse(list(run.glob("*.started.json")))
        self.assertFalse(list(temporary.glob("benchmark-lab-x-collect-*")))

    def test_sigterm_before_complete_link_cannot_commit_complete(self):
        run, _, process, sock, stream = self._start()
        _until(self, stream, "OWNED")
        sock.sendall(b"G")
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        os.kill(process.pid, signal.SIGTERM)
        _until(self, stream, "INTERRUPTED_ARBITRATED")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], "collect interrompu par SIGTERM")
        persisted = json.loads((run / "collection.json").read_text())
        self.assertEqual(persisted["stop_reason"], "INTERRUPTION_SIGTERM")
        self.assertNotIn("COMPLETE", stdout + stderr)
        self.assertNotIn("COMPLETE", (run / "collection.json").read_text())

    def test_sigterm_after_complete_link_is_post_terminal(self):
        run, _, process, sock, stream = self._start()
        _until(self, stream, "OWNED")
        sock.sendall(b"G")
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        sock.sendall(b"G")
        _until(self, stream, "COMPLETE_LINKED")
        before = (run / "collection.json").read_bytes()
        os.kill(process.pid, signal.SIGTERM)
        post_events = _until(self, stream, "POST_TERMINAL_SIGTERM")
        self.assertFalse(any(event in {"INTERRUPTED_ARBITRATED", "PGID_GONE"} for event in post_events))
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout)["stop_reason"], "COMPLETE")
        self.assertEqual((run / "collection.json").read_bytes(), before)
        self.assertEqual(stdout.count('"stop_reason": "COMPLETE"'), 1)

    def test_existing_collection_is_never_overwritten(self):
        run, _, process, sock, stream = self._start()
        _until(self, stream, "OWNED")
        sock.sendall(b"G")
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        sentinel = b'{"sentinel":"immutable"}\n'
        (run / "collection.json").write_bytes(sentinel)
        (run / "collection.json").chmod(0o600)
        before = hashlib.sha256(sentinel).hexdigest()
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertIn("File exists", json.loads(stderr)["error"])
        self.assertEqual(hashlib.sha256((run / "collection.json").read_bytes()).hexdigest(), before)
        self.assertEqual((run / "collection.json").read_bytes(), sentinel)
        self.assertFalse(list(run.glob(".collection-*.tmp")))

    def test_no_private_result_or_process_survives(self):
        run, temporary, process, sock, stream = self._start("ignore-term")
        events = _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events += _until(self, stream, "CANDIDATE_PIDS")
        pids = _event_pids(events, "CANDIDATE_PIDS")
        os.kill(process.pid, signal.SIGTERM)
        _until(self, stream, "INTERRUPTED_ARBITRATED")
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        sock.sendall(b"G")
        process.communicate(timeout=5)
        _assert_dead(self, pids)
        self.assertFalse(list(temporary.glob("benchmark-lab-x-collect-*")))
        self.assertFalse(list(run.glob(".collection-*.tmp")))
        self.assertEqual([path.name for path in run.glob("collection.json")], ["collection.json"])

    def test_inherited_sigign_fails_before_worker_creation(self):
        run, temporary, process, _, _ = self._start(inherited_ignore=True)
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], "SIGTERM hérité en SIG_IGN")
        self.assertFalse((run / "authorization-s9.json").exists())
        self.assertFalse((run / "collection.json").exists())
        self.assertFalse(list(temporary.glob("benchmark-lab-x-collect-*")))


class SigtermRaceTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(platform.system(), "Darwin", "la preuve d’acceptation exige macOS")
        self.fixture = DemoTest()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_after_c1_stops_before_c2(self):
        run, _, process, sock, stream = _start_supervisor(
            self, self.fixture, extra_env={"BENCHMARK_LAB_X_TEST_C2_REQUEST_GATE": "1"}
        )
        _until(self, stream, "OWNED")
        sock.sendall(b"G")
        _until(self, stream, "C1_DONE")
        _until(self, stream, "C2_LAUNCH_REQUEST")
        os.kill(process.pid, signal.SIGTERM)
        _until(self, stream, "INTERRUPTED_ARBITRATED")
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], "collect interrompu par SIGTERM")
        collection = json.loads((run / "collection.json").read_text())
        self.assertEqual([item["config_id"] for item in collection["receipts"]], ["C1"])
        self.assertEqual([item["executed"] for item in collection["matrix"]], [True, False, False])
        self.assertEqual(collection["stop_reason"], "INTERRUPTION_SIGTERM")
        self.assertFalse(any(run.glob("C[23].*")))
        self.assertFalse(json.loads((run / "C1.receipt.json").read_text())["retry"])

    def test_between_popen_and_assignment_kills_and_waits(self):
        run, _, process, sock, stream = _start_supervisor(self, self.fixture, "timeout")
        events = _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events += _until(self, stream, "CANDIDATE_PIDS")
        pids = _event_pids(events, "CANDIDATE_PIDS")
        exits = _watch_exits(self, pids)
        os.kill(process.pid, signal.SIGTERM)
        _until(self, stream, "INTERRUPTED_ARBITRATED")
        _assert_exit_events(self, exits, pids)
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        sock.sendall(b"G")
        process.communicate(timeout=5)
        self.assertIsNotNone(process.returncode)
        _assert_dead(self, pids)
        collection = json.loads((run / "collection.json").read_text())
        self.assertEqual(collection["stop_reason"], "INTERRUPTION_SIGTERM")
        self.assertEqual([item["executed"] for item in collection["matrix"]], [True, False, False])
        receipt = json.loads((run / "C1.receipt.json").read_text())
        self.assertIn("SIGTERM", receipt["incident"])
        self.assertFalse(receipt["retry"])
        self.assertFalse(any(run.glob("C[23].*")))

    def test_before_collection_publication_validates_then_restores(self):
        run, _, process, sock, stream = _start_supervisor(self, self.fixture)
        _until(self, stream, "OWNED")
        sock.sendall(b"G")
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        os.kill(process.pid, signal.SIGTERM)
        _until(self, stream, "INTERRUPTED_ARBITRATED")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], "collect interrompu par SIGTERM")
        seal, panel = demo._validate_prepared(run, f"runs/{run.name}")
        collection, _ = demo._validate_collection(run, f"runs/{run.name}", seal, panel)
        self.assertEqual(collection["stop_reason"], "INTERRUPTION_SIGTERM")
        self.assertEqual([item["executed"] for item in collection["matrix"]], [True, True, True])
        self.assertEqual(stat.S_IMODE((run / "collection.json").stat().st_mode), 0o600)


class FinalSigtermHandoffTests(unittest.TestCase):
    setUp = SigtermRaceTests.setUp
    tearDown = SigtermRaceTests.tearDown

    def test_sigterm_after_validation_cannot_return_complete(self):
        run, _, process, sock, stream = _start_supervisor(self, self.fixture)
        _until(self, stream, "OWNED")
        sock.sendall(b"G")
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        os.kill(process.pid, signal.SIGTERM)
        _until(self, stream, "INTERRUPTED_ARBITRATED")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], "collect interrompu par SIGTERM")
        collection = json.loads((run / "collection.json").read_text())
        self.assertEqual(collection["stop_reason"], "INTERRUPTION_SIGTERM")
        self.assertFalse(list(run.glob(".collection-*.tmp")))


class MultithreadedFinalSigtermHandoffTests(unittest.TestCase):
    setUp = SigtermRaceTests.setUp
    tearDown = SigtermRaceTests.tearDown

    def test_sigterm_between_latch_read_and_handler_restore_is_terminal(self):
        run, _, process, sock, stream = _start_supervisor(self, self.fixture)
        _until(self, stream, "OWNED")
        sock.sendall(b"G")
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        os.kill(process.pid, signal.SIGTERM)
        _until(self, stream, "INTERRUPTED_ARBITRATED")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], "collect interrompu par SIGTERM")
        collection = json.loads((run / "collection.json").read_text())
        self.assertEqual(collection["stop_reason"], "INTERRUPTION_SIGTERM")
        self.assertEqual(stat.S_IMODE((run / "collection.json").stat().st_mode), 0o600)
        self.assertFalse(list(run.glob(".collection-*.tmp")))


class SignalQuiescenceSinglePgidTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(platform.system(), "Darwin", "la preuve d’acceptation exige macOS")
        self.fixture = DemoTest()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def _start(self, variant="good", **env):
        return _start_supervisor(self, self.fixture, variant, extra_env=env)

    def _finish_interruption(self, run, process, sock, stream):
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"], "collect interrompu par SIGTERM")
        collection = json.loads((run / "collection.json").read_text())
        self.assertEqual(collection["stop_reason"], "INTERRUPTION_SIGTERM")
        return collection

    def test_after_c1_before_c2_revokes_launch_permit(self):
        run, _, process, sock, stream = self._start(BENCHMARK_LAB_X_TEST_C2_REQUEST_GATE="1")
        events = _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events += _until(self, stream, "C1_DONE")
        events += _until(self, stream, "C2_LAUNCH_REQUEST")
        worker_pid = _event_pids(events, "WORKER_PGID")[0]
        exits = _watch_exits(self, [worker_pid])
        self.assertFalse((run / "C2.started.json").exists())
        os.kill(process.pid, signal.SIGTERM)
        events += _until(self, stream, "INTERRUPTED_ARBITRATED")
        _assert_exit_events(self, exits, [worker_pid])
        collection = self._finish_interruption(run, process, sock, stream)
        self.assertFalse(any(event.startswith("CANDIDATE_POPENED_BEFORE_ACTIVE_PID C2") for event in events))
        self.assertEqual([item["executed"] for item in collection["matrix"]], [True, False, False])

    def test_between_popen_and_identity_uses_worker_pgid(self):
        run, temporary, process, sock, stream = self._start("timeout", BENCHMARK_LAB_X_TEST_POPEN_GATE="C1")
        events = _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events += _until(self, stream, "CANDIDATE_POPENED_BEFORE_ACTIVE_PID C1")
        events += _until(self, stream, "CANDIDATE_PIDS")
        worker_pid = _event_pids(events, "WORKER_PGID")[0]
        candidate_pid = int(next(event.split()[2] for event in events if event.startswith("CANDIDATE_POPENED_BEFORE_ACTIVE_PID C1")))
        candidate_pids = _event_pids(events, "CANDIDATE_PIDS")
        pids = [worker_pid, candidate_pid, candidate_pids[1]]
        self.assertEqual(candidate_pid, candidate_pids[0])
        self.assertTrue(all(os.getpgid(pid) == worker_pid for pid in pids))
        private_dirs = list(temporary.glob("benchmark-lab-x-collect-*"))
        self.assertEqual(len(private_dirs), 1)
        self.assertFalse((private_dirs[0] / "active.pid").exists())
        exits = _watch_exits(self, pids)
        os.kill(process.pid, signal.SIGTERM)
        events += _until(self, stream, "INTERRUPTED_ARBITRATED")
        _assert_exit_events(self, exits, pids)
        _assert_dead(self, pids)
        self._finish_interruption(run, process, sock, stream)

    def test_partial_private_receipt_is_ignored_and_rebuilt(self):
        run, _, process, sock, stream = self._start(BENCHMARK_LAB_X_TEST_PRIVATE_RECEIPT_GATE="C1.receipt.json")
        events = _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events += _until(self, stream, "PRIVATE_JSON_HALF_WRITTEN C1.receipt.json")
        worker_pid = _event_pids(events, "WORKER_PGID")[0]
        exits = _watch_exits(self, [worker_pid])
        self.assertFalse((run / "C1.receipt.json").exists())
        os.kill(process.pid, signal.SIGTERM)
        _until(self, stream, "INTERRUPTED_ARBITRATED")
        _assert_exit_events(self, exits, [worker_pid])
        collection = self._finish_interruption(run, process, sock, stream)
        self.assertEqual(json.loads((run / "C1.receipt.json").read_text())["config_id"], "C1")
        self.assertTrue(collection["matrix"][0]["executed"])
        self.assertFalse(list(run.glob(".private-json-*.tmp")))

        invalid_run = self.fixture.prepared()
        authority, _ = self.fixture.s9(invalid_run)
        authority_raw = authority.read_bytes()
        seal, panel = demo._validate_prepared(invalid_run, f"runs/{invalid_run.name}")
        (invalid_run / "C1.receipt.json").write_bytes(b'{"schema":')
        (invalid_run / "C1.receipt.json").chmod(0o600)
        rebuilt = demo._interruption_collection(invalid_run, f"runs/{invalid_run.name}", seal, panel, authority_raw)
        self.assertEqual(rebuilt["receipts"], [])
        publication = invalid_run / ".collection-test.tmp"
        demo._write_private_collection(publication, rebuilt, invalid_run, f"runs/{invalid_run.name}", seal, panel)
        publication.unlink()

    def test_sigterm_ignored_by_worker_and_descendants_escalates_to_quiescence(self):
        run, _, process, sock, stream = self._start("ignore-term")
        events = _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events += _until(self, stream, "CANDIDATE_PIDS")
        worker_pid = _event_pids(events, "WORKER_PGID")[0]
        candidate_pids = _event_pids(events, "CANDIDATE_PIDS")
        pids = [worker_pid, *candidate_pids]
        self.assertTrue(all(os.getpgid(pid) == worker_pid for pid in pids))
        exits = _watch_exits(self, pids)
        os.kill(process.pid, signal.SIGTERM)
        events += _until(self, stream, "INTERRUPTED_ARBITRATED")
        _assert_exit_events(self, exits, pids)
        _assert_dead(self, pids)
        self.assertLess(events.index("PGID_GONE"), events.index("INTERRUPTED_ARBITRATED"))
        self._finish_interruption(run, process, sock, stream)

    def test_interrupted_arbitrated_is_after_quiescence(self):
        run, _, process, sock, stream = self._start("ignore-term")
        events = _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events += _until(self, stream, "CANDIDATE_PIDS")
        pids = [_event_pids(events, "WORKER_PGID")[0], *_event_pids(events, "CANDIDATE_PIDS")]
        exits = _watch_exits(self, pids)
        os.kill(process.pid, signal.SIGTERM)
        events += _until(self, stream, "INTERRUPTED_ARBITRATED")
        _assert_exit_events(self, exits, pids)
        self.assertLess(events.index("INTERRUPTED_SEEN"), events.index("PGID_GONE"))
        self.assertLess(events.index("PGID_GONE"), events.index("INTERRUPTED_ARBITRATED"))
        with self.assertRaises(ProcessLookupError):
            os.killpg(pids[0], 0)
        self._finish_interruption(run, process, sock, stream)

    def test_collection_link_never_overwrites_existing_file(self):
        run, _, process, sock, stream = self._start()
        _until(self, stream, "OWNED")
        sock.sendall(b"G")
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        sentinel = b'{"sentinel":"immutable"}\n'
        (run / "collection.json").write_bytes(sentinel)
        (run / "collection.json").chmod(0o600)
        digest = hashlib.sha256(sentinel).hexdigest()
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertIn("File exists", json.loads(stderr)["error"])
        self.assertEqual(demo._sha(run / "collection.json"), digest)
        self.assertFalse(list(run.glob(".private-json-*.tmp")))
        self.assertFalse(list(run.glob(".collection-*.tmp")))


class R11PgidRetentionAtomicIoTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(platform.system(), "Darwin", "la preuve d’acceptation exige macOS")
        self.fixture = DemoTest()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def _start(self, variant="good", **env):
        return _start_supervisor(self, self.fixture, variant, extra_env=env)

    def _finish_failure(self, run, process, sock, stream, reason, before_link_seen=False):
        if not before_link_seen:
            _until(self, stream, "BEFORE_COMPLETE_LINK")
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        expected = "collect interrompu par SIGTERM" if reason == "INTERRUPTION_SIGTERM" else f"collect arrêté: {reason}"
        self.assertEqual(json.loads(stderr)["error"], expected)
        collection = json.loads((run / "collection.json").read_text())
        self.assertEqual(collection["stop_reason"], reason)
        return collection

    def test_reaped_worker_pgid_is_retained_and_lingering_descendant_killed(self):
        run, _, process, sock, stream = self._start("linger")
        events = _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events += _until(self, stream, "WORKER_REAPED")
        worker_pgid = _event_pids(events, "WORKER_PGID")[0]
        descendants = [int(event.split()[2]) for event in events if event.startswith("CANDIDATE_PIDS")]
        self.assertEqual(len(descendants), 3)
        self.assertTrue(all(os.getpgid(pid) == worker_pgid for pid in descendants))
        exits = _watch_exits(self, descendants)
        os.kill(process.pid, signal.SIGTERM)
        events = _until(self, stream, "INTERRUPTED_ARBITRATED")
        _assert_exit_events(self, exits, descendants)
        self.assertLess(events.index("PGID_QUIESCENT"), events.index("PGID_GONE"))
        with self.assertRaises(ProcessLookupError):
            os.killpg(worker_pgid, 0)
        self._finish_failure(run, process, sock, stream, "INTERRUPTION_SIGTERM", "BEFORE_COMPLETE_LINK" in events)

    def test_supervisor_owns_candidate_timeout_and_kills_single_pgid(self):
        run, _, process, sock, stream = self._start(
            "ignore-term", BENCHMARK_LAB_X_TEST_POPEN_GATE="C1", BENCHMARK_LAB_X_TEST_TIMEOUT_SECONDS="0.1"
        )
        events = _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events += _until(self, stream, "CANDIDATE_PIDS")
        worker_pgid = _event_pids(events, "WORKER_PGID")[0]
        pids = [worker_pgid, *_event_pids(events, "CANDIDATE_PIDS")]
        self.assertTrue(all(os.getpgid(pid) == worker_pgid for pid in pids))
        exits = _watch_exits(self, pids)
        sock.sendall(b"G")
        events = _until(self, stream, "INTERRUPTED_ARBITRATED")
        self.assertIn("TERMINATION_REQUESTED CANDIDATE_TIMEOUT", events)
        self.assertIn("LAUNCHES_REVOKED", events)
        _assert_exit_events(self, exits, pids)
        _assert_dead(self, pids)
        collection = self._finish_failure(run, process, sock, stream, "CANDIDATE_TIMEOUT")
        self.assertEqual([item["executed"] for item in collection["matrix"]], [True, False, False])
        self.assertFalse(any(run.glob("C[23].*")))

    def test_supervisor_owns_candidate_exception_and_reconstructs_fail_closed(self):
        run, _, process, sock, stream = self._start(
            "ignore-term", BENCHMARK_LAB_X_TEST_POPEN_GATE="C1", BENCHMARK_LAB_X_TEST_CANDIDATE_EXCEPTION="C1"
        )
        events = _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events += _until(self, stream, "CANDIDATE_PIDS")
        pids = [_event_pids(events, "WORKER_PGID")[0], *_event_pids(events, "CANDIDATE_PIDS")]
        exits = _watch_exits(self, pids)
        sock.sendall(b"G")
        events = _until(self, stream, "INTERRUPTED_ARBITRATED")
        self.assertIn("TERMINATION_REQUESTED CANDIDATE_EXCEPTION", events)
        self.assertIn("LAUNCHES_REVOKED", events)
        _assert_exit_events(self, exits, pids)
        _assert_dead(self, pids)
        collection = self._finish_failure(run, process, sock, stream, "CANDIDATE_EXCEPTION")
        self.assertEqual(collection["matrix"][0]["reason"], "INTERRUPTION_GROUPE_TUE")
        self.assertFalse(any(run.glob("C[23].*")))

    def test_receipt_and_marker_require_object_root(self):
        run = self.fixture.prepared()
        authority, _ = self.fixture.s9(run)
        authority_raw = authority.read_bytes()
        seal, panel = demo._validate_prepared(run, f"runs/{run.name}")
        receipt_path = run / "C1.receipt.json"
        marker_path = run / "C1.started.json"
        roots = [[], None, "x", 1, True]
        for root in roots:
            with self.subTest(receipt=root):
                receipt_path.write_text(json.dumps(root))
                receipt_path.chmod(0o600)
                collection = demo._interruption_collection(run, f"runs/{run.name}", seal, panel, authority_raw)
                self.assertEqual(collection["receipts"], [])
                publication = run / ".collection-root-test.tmp"
                demo._write_private_collection(publication, collection, run, f"runs/{run.name}", seal, panel)
                publication.unlink()
        receipt_path.unlink()
        for root in roots:
            with self.subTest(marker=root):
                marker_path.write_text(json.dumps(root))
                marker_path.chmod(0o600)
                collection = demo._interruption_collection(run, f"runs/{run.name}", seal, panel, authority_raw)
                self.assertEqual(collection["receipts"], [])
                publication = run / ".collection-root-test.tmp"
                demo._write_private_collection(publication, collection, run, f"runs/{run.name}", seal, panel)
                publication.unlink()

    def test_sigterm_mid_private_json_waits_for_io_quiescence_and_rebuilds(self):
        run, _, process, sock, stream = self._start(BENCHMARK_LAB_X_TEST_PRIVATE_COLLECTION_GATE="1")
        _until(self, stream, "OWNED")
        sock.sendall(b"G")
        events = _until(self, stream, "PRIVATE_COLLECTION_HALF_WRITTEN")
        os.kill(process.pid, signal.SIGTERM)
        events += _until(self, stream, "PGID_QUIESCENT")
        self.assertNotIn("INTERRUPTED_ARBITRATED", events)
        sock.sendall(b"G")
        events = _until(self, stream, "INTERRUPTED_ARBITRATED")
        self.assertIn("PGID_GONE", events)
        collection = self._finish_failure(run, process, sock, stream, "INTERRUPTION_SIGTERM", "BEFORE_COMPLETE_LINK" in events)
        seal, panel = demo._validate_prepared(run, f"runs/{run.name}")
        self.assertEqual(demo._validate_collection(run, f"runs/{run.name}", seal, panel)[0], collection)
        self.assertFalse(list(run.glob(".private-json-*.tmp")))
        self.assertFalse(list(run.glob(".collection-*.tmp")))

    def test_kqueue_accumulates_all_note_exit_events(self):
        event = lambda pid, **changes: mock.Mock(
            ident=pid, filter=changes.get("filter", select.KQ_FILTER_PROC),
            fflags=changes.get("fflags", select.KQ_NOTE_EXIT), flags=changes.get("flags", 0),
        )
        queue = mock.Mock()
        queue.control.side_effect = [[event(11)], [event(12)]]
        _assert_exit_events(self, queue, [11, 12])
        self.assertEqual(queue.control.call_count, 2)
        invalid = [event(13), event(11, filter=0), event(11, fflags=0), event(11, flags=select.KQ_EV_ERROR)]
        for item in invalid:
            with self.subTest(event=item):
                bad_queue = mock.Mock()
                bad_queue.control.return_value = [item]
                with self.assertRaises(AssertionError):
                    _assert_exit_events(self, bad_queue, [11])

    def test_post_l_sigterm_uses_distinct_terminal_milestone(self):
        run, _, process, sock, stream = self._start()
        _until(self, stream, "OWNED")
        sock.sendall(b"G")
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        sock.sendall(b"G")
        _until(self, stream, "COMPLETE_LINKED")
        before = (run / "collection.json").read_bytes()
        os.kill(process.pid, signal.SIGTERM)
        events = _until(self, stream, "POST_TERMINAL_SIGTERM")
        self.assertFalse(any(event in {"INTERRUPTED_ARBITRATED", "PGID_GONE"} for event in events))
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["stop_reason"], "COMPLETE")
        self.assertEqual((run / "collection.json").read_bytes(), before)

    def test_collection_link_never_overwrites_existing_file(self):
        run, _, process, sock, stream = self._start()
        _until(self, stream, "OWNED")
        sock.sendall(b"G")
        _until(self, stream, "BEFORE_COMPLETE_LINK")
        sentinel = b'{"sentinel":"immutable"}\n'
        (run / "collection.json").write_bytes(sentinel)
        (run / "collection.json").chmod(0o600)
        sock.sendall(b"G")
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertIn("File exists", json.loads(stderr)["error"])
        self.assertEqual((run / "collection.json").read_bytes(), sentinel)
        self.assertFalse(list(run.glob(".private-json-*.tmp")))
        self.assertFalse(list(run.glob(".collection-*.tmp")))


class IsolatedSupervisorFailureTests(unittest.TestCase):
    setUp = DemoTest.setUp
    tearDown = DemoTest.tearDown
    write_json = DemoTest.write_json
    fake_pi = DemoTest.fake_pi
    prepared = DemoTest.prepared
    s9 = DemoTest.s9

    def test_worker_validation_failure_never_publishes(self):
        run = self.prepared()
        authority, value = self.s9(run)
        value["seal_sha256"] = "0" * 64
        authority.write_text(json.dumps(value))
        parent, child = socket.socketpair()
        temporary = self.root / "tmp"
        temporary.mkdir()
        env = {**os.environ, "TMPDIR": str(temporary), "BENCHMARK_LAB_X_TEST_SOCKET_FD": str(child.fileno()), "BENCHMARK_LAB_X_TEST_REPO_ROOT": str(self.root)}
        process = subprocess.run(
            [sys.executable, "-B", "-m", "benchmark", "collect", "--run-dir", str(run), "--authority", str(authority)],
            cwd=demo.PACKAGE_DIR.parent, env=env, pass_fds=(child.fileno(),), capture_output=True, text=True, timeout=5,
        )
        parent.close()
        child.close()
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse((run / "collection.json").exists())
        self.assertFalse(list(temporary.glob("benchmark-lab-x-collect-*")))


class R13UnsupervisedKeyboardInterruptCleanupTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(platform.system(), "Darwin", "la preuve d’acceptation exige macOS")
        self.fixture = DemoTest()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_direct_collect_sigint_kills_candidate_group_before_return(self):
        run = self.fixture.prepared("timeout")
        authority, _ = self.fixture.s9(run)
        temporary = self.fixture.root / "tmp"
        temporary.mkdir()
        parent, child = socket.socketpair()
        env = {
            **os.environ,
            "TMPDIR": str(temporary),
            "BENCHMARK_LAB_X_TEST_SOCKET_FD": str(child.fileno()),
        }
        env.pop("BENCHMARK_LAB_X_LAUNCH_SOCKET_FD", None)
        process = subprocess.Popen(
            [
                sys.executable, "-B", "-c",
                "from benchmark import __main__ as d; import sys; d.collect(*sys.argv[1:])",
                str(run), str(authority), str(self.fixture.root),
            ],
            cwd=demo.PACKAGE_DIR.parent, env=env, pass_fds=(child.fileno(),),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        child.close()
        parent.settimeout(5)
        stream = parent.makefile("rb", buffering=0)
        self.addCleanup(parent.close)
        self.addCleanup(stream.close)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        events = _until(self, stream, "CANDIDATE_PIDS")
        pids = _event_pids(events, "CANDIDATE_PIDS")

        def kill_group():
            try:
                os.killpg(pids[0], signal.SIGKILL)
            except ProcessLookupError:
                pass

        self.addCleanup(kill_group)
        self.assertEqual([os.getpgid(pid) for pid in pids], [pids[0], pids[0]])
        exits = _watch_exits(self, pids)
        os.kill(process.pid, signal.SIGINT)
        _assert_exit_events(self, exits, pids)
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertIn("KeyboardInterrupt", stderr)
        _assert_dead(self, pids)
        with self.assertRaises(ProcessLookupError):
            os.killpg(pids[0], 0)
        seal, panel = demo._validate_prepared(run, f"runs/{run.name}")
        collection, _ = demo._validate_collection(run, f"runs/{run.name}", seal, panel)
        self.assertEqual(collection["stop_reason"], "COUT_OU_BUDGET_INCONNU")
        self.assertEqual([item["executed"] for item in collection["matrix"]], [True, False, False])
        self.assertIn("INTERRUPTION_GROUPE_TUE", collection["matrix"][0]["reason"])
        self.assertFalse(any(run.glob("C[23].*")))
        self.assertFalse(list(temporary.glob("benchmark-lab-x-collect-*")))
        self.assertFalse(list(run.glob(".private-json-*.tmp")))
        self.assertFalse(list(run.glob(".collection-*.tmp")))


class R16HeldPgidBehavioralProofTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(platform.system(), "Darwin", "la preuve d’acceptation exige macOS")
        self.fixture = DemoTest()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_direct_collect_cannot_exit_while_candidate_pgid_is_held(self):
        pi = self.fixture.root / "pi-held-pgid"
        pi.write_text('''#!/usr/bin/env python3
import os, signal, socket, sys

if sys.argv[1:] == ["--version"]:
    print("0.84.4")
    raise SystemExit(0)

sock = socket.socket(fileno=int(os.environ["BENCHMARK_LAB_X_TEST_SOCKET_FD"]))
ready_read, ready_write = os.pipe()
reaper = os.fork()
if reaper == 0:
    os.close(ready_read)
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    if devnull > 2:
        os.close(devnull)
    target = os.fork()
    if target == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        os.write(ready_write, b"R")
        os.close(ready_write)
        signal.pause()
        raise SystemExit(1)
    os.close(ready_write)
    os.setpgid(0, 0)
    os.write(sock.fileno(), f"HELD_PGID {os.getppid()} {os.getpid()} {target}\\n".encode())
    if sock.recv(1) != b"R":
        raise SystemExit(2)
    waited, _ = os.waitpid(target, 0)
    os.write(sock.fileno(), f"TARGET_REAPED {waited}\\n".encode())
    raise SystemExit(0)

os.close(ready_write)
if os.read(ready_read, 1) != b"R":
    raise SystemExit(3)
os.close(ready_read)
signal.pause()
''')
        pi.chmod(0o700)
        run = self.fixture.root / "runs" / "run-held-pgid"
        demo.prepare(run, pi, self.fixture.root)
        authority, _ = self.fixture.s9(run)
        temporary = self.fixture.root / "tmp"
        temporary.mkdir()
        parent, child = socket.socketpair()
        env = {
            **os.environ,
            "TMPDIR": str(temporary),
            "BENCHMARK_LAB_X_TEST_SOCKET_FD": str(child.fileno()),
        }
        env.pop("BENCHMARK_LAB_X_LAUNCH_SOCKET_FD", None)
        program = '''
from benchmark import __main__ as d
import os, sys
original = d._wait_pgid_gone
def observed(*args):
    os.write(int(os.environ["BENCHMARK_LAB_X_TEST_SOCKET_FD"]), b"WAIT_PGID_ENTER\\n")
    result = original(*args)
    os.write(int(os.environ["BENCHMARK_LAB_X_TEST_SOCKET_FD"]), f"WAIT_PGID_RETURN {int(result)}\\n".encode())
    return result
d._wait_pgid_gone = observed
d.collect(*sys.argv[1:])
'''
        process = subprocess.Popen(
            [
                sys.executable, "-B", "-c",
                program,
                str(run), str(authority), str(self.fixture.root),
            ],
            cwd=demo.PACKAGE_DIR.parent, env=env, pass_fds=(child.fileno(),),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        child.close()
        parent.settimeout(5)
        stream = parent.makefile("rb", buffering=0)
        self.addCleanup(parent.close)
        self.addCleanup(stream.close)
        def clean_collect():
            if process.poll() is None:
                process.kill()
            process.communicate()

        self.addCleanup(clean_collect)
        events = _until(self, stream, "HELD_PGID")
        candidate, reaper, target = _event_pids(events, "HELD_PGID")

        def clean_processes():
            try:
                parent.sendall(b"R")
            except OSError:
                pass
            try:
                os.killpg(candidate, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                pass
            for pid in [candidate, reaper, target]:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        def assert_pgid_present():
            try:
                os.killpg(candidate, 0)
            except PermissionError:
                pass

        self.addCleanup(clean_processes)
        self.assertEqual(
            [os.getpgid(candidate), os.getpgid(reaper), os.getpgid(target)],
            [candidate, reaper, candidate],
        )
        assert_pgid_present()
        queue = _watch_exits(self, [candidate, reaper, target, process.pid])
        queue.control([select.kevent(parent.fileno(), filter=select.KQ_FILTER_READ, flags=select.KQ_EV_ADD)], 0, 0)
        parent.setblocking(False)
        deadline = time.monotonic() + 5
        os.kill(process.pid, signal.SIGINT)
        exited = set()
        wait_entered = wait_returned = target_reaped = False
        released = False
        behavioral_failure = None
        socket_buffer = b""
        while process.pid not in exited or reaper not in exited or not target_reaped or not wait_returned:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, "borne totale dépassée")
            ready = queue.control(None, 5, remaining)
            self.assertTrue(ready, "événement causal absent")
            for event in [item for item in ready if item.filter == select.KQ_FILTER_PROC]:
                self.assertIn(event.ident, {candidate, reaper, target, process.pid})
                self.assertTrue(event.fflags & select.KQ_NOTE_EXIT)
                self.assertFalse(event.flags & select.KQ_EV_ERROR)
                exited.add(event.ident)
            if any(event.filter == select.KQ_FILTER_READ for event in ready):
                try:
                    socket_buffer += parent.recv(4096)
                except BlockingIOError:
                    pass
                lines = socket_buffer.split(b"\n")
                socket_buffer = lines.pop()
                for line in lines:
                    wait_entered |= line == b"WAIT_PGID_ENTER"
                    wait_returned |= line == b"WAIT_PGID_RETURN 1"
                    target_reaped |= line == f"TARGET_REAPED {target}".encode()
            if {candidate, target} <= exited and not released:
                assert_pgid_present()
                if process.pid in exited:
                    behavioral_failure = "collect sorti avec un PGID encore présent"
                elif wait_entered:
                    self.assertNotIn(process.pid, exited, "collect sorti avec un PGID encore présent")
                if behavioral_failure or wait_entered:
                    parent.sendall(b"R")
                    released = True
            if behavioral_failure and target_reaped and reaper in exited:
                break
            if target_reaped:
                with self.assertRaises(ProcessLookupError):
                    os.killpg(candidate, 0)
        stdout, stderr = process.communicate(timeout=max(0.001, deadline - time.monotonic()))

        if behavioral_failure:
            self.fail(behavioral_failure)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertIn("KeyboardInterrupt", stderr)
        self.assertTrue(wait_entered)
        self.assertTrue(wait_returned)
        self.assertTrue(target_reaped)
        self.assertIn(reaper, exited)
        _assert_dead(self, [candidate, reaper, target, process.pid])
        seal, panel = demo._validate_prepared(run, f"runs/{run.name}")
        collection, _ = demo._validate_collection(run, f"runs/{run.name}", seal, panel)
        self.assertEqual(collection["stop_reason"], "COUT_OU_BUDGET_INCONNU")
        self.assertEqual([item["executed"] for item in collection["matrix"]], [True, False, False])
        self.assertIn("INTERRUPTION_GROUPE_TUE", collection["matrix"][0]["reason"])
        self.assertFalse(json.loads((run / "C1.receipt.json").read_text())["retry"])
        self.assertFalse(any(run.glob("C[23].*")))
        self.assertFalse(list(temporary.glob("benchmark-lab-x-collect-*")))
        self.assertFalse(list(run.glob(".private-json-*.tmp")))
        self.assertFalse(list(run.glob(".collection-*.tmp")))


class IsolatedSupervisorImmutabilityTests(unittest.TestCase):
    setUp = DemoTest.setUp
    tearDown = DemoTest.tearDown
    write_json = DemoTest.write_json
    fake_pi = DemoTest.fake_pi
    prepared = DemoTest.prepared
    s9 = DemoTest.s9

    def test_existing_collection_prevents_worker_creation(self):
        run = self.prepared()
        auth, _ = self.s9(run)
        demo.collect(run, auth, self.root)
        parent, child = socket.socketpair()
        env = {**os.environ, "BENCHMARK_LAB_X_TEST_SOCKET_FD": str(child.fileno()), "BENCHMARK_LAB_X_TEST_REPO_ROOT": str(self.root)}
        process = subprocess.run(
            [sys.executable, "-B", "-m", "benchmark", "collect", "--run-dir", str(run), "--authority", str(auth)],
            cwd=demo.PACKAGE_DIR.parent, env=env, pass_fds=(child.fileno(),), capture_output=True, text=True, timeout=5,
        )
        parent.close()
        child.close()
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("collection immuable déjà présente", process.stderr)


if __name__ == "__main__":
    unittest.main()
