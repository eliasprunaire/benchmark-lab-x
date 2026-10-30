---
version: b
name: Bench-X
description: Parcours web de Bench-X, du besoin à la comparaison des configurations et à leurs preuves. Direction B « le journal d'essai ».
colors:
  paper: "#f6f7f9"
  surface: "#ffffff"
  soft: "#eceef3"
  line: "#d9dce4"
  line-soft: "#e8eaf0"
  line-2: "#767c8c"
  ink: "#1a1d29"
  ink-2: "#4a4f5e"
  muted: "#4a4f5e"
  accent: "#2f4bd8"
  accent-ink: "#2f4bd8"
  accent-soft: "#e7ebfc"
  accent-soft-2: "#eef1fd"
  btn: "#2f4bd8"
  btn-hover: "#1f32a0"
  on-btn: "#ffffff"
  warm: "#6a3d9a"
  warm-soft: "#f1eaf8"
  ok: "#1e6e4e"
  ok-soft: "#e1f1ea"
  ko: "#b42318"
  ko-soft: "#fbe7e5"
  warn: "#7a4b00"
  warn-soft: "#fbeed6"
  unk: "#4a4f5e"
  unk-soft: "#eceef3"
  wait: "#2f4bd8"
  wait-soft: "#e7ebfc"
  focus: "#b45309"
  fait: "#7387e2"
typography:
  display:
    fontFamily: Newsreader
    fontWeight: 560
    letterSpacing: -0.025em
  title:
    fontFamily: Newsreader
    fontWeight: 600
  body:
    fontFamily: Instrument Sans
    fontSize: 16px
    lineHeight: 1.6
rounded:
  base: 4px
  frame: 4px
spacing:
  s1: 4px
  s2: 8px
  s3: 12px
  s4: 16px
  s5: 24px
  s6: 32px
  s7: 48px
---

# Bench-X

## Overview

Bench-X aide à choisir un modèle pour une tâche précise à partir de preuves lisibles. Le ton visuel est celui d'un journal d'essai : papier froid, titres à empattements, une seule couleur d'interaction (cobalt), le parcours lu comme une colonne d'étapes.

Direction B retenue par Ayo le 30 octobre 2026, à partir de la maquette `reports/refonte-ui/` (ignorée par Git). Elle remplace le style S18. Ce fichier est la seule source de vérité visuelle. Toute valeur vient d'une variable de `benchmark_web/static/preparation.css`.

Les pages publiées utilisent `benchmark_web/static/projection.css`. Cette feuille reprend les mêmes couleurs, en thème clair et sombre. Elle utilise la police système, car le paquet publié ne contient aucune police. Ses octets sont liés à la version de présentation : toute modification incrémente `PRESENTATION_VERSION` dans `benchmark/publications.py`.

Principes :

1. **L'état d'abord.** Chaque écran commence par son état principal, en clair. La raison suit, en une ou deux phrases.
2. **Une seule décision visible par écran**, dans la zone de synthèse au fond `accent-soft`.
3. **Le rouge signale un manque.** `ko` sert seulement à un manquement, à une condition non remplie ou à un point à relire. `ok` sert seulement à une condition remplie.
4. **Aucun chiffre inventé.** Une estimation est nommée « estimation ». Une valeur inconnue est affichée « inconnu ». Aucun score global.
5. **Aucune étiquette en capitales, aucune police à chasse fixe pour les données.** Les chiffres sont tabulaires dans la police du texte. La chasse fixe reste réservée au code et aux pièces copiées telles quelles.
6. **Le détail rigoureux se replie.** Une aide ne s'ouvre jamais au seul survol (`docs/PRD.md`).

## Colors

- **Paper** sert de fond de page. **Surface** sert de fond aux encadrés et aux pièces. **Soft** sert de fond de survol et de champ désactivé.
- **Accent** (cobalt) est la couleur d'interaction : liens, bouton principal, entrée de menu courante, étape courante. Aucune autre teinte d'accent.
- **Accent-soft** est le fond de la zone de synthèse et de l'encadré d'action.
- **Fait** marque une étape déjà franchie. Il ne porte aucune information seul : l'étape garde son nom.
- Les paires de statut (`ok`, `ko`, `warn`, `unk`, `wait`, chacune avec sa variante `-soft`) portent un verdict ou un état, jamais une décoration. Un statut s'accompagne toujours d'un libellé, pas de la couleur seule.
- **Warm** (violet) est réservé à ce qui est inventé : consigne donnée aux modèles, marque « cas d'usage inventé ».
- **Focus** (ambre) est réservé au contour du focus clavier.
- `line` et `line-soft` ne portent aucune information seule. Un bord de champ utilise `ink-2`.

Contrastes mesurés (texte sur fond), thème clair et thème sombre :

| Texte | Fond | Clair | Sombre |
|---|---|---|---|
| ink | paper / surface | 15,65:1 / 16,78:1 | 15,45:1 / 14,01:1 |
| ink-2 | paper / surface | 7,62:1 / 8,17:1 | 8,87:1 / 8,04:1 |
| muted | soft | 7,04:1 | 7,11:1 |
| accent-ink | surface | 6,73:1 | 8,13:1 |
| ink | accent-soft | 14,13:1 | 12,14:1 |
| ko / ok / warn | leur fond `-soft` | 5,53:1 / 5,29:1 / 6,46:1 | 7,45:1 / 7,94:1 / 8,94:1 |
| warm | warm-soft | 6,50:1 | 8,65:1 |
| on-btn | btn | 6,73:1 | 8,96:1 |
| focus | paper | 4,68:1 | 8,93:1 |
| fait (élément graphique) | surface | 3,34:1 | 3,16:1 |

`tests/test_web_redesign.py` vérifie ces paires à chaque exécution.

## Themes

Le thème sombre suit `prefers-color-scheme: dark`, avec les mêmes noms de variables. Le cobalt s'éclaircit et le bouton principal passe en texte sombre sur cobalt clair.

| Variable | Clair | Sombre |
|---|---|---|
| paper | #f6f7f9 | #111318 |
| surface | #ffffff | #1a1d25 |
| soft | #eceef3 | #232733 |
| line | #d9dce4 | #2c303c |
| line-soft | #e8eaf0 | #23262f |
| line-2 | #767c8c | #7d8396 |
| ink | #1a1d29 | #e8eaf0 |
| ink-2 | #4a4f5e | #aeb3c2 |
| muted | #4a4f5e | #aeb3c2 |
| accent | #2f4bd8 | #9fb0ff |
| accent-ink | #2f4bd8 | #9fb0ff |
| accent-soft | #e7ebfc | #20264a |
| accent-soft-2 | #eef1fd | #1d2240 |
| btn | #2f4bd8 | #9fb0ff |
| btn-hover | #1f32a0 | #d6ddff |
| on-btn | #ffffff | #0f1222 |
| warm | #6a3d9a | #d3b6f2 |
| warm-soft | #f1eaf8 | #2a2036 |
| ok | #1e6e4e | #7fd3ac |
| ok-soft | #e1f1ea | #15302a |
| ko | #b42318 | #ff9b90 |
| ko-soft | #fbe7e5 | #3d1d1a |
| warn | #7a4b00 | #f1c27a |
| warn-soft | #fbeed6 | #33260f |
| unk | #4a4f5e | #aeb3c2 |
| unk-soft | #eceef3 | #232733 |
| wait | #2f4bd8 | #9fb0ff |
| wait-soft | #e7ebfc | #20264a |
| focus | #b45309 | #f0a35a |
| fait | #7387e2 | #5866b0 |

## Typography

| Rôle | Police | Réglage | Où |
|---|---|---|---|
| Grands titres | Newsreader | 560, axe optique 72, espacement −0,025 em | `h1` |
| Autres titres | Newsreader | 600 | `h2` à `h6`, nom de la configuration conseillée, noms des cas d'usage, consigne |
| Texte | Instrument Sans | 400 à 700, 16 px, interligne 1,6, chiffres tabulaires | paragraphes, menu, étapes, tableaux, badges, montants |
| Code et pièces copiées | police à chasse fixe du système | 0,86 à 0,88 em | `code`, `pre`, texte des pièces et des preuves |

Les deux polices sont sous licence SIL Open Font License 1.1. Elles sont servies en local depuis `/preparation/fonts/`, chacune avec son fichier `OFL-<police>.txt`. Les noms de fichier ne contiennent que des lettres, comme l'exige la route du serveur.

## Layout

```
ordinateur (64 rem et plus)
┌ en-tête : marque, menu ─────────────────────────────────────┐
│ ● Besoin      │ Titre                                       │
│ ● Exemple     │ état principal                              │
│ ◉ Validation  │ ┌ zone principale ───────┐ ┌ synthèse ────┐ │
│ ○ Modèles     │ │ contenu                │ │ décision     │ │
│ ○ Résultats   │ └────────────────────────┘ └──────────────┘ │
└ pied : légal, code source ──────────────────────────────────┘

téléphone
┌ en-tête ┐
│ barre de cinq segments nommés │
│ titre, état, synthèse, contenu │
└ pied ┘
```

- L'en-tête porte la marque et le menu sur une ligne, au-dessus de la page.
- Ordinateur : les étapes du parcours (Besoin, Exemple, Validation, Modèles, Résultats) forment une colonne de 13 rem à gauche, qui reste visible au défilement. Cercle plein pour une étape faite, cercle évidé épais et cobalt pour l'étape courante, cercle fin pour une étape à venir.
- Sous 64 rem : les étapes forment une barre de cinq segments nommés au-dessus du titre.
- Le gabarit `templates/preparation.html` garde son HTML. `header.site` reste un vrai bloc d'en-tête, sans `display: contents`, pour garder son rôle de bannière.
- Chaque page se lit à 390 px sans défilement horizontal de la page. Seul un tableau peut défiler dans son propre conteneur.

## Shapes

Un seul rayon de 4 px (`rounded.base`) pour les champs, boutons et segments. Les badges ont un rayon de 2 px. Les encadrés d'état n'ont pas de cadre : un trait de 4 px à gauche, de la teinte du statut.

## Components

- **Encadré d'état** : un seul modèle d'encadré, réservé aux états et alertes. Trait de 4 px à gauche, ton venu des paires de statut, toujours un titre. Une note ordinaire est un paragraphe, pas un encadré.
- **Badges de verdict** : pastille de la couleur du statut sur son fond `-soft`, icône puis mot, en casse normale.
- **Boutons** : 48 px de haut minimum. Le bouton principal est plein (`btn`). Le bouton secondaire (`.sec`) a un fond transparent et un bord cobalt. Un seul bouton principal par écran.
- **Champs** : libellé visible au-dessus, bord `ink-2`, 48 px de haut minimum. Une erreur porte `aria-invalid="true"` et un bord `ko` de 2 px.
- **Navigation** : le menu marque la page courante avec `aria-current="page"` seulement quand la page est réellement l'entrée du menu. L'entrée courante est en gras, soulignée d'un trait cobalt de 3 px.
- **Tableaux** : en-têtes en gras sur un filet de 2 px, filets pleins entre les lignes, chiffres tabulaires.
- **Listes de cas d'usage** : une ligne par cas, séparée par un filet, nom en Newsreader.
- **Zone principale et colonne de synthèse** (`.workbench`, `fragments.workbench`) : les réglages ou le détail à gauche, la synthèse et l'action à droite, sur fond `accent-soft`, dans une colonne qui reste visible au défilement. Sous 64 rem, une seule colonne dans l'ordre du HTML. La synthèse vient d'abord dans le HTML quand elle doit être lue avant le détail.
- **Relevé** (`.readout`, `fragments.readout`) : un chiffre déjà connu, en grand dans la police des titres, toujours nommé au-dessus en casse normale. Jamais un chiffre calculé pour l'affichage.
- **Récapitulatif avant dépense** : une ligne de synthèse, puis le bouton qui lance, puis le détail. La prévision des réponses et la réservation de l'évaluation s'affichent séparément, jamais additionnées.
- **Dépliants** (`details`) : pour le détail rigoureux, les preuves et les options secondaires ; filets en haut et en bas, chevron à gauche ; ils restent utilisables sans JavaScript.
- **Pied de page** : quatre colonnes (deux sur tablette, une sur téléphone), titres en gras. Le lien « Code source » répond à l'offre de source de l'AGPL §13 et ne doit jamais disparaître.

## Interactions

- Survol : le fond change ou le soulignement s'épaissit, jamais la couleur du texte vers un gris plus clair.
- Focus : contour `focus` de 3 px, décalé de 2 px, sur tout élément interactif.
- Transitions : 200 ms, seulement sur le fond des boutons et le chevron des dépliants. Aucune sous `prefers-reduced-motion`.
- Zones tactiles : 44 px minimum.

## Do's and Don'ts

- Ne pas écrire d'étiquette en capitales, ni de donnée en chasse fixe.
- Ne pas ajouter de seconde couleur d'accent, de dégradé, de halo ni d'ombre décorative.
- Ne pas signaler un statut par la couleur seule.
- Ne pas ajouter de colonne au tableau des résultats ni de paragraphe explicatif sous lui ; le détail va dans l'infobulle.
- Ne pas afficher les alias internes de configuration ; utiliser les noms commerciaux.
- Ne pas exiger JavaScript pour lire, saisir ou naviguer ; aucune ressource externe ; aucun style ni script inline qui demanderait `unsafe-inline` dans la CSP.
- Respecter `prefers-reduced-motion`.
