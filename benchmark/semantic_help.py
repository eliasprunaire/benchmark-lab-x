"""Aide sémantique facultative commune au répertoire et au catalogue.

Seule une soumission volontaire atteint TypeSafe. La consultation ordinaire ne
passe par aucun relais, ne résout aucun secret et n'émet aucun appel. Chaque
usage porte ses questions versionnées côté serveur et son activation propre ;
l'enveloppe, les réserves, la concurrence et la fréquence sont communes.

Aucun texte recherché n'est conservé : les reçus ne portent que l'usage, la
version des questions, les identifiants de notices et de questions, le modèle,
les jetons, le tarif daté, l'incident et le coût.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import re
import time
from typing import Any, Protocol

from . import storage
from .storage import _money, _strict_json as encode, _sum_money, _transaction, _unique_object

FORMAT = 'benchmark-lab-x/semantic-help/v1'
PROFILE_FORMAT = 'benchmark-lab-x/semantic-help-profile/v1'
MODEL = 'jev-1.13.0'
ENDPOINT = 'https://api.typesafe.ai/v1/systemone'
# Bornes conservatrices : un jeton d'un tokenizer à octets ne dépasse pas un octet UTF-8
REQUEST_CONTEXT_LIMIT = 65536
QUESTION_CONTEXT_LIMIT = 32768
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 10
MAX_CONCURRENT_EMISSIONS = 2
MIN_EMISSION_INTERVAL = 1
BUDGET_ID = 'typesafe-semantic-aid'
BUDGET_USD = '1.00'
CURRENCY = 'USD'
# Délai au-delà duquel un fil émetteur ne peut plus écrire : sa réponse tardive est ignorée
STALE_EMISSION = timedelta(seconds=12)
USAGES = ('repertoire', 'epreuves')
QUESTIONS_VERSIONS = {'repertoire': 'repertoire-v1', 'epreuves': 'epreuves-v1'}
NOTICE_FIELDS = ('name', 'category', 'work', 'scope', 'limits', 'version')
MAX_NOTICES = 200
REQUEST_ID = re.compile(r'[0-9a-f]{32}')

TABLE_SQL = """CREATE TABLE s2_semantic_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    usage TEXT NOT NULL CHECK(usage IN ('repertoire', 'epreuves')),
    questions_version TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('EMITTING', 'RECEIVED')),
    notice_ids_json TEXT NOT NULL,
    question_ids_json TEXT NOT NULL,
    reserve_amount TEXT NOT NULL,
    tariff_json TEXT,
    model_requested TEXT NOT NULL,
    model_observed TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_status TEXT NOT NULL CHECK(cost_status IN ('KNOWN', 'UNKNOWN')),
    cost_amount TEXT,
    cost_currency TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    emitted_at TEXT NOT NULL,
    received_at TEXT,
    CHECK(state = 'EMITTING' OR received_at IS NOT NULL),
    CHECK(cost_status = 'UNKNOWN' OR cost_amount IS NOT NULL)
)"""

QUESTION_SETS = {
    'repertoire-v1': {
        'question': 'Cette notice d’évaluation externe aide-t-elle directement la formulation soumise ?',
        'criteria': {
            'correspond': 'Le travail mesuré par la notice couvre directement la formulation soumise.',
            'partiel': 'Le travail mesuré recouvre une partie de la demande ou un usage voisin utile, '
                       'sans couvrir la formulation entière.',
            'sans_correspondance': 'Le travail mesuré ne porte pas sur la formulation soumise.',
        },
    },
    'epreuves-v1': {
        'question': 'Cette épreuve éditoriale et le travail qu’elle mesure aident-ils directement '
                    'la formulation soumise ?',
        'criteria': {
            'correspond': 'Le travail évalué par l’épreuve couvre directement la formulation soumise.',
            'partiel': 'Le travail évalué recouvre une partie de la demande ou un usage voisin utile, '
                       'sans couvrir la formulation entière.',
            'sans_correspondance': 'Le travail évalué ne porte pas sur la formulation soumise.',
        },
    },
}


@dataclass(frozen=True)
class Tariff:
    input_usd_per_million_tokens: Decimal
    currency: str
    effective_at: str
    source: str


@dataclass(frozen=True)
class Usage:
    enabled: bool
    threshold: Decimal | None


@dataclass(frozen=True)
class Configuration:
    tariff: Tariff | None
    usages: dict[str, Usage]


class Transport(Protocol):
    def post(self, wire: str) -> dict[str, Any]:
        ...


class Unavailable(Exception):
    def __init__(self, reason: str, code: int = 503):
        super().__init__(reason)
        self.reason = reason
        self.code = code


class InvalidRequest(ValueError):
    pass


class InvalidResponse(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _decimal(value, label: str) -> Decimal:
    if type(value) is not str:
        raise ValueError('Valeur décimale textuelle requise : ' + label)
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError('Décimale invalide : ' + label) from error
    if not amount.is_finite():
        raise ValueError('Décimale non finie : ' + label)
    return amount


def _probability(value, label: str) -> Decimal:
    amount = _decimal(value, label)
    if not 0 <= amount <= 1:
        raise ValueError('Probabilité hors de [0, 1] : ' + label)
    return amount


def configure(document) -> Configuration:
    """Profil fermé : activation par usage et tarif daté, sans modèle substituable"""
    if type(document) is not dict or set(document) != {'format', 'tariff', 'usages'}:
        raise ValueError('Profil d’aide sémantique fermé requis')
    if document['format'] != PROFILE_FORMAT:
        raise ValueError('Format de profil d’aide sémantique inconnu')
    tariff = document['tariff']
    if tariff is not None:
        if type(tariff) is not dict or set(tariff) != {
                'input_usd_per_million_tokens', 'currency', 'effective_at', 'source'}:
            raise ValueError('Tarif fermé requis')
        per_million = _decimal(tariff['input_usd_per_million_tokens'], 'tarif')
        if per_million <= 0 or tariff['currency'] != CURRENCY:
            raise ValueError('Tarif USD positif requis')
        effective_at = tariff['effective_at']
        if (type(effective_at) is not str
                or re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', effective_at) is None
                or not _valid_date(effective_at)):
            raise ValueError('Date de tarif au format AAAA-MM-JJ requise')
        source = tariff['source']
        if (type(source) is not str or not source.startswith('https://') or len(source) > 500
                or any(character.isspace() or ord(character) < 32 for character in source)):
            raise ValueError('Source datée du tarif requise')
        tariff = Tariff(per_million, CURRENCY, effective_at, source)
    usages = document['usages']
    if type(usages) is not dict or set(usages) - set(USAGES):
        raise ValueError('Usages d’aide sémantique inconnus')
    checked: dict[str, Usage] = {}
    for usage in USAGES:
        value = usages.get(usage)
        if value is None:
            checked[usage] = Usage(False, None)
            continue
        if type(value) is not dict or set(value) != {'enabled', 'suggestion_threshold'}:
            raise ValueError('Usage d’aide sémantique fermé requis')
        if type(value['enabled']) is not bool:
            raise ValueError('Activation invalide')
        threshold = value['suggestion_threshold']
        if value['enabled'] and threshold is None:
            raise ValueError('Seuil de suggestion requis pour un usage actif')
        checked[usage] = Usage(value['enabled'], None if threshold is None
                               else _probability(threshold, 'seuil'))
    return Configuration(tariff, checked)


def load_profile(path) -> Configuration:
    """Profil JSON local, borné et fermé ; aucun profil par défaut n'active le service"""
    from pathlib import Path
    source = Path(path)
    with source.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Profil d’aide sémantique hors limites')
    try:
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as error:
        raise ValueError('Profil d’aide sémantique illisible') from error
    return configure(document)


def _valid_date(value: str) -> bool:
    try:
        return datetime.strptime(value, '%Y-%m-%d').date().isoformat() == value
    except ValueError:
        return False


def _request_id(value) -> str:
    if type(value) is not str or REQUEST_ID.fullmatch(value) is None:
        raise InvalidRequest('Identité de demande invalide')
    return value


def _closed_notices(values) -> list[tuple[str, dict]]:
    if type(values) is not list or len(values) > MAX_NOTICES:
        raise InvalidRequest('Notices publiques admissibles requises')
    closed, seen = [], set()
    for index, item in enumerate(values, 1):
        if type(item) is not dict or 'id' not in item:
            raise InvalidRequest('Notice publique fermée requise')
        notice_id = item['id']
        if (type(notice_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', notice_id)
                or notice_id in seen):
            raise InvalidRequest('Identifiant de notice invalide')
        seen.add(notice_id)
        entry: dict[str, str] = {'ref': 'n' + str(index)}
        for field in NOTICE_FIELDS:
            value = item.get(field)
            if value is None:
                continue
            if type(value) is not str or not value or len(value) > 2000 or '\0' in value:
                raise InvalidRequest('Champ public de notice invalide')
            entry[field] = value
        if 'name' not in entry:
            raise InvalidRequest('Nom public de notice requis')
        closed.append((notice_id, entry))
    return closed


def _projection(usage: str, formulation, closed: list[tuple[str, dict]]
                ) -> tuple[str, dict[str, dict], list[tuple[str, dict]]]:
    if type(formulation) is not str or not formulation.strip():
        raise InvalidRequest('Formulation requise')
    if len(formulation) > REQUEST_CONTEXT_LIMIT:
        raise Unavailable('CONTEXT', 413)
    questions_set = QUESTION_SETS[QUESTIONS_VERSIONS[usage]]
    state = {'formulation': formulation, 'notices': [entry for _, entry in closed]}
    questions = {}
    for index, (_, entry) in enumerate(closed, 1):
        questions['n' + str(index)] = {
            'type': 'choice',
            'instructions': {'notice': entry['ref'], 'question': questions_set['question']},
            'criteria': dict(questions_set['criteria']),
        }
    wire = encode({'model': MODEL, 'state': state, 'questions': questions})
    if len(wire.encode()) > REQUEST_CONTEXT_LIMIT:
        raise Unavailable('CONTEXT', 413)
    for question_id, question in questions.items():
        single = encode({'model': MODEL, 'state': state,
                         'questions': {question_id: question}})
        if len(single.encode()) > QUESTION_CONTEXT_LIMIT:
            raise Unavailable('CONTEXT', 413)
    return wire, questions, closed


def schema_objects():
    return [('table', 's2_semantic_requests', 's2_semantic_requests', TABLE_SQL),
            ('index', 'sqlite_autoindex_s2_semantic_requests_1', 's2_semantic_requests', None)]


def initialize(data) -> dict:
    store = storage.Store(data)
    try:
        connection = store._connection_checked()
        with _transaction(connection, write=True):
            storage._check_schema(connection)
            if not connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='s2_semantic_requests'").fetchone():
                connection.execute(TABLE_SQL)
            row = connection.execute('SELECT limit_amount,currency FROM budgets WHERE budget_id=?',
                                     (BUDGET_ID,)).fetchone()
            if row is None:
                connection.execute('INSERT INTO budgets VALUES (?,?,?)', (BUDGET_ID, BUDGET_USD, CURRENCY))
            elif tuple(row) != (BUDGET_USD, CURRENCY):
                raise storage.SchemaError('enveloppe d’aide sémantique divergente')
            storage._check_schema(connection)
    finally:
        store.close()
    return {'format_identity': FORMAT}


def _request(connection, request_id: str):
    return connection.execute(
        'SELECT request_id,usage,questions_version,state,notice_ids_json,question_ids_json,'
        'reserve_amount,tariff_json,model_requested,model_observed,input_tokens,output_tokens,'
        'cost_status,cost_amount,cost_currency,outcome_json,created_at,emitted_at,received_at '
        'FROM s2_semantic_requests WHERE request_id=?', (request_id,)).fetchone()


def _row(row) -> dict:
    keys = ('request_id', 'usage', 'questions_version', 'state', 'notice_ids', 'question_ids',
            'reserve_amount', 'tariff', 'model_requested', 'model_observed', 'input_tokens',
            'output_tokens', 'cost_status', 'cost_amount', 'cost_currency', 'outcome',
            'created_at', 'emitted_at', 'received_at')
    value = dict(zip(keys, row))
    for key in ('notice_ids', 'question_ids', 'outcome'):
        value[key] = json.loads(value[key], object_pairs_hook=_unique_object)
    value['tariff'] = None if value['tariff'] is None else json.loads(
        value['tariff'], object_pairs_hook=_unique_object)
    return value


def _budget(connection) -> dict:
    row = connection.execute('SELECT limit_amount,currency FROM budgets WHERE budget_id=?',
                             (BUDGET_ID,)).fetchone()
    if row is None or tuple(row) != (BUDGET_USD, CURRENCY):
        raise Unavailable('FINANCE')
    rows = connection.execute(
        'SELECT reserve_amount,cost_status,cost_amount FROM s2_semantic_requests').fetchall()
    reserved = _sum_money(_money(value) for value, status, _ in rows if status == 'UNKNOWN')
    spent = _sum_money(_money(value) for _, status, value in rows
                       if status == 'KNOWN' and value is not None)
    available = Decimal(BUDGET_USD) - reserved - spent
    return {'budget_id': BUDGET_ID, 'limit': BUDGET_USD, 'currency': CURRENCY,
            'reserved': str(reserved), 'spent': str(spent), 'available': str(available),
            'provider_managed': False}


def _reserve(tariff: Tariff, wire: str) -> str:
    return str(Decimal(len(wire.encode())) * tariff.input_usd_per_million_tokens / Decimal(1_000_000))


def _cost(tariff: Tariff, input_tokens: int) -> str:
    return str(Decimal(input_tokens) * tariff.input_usd_per_million_tokens / Decimal(1_000_000))


def _close_rows(connection, now: datetime, *, stale_only: bool) -> int:
    outcome = encode({'status': 'unavailable', 'reason': 'INTERRUPTED', 'answers': {}})
    if stale_only:
        limit = (now - STALE_EMISSION).isoformat()
        cursor = connection.execute(
            "UPDATE s2_semantic_requests SET state='RECEIVED', received_at=?, outcome_json=? "
            "WHERE state='EMITTING' AND emitted_at<?", (now.isoformat(), outcome, limit))
    else:
        cursor = connection.execute(
            "UPDATE s2_semantic_requests SET state='RECEIVED', received_at=?, outcome_json=? "
            "WHERE state='EMITTING'", (now.isoformat(), outcome))
    return cursor.rowcount


def close_interrupted(store) -> int:
    """Clore sans réémission les émissions interrompues par un arrêt du processus"""
    connection = store._connection_checked()
    if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='s2_semantic_requests'").fetchone():
        return 0
    with _transaction(connection, write=True):
        return _close_rows(connection, _now(), stale_only=False)


def _unavailable(reason: str) -> dict:
    return {'status': 'unavailable', 'reason': reason, 'suggestions': [], 'answers': {}}


def _reject_constant(_):
    raise ValueError('nombre non fini')


def _as_decimal(value):
    if type(value) is bool:
        return None
    if type(value) is Decimal:
        return value
    if type(value) is int:
        return Decimal(value)
    return None


def _validated(raw, complete, question_ids: list[str], usage: str) -> dict:
    if complete is not True or type(raw) is not bytes or len(raw) > MAX_RESPONSE_BYTES:
        raise InvalidResponse('réponse incomplète')
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_float=Decimal,
                           parse_constant=_reject_constant)
    except (ValueError, TypeError, UnicodeError) as error:
        raise InvalidResponse('corps illisible') from error
    if type(value) is not dict or set(value) != {'model', 'answers', 'usage'}:
        raise InvalidResponse('structure fermée requise')
    if value['model'] != MODEL:
        raise InvalidResponse('modèle observé divergent')
    answers = value['answers']
    if type(answers) is not dict or set(answers) != set(question_ids):
        raise InvalidResponse('réponses incomplètes')
    criteria = QUESTION_SETS[QUESTIONS_VERSIONS[usage]]['criteria']
    checked = {}
    for question_id in question_ids:
        answer = answers[question_id]
        if (type(answer) is not dict
                or set(answer) != {'type', 'choice', 'confidence', 'probabilities'}
                or answer['type'] != 'choice' or answer['choice'] not in criteria):
            raise InvalidResponse('réponse de choix invalide')
        confidence = _as_decimal(answer['confidence'])
        if confidence is None or not confidence.is_finite() or not 0 <= confidence <= 1:
            raise InvalidResponse('confiance non finie')
        probabilities = answer['probabilities']
        if type(probabilities) is not dict or set(probabilities) != set(criteria):
            raise InvalidResponse('probabilités incomplètes')
        total = Decimal(0)
        for label in criteria:
            probability = _as_decimal(probabilities[label])
            if probability is None or not probability.is_finite() or not 0 <= probability <= 1:
                raise InvalidResponse('probabilité non finie')
            total += probability
        if abs(total - 1) > Decimal('0.000001'):
            raise InvalidResponse('probabilités incohérentes')
        checked[question_id] = {
            'label': answer['choice'],
            'probability': str(probabilities[answer['choice']]),
            'probabilities': {label: str(probabilities[label]) for label in criteria}}
    usage_block = value['usage']
    if (type(usage_block) is not dict or set(usage_block) != {'input_tokens', 'output_tokens'}
            or type(usage_block['input_tokens']) is not int or usage_block['input_tokens'] < 1
            or type(usage_block['output_tokens']) is not int or usage_block['output_tokens'] < 0):
        raise InvalidResponse('jetons invalides')
    return {'answers': checked, 'input_tokens': usage_block['input_tokens'],
            'output_tokens': usage_block['output_tokens'], 'model': value['model']}


def _outcome(checked: dict, closed: list[tuple[str, dict]], threshold: Decimal) -> dict:
    answers, suggestions = {}, []
    for index, (notice_id, _) in enumerate(closed, 1):
        answer = checked['answers']['n' + str(index)]
        answers[notice_id] = {'label': answer['label'], 'probability': answer['probability'],
                              'probabilities': answer['probabilities']}
        if (answer['label'] != 'sans_correspondance'
                and Decimal(answer['probability']) >= threshold):
            suggestions.append({'notice_id': notice_id, 'label': answer['label'],
                                'probability': answer['probability']})
    return {'status': 'suggestions' if suggestions else 'none', 'reason': None,
            'suggestions': suggestions, 'answers': answers}


def _record(store, request_id: str, *, model=None, tokens=None, cost_status='UNKNOWN',
            cost_amount=None, outcome: dict) -> bool:
    connection = store._connection_checked()
    now = _now().isoformat()

    def write():
        with _transaction(connection, write=True):
            cursor = connection.execute(
                "UPDATE s2_semantic_requests SET state='RECEIVED', model_observed=?, input_tokens=?, "
                "output_tokens=?, cost_status=?, cost_amount=?, outcome_json=?, received_at=? "
                "WHERE request_id=? AND state='EMITTING'",
                (model, None if tokens is None else tokens[0], None if tokens is None else tokens[1],
                 cost_status, cost_amount, encode(outcome), now, request_id))
            return cursor.rowcount == 1
    try:
        return storage.retry_locked(write)
    except Exception as error:  # une écriture ratée ne rejoue jamais l'appel
        logging.getLogger(__name__).error('SEMANTIC_HELP_RECORD_SKIPPED error=%s', type(error).__name__)
        return False


def _result(row: dict) -> dict:
    receipt = {'request_id': row['request_id'], 'usage': row['usage'],
               'questions_version': row['questions_version'],
               'model_requested': row['model_requested'], 'model_observed': row['model_observed'],
               'notices': row['notice_ids'], 'questions': row['question_ids'],
               'input_tokens': row['input_tokens'], 'output_tokens': row['output_tokens'],
               'tariff': row['tariff'],
               'cost': {'status': row['cost_status'], 'amount': row['cost_amount'],
                        'currency': row['cost_currency'],
                        'source': ('Calcul local : jetons d’entrée reçus × tarif daté ; hors facture'
                                   if row['cost_status'] == 'KNOWN' else
                                   'Coût INCONNU ; réserve conservée')},
               'reserve': row['reserve_amount'], 'created_at': row['created_at'],
               'emitted_at': row['emitted_at'], 'received_at': row['received_at'],
               'answers': row['outcome'].get('answers', {})}
    return {'kind': 'semantic_help', 'request_id': row['request_id'],
            'status': row['outcome'].get('status', 'unavailable'),
            'reason': row['outcome'].get('reason'),
            'suggestions': row['outcome'].get('suggestions', []), 'receipt': receipt}


def _wait(store, request_id: str) -> dict | None:
    deadline = time.monotonic() + TIMEOUT_SECONDS + 1
    while time.monotonic() < deadline:
        time.sleep(0.05)
        with store.read_snapshot() as connection:
            row = _request(connection, request_id)
        if row is not None and row[3] == 'RECEIVED':
            return _row(row)
    return None


def handle(store, body, transport: Transport | None, config: Configuration | None,
           *, usage: str) -> tuple[int, dict]:
    """Une soumission volontaire : au plus une émission, aucune reprise ni secours"""
    if (config is None or transport is None or usage not in USAGES
            or not config.usages[usage].enabled):
        return 503, {'kind': 'semantic_help', 'status': 'unavailable', 'reason': 'DISABLED'}
    if type(body) is not dict or set(body) != {'submission_id', 'formulation', 'notices'}:
        raise InvalidRequest('Demande fermée requise')
    request_id = _request_id(body['submission_id'])
    if type(body['formulation']) is not str or not body['formulation'].strip():
        raise InvalidRequest('Formulation requise')

    connection = store._connection_checked()
    existing = _request(connection, request_id)
    if existing is not None:
        row = _row(existing)
        if row['usage'] != usage:
            return 409, {'kind': 'semantic_help', 'status': 'unavailable', 'reason': 'CONFLICT'}
        if row['state'] == 'RECEIVED':
            return 200, _result(row)
        waited = _wait(store, request_id)
        if waited is None:
            return 202, {'kind': 'semantic_help', 'request_id': request_id, 'status': 'pending'}
        return 200, _result(waited)

    closed = _closed_notices(body['notices'])
    if not closed:
        return 200, {'kind': 'semantic_help', 'status': 'none', 'reason': None,
                     'suggestions': [], 'receipt': None}
    if config.tariff is None:
        return 503, {'kind': 'semantic_help', 'status': 'unavailable', 'reason': 'FINANCE'}
    wire, questions, closed = _projection(usage, body['formulation'], closed)
    tariff = config.tariff
    reserve = _reserve(tariff, wire)
    now = _now()
    question_ids = list(questions)
    notice_ids = [notice_id for notice_id, _ in closed]
    try:
        with _transaction(connection, write=True):
            _close_rows(connection, now, stale_only=True)
            if connection.execute('SELECT 1 FROM s2_semantic_requests WHERE request_id=?',
                                  (request_id,)).fetchone():
                raise Unavailable('CONFLICT', 409)
            if connection.execute(
                    "SELECT count(*) FROM s2_semantic_requests WHERE state='EMITTING'").fetchone()[0] >= \
                    MAX_CONCURRENT_EMISSIONS:
                raise Unavailable('RATE', 429)
            last = connection.execute(
                'SELECT emitted_at FROM s2_semantic_requests ORDER BY emitted_at DESC LIMIT 1').fetchone()
            if last is not None and (now - datetime.fromisoformat(last[0])).total_seconds() < \
                    MIN_EMISSION_INTERVAL:
                raise Unavailable('RATE', 429)
            budget = _budget(connection)
            if (Decimal(reserve) + Decimal(budget['reserved']) + Decimal(budget['spent'])
                    > Decimal(BUDGET_USD)):
                raise Unavailable('BUDGET', 429)
            connection.execute(
                'INSERT INTO s2_semantic_requests (request_id,usage,questions_version,state,'
                'notice_ids_json,question_ids_json,reserve_amount,tariff_json,model_requested,'
                'cost_status,cost_currency,outcome_json,created_at,emitted_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (request_id, usage, QUESTIONS_VERSIONS[usage], 'EMITTING', encode(notice_ids),
                 encode(question_ids), reserve,
                 encode({'input_usd_per_million_tokens': str(tariff.input_usd_per_million_tokens),
                         'currency': tariff.currency, 'effective_at': tariff.effective_at,
                         'source': tariff.source}),
                 MODEL, 'UNKNOWN', CURRENCY, encode({'status': 'pending', 'answers': {}}),
                 now.isoformat(), now.isoformat()))
    except Unavailable as error:
        if error.reason != 'CONFLICT':
            raise
        waited = _wait(store, request_id)
        if waited is None:
            return 202, {'kind': 'semantic_help', 'request_id': request_id, 'status': 'pending'}
        return 200, _result(waited)
    except storage.BudgetError as error:
        raise Unavailable('BUDGET', 429) from error

    model, tokens = None, None
    cost_status, cost_amount = 'UNKNOWN', None
    try:
        response = transport.post(wire)
    except TimeoutError:
        outcome = _unavailable('TIMEOUT')
    except OSError:
        outcome = _unavailable('INTERRUPTED')
    else:
        outcome = _unavailable('INVALID_RESPONSE')
        if type(response) is not dict or not {'status', 'body', 'complete'} <= set(response):
            outcome = _unavailable('INVALID_RESPONSE')
        elif response['status'] != 200:
            incident = {401: 'KEY_REJECTED', 413: 'CONTEXT', 429: 'RATE_LIMITED',
                        529: 'OVERLOADED'}.get(response['status'], 'PROVIDER_ERROR')
            if (response['status'] == 400 and type(response['body']) is bytes
                    and b'max_tokens_exceeded' in response['body']):
                incident = 'CONTEXT'
            outcome = _unavailable(incident)
        else:
            try:
                checked = _validated(response['body'], response['complete'], question_ids, usage)
            except InvalidResponse:
                outcome = _unavailable('INVALID_RESPONSE')
            else:
                threshold = config.usages[usage].threshold
                outcome = _outcome(checked, closed, threshold if threshold is not None else Decimal(0))
                cost_status = 'KNOWN'
                cost_amount = _cost(tariff, checked['input_tokens'])
                tokens = (checked['input_tokens'], checked['output_tokens'])
                model = checked['model']
    if not _record(store, request_id, model=model, tokens=tokens, cost_status=cost_status,
                   cost_amount=cost_amount, outcome=outcome):
        return 503, {'kind': 'semantic_help', 'request_id': request_id,
                     'status': 'unavailable', 'reason': 'RECORD_FAILED'}
    try:
        with store.read_snapshot() as connection:
            row = _request(connection, request_id)
    except Exception as error:
        logging.getLogger(__name__).error('SEMANTIC_HELP_RECEIPT_READ_FAILED error=%s',
                                          type(error).__name__)
        row = None
    if row is None:
        return 503, {'kind': 'semantic_help', 'status': 'unavailable', 'reason': 'RECORD_FAILED'}
    return 200, _result(_row(row))


def receipt(store, request_id: str) -> tuple[int, dict]:
    """Reçu lisible sans texte recherché, y compris service ensuite désactivé"""
    request_id = _request_id(request_id)
    connection = store._connection_checked()
    if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='s2_semantic_requests'").fetchone():
        return 404, {'error': 'NOT_FOUND'}
    row = _request(connection, request_id)
    if row is None:
        return 404, {'error': 'NOT_FOUND'}
    if row[3] == 'EMITTING':
        return 202, {'kind': 'semantic_help', 'request_id': request_id, 'status': 'pending'}
    return 200, _result(_row(row))


def inspect(store) -> dict:
    connection = store._connection_checked()
    if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='s2_semantic_requests'").fetchone():
        return {'initialized': False, 'budget': None, 'requests': []}
    budget = _budget(connection)
    rows = connection.execute(
        'SELECT request_id,usage,state,cost_status,cost_amount,reserve_amount,received_at '
        'FROM s2_semantic_requests ORDER BY created_at, request_id').fetchall()
    return {'initialized': True, 'budget': budget,
            'requests': [dict(zip(('request_id', 'usage', 'state', 'cost_status', 'cost_amount',
                                   'reserve', 'received_at'), row)) for row in rows]}
