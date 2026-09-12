"""Read-only S6 comparisons and explicitly approved fictional local projections"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import re
import secrets
import stat
from urllib.parse import parse_qsl, urlencode

from . import campaigns as c, evaluation as e, preparation as p, qualification as q
from .storage import _transaction, _strict_json as encode, _unique_object

SCHEMA = 'benchmark-lab-x/restitution-fictional/v1'
ATTRIBUTION = (
    'Le verdict porte sur la configuration observée sous les conditions communes déclarées. '
    'Il n’attribue pas au seul modèle les effets du fournisseur, de l’effort, de Pi ou de ses réglages. '
    'Il ne démontre pas le même résultat sous un autre harnais, contexte ou environnement.')
LIMIT = 'Observations fictives locales, sans généralisation aux dossiers réels ni agrégation entre cas ou campagnes.'
VERDICTS = ('SATISFAIT', 'NE SATISFAIT PAS', 'A_REPRENDRE', 'INDETERMINE')
FILTERS = ('case', 'sort', 'direction', 'verdict', 'obligation', 'configuration')
_FILES = re.compile(r'[A-Za-z0-9_-]+\.(?:html|css|txt)\Z')


def campaign_url(dossier_id, campaign_id):
    return f'/preparation/dossiers/{p.identifier(dossier_id)}/campaigns/{p.identifier(campaign_id)}'


def query_parameters(raw):
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, errors='strict')
    if len(dict(pairs)) != len(pairs) or any(k not in FILTERS for k, _ in pairs):
        raise ValueError('Paramètre inconnu ou répété')
    return dict(pairs)


def _orderable(definition):
    unit = definition['unit'].strip().lower()
    return (bool(definition['measure'].strip()) and bool(definition['proof'].strip())
            and bool(unit) and unit not in ('descriptif', 'descriptive', 'texte', 'text', 'description')
            and definition['favorable'] in ('lower', 'higher', 'yes')
            and (definition['favorable'] != 'yes' or unit in ('bool', 'boolean', 'booléen')))


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


def _rank(rows, columns):
    for case_id in dict.fromkeys(r['case_id'] for r in rows):
        group = [r for r in rows if r['case_id'] == case_id]
        for column in columns:
            metrics = [_metric(row, column) for row in group]
            known = [m for m in metrics if m['reason'] is None]
            values = [_number(m['value'], m['unit']) for m in known]
            higher = column['favorable'] in ('higher', 'yes')
            for metric, value in zip(known, values):
                metric['rank'] = 1 + sum(other > value if higher else other < value for other in values)


def _comparison(store, connection, session_id, dossier_id, campaign_id, query):
    p.owner(connection, session_id, dossier_id)
    p.identifier(campaign_id)
    campaign = next((v for v in c.projection(store, connection, dossier_id) if v['campaign_id'] == campaign_id), None)
    if campaign is None:
        raise p.Denied('Campagne inaccessible')
    contract = q._contract(store, connection, campaign['contract_sha256'])
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
        row['detail_href'] = base + '/attempts/' + p.identifier(row['attempt_id']) + suffix
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
            known.sort(key=lambda r: _number(_metric(r, key)['value'], _metric(r, key)['unit']),
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
                dossier_href=f'/preparation/dossiers/{dossier_id}/revisions/{contract["revision"]}')


def comparison(store, session_id, dossier_id, campaign_id, *, query=None):
    connection = e.connection_for(store)
    with _transaction(connection):
        return _comparison(store, connection, session_id, dossier_id, campaign_id, {} if query is None else query)


def detail(store, session_id, dossier_id, campaign_id, attempt_id, *, query=None):
    p.identifier(attempt_id)
    value = comparison(store, session_id, dossier_id, campaign_id, query=query)
    history = [r for r in value['history'] if r['attempt_id'] == attempt_id]
    if not history:
        raise p.Denied('Tentative évaluée inaccessible')
    for record in history:
        record['proof_contents'] = {
            link['piece_id']: e.piece_bytes(store, session_id, dossier_id,
                                           record['evaluation_id'], link['piece_id']).decode('utf-8')
            for link in record['proof_links']
        }
    return dict(kind='attempt_detail', campaign_id=campaign_id, task=value['task'],
                need=value['need'], conclusion=value['conclusion'], history=history,
                filter_scope=value['filter_scope'], dossier_href=value['dossier_href'],
                back_href=value['href'] + ('?' + urlencode(value['filter_scope']) if value['filter_scope'] else '') +
                          '#attempt-' + attempt_id)


def catalogue(store, session_id):
    connection = p.connection_for(store)
    with _transaction(connection):
        if not connection.execute('SELECT 1 FROM s2_sessions WHERE session_id=?', (session_id,)).fetchone():
            raise p.Denied('Session requise')
        tasks = []
        has_contracts = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s3_control'").fetchone()
        has_campaigns = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s4_control'").fetchone()
        for dossier_id, current in connection.execute(
                'SELECT dossier_id,current_revision FROM s2_dossiers WHERE session_id=? ORDER BY dossier_id', (session_id,)).fetchall():
            revisions = [r[0] for r in connection.execute('SELECT revision FROM s2_revisions WHERE dossier_id=? ORDER BY revision', (dossier_id,))]
            campaigns = c.projection(store, connection, dossier_id) if has_campaigns else []
            versions = []
            fingerprints = connection.execute('SELECT contract_sha256 FROM s3_contracts WHERE dossier_id=? ORDER BY version',
                                              (dossier_id,)).fetchall() if has_contracts else []
            for fingerprint, in fingerprints:
                contract = q._contract(store, connection, fingerprint)
                versions.append(dict(version=contract['version'], revision=contract['revision'], contract_sha256=fingerprint,
                    campaigns=[dict(campaign_id=v['campaign_id'], href=campaign_url(dossier_id, v['campaign_id']))
                               for v in campaigns if v['contract_sha256'] == fingerprint]))
            tasks.append(dict(dossier_id=dossier_id, need=store.get_dossier(dossier_id, current)['request'],
                              revision=current, revisions=revisions, versions=versions,
                              href='/preparation/dossiers/' + dossier_id))
        return dict(kind='catalogue', visibility='private', catalogue_admission=False, tasks=tasks)


def _html_text(value):
    return escape(str(value), quote=True)


def _projection_body(value, selected):
    """Only explicit presentation fields; never serialize a private evaluation object"""
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


def _public_page(value, selected):
    body = '<p>Projection fictive locale S6. Aucun droit de publication réelle ni admission au catalogue.</p>'
    body += '<p>L’aperçu en mémoire reste sans approbation. Le service local ne rend cette projection qu’après '
    body += 'vérification de son reçu fictif et de ses octets exacts.</p>'
    body += _projection_body(value, selected)
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width, initial-scale=1"><title>Projection fictive · Benchmark Lab-X</title>'
            '<link rel="stylesheet" href="style.css"></head><body><a class="skip" href="#main">Aller au contenu</a>'
            '<main id="main">' + body + '</main></body></html>').encode('utf-8')


def _preview(store, value, piece_ids):
    if type(piece_ids) is not list or any(type(pid) is not str for pid in piece_ids) or len(set(piece_ids)) != len(piece_ids):
        raise ValueError('Liste explicite de pièces uniques requise')
    linked = {link['piece_id'] for row in value['rows'] for link in row['proof_links']}
    if not set(piece_ids) <= linked:
        raise p.Denied('Pièce non liée à la restitution')
    selected = {pid: 'piece-' + p.identifier(pid) + '.txt' for pid in sorted(piece_ids)}
    files = {name: store.read_piece(pid) for pid, name in selected.items()}
    files['index.html'] = _public_page(value, selected)
    files['style.css'] = Path(__file__).with_name('preparation.css').read_bytes()
    manifest = dict(schema_version=SCHEMA, presentation_version='1', conclusion_version='1',
                    campaign_id=value['campaign_id'], contract_sha256=value['contract_sha256'], task=value['task'],
                    evaluation_ids=[row['evaluation_id'] for row in value['rows']],
                    limits=value['conclusion']['limits'],
                    pieces={pid: dict(file=name, sha256=sha256(files[name]).hexdigest()) for pid, name in selected.items()},
                    files={name: sha256(raw).hexdigest() for name, raw in files.items()})
    raw = encode(manifest).encode('utf-8')
    return dict(manifest=raw, files=files, projection_sha256=sha256(raw).hexdigest())


def preview(store, session_id, dossier_id, campaign_id, *, piece_ids):
    connection = e.connection_for(store)
    with _transaction(connection):
        value = _comparison(store, connection, session_id, dossier_id, campaign_id, {})
        return _preview(store, value, piece_ids)


def preview_view(store, session_id, dossier_id, campaign_id, *, piece_ids):
    connection = e.connection_for(store)
    with _transaction(connection):
        value = _comparison(store, connection, session_id, dossier_id, campaign_id, {})
        bundle = _preview(store, value, piece_ids)
        links = {link['piece_id']: link for row in value['rows'] for link in row['proof_links']}
        return dict(kind='projection_preview', comparison=value, pieces=list(links.values()),
                    selected_links={pid: links[pid]['href'] for pid in piece_ids},
                    manifest=_decode(bundle['manifest']), projection_sha256=bundle['projection_sha256'])


def _decode(raw):
    def invalid(value):
        raise ValueError('Constante JSON invalide')
    return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=invalid)


def _approval(approval, identity):
    expected = dict(actor='approbateur-fictif-S6', authority_id='TEST_ONLY_PUBLICATION_S6',
                    projection_sha256=identity, catalogue=False)
    if type(approval) is not dict or approval != expected or approval.get('catalogue') is not False:
        raise ValueError('Approbation fictive exacte requise')


def _manifest(raw, identity):
    if type(raw) is not bytes or type(identity) is not str or not re.fullmatch('[0-9a-f]{64}', identity):
        raise ValueError('Identité de projection invalide')
    if sha256(raw).hexdigest() != identity:
        raise ValueError('Manifeste divergent')
    m = _decode(raw)
    keys = {'schema_version', 'presentation_version', 'conclusion_version', 'campaign_id',
            'contract_sha256', 'task', 'evaluation_ids', 'limits', 'pieces', 'files'}
    if type(m) is not dict or set(m) != keys or m['schema_version'] != SCHEMA:
        raise ValueError('Schéma de projection fictive inconnu')
    if m['presentation_version'] != '1' or m['conclusion_version'] != '1':
        raise ValueError('Version de restitution inconnue')
    p.identifier(m['campaign_id'])
    q._hash(m['contract_sha256'])
    if type(m['task']) is not dict or set(m['task']) != {'dossier_id', 'version', 'revision', 'package_sha256'}:
        raise ValueError('Identité de tâche requise')
    p.identifier(m['task']['dossier_id'])
    q._hash(m['task']['package_sha256'])
    if any(type(m['task'][k]) is not int or m['task'][k] < 1 for k in ('revision', 'version')):
        raise ValueError('Version de tâche invalide')
    for key in ('evaluation_ids', 'limits'):
        q._texts(m[key], key, unique=True)
    for eid in m['evaluation_ids']:
        p.identifier(eid)
    if type(m['files']) is not dict or not {'index.html', 'style.css'} <= m['files'].keys():
        raise ValueError('Fichiers de projection requis')
    for name, digest in m['files'].items():
        if not _FILES.fullmatch(name):
            raise ValueError('Nom de pièce publique invalide')
        q._hash(digest)
    if type(m['pieces']) is not dict:
        raise ValueError('Pièces sélectionnées requises')
    for pid, piece in m['pieces'].items():
        p.identifier(pid)
        if (type(piece) is not dict or set(piece) != {'file', 'sha256'}
                or piece['file'] != 'piece-' + pid + '.txt'
                or m['files'].get(piece['file']) != piece['sha256']):
            raise ValueError('Pièce sélectionnée divergente')
    if set(m['files']) != {'index.html', 'style.css'} | {v['file'] for v in m['pieces'].values()}:
        raise ValueError('Fichier public non sélectionné')
    return m


def _bundle(bundle, approval):
    if type(bundle) is not dict or set(bundle) != {'manifest', 'files', 'projection_sha256'}:
        raise ValueError('Paquet de projection invalide')
    m = _manifest(bundle['manifest'], bundle['projection_sha256'])
    _approval(approval, bundle['projection_sha256'])
    if type(bundle['files']) is not dict or set(bundle['files']) != set(m['files']):
        raise ValueError('Sélection de fichiers divergente')
    for name, raw in bundle['files'].items():
        if type(raw) is not bytes or sha256(raw).hexdigest() != m['files'][name]:
            raise ValueError('Octets de projection divergents')
    return m


def _directory(path):
    """Open each directory without following a symbolic link, including ancestors"""
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(fd, name):
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(file_fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Fichier ordinaire requis')
        return stream.read()


def _read_bundle(fd, identity):
    raw = _read(fd, 'publication.json')
    m = _manifest(raw, identity)
    approval = _decode(_read(fd, 'approval.json'))
    _approval(approval, identity)
    bundle = dict(manifest=raw, projection_sha256=identity,
                  files={name: _read(fd, name) for name in m['files']})
    _bundle(bundle, approval)
    return bundle


def public_bytes(destination, identity, name):
    """Verify an S6 identity using only the public directory, independent of active.json"""
    if type(identity) is not str or not re.fullmatch('[0-9a-f]{64}', identity) or not _FILES.fullmatch(name):
        raise ValueError('Chemin public invalide')
    root = _directory(destination)
    try:
        folder = os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        try:
            bundle = _read_bundle(folder, identity)
            if name not in bundle['files']:
                raise ValueError('Pièce publique non déclarée')
            return bundle['files'][name]
        finally:
            os.close(folder)
    finally:
        os.close(root)


def _write(fd, name, raw):
    file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(file_fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def materialize(bundle, approval, destination):
    """Validate all bytes before atomically selecting a complete fictional local bundle"""
    bundle, approval = deepcopy(bundle), deepcopy(approval)
    _bundle(bundle, approval)
    identity = bundle['projection_sha256']
    root = _directory(destination)
    stage = '.s6-' + secrets.token_hex(16)
    pointer = '.active-' + secrets.token_hex(16)
    folder = None
    staged = False
    try:
        os.mkdir(stage, mode=0o700, dir_fd=root)
        staged = True
        folder = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        for name, raw in dict(bundle['files'], **{'publication.json': bundle['manifest'],
                                                'approval.json': encode(approval).encode('utf-8')}).items():
            _write(folder, name, raw)
        _read_bundle(folder, identity)
        os.fsync(folder)
        try:
            existing = os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        except FileNotFoundError:
            os.rename(stage, identity, src_dir_fd=root, dst_dir_fd=root)
            staged = False
        else:
            try:
                if _read_bundle(existing, identity) != bundle:
                    raise ValueError('Projection existante divergente')
            finally:
                os.close(existing)
        os.fsync(root)
        _write(root, pointer, encode({'directory': identity}).encode('utf-8'))
        os.replace(pointer, 'active.json', src_dir_fd=root, dst_dir_fd=root)
        os.fsync(root)
    finally:
        if folder is not None:
            if staged:
                for name in os.listdir(folder):
                    os.unlink(name, dir_fd=folder)
            os.close(folder)
        if staged:
            os.rmdir(stage, dir_fd=root)
        try:
            os.unlink(pointer, dir_fd=root)
        except FileNotFoundError:
            pass
        os.close(root)
