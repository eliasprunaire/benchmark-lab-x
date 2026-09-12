---
style_gate: pass
---

# Carte de tâche : `<nom lisible>`

Ce gabarit prépare le contrat d’une tâche de benchmark, distinct d’une Story de livraison. Les sections 1 à 4 définissent le contrat à geler. L’empreinte porte sur le contenu contractuel identifié, sans sa propre valeur ni les enregistrements marqués hors empreinte. Les sections 5 à 7 décrivent le manifeste de campagne et les enregistrements d’exploitation qui lui sont liés ; les sections 8 et 9 concernent les résultats et la publication. Les observations et autorisations acquises après le gel ne réécrivent ni le contrat ni le manifeste.

Le suivi GitHub reste extérieur à la carte : état de l’Issue, `Status` du Project et progrès des sous-Issues ne décrivent pas l’exécution d’une campagne. Une carte approuvée n’autorise ni acquisition, ni dépense, ni publication.

Toute extension suit la [règle KISS](../docs/RULES.md#11-kiss-et-évolution).

## 1. Identité et autorité

| Champ | Valeur |
|---|---|
| Identifiant stable de tâche | `<slug décidé>` |
| Version de tâche | `<identité et empreinte du contrat, des cas, de la référence de jugement et de la méthode d’évaluation>` |
| Tâche | `<travail précis>` |
| Métier ou domaine ; famille de tâche | `<contexte d’usage ; travail demandé, sans comparabilité implicite>` |
| Titre public | `<titre lisible de la tâche, repris tel quel par la restitution>` |
| Demandeur-lecteur | `<besoin exprimé par ce rôle>` |
| Responsable de campagne | `<rôle et référence de responsabilité vérifiable ; identité privée si nécessaire>` |
| Date de préparation | `<date>` |

Approbation du responsable de campagne avant appels candidats, hors empreinte du contrat : `EN_ATTENTE` / `<preuve et date, référençant l’empreinte du contrat et les preuves de qualification>`. Cette preuve est liée au contrat sans entrer dans l’empreinte qu’elle approuve.

Une approbation `EN_ATTENTE` interdit les appels candidats. Le brouillon peut être préparé avec assistance sous autorité et budget propres. Le demandeur-lecteur valide le besoin représenté ; le responsable de campagne prépare et approuve le contrat. Les deux rôles peuvent être tenus par la même personne, sans que la validation du besoin remplace qualification, approbation, dépense ou publication.

## 2. Besoin et résultat attendu

### Situation

`<acteur, contexte et besoin>`

Demande et précisions : `<expression initiale et réponses utiles, sous la politique de saisie et de conservation approuvée ; aucune donnée personnelle ou confidentielle>`.

Reformulation : `<travail compris, sans réduction silencieuse du besoin ni solution intégrée à la consigne>`.

Hypothèses validées et paramètres fictifs : `<distinguer les accords sur le besoin des personnes, organismes, faits et pièces inventés>`.

Limites de préparation : `<outil absent, travail non évaluable ou référence insuffisante ; périmètre alternatif accepté ou motif d’arrêt>`. Aucun nombre fixe de questions n’est déduit du gabarit.

### Résultat attendu

`<artefact ou état précis qui sert le besoin ; propriétés réellement mesurées et propriétés non évaluées>`

Usage du résultat et intervention humaine : `<ce que le destinataire peut en faire ; relecture, adaptations ou corrections nécessaires admises par le contrat>`.

### Décision éclairée

`<choix entre des modèles sur cette tâche précise>`

### Conclusion permise

`<conclusion bornée à la version, aux cas et tentatives couverts, à la campagne, aux configurations, aux conditions communes et à la date>`

### Conclusions interdites

- meilleur modèle absolu
- classement universel
- podium général ou graphique trompeur
- effet causal du modèle isolé, ou effet attribué au seul modèle alors que le fournisseur, l'effort, Pi ou ses réglages peuvent l'influencer
- conclusion hors de la tâche ou du contrat
- requalification des campagnes historiques

## 3. Entrées et sortie brute

### Cas d'essai

| Cas | Entrée exacte et provenance | Identité ou empreinte | Charge et difficulté décrites | Preuves attendues |
|---|---|---|---|---|
| `<id>` | `<texte ou référence>` | `<identité>` | `<quantité et unité pertinentes ; contraintes concrètes et motif du choix>` | `<références>` |

Niveau éventuel : `<définition et dimensions approuvées avant exécution, ou NON DÉFINI>`. Décrire les caractéristiques qui varient entre cas et celles qui restent communes, selon les [règles de charge et de portée](../docs/RULES.md#4-contrat-avant-exécution). Une étiquette ne remplace pas cette description.

Couverture et limites : `<motif de sélection, usages couverts et exclus, nature entièrement fictive du dossier 0.1.0, biais connus et limites de généralisation>`. Un cas validé ne prouve pas la réussite sur les dossiers réels de l’utilisateur. Un exemple pédagogique public n’est pas réputé inédit.

Règle d’agrégation : `<forme et portée du résultat ; cas et tentatives pris en compte, dénominateur, traitement des manquants, incidents et INDETERMINE ; ou AUCUNE : verdicts par cas et tentative seulement>`.

### Entrées et outils autorisés

Format de contenu sortant : `<version de projection liée au paquet validé>`. Déclarer les fichiers candidats par manifeste fermé avec rôles `instructions` et `input` ; aucune découverte automatique de Markdown. Conserver méthode, statuts, historique et pièces de jugement hors des messages candidats. La génération de l’épreuve sépare le contenu candidat, les notes internes et la référence de jugement, sans rôle fourni par le modèle. Toute contrainte nécessaire issue de notes internes est rédigée explicitement dans la consigne de cette version. Après émission, conserver séparément l’empreinte du corps HTTP réel, système inclus, sans authentification.

Consigne exacte commune aux candidats : `<texte ou pièce identifiée, sans réponse attendue réservée au jugement>`.

Aperçu présenté : `<situation fictive, informations importantes, livrables, critères compréhensibles, incertitudes recevables, exclusions et intervention humaine ; références aux pièces existantes et au même paquet prévu pour les candidats>`.

Validation du besoin, hors empreinte : `EN_ATTENTE` / `<preuve datée liée à l’identité et à l’empreinte du paquet présenté>`. Les pièces de « Voir l’exemple » sont consultables ; la référence de jugement reste séparée sauf exposition explicitement décidée et déclarée.

Modifications, hors empreinte : `<changements demandés, accords conservés, question ciblée si nécessaire, contrôles affectés refaits et nouvelle validation du paquet>`. Toute modification du paquet présenté exige cette validation ; après gel, nouvelle version sans réécriture des résultats.

| Élément | Rôle | Visible au candidat | Identité ou empreinte |
|---|---|:---:|---|
| `<entrée ou outil>` | `<rôle>` | oui / non | `<version, SHA-256 ou INCONNU>` |

Modalité documentaire, si pertinente : `<textes utiles fournis, recherche dans une bibliothèque figée ou consultation externe autorisée ; corpus, versions et droits ; preuves prévues des requêtes et pièces consultées>`.

Tout élément non listé est indisponible. Le parcours 0.1.0 ne comprend aucun téléversement de dossier réel, accès à l’ordinateur, connecteur vers les données de l’utilisateur ou action sur son téléphone. Aucun secret n’est fourni au candidat. Préparer un calendrier ne prouve aucune notification réelle ; fournir du texte ne mesure pas l’OCR.

### Sortie brute attendue

`<artefact, encodage et emplacement attendus>`

La sortie brute est conservée avant contrôle ou jugement. Aucun post-traitement silencieux n'est permis.

## 4. Contrat de réussite

### Obligations

| ID | Obligation | Preuve attendue |
|---|---|---|
| `O1` | `<condition nécessaire à l’usage ; motif et tolérances recevables propres à ce critère>` | `<contrôle, version, observation et pièce attendue>` |

### Erreurs éliminatoires

| ID | Erreur | Preuve | Effet |
|---|---|---|---|
| `E1` | `<défaut précis, avec limites ou tolérances propres à cette condition>` | `<contrôle, version et observation>` | interdit `SATISFAIT` |

### Critères secondaires

Conserver au maximum deux lignes pour 0.1.0, complémentaires aux obligations et au coût. Un critère est défini avant les appels candidats ; une mesure valide peut porter sur une sortie non admissible sans changer son verdict. Une colonne ordonnable a une mesure, une preuve, une unité ou échelle justifiée, un sens favorable et une règle d’agrégation si nécessaire ; sinon elle reste descriptive. Calculs, complétude, rapprochements et durée sont des possibilités, pas des colonnes obligatoires. Une note ou un décompte des obligations pour les classer compte comme critère supplémentaire.

| ID | Critère | Question observable | Unité ou échelle | Sens favorable | Preuve |
|---|---|---|---|---|---|
| `S1` | `<nom>` | `<question>` | `<unité, échelle justifiée ou descriptif>` | `<plus haut / plus bas / oui>` | `<observation>` |
| `S2` | `<nom ou supprimer la ligne>` | `<question>` | `<unité, échelle justifiée ou descriptif>` | `<plus haut / plus bas / oui>` | `<observation>` |

### Verdicts

- `SATISFAIT` : résultat attendu et obligations prouvés, aucune erreur éliminatoire
- `NE SATISFAIT PAS` : erreur éliminatoire ou obligation non remplie établie
- Évaluation à reprendre, sans verdict métier : preuve insuffisante ou contradictoire, avec cause et prochaine action

Appliquer les [règles de verdict](../docs/RULES.md#6-erreurs-et-verdict), notamment lorsqu’un défaut est prouvé mais qu’un autre contrôle manque.

Distinguer défaut candidat et problème de consigne, données, référence, évaluation ou exécution. Ni l’accord de l’utilisateur sur l’exemple ni un consensus de modèles ne prouve la justesse de la référence ; passages, calculs et contrôles adaptés l’étayent. Une ambiguïté de l’épreuve n’est pas un échec du modèle.

### Référence et méthode d’évaluation

Référence de jugement : `<identité et empreinte ; attendus reliés aux passages, calculs ou contraintes ; solutions alternatives recevables ; informations insuffisantes et points discutables>`.

Qualification avant approbation, enregistrée hors empreinte du contrat : `<preuves référençant le contrat candidat exact ; vérification de la consigne, des cas, de la référence et des contrôles ; témoins adaptés de réussite, de défaut et d’alternative valable lorsqu’il en existe ; ambiguïtés lorsqu’elles sont prévues ; limites non résolues>`.

Méthode : `<contrôles automatiques et témoins prévus identifiés et versionnés ; jugement humain ou assisté, configuration et consignes prévues de l’assistance IA éventuelle ; responsable, constats et approbation requis ; visibilité de l’identité et du coût pendant le jugement>`.

Revue de la référence et de la méthode avant approbation, enregistrée hors empreinte du contrat : `<auteurs et pièces ; pour chaque assistance IA, configuration, consignes et sources, critiques, désaccords et arbitrage ; revue professionnelle : phase, périmètre et preuve, ou ABSENTE ; limites restantes>`. Appliquer les [règles de qualification et de revue](../docs/RULES.md#4-contrat-avant-exécution).

Exposition connue avant approbation : `<part de la référence visible au candidat ; connaissance préalable des cas par les modèles ou évaluateurs, si connue ; protections et limites>`. Une référence incertaine suit les [règles de verdict](../docs/RULES.md#6-erreurs-et-verdict).

### Base de coût fixée avant exécution

| Champ | Valeur |
|---|---|
| Périmètre d'attribution | `<coûts inclus et exclus ; préparation et jugement distingués, avec règle d’imputation s’ils entrent dans la comparaison>` |
| Tentatives comptées | `<première tentative, retries autorisés, incidents>` |
| Unité commune | `<devise et unité>` |
| Règle de conversion | `<source, date et formule, ou SANS OBJET>` |

La base fixe aussi l’unité de travail comparable : `<cas et quantité de travail auxquels le coût se rapporte>`. Les prix datés, prévisions, réservations et dépenses observées appartiennent à la campagne ; ils ne réécrivent pas cette base.

## 5. Références de campagne et de panel

Le manifeste de chaque campagne, référencé par le catalogue, fige les informations suivantes sans réécrire la version de tâche.

La liste de modèles à essayer pour l’assistance n’est pas ce panel. Présenter les critères déjà proposés, le panel, les conditions et les coûts avant lancement ; modifier un critère renvoie à la qualification et aux validations affectées. Le manifeste et ses conditions doivent être approuvés avant admission, indépendamment de l’approbation de la tâche.

| Champ | Valeur |
|---|---|
| Campagne et version de manifeste | `<identité et empreinte>` |
| Moteur prévu | `<version et interfaces retenues>` |
| Version de tâche et cas retenus | `<références et empreintes>` |
| Panel figé | `<référence et empreinte>` |

Liens entre l’assistance IA et ce panel : `<modèle ou fournisseur commun à la préparation, au jugement et aux candidats ; exposition connue lors de la campagne, protections et limites>`, selon les [règles de revue](../docs/RULES.md#4-contrat-avant-exécution).

Autorités liées au manifeste : exécution produit `<référence ou ABSENTE>` ; appels candidats et budget `<référence ou ABSENTE>`. Leur preuve, comme celle d’une reprise ultérieure, est conservée séparément des conditions figées. L’autorité de publication est référencée en section 9.

Pour chaque configuration demandée du panel, conserver :

| Champ | Valeur |
|---|---|
| Identifiant de configuration | `<identité>` |
| Modèle et révision imposée | `<nom, version exacte et preuve attendue>` |
| Fournisseur et accès direct ou API | `<valeurs>` |
| Identifiant utilisable sur le canal | `<identifiant vérifié, ou INCONNU>` |
| Route demandée | `<valeur, ou INCONNU>` |
| Paramètres et effort demandés | `<valeurs, ou INCONNU>` |
| Observations exigées | `<sources, champs ou pièces observables attendus pour prouver l’identité, la route, les paramètres et l’effort>` |

La sélection d'un nom ne prouve pas sa disponibilité. Une révision imposée ne peut pas être remplacée silencieusement. Pour un modèle local autorisé, relever aussi poids, quantification, serveur d'inférence et matériel. Les conditions communes sont référencées une fois en section 6.

## 6. Conditions de test communes

Déclarées et figées dans le manifeste de campagne avant le premier candidat, puis référencées par toutes les configurations de son panel.

| Champ | Valeur commune figée | Statut |
|---|---|---|
| Paquet ou fork Pi | `<valeur>` | `<déclarée / configurée / active / observée>` |
| Version exécutée et empreinte de Pi | `<valeurs ou INCONNU>` | `<statut>` |
| Paquets ou extensions | `<identifiants exacts ou aucun>` | `<statut>` |
| Outils | `<liste ou aucun>` | `<statut>` |
| Skills | `<état>` | `<statut>` |
| Contexte | `<identité ou empreinte>` | `<statut>` |
| Réglages par défaut de Pi | `<fournisseur, modèle et effort par défaut>` | `<statut>` |
| Environnement | `<système, matériel, runtimes, dépendances et identités nécessaires à l’attribution>` | `<statut>` |
| Date de gel | `<date>` | observée |

Chaque valeur référence sa preuve et sa date. Les [règles de gel](../docs/RULES.md#4-contrat-avant-exécution) et l’[identité d’environnement](../docs/ARD.md#31-identité-de-lenvironnement-dexécution) s’appliquent. Les réglages influents non observables et les limites de reproduction sont déclarés.

## 7. Acquisition et incidents

### Assistance de préparation et de jugement

Enregistrements hors empreinte du contrat : `<opération et phase : interview, génération, correction ou jugement ; configuration et consignes demandées puis observées ; ressources vues ; autorité et enveloppe accordées avant consommation ; intentions, reçus, dépenses et réservations ; terminaison, incident ou effets inconnus>`.

Les opérations de préparation peuvent référencer le brouillon avant qu’une campagne existe. Leurs dépenses restent séparées des tentatives candidates. Une enveloppe épuisée ou un appel ambigu bloque les nouveaux appels dépendants ; conserver l’état et les coûts connus, sans relance implicite. La simple restitution n’appelle aucun modèle.

### Autorisation propre à la campagne

| Champ | Valeur |
|---|---|
| Tentatives autorisées par cas et configuration | `<règle et autorité, ou aucune>` |
| Retries autorisés | `<règle et autorité, ou aucun>` |
| Dépense maximale | `<montant, devise, périmètre et autorité, ou ABSENTE : appel interdit>` |
| Durée et arrêt | `<limites décidées ou mesurées, sans valeur inventée>` |

Une autorité absente interdit l’opération correspondante. Le manifeste fixe aussi le plan d’ordre, les répétitions éventuelles et leur justification ; aucun nombre n’est imposé par le gabarit. La reprise doit nommer les cellules encore autorisées et les effets acquis, selon les [règles d’admission et de reprise](../docs/RULES.md#9-incidents-et-inconnues).

La base de coût est celle du contrat en section 4. Chaque campagne lui associe :

| Champ | Valeur |
|---|---|
| Prix et prévision avant appel | `<source datée, calcul, périmètre et limite de facturation connue>` |
| Réservations et dépenses | `<registre lié aux tentatives ; coût observé sourcé ou INCONNU>` |
| Admission | `<preuve des identités, conditions, stockage, autorités et budget avant émission>` |
| Interruption ou reprise | `<motif, intentions, reçus et effets inconnus conservés ; autorité de reprise éventuelle>` |

Les incidents conservent leur preuve et leur portée. Leur effet sur l’évaluation suit les [règles de verdict](../docs/RULES.md#6-erreurs-et-verdict) ; la couverture manquante reste visible.

## 8. Verdicts et décision économique

La restitution référence les verdicts par cas, les tentatives et les reçus de la campagne, sans les recopier dans le contrat gelé. Chaque opération conserve son identifiant d’exécution, sa version réelle du moteur, ses entrées, son autorité et sa terminaison. Chaque tentative relie la demande figée aux valeurs observées de fournisseur, modèle, accès, route, paramètres et effort, avec leur source ou `INCONNU`, selon les [objets d’acquisition](../docs/ARD.md#44-acquisition-tentative-et-exécution).

| Cas et tentative | Configuration | Erreurs et obligations | Verdict | Motif et critères concernés | Preuves | Coût observé | Mesures prévues |
|---|---|---|---|---|---|---|---|
| `<identités>` | `<identité>` | `<constats>` | `<verdict>` | `<motif et références>` | `<pièces et passages>` | `<valeur et unité, ou INCONNU>` | `<faits ou AUCUN>` |

Les reçus d’évaluation conservent les configurations et consignes réellement utilisées par l’assistance IA éventuelle, les pièces vues, les constats, désaccords et arbitrages requis par la méthode, sans les ajouter rétroactivement à la carte gelée.

Une synthèse multi-cas applique uniquement la règle d'agrégation du contrat et affiche sa couverture.

Responsable des verdicts : `<rôle>`.

Appliquer l’[ordre de décision](../docs/RULES.md#7-ordre-de-décision) et les [règles économiques](../docs/RULES.md#8-coût-et-bénéfices). La restitution suit le [parcours public](../docs/PRD.md#10-restitution-publique).

### Classements par critère et filtres

`<colonnes ordonnables reliées aux définitions et preuves du contrat ; observations descriptives ; périmètre consulté et couverture>`.

Ordonner seulement les valeurs connues et comparables ; afficher les autres sans rang avec motif. Conserver les égalités et les verdicts, même pour une sortie erronée bon marché. Les filtres ne changent ni contrat, ni verdicts, ni population des statistiques déjà calculées. Sans agrégation préalable, rester au cas et à la tentative. Aucun rang ne désigne automatiquement une option utilisable ou un meilleur modèle.

### Conclusion économique

`<périmètre de comparaison des coûts ; valeurs connues, inconnues et incompatibilités ; INCOMPLETE si la comparaison de ce périmètre est incomplète ; aucune désignation d’option>`.

Un coût inconnu ou non comparable peut conserver l’admissibilité sur les critères non économiques. `INCOMPLETE` est un état économique distinct du verdict. Un coût `INCONNU` ne satisfait jamais une obligation de coût. Les dépenses non admissibles restent consultables et triables sur une base comparable, sans rendre les sorties utilisables ; un sous-total connu n’est pas un coût complet.

### Mesures complémentaires

`<observations et preuves des critères secondaires prévus, ou AUCUNE ; comparabilité et limites>`. Une mesure valide reste consultable quel que soit le verdict ; elle ne compense pas une obligation non satisfaite.

## 9. Publication et limite d'attribution

Pièces publiables : `<entrées, sorties et passages approuvés>`.

Pièces privées et limites de vérification publique : `<références et motifs>`.

La publication référence son autorité, les pièces approuvées et sa version de restitution. Valider un besoin ou terminer une préparation ou campagne ne publie rien au catalogue. Aucun contenu candidat n’est interprété comme code actif dans le site.

Limite d’attribution affichée : `<formulation conforme au PRD, section 8, et limites propres à la campagne>`.

## 10. Qualification documentaire

- [ ] demande, reformulation, hypothèses validées et paramètres fictifs sont distincts ; aucune tâche non évaluable n’est transformée silencieusement
- [ ] l’aperçu ouvre les pièces construites et référence le paquet prévu pour les candidats ; toute modification entraîne les vérifications concernées et une nouvelle validation
- [ ] les appels d’assistance ont leur autorité et budget avant consommation, avec coûts et reprises séparés des candidats
- [ ] les filtres préservent les verdicts et la population des statistiques calculées ; chaque tri expose sa portée
- [ ] le parcours, les pièces et les preuves sont accessibles au clavier et sur petit écran

- [ ] version, cas et preuves attendues sont identifiés ; la couverture est justifiée
- [ ] résultat attendu, usage, intervention humaine, obligations et tolérances, erreurs éliminatoires sont définis avant exécution
- [ ] référence et méthode sont identifiées et qualifiées ; alternatives, exposition, assistance IA et éventuelle revue professionnelle sont documentées
- [ ] toute agrégation des cas est définie avant exécution, sinon seuls les verdicts par cas sont permis
- [ ] chaque campagne référence le contrat sans le réécrire ; ses autorités et états restent distincts
- [ ] les verdicts conclusifs sont distincts des évaluations à reprendre ; chaque travail restant a une cause et une prochaine action
- [ ] zéro à deux critères secondaires sont prévus pour 0.1.0 ; chaque colonne ordonnable a sa mesure, preuve, unité ou échelle justifiée, sens favorable et agrégation éventuelle
- [ ] le besoin représenté par le paquet a été validé et le responsable a approuvé le contrat qualifié, ou les appels candidats restent interdits
- [ ] les conditions de test communes sont déclarées une fois et identiques entre les configurations comparées
- [ ] chaque configuration expose l’effort demandé et l’effort observé, ou `INCONNU` pour une valeur non prouvée
- [ ] chaque configuration distingue sa route demandée de sa route observée ; toute valeur non prouvée reste `INCONNU`
- [ ] la base de coût fixe le périmètre d'attribution, les tentatives comptées, l'unité commune et la conversion éventuelle avant exécution
- [ ] chaque verdict porte un motif, ses preuves et son responsable
- [ ] les coûts et mesures valides restent visibles avec les erreurs ; inconnues et incompatibilités sont sans rang, égalités conservées et complétude économique explicite
- [ ] aucun score combiné n’est produit en 0.1.0 ; aucun meilleur modèle absolu, podium général, classement universel ou graphique trompeur n’est produit
- [ ] la limite d'attribution est visible
- [ ] les extensions non autorisées restent absentes
- [ ] la conclusion est bornée au contrat, aux cas et tentatives couverts, à la campagne, aux conditions communes et à la date
- [ ] les pièces publiables sont autorisées et les restrictions sont visibles

Un scénario de maquette ou un exemple pédagogique ne devient pas implicitement une tâche exécutable du catalogue. Le score pondéré personnalisé reste une capacité différée du [PRD](../docs/PRD.md#52-extensions), sans formule ni exécution dans ce gabarit 0.1.0.
