---
style_gate: pass
---

# Vérification locale

Les contrôles du lecteur historique et des formats de preuve passent sans Pi ni acquisition :

```bash
uv run python -B -m unittest tests.test_historical_reader tests.test_runtime_format_migration -v
```

Ils utilisent des résultats synthétiques scellés dans des répertoires temporaires. Ils vérifient la lecture, le rendu, la copie exacte des résultats, le refus des altérations et l'absence des anciennes commandes d'acquisition. Ils sont inclus dans la commande CI :

```bash
uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests
```

La suite `benchmark/test_demo.py` a été retirée avec l'acquisition historique. Ses régressions utiles au lecteur ont rejoint `tests/test_historical_reader.py`. Les tests du moteur courant utilisent des transports simulés.

Un résultat vert prouve les comportements testés localement. Il ne prouve aucun appel candidat, jugement réel, publication ou déploiement. Les résultats macOS restent distincts de ceux acquis sous Linux.
