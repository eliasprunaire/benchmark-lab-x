---
version: s18
name: Bench-X
description: Parcours web de Bench-X, du besoin à la comparaison des configurations et à leurs preuves. Style S18 « instrument ».
colors:
  paper: "#e9e8e3"
  surface: "#fbfbf9"
  soft: "#dddcd5"
  line: "#c9c8c0"
  line-soft: "#e2e1db"
  line-2: "#77766e"
  ink: "#121212"
  ink-2: "#45453f"
  muted: "#55554e"
  accent: "#121212"
  accent-ink: "#121212"
  accent-soft: "#e2e1db"
  accent-soft-2: "#c9c8c0"
  btn: "#121212"
  btn-hover: "#3d3d37"
  on-btn: "#fbfbf9"
  warm: "#7d4710"
  warm-soft: "#f1e6d8"
  ok: "#24613a"
  ok-soft: "#e1ece3"
  ko: "#b0241a"
  ko-soft: "#f6e5e2"
  warn: "#765000"
  warn-soft: "#f2e8d2"
  unk: "#4b4f55"
  unk-soft: "#e4e4e0"
  wait: "#2c5677"
  wait-soft: "#e1e8ee"
  focus: "#1f4fd6"
typography:
  display:
    fontFamily: Mona Sans
    fontWeight: 700
    fontStretch: 115%
    letterSpacing: -0.02em
  title:
    fontFamily: Hubot Sans
    fontWeight: 600
  body:
    fontFamily: Host Grotesk
    fontSize: 16px
    lineHeight: 1.55
  mono:
    fontFamily: Geist Mono
  pixel:
    fontFamily: Silkscreen
    fontWeight: 700
rounded:
  base: 8px
  frame: 12px
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

Bench-X aide à choisir un modèle pour une tâche précise à partir de preuves lisibles. Le ton visuel est celui d'un instrument de mesure : papier gris neutre, encre noire, étiquettes en chasse fixe, chiffres alignés, filets fins. Aucun décor.

Direction retenue par Ayo le 26 octobre 2026 : le shot Dribbble S18 (Purrweb) pour le style, le shot S1 (Groundwork) pour la structure. Ce fichier est la seule source de vérité visuelle. Toute valeur vient d'une variable de `benchmark_web/static/preparation.css`.

Les pages publiées utilisent `benchmark_web/static/projection.css`. Cette feuille reprend les mêmes couleurs, en thème clair et sombre. Elle utilise la police système, car le paquet publié ne contient aucune police. Ses octets sont liés à la version de présentation : toute modification incrémente `PRESENTATION_VERSION` dans `benchmark/publications.py`.

Principes :

1. **L'état d'abord.** Chaque écran commence par son état principal, en clair. La raison suit, en une ou deux phrases.
2. **Le rouge signale un manque.** `ko` sert seulement à un manquement, à une condition non remplie ou à un point à relire. `ok` sert seulement à une condition remplie.
3. **Aucun chiffre inventé.** Une estimation est nommée « estimation ». Une valeur inconnue est affichée « inconnu ». Aucun score global.
4. **La police pixel est rare.** Elle sert seulement à quelques grands chiffres de relevé. Jamais pour un titre ni pour du texte.

## Colors

- **Paper** sert de fond de page. **Surface** sert de fond au rail et à la fenêtre d'application. **Soft** sert de fond appuyé.
- **Ink** est la couleur d'interaction : liens, bouton principal, entrée de menu courante, étape courante. Aucune autre teinte d'accent.
- Les paires de statut (`ok`, `ko`, `warn`, `unk`, `wait`, chacune avec sa variante `-soft`) portent un verdict ou un état, jamais une décoration. Un statut s'accompagne toujours d'un libellé, pas de la couleur seule.
- **Warm** est réservé à ce qui est inventé : consigne donnée aux modèles, marque « cas d'usage inventé ».
- **Focus** est le seul bleu de l'interface : contour du focus clavier.
- `line` et `line-soft` ne portent aucune information seule. Un bord de champ utilise `ink-2`.

Contrastes mesurés (texte sur fond), thème clair et thème sombre :

| Texte | Fond | Clair | Sombre |
|---|---|---|---|
| ink | paper / surface | 15,27:1 / 18,08:1 | 15,46:1 / 14,09:1 |
| ink-2 | paper / surface | 7,87:1 / 9,31:1 | 10,79:1 / 9,84:1 |
| muted | soft | 5,46:1 | 5,96:1 |
| ko / ok / warn | leur fond `-soft` | 5,53:1 / 6,08:1 / 5,91:1 | 6,11:1 / 6,78:1 / 6,86:1 |
| on-btn | btn | 18,08:1 | 15,98:1 |
| focus | paper | 5,44:1 | 8,60:1 |

`tests/test_web_redesign.py` vérifie ces paires à chaque exécution.

## Themes

Le thème sombre suit `prefers-color-scheme: dark`, avec les mêmes noms de variables. L'encre claire devient la couleur d'interaction et le bouton principal passe en encre claire sur texte sombre.

| Variable | Clair | Sombre |
|---|---|---|
| paper | #e9e8e3 | #161614 |
| surface | #fbfbf9 | #1f1f1c |
| soft | #dddcd5 | #2a2a26 |
| line | #c9c8c0 | #3a3a35 |
| line-soft | #e2e1db | #2c2c28 |
| line-2 | #77766e | #8a897f |
| ink | #121212 | #eeede8 |
| ink-2 | #45453f | #c9c8c0 |
| muted | #55554e | #a8a79e |
| accent | #121212 | #eeede8 |
| accent-ink | #121212 | #f4f3ee |
| accent-soft | #e2e1db | #2c2c28 |
| accent-soft-2 | #c9c8c0 | #3a3a35 |
| btn | #121212 | #eeede8 |
| btn-hover | #3d3d37 | #b9b8b0 |
| on-btn | #fbfbf9 | #121212 |
| warm | #7d4710 | #e0a24a |
| warm-soft | #f1e6d8 | #3a2c14 |
| ok | #24613a | #7dc795 |
| ok-soft | #e1ece3 | #1b3324 |
| ko | #b0241a | #ef8f84 |
| ko-soft | #f6e5e2 | #43201b |
| warn | #765000 | #e3b45a |
| warn-soft | #f2e8d2 | #3a2f12 |
| unk | #4b4f55 | #b3b6ba |
| unk-soft | #e4e4e0 | #2a2c2f |
| wait | #2c5677 | #8fbde0 |
| wait-soft | #e1e8ee | #1d2e3c |
| focus | #1f4fd6 | #8ab4f8 |

## Typography

| Rôle | Police | Réglage | Où |
|---|---|---|---|
| Grands titres | Mona Sans | gras 700, largeur 115 %, espacement −0,02 em | `h1`, titre du conseil |
| Autres titres | Hubot Sans | demi-gras 600 | `h2` à `h6`, noms des configurations, cas d'usage |
| Texte | Host Grotesk | 400, 16 px, interligne 1,55 | paragraphes, descriptions, tableaux |
| Étiquettes et chiffres | Geist Mono | 11 à 14 px ; capitales espacées pour les étiquettes ; chiffres tabulaires | menu, étapes, en-têtes de tableau, badges, montants, consigne et pièces |
| Grands chiffres de relevé | Silkscreen | gras 700 | réservé ; aucun écran ne l'utilise encore |

Les cinq polices sont sous licence SIL Open Font License 1.1. Elles sont servies en local depuis `/preparation/fonts/`, chacune avec son fichier `OFL-<police>.txt`. Les noms de fichier ne contiennent que des lettres, comme l'exige la route du serveur.

## Layout

```
rail latéral (marque, menu)  │  fenêtre d'application (étapes, titre, contenu)
──────────────────────────────┴──────────────────────────────────────────────
pied de page (présentation, parcours, informations légales, confidentialité, code source)
```

- Ordinateur : le rail occupe 12 rem à gauche. Il porte la marque et le menu en colonne. La fenêtre d'application occupe le reste. Le rail et la fenêtre forment un seul cadre `surface` au bord `line`.
- 52 rem et moins : le rail passe au-dessus de la fenêtre et son menu passe en ligne.
- Le gabarit `templates/preparation.html` garde son HTML. La grille CSS place l'en-tête, la fenêtre et le pied de page. `header.site` reste un vrai bloc d'en-tête, sans `display: contents`, pour garder son rôle de bannière.
- Les étapes du parcours (Besoin, Exemple, Validation, Modèles, Résultats) forment une barre horizontale en chasse fixe au-dessus du titre.
- Chaque page se lit à 390 px sans défilement horizontal de la page. Seul un tableau peut défiler dans son propre conteneur.

## Shapes

Deux rayons : `rounded.base` (8 px) pour les champs, boutons et dépliants, `rounded.frame` (12 px) pour le cadre de l'application et les encadrés d'état. Les étiquettes et badges ont un rayon de 4 px.

## Components

- **Encadré d'état** : un seul modèle d'encadré, réservé aux états et alertes. Le ton vient des paires de statut. Une note ordinaire est un paragraphe, pas un encadré.
- **Badges de verdict** : capsule rectangulaire en chasse fixe et capitales, bord de la couleur du statut, toujours avec son mot.
- **Boutons** : 48 px de haut minimum. Le bouton principal est plein (`btn`). Le bouton secondaire (`.sec`) a un fond transparent et un bord encre. Un seul bouton principal par écran.
- **Champs** : libellé visible au-dessus, bord `ink-2`, 48 px de haut minimum. Une erreur porte `aria-invalid="true"` et un bord `ko` de 2 px.
- **Navigation** : le menu marque la page courante avec `aria-current="page"` seulement quand la page est réellement l'entrée du menu. L'entrée courante a un filet encre et un fond `paper`.
- **Tableaux** : en-têtes en chasse fixe et capitales, filets pointillés entre les lignes, montants en chasse fixe et chiffres tabulaires.
- **Listes de cas d'usage** : une ligne par cas, séparée par un filet, nom en Hubot Sans.
- **Zone principale et colonne de synthèse** (`.workbench`, `fragments.workbench`) : les réglages ou le détail à gauche, la synthèse et l'action à droite, dans une colonne qui reste visible au défilement. Sous 64rem, une seule colonne dans l'ordre du HTML. La synthèse vient d'abord dans le HTML quand elle doit être lue avant le détail.
- **Relevé** (`.readout`, `fragments.readout`) : un chiffre déjà connu, en grand et en chasse fixe, toujours nommé au-dessus. Jamais un chiffre calculé pour l'affichage.
- **Récapitulatif avant dépense** : une ligne de synthèse, puis le bouton qui lance, puis le détail. La prévision des réponses et la réservation de l'évaluation s'affichent séparément, jamais additionnées.
- **Dépliants** (`details`) : pour le détail rigoureux, les preuves et les options secondaires ; ils restent utilisables sans JavaScript.
- **Pied de page** : quatre colonnes (deux sur tablette, une sur téléphone), titres en chasse fixe. Le lien « Code source » répond à l'offre de source de l'AGPL §13 et ne doit jamais disparaître.

## Interactions

- Survol : le fond change, jamais la couleur du texte vers un gris plus clair.
- Focus : contour `focus` de 3 px, décalé de 2 px, sur tout élément interactif.
- Transitions : 200 ms, seulement sur le fond et la flèche des dépliants. Aucune sous `prefers-reduced-motion`.
- Zones tactiles : 44 px minimum.

## Do's and Don'ts

- Ne pas utiliser la police pixel pour un titre ou du texte.
- Ne pas ajouter de seconde couleur d'accent, de dégradé, de halo ni d'ombre décorative.
- Ne pas signaler un statut par la couleur seule.
- Ne pas ajouter de colonne au tableau des résultats ni de paragraphe explicatif sous lui ; le détail va dans l'infobulle.
- Ne pas afficher les alias internes de configuration ; utiliser les noms commerciaux.
- Ne pas exiger JavaScript pour lire, saisir ou naviguer ; aucune ressource externe ; aucun style ni script inline qui demanderait `unsafe-inline` dans la CSP.
- Respecter `prefers-reduced-motion`.
