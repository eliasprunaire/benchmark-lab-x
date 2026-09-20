---
style_gate: pass
---

# Release et livraison Bench-X

## Règle de version

`tools/release.py` lit les Conventional Commits depuis la dernière release SemVer atteignable, ou depuis le commit précédent lors de l’amorçage sans tag :

- `feat` produit ou rupture sous `0.x` : `MINOR`, avec `PATCH` remis à zéro ;
- `fix` : `PATCH` ;
- `docs`, `test`, `chore`, `ci`, `style`, `refactor`, ainsi que les scopes `ci`, et autres types : aucune release ;
- `!` ou `BREAKING CHANGE:` : rupture ; `MAJOR` à partir de `1.0.0`.

La version calculée est passée au constructeur, injectée dans `benchmark.VERSION` et `release.json`, utilisée pour le tag et la release GitHub, puis affichée par `/healthz` et `/readyz`. Une préversion avec un suffixe comme `alpha.N` est demandée par l’entrée `pre_release` du déclenchement manuel ou par la variable `BENCHMARK_PRE_RELEASE`; elle est publiée avec le marqueur GitHub prerelease. Le même commit et la même version ne peuvent créer deux releases : la concurrence est sérialisée, un tag existant pour un autre commit arrête le workflow et une release partielle est vérifiée puis complétée sans écraser une divergence.

## Preuves

La CI PR exécute les tests, valide la syntaxe des workflows, construit deux fois le runtime et compare les octets. La release produit un lot Actions puis attache le premier artefact validé à la release avec `release-decision.json`, `build-receipt.json` et `SHA256SUMS`. Le contrôleur existant vérifie ensuite le commit, la version, l’empreinte de l’archive, le reçu de build, les blobs, le schéma et `/readyz`.

## Exploitation active

Le workflow GitHub est commandé par les variables de dépôt `BENCHMARK_RELEASE_ENABLED` et `BENCHMARK_DEPLOY_ENABLED`. L’environnement GitHub `production` porte les secrets SSH nécessaires au seul job de déploiement. Les jobs de pull request n’utilisent ni cet environnement, ni le runner interne.

Après une fusion dans `main`, la décision SemVer, la release et le déploiement sont automatiques. Un déclenchement manuel reste disponible pour une préversion ou une reprise opérateur ; le SHA demandé doit toujours être le `main` courant.

Le dépôt, la CI et l’artefact ciblent la version de Python définie dans `.python-version`. Le runner et `labx-bench` utilisent Ubuntu 26.04 ; `labx-bench` possède en plus un runtime applicatif isolé construit depuis `benchmark/requirements-runtime.lock`. Le contrôleur refuse une release si l’empreinte de ce lock diffère de celle du runtime provisionné.

La VM dédiée `librenet-benchrunner` (VM1013) est enregistrée comme runner GitHub du dépôt avec le seul label `bench-deploy`. L’ancien runner Forgejo et Docker sont désactivés. Un hook local refuse avant les étapes tout job qui ne provient pas du dépôt, de `main`, du workflow de release et d’un événement autorisé.

### Réseau du runner

La VM utilise désormais `10.10.0.34` sur VLAN10 pour sa route par défaut et `10.20.0.9` sur VLAN20 pour l’administration. Une route explicite conserve le retour vers `172.16.100.0/24` par VLAN20. L’interface VLAN60 a été retirée et l’accès au KMS est refusé ; le runner GitHub n’en a pas besoin.

Un runner GitHub n’a pas besoin d’un nom de domaine `librenet.work` ni d’une connexion entrante depuis GitHub : il établit des connexions sortantes HTTPS vers GitHub, reçoit ainsi les jobs, puis atteint `labx-bench` en SSH interne pendant le déploiement. Les jobs de PR et de construction restent sur des runners GitHub hébergés ; seul le déploiement validé utilise le runner interne. Le domaine `bench.librenet.work` concerne le service Bench-X, pas l’enregistrement du runner.

Le runner télécharge l’artefact Actions produit par la même exécution, vérifie `SHA256SUMS`, charge la clé dans un agent SSH temporaire et contacte le compte forcé du contrôleur avec une vérification stricte de l’hôte. Il ne dépend pas du VLAN60 ni du KMS. Le workflow reste rouge tant que le contrôleur, le reçu terminal, `/healthz` et `/readyz` ne concordent pas.

## Urgence manuelle

Utiliser le workflow Forgejo `Benchmark delivery and receipt recovery` en mode `submit`, avec une transaction nouvelle et les identités exactes de l’artefact, du reçu de build et du contrôleur. En cas d’interruption, utiliser `recover` avec la même transaction. Ne jamais lancer `execute` directement, réutiliser une transaction ambiguë, contourner l’autorisation `GO_BENCHMARK_DEPLOY`, ou présenter `SUBMITTED` comme une livraison terminée. Seuls `READY_ADMISSION_CLOSED` et `READY_PREPARATION_OPEN` constituent une fin vérifiée ; tout autre état reste à examiner.
