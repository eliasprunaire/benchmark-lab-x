---
style_gate: pass
---

# ARD : contrat d'architecture de Bench-X

## 1. Portée

Cet ARD fixe les objets, frontières, flux et contraintes de l'architecture retenue. Il prescrit ce que l'implémentation doit garantir, sans attester sa construction ou son déploiement. Les versions et états livrés se vérifient dans le code et les reçus, hors de ce document.

Le [PRD](PRD.md) gouverne le besoin et le périmètre. Les [règles](RULES.md) portent les invariants. Le glossaire fixe le sens des termes. Le [gabarit de carte](task-template.md) prépare le contrat d'une tâche.

Le produit du monorepo partage une version ; les contrats de compatibilité et les versions de schéma suivent les [règles de versionnement](RULES.md#14-versionnement-du-produit).

## 2. Principes

L’architecture relie des contrats figés, des observations attribuables et des vues vérifiables. Les invariants de décision, de coût et d’histoire appartiennent aux [règles](RULES.md) ; le parcours et les exclusions appartiennent au [PRD](PRD.md#10-restitution-publique).

## 3. Pi comme frontière constante

Pi est le harnais commun des comparaisons. Sa constance réduit une source de variation entre configurations ; elle ne prouve pas que Pi est neutre ou que le modèle seul cause le résultat. Pi, ses paquets et l'environnement sont présentés une fois comme conditions de test communes, jamais comme propriétés répétées de chaque modèle.

Pi est la frontière des comparaisons candidates ; il ne choisit pas les modèles de préparation ou de jugement. OpenRouter est le canal normal des appels modèles du produit. Pour une acquisition en incident, après diagnostic et épuisement des routes utilisables du même modèle, une API officielle peut être admise comme dernier recours. L’autorité propriétaire peut figer ce secours dès l’admission initiale ; le moteur l’enchaîne alors sans nouvelle interaction, tout en créant une configuration, une admission, une réserve et un reçu distincts. Sans cette préautorisation, une nouvelle admission opérateur reste nécessaire. L’identité exacte disponible, la preuve de correspondance de version, les paramètres réellement pris en charge, l’accès produit, le budget, les octets émis et le reçu natif doivent être liés. Pi, la tâche et les projections fermées restent constants. Aucune substitution de modèle ou révision ni recours destiné à contourner un refus de sécurité n’est permis. Le secret reste réservé à l’exécuteur. Les reprises techniques ne prononcent aucun verdict et ne sélectionnent pas des réponses jusqu’au succès. Le secours natif OpenRouter de préparation et correction conserve ses endpoints explicitement autorisés et les mêmes paramètres. Cette règle produit ne s’applique pas aux outils de développement et ne réinterprète aucun contrat déjà scellé.

L’architecture est indépendante des modèles et de leurs versions commerciales. L’assistant de préparation est choisi au démarrage par un profil OpenRouter approuvé, désigné par un alias de compatibilité ou le chemin d’un fichier JSON local. Les identifiants disponibles et les alias pris en charge relèvent de la configuration et de la documentation opérateur. Le profil fige l’identifiant demandé, la révision unique de réponse autorisée, les paramètres envoyés, les routes, les capacités attendues, le message système et les limites de taille et de temps, sans dépasser les plafonds définis par cet adaptateur. Il est chargé une fois, recopié indépendamment du fichier ou de l’objet appelant, et son empreinte canonique est liée à la configuration demandée avec le relevé tarifaire et la réserve. Le chemin hôte du fichier n’entre pas dans la configuration, les messages ni les reçus. Une divergence de profil, d’empreinte, de modèle, de révision, de paramètres, de système, de route, de capacité, de relevé, de réserve ou de limite bloque avant l’émission HTTP. Aucun registre de plugins, fabrique générique ni base de profils n’est requis. Un relevé périodique des modèles disponibles auprès du canal, filtré par des règles tenues dans le dépôt, peut alimenter la sélection des configurations candidates ; il ne remplace ni l’admission ni la vérification d’hébergeur avant appel. Les profils des assistants restent OpenRouter ; le secours officiel candidat exige sa propre configuration. Fournir un fichier de profil ne sélectionne aucun nouvel assistant de production.

Les appels engagés pour un demandeur peuvent être imputés à un accès fournisseur que ce demandeur fournit lui-même, en saisissant une clé personnelle dans le parcours. C’est le seul mécanisme d’accès du demandeur : l’ancienne autorisation déléguée est retirée. L’architecture exige que la clé soit reçue, conservé et utilisé par l’exécuteur seul, lié à la session du demandeur, révocable par lui, jamais transmis au serveur public, aux pages, aux journaux ni aux reçus publics. Le plafond non renouvelable de la clé OpenRouter est la seule limite financière du parcours financé par le demandeur. Préparation, correction, qualification, vérification des modèles, comparaison et jugement utilisent cette même clé, sans plafond local quotidien, par navigateur ou par campagne. Les estimations, réservations et coûts restent enregistrés pour le suivi, sans constituer une seconde limite. Cette imputation ne modifie ni la séparation des autorités de validation, de qualification et de lancement, ni les exigences de reçu et de preuve.

L’aide sémantique de recherche constitue une exception bornée au canal OpenRouter et au financement personnel. Elle utilise directement TypeSafe selon la [section 4.9](#49-aide-sémantique-commune). Cette exception ne concerne ni préparation, ni qualification d’épreuve, ni candidat, ni jugement.

### 3.1 Identité de l'environnement d'exécution

Chaque campagne doit figer le paquet ou fork Pi, sa version exécutée, les empreintes nécessaires, les paquets, outils, skills, contexte, réglages, environnement et date de gel. Les paramètres demandés et observés restent distincts.

`déclaré` vient d'une source documentaire, `configuré` d'un réglage, `actif` d'une preuve de chargement et `observé` d'une exécution. Une valeur absente reste `INCONNU`. Un réglage du poste ou un numéro de changelog ne prouve pas la version exécutée.

La campagne doit conserver son environnement identifié indépendamment des mises à jour du poste et du site. Une nouvelle version de présentation ne doit ni réécrire les preuves ni imposer une nouvelle acquisition. Cette traçabilité permet de contrôler les conditions et de réappliquer les évaluations déterministes ; elle ne garantit pas une sortie candidate identique, notamment si le fournisseur change une révision non observable.

## 4. Objets et responsabilités

### 4.1 Tâche, version et cas d'essai

Responsabilité : relier le besoin au contrat de réussite et à sa base de coût, définis par les [règles du contrat](RULES.md#4-contrat-avant-exécution).

Avant approbation, le brouillon conserve la demande et ses précisions, la reformulation, les hypothèses validées et les paramètres fictifs du dossier, sous la politique de données approuvée. Il réutilise l’état `EN_ATTENTE` du gabarit. L’aperçu lie sa présentation à la consigne, aux livrables et aux pièces réellement construites du paquet candidat. La validation du besoin référence ce paquet ; elle n’approuve ni le contrat ni une dépense. Les preuves de validation et d’approbation restent hors de l’empreinte qu’elles référencent.

Les modifications conservent les accords non touchés et entraînent les vérifications concernées puis une nouvelle validation du paquet présenté. Qualification et approbation portent sur les octets finalement retenus. Si la référence ou les outils ne permettent pas de juger le travail, la préparation explique la limite et propose un périmètre soumis à accord ou s’arrête.

La tâche possède un identifiant stable ; chaque version relie un contrat approuvé aux cas d'essai et à leurs entrées identifiées. Le contrat approuvé est immuable ; une modification crée une nouvelle version, sans remplacer les références des campagnes existantes. Le catalogue référence ces tâches sans confondre leurs versions. Le métier ou domaine et la famille de tâche sont des repères descriptifs du contexte ; ils ne constituent ni des clés de comparabilité ni des intégrations techniques par profession.

Chaque version identifie aussi la référence de jugement et la méthode. Les preuves de qualification lui sont liées hors de son empreinte, selon les [règles de qualification](RULES.md#4-contrat-avant-exécution). Les pièces accessibles au candidat et celles réservées à l’évaluation sont identifiées séparément. Une tâche de recherche décrit son corpus, les outils et droits d’accès : textes fournis, bibliothèque figée ou source externe autorisée. Les requêtes, résultats et pièces effectivement consultés sont conservés lorsqu’une recherche intervient ; leur identité distingue les ressources annoncées des ressources réellement vues.

Une campagne référence une version de tâche, les cas retenus, le panel, les conditions communes et les autorisations. Plusieurs campagnes peuvent référencer une même version. Une éventuelle agrégation des cas exige une règle préalable ; les verdicts par cas restent accessibles.

### 4.2 Conditions de test communes

Responsabilité : déclarer une fois, avant le premier candidat, ce que toutes les configurations comparées partagent : état de Pi (paquet ou fork, version, paquets, outils, skills, contexte, réglages par défaut), environnement et date de gel. Chaque valeur porte son statut : déclarée, configurée, active ou observée.

Toute configuration comparée référence ces conditions. Une condition commune modifiée ouvre une nouvelle comparaison ; elle ne requalifie pas les sorties déjà obtenues.

### 4.3 Configuration demandée et configuration observée

La configuration demandée est la cible figée du manifeste : elle existe avant l’appel. Chaque tentative lui associe une configuration observée, avec la source de chaque observation. Plusieurs tentatives visant la même cible peuvent observer des valeurs différentes ; elles ne réécrivent pas la demande initiale.

Responsabilité : conserver tout ce qui borne l’attribution de la sortie :

- fournisseur
- modèle et révision exacte exigée par le contrat
- accès API via OpenRouter pour les appels courants ; accès d’origine conservé dans les preuves déjà enregistrées
- route demandée et route observée
- paramètres demandés et observés
- effort de raisonnement demandé et observé
- identité demandée et identité observée
- référence aux conditions de test communes

Une demande et une observation restent distinctes. Une identité, une route ou une valeur absente reste `INCONNU`. Une révision imposée ne peut pas être remplacée par un alias mobile non vérifié. Le panel fige la liste des configurations ; il ne prouve pas leur disponibilité.

Pour une configuration locale, l'identité inclut les poids, leur révision, la quantification, le serveur d'inférence et le matériel nécessaires à l'attribution.

### 4.4 Acquisition, tentative et exécution

Les transports construisent des projections sortantes fermées par rôle. Le candidat reçoit la consigne, les livrables, les critères publics, les ambiguïtés utiles et les pièces candidates vérifiées sous leurs noms logiques. Le préparateur reçoit la demande initiale, le message courant, la reformulation, les clarifications antérieures, les accords validés, les paramètres fictifs et le précédent contenu candidat utile ; le kind du workflow reste interne. La référence de jugement est reconstruite, sans reprendre un ancien verdict comme autorité. La génération sépare le paquet candidat, les notes internes et la référence de jugement ; le modèle n’assigne aucun rôle. La revue reçoit la tâche candidate exacte, le résultat attendu, les obligations, les erreurs éliminatoires, la méthode, les critères secondaires nécessaires, les limites de la spécification de jugement, la sortie identifiée et les références autorisées, chaque pièce de preuve portant identifiant, nom logique, empreinte et contenu. La spécification identifie explicitement les critères dont les preuves opérationnelles restent locales ; leur portée n’est jamais déduite de mots présents dans le cas métier. Les identifiants de campagne et de tentative restent dans une liaison locale. L’export opérateur reste local. Statuts, journal d’exécution, autorités, budgets et chemins hôte ne sont pas sérialisés vers ces rôles. Les notes internes et la référence de jugement restent distinctes de la sortie candidate dans la génération.

Une exécution identifie une opération du produit : préparation, acquisition, évaluation ou construction de restitution. Elle référence la version du moteur, ses entrées, son autorité, sa chronologie et sa terminaison. Une préparation référence le brouillon avant qu’une campagne existe ; les exécutions suivantes référencent leur campagne. Une campagne peut avoir plusieurs exécutions ; une exécution d’acquisition peut contenir plusieurs tentatives.

Les appels assistés de préparation, correction et jugement conservent leur intention avant émission, la configuration demandée et observée, les ressources vues, le reçu, les coûts et les effets inconnus. Ils sont attribués à leur opération, séparément des tentatives candidates, sans créer de résultat de benchmark. Leur budget et leur autorité précèdent leur consommation ; arrêt et reprise suivent les mêmes règles d’absence de rejeu implicite. L’autorité d’appel est la session munie de la clé du demandeur ; aucune clé ni enveloppe opérateur ne finance ces appels. L’opérateur ne l’ouvre ni ne la ferme, et ni un redémarrage, ni un déploiement, ni l’échec d’un autre visiteur ne la retire. Une préparation en cours, un effet ambigu ou un résultat inutilisable ne bloquent que la session ou le cas d’usage concernés. Un effet ambigu se clôt de lui-même quinze minutes après l’envoi, sans nouvel état d’opération : le stockage, sous écriture exclusive et idempotente, la passe à `RECEIVED` avec un reçu d’incident `AMBIGUOUS_EXPIRED` et un coût inconnu dont la réserve reste comptée ; l’exécuteur déclenche cette clôture au démarrage, à chaque envoi de session et par minuteur, et un appel dont l’émission possible a survécu à un plantage y passe après son arrêt au démarrage. Aucun appel n’est relancé et une base restaurée n’est pas close. Une intention bloquée avant émission est close sans coût, et la session peut renvoyer ; il en va de même d’un appel dont la connexion au fournisseur n’a pu s’ouvrir (résolution DNS, refus, TLS), car aucun octet de la requête n’est parti. Toute erreur survenue après le début de l’envoi garde des effets inconnus. Un coût reçu comme inconnu peut être rapproché par une preuve externe attribuable ajoutée sans écraser le reçu ; le coût effectif reste distinct de l’observation initiale. Ce rapprochement ne rejoue aucune action. La préparation et la correction distinguent ce coût financier de l’indication calculée après succès selon les [règles économiques](RULES.md#8-coût-et-bénéfices) ; le seul coût financier inconnu d’une opération reçue ne bloque pas un nouvel échange autorisé, sa réserve restant comptée.

La tentative est l’unité d’intention d’appel pour un cas et une configuration demandée. Son identifiant précède l’émission ; le reçu distingue intention enregistrée, émission établie ou inconnue et résultat reçu. Une tentative locale n’atteste pas que le fournisseur a reçu la requête. Une cellule prévue mais jamais lancée n’est pas une tentative.

L’acquisition conserve :

- campagne, version de tâche, cas, contrat et identifiant de tentative
- configuration demandée et observée
- requête
- sortie brute ou absence de sortie
- chronologie
- coût observé
- incident, retry et intervention
- reçu relié aux autres preuves

Une tentative ne décide pas de son propre verdict. L'exécuteur doit enregistrer l'intention avant l'appel puis conserver le reçu ou l'ambiguïté. Après interruption, une tentative aux effets inconnus ne doit pas être rejouée ; une cellule jamais lancée reste distincte d'un échec.

### 4.5 Évaluation

Responsabilité : appliquer les erreurs éliminatoires et obligations du contrat, puis produire un verdict conclusif :

- `SATISFAIT`
- `NE SATISFAIT PAS`

Une évaluation non concluante conserve son rapport et ses preuves comme travail à reprendre, sans verdict métier. Son état, ses causes et la prochaine action restent consultables. Les valeurs `INDETERMINE` déjà enregistrées restent inchangées dans leur reçu.

L'évaluation s'applique à un cas et une tentative identifiés. Le verdict porte les éléments exigés par la règle « Verdict explicable » des [règles](RULES.md#6-erreurs-et-verdict) : valeur, motif court, critères ou constats concernés, références de preuve et responsable. L’évaluation référence la version de la méthode et de la référence de jugement ainsi que les preuves de leur qualification. Lorsqu’elle est assistée par un modèle, elle conserve sa configuration demandée et observée, les consignes, les pièces vues, les constats et la règle de décision versionnée ; ces éléments se distinguent de la configuration candidate et suivent les [règles de provenance](RULES.md#5-sortie-et-provenance). Dans le parcours public, le lancement autorisé enchaîne automatiquement les appels candidats, le jugement et la restitution privée. Le juge fournit les identifiants de pièces et les passages exacts ; le serveur vérifie les citations et calcule leurs empreintes avant le verdict. Une omission désigne la sortie examinée sans passage et cite la pièce candidate qui porte l’élément ; seul un juge dont le profil figé annonce cette règle (`evidence_rule`) peut l’employer, et la liaison du jugement porte alors la version `server-evidence/v2` ; une réponse antérieure se relit comme avant, sans requalification. Une empreinte recopiée incorrectement est corrigée dans le rapport dérivé et signalée côté serveur, sans bloquer les résultats dont les citations sont vérifiables. Le reçu brut reste inchangé. Aucune soumission opérateur n’est exigée. Chaque évaluation déjà enregistrée garde son contrat scellé. L’éventuelle revue professionnelle est une preuve attribuée à son auteur et à son périmètre, sans nouveau rôle produit obligatoire. Une mesure valide d’un critère secondaire peut décrire une sortie non admissible ; elle ne change pas le verdict ni ne compense une erreur éliminatoire. Un défaut de consigne, de données, de référence, de contrôle ou d’exécution ne devient pas artificiellement une erreur candidate.

### 4.6 Vue de décision

Responsabilité : présenter les évaluations, les classements par critère, les filtres et la complétude de la comparaison des coûts, selon les [règles](RULES.md#7-ordre-de-décision). Chaque colonne ordonnable référence sa définition contractuelle et ses preuves ; les valeurs inconnues ou incompatibles sont sans rang, les égalités conservées. La vue expose le périmètre filtré sans modifier les sorties, reçus, verdicts ou populations des statistiques déjà calculées. Elle ne fusionne pas des couvertures incompatibles. Elle peut produire l’aide économique conditionnelle définie dans les règles, à partir des observations complètes avant filtrage, sans nouveau stockage ni appel modèle. Le score pondéré personnalisé reste différé selon le PRD ; aucun moteur ni formule n’est ajouté avant sa décision.

La priorité privée est un paramètre de consultation validé côté serveur : conseil par défaut, coût observé ou critère secondaire ordonnable du contrat. Une sélection invalide est refusée explicitement, sans substitution silencieuse. La restitution réutilise les mesures, les ordres contractuels et les contrôles existants, avant les filtres d’affichage. Le rendu conserve la priorité et permet son effacement, y compris sans JavaScript.

Aucun stockage de préférence, nouvel appel, nouvelle évaluation ni moteur de classement distinct n’est requis. La consultation des campagnes enregistrées conserve leurs octets et leurs preuves. Les projections publiques restent hors de cette capacité.

### 4.7 Restitution

La restitution relie les identités du catalogue, de la tâche et de la campagne au parcours du [PRD](PRD.md#10-restitution-publique). La publication est une projection approuvée et identifiée de cette restitution et de ses pièces autorisées, séparée des données privées et de l’état d’exécution.

La projection référence les versions de schéma, de présentation, de conclusion et de pièces utilisées. Sa mise à disposition doit être cohérente : une consultation ne mélange pas une conclusion nouvelle avec les pièces d’une publication précédente. Une nouvelle présentation ne relance aucune acquisition et ne modifie aucun verdict ; son exposition exige l’autorité de publication correspondante.

### 4.7.1 Publication d’une épreuve éditoriale

Une publication d’épreuve référence une identité de tâche, une version documentaire immuable, sa notice et les pièces publiques explicitement sélectionnées. Elle ne requiert aucune campagne. Son état de qualification reste distinct de l’approbation de publication ; une version documentaire non qualifiée ne vaut pas contrat approuvé pour exécution.

L’opération opérateur vérifie l’autorité applicable et les octets approuvés avant d’activer un paquet complet. Ce paquet conserve les pièces publiques indépendamment des dossiers temporaires. Le serveur public lit seulement ce paquet et son état de publication. Il n’accède ni aux références du juge ni aux données privées pour servir la notice.

Les primitives existantes de sélection, d’empreinte, de validation et d’activation atomique sont réutilisées. Les projections fictives historiques conservent leurs formats et leur autorité de test. Elles ne deviennent pas des publications éditoriales par changement de libellé. Aucun nouveau service ni interface d’administration complète n’est requis.

Le retrait conserve une trace liée à la version, sans modifier ses octets approuvés. L’accès public à la notice, aux pièces et l’admission d’une nouvelle reprise vérifient cet état. Une ancienne URL ne contourne pas le retrait. Une nouvelle version ne remplace pas silencieusement une version antérieure.

### 4.7.2 Copie privée d’une épreuve publiée

L’admission de copie vérifie la version publiée, son autorité, ses empreintes et son absence de retrait. Le dossier privé conserve une copie autonome de la notice et des pièces publiques exactes. Sa filiation lie l’identité de tâche, la version documentaire et l’empreinte du paquet source. Une URL seule ne suffit pas. Le transfert ne donne au serveur public aucun accès direct au stockage privé.

La copie réutilise les contrôles de session, de propriétaire, d’intégrité et de conservation du parcours privé. Elle ne résout aucun transport fournisseur et ne réserve aucun appel. Un paquet incomplet ne devient pas un dossier utilisable. La répétition de la même demande de copie ne crée pas de doublon. Le contrôle du retrait et la conservation de la source doivent rester cohérents lors de demandes concurrentes.

La source documentaire reste distincte du besoin personnel et des révisions préparées. Le préparateur reçoit les contenus utiles comme contexte, sans importer d’autorité de qualification ni de référence réservée au juge. Le contrat et la référence de la nouvelle instance suivent la préparation et la qualification existantes. Les preuves, résultats et coûts des anciennes campagnes gardent leurs identités.

Le signalement éditorial d’une erreur est attribué et lié à la version source. Il reste distinct des octets immuables de publication et des preuves privées. La consultation privée expose les signalements applicables à cette filiation, y compris après un retrait ordinaire. Aucun inventaire public des copies privées n’est créé. Le mécanisme ne requiert ni collecte automatique ni nouveau service.

### 4.7.3 Recherche dans les épreuves éditoriales

La recherche lit uniquement les paquets publics admis par le lecteur éditorial existant. Le serveur valide les paramètres, puis combine recherche textuelle sur les notices et filtres explicites. Les résultats et valeurs de filtres excluent les versions retirées ou invalides, les dossiers privés et les contributions. Aucun transport modèle, accès personnel ou nouveau service n’est nécessaire.

Les caractéristiques décrites dans le [PRD](PRD.md#trouver-une-épreuve-par-ses-caractéristiques) appartiennent à la version approuvée. L’évolution du format conserve la lecture des anciens paquets et leur identité. Elle ne réécrit pas leurs manifestes et ne complète pas leurs inconnues. L’ajout de caractéristiques à une publication existante suit la création et l’approbation d’une nouvelle version.

La projection de recherche conserve une entrée distincte par version disponible et ne transforme aucun ordre d’affichage en préférence. La notice, ses pièces et la copie utilisent leurs contrôles existants d’autorité, d’intégrité et de retrait. Le rendu serveur réutilise les protections et harnais web existants. La recherche ne modifie ni qualification, ni preuve, ni état de publication de résultats.

### 4.7.4 Publication des résultats d’une épreuve

L’opération opérateur construit une projection publique fermée à partir des restitutions existantes. Elle conserve les verdicts et observations sans nouvelle acquisition ni évaluation. Elle exclut la saisie initiale, les messages privés, les références réservées au juge, les secrets, les journaux bruts et les pièces étrangères. La projection privée ne devient pas publique par simple changement de visibilité.

Le paquet autonome relie la version éditoriale exacte, son empreinte, la campagne, son contrat, la restitution et les pièces sélectionnées. L’approbation d’Ayo vise ces identités et les octets exacts. L’admission vérifie leur cohérence et la disponibilité de l’épreuve avant activation complète. Les primitives existantes d’empreinte, de sélection et d’activation sont réutilisées ; les formats fictifs historiques gardent leur autorité de test.

Le serveur public lit seulement les paquets approuvés et leurs états de publication. La consultation ne dépend pas d’un accès au stockage privé. Elle conserve les conditions, dates, couverture, incidents, inconnues et restrictions nécessaires à la conclusion. Une autre campagne reste une restitution distincte ; les tris et filtres ne recalculent pas les verdicts ni les populations de référence.

La présence de résultats est une projection des publications accessibles liées à la version. Elle ne réécrit aucun ancien manifeste éditorial. Le lecteur vérifie l’intégrité et les retraits de la restitution et de l’épreuve, y compris pour les accès directs aux pièces. Le retrait d’une restitution ferme seulement celle-ci ; le retrait de l’épreuve ferme l’accès public à toutes ses restitutions. Les paquets et décisions restent identifiables sans exposer les contenus retirés.

### 4.8 Répertoire d’évaluations externes

Le serveur public sert un répertoire éditorial de notices, distinct des tâches, campagnes et dossiers privés. Chaque notice possède une identité stable, sa source officielle et les métadonnées définies par le [PRD](PRD.md#consulter-des-évaluations-externes). Le statut de disponibilité et la vérification éditoriale restent explicites.

La consultation, la recherche simple et les filtres s’exécutent sur les notices disponibles, sans transport modèle ni résolution de clé. Ils ne créent ni session requise, ni dossier, ni campagne. Le même contenu alimente l’accès direct et les renvois de préparation. Les identifiants et liens proviennent du répertoire ; les textes de recherche ne construisent aucune destination externe.

Le rendu serveur permet l’usage sans JavaScript. Les entrées et textes sont validés et échappés selon les frontières existantes. Les liens externes ne transmettent ni besoin privé ni référent contenant des données privées. Aucun accès aux pièces privées n’est nécessaire pour servir une notice.

Le retrait d’une notice produit un état indisponible explicite, y compris sur son lien direct. Une vérification éditoriale insuffisante reste visible. Le répertoire vide et la recherche sans résultat conservent une sortie vers la description d’un besoin. Cette capacité réutilise le serveur et les harnais existants ; elle ne requiert ni nouveau service, ni Jev, ni acquisition automatique de sources.

### 4.9 Aide sémantique commune

Le répertoire externe et le catalogue d’épreuves utilisent une seule intégration TypeSafe dans l’exécuteur existant. Chaque usage conserve ses questions versionnées et sa qualification distincte. Les lecteurs publics existants fournissent les seules notices admissibles. Aucun nouveau microservice, collecte de sources ou index privé n’est requis.

Le serveur relaie la demande volontaire sans recevoir la clé TypeSafe. L’exécuteur construit une projection fermée : formulation soumise et champs publics utiles. Les identifiants de correspondance proviennent du serveur ; aucune destination n’est construite depuis une sortie libre. Les textes soumis restent des données, jamais des instructions ou une autorité. Les questions indépendantes par notice permettent une absence de suggestion. Le modèle demandé et observé, les identifiants, la complétude et les probabilités finies sont contrôlés avant usage. Aucun modèle, alias mobile ou fournisseur de secours n’est substitué.

La configuration initiale fixe `jev-1.13.0`, sans alias mobile. Elle conserve un tarif daté, les limites et les questions propres à chaque usage. Les limites ordinaires retenues sont 64k jetons par requête et 32k par question, contexte compris. Leur application et la borne de réservation doivent être justifiées avant émission. Aucun contexte étendu, troncature silencieuse ou découpage multipliant les appels n’est permis. Un dépassement demande une formulation plus courte ou des filtres supplémentaires ; il ne sélectionne pas silencieusement un sous-ensemble.

L’enveloppe initiale commune est de 1 USD de consommation API, sans renouvellement automatique. Elle ne constitue pas une autorisation d’émission. Le payeur TypeSafe est distinct des clés personnelles. L’exécuteur réserve avant émission sous contrôle transactionnel commun aux deux usages. Un tarif ou une borne de coût non établi empêche l’envoi. Le coût calculé depuis les jetons et le tarif reste distinct d’une facture. Un coût inconnu conserve sa réserve ; il ne devient pas nul. La recharge automatique du compte doit rester désactivée ; le contrôle local ne garantit pas la facture fournisseur.

Une demande autorise au plus une émission, sans reprise automatique, y compris après 429, surcharge, interruption ou redémarrage. Les reprises implicites du SDK sont désactivées. Le délai maximal est de 10 secondes. Les deux usages partagent deux émissions simultanées au maximum et une nouvelle émission par seconde au maximum. Il n’existe aucune file d’attente durable. Une double soumission de la même demande ne produit pas deux émissions. Une nouvelle demande volontaire garde les mêmes contrôles de financement et de fréquence.

Une intention et sa réservation précèdent l’envoi. Les reçus attribuent l’opération à son usage, au modèle demandé et observé, aux questions, aux notices, aux jetons et au tarif. Ils conservent la chronologie, l’incident et le coût connu ou inconnu, sans texte recherché, secret, cookie ou historique privé. Le contenu recherché ne persiste pas dans les journaux, les URL ou les reçus durables. Une empreinte ne constitue pas une promesse d’anonymat. Une émission incertaine garde sa réserve et ne repart pas. Une réponse tardive ne réactive pas une demande terminée.

L’admissibilité des notices est revérifiée avant affichage, ouverture et copie. Les correspondances restent temporaires et n’altèrent aucun paquet publié. Une réponse invalide ou une limite atteinte ferme seulement l’aide concernée ; les recherches simples restent utilisables. Un interrupteur désactivé interdit toute émission et toute résolution de secret. L’activation de chaque usage exige son autorisation et sa qualification en français ; celle du répertoire ne qualifie pas celle des épreuves.

## 5. Flux minimal

```text
Demande, éventuellement vague
  -> Clarification et reformulation, assistance sous autorité et budget propres
  -> Dossier fictif construit, consigne, référence et contrôles
  -> Aperçu consultable, modifications et validation du besoin
  -> Qualification de la référence et de l’épreuve
  -> Présentation des critères, du panel, des conditions et des coûts de campagne
  -> Contrat de tâche qualifié et approuvé ; manifeste et autorités approuvés
  -> Admission et acquisition candidate sous Pi constant
  -> Sortie brute ou incident, conservé avec sa provenance
  -> Évaluation automatique : SATISFAIT / NE SATISFAIT PAS, ou résultat non concluant ; preuves conservées
  -> Mesures et coûts observés, classements par critère et filtres
  -> Choix de l’utilisateur ; publication uniquement sous autorité distincte
```

Cette représentation décrit les dépendances métier ; les composants doivent respecter les frontières de la section 12. Le contrat de tâche et le manifeste de campagne restent distincts. Modifier un critère à l’examen des conditions fait revenir à la qualification et aux validations affectées. Une limite de référence, d’outil, d’autorité ou de budget peut arrêter la préparation. Les résultats restent privés avant publication.

## 6. Identités, jointures et immutabilité

Chaque tâche, version, cas, campagne, contrat, conditions communes, configuration demandée, observation de configuration, exécution, tentative, sortie, verdict et publication possède une identité vérifiable. Les jointures relient explicitement :

- la tâche à ses versions, contrats et cas
- la campagne à sa version de tâche, ses cas, son panel et ses autorisations
- la configuration à ses conditions de test communes
- la tentative à l’exécution, au cas, à sa configuration demandée et à ses observations
- la sortie au reçu d'acquisition
- le verdict à la sortie, au contrat, à la référence et à la méthode utilisés, ainsi qu’à ses preuves
- la vue de décision aux seuls verdicts compatibles
- la publication à la version éditoriale ou à la vue de restitution et aux pièces explicitement approuvées

Un libellé public n'est pas une clé de jointure. La sortie brute et les preuves déjà enregistrées ne sont pas corrigées silencieusement.

La conclusion refuse de combiner comme comparables les éléments suivants ; les observations conservées et leur couverture restent consultables :

- contrats différents ou modifiés après résultat
- conditions de test communes différentes entre candidats présentés comme comparables
- identité incomplète au regard du contrat
- sortie modifiée après acquisition
- mesure de classement absente des critères prévus
- coût `INCONNU`, base de coût incompatible ou couverture insuffisante pour le classement proposé

Une mesure valide d’une configuration non admissible peut être classée sur son critère ; elle ne permet pas de la désigner comme utilisable. La vue conserve le verdict et son motif pendant les tris.

## 7. Verdict, coût et bénéfices

L’évaluation applique l’[ordre de décision](RULES.md#7-ordre-de-décision), puis la vue applique les [règles économiques](RULES.md#8-coût-et-bénéfices). La conclusion économique décrit le périmètre et la complétude de la comparaison des coûts, avec inconnues et incompatibilités, sans objet persistant de recommandation ni agrégation implicite. L’aide économique privée reste une lecture conditionnelle des observations conforme aux règles. Elle reste distincte du verdict : `INCOMPLETE` est un état économique distinct du verdict. Les versions de l’évaluateur, de sa méthode et du calcul de conclusion restent reliées aux preuves. Le coût ne compense aucune non-admissibilité, y compris dans la vision future du score personnalisé.

## 8. Incidents et attribution

| Classe | Sens | Effet |
|---|---|---|
| Sortie obtenue | artefact disponible pour le contrat | entre dans l'évaluation |
| Incident fournisseur | fournisseur ou route n'accomplit pas l'unité prévue | observation attribuable, séparée du contenu de la sortie |
| `HARNESS_ERROR` | Pi ou le dispositif empêche l'attribution | réduit la couverture, ne devient pas `NE SATISFAIT PAS` |
| Preuve manquante | identité ou preuve nécessaire absente | Travail à reprendre ; `INCONNU` pour une valeur absente |

Le verdict n'attribue pas au seul modèle un effet que le fournisseur, l'effort, Pi ou ses réglages peuvent influencer, et ne permet pas d'affirmer que le modèle isolé aurait produit la même sortie avec un autre harnais, fournisseur, contexte ou environnement.

## 9. Vues des évaluations enregistrées

Les campagnes déjà enregistrées conservent leurs questions, panels, schémas et verdicts d'origine. Une vue de consultation peut les résumer fidèlement. Elle ne peut pas :

- renoter une sortie et appeler cela le verdict d'origine
- fabriquer des répétitions ou une stabilité
- déduire un rang qualitatif des `FAIL G-001`
- transformer un `PASS` de qualification du harnais en `PASS` produit
- produire une baseline ou un coût par résultat acceptable
- remplacer une inconnue par une estimation

Le conseil privé reste permis sur les observations enregistrées qui satisfont les gardes de la [section 8 des règles](RULES.md#8-coût-et-bénéfices). Une priorité de consultation ne réécrit ni contrat ni verdict historique.

## 10. Sécurité et autorité

- dossiers du parcours entièrement inventés ; sauf décision contraire, aucun téléversement de dossier réel ni accès aux données de l’ordinateur ou du téléphone
- description générale sans donnée personnelle ou confidentielle ; la consigne ne garantit pas leur absence, le stockage privé suit la politique de conservation et de retrait du PRD
- secrets absents des tâches, sorties publiées et reçus publics
- permissions minimales et outils déclarés
- sorties brutes privées par défaut avant décision de publication
- intégration Git, exécution produit, appels candidats et budget, provisionnement et publication soumis à des autorités distinctes
- champs, filtres, chemins, contenus et sorties considérés comme non fiables : validation côté serveur, requêtes paramétrées et rendu échappé
- aucune donnée candidate rendue comme code actif dans le site ; aucune opération protégée autorisée par un simple libellé ou identifiant public
- les instructions contenues dans une demande, une pièce ou une sortie ne créent aucun droit, changement de règles d’évaluation, budget ou instruction d’exploitation ; elles restent des données à traiter sous les autorités établies
- contextes et ressources de préparation, jugement et candidats séparés ; références réservées au jugement non transmises aux candidats sauf exposition explicitement décidée et déclarée
- si une tâche ou les outils qu’elle autorise peuvent exécuter du code candidat, son confinement doit être vérifié avant l’appel : séparation du serveur public et des secrets, accès fichiers et réseau limités au contrat, ressources et arrêt contrôlés ; un compte de service distinct ne suffit pas à prouver ce confinement

## 11. Extensions de périmètre

Les extensions de périmètre suivent le PRD et les règles KISS. Aucun microservice, Kubernetes, bus de messages, système de plugins ou moteur d'inférence supplémentaire n'est requis par cette architecture.

## 12. Composants et frontières

### 12.1 Frontières du produit

Le frontend et le backend appartiennent au même dépôt produit et sont servis depuis une même VM Linux, sous une même origine. Le moteur commun et ses formats doivent être vérifiables sous Linux et macOS ; la supervision et les comptes de service Linux appartiennent à la couche d’exploitation. Une preuve acquise sur un système ne vaut pas preuve sur l’autre.

| Composant | Responsabilité et accès |
|---|---|
| Serveur public | présenter l’entrée de demande et le parcours interactif ; relayer à l’exécuteur la clé personnelle saisie par le demandeur via HTTPS, sans conserver la clé ; servir la projection approuvée et ses pièces publiables ; aucun accès public direct au stockage privé ni aux secrets opérateur |
| Exécuteur de travaux longs | opérer les campagnes indépendamment des requêtes HTTP, sous identité et autorité propres ; conserver observations, incidents et évaluations privées |
| Opérateur autorisé | préparer, admettre, arrêter, reprendre et approuver selon l’opération ; une interface opérateur web n’est pas exigée |
| Contrôleur de livraison | installer une source ou un artefact approuvé, vérifier son identité et consigner le résultat ; aucun droit d’appel candidat déduit du droit de déployer |

Les comptes du web, de l’exécuteur et de la livraison limitent chacun l’accès à leur responsabilité. Une projection contrôlée est remise au serveur public sous autorité de publication ; exposer directement un dossier privé n’est pas une interface de publication. Les secrets sont injectés séparément du code et de la projection. Les journaux d’exploitation doivent permettre le diagnostic sans exposer secrets, entrées privées ou sorties brutes.

Soumission d’une demande, consultation autorisée de son dossier et publication ouverte sont des accès distincts. Le parcours public ne rend pas ses dossiers ni ses résultats publics par défaut. L’accès privé repose sur une session opaque de navigateur et des contrôles de propriétaire côté exécuteur. La clé fournisseur reste chiffrée chez l’exécuteur ; le serveur public la relaie seulement à son enregistrement. Aucune attribution de secrets au serveur public n’est déduite de l’ouverture du formulaire. Ces choix n’imposent ni nouveau service ni gestion de comptes ; ils bloquent les fonctions qui en dépendent tant qu’ils ne sont pas approuvés. Les décisions de traitement des saisies, conservation, financement, abus et publication relèvent du [PRD](PRD.md#51-capacités).

### 12.2 Persistance et intégrité

SQLite sur disque local conserve les métadonnées transactionnelles. Les pièces privées restent hors du dépôt et des répertoires de release. Les enregistrements lient les pièces par identité, emplacement contrôlé et empreinte. Une référence cassée ou une empreinte divergente empêche d’utiliser la pièce comme preuve.

La version du schéma de stockage et les versions des contrats et formats compatibles sont distinctes. Le runtime vérifie leur compatibilité avant d’écrire ; une version inconnue ou une migration non autorisée bloque l’opération sans réinterpréter les preuves. Les contraintes de référence, l’unicité des tentatives et la réservation budgétaire doivent tenir aussi lorsque plusieurs opérations se présentent simultanément, sans imposer ici une politique de sérialisation.

L’écriture d’une pièce et celle de sa référence doivent laisser un état détectable après interruption. Une pièce incomplète ne peut pas devenir une preuve valide. Initialisation, migration, sauvegarde et restauration couvrent ensemble SQLite, les pièces et leurs liens ; une sauvegarde réussie n’atteste pas une restauration réussie.

### 12.2.1 Conservation privée

L’extension de stockage `s7` ajoute les échéances de sessions et dossiers, la version de contenu, les consentements, les copies autorisées, les suppressions en attente et l’identité de migration. Le schéma complet reste vérifié strictement. Les objets anciens reçoivent une date de début de conservation distincte de l’activité humaine, une seule fois. Aucun consentement n’est déduit de leur présence.

La clé personnelle, et le vérificateur des lignes héritées de l’autorisation déléguée retirée, utilisent AES-256-GCM, un secret maître existant de 32 octets, un nonce aléatoire de 96 bits et un format versionné. Les données associées lient session stable, identité de clé et usage. Une erreur de déchiffrement conserve le chiffré. Les routes de consultation, export et suppression ne résolvent pas le transport fournisseur.

Le cookie principal et celui des contributions sont `HttpOnly`, `Secure`, `SameSite=Strict`, sans `Domain`. Leurs autorités et protections intersites sont distinctes. Un premier GET sans cookie présente une entrée neutre : la requête de même origine retrouve l’accès existant avant toute création. Le parcours sans JavaScript propose « Continuer » et évite les boucles si les cookies sont refusés.

L’archive personnelle repose sur un manifeste figé, versionné, vérifié puis transféré par blocs d’au plus 1 Mio brut. La limite du relais reste inchangée. IndexedDB conserve une archive provisoire avant remplacement atomique de la dernière copie complète. Versions et marqueurs d’effacement empêchent les réponses tardives et les autres onglets de rétablir une copie supprimée. Le rendu utilise des nœuds texte ; l’archive locale ne constitue pas une certification serveur. Aucun service worker ni réimport moteur n’est requis.

La copie de contribution utilise une liste de champs autorisés distincte de l’archive personnelle. Elle est construite par le serveur, autonome, sans lien physique vers les pièces temporaires. Chaque écriture revérifie consentement, version d’exemple, échéance et absence de retrait dans la transaction. Son accès de gestion est enregistré sous forme d’empreinte du jeton.

Le cycle d’un dossier est `ACTIVE → DELETE_REQUESTED → PURGING → PURGED`. La demande ferme immédiatement les nouveaux appels et accès ordinaires. Le verrou partagé couvre la dernière écriture de tout travailleur ; un reçu déjà engagé peut terminer. La purge obtient ensuite le verrou exclusif et supprime les dépendances enfants avant leurs parents. Toute référence entrante inattendue bloque cette purge sans élargir son périmètre. Les règles d’immuabilité n’autorisent une suppression que pour le dossier effectivement en purge.

La transaction de purge conserve le manifeste exact des fichiers à retirer et les seules métadonnées nécessaires à la prévention des réémissions et au suivi des coûts inconnus. La suppression des fichiers reprend après interruption, contrôle leur répertoire, leur identité et les liens, puis confirme leur absence. `secure_delete=ON` et un nettoyage initial des pages libres traitent SQLite actif ; ils ne prétendent pas effacer les snapshots ou le support physique. Le mode de journal SQLite reste `DELETE`. Les anciens dossiers financés par l’opérateur nécessitent un traitement distinct préservant leur comptabilité ; aucun nouveau dossier personnel n’entre dans cette exception.

Un journal minimal de révocation hors des sauvegardes applicatives précède chaque retrait. Il ne contient ni clé ni texte. La restauration reste en quarantaine jusqu’à application des révocations, expirations et purges. Sans preuve indépendante d’actualité du journal après restauration complète de VM, l’accès privé reste fermé. Un redémarrage normal de la machine ne met pas le service en quarantaine. Une restauration à chaud exige une mise en quarantaine explicite avant tout accès.

La migration explicite arme une barrière persistante de démarrage avant quiescence. Après sauvegarde et restauration isolée vérifiée, les services restent arrêtés et le contrôleur conserve son exclusion pendant que le candidat acquiert ses propres verrous de migration. L’identité est enregistrée dans la transaction `s7`. Après commit, seul un programme compatible peut reprendre ; aucun rollback automatique de données n’est permis. Le programme compatible est activé durablement avant levée de la barrière. Un déploiement ordinaire ne migre jamais implicitement.

Un traitement planifié natif exécute purges et expiration des sauvegardes sans visite du site. La rétention applicative, les copies de travail, snapshots et sauvegardes VM/PBS doivent être inventoriés et vérifiés avant de promettre une durée publique. Les snapshots de VM doivent être bornés à sept jours par âge réel ; cette borne reste à vérifier. Tant que cet inventaire n’est pas vérifié, aucune borne de conservation en sauvegarde n’est annoncée dans une page servie ni dans une notice. Une telle borne ne certifierait pas un effacement physique du support. Une demande de suppression ou une attestation non étayée ne prouve pas l’effacement.

### 12.3 Interfaces et cycle de vie

Les lignes suivantes fixent des effets et des preuves, pas des noms de commandes ni un format de protocole.

Interruption et reprise couvrent aussi les appels assistés de préparation et de jugement, même avant l’existence d’une campagne. Un appel aux effets inconnus ne peut pas être rejoué implicitement et se clôt automatiquement après quinze minutes ; les réservations et dépenses des différentes phases restent attribuées et ne sont pas allouées deux fois.

| Opération | Entrée et effet autorisé | Preuve ou refus attendu |
|---|---|---|
| Initialiser | emplacement de données et runtime identifiés | schéma et répertoires privés cohérents ; aucune base existante écrasée |
| Préparer | demande et brouillon, puis version de tâche, cas, panel et conditions communes | assistance sous autorité et budget propres ; dossier construit et aperçu lié au paquet, validations et qualification référencées ; gel du contrat et du manifeste distincts, aucun appel candidat |
| Admettre et lancer | manifeste et autorités d’exécution, d’appels et de budget | contrôles des [règles d’admission](RULES.md#9-incidents-et-inconnues), réservation et intention persistante avant émission |
| Interrompre | exécution identifiée et motif d’arrêt | admission de nouveaux appels arrêtée, travaux actifs suivis, reçus acquis conservés ; effets inconnus signalés |
| Reprendre | état conservé et autorité explicite | rapprochement des tentatives et du budget, cellules encore autorisées identifiées ; aucun rejeu d’une tentative ambiguë |
| Évaluer et restituer | observations intègres, méthode et décisions requises | jugement assisté éventuel sous autorité et budget propres, sans nouvel appel candidat ; restitution sans appel modèle, verdicts et comparaison traçables, couverture partielle visible |
| Publier | projection et pièces explicitement approuvées | identité de publication et cohérence des références visibles ; aucune ouverture implicite des données privées |

L’état de campagne décrit préparation, admission, activité, interruption ou clôture à partir des reçus. Une clôture peut être partielle ; elle ne prouve ni satisfaction ni publication. Les tentatives conservent leur état propre et les cellules non lancées restent distinguées. Une correction d’évaluation autorisée crée une nouvelle évaluation reliée à la précédente ; elle ne remplace pas silencieusement le verdict déjà publié.

## 13. Historique Git

Les révisions et décisions retirées ne décrivent pas le produit courant. Git est l’unique archive de ces états.
