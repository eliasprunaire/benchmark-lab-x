---
style_gate: pass
---

# Contribuer à Bench-X

Une contribution doit préserver les preuves, les frontières d’autorité et la distinction entre simulation, campagne réelle, release et déploiement. La CI vérifie le changement proposé. Après revue et fusion dans `main`, la chaîne de release et de déploiement prend le relais sans intervention humaine lorsque le commit est éligible.

## Lire avant de modifier

Les sources canoniques ont des responsabilités distinctes :

- [PRD](docs/PRD.md) : besoin, utilisateurs et périmètre produit ;
- [ARD](docs/ARD.md) : objets, frontières et flux ;
- [Règles](docs/RULES.md) : décisions, preuves, coûts, autorités et versionnement ;
- [Contexte](CONTEXT.md) : vocabulaire du projet ;
- [Release et livraison](docs/release.md) : automatisation de la publication et du déploiement.

Une Issue ou une pull request ne remplace pas ces documents. Signalez une contradiction au lieu d’inventer une synthèse.

## Préparer l’environnement

Le dépôt utilise la version de Python définie dans [`.python-version`](.python-version) et des dépendances verrouillées avec `uv`.

```bash
git clone https://github.com/eliasprunaire/benchmark-lab-x.git
cd benchmark-lab-x
uv run --with-requirements benchmark/requirements.lock python -m unittest discover -s tests
```

Les dépendances de développement proviennent de `benchmark/requirements-dev.in` et de `benchmark/requirements.lock`. Le runtime autonome utilise `benchmark/requirements-runtime.lock`. N’ajoutez pas une autre source de dépendances sans supprimer la duplication qu’elle remplacerait.

Ne versionnez jamais `.env`, une clé, un jeton, une sortie privée, un reçu contenant des données sensibles ou un fichier local non destiné au dépôt. Ne modifiez pas `AGENTS.md` ou `CONTEXT.md` sans demande explicite couvrant ces fichiers.

## Créer une branche

Partez du `main` distant courant :

```bash
git fetch origin
git switch -c feat/nom-court origin/main
```

Utilisez un préfixe adapté, par exemple `feat/`, `fix/`, `docs/` ou `refactor/`. Travaillez sur une branche dédiée et préservez les changements locaux qui ne relèvent pas de votre contribution.

## Écrire les commits

Les commits suivent Conventional Commits. Leur message détermine si une release doit être créée après fusion.

| Message | Effet de release |
|---|---|
| `feat: ...` | niveau mineur |
| `fix: ...` | niveau correctif |
| `type!: ...` ou `BREAKING CHANGE:` | rupture ; niveau mineur tant que le projet reste en `0.x` |
| `docs`, `test`, `chore`, `ci`, `style`, `refactor` | aucune release |
| scope `(ci)` | aucune release, quel que soit le type |

Exemples :

```text
feat(web): ajouter un filtre de comparaison
fix(storage): préserver le reçu après reprise
test(api): couvrir une réponse incomplète
fix(ci): corriger une garde du workflow
```

Un commit sans effet produit ne doit pas déclencher une nouvelle version.

## Vérifier localement

Commencez par le test le plus proche du changement. Avant la pull request, exécutez la suite complète :

```bash
uv run --with-requirements benchmark/requirements.lock python -m unittest discover -s tests
```

Les tests n’effectuent aucun appel modèle payant. Un résultat local vert ne remplace pas la CI Linux, une campagne réelle ou une vérification de production.

## Ouvrir la pull request

La pull request doit expliquer le résultat, les choix nécessaires et les validations exécutées. Elle déclenche le check protégé `tests` sur un runner GitHub hébergé.

La CI :

- utilise la version de Python et les locks du dépôt ;
- valide les workflows GitHub ;
- exécute les tests Python et navigateur ;
- vérifie les contrats cryptographiques du runtime ;
- construit deux fois l’archive du commit proposé ;
- refuse deux archives différentes ;
- calcule la décision de release attendue.

Les jobs de pull request ne reçoivent aucun secret de production et ne peuvent pas utiliser le runner de déploiement interne.

## Relire et fusionner

Utilisez `Rebase and Merge` lorsque chaque commit est propre, utile et conforme à Conventional Commits. Chaque message conservé dans `main` participe alors à la décision SemVer.

Si la branche contient des commits intermédiaires ou des messages non conformes, utilisez `Squash and Merge` avec un titre final Conventional Commit. La fusion reste une décision humaine : vérifiez le diff, le check `tests` et le message qui arrivera dans `main`.

Ne créez pas manuellement un tag ou une release pour une contribution ordinaire.

## Après la fusion

Le push dans `main` déclenche automatiquement la chaîne de release :

1. refus d’un SHA qui n’est plus le `main` courant ;
2. calcul de la prochaine version depuis les commits éligibles ;
3. arrêt sans release si aucun changement produit n’est détecté ;
4. double construction reproductible de l’artefact ;
5. production du reçu, de la décision et des empreintes ;
6. création ou reprise sûre du tag et de la release GitHub ;
7. téléchargement du même artefact par le runner interne ;
8. soumission au contrôleur de déploiement ;
9. vérification de la transaction, de la version, du SHA et de la santé.

Une réussite n’est annoncée que lorsque le contrôleur renvoie un état terminal accepté. Un état `HOLD`, `UNKNOWN`, une divergence d’empreinte ou un contrôle de santé rouge fait échouer le workflow.

Les préversions et leur promotion relèvent d’une décision opérateur. Ne modifiez pas les variables d’activation, l’environnement GitHub `production`, les secrets, le runner ou le contrôleur dans le cadre d’une contribution produit ordinaire.

## Autorités séparées

Une contribution de code n’autorise pas :

- un appel modèle réel ou une dépense ;
- une campagne ;
- la publication de résultats privés ;
- un déploiement manuel ;
- le contournement du contrôleur ;
- la réécriture d’un tag ou d’une release.

La fusion autorise seulement l’automatisation déjà configurée. Toute opération exceptionnelle suit le runbook et conserve ses propres autorisations.
