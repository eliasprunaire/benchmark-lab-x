"""Puces des écrans de résultats privés et aperçu privé d’une publication.

La restitution publique d’une campagne est retirée de la construction alpha. Ce module
ne garde que l’aperçu privé, qui montre le corps publié tel qu’un lecteur le verrait.
"""
from .fragments import bouton, contexte_cas, lien, mot, puce, ruban, text


def contexte_resultats(value, *, retour=None):
    """Puces des écrans de résultats privés : cas, version de l’exemple, ruban, portée de la lecture, retour éventuel"""
    puces = puce('Visible seulement dans ce navigateur', icone='cadenas') + (lien(*retour) if retour else '')
    return contexte_cas(value['need'], 'Exemple validé · v' + str(value['task']['version']), puces)


def render_preview(value, comparison_body):
    """Aperçu privé : choix des pièces, puis la page telle qu’un lecteur la verrait ; `comparison_body` est le corps publié"""
    from .projection import candidate_names, piece_name
    comparison = value['comparison']
    candidates = candidate_names(comparison)
    rows = {link['piece_id']: row for row in comparison['rows'] for link in row['proof_links']}
    choice = ('<form class="carte" method="get" action="' + text(comparison['href'] + '/preview') + '"><fieldset class="g-pieces">'
              '<legend>Pièces que la publication montrerait</legend>'
              '<p class="petit">Si vous n’en cochez aucune, seuls les verdicts et leurs motifs apparaissent.</p>')
    for piece in value['pieces']:
        pid = piece['piece_id']
        choice += ('<label class="case"><input type="checkbox" name="piece" value="' + text(pid) + '"'
                   + (' checked' if pid in value['selected_links'] else '') + '><span>'
                   + text(piece_name(rows[pid], piece, candidates)) + '</span></label>')
    choice += ('</fieldset><div class="actions">' + bouton('Actualiser l’aperçu', icone='actualiser') + '</div>'
               '<p class="petit">L’aperçu montre tout le benchmark : les filtres des résultats ne s’appliquent pas ici.</p></form>')
    return ('<p class="texte-l">Aperçu privé, non approuvé : rien n’est publié. Cette page montre ce qu’un lecteur '
            'verrait si ce benchmark était publié.</p><div class="pile-l">' + choice
            + '<div class="g-feuillet">' + ruban('Vue du lecteur') + comparison_body + '</div></div>')


def contexte_apercu(value):
    return ruban('Aperçu privé') + mot('Non approuvé', 'vig') + puce('Rien n’est publié', icone='cadenas') + lien(
        value['comparison']['href'], 'Revenir aux résultats', 'retour')
