"""Rendu de la restitution publique d’une campagne.

Ce module ne lit que la projection fermée du paquet publié : ni campagne privée,
ni dossier, ni pièce réservée au juge. Il partage les fragments du parcours privé
pour les verdicts, les montants et les dates, et n’ajoute aucun script.
"""
from .campaign_views import effort_label
from .fragments import (badge, date_lisible_utc, jour_lisible, listing, montant_lisible, readable_fields,
                        section, text, valeur_mesure, workbench)

STATES = {'PASS': 'respectée', 'FAIL': 'non respectée', 'INDETERMINE': 'non vérifiable'}
VERDICT_LABELS = {'SATISFAIT': 'Satisfait', 'NE SATISFAIT PAS': 'Ne satisfait pas', None: 'À reprendre'}
TRI_LABELS = {'asc': 'ordre croissant', 'desc': 'ordre décroissant'}
CONFIGURATION_LABELS = (('provider', 'Fournisseur'), ('model', 'Modèle'), ('revision', 'Révision'),
                        ('access', 'Accès'), ('channel_id', 'Canal'), ('route', 'Route'),
                        ('parameters', 'Paramètres'), ('effort', 'Effort de raisonnement'))


def _panel(value):
    return {configuration['position']: configuration for configuration in value['panel']}


def _candidate(configuration):
    return configuration['name'] + ' · ' + effort_label(configuration)


def _example(position):
    return 'Exemple ' + str(position)


def _metric(metric):
    """Valeur affichable d’une mesure classable, jamais un zéro à la place d’une inconnue"""
    return valeur_mesure(metric['value'], metric['unit']).capitalize()


def _base(value):
    return ('/epreuves/' + text(value['task_id']) + '/' + text(value['version_id']) +
            '/campagnes/' + text(value['campaign_id']))


def _scope(value, columns):
    query = value.get('filter_scope') or {}
    shown, total = len(value['rows']), value['population']
    label = ('1 réponse affichée sur ' if shown == 1 else ' réponses affichées sur ')
    content = '<p class="view-scope note">' + text(str(shown) + label + str(total))
    if query.get('sort'):
        column = next(column for column in columns if column['id'] == query['sort'])
        content += ' · ' + text('triées par ' + column['label'][:1].lower() + column['label'][1:]
                                 + ', ' + TRI_LABELS[query.get('direction', 'asc')])
    else:
        content += ' · sans tri'
    content += '. Un filtre ne change ni les verdicts ni la population des calculs.</p>'
    return content


def _filters(value, columns):
    query = value.get('filter_scope') or {}
    panel = _panel(value)
    cases = [case['position'] for case in value['cases']]
    options = [
        ('case', 'Exemple', 'Tous les exemples', [(str(position), _example(position)) for position in cases]),
        ('configuration', 'Modèle', 'Tous les modèles',
         [(str(position), _candidate(panel[position])) for position in sorted(panel)]),
        ('verdict', 'Résultat', 'Tous les résultats',
         [('SATISFAIT', 'Satisfait'), ('NE SATISFAIT PAS', 'Ne satisfait pas'), ('A_REPRENDRE', 'À reprendre')]),
        ('sort', 'Trier par', 'Sans tri', [(column['id'], column['label']) for column in columns]),
        ('direction', 'Sens du tri', 'Croissant', [('desc', 'Décroissant')]),
    ]
    content = '<details id="filters" class="comparison-filters"' + (' open' if query else '') + '>'
    content += '<summary>Trier et filtrer</summary><form method="get" action="' + _base(value) + '#filters">'
    content += '<div class="filter-grid">'
    for name, label, default, values in options:
        content += '<div><label for="filter-' + name + '">' + text(label) + '</label>'
        content += '<select id="filter-' + name + '" name="' + name + '">'
        content += '<option value="">' + text(default) + '</option>'
        for option, shown in values:
            content += ('<option value="' + text(option) + '"' +
                        (' selected' if query.get(name) == option else '') + '>' + text(shown) + '</option>')
        content += '</select></div>'
    content += '</div>'
    if value['obligations']:
        content += '<details class="filter-advanced"' + (' open' if 'obligation' in query else '') + '>'
        content += '<summary>Filtrer par exigence</summary><p class="hint">Pour retrouver les réponses qui respectent ou non une exigence précise.</p>'
        content += '<label for="filter-obligation">Exigence à examiner</label>'
        content += '<select id="filter-obligation" name="obligation"><option value="">Toutes les exigences</option>'
        for obligation in value['obligations']:
            for state in obligation['states']:
                option = obligation['id'] + ':' + state
                content += ('<option value="' + text(option) + '"' +
                            (' selected' if query.get('obligation') == option else '') + '>'
                            + text(obligation['label'] + ' : ' + STATES[state]) + '</option>')
        content += '</select></details>'
    content += '<div class="actions"><button type="submit">Appliquer</button>'
    content += '<a class="button sec" href="' + _base(value) + '#filters">Retirer les filtres</a></div></form></details>'
    return content


def _conditions(value):
    conditions = value['conditions']
    pi = conditions['pi']
    content = '<section><h2>Conditions communes</h2><ul>'
    content += '<li>Même consigne et mêmes pièces pour tous les modèles, avec l’outil ' + text(
        pi['package'] + ' ' + pi['version']) + ' (' + text(pi['status']) + '), fixé le '
    content += text(date_lisible_utc(conditions['frozen_at'])) + '.</li>'
    if conditions['environment']:
        content += '<li>Environnement : ' + text(', '.join(key + ' ' + item for key, item in sorted(
            conditions['environment'].items()))) + '.</li>'
    for key, label in (('tools', 'Outils'), ('packages', 'Paquets'), ('skills', 'Compétences')):
        declared = conditions[key]
        content += '<li>' + text(label) + ' : ' + (text(', '.join(declared)) if declared else 'aucun déclaré') + '.</li>'
    content += '<li>Base de coût : ' + text(value['cost_basis']['scope'] + ', ' + value['cost_basis']['attempts']) + '.</li>'
    return content + '</ul></section>'


def _campaigns(value):
    others = [campaign for campaign in value['campaigns']
              if campaign['campaign_id'] != value['campaign_id']]
    content = '<section><h2>Campagnes publiées de cette épreuve</h2>'
    if len(value['campaigns']) > 1:
        content += '<p class="hint">Chaque campagne reste une restitution distincte : aucune ne fusionne ses résultats avec une autre.</p>'
    content += '<ul>'
    for campaign in value['campaigns']:
        shown = ('Campagne du ' + date_lisible_utc(campaign['frozen_at']) + ' · ' + campaign['pi']['package']
                 + ' ' + campaign['pi']['version'] + ' · ' + str(campaign['coverage']['evaluated_attempts'])
                 + (' réponse évaluée' if campaign['coverage']['evaluated_attempts'] == 1 else ' réponses évaluées')
                 + ' sur ' + str(campaign['coverage']['planned_cells'])
                 + (' essai prévu' if campaign['coverage']['planned_cells'] == 1 else ' essais prévus') + ' · coûts '
                 + ('complets' if campaign['economic_status'] == 'COMPLETE' else 'incomplets'))
        if campaign['campaign_id'] == value['campaign_id']:
            content += '<li><strong>' + text(shown) + '</strong> (campagne affichée)</li>'
        else:
            href = ('/epreuves/' + text(value['task_id']) + '/' + text(value['version_id']) +
                    '/campagnes/' + text(campaign['campaign_id']))
            content += '<li><a href="' + href + '">' + text(shown) + '</a></li>'
    content += '</ul>'
    if any(campaign['contract_sha256'] != value['contract_sha256'] or
           campaign['cases'] != len(value['cases']) or
           campaign['environment'] != value['conditions']['environment'] or
           campaign['pi'] != value['conditions']['pi'] or
           campaign['unit'] != value['cost_basis']['unit'] for campaign in others):
        content += ('<p class="note">Une autre campagne présente un contrat, un périmètre d’exemples, un environnement, '
                    'un harnais ou une unité de coût différents : les résultats ne se comparent pas directement.</p>')
    return content + '</section>'


def _proofs(value, row):
    content = '<ul>'
    for proof in row['proofs']:
        if proof['piece_id'] is None:
            content += '<li>' + text(proof['name']) + ' : pièce non publiée. La vérification de ce constat reste limitée.</li>'
        else:
            href = _base(value) + '/pieces/' + text(proof['piece_id'])
            content += '<li><a href="' + href + '">' + text(proof['name']) + '</a></li>'
    return content + '</ul>'


def _config_cell(value, unknown='Inconnu'):
    if value in (None, 'INCONNU', 'UNKNOWN'):
        return text(unknown)
    if type(value) is dict:
        return readable_fields(value)
    return text(value)


def _configuration_table(row):
    configuration = row['configuration']
    content = ('<div class="table-scroll"><table class="config-table"><caption>Configuration demandée, observée '
               'et source de chaque observation.</caption><thead><tr><th scope="col">Caractéristique</th>'
               '<th scope="col">Demandée</th><th scope="col">Observée</th><th scope="col">Source</th></tr></thead><tbody>')
    unknown = []
    for field, label in CONFIGURATION_LABELS:
        requested = configuration['requested'][field]
        observed = configuration['observed'][field]
        source = configuration['sources'][field]
        if observed in (None, 'INCONNU', 'UNKNOWN'):
            unknown.append(label)
        content += ('<tr><th scope="row">' + text(label) + '</th><td>' + _config_cell(requested)
                    + '</td><td>' + _config_cell(observed) + '</td><td>' + _config_cell(source, 'Inconnue') + '</td></tr>')
    content += '</tbody></table></div>'
    if unknown:
        content += '<p class="hint">Observations inconnues : ' + text(', '.join(unknown)) + '.</p>'
    return content


def _row_detail(value, row):
    panel = _panel(value)
    content = '<details><summary>Détail et preuves</summary>'
    content += '<p><strong>' + text(_candidate(panel[row['panel_position']])) + '</strong></p>'
    if row['next_action']:
        content += '<p>' + text(row['next_action']) + '</p>'
    content += _configuration_table(row)
    content += '<p><strong>Constats</strong></p>' + listing(
        finding['label'] + ' : ' + STATES[finding['status']] + ' · ' + finding['finding']
        for finding in row['findings'])
    if row['measures']:
        content += '<p><strong>Mesures</strong></p><ul>'
        for measure in row['measures']:
            shown = valeur_mesure(measure['value'], measure['unit']).capitalize()
            content += '<li>' + text(measure['label'] + ' : ' + shown)
            if measure['reason']:
                content += text(' (' + measure['reason'] + ')')
            content += '</li>'
        content += '</ul>'
    judgment_cost = row['judgment']['cost']
    content += '<p>Coût du jugement : ' + text(
        'Inconnu' if judgment_cost['value'] is None else
        montant_lisible(judgment_cost['value']) + ' ' + judgment_cost['unit']) + '.</p>'
    if row['judgment']['professional_review']:
        review = row['judgment']['professional_review']
        content += '<p>Revue professionnelle annoncée : ' + text(review['author'] + ', ' + review['phase']
                                                               + ', ' + review['scope']) + '.</p>'
    else:
        content += '<p>Revue professionnelle : aucune.</p>'
    content += '<p><strong>Preuves</strong></p>' + _proofs(value, row)
    content += '<p>Évaluée le ' + text(jour_lisible(row['created_at'])) + ' par ' + text(row['responsible']) + '.</p>'
    content += '<p><strong>Limites</strong></p>' + listing(row['limits'])
    return content + '</details>'


def _table(value, columns):
    panel = _panel(value)
    quality = [column for column in columns if not column['cost']]
    content = '<section class="comparison-results"><h2>Comparaison des modèles</h2>'
    content += '<p class="table-hint">Sur petit écran, faites défiler le tableau horizontalement.</p>'
    content += _filters(value, columns) + _scope(value, columns)
    if not value['rows']:
        content += ('<p class="note">Aucune réponse ne correspond à ces filtres. Retirez un filtre pour revoir les autres : '
                    'rien n’a été supprimé.</p>')
    for case in value['cases']:
        rows = [row for row in value['rows'] if row['case_position'] == case['position']]
        if not rows and (len(value['cases']) == 1 or (value.get('filter_scope') or {}).get('case') != str(case['position'])):
            continue
        if len(value['cases']) > 1:
            content += '<h3>' + text(_example(case['position'])) + '</h3>'
        content += '<div class="table-scroll" role="region" tabindex="0" aria-label="' + text(
            _example(case['position']) if len(value['cases']) > 1 else 'Comparaison des modèles') + '">'
        content += '<table><caption>Chaque verdict concerne la réponse obtenue sur cet exemple.</caption><thead><tr>'
        titles = ['Modèle', 'Résultat'] + (['Qualité observée'] if quality else []) + ['Coût observé', 'Détails']
        for title in titles:
            content += '<th scope="col">' + text(title) + '</th>'
        content += '</tr></thead><tbody>'
        for row in rows:
            content += '<tr id="reponse-' + text(str(row['panel_position'])) + '-' + text(str(row['case_position'])) + '">'
            content += '<th scope="row"><strong>' + text(_candidate(panel[row['panel_position']])) + '</strong>'
            if row.get('repetition'):
                content += '<p class="hint">' + text('Essai ' + str(row['repetition'])) + '</p>'
            if row.get('relaunch'):
                content += '<p class="hint">' + text('Réponse obtenue à la tentative ' + str(row['relaunch'])
                                                     + ', après un incident fournisseur') + '</p>'
            if row.get('recovery_limit'):
                content += '<p class="hint">' + text('Reprise après arrêt pour longueur : limite de sortie de '
                                                     + str(row['recovery_limit']) + ' jetons') + '</p>'
            content += '</th><td>' + badge(row['verdict'])
            content += '<p class="hint">' + text(row['reason']) + '</p></td>'
            if quality:
                content += '<td><ul class="quality-list">'
                for column in quality:
                    metric = row['metrics'][column['id']]
                    content += '<li><strong>' + text(column['label']) + '</strong> : ' + text(_metric(metric)) + '</li>'
                    if metric['rank'] is None:
                        content += '<li class="hint">Non comparable : ' + text(metric['reason']) + '</li>'
                content += '</ul></td>'
            cost = row['metrics']['cost']
            content += '<td><span class="source-value">' + text(
                'Inconnu' if cost['value'] is None else montant_lisible(cost['value']) + ' ' + cost['unit']) + '</span>'
            if cost['value'] is not None and cost['rank'] is None:
                content += '<p class="hint">Coût non comparable : ' + text(cost['reason']) + '</p>'
            content += '</td><td>' + _row_detail(value, row) + '</td></tr>'
        content += '</tbody></table></div>'
    return content + '</section>'


def _pending(value):
    if not value['pending']:
        return ''
    panel, content = _panel(value), '<section><h2>Réponses en attente ou incidents</h2><ul>'
    for item in value['pending']:
        prefix = ''
        if len(value['cases']) > 1:
            prefix = _example(item['case_position']) + ' : '
        attempt = ', essai ' + str(item['repetition']) if item['repetition'] else ''
        details = [item['cause'] or 'cause non établie par le reçu'] if item['cause'] else []
        cost = item['cost']
        shown = 'Inconnu' if cost['value'] is None else montant_lisible(cost['value']) + ' ' + cost['unit']
        source = 'Inconnue' if cost['source'] in ('INCONNU', 'UNKNOWN') else cost['source']
        content += '<li>' + text(prefix + _candidate(panel[item['panel_position']]) + attempt + '. '
                                 + item['next_action'] + (' (' + ' ; '.join(details) + ')' if details else '')
                                 + ' Coût observé : ' + shown + ' · source : ' + source + '.') + '</li>'
    return content + '</ul></section>'


def _unknowns(value):
    content = ''
    if value['unknowns']:
        content += '<section><h2>Inconnues et incidents</h2><ul>'
        content += ''.join('<li>' + text(item) + '</li>' for item in value['unknowns'])
        content += '</ul></section>'
    return content


def _restrictions(value):
    content = '<section><h2>Ce que cette publication permet de vérifier</h2><ul>'
    content += ''.join('<li>' + text(item) + '</li>' for item in value['restrictions'])
    return content + '</ul></section>'


def render_results(value):
    back = ('<p><a href="/epreuves/' + text(value['task_id']) + '/' + text(value['version_id'])
            + '">Retour à l’épreuve</a> · <a href="/epreuves">Toutes les épreuves</a></p>')
    if value.get('unavailable') == 'trial-withdrawn':
        return 'Résultats retirés', back + ('<p class="note">Cette épreuve est retirée : ses résultats publiés et leurs '
                                            'pièces ne sont plus accessibles.</p>')
    if value.get('withdrawn'):
        content = ('<p class="note">Cette restitution est retirée. Son contenu et ses pièces ne sont plus disponibles.</p>'
                   '<p>L’épreuve et les autres restitutions restent consultables.</p>')
        return 'Restitution retirée', back + content
    columns = value['columns']
    content = ''
    content += '<p class="hint">' + text(value['title'] + ' · Version ' + value['version_id']
                                          + ' · exemple entièrement inventé') + '</p>'
    content += '<p>Travail évalué : ' + text(value['work']) + '</p>'
    content += '<p>' + text(value['conclusion']['text']) + '</p>'
    content += '<p>' + text(value['conclusion']['attribution']) + '</p>'
    coverage = value['coverage']
    content += '<p class="note">Réponses évaluées : ' + text(str(coverage['evaluated_attempts'])) + ' · essais lancés : '
    content += text(str(coverage['attempted_cells'])) + ' sur ' + text(str(coverage['planned_cells'])) + '. '
    content += ('Comparaison des coûts complète.' if value['economic_status'] == 'COMPLETE'
                else 'Comparaison des coûts incomplète : certains coûts observés manquent ou ne sont pas comparables.')
    content += '</p>'
    dates = sorted({date[:10] for date in value['acquisition_dates']})
    if dates:
        content += '<p class="hint">Réponses reçues ' + text(
            'le ' + jour_lisible(dates[0]) if len(dates) == 1 else
            'du ' + jour_lisible(dates[0]) + ' au ' + jour_lisible(dates[-1])) + '.</p>'
    content += _table(value, columns)
    content += _pending(value)
    # Le cadre de lecture suit la comparaison : sur téléphone, la synthèse vient après le tableau
    side = section('Limites', listing(value['conclusion']['limits']))
    side += _conditions(value)
    side += _campaigns(value)
    side += _unknowns(value)
    side += _restrictions(value)
    side += ('<details id="method"><summary>Comment lire ces résultats</summary><ul>'
             '<li><strong>Satisfait</strong> : toutes les exigences sont respectées et aucune erreur éliminatoire n’a été relevée.</li>'
             '<li><strong>Ne satisfait pas</strong> : une exigence n’est pas respectée ou une erreur éliminatoire a été relevée ; '
             'la réponse n’est pas utilisable, quel que soit son coût.</li>'
             '<li><strong>À reprendre</strong> : la preuve ne permet pas encore de conclure ; ce n’est pas un échec du modèle.</li>'
             '<li>Les verdicts et les mesures sont ceux enregistrés lors de la campagne ; cette page ne relance aucune évaluation.</li>'
             '<li>Les tris ordonnent les seules valeurs classables. Les valeurs inconnues ou incompatibles restent sans rang, '
             'sans être traitées comme défavorables.</li></ul></details>')
    return 'Résultats : ' + value['title'], back + workbench(content, side, 'Cadre de lecture')
