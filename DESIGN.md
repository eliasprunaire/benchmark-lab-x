---
version: c
name: Evaly
description: Parcours web d'Evaly, du cas d'usage à la comparaison des configurations et à leurs preuves. Direction « Banc d'essai » (refonte C).
colors:
  papier: "#f1eee5"
  carte: "#fffdf8"
  creux: "#e7e3d8"
  encre: "#141311"
  encre-2: "#48453d"
  discret: "#5b574d"
  filet: "#141311"
  filet-doux: "#d3cebf"
  signal: "#ffd21f"
  signal-pale: "#fff0b0"
  sur-signal: "#141311"
  ok: "#13703a"
  ok-fond: "#dcf0e1"
  ko: "#bf2419"
  ko-fond: "#fbe3df"
  att: "#1d52b0"
  att-fond: "#e1e9f8"
  vig: "#845400"
  vig-fond: "#f6ead0"
  inc: "#4f545b"
  inc-fond: "#e6e5e0"
  ruban: "#e6d3a6"
  sur-ruban: "#3a2a0b"
  focus: "#1f47e0"
  nuit: "#0f0e0d"
  sur-nuit: "#f1eee5"
  nuit-2: "#bdb7a8"
  nuit-filet: "#34312b"
typography:
  titre:
    fontFamily: Mona Sans
    fontWeight: 800
    fontStretch: 112%
    letterSpacing: -0.02em
  corps:
    fontFamily: Mona Sans
    fontWeight: 400
    fontSize: 16px
    lineHeight: 1.55
  mono:
    fontFamily: Geist Mono
  pixel:
    fontFamily: Silkscreen
    fontWeight: 700
rounded:
  carte: 14px
  bouton: 12px
  ligne: 8px
spacing:
  gout: clamp(16px, 4vw, 40px)
  cadre: 1240px
---

# Evaly

> **Ce fichier remplace la direction S18 « instrument »** (Bench-X, 26 octobre 2026). Le rail latéral, le papier gris neutre, l'accent unique en encre, les trois polices de texte et l'interdiction du mouvement n'existent plus. Les règles de preuve de S18 (l'état d'abord, aucun chiffre inventé, un statut porte son mot) restent : voir « Invariants ». Le nom Bench-X ne s'affiche plus, le produit s'appelle Evaly.

## Vue d'ensemble

Evaly aide à choisir un modèle pour un cas d'usage précis à partir de preuves lisibles. Le ton visuel est celui d'un banc d'essai : papier crème, encre noire, filets épais, un seul jaune de signal, des tampons de verdict. Le mouvement existe, mais il illustre le test (un disque qui court, un tampon qui frappe) et ne décore rien.

Source de vérité : la feuille `benchmark_web/static/preparation.css`. Elle concatène la feuille de base de la maquette (`reports/refonte-c/maquette/static/app.css`) puis les feuilles par écran (`static/ecrans/*.css`, ordre alphabétique). Toute valeur vient d'une variable de `:root`. Les composants HTML vivent dans `benchmark_web/fragments.py`, qui reprend `reports/refonte-c/maquette/mq/kit.py`.

Les pages publiées utilisent `benchmark_web/static/projection.css`. Cette feuille garde sa propre palette. Ses octets sont liés à la version de présentation : toute modification incrémente `PRESENTATION_VERSION` dans `benchmark/publications.py`.

## Couleurs

- **Papier** est le fond de page. **Carte** est le fond des cartes, des champs et des boutons secondaires. **Creux** est le fond appuyé (lignes d'état, cases, tableaux).
- **Encre** sert au texte, aux filets de 2 px et à l'entrée de menu courante. **Encre-2** et **discret** servent au texte secondaire.
- **Signal** (jaune) est le seul accent. Il marque l'action principale, la station courante, la pastille d'état et le Y de la marque. Le texte sur signal est toujours `sur-signal`. `signal-pale` marque aussi un état vide, sans valeur d'action.
- Cinq tons portent un verdict ou un état, chacun avec son fond : `ok`, `ko`, `att` (en attente, à reprendre), `vig` (vigilance), `inc` (inconnu). Un ton s'accompagne toujours d'un mot et d'une icône.
- **Ruban** marque ce qui est inventé : pièces, consigne, exemple. Le texte sur ruban est `sur-ruban`.
- **Nuit** fond les bandes sombres : banc d'essai de l'accueil, suivi en direct, pied de page. Le texte y est `sur-nuit` ou `nuit-2`.
- **Focus** est le seul bleu de l'interface. Sur fond jaune, le contour de focus reste `#1f47e0`.
- Le thème sombre suit `prefers-color-scheme: dark`, avec les mêmes noms. Il redéfinit `papier`, `carte`, `creux`, `encre`, `encre-2`, `discret`, `filet`, `filet-doux`, `signal-pale`, `sur-signal`, les cinq tons et leurs fonds, `ruban`, `sur-ruban`, `focus`, `nuit` et `nuit-filet`. `signal` reste jaune dans les deux thèmes.

Contrastes mesurés (texte sur fond), minimum 4,5:1 hors focus sur jaune (4,77:1) :

| Texte | Fond | Clair | Sombre |
|---|---|---|---|
| encre | papier | 16,00:1 | 16,28:1 |
| encre-2 | papier | 8,25:1 | 11,06:1 |
| discret | papier / carte / creux | 6,21 / 7,09 / 5,62 | 7,68 / 7,02 / 6,24 |
| ok, ko, att, vig, inc | leur fond | 4,93:1 à 6,05:1 | 6,95:1 à 8,43:1 |
| sur-signal | signal | 12,81:1 | 13,01:1 |
| sur-ruban | ruban | 9,39:1 | 7,42:1 |
| sur-nuit | nuit | 16,62:1 | 18,10:1 |
| focus | papier | 5,96:1 | 8,96:1 |

`tests/test_web_redesign.py` vérifie ces paires à chaque exécution. Le tableau complet est dans `reports/refonte-c/contrastes.md`.

## Typographie

| Rôle | Police | Réglage | Où |
|---|---|---|---|
| Titres | Mona Sans | 800, largeur 112 %, espacement −0,02 em ; `h1` en 850 et largeur 125 % | `h1` à `h4`, marque, boutons |
| Texte | Mona Sans | 400, 16 px, interligne 1,55 | paragraphes, champs, tableaux |
| Étiquettes | Geist Mono | 11 à 13 px, capitales espacées | menu, étiquettes, mots de statut, notes de relevé |
| Compteurs | Silkscreen | 700 | seulement le résumé de la barre de choix des modèles |

Montants, durées, numéros d'étape et codes d'erreur utilisent Mona Sans à chiffres tabulaires. Le 4 de Silkscreen se lit comme un bloc plein.

Les polices sont sous licence SIL Open Font License 1.1. Elles sont servies en local depuis `/preparation/fonts/`, chacune avec son fichier `OFL-<police>.txt`. Les fichiers HubotSans et HostGrotesk restent servis, car les mentions légales les nomment, mais aucune feuille ne les charge.

## Mise en page

```
en-tête fixe : marque EVAL + Y, menu (Accueil, Mes cas d'usage, Mes données, Benchmarks publics)
────────────────────────────────────────────────────────────────────────────────
tête d'écran : piste des cinq stations, titre h1, puces de contexte
contenu : colonne principale  │  colonne de synthèse collante (.cote)
────────────────────────────────────────────────────────────────────────────────
pied de page : marque, Parcours, Informations légales, grand EVALY, bande basse
```

- `.cadre` centre le contenu : 1240 px au plus, gouttière `clamp(16px, 4vw, 40px)`. Les bandes pleine largeur (`.zone`, `.une`, `.compo-zone`, `.pied`) portent leur propre `.cadre`.
- Le gabarit `templates/preparation.html` écrit l'en-tête, le pied et le marqueur `{{tete}}`. `views.render` remplit `{{tete}}` (piste, titre, puces de contexte) et enveloppe le contenu dans un `.cadre`. Une vue qui écrit son propre h1 et ses propres bandes (accueil, Mes cas d'usage) passe `plein`.
- Le menu est une liste. Sous 860 px, il devient un dépliant « Menu ». `aria-current="page"` marque seulement l'entrée qui est la page affichée, jamais une page descendante ni une page d'erreur.
- Le pied de page a trois colonnes (deux sous 860 px, une sous 480 px) : la marque et sa tagline, sans note, puis « Parcours » et « Informations légales ». Viennent ensuite le mot EVALY en grand, puis une bande en trois zones : « Par Cybrel · né au Lab-X », la version et la source, le lien « Code source ».
- Les cinq stations du cas (Besoin, Exemple, Validation, Modèles, Résultats) forment la piste. Les stations franchies sont des liens. Sous 700 px, seul le nom de la station courante reste visible.
- Deux colonnes (`.colonnes`) : 360 px à droite pour l'état, la décision et le coût. Sous 1000 px, une seule colonne. `.cote-avant` remonte la colonne de droite avant le détail.
- Chaque page se lit à 390 px sans défilement horizontal de la page. Seul un tableau défile dans son propre conteneur.

## Formes

Trois rayons : 14 px (`--r`) pour les cartes, les états et les tableaux ; 12 px pour les boutons et les champs ; 8 px (`--r-s`) pour les lignes d'état et les petits blocs. Les filets de structure font 2 px en `filet`. Les séparations internes sont en pointillés `filet-doux`.

## Composants

Chaque composant HTML a sa fonction dans `benchmark_web/fragments.py`. Les noms de classe viennent de la feuille.

- **Marque** (`.marque`) : EVAL en lettres grasses larges, suivi d'un carré jaune portant Y, incliné de 6 degrés. Le fichier `static/evaly.svg` reprend le Y en carré jaune, avec un nœud noir à la jonction des branches. Il sert d'icône et de logo du pied de page. Le serveur le sert aussi à l'ancienne adresse `/bench-x.svg`.
- **Piste** (`piste`) : cinq stations avec leur numéro et leur nom ; la courante porte le disque d'essai, les franchies portent une coche.
- **Tête** (`tete`) : piste, `h1`, puces de contexte (`puce`, `ruban`).
- **État** (`etat`) : un seul encadré par écran, réservé à l'état principal. Pastille, mot de statut (`mot`), titre `h2`, une phrase, actions. Une note ordinaire est un paragraphe.
- **Tampon** (`tampon`) : verdict en capitales dans un cadre double, toujours avec son mot. `ok` Satisfait, `ko` Ne satisfait pas, `att` À reprendre, `vig` Non vérifié, `inc` Inconnu. La variante `xl` sert au bilan. La classe `frappe` anime l'arrivée.
- **Boutons** (`btn`, `bouton`) : 52 px de haut minimum. Le bouton principal est jaune. `sec` est transparent à bord encre. `xl` est grand. Au survol, le bouton se décale de 3 px et projette un filet plein. Un seul bouton principal par écran. Le bouton de suppression est rouge (`btn-danger`). `lien` est l'action discrète avec flèche.
- **Relevés** (`releves`, `readout`) : un chiffre déjà connu, en grand, toujours nommé au-dessus. Une valeur absente s'affiche « inconnu ». La variante `compact` sert à la colonne de droite.
- **Récapitulatif avant dépense** : une ligne de synthèse, puis le bouton qui lance, puis le détail. La prévision des réponses et la réservation de la vérification s'affichent séparément, jamais additionnées. L'annonce des réserves avant la validation de l'exemple est facultative et informative : la page de validation ne l'affiche pas ; une info-bulle pourra la porter.
- **Tiroir** (`tiroir`) : `details` natif avec titre, résumé de statut et signe plus. Il contient le détail rigoureux, les preuves et les options secondaires.
- **Champ** (`champ`, `composeur` dans `views.py`) : libellé visible, aide liée par `aria-describedby`, bord encre de 2 px, 52 px de haut minimum. Une erreur porte `aria-invalid="true"` et un message `role="alert"` (`erreur_champ`). Le champ unique de Mes cas d'usage (`.compo`) a un halo animé au focus et des exemples qui défilent quand il est vide.
- **Zone** (`zone`, `section`) : bande pleine largeur avec étiquette et titre, ou section d'une colonne.
- **Colonnes** (`workbench`) : colonne principale (`.pile-l`) et colonne de synthèse collante (`.cote`).
- **Harnais** (`harnais`) : valeur « pi.dev » avec lien et info-bulle sur le lancement à vide, sans numéro de version.
- **Table des cas** (`table-cas`) : cas d'usage, version, effacement prévu, actions ; une carte par ligne sous 900 px, libellé de date en mono. Le vide s'écrit « Aucun cas d’usage. » sur fond `signal-pale`.
- **Accueil** : dès 1100 px, l'accroche et les trois atouts à parts égales sur une ligne, alignés par le haut, puis le bouton et le lien, l'un sous l'autre et centrés, dans un bloc à part, plus aéré ; deux colonnes de 861 à 1099 px ; une seule sous 860 px ; puis banc d'essai de nuit avec trois couloirs de démonstration, cinq stations, règle du verdict.
- **Suivi en direct** : couloirs de nuit, un disque qui avance par modèle, tampon d'état à droite.
- **Pied de page** : voir « Mise en page ». Le lien « Code source » répond à l'offre de source de l'AGPL §13 et ne disparaît jamais.

## Mouvement

- Le mouvement sert la lecture : le disque d'essai qui court (accueil, suivi), le tampon qui frappe (`frappe`, 0,32 s, ressort `cubic-bezier(.2, .9, .3, 1.25)`), le mot qui défile dans le titre de l'accueil, les exemples qui défilent dans le champ vide, le halo du champ au focus.
- Aucune animation n'est nécessaire pour comprendre un état.
- `prefers-reduced-motion: reduce` supprime toutes les animations et transitions. Le disque et les traces prennent alors leur position finale.
- Les transitions de survol durent 150 à 200 ms : décalage du bouton, flèche des liens, signe plus des tiroirs.
- Aucun nouveau mouvement sans nouvelle règle dans ce fichier.

## Invariants

Ces règles viennent du produit. Le style peut changer, elles restent. Le script `reports/refonte-c/outil/invariants.py` les contrôle sur la maquette.

1. Un statut porte toujours son mot. La couleur seule ne signale rien.
2. Aucun chiffre inventé. Une valeur absente s'affiche « inconnu ». Une estimation porte le mot « estimation » ou « estimé ».
3. Aucun JavaScript requis pour lire, saisir ou naviguer. Le JavaScript ajoute seulement du confort.
4. Aucune ressource externe. Aucun attribut `style=""`, aucune balise `<style>`, aucun script inline qui demanderait `unsafe-inline` dans la CSP.
5. `prefers-reduced-motion: reduce` supprime tout mouvement.
6. Cibles tactiles de 44 px au minimum. Contour de focus visible sur tout élément interactif.
7. À 390 px, la page ne défile pas horizontalement.
8. Le pied de page de chaque page porte le lien « Code source ».
9. Les modèles portent leur nom commercial, jamais un alias interne.
10. Le tableau des résultats n'a pas de colonne ajoutée ni de paragraphe explicatif sous lui. Le détail va dans une info-bulle ou un tiroir. Pas de barres de coût.
11. Aucun score global, aucun « meilleur modèle » en général.
12. Un seul bouton principal par écran.

## À ne pas faire

- Ne pas utiliser la police pixel pour un titre ou du texte.
- Ne pas ajouter de seconde couleur d'accent, de dégradé de décor ni d'ombre décorative. Le seul relief est le filet plein des boutons au survol.
- Ne pas signaler un statut par la couleur seule.
- Ne pas afficher les alias internes de configuration.
- Ne pas écrire « Bench-X » ni « tâche » pour le travail de l'utilisateur : le produit s'appelle Evaly et le travail s'appelle un cas d'usage. Les pages légales et les descriptions de benchmarks publics gardent leur texte.
- Ne pas écrire « benchmark » seul pour un tiers : « benchmark » seul désigne celui de l'utilisateur ; les tiers sont « benchmarks publics », « publiés par d'autres » ou nommés (SWE-bench, MTEB…).
- Ne pas écrire de numéro de version de Pi dans un texte affiché.
- Ne pas copier la feuille ni les composants hors de leurs deux emplacements.
