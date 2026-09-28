---
version: alpha
name: Bench-X
description: Parcours web de Bench-X, du besoin à la comparaison des configurations et à leurs preuves.
colors:
  paper: "#f5f3ec"
  surface: "#ffffff"
  soft: "#ebe8df"
  line: "#d8d4c8"
  line-2: "#847d6d"
  ink: "#1c1f1a"
  ink-2: "#3f463c"
  muted: "#5f665c"
  accent: "#356b3a"
  accent-ink: "#27522c"
  accent-soft: "#e3eedf"
  accent-soft-2: "#cfe2c9"
  btn: "#356b3a"
  btn-hover: "#27522c"
  on-btn: "#ffffff"
  warm: "#985a0d"
  warm-soft: "#f8ead5"
  ok: "#157454"
  ok-soft: "#dcf1e7"
  ko: "#b0392b"
  ko-soft: "#f8e1dc"
  warn: "#865700"
  warn-soft: "#f9edd0"
  unk: "#5b6570"
  unk-soft: "#e5e9ec"
  wait: "#3d6a8c"
  wait-soft: "#e0ecf5"
  focus: "#1a4fb8"
typography:
  body:
    fontFamily: Atkinson Hyperlegible Next
    fontSize: 16px
    lineHeight: 1.55
  mono:
    fontFamily: Atkinson Hyperlegible Mono
rounded:
  base: 5px
omitted:
  - section: spacing
    reason: Aucune échelle d'espacement nommée dans preparation.css
---

# Bench-X

## Overview

Bench-X aide à choisir un modèle pour une tâche précise à partir de preuves lisibles. Le lecteur ne connaît ni le harnais ni les règles : quand la rigueur et la lisibilité s'opposent à l'écran, la lisibilité gagne et le détail rigoureux passe dans un dépliant ou une infobulle. Le ton visuel est celui d'un rapport sobre : surfaces plates, filets fins, un seul accent vert, la donnée avant la décoration.

## Colors

- **Paper** sert de fond de page, **Surface** de fond aux blocs qui contiennent une saisie, une pièce ou un tableau.
- **Accent** est la seule couleur d'interaction : liens, bouton principal, étape courante.
- Les paires de statut (`ok`, `ko`, `warn`, `unk`, `wait`, chacune avec sa variante `-soft`) portent un verdict ou un état, jamais une décoration. Un statut s'accompagne toujours d'un libellé ou d'une icône, pas de la couleur seule.
- **Warm** est réservé à ce qui est inventé : consigne donnée aux modèles, marque « cas d'usage inventé ».
- `projection.css` suit les mêmes tokens que la page privée.

## Themes

Le thème sombre suit `prefers-color-scheme: dark`, avec les mêmes noms de tokens.

| Token | Clair | Sombre |
|---|---|---|
| paper | #f5f3ec | #15181a |
| surface | #ffffff | #1d2124 |
| soft | #ebe8df | #252a2e |
| line | #d8d4c8 | #333a3f |
| line-2 | #847d6d | #78828a |
| ink | #1c1f1a | #eceee9 |
| ink-2 | #3f463c | #c6cbc4 |
| muted | #5f665c | #98a099 |
| accent | #356b3a | #8fc283 |
| accent-ink | #27522c | #a9d69d |
| accent-soft | #e3eedf | #233326 |
| accent-soft-2 | #cfe2c9 | #2d4531 |
| btn | #356b3a | #3f7a45 |
| btn-hover | #27522c | #285c32 |
| on-btn | #ffffff | #ffffff |
| warm | #985a0d | #e0a24a |
| warm-soft | #f8ead5 | #3a2c14 |
| ok | #157454 | #6fcaa6 |
| ok-soft | #dcf1e7 | #173a2d |
| ko | #b0392b | #ec9484 |
| ko-soft | #f8e1dc | #43231d |
| warn | #865700 | #e3b45a |
| warn-soft | #f9edd0 | #3a2f12 |
| unk | #5b6570 | #aab3bc |
| unk-soft | #e5e9ec | #2a3136 |
| wait | #3d6a8c | #8fbde0 |
| wait-soft | #e0ecf5 | #1d2e3c |
| focus | #1a4fb8 | #8ab4f8 |

## Typography

- Une seule famille pour le texte et les titres : Atkinson Hyperlegible Next, choisie pour la lisibilité. Les titres se distinguent par la graisse et la taille, pas par une police d'affichage.
- Atkinson Hyperlegible Mono pour la consigne, les pièces, les sorties et les identifiants.
- Les nombres comparés (coûts, durées, décomptes) utilisent des chiffres tabulaires.
- Les polices sont servies en local depuis `/preparation/fonts/`.

## Layout

- Colonne de lecture unique centrée ; la largeur utile revient au contenu, en particulier au tableau des résultats.
- Les étapes du parcours (Besoin, Exemple, Validation, Modèles, Résultats) forment une barre horizontale compacte au-dessus du titre, pas une colonne latérale.
- Chaque page se lit à 390 px de large sans défilement horizontal de la page ; seul un tableau peut défiler dans son propre conteneur.

## Shapes

Un seul rayon, `rounded.base`, pour les champs, boutons, blocs et dépliants. Pas de pilule ni de pastille ronde décorative.

## Components

- **Encadré d'état** : un seul modèle d'encadré, réservé aux états et alertes (où en est le cas d'usage, erreur, avertissement). Le ton vient des paires de statut. Une note ou une explication ordinaire est un paragraphe, pas un encadré.
- **Boutons** : un seul bouton principal par écran, pour l'action qui fait avancer le parcours. Revenir en arrière ou changer de page est un lien, pas un bouton principal.
- **Navigation** : le menu d'en-tête marque la page courante avec `aria-current="page"` seulement quand la page est réellement l'entrée du menu. Toute page atteignable a au moins un lien entrant visible et un lien de retour vers son parent.
- **Résultats** : chaque ligne nomme le candidat entier (nom commercial et effort déclaré). Les caractéristiques de test (route servie, paramètres omis, budget) vont dans une infobulle par ligne.
- **Dépliants** (`details`) : pour le détail rigoureux, les preuves et les options secondaires ; ils restent utilisables sans JavaScript.

## Do's and Don'ts

- Ne pas utiliser Syne ni une autre police d'affichage.
- Ne pas mettre de petits titres en capitales décoratives.
- Ne pas utiliser de dégradé, de cartes numérotées ni d'icône dans un rond teinté.
- Ne pas ajouter de colonne au tableau des résultats ni de paragraphe explicatif sous lui ; le détail va dans l'infobulle.
- Ne pas afficher les alias internes de configuration ; utiliser les noms commerciaux.
- Ne pas utiliser d'échelle logarithmique ni de bandes de couleur par colonne.
- Ne pas exiger JavaScript pour lire, saisir ou naviguer ; aucune ressource externe ; aucun style ni script inline qui demanderait `unsafe-inline` dans la CSP.
- Respecter `prefers-reduced-motion`.
