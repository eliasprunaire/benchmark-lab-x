---
style_gate: pass
---

# Recette du parcours S12

État : **HOLD_VALIDATION_UX**. Le scénario HTTP complet passe sur données contrôlées, avec une intervention du dispositif de recette, en attente de décision d’Ayo. La rupture du parcours réel entre qualification automatique et contrat S3 approuvé reste bloquante ; la recette la reproduit avant cette intervention. La validation UX appartient à Ayo et ne vaut pas étude de représentativité.

Base : `main` à jour au départ, `bcd9231158e636ea527e49fbb839e443e816d01b`, branche `fix/s12-corrections-recette`. Le contrat vient de [S12 #215](https://github.com/eliasprunaire/benchmark-lab-x/issues/215) et de la demande de recette ; les directives communes, D13 bis à D16 et la correction des bornes du plafond ont été relues dans [S22 #247](https://github.com/eliasprunaire/benchmark-lab-x/issues/247).

## Nature de la preuve

L’inspection experte porte sur le HTML reçu en jouant les actions du scénario et sur le code qui explique les refus. Les ambiguïtés ci-dessous sont des constats de lecture de cette inspection. Aucun participant, entretien, temps d’hésitation ou comportement humain n’a été observé. Aucun test avec une personne réelle n’a été effectué.

Le [scénario rejouable](../tests/test_parcours_complet.py) démarre le serveur HTTP du produit sur `127.0.0.1`, avec un port attribué par le système. Le web communique par socket Unix avec un exécuteur factice qui appelle réellement `benchmark.preparation.dispatch`. Celui-ci conserve les travaux dans une file de test ; le scénario les exécute entre deux consultations pour rendre l’attente déterministe. Les vues, validations, sessions, reçus et gardes du moteur utilisent une base temporaire neuve créée dans le répertoire temporaire du système. Elle est nettoyée à la fin du test.

Les réponses de préparation, qualification, accès OpenRouter, candidats et jugement sont contrôlées. Le catalogue et l’horloge de préparation sont figés. Le garde de connexion autorise seulement le port HTTP attribué et la socket Unix de cette instance ; toute autre destination et tout `connect_ex` lèvent `AssertionError('No network')`. La redirection OpenRouter est inspectée sans être suivie ; seul le retour local reçoit un code factice. Aucune clé réelle, aucun appel fournisseur, aucune dépense ni donnée historique n’est utilisé.

Après avoir prouvé l’absence de contrat S3 et le refus HTTP 400, le test crée, qualifie et approuve un contrat synthétique avec les primitives et autorités `TEST_ONLY` des fixtures S3 existantes, sur la révision validée. Cette intervention appartient au dispositif de recette : elle n’est pas une étape web et ne répare pas le parcours réel. Les deux acquisitions passent ensuite par `campaigns.execute_launch`, avec un callback factice. Les verdicts sont produits séparément par les contrôles S5 factices existants ; le scénario constate aussi l’absence de résultats évalués avant cette intervention. Il ne prouve pas une orchestration autonome du jugement en production.

## Parcours effectivement exécuté

Dans les routes ci-dessous, `{d}`, `{c}` et `{t}` désignent le dossier, la campagne et la tentative créés pendant le test. Les actions HTTP utilisent les liens et les champs cachés des pages reçues.

| Route | État et vérification |
|---|---|
| `/` | Accueil, action « Décrire mon cas d’usage », portée située du benchmark |
| `/preparation` | Aucun dossier, besoin et ordre des champs : tâche, résultat attendu, contexte |
| `POST /preparation/dossiers` | Texte trop court refusé ; saisie conservée pour correction puis envoi accepté |
| `/preparation/dossiers/{d}` | Attente avant exécution factice, puis question sur le format des actions |
| `POST …/{d}/messages` | Précision, exemple avec consigne et pièce intégrée, correction demandant un tableau avec responsable |
| `…/{d}/revisions/3` puis `…/{d}` | Ancienne révision en lecture seule ; retour explicite à la révision courante ; livrable antérieur conservé |
| `POST …/{d}/validation` puis `…/{d}` | Validation enregistrée, qualification en attente puis réussie ; résumé consultable |
| `GET` et `POST …/{d}/configurations` | Deux modèles et un palier choisis ; premier enregistrement refusé sans contrat S3, puis accepté après intervention contrôlée du dispositif de recette |
| `…/campaigns/{c}/conditions` | Sélection enregistrée, accès requis ; lien vers la connexion |
| `/preparation/access`, `/start`, `/callback` | Autorisation et crédit simulés ; retour à la liste, au dossier, puis au récapitulatif |
| `…/campaigns/{c}/conditions` | Travail, configurations, estimation et plafond relus ; lancement indisponible si le transport candidat manque, sans faux accusé d’enregistrement |
| `POST …/campaigns/{c}/start`, puis `/conditions` | Confirmation unique, deux intentions réservées ; attente, première réponse reçue avec seconde en attente, puis deux réponses reçues ; plafond désormais figé |
| `…/campaigns/{c}` | Aucune évaluation avant les constats factices ; ensuite un verdict satisfait, un non satisfait, coût connu de 0,10 USD et coût inconnu ; comparaison économique incomplète |
| `…/campaigns/{c}?sort=cost` | Tri du coût observé, verdicts et portée par cas conservés ; accès au détail |
| `…/campaigns/{c}/attempts/{t}?sort=cost` | Pièces complètes présentes dans les dépliants, sortie longue échappée, absence de script actif ; retour conservant le tri et ciblant une ligne focalisable |
| Dossier, récapitulatif, comparaison et détail avec une autre session valide | HTTP 403 sans besoin ni contenu de preuve ; contrôle supplémentaire avec cookie absent ou inconnu sur le dossier |
| `/preparation` puis `…/{d}` avec le cookie initial | Dossier retrouvable et lecture conservée après fermeture de la préparation ; formulaire désactivé et envoi forcé refusé |
| `/preparation/access`, `/callback`, `/disconnect` | Test complémentaire indépendant : échange refusé, reconnexion réussie puis déconnexion ; code et clé absents des réponses |
| `/preparation/style.css` | Feuille servie ; règles de colonne unique sous `40rem`, en-tête vertical, focus visible, boutons à retour à la ligne et tableau à défilement horizontal |

Le scénario principal conserve trois réponses de préparation, une qualification et deux réponses candidates factices. Les lectures et retours n’ajoutent aucun travail à la file. Les identifiants nécessaires aux formulaires, URL et ancres restent présents ; ceux du stockage et de l’exécution ne sont plus affichés dans le texte hors dépliants. Les identités des modèles comparés restent lisibles.

## Incompréhensions constatées et corrections

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

## Limites et décisions ouvertes

- **Parcours réel bloqué à S3** : `POST …/{d}/configurations` exige un contrat approuvé que la qualification automatique ne construit pas. L’intervention du dispositif de recette reste en attente de décision d’Ayo. La rupture S17 → S3 est traitée par une story distincte, S29, sans préjuger de son contrat.
- **Retour OpenRouter indirect** : après connexion depuis `/preparation/access`, le scénario revient à la liste puis au dossier avant le récapitulatif. Aucune perte de données constatée ; la conservation directe du contexte de campagne reste à décider.
- **Jugement contrôlé** : les résultats sont obtenus par un contrôleur factice injecté via les primitives S5, sans participant ni jugement réel. Le test démontre leur restitution, pas la justesse métier d’une épreuve ni le fonctionnement réel des modèles.
- **Clavier et petit écran** : ordre lu dans le HTML, lien d’évitement, absence de tabulation positive, correction repliée, région de tableau focalisable, ancres de retour et règles CSS vérifiés. Aucun événement clavier physique, ouverture interactive de dépliant ou mesure de mise en page dans un navigateur n’est revendiqué pour S12. Le script de focus existant reste inchangé ; sa présence ne prouve pas son exécution.
- **HOLD_VALIDATION_UX** : Ayo doit valider le parcours démontré. Les assertions portent sur les indications disponibles, pas sur la compréhension d’une personne ni sur la représentativité des usages.

## Rejeu et validations

```sh
uv run --with requests --with mpmath==1.3.0 python -m unittest tests.test_parcours_complet
```

Résultat final ciblé : `Ran 3 tests in 1.317s`, `OK`. `test_parcours_complet` couvre la chaîne entière avec l’intervention du dispositif de recette, en conservant la preuve de la rupture S17 → S3 préalable.

Les tests de recette et les suites web demandées ont passé ensemble : `Ran 48 tests in 22.045s`, `OK`. Le contrôle S10 de région accessible suit le libellé « Observations du cas 1 » ; sa focalisation et ses autres exigences sont conservées.

Validation complète obligatoire, sous macOS avec Python 3.12.13 :

```sh
uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests
```

Résultat exact après la dernière correction :

```text
Ran 1216 tests in 157.533s

OK
```

Cette découverte exclut `benchmark.test_demo`. La suite historique séparée `uv run python -B -m unittest benchmark.test_demo` a passé `Ran 69 tests in 38.765s`, `OK`, pendant cette reprise. Les corrections suivantes concernent seulement les motifs du rendu web et leur assertion ; le contexte de cette preuve historique reste inchangé.

Les liens locaux du rapport et `git diff --check` passent aussi.

Les preuves locales restent distinctes d’une CI Linux, d’une intégration sur `main` et d’un déploiement. Aucun push ni PR n’est demandé.
