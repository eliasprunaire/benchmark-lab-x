---
style_gate: pass
---

# Feuille de route de Bench-X

## Rôle

Ce document relie les exigences décrites par le [PRD](PRD.md) à un jalon nommé. Il ne crée aucune exigence : une ligne absente du PRD n’a rien à faire ici. Il ne porte aucun état d’avancement, qui vit dans GitHub, ni aucune preuve de livraison, qui vit dans les releases et leurs reçus. Les jalons GitHub portent les mêmes noms et regroupent les Issues ; ce document garde le lien entre chaque jalon et les exigences du PRD.

Un jalon porte un nom, jamais un numéro. Les jalons se succèdent sous les noms alpha, bêta, puis release candidate 1, 2, 3 ; seul le prochain est défini. Le numéro d’une version est calculé à la release à partir des commits, selon [la règle de version](release.md#règle-de-version) ; aucun numéro n’est visé à l’avance. Les [règles de versionnement](RULES.md#14-versionnement-du-produit) restent seules à gouverner ces numéros.

## Jalons

| Jalon | Résultat |
|---|---|
| Alpha | Première version ouverte au public. Suivi : initiative [#337](https://github.com/eliasprunaire/benchmark-lab-x/issues/337) |

## Matrice

| Exigence du PRD | Source | Jalon |
|---|---|---|
| Parcours public de description, clarification et préparation assistée d’un dossier fictif consultable et modifiable, jusqu’au lancement autorisé, aux résultats privés et à leur comparaison | [§3.1](PRD.md#31-audience-et-accès), [§10](PRD.md#préparer-et-valider-lexemple) | Alpha |
| Accès API via OpenRouter sous Pi constant, financé par la clé personnelle du demandeur : clé chiffrée liée à la session, retrait, expiration | [§5.1](PRD.md#51-capacités), [§10](PRD.md#accès-openrouter-personnel) | Alpha |
| Historique et contributions : conservation, historique local, contribution facultative, « Mes données » | [§10](PRD.md#historique-et-contributions) | Alpha |
| Saisie, interview, aperçus, corrections et accès aux preuves au clavier et sur petit écran | [§10](PRD.md#consulter-les-résultats) | Alpha |

## Sans jalon décidé

Les exigences suivantes du PRD n’ont pas encore de jalon. Les dater demande une décision d’Ayo, reportée dans la matrice.

- navigation catalogue, tâche, campagne et comparaison des configurations ([§5.1](PRD.md#51-capacités), [§10](PRD.md#consulter-les-résultats))
- consultation publique des seules restitutions approuvées, sans classement universel ([§5.1](PRD.md#51-capacités), [§10](PRD.md#consulter-les-résultats))
- catalogue de tâches versionnées, avec contrat et cas d'essai identifiés ; aucune demande n’y est publiée automatiquement ([§5.1](PRD.md#51-capacités))
- plusieurs campagnes, chacune liée à une version de tâche, à ses cas et à un panel figé ([§5.1](PRD.md#51-capacités))
- résultats réellement acquis et évalués sur le catalogue et le panel approuvés ([§5.1](PRD.md#51-capacités))
- classements par critère et filtres combinés, sans note pondérée ni désignation automatique du meilleur modèle ([§5.1](PRD.md#51-capacités), [§7](PRD.md#7-ordre-de-décision))
- secours officiel candidat en dernier recours selon les conditions de l’ARD ([§5.1](PRD.md#51-capacités))
- suivi des tentatives, incidents, coûts et preuves sans relance implicite ([§5.1](PRD.md#51-capacités))
- extensions : score pondéré personnalisé, couverture de métiers variés, modèles locaux, abonnements, produits agentiques et comparaison de harnais ([§5.2](PRD.md#52-extensions))
