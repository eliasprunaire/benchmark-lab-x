---
style_gate: pass
---

# ARD : contrat d'architecture de Benchmark Lab-X

## 1. Portée

Cet ARD fixe les objets, frontières, flux et contraintes de l'architecture retenue. Il prescrit ce que l'implémentation doit garantir, sans attester sa construction ou son déploiement. Les versions et états livrés se vérifient dans le code et les reçus, hors de ce document.

Le [PRD](PRD.md) gouverne le besoin et le périmètre. Les [règles](RULES.md) portent les invariants. Le [glossaire](../CONTEXT.md) fixe le sens des termes. Le [gabarit de carte](../tasks/TEMPLATE.md) prépare le contrat d'une tâche.

Le produit du monorepo partage une version ; les contrats de compatibilité et les versions de schéma suivent les [règles de versionnement](RULES.md#14-versionnement-du-produit).

## 2. Principes

L’architecture relie des contrats figés, des observations attribuables et des vues vérifiables. Les invariants de décision, de coût et d’histoire appartiennent aux [règles](RULES.md) ; le parcours et les exclusions appartiennent au [PRD](PRD.md#10-restitution-publique).

## 3. Pi comme frontière constante

Pi est le harnais commun des comparaisons. Sa constance réduit une source de variation entre configurations ; elle ne prouve pas que Pi est neutre ou que le modèle seul cause le résultat. Pi, ses paquets et l'environnement sont présentés une fois comme conditions de test communes, jamais comme propriétés répétées de chaque modèle.

Pi est la frontière des comparaisons candidates ; il ne choisit pas les modèles de préparation ou de jugement. OpenRouter est le canal normal des appels modèles du produit. Pour une acquisition en incident, après diagnostic et épuisement des routes utilisables du même modèle, une API officielle peut être admise comme dernier recours explicite. Cette configuration est distincte : identité exacte disponible, preuve de correspondance de version, paramètres réellement pris en charge, accès produit, budget, octets émis et reçu natif doivent être liés. Pi, la tâche et les projections fermées restent constants. Aucune substitution de modèle ou révision ni recours destiné à contourner un refus de sécurité n’est permis. Le secret reste réservé à l’exécuteur. Les reprises techniques ne prononcent aucun verdict et ne sélectionnent pas des réponses jusqu’au succès. Le secours natif OpenRouter de préparation et correction conserve ses endpoints explicitement autorisés et les mêmes paramètres. Cette règle produit ne s’applique pas aux outils de développement et ne requalifie aucun contrat historique.

L’architecture est indépendante des modèles et de leurs versions commerciales. L’assistant de préparation est choisi au démarrage par un profil OpenRouter approuvé, désigné par un alias de compatibilité ou le chemin d’un fichier JSON local. Les identifiants disponibles et les alias pris en charge relèvent de la configuration et de la documentation opérateur. Le profil fige l’identifiant demandé, la révision unique de réponse autorisée, les paramètres envoyés, les routes, les capacités attendues, le message système et les limites de taille et de temps, sans dépasser les plafonds historiques de cet adaptateur. Il est chargé une fois, recopié indépendamment du fichier ou de l’objet appelant, et son empreinte canonique est liée à la configuration demandée avec le relevé tarifaire et la réserve. Le chemin hôte du fichier n’entre pas dans la configuration, les messages ni les reçus. Une divergence de profil, d’empreinte, de modèle, de révision, de paramètres, de système, de route, de capacité, de relevé, de réserve ou de limite bloque avant l’émission HTTP. Aucun registre de plugins, fabrique générique, base de profils, synchronisation de catalogue ou OAuth personnel n’est requis. Les profils des assistants restent OpenRouter ; le secours officiel candidat exige sa propre configuration. Fournir un fichier de profil ne sélectionne aucun nouvel assistant de production.

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
- accès API via OpenRouter pour les appels courants ; accès d’origine conservé dans les preuves historiques
- route demandée et route observée
- paramètres demandés et observés
- effort de raisonnement demandé et observé
- identité demandée et identité observée
- référence aux conditions de test communes

Une demande et une observation restent distinctes. Une identité, une route ou une valeur absente reste `INCONNU`. Une révision imposée ne peut pas être remplacée par un alias mobile non vérifié. Le panel fige la liste des configurations ; il ne prouve pas leur disponibilité.

Pour une configuration locale, l'identité inclut les poids, leur révision, la quantification, le serveur d'inférence et le matériel nécessaires à l'attribution.

### 4.4 Acquisition, tentative et exécution

Les transports construisent des projections sortantes fermées par rôle. Le candidat reçoit la consigne, les livrables, les critères publics, les ambiguïtés utiles et les pièces candidates vérifiées sous leurs noms logiques. Le préparateur reçoit la demande initiale, le message courant, la reformulation, les clarifications antérieures, les accords validés, les paramètres fictifs et le précédent contenu candidat utile ; le kind du workflow reste interne. La référence de jugement est reconstruite, sans reprendre un ancien verdict comme autorité. La génération sépare le paquet candidat, les notes internes et la référence de jugement ; le modèle n’assigne aucun rôle. La revue reçoit la tâche candidate exacte, le résultat attendu, les obligations, les erreurs éliminatoires, la méthode, les critères secondaires nécessaires, les limites de la spécification de jugement, la sortie identifiée et les références autorisées, chaque pièce de preuve portant identifiant, nom logique, empreinte et contenu. Les identifiants de campagne et de tentative restent dans une liaison locale. L’export opérateur reste local. Statuts, historique, autorités, budgets et chemins hôte ne sont pas sérialisés vers ces rôles. Les notes internes et la référence de jugement restent distinctes de la sortie candidate dans la génération.

Une exécution identifie une opération du produit : préparation, acquisition, évaluation ou construction de restitution. Elle référence la version du moteur, ses entrées, son autorité, sa chronologie et sa terminaison. Une préparation référence le brouillon avant qu’une campagne existe ; les exécutions suivantes référencent leur campagne. Une campagne peut avoir plusieurs exécutions ; une exécution d’acquisition peut contenir plusieurs tentatives.

Les appels assistés de préparation, correction et jugement conservent leur intention avant émission, la configuration demandée et observée, les ressources vues, le reçu, les coûts et les effets inconnus. Ils sont attribués à leur opération, séparément des tentatives candidates, sans créer de résultat de benchmark. Leur budget et leur autorité précèdent leur consommation ; arrêt et reprise suivent les mêmes règles d’absence de rejeu implicite. Un coût reçu comme inconnu peut être rapproché par une preuve externe attribuable ajoutée sans écraser le reçu ; le coût effectif reste distinct de l’observation initiale. Ce rapprochement ne rouvre pas l’admission et ne rejoue aucune action. La préparation et la correction distinguent ce coût financier de l’indication calculée après succès selon les [règles économiques](RULES.md#8-coût-et-bénéfices) ; le seul coût financier inconnu d’une opération reçue ne bloque pas un nouvel échange autorisé, sa réserve restant comptée.

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

Une évaluation non concluante conserve son rapport et ses preuves comme travail à reprendre, sans verdict métier. Son état, ses causes et la prochaine action restent consultables. Les anciens verdicts `INDETERMINE` gardent leur valeur d’origine dans l’historique.

L'évaluation s'applique à un cas et une tentative identifiés. Le verdict porte les éléments exigés par la règle « Verdict explicable » des [règles](RULES.md#6-erreurs-et-verdict) : valeur, motif court, critères ou constats concernés, références de preuve et responsable. L’évaluation référence la version de la méthode et de la référence de jugement ainsi que les preuves de leur qualification. Lorsqu’elle est assistée par un modèle, elle conserve sa configuration demandée et observée, les consignes, les pièces vues et l’arbitrage humain ; ces éléments se distinguent de la configuration candidate et suivent les [règles de provenance](RULES.md#5-sortie-et-provenance). L’éventuelle revue professionnelle est une preuve attribuée à son auteur et à son périmètre, sans nouveau rôle produit obligatoire. Une mesure valide d’un critère secondaire peut décrire une sortie non admissible ; elle ne change pas le verdict ni ne compense une erreur éliminatoire. Un défaut de consigne, de données, de référence, de contrôle ou d’exécution ne devient pas artificiellement une erreur candidate.

### 4.6 Vue de décision

Responsabilité : présenter les évaluations, les classements par critère, les filtres et la complétude de la comparaison des coûts, selon les [règles](RULES.md#7-ordre-de-décision). Chaque colonne ordonnable référence sa définition contractuelle et ses preuves ; les valeurs inconnues ou incompatibles sont sans rang, les égalités conservées. La vue expose le périmètre filtré sans modifier les sorties, reçus, verdicts ou populations des statistiques déjà calculées. Elle ne fusionne pas des couvertures incompatibles et ne désigne aucune option automatiquement. Le score pondéré personnalisé reste différé selon le PRD ; aucun moteur ni formule n’est ajouté à 0.1.0.

### 4.7 Restitution

La restitution relie les identités du catalogue, de la tâche et de la campagne au parcours du [PRD](PRD.md#10-restitution-publique). La publication est une projection approuvée et identifiée de cette restitution et de ses pièces autorisées, séparée des données privées et de l’état d’exécution.

La projection référence les versions de schéma, de présentation, de conclusion et de pièces utilisées. Sa mise à disposition doit être cohérente : une consultation ne mélange pas une conclusion nouvelle avec les pièces d’une publication précédente. Une nouvelle présentation ne relance aucune acquisition et ne modifie aucun verdict ; son exposition exige l’autorité de publication correspondante.

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
  -> Évaluation : SATISFAIT / NE SATISFAIT PAS, ou travail à reprendre sans verdict ; preuves conservées
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
- la publication à la vue et aux preuves explicitement approuvées

Un libellé public n'est pas une clé de jointure. La sortie brute et les preuves historiques ne sont pas corrigées silencieusement.

La conclusion refuse de combiner comme comparables les éléments suivants ; les observations conservées et leur couverture restent consultables :

- contrats différents ou modifiés après résultat
- conditions de test communes différentes entre candidats présentés comme comparables
- identité incomplète au regard du contrat
- sortie modifiée après acquisition
- mesure de classement absente des critères prévus
- coût `INCONNU`, base de coût incompatible ou couverture insuffisante pour le classement proposé

Une mesure valide d’une configuration non admissible peut être classée sur son critère ; elle ne permet pas de la désigner comme utilisable. La vue conserve le verdict et son motif pendant les tris.

## 7. Verdict, coût et bénéfices

L’évaluation applique l’[ordre de décision](RULES.md#7-ordre-de-décision), puis la vue applique les [règles économiques](RULES.md#8-coût-et-bénéfices). La conclusion économique décrit le périmètre et la complétude de la comparaison des coûts, avec inconnues et incompatibilités, sans objet de recommandation « co-moins-chères ». Elle reste distincte du verdict : `INCOMPLETE` est un état économique distinct du verdict. Les versions de l’évaluateur, de sa méthode et du calcul de conclusion restent reliées aux preuves. Le coût ne compense aucune non-admissibilité, y compris dans la vision future du score personnalisé.

## 8. Incidents et attribution

| Classe | Sens | Effet |
|---|---|---|
| Sortie obtenue | artefact disponible pour le contrat | entre dans l'évaluation |
| Incident fournisseur | fournisseur ou route n'accomplit pas l'unité prévue | observation attribuable, séparée du contenu de la sortie |
| `HARNESS_ERROR` | Pi ou le dispositif empêche l'attribution | réduit la couverture, ne devient pas `NE SATISFAIT PAS` |
| Preuve manquante | identité ou preuve nécessaire absente | Travail à reprendre ; `INCONNU` pour une valeur absente |

Le verdict n'attribue pas au seul modèle un effet que le fournisseur, l'effort, Pi ou ses réglages peuvent influencer, et ne permet pas d'affirmer que le modèle isolé aurait produit la même sortie avec un autre harnais, fournisseur, contexte ou environnement.

## 9. Vues historiques

Les campagnes historiques restent dans leurs questions, panels, schémas et verdicts d'origine. Une vue historique peut les résumer fidèlement. Elle ne peut pas :

- renoter une sortie et appeler cela le verdict d'origine
- fabriquer des répétitions ou une stabilité
- déduire un rang qualitatif des `FAIL G-001`
- transformer un `PASS` de qualification du harnais en `PASS` produit
- produire une baseline, un coût par résultat acceptable ou une recommandation absente
- remplacer une inconnue par une estimation

## 10. Sécurité et autorité

- dossiers du parcours 0.1.0 entièrement inventés ; aucun téléversement de dossier réel ni accès aux données de l’ordinateur ou du téléphone
- description générale sans donnée personnelle ou confidentielle ; la consigne ne garantit pas leur absence, la politique de traitement et de conservation d’une saisie sensible doit être approuvée avant ouverture
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

## 12. Composants et exploitation

### 12.1 Frontières du produit

Le frontend et le backend appartiennent au même dépôt produit et sont servis depuis une même VM Linux, sous une même origine. Le moteur commun et ses formats doivent être vérifiables sous Linux et macOS ; la supervision et les comptes de service Linux appartiennent à la couche d’exploitation. Une preuve acquise sur un système ne vaut pas preuve sur l’autre.

| Composant | Responsabilité et accès |
|---|---|
| Serveur public | présenter l’entrée de demande et le parcours interactif ; servir la projection approuvée et ses pièces publiables ; aucun accès public direct au stockage privé ni aux secrets fournisseurs |
| Exécuteur de travaux longs | opérer les campagnes indépendamment des requêtes HTTP, sous identité et autorité propres ; conserver observations, incidents et évaluations privées |
| Opérateur autorisé | préparer, admettre, arrêter, reprendre et approuver selon l’opération ; une interface opérateur web n’est pas exigée |
| Contrôleur de livraison | installer une source ou un artefact approuvé, vérifier son identité et consigner le résultat ; aucun droit d’appel candidat déduit du droit de déployer |

Les comptes du web, de l’exécuteur et de la livraison limitent chacun l’accès à leur responsabilité. Une projection contrôlée est remise au serveur public sous autorité de publication ; exposer directement un dossier privé n’est pas une interface de publication. Les secrets sont injectés séparément du code et de la projection. Les journaux d’exploitation doivent permettre le diagnostic sans exposer secrets, entrées privées ou sorties brutes.

Soumission d’une demande, consultation autorisée de son dossier et publication ouverte sont des accès distincts. Le parcours public ne rend pas ses dossiers ni ses résultats publics par défaut. Avant réalisation de ces accès, décider l’identité ou session, les droits, l’isolation et le composant existant responsable de la préparation assistée et de ses accès fournisseur. Aucune attribution de secrets au serveur public n’est déduite de l’ouverture du formulaire. Ces choix n’imposent ni nouveau service ni gestion de comptes ; ils bloquent les fonctions qui en dépendent tant qu’ils ne sont pas approuvés. Les décisions de traitement des saisies, conservation, financement, abus et publication relèvent du [PRD](PRD.md#51-périmètre-010).

### 12.2 Persistance et intégrité

SQLite sur disque local conserve les métadonnées transactionnelles. Les pièces privées restent hors du dépôt et des répertoires de release. Les enregistrements lient les pièces par identité, emplacement contrôlé et empreinte. Une référence cassée ou une empreinte divergente empêche d’utiliser la pièce comme preuve.

La version du schéma de stockage et les versions des contrats et formats historiques sont distinctes. Le runtime vérifie leur compatibilité avant d’écrire ; une version inconnue ou une migration non autorisée bloque l’opération sans réinterpréter les preuves. Les contraintes de référence, l’unicité des tentatives et la réservation budgétaire doivent tenir aussi lorsque plusieurs opérations se présentent simultanément, sans imposer ici une politique de sérialisation.

L’écriture d’une pièce et celle de sa référence doivent laisser un état détectable après interruption. Une pièce incomplète ne peut pas devenir une preuve valide. Initialisation, migration, sauvegarde et restauration couvrent ensemble SQLite, les pièces et leurs liens ; une sauvegarde réussie n’atteste pas une restauration réussie.

### 12.3 Interfaces et cycle de vie

Les lignes suivantes fixent des effets et des preuves, pas des noms de commandes ni un format de protocole.

Interruption et reprise couvrent aussi les appels assistés de préparation et de jugement, même avant l’existence d’une campagne. Un appel aux effets inconnus ne peut pas être rejoué implicitement ; les réservations et dépenses des différentes phases restent attribuées et ne sont pas allouées deux fois.

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

### 12.4 Exploitation vérifiable

Avant usage opérationnel, le produit doit fournir à l’infrastructure les interfaces suivantes avec leur preuve de validation.

| Besoin | Contrat à vérifier |
|---|---|
| Démarrage et santé | disponibilité du web et de sa projection, disponibilité distincte de l’exécuteur et du stockage ; aucun appel candidat utilisé comme test de santé |
| Arrêt et maintenance | inhibition des nouveaux appels, arrêt contrôlé et état des travaux actifs ou ambigus, y compris après arrêt forcé ; aucun redémarrage ne reprend implicitement une campagne |
| Livraison | provenance reliant l’artefact au commit produit approuvé, empreinte des octets installés, compatibilité des données et reçu ; un identifiant déclaratif de commit ne prouve pas la construction |
| Sauvegarde | point cohérent de SQLite et des pièces associées, manifeste d’intégrité, accès privé et résultat observable |
| Restauration | cible autorisée et données existantes à préserver identifiées, intégrité et compatibilité, lisibilité des pièces et validité des liens métier, puis autorité distincte avant reprise ; restaurer un état antérieur ne prouve pas qu’un appel ultérieur n’a jamais eu lieu |

Une reprise après restauration doit rapprocher les preuves de tentatives potentiellement postérieures à la sauvegarde ; si leurs effets ou leur coût restent inconnus, les opérations dépendantes restent bloquées. Une bascule inverse de code ne vaut pas rollback de données.

Les noms de commandes, le format d’artefact, le lieu de build, le transport de livraison, la politique de concurrence, les fenêtres de sauvegarde, le maintien du web pendant celles-ci et les modalités concrètes de reprise restent à décider. Le candidat local de cybrel-infrastructure n’accorde aucune valeur normative à ses choix sur ces points. Les affectations VM, réseau, domaine, ressources, sauvegarde et supervision doivent être fixées dans un contrat d’exploitation à approuver dans ce dépôt indépendant.

### 12.5 Dépôts et infrastructure

GitHub porte le produit et le backlog ; Forgejo pilote la livraison et le déploiement contrôlés. Le contrôleur appartient à cybrel-infrastructure. Ses accès au runner, au réseau et aux secrets doivent être définis avant son installation. Aucun miroir bidirectionnel ni deuxième backlog produit n’est requis.

Le provisionnement utilise les primitives Terraform, l’orchestrateur Bash et Ansible de Cybrel. L’exposition respecte la chaîne Consul, consul-template et HAProxy, notamment la déclaration initiale du backend avant son référencement dynamique. Une release produit ne relance pas implicitement le provisionnement.

Graph Engineering Tool reste dans son dépôt indépendant pour l’exécution agentique des Stories. Il ne remplace ni le moteur des campagnes ni leurs autorisations. Son identité est épinglée dans chaque contrat de run ; aucun numéro de version de cet outil n’est fixé ici. La piste d’une VM macOS Graph est exclue.
