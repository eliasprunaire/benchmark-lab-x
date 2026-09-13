"""Public OpenRouter prices for operator forecasts, never observed expenditure."""
from datetime import datetime, timezone
from decimal import localcontext
from hashlib import sha256
from http.client import HTTPSConnection, HTTPException
import json
import re

from .storage import _money, _sum_money, _unique_object, _strict_json as encode


HOST = 'openrouter.ai'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
UNITS = {'prompt': 'USD/input_token', 'completion': 'USD/output_token',
         'input_cache_read': 'USD/cached_input_token', 'input_cache_write': 'USD/cache_write_token',
         'request': 'USD/request', 'internal_reasoning': 'USD/reasoning_token',
         'image': 'USD/image', 'web_search': 'USD/search'}
DISCOUNT_SOURCE = 'https://github.com/OpenRouterTeam/terraform-provider-openrouter/blob/main/docs/data-sources/model.md#nested-schema-for-datapricing'


def read_public(path):
    connection = HTTPSConnection(HOST, timeout=20)
    try:
        connection.request('GET', path, headers={'Accept': 'application/json'})
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if response.status != 200 or len(raw) > MAX_RESPONSE_BYTES or response.length not in (None, 0):
            raise ValueError('Métadonnées OpenRouter non vérifiées')
    except HTTPException as error:
        raise ValueError('Réponse OpenRouter incomplète') from error
    finally:
        connection.close()
    document = json.loads(raw, object_pairs_hook=_unique_object)
    encode(document)
    if type(document) is not dict or type(document.get('data')) is not dict:
        raise ValueError('Objet modèle requis')
    return document['data'], {'url': 'https://' + HOST + path,
                              'retrieved_at': datetime.now(timezone.utc).isoformat(),
                              'body_sha256': sha256(raw).hexdigest()}


def price_row(pricing, quantities):
    if pricing is None:
        pricing = {}
    if type(pricing) is not dict:
        raise ValueError('Tarifs invalides')
    rates = {key: {'amount': None if pricing.get(key) is None else str(_money(pricing[key])), 'unit': unit}
             for key, unit in UNITS.items()}
    conditional = bool(pricing.get('overrides')) or bool(pricing.keys() - (UNITS.keys() | {'overrides', 'discount'}))
    components = {}
    for key, count in quantities.items():
        rate = rates[key]['amount']
        amount = None
        if not conditional:
            if count == 0:
                amount = '0'
            elif rate is not None:
                value = _money(rate)
                with localcontext() as context:
                    context.prec = len(value.as_tuple().digits) + len(str(count)) + 1
                    amount = str(value * count)
        components[key] = amount
    subtotal = None
    if all(value is not None for value in components.values()):
        subtotal = str(_sum_money(_money(value) for value in components.values()))
    return {'pricing_raw': pricing, 'rates': rates,
            'forecast': {'kind': 'INDICATIVE_TOKEN_SUBTOTAL', 'currency': 'USD',
                         'price_basis': 'API_VALUES_BEFORE_DISCOUNT_APPLICATION',
                         'discount_unresolved': not (type(pricing.get('discount')) in (int, float) and pricing['discount'] == 0),
                         'discount_formula': 'price * (1 - discount)', 'discount_source': DISCOUNT_SOURCE,
                         'components_usd': components, 'token_subtotal_usd': subtotal,
                         'total_usd': None,
                         'conditional_pricing_unresolved': conditional}}


def indication(estimate, usage):
    """Model token indication, distinct from any reported account charge"""
    try:
        quantities = {'prompt': usage['prompt_tokens'], 'completion': usage['completion_tokens']}
        if any(type(value) is not int or value < 0 for value in quantities.values()):
            return None
        pricing = estimate['model_summary']['pricing_raw']
        for key in quantities:
            _money(pricing[key])
        forecast = price_row({key: pricing[key] for key in quantities}, quantities)['forecast']
        return {**forecast, 'quantities': quantities, 'model_id': estimate['model_id'],
                'source': estimate['sources']['model']}
    except (ValueError, TypeError, KeyError):
        return None


def forecast(model, input_tokens, output_tokens, cached_input_tokens=0):
    from .model_catalog import require_current
    require_current(dict(model=model))
    if (type(model) is not str or re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', model) is None
            or any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens, cached_input_tokens))
            or cached_input_tokens > input_tokens):
        raise ValueError('Modèle exact et quantités de tokens cohérentes requis')
    summary, summary_source = read_public('/api/v1/model/' + model)
    if summary.get('id') != model:
        raise ValueError('Identité modèle divergente')
    detail, endpoint_source = read_public('/api/v1/models/' + model + '/endpoints')
    if detail.get('id') != model or type(detail.get('endpoints')) is not list:
        raise ValueError('Endpoints du modèle non vérifiés')
    quantities = {'prompt': input_tokens - cached_input_tokens,
                  'input_cache_read': cached_input_tokens, 'completion': output_tokens}
    endpoints = []
    for endpoint in detail['endpoints']:
        if type(endpoint) is not dict or endpoint.get('model_id') != model:
            raise ValueError('Identité endpoint divergente')
        row = {key: endpoint.get(key) for key in ('model_id', 'provider_name', 'tag', 'name', 'quantization', 'status', 'supported_parameters')}
        if any(type(row[key]) is not str or not row[key] for key in ('provider_name', 'tag')):
            raise ValueError('Identité fournisseur requise')
        row.update(price_row(endpoint.get('pricing'), quantities))
        endpoints.append(row)
    return {'kind': 'OPENROUTER_INDICATIVE_FORECAST', 'channel': 'OpenRouter',
            'model_id': model, 'context_length': summary.get('context_length'), 'canonical_slug': summary.get('canonical_slug'),
            'sources': {'model': summary_source, 'endpoints': endpoint_source},
            'model_summary': {'scope': 'TOP_PROVIDER_UNIDENTIFIED_NOT_ALL_ENDPOINTS',
                              'pricing_raw': summary.get('pricing')},
            'assumptions': {'input_tokens': input_tokens, 'cached_input_tokens': cached_input_tokens,
                            'output_tokens': output_tokens, 'requests': 1,
                            'output_scope': 'All output tokens assumed by operator, including reasoning',
                            'exclusions': 'Request fees, extra reasoning charges, cache writes, tools, media, taxes and funding fees',
                            'zero_components': 'Only an explicitly zero token quantity implies a zero component',
                            'applicability': 'Indicative OpenRouter reference; not observed expenditure or admission'},
            'endpoints': endpoints}
