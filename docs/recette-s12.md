---
style_gate: pass
---

# Recette du parcours S12

État : **HOLD_CONTRAT_S3** et **HOLD_VALIDATION_UX**. La recette complète n’est pas acquise. Le parcours HTTP atteint la qualification automatique, puis l’enregistrement des configurations échoue faute de contrat S3 approuvé. Aucun contrat n’a été ajouté pour franchir ce refus.

Base : `main` à jour, `8099cbdf7356f87cd5c775c21432d07afe86e8f3`, branche `fix/s12-recette-ux`. L’[acceptation de S11](https://github.com/eliasprunaire/benchmark-lab-x/issues/214#issuecomment-5689010138) est conservée sur cette révision. Le contrat vient de [S12 #215](https://github.com/eliasprunaire/benchmark-lab-x/issues/215) et de la demande de recette ; les directives communes, D13 bis à D16 et la correction des bornes du plafond ont été relues dans [S22 #247](https://github.com/eliasprunaire/benchmark-lab-x/issues/247).

## Nature de la preuve

L’inspection experte porte sur le HTML reçu en jouant les actions du scénario et sur le chemin de code qui explique le refus. Les ambiguïtés ci-dessous sont des constats de lecture de cette inspection. Aucun participant, entretien, temps d’hésitation ou comportement humain n’a été observé. Aucun test avec une personne réelle n’a été effectué.

Le [scénario rejouable](../tests/test_parcours_complet.py) démarre le serveur HTTP du produit sur `127.0.0.1`, avec un port attribué par le système. Le web communique par socket Unix avec un exécuteur factice qui appelle réellement `benchmark.preparation.dispatch`. Il conserve les travaux à exécuter dans une file de test ; le scénario les exécute entre deux consultations pour rendre l’attente déterministe. Les vues, validations, sessions, reçus et gardes du moteur utilisent une base temporaire neuve sous `~/Projects`, hors des sources du dépôt. Elle est nettoyée à la fin du test.

Les réponses de préparation, qualification et accès OpenRouter sont contrôlées. Le catalogue et l’horloge de préparation sont figés. Le garde de connexion autorise seulement le port HTTP attribué et la socket Unix de cette instance ; toute autre destination et tout `connect_ex` lèvent `AssertionError('No network')`. La redirection d’autorisation OpenRouter est inspectée sans être suivie ; seul le retour local reçoit un code factice. Aucune clé réelle, aucun appel fournisseur, aucune dépense ni donnée historique n’est utilisé.

## Parcours effectivement exécuté

Dans les routes ci-dessous, `{d}` est l’identifiant opaque créé par le formulaire. Les étapes empruntent les liens et les champs cachés du HTML reçu.

| Route | État et vérification |
|---|---|
| `/` | Accueil, action « Décrire mon cas d’usage », portée située du benchmark |
| `/preparation` | Aucun dossier, besoin et ordre des champs : tâche, résultat attendu, contexte |
| `POST /preparation/dossiers` | Texte trop court refusé ; saisie conservée pour correction puis envoi accepté |
| `/preparation/dossiers/{d}` | Attente avant exécution factice, puis question sur le format des actions |
| `POST …/{d}/messages` | Précision, exemple avec consigne et pièce intégrée, puis correction demandant un tableau avec responsable |
| `…/{d}/revisions/3` puis `…/{d}` | Ancienne révision en lecture seule ; retour explicite à la révision courante ; livrable antérieur conservé |
| `POST …/{d}/validation` puis `…/{d}` | Validation enregistrée, qualification en attente puis réussie ; résumé de qualification consultable |
| `GET …/{d}/configurations` | Choix de deux modèles et d’un palier ; aucune campagne créée |
| `POST …/{d}/configurations` | HTTP 400 ; diagnostic local exact : `Contrat qualifié et approuvé requis` ; zéro contrat S3 et zéro campagne conservés |
| `…/{d}` avec cookie absent ou inconnu | HTTP 403 sans besoin, pièce ou référence privée ; liste vide dans la nouvelle session |
| `/preparation` puis `…/{d}` avec le cookie initial | Dossier retrouvable et lecture conservée après fermeture des appels ; formulaire désactivé et envoi forcé refusé |
| `/preparation/access`, `/start`, `/callback`, `/disconnect` | Scénario indépendant : déconnecté, échange factice refusé, reconnexion réussie, crédit affiché, retour local, déconnexion ; code et clé absents des réponses |
| `/preparation/style.css` | Feuille effectivement servie ; règles de colonne unique sous `40rem`, en-tête vertical, focus visible, boutons à retour à la ligne et tableau à défilement horizontal |

Le scénario principal conserve trois réponses de préparation et une qualification factices. Il ne passe aucun candidat. Le test OpenRouter est indépendant : il ne prouve pas une continuité depuis une sélection enregistrée.

## Incompréhensions constatées et corrections

| Route et état | Constat de l’inspection | Résultat |
|---|---|---|
| `/`, `…/{d}` avant validation | « Rien n’est lancé » et « la validation ne lance aucun appel » contredisent la réservation d’une qualification automatique | Texte corrigé : qualification financée par l’opérateur, lancement candidat distinct, aucune publication |
| `…/{d}`, qualification en attente ou réussie | Le même état « Cas d’usage validé » annonce une attente du responsable, même après réception d’une qualification automatique ; son résumé reste absent du dépliant | État de qualification affiché, prochaine action adaptée et résumé rendu dans le dépliant existant ; les états S3 historiques gardent leur présentation distincte |
| `…/{d}`, attente ; `…/{d}/revisions/3`, lecture historique | Aucune action principale pour actualiser l’attente ou retrouver la version modifiable | Liens existants mis en avant : « Actualiser cet état » et « Revenir à la révision courante » |
| `/preparation/access`, connecté | « Déconnecter » est l’action principale après une connexion réussie | Retour aux cas d’usage mis en avant ; déconnexion secondaire |
| `/preparation/access/callback` et `…/{d}`, refus d’accès | La page de refus ne présente aucune action principale | Retour aux cas d’usage mis en avant ; sur une erreur de saisie, la correction reste principale |
| `/`, `/preparation`, `…/{d}/configurations`, entrée dans l’étape | L’indication de départ n’est pas identifiée comme phrase d’état ; le minimum de deux modèles n’est pas annoncé ; « Enhanced » n’est pas traduit | Phrase d’état identifiée, minimum expliqué et palier « Renforcé » |
| Toutes les pages parcourues | L’identifiant de révision du logiciel apparaît dans le pied de page hors dépliant | Version conservée sous « Version du site » |

## Limites et décisions ouvertes

| Route et état | Limite observée ou partie non exécutée | Décision attendue |
|---|---|---|
| `POST …/{d}/configurations`, exemple qualifié automatiquement | Rupture reproduite : `campaigns._current_contract` exige un contrat S3 approuvé. La qualification automatique ne le construit pas ; le web reçoit une erreur générique | Autoriser ou refuser l’injection explicite d’un contrat S3 factice pour poursuivre la recette. Cette injection ne corrigerait pas le parcours réel et devrait rester déclarée comme limite bloquante |
| `…/{d}/configurations`, sélection enregistrée | Lecture du rendu seulement : « Enregistrer les configurations » et « Voir le récapitulatif » ont tous deux le style principal ; cet état n’a pas été atteint par HTTP | Reprendre cette partie après décision sur le contrat factice |
| `…/campaigns/{c}/conditions`, lancement demandeur enregistré | Lecture du rendu seulement : la branche demandeur n’affiche pas la section de suivi présente dans l’autre branche | Vérifier par scénario après décision ; aucune correction spéculative réalisée |
| Récapitulatif → suivi → résultats → preuves → retour | Non exécuté dans cette recette S12 ; aucun verdict, coût candidat, preuve ouverte ou retour de comparaison revendiqué | Poursuite du scénario et corrections observées après levée du blocage |
| Clavier et petit écran | Ordre lu dans le HTML, absence de tabulation positive, lien d’évitement, correction repliée et règles CSS vérifiés ; aucune tabulation physique ni mesure de mise en page dans un navigateur pendant S12 | Validation d’Ayo sur le parcours démontré ; les observations visuelles historiques S11 ne sont pas présentées comme rejouées ici |
| Compréhension du parcours | Les assertions contrôlent les indications de la page ; elles ne prouvent pas qu’une personne les comprend | **HOLD_VALIDATION_UX** : décision d’Ayo, sans valeur d’étude de représentativité |

## Rejeu et validations

```sh
uv run python -m unittest tests.test_parcours_complet
```

Résultat : `Ran 3 tests in 0.384s`, `OK`. Le test principal s’appelle `test_parcours_jusqua_la_rupture_du_contrat` : son succès prouve le parcours partiel et le refus observé, pas l’achèvement de S12.

Les tests existants `tests.test_web_redesign`, `tests.test_s9_inline`, `tests.test_s10_regressions`, `tests.test_web_access` et `tests.test_web_boundary` passent : `Ran 32 tests in 6.766s`, `OK`.

Validation complète obligatoire, sous macOS avec Python 3.12.13 :

```sh
uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests
```

Résultat exact :

```text
Ran 1208 tests in 147.706s

OK
```

La suite historique séparée `uv run python -B -m unittest benchmark.test_demo` passe : `Ran 69 tests in 37.334s`, `OK`. Les liens locaux du rapport et `git diff --check` passent aussi.

Cette découverte exclut la suite historique `benchmark.test_demo`. Les preuves locales restent distinctes d’une CI Linux, d’une intégration sur `main` et d’un déploiement. Aucun push ni PR n’est demandé.
