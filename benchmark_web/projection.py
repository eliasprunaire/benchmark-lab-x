"""Projection publique S6 : corps, page et feuille de style, à octets constants.

Le moteur reçoit ce module par injection (`presentation`) ; il ne l'importe pas.
"""
from html import escape
from pathlib import Path
import re

from benchmark.storage import _strict_json as encode

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


def projection_body(value, selected):
    """Only explicit presentation fields; never serialize a private evaluation object"""
    from benchmark.restitution import ATTRIBUTION
    t = _html_text
    body = '<h1>Comparaison : ' + t(value['need']) + '</h1>'
    body += '<p>Dossier ' + t(value['task']['dossier_id']) + ', version ' + t(value['task']['version'])
    body += ', campagne ' + t(value['campaign_id']) + '.</p><p>' + t(value['result_expected']) + '</p>'
    body += '<p>' + t(value['conclusion']['text']) + '</p><p>' + t(ATTRIBUTION) + '</p>'
    body += '<p>' + t('; '.join(value['conclusion']['limits'])) + '</p>'
    for label, data in (('Couverture de la campagne', value['coverage']), ('Population des rangs', value['population']),
                        ('Conditions communes', value['conditions']), ('Base de coût', value['cost_basis'])):
        body += '<details><summary>' + label + '</summary><pre>' + t(encode(data)) + '</pre></details>'
    body += '<p>Comparaison économique : ' + t(value['economic_status']) + '. Coûts candidats et jugement séparés.</p>'
    for pending in value.get('pending_attempts', []):
        body += '<p>Tentative ' + t(pending['attempt_id']) + ' : ' + t(pending['next_action']) + '</p>'
    body += '<p>Les descriptions des obligations et des erreurs éliminatoires sont publiées comme libellés. '
    body += 'La référence de jugement et les preuves de qualification restent privées ; '
    body += 'ces descriptions seules ne permettent pas de vérifier publiquement la qualification des critères.</p>'
    body += '<p>' + t(RESTRICTION_PUBLIQUE) + '</p>'
    labels = {}
    for row in value['rows']:
        labels.update(_libelles_criteres(row))
    for column in value['columns']:
        label = ('Coût observé' if 'criterion_id' not in column else
                 labels.get(column['criterion_id'], column['definition']['measure']))
        displayed = dict(column, id=label)
        if 'criterion_id' in displayed:
            displayed['criterion_id'] = label
        body += '<details><summary>Critère ' + t(label) + '</summary><pre>' + t(encode(displayed)) + '</pre></details>'
    for row in value['rows']:
        row_labels = _libelles_criteres(row)
        body += '<section><h2>Cas ' + t(row['case_id']) + ' · ' + t(row['configuration_id']) + '</h2>'
        body += '<p>Tentative ' + t(row['attempt_id']) + ', évaluation ' + t(row['evaluation_id'])
        body += ', date ' + t(row['created_at']) + ', responsable ' + t(row['responsible']) + '.</p>'
        decision = row.get('decision', {})
        label = decision.get('verdict') or ('Évaluation à reprendre' if row['verdict'] in (None, 'INDETERMINE') else row['verdict'])
        body += '<p><strong>' + t(label) + '</strong> : ' + t(_remplacer_criteres(row['reason'], row_labels)) + '</p>'
        if decision.get('next_action'):
            body += '<p>' + t(decision['next_action']) + '</p>'
        for label, data in (('Configuration demandée', row['requested_configuration']),
                            ('Configuration observée', row['observed_configuration']),
                            ('Sources des observations', row['observation_sources']), ('Coût candidat', row['cost']),
                            ('Coût de jugement', row['judgment']['cost']), ('Méthode', row['method'])):
            body += '<details><summary>' + label + '</summary><pre>' + t(encode(data)) + '</pre></details>'
        body += '<p>Qualification liée : ' + t(row['qualification_id']) + '. Preuves complètes restreintes.</p>'
        body += '<p>Revue professionnelle : ' + ('ABSENTE' if row['judgment']['professional_review'] == 'ABSENTE'
                 else 'Déclarée ; preuve restreinte dans cette projection') + '.</p>'
        body += '<ul>'
        for finding in row['findings']:
            criterion = row_labels.get(finding['criterion_id'], finding['criterion_id'])
            body += '<li>' + t(criterion + ' : ' + finding['status'] + ' · ' + finding['finding']) + '</li>'
        for measure in row['measures']:
            data = {k: measure[k] for k in ('criterion_id', 'value', 'unit', 'rank', 'reason')}
            data['criterion_id'] = row_labels.get(data['criterion_id'], data['criterion_id'])
            body += '<li>' + t(encode(data)) + '</li>'
        body += '</ul><ul>'
        for link in row['proof_links']:
            if link['piece_id'] in selected:
                body += '<li><a href="' + t(selected[link['piece_id']]) + '">' + t(link['name']) + ' · octets exacts</a></li>'
            else:
                body += '<li>' + t(link['name']) + ' : pièce restreinte, non sélectionnée.</li>'
        body += '</ul><p>' + t('; '.join(row['limits'])) + '</p>'
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
            '<main id="main">' + body + '</main></body></html>').encode('utf-8')
