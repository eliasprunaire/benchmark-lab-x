from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import platform
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = PACKAGE_DIR.parent
BASE_COMMIT = "40beaa480cd101b81be0e2c3f4acc701c7b320eb"
PI_VERSION = "0.84.4"
CAP = Decimal("0.50")
TIMEOUT_SECONDS = 300
SYSTEM_PROMPT = "Réponds uniquement à la tâche fournie, en texte, sans outil ni fait inventé."
COMMON_ARGS = [
    "--provider", "openrouter", "--thinking", "high", "--system-prompt", SYSTEM_PROMPT,
    "--mode", "json", "--no-session", "--no-tools", "--no-extensions", "--no-skills",
    "--no-prompt-templates", "--no-themes", "--no-context-files", "--no-approve",
]
ALLOWED_EVIDENCE = {"blind-copy", "receipt", "incident"}
OBSERVED_KEYS = ("provider", "model", "responseModel", "stopReason")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(131072), b""):
            h.update(block)
    return h.hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _strict_json_bytes(raw, label):
    try:
        value = json.loads(raw, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
        def finite(item):
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("nombre non fini")
            if isinstance(item, dict):
                for child in item.values():
                    finite(child)
            elif isinstance(item, list):
                for child in item:
                    finite(child)
        finite(value)
        return value
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"JSON invalide ({label}): {exc}") from exc


def _load_json(path, label=None):
    path = Path(path)
    return _strict_json_bytes(path.read_bytes(), label or str(path))


def _write_exclusive(path, data):
    path = Path(path)
    raw = data if isinstance(data, bytes) else data.encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _write_json_bytes(path, raw):
    path = Path(path)
    value = _strict_json_bytes(raw, path.name)
    temporary = path.parent / f".private-json-{path.name}-{secrets.token_hex(16)}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            halfway = len(raw) // 2
            stream.write(raw[:halfway])
            stream.flush()
            os.fsync(stream.fileno())
            if os.environ.get("BENCHMARK_LAB_X_LAUNCH_SOCKET_FD") is not None and os.environ.get("BENCHMARK_LAB_X_TEST_PRIVATE_RECEIPT_GATE") == path.name:
                _test_event(f"PRIVATE_JSON_HALF_WRITTEN {path.name} {temporary.name}")
                _test_gate()
            if os.environ.get("BENCHMARK_LAB_X_TEST_PRIVATE_COLLECTION_GATE") == "1" and path.name.startswith(".collection-"):
                os.environ.pop("BENCHMARK_LAB_X_TEST_PRIVATE_COLLECTION_GATE")
                _test_event(f"PRIVATE_COLLECTION_HALF_WRITTEN {temporary.name}")
                _test_gate()
            stream.write(raw[halfway:])
            stream.flush()
            os.fsync(stream.fileno())
        if _strict_json_bytes(temporary.read_bytes(), temporary.name) != value:
            raise ValueError(f"JSON privé divergent ({path.name})")
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path, value):
    _write_json_bytes(path, _json_bytes(value))


def _private_tree(run):
    for path in [run, *run.rglob("*")]:
        if path.is_symlink():
            raise ValueError(f"lien symbolique interdit: {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        expected = 0o700 if path.is_dir() else 0o600
        if mode != expected:
            raise ValueError(f"mode privé requis {oct(expected)}: {path}")


def _run_path(run_dir, repo_root=None, create=False):
    root = Path(repo_root or DEFAULT_REPO_ROOT).absolute()
    runs = root / "runs"
    if not runs.is_dir() or runs.is_symlink():
        raise ValueError("le répertoire runs/ réel du dépôt doit exister et ne pas être symbolique")
    path = Path(run_dir)
    path = path if path.is_absolute() else root / path
    path = path.absolute()
    if path.parent != runs or path.name in {"", ".", ".."}:
        raise ValueError("le run doit être un enfant direct de runs/")
    if create:
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
        os.mkdir(path, 0o700)
    else:
        if not path.is_dir() or path.is_symlink() or path.resolve().parent != runs.resolve():
            raise ValueError("run absent, symbolique ou hors de runs/")
        _private_tree(path)
    return path, f"runs/{path.name}"


def _campaign():
    campaign = _load_json(PACKAGE_DIR / "campaign.json")
    _validate_brief(campaign)
    return campaign


def _panel_models(panel):
    return {
        "providers": {"openrouter": {"modelOverrides": {
            item["model"]: {
                "maxTokens": 2048,
                "compat": {"maxTokensField": "max_tokens", "openRouterRouting": {
                    "only": [item["upstream"]],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                    "data_collection": "deny",
                }},
            }
            for item in panel
        }}}
    }


def _settings():
    return {
        "compaction": {"enabled": False},
        "retry": {"enabled": False, "maxRetries": 0, "provider": {"maxRetries": 0, "timeoutMs": 300000}},
    }


def _public_environment(repo_root):
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return {
        "python": platform.python_version(),
        "system": platform.system(),
        "system_version": platform.version(),
        "architecture": platform.machine(),
        "git_head": completed.stdout.strip(),
    }


def _value_sha(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _resolve_pi(pi_binary):
    candidate = Path(pi_binary).expanduser() if pi_binary else None
    found = str(candidate) if candidate else shutil.which("pi")
    if not found:
        raise ValueError("binaire Pi absent")
    real = Path(found).resolve()
    if not real.is_file() or not os.access(real, os.X_OK):
        raise ValueError("binaire Pi introuvable ou non exécutable")
    check = subprocess.run([str(real), "--version"], capture_output=True, text=True, timeout=10)
    if check.returncode or check.stdout.strip() != PI_VERSION:
        raise ValueError(f"version Pi exigée: {PI_VERSION}")
    return real


def prepare(run_dir, pi_binary=None, repo_root=None):
    run, run_id = _run_path(run_dir, repo_root, create=True)
    try:
        pi = _resolve_pi(pi_binary)
        campaign = _campaign()
        if campaign["base_commit"] != BASE_COMMIT:
            raise ValueError("commit de base divergent")
        mail = (PACKAGE_DIR / "mail-thread.md").read_bytes()
        if hashlib.sha256(mail).hexdigest() != "493fd10e74c937056cfeb532c2531087944fab0d471aef12f8b8a6a77f365cb6":
            raise ValueError("entrée historique divergente")
        task = (PACKAGE_DIR / "task.md").read_bytes()
        prompt = task + b"\n" + mail
        artifacts = {
            "input.md": mail,
            "prompt.txt": prompt,
            "panel.json": _json_bytes(campaign["panel"]),
            "settings.json": _json_bytes(_settings()),
            "models.json": _json_bytes(_panel_models(campaign["panel"])),
        }
        for name, raw in artifacts.items():
            (_write_json_bytes if name.endswith(".json") else _write_exclusive)(run / name, raw)
        public_environment = _public_environment(Path(repo_root or DEFAULT_REPO_ROOT).absolute())
        seal = {
            "schema": "benchmark-lab-x-seal-1",
            "run": run_id,
            "created_at": _now(),
            "base_commit": BASE_COMMIT,
            "artifacts": {name: _sha(run / name) for name in artifacts},
            "sources": {name: _sha(PACKAGE_DIR / name) for name in ["campaign.json", "mail-thread.md", "task.md", "__main__.py", "page.html"]},
            "pi": {"path": str(pi), "sha256": _sha(pi), "version": PI_VERSION},
            "public_environment": public_environment,
            "public_environment_sha256": _value_sha(public_environment),
            "requested_environment": {"PI_CODING_AGENT_DIR": f"{run_id}/pi-agent", "PI_SKIP_VERSION_CHECK": "1"},
            "common_args": COMMON_ARGS,
        }
        _write_json(run / "seal.json", seal)
        _private_tree(run)
        return seal
    except BaseException:
        shutil.rmtree(run, ignore_errors=True)
        raise


def _validate_models(models, panel):
    if models != _panel_models(panel):
        raise ValueError("configuration models.json divergente")


def _validate_prepared(run, run_id):
    _private_tree(run)
    seal_path = run / "seal.json"
    seal = _load_json(seal_path)
    if seal.get("schema") != "benchmark-lab-x-seal-1" or seal.get("run") != run_id or seal.get("base_commit") != BASE_COMMIT:
        raise ValueError("sceau de préparation invalide")
    for name, digest in seal.get("artifacts", {}).items():
        path = run / name
        if not path.is_file() or _sha(path) != digest:
            raise ValueError(f"artefact préparé altéré: {name}")
    for name, digest in seal.get("sources", {}).items():
        path = PACKAGE_DIR / name
        if not path.is_file() or _sha(path) != digest:
            raise ValueError(f"source du moteur altérée: {name}")
    pi = Path(seal["pi"]["path"])
    if not pi.is_file() or _sha(pi) != seal["pi"]["sha256"]:
        raise ValueError("binaire Pi altéré")
    checked = _resolve_pi(pi)
    if checked != pi.resolve() or seal["pi"]["version"] != PI_VERSION:
        raise ValueError("identité Pi divergente")
    panel = _load_json(run / "panel.json")
    if panel != _campaign()["panel"] or seal.get("common_args") != COMMON_ARGS:
        raise ValueError("panel ou arguments communs divergents")
    if _load_json(run / "settings.json") != _settings():
        raise ValueError("settings.json divergent")
    _validate_models(_load_json(run / "models.json"), panel)
    public_environment = _public_environment(run.parent.parent)
    if seal.get("public_environment") != public_environment or seal.get("public_environment_sha256") != _value_sha(public_environment):
        raise ValueError("environnement public ou HEAD Git divergent")
    return seal, panel


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"nombre fini non négatif requis: {label}")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"nombre invalide: {label}") from exc


def _validate_s9(auth, run_id, seal, seal_sha, panel):
    required = {
        "schema": "benchmark-lab-x-s9-authorization-1",
        "effect": "candidate_calls_and_spend_s9",
        "run": run_id,
        "seal_sha256": seal_sha,
        "contract_sha256": seal["sources"]["campaign.json"],
        "panel_sha256": seal["artifacts"]["panel.json"],
    }
    for key, value in required.items():
        if auth.get(key) != value:
            raise ValueError(f"autorité S9 divergente: {key}")
    if not isinstance(auth.get("authority_id"), str) or not auth["authority_id"].strip():
        raise ValueError("identifiant d’autorité S9 absent")
    pi = auth.get("pi", {})
    expected_pi = {
        "binary_sha256": seal["pi"]["sha256"], "version": PI_VERSION,
        "settings_sha256": seal["artifacts"]["settings.json"], "models_sha256": seal["artifacts"]["models.json"],
    }
    if pi != expected_pi:
        raise ValueError("autorité S9 divergente: Pi/configuration")
    budget = auth.get("budget", {})
    if budget.get("currency") != "USD" or _number(budget.get("cap"), "plafond") != CAP:
        raise ValueError("plafond S9 divergent")
    if not isinstance(budget.get("price_date"), str) or not budget["price_date"].strip() or not isinstance(budget.get("price_source"), str) or not budget["price_source"].strip():
        raise ValueError("date/source de prix absente")
    forecasts = budget.get("forecasts")
    if not isinstance(forecasts, dict) or set(forecasts) != {item["id"] for item in panel}:
        raise ValueError("prévisions S9 incomplètes")
    total = sum((_number(forecasts[item["id"]], item["id"]) for item in panel), Decimal(0))
    if total > CAP:
        raise ValueError("prévisions supérieures au plafond")
    return forecasts


def _pi_agent_dir(run):
    agent_dir = run / "pi-agent"
    os.mkdir(agent_dir, 0o700)
    for name in ["settings.json", "models.json"]:
        _write_exclusive(agent_dir / name, (run / name).read_bytes())
    return agent_dir


def _bounded_env(agent_dir):
    env = {key: os.environ[key] for key in ["PATH", "LANG", "LC_ALL", "TMPDIR", "OPENROUTER_API_KEY"] if key in os.environ}
    if "BENCHMARK_LAB_X_TEST_SOCKET_FD" in os.environ:
        env["BENCHMARK_LAB_X_TEST_SOCKET_FD"] = os.environ["BENCHMARK_LAB_X_TEST_SOCKET_FD"]
    if "BENCHMARK_LAB_X_LAUNCH_SOCKET_FD" in os.environ:
        env["BENCHMARK_LAB_X_LAUNCH_SOCKET_FD"] = os.environ["BENCHMARK_LAB_X_LAUNCH_SOCKET_FD"]
    for key in ["BENCHMARK_LAB_X_TEST_POPEN_GATE", "BENCHMARK_LAB_X_TEST_PRIVATE_RECEIPT_GATE", "BENCHMARK_LAB_X_TEST_TIMEOUT_SECONDS", "BENCHMARK_LAB_X_TEST_CANDIDATE_EXCEPTION"]:
        if key in os.environ:
            env[key] = os.environ[key]
    env.update({"PI_CODING_AGENT_DIR": str(agent_dir), "PI_SKIP_VERSION_CHECK": "1"})
    return env


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _signal_group(process, signum):
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def _test_event(message):
    fd = os.environ.get("BENCHMARK_LAB_X_TEST_SOCKET_FD")
    if fd is not None:
        os.write(int(fd), f"{message}\n".encode())


def _socket_line(sock):
    raw = bytearray()
    while not raw.endswith(b"\n"):
        block = sock.recv(1)
        if not block:
            raise RuntimeError("canal de permis fermé")
        raw.extend(block)
    return raw.decode().rstrip("\n")


def _launch_permission(config_id):
    fd = os.environ.get("BENCHMARK_LAB_X_LAUNCH_SOCKET_FD")
    if fd is None:
        return
    sock = socket.socket(fileno=int(fd))
    try:
        sock.sendall(f"LAUNCH_REQUEST {config_id}\n".encode())
        if _socket_line(sock) != f"GRANT {config_id}":
            raise RuntimeError("permis de lancement invalide")
    finally:
        sock.detach()


def _request_supervised_termination(config_id, reason):
    sock = socket.socket(fileno=int(os.environ["BENCHMARK_LAB_X_LAUNCH_SOCKET_FD"]))
    try:
        sock.sendall(f"TERMINATE {config_id} {reason}\n".encode())
        sock.settimeout(3)
        while sock.recv(1):
            pass
    except (OSError, TimeoutError):
        pass
    finally:
        sock.detach()


def _attempt_result(raw, expected):
    events = []
    try:
        for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if line.strip():
                events.append(_strict_json_bytes(line.encode(), f"JSONL ligne {number}"))
    except (UnicodeDecodeError, ValueError) as exc:
        return {"incident": f"JSONL_INVALIDE: {exc}", "cost": "INCONNU", "output": "INCONNU", "observed": {key: "INCONNU" for key in OBSERVED_KEYS}, "retry": False, "final_text_sha256": None}
    retry = any("retry" in str(event.get("type", "")).lower() for event in events if isinstance(event, dict))
    finals = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "message_end":
            continue
        message = event.get("message", event)
        # un message non objet ne porte aucun tour assistant attribuable
        if isinstance(message, dict) and message.get("role", "assistant") == "assistant":
            finals.append(message)
    if len(finals) != 1:
        return {"incident": "TOURS_ASSISTANT_INVALIDES", "cost": "INCONNU", "output": "INCONNU", "observed": {key: "INCONNU" for key in OBSERVED_KEYS}, "retry": retry, "final_text_sha256": None}
    final = finals[0]
    observed, structured = {}, False
    for key in OBSERVED_KEYS:
        value = final.get(key, "INCONNU")
        if not isinstance(value, str) or not value.strip():
            # une observation structurée ou vide devient une absence lisible, jamais une représentation brute
            structured, value = True, "INCONNU"
        observed[key] = value
    try:
        cost = _number(final["usage"]["cost"]["total"], "usage.cost.total")
    except (KeyError, TypeError, ValueError):
        cost = None
    content = final.get("content")
    text_parts, text_only = [], isinstance(content, list)
    if text_only:
        for item in content:
            if not isinstance(item, dict) or item.get("type") not in {"text", "thinking"}:
                text_only = False
                break
            if item["type"] == "text":
                if not isinstance(item.get("text"), str):
                    text_only = False
                    break
                text_parts.append(item["text"])
    output = "".join(text_parts) if text_only and "".join(text_parts).strip() else "INCONNU"
    text_only = output != "INCONNU"
    incidents = []
    if retry:
        incidents.append("RETRY_DETECTE")
    if observed["provider"] != expected["provider"] or observed["model"] != expected["model"]:
        incidents.append("IDENTITE_DIVERGENTE")
    if observed["responseModel"] != "INCONNU" and observed["responseModel"] != expected["model"]:
        incidents.append("RESPONSE_MODEL_DIVERGENT")
    if observed["stopReason"] not in {"stop", "end_turn"}:
        incidents.append("ARRET_NON_FINAL")
    if not text_only:
        incidents.append("SORTIE_NON_TEXTUELLE")
    if cost is None:
        incidents.append("COUT_INCONNU_OU_INVALIDE")
    if structured:
        incidents.append("OBSERVATION_NON_TEXTUELLE")
    return {
        "incident": ";".join(incidents) if incidents else "AUCUN",
        "cost": float(cost) if cost is not None else "INCONNU",
        "output": output,
        "observed": observed,
        "retry": retry,
        "final_text_sha256": hashlib.sha256(output.encode()).hexdigest() if text_only else None,
    }


def _receipt(run, config, marker, authority_sha, stdout_path, stderr_path, returncode, result, process_incident):
    incident = result["incident"]
    if process_incident:
        incident = process_incident if incident == "AUCUN" else f"{process_incident};{incident}"
    value = {
        "schema": "benchmark-lab-x-receipt-1",
        "config_id": config["id"],
        "marker": marker.name,
        "marker_sha256": _sha(marker),
        "stdout": stdout_path.name,
        "stdout_sha256": _sha(stdout_path),
        "raw_jsonl_sha256": _sha(stdout_path),
        "stderr": stderr_path.name,
        "stderr_sha256": _sha(stderr_path),
        "authority_sha256": authority_sha,
        "returncode": returncode,
        "incident": incident,
        "retry": bool(result.get("retry")),
        "cost": result["cost"],
        "output": result["output"],
        "final_text_sha256": result.get("final_text_sha256"),
        "observed": result["observed"],
        "requested": config,
        "route_observed": "INCONNU",
        "effort_observed": "INCONNU",
        "completed_at": _now(),
    }
    path = run / f"{config['id']}.receipt.json"
    _write_json(path, value)
    return path, value


def collect(run_dir, authority, repo_root=None, _collection_path=None, _own_sigterm=False, _active_path=None):
    run, run_id = _run_path(run_dir, repo_root)
    if (run / "collection.json").exists():
        raise FileExistsError("collection immuable déjà présente")
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise ValueError("OPENROUTER_API_KEY absente ou vide")
    seal, panel = _validate_prepared(run, run_id)
    auth_path = Path(authority)
    auth_raw = auth_path.read_bytes()
    auth = _strict_json_bytes(auth_raw, str(auth_path))
    forecasts = _validate_s9(auth, run_id, seal, _sha(run / "seal.json"), panel)
    child_env = _bounded_env(_pi_agent_dir(run))
    copied_auth = run / "authorization-s9.json"
    _write_json_bytes(copied_auth, auth_raw)
    authority_sha = _sha(copied_auth)
    matrix = [{"config_id": config["id"], "expected": config, "executed": False, "receipt": None, "receipt_sha256": None, "reason": "NON_TENTEE"} for config in panel]
    receipts, spent, budget_known, stop_reason = [], Decimal(0), True, None
    interrupted = None
    quiescence_failure = None
    active = {"process": None, "sigterm": False}
    try:
      for position, config in enumerate(panel):
        marker = None
        try:
            _validate_prepared(run, run_id)
            if _sha(copied_auth) != authority_sha:
                raise ValueError("autorité S9 copiée altérée")
            _validate_s9(_load_json(copied_auth), run_id, seal, _sha(run / "seal.json"), panel)
            forecast = _number(forecasts[config["id"]], config["id"])
            if forecast > CAP - spent:
                stop_reason = "PREVISION_SUPERIEURE_AU_RESTANT"
                break
            if active["sigterm"]:
                stop_reason, interrupted = "INTERRUPTION_SIGTERM", RuntimeError("collect interrompu par SIGTERM")
                break
            stdout_path, stderr_path = run / f"{config['id']}.stdout.jsonl", run / f"{config['id']}.stderr.txt"
            argv = [seal["pi"]["path"], "--provider", "openrouter", "--model", config["model"], *COMMON_ARGS[2:], "--", (run / "prompt.txt").read_text()]
            process_incident = None
            if active["sigterm"]:
                marker.unlink()
                marker = None
                stop_reason, interrupted = "INTERRUPTION_SIGTERM", RuntimeError("collect interrompu par SIGTERM")
                break
            try:
                _launch_permission(config["id"])
                marker = run / f"{config['id']}.started.json"
                _write_json(marker, {"schema": "benchmark-lab-x-attempt-1", "config_id": config["id"], "nonce": secrets.token_hex(16), "seal_sha256": _sha(run / "seal.json"), "authority_sha256": authority_sha, "started_at": _now()})
                test_fd = os.environ.get("BENCHMARK_LAB_X_TEST_SOCKET_FD")
                process = subprocess.Popen(
                    argv, cwd=run, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    umask=0o077,
                    start_new_session=os.environ.get("BENCHMARK_LAB_X_LAUNCH_SOCKET_FD") is None,
                    pass_fds=(int(test_fd),) if test_fd is not None else (),
                )
                _test_event(f"CANDIDATE_POPENED_BEFORE_ACTIVE_PID {config['id']} {process.pid}")
                if os.environ.get("BENCHMARK_LAB_X_TEST_POPEN_GATE") == config["id"]:
                    _test_gate()
                if _active_path:
                    _write_exclusive(_active_path, str(process.pid))
                active["process"] = process
                try:
                    if active["sigterm"]:
                        _kill_group(process)
                        stdout, stderr = process.communicate()
                        process_incident = "SIGTERM_GROUPE_TUE"
                    else:
                        if os.environ.get("BENCHMARK_LAB_X_TEST_CANDIDATE_EXCEPTION") == config["id"]:
                            raise RuntimeError("exception candidat causale")
                        timeout = float(os.environ.get("BENCHMARK_LAB_X_TEST_TIMEOUT_SECONDS", TIMEOUT_SECONDS))
                        stdout, stderr = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    if os.environ.get("BENCHMARK_LAB_X_LAUNCH_SOCKET_FD") is not None:
                        _request_supervised_termination(config["id"], "CANDIDATE_TIMEOUT")
                        raise RuntimeError("timeout candidat supervisé")
                    _kill_group(process)
                    stdout, stderr = process.communicate()
                    process_incident = "TIMEOUT_GROUPE_TUE"
                except BaseException as exc:
                    if os.environ.get("BENCHMARK_LAB_X_LAUNCH_SOCKET_FD") is not None:
                        _request_supervised_termination(config["id"], "CANDIDATE_EXCEPTION")
                        raise
                    pgid = process.pid
                    _kill_group(process)
                    stdout, stderr = process.communicate()
                    if isinstance(exc, KeyboardInterrupt):
                        try:
                            pgid_gone = _wait_pgid_gone(pgid, 5)
                        except BaseException as wait_exc:
                            quiescence_failure = RuntimeError("quiescence du groupe candidat non prouvée")
                            raise quiescence_failure from wait_exc
                        if not pgid_gone:
                            quiescence_failure = RuntimeError("groupe candidat encore vivant")
                            raise quiescence_failure
                    process_incident = "INTERRUPTION_GROUPE_TUE"
                    interrupted = exc
                if active["sigterm"]:
                    process_incident = "SIGTERM_GROUPE_TUE"
                returncode = process.returncode
                if returncode and not process_incident:
                    process_incident = "ERREUR_FOURNISSE"
                active["process"] = None
                if _active_path:
                    Path(_active_path).unlink(missing_ok=True)
            except OSError as exc:
                stdout, stderr = b"", f"PROCESS_START_ERROR:{type(exc).__name__}".encode()
                process_incident, returncode = "PROCESS_START_ERROR", None
            _write_exclusive(stdout_path, stdout)
            _write_exclusive(stderr_path, stderr)
            result = _attempt_result(stdout, config)
            receipt_path, receipt = _receipt(run, config, marker, authority_sha, stdout_path, stderr_path, returncode, result, process_incident)
            receipts.append({"config_id": config["id"], "path": receipt_path.name, "sha256": _sha(receipt_path)})
            matrix[position].update({"executed": True, "receipt": receipt_path.name, "receipt_sha256": _sha(receipt_path), "reason": receipt["incident"] if receipt["incident"] != "AUCUN" else "EXECUTEE"})
            _test_event(f"{config['id']}_DONE")
            if active["sigterm"]:
                if receipt["cost"] == "INCONNU":
                    budget_known = False
                else:
                    spent += _number(receipt["cost"], "coût reçu")
                stop_reason, interrupted = "INTERRUPTION_SIGTERM", RuntimeError("collect interrompu par SIGTERM")
                break
            if receipt["cost"] == "INCONNU":
                budget_known, stop_reason = False, "COUT_OU_BUDGET_INCONNU"
                break
            spent += _number(receipt["cost"], "coût reçu")
            if spent > CAP:
                stop_reason = "PLAFOND_DEPASSE"
                break
            if any(token in receipt["incident"] for token in ["IDENTITE_DIVERGENTE", "RESPONSE_MODEL_DIVERGENT", "RETRY_DETECTE"]):
                stop_reason = "PROVENANCE_OU_ROUTE_DIVERGENTE"
                break
            if interrupted:
                stop_reason = "INTERRUPTION"
                break
        except BaseException as exc:
            if marker:
                if marker.exists() and not any(item["config_id"] == config["id"] for item in receipts):
                    stop_reason = f"ETAT_AMBIGU: {type(exc).__name__}"
            if isinstance(exc, KeyboardInterrupt):
                interrupted = exc
            else:
                stop_reason = stop_reason or f"VALIDATION_AVANT_APPEL: {exc}"
            matrix[position]["reason"] = stop_reason
            break
      if quiescence_failure:
          raise quiescence_failure
      _private_tree(run)
      if active["sigterm"]:
          stop_reason, interrupted = "INTERRUPTION_SIGTERM", RuntimeError("collect interrompu par SIGTERM")
      if stop_reason:
          for entry in matrix:
              if not entry["executed"] and entry["reason"] == "NON_TENTEE":
                  entry["reason"] = f"NON_TENTEE_APRES_{stop_reason}"
      collection = {
          "schema": "benchmark-lab-x-collection-1",
          "run": run_id,
          "seal_sha256": _sha(run / "seal.json"),
          "authority_sha256": authority_sha,
          "receipts": receipts,
          "matrix": matrix,
          "spent": float(spent) if budget_known else "INCONNU",
          "budget_known": budget_known,
          "stop_reason": stop_reason or "COMPLETE",
          "created_at": _now(),
      }
      if active["sigterm"]:
          collection["stop_reason"] = "INTERRUPTION_SIGTERM"
          interrupted = RuntimeError("collect interrompu par SIGTERM")
      collection_path = Path(_collection_path) if _collection_path else run / "collection.json"
      _write_json(collection_path, collection)
      _private_tree(run)
      _validate_collection(run, run_id, seal, panel, collection_path)
      if interrupted:
          raise interrupted
      return collection
    except BaseException:
        raise


def _validate_collection(run, run_id, seal, panel, collection_path=None):
    collection_path = Path(collection_path) if collection_path else run / "collection.json"
    collection = _load_json(collection_path)
    if collection.get("schema") != "benchmark-lab-x-collection-1" or collection.get("run") != run_id or collection.get("seal_sha256") != _sha(run / "seal.json"):
        raise ValueError("collection invalide ou non liée")
    auth_path = run / "authorization-s9.json"
    if not auth_path.is_file() or collection.get("authority_sha256") != _sha(auth_path):
        raise ValueError("autorité S9 de collection absente ou altérée")
    _validate_s9(_load_json(auth_path), run_id, seal, _sha(run / "seal.json"), panel)
    matrix = collection.get("matrix")
    if not isinstance(matrix, list) or len(matrix) != len(panel) or [item.get("config_id") for item in matrix] != [item["id"] for item in panel]:
        raise ValueError("matrice attendu/exécuté invalide")
    if any(item.get("expected") != expected or not isinstance(item.get("executed"), bool) for item, expected in zip(matrix, panel)):
        raise ValueError("configuration attendue ou état exécuté divergent")
    seen, receipts = set(), []
    for item in collection.get("receipts", []):
        if item.get("config_id") in seen or item.get("config_id") not in {p["id"] for p in panel}:
            raise ValueError("reçu dupliqué ou inconnu")
        seen.add(item["config_id"])
        receipt_path = run / item.get("path", "")
        if receipt_path.name != f"{item['config_id']}.receipt.json" or not receipt_path.is_file() or _sha(receipt_path) != item.get("sha256"):
            raise ValueError("reçu absent ou altéré")
        receipt = _load_json(receipt_path)
        if receipt.get("schema") != "benchmark-lab-x-receipt-1" or receipt.get("config_id") != item["config_id"] or receipt.get("authority_sha256") != collection["authority_sha256"]:
            raise ValueError("reçu fabriqué ou mal lié")
        for kind in ["marker", "stdout", "stderr"]:
            source = run / receipt[kind]
            if not source.is_file() or _sha(source) != receipt[f"{kind}_sha256"]:
                raise ValueError(f"preuve brute altérée: {kind}")
        marker = _load_json(run / receipt["marker"])
        if marker.get("schema") != "benchmark-lab-x-attempt-1" or marker.get("config_id") != item["config_id"] or marker.get("authority_sha256") != collection["authority_sha256"]:
            raise ValueError("marqueur de tentative invalide")
        config = next(p for p in panel if p["id"] == item["config_id"])
        if receipt.get("requested") != config or receipt.get("route_observed") != "INCONNU" or receipt.get("effort_observed") != "INCONNU":
            raise ValueError("provenance demandée ou observation fabriquée")
        parsed = _attempt_result((run / receipt["stdout"]).read_bytes(), config)
        for key in ["cost", "output", "observed", "retry", "final_text_sha256"]:
            if receipt.get(key) != parsed.get(key):
                raise ValueError("reçu non dérivé du stdout collecté")
        if receipt.get("raw_jsonl_sha256") != receipt.get("stdout_sha256"):
            raise ValueError("empreinte JSONL brute divergente")
        receipts.append((item, receipt))
    ids = [item["config_id"] for item in collection.get("receipts", [])]
    if ids != [item["id"] for item in panel][:len(ids)]:
        raise ValueError("collection non issue du chemin séquentiel collect")
    markers = {path.name.removesuffix(".started.json") for path in run.glob("*.started.json")}
    if (collection.get("stop_reason") != "COMPLETE" and not set(ids) <= markers) or (collection.get("stop_reason") == "COMPLETE" and markers != set(ids)):
        raise ValueError("marqueur sans reçu ou reçu sans marqueur")
    for entry in matrix:
        receipt_item = next((item for item in collection["receipts"] if item["config_id"] == entry["config_id"]), None)
        if entry.get("executed") is not bool(receipt_item) or entry.get("receipt") != (receipt_item["path"] if receipt_item else None) or entry.get("receipt_sha256") != (receipt_item["sha256"] if receipt_item else None) or not isinstance(entry.get("reason"), str) or not entry["reason"]:
            raise ValueError("matrice et reçus divergents")
    known = all(receipt["cost"] != "INCONNU" for _, receipt in receipts)
    spent = sum((_number(receipt["cost"], "coût reçu") for _, receipt in receipts), Decimal(0)) if known else None
    if collection.get("budget_known") is not known or collection.get("spent") != (float(spent) if known else "INCONNU"):
        raise ValueError("réconciliation budgétaire de collection invalide")
    return collection, receipts


def _require_complete(collection, panel):
    if len(collection.get("matrix", [])) != len(panel) or not all(item.get("executed") for item in collection["matrix"]):
        raise ValueError("panel incomplet: revue et page interdites")


def review(run_dir, repo_root=None):
    run, run_id = _run_path(run_dir, repo_root)
    seal, panel = _validate_prepared(run, run_id)
    collection, receipts = _validate_collection(run, run_id, seal, panel)
    _require_complete(collection, panel)
    review_dir = run / "review"
    if review_dir.exists():
        raise FileExistsError("revue immuable déjà présente")
    os.mkdir(review_dir, 0o700)
    order = list(range(len(receipts)))
    secrets.SystemRandom().shuffle(order)
    cases = []
    private_cases = []
    for index in order:
        item, receipt = receipts[index]
        blind_id = f"D-{secrets.token_hex(6).upper()}"
        copy = review_dir / f"{blind_id}.txt"
        output = receipt["output"] if receipt["output"] != "INCONNU" else f"INCONNU\nIncident: {receipt['incident']}"
        content = f"Cas {blind_id}\n\n{output}"
        _write_exclusive(copy, content.encode())
        cases.append({"blind_id": blind_id, "copy": f"review/{copy.name}", "copy_sha256": _sha(copy)})
        private_cases.append({"blind_id": blind_id, "receipt_sha256": item["sha256"]})
    review_map = {"schema": "benchmark-lab-x-review-map-1", "cases": private_cases}
    _write_json(run / "review-map.json", review_map)
    campaign = _campaign()
    dossier = {
        "schema": "benchmark-lab-x-review-1",
        "review_map_sha256": _sha(run / "review-map.json"),
        "cases": cases,
        "checklist": {"obligations": campaign["obligations"], "fatal_errors": campaign["fatal_errors"]},
        "created_at": _now(),
    }
    _write_json(run / "review.json", dossier)
    _private_tree(run)
    return dossier


def _validate_review(run, collection, receipts):
    review_path = run / "review.json"
    dossier = _load_json(review_path)
    campaign = _campaign()
    if set(dossier) != {"schema", "review_map_sha256", "cases", "checklist", "created_at"} or dossier.get("schema") != "benchmark-lab-x-review-1":
        raise ValueError("revue non liée ou altérée")
    if dossier.get("checklist") != {"obligations": campaign["obligations"], "fatal_errors": campaign["fatal_errors"]}:
        raise ValueError("checklist de revue divergente")
    receipt_hashes = {item["sha256"] for item, _ in receipts}
    cases = dossier.get("cases", [])
    map_path = run / "review-map.json"
    if not map_path.is_file() or dossier.get("review_map_sha256") != _sha(map_path):
        raise ValueError("table privée absente ou altérée")
    review_map = _load_json(map_path)
    private_cases = review_map.get("cases", [])
    public_ids = [case.get("blind_id") for case in cases]
    if set(review_map) != {"schema", "cases"} or review_map.get("schema") != "benchmark-lab-x-review-map-1" or len(cases) != len(receipts) or len(set(public_ids)) != len(receipts):
        raise ValueError("revue aveugle invalide")
    if len(private_cases) != len(receipts) or any(set(item) != {"blind_id", "receipt_sha256"} for item in private_cases) or {item.get("blind_id") for item in private_cases} != set(public_ids) or {item.get("receipt_sha256") for item in private_cases} != receipt_hashes:
        raise ValueError("bijection privée invalide")
    for case in cases:
        copy = run / case.get("copy", "")
        if set(case) != {"blind_id", "copy", "copy_sha256"} or copy.parent != run / "review" or not copy.is_file() or _sha(copy) != case.get("copy_sha256"):
            raise ValueError("copie aveugle altérée")
    return dossier, review_map


def _validate_decisions(value, dossier):
    if value.get("schema") != "benchmark-lab-x-decisions-1" or value.get("review_sha256") is None or value.get("accepted") is not True:
        raise ValueError("décisions non acceptées ou schéma invalide")
    expected_ids = {case["blind_id"] for case in dossier["cases"]}
    decisions = value.get("decisions")
    if not isinstance(decisions, list) or {item.get("blind_id") for item in decisions} != expected_ids or len(decisions) != len(expected_ids):
        raise ValueError("exactement une décision par cas aveugle est requise")
    checks = {item["id"] for item in dossier["checklist"]["obligations"] + dossier["checklist"]["fatal_errors"]}
    for decision in decisions:
        if decision.get("verdict") not in _campaign()["verdicts"] or not isinstance(decision.get("reason"), str) or not decision["reason"].strip() or decision.get("role") != "responsable de campagne":
            raise ValueError("verdict, motif ou rôle invalide")
        findings = decision.get("findings")
        if not isinstance(findings, dict) or set(findings) != checks:
            raise ValueError("constats complets requis")
        if any(not isinstance(finding, dict) or not isinstance(finding.get("finding"), str) or not finding["finding"].strip() or finding.get("evidence") not in ALLOWED_EVIDENCE for finding in findings.values()):
            raise ValueError("constat ou référence de preuve invalide")
        secondary = decision.get("secondary")
        if decision["verdict"] == "SATISFAIT":
            if not isinstance(secondary, dict) or set(secondary) != {"S1", "S2"} or any(value not in {"acceptable", "excellent"} for value in secondary.values()):
                raise ValueError("S1 et S2 requis pour SATISFAIT")
        elif secondary not in (None, {}):
            raise ValueError("aucun critère secondaire hors SATISFAIT")
    return decisions


def _validate_s10(auth, run_id, seal_sha, review_sha, decisions_sha, s9_id):
    expected = {
        "schema": "benchmark-lab-x-s10-authorization-1",
        "effect": "product_execution_and_acceptance_s10",
        "run": run_id,
        "seal_sha256": seal_sha,
        "review_sha256": review_sha,
        "decisions_sha256": decisions_sha,
    }
    for key, value in expected.items():
        if auth.get(key) != value:
            raise ValueError(f"autorité S10 divergente: {key}")
    if not isinstance(auth.get("authority_id"), str) or not auth["authority_id"].strip() or auth["authority_id"] == s9_id:
        raise ValueError("autorité S10 absente ou non distincte")


def _safe_satisfied(receipt):
    # Le coût inconnu n'est pas un défaut d'intégrité de la sortie : il rend seulement la conclusion économique INCOMPLETE
    incidents = set(receipt["incident"].split(";")) - {"AUCUN", "COUT_INCONNU_OU_INVALIDE"}
    return (
        isinstance(receipt["output"], str) and bool(receipt["output"].strip()) and receipt["output"] != "INCONNU" and not incidents and not receipt["retry"]
        and receipt["returncode"] == 0
        and receipt["observed"].get("provider") == receipt["requested"]["provider"]
        and receipt["observed"].get("model") == receipt["requested"]["model"]
        and receipt["observed"].get("responseModel", "INCONNU") in {"INCONNU", receipt["requested"]["model"]}
    )


def _results(run, dossier, review_map, receipts, decisions):
    by_hash = {item["sha256"]: receipt for item, receipt in receipts}
    private_by_id = {item["blind_id"]: item["receipt_sha256"] for item in review_map["cases"]}
    by_id = {item["blind_id"]: item for item in decisions}
    configs = []
    for case in dossier["cases"]:
        receipt, decision = by_hash[private_by_id[case["blind_id"]]], by_id[case["blind_id"]]
        if decision["verdict"] == "SATISFAIT" and not _safe_satisfied(receipt):
            raise ValueError("SATISFAIT impossible sans sortie attribuable intègre")
        configs.append({
            "blind_id": case["blind_id"], "requested": receipt["requested"],
            "observed": {**receipt["observed"], "route": receipt["route_observed"], "effort": receipt["effort_observed"]},
            "verdict": decision["verdict"], "reason": decision["reason"], "secondary": decision.get("secondary"),
            "findings": decision["findings"], "role": decision["role"], "incident": receipt["incident"],
            "unknowns": [key for key, value in {**receipt["observed"], "route": receipt["route_observed"], "effort": receipt["effort_observed"]}.items() if value == "INCONNU"],
            "cost": receipt["cost"], "fingerprints": {"raw_jsonl": receipt["raw_jsonl_sha256"], "final_text": receipt["final_text_sha256"], "receipt": private_by_id[case["blind_id"]], "blind_copy": case["copy_sha256"]},
        })
    satisfied = [item for item in configs if item["verdict"] == "SATISFAIT"]
    if any(item["cost"] == "INCONNU" for item in satisfied):
        economy = {"status": "INCOMPLETE", "known_costs": {item["blind_id"]: item["cost"] for item in satisfied if item["cost"] != "INCONNU"}, "least_expensive": [], "benefits": {}}
    elif satisfied:
        minimum = min(item["cost"] for item in satisfied)
        cheapest = [item for item in satisfied if item["cost"] == minimum]
        benefits = {}
        for item in satisfied:
            if item["cost"] > minimum:
                gains = [key for key in ["S1", "S2"] if item["secondary"][key] == "excellent" and any(base["secondary"][key] == "acceptable" for base in cheapest)]
                if gains:
                    benefits[item["blind_id"]] = gains
        economy = {"status": "COMPLETE", "known_costs": {item["blind_id"]: item["cost"] for item in satisfied}, "least_expensive": [item["blind_id"] for item in cheapest], "benefits": benefits}
    else:
        economy = {"status": "COMPLETE", "known_costs": {}, "least_expensive": [], "benefits": {}}
    campaign = _campaign()
    return {
        "schema": "benchmark-lab-x-results-1", "task": campaign["task_id"], "contract": campaign,
        "dates": {"prepared": _load_json(run / "seal.json")["created_at"], "reviewed": dossier["created_at"], "built": _now()},
        "conditions": {"requested": campaign["common_conditions"], "applied": {"settings_sha256": _sha(run / "settings.json"), "models_sha256": _sha(run / "models.json")}, "observed": {"pi_version": PI_VERSION, **_load_json(run / "seal.json")["public_environment"]}},
        "configurations": configs, "economy": economy,
        "fingerprints": {"contract": _sha(PACKAGE_DIR / "campaign.json"), "panel": _sha(run / "panel.json"), "seal": _sha(run / "seal.json"), "collection": _sha(run / "collection.json"), "review": _sha(run / "review.json"), "decisions": None, "authority_s9": _sha(run / "authorization-s9.json"), "authority_s10": None},
        "attribution_limit": "Le verdict porte sur la configuration observée sous les conditions de test communes déclarées, pas sur le modèle isolé.",
    }


def _e(value):
    return html.escape(str(value), quote=True)


def _money(value, decimals=4):
    if value == "INCONNU":
        return "Non communiqué"
    amount = Decimal(str(value))
    rendered = format(amount, "f") if decimals is None else f"{amount:.{decimals}f}"
    return rendered.replace(".", ",") + " USD"


def _public_label(item):
    requested = item["requested"]
    return f"{requested['id']} · {requested['model']}"


VERDICT_CLASS = {"SATISFAIT": "success", "NE SATISFAIT PAS": "failure", "INDETERMINE": "unknown"}
BRIEF_FIELDS = ("title", "context", "objective", "decision")
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]

# Brief de présentation propre au scénario historique, rédigé après la campagne et relié à aucune empreinte de source
# Il ne fait pas partie du contrat scellé ; une future campagne fige son brief dans campaign.json avant exécution
PRESENTATION_BRIEFS = {
    "quote-thread-summary": {
        "title": "Synthèse d’un fil de courriels de devis",
        "context": "Douze courriels synthétiques entre un atelier et un studio, présentés hors ordre chronologique, autour d’un devis de refonte de site vitrine. Le montant, la date des maquettes et une question restée sans réponse évoluent au fil des messages.",
        "objective": "Produire une synthèse fidèle qui distingue les décisions confirmées des demandes ou annonces, restitue les montants et les échéances, signale les révisions résolues et les points encore ouverts, sans inventer de fait.",
        "decision": "Quelles configurations testées produisent cette synthèse sans erreur éliminatoire, et parmi elles laquelle coûte le moins.",
    },
}


def _validate_brief(campaign):
    brief = campaign.get("brief")
    if brief is None:
        return None
    if not isinstance(brief, dict) or set(brief) != set(BRIEF_FIELDS) or any(not isinstance(brief[key], str) or not brief[key].strip() for key in BRIEF_FIELDS):
        raise ValueError("brief de campagne invalide: exactement title, context, objective et decision, chacun chaîne non vide ; le résultat attendu reste expected_result")
    return brief


def _task_brief(campaign):
    frozen = _validate_brief(campaign)
    if frozen:
        return {**frozen, "source": "contract"}
    fallback = PRESENTATION_BRIEFS.get(campaign["task_id"])
    if fallback:
        return {**fallback, "source": "presentation"}
    return {"title": campaign["task_id"], "context": None, "objective": None, "decision": None, "source": "none"}


def _human_duration(seconds):
    if type(seconds) is not int or seconds < 0:
        return "Non communiqué"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = [(hours, "h"), (minutes, "min"), (seconds, "s")]
    return " ".join(f"{value} {unit}" for value, unit in parts if value) or "0 s"


def _human_date(raw, with_time=False):
    try:
        value = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return str(raw)
    day = f"{value.day} {MONTHS[value.month - 1]} {value.year}"
    if not with_time:
        return day
    offset = value.strftime("%z")
    zone = "UTC" if offset == "+0000" else f"UTC{offset[:3]}:{offset[3:]}"
    return f"{day} à {value:%H:%M} {zone}"


def _render_dates(dates):
    labels = {"prepared": "Campagne préparée", "reviewed": "Revue réalisée", "built": "Restitution initiale"}
    items = "".join(f"<li><span>{label}</span><time datetime=\"{_e(dates[key])}\">{_e(_human_date(dates[key], with_time=True))}</time></li>" for key, label in labels.items())
    return f"<ul class=\"dates\">{items}</ul>"


def _render_brief(campaign):
    brief = _task_brief(campaign)
    undocumented = "Non documenté : aucun brief figé avant exécution."
    rows = [("Contexte", brief["context"] or undocumented), ("Objectif", brief["objective"] or undocumented), ("Résultat attendu", campaign["expected_result"]), ("Décision éclairée", brief["decision"] or undocumented)]
    items = lambda entries: "".join(f"<li><b>{_e(item['id'])}</b> {_e(item['text'])}</li>" for item in entries)
    secondary = "".join(f"<li><b>{_e(item['id'])}</b> {_e(item['text'])} (valeur favorable : {_e(item['favorable'])})</li>" for item in campaign["secondary"])
    notes = {
        "contract": "",
        "presentation": "<p class=\"note\">Ce brief de présentation a été rédigé après la campagne pour rendre la restitution lisible. Il ne fait pas partie du contrat scellé et n’est relié à aucune empreinte de source : il n’est pas prouvé par les artefacts de la campagne.</p>",
        "none": "<p class=\"note\">Cette tâche n’a pas de brief figé avant exécution : seul l’identifiant et le résultat attendu du contrat sont disponibles.</p>",
    }
    return (
        "<dl>" + "".join(f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in rows) + "</dl>" + notes[brief["source"]]
        + f"<details><summary>Contrat de réussite : {len(campaign['obligations'])} obligations, {len(campaign['fatal_errors'])} erreurs éliminatoires, {len(campaign['secondary'])} critères secondaires</summary>"
        f"<h3>Obligations</h3><ul>{items(campaign['obligations'])}</ul><h3>Erreurs éliminatoires</h3><ul>{items(campaign['fatal_errors'])}</ul>"
        f"<h3>Critères secondaires</h3><ul>{secondary}</ul><p class=\"meta\">Verdicts permis : {_e(', '.join(campaign['verdicts']))}. Un critère secondaire décrit seulement une configuration déjà SATISFAIT.</p></details>"
    )


def _render_fingerprints(fingerprints):
    labels = {
        "contract": "Contrat", "panel": "Panel", "seal": "Sceau de préparation",
        "collection": "Collection", "review": "Revue", "decisions": "Décisions",
        "authority_s9": "Autorité de collecte", "authority_s10": "Autorité de restitution",
        "raw_jsonl": "Sortie brute", "final_text": "Texte final",
        "receipt": "Reçu", "blind_copy": "Copie aveugle",
    }
    items = []
    for key, value in fingerprints.items():
        display = "Non disponible" if value is None else f"<code>{_e(value)}</code>"
        items.append(f"<li><span>{_e(labels.get(key, key))}</span>{display}</li>")
    return "<ul class=\"fingerprints\">" + "".join(items) + "</ul>"


def _rows(values):
    return "".join(f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in values)


INCIDENT_LABELS = {
    "COUT_INCONNU_OU_INVALIDE": "coût non communiqué par le canal observé",
    "OBSERVATION_NON_TEXTUELLE": "une observation reçue n’était pas un texte et a été remplacée par une absence",
    "RETRY_DETECTE": "nouvelle tentative détectée",
    "IDENTITE_DIVERGENTE": "identité observée divergente de la demande",
    "RESPONSE_MODEL_DIVERGENT": "modèle de réponse divergent",
    "ARRET_NON_FINAL": "arrêt non final de la réponse",
    "SORTIE_NON_TEXTUELLE": "sortie non textuelle",
    "JSONL_INVALIDE": "flux de réponse illisible",
    "TOURS_ASSISTANT_INVALIDES": "nombre de tours de réponse invalide",
    "TIMEOUT_GROUPE_TUE": "durée maximale dépassée, exécution arrêtée",
    "SIGTERM_GROUPE_TUE": "campagne interrompue par le superviseur pendant l’exécution",
    "INTERRUPTION_GROUPE_TUE": "exécution interrompue par le dispositif",
    "ERREUR_FOURNISSE": "erreur du fournisseur",
    "PROCESS_START_ERROR": "démarrage du harnais impossible",
}


def _incident_label(token):
    # le jeton peut porter un message technique après un deux-points ; seul le libellé humain atteint la page
    return INCIDENT_LABELS.get(token.split(":", 1)[0].strip(), "incident technique non répertorié")


def _render_row(item, secondary_definitions):
    requested, observed = item["requested"], item["observed"]
    config_id = requested["id"]
    verdict_class = VERDICT_CLASS[item["verdict"]]
    evidence_labels = {"blind-copy": "copie aveugle", "receipt": "reçu d’exécution", "incident": "reçu d’incident"}
    findings = "".join(
        f"<li><b>{_e(key)}</b><span>{_e(value['finding'])}</span><small>Preuve : {_e(evidence_labels.get(value['evidence'], value['evidence']))}</small></li>"
        for key, value in item["findings"].items()
    )
    secondary = item["secondary"] or {}
    if secondary:
        criteria = "".join(
            f"<li><span>{_e(definition['text'])}</span><strong>{_e(secondary[definition['id']].capitalize())}</strong></li>"
            for definition in secondary_definitions
        )
    else:
        criteria = "<li class=\"not-evaluated\">Critères secondaires non évalués pour cette configuration.</li>"
    unknown_labels = {
        "provider": "Fournisseur non communiqué par le canal observé",
        "model": "Modèle non communiqué par le canal observé",
        "responseModel": "Modèle de réponse non communiqué par le canal observé",
        "stopReason": "Motif d’arrêt non communiqué par le canal observé",
        "route": "Route observée non communiquée",
        "effort": "Effort observé non communiqué",
    }
    unknowns = "".join(f"<li>{_e(unknown_labels.get(key, 'Valeur non communiquée'))}</li>" for key in item["unknowns"])
    shown = lambda value: "Non communiqué" if value == "INCONNU" else value
    observed_rows = [("Fournisseur", shown(observed.get("provider"))), ("Modèle", shown(observed.get("model")))]
    if observed.get("responseModel") != "INCONNU":
        observed_rows.append(("Modèle déclaré dans la réponse", observed.get("responseModel")))
    if observed.get("route") != "INCONNU":
        observed_rows.append(("Route", observed.get("route")))
    if observed.get("effort") != "INCONNU":
        observed_rows.append(("Effort", observed.get("effort")))
    if item["incident"] == "AUCUN":
        incident = "Aucun incident constaté."
    else:
        incident = "Incident constaté : " + _e(", ".join(_incident_label(token) for token in item["incident"].split(";"))) + "."
    return (
        f"<li><article class=\"result result--{verdict_class}\" id=\"config-{_e(config_id)}\" aria-labelledby=\"config-{_e(config_id)}-title\">"
        f"<div class=\"result-head\"><h3 id=\"config-{_e(config_id)}-title\"><span class=\"config-id\">{_e(config_id)}</span>{_e(requested['model'])}</h3>"
        f"<p class=\"verdict verdict--{verdict_class}\">{_e(item['verdict'])}</p></div>"
        f"<p class=\"reason\">{_e(item['reason'])}</p>"
        f"<details><summary>Examiner le résultat {_e(config_id)}</summary><div class=\"detail-grid\">"
        f"<section><h4>Configuration demandée</h4><dl>{_rows([('Fournisseur', requested['provider']), ('Modèle', requested['model']), ('Route amont', requested['upstream']), ('Effort demandé', requested['thinking'])])}</dl></section>"
        f"<section><h4>Configuration observée</h4><dl>{_rows(observed_rows)}</dl><ul class=\"unknowns\">{unknowns}</ul><p>{incident}</p></section></div>"
        f"<h4>Critères secondaires</h4><ul class=\"criteria\">{criteria}</ul>"
        f"<h4>Constats de la revue</h4><ul class=\"findings\">{findings}</ul>"
        f"<p class=\"meta\">Décision portée par {_e(item['role'])}. Les empreintes de cette configuration figurent dans « Vérifier cette restitution ».</p>"
        "</details></article></li>"
    )


def _economic_status(item, economy):
    satisfied, known = item["verdict"] == "SATISFAIT", item["cost"] != "INCONNU"
    if satisfied and known and economy["status"] == "COMPLETE":
        return "in", "Prise en compte dans la recommandation"
    if satisfied and known:
        return "in", "Coût connu ; recommandation suspendue (conclusion INCOMPLETE)"
    if satisfied:
        return "unknown", "Coût non communiqué : la recommandation reste incomplète"
    if known:
        return "out", "Dépense observée, exclue de la recommandation"
    return "out", "Coût non communiqué, exclue de la recommandation"


def _spend_tile(label, items):
    known = [Decimal(str(item["cost"])) for item in items if item["cost"] != "INCONNU"]
    unknown = len(items) - len(known)
    if not items:
        return f"<p><span>{_e(label)}</span><strong>Aucune configuration</strong></p>"
    if unknown == 0:
        return f"<p><span>{_e(label)} : dépense observée</span><strong>{_e(_money(sum(known, Decimal(0))))}</strong></p>"
    if not known:
        return f"<p><span>{_e(label)} : dépense connue</span><strong>Aucun coût communiqué</strong><span>{unknown} coût{'s' if unknown > 1 else ''} non communiqué{'s' if unknown > 1 else ''} sur {len(items)}</span></p>"
    return f"<p><span>{_e(label)} : dépense connue</span><strong>{_e(_money(sum(known, Decimal(0))))}</strong><span>{unknown} coût{'s' if unknown > 1 else ''} non communiqué{'s' if unknown > 1 else ''} sur {len(items)}</span></p>"


def _render_economy(results):
    economy, configurations, campaign = results["economy"], results["configurations"], results["contract"]
    by_blind_id = {item["blind_id"]: item for item in configurations}
    least = [by_blind_id[key] for key in economy["least_expensive"] if key in by_blind_id]
    if economy["status"] == "INCOMPLETE":
        conclusion = "Conclusion économique INCOMPLETE : le coût d’au moins une configuration admissible est inconnu ou non comparable. Les coûts connus restent visibles, sans option déclarée globalement moins chère."
    elif len(least) == 1:
        conclusion = f"La configuration la moins chère parmi celles qui satisfont le contrat est {_public_label(least[0])}, à {_money(least[0]['cost'])}."
    elif least:
        conclusion = "Les configurations co-moins-chères parmi celles qui satisfont le contrat sont " + ", ".join(_public_label(item) for item in least) + "."
    else:
        conclusion = "Aucune configuration ne satisfait le contrat : aucune recommandation économique n’est possible."
    known = [Decimal(str(item["cost"])) for item in configurations if item["cost"] != "INCONNU"]
    maximum = max(known) if known else None
    rows = []
    for item in configurations:
        kind, status = _economic_status(item, economy)
        excluded = item["verdict"] != "SATISFAIT"
        if item["cost"] != "INCONNU" and maximum is not None:
            # un coût nul est un coût communiqué : barre vide, sans division par zéro
            width = (Decimal(str(item["cost"])) / maximum * 100).quantize(Decimal("0.1")) if maximum > 0 else Decimal("0.0")
            bar = f"<div class=\"bar\" style=\"--w:{_e(width)}%\" aria-hidden=\"true\"></div>"
        elif maximum is not None:
            bar = "<div class=\"bar bar--none\" aria-hidden=\"true\"></div>"
        else:
            bar = ""
        rows.append(
            f"<li class=\"{'excluded' if excluded else 'included'}\">"
            f"<p class=\"name\"><span class=\"cell-label\">Configuration</span>{_e(_public_label(item))}</p>"
            f"<p><span class=\"cell-label\">Verdict</span><span class=\"verdict verdict--{VERDICT_CLASS[item['verdict']]}\">{_e(item['verdict'])}</span></p>"
            f"<p class=\"cost\"><span class=\"cell-label\">Coût observé</span>{_e(_money(item['cost']))}</p>"
            f"<p class=\"status status--{kind}\"><span class=\"cell-label\">Statut économique</span><span class=\"status-text\">{_e(status)}</span></p>{bar}</li>"
        )
    unknown_total = len(configurations) - len(known)
    unknown_note = f" ; {unknown_total} coût{'s' if unknown_total > 1 else ''} non communiqué{'s' if unknown_total > 1 else ''} sans barre." if unknown_total else "."
    if maximum is None:
        scale = "Aucun coût communiqué : pas d’échelle commune ni de barre."
    elif maximum == 0:
        scale = "Tous les coûts communiqués sont nuls : barres vides, sans échelle" + unknown_note
    else:
        scale = f"Barres à l’échelle commune du coût connu le plus élevé ({_e(_money(maximum))}) ; les configurations non admissibles sont hachurées" + unknown_note
    ledger = (
        f"<p class=\"scale\">{scale}</p>"
        "<ol class=\"ledger\" aria-label=\"Registre des coûts observés\"><li class=\"ledger-head\" aria-hidden=\"true\"><span>Configuration</span><span>Verdict</span><span>Coût observé</span><span>Statut économique</span></li>"
        + "".join(rows) + "</ol>"
    )
    admissible = [item for item in configurations if item["verdict"] == "SATISFAIT"]
    excluded_items = [item for item in configurations if item["verdict"] != "SATISFAIT"]
    totals = (
        "<div class=\"spend-summary\">" + _spend_tile("Toutes configurations", configurations)
        + f"<p><span>Plafond de campagne</span><strong>{_e(_money(campaign['cost_basis']['cap'], 2))}</strong></p>"
        + _spend_tile("Configurations admissibles", admissible) + _spend_tile("Hors recommandation", excluded_items) + "</div>"
    )
    if economy["benefits"]:
        definitions = {item["id"]: item["text"] for item in campaign["secondary"]}
        benefit_items = "".join(
            f"<li><strong>{_e(_public_label(by_blind_id[key]))}</strong> : {_e(', '.join(definitions[item] for item in value))}</li>"
            for key, value in economy["benefits"].items() if key in by_blind_id
        )
        benefits = f"<div class=\"benefits\"><h3>Bénéfices prévus des options admissibles plus chères</h3><ul>{benefit_items}</ul></div>"
    else:
        benefits = "<p class=\"benefits\"><strong>Bénéfices prévus :</strong> aucun critère secondaire ne justifie ici le surcoût d’une autre option admissible.</p>"
    return f"<div class=\"economy-callout\"><p>{_e(conclusion)}</p></div>{ledger}{totals}{benefits}"


def _render_verify(results):
    requested, observed, applied = results["conditions"]["requested"], results["conditions"]["observed"], results["conditions"]["applied"]
    campaign = results["contract"]
    thinking = {"high": "élevé", "medium": "moyen", "low": "faible"}.get(requested["thinking"], requested["thinking"])
    disabled_labels = {"tools": "outils", "extensions": "extensions", "skills": "compétences", "prompt_templates": "modèles de prompt", "themes": "thèmes", "project_context": "contexte projet", "compaction": "compaction", "retry": "nouvelle tentative"}
    disabled = [label for key, label in disabled_labels.items() if requested.get(key) is False]
    protocol = [
        ("Harnais", f"Pi {requested['pi_version']}, identique pour toutes les configurations"),
        ("Sortie", {"text-only": "texte uniquement"}.get(requested["output"], requested["output"])),
        ("Limite de sortie", f"{requested['max_tokens']:,}".replace(",", " ") + " tokens"),
        ("Durée maximale", _human_duration(requested["timeout_seconds"])),
        ("Effort de raisonnement demandé", thinking),
        ("Session", {"ephemeral": "éphémère"}.get(requested["session"], requested["session"])),
        ("Tentatives comptées", f"{campaign['cost_basis']['attempts_per_configuration']} par configuration, sans nouvelle tentative"),
        ("Unité de coût", campaign["cost_basis"]["currency"]),
        ("Options désactivées", ", ".join(disabled)),
    ]
    environment = [
        ("Pi observé", observed["pi_version"]), ("Système", observed["system"]), ("Architecture", observed["architecture"]),
        ("Python", observed["python"]), ("Système complet", observed["system_version"]),
    ]
    per_config = "".join(f"<h4>{_e(_public_label(item))}</h4>{_render_fingerprints(item['fingerprints'])}" for item in results["configurations"])
    return (
        "<p class=\"meta\">Ce bloc rassemble le protocole commun, l’environnement, les dates et les empreintes SHA-256 qui permettent de recouper cette page avec les artefacts scellés du run. Il n’ajoute aucune étape à la restitution.</p>"
        f"<h3>Protocole commun</h3><dl>{_rows(protocol)}</dl>"
        f"<h3>Environnement observé</h3><dl>{_rows(environment)}</dl>"
        f"<p class=\"meta\"><strong>Commit du moteur :</strong> <code>{_e(observed['git_head'])}</code></p>"
        f"<h3>Empreintes de configuration appliquée</h3><dl><dt>Réglages</dt><dd><code>{_e(applied['settings_sha256'])}</code></dd><dt>Modèles</dt><dd><code>{_e(applied['models_sha256'])}</code></dd></dl>"
        f"<h3>Dates</h3>{_render_dates(results['dates'])}"
        f"<h3>Empreintes de la campagne</h3>{_render_fingerprints(results['fingerprints'])}"
        f"<h3>Empreintes par configuration</h3>{per_config}"
    )


def _render_html(results):
    panel_order = {item["id"]: index for index, item in enumerate(results["contract"]["panel"])}
    configurations = sorted(results["configurations"], key=lambda item: panel_order.get(item["requested"]["id"], len(panel_order)))
    view = {**results, "configurations": configurations}
    data = {
        "title": _e(_task_brief(results["contract"])["title"]),
        "campaign_date": _e(f"Campagne préparée le {_human_date(results['dates']['prepared'])}, restitution construite le {_human_date(results['dates']['built'])}."),
        "brief": _render_brief(results["contract"]),
        "count": _e(len(configurations)),
        "rows": "".join(_render_row(item, results["contract"]["secondary"]) for item in configurations),
        "economy": _render_economy(view),
        "verify": _render_verify(view),
        "limit": _e(results["attribution_limit"]),
    }
    template = (PACKAGE_DIR / "page.html").read_text()
    for key, value in data.items():
        template = template.replace("{{" + key + "}}", value)
    if "{{" in template:
        raise ValueError("gabarit incomplet")
    return template.encode()


def build(run_dir, decisions, authority, repo_root=None):
    run, run_id = _run_path(run_dir, repo_root)
    seal, panel = _validate_prepared(run, run_id)
    collection, receipts = _validate_collection(run, run_id, seal, panel)
    _require_complete(collection, panel)
    dossier, review_map = _validate_review(run, collection, receipts)
    decisions_raw, auth_raw = Path(decisions).read_bytes(), Path(authority).read_bytes()
    decisions_value = _strict_json_bytes(decisions_raw, str(decisions))
    if decisions_value.get("review_sha256") != _sha(run / "review.json"):
        raise ValueError("décisions non liées à la revue exacte")
    decisions_list = _validate_decisions(decisions_value, dossier)
    auth_value = _strict_json_bytes(auth_raw, str(authority))
    s9_id = _load_json(run / "authorization-s9.json")["authority_id"]
    _validate_s10(auth_value, run_id, _sha(run / "seal.json"), _sha(run / "review.json"), hashlib.sha256(decisions_raw).hexdigest(), s9_id)
    for name in ["decisions.json", "authorization-s10.json", "results.json", "index.html", "final-seal.json"]:
        if (run / name).exists():
            raise FileExistsError(f"artefact final non écrasable: {name}")
    results = _results(run, dossier, review_map, receipts, decisions_list)
    results["fingerprints"]["decisions"] = hashlib.sha256(decisions_raw).hexdigest()
    results["fingerprints"]["authority_s10"] = hashlib.sha256(auth_raw).hexdigest()
    html_bytes = _render_html(results)
    _write_json_bytes(run / "decisions.json", decisions_raw)
    _write_json_bytes(run / "authorization-s10.json", auth_raw)
    _write_json(run / "results.json", results)
    _write_exclusive(run / "index.html", html_bytes)
    final = {"schema": "benchmark-lab-x-final-seal-1", "run": run_id, "files": {name: _sha(run / name) for name in ["results.json", "index.html", "decisions.json", "authorization-s10.json"]}, "built_at": _now()}
    _write_json(run / "final-seal.json", final)
    _private_tree(run)
    return results


# Historical schema identifiers are accepted only when reading sealed results
def present(source_run_dir, run_dir, repo_root=None):
    source, source_id = _run_path(source_run_dir, repo_root)
    _validate_final(source, source_id)
    results_raw = (source / "results.json").read_bytes()
    results = _strict_json_bytes(results_raw, str(source / "results.json"))
    if not isinstance(results, dict) or results.get("schema") not in {"benchmark-lab-x-results-1", "benchmark-lab-x-v2-alpha-results-1"}:
        raise ValueError("résultats source invalides")
    page = _render_html(results)
    run, run_id = _run_path(run_dir, repo_root, create=True)
    source_record = {
        "schema": "benchmark-lab-x-presentation-source-1",
        "source_run": source_id,
        "source_final_seal_sha256": _sha(source / "final-seal.json"),
        "source_results_sha256": hashlib.sha256(results_raw).hexdigest(),
    }
    try:
        _write_json_bytes(run / "results.json", results_raw)
        _write_json(run / "source.json", source_record)
        _write_exclusive(run / "index.html", page)
        seal = {
            "schema": "benchmark-lab-x-presentation-seal-1",
            "run": run_id,
            "source_run": source_id,
            "source_sha256": _sha(run / "source.json"),
            "files": {name: _sha(run / name) for name in ["results.json", "index.html", "source.json"]},
            "built_at": _now(),
        }
        _write_json(run / "presentation-seal.json", seal)
        _private_tree(run)
        _validate_presentation(run, run_id, repo_root)
    except BaseException:
        shutil.rmtree(run)
        raise
    return {"run": run_id, "source_run": source_id, "page": str(run / "index.html")}


def _validate_final(run, run_id):
    seal = _load_json(run / "final-seal.json")
    if seal.get("schema") not in {"benchmark-lab-x-final-seal-1", "benchmark-lab-x-v2-alpha-final-seal-1"} or seal.get("run") != run_id:
        raise ValueError("sceau final invalide")
    for name, digest in seal.get("files", {}).items():
        if not (run / name).is_file() or _sha(run / name) != digest:
            raise ValueError(f"artefact final altéré: {name}")
    return run / "index.html"


def _validate_presentation(run, run_id, repo_root=None):
    seal = _load_json(run / "presentation-seal.json")
    if seal.get("schema") not in {"benchmark-lab-x-presentation-seal-1", "benchmark-lab-x-v2-alpha-presentation-seal-1"} or seal.get("run") != run_id:
        raise ValueError("sceau de présentation invalide")
    if set(seal.get("files", {})) != {"results.json", "index.html", "source.json"}:
        raise ValueError("fichiers de présentation incomplets")
    for name, digest in seal["files"].items():
        if not (run / name).is_file() or _sha(run / name) != digest:
            raise ValueError(f"artefact de présentation altéré: {name}")
    if seal.get("source_sha256") != _sha(run / "source.json"):
        raise ValueError("source de présentation altérée")
    source_record = _load_json(run / "source.json")
    if source_record.get("schema") not in {"benchmark-lab-x-presentation-source-1", "benchmark-lab-x-v2-alpha-presentation-source-1"} or source_record.get("source_run") != seal.get("source_run"):
        raise ValueError("source de présentation invalide")
    source, source_id = _run_path(source_record["source_run"], repo_root)
    _validate_final(source, source_id)
    if source_record.get("source_final_seal_sha256") != _sha(source / "final-seal.json"):
        raise ValueError("sceau source divergent")
    if source_record.get("source_results_sha256") != _sha(source / "results.json") or (run / "results.json").read_bytes() != (source / "results.json").read_bytes():
        raise ValueError("résultats source divergents")
    return run / "index.html"


def show(run_dir, repo_root=None, opener=None):
    run, run_id = _run_path(run_dir, repo_root)
    page = _validate_presentation(run, run_id, repo_root) if (run / "presentation-seal.json").is_file() else _validate_final(run, run_id)
    command = opener or "open"
    completed = subprocess.run([command, str(page)], check=False)
    if completed.returncode:
        raise RuntimeError("ouverture impossible")
    return page


def _collect_worker():
    run_dir, authority, repo_root, result_path, active_path = sys.argv[1:]
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
    if os.environ.get("BENCHMARK_LAB_X_TEST_WORKER_IGNORES_SIGTERM") == "1":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        collect(run_dir, authority, repo_root, result_path, False, active_path)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, allow_nan=False), file=sys.stderr)
        return 1


def _test_gate():
    fd = os.environ.get("BENCHMARK_LAB_X_TEST_SOCKET_FD")
    if fd is not None and not os.read(int(fd), 1):
        raise RuntimeError("canal de test fermé")


def _ensure_authority_copy(run, authority_raw):
    copied = run / "authorization-s9.json"
    if copied.exists():
        if copied.read_bytes() != authority_raw:
            raise ValueError("autorité S9 copiée divergente")
    else:
        _write_json_bytes(copied, authority_raw)
    return _sha(copied)


def _valid_interruption_receipt(run, config, authority_sha):
    try:
        receipt = _load_json(run / f"{config['id']}.receipt.json")
        if (
            not isinstance(receipt, dict)
            or receipt.get("schema") != "benchmark-lab-x-receipt-1"
            or receipt.get("config_id") != config["id"]
            or receipt.get("authority_sha256") != authority_sha
            or receipt.get("requested") != config
            or receipt.get("route_observed") != "INCONNU"
            or receipt.get("effort_observed") != "INCONNU"
        ):
            return False
        for kind in ["marker", "stdout", "stderr"]:
            source = run / receipt[kind]
            if source.parent != run or not source.is_file() or _sha(source) != receipt[f"{kind}_sha256"]:
                return False
        marker = _load_json(run / receipt["marker"])
        if not isinstance(marker, dict) or marker.get("schema") != "benchmark-lab-x-attempt-1" or marker.get("config_id") != config["id"] or marker.get("authority_sha256") != authority_sha:
            return False
        parsed = _attempt_result((run / receipt["stdout"]).read_bytes(), config)
        return receipt.get("raw_jsonl_sha256") == receipt.get("stdout_sha256") and all(receipt.get(key) == parsed.get(key) for key in ["cost", "output", "observed", "retry", "final_text_sha256"])
    except (KeyError, OSError, TypeError, ValueError):
        return False


def _interruption_collection(run, run_id, seal, panel, authority_raw, stop_reason="INTERRUPTION_SIGTERM"):
    authority_sha = _ensure_authority_copy(run, authority_raw)
    incident = {"INTERRUPTION_SIGTERM": "SIGTERM_GROUPE_TUE", "CANDIDATE_TIMEOUT": "TIMEOUT_GROUPE_TUE", "CANDIDATE_EXCEPTION": "INTERRUPTION_GROUPE_TUE"}[stop_reason]
    receipts = []
    for config in panel:
        path = run / f"{config['id']}.receipt.json"
        marker = run / f"{config['id']}.started.json"
        valid = path.is_file() and _valid_interruption_receipt(run, config, authority_sha)
        marker_valid = False
        if marker.is_file():
            try:
                marker_value = _load_json(marker)
                marker_valid = isinstance(marker_value, dict) and marker_value.get("schema") == "benchmark-lab-x-attempt-1" and marker_value.get("config_id") == config["id"] and marker_value.get("authority_sha256") == authority_sha
            except (OSError, ValueError):
                pass
        if not valid and marker_valid:
            path.unlink(missing_ok=True)
            stdout_path = run / f"{config['id']}.stdout.jsonl"
            stderr_path = run / f"{config['id']}.stderr.txt"
            if not stdout_path.is_file():
                _write_exclusive(stdout_path, b"")
            if not stderr_path.is_file():
                _write_exclusive(stderr_path, f"collect arrêté: {stop_reason}\n".encode())
            result = _attempt_result(stdout_path.read_bytes(), config)
            _receipt(run, config, marker, authority_sha, stdout_path, stderr_path, None, result, incident)
            valid = True
        if not valid:
            break
        receipts.append({"config_id": config["id"], "path": path.name, "sha256": _sha(path)})
    matrix = []
    for config in panel:
        item = next((item for item in receipts if item["config_id"] == config["id"]), None)
        matrix.append({
            "config_id": config["id"], "expected": config, "executed": item is not None,
            "receipt": item["path"] if item else None, "receipt_sha256": item["sha256"] if item else None,
            "reason": incident if item else f"NON_TENTEE_APRES_{stop_reason}",
        })
    values = [_load_json(run / item["path"])["cost"] for item in receipts]
    known = all(value != "INCONNU" for value in values)
    spent = sum((_number(value, "coût reçu") for value in values), Decimal(0)) if known else None
    return {
        "schema": "benchmark-lab-x-collection-1", "run": run_id,
        "seal_sha256": _sha(run / "seal.json"), "authority_sha256": authority_sha,
        "receipts": receipts, "matrix": matrix,
        "spent": float(spent) if known else "INCONNU", "budget_known": known,
        "stop_reason": stop_reason, "created_at": _now(),
    }


def _wait_pgid_gone(pgid, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(min(0.01, max(0, deadline - time.monotonic())))
    return False


def _terminate_pgid(pgid):
    if pgid is None:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if _wait_pgid_gone(pgid, 0.5):
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if not _wait_pgid_gone(pgid, 4):
        raise RuntimeError("groupe de collecte encore vivant")


def _clean_private_json_temps(run):
    for path in run.glob(".private-json-*.tmp"):
        path.unlink(missing_ok=True)


def _write_private_collection(path, collection, run, run_id, seal, panel):
    path.unlink(missing_ok=True)
    _write_json(path, collection)
    _validate_collection(run, run_id, seal, panel, path)


def _supervise_collect(run_dir, authority):
    if signal.getsignal(signal.SIGTERM) is signal.SIG_IGN:
        raise RuntimeError("SIGTERM hérité en SIG_IGN")
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    test_fd = os.environ.get("BENCHMARK_LAB_X_TEST_SOCKET_FD")
    repo_root = os.environ.get("BENCHMARK_LAB_X_TEST_REPO_ROOT") if test_fd is not None else None
    run, run_id = _run_path(run_dir, repo_root)
    if (run / "collection.json").exists():
        raise FileExistsError("collection immuable déjà présente")
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise ValueError("OPENROUTER_API_KEY absente ou vide")
    seal, panel = _validate_prepared(run, run_id)
    authority_raw = Path(authority).read_bytes()
    auth = _strict_json_bytes(authority_raw, str(authority))
    _validate_s9(auth, run_id, seal, _sha(run / "seal.json"), panel)
    lock = threading.Lock()
    io_lock = threading.Lock()
    state = {"terminal": "OPEN", "worker": None, "worker_pgid": None, "reason": None}
    ready = threading.Event()
    interrupted = threading.Event()
    quiesced = threading.Event()
    quiescence_error = []
    permit_parent, permit_child = socket.socketpair()

    def close_permits():
        try:
            permit_parent.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        permit_parent.close()

    def arbitrate(reason, from_sigterm=False):
        with lock:
            if state["terminal"].startswith("COMMITTED_"):
                _test_event("POST_TERMINAL_SIGTERM")
                return
            if state["terminal"] != "OPEN":
                return
            state["terminal"] = "INTERRUPTED"
            state["reason"] = reason
            pgid = state["worker_pgid"]
            interrupted.set()
            close_permits()
        _test_event("INTERRUPTED_SEEN" if from_sigterm else f"TERMINATION_REQUESTED {reason}")
        if not from_sigterm:
            _test_event("LAUNCHES_REVOKED")
        try:
            _terminate_pgid(pgid)
            _test_event("PGID_QUIESCENT")
            with io_lock:
                _clean_private_json_temps(run)
            _test_event("PGID_GONE")
            _test_event("INTERRUPTED_ARBITRATED")
        except Exception as exc:
            quiescence_error.append(exc)
            _test_event(f"ARBITRATION_FAILED {type(exc).__name__} {exc}")
        finally:
            quiesced.set()

    def receive_sigterm():
        ready.set()
        signal.sigwait({signal.SIGTERM})
        arbitrate("INTERRUPTION_SIGTERM", True)

    def grant_launches():
        try:
            while True:
                request = _socket_line(permit_parent)
                if request.startswith("TERMINATE "):
                    fields = request.split()
                    if len(fields) != 3 or fields[2] not in {"CANDIDATE_TIMEOUT", "CANDIDATE_EXCEPTION"}:
                        raise RuntimeError("demande de terminaison invalide")
                    arbitrate(fields[2])
                    return
                _, config_id = request.split(" ", 1)
                if request != f"LAUNCH_REQUEST {config_id}":
                    raise RuntimeError("demande de lancement invalide")
                if os.environ.get("BENCHMARK_LAB_X_TEST_C2_REQUEST_GATE") == "1" and config_id == "C2":
                    _test_event("C2_LAUNCH_REQUEST")
                    if not interrupted.wait(5):
                        raise RuntimeError("SIGTERM causal absent avant permis C2")
                with lock:
                    if state["terminal"] != "OPEN":
                        return
                    permit_parent.sendall(f"GRANT {config_id}\n".encode())
        except (OSError, RuntimeError, ValueError):
            return

    threading.Thread(target=receive_sigterm, daemon=True).start()
    ready.wait()
    _test_event("OWNED")
    _test_gate()
    with tempfile.TemporaryDirectory(prefix="benchmark-lab-x-collect-") as private_dir:
        result_path = Path(private_dir) / "collection.json"
        active_path = Path(private_dir) / "active.pid"
        command = [
            sys.executable, "-B", "-c",
            "from benchmark.__main__ import _collect_worker; raise SystemExit(_collect_worker())",
            str(run), str(authority), str(Path(repo_root or DEFAULT_REPO_ROOT).absolute()), str(result_path), str(active_path),
        ]
        worker_env = os.environ.copy()
        worker_env["BENCHMARK_LAB_X_LAUNCH_SOCKET_FD"] = str(permit_child.fileno())
        pass_fds = tuple(fd for fd in (int(test_fd) if test_fd is not None else None, permit_child.fileno()) if fd is not None)
        with lock:
            worker = None
            if state["terminal"] == "OPEN":
                worker = subprocess.Popen(
                    command, cwd=PACKAGE_DIR.parent, env=worker_env, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, start_new_session=True, pass_fds=pass_fds,
                )
                state["worker"] = worker
                state["worker_pgid"] = worker.pid
                _test_event(f"WORKER_PGID {worker.pid}")
        permit_child.close()
        threading.Thread(target=grant_launches, daemon=True).start()
        stdout = stderr = ""
        if worker is not None:
            stdout, stderr = worker.communicate()
            with lock:
                state["worker"] = None
            _test_event("WORKER_REAPED")
        if interrupted.is_set():
            if not quiesced.wait(5):
                raise RuntimeError("quiescence du worker non confirmée")
            if quiescence_error:
                raise quiescence_error[0]
        if interrupted.is_set():
            with io_lock:
                collection = _interruption_collection(run, run_id, seal, panel, authority_raw, state["reason"])
        elif not result_path.is_file():
            message = stderr.strip()
            try:
                message = json.loads(message)["error"]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
            raise RuntimeError(message or "worker de collecte sans résultat")
        else:
            collection, _ = _validate_collection(run, run_id, seal, panel, result_path)
        publication = run / f".collection-{secrets.token_hex(16)}.tmp"
        try:
            with io_lock:
                _write_private_collection(publication, collection, run, run_id, seal, panel)
            _test_event("BEFORE_COMPLETE_LINK")
            _test_gate()
            while True:
                if interrupted.is_set() and collection.get("stop_reason") != state["reason"]:
                    if not quiesced.wait(5):
                        raise RuntimeError("quiescence du worker non confirmée")
                    if quiescence_error:
                        raise quiescence_error[0]
                    with io_lock:
                        collection = _interruption_collection(run, run_id, seal, panel, authority_raw, state["reason"])
                        _write_private_collection(publication, collection, run, run_id, seal, panel)
                with lock:
                    if state["terminal"] == "INTERRUPTED" and collection.get("stop_reason") != state["reason"]:
                        continue
                    os.link(publication, run / "collection.json")
                    directory = os.open(run, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                    if state["terminal"] == "INTERRUPTED":
                        state["terminal"] = "COMMITTED_INTERRUPTION"
                    else:
                        state["terminal"] = "COMMITTED_COMPLETE"
                        _test_event("COMPLETE_LINKED")
                    break
        finally:
            publication.unlink(missing_ok=True)
        if state["terminal"] == "COMMITTED_COMPLETE":
            _test_gate()
        _private_tree(run)
        _validate_collection(run, run_id, seal, panel)
        if state["terminal"] == "COMMITTED_INTERRUPTION":
            if state["reason"] == "INTERRUPTION_SIGTERM":
                raise RuntimeError("collect interrompu par SIGTERM")
            raise RuntimeError(f"collect arrêté: {state['reason']}")
        if worker is not None and worker.returncode:
            raise RuntimeError(stderr.strip() or "worker de collecte en échec")
        return collection


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--pi")
    p = commands.add_parser("collect")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--authority", required=True)
    p = commands.add_parser("review")
    p.add_argument("--run-dir", required=True)
    p = commands.add_parser("build")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--decisions", required=True)
    p.add_argument("--authority", required=True)
    p = commands.add_parser("present")
    p.add_argument("--source-run", required=True)
    p.add_argument("--run-dir", required=True)
    p = commands.add_parser("show")
    p.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.run_dir, args.pi)
        elif args.command == "collect":
            result = _supervise_collect(args.run_dir, args.authority)
        elif args.command == "review":
            result = review(args.run_dir)
        elif args.command == "build":
            result = build(args.run_dir, args.decisions, args.authority)
        elif args.command == "present":
            result = present(args.source_run, args.run_dir)
        else:
            result = {"page": str(show(args.run_dir))}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, allow_nan=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
