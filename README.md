<div align="center">
  <img src="benchmark_web/static/bench-x.svg" width="96" height="96" alt="Logo Bench-X : trois barres de comparaison">
  <h1 id="bench-x">Bench-X</h1>
  <p><strong>by Le Lab-X</strong></p>
  <p>Choisir une configuration de modèle d’IA à partir d’une tâche précise, de résultats observés, de preuves consultables et de leur coût.</p>
  <p>
    <a href="https://github.com/eliasprunaire/benchmark-lab-x/actions/workflows/ci.yml"><img alt="CI Python" src="https://github.com/eliasprunaire/benchmark-lab-x/actions/workflows/ci.yml/badge.svg"></a>
    <a href="LICENSE"><img alt="Licence AGPL-3.0" src="https://img.shields.io/badge/licence-AGPL--3.0-blue.svg"></a>
    <img alt="Python 3.14.7" src="https://img.shields.io/badge/python-3.14.7-3776AB.svg">
  </p>
  <p>
    <a href="#ce-que-fait-bench-x">Fonctionnalités</a> ·
    <a href="#démarrage-local">Démarrage</a> ·
    <a href="#architecture-du-dépôt">Architecture</a> ·
    <a href="#documentation">Documentation</a>
  </p>
</div>

## Pourquoi Bench-X

Un modèle n’est jamais « le meilleur » dans l’absolu. Bench-X compare des configurations dans des conditions communes, sur une tâche définie avant l’exécution. Chaque conclusion reste liée à son contrat, à ses cas d’essai, à la configuration réellement observée, aux preuves conservées et à la date de la campagne.

Le projet sépare clairement la préparation, l’acquisition, l’évaluation et la restitution. Une simulation teste le logiciel ; elle ne devient jamais un résultat de benchmark réel.

## Ce que fait Bench-X

- prépare un dossier entièrement fictif à partir d’un besoin général ;
- qualifie un contrat de réussite avant les appels candidats ;
- fige le panel, les conditions communes, les autorités et le budget d’une campagne ;
- exécute les candidats sous Pi, normalement via OpenRouter ;
- conserve les sorties, incidents, coûts et configurations observées ;
- produit des évaluations explicables et une restitution reliée aux preuves ;
- publie uniquement une projection explicitement approuvée.

Le jalon visé est `0.1.0`. Ce numéro décrit un périmètre produit ; il ne prouve ni release, ni déploiement, ni campagne réelle terminée.

## Démarrage local

### Prérequis

- Python 3.14.7 ;
- [uv](https://docs.astral.sh/uv/) ;
- Node.js et Pi uniquement pour les parcours candidats qui les utilisent.

Depuis la racine du dépôt :

```bash
git clone https://github.com/eliasprunaire/benchmark-lab-x.git
cd benchmark-lab-x
uv run --python 3.14.7 python -m benchmark --help
```

Pour préparer un environnement privé, copiez le fichier d’exemple puis renseignez uniquement les accès nécessaires :

```bash
cp .env.example .env
uv run --env-file .env python -m benchmark --help
```

Le fichier `.env` reste local et ne doit jamais être versionné. Renseigner une clé ne lance aucun appel et ne crée aucune autorité.

`python -m benchmark`, `python -m benchmark.runtime` et `benchmark/benchmark-runtime` appellent le même moteur.

Dans le parcours web configuré en mode personnel, **Ajouter ma clé Openrouter** permet de fournir votre clé depuis le navigateur, sans modifier le `.env` du serveur. Elle finance vos préparations, qualifications et comparaisons sous leurs plafonds respectifs. Enregistrer la clé ne lance aucun modèle.

Pour les commandes opérateur, utilisez `python -m benchmark --help`. Le PRD, l’ARD et les règles en définissent la portée et les conditions.

## Vérification

La suite locale et la CI utilisent la même commande principale :

```bash
uv run --with-requirements benchmark/requirements.lock --python 3.14.7 \
  python -m unittest discover -s tests
```

Les transports sont simulés dans les tests. Un résultat vert prouve les comportements couverts sur l’environnement observé ; il ne prouve pas un accès fournisseur, une campagne réelle, un déploiement ou une publication.

## Architecture du dépôt

```text
benchmark/
├── acquisition/       campagnes, émissions et reprises préautorisées
├── transports/        OpenRouter, Pi, secours officiels et profils
├── preparation.py     dossier fictif et validation du besoin
├── qualification.py   qualification du contrat
├── evaluation.py      constats, mesures et verdicts
├── restitution.py     comparaison privée et preuves
├── storage.py         stockage privé et contrôles d’intégrité
└── runtime.py         commandes opérateur

benchmark_web/         interface web et projections publiques
docs/                  sources canoniques du produit
tests/                 régressions hors ligne et parcours locaux
tools/                 construction reproductible du runtime
```

Les identifiants techniques du produit conservent le préfixe `benchmark-lab-x`. Ils appartiennent aux formats de données et restent stables malgré le nom public Bench-X.

## Documentation

- [PRD](docs/PRD.md) : besoin, utilisateurs, parcours et périmètre ;
- [ARD](docs/ARD.md) : objets, responsabilités, flux et exploitation ;
- [Règles](docs/RULES.md) : contrats, preuves, coûts, autorités et versions ;
- [Gabarit de tâche](docs/task-template.md) : contenu minimal d’une future tâche.

Les [Issues GitHub](https://github.com/eliasprunaire/benchmark-lab-x/issues) et le [Project](https://github.com/users/eliasprunaire/projects/5) portent le travail de livraison et son avancement.

## Contribuer

Une contribution doit préserver les frontières d’autorité et la distinction entre simulation et résultat réel. Consultez le PRD, l’ARD et les règles, puis exécutez le test le plus proche du changement et la suite complète avant livraison.

## Licence

Bench-X est distribué sous licence [AGPL-3.0-only](LICENSE).

---

<div align="center">
  <p><strong>Bench-X</strong> by <strong>Le Lab-X</strong></p>
  <p>Des choix de modèles fondés sur des tâches précises et des preuves lisibles.</p>
  <p><a href="#bench-x">Retour en haut</a></p>
</div>
