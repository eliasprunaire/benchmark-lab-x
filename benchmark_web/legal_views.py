"""Pages légales publiques : source unique, sans session, secret ni accès au stockage"""

from .fragments import text

CONTACT = '<a href="mailto:contact@cybrel.fr">contact@cybrel.fr</a>'


def table(label, head, rows):
    """Tableau défilant dans sa propre région au clavier, jamais dans la page"""
    return ('<p class="table-hint">Sur petit écran, faites défiler le tableau horizontalement.</p>'
            '<div class="table-scroll" role="region" tabindex="0" aria-label="' + text(label) + '"><table><thead><tr>'
            + ''.join('<th scope="col">' + cell + '</th>' for cell in head) + '</tr></thead><tbody>'
            + ''.join('<tr>' + ''.join('<td>' + cell + '</td>' for cell in row) + '</tr>' for row in rows)
            + '</tbody></table></div>')


MENTIONS_LEGALES = (
    '<section><h2>Éditeur du site</h2><p><strong>CYBREL</strong><br>Courriel : ' + CONTACT + '</p>'
    '<p>Directeur de la publication : Cybrel RSSI</p></section>'
    '<section><h2>Hébergeur</h2><p><strong>CYBREL</strong></p></section>'
    '<section><h2>Nature du service</h2><p>Bench-X est un service gratuit qui aide à préparer un exemple de travail '
    'entièrement fictif, puis à comparer sur cet exemple plusieurs configurations de modèles d’intelligence '
    'artificielle. Il ne fournit ni conseil professionnel, ni prestation de traitement de documents réels.</p>'
    '<p>Bench-X ne demande pas de compte. L’accès à vos cas d’usage repose sur un cookie technique déposé dans '
    'votre navigateur.</p></section>'
    '<section><h2>Code source</h2><p>Bench-X est un logiciel libre distribué sous licence <strong>GNU Affero '
    'General Public License, version 3 uniquement (AGPL-3.0-only)</strong>.</p>'
    '<p>Conformément à la section 13 de cette licence, relative à l’interaction par le réseau, le code source '
    'complet de la version exécutée par ce service est mis à votre disposition :</p><ul>'
    '<li>dépôt : <a href="https://github.com/eliasprunaire/benchmark-lab-x">'
    'https://github.com/eliasprunaire/benchmark-lab-x</a></li>'
    '<li>version exécutée : <strong>version de release indiquée au dépôt</strong></li></ul></section>'
    '<section><h2>Propriété intellectuelle</h2><p>Les textes, la charte graphique et les contenus éditoriaux du '
    'site sont protégés. Les polices de caractères utilisées sont distribuées sous licence SIL Open Font License '
    'version 1.1 : Syne, Atkinson Hyperlegible Next et Atkinson Hyperlegible Mono.</p>'
    '<p>Les exemples, pièces et résultats produits dans votre parcours restent privés et vous appartiennent, sous '
    'réserve des droits que vous accordez explicitement en activant la contribution facultative.</p></section>'
    '<section><h2>Signalement</h2><p>Pour signaler un contenu illicite, une faille de sécurité ou une difficulté '
    'd’accès : ' + CONTACT + '.</p></section>'
    '<section><h2>Droit applicable</h2><p>Le présent site est soumis au droit français.</p></section>')

CGU = (
    '<p>Dernière mise à jour : 29 septembre 2026</p>'
    '<section><h2>1. Objet</h2><p>Les présentes conditions régissent l’utilisation de Bench-X, service gratuit '
    'accessible à l’adresse bench.librenet.work. Utiliser le service vaut acceptation de ces conditions.</p></section>'
    '<section><h2>2. Ce que fait le service</h2><p>Bench-X vous aide à décrire une tâche de votre travail, '
    'construit avec vous un exemple entièrement inventé qui la représente, puis compare plusieurs configurations '
    'de modèles d’intelligence artificielle sur cet exemple, dans des conditions communes.</p>'
    '<p>Le résultat porte sur la configuration observée, sur cet exemple, à la date de la comparaison. Il ne '
    'désigne pas de meilleur modèle en général et ne garantit aucun résultat sur vos dossiers réels.</p></section>'
    '<section><h2>3. Ce que le service ne fait pas</h2><p>Bench-X ne traite aucun document réel, n’accède ni à '
    'votre ordinateur, ni à votre téléphone, ni à vos données. Il ne fournit aucun conseil juridique, médical, '
    'comptable, financier ou professionnel, et ne délivre aucune habilitation.</p>'
    '<p>Une réussite sur un exemple fictif ne préjuge pas de la qualité d’un modèle sur un cas réel. Les décisions '
    'que vous prenez à partir des résultats relèvent de votre seule responsabilité.</p></section>'
    '<section><h2>4. Accès sans compte</h2><p>Le service ne demande pas de compte. L’accès à vos cas d’usage repose '
    'sur un cookie technique déposé dans votre navigateur, valable trente jours au maximum sans interaction réelle '
    'de votre part.</p><p>Perdre ou effacer ce cookie fait perdre l’accès à vos cas d’usage conservés sur le '
    'serveur. Nous ne pouvons pas rétablir cet accès, faute de pouvoir vous identifier.</p></section>'
    '<section><h2>5. Vos engagements</h2><p>En utilisant Bench-X, vous vous engagez à :</p><ul>'
    '<li>ne saisir <strong>aucune donnée personnelle</strong> et <strong>aucune information confidentielle</strong>, '
    'ni la vôtre ni celle d’un tiers ; les personnes, organismes, échanges et pièces que vous décrivez doivent être '
    'entièrement inventés ;</li>'
    '<li>ne pas soumettre de dossier réel, même anonymisé ;</li>'
    '<li>ne pas utiliser le service pour produire un contenu illicite, diffamatoire, haineux, contrefaisant ou '
    'portant atteinte aux droits d’un tiers ;</li>'
    '<li>ne pas tenter de contourner les limites techniques du service, de l’automatiser massivement, de le saturer '
    'ni d’en perturber le fonctionnement ;</li>'
    '<li>ne pas tenter d’accéder aux cas d’usage d’un autre visiteur.</li></ul>'
    '<p>Aucune anonymisation automatique n’est réalisée sur vos saisies. La consigne de n’utiliser que des données '
    'fictives ne garantit pas à elle seule l’absence de contenu sensible dans ce que vous écrivez.</p></section>'
    '<section><h2>6. Accès fournisseur personnel</h2><p>Si vous enregistrez votre propre clé d’accès OpenRouter, '
    'elle finance vos propres appels, sous le plafond défini chez ce fournisseur.</p>'
    '<p>La clé doit porter chez OpenRouter un <strong>plafond de dépense non renouvelable d’au plus 50 dollars '
    'américains</strong>. Une clé sans plafond, avec un plafond supérieur, ou dont le plafond se réinitialise '
    'périodiquement est refusée par le service. Utilisez une clé dédiée à Bench-X.</p>'
    '<p>Enregistrer une clé ne lance aucun appel. Retirer la clé interdit les nouveaux appels avec celle-ci, sans '
    'la révoquer chez OpenRouter et sans annuler un appel déjà émis. Nous ne remboursons aucune dépense engagée chez '
    'un fournisseur tiers.</p></section>'
    '<section><h2>7. Contribution facultative</h2><p>Après examen d’un exemple, vous pouvez autoriser sa '
    'conservation pour améliorer Bench-X. Ce choix est facultatif, sans effet sur votre accès au service, et '
    'révocable à tout moment. Il n’autorise aucune publication. Les modalités figurent dans la '
    '<a href="/confidentialite">politique de confidentialité</a>.</p></section>'
    '<section><h2>8. Publication des résultats</h2><p>Vos cas d’usage et leurs résultats sont privés par défaut. '
    'Aucune publication n’a lieu sans une autorisation explicite et distincte. Les comparaisons publiées '
    'accessibles depuis le site ne contiennent que des éléments approuvés pour cette publication.</p></section>'
    '<section><h2>9. Disponibilité et évolution</h2><p>Le service est fourni en l’état, sans garantie de '
    'disponibilité, de continuité ni d’absence d’erreur. Il peut être interrompu, limité ou modifié à tout moment, '
    'notamment pour maintenance, pour préserver l’intégrité des données ou en cas de dépassement de budget.</p>'
    '<p>Les préparations et comparaisons en cours peuvent être interrompues. Les résultats déjà acquis restent '
    'consultables tant qu’ils sont conservés selon les durées annoncées.</p></section>'
    '<section><h2>10. Responsabilité</h2><p>Dans la limite permise par la loi, la responsabilité de l’éditeur ne '
    'peut être engagée au titre :</p><ul>'
    '<li>des décisions prises à partir des résultats fournis ;</li>'
    '<li>de la qualité, de l’exactitude ou de la disponibilité des modèles tiers ;</li>'
    '<li>des dépenses engagées chez un fournisseur tiers avec votre propre clé ;</li>'
    '<li>de la perte d’accès consécutive à l’effacement du cookie technique ou de l’historique local de votre '
    'navigateur ;</li>'
    '<li>des contenus que vous saisissez en violation de l’article 5.</li></ul></section>'
    '<section><h2>11. Suspension</h2><p>L’accès peut être suspendu, sans préavis, en cas de manquement aux '
    'présentes conditions, notamment en cas d’usage abusif ou de tentative de saturation du service.</p></section>'
    '<section><h2>12. Propriété intellectuelle</h2><p>Le logiciel Bench-X est distribué sous licence '
    'AGPL-3.0-only. Le code source de la version exécutée est accessible depuis les '
    '<a href="/mentions-legales">mentions légales</a>.</p>'
    '<p>Vous conservez vos droits sur les descriptions que vous saisissez. Vous accordez à l’éditeur le droit de les '
    'traiter pour vous fournir le service, et rien de plus, sauf contribution que vous auriez explicitement '
    'activée.</p></section>'
    '<section><h2>13. Modification des conditions</h2><p>Ces conditions peuvent être modifiées. La version '
    'applicable est celle publiée sur cette page à la date de votre utilisation. La date de dernière mise à jour '
    'figure en tête.</p></section>'
    '<section><h2>14. Droit applicable et juridiction</h2><p>Droit français. À défaut de résolution amiable, les '
    'tribunaux français sont compétents, sous réserve des règles protectrices applicables aux '
    'consommateurs.</p></section>'
    '<section><h2>15. Contact</h2><p>' + CONTACT + '</p></section>')

CONFIDENTIALITE = (
    '<p>Dernière mise à jour : 29 septembre 2026</p>'
    '<section><h2>1. Responsable du traitement</h2><p><strong>CYBREL</strong><br>Contact pour toute question ou '
    'demande relative à vos données : <strong>' + CONTACT + '</strong></p></section>'
    '<section><h2>2. Principe</h2><p>Bench-X est conçu pour fonctionner <strong>sans que vous ayez à fournir de '
    'donnée personnelle</strong>. Vous décrivez une tâche de travail ; nous construisons avec vous un exemple '
    'entièrement inventé. Ne saisissez aucune donnée personnelle ni information confidentielle, ni la vôtre ni '
    'celle d’un tiers.</p>'
    '<p>Aucune anonymisation automatique n’est réalisée sur vos saisies, et aucune n’est garantie. Si vous écrivez '
    'malgré tout une information identifiante, elle sera traitée comme le reste de votre saisie et conservée selon '
    'les durées ci-dessous.</p>'
    '<p>Le service ne demande pas de compte et ne cherche pas à vous identifier. Deux éléments techniques restent '
    'néanmoins des données personnelles au sens du RGPD : l’identifiant de session déposé dans votre navigateur et '
    'l’empreinte calculée à partir de votre adresse IP pour limiter les abus. Ils figurent dans le tableau '
    'ci-dessous.</p></section>'
    '<section><h2>3. Données traitées, finalités et bases légales</h2>'
    + table('Données traitées, finalités et bases légales', ('Donnée', 'Finalité', 'Base légale', 'Durée'), (
        ('Texte que vous saisissez (tâche, résultat attendu, contexte, corrections)',
         'Construire l’exemple fictif et préparer la comparaison',
         'Nécessaire à la fourniture du service que vous demandez (art. 6.1.b RGPD)',
         'Accès fermé 7 jours après la dernière activité. Effacement effectif : voir §9'),
        ('Exemple fictif produit, pièces, qualification, réponses des modèles, verdicts et coûts',
         'Vous restituer la comparaison', 'Nécessaire à la fourniture du service (art. 6.1.b)',
         'Accès fermé 7 jours après la dernière activité. Effacement effectif : voir §9'),
        ('Identifiant de session opaque, déposé en cookie', 'Vous redonner accès à vos cas d’usage sans compte',
         'Nécessaire au service que vous demandez (art. 6.1.b)', '30 jours sans interaction réelle'),
        ('Clé d’accès OpenRouter, si vous en enregistrez une', 'Financer vos propres appels avec votre accès',
         'Nécessaire au service que vous demandez (art. 6.1.b)',
         'Chiffrée, retrait immédiat possible, sinon 30 jours sans interaction réelle'),
        ('Empreinte de votre adresse IP, hachée avec un secret renouvelé à chaque démarrage du service',
         'Limiter les abus et la saturation du service', 'Intérêt légitime à protéger le service (art. 6.1.f)',
         '1 heure glissante, en mémoire uniquement'),
        ('Copie de l’exemple, de sa qualification et de ses résultats, si vous activez la contribution',
         'Améliorer Bench-X', 'Votre consentement (art. 6.1.a)', '6 mois à compter du consentement'),
        ('Historique local dans votre navigateur (IndexedDB)',
         'Vous permettre de relire et d’exporter vos cas après expiration de l’accès serveur',
         'Votre consentement (art. 6.1.a)', 'Jusqu’à effacement par vous ou par votre navigateur')))
    + '<p>Nous ne réalisons <strong>aucun profilage</strong>, aucune publicité, aucune revente, et aucune décision '
    'automatisée produisant des effets juridiques à votre égard.</p></section>'
    '<section><h2>4. Destinataires</h2><p>Pour produire une comparaison, le contenu nécessaire aux appels est '
    'transmis à :</p><ul><li><strong>OpenRouter</strong>, qui achemine les appels vers les fournisseurs de '
    'modèles ;</li><li><strong>les fournisseurs des modèles comparés</strong>, à travers OpenRouter.</li></ul>'
    '<p>Leurs propres conditions de traitement et de conservation s’appliquent. Une suppression dans Bench-X ne '
    'garantit pas l’effacement des copies déjà traitées par ces tiers.</p>'
    '<p>Aucun autre destinataire. Aucune donnée n’est transmise à des régies publicitaires ou à des outils de '
    'mesure d’audience : <strong>le service n’en utilise aucun</strong>.</p></section>'
    '<section><h2>5. Transferts hors Union européenne</h2>'
    '<p>Les appels sont acheminés par OpenRouter, Inc., établi aux États-Unis, et peuvent être traités par des '
    'fournisseurs de modèles situés partout dans le monde, y compris dans des pays qui ne bénéficient pas d’une '
    'décision d’adéquation de la Commission européenne.</p>'
    '<p>Pour les données qu’il reçoit, OpenRouter déclare s’appuyer sur les décisions d’adéquation de la Commission '
    'européenne (article 45 du RGPD) et sur les clauses contractuelles types approuvées par la Commission '
    '(article 46 du RGPD). Vous pouvez en obtenir une copie à ' + CONTACT + '.</p>'
    '<p>Les fournisseurs de modèles appliquent leurs propres conditions de traitement. Nous ne pouvons pas garantir '
    'que chacun d’eux offre un niveau de protection équivalent à celui de l’Union européenne : c’est l’une des '
    'raisons pour lesquelles vous ne devez saisir aucune donnée personnelle dans Bench-X.</p></section>'
    '<section><h2>6. Cookies et stockage dans votre navigateur</h2><p>Le site <strong>ne dépose aucun cookie de '
    'mesure d’audience, de publicité ou de réseau social</strong>. Aucun cookie n’est déposé lorsque vous consultez '
    'la page d’accueil.</p>'
    + table('Cookies déposés', ('Nom', 'Déposé quand', 'Rôle', 'Durée'), (
        ('<code>benchmark_session</code>', 'À l’ouverture de votre espace', 'Accès à vos cas d’usage sans compte',
         '30 jours sans interaction réelle'),
        ('<code>benchmark_access_callback</code>', 'Quand vous connectez votre accès OpenRouter',
         'Sécuriser le retour depuis OpenRouter', 'Le temps de l’opération, supprimé au retour'),
        ('<code>benchmark_contributions</code>', 'Seulement si vous activez une contribution',
         'Vous permettre de la retirer même après expiration de l’accès principal',
         'Jusqu’à la dernière échéance de vos contributions')))
    + '<p>Ces cookies sont protégés (<code>HttpOnly</code>, <code>Secure</code>) et limités au parcours privé. Ils '
    'ne servent à aucun suivi.</p>'
    '<p>Les deux premiers sont exemptés de consentement car strictement nécessaires au service que vous demandez. '
    'Le troisième n’est déposé que si vous activez la contribution facultative : il accompagne votre consentement '
    'et disparaît avec lui.</p><p>Stockage local complémentaire :</p>'
    + table('Stockage local complémentaire', ('Élément', 'Rôle'), (
        ('<code>bench-x-history</code> (IndexedDB)',
         'Copies complètes de vos cas d’usage, pour relecture et export depuis ce navigateur'),
        ('<code>bench-x-session-opening</code> (sessionStorage)',
         'Éviter une boucle d’ouverture si les cookies sont bloqués')))
    + '<p>L’historique local n’est pas une sauvegarde garantie : votre navigateur peut l’effacer, notamment en '
    'navigation privée ou par manque de place. Toute personne utilisant ce profil de navigateur peut le consulter. '
    'Il ne contient ni votre clé d’accès ni la référence privée de jugement. Vous pouvez le suspendre ou l’effacer à '
    'tout moment depuis <a href="/preparation/data">Mes données</a>.</p></section>'
    '<section><h2>7. Sécurité</h2><p>Votre clé d’accès fournisseur est chiffrée sur le serveur (AES-256-GCM) et '
    'n’est jamais réaffichée. Les identifiants de session sont opaques et sans lien avec votre identité. Les '
    'journaux d’exploitation ne contiennent ni votre saisie, ni les réponses des modèles, ni votre clé.</p></section>'
    '<section><h2>8. Vos droits</h2><p>Vous disposez des droits d’<strong>accès</strong>, de '
    '<strong>rectification</strong>, d’<strong>effacement</strong>, de <strong>limitation</strong>, '
    'd’<strong>opposition</strong> et de <strong>portabilité</strong> sur les données vous concernant, ainsi que du '
    'droit de <strong>retirer votre consentement</strong> à tout moment pour la contribution facultative.</p>'
    '<p>Vous pouvez exercer directement, sans nous écrire :</p><ul>'
    '<li><strong>supprimer un cas d’usage</strong> : bouton « Supprimer ce cas d’usage » sur la page du cas ;</li>'
    '<li><strong>retirer votre clé d’accès</strong> : page <a href="/preparation">Mes cas d’usage</a> ;</li>'
    '<li><strong>retirer une contribution</strong> : page <a href="/preparation/contributions">Mes '
    'contributions</a> ;</li>'
    '<li><strong>exporter ou effacer votre historique local</strong> : page <a href="/preparation/data">Mes '
    'données</a>.</li></ul>'
    '<p>Pour toute autre demande : <strong>' + CONTACT + '</strong>.</p>'
    '<p><strong>Limite importante que nous devons vous signaler.</strong> Le service ne vous identifie pas. Nous ne '
    'pouvons rattacher une demande à vos données que si elle provient du navigateur porteur du cookie '
    'correspondant. Si vous avez perdu ce cookie, nous ne pouvons ni retrouver ni supprimer vos données à votre '
    'demande : elles seront effacées à l’échéance de conservation indiquée.</p>'
    '<p>Vous pouvez introduire une réclamation auprès de la <strong>Commission nationale de l’informatique et des '
    'libertés (CNIL)</strong>, 3 place de Fontenoy, TSA 80715, 75334 Paris Cedex 07, ou sur '
    '<a href="https://www.cnil.fr" rel="noreferrer">www.cnil.fr</a>.</p></section>'
    '<section><h2>9. Suppression et sauvegardes</h2><p>Supprimer un cas d’usage ferme immédiatement les accès '
    'ordinaires et demande la suppression sur le serveur, ainsi que celle de sa contribution éventuelle.</p>'
    '<p>L’échéance de 7 jours ferme l’accès à vos données. '
    'L’effacement des octets sur le serveur est réalisé par une tâche de suppression planifiée.</p></section>'
    '<section><h2>10. Modification de cette politique</h2><p>Cette politique peut évoluer. La date de dernière mise '
    'à jour figure en tête. En cas de changement substantiel affectant une finalité fondée sur votre consentement, '
    'un nouveau choix vous sera demandé.</p></section>')

LEGAL_PAGES = {
    '/mentions-legales': ('Mentions légales', MENTIONS_LEGALES),
    '/cgu': ('Conditions générales d’utilisation', CGU),
    '/confidentialite': ('Politique de confidentialité', CONFIDENTIALITE),
}
