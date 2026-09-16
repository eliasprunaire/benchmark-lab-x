"""Private campaigns on S1–S3, with an explicitly injected fictional transport.

The local operator supplies authority and availability evidence. No provider,
plugin loader, queue drainer or content evaluator belongs to this module.
"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3

from . import qualification as q, storage
from .preparation import identifier
from .storage import (BudgetError, ConflictError, IntegrityError, SchemaError,
                      _fields, _money, _sum_money, _text, _transaction,
                      _strict_json as encode)

FORMAT_IDENTITY = 'benchmark-lab-x/campaigns/v1'
_TABLES = {
    's4_control': """CREATE TABLE s4_control (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        format_identity TEXT NOT NULL CHECK(format_identity = 'benchmark-lab-x/campaigns/v1')
    )""",
    's4_campaigns': """CREATE TABLE s4_campaigns (
        campaign_id TEXT PRIMARY KEY NOT NULL,
        contract_sha256 TEXT NOT NULL,
        manifest_json TEXT NOT NULL,
        manifest_sha256 TEXT UNIQUE NOT NULL CHECK(length(manifest_sha256) = 64)
    )""",
    's4_cells': """CREATE TABLE s4_cells (
        campaign_id TEXT NOT NULL REFERENCES s4_campaigns(campaign_id),
        cell_id TEXT NOT NULL,
        case_id TEXT NOT NULL,
        configuration_id TEXT NOT NULL,
        PRIMARY KEY(campaign_id, cell_id)
    )""",
    's4_admissions': """CREATE TABLE s4_admissions (
        admission_id TEXT PRIMARY KEY NOT NULL,
        campaign_id TEXT NOT NULL REFERENCES s4_campaigns(campaign_id),
        record_json TEXT NOT NULL,
        record_sha256 TEXT NOT NULL CHECK(length(record_sha256) = 64),
        UNIQUE(campaign_id, admission_id)
    )""",
    's4_status': """CREATE TABLE s4_status (
        campaign_id TEXT PRIMARY KEY NOT NULL REFERENCES s4_campaigns(campaign_id),
        admission_id TEXT,
        stop_reason TEXT,
        stopped_at TEXT,
        FOREIGN KEY(campaign_id, admission_id) REFERENCES s4_admissions(campaign_id, admission_id),
        CHECK((stop_reason IS NULL AND stopped_at IS NULL) OR
              (admission_id IS NULL AND stop_reason IS NOT NULL AND stopped_at IS NOT NULL))
    )""",
    's4_caps': """CREATE TABLE s4_caps (
        campaign_id TEXT PRIMARY KEY NOT NULL REFERENCES s4_campaigns(campaign_id),
        cap_usd TEXT NOT NULL,
        cap_source TEXT NOT NULL CHECK(cap_source IN ('default', 'requester'))
    )""",
    's4_attempts': """CREATE TABLE s4_attempts (
        operation_id TEXT PRIMARY KEY NOT NULL REFERENCES operations(operation_id),
        execution_id TEXT UNIQUE NOT NULL,
        campaign_id TEXT NOT NULL,
        cell_id TEXT NOT NULL,
        admission_id TEXT NOT NULL,
        request_json TEXT NOT NULL,
        request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
        engine_json TEXT NOT NULL,
        UNIQUE(campaign_id, cell_id),
        FOREIGN KEY(campaign_id, cell_id) REFERENCES s4_cells(campaign_id, cell_id),
        FOREIGN KEY(campaign_id, admission_id) REFERENCES s4_admissions(campaign_id, admission_id)
    )""",
    's4_emissions': """CREATE TABLE s4_emissions (
        operation_id TEXT PRIMARY KEY NOT NULL REFERENCES s4_attempts(operation_id),
        admission_id TEXT NOT NULL REFERENCES s4_admissions(admission_id),
        emitted_at TEXT NOT NULL
    )""",
    's4_results': """CREATE TABLE s4_results (
        operation_id TEXT PRIMARY KEY NOT NULL REFERENCES s4_emissions(operation_id),
        output_piece_id TEXT UNIQUE REFERENCES pieces(piece_id),
        receipt_sha256 TEXT NOT NULL CHECK(length(receipt_sha256) = 64),
        cost_sha256 TEXT NOT NULL CHECK(length(cost_sha256) = 64),
        received_at TEXT NOT NULL
    )""",
}
# The mutable admission pointer never rewrites its immutable authority history
_TRIGGERS = {}
for _table in _TABLES:
    if _table in ('s4_status', 's4_caps'):
        continue
    for _action in ('UPDATE', 'DELETE'):
        _TRIGGERS[f'{_table}_{_action.lower()}'] = (
            f'CREATE TRIGGER {_table}_{_action.lower()} BEFORE {_action} ON {_table} '
            "BEGIN SELECT RAISE(ABORT, 'immutable campaign evidence'); END")
    _keys = {'s4_control': ('singleton',), 's4_cells': ('campaign_id', 'cell_id')}.get(
        _table, ('campaign_id',) if _table == 's4_campaigns' else
        ('admission_id',) if _table == 's4_admissions' else ('operation_id',))
    _condition = ' AND '.join(f'{k}=NEW.{k}' for k in _keys)
    if _table == 's4_campaigns':
        _condition += ' OR manifest_sha256=NEW.manifest_sha256'
    if _table == 's4_attempts':
        _condition += ' OR execution_id=NEW.execution_id OR (campaign_id=NEW.campaign_id AND cell_id=NEW.cell_id)'
    if _table == 's4_results':
        _condition += ' OR output_piece_id=NEW.output_piece_id'
    _TRIGGERS[_table + '_insert'] = (
        f'CREATE TRIGGER {_table}_insert BEFORE INSERT ON {_table} '
        f'WHEN EXISTS (SELECT 1 FROM {_table} WHERE {_condition}) '
        "BEGIN SELECT RAISE(ABORT, 'immutable campaign identity'); END")

_MANIFEST = ('campaign_id', 'version', 'contract_sha256', 'cases', 'panel',
             'conditions', 'plan', 'attempt_policy', 'cost_basis')
_MANIFEST_OPTIONAL = ('financial_cost_policy', 'recovery_of', 'official_fallback', 'funding')
_CONFIGURATION = ('id', 'provider', 'model', 'revision', 'access', 'channel_id',
                  'route', 'parameters', 'effort', 'required_observations')
_CONFIGURATION_OPTIONAL = ('effort_limit', 'estimate')
_AUTHORITY = ('actor', 'authority_id', 'purpose', 'manifest_sha256', 'execution_authority',
              'candidate_authority', 'budget_authority', 'budget_id', 'allowed_cells', 'reserve_amounts')
_OBSERVED = ('provider', 'model', 'revision', 'access', 'channel_id', 'route', 'parameters', 'effort',
             'data_collection')
DEFAULT_MAX_OUTPUT_TOKENS = 4096
COMPARISON_COST_BASIS = {'scope': 'Une tentative par cellule', 'attempts': 'Sans reprise',
                         'unit': 'USD', 'conversion': None}
BYTES_PER_TOKEN = 3
DEFAULT_CAP_USD = Decimal('50.00')
CANDIDATE_SYSTEM_PROMPT = (
    'Vous répondez à une tâche de travail décrite dans le message. Répondez en français. '
    'Produisez exactement les livrables demandés, sans commenter la consigne ni les pièces.')
_EFFORT_ORDER = ('minimal', 'low', 'medium', 'high', 'xhigh')


def schema_objects():
    return ([("table", name, name, sql) for name, sql in _TABLES.items()]
            + [("index", f'sqlite_autoindex_{name}_{i}', name, None)
               for name, count in (('s4_campaigns', 2), ('s4_cells', 1), ('s4_admissions', 2),
                                   ('s4_status', 1), ('s4_caps', 1), ('s4_attempts', 3),
                                   ('s4_emissions', 1), ('s4_results', 2))
               for i in range(1, count + 1)]
            + [("trigger", name, name.rsplit('_', 1)[0], sql) for name, sql in _TRIGGERS.items()])


def initialize(data):
    """Add S4 explicitly to an intact S3 database, without altering prior records."""
    with closing(storage.Store(data)) as store:
        _intact(store)
        connection = store._connection_checked()
        with _transaction(connection, write=True):
            layout = storage._check_schema(connection)
            if layout in ('s4', 's5', 's6'):
                return
            if layout != 's3':
                raise SchemaError('Extension explicite sur une base S3 requise')
            for sql in (*_TABLES.values(), *_TRIGGERS.values()):
                connection.execute(sql)
            connection.execute('INSERT INTO s4_control VALUES (1, ?)', (FORMAT_IDENTITY,))
            storage._check_schema(connection)


def connection_for(store):
    connection = store._connection_checked()
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s4_control'").fetchone():
        raise SchemaError('Initialisation explicite des campagnes requise')
    return connection


def _intact(store):
    proof = store.verify_storage()
    if not proof['integrity_ok'] or proof['orphan_files']:
        raise IntegrityError('Stockage incomplet ou altéré')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _date(value):
    _text(value, 'date')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Date avec fuseau requise')
    return parsed


def _present(value, label):
    _text(value, label)
    if not value.strip() or value.upper() in ('INCONNU', 'UNKNOWN'):
        raise ValueError('Preuve requise : ' + label)


def _entries(values, fields, label, optional=()):
    if type(values) is not list or not values:
        raise ValueError('Liste non vide requise : ' + label)
    seen = set()
    for value in values:
        _fields(value, fields + tuple(key for key in optional if key in value), label)
        key = identifier(value[fields[0]])
        if key in seen:
            raise ValueError('Identité répétée : ' + label)
        seen.add(key)
    return seen


def _manifest(value, contract, *, require_data_collection=False):
    _fields(value, _MANIFEST + tuple(k for k in _MANIFEST_OPTIONAL if k in value), 'manifest')
    if value.get('funding', 'operator') not in ('operator', 'requester'):
        raise ValueError('Financement de campagne inconnu')
    if value.get('financial_cost_policy', 'require_observed') not in ('require_observed', 'retain_reserve'):
        raise ValueError('Politique financière inconnue')
    if 'recovery_of' in value:
        identifier(value['recovery_of'])
    if 'official_fallback' in value:
        if 'recovery_of' not in value:
            raise ValueError('Secours officiel sans tentative source')
        _fields(value['official_fallback'], ('route_attempts',), 'official fallback')
        q._texts(value['official_fallback']['route_attempts'], 'route attempts', required=True, unique=True)
    encode(value)
    identifier(value['campaign_id'])
    if type(value['version']) is not int or value['version'] < 1:
        raise ValueError('Version de manifeste requise')
    q._hash(value['contract_sha256'])
    cases = _entries(value['cases'], ('id', 'package_sha256'), 'cases')
    for case in value['cases']:
        if case['package_sha256'] != contract['package_sha256']:
            raise ValueError('Cas sans paquet contractuel exact')
    panel = _entries(value['panel'], _CONFIGURATION, 'panel', _CONFIGURATION_OPTIONAL)
    from .pi_official import provider_for_endpoint
    from .openrouter_preparation import ENDPOINT
    if any(provider_for_endpoint(config['channel_id']) for config in value['panel']) and 'official_fallback' not in value:
        raise ValueError('Secours officiel lié aux reçus OpenRouter requis')
    for config in value['panel']:
        for field in ('provider', 'model', 'revision', 'access', 'channel_id'):
            _present(config[field], field)
        if config['revision'].lower() in ('latest', 'default', 'current'):
            raise ValueError('Révision mobile non prouvée')
        for field in ('route', 'effort'):
            _text(config[field], field)
        if config['access'] not in ('API', 'direct') or type(config['parameters']) is not dict:
            raise ValueError('Configuration demandée invalide')
        if require_data_collection and config['channel_id'] == ENDPOINT:
            provider = config['parameters'].get('provider')
            if type(provider) is not dict or provider.get('data_collection') != 'deny':
                raise ValueError('DATA_COLLECTION_REQUIRED')
        if config.get('effort_limit') not in (None, 'not_adjustable'):
            raise ValueError('Limite d’effort inconnue')
        if 'estimate' in config:
            encode(config['estimate'])
        q._texts(config['required_observations'], 'required_observations', required=True, unique=True)
        required = set(config['required_observations'])
        if not {'revision', 'channel_id'} <= required <= set(_OBSERVED) & set(_CONFIGURATION):
            raise ValueError('required_observations invalide')
        if any(config[field] in (None, 'INCONNU') for field in required):
            raise ValueError('Identité requise inconnue')
    if value.get('funding') == 'requester':
        if any(config['channel_id'] != ENDPOINT for config in value['panel']):
            raise ValueError('Financement demandeur réservé au canal OpenRouter')
    conditions = value['conditions']
    _fields(conditions, ('pi', 'packages', 'tools', 'skills', 'context_sha256', 'defaults', 'environment', 'frozen_at'), 'conditions')
    pi = conditions['pi']
    _fields(pi, ('package', 'version', 'sha256', 'status', 'proof'), 'pi')
    for key in ('package', 'version', 'proof'):
        _present(pi[key], key)
    q._hash(pi['sha256'])
    if pi['status'] not in ('declared', 'configured', 'active', 'observed'):
        raise ValueError('Statut Pi inconnu')
    q._hash(conditions['context_sha256'])
    _date(conditions['frozen_at'])
    for field in ('packages', 'tools', 'skills'):
        if type(conditions[field]) is not list:
            raise ValueError('Liste de ressources requise')
    if type(conditions['defaults']) is not dict or type(conditions['environment']) is not dict or not conditions['environment']:
        raise ValueError('Conditions communes incomplètes')
    cells = _entries(value['plan'], ('cell_id', 'case_id', 'configuration_id'), 'plan')
    for cell in value['plan']:
        if cell['case_id'] not in cases or cell['configuration_id'] not in panel:
            raise ValueError('Cellule hors du manifeste')
    policy = value['attempt_policy']
    _fields(policy, ('retries', 'order', 'reason'), 'attempt_policy')
    q._texts(policy['order'], 'order', required=True, unique=True)
    _present(policy['reason'], 'reason')
    if policy['retries'] is not False or set(policy['order']) != cells:
        raise ValueError('Plan exact sans retry requis')
    if value['cost_basis'] != contract['specification']['cost_basis']:
        raise ValueError('Base de coût divergente')


def _comparison_specification(spec):
    from .outgoing import criteria
    _fields(spec, ('result_expected', 'obligations', 'eliminatory_errors',
                   'secondary_criteria', 'limits', 'cost_basis'), 'Spécification de comparaison')
    _text(spec['result_expected'], 'Résultat attendu')
    q._texts(spec['limits'], 'Limites')
    ids = set()
    for kind in ('obligations', 'eliminatory_errors', 'secondary_criteria'):
        entries = spec[kind]
        if type(entries) is not list:
            raise ValueError('Liste de critères requise')
        if kind == 'secondary_criteria' and len(entries) > 2:
            raise ValueError('QUALITY_LIMIT')
        for entry in entries:
            keys = ('id', 'label', 'scale', 'favorable') if kind == 'secondary_criteria' else ('id', 'description')
            _fields(entry, keys, 'Critère de comparaison')
            identifier(entry['id'])
            if entry['id'] in ids:
                raise ValueError('Identifiant de critère répété')
            ids.add(entry['id'])
            if kind == 'secondary_criteria':
                criteria({'eliminatory': [], 'obligations': [],
                          'quality': [{key: entry[key] for key in ('label', 'scale', 'favorable')}]})
            else:
                _text(entry['description'], 'Description du critère')
    _fields(spec['cost_basis'], COMPARISON_COST_BASIS, 'Base de coût')
    if spec['cost_basis'] != COMPARISON_COST_BASIS:
        raise ValueError('Base de coût de comparaison divergente')


def _comparison_spec(package):
    from .outgoing import criteria
    grouped = criteria(package['criteria'])
    return dict(result_expected=package['instruction'],
                obligations=[dict(id=f'O{i}', description=text) for i, text in enumerate(grouped['obligations'], 1)],
                eliminatory_errors=[dict(id=f'E{i}', description=text) for i, text in enumerate(grouped['eliminatory'], 1)],
                secondary_criteria=[dict(id=f'Q{i}', **item) for i, item in enumerate(grouped['quality'], 1)],
                limits=deepcopy(package['acceptable_ambiguities']), cost_basis=deepcopy(COMPARISON_COST_BASIS))


def _record_comparison_contract(store, connection, operation):
    dossier_id, revision = operation['dossier_id'], operation['revision']
    if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s3_control'").fetchone():
        if connection.execute('SELECT 1 FROM s3_contracts JOIN s3_approvals USING(contract_sha256) '
                              'WHERE dossier_id=? AND revision=?', (dossier_id, revision)).fetchone():
            return
    package, package_hash = q._package(store, connection, dossier_id, revision)
    spec = _comparison_spec(package)
    _comparison_specification(spec)
    version = connection.execute('SELECT COALESCE(MAX(version), 0)+1 FROM s2_comparison_contracts '
                                 'WHERE dossier_id=?', (dossier_id,)).fetchone()[0]
    contract = dict(dossier_id=dossier_id, revision=revision, version=version, package=package,
                    package_sha256=package_hash, reference_pieces=[], specification=spec)
    q._validated(connection, contract)
    received = store._operation_for_update(connection, operation['operation_id'], ('RECEIVED',))
    authority = dict(authority_id='assistant:' + received['requested_configuration']['model'],
                     operation_id=received['operation_id'], receipt_sha256=q.digest(received['receipt']))
    connection.execute('INSERT INTO s2_comparison_contracts VALUES (?,?,?,?,?,?)',
                       (q.digest(contract), dossier_id, revision, version, encode(contract), encode(authority)))


def _comparison_contract(store, connection, fingerprint):
    from .preparation import _automatic_qualification, _qualification_result
    q._hash(fingerprint)
    row = connection.execute('SELECT dossier_id,revision,version,contract_json,authority_json '
                             'FROM s2_comparison_contracts WHERE contract_sha256=?', (fingerprint,)).fetchone()
    if row is None:
        raise KeyError(fingerprint)
    contract = q._decode(row[3], fingerprint)
    _fields(contract, ('dossier_id', 'revision', 'version', 'package', 'package_sha256',
                       'reference_pieces', 'specification'), 'Contrat de comparaison')
    storage._identity(contract['dossier_id'], contract['revision'])
    if (tuple(contract[key] for key in ('dossier_id', 'revision', 'version')) != row[:3]
            or type(contract['version']) is not int or contract['version'] < 1
            or contract['reference_pieces'] != []):
        raise IntegrityError('Identité du contrat de comparaison divergente')
    _comparison_specification(contract['specification'])
    package, package_hash = q._package(store, connection, *row[:2])
    if ((contract['package'], contract['package_sha256']) != (package, package_hash)
            or contract['specification'] != _comparison_spec(package)):
        raise IntegrityError('Contrat de comparaison divergent du paquet')
    qualified = _automatic_qualification(store, connection, *row[:2])
    if qualified is None or not qualified['qualified']:
        raise IntegrityError('Contrat de comparaison sans qualification réussie')
    operation = store._operation_for_update(connection, qualified['operation_id'], ('RECEIVED',))
    result = _qualification_result(operation['receipt']['result'])
    authority = json.loads(row[4], object_pairs_hook=storage._unique_object)
    if (encode(authority) != row[4]
            or authority != dict(authority_id='assistant:' + qualified['model'],
                                 operation_id=qualified['operation_id'], receipt_sha256=q.digest(operation['receipt']))
            or result != {key: qualified[key] for key in ('qualified', 'findings', 'summary')}):
        raise IntegrityError('Autorité ou reçu du contrat de comparaison divergent')
    q._validated(connection, contract)
    return contract


def _approved(store, connection, fingerprint, *, current=False):
    has_s3 = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s3_control'").fetchone()
    if not has_s3 or not connection.execute('SELECT 1 FROM s3_contracts WHERE contract_sha256=?', (fingerprint,)).fetchone():
        contract = _comparison_contract(store, connection, fingerprint)
        if current:
            q._current(store, connection, contract['dossier_id'], contract['revision'])
            latest = connection.execute('SELECT contract_sha256 FROM s2_comparison_contracts '
                                        'WHERE dossier_id=? ORDER BY version DESC LIMIT 1',
                                        (contract['dossier_id'],)).fetchone()
            if latest != (fingerprint,):
                raise ConflictError('Version contractuelle périmée')
        return contract
    snapshot = q._inspect(store, connection, fingerprint)
    if snapshot['approval'] is None:
        raise ValueError('Contrat S3 approuvé requis')
    contract = snapshot['contract']
    if current:
        q._current(store, connection, contract['dossier_id'], contract['revision'], fingerprint)
        q._validated(connection, contract)
    return contract


def create(store, manifest):
    value = deepcopy(manifest)
    _fields(value, _MANIFEST + tuple(k for k in _MANIFEST_OPTIONAL if k in value), 'manifest')
    _intact(store)
    connection = connection_for(store)
    with _transaction(connection, write=True):
        return _create(store, connection, value)


def _current_contract(store, connection, dossier_id):
    has_s3 = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s3_control'").fetchone()
    row = connection.execute(
        'SELECT c.contract_sha256 FROM s3_contracts c JOIN s3_approvals a USING(contract_sha256) '
        'WHERE c.dossier_id=? ORDER BY c.version DESC LIMIT 1', (dossier_id,)).fetchone() if has_s3 else None
    if row is None:
        row = connection.execute('SELECT c.contract_sha256 FROM s2_comparison_contracts c JOIN s2_dossiers d '
                                 'ON c.dossier_id=d.dossier_id AND c.revision=d.current_revision '
                                 'WHERE c.dossier_id=? ORDER BY c.version DESC LIMIT 1', (dossier_id,)).fetchone()
    if row is None:
        from .preparation import Denied
        raise Denied('CONTRACT_MISSING')
    return _approved(store, connection, row[0], current=True)


def _requester_campaigns(store, connection, dossier_id):
    prefix = f'{dossier_id}-c'
    rows = connection.execute(
        'SELECT c.campaign_id FROM s4_campaigns c JOIN '
        '(SELECT contract_sha256,dossier_id FROM s3_contracts UNION ALL '
        'SELECT contract_sha256,dossier_id FROM s2_comparison_contracts) q USING(contract_sha256) '
        'WHERE q.dossier_id=? ORDER BY c.rowid', (dossier_id,)).fetchall()
    snapshots = [_inspect(store, connection, campaign_id) for (campaign_id,) in rows]
    return [snapshot for snapshot in snapshots
            if snapshot['manifest'].get('funding') == 'requester'
            and snapshot['manifest']['campaign_id'].startswith(prefix)]


def _configuration(model, tier, index, tier_table, assumptions, fetched_at):
    from . import openrouter_prices
    parameters = {
        'max_tokens': DEFAULT_MAX_OUTPUT_TOKENS,
        'provider': {'only': [model['route']], 'order': [model['route']],
                     'allow_fallbacks': False, 'require_parameters': True,
                     'data_collection': 'deny'},
    }
    effort = 'off'
    effort_limit = None
    if tier == 'enhanced':
        levels = [level for level in model['reasoning_levels']
                  if level in _EFFORT_ORDER and level != 'low']
        if 'high' in levels:
            effort = 'high'
            parameters['reasoning'] = {'effort': effort}
        elif levels:
            effort = max(levels, key=_EFFORT_ORDER.index)
            parameters['reasoning'] = {'effort': effort}
        elif model['maker'] in tier_table:
            effort = 'on'
            parameters['reasoning'] = deepcopy(tier_table[model['maker']]['enhanced'])
        else:
            effort_limit = 'not_adjustable'
    pricing = {
        'prompt': (None if model['input_price_per_million'] is None else
                   str(_money(model['input_price_per_million']) / Decimal(1_000_000))),
        'completion': (None if model['output_price_per_million'] is None else
                       str(_money(model['output_price_per_million']) / Decimal(1_000_000))),
    }
    forecast = openrouter_prices.price_row(
        pricing, {'prompt': assumptions['input_tokens'], 'completion': assumptions['output_tokens']})['forecast']
    estimate = {'amount_usd': forecast['token_subtotal_usd'], 'forecast': forecast,
                'fetched_at': fetched_at, 'assumptions': deepcopy(assumptions)}
    configuration = dict(
        id=f'configuration-{index}', provider='OpenRouter', model=model['id'], revision=model['id'],
        access='API', channel_id='https://openrouter.ai/api/v1/chat/completions',
        route='OpenRouter pinned endpoint: ' + model['route'], parameters=parameters, effort=effort,
        required_observations=['revision', 'channel_id'], estimate=estimate)
    if effort_limit is not None:
        configuration['effort_limit'] = effort_limit
    return configuration


def prepare_configurations(store, session_id, dossier_id, body, candidate_identity):
    from . import model_catalogue, outgoing
    from .pi_openrouter import system_context
    _fields(body, ('models', 'tier'), 'configurations')
    if (type(body['models']) is not list or len(body['models']) < 2
            or len(set(body['models'])) != len(body['models'])
            or any(type(model_id) is not str for model_id in body['models'])):
        raise ValueError('Au moins deux modèles distincts sont requis')
    if body['tier'] not in ('standard', 'enhanced'):
        raise ValueError('Palier inconnu')
    if type(candidate_identity) is not dict:
        raise LookupError('CANDIDATE_PI_UNAVAILABLE')
    _intact(store)
    connection = connection_for(store)
    with _transaction(connection, write=True):
        from .preparation import owner
        owner(connection, session_id, dossier_id)
        contract = _current_contract(store, connection, dossier_id)
        catalogue = model_catalogue.selection(store)
        by_id = {model['id']: model for model in catalogue['models']}
        try:
            selected = [by_id[model_id] for model_id in body['models']]
        except KeyError as error:
            raise ValueError('Modèle absent du catalogue') from error
        if any(model['excluded'] is not None or model['route'] is None for model in selected):
            raise ValueError('Modèle exclu du catalogue')
        context_lengths = [model['context_length'] for model in selected]
        if any(type(value) is not int or value <= 0 for value in context_lengths):
            raise ValueError('Fenêtre de contexte absente du relevé')
        pieces = [dict(id=piece['id'], role='candidate',
                       content=store.read_piece(piece['id']).decode('utf-8'))
                  for piece in contract['package']['pieces']]
        user_message = encode(outgoing.candidate(contract['package'], pieces))
        candidate_bytes = (system_context(CANDIDATE_SYSTEM_PROMPT) + user_message).encode('utf-8')
        assumptions = {'bytes_per_token': BYTES_PER_TOKEN,
                       'input_tokens': (len(candidate_bytes) + BYTES_PER_TOKEN - 1) // BYTES_PER_TOKEN,
                       'output_tokens': DEFAULT_MAX_OUTPUT_TOKENS, 'cached_input_tokens': 0,
                       'requests_per_cell': 1}
        tier_table = model_catalogue.tiers()
        panel = [_configuration(model, body['tier'], index, tier_table, assumptions,
                                catalogue['fetched_at'])
                 for index, model in enumerate(selected, 1)]
        count = len(_requester_campaigns(store, connection, dossier_id))
        campaign_id = f'{dossier_id}-c{count + 1}'
        case = {'id': 'case-1', 'package_sha256': contract['package_sha256']}
        plan = [dict(cell_id=f'cell-{index}', case_id=case['id'], configuration_id=config['id'])
                for index, config in enumerate(panel, 1)]
        manifest = dict(
            campaign_id=campaign_id, version=1, contract_sha256=q.digest(contract), cases=[case], panel=panel,
            conditions=dict(
                pi=dict(package=candidate_identity['package'], version=candidate_identity['version'],
                        sha256=candidate_identity['sha256'], status='active', proof=candidate_identity['scope']),
                packages=[], tools=[], skills=[],
                context_sha256=sha256(system_context(CANDIDATE_SYSTEM_PROMPT).encode()).hexdigest(),
                defaults=dict(system_prompt=CANDIDATE_SYSTEM_PROMPT, timeout_seconds=300,
                              context_window=min(context_lengths), max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                              defaults_source='DEFAULT_MAX_OUTPUT_TOKENS'),
                environment={key: candidate_identity[key] for key in ('node_version', 'node_sha256', 'bridge_sha256')},
                frozen_at=_now()),
            plan=plan,
            attempt_policy=dict(retries=False, order=[cell['cell_id'] for cell in plan],
                                reason='Ordre de sélection du demandeur'),
            cost_basis=contract['specification']['cost_basis'], funding='requester')
        _create(store, connection, manifest)
    return configurations_view(store, session_id, dossier_id)


def configurations_view(store, session_id, dossier_id):
    from . import model_catalogue
    from .preparation import owner, page_view
    connection = connection_for(store)
    with _transaction(connection):
        owner(connection, session_id, dossier_id)
        try:
            catalogue = model_catalogue.selection(store)
        except LookupError:
            catalogue = None
        tier_table = model_catalogue.tiers() if catalogue is not None else {}
        prepared = [snapshot for snapshot in _requester_campaigns(store, connection, dossier_id)
                    if not snapshot['admissions'] and not snapshot['attempts']]
        selected = set() if not prepared else {
            item['model'] for item in prepared[-1]['manifest']['panel']}
        models = []
        for model in [] if catalogue is None else catalogue['models']:
            if model['excluded'] is not None or model['route'] is None:
                continue
            levels = [level for level in model['reasoning_levels']
                      if level in _EFFORT_ORDER and level != 'low']
            models.append({'id': model['id'], 'name': model['name'] or model['id'],
                           'selected': model['id'] in selected,
                           'not_adjustable': not levels and model['maker'] not in tier_table})
        if not prepared:
            return page_view({'kind': 'configurations', 'dossier_id': dossier_id,
                              'current_campaign_id': None, 'configurations': [], 'models': models,
                              'current_tier': 'standard', 'superseded': [],
                              'available_tiers': ['standard', 'enhanced'], 'cap_usd': str(DEFAULT_CAP_USD),
                              'cap_source': 'default', 'estimate_total_usd': None,
                              'estimate_under_cap': False, 'assumptions': None,
                              'fetched_at': None if catalogue is None else catalogue['fetched_at'],
                              'catalogue_available': catalogue is not None,
                              'detail': ('Relevé de modèles indisponible'
                                         if catalogue is None else None)})
        current = prepared[-1]
        estimates = [config['estimate']['amount_usd'] for config in current['manifest']['panel']]
        total = None if any(value is None for value in estimates) else format(
            _sum_money(_money(value) for value in estimates), 'f')
        first = current['manifest']['panel'][0]['estimate']
        return page_view({
            'kind': 'configurations', 'dossier_id': dossier_id, 'models': models,
            'current_campaign_id': current['manifest']['campaign_id'],
            'configurations': current['manifest']['panel'],
            'current_tier': ('enhanced' if any(item['effort'] != 'off' or
                                               item.get('effort_limit') == 'not_adjustable'
                                               for item in current['manifest']['panel']) else 'standard'),
            'superseded': [snapshot['manifest']['campaign_id'] for snapshot in prepared[:-1]],
            'available_tiers': ['standard', 'enhanced'], 'cap_usd': current['cap_usd'],
            'cap_source': current['cap_source'], 'estimate_total_usd': total,
            'estimate_under_cap': total is not None and _money(total) <= _money(current['cap_usd']),
            'assumptions': first['assumptions'], 'fetched_at': first['fetched_at'],
            'catalogue_available': catalogue is not None,
            'detail': 'Relevé de modèles indisponible' if catalogue is None else None})


def _create(store, connection, value):
    """Shared creation body; caller owns the enclosing transaction"""
    try:
        if not connection.execute('SELECT 1 FROM s3_contracts WHERE contract_sha256=? UNION ALL '
                                  'SELECT 1 FROM s2_comparison_contracts WHERE contract_sha256=?',
                                  (value['contract_sha256'], value['contract_sha256'])).fetchone():
            raise IntegrityError('Contrat de campagne introuvable')
        contract = _approved(store, connection, value['contract_sha256'], current=True)
        _manifest(value, contract, require_data_collection=True)
        from .model_catalog import require_current
        for configuration in value['panel']:
            require_current(configuration)
        if 'recovery_of' in value:
            from .recovery import validate_link
            validate_link(store, connection, value)
        fingerprint = q.digest(value)
        connection.execute('INSERT INTO s4_campaigns VALUES (?,?,?,?)',
                           (value['campaign_id'], value['contract_sha256'], encode(value), fingerprint))
        for cell in value['plan']:
            connection.execute('INSERT INTO s4_cells VALUES (?,?,?,?)',
                               (value['campaign_id'], cell['cell_id'], cell['case_id'], cell['configuration_id']))
        connection.execute('INSERT INTO s4_status VALUES (?,NULL,NULL,NULL)', (value['campaign_id'],))
        connection.execute('INSERT INTO s4_caps VALUES (?,?,?)',
                           (value['campaign_id'], str(DEFAULT_CAP_USD), 'default'))
        return dict(manifest=value, manifest_sha256=fingerprint)
    except sqlite3.IntegrityError as error:
        raise ConflictError('Identité de campagne déjà utilisée') from error


def _load(store, connection, campaign_id):
    identifier(campaign_id)
    row = connection.execute('SELECT contract_sha256, manifest_json, manifest_sha256 FROM s4_campaigns '
                             'WHERE campaign_id=?', (campaign_id,)).fetchone()
    if row is None:
        raise KeyError(campaign_id)
    manifest = q._decode(row[1], row[2])
    if (manifest['campaign_id'], manifest['contract_sha256']) != (campaign_id, row[0]):
        raise IntegrityError('Identité du manifeste divergente')
    contract = _approved(store, connection, row[0])
    _manifest(manifest, contract)
    if 'recovery_of' in manifest:
        from .recovery import validate_link
        validate_link(store, connection, manifest)
    cells = connection.execute('SELECT cell_id, case_id, configuration_id FROM s4_cells '
                               'WHERE campaign_id=? ORDER BY cell_id', (campaign_id,)).fetchall()
    if cells != sorted(tuple(cell[k] for k in ('cell_id', 'case_id', 'configuration_id')) for cell in manifest['plan']):
        raise IntegrityError('Cellules divergentes du manifeste')
    return manifest, row[2], contract


def _authority(value, manifest, fingerprint):
    extra = tuple(key for key in ('browser_launch', 'technical_recovery', 'derived_from') if key in value)
    _fields(value, _AUTHORITY + extra, 'campaign authority')
    if 'technical_recovery' in value and 'derived_from' in value:
        raise ValueError('Préautorisation propriétaire distincte de l’admission dérivée')
    if 'derived_from' in value and 'browser_launch' in value:
        raise ValueError('Admission dérivée sans nouveau lancement')
    if 'technical_recovery' in value:
        from .recovery import validate_grant
        validate_grant(value['technical_recovery'], purpose=value.get('purpose'),
                       recovery='recovery_of' in manifest)
    if 'derived_from' in value:
        from .recovery import validate_derived_from
        validate_derived_from(value['derived_from'])
    if 'browser_launch' in value:
        grant = value['browser_launch']
        _fields(grant, ('session_id', 'estimate'), 'browser launch')
        identifier(grant['session_id'])
        estimate = grant['estimate']
        if estimate is not None:
            _fields(estimate, ('amount', 'currency', 'assumptions', 'source'), 'estimate')
            _money(estimate['amount'])
            if estimate['currency'] != manifest['cost_basis']['unit']:
                raise ValueError('Unité de prévision divergente')
            _present(estimate['assumptions'], 'assumptions')
            _present(estimate['source'], 'source')
    encode(value)
    for key in ('actor', 'authority_id', 'execution_authority', 'candidate_authority', 'budget_authority', 'budget_id'):
        _present(value[key], key)
    if value['actor'] != 'Ayo' or value['manifest_sha256'] != fingerprint or value['purpose'] not in ('start', 'resume'):
        raise ValueError('Autorité locale divergente')
    q._texts(value['allowed_cells'], 'allowed_cells', required=True, unique=True)
    cells = {c['cell_id'] for c in manifest['plan']}
    if not set(value['allowed_cells']) <= cells:
        raise ValueError('Cellules non prévues')
    amounts = value['reserve_amounts']
    if type(amounts) is not dict or not set(value['allowed_cells']) <= amounts.keys() <= cells:
        raise ValueError('Réserves par cellule requises')
    for amount in amounts.values():
        _money(amount)


def _evidence(value, manifest):
    _fields(value, ('pi_sha256', 'context_sha256', 'channels', 'confinement'), 'admission evidence')
    encode(value)
    if value['pi_sha256'] != manifest['conditions']['pi']['sha256'] or value['context_sha256'] != manifest['conditions']['context_sha256']:
        raise ValueError('Conditions Pi divergentes')
    channels = value['channels']
    if type(channels) is not dict or channels.keys() != {c['id'] for c in manifest['panel']}:
        raise ValueError('Disponibilité du panel requise')
    for config in manifest['panel']:
        channel = channels[config['id']]
        keys = {'available', 'revision', 'channel_id', 'route', 'proof'} | set(config['required_observations'])
        _fields(channel, keys, 'channel')
        _present(channel['proof'], 'channel proof')
        if channel['available'] is not True or any(channel[k] != config[k] for k in keys - {'available', 'proof'}):
            raise ValueError('Canal ou identité indisponible')
    confinement = value['confinement']
    _fields(confinement, ('code_execution', 'proof'), 'confinement')
    _present(confinement['proof'], 'confinement proof')
    # This slice has no code-execution adapter or confinement verifier. A supplied
    # boolean cannot qualify one. Fail closed on executable tools and extensions
    if confinement['code_execution'] is not False or any(manifest['conditions'][k] for k in ('tools', 'packages', 'skills')):
        raise ValueError('Confinement des outils non qualifié dans ce transport fictif')


def _admissions(store, connection, manifest, fingerprint):
    result = {}
    for aid, raw, digest in connection.execute('SELECT admission_id, record_json, record_sha256 '
                                             'FROM s4_admissions WHERE campaign_id=? ORDER BY rowid',
                                             (manifest['campaign_id'],)):
        record = q._decode(raw, digest)
        _fields(record, ('admission_id', 'campaign_id', 'authority', 'evidence', 'created_at'), 'admission record')
        if (record['admission_id'], record['campaign_id']) != (aid, manifest['campaign_id']):
            raise IntegrityError('Admission étrangère')
        _date(record['created_at'])
        _authority(record['authority'], manifest, fingerprint)
        _evidence(record['evidence'], manifest)
        if 'derived_from' in record['authority']:
            from .recovery import bind_derived
            bind_derived(store, connection, manifest, record['authority'], stored=True)
        if record['authority']['purpose'] != ('resume' if result else 'start'):
            raise IntegrityError('Chronologie des admissions divergente')
        result[aid] = record
    return result


def _request(store, manifest, fingerprint, contract, cell):
    config = next(c for c in manifest['panel'] if c['id'] == cell['configuration_id'])
    request = dict(campaign_id=manifest['campaign_id'], manifest_sha256=fingerprint,
                contract_sha256=manifest['contract_sha256'], cell_id=cell['cell_id'], case_id=cell['case_id'],
                requested_configuration=config, conditions=manifest['conditions'], package=contract['package'],
                pieces=[dict(id=p['id'], sha256=p['sha256'], content=store.read_piece(p['id']).decode('utf-8'))
                        for p in contract['package']['pieces']])
    if 'outgoing_format' in contract['package']:
        from . import outgoing
        from .preparation import package_check
        package_check(store, contract['dossier_id'], contract['revision'], contract['package'], contract['package_sha256'])
        request['outgoing_format'] = contract['package']['outgoing_format']
        request['pieces'] = [dict(p, role=store.get_piece(p['id'])['role']) for p in request['pieces']]
        request['outgoing'] = outgoing.candidate(contract['package'], request['pieces'])
    return request


def _engine():
    return {name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ('campaigns.py', 'storage.py', 'preparation.py', 'web_api.py', 'qualification.py', 'runtime.py',
                         'validation.py',
                         'pi_openrouter.py', 'pi_official.py', 'pi_bridge.mjs',
                         'openrouter_preparation.py', 'recovery.py', 'outgoing.py')}


def _transport_view(request):
    from . import outgoing
    if request.get('outgoing_format') != outgoing.FORMAT:
        raise ValueError('Ancien format sortant : nouvelle version de tâche requise')
    content = request['outgoing']
    outgoing_view = outgoing.closed_candidate(dict(
        instruction=content['instruction'], deliverables=list(content['deliverables']),
        criteria=deepcopy(content['criteria']), acceptable_ambiguities=list(content['acceptable_ambiguities']),
        pieces=[dict(name=piece['name'], content=piece['content']) for piece in content['pieces']]))
    config = request['requested_configuration']
    conditions = request['conditions']
    defaults = conditions.get('defaults') or {}
    environment = conditions.get('environment') or {}
    pi = conditions['pi']
    return dict(
        outgoing_format=outgoing.FORMAT,
        outgoing=outgoing_view,
        requested_configuration=dict(
            provider=config['provider'], model=config['model'], revision=config['revision'],
            access=config['access'], channel_id=config['channel_id'], route=config['route'],
            parameters=deepcopy(config['parameters']), effort=config['effort'],
            required_observations=list(config['required_observations'])),
        conditions=dict(
            pi=dict(package=pi['package'], version=pi['version'], sha256=pi['sha256']),
            packages=list(conditions['packages']), tools=list(conditions['tools']),
            skills=list(conditions['skills']), context_sha256=conditions['context_sha256'],
            defaults={key: defaults[key] for key in ('system_prompt', 'timeout_seconds', 'context_window',
                                                     'max_output_tokens', 'defaults_source') if key in defaults},
            environment={key: environment[key] for key in ('node_version', 'node_sha256', 'bridge_sha256') if key in environment},
            frozen_at=conditions['frozen_at']))


def _transport_operation(operation):
    value = dict(operation_id=operation['operation_id'], phase=operation['phase'])
    if operation.get('state') == 'EMISSION_POSSIBLE':
        value['state'] = 'EMISSION_POSSIBLE'
    return value


def _attribution(receipt, configuration):
    observed = receipt['observed_configuration'] or {}
    sources = observed.get('sources', {})
    return [field for field in configuration['required_observations']
            if observed.get(field) != configuration[field]
            or type(sources) is not dict or type(sources.get(field)) is not str
            or not sources[field].strip() or sources[field] in ('INCONNU', 'UNKNOWN')]


def _result(receipt, cost):
    storage._receipt(receipt, cost)
    result = receipt['result']
    _fields(result, ('output', 'incident', 'emission'), 'acquisition result')
    if result['output'] is not None:
        if type(result['output']) is not str:
            raise ValueError('Sortie texte UTF-8 ou null requise')
        result['output'].encode('utf-8')
    if result['incident'] is not None:
        _present(result['incident'], 'incident')
    if result['emission'] not in ('ESTABLISHED', 'UNKNOWN', 'INCONNU'):
        raise ValueError('Émission établie ou inconnue requise')


def _inspect(store, connection, campaign_id):
    manifest, fingerprint, contract = _load(store, connection, campaign_id)
    admissions = _admissions(store, connection, manifest, fingerprint)
    status = connection.execute('SELECT admission_id, stop_reason, stopped_at FROM s4_status WHERE campaign_id=?',
                                (campaign_id,)).fetchone()
    cap = connection.execute('SELECT cap_usd, cap_source FROM s4_caps WHERE campaign_id=?',
                             (campaign_id,)).fetchone()
    if cap is None or cap[1] not in ('default', 'requester'):
        raise IntegrityError('Plafond de campagne absent ou invalide')
    cap_usd = _money(cap[0])
    if not Decimal('0.10') <= cap_usd <= Decimal('100.00'):
        raise IntegrityError('Plafond de campagne hors bornes')
    if status is None or (status[0] is not None and status[0] not in admissions):
        raise IntegrityError('État de campagne sans admission liée')
    if status[0] is not None and status[0] != next(reversed(admissions)):
        raise IntegrityError('Admission courante périmée')
    if status[1] is not None:
        _present(status[1], 'stop reason')
        _date(status[2])
    operation_ids = {row[0] for row in connection.execute(
        'SELECT operation_id FROM s4_attempts WHERE campaign_id=?', (campaign_id,))}
    operations = {op['operation_id']: op for op in store._operations(connection, operation_ids=operation_ids)}
    attempts = []
    for row in connection.execute('SELECT operation_id, execution_id, cell_id, admission_id, request_json, request_sha256, engine_json '
                                  'FROM s4_attempts WHERE campaign_id=? ORDER BY rowid', (campaign_id,)):
        oid, eid, cid, aid, raw, digest, engine_raw = row
        identifier(oid)
        identifier(eid)
        cell = next(c for c in manifest['plan'] if c['cell_id'] == cid)
        request = q._decode(raw, digest)
        engine = json.loads(engine_raw, object_pairs_hook=storage._unique_object)
        if encode(engine) != engine_raw or type(engine) is not dict or not engine:
            raise IntegrityError('Source moteur absente')
        for source, source_hash in engine.items():
            _present(source, 'engine source')
            q._hash(source_hash)
        if request != _request(store, manifest, fingerprint, contract, cell):
            raise IntegrityError('Requête divergente du paquet ou du manifeste')
        authority = admissions[aid]['authority']
        op = operations[oid]
        if (op['phase'] != 'acquisition' or (op['dossier_id'], op['revision']) != (contract['dossier_id'], contract['revision'])
                or op['requested_configuration'] != request['requested_configuration']
                or op['resources'] != [digest] or op['engine_version'] != FORMAT_IDENTITY + ':' + q.digest(engine)
                or op['authority'] != authority['authority_id'] or op['budget_id'] != authority['budget_id']
                or op['reserved_amount'] != authority['reserve_amounts'][cid] or cid not in authority['allowed_cells']):
            raise IntegrityError('Tentative sans intention S1 attribuée')
        emission = connection.execute('SELECT admission_id, emitted_at FROM s4_emissions WHERE operation_id=?', (oid,)).fetchone()
        acquired = connection.execute('SELECT output_piece_id, receipt_sha256, cost_sha256, received_at '
                                      'FROM s4_results WHERE operation_id=?', (oid,)).fetchone()
        if (op['state'] == 'INTENT_RECORDED') != (emission is None) or (op['state'] == 'RECEIVED') != (acquired is not None):
            raise IntegrityError('Chronologie S4 divergente de S1')
        if emission:
            emitted_authority = admissions[emission[0]]['authority']
            if (cid not in emitted_authority['allowed_cells'] or emitted_authority['budget_id'] != op['budget_id']
                    or emitted_authority['reserve_amounts'][cid] != op['reserved_amount']
                    or _date(emission[1]) < _date(op['created_at'])):
                raise IntegrityError('Émission sans autorité liée')
        output_id = None
        if acquired:
            output_id, receipt_hash, cost_hash, received_at = acquired
            _result(op['receipt'], op['observed_cost'])
            if (q.digest(op['receipt']), q.digest(op['observed_cost'])) != (receipt_hash, cost_hash) or _date(received_at) < _date(emission[1]):
                raise IntegrityError('Reçu ou chronologie divergents')
            output = op['receipt']['result']['output']
            if (output is None) != (output_id is None):
                raise IntegrityError('Sortie sans pièce liée')
            if output_id is not None:
                meta = store.get_piece(output_id)
                if ((meta['dossier_id'], meta['revision'], meta['role']) != (contract['dossier_id'], contract['revision'], 'judge')
                        or output_id in {p['id'] for p in contract['reference_pieces']}
                        or store.read_piece(output_id) != output.encode('utf-8')):
                    raise IntegrityError('Sortie brute divergente')
        attempts.append(dict(operation_id=oid, execution_id=eid, cell_id=cid, state=op['state'],
                             admission_id=aid, emission_admission_id=emission[0] if emission else None,
                             input_sha256=digest, engine_source=engine, created_at=op['created_at'],
                             emitted_at=emission[1] if emission else None, received_at=acquired[3] if acquired else None,
                             output_piece_id=output_id, operation=op,
                             attribution_incident=_attribution(op['receipt'], op['requested_configuration']) if acquired else []))
    by_cell = {a['cell_id']: a for a in attempts}
    cells = [{**c, 'state': by_cell[c['cell_id']]['state'] if c['cell_id'] in by_cell else 'NOT_STARTED'}
             for c in manifest['plan']]
    active = admissions.get(status[0])
    latest = next(reversed(admissions.values())) if admissions else None
    budget_id = latest['authority']['budget_id'] if latest else campaign_id
    try:
        budget = store._budget(connection, budget_id)
    except KeyError:
        if latest:
            raise IntegrityError('Enveloppe autorisée absente')
        budget = None
    state = ('ACTIVE' if any(c['state'] == 'EMISSION_POSSIBLE' for c in cells) else
             'BLOCKED' if any(c['state'] == 'AMBIGUOUS' for c in cells) else
             'RECEIVED' if all(c['state'] == 'RECEIVED' for c in cells) else
             'ADMITTED' if active else 'STOPPED' if admissions else 'PREPARED')
    return dict(manifest=manifest, manifest_sha256=fingerprint, task=dict(dossier_id=contract['dossier_id'],
                revision=contract['revision'], version=contract['version'], package_sha256=contract['package_sha256']),
                state=state, admission=active, admissions=list(admissions.values()),
                stop_reason=status[1], stopped_at=status[2], cells=cells, attempts=attempts, budget=budget,
                cap_usd=str(cap_usd), cap_source=cap[1],
                restore_pending=os.path.lexists(store._root / 'restore.json'))


def inspect(store, campaign_id):
    connection = connection_for(store)
    with _transaction(connection):
        return _inspect(store, connection, campaign_id)


def list_campaigns(store):
    connection = connection_for(store)
    with _transaction(connection):
        return [_inspect(store, connection, cid) for (cid,) in connection.execute('SELECT campaign_id FROM s4_campaigns ORDER BY rowid')]


def verify_campaigns(store, connection):
    for (cid,) in connection.execute('SELECT campaign_id FROM s4_campaigns').fetchall():
        _inspect(store, connection, cid)


def _retained_costs(snapshot, store=None, connection=None):
    from .recovery import routing_error
    if snapshot['manifest'].get('financial_cost_policy') != 'retain_reserve':
        return set()
    attempts = list(snapshot['attempts'])
    if snapshot['manifest'].get('recovery_of'):
        from .recovery import parent
        previous, _ = parent(store, connection, snapshot['manifest']['recovery_of'])
        return _retained_costs(previous, store, connection) | {a['operation_id'] for a in attempts
                if a['state'] == 'RECEIVED' and (not a['attribution_incident'] or routing_error(a['operation']))
                and a['operation']['receipt']['result']['emission'] == 'ESTABLISHED'}
    return {a['operation_id'] for a in attempts
            if a['state'] == 'RECEIVED' and (not a['attribution_incident'] or routing_error(a['operation']))
            and a['operation']['receipt']['result']['emission'] == 'ESTABLISHED'}


def _envelope(store, connection, snapshot, authority):
    from .recovery import routing_error
    operations = store._operations(connection)
    budget = store._budget(connection, authority['budget_id'], operations)
    campaign_operations = {row[0] for row in connection.execute('SELECT operation_id FROM s4_attempts')}
    dependent_receipts = [op for op in operations if op['operation_id'] in campaign_operations
                          and op['budget_id'] == authority['budget_id'] and op['receipt'] is not None]
    if budget['currency'] != snapshot['manifest']['cost_basis']['unit']:
        raise BudgetError('Unité du budget différente de la base de coût')
    retained = _retained_costs(snapshot, store, connection)
    if (set(budget['unknown_cost_operations']) - retained or Decimal(budget['available']) < 0
            or any((_attribution(op['receipt'], op['requested_configuration']) and not routing_error(op))
                   or op['receipt']['result']['emission'] != 'ESTABLISHED' for op in dependent_receipts)
            or any(op['budget_id'] == authority['budget_id'] and op['state'] in ('EMISSION_POSSIBLE', 'AMBIGUOUS') for op in operations)
            or any(a['state'] in ('EMISSION_POSSIBLE', 'AMBIGUOUS') or (a['attribution_incident'] and not routing_error(a['operation']))
                   or (a['operation']['observed_cost'] is not None and a['operation']['observed_cost']['status'] == 'UNKNOWN'
                       and a['operation_id'] not in retained)
                   or (a['operation']['receipt'] is not None and a['operation']['receipt']['result']['emission'] != 'ESTABLISHED')
                   for a in snapshot['attempts'])):
        raise BudgetError('Effets, attribution ou solde non établis')
    return budget


def _eligible(store, connection, snapshot, authority, evidence):
    _approved(store, connection, snapshot['manifest']['contract_sha256'], current=True)
    _authority(authority, snapshot['manifest'], snapshot['manifest_sha256'])
    _evidence(evidence, snapshot['manifest'])
    return _envelope(store, connection, snapshot, authority)


def admit(store, campaign_id, authority, evidence, *, owner_launch=False, estimate=None):
    authority, evidence = deepcopy(authority), deepcopy(evidence)
    _intact(store)
    connection = connection_for(store)
    with _transaction(connection, write=True):
        return _admit(store, connection, campaign_id, authority, evidence,
                      owner_launch=owner_launch, estimate=estimate)


def _admit(store, connection, campaign_id, authority, evidence, *, owner_launch=False, estimate=None):
    """Shared admission body; caller owns the enclosing transaction"""
    snapshot = _inspect(store, connection, campaign_id)
    contract = _approved(store, connection, snapshot['manifest']['contract_sha256'], current=True)
    _manifest(snapshot['manifest'], contract, require_data_collection=True)
    if owner_launch:
        session_id = connection.execute('SELECT session_id FROM s2_dossiers WHERE dossier_id=?',
                                        (snapshot['task']['dossier_id'],)).fetchone()[0]
        authority['browser_launch'] = dict(session_id=session_id, estimate=deepcopy(estimate))
    elif 'browser_launch' in authority or estimate is not None:
        raise ValueError('Permission privée explicite de lancement propriétaire requise')
    budget = _eligible(store, connection, snapshot, authority, evidence)
    if 'derived_from' in authority:
        from .recovery import bind_derived
        bind_derived(store, connection, snapshot['manifest'], authority)
    if snapshot['admission'] is not None or authority['purpose'] != ('resume' if snapshot['admissions'] else 'start'):
        raise ConflictError('Arrêt puis autorité de reprise explicite requis')
    if snapshot['admissions'] and authority['budget_id'] != snapshot['admissions'][-1]['authority']['budget_id']:
        raise BudgetError('Une reprise ne change pas l’enveloppe')
    by_cell = {a['cell_id']: a for a in snapshot['attempts']}
    needed = []
    for cid in authority['allowed_cells']:
        attempt = by_cell.get(cid)
        if attempt is None:
            needed.append(_money(authority['reserve_amounts'][cid]))
        elif attempt['state'] != 'INTENT_RECORDED' or attempt['operation']['reserved_amount'] != authority['reserve_amounts'][cid]:
            raise ConflictError('Cellule déjà émise ou réserve divergente')
    if _sum_money(needed) > Decimal(budget['available']):
        raise BudgetError('Enveloppe insuffisante pour les cellules autorisées')
    aid = secrets.token_hex(16)
    record = dict(admission_id=aid, campaign_id=campaign_id, authority=authority, evidence=evidence, created_at=_now())
    connection.execute('INSERT INTO s4_admissions VALUES (?,?,?,?)', (aid, campaign_id, encode(record), q.digest(record)))
    connection.execute('UPDATE s4_status SET admission_id=?, stop_reason=NULL, stopped_at=NULL WHERE campaign_id=?', (aid, campaign_id))
    return record


def reserve(store, campaign_id, cell_id, attempt_id):
    identifier(cell_id)
    identifier(attempt_id)
    _intact(store)
    connection = connection_for(store)
    with _transaction(connection, write=True):
        snapshot = _inspect(store, connection, campaign_id)
        return _reserve(store, connection, snapshot, cell_id, attempt_id)


def _reserve(store, connection, snapshot, cell_id, attempt_id):
    campaign_id = snapshot['manifest']['campaign_id']
    manifest = snapshot['manifest']
    contract = _approved(store, connection, manifest['contract_sha256'])
    _manifest(manifest, contract, require_data_collection=True)
    admission = snapshot['admission']
    if admission is None or cell_id not in admission['authority']['allowed_cells']:
        raise ValueError('Admission explicite de cette cellule requise')
    _eligible(store, connection, snapshot, admission['authority'], admission['evidence'])
    cell = next(c for c in snapshot['cells'] if c['cell_id'] == cell_id)
    if cell['state'] != 'NOT_STARTED':
        raise ConflictError('Tentative déjà enregistrée pour cette cellule')
    request = _request(store, manifest, snapshot['manifest_sha256'], contract, cell)
    engine = _engine()
    digest = q.digest(request)
    authority = admission['authority']
    if manifest.get('funding') == 'requester':
        spent = _sum_money(_money(attempt['operation']['observed_cost']['amount'])
                           for attempt in snapshot['attempts']
                           if attempt['operation']['observed_cost'] is not None
                           and attempt['operation']['observed_cost']['status'] == 'KNOWN')
        if _money(authority['reserve_amounts'][cell_id]) > _money(snapshot['cap_usd']) - spent:
            raise BudgetError('Réserve supérieure au plafond restant')
    operation = dict(operation_id=attempt_id, phase='acquisition', dossier_id=contract['dossier_id'], revision=contract['revision'],
                     authority=authority['authority_id'], engine_version=FORMAT_IDENTITY + ':' + q.digest(engine),
                     requested_configuration=request['requested_configuration'], resources=[digest])
    store._reserve_intent(connection, operation, authority['budget_id'], authority['reserve_amounts'][cell_id],
                          retained_cost_ids=_retained_costs(snapshot, store, connection))
    execution_id = secrets.token_hex(16)
    connection.execute('INSERT INTO s4_attempts VALUES (?,?,?,?,?,?,?,?)',
                       (attempt_id, execution_id, campaign_id, cell_id, admission['admission_id'], encode(request), digest, encode(engine)))
    return dict(operation_id=attempt_id, execution_id=execution_id, cell_id=cell_id, output_piece_id=None)


def _estimate_total(snapshot):
    values = [configuration.get('estimate', {}).get('amount_usd')
              for configuration in snapshot['manifest']['panel']]
    return None if any(value is None for value in values) else _sum_money(_money(value) for value in values)


def _requester_checks(store, connection, snapshot, session_id, access):
    from . import model_catalogue
    from .preparation import Denied, require_qualification
    task = snapshot['task']
    validated = connection.execute(
        'SELECT 1 FROM s2_validations WHERE dossier_id=? AND revision=? '
        'AND package_sha256=? AND session_id=?',
        (task['dossier_id'], task['revision'], task['package_sha256'], session_id)).fetchone() is not None
    qualified, qualification_findings = False, []
    if validated:
        try:
            require_qualification(store, connection, task['dossier_id'], task['revision'])
            qualified = True
        except Denied as error:
            qualification_findings = error.findings or []
    try:
        catalogue = model_catalogue.selection(store)
        selectable = {model['id'] for model in catalogue['models']
                      if model['excluded'] is None and model['route'] is not None}
        missing = [configuration['model'] for configuration in snapshot['manifest']['panel']
                   if configuration['model'] not in selectable]
    except LookupError:
        missing = [configuration['model'] for configuration in snapshot['manifest']['panel']]
    total = _estimate_total(snapshot)
    cap = _money(snapshot['cap_usd'])
    access_detail = {
        'limit_remaining_usd': access.get('limit_remaining_usd'),
        'limit_usd': access.get('limit_usd'),
    }
    return [
        {'key': 'example_validated', 'ok': validated,
         'detail': 'Exemple validé' if validated else 'Validez l’exemple présenté'},
        {'key': 'example_qualified', 'ok': qualified,
         'detail': 'Exemple qualifié' if qualified else 'La qualification de l’exemple est requise',
         'findings': qualification_findings},
        {'key': 'configurations_available', 'ok': not missing,
         'detail': ('Tous les modèles sélectionnés sont disponibles' if not missing else
                    'Modèles à choisir de nouveau : ' + ', '.join(missing))},
        {'key': 'access_connected', 'ok': access.get('status') == 'connected',
         'detail': access_detail},
        {'key': 'estimate_under_cap', 'ok': total is not None and total <= cap,
         'detail': ('Estimation totale : non estimable' if total is None else
                    'Estimation totale : ' + format(total, 'f').replace('.', ',') +
                    ' USD pour un plafond de ' + format(cap, '.2f').replace('.', ',') + ' USD')},
    ]


def set_cap(store, session_id, dossier_id, campaign_id, body, *, access_secret=None,
            access_transport=None):
    from .preparation import owner
    _fields(body, ('cap_usd',), 'plafond')
    raw = body['cap_usd']
    try:
        if type(raw) is not str or re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', raw) is None:
            raise ValueError
        cap = Decimal(raw)
        if not Decimal('0.10') <= cap <= Decimal('100.00'):
            raise ValueError
    except (ValueError, ArithmeticError):
        raise ValueError('Plafond hors bornes : 0,10 à 100 USD') from None
    _intact(store)
    connection = connection_for(store)
    with _transaction(connection, write=True):
        owner(connection, session_id, dossier_id)
        snapshot = _inspect(store, connection, campaign_id)
        if (snapshot['task']['dossier_id'] != dossier_id
                or snapshot['manifest'].get('funding') != 'requester'):
            raise ValueError('Campagne demandeur requise')
        if snapshot['admissions'] or snapshot['attempts']:
            raise ConflictError('Plafond figé au lancement')
        connection.execute('UPDATE s4_caps SET cap_usd=?, cap_source=? WHERE campaign_id=?',
                           (str(cap.quantize(Decimal('0.01'))), 'requester', campaign_id))
    return launch_view(store, session_id, dossier_id, campaign_id,
                       access_secret=access_secret, access_transport=access_transport)


def launch_view(store, session_id, dossier_id, campaign_id, *, access_secret=None,
                access_transport=None):
    from .preparation import owner, page_view
    connection = connection_for(store)
    with _transaction(connection):
        owner(connection, session_id, dossier_id)
        snapshot = _inspect(store, connection, campaign_id)
        if snapshot['task']['dossier_id'] != dossier_id:
            raise ValueError('Campagne étrangère au dossier')
        requester = snapshot['manifest'].get('funding') == 'requester'
    access = {'connected': False, 'status': 'unavailable'}
    if requester:
        from .provider_access import view as access_view
        access = access_view(store, session_id, access_secret, access_transport, refresh=False)
    with _transaction(connection):
        snapshot = _inspect(store, connection, campaign_id)
        if snapshot['task']['dossier_id'] != dossier_id:
            raise ValueError('Campagne étrangère au dossier')
        projected = _projected(store, connection, campaign_id, snapshot)
        contract = _approved(store, connection, snapshot['manifest']['contract_sha256'])
        criteria = {key: contract['specification'][key]
                    for key in ('result_expected', 'obligations', 'eliminatory_errors', 'limits')}
        if requester:
            checks = _requester_checks(store, connection, snapshot, session_id, access)
            total = _estimate_total(snapshot)
            return page_view(dict(
                kind='campaign_launch', dossier_id=dossier_id, campaign=projected,
                criteria=criteria, checks=checks,
                launchable=all(check['ok'] for check in checks)
                and not snapshot['admissions'] and not snapshot['attempts'],
                cap_usd=snapshot['cap_usd'], cap_source=snapshot['cap_source'],
                estimate_total_usd=None if total is None else str(total), access=access))
        admission = snapshot['admission']
        grant = admission['authority'].get('browser_launch') if admission else None
        eligible = False
        if grant and grant['session_id'] == session_id:
            try:
                from .model_catalog import require_current
                for configuration in snapshot['manifest']['panel']:
                    require_current(configuration)
                _eligible(store, connection, snapshot, admission['authority'], admission['evidence'])
                eligible = not snapshot['restore_pending'] and not snapshot['attempts']
            except (ValueError, ConflictError, BudgetError):
                pass
        return page_view(dict(kind='campaign_launch', dossier_id=dossier_id, campaign=projected,
                         criteria=criteria, can_launch=eligible,
                         admission_id=admission['admission_id'] if grant else None,
                         estimate=grant['estimate'] if grant else None))


def launch(store, session_id, dossier_id, campaign_id, body, *, access_secret=None, access_transport=None):
    from .preparation import owner, Denied
    _intact(store)
    connection = connection_for(store)
    with _transaction(connection):
        owner(connection, session_id, dossier_id)
        preview = _inspect(store, connection, campaign_id)
    requester_funding = preview['manifest'].get('funding', 'operator') == 'requester'
    requester = requester_funding and not preview['admissions']
    fields = ('manifest_version', 'frozen_at', 'confirm') if requester else (
        'manifest_version', 'frozen_at', 'admission_id', 'confirm')
    _fields(body, fields, 'lancement')
    if body['confirm'] != 'yes':
        raise ValueError('Confirmation requise')
    access = None
    if requester:
        from .provider_access import view as access_view
        access = access_view(store, session_id, access_secret, access_transport, refresh=False)
        with _transaction(connection):
            snapshot = _inspect(store, connection, campaign_id)
            failed = next((check for check in _requester_checks(
                store, connection, snapshot, session_id, access) if not check['ok']), None)
        if failed:
            raise Denied(failed['key'])
    if requester_funding:
        from .provider_access import key_for_session
        key_for_session(store, session_id, access_secret, access_transport)
    if requester:
        access = access_view(store, session_id, access_secret, access_transport, refresh=False)
    with _transaction(connection, write=True):
        owner(connection, session_id, dossier_id)
        snapshot = _inspect(store, connection, campaign_id)
        if snapshot['task']['dossier_id'] != dossier_id:
            raise Denied('Campagne non autorisée')
        if requester:
            if (body['manifest_version'], body['frozen_at']) != (
                    snapshot['manifest']['version'], snapshot['manifest']['conditions']['frozen_at']):
                raise ConflictError('Conditions périmées')
            checks = _requester_checks(store, connection, snapshot, session_id, access)
            failed = next((check for check in checks if not check['ok']), None)
            if failed:
                raise Denied(failed['key'])
            if snapshot['admissions'] or snapshot['attempts']:
                raise ConflictError('Lancement déjà enregistré')
            cap = snapshot['cap_usd']
            try:
                connection.execute('INSERT INTO budgets VALUES (?,?,?)',
                                   (campaign_id, cap, 'USD'))
            except sqlite3.IntegrityError as error:
                raise ConflictError('Enveloppe demandeur déjà créée') from error
            reserve_amounts = {
                cell['cell_id']: next(configuration['estimate']['amount_usd']
                                      for configuration in snapshot['manifest']['panel']
                                      if configuration['id'] == cell['configuration_id'])
                for cell in snapshot['manifest']['plan']}
            identity = 'requester:' + session_id
            authority = dict(
                actor='Ayo', authority_id=identity, purpose='start',
                manifest_sha256=snapshot['manifest_sha256'],
                execution_authority=identity, candidate_authority=identity,
                budget_authority=identity, budget_id=campaign_id,
                allowed_cells=list(snapshot['manifest']['attempt_policy']['order']),
                reserve_amounts=reserve_amounts)
            fetched_at = snapshot['manifest']['panel'][0]['estimate']['fetched_at']
            channels = {}
            for configuration in snapshot['manifest']['panel']:
                channel = {key: configuration[key]
                           for key in {'revision', 'channel_id', 'route'}
                           | set(configuration['required_observations'])}
                channel.update(available=True, proof='Relevé de modèles conservé du ' + fetched_at)
                channels[configuration['id']] = channel
            evidence = dict(
                pi_sha256=snapshot['manifest']['conditions']['pi']['sha256'],
                context_sha256=snapshot['manifest']['conditions']['context_sha256'],
                channels=channels,
                confinement=dict(code_execution=False,
                                 proof='Campagne sans outil, paquet ni compétence exécutable'))
            total = _estimate_total(snapshot)
            estimate = dict(amount=str(total), currency='USD',
                            assumptions='Somme des estimations figées de chaque configuration',
                            source='Relevé de modèles conservé du ' + fetched_at)
            admission = _admit(store, connection, campaign_id, authority, evidence,
                               owner_launch=True, estimate=estimate)
            snapshot = _inspect(store, connection, campaign_id)
            attempts = []
            for cell in snapshot['manifest']['attempt_policy']['order']:
                attempt_id = 'web-' + q.digest(
                    [campaign_id, cell, admission['admission_id']])[:40]
                _reserve(store, connection, snapshot, cell, attempt_id)
                attempts.append(attempt_id)
            return attempts
        admission = snapshot['admission']
        if not admission:
            raise Denied('Campagne non autorisée')
        grant = admission['authority'].get('browser_launch')
        if not grant or grant['session_id'] != session_id:
            raise Denied('Lancement non autorisé')
        if (body['manifest_version'], body['frozen_at'], body['admission_id']) != (
                snapshot['manifest']['version'], snapshot['manifest']['conditions']['frozen_at'], admission['admission_id']):
            raise ConflictError('Conditions périmées')
        # Existing intentions are a receipt, never permission to redispatch a worker
        if snapshot['attempts']:
            return []
        if (snapshot['manifest'].get('funding', 'operator') == 'requester'
                and connection.execute("SELECT status FROM s2_provider_access WHERE session_id=?",
                                       (session_id,)).fetchone() != ('connected',)):
            raise Denied('ACCESS_REQUIRED')
        _eligible(store, connection, snapshot, admission['authority'], admission['evidence'])
        attempts = []
        for cell in snapshot['manifest']['attempt_policy']['order']:
            if cell in admission['authority']['allowed_cells']:
                aid = 'web-' + q.digest([campaign_id, cell, admission['admission_id']])[:40]
                _reserve(store, connection, snapshot, cell, aid)
                attempts.append(aid)
        return attempts


def execute_launch(data, attempts, transport=None, *, transport_factory=None,
                   access_secret=None, access_transport=None):
    for attempt_id in attempts:
        try:
            execute(data, attempt_id, transport, transport_factory=transport_factory,
                    access_secret=access_secret, access_transport=access_transport)
        except (ValueError, ConflictError, BudgetError, IntegrityError):
            # An interruption leaves the remaining intentions for private inspection
            break


def _recovery_descendants(connection, campaign_id):
    children = {}
    for cid, raw, digest in connection.execute(
            'SELECT campaign_id, manifest_json, manifest_sha256 FROM s4_campaigns'):
        parent_oid = q._decode(raw, digest).get('recovery_of')
        if parent_oid:
            children.setdefault(parent_oid, []).append(cid)
    found = []
    pending = [row[0] for row in connection.execute(
        'SELECT operation_id FROM s4_attempts WHERE campaign_id=?', (campaign_id,))]
    seen = set()
    while pending:
        oid = pending.pop()
        for cid in children.get(oid, ()):
            if cid in seen:
                continue
            seen.add(cid)
            found.append(cid)
            pending.extend(row[0] for row in connection.execute(
                'SELECT operation_id FROM s4_attempts WHERE campaign_id=?', (cid,)))
    return found


def stop(store, campaign_id, reason='OPERATOR_STOP'):
    _present(reason, 'stop reason')
    connection = connection_for(store)
    with _transaction(connection, write=True):
        if not connection.execute('SELECT 1 FROM s4_campaigns WHERE campaign_id=?', (campaign_id,)).fetchone():
            raise KeyError(campaign_id)
        now = _now()
        connection.execute('UPDATE s4_status SET admission_id=NULL, stop_reason=?, stopped_at=? WHERE campaign_id=?',
                           (reason, now, campaign_id))
        for descendant in _recovery_descendants(connection, campaign_id):
            connection.execute('UPDATE s4_status SET admission_id=NULL, stop_reason=?, stopped_at=? '
                               'WHERE campaign_id=? AND admission_id IS NOT NULL',
                               (reason, now, descendant))


def close_admission(store, reason):
    """Close every campaign on maintenance/startup, without claiming quiescence."""
    _present(reason, 'stop reason')
    connection = connection_for(store)
    with _transaction(connection, write=True):
        connection.execute('UPDATE s4_status SET admission_id=NULL, stop_reason=?, stopped_at=? WHERE admission_id IS NOT NULL', (reason, _now()))


def execute(data, attempt_id, transport=None, *, transport_factory=None,
            access_secret=None, access_transport=None):
    """One explicit worker, one durable boundary, one callback; never an implicit retry."""
    if not callable(transport) and not callable(transport_factory):
        raise ValueError('Transport injecté par le lanceur de confiance requis')
    from .runtime import worker_lock
    received = False
    with closing(storage.Store(data)) as store, worker_lock(store, shared=True):
        _intact(store)
        connection = connection_for(store)
        requester_key = None
        with _transaction(connection):
            row = connection.execute('SELECT campaign_id FROM s4_attempts WHERE operation_id=?',
                                     (attempt_id,)).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            preview = _inspect(store, connection, row[0])
            if preview['manifest'].get('funding', 'operator') == 'requester':
                session_id = connection.execute('SELECT session_id FROM s2_dossiers WHERE dossier_id=?',
                                                (preview['task']['dossier_id'],)).fetchone()[0]
        if preview['manifest'].get('funding', 'operator') == 'requester':
            from .provider_access import key_for_session
            requester_key = key_for_session(store, session_id, access_secret, access_transport)
        with _transaction(connection, write=True):
            row = connection.execute('SELECT campaign_id FROM s4_attempts WHERE operation_id=?', (attempt_id,)).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            snapshot = _inspect(store, connection, row[0])
            attempt = next(a for a in snapshot['attempts'] if a['operation_id'] == attempt_id)
            admission = snapshot['admission']
            if attempt['state'] != 'INTENT_RECORDED' or admission is None or attempt['cell_id'] not in admission['authority']['allowed_cells']:
                raise ConflictError('Tentative non admise ou déjà émise')
            _eligible(store, connection, snapshot, admission['authority'], admission['evidence'])
            if attempt['engine_source'] != _engine():
                raise ConflictError('Source moteur modifiée depuis la réservation')
            order = snapshot['manifest']['attempt_policy']['order']
            states = {c['cell_id']: c['state'] for c in snapshot['cells']}
            if any(states[cid] != 'RECEIVED' for cid in order[:order.index(attempt['cell_id'])]):
                raise ConflictError('Ordre de tentative non respecté')
            raw = connection.execute('SELECT request_json FROM s4_attempts WHERE operation_id=?', (attempt_id,)).fetchone()[0]
            request = json.loads(raw)
            closed_request = _transport_view(request)
            closed_operation = _transport_operation(attempt['operation'])
            if transport_factory is not None:
                if snapshot['manifest'].get('funding', 'operator') == 'requester':
                    from .provider_access import decrypt
                    row = connection.execute("SELECT key_cipher FROM s2_provider_access WHERE session_id=? AND status='connected'",
                                             (session_id,)).fetchone()
                    if (row is None or not hmac.compare_digest(
                            decrypt(access_secret, row[0]).encode(), requester_key.encode())):
                        from .preparation import Denied
                        raise Denied('ACCESS_REQUIRED')
                    transport = transport_factory(
                        closed_request['requested_configuration']['channel_id'], requester_key)
                else:
                    transport = transport_factory(
                        closed_request['requested_configuration']['channel_id'])
            if not callable(transport):
                raise ValueError('Transport injecté par le lanceur de confiance requis')
            if hasattr(transport, 'prepare'):
                transport.prepare(deepcopy(closed_operation), deepcopy(closed_request))
            connection.execute('INSERT INTO s4_emissions VALUES (?,?,?)', (attempt_id, admission['admission_id'], _now()))
            # Same S1 transition as mark_emission_possible, in the transaction that
            # also freezes the admission actually used by this worker
            operation = store._operation_for_update(connection, attempt_id, ('INTENT_RECORDED',))
            connection.execute("UPDATE operations SET state='EMISSION_POSSIBLE' WHERE operation_id=?", (attempt_id,))
        operation['state'] = 'EMISSION_POSSIBLE'
        try:
            response = deepcopy(transport(_transport_operation(operation), deepcopy(closed_request)))
            _fields(response, ('receipt', 'cost'), 'transport response')
            receipt, cost = response['receipt'], response['cost']
            receipt['resources_seen'] = [p['id'] for p in request['pieces']]
            _result(receipt, cost)
            with _transaction(connection, write=True):
                # A stop during the callback must not discard the late receipt
                store._record_receipt(connection, attempt_id, receipt, cost)
                output = receipt['result']['output']
                output_id = None
                if output is not None:
                    output_id = 'output-' + secrets.token_hex(16)
                    store._put_piece(connection, operation['dossier_id'], operation['revision'], output_id,
                                     name='Sortie brute ' + attempt_id, role='judge', media_type='text/plain; charset=utf-8', content=output.encode('utf-8'))
                connection.execute('INSERT INTO s4_results VALUES (?,?,?,?,?)',
                                   (attempt_id, output_id, q.digest(receipt), q.digest(cost), _now()))
                incomplete = (_attribution(receipt, request['requested_configuration'])
                              or (cost['status'] == 'UNKNOWN'
                                  and snapshot['manifest'].get('financial_cost_policy') != 'retain_reserve')
                              or receipt['result']['emission'] != 'ESTABLISHED')
                if incomplete:
                    connection.execute('UPDATE s4_status SET admission_id=NULL, stop_reason=?, stopped_at=? WHERE campaign_id=?',
                                       ('ACQUISITION_EVIDENCE_INCOMPLETE', _now(), snapshot['manifest']['campaign_id']))
                elif snapshot['manifest'].get('funding') == 'requester':
                    operation_ids = {row[0] for row in connection.execute(
                        'SELECT operation_id FROM s4_attempts WHERE campaign_id=?',
                        (snapshot['manifest']['campaign_id'],))}
                    spent = _sum_money(_money(operation['observed_cost']['amount'])
                                       for operation in store._operations(
                                           connection, operation_ids=operation_ids)
                                       if operation['observed_cost'] is not None
                                       and operation['observed_cost']['status'] == 'KNOWN')
                    if spent >= _money(snapshot['cap_usd']):
                        connection.execute(
                            'UPDATE s4_status SET admission_id=NULL, stop_reason=?, stopped_at=? '
                            'WHERE campaign_id=?',
                            ('CAP_REACHED', _now(), snapshot['manifest']['campaign_id']))
            received = True
        except Exception:
            # Exception text can contain private bytes. Preserve a fixed technical
            # reason; neither an unusable response nor an exception settles cost
            with _transaction(connection, write=True):
                op = store._operation_for_update(connection, attempt_id, ('EMISSION_POSSIBLE', 'AMBIGUOUS'))
                if op['state'] == 'EMISSION_POSSIBLE':
                    connection.execute("UPDATE operations SET state='AMBIGUOUS', ambiguity_reason=? WHERE operation_id=?",
                                       ('ACQUISITION_RECEIPT_NOT_VERIFIED', attempt_id))
                connection.execute('UPDATE s4_status SET admission_id=NULL, stop_reason=?, stopped_at=? WHERE campaign_id=?',
                                   ('ACQUISITION_RECEIPT_NOT_VERIFIED', _now(), snapshot['manifest']['campaign_id']))
    if received:
        from .recovery import continue_preauthorized
        continue_preauthorized(data, attempt_id, transport,
                               transport_factory=transport_factory)


def _projected(store, connection, campaign_id, snapshot):
    """Allowlisted session-owner view of one already verified snapshot"""
    manifest = snapshot['manifest']
    admission = snapshot['admission']
    latest = snapshot['admissions'][-1] if snapshot['admissions'] else None
    attempts = []
    for attempt in snapshot['attempts']:
        op = attempt['operation']
        receipt = op['receipt']
        observed = receipt['observed_configuration'] if receipt else None
        sources = (observed or {}).get('sources', {})
        attempts.append({key: attempt[key] for key in ('operation_id', 'execution_id', 'cell_id', 'state', 'created_at', 'emitted_at', 'received_at', 'attribution_incident')} | dict(
            observed_configuration={field: (observed or {}).get(field) if (observed or {}).get(field) is not None else 'INCONNU' for field in _OBSERVED},
            observation_sources={field: sources.get(field, 'INCONNU') if type(sources) is dict else 'INCONNU' for field in _OBSERVED},
            observed_cost=op['observed_cost'], receipt_id=receipt['receipt_id'] if receipt else None,
            incident=receipt['result']['incident'] if receipt else None,
            emission=receipt['result']['emission'] if receipt else 'INCONNU'))
    budget = deepcopy(snapshot['budget'])
    if budget:
        # S1 retains its arithmetic remainder as evidence. It is not a known
        # balance when a receipt/cost is missing or a call may be active
        unresolved = budget['unknown_cost_operations'] or any(
            op['budget_id'] == budget['budget_id'] and op['state'] in ('EMISSION_POSSIBLE', 'AMBIGUOUS')
            for op in store._operations(connection))
        budget['balance_status'] = 'INCONNU' if unresolved else 'KNOWN'
        if unresolved:
            budget['available'] = None
    return dict(recovery_of=manifest.get('recovery_of'), campaign_id=campaign_id, manifest_sha256=snapshot['manifest_sha256'],
                contract_sha256=manifest['contract_sha256'], task=snapshot['task'], version=manifest['version'],
                panel=manifest['panel'], conditions=manifest['conditions'], cases=manifest['cases'],
                cost_basis=manifest['cost_basis'], cells=snapshot['cells'], attempts=attempts, budget=budget,
                state=snapshot['state'], admission_open=admission is not None and not snapshot['restore_pending'],
                allowed_cells=admission['authority']['allowed_cells'] if admission else [],
                reserve_amounts=latest['authority']['reserve_amounts'] if latest else None,
                missing_authorities=[] if admission else ['exécution', 'appels candidats', 'budget'],
                stop_reason=snapshot['stop_reason'], restore_pending=snapshot['restore_pending'])


def projection(store, connection, dossier_id, campaign_id=None):
    """Allowlisted session-owner view; no raw output, authority evidence or judge pieces."""
    query = ('SELECT c.campaign_id FROM s4_campaigns c JOIN '
             '(SELECT contract_sha256,dossier_id FROM s3_contracts UNION ALL '
             'SELECT contract_sha256,dossier_id FROM s2_comparison_contracts) q USING(contract_sha256) '
             'WHERE q.dossier_id=?')
    parameters = (dossier_id,) if campaign_id is None else (dossier_id, campaign_id)
    if campaign_id is not None:
        query += ' AND c.campaign_id=?'
    rows = connection.execute(query + ' ORDER BY c.rowid', parameters).fetchall()
    return [_projected(store, connection, cid, _inspect(store, connection, cid)) for (cid,) in rows]
