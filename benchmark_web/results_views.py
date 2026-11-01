"""Rendu de la restitution publique d’une campagne.

Ce module ne lit que la projection fermée du paquet publié : ni campagne privée,
ni dossier, ni pièce réservée au juge. Il partage les fragments du parcours privé
pour les verdicts, les montants et les dates, et n’ajoute aucun script.
"""
from .campaign_views import _plural, effort_label
from .fragments import (badge, bilan, bilan_exemple, bouton, cles, contexte_cas, date_lisible_utc, harnais, ic, info_modele, jour_lisible,
                        ligne_modele, lien, listing, montant_lisible, mot, puce, readable_fields, ruban, signal, table_cas, tampon,
                        text, tiroir, valeur_mesure)

STATES = {'PASS': 'respectée', 'FAIL': 'non respectée', 'INDETERMINE': 'non vérifiable'}
VERDICT_LABELS = {'SATISFAIT': 'Satisfait', 'NE SATISFAIT PAS': 'Ne satisfait pas', None: 'À reprendre'}
TRI_LABELS = {'asc': 'ordre croissant', 'desc': 'ordre décroissant'}
CONFIGURATION_LABELS = (('provider', 'Fournisseur'), ('model', 'Modèle'), ('revision', 'Révision'),
                        ('access', 'Accès'), ('channel_id', 'Canal'), ('route', 'Route'),
                        ('parameters', 'Paramètres'), ('effort', 'Raisonnement'))
SOUS_TITRE = 'Réponses de plusieurs modèles au même travail, cas par cas, sur des critères fixés d’avance.'


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


def _anchor(row):
    # Le numéro d'essai distingue les répétitions d'un même modèle sur un même exemple
    return ('d-' + str(row['panel_position']) + '-' + str(row['case_position'])
            + ('-' + str(row['repetition']) if row.get('repetition') else ''))


def _active_filters(value):
    """Puces des filtres appliqués, avec le lien qui les retire"""
    query = value.get('filter_scope') or {}
    if not query:
        return ''
    panel, shown = _panel(value), []
    if 'case' in query:
        shown.append(puce(_example(query['case']), icone='check'))
    if 'configuration' in query and int(query['configuration']) in panel:
        shown.append(puce(_candidate(panel[int(query['configuration'])]), icone='check'))
    if 'verdict' in query:
        shown.append(puce('Résultat : ' + VERDICT_LABELS.get(query['verdict'], 'À reprendre').lower(), icone='check'))
    if 'obligation' in query:
        shown.append(puce('Exigence', icone='check'))
    if 'sort' in query:
        shown.append(puce('Tri choisi', icone='check'))
    return '<div class="g-res-actifs">' + ''.join(shown) + lien(_base(value) + '#filters', 'Retirer les filtres', 'croix') + '</div>'


def _scope(value, columns):
    query = value.get('filter_scope') or {}
    shown, total = len(value['rows']), value['population']
    label = ('1 réponse affichée sur ' if shown == 1 else ' réponses affichées sur ')
    content = text(str(shown) + label + str(total))
    if query.get('sort'):
        column = next(column for column in columns if column['id'] == query['sort'])
        content += ' · ' + text('triées par ' + column['label'][:1].lower() + column['label'][1:]
                                 + ', ' + TRI_LABELS[query.get('direction', 'asc')])
    else:
        content += ' · sans tri'
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
    if value['obligations']:
        options.append(('obligation', 'Exigence à examiner', 'Toutes les exigences',
                        [(obligation['id'] + ':' + state, obligation['label'] + ' : ' + STATES[state])
                         for obligation in value['obligations'] for state in obligation['states']]))
    controls = ''
    for name, label, default, values in options:
        controls += '<div class="champ"><label for="filter-' + name + '">' + text(label) + '</label>'
        controls += '<select id="filter-' + name + '" name="' + name + '"><option value="">' + text(default) + '</option>'
        for option, shown in values:
            controls += ('<option value="' + text(option) + '"' +
                         (' selected' if query.get(name) == option else '') + '>' + text(shown) + '</option>')
        controls += '</select></div>'
    form = ('<form method="get" action="' + _base(value) + '#filters"><div class="filtres-grille">' + controls + '</div>'
            '<p class="petit">Un filtre ne change ni les verdicts ni la population des calculs.</p>'
            '<div class="actions">' + bouton('Appliquer', sec=True, icone=None)
            + lien(_base(value) + '#filters', 'Retirer les filtres', 'croix') + '</div></form>')
    chosen = [key for key in query if key != 'direction']
    return ('<div class="filtres">' + tiroir('Tri et filtres', form, resume=_plural(len(chosen), 'filtre') if chosen else 'Aucun filtre',
                                             ouvert=bool(query), ident='filters') + '</div>')


def _conditions(value):
    conditions = value['conditions']
    rows = [('Consigne', 'même consigne et mêmes pièces pour tous les modèles'),
            ('Harnais', harnais(', fixée le ' + date_lisible_utc(conditions['frozen_at'])))]
    if conditions['environment']:
        rows.append(('Environnement', text(', '.join(key + ' ' + item for key, item in sorted(conditions['environment'].items())))))
    for key, label in (('tools', 'Outils'), ('packages', 'Paquets'), ('skills', 'Compétences')):
        declared = conditions[key]
        rows.append((label, text(', '.join(declared)) if declared else 'aucun déclaré'))
    rows.append(('Base de coût', text(value['cost_basis']['scope'] + ', ' + value['cost_basis']['attempts'])))
    return ('<section aria-labelledby="t-cond"><div class="zone-titre"><h2 id="t-cond">Conditions communes</h2></div>'
            + cles(rows) + '</section>')


def _campaigns(value):
    others = [campaign for campaign in value['campaigns']
              if campaign['campaign_id'] != value['campaign_id']]
    content = '<section aria-labelledby="t-camp"><div class="zone-titre"><h2 id="t-camp">Benchmarks publiés pour cette épreuve</h2></div>'
    if len(value['campaigns']) > 1:
        content += '<p class="petit">Chaque benchmark reste une restitution distincte : aucun ne fusionne ses résultats avec un autre.</p>'
    content += '<ul>'
    for campaign in value['campaigns']:
        shown = ('Benchmark du ' + date_lisible_utc(campaign['frozen_at']) + ' · ' + str(campaign['coverage']['evaluated_attempts'])
                 + (' réponse évaluée' if campaign['coverage']['evaluated_attempts'] == 1 else ' réponses évaluées')
                 + ' sur ' + str(campaign['coverage']['planned_cells'])
                 + (' essai prévu' if campaign['coverage']['planned_cells'] == 1 else ' essais prévus') + ' · coûts '
                 + ('complets' if campaign['economic_status'] == 'COMPLETE' else 'incomplets'))
        if campaign['campaign_id'] == value['campaign_id']:
            content += '<li><strong>' + text(shown) + '</strong> (benchmark affiché)</li>'
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
        content += ('<p class="petit">Un autre benchmark présente un contrat, un périmètre d’exemples, un environnement, '
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
    content = ('<div class="table-defile" role="region" tabindex="0" aria-label="Configuration demandée, observée et source de chaque observation">'
               '<table class="g-tab-conf"><caption class="cache">Configuration demandée, observée '
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
        content += '<p class="petit">Observations inconnues : ' + text(', '.join(unknown)) + '.</p>'
    return content


def _row_detail(value, row):
    """Détail d’un modèle sur un exemple : constats, mesures, coût de la vérification, preuves, limites"""
    content = '<dl class="cles"><dt>Résultat</dt><dd>' + badge(row['verdict']) + '</dd></dl>'
    if row['next_action']:
        content += '<p>' + text(row['next_action']) + '</p>'
    content += '<h4>Constats</h4>' + listing(
        finding['label'] + ' : ' + STATES[finding['status']] + ' · ' + finding['finding']
        for finding in row['findings'])
    if row['measures']:
        content += '<h4>Mesures</h4><ul>'
        for measure in row['measures']:
            shown = valeur_mesure(measure['value'], measure['unit']).capitalize()
            content += '<li>' + text(measure['label'] + ' : ' + shown)
            if measure['reason']:
                content += text(' (' + measure['reason'] + ')')
            content += '</li>'
        content += '</ul>'
    judgment_cost = row['judgment']['cost']
    content += '<p>Coût de la vérification : ' + text(
        'inconnu' if judgment_cost['value'] is None else
        montant_lisible(judgment_cost['value']) + ' ' + judgment_cost['unit']) + '</p>'
    if row['judgment']['professional_review']:
        review = row['judgment']['professional_review']
        content += '<p>Revue professionnelle annoncée : ' + text(review['author'] + ', ' + review['phase']
                                                               + ', ' + review['scope']) + '</p>'
    else:
        content += '<p>Revue professionnelle : aucune.</p>'
    content += '<h4>Preuves</h4>' + _proofs(value, row)
    content += _configuration_table(row)
    content += '<p class="petit">Évaluée le ' + text(jour_lisible(row['created_at'])) + ' par ' + text(row['responsible']) + '.</p>'
    content += '<h4>Limites</h4>' + listing(row['limits'])
    return content


def _table(value, columns):
    panel = _panel(value)
    quality = [column for column in columns if not column['cost']]
    scope = _scope(value, columns)
    content = ('<section aria-labelledby="t-comp"><div class="zone-titre"><h2 id="t-comp">Comparaison des modèles</h2><span class="petit">'
               + scope + '</span></div>')
    content += _active_filters(value) + _filters(value, columns)
    if not value['rows']:
        content += ('<p class="etat-ligne">Aucune réponse ne correspond à ces filtres. Retirez un filtre pour revoir les autres : '
                    'rien n’a été supprimé.</p>')
    details = ''
    for case in value['cases']:
        rows = [row for row in value['rows'] if row['case_position'] == case['position']]
        if not rows and (len(value['cases']) == 1 or (value.get('filter_scope') or {}).get('case') != str(case['position'])):
            continue
        multiple = len(value['cases']) > 1
        lines = []
        for row in rows:
            configuration = panel[row['panel_position']]
            hints = ''
            if row.get('repetition'):
                hints += '<p class="petit">' + text('Essai ' + str(row['repetition'])) + '</p>'
            if row.get('relaunch'):
                hints += '<p class="petit">' + text('Réponse obtenue à la tentative ' + str(row['relaunch'])
                                                    + ', après un incident fournisseur') + '</p>'
            if row.get('recovery_limit'):
                hints += '<p class="petit">' + text('Reprise après arrêt pour longueur : longueur de réponse de '
                                                    + str(row['recovery_limit']) + ' jetons') + '</p>'
            measures = ''
            if quality:
                measures = '<ul class="mesures">'
                for column in quality:
                    metric = row['metrics'][column['id']]
                    measures += '<li><b>' + text(column['label']) + '</b> : ' + text(_metric(metric))
                    if metric['rank'] is None:
                        measures += ' <span class="nc">non comparable : ' + text(metric['reason']) + '</span>'
                    measures += '</li>'
                measures += '</ul>'
            cost = row['metrics']['cost']
            cost_html = text('Inconnu' if cost['value'] is None else montant_lisible(cost['value']) + ' ' + cost['unit'])
            if cost['value'] is not None and cost['rank'] is None:
                cost_html += ' <span class="nc">non comparable : ' + text(cost['reason']) + '</span>'
            name = configuration['name']
            link = ('<span class="actions"><a class="lien" href="#' + _anchor(row) + '">Preuves<span class="cache"> et détail de '
                    + text(name) + '</span>' + ic('fleche') + '</a>'
                    + info_modele(name, [('Fournisseur', configuration['provider']), ('Identifiant', configuration['model']),
                                         ('Raisonnement', effort_label(configuration))]) + '</span>')
            lines.append(ligne_modele('reponse-' + str(row['panel_position']) + '-' + str(row['case_position']), name,
                                      effort_label(configuration), badge(row['verdict']) + '<p class="motif">' + text(row['reason']) + '</p>',
                                      measures, cost_html, link, sous_nom_html=hints))
            details += tiroir((_example(case['position']) + ' · ' if multiple else '') + _candidate(configuration),
                              _row_detail(value, row), ident=_anchor(row))
        if rows:
            content += table_cas(_example(case['position']), _plural(len(rows), 'réponse') + ' affichée' + ('s' if len(rows) > 1 else ''),
                                 lines, region=_example(case['position']) if multiple else 'Comparaison des modèles')
    content += '</section>'
    if details:
        content += ('<section aria-labelledby="t-det"><div class="zone-titre"><h2 id="t-det">Détail et preuves</h2></div>'
                    + details + '</section>')
    return content


def _pending(value):
    if not value['pending']:
        return ''
    panel, content = _panel(value), '<section aria-labelledby="t-att"><div class="zone-titre"><h2 id="t-att">Réponses en attente ou incidents</h2></div><ul>'
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
        content += '<section aria-labelledby="t-inc"><div class="zone-titre"><h2 id="t-inc">Inconnues et incidents</h2></div><ul>'
        content += ''.join('<li>' + text(item) + '</li>' for item in value['unknowns'])
        content += '</ul></section>'
    return content


def _restrictions(value):
    return ('<aside class="g-pied-pub" aria-labelledby="t-pied"><h2 id="t-pied">Ce que cette publication permet de vérifier</h2><ul>'
            + ''.join('<li>' + text(item) + '</li>' for item in value['restrictions']) + '</ul></aside>')


def _retired(title, sentence, word, back):
    """État de retrait : le code « Retiré », son mot de statut, une phrase, le retour vers l’épreuve et le catalogue"""
    return ('<section class="g-err" data-ton="inc"><div class="g-err-code" aria-hidden="true">'
            '<span class="disque" aria-hidden="true"></span><b>Retiré</b></div><div class="g-err-texte">' + mot(word, 'inc')
            + '<p>' + text(sentence) + '</p><div class="actions">' + back + lien('/epreuves', 'Toutes les épreuves') + '</div></div></section>')


def contexte_publication(value):
    """Puces de la page publiée : version de l’exemple, ruban, retours vers l’épreuve et le catalogue"""
    if value.get('unavailable') or value.get('withdrawn'):
        return ''
    return (puce('Version ' + str(value['version_id'])) + ruban('Exemple inventé')
            + lien('/epreuves/' + text(value['task_id']) + '/' + text(value['version_id']), 'Retour à l’épreuve', 'retour')
            + lien('/epreuves', 'Toutes les épreuves'))


def render_results(value):
    back = lien('/epreuves/' + text(value['task_id']) + '/' + text(value['version_id']), 'Retour à l’épreuve', 'retour')
    if value.get('unavailable') == 'trial-withdrawn':
        return 'Résultats retirés', _retired('Résultats retirés', 'Cette épreuve est retirée : ses résultats publiés et leurs '
                                              'pièces ne sont plus accessibles.', 'Épreuve', back)
    if value.get('withdrawn'):
        return 'Restitution retirée', _retired('Restitution retirée', 'Cette restitution est retirée. Son contenu et ses pièces ne sont plus '
                                                'disponibles. L’épreuve et les autres restitutions restent consultables.', 'Restitution', back)
    columns = value['columns']
    coverage = value['coverage']
    content = '<div class="pile-l"><p class="texte-l">' + text(SOUS_TITRE) + '</p>'
    content += '<p>Travail évalué : ' + text(value['work']) + '</p>'
    content += '<p class="petit">' + text(value['conclusion']['text'] + ' ' + value['conclusion']['attribution']) + '</p>'
    dates = sorted({date[:10] for date in value['acquisition_dates']})
    subtitle = (str(coverage['attempted_cells']) + (' essai lancé' if coverage['attempted_cells'] == 1 else ' essais lancés')
                + ' sur ' + str(coverage['planned_cells']))
    if dates:
        subtitle += ' · ' + ('reçues le ' + jour_lisible(dates[0]) if len(dates) == 1 else
                             'reçues du ' + jour_lisible(dates[0]) + ' au ' + jour_lisible(dates[-1]))
    examples = ''
    # Bilan sur toute la population : les filtres ne réduisent que le tableau
    for case in value['cases']:
        rows = [row for row in value['population_rows'] if row['case_position'] == case['position']]
        counts = ''
        for verdict, ton, one, many in (('SATISFAIT', 'ok', 'satisfait', 'satisfont'), ('NE SATISFAIT PAS', 'ko', 'ne satisfait pas', 'ne satisfont pas'),
                                        (None, 'att', 'à reprendre', 'à reprendre')):
            count = sum(row['verdict'] == verdict for row in rows)
            if count:
                counts += tampon(ton, str(count) + ' ' + (one if count == 1 else many))
        if rows:
            examples += bilan_exemple(_example(case['position']), ' <span class="petit">' + text(_plural(len(rows), 'réponse')) + '</span>', counts)
    signals = (signal(mot('Coûts', 'ok'), 'Comparaison des coûts complète.') if value['economic_status'] == 'COMPLETE' else
               signal(mot('Coûts', 'inc'), 'Comparaison des coûts incomplète : certains coûts observés manquent ou ne sont pas comparables.'))
    signals += ''.join(signal(mot('Limite', 'vig'), text(limit)) for limit in value['conclusion']['limits'])
    evaluated = coverage['evaluated_attempts']
    content += bilan('Aucune réponse vérifiée' if not evaluated else _plural(evaluated, 'réponse') + ' vérifiée' + ('s' if evaluated > 1 else ''),
                     subtitle, examples, signals)
    content += _table(value, columns)
    content += _pending(value)
    content += _conditions(value)
    content += _campaigns(value)
    content += _unknowns(value)
    content += tiroir('Comment lire ces résultats', '<ul>'
                      '<li><strong>Satisfait</strong> : toutes les exigences sont respectées et aucune erreur éliminatoire n’a été relevée.</li>'
                      '<li><strong>Ne satisfait pas</strong> : une exigence n’est pas respectée ou une erreur éliminatoire a été relevée ; '
                      'la réponse n’est pas utilisable, quel que soit son coût.</li>'
                      '<li><strong>À reprendre</strong> : la preuve ne permet pas encore de conclure ; ce n’est pas un échec du modèle.</li>'
                      '<li>Les verdicts et les mesures sont ceux enregistrés lors du benchmark ; cette page ne relance aucune vérification.</li>'
                      '<li>Les tris ordonnent les seules valeurs classables. Les valeurs inconnues ou incompatibles restent sans rang, '
                      'sans être traitées comme défavorables.</li></ul>', ident='method')
    content += _restrictions(value) + '</div>'
    return 'Benchmark : ' + value['title'], content


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
