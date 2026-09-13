"""Single OpenRouter preparation transport, reported cost, no tools or retries"""
from base64 import b64encode
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from http.client import HTTPSConnection, IncompleteRead
import json
from pathlib import Path
import re
import time

from .storage import _strict_json as encode, _unique_object, _money
from . import openrouter_prices, outgoing


ASSISTANT = 'glm-5.3-flash'
HOST = 'openrouter.ai'
PATH = '/api/v1/chat/completions'
ENDPOINT = 'https://' + HOST + PATH
USAGE_METHOD = {
    'field': '/usage/cost', 'currency': 'USD',
    'scope': 'Amount charged to the OpenRouter account; not upstream_inference_cost or a final invoice',
    'sources': ['https://openrouter.ai/docs/cookbook/administration/usage-accounting',
                'https://openrouter.ai/docs/faq'],
}
HISTORICAL_PROFILE_NAME = 'glm-5.3-flash.profile.json'
MAX_PROFILE_BYTES = 65536
MAX_REQUEST_BYTES = 65536
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 120
PROFILE_FIELDS = ('profile_id', 'model', 'revision', 'parameters', 'routes',
                  'required_capabilities', 'system', 'max_request_bytes', 'max_response_bytes',
                  'timeout_seconds')
PARAMETER_FIELDS = ('temperature', 'top_p', 'reasoning', 'provider', 'max_tokens', 'stream',
                    'response_format')
REQUIRED_PARAMETERS = ('provider', 'max_tokens', 'stream')
PROVIDER_FIELDS = ('only', 'order', 'allow_fallbacks', 'require_parameters')
ROUTE_FIELDS = ('tag', 'provider_name')
CAPABILITY_PARAMETERS = ('temperature', 'top_p', 'reasoning', 'max_tokens', 'response_format')
MODEL_ID = r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+'
PROFILE_ID = r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}'
ROUTE_TAG = r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}'
THREE_ROUTE_TEXT = 'Native OpenRouter fallback within the three explicit endpoint slugs, in configured order'
ROUTE_TEXT = 'Native OpenRouter fallback within the explicit endpoint slugs, in configured order'


def _bounded_int(value, minimum, maximum):
    if type(value) is not int or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError('Profil de préparation invalide')
    return value


def _plain_text(value, pattern, maximum):
    if (type(value) is not str or not value or len(value) > maximum
            or any(ord(character) < 32 for character in value)
            or re.fullmatch(pattern, value) is None):
        raise ValueError('Profil de préparation invalide')
    return value


def _finite_number(value, minimum, maximum):
    if type(value) is bool or type(value) not in (int, float) or not minimum <= value <= maximum:
        raise ValueError('Profil de préparation invalide')
    if type(value) is float and value != value:
        raise ValueError('Profil de préparation invalide')
    return value


def _unique_texts(values, pattern, maximum, count_min, count_max):
    if type(values) is not list or not count_min <= len(values) <= count_max:
        raise ValueError('Profil de préparation invalide')
    result = [_plain_text(value, pattern, maximum) for value in values]
    if len(set(result)) != len(result):
        raise ValueError('Profil de préparation invalide')
    return result


def _accepted_revision(frozen, estimate):
    canonical = estimate.get('canonical_slug')
    if type(canonical) is str and canonical:
        if canonical != frozen['revision']:
            raise ValueError('Révision OpenRouter divergente')
    elif frozen['revision'] != frozen['model']:
        raise ValueError('Révision OpenRouter divergente')


def _model_identities(frozen):
    if frozen['revision'] == frozen['model']:
        return [frozen['model']]
    return [frozen['model'], frozen['revision']]


def _validated_profile(document):
    if type(document) is not dict or set(document) != set(PROFILE_FIELDS):
        raise ValueError('Champ de profil inconnu' if type(document) is dict and set(document) - set(PROFILE_FIELDS)
                         else 'Profil de préparation invalide')
    model = _plain_text(document['model'], MODEL_ID, 256)
    revision = _plain_text(document['revision'], MODEL_ID, 256)
    parameters = document['parameters']
    if (type(parameters) is not dict or set(parameters) - set(PARAMETER_FIELDS)
            or not set(REQUIRED_PARAMETERS) <= set(parameters)):
        raise ValueError('Profil de préparation invalide')
    provider = parameters['provider']
    if type(provider) is not dict or set(provider) != set(PROVIDER_FIELDS):
        raise ValueError('Profil de préparation invalide')
    if parameters['stream'] is not False or provider['require_parameters'] is not True:
        raise ValueError('Profil de préparation invalide')
    copied_parameters = {}
    if 'temperature' in parameters:
        copied_parameters['temperature'] = _finite_number(parameters['temperature'], 0, 2)
    if 'top_p' in parameters:
        copied_parameters['top_p'] = _finite_number(parameters['top_p'], 0, 1)
    if 'reasoning' in parameters:
        reasoning = parameters['reasoning']
        if (type(reasoning) is not dict or set(reasoning) != {'effort'}
                or type(reasoning['effort']) is not str or not reasoning['effort']
                or any(ord(character) < 32 for character in reasoning['effort'])
                or len(reasoning['effort']) > 32):
            raise ValueError('Profil de préparation invalide')
        copied_parameters['reasoning'] = dict(effort=reasoning['effort'])
    if type(provider['allow_fallbacks']) is not bool:
        raise ValueError('Profil de préparation invalide')
    routes = document['routes']
    if type(routes) is not list or not 1 <= len(routes) <= 3:
        raise ValueError('Profil de préparation invalide')
    copied_routes = []
    tags = []
    names = []
    for route in routes:
        if type(route) is not dict or set(route) != set(ROUTE_FIELDS):
            raise ValueError('Profil de préparation invalide')
        tag = _plain_text(route['tag'], ROUTE_TAG, 128)
        name = route['provider_name']
        if (type(name) is not str or not name or len(name) > 128
                or any(ord(character) < 32 for character in name)):
            raise ValueError('Profil de préparation invalide')
        if tag in tags or name in names:
            raise ValueError('Profil de préparation invalide')
        tags.append(tag)
        names.append(name)
        copied_routes.append(dict(tag=tag, provider_name=name))
    only = provider['only']
    order = provider['order']
    if type(only) is not list or type(order) is not list or only != tags or order != tags:
        raise ValueError('Profil de préparation invalide')
    copied_parameters['provider'] = dict(
        only=list(tags), order=list(tags),
        allow_fallbacks=provider['allow_fallbacks'], require_parameters=True)
    copied_parameters['max_tokens'] = _bounded_int(parameters['max_tokens'], 1, 128000)
    copied_parameters['stream'] = False
    if 'response_format' in parameters:
        response_format = parameters['response_format']
        if type(response_format) is not dict or response_format != {'type': 'json_object'}:
            raise ValueError('Profil de préparation invalide')
        copied_parameters['response_format'] = dict(type='json_object')
    expected_capabilities = [name for name in CAPABILITY_PARAMETERS if name in parameters]
    capabilities = _unique_texts(
        document['required_capabilities'], r'[A-Za-z0-9_]+', 64,
        len(expected_capabilities), len(expected_capabilities))
    if set(capabilities) != set(expected_capabilities):
        raise ValueError('Profil de préparation invalide')
    system = document['system']
    if type(system) is not str or not system.strip() or len(system.encode()) > 65536 or '\0' in system:
        raise ValueError('Profil de préparation invalide')
    value = dict(
        profile_id=_plain_text(document['profile_id'], PROFILE_ID, 64),
        model=model, revision=revision, parameters=copied_parameters,
        routes=copied_routes, required_capabilities=expected_capabilities, system=system,
        max_request_bytes=_bounded_int(document['max_request_bytes'], 1, MAX_REQUEST_BYTES),
        max_response_bytes=_bounded_int(document['max_response_bytes'], 1, MAX_RESPONSE_BYTES),
        timeout_seconds=_bounded_int(document['timeout_seconds'], 1, TIMEOUT_SECONDS))
    encode(value)
    return json.loads(encode(value), object_pairs_hook=_unique_object)


def _load_profile_file(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError('Profil de préparation introuvable')
    raw = path.read_bytes()
    if len(raw) > MAX_PROFILE_BYTES:
        raise ValueError('Profil de préparation hors limites')
    document = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
    return _validated_profile(document)


def load_profile(source):
    if source == ASSISTANT:
        return deepcopy(HISTORICAL_PROFILE)
    if type(source) is not str or not source:
        raise ValueError('Profil de préparation invalide')
    return _load_profile_file(source)


def frozen_profile(profile=None):
    if profile is None:
        return deepcopy(HISTORICAL_PROFILE)
    if type(profile) is dict:
        return _validated_profile(profile)
    if type(profile) is str:
        return load_profile(profile)
    raise ValueError('Profil de préparation invalide')


def profile_digest(profile):
    return sha256(encode(profile).encode()).hexdigest()


def providers(profile):
    return {route['tag']: route['provider_name'] for route in profile['routes']}


HISTORICAL_PROFILE = _load_profile_file(Path(__file__).with_name(HISTORICAL_PROFILE_NAME))
MODEL = HISTORICAL_PROFILE['model']
PROVIDERS = providers(HISTORICAL_PROFILE)
PARAMETERS = deepcopy(HISTORICAL_PROFILE['parameters'])
SYSTEM_PROMPT = HISTORICAL_PROFILE['system']


def reservation(estimate, profile=None):
    frozen = frozen_profile(profile)
    named = providers(frozen)
    if (type(estimate) is not dict or estimate.get('channel') != 'OpenRouter'
            or estimate.get('model_id') != frozen['model']):
        raise ValueError('Relevé OpenRouter du modèle exact requis')
    _accepted_revision(frozen, estimate)
    assumptions = estimate['assumptions']
    context = estimate['context_length']
    if (type(context) is not int or context <= 0 or assumptions['input_tokens'] < context
            or assumptions['cached_input_tokens'] != 0
            or assumptions['output_tokens'] != frozen['parameters']['max_tokens']):
        raise ValueError('Prévision sur contexte complet, sans économie de cache, et sortie configurée requise')
    for key, path in (('model', '/api/v1/model/'), ('endpoints', '/api/v1/models/')):
        source = estimate['sources'][key]
        expected = 'https://' + HOST + path + frozen['model'] + ('/endpoints' if key == 'endpoints' else '')
        if (source['url'] != expected or re.fullmatch('[0-9a-f]{64}', source['body_sha256']) is None
                or not datetime.fromisoformat(source['retrieved_at']).tzinfo):
            raise ValueError('Source datée du relevé requise')
    seen = set()
    for endpoint in estimate['endpoints']:
        if endpoint['tag'] not in named:
            continue
        if endpoint['tag'] in seen or endpoint['provider_name'] != named[endpoint['tag']]:
            raise ValueError('Endpoint autorisé absent ou répété')
        seen.add(endpoint['tag'])
        if not set(frozen['required_capabilities']) <= set(endpoint['supported_parameters']):
            raise ValueError('Paramètres requis non annoncés par cet endpoint')
        if endpoint['model_id'] != frozen['model']:
            raise ValueError('Identité endpoint requise')
    if seen != named.keys():
        raise ValueError('Relevé des endpoints autorisés requis')
    indication = openrouter_prices.indication(estimate, {
        'prompt_tokens': assumptions['input_tokens'], 'completion_tokens': assumptions['output_tokens']})
    return None if indication is None else indication['token_subtotal_usd']


def configuration(estimate=None, profile=None):
    frozen = frozen_profile(profile)
    tags = [route['tag'] for route in frozen['routes']]
    value = {'provider': 'OpenRouter', 'model': frozen['model'], 'access': 'API', 'endpoint': ENDPOINT,
             'route': THREE_ROUTE_TEXT if tags == ['modal/fp8', 'coreweave/fp8', 'novita/fp8'] else ROUTE_TEXT,
             'reserve_basis': 'Indicative model token reference; explicit admission reserve remains counted, no invoice cap',
             'outgoing_format': outgoing.FORMAT, 'parameters': deepcopy(frozen['parameters']),
             'prompt_sha256': sha256(frozen['system'].encode()).hexdigest(),
             'max_request_bytes': frozen['max_request_bytes'],
             'max_response_bytes': frozen['max_response_bytes'],
             'timeout_seconds': frozen['timeout_seconds'], 'cost_method': deepcopy(USAGE_METHOD),
             'profile_id': frozen['profile_id'], 'profile_sha256': profile_digest(frozen),
             'revision': frozen['revision'], 'model_identities': _model_identities(frozen),
             'routes': deepcopy(frozen['routes'])}
    if estimate is not None:
        value['reservation_estimate'] = deepcopy(estimate)
        value['reserve_usd'] = reservation(estimate, frozen)
        _accepted_revision(frozen, estimate)
    return value


def consumption(document, *, complete=True):
    usage = document.get('usage') if type(document) is dict else None
    value = usage.get('cost') if type(usage) is dict else None
    amount = None
    if complete and type(value) in (str, int, float):
        try:
            amount = str(_money(str(value)))
        except ValueError:
            pass
    return {'usage': usage, 'amount': amount, 'currency': 'USD',
            'status': 'REPORTED' if amount is not None else 'UNKNOWN',
            'source': 'HTTP response JSON /usage/cost', 'method': deepcopy(USAGE_METHOD), 'invoice': False}


def post(api_key, wire, timeout=TIMEOUT_SECONDS, max_response_bytes=MAX_RESPONSE_BYTES):
    """One OpenRouter HTTP exchange; response bytes retained, no automatic retry"""
    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    connection = HTTPSConnection(HOST, timeout=timeout)
    try:
        connection.request('POST', PATH, body=wire.encode(), headers={
            'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json',
            'X-OpenRouter-Metadata': 'enabled'})
        response = connection.getresponse()
        status = response.status
        safe_headers = {}
        for name, pattern in (('X-Generation-Id', r'gen-[A-Za-z0-9_-]{1,200}'),
                              ('Retry-After', r'[0-9]{1,10}|[A-Za-z]{3}, [0-9]{2} [A-Za-z]{3} [0-9]{4} [0-9:]{8} GMT')):
            value = response.getheader(name)
            if type(value) is str and re.fullmatch(pattern, value) and api_key not in value:
                safe_headers[name] = value
        try:
            raw = response.read(max_response_bytes + 1)
            complete = response.length in (None, 0)
        except IncompleteRead as error:
            raw, complete = error.partial, False
        if len(raw) > max_response_bytes:
            raw, complete = raw[:max_response_bytes], False
    finally:
        connection.close()
    return status, safe_headers, raw, complete, started, clock


class OpenRouterPreparation:
    phases = ('preparation', 'correction')

    def content(self, request):
        return outgoing.closed_preparation(request['outgoing'])

    def validate_document(self, document):
        pass

    def validate_answer(self, result, message):
        return result

    def __init__(self, api_key, profile=None):
        if (type(api_key) is not str or not api_key or not api_key.isascii()
                or any(character.isspace() or ord(character) < 32 for character in api_key)):
            raise ValueError('Clé OpenRouter explicite requise côté exécuteur')
        self._api_key = api_key
        self._profile = frozen_profile(profile)

    def prepare(self, operation, request):
        requested = operation['requested_configuration']
        expected = configuration(requested.get('reservation_estimate'), self._profile)
        if ('reserve_usd' not in expected or requested != expected
                or operation['phase'] not in self.phases):
            raise ValueError('Configuration ou réservation OpenRouter divergente')
        if request.get('outgoing_format') != outgoing.FORMAT:
            raise ValueError('Ancien format sortant : nouvelle préparation requise')
        content = self.content(request)
        wire = encode({'model': self._profile['model'], **self._profile['parameters'], 'messages': [
            {'role': 'system', 'content': self._profile['system']}, {'role': 'user', 'content': encode(content)}]})
        if len(wire.encode()) > self._profile['max_request_bytes'] or self._api_key in wire:
            raise ValueError('Requête hors limites')
        self._wire = wire
        self._wire_sha256 = sha256(wire.encode()).hexdigest()
        return wire

    def __call__(self, operation, request):
        if operation['state'] != 'EMISSION_POSSIBLE':
            raise ValueError('Intention HTTP persistée requise')
        if operation['requested_configuration'].get('outgoing_format') != outgoing.FORMAT:
            raise ValueError('Ancienne intention : nouvelle préparation requise')
        wire = operation.get('conserved_wire')
        if (type(wire) is not str or getattr(self, '_wire_sha256', None) is None
                or sha256(wire.encode()).hexdigest() != self._wire_sha256 or self._api_key in wire):
            raise ValueError('Corps préparé divergent')
        expected_models = operation['requested_configuration']['model_identities']
        named_providers = {row['provider_name'] for row in operation['requested_configuration']['reservation_estimate']['endpoints']}
        authorized = providers(self._profile)
        status, safe_headers, raw, complete, started, clock = post(
            self._api_key, wire, timeout=self._profile['timeout_seconds'],
            max_response_bytes=self._profile['max_response_bytes'])
        # A reflected credential cannot enter private receipts either
        redacted = self._api_key.encode() in raw
        if redacted:
            raw = raw.replace(self._api_key.encode(), b'[REDACTED_CREDENTIAL]')
        document, result, incident = None, None, 'UNUSABLE_RESPONSE'
        try:
            parsed = json.loads(raw, object_pairs_hook=_unique_object, parse_float=str)
            encode(parsed)
            document = parsed
            if self._api_key in encode(document):
                redacted = True
            if status != 200 or not complete or redacted or document.get('model') not in expected_models:
                raise ValueError('Réponse non attribuable')
            self.validate_document(document)
            route = document.get('openrouter_metadata')
            if type(route) is dict:
                if 'requested' in route and route['requested'] != self._profile['model']:
                    raise ValueError('Modèle demandé rapporté divergent')
                attempted = route.get('attempts', [])
                endpoints = route.get('endpoints', {})
                selected = endpoints.get('available', []) if type(endpoints) is dict else []
                rows = attempted if type(attempted) is list else []
                if type(selected) is list:
                    rows = rows + [row for row in selected if type(row) is dict and row.get('selected') is True]
                for row in rows:
                    if type(row) is dict and (('provider' in row and row['provider'] is not None and row['provider'] in named_providers and row['provider'] not in authorized.values())
                            or ('model' in row and row['model'] is not None and row['model'] not in expected_models)
                            or ('tag' in row and row['tag'] is not None and row['tag'] not in authorized)):
                        raise ValueError('Fournisseur, endpoint ou modèle rapporté hors autorisation')
            if safe_headers.get('X-Generation-Id') and document.get('id') and document['id'] != safe_headers['X-Generation-Id']:
                raise ValueError('Identifiants de génération divergents')
            choices = document['choices']
            if type(choices) is not list or len(choices) != 1:
                raise ValueError('Choix unique requis')
            choice = choices[0]
            message = choice['message']
            if choice['finish_reason'] != 'stop' or message.get('tool_calls') or message.get('role') != 'assistant':
                raise ValueError('Réponse incomplète ou appel outil')
            result = json.loads(message['content'], object_pairs_hook=_unique_object)
            encode(result)
            if self._api_key in encode(result):
                redacted = True
                raise ValueError('Réponse confidentielle')
            if type(result) is dict and operation['phase'] != 'judgment':
                result = {key: value for key, value in result.items() if value is not None or key in (
                    'stage', 'explanation', 'reformulation', 'fictional_parameters', 'package')}
            result = self.validate_answer(result, message)
            incident = None
        except (ValueError, TypeError, KeyError, AttributeError):
            result = None
        if redacted:
            raw, document = b'[REDACTED_CREDENTIAL]', None
        measured = consumption(document, complete=complete and status == 200 and not redacted)
        amount = measured['amount']
        model = document.get('model') if type(document) is dict else None
        route = document.get('openrouter_metadata') if type(document) is dict else None
        selected = route.get('endpoints', {}).get('available', []) if type(route) is dict and type(route.get('endpoints')) is dict else []
        found = [row.get('provider') for row in selected if type(row) is dict and row.get('selected') is True] if type(selected) is list else []
        provider = found[0] if len(found) == 1 and type(found[0]) is str and found[0] in authorized.values() else None
        observed = {'outgoing': outgoing.wire_proof(wire, json.loads(wire)['messages']), 'model': model if type(model) is str else None, 'revision': None,
                    'generation_id': (document.get('id') if type(document) is dict else None) or safe_headers.get('X-Generation-Id'),
                    'provider': provider, 'route': route if type(route) is dict else None,
                    'routing_limit': ('Reported internal attempt exceeds the number of permitted providers; no internal retry cap is documented'
                                      if type(route) is dict and type(route.get('attempt')) is int and route['attempt'] > len(authorized) else None),
                    'parameters': None, 'reasoning_effort': None,
                    'sources': {'model': 'HTTP response JSON /model' if type(model) is str else None,
                                'route': 'HTTP response JSON /openrouter_metadata' if type(route) is dict else None,
                                'provider': 'HTTP response JSON /openrouter_metadata/endpoints/available selected' if provider else None},
                    'http': {'endpoint': ENDPOINT, 'status': status, 'response_headers': safe_headers, 'started_at': started,
                             'received_at': datetime.now(timezone.utc).isoformat(),
                             'elapsed_seconds': time.monotonic() - clock, 'complete': complete,
                             'credential_redacted': redacted, 'body_base64': b64encode(raw).decode(),
                             'body_sha256': sha256(raw).hexdigest()},
                    'consumption': measured, 'incident': incident}
        return {'receipt': {'receipt_id': 'openrouter-' + operation['operation_id'],
                            'observed_configuration': observed, 'resources_seen': [wire], 'result': result},
                'cost': {'status': 'KNOWN' if amount is not None else 'UNKNOWN', 'amount': amount, 'currency': 'USD',
                         'source': ('Montant débité rapporté par OpenRouter /usage/cost, en USD ; hors facture finale'
                                    if amount is not None else 'Usage ou attribution incomplets ; coût INCONNU')}}
