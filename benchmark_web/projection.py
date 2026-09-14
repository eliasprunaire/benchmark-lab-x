"""Projection publique S6 : corps, page et feuille de style, à octets constants.

Le moteur reçoit ce module par injection (`presentation`) ; il ne l'importe pas.
"""
from html import escape
from pathlib import Path

from benchmark.storage import _strict_json as encode

STYLESHEET_PATH = Path(__file__).with_name('static') / 'preparation.css'


def stylesheet():
    return STYLESHEET_PATH.read_bytes()


def _html_text(value):
    return escape(str(value), quote=True)


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
    body += '<p>Vérification publique restreinte : les pièces non sélectionnées et leurs passages restent privés. '
    body += 'Leur empreinte ne remplace pas une preuve consultable. Les constats qui en dépendent restent invérifiables ici.</p>'
    for column in value['columns']:
        body += '<details><summary>Critère ' + t(column['id']) + '</summary><pre>' + t(encode(column)) + '</pre></details>'
    for row in value['rows']:
        body += '<section><h2>Cas ' + t(row['case_id']) + ' · ' + t(row['configuration_id']) + '</h2>'
        body += '<p>Tentative ' + t(row['attempt_id']) + ', évaluation ' + t(row['evaluation_id'])
        body += ', date ' + t(row['created_at']) + ', responsable ' + t(row['responsible']) + '.</p>'
        decision = row.get('decision', {})
        label = decision.get('verdict') or ('Évaluation à reprendre' if row['verdict'] in (None, 'INDETERMINE') else row['verdict'])
        body += '<p><strong>' + t(label) + '</strong> : ' + t(row['reason']) + '</p>'
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
            body += '<li>' + t(finding['criterion_id'] + ' : ' + finding['status'] + ' · ' + finding['finding']) + '</li>'
        for measure in row['measures']:
            data = {k: measure[k] for k in ('criterion_id', 'value', 'unit', 'rank', 'reason')}
            body += '<li>' + t(encode(data)) + '</li>'
        body += '</ul><ul>'
        for link in row['proof_links']:
            if link['piece_id'] in selected:
                body += '<li><a href="' + t(selected[link['piece_id']]) + '">' + t(link['name']) + ' · octets exacts</a></li>'
            else:
                body += '<li>' + t(link['name']) + ' : pièce restreinte, non sélectionnée.</li>'
        body += '</ul><p>' + t('; '.join(row['limits'])) + '</p></section>'
    return body


def public_page(value, selected):
    body = '<p>Projection fictive locale S6. Aucun droit de publication réelle ni admission au catalogue.</p>'
    body += '<p>L’aperçu en mémoire reste sans approbation. Le service local ne rend cette projection qu’après '
    body += 'vérification de son reçu fictif et de ses octets exacts.</p>'
    body += projection_body(value, selected)
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width, initial-scale=1"><title>Projection fictive · Benchmark Lab-X</title>'
            '<link rel="stylesheet" href="style.css"></head><body><a class="skip" href="#main">Aller au contenu</a>'
            '<main id="main">' + body + '</main></body></html>').encode('utf-8')
