# /// script
# requires-python = ">=3.12"
# dependencies = ["requests"]
# ///
"""Collecteur d'un appel direct, selon R-004.

Charge les consignes et entrées d'une carte, effectue un appel OpenRouter sur
un provider épinglé, écrit la réponse et son reçu, puis s'arrête. Il ne note
pas, ne boucle pas et ne relance pas.

Codes de sortie stables :
    0  succès
    1  arguments invalides
    2  clé API absente
    3  dossier de carte invalide
    4  assemblage du prompt impossible
    5  erreur HTTP ou transport
    6  erreur renvoyée par l'API
    7  réponse structurellement invalide
    8  modèle ou provider servi différent du pin
    9  dossier de run déjà présent
   10  erreur d'entrée-sortie après réservation
   11  route non conforme au pré-vol, INELIGIBLE sans appel (R-004, R-025)
   12  métadonnées de route inatteignables, INFRA_ERROR sans appel (R-013)

Usage:
    uv run tools/collect.py tasks/dev/<slug> \
        --model openai/gpt-5.6-sol --provider openai --run 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, NoReturn

import requests

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_lab_x.model_catalog import require_current
from empreintes import empreinte  # noqa: E402
from protocole_v2 import (  # noqa: E402
    CAUSES_REPRISE,
    PROTOCOLE_VERSION as PROTOCOLE_V2,
    SCHEMA_ATTEMPT,
    SCHEMA_COLLECTION,
    SCHEMA_EXECUTION_ROUTE_PROGRAM,
    ContratV2Invalide,
    RegistreBudget,
    assembler_prompt_verrouille,
    cellule_du_lock,
    charger_json,
    construire_payload,
    descripteur_environnement_runner,
    empreinte_lock,
    resultat_acquis_v2,
    valider_autorisation_payante,
    valider_chaine_collecte,
    valider_environnement_observe,
    valider_lock,
    valider_recu_collecte,
    valider_recu_tentative,
)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
# Judge-side files never sent to the model
EXCLUDED = {"task.md", "verify.md", "prompt.txt"}
# Files matching these prefixes are also never sent
EXCLUDED_PREFIXES = ("anchor-",)

# Backend réellement traversé. Constante aujourd'hui, mais consignée : R-003
# distingue deux backends comme deux candidats, ce qui suppose que le reçu
# nomme celui qui a servi au lieu de le laisser implicite dans le code
BACKEND = "openrouter"
# Le protocole désigne l'ensemble indissociable collecte + notation (ARD §2.2) :
# tout changement de l'une des deux parties l'incrémente
PROTOCOLE_VERSION = "benchmark-lab-x/protocol/v1"
COLLECTEUR_VERSION = "collect.py/v3"
SCHEMA_MANIFESTE = "benchmark-lab-x/execution-manifest/v1"

# Stable exit codes
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_KEY = 2
EXIT_BAD_TASK = 3
EXIT_PROMPT = 4
EXIT_HTTP = 5
EXIT_API_ERROR = 6
EXIT_VALIDATION = 7
EXIT_ROUTE_MISMATCH = 8
EXIT_EXISTS = 9
EXIT_IO = 10
EXIT_INELIGIBLE = 11
EXIT_INFRA = 12


def die(code: int, msg: str) -> NoReturn:
    print(f"erreur : {msg}", file=sys.stderr)
    raise SystemExit(code)


def load_dotenv_key() -> str | None:
    """Read OPENROUTER_API_KEY from a repo-local .env (gitignored), if any"""
    env_file = Path(".env")
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("OPENROUTER_API_KEY="):
            value = line.split("=", 1)[1].strip().strip("'\"")
            return value or None
    return None


def preflight_key() -> str:
    """Require a key; repo-local .env (dedicated campaign key) wins over the
    shell environment. Never print its value"""
    key = load_dotenv_key()
    if key:
        print("source de la clé : .env local au dépôt et ignoré par Git", file=sys.stderr)
        return key
    key = os.environ.get("OPENROUTER_API_KEY")
    if key is None or not str(key).strip():
        die(
            EXIT_NO_KEY,
            "OPENROUTER_API_KEY absente de l’environnement et du fichier .env local",
        )
    print("source de la clé : variable d’environnement", file=sys.stderr)
    return str(key).strip()


def is_excluded(name: str) -> bool:
    if name in EXCLUDED:
        return True
    return any(name.startswith(p) for p in EXCLUDED_PREFIXES)


def assemble_prompt(task_dir: Path) -> tuple[str, dict[str, str], list[str]]:
    """New calls require the existing explicit instructions/input manifest"""
    die(EXIT_PROMPT, "Manifeste explicite requis : utiliser le lock et ses rôles instructions/input ; aucune découverte de fichiers")


def _redact_tokens(text: str) -> str:
    redacted = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+",
        r"\1[REDACTED]",
        text,
    )
    redacted = re.sub(
        r"(?i)(api[_-]?key\s*[:=]\s*)\S+",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(r"\bsk-[a-zA-Z0-9_-]{8,}\b", "[REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)((?:\\*[\"'])?(?:request_id|user_id)(?:\\*[\"'])?"
        r"\s*:\s*(?:\\*[\"'])?)([^\s,\"'\\}\]]+)",
        lambda match: f"{match.group(1)}[REDACTED]",
        redacted,
    )
    return redacted


def _redact_diagnostic_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if key.lower() in {"request_id", "user_id"}
                else _redact_diagnostic_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_diagnostic_value(item) for item in value]
    if not isinstance(value, str):
        return value
    redacted = _redact_tokens(value)
    stripped = redacted.strip()
    if not stripped.startswith(("{", "[")):
        return redacted
    try:
        nested = json.loads(stripped)
    except json.JSONDecodeError:
        return redacted
    if not isinstance(nested, (dict, list)):
        return redacted
    return json.dumps(
        _redact_diagnostic_value(nested),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def redact_http_body(text: str) -> str:
    """Expurger secrets et identifiants techniques avant écriture"""
    redacted = _redact_tokens(text)
    try:
        parsed = json.loads(redacted)
    except json.JSONDecodeError:
        return redacted
    return json.dumps(
        _redact_diagnostic_value(parsed),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def normalize_cost_usd(usage: Any) -> float | None:
    """Coût réel de l'appel, en dollars.

    `cost` vaut 0 sur les routes facturées en direct par le fournisseur plutôt
    qu'en crédits OpenRouter (`is_byok`). Le coût réel est alors dans
    `cost_details.upstream_inference_cost`, et retenir le zéro fausserait le
    diagnostic de coût publié au titre de R-021. Observé le 2026-08-05 sur la
    route de premier rang de DeepSeek : `cost = 0` pour 0,0176 $ réellement dus
    """
    if not isinstance(usage, dict):
        return None
    facture = None
    for key in ("cost", "total_cost", "cost_usd"):
        val = usage.get(key)
        if val is None:
            continue
        try:
            facture = float(val)
            break
        except (TypeError, ValueError):
            continue
    amont = None
    details = usage.get("cost_details")
    if isinstance(details, dict):
        try:
            amont = float(details.get("upstream_inference_cost"))
        except (TypeError, ValueError):
            amont = None
    if facture is not None and facture > 0:
        return facture
    if amont is not None and amont > 0:
        return amont
    return facture


def extract_provider_served(data: dict[str, Any]) -> str | None:
    """Best-effort provider id from a chat-completions payload"""
    raw = data.get("provider")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("slug", "id", "name"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def validate_response(data: Any) -> tuple[str, dict[str, Any], str]:
    """Adapter une réponse OpenRouter en octets candidats matérialisables"""
    if not isinstance(data, dict):
        die(EXIT_VALIDATION, "la réponse n’est pas un objet JSON")
    if "error" in data and data["error"]:
        # Caller should have branched earlier; treat as validation miss
        die(EXIT_API_ERROR, "la réponse contient un objet error")
    choices = data.get("choices")
    if not isinstance(choices, list) or len(choices) == 0:
        die(EXIT_VALIDATION, "choices est absent ou vide")
    first = choices[0]
    if not isinstance(first, dict):
        die(EXIT_VALIDATION, "choices[0] n’est pas un objet")
    message = first.get("message")
    if not isinstance(message, dict):
        die(EXIT_VALIDATION, "choices[0].message est absent ou n’est pas un objet")
    if first.get("error"):
        die(
            EXIT_API_ERROR,
            "choices[0] contient un objet error, échec masqué par un HTTP 200",
        )
    if first.get("finish_reason") == "error":
        die(EXIT_API_ERROR, "finish_reason=error, échec masqué par un HTTP 200")
    content = message.get("content")
    refusal = message.get("refusal")
    if isinstance(content, str) and content:
        return content, first, "content"
    if isinstance(refusal, str) and refusal:
        return refusal, first, "refusal"
    if isinstance(content, str):
        return content, first, "empty"
    if content is None and refusal is None:
        die(EXIT_VALIDATION, "aucune séquence candidate matérialisable dans message")
    die(EXIT_VALIDATION, "content ou refusal n'est pas une chaîne")


def atomic_write_text(path: Path, text: str) -> None:
    """Write via sibling tmp + os.replace"""
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


# Request summary appended to every FAILED receipt so failed runs keep their
# parameters (timeout, reasoning cap, hashes); set once in main after the
# request body is final, empty before that point
REQUEST_NOTE: str = ""
V2_ATTEMPT_CONTEXT: dict[str, Any] | None = None


def _cout_microdollars(cost_usd: float | None) -> int | None:
    if cost_usd is None:
        return None
    try:
        valeur = Decimal(str(cost_usd)) * Decimal(1_000_000)
    except (InvalidOperation, ValueError):
        return None
    if valeur < 0:
        return None
    return math.ceil(valeur)


def _compteur_jetons(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _recu_tentative(
    reason: str, result: str = "FAILED", cost_usd: float | None = None
) -> dict[str, Any] | None:
    if V2_ATTEMPT_CONTEXT is None:
        return None
    cause = {
        "http_transport": "TRANSPORT_NO_HTTP_RESPONSE",
        "http_429": "HTTP_429",
        "http_502": "HTTP_502",
        "http_503": "HTTP_503",
        "lock_parameter_mismatch": "LOCK_PARAMETER_MISMATCH",
        "lock_payload_mismatch": "LOCK_PAYLOAD_MISMATCH",
        "empty_body": "EMPTY_HTTP_BODY",
        "invalid_json": "INVALID_JSON",
        "api_error": "API_ERROR",
        "validation": "RESPONSE_SCHEMA_INVALID",
        "route_mismatch": "ROUTE_MISMATCH",
        "lock_manifest_mismatch": "LOCK_MANIFEST_MISMATCH",
        "lock_prompt_mismatch": "LOCK_PROMPT_MISMATCH",
        "key_leak_guard": "KEY_LEAK_GUARD",
        "write_failed": "WRITE_FAILED",
    }.get(reason)
    if cause is None and reason.startswith("http_redirect_"):
        cause = "HTTP_REDIRECT"
    if cause is None and reason.startswith("http_"):
        cause = "HTTP_NON_RETRYABLE"
    if cause is None:
        cause = "UNEXPECTED_ERROR"
    cout = _cout_microdollars(cost_usd)
    if result == "COMPLETE":
        etat = "COMPLETE"
    elif cause in CAUSES_REPRISE:
        etat = "FAILED_RETRYABLE"
    else:
        etat = "FAILED_NON_RETRYABLE"
    http_recu = bool(V2_ATTEMPT_CONTEXT.get("http_response_received"))
    artefact_accepte = bool(V2_ATTEMPT_CONTEXT.get("candidate_artifact_accepted"))
    if (
        cause in {"HTTP_429", "HTTP_502", "HTTP_NON_RETRYABLE", "API_ERROR"}
        and http_recu
        and not artefact_accepte
    ):
        statut_cout = "known"
        cout = 0
    else:
        statut_cout = "known" if cout is not None else "upper_bound"
        if cout is None:
            cout = V2_ATTEMPT_CONTEXT["max_cost_microdollars"]
    receipt = {
        "schema_version": SCHEMA_ATTEMPT,
        "protocol_version": PROTOCOLE_V2,
        "campaign_lock_hash": V2_ATTEMPT_CONTEXT["campaign_lock_hash"],
        "collection_id": V2_ATTEMPT_CONTEXT["collection_id"],
        "attempt": V2_ATTEMPT_CONTEXT["attempt"],
        "result": etat,
        "cause_code": None if etat == "COMPLETE" else cause,
        "execution_manifest_hash": V2_ATTEMPT_CONTEXT["execution_manifest_hash"],
        "payload_hash": V2_ATTEMPT_CONTEXT["payload_hash"],
        "http_response_received": http_recu,
        "candidate_artifact_accepted": artefact_accepte,
        "cost_accounting": {
            "status": statut_cout,
            "cost_microdollars": cout,
            "reservation_id": V2_ATTEMPT_CONTEXT["reservation_id"],
        },
        "retry_after": V2_ATTEMPT_CONTEXT.get("retry_after"),
    }
    valider_recu_tentative(
        receipt,
        V2_ATTEMPT_CONTEXT["cell"],
        V2_ATTEMPT_CONTEXT["campaign_lock_hash"],
    )
    return receipt


def mark_failed(out: Path, reason: str, detail: str | None = None) -> None:
    """Leave a single FAILED marker file carrying reason and redacted receipt.

    One file, not a sibling directory: on case-insensitive filesystems (APFS)
    a 'failed/' directory would collide with the 'FAILED' marker
    """
    try:
        body = reason + "\n"
        if detail is not None:
            body += "\n" + redact_http_body(detail) + "\n"
        if REQUEST_NOTE:
            body += "\nrequest: " + REQUEST_NOTE + "\n"
        (out / "FAILED").write_text(body, encoding="utf-8")
        recu = _recu_tentative(reason)
        if recu is not None:
            atomic_write_text(
                out / "attempt-receipt.json",
                json.dumps(recu, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
    except OSError as exc:
        print(f"avertissement : reçu FAILED impossible à écrire : {exc}", file=sys.stderr)


def cleanup_or_fail(out: Path, reason: str, detail: str | None = None) -> None:
    """On post-reservation failure: mark FAILED (keep dir for diagnosis)"""
    mark_failed(out, reason, detail)


def marquer_etat(out: Path, etat: str, motif: str, detail: dict[str, Any]) -> None:
    """Poser un marqueur d'état terminal R-013, lisible par machine.

    `FAILED` reste la preuve brute d'un appel qui a mal tourné ; il ne dit pas
    dans quel état R-013 termine le run attendu. `INELIGIBLE` et `INFRA_ERROR`
    sont ces états, et ils se distinguent : le premier signifie que la route
    était non conforme avant toute tentative et vaut pour tous les runs du
    couple carte-configuration, le second qu'aucune tentative autorisée n'a
    abouti. Le corps est du JSON pour que l'agrégateur n'ait rien à deviner
    """
    corps = {"etat": etat, "motif": motif, "date": datetime.now(timezone.utc).isoformat()}
    corps.update(detail)
    try:
        (out / etat).write_text(
            json.dumps(corps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"avertissement : reçu {etat} impossible à écrire : {exc}", file=sys.stderr)


def regime_de_la_carte(task_md: Path) -> str | None:
    """Régime de confidentialité déclaré par la carte, s'il l'est.

    Le régime est une propriété de la carte, pas de la ligne de commande : une
    carte du jeu retenu ne doit pas pouvoir être collectée sous un régime
    permissif par oubli d'un drapeau. La ligne de commande ne peut que durcir
    """
    try:
        texte = task_md.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^-\s*\*\*Régime de confidentialité\*\*\s*:\s*(\w+)", texte, re.M)
    if not m:
        return None
    valeur = m.group(1).lower()
    return {"exposé": "expose", "expose": "expose", "retenu": "retenu"}.get(valeur)


def version_de_la_carte(task_md: Path) -> str | None:
    """Étiquette `task-vN` déclarée par la carte (R-015).

    Le reçu de collecte doit la conserver : sans elle, deux campagnes séparées
    par une réécriture des consignes se comparent sans que rien ne le signale
    """
    try:
        texte = task_md.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"\btask-v(\d+)\b", texte)
    return f"task-v{m.group(1)}" if m else None


# Le budget de sortie doit laisser de la place au prompt : sur certaines routes
# `max_completion_tokens` égale `context_length`, et demander tout le budget en
# sortie fait dépasser le contexte total. Constaté le 2026-08-05 sur tencent/hy3,
# qui déclare 262144 pour les deux : quatre runs perdus
MARGE_PROMPT = 8192


def _endpoint_epingle(endpoints: list[dict[str, Any]], provider: str) -> dict[str, Any] | None:
    """Retrouver l'endpoint réellement épinglé parmi ceux du modèle.

    Le pin de `models.toml` est un slug (`modal`, `xai`) ; l'API rend un nom
    d'affichage (`Modal`, `xAI`) et une étiquette éventuellement suffixée par
    la quantification (`modal/mxfp4`). Les trois formes sont rapprochées
    """
    cible = re.sub(r"[\s_]+", "-", provider.strip().lower())
    for e in endpoints:
        noms = {
            re.sub(r"[\s_]+", "-", str(e.get("provider_name") or "").strip().lower()),
            str(e.get("tag") or "").strip().lower(),
            str(e.get("tag") or "").strip().lower().split("/")[0],
        }
        if cible in noms:
            return e
    return None


def resoudre_budget(
    model: str, provider: str, plafond: int, tentatives: int, pause: float = 3.0
) -> dict[str, Any]:
    """Budget de sortie résolu avant l'appel depuis ce que la route déclare (R-025).

    Rend un verdict, jamais un nombre nu, parce que trois issues sont possibles
    et qu'une seule autorise l'appel :

    - `ok` : la route déclare une limite exploitable, bornée par le plafond
    - `ineligible` : la route n'existe pas ou ne déclare aucune limite exploitable
    - `infra` : les métadonnées de route sont restées inatteignables après les
      tentatives autorisées. Retomber sur une valeur locale reviendrait à inventer un
      nombre sans source, ce que R-025 interdit tout autant

    La résolution porte sur l'endpoint épinglé et sur lui seul. Prendre le
    maximum sur tous les endpoints, comme le faisait la version précédente,
    pouvait tirer le budget d'un fournisseur que l'appel ne traverse jamais
    """
    url = f"https://openrouter.ai/api/v1/models/{model}/endpoints"
    dernier = ""
    for essai in range(1, max(1, tentatives) + 1):
        try:
            r = requests.get(url, headers={"Accept": "application/json"}, timeout=15)
            # Un 4xx autre que 429 est une non-conformité permanente, pas une
            # panne : le modèle n'existe pas sous cet identifiant, et relancer
            # trois fois ne le fera pas apparaître. R-004 en fait un INELIGIBLE
            # au pré-vol, pas un INFRA_ERROR
            if 400 <= r.status_code < 500 and r.status_code != 429:
                return {"etat": "ineligible", "motif": "modele_inconnu_du_backend",
                        "detail": f"HTTP {r.status_code} sur {model}"}
            r.raise_for_status()
            data = r.json().get("data", {})
            break
        except Exception as e:
            dernier = f"{type(e).__name__}: {str(e)[:120]}"
            if essai < tentatives:
                print(f"  métadonnées de route indisponibles ({dernier}), "
                      f"tentative {essai}/{tentatives}", file=sys.stderr)
                time.sleep(pause * essai)
    else:
        # Cette requête est une lecture de métadonnées publiques : elle ne
        # consomme aucun jeton et ne coûte rien. La borne protège la durée de
        # la campagne, pas son budget
        return {"etat": "infra", "motif": "metadonnees_route_inatteignables",
                "detail": dernier, "tentatives": tentatives}

    endpoints = data.get("endpoints") or []
    ep = _endpoint_epingle(endpoints, provider)
    if ep is None:
        connus = sorted({str(e.get("tag") or e.get("provider_name")) for e in endpoints})
        return {"etat": "ineligible", "motif": "provider_epingle_absent_de_la_route",
                "detail": f"pin {provider!r} introuvable ; endpoints déclarés : {connus}"}

    mc, ctx = ep.get("max_completion_tokens"), ep.get("context_length")
    if isinstance(mc, int) and mc > 0:
        brut, origine = mc, "max_completion_tokens"
    elif isinstance(ctx, int) and ctx > 0:
        # Source secondaire assumée et nommée : certains endpoints ne déclarent
        # pas de limite de complétion mais bien un contexte total. Le nombre a
        # alors une source vérifiable
        brut, origine = ctx, "context_length"
    else:
        return {"etat": "ineligible", "motif": "route_sans_limite_declaree",
                "detail": f"{ep.get('tag')} ne déclare ni max_completion_tokens ni context_length"}

    if isinstance(ctx, int) and ctx > 0:
        brut = min(brut, ctx - MARGE_PROMPT)

    # `model_id` de l'endpoint est le seul identifiant daté qu'OpenRouter expose
    # (`moonshotai/kimi-k3-20260715`) : c'est ce qui se rapproche le plus d'une
    # révision au sens R-004. Il ne prouve pas un binaire, seulement un endpoint
    # observé sous cette étiquette à cette date
    # Quand l'endpoint répète l'identifiant demandé, il n'apporte aucune
    # révision : consigner cette chaîne la ferait passer pour une information
    # que le fournisseur n'a pas donnée
    mid = ep.get("model_id")
    # La quantification servie est une propriété structurelle de la route, au
    # même titre que l'effort de raisonnement : elle change les poids réellement
    # évalués. Elle entre donc au manifeste d'exécution, sans quoi deux mesures
    # du même modèle en `bf16` et en `mxfp4` porteraient la même identité de
    # configuration
    commun = {"endpoint": ep.get("tag") or ep.get("provider_name"),
              "quantization": ep.get("quantization") or "non déclarée",
              "revision": mid if (mid and mid != model) else "opaque",
              "declare": {"max_completion_tokens": mc, "context_length": ctx},
              "marge_prompt": MARGE_PROMPT}
    if brut < 1:
        return {"etat": "ineligible", "motif": "budget_de_route_non_positif",
                "detail": f"la route accorde {brut} jetons de sortie", **commun}
    budget = min(brut, plafond)
    source = (f"route/{origine} ({brut})" if budget == brut
              else f"plafond de campagne (la route accorde {brut} via {origine})")
    return {"etat": "ok", "budget": budget, "source": source, **commun}


def main() -> None:
    ap = argparse.ArgumentParser(description="collecteur d’appels benchmark-lab-x")
    ap.add_argument("task_dir", type=Path)
    ap.add_argument("--model", help="identifiant du modèle OpenRouter, ou utiliser --alias")
    ap.add_argument("--provider", help="provider épinglé dans provider.only, ou utiliser --alias")
    ap.add_argument(
        "--alias",
        default=None,
        help="alias résolu depuis models.toml : model, provider et expect_provider",
    )
    ap.add_argument(
        "--models-file",
        type=Path,
        default=Path("models.toml"),
        help="registre utilisé par --alias, par défaut ./models.toml",
    )
    ap.add_argument("--run", type=int, default=1, help="numéro de run, de 1 à 4")
    ap.add_argument(
        "--attempt",
        type=int,
        default=1,
        help="numéro de tentative après un run FAILED, sans effacer la preuve",
    )
    ap.add_argument("--temperature", type=float, default=0.0)
    # Le budget de sortie n'est pas une constante. `auto` le résout avant
    # l'appel depuis ce que la route déclare :
    # pas de relance, pas d'escalade, et le modèle reçoit d'emblée le maximum
    # que son fournisseur autorise, borné par --plafond-tokens
    ap.add_argument("--max-tokens", default="auto",
                    help="budget de sortie : 'auto' (défaut, résolu depuis la route) ou un entier")
    ap.add_argument("--budget-tentatives", type=int, default=3,
                    help="lectures des métadonnées de route avant INFRA_ERROR ; "
                         "requête gratuite, la borne protège la durée (défaut: 3)")
    # Régime de confidentialité, R-005 scindée le 2026-08-05
    # `retenu` (défaut) : seules les routes qui ne s'entraînent pas sur les
    # requêtes sont acceptables. C'est le régime des cartes du jeu retenu, dont
    # une fuite ne coûte pas une carte mais toute la série de régression
    # `expose` : les routes qui s'entraînent sur les requêtes sont acceptées,
    # parce que la carte est déjà publique dans le dépôt et que la protection
    # serait une ligne Maginot. Le provider servi reste consigné
    ap.add_argument("--regime", choices=("retenu", "expose"), default="retenu",
                    help="régime de confidentialité de la carte (défaut: retenu, le plus strict)")
    ap.add_argument("--plafond-tokens", type=int, default=262144,
                    help="budget maximal, borne le coût du pire cas (défaut: 262144)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="délai HTTP en secondes, par défaut 600",
    )
    ap.add_argument(
        "--expect-provider",
        default=None,
        help="nom affiché attendu du provider lorsqu’il diffère du pin, par exemple google-vertex servi par Google",
    )
    ap.add_argument("--out-root", type=Path, default=Path("runs") / date.today().isoformat())
    ap.add_argument("--campaign-lock", type=Path,
                    help="campaign.lock.json v2, source directe d’exécution")
    ap.add_argument("--collection-id",
                    help="cellule exacte du campaign.lock v2")
    ap.add_argument("--paid-authorization", type=Path,
                    help="autorisation payante distincte liée au hash du lock")
    ap.add_argument("--budget-ledger", type=Path,
                    help="registre atomique où la tentative est déjà réservée")
    ap.add_argument("--reservation-id",
                    help="réservation exacte de cette tentative")
    args = ap.parse_args()

    reasoning_max_tokens: int | None = None
    reasoning_effort: str | None = None
    lock_v2: dict[str, Any] | None = None
    cellule_v2: dict[str, Any] | None = None
    lock_hash: str | None = None
    locked_alias: str | None = None
    route_program = False
    task_file = "task.md"
    protocol_version = PROTOCOLE_VERSION
    runner_version = COLLECTEUR_VERSION
    v2_demande = any((args.campaign_lock, args.collection_id, args.paid_authorization,
                      args.budget_ledger, args.reservation_id))
    if v2_demande:
        if not all((args.campaign_lock, args.collection_id, args.paid_authorization,
                    args.budget_ledger, args.reservation_id)):
            die(EXIT_USAGE, "le mode v2 exige lock, collection-id, autorisation, registre et réservation")
        if args.alias or args.model or args.provider:
            die(EXIT_USAGE, "le mode v2 refuse alias, modèle et provider en ligne de commande")
        try:
            if args.campaign_lock.is_symlink() or args.paid_authorization.is_symlink() or args.budget_ledger.is_symlink():
                raise ContratV2Invalide("lien symbolique interdit pour les artefacts de campagne")
            campaign_dir_v2 = args.campaign_lock.resolve().parent
            if args.paid_authorization.resolve().parent != campaign_dir_v2:
                raise ContratV2Invalide("autorisation payante hors du dossier de campagne")
            if args.budget_ledger.resolve().parent != campaign_dir_v2:
                raise ContratV2Invalide("registre budget hors du dossier de campagne")
            if args.out_root.resolve() != campaign_dir_v2:
                raise ContratV2Invalide("out-root différent du dossier du campaign.lock")
            lock_v2 = valider_lock(charger_json(args.campaign_lock), Path(__file__).parent.parent)
            valider_environnement_observe(
                lock_v2, "runner", descripteur_environnement_runner()
            )
            lock_hash = empreinte_lock(lock_v2)
            valider_autorisation_payante(
                charger_json(args.paid_authorization), lock_hash,
                lock_v2["budget"]["cap_microdollars"],
            )
            cellule_v2 = cellule_du_lock(lock_v2, args.collection_id)
            acquis = resultat_acquis_v2(campaign_dir_v2, args.collection_id)
            if acquis is not None:
                raise ContratV2Invalide(f"tentative interdite après résultat acquis: {acquis}")
            attendu_task = (Path(__file__).parent.parent / lock_v2["task"]["task_dir"]).resolve()
            if args.task_dir.resolve() != attendu_task:
                raise ContratV2Invalide("task_dir différent du lock")
            if args.attempt not in (1, 2, 3):
                raise ContratV2Invalide("tentative v2 hors de la borne 1 à 3")
            reservation_attendue = f"{args.collection_id}__a{args.attempt}"
            if args.reservation_id != reservation_attendue:
                raise ContratV2Invalide("reservation_id différent de la cellule et de la tentative")
            ledger = RegistreBudget(
                args.budget_ledger, lock_v2["budget"]["cap_microdollars"], lock_hash
            ).etat()
            reservation = ledger.get("reservations", {}).get(args.reservation_id)
            if ledger.get("hold"):
                raise ContratV2Invalide("registre budgétaire en HOLD")
            if not isinstance(reservation, dict) or reservation.get("status") != "reserved":
                raise ContratV2Invalide("réservation budgétaire absente ou non active")
            if reservation.get("max_microdollars") != cellule_v2["max_cost_microdollars"]:
                raise ContratV2Invalide("montant réservé différent du lock")
        except ContratV2Invalide as exc:
            die(EXIT_USAGE, f"HOLD v2 avant appel: {exc}")
        locked_alias = cellule_v2["alias"]
        execution_v3 = cellule_v2["execution_manifest"]
        route_program = (
            execution_v3["schema_version"] == SCHEMA_EXECUTION_ROUTE_PROGRAM
        )
        args.model = execution_v3["model_requested"]
        args.provider = execution_v3["provider_pinned"]
        args.expect_provider = execution_v3["provider_expected"]
        args.run = cellule_v2["run"]
        args.max_tokens = str(execution_v3["max_tokens"])
        args.plafond_tokens = execution_v3["max_tokens"]
        params_v2 = execution_v3["request_parameters"]
        args.temperature = params_v2.get("temperature", 0.0)
        args.seed = params_v2.get("seed", 42)
        omit_params = [p for p in ("temperature", "top_p", "seed") if p not in params_v2]
        reasoning_v2 = params_v2.get("reasoning")
        if isinstance(reasoning_v2, dict):
            reasoning_effort = reasoning_v2.get("effort")
            reasoning_max_tokens = reasoning_v2.get("max_tokens")
        args.regime = lock_v2["task"]["confidentiality_regime"]
        task_file = lock_v2["task"]["task_file"]
        protocol_version = PROTOCOLE_V2
        runner_version = execution_v3["request_adapter_version"]
    elif args.alias:
        if args.model or args.provider:
            die(EXIT_USAGE, "--alias remplace --model/--provider ; ne pas les combiner")
        if not args.models_file.is_file():
            die(EXIT_USAGE, f"{args.models_file} introuvable, requis par --alias")
        import tomllib

        try:
            registry = tomllib.loads(args.models_file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            die(EXIT_USAGE, f"{args.models_file} invalide : {exc}")
        entry = registry.get(args.alias)
        if not isinstance(entry, dict) or "model" not in entry or "provider" not in entry:
            known = ", ".join(sorted(k for k, v in registry.items() if isinstance(v, dict)))
            die(
                EXIT_USAGE,
                f"alias {args.alias!r} absent de {args.models_file}, alias connus : {known}",
            )
        args.model = entry["model"]
        args.provider = entry["provider"]
        if not args.expect_provider and entry.get("expect_provider"):
            args.expect_provider = entry["expect_provider"]
        omit_params = entry.get("omit_params", [])
        if omit_params and not isinstance(omit_params, list):
            die(EXIT_USAGE, "omit_params doit être une liste de noms de paramètres")
        raw_rmt = entry.get("reasoning_max_tokens")
        if raw_rmt is not None:
            if not isinstance(raw_rmt, int) or isinstance(raw_rmt, bool) or raw_rmt < 1:
                die(EXIT_USAGE, "reasoning_max_tokens doit être un entier positif")
            reasoning_max_tokens = raw_rmt
        raw_eff = entry.get("reasoning_effort")
        if raw_eff is not None:
            # Liste blanche retirée le 2026-08-05 : elle refusait `max`, une
            # valeur que certains fournisseurs exposent réellement, et le refus
            # venait de nous et non de l'API. Une liste blanche locale sur un
            # espace de valeurs que nous ne contrôlons pas rend certains
            # candidats intestables sans qu'aucune mesure le justifie
            # Le vrai garde-fou est ailleurs : une valeur peut être acceptée
            # puis ignorée en silence, et seule la comparaison de consommation
            # de jetons de raisonnement entre deux efforts le montre (R-003)
            if not isinstance(raw_eff, str) or not raw_eff.strip():
                die(EXIT_USAGE, "reasoning_effort doit être une chaîne non vide")
            reasoning_effort = raw_eff.strip()
    else:
        omit_params = []
    if not args.model or not args.provider:
        die(EXIT_USAGE, "--model et --provider sont requis, ou utiliser --alias")
    try:
        require_current(dict(model=args.model))
    except ValueError as error:
        die(EXIT_USAGE, str(error))

    # Quatre runs par candidat depuis le 2026-08-05 : le niveau retenu est le
    # troisième meilleur des quatre (R-019), ce qui tolère un mauvais tirage
    runs_valides = (1, 2, 3, 4, 5, 6) if lock_v2 else (1, 2, 3, 4)
    if args.run not in runs_valides:
        die(EXIT_USAGE, f"--run doit appartenir à {runs_valides}")
    if args.attempt < 1:
        die(EXIT_USAGE, "--attempt doit être supérieur ou égal à 1")
    if args.timeout < 1:
        die(EXIT_USAGE, "--timeout doit être supérieur ou égal à 1")
    declare = regime_de_la_carte(Path(args.task_dir) / task_file)
    if declare == "retenu" and args.regime == "expose":
        die(EXIT_USAGE, "la carte déclare le régime retenu : --regime expose est refusé (R-005a)")
    if declare and args.regime == "retenu":
        args.regime = declare
    print(f"régime de confidentialité: {args.regime}"
          f"{' (déclaré par la carte)' if declare else ' (défaut, la carte ne déclare rien)'}",
          file=sys.stderr)

    if args.max_tokens != "auto":
        try:
            int(args.max_tokens)
        except ValueError:
            die(EXIT_USAGE, "--max-tokens doit valoir 'auto' ou un entier")
    if args.budget_tentatives < 1:
        die(EXIT_USAGE, "--budget-tentatives doit être supérieur ou égal à 1")
    if args.temperature != 0.0 or args.seed != 42:
        print(
            "avertissement : les paramètres d'échantillonnage divergent du contrat "
            "de campagne (température 0, seed 42) ; l'écart est consigné dans meta.json",
            file=sys.stderr,
        )

    if not args.task_dir.is_dir():
        die(EXIT_BAD_TASK, f"{args.task_dir} n’est pas un répertoire")
    if not (args.task_dir / task_file).exists():
        die(
            EXIT_BAD_TASK,
            f"{args.task_dir} n’est pas un dossier de carte, {task_file} est absent",
        )

    try:
        if lock_v2:
            prompt, inputs = assembler_prompt_verrouille(
                Path(__file__).parent.parent, lock_v2["task"], verifier_arbre=True
            )
            warnings = []
        else:
            prompt, inputs, warnings = assemble_prompt(args.task_dir)
    except SystemExit:
        raise
    except OSError as exc:
        die(EXIT_PROMPT, f"lecture des fichiers de la carte impossible : {exc}")

    for w in warnings:
        print(f"avertissement : {w}", file=sys.stderr)

    key = preflight_key()

    slug = args.task_dir.name
    # Le candidat est le couple (modèle, route, effort de raisonnement), pas le
    # seul modèle : deux alias du même modèle à des efforts différents sont deux
    # candidats. Nommer le dossier sur le modèle seul les faisait entrer en
    # collision et le second run était refusé
    model_slug = re.sub(r"[^a-zA-Z0-9.-]+", "-", locked_alias or args.alias or args.model)
    if lock_v2:
        out = args.out_root / "collections" / args.collection_id / f"attempt-{args.attempt}"
    else:
        suffix = f"__a{args.attempt}" if args.attempt > 1 else ""
        out = args.out_root / f"{slug}__{model_slug}__r{args.run}{suffix}"
    if out.exists():
        hint = (
            "la tentative précédente est une preuve FAILED, la conserver et passer --attempt "
            f"{args.attempt + 1}"
            if (out / "FAILED").exists()
            else "les runs ne sont jamais écrasés ni supprimés"
        )
        die(EXIT_EXISTS, f"{out} existe déjà ({hint})")

    # P0: reserve the run directory before the network call
    try:
        out.mkdir(parents=True)
    except OSError as exc:
        die(EXIT_IO, f"réservation de {out} impossible : {exc}")

    global V2_ATTEMPT_CONTEXT
    if lock_v2:
        V2_ATTEMPT_CONTEXT = {
            "campaign_lock_hash": lock_hash,
            "collection_id": args.collection_id,
            "attempt": args.attempt,
            "execution_manifest_hash": cellule_v2["execution_manifest_hash"],
            "payload_hash": cellule_v2["payload_hash"],
            "max_cost_microdollars": cellule_v2["max_cost_microdollars"],
            "reservation_id": args.reservation_id,
            "retry_after": None,
            "http_response_received": False,
            "candidate_artifact_accepted": False,
            "cell": cellule_v2,
        }

    # Le budget se résout après la réservation du dossier et avant l'appel : les
    # deux verdicts d'arrêt de R-025 sont des états terminaux R-013 et doivent
    # laisser un reçu quelque part. « Avant toute tentative » qualifie l'appel
    # au modèle, pas la création du dossier
    if args.max_tokens == "auto":
        verdict = resoudre_budget(
            args.model, args.provider, args.plafond_tokens, args.budget_tentatives
        )
        if verdict["etat"] == "ineligible":
            marquer_etat(out, "INELIGIBLE", verdict["motif"],
                         {"regle": "R-025", "modele": args.model, "provider": args.provider,
                          **{k: v for k, v in verdict.items() if k not in ("etat", "motif")}})
            die(EXIT_INELIGIBLE,
                f"route inéligible sans appel : {verdict['motif']} - {verdict.get('detail')}")
        if verdict["etat"] == "infra":
            marquer_etat(out, "INFRA_ERROR", verdict["motif"],
                         {"regle": "R-013", "modele": args.model, "provider": args.provider,
                          **{k: v for k, v in verdict.items() if k not in ("etat", "motif")}})
            die(EXIT_INFRA,
                f"métadonnées de route inatteignables après {args.budget_tentatives} "
                f"tentatives : {verdict.get('detail')}")
        budget, source_budget = verdict["budget"], verdict["source"]
        budget_route = {k: verdict.get(k) for k in
                        ("endpoint", "quantization", "revision", "declare", "marge_prompt")}
    else:
        budget = int(args.max_tokens)
        if lock_v2:
            source_budget = "campaign.lock v3"
            budget_route = {
                "endpoint": cellule_v2["route"]["provider"],
                "quantization": cellule_v2["route"]["quantization"],
                "revision": cellule_v2["route"]["revision"],
                "declare": {"max_completion_tokens": budget},
                "marge_prompt": None,
            }
        else:
            source_budget = "imposé en ligne de commande"
            budget_route = {"endpoint": None, "quantization": "opaque", "revision": "opaque",
                            "declare": None, "marge_prompt": None}
    args.max_tokens = budget
    print(f"budget de sortie: {budget} jetons, source: {source_budget}", file=sys.stderr)

    if reasoning_max_tokens is not None and reasoning_max_tokens >= args.max_tokens:
        marquer_etat(out, "INELIGIBLE", "reasoning_max_tokens_superieur_au_budget",
                     {"regle": "R-025", "reasoning_max_tokens": reasoning_max_tokens,
                      "max_tokens": args.max_tokens, "source_budget": source_budget})
        die(EXIT_INELIGIBLE,
            f"reasoning_max_tokens ({reasoning_max_tokens}) >= budget résolu "
            f"({args.max_tokens}) : la configuration ne laisse aucun jeton de réponse")

    body: dict[str, Any] = {
        "model": args.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": args.temperature,
        "top_p": 1,
        "max_tokens": args.max_tokens,
        "seed": args.seed,
        "provider": {
            "only": [args.provider],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "allow" if args.regime == "expose" else "deny",
        },
        "usage": {"include": True},
    }
    if route_program:
        body["provider"] = params_v2["provider"]
    if reasoning_max_tokens is not None:
        body["reasoning"] = {"max_tokens": reasoning_max_tokens}
    # effort and max_tokens are mutually exclusive on OpenRouter's reasoning
    # object: effort wins when the registry sets it
    if reasoning_effort is not None:
        body["reasoning"] = {"effort": reasoning_effort}
    # Documented deviation: some pinned endpoints reject require_parameters
    # when a sampling knob is unsupported (e.g. seed); the registry may omit
    # those knobs explicitly and the omission is recorded in meta.json
    allowed_omissions = {"seed", "top_p", "temperature"}
    for name in omit_params:
        if name not in allowed_omissions:
            die(
                EXIT_USAGE,
                f"entrée omit_params {name!r} interdite, valeurs admises : {sorted(allowed_omissions)}",
            )
        body.pop(name, None)
        print(
            f"avertissement : paramètre {name!r} omis pour cet endpoint, selon le registre",
            file=sys.stderr,
        )
    # Les octets v3 viennent du manifeste verrouillé. La reconstruction locale
    # est comparée avant tout appel, elle ne peut pas corriger le lock
    payload_calcule = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if lock_v2:
        payload_bytes = construire_payload(cellule_v2["execution_manifest"], prompt)
        if payload_calcule != payload_bytes:
            cleanup_or_fail(out, "lock_parameter_mismatch")
            die(EXIT_USAGE, "payload construit différent du manifeste v3")
    else:
        payload_bytes = payload_calcule
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    if lock_v2 and payload_hash != cellule_v2["payload_hash"]:
        cleanup_or_fail(out, "lock_payload_mismatch")
        die(EXIT_USAGE, "payload_hash différent du campaign.lock")

    global REQUEST_NOTE
    REQUEST_NOTE = json.dumps(
        {
            "model": args.model,
            "provider": args.provider,
            "provider_order": (
                [route["provider_pinned"]
                 for route in execution_v3["provider_routes"]]
                if route_program else [args.provider]
            ),
            "regime_confidentialite": args.regime,
        "params": {k: body[k] for k in ("temperature", "top_p", "max_tokens", "seed", "reasoning") if k in body},
            "timeout_s": args.timeout,
            "budget_sortie": {"max_tokens": args.max_tokens, "source": source_budget},
            "payload_hash": payload_hash,
        },
        ensure_ascii=False,
    )

    started = datetime.now(timezone.utc)
    t0_ns = time.monotonic_ns()
    request_headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if route_program:
        metadata_request = execution_v3["router_metadata"]
        request_headers[metadata_request["header"]] = metadata_request["value"]
    try:
        resp = requests.post(
            API_URL,
            headers=request_headers,
            data=payload_bytes,
            timeout=args.timeout,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        cleanup_or_fail(out, "http_transport", redact_http_body(str(exc)))
        die(EXIT_HTTP, f"échec du transport HTTP : {redact_http_body(str(exc))}")

    if lock_v2:
        V2_ATTEMPT_CONTEXT["http_response_received"] = True

    if resp.is_redirect or resp.is_permanent_redirect:
        cleanup_or_fail(out, f"http_redirect_{resp.status_code}")
        die(
            EXIT_HTTP,
            f"redirection HTTP {resp.status_code} inattendue, contrat à requête unique",
        )

    duration_ns = time.monotonic_ns() - t0_ns
    duration = duration_ns / 1_000_000_000

    if not resp.ok:
        if lock_v2 and resp.status_code in (429, 503):
            retry_after = resp.headers.get("Retry-After")
            V2_ATTEMPT_CONTEXT["retry_after"] = retry_after if retry_after else None
        cleanup_or_fail(
            out,
            f"http_{resp.status_code}",
            redact_http_body(resp.text),
        )
        die(EXIT_HTTP, f"HTTP {resp.status_code}, reçu expurgé dans {out / 'FAILED'}")

    # Empty/whitespace HTTP body is infrastructure-invalid (never judged)
    if not resp.text or not resp.text.strip():
        cleanup_or_fail(out, "empty_body", "(empty or whitespace HTTP body)")
        die(
            EXIT_VALIDATION,
            "le corps de réponse est vide ou ne contient que des espaces, infrastructure invalide",
        )

    try:
        data = resp.json()
    except ValueError:
        cleanup_or_fail(out, "invalid_json", redact_http_body(resp.text))
        die(EXIT_VALIDATION, "le corps de réponse n’est pas un JSON valide")

    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        code_erreur = err.get("code") if isinstance(err, dict) else None
        try:
            code_erreur = int(code_erreur)
        except (TypeError, ValueError):
            code_erreur = None
        raison = f"http_{code_erreur}" if code_erreur in {429, 502, 503} else "api_error"
        if lock_v2 and code_erreur in {429, 502, 503}:
            retry_after = resp.headers.get("Retry-After")
            V2_ATTEMPT_CONTEXT["retry_after"] = retry_after if retry_after else None
        # Never dump secrets; serialize error object only
        detail = redact_http_body(json.dumps(err, ensure_ascii=False, indent=2))
        cleanup_or_fail(out, raison, detail)
        die(EXIT_API_ERROR, "l’API a renvoyé un objet error, voir le reçu FAILED")

    model_served = data.get("model") if isinstance(data.get("model"), str) else None
    provider_served = extract_provider_served(data)
    usage = data.get("usage")
    cost_usd = normalize_cost_usd(usage)

    # OpenRouter returns the provider as a display name ("OpenAI") while the
    # pin is a slug ("openai"): compare on a normalized form, still fail-closed
    def normalize_route(value: str | None) -> str | None:
        if value is None:
            return None
        return re.sub(r"[\s_]+", "-", value.strip().lower())

    expected_providers = {normalize_route(args.provider)}
    if args.expect_provider:
        expected_providers.add(normalize_route(args.expect_provider))
    if route_program:
        for route in execution_v3["provider_routes"]:
            expected_providers.add(normalize_route(route["provider_pinned"]))
            expected_providers.add(normalize_route(route["provider_expected"]))
    provider_ok = normalize_route(provider_served) in expected_providers
    model_ok = normalize_route(model_served) == normalize_route(args.model)

    if not model_ok or not provider_ok:
        cleanup_or_fail(
            out,
            "route_mismatch",
            (
                f"model_requested={args.model!r} model_served={model_served!r}\n"
                f"provider_pinned={args.provider!r} provider_served={provider_served!r}\n"
                "réponse brute complète :\n"
                + redact_http_body(json.dumps(data, ensure_ascii=False, indent=2))
            ),
        )
        die(
            EXIT_ROUTE_MISMATCH,
            (
                f"route divergente : model_served={model_served!r} "
                f"au lieu de {args.model!r}, provider_served={provider_served!r} "
                f"au lieu de {args.provider!r} ; run invalide : "
                f"dossier marqué FAILED sous {out}"
            ),
        )

    try:
        content, first_choice, candidate_kind = validate_response(data)
    except SystemExit:
        # Full raw payload preserved as evidence (redacted), no truncation
        cleanup_or_fail(
            out,
            "validation",
            redact_http_body(json.dumps(data, ensure_ascii=False, indent=2)),
        )
        raise
    if lock_v2:
        # The route adapter accepts candidate bytes only after the served
        # identity matches the pin. A payload from another route is evidence
        # of a routing failure, never an accepted benchmark artifact
        V2_ATTEMPT_CONTEXT["candidate_artifact_accepted"] = True

    finish_reason = first_choice.get("finish_reason")
    if finish_reason == "length":
        print(
            "avertissement : finish_reason=length ; R-013 tranche à la notation en "
            "comparant completion_tokens au budget RÉSOLU : "
            "au-dessus ou égal, le budget est prouvé épuisé et le run vaut FAIL ; "
            "en dessous, le fournisseur a coupé tôt et le run vaut UNKNOWN",
            file=sys.stderr,
        )

    # Input fidelity manifest: every file body actually placed in the prompt
    input_manifest = {
        name: {
            "sha256_16": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "bytes": len(text.encode("utf-8")),
        }
        for name, text in inputs.items()
    }

    params: dict[str, Any] = {
        k: body[k]
        for k in ("temperature", "top_p", "max_tokens", "seed")
        if k in body
    }
    if "reasoning" in body:
        params["reasoning"] = body["reasoning"]

    # Politique de données : R-004 demande de la consigner, mais l'API
    # `/endpoints` d'OpenRouter n'expose plus `data_policy` par endpoint. Nous
    # consignons donc ce que nous contrôlons réellement, la valeur demandée, et
    # marquons `opaque` ce que le fournisseur ne publie plus. Voir le point
    # ouvert de l'audit du 2026-08-06 : c'est la règle qui doit bouger, pas le
    # code, si l'on veut mieux qu'un `opaque` ici
    politique_donnees = {
        "demandee": body["provider"]["data_collection"],
        "route": "opaque",
        "regime": args.regime,
    }
    # Chaque paramètre porte sa provenance (ARD §2.2) : `campaign` pour un
    # réglage de campagne, `candidate` pour ce que le registre attache au
    # candidat, `route_default` pour ce que la route impose
    def provenance(nom: str) -> str:
        if nom == "max_tokens":
            return "route_default" if source_budget.startswith("route/") else "campaign"
        return "candidate" if nom == "reasoning" else "campaign"

    manifeste_calcule = {
        "schema_version": SCHEMA_MANIFESTE,
        "mode": "direct",
        "model": {
            "requested": args.model,
            "served": model_served,
            "revision": budget_route.get("revision") or "opaque",
        },
        "route": {
            "backend": BACKEND,
            "provider_requested": args.provider,
            "provider_served": provider_served,
            "quantization": budget_route.get("quantization") or "opaque",
        },
        "reasoning": {
            "effort": reasoning_effort,
            "max_tokens": reasoning_max_tokens,
        },
        "system_prompt_hash": None,  # aucun message système en mode direct
        "parameters": {k: {"value": v, "source": provenance(k)} for k, v in params.items()},
        "data_policy": politique_donnees,
        "runner_version": runner_version,
        "protocol_version": protocol_version,
        # L'environnement d'exécution du candidat est celui du fournisseur : il
        # n'est pas observable depuis ici. `opaque` est la valeur prévue par
        # l'ARD pour une information non publiée, et vaut mieux qu'un descripteur
        # de notre hôte, qui décrirait la mauvaise machine
        "environment_hash": "opaque",
        "tools": [],
        "agent": None,
    }
    manifeste = cellule_v2["execution_manifest"] if lock_v2 else manifeste_calcule
    if lock_v2 and empreinte(manifeste) != cellule_v2["execution_manifest_hash"]:
        cleanup_or_fail(out, "lock_manifest_mismatch")
        die(EXIT_USAGE, "manifeste d’exécution différent du campaign.lock")

    meta = {
        "task": slug,
        "task_version": lock_v2["task"]["task_version"] if lock_v2 else (
            version_de_la_carte(args.task_dir / task_file) or "inconnue"
        ),
        "run": args.run,
        "date": started.isoformat(),
        "duration_s": round(duration, 2),
        "backend": BACKEND,
        "model_requested": args.model,
        "model_served": model_served,
        "revision": budget_route.get("revision") or "opaque",
        "provider_pinned": args.provider,
        "provider_served": provider_served,
        "quantization_servie": budget_route.get("quantization") or "opaque",
        "regime_confidentialite": args.regime,
        "politique_donnees": politique_donnees,
        "params": params,
        "params_omitted": sorted(omit_params),
        # R-025 : la valeur et sa source restent distinctes
        "budget_sortie": {
            "max_tokens": args.max_tokens,
            "source": source_budget,
            "plafond": args.plafond_tokens,
            **budget_route,
        },
        "timeout_s": args.timeout,
        "usage": usage,
        "cost_usd": cost_usd,
        "finish_reason": finish_reason,
        "input_manifest": input_manifest,
        # Kept for older grid readers; same hashes as input_manifest
        "input_hashes": {name: info["sha256_16"] for name, info in input_manifest.items()},
        # Empreintes complètes, 64 hexadécimaux, comme l'exige l'ARD §2.2. Les
        # reçus antérieurs au 2026-08-06 les portent tronquées à 16 : elles ne
        # se comparent pas entre les deux formats, ce que la version de
        # protocole signale
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "payload_hash": payload_hash,
        "execution_manifest": manifeste,
        "execution_manifest_hash": empreinte(manifeste),
        "protocol_version": protocol_version,
        "runner_version": runner_version,
        "files_sent": sorted(input_manifest.keys()),
        "assembly": (
            "prompt.txt override"
            if (args.task_dir / "prompt.txt").exists()
            else (f"{task_file} instructions block + locked FILE sections" if lock_v2
                  else "task.md instructions block + FILE sections")
        ),
    }
    if lock_v2:
        if meta["prompt_hash"] != lock_v2["task"]["prompt_sha256"]:
            cleanup_or_fail(out, "lock_prompt_mismatch")
            die(EXIT_USAGE, "prompt_hash différent du campaign.lock")
        meta.update({
            "campaign_lock_hash": lock_hash,
            "collection_id": args.collection_id,
            "attempt": args.attempt,
            "reservation_id": args.reservation_id,
        })
        if route_program:
            meta.update({
                "provider_route_order": [
                    route["provider_pinned"]
                    for route in execution_v3["provider_routes"]
                ],
                "openrouter_routing": data.get("openrouter_metadata"),
            })
    # Invariant: meta/raw never hold the API key
    raw_text = json.dumps(data, indent=2, ensure_ascii=False)
    collection_receipt = None
    attempt_receipt = None
    if lock_v2:
        cout_micro = _cout_microdollars(cost_usd)
        statut_cout = "known" if cout_micro is not None else "upper_bound"
        if cout_micro is None:
            cout_micro = cellule_v2["max_cost_microdollars"]
        candidate_bytes = content.encode("utf-8")
        collection_receipt = {
            "schema_version": SCHEMA_COLLECTION,
            "protocol_version": PROTOCOLE_V2,
            "campaign_lock_hash": lock_hash,
            "collection_id": args.collection_id,
            "attempt": args.attempt,
            "result": "COLLECTED",
            "payload_hash": payload_hash,
            "execution_manifest_hash": cellule_v2["execution_manifest_hash"],
            "served": {"model": model_served, "provider": provider_served},
            "candidate": {
                "sha256": hashlib.sha256(candidate_bytes).hexdigest(),
                "bytes": len(candidate_bytes),
                "kind": candidate_kind,
                "truncated": finish_reason == "length",
            },
            "response_json_sha256": hashlib.sha256(
                (raw_text + "\n").encode("utf-8")
            ).hexdigest(),
            "usage": {
                "prompt_tokens": _compteur_jetons(usage.get("prompt_tokens"))
                if isinstance(usage, dict) else None,
                "completion_tokens": _compteur_jetons(usage.get("completion_tokens"))
                if isinstance(usage, dict) else None,
                "reasoning_tokens": _compteur_jetons(
                    (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
                ) if isinstance(usage, dict) else None,
            },
            "cost_accounting": {
                "status": statut_cout,
                "cost_microdollars": cout_micro,
                "reservation_id": args.reservation_id,
            },
            "duration_ns": duration_ns,
            "cause_code": None,
        }
        valider_recu_collecte(collection_receipt, lock_hash, cellule_v2)
        meta["collection_receipt_hash"] = empreinte(collection_receipt)
        attempt_receipt = _recu_tentative("COMPLETE", result="COMPLETE", cost_usd=cost_usd)
        valider_chaine_collecte(
            attempt_receipt, collection_receipt, lock_hash, cellule_v2
        )
    meta_text = json.dumps(meta, indent=2, ensure_ascii=False)
    if key in meta_text or key in raw_text or key in content:
        cleanup_or_fail(
            out,
            "key_leak_guard",
            "la clé API apparaissait dans les données à écrire",
        )
        die(EXIT_IO, "écriture refusée : la clé API apparaîtrait dans les artefacts du run")

    try:
        atomic_write_text(out / "response.md", content)
        atomic_write_text(out / "raw.json", raw_text + "\n")
        atomic_write_text(out / "meta.json", meta_text + "\n")
        if collection_receipt is not None and attempt_receipt is not None:
            atomic_write_text(
                out / "collection-receipt.json",
                json.dumps(collection_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            atomic_write_text(
                out / "attempt-receipt.json",
                json.dumps(attempt_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
        # Run-level commit marker, written last: a folder without COMPLETE
        # was interrupted and must not be judged
        atomic_write_text(out / "COMPLETE", started.isoformat() + "\n")
    except OSError as exc:
        cleanup_or_fail(out, "write_failed", str(exc))
        die(EXIT_IO, f"écriture des artefacts du run impossible : {exc}")

    print(
        f"{out}  provider_served={provider_served}  "
        f"model_served={model_served}  {meta['duration_s']}s  cost_usd={cost_usd}"
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001, voie ultime pour les dossiers réservés
        print(f"erreur inattendue : {exc}", file=sys.stderr)
        raise SystemExit(EXIT_IO) from exc
