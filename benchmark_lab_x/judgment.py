"""Private assisted proposals; only evaluation.submit_report creates a verdict."""
from base64 import b64decode
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
import os
import re

from . import evaluation as e, outgoing, qualification as q, storage
from .storage import ConflictError, IntegrityError, BudgetError, _fields, _money, _strict_json as encode, _transaction
from .runtime import worker_lock, verify

FORMAT = 'benchmark-lab-x/judgment/v1'
REQUEST_FIELDS = ('operation_id', 'campaign_id', 'attempt_id', 'review_sha256',
                  'previous_evaluation_id', 'authority', 'budget_id', 'reserve_amount',
                  'requested_configuration')


def _admission(store, ctx):
    if os.path.lexists(store._root / 'restore.json') or ctx['campaign']['admission'] is None:
        raise ConflictError('Admission fermée ou restauration à rapprocher')
    return ctx['campaign']['admission']['admission_id']


def _inputs(store, connection, request, *, latest=True):
    _fields(request, REQUEST_FIELDS, 'judgment request')
    e._authority(request['authority']['actor'], request['authority'])
    if request['authority']['actor'] != 'Ayo':
        raise ValueError('Autorité opérateur requise')
    for key in ('operation_id', 'campaign_id', 'attempt_id', 'budget_id'):
        e.c.identifier(request[key])
    ctx = e._context(store, connection, request['campaign_id'], request['attempt_id'])
    content = e._review_content(store, ctx)
    if content['output'] is None or q.digest(content) != request['review_sha256']:
        raise IntegrityError('Projection ou sortie divergente')
    if latest:
        row = connection.execute('SELECT evaluation_id FROM s5_evaluations WHERE attempt_id=? ORDER BY rowid DESC LIMIT 1',
                                 (request['attempt_id'],)).fetchone()
        if request['previous_evaluation_id'] != (row[0] if row else None):
            raise ConflictError('Dernière évaluation requise')
    return ctx, content


def _envelope(store, connection, request, operation_id=None):
    config = request['requested_configuration']
    operations = store._operations(connection)
    budget = store._budget(connection, request['budget_id'], operations)
    if (budget['currency'] != 'USD' or _money(request['reserve_amount']) <= 0
            or _money(request['reserve_amount']) != _money(config['reserve_usd'])):
        raise BudgetError('Réserve USD liée au profil requise')
    if (store._blocking_costs(operations, budget, 'judgment')
            or _money(budget['available']) < 0
            or any(op['operation_id'] != operation_id and op['budget_id'] == request['budget_id']
                   and op['state'] in ('EMISSION_POSSIBLE', 'AMBIGUOUS') for op in operations)):
        raise BudgetError('Effets ou coûts non résolus')
    for op in operations:
        if op['operation_id'] == operation_id or op['engine_version'] != FORMAT:
            continue
        source = json.loads(op['resources'][0])['request']
        cost = store._effective_cost(connection, op)
        if source['campaign_id'] == request['campaign_id'] and (
                op['state'] in ('EMISSION_POSSIBLE', 'AMBIGUOUS')
                or cost is not None and cost['status'] == 'UNKNOWN'):
            raise BudgetError('Jugement dépendant non résolu, même avec une autre enveloppe')


def reserve(store, request, transport):
    request = deepcopy(request)
    with worker_lock(store, shared=True):
        verify(store)
        connection = e.connection_for(store)
        with _transaction(connection, write=True):
            ctx, content = _inputs(store, connection, request)
            aid = _admission(store, ctx)
            _envelope(store, connection, request)
            contract = ctx['qualification']['contract']
            operation = dict(operation_id=request['operation_id'], dossier_id=contract['dossier_id'],
                revision=contract['revision'], phase='judgment', authority=request['authority']['authority_id'],
                engine_version=FORMAT, requested_configuration=request['requested_configuration'], resources=[])
            wire = transport.prepare(deepcopy(operation), dict(outgoing_format=outgoing.FORMAT, outgoing=content))
            operation['resources'] = [encode(dict(request=request, admission_id=aid,
                context_sha256=q.digest(ctx), context=ctx, content=content)), wire]
            store._reserve_intent(connection, operation, request['budget_id'], request['reserve_amount'])
    return inspect(store, request['operation_id'])


def _bound(store, connection, operation, *, latest=False):
    if operation['engine_version'] != FORMAT or operation['phase'] != 'judgment' or len(operation['resources']) != 2:
        raise ValueError('Intention S14 requise')
    saved = json.loads(operation['resources'][0], object_pairs_hook=storage._unique_object)
    _fields(saved, ('request', 'admission_id', 'context_sha256', 'context', 'content'), 'judgment binding')
    request = saved['request']
    ctx, content = _inputs(store, connection, request, latest=latest)
    e._validate_context(saved['context'], ctx)
    if (q.digest(saved['context']) != saved['context_sha256']
            or saved['context']['campaign']['admission'] is None
            or saved['context']['campaign']['admission']['admission_id'] != saved['admission_id']):
        raise IntegrityError('Instantané de jugement divergent')
    contract = ctx['qualification']['contract']
    if (content != saved['content']
            or operation['operation_id'] != request['operation_id']
            or operation['authority'] != request['authority']['authority_id']
            or operation['budget_id'] != request['budget_id']
            or operation['reserved_amount'] != request['reserve_amount']
            or operation['requested_configuration'] != request['requested_configuration']
            or (operation['dossier_id'], operation['revision']) != (contract['dossier_id'], contract['revision'])):
        raise IntegrityError('Liaison de jugement divergente')
    wire = json.loads(operation['resources'][1], object_pairs_hook=storage._unique_object)
    config = operation['requested_configuration']
    from . import openrouter_preparation as profiles
    profile = dict(profile_id=config['profile_id'], model=config['model'], revision=config['revision'],
        parameters=config['parameters'], routes=config['routes'], system=wire['messages'][0]['content'],
        required_capabilities=[k for k in profiles.CAPABILITY_PARAMETERS if k in config['parameters']],
        **{k: config[k] for k in ('max_request_bytes', 'max_response_bytes', 'timeout_seconds')})
    if profiles.configuration(config['reservation_estimate'], profile) != config:
        raise IntegrityError('Configuration de jugement divergente')
    if (set(wire) != {'model', 'messages', *config['parameters']}
            or wire['model'] != config['model']
            or any(wire[k] != v for k, v in config['parameters'].items())
            or wire['messages'] != [dict(role='system', content=wire['messages'][0]['content']),
                                    dict(role='user', content=encode(content))]
            or sha256(wire['messages'][0]['content'].encode()).hexdigest() != config['prompt_sha256']):
        raise IntegrityError('Octets de jugement divergents')
    return saved, ctx


def local_criteria(spec):
    return {x['id'] for x in spec['obligations'] + spec['eliminatory_errors']
            if re.search(r'co[uû]t|\bcost\b|budget|latenc|durée|duration|transport', encode(x), re.I)}


def _proposal(store, connection, operation, ctx, answer):
    _fields(answer, ('findings', 'measures', 'limits', 'proposed_verdict'), 'judgment proposal')
    if answer['proposed_verdict'] not in ('SATISFAIT', 'NE SATISFAIT PAS', 'INDETERMINE'):
        raise ValueError('Verdict proposé inconnu')
    resources = e._resources(store, ctx)
    instructions = json.loads(operation['resources'][1])['messages'][0]['content']
    report = dict(findings=deepcopy(answer['findings']), measures=deepcopy(answer['measures']),
        limits=deepcopy(answer['limits']), judgment=dict(mode='human', instructions=instructions,
            resources_seen=list(resources), assistance_operation_id=None, model_links='INCONNU',
            disagreements=[], professional_review='ABSENTE'))
    # Validate model findings as data, before assigning server provenance
    e._report(store, connection, report, ctx, resources)
    spec = ctx['qualification']['contract']['specification']
    local = local_criteria(spec)
    for finding in report['findings']:
        if finding['criterion_id'] in local and finding['status'] != 'INDETERMINE':
            finding.update(status='INDETERMINE', attribution='evidence', evidence=[],
                           finding='Contrôle local requis : données exclues de la projection')
    if local:
        report['limits'].append('Les contrôles sur les données opérationnelles exigent une vérification locale.')
    report['judgment'].update(mode='assisted', assistance_operation_id=operation['operation_id'])
    return dict(report=report, proposed_verdict=answer['proposed_verdict'])


def execute(data, operation_id, transport):
    with closing(storage.Store(data)) as store, worker_lock(store, shared=True):
        verify(store)
        connection = e.connection_for(store)
        with _transaction(connection, write=True):
            operation = store._operation_for_update(connection, operation_id, ('INTENT_RECORDED',))
            saved, ctx = _bound(store, connection, operation, latest=True)
            if _admission(store, ctx) != saved['admission_id']:
                raise ConflictError('Admission modifiée')
            _envelope(store, connection, saved['request'], operation_id)
            request = dict(outgoing_format=outgoing.FORMAT, outgoing=saved['content'])
            wire = transport.prepare(deepcopy(operation), deepcopy(request))
            if wire != operation['resources'][1]:
                raise IntegrityError('Profil ou octets modifiés')
            connection.execute("UPDATE operations SET state='EMISSION_POSSIBLE' WHERE operation_id=?", (operation_id,))
        operation.update(state='EMISSION_POSSIBLE', conserved_wire=wire)
        try:
            response = transport(deepcopy(operation), deepcopy(request))
            _fields(response, ('receipt', 'cost'), 'judgment response')
            receipt = response['receipt']
            try:
                proposal = _proposal(store, connection, operation, ctx, receipt['result'])
            except (ValueError, KeyError, TypeError):
                proposal = None
                receipt['observed_configuration']['incident'] = 'UNUSABLE_JUDGMENT_PROPOSAL'
            receipt['result'] = proposal
            store.record_receipt(operation_id, receipt, response['cost'])
        except BaseException:
            current = next(x for x in store.inspect_operations() if x['operation_id'] == operation_id)
            if current['state'] == 'EMISSION_POSSIBLE':
                store.mark_ambiguous(operation_id, 'JUDGMENT_EFFECTS_UNKNOWN')
            raise


def inspect(store, operation_id):
    verify(store)
    connection = e.connection_for(store)
    with _transaction(connection):
        operation = store._operation_for_update(connection, operation_id,
                    ('INTENT_RECORDED', 'EMISSION_POSSIBLE', 'AMBIGUOUS', 'RECEIVED'))
        saved, ctx = _bound(store, connection, operation)
        request = saved['request']
        proposal = _retained_proposal(store, connection, operation, ctx)
        return dict(operation=operation, binding={k: request[k] for k in
                    ('campaign_id', 'attempt_id', 'previous_evaluation_id', 'review_sha256')}, proposal=proposal,
                    diagnostic=diagnostic(store, connection, operation, ctx))


def diagnostic(store, connection, operation, ctx):
    """Explain retained judge evidence without changing its receipt or repairing quotes"""
    receipt = operation['receipt']
    if receipt is None:
        return dict(state='RECONCILIATION_REQUIRED' if operation['state'] != 'INTENT_RECORDED' else 'EXECUTION_REQUIRED',
                    reason='Jugement sans reçu ; vérifier les effets avant tout nouvel appel')
    if receipt['result'] is not None:
        return dict(state='OWNER_REVIEW_REQUIRED', reason='Proposition disponible ; relecture et soumission locales requises')
    observed = receipt['observed_configuration']
    http = observed.get('http', {})
    if not http.get('complete') or http.get('status') != 200 or http.get('credential_redacted'):
        return dict(state='JUDGE_EXECUTION_REQUIRED', reason='Réponse du juge incomplète ou incident HTTP ; vérifier reçu et coût')
    try:
        data = json.loads(b64decode(http['body_base64'], validate=True), object_pairs_hook=storage._unique_object)
        choice = data['choices'][0]
        message = choice['message']
        if choice.get('finish_reason') == 'content_filter' or message.get('refusal') or choice.get('native_finish_reason') == 'refusal':
            return dict(state='REFUSAL_REVIEW_REQUIRED', reason='Refus du juge conservé ; examiner le contexte et le contenu')
        if choice.get('finish_reason') != 'stop':
            return dict(state='JUDGE_EXECUTION_REQUIRED', reason='Sortie du juge non terminée ; examiner les limites de transport')
        answer = json.loads(message['content'], object_pairs_hook=storage._unique_object)
        _proposal(store, connection, operation, ctx, answer)
    except IntegrityError:
        return dict(state='EVIDENCE_REVIEW_REQUIRED', reason='Identifiant, empreinte ou passage de preuve divergent ; relire la même sortie et corriger explicitement le jugement')
    except (ValueError, KeyError, TypeError, IndexError):
        return dict(state='JUDGE_FORMAT_REVIEW_REQUIRED', reason='Structure ou critères de la proposition invalides ; corriger le jugement sur la même sortie')
    return dict(state='JUDGE_EXECUTION_REQUIRED', reason='Incident de provenance du juge ; rapprocher les observations conservées')


def evaluation_judgment(store, connection, value, ctx, operation, result):
    saved, bound_ctx = _bound(store, connection, operation)
    e._validate_context(saved['context'], ctx)
    if (operation['receipt'] is None
            or operation['receipt']['result'] is None):
        raise IntegrityError('Proposition reçue et observation liée requises')
    expected = operation['receipt']['result']['report']['judgment']
    for key in ('mode', 'instructions', 'resources_seen', 'assistance_operation_id', 'model_links'):
        if value[key] != expected[key]:
            raise IntegrityError('Provenance assistée divergente')
    result.update(operation=operation, requested_configuration=operation['requested_configuration'],
                  observed_configuration=operation['receipt']['observed_configuration'], cost=operation['observed_cost'])
    return result


def _retained_proposal(store, connection, operation, ctx):
    receipt = operation['receipt']
    if receipt is None:
        return None
    wire = operation['resources'][1]
    observed = receipt['observed_configuration']
    if (receipt['resources_seen'] != [wire]
            or observed['outgoing'] != outgoing.wire_proof(wire, json.loads(wire)['messages'])):
        raise IntegrityError('Reçu sans lien aux octets émis')
    raw = b64decode(observed['http']['body_base64'], validate=True)
    if sha256(raw).hexdigest() != observed['http']['body_sha256']:
        raise IntegrityError('Octets de réponse divergents')
    proposal = receipt['result']
    if proposal is not None:
        document = json.loads(raw, object_pairs_hook=storage._unique_object)
        message = document['choices'][0]['message']
        answer = json.loads(message['content'], object_pairs_hook=storage._unique_object)
        from .openrouter_judgment import OpenRouterJudgment
        OpenRouterJudgment.validate_answer(None, answer, message)
        if (observed['incident'] is not None or not observed['http']['complete']
                or observed['http']['status'] != 200
                or document['model'] not in operation['requested_configuration']['model_identities']
                or document['choices'][0]['finish_reason'] != 'stop' or message.get('tool_calls')
                or proposal != _proposal(store, connection, operation, ctx, answer)):
            raise IntegrityError('Proposition divergente de la réponse conservée')
    return proposal


def verify_judgments(store, connection):
    for operation in store._operations(connection):
        if operation['engine_version'] == FORMAT:
            _, ctx = _bound(store, connection, operation)
            _retained_proposal(store, connection, operation, ctx)
