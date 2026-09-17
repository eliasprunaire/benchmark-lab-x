---
style_gate: pass
---

# Recette visuelle S11

État : **HOLD_VALIDATION_VISUELLE**. La campagne de captures avant et après reste à produire, puis à soumettre à la décision visuelle d’Ayo.

Le correctif CSS intégré à `main` est `d11e531`. Ses règles sont présentes dans [la feuille de style courante](../benchmark_web/static/preparation.css) : retour à la ligne `break-word` sur `body`, `normal` sur `th` et couleur de survol sombre `#285c32`. Cette inspection du code ne constitue pas une preuve visuelle.

Le contrat de recette reste l’[Issue #214](https://github.com/eliasprunaire/benchmark-lab-x/issues/214). Les directives communes et D13 bis à D16 ont été relues dans [S22 #247](https://github.com/eliasprunaire/benchmark-lab-x/issues/247).

## Vues à capturer

Trente-neuf vues, à produire lors de la prochaine campagne de captures. Cette liste ne prouve aucune capture déjà faite.

| Famille | Vues et états | Route produit |
|---|---|---|
| 1. Accueil | `01-accueil`, `01-sans-dossier`, `01-avec-dossiers` | `/`, `/preparation` |
| 2. Préparation | `02-saisie`, `02-clarification`, `02-traitement`, `02-interruption`, `02-indisponible` | `/preparation`, `/preparation/dossiers/fixture` |
| 3. Exemple | `03-exemple`, `03-piece-longue`, `03-correction-ouverte`, `03-revision-modifiee`, `03-validation` | `/preparation/dossiers/fixture` |
| 4. Qualification | `04-reussie`, `04-refusee`, `04-a-reprendre` | `/preparation/dossiers/fixture` |
| 5. OpenRouter | `05-non-connecte`, `05-connecte`, `05-invalide` | `/preparation/access` |
| 6. Configurations | `06-estimation-connue`, `06-estimation-incomplete`, dont un modèle non réglable | `/preparation/dossiers/fixture/configurations` |
| 7. Lancement | `07-controles-passants`, puis un `07-bloque-*` par contrôle | `/preparation/dossiers/fixture/campaigns/comparison/conditions` |
| 8. Campagne | `08-vide`, `08-en-cours`, `08-partielle`, `08-interrompue`, `08-terminee` | `/preparation/dossiers/fixture`, historique ouvert |
| 9. Comparaison | `09-comparaison`, `09-filtres-ouverts` : satisfait, non satisfait, à reprendre, coûts connus et inconnus | `/preparation/dossiers/fixture/campaigns/comparison` |
| 10. Preuve | `10-preuve-fermee`, `10-preuve-longue`, `10-retour` | `…/campaigns/comparison/attempts/attempt-error`, puis comparaison |
| 11. Accès privé | `11-session-perdue`, `11-acces-refuse` | `/preparation`, `/preparation/dossiers/fixture` |

Famille 7 : un `07-bloque-*` pour chacun des cinq contrôles (`example_validated`, `example_qualified`, `configurations_available`, `access_connected`, `estimate_under_cap`), soit six vues de lancement avec `07-controles-passants`. Total : 3 + 5 + 5 + 3 + 3 + 2 + 6 + 5 + 2 + 3 + 2 = 39.

## Preuves absentes

Les captures avant et après, les zooms, le harnais de capture et les relevés annoncés ne sont pas disponibles. Le répertoire hors dépôt précédemment cité est absent. Les fichiers `matrice.json`, `mesures.json`, `clavier.json`, `structure.json`, `zoom.json` et `apres/source.txt` ne sont donc pas des preuves consultables.

Aucune matrice de vues effectivement capturées, aucun compte de PNG ni aucun résultat de parcours clavier ou de géométrie n’est établi par ce document. La correspondance entre `--window-size` et la largeur CSS n’est pas prouvée ; elle devra être mesurée dans le navigateur lors de la future recette.

## Contrastes courants

Les paires du tableau retiré sont recalculées depuis les couleurs hexadécimales de la feuille de style courante, avec héritage des variables du thème clair dans le thème sombre. Les colonnes historiques avant/après sont retirées : les valeurs ci-dessous décrivent uniquement le CSS actuel.

Le calcul reprend la conversion sRGB linéaire du [test des contrastes](../tests/test_web_redesign.py), puis le rapport des luminances corrigées. Les rapports sont arrondis à deux décimales pour l’affichage.

| Texte ou indicateur / fond | Clair courant | Sombre courant |
|---|---:|---:|
| `--ink` / `--paper` | 15,01:1 | 15,26:1 |
| `--ink` / `--surface` | 16,66:1 | 13,88:1 |
| `--ink` / `--soft` | 13,60:1 | 12,40:1 |
| `--ink-2` / `--paper` | 8,79:1 | 10,82:1 |
| `--ink-2` / `--surface` | 9,76:1 | 9,84:1 |
| `--ink-2` / `--soft` | 7,96:1 | 8,79:1 |
| `--muted` / `--paper` | 5,34:1 | 6,64:1 |
| `--muted` / `--surface` | 5,93:1 | 6,04:1 |
| `--muted` / `--soft` | 4,84:1 | 5,40:1 |
| `--accent-ink` / `--paper` | 8,12:1 | 10,86:1 |
| `--accent-ink` / `--surface` | 9,01:1 | 9,88:1 |
| `--accent-ink` / `--soft` | 7,36:1 | 8,83:1 |
| `--accent` / `--paper` | 5,70:1 | 8,67:1 |
| `--accent` / `--surface` | 6,33:1 | 7,88:1 |
| `--accent` / `--soft` | 5,17:1 | 7,05:1 |
| `--ink` / `--accent-soft` | 13,93:1 | 11,43:1 |
| `--ink-2` / `--accent-soft` | 8,16:1 | 8,10:1 |
| `--muted` / `--accent-soft` | 4,96:1 | 4,97:1 |
| `--accent-ink` / `--accent-soft` | 7,54:1 | 8,13:1 |
| `--ink` / `--warn-soft` | 14,32:1 | 11,27:1 |
| `--ink` / `--ko-soft` | 13,33:1 | 12,01:1 |
| `--warm` / `--warm-soft` | 4,66:1 | 6,09:1 |
| `--ok` / `--ok-soft` | 4,86:1 | 6,36:1 |
| `--ko` / `--ko-soft` | 4,85:1 | 6,10:1 |
| `--warn` / `--warn-soft` | 5,35:1 | 6,86:1 |
| `--unk` / `--unk-soft` | 4,86:1 | 6,21:1 |
| `--wait` / `--wait-soft` | 4,81:1 | 6,98:1 |
| `--on-btn` / `--btn` | 6,33:1 | 5,15:1 |
| `--on-btn` / `--btn-hover` | 9,01:1 | 7,87:1 |
| `--surface` / `--accent` | 6,33:1 | 7,88:1 |
| `--focus` / `--paper` | 6,63:1 | 8,46:1 |
| `--focus` / `--surface` | 7,36:1 | 7,69:1 |
| `--line-2` / `--paper` | 3,68:1 | 4,55:1 |
| `--line-2` / `--surface` | 4,09:1 | 4,14:1 |

La paire `--surface` / `--accent` correspond au chiffre de l’étape active. Les assertions existantes exigent au moins 4,5:1 pour leurs paires de texte et 3:1 pour les contours de champs et le focus ; elles ne couvrent pas toutes les lignes de ce tableau.

Le test compare aussi `--btn` et `--btn-hover` au sein de chaque thème. Son seuil de non-régression est 1,4:1 pour éviter un survol presque identique au repos. Ce seuil protège l’écart de couleur ; il ne constitue ni une norme d’accessibilité ni une validation perceptive humaine. Les rapports courants sont 1,42:1 en clair et 1,53:1 en sombre.

## Vérifications reproductibles

```sh
uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests -p test_web_redesign.py
uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests
```

Ces commandes contrôlent des propriétés automatisées. Leurs résultats ne prouvent ni une campagne de captures ni une validation visuelle. La suite historique `benchmark/test_demo.py` n’était pas découverte par la seconde commande ; elle a depuis été retirée avec l’acquisition historique. Le lecteur est désormais couvert sous `tests/test_historical_reader.py`.

## Limites et décision

- La campagne de captures avant et après, les mesures de viewport, de reflow et les contrôles clavier dans le navigateur restent à produire
- L’absence de défaut visuel restant n’est pas établie
- La validation visuelle d’Ayo reste ouverte
- Les captures et les essais réels ne sont pas exécutés dans cette correction documentaire et de test

Le [PRD](PRD.md), l’[ARD](ARD.md), les [règles](RULES.md) et le [glossaire](../CONTEXT.md) restent les sources canoniques ; cette recette n’ajoute aucun contrat produit.
