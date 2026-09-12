"""Private evaluations of identified S4 observations.

The trusted local caller supplies findings, never a verdict or a transport.
Real local or human findings require an explicit private operator authority.
Assisted proposals require a separate private operator submission.
"""
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import secrets

from . import campaigns as c, qualification as q, storage
from .storage import (ConflictError, IntegrityError, SchemaError, _fields,
                      _strict_json as encode, _transaction)

FORMAT_IDENTITY = 'benchmark-lab-x/evaluations/v1'
RECORD_FORMAT_IDENTITY = 'benchmark-lab-x/evaluations/v2'
_ACTOR = 'responsable-fictif-S5'
_AUTHORITY = 'TEST_ONLY_EVALUATION_S5'
_TABLES = {
    's5_control': """CREATE TABLE s5_control (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        format_identity TEXT NOT NULL CHECK(format_identity = 'benchmark-lab-x/evaluations/v1')
    )""",
    's5_evaluations': """CREATE TABLE s5_evaluations (
        evaluation_id TEXT PRIMARY KEY NOT NULL,
        campaign_id TEXT NOT NULL REFERENCES s4_campaigns(campaign_id),
        attempt_id TEXT NOT NULL REFERENCES s4_attempts(operation_id),
        contract_sha256 TEXT NOT NULL REFERENCES s3_contracts(contract_sha256),
        qualification_id TEXT NOT NULL,
        previous_evaluation_id TEXT UNIQUE REFERENCES s5_evaluations(evaluation_id),
        record_json TEXT NOT NULL,
        record_sha256 TEXT NOT NULL CHECK(length(record_sha256) = 64),
        context_json TEXT NOT NULL,
        context_sha256 TEXT NOT NULL CHECK(length(context_sha256) = 64),
        FOREIGN KEY(contract_sha256, qualification_id)
            REFERENCES s3_qualifications(contract_sha256, qualification_id)
    )""",
}
_TRIGGERS = {
    f'{table}_{action.lower()}': (
        f'CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} '
        "BEGIN SELECT RAISE(ABORT, 'immutable evaluation evidence'); END")
    for table in _TABLES for action in ('UPDATE', 'DELETE')
}
for _table, _key in (('s5_control', 'singleton'), ('s5_evaluations', 'evaluation_id')):
    _TRIGGERS[_table + '_insert'] = (
        f'CREATE TRIGGER {_table}_insert BEFORE INSERT ON {_table} '
        f'WHEN EXISTS (SELECT 1 FROM {_table} WHERE {_key}=NEW.{_key}) '
        "BEGIN SELECT RAISE(ABORT, 'immutable evaluation identity'); END")
_TRIGGERS['s5_evaluations_chain'] = """CREATE TRIGGER s5_evaluations_chain
    BEFORE INSERT ON s5_evaluations
    WHEN NEW.previous_evaluation_id IS NOT (
        SELECT evaluation_id FROM s5_evaluations WHERE attempt_id=NEW.attempt_id ORDER BY rowid DESC LIMIT 1)
    BEGIN SELECT RAISE(ABORT, 'latest evaluation predecessor required'); END"""
_REPORT_FIELDS = ('findings', 'measures', 'judgment', 'limits')
_JUDGMENT_FIELDS = ('mode', 'instructions', 'resources_seen', 'assistance_operation_id',
                    'model_links', 'disagreements', 'professional_review')


def schema_objects():
    return ([("table", name, name, sql) for name, sql in _TABLES.items()]
            + [("index", f'sqlite_autoindex_s5_evaluations_{i}', 's5_evaluations', None)
               for i in (1, 2)]
            + [("trigger", name, name.rsplit('_', 1)[0], sql) for name, sql in _TRIGGERS.items()])


def initialize(data):
    """Explicitly extend an intact S4 database; never initialize on inspection"""
    from .runtime import worker_lock
    with closing(storage.Store(data)) as store, worker_lock(store):
        c._intact(store)
        connection = store._connection_checked()
        with _transaction(connection, write=True):
            layout = storage._check_schema(connection)
            if layout == 's5':
                return
            if layout != 's4':
                raise SchemaError('Extension explicite sur une base S4 requise')
            for sql in (*_TABLES.values(), *_TRIGGERS.values()):
                connection.execute(sql)
            connection.execute('INSERT INTO s5_control VALUES (1, ?)', (FORMAT_IDENTITY,))
            storage._check_schema(connection)


def connection_for(store):
    connection = store._connection_checked()
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s5_control'").fetchone():
        raise SchemaError('Initialisation explicite S5 requise')
    return connection


def _authority(responsible, authority):
    _fields(authority, ('actor', 'authority_id'), 'evaluation authority')
    if responsible == _ACTOR and authority == {'actor': _ACTOR, 'authority_id': _AUTHORITY}:
        return
    c._present(responsible, 'responsible')
    c._present(authority['authority_id'], 'authority_id')
    if authority['actor'] != 'Ayo' or authority['authority_id'].startswith('TEST_ONLY'):
        raise ValueError('Autorité opérateur explicite requise')


def _context(store, connection, campaign_id, attempt_id):
    campaign = c._inspect(store, connection, campaign_id)
    attempt = next((a for a in campaign['attempts'] if a['operation_id'] == attempt_id), None)
    if attempt is None:
        raise KeyError(attempt_id)
    qualification = q._inspect(store, connection, campaign['manifest']['contract_sha256'])
    return dict(campaign=campaign, qualification=qualification, attempt=attempt)


def _resources(store, ctx):
    contract = ctx['qualification']['contract']
    ids = [p['id'] for p in contract['package']['pieces'] + contract['reference_pieces']]
    if ctx['attempt']['output_piece_id'] is not None:
        ids.append(ctx['attempt']['output_piece_id'])
    return {pid: store.read_piece(pid) for pid in ids}


def _evidence(value, resources, *, required=False):
    if type(value) is not list or (required and not value):
        raise ValueError('Pièces de preuve requises')
    for proof in value:
        _fields(proof, ('piece_id', 'sha256', 'passage'), 'evidence')
        c.identifier(proof['piece_id'])
        q._hash(proof['sha256'])
        if type(proof['passage']) is not str:
            raise ValueError('Passage textuel requis')
        raw = resources.get(proof['piece_id'])
        if (raw is None or sha256(raw).hexdigest() != proof['sha256']
                or (not proof['passage'] and raw)
                or proof['passage'].encode('utf-8') not in raw):
            raise IntegrityError('Preuve étrangère, empreinte ou passage divergent')


def _operation_snapshot(old, current):
    """Later receipt acquisition cannot rewrite the earlier evaluation snapshot"""
    stable = set(current) - {'state', 'ambiguity_reason', 'receipt', 'observed_cost'}
    if set(old) != set(current) or any(old[k] != current[k] for k in stable):
        raise IntegrityError('Intention source divergente')
    states = {'INTENT_RECORDED': 0, 'EMISSION_POSSIBLE': 1, 'AMBIGUOUS': 2, 'RECEIVED': 3}
    if old['state'] not in states or states[old['state']] > states[current['state']]:
        raise IntegrityError('Chronologie source divergente')
    for key in ('ambiguity_reason', 'receipt', 'observed_cost'):
        if old[key] is not None and old[key] != current[key]:
            raise IntegrityError('Observation source réécrite')
    if (old['state'] == 'RECEIVED') != (old['receipt'] is not None and old['observed_cost'] is not None):
        raise IntegrityError('Reçu source incohérent')


def _validate_context(old, current):
    _fields(old, ('campaign', 'qualification', 'attempt'), 'context')
    if old['qualification'] != current['qualification']:
        raise IntegrityError('Qualification source divergente')
    for key in ('manifest', 'manifest_sha256', 'task'):
        if old['campaign'][key] != current['campaign'][key]:
            raise IntegrityError('Campagne source divergente')
    a, b = old['attempt'], current['attempt']
    evolving = {'operation', 'state', 'emitted_at', 'received_at', 'output_piece_id',
                'emission_admission_id', 'attribution_incident'}
    if set(a) != set(b) or any(a[k] != b[k] for k in set(b) - evolving):
        raise IntegrityError('Tentative source divergente')
    _operation_snapshot(a['operation'], b['operation'])
    if a['state'] != a['operation']['state']:
        raise IntegrityError('État source incohérent')
    for key in ('emitted_at', 'received_at', 'output_piece_id', 'emission_admission_id'):
        if a[key] is not None and a[key] != b[key]:
            raise IntegrityError('Preuve source réécrite')
    if a['state'] == 'RECEIVED':
        if a != b:
            raise IntegrityError('Reçu candidat réécrit')
    elif a['received_at'] is not None or a['output_piece_id'] is not None or a['attribution_incident']:
        raise IntegrityError('Sortie inventée avant réception')


def _judgment(store, connection, value, ctx, resources, source_operation=None, responsible=_ACTOR):
    _fields(value, _JUDGMENT_FIELDS, 'judgment')
    if value['mode'] not in ('local', 'human', 'assisted'):
        raise ValueError('Mode de jugement inconnu')
    c._present(value['instructions'], 'instructions')
    q._texts(value['resources_seen'], 'resources_seen', unique=True)
    if not set(value['resources_seen']) <= resources.keys():
        raise ValueError('Ressource vue étrangère')
    links = value['model_links']
    if links != 'INCONNU' and (type(links) is not dict or not links):
        raise ValueError('Liens de modèle/fournisseur explicites ou INCONNU requis')
    q._professional(value['professional_review'])
    if value['professional_review'] != 'ABSENTE':
        for key in ('author', 'phase', 'scope'):
            c._present(value['professional_review'][key], key)
        _evidence(value['professional_review']['proof'], resources, required=True)
    disagreements = value['disagreements']
    if type(disagreements) is not list:
        raise ValueError('Désaccords requis')
    for disagreement in disagreements:
        _fields(disagreement, ('finding', 'arbitration'), 'disagreement')
        c._present(disagreement['finding'], 'disagreement finding')
        arbitration = disagreement['arbitration']
        if arbitration is not None:
            _fields(arbitration, ('responsible', 'decision', 'proof'), 'arbitration')
            if arbitration['responsible'] != responsible:
                raise ValueError('Arbitrage attribué au responsable requis')
            c._present(arbitration['decision'], 'decision')
            _evidence(arbitration['proof'], resources, required=True)
    review_proofs = ([] if value['professional_review'] == 'ABSENTE' else list(value['professional_review']['proof']))
    review_proofs += [proof for d in disagreements if d['arbitration'] is not None for proof in d['arbitration']['proof']]
    if not {p['piece_id'] for p in review_proofs} <= set(value['resources_seen']):
        raise ValueError('Pièce de revue ou arbitrage non vue')
    result = deepcopy(value)
    result.update(requested_configuration=None, observed_configuration=None,
                  cost=dict(status='UNKNOWN', amount=None, currency='INCONNU',
                            source='Aucune méthode ni mesure de coût local ou humain'),
                  operation=None)
    oid = value['assistance_operation_id']
    if value['mode'] != 'assisted':
        if oid is not None or source_operation is not None:
            raise ValueError('Opération assistée incompatible avec le mode')
        return result
    c.identifier(oid)
    current = next(iter(store._operations(connection, operation_ids={oid})), None)
    if current is None:
        raise KeyError(oid)
    op = current if source_operation is None else source_operation
    _operation_snapshot(op, current)
    if op['engine_version'] == 'benchmark-lab-x/judgment/v1':
        from .judgment import evaluation_judgment
        return evaluation_judgment(store, connection, value, ctx, op, result)
    contract = ctx['qualification']['contract']
    if (op['phase'] != 'judgment' or op['authority'] != 'TEST_ONLY_JUDGMENT_S5'
            or (op['dossier_id'], op['revision']) != (contract['dossier_id'], contract['revision'])):
        raise ValueError('Intention de jugement fictif de ce dossier requise')
    if not op['resources']:
        raise ValueError('Consignes de jugement absentes')
    binding = json.loads(op['resources'][0], object_pairs_hook=storage._unique_object)
    _fields(binding, ('instructions', 'context_sha256', 'piece_ids'), 'judgment inputs')
    if (binding['instructions'] != value['instructions'] or binding['context_sha256'] != q.digest(ctx)
            or binding['piece_ids'] != list(resources) or op['resources'][1:] != list(resources)):
        raise ValueError('Jugement sans lien exact aux consignes et observations')
    receipt = op['receipt']
    if receipt is not None and (receipt['resources_seen'] != value['resources_seen']
                               or not set(receipt['resources_seen']) <= resources.keys()):
        raise ValueError('Pièces vues divergentes du reçu de jugement')
    budget = store._budget(connection, op['budget_id'], store._operations(connection))
    if budget['currency'] != 'TEST':
        raise ValueError('Budget de jugement fictif requis')
    observed = (receipt or {}).get('observed_configuration') or {}
    result.update(operation=op, requested_configuration=op['requested_configuration'],
                  observed_configuration={key: observed[key] if observed.get(key) is not None else 'INCONNU'
                                          for key in observed.keys() | {'model', 'provider', 'effort'}},
                  cost=op['observed_cost'] or dict(status='UNKNOWN', amount=None, currency=budget['currency'],
                                                 source='Reçu de jugement absent ; réservation conservée'))
    return result


def _report(store, connection, report, ctx, resources, source_operation=None, responsible=_ACTOR):
    _fields(report, _REPORT_FIELDS, 'evaluation report')
    encode(report)
    q._texts(report['limits'], 'limits')
    spec = ctx['qualification']['contract']['specification']
    criteria = {x['id']: x for x in spec['obligations'] + spec['eliminatory_errors']}
    if type(report['findings']) is not list:
        raise ValueError('Constats requis')
    findings = deepcopy(report['findings'])
    for finding in findings:
        _fields(finding, ('criterion_id', 'control_id', 'status', 'attribution', 'finding', 'evidence'), 'finding')
        if (finding['criterion_id'] not in criteria
                or finding['control_id'] not in criteria[finding['criterion_id']]['control_ids']):
            raise ValueError('Critère ou contrôle non déclaré')
        if finding['status'] not in ('PASS', 'FAIL', 'INDETERMINE'):
            raise ValueError('État de constat inconnu')
        for key in ('attribution', 'finding'):
            c._present(finding[key], key)
        _evidence(finding['evidence'], resources, required=finding['status'] != 'INDETERMINE')
    pairs = {(f['criterion_id'], f['control_id']) for f in findings}
    for cid, criterion in criteria.items():
        for control in criterion['control_ids']:
            if (cid, control) not in pairs:
                findings.append(dict(criterion_id=cid, control_id=control, status='INDETERMINE',
                                     attribution='evidence', finding='Contrôle prévu sans constat conservé', evidence=[]))
    if type(report['measures']) is not list:
        raise ValueError('Mesures prévues requises')
    measures, seen = [], set()
    planned = {m['id']: m for m in spec['secondary_criteria']}
    for measure in report['measures']:
        _fields(measure, ('criterion_id', 'value', 'unit', 'evidence'), 'measure')
        cid = measure['criterion_id']
        if cid not in planned or cid in seen or measure['unit'] != planned[cid]['unit']:
            raise ValueError('Mesure ou unité étrangère au contrat')
        if type(measure['value']) not in (str, bool, int, float, type(None)):
            raise ValueError('Valeur source de mesure invalide')
        seen.add(cid)
        _evidence(measure['evidence'], resources, required=measure['value'] not in (None, 'INCONNU', 'UNKNOWN'))
        measures.append(dict(measure, definition=planned[cid], status='UNKNOWN' if measure['value'] in (None, 'INCONNU', 'UNKNOWN') else 'KNOWN'))
    for cid, definition in planned.items():
        if cid not in seen:
            measures.append(dict(criterion_id=cid, value=None, unit=definition['unit'], evidence=[],
                                 definition=definition, status='UNKNOWN'))
    judgment = _judgment(store, connection, report['judgment'], ctx, resources, source_operation, responsible)
    used = {e['piece_id'] for f in findings + measures for e in f['evidence']}
    if not used <= set(judgment['resources_seen']):
        raise ValueError('Une preuve ne figure pas parmi les pièces vues')
    return findings, measures, judgment


def _configuration_links(store, connection, ctx, judgment):
    """Expose shared identities with their sources, without inferring independence"""
    contract = ctx['qualification']['contract']
    preparation_ids = {row[0] for row in connection.execute(
        'SELECT operation_id FROM s2_actions WHERE dossier_id=? AND input_revision<?',
        (contract['dossier_id'], contract['revision']))}
    candidate = ctx['attempt']['operation']
    others = store._operations(connection, operation_ids=preparation_ids)
    if judgment['operation'] is not None:
        others.append(judgment['operation'])
    result = []
    for operation in others:
        for kind in ('requested', 'observed'):
            def configuration(op):
                return (op['requested_configuration'] if kind == 'requested' else
                        (op['receipt'] or {}).get('observed_configuration') or {})
            left, right = configuration(operation), configuration(candidate)
            for field in ('model', 'provider'):
                a, b = left.get(field), right.get(field)
                known = all(type(v) is str and v.strip() and v not in ('INCONNU', 'UNKNOWN') for v in (a, b))
                result.append(dict(phase=operation['phase'], operation_id=operation['operation_id'],
                    observation=kind, field=field, value=a if a is not None else 'INCONNU',
                    candidate_value=b if b is not None else 'INCONNU', shared=a == b if known else 'INCONNU',
                    source=operation['operation_id'] if kind == 'requested' else (operation['receipt'] or {}).get('receipt_id', 'INCONNU'),
                    candidate_source=candidate['operation_id'] if kind == 'requested' else (candidate['receipt'] or {}).get('receipt_id', 'INCONNU')))
    return result


def _verdict(ctx, findings, judgment):
    attempt = ctx['attempt']
    receipt = attempt['operation']['receipt']
    if (attempt['state'] != 'RECEIVED' or attempt['output_piece_id'] is None
            or attempt['attribution_incident'] or receipt['result']['emission'] != 'ESTABLISHED'
            or receipt['result']['incident'] == 'HARNESS_ERROR'):
        return 'INDETERMINE', 'Sortie, émission ou attribution insuffisante ; incident conservé séparément'
    if judgment['mode'] == 'assisted' and judgment['operation']['receipt'] is None:
        return 'INDETERMINE', 'Reçu de jugement absent ; effets et coût inconnus'
    output = attempt['output_piece_id']
    grouped = {}
    for finding in findings:
        grouped.setdefault((finding['criterion_id'], finding['control_id']), []).append(finding)
    defects = []
    for group in grouped.values():
        if any(f['status'] == 'PASS' for f in group):
            continue
        defects.extend(f for f in group if f['status'] == 'FAIL' and f['attribution'] == 'candidate'
                       and any(e['piece_id'] == output for e in f['evidence']))
    if defects:
        return 'NE SATISFAIT PAS', '; '.join(f['criterion_id'] + ' : ' + f['finding'] for f in defects)
    if receipt['result']['incident'] is not None:
        return 'INDETERMINE', 'Incident conservé ; aucune erreur candidate établie indépendamment'
    if any(d['arbitration'] is None for d in judgment['disagreements']):
        return 'INDETERMINE', 'Désaccord de jugement non arbitré'
    if all(f['status'] == 'PASS' and f['attribution'] in ('candidate', 'evidence')
           and any(e['piece_id'] == output for e in f['evidence']) for f in findings):
        return 'SATISFAIT', 'Résultat et obligations prouvés ; contrôles éliminatoires satisfaits : ' + ', '.join(grouped_key[0] for grouped_key in grouped)
    return 'INDETERMINE', 'Preuve insuffisante ou contradictoire : ' + ', '.join(
        dict.fromkeys(f['criterion_id'] for f in findings if f['status'] != 'PASS'
                      or f['attribution'] not in ('candidate', 'evidence')
                      or not any(proof['piece_id'] == output for proof in f['evidence'])))


def _record(store, connection, ctx, report, *, evaluation_id, created_at, engine_source,
            previous_evaluation_id, source_operation=None, responsible=_ACTOR, authority=None,
            record_format=FORMAT_IDENTITY):
    if record_format not in (FORMAT_IDENTITY, RECORD_FORMAT_IDENTITY):
        raise IntegrityError('Format d’évaluation inconnu')
    authority = authority or {'actor': _ACTOR, 'authority_id': _AUTHORITY}
    _authority(responsible, authority)
    resources = _resources(store, ctx)
    findings, measures, judgment = _report(store, connection, report, ctx, resources, source_operation, responsible)
    if judgment['mode'] == 'assisted':
        op = judgment['operation']
        if op['engine_version'] == 'benchmark-lab-x/judgment/v1':
            binding = json.loads(op['resources'][0])['request']
            from .judgment import local_criteria
            local = local_criteria(ctx['qualification']['contract']['specification'])
            if any(f['criterion_id'] in local and f['status'] != 'INDETERMINE' for f in findings):
                raise ValueError('Preuve opérationnelle absente des pièces de la relecture assistée')
            if binding['previous_evaluation_id'] != previous_evaluation_id:
                raise ConflictError('Prédécesseur distinct de la relecture assistée')
        elif authority['actor'] == 'Ayo':
            raise ValueError('Jugement historique réservé à son autorité fictive')
    verdict, reason = _verdict(ctx, findings, judgment)
    campaign, qualification, attempt = (ctx[k] for k in ('campaign', 'qualification', 'attempt'))
    contract = qualification['contract']
    manifest = campaign['manifest']
    cell = next(x for x in manifest['plan'] if x['cell_id'] == attempt['cell_id'])
    op = attempt['operation']
    observed = (op['receipt'] or {}).get('observed_configuration') or {}
    sources = observed.get('sources', {})
    output = resources.get(attempt['output_piece_id'])
    c.identifier(evaluation_id)
    c._date(created_at)
    q._hash(engine_source)
    record = dict(evaluation_id=evaluation_id, execution_id=evaluation_id, created_at=created_at,
                engine_version=record_format, engine_source_sha256=engine_source,
                context_sha256=q.digest(ctx), campaign_id=manifest['campaign_id'],
                manifest_sha256=campaign['manifest_sha256'], attempt_id=attempt['operation_id'],
                case_id=cell['case_id'], configuration_id=cell['configuration_id'],
                contract_sha256=qualification['contract_sha256'], qualification_id=qualification['approval']['qualification_id'],
                method=contract['specification']['method'], reference_pieces=contract['reference_pieces'],
                input_sha256=attempt['input_sha256'], output_piece_id=attempt['output_piece_id'],
                output_sha256=None if output is None else sha256(output).hexdigest(),
                requested_configuration=op['requested_configuration'],
                observed_configuration={k: observed[k] if observed.get(k) is not None else 'INCONNU' for k in c._OBSERVED},
                observation_sources={k: sources.get(k, 'INCONNU') if type(sources) is dict else 'INCONNU' for k in c._OBSERVED},
                incident=(op['receipt'] or {}).get('result', {}).get('incident'),
                attribution_incident=attempt['attribution_incident'], candidate_cost=op['observed_cost'],
                cost_basis=manifest['cost_basis'], aggregation=contract['specification']['aggregation'],
                verdict=verdict, reason=reason, findings=findings, measures=measures,
                responsible=responsible, authority_id=authority['authority_id'], judgment=judgment,
                configuration_links=_configuration_links(store, connection, ctx, judgment),
                limits=list(dict.fromkeys(contract['specification']['limits'] + report['limits'])),
                previous_evaluation_id=previous_evaluation_id)
    if authority['actor'] == 'Ayo':
        record['authority_actor'] = 'Ayo'
    if record_format == RECORD_FORMAT_IDENTITY:
        record['decision'] = decision(record, attempt=attempt)
        record['verdict'] = record['decision']['verdict']
        record['state'] = record['decision']['state']
    return record


def _records(store, connection, attempt_id=None):
    query = 'SELECT * FROM s5_evaluations'
    params = ()
    if attempt_id is not None:
        query += ' WHERE attempt_id=?'
        params = (attempt_id,)
    rows = connection.execute(query + ' ORDER BY rowid', params).fetchall()
    result, latest = [], {}
    for eid, cid, aid, fingerprint, qid, previous, raw, digest, context_raw, context_digest in rows:
        record = q._decode(raw, digest)
        ctx = q._decode(context_raw, context_digest)
        if previous != latest.get(aid):
            raise IntegrityError('Chaîne de correction incohérente')
        if tuple(record[k] for k in ('evaluation_id', 'campaign_id', 'attempt_id', 'contract_sha256',
                                     'qualification_id', 'previous_evaluation_id')) != (eid, cid, aid, fingerprint, qid, previous):
            raise IntegrityError('Identité d’évaluation divergente')
        _validate_context(ctx, _context(store, connection, cid, aid))
        # Reapply only structural checks and verdict logic to the saved findings
        # Never re-run the controller or substitute later observations
        report = dict(findings=record['findings'],
                      measures=[{k: m[k] for k in ('criterion_id', 'value', 'unit', 'evidence')} for m in record['measures']],
                      judgment={k: record['judgment'][k] for k in _JUDGMENT_FIELDS}, limits=record['limits'])
        expected = _record(store, connection, ctx, report, evaluation_id=eid, created_at=record['created_at'],
                           engine_source=record['engine_source_sha256'], previous_evaluation_id=previous,
                           source_operation=record['judgment']['operation'], responsible=record['responsible'],
                           authority={'actor': record.get('authority_actor', _ACTOR), 'authority_id': record['authority_id']},
                           record_format=record['engine_version'])
        if expected != record:
            raise IntegrityError('Verdict ou preuve d’évaluation divergent')
        latest[aid] = eid
        result.append(record)
    return result


def verify_evaluations(store, connection):
    _records(store, connection)


def inspect(store, evaluation_id):
    c.identifier(evaluation_id)
    connection = connection_for(store)
    with _transaction(connection):
        row = connection.execute('SELECT attempt_id FROM s5_evaluations WHERE evaluation_id=?', (evaluation_id,)).fetchone()
        if row is None:
            raise KeyError(evaluation_id)
        return next(r for r in _records(store, connection, row[0]) if r['evaluation_id'] == evaluation_id)


def decision(record, *, attempt=None):
    """Current business view; never rewrite the historical evaluation record"""
    if 'decision' in record:
        return deepcopy(record['decision'])
    if record['verdict'] != 'INDETERMINE':
        return dict(verdict=record['verdict'], state='DECIDED', reason=record['reason'], next_action=None)
    if attempt is not None and (attempt['state'] != 'RECEIVED' or
            attempt['operation']['receipt']['result']['emission'] != 'ESTABLISHED'):
        state, action = 'RECONCILIATION_REQUIRED', 'Rapprocher les effets de la tentative avant toute reprise'
    elif record['output_piece_id'] is None or record['attribution_incident'] or record['incident']:
        state, action = 'EXECUTION_REQUIRED', 'Diagnostiquer le reçu candidat avant toute reprise autorisée'
    elif record['judgment']['mode'] == 'assisted' and record['judgment']['operation']['receipt'] is None:
        state, action = 'RECONCILIATION_REQUIRED', 'Rapprocher les effets et le coût du jugement avant tout nouvel appel'
    elif any(d['arbitration'] is None for d in record['judgment']['disagreements']):
        state, action = 'REVIEW_REQUIRED', 'Arbitrer les désaccords sur la même sortie et soumettre une nouvelle décision'
    else:
        state, action = 'REVIEW_REQUIRED', 'Compléter ou corriger les constats sur la même sortie ; qualifier une nouvelle version si le contrat change'
    return dict(verdict=None, state=state, reason=record['reason'], next_action=action,
                criteria=list(dict.fromkeys(f['criterion_id'] for f in record['findings'] if f['status'] != 'PASS')))


def attempt_status(store, campaign_id, attempt_id):
    """Private read-only next action, including attempts without an official verdict"""
    from . import recovery, judgment
    recovery_status = recovery.diagnose(store, attempt_id)
    connection = connection_for(store)
    with _transaction(connection):
        ctx = _context(store, connection, campaign_id, attempt_id)
        records = _records(store, connection, attempt_id)
        latest = records[-1] if records else None
        result = (decision(latest) if latest else dict(verdict=None,
            state='REVIEW_REQUIRED' if recovery_status['kind'] == 'COMPLETE' else 'EXECUTION_REQUIRED',
            reason=recovery_status['reason'], next_action=recovery_status['reason']))
        result.update(attempt_id=attempt_id, campaign_id=campaign_id,
                      evaluation_id=latest['evaluation_id'] if latest else None, recovery=recovery_status)
        for op in reversed(store._operations(connection)):
            if op['engine_version'] != judgment.FORMAT:
                continue
            binding = json.loads(op['resources'][0])['request']
            if binding['attempt_id'] == attempt_id and binding['campaign_id'] == campaign_id:
                result['judgment'] = dict(operation_id=op['operation_id'], **judgment.diagnostic(store, connection, op, ctx))
                result['judgment']['review_pending'] = binding['previous_evaluation_id'] == (latest['evaluation_id'] if latest else None)
                break
        return result


def pending_judgments(store, connection, campaign_id, latest):
    """Expose only the next local work, never a judge payload or its authority"""
    from . import judgment
    pending = {}
    ids = {row[0] for row in connection.execute("SELECT operation_id FROM operations WHERE phase='judgment'")}
    for op in store._operations(connection, operation_ids=ids):
        if op['engine_version'] != judgment.FORMAT:
            continue
        binding = json.loads(op['resources'][0])['request']
        attempt_id = binding['attempt_id']
        previous = latest.get(attempt_id)
        if (binding['campaign_id'] != campaign_id or binding['previous_evaluation_id'] !=
                (previous['evaluation_id'] if previous else None)):
            continue
        _, ctx = judgment._bound(store, connection, op)
        diagnostic = judgment.diagnostic(store, connection, op, ctx)
        pending[attempt_id] = dict(attempt_id=attempt_id, operation_id=op['operation_id'],
                                  verdict=None, state=diagnostic['state'], next_action=diagnostic['reason'])
    return list(pending.values())


def evaluate(store, campaign_id, attempt_id, *, responsible, authority, check, previous_evaluation_id=None):
    """One trusted callback; immutable result and explicit correction chain"""
    _authority(responsible, authority)
    if not callable(check):
        raise ValueError('Contrôleur local injecté requis')
    from .runtime import worker_lock
    with worker_lock(store, shared=True):
        c._intact(store)
        connection = connection_for(store)
        with _transaction(connection, write=True):
            ctx = _context(store, connection, campaign_id, attempt_id)
            previous = _records(store, connection, attempt_id)
            if previous_evaluation_id != (previous[-1]['evaluation_id'] if previous else None):
                raise ConflictError('Dernière évaluation de cette tentative requise pour corriger')
            resources = _resources(store, ctx)
            report = deepcopy(check(deepcopy(ctx), deepcopy(resources)))
            record = _record(store, connection, ctx, report, evaluation_id=secrets.token_hex(16),
                             created_at=c._now(), engine_source=sha256(Path(__file__).read_bytes()).hexdigest(),
                             previous_evaluation_id=previous_evaluation_id, responsible=responsible, authority=authority,
                             record_format=RECORD_FORMAT_IDENTITY if authority['actor'] == 'Ayo' else FORMAT_IDENTITY)
            if _context(store, connection, campaign_id, attempt_id) != ctx or _resources(store, ctx) != resources:
                raise IntegrityError('Source changée pendant le jugement')
            connection.execute('INSERT INTO s5_evaluations VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (record['evaluation_id'], campaign_id, attempt_id, record['contract_sha256'],
                                record['qualification_id'], previous_evaluation_id, encode(record), q.digest(record),
                                encode(ctx), q.digest(ctx)))
            return record


def projection(store, connection, dossier_id, campaign_id):
    """Private owner projection, without broad access to the judge piece role"""
    records = []
    for record in _records(store, connection):
        if record['campaign_id'] != campaign_id:
            continue
        contract = q._contract(store, connection, record['contract_sha256'])
        if contract['dossier_id'] != dossier_id:
            raise IntegrityError('Évaluation étrangère au dossier')
        # The context, operator admissions and other S1 operations stay private
        visible = {k: deepcopy(v) for k, v in record.items() if k != 'judgment'}
        visible['decision'] = decision(record)
        visible['judgment'] = {k: deepcopy(v) for k, v in record['judgment'].items() if k != 'operation'}
        operation = record['judgment']['operation']
        if operation is not None:
            visible['judgment']['operation_provenance'] = {k: deepcopy(operation[k]) for k in (
                'operation_id', 'phase', 'authority', 'engine_version', 'created_at', 'state',
                'ambiguity_reason', 'budget_id', 'reserved_amount', 'receipt')}
        visible['qualification'] = q._inspect(store, connection, record['contract_sha256'])
        pieces = contract['package']['pieces'] + contract['reference_pieces']
        ids = [p['id'] for p in pieces]
        if record['output_piece_id'] is not None:
            ids.append(record['output_piece_id'])
        visible['proof_links'] = [dict(piece_id=pid, name=store.get_piece(pid)['name'],
            href=f'/preparation/dossiers/{dossier_id}/evaluations/{record["evaluation_id"]}/pieces/{pid}') for pid in ids]
        records.append(visible)
    return records


def piece_bytes(store, session_id, dossier_id, evaluation_id, piece_id):
    from .preparation import Denied, owner
    connection = connection_for(store)
    with _transaction(connection):
        owner(connection, session_id, dossier_id)
        row = connection.execute('SELECT e.attempt_id FROM s5_evaluations e JOIN s3_contracts c USING(contract_sha256) '
                                 'WHERE e.evaluation_id=? AND c.dossier_id=?', (evaluation_id, dossier_id)).fetchone()
        if row is None:
            raise Denied('Évaluation inaccessible')
        record = next(r for r in _records(store, connection, row[0]) if r['evaluation_id'] == evaluation_id)
        contract = q._contract(store, connection, record['contract_sha256'])
        ids = {p['id'] for p in contract['package']['pieces'] + contract['reference_pieces']}
        if record['output_piece_id'] is not None:
            ids.add(record['output_piece_id'])
        if piece_id not in ids:
            raise Denied('Pièce non liée à cette évaluation')
        return store.read_piece(piece_id)

def prepare_report(store, campaign_id, attempt_id):
    """Export private inputs for a reviewer; no verdict or approval is inferred"""
    connection = connection_for(store)
    with _transaction(connection):
        ctx = _context(store, connection, campaign_id, attempt_id)
        resources = _resources(store, ctx)
        previous = _records(store, connection, attempt_id)
        spec = ctx['qualification']['contract']['specification']
        return dict(campaign_id=campaign_id, attempt_id=attempt_id, context_sha256=q.digest(ctx),
                    previous_evaluation_id=previous[-1]['evaluation_id'] if previous else None,
                    contract=ctx['qualification']['contract'], attempt=ctx['attempt'],
                    pieces=[dict(piece_id=pid, sha256=sha256(raw).hexdigest(), content=raw.decode('utf-8'))
                            for pid, raw in resources.items()],
                    report=dict(findings=[dict(criterion_id=criterion['id'], control_id=control,
                        status='INDETERMINE', attribution='evidence', finding='Contrôle à examiner', evidence=[])
                        for criterion in spec['obligations'] + spec['eliminatory_errors'] for control in criterion['control_ids']],
                        measures=[], judgment=dict(mode='human', instructions='Revue à renseigner selon la méthode du contrat',
                            resources_seen=[], assistance_operation_id=None, model_links='INCONNU',
                            disagreements=[], professional_review='ABSENTE'), limits=[]))



def _review_piece(piece_id, name, raw):
    return dict(piece_id=piece_id, name=name, sha256=sha256(raw).hexdigest(), content=raw.decode('utf-8'))


def _review_content(store, ctx):
    from . import outgoing
    resources = _resources(store, ctx)
    contract = ctx['qualification']['contract']
    spec = contract['specification']
    package = contract['package']
    task_pieces = [_review_piece(p['id'], p['name'], resources[p['id']]) for p in package['pieces']]
    references = [_review_piece(p['id'], p['name'], resources[p['id']]) for p in contract['reference_pieces']]
    output = None
    output_id = ctx['attempt']['output_piece_id']
    if output_id is not None:
        meta = store.get_piece(output_id)
        output = _review_piece(output_id, meta['name'], resources[output_id])
    method = spec['method']
    content = outgoing.closed_review(dict(
        task=dict(instruction=package['instruction'], deliverables=list(package['deliverables']),
                  criteria=list(package['criteria']), acceptable_ambiguities=list(package['acceptable_ambiguities']),
                  pieces=task_pieces),
        result_expected=spec['result_expected'],
        obligations=[dict(id=x['id'], description=x['description'], tolerance=x['tolerance'],
                          control_ids=list(x['control_ids'])) for x in spec['obligations']],
        eliminatory_errors=[dict(id=x['id'], description=x['description'],
                                 control_ids=list(x['control_ids'])) for x in spec['eliminatory_errors']],
        method=dict(id=method['id'], version=method['version'], control_ids=list(method['control_ids']),
                    expected_evidence=method['expected_evidence'], responsible_role=method['responsible_role']),
        secondary_criteria=[dict(id=x['id'], measure=x['measure'], proof=x['proof'], unit=x['unit'],
                                 favorable=x['favorable'], aggregation=x['aggregation'])
                            for x in spec['secondary_criteria']],
        limits=list(spec['limits']), output=output, references=references))
    return content


def prepare_review(store, campaign_id, attempt_id):
    """Closed judgment view; campaign and attempt ids stay in a local binding"""
    from . import outgoing
    connection = connection_for(store)
    with _transaction(connection):
        ctx = _context(store, connection, campaign_id, attempt_id)
        content = _review_content(store, ctx)
        previous = _records(store, connection, attempt_id)
        return dict(outgoing_format=outgoing.FORMAT, content=content, content_sha256=q.digest(content),
                    binding=dict(campaign_id=campaign_id, attempt_id=attempt_id,
                                 previous_evaluation_id=previous[-1]['evaluation_id'] if previous else None))


def submit_report(store, request):
    """The private operator submits findings; the engine derives the verdict"""
    _fields(request, ('campaign_id', 'attempt_id', 'responsible', 'authority', 'report', 'previous_evaluation_id'), 'reviewed evaluation')
    _fields(request['authority'], ('actor', 'authority_id'), 'evaluation authority')
    if request['authority']['actor'] != 'Ayo':
        raise ValueError('Autorité opérateur requise')
    return evaluate(store, request['campaign_id'], request['attempt_id'], responsible=request['responsible'],
                    authority=request['authority'], check=lambda ctx, resources: request['report'],
                    previous_evaluation_id=request['previous_evaluation_id'])
