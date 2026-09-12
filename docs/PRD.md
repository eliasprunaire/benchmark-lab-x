---
style_gate: pass
---

# PRD de Benchmark Lab-X

## 1. Rôle et autorité

Ce document fixe la vision durable, le besoin, l’audience et les résultats attendus de Benchmark Lab-X. Les parcours et principes décrivent le produit à long terme ; un périmètre associé à une version borne seulement son jalon. Le document ne porte aucun statut de livraison et les options non décidées ne deviennent pas des exigences par leur seule mention.

L'approbation de ce document n'autorise aucune campagne ni publication. Les campagnes historiques restent sous leurs contrats d'origine.

L'[ARD](ARD.md) fixe le contrat d'architecture. Les [règles](RULES.md) portent les invariants. Le [glossaire](../CONTEXT.md) fixe le vocabulaire.

## 2. Besoin

**FAIT ÉTABLI** : le besoin originel est de permettre à la communauté Lab X de tester elle-même des solutions d'IA sur des tâches utiles, avec des preuves lisibles plutôt qu'un palmarès repris d'un tiers.

L’utilisateur décrit un travail qu’il souhaite comparer. Le produit l’aide à le préciser et construit un dossier fictif qu’il peut examiner et modifier avant de valider le besoin représenté. Une campagne autorisée compare ensuite les configurations de modèle sous le même harnais Pi. Résultats, preuves et coûts observés alimentent des classements par critère et des filtres ; l’utilisateur choisit la configuration qui lui convient. Le produit ne désigne pas automatiquement un gagnant et un coût faible ne rend pas une sortie erronée utilisable.

Le nom du modèle ne suffit toutefois pas comme preuve. Le modèle est l'identifiant principal présenté, mais le verdict s'applique à sa configuration observée sous les conditions de test communes déclarées. Aucun effet que le fournisseur, l'effort, Pi ou ses réglages peuvent influencer n'est attribué au seul modèle.

## 3. Audience et jobs-to-be-done

### 3.1 Audience et accès

Le produit s’adresse à la communauté Lab X et aux utilisateurs qui cherchent une configuration adaptée à une tâche. Les tâches représentent des besoins de métiers et de domaines variés ; le catalogue n’est pas limité aux usages informatiques ou administratifs. Dès 0.1.0, le parcours public permet de décrire un besoin et de préparer une épreuve avec une assistance IA. L’approbation du contrat, l’exécution et la publication gardent leurs autorités propres. Le site permet aussi de consulter les résultats dont la publication a été approuvée.

La description reste générale, sans donnée personnelle ni information confidentielle. Les personnes, organismes, échanges et pièces du dossier sont entièrement inventés ; un dossier réel simplement anonymisé ne convient pas. Le parcours 0.1.0 ne comprend aucun téléversement de dossier réel, accès à l’ordinateur, connecteur vers les données de l’utilisateur ou action sur son téléphone. Une consigne de saisie ne garantit pas l’absence de contenu sensible et aucune anonymisation automatique, notamment dans le navigateur, n’est promise. Les modalités d’accès, de conservation et de traitement d’une saisie sensible restent à décider avant ouverture.

Le catalogue distingue le métier ou domaine, qui donne le contexte, et la famille de tâche, qui décrit le travail, par exemple : extraire, rapprocher, synthétiser, rédiger, décider, organiser, rechercher ou argumenter. Ces repères peuvent se croiser et évoluer. Ils servent à trouver un usage proche, sans promettre une compétence générale sur une profession.

### 3.2 Jobs-to-be-done

| Situation                                        | Job-to-be-done                                                                | Résultat utile                                           |
| ------------------------------------------------ | ----------------------------------------------------------------------------- | -------------------------------------------------------- |
| Mon besoin est encore vague | le préciser sans inventer une méthode de benchmark | reformulation fidèle et exemple fictif validable |
| L’exemple ne me convient pas | corriger ce qui diffère de mon besoin | dossier révisé sans perdre les accords non touchés |
| Je dois choisir un modèle pour une tâche précise | comparer les résultats sous le même Pi | verdicts, classements par critère et filtres explicables |
| Je veux examiner les compromis de coût et de résultat | consulter des mesures connues et comparables | erreurs visibles, égalités conservées et inconnues non classées |
| La preuve ne suffit pas | résoudre ce qui empêche la décision | préparation, exécution ou évaluation à reprendre, avec cause et prochaine action |
| Je veux vérifier une conclusion                  | retrouver tâche, contrat, configuration, sortie et preuves                    | chaîne d'attribution bornée                              |
| Je cherche une tâche proche de mon besoin        | parcourir le catalogue et sa couverture réelle                               | tâche, version, cas et campagnes pertinents              |

Le [demandeur-lecteur](../CONTEXT.md#demandeur-lecteur) exprime son besoin et valide l’exemple qui le représente. Il n’a pas à inventer un seuil, une métrique ou une méthode de jugement : le [responsable de campagne](../CONTEXT.md#responsable-de-campagne) prépare et approuve le contrat avant les appels candidats. L’assistance de préparation exige ses propres autorités. Ces deux rôles peuvent être tenus par la même personne ; leur existence n’impose aucun système de comptes. L’affectation de l’approbateur dans le service public reste à décider.

## 4. Question active

> À partir d’un besoin précisé avec l’utilisateur et d’un exemple fictif validé, que produisent les configurations testées sous le même harnais Pi, sur les critères fixés avant exécution et à quel coût observé ? Quels résultats et limites permettent à l’utilisateur de faire son choix ?

Qualité ou stabilité ne deviennent des critères que si une tâche les définit de manière testable avant l'exécution, dans la limite du contrat minimal.

## 5. Périmètre produit

### 5.1 Périmètre 0.1.0

Ce jalon réunit les capacités ci-dessous et les [critères d’acceptation produit](#12-critères-dacceptation-produit). Son numéro suit les [règles de versionnement](RULES.md#14-versionnement-du-produit) ; il ne porte aucun état de livraison.

- parcours public de description, clarification et préparation assistée d’un dossier fictif consultable et modifiable
- catalogue de tâches versionnées, avec contrat et cas d'essai identifiés ; aucune demande n’y est publiée automatiquement
- plusieurs campagnes, chacune liée à une version de tâche, à ses cas et à un panel figé
- résultats réellement acquis et évalués sur le catalogue et le panel approuvés
- accès API via OpenRouter sous Pi constant ; secours officiel candidat en dernier recours selon les conditions de l’ARD
- suivi des tentatives, incidents, coûts et preuves sans relance implicite
- navigation catalogue, tâche, campagne et comparaison des configurations
- classements par critère et filtres combinés, sans note pondérée ni désignation automatique du meilleur modèle
- consultation publique des seules restitutions approuvées, sans classement universel

Le parcours fondé sur les demandes des utilisateurs remplace le corpus prédéfini de deux à neuf tâches. Comparer des salles pour une association et transformer des notes de réunion en suivi des décisions et actions deviennent des exemples pédagogiques, sans périmètre obligatoire ni preuve de couverture métier. Les cas de chaque tâche restent à construire et à qualifier. Les réponses d’une campagne sont réellement acquises sous autorisation ; une simulation ne constitue pas un résultat de benchmark. Le choix et le nombre des campagnes réelles nécessaires à 0.1.0 restent à décider.

Le produit est agnostique des modèles et de leurs versions. Les choix courants relèvent du registre de modèles, des profils d’assistance approuvés et des manifestes de campagne. Ils peuvent évoluer sans modifier les spécifications. Chaque campagne fige son panel et les identités exactes requises avant admission ; les résultats historiques conservent leurs configurations d’origine.

La sélection d’un modèle ne prouve ni sa disponibilité ni sa compatibilité avec le harnais. Les contrats, cas, configurations exactes, accès, routes, agrégations et budgets sont arrêtés avant les manifestes de campagne. Aucun alias ou modèle de substitution n’est déduit du nom retenu. La capacité logicielle sur données synthétiques prouve seulement le logiciel ; 0.1.0 exige aussi les résultats réels autorisés.

Les assistants d’accueil, de préparation et de jugement sont sélectionnés séparément du panel candidat. Aucun assistant n’est déclaré qualifié, disponible ou moins cher sans preuve. Le responsable peut retenir un profil OpenRouter déjà approuvé au démarrage, sans modifier le code ; un fichier de profil ne promeut aucun essai en assistant de production. Les essais de préparation examinent la fidélité au besoin vague, l’utilité des questions, l’absence de besoins inventés, la cohérence et la vérifiabilité du cas, ainsi que la conservation des accords lors des modifications. Coûts et provenance sont examinés séparément de cette qualité ; identifiants, méthode d’essai et budget doivent être décidés avant les appels.

Restent ouverts avant réalisation ou ouverture des fonctions concernées : approbateur du contrat et admission des demandes, identité et sessions, traitement des saisies sensibles et conservation, financement et enveloppes, protection contre les abus, articulation des panels et campagnes réelles, publication et alimentation du catalogue. La confidentialité demeure la règle avant autorisation de publication. Les frontières d’accès et le composant portant l’assistance sont à préciser dans l’[ARD](ARD.md#121-frontières-du-produit). Ces inconnues n’annulent pas le cap produit ; elles n’autorisent aucun choix implicite de compte, quota, prix ou publication automatique.

### 5.2 Extensions

Un score pondéré personnalisé appartient à la vision durable, hors 0.1.0. Avant réalisation, ses composantes, poids, normalisation, traitement des inconnues et erreurs, règles d’agrégation et sensibilité des rangs doivent être décidés et explicables. Les mesures d’origine, les verdicts et les erreurs restent accessibles ; le coût ne rend jamais acceptable une sortie non admissible. Cette capacité ne produit aucun meilleur modèle absolu ni classement universel.

La couverture de métiers variés appartient à la vision durable : droit et notariat, documentation de santé, enseignement, artisanat, maintenance, logistique, agriculture, comptabilité, journalisme ou qualité industrielle, sans liste fermée ni couverture de tous ces domaines exigée pour 0.1.0. Les cas sont choisis pour leur utilité et les difficultés concrètes du travail, sans obligation de mettre en échec un humain ou un modèle réputé performant.

Les bons modèles locaux appartiennent à la vision durable du produit, sans intégration imposée à 0.1.0. Leur entrée dans un panel exige une décision propre, avec identité des poids, quantification, serveur d’inférence, matériel et base de coût explicites. Cette perspective n’autorise aucun contournement du canal normal [OpenRouter](ARD.md#3-pi-comme-frontière-constante).

Les abonnements comme objets de comparaison, les produits agentiques et la comparaison de harnais exigent un besoin démontré et une décision de périmètre. Aucun scénario de maquette ne devient implicitement une tâche du catalogue.

## 6. Contrat de réussite

Le contrat d’une version de tâche traduit le besoin en résultat attendu, obligations, erreurs éliminatoires et critères secondaires prévus. Le responsable de campagne l’approuve avant les appels candidats ; confirmer le besoin ne remplace ni cette approbation ni la qualification de la référence. Les [règles](RULES.md#4-contrat-avant-exécution) fixent le contenu minimal, le gel, les verdicts et les conditions d’agrégation.

La tâche annonce ce qu’elle mesure et le travail qui reste à l’utilisateur : brouillon à reprendre, résultat utilisable après relecture ou autre usage explicitement défini. La qualité de l’épreuve dépend aussi de sa référence de jugement et de ses contrôles, qualifiés avant l’approbation du contrat selon les règles ; la réputation d’un modèle ne suffit à elle seule ni à valider l’épreuve ni à l’invalider.

Les cas explicitent les difficultés qu’ils couvrent. La tâche annonce la portée de sa conclusion et la base de coût nécessaire à la décision : une réussite sur un exemple ne suffit pas à promettre une fiabilité générale. L’absence de règle d’agrégation limite la restitution aux verdicts par cas.

Le [gabarit de carte](../tasks/TEMPLATE.md) matérialise ce contrat. La méthode de contrôle, les données et leur provenance rendent chaque obligation vérifiable.

## 7. Ordre de décision

L’évaluation établit les verdicts avant la comparaison. L’utilisateur consulte ensuite les mesures et coûts connus au moyen de classements par critère et de filtres, selon l’[ordre de décision](RULES.md#7-ordre-de-décision). Une mesure valide reste visible pour une sortie non admissible ; son rang ne change pas le verdict et le coût ne rend pas cette sortie utilisable.

La comparaison ne désigne aucune option automatiquement. Chaque tri annonce son critère et son périmètre ; les valeurs inconnues ou incompatibles restent sans rang. Les filtres ne changent pas le contrat, les verdicts ou le dénominateur d’une statistique déjà calculée. Sans règle d’agrégation préalable, les résultats restent par cas et tentative. La conclusion reste bornée à la tâche, à sa version et aux observations de la campagne.

## 8. Preuve et transparence

Les [conditions de test communes](../CONTEXT.md#conditions-de-test-communes) sont exposées une fois par comparaison : état de Pi, environnement et date de gel. Chaque configuration observée expose ensuite ses valeurs propres : fournisseur, modèle, accès API via OpenRouter, route, paramètres et effort de raisonnement, demandés puis observés. Les preuves historiques conservent leur accès d’origine. Les champs exacts sont ceux de l'[ARD](ARD.md#4-objets-et-responsabilités).

Une valeur non observée reste `INCONNU`. La restitution porte l'avertissement suivant ou une formulation équivalente :

> Le verdict porte sur la configuration observée sous les conditions de test communes déclarées. Il n'attribue pas au seul modèle un effet que le fournisseur, l'effort, Pi ou ses réglages peuvent influencer, et ne démontre pas que le modèle isolé aurait produit le même résultat sous un autre harnais, fournisseur, contexte ou environnement.

## 9. Contrats historiques

Les campagnes historiques conservent leurs questions, contrats, observations et verdicts. Leur bilan opérationnel appartient aux preuves d’origine ; il ne vaut pas validation de la méthode courante.

## 10. Restitution publique

### Préparer et valider l’exemple

L’invite d’accueil est :

> Décrivez une tâche de votre travail, sans donnée personnelle ni information confidentielle. Nous préparerons avec vous un exemple fictif pour comparer les modèles sur des critères vérifiables et leur coût observé. Les résultats du test vous aideront à faire votre choix.

Le parcours suit : demande → clarification → reformulation → construction du dossier → aperçu et validation du besoin → qualification de la référence → présentation des conditions de campagne → approbations → lancement autorisé → résultats et comparaison. Les appels d’interview, de génération, de correction ou de jugement ont leur propre autorité et leur enveloppe avant consommation, selon les [règles de coût](RULES.md#8-coût-et-bénéfices).

L’agent comprend le travail pour construire son épreuve. Il pose seulement les questions dont la réponse change l’attendu, conserve les besoins et précisions, et distingue hypothèses validées et paramètres fictifs inventés. Il ne simplifie pas silencieusement le travail, ne résout pas l’épreuve dans le prompt candidat et n’adapte pas ce prompt pour favoriser une configuration. Aucun nombre de questions n’est imposé ; l’autorité et le budget bornent la préparation.

Une demande trop large, une référence insuffisante, des outils absents ou une action réelle impossible à reproduire doivent être expliqués. L’agent propose un périmètre évaluable soumis à validation ; sans accord ou sans preuve suffisante, la préparation s’arrête. Elle ne promet pas de rendre toute demande benchmarkable.

L’aperçu présente une situation fictive concrète, les informations importantes, la consigne exacte, les livrables, des critères compréhensibles, les ambiguïtés acceptables et le travail humain restant. « Voir l’exemple » ouvre des pièces effectivement construites et consultables. Le résumé référence le même paquet que celui prévu pour les candidats ; une liste de fichiers annoncés ne suffit pas. La référence réservée au jugement reste séparée ; toute exposition choisie est déclarée avec sa conséquence sur la mesure.

« Modifier cet exemple » accepte une correction libre ou ciblée. Une modification claire est appliquée directement ; une question est posée si son sens ou ses conséquences restent incertains. Les accords non touchés sont conservés, les changements résumés et les pièces, attendus et contrôles affectés revérifiés. Toute modification du paquet présenté entraîne une nouvelle validation. Après gel, une nouvelle version préserve les résultats précédents.

Les critères sont fixés avant approbation du contrat. La présentation du panel, des conditions et des coûts de campagne permet de les examiner avant lancement ; modifier un critère à ce stade fait revenir aux vérifications et validations affectées. Validation du besoin, qualification, approbation du contrat, autorisation de dépense et publication restent distinctes. La préparation et le suivi des résultats sont privés tant que leur publication n’est pas autorisée.

### Exemple pédagogique : Orme & Signal

Orme & Signal est une entreprise entièrement fictive dont la dirigeante veut préparer un tableau par opération, les justificatifs organisés sans doublons, la liste des pièces manquantes et un calendrier de rappels. L’illustration porte sur cinq débits de janvier 2027 : 240 €, 96 €, 96 €, 185 € et 72 €. Elle comprend une facture de fournitures de 240 €, deux factures distinctes Despins de 96 € à la même date dont une copie documentaire, une facture de 185 € d’un fournisseur fictif en Chine avec une note expliquant l’achat d’échantillons, et un justificatif de 72 € manquant.

Le désordre repose sur des pièces dispersées et des noms imparfaits, sans cacher artificiellement une facture. Les deux factures Despins peuvent être rapprochées collectivement des deux paiements, mais aucune référence ne prouve leur attribution individuelle. Cette incertitude acceptable est expliquée à l’utilisateur ; le prompt candidat reçoit les exigences et les pièces utiles, sans le rapprochement résolu réservé au jugement.

Le calendrier illustre un suivi mensuel le 5 pour le mois précédent, un point semestriel à partir du 15 juin 2027 et une préparation 30 jours avant la clôture fictive du 31 décembre 2027. Il ne définit aucune échéance légale et ne certifie aucune comptabilité. Préparer un calendrier ne prouve ni son installation ni une sonnerie sur le téléphone ; des pièces textuelles ne testent pas l’OCR. Le dossier doit préciser la relecture et les adaptations recevables sans appeler une correction de fond une simple relecture.

Cette illustration de conception n’est ni un contrat gelé ni une campagne. Les fichiers évoqués dans l’exemple ne sont pas fournis par cette spécification ; ils devront exister avant un aperçu de tâche validable. Une réussite sur ce cas fictif, même validé, ne prouve pas la réussite sur les dossiers réels de l’utilisateur. Un exemple pédagogique public n’est pas réputé inédit pour les modèles.

### Consulter les résultats

Le catalogue permet de chercher un travail proche de son besoin et d’identifier ses versions et campagnes publiées. La page tâche explique le contexte métier, la famille de travail, le résultat attendu et son usage, les cas, leur charge et leurs difficultés concrètes, la couverture recherchée et les exclusions. Pour une recherche documentaire, elle distingue l’exploitation de textes utiles fournis, la recherche dans une bibliothèque figée et la consultation externe autorisée. Les compétences sollicitées et les limites de reproduction diffèrent ; aucune de ces modalités ne présume un connecteur disponible. Elle distingue l’existence d’une tâche de la présence de résultats approuvés.

La page campagne commence par un rappel bref du contexte et du travail demandé, puis expose la conclusion permise, son périmètre, les cas et tentatives couverts, les dates d’acquisition et sa limite principale. Un lien direct vers cette page conserve l’accès à la tâche et au catalogue. Le lecteur peut choisir une autre campagne de la même tâche en voyant sa version, sa date et ses conditions ; ce changement ne fusionne pas les résultats. Une différence de contrat, de cas, d’environnement ou de base de coût rend la limite de comparaison explicite.

Un tableau de synthèse suit cette conclusion et présente chaque configuration, son verdict et son motif, les mesures prévues, le coût observé et les limites de comparaison. Il propose des tris par critère et des filtres combinés. Une colonne ordonnable possède une mesure, une preuve, une unité ou échelle justifiée et un sens favorable fixés au contrat ; sinon elle reste descriptive. Calculs, complétude, rapprochements et durée sont des possibilités, pas des colonnes obligatoires. Les constats par obligation restent accessibles et filtrables ; une note ou un décompte pour les classer constitue un critère supplémentaire, dans la limite du contrat 0.1.0.

Le tri ordonne les seules valeurs connues et comparables. Les autres forment un groupe « non classables sur ce critère », avec motif et sans rang défavorable. Les égalités restent visibles. Un tri par coût s’annonce comme coût observé, sans libellé « meilleur modèle » ou « meilleur rapport qualité-prix ». Les erreurs restent visibles sur les lignes triées et aucun ordre initial n’est présenté comme une préférence du produit. Le périmètre filtré est explicite ; les statistiques conservent leur population de calcul et leur couverture. La conclusion économique décrit la complétude de la comparaison des coûts et porte `INCOMPLETE` lorsqu’elle est incomplète, selon les [règles de coût](RULES.md#8-coût-et-bénéfices).

Une aide à proximité explique les verdicts, unités et inconnues sans exiger un survol. Le tableau conduit directement au détail de chaque configuration ; sa disposition sur mobile n’est pas imposée. Les dépenses des configurations non admissibles restent visibles. Une couverture partielle ou une preuve insuffisante ne devient pas un échec du modèle.

Le détail des configurations vient après la synthèse. Le lecteur peut examiner ce que chacune a produit et revenir à la comparaison sans perdre le contexte de campagne. La méthode est accessible dès la synthèse, sans lecture préalable obligatoire ni dissimulation des limites pour prolonger la visite. Depuis une conclusion ou un verdict, le lecteur retrouve les constats et les pièces autorisées : entrée du cas, sortie exacte ou extrait identifié, et passage qui soutient l’évaluation. Une pièce restreinte indique ce que le public peut vérifier et ce qui reste inaccessible, selon les [règles de publication](RULES.md#10-restitution). Les limites de représentativité, de variabilité et de jugement accompagnent la conclusion, sans précision statistique inventée. La méthode expose comment la référence a été vérifiée, l’assistance éventuelle de modèles et l’existence ou l’absence d’une revue professionnelle, avec sa phase et son périmètre. Un résultat sur un exercice métier ne vaut pas habilitation professionnelle.

Une durée affichée précise ce qu’elle mesure : exécution, attente ou travail humain. Elle emploie des secondes, minutes ou heures selon l’ordre de grandeur, en gardant la valeur source accessible. Une limite de temps n’est pas une durée observée ; une durée d’exécution ne prouve pas du temps humain économisé. Cette règle de présentation n’impose aucune nouvelle mesure ni critère de classement.

La saisie, l’interview, les aperçus, les corrections, les tris et filtres, la sélection de campagne et l’accès aux preuves doivent fonctionner au clavier, avec un focus visible et des intitulés compréhensibles. Les tableaux gardent leurs en-têtes et leur sens sur petit écran ou avec un texte agrandi. Les verdicts et inconnues restent compréhensibles sans couleur seule. Ces propriétés se vérifient sur le parcours complet, y compris les pièces ouvertes depuis l’aperçu et la comparaison. Un parcours manuel consigné vérifie les actions principales au clavier ; il nomme l’environnement utilisé, les actions et les écarts observés.

## 11. Hors périmètre documentaire

Les choix techniques relèvent de l'ARD. Le backlog et son avancement relèvent de GitHub. Un panel sélectionné n'autorise ni appel, ni retry, ni dépense. Intégration Git, exécution du produit, appels candidats et budget, provisionnement et publication gardent des autorités distinctes.

## 12. Critères d’acceptation produit

| Situation à vérifier | Résultat attendu |
|---|---|
| Le besoin est vague ou non évaluable | questions utiles, reformulation fidèle ; proposition de périmètre soumise à accord ou arrêt motivé |
| L’utilisateur examine ou modifie l’exemple | pièces existantes liées au paquet, limites visibles, accords préservés, contrôles affectés refaits et nouvelle validation |
| Le dossier comporte une ambiguïté ou une pièce manquante | aucune réponse devinée ; alternatives et incertitudes recevables exposées, comme le rapprochement collectif Despins |
| Le livrable prépare une action réelle | aucune installation, sonnerie, automatisation externe ou conformité professionnelle non prouvée n’est annoncée |
| Une saisie contient une information sensible | traitement selon la politique approuvée avant ouverture, sans promesse d’anonymisation parfaite ni publication implicite |
| L’utilisateur valide une référence fausse ou insuffisante | la confirmation du besoin ne remplace pas la qualification ; le contrat ne peut pas être approuvé en l’état |
| Préparateur, juge et candidat partagent un modèle ou fournisseur | lien, ressources exposées, contrôles et limites de jugement visibles |
| Le budget de préparation est épuisé ou un appel reste ambigu | arrêt et coûts connus conservés, sans relance implicite |
| Un critère change avant lancement ou après gel | nouvelle qualification et validations affectées ; nouvelle version après gel, sans réécriture des anciens résultats |
| Une tâche possède plusieurs versions et campagnes | le lecteur choisit une campagne, retrouve sa tâche et distingue les différences qui bornent la comparaison |
| Le lecteur arrive directement sur une campagne | un contexte bref précède la conclusion et le tableau ; l’aide, la méthode et les sorties par configuration sont accessibles, avec retour à la synthèse |
| Des cas diffèrent par leur charge ou leur difficulté | les caractéristiques et la couverture observée sont lisibles ; aucun niveau non testé ni capacité maximale ne sont déduits du seul résultat |
| Une tâche vise un usage métier | le travail réellement mesuré, l’intervention humaine attendue et la qualification de la référence sont visibles, sans compétence professionnelle générale déduite |
| Les preuves permettent une conclusion | chaque verdict conduit à son contrat, ses constats et ses pièces ; observations et évaluation restent distinguées |
| Une campagne est partielle ou indéterminée | couverture prévue et acquise, incidents et inconnues sont lisibles sans succès ni échec inventé |
| Une sortie erronée coûte peu ; des mesures sont égales, manquantes ou incompatibles | verdicts visibles pendant le tri, égalités conservées, valeurs non classables sans rang et comparaison incomplète signalée |
| Un filtre est appliqué après résultat | périmètre affiché, verdicts et population des statistiques inchangés ; aucune nouvelle agrégation implicite |
| Aucune configuration n’est admissible | aucune option désignée comme utilisable ; dépenses et mesures valides restent consultables |
| Une pièce est privée ou du contenu candidat est affiché | la limite de vérification est visible et le parcours ne donne aucun accès privé non autorisé ni exécution active |
| Le parcours est utilisé au clavier ou sur petit écran | saisie, interview, aperçu, correction, campagne, comparaison et preuves restent compréhensibles et accessibles |
| La liste d’essai des assistants ou le score futur est présentée | aucun panel candidat substitué, assistant sélectionné ou score 0.1.0 déduit de cette mention |
| Une préparation ou campagne est terminée sans autorisation de publication | aucune demande, sortie ou pièce privée n’est publiée au catalogue |
| Une démonstration utilise des données synthétiques | cette nature est visible ; elle ne remplace pas les résultats réels autorisés exigés par 0.1.0 |

La validation logicielle utilise des cas contrôlés couvrant ces situations. La validation du lot de résultats cite séparément les campagnes réelles, leurs autorités et leurs preuves. Ni l’une ni l’autre n’autorise à elle seule la publication ou le déploiement.
