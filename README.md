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

## Ce que fait Evaly

1. Vous décrivez votre cas d’usage en quelques phrases. L’assistant vous pose une question si besoin.
2. L’assistant crée un exemple réaliste : une consigne et des documents inventés.
3. Vous corrigez l’exemple si besoin, puis vous le validez.
4. Vous choisissez les modèles à comparer et voyez le coût estimé avant de lancer.
5. Pour chaque modèle, Evaly donne son verdict, ses preuves et son coût observé.

Evaly ne donne ni note globale ni classement universel.

## Utiliser Evaly

L’instance du Lab-X est en ligne : [evaly.cybrel.fr](https://evaly.cybrel.fr/). Evaly est en alpha.

Evaly appelle les modèles par OpenRouter, avec votre propre clé. Vous l’ajoutez sur la page **Ma clé OpenRouter**, avec le bouton **Ajouter ma clé**. La même page permet de la remplacer ou de la retirer. Evaly n’ajoute aucune limite de dépense à celle que vous fixez, ou non, chez OpenRouter. Enregistrer la clé ne lance aucun modèle.

## Documentation

- [Guide de contribution](CONTRIBUTING.md) : branches, commits, CI, revue et fusion ;
- [Release et livraison](docs/release.md) : versionnement, artefacts et déploiement ;
- [Sécurité](SECURITY.md) : signaler une vulnérabilité en privé.

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
