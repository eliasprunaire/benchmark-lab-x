---
style_gate: pass
---

# Recette visuelle S11

État : **HOLD_VALIDATION_VISUELLE**. La décision visuelle appartient à Ayo et doit être consignée dans l’Issue #214 avec le SHA retenu.

Base : `main` à jour, `2b1bfce48492f0113fbabf4ac0aa94bff0caf1f1`. S19 (PR #274), S21 (#276), S22 (#283) et S27 (#286) sont intégrées. Branche : `fix/s11-validation-visuelle`. Contrat : [Issue #214](https://github.com/eliasprunaire/benchmark-lab-x/issues/214), complété par la demande de recette. Les directives communes et décisions D13 bis à D16 ont été relues dans [S22](https://github.com/eliasprunaire/benchmark-lab-x/issues/247).

Les données sont entièrement synthétiques. Les vues de base réutilisent les fixtures S6 et `preparation.dispatch`, puis leurs copies en mémoire couvrent les états de présentation. Aucun reçu historique ni donnée privée n’est chargé. Ces variantes ne prouvent pas les transitions du moteur.

Les captures et les vues structurées sont conservées hors dépôt dans `/Users/ayo/Projects/benchmark-lab-x-s11-captures`. Le fichier `matrice.json` fixe les entrées communes avant/après. Chaque vue est rendue par `benchmark_web.views.render`, avec le gabarit, la feuille de style et les polices locales du commit concerné. Les variantes ouvertes ajoutent uniquement l’attribut natif `open` aux dépliants indiqués.

Chrome local : **153.0.8010.37**, macOS. Capture native `--headless=new --screenshot`, à **1280, 900, 800, 700, 600 et 400 px**, en clair et sombre. Le protocole natif de Chrome impose le viewport et `prefers-color-scheme`. La hauteur englobe le document mesuré. Un serveur limité à `127.0.0.1` sert ces fichiers synthétiques. Les autres destinations sont désactivées. Aucun script client ajouté.

Le diagnostic du dispositif de capture a montré que `--window-size=400,…` peut produire une image de 400 px d’un contenu mis en page à 500 px. Les images de diagnostic sont séparées et exclues de la preuve. La capture retenue fixe le viewport par `Emulation.setDeviceMetricsOverride` sans `--window-size`, qui réimposerait une taille à la prise de vue. Les dimensions PNG et la couleur du thème sont contrôlées pour chaque capture. Référence : [commande native Chromium](https://github.com/chromium/chromium/blob/main/components/headless/command_handler/headless_command.js).

## Matrice

Les noms correspondent aux fichiers HTML et aux préfixes des PNG dans `avant/` et `apres/`. Chaque ligne couvre les deux thèmes et les six largeurs.

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

## Défauts constatés avant correction

| Défaut | Route, état, thème et largeur | Avant | Après |
|---|---|---|---|
| F1 | Toutes les routes et tous les états, deux thèmes, six largeurs | Le conteneur SVG des symboles réserve 150 px au-dessus de l’en-tête malgré `hidden` | Conteneur de symboles sans boîte de rendu ; en-tête à 0 px |
| F2 | Dossier, exemple et critères structurés ; familles 3, 4 et 8 ; deux thèmes, six largeurs | Le titre principal et le titre de fenêtre deviennent « Qualité », dernier libellé de la boucle des critères | Titre du dossier conservé : « Est-ce le travail que vous voulez tester ? » |
| F3 | Dossier, étape active ; sombre, six largeurs | Chiffre blanc sur fond vert clair, contraste 2,06:1 | Chiffre sur fond vert : 7,88:1 en sombre |
| F4 | Dossier, étiquette de révision ; comparaison, badges ; clair, six largeurs | Étiquette 3,36:1, badge satisfait 4,48:1 et badge à reprendre 3,93:1 | Étiquette 4,66:1 ; satisfait 4,86:1 ; à reprendre 5,35:1 |
| F5 | Formulaires et boutons ; sombre, six largeurs | Bouton survolé 3,92:1 ; focus sur papier 2,42:1 ; bordures de champs 2,10:1 en sombre et 2,21:1 en clair | Survol sombre 5,07:1 ; focus 8,46:1 sur papier ; champs 4,14:1 en sombre et 4,09:1 en clair |
| F6 | Lancement, contrôle passant ou bloquant ; deux thèmes, six largeurs | Le champ numérique du plafond garde son habillage natif, différent des autres champs | Champ numérique aligné sur les champs textuels, sans changer ses bornes ni sa valeur |
| F7 | Préparation indisponible, deux thèmes, six largeurs | `aria-describedby` du besoin est dupliqué ; Chrome conserve seulement l’aide et perd le lien à l’indisponibilité | Attribut unique reliant à la fois l’aide et l’état de disponibilité |
| F8 | Lien d’évitement, toutes les routes ; contrôle clavier à 400 px | L’activation atteint l’ancre mais laisse `document.activeElement` sur le corps | Focus effectivement placé sur `main`, sans tabulation positive |
| F9 | Accès, configurations et lancement ; deux thèmes, 400 px avec agrandissement à 200 % | Les mots longs des titres et libellés débordent ; après retrait du SVG, largeurs résiduelles de 212, 246 et 229 px CSS pour un viewport de 200 | Retour à la ligne hérité, sans masquer ni retirer le texte |

## Contrastes

Calcul à partir des couleurs hexadécimales CSS : conversion sRGB linéaire, luminance `0,2126 R + 0,7152 G + 0,0722 B`, puis `(L claire + 0,05) / (L sombre + 0,05)`. Les assertions exigent 4,5:1 pour les textes contrôlés et 3:1 pour les contours de champs et le focus. Les chiffres sont arrondis uniquement dans ce tableau.

| Texte ou indicateur / fond | Clair avant | Clair après | Sombre avant | Sombre après |
|---|---:|---:|---:|---:|
| `--ink` / `--paper` | 15.01:1 | 15.01:1 | 15.26:1 | 15.26:1 |
| `--ink` / `--surface` | 16.66:1 | 16.66:1 | 13.88:1 | 13.88:1 |
| `--ink` / `--soft` | 13.60:1 | 13.60:1 | 12.40:1 | 12.40:1 |
| `--ink-2` / `--paper` | 8.79:1 | 8.79:1 | 10.82:1 | 10.82:1 |
| `--ink-2` / `--surface` | 9.76:1 | 9.76:1 | 9.84:1 | 9.84:1 |
| `--ink-2` / `--soft` | 7.96:1 | 7.96:1 | 8.79:1 | 8.79:1 |
| `--muted` / `--paper` | 5.34:1 | 5.34:1 | 6.64:1 | 6.64:1 |
| `--muted` / `--surface` | 5.93:1 | 5.93:1 | 6.04:1 | 6.04:1 |
| `--muted` / `--soft` | 4.84:1 | 4.84:1 | 5.40:1 | 5.40:1 |
| `--accent-ink` / `--paper` | 8.12:1 | 8.12:1 | 10.86:1 | 10.86:1 |
| `--accent-ink` / `--surface` | 9.01:1 | 9.01:1 | 9.88:1 | 9.88:1 |
| `--accent-ink` / `--soft` | 7.36:1 | 7.36:1 | 8.83:1 | 8.83:1 |
| `--accent` / `--paper` | 5.70:1 | 5.70:1 | 8.67:1 | 8.67:1 |
| `--accent` / `--surface` | 6.33:1 | 6.33:1 | 7.88:1 | 7.88:1 |
| `--accent` / `--soft` | 5.17:1 | 5.17:1 | 7.05:1 | 7.05:1 |
| `--ink` / `--accent-soft` | 13.93:1 | 13.93:1 | 11.43:1 | 11.43:1 |
| `--ink-2` / `--accent-soft` | 8.16:1 | 8.16:1 | 8.10:1 | 8.10:1 |
| `--muted` / `--accent-soft` | 4.96:1 | 4.96:1 | 4.97:1 | 4.97:1 |
| `--accent-ink` / `--accent-soft` | 7.54:1 | 7.54:1 | 8.13:1 | 8.13:1 |
| `--ink` / `--warn-soft` | 14.32:1 | 14.32:1 | 11.27:1 | 11.27:1 |
| `--ink` / `--ko-soft` | 13.33:1 | 13.33:1 | 12.01:1 | 12.01:1 |
| `--warm` / `--warm-soft` | 3.36:1 | 4.66:1 | 6.09:1 | 6.09:1 |
| `--ok` / `--ok-soft` | 4.48:1 | 4.86:1 | 6.36:1 | 6.36:1 |
| `--ko` / `--ko-soft` | 4.85:1 | 4.85:1 | 6.10:1 | 6.10:1 |
| `--warn` / `--warn-soft` | 3.93:1 | 5.35:1 | 6.86:1 | 6.86:1 |
| `--unk` / `--unk-soft` | 4.86:1 | 4.86:1 | 6.21:1 | 6.21:1 |
| `--wait` / `--wait-soft` | 4.81:1 | 4.81:1 | 6.98:1 | 6.98:1 |
| `--on-btn` / `--btn` | 6.33:1 | 6.33:1 | 5.15:1 | 5.15:1 |
| `--on-btn` / `--btn-hover` | 9.01:1 | 9.01:1 | 3.92:1 | 5.07:1 |
| `--surface` / `--accent` | 6.33:1 | 6.33:1 | 2.06:1 | 7.88:1 |
| `--focus` / `--paper` | 6.63:1 | 6.63:1 | 2.42:1 | 8.46:1 |
| `--focus` / `--surface` | 7.36:1 | 7.36:1 | 2.20:1 | 7.69:1 |
| `--line-2` / `--paper` | 1.99:1 | 3.68:1 | 2.31:1 | 4.55:1 |
| `--line-2` / `--surface` | 2.21:1 | 4.09:1 | 2.10:1 | 4.14:1 |

La ligne `--surface / --accent` désigne le chiffre de l’étape active ; avant correction, son texte était blanc dans les deux thèmes. `--line` reste une séparation décorative, sans être le seul moyen d’identifier un champ ou un état. Les états gardent texte et icône ; les barres de coût restent décoratives et accompagnées des valeurs.

## Vérifications

Avant correction : `uv run --with requests --with mpmath==1.3.0 python -m unittest tests.test_web_redesign tests.test_s9_inline tests.test_s10_regressions tests.test_web_access tests.test_web_boundary` : **29 tests en 7.312 s, OK**.

Les mesures avant correction ne trouvent aucun débordement horizontal de page sur les 468 combinaisons. Les tableaux ont leur propre région de défilement. Ce constat de géométrie reste distinct de la lecture visuelle et des contrôles clavier.

Après correction : mêmes tests ciblés, **32 tests en 7.055 s, OK**.

Commande complète obligatoire :

```sh
uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests
```

Résultat exact :

```text
Ran 1205 tests in 154.972s

OK
```

La suite historique séparée `uv run python -B -m unittest benchmark.test_demo` passe : **69 tests en 39.384 s, OK**. Ces résultats sont acquis sous macOS avec Python 3.12.13. Les tests ajoutés utilisent `patch('socket.socket.connect', side_effect=AssertionError('No network'))`. Les tests HTTP existants continuent à utiliser leurs sockets locales et leurs exécuteurs factices ; aucun appel fournisseur n’est utilisé comme validation.

Lecture du HTML rendu et assertions : identifiants uniques, lien d’évitement en premier, cible `main` focalisable sans ajouter de tabulation, labels associés, aides et indisponibilité reliées, formulaires POST et jeton CSRF existant, absence de tabulation positive, correction et pièces repliées par défaut, tableau nommé et défilable au clavier, en-têtes de colonnes, lignes de retour focalisables, preuves échappées et badges textuels. Les frontières web/moteur restent vérifiées par la suite.

Contrôle Chrome à 1280 et 400 px, dans les deux thèmes : lien d’évitement au premier Tab, Entrée donnant le focus à `main` après correction ; ouverture d’une preuve puis fermeture avec Entrée sur son résumé ; retour navigateur et retour explicite donnant le focus à `attempt-attempt-error`. Les filtres `sort=cost&direction=desc` sont conservés au retour explicite. Le script de retour existant et sa portée restent inchangés. Les résultats détaillés se trouvent dans `avant/clavier.json` et `apres/clavier.json`.

Le contrôle des 39 HTML rendus ne relève aucun attribut ou identifiant dupliqué, aucune aide manquante ni tabulation positive (`apres/structure.json`). Les 468 mesures après correction ne trouvent aucun débordement horizontal de page. L’en-tête commence à 0 px dans toutes les vues. Le tableau large conserve son défilement interne ; la correction ouverte, les pièces et les preuves longues restent dans le flux du document.

Le contrôle de texte agrandi utilise un reflow équivalent à 200 % : fenêtres de 1280 et 400 pixels rendues à 640 et 200 pixels CSS avec un facteur de pixels de 2, sur un représentant de chacune des onze familles, dans les deux thèmes. Ce contrôle est une émulation documentée, pas une observation du menu Zoom de Chrome. Avant correction, le SVG caché provoque un débordement de 316 pixels CSS pour un viewport de 200 ; sa suppression de la mise en page retire ce débordement. Le retour à la ligne des mots longs traite les trois débordements résiduels des familles 5, 6 et 7. Les 44 captures `zoom-*` et `zoom.json` portent cette preuve complémentaire. Aucun débordement de page ne subsiste dans ces 44 contrôles après correction.

`avant/` conserve 468 captures de référence, plus les compléments de zoom. `apres/` reçoit la même matrice depuis le commit proposé, avec son SHA complet dans `apres/source.txt`. Les fichiers `mesures.json` relient noms, thèmes, largeurs et géométrie. Les captures de diagnostic sont exclues de ces ensembles.

## Limites et décision

- Les états sont simulés ; aucune campagne réelle, aucun appel fournisseur, aucune publication.
- La lecture du HTML et les tests ne remplacent pas la décision visuelle d’Ayo.
- La qualification affichée utilise les états structurés existants ; son résumé automatisé et ses constats ne sont pas présentés dans ce dépliant. Aucune nouvelle présentation métier n’est inventée dans cette recette.
- Les captures couvrent toute la matrice ; la décision esthétique reste humaine. Les planches de lecture et les mesures ne constituent pas une approbation d’Ayo.
- La CI Linux sur le SHA proposé et la consignation de la décision dans l’Issue restent ouvertes. Aucun push ni PR n’est demandé.
