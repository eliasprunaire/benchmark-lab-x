---
style_gate: pass
---

# Outillage local de Benchmark Lab-X

Cet outil Python utilise la bibliothèque standard. Il prépare une campagne scellée, exécute une fois chaque configuration après autorité S9, produit une revue aveugle, puis construit hors ligne une Salle de décision après décisions et autorité S10.

Il utilise le scénario et le panel figés de [campaign.json](campaign.json), Pi `0.84.4`, un plafond historique de 0,50 USD et des fichiers privés sous `runs/`. Ces paramètres appartiennent à ce scénario ; ils ne définissent pas les tâches, le panel ni les budgets du [jalon produit](../docs/PRD.md#51-périmètre-010). La revue et la construction refusent un panel incomplet ; route et effort non observés restent `INCONNU`.

Les commandes ci-dessous décrivent une séquence : obtenir l’autorité d’acquisition avant `collect`, puis les décisions et l’autorité de construction avant `build`. Elles ne constituent pas un script à lancer d’un bloc. Le lancement d’une campagne réelle exige des identités actuellement vérifiées ; le panel historique ne prouve pas la disponibilité des modèles.

Depuis la racine du dépôt, sur macOS, avec Python 3 et le binaire Pi requis :

```bash
mkdir -p -m 700 runs
python3 -B -m benchmark_lab_x prepare --run-dir runs/ma-campagne --pi /chemin/reel/vers/pi
python3 -B -m benchmark_lab_x collect --run-dir runs/ma-campagne --authority /chemin/authorization-s9.json
python3 -B -m benchmark_lab_x review --run-dir runs/ma-campagne
python3 -B -m benchmark_lab_x build --run-dir runs/ma-campagne --decisions /chemin/decisions.json --authority /chemin/authorization-s10.json
python3 -B -m benchmark_lab_x show --run-dir runs/ma-campagne
```

`show` vérifie le sceau final et ouvre la page existante sans la régénérer. L’alternative directe est `open runs/ma-campagne/index.html`.

La page identifie la tâche par un brief (titre public, contexte, objectif, décision éclairée) et par le résultat attendu contractuel. Pour une future campagne, ce brief se fige avant exécution dans `campaign.json` sous la clé `brief` (exactement `title`, `context`, `objective`, `decision`) et entre ainsi dans l’empreinte du contrat ; le résultat attendu reste `expected_result`, unique source contractuelle, et un brief qui porterait `expected` est refusé. Deux replis existent quand ce champ manque : le scénario historique `quote-thread-summary` dispose d’un brief de présentation propre, rédigé après coup, signalé comme tel sur la page et relié à aucune empreinte de source ; toute autre tâche retombe sur son identifiant et le résultat attendu du contrat, avec les autres champs signalés non documentés.

Après une évolution du rendu, `present` construit une nouvelle présentation locale depuis un run final scellé, sans modifier ses résultats ni relancer de candidat :

```bash
python3 -B -m benchmark_lab_x present --source-run runs/ma-campagne --run-dir runs/ma-presentation
python3 -B -m benchmark_lab_x show --run-dir runs/ma-presentation
```

## Témoins d’autorité

Les schémas S9 et S10 sont vérifiés strictement. Les autorités restent externes à `prepare` et doivent utiliser les empreintes du run concerné.

S9 :

```json
{
  "schema": "benchmark-lab-x-s9-authorization-1",
  "effect": "candidate_calls_and_spend_s9",
  "authority_id": "TEMOIN-TEMPORAIRE-S9",
  "run": "runs/ma-campagne",
  "seal_sha256": "<sha256 seal.json>",
  "contract_sha256": "<sources.campaign.json du sceau>",
  "panel_sha256": "<artifacts.panel.json du sceau>",
  "pi": {
    "binary_sha256": "<pi.sha256 du sceau>",
    "version": "0.84.4",
    "settings_sha256": "<artifacts.settings.json du sceau>",
    "models_sha256": "<artifacts.models.json du sceau>"
  },
  "budget": {
    "currency": "USD",
    "cap": 0.5,
    "price_date": "<date>",
    "price_source": "<source>",
    "forecasts": {"C1": 0.1, "C2": 0.1, "C3": 0.1}
  }
}
```

Les décisions contiennent `accepted: true`, l’empreinte exacte de `review.json`, une décision par identifiant aveugle et les neuf constats `O1` à `O6`, `E1` à `E3`. Chaque constat possède un texte `finding` et une référence `evidence` parmi `blind-copy`, `receipt`, `incident`. `S1` et `S2` valent `acceptable` ou `excellent` uniquement pour `SATISFAIT`.

S10 lie le run, `seal.json`, `review.json` et les octets exacts des décisions :

```json
{
  "schema": "benchmark-lab-x-s10-authorization-1",
  "effect": "product_execution_and_acceptance_s10",
  "authority_id": "TEMOIN-TEMPORAIRE-S10-DISTINCT",
  "run": "runs/ma-campagne",
  "seal_sha256": "<sha256 seal.json>",
  "review_sha256": "<sha256 review.json>",
  "decisions_sha256": "<sha256 du fichier decisions.json externe>"
}
```

Ces témoins documentent le format. Ils n’accordent aucune autorité réelle.

## Compatibilité et intégrité

La commande courante est `python3 -B -m benchmark_lab_x`. Les nouveaux enregistrements utilisent le préfixe de schéma `benchmark-lab-x-`, suivi de leur objet et de leur version de format. Les exemples d’autorité ci-dessus correspondent à ces formats.

Le contrat figé dans `campaign.json`, les données d’entrée et la carte du scénario conservent leurs octets et identifiants historiques. Les lecteurs `show` et `present` reconnaissent explicitement les anciens formats de résultats et de sceaux. `present` copie les résultats à l’identique dans une présentation distincte et conserve le lien au sceau source ; il ne convertit pas une campagne et ne change aucun verdict.

Une préparation antérieure au changement de moteur n’est pas réutilisable pour acquérir, revoir ou construire : ses empreintes de sources ne correspondent plus. Il faut une nouvelle préparation et les autorités correspondantes. Aucun ancien reçu ou témoin d’autorité n’est converti automatiquement. Cette migration de noms ne constitue pas la livraison du périmètre produit.

Le workflow [GitHub Pages](../.github/workflows/pages.yml) publie `pages/` lors des changements configurés sur `main`. Construire une page locale et l’intégrer dans cette publication sont deux actions d’autorités distinctes.

## Interfaces locales du service Linux en construction

L’[ARD](../docs/ARD.md#3-pi-comme-frontière-constante) impose OpenRouter pour tous les appels modèles du produit : préparation, correction, jugement et candidats. Le secours officiel candidat explicitement autorisé suit les conditions décrites ci-dessous ; aucune substitution implicite de modèle n’est admise. Pi reste le harnais des candidats et doit utiliser OpenRouter ; le moteur historique le sélectionne déjà avec `--provider openrouter` et désactive le repli fournisseur. S4 fournit une interface à transport injecté ; le raccordement Pi/OpenRouter et le jugement opérateur sont décrits dans la section « Première comparaison privée » ci-dessous. S5 ne raccorde aucun modèle juge réel. Les contrats historiques et les outils de développement Graph/Codex restent hors de cette nouvelle règle produit.

Le module `benchmark_lab_x.runtime` fournit une initialisation privée, la vérification de SQLite et des pièces, la maintenance, une sauvegarde cohérente et une restauration vers un nouvel emplacement. Ces interfaces sont distinctes du moteur historique ci-dessus. Les processus web et exécuteur sont fournis ci-dessous. Le candidat local ajoute le parcours fictif S2 décrit plus bas. Le raccordement local OpenRouter de préparation est décrit ci-dessous ; ses essais réels restent à autoriser et vérifier.

Avec Python 3.12 ou supérieur, le répertoire parent des données doit exister. L’initialisation crée son emplacement privé ou utilise le répertoire vide préparé par Ansible sous le compte de service. Elle refuse tout emplacement contenant déjà des données :

```sh
python3 -B -m benchmark_lab_x.runtime initialize --data /chemin/prive/benchmark
python3 -B -m benchmark_lab_x.runtime verify --data /chemin/prive/benchmark
python3 -B -m benchmark_lab_x.runtime status --data /chemin/prive/benchmark
```

Ces commandes n’émettent aucun appel modèle. Les pièces et les révisions de dossier sont immuables. Une empreinte divergente, une pièce orpheline, une référence dangereuse ou un schéma inconnu provoque un refus. La bibliothèque S1 conserve les montants en texte décimal exact, les intentions, les réservations et les reçus. Elle persiste l’état `EMISSION_POSSIBLE` avant le transport. Elle ne fournit pas encore le transport ni l’interface publique d’autorisation.

Sans sélection opérateur de l’assistant, le runtime ne fournit aucun transport. Sur une base S1 seule, `admission` reste faux. L’extension explicite S2 permet une admission opérateur ; `maintenance` la ferme durablement. Même avec cette admission configurée, l’absence de transport interdit tout appel. `quiescence` et `backup` refusent les opérations S1 encore en `EMISSION_POSSIBLE`. Après arrêt du processus, les effets inconnus sont conservés en `AMBIGUOUS` ; ils restent sauvegardables et bloquent les nouveaux appels dépendants dans S1 :

```sh
python3 -B -m benchmark_lab_x.runtime maintenance --data /chemin/prive/benchmark
python3 -B -m benchmark_lab_x.runtime quiescence --data /chemin/prive/benchmark
python3 -B -m benchmark_lab_x.runtime backup --data /chemin/prive/benchmark --destination /chemin/prive/sauvegarde-neuve
python3 -B -m benchmark_lab_x.runtime verify-backup --data /chemin/prive/sauvegarde-neuve
python3 -B -m benchmark_lab_x.runtime restore --data /chemin/prive/sauvegarde-neuve --destination /chemin/prive/restauration-neuve
```

`quiescence` exige aussi le verrou d’écriture SQLite, sans attente, pendant la lecture de l’état. Une transaction active, notamment pendant le callback de qualification S3, provoque le refus existant 78 / `HOLD` / `OPERATION_NOT_VERIFIED`. Le contrôle libère ses verrous au succès comme au refus, sans écrire de données ni ajouter de champ au protocole de santé. Ce contrôle ponctuel ne crée aucune barrière persistante : l’opérateur doit suspendre les futurs lancements et les publications avant une maintenance.

La sauvegarde bloque les écrivains SQLite pendant la copie des pièces et vérifie leurs liens. La restauration préserve la source et toute cible existante. Elle crée le marqueur privé `restore.json`, conservé par les sauvegardes suivantes et exposé par `restore_pending`. Aucun effacement ni reprise n’est fourni : le rapprochement des effets postérieurs à la sauvegarde doit être réalisé avant toute future ouverture des appels. Une copie de données vérifiée ne prouve pas la restauration d’un service Linux ni une récupération PBS.

`tools/build_runtime.py` construit une archive déterministe depuis les seuls fichiers suivis d’un commit complet. Il ignore les modifications du worktree et refuse une source dépourvue des interfaces runtime. La sortie JSON relie le commit, l’arbre, les blobs Git et l’empreinte de l’archive. Le build n’installe aucune dépendance et n’exécute pas la source construite :

```sh
python3 tools/build_runtime.py --source <commit-produit-complet> --output /chemin/benchmark-runtime.tar.gz
```

Le reçu décrit la construction effectuée ; son authenticité doit être vérifiée depuis le job ou l’opérateur identifié. L’archive utilise le format `release.json` du candidat infra. Elle inclut les deux processus, leur commande et le stockage S1 ; le build refuse une archive sans ces composants. Aucun tag de livraison 0.1.0 n’est créé par ces interfaces.

Les commandes `benchmark-runtime web --public … --socket …` et
`benchmark-runtime executor --data … --socket …` fournissent les processus
Linux. Le web expose `/healthz` et `/readyz` sans appel modèle ; le second contrôle
interroge l'exécuteur par socket Unix et vérifie le stockage. Au démarrage,
l'exécuteur conserve les émissions S1 sans reçu en `AMBIGUOUS`, avec leur
réservation et un motif d'interruption. Les campagnes locales S4 utilisent le travailleur explicite décrit ci-dessous ; ce processus ne reprend aucune file candidate et ne fournit pas S5.

En dehors de `/preparation`, le web sert une projection nommée par l'empreinte de son manifeste
`publication.json`, sélectionnée par `public/active.json`. Chaque fichier servi
est vérifié ; sans publication vérifiée, la racine répond 503. Ce mécanisme ne
publie aucun dossier du parcours privé S2 et ne fournit pas S6. Les deux processus, leur
arrêt et leur redémarrage sont testés avec de vraies sockets locales ; la preuve
de déploiement Linux reste distincte.

Le schéma S1 reste en version 1 avec ses contraintes exactes. Le premier candidat
de la PR #197 possédait un autre schéma portant aussi le numéro 1 ; il est refusé
sans conversion ni réécriture. Un déploiement sur des données de ce candidat
exige une décision distincte de migration ou d'initialisation dans un nouvel
emplacement, en préservant la base d'origine. Cette intégration ne migre aucune
donnée et ne déploie aucun service.

## Parcours fictif de préparation S2

Le module fournit `/preparation` : saisie du besoin, clarification, consultation des pièces, correction et validation du dossier, de sa révision et de l’empreinte exacte du paquet. Les preuves historiques de ce parcours utilisent les services locaux et un transport fictif de test. L’assistant réel, l’ouverture du service et la publication gardent leurs qualifications et autorités distinctes.

L’exécuteur existant porte les sessions et les opérations ; le web relaie les actions par sa socket Unix et rend des formulaires HTML natifs, sans script ni dépendance ajoutée. Un GET ne lance aucun travail. Après un envoi, le lien vers le dossier permet de consulter l’attente puis le résultat. Les erreurs de révision imposent de consulter la version courante. Les anciennes révisions, pièces et validations restent consultables ; tout nouveau paquet exige un nouvel accord. Le besoin et les réponses restent dans le payload S1, les corrections dans les actions attribuées. Les paramètres fictifs ne deviennent pas des accords. La validation du besoin ne qualifie pas la référence et n’approuve aucun contrat, appel, budget ou publication.

Le cookie `benchmark_session` est opaque, `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/preparation`, sans `Domain`, `Expires` ou `Max-Age`. Le stockage ne conserve que son SHA-256 et une identité interne. Le même navigateur retrouve ses dossiers tant qu’il conserve ce cookie ; sa survie à la fermeture n’est pas garantie. Perdre le cookie fait perdre l’accès sans effacer les dossiers. Aucun compte, durée de conservation ou récupération n’est ajouté. Chaque action et pièce exige sa session propriétaire ; les POST exigent aussi le jeton CSRF. Les champs d’autorité ne sont pas acceptés dans les formulaires. Les pièces sont du texte inerte, et la référence privée de jugement reste hors du paquet demandeur/candidat.

L’initialisation suivante est une opération locale explicite sur une base S1 intégrée neuve, sans données métier ni pièces résiduelles. Elle refuse une base S1 peuplée, une extension partielle, un schéma inconnu et le schéma distinct de #197 :

```sh
python3 -B -m benchmark_lab_x.runtime initialize-preparation --data /chemin/prive/benchmark
```

Elle ajoute les six tables identifiées `benchmark-lab-x/preparation/v1`, dans une seule transaction, avec admission fermée. Sur une extension déjà exacte, elle ne réécrit rien. `user_version=1` et les tables S1 restent inchangés. Le lecteur courant conserve la lecture des deux formes S1 ; les lecteurs S1 antérieurs refusent les tables S2 supplémentaires. Aucune migration ou compatibilité inverse n’est annoncée. Sauvegarde, restauration et vérification comprennent les jointures S2 et les empreintes des paquets.

L’opérateur peut enregistrer une admission depuis un fichier privé contenant exactement `authority_id`, `budget_id`, `reserve_amount` (texte décimal) et `requested_configuration` (objet non vide). Le budget S1 doit déjà exister ; cette commande ne crée pas d’enveloppe :

```sh
python3 -B -m benchmark_lab_x.runtime admit-preparation --data /chemin/prive/benchmark --authority /chemin/prive/autorite.json
```

Cette commande d’admission ne fournit aucun transport. Le point d’injection reste `serve_executor(data, socket_path, source, *, transport=None)`. Le lanceur peut sélectionner explicitement l’assistant OpenRouter décrit ci-dessous ; les tests historiques injectent leur fonction fictive `transport(operation, request)`. Aucun paramètre HTTP ni fichier utilisateur ne sélectionne le transport. La valeur par défaut refuse tout appel. Les valeurs fictives des tests ne prouvent aucun coût réel ; les preuves historiques sur abonnement gardent leur statut `UNMEASURED`.

L’action, son contexte exact, l’intention S1 et la réserve sont persistés avant accusé de réception. L’admission et le marqueur de restauration sont revérifiés dans la transaction qui précède l’émission. Le travailleur possède sa connexion SQLite et laisse le gestionnaire de santé disponible. Un doublon aux mêmes données retrouve son opération ; une identité réutilisée avec un autre contenu est refusée. Un reçu publié conserve les coûts sourcés, y compris `UNKNOWN`, puis les pièces vérifiées et le nouveau pointeur deviennent visibles ensemble. Si le reçu S1 est valide mais le résultat applicatif inutilisable, le reçu et le coût originaux sont conservés avec une révision suspendue et l’admission fermée, dans une seule transaction. Aucun paquet ni accord ne sont inventés. Un effet sans reçu S1 valide reste ambigu ; une interruption ne relance aucun travail, même une intention jamais émise. Démarrage, maintenance et restauration ferment l’admission. Le marqueur `restore_pending` ne peut être supprimé par le navigateur ou `admit-preparation`.

L’API négocie JSON avec `Accept: application/json`. Ses routes sont :

| Méthode | Route | Effet |
|---|---|---|
| GET | `/preparation` | Session, CSRF et liste des dossiers propres |
| POST | `/preparation/dossiers` | Brouillon et opération réservée, réponse 202 |
| GET | `/preparation/dossiers/{id}` | Vue courante, attente ou suspension |
| GET | `/preparation/dossiers/{id}/revisions/{n}` | Révision exacte conservée |
| POST | `/preparation/dossiers/{id}/messages` | Clarification ou correction depuis la révision courante |
| POST | `/preparation/dossiers/{id}/validation` | Accord lié au dossier, à la révision et à `package_sha256` |
| GET | `/preparation/dossiers/{id}/revisions/{n}/pieces/{piece_id}` | Octets vérifiés d’une pièce candidate appartenant au paquet |

Les requêtes JSON et formulaires portent les mêmes champs. Une création porte `dossier_id`, `action_id`, `request`, `csrf_token` ; un message porte `action_id`, `revision` (entier JSON), `kind` (`clarify` ou `correct`), `message`, `csrf_token` ; une validation porte `dossier_id`, `revision`, `package_sha256`, `csrf_token`. Les champs supplémentaires sont refusés. Une soumission ne transforme pas son texte en autorité opérateur.

Les contrôles S2 de publication du paquet portent sur sa structure, les jointures, la relecture des pièces et leurs empreintes. À eux seuls, ils laissent la justesse métier de la référence NON VÉRIFIÉ et `qualified` faux. L’extension locale S3 ci-dessous conserve une qualification distincte. Les déclarations de limites et les demandes de clarification sont celles du transport fictif attribué ; aucun assistant réel n’a été évalué.

La commande CI est `uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests` : 870 tests passent sur macOS pour le candidat corrigé. Elle exclut `benchmark_lab_x/test_demo.py`, dont les 69 tests ont été exécutés séparément sur macOS. Les huit parcours HTTP avec vrais processus Web et exécuteur locaux passent également. Les transports sont entièrement fictifs ; ces preuves ne qualifient aucun assistant réel.

La vérification manuelle du 7 septembre 2026 utilise macOS 27.0 (26A5425a) et Chrome 152.0.7977.83 installé. Saisie, clarification, consultation de la pièce textuelle, retour à l’aperçu, validation et correction ont été effectués au clavier, avec focus visible. Le texte de la pièce a été effectivement affiché ; la correction conserve les accords antérieurs et exige une nouvelle validation. Le zoom Chrome à 200 % a été observé sur l’aperçu, ses limites et son lien de pièce. Le contrôle antérieur à 320 × 720 dans le navigateur Codex a vérifié l’absence de débordement horizontal de la page ; il reste une preuve distincte.

Le 7 septembre 2026, Ayo a retiré l’exigence de qualification au lecteur d’écran du périmètre produit. Le contrôle VoiceOver a été interrompu sans verdict de réussite ; il ne conditionne plus la fusion S2. La [CI Ubuntu de la PR #199](https://github.com/ayoahha/benchmark-lab-x/actions/runs/34130571182) a exécuté 870 tests et construit le runtime de `009ae3293ba89955db47a1af3448d987ac23dabc`, avec les fichiers de préparation inclus. Elle ne lance ni les huit parcours HTTP du complément local ni la suite demo ; ces preuves restent acquises séparément sur macOS. Le test HTTP relaie explicitement le cookie Secure ; la qualification HTTPS et d’exploitation reste distincte de la preuve logicielle.

## Assistant de préparation via OpenRouter, candidat local

[Le transport unique](openrouter_preparation.py) raccorde l’exécuteur S2 à `POST https://openrouter.ai/api/v1/chat/completions`. Le responsable choisit l’assistant au démarrage par `--preparation-assistant` : l’alias historique `glm-5.3-flash`, qui charge [le profil de compatibilité](glm-5.3-flash.profile.json), ou le chemin d’un profil JSON local déjà approuvé. Sans cette option, aucun transport de préparation n’est chargé. Le profil fige modèle, une unique révision, paramètres, routes, capacités, message système et limites ; son empreinte canonique, le relevé tarifaire et la réserve sont liés à la configuration demandée. Le chemin hôte du fichier n’entre pas dans cette configuration, les messages ni les reçus. Ce raccordement applique le canal unique OpenRouter décidé pour tous les appels modèles du produit. Les preuves et reçus historiques restent inchangés. Après intégration et autorisation d’essai distinctes, l’opérateur peut sélectionner :

```sh
python3 -B -m benchmark_lab_x.runtime executor --data /chemin/prive/benchmark --socket /chemin/prive/executor.sock --preparation-assistant glm-5.3-flash
python3 -B -m benchmark_lab_x.runtime executor --data /chemin/prive/benchmark --socket /chemin/prive/executor.sock --preparation-assistant /chemin/prive/assistant.profile.json
```

`OPENROUTER_API_KEY` est injectée uniquement dans l’environnement de cet exécuteur par le mécanisme privé de l’opérateur, jamais dans la commande, l’autorité, le navigateur ou l’environnement du web. Le lanceur la retire de son environnement après lecture. Une clé absente ou invalide donne `78 / HOLD` sans connexion. L’ancien secret Z.AI n’est plus utilisé et aucun second transport n’est conservé. Sans sélection explicite, le transport est absent ; le démarrage ferme toujours l’admission.

La requête utilise les [options natives OpenRouter](https://openrouter.ai/docs/api_reference/parameters) : `temperature=1`, `top_p=0.95`, `reasoning={"effort":"low"}`, `max_tokens=16384`, `stream=false`, `response_format={"type":"json_object"}`. Le relevé public du modèle du 10 septembre 2026 annonce les efforts `max`, `high` et `low`, avec raisonnement obligatoire, sans budget direct `reasoning.max_tokens` annoncé. L’effort `low` réduit le raisonnement demandé pour laisser de la place à la réponse dans les 16 384 tokens de sortie, qui incluent le raisonnement. La [documentation du champ reasoning](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) ne garantit pas un quota strict de tokens de réponse. Ce réglage reprend la marge totale de 16 384 tokens du collecteur historique sans supposer un budget direct supporté par GLM. Les trois endpoints autorisés annoncent un plafond de sortie supérieur à cette valeur. L’admission doit être régénérée avec la configuration du runtime livré ; les anciennes opérations restent intactes. Aucun quota d’échanges n’est ajouté. Un message système précède le contexte S2 exact. Aucun champ `thinking` propre à Z.AI, outil ou autre modèle n’est envoyé.

La route demandée autorise le secours natif OpenRouter dans `provider.only=["modal/fp8","coreweave/fp8","novita/fp8"]`, avec le même `order`, `allow_fallbacks=true` et `require_parameters=true`. Le relevé public du 10 septembre 2026 annonce ces trois endpoints FP8, le modèle et les paramètres requis ; leurs disponibilités rapportées sur un jour justifient cet ordre ponctuel, sans prouver l’accès du compte ni une supériorité générale. Les [règles de routage](https://openrouter.ai/docs/guides/routing/provider-selection) limitent les fournisseurs admissibles et permettent de passer au suivant après un échec. Trois tags autorisés ne garantissent pas trois requêtes internes maximum. Une valeur `attempt` supérieure est conservée avec cette limite d’attribution. Le produit envoie une seule requête HTTP, sans boucle locale ni recherche d’une autre réponse après succès ; aucun proxy d’environnement ou autre canal n’est utilisé.

Avant l’admission, l’opérateur renouvelle la consultation publique décrite ci-dessous, avec le contexte complet annoncé par l’API et la limite de sortie configurée. Exemple pour le contexte observé le 10 septembre 2026 :

```sh
python3 -B -m benchmark_lab_x.runtime forecast-prices --model z-ai/glm-5.3-flash \
  --input-tokens 1310720 --output-tokens 16384 --cached-input-tokens 0 \
  --preparation-assistant glm-5.3-flash
```

`--model`, le profil sélectionné et le relevé obtenu doivent désigner le même modèle ; une divergence refuse avant l’appel d’inférence. Le champ `preparation` retourné contient `requested_configuration` et `reserve_amount`. L’opérateur les reprend dans l’autorité S2 existante avec l’`authority_id` de l’essai et le `budget_id` de l’enveloppe S1 unique en USD, plafonnée à 100 USD pour clarification, génération et corrections. La configuration contient l’identité et l’empreinte du profil, le relevé daté, les sources, la prévision, les routes, l’unique `revision` et `model_identities` dérivées de celle-ci : `[model]` si la révision égale le modèle, sinon exactement `[model, revision]`. Pour le profil historique GLM, `model` est `z-ai/glm-5.3-flash` et `revision` est `z-ai/glm-5.3-flash-20260826`. Les champs modèle de la réponse et des tentatives peuvent porter l’une de ces deux identités ; une troisième identité, une autre révision, route ou capacité n’est pas admise. Aucun compte, catalogue ou synchronisation n’est créé. Le formulaire utilisateur ne choisit aucun de ces champs.

La réserve proposée est une référence indicative calculée avec les tarifs `prompt` et `completion` du modèle dans le relevé figé, sur le contexte complet et 16 384 tokens de sortie. Le calcul réutilise `openrouter_prices.price_row`. Les prix des endpoints, leurs écarts, les économies de cache et les remises ne conditionnent pas ce calcul ; les identités et paramètres des trois endpoints restent vérifiés. Si le tarif modèle manque ou est invalide, `reserve_amount` proposé est `null` : l’opérateur conserve la réserve explicite de son admission existante ou en fixe une sous l’autorité budgétaire acquise. Aucun montant nul n’est déduit de cette absence. La réserve n’est ni un coût observé ni un plafond de facture. Le relevé est conservé dans l’admission, sans actualisation silencieuse.

Après une réponse exploitable publiée en clarification, aperçu ou autre état applicatif valide, `indicative_cost` conserve le sous-total indicatif calculé avec `prompt_tokens × tarif prompt + completion_tokens × tarif completion` du modèle. Les [tokens de raisonnement sont des tokens de sortie](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) : ils ne sont pas ajoutés une seconde fois. Le calcul ne soustrait pas le cache et n’applique pas les remises ni les frais annexes. Il conserve quantités et source datée du tarif ; le prix exact du fournisseur sélectionné n’est pas requis. Tokens ou tarifs absents ou invalides donnent une estimation non calculable, sans blocage financier automatique. Une erreur 429, un timeout ou une sortie refusée ne produit pas d’estimation de succès. La vue propriétaire indique « Estimation indicative » et « ce montant n’est pas une facture », séparément du débit rapporté lorsqu’il existe. Les anciens reçus ne sont ni recalculés ni réécrits.

La configuration, la devise, l’enveloppe et la réserve sont vérifiées avant émission. Le corps complet est validé dans la transaction de soumission : un refus de taille ou de configuration ne laisse ni nouvelle action, ni intention, ni réservation. Une demande courte peut ensuite être acceptée sous la même enveloppe. Les gardes d’admission, de restauration et de budget restent revérifiés juste avant émission. Le corps HTTP exact est déjà conservé dans `operation.resources[1]` à l’acceptation.

Le corps est limité à 65 536 octets UTF-8, la réponse à 2 Mio, l’attente réseau à 120 secondes par opération de socket. Ces trois valeurs sont aussi les plafonds absolus de cet adaptateur : un profil peut les reprendre ou les réduire, jamais les dépasser. Ces protections existantes ne comptent pas les tokens et ne garantissent pas une durée totale face à un serveur lent. Une réponse partielle, une identité divergente, une sortie coupée ou un appel outil ne produit aucun paquet. Le reçu et la dépense rapportée restent conservés ; une exception sans reçu laisse l’émission ambiguë, sans rejeu au redémarrage.

Le [coût observé OpenRouter](https://openrouter.ai/docs/cookbook/administration/usage-accounting) vient uniquement de `/usage/cost`, montant débité du compte en crédits dont l’[unité de base est le dollar US](https://openrouter.ai/docs/faq). Il n’est pas remplacé par `cost_details.upstream_inference_cost`, une prévision ou un tarif direct. Un montant fini et non négatif dans une réponse HTTP 200 complète donne `KNOWN`, même si les détails de tokens facultatifs ou le résultat applicatif sont inutilisables. Zéro exige une valeur zéro effectivement reçue. Un coût absent, invalide ou une réponse partielle donne `UNKNOWN` : réserve conservée et inconnue toujours présente dans `inspect_budget`. En préparation et correction, ce seul coût financier inconnu sur une opération reçue ne bloque plus un nouvel échange autorisé. Les états `EMISSION_POSSIBLE` et `AMBIGUOUS`, une admission fermée, une restauration non rapprochée et une enveloppe insuffisante restent bloquants. Les appels candidats et de jugement conservent leur contrôle strict. Aucune estimation ne remplace le coût observé dans le budget. Les nombres décimaux de la réponse sont conservés comme texte décimal dans l’observation pour préserver leur précision ; les octets bruts restent disponibles. Ce débit rapporté n’est pas une facture finale.

L’adaptateur OpenRouter ignore seulement les clés racine supplémentaires de valeur JSON `null` dans le résultat interprété. Les cinq champs requis restent obligatoires et leurs valeurs ne sont pas modifiées ; toute clé supplémentaire non nulle ou erreur de schéma du paquet ou des pièces reste refusée. Cette adaptation ne valide ni le besoin ni la référence et ne modifie pas la réponse brute ou les reçus historiques.

Le reçu conserve réponse brute en base64 et SHA-256, identifiant de génération, statut HTTP, dates, durée, usage et résultat interprété. Seuls les en-têtes de réponse `X-Generation-Id` et `Retry-After`, de forme bornée et sans secret reflété, sont retenus. Le premier conserve aussi l’identifiant d’une erreur sans objet de génération ; aucun autre en-tête n’est capturé et aucun délai de retry local n’est déclenché. L’en-tête `X-OpenRouter-Metadata: enabled` demande les [métadonnées de route](https://openrouter.ai/docs/guides/features/router-metadata). La route effectivement rapportée, y compris les tentatives de secours, est conservée séparément. Un secours autorisé n’invalide pas une réponse complète. Un modèle divergent, un tag hors liste ou un fournisseur identifié dans le relevé mais hors autorisation rend la sortie inutilisable, sans effacer son coût connu. Le fournisseur observé vient de l’endpoint marqué `selected` avec un nom prouvé par le relevé ; un libellé sans correspondance reste non attribué, sans alias inventé. Les métadonnées brutes conservent ce libellé. Une métadonnée absente reste inconnue sans être déduite du modèle demandé. Paramètres effectivement appliqués, effort interne et révision non rapportés restent inconnus. Une clé reflétée est retirée du reçu ; la réponse brute n’est jamais rendue par le web.

Les pièces candidates restent distinctes de la référence réservée au jugement. Le contrôle de structure ne qualifie pas la vérité du contenu. Les [tests HTTP simulés](../tests/test_openrouter_preparation.py) couvrent le scénario fictif, les corrections et accords, le coût inconnu, le refus avant réservation, l’absence de clé, la route rapportée et l’absence de rejeu. Ils ne prouvent ni accès du compte, ni qualité de GLM, ni fonctionnement réel sous Linux.

## Rapprocher un coût de préparation inconnu

Le rapprochement est une action opérateur privée, hors réseau, limitée aux opérations OpenRouter de préparation ou correction en `RECEIVED` avec coût initial `UNKNOWN`. Il ajoute une preuve immuable dans SQLite. Le reçu, `observed_cost_json`, l’action, l’état reçu, la réserve originale et l’enveloppe existante restent inchangés. Le calcul du budget utilise séparément le coût rapproché ; il ne crée aucun budget et conserve un montant prouvé même supérieur à la réserve ou à l’enveloppe. Une preuve absente ne libère rien. Ce rapprochement reste facultatif pour poursuivre la préparation après un reçu ; il ne constitue pas un prérequis financier au prochain échange autorisé.

Après déploiement du code compatible, sous l’identité opérateur et avec les chemins privés vérifiés, fermer l’admission, vérifier la quiescence et le stockage, puis sauvegarder avant l’extension explicite :

```sh
python3 -B -m benchmark_lab_x.runtime maintenance --data /chemin/prive/benchmark
python3 -B -m benchmark_lab_x.runtime quiescence --data /chemin/prive/benchmark
python3 -B -m benchmark_lab_x.runtime verify --data /chemin/prive/benchmark
python3 -B -m benchmark_lab_x.runtime backup --data /chemin/prive/benchmark --destination /chemin/prive/sauvegarde-neuve
python3 -B -m benchmark_lab_x.runtime initialize-reconciliation --data /chemin/prive/benchmark
python3 -B -m benchmark_lab_x.runtime verify --data /chemin/prive/benchmark
```

L’ouverture normale ne migre rien. La table additive `cost_reconciliations` reste distincte du layout opérationnel S1–S5 et de `schema_version=1`. `verify` rapporte `cost_reconciliation_format=benchmark-lab-x/cost-reconciliation/v1` lorsque cette structure exacte est présente. Le nouveau code lit aussi les bases non étendues ; les anciens lecteurs refusent la table supplémentaire. Après initialisation, un retour à l’ancien code exige une base préextension cohérente, restaurée selon la procédure existante ; une sauvegarde ancienne ne prouve pas l’absence d’appels ultérieurs.

La preuve JSON privée contient exactement `format_identity`, `operation_id`, `budget_id`, `receipt_sha256`, `actor`, `authority_id`, `account_reference`, `generation_id`, `model`, `cost`, `source` et `correlation`. `receipt_sha256` porte sur le reçu JSON canonique S1. `cost` contient `status=KNOWN`, `amount` en texte décimal fini non négatif, `currency=USD` identique à l’enveloppe et `source`. `source` contient `kind`, `http_status`, `name`, `observed_at` avec fuseau, `document` UTF-8 exact, son `sha256` et un `excerpt` exact non vide. Le JSON entier est borné à 2 Mio.

Deux formes de source sont reconnues :

- `openrouter_generation`, `http_status=200` : réponse native [GET génération](https://openrouter.ai/docs/api/api-reference/generations/get-generation), avec `data.id`, `data.model` et `data.total_cost` contrôlés. Le modèle doit correspondre à l’identifiant demandé ou au `canonical_slug` du relevé figé. Le compte et l’accès ayant fourni la réponse sont attestés par l’opérateur ; aucune clé n’entre dans la preuve. Une réponse 404 ou 429, ou un montant absent, ne prouve jamais zéro.
- `operator_attested_openrouter_record`, `http_status=null` : relevé ou export de facturation OpenRouter, dont l’opérateur atteste le montant et la corrélation au compte, à la génération et à l’opération. Les octets, leur empreinte et l’extrait sont contrôlés ; cette vérification structurelle ne certifie pas l’exactitude financière de l’attestation. Un message d’erreur sans preuve de montant ne suffit pas.

La génération connue dans le reçu ou son en-tête doit correspondre exactement. Si l’ancien reçu ne l’a pas conservée, `correlation` décrit les preuves utilisées par l’opérateur pour établir le lien. Les [tests fictifs de rapprochement](../tests/test_cost_reconciliation.py) donnent des exemples de format, sans valeur de preuve de facturation réelle.

```sh
python3 -B -m benchmark_lab_x.runtime reconcile-cost --data /chemin/prive/benchmark --authority /chemin/prive/preuve-cout.json
python3 -B -m benchmark_lab_x.runtime inspect-cost --data /chemin/prive/benchmark --authority /chemin/prive/identite-operation.json
```

Le fichier d’inspection contient seulement `{"operation_id":"identifiant-exact"}`. L’inspection privée distingue `observed_cost`, `effective_cost`, la preuve de rapprochement et le budget : `spent` est le sous-total connu ; une liste `unknown_cost_operations` non vide conserve l’incertitude sur le total. La même preuve de rapprochement est idempotente ; une preuve différente sur la même opération est refusée. L’admission doit rester fermée pendant ces écritures.

La vue propriétaire S2 conserve le coût initial et présente séparément l’estimation indicative éventuelle ainsi que le coût rapproché et sa provenance datée, sans document brut, compte ni budget partagé. Le dossier reste suspendu après rapprochement. Après une suspension ayant fermé l’admission, une nouvelle admission autorisée puis un nouveau message avec une nouvelle identité d’action peuvent poursuivre la préparation, avec ou sans rapprochement financier ; l’action d’origine ne se rejoue jamais. Les sauvegardes conservent la preuve SQLite et la restauration garde son blocage d’admission jusqu’au rapprochement opérationnel requis. Les évaluations et reçus historiques restent figés.

## Tarifs indicatifs OpenRouter pour l’opérateur

La commande ponctuelle ci-dessous consulte l’[API Models](https://openrouter.ai/docs/guides/overview/models) et les [endpoints du modèle](https://openrouter.ai/docs/api/api-reference/endpoints/list-all-endpoints-for-a-model), sans clé ni appel d’inférence. Elle sert à examiner une prévision avant de fixer une enveloppe ; elle ne lit ni ne modifie le stockage du produit.

```sh
python3 -B -m benchmark_lab_x.runtime forecast-prices \
  --model z-ai/glm-5.3-flash --input-tokens 1000000 --output-tokens 8192 \
  --cached-input-tokens 0
```

Les quantités décrivent une requête hypothétique. L’entrée comprend le cache ; `--cached-input-tokens`, zéro par défaut, indique la part supposée déjà en cache. La sortie comprend tous les tokens supposés générés, raisonnement inclus. Ces nombres sont des hypothèses opérateur, sans conversion de caractères en tokens ni preuve de faisabilité de l’appel.

Le JSON conserve l’identité exacte du modèle, son slug canonique déclaré, les URL consultées, la date UTC de chaque lecture et le SHA-256 de chaque réponse. Chaque endpoint garde son fournisseur, son tag, sa quantification, son statut déclaré et ses tarifs bruts. Les prix normalisés ont une unité explicite en USD par token, requête ou autre unité documentée. Le prix résumé du modèle reste identifié comme celui du fournisseur principal non nommé dans ce résumé ; il n’est jamais appliqué aux autres endpoints. Un statut déclaré ne prouve pas l’accès du compte.

Les sous-totaux entrée hors cache, lecture du cache et sortie sont calculés séparément avec les tarifs de chaque endpoint. Une composante absente reste `null` sans masquer les autres ; seule une quantité de tokens explicitement nulle donne une composante nulle sans tarif. La somme des trois composantes reste inconnue si l’une des composantes nécessaires manque. Les conditions tarifaires supplémentaires non résolues sont conservées et empêchent d’appliquer automatiquement le tarif de base. Le calcul porte explicitement `API_VALUES_BEFORE_DISCOUNT_APPLICATION` : il multiplie les quantités par les prix retournés avant toute application du champ `discount`. Le [schéma officiel OpenRouter](https://github.com/OpenRouterTeam/terraform-provider-openrouter/blob/main/docs/data-sources/model.md#nested-schema-for-datapricing) décrit la formule `price * (1 - discount)`. La remise reste non résolue lorsqu’elle est absente ou non nulle ; sa valeur, sa formule et sa source sont exposées, sans annoncer de prix net applicable.

Le résultat est un sous-total indicatif de tokens. `total_usd` reste inconnu : frais par requête, supplément de raisonnement, écriture de cache, outils, médias, taxes et financement ne sont pas couverts par ce calcul. Les tarifs manquants ne deviennent jamais zéro. Ces tarifs décrivent le canal OpenRouter. L’option de préparation décrite ci-dessus permet d’en tirer une réserve de tokens avant admission ; le coût consommé provient toujours du reçu et jamais de cette prévision.

La commande effectue deux lectures HTTPS bornées à 20 secondes par opération réseau et 2 Mio par réponse, sans redirection ni retry. Une identité divergente, une réponse invalide ou un échec réseau donne le refus opérateur existant `78 / HOLD`. Aucun catalogue, synchronisation, sélection de route ou garde d’admission n’est ajouté. Les [tests de consultation et calcul](../tests/test_openrouter_prices.py) utilisent des réponses HTTP simulées ; les lectures publiques ponctuelles restent distinctes des essais d’inférence.

## Qualification et approbation locales S3

Le candidat local [qualification.py](qualification.py) ajoute des contrats versionnés, leurs qualifications et une approbation opérateur explicite sur les mêmes octets. Il applique le [contrat de preuve](../docs/RULES.md#4-contrat-avant-exécution) aux pièces privées conservées par S1/S2. Aucun contrôleur métier universel, chargeur de plugin ou transport assistant n’est fourni. Le code produit n’importe aucun test ni rapport de préparation.

L’appel interne `qualification.initialize(data)` ajoute explicitement l’extension `benchmark-lab-x/qualification/v1` à une base S2 reconnue, y compris peuplée dans les fixtures locales. Il conserve ses lignes, ses pièces et `user_version=1`. Il refuse une autre base ou une extension partielle ; une extension exacte déjà présente reste inchangée. Ouvrir le stockage n’effectue aucune migration. L’usage sur une base existante hors fixtures n’est pas autorisé par cette construction locale. Les anciens lecteurs S2 refusent les objets S3 supplémentaires.

| Interface locale | Effet |
|---|---|
| `draft(store, dossier_id, revision, specification)` | Nouvelle version, paquet candidat exact et empreintes des références réservées ; retourne `contract` et `contract_sha256` |
| `qualify(store, contract_sha256, reviewer=…, check=…)` | Lit les octets, appelle une seule fois le contrôleur injecté par l’appelant de confiance et conserve sa preuve attribuée |
| `approve(store, contract_sha256, qualification_id, actor=…, authority=…)` | Lie explicitement la version courante à sa dernière qualification complète et à l’autorité locale |
| `inspect_contract(store, contract_sha256)` | Relit le contrat, ses `qualifications` et son `approval`, y compris historiques, sans les modifier |

La spécification structurée contient `result_expected`, `obligations`, `eliminatory_errors`, `reference_piece_ids`, `method`, `witnesses`, `secondary_criteria`, `aggregation`, `cost_basis`, `exposure`, `professional_review` et `limits`. Les obligations justifient leur usage et leurs tolérances ; obligations et erreurs relient leurs identifiants de contrôle à une méthode identifiée et versionnée, sa preuve attendue et son responsable. Les témoins sont déclarés avant le contrôle. Une référence doit appartenir à la même révision et être réservée au jugement. Les champs supplémentaires, dont un classement implicite des obligations, sont refusés.

Zéro à deux critères secondaires sont acceptés, chacun avec `id`, `measure`, `proof`, `unit`, `favorable` (`lower`, `higher` ou `yes`) et `aggregation`. Une agrégation absente vaut `null` ; aucune agrégation n’est calculée par S3. Une déclaration présente explicite `scope`, `cases`, `attempts`, `denominator`, `missing`, `incidents`, `indeterminate` et `rule`. La base de coût déclare `scope`, `attempts`, `unit` et `conversion`, celle-ci étant absente ou attribuée par `source`, `date` et `formula`. Aucune valeur de mesure ou de coût n’est acquise par ces déclarations.

Le contrôleur reçoit une copie du contrat et un dictionnaire des identifiants de pièces vers leurs octets relus. Il retourne `checks`, `limits`, `professional_review`, `assistance` et `disagreements`. Chaque contrôle porte `control_id`, `status`, `finding` et un objet `proof` non vide. Tous les contrôles déclarés doivent être présents exactement une fois et prouvés `PASS`. Un contrôle manquant, supplémentaire, répété, `FAIL`, `INDETERMINE` ou sans preuve produit `BLOCKED` ; une structure invalide est refusée sans écriture. Un désaccord sans arbitrage et preuve bloque aussi la qualification. La revue professionnelle est `ABSENTE` ou attribuée par `author`, `phase`, `scope` et `proof`. L’assistance reste `null` dans cette interface locale sans transport ni enveloppe de jugement.

La couverture des contrôles et l’intégrité des octets sont vérifiées par le mécanisme ; la justesse métier dépend du contrôleur de confiance et de ses preuves. La qualification conserve au moins les limites du contrat et la même déclaration de revue professionnelle. Un contrôleur qui affirme à tort un succès ne devient pas fiable par sa signature, sa configuration ou un consensus. Les tests locaux utilisent des entrées inventées ; ils ne qualifient ni une profession ni un assistant réel.

Contrat, preuves et approbation sont stockés séparément dans SQLite. Le SHA-256 du contrat porte sur le JSON UTF-8 strict canonique, sans saut de ligne, sans sa propre empreinte ni validation, qualification ou approbation. Les preuves possèdent aussi leurs empreintes. Les écritures sont transactionnelles ; les mises à jour, suppressions et remplacements d’une identité S3 existante sont refusés. La version courante est dérivée de l’historique conservé. Un nouveau contrat impose une nouvelle qualification et approbation ; un nouveau paquet exige en plus une nouvelle validation S2. Une correction en attente bloque l’éligibilité courante. Après approbation, une nouvelle qualification exige une nouvelle version. Les anciennes versions et preuves restent lisibles ; sauvegarde, vérification et restauration les contrôlent ensemble. Le marqueur de restauration interdit une nouvelle approbation, même répétée à l’identique.

Ayo est l’approbateur local désigné par le GO S3. Les seuls acteurs fictifs des tests sont `responsable-fictif-S3` et son autorité `TEST_ONLY_APPROVAL_S3` ; ces reçus fictifs n’accordent aucun droit réel. La frontière de confiance est l’accès opérateur autorisé au stockage privé et au fichier de décision. Une chaîne `actor`, un rôle, une session ou une saisie publique n’authentifie personne. Aucune route HTTP d’approbation n’existe.

Les commandes suivantes consomment un fichier privé, sans appel fournisseur. Elles retournent du JSON et le code 0 en cas de réussite ; une entrée ou opération non vérifiée retourne `HOLD` et le code 78 :

```sh
python3 -B -m benchmark_lab_x.runtime inspect-qualification --data /chemin/prive/benchmark --authority /chemin/prive/inspection.json
python3 -B -m benchmark_lab_x.runtime approve-qualification --data /chemin/prive/benchmark --authority /chemin/prive/decision.json
```

Pour inspecter, le fichier contient `contract_sha256`. Pour approuver, il contient exactement `contract_sha256`, `qualification_id`, `actor` et `authority = {authority_id, actor}`. L’acteur doit correspondre à l’autorité explicite ; les autorités fictives ne sont pas utilisables au nom de l’approbateur réel. L’inspection accepte aussi ce fichier complet. Les résultats de cette inspection sont privés. Le responsable examine les alternatives et limites dans `contract.specification`, les contrôles dans `qualifications` et les octets de chaque référence via `store.read_piece(piece['id'])` pour les entrées de `contract.reference_pieces`.

La vue du demandeur présente séparément validation, qualification et approbation. Sur une base S3, `qualification` expose `status`, `contract_sha256`, `qualification_status` et `approval_status`. `qualified` ne remplace pas l’approbation : celle-ci est indiquée par `APPROVED`. Attente, blocage et nouvelle validation requise restent distincts ; aucune référence, sortie de contrôle ou limite privée n’est projetée. Les formulaires, les pièces autorisées et les liens de retour S2 sont réutilisés. Leur HTML ne prouve pas à lui seul le parcours clavier, le focus visible, le petit écran ou le texte agrandi : une observation sur ce candidat reste requise, distincte des preuves historiques S2 et des tests HTTP.

Les régressions complémentaires sont dans [test_s3_regressions.py](../tests/test_s3_regressions.py). La découverte CI les inclut ; l’acceptation privée S3 et la suite demo restent séparées. Une évaluation empêchée par le sandbox de l’écrivain n’est pas un succès : les tests de sockets doivent être exécutés par le juge local Graph autorisé. Les preuves macOS restent distinctes d’une validation Linux. Cette construction ne réalise aucune intégration Git, migration opérationnelle, campagne ou publication.

## Campagnes privées locales S4

Le module [campaigns.py](campaigns.py) conserve plusieurs manifestes et leurs cellules sur les contrats approuvés S3. Il fournit l’admission, la réservation, une acquisition par callback et le suivi du dossier S2. Les témoins historiques utilisent un callback fictif ; le raccordement Pi/OpenRouter ci-dessous réutilise cette même frontière. Les paramètres du prototype historique restent propres à celui-ci. La capacité locale ne produit aucun verdict de contenu, classement ou publication.

L’initialisation est explicite sur une base S3 reconnue et intègre, même peuplée. Elle ajoute l’identité `benchmark-lab-x/campaigns/v1`, ses tables et contraintes, avec admission fermée pour chaque nouvelle campagne. `user_version=1`, les pièces et les lignes S1–S3 sont conservés. Répéter cette initialisation, celle de S3 ou celle de S2 ne réécrit pas une extension S4 exacte. L’ouverture et l’inspection ne migrent rien ; les anciens lecteurs refusent l’extension non reconnue. L’usage opérationnel de données existantes garde son autorité distincte.

```sh
python3 -B -m benchmark_lab_x.runtime initialize-campaigns --data /chemin/prive/benchmark
python3 -B -m benchmark_lab_x.runtime create-campaign --data /chemin/prive/benchmark --authority /chemin/prive/manifeste.json
python3 -B -m benchmark_lab_x.runtime inspect-campaign --data /chemin/prive/benchmark --authority /chemin/prive/inspection.json
```

Les fichiers opérateur sont des fichiers réguliers privés, sans lien symbolique ni accès groupe/autres, comme pour S3. Les entrées sont du JSON strict ; champs supplémentaires et clés répétées sont refusés. Le runtime retourne du JSON privé, code 0 à réussite ou `HOLD` et code 78 en cas de refus. L’inspection contient les reçus et sorties brutes : sa sortie doit rester privée.

| Commande | Contenu exact du fichier `--authority` |
|---|---|
| `create-campaign` | `{manifest: objet}` |
| `inspect-campaign` | `{campaign_id: identifiant}` |
| `admit-campaign` | `{campaign_id, authority, evidence}`, avec `authority.purpose = "start"` |
| `stop-campaign` | `{campaign_id, reason}` |
| `resume-campaign` | `{campaign_id, authority, evidence}`, avec `authority.purpose = "resume"` |

Ces formes décrivent les clés ; les fichiers utilisent la syntaxe JSON avec clés et textes entre guillemets. Chaque commande d’admission ou de reprise prend les mêmes options `--data` et `--authority`. Aucune de ces commandes n’émet d’appel. L’identité de campagne est unique ; modifier un manifeste exige une autre identité et conserve les campagnes précédentes.

Le manifeste contient exactement `campaign_id`, `version` (entier positif), `contract_sha256`, `cases`, `panel`, `conditions`, `plan`, `attempt_policy` et `cost_basis`. Son SHA-256 porte sur le JSON UTF-8 canonique S3 : clés triées, sans espaces de séparation ni saut de ligne. Les autorités, états, réserves et observations restent hors de cette empreinte.

- Chaque cas contient `id` et `package_sha256`, égal à celui du contrat S3. Cette tranche utilise le paquet exact de ce contrat pour les cas déclarés ; elle ne construit pas de nouvelles entrées de tâche.
- Chaque configuration contient `id`, `provider`, `model`, `revision`, `access` (`direct` ou `API`), `channel_id`, `route`, `parameters` (objet), `effort` et `required_observations`. Cette dernière liste inclut au moins `revision` et `channel_id`. Une révision mobile non prouvée est refusée.
- `conditions` contient `pi`, `packages`, `tools`, `skills`, `context_sha256`, `defaults`, `environment` et `frozen_at` (date avec fuseau). `pi` contient `package`, `version`, `sha256`, `status` (`declared`, `configured`, `active` ou `observed`) et `proof`. Ces déclarations ne prouvent pas une exécution réelle.
- Chaque cellule de `plan` contient `cell_id`, `case_id` et `configuration_id`. `attempt_policy` contient `retries: false`, `order` (chaque cellule une seule fois) et `reason`. L’ordre est contrôlé avant émission. Aucune cellule reçue ou ambiguë ne peut être rejouée sous une seconde identité.
- `cost_basis` est exactement la base S3 approuvée ; aucun critère n’est copié ou changé par l’acquisition.

L’objet `authority` contient `actor`, `authority_id`, `purpose`, `manifest_sha256`, `execution_authority`, `candidate_authority`, `budget_authority`, `budget_id`, `allowed_cells` et `reserve_amounts`. Ayo reste l’opérateur local désigné ; ce champ ne l’authentifie pas. La frontière de confiance est l’accès opérateur privé autorisé, jamais une saisie HTTP. Les autorités `TEST_ONLY` et les identités `fictional-*` des tests ne donnent aucun droit réel.

Le budget doit déjà exister dans S1 via `Store.create_budget(budget_id, limit, currency)`, sous autorité propre ; montants et réserves sont des textes décimaux non négatifs. Sa devise doit égaler l’unité contractuelle. Avant la première admission, le suivi montre l’enveloppe portant l’identifiant de campagne si elle existe, sinon `INCONNU` ; cette convention d’affichage n’accorde aucune autorité de budget. L’admission lie explicitement `budget_id`, les cellules autorisées et leurs réserves. Les montants prévus des cellules sans intention doivent tenir dans le solde disponible. Une reprise conserve l’enveloppe et les réserves des intentions existantes.

L’objet `evidence` contient `pi_sha256`, `context_sha256`, `channels` et `confinement`. Chaque canal, indexé par l’identifiant de configuration, contient `available: true`, `revision`, `channel_id`, `route`, `proof` et les autres champs d’observation exigés, identiques à la demande. `confinement` contient `code_execution: false` et une preuve textuelle non vide. Cette tranche refuse les outils, paquets et skills non vides ainsi que toute exécution de code candidat : elle ne dispose pas du vérificateur de confinement nécessaire. Une déclaration opérateur ne qualifie pas un adaptateur réel.

Le lanceur local de confiance utilise les interfaces Python suivantes, avec son propre transport fictif installé dans le code du lanceur :

| Interface | Effet |
|---|---|
| `create(store, manifest)` | Manifeste conservé et `manifest_sha256`, sans appel |
| `inspect(store, campaign_id)` / `list_campaigns(store)` | État privé, cellules, tentatives, reçus, budgets et autorités conservées |
| `admit(store, campaign_id, authority, evidence)` | Nouvelle preuve d’admission distincte du manifeste ; aucune réservation implicite |
| `reserve(store, campaign_id, cell_id, attempt_id)` | Identité d’exécution, intention S1 et réserve atomiques ; `operation_id = attempt_id` |
| `execute(data, attempt_id, transport=None)` | Connexion propre, recontrôles et callback unique ; sans callback, refus avant émission |
| `stop(store, campaign_id, reason=...)` | Admission fermée durablement ; intentions, réserves et reçus conservés |

Le callback reçoit des copies de l’opération S1 persistée et de la requête : campagne, empreintes du manifeste et du contrat, cellule et cas, configuration demandée, conditions communes, paquet S3 et pièces candidates `{id, sha256, content}` relues en UTF-8. Il ne reçoit ni Store ni références réservées. Aucun champ du manifeste, fichier opérateur, chemin utilisateur, variable d’environnement ou route HTTP ne sélectionne un transport. Les empreintes des sources moteur sont conservées à la réservation et recontrôlées avant émission.

La réponse contient `receipt` et `cost` au format S1. Le reçu contient `receipt_id`, `observed_configuration`, `resources_seen` et `result = {output, incident, emission}`. `output` est un texte UTF-8 exact ou `null`, `incident` un motif ou `null`, `emission` vaut `ESTABLISHED`, `UNKNOWN` ou `INCONNU`. Les observations exigées portent leurs sources dans `observed_configuration.sources`. Une valeur absente reste `INCONNU` dans le suivi, avec source absente visible ; la demande n’est jamais utilisée pour compléter l’observation. Le coût contient `status`, `amount`, `currency`, `source` ; `UNKNOWN` exige un montant `null`.

Avant le callback, l’admission utilisée et la transition S1 `EMISSION_POSSIBLE` sont visibles depuis une autre connexion. Le reçu, son coût et la pièce de sortie sont ensuite reliés dans la même transaction. La sortie est conservée exactement, même erronée, avec le rôle privé `judge` de S1 pour préserver le paquet candidat S2. Ce rôle de stockage n’est pas un verdict. L’inspection vérifie aussi les empreintes des reçus, les jointures et les octets de sortie. Une interruption d’écriture peut laisser une pièce orpheline détectée par la vérification S1 ; elle n’est pas effacée automatiquement.

Un coût sourcé supérieur à la prévision reste acquis. Un coût inconnu garde la réserve ; un effet ambigu, une émission non établie ou une observation exigée divergente bloque les appels dépendants, y compris sur une enveloppe partagée. Le reçu original reste conservé. Une réponse inexploitable ou une exception sans reçu vérifiable laisse la tentative ambiguë, sans coût inventé ni retry. Les journaux n’exposent pas le texte d’exception privé.

Le suivi est ajouté à la page propriétaire S2, avec son lien « Actualiser cet état ». Il présente chaque campagne, la version de tâche, les configurations demandées, les conditions communes, les autorités à fournir ou renouveler, les prévisions, réserves et coûts connus. Une cellule `NOT_STARTED` n’a aucune tentative ; `INTENT_RECORDED`, `EMISSION_POSSIBLE`, `AMBIGUOUS` et `RECEIVED` décrivent la technique. Les sorties brutes et références de jugement restent réservées à l’inspection opérateur. Les sources d’observation absentes et le solde non établi sont signalés. Une session étrangère et les actions HTTP de lancement, admission, arrêt ou reprise sont refusées.

Maintenance, démarrage et arrêt de l’exécuteur ferment aussi les admissions S4. Un worker indépendant garde son état actif et peut rendre son reçu après cet arrêt. `status` conserve les champs `admission`, `restore_pending` et `operations` du protocole de santé S1–S3. Son booléen `admission` tient compte des admissions S2 et S4. `quiescence` et `backup` refusent une admission ouverte, une émission possible ou un worker S4 encore actif. Le worker tient un verrou partagé sur le répertoire de données pendant toute son acquisition ; le contrôle d’arrêt et la sauvegarde exigent le verrou exclusif. La sauvegarde le conserve pendant la copie, en plus du verrou SQLite. Aucun fichier de verrou ni état de processus n’est recopié dans la sauvegarde.

L’arrêt du service ne prouve pas l’arrêt des workers S4. Le rapprochement `runtime.stop(..., after_process_exit=True)` ne marque leurs émissions sans reçu ambiguës que si aucun worker ne détient encore le verrou. Après une mort forcée, le noyau libère le verrou ; le rapprochement explicite conserve alors l’ambiguïté et la réserve, et permet une sauvegarde sans autoriser le rejeu. Tant qu’un autre worker S4 reste actif dans la même base, ce rapprochement attend aussi son arrêt. Les autres opérations gardent le contrat d’arrêt de leur service. Une simple ouverture ne change pas les états. La reprise explicite nomme les cellules jamais émises et refait les contrôles ; aucune file n’est drainée au démarrage. Ne pas mélanger des workers de versions différentes sur une base active.

Sauvegarde et restauration couvrent SQLite, toutes les pièces et leurs liens S4. Le marqueur `restore.json` bloque durablement l’admission, y compris pour une nouvelle campagne, car une sauvegarde ancienne ne prouve pas l’absence d’appels ultérieurs. Cette tranche ne fournit aucune commande de levée de ce blocage sans rapprochement des preuves.

Les [régressions S4](../tests/test_s4_regressions.py) utilisent uniquement des données fictives et les interfaces S1–S3. Elles vérifient notamment l’intention concurrente unique, l’immutabilité des preuves, la conservation des dépenses de préparation, le contrôle d’ordre, la reprise et l’isolation du suivi. Les contrôles automatiques ne qualifient aucun modèle ni contenu métier. La revue propriétaire du candidat reste nécessaire pour les libellés, la retrouvabilité des campagnes, le clavier/focus, le petit écran et le texte agrandi ; aucune observation de navigateur S4 n’est revendiquée. Les preuves macOS restent distinctes de Linux, et la suite demo du prototype reste séparée de la découverte CI.

### Validation du candidat local du 7 septembre 2026

État de la première remise, avant E1 : `HOLD`. Les contrôles ci-dessous ont été exécutés sur macOS 27.0 arm64 dans le sandbox de l’écrivain, par les commandes du juge épinglé. Ils ne constituent pas une évaluation native Graph hors sandbox ni une preuve Linux. Le manifeste de préparation SHA-256 `38c9689c38d70910e70f6fa226b53c44ed423234ee33c266af43055a21915dcd` et ses 45 fichiers ont été revérifiés inchangés. À cette première remise, le fusible natif comptait une entrée `implementation`, zéro entrée `correction` ; aucun registre parallèle n’a été créé.

| Commande exécutée | Résultat observé |
|---|---|
| `python3 -B -m unittest tests.test_s4_regressions` | 15 tests, succès |
| `python3 -B reports/s4-preparation/judge.py witnesses` | 4 tests, succès |
| `python3 -B reports/s4-preparation/judge.py s4` | 14 tests, un échec : coût de préparation attendu `2`, reçu conservé `3` |
| `python3 -B reports/s4-preparation/judge.py s3` | 16 tests, une erreur : ouverture TCP locale refusée par le sandbox |
| `python3 -B reports/s4-preparation/judge.py s2` | 13 tests, une erreur : ouverture TCP locale refusée par le sandbox |
| `python3 -B reports/s4-preparation/judge.py storage` | 23 tests, succès |
| `python3 -B reports/s4-preparation/judge.py services` | 2 tests, une erreur : chemin temporaire de socket Unix trop long |
| `python3 -B reports/s4-preparation/judge.py ci` | 894 tests, deux erreurs : ouverture TCP refusée et chemin de socket Unix trop long |
| `python3 -B reports/s4-preparation/judge.py demo` | 69 tests, succès, suite historique séparée |

Le mode `ci` exécute bien `uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests`, avec les variables offline du juge et vérification des dépendances avant/après. Syntaxe Python, liens locaux du README, absence d’import produit de tests/rapports et `git diff --check` ont aussi été vérifiés.

Le défaut du critère S4 est reproductible avant toute initialisation S4 : `seeded(data)` du juge scellé appelle la fixture S3, qui reçoit `amount = "3"` de `response_for` dans `tests/test_s2_review_regressions.py`. L’inspection S1 donne alors `spent = "3"`, `reserved = "0"`, `available = "97"` sur l’enveloppe `fictional`. L’assertion de `reports/s4-preparation/acceptance.py:242` attend pourtant `"2"`. S4 conserve ce reçu et ce coût ; les diminuer pour satisfaire l’assertion contredirait la conservation des preuves S1–S3. Aucun test, juge ni critère scellé n’a été modifié ou ignoré. Ce test interrompu ne prouve pas les sous-cas placés après son assertion en échec.

La coordination doit résoudre cette contradiction sous autorité et faire exécuter les contrôles réseau dans le contexte du juge Graph qualifié avant de pouvoir établir tous les critères. Aucune correction produit ne peut fabriquer le montant attendu. La revue propriétaire du code et du parcours demeure distincte, sans score qualitatif automatique. Aucun appel réel, opération Git de livraison ou action externe n’a été effectué.

### Correction unique après E1

État du candidat après la correction autorisée : `HOLD_EVALUATOR_FAILURE`. Le retour natif E1 porte sur le contrat Graph SHA-256 `49ec773fd55f8ca5f269175bbcd534819a1d40c81101f24f51f904dc935f7d14` et le candidat `836983652919d28932415116fd132827018644f6b7855a0868dd938cbe375729`. Le fusible natif lu pendant cette passe compte une implémentation et une correction. L’écrivain ne modifie ni ce registre ni les critères scellés et n’engage aucune autre boucle.

E1 a révélé un défaut produit que les refus de sockets du sandbox écrivain empêchaient d’observer : `runtime.status()` ajoutait `campaign_admissions`, alors que `service.executor_health()` exige exactement les cinq champs de sa réponse de santé. Le lecteur rejetait la réponse, puis `/readyz` retournait 503. La correction retire ce champ supplémentaire de `status` et conserve la prise en compte des admissions S4 dans le booléen existant `admission`. Le service et ses tests existants restent inchangés.

La régression `test_health_consumer_accepts_s4_status_before_during_and_after_admission` transmet le JSON du producteur réel au lecteur produit inchangé, avec seulement les entrées/sorties de socket simulées. Avant correction, ses trois états reproduisaient `ValueError: Réponse de santé invalide` à `service.py:44`. Après correction, elle passe et vérifie les états d’admission fermé, ouvert puis arrêté. Cette preuve du format échangé reste distincte des tests avec processus et sockets réels.

Fichiers touchés pendant cette seule correction : [runtime.py](runtime.py), [test_s4_regressions.py](../tests/test_s4_regressions.py) et ce README. Résultats des mêmes commandes épinglées après correction :

| Contrôle | Résultat dans le sandbox écrivain |
|---|---|
| Régressions S4 | 16 tests, succès |
| `witnesses` | 4 tests, succès |
| `s4` | 14 tests, un échec : `2 != 3` à l’assertion scellée de coût |
| `s3` | 16 tests, une erreur : ouverture TCP locale refusée |
| `s2` | 13 tests, une erreur : ouverture TCP locale refusée |
| `storage` | 23 tests, succès |
| `services` | 2 tests, une erreur : chemin de socket Unix trop long |
| `ci` | 895 tests, deux erreurs : ouverture TCP refusée et chemin de socket Unix trop long |
| `demo` | 69 tests, succès, suite historique séparée |

La contradiction de coût décrite ci-dessus subsiste après l’unique correction : le juge attend `2 TEST` pour un reçu de préparation sourcé à `3 TEST`. Corriger le protocole de santé ne change pas cette dépense. Modifier le reçu, son calcul ou le juge pour obtenir un succès contournerait le contrat scellé. Ce critère restant impose l’arrêt ; aucun `READY_FOR_OWNER_REVIEW_LOCAL` ni succès natif E2 n’est revendiqué. L’exécution native du candidat corrigé, les observations Linux et la revue propriétaire du code/parcours restent des preuves distinctes. Aucun appel réel ou acte de livraison n’a été effectué.

### Correction locale des constats de revue

Sous `GO_CORRIGER_S4_CONSTATS_DE_REVUE_SANS_APPEL`, la durée de vie des workers S4 est vérifiée par verrou système, indépendamment de celle du service. Les appels existants de `service.py` passent par le contrôle commun corrigé ; leur code reste inchangé. L’identité moteur inclut désormais `runtime.py`, qui porte ce contrôle. Les commentaires signalés respectent la règle locale de ponctuation.

Les deux nouvelles régressions lancent un vrai service local et un worker séparé à transport fictif. Elles vérifient l’arrêt puis le redémarrage du service pendant le callback, le refus de quiescence et de sauvegarde pendant l’activité, puis la réception tardive ou la mort forcée suivie du rapprochement. Elles reproduisaient le défaut avant correction et passent après correction ; les 18 régressions S4 passent également. Les preuves et juges antérieurs restent conservés. Le terminal Graph historique n’est ni repris ni réécrit. Le nouveau candidat attend sa revue, sans intégration ni appel modèle.

## Évaluations privées fictives S5

[evaluation.py](evaluation.py) évalue une tentative S4 identifiée sous son contrat S3 exact. Cette frontière locale reçoit des constats d’un contrôleur de confiance injecté par l’opérateur ; elle calcule le verdict, conserve les preuves et les rend consultables dans la session propriétaire S2. Aucun transport, chargeur de contrôleur, endpoint de jugement ou appel modèle n’est fourni. Le responsable réel des verdicts reste à désigner ; l’approbation locale S3 par Ayo ne l’attribue pas.

| Interface Python | Effet |
|---|---|
| `evaluation.initialize(data)` | Extension explicite d’une base S4 reconnue et intègre |
| `evaluation.evaluate(store, campaign_id, attempt_id, *, responsible, authority, check, previous_evaluation_id=None)` | Contrôle fictif local, verdict et conservation atomique |
| `evaluation.inspect(store, evaluation_id)` | Lecture vérifiée du résultat conservé, sans rejouer le contrôleur |

L’autorité des preuves fictives historiques est `authority = {"actor": "responsable-fictif-S5", "authority_id": "TEST_ONLY_EVALUATION_S5"}`, avec ce même `responsible`, dans des données fictives isolées. Ces chaînes ne constituent pas une authentification ni une autorisation réelle. L’accès opérateur privé reste la frontière de confiance. Aucun formulaire ne peut fournir un callback.

Le callback `check(context, resources)` reçoit des copies des inspections S4 et S3 sous `{campaign, qualification, attempt}` et les octets des seules entrées candidates, références et sortie de cette tentative. Une intention sans reçu est évaluable comme observation insuffisante ; une cellule jamais lancée n’est pas une tentative. Le callback retourne exactement `findings`, `measures`, `judgment` et `limits`, sans verdict imposé. Les pièces et le contexte sont revérifiés avant conservation.

- Un constat porte `criterion_id`, `control_id`, `status` (`PASS`, `FAIL`, `INDETERMINE`), `attribution`, `finding` et `evidence`. Critère et contrôle doivent être déclarés ensemble au contrat. Chaque preuve contient `piece_id`, `sha256` et `passage`, vérifiés sur les octets du contexte. Une preuve booléenne, étrangère ou divergente est refusée. Un passage vide peut seulement témoigner d’une pièce exactement vide. Un contrôle prévu absent devient un constat explicite d’insuffisance.
- Une mesure porte `criterion_id`, `value`, `unit`, `evidence`. Sa définition contractuelle est jointe au reçu ; une unité divergente ou un critère ajouté après coup est refusé. Une mesure absente conserve une valeur `null` et un état `UNKNOWN`. Les valeurs sources restent conservées, y compris sur une sortie non admissible. Aucune agrégation n’est calculée ; sa déclaration préalable ou son absence reste consultable.
- Le jugement porte `mode` (`local`, `human`, `assisted`), `instructions`, `resources_seen`, `assistance_operation_id`, `model_links`, `disagreements` et `professional_review`. Les liens déclarés sont un objet explicite ou `INCONNU`. Chaque désaccord conserve `finding` et `arbitration`, nul si absent, sinon `{responsible, decision, proof}` ; l’arbitrage est attribué au responsable de cette évaluation. La revue professionnelle est `ABSENTE` ou `{author, phase, scope, proof}`. Ses pièces, comme celles de l’arbitrage, doivent appartenir aux ressources vues.

Sur une sortie intègre et attribuable, un `FAIL` candidat prouvé donne `NE SATISFAIT PAS`, même si un autre contrôle manque, si une autre référence est contestée ou si un autre jugement reste non arbitré. Un conflit `PASS`/`FAIL` sur le même contrôle ne prouve pas ce défaut ; un défaut établi sur un autre contrôle reste conservé. `SATISFAIT` exige tous les contrôles d’obligations et d’erreurs éliminatoires prouvés, sans désaccord non arbitré. Sinon le verdict est `INDETERMINE`. Une attribution requise manquante, une émission inconnue ou un `HARNESS_ERROR` empêche d’attribuer un défaut de contenu. Un autre incident reste distinct d’un éventuel défaut établi indépendamment dans la sortie. Une rupture d’intégrité provoque un refus, sans créer d’erreur candidate.

La justesse d’un constat dépend du contrôleur qualifié et de ses preuves. Le produit vérifie les liens, les passages, la couverture et la règle de verdict ; il n’interprète pas universellement les obligations écrites en langage naturel. Le contrôleur applique aussi les obligations économiques éventuellement prévues : il doit conserver l’insuffisance lorsque le coût requis est inconnu. Un coût inconnu n’annule pas la satisfaction des obligations non économiques. Les dépenses candidates proviennent du reçu S1 de la tentative, avec leur base et leur source ; elles ne comprennent pas implicitement la préparation ou le jugement.

L’assistance fictive réutilise une intention S1 de phase `judgment`, sous `TEST_ONLY_JUDGMENT_S5`, liée au même dossier et à sa révision, avec un budget en `TEST`. Sa première ressource est le JSON strict `{instructions, context_sha256, piece_ids}`, suivi des identifiants de pièces dans le même ordre que `resources`. L’empreinte du contexte est calculée avec `qualification.digest(context)`. Consignes, contexte et pièces sont vérifiés contre le jugement. L’opération conserve configuration demandée, reçu, configuration observée, ressources vues, autorité, moteur, réserve, coût et effets inconnus. Une opération candidate ou un jugement étranger est refusé. Un reçu absent ne permet pas une satisfaction assistée ; son éventuelle réception tardive ne réécrit pas l’évaluation précédente. Une correction reste explicite. Aucun appel ni retry n’est réalisé par S5.

Les configurations demandées et observées des assistants de préparation, du juge éventuel et de la tentative sont rapprochées séparément pour exposer les liens connus de modèle ou fournisseur, avec les identifiants d’opération et de reçu sources. Une valeur absente reste `INCONNU` ; des noms différents ne prouvent pas l’indépendance. Le temps humain et le coût local restent inconnus sans méthode ni mesure. Les témoins fictifs conservent leurs dépenses propres : notamment `3 TEST` pour la préparation S2, `2 TEST` pour l’acquisition et `4 TEST` pour le reçu de jugement utilisé par l’acceptation. Ces unités ne sont pas des dépenses réelles de modèles ou de Graph.

Chaque évaluation conserve les identités de campagne, manifeste, tentative, cas, configuration, contrat, qualification et méthode, la sortie brute et son empreinte, les observations sourcées, les constats, les mesures, le responsable et la provenance du moteur d’évaluation. Le contexte observé au moment du jugement est conservé séparément du résultat. Une réception, un arrêt, une restauration ou une nouvelle révision de dossier ne substitue pas les observations ultérieures à ce contexte.

La correction doit référencer la dernière évaluation de la même tentative dans `previous_evaluation_id`. Une première évaluation, puis ses successeurs, forment une chaîne conservée ; les mises à jour, suppressions et remplacements sont refusés en SQLite. Deux corrections concurrentes ne peuvent consommer le même prédécesseur. Une nouvelle méthode ou référence contractuelle exige une nouvelle version S3 ; elle ne remplace pas les octets d’une campagne existante.

Le format `benchmark-lab-x/evaluations/v1` ajoute explicitement `s5_control`, `s5_evaluations` et leurs contraintes au schéma de stockage 1. Les dispositions canary et S1–S4 restent reconnues sans réécriture. Un ancien lecteur sans reconnaissance S5 refuse cette structure ; aucun rollback de données ni migration implicite n’est fourni. La vérification du stockage contrôle aussi les évaluations, leurs empreintes, leurs sources et leur chaîne. L’évaluation conserve le verrou partagé de worker et une transaction SQLite pendant le callback ; la sauvegarde exige le verrou exclusif. Sauvegarde et restauration couvrent les nouvelles tables avec les pièces existantes, en gardant le blocage de reprise après restauration.

La page du dossier propriétaire présente les évaluations dans chaque tentative, leurs motifs, limites, coûts séparés, mesures, qualification exacte, jugement et liens de correction. Les liens GET `/preparation/dossiers/{dossier_id}/evaluations/{evaluation_id}/pieces/{piece_id}` vérifient la session et la chaîne d’appartenance avant de rendre les octets en texte inerte. Le rôle `judge` ne donne pas d’accès global : une pièce étrangère, même réservée dans le même dossier, reste refusée. La route historique des pièces candidates reste limitée au paquet candidat. Les données sont échappées dans le HTML ; le service conserve ses en-têtes de protection existants. La frontière S5 conserve les évaluations ; la comparaison et la projection S6 sont décrites ci-dessous.

Les deux commandes locales suivantes retournent du JSON et le code 0 après vérification, ou `HOLD` avec le code 78 si l’opération n’est pas vérifiée :

```sh
python3 -B -m benchmark_lab_x.runtime initialize-evaluations --data /chemin/prive/benchmark
python3 -B -m benchmark_lab_x.runtime inspect-evaluation --data /chemin/prive/benchmark --authority /chemin/prive/inspection.json
```

Le fichier d’inspection, ordinaire, privé et détenu par l’opérateur, contient exactement `{"evaluation_id": "identifiant-conserve"}`. L’inspection n’initialise rien et ne donne aucune autorité de jugement réel.

Les [régressions S5](../tests/test_s5_regressions.py) couvrent les garanties complémentaires de concurrence, d’intégrité, d’évolution du dossier, de réception tardive et de sauvegarde. Elles sont indépendantes des rapports et fixtures du juge scellé. La découverte CI et la suite `benchmark_lab_x.test_demo` restent des validations distinctes ; les preuves macOS ne valent pas preuve Linux. La revue du code et du parcours propriétaire reste nécessaire : comprendre le motif, retrouver qualification et correction, ouvrir la sortie et ses preuves, revenir au dossier, puis vérifier clavier, focus, petit écran et texte agrandi dans un navigateur identifié. Les contrôles binaires ne certifient ni la qualité métier ni ce parcours humain.

## Comparaison et restitution fictives locales S6

Le module [restitution.py](restitution.py) relie le besoin, la révision du dossier S2, la version d’épreuve S3, une campagne S4 et ses évaluations S5. Il utilise les tables existantes en lecture seule. La consultation n’évalue rien et n’appelle aucun modèle.

| Interface | Comportement |
|---|---|
| `GET /preparation/catalogue` | « Mes tâches », index privé de la session S2, avec révisions, versions et campagnes ; `visibility=private`, `catalogue_admission=false` ; liste vide pour une session sans dossier |
| `GET /preparation/dossiers/{dossier_id}/campaigns/{campaign_id}` | Comparaison HTML ou JSON selon `Accept`, limitée à une campagne du dossier possédé |
| `GET …/campaigns/{campaign_id}/attempts/{attempt_id}` | Dernière évaluation, historique de correction, méthode, qualification, jugement, coûts séparés et preuves S5 ; retour conservant les paramètres et une ancre de tentative |
| `GET …/campaigns/{campaign_id}/preview` | Aperçu privé non approuvé, pièces choisies par cases à cocher (`piece` répété), sans activation |
| `restitution.comparison(store, session_id, dossier_id, campaign_id, *, query=None)` | Même comparaison locale, sans effet sur le stockage |
| `restitution.preview(store, session_id, dossier_id, campaign_id, *, piece_ids)` | Aperçu en mémoire : `manifest` en octets, `files` (nom → octets), `projection_sha256` |
| `restitution.materialize(bundle, approval, destination)` | Vérification des octets et du reçu fictif, puis activation atomique dans un répertoire local existant |
| `GET /publications/{sha256}/{file}` | Lecture de la projection S6 vérifiée, indépendante du pointeur actif, du stockage privé et de l’exécuteur |

Les paramètres GET combinables sont `case`, `sort`, `direction`, `verdict`, `obligation` et `configuration`. `sort` accepte les clés `id` renvoyées dans `columns`. `cost` désigne le coût observé. Les critères secondaires gardent leur identifiant, sauf le critère nommé `cost` : sa clé de tri reçoit autant de préfixes `criterion:` que nécessaire pour éviter toute collision. `criterion_id` conserve son identifiant contractuel, sans modifier le contrat approuvé ; `direction` accepte `asc` et `desc`. `verdict` accepte `SATISFAIT`, `NE SATISFAIT PAS`, `A_REPRENDRE` et l’alias historique `INDETERMINE`, et `obligation` associe un identifiant d’obligation à `PASS`, `FAIL` ou `INDETERMINE`, par exemple `O1:FAIL`. Les identifiants de cas et configuration proviennent de la seule campagne choisie. Paramètre inconnu, répété, vide ou hors contrat : refus. L’interface propose des formulaires GET séparés qui conservent les autres sélections, et des liens pour enlever un filtre ou tous les filtres. L’absence de tri garde un ordre descriptif, sans préférence produit.

Un script fixe, autorisé par l’empreinte CSP `sha256-UYVwhfSrYOHss9ut/0sNyZev/f+WGn1ovpct7BS3gkA=` sur la seule comparaison HTML, conserve la ligne consultée dans l’historique du navigateur et rétablit son focus au retour. Les régressions vérifient cette empreinte exacte et son périmètre HTTP. Il ne lit ni pièce ni contenu candidat et n’effectue aucune requête. Les formulaires, le lien de retour explicite et le reste du parcours restent natifs.

Les rangs de compétition (`1, 2, 2, 4`) utilisent les valeurs sources exactes, séparément par cas et selon le sens favorable du contrat. L’ordre visible croissant ou décroissant ne modifie pas ces rangs. Aucun arrondi n’est appliqué aux valeurs affichées. Les booléens sont classables seulement sur une unité `bool`, `boolean` ou `booléen` ; les unités descriptives restent sans tri. Une mesure absente, non numérique sur une unité numérique, sans preuve ou sans attribution requise conserve `rank=null` et un motif. Le coût garde sa valeur, son unité et sa source, avec refus de comparaison si la base ou l’unité diffère ; aucune conversion n’est inventée. Une erreur candidate conserve son verdict et son motif avec son coût, même classé.

La population comprend une entrée par tentative évaluée, en utilisant sa dernière évaluation ; les corrections restent accessibles dans le détail. Les comptes `planned_cells`, `attempted_cells`, `evaluated_attempts` et `not_started` portent sur la campagne entière. Les filtres conservent les comptes, la population et les rangs, y compris lorsque zéro ligne reste visible. La complétude économique est `INCOMPLETE` si une cellule manque, une tentative reste sans évaluation ou un coût ne peut être classé ; cet état n’est pas un verdict. Aucun coût global par configuration, verdict multi-cas, score pondéré, classement d’obligations ou gagnant n’est calculé. Changer de campagne remplace les observations consultées, avec sa version, son contrat, ses conditions et sa base de coût.

L’aperçu exige une liste explicite de pièces liées aux évaluations retenues. Liste vide : page et styles seulement. Les passages privés ne sont pas copiés implicitement dans les fichiers de projection ; les pièces non sélectionnées sont signalées comme restreintes et invérifiables dans cette vue. Les seules pièces choisies sont copiées à l’identique en `.txt`, reliées par leur identité et leur SHA-256. Le contenu candidat est échappé dans la page privée et servi comme texte brut inerte, avec les espaces et retours à la ligne conservés. Les fichiers de projection ne contiennent aucun lien vers les routes privées, cookie ou objet d’opération complet. Les liens relatifs restent dans le répertoire de leur projection.

La vue d’aperçu est accessible depuis la comparaison sous la même session propriétaire. Les cases sont décochées au départ ; chaque actualisation reconstruit en mémoire le paquet de la campagne entière et affiche son empreinte. Le rendu réutilise la présentation de projection avec des liens privés vers les seules pièces choisies et un bandeau « NON APPROUVÉ ». Cet habillage privé est distinct des fichiers du paquet. Consulter ou modifier la sélection ne matérialise rien et ne change aucune projection déjà activée.

Le seul reçu accepté est exactement `{"actor":"approbateur-fictif-S6","authority_id":"TEST_ONLY_PUBLICATION_S6","projection_sha256":"<empreinte exacte du manifeste>","catalogue":false}`. Ce reçu est extérieur aux octets du manifeste qu’il approuve. Il constitue un témoin logiciel local, sans authentification ni autorité réelle de publication. Le manifeste `benchmark-lab-x/restitution-fictional/v1` identifie présentation, conclusion, tâche/version, campagne, contrat, évaluations, limites et fichiers. Toute modification d’octets exige une nouvelle empreinte et son approbation exacte.

La matérialisation vérifie l’ensemble avant de sélectionner `<sha256>/` dans `active.json`. Elle conserve les fichiers exacts, `publication.json` et `approval.json`, sans activer un paquet partiel. Le lecteur public vérifie le manifeste, le reçu et les fichiers à chaque lecture ; pièce inconnue, octets altérés ou lien symbolique ferme la lecture. Les répertoires ancêtres du chemin S6 doivent également être ordinaires ; fournir un chemin absolu sans lien symbolique, notamment le chemin résolu d’un répertoire temporaire sur macOS. Un paquet historique reste sous son format d’origine. Lorsque le pointeur actif désigne S6, les anciennes URLs simples redirigent vers l’URL contenant son empreinte, afin que les liens suivants gardent la même identité.

Consultation privée, aperçu sans activation et projection fictive approuvée sont distincts. Le service annonce cette dernière par `X-Benchmark-Publication: APPROVED_FICTIONAL_S6` et l’empreinte dans l’URL et `X-Benchmark-Projection-SHA256`. Les octets approuvés ne sont pas réécrits pour changer un libellé d’aperçu. Aucun endpoint d’approbation, commande d’exploitation réelle, compte, admission au catalogue public ou ouverture extérieure n’est ajouté. Droits publics, admission au catalogue, approbateur et pièces réellement publiables restent à décider.

Les [régressions S6](../tests/test_s6_regressions.py) utilisent les primitives et fixtures maintenues S2–S5, sans dépendre de `reports`. Elles couvrent calcul, routes locales, isolation, octets et activation. La découverte CI et `benchmark_lab_x.test_demo` restent des preuves distinctes ; les résultats acquis sur macOS ne prouvent pas le candidat sous Linux ni une campagne réelle. La validation HTTP native et la revue propriétaire avec Ordinateur du candidat exact restent nécessaires. Les assertions HTML ne prouvent ni lisibilité, ni clavier, ni focus, ni Retour du navigateur. Un refus du navigateur sur une pièce brute reste distinct de la conformité de sa réponse HTTP.

## Outillage des premières campagnes

Les outils sous `tools/` conservent leurs contrats historiques et ne sont pas les commandes décrites ci-dessus. Dans `tools/campagne_v1.py`, le rendu et sa vérification calculent encore les empreintes des canons du checkout courant sous des libellés historiques ; les tests rétablissent au contraire les contrats du commit `38e226a59020aad517cd0dbb16892ffb87d448ab`. Leur réussite ne valide pas une restitution historique régénérée contre les canons courants. Toute opération sur ces campagnes doit identifier ses sources d’origine avant exécution.


## Première comparaison privée : Pi et jugement opérateur

Le transport [pi_openrouter.py](pi_openrouter.py) raccorde le SDK installé `@earendil-works/pi-coding-agent` 0.85.1 à l’acquisition S4. Pi construit et termine un tour sans outils ; le processus Python effectue l’échange OpenRouter avec le mécanisme HTTP déjà utilisé pour la préparation. Le secret reste dans Python. Pi ne charge ni contexte du poste, ni extension, ni skill, ni historique ; ses reprises et sa compaction sont désactivées. Aucune référence réservée au jugement ne lui est transmise. Le lanceur historique conserve ses paramètres et son contrat.

Cette capacité concerne les tâches textuelles sans outils, comme le suivi de réunion. Elle ne fournit ni exécution de code candidat, ni recherche externe, ni juge universel. L’installation de Pi dans l’environnement Linux et son essai opérationnel restent distincts du code et des tests locaux. L’archive produit inclut le pont JavaScript, mais n’installe ni Node ni Pi.

### Identifier et exécuter

Les commandes suivantes utilisent la même interface opérateur privée que l’admission S4. Les chemins sont à remplacer par les installations approuvées ; aucune commande n’installe une dépendance.

```sh
python3 -B -m benchmark_lab_x.runtime inspect-pi --pi-package /chemin/pi-coding-agent --node /chemin/node
python3 -B -m benchmark_lab_x.runtime reserve-candidate --data /chemin/prive/benchmark --authority /chemin/prive/reservation.json
python3 -B -m benchmark_lab_x.runtime execute-candidate --data /chemin/prive/benchmark --authority /chemin/prive/tentative.json --pi-package /chemin/pi-coding-agent --node /chemin/node
python3 -B -m benchmark_lab_x.runtime inspect-model-profile --data /chemin/prive/benchmark --authority /chemin/prive/identite-transport.json
```

`identite-transport.json` contient exactement `provider`, `model`, `revision`, `access`, `channel_id` et `outgoing_format`. La commande ne cherche pas par le seul nom de modèle. Un profil retrouvé décrit une réponse de transport `COMPLETE`, pas un verdict de tâche.

`inspect-pi` n’appelle aucun modèle. Il donne `package`, `version`, `sha256` à reprendre dans `conditions.pi`, et `bridge_sha256`, `node_version`, `node_sha256` à reprendre dans `conditions.environment`. L’empreinte Pi couvre les modules installés coding-agent, agent-core et pi-ai ainsi que le verrou de dépendances du paquet ; elle ne vérifie pas les octets installés de toutes les dépendances transitives. Ces dépendances restent à installer depuis le verrou et à qualifier dans l’environnement cible.

`conditions.defaults` contient exactement `system_prompt`, `timeout_seconds` et `context_window`, décidés pour la comparaison. `conditions.context_sha256` est le SHA-256 UTF-8 de `pi_openrouter.system_context(system_prompt)` : il inclut le répertoire constant `/` ajouté par Pi. `timeout_seconds` borne chaque attente du pont et chaque opération réseau ; ce n’est pas un plafond absolu de durée de campagne. Les listes `tools`, `packages` et `skills` restent vides.

Chaque configuration utilise `access: "API"`, `channel_id: "https://openrouter.ai/api/v1/chat/completions"` et le même identifiant exact dans `model` et `revision`. Ici, la révision observée désigne le slug renvoyé par OpenRouter ; elle ne prouve pas une révision cachée des poids. Aucun alias différent n’est assimilé à cette identité. Les paramètres acceptés sont `max_tokens`, `provider`, et éventuellement `temperature`, `top_p`, `reasoning: {"effort": "…"}` et `stream: false`. Le champ `effort` doit correspondre au paramètre émis, ou à `off` sans raisonnement demandé.

Le bloc `provider` contient `only`, `order` (la même liste ordonnée), `allow_fallbacks` et `require_parameters: true`. Le manifeste décide les fournisseurs permis et l’autorisation de secours ; le transport ne les choisit pas. OpenRouter peut essayer les routes autorisées dans un même échange, selon sa [règle native de routage](https://openrouter.ai/docs/guides/routing/provider-selection). Aucun retry Python ou Pi n’est ajouté. Les [métadonnées de routage](https://openrouter.ai/docs/guides/features/router-metadata) sont conservées ; une route ou un effort non observé reste inconnu. Une transformation rapportée par le pipeline entraîne `HARNESS_ERROR` et conserve les octets reçus.

`reservation.json` contient exactement `campaign_id`, `cell_id`, `attempt_id`. `tentative.json` contient `campaign_id` et `attempt_id`. Le contrat S3 doit déjà être qualifié et approuvé, le manifeste créé, le budget USD enregistré et la cellule admise sous les autorités d’exécution, de candidat et de budget. La préparation vérifie les empreintes avant la frontière d’émission. La commande d’exécution reçoit `OPENROUTER_API_KEY` dans son environnement privé ; elle ne reçoit pas la clé en argument et ne la transmet pas à Pi.

Une tentative reçue n’est pas rejouée. Un problème après réception conserve le corps HTTP privé, le coût rapporté et l’incident, même si Pi n’a pas terminé correctement. L’absence de coût financier garde `UNKNOWN` et sa réservation selon les règles S4 existantes ; le budget de préparation n’est pas réutilisé. La sortie reste brute et les paramètres demandés ne deviennent pas des paramètres observés. Le code de sortie vaut 78 si la tentative n’est pas reçue ; un code 0 ne prouve ni sortie exploitable ni satisfaction du contrat, qui se lisent dans le reçu et le verdict.

### Évaluer et consulter

```sh
python3 -B -m benchmark_lab_x.runtime prepare-evaluation --data /chemin/prive/benchmark --authority /chemin/prive/tentative.json
python3 -B -m benchmark_lab_x.runtime evaluate-attempt --data /chemin/prive/benchmark --authority /chemin/prive/constats.json
```

La première commande exporte les entrées privées, le contrat, la sortie et un rapport à compléter. Ses constats commencent à `INDETERMINE` ; aucun succès ni lecture humaine ne sont inférés. Le format du rapport reste celui de S5 décrit plus haut. Les constats doivent appliquer la méthode qualifiée du contrat et citer les pièces effectivement examinées ; un contrôleur de témoins tabulaires ne devient pas un juge de toutes les réponses libres.

Le fichier `constats.json` contient exactement `campaign_id`, `attempt_id`, `responsible`, `authority`, `report` et `previous_evaluation_id`. `authority` contient `actor: "Ayo"` et l’identifiant de l’autorisation effective dans `authority_id`, sans identité `TEST_ONLY`. `responsible` nomme l’auteur ou le responsable réel des constats ; une revue déléguée ne se présente pas comme une lecture personnelle d’Ayo. `previous_evaluation_id` vaut `null` pour la première évaluation et référence la dernière pour une correction. Les modes `local` et `human` sont ouverts ; le jugement assisté utilise le raccordement privé décrit ci-dessous, sous sa propre autorité et son budget.

L’accès au fichier et au stockage privés reste la frontière de confiance : les champs `actor` et `authority_id` ne sont pas une authentification. Le moteur vérifie les critères du contrat, les identités, empreintes et passages de preuve, puis calcule le verdict. Il ne certifie pas la vérité métier du constat. L’évaluation et ses corrections sont immuables, consultables dans le parcours propriétaire S6 et comprises dans la sauvegarde/restauration. Les reçus S5 fictifs restent inchangés ; un ancien lecteur S5 qui ne reconnaît pas les nouveaux reçus opérateur les refuse, sans réécriture ni migration de données.

Les [tests de comparaison privée](../tests/test_private_comparison.py) réutilisent les fixtures S3–S6. Avec Pi 0.85.1 et Node installés, ils exécutent le vrai harnais en remplaçant uniquement l’échange HTTP par des réponses fictives. Sans Pi, ces tests de transport sont signalés comme ignorés ; `BENCHMARK_TEST_PI_PACKAGE` permet de sélectionner explicitement le paquet installé. Aucun résultat de ces tests ne vaut acquisition candidate réelle, recette Linux, déploiement ou publication.


## Relecture assistée privée OpenRouter

[judgment.py](judgment.py) conserve une proposition dans le reçu d’une opération S1 de phase `judgment`, identifiée par `benchmark-lab-x/judgment/v1`. Aucune nouvelle table ni migration implicite n’est nécessaire. Les reçus S5 historiques gardent leur validateur et leur sémantique. Un lecteur antérieur à ce raccordement ne sait pas valider une évaluation assistée S14.

[OpenRouterJudgment](openrouter_judgment.py) exige un profil explicite au format S13. Modèle, paramètres, système, routes, limites et relevé tarifaire sont liés avant émission. Le système du profil doit demander un objet JSON contenant exactement `findings`, `measures`, `limits`, `proposed_verdict`. Le serveur construit la provenance ; une réponse modèle ne fournit ni autorité ni arbitrage propriétaire. Le verdict proposé est conservé comme proposition, même s’il diffère du verdict calculé ensuite.

La projection est celle de `evaluation.prepare_review()` et `outgoing.closed_review()` : tâche, critères, pièces du paquet, sortie exacte et références du contrat. L’export `prepare-evaluation` reste destiné à la revue locale et ne constitue jamais le corps HTTP. L’intention conserve séparément le contexte privé, l’autorité, l’admission de campagne, la réserve et les octets sortants. Le reçu conserve réponse, empreintes, incident et coût disponible. L’inspection vérifie ces liens sans appel ni rejeu.

Les commandes privées se lancent ainsi, avec une clé injectée dans `OPENROUTER_API_KEY` pour les deux premières seulement :

```sh
python -m benchmark_lab_x.runtime reserve-judgment --data /chemin/prive --authority /chemin/demande.json --judgment-profile /chemin/profil.json
python -m benchmark_lab_x.runtime execute-judgment --data /chemin/prive --authority /chemin/operation.json --judgment-profile /chemin/profil.json
python -m benchmark_lab_x.runtime inspect-judgment --data /chemin/prive --authority /chemin/operation.json
```

`demande.json` contient exactement `operation_id`, `campaign_id`, `attempt_id`, `review_sha256`, `previous_evaluation_id`, `authority`, `budget_id`, `reserve_amount`, `requested_configuration`. `review_sha256` et le prédécesseur viennent de `prepare-review`. `authority` contient l’acteur opérateur `Ayo` et son `authority_id` effectif. La configuration vient de `openrouter_preparation.configuration(estimate, profile)` et la réserve de `reservation(estimate, profile)`, à partir d’un relevé déjà obtenu sous autorité distincte. Le budget USD doit déjà exister. `operation.json` contient seulement `{"operation_id":"identifiant-reserve"}`. Les fichiers opérateur doivent être privés. La réservation ne fait aucun HTTP ; l’exécution revalide le même profil, la projection, l’admission et l’enveloppe.

Une campagne arrêtée, une restauration à rapprocher, une dérive ou un verrou de sauvegarde bloque l’admission. Une émission ne peut partir qu’une fois. Interruption et timeout conservent une ambiguïté ; une réception tardive peut compléter ce reçu sans réécrire une évaluation. Un coût inconnu garde la réserve et bloque les appels dépendant de l’enveloppe ou de la même campagne, même avec un autre budget : l’exception de préparation ne s’applique pas au jugement.

La sortie d’inspection contient `operation`, `binding`, `proposal` (nul si inexploitable) et un `diagnostic` dérivé de la réponse conservée. L’opérateur examine `proposal.report`, complète ou corrige les constats et soumet séparément le format `constats.json` décrit plus haut avec `evaluate-attempt`. Il conserve le mode `assisted` et l’opération effectivement utilisée. Critères, pièces, empreintes, passages, provenance et dernier prédécesseur sont contrôlés avant calcul local du verdict. Une seconde correction concurrente est refusée.

Les pièces citées prouvent leurs octets, pas la justesse métier d’un constat. Les critères sur les données opérationnelles exclues exigent un contrôle local. Une garde conservatrice repère les mentions de coût, budget, transport, latence ou durée dans les critères et conserve leurs constats assistés à `INDETERMINE` ; une soumission assistée ne peut pas transformer ces constats en succès en citant seulement la sortie. Cette garde ne constitue pas une analyse sémantique générale des exigences. La référence qualifiée et la relecture opérateur restent nécessaires pour identifier les autres preuves absentes.

Les [tests propres S14](../tests/test_s14_judgment.py) couvrent CLI, profil changé, concurrence, verrou de sauvegarde, maintenance, restauration et réception tardive. Les tests utilisent des réponses HTTP synthétiques ; ils ne prouvent aucun appel produit ni qualification d’un modèle. La CI Linux avec services et la suite historique native macOS restent des preuves distinctes à acquérir sur le candidat exact.


## Décisions conclusives et travaux à reprendre

Les nouvelles évaluations propriétaire utilisent le format de reçu `benchmark-lab-x/evaluations/v2`. Elles conservent le rapport complet, le contexte, les preuves et la chaîne `previous_evaluation_id`. Une évaluation non concluante porte `verdict: null`, un `state` et une `decision` contenant le motif et la prochaine action. Elle reste consultable et corrigible ; aucun constat ni arbitrage soumis n’est perdu. Une décision conclusive porte `SATISFAIT` ou `NE SATISFAIT PAS`. L’assistant ne finalise aucune décision.

La disposition SQL et `s5_control` restent en version 1. Le lecteur accepte explicitement les reçus v1 et v2 et refuse les formats inconnus. Les reçus historiques v1 sont reconstruits avec leur logique d’origine. **Après une première écriture v2, l’ancien binaire ne peut plus vérifier ce stockage.** Un retour arrière exige un lecteur compatible ou une restauration coordonnée autorisée, en conservant les écritures postérieures à la sauvegarde.

L’opérateur peut consulter la suite à donner sans clé ni appel modèle :

```sh
python -m benchmark_lab_x.runtime inspect-attempt-status --data /chemin/prive --authority /chemin/tentative.json
```

Le fichier privé contient exactement `campaign_id` et `attempt_id`. La réponse distingue décision, diagnostic candidat, dernière opération de juge et travail de relecture restant. `inspect-judgment` distingue notamment une citation de preuve invalide d’un incident de transport. Les passages restent vérifiés exactement ; aucune correction automatique de citation ne modifie le reçu. Une erreur du juge se résout sur la même sortie, avec une nouvelle évaluation liée à la précédente.

La comparaison privée expose `decision`, un verdict métier éventuellement nul, les `pending_attempts` et le nombre `decided_attempts`. Le détail historique garde la valeur d’origine. Le filtre `A_REPRENDRE` sélectionne les lignes sans verdict ; l’ancien filtre `INDETERMINE` reste un alias de lecture. Les coûts et populations de classement ne changent pas.

Les reprises utilisent toujours les capacités et autorités figées. Une réponse HTTP 429/502/503/504 complète peut omettre l’identité du modèle sans constituer une contradiction ; une identité explicitement divergente reste bloquante. La réserve prudente ne devient pas une consommation observée. Une route fautive non attribuable, des routes épuisées, une autorité absente ou des effets inconnus donnent un motif d’arrêt. Aucun nouvel appel candidat ne suit une réponse complète simplement pour chercher un meilleur verdict.

Le transport candidat officiel réutilise Pi et la projection fermée. La commande privée `execute-candidate` sélectionne explicitement `--candidate-provider anthropic|deepseek|zai` ; OpenRouter reste le défaut. Les canaux sont fixes : Messages Anthropic, Chat Completions DeepSeek et API générale Z.ai. Les clés ne rejoignent ni Pi ni le corps candidat. Le reçu conserve le corps natif, son empreinte, l’identité observée et les quantités natives. Sans reçu financier, le coût reste `UNKNOWN` et la réserve reste engagée.

Le manifeste de secours conserve `recovery_of` et ajoute `official_fallback: {"route_attempts": ["identifiant-operation"]}`. Ces opérations antérieures doivent prouver des incidents de route, sorties vides ou troncatures épuisées sur toutes les routes OpenRouter initialement autorisées, pour la même cellule, tâche et identité. Une réponse complète ou un refus ne permet pas ce secours. Une troncature n’est épuisée que si son plafond figé et autorisé est atteint, ou si augmenter la limite sur la même route n’a produit aucune progression. Sans cette preuve, les reprises OpenRouter restent prioritaires.

La configuration officielle conserve l’effort et les identifiants modèle/révision, à l’exception du préfixe fournisseur OpenRouter. Elle est admise séparément avec son budget et ses paramètres natifs : `output_config.effort` pour Anthropic ; `thinking.type=enabled` et `reasoning_effort` pour DeepSeek et Z.ai ; `max_tokens` et `stream=false` dans les trois cas. Un ancien binaire ne sait pas lire ce nouveau manifeste : appliquer les mêmes précautions de retour arrière que pour les reçus v2.

La découverte authentifiée du 12 septembre 2026 confirme `claude-opus-5` chez Anthropic et `deepseek-flash` chez DeepSeek. Le second est un alias : le validateur ne le substitue pas silencieusement à `deepseek-v4.1-flash`. L’API DeepSeek a retiré V4 Flash et redirige son ancien alias vers V4.1 ; aucun secours exact 0731 n’est établi. Le raccordement local testé avec des réponses HTTP simulées ne prouve ni un appel officiel réel ni son déploiement sur la VM.


## Clés API dans un fichier .env

Depuis la racine du projet, copier [.env.example](../.env.example) vers `.env`, puis renseigner les clés souhaitées. Le fichier `.env` est ignoré par Git ; seul l’exemple avec des valeurs vides est versionné. Lui donner les permissions `600` sur macOS ou Linux.

`OPENROUTER_API_KEY` concerne le canal principal. `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY` et `ZAI_API_KEY` sont facultatives et réservées aux secours officiels. Les renseigner ne déclenche aucun appel ; le secours exige sa sélection et une admission distinctes.

Le chargement local utilise uv, déjà présent dans la chaîne du projet, sans dépendance supplémentaire :

```sh
uv run --env-file .env python -m benchmark_lab_x.runtime <commande> <options>
```

Cette option charge les variables pour la commande opérateur concernée ; le runtime ne recherche pas automatiquement un fichier dans le dossier courant. Ne pas charger les clés dans le processus du serveur web public. Sur la VM, le fichier privé d’environnement de l’exécuteur remplit déjà cette fonction et reste hors des archives de déploiement. Aucun coffre de secrets supplémentaire n’est requis pour ce mode de configuration.

## Modèle DeepSeek courant

DeepSeek V4.1 Flash remplace 0731 dans le panel et les assistants à sélectionner. Le registre propose `deepseek-v4-1-flash`, identifiant OpenRouter `deepseek/deepseek-v4.1-flash`. L’ancien modèle et son alias redirigé sont refusés pour les nouvelles campagnes, réservations et émissions, y compris depuis un ancien profil ou une intention déjà enregistrée. Le parcours de préparation affiche ce retrait. Les lectures et preuves historiques gardent leurs identités ; aucun résultat 0731 n’est renommé en V4.1.
