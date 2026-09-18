"""Read-only S6 comparisons and the private preview of a fictional local projection"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from urllib.parse import parse_qsl, urlencode

from .validation import identifier
from .acquisition import campaigns as c
from . import evaluation as e, preparation as p, qualification as q
from .publications import SCHEMA, PRESENTATION_VERSION, _decode
from .storage import _transaction, _strict_json as encode

ATTRIBUTION = (
    'Le verdict porte sur la configuration observée sous les conditions communes déclarées. '
    'Il n’attribue pas au seul modèle les effets du fournisseur, de l’effort, de Pi ou de ses réglages. '
    'Il ne démontre pas le même résultat sous un autre harnais, contexte ou environnement.')
LIMIT = 'Observations fictives locales, sans généralisation aux dossiers réels ni agrégation entre cas ou campagnes.'
VERDICTS = ('SATISFAIT', 'NE SATISFAIT PAS', 'A_REPRENDRE', 'INDETERMINE')
FILTERS = ('case', 'sort', 'direction', 'verdict', 'obligation', 'configuration')


def campaign_url(dossier_id, campaign_id):
    return f'/preparation/dossiers/{identifier(dossier_id)}/campaigns/{identifier(campaign_id)}'


def query_parameters(raw):
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, errors='strict')
    if len(dict(pairs)) != len(pairs) or any(k not in FILTERS for k, _ in pairs):
        raise ValueError('Paramètre inconnu ou répété')
    return {key: value for key, value in pairs if value}


def _orderable(definition):
    unit = definition.get('unit', '').strip().lower()
    return (bool(definition.get('measure', '').strip()) and bool(definition.get('proof', '').strip())
            and bool(unit) and unit not in ('descriptif', 'descriptive', 'texte', 'text', 'description')
            and definition.get('favorable', '') in ('lower', 'higher', 'yes')
            and (definition.get('favorable', '') != 'yes' or unit in ('bool', 'boolean', 'booléen')))


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
        if not _orderable(measure):
            continue
        key = measure['id']
        if key == 'cost':
            while key in used:
                key = 'criterion:' + key
        used.add(key)
        columns.append(dict(id=key, criterion_id=measure['id'], definition=deepcopy(measure),
                            unit=measure['unit'], favorable=measure['favorable'], proof=measure['proof']))
    _queries(query, campaign, spec, columns)
    records = e.projection(store, connection, dossier_id, campaign_id)
    latest = {record['attempt_id']: record for record in records}
    pending = [dict(attempt_id=a['operation_id'], verdict=None,
                    state='REVIEW_REQUIRED' if a['state'] == 'RECEIVED' and a['incident'] is None else 'EXECUTION_REQUIRED',
                    next_action='Inspecter cette tentative et compléter son évaluation avant finalisation')
               for a in campaign['attempts'] if a['operation_id'] not in latest]
    if connection.execute('SELECT 1 FROM s2_comparison_contracts WHERE contract_sha256=?',
                          (campaign['contract_sha256'],)).fetchone():
        from . import automatic_judgment as auto
        progress = auto.status(store, connection, campaign_id)
        for attempt in pending:
            if attempt['state'] == 'REVIEW_REQUIRED':
                attempt.update(state='EVALUATION_' + progress['status'],
                    next_action=progress['reason'] or 'Réponse reçue et conservée. Son évaluation automatique reste à terminer.')
    pending += [dict(attempt_id=record['attempt_id'], **record['decision'])
                for record in latest.values() if record['decision']['verdict'] is None]
    pending += e.pending_judgments(store, connection, campaign_id, latest)
    rows = []
    base = campaign_url(dossier_id, campaign_id)
    suffix = '?' + urlencode(query) if query else ''
    for record in latest.values():
        row = deepcopy(record)
        row['historical_verdict'] = record['verdict'] if record['engine_version'] == e.FORMAT_IDENTITY else None
        row['verdict'] = row['decision']['verdict']
        attempt = next(a for a in campaign['attempts'] if a['operation_id'] == record['attempt_id'])
        incompatible = ('Attribution requise absente ou incompatible' if record['attribution_incident'] else
                        'Incident du harnais empêchant l’attribution' if record['incident'] == 'HARNESS_ERROR' else
                        'Sortie ou émission non établie' if record['output_piece_id'] is None or attempt['emission'] != 'ESTABLISHED' else None)
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
                    measure['reason'] = 'Observation descriptive : aucune échelle ordonnable déclarée'
                elif measure['status'] != 'KNOWN' or not measure['evidence']:
                    measure['reason'] = 'Mesure ou preuve absente'
                elif _number(measure['value'], measure['unit']) is None:
                    measure['reason'] = 'Valeur non interprétable sur l’échelle déclarée'
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
    conclusion = dict(text='Comparaison des observations conservées, par cas et tentative, sur les critères du contrat.',
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
                cases=campaign['cases'], panel=campaign['panel'], conditions=campaign['conditions'],
                obligations=spec['obligations'], cost_basis=basis, cells=campaign['cells'],
                campaign_state=campaign['state'], history=records, href=base, pending_attempts=pending,
                acquisition_dates=[a['received_at'] for a in campaign['attempts'] if a['received_at']],
                stop_reason=campaign['stop_reason'],
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
    history = [r for r in value['history'] if r['attempt_id'] == attempt_id]
    if not history:
        raise p.Denied('Tentative évaluée inaccessible')
    for record in history:
        record['proof_contents'] = {
            link['piece_id']: e.piece_bytes(store, session_id, dossier_id,
                                           record['evaluation_id'], link['piece_id']).decode('utf-8')
            for link in record['proof_links']
        }
    return p.page_view(dict(kind='attempt_detail', campaign_id=campaign_id, task=value['task'],
                       need=value['need'], conclusion=value['conclusion'], history=history,
                       filter_scope=value['filter_scope'], dossier_href=value['dossier_href'],
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
            campaigns=[dict(campaign_id=v['campaign_id'], href=campaign_url(dossier_id, v['campaign_id']))
                       for v in campaigns if v['contract_sha256'] == fingerprint]))
    for fingerprint, version, revision in connection.execute(
            'SELECT contract_sha256,version,revision FROM s2_comparison_contracts '
            'WHERE dossier_id=? ORDER BY version', (dossier_id,)):
        c._comparison_contract(store, connection, fingerprint)
        versions.append(dict(version=version, revision=revision,
            campaigns=[dict(campaign_id=v['campaign_id'], href=campaign_url(dossier_id, v['campaign_id']))
                       for v in campaigns if v['contract_sha256'] == fingerprint]))
    return dict(dossier_id=dossier_id, need=store.get_dossier(dossier_id, current)['request'],
                revision=current, revisions=revisions, versions=versions,
                href='/preparation/dossiers/' + dossier_id)


def catalogue(store, session_id):
    connection = p.connection_for(store)
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


def _preview(store, value, piece_ids, presentation):
    if type(piece_ids) is not list or any(type(pid) is not str for pid in piece_ids) or len(set(piece_ids)) != len(piece_ids):
        raise ValueError('Liste explicite de pièces uniques requise')
    linked = {link['piece_id'] for row in value['rows'] for link in row['proof_links']}
    if not set(piece_ids) <= linked:
        raise p.Denied('Pièce non liée à la restitution')
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
    connection = e.connection_for(store)
    with _transaction(connection):
        value = _comparison(store, connection, session_id, dossier_id, campaign_id, {})
        return _preview(store, value, piece_ids, presentation)


def preview_view(store, session_id, dossier_id, campaign_id, *, piece_ids, presentation=None):
    connection = e.connection_for(store)
    with _transaction(connection):
        value = _comparison(store, connection, session_id, dossier_id, campaign_id, {})
        bundle = _preview(store, value, piece_ids, presentation)
        links = {link['piece_id']: link for row in value['rows'] for link in row['proof_links']}
        return dict(kind='projection_preview', comparison=p.page_view(value), pieces=list(links.values()),
                    selected_links={pid: links[pid]['href'] for pid in piece_ids},
                    manifest=_decode(bundle['manifest']), projection_sha256=bundle['projection_sha256'])
