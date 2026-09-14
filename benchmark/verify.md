---
style_gate: pass
---

# Vérification locale

Commande contractuelle, hors ligne :

```bash
python3 -B -m unittest benchmark.test_demo -v
```

Les tests créent un dépôt et des runs temporaires, utilisent un faux exécutable Pi local, traversent les interfaces publiques et suppriment leurs artefacts à la fin. Ils ne construisent aucun reçu ni aucune collection à la place de `collect`.

Un résultat vert prouve le bundle local et ses protections testées. Il ne prouve aucun appel candidat, résultat réel, jugement réel, publication ou déploiement.

Les tests de compatibilité des formats sont également découverts par la suite générale :

```bash
python3 -B -m unittest discover -s tests -p test_runtime_format_migration.py -v
uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests
```

La première suite reste une commande distincte : la découverte sous `tests/` ne couvre pas l’ensemble de `benchmark/test_demo.py`. Plusieurs contrôles d’interruption de cette suite exigent macOS et `kqueue` ; un résultat local ne prouve pas leur équivalent Linux.
