"""Receipt-driven recovery; auto-execution needs a frozen owner preauthorization"""
from base64 import b64decode
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import os

from . import campaigns as c
from .. import qualification as q, outgoing
from ..validation import digest as value_digest, identifier, _hash, _texts
from ..storage import (BudgetError, ConflictError, IntegrityError, _fields, _money, _transaction,
                       candidate_incident)

_IDENTITY = ('provider', 'model', 'revision', 'access', 'channel_id')
_UNRECOVERABLE = {'MODEL_IDENTITY_MISMATCH', 'PROVIDER_ROUTE_MISMATCH', 'HARNESS_ERROR'}
_RECOVERABLE = {'LENGTH', 'EMPTY_OUTPUT', 'ROUTE_ERROR'}
# Seul arrêt qui laisse partir une relance après incident fournisseur : le redémarrage de l'exécuteur (#533)
RESTARTED = 'PROCESS_STARTED_ADMISSION_BLOCKED'


def parent(store, connection, operation_id):
    row = connection.execute('SELECT campaign_id FROM s4_attempts WHERE operation_id=?', (operation_id,)).fetchone()
    if row is None:
        raise ValueError('Tentative source absente')
    snapshot = c._inspect(store, connection, row[0])
    attempt = next(a for a in snapshot['attempts'] if a['operation_id'] == operation_id)
    return snapshot, attempt


def _source(store, connection, operation_id):
    """Reçu source d'une relance et manifeste de sa campagne, lus sans `_inspect`

    Les contrôles d'une campagne descendante s'exécutent pendant son propre `_inspect` : inspecter la source
    à chaque maillon revérifierait toute la chaîne en cascade (#533)
    """
    row = connection.execute('SELECT a.cell_id, c.manifest_json, c.manifest_sha256 FROM s4_attempts a JOIN s4_campaigns c '
                             'USING(campaign_id) WHERE a.operation_id=?', (operation_id,)).fetchone()
    operations = store._operations(connection, operation_ids={operation_id})
    if row is None or len(operations) != 1:
        raise ValueError('Tentative source absente')
    return q._decode(row[1], row[2]), dict(operation_id=operation_id, cell_id=row[0], operation=operations[0])


def observation(attempt) -> dict:
    if attempt['state'] != 'RECEIVED':
        raise ConflictError('Reçu attribuable requis ; aucun rejeu ambigu')
    receipt = attempt['operation']['receipt']
    observed = receipt['observed_configuration']
    http = observed.get('http', {})
    raw = b64decode(http.get('body_base64', ''), validate=True)
    if (sha256(raw).hexdigest() != http.get('body_sha256') or not http.get('complete')
            or http.get('credential_redacted') or receipt['result']['emission'] != 'ESTABLISHED'):
        raise IntegrityError('Reçu HTTP complet requis')
    data = json.loads(raw)
    choices = data.get('choices') or []
    choice = choices[0] if len(choices) == 1 else {}
    message = choice.get('message') or {}
    reason = choice.get('finish_reason')
    incident = receipt['result']['incident']
    content = message.get('content') or ''
    if observed.get('channel_id') == 'https://api.anthropic.com/v1/messages':
        reason = {'end_turn': 'stop', 'max_tokens': 'length'}.get(data.get('stop_reason'), data.get('stop_reason'))
        content = receipt['result']['output'] or ''
    elif observed.get('channel_id', '').endswith('/responses'):
        status = data.get('status')
        detail = data.get('incomplete_details') or {}
        if status == 'completed':
            reason = 'stop'
        elif status == 'incomplete' and detail.get('reason') == 'max_output_tokens':
            reason = 'length'
        elif status == 'incomplete' and detail.get('reason') == 'content_filter':
            reason = 'content_filter'
        else:
            reason = status
        content = receipt['result']['output'] or ''
    if incident == 'CONTENT_REFUSAL' or reason in ('content_filter', 'refusal') or choice.get('native_finish_reason') == 'refusal' or message.get('refusal'):
        kind = 'CONTENT_REFUSAL'
    elif incident in _UNRECOVERABLE or observed.get('pi', {}).get('terminal') is False:
        kind = 'UNRECOVERABLE'
    elif http['status'] == 200 and reason == 'length':
        kind = 'LENGTH'
    elif http['status'] in (429, 502, 503, 504):
        kind = 'ROUTE_ERROR'
    elif http['status'] == 200 and reason == 'stop' and not content and incident in (None, 'PROVIDER_RESPONSE_INCOMPLETE'):
        kind = 'EMPTY_OUTPUT'
    elif http['status'] == 200 and reason == 'stop' and content and incident is None:
        kind = 'COMPLETE'
    else:
        kind = 'UNRECOVERABLE'
    if attempt['attribution_incident'] and (kind != 'ROUTE_ERROR' or any(
            observed.get(field) is not None for field in attempt['attribution_incident'])):
        raise ConflictError('Reçu attribuable requis ; aucune identité contradictoire admise')
    usage = data.get('usage') or {}
    return dict(kind=kind, output_chars=len(content), usage=usage,
                provider=observed.get('provider'), route=observed.get('route'),
                receipt_sha256=value_digest(receipt), received_at=http.get('received_at'))


def transmitted(receipt) -> dict:
    """Motifs de fin et consommation tels que le transport les a écrits dans le corps HTTP conservé

    Lecture seule, indépendante de l'attribution et de l'admissibilité à une reprise ; une valeur
    absente, ou un corps incomplet, expurgé ou altéré, reste None
    """
    unknown = dict(finish_reason=None, finish_detail=None, usage=None)
    observed = receipt.get('observed_configuration') if type(receipt) is dict else None
    http = observed.get('http') if type(observed) is dict else None
    if type(observed) is not dict or type(http) is not dict or not http.get('complete') or http.get('credential_redacted'):
        return unknown
    try:
        raw = b64decode(http.get('body_base64', ''), validate=True)
        data = json.loads(raw) if sha256(raw).hexdigest() == http.get('body_sha256') else None
    except (ValueError, TypeError):
        return unknown
    if type(data) is not dict:
        return unknown
    if observed.get('channel_id') == 'https://api.anthropic.com/v1/messages':
        native, detail = data.get('stop_reason'), None
    elif str(observed.get('channel_id', '')).endswith('/responses'):
        incomplete = data.get('incomplete_details')
        native, detail = data.get('status'), incomplete.get('reason') if type(incomplete) is dict else None
    else:
        choices = data.get('choices')
        choice = choices[0] if type(choices) is list and len(choices) == 1 and type(choices[0]) is dict else {}
        native, detail = choice.get('finish_reason'), choice.get('native_finish_reason')
    usage = data.get('usage')
    return dict(finish_reason=native if type(native) is str else None,
                finish_detail=detail if type(detail) is str else None,
                usage=usage if type(usage) is dict and usage else None)


def routing_error(operation):
    """A complete HTTP error may omit model identity without contradicting it"""
    if operation['receipt'] is None:
        return False
    try:
        return observation(dict(state=operation['state'], operation=operation,
            attribution_incident=c._attribution(operation['receipt'], operation['requested_configuration'])))['kind'] == 'ROUTE_ERROR'
    except (ValueError, ConflictError, IntegrityError, KeyError, TypeError):
        return False


def validate_link(store, connection, manifest):
    oid = manifest['recovery_of']
    row = connection.execute('SELECT c.rowid FROM s4_campaigns c JOIN s4_attempts a USING(campaign_id) WHERE a.operation_id=?', (oid,)).fetchone()
    own = connection.execute('SELECT rowid FROM s4_campaigns WHERE campaign_id=?', (manifest['campaign_id'],)).fetchone()
    if row is None or (own and row[0] >= own[0]):
        raise ValueError('La reprise doit suivre un reçu antérieur')
    old, attempt = _source(store, connection, oid)
    if candidate_incident(attempt['operation']):
        # Relance après incident : la même demande exactement ; son autorité est vérifiée à l'admission
        # (`bind_derived`). Une reprise de route change ses routes et suit le contrôle ci-dessous
        cell = next(x for x in old['plan'] if x['cell_id'] == attempt['cell_id'])
        config = next(x for x in old['panel'] if x['id'] == cell['configuration_id'])
        # Un secours officiel relancé garde sa preuve de routes épuisées, inchangée (#535)
        if (manifest['plan'] == [cell] and manifest['panel'] == [config]
                and all(manifest.get(k) == old.get(k) for k in ('contract_sha256', 'conditions', 'cases', 'cost_basis',
                                                                 'financial_cost_policy', 'official_fallback'))):
            return
    previous, attempt = parent(store, connection, oid)
    if observation(attempt)['kind'] not in _RECOVERABLE:
        raise ValueError('Incident non récupérable ; refus conservé')
    if any(manifest.get(k) != old.get(k) for k in ('contract_sha256', 'conditions', 'cases', 'cost_basis', 'financial_cost_policy')):
        raise ValueError('La reprise conserve tâche et conditions')
    cell = next(x for x in old['plan'] if x['cell_id'] == attempt['cell_id'])
    config = next(x for x in old['panel'] if x['id'] == cell['configuration_id'])
    if manifest['plan'] != [cell] or len(manifest['panel']) != 1:
        raise ValueError('Une reprise concerne uniquement la cellule interrompue')
    new = manifest['panel'][0]
    if 'official_fallback' in manifest:
        validate_official_link(store, connection, manifest, old, config)
        return
    if any(new[k] != config[k] for k in config if k not in ('parameters', 'route', 'effort')):
        raise ValueError('Modèle ou identité de tâche modifié')
    if not set(new['parameters']['provider']['only']) <= set(config['parameters']['provider']['only']):
        raise ValueError('Endpoint hors autorisation initiale')


def validate_official_link(store, connection, manifest, source, original_config):
    from ..transports.official import CHANNELS, native_identity, provider_for_endpoint
    from ..transports.openrouter import ENDPOINT
    new = manifest['panel'][0]
    official_provider = provider_for_endpoint(new['channel_id'])
    if (official_provider is None or new['provider'] != official_provider
            or original_config['channel_id'] != ENDPOINT):
        raise ValueError('Secours officiel depuis OpenRouter uniquement')
    kind = next(kind for kind, values in CHANNELS.items() if values[0] == new['provider'])
    if any(native_identity(kind, original_config[key]) != new[key] for key in ('model', 'revision')):
        raise ValueError('Identité native exacte indisponible ; aucun alias de substitution')
    if new['id'] != original_config['id'] or new['effort'] != original_config['effort']:
        raise ValueError('Cellule ou effort modifié dans le secours officiel')
    root = source
    while root.get('recovery_of'):
        root = parent(store, connection, root['recovery_of'])[0]['manifest']
    root_config = next(p for p in root['panel'] if p['id'] == original_config['id'])
    expected = set(root_config['parameters']['provider']['only'])
    attempted = set()
    own = connection.execute('SELECT rowid FROM s4_campaigns WHERE campaign_id=?', (manifest['campaign_id'],)).fetchone()
    for oid in manifest['official_fallback']['route_attempts']:
        row = connection.execute('SELECT c.rowid FROM s4_campaigns c JOIN s4_attempts a USING(campaign_id) WHERE a.operation_id=?', (oid,)).fetchone()
        if row is None or own and row[0] >= own[0]:
            raise ValueError('Preuve de route antérieure requise')
        snapshot, attempt = parent(store, connection, oid)
        config = attempt['operation']['requested_configuration']
        if (snapshot['manifest']['contract_sha256'] != source['contract_sha256']
                or snapshot['manifest']['conditions'] != source['conditions']
                or snapshot['manifest']['cases'] != source['cases']
                or attempt['cell_id'] != manifest['plan'][0]['cell_id']
                or next(cell for cell in snapshot['manifest']['plan'] if cell['cell_id'] == attempt['cell_id']) != manifest['plan'][0]
                or any(config[k] != original_config[k] for k in _IDENTITY)):
            raise ValueError('Tentative de route étrangère à la configuration source')
        observed = observation(attempt)
        exhausted_length = False
        if observed['kind'] == 'LENGTH':
            prior_id = snapshot['manifest'].get('recovery_of')
            if prior_id:
                _, prior = parent(store, connection, prior_id)
                before = observation(prior)
                prior_config = prior['operation']['requested_configuration']
                exhausted_length = (before['kind'] == 'LENGTH' and before['route'] == observed['route']
                    and config['effort'] == prior_config['effort']
                    and {k: v for k, v in config['parameters'].items() if k not in ('max_tokens', 'provider')}
                        == {k: v for k, v in prior_config['parameters'].items() if k not in ('max_tokens', 'provider')}
                    and config['parameters']['max_tokens'] > prior_config['parameters']['max_tokens']
                    and observed['output_chars'] <= before['output_chars'])
            _, grant = _owner_grant(store, connection, snapshot)
            tokens = observed['usage'].get('prompt_tokens')
            if grant and type(tokens) is int and tokens >= 0:
                capabilities = frozen_capabilities(grant, config['model'])
                endpoints = [endpoint for endpoint in capabilities['endpoints'] if endpoint['tag'] == observed['route']]
                if len(endpoints) == 1:
                    endpoint = endpoints[0]
                    limit = min(endpoint['max_completion_tokens'], endpoint['context_length'] - tokens,
                                snapshot['manifest']['conditions']['defaults']['context_window'] - tokens)
                    exhausted_length = exhausted_length or config['parameters']['max_tokens'] >= limit
        if observed['kind'] not in ('ROUTE_ERROR', 'EMPTY_OUTPUT') and not exhausted_length:
            raise ValueError('Reprise OpenRouter à résoudre avant secours officiel')
        allowed = config['parameters']['provider']['only']
        if not set(allowed) <= expected:
            raise ValueError('Route non autorisée')
        route = observed.get('route')
        if route:
            attempted.update(tag for tag in expected if route == tag or route.startswith(tag + '/'))
        elif len(allowed) == 1:
            attempted.add(allowed[0])
    if not expected <= attempted or manifest['recovery_of'] not in manifest['official_fallback']['route_attempts']:
        raise ValueError('Routes OpenRouter non épuisées ou reçu source absent')


def _identity(configuration, outgoing_format):
    return dict({key: configuration[key] for key in _IDENTITY}, outgoing_format=outgoing_format)


def _cost(attempt):
    return deepcopy(attempt['operation']['observed_cost'])


def _chain(store, connection, snapshot, attempt):
    steps = []
    current_snapshot, current = snapshot, attempt
    while True:
        try:
            observed = observation(current)
        except (ValueError, ConflictError, IntegrityError):
            observed = dict(kind='UNRECOVERABLE', route=None, received_at=None)
        config = current['operation']['requested_configuration']
        steps.append(dict(operation_id=current['operation_id'], kind=observed['kind'],
                          incident=current['operation']['receipt']['result']['incident'],
                          route=observed.get('route'), requested_route=config.get('route'),
                          parameters=deepcopy(config['parameters']), received_at=observed.get('received_at'),
                          cost=_cost(current)))
        parent_id = current_snapshot['manifest'].get('recovery_of')
        if not parent_id:
            break
        current_snapshot, current = parent(store, connection, parent_id)
    steps.reverse()
    return steps


def _official_route_attempts(store, connection, snapshot, attempt):
    """Keep the final LENGTH proof for a route; recovery_of retains prior receipts"""
    selected = []
    length_routes = set()
    for step in reversed(_chain(store, connection, snapshot, attempt)):
        if step['kind'] == 'LENGTH':
            allowed = step['parameters'].get('provider', {}).get('only', [])
            route = step['route'] or (allowed[0] if len(allowed) == 1 else tuple(allowed))
            if route in length_routes:
                continue
            length_routes.add(route)
        selected.append(step['operation_id'])
    selected.reverse()
    return selected


def _record(snapshot, attempt, observed, chain):
    config = attempt['operation']['requested_configuration']
    return dict(outgoing_format=outgoing.FORMAT, provider=config['provider'], model=config['model'],
                revision=config['revision'], access=config['access'], channel_id=config['channel_id'],
                parameters=deepcopy(config['parameters']), effort=config['effort'], route=config['route'],
                observed_provider=observed['provider'], observed_route=observed['route'],
                operation_id=attempt['operation_id'], campaign_id=snapshot['manifest']['campaign_id'],
                receipt_sha256=observed['receipt_sha256'], received_at=observed['received_at'],
                cost=_cost(attempt), recovery_chain=chain)


def profile(store, identity):
    """Successful transport parameters derived from the existing durable receipts"""
    _fields(identity, _IDENTITY + ('outgoing_format',), 'transport identity')
    connection = c.connection_for(store)
    with _transaction(connection):
        for (oid,) in connection.execute('SELECT operation_id FROM s4_attempts ORDER BY rowid DESC').fetchall():
            snapshot, attempt = parent(store, connection, oid)
            config = attempt['operation']['requested_configuration']
            contract = c._approved(store, connection, snapshot['manifest']['contract_sha256'])
            if (contract['package'].get('outgoing_format') != identity['outgoing_format']
                    or any(config[key] != identity[key] for key in _IDENTITY)):
                continue
            try:
                observed = observation(attempt)
            except (ValueError, ConflictError, IntegrityError):
                continue
            if observed['kind'] == 'COMPLETE':
                return _record(snapshot, attempt, observed, _chain(store, connection, snapshot, attempt))
    return None


def derive_child(source_manifest, attempt, observed, capabilities, *, budget_id, earlier_output_chars=None):
    """Pure child manifest from a receipt and frozen capabilities, without a live budget check"""
    if observed['kind'] not in _RECOVERABLE:
        raise ValueError('Aucune reprise automatique de ce reçu')
    manifest = deepcopy(source_manifest)
    cell = next(x for x in manifest['plan'] if x['cell_id'] == attempt['cell_id'])
    config = next(x for x in manifest['panel'] if x['id'] == cell['configuration_id'])
    if capabilities['id'] != config['model']:
        raise ValueError('Capacités d’un autre modèle')
    if source_manifest.get('recovery_of') and observed['kind'] == 'LENGTH':
        if earlier_output_chars is None or observed['output_chars'] <= earlier_output_chars:
            raise ValueError('Arrêt : aucune progression de sortie')
    params = config['parameters']
    allowed = params['provider']['only']
    endpoints = [e for tag in allowed for e in capabilities['endpoints']
                 if e['tag'] == tag and e['status'] == 0]
    if observed['kind'] in ('ROUTE_ERROR', 'EMPTY_OUTPUT'):
        if not observed['route']:
            raise ValueError('Endpoint fautif non attribué')
        endpoints = [e for e in endpoints if e['tag'] != observed['route']]
    if not endpoints:
        raise ValueError('Endpoints autorisés épuisés')
    input_tokens = observed['usage'].get('prompt_tokens')
    if type(input_tokens) is not int or input_tokens < 0:
        if observed['kind'] != 'ROUTE_ERROR':
            raise ValueError('Quantité d’entrée non établie')
        # Bound context without claiming an observed token count
        input_tokens = manifest['conditions']['defaults']['context_window'] - params['max_tokens']
        if input_tokens < 0:
            raise ValueError('Limite de contexte atteinte')
    limit = min(manifest['conditions']['defaults']['context_window'] - input_tokens,
                *(min(e['max_completion_tokens'], e['context_length'] - input_tokens) for e in endpoints))
    if observed['kind'] == 'LENGTH':
        params['max_tokens'] = min(params['max_tokens'] * 2, limit)
        if params['max_tokens'] <= attempt['operation']['requested_configuration']['parameters']['max_tokens']:
            raise ValueError('Limite modèle atteinte')
    if params['max_tokens'] > limit:
        raise ValueError('Limite endpoint atteinte')
    for e in endpoints:
        if not set(params).difference({'provider', 'stream'}) <= set(e['supported_parameters']):
            raise ValueError('Paramètres non pris en charge')
    tags = [e['tag'] for e in endpoints]
    params['provider'].update(only=tags, order=tags)
    config['route'] = 'OpenRouter ordered endpoints: ' + ','.join(tags)
    # Reserve the full declared context as input, without a speculative discount
    reserves = []
    for e in endpoints:
        pricing = e['pricing']
        if pricing.keys() - {'prompt','completion','input_cache_read','input_cache_write','discount'}:
            raise ValueError('Tarification additionnelle à vérifier')
        reserves.append(_money(pricing['prompt']) * manifest['conditions']['defaults']['context_window']
                        + _money(pricing['completion']) * params['max_tokens'])
    reserve = max(reserves)
    manifest.update(campaign_id='recovery-' + value_digest([attempt['operation_id'], params])[:40],
                    recovery_of=attempt['operation_id'], panel=[config], plan=[cell],
                    attempt_policy=dict(retries=False, order=[cell['cell_id']],
                    reason='Reprise technique liée au reçu ' + attempt['operation_id'] + ' ; aucune sélection sémantique'))
    return dict(manifest=manifest, reserve_amount=str(reserve), budget_id=budget_id,
                source_receipt=observed, capabilities_sha256=value_digest(capabilities))


def _progress_chars(store, connection, source_manifest, observed):
    if not source_manifest.get('recovery_of') or observed['kind'] != 'LENGTH':
        return None
    _, earlier = parent(store, connection, source_manifest['recovery_of'])
    return observation(earlier)['output_chars']


def _proposal(store, connection, operation_id, capabilities, *, check_budget=True) -> dict:
    """Shared proposal body; caller owns the enclosing transaction"""
    snapshot, attempt = parent(store, connection, operation_id)
    contract = c._approved(store, connection, snapshot['manifest']['contract_sha256'])
    if contract['package'].get('outgoing_format') != outgoing.FORMAT:
        raise ValueError('Ancien contenu : nouvelle comparaison requise')
    observed = observation(attempt)
    proposal = derive_child(snapshot['manifest'], attempt, observed, capabilities,
                            budget_id=snapshot['admissions'][-1]['authority']['budget_id'],
                            earlier_output_chars=_progress_chars(store, connection, snapshot['manifest'], observed))
    if check_budget:
        budget = c._envelope(store, connection, snapshot, snapshot['admissions'][-1]['authority'])
        if not budget['provider_managed'] and _money(proposal['reserve_amount']) > Decimal(budget['available']):
            raise BudgetError('Budget de reprise insuffisant')
    return proposal


def _official_grant(grant, configuration_id):
    item = (grant.get('official_fallbacks') or {}).get(configuration_id)
    if item is None:
        raise ValueError('Secours officiel préautorisé absent')
    return deepcopy(item)


def _official_proposal(store, connection, operation_id, grant, budget_id, *, check_budget=True) -> dict:
    snapshot, attempt = parent(store, connection, operation_id)
    observed = observation(attempt)
    if observed['kind'] not in _RECOVERABLE:
        raise ValueError('Aucun secours automatique de ce reçu')
    cell = next(x for x in snapshot['manifest']['plan'] if x['cell_id'] == attempt['cell_id'])
    source_config = next(x for x in snapshot['manifest']['panel'] if x['id'] == cell['configuration_id'])
    item = _official_grant(grant, source_config['id'])
    manifest = deepcopy(snapshot['manifest'])
    config = item['configuration']
    route_attempts = _official_route_attempts(store, connection, snapshot, attempt)
    manifest.update(
        campaign_id='recovery-' + value_digest([operation_id, config, route_attempts])[:40],
        recovery_of=operation_id, panel=[config], plan=[cell],
        official_fallback=dict(route_attempts=route_attempts),
        attempt_policy=dict(retries=False, order=[cell['cell_id']],
                            reason='Secours officiel préautorisé après épuisement OpenRouter ; aucune sélection sémantique'))
    validate_official_link(store, connection, manifest, snapshot['manifest'], source_config)
    proposal = dict(manifest=manifest, reserve_amount=item['reserve_amount'], budget_id=budget_id,
                    source_receipt=observed, capabilities_sha256=value_digest(item))
    if check_budget:
        budget = c._envelope(store, connection, snapshot, snapshot['admissions'][-1]['authority'])
        if not budget['provider_managed'] and _money(proposal['reserve_amount']) > Decimal(budget['available']):
            raise BudgetError('Budget de secours officiel insuffisant')
    return proposal


def _automatic_proposal(store, connection, operation_id, grant, *, allow_official,
                        check_budget=True):
    if 'incident_retries' in grant:
        manifest, attempt = _source(store, connection, operation_id)
        if candidate_incident(attempt['operation']):
            return _incident_proposal(store, connection, manifest, attempt, grant, check_budget=check_budget)
    snapshot, attempt = parent(store, connection, operation_id)
    if 'max_recoveries' in grant:
        # Compteur explicite : les limites calculées ne suffisent pas à borner la chaîne
        # Une relance après incident fournisseur a son propre plafond
        depth, current = 0, snapshot
        while current['manifest'].get('recovery_of'):
            current, source = parent(store, connection, current['manifest']['recovery_of'])
            depth += not (candidate_incident(source['operation']) and 'incident_retries' in grant)
        if depth >= grant['max_recoveries']:
            raise ValueError('Nombre de reprises préautorisées atteint')
    budget_id = snapshot['admissions'][-1]['authority']['budget_id']
    capabilities = frozen_capabilities(grant, attempt['operation']['requested_configuration']['model'])
    try:
        return _proposal(store, connection, operation_id, capabilities,
                         check_budget=check_budget)
    except ValueError:
        if not allow_official:
            raise
        return _official_proposal(store, connection, operation_id, grant, budget_id,
                                  check_budget=check_budget)


def relaunched(store, connection, snapshot, attempt):
    """Reçu en incident fournisseur que le lancement préautorise à relancer (#533)

    Une préautorisation antérieure garde la reprise ROUTE_ERROR sur une autre route
    """
    if not candidate_incident(attempt['operation']):
        return False
    _, grant = _owner_grant(store, connection, snapshot)
    return 'incident_retries' in (grant or {})


def incident_rank(store, connection, snapshot):
    """Rang de la tentative de cette campagne dans sa série de relances après incident fournisseur (#533)"""
    # L'appelant a vérifié la préautorisation du lancement. Les manifestes et reçus sont lus sans `_inspect` :
    # appelé depuis la vérification d'une admission dérivée, un `_inspect` par maillon revérifierait chaque
    # ancêtre en cascade
    rank, source_id = 1, snapshot['manifest'].get('recovery_of')
    while source_id:
        source = store._operations(connection, operation_ids={source_id})
        if len(source) != 1 or not candidate_incident(source[0]):
            break
        rank += 1
        raw, digest = connection.execute('SELECT c.manifest_json, c.manifest_sha256 FROM s4_campaigns c JOIN s4_attempts a '
                                         'USING(campaign_id) WHERE a.operation_id=?', (source_id,)).fetchone()
        source_id = q._decode(raw, digest).get('recovery_of')
    return rank


def cell_attempts(store, connection, snapshot):
    """Tentatives de la cellule jusqu'à celle de cette campagne, reprises pour longueur ou de route comprises

    Le plafond de relance porte sur toute la cellule (#533). Manifestes lus sans `_inspect`, comme `incident_rank`
    """
    count, source_id = 1, snapshot['manifest'].get('recovery_of')
    while source_id:
        count += 1
        raw, digest = connection.execute('SELECT c.manifest_json, c.manifest_sha256 FROM s4_campaigns c JOIN s4_attempts a '
                                         'USING(campaign_id) WHERE a.operation_id=?', (source_id,)).fetchone()
        source_id = q._decode(raw, digest).get('recovery_of')
    return count


def incident_due(store, connection, snapshot, attempt):
    """Échéance de la relance : 30 s, 2 min, 10 min puis 30 min après le reçu, ou un Retry-After plus long"""
    from ..preparation import RETRY_SECONDS, _retry_after
    observed = attempt['operation']['receipt']['observed_configuration'] or {}
    http = observed.get('http')
    http = http if isinstance(http, dict) else {}
    failed_at = datetime.fromisoformat(http.get('received_at') or attempt['operation']['created_at'])
    headers = http.get('response_headers')
    headers = headers if isinstance(headers, dict) else {}
    rank = min(incident_rank(store, connection, snapshot), len(RETRY_SECONDS))
    return failed_at + timedelta(seconds=max(RETRY_SECONDS[rank - 1], _retry_after(headers.get('Retry-After'), failed_at)))


def incident_open(store, connection, snapshot):
    """Relance permise : chaque campagne de la chaîne admise, ou arrêtée par le seul redémarrage de l'exécuteur

    Un redémarrage ferme toutes les admissions sans rien établir contre la série ; tout autre arrêt la clôt
    """
    current = snapshot
    while True:
        if current['admission'] is None and current['stop_reason'] != RESTARTED:
            return False
        if not current['manifest'].get('recovery_of'):
            return not os.path.lexists(store._root / 'restore.json')
        current, _ = parent(store, connection, current['manifest']['recovery_of'])


def pending_relaunches(store, connection, campaign_id):
    """Relances après incident fournisseur encore attendues dans une comparaison et ses reprises

    (tentative en incident, échéance) ; la comparaison n'est pas acquise tant que la liste n'est pas vide
    """
    snapshots = [c._inspect(store, connection, cid)
                 for cid in [campaign_id] + c._recovery_descendants(connection, campaign_id)]
    # Une relance réservée puis arrêtée par le seul redémarrage, jamais émise, reste attendue (#535)
    followed = {snapshot['manifest'].get('recovery_of') for snapshot in snapshots
                if snapshot['admission'] is not None or snapshot['stop_reason'] != RESTARTED
                or [a['state'] for a in snapshot['attempts']] != ['INTENT_RECORDED']}
    result = []
    for snapshot in snapshots:
        for attempt in snapshot['attempts']:
            if attempt['operation_id'] in followed or not candidate_incident(attempt['operation']):
                continue
            _, grant = _owner_grant(store, connection, snapshot)
            if (grant is not None and incident_open(store, connection, snapshot)
                    and cell_attempts(store, connection, snapshot) < grant.get('incident_retries', 0)):
                result.append((attempt, incident_due(store, connection, snapshot, attempt)))
    return result


def _incident_proposal(store, connection, source, attempt, grant, *, check_budget=True):
    """Même demande sous une nouvelle identité liée au reçu en incident, avec sa propre réserve"""
    if cell_attempts(store, connection, dict(manifest=source)) >= grant.get('incident_retries', 0):
        raise ValueError('Relances après incident fournisseur épuisées')
    manifest = deepcopy(source)
    cell = next(x for x in manifest['plan'] if x['cell_id'] == attempt['cell_id'])
    config = next(x for x in manifest['panel'] if x['id'] == cell['configuration_id'])
    manifest.update(campaign_id='recovery-' + value_digest([attempt['operation_id'], 'incident'])[:40],
                    recovery_of=attempt['operation_id'], panel=[config], plan=[cell],
                    attempt_policy=dict(retries=False, order=[cell['cell_id']],
                                        reason='Relance après incident fournisseur du reçu ' + attempt['operation_id']
                                        + ' ; même demande, aucune sélection sémantique'))
    # Même enveloppe que la tentative source, dont la réservation porte le budget
    proposal = dict(manifest=manifest, reserve_amount=attempt['operation']['reserved_amount'],
                    budget_id=attempt['operation']['budget_id'])
    if check_budget:
        snapshot, _ = parent(store, connection, attempt['operation_id'])
        budget = c._envelope(store, connection, snapshot, snapshot['admissions'][-1]['authority'])
        if not budget['provider_managed'] and _money(proposal['reserve_amount']) > Decimal(budget['available']):
            raise BudgetError('Budget de relance insuffisant')
    return proposal


def propose(store, operation_id, capabilities):
    """No call or write: adapt from a receipt and already supplied public metadata"""
    connection = c.connection_for(store)
    with _transaction(connection):
        return _proposal(store, connection, operation_id, capabilities)


def diagnose(store, operation_id):
    """Explain the existing recovery gate without reserving or emitting anything"""
    c._intact(store)
    connection = c.connection_for(store)
    with _transaction(connection):
        snapshot, attempt = parent(store, connection, operation_id)
        try:
            observed = observation(attempt)
        except (ValueError, ConflictError, IntegrityError, KeyError, TypeError):
            return dict(kind='RECONCILIATION_REQUIRED', automatic=False,
                        reason='Reçu complet attribuable absent ; rapprocher les effets avant tout rejeu')
        kind = observed['kind']
        if kind == 'COMPLETE':
            return dict(kind=kind, automatic=False, reason='Sortie complète : poursuivre le jugement, sans nouvel appel candidat')
        if kind not in _RECOVERABLE:
            return dict(kind=kind, automatic=False, reason='Examiner le refus ou l’intégrité ; aucun contournement ni rejeu automatique')
        _, grant = _owner_grant(store, connection, snapshot)
        if grant is None:
            return dict(kind=kind, automatic=False, reason='Préautorisation de reprise absente ; préparer une reprise avec ses capacités et son autorité')
        try:
            proposal = _automatic_proposal(store, connection, operation_id, grant,
                                           allow_official=True)
        except (ValueError, ConflictError, IntegrityError, BudgetError, KeyError) as exc:
            return dict(kind=kind, automatic=False, reason=str(exc))
        admitted = snapshot['admission'] is not None and not os.path.lexists(store._root / 'restore.json')
        return dict(kind=kind, automatic=admitted, reason='Reprise technique préautorisée' if admitted else 'Admission fermée ; aucune émission',
                    next_campaign_id=proposal['manifest']['campaign_id'], reserve_amount=proposal['reserve_amount'])


def starting_configuration(store, configuration, *, content_format=None):
    """Prepare a future configuration from a complete receipt, never edit a contract"""
    proposed = deepcopy(configuration)
    if content_format != outgoing.FORMAT:
        return dict(configuration=proposed, profile=None)
    learned = profile(store, _identity(proposed, content_format))
    if learned is None:
        return dict(configuration=proposed, profile=None)
    proposed['parameters'] = deepcopy(learned['parameters'])
    proposed['route'] = learned['route']
    proposed['effort'] = learned['effort']
    return dict(configuration=proposed, profile=learned)


def _capability(value):
    _fields(value, ('id', 'endpoints'), 'frozen capabilities')
    c._present(value['id'], 'identifiant de modèle')
    if type(value['endpoints']) is not list or not value['endpoints']:
        raise ValueError('Endpoints figés requis')
    for endpoint in value['endpoints']:
        needed = ('tag', 'status', 'max_completion_tokens', 'context_length', 'supported_parameters', 'pricing')
        if type(endpoint) is not dict or any(key not in endpoint for key in needed):
            raise ValueError('Endpoint figé incomplet')
        c._present(endpoint['tag'], 'étiquette d’endpoint')
        if type(endpoint['status']) is not int:
            raise ValueError('Statut d’endpoint figé requis')
        if type(endpoint['max_completion_tokens']) is not int or endpoint['max_completion_tokens'] < 0:
            raise ValueError('Plafond de sortie figé requis')
        if type(endpoint['context_length']) is not int or endpoint['context_length'] < 0:
            raise ValueError('Contexte figé requis')
        _texts(endpoint['supported_parameters'], 'supported_parameters', unique=True)
        pricing = endpoint['pricing']
        if type(pricing) is not dict:
            raise ValueError('Tarifs figés requis')
        if pricing.keys() - {'prompt', 'completion', 'input_cache_read', 'input_cache_write', 'discount'}:
            raise ValueError('Tarification additionnelle à vérifier')
        for key in ('prompt', 'completion'):
            if key not in pricing:
                raise ValueError('Tarifs figés requis')
            _money(pricing[key])
    value_digest(value)


def validate_grant(grant, *, purpose, recovery):
    if purpose != 'start' or recovery:
        raise ValueError('Préautorisation figée à l’admission propriétaire initiale')
    extra = tuple(key for key in ('official_fallbacks', 'max_recoveries', 'incident_retries') if key in grant)
    _fields(grant, ('capabilities',) + extra, 'technical recovery')
    limit = grant.get('max_recoveries', 1)
    if type(limit) is not int or limit < 1:
        raise ValueError('Nombre de reprises invalide')
    attempts = grant.get('incident_retries', 1)
    if type(attempts) is not int or attempts < 1:
        raise ValueError('Nombre de tentatives après incident invalide')
    capabilities = grant['capabilities']
    if type(capabilities) is dict:
        items = [capabilities]
    # Sans capacité figée, seule la relance après incident reste préautorisée (#533)
    elif type(capabilities) is list and (capabilities or 'incident_retries' in grant):
        items = capabilities
    else:
        raise ValueError('Capacités figées requises')
    seen = set()
    for item in items:
        _capability(item)
        if item['id'] in seen:
            raise ValueError('Capacités dupliquées')
        seen.add(item['id'])
    fallbacks = grant.get('official_fallbacks', {})
    if type(fallbacks) is not dict:
        raise ValueError('Secours officiels figés invalides')
    for configuration_id, item in fallbacks.items():
        identifier(configuration_id)
        _fields(item, ('configuration', 'channel', 'reserve_amount'), 'official fallback grant')
        config = item['configuration']
        _fields(config, c._CONFIGURATION, 'official fallback configuration')
        if config['id'] != configuration_id:
            raise ValueError('Identité de secours officielle divergente')
        from ..transports.official import provider_for_endpoint
        if (provider_for_endpoint(config['channel_id']) != config['provider']
                or config['access'] != 'API' or config['route'] != config['channel_id']):
            raise ValueError('Canal de secours officiel invalide')
        channel = item['channel']
        keys = {'available', 'revision', 'channel_id', 'route', 'proof'} | set(config['required_observations'])
        _fields(channel, keys, 'official fallback channel')
        c._present(channel['proof'], 'official fallback proof')
        if channel['available'] is not True or any(
                channel[key] != config[key] for key in keys - {'available', 'proof'}):
            raise ValueError('Preuve de secours officielle divergente')
        _money(item['reserve_amount'])


def validate_derived_from(value):
    _fields(value, ('admission_id', 'operation_id', 'manifest_sha256'), 'derived recovery authority')
    identifier(value['admission_id'])
    identifier(value['operation_id'])
    _hash(value['manifest_sha256'])


def bind_derived(store, connection, manifest, authority, *, stored=False):
    error = IntegrityError if stored else ValueError
    derived = authority['derived_from']
    if manifest.get('recovery_of') != derived['operation_id']:
        raise error('Admission dérivée sans reçu source')
    row = connection.execute('SELECT record_json, record_sha256 FROM s4_admissions WHERE admission_id=?',
                             (derived['admission_id'],)).fetchone()
    if row is None:
        raise error('Admission propriétaire absente')
    source = q._decode(row[0], row[1])
    owner = source['authority']
    if 'technical_recovery' not in owner:
        raise error('Préautorisation propriétaire absente')
    if owner['manifest_sha256'] != derived['manifest_sha256']:
        raise error('Empreinte propriétaire divergente')
    for key in ('actor', 'authority_id', 'execution_authority', 'candidate_authority', 'budget_authority', 'budget_id'):
        if authority[key] != owner[key]:
            raise error('Autorité dérivée divergente')
    try:
        expected = _automatic_proposal(store, connection, derived['operation_id'],
                                       owner['technical_recovery'], allow_official=True,
                                       check_budget=False)
    except (ValueError, ConflictError, IntegrityError) as exc:
        raise error('Reprise dérivée non reconstruite') from exc
    cell = expected['manifest']['plan'][0]['cell_id']
    if (value_digest(manifest) != value_digest(expected['manifest'])
            or authority['budget_id'] != expected['budget_id']
            or authority['reserve_amounts'] != {cell: expected['reserve_amount']}):
        raise error('Manifeste dérivé non conforme à la préautorisation')


def frozen_capabilities(grant, model):
    capabilities = grant['capabilities']
    items = [capabilities] if type(capabilities) is dict else list(capabilities)
    match = [item for item in items if item['id'] == model]
    if len(match) != 1:
        raise ValueError('Capacités d’un autre modèle')
    return deepcopy(match[0])


def _owner_grant(store, connection, snapshot):
    current = snapshot
    while current['manifest'].get('recovery_of'):
        current, _ = parent(store, connection, current['manifest']['recovery_of'])
    start = next((record for record in current['admissions'] if record['authority']['purpose'] == 'start'), None)
    if start is None:
        return None, None
    return start, start['authority'].get('technical_recovery')
