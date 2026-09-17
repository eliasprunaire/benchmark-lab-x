"""Vérification explicite de slugs personnels, distincte d'une comparaison"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.client import HTTPException
import json
import os

from . import model_catalogue, preparation as p, provider_access, storage
from .model_catalog import require_current
from .transports.openrouter import ENDPOINT, consumption, post
from .transports.prices import UNITS, price_row
from .validation import identifier


ENGINE = 'benchmark-lab-x/openrouter-slug-check/v1'
MAX_OUTPUT_TOKENS = 128
MESSAGES = [{'role': 'user', 'content': 'Reply with the single word OK.'}]
STATUS_TEXT = {
    'EMISSION_POSSIBLE': 'Vérification en cours…',
    'AMBIGUOUS': 'Vérification interrompue : les effets de l’appel restent inconnus. Aucune relance automatique.',
    'RESPONDED': 'Le modèle a répondu. Vous pouvez le sélectionner pour la comparaison.',
    'UNCONFIRMED': 'Aucune réponse complète vérifiable : erreur, refus, réponse vide ou interrompue. Le modèle n’a pas été ajouté.',
    'EXPIRED': 'Vérification à renouveler : le relevé a expiré ou votre clé a changé.',
    'NOT_SENT': 'Vérification annulée avant envoi : les nouveaux appels ont été fermés.',
}


def _key_binding(connection, session_id):
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s2_provider_access'").fetchone():
        return None
    row = connection.execute(
        "SELECT key_cipher FROM s2_provider_access WHERE session_id=? AND status='connected'",
        (session_id,)).fetchone()
    return None if row is None else sha256(row[0].encode()).hexdigest()


def _records(store, session_id, dossier_id):
    connection = p.connection_for(store)
    p.owner(connection, session_id, dossier_id)
    ids = {row[0] for row in connection.execute(
        'SELECT operation_id FROM operations WHERE dossier_id=? AND engine_version=? ORDER BY created_at',
        (dossier_id, ENGINE))}
    return sorted(store._operations(connection, operation_ids=ids), key=lambda row: row['created_at'])


def _valid(record, binding):
    configuration = record['requested_configuration']
    return (record['state'] == 'RECEIVED'
            and record['receipt']['result']['status'] == 'RESPONDED'
            and binding is not None and configuration['key_binding'] == binding
            and model_catalogue._now() - datetime.fromisoformat(configuration['metadata_at'])
            < timedelta(hours=model_catalogue._settings(model_catalogue._registry())['cache_hours']))


def selection(store, session_id, dossier_id):
    """Le catalogue partagé reste intact ; les ajouts appartiennent au dossier privé"""
    try:
        catalogue = model_catalogue.selection(store)
    except LookupError:
        catalogue = {'models': [], 'fetched_at': None, 'stale': False}
    models = {model['id']: model for model in catalogue['models']}
    binding = _key_binding(p.connection_for(store), session_id)
    for record in _records(store, session_id, dossier_id):
        if _valid(record, binding):
            config = record['requested_configuration']
            models[config['model']] = {**config['metadata'], 'probe_operation_id': record['operation_id'],
                                      'fetched_at': config['metadata_at']}
    if not models and catalogue['fetched_at'] is None:
        raise LookupError('Aucun relevé de modèles connu')
    return {**catalogue, 'models': list(models.values())}


def view(store, session_id, dossier_id):
    binding = _key_binding(p.connection_for(store), session_id)
    results = []
    for record in _records(store, session_id, dossier_id):
        config = record['requested_configuration']
        state = record['state']
        result = {} if state != 'RECEIVED' else record['receipt']['result']
        status = result.get('status', state)
        usable = _valid(record, binding)
        if status == 'RESPONDED' and not usable:
            status = 'EXPIRED'
        results.append({'operation_id': record['operation_id'], 'slug': config['model'],
            'name': config['metadata']['name'], 'status': status, 'usable': usable,
            'detail': STATUS_TEXT.get(status, 'Vérification en attente.'), 'cost': record['observed_cost'],
            'reserve_usd': record['reserved_amount']})
    return list(reversed(results))


def _metadata(slug, fetch):
    model = model_catalogue._data(fetch('/api/v1/model/' + slug), dict)
    detail = model_catalogue._data(fetch('/api/v1/models/' + slug + '/endpoints'), dict)
    if (model.get('id') != slug or detail.get('id') != slug or model_catalogue._malformed(model)
            or model['architecture'].get('output_modalities') != ['text']
            or type(detail.get('endpoints')) is not list):
        raise ValueError('Identité ou sortie texte non vérifiée')
    excluded = model_catalogue._settings(model_catalogue._registry()).get('excluded_providers', [])
    detail = {**detail, 'endpoints': [row for row in detail['endpoints']
        if type(row) is dict and row.get('model_id') == slug
        and 'max_tokens' in row.get('supported_parameters', [])
        and type(row.get('context_length')) is int and row['context_length'] >= MAX_OUTPUT_TOKENS
        and type(row.get('max_completion_tokens')) is int and row['max_completion_tokens'] >= MAX_OUTPUT_TOKENS]}
    metadata = model_catalogue.model_view(model, detail, excluded)
    endpoint = next((row for row in detail['endpoints'] if row.get('tag') == metadata['route']), None)
    if endpoint is None:
        raise ValueError('Endpoint utilisable non vérifié')
    pricing = endpoint['pricing']
    allowed = UNITS.keys() | {'discount', 'overrides'}
    if type(pricing) is not dict or pricing.keys() - allowed:
        raise ValueError('Tarification non prise en charge')
    parameters = {'max_tokens': MAX_OUTPUT_TOKENS, 'stream': False,
        'provider': {'only': [metadata['route']], 'order': [metadata['route']],
                     'allow_fallbacks': False, 'require_parameters': True, 'data_collection': 'deny'}}
    levels = metadata['reasoning_levels']
    if 'reasoning' in endpoint.get('supported_parameters', []):
        lowest = next((level for level in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
                       if level in levels), None)
        if lowest is not None:
            parameters['reasoning'] = {'effort': lowest}
    # Réserve prudente : un token par octet du message, plus l'enveloppe de chat
    input_tokens = len(storage._strict_json(MESSAGES).encode()) + MAX_OUTPUT_TOKENS
    overrides = pricing.get('overrides', [])
    if type(overrides) is not list or any(type(row) is not dict or row.keys() - (UNITS.keys() | {'min_prompt_tokens'})
            or type(row.get('min_prompt_tokens')) is not int or row['min_prompt_tokens'] < 0 for row in overrides):
        raise ValueError('Tarification conditionnelle non prise en charge')
    quantities = {'prompt': input_tokens, 'completion': MAX_OUTPUT_TOKENS,
                  'request': 1, 'internal_reasoning': MAX_OUTPUT_TOKENS}
    forecast = price_row({'request': '0', 'internal_reasoning': '0', **pricing}, quantities)
    reserve = forecast['forecast']['token_subtotal_usd']
    if reserve is None:
        raise ValueError('Réserve non estimable')
    metadata.update(input_price_per_million=model_catalogue._million_price(pricing['prompt']),
                    output_price_per_million=model_catalogue._million_price(pricing['completion']),
                    context_length=endpoint['context_length'],
                    max_output_tokens=endpoint['max_completion_tokens'])
    canonical = model.get('canonical_slug')
    return dict(model=slug, revision=slug, provider='OpenRouter', channel_id=ENDPOINT,
        parameters=parameters, metadata=metadata, canonical_slug=canonical,
        metadata_at=model_catalogue._now().isoformat(), reserve_usd=reserve)


def request_id(store, session_id, dossier_id, body):
    storage._fields(body, ('slug', 'action_id'), 'model probe')
    slug = body['slug'].strip() if type(body['slug']) is str else ''
    if (model_catalogue._model_id({'id': slug}) is None or len(slug) > 256
            or slug.startswith('openrouter/')):
        raise p.Denied('PROBE_SLUG_INVALID')
    identifier(body['action_id'])
    require_current({'model': slug})
    p.owner(p.connection_for(store), session_id, dossier_id)
    return sha256(storage._strict_json([session_id, dossier_id, body['action_id']]).encode()).hexdigest()


def submit(store, session_id, dossier_id, body, fetch, secret, access_transport=None):
    operation_id = request_id(store, session_id, dossier_id, body)
    slug = body['slug'].strip()
    connection = p.connection_for(store)
    revision = p.owner(connection, session_id, dossier_id)
    binding = _key_binding(connection, session_id)
    for record in _records(store, session_id, dossier_id):
        if record['operation_id'] == operation_id:
            if record['requested_configuration']['model'] != slug:
                raise storage.ConflictError('Identité déjà utilisée avec un autre slug')
            return operation_id, None
        if record['requested_configuration']['model'] == slug and _valid(record, binding):
            return record['operation_id'], None
    if not callable(fetch) or secret is None:
        raise p.Denied('PROBE_UNAVAILABLE')
    if not p.admission(store, connection) or os.path.lexists(store._root / 'restore.json'):
        raise p.Denied('PROBE_CLOSED')
    key = provider_access.key_for_session(store, session_id, secret, access_transport)
    binding = _key_binding(connection, session_id)
    try:
        config = _metadata(slug, fetch)
    except (OSError, HTTPException, KeyError, TypeError, ValueError) as error:
        raise p.Denied('PROBE_MODEL_UNAVAILABLE') from error
    config['key_binding'] = binding
    wire = storage._strict_json({'model': slug, 'messages': MESSAGES, **config['parameters']})
    with storage._transaction(connection, write=True):
        if (p.owner(connection, session_id, dossier_id) != revision or
                _key_binding(connection, session_id) != binding or not p.admission(store, connection)
                or os.path.lexists(store._root / 'restore.json')):
            raise p.Denied('PROBE_CLOSED')
        if connection.execute("SELECT 1 FROM operations WHERE phase IN ('preparation','correction','qualification') "
                              "AND state!='RECEIVED'").fetchone():
            raise p.Denied('PREPARATION_IN_PROGRESS')
        if (p._daily_preparation_reserved(connection, p._now()) + storage._money(config['reserve_usd'])
                > p.PREPARATION_DAILY_CAP_USD):
            raise p.Denied('DAILY_CAP')
        operation = dict(operation_id=operation_id, phase='preparation', dossier_id=dossier_id,
            revision=revision, authority='requester-model-probe:' + body['action_id'], engine_version=ENGINE,
            requested_configuration=config, resources=[wire])
        store._reserve_intent(connection, operation, provider_access.preparation_budget_id(session_id),
                              config['reserve_usd'])
        # Aucune intention isolée ne survit à un refus avant la frontière d'émission
        connection.execute("UPDATE operations SET state='EMISSION_POSSIBLE' WHERE operation_id=?", (operation_id,))
    return operation_id, key


def run(data, session_id, dossier_id, body, fetch, secret, access_transport=None, transport=None):
    """Métadonnées gratuites puis appel réservé dans le worker de l'exécuteur"""
    with closing(storage.Store(data)) as store:
        operation_id, key = submit(store, session_id, dossier_id, body, fetch, secret, access_transport)
    if key is not None:
        execute(data, operation_id, key, transport)
    return operation_id


def execute(data, operation_id, key, transport=None):
    transport = post if transport is None else transport
    with closing(storage.Store(data)) as store:
        operation = store._operations(store._connection_checked(), operation_ids={operation_id})[0]
        if operation['engine_version'] != ENGINE or operation['state'] != 'EMISSION_POSSIBLE':
            return
        config = operation['requested_configuration']
        connection = p.connection_for(store)
        session_id = connection.execute('SELECT session_id FROM s2_dossiers WHERE dossier_id=?',
                                        (operation['dossier_id'],)).fetchone()[0]
        if (not p.admission(store, connection) or os.path.lexists(store._root / 'restore.json')
                or _key_binding(connection, session_id) != config['key_binding']):
            store.record_receipt(operation_id, dict(receipt_id='probe-' + operation_id,
                observed_configuration=None, resources_seen=[], result={'status': 'NOT_SENT'}),
                dict(status='KNOWN', amount='0', currency='USD', source='Local control: transport not entered'))
            return
        try:
            status, headers, raw, complete, started, _ = transport(
                key, operation['resources'][0], max_response_bytes=65536)
        except Exception:
            store.mark_ambiguous(operation_id, 'MODEL_PROBE_INTERRUPTED_NO_RETRY')
            return
        sensitive = key.encode() in raw
        raw = raw.replace(key.encode(), b'[REDACTED]')
        try:
            document = json.loads(raw, object_pairs_hook=storage._unique_object)
            if key in storage._strict_json(document):
                sensitive = True
                document = None
        except (ValueError, UnicodeError, RecursionError):
            document = None
        charge = consumption(document, complete=complete)
        choices = document.get('choices') if type(document) is dict else None
        choice = choices[0] if type(choices) is list and len(choices) == 1 and type(choices[0]) is dict else {}
        message = choice.get('message') if type(choice.get('message')) is dict else {}
        observed = document.get('model') if type(document) is dict else None
        content = message.get('content')
        success = (status == 200 and complete and not sensitive and type(document) is dict
                   and not document.get('error') and type(observed) is str
                   and observed in (config['model'], config['canonical_slug'])
                   and choice.get('finish_reason') == 'stop' and not message.get('refusal')
                   and type(content) is str and content.strip().casefold() in ('ok', 'ok.'))
        result = {'status': 'RESPONDED' if success else 'UNCONFIRMED',
                  'http_status': status, 'complete': complete, 'response': document,
                  'received_at': datetime.now(timezone.utc).isoformat(), 'started_at': started}
        store.record_receipt(operation_id, dict(receipt_id='probe-' + operation_id,
            observed_configuration=None if observed is None else {'model': observed},
            resources_seen=operation['resources'], result=result),
            dict(status='KNOWN' if charge['amount'] is not None else 'UNKNOWN',
                 amount=charge['amount'], currency='USD', source=charge['source']))
