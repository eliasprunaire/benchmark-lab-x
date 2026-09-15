---
style_gate: pass
---

# Benchmark Lab-X

Licence : [AGPL-3.0-only](LICENSE)

Benchmark Lab-X aide à choisir une configuration de modèle d’IA pour une tâche précise. Il rapproche le travail demandé, les résultats obtenus, leur évaluation et leur coût pour permettre une décision fondée sur des preuves consultables.

Le cap de 0.1.0 est un parcours public : décrire un besoin, le préciser avec une assistance IA, examiner et modifier un dossier entièrement fictif, puis autoriser une comparaison. Les classements par critère et les filtres aideront l’utilisateur à choisir à partir des résultats, des erreurs et des coûts observés. Une conclusion vaut pour la tâche et les conditions testées, sans meilleur modèle universel. Le score pondéré personnalisé appartient à la vision ultérieure, hors 0.1.0.

## Découvrir les résultats

Les comparaisons publiées sont servies par le service web sous `/publications/` ; la publication GitHub Pages est retirée.

La restitution historique présentait un scénario et trois configurations, sous son contrat historique. Son parcours reste :

1. Lisez le besoin, l’entrée et le résultat attendu pour vérifier que la tâche ressemble à votre usage.
2. Examinez le verdict de chaque configuration et les constats qui le justifient.
3. Comparez les coûts des configurations qui satisfont les critères, puis les bénéfices prévus d’une option plus chère.
4. Consultez les sorties, incidents et limites avant de transposer la conclusion à votre situation.

Un verdict indéterminé signifie que les preuves ne permettent pas de conclure. Un coût manquant limite la comparaison économique ; les dépenses des configurations non admissibles restent visibles. Cette présentation historique reste inchangée ; les [règles de décision courantes](docs/RULES.md#7-ordre-de-décision) définissent les futurs classements par critère sans requalifier ces résultats.

## Utiliser l’outil local

L’outillage Python prépare un scénario figé, recueille les sorties après autorisation, prépare une revue et construit une page à partir de décisions approuvées. Il peut aussi produire une nouvelle présentation d’un résultat scellé sans relancer de candidat.

Depuis la racine du dépôt, avec Python 3 :

```bash
python3 -B -m benchmark --help
```

Pour consulter une campagne locale déjà construite et scellée, remplacez le chemin d’exemple par le sien :

```bash
python3 -B -m benchmark show --run-dir runs/ma-campagne
```

Cette commande vérifie l’intégrité puis ouvre la page sur macOS. Le [guide local](benchmark/README.md) détaille les étapes, les prérequis et les autorisations nécessaires. Les [tests hors ligne](benchmark/verify.md) utilisent un faux Pi et n’appellent aucun modèle.

Ce moteur historique reste attaché à un scénario et à son panel figés. Le runtime du service fournit séparément la préparation privée, les dossiers versionnés, les campagnes et leur restitution. Le [guide opérateur](benchmark/README.md#première-comparaison-privée--pi-et-jugement-opérateur) décrit le raccordement candidat Pi/OpenRouter et l’évaluation locale ou humaine ; leurs tests simulés ne remplacent ni les campagnes réelles ni leur autorisation. Le [périmètre produit](docs/PRD.md#5-périmètre-produit) définit ces capacités attendues.

## Préparer une tâche de benchmark

Le parcours prévu part d’une description générale, sans donnée personnelle ni information confidentielle. L’assistance pose les questions utiles, construit les pièces fictives puis présente un exemple consultable et modifiable. Une demande non évaluable est expliquée et reformulée avec accord, ou arrêtée. Valider l’exemple ne lance aucune campagne et ne publie rien. Aucun dossier réel, accès à l’ordinateur ou action sur téléphone n’entre dans ce parcours 0.1.0.

Comparer des salles, préparer le suivi d’une réunion et organiser les pièces de l’entreprise fictive Orme & Signal sont des exemples pédagogiques, sans corpus obligatoire ni preuve de couverture métier. Le [PRD](docs/PRD.md#10-restitution-publique) décrit le parcours et l’illustration ; ses fichiers ne sont pas construits par la seule spécification. Le [gabarit de tâche](tasks/TEMPLATE.md) aide à relier :

- le besoin, le résultat utilisable, ce que l’utilisateur doit encore faire et la décision à éclairer ;
- les cas d’essai, leurs données et les conditions communes ;
- les obligations, les variations acceptables, les erreurs éliminatoires et la référence permettant de juger ;
- le périmètre des coûts et les limites de la conclusion.

La préparation vérifie aussi que la référence est étayée et que les contrôles acceptent une solution valable et repèrent les défauts visés. Des modèles peuvent aider à la relire ; leur accord ne suffit pas à établir sa justesse. Le responsable de campagne prépare et approuve ce contrat avant les appels candidats, selon les [règles de qualification](docs/RULES.md#4-contrat-avant-exécution). La préparation assistée, le choix des configurations, le budget et la publication nécessitent leurs propres autorités. Les résultats de benchmark doivent être réellement acquis ; une réponse simulée ne les remplace pas.

Remplir cette carte ne l’enregistre pas automatiquement dans un catalogue et ne la rend pas exécutable par l’outillage actuel. L’intégration d’une nouvelle tâche doit relier la carte à ses données, à ses contrôles et à une campagne autorisée. La restitution historique ne propose ni formulaire de contribution, ni téléversement, ni commentaire ; l’ouverture du parcours public relève du [périmètre 0.1.0](docs/PRD.md#51-périmètre-010), avec les décisions d’accès, de financement et de données encore à prendre.

## Comprendre le projet et suivre son évolution

- [PRD](docs/PRD.md) : utilisateurs, parcours, périmètre et critères produit
- [ARD](docs/ARD.md) : architecture, données, interfaces et exploitation
- [Règles](docs/RULES.md) : évaluation, coûts, autorités et [versionnement](docs/RULES.md#14-versionnement-du-produit)
- [Glossaire](CONTEXT.md) : vocabulaire partagé
- [Instructions agents](AGENTS.md) : travail et validation dans le dépôt

Les [Issues GitHub](https://github.com/eliasprunaire/benchmark-lab-x/issues) et le [Project](https://github.com/users/eliasprunaire/projects/5) portent le travail de livraison et son avancement.
