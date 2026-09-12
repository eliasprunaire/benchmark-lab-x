---
style_gate: pass
---

# Correctif des verdicts et des reprises

Plan demandé par Ayo le 12 septembre 2026, établi depuis `7617a09` après audit du jugement, des reprises et des transports. Les règles durables restent dans PRD, ARD, RULES et CONTEXT.

## Résultat attendu

Une décision métier aboutie porte `SATISFAIT` ou `NE SATISFAIT PAS`. Une preuve insuffisante ouvre un travail identifié avec un motif et une suite. Les constats peuvent rester indéterminés pendant leur instruction. Aucun niveau intermédiaire de satisfaction n’est ajouté sans définition au contrat. Les anciens reçus conservent leurs valeurs et leurs empreintes.

## Séquence

1. Conserver le calcul et la lecture des évaluations historiques. Contrôler la conclusion avant toute nouvelle finalisation propriétaire ; exposer un diagnostic structuré si elle manque. Rendre visibles les tentatives sans décision et les anciennes évaluations indéterminées dans la comparaison privée.
2. Diagnostiquer les propositions de juge rejetées depuis la réponse conservée : preuve divergente, structure incorrecte, réponse incomplète ou refus. Réutiliser la chaîne de correction locale sur la même sortie. Ne jamais corriger silencieusement une citation du juge.
3. Corriger les erreurs HTTP sans identité de modèle, actuellement confondues avec une identité contradictoire. Réutiliser les reprises OpenRouter préautorisées et leurs réserves. Expliquer tout arrêt lié aux capacités, routes, coûts ou effets restant à résoudre. Une réponse complète ne déclenche aucun réessai destiné à rechercher un succès.
4. Raccorder l’API officielle comme dernier recours explicite : identité exacte vérifiée, configuration distincte admise, mêmes pièces candidates et Pi, paramètres pris en charge, clé confinée, corps empreinté, reçu natif et coûts attribuables. Implémenter les canaux utilisables après inventaire des accès. DeepSeek annonce le retrait de V4 Flash et la redirection de son ancien alias vers V4.1 : cet alias ne constitue donc pas un secours pour 0731.
5. Réexaminer Rivage 0731 sur ses pièces et critères existants, puis conserver toute nouvelle décision avec son prédécesseur. Une modification du contrat ouvre une nouvelle version commune ; aucun changement rétroactif des conditions de jugement.
6. Vérifier les suites concernées, puis la commande CI et la suite moteur distincte. Toute validation sur VM distingue installation, appel reçu et effet observé ; aucune publication des sorties privées.

## Critères observables et tests

- Rapport incomplet : aucune nouvelle décision officielle, diagnostic et tentative visibles
- Citation du juge incorrecte : reçu et coût conservés, preuve divergente identifiée, zéro verdict automatique
- Correction locale : nouvelle décision sur la même sortie, historique inchangé, prédécesseur périmé refusé
- HTTP 429/503 minimal sans modèle : incident de route ; identité explicitement contradictoire toujours bloquante
- Reprise : routes, capacités et budget autorisés conservés ; aucun rejeu implicite après refus ou effets inconnus
- API directe : aucune substitution de version, estimation séparée d’une facture absente
- Projections et comparaison : rôles, octets, autorités et populations préservés

Tests proches : `test_recovery`, `test_s14_judgment`, `test_s14_acceptance`, `test_private_comparison`, `test_s5_regressions`, `test_s6_regressions`. Validation de livraison : `uv run --with requests --with mpmath==1.3.0 python -m unittest discover -s tests`, puis `benchmark_lab_x/test_demo.py` séparément. Les nouvelles régressions portent sur les défauts observés et les chemins opérateur.

## Point d’arrêt

Les chemins accessibles sont implémentés et vérifiés, les accès ou versions indisponibles sont explicités. Une absence de preuve ne devient jamais `SATISFAIT`. Aucun nombre arbitraire d’essais, sélection jusqu’au succès, remplacement de modèle ou nettoyage destructif n’est ajouté. Intégration, déploiement et appels gardent leurs preuves et autorités propres.

## Sources

- [Routage OpenRouter](https://openrouter.ai/docs/guides/routing/provider-selection)
- [Retrait DeepSeek V4 Flash](https://deepseek.com/news/deepseek-v4-1-flash/)
- [API Claude native](https://platform.claude.com/docs/en/api/messages/create)
- [GLM 5.3](https://docs.z.ai/guides/llm/glm-5.3)
