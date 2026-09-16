---
style_gate: pass
---

# Recette du parcours S12

État : **HOLD_VALIDATION_UX**. Le scénario HTTP passe sur données contrôlées jusqu’aux reçus candidats, par la seule qualification automatique. S29 crée le contrat de comparaison nécessaire aux configurations ; aucun contrat S3 ni verdict n’est injecté. La validation UX appartient à Ayo et ne vaut pas étude de représentativité.

Base de la correction S29 : `main` à jour au départ, `f8bdd37`, branche `feat/s29-contrat-comparaison`. Le contrat vient de [S12 #215](https://github.com/eliasprunaire/benchmark-lab-x/issues/215) et de la demande de recette ; les directives communes, D13 bis à D16 et la correction des bornes du plafond ont été relues dans [S22 #247](https://github.com/eliasprunaire/benchmark-lab-x/issues/247).

## Nature de la preuve

L’inspection experte porte sur le HTML reçu en jouant les actions du scénario et sur le code qui explique les refus. Les ambiguïtés ci-dessous sont des constats de lecture de cette inspection. Aucun participant, entretien, temps d’hésitation ou comportement humain n’a été observé. Aucun test avec une personne réelle n’a été effectué.

Le [scénario rejouable](../tests/test_parcours_complet.py) démarre le serveur HTTP du produit sur `127.0.0.1`, avec un port attribué par le système. Le web communique par socket Unix avec un exécuteur factice qui appelle réellement `benchmark.preparation.dispatch`. Celui-ci conserve les travaux dans une file de test ; le scénario les exécute entre deux consultations pour rendre l’attente déterministe. Les vues, validations, sessions, reçus et gardes du moteur utilisent une base temporaire neuve créée dans le répertoire temporaire du système. Elle est nettoyée à la fin du test.

Les réponses de préparation, qualification, accès OpenRouter et candidats sont contrôlées. Le catalogue et l’horloge de préparation sont figés. Le garde de connexion autorise seulement le port HTTP attribué et la socket Unix de cette instance ; toute autre destination et tout `connect_ex` lèvent `AssertionError('No network')`. La redirection OpenRouter est inspectée sans être suivie ; seul le retour local reçoit un code factice. Aucune clé réelle, aucun appel fournisseur, aucune dépense ni donnée historique n’est utilisé.

Avant correction, la reproduction sur `f8bdd37` donne `qualified=true`, zéro contrat S3 et HTTP 400 avec « Action non vérifiée. Vérifiez les champs ou consultez le dossier courant. ». Le test courant exige HTTP 201 en JSON au premier enregistrement des configurations, après qualification automatique, avec un contrat de comparaison et toujours zéro contrat S3. Les deux acquisitions passent ensuite par `campaigns.execute_launch`, avec un callback factice. La comparaison affiche l’absence d’évaluation et aucune ligne S5 n’est créée. Le jugement expert refuse ce contrat ; son raccordement au parcours public reste hors du périmètre S29.

## Parcours effectivement exécuté

Dans les routes ci-dessous, `{d}` et `{c}` désignent le dossier et la campagne créés pendant le test. Les actions HTTP utilisent les liens et les champs cachés des pages reçues.

| Route | État et vérification |
|---|---|
| `/` | Accueil, action « Décrire mon cas d’usage », portée située du benchmark |
| `/preparation` | Aucun dossier, besoin et ordre des champs : tâche, résultat attendu, contexte |
| `POST /preparation/dossiers` | Texte trop court refusé ; saisie conservée pour correction puis envoi accepté |
| `/preparation/dossiers/{d}` | Attente avant exécution factice, puis question sur le format des actions |
| `POST …/{d}/messages` | Précision, exemple avec consigne et pièce intégrée, correction demandant un tableau avec responsable |
| `…/{d}/revisions/3` puis `…/{d}` | Ancienne révision en lecture seule ; retour explicite à la révision courante ; livrable antérieur conservé |
| `POST …/{d}/validation` puis `…/{d}` | Validation enregistrée, qualification en attente puis réussie ; résumé consultable |
| `GET` et `POST …/{d}/configurations` | Deux modèles et un palier choisis ; premier enregistrement accepté en HTTP 201 après qualification automatique, sans injection de contrat S3 |
| `…/campaigns/{c}/conditions` | Sélection enregistrée, accès requis ; lien vers la connexion |
| `/preparation/access`, `/start`, `/callback` | Autorisation et crédit simulés ; retour à la liste, au dossier, puis au récapitulatif |
| `…/campaigns/{c}/conditions` | Travail, configurations, estimation et plafond relus ; lancement indisponible si le transport candidat manque, sans faux accusé d’enregistrement |
| `POST …/campaigns/{c}/start`, puis `/conditions` | Confirmation unique, deux intentions réservées ; attente, première réponse reçue avec seconde en attente, puis deux réponses reçues ; plafond désormais figé |
| `…/campaigns/{c}` | Comparaison consultable, aucune tentative évaluée ; reçus conservés sans verdict |
| Dossier, récapitulatif et comparaison avec une autre session valide | HTTP 403 sans besoin ni contenu de preuve ; contrôle supplémentaire avec cookie absent ou inconnu sur le dossier |
| `/preparation` puis `…/{d}` avec le cookie initial | Dossier retrouvable et lecture conservée après fermeture de la préparation ; formulaire désactivé et envoi forcé refusé |
| `/preparation/access`, `/callback`, `/disconnect` | Test complémentaire indépendant : échange refusé, reconnexion réussie puis déconnexion ; code et clé absents des réponses |
| `/preparation/style.css` | Feuille servie ; règles de colonne unique sous `40rem`, en-tête vertical, focus visible, boutons à retour à la ligne et tableau à défilement horizontal |

Le scénario principal conserve trois réponses de préparation, une qualification et deux réponses candidates factices. Les lectures et retours n’ajoutent aucun travail à la file. Les identifiants nécessaires aux formulaires, URL et ancres restent présents ; ceux du stockage et de l’exécution ne sont plus affichés dans le texte hors dépliants. Les identités des modèles comparés restent lisibles.

## Constats et corrections de la recette S12 initiale

| Route et état | Constat de l’inspection | Résultat |
|---|---|---|
| `/`, `…/{d}` avant validation | « Rien n’est lancé » et « la validation ne lance aucun appel » contredisent la réservation d’une qualification automatique | Qualification financée par l’opérateur explicitée, lancement candidat distinct, aucune publication |
| `…/{d}`, qualification en attente ou réussie | État de validation inchangé, attente du responsable annoncée même après qualification automatique, résumé absent | État et prochaine action adaptés ; résumé dans le dépliant existant ; distinction avec S3 conservée |
| `…/{d}`, attente ; `…/{d}/revisions/3`, historique | Aucune action principale pour actualiser ou retrouver la version modifiable | Liens d’actualisation et de retour courant mis en avant |
| `/preparation/access`, connecté | « Déconnecter » reste l’action principale après connexion | Retour aux cas d’usage principal ; déconnexion secondaire |
| Callback et dossier, refus d’accès | Aucune action principale | Retour aux cas d’usage mis en avant ; correction prioritaire sur les erreurs de saisie |
| Accueil, besoin et configurations, entrée dans l’étape | Indication de départ sans rôle d’état, minimum de deux modèles absent, « Enhanced » non traduit | Phrase d’état identifiée, minimum expliqué, palier « Renforcé » |
| Configurations enregistrées ; dossier avec campagne ; récapitulatif prêt | Deux actions de même importance : enregistrer ou poursuivre, choisir les modèles ou consulter la campagne, modifier le plafond ou lancer | Poursuite du parcours principale ; modifications secondaires |
| Récapitulatif, avant lancement | Le travail et les modèles ne sont pas rappelés ; seule une liste de contrôles précède la confirmation | Résultat attendu et modèles visibles, critères et conditions détaillés consultables, financement et différence entre estimation, plafond et coût rappelés |
| Récapitulatif, lancement enregistré | Aucun suivi ni lien direct vers les résultats dans la branche demandeur | Suivi par modèle, attente/réception visibles, actualisation puis accès aux résultats ; formulaire du plafond retiré après son gel |
| Récapitulatif, transport candidat absent | « Lancement enregistré » sans lancement | Indisponibilité explicite et retour au dossier, sans formulaire de lancement |
| Comparaison et détail des preuves | Identifiants de campagne, tentative, configuration et évaluation dans les titres ou textes ; retour sans hiérarchie ; dernier lien de détail annonçant à tort un retour au dossier | Identifiants conservés sous dépliants, cas numérotés, liens de preuves lisibles ; codes de critères remplacés par leurs libellés dans les motifs affichés ; retour principal et destination correctement nommée |
| Toutes les pages parcourues | Révision technique du logiciel dans le pied de page | Version conservée sous « Version du site » |

Les contrôles de détail et de verdict de la recette S12 initiale utilisaient un contrat S3 et des constats factices. Le parcours S29 ci-dessus se termine aux reçus ; les tests S5 et S6 conservent la couverture du jugement expert et de ses preuves.

## Limites et décisions ouvertes

- **Stockage à recréer** : le nouveau schéma S2 et le retrait de la clé étrangère S4 rendent les bases antérieures incompatibles ; aucune migration ni réécriture n’est exécutée par cette correction.
- **Retour OpenRouter indirect** : après connexion depuis `/preparation/access`, le scénario revient à la liste puis au dossier avant le récapitulatif. Aucune perte de données constatée ; la conservation directe du contexte de campagne reste à décider.
- **Aucun verdict dans le parcours public** : `evaluation.evaluate` reste accessible par le runtime opérateur et refuse le contrat de comparaison, comme le jugement assisté. La clé étrangère S5 vers S3 reste en place ; aucune ligne S5 n’est créée pour ce contrat.
- **Clavier et petit écran** : ordre lu dans le HTML, lien d’évitement, absence de tabulation positive, correction repliée et règles CSS vérifiés ; la région de tableau et les ancres de détail relevaient de la recette S12 initiale. Aucun événement clavier physique, ouverture interactive de dépliant ou mesure de mise en page dans un navigateur n’est revendiqué pour S12. Le script de focus existant reste inchangé ; sa présence ne prouve pas son exécution.
- **HOLD_VALIDATION_UX** : Ayo doit valider le parcours démontré. Les assertions portent sur les indications disponibles, pas sur la compréhension d’une personne ni sur la représentativité des usages.

## Rejeu et validations

```sh
uv run --with requests --with mpmath==1.3.0 python -m unittest tests.test_parcours_complet
```

Les six modules demandés, dont le parcours HTTP, passent ensemble : `Ran 74 tests in 6.740s`, `OK`.
Les huit nouveaux tests et deux régressions adaptées ont aussi été exécutés avec les sources produit de `f8bdd37` dans une copie temporaire : `Ran 10 tests in 0.524s`, `FAILED (failures=2, errors=9)` ; les sous-tests de compatibilité expliquent le nombre de constats supérieur à celui des tests.

Validation complète obligatoire, sous macOS avec Python 3.12.13 :

```sh
uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests
```

Résultat exact S29 :

```text
Ran 1224 tests in 158.530s

OK
```

Cette découverte exclut `benchmark.test_demo` ; la commande séparée `uv run python -B -m unittest benchmark.test_demo` passe avec `Ran 69 tests in 40.124s`, `OK`. Le scan des accents et des commentaires ajoutés, les liens locaux et `git diff --check` passent aussi. Les preuves locales restent distinctes d’une CI Linux, d’une intégration sur `main` et d’un déploiement. Aucun push ni PR n’est demandé.
