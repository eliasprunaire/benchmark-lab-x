---
style_gate: pass
---

# Instructions pour les agents

## Mission

Bench-X aide à choisir un modèle pour une tâche précise à partir de preuves lisibles. Le verdict porte sur la configuration observée sous des conditions communes, jamais sur le nom du modèle seul.

Le périmètre, l'architecture, le backlog et les campagnes suivent les décisions explicites d'Ayo. Les canons ne portent aucun statut de livraison des versions ; README, GitHub et les reçus portent les faits opérationnels.

## Sources faisant autorité

Lire les documents utiles avant de modifier leur domaine :

- `docs/PRD.md` pour le besoin et le périmètre produit
- `docs/ARD.md` pour les objets, frontières et flux
- `docs/RULES.md` pour les invariants de décision et de preuve, ainsi que le versionnement SemVer
- `GLOSSARY.md` pour le vocabulaire
- `docs/roadmap.md` pour le jalon nommé de chaque exigence du PRD
- `docs/task-template.md` pour le contrat minimal d'une future tâche
- `DESIGN.md` pour le langage visuel du parcours web : lis DESIGN.md avant tout travail UI

Ces chemins sont uniques. Ne jamais créer de copie, variante datée ou dossier d'archives. Git porte l'historique. Une Issue, une PR, un ancien résultat ou une branche ne remplace pas la spécification courante.

La demande actuelle d'Ayo prévaut sur ce fichier. PRD, ARD et règles gouvernent des domaines distincts ; aucun ne corrige silencieusement les autres. Signaler un conflit au lieu d'inventer une synthèse.

## Manière de travailler

- comprendre le flux concerné avant d'écrire
- viser le plus petit changement qui satisfait la demande
- réutiliser l'existant avant d'ajouter une abstraction ou une dépendance
- préserver les changements non liés, notamment ceux déjà présents dans le worktree
- ne pas transformer une découverte adjacente en nouvelle tâche
- ne pas fabriquer de mesure, de preuve, d'état ou d'autorité absente
- séparer clairement fait établi, déduction et hypothèse lorsque cette distinction change une décision
- ne jamais requalifier les campagnes et prototypes historiques à partir de la spécification courante

## Autorité et sécurité

Une approbation documentaire n'autorise ni appel candidat, ni retry, ni dépense, ni campagne, ni publication, ni déploiement. Intégration Git, exécution produit, appels candidats et budget, provisionnement et publication gardent des autorités distinctes.

Demander une autorisation explicite avant toute opération Git destructive, fusion, suppression de branche, fermeture de PR ou d'Issue, release, appel payant ou action sur un système externe. Ne jamais exposer de secret. Les données et sorties réelles restent privées tant qu'une publication n'est pas autorisée.

## GitHub

GitHub Issues porte les tâches, dépendances, décisions et preuves. Le champ `Status` du [Project #5](https://github.com/users/eliasprunaire/projects/5) porte l'état de travail. Aucun backlog local ne le duplique.

Créer ou redécouper les Issues seulement après décision explicite d’Ayo. Vérifier le parent courant avant toute création ; ne pas rattacher une nouvelle livraison à une initiative historique par défaut. Ne pas redécouper les anciennes cartes pour fabriquer le nouveau backlog. Utiliser les relations natives `Parent issue` et `Sub-issues progress` pour Initiative, Epic et Story ; réserver « graphe » à l’exécution agentique. Le nombre d’Epics et de Stories découle des résultats décidés.

Aucune Story de tâche, campagne ou candidat tant que son contenu, son panel et son budget ne sont pas décidés. Une Issue se ferme avec sa preuve de résultat ou une décision explicite de remplacement. Distinguer l’état ouvert ou fermé de l’Issue, le champ `Status` du Project et le progrès de ses sous-Issues. Aucun ne prouve l’état d’exécution d’une campagne. `docs/task-template.md` décrit une tâche de benchmark, pas une Story de livraison.

## Écrire des tests

Cette politique s’applique à Codex, Claude Code, Grok et aux autres agents intervenant dans ce dépôt.
`CLAUDE.md` importe ce fichier. Les adaptateurs d’autres agents doivent lire ce fichier avant toute implémentation.

- Étendre d’abord un parcours E2E existant pour vérifier le comportement demandé depuis une entrée publique.
- Rechercher les scénarios et assertions existants avant d’ajouter un test. Compléter le scénario responsable du comportement.
- Ne créer aucun TU isolé ni doublon d’un comportement déjà vérifié, sauf exception explicitement demandée par Ayo.
- Ne pas renommer un TU, entourer son appel d’un serveur ou recopier ses mocks pour le présenter comme E2E.
- Ne créer aucun nouveau harnais. Réutiliser les fixtures, le serveur et les transports fictifs existants.
- Préserver les contrôles existants de sécurité, d’intégrité, de cryptographie et d’architecture. Leur présence n’autorise pas de nouveaux TU isolés.
- Avant une suppression, relier chaque comportement et cas limite à son scénario de remplacement. Exécuter ce scénario avant de supprimer.
- Pour une correction, ajouter l’assertion manquante au parcours existant avant de modifier le code produit.
- Conserver un résultat reproductible : commande, scénario, résultat et artefact dans `reports/`. Ne jamais remplacer une preuve par une déclaration.
- Exécuter chaque test une seule fois dans les quatre étapes de CI. Vérifier leur union et l’absence de doublon après modification.

Choisir le harnais selon la preuve nécessaire :

| Preuve | Harnais existant |
|---|---|
| Parcours complet, HTTP réel, exécuteur, stockage et résultat observable | `tests/test_parcours_complet.py` |
| Statuts, en-têtes, routage et négociation du vrai serveur HTTP | `tests/test_web_access.py` |
| Frontière moteur/présentation et projections | `tests/test_web_boundary.py` |
| DOM, JavaScript, accessibilité, CSP et affichage mobile dans Chromium | `tests/privacy_browser.test.cjs` |

Le navigateur reçoit le HTML produit par `views.render` derrière le serveur Node de fixture existant.
Il ne contacte pas `benchmark_web/server.py`. Conserver les preuves HTTP distinctes lorsque le comportement dépend du serveur réel.
Ne remplacer aucun de ces contrôles par une inspection manuelle.

## Validation

Exécuter d’abord les tests pertinents existants.
Avant de livrer un changement de comportement ou de répartition des tests, exécuter les quatre étapes de `.github/workflows/ci.yml`.
Ce workflow porte les commandes et versions de référence. `CONTRIBUTING.md` décrit l’installation locale de Pi.

```text
Pi et comparaison privée -> découverte Python restante -> cryptographie et confidentialité -> Chromium
```

| Étape | Répartition |
|---|---|
| Harnais Pi sans appel modèle | Inspection Pi et `tests.test_private_comparison` |
| Suite Python générale | `unittest discover -s tests` avec `BENCHMARK_TEST_CI_SPLIT=1` |
| Runtime cryptographique | `tests.test_provider_crypto` et `tests.test_privacy` |
| Navigateur | `tests/privacy_browser.test.cjs` |

Les trois modules dédiés utilisent le protocole standard `load_tests` pour sortir uniquement de la découverte répartie en CI.
Sans `BENCHMARK_TEST_CI_SPLIT=1`, la découverte locale inclut tous les tests Python.
Le chargement explicite d’un module reste complet, même avec cette variable.
Ne désactiver aucun test avec `skip` pour réduire la durée ou le nombre d’exécutions.

La CI construit aussi deux fois le runtime du commit vérifié et contrôle sa reproductibilité.
Conserver ce contrôle en CI. Distinguer les résultats macOS, Linux et navigateur dans le compte rendu.
Ne lancer aucun appel modèle pour compenser une preuve absente.

Pour un changement documentaire, vérifier les liens, les chemins canoniques et l’absence de source concurrente.
Ne recopier les commandes de CI ni dans les directives, ni dans une Issue ou une PR.

## Livraison et arrêt

Avant de conclure, fournir :

- les fichiers modifiés ou supprimés
- les validations exécutées et leur résultat
- les limites ou décisions encore ouvertes

S'arrêter dès que le résultat autorisé est prouvé. Si une information ou une autorité indispensable manque, rester en `HOLD` sans fallback implicite.

## Règles de revue

Signaler comme bloquant toute nouvelle autorité documentaire concurrente, requalification de campagnes ou prototypes historiques, architecture présentée comme construite sans preuve ou choix d'architecture sans autorité, secret versionné ou contournement d'une preuve requise. Laisser le formatage automatique aux outils du dépôt.
