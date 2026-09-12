---
style_gate: pass
---

# Glossaire Benchmark Lab-X

Ce glossaire fixe les termes du domaine. Il ne porte ni statut de livraison des versions, ni backlog, ni inventaire d'environnement.

## Objet du benchmark

### Modèle
Objet produit mis en avant. Un verdict sur un modèle reste borné à la configuration dans laquelle il a été observé.

### Accès direct ou API
Mode d’accès déclaré au modèle. Les appels courants du produit utilisent normalement l’API OpenRouter ; le secours par API officielle exige les conditions et autorités distinctes, selon l’[ARD](docs/ARD.md#3-pi-comme-frontière-constante). Les preuves historiques conservent leurs modes d’origine, notamment OAuth ou API directe ; un produit agentique sous abonnement n’est pas pour autant l’objet comparé.

### Configuration demandée
Cible figée du panel de campagne : modèle, fournisseur, accès, route, paramètres et effort requis, avec les identités exactes attendues. Sa présence dans un manifeste ne prouve ni disponibilité ni exécution.

### Profil d’assistant de préparation
Configuration OpenRouter explicite chargée au démarrage pour l’assistance de préparation et de correction. Elle fige l’identité, les paramètres, les routes, le système et les limites, et lie leur empreinte à la configuration demandée. Un alias de compatibilité ou un chemin JSON local désigne un profil déjà approuvé ; leurs valeurs courantes relèvent de la configuration. Ce n’est ni un panel candidat, ni une base de modèles, ni un choix de production.

### Configuration observée
Valeurs établies pour une tentative, avec leurs sources, reliées à la configuration demandée et aux [conditions de test communes](#conditions-de-test-communes). Une valeur absente reste `INCONNU`. Les champs et relations sont définis dans l’[ARD](docs/ARD.md#43-configuration-demandée-et-configuration-observée).
_À éviter_ : modèle seul, solution complète.

### Conditions de test communes
Objet logique unique, déclaré avant le premier candidat et référencé par toutes les configurations comparées : état de Pi, environnement et date de gel. Une condition commune modifiée ouvre une nouvelle comparaison. Les valeurs qui varient par candidat restent dans sa configuration observée.

### Pi
Harnais commun de chaque comparaison. Sa constance rend les comparaisons situées ; elle ne prouve ni sa neutralité ni l'effet causal du modèle isolé.

### État de Pi
Description datée du paquet ou fork, de la version, des paquets ou extensions, des outils, des skills, du contexte et des réglages par défaut de Pi. Chaque valeur porte son statut : déclarée, configurée, active ou observée ; une valeur absente reste `INCONNU`.

### Tâche
Travail précis que le benchmark cherche à faire accomplir, avec un résultat attendu et une décision à éclairer.

### Demande et reformulation
Expression initiale du besoin et précisions de l’utilisateur, puis description du travail comprise par l’assistance. La reformulation distingue besoins, hypothèses validées et paramètres fictifs ; elle ne réduit pas silencieusement le travail. Saisie et conservation suivent la politique approuvée.

### Dossier fictif et aperçu
Ensemble de pièces inventées pour un cas, avec consigne et livrables attendus. L’aperçu résume les informations utiles et ouvre les pièces réellement construites du paquet prévu pour les candidats ; il expose critères, incertitudes acceptables et limites. La référence réservée au jugement reste distincte, sauf exposition décidée et déclarée.

### Version de tâche
Identité immuable reliant un contrat approuvé, ses cas, sa référence de jugement et sa méthode d’évaluation. Son évolution suit les [règles de gel](docs/RULES.md#4-contrat-avant-exécution).

### Métier ou domaine et famille de tâche
Le métier ou domaine situe l’usage ; la famille décrit le travail demandé. Ces repères de navigation peuvent se croiser. Ils ne prouvent ni représentativité ni comparabilité ; leur rôle produit est défini dans le [PRD](docs/PRD.md#31-audience-et-accès).

### Corpus
Ensemble identifié de tâches, de cas ou de ressources, dont le périmètre est précisé : corpus de tâches du catalogue ou corpus documentaire d’un cas. Un corpus fictif décrit la nature des entrées ; une campagne réelle décrit l’acquisition effective des réponses. Les deux sont compatibles.

### Cas d'essai
Entrée identifiée utilisée pour éprouver une tâche, avec les preuves attendues. Un cas n'est pas toute la tâche ; les conclusions indiquent la couverture effectivement observée.

### Variante et répétition
Une variante change des caractéristiques définies d’un cas et en identifie les différences. Une répétition est une nouvelle tentative sur le même cas sous les conditions prévues ; elle ne crée pas une variante. Ni leur nombre ni une généralisation statistique ne sont déduits de leur présence.

### Volume, difficulté et charge
Le volume décrit la quantité d’information ; la difficulté, les contraintes de résolution ; la charge, une quantité de travail dans une unité déclarée. Ces dimensions ne sont pas interchangeables et n’imposent aucun nombre de niveaux.

### Catalogue
Ensemble navigable des tâches, de leurs versions et des campagnes associées.

### Panel
Liste des configurations demandées pour une campagne, figée avec leurs révisions et conditions avant admission. La sélection courante relève de la configuration opérationnelle ; le [PRD](docs/PRD.md#51-périmètre-010) définit les exigences de sélection et de preuve sans fixer de modèles. La liste de modèles à essayer pour l’accueil et la préparation est distincte de ces panels ; elle ne constitue ni un panel candidat de campagne ni une sélection d’assistants déjà qualifiés.

### Campagne
Ensemble organisé sur une version de tâche, des cas, un panel, des conditions communes et des autorisations identifiés. Elle relie plusieurs opérations et leurs preuves. Son état reste distinct de celui d’une Issue, des verdicts et de sa publication.

### Scénario de maquette
Tâche choisie seulement pour rendre un mécanisme compréhensible dans une maquette réversible. Elle n'acquiert aucune autorité sur le benchmark futur.

### Exemple pédagogique
Illustration destinée à expliquer le parcours et les tâches possibles, éventuellement à partir de pièces fictives consultables. Elle n’est pas une preuve de couverture métier. Elle ne devient lançable qu’avec un dossier construit, une qualification, un contrat et les autorisations nécessaires. Ses éventuels résultats simulés ne sont pas des résultats de benchmark et son exposition publique n’est pas réputée inédite.

## Rôles

### Demandeur-lecteur
Personne qui exprime le besoin, valide l’exemple qui le représente et lit la restitution pour décider. Elle n’invente ni seuil, ni métrique, ni méthode de jugement.

### Responsable de campagne
Rôle qui prépare et approuve le contrat de réussite avant les appels candidats, déclare les conditions de test communes, répond de chaque verdict et de la restitution. Les deux rôles peuvent être tenus par la même personne si le besoin le permet. Aucun rôle n’est lié à une personne, un compte, une organisation ou un pseudonyme ; l’affectation publique reste à décider. L’assistance peut aider ce rôle sans s’attribuer son autorité.

## Contrat et verdict

### Validation du besoin, qualification et approbation
La validation confirme que le dossier présenté représente le besoin de l’utilisateur. La qualification vérifie la consigne, les cas, la référence et les contrôles. L’approbation du responsable lie le contrat exact aux preuves de qualification. Ces actes ne se remplacent pas et n’accordent ni autorisation d’appel ou de dépense ni publication.

### Contrat de réussite
Contrat préparé et approuvé avant les appels candidats, reliant le besoin aux critères vérifiables, aux cas, à l’évaluation et à la base de coût. Son contenu normatif est défini dans les [règles](docs/RULES.md#4-contrat-avant-exécution) et renseigné dans le [gabarit](tasks/TEMPLATE.md). La préparation assistée préalable exige son autorité et son budget propres.

### Résultat attendu
Artefact ou état précis que la tâche doit produire pour servir le besoin déclaré.

### Référence de jugement
Éléments justifiant l’attendu d’un cas : faits, sources, calculs, contraintes, alternatives recevables et limites. Sa qualification établit ce que l’évaluation peut soutenir, selon les [règles du contrat](docs/RULES.md#4-contrat-avant-exécution). Elle se distingue de la sortie candidate et d’un exemple unique de bonne réponse.

### Obligation
Condition que la sortie doit respecter et dont la preuve est prévue avant l'exécution.

### Erreur éliminatoire
Défaut défini avant l'exécution qui interdit le verdict `SATISFAIT`, indépendamment du coût ou d'un autre bénéfice.

### Critère secondaire
Propriété complémentaire aux obligations et au coût, prévue au contrat pour comparer les résultats. Une mesure valide peut porter sur une sortie non admissible sans modifier son verdict. Les conditions de mesure, de classement et le plafond de 0.1.0 appartiennent aux [règles](docs/RULES.md#4-contrat-avant-exécution).

### Verdict d'admissibilité
Conclusion d'une configuration selon le contrat de réussite : `SATISFAIT` ou `NE SATISFAIT PAS`. Une évaluation à reprendre n’a pas encore de verdict métier. Un verdict publiable porte sa valeur, un motif court intelligible, les critères ou constats concernés, les références de preuve et son responsable.

### SATISFAIT
Verdict indiquant que la preuve observée respecte le contrat de réussite et ne présente aucune erreur éliminatoire.

### NE SATISFAIT PAS
Verdict indiquant qu'une erreur éliminatoire ou une obligation non remplie est établie.

### INDETERMINE
État de constat, ou valeur d’un verdict historique, indiquant que la preuve disponible ne permet pas de conclure. Le parcours courant l’expose comme travail à reprendre avec une cause et une suite ; aucune nouvelle finalisation propriétaire ne crée ce verdict.

## Preuve et décision

### Exécution du produit
Déroulement identifié d’une opération du produit, avec version du moteur, entrées, autorité et reçu. La préparation peut référencer un brouillon avant qu’une campagne existe ; acquisition, évaluation et restitution référencent leur campagne. Préparer, acquérir, évaluer et restituer sont distincts ; les appels assistés ne deviennent pas des tentatives candidates ni des résultats de benchmark.

### Tentative
Intention d’appel identifiée pour un cas et une configuration demandée. Elle peut aboutir à une sortie, un incident ou des effets inconnus. Une tentative locale ne prouve pas un appel reçu par le fournisseur ; une cellule jamais lancée n’est pas une tentative.

### Acquisition
Opération autorisée visant à obtenir et conserver une sortie. Elle relie les tentatives à leurs observations et reçus, sans prononcer leur verdict. Le cycle de vie est défini dans l’[ARD](docs/ARD.md#123-interfaces-et-cycle-de-vie).

### Observation, évaluation et conclusion
L’observation rapporte ce qui a été obtenu, avec sa provenance. L’évaluation applique le contrat à ces observations. La conclusion relie les évaluations compatibles à la décision permise ; aucune de ces étapes ne remplace sa source.

### Sortie brute
Artefact produit par une configuration avant correction, transformation ou jugement.

### Pièce
Artefact identifié utilisé comme source ou preuve : entrée figée, sortie brute ou extrait identifié. Les métadonnées le décrivent et le relient aux autres objets sans en remplacer le contenu.

### Reçu
Enregistrement d’une opération ou tentative, avec identité, chronologie, effets établis ou inconnus et liens aux pièces. Il peut lui-même être conservé comme pièce ; il ne décide pas du verdict.

### Erreur du harnais
Incident du dispositif de benchmark qui empêche une observation attribuable. Une erreur du harnais n'est pas un échec de la configuration.

### Coût observé
Dépense établie par une source pour les tentatives imputables selon la base de coût du contrat. Elle est distincte du prix affiché, de la prévision, de la réservation et du plafond. Les [règles économiques](docs/RULES.md#8-coût-et-bénéfices) gouvernent les inconnues et la comparaison.

### Bénéfice prévu
Avantage observé sur un critère secondaire défini avant exécution, lorsque les mesures sont comparables. Il ne compense pas une obligation non satisfaite. En 0.1.0, il n’est pas fusionné avec le coût en note unique ; la vision du score personnalisé reste différée.

### Classement par critère et filtre
Un classement ordonne les valeurs connues et comparables d’un critère prévu, sans rang pour les inconnues ou incompatibilités. Un filtre restreint la vue sans modifier le contrat, les verdicts ni la population des statistiques déjà calculées. Aucun de ces outils ne désigne automatiquement une option utilisable.

### Conclusion économique et INCOMPLETE
État de complétude de la comparaison des coûts sur un périmètre identifié : valeurs connues, inconnues et incompatibilités. `INCOMPLETE` indique que cette comparaison est incomplète, sans constituer un verdict ni désigner un gagnant.

### Score pondéré personnalisé
Capacité future de combinaison de critères selon des poids explicites, hors 0.1.0. Sa méthode reste à décider selon le [PRD](docs/PRD.md#52-extensions) ; elle conserve mesures, verdicts et erreurs et ne rend pas une sortie non admissible acceptable grâce au coût. Elle n’établit aucun classement universel.

### Restitution
Présentation reliant tâche et campagne à la conclusion, à la comparaison et aux preuves. Elle peut être locale et privée ; son exposition publique passe par une publication approuvée.

### Publication
Projection explicitement approuvée d'une restitution et de ses preuves publiables. Elle n'expose pas implicitement les pièces privées ni toutes les observations en cours.

### Attribution bornée
Limite selon laquelle le verdict décrit la configuration observée sous les conditions de test communes, sans attribuer au seul modèle un effet que le fournisseur, l'effort, Pi ou ses réglages peuvent influencer.

### Conclusion située
Conclusion bornée à la version de tâche, aux cas et tentatives couverts, à la campagne, au contrat, aux configurations, aux conditions communes, aux preuves et à la date.

### Campagne historique
Campagne conservée sous son identité et son contrat d'origine, sans requalification par les règles actuelles.

### Élément différé
Capacité hors du périmètre de réalisation courant. Elle peut appartenir à la vision durable décidée, comme le score pondéré personnalisé, sans autoriser son implémentation ; son entrée dans une version exige sa méthode, son périmètre et une décision explicites.

## Livraison et exécution agentique

### Version du produit
Numéro SemVer identifiant une livraison du logiciel selon les [règles de versionnement](docs/RULES.md#14-versionnement-du-produit). Il est distinct de la version d’une tâche, d’un schéma de données ou d’un modèle.

### Jalon
Périmètre produit et critères d’acceptation associés à une version cible. Son suivi dans GitHub ne prouve pas à lui seul la livraison ni l’exécution des campagnes.

### Initiative, Epic et Story
Niveaux de la structure de livraison portée par GitHub Issues. La hiérarchie utilise Parent issue et Sub-issues progress ; Status porte l'état de travail.

### Graphe
Structure d'exécution agentique, notamment consommée par Graph Engineering Tool. Ce terme ne désigne pas la hiérarchie Initiative, Epic et Story.
