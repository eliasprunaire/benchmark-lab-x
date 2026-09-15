"""Relevé filtré des modèles OpenRouter, sans sélection de campagne implicite"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import localcontext
from http.client import HTTPException
import argparse
import json
from pathlib import Path
import re
import tomllib

from . import openrouter_prices, storage


MAX_RESPONSE_BYTES = 4 * 1024 * 1024
TABLE_SQL = """CREATE TABLE s2_model_catalogue (
    fetched_at TEXT NOT NULL,
    raw_json TEXT NOT NULL
)"""
CONFIG_PATH = Path(__file__).resolve().parents[1] / 'models.toml'
MODEL_ID = re.compile(r'^[a-z0-9.-]+/[a-z0-9.:\_-]+$')


def schema_objects():
    return [('table', 's2_model_catalogue', 's2_model_catalogue', TABLE_SQL)]


def _now():
    return datetime.now(timezone.utc)


def family(model_id):
    """Regroupe les révisions et versions d'une même lignée de modèle"""
    base = model_id.split(':', 1)[0]
    base = re.sub(r'-(?:\d{4}|\d{8})$', '', base)
    return re.sub(r'\d+(?:\.\d+)*', '#', base)


def _registry(path=CONFIG_PATH):
    return tomllib.loads(path.read_text(encoding='utf-8'))


def _settings(registry):
    value = registry.get('catalogue')
    required = {'makers', 'max_per_family', 'max_age_days', 'cache_hours'}
    optional = {'baseline_families', 'baseline_models'}
    if (type(value) is not dict or not required <= value.keys()
            or value.keys() - required - optional):
        raise ValueError('Configuration [catalogue] incomplète')
    if (type(value['makers']) is not list or not value['makers']
            or any(type(item) is not str or not item for item in value['makers'])
            or any(type(value[key]) is not int or value[key] <= 0
                   for key in ('max_per_family', 'max_age_days', 'cache_hours'))):
        raise ValueError('Paramètres du catalogue invalides')
    return value


def _data(document, expected):
    storage._strict_json(document)
    if type(document) is dict and 'data' in document:
        document = document['data']
    if type(document) is not expected:
        raise ValueError('Réponse OpenRouter invalide')
    return document


def _model_id(model):
    if type(model) is not dict or type(model.get('id')) is not str:
        return None
    return model['id'] if MODEL_ID.fullmatch(model['id']) else None


def _malformed(model):
    return (type(model.get('created')) is not int
            or type(model.get('name')) is not str
            or type(model.get('architecture')) is not dict)


def _candidates(models, settings, now):
    grouped = defaultdict(list)
    for model in models:
        model_id = _model_id(model)
        if model_id is None or model_id.split('/', 1)[0] not in settings['makers']:
            continue
        if (_malformed(model) or model_id.endswith(':batch')
                or model['architecture'].get('output_modalities') != ['text']):
            continue
        grouped[family(model_id)].append(model)
    cutoff = int((now - timedelta(days=settings['max_age_days'])).timestamp())
    selected = []
    for models_in_family in grouped.values():
        models_in_family.sort(key=lambda item: (-item['created'], item['id']))
        if models_in_family[0]['created'] >= cutoff:
            selected.extend(models_in_family[:settings['max_per_family']])
    return sorted(selected, key=lambda item: (family(item['id']), -item['created'], item['id']))


def _registered_routes(registry):
    routes = defaultdict(set)
    for alias, value in registry.items():
        if alias == 'catalogue' or type(value) is not dict:
            continue
        model, provider = value.get('model'), value.get('provider')
        if (type(model) is str and type(provider) is str
                and value.get('catalogue_compliant', True) is True):
            routes[model].add(provider)
    return routes


def _compliant(model_id, endpoints, routes):
    allowed = routes.get(model_id, ())
    for endpoint in endpoints:
        if type(endpoint) is not dict or endpoint.get('model_id') != model_id:
            raise ValueError('Identité endpoint divergente')
        tag = endpoint.get('tag')
        if type(tag) is str and tag in allowed:
            return True
    return False


def _latest(store):
    connection = store._connection_checked()
    if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='s2_model_catalogue'").fetchone():
        return None
    row = connection.execute(
        'SELECT fetched_at, raw_json FROM s2_model_catalogue ORDER BY fetched_at DESC LIMIT 1').fetchone()
    if row is None:
        return None
    try:
        fetched_at = datetime.fromisoformat(row[0].replace('Z', '+00:00'))
        document = json.loads(row[1], object_pairs_hook=storage._unique_object)
        storage._strict_json(document)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise storage.IntegrityError('Relevé de modèles illisible') from error
    if fetched_at.tzinfo is None or type(document) is not dict:
        raise storage.IntegrityError('Relevé de modèles invalide')
    return fetched_at, document


def refresh(store, fetch):
    """Actualise le relevé au plus une fois par fenêtre de cache"""
    registry = _registry()
    settings = _settings(registry)
    now = _now()
    previous = _latest(store)
    if previous and now - previous[0] < timedelta(hours=settings['cache_hours']):
        return selection(store)
    try:
        models = _data(fetch('/api/v1/models'), list)
        if not models:
            raise ValueError('Liste OpenRouter vide')
        endpoint_documents = {}
        candidates = _candidates(models, settings, now)
        if not candidates or len({model['id'] for model in candidates}) != len(candidates):
            raise ValueError('Sélection OpenRouter vide ou dupliquée')
        for model in candidates:
            model_id = model['id']
            detail = _data(fetch('/api/v1/models/' + model_id + '/endpoints'), dict)
            if detail.get('id') != model_id or type(detail.get('endpoints')) is not list:
                raise ValueError('Endpoints du modèle non vérifiés')
            endpoint_documents[model_id] = detail
        document = {'models': models, 'endpoints': endpoint_documents}
        raw = storage._strict_json(document)
    except (OSError, HTTPException, ValueError):
        if previous:
            return selection(store)
        raise
    connection = store._connection_checked()
    with storage._transaction(connection, write=True):
        layout = storage._check_schema(connection)
        if layout not in ('s2', 's3', 's4', 's5', 's6'):
            raise storage.SchemaError('Catalogue de modèles sur stockage S2 ou ultérieur requis')
        connection.execute(TABLE_SQL.replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS'))
        connection.execute('DELETE FROM s2_model_catalogue')
        connection.execute('INSERT INTO s2_model_catalogue VALUES (?, ?)', (now.isoformat(), raw))
        storage._check_schema(connection)
    return selection(store)


def _million_price(value):
    if value is None:
        return None
    amount = storage._money(value)
    with localcontext() as context:
        context.prec = len(amount.as_tuple().digits) + 7
        return str(amount * 1_000_000)


def _variant(model_id):
    variants = []
    if re.search(r'(^|[-._])preview($|[-._:])', model_id):
        variants.append('preview')
    elif re.search(r'(^|[-._])exp($|[-._:])', model_id):
        variants.append('exp')
    if model_id.endswith(':free'):
        variants.append('free')
    return ':'.join(variants) or None


def selection(store):
    """Renvoie la dernière sélection connue et signale explicitement son âge"""
    latest = _latest(store)
    if latest is None:
        raise LookupError('Aucun relevé de modèles connu')
    fetched_at, document = latest
    registry = _registry()
    settings = _settings(registry)
    models = _data(document.get('models'), list)
    endpoint_documents = document.get('endpoints')
    if type(endpoint_documents) is not dict:
        raise storage.IntegrityError('Cache des endpoints absent')
    routes = _registered_routes(registry)
    view = [{
        'id': model_id,
        'name': model.get('name') if type(model.get('name')) is str else None,
        'maker': model_id.split('/', 1)[0],
        'family': family(model_id),
        'released': None,
        'input_price_per_million': None,
        'output_price_per_million': None,
        'reasoning_levels': [],
        'max_output_tokens': None,
        'variant': _variant(model_id),
        'excluded': 'malformed',
    } for model in models
        if (model_id := _model_id(model)) is not None
        and model_id.split('/', 1)[0] in settings['makers'] and _malformed(model)]
    for model in _candidates(models, settings, fetched_at):
        model_id = model['id']
        detail = endpoint_documents.get(model_id)
        if type(detail) is not dict or type(detail.get('endpoints')) is not list:
            raise storage.IntegrityError('Cache endpoint incomplet')
        reasoning = model.get('reasoning')
        levels = reasoning.get('supported_efforts') if type(reasoning) is dict else None
        if type(levels) is not list or any(type(level) is not str for level in levels):
            levels = []
        pricing = model.get('pricing')
        if pricing is None:
            pricing = {}
        if type(pricing) is not dict:
            raise ValueError('Tarifs modèle invalides')
        top_provider = model.get('top_provider')
        max_output = top_provider.get('max_completion_tokens') if type(top_provider) is dict else None
        if max_output is not None and type(max_output) is not int:
            raise ValueError('Limite de sortie invalide')
        view.append({
            'id': model_id,
            'name': model['name'],
            'maker': model_id.split('/', 1)[0],
            'family': family(model_id),
            'released': datetime.fromtimestamp(model['created'], timezone.utc).date().isoformat(),
            'input_price_per_million': _million_price(pricing.get('prompt')),
            'output_price_per_million': _million_price(pricing.get('completion')),
            'reasoning_levels': levels,
            'max_output_tokens': max_output,
            'variant': _variant(model_id),
            'excluded': None if _compliant(model_id, detail['endpoints'], routes)
            else 'no_compliant_provider',
        })
    return {'fetched_at': fetched_at.isoformat(),
            'stale': _now() - fetched_at >= timedelta(hours=settings['cache_hours']),
            'models': view}


def report(models, registry=None, now=None):
    registry = _registry() if registry is None else registry
    settings = _settings(registry)
    now = _now() if now is None else now
    current_ids = set()
    for model in models:
        model_id = _model_id(model)
        if model_id is None or _malformed(model):
            continue
        current_ids.add(model_id)
    configured_baseline = settings.get('baseline_models')
    if configured_baseline is not None and (
            type(configured_baseline) is not list
            or any(type(model_id) is not str for model_id in configured_baseline)):
        raise ValueError('Baseline des modèles invalide')
    baseline_ids = (set(configured_baseline) if configured_baseline is not None else
                    {value['model'] for key, value in registry.items()
                     if key != 'catalogue' and type(value) is dict
                     and type(value.get('model')) is str})
    current_families = {family(model['id']) for model in _candidates(models, settings, now)}
    configured_families = settings.get('baseline_families')
    if configured_families is not None and (
            type(configured_families) is not list
            or any(type(name) is not str for name in configured_families)):
        raise ValueError('Baseline des familles invalide')
    baseline_families = (set(configured_families) if configured_families is not None
                         else {family(model_id) for model_id in baseline_ids})
    return {'new_families': sorted(current_families - baseline_families),
            'missing_models': sorted(baseline_ids - current_ids)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', action='store_true')
    arguments = parser.parse_args(argv)
    if not arguments.report:
        parser.error('--report requis')
    document, _ = openrouter_prices.fetch_public(
        '/api/v1/models', max_response_bytes=MAX_RESPONSE_BYTES)
    result = report(_data(document, list))
    print(storage._strict_json(result))
    return 1 if result['new_families'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
