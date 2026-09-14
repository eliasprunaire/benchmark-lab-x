"""Rendu HTML du parcours privé : formulaires natifs, preuves inertes, gabarit unique.

Ce module ne touche ni au stockage, ni aux secrets, ni aux fournisseurs : il met en
forme les vues structurées renvoyées par l'exécuteur.
"""
from html import escape
from pathlib import Path
import secrets
from urllib.parse import urlencode

from benchmark.model_catalog import RETIREMENT_NOTICE
from benchmark.preparation import binding
from benchmark.storage import _strict_json as encode

from .projection import projection_body

TEMPLATE_PATH = Path(__file__).with_name('templates') / 'preparation.html'
STYLESHEET_PATH = Path(__file__).with_name('static') / 'preparation.css'

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
              'environment': 'Environnement', 'package': 'Paquet', 'version': 'Version', 'status': 'État'}
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
        label = ('Évaluation à reprendre (valeur historique : INDETERMINE)' if record['verdict'] == 'INDETERMINE'
                 else record['verdict'] or 'Évaluation à reprendre')
        content += '<section id="evaluation-' + text(eid) + '"><h5>' + text(label) + '</h5>'
        content += '<p>' + text(record['reason']) + '</p><p>Cas ' + text(record['case_id'])
        content += ', configuration ' + text(record['configuration_id']) + ', évaluation ' + text(eid) + '.</p>'
        content += '<p>Responsable : ' + text(record['responsible']) + '. Date : ' + text(record['created_at']) + '.</p>'
        previous = record['previous_evaluation_id']
        if previous:
            content += '<p>Correction de <a href="#evaluation-' + text(previous) + '">' + text(previous) + '</a>.</p>'
        content += '<ul>'
        for finding in record['findings']:
            content += '<li>' + text(finding['criterion_id'] + ' / ' + finding['control_id'])
            content += ' : ' + text(finding['status']) + ', attribution ' + text(finding['attribution'])
            content += '. ' + text(finding['finding'])
            for proof in finding['evidence']:
                link = next(p for p in record['proof_links'] if p['piece_id'] == proof['piece_id'])
                content += '<details><summary>Passage de ' + text(link['name']) + '</summary>'
                content += '<pre>' + text(proof['passage']) + '</pre><p>SHA-256 : <code>' + text(proof['sha256'])
                target = ('#proof-' + eid + '-' + link['piece_id']
                          if link['piece_id'] in record.get('proof_contents', {}) else link['href'])
                content += '</code></p><a href="' + text(target) + '">Ouvrir la pièce exacte</a></details>'
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
        content += '<p><a href="' + text(dossier_url) + '">Revenir au dossier</a></p></section>'
    return content


def render_task_index(task):
    def text(value):
        return escape(str(value), quote=True)

    content = '<p><a href="' + text(task['href']) + '">' + text(task['need']) + '</a></p>'
    content += '<p>Dossier ' + text(task['dossier_id']) + '. Consultation privée, sans admission au catalogue public.</p>'
    content += '<p>Révisions du dossier : ' + ' · '.join(
        '<a href="' + text(task['href']) + '/revisions/' + str(revision) + '">' + str(revision) + '</a>'
        for revision in task['revisions']) + '.</p>'
    for version in task['versions']:
        content += '<section id="contract-' + text(version['contract_sha256']) + '"><h3>Version d’épreuve '
        content += text(version['version']) + '</h3><p>Contrat : <code>' + text(version['contract_sha256']) + '</code>.</p>'
        content += '<p><a href="' + text(task['href']) + '/revisions/' + str(version['revision']) + '">Ouvrir le dossier associé</a></p><ul>'
        for campaign in version['campaigns']:
            content += '<li><a href="' + text(campaign['href']) + '">Comparer la campagne ' + text(campaign['campaign_id']) + '</a></li>'
        content += '</ul>' if version['campaigns'] else '</ul><p>Aucune campagne pour cette version.</p>'
        content += '</section>'
    if not task['versions']:
        content += '<p>Aucune version d’épreuve contractuelle conservée.</p>'
    return content


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
    content = '<nav aria-label="Parcours"><a href="/preparation/catalogue">Mes tâches</a> · '
    content += '<a href="' + text(value['dossier_href']) + '">Dossier, versions et campagnes</a></nav>'
    content += '<p class="hint">Résultats privés · version d’épreuve ' + text(value['task']['version'])
    content += ' · campagne ' + text(value['campaign_id']) + '.</p>'
    content += '<p class="lead">' + text(value['result_expected']) + '</p>'
    content += '<div class="campaign-summary" aria-label="Conclusion de la campagne">'
    latest = {record['attempt_id']: record for record in value['history']}
    # Décompter les verdicts conservés par cas, sans créer de verdict agrégé
    for case in value['cases']:
        records = [record for record in latest.values() if record['case_id'] == case['id']]
        if records:
            counts = [str(sum(record['decision']['verdict'] == verdict for record in records)) + ' ' + label
                      for verdict, label in (('SATISFAIT', 'satisfait(s)'), ('NE SATISFAIT PAS', 'non satisfait(s)'),
                                             (None, 'évaluation(s) à reprendre'))
                      if any(record['decision']['verdict'] == verdict for record in records)]
            content += '<p><strong>Cas ' + text(case['id']) + '</strong> : ' + text(' · '.join(counts)) + '.</p>'
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
        'case': ('Cas', [(v['id'], v['id']) for v in value['cases']]),
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
    for case in value['cases']:
        rows = [r for r in value['rows'] if r['case_id'] == case['id']]
        if not rows:
            continue
        content += '<section class="comparison-results"><h2>Cas ' + text(case['id']) + '</h2>'
        content += '<p class="table-hint">Sur petit écran, faites défiler le tableau horizontalement pour lire coûts et preuves.</p>'
        content += '<div class="table-scroll" role="region" tabindex="0" aria-label="Observations du cas ' + text(case['id']) + '">'
        content += '<table><caption>Cas ' + text(case['id']) + ' · valeurs par tentative, sans agrégation</caption><thead><tr>'
        for title in ('Configuration et tentative', 'Verdict et motif', 'Coût observé', 'Mesures prévues', 'Preuves'):
            content += '<th scope="col">' + title + '</th>'
        content += '</tr></thead><tbody>'
        for row in rows:
            content += '<tr id="attempt-' + text(row['attempt_id']) + '" tabindex="-1"><th scope="row">'
            content += '<strong>' + text(row['requested_configuration']['model']) + '</strong>'
            content += data('Demandée, observée et sources', {k: row[k] for k in ('requested_configuration', 'observed_configuration', 'observation_sources')}) + '</th>'
            content += '<td><strong>' + text(row['verdict'] or 'Évaluation à reprendre') + '</strong><p>' + text(row['reason']) + '</p>'
            if row.get('decision', {}).get('next_action'):
                content += '<p>' + text(row['decision']['next_action']) + '</p>'
            if row['incident']:
                content += '<p>Incident : ' + text(row['incident']) + '</p>'
            content += '</td><td>' + metric(row['cost']) + '</td><td>'
            for measure in row['measures']:
                content += '<p>' + text(measure['definition']['measure']) + '</p>' + metric(measure)
            content += '</td><td><a href="' + text(row['detail_href']) + '">Détail et preuves de ' + text(row['attempt_id']) + '</a></td></tr>'
        content += '</tbody></table></div></section>'
    content += '<details id="method"><summary>Méthode, critères et limites</summary>'
    content += '<p>' + text(value['conclusion']['attribution']) + '</p><p>' + text('; '.join(value['conclusion']['limits'])) + '</p>'
    content += '<p>Travail humain restant : ' + text(value['human_work']) + '</p>'
    content += '<p>Les rangs comparent seulement les valeurs connues d’un même cas. Les égalités sont conservées ; '
    content += 'les valeurs inconnues ou incompatibles restent sans rang. Aucun choix automatique ni total multi-cas.</p>'
    for pending in value.get('pending_attempts', []):
        content += '<p>Tentative ' + text(pending['attempt_id']) + ' : ' + text(pending['next_action']) + '</p>'
    content += '<p>SATISFAIT : obligations prouvées. NE SATISFAIT PAS : défaut établi. Une évaluation à reprendre ne porte pas encore de verdict métier.</p>'
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

    state = value.get('availability', {})
    can_submit = state.get('can_submit', False)
    disabled = '' if can_submit else ' disabled aria-describedby="availability"'
    s9 = value.get('kind') != 'projection_preview'
    navigation = ''
    title = 'Décrire votre besoin'
    if error:
        title = 'Préparation indisponible' if value.get('unavailable') else 'Action non aboutie'
        content = '<p role="alert">' + text(value['error']) + '</p><p><a href="/preparation">Retrouver mes dossiers</a></p>'
    elif value.get('kind') == 'campaign_launch':
        campaign = value['campaign']
        base = '/preparation/dossiers/' + value['dossier_id'] + '/campaigns/' + campaign['campaign_id']
        title = 'Examiner puis lancer la comparaison'
        content = '<p>Le responsable prépare et autorise cette campagne. Votre confirmation déclenche uniquement les essais qu’il a admis.</p>'
        content += '<p><a href="' + text('/preparation/dossiers/' + value['dossier_id']) + '">Revenir à l’épreuve</a></p>'
        content += section('Critères fixés', '<p>' + text(value['criteria']['result_expected']) + '</p>' + listing(
            [item['description'] for item in value['criteria']['obligations']] +
            ['Erreur éliminatoire : ' + item['description'] for item in value['criteria']['eliminatory_errors']]))
        content += section('Modèles et conditions', listing([item['model'] + ' · ' + item['revision'] for item in campaign['panel']]) +
            '<p>Pi : ' + text(campaign['conditions']['pi']['package']) + ' · ' + text(campaign['conditions']['pi']['version']) +
            '. Conditions figées le ' + text(campaign['conditions']['frozen_at']) + '.</p>' +
            '<details><summary>Configurations et conditions exactes</summary><pre>' + text(encode(dict(panel=campaign['panel'], conditions=campaign['conditions']))) + '</pre></details>')
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
            content += form(base + '/start', {'manifest_sha256': campaign['manifest_sha256'], 'admission_id': value['admission_id']},
                '<label><input type="checkbox" name="confirm" value="yes" required> Je confirme le lancement des essais autorisés présentés.</label><button type="submit">Lancer la comparaison autorisée</button>')
        else:
            content += '<p role="status">' + ('Lancement enregistré. Consultez les essais et leurs résultats ci-dessous.' if campaign['attempts'] else 'Lancement indisponible. Le responsable doit vérifier les autorisations et la disponibilité de l’exécution.') + '</p>'
        content += section('Suivi des essais', listing([cell['cell_id'] + ' : ' + {'NOT_STARTED': 'non démarré', 'INTENT_RECORDED': 'en attente', 'EMISSION_POSSIBLE': 'en cours', 'RECEIVED': 'réponse reçue, consulter l’évaluation', 'AMBIGUOUS': 'état incertain, vérification requise'}.get(cell['state'], cell['state']) for cell in campaign['cells']]))
        content += '<p><a href="' + text(base + '/conditions') + '">Actualiser le suivi</a> · <a href="' + text(base) + '">Comparer les résultats et lire les preuves</a></p>'
    elif value.get('kind') == 'home':
        title = 'Quel modèle pour votre travail ?'
        content = '<p class="lead">Décrivez une tâche de votre travail, sans donnée personnelle ni information confidentielle. '
        content += 'Nous préparerons avec vous un exemple fictif pour comparer les modèles sur des critères vérifiables et leur coût observé. '
        content += 'Les résultats du test vous aideront à faire votre choix.</p>'
        content += '<div class="actions"><a class="button" href="/preparation">Décrire mon besoin ou retrouver mes dossiers</a></div>'
        content += '<section class="two"><div><h2>Un besoin, puis une épreuve</h2><p>Précisez le résultat utile. '
        content += 'Examinez la consigne, les pièces inventées et les critères ; corrigez l’exemple avant de le valider.</p></div>'
        content += '<div><h2>Des preuves pour choisir</h2><p>Une comparaison autorisée permet ensuite de consulter '
        content += 'les résultats et leurs limites dans les mêmes conditions de test. Aucun meilleur modèle universel.</p></div></section>'
        content += section('Votre espace de préparation', '<p>Vos dossiers restent privés. L’état de l’assistant et l’ouverture '
            'des appels sont indiqués dans cet espace. Un assistant configuré ne signifie pas que les appels sont ouverts.</p>'
            '<p>Vous pouvez consulter les dossiers existants lorsque les appels sont fermés. Si l’exécuteur est indisponible, '
            'cet accueil reste accessible ; revenez à votre espace pour vérifier son état.</p>')
        content += section('Consulter les publications', '<p>Seules les restitutions approuvées sont accessibles publiquement. '
            'La validation d’un besoin ne publie rien et ne lance aucun test.</p>'
            '<a href="/index.html">Ouvrir la publication active, si disponible</a>')
    elif value.get('kind') == 'publication_unavailable':
        title = 'Aucune publication vérifiée disponible'
        content = '<p class="lead">Aucun résultat public vérifié n’est disponible à cette adresse pour le moment.</p>'
        content += '<p>Vos dossiers et leurs résultats restent privés. Leur consultation ne publie aucune pièce.</p>'
        content += '<div class="actions"><a class="button" href="/">Revenir à l’accueil</a>'
        content += '<a href="/preparation">Retrouver mes dossiers</a></div>'
    elif value.get('kind') == 'catalogue':
        title = 'Mes tâches'
        content = '<p>Index privé de cette session. Aucune admission au catalogue public.</p>'
        content += ''.join('<section><h2>Dossier ' + text(task['dossier_id']) + '</h2>' + render_task_index(task) + '</section>' for task in value['tasks'])
        if not value['tasks']:
            content += '<p>Aucune tâche dans cette session.</p>'
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
        content += '<p>Empreinte du paquet proposé : <code>' + text(value['projection_sha256']) + '</code>.</p>'
        content += '<p>Cette vue privée reprend le contenu de la projection avec des liens privés vers les seules pièces '
        content += 'sélectionnées. Son habillage n’est pas un fichier approuvé. Le reçu fictif devra porter sur les octets du paquet.</p>'
        content += '<hr>' + projection_body(value['comparison'], value['selected_links'])
    elif value.get('kind') == 'attempt_detail':
        title = 'Détail de la tentative ' + value['history'][-1]['attempt_id']
        content = '<nav aria-label="Retour"><a href="' + text(value['back_href']) + '">Revenir à la comparaison avec ses filtres</a> · '
        content += '<a href="/preparation/catalogue">Mes tâches</a></nav><p>' + text(value['need']) + '</p>'
        content += '<p>Consultation privée. Campagne ' + text(value['campaign_id']) + ', version ' + text(value['task']['version']) + '.</p>'
        content += '<p>Les pièces exactes et leurs passages restent inertes. Historique conservé ; la dernière évaluation est affichée en premier.</p>'
        content += render_evaluations(list(reversed(value['history'])), value['back_href'])
    elif 'dossiers' in value:
        content = '<p class="lead">Décrivez le travail et le résultat qui vous serait utile. Vous pourrez examiner et corriger les pièces avant de valider le besoin représenté.</p>'
        dossiers = '<ul class="dossiers">' + ''.join(
            f'<li><a href="/preparation/dossiers/{text(d["dossier_id"])}">{text(d.get("need") or "Dossier " + d["dossier_id"])}</a>'
            f'<small>Révision {d["revision"]} · dossier {text(d["dossier_id"])}</small></li>'
            for d in value['dossiers']) + '</ul>' if value['dossiers'] else (
                '<p>Aucun dossier dans cette session. Commencez par un besoin lorsque les appels sont ouverts.</p>'
                '<p>Si vous aviez déjà un dossier, vérifiez que vous utilisez le même navigateur et son cookie de session.</p>')
        content += section('Mes dossiers dans ce navigateur', dossiers)
        content += section('Décrire mon besoin', form('/preparation/dossiers',
            {'dossier_id': secrets.token_hex(16), 'action_id': secrets.token_hex(16)},
            '<label for="request">Une tâche de votre travail</label><p id="request-help">Décrivez le travail et le résultat utile, sans donnée personnelle ni information confidentielle. Aucun dossier réel, même anonymisé.</p>'
            '<textarea id="request" name="request" required rows="5" aria-describedby="request-help"' + disabled + '></textarea>'
            '<button type="submit"' + disabled + '>Préparer cet exemple</button>'), 'besoin')
    elif 'operation_id' in value:
        title = 'Demande enregistrée'
        url = '/preparation/dossiers/' + value['dossier_id']
        content = '<p role="status">Préparation en attente. L’envoi a été enregistré.</p>'
        content += f'<p><a href="{text(url)}">Consulter le dossier et son avancement</a></p>'
    else:
        dossier_id, revision = value['dossier_id'], value['revision']
        url = '/preparation/dossiers/' + dossier_id
        title = 'Est-ce le travail que vous voulez tester ?' if value['package'] else 'Précisons le résultat utile'
        historical = revision != value.get('current_revision', revision)
        editable = not historical and value['stage'] != 'waiting'
        disabled = '' if can_submit and editable else ' disabled aria-describedby="availability"'
        navigation = '<nav class="steps" aria-label="Étapes de préparation">'
        current_step = 'validation' if value['validation'] else 'epreuve' if value['package'] else 'besoin'
        for anchor, label in [('besoin', '1. Besoin et précisions'), ('epreuve', '2. Épreuve proposée'), ('validation', '3. Validation du besoin')]:
            if anchor == 'epreuve' and not value['package']:
                navigation += '<span>' + label + '</span>'
            else:
                navigation += '<a href="#' + anchor + '"' + (' aria-current="step"' if anchor == current_step else '') + '>' + label + '</a>'
        navigation += '<small>Dossier privé · pièces entièrement inventées</small></nav>'
        stages = {'draft': 'Brouillon', 'waiting': 'Préparation en attente', 'clarification': 'Précision nécessaire',
                  'preview': 'Exemple à examiner', 'scope_confirmation': 'Périmètre à confirmer', 'suspended': 'Préparation suspendue'}
        content = '<p class="tag">Dossier fictif · révision ' + text(revision) + '</p>'
        if historical:
            content += '<p class="notice">Révision précédente en lecture seule. Pour modifier ou valider, ouvrez la révision courante.</p>'
        content += '<p role="status">' + text(stages[value['stage']]) + '</p>'
        content += '<p>' + text(value['explanation']) + '</p>'
        content += f'<p><a href="{text(path)}">Actualiser cet état</a> · <a href="{text(url)}">Révision courante</a></p>'
        if revision > 1:
            content += f'<p><a href="{text(url)}/revisions/{revision - 1}">Consulter la révision précédente</a></p>'
        if editable and value['package'] is None:
            content += section('Votre réponse', form(url + '/messages',
                {'action_id': secrets.token_hex(16), 'revision': revision, 'kind': 'clarify'},
                '<label for="message">Votre précision</label><textarea id="message" name="message" rows="3" required' + disabled + '></textarea><button type="submit"' + disabled + '>Envoyer ma réponse</button>'))
        payload = value['payload']
        content += section('Besoin conservé', '<p>' + text(payload['request']) + '</p>', 'besoin')
        if value.get('task_index'):
            content += '<details><summary>Historique du dossier et versions d’épreuve</summary>' + render_task_index(value['task_index']) + '</details>'
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
            content += section('Consigne exacte du paquet', '<p class="verbatim">' + text(package['instruction']) + '</p>', 'epreuve')
            content += section('L’exemple à examiner', '<p class="hint">Ouvrez le contenu pour le lire ici, puis refermez-le pour poursuivre.</p>' + ''.join(
                '<details class="example-content"><summary>Voir le contenu'
                + (f' {index}' if len(package['pieces']) > 1 else '') + '</summary>'
                + '<div class="example-text">' + text(value['example_contents'][piece['id']]) + '</div></details>'
                for index, piece in enumerate(package['pieces'], start=1)))
            content += '<div class="two">' + section('Livrables attendus', listing(package['deliverables']))
            content += section('Critères compréhensibles', listing(package['criteria'])) + '</div>'
            limits = '<h3>Travail humain restant</h3><p>' + text(package['human_work']) + '</p>'
            if package['acceptable_ambiguities']:
                limits += '<h3>Ambiguïtés recevables</h3>' + listing(package['acceptable_ambiguities'])
            if package['limits']:
                limits += '<h3>Limites de l’exemple</h3>' + listing(package['limits'])
            content += section('Ce qui restera à faire', limits)
            change_labels = {'instruction': 'Consigne', 'deliverables': 'Livrables', 'criteria': 'Critères',
                'acceptable_ambiguities': 'Ambiguïtés recevables', 'human_work': 'Travail humain restant',
                'limits': 'Limites', 'pieces': 'Pièces', 'stage': 'Étape de préparation',
                'explanation': 'Explication', 'reformulation': 'Reformulation', 'fictional_parameters': 'Paramètres fictifs'}
            content += section('Changements à relire', listing(change_labels.get(c, c) for c in value['changes']) if value['changes'] else
                               '<p>Aucun changement signalé.</p>')
            content += '<details><summary>Intégrité du paquet et vérifications</summary>'
            content += '<p>Les octets des pièces et l’empreinte du paquet ont été vérifiés. Ce contrôle d’intégrité ne qualifie pas le jugement.</p>'
            content += '<p>Empreinte du paquet : <code>' + text(value['package_sha256']) + '</code></p></details>'
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
        content += '<section id="validation"><h2>Validation du besoin</h2>'
        if value['validation']:
            content += '<p role="status">Votre validation est enregistrée pour ce dossier, cette révision et cette empreinte.</p>'
            current_campaigns = [c for c in value.get('campaigns', []) if c['task']['revision'] == revision]
            for campaign in current_campaigns:
                content += '<p><a class="button" href="' + text(url + '/campaigns/' + campaign['campaign_id'] + '/conditions') + '">Examiner les conditions et suivre la comparaison</a></p>'
            if not current_campaigns:
                content += '<p>En attente de préparation des conditions par le responsable.</p>'
        elif package:
            content += '<p role="status">Validation du besoin : nouvelle validation requise pour le paquet présenté.</p>'
        else:
            content += '<p>La validation sera possible lorsqu’un paquet à examiner sera disponible.</p>'
        if editable and package and value['stage'] == 'preview' and value['validation'] is None:
            content += '<p>Cette validation concerne uniquement ce paquet. Elle n’approuve ni contrat, ni appel, ni dépense, ni publication.</p>'
            content += form(url + '/validation', binding(dossier_id, revision, value['package_sha256']),
                            '<button type="submit">Valider cette révision et ce paquet exacts</button>')
        content += '</section>'
        if editable and value['package'] is not None:
            content += section('Préciser ou corriger cet exemple', form(url + '/messages',
                {'action_id': secrets.token_hex(16), 'revision': revision},
                '<p>Indiquez ce qui doit changer. Les accords non touchés et les révisions précédentes sont conservés. Une modification du paquet demande une nouvelle validation.</p>'
                '<label for="kind">Objet du message</label><select id="kind" name="kind"' + disabled + '>'
                '<option value="clarify">Répondre à la clarification ou confirmer le périmètre</option>'
                '<option value="correct"' + (' selected' if package else '') + '>Modifier cet exemple</option></select>'
                '<label for="message">Votre précision ou correction</label>'
                '<textarea id="message" name="message" rows="4" required' + disabled + '></textarea><button type="submit"' + disabled + '>Envoyer ce message</button>'))
        qualification = value.get('qualification', {})
        labels = {'PENDING': 'En attente', 'QUALIFIED': 'Contrôles requis prouvés',
                  'BLOCKED': 'Bloquée : référence ou contrôles insuffisamment prouvés',
                  'APPROVED': 'Approuvée par action opérateur locale'}
        content += '<details><summary>Qualification et approbation de l’épreuve</summary>'
        content += section('Qualification', '<p>' + text(labels.get(
            qualification.get('qualification_status'), 'En attente')) + '</p>')
        content += section('Approbation', '<p>' + text(labels.get(
            qualification.get('approval_status'), 'En attente')) + '</p>'
            '<p>La validation du besoin, la qualification et l’approbation restent distinctes. '
            'Aucun appel ni publication n’est autorisé par cet état. Les preuves, la référence '
            'et les limites de jugement sont réservées à l’inspection locale du responsable.</p>')
        if qualification.get('contract_sha256'):
            content += '<details><summary>Identité du contrat</summary><p>Contrat : <code>' + text(qualification['contract_sha256']) + '</code></p></details>'
        content += '</details>'
        if 'campaigns' in value:
            campaigns = '<p>Suivi privé des comparaisons fictives de ce dossier. '
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
                campaigns += '<p>Tâche ' + text(task['dossier_id']) + ', version ' + text(task['version'])
                campaigns += ', révision du dossier ' + text(task['revision']) + '.</p>'
                for label, digest in (('Manifeste', campaign['manifest_sha256']), ('Contrat', campaign['contract_sha256']),
                                      ('Paquet', task['package_sha256'])):
                    campaigns += '<p>' + label + ' : <code>' + text(digest) + '</code></p>'
                campaigns += '<h4>Configurations demandées</h4>' + listing(
                    f'{c["id"]} : {c["model"]}, révision {c["revision"]}, fournisseur {c["provider"]}, '
                    f'accès {c["access"]}, canal {c["channel_id"]}, route {c["route"]}, effort {c["effort"]}, '
                    f'paramètres {encode(c["parameters"])} ; observations exigées : {", ".join(c["required_observations"])}'
                    for c in campaign['panel'])
                conditions = campaign['conditions']
                pi = conditions['pi']
                campaigns += '<h4>Conditions Pi communes</h4><p>' + text(
                    f'{pi["package"]} {pi["version"]} ; état {pi["status"]} ; gel {conditions["frozen_at"]}') + '</p>'
                campaigns += '<p>Empreinte Pi : <code>' + text(pi['sha256']) + '</code></p>'
                campaigns += '<details><summary>Contexte et environnement communs</summary>' + listing(
                    f'{k} : {encode(conditions[k])}' for k in ('context_sha256', 'packages', 'tools', 'skills', 'defaults', 'environment')) + '</details>'
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
            content += '<details><summary>Historique et détails des comparaisons de ce dossier</summary>' + campaigns + '</details>'
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
        status += text(reasons[state['reason']]) + '</p><p class="hint">La consultation et la validation d’un paquet ne lancent aucun appel.</p></aside>'
        content = status + content
    content = '<aside aria-label="Modèles disponibles"><p>' + text(RETIREMENT_NOTICE) + '</p></aside>' + content
    template = TEMPLATE_PATH.read_text()
    body_class = 's9 comparison' if value.get('kind') == 'comparison' else 's9' if s9 else ''
    return (template.replace('{{title}}', text(title)).replace('{{body_class}}', body_class)
            .replace('{{navigation}}', navigation).replace('{{layout_class}}', 'layout' if navigation else '')
            .replace('{{content}}', content).encode('utf-8'))
