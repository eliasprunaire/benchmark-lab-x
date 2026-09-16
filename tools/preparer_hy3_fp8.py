# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Préparer le lot HY3 FP8 autorisé sans effectuer d'appel modèle"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any

RACINE = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(Path(__file__).parent))

from empreintes import empreinte  # noqa: E402
from protocole_v2 import (  # noqa: E402
    POLITIQUE_ROUTE_PROGRAM,
    PROTOCOLE_VERSION,
    SCHEMA_ENDPOINTS_SNAPSHOT,
    SCHEMA_EXECUTION_ROUTE_PROGRAM,
    SCHEMA_LOCK_ROUTE_PROGRAM,
    assembler_prompt_verrouille,
    construire_payload,
    ecrire_json_immuable,
    empreinte_lock,
    sha256_fichier,
    sha256_octets,
    valider_lock,
)

CAMPAIGN_ID = "2026-08-10-pentagone-hy3-fp8"
MODEL = "tencent/hy3"
SOURCE_URL = "https://openrouter.ai/api/v1/models/tencent/hy3/endpoints"
MAX_TOKENS = 131_072
PROMPT_TOKEN_UPPER_BOUND = 3_424
CAP_MICRODOLLARS = 633_258
REFERENCE_LOCK = Path("runs/2026-08-09-pentagone-v6/campaign.lock.v4.json")
HISTORICAL = (
    (
        1,
        Path("runs/2026-08-10-pentagone-v0-continuation-01/collections/"
             "hy3__r1/attempt-1/collection-receipt.json"),
    ),
    (
        2,
        Path("runs/2026-08-10-pentagone-v0-continuation-03/collections/"
             "hy3__r2/attempt-1/collection-receipt.json"),
    ),
)
ROUTES = (
    ("primary", "atlas-cloud", "AtlasCloud", "atlas-cloud/fp8"),
    ("secondary", "deepinfra", "DeepInfra", "deepinfra/fp8"),
)


class PreparationInvalide(RuntimeError):
    pass


def _exiger(condition: bool, message: str) -> None:
    if not condition:
        raise PreparationInvalide(message)


def _charger_json(path: Path) -> dict[str, Any]:
    try:
        valeur = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationInvalide(f"JSON illisible: {path}") from exc
    _exiger(isinstance(valeur, dict), f"objet JSON requis: {path}")
    return valeur


def _head() -> str:
    proc = subprocess.run(
        ["git", "-C", str(RACINE), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    _exiger(proc.returncode == 0, "HEAD Git inaccessible")
    return proc.stdout.strip()


def _prix_par_million(endpoint: dict[str, Any], champ: str) -> str:
    try:
        valeur = Decimal(str(endpoint["pricing"][champ])) * Decimal(1_000_000)
    except (KeyError, TypeError, ArithmeticError) as exc:
        raise PreparationInvalide(f"prix {champ} absent") from exc
    _exiger(valeur >= 0, f"prix {champ} négatif")
    return format(valeur.normalize(), "f")


def _endpoints(raw: bytes) -> dict[str, dict[str, Any]]:
    try:
        objets = json.loads(raw.decode("utf-8"))["data"]["endpoints"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PreparationInvalide("réponse endpoints HY3 illisible") from exc
    resultat: dict[str, dict[str, Any]] = {}
    for _, slug, nom, tag in ROUTES:
        trouves = [e for e in objets if isinstance(e, dict) and e.get("tag") == tag]
        _exiger(len(trouves) == 1, f"endpoint {tag} absent ou dupliqué")
        endpoint = trouves[0]
        _exiger(
            endpoint.get("model_id") == MODEL
            and endpoint.get("provider_name") == nom
            and str(endpoint.get("quantization") or "").lower() == "fp8"
            and isinstance(endpoint.get("max_completion_tokens"), int)
            and endpoint["max_completion_tokens"] >= MAX_TOKENS
            and {"max_tokens", "temperature", "top_p", "seed"}
            <= set(endpoint.get("supported_parameters") or []),
            f"endpoint {tag} incompatible",
        )
        resultat[slug] = endpoint
    return resultat


def _route_primaire(
    endpoint: dict[str, Any], observed_at: str, response_sha256: str
) -> dict[str, Any]:
    quantization = {"status": "declared", "value": "fp8"}
    revision = {"status": "declared", "kind": "endpoint_model_id", "value": MODEL}
    return {
        "metadata_status": "resolved",
        "backend": "openrouter",
        "provider": "atlas-cloud",
        "expect_provider": "AtlasCloud",
        "quantization": quantization,
        "revision": revision,
        "criterion_version": POLITIQUE_ROUTE_PROGRAM,
        "price_source": SOURCE_URL,
        "price_observed_at": observed_at,
        "input_usd_per_million_tokens": _prix_par_million(endpoint, "prompt"),
        "output_usd_per_million_tokens": _prix_par_million(endpoint, "completion"),
        "request_usd": "0",
        "prompt_token_upper_bound": PROMPT_TOKEN_UPPER_BOUND,
        "endpoint_tag": "atlas-cloud/fp8",
        "ownership": {
            "kind": "third_party",
            "canonical_publisher": "tencent",
            "provider_slug": "atlas-cloud",
            "provider_name": "AtlasCloud",
            "endpoint_tag": "atlas-cloud/fp8",
        },
        "metadata_evidence": {
            "url": SOURCE_URL,
            "observed_at": observed_at,
            "response_sha256": response_sha256,
        },
    }


def _max_cost(route: dict[str, Any]) -> int:
    total = (
        Decimal(route["input_usd_per_million_tokens"])
        * PROMPT_TOKEN_UPPER_BOUND
        + Decimal(route["output_usd_per_million_tokens"]) * MAX_TOKENS
    )
    return int(total.to_integral_value(rounding=ROUND_CEILING))


def _manifest(
    endpoints: dict[str, dict[str, Any]], observed_at: str, response_sha256: str
) -> dict[str, Any]:
    quantization = {"status": "declared", "value": "fp8"}
    revision = {"status": "declared", "kind": "endpoint_model_id", "value": MODEL}
    evidence = {
        "url": SOURCE_URL,
        "observed_at": observed_at,
        "response_sha256": response_sha256,
    }
    provider_routes = []
    for role, slug, nom, tag in ROUTES:
        provider_routes.append({
            "role": role,
            "provider_pinned": slug,
            "provider_expected": nom,
            "endpoint_tag": tag,
            "quantization": quantization,
            "revision": revision,
            "max_tokens": endpoints[slug]["max_completion_tokens"],
            "metadata_evidence": evidence,
        })
    return {
        "schema_version": SCHEMA_EXECUTION_ROUTE_PROGRAM,
        "mode": "direct",
        "model_requested": MODEL,
        "backend": "openrouter",
        "provider_pinned": "atlas-cloud",
        "provider_expected": "AtlasCloud",
        "endpoint_tag": "atlas-cloud/fp8",
        "quantization": quantization,
        "revision": revision,
        "reasoning_effort": None,
        "request_parameters": {
            "temperature": 0,
            "top_p": 1,
            "seed": 42,
            "provider": {
                "order": ["atlas-cloud", "deepinfra"],
                "only": ["atlas-cloud", "deepinfra"],
                "allow_fallbacks": True,
                "require_parameters": True,
                "data_collection": "deny",
            },
            "usage": {"include": True},
        },
        "max_tokens": MAX_TOKENS,
        "data_policy_requested": "allow",
        "request_adapter_version": "openrouter-chat-completions/v2",
        "tools": [],
        "agent": None,
        "local_environment": None,
        "provider_routes": provider_routes,
        "router_metadata": {
            "header": "X-OpenRouter-Metadata",
            "value": "enabled",
        },
    }


def _historique() -> dict[str, Any]:
    acquisitions = []
    for run, receipt_relative in HISTORICAL:
        receipt_path = RACINE / receipt_relative
        meta_path = receipt_path.with_name("meta.json")
        receipt = _charger_json(receipt_path)
        meta = _charger_json(meta_path)
        execution = meta.get("execution_manifest") or {}
        _exiger(
            receipt.get("collection_id") == f"hy3__r{run}"
            and receipt.get("result") == "COLLECTED"
            and execution.get("model_requested") == MODEL
            and execution.get("provider_pinned") == "gmicloud"
            and execution.get("quantization") == {"status": "declared", "value": "bf16"},
            f"acquisition BF16 historique incohérente pour r{run}",
        )
        acquisitions.append({
            "historical_run": run,
            "configuration": "hy3-bf16-gmicloud",
            "collection_receipt": {
                "path": receipt_relative.as_posix(),
                "sha256": sha256_fichier(receipt_path),
            },
            "meta": {
                "path": meta_path.relative_to(RACINE).as_posix(),
                "sha256": sha256_fichier(meta_path),
            },
            "disposition": "historical_excluded_from_hy3_fp8_aggregate",
        })
    return {
        "schema_version": "benchmark-lab-x/configuration-replacement/v1",
        "model_identity": MODEL,
        "historical_configuration": "hy3-bf16-gmicloud",
        "replacement_configuration": "hy3-fp8-atlas-deepinfra",
        "historical_acquisitions": acquisitions,
        "replacement_slots": [f"hy3__r{run}" for run in range(1, 7)],
    }


def preparer(args: argparse.Namespace) -> dict[str, Any]:
    source_commit = args.source_commit.strip()
    _exiger(source_commit == _head(), "source_commit différent de HEAD")
    out_dir = args.out_dir.resolve()
    runs_root = (RACINE / "runs").resolve()
    _exiger(out_dir.parent == runs_root, "le dossier doit être un enfant direct de runs")
    _exiger(not out_dir.exists(), "le dossier de campagne existe déjà")
    out_dir.mkdir(parents=False)

    reference_path = (RACINE / args.reference_lock).resolve()
    reference = _charger_json(reference_path)
    _exiger(
        reference.get("campaign_id") == "2026-08-09-pentagone-v6"
        and reference.get("task", {}).get("task_version") == "task-v4",
        "lock de référence inattendu",
    )
    raw = args.raw_endpoints.read_bytes()
    response_sha256 = sha256_octets(raw)
    endpoints = _endpoints(raw)
    route = _route_primaire(endpoints["atlas-cloud"], args.observed_at, response_sha256)
    max_cost = _max_cost(route)
    _exiger(max_cost * 6 == CAP_MICRODOLLARS, "plafond HY3 différent de 0,633258 $")

    snapshot = {
        "schema_version": SCHEMA_ENDPOINTS_SNAPSHOT,
        "criterion_version": POLITIQUE_ROUTE_PROGRAM,
        "observed_at": args.observed_at,
        "model": MODEL,
        "source_url": SOURCE_URL,
        "response_sha256": response_sha256,
        "response_body": raw.decode("utf-8"),
        "approval": {
            "decision": "GO_HY3_FP8_ROUTE_PROGRAM",
            "approved_by": "Ayo",
            "approved_at": args.approved_at,
            "route_order": ["atlas-cloud", "deepinfra"],
            "quantization": "fp8",
            "max_tokens": MAX_TOKENS,
            "additional_cap_microdollars": CAP_MICRODOLLARS,
            "global_cap_microdollars": 100_000_000,
        },
        "panel": ["hy3"],
        "models_file": "models.toml",
        "models_file_sha256": sha256_fichier(RACINE / "models.toml"),
        "resolved": {"hy3": {**route, "max_tokens": MAX_TOKENS}},
    }
    snapshot_path = out_dir / "routes.hy3-fp8.approved.json"
    ecrire_json_immuable(snapshot_path, snapshot)

    manifest = _manifest(endpoints, args.observed_at, response_sha256)
    manifest_hash = empreinte(manifest)
    prompt, _ = assembler_prompt_verrouille(RACINE, reference["task"])
    payload_hash = sha256_octets(construire_payload(manifest, prompt))
    base_identity = {
        "mode": "direct",
        "model_requested": MODEL,
        "backend": "openrouter",
        "provider_pinned": "atlas-cloud",
        "reasoning_effort": None,
        "endpoint_tag": "atlas-cloud/fp8",
    }
    collections = [{
        "collection_id": f"hy3__r{run}",
        "alias": "hy3",
        "run": run,
        "task_version": reference["task"]["task_version"],
        "prompt_sha256": reference["task"]["prompt_sha256"],
        "base_identity": base_identity,
        "route": route,
        "execution_manifest": manifest,
        "execution_manifest_hash": manifest_hash,
        "payload_hash": payload_hash,
        "max_cost_microdollars": max_cost,
    } for run in range(1, 7)]
    lock = {
        "schema_version": SCHEMA_LOCK_ROUTE_PROGRAM,
        "protocol_version": PROTOCOLE_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "operation": "new_collection",
        "question": "Mesurer HY3 FP8 sous task-v4 avec fallback AtlasCloud vers DeepInfra",
        "created_at": args.created_at,
        "paid_authorization_required": True,
        "repository_source": {"commit": source_commit},
        "instrument_source": {
            "commit": reference["repository_source"]["commit"],
            "reference_lock_path": reference_path.relative_to(RACINE).as_posix(),
            "reference_lock_sha256": sha256_fichier(reference_path),
            "reference_campaign_lock_hash": empreinte_lock(reference),
        },
        "environments": copy.deepcopy(reference["environments"]),
        "panel": ["hy3"],
        "runs": 6,
        "attempts_max": 3,
        "runner": {"concurrency": 1, "transport_timeout_s": 600},
        "quotas": {
            "attempts_total_max": 18,
            "in_flight_by_backend": {"openrouter": 1},
            "in_flight_by_provider": {"atlas-cloud": 1, "deepinfra": 1},
        },
        "selection_policy": {"version": POLITIQUE_ROUTE_PROGRAM},
        "task": copy.deepcopy(reference["task"]),
        "axes": copy.deepcopy(reference["axes"]),
        "collections": collections,
        "budget": {
            "currency": "USD",
            "cap_microdollars": CAP_MICRODOLLARS,
            "estimate_microdollars": CAP_MICRODOLLARS,
            "estimate_source": "AtlasCloud FP8, borne de six sorties à 131072 jetons",
        },
        "registry_source": {
            "path": "models.toml",
            "sha256": sha256_fichier(RACINE / "models.toml"),
        },
        "route_snapshot_source": {
            "path": snapshot_path.relative_to(RACINE).as_posix(),
            "sha256": sha256_fichier(snapshot_path),
            "schema_version": SCHEMA_ENDPOINTS_SNAPSHOT,
            "criterion_version": POLITIQUE_ROUTE_PROGRAM,
            "observed_at": args.observed_at,
        },
    }
    valider_lock(lock, RACINE)
    lock_path = out_dir / "campaign.lock.v7.json"
    ecrire_json_immuable(lock_path, lock)
    lock_hash = empreinte_lock(lock)
    ecrire_json_immuable(out_dir / "paid-authorization.json", {
        "schema_version": "benchmark-lab-x/paid-authorization/v1",
        "decision": "GO_PAID_COLLECTION",
        "campaign_lock_hash": lock_hash,
        "cap_microdollars": CAP_MICRODOLLARS,
        "approved_by": "Ayo",
        "approved_at": args.approved_at,
    })
    ecrire_json_immuable(out_dir / "historical-exclusions.json", _historique())
    (out_dir / "campaign.toml").write_text(
        "\n".join((
            f'protocol_version = "{PROTOCOLE_VERSION}"',
            'operation = "new_collection"',
            f'campaign_id = "{CAMPAIGN_ID}"',
            f'created_at = "{args.created_at}"',
            f'source_commit = "{source_commit}"',
            "runs = 6",
            "attempts_max = 3",
            "concurrence = 1",
            "timeout = 600",
            f"cap_microdollars = {CAP_MICRODOLLARS}",
            f"estimate_microdollars = {CAP_MICRODOLLARS}",
            'b0_10_status = "APPROVED"',
            'campaign_lock = "campaign.lock.v7.json"',
            'paid_authorization = "paid-authorization.json"',
            'budget_ledger = "budget-ledger.json"',
            'candidates = ["hy3"]',
            "",
        )),
        encoding="utf-8",
    )
    return {
        "campaign_id": CAMPAIGN_ID,
        "campaign_lock": str(lock_path),
        "campaign_lock_hash": lock_hash,
        "collections": 6,
        "max_cost_per_collection_microdollars": max_cost,
        "cap_microdollars": CAP_MICRODOLLARS,
        "historical_bf16_excluded": 2,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--raw-endpoints", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--approved-at", required=True)
    parser.add_argument("--reference-lock", type=Path, default=REFERENCE_LOCK)
    args = parser.parse_args()
    try:
        resultat = preparer(args)
    except (PreparationInvalide, OSError, ValueError) as exc:
        print(f"HOLD: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(resultat, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
