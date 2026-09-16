---
style_gate: pass
---

# Recette visuelle S11

État : **HOLD_VALIDATION_VISUELLE**. La décision visuelle reste celle d’Ayo, à consigner dans l’Issue #214 avec le SHA retenu.

Base avant : `b4c406ca4b47e75e01044715c3f6e5d73fea9230`. Correctif CSS : `7fc8348`. Les données sont synthétiques, les vues sont rendues par `benchmark_web.views.render` et aucun reçu historique, secret, appel fournisseur, campagne réelle ou publication n’intervient.

Les preuves sont hors dépôt dans `/Users/ayo/Projects/benchmark-lab-x-s11-captures`. Le harnais local `harnais/capturer.py` démarre chaque Chrome dans une nouvelle session, attend `communicate(timeout=60)` et détruit le groupe au dépassement. Le serveur est limité à `127.0.0.1` et arrêté dans un `finally`.

La sonde utilise `matchMedia('(width: …px)')` après `Emulation.setDeviceMetricsOverride`. La correspondance contrôlée est donc `window-size 1280, 900, 800, 700, 600, 400` vers les mêmes largeurs CSS, et `640` et `200` CSS pour le reflow. Cette sonde évite le faux viewport étroit produit par `--window-size=400,…` seul sur ce Mac.

## Matrice

| Famille | Vues |
|---|---|
| 1. Accueil | `01-accueil`, `01-sans-dossier`, `01-avec-dossiers` |
| 2. Préparation | `02-saisie`, `02-clarification`, `02-traitement`, `02-interruption`, `02-indisponible` |
| 3. Exemple | `03-exemple`, `03-piece-longue`, `03-correction-ouverte`, `03-revision-modifiee`, `03-validation` |
| 4. Qualification | `04-reussie`, `04-refusee`, `04-a-reprendre` |
| 5. OpenRouter | `05-non-connecte`, `05-connecte`, `05-invalide` |
| 6. Configurations | `06-estimation-connue`, `06-estimation-incomplete` |
| 7. Lancement | `07-controles-passants`, `07-bloque-*` |
| 8. Campagne | `08-vide`, `08-en-cours`, `08-partielle`, `08-interrompue`, `08-terminee` |
| 9. Comparaison | `09-comparaison`, `09-filtres-ouverts` |
| 10. Preuve | `10-preuve-fermee`, `10-preuve-longue`, `10-retour` |
| 11. Accès privé | `11-session-perdue`, `11-acces-refuse` |

La matrice compte 39 vues, chacune en clair et sombre aux six largeurs. `avant/` et `apres/` contiennent chacune 468 PNG. `zoom/` contient 44 PNG, soit deux thèmes et deux largeurs de reflow pour un représentant de chaque famille. `matrice.json`, `mesures.json`, `clavier.json`, `structure.json` et `zoom.json` accompagnent les captures. `apres/source.txt` porte le SHA du commit capturé.

## Défauts constatés et corrections

| Défaut | Route, état, thème et largeur | Avant | Après |
|---|---|---|---|
| F1 à F9 | Correctifs déjà présents dans la base avant de cette reprise | Les anciennes transitions visuelles ne sont pas recréées sans leurs captures d’origine | Les invariants automatisés restent vérifiés ; cette passe ne présente pas une preuve avant/après inventée |
| F10 | Comparaison et preuve, en-têtes de tableau, clair et sombre, 1280 puis viewport étroit | `overflow-wrap: anywhere` sur `body`, puis sur `th`, coupe « PREUVE / S » et « CONFIGURAT / ION » | `body` utilise `break-word` et `th` reste sans rupture arbitraire |
| F11 | Boutons principaux, sombre, toutes largeurs | `--btn-hover: #407b46` est visuellement confondu avec `--btn: #3f7a45` | `--btn-hover: #285c32` est distinct et conserve un libellé lisible |

Le contraste `--on-btn` blanc sur `--btn-hover` sombre `#285c32` est **7,87:1**. Le test vérifie aussi que les luminances de `--btn` et `--btn-hover` diffèrent et que le contraste du libellé reste au moins à 4,5:1.

## Vérifications

La lecture structurée des 39 HTML alimente `structure.json` : identifiants uniques, lien d’évitement en premier et absence de tabulation positive. `clavier.json` conserve ces invariants HTML. Les captures complètes, leurs dimensions PNG et la correspondance de viewport sont dans `mesures.json`.

La validation complète a exécuté :

```sh
uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests
```

Résultat exact :

```text
Ran 1215 tests in 153.580s

OK
```

## Limites et décision

- Les états sont simulés et les captures ne prouvent aucune transition du moteur
- La preuve de structure et les captures ne remplacent pas la décision visuelle d’Ayo
- Aucun défaut visuel restant n’est déclaré par cette passe avant la décision humaine
