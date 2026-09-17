"""Lecture et présentation des preuves scellées du prototype historique"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import secrets
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = PACKAGE_DIR.parents[1]

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
            stream.write(raw)
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
# Il ne fait pas partie du contrat scellé
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("present")
    p.add_argument("--source-run", required=True)
    p.add_argument("--run-dir", required=True)
    p = commands.add_parser("show")
    p.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "present":
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
