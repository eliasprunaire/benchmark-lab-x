---
style_gate: pass
---

# Règles de Benchmark Lab-X

Ces règles préservent les contrats historiques et n'autorisent aucune exécution. Elles ne portent aucun statut de livraison des versions.

## 1. Autorité et preuve

**Autorité exacte.** Aucune tâche, contrat, campagne, acquisition, retry, dépense ou publication n'acquiert autorité sans décision explicite qui nomme son périmètre.

**Aucune promotion implicite.** Une Issue fermée, un statut `Done`, une PR verte, un document présent ou un test réussi ne prouve ni satisfaction du contrat, ni autorisation d'exécuter.

**Nature des affirmations.** Distinguer fait prouvé, décision d'Ayo, recommandation et inconnu. Une déduction nomme ses prémisses ; une recommandation ne se présente pas comme une décision. Les états de livraison vivent dans GitHub et les reçus, hors des spécifications. Les statuts d'observation d'une donnée ne sont pas des statuts de livraison.

## 2. Objet produit et unité de preuve

**Modèle mis en avant.** Le produit aide à choisir un modèle pour une tâche précise.

**Configuration observée.** Le modèle est l'identifiant principal présenté ; le verdict porte sur sa configuration observée sous les conditions de test communes, jamais sur le nom du modèle seul.

**Attribution bornée.** Aucune restitution n'attribue au seul modèle un effet que le fournisseur, l'effort, Pi ou ses réglages peuvent influencer, ni n'affirme que le modèle isolé aurait produit le même résultat sous un autre harnais, fournisseur, contexte ou environnement.

**Conclusion située.** Toute conclusion nomme la version de tâche, les cas et tentatives couverts, la campagne, le contrat, les configurations, les conditions communes, les preuves et la date. Une réussite sur un cas ne prouve pas une fiabilité générale.

## 3. Périmètre produit

**Canal API et secours officiel.** OpenRouter reste le canal normal des appels modèles du produit. Après diagnostic et épuisement des routes de secours utilisables du même modèle, l’API officielle peut servir de dernier recours sous configuration et admission distinctes, selon l’[ARD](ARD.md#3-pi-comme-frontière-constante). L’identité exacte disponible, les paramètres, l’accès, le budget et les preuves doivent être établis. Un alias redirigé vers une autre version ne constitue pas un secours. Les contrats historiques restent inchangés.

**Pi obligatoire.** Pi est le harnais commun de chaque comparaison candidate. Son choix n'est pas rouvert par une revue de configuration. Cette contrainte ne choisit pas le transport de l’assistance de préparation ou de jugement.

**Conditions de test communes.** L’objet défini par l’[ARD](ARD.md#42-conditions-de-test-communes) est gelé avant le premier candidat et référencé par toutes les configurations comparées. Une condition commune modifiée ouvre une nouvelle comparaison. Un paramètre propre au candidat ne doit pas être présenté comme une condition partagée.

**Périmètre décidé.** Le périmètre et les exclusions appartiennent au [PRD](PRD.md#5-périmètre-produit). Les modèles et versions sélectionnés relèvent de la configuration opérationnelle ; le manifeste de chaque campagne fige son panel avant admission. Choisir un panel ne prouve ni la disponibilité des configurations ni l’autorisation de les appeler.

## 4. Contrat avant exécution

**Rôles génériques.** Le produit connaît deux rôles : le demandeur-lecteur, qui exprime son besoin, valide l’exemple qui le représente et lit la restitution, et le responsable de campagne, qui prépare et approuve le contrat avant les appels candidats, déclare les conditions communes et répond des verdicts. Les deux rôles peuvent être tenus par la même personne si le besoin le permet. Aucun rôle produit n'est lié à une personne ou à un compte nommé. Les décisions du propriétaire restent distinctes des rôles du produit.

**Préparation et approbation.** Le demandeur-lecteur n’invente ni seuil ni méthode de jugement. Sa validation du besoin reste distincte de la qualification de la référence, de l’approbation du contrat par le responsable, des autorisations d’appel et de dépense et de la publication. L’assistant ne s’attribue aucune de ces autorités. L’approbation de la tâche et celle du manifeste de campagne restent distinctes ; critères, panel et conditions effectivement exécutés doivent avoir été approuvés avant admission. La préparation assistée peut précéder cette approbation, sous ses propres autorités et budget. L’affectation de l’approbateur public reste à décider dans le PRD.

**Fidélité de la préparation.** Conserver la demande et ses précisions, la reformulation, les hypothèses validées et les paramètres fictifs inventés, dans le respect de la politique de saisie et de conservation approuvée. Les pièces du parcours 0.1.0 sont entièrement inventées. Comprendre la tâche permet de construire son protocole ; l’assistant ne réduit pas silencieusement le besoin pour faciliter le jugement. La même consigne et les mêmes ressources sont prévues pour les configurations comparées, sans solution intégrée au prompt candidat ni adaptation pour favoriser un modèle. Une limite d’outil ou de référence conduit à proposer un périmètre évaluable soumis à accord, ou à arrêter la préparation.

**Aperçu et modification.** Le résumé validé référence la consigne, les livrables et les pièces effectivement construites du paquet prévu pour les candidats. Il expose critères, incertitudes acceptables, exclusions et intervention humaine restante. Les éléments réservés au jugement restent séparés. Une modification claire est appliquée sans questionnaire systématique ; une ambiguïté appelle une question ciblée. Préserver les accords non touchés, résumer les changements et revérifier les pièces, la référence et les contrôles affectés. Toute modification du paquet présenté exige une nouvelle validation. Un changement de critère après présentation des conditions de campagne entraîne les mêmes vérifications avant approbation ; après gel, les règles de versionnement s’appliquent.

**Contenu minimal.** Le contrat contient un résultat attendu, des obligations, des erreurs éliminatoires, une référence de jugement, une méthode d’évaluation, les verdicts conclusifs permis et le traitement des preuves insuffisantes. En 0.1.0, il contient au maximum deux critères secondaires complémentaires aux obligations et au coût. Chaque colonne ordonnable possède une mesure, une preuve, une unité ou échelle justifiée, un sens favorable et, si nécessaire, une règle d’agrégation préalable ; sinon elle reste descriptive. Les constats par obligation sont consultables et filtrables ; créer une note ou un décompte pour les classer constitue un critère supplémentaire, sans contourner ce plafond. Le contrat fixe aussi le périmètre d’attribution du coût, les tentatives comptées, l’unité commune et la conversion éventuelle.

**Usage et tolérances.** Chaque obligation justifie son utilité pour le résultat demandé. Le contrat décrit l’intervention humaine qui reste nécessaire et les variations recevables de forme, de contenu ou de méthode de calcul pour chaque critère concerné, dans le respect des exigences de la tâche. Une reformulation correcte ne devient pas une erreur parce qu’elle diffère d’un exemple ; une correction de fond ne devient pas une simple relecture. Aucun seuil de similarité ni tolérance numérique universelle n’est déduit de ces principes.

**Référence de jugement.** La référence relie les attendus aux faits, passages, calculs ou contraintes qui les justifient. Elle prévoit les réponses alternatives recevables, les informations insuffisantes et les désaccords possibles ; un texte idéal ou une liste de sources attendues ne suffit pas à rejeter une solution différente mais étayée. Une source métier précise sa version, sa date, son périmètre d’application et ses droits d’usage ; un référentiel inventé permet un exercice de raisonnement, sans prouver une connaissance du droit, de la santé ou des pratiques professionnelles réels. La consigne expose les exigences nécessaires au travail et les informations normalement accessibles dans l’usage visé. Le contrat distingue les ressources accessibles au candidat des éléments réservés à l’évaluation. Si une référence lui est montrée, cette exposition et ce qu’elle change à la mesure sont déclarés.

**Gel.** Chaque version de tâche identifie son contrat, ses cas, les octets des entrées initiales, la référence de jugement et la méthode d’évaluation. Le contrat approuvé est immuable ; une modification crée une nouvelle version et ne requalifie pas les sorties antérieures. Le contrat fixe l’accès et les preuves à conserver pour une consultation externe ; les résultats effectivement obtenus sont des observations, sans promesse de stabilité des sources. Chaque campagne fige la sélection de cas, le plan des tentatives et les conditions communes avant exécution.

**Agrégation explicite.** La règle fixe la forme et la portée du résultat, les cas et tentatives pris en compte, leur dénominateur et le traitement des manquants, incidents et verdicts `INDETERMINE`. Sans règle préalable, seuls les verdicts par cas et tentative sont permis. Aucune sélection après coup des meilleurs cas ou essais ne peut soutenir une conclusion globale ; les observations exclues et leur motif restent visibles.

**Charge, difficulté et portée.** La charge décrit une quantité de travail dans une unité déclarée ; la difficulté décrit les contraintes du cas qui peuvent rendre sa résolution délicate. Un volume supérieur ne prouve pas à lui seul une difficulté supérieure. Si le contrat emploie des niveaux, il définit leurs dimensions et critères avant exécution, dans le périmètre de la tâche ; aucun nombre de niveaux ni échelle universelle n’est imposé. Une réussite au niveau le plus élevé testé ne prouve ni une capacité maximale ni la réussite de tous les niveaux inférieurs non testés. Un libellé de famille ou de difficulté ne suffit pas à rendre des cas comparables ou à produire des statistiques générales : les règles d’agrégation et de représentativité restent applicables.

**Méthode vérifiable.** Chaque obligation et erreur éliminatoire nomme le contrôle, sa version, ses preuves attendues et le responsable du jugement. Un contrôle automatique ne prouve que les propriétés qu’il sait décider, avec des témoins adaptés. Une évaluation humaine ou assistée conserve ses constats et les décisions requises ; la seule présence d’un commentaire ou d’un nom de rôle ne prouve pas son approbation.

**Qualification de l’épreuve.** Avant l’approbation du contrat, le responsable vérifie la cohérence entre besoin, consigne, cas, référence et contrôles : exactitude des sources et calculs, solution recevable, défauts ciblés et situations ambiguës lorsque le contrat en comporte. Des témoins adaptés vérifient aussi qu’une alternative valable peut être acceptée lorsqu’il en existe, et qu’un défaut pertinent peut être détecté ; leur nombre dépend de ce qui doit être contrôlé. Les preuves et limites de cette qualification restent consultables. Elles référencent l’empreinte du contrat candidat sans entrer dans son calcul ; l’approbation lie ensuite cette même empreinte aux preuves de qualification. Si la préparation change le contrat candidat, la qualification doit porter sur les octets finalement soumis à l’approbation. Une préparation exploratoire sert à corriger l’épreuve ; ses résultats ne deviennent pas implicitement ceux d’une comparaison sous contrat gelé.

**Revue assistée et compétence métier.** Un modèle généraliste ou spécialisé peut proposer des cas, critiquer la référence ou assister le jugement. Son avis doit être confronté aux sources et aux contrôles ; ni son statut de modèle frontière, ni sa spécialisation, ni l’accord de plusieurs modèles ne prouvent la justesse de l’attendu. Consigner la configuration et les consignes utilisées, les sources consultées, les constats, les désaccords et leur arbitrage. Une relecture critique distincte de la rédaction réduit la dépendance au premier avis sans prouver l’indépendance des erreurs des modèles. Lorsqu’un assistant et un candidat partagent un modèle ou un fournisseur, ce lien et le risque d’auto-préférence sont déclarés ; des identités différentes ne prouvent pas l’indépendance. Une revue professionnelle n’est pas obligatoire pour toute tâche : sa présence, sa phase, son périmètre ou son absence sont déclarés. En son absence, limiter les conclusions aux propriétés dont les preuves permettent effectivement le jugement. L’approbation du responsable engage sa décision ; elle ne transforme pas un avis incertain en fait métier ni en certification.

**Aucune métrique hors contrat.** Qualité, stabilité, répétitions ou statistiques ne sont ajoutées que si la tâche les définit avant l’exécution et si un besoin observé les justifie.

**Représentativité et biais.** La conclusion expose pourquoi les cas ont été choisis, les difficultés et populations d’usage non couvertes, la nature synthétique ou réelle des entrées, les conditions d’ordre, de contexte et de jugement susceptibles d’influencer le résultat. Une exposition antérieure possible des données aux modèles ou aux évaluateurs est signalée lorsqu’elle est connue ; son absence de preuve ne démontre pas l’absence de contamination. Le responsable documente les protections retenues et les limites restantes. Répétitions, ordre ou randomisation, aveuglement et protocole statistique restent des choix à approuver, sans nombre ni seuil imposé ici. Sans répétition décidée et observée, aucune stabilité n’est démontrée ; lorsqu’il y en a, les essais individuels et leur variabilité restent visibles selon la règle préenregistrée.

## 5. Sortie et provenance

**Frontière sortante.** Chaque projection possède une version ; le reçu identifie l’empreinte des octets du corps HTTP émis, système compris, avec authentification séparée. La validation du paquet lie le format et le contenu à la version de tâche, sans confirmation supplémentaire par appel. Une reprise conserve les mêmes messages figés ; un changement de paramètres ou de route produit une nouvelle empreinte, un changement de messages exige une nouvelle comparaison. Les anciens reçus restent lisibles et vérifiables, sans réémission ni réutilisation implicite de leurs profils dans un nouveau format. Une annotation historique reste séparée. Le kind du workflow de préparation n’est pas transmis à l’assistant. La génération distingue paquet candidat, notes internes et référence de jugement, sans rôle fourni par le modèle. Les champs limits et human_work ne partent pas au candidat : une contrainte utile qui s’y trouve doit être inscrite explicitement dans une nouvelle consigne/version, sans transfert sémantique automatique. La projection ne censure pas le texte libre et ne garantit pas la détection de toute donnée sensible. Le profil d’assistant de préparation, son empreinte, le modèle, la révision, les paramètres, les routes, les capacités, le relevé, la réserve et les limites doivent coïncider entre le profil figé, l’admission et le transport avant toute émission HTTP ; le chemin hôte du fichier de profil n’entre pas dans ces octets.

**Exposition et portée du fictif.** Préparateur, juge et candidats ont des contextes et ressources distincts ; les liens de modèle ou de fournisseur et les expositions connues restent déclarés. Un exemple pédagogique public n’est pas réputé inédit. La validation d’un cas fictif ne prouve ni sa représentativité ni la réussite sur les dossiers réels de l’utilisateur.

**Sortie brute.** La sortie candidate obtenue est conservée telle quelle, avant correction, transformation ou jugement.

**Demande et observation séparées.** L'identité demandée et l'identité observée restent distinctes. Une observation absente vaut `INCONNU`.

**Reçus reliés.** Demande, observation, tentative, sortie, contrôle, évaluation et conclusion restent distincts et reliés par des identités vérifiables. La provenance précise le producteur, la date, la source et la transformation éventuelle. Une empreinte vérifie des octets ; elle ne prouve ni l’authenticité du producteur, ni l’approbation, ni la justesse du jugement.

**Révision imposée.** Lorsqu'une révision de modèle est exigée, un alias mobile ne la remplace pas sans preuve de correspondance. Une identité non vérifiable bloque son utilisation sous cette identité ; aucune substitution implicite n'est permise.

**Pas de fallback silencieux.** Un changement de modèle, fournisseur, route, paramètres ou effort de raisonnement change la configuration observée ; les valeurs demandées et observées de fournisseur, modèle, route et effort sont relevées par candidat. Une valeur non prouvée reste `INCONNU`. Un changement de Pi, paquet, outil, skill, contexte ou environnement modifie les conditions communes.

## 6. Erreurs et verdict

**Erreurs éliminatoires d'abord.** Une erreur éliminatoire établie interdit `SATISFAIT`, quel que soit le coût.

**Décision et travail restant.** Une nouvelle décision métier officielle porte `SATISFAIT` ou `NE SATISFAIT PAS`. Une évaluation non concluante reste un travail à reprendre, avec causes et prochaine action, sans verdict métier. Les constats intermédiaires et les évaluations historiques peuvent conserver `INDETERMINE` ; leur lecture ne les transforme pas en nouvelles décisions. Aucun niveau de satisfaction partielle n’est déduit d’une preuve manquante.

**Application des verdicts.** Sur une observation intègre et attribuable, une erreur éliminatoire ou une obligation non remplie établie donne `NE SATISFAIT PAS`, même si un autre contrôle manque. `SATISFAIT` exige le résultat et toutes les obligations prouvés, sans erreur éliminatoire. Si l’intégrité, l’attribution ou les preuves nécessaires ne permettent ni réussite ni défaut établi, la finalisation est suspendue : diagnostic d’exécution, rapprochement des effets, relecture ou arbitrage selon la cause. Une panne technique seule ne prouve pas une erreur de contenu ; son éventuel effet sur une obligation de service doit être prévu par le contrat.

**Référence insuffisante.** Une incertitude portant sur l’attendu ou le contrôle se distingue d’une erreur candidate. Avant la comparaison, une obligation ou une erreur éliminatoire sans méthode suffisamment étayée empêche d’approuver la tâche en l’état. Après acquisition, conserver les observations et expliciter la limite : un défaut prouvé indépendamment reste `NE SATISFAIT PAS` ; sinon la finalisation attend la résolution du jugement. Corriger la référence suit les règles de versionnement du contrat, sans réécrire l’histoire. Une erreur de juge se traite sur la sortie conservée ; elle ne déclenche pas un nouvel appel candidat. Une reprise technique doit traiter un incident identifié et conserver chaque tentative, jamais sélectionner des essais jusqu’à obtenir un succès.

**Erreur du harnais séparée.** `HARNESS_ERROR` empêche l'attribution et réduit la couverture. Il ne devient pas automatiquement `NE SATISFAIT PAS`.

**Défaut correctement attribué.** Distinguer erreur candidate et problème de consigne, données, référence, évaluation ou exécution. Une ambiguïté de l’épreuve ne devient pas un échec du modèle ; sa réputation ne justifie pas non plus d’écarter une erreur démontrée. La qualification vérifie les passages, calculs et contrôles avec des preuves adaptées ; un même raisonnement généré ou un consensus IA ne constitue pas la preuve unique de la référence.

**Verdict explicable.** Tout verdict publiable porte sa valeur, un motif court intelligible, les critères ou constats concernés, les références de preuve et son responsable. Les obligations prouvées expliquent `SATISFAIT` ; une erreur éliminatoire ou une obligation non remplie explique `NE SATISFAIT PAS` ; une preuve insuffisante explique le travail de relecture ou de reprise restant, sans verdict métier. Aucune taxonomie exhaustive de motifs ni entrepôt de preuves n'est requis.

## 7. Ordre de décision

L'ordre est obligatoire :

1. erreurs éliminatoires ;
2. obligations et preuve ;
3. verdict d'admissibilité ;
4. aucune configuration `NE SATISFAIT PAS` ou sans décision conclusive désignée comme utilisable ;
5. mesures et coûts observés, avec leurs limites de comparabilité ;
6. classements par critère et filtres de consultation, sans désignation automatique d’une option.

Le coût ne compense jamais une non-admissibilité. Une mesure valide d’une sortie non admissible peut être consultée et classée sur son critère sans changer son verdict. Cet ordre logique n’impose pas la disposition des écrans ni un classement initial préféré par le produit.

**Tris et filtres.** Un tri ordonne seulement les valeurs connues et comparables, selon le critère préalablement défini. Les autres restent dans un groupe non classable, avec motif et sans rang défavorable. Le périmètre filtré est visible ; les filtres ne modifient ni contrat, ni verdicts, ni population d’une statistique déjà calculée. Une nouvelle agrégation exige sa règle propre préalable. Sans cette règle, rester au cas et à la tentative. Aucune comparaison n’est déduite de couvertures ou conditions incompatibles.

## 8. Coût et bénéfices

**Base de coût gelée.** Avant l’exécution, le contrat fixe le périmètre d’attribution, les tentatives comptées, l’unité commune et la conversion éventuelle. Les quantités de travail, cas et règles d’agrégation doivent être comparables : le total d’une couverture réduite ne démontre pas qu’une configuration est moins chère sur le travail complet.

**Coût observable.** Le coût comprend les tentatives imputables selon cette base. Une valeur absente reste `INCONNU` : ni zéro, ni estimation, ni maximum. Seuls les coûts connus et comparables peuvent être ordonnés. Le tri s’annonce comme coût observé, jamais comme meilleur modèle ou meilleur rapport qualité-prix ; les erreurs et verdicts restent visibles.

**Complétude économique.** La conclusion économique décrit le périmètre de comparaison des coûts, les valeurs connues, les inconnues et les incompatibilités, sans désigner d’option. Elle porte `INCOMPLETE` lorsque les données de ce périmètre ne permettent pas une comparaison complète. Les coûts connus restent visibles et un coût inconnu ne retire pas l’admissibilité sur les critères non économiques. `INCOMPLETE` est un état économique distinct du verdict. Si le coût est une obligation figée avant exécution, un coût `INCONNU` interdit de la déclarer satisfaite.

**Préparation et jugement.** Interview, génération du dossier, correction et jugement assisté exigent une autorité d’appel et une enveloppe identifiées avant consommation, éventuellement accordées par l’opérateur autorisé dans son périmètre. Leurs dépenses sont relevées séparément des appels candidats. Le temps humain n’est pas monétisé sans méthode décidée et mesure correspondante. Leur inclusion éventuelle dans une base de coût est déclarée avant comparaison, avec une imputation commune ; elles ne sont ni dissimulées dans le coût candidat ni réputées nulles. L’écran de lancement d’une campagne n’autorise pas rétroactivement les dépenses de préparation.

**Contrôle de dépense.** L’autorisation nomme les tentatives et l’enveloppe. Prévision, réservation avant appel, coût observé et limite du fournisseur restent distincts. Les réservations et dépenses actives sont prises en compte ensemble ; aucune même enveloppe ne peut être allouée deux fois. Un coût manquant ne libère pas une réservation et ne reconstitue pas un solde connu. Un contrôle d’admission ne prouve pas un plafond absolu de facturation. Un coût local n’est pas nul par défaut.

**Indication en préparation.** Après une réponse exploitable de préparation ou correction, le produit peut afficher un coût indicatif calculé avec les tokens utilisés et les tarifs du modèle relevés avant appel, sans exiger le prix exact du fournisseur sélectionné. Cette estimation, clairement distincte d’un débit observé ou d’une facture, ne remplace aucune preuve financière. Des tokens ou tarifs absents restent non estimables. Un coût financier inconnu sur une opération de préparation ou correction reçue ne bloque pas à lui seul les nouveaux échanges autorisés ; sa réserve reste comptée et son reçu inchangé. Les effets actifs ou ambigus et l’enveloppe insuffisante restent bloquants. Les contrats candidats et de jugement déjà figés restent inchangés ; aucun reçu historique n’est requalifié.

**Ensemble admissible.** Sans configuration `SATISFAIT`, aucune option n’est désignée comme utilisable ; dépenses et mesures valides restent consultables et triables sur une base comparable. Une seule configuration admissible peut être décrite comme telle dans le périmètre observé, sans gain comparatif inventé.

**Égalités.** Des valeurs observées égales sur un critère restent une égalité ; aucune heuristique ne les départage. Un arrondi d’affichage ne crée pas une égalité de calcul. Le tri des coûts ne crée pas un objet de recommandation « co-moins-chères ».

**Bénéfice prévu.** Un avantage sur un critère secondaire n’est établi que par une mesure comparable prévue au contrat, avec unité ou échelle justifiée et sens favorable. Sinon l’observation reste descriptive. Une mesure peut porter sur une sortie non admissible ; elle ne compense aucune obligation non satisfaite.

**Dépense visible.** Le coût consommé par une configuration non admissible reste visible comme dépense, sans rendre cette configuration utilisable. Un total auquel manque une dépense nécessaire reste inconnu ; la somme des montants connus est identifiée comme sous-total. Toute dépense manquante peut bloquer l’admission du prochain appel si le budget restant n’est plus établi, y compris pendant la préparation ou le jugement.

**Score différé.** En 0.1.0, admissibilité, coût et critères secondaires ne sont ni moyennés, ni pondérés, ni fusionnés en note unique. Le score pondéré personnalisé appartient à la vision du [PRD](PRD.md#52-extensions) ; sa méthode exige une décision avant réalisation. Il conserve les mesures d’origine, verdicts et erreurs et ne permet pas au coût de rendre acceptable une sortie non admissible. Aucun meilleur modèle absolu, podium général ou classement universel n’est produit, y compris à terme.

## 9. Incidents et inconnues

**Causalité prouvée.** Un incident n'est attribué au fournisseur, au modèle ou à Pi que si le reçu ou l'environnement autorisé l'établit.

**Valeurs littérales.** `INCONNU`, `INDETERMINE` et `HARNESS_ERROR` ne sont remplacés ni par zéro, ni par moyenne, ni par estimation.

**Couverture visible.** Toute conclusion indique les cas, configurations et tentatives prévus, observés ou manquants. Une campagne partielle conserve les preuves acquises ; une cellule manquante n’est pas un échec attribué au modèle. L’agrégation ne conclut que sur la couverture permise par le contrat. La consultation d’une autre campagne n’autorise aucune fusion de cas ou comparaison de totaux incompatibles.

**Admission avant appel.** L’exécuteur vérifie les identités et empreintes gelées, la disponibilité du canal exact, l’environnement exigé, le stockage des preuves, les autorités et le budget restant. Une inconnue requise, une dérive ou une tentative active ou ambiguë empêchant cette admission bloque le nouvel appel. Un champ non observable que le contrat n’exige pas comme condition d’admission reste explicitement `INCONNU`, avec sa limite d’attribution.

**Reprise sans replay implicite.** Une intention d’appel doit être enregistrée avant émission. Après interruption, l’exécuteur rapproche intentions, reçus et dépenses avant d’admettre une cellule encore autorisée. Une tentative partie aux effets inconnus reste ambiguë ; ni redémarrage, restauration ni déploiement n’autorise son rejeu. Une nouvelle tentative exige une identité propre et l’autorité correspondante, sans effacer la précédente. LENGTH, EMPTY_OUTPUT et ROUTE_ERROR peuvent proposer une nouvelle campagne. Lorsque l’admission propriétaire initiale porte une préautorisation fermée de reprise technique, avec les capacités, routes, limites et tarifs figés avant le premier appel, cette reprise s’exécute sans nouvelle intervention. Sans cette préautorisation, la proposition n’est pas lancée. Un arrêt de la campagne source ferme aussi l’admission des descendants de reprise déjà créés dans cette chaîne, sans modifier leurs manifestes, intentions ni reçus. CONTENT_REFUSAL est terminal. Un profil de transport décrit une réponse COMPLETE, jamais un verdict de tâche ; il se recherche par l’identité exacte provider, model, revision, access, channel_id et format sortant. Le détail d’exploitation restant est à décider dans le contrat d’exploitation.

**Appels assistés.** Les règles d’admission, de réservation, d’interruption et de reprise s’appliquent aussi aux appels de préparation et de jugement. Une enveloppe épuisée ou un effet d’appel ambigu bloque les nouveaux appels dépendants ; l’état et les coûts connus sont conservés. Ni une nouvelle question ni une correction de dossier ne créent une autorité de dépense.

## 10. Restitution

**Lecture publique.** Le parcours et ses critères d’accessibilité sont définis par le [PRD](PRD.md#10-restitution-publique). L’ordre de calcul reste celui de la section 7, quel que soit l’agencement des écrans.

**Minimum accessible.** La restitution contient la tâche, le contrat, les configurations, les conditions de test communes, les verdicts et leurs motifs, les mesures prévues, les coûts observés et la complétude de leur comparaison, les incidents, les inconnues, les limites et les preuves nécessaires. Contenu présent ne signifie pas contenu affiché d'emblée.

**Aucun visuel trompeur.** Aucun graphique ou classement n’implique une échelle ou une précision absentes du contrat. Aucun score combiné n’est produit en 0.1.0. L’interdiction de podium général ou de classement universel reste durable.

**Preuves accessibles.** Une pièce publiée relie l’entrée, la sortie et les passages justifiant le verdict. La sortie exacte reste privée tant que sa publication n’est pas autorisée ; un extrait, masquage ou résumé publié est identifié comme dérivé, avec son lien à la source. Une empreinte ne remplace pas une pièce accessible. La restriction et son effet sur la vérification publique sont signalés. Un scénario de maquette ne devient pas implicitement une tâche du catalogue.

**Publication explicite.** L’approbation lie les octets de la projection et les pièces publiables à leur périmètre. Elle ne publie pas les données privées acquises ensuite. Une modification de la projection exige l’autorité correspondante et conserve la traçabilité de la version remplacée. Une page locale, une CI verte ou une PR ouverte ne constitue pas une publication officielle. Intégration Git, exécution produit, appels candidats et budget, provisionnement et publication gardent des autorités distinctes.

## 11. KISS et évolution

**Règle KISS.** Une complexité entre seulement lorsqu'une itération antérieure démontre le besoin qu'elle résout.

**Résultat suffisant.** Livrer les capacités nécessaires au catalogue et aux campagnes décidés. Une démonstration du moteur ne remplace pas les résultats réels attendus.

**Pas d'anticipation.** Réutiliser les primitives retenues dans l’ARD. Aucun microservice, Kubernetes, bus de messages, système de plugins, moteur de score pondéré ou abstraction spéculative n’est ajouté sans besoin démontré. Le parcours public et les tris et filtres de 0.1.0 sont décidés ; ils n’autorisent pas une gestion de comptes, une politique de publication ou une formule de score encore non choisies.

**Évolution traçable.** Lorsqu'un besoin est observé, l'itération suivante nomme la preuve, la complexité ajoutée et la condition de retrait ou de révision.

## 12. Histoire

**Campagnes immuables.** Les campagnes, preuves et reçus historiques gardent leur identité, leur sémantique et leurs verdicts d'origine. Leur historique documentaire appartient à Git.

**Aucune requalification rétrospective.** Les campagnes et prototypes historiques conservent leurs contrats et conclusions d’origine. La spécification courante ne crée pour ces campagnes aucune qualification, baseline, mesure ou recommandation absente de leur contrat et de leurs preuves.

**Preuve technique bornée.** Un `PASS` de témoin, transport, qualification, verrou ou préparation prouve seulement son objet technique.

**Artefacts historiques non normatifs.** Les générateurs et restitutions historiques restent sous leurs contrats d'origine. Leurs anciennes références ne sont pas remappées implicitement et leur vocabulaire ne remplace pas la spécification courante.

## 13. Arrêt

À l'épuisement de l'autorité ou en présence d'une preuve bloquante, la tranche s'arrête en `HOLD` sans retry, fallback, dépense ou extension implicite.

## 14. Versionnement du produit

**Version unique.** Benchmark Lab-X adopte [Semantic Versioning 2.0.0](https://semver.org/lang/fr/) sous la forme `MAJOR.MINOR.PATCH`. Une version identifie le produit du monorepo, frontend et backend ensemble. Les versions de Pi, de Graph Engineering Tool, de l’infrastructure, des modèles, des tâches et des schémas de données restent distinctes.

**Jalon initial.** `0.1.0` désigne le périmètre approuvé dans le [PRD](PRD.md#51-périmètre-010), avec ses critères d’acceptation. Ce numéro ne requalifie aucun prototype ni résultat historique et ne prouve aucune livraison. Aucun autre jalon chiffré n’est déduit de cette décision.

**Compatibilité publique.** Le contrat de compatibilité couvre les commandes, options et codes de sortie documentés, les interfaces publiques documentées et les formats de données exposés. Les détails internes ne constituent pas une interface publique. Une modification de schéma possède sa propre identité et explicite les lecteurs compatibles, la migration éventuelle et ses limites ; le numéro du produit ne remplace pas cette information.

**Avant 1.0.0.** La version majeure zéro indique un développement initial. Pour ce projet, un correctif compatible incrémente `PATCH` ; une fonctionnalité ou une rupture du contrat public incrémente `MINOR` et remet `PATCH` à zéro. Une rupture doit être documentée, même sous zéro. À partir de `1.0.0`, une rupture incrémente `MAJOR`, une fonctionnalité compatible `MINOR`, et un correctif compatible `PATCH`, selon SemVer. Un éventuel suffixe de préversion qualifie une version précise et exige une décision de livraison ; il ne crée pas un jalon concurrent.

**Identification d’une livraison.** Une version publiée est reliée à un commit et à des artefacts identifiés, dont le contenu ne change plus sous ce numéro. Un checkout sans version publiée s’identifie par son commit et ses modifications locales ; il ne s’annonce pas automatiquement comme la version cible. Le périmètre et les critères vivent dans le PRD, l’avancement dans GitHub, et la preuve livrée dans les artefacts et reçus. Choisir un numéro n’autorise ni tag, ni release, ni déploiement, ni publication.

**Historique et migration.** La documentation courante emploie les numéros de produit décidés et des noms techniques sans phase de livraison. Les identifiants présents dans les contrats et preuves scellés restent exacts. Leur reconnaissance explicite par un lecteur compatible n’autorise ni réécriture des preuves ni reprise d’une ancienne acquisition ; une nouvelle préparation et ses autorités restent nécessaires.
