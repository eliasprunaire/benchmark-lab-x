"""Read-only S6 comparisons and the private preview of a fictional local projection"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from urllib.parse import parse_qsl, urlencode

from .validation import identifier
from .acquisition import campaigns as c
from . import evaluation as e, model_catalogue, preparation as p, qualification as q
from .publications import SCHEMA, PRESENTATION_VERSION, _decode
from .storage import ConflictError, IntegrityError, _strict_json as encode

ATTRIBUTION = (
    'Le verdict vaut pour chaque modèle tel qu’il a été réglé et appelé ici, dans les conditions communes décrites. '
    'Le fournisseur, le niveau de raisonnement, Pi (l’outil qui fait travailler les modèles) et ses réglages influent aussi sur la réponse : le verdict ne les sépare pas du modèle. '
    'Avec un autre outil, un autre contexte ou un autre environnement, le résultat peut être différent.')
NO_USABLE_RESPONSE = 'Aucune réponse exploitable de ce modèle : il n’est pas évalué et n’entre pas dans la comparaison.'
LIMIT = 'Ces résultats portent sur un exemple inventé. Ils ne se transposent pas tels quels à vos dossiers réels et ne s’additionnent pas avec ceux d’autres cas ou d’autres comparaisons.'
VERDICTS = ('SATISFAIT', 'NE SATISFAIT PAS', 'A_REPRENDRE', 'INDETERMINE')
FILTERS = ('case', 'sort', 'direction', 'verdict', 'obligation', 'configuration')


# Lues dans le reçu HTTP conservé ; un reçu illisible ne fait avancer aucune cause
RESPONSE_CAUSES = {'EMPTY_OUTPUT': 'réponse terminée sans texte',
                   'ROUTE_ERROR': 'fournisseur indisponible ou limite de débit atteinte',
                   'CONTENT_REFUSAL': 'refus du modèle'}


def _response_cause(attempt):
    """Cause lisible d'une réponse inexploitable, ou None si le reçu ne l'établit pas"""
    from .acquisition import recovery
    try:
        kind = recovery.observation(attempt)['kind']
    except (ValueError, KeyError, TypeError, AttributeError, ConflictError, IntegrityError):
        return None
    if kind == 'LENGTH':
        limit = attempt['operation']['requested_configuration'].get('parameters', {}).get('max_tokens')
        return 'arrêt pour longueur' + (f', plafond demandé : {limit} jetons de sortie' if type(limit) is int else '')
    return RESPONSE_CAUSES.get(kind)


def campaign_url(dossier_id, campaign_id):
    return f'/preparation/dossiers/{identifier(dossier_id)}/campaigns/{identifier(campaign_id)}'


def query_parameters(raw):
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, errors='strict')
    if len(dict(pairs)) != len(pairs) or any(k not in FILTERS for k, _ in pairs):
        raise ValueError('Paramètre inconnu ou répété')
    return {key: value for key, value in pairs if value}


def _orderable(definition):
    unit = definition.get('unit', '').strip().lower()
    scale = definition.get('scale')
    ordinal = (type(scale) is list and len(scale) > 1 and len(set(scale)) == len(scale)
               and all(type(value) is str and value for value in scale)
               and definition.get('favorable') in (scale[0], scale[-1]))
    return (bool(definition.get('measure', '').strip()) and bool(definition.get('proof', '').strip())
            and (ordinal or (bool(unit) and unit not in ('descriptif', 'descriptive', 'texte', 'text', 'description')
            and definition.get('favorable', '') in ('lower', 'higher', 'yes')
            and (definition.get('favorable', '') != 'yes' or unit in ('bool', 'boolean', 'booléen')))))


def _criterion_definition(criterion):
    definition = deepcopy(criterion)
    if 'scale' in definition:
        definition.update(measure=definition['label'], proof='Passages de la sortie cités exactement',
                          unit='descriptif', aggregation='Aucune agrégation')
    return definition


def _number(value, unit):
    if unit.strip().lower() in ('bool', 'boolean', 'booléen'):
        return Decimal(int(value)) if type(value) is bool else None
    if type(value) not in (str, int, float):
        return None
    if isinstance(value, str) and not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def _queries(query, campaign, spec, columns):
    if type(query) is not dict or any(type(k) is not str or type(v) is not str for k, v in query.items()):
        raise ValueError('Paramètres textuels requis')
    allowed = {
        'case': {x['id'] for x in campaign['cases']},
        'sort': {x['id'] for x in columns},
        'direction': {'asc', 'desc'}, 'verdict': set(VERDICTS),
        'configuration': {x['id'] for x in campaign['panel']},
        'obligation': {x['id'] + ':' + status for x in spec['obligations']
                       for status in ('PASS', 'FAIL', 'INDETERMINE')},
    }
    if any(k not in allowed or v not in allowed[k] for k, v in query.items()):
        raise ValueError('Filtre ou tri hors contrat')


def _metric(row, column):
    return (row['cost'] if 'criterion_id' not in column else
            next(m for m in row['measures'] if m['criterion_id'] == column['criterion_id']))


def _metric_number(metric):
    definition = metric.get('definition', {})
    scale = definition.get('scale')
    if type(scale) is list and metric.get('value') in scale:
        index = scale.index(metric['value'])
        return Decimal(len(scale) - index if definition.get('favorable') == scale[0] else index + 1)
    value = _number(metric['value'], metric['unit'])
    if value is None:
        raise ValueError('Mesure non ordonnable')
    return value


def _rank(rows, columns):
    for case_id in dict.fromkeys(r['case_id'] for r in rows):
        group = [r for r in rows if r['case_id'] == case_id]
        for column in columns:
            metrics = [_metric(row, column) for row in group]
            known = [m for m in metrics if m['reason'] is None]
            values = [_metric_number(m) for m in known]
            higher = column['favorable'] in ('higher', 'yes')
            for metric, value in zip(known, values):
                metric['rank'] = 1 + sum(other > value if higher else other < value for other in values)


def _recommendation(rows, columns, case_count, coverage, pending):
    """Recommend the strongest observed quality, using cost only to break an exact tie"""
    if (case_count != 1 or pending or coverage['not_started']
            or coverage['decided_attempts'] != coverage['planned_cells']
            or len({row['configuration_id'] for row in rows}) != len(rows)):
        return None
    eligible = [row for row in rows if row['verdict'] == 'SATISFAIT']
    quality = [column for column in columns if 'criterion_id' in column]
    if (len(eligible) < 2 or not quality or any(row['cost']['rank'] is None for row in eligible)
            or any(_metric(row, column)['rank'] is None for row in eligible for column in quality)):
        return None
    quality_vectors = {row['attempt_id']: tuple(_metric_number(_metric(row, column)) for column in quality)
                       for row in eligible}
    frontier = [row for row in eligible if not any(
        all(left >= right for left, right in zip(quality_vectors[other['attempt_id']],
                                                quality_vectors[row['attempt_id']]))
        and any(left > right for left, right in zip(quality_vectors[other['attempt_id']],
                                                   quality_vectors[row['attempt_id']]))
        for other in eligible if other is not row)]
    if len({quality_vectors[row['attempt_id']] for row in frontier}) != 1:
        return None
    cheapest = min(_metric_number(row['cost']) for row in frontier)
    winners = [row for row in frontier if _metric_number(row['cost']) == cheapest]
    if len(winners) != 1:
        return None
    row = winners[0]
    basis = ('equal_quality_cost' if len(set(quality_vectors.values())) == 1 else 'quality_then_cost')
    return dict(configuration=deepcopy(row['requested_configuration']), count=len(eligible),
                amount=row['cost']['value'], unit=row['cost']['unit'], basis=basis,
                quality=[deepcopy(column['definition']) for column in quality], detail_href=row['detail_href'])


def _comparison(store, connection, session_id, dossier_id, campaign_id, query):
    p.owner(connection, session_id, dossier_id)
    identifier(campaign_id)
    campaign = next(iter(c.projection(store, connection, dossier_id, campaign_id)), None)
    if campaign is None:
        raise p.Denied('Campagne inaccessible')
    contract = c._approved(store, connection, campaign['contract_sha256'])
    spec = contract['specification']
    basis = campaign['cost_basis']
    columns = [dict(id='cost', definition=deepcopy(basis), unit=basis['unit'], favorable='lower',
                    proof='Coût observé et source du reçu candidat de chaque tentative')]
    used = {'cost'} | {m['id'] for m in spec['secondary_criteria']}
    for measure in spec['secondary_criteria']:
        definition = _criterion_definition(measure)
        if not _orderable(definition):
            continue
        key = measure['id']
        if key == 'cost':
            while key in used:
                key = 'criterion:' + key
        used.add(key)
        scale = definition.get('scale')
        favorable = ('higher' if type(scale) is list and definition.get('favorable') == scale[0]
                     else 'lower' if type(scale) is list else measure['favorable'])
        columns.append(dict(id=key, criterion_id=measure['id'], definition=definition,
                            unit=definition['unit'], favorable=favorable, proof=definition['proof']))
    _queries(query, campaign, spec, columns)
    records = e.projection(store, connection, dossier_id, campaign_id)
    from . import automatic_judgment as auto
    # Reprises techniques (RULES.md §9) : chaque reprise vise une tentative ; seule la dernière d'une chaîne compte
    descendants = auto.family(connection, campaign_id)[1:]
    snapshots = [c._inspect(store, connection, cid) for cid in descendants]
    # Une reprise arrêtée avant tout envoi n'a pas eu lieu : la tentative source reste la dernière
    held = {a['operation_id'] for _, a in auto._attempts(store, connection, campaign_id)}
    parents = {a['operation_id']: s['manifest']['recovery_of'] for s in snapshots for a in s['attempts']
               if a['operation_id'] in held}
    superseded = set(parents.values())
    every = campaign['attempts'] + [a for cid in descendants
                                    for a in c.projection(store, connection, dossier_id, cid)[0]['attempts']
                                    if a['operation_id'] in held]
    visible = [a for a in every if a['operation_id'] not in superseded]

    def recoveries(attempt_id):
        count = 0
        while attempt_id in parents:
            attempt_id, count = parents[attempt_id], count + 1
        return count
    causes = {}
    latest = {record['attempt_id']: record for record in records}
    concerned = {a['operation_id'] for a in visible
                 if a['state'] == 'RECEIVED' and not a['answered'] and a['operation_id'] not in latest}
    if concerned:
        causes = {a['operation_id']: _response_cause(a)
                  for s in [c._inspect(store, connection, campaign_id)] + snapshots
                  for a in s['attempts'] if a['operation_id'] in concerned}
    pending: list[dict] = [dict(attempt_id=a['operation_id'], verdict=None,
                    state='REVIEW_REQUIRED' if a['state'] == 'RECEIVED' and a['incident'] is None else 'EXECUTION_REQUIRED',
                    next_action='Cette réponse doit être relue et son évaluation terminée avant de conclure.')
               for a in visible if a['operation_id'] not in latest]
    if pending and connection.execute('SELECT 1 FROM s2_comparison_contracts WHERE contract_sha256=?',
                          (campaign['contract_sha256'],)).fetchone():
        progress = campaign.get('judgment') or auto.status(store, connection, campaign_id)
        progress_status = progress.get('status')
        progress_reason = progress.get('reason')
        if type(progress_status) is not str or (progress_reason is not None and type(progress_reason) is not str):
            raise ValueError('Progression du jugement invalide')
        attempts = {a['operation_id']: a for a in visible}
        for attempt in pending:
            # Rien n'a été envoyé au juge : état terminal, la cellule reste comptée comme non couverte
            if attempts[attempt['attempt_id']]['state'] == 'RECEIVED' and not attempts[attempt['attempt_id']]['answered']:
                cell = next(c for c in campaign['cells'] if c['cell_id'] == attempts[attempt['attempt_id']]['cell_id'])
                attempt.update(state='NO_USABLE_RESPONSE', next_action=NO_USABLE_RESPONSE,
                               configuration_id=cell['configuration_id'], cause=causes.get(attempt['attempt_id']),
                               recoveries=recoveries(attempt['attempt_id']))
            elif attempt['state'] == 'REVIEW_REQUIRED':
                attempt.update(state='EVALUATION_' + progress_status,
                    next_action=progress_reason or 'Réponse reçue. Son évaluation automatique n’est pas terminée.')
    pending += [dict(attempt_id=record['attempt_id'], **record['decision'])
                for record in latest.values() if record['decision']['verdict'] is None]
    pending += e.pending_judgments(store, connection, campaign_id, latest)
    rows = []
    base = campaign_url(dossier_id, campaign_id)
    suffix = '?' + urlencode(query) if query else ''
    for record in latest.values():
        row = deepcopy(record)
        row['verdict'] = row['decision']['verdict']
        # Une reprise est une configuration distincte, dite sur sa ligne (RULES.md §5)
        row['recovery_limit'] = (record['requested_configuration']['parameters']['max_tokens']
                                 if record['attempt_id'] in parents else None)
        attempt = next(a for a in visible if a['operation_id'] == record['attempt_id'])
        incompatible = ('Impossible de confirmer que la réponse vient du modèle demandé' if record['attribution_incident'] else
                        'Un problème technique empêche de rattacher la réponse au modèle' if record['incident'] == 'HARNESS_ERROR' else
                        'Réponse absente ou envoi de l’appel non confirmé' if record['output_piece_id'] is None or attempt['emission'] != 'ESTABLISHED' else None)
        cost = record['candidate_cost']
        metric = dict(value=None if cost is None else cost['amount'], unit=basis['unit'] if cost is None else cost['currency'], rank=None,
                      source='INCONNU' if cost is None else cost['source'], reason=incompatible)
        if metric['reason'] is None:
            if cost is None or cost['status'] != 'KNOWN':
                metric['reason'] = 'Coût INCONNU : reçu ou mesure absent'
            elif record['cost_basis'] != basis or cost['currency'] != basis['unit']:
                metric['reason'] = 'Base ou unité de coût incompatible ; aucune conversion implicite'
            elif _number(metric['value'], metric['unit']) is None:
                metric['reason'] = 'Coût non interprétable sur l’unité déclarée'
        row['cost'] = metric
        for measure in row['measures']:
            measure.update(rank=None, reason=incompatible)
            if measure['reason'] is None:
                if not _orderable(measure['definition']):
                    measure['reason'] = 'Cette observation est décrite en mots et ne se classe pas'
                elif measure['status'] != 'KNOWN' or not measure['evidence']:
                    measure['reason'] = 'Mesure non relevée ou sans preuve'
                else:
                    try:
                        _metric_number(measure)
                    except ValueError:
                        measure['reason'] = 'Valeur impossible à placer sur l’échelle prévue'
        row['detail_href'] = base + '/attempts/' + identifier(row['attempt_id']) + suffix
        rows.append(row)
    _rank(rows, columns)
    population = [r['attempt_id'] for r in rows]
    attempted = sum(cell['state'] != 'NOT_STARTED' for cell in campaign['cells'])
    coverage = dict(planned_cells=len(campaign['cells']), attempted_cells=attempted,
                    evaluated_attempts=len(rows), decided_attempts=sum(r['verdict'] is not None for r in rows),
                    not_started=len(campaign['cells']) - attempted)
    complete = (len(rows) == len(campaign['cells']) and all(r['cost']['rank'] is not None for r in rows))
    scope = dict(task=campaign['task'], campaign_id=campaign_id, contract_sha256=campaign['contract_sha256'],
                 cases=campaign['cases'], attempts=population, configurations=campaign['panel'],
                 conditions=campaign['conditions'], evaluation_ids=[r['evaluation_id'] for r in rows],
                 dates=[r['created_at'] for r in rows])
    conclusion = dict(text='Comparaison des réponses enregistrées, cas par cas, sur les critères fixés pour cet exemple.',
                      scope=scope, attribution=ATTRIBUTION, limits=list(dict.fromkeys([LIMIT] + spec['limits'] +
                          [limit for row in rows for limit in row['limits']])))
    selected = []
    for row in rows:
        if any(query.get(key) is not None and query[key] != row[field] for key, field in (
                ('case', 'case_id'), ('configuration', 'configuration_id'))):
            continue
        if 'verdict' in query and (None if query['verdict'] in ('A_REPRENDRE', 'INDETERMINE') else query['verdict']) != row['verdict']:
            continue
        if 'obligation' in query:
            cid, status = query['obligation'].split(':')
            if not any(f['criterion_id'] == cid and f['status'] == status for f in row['findings']):
                continue
        selected.append(row)
    ordered = []
    for case in campaign['cases']:
        group = [r for r in selected if r['case_id'] == case['id']]
        if 'sort' in query:
            key = next(column for column in columns if column['id'] == query['sort'])
            known = [r for r in group if _metric(r, key)['rank'] is not None]
            unknown = [r for r in group if _metric(r, key)['rank'] is None]
            known.sort(key=lambda r: _metric_number(_metric(r, key)),
                       reverse=query.get('direction', 'asc') == 'desc')
            group = known + unknown
        ordered.extend(group)
    payload = store.get_dossier(dossier_id, contract['revision'])
    return dict(kind='comparison', visibility='private', catalogue_admission=False,
                campaign_id=campaign_id, contract_sha256=campaign['contract_sha256'], task=campaign['task'],
                need=payload['request'], reformulation=payload['reformulation'],
                result_expected=spec['result_expected'], human_work=contract['package']['human_work'],
                conclusion=conclusion, coverage=coverage, population=population, filter_scope=deepcopy(query),
                economic_status='COMPLETE' if complete else 'INCOMPLETE', columns=columns, rows=ordered,
                # Décision d'Ayo : pas de conseil sur une comparaison qui compte une reprise (une tentative par configuration)
                recommendation=None if descendants else _recommendation(rows, columns, len(campaign['cases']), coverage, pending),
                cases=campaign['cases'], panel=campaign['panel'], conditions=campaign['conditions'],
                obligations=spec['obligations'], cost_basis=basis, cells=campaign['cells'],
                campaign_state=campaign['state'], history=records, href=base, pending_attempts=pending,
                acquisition_dates=[a['received_at'] for a in campaign['attempts'] if a['received_at']],
                stop_reason=campaign['stop_reason'], model_names=model_catalogue.display_names(store),
                dossier_href=f'/preparation/dossiers/{dossier_id}/revisions/{contract["revision"]}?campaign={campaign_id}')


def comparison(store, session_id, dossier_id, campaign_id, *, query=None):
    with store.read_snapshot() as connection:
        return p.page_view(_comparison(store, connection, session_id, dossier_id, campaign_id,
                                      {} if query is None else query))


def detail(store, session_id, dossier_id, campaign_id, attempt_id, *, query=None):
    identifier(attempt_id)
    with store.read_snapshot() as connection:
        value = _comparison(store, connection, session_id, dossier_id, campaign_id,
                            {} if query is None else query)
        return _detail(store, connection, session_id, dossier_id, value, attempt_id)


def _detail(store, connection, session_id, dossier_id, value, attempt_id):
    """Une tentative d'une comparaison déjà calculée dans ce même instantané"""
    campaign_id = value['campaign_id']
    history = [r for r in value['history'] if r['attempt_id'] == attempt_id]
    if not history:
        raise p.Denied('Tentative évaluée inaccessible')
    for record in history:
        pieces = e._pieces_bytes(store, connection, session_id, dossier_id,
                                 record['evaluation_id'], [link['piece_id'] for link in record['proof_links']])
        record['proof_contents'] = {pid: raw.decode('utf-8') for pid, raw in pieces.items()}
    return p.page_view(dict(kind='attempt_detail', campaign_id=campaign_id, task=value['task'],
                       need=value['need'], model_names=value['model_names'], conclusion=value['conclusion'], history=history,
                       filter_scope=value['filter_scope'], dossier_href=value['dossier_href'], href=value['href'],
                       back_href=value['href'] + ('?' + urlencode(value['filter_scope']) if value['filter_scope'] else '') +
                                 '#attempt-' + attempt_id))


def _task_index(store, connection, dossier_id, current, campaigns):
    revisions = [r[0] for r in connection.execute(
        'SELECT revision FROM s2_revisions WHERE dossier_id=? ORDER BY revision', (dossier_id,))]
    versions = []
    has_contracts = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s3_control'").fetchone()
    fingerprints = connection.execute('SELECT contract_sha256 FROM s3_contracts WHERE dossier_id=? ORDER BY version',
                                      (dossier_id,)).fetchall() if has_contracts else []
    for fingerprint, in fingerprints:
        contract = q._contract(store, connection, fingerprint)
        versions.append(dict(version=contract['version'], revision=contract['revision'],
            campaigns=[dict(campaign_id=v['campaign_id'], href=campaign_url(dossier_id, v['campaign_id']),
                                frozen_at=v['conditions']['frozen_at'])
                       for v in campaigns if v['contract_sha256'] == fingerprint]))
    for fingerprint, version, revision in connection.execute(
            'SELECT contract_sha256,version,revision FROM s2_comparison_contracts '
            'WHERE dossier_id=? ORDER BY version', (dossier_id,)):
        c._comparison_contract(store, connection, fingerprint)
        versions.append(dict(version=version, revision=revision,
            campaigns=[dict(campaign_id=v['campaign_id'], href=campaign_url(dossier_id, v['campaign_id']),
                                frozen_at=v['conditions']['frozen_at'])
                       for v in campaigns if v['contract_sha256'] == fingerprint]))
    return dict(dossier_id=dossier_id, need=store.get_dossier(dossier_id, current)['request'],
                revision=current, revisions=revisions, versions=versions,
                href='/preparation/dossiers/' + dossier_id)


def catalogue(store, session_id):
    with store.read_snapshot() as connection:
        if not connection.execute('SELECT 1 FROM s2_sessions WHERE session_id=?', (session_id,)).fetchone():
            raise p.Denied('Session requise')
        tasks = []
        has_campaigns = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s4_control'").fetchone()
        for dossier_id, current in connection.execute(
                'SELECT dossier_id,current_revision FROM s2_dossiers WHERE session_id=? ORDER BY dossier_id', (session_id,)).fetchall():
            campaigns = c.projection(store, connection, dossier_id) if has_campaigns else []
            tasks.append(_task_index(store, connection, dossier_id, current, campaigns))
        return p.page_view(dict(kind='catalogue', visibility='private', catalogue_admission=False, tasks=tasks))


def _publishable(store, value):
    """Pièces liées qu'une publication peut montrer : jamais la référence réservée au juge (RULES §4)"""
    linked = {link['piece_id'] for row in value['rows'] for link in row['proof_links']}
    # Les sorties candidates sont stockées pour le juge : seules les autres pièces du juge forment la référence
    outputs = {row.get('output_piece_id') for row in value['rows']}
    return {pid for pid in linked if pid in outputs or store.get_piece(pid)['role'] != 'judge'}


def _preview(store, value, piece_ids, presentation):
    if type(piece_ids) is not list or any(type(pid) is not str for pid in piece_ids) or len(set(piece_ids)) != len(piece_ids):
        raise ValueError('Liste explicite de pièces uniques requise')
    linked = _publishable(store, value)
    if not set(piece_ids) <= linked:
        raise p.Denied('Pièce non liée à la restitution ou réservée à l’évaluation')
    selected = {pid: 'piece-' + identifier(pid) + '.txt' for pid in sorted(piece_ids)}
    files = {name: store.read_piece(pid) for pid, name in selected.items()}
    if presentation is None:
        raise ValueError('Présentation de projection non enregistrée')
    files['index.html'] = presentation.public_page(value, selected)
    files['style.css'] = presentation.stylesheet()
    manifest = dict(schema_version=SCHEMA, presentation_version=PRESENTATION_VERSION, conclusion_version='1',
                    campaign_id=value['campaign_id'], contract_sha256=value['contract_sha256'], task=value['task'],
                    evaluation_ids=[row['evaluation_id'] for row in value['rows']],
                    limits=value['conclusion']['limits'],
                    pieces={pid: dict(file=name, sha256=sha256(files[name]).hexdigest()) for pid, name in selected.items()},
                    files={name: sha256(raw).hexdigest() for name, raw in files.items()})
    raw = encode(manifest).encode('utf-8')
    return dict(manifest=raw, files=files, projection_sha256=sha256(raw).hexdigest())


def preview(store, session_id, dossier_id, campaign_id, *, piece_ids, presentation=None):
    with store.read_snapshot() as connection:
        e.connection_for(store)
        value = _comparison(store, connection, session_id, dossier_id, campaign_id, {})
        return _preview(store, value, piece_ids, presentation)


def preview_view(store, session_id, dossier_id, campaign_id, *, piece_ids, presentation=None):
    with store.read_snapshot() as connection:
        e.connection_for(store)
        value = _comparison(store, connection, session_id, dossier_id, campaign_id, {})
        bundle = _preview(store, value, piece_ids, presentation)
        publishable = _publishable(store, value)
        links = {link['piece_id']: link for row in value['rows'] for link in row['proof_links']
                 if link['piece_id'] in publishable}
        return dict(kind='projection_preview', comparison=p.page_view(value), pieces=list(links.values()),
                    selected_links={pid: links[pid]['href'] for pid in piece_ids},
                    manifest=_decode(bundle['manifest']), projection_sha256=bundle['projection_sha256'])
