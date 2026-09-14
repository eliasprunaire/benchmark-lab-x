---
style_gate: pass
---

# Instructions pour les agents

## Mission

Benchmark Lab-X aide à choisir un modèle pour une tâche précise à partir de preuves lisibles. Le verdict porte sur la configuration observée sous des conditions communes, jamais sur le nom du modèle seul.

Le périmètre, l'architecture, le backlog et les campagnes suivent les décisions explicites d'Ayo. Les canons ne portent aucun statut de livraison des versions ; README, GitHub et les reçus portent les faits opérationnels.

## Sources faisant autorité

Lire les documents utiles avant de modifier leur domaine :

- `docs/PRD.md` pour le besoin et le périmètre produit
- `docs/ARD.md` pour les objets, frontières et flux
- `docs/RULES.md` pour les invariants de décision et de preuve, ainsi que le versionnement SemVer
- `CONTEXT.md` pour le vocabulaire
- `tasks/TEMPLATE.md` pour le contrat minimal d'une future tâche

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

GitHub Issues porte les tâches, dépendances, décisions et preuves. Le champ `Status` du [Project #5](https://github.com/users/ayoahha/projects/5) porte l'état de travail. Aucun backlog local ne le duplique.

Créer ou redécouper les Issues seulement après décision explicite d’Ayo. Vérifier le parent courant avant toute création ; ne pas rattacher une nouvelle livraison à une initiative historique par défaut. Ne pas redécouper les anciennes cartes pour fabriquer le nouveau backlog. Utiliser les relations natives `Parent issue` et `Sub-issues progress` pour Initiative, Epic et Story ; réserver « graphe » à l’exécution agentique. Le nombre d’Epics et de Stories découle des résultats décidés.

Aucune Story de tâche, campagne ou candidat tant que son contenu, son panel et son budget ne sont pas décidés. Une Issue se ferme avec sa preuve de résultat ou une décision explicite de remplacement. Distinguer l’état ouvert ou fermé de l’Issue, le champ `Status` du Project et le progrès de ses sous-Issues. Aucun ne prouve l’état d’exécution d’une campagne. `tasks/TEMPLATE.md` décrit une tâche de benchmark, pas une Story de livraison.

## Validation

Exécuter d'abord le test le plus proche du changement. Avant livraison d'un changement susceptible d'affecter le comportement, exécuter les suites du périmètre concerné et la commande actuellement configurée en CI :

```bash
uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests
```

Cette commande ne découvre pas la suite `benchmark/test_demo.py`. Ne pas la présenter comme validation complète du moteur ; distinguer ses preuves macOS de celles réellement acquises sous Linux. Ne pas lancer un appel modèle pour compenser un test absent.

Pour un changement documentaire, vérifier aussi les liens, les chemins canoniques et l'absence de source concurrente. Ne pas déplacer dans la prose un contrôle que la CI peut garantir.

## Livraison et arrêt

Avant de conclure, fournir :

- les fichiers modifiés ou supprimés
- les validations exécutées et leur résultat
- les limites ou décisions encore ouvertes

S'arrêter dès que le résultat autorisé est prouvé. Si une information ou une autorité indispensable manque, rester en `HOLD` sans fallback implicite.

## Règles de revue

Signaler comme bloquant toute nouvelle autorité documentaire concurrente, requalification de campagnes ou prototypes historiques, architecture présentée comme construite sans preuve ou choix d'architecture sans autorité, secret versionné ou contournement d'une preuve requise. Laisser le formatage automatique aux outils du dépôt.
