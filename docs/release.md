# Release et livraison Bench-X

## Règle de version

`tools/release.py` lit les Conventional Commits depuis la dernière release SemVer atteignable, ou depuis le commit précédent lors de l’amorçage sans tag :

- `feat` produit ou rupture sous `0.x` : `MINOR`, avec `PATCH` remis à zéro ;
- `fix` : `PATCH` ;
- `docs`, `test`, `chore`, `ci`, `style`, `refactor`, ainsi que les scopes `ci`, et autres types : aucune release ;
- `!` ou `BREAKING CHANGE:` : rupture ; `MAJOR` à partir de `1.0.0`.

La version calculée est passée au constructeur, injectée dans `benchmark.VERSION`, utilisée pour le tag et la release GitHub, puis affichée par le runtime. Une préversion comme `0.2.0-alpha.1` est possible uniquement par déclenchement manuel explicite ; elle ne crée pas un jalon concurrent. Le même commit et la même version ne peuvent créer deux releases : la concurrence est sérialisée et un tag existant pour un autre commit arrête le workflow.

## Preuves

La CI PR exécute les tests, valide les workflows, construit deux fois le runtime et compare les octets. La release produit `benchmark-runtime.tar.gz`, `build-receipt.json` et `SHA256SUMS`. Le contrôleur existant vérifie ensuite le commit, l’empreinte de l’archive, le reçu de build, les blobs, le schéma et `/readyz`.

## Activation contrôlée

Le workflow GitHub reste inactif tant que la variable de dépôt `BENCHMARK_RELEASE_ENABLED` n’est pas définie à `true`. Elle est absente actuellement. L’activation doit être faite après fusion et revue de la PR, avec un premier déclenchement manuel sur le commit `main` voulu ; aucun tag, release ou déploiement n’est créé par cette PR.

Le dépôt et l’artefact ciblent désormais Python `3.14.7` via `.python-version`, la CI, la construction et les commandes locales. Les deux VMs observées utilisent encore Python `3.12.3`; aucune release ou bascule ne doit donc être activée avant l’installation vérifiée de Python `3.14.7` sur `labx-bench` et sur le futur runner GitHub, puis l’exécution explicite des services avec cet interpréteur.

Le choix cible retenu pour la suite est de convertir la VM dédiée `librenet-benchrunner` (VM1013) en runner GitHub réutilisable, plutôt que de conserver le runner Forgejo isolé. La VM existe, mais `forgejo-runner@benchmark-delivery` est actuellement en échec après des `503 Service Unavailable` lors de `Declare`; aucun redémarrage ou changement distant n’est inclus ici.

### Réseau du runner

L’état observé de cette VM est `10.20.0.9` sur le management et `10.60.0.9` sur le KMS; elle n’a pas d’adresse VLAN10 et sa route par défaut est `10.20.0.1`. Pour le futur runner GitHub, l’adresse de service devra être sur VLAN10, avec administration séparée sur VLAN20 et KMS conservé seulement si réellement nécessaire.

Un runner GitHub n’a pas besoin d’un nom de domaine `librenet.work` ni d’une connexion entrante depuis GitHub : il établit des connexions sortantes HTTPS vers GitHub, reçoit ainsi les jobs, puis peut atteindre `labx-bench` en SSH interne pendant le job de déploiement. Les jobs de PR et de build restent sur des runners GitHub hébergés ; seul le déploiement de `main`, après validation, doit utiliser le runner interne. Les domaines GitHub requis et les security groups doivent être autorisés dans le dépôt d’infrastructure. Le domaine existant `bench.librenet.work` concerne le service Bench-X, pas l’enregistrement du runner.

Le raccord externe restant est donc borné : reconfigurer cette VM dans `cybrel-infrastructure` comme runner GitHub, lui attribuer le réseau VLAN10/VLAN20 validé, installer Python `3.14.7`, enregistrer le runner au niveau d’une organisation GitHub si plusieurs dépôts doivent le partager, puis donner au job de déploiement l’accès SSH strictement contrôlé au contrôleur existant. Tant que ces opérations ne sont pas vérifiées, le déploiement reste HOLD et aucune réussite n’est annoncée.

## Urgence manuelle

Utiliser le workflow Forgejo `Benchmark delivery and receipt recovery` en mode `submit`, avec une transaction nouvelle et les identités exactes de l’artefact, du reçu de build et du contrôleur. En cas d’interruption, utiliser `recover` avec la même transaction. Ne jamais lancer `execute` directement, réutiliser une transaction ambiguë, contourner l’autorisation `GO_BENCHMARK_DEPLOY`, ou présenter `SUBMITTED` comme une livraison terminée. Seuls `READY_ADMISSION_CLOSED` et `READY_PREPARATION_OPEN` constituent une fin vérifiée ; tout autre état reste à examiner.
