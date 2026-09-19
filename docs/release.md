# Release et livraison Bench-X

## Règle de version

`tools/release.py` lit les Conventional Commits depuis la dernière release SemVer atteignable, ou depuis le commit précédent lors de l’amorçage sans tag :

- `feat` ou rupture sous `0.x` : `MINOR`, avec `PATCH` remis à zéro ;
- `fix` : `PATCH` ;
- `docs`, `test`, `chore`, `ci`, `style`, `refactor` et autres types : aucune release ;
- `!` ou `BREAKING CHANGE:` : rupture ; `MAJOR` à partir de `1.0.0`.

La version calculée est passée au constructeur, injectée dans `benchmark.VERSION`, utilisée pour le tag et la release GitHub, puis affichée par le runtime. Le même commit et la même version ne peuvent créer deux releases : la concurrence est sérialisée et un tag existant pour un autre commit arrête le workflow.

## Preuves

La CI PR exécute les tests, valide les workflows, construit deux fois le runtime et compare les octets. La release produit `benchmark-runtime.tar.gz`, `build-receipt.json` et `SHA256SUMS`. Le contrôleur existant vérifie ensuite le commit, l’empreinte de l’archive, le reçu de build, les blobs, le schéma et `/readyz`.

## Activation contrôlée

Le workflow GitHub reste inactif tant que la variable de dépôt `BENCHMARK_RELEASE_ENABLED` n’est pas définie à `true`. Elle est absente actuellement. L’activation doit être faite après fusion et revue de la PR, avec un premier déclenchement manuel sur le commit `main` voulu ; aucun tag, release ou déploiement n’est créé par cette PR.

Le chemin d’exécution existant est la VM Forgejo dédiée `librenet-benchrunner` (VM1013, `10.20.0.9`), avec le label `benchmark-delivery-isolated`, et non le runner Forgejo habituel. La VM existe, mais `forgejo-runner@benchmark-delivery` est actuellement en échec après des `503 Service Unavailable` lors de `Declare`; le service ne doit pas être relancé ou reconfiguré sans autorisation d’exploitation. Le workflow d’infrastructure reste `workflow_dispatch` et ses drapeaux `BENCHMARK_PREPARATION_ENABLED`, `BENCHMARK_TRANSPORT_READY` et `BENCHMARK_ISOLATED_RUNNER_READY` ne constituent pas une activation automatique.

### Réseau du runner

L’état observé de cette VM est `10.20.0.9` sur le management et `10.60.0.9` sur le KMS; elle n’a pas d’adresse VLAN10 et sa route par défaut est `10.20.0.1`. Cette topologie correspond au runner Forgejo et à son accès KMS existants, pas à un runner GitHub. Le fait qu’une sortie HTTPS vers GitHub fonctionne depuis VLAN20 ne suffit pas à justifier ce placement.

Si la VM est convertie en runner GitHub, le redesign devra lui attribuer une adresse de service VLAN10, faire passer la sortie de service par cette interface et conserver l’administration sur un chemin VLAN20 séparé, sans réutiliser la route d’administration comme transport de build. Il devra aussi limiter les flux au HTTPS sortant vers GitHub, au SSH contrôlé vers `10.10.0.33` et aux dépendances strictement nécessaires; l’adresse VLAN10 exacte, les security groups et le mode d’enregistrement restent à décider dans le dépôt d’infrastructure. Cette conversion n’est pas incluse dans la PR applicative.

Le raccord externe restant est donc borné : rétablir et qualifier cette instance Forgejo dédiée, vérifier les variables et secrets déjà prévus (`BENCHMARK_SSH_PRIVATE_KEY`, `BENCHMARK_ARTIFACT_READ_TOKEN`, les URLs de paquets et les drapeaux), puis relier la release GitHub à l’origine de paquets Forgejo consommée par le contrôleur. Tant que ce raccord n’est pas vérifié, le déploiement reste HOLD et aucune réussite n’est annoncée.

## Urgence manuelle

Utiliser le workflow Forgejo `Benchmark delivery and receipt recovery` en mode `submit`, avec une transaction nouvelle et les identités exactes de l’artefact, du reçu de build et du contrôleur. En cas d’interruption, utiliser `recover` avec la même transaction. Ne jamais lancer `execute` directement, réutiliser une transaction ambiguë, contourner l’autorisation `GO_BENCHMARK_DEPLOY`, ou présenter `SUBMITTED` comme une livraison terminée. Seuls `READY_ADMISSION_CLOSED` et `READY_PREPARATION_OPEN` constituent une fin vérifiée ; tout autre état reste à examiner.
