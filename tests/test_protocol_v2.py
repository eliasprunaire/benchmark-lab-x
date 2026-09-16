"""Preuves locales sans réseau ni Chromium du protocole v2"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import shutil
import sys
import tempfile
import tomllib
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

RACINE = Path(__file__).parent.parent
sys.path.insert(0, str(RACINE / "tools"))

from empreintes import empreinte  # noqa: E402
from protocole_v2 import (  # noqa: E402
    AXES_PENTAGONE,
    CARDS_V4,
    PANEL_B0,
    PREDICATS_V4,
    PREDICATS_V5,
    PROTOCOLE_VERSION,
    SCHEMA_ABANDONMENT,
    SCHEMA_ATTEMPT,
    SCHEMA_COLLECTION,
    SCHEMA_CONTEXT,
    SCHEMA_COVERAGE,
    SCHEMA_ENVIRONMENT,
    SCHEMA_EXECUTION,
    SCHEMA_EXECUTION_ROUTE_PROGRAM,
    SCHEMA_EXECUTION_V3,
    SCHEMA_LOCK,
    SCHEMA_LOCK_CONTINUATION,
    SCHEMA_LOCK_CONTINUATION_V5,
    SCHEMA_LOCK_HISTORIQUE,
    SCHEMA_LOCK_V3,
    SCHEMA_PANEL_EVENTS,
    SCHEMA_SCORE,
    ContratV2Invalide,
    PlafondDepasse,
    RegistreBudget,
    agreger_scores,
    assembler_prompt_verrouille,
    charger_json,
    chemin_relatif_sur,
    construire_payload,
    decision_reprise,
    ecrire_json_immuable,
    empreinte_lock,
    expurger_diagnostic_processus,
    resultat_acquis_v2,
    valider_autorisation_payante,
    valider_chaine_collecte,
    valider_diagnostic_processus_r016,
    valider_etat_collecte,
    valider_evenements_panel,
    valider_environnement_observe,
    valider_lock,
    valider_manifeste_execution,
    valider_recu_audit,
    valider_recu_collecte,
    valider_recu_couverture,
    valider_recu_score,
    valider_recu_tentative,
    valider_resultat_carte,
)
import audit_instrument  # noqa: E402
import collect as collecteur  # noqa: E402
import figer_routes_precollecte  # noqa: E402
import lancer_campagne  # noqa: E402
import page_resultats  # noqa: E402
import preparer_campagne  # noqa: E402
import rapport_campagne  # noqa: E402
from integrer_temoins_r016 import (  # noqa: E402
    IntegrationR016Invalide,
    verifier_destination,
)
from preparer_campagne import (  # noqa: E402
    _arbre_tache,
    _charger_snapshot_routes,
    construire_lock,
    cout_max_microdollars,
)
from qualifier_temoins import charger_qualification_set, noter_v5  # noqa: E402


class ReponseHTTPFixture:
    def __init__(
        self,
        status_code: int,
        payload: dict | None = None,
        *,
        text: str | None = None,
        headers: dict | None = None,
    ):
        self.status_code = status_code
        self._payload = payload
        self.text = (
            text
            if text is not None
            else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        self.headers = headers or {}
        self.ok = 200 <= status_code < 300
        self.is_redirect = 300 <= status_code < 400
        self.is_permanent_redirect = status_code in {301, 308}

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("fixture sans JSON")
        return self._payload


def environnement_fixture(browser: dict | None) -> dict:
    runtimes = [{"name": "python", "version": "3.14.6"}]
    if browser is not None:
        runtimes.append({"name": "playwright", "version": "1.62.0"})
    return {
        "schema_version": SCHEMA_ENVIRONMENT,
        "os": {"name": "FixtureOS", "version": "1", "kernel": "fixture-kernel"},
        "architecture": "fixture-arch",
        "locale": "fr_FR.UTF-8",
        "timezone": "Europe/Paris",
        "runtimes": sorted(runtimes, key=lambda x: x["name"]),
        "browser": browser,
        "sandbox_image_digest": None,
    }


def audit_plan_fixture(axis_id: str) -> dict:
    return {
        "classes_and_boundaries": [f"frontière {axis_id}"],
        "anomalies_and_causes": [f"anomalie {axis_id}"],
        "blind_selection_method": "fixture-blind-selection/v1",
        "sample_size": 1,
        "sample_size_justification": "unité minimale de la fixture",
        "allowed_conclusion": "conclusion limitée à la fixture",
    }


def _lock_fixture_historique() -> dict:
    kinds = {cid: "binary" if cid in CARDS_V4[:2] else "levels" for cid in CARDS_V4}
    cards = []
    for cid in CARDS_V4:
        manifeste_verify = {
            "schema_version": "benchmark-lab-x/verifier-manifest/v2",
            "card_id": cid,
            "verify_version": "verify-v5",
            "predicates": list(PREDICATS_V4[cid]),
            "assets": [{
                "path": "tools/verifier_pentagone_v5.py",
                "sha256": "f" * 64,
                "bytes": 1,
            }],
        }
        cards.append({
            "id": cid,
            "kind": kinds[cid],
            "verify_version": "verify-v5",
            "verify_hash": empreinte(manifeste_verify),
            "verifier_path": "tools/verifier_pentagone_v5.py",
            "verify_manifest": manifeste_verify,
            "watchdog_s": 180,
            "predicates": list(PREDICATS_V4[cid]),
            "aggregation": {"runs": 6, "order_statistic": 4},
        })
    collections = []
    for alias in PANEL_B0:
        route_execution = {
            "backend": "openrouter",
            "provider": "example",
            "expect_provider": "Example",
            "quantization": "fixture",
            "revision": "fixture",
            "criterion_version": "fixture/v1",
            "price_source": "fixture locale",
            "price_observed_at": "2026-08-08T00:00:00+02:00",
            "input_usd_per_million_tokens": "0.1",
            "output_usd_per_million_tokens": "0.2",
            "request_usd": "0",
            "prompt_token_upper_bound": 100,
        }
        parameters = {"temperature": 0, "top_p": 1, "seed": 42}
        manifeste = {
            "schema_version": "benchmark-lab-x/execution-manifest/v2",
            "protocol_version": PROTOCOLE_VERSION,
            "task_version": "task-v3",
            "prompt_sha256": "1" * 64,
            "model": f"example/{alias}",
            "route": route_execution,
            "parameters": parameters,
            "max_tokens": 65_536,
            "data_policy": "deny",
            "runner_version": "collect.py/v3",
        }
        for run in range(1, 7):
            collections.append({
                "collection_id": f"{alias}__r{run}",
                "alias": alias,
                "run": run,
                "task_version": "task-v3",
                "prompt_sha256": "1" * 64,
                "model": f"example/{alias}",
                "route": {**route_execution, "metadata_status": "resolved"},
                "parameters": parameters,
                "max_tokens": 65_536,
                "max_cost_microdollars": 13_118,
                "execution_manifest": manifeste,
                "execution_manifest_hash": empreinte(manifeste),
            })
    environnement_runner = environnement_fixture(None)
    environnement_mesure = environnement_fixture(
        {"name": "chromium", "version": "151.0.7922.34"}
    )
    return {
        "schema_version": SCHEMA_LOCK_HISTORIQUE,
        "protocol_version": PROTOCOLE_VERSION,
        "campaign_id": "fixture-v2",
        "paid_authorization_required": True,
        "repository_source": {"commit": "d" * 40},
        "environments": {
            "runner": {
                "descriptor": environnement_runner,
                "sha256": empreinte(environnement_runner),
            },
            "measurement": {
                "descriptor": environnement_mesure,
                "sha256": empreinte(environnement_mesure),
            },
        },
        "panel": list(PANEL_B0),
        "runs": 6,
        "attempts_max": 3,
        "runner": {"concurrency": 2, "transport_timeout_s": 600},
        "budget": {"currency": "USD", "cap_microdollars": 55_000_000,
                   "estimate_microdollars": 31_812_500},
        "registry_source": {"path": "models.toml", "sha256": "a" * 64},
        "route_snapshot_source": {
            "path": "runs/fixture/routes-preflight.json",
            "sha256": "b" * 64,
            "schema_version": "benchmark-lab-x/route-preflight-snapshot/v1",
            "observed_at": "2026-08-08T00:00:00+02:00",
            "criterion_version": "benchmark-lab-x/selection-route/v2",
            "budget_status": "B0_09_UNCHANGED",
            "repriced_estimate_microdollars": 31_812_500,
            "b0_09_approval_hash": "c" * 64,
        },
        "task": {"task_version": "task-v3", "task_dir": "tasks/dev/pentagone-rotatif",
                 "task_file": "task-v3.md", "task_tree": [{"path": "task-v3.md"}],
                 "prompt_sha256": "1" * 64},
        "score_cards": cards,
        "collections": collections,
    }


def lock_minimal() -> dict:
    kinds = {
        axis_id: "binary" if axis_id in AXES_PENTAGONE[:2] else "levels"
        for axis_id in AXES_PENTAGONE
    }
    axes = []
    for axis_id in AXES_PENTAGONE:
        manifeste_verify = {
            "schema_version": "benchmark-lab-x/verifier-manifest/v2",
            "card_id": axis_id,
            "verify_version": "verify-v5",
            "predicates": list(PREDICATS_V4[axis_id]),
            "assets": [{
                "path": "tools/verifier_pentagone_v5.py",
                "sha256": "f" * 64,
                "bytes": 1,
            }],
        }
        axes.append({
            "id": axis_id,
            "kind": kinds[axis_id],
            "verify_version": "verify-v5",
            "verify_hash": empreinte(manifeste_verify),
            "verifier_path": "tools/verifier_pentagone_v5.py",
            "verify_manifest": manifeste_verify,
            "watchdog_s": 180,
            "predicates": list(PREDICATS_V4[axis_id]),
            "aggregation": {"runs": 6, "order_statistic": 4},
            "audit_plan": audit_plan_fixture(axis_id),
        })
    collections = []
    for alias in PANEL_B0:
        route = {
            "metadata_status": "resolved",
            "backend": "openrouter",
            "provider": "example",
            "expect_provider": "Example",
            "quantization": "fixture",
            "revision": "fixture",
            "criterion_version": "benchmark-lab-x/selection-route/v2",
            "price_source": "fixture locale",
            "price_observed_at": "2026-08-08T00:00:00+02:00",
            "input_usd_per_million_tokens": "0.1",
            "output_usd_per_million_tokens": "0.2",
            "request_usd": "0",
            "prompt_token_upper_bound": 100,
        }
        request_parameters = {
            "temperature": 0,
            "top_p": 1,
            "seed": 42,
            "provider": {
                "only": ["example"],
                "allow_fallbacks": False,
                "require_parameters": True,
                "data_collection": "deny",
            },
            "usage": {"include": True},
        }
        manifeste = {
            "schema_version": SCHEMA_EXECUTION_V3,
            "mode": "direct",
            "model_requested": f"example/{alias}",
            "backend": "openrouter",
            "provider_pinned": "example",
            "provider_expected": "Example",
            "quantization": "fixture",
            "revision": "fixture",
            "reasoning_effort": None,
            "request_parameters": request_parameters,
            "max_tokens": 4_096,
            "data_policy_requested": "deny",
            "request_adapter_version": "openrouter-chat-completions/v1",
            "tools": [],
            "agent": None,
            "local_environment": None,
        }
        base_identity = {
            "mode": "direct",
            "model_requested": f"example/{alias}",
            "backend": "openrouter",
            "provider_pinned": "example",
            "reasoning_effort": None,
        }
        for run in range(1, 7):
            collections.append({
                "collection_id": f"{alias}__r{run}",
                "alias": alias,
                "run": run,
                "task_version": "task-v3",
                "prompt_sha256": "1" * 64,
                "base_identity": base_identity,
                "route": route,
                "execution_manifest": manifeste,
                "execution_manifest_hash": empreinte(manifeste),
                "payload_hash": "2" * 64,
                "max_cost_microdollars": 830,
            })
    environnement_runner = environnement_fixture(None)
    environnement_mesure = environnement_fixture(
        {"name": "chromium", "version": "151.0.7922.34"}
    )
    return {
        "schema_version": SCHEMA_LOCK_V3,
        "protocol_version": PROTOCOLE_VERSION,
        "campaign_id": "fixture-v3",
        "operation": "new_collection",
        "question": "fixture locale",
        "created_at": "2026-08-08T00:00:00+02:00",
        "paid_authorization_required": True,
        "repository_source": {"commit": "d" * 40},
        "environments": {
            "runner": {
                "descriptor": environnement_runner,
                "sha256": empreinte(environnement_runner),
            },
            "measurement": {
                "descriptor": environnement_mesure,
                "sha256": empreinte(environnement_mesure),
            },
        },
        "panel": list(PANEL_B0),
        "runs": 6,
        "attempts_max": 3,
        "runner": {"concurrency": 2, "transport_timeout_s": 600},
        "quotas": {
            "attempts_total_max": len(PANEL_B0) * 6 * 3,
            "in_flight_by_backend": {"openrouter": 2},
            "in_flight_by_provider": {"example": 2},
        },
        "selection_policy": {"version": "benchmark-lab-x/selection-route/v2"},
        "budget": {
            "currency": "USD",
            "cap_microdollars": 1_000_000,
            "estimate_microdollars": 100_000,
            "estimate_source": "fixture locale",
        },
        "registry_source": {"path": "models.toml", "sha256": "a" * 64},
        "route_snapshot_source": {
            "path": "runs/fixture/routes-preflight.json",
            "sha256": "b" * 64,
            "schema_version": "benchmark-lab-x/route-preflight-snapshot/v2",
            "observed_at": "2026-08-08T00:00:00+02:00",
            "criterion_version": "benchmark-lab-x/selection-route/v2",
        },
        "task": {
            "task_id": "pentagone-rotatif",
            "task_version": "task-v3",
            "task_dir": "tasks/dev/pentagone-rotatif",
            "task_file": "task-v3.md",
            "task_tree": [{"path": "task-v3.md"}],
            "prompt_sha256": "1" * 64,
            "confidentiality_regime": "expose",
        },
        "axes": axes,
        "collections": collections,
    }


def lock_minimal_v4() -> dict:
    lock = lock_minimal()
    lock["schema_version"] = SCHEMA_LOCK
    lock["selection_policy"] = {"version": "benchmark-lab-x/selection-route/v3"}
    lock["route_snapshot_source"] = {
        "path": "runs/fixture/routes-preflight-v3.json",
        "sha256": "b" * 64,
        "schema_version": "benchmark-lab-x/route-preflight-snapshot/v3",
        "observed_at": "2026-08-08T00:00:00+02:00",
        "criterion_version": "benchmark-lab-x/selection-route/v3",
        "budget_estimate_microdollars": lock["budget"]["estimate_microdollars"],
        "budget_cap_microdollars": lock["budget"]["cap_microdollars"],
        "b0_09_approval_sha256": "c" * 64,
        "b0_09_proposal_snapshot_sha256": "d" * 64,
    }
    ownership = {
        "kind": "publisher_managed",
        "canonical_publisher": "example",
        "provider_slug": "example",
        "provider_name": "Example",
        "endpoint_tag": "example",
    }
    evidence = {
        "url": "https://example.invalid/endpoints",
        "observed_at": "2026-08-08T00:00:00+02:00",
        "response_sha256": "e" * 64,
    }
    quantification = {"status": "declared", "value": "bf16"}
    revision = {
        "status": "declared",
        "kind": "endpoint_model_id",
        "value": "example/model-r1",
    }
    for cellule in lock["collections"]:
        route = cellule["route"]
        route.update({
            "endpoint_tag": "example",
            "ownership": copy.deepcopy(ownership),
            "metadata_evidence": copy.deepcopy(evidence),
            "quantization": copy.deepcopy(quantification),
            "revision": copy.deepcopy(revision),
            "criterion_version": "benchmark-lab-x/selection-route/v3",
            "price_source": evidence["url"],
        })
        manifeste = cellule["execution_manifest"]
        manifeste.update({
            "schema_version": SCHEMA_EXECUTION,
            "endpoint_tag": "example",
            "quantization": copy.deepcopy(quantification),
            "revision": copy.deepcopy(revision),
        })
        cellule["base_identity"]["endpoint_tag"] = "example"
        cellule["execution_manifest_hash"] = empreinte(manifeste)
    return lock


def _cause_negative(card_id: str) -> str:
    return {
        "pentagone-api": "OUTPUT_NO_PAGE",
        "pentagone-determinisme": "NON_DETERMINISTIC",
        "pentagone-confinement-court": "INITIAL_STATE_INVALID",
        "pentagone-precision-24s": "PRECISION_THRESHOLD_FAILED",
        "pentagone-horizons-longs": "OUT_OF_BOUNDS",
    }[card_id]


def _resultat_carte(card: dict, valeur: bool) -> dict:
    predicats = {p: valeur for p in card["predicates"]}
    resultat = {
        "etat": "SCORED",
        "cause_code": None if valeur else _cause_negative(card["id"]),
        "verdict": "PASS" if valeur else "FAIL",
        "niveau": None,
        "frontiere": None,
        "predicates": predicats,
        "measurements": {},
    }
    if card["kind"] == "levels":
        resultat["niveau"] = len(predicats) if valeur else 0
        resultat["frontiere"] = None if valeur else card["predicates"][0]
    return resultat


def couverture_complete(racine: Path) -> tuple[dict, str, dict]:
    task_dir = racine / "task"
    (task_dir / "temoins").mkdir(parents=True)
    contenus = {"positif.md": "cas positif\n", "negatif.md": "cas négatif\n"}
    for nom, contenu in contenus.items():
        (task_dir / nom).write_text(contenu, encoding="utf-8")

    lock = lock_minimal()
    provenance = {"temoins": {}}
    for nom, valeur in (("positif.md", True), ("negatif.md", False)):
        provenance["temoins"][nom] = {
            "producteur": f"producteur-{nom}",
            "acces_au_verificateur": False,
            "consignes": f"produire {nom}",
            "resultat_attendu": {
                card["id"]: {p: valeur for p in card["predicates"]}
                for card in lock["axes"]
            },
        }
    provenance_data = (
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    provenance_path = task_dir / "temoins" / "provenance.json"
    provenance_path.write_bytes(provenance_data)

    lock["task"]["task_dir"] = "task"
    lock["task"]["task_tree"] = []
    for nom, contenu in contenus.items():
        data = contenu.encode("utf-8")
        lock["task"]["task_tree"].append({
            "path": nom,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "role": "judge",
        })
    lock["task"]["task_tree"].append({
        "path": "temoins/provenance.json",
        "sha256": hashlib.sha256(provenance_data).hexdigest(),
        "bytes": len(provenance_data),
        "role": "judge",
    })
    lock_hash = empreinte_lock(lock)
    environnement = copy.deepcopy(lock["environments"]["measurement"]["descriptor"])
    environnement_hash = empreinte(environnement)

    witnesses = {}
    observations = {}
    cards = {}
    for nom, valeur in (("positif.md", True), ("negatif.md", False)):
        temoin_hash = hashlib.sha256(contenus[nom].encode("utf-8")).hexdigest()
        source = provenance["temoins"][nom]
        witnesses[nom] = {
            "producer": source["producteur"],
            "access_to_verifier": source["acces_au_verificateur"],
            "instructions": source["consignes"],
            "expected_result": source["resultat_attendu"],
            "sha256": temoin_hash,
        }
        observations[nom] = {}
        for card in lock["axes"]:
            observations[nom][card["id"]] = {
                "score_card_id": card["id"],
                "witness_sha256": temoin_hash,
                "verify_hash": card["verify_hash"],
                "measurement_environment_hash": environnement_hash,
                **_resultat_carte(card, valeur),
            }
    for card in lock["axes"]:
        cards[card["id"]] = {
            "verify_hash": card["verify_hash"],
            "predicates": {
                p: {"positive": ["positif.md"], "negative": ["negatif.md"]}
                for p in card["predicates"]
            },
        }
    receipt = {
        "schema_version": SCHEMA_COVERAGE,
        "campaign_lock_hash": lock_hash,
        "task_version": "task-v3",
        "prompt_sha256": "1" * 64,
        "measurement_environment": environnement,
        "measurement_environment_hash": environnement_hash,
        "provenance_path": "temoins/provenance.json",
        "provenance_sha256": hashlib.sha256(provenance_data).hexdigest(),
        "witnesses": witnesses,
        "observations": observations,
        "cards": cards,
        "qualified": True,
    }
    return lock, lock_hash, receipt


def score_complet(lock: dict, card: dict, cellule: dict) -> tuple[dict, dict, str]:
    lock_hash = empreinte_lock(lock)
    collection = {
        "schema_version": SCHEMA_COLLECTION,
        "protocol_version": PROTOCOLE_VERSION,
        "campaign_lock_hash": lock_hash,
        "collection_id": cellule["collection_id"],
        "attempt": 1,
        "result": "COLLECTED",
        "payload_hash": cellule["payload_hash"],
        "execution_manifest_hash": cellule["execution_manifest_hash"],
        "served": {
            "model": cellule["execution_manifest"]["model_requested"],
            "provider": cellule["execution_manifest"]["provider_expected"],
        },
        "candidate": {
            "sha256": hashlib.sha256(b"response\n").hexdigest(),
            "bytes": len(b"response\n"),
            "kind": "content",
            "truncated": False,
        },
        "response_json_sha256": hashlib.sha256(b"{}\n").hexdigest(),
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "reasoning_tokens": 0,
        },
        "cost_accounting": {
            "status": "known",
            "cost_microdollars": 1,
            "reservation_id": f"{cellule['collection_id']}__a1",
        },
        "duration_ns": 1,
        "cause_code": None,
    }
    collection_hash = empreinte(collection)
    environnement = copy.deepcopy(lock["environments"]["measurement"]["descriptor"])
    contexte = {
        "schema_version": SCHEMA_CONTEXT,
        "protocol_version": PROTOCOLE_VERSION,
        "task": {
            "id": lock["task"]["task_id"],
            "version": lock["task"]["task_version"],
        },
        "prompt_hash": lock["task"]["prompt_sha256"],
        "system_prompt_hash": None,
        "axis_id": card["id"],
        "verify_version": card["verify_version"],
        "verify_hash": card["verify_hash"],
        "measurement_environment_hash": empreinte(environnement),
        "confidentiality_regime": lock["task"]["confidentiality_regime"],
    }
    resultat = _resultat_carte(card, True)
    score = {
        "schema_version": SCHEMA_SCORE,
        "protocol_version": PROTOCOLE_VERSION,
        "campaign_lock_hash": lock_hash,
        "collection_id": cellule["collection_id"],
        "collection_receipt_hash": collection_hash,
        "response_sha256": collection["candidate"]["sha256"],
        "alias": cellule["alias"],
        "run": cellule["run"],
        "axis_id": card["id"],
        "verify_version": card["verify_version"],
        "verify_hash": card["verify_hash"],
        "measurement_context": contexte,
        "measurement_context_hash": empreinte(contexte),
        "measurement_environment": environnement,
        "etat": resultat["etat"],
        "cause_code": resultat["cause_code"],
        "verdict": resultat["verdict"],
        "niveau": resultat["niveau"],
        "frontiere": resultat["frontiere"],
        "predicats": resultat["predicates"],
        "mesures": resultat["measurements"],
    }
    return collection, score, collection_hash


def tentative_complete(lock: dict, cellule: dict, collection: dict) -> dict:
    return {
        "schema_version": SCHEMA_ATTEMPT,
        "protocol_version": PROTOCOLE_VERSION,
        "campaign_lock_hash": empreinte_lock(lock),
        "collection_id": cellule["collection_id"],
        "attempt": collection["attempt"],
        "result": "COMPLETE",
        "cause_code": None,
        "execution_manifest_hash": cellule["execution_manifest_hash"],
        "payload_hash": cellule["payload_hash"],
        "http_response_received": True,
        "candidate_artifact_accepted": True,
        "cost_accounting": copy.deepcopy(collection["cost_accounting"]),
        "retry_after": None,
    }


class ProtocolV2Tests(unittest.TestCase):
    def _lock_continuation_fixture(self, schema: str) -> dict:
        lock = lock_minimal_v4()
        lock["schema_version"] = schema
        lock["operation"] = "continuation_collection"
        lock["instrument_source"] = {
            "commit": "c" * 40,
            "reference_lock_path": "runs/reference/campaign.lock.v4.json",
            "reference_lock_sha256": "d" * 64,
            "reference_campaign_lock_hash": "e" * 64,
        }
        lock["series_source"] = {
            "series_id": "pentagone-v0",
            "inventory_path": "runs/continuation/series-manifest.v1.json",
            "inventory_sha256": "f" * 64,
            "inventory_hash": "a" * 64,
            "global_cap_microdollars": 1_500_000,
            "source_ledger_engaged_microdollars": 500_000,
            "continuation_cap_microdollars": 1_000_000,
            "approved_estimate_microdollars": 100_000,
            "approved_fallback_routes": [],
        }
        if schema == SCHEMA_LOCK_CONTINUATION:
            lock["failure_scope_policy"] = {
                "version": "benchmark-lab-x/failure-scope/v1",
            }
            lock["series_source"]["deferred_pending_slots"] = []
        return lock

    def test_lock_v6_separe_collecte_instrument_budget_et_portee_echec(self):
        lock = self._lock_continuation_fixture(SCHEMA_LOCK_CONTINUATION)
        self.assertEqual(valider_lock(lock), lock)
        invalide = copy.deepcopy(lock)
        invalide["series_source"]["global_cap_microdollars"] += 1
        with self.assertRaises(ContratV2Invalide):
            valider_lock(invalide)

    def test_lock_v5_historique_reste_valide_sans_politique_locale(self):
        lock = self._lock_continuation_fixture(SCHEMA_LOCK_CONTINUATION_V5)
        self.assertEqual(valider_lock(lock), lock)

    def test_lock_v6_accepte_un_sous_ensemble_irregulier_explicitement_differe(self):
        lock = self._lock_continuation_fixture(SCHEMA_LOCK_CONTINUATION)
        aliases = lock["panel"][:3]
        lock["panel"] = aliases
        lock["collections"] = [
            cellule
            for cellule in lock["collections"]
            if (
                cellule["alias"] in aliases[1:]
                or cellule["alias"] == aliases[0] and cellule["run"] == 5
            )
        ]
        lock["quotas"]["attempts_total_max"] = len(lock["collections"]) * 3
        alias_differe = PANEL_B0[3]
        lock["series_source"]["deferred_pending_slots"] = [
            {
                "collection_id": f"{alias_differe}__r{run}",
                "reason": "HOLD_RUNTIME_METADATA_CONFLICT",
            }
            for run in range(2, 7)
        ]
        self.assertEqual(len(lock["collections"]), 13)
        self.assertEqual(valider_lock(lock), lock)

        invalide = copy.deepcopy(lock)
        invalide["series_source"]["deferred_pending_slots"][0][
            "collection_id"
        ] = lock["collections"][0]["collection_id"]
        with self.assertRaisesRegex(
            ContratV2Invalide, "slots différés dupliqués ou encore ciblés"
        ):
            valider_lock(invalide)

    def test_http_non_retryable_v6_bloque_route_sans_hold_global(self):
        lock = self._lock_continuation_fixture(SCHEMA_LOCK_CONTINUATION)
        lock_hash = empreinte_lock(lock)
        cellule = lock["collections"][0]
        meme_alias_en_vol = lock["collections"][1]
        autre_alias = next(
            item for item in lock["collections"] if item["alias"] != cellule["alias"]
        )
        receipt = {
            "schema_version": SCHEMA_ATTEMPT,
            "protocol_version": PROTOCOLE_VERSION,
            "campaign_lock_hash": lock_hash,
            "collection_id": cellule["collection_id"],
            "attempt": 1,
            "result": "FAILED_NON_RETRYABLE",
            "cause_code": "HTTP_NON_RETRYABLE",
            "execution_manifest_hash": cellule["execution_manifest_hash"],
            "payload_hash": cellule["payload_hash"],
            "http_response_received": True,
            "candidate_artifact_accepted": False,
            "cost_accounting": {
                "status": "upper_bound",
                "cost_microdollars": cellule["max_cost_microdollars"],
                "reservation_id": f"{cellule['collection_id']}__a1",
            },
            "retry_after": None,
        }
        artefact_accepte = copy.deepcopy(receipt)
        artefact_accepte["candidate_artifact_accepted"] = True
        self.assertEqual(
            decision_reprise(
                artefact_accepte,
                cellule,
                lock_hash,
                lock["attempts_max"],
                "benchmark-lab-x/failure-scope/v1",
            )["action"],
            "hold",
        )
        api_error = copy.deepcopy(receipt)
        api_error["cause_code"] = "API_ERROR"
        self.assertEqual(
            decision_reprise(
                api_error,
                cellule,
                lock_hash,
                lock["attempts_max"],
                "benchmark-lab-x/failure-scope/v1",
            )["action"],
            "block_route",
        )
        with tempfile.TemporaryDirectory() as tmp:
            campagne = Path(tmp)
            ledger = RegistreBudget(
                campagne / "budget-ledger.json",
                lock["budget"]["cap_microdollars"],
                lock_hash,
            )
            ledger.reserver(
                receipt["cost_accounting"]["reservation_id"],
                cellule["max_cost_microdollars"],
            )
            arrete, reprise = lancer_campagne._traiter_resultat_v2(
                campagne,
                ledger,
                lock,
                lock_hash,
                cellule,
                {
                    "collection_id": cellule["collection_id"],
                    "attempt": 1,
                    "reservation_id": receipt["cost_accounting"]["reservation_id"],
                    "code": 5,
                    "receipt": receipt,
                    "error": "",
                },
                False,
                {meme_alias_en_vol["collection_id"]},
            )
            self.assertFalse(arrete)
            self.assertIsNone(reprise)
            self.assertFalse((campagne / "operator-hold.json").exists())
            self.assertFalse((
                campagne / "collections" / meme_alias_en_vol["collection_id"]
                / "collection-state.json"
            ).exists())
            self.assertFalse((
                campagne / "collections" / autre_alias["collection_id"]
                / "collection-state.json"
            ).exists())
            fermes = [
                item for item in lock["collections"]
                if item["alias"] == cellule["alias"]
                and item["collection_id"] != meme_alias_en_vol["collection_id"]
            ]
            for item in fermes:
                state = json.loads((
                    campagne / "collections" / item["collection_id"]
                    / "collection-state.json"
                ).read_text(encoding="utf-8"))
                self.assertEqual(state["state"], "INFRA_ERROR")
                self.assertEqual(state["cause_code"], "PROVIDER_ROUTE_UNAVAILABLE")

            reservation_en_vol = f"{meme_alias_en_vol['collection_id']}__a1"
            ledger.reserver(
                reservation_en_vol, meme_alias_en_vol["max_cost_microdollars"]
            )
            tentative_en_vol = copy.deepcopy(receipt)
            tentative_en_vol.update({
                "collection_id": meme_alias_en_vol["collection_id"],
                "result": "FAILED_RETRYABLE",
                "cause_code": "HTTP_503",
                "execution_manifest_hash": meme_alias_en_vol[
                    "execution_manifest_hash"
                ],
                "payload_hash": meme_alias_en_vol["payload_hash"],
                "cost_accounting": {
                    "status": "upper_bound",
                    "cost_microdollars": meme_alias_en_vol["max_cost_microdollars"],
                    "reservation_id": reservation_en_vol,
                },
            })
            arrete, reprise = lancer_campagne._traiter_resultat_v2(
                campagne,
                ledger,
                lock,
                lock_hash,
                meme_alias_en_vol,
                {
                    "collection_id": meme_alias_en_vol["collection_id"],
                    "attempt": 1,
                    "reservation_id": reservation_en_vol,
                    "code": 5,
                    "receipt": tentative_en_vol,
                    "error": "",
                },
                False,
                set(),
            )
            self.assertFalse(arrete)
            self.assertIsNone(reprise)
            state = json.loads((
                campagne / "collections" / meme_alias_en_vol["collection_id"]
                / "collection-state.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(state["cause_code"], "PROVIDER_ROUTE_UNAVAILABLE")

    def test_marqueur_http_expurge_identifiants_fournisseur(self):
        brut = json.dumps({
            "error": {
                "metadata": {
                    "raw": json.dumps({
                        "error": {"request_id": "request-fixture-secret"}
                    })
                }
            },
            "user_id": "user-fixture-secret",
        })
        expurge = collecteur.redact_http_body(brut)
        self.assertNotIn("request-fixture-secret", expurge)
        self.assertNotIn("user-fixture-secret", expurge)
        self.assertIn("[REDACTED]", expurge)

    def test_marqueur_http_expurge_json_doublement_encode_avec_prefixe(self):
        imbrique = json.dumps({
            "error": {
                "request_id": "request-deep-fixture-secret",
            }
        })
        brut = "http_400\n\n" + json.dumps({
            "error": {
                "metadata": {
                    "raw": json.dumps({"details": imbrique}),
                },
            },
            "user_id": "user-prefix-fixture-secret",
        })
        expurge = collecteur.redact_http_body(brut)
        self.assertNotIn("request-deep-fixture-secret", expurge)
        self.assertNotIn("user-prefix-fixture-secret", expurge)
        self.assertGreaterEqual(expurge.count("[REDACTED]"), 2)

    def _preparer_collecte_simulee(self) -> dict:
        temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(temporaire.cleanup)
        campagne = Path(temporaire.name)
        lock = lock_minimal()
        cellule = lock["collections"][0]
        prompt = "stimulus exact de la fixture\n"
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        lock["task"]["prompt_sha256"] = prompt_hash
        for collecte in lock["collections"]:
            collecte["prompt_sha256"] = prompt_hash
        payload = construire_payload(cellule["execution_manifest"], prompt)
        cellule["payload_hash"] = hashlib.sha256(payload).hexdigest()
        lock_path = campagne / "campaign.lock.json"
        lock_path.write_text(
            json.dumps(lock, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        autorisation = campagne / "paid-authorization.json"
        autorisation.write_text("{}\n", encoding="utf-8")
        lock_hash = empreinte_lock(lock)
        ledger_path = campagne / "budget-ledger.json"
        reservation_id = f"{cellule['collection_id']}__a1"
        RegistreBudget(
            ledger_path, lock["budget"]["cap_microdollars"], lock_hash
        ).reserver(reservation_id, cellule["max_cost_microdollars"])
        argv = [
            "collect.py",
            str(RACINE / lock["task"]["task_dir"]),
            "--campaign-lock",
            str(lock_path),
            "--collection-id",
            cellule["collection_id"],
            "--paid-authorization",
            str(autorisation),
            "--budget-ledger",
            str(ledger_path),
            "--reservation-id",
            reservation_id,
            "--out-root",
            str(campagne),
        ]
        return {
            "campaign_dir": campagne,
            "lock": lock,
            "lock_hash": lock_hash,
            "cell": cellule,
            "payload": payload,
            "prompt": prompt,
            "argv": argv,
            "attempt_dir": campagne / "collections" / cellule["collection_id"] / "attempt-1",
        }

    def _appeler_collecteur_simule(self, fixture: dict, transport) -> tuple[int, object]:
        post_patch = (
            {"side_effect": transport}
            if isinstance(transport, BaseException)
            else {"return_value": transport}
        )
        with (
            patch.object(sys, "argv", fixture["argv"]),
            patch.object(
                collecteur,
                "valider_lock",
                side_effect=lambda objet, _racine=None: valider_lock(objet),
            ),
            patch.object(collecteur, "valider_environnement_observe"),
            patch.object(collecteur, "valider_autorisation_payante"),
            patch.object(
                collecteur,
                "assembler_prompt_verrouille",
                return_value=(fixture["prompt"], {}),
            ),
            patch.object(collecteur, "preflight_key", return_value="fixture-key"),
            patch.object(collecteur.requests, "post", **post_patch) as post,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            try:
                collecteur.main()
            except SystemExit as exc:
                return int(exc.code), post
        return 0, post

    def test_prompt_verrouille_inclut_donnees(self):
        task_dir = RACINE / "tasks/dev/pentagone-rotatif"
        task = {
            "task_dir": "tasks/dev/pentagone-rotatif",
            "task_file": "task-v3.md",
            "task_tree": _arbre_tache(task_dir, "task-v3.md", ["donnees.md"]),
            "prompt_sha256": "0" * 64,
        }
        prompt, inputs = assembler_prompt_verrouille(
            RACINE, task, verifier_arbre=True, verifier_prompt=False
        )
        self.assertEqual(set(inputs), {"donnees.md"})
        self.assertEqual(
            hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "9e64405f9fac5dec58a576e7cdbf81aed5b1b6d4c2be8bca74ef1f73fbe4a613",
        )

    def test_collecteur_direct_refuse_apres_complete_ou_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            cid = "reference-gpt-5-6__r1"
            tentative = racine / "collections" / cid / "attempt-1"
            tentative.mkdir(parents=True)
            (tentative / "COMPLETE").write_text("ok\n", encoding="utf-8")
            self.assertIn("COMPLETE", resultat_acquis_v2(racine, cid) or "")

            (tentative / "COMPLETE").unlink()
            score = racine / "scores" / ("a" * 64) / "pentagone-api" / "score.json"
            score.parent.mkdir(parents=True)
            score.write_text(
                json.dumps({"collection_id": cid, "etat": "SCORED"}), encoding="utf-8"
            )
            self.assertIn("SCORED", resultat_acquis_v2(racine, cid) or "")

    def test_qualification_set_est_fermee_et_exclut_les_temoins_historique(self):
        with tempfile.TemporaryDirectory() as tmp:
            dossier = Path(tmp)
            (dossier / "temoins").mkdir()
            qualifiant = dossier / "temoins/positif.md"
            qualifiant.write_text("positif\n", encoding="utf-8")
            (dossier / "temoins/historique.md").write_text("ancien\n", encoding="utf-8")
            (dossier / "anchor-historique.md").write_text("ancien\n", encoding="utf-8")
            objet = {
                "qualification_set": ["temoins/positif.md"],
                "temoins": {
                    "temoins/positif.md": {"producteur": "fixture"},
                    "temoins/historique.md": {"producteur": "historique"},
                },
            }
            provenance, chemins = charger_qualification_set(dossier, objet)
            self.assertEqual(
                set(provenance), {"temoins/positif.md", "temoins/historique.md"}
            )
            self.assertEqual(chemins, [qualifiant.resolve()])

            incoherent = copy.deepcopy(objet)
            incoherent["qualification_set"].append("temoins/inconnu.md")
            with self.assertRaises(ContratV2Invalide):
                charger_qualification_set(dossier, incoherent)

    def test_lock_contient_114_collectes_et_cinq_axes(self):
        lock = valider_lock(lock_minimal())
        self.assertEqual(len(lock["collections"]), 114)
        self.assertEqual([c["id"] for c in lock["axes"]], list(AXES_PENTAGONE))
        altere = copy.deepcopy(lock)
        altere["collections"].pop()
        with self.assertRaises(ContratV2Invalide):
            valider_lock(altere)

    def test_verify_v6_retire_uniquement_e75_precision(self):
        self.assertEqual(sum(map(len, PREDICATS_V4.values())), 51)
        self.assertEqual(sum(map(len, PREDICATS_V5.values())), 50)
        for axis_id in AXES_PENTAGONE[:-1]:
            self.assertEqual(PREDICATS_V5[axis_id], PREDICATS_V4[axis_id])
        self.assertEqual(
            PREDICATS_V5["pentagone-horizons-longs"],
            tuple(
                predicate
                for predicate in PREDICATS_V4["pentagone-horizons-longs"]
                if predicate != "E75_PRECISION"
            ),
        )

    def test_lock_refuse_source_ou_environnement_non_fige(self):
        sans_source = lock_minimal()
        del sans_source["repository_source"]
        with self.assertRaises(ContratV2Invalide):
            valider_lock(sans_source)

        environnement_altere = lock_minimal()
        environnement_altere["environments"]["runner"]["descriptor"]["timezone"] = "UTC"
        with self.assertRaisesRegex(ContratV2Invalide, "empreinte"):
            valider_lock(environnement_altere)

        lock = lock_minimal()
        observe = copy.deepcopy(lock["environments"]["runner"]["descriptor"])
        valider_environnement_observe(lock, "runner", observe)
        observe["architecture"] = "autre"
        with self.assertRaisesRegex(ContratV2Invalide, "différent du lock"):
            valider_environnement_observe(lock, "runner", observe)

    def test_lock_refuse_une_identite_ou_un_budget_auto_declare(self):
        lock = lock_minimal()
        variantes = []
        modele = copy.deepcopy(lock)
        modele["collections"][0]["execution_manifest"]["model_requested"] = "example/autre"
        variantes.append(modele)
        cout = copy.deepcopy(lock)
        cout["collections"][0]["max_cost_microdollars"] -= 1
        variantes.append(cout)
        agregation = copy.deepcopy(lock)
        agregation["axes"][0]["aggregation"]["order_statistic"] = 3
        variantes.append(agregation)
        verify_hash = copy.deepcopy(lock)
        verify_hash["axes"][0]["verify_hash"] = "0" * 64
        variantes.append(verify_hash)
        for variante in variantes:
            with self.subTest(variante=variante):
                with self.assertRaises(ContratV2Invalide):
                    valider_lock(variante)

    def test_manifestes_v3_et_v4_gardent_des_contrats_distincts(self):
        manifeste_v3 = lock_minimal()["collections"][0]["execution_manifest"]
        valider_manifeste_execution(manifeste_v3)
        manifeste_v4 = lock_minimal_v4()["collections"][0]["execution_manifest"]
        valider_manifeste_execution(manifeste_v4)

        chaine_dans_v4 = copy.deepcopy(manifeste_v4)
        chaine_dans_v4["quantization"] = "bf16"
        with self.assertRaises(ContratV2Invalide):
            valider_manifeste_execution(chaine_dans_v4)

        endpoint_dans_v3 = copy.deepcopy(manifeste_v3)
        endpoint_dans_v3["endpoint_tag"] = "example"
        with self.assertRaises(ContratV2Invalide):
            valider_manifeste_execution(endpoint_dans_v3)

    def test_manifeste_v5_fige_le_fallback_avant_artefact(self):
        manifeste = copy.deepcopy(
            lock_minimal_v4()["collections"][0]["execution_manifest"]
        )
        manifeste.update({
            "schema_version": SCHEMA_EXECUTION_ROUTE_PROGRAM,
            "provider_routes": [
                {
                    "role": "primary",
                    "provider_pinned": "atlas-cloud",
                    "provider_expected": "AtlasCloud",
                    "endpoint_tag": "atlas-cloud/fp8",
                    "quantization": {"status": "declared", "value": "fp8"},
                    "revision": manifeste["revision"],
                    "max_tokens": 131_072,
                    "metadata_evidence": manifeste["request_parameters"].get(
                        "metadata_evidence",
                        {
                            "url": "https://example.invalid/endpoints",
                            "observed_at": "2026-08-10T00:00:00Z",
                            "response_sha256": "a" * 64,
                        },
                    ),
                },
                {
                    "role": "secondary",
                    "provider_pinned": "deepinfra",
                    "provider_expected": "DeepInfra",
                    "endpoint_tag": "deepinfra/fp8",
                    "quantization": {"status": "declared", "value": "fp8"},
                    "revision": manifeste["revision"],
                    "max_tokens": 131_072,
                    "metadata_evidence": {
                        "url": "https://example.invalid/endpoints",
                        "observed_at": "2026-08-10T00:00:00Z",
                        "response_sha256": "a" * 64,
                    },
                },
            ],
            "router_metadata": {
                "header": "X-OpenRouter-Metadata",
                "value": "enabled",
            },
            "provider_pinned": "atlas-cloud",
            "provider_expected": "AtlasCloud",
            "endpoint_tag": "atlas-cloud/fp8",
            "quantization": {"status": "declared", "value": "fp8"},
            "max_tokens": 131_072,
        })
        manifeste["request_parameters"]["provider"] = {
            "order": ["atlas-cloud", "deepinfra"],
            "only": ["atlas-cloud", "deepinfra"],
            "allow_fallbacks": True,
            "require_parameters": True,
            "data_collection": manifeste["data_policy_requested"],
        }
        valider_manifeste_execution(manifeste)
        payload = json.loads(construire_payload(manifeste, "fixture").decode("utf-8"))
        self.assertEqual(
            payload["provider"]["order"], ["atlas-cloud", "deepinfra"]
        )
        self.assertTrue(payload["provider"]["allow_fallbacks"])

        ordre_inverse = copy.deepcopy(manifeste)
        ordre_inverse["request_parameters"]["provider"]["order"].reverse()
        with self.assertRaisesRegex(ContratV2Invalide, "programme provider"):
            valider_manifeste_execution(ordre_inverse)

        cellule = {
            "collection_id": "hy3__r1",
            "payload_hash": "b" * 64,
            "execution_manifest_hash": empreinte(manifeste),
            "execution_manifest": manifeste,
            "max_cost_microdollars": 105_543,
        }
        recu = {
            "schema_version": SCHEMA_COLLECTION,
            "protocol_version": PROTOCOLE_VERSION,
            "campaign_lock_hash": "c" * 64,
            "collection_id": "hy3__r1",
            "attempt": 1,
            "result": "COLLECTED",
            "payload_hash": "b" * 64,
            "execution_manifest_hash": empreinte(manifeste),
            "served": {"model": manifeste["model_requested"], "provider": "DeepInfra"},
            "candidate": {
                "sha256": "d" * 64,
                "bytes": 1,
                "kind": "content",
                "truncated": False,
            },
            "response_json_sha256": "e" * 64,
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "reasoning_tokens": 0,
            },
            "cost_accounting": {
                "status": "known",
                "cost_microdollars": 1,
                "reservation_id": "hy3__r1__a1",
            },
            "duration_ns": 1,
            "cause_code": None,
        }
        valider_recu_collecte(recu, "c" * 64, cellule)
        recu_hors_programme = copy.deepcopy(recu)
        recu_hors_programme["served"]["provider"] = "Other"
        with self.assertRaisesRegex(ContratV2Invalide, "provider servi"):
            valider_recu_collecte(recu_hors_programme, "c" * 64, cellule)

    def test_lock_v4_refuse_not_disclosed_hors_api_editeur(self):
        lock = lock_minimal_v4()
        valider_lock(lock)
        quantification = {
            "status": "not_disclosed",
            "value": None,
            "basis": "publisher_managed_api",
            "publisher": "example",
        }
        for cellule in lock["collections"]:
            cellule["route"]["quantization"] = copy.deepcopy(quantification)
            cellule["execution_manifest"]["quantization"] = copy.deepcopy(quantification)
            cellule["execution_manifest_hash"] = empreinte(cellule["execution_manifest"])
        valider_lock(lock)

        altere = copy.deepcopy(lock)
        for cellule in altere["collections"]:
            cellule["route"]["ownership"]["kind"] = "third_party"
        with self.assertRaisesRegex(ContratV2Invalide, "not_disclosed"):
            valider_lock(altere)

    def test_selection_route_v3_distingue_editeurs_et_tiers(self):
        cas_editeurs = (
            ({"tag": "alibaba", "provider_name": "Alibaba"}, "qwen/modele"),
            ({"tag": "xai", "provider_name": "xAI"}, "x-ai/modele"),
            ({"tag": "mistral", "provider_name": "Mistral"}, "mistralai/modele"),
            ({"tag": "moonshotai/mxfp4", "provider_name": "Moonshot AI"},
             "moonshotai/kimi-k3"),
        )
        for endpoint, modele in cas_editeurs:
            endpoint.update({
                "context_length": 16_384,
                "supported_parameters": ["temperature", "top_p", "seed", "max_tokens"],
            })
            with self.subTest(modele=modele):
                resultat = figer_routes_precollecte.evaluer(endpoint, modele)
                self.assertTrue(resultat["editeur"])
                self.assertEqual(resultat["exclusions"], [])

        non_divulgue = figer_routes_precollecte.evaluer(
            {
                "tag": "openai",
                "provider_name": "OpenAI",
                "context_length": 16_384,
                "supported_parameters": ["temperature", "top_p", "seed", "max_tokens"],
            },
            "openai/modele",
        )
        self.assertEqual(non_divulgue["format_policy_class"], "publisher_not_disclosed")
        self.assertIsNone(non_divulgue["rang_fidelite"])

        tiers = {
            "tag": "deepinfra",
            "provider_name": "DeepInfra",
            "context_length": 16_384,
            "supported_parameters": ["temperature", "top_p", "seed", "max_tokens"],
        }
        resultat = figer_routes_precollecte.evaluer(tiers, "xiaomi/mimo-v2.5")
        self.assertIn("quantification non déclarée sur une route tierce", resultat["exclusions"])

    def test_approbation_b0_09_v2_cible_le_snapshot_propose_exact(self):
        self.assertEqual(
            figer_routes_precollecte._raisons_hold_cible({
                "pin_changes": [],
                "b0_09_approval": None,
                "budget_reestimate": {"status": "B0_09_UNCHANGED"},
            }),
            ["HOLD_B0_09_SNAPSHOT_APPROVAL_REQUIRED"],
        )
        proposition = {"proposal_source": None, "b0_09_approval": None}
        with tempfile.TemporaryDirectory() as tmp:
            racine_fixture = Path(tmp)
            source_path = racine_fixture / "routes.v3.proposed.json"
            source_path.write_text(
                json.dumps(proposition, sort_keys=True) + "\n", encoding="utf-8"
            )
            relatif = source_path.relative_to(racine_fixture).as_posix()
            source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            approbation = {
                "schema_version": "benchmark-lab-x/b0-09-approval/v2",
                "decision": "B0_09_REVISED_ESTIMATE_APPROVED",
                "approved_by": "Ayo",
                "approved_at": "2026-08-08T00:00:00+02:00",
                "estimate_microdollars": 32_226_068,
                "cap_microdollars": 100_000_000,
                "source_snapshot_path": relatif,
                "source_snapshot_sha256": source_hash,
            }
            snapshot = {
                "proposal_source": {"path": relatif, "sha256": source_hash},
                "b0_09_approval": approbation,
                "budget_reestimate": {
                    "repriced_estimate_microdollars": 32_226_068,
                    "approved_cap_microdollars": 100_000_000,
                },
            }
            lu, source = figer_routes_precollecte._source_proposition(snapshot, racine_fixture)
            self.assertEqual(lu, proposition)
            self.assertEqual(source["sha256"], source_hash)

            altere = copy.deepcopy(snapshot)
            altere["proposal_source"]["sha256"] = "f" * 64
            altere["b0_09_approval"]["source_snapshot_sha256"] = "f" * 64
            with self.assertRaisesRegex(
                figer_routes_precollecte.SnapshotRoutesInvalide,
                "empreinte de la proposition",
            ):
                figer_routes_precollecte._source_proposition(altere, racine_fixture)

    def test_preparateur_v3_prend_budget_quotas_et_payload_dans_l_intention(self):
        alias = "reference-gpt-5-6"
        route = {
            "metadata_status": "resolved",
            "backend": "openrouter",
            "provider": "openai",
            "expect_provider": "OpenAI",
            "quantization": "bf16",
            "revision": "openai/gpt-5.6-sol",
            "criterion_version": "benchmark-lab-x/selection-route/v2",
            "price_source": "fixture locale",
            "price_observed_at": "2026-08-08T00:00:00+02:00",
            "input_usd_per_million_tokens": "0.1",
            "output_usd_per_million_tokens": "0.2",
            "request_usd": "0",
            "max_tokens": 4_096,
        }
        source = {
            "path": "runs/fixture/routes-preflight.json",
            "sha256": "b" * 64,
            "schema_version": "benchmark-lab-x/route-preflight-snapshot/v2",
            "criterion_version": route["criterion_version"],
            "observed_at": route["price_observed_at"],
        }
        quotas = {
            "attempts_total_max": 18,
            "in_flight_by_backend": {"openrouter": 1},
            "in_flight_by_provider": {"openai": 1},
        }
        draft = {
            "schema_version": "benchmark-lab-x/campaign-draft/v3",
            "protocol_version": PROTOCOLE_VERSION,
            "operation": "new_collection",
            "campaign_id": "fixture-preparation-v3",
            "question": "Quelle configuration réussit cette carte ?",
            "created_at": "2026-08-08T00:00:00+02:00",
            "source_commit": "d" * 40,
            "candidates": [alias],
            "runs": 6,
            "attempts_max": 3,
            "concurrence": 1,
            "timeout": 321,
            "quotas": quotas,
            "task_dir": "tasks/dev/pentagone-rotatif",
            "task_file": "task-v3.md",
            "visible_inputs": ["donnees.md"],
            "confidentiality_regime": "expose",
            "data_policy_requested": "deny",
            "models_file": "models.toml",
            "route_snapshot_file": source["path"],
            "route_snapshot_sha256": source["sha256"],
            "cap_microdollars": 1_000_000,
            "estimate_microdollars": 100_000,
            "estimate_source": "fixture préenregistrée",
            "audit_plans": {
                axis_id: audit_plan_fixture(axis_id)
                for axis_id in AXES_PENTAGONE
            },
        }
        with patch.object(
            preparer_campagne, "_charger_snapshot_routes",
            return_value=({alias: route}, source),
        ), patch.object(
            preparer_campagne, "descripteur_environnement_runner",
            return_value=environnement_fixture(None),
        ), patch.object(
            preparer_campagne, "descripteur_mesure",
            return_value=environnement_fixture(
                {"name": "chromium", "version": "151.0.7922.34"}
            ),
        ), patch.object(
            preparer_campagne, "valider_lock",
            side_effect=lambda objet, _racine: valider_lock(objet),
        ):
            lock = construire_lock(draft)
        valider_lock(lock)
        self.assertEqual(lock["budget"], {
            "currency": "USD",
            "cap_microdollars": 1_000_000,
            "estimate_microdollars": 100_000,
            "estimate_source": "fixture préenregistrée",
        })
        self.assertEqual(lock["quotas"], quotas)
        self.assertEqual(lock["runner"], {"concurrency": 1, "transport_timeout_s": 321})
        self.assertEqual(len(lock["collections"]), 6)
        self.assertEqual(len(lock["axes"]), 5)
        cellule = lock["collections"][0]
        self.assertEqual(cellule["execution_manifest"]["max_tokens"], 4_096)
        prompt, _ = assembler_prompt_verrouille(RACINE, lock["task"])
        self.assertEqual(
            cellule["payload_hash"],
            hashlib.sha256(
                construire_payload(cellule["execution_manifest"], prompt)
            ).hexdigest(),
        )

    def test_preparateur_v4_materialise_route_et_quantification_structurees(self):
        alias = "reference-gpt-5-6"
        evidence = {
            "url": "https://openrouter.ai/api/v1/models/openai/gpt-5.6-sol/endpoints",
            "observed_at": "2026-08-08T00:00:00+02:00",
            "response_sha256": "e" * 64,
        }
        quantification = {
            "status": "not_disclosed",
            "value": None,
            "basis": "publisher_managed_api",
            "publisher": "openai",
        }
        revision = {
            "status": "declared",
            "kind": "endpoint_model_id",
            "value": "openai/gpt-5.6-sol",
        }
        route = {
            "metadata_status": "resolved",
            "backend": "openrouter",
            "endpoint_tag": "openai",
            "provider": "openai",
            "expect_provider": "OpenAI",
            "ownership": {
                "kind": "publisher_managed",
                "canonical_publisher": "openai",
                "provider_slug": "openai",
                "provider_name": "OpenAI",
                "endpoint_tag": "openai",
            },
            "quantization": quantification,
            "revision": revision,
            "metadata_evidence": evidence,
            "criterion_version": "benchmark-lab-x/selection-route/v3",
            "price_source": evidence["url"],
            "price_observed_at": evidence["observed_at"],
            "input_usd_per_million_tokens": "0.1",
            "output_usd_per_million_tokens": "0.2",
            "request_usd": "0",
            "max_tokens": 4_096,
        }
        source = {
            "path": "runs/fixture/routes-preflight-v3.json",
            "sha256": "b" * 64,
            "schema_version": "benchmark-lab-x/route-preflight-snapshot/v3",
            "criterion_version": route["criterion_version"],
            "observed_at": evidence["observed_at"],
            "budget_estimate_microdollars": 32_226_068,
            "budget_cap_microdollars": 100_000_000,
            "b0_09_approval_sha256": "c" * 64,
            "b0_09_proposal_snapshot_sha256": "d" * 64,
        }
        draft = {
            "schema_version": "benchmark-lab-x/campaign-draft/v4",
            "protocol_version": PROTOCOLE_VERSION,
            "operation": "new_collection",
            "campaign_id": "fixture-preparation-v4",
            "question": "Quelle configuration réussit cette carte ?",
            "created_at": "2026-08-08T00:00:00+02:00",
            "source_commit": "d" * 40,
            "b0_08_status": "APPROVED",
            "b0_09_status": "APPROVED",
            "b0_10_status": "HOLD",
            "candidates": [alias],
            "runs": 6,
            "attempts_max": 3,
            "concurrence": 1,
            "timeout": 321,
            "quotas": {
                "attempts_total_max": 18,
                "in_flight_by_backend": {"openrouter": 1},
                "in_flight_by_provider": {"openai": 1},
            },
            "task_dir": "tasks/dev/pentagone-rotatif",
            "task_file": "task-v4.md",
            "visible_inputs": ["donnees.md"],
            "confidentiality_regime": "expose",
            "data_policy_requested": "deny",
            "models_file": "models.toml",
            "route_snapshot_file": source["path"],
            "route_snapshot_sha256": source["sha256"],
            "campaign_lock": "campaign.lock.v4.json",
            "paid_authorization": "paid-authorization.json",
            "budget_ledger": "budget-ledger.json",
            "cap_microdollars": 100_000_000,
            "estimate_microdollars": 32_226_068,
            "estimate_source": "fixture snapshot v3 approuvée",
            "audit_plans": {
                axis_id: audit_plan_fixture(axis_id)
                for axis_id in AXES_PENTAGONE
            },
        }
        with patch.object(
            preparer_campagne, "_charger_snapshot_routes",
            return_value=({alias: route}, source),
        ), patch.object(
            preparer_campagne, "descripteur_environnement_runner",
            return_value=environnement_fixture(None),
        ), patch.object(
            preparer_campagne, "descripteur_mesure",
            return_value=environnement_fixture(
                {"name": "chromium", "version": "151.0.7922.34"}
            ),
        ), patch.object(
            preparer_campagne, "valider_lock",
            side_effect=lambda objet, _racine: valider_lock(objet),
        ):
            lock = construire_lock(draft)
        valider_lock(lock)
        cellule = lock["collections"][0]
        self.assertEqual(lock["schema_version"], SCHEMA_LOCK)
        self.assertEqual(lock["budget"]["cap_microdollars"], 100_000_000)
        self.assertEqual(lock["task"]["task_version"], "task-v4")
        self.assertEqual({axis["verify_version"] for axis in lock["axes"]}, {"verify-v6"})
        self.assertEqual(sum(len(axis["predicates"]) for axis in lock["axes"]), 50)
        self.assertEqual(cellule["execution_manifest"]["schema_version"], SCHEMA_EXECUTION)
        self.assertEqual(cellule["base_identity"]["endpoint_tag"], "openai")
        self.assertEqual(cellule["route"]["quantization"], quantification)

    def test_snapshot_routes_v2_accepte_le_seul_panel_demande(self):
        with tempfile.TemporaryDirectory() as tmp:
            models_file = Path(tmp) / "models.toml"
            models_file.write_text(
                '[fixture]\nmodel = "example/model"\nprovider = "example"\n',
                encoding="utf-8",
            )
            registre = figer_routes_precollecte.charger_registre(models_file)
        self.assertEqual(set(registre), {"fixture"})
        route = {
            "metadata_status": "resolved",
            "backend": "openrouter",
            "provider": "example",
            "expect_provider": "Example",
            "quantization": "bf16",
            "revision": "example/model-r1",
            "criterion_version": figer_routes_precollecte.CRITERE_VERSION_HISTORIQUE,
            "price_source": "fixture locale",
            "price_observed_at": "2026-08-08T00:00:00+02:00",
            "input_usd_per_million_tokens": "0.1",
            "output_usd_per_million_tokens": "0.2",
            "request_usd": "0",
            "max_tokens": 4_096,
        }
        snapshot = {
            "schema_version": "benchmark-lab-x/route-preflight-snapshot/v2",
            "panel": ["fixture"],
            "observed_at": "2026-08-08T00:00:00+02:00",
            "criterion_version": figer_routes_precollecte.CRITERE_VERSION_HISTORIQUE,
            "models_file": "models.toml",
            "models_file_sha256": "a" * 64,
            "models": {"example/model": {"selected_tag": "example"}},
            "resolved": {"fixture": route},
        }
        self.assertEqual(
            figer_routes_precollecte.valider_cible_historique_v2(snapshot)["aliases_resolved"], 1
        )

    def test_adaptateur_materialise_contenu_vide_et_refus(self):
        normal = {"choices": [{"message": {"content": "ok"}}]}
        vide = {"choices": [{"message": {"content": ""}}]}
        refus = {"choices": [{"message": {"content": None, "refusal": "refus"}}]}
        self.assertEqual(collecteur.validate_response(normal)[2], "content")
        self.assertEqual(collecteur.validate_response(vide), ("", vide["choices"][0], "empty"))
        self.assertEqual(collecteur.validate_response(refus)[2], "refusal")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            collecteur.validate_response({"choices": [{"message": {}}]})

    def test_collecteur_v3_envoie_les_octets_figes_et_accepte_vide_ou_refus(self):
        cas = (
            ({"content": ""}, "empty", b""),
            ({"content": None, "refusal": "refus explicite"}, "refusal", b"refus explicite"),
        )
        for message, kind, octets_attendus in cas:
            with self.subTest(kind=kind):
                fixture = self._preparer_collecte_simulee()
                payload_reponse = {
                    "model": fixture["cell"]["execution_manifest"]["model_requested"],
                    "provider": "Example",
                    "choices": [{"message": message, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 0,
                        "cost": 0.0001,
                    },
                }
                code, post = self._appeler_collecteur_simule(
                    fixture, ReponseHTTPFixture(200, payload_reponse)
                )
                self.assertEqual(code, 0)
                self.assertEqual(post.call_count, 1)
                self.assertEqual(post.call_args.kwargs["data"], fixture["payload"])
                tentative = charger_json(fixture["attempt_dir"] / "attempt-receipt.json")
                collecte = charger_json(fixture["attempt_dir"] / "collection-receipt.json")
                valider_chaine_collecte(
                    tentative,
                    collecte,
                    fixture["lock_hash"],
                    fixture["cell"],
                )
                self.assertEqual(tentative["result"], "COMPLETE")
                self.assertTrue(tentative["candidate_artifact_accepted"])
                self.assertEqual(collecte["candidate"]["kind"], kind)
                self.assertEqual(
                    (fixture["attempt_dir"] / "response.md").read_bytes(),
                    octets_attendus,
                )

    def test_rapport_v2_conserve_la_route_servie_sans_score(self):
        fixture = self._preparer_collecte_simulee()
        payload_reponse = {
            "model": fixture["cell"]["execution_manifest"]["model_requested"],
            "provider": "Example",
            "choices": [{
                "message": {"content": "fixture"},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "cost": 0.0001,
            },
        }
        code, _post = self._appeler_collecteur_simule(
            fixture, ReponseHTTPFixture(200, payload_reponse)
        )
        self.assertEqual(code, 0)
        score = rapport_campagne._score_v2(
            fixture["campaign_dir"],
            fixture["lock"],
            fixture["lock_hash"],
            fixture["lock"]["axes"][0],
            fixture["cell"]["alias"],
            fixture["cell"]["run"],
        )
        self.assertEqual(score["etat"], "MISSING")
        self.assertEqual(score["served"]["provider"], "Example")
        self.assertEqual(score["provider_route_order"], ["example"])

    def test_collecteur_v3_emet_les_reprises_fermees_sans_remplacement_tardif(self):
        for status, cause in ((429, "HTTP_429"), (503, "HTTP_503")):
            with self.subTest(cause=cause):
                fixture = self._preparer_collecte_simulee()
                code, post = self._appeler_collecteur_simule(
                    fixture,
                    ReponseHTTPFixture(status, text="indisponible", headers={"Retry-After": "1"}),
                )
                self.assertEqual(code, collecteur.EXIT_HTTP)
                self.assertEqual(post.call_args.kwargs["data"], fixture["payload"])
                tentative = charger_json(fixture["attempt_dir"] / "attempt-receipt.json")
                self.assertEqual(tentative["result"], "FAILED_RETRYABLE")
                self.assertEqual(tentative["cause_code"], cause)
                self.assertFalse(tentative["candidate_artifact_accepted"])
                if status == 429:
                    self.assertEqual(tentative["cost_accounting"], {
                        "status": "known",
                        "cost_microdollars": 0,
                        "reservation_id": f"{fixture['cell']['collection_id']}__a1",
                    })

        fixture = self._preparer_collecte_simulee()
        code, post = self._appeler_collecteur_simule(
            fixture,
            ReponseHTTPFixture(
                200,
                {"error": {"code": 502, "message": "Network connection lost."}},
            ),
        )
        self.assertEqual(code, collecteur.EXIT_API_ERROR)
        self.assertEqual(post.call_count, 1)
        tentative = charger_json(fixture["attempt_dir"] / "attempt-receipt.json")
        self.assertEqual(tentative["result"], "FAILED_RETRYABLE")
        self.assertEqual(tentative["cause_code"], "HTTP_502")
        self.assertEqual(tentative["cost_accounting"]["cost_microdollars"], 0)
        self.assertEqual(
            decision_reprise(tentative, fixture["cell"], fixture["lock_hash"])["action"],
            "retry",
        )

        fixture = self._preparer_collecte_simulee()
        code, post = self._appeler_collecteur_simule(
            fixture, ReponseHTTPFixture(400, text="route fournisseur absente")
        )
        self.assertEqual(code, collecteur.EXIT_HTTP)
        self.assertEqual(post.call_count, 1)
        tentative = charger_json(fixture["attempt_dir"] / "attempt-receipt.json")
        self.assertEqual(tentative["cause_code"], "HTTP_NON_RETRYABLE")
        self.assertEqual(tentative["cost_accounting"], {
            "status": "known",
            "cost_microdollars": 0,
            "reservation_id": f"{fixture['cell']['collection_id']}__a1",
        })

        fixture = self._preparer_collecte_simulee()
        code, post = self._appeler_collecteur_simule(
            fixture, ReponseHTTPFixture(200, text="")
        )
        self.assertEqual(code, collecteur.EXIT_VALIDATION)
        self.assertEqual(post.call_args.kwargs["data"], fixture["payload"])
        tentative = charger_json(fixture["attempt_dir"] / "attempt-receipt.json")
        self.assertEqual(tentative["result"], "FAILED_RETRYABLE")
        self.assertEqual(tentative["cause_code"], "EMPTY_HTTP_BODY")
        self.assertTrue(tentative["http_response_received"])
        self.assertFalse(tentative["candidate_artifact_accepted"])
        self.assertEqual(
            decision_reprise(tentative, fixture["cell"], fixture["lock_hash"])["action"],
            "retry",
        )

        fixture = self._preparer_collecte_simulee()
        code, post = self._appeler_collecteur_simule(
            fixture, collecteur.requests.Timeout("aucune réponse HTTP")
        )
        self.assertEqual(code, collecteur.EXIT_HTTP)
        self.assertEqual(post.call_args.kwargs["data"], fixture["payload"])
        recu_path = fixture["attempt_dir"] / "attempt-receipt.json"
        tentative = charger_json(recu_path)
        self.assertEqual(tentative["cause_code"], "TRANSPORT_NO_HTTP_RESPONSE")
        self.assertFalse(tentative["http_response_received"])
        empreinte_avant = hashlib.sha256(recu_path.read_bytes()).hexdigest()

        tardive = {
            "model": fixture["cell"]["execution_manifest"]["model_requested"],
            "provider": "Example",
            "choices": [{"message": {"content": "trop tard"}, "finish_reason": "stop"}],
            "usage": {"cost": 0.0001},
        }
        code, post = self._appeler_collecteur_simule(
            fixture, ReponseHTTPFixture(200, tardive)
        )
        self.assertEqual(code, collecteur.EXIT_EXISTS)
        post.assert_not_called()
        self.assertEqual(hashlib.sha256(recu_path.read_bytes()).hexdigest(), empreinte_avant)
        self.assertFalse((fixture["attempt_dir"] / "response.md").exists())

    def test_identite_divergente_ne_materialise_aucun_artefact_candidat(self):
        fixture = self._preparer_collecte_simulee()
        payload_reponse = {
            "model": "example/autre-modele",
            "provider": "Example",
            "choices": [{"message": {"content": "sortie étrangère"}, "finish_reason": "stop"}],
            "usage": {"cost": 0.0001},
        }
        code, _post = self._appeler_collecteur_simule(
            fixture, ReponseHTTPFixture(200, payload_reponse)
        )
        self.assertEqual(code, collecteur.EXIT_ROUTE_MISMATCH)
        tentative = charger_json(fixture["attempt_dir"] / "attempt-receipt.json")
        self.assertEqual(tentative["result"], "FAILED_NON_RETRYABLE")
        self.assertEqual(tentative["cause_code"], "ROUTE_MISMATCH")
        self.assertTrue(tentative["http_response_received"])
        self.assertFalse(tentative["candidate_artifact_accepted"])
        self.assertFalse((fixture["attempt_dir"] / "response.md").exists())
        contradictoire = copy.deepcopy(tentative)
        contradictoire["candidate_artifact_accepted"] = True
        with self.assertRaises(ContratV2Invalide):
            valider_recu_tentative(
                contradictoire,
                fixture["cell"],
                fixture["lock_hash"],
            )

    def test_recu_v3_lie_lock_identite_cout_et_octets_candidats(self):
        lock = lock_minimal()
        cellule = lock["collections"][0]
        card = lock["axes"][0]
        collection, score, collection_hash = score_complet(lock, card, cellule)
        lock_hash = empreinte_lock(lock)
        tentative = tentative_complete(lock, cellule, collection)
        valider_chaine_collecte(tentative, collection, lock_hash, cellule)

        autre_lock = copy.deepcopy(tentative)
        autre_lock["campaign_lock_hash"] = "e" * 64
        with self.assertRaises(ContratV2Invalide):
            valider_recu_tentative(autre_lock, cellule, lock_hash)

        autre_provider = copy.deepcopy(collection)
        autre_provider["served"]["provider"] = "autre"
        with self.assertRaises(ContratV2Invalide):
            valider_recu_collecte(autre_provider, lock_hash, cellule)

        borne_minorée = copy.deepcopy(collection)
        borne_minorée["cost_accounting"] = {
            "status": "upper_bound",
            "cost_microdollars": cellule["max_cost_microdollars"] - 1,
            "reservation_id": collection["cost_accounting"]["reservation_id"],
        }
        with self.assertRaises(ContratV2Invalide):
            valider_recu_collecte(borne_minorée, lock_hash, cellule)

        manifeste_ouvert = copy.deepcopy(cellule["execution_manifest"])
        manifeste_ouvert["champ_imprevu"] = True
        with self.assertRaises(ContratV2Invalide):
            valider_manifeste_execution(manifeste_ouvert)

        score_ouvert = copy.deepcopy(score)
        score_ouvert["champ_imprevu"] = True
        with self.assertRaises(ContratV2Invalide):
            valider_recu_score(
                score_ouvert, lock, lock_hash, card, cellule,
                collection, collection_hash,
            )

        for contenu, kind in (("", "empty"), ("refus", "refusal")):
            recu = copy.deepcopy(collection)
            octets = contenu.encode("utf-8")
            recu["candidate"].update({
                "sha256": hashlib.sha256(octets).hexdigest(),
                "bytes": len(octets),
                "kind": kind,
            })
            valider_recu_collecte(recu, lock_hash, cellule)

    def historique_preparateur_local_construit_le_panel_reel_sans_reseau(self):
        registry = tomllib.loads((RACINE / "models.toml").read_text(encoding="utf-8"))
        resolved = {}
        for alias in PANEL_B0:
            resolved[alias] = {
                "metadata_status": "resolved",
                "provider": registry[alias]["provider"],
                "quantization": "fixture",
                "revision": "fixture",
                "criterion_version": "fixture/v1",
                "price_source": "fixture locale",
                "price_observed_at": "2026-08-08T00:00:00+02:00",
                "input_usd_per_million_tokens": "0.1",
                "output_usd_per_million_tokens": "0.2",
                "request_usd": "0",
                "max_tokens": 65_536,
            }
        draft = {
            "schema_version": "benchmark-lab-x/campaign-draft/v2",
            "protocol_version": PROTOCOLE_VERSION,
            "campaign_id": "fixture-preparer-v2",
            "question": "fixture locale",
            "created_at": "2026-08-08T00:00:00+02:00",
            "source_commit": "a" * 40,
            "task_dir": "tasks/dev/pentagone-rotatif",
            "task_file": "task-v3.md",
            "visible_inputs": ["donnees.md"],
            "models_file": "models.toml",
            "candidates": list(PANEL_B0),
            "runs": 6,
            "attempts_max": 3,
            "concurrence": 2,
            "timeout": 600,
            "cap_microdollars": 55_000_000,
            "estimate_microdollars": 31_812_500,
            "resolved": resolved,
        }
        snapshot = {
            "schema_version": "benchmark-lab-x/route-preflight-snapshot/v1",
            "panel": list(PANEL_B0),
            "observed_at": "2026-08-08T00:00:00+02:00",
            "criterion_version": "benchmark-lab-x/selection-route/v2",
            "models_file": "models.toml",
            "models_file_sha256": hashlib.sha256(
                (RACINE / "models.toml").read_bytes()
            ).hexdigest(),
            "resolved": resolved,
            "budget_reestimate": {
                "status": "B0_09_UNCHANGED",
                "approved_estimate_microdollars": 31_812_500,
                "repriced_estimate_microdollars": 31_812_500,
                "approved_cap_microdollars": 55_000_000,
            },
            "b0_09_approval": {
                "schema_version": "benchmark-lab-x/b0-09-approval/v1",
                "decision": "B0_09_REVISED_ESTIMATE_APPROVED",
                "approved_by": "Ayo",
                "approved_at": "2026-08-08T18:30:15+02:00",
                "estimate_microdollars": 31_812_500,
                "cap_microdollars": 55_000_000,
                "source_snapshot_path": "runs/fixture/source.json",
                "source_snapshot_sha256": "a" * 64,
                "scope": "fixture",
            },
        }
        with tempfile.TemporaryDirectory(dir=RACINE) as tmp:
            snapshot_path = Path(tmp) / "routes-preflight.json"
            snapshot_path.write_text(
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            draft["route_snapshot_file"] = snapshot_path.relative_to(RACINE).as_posix()
            draft["route_snapshot_sha256"] = hashlib.sha256(
                snapshot_path.read_bytes()
            ).hexdigest()
            with patch("protocole_v2._verifier_source_depot"):
                lock = construire_lock(draft)
        self.assertEqual(len(lock["collections"]), 114)
        self.assertEqual(lock["budget"]["cap_microdollars"], 55_000_000)
        self.assertEqual(lock["repository_source"], {"commit": "a" * 40})
        self.assertEqual(set(lock["environments"]), {"runner", "measurement"})
        lignes_actifs = lock["score_cards"][0]["verify_manifest"]["assets"]
        self.assertEqual(
            [a["path"] for a in lignes_actifs],
            sorted(a["path"] for a in lignes_actifs),
        )
        actifs = {a["path"] for a in lignes_actifs}
        self.assertIn("tools/qualifier_temoins.py", actifs)
        self.assertIn("tasks/dev/pentagone-rotatif/temoins/provenance.json", actifs)
        self.assertEqual(
            len([p for p in actifs if "/temoins/" in p and p.endswith(".md")]),
            13,
        )

    def historique_preparateur_refuse_le_snapshot_b0_09_en_hold(self):
        snapshot = RACINE / "runs/2026-08-08-pentagone-v3-preflight/routes-preflight.json"
        draft = {
            "route_snapshot_file": snapshot.relative_to(RACINE).as_posix(),
            "route_snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            "estimate_microdollars": 31_812_500,
            "cap_microdollars": 55_000_000,
        }
        with self.assertRaisesRegex(ContratV2Invalide, "B0-09"):
            _charger_snapshot_routes(
                draft,
                list(PANEL_B0),
                RACINE / "models.toml",
            )

    def test_controle_r016_refuse_une_provenance_qualifiante_modifiee(self):
        source = RACINE / "tasks/dev/pentagone-rotatif/temoins"
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task"
            shutil.copytree(source, task_dir / "temoins")
            provenance_path = task_dir / "temoins/provenance.json"
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            nom = provenance["qualification_set"][0]
            provenance["temoins"][nom]["producteur"] = "producteur altéré"
            provenance_path.write_text(
                json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(IntegrationR016Invalide):
                verifier_destination(task_dir)

    def test_agregation_quatrieme_meilleur_sur_six(self):
        niveaux = [9, 3, 8, 7, 1, 6]
        scores = [{"run": i, "etat": "SCORED", "niveau": n}
                  for i, n in enumerate(niveaux, start=1)]
        agrege = agreger_scores("levels", scores)
        self.assertEqual(agrege["niveau_retenu"], 6)
        verdicts = ["PASS", "FAIL", "PASS", "PASS", "FAIL", "PASS"]
        binaire = agreger_scores("binary", [
            {"run": i, "etat": "SCORED", "verdict": v}
            for i, v in enumerate(verdicts, start=1)
        ])
        self.assertEqual(binaire["verdict_retenu"], "PASS")
        self.assertEqual(binaire["pass_count"], 4)

    def test_unknown_bloque_seulement_l_agregat_concerne(self):
        scores = [{"run": i, "etat": "SCORED", "niveau": i} for i in range(1, 7)]
        scores[2] = {"run": 3, "etat": "UNKNOWN", "cause_code": "VERIFY_TIMEOUT"}
        bloque = agreger_scores("levels", scores)
        self.assertFalse(bloque["classement_valide"])
        autres = agreger_scores("levels", [
            {"run": i, "etat": "SCORED", "niveau": i} for i in range(1, 7)
        ])
        self.assertTrue(autres["classement_valide"])

    def test_reprise_fermee(self):
        cellule = lock_minimal()["collections"][0]
        lock_hash = "3" * 64
        base = {
            "schema_version": SCHEMA_ATTEMPT,
            "protocol_version": PROTOCOLE_VERSION,
            "campaign_lock_hash": lock_hash,
            "collection_id": cellule["collection_id"],
            "attempt": 1,
            "result": "FAILED_RETRYABLE",
            "cause_code": "HTTP_429",
            "execution_manifest_hash": cellule["execution_manifest_hash"],
            "payload_hash": cellule["payload_hash"],
            "http_response_received": True,
            "candidate_artifact_accepted": False,
            "cost_accounting": {
                "status": "known",
                "cost_microdollars": 0,
                "reservation_id": f"{cellule['collection_id']}__a1",
            },
            "retry_after": None,
        }
        self.assertEqual(decision_reprise(base, cellule, lock_hash)["action"], "retry")
        inconnu = copy.deepcopy(base)
        inconnu["cost_accounting"]["status"] = "unknown"
        inconnu["cost_accounting"]["cost_microdollars"] = None
        self.assertEqual(decision_reprise(inconnu, cellule, lock_hash)["action"], "hold")
        contenu = copy.deepcopy(base)
        contenu["candidate_artifact_accepted"] = True
        with self.assertRaises(ContratV2Invalide):
            decision_reprise(contenu, cellule, lock_hash)
        autre = copy.deepcopy(base)
        autre["result"] = "FAILED_NON_RETRYABLE"
        autre["cause_code"] = "HTTP_NON_RETRYABLE"
        self.assertEqual(decision_reprise(autre, cellule, lock_hash)["action"], "hold")
        epuise = copy.deepcopy(base)
        epuise["attempt"] = 3
        epuise["cost_accounting"]["reservation_id"] = f"{cellule['collection_id']}__a3"
        self.assertEqual(
            decision_reprise(epuise, cellule, lock_hash)["action"], "infra_error"
        )

    def test_429_suspend_tout_l_alias_sans_bloquer_les_autres(self):
        lock = lock_minimal()
        cellule = lock["collections"][0]
        meme_alias = lock["collections"][1]
        autre_alias = next(
            candidate for candidate in lock["collections"]
            if candidate["alias"] != cellule["alias"]
        )
        cooldowns = {}
        lancer_campagne._enregistrer_cooldown_alias_v2(
            cooldowns,
            cellule,
            {"cause_code": "HTTP_429", "retry_after": "60"},
            100.0,
        )
        self.assertFalse(lancer_campagne._alias_admissible_v2(
            meme_alias, [cellule], cooldowns, 159.9
        ))
        self.assertTrue(lancer_campagne._alias_admissible_v2(
            autre_alias, [cellule], cooldowns, 100.0
        ))
        self.assertTrue(lancer_campagne._alias_admissible_v2(
            meme_alias, [], cooldowns, 160.0
        ))

    def test_corps_vide_epuise_ferme_la_cellule_sans_hold_global(self):
        lock = lock_minimal()
        lock_hash = empreinte_lock(lock)
        cellule = lock["collections"][0]
        reservation = f"{cellule['collection_id']}__a3"
        tentative = {
            "schema_version": SCHEMA_ATTEMPT,
            "protocol_version": PROTOCOLE_VERSION,
            "campaign_lock_hash": lock_hash,
            "collection_id": cellule["collection_id"],
            "attempt": 3,
            "result": "FAILED_RETRYABLE",
            "cause_code": "EMPTY_HTTP_BODY",
            "execution_manifest_hash": cellule["execution_manifest_hash"],
            "payload_hash": cellule["payload_hash"],
            "http_response_received": True,
            "candidate_artifact_accepted": False,
            "cost_accounting": {
                "status": "upper_bound",
                "cost_microdollars": cellule["max_cost_microdollars"],
                "reservation_id": reservation,
            },
            "retry_after": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            campagne = Path(tmp)
            ledger = RegistreBudget(
                campagne / "budget-ledger.json",
                lock["budget"]["cap_microdollars"],
                lock_hash,
            )
            ledger.reserver(reservation, cellule["max_cost_microdollars"])
            arrete, reprise = lancer_campagne._traiter_resultat_v2(
                campagne,
                ledger,
                lock,
                lock_hash,
                cellule,
                {
                    "collection_id": cellule["collection_id"],
                    "attempt": 3,
                    "reservation_id": reservation,
                    "code": collecteur.EXIT_VALIDATION,
                    "receipt": tentative,
                    "error": "",
                },
                False,
            )
            self.assertFalse(arrete)
            self.assertIsNone(reprise)
            self.assertFalse((campagne / "operator-hold.json").exists())
            etat = charger_json(
                campagne / "collections" / cellule["collection_id"]
                / "collection-state.json"
            )
            self.assertEqual(etat["state"], "INFRA_ERROR")
            self.assertEqual(etat["cause_code"], "ATTEMPTS_EXHAUSTED")

    def test_registre_budget_atomique_ne_depasse_pas_le_plafond(self):
        with tempfile.TemporaryDirectory() as tmp:
            registre = RegistreBudget(Path(tmp) / "ledger.json", 100, "c" * 64)
            def reserver(i):
                try:
                    registre.reserver(f"r{i}", 60)
                    return "ok"
                except PlafondDepasse:
                    return "refuse"
            with ThreadPoolExecutor(max_workers=2) as pool:
                resultats = list(pool.map(reserver, (1, 2)))
            self.assertEqual(sorted(resultats), ["ok", "refuse"])
            state = registre.etat()
            self.assertEqual(RegistreBudget._reserve_total(state), 60)
            self.assertFalse(state["hold"])

    def test_registre_budget_refuse_un_etat_valide_json_mais_corrompu(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            registre = RegistreBudget(path, 100, "a" * 64)
            registre.reserver("r1", 60)
            original = json.loads(path.read_text(encoding="utf-8"))
            variantes = []
            negatif = copy.deepcopy(original)
            negatif["engaged_microdollars"] = -1
            variantes.append(negatif)
            statut = copy.deepcopy(original)
            statut["reservations"]["r1"]["status"] = "opaque"
            variantes.append(statut)
            depasse = copy.deepcopy(original)
            depasse["reservations"]["r1"]["max_microdollars"] = 101
            variantes.append(depasse)
            for variante in variantes:
                with self.subTest(variante=variante):
                    path.write_text(json.dumps(variante), encoding="utf-8")
                    with self.assertRaises(ContratV2Invalide):
                        registre.etat()

    def test_ecriture_immuable_concurrente_necrase_jamais(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            def ecrire(valeur):
                try:
                    ecrire_json_immuable(path, {"valeur": valeur})
                    return "ok"
                except ContratV2Invalide:
                    return "refuse"
            with ThreadPoolExecutor(max_workers=2) as pool:
                resultats = list(pool.map(ecrire, (1, 2)))
            self.assertEqual(sorted(resultats), ["ok", "refuse"])
            self.assertIn(json.loads(path.read_text(encoding="utf-8"))["valeur"], {1, 2})

    def test_cout_maximal_est_calcule_depuis_les_prix_figes(self):
        route = {"input_usd_per_million_tokens": "1.25",
                 "output_usd_per_million_tokens": "10", "request_usd": "0.001"}
        self.assertEqual(cout_max_microdollars(route, 100, 1_000), 11_125)

    def test_cout_absent_conserve_le_maximum_et_place_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            registre = RegistreBudget(Path(tmp) / "ledger.json", 100, "d" * 64)
            registre.reserver("r1", 60)
            registre.finaliser("r1", None)
            state = registre.etat()
            self.assertTrue(state["hold"])
            self.assertEqual(RegistreBudget._reserve_total(state), 60)

    def test_hold_operateur_conserve_la_premiere_cause(self):
        lock = lock_minimal()
        lock_hash = empreinte_lock(lock)
        cellule = lock["collections"][0]
        with tempfile.TemporaryDirectory() as tmp:
            campagne = Path(tmp)
            premier = lancer_campagne._poser_hold_v2(
                campagne, lock, lock_hash, "ATTEMPT_RECEIPT_MISSING",
                cellule["collection_id"], 1,
            )
            second = lancer_campagne._poser_hold_v2(
                campagne, lock, lock_hash, "COST_ACCOUNTING_UNKNOWN",
                cellule["collection_id"], 2,
            )
            self.assertEqual(second, premier)
            self.assertEqual(
                json.loads((campagne / "operator-hold.json").read_text(encoding="utf-8")),
                premier,
            )

    def test_erreur_enfant_est_expurgee_et_conserve_le_code_sortie(self):
        erreur = (
            'warning\n"Authorization": "Bearer bearer-secret"\n'
            'OPENROUTER_API_KEY=env-secret\n"api_key": "json-secret"\n'
            'token: plain-secret\nsk-route-secret\n'
            'https://example.invalid/?access_token=query-secret'
        )
        lock = lock_minimal()
        cellule = lock["collections"][0]
        with tempfile.TemporaryDirectory() as tmp:
            campagne = Path(tmp)
            with patch.object(lancer_campagne.subprocess, "run") as run:
                run.return_value.returncode = 2
                run.return_value.stderr = erreur
                resultat = lancer_campagne._collecter_v2(
                    {"transport_timeout_s": 1},
                    campagne,
                    campagne / "campaign.lock.json",
                    campagne / "paid-authorization.json",
                    campagne / "budget-ledger.json",
                    cellule,
                    1,
                )
            diagnostic = lancer_campagne._ecrire_erreur_enfant_v2(
                campagne, "e" * 64, resultat
            )
            expurge = diagnostic["stderr_redacted"]
            for secret in (
                "bearer-secret", "env-secret", "json-secret", "plain-secret",
                "sk-route-secret", "query-secret",
            ):
                self.assertNotIn(secret, expurge)
            self.assertEqual(len(expurge.splitlines()), len(erreur.splitlines()))
            self.assertEqual(diagnostic["child_exit_code"], 2)
            self.assertEqual(set(diagnostic), {
                "schema_version",
                "campaign_lock_hash",
                "collection_id",
                "attempt",
                "child_exit_code",
                "stderr_redacted",
            })

    def test_hold_draine_un_appel_en_vol_sans_programmer_de_reprise(self):
        lock = lock_minimal()
        lock_hash = empreinte_lock(lock)
        cellule = lock["collections"][0]
        reservation_1 = f"{cellule['collection_id']}__a1"
        reservation_2 = f"{cellule['collection_id']}__a2"
        with tempfile.TemporaryDirectory() as tmp:
            campagne = Path(tmp)
            ledger = RegistreBudget(
                campagne / "budget-ledger.json",
                lock["budget"]["cap_microdollars"],
                lock_hash,
            )
            ledger.reserver(reservation_1, cellule["max_cost_microdollars"])
            ledger.reserver(reservation_2, cellule["max_cost_microdollars"])
            futur = Future()
            futur.set_exception(
                RuntimeError('"api_key": "future-secret"\néchec du worker')
            )
            resultat = lancer_campagne._resultat_futur_v2(futur, cellule, 1)
            arrete, reprise = lancer_campagne._traiter_resultat_v2(
                campagne,
                ledger,
                lock,
                lock_hash,
                cellule,
                resultat,
                False,
            )
            self.assertTrue(arrete)
            self.assertIsNone(reprise)
            diagnostic = json.loads((
                campagne / "launcher-failures"
                / f"{cellule['collection_id']}__a1.json"
            ).read_text(encoding="utf-8"))
            self.assertNotIn("future-secret", diagnostic["stderr_redacted"])
            self.assertIn("échec du worker", diagnostic["stderr_redacted"])

            tentative = {
                "schema_version": SCHEMA_ATTEMPT,
                "protocol_version": PROTOCOLE_VERSION,
                "campaign_lock_hash": lock_hash,
                "collection_id": cellule["collection_id"],
                "attempt": 2,
                "result": "FAILED_RETRYABLE",
                "cause_code": "HTTP_429",
                "execution_manifest_hash": cellule["execution_manifest_hash"],
                "payload_hash": cellule["payload_hash"],
                "http_response_received": True,
                "candidate_artifact_accepted": False,
                "cost_accounting": {
                    "status": "known",
                    "cost_microdollars": 1,
                    "reservation_id": reservation_2,
                },
                "retry_after": None,
            }
            arrete, reprise = lancer_campagne._traiter_resultat_v2(
                campagne,
                ledger,
                lock,
                lock_hash,
                cellule,
                {
                    "collection_id": cellule["collection_id"],
                    "attempt": 2,
                    "reservation_id": reservation_2,
                    "code": 0,
                    "receipt": tentative,
                    "error": "",
                },
                arrete,
            )
            state = ledger.etat()
            self.assertTrue(arrete)
            self.assertIsNone(reprise)
            self.assertEqual(state["reservations"][reservation_1]["status"], "unknown")
            self.assertEqual(state["reservations"][reservation_2]["status"], "finalized")
            self.assertEqual(state["engaged_microdollars"], 1)
            self.assertEqual(
                json.loads((campagne / "operator-hold.json").read_text(encoding="utf-8"))[
                    "cause_code"
                ],
                "ATTEMPT_RECEIPT_MISSING",
            )

    def test_autorisation_payante_liee_au_lock_et_au_plafond(self):
        auth = {"schema_version": "benchmark-lab-x/paid-authorization/v1",
                "decision": "GO_PAID_COLLECTION", "campaign_lock_hash": "e" * 64,
                "cap_microdollars": 55_000_000, "approved_by": "Ayo",
                "approved_at": "2026-08-08T00:00:00+02:00"}
        valider_autorisation_payante(auth, "e" * 64, 55_000_000)
        auth["cap_microdollars"] += 1
        with self.assertRaises(ContratV2Invalide):
            valider_autorisation_payante(auth, "e" * 64, 55_000_000)

    def test_recu_r016_reutilisable_entre_lots_identiques(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            lock, lock_hash, receipt = couverture_complete(racine)
            self.assertEqual(
                valider_recu_couverture(receipt, lock, lock_hash, racine),
                (True, []),
            )
            autre_lot = copy.deepcopy(receipt)
            autre_lot["campaign_lock_hash"] = "f" * 64
            self.assertEqual(
                valider_recu_couverture(autre_lot, lock, lock_hash, racine),
                (True, []),
            )
            autre_prompt = copy.deepcopy(receipt)
            autre_prompt["prompt_sha256"] = "f" * 64
            ok, motifs = valider_recu_couverture(
                autre_prompt, lock, lock_hash, racine
            )
            self.assertFalse(ok)
            self.assertIn("reçu R-016 lié à un autre prompt", motifs)
            non_aveugle = copy.deepcopy(receipt)
            non_aveugle["witnesses"]["positif.md"]["access_to_verifier"] = True
            non_aveugle["qualified"] = False
            ok, motifs = valider_recu_couverture(non_aveugle, lock, lock_hash, racine)
            self.assertFalse(ok)
            self.assertTrue(any("provenance verrouillée" in motif for motif in motifs))

    def test_recu_r016_reste_valide_apres_ecriture_canonique(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            lock, lock_hash, receipt = couverture_complete(racine)
            card = next(c for c in lock["axes"] if c["kind"] == "levels")
            observation = receipt["observations"]["positif.md"][card["id"]]
            observation["measurements"] = {"absolute_error": "8.465638021306306e-05"}
            path = racine / "receipt.json"
            ecrire_json_immuable(path, receipt)
            relu = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(empreinte(relu), empreinte(receipt))
            self.assertEqual(
                valider_recu_couverture(relu, lock, lock_hash, racine),
                (True, []),
            )

    def test_recu_r016_refuse_une_mesure_flottante_non_canonique(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            lock, lock_hash, receipt = couverture_complete(racine)
            card = next(c for c in lock["axes"] if c["kind"] == "levels")
            receipt["observations"]["positif.md"][card["id"]]["measurements"] = {
                "absolute_error": 8.465638021306306e-05,
            }
            ok, motifs = valider_recu_couverture(receipt, lock, lock_hash, racine)
            self.assertFalse(ok)
            self.assertTrue(any("reçu R-016 non canonique" in motif for motif in motifs))

    def test_resultat_carte_accepte_lordre_json_des_cles(self):
        card = lock_minimal()["axes"][2]
        resultat = _resultat_carte(card, True)
        resultat["predicates"] = dict(sorted(resultat["predicates"].items()))
        valider_resultat_carte(
            resultat, card,
            champ_predicats="predicates", champ_mesures="measurements",
        )

    def test_qualifier_preserve_les_decimales_comme_texte(self):
        class Processus:
            returncode = 0

            def communicate(self, timeout=None):
                return (
                    '{"etat":"SCORED","measurements":{"absolute_error":8.465638021306306e-05}}',
                    "",
                )

        with tempfile.TemporaryDirectory() as tmp:
            temoin = Path(tmp) / "temoin.md"
            temoin.write_text("témoin\n", encoding="utf-8")
            with patch("qualifier_temoins.subprocess.Popen", return_value=Processus()):
                resultat = noter_v5(temoin, Path("verify.py"), "carte", delai=1)
        self.assertEqual(
            resultat["measurements"]["absolute_error"],
            "8.465638021306306e-05",
        )

    def test_qualifier_conserve_le_diagnostic_processus_expurge(self):
        erreur = (
            'début\n"Authorization": "Bearer bearer-secret"\n'
            'Authorization: Basic basic-secret\n'
            'Authorization: Token token-scheme-secret\n'
            'Authorization:\ntrace utile\n'
            'OPENROUTER_API_KEY=env-secret\n"api_key": "json-secret"\n'
            'token: plain-secret\nsk-route-secret\n'
            'https://example.invalid/?access_token=query-secret\nfin'
        )

        class Processus:
            returncode = 23

            def communicate(self, timeout=None):
                return "", erreur

        with tempfile.TemporaryDirectory() as tmp:
            temoin = Path(tmp) / "temoin.md"
            temoin.write_text("témoin\n", encoding="utf-8")
            with patch("qualifier_temoins.subprocess.Popen", return_value=Processus()):
                resultat = noter_v5(temoin, Path("verify.py"), "carte", delai=1)
        diagnostic = resultat["process_diagnostic"]
        self.assertEqual(diagnostic["failure_stage"], "exit")
        self.assertEqual(diagnostic["verifier_exit_code"], 23)
        self.assertEqual(
            diagnostic["stderr_redacted"],
            expurger_diagnostic_processus(erreur),
        )
        self.assertNotIn("trace utile", diagnostic["stderr_redacted"])
        for secret in (
            "bearer-secret", "basic-secret", "token-scheme-secret", "env-secret",
            "json-secret", "plain-secret", "sk-route-secret", "query-secret",
            "début", "fin",
        ):
            self.assertNotIn(secret, diagnostic["stderr_redacted"])

    def test_diagnostic_r016_est_lie_a_une_erreur_de_verification(self):
        observation = {
            "etat": "UNKNOWN",
            "cause_code": "VERIFY_PROCESS_ERROR",
            "process_diagnostic": {
                "failure_stage": "exit",
                "verifier_exit_code": -5,
                "stderr_redacted": "échec expurgé",
            },
        }
        valider_diagnostic_processus_r016(observation)
        observation["etat"] = "SCORED"
        with self.assertRaises(ContratV2Invalide):
            valider_diagnostic_processus_r016(observation)
        observation["etat"] = "UNKNOWN"
        observation["process_diagnostic"]["verifier_exit_code"] = 0
        with self.assertRaises(ContratV2Invalide):
            valider_diagnostic_processus_r016(observation)
        observation["process_diagnostic"].update({
            "failure_stage": "output",
            "verifier_exit_code": 7,
        })
        with self.assertRaises(ContratV2Invalide):
            valider_diagnostic_processus_r016(observation)
        observation.update({
            "cause_code": "VERIFY_TIMEOUT",
            "process_diagnostic": {
                "failure_stage": "timeout",
                "verifier_exit_code": -9,
                "stderr_redacted": "",
            },
        })
        valider_diagnostic_processus_r016(observation)
        observation["process_diagnostic"]["verifier_exit_code"] = 0
        with self.assertRaises(ContratV2Invalide):
            valider_diagnostic_processus_r016(observation)
        observation["process_diagnostic"]["verifier_exit_code"] = None
        with self.assertRaises(ContratV2Invalide):
            valider_diagnostic_processus_r016(observation)

    def test_recu_r016_recalcule_la_couverture_depuis_les_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            lock, lock_hash, receipt = couverture_complete(racine)
            card = lock["axes"][0]
            predicat = card["predicates"][0]
            receipt["cards"][card["id"]]["predicates"][predicat]["positive"] = [
                "negatif.md"
            ]
            ok, motifs = valider_recu_couverture(receipt, lock, lock_hash, racine)
            self.assertFalse(ok)
            self.assertTrue(any("différente des observations" in motif for motif in motifs))

    def test_recu_score_recalcule_niveau_et_contexte(self):
        lock = lock_minimal()
        cellule = lock["collections"][0]
        card = lock["axes"][2]
        collection, score, collection_hash = score_complet(lock, card, cellule)
        lock_hash = empreinte_lock(lock)
        self.assertIs(
            valider_recu_score(
                score, lock, lock_hash, card, cellule, collection, collection_hash
            ),
            score,
        )
        niveau_faux = copy.deepcopy(score)
        niveau_faux["niveau"] -= 1
        with self.assertRaises(ContratV2Invalide):
            valider_recu_score(
                niveau_faux, lock, lock_hash, card, cellule, collection, collection_hash
            )
        contexte_faux = copy.deepcopy(score)
        contexte_faux["measurement_context"]["prompt_sha256"] = "f" * 64
        contexte_faux["measurement_context_hash"] = empreinte(
            contexte_faux["measurement_context"]
        )
        with self.assertRaises(ContratV2Invalide):
            valider_recu_score(
                contexte_faux, lock, lock_hash, card, cellule, collection, collection_hash
            )

    def test_rapport_v2_refuse_un_score_semantiquement_altere(self):
        lock = lock_minimal()
        cellule = lock["collections"][0]
        card = lock["axes"][2]
        collection, score, collection_hash = score_complet(lock, card, cellule)
        score["niveau"] -= 1
        with tempfile.TemporaryDirectory() as tmp:
            campagne = Path(tmp)
            tentative = campagne / "collections" / cellule["collection_id"] / "attempt-1"
            tentative.mkdir(parents=True)
            (tentative / "response.md").write_text("response\n", encoding="utf-8")
            (tentative / "collection-receipt.json").write_text(
                json.dumps(collection), encoding="utf-8"
            )
            (tentative / "attempt-receipt.json").write_text(
                json.dumps(tentative_complete(lock, cellule, collection)),
                encoding="utf-8",
            )
            (tentative / "raw.json").write_text("{}\n", encoding="utf-8")
            (tentative / "COMPLETE").write_text("ok\n", encoding="utf-8")
            score_path = (
                campagne / "scores" / collection_hash / card["id"]
                / f"{card['verify_hash']}.json"
            )
            score_path.parent.mkdir(parents=True)
            score_path.write_text(json.dumps(score), encoding="utf-8")
            with self.assertRaisesRegex(ContratV2Invalide, "niveau"):
                rapport_campagne._score_v2(
                    campagne, lock, empreinte_lock(lock), card,
                    cellule["alias"], cellule["run"],
                )

    def test_abandon_humain_ferme_seulement_la_collecte_en_hold_identite(self):
        lock = lock_minimal()
        lock_hash = empreinte_lock(lock)
        cellule = lock["collections"][0]
        with tempfile.TemporaryDirectory() as tmp:
            campagne = Path(tmp)
            lancer_campagne._poser_hold_v2(
                campagne, lock, lock_hash, "ROUTE_MISMATCH",
                cellule["collection_id"], 1,
            )
            abandon_path = campagne / "abandonment.json"
            abandon_path.write_text(json.dumps({
                "schema_version": SCHEMA_ABANDONMENT,
                "campaign_lock_hash": lock_hash,
                "decision": "ABANDON_HELD_COLLECTION",
                "collection_id": cellule["collection_id"],
                "cause_code": "ABANDONED_AFTER_IDENTITY_MISMATCH",
                "decided_at": "2026-08-08T00:00:00+02:00",
                "approved_by": "Ayo",
            }), encoding="utf-8")
            resultat = lancer_campagne._abandonner_hold_v2(
                campagne, lock, lock_hash, abandon_path
            )
            self.assertEqual(resultat["state"], "INFRA_ERROR")
            etat = json.loads((
                campagne / "collections" / cellule["collection_id"]
                / "collection-state.json"
            ).read_text(encoding="utf-8"))
            valider_etat_collecte(etat, lock_hash, cellule)
            self.assertTrue((campagne / "operator-hold.json").is_file())
            self.assertEqual(empreinte_lock(lock), lock_hash)

    def test_retrait_panel_est_date_motive_et_ne_modifie_pas_le_lock(self):
        lock = lock_minimal()
        lock_hash = empreinte_lock(lock)
        objet = {
            "schema_version": SCHEMA_PANEL_EVENTS,
            "campaign_lock_hash": lock_hash,
            "events": [{
                "event": "RETIRE",
                "alias": lock["panel"][0],
                "reason": "configuration retirée du panel",
                "decided_at": "2026-08-08T00:00:00+02:00",
                "decision_basis": "independent_of_measured_result",
            }],
        }
        valider_evenements_panel(objet, lock, lock_hash)
        self.assertEqual(empreinte_lock(lock), lock_hash)
        non_motive = copy.deepcopy(objet)
        non_motive["events"][0]["reason"] = ""
        with self.assertRaises(ContratV2Invalide):
            valider_evenements_panel(non_motive, lock, lock_hash)

    def test_rapport_v3_peut_meler_axes_valides_et_provisoires(self):
        lock = lock_minimal()
        lock_hash = empreinte_lock(lock)
        axe_valide = lock["axes"][0]
        axe_provisoire = lock["axes"][1]

        def contexte(axis_id: str) -> str:
            return hashlib.sha256(axis_id.encode("utf-8")).hexdigest()

        def collection_hash(alias: str, run: int) -> str:
            return hashlib.sha256(f"{alias}:{run}".encode("utf-8")).hexdigest()

        def score_fixture(_campagne, _lock, _hash, card, alias, run):
            if card["id"] == axe_provisoire["id"] and alias == lock["panel"][0] and run == 1:
                return {
                    "alias": alias,
                    "run": run,
                    "etat": "UNKNOWN",
                    "cause_code": "VERIFY_TIMEOUT",
                    "measurement_context_hash": contexte(card["id"]),
                    "collection_receipt_hash": collection_hash(alias, run),
                }
            resultat = {
                "alias": alias,
                "run": run,
                "etat": "SCORED",
                "cause_code": None,
                "measurement_context_hash": contexte(card["id"]),
                "collection_receipt_hash": collection_hash(alias, run),
            }
            if card["kind"] == "binary":
                resultat["verdict"] = "PASS"
            else:
                resultat["niveau"] = len(card["predicates"])
            return resultat

        scores_audites = [
            score_fixture(None, None, None, axe_valide, alias, run)
            for alias in lock["panel"] for run in range(1, 7)
        ]
        results_audit = {
            "schema_version": "benchmark-lab-x/results-data/v3",
            "campaign_lock_hash": lock_hash,
            "axes": [{
                "id": axe_valide["id"],
                "measurement_context_hash": contexte(axe_valide["id"]),
                "candidats": [{"scores": scores_audites}],
            }],
        }
        review = {
            "schema_version": "benchmark-lab-x/axis-audit-review/v1",
            "selection_method": axe_valide["audit_plan"]["blind_selection_method"],
            "identity_blinded": True,
            "score_changes": 0,
            "sample": [{
                "collection_receipt_hash": scores_audites[0]["collection_receipt_hash"],
                "code_result_correct": True,
            }],
            "completed_at": "2026-08-08T00:00:00+02:00",
            "auditor_role": "propriétaire du benchmark",
            "conclusion": axe_valide["audit_plan"]["allowed_conclusion"],
        }
        audit_receipt = audit_instrument.construire_recu(
            lock, results_audit, axe_valide["id"], review
        )
        valider_recu_audit(
            audit_receipt, lock_hash, axe_valide,
            contexte(axe_valide["id"]),
            {score["collection_receipt_hash"] for score in scores_audites},
        )

        with tempfile.TemporaryDirectory() as tmp:
            campagne = Path(tmp)
            (campagne / "campaign.lock.json").write_text("{}\n", encoding="utf-8")
            (campagne / "witness-coverage-receipt.json").write_text(
                "{}\n", encoding="utf-8"
            )
            audit_path = (
                campagne / "audits" / axe_valide["id"]
                / f"{axe_valide['verify_hash']}.json"
            )
            audit_path.parent.mkdir(parents=True)
            audit_path.write_text(json.dumps(audit_receipt), encoding="utf-8")
            conf = {
                "protocol_version": PROTOCOLE_VERSION,
                "campaign_lock": "campaign.lock.json",
            }
            with patch.object(rapport_campagne, "valider_lock", return_value=lock), \
                 patch.object(rapport_campagne, "valider_recu_couverture", return_value=(True, [])), \
                 patch.object(rapport_campagne, "_score_v2", side_effect=score_fixture), \
                 patch.object(rapport_campagne.subprocess, "Popen",
                              side_effect=AssertionError("processus interdit")), \
                 contextlib.redirect_stdout(io.StringIO()) as sortie:
                code = rapport_campagne.rapport_v2(campagne, conf)
            self.assertEqual(code, 0)
            resultat = json.loads(sortie.getvalue())
            statuts = {axe["id"]: axe["statut"] for axe in resultat["axes"]}
            self.assertEqual(statuts[axe_valide["id"]], "valide")
            self.assertEqual(statuts[axe_provisoire["id"]], "provisoire")
            self.assertFalse(resultat["conformite"]["page_validee"])
            page = page_resultats.page_v3(resultat, {}, Path("fixture.json"))
            self.assertIn("Statut : valide", page)
            self.assertIn("Statut : provisoire", page)

    def test_traversee_refusee(self):
        for chemin in ("../secret", "/tmp/secret", "a/./b"):
            with self.assertRaises(ContratV2Invalide):
                chemin_relatif_sur(chemin, "fixture")

    def test_rapport_v2_ne_lance_ni_processus_ni_chromium(self):
        lock = lock_minimal()
        with tempfile.TemporaryDirectory() as tmp:
            campagne = Path(tmp)
            (campagne / "campaign.lock.json").write_text("{}\n", encoding="utf-8")
            conf = {"protocol_version": PROTOCOLE_VERSION,
                    "campaign_lock": "campaign.lock.json"}
            with patch.object(rapport_campagne, "valider_lock", return_value=lock), \
                 patch.object(rapport_campagne, "empreinte_lock", return_value="9" * 64), \
                 patch.object(rapport_campagne.subprocess, "Popen",
                              side_effect=AssertionError("processus interdit")), \
                 contextlib.redirect_stdout(io.StringIO()) as sortie:
                code = rapport_campagne.rapport_v2(campagne, conf)
            self.assertEqual(code, 0)
            resultat = json.loads(sortie.getvalue())
            self.assertFalse(resultat["conformite"]["page_validee"])
            self.assertEqual(len(resultat["axes"]), 5)


if __name__ == "__main__":
    unittest.main()
