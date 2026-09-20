# Release et livraison Bench-X

## Règle de version

`tools/release.py` lit les Conventional Commits depuis la dernière release SemVer atteignable, ou depuis le commit précédent lors de l’amorçage sans tag :

- `feat` produit ou rupture sous `0.x` : `MINOR`, avec `PATCH` remis à zéro ;
- `fix` : `PATCH` ;
- `docs`, `test`, `chore`, `ci`, `style`, `refactor`, ainsi que les scopes `ci`, et autres types : aucune release ;
- `!` ou `BREAKING CHANGE:` : rupture ; `MAJOR` à partir de `1.0.0`.

La version calculée est passée au constructeur, injectée dans `benchmark.VERSION` et `release.json`, puis utilisée pour le tag et la release GitHub. Une préversion avec un suffixe comme `alpha.N` est demandée par l’entrée `pre_release` du déclenchement manuel ou par la variable `BENCHMARK_PRE_RELEASE`; elle est publiée avec le marqueur GitHub prerelease. Le même commit et la même version ne peuvent créer deux releases : la concurrence est sérialisée, un tag existant pour un autre commit arrête le workflow et une release partielle est vérifiée puis complétée sans écraser une divergence.

## Preuves

La CI PR exécute les tests, valide la syntaxe des workflows, construit deux fois le runtime et compare les octets. La release produit un lot Actions puis attache le premier artefact validé à la release avec `release-decision.json`, `build-receipt.json` et `SHA256SUMS`. Le service de déploiement vérifie ensuite la provenance, l’intégrité et la santé de la version livrée.

## Exploitation active

Le workflow GitHub est commandé par les variables de dépôt `BENCHMARK_RELEASE_ENABLED` et `BENCHMARK_DEPLOY_ENABLED`. L’environnement GitHub `production` porte les secrets SSH nécessaires au seul job de déploiement. Les jobs de pull request n’utilisent ni cet environnement, ni le runner interne.

Après une fusion dans `main`, la décision SemVer, la release et le déploiement sont automatiques. Un déclenchement manuel reste disponible pour une préversion ou une reprise opérateur ; le SHA demandé doit toujours être le `main` courant.

Le dépôt, la CI et l’artefact ciblent la version de Python définie dans `.python-version`. La production possède un runtime applicatif isolé construit depuis `benchmark/requirements-runtime.lock`. Le service de déploiement refuse une release si l’empreinte de ce lock diffère de celle du runtime provisionné.

Le déploiement utilise un runner GitHub dédié portant le seul label `bench-deploy`. Un hook local refuse avant les étapes tout job qui ne provient pas du dépôt, de `main`, du workflow de release et d’un événement autorisé.

### Déployer depuis un fork

La CI d’une pull request peut fonctionner sur les runners hébergés par GitHub sans accès à l’environnement de déploiement du projet d’origine.

Pour publier et déployer depuis un fork, son propriétaire doit fournir son propre runner GitHub, sa propre cible, son environnement protégé et ses secrets. Un fork n’a accès ni au runner, ni aux secrets, ni à la production du projet d’origine. Le workflow doit être adapté à l’infrastructure du fork sans recopier une topologie ou des identifiants privés.

Le runner du fork doit télécharger l’artefact produit par la même exécution, vérifier `SHA256SUMS` et utiliser un accès restreint à sa propre cible. Le workflow reste rouge tant que le reçu terminal et les contrôles de santé ne concordent pas.

## Urgence manuelle

Utiliser le workflow opérateur d’urgence en mode `submit`, avec une transaction nouvelle et les identités exactes de l’artefact, du reçu de build et du service de déploiement. En cas d’interruption, utiliser `recover` avec la même transaction. Ne jamais lancer `execute` directement, réutiliser une transaction ambiguë, contourner l’autorisation de déploiement ou présenter `SUBMITTED` comme une livraison terminée. Seuls les états terminaux documentés constituent une fin vérifiée ; tout autre état reste à examiner.
