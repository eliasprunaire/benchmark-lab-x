"""Rendu HTML du parcours privé : formulaires natifs, preuves inertes, gabarit unique.

Ce module ne touche ni au stockage, ni aux secrets, ni aux fournisseurs : il met en
forme les vues structurées renvoyées par l'exécuteur.
"""
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import re
import secrets
from urllib.parse import urlencode

from benchmark import VERSION
from benchmark.model_catalog import RETIREMENT_NOTICE
from benchmark.preparation import binding
from benchmark.storage import _strict_json as encode

from .projection import projection_body

TEMPLATE_PATH = Path(__file__).with_name('templates') / 'preparation.html'
STYLESHEET_PATH = Path(__file__).with_name('static') / 'preparation.css'
FONTS_PATH = Path(__file__).with_name('static') / 'fonts'
SOURCE_SHA = ''

MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin',
        'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')

VERDICT_BADGES = {'SATISFAIT': ('b-ok', 'i-check', 'Satisfait'), 'NE SATISFAIT PAS': ('b-ko', 'i-cross', 'Ne satisfait pas'),
                  None: ('b-ind', 'i-help', 'À reprendre')}


def icon(name):
    return '<svg class="ico" aria-hidden="true"><use href="#' + name + '"/></svg>'


def badge(verdict):
    """Verdict d'une tentative sous forme de badge lisible sans couleur"""
    tone, name, label = VERDICT_BADGES.get(verdict, ('b-unk', 'i-help', str(verdict)))
    return '<span class="badge ' + tone + '">' + icon(name) + escape(label, quote=True) + '</span>'


def state_block(tone, eyebrow, heading, body, actions=''):
    """Bloc « Où j'en suis » : une icône, une ligne d'état, la prochaine action"""
    names = {'action': 'i-pen', 'wait': 'i-clock', 'warn': 'i-alert', 'err': 'i-alert', 'done': 'i-check', 'unk': 'i-help'}
    return ('<div class="state ' + tone + '" role="status"><span class="ic">' + icon(names[tone]) + '</span>'
            '<p class="eyebrow">' + escape(eyebrow, quote=True) + '</p><h2>' + escape(heading, quote=True) + '</h2>'
            + body + (('<div class="actions">' + actions + '</div>') if actions else '') + '</div>')


def date_lisible_utc(value):
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return str(value)
    return f'{moment.day} {MOIS[moment.month - 1]} {moment.year} à {moment:%H:%M} UTC'

COMPARISON_FOCUS_SCRIPT = """document.addEventListener('click', event => {
  const link = event.target.closest('tr[id] a[href]');
  if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  history.replaceState({...history.state, comparisonFocus: link.closest('tr').id}, '');
});
window.addEventListener('pageshow', () => {
  const row = document.getElementById(history.state?.comparisonFocus);
  if (row) row.focus({preventScroll: true});
});"""


def readable_fields(value):
    """Present structured evidence as inert, labelled fields"""
    labels = {'requested_configuration': 'Configuration demandée', 'observed_configuration': 'Configuration observée',
              'observation_sources': 'Sources des observations', 'model': 'Modèle', 'provider': 'Fournisseur',
              'revision': 'Révision', 'access': 'Accès', 'channel_id': 'Canal', 'route': 'Route',
              'effort': 'Effort de raisonnement', 'parameters': 'Paramètres', 'max_tokens': 'Limite de tokens',
              'temperature': 'Température', 'source': 'Source', 'unit': 'Unité', 'value': 'Valeur',
              'measure': 'Mesure', 'proof': 'Preuve', 'favorable': 'Sens favorable', 'aggregation': 'Agrégation',
              'scope': 'Périmètre', 'attempts': 'Tentatives', 'conversion': 'Conversion', 'frozen_at': 'Date de gel',
              'environment': 'Environnement', 'package': 'Paquet', 'version': 'Version', 'status': 'État',
              'criteria': 'Critères', 'obligations': 'Obligations',
              'eliminatory_errors': 'Erreurs éliminatoires', 'result_expected': 'Résultat attendu',
              'panel': 'Modèles comparés', 'limits': 'Limites', 'pi': 'Harnais Pi',
              'conditions': 'Conditions'}
    if isinstance(value, dict):
        return ('<dl class="evidence-fields">' + ''.join(
            '<dt>' + escape(labels.get(key, key.replace('_', ' ')), quote=True) + '</dt><dd>' + readable_fields(item) + '</dd>'
            for key, item in value.items()) + '</dl>') if value else '<span>Non renseigné</span>'
    if isinstance(value, list):
        return ('<ul>' + ''.join('<li>' + readable_fields(item) + '</li>' for item in value) + '</ul>') if value else '<span>Aucun élément déclaré</span>'
    if value is None:
        value = 'Non renseigné'
    elif type(value) is bool:
        value = 'Oui' if value else 'Non'
    return '<span class="verbatim">' + escape(str(value), quote=True) + '</span>'


def render_evaluations(evaluations, dossier_url):
    """Inert evidence and correction history inside the owner's existing page"""
    def text(value):
        return escape(str(value), quote=True)

    content = '<h4>Verdicts et preuves</h4><p>Évaluations fictives, par cas et tentative. '
    content += 'Le verdict porte sur la configuration observée sous les conditions communes ; '
    content += 'il ne prouve ni une propriété du modèle seul ni une compétence métier générale.</p>'
    for record in evaluations:
        eid = record['evaluation_id']
        spec = record['qualification']['contract']['specification']
        labels = {item['id']: item['description'] for item in spec['obligations'] + spec['eliminatory_errors']}
        reason = re.sub(r'(?<!\w)(' + '|'.join(map(re.escape, labels)) + r')(?!\w)',
                        lambda match: labels[match[0]], record['reason'])
        label = ('Évaluation à reprendre (valeur historique : INDETERMINE)' if record['verdict'] == 'INDETERMINE'
                 else record['verdict'] or 'Évaluation à reprendre')
        content += '<section id="evaluation-' + text(eid) + '"><h5>' + text(label) + '</h5>'
        content += '<p role="status">' + text(reason) + '</p><details><summary>Identifiants de cette évaluation</summary><p>Cas ' + text(record['case_id'])
        content += ', configuration ' + text(record['configuration_id']) + ', évaluation ' + text(eid) + '.</p></details>'
        content += '<p>Responsable : ' + text(record['responsible']) + '. Date : ' + text(record['created_at']) + '.</p>'
        previous = record['previous_evaluation_id']
        if previous:
            content += '<details><summary>Évaluation précédente</summary><p>Correction de <a href="#evaluation-' + text(previous) + '">' + text(previous) + '</a>.</p></details>'
        content += '<ul>'
        for finding in record['findings']:
            content += '<li>' + text(finding['finding'])
            content += '<details><summary>Contrôle et attribution</summary><p>' + text(finding['criterion_id'] + ' / ' + finding['control_id'])
            content += ' : ' + text(finding['status']) + ', attribution ' + text(finding['attribution'])
            content += '</p></details>'
            for proof in finding['evidence']:
                link = next(p for p in record['proof_links'] if p['piece_id'] == proof['piece_id'])
                content += '<details><summary>Passage de ' + text(link['name']) + '</summary>'
                content += '<pre>' + text(proof['passage']) + '</pre>'
                target = ('#proof-' + eid + '-' + link['piece_id']
                          if link['piece_id'] in record.get('proof_contents', {}) else link['href'])
                content += '<p><a href="' + text(target) + '">Ouvrir la pièce exacte</a></p></details>'
            content += '</li>'
        content += '</ul><p>Pièces liées à cette évaluation, accessibles dans votre session :</p><ul>'
        for link in record['proof_links']:
            content += '<li>'
            if link['piece_id'] in record.get('proof_contents', {}):
                content += '<details class="proof-content" id="proof-' + text(eid + '-' + link['piece_id'])
                content += '"><summary>Lire la pièce complète : ' + text(link['name']) + '</summary>'
                content += '<div class="proof-text">' + text(record['proof_contents'][link['piece_id']]) + '</div>'
                content += '<p><a href="' + text(dossier_url) + '">Revenir à la comparaison avec ses filtres</a></p></details>'
            else:
                content += '<a href="' + text(link['href']) + '">' + text(link['name']) + '</a>'
            content += '</li>'
        content += '</ul><h6>Mesures prévues</h6><ul>'
        for measure in record['measures']:
            content += '<li>' + text(measure['definition']['measure']) + ' : '
            content += readable_fields('INCONNU' if measure['status'] == 'UNKNOWN' else measure['value'])
            content += ('' if measure['unit'] in ('bool', 'boolean', 'booléen') else ' ' + text(measure['unit']))
            content += '<details><summary>Définition et preuve source</summary>' + readable_fields(measure) + '</details></li>'
        content += '</ul><p>Aucune synthèse de plusieurs cas ou tentatives calculée.</p>'
        for label, cost in (('Dépense candidate', record['candidate_cost']), ('Dépense de jugement', record['judgment']['cost'])):
            content += '<p>' + label + ' : ' + text('INCONNU' if cost is None or cost['status'] == 'UNKNOWN' else cost['amount'] + ' ' + cost['currency'])
            content += '. Source : ' + text(cost['source'] if cost else 'INCONNU') + '.</p>'
        content += '<p>Limites : ' + text('; '.join(record['limits'])) + '</p>'
        for label, value in (('Méthode et qualification du contrat exact', record['qualification']),
                             ('Jugement, consignes, pièces vues, désaccords et arbitrages', record['judgment']),
                             ('Liens connus entre préparation, jugement et candidat', record['configuration_links']),
                             ('Configurations demandée et observée, sources', {k: record[k] for k in ('requested_configuration', 'observed_configuration', 'observation_sources')}),
                             ('Portée du coût et règle d’agrégation', {k: record[k] for k in ('cost_basis', 'aggregation')})):
            content += '<details><summary>' + label + '</summary>' + readable_fields(value) + '</details>'
        content += '<p><a href="' + text(dossier_url) + '">' + ('Revenir à la comparaison' if '/campaigns/' in dossier_url else 'Revenir au cas d’usage') + '</a></p></section>'
    return content


def render_task_index(task):
    def text(value):
        return escape(str(value), quote=True)

    content = '<p><a href="' + text(task['href']) + '">' + text(task['need']) + '</a></p>'
    content += '<p>Cas d’usage ' + text(task['dossier_id']) + '. Consultation privée, sans admission au catalogue public.</p>'
    content += '<p>Révisions : ' + ' · '.join(
        '<a href="' + text(task['href']) + '/revisions/' + str(revision) + '">' + str(revision) + '</a>'
        for revision in task['revisions']) + '.</p>'
    for version in task['versions']:
        content += '<section id="version-' + text(version['version']) + '"><h3>Version d’épreuve '
        content += text(version['version']) + '</h3>'
        content += '<p><a href="' + text(task['href']) + '/revisions/' + str(version['revision']) + '">Ouvrir la révision associée</a></p><ul>'
        for campaign in version['campaigns']:
            content += '<li><a href="' + text(campaign['href']) + '">Comparer la campagne ' + text(campaign['campaign_id']) + '</a></li>'
        content += '</ul>' if version['campaigns'] else '</ul><p>Aucune campagne pour cette version.</p>'
        content += '</section>'
    if not task['versions']:
        content += '<p>Aucune version d’épreuve contractuelle conservée.</p>'
    return content


def _numeric(value):
    try:
        return float(value) >= 0
    except (TypeError, ValueError):
        return False


def cost_bar(value, known):
    """Barre proportionnelle au coût le plus élevé connu du même cas ; vide si inconnu"""
    if value is None or not _numeric(value) or not known or max(known) <= 0:
        return '<div class="costbar none" aria-hidden="true"></div>'
    return '<div class="costbar" aria-hidden="true" style="--w:' + str(round(100 * float(value) / max(known))) + '%"></div>'


def render_comparison(value):
    def text(value):
        return escape(str(value), quote=True)

    def data(label, value):
        return '<details><summary>' + text(label) + '</summary>' + readable_fields(value) + '</details>'

    def metric(value):
        source = 'INCONNU' if value['value'] is None else value['value']
        content = '<span class="source-value">' + readable_fields(source)
        content += ('' if value['unit'] in ('bool', 'boolean', 'booléen') else ' ' + text(value['unit'])) + '</span>'
        if value['rank'] is None:
            return content + '<p>Sans rang : ' + text(value['reason']) + '</p>'
        return content + '<p>Rang ' + text(value['rank']) + '</p>'

    base, query = value['href'], value['filter_scope']
    content = '<nav aria-label="Parcours"><a href="/preparation">Mes cas d’usage</a> · '
    content += '<a class="button" href="' + text(value['dossier_href']) + '">Revenir au cas d’usage</a></nav>'
    content += '<p class="hint">Résultats privés · version d’épreuve ' + text(value['task']['version'])
    content += '.</p><details><summary>Identité de la campagne</summary><p>' + text(value['campaign_id']) + '</p></details>'
    content += '<p class="lead">' + text(value['result_expected']) + '</p>'
    content += '<div class="campaign-summary" aria-label="Conclusion de la campagne"><span class="ic">' + icon('i-scale') + '</span>'
    content += '<p class="eyebrow">Le verdict ne fait pas de moyenne</p>'
    latest = {record['attempt_id']: record for record in value['history']}
    # Décompter les verdicts conservés par cas, sans créer de verdict agrégé
    for case_number, case in enumerate(value['cases'], 1):
        records = [record for record in latest.values() if record['case_id'] == case['id']]
        if records:
            counts = [str(sum(record['decision']['verdict'] == verdict for record in records)) + ' ' + label
                      for verdict, label in (('SATISFAIT', 'satisfait(s)'), ('NE SATISFAIT PAS', 'non satisfait(s)'),
                                             (None, 'évaluation(s) à reprendre'))
                      if any(record['decision']['verdict'] == verdict for record in records)]
            content += '<p><strong>Cas ' + text(case_number) + '</strong> : ' + text(' · '.join(counts)) + '.</p>'
    if not latest:
        content += '<p>Aucun résultat évalué pour cette campagne.</p>'
    coverage = value['coverage']
    content += '<p role="status">' + text(coverage['evaluated_attempts']) + ' tentative(s) évaluée(s) · '
    content += text(coverage['attempted_cells']) + ' essai(s) lancé(s) sur ' + text(coverage['planned_cells'])
    content += ' prévu(s) · ' + text(coverage['not_started']) + ' non lancé(s). '
    content += ('Comparaison des coûts complète.' if value['economic_status'] == 'COMPLETE' else 'Comparaison des coûts incomplète.') + '</p>'
    dates = sorted(set(date[:10] for date in value.get('acquisition_dates', [])))
    if dates:
        content += '<p class="hint">Réponses reçues : ' + text(dates[0] if len(dates) == 1 else dates[0] + ' au ' + dates[-1]) + '.</p>'
    content += '<p class="hint">Verdicts par cas et tentative, sans conclusion globale. '
    content += 'Un coût inconnu ne change pas le verdict. <a href="#method">Méthode et limites</a></p></div>'
    content += '<details id="filters" class="comparison-filters"><summary>Tris et filtres'
    content += (' · ' + text(len(query)) + ' sélection(s) active(s)' if query else '') + '</summary>'
    content += '<p id="filter-help">Chaque bouton applique le champ choisi et conserve les autres sélections. '
    content += 'Les filtres changent seulement les lignes visibles. Les rangs et la couverture gardent la population entière.</p>'
    sort_label = next((('Coût observé' if 'criterion_id' not in column else column['definition']['measure'])
                       for column in value['columns'] if column['id'] == query.get('sort')), 'descriptif, sans préférence')
    content += '<p>Ordre actuel : ' + text(sort_label)
    content += (', ' + ('décroissant' if query.get('direction') == 'desc' else 'croissant') if 'sort' in query else '') + '.</p>'
    options = {
        'case': ('Cas', [(v['id'], 'Cas ' + str(number))
                         for number, v in enumerate(value['cases'], 1)]),
        'sort': ('Critère de tri', [(v['id'], 'Coût observé' if 'criterion_id' not in v else v['definition']['measure']) for v in value['columns']]),
        'direction': ('Ordre d’affichage', [('asc', 'Croissant'), ('desc', 'Décroissant')]),
        'verdict': ('Décision ou travail restant', [('SATISFAIT', 'SATISFAIT'), ('NE SATISFAIT PAS', 'NE SATISFAIT PAS'), ('A_REPRENDRE', 'À reprendre')]),
        'obligation': ('Constat par obligation', [(v['id'] + ':' + s, v['id'] + ' : ' + s)
                        for v in value['obligations'] for s in ('PASS', 'FAIL', 'INDETERMINE')]),
        'configuration': ('Configuration', [(v['id'], v['model'] + ' · ' + v['id']) for v in value['panel']]),
    }
    if query:
        content += '<ul aria-label="Sélections actives">'
        for key, val in query.items():
            remaining = {k: v for k, v in query.items() if k != key}
            target = base + ('?' + urlencode(remaining) if remaining else '') + '#filters'
            label = next((label for option, label in options[key][1] if option == val), val)
            content += '<li>' + text(options[key][0] + ' : ' + label) + ' · <a href="' + text(target)
            content += '">Enlever ' + text(options[key][0].lower()) + '</a></li>'
        content += '</ul>'
    else:
        content += '<p>Aucun filtre ni tri appliqué.</p>'
    content += '<div class="filter-grid">'
    for key, (label, values) in options.items():
        if not values:
            continue
        content += '<form method="get" action="' + text(base) + '" aria-describedby="filter-help">'
        for k, v in query.items():
            if k != key:
                content += '<input type="hidden" name="' + text(k) + '" value="' + text(v) + '">'
        content += '<label for="filter-' + key + '">' + label + '</label><select id="filter-' + key + '" name="' + key + '">'
        for val, title in values:
            content += '<option value="' + text(val) + '"' + (' selected' if query.get(key) == val else '') + '>' + text(title) + '</option>'
        content += '</select><button type="submit">Appliquer : ' + label.lower() + '</button></form>'
    content += '</div><p><a href="' + text(base) + '#filters">Enlever tous les filtres et le tri</a></p></details>'
    content += '<p class="view-scope">' + text(len(value['rows'])) + ' ligne(s) affichée(s) sur '
    content += text(len(value['population'])) + ' tentatives évaluées · ordre ' + text(sort_label)
    content += (', décroissant' if query.get('direction') == 'desc' else ', croissant') if 'sort' in query else ''
    content += '. Les filtres ne changent pas le bilan de campagne.</p>'
    if not value['rows']:
        content += '<p role="status">' + ('Aucune ligne ne correspond aux filtres ; les observations de la campagne restent conservées.'
                     if value['population'] else 'Aucune tentative évaluée dans cette campagne.') + '</p>'
    for case_number, case in enumerate(value['cases'], 1):
        rows = [r for r in value['rows'] if r['case_id'] == case['id']]
        if not rows:
            continue
        content += '<section class="comparison-results"><h2>Cas ' + text(case_number) + '</h2>'
        content += '<p class="table-hint">Sur petit écran, faites défiler le tableau horizontalement pour lire coûts et preuves.</p>'
        content += '<div class="table-scroll" role="region" tabindex="0" aria-label="Observations du cas ' + text(case_number) + '">'
        content += '<table><caption>Cas ' + text(case_number) + ' · valeurs par tentative, sans agrégation</caption><thead><tr>'
        for title in ('Configuration et tentative', 'Verdict et motif', 'Coût observé', 'Mesures prévues', 'Preuves'):
            content += '<th scope="col">' + title + '</th>'
        content += '</tr></thead><tbody>'
        known = [float(r['cost']['value']) for r in rows if r['cost']['value'] is not None and _numeric(r['cost']['value'])]
        for row in rows:
            content += '<tr id="attempt-' + text(row['attempt_id']) + '"' + ('' if row['verdict'] == 'SATISFAIT' else ' class="out"') + ' tabindex="-1"><th scope="row">'
            content += '<strong>' + text(row['requested_configuration']['model']) + '</strong>'
            content += data('Demandée, observée et sources', {k: row[k] for k in ('requested_configuration', 'observed_configuration', 'observation_sources')}) + '</th>'
            spec = row['qualification']['contract']['specification']
            labels = {item['id']: item['description'] for item in spec['obligations'] + spec['eliminatory_errors']}
            reason = re.sub(r'(?<!\w)(' + '|'.join(map(re.escape, labels)) + r')(?!\w)',
                            lambda match: labels[match[0]], row['reason'])
            content += '<td>' + badge(row['verdict']) + '<p>' + text(reason) + '</p>'
            if row.get('decision', {}).get('next_action'):
                content += '<p>' + text(row['decision']['next_action']) + '</p>'
            if row['incident']:
                content += '<p>Incident : ' + text(row['incident']) + '</p>'
            content += '</td><td>' + metric(row['cost']) + cost_bar(row['cost']['value'], known) + '</td><td>'
            for measure in row['measures']:
                content += '<p>' + text(measure['definition']['measure']) + '</p>' + metric(measure)
            content += '</td><td><a href="' + text(row['detail_href']) + '">Détail et preuves</a></td></tr>'
        content += '</tbody></table></div></section>'
    content += '<details id="method"><summary>Méthode, critères et limites</summary>'
    content += '<p>' + text(value['conclusion']['attribution']) + '</p><p>' + text('; '.join(value['conclusion']['limits'])) + '</p>'
    content += '<p>Travail humain restant : ' + text(value['human_work']) + '</p>'
    content += '<p>Les rangs comparent seulement les valeurs connues d’un même cas. Les égalités sont conservées ; '
    content += 'les valeurs inconnues ou incompatibles restent sans rang. Aucun choix automatique ni total multi-cas.</p>'
    for pending in value.get('pending_attempts', []):
        content += '<p>Tentative ' + text(pending['attempt_id']) + ' : ' + text(pending['next_action']) + '</p>'
    content += '<p>' + badge('SATISFAIT') + ' obligations prouvées. ' + badge('NE SATISFAIT PAS') + ' défaut établi. ' + badge(None) + ' pas encore de verdict métier.</p>'
    for column in value['columns']:
        content += data('Définition, unité, sens favorable et preuve : ' + column['id'], column)
    content += data('Population entière utilisée pour les rangs, conservée après filtrage', value['population'])
    content += data('Cellules prévues et couverture manquante', value['cells'])
    content += data('Conditions communes et date de gel', value['conditions'])
    content += data('Dates de réception', value.get('acquisition_dates', []))
    content += data('Contrat et portée exacte de la conclusion', value['conclusion']['scope'])
    content += data('Base de coût et conversion prévue', value['cost_basis'])
    if value.get('stop_reason'):
        content += '<p>Motif d’arrêt enregistré : ' + text(value['stop_reason']) + '</p>'
    content += '<p><a href="' + text(base + '/preview') + '">Examiner un aperçu privé de la projection</a></p></details>'
    return content


def render(value, csrf, path='/preparation', *, error=False):
    """Native HTML forms, inert evidence and a fixed comparison focus script"""
    def text(value):
        return escape(str(value), quote=True)

    def hidden(name, value):
        return f'<input type="hidden" name="{text(name)}" value="{text(value)}">'

    def form(url, fields, content):
        return (f'<form method="post" action="{text(url)}">' + hidden('csrf_token', csrf)
                + ''.join(hidden(k, v) for k, v in fields.items()) + content + '</form>')

    def section(title, content, anchor=None):
        target = '' if anchor is None else f' id="{text(anchor)}"'
        return f'<section{target}><h2>{text(title)}</h2>{content}</section>'

    def listing(values):
        return '<ul>' + ''.join(f'<li>{text(v)}</li>' for v in values) + '</ul>'

    def field_attributes(name):
        return f' aria-describedby="{text(name)}-error"' if value.get('error_field') == name else ''

    def field_error(name):
        if value.get('error_field') != name:
            return ''
        return f'<p id="{text(name)}-error" role="alert">{text(value["error"])}</p>'

    state = value.get('availability', {})
    can_submit = state.get('can_submit', False)
    disabled = '' if can_submit else ' disabled aria-describedby="availability"'
    s9 = value.get('kind') != 'projection_preview'
    navigation = ''
    title = 'Décrire mon cas d’usage'
    current = {'home': '/', 'publication_unavailable': '/index.html'}.get(value.get('kind'), '/preparation')
    menu = ''.join('<a href="' + href + '"' + (' aria-current="page"' if href == current else '') + '>' + label + '</a>'
                   for href, label in (('/', 'Accueil'), ('/preparation', 'Mes cas d’usage'), ('/index.html', 'Comparaisons publiées')))
    if error:
        title = 'Préparation indisponible' if value.get('unavailable') else 'Action non aboutie'
        submitted = value.get('form')
        attached = (value.get('error_field') if type(submitted) is dict
                    and value.get('error_field') in submitted else None)
        content = '' if attached else '<p role="alert">' + text(value['error']) + '</p>'
        if type(submitted) is dict and 'request' in submitted:
            content += form(path, {key: submitted[key] for key in ('dossier_id', 'action_id')},
                '<label for="request">Une tâche de votre travail</label>'
                '<textarea id="request" name="request" required minlength="40" maxlength="1500" rows="5"' + field_attributes('request') + '>' + text(submitted['request']) + '</textarea>' + field_error('request') +
                '<label for="useful">Résultat attendu</label><textarea id="useful" name="useful" maxlength="800" rows="3"' + field_attributes('useful') + '>' + text(submitted.get('useful', '')) + '</textarea>' + field_error('useful') +
                '<label for="context">Contexte utile</label><textarea id="context" name="context" maxlength="200" rows="2"' + field_attributes('context') + '>' + text(submitted.get('context', '')) + '</textarea>' + field_error('context') +
                '<div class="website"><label for="website">Site web</label><input id="website" name="website" autocomplete="off" tabindex="-1"></div>'
                '<button type="submit">Corriger et renvoyer</button>')
        elif type(submitted) is dict and 'message' in submitted:
            content += form(path, {key: submitted[key] for key in ('action_id', 'revision', 'kind')},
                '<label for="message">Votre précision ou correction</label>'
                '<textarea id="message" name="message" required maxlength="1000" rows="4"' + field_attributes('message') + '>' + text(submitted['message']) + '</textarea>' + field_error('message') +
                '<div class="website"><label for="website">Site web</label><input id="website" name="website" autocomplete="off" tabindex="-1"></div>'
                '<button type="submit">Corriger et renvoyer</button>')
        back_class = 'button sec' if type(submitted) is dict and ('request' in submitted or 'message' in submitted) else 'button'
        content += '<p><a class="' + back_class + '" href="/preparation">Retrouver mes cas d’usage</a></p>'
    elif value.get('kind') == 'access':
        title = 'Accès OpenRouter'
        status = value['status']
        if status == 'connected':
            content = state_block('done', 'Accès OpenRouter', 'Compte connecté',
                '<p>Crédit restant : ' + text(value['limit_remaining_usd'] if value['limit_remaining_usd'] is not None else 'INCONNU')
                + ' USD. Limite du compte : ' + text(value['limit_usd'] if value['limit_usd'] is not None else 'INCONNU') + ' USD.</p>')
            content += form('/preparation/access/disconnect', {}, '<button class="sec" type="submit">Déconnecter</button>')
        elif status == 'invalid':
            content = state_block('err', 'Accès OpenRouter', 'Accès invalide',
                                  '<p>Motif : ' + text(value.get('reason') or 'INCONNU') + '.</p>')
            content += form('/preparation/access/start', {'return': path},
                            '<button type="submit">Reconnecter mon compte OpenRouter</button>')
        elif status == 'disconnected':
            content = state_block('action', 'Accès OpenRouter', 'Compte non connecté',
                                  '<p>Connectez votre compte pour financer les appels candidats de votre comparaison.</p>')
            content += form('/preparation/access/start', {'return': path},
                            '<button type="submit">Connecter mon compte OpenRouter</button>')
        else:
            content = state_block('err', 'Accès OpenRouter', 'Connexion indisponible',
                                  '<p>Connexion OpenRouter indisponible.</p>')
        content += '<p><a class="button' + ('' if status in ('connected', 'unavailable') else ' sec') + '" href="/preparation">Revenir à mes cas d’usage</a></p>'
    elif value.get('kind') == 'configurations':
        title = 'Choisir les configurations'
        dossier_url = '/preparation/dossiers/' + value['dossier_id']
        content = '<p><a href="' + text(dossier_url) + '">Revenir au cas d’usage</a></p>'
        content += '<p role="status">Choisissez au moins deux modèles et un palier de raisonnement. Aucun appel candidat ne part à cette étape.</p>'
        if not value.get('catalogue_available', True):
            content += '<p>' + text(value['detail']) + '</p>'
        else:
            content += '<p>Relevé des modèles du ' + text(date_lisible_utc(value['fetched_at'])) + '.</p>'
            choices = ''
            for model in value['models']:
                checked = ' checked' if model['selected'] else ''
                choices += '<label><input type="checkbox" name="models" value="' + text(
                    model['id']) + '"' + checked + '> ' + text(model['name'])
                if model['not_adjustable']:
                    choices += ' · palier de raisonnement non réglable'
                choices += '</label>'
            tiers = ''.join(
                '<label><input type="radio" name="tier" value="' + tier + '"' +
                (' checked' if value['current_tier'] == tier else '') + '> ' +
                ('Standard' if tier == 'standard' else 'Renforcé') + '</label>'
                for tier in value['available_tiers'])
            content += ('<form method="post" action="' + text(dossier_url + '/configurations') + '">' +
                        hidden('csrf_token', csrf) + '<fieldset><legend>Modèles à comparer</legend>' +
                        choices + '</fieldset><fieldset><legend>Palier</legend>' + tiers +
                        '</fieldset><button' + (' class="sec"' if value['configurations'] else '') + ' type="submit">Enregistrer les configurations</button></form>')
        if value['configurations']:
            model_names = {model['id']: model['name'] for model in value['models']}
            summary = '<ul>'
            for configuration in value['configurations']:
                amount = configuration['estimate']['amount_usd']
                technical = configuration['model']
                detail = ' · estimation ' + (
                    'non calculable' if amount is None else amount + ' USD')
                if configuration.get('effort_limit') == 'not_adjustable':
                    detail += ' · palier de raisonnement non réglable'
                summary += '<li><span title="Identifiant technique : ' + text(technical) + '">' + text(
                    model_names.get(technical, technical)) + '</span>' + text(detail) + '</li>'
            summary += '</ul>'
            summary += '<p>Estimation totale : ' + text(
                'non calculable' if value['estimate_total_usd'] is None else
                value['estimate_total_usd'] + ' USD') + '.</p>'
            summary += '<p>Plafond : ' + text(value['cap_usd']) + ' USD.</p>'
            summary += '<p><a class="button" href="' + text(
                dossier_url + '/campaigns/' + value['current_campaign_id'] +
                '/conditions') + '">Voir le récapitulatif</a></p>'
            content += section('Sélection courante', summary)
    elif value.get('kind') == 'campaign_launch' and 'checks' in value:
        campaign = value['campaign']
        dossier_url = '/preparation/dossiers/' + value['dossier_id']
        base = dossier_url + '/campaigns/' + campaign['campaign_id']
        title = 'Vérifier puis lancer la comparaison'
        content = '<p><a href="' + text(dossier_url) + '">Revenir au cas d’usage</a></p>'
        content += section('Ce qui sera testé', '<p>' + text(value['criteria']['result_expected']) + '</p>' +
            listing(item['model'] for item in campaign['panel']) +
            '<p>Chaque modèle reçoit la même consigne et les mêmes pièces. Le verdict reste limité à cet exemple et aux configurations observées.</p>' +
            '<details><summary>Critères et conditions exactes</summary>' + readable_fields(
                {'criteria': value['criteria'], 'conditions': campaign['conditions'], 'panel': campaign['panel']}) + '</details>')
        content += '<p>Les appels candidats sont financés par votre accès OpenRouter. Estimation, plafond et coût observé sont distincts ; le plafond ne garantit pas une limite absolue de facturation.</p>'
        check_content = '<ul>'
        for check in value['checks']:
            detail = check['detail']
            if type(detail) is dict:
                detail = ('Crédit restant : ' + str(detail.get('limit_remaining_usd')
                          if detail.get('limit_remaining_usd') is not None else 'INCONNU') +
                          ' USD ; limite du compte : ' + str(detail.get('limit_usd')
                          if detail.get('limit_usd') is not None else 'INCONNU') + ' USD')
            check_content += '<li>' + text(('✓ ' if check['ok'] else '✕ ') + str(detail))
            if check['key'] == 'example_qualified' and check.get('findings'):
                check_content += '<p>Constats de qualification</p>' + listing(
                    finding['text'] for finding in check['findings'])
            check_content += '</li>'
        check_content += '</ul>'
        content += section('Contrôles avant lancement', check_content)
        content += '<p>Plafond actuel : ' + text(value['cap_usd']) + ' USD.</p>'
        if not campaign['attempts']:
            content += section('Modifier le plafond',
                '<form method="post" action="' + text(base + '/cap') + '">' + hidden('csrf_token', csrf) +
                '<label for="cap_usd">Plafond en USD, de 0,10 à 100</label>' +
                '<input id="cap_usd" name="cap_usd" type="number" min="0.10" max="100.00" step="0.01" value="' +
                text(value['cap_usd']) + '" required><button class="sec" type="submit">Modifier le plafond</button></form>')
        failed = next((check for check in value['checks'] if not check['ok']), None)
        if campaign['attempts']:
            received = all(cell['state'] == 'RECEIVED' for cell in campaign['cells'])
            content += '<p role="status">Lancement enregistré. ' + (
                'Toutes les réponses sont reçues ; consultez les évaluations disponibles.' if received else
                'Les essais sont en attente ou en cours ; actualisez pour suivre leur avancement.') + '</p>'
            states = {'NOT_STARTED': 'non démarré', 'INTENT_RECORDED': 'en attente',
                      'EMISSION_POSSIBLE': 'en cours', 'RECEIVED': 'réponse reçue',
                      'AMBIGUOUS': 'état incertain, vérification requise'}
            models = {item['id']: item['model'] for item in campaign['panel']}
            content += section('Suivi des essais', listing(
                models[cell['configuration_id']] + ' : ' + states[cell['state']] for cell in campaign['cells']))
            if not campaign['admission_open']:
                content += '<p>Les nouveaux appels sont fermés. Les réponses reçues restent consultables.</p>'
            content += '<p><a class="button' + (' sec' if received else '') + '" href="' + text(base + '/conditions') + '">Actualiser le suivi</a> '
            content += '<a class="button' + ('' if received else ' sec') + '" href="' + text(base) + '">Comparer les résultats et lire les preuves</a></p>'
        elif value['launchable']:
            content += '<p role="status">Les contrôles sont satisfaits. Vérifiez le travail, les modèles et le plafond avant de confirmer le lancement.</p>'
            content += form(base + '/start', {
                'manifest_version': campaign['version'],
                'frozen_at': campaign['conditions']['frozen_at']},
                '<label><input type="checkbox" name="confirm" value="yes" required> '
                'Je confirme le lancement de cette comparaison.</label>'
                '<button type="submit">Lancer la comparaison</button>')
        elif failed:
            links = {
                'example_validated': dossier_url + '#validation',
                'example_qualified': dossier_url,
                'configurations_available': dossier_url + '/configurations',
                'access_connected': '/preparation/access',
                'estimate_under_cap': dossier_url + '/configurations',
            }
            content += '<p role="status">Lancement indisponible : ' + text(
                failed['detail'] if type(failed['detail']) is str else
                'connectez votre accès OpenRouter') + '. <a class="button" href="' + text(
                links[failed['key']]) + '">Compléter cette étape</a></p>'
        else:
            content += '<p role="status">Lancement indisponible. Le responsable doit vérifier la disponibilité de l’exécution.</p>'
            content += '<p><a class="button" href="' + text(dossier_url) + '">Revenir au cas d’usage</a></p>'
    elif value.get('kind') == 'campaign_launch':
        campaign = value['campaign']
        base = '/preparation/dossiers/' + value['dossier_id'] + '/campaigns/' + campaign['campaign_id']
        title = 'Examiner puis lancer la comparaison'
        content = '<p>Le responsable prépare et autorise cette comparaison. Votre confirmation déclenche uniquement les essais qu’il a admis.</p>'
        content += '<p><a href="' + text('/preparation/dossiers/' + value['dossier_id']) + '">Revenir au cas d’usage</a></p>'
        groups = '<div class="crit">'
        if value['criteria']['eliminatory_errors']:
            groups += '<div class="grp elim"><h3>' + badge('NE SATISFAIT PAS') + 'Erreurs éliminatoires</h3>' + listing(item['description'] for item in value['criteria']['eliminatory_errors']) + '</div>'
        groups += '<div class="grp oblig"><h3>' + badge('SATISFAIT') + 'Obligations à prouver</h3>' + listing(item['description'] for item in value['criteria']['obligations']) + '</div></div>'
        content += section('Critères fixés', '<p>' + text(value['criteria']['result_expected']) + '</p>' + groups)
        content += section('Modèles et conditions', listing([item['model'] + ' · ' + item['revision'] for item in campaign['panel']]) +
            '<p>Pi : ' + text(campaign['conditions']['pi']['package']) + ' · ' + text(campaign['conditions']['pi']['version']) +
            '. Conditions figées le ' + text(campaign['conditions']['frozen_at']) + '.</p>' +
            '<details><summary>Configurations et conditions exactes</summary><pre>' + text(encode(dict(panel=campaign['panel'], conditions=campaign['conditions']))) + '</pre></details>')
        access = value.get('access', {'status': 'unavailable'})
        status = access.get('status')
        if status == 'connected':
            access_content = '<p>Compte OpenRouter connecté. Crédit restant : ' + text(
                access.get('limit_remaining_usd') if access.get('limit_remaining_usd') is not None else 'INCONNU') + ' USD.</p>'
            access_content += form('/preparation/access/disconnect', {},
                                   '<button type="submit">Déconnecter</button>')
        elif status == 'invalid':
            access_content = '<p>Accès OpenRouter invalide : ' + text(access.get('reason') or 'INCONNU') + '.</p>'
            access_content += form('/preparation/access/start', {'return': base + '/conditions'},
                                   '<button type="submit">Reconnecter mon compte OpenRouter</button>')
        elif status == 'disconnected':
            access_content = '<p>Compte OpenRouter non connecté.</p>'
            access_content += form('/preparation/access/start', {'return': base + '/conditions'},
                                   '<button type="submit">Connecter mon compte OpenRouter</button>')
        else:
            access_content = '<p>Connexion OpenRouter indisponible.</p>'
        content += section('Accès OpenRouter', access_content)
        estimate = value['estimate']
        content += '<h2>Coût et autorisation</h2><p>Estimation indicative : ' + text(
            estimate['amount'] + ' ' + estimate['currency'] if estimate else 'non fournie par le responsable') + '.</p>'
        if estimate:
            content += '<p>' + text(estimate['assumptions']) + ' Source : ' + text(estimate['source']) + '.</p>'
        budget = campaign['budget']
        if budget:
            content += '<p>Enveloppe autorisée : ' + text(budget['limit']) + ' ' + text(budget['currency']) + '.</p>'
        content += '<p>Réserves prévues par essai : ' + text(', '.join(key + ' : ' + amount for key, amount in campaign['reserve_amounts'].items()) if campaign['reserve_amounts'] else 'non autorisées') + '.</p>'
        content += '<p>Estimation, réservation et coût observé sont distincts. La réserve ne garantit pas un plafond de facturation.</p>'
        if value['can_launch']:
            content += form(base + '/start', {'manifest_version': campaign['version'],
                'frozen_at': campaign['conditions']['frozen_at'], 'admission_id': value['admission_id']},
                '<label><input type="checkbox" name="confirm" value="yes" required> Je confirme le lancement des essais autorisés présentés.</label><button type="submit">Lancer la comparaison autorisée</button>')
        else:
            content += '<p role="status">' + ('Lancement enregistré. Consultez les essais et leurs résultats ci-dessous.' if campaign['attempts'] else 'Lancement indisponible. Le responsable doit vérifier les autorisations et la disponibilité de l’exécution.') + '</p>'
        content += section('Suivi des essais', listing([cell['cell_id'] + ' : ' + {'NOT_STARTED': 'non démarré', 'INTENT_RECORDED': 'en attente', 'EMISSION_POSSIBLE': 'en cours', 'RECEIVED': 'réponse reçue, consulter l’évaluation', 'AMBIGUOUS': 'état incertain, vérification requise'}.get(cell['state'], cell['state']) for cell in campaign['cells']]))
        content += '<p><a href="' + text(base + '/conditions') + '">Actualiser le suivi</a> · <a href="' + text(base) + '">Comparer les résultats et lire les preuves</a></p>'
    elif value.get('kind') == 'home':
        title = 'Quel modèle pour votre travail ?'
        content = '<div class="hero"><p class="lead" role="status">Décrivez une tâche de votre travail, sans donnée personnelle ni information confidentielle. '
        content += 'Nous préparons avec vous un exemple entièrement inventé, puis les modèles sont comparés dans les mêmes conditions, '
        content += 'sur des critères vérifiables et leur coût observé.</p>'
        content += '<div class="actions"><a class="button" href="/preparation">' + icon('i-pen') + 'Décrire mon cas d’usage</a>'
        content += '<a class="button sec" href="/preparation">Retrouver mes cas d’usage</a></div></div>'
        content += section('Le parcours en quatre étapes', '<div class="tiles">'
            '<div class="tile"><h3>Besoin</h3><p>Vous décrivez la tâche et le résultat utile. L’assistant pose des questions si nécessaire.</p></div>'
            '<div class="tile"><h3>Exemple</h3><p>Une consigne et des pièces inventées vous sont proposées. Vous corrigez jusqu’à ce que l’exemple soit fidèle.</p></div>'
            '<div class="tile"><h3>Validation</h3><p>Vous confirmez le travail à tester. La qualification de l’exemple suit ; aucun candidat n’est lancé et rien n’est publié.</p></div>'
            '<div class="tile"><h3>Comparaison</h3><p>Chaque modèle passe l’épreuve dans les mêmes conditions. Vous lisez les verdicts, les preuves et les coûts.</p></div></div>')
        content += section('Ce qui rend le résultat lisible', '<div class="rule">' + icon('i-scale') + '<span><strong>Le verdict ne fait pas de moyenne.</strong> '
            'Une obligation non prouvée ou une erreur éliminatoire suffit à écarter une configuration, quel que soit le reste.</span></div>'
            '<ul><li>Le verdict porte sur la configuration observée sous des conditions communes, jamais sur le nom du modèle seul.</li>'
            '<li>Le coût est observé sur reçu, pas estimé. Un coût inconnu reste inconnu.</li>'
            '<li>Les pièces sont entièrement inventées : aucun dossier réel, même anonymisé.</li></ul>')
        content += section('Comparaisons publiées', '<p>Seules les restitutions approuvées sont accessibles publiquement. '
            'La validation d’un cas d’usage ne publie rien et ne lance aucun candidat.</p>'
            '<p><a href="/index.html">Ouvrir la comparaison publiée, si disponible</a></p>')
    elif value.get('kind') == 'publication_unavailable':
        title = 'Aucune publication vérifiée disponible'
        content = '<p class="lead">Aucun résultat public vérifié n’est disponible à cette adresse pour le moment.</p>'
        content += '<p>Vos cas d’usage et leurs résultats restent privés. Leur consultation ne publie aucune pièce.</p>'
        content += '<div class="actions"><a class="button" href="/">Revenir à l’accueil</a>'
        content += '<a class="button sec" href="/preparation">Retrouver mes cas d’usage</a></div>'
    elif value.get('kind') == 'catalogue':
        title = 'Versions et comparaisons'
        content = '<p class="lead">Index privé de cette session : chaque cas d’usage validé, ses versions d’épreuve et les comparaisons lancées.</p>'
        content += ''.join('<section><h2>' + text(task['need']) + '</h2>' + render_task_index(task) + '</section>' for task in value['tasks'])
        if not value['tasks']:
            content += '<p>Aucun cas d’usage validé dans cette session.</p>'
    elif value.get('kind') == 'comparison':
        title = value['need']
        content = render_comparison(value) + '<script>' + COMPARISON_FOCUS_SCRIPT + '</script>'
    elif value.get('kind') == 'projection_preview':
        title = 'Aperçu privé · NON APPROUVÉ'
        content = '<p role="status">Aperçu privé · NON APPROUVÉ. Aucune activation ni publication.</p>'
        content += '<p><a href="' + text(value['comparison']['href']) + '">Revenir à la comparaison</a></p>'
        content += '<p>Choisissez les pièces à inclure. Aucune pièce cochée : page et styles seulement. '
        content += 'L’aperçu porte sur la campagne entière, sans les filtres de consultation.</p>'
        content += '<form method="get" action="' + text(value['comparison']['href'] + '/preview') + '">'
        content += '<fieldset><legend>Pièces proposées pour la projection</legend>'
        for piece in value['pieces']:
            pid = piece['piece_id']
            content += '<label><input type="checkbox" name="piece" value="' + text(pid) + '"'
            content += (' checked' if pid in value['selected_links'] else '') + '> ' + text(piece['name']) + ' · ' + text(pid) + '</label>'
        content += '</fieldset><button type="submit">Actualiser l’aperçu</button></form>'
        content += '<p>Cette vue privée reprend le contenu de la projection avec des liens privés vers les seules pièces '
        content += 'sélectionnées. Son habillage n’est pas un fichier approuvé. Le reçu fictif devra porter sur les octets du paquet.</p>'
        content += '<hr>' + projection_body(value['comparison'], value['selected_links'])
    elif value.get('kind') == 'attempt_detail':
        title = 'Détail et preuves du résultat'
        content = '<nav aria-label="Retour"><a class="button" href="' + text(value['back_href']) + '">Revenir à la comparaison avec ses filtres</a> · '
        content += '<a href="/preparation">Mes cas d’usage</a></nav><p class="lead">' + text(value['need']) + '</p>'
        content += '<p>Consultation privée · version d’épreuve ' + text(value['task']['version']) + '.</p>'
        content += '<details><summary>Identité de la campagne</summary><p>' + text(value['campaign_id']) + '</p></details>'
        content += '<p>Les pièces exactes et leurs passages restent inertes. Historique conservé ; la dernière évaluation est affichée en premier.</p>'
        content += render_evaluations(list(reversed(value['history'])), value['back_href'])
    elif 'dossiers' in value:
        title = 'Mes cas d’usage'
        content = '<p class="lead" role="status">Décrivez le travail et le résultat qui vous serait utile. Vous pourrez examiner et corriger l’exemple avant de le valider.</p>'
        dossiers = '<ul class="dossiers">' + ''.join(
            f'<li><a href="/preparation/dossiers/{text(d["dossier_id"])}">{text(d.get("need") or "Cas d’usage " + d["dossier_id"])}</a>'
            f'<small>Révision {d["revision"]}</small><a class="button sec" href="/preparation/dossiers/{text(d["dossier_id"])}">Reprendre</a></li>'
            for d in value['dossiers']) + '</ul><p><a href="/preparation/catalogue">Versions d’épreuve et comparaisons de cette session</a></p>' if value['dossiers'] else (
                '<p>Aucun cas d’usage dans ce navigateur. Commencez par décrire un besoin lorsque les appels sont ouverts.</p>'
                '<p>Si vous en aviez déjà un, vérifiez que vous utilisez le même navigateur et son cookie de session.</p>')
        content += section('Décrire un nouveau cas d’usage', form('/preparation/dossiers',
            {'dossier_id': secrets.token_hex(16), 'action_id': secrets.token_hex(16)},
            '<label for="request">Une tâche de votre travail</label><p id="request-help" class="hint">Décrivez le travail et le résultat utile, sans donnée personnelle ni information confidentielle. Aucun dossier réel, même anonymisé.</p>'
            '<textarea id="request" name="request" required minlength="40" maxlength="1500" rows="5" aria-describedby="request-help' + ('"' if can_submit else ' availability" disabled') + '></textarea>'
            '<label for="useful">Résultat attendu</label><textarea id="useful" name="useful" maxlength="800" rows="3"' + disabled + '></textarea>'
            '<label for="context">Contexte utile</label><textarea id="context" name="context" maxlength="200" rows="2"' + disabled + '></textarea>'
            '<div class="website"><label for="website">Site web</label><input id="website" name="website" autocomplete="off" tabindex="-1"></div>'
            '<button type="submit"' + disabled + '>' + icon('i-pen') + 'Préparer cet exemple</button>'), 'besoin')
        content += section('Mes cas d’usage dans ce navigateur', dossiers)
    elif value.get('kind') == 'honeypot_ack' or 'operation_id' in value:
        title = 'Demande enregistrée'
        url = ('/preparation' if value.get('kind') == 'honeypot_ack'
               else '/preparation/dossiers/' + value['dossier_id'])
        content = state_block('wait', 'Où j’en suis', 'Préparation en attente', '<p>L’envoi a été enregistré. L’assistant prépare une réponse.</p>',
                              f'<a class="button" href="{text(url)}">Consulter le cas d’usage et son avancement</a>')
    else:
        dossier_id, revision = value['dossier_id'], value['revision']
        url = '/preparation/dossiers/' + dossier_id
        title = 'Est-ce le travail que vous voulez tester ?' if value['package'] else 'Précisons le résultat utile'
        historical = revision != value.get('current_revision', revision)
        editable = not historical and value['stage'] != 'waiting'
        disabled = '' if can_submit and editable else ' disabled aria-describedby="availability"'
        current_campaigns = [c for c in value.get('campaigns', []) if c['task']['revision'] == revision]
        navigation = '<nav class="steps" aria-label="Étapes de préparation">'
        current_step = 'comparaison' if current_campaigns else 'validation' if value['validation'] else 'exemple' if value['package'] else 'besoin'
        steps = [('besoin', 'Besoin'), ('exemple', 'Exemple'), ('validation', 'Validation')] + ([('comparaison', 'Comparaison')] if current_campaigns else [])
        order = [anchor for anchor, _ in steps]
        for number, (anchor, label) in enumerate(steps, start=1):
            inner = '<span class="n">' + str(number) + '</span>' + label
            if anchor == 'exemple' and not value['package']:
                navigation += '<span>' + inner + '</span>'
            else:
                done = ' class="done"' if order.index(anchor) < order.index(current_step) else ''
                navigation += '<a href="#' + anchor + '"' + (' aria-current="step"' if anchor == current_step else done) + '>' + inner + '</a>'
        navigation += '<small>Cas d’usage privé · pièces entièrement inventées</small></nav>'
        stages = {'draft': ('unk', 'Brouillon', 'Rien n’a encore été envoyé à l’assistant.'),
                  'waiting': ('wait', 'Préparation en attente', 'L’assistant prépare une réponse. Actualisez pour voir son avancement.'),
                  'clarification': ('action', 'Une précision est attendue de vous', 'Répondez ci-dessous pour que l’exemple soit préparé.'),
                  'preview': ('action', 'Un exemple est prêt à être examiné', 'Lisez la consigne et les pièces, corrigez si besoin, puis validez.'),
                  'scope_confirmation': ('action', 'Le périmètre est à confirmer', 'Confirmez ou corrigez le périmètre proposé ci-dessous.'),
                  'suspended': ('err', 'Préparation suspendue', 'Une intervention du responsable est nécessaire ; aucun rejeu automatique.')}
        tone, heading, next_step = stages[value['stage']]
        qualification = value.get('qualification', {})
        automatic = 'operation_id' in qualification
        if value['validation']:
            tone, heading, next_step = ('done', 'Cas d’usage validé', 'La comparaison est en attente de préparation par le responsable.') if not current_campaigns \
                else ('done', 'Cas d’usage validé', 'Une comparaison est préparée ou lancée : suivez-la à l’étape 4.')
        if value['validation'] and automatic:
            if value.get('qualified'):
                tone, heading, next_step = 'done', 'Exemple qualifié', 'Consultez les constats puis choisissez les modèles à comparer.'
            elif qualification.get('status') == 'BLOCKED':
                tone, heading, next_step = 'err', 'Qualification à reprendre', qualification['summary']
            else:
                tone, heading, next_step = 'wait', 'Qualification en attente', 'Votre validation est enregistrée. Actualisez pour consulter le contrôle de l’exemple.'
        content = '<p class="tag">Cas d’usage inventé · révision ' + text(revision) + '</p>'
        if historical:
            content += '<p class="notice">Révision précédente en lecture seule. Pour modifier ou valider, ouvrez la révision courante.</p>'
        actions = f'<a class="button" href="{text(path)}">Actualiser cet état</a>' if (value['stage'] == 'waiting' or value['validation'] and automatic and not value.get('qualified')) else ''
        if historical:
            actions = f'<a class="button" href="{text(url)}">Revenir à la révision courante</a>'
        content += state_block(tone, 'Où j’en suis', heading, '<p>' + text(value['explanation']) + '</p><p class="hint">' + next_step + '</p>', actions)
        refresh = '' if 'Actualiser cet état' in actions else f'<a href="{text(path)}">Actualiser cet état</a> · '
        content += f'<p class="hint">{refresh}<a href="{text(url)}">Révision courante</a>'
        if revision > 1:
            content += f' · <a href="{text(url)}/revisions/{revision - 1}">Révision précédente</a>'
        content += '</p>'
        if editable and value['package'] is None:
            content += section('Votre réponse', form(url + '/messages',
                {'action_id': secrets.token_hex(16), 'revision': revision, 'kind': 'clarify'},
                '<label for="message">Votre précision</label><textarea id="message" name="message" rows="3" required maxlength="1000"' + disabled + '></textarea>'
                '<div class="website"><label for="website">Site web</label><input id="website" name="website" autocomplete="off" tabindex="-1"></div>'
                '<button type="submit"' + disabled + '>Envoyer ma réponse</button>'))
        payload = value['payload']
        content += section('Besoin conservé', '<p>' + text(payload['request']) + '</p>', 'besoin')
        if value.get('task_index'):
            content += '<details><summary>Historique du cas d’usage et versions d’épreuve</summary>' + render_task_index(value['task_index']) + '</details>'
        if value.get('message') and 'message' in value['message']:
            content += section('Message à l’origine de cette révision', '<p>' + text(value['message']['message']) + '</p>')
        if payload['clarifications'] or payload['validated_assumptions']:
            agreements = listing(payload['clarifications']) if payload['clarifications'] else ''
            for agreement in payload['validated_assumptions']:
                if type(agreement) is dict and set(agreement) == {'question', 'answer'}:
                    agreements += '<blockquote><p>' + text(agreement['question']) + '</p>'
                    agreements += '<p><strong>Votre accord : </strong>' + text(agreement['answer']) + '</p></blockquote>'
                else:
                    agreements += '<p>' + text(encode(agreement) if type(agreement) is dict else agreement) + '</p>'
            content += section('Précisions et accords conservés', agreements)
        if payload['reformulation']:
            content += section('Reformulation', '<p>' + text(payload['reformulation']) + '</p>')
        if payload['fictional_parameters']:
            content += section('Paramètres entièrement inventés', listing(f'{k} : {v}' for k, v in payload['fictional_parameters'].items()))
        package = value['package']
        if package:
            content += section('Consigne donnée aux modèles', '<p class="consigne">' + text(package['instruction']) + '</p>', 'exemple')
            content += section('Les pièces de l’exemple', '<p class="hint">Ouvrez chaque pièce pour la lire ici, puis refermez-la pour poursuivre.</p>' + ''.join(
                '<details class="example-content"><summary>Voir le contenu'
                + (f' {index}' if len(package['pieces']) > 1 else '') + '</summary>'
                + '<div class="example-text">' + text(value['example_contents'][piece['id']]) + '</div></details>'
                for index, piece in enumerate(package['pieces'], start=1)))
            content += '<div class="two">' + section('Livrables attendus', listing(package['deliverables']))
            criteria = value['criteria']
            groups = ''
            for key, tone, group_title in (('eliminatory', 'elim', 'Éliminatoires'),
                                     ('obligations', 'oblig', 'Obligations'),
                                     ('quality', 'sec', 'Qualité')):
                items = criteria[key]
                if items:
                    labels = (item['label'] for item in items) if key == 'quality' else items
                    groups += '<div class="grp ' + tone + '"><h3>' + group_title + '</h3>' + listing(labels) + '</div>'
            groups = '<div class="crit">' + groups + '</div><p class="rule"><span>Règle</span><span>' \
                     + text(value['criteria_rule']) + '</span></p>'
            content += section('Critères de réussite', groups) + '</div>'
            limits = '<h3>Travail humain restant</h3><p>' + text(package['human_work']) + '</p>'
            if package['acceptable_ambiguities']:
                limits += '<h3>Ambiguïtés recevables</h3>' + listing(package['acceptable_ambiguities'])
            if package['limits']:
                limits += '<h3>Limites de l’exemple</h3>' + listing(package['limits'])
            content += section('Ce qui restera à faire', limits)
            change_labels = {'instruction': 'Consigne', 'deliverables': 'Livrables', 'criteria': 'Critères',
                'acceptable_ambiguities': 'Ambiguïtés recevables', 'pieces': 'Pièces'}
            if value['changes']:
                changes = listing(change_labels.get(change, change) for change in value['changes'])
                for kind, label in (('added', 'Pièces ajoutées'), ('removed', 'Pièces retirées'),
                                    ('modified', 'Pièces modifiées')):
                    names = value['piece_changes'][kind]
                    if names:
                        changes += '<h3>' + label + '</h3>' + listing(names)
                content += section('Changements à relire', changes)
        if 'indicative_cost' in value:
            estimate = value['indicative_cost']
            amount = estimate.get('token_subtotal_usd') if estimate else None
            content += '<p>Estimation indicative de cette préparation : ' + text(
                'non estimable' if amount is None else amount + ' USD') + \
                '. Tokens utilisés × tarifs du modèle relevés avant appel ; ce montant n’est pas une facture.</p>'
        if value.get('observed_cost'):
            cost = value['observed_cost']
            content += '<p>Coût observé de cette préparation : ' + text(
                'INCONNU' if cost['status'] == 'UNKNOWN' else cost['amount'] + ' ' + cost['currency']) + '. Source : ' + text(cost['source']) + '.</p>'
        else:
            content += '<p>Coût observé : INCONNU en l’absence de reçu de coût.</p>'
        if value.get('cost_reconciliation'):
            proof, cost = value['cost_reconciliation'], value['effective_cost']
            content += '<p>Coût rapproché : ' + text(cost['amount'] + ' ' + cost['currency']) + '. Source : ' + text(
                proof['source']) + ', attestée par ' + text(proof['actor']) + ' le ' + text(proof['observed_at']) + \
                '. Le reçu original reste inchangé.</p>'
        content += '<section id="validation"><h2>Validation du cas d’usage</h2>'
        if value['validation']:
            content += '<p role="status">Votre validation est enregistrée pour ce cas d’usage, cette révision et cet exemple exact.</p>'
            if not current_campaigns and not automatic:
                content += '<p>En attente de préparation des conditions par le responsable.</p>'
        elif package:
            content += '<p role="status">Une nouvelle validation est requise pour l’exemple présenté.</p>'
        else:
            content += '<p>La validation sera possible lorsqu’un exemple à examiner sera disponible.</p>'
        if editable and package and value['stage'] == 'preview' and value['validation'] is None:
            content += '<p>Cette validation confirme la fidélité de cet exemple à votre besoin. Si la qualification est disponible, elle est financée par l’opérateur sur l’enveloppe de préparation. Aucun appel candidat ni publication n’est autorisé ici.</p>'
            content += '<div class="actionbar">' + form(url + '/validation', binding(dossier_id, revision, value['package_sha256']),
                            '<button type="submit">' + icon('i-check') + 'Oui, c’est le travail à tester</button>') + '</div>'
        content += '</section>'
        if current_campaigns:
            content += '<section id="comparaison"><h2>Comparaison</h2>'
            current_campaign = current_campaigns[-1]
            content += '<p><a class="button" href="' + text(url + '/campaigns/' + current_campaign['campaign_id'] + '/conditions') + '">Examiner les conditions et suivre la comparaison courante</a></p>'
            for number, campaign in enumerate(reversed(current_campaigns[:-1]), 1):
                content += '<p><a class="button sec" href="' + text(url + '/campaigns/' + campaign['campaign_id'] + '/conditions') + '">Consulter la comparaison précédente ' + str(number) + '</a></p>'
            content += '</section>'
        if editable and value['package'] is not None:
            content += '<details class="corr"><summary class="button sec">' + icon('i-pen') + 'Préciser ou corriger cet exemple</summary><div>' + form(url + '/messages',
                {'action_id': secrets.token_hex(16), 'revision': revision},
                '<p>Indiquez ce qui doit changer. Les accords non touchés et les révisions précédentes sont conservés. Une modification de l’exemple demande une nouvelle validation.</p>'
                '<label for="kind">Objet du message</label><select id="kind" name="kind"' + disabled + '>'
                '<option value="clarify">Répondre à la clarification ou confirmer le périmètre</option>'
                '<option value="correct"' + (' selected' if package else '') + '>Modifier cet exemple</option></select>'
                '<label for="message">Votre précision ou correction</label>'
                '<textarea id="message" name="message" rows="4" required maxlength="1000"' + disabled + '></textarea>'
                '<div class="website"><label for="website">Site web</label><input id="website" name="website" autocomplete="off" tabindex="-1"></div>'
                '<button type="submit"' + disabled + '>Envoyer ce message</button>') + '</div></details>'
        qualification = value.get('qualification', {})
        labels = {'PENDING': 'En attente', 'QUALIFIED': 'Contrôles requis prouvés',
                  'BLOCKED': 'Bloquée : référence ou contrôles insuffisamment prouvés',
                  'APPROVED': 'Approuvée par action opérateur locale'}
        content += '<details><summary>Qualification et approbation de l’épreuve</summary>'
        content += section('Qualification', '<p>' + text(labels.get(
            qualification.get('qualification_status'), 'En attente')) + '</p>')
        if automatic:
            content += '<p>' + text(qualification['summary']) + '</p>'
            content += listing(finding['text'] for finding in qualification.get('findings', []))
        content += section('Approbation', '<p>' + text(labels.get(
            qualification.get('approval_status'), 'En attente')) + '</p>'
            '<p>La validation du besoin, la qualification et l’approbation restent distinctes. '
            'Aucun appel ni publication n’est autorisé par cet état. Les preuves, la référence '
            'et les limites de jugement sont réservées à l’inspection locale du responsable.</p>')
        content += '</details>'
        if value.get('qualified'):
            content += '<p><a class="button' + (' sec' if current_campaigns or historical else '') + '" href="' + text(
                url + '/configurations') + '">Choisir les modèles</a></p>'
        if 'campaigns' in value:
            campaigns = '<p>Suivi privé des comparaisons fictives de ce cas d’usage. '
            campaigns += 'L’acquisition conserve des reçus ; elle ne juge pas le contenu des sorties.</p>'
            technical = {'NOT_STARTED': 'Non lancée : aucune tentative', 'INTENT_RECORDED': 'Intention enregistrée',
                         'EMISSION_POSSIBLE': 'Appel actif ou émission possible, reçu en attente',
                         'AMBIGUOUS': 'Effets inconnus : reprise bloquée', 'RECEIVED': 'Reçu conservé'}
            for campaign in value['campaigns']:
                task = campaign['task']
                campaigns += '<article><h3>Campagne ' + text(campaign['campaign_id']) + '</h3>'
                if campaign.get('recovery_of'):
                    campaigns += '<p>Reprise technique de ' + text(campaign['recovery_of']) + '. Les reçus et coûts précédents restent conservés.</p>'
                if 'evaluations' in campaign:
                    campaigns += '<p><a href="' + text(url) + '/campaigns/' + text(campaign['campaign_id']) + '">Comparer les observations de cette campagne</a></p>'
                campaigns += '<p>Version d’épreuve ' + text(task['version']) + ', révision ' + text(task['revision']) + '.</p>'
                campaigns += '<h4>Configurations demandées</h4>' + listing(
                    f'{c["id"]} : {c["model"]}, révision {c["revision"]}, fournisseur {c["provider"]}, '
                    f'accès {c["access"]}, canal {c["channel_id"]}, route {c["route"]}, effort {c["effort"]}, '
                    f'paramètres {encode(c["parameters"])} ; observations exigées : {", ".join(c["required_observations"])}'
                    for c in campaign['panel'])
                conditions = campaign['conditions']
                pi = conditions['pi']
                campaigns += '<h4>Conditions Pi communes</h4><p>' + text(
                    f'{pi["package"]} {pi["version"]} ; état {pi["status"]} ; gel {conditions["frozen_at"]}') + '</p>'
                campaigns += '<details><summary>Contexte et environnement communs</summary>' + listing(
                    f'{k} : {encode(conditions[k])}' for k in ('packages', 'tools', 'skills', 'defaults', 'environment')) + '</details>'
                campaigns += '<h4>Autorités et budget</h4><p>' + (
                    'Admission opérateur ouverte pour les cellules : ' + text(', '.join(campaign['allowed_cells'])) if campaign['admission_open'] else
                    'Admission fermée. Autorités à fournir ou renouveler par l’opérateur : ' + text(', '.join(campaign['missing_authorities']))) + '.</p>'
                if campaign['restore_pending']:
                    campaigns += '<p>Restauration à rapprocher : toute nouvelle admission reste bloquée.</p>'
                if campaign['stop_reason']:
                    campaigns += '<p>Motif d’arrêt : ' + text(campaign['stop_reason']) + '.</p>'
                budget = campaign['budget']
                if budget:
                    campaigns += '<p>' + text(f'Enveloppe {budget["budget_id"]} : {budget["limit"]} {budget["currency"]}. '
                        f'Sous-total des coûts connus : {budget["spent"]}. Réservations conservées : {budget["reserved"]}. '
                        f'Solde disponible : {budget["available"] if budget["balance_status"] == "KNOWN" else "INCONNU"}.') + '</p>'
                else:
                    campaigns += '<p>Budget prévu : INCONNU, enveloppe à désigner par l’opérateur.</p>'
                campaigns += '<p>Prévisions de réserve par cellule : ' + text(
                    encode(campaign['reserve_amounts']) if campaign['reserve_amounts'] else 'INCONNU, autorité attendue') + '.</p>'
                campaigns += '<p>Base de coût : ' + text(encode(campaign['cost_basis'])) + '.</p>'
                campaigns += '<p>La réservation ne prouve pas un plafond de facturation. Préparation et jugement conservent leurs opérations propres.</p>'
                campaigns += '<h4>Cellules prévues</h4>' + listing(
                    f'{c["cell_id"]} — cas {c["case_id"]}, configuration {c["configuration_id"]} : {technical[c["state"]]}'
                    for c in campaign['cells'])
                for attempt in campaign['attempts']:
                    campaigns += '<details><summary>Tentative ' + text(attempt['operation_id']) + ' — ' + text(technical[attempt['state']]) + '</summary>'
                    campaigns += '<p>Exécution ' + text(attempt['execution_id']) + ', cellule ' + text(attempt['cell_id']) + '.</p>'
                    campaigns += '<p>Intention : ' + text(attempt['created_at']) + '. Émission possible : ' + text(attempt['emitted_at'] or 'Non lancée')
                    campaigns += '. Réception : ' + text(attempt['received_at'] or 'INCONNU') + '.</p>'
                    campaigns += '<p>Reçu : ' + text(attempt['receipt_id'] or 'Absent') + '. Preuve d’émission : ' + text(attempt['emission']) + '.</p>'
                    campaigns += '<p>Configuration observée : ' + text(encode(attempt['observed_configuration'])) + '.</p>'
                    campaigns += '<p>Sources des observations : ' + text(encode(attempt['observation_sources'])) + '.</p>'
                    cost = attempt['observed_cost']
                    campaigns += '<p>Coût observé : ' + text('INCONNU' if cost is None or cost['status'] == 'UNKNOWN' else cost['amount'] + ' ' + cost['currency'])
                    campaigns += '. Source : ' + text(cost['source'] if cost else 'INCONNU') + '.</p>'
                    if attempt['incident']:
                        campaigns += '<p>Incident technique : ' + text(attempt['incident']) + '.</p>'
                    if attempt['attribution_incident']:
                        campaigns += '<p>Attribution non prouvée : ' + text(', '.join(attempt['attribution_incident'])) + '.</p>'
                    evaluations = [e for e in campaign.get('evaluations', []) if e['attempt_id'] == attempt['operation_id']]
                    if evaluations:
                        campaigns += render_evaluations(evaluations, url)
                    else:
                        campaigns += '<p>Aucune évaluation conservée. Sortie brute réservée à l’inspection opérateur.</p>'
                    campaigns += '</details>'
                campaigns += '</article>'
            if not value['campaigns']:
                campaigns += '<p>Aucune campagne liée à ce dossier.</p>'
            content += '<details><summary>Historique et détails des comparaisons de ce cas d’usage</summary>' + campaigns + '</details>'
    if state and s9:
        reasons = {
            'open': 'Échanges disponibles. Chaque envoi reste vérifié par le serveur avant admission.',
            'closed': 'Appels fermés : aucune admission de préparation ouverte.',
            'unconfigured': 'Appels fermés : aucun assistant configuré pour la préparation.',
            'waiting': 'Nouveaux appels fermés : une préparation est en attente. Actualisez pour consulter son état.',
            'interrupted': 'Appels fermés : préparation interrompue ou suspendue. Une intervention du responsable est nécessaire ; aucun rejeu automatique.',
            'restore': 'Appels fermés : restauration à vérifier par le responsable.',
            'unresolved': 'Appels fermés : effets ou coûts non résolus dans l’enveloppe de préparation.',
            'budget': 'Appels fermés : enveloppe insuffisante pour un nouvel échange.'}
        status = '<aside id="availability" class="availability" aria-label="État de la préparation"><p><strong>Assistant '
        status += 'configuré' if state['assistant_configured'] else 'non configuré'
        status += '.</strong> Admission ' + ('ouverte' if state['admission_open'] else 'fermée') + '.</p><p>'
        status += text(reasons[state['reason']]) + '</p><p class="hint">La consultation ne lance aucun appel. La préparation et la qualification sont financées par l’opérateur ; les appels candidats demandent un lancement distinct.</p></aside>'
        content = status + content
    content = '<aside aria-label="Modèles disponibles"><p>' + text(RETIREMENT_NOTICE) + '</p></aside>' + content
    template = TEMPLATE_PATH.read_text()
    body_class = 's9 comparison' if value.get('kind') == 'comparison' else 's9' if s9 else ''
    version = 'v' + VERSION + ('+' + SOURCE_SHA[:7] if SOURCE_SHA else '')
    return (template.replace('{{title}}', text(title)).replace('{{body_class}}', body_class).replace('{{menu}}', menu)
            .replace('{{navigation}}', navigation).replace('{{layout_class}}', 'layout' if navigation else '')
            .replace('{{version}}', text(version)).replace('{{content}}', content).encode('utf-8'))
