---
style_gate: pass
---

# Glossaire Evaly

Ce glossaire fixe les termes du domaine. Il ne porte ni statut de livraison des versions, ni backlog, ni inventaire d'environnement.

## Objet du benchmark

### Modèle
Objet produit mis en avant. Un verdict sur un modèle reste borné à la configuration dans laquelle il a été observé.

### Accès direct ou API
Mode d’accès déclaré au modèle. Les appels courants du produit utilisent normalement l’API OpenRouter ; le secours par API officielle exige les conditions et autorités distinctes, selon l’ARD. Un produit agentique sous abonnement n’est pas pour autant l’objet comparé.

### Configuration demandée
Cible figée du panel de campagne : modèle, fournisseur, accès, route, paramètres et effort requis, avec les identités exactes attendues. Sa présence dans un manifeste ne prouve ni disponibilité ni exécution.

### Profil d’assistant de préparation
Configuration OpenRouter explicite chargée au démarrage pour l’assistance de préparation et de correction. Elle fige l’identité, les paramètres, les routes, le système et les limites, et lie leur empreinte à la configuration demandée. Un alias de compatibilité ou un chemin JSON local désigne un profil déjà approuvé ; leurs valeurs courantes relèvent de la configuration. Ce n’est ni un panel candidat, ni une base de modèles, ni un choix de production.

### Configuration observée
Valeurs établies pour une tentative, avec leurs sources, reliées à la configuration demandée et aux [conditions de test communes](#conditions-de-test-communes). Une valeur absente reste `INCONNU`. Les champs et relations sont définis dans l’ARD.
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
Identité immuable reliant un contrat approuvé, ses cas, sa référence de jugement et sa méthode d’évaluation. Son évolution suit les règles de gel.

### Métier ou domaine et famille de tâche
Le métier ou domaine situe l’usage ; la famille décrit le travail demandé. Ces repères de navigation peuvent se croiser. Ils ne prouvent ni représentativité ni comparabilité ; leur rôle produit est défini dans le PRD.

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
Liste des configurations demandées pour une campagne, figée avec leurs révisions et conditions avant admission. La sélection courante relève de la configuration opérationnelle ; le PRD définit les exigences de sélection et de preuve sans fixer de modèles. La liste de modèles à essayer pour l’accueil et la préparation est distincte de ces panels ; elle ne constitue ni un panel candidat de campagne ni une sélection d’assistants déjà qualifiés.

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
Contrat préparé et approuvé avant les appels candidats, reliant le besoin aux critères vérifiables, aux cas, à l’évaluation et à la base de coût. Son contenu normatif est défini dans les règles et renseigné dans le [gabarit](docs/task-template.md). La préparation assistée préalable exige son autorité et son budget propres.

### Résultat attendu
Artefact ou état précis que la tâche doit produire pour servir le besoin déclaré.

### Référence de jugement
Éléments justifiant l’attendu d’un cas : faits, sources, calculs, contraintes, alternatives recevables et limites. Sa qualification établit ce que l’évaluation peut soutenir, selon les règles du contrat. Elle se distingue de la sortie candidate et d’un exemple unique de bonne réponse.

### Obligation
Condition que la sortie doit respecter et dont la preuve est prévue avant l'exécution.

### Erreur éliminatoire
Défaut défini avant l'exécution qui interdit le verdict `SATISFAIT`, indépendamment du coût ou d'un autre bénéfice.

### Critère secondaire
Propriété complémentaire aux obligations et au coût, prévue au contrat pour comparer les résultats. Une mesure valide peut porter sur une sortie non admissible sans modifier son verdict. Les conditions de mesure, de classement et le plafond des critères secondaires appartiennent aux règles.

### Verdict d'admissibilité
Conclusion d'une configuration selon le contrat de réussite : `SATISFAIT` ou `NE SATISFAIT PAS`. Une évaluation à reprendre n’a pas encore de verdict métier. Un verdict publiable porte sa valeur, un motif court intelligible, les critères ou constats concernés, les références de preuve et son responsable.

### SATISFAIT
Verdict indiquant que la preuve observée respecte le contrat de réussite et ne présente aucune erreur éliminatoire.

### NE SATISFAIT PAS
Verdict indiquant qu'une erreur éliminatoire ou une obligation non remplie est établie.

### INDETERMINE
État de constat, ou valeur déjà enregistrée dans un reçu compatible, indiquant que la preuve disponible ne permet pas de conclure. Le parcours courant l’expose comme travail à reprendre avec une cause et une suite ; aucune nouvelle finalisation propriétaire ne crée ce verdict.

## Preuve et décision

### Exécution du produit
Déroulement identifié d’une opération du produit, avec version du moteur, entrées, autorité et reçu. La préparation peut référencer un brouillon avant qu’une campagne existe ; acquisition, évaluation et restitution référencent leur campagne. Préparer, acquérir, évaluer et restituer sont distincts ; les appels assistés ne deviennent pas des tentatives candidates ni des résultats de benchmark.

### Tentative
Intention d’appel identifiée pour un cas et une configuration demandée. Elle peut aboutir à une sortie, un incident ou des effets inconnus. Une tentative locale ne prouve pas un appel reçu par le fournisseur ; une cellule jamais lancée n’est pas une tentative.

### Acquisition
Opération autorisée visant à obtenir et conserver une sortie. Elle relie les tentatives à leurs observations et reçus, sans prononcer leur verdict. Le cycle de vie est défini dans l’ARD.

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
Dépense établie par une source pour les tentatives imputables selon la base de coût du contrat. Elle est distincte du prix affiché, de la prévision, de la réservation et du plafond. Les règles économiques gouvernent les inconnues et la comparaison.

### Bénéfice prévu
Avantage observé sur un critère secondaire défini avant exécution, lorsque les mesures sont comparables. Il ne compense pas une obligation non satisfaite. Tant que la méthode du score pondéré n’est pas décidée, il n’est pas fusionné avec le coût en note unique ; la vision du score personnalisé reste différée.

### Classement par critère et filtre
Un classement ordonne les valeurs connues et comparables d’un critère prévu, sans rang pour les inconnues ou incompatibilités. Un filtre restreint la vue sans modifier le contrat, les verdicts ni la population des statistiques déjà calculées. Aucun de ces outils ne désigne automatiquement une option utilisable.

### Conclusion économique et INCOMPLETE
État de complétude de la comparaison des coûts sur un périmètre identifié : valeurs connues, inconnues et incompatibilités. `INCOMPLETE` indique que cette comparaison est incomplète, sans constituer un verdict ni désigner un gagnant.

### Score pondéré personnalisé
Capacité future de combinaison de critères selon des poids explicites. Sa méthode reste à décider selon le PRD ; elle conserve mesures, verdicts et erreurs et ne rend pas une sortie non admissible acceptable grâce au coût. Elle n’établit aucun classement universel.

### Restitution
Présentation reliant tâche et campagne à la conclusion, à la comparaison et aux preuves. Elle peut être locale et privée ; son exposition publique passe par une publication approuvée.

### Publication
Projection explicitement approuvée d'une restitution et de ses preuves publiables. Elle n'expose pas implicitement les pièces privées ni toutes les observations en cours.

### Attribution bornée
Limite selon laquelle le verdict décrit la configuration observée sous les conditions de test communes, sans attribuer au seul modèle un effet que le fournisseur, l'effort, Pi ou ses réglages peuvent influencer.

### Conclusion située
Conclusion bornée à la version de tâche, aux cas et tentatives couverts, à la campagne, au contrat, aux configurations, aux conditions communes, aux preuves et à la date.

### Élément différé
Capacité hors du périmètre de réalisation courant. Elle peut appartenir à la vision durable décidée, comme le score pondéré personnalisé, sans autoriser son implémentation ; son entrée dans une version exige sa méthode, son périmètre et une décision explicites.

## Livraison et exécution agentique

### Version du produit
Numéro SemVer identifiant une livraison du logiciel selon les règles de versionnement. Il est distinct de la version d’une tâche, d’un schéma de données ou d’un modèle.

### Jalon
Périmètre produit et critères d’acceptation associés à une version cible. Son suivi dans GitHub ne prouve pas à lui seul la livraison ni l’exécution des campagnes.

### Initiative, Epic et Story
Niveaux de la structure de livraison portée par GitHub Issues. La hiérarchie utilise Parent issue et Sub-issues progress ; Status porte l'état de travail.

### Graphe
Structure d'exécution agentique, notamment consommée par Graph Engineering Tool. Ce terme ne désigne pas la hiérarchie Initiative, Epic et Story.

## Vocabulaire affiché

Cette section relie chaque terme affiché à l’écran à son terme du domaine et à son nom technique. Les sections précédentes gardent les termes du domaine ; l’écran emploie le terme affiché. Le produit se présente comme « Evaly, le benchmark personnalisé » : un benchmark rapproché d’un cas d’usage précis, dans des conditions communes, avec preuves et coût observé. Aucun terme affiché ne promet un « meilleur modèle » en général ni un classement. Les chemins d’URL et les noms techniques ne changent pas avec le vocabulaire affiché.

| Terme affiché | Terme du domaine | Nom technique | Règle |
|---|---|---|---|
| cas d’usage | Tâche | dossier, `dossier_id`, `s2_dossiers` | Le travail de l’utilisateur. Jamais « tâche » ni « dossier » à l’écran. |
| exemple | Cas d’essai, Dossier fictif et aperçu | package, revision, `case_of` | Le dossier inventé qui représente le cas. « Exemple 1, 2 » pour plusieurs cas d’essai ; « Version N » pour une révision. |
| benchmark | Campagne ; les canons disent aussi « comparaison » | campaign, `campaign_id`, `s4_campaigns` | Le lancement de l’utilisateur sur son exemple. Employé seul, « benchmark » désigne toujours celui de l’utilisateur. |
| benchmarks publics | Répertoire d’évaluations externes | references, `BENCHMARK_REFERENCES`, `/evaluations-externes` | Les références publiées par des tiers. Toujours qualifié : « publics », « publiés par d’autres », ou le nom propre. « Évaluation externe » reste un terme descriptif du domaine, jamais un libellé. |
| Résultats | Restitution, vue de décision | kind `comparison` | Page des verdicts, qualité observée et coût observé d’un benchmark. Le tableau garde son titre « Comparaison des modèles ». |
| modèle | Configuration demandée et observée | panel, model, slug | Nom commercial à l’écran. |
| verdict | Verdict d’admissibilité | `SATISFAIT`, `NE SATISFAIT PAS` | Tampons « Satisfait », « Ne satisfait pas », « À reprendre », « Non vérifié », « Inconnu ». |
| Mes cas d’usage | aucun | `/preparation` | La page qui décrit un cas et porte la clé OpenRouter. |
| Mes données | « Mes données » | `/preparation/data`, kind `privacy_data` | Cas d’usage ouverts, historique local et conservation automatique. |
| Ma clé OpenRouter | Accès OpenRouter personnel | `/preparation/access`, kind `access` | Nom de la page. Actions : « Ajouter ma clé », « Remplacer ma clé », « Retirer la clé de ce navigateur ». |
| épreuve | Qualification de l’épreuve ; épreuve éditoriale publiée | editorial | Sens des règles §4. Le sens public est hors alpha ; les copies existantes affichent « Exemple source ». Voir l’entrée Épreuve. |
| exemple source | Copie d’un exemple publié | `source`, `checks_json.source` | Dossier créé avant l’alpha depuis une épreuve éditoriale. |
| contribution | Contribution facultative | contributions, `benchmark_contributions` | Fermée. Retrait seul, jusqu’à la dernière échéance. Aucune nouvelle utilisation. |
| historique local | Historique personnel | IndexedDB, `privacy.js` | Copies gardées par le navigateur. |

Un lien porte le nom de sa page de destination ; un lien de retour s’écrit « Revenir à » suivi du nom de la page, par exemple « Revenir à mes cas d’usage ». Mots interdits à l’écran : « tâche » pour le travail de l’utilisateur, « Bench-X », « meilleur modèle », « classement », « benchmark » nu pour un tiers. Les pages légales gardent leur texte ; seuls leurs liens suivent le nom des pages.

### Cas d’usage
Terme affiché de la Tâche : le travail précis de l’utilisateur. L’appel principal est « Décrire mon cas d’usage ». La page `/preparation` s’appelle « Mes cas d’usage ».

### Exemple
Terme affiché du dossier fictif qui représente le cas d’usage. « Exemple 1, 2 » numérote les cas d’essai ; « Version N » désigne une révision de l’exemple.

### Benchmark
Terme affiché de la Campagne : le lancement de l’utilisateur sur son exemple. Le benchmark se lance, se suit et se termine. Il ne désigne aucun gagnant : les verdicts et le coût observé restent par modèle et par exemple.

### Benchmarks publics
Terme affiché du répertoire d’évaluations externes : les références publiées par des tiers, comme SWE-bench ou MTEB. Les benchmarks publics mesurent surtout des tâches génériques. Ils ne prouvent pas la réussite sur le cas d’usage précis d’un utilisateur ; Evaly rapproche le benchmark de ce cas d’usage. Aucun texte affiché ne dit que les benchmarks publics sont déconnectés de la vraie vie, et aucun ne promet une réussite générale. Les indices composites et les classements, comme l’indice d’Artificial Analysis ou Arena (anciennement LMArena), ne sont pas des benchmarks publics au sens du répertoire : ils compilent des résultats, ils ne mesurent pas un travail.

### Épreuve
Le terme a deux sens. Dans le parcours public, une épreuve est une épreuve éditoriale publiée ; ce sens est hors alpha. Dans les règles, la qualification de l’épreuve vérifie la cohérence entre besoin, consigne, cas, référence et contrôles avant l’approbation du contrat.

### Contribution
Copie privée et distincte d’un exemple et de ses résultats, que l’utilisateur autorisait Evaly à conserver pour améliorer le service, sans publication. Fermée : aucune nouvelle contribution n’est enregistrée. Retrait seul, jusqu’à la dernière échéance : le consentement portait sur une version de l’exemple et échoit après six mois calendaires. Aucune nouvelle utilisation.

### Historique local
Copies complètes des cas gardées par le navigateur, pour relecture et export. Le navigateur peut les effacer ; ce n’est pas une sauvegarde garantie. Toute personne qui utilise le même profil de navigateur peut les lire. Actif par défaut ; l’utilisateur peut l’arrêter ou l’effacer.

### Mes données
Page `/preparation/data` : cas d’usage ouverts, historique local et conservation automatique. Elle se distingue de « Mes cas d’usage », la page `/preparation` qui décrit un cas.
