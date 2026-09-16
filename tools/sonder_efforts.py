# /// script
# requires-python = ">=3.12"
# dependencies = ["requests"]
# ///
"""Quels niveaux de raisonnement un modèle expose-t-il réellement ?

À lancer avant d'ajouter un modèle au registre. Un fournisseur peut accepter
une valeur d'effort puis l'ignorer en silence : seule la consommation de jetons
de raisonnement le montre. Deux valeurs qui consomment la même chose sont le
même candidat, et n'en méritent qu'un seul alias (R-003).

Usage :
    uv run tools/sonder_efforts.py <slug> <provider> [--efforts a,b,c]
"""

import argparse
import json
import pathlib
import sys

import requests

API = "https://openrouter.ai/api/v1/chat/completions"
# Valeurs vues chez au moins un fournisseur. La liste s'allonge, elle ne filtre
# rien : une valeur inconnue est envoyée et le fournisseur tranche
CONNUS = ("minimal", "low", "medium", "high", "xhigh", "max")

# La question doit être assez dure pour que le budget de réflexion serve
# réellement, sinon les niveaux ne se départagent pas. Mesuré le 2026-08-05 :
# sur « combien de premiers sous 100 », `low` consommait 86 jetons et `medium`
# 43, ordre non monotone ; la question ne forçait aucun effort
QUESTION = (
    "Combien d'entiers de 1 à 10000 inclus ont une somme de chiffres qui est "
    "un carré parfait ? Détaille ton raisonnement puis donne le nombre final."
)


def cle() -> str:
    for ligne in pathlib.Path(".env").read_text(encoding="utf-8").splitlines():
        if ligne.startswith("OPENROUTER_API_KEY="):
            return ligne.split("=", 1)[1].strip().strip('"').strip("'")
    print("clé absente de .env", file=sys.stderr)
    raise SystemExit(2)


def essai(modele: str, provider: str, effort: str | None, jeton: str) -> dict:
    corps = {
        "model": modele,
        "messages": [{"role": "user", "content": QUESTION}],
        "max_tokens": 32768,
        "provider": {"only": [provider], "allow_fallbacks": False, "data_collection": "deny"},
        "usage": {"include": True},
    }
    if effort is not None:
        corps["reasoning"] = {"effort": effort}
    try:
        r = requests.post(API, headers={"Authorization": f"Bearer {jeton}"},
                          json=corps, timeout=300)
    except requests.RequestException as e:
        return {"etat": f"réseau : {type(e).__name__}"}
    if r.status_code != 200:
        msg = r.text[:110].replace("\n", " ")
        return {"etat": f"refusé ({r.status_code}) {msg}"}
    d = r.json()
    u = d.get("usage") or {}
    return {
        "etat": "accepté",
        "raisonnement": (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0,
        "servi": d.get("provider"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("modele")
    ap.add_argument("provider")
    ap.add_argument("--efforts", default=",".join(CONNUS))
    ap.add_argument("--repetitions", type=int, default=3,
                    help="essais par effort ; un seul tirage ne prouve rien (défaut: 3)")
    args = ap.parse_args()
    jeton = cle()

    valeurs = [None] + [e.strip() for e in args.efforts.split(",") if e.strip()]
    resultats = []
    print(f"{args.modele} chez {args.provider}, {args.repetitions} essais par effort\n",
          file=sys.stderr)
    print(f"  {'effort':10} {'état':28} {'jetons de raisonnement':>34}", file=sys.stderr)
    for v in valeurs:
        tirages, etat = [], "accepté"
        for _ in range(args.repetitions):
            r = essai(args.modele, args.provider, v, jeton)
            if r["etat"] != "accepté":
                etat = r["etat"]
                break
            tirages.append(r["raisonnement"])
        nom = v if v is not None else "(aucun)"
        if tirages:
            med = sorted(tirages)[len(tirages) // 2]
            detail = f"{sorted(tirages)}  médiane {med}"
        else:
            med, detail = None, "-"
        print(f"  {nom:10} {etat[:28]:28} {detail:>34}", file=sys.stderr)
        resultats.append({"effort": v, "etat": etat, "tirages": tirages,
                          "raisonnement": med})

    acceptes = [r for r in resultats if r["etat"] == "accepté" and r["raisonnement"] is not None]
    print(file=sys.stderr)
    if not acceptes:
        print("  Aucun effort accepté : ce modèle n'expose pas ce réglage, ou la "
              "route est indisponible.", file=sys.stderr)
    elif all(r["raisonnement"] == 0 for r in acceptes):
        print("  Aucune consommation de raisonnement : le modèle ne raisonne pas, "
              "ou le fournisseur ne le déclare pas. Un seul alias suffit.", file=sys.stderr)
    else:
        # Deux efforts sont distincts si leurs plages de tirages ne se recouvrent
        # pas. Comparer des médianes ne suffit pas quand la dispersion intra-effort
        # dépasse l'écart inter-efforts, ce qui est le cas courant
        tries = sorted(acceptes, key=lambda x: x["raisonnement"])
        distincts, dernier = [], None
        for r in tries:
            if dernier is None or min(r["tirages"]) > max(dernier["tirages"]):
                distincts.append(r)
                dernier = r
        noms = [(r["effort"] or "(aucun)") for r in distincts]
        confondus = [(r["effort"] or "(aucun)") for r in tries if r not in distincts]
        print(f"  Niveaux séparables (plages disjointes) : {', '.join(noms)}", file=sys.stderr)
        if confondus:
            print(f"  Indistinguables des précédents : {', '.join(confondus)} - "
                  f"leurs plages se recouvrent, un seul alias suffit", file=sys.stderr)
        print(f"  → un alias par niveau distinct, jamais d'effort implicite (R-003)",
              file=sys.stderr)
        aucun = next((r for r in acceptes if r["effort"] is None), None)
        if aucun:
            proches = [r["effort"] for r in acceptes
                       if r["effort"] and abs(r["raisonnement"] - aucun["raisonnement"])
                       <= 0.25 * max(aucun["raisonnement"], 1)]
            if proches:
                print(f"  → le défaut correspond à : {', '.join(proches)}. "
                      f"Nommer l'alias par cette valeur plutôt que « défaut »", file=sys.stderr)

    print(json.dumps(resultats, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
