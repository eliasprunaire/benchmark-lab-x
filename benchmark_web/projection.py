"""Projection publique S6 : corps, page et feuille de style, à octets constants.

Le moteur reçoit ce module par injection (`presentation`) ; il ne l'importe pas.
"""
from html import escape
from pathlib import Path
import re

STYLESHEET_PATH = Path(__file__).with_name('static') / 'projection.css'
RESTRICTION_PUBLIQUE = (
    'Vérification publique restreinte : les pièces non sélectionnées et leurs passages restent privés. '
    'Leur empreinte ne remplace pas une preuve consultable. Les constats qui en dépendent restent invérifiables ici.')


def stylesheet():
    return STYLESHEET_PATH.read_bytes()


def _html_text(value):
    return escape(str(value), quote=True)


def _libelles_criteres(row):
    specification = row['qualification']['contract']['specification']
    items = (specification['obligations'] + specification['eliminatory_errors']
             + specification['secondary_criteria'])
    return {item['id']: item.get('description') or item.get('measure') or item.get('label') for item in items}


def _remplacer_criteres(value, labels):
    if not labels:
        return str(value)
    pattern = r'(?<!\w)(' + '|'.join(map(re.escape, labels)) + r')(?!\w)'
    return re.sub(pattern, lambda match: labels[match[0]], str(value))


VERDICTS = {'SATISFAIT': 'Satisfait', 'NE SATISFAIT PAS': 'Ne satisfait pas'}
STATUTS = {'PASS': 'respectée', 'FAIL': 'non respectée', 'INDETERMINE': 'non vérifiable'}


def _montant(cost):
    from .fragments import montant_lisible
    if not cost or cost.get('value', cost.get('amount')) is None:
        return 'inconnu'
    return montant_lisible(cost.get('value', cost.get('amount'))) + ' ' + str(cost.get('unit', cost.get('currency', '')))


def candidate_names(value):
    """Nom commercial et effort par configuration ; la sortie brute porte ce nom plutôt que l'identifiant de tentative"""
    from .campaign_views import candidate_label
    names = value.get('model_names', {})
    return {row['configuration_id']: candidate_label(row['requested_configuration'], names) for row in value['rows']}


def piece_name(row, link, candidates):
    return ('Réponse de ' + candidates[row['configuration_id']] if link['piece_id'] == row.get('output_piece_id')
            else link['name'])


def projection_body(value, selected, level=1):
    """Only explicit presentation fields; never serialize a private evaluation object

    `level` : rang du titre principal, 2 quand la projection s'insère dans une page qui a déjà son H1
    """
    from benchmark.restitution import ATTRIBUTION
    from .fragments import jour_lisible
    t = _html_text
    main, sub = 'h' + str(level), 'h' + str(level + 1)
    candidates = candidate_names(value)
    coverage = value['coverage']
    conditions = value['conditions']
    body = '<' + main + '>Comparaison : ' + t(value['need']) + '</' + main + '>'
    body += '<p>Résultat attendu : ' + t(value['result_expected']) + '</p>'
    body += '<p>' + t(value['conclusion']['text']) + '</p><p>' + t(ATTRIBUTION) + '</p>'
    body += '<p>Limites : ' + t('; '.join(value['conclusion']['limits'])) + '</p>'
    if coverage:
        body += '<p>Réponses évaluées : ' + t(coverage['evaluated_attempts']) + ' · essais lancés : ' + t(coverage['attempted_cells'])
        body += ' sur ' + t(coverage['planned_cells']) + '.</p>'
    body += '<p>Comparaison des coûts ' + ('complète' if value['economic_status'] == 'COMPLETE' else 'incomplète')
    body += ' ; coûts candidats et de jugement séparés.</p>'
    if conditions:
        body += '<p>Conditions communes : harnais ' + t(conditions['pi']['package'] + ' ' + conditions['pi']['version'])
        body += ', figées le ' + t(jour_lisible(conditions['frozen_at'])) + '.</p>'
    for pending in value.get('pending_attempts', []):
        body += '<p>Une réponse reste à évaluer : ' + t(pending['next_action']) + '</p>'
    body += '<p>Les descriptions des obligations et des erreurs éliminatoires sont publiées comme libellés. '
    body += 'La référence de jugement et les preuves de qualification restent privées ; '
    body += 'ces descriptions seules ne permettent pas de vérifier publiquement la qualification des critères.</p>'
    body += '<p>' + t(RESTRICTION_PUBLIQUE) + '</p>'
    labels = {}
    for row in value['rows']:
        labels.update(_libelles_criteres(row))
    criteria = []
    for column in value['columns']:
        definition = column['definition']
        label = ('Coût observé' if 'criterion_id' not in column else
                 labels.get(column['criterion_id'], definition.get('measure')))
        criteria.append(t(label) + (' : ' + t(definition['unit']) if definition.get('unit') else '')
                        + (', valeur favorable ' + t(definition['favorable']) if definition.get('favorable') else ''))
    if criteria:
        body += '<details><summary>Critères de comparaison</summary><ul>' + ''.join('<li>' + item + '</li>' for item in criteria) + '</ul></details>'
    for row in value['rows']:
        row_labels = _libelles_criteres(row)
        body += '<section><' + sub + '>' + t(candidates[row['configuration_id']]) + '</' + sub + '>'
        verdict = row.get('decision', {}).get('verdict') or row['verdict']
        body += '<p><strong>' + t(VERDICTS.get(verdict, 'À reprendre')) + '</strong> : '
        body += t(_remplacer_criteres(row['reason'], row_labels)) + '</p>'
        if row.get('decision', {}).get('next_action'):
            body += '<p>' + t(row['decision']['next_action']) + '</p>'
        body += '<p>Coût observé : ' + t(_montant(row['cost'])) + ' · coût du jugement : ' + t(_montant(row['judgment']['cost'])) + '.</p>'
        body += '<ul>'
        for finding in row['findings']:
            criterion = row_labels.get(finding['criterion_id'], finding['criterion_id'])
            body += '<li>' + t(criterion + ' : ' + STATUTS.get(finding['status'], finding['status']) + ' · ' + finding['finding']) + '</li>'
        for measure in row['measures']:
            criterion = row_labels.get(measure['criterion_id'], measure['criterion_id'])
            from .fragments import valeur_mesure
            shown = valeur_mesure(measure['value'], measure.get('unit'))
            body += '<li>' + t(criterion + ' : ' + shown) + ('' if measure['reason'] is None else ' (' + t(measure['reason']) + ')') + '</li>'
        body += '</ul><ul>'
        for link in row['proof_links']:
            name = piece_name(row, link, candidates)
            if link['piece_id'] in selected:
                body += '<li><a href="' + t(selected[link['piece_id']]) + '">' + t(name) + '</a></li>'
            else:
                body += '<li>' + t(name) + ' : pièce restreinte, non sélectionnée.</li>'
        body += '</ul><p>Évaluée le ' + t(jour_lisible(row['created_at'])) + ' par ' + t(row['responsible']) + '. Revue professionnelle : '
        body += ('absente' if row['judgment']['professional_review'] == 'ABSENTE' else 'déclarée ; preuve restreinte dans cette projection') + '.</p>'
        body += '<p>' + t('; '.join(row['limits'])) + '</p>'
        body += '<p>' + t(RESTRICTION_PUBLIQUE) + '</p></section>'
    return body


def public_page(value, selected):
    body = '<p>Projection fictive locale S6. Aucun droit de publication réelle ni admission au catalogue.</p>'
    body += '<p>L’aperçu en mémoire reste sans approbation. Le service local ne rend cette projection qu’après '
    body += 'vérification de son reçu fictif et de ses octets exacts.</p>'
    body += projection_body(value, selected)
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width, initial-scale=1"><title>Projection fictive · Bench-X</title>'
            '<link rel="stylesheet" href="style.css"></head><body><a class="skip" href="#main">Aller au contenu</a>'
            '<main id="main">' + body + '</main><footer><nav aria-label="Informations légales">'
            '<a href="/mentions-legales">Mentions légales</a> · <a href="/cgu">Conditions d’utilisation</a> · '
            '<a href="/confidentialite">Confidentialité</a></nav></footer></body></html>').encode('utf-8')
