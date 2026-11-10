<div align="center">
  <img src="benchmark_web/static/evaly.svg" width="96" height="96" alt="Logo Evaly : un Y dans un carré jaune">
  <h1 id="evaly">Evaly</h1>
  <p><strong>par Cybrel · né au Lab-X</strong></p>
  <p>Evaly, le benchmark personnalisé : choisir une configuration de modèle d’IA à partir d’un cas d’usage précis, de résultats observés, de preuves consultables et de leur coût.</p>
  <p>
    <a href="https://github.com/eliasprunaire/benchmark-lab-x/actions/workflows/ci.yml"><img alt="CI Python" src="https://github.com/eliasprunaire/benchmark-lab-x/actions/workflows/ci.yml/badge.svg"></a>
    <a href="LICENSE"><img alt="Licence AGPL-3.0" src="https://img.shields.io/badge/licence-AGPL--3.0-blue.svg"></a>
  </p>
  <p>
    <a href="https://evaly.cybrel.fr/">Ouvrir Evaly</a> ·
    <a href="#documentation">Documentation</a> ·
    <a href="CONTRIBUTING.md">Contribuer</a>
  </p>
</div>

## Pourquoi Evaly

Un modèle n’est jamais « le meilleur » dans l’absolu. Evaly compare des modèles dans les mêmes conditions, sur votre propre cas d’usage. Chaque verdict reste lié à l’exemple que vous avez validé, à la configuration réellement observée, aux preuves conservées et à la date du benchmark.

Evaly est en version alpha. Quelques erreurs peuvent encore subsister.

## Ce que fait Evaly

1. Vous décrivez votre cas d’usage en quelques phrases. L’assistant vous pose une question si besoin.
2. L’assistant crée un exemple réaliste : une consigne et des documents inventés.
3. Vous corrigez l’exemple si besoin, puis vous le validez.
4. Vous choisissez les modèles à comparer et voyez le coût estimé avant de lancer.
5. Pour chaque modèle, Evaly donne son verdict, ses preuves et son coût observé.

Evaly ne donne ni note globale ni classement universel.

## Utiliser Evaly

L’outil est en ligne : [evaly.cybrel.fr](https://evaly.cybrel.fr/).

Evaly envoie chaque appel de modèle à OpenRouter. Vous fournissez votre propre clé. Créez-en une réservée à Evaly.

Sur vos cas d’usage, **Ajouter ma clé** ouvre la page **Ma clé OpenRouter**.

Sur cette page, **Enregistrer la clé** vérifie la clé auprès d’OpenRouter et la conserve. Vous pouvez ensuite la remplacer, ou choisir **Retirer la clé de ce navigateur**.

Evaly n’ajoute aucun plafond de dépense. Le plafond, s’il existe, est celui de votre clé chez OpenRouter.

Evaly recommande d’en fixer un chez OpenRouter. Vous choisissez le montant. Une clé sans plafond reste acceptée.

Enregistrer la clé ne lance aucun modèle.

**Retirer la clé de ce navigateur** arrête les appels suivants depuis ce navigateur. La clé reste active chez OpenRouter. Un appel déjà envoyé n’est pas annulé.

## Le harnais

Dans un benchmark, tous les modèles passent par le même harnais, [pi.dev](https://pi.dev), dans la même version.

Evaly le démarre à vide : aucun outil, aucune extension, aucune skill et aucune configuration personnelle.

Chaque modèle reçoit seulement la consigne et l’exemple, dans les mêmes conditions.

## Lire un verdict

- **Satisfait** : toutes les exigences sont respectées, et aucune erreur éliminatoire n’est relevée.
- **Ne satisfait pas** : une exigence n’est pas respectée, ou une erreur éliminatoire est relevée. La réponse n’est pas utilisable, quel que soit son coût.
- **À reprendre** : la preuve ne permet pas encore de conclure. Ce n’est pas un échec du modèle.

Le coût observé départage seulement des réponses qui satisfont l’exemple avec la même qualité. Un coût inconnu ne change pas le verdict.

## Recommandations

N’envoyez pas de dossier réel, même anonymisé. L’exemple et ses documents sont inventés.

Vos cas d’usage et vos résultats restent privés. Evaly ne les publie pas sans votre autorisation.

## Documentation

- [Guide de contribution](CONTRIBUTING.md) : branches, commits, CI, revue et fusion ;
- [Release et livraison](docs/release.md) : versionnement, artefacts et déploiement.

## Contribuer

Consultez [CONTRIBUTING.md](CONTRIBUTING.md) avant de créer une branche ou une pull request.

## Sécurité

Ne publiez pas une vulnérabilité dans une Issue, une pull request ou une Discussion. Utilisez le bouton **Report a vulnerability** dans l’onglet [Security](https://github.com/eliasprunaire/benchmark-lab-x/security) afin de transmettre le rapport de manière privée aux mainteneurs. La procédure et les informations utiles sont précisées dans [SECURITY.md](SECURITY.md).

## Licence

Evaly est distribué sous licence [AGPL-3.0-only](LICENSE).

---

<div align="center">
  <p><strong>Evaly</strong> par Cybrel · né au Lab-X</p>
  <p>Des choix de modèles fondés sur des cas d’usage précis et des preuves lisibles.</p>
  <p><a href="#evaly">Retour en haut</a></p>
</div>
