"""Restitution publique d’une campagne : format, activation, retrait et lecture.

Ce module ne voit ni campagne d’exécution, ni stockage privé, ni jugement. Il
vérifie des octets déjà construits sous l’autorité de publication des résultats
et les sert depuis le seul répertoire public. La version éditoriale et son
empreinte sont relues dans `editorial` : un retrait de l’épreuve ferme aussi ses
restitutions, sans modifier aucun de leurs octets.

Le paquet est autonome : sa projection fermée et ses pièces retenues suffisent à
la consultation, sans accès au stockage privé ni à un dossier temporaire.
Les formats fictifs historiques (`publications`, S6) gardent leur autorité propre.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import fcntl
import math
import os
import re
import secrets

from . import editorial
from .publications import _decode, _directory, _listdir, _read, _write
from .storage import _strict_json as encode, _text
from .validation import identifier, _hash, _texts

SCHEMA = 'benchmark-lab-x/published-results/v1'
PRESENTATION_VERSION = '1'
_QUERY = ('case', 'verdict', 'obligation', 'configuration', 'sort', 'direction')
_VERDICTS = ('SATISFAIT', 'NE SATISFAIT PAS', 'A_REPRENDRE')
_STATES = ('PASS', 'FAIL', 'INDETERMINE')
_ECONOMIC = ('COMPLETE', 'INCOMPLETE')
_MANIFEST = {'schema_version', 'presentation_version', 'task', 'campaign_id', 'contract_sha256',
             'title', 'work', 'conclusion', 'coverage', 'economic_status', 'conditions',
             'cost_basis', 'cases', 'panel', 'columns', 'obligations', 'rows', 'pending',
             'acquisition_dates', 'unknowns', 'restrictions', 'pieces'}
_COVERAGE = {'planned_cells', 'attempted_cells', 'evaluated_attempts', 'decided_attempts', 'not_started'}
_COST_BASIS = {'scope', 'attempts', 'unit', 'conversion'}
_PANEL = {'position', 'provider', 'model', 'name', 'revision', 'access', 'channel_id', 'route',
          'parameters', 'effort', 'effort_requested', 'effort_choice', 'output_bound', 'effort_limit'}
_PANEL_REQUIRED = {'position', 'provider', 'model', 'name', 'revision', 'access', 'channel_id', 'route',
                   'parameters', 'effort'}
_COLUMN = {'id', 'label', 'unit', 'favorable', 'proof', 'cost'}
_OBLIGATION = {'id', 'label', 'states'}
_ROW = {'panel_position', 'case_position', 'repetition', 'recovery_limit', 'relaunch', 'verdict',
        'reason', 'next_action', 'findings', 'measures', 'metrics', 'states', 'created_at',
        'responsible', 'judgment', 'limits', 'proofs', 'configuration'}
_ROW_REQUIRED = _ROW - {'repetition', 'recovery_limit', 'relaunch'}
CONFIGURATION_FIELDS = ('provider', 'model', 'revision', 'access', 'channel_id', 'route', 'parameters',
                        'effort')
_CONFIGURATION = {'requested', 'observed', 'sources'}
_FINDING = {'label', 'status', 'finding'}
_MEASURE = {'label', 'value', 'unit', 'status', 'reason'}
_METRIC = {'value', 'unit', 'rank', 'reason', 'key'}
_JUDGMENT = {'mode', 'professional_review', 'cost'}
_PENDING = {'panel_position', 'case_position', 'repetition', 'state', 'next_action', 'cause', 'cost'}
_COST = {'value', 'unit', 'status', 'source'}
_PIECE = {'file', 'sha256', 'label'}
_FIXED = {'manifest.json', 'approval.json'}
_FILE = re.compile(r'piece-[A-Za-z0-9_-]+\.txt\Z')
_ROOT = 'published-results'
_FIXTURE_ROOT = 'published-results-fixture'
_FIXTURE = ('approbateur-fictif-resultats', 'FIXTURE_PUBLIC_RESULTS')
_PRODUCTION = ('Ayo', 'PUBLIC_RESULTS')
RESTRICTION_PUBLIQUE = (
    'Vérification limitée pour le lecteur : les pièces non retenues pour la publication restent privées, extraits compris. '
    'Leur empreinte numérique ne permet pas de les lire. Les constats qui s’appuient sur elles ne peuvent pas être vérifiés sur cette page.')


class NotFound(LookupError):
    """Aucune restitution publiée à cette adresse"""


class Invalid(ValueError):
    """Filtre ou tri hors du contrat publié"""


def _document(actor, identity):
    return dict(actor=actor[0], authority_id=actor[1], package_sha256=identity, scope='results-publication')


def approval(identity):
    """Autorité de production des résultats, distincte de l’autorité éditoriale"""
    _hash(identity)
    return _document(_PRODUCTION, identity)


def fixture_approval(identity):
    """Autorité de fixture, hors du lecteur de production et de l’autorité éditoriale"""
    _hash(identity)
    return _document(_FIXTURE, identity)


def _withdrawal_document(publication, identity, task_id, version_id, campaign_id):
    return dict(actor=publication['actor'], authority_id=publication['authority_id'], package_sha256=identity,
                scope='results-withdrawal', task_id=task_id, version_id=version_id, campaign_id=campaign_id)


def withdrawal(identity, task_id, version_id, campaign_id):
    """Décision de retrait de production pour ces octets et cette campagne"""
    _hash(identity)
    return _withdrawal_document(_document(_PRODUCTION, identity), identity, identifier(task_id),
                                identifier(version_id), identifier(campaign_id))


def fixture_withdrawal(identity, task_id, version_id, campaign_id):
    """Décision de retrait de fixture pour ces octets et cette campagne"""
    _hash(identity)
    return _withdrawal_document(_document(_FIXTURE, identity), identity, identifier(task_id),
                                identifier(version_id), identifier(campaign_id))


def _fields(value, expected, label):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError('Paquet incomplet : ' + label)


def _optional(value, expected, required, label):
    if type(value) is not dict or set(value) - set(expected) or not set(required) <= set(value):
        raise ValueError('Paquet incomplet : ' + label)


def _date(value, label):
    if type(value) is not str:
        raise ValueError('Date invalide : ' + label)
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError('Date invalide : ' + label) from None
    if moment.tzinfo is None:
        raise ValueError('Date invalide : ' + label)


def _count(value, label):
    if type(value) is not int or value < 0:
        raise ValueError('Compteur invalide : ' + label)


def _rank(value, label):
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError('Rang invalide : ' + label)


def _value(value, label):
    if not (value is None or type(value) in (str, bool, int, float)):
        raise ValueError('Valeur invalide : ' + label)
    if type(value) is float and not math.isfinite(value):
        raise ValueError('Valeur invalide : ' + label)


def _text_value(value, label):
    if value is not None:
        _text(value, label)


def _number_text(value, label):
    if type(value) is not str or re.fullmatch(
            r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value) is None:
        raise ValueError('Valeur numérique invalide : ' + label)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError('Valeur numérique invalide : ' + label) from None
    if not number.is_finite():
        raise ValueError('Valeur numérique invalide : ' + label)


def _configuration(value):
    _fields(value, _CONFIGURATION, 'configuration')
    for section in ('requested', 'observed', 'sources'):
        _fields(value[section], CONFIGURATION_FIELDS, 'configuration ' + section)
    for field in CONFIGURATION_FIELDS:
        for section in ('requested', 'observed', 'sources'):
            item = value[section][field]
            if field == 'parameters' and type(item) is dict:
                encode(item)
            else:
                _text(item, 'configuration')


def _cost(value, label):
    _fields(value, _COST, label)
    _value(value['value'], label)
    _text(value['unit'], label)
    if value['status'] not in ('KNOWN', 'UNKNOWN'):
        raise ValueError('État de coût invalide : ' + label)
    _text(value['source'], label)


def _manifest(raw):
    if type(raw) is not bytes:
        raise ValueError('Paquet incomplet')
    try:
        manifest = _decode(raw)
    except ValueError:
        raise ValueError('Paquet incomplet') from None
    if type(manifest) is not dict or set(manifest) != _MANIFEST:
        raise ValueError('Sélection publique fermée')
    if manifest['schema_version'] != SCHEMA or manifest['presentation_version'] != PRESENTATION_VERSION:
        raise ValueError('Paquet incomplet : version de restitution')
    task = manifest['task']
    _fields(task, {'task_id', 'version_id', 'package_sha256'}, 'task')
    identifier(task['task_id'])
    identifier(task['version_id'])
    _hash(task['package_sha256'])
    identifier(manifest['campaign_id'])
    _hash(manifest['contract_sha256'])
    _text(manifest['title'], 'title')
    _text(manifest['work'], 'work')
    conclusion = manifest['conclusion']
    _fields(conclusion, {'text', 'attribution', 'limits'}, 'conclusion')
    _text(conclusion['text'], 'conclusion')
    _text(conclusion['attribution'], 'attribution')
    _texts(conclusion['limits'], 'limits', required=True, unique=True)
    coverage = manifest['coverage']
    _fields(coverage, _COVERAGE, 'coverage')
    for key in coverage:
        _count(coverage[key], key)
    if coverage['decided_attempts'] > coverage['evaluated_attempts'] or \
            coverage['evaluated_attempts'] > coverage['attempted_cells'] or \
            coverage['attempted_cells'] > coverage['planned_cells']:
        raise ValueError('Paquet incomplet : couverture')
    if manifest['economic_status'] not in _ECONOMIC:
        raise ValueError('Paquet incomplet : état économique')
    conditions = manifest['conditions']
    _fields(conditions, {'pi', 'environment', 'tools', 'packages', 'skills', 'frozen_at'}, 'conditions')
    _fields(conditions['pi'], {'package', 'version', 'status'}, 'pi')
    for key in ('package', 'version', 'status'):
        _text(conditions['pi'][key], 'pi')
    if type(conditions['environment']) is not dict or not conditions['environment']:
        raise ValueError('Paquet incomplet : environnement')
    for key, value in conditions['environment'].items():
        _text(key, 'environnement')
        _text(value, 'environnement')
    for key in ('tools', 'packages', 'skills'):
        _texts(conditions[key], key, unique=True)
    _date(conditions['frozen_at'], 'frozen_at')
    basis = manifest['cost_basis']
    _fields(basis, _COST_BASIS, 'cost_basis')
    for key in ('scope', 'attempts', 'unit'):
        _text(basis[key], key)
    if basis['conversion'] is not None:
        _fields(basis['conversion'], {'source', 'date', 'formula'}, 'conversion')
        for key in ('source', 'date', 'formula'):
            _text(basis['conversion'][key], 'conversion')
    cases = manifest['cases']
    if type(cases) is not list or not cases:
        raise ValueError('Paquet incomplet : cas')
    positions = set()
    for case in cases:
        _fields(case, {'position'}, 'cas')
        position = case['position']
        if type(position) is not int or not 0 < position <= len(cases) or position in positions:
            raise ValueError('Paquet incomplet : cas')
        positions.add(position)
    panel = manifest['panel']
    if type(panel) is not list or not panel:
        raise ValueError('Paquet incomplet : panel')
    panel_positions = set()
    for configuration in panel:
        _optional(configuration, _PANEL, _PANEL_REQUIRED, 'panel')
        position = configuration['position']
        if type(position) is not int or not 0 < position <= len(panel) or position in panel_positions:
            raise ValueError('Paquet incomplet : panel')
        panel_positions.add(position)
        for key in ('provider', 'model', 'name', 'revision', 'access', 'channel_id', 'route', 'effort'):
            _text(configuration[key], key)
        if type(configuration['parameters']) is not dict:
            raise ValueError('Paquet incomplet : paramètres')
        encode(configuration['parameters'])
        for key in ('effort_requested', 'effort_choice'):
            _text_value(configuration.get(key), key)
        if 'output_bound' in configuration:
            bound = configuration['output_bound']
            if bound is not None and (type(bound) is not int or bound <= 0):
                raise ValueError('Paquet incomplet : borne de sortie')
    columns = manifest['columns']
    if type(columns) is not list or not columns:
        raise ValueError('Paquet incomplet : colonnes')
    column_ids = set()
    for column in columns:
        _fields(column, _COLUMN, 'colonne')
        identifier(column['id'])
        if column['id'] in column_ids:
            raise ValueError('Paquet incomplet : colonne répétée')
        column_ids.add(column['id'])
        for key in ('label', 'unit', 'favorable', 'proof'):
            _text(column[key], key)
        if type(column['cost']) is not bool:
            raise ValueError('Paquet incomplet : colonne')
    if sum(column['cost'] for column in columns) != 1:
        raise ValueError('Paquet incomplet : coût observé')
    obligations = manifest['obligations']
    if type(obligations) is not list:
        raise ValueError('Paquet incomplet : exigences')
    obligation_ids = set()
    allowed_states = {}
    for obligation in obligations:
        _fields(obligation, _OBLIGATION, 'exigence')
        identifier(obligation['id'])
        if obligation['id'] in obligation_ids:
            raise ValueError('Paquet incomplet : exigence répétée')
        obligation_ids.add(obligation['id'])
        _text(obligation['label'], 'label')
        states = obligation['states']
        if type(states) is not list or not states or len(set(states)) != len(states) or \
                any(state not in _STATES for state in states):
            raise ValueError('Paquet incomplet : états d’exigence')
        allowed_states[obligation['id']] = set(states)
    rows = manifest['rows']
    if type(rows) is not list:
        raise ValueError('Paquet incomplet : réponses')
    for row in rows:
        _optional(row, _ROW, _ROW_REQUIRED, 'réponse')
        _configuration(row['configuration'])
        if type(row['panel_position']) is not int or row['panel_position'] not in panel_positions:
            raise ValueError('Paquet incomplet : modèle de réponse')
        if type(row['case_position']) is not int or row['case_position'] not in positions:
            raise ValueError('Paquet incomplet : exemple de réponse')
        if 'repetition' in row and (type(row['repetition']) is not int or row['repetition'] < 1):
            raise ValueError('Paquet incomplet : essai')
        if row.get('recovery_limit') is not None and (type(row['recovery_limit']) is not int
                                                      or row['recovery_limit'] < 1):
            raise ValueError('Paquet incomplet : limite de reprise')
        if row.get('relaunch') is not None and (type(row['relaunch']) is not int or row['relaunch'] < 1):
            raise ValueError('Paquet incomplet : relance')
        if row['verdict'] is not None and row['verdict'] not in _VERDICTS:
            raise ValueError('Paquet incomplet : verdict')
        _text(row['reason'], 'motif')
        _text_value(row['next_action'], 'prochaine action')
        findings = row['findings']
        if type(findings) is not list:
            raise ValueError('Paquet incomplet : constats')
        for finding in findings:
            _fields(finding, _FINDING, 'constat')
            _text(finding['label'], 'label de constat')
            if finding['status'] not in _STATES:
                raise ValueError('Paquet incomplet : état de constat')
            _text(finding['finding'], 'constat')
        measures = row['measures']
        if type(measures) is not list:
            raise ValueError('Paquet incomplet : mesures')
        for measure in measures:
            _fields(measure, _MEASURE, 'mesure')
            _text(measure['label'], 'label de mesure')
            _value(measure['value'], 'valeur de mesure')
            _text(measure['unit'], 'unité de mesure')
            if measure['status'] not in ('KNOWN', 'UNKNOWN'):
                raise ValueError('Paquet incomplet : état de mesure')
            if measure['reason'] is not None:
                _text(measure['reason'], 'motif de mesure')
            elif measure['status'] != 'KNOWN':
                raise ValueError('Paquet incomplet : mesure inconnue sans motif')
        metrics = row['metrics']
        if type(metrics) is not dict or set(metrics) != column_ids:
            raise ValueError('Paquet incomplet : mesures par colonne')
        for metric in metrics.values():
            _fields(metric, _METRIC, 'mesure par colonne')
            _value(metric['value'], 'valeur par colonne')
            _text(metric['unit'], 'unité par colonne')
            _rank(metric['rank'], 'rang')
            _text_value(metric['key'], 'clé de tri')
            if metric['key'] is not None:
                _number_text(metric['key'], 'clé de tri')
            if metric['reason'] is not None:
                _text(metric['reason'], 'motif de rang')
            elif metric['rank'] is None:
                raise ValueError('Paquet incomplet : mesure classable sans rang')
            if metric['rank'] is None and metric['key'] is not None:
                raise ValueError('Paquet incomplet : clé sans rang')
        states = row['states']
        if type(states) is not dict or set(states) != obligation_ids:
            raise ValueError('Paquet incomplet : états par exigence')
        for obligation_id, state in states.items():
            if state not in allowed_states[obligation_id]:
                raise ValueError('Paquet incomplet : état d’exigence')
        _date(row['created_at'], 'created_at')
        _text(row['responsible'], 'responsable')
        judgment = row['judgment']
        _fields(judgment, _JUDGMENT, 'jugement')
        if judgment['mode'] not in ('local', 'human', 'assisted'):
            raise ValueError('Paquet incomplet : mode de jugement')
        review = judgment['professional_review']
        if review is not None:
            _fields(review, {'author', 'phase', 'scope'}, 'revue professionnelle')
            for key in ('author', 'phase', 'scope'):
                _text(review[key], key)
        cost = judgment['cost']
        _fields(cost, {'value', 'unit', 'status'}, 'coût du jugement')
        _value(cost['value'], 'coût du jugement')
        _text(cost['unit'], 'unité du jugement')
        _text(cost['status'], 'état du jugement')
        _texts(row['limits'], 'limites', unique=True)
        proofs = row['proofs']
        if type(proofs) is not list or not proofs:
            raise ValueError('Paquet incomplet : preuves')
        for proof in proofs:
            _fields(proof, {'name', 'piece_id'}, 'preuve')
            _text(proof['name'], 'nom de preuve')
            if proof['piece_id'] is not None:
                identifier(proof['piece_id'])
    pending = manifest['pending']
    if type(pending) is not list:
        raise ValueError('Paquet incomplet : attentes')
    for item in pending:
        _fields(item, _PENDING, 'attente')
        if type(item['panel_position']) is not int or item['panel_position'] not in panel_positions:
            raise ValueError('Paquet incomplet : modèle en attente')
        if type(item['case_position']) is not int or item['case_position'] not in positions:
            raise ValueError('Paquet incomplet : exemple en attente')
        if item['repetition'] is not None and (type(item['repetition']) is not int or item['repetition'] < 1):
            raise ValueError('Paquet incomplet : essai en attente')
        _text(item['state'], 'état en attente')
        _text(item['next_action'], 'prochaine action en attente')
        _text_value(item['cause'], 'cause en attente')
        _cost(item['cost'], 'coût en attente')
    dates = manifest['acquisition_dates']
    if type(dates) is not list:
        raise ValueError('Paquet incomplet : dates')
    for value in dates:
        _date(value, 'acquisition')
    _texts(manifest['unknowns'], 'inconnues', unique=True)
    _texts(manifest['restrictions'], 'restrictions', required=True, unique=True)
    pieces = manifest['pieces']
    if type(pieces) is not dict:
        raise ValueError('Paquet incomplet : pièces')
    for pid, piece in pieces.items():
        identifier(pid)
        _fields(piece, _PIECE, 'pièce')
        if piece['file'] != 'piece-' + pid + '.txt' or not _FILE.fullmatch(piece['file']):
            raise ValueError('Sélection publique fermée')
        _hash(piece['sha256'])
        _text(piece['label'], 'label de pièce')
    if not {proof['piece_id'] for row in rows for proof in row['proofs'] if proof['piece_id']} <= set(pieces):
        raise ValueError('Sélection publique fermée')
    return manifest


def _approval(value, identity, actor):
    if type(value) is not dict or not value:
        raise ValueError('Autorité absente')
    if type(value.get('package_sha256')) is str and value['package_sha256'] != identity:
        raise ValueError('Approbation visant d’autres octets')
    if value != _document(actor, identity):
        raise ValueError('Autorité de publication refusée')


def _bundle(bundle, approved, actor):
    if type(bundle) is not dict or set(bundle) != {'manifest', 'files', 'projection_sha256'}:
        raise ValueError('Paquet incomplet')
    raw, identity, files = bundle['manifest'], bundle['projection_sha256'], bundle['files']
    if type(raw) is not bytes or type(identity) is not str or sha256(raw).hexdigest() != identity:
        raise ValueError('Manifeste divergent')
    _approval(approved, identity, actor)
    manifest = _manifest(raw)
    if type(files) is not dict:
        raise ValueError('Paquet incomplet')
    declared = {piece['file'] for piece in manifest['pieces'].values()}
    if set(files) - declared:
        raise ValueError('Sélection publique fermée')
    if declared - set(files):
        raise ValueError('Paquet incomplet')
    for piece in manifest['pieces'].values():
        body = files[piece['file']]
        if type(body) is not bytes or sha256(body).hexdigest() != piece['sha256']:
            raise ValueError('Octets divergents')
        try:
            body.decode('utf-8')
        except UnicodeError:
            raise ValueError('Pièce illisible') from None
    return manifest


def _open_child(parent, name):
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def _ensure_dir(parent, name):
    if name not in (_ROOT, _FIXTURE_ROOT):
        identifier(name)
    try:
        return _open_child(parent, name)
    except FileNotFoundError:
        os.mkdir(name, 0o700, dir_fd=parent)
        return _open_child(parent, name)


def _open_campaign(root, task_id, version_id, campaign_id, root_name):
    try:
        tasks = _open_child(root, root_name)
    except FileNotFoundError:
        raise NotFound() from None
    try:
        try:
            task = _open_child(tasks, task_id)
        except FileNotFoundError:
            raise NotFound() from None
        try:
            try:
                version = _open_child(task, version_id)
            except FileNotFoundError:
                raise NotFound() from None
            try:
                try:
                    return _open_child(version, campaign_id)
                except FileNotFoundError:
                    raise NotFound() from None
            finally:
                os.close(version)
        finally:
            os.close(task)
    finally:
        os.close(tasks)


def _names(fd, pieces):
    names = set(_listdir(fd))
    allowed = set(_FIXED) | {piece['file'] for piece in pieces.values()}
    if 'withdrawal.json' in names:
        allowed.add('withdrawal.json')
    if names != allowed:
        raise ValueError('Sélection publique fermée')


def _withdrawal(fd, publication, identity, task_id, version_id, campaign_id):
    try:
        raw = _read(fd, 'withdrawal.json')
    except FileNotFoundError:
        return False
    if _decode(raw) != _withdrawal_document(publication, identity, task_id, version_id, campaign_id):
        raise ValueError('Retrait divergent')
    return True


def _bytes(fd, name):
    raw = _read(fd, name)
    if type(raw) is not bytes:
        raise ValueError('Octets divergents')
    return raw


def _read_record(fd, task_id, version_id, campaign_id, actor):
    manifest_raw = _bytes(fd, 'manifest.json')
    identity = sha256(manifest_raw).hexdigest()
    publication = _decode(_bytes(fd, 'approval.json'))
    _approval(publication, identity, actor)
    manifest = _manifest(manifest_raw)
    if (manifest['task']['task_id'], manifest['task']['version_id'], manifest['campaign_id']) != \
            (task_id, version_id, campaign_id):
        raise ValueError('Identité divergente')
    _names(fd, manifest['pieces'])
    pieces = {}
    for pid, piece in manifest['pieces'].items():
        raw = _bytes(fd, piece['file'])
        if sha256(raw).hexdigest() != piece['sha256']:
            raise ValueError('Octets divergents')
        try:
            raw.decode('utf-8')
        except UnicodeError:
            raise ValueError('Pièce illisible') from None
        pieces[pid] = raw
    withdrawn = _withdrawal(fd, publication, identity, task_id, version_id, campaign_id)
    return dict(identity=identity, manifest_raw=manifest_raw, approval=publication, manifest=manifest,
                pieces=pieces, withdrawn=withdrawn)


def _load(destination, task_id, version_id, campaign_id, actor, root_name):
    root = _directory(destination)
    try:
        folder = _open_campaign(root, task_id, version_id, campaign_id, root_name)
        try:
            fcntl.flock(folder, fcntl.LOCK_SH)
            try:
                return _read_record(folder, task_id, version_id, campaign_id, actor)
            finally:
                fcntl.flock(folder, fcntl.LOCK_UN)
        finally:
            os.close(folder)
    finally:
        os.close(root)


def _trial(destination, task_id, version_id, actor):
    state = (editorial.version_state(destination, task_id, version_id) if actor == _PRODUCTION
             else editorial.fixture_version_state(destination, task_id, version_id))
    return state


def _activate(parent, manifest, bundle, approved, actor):
    task_id, version_id = manifest['task']['task_id'], manifest['task']['version_id']
    campaign_id = manifest['campaign_id']
    try:
        existing = _open_child(parent, campaign_id)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        try:
            record = _read_record(existing, task_id, version_id, campaign_id, actor)
            if record['withdrawn']:
                raise ValueError('Restitution retirée')
            found = {manifest['pieces'][pid]['file']: raw for pid, raw in record['pieces'].items()}
            if record['manifest_raw'] != bundle['manifest'] or record['approval'] != approved or \
                    found != bundle['files']:
                raise ValueError('Restitution existante divergente')
            return
        finally:
            os.close(existing)
    stage = '.p-' + secrets.token_hex(16)
    folder = None
    staged = False
    try:
        os.mkdir(stage, 0o700, dir_fd=parent)
        staged = True
        folder = _open_child(parent, stage)
        _write(folder, 'manifest.json', bundle['manifest'])
        _write(folder, 'approval.json', encode(approved).encode())
        for name, raw in bundle['files'].items():
            _write(folder, name, raw)
        os.fsync(folder)
        record = _read_record(folder, task_id, version_id, campaign_id, actor)
        found = {manifest['pieces'][pid]['file']: raw for pid, raw in record['pieces'].items()}
        if record['withdrawn'] or record['manifest_raw'] != bundle['manifest'] or \
                record['approval'] != approved or found != bundle['files']:
            raise ValueError('Paquet incomplet')
        os.rename(stage, campaign_id, src_dir_fd=parent, dst_dir_fd=parent)
        staged = False
        os.fsync(parent)
    finally:
        if folder is not None:
            if staged:
                for name in _listdir(folder):
                    os.unlink(name, dir_fd=folder)
            os.close(folder)
        if staged:
            os.rmdir(stage, dir_fd=parent)


def _publish(destination, bundle, approved, actor, root_name):
    bundle, approved = deepcopy(bundle), deepcopy(approved)
    manifest = _bundle(bundle, approved, actor)
    task = manifest['task']
    try:
        state = _trial(destination, task['task_id'], task['version_id'], actor)
    except NotFound:
        raise ValueError('Épreuve publiée requise') from None
    if state['withdrawn']:
        raise ValueError('Épreuve retirée')
    if state['package_sha256'] != task['package_sha256']:
        raise ValueError('Rattachement divergent')
    root = _directory(destination)
    try:
        results_root = _ensure_dir(root, root_name)
        try:
            task_folder = _ensure_dir(results_root, task['task_id'])
            try:
                version_folder = _ensure_dir(task_folder, task['version_id'])
                try:
                    _activate(version_folder, manifest, bundle, approved, actor)
                finally:
                    os.close(version_folder)
            finally:
                os.close(task_folder)
        finally:
            os.close(results_root)
    finally:
        os.close(root)


def publish(destination, bundle, approved):
    """Active une restitution complète sous l’autorité de production des résultats"""
    _publish(destination, bundle, approved, _PRODUCTION, _ROOT)


def fixture_publish(destination, bundle, approved):
    """Active une restitution de fixture hors du répertoire lu par la production"""
    _publish(destination, bundle, approved, _FIXTURE, _FIXTURE_ROOT)


def _withdraw(destination, task_id, version_id, campaign_id, approved, actor, root_name):
    approved = deepcopy(approved)
    root = _directory(destination)
    try:
        try:
            folder = _open_campaign(root, task_id, version_id, campaign_id, root_name)
        except NotFound:
            raise ValueError('Restitution publiée requise') from None
        fcntl.flock(folder, fcntl.LOCK_EX)
        try:
            record = _read_record(folder, task_id, version_id, campaign_id, actor)
            expected = _withdrawal_document(record['approval'], record['identity'], task_id, version_id, campaign_id)
            if approved != expected:
                if type(approved) is dict and type(approved.get('package_sha256')) is str and \
                        approved['package_sha256'] != record['identity']:
                    raise ValueError('Approbation visant d’autres octets')
                raise ValueError('Autorité de publication refusée')
            raw = encode(approved).encode()
            try:
                current = _read(folder, 'withdrawal.json')
            except FileNotFoundError:
                current = None
            if current is not None:
                if current != raw:
                    raise ValueError('Retrait déjà conservé')
                return
            before = record['manifest_raw']
            pieces = dict(record['pieces'])
            pointer = '.w-' + secrets.token_hex(16)
            written = False
            try:
                _write(folder, pointer, raw)
                written = True
                os.rename(pointer, 'withdrawal.json', src_dir_fd=folder, dst_dir_fd=folder)
                written = False
                os.fsync(folder)
                again = _read_record(folder, task_id, version_id, campaign_id, actor)
                if again['manifest_raw'] != before or again['pieces'] != pieces or not again['withdrawn']:
                    raise ValueError('Octets approuvés modifiés')
            finally:
                if written:
                    try:
                        os.unlink(pointer, dir_fd=folder)
                    except FileNotFoundError:
                        pass
        finally:
            fcntl.flock(folder, fcntl.LOCK_UN)
            os.close(folder)
    finally:
        os.close(root)


def withdraw(destination, task_id, version_id, campaign_id, approved):
    """Retire une restitution de production sans modifier ses octets ni l’épreuve"""
    _withdraw(destination, task_id, version_id, campaign_id, approved, _PRODUCTION, _ROOT)


def fixture_withdraw(destination, task_id, version_id, campaign_id, approved):
    """Retire une restitution de fixture sans modifier ses octets ni l’épreuve"""
    _withdraw(destination, task_id, version_id, campaign_id, approved, _FIXTURE, _FIXTURE_ROOT)


def _is_id(name):
    try:
        identifier(name)
    except ValueError:
        return False
    return True


def _summary(record):
    manifest = record['manifest']
    return dict(campaign_id=manifest['campaign_id'], title=manifest['title'], work=manifest['work'],
                frozen_at=manifest['conditions']['frozen_at'], pi=deepcopy(manifest['conditions']['pi']),
                environment=deepcopy(manifest['conditions']['environment']), cases=len(manifest['cases']),
                contract_sha256=manifest['contract_sha256'], unit=manifest['cost_basis']['unit'],
                coverage=deepcopy(manifest['coverage']), economic_status=manifest['economic_status'])


def _records(destination, task_id, version_id, actor, root_name):
    """Dossiers de restitution du répertoire demandé, sans ouvrir le lecteur de l’autre autorité"""
    root = _directory(destination)
    try:
        try:
            tasks = _open_child(root, root_name)
        except FileNotFoundError:
            return []
        try:
            try:
                task = _open_child(tasks, task_id)
            except FileNotFoundError:
                return []
            try:
                try:
                    version = _open_child(task, version_id)
                except FileNotFoundError:
                    return []
                try:
                    records = []
                    for campaign_id in sorted(name for name in _listdir(version) if _is_id(name)):
                        try:
                            folder = _open_child(version, campaign_id)
                        except OSError:
                            continue
                        try:
                            fcntl.flock(folder, fcntl.LOCK_SH)
                            try:
                                records.append(_read_record(folder, task_id, version_id, campaign_id, actor))
                            finally:
                                fcntl.flock(folder, fcntl.LOCK_UN)
                        except (OSError, ValueError):
                            continue
                        finally:
                            os.close(folder)
                    return records
                finally:
                    os.close(version)
            finally:
                os.close(task)
        finally:
            os.close(tasks)
    finally:
        os.close(root)


def _published(destination, task_id, version_id, actor):
    state = _trial(destination, task_id, version_id, actor)
    if state['withdrawn']:
        return []
    return [_summary(record) for record in _records(destination, task_id, version_id, actor,
                                                    _ROOT if actor == _PRODUCTION else _FIXTURE_ROOT)
            if not record['withdrawn']
            and record['manifest']['task']['package_sha256'] == state['package_sha256']]


def published(destination, task_id, version_id):
    """Campagnes de production encore consultables pour cette version"""
    identifier(task_id)
    identifier(version_id)
    return _published(destination, task_id, version_id, _PRODUCTION)


def fixture_published(destination, task_id, version_id):
    """Campagnes de fixture encore consultables, hors du lecteur de production"""
    identifier(task_id)
    identifier(version_id)
    return _published(destination, task_id, version_id, _FIXTURE)


def query_parameters(raw):
    """Paramètres de consultation : noms prévus, une valeur chacun, texte UTF-8 borné"""
    if type(raw) is not str or len(raw) > 1024:
        raise ValueError('Paramètres de consultation invalides')
    from urllib.parse import parse_qsl
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, errors='strict')
    except (ValueError, UnicodeError):
        raise ValueError('Paramètres de consultation invalides') from None
    if len(pairs) > len(_QUERY) or len(dict(pairs)) != len(pairs) or any(key not in _QUERY for key, _ in pairs):
        raise ValueError('Paramètre inconnu ou répété')
    query = {key: value for key, value in pairs if value}
    if any(len(value) > 128 for value in query.values()):
        raise ValueError('Paramètre de consultation invalide')
    return query


def _queries(query, manifest):
    if type(query) is not dict or any(type(key) is not str or type(value) is not str for key, value in query.items()):
        raise Invalid('Paramètres textuels requis')
    allowed = {
        'case': {str(case['position']) for case in manifest['cases']},
        'configuration': {str(configuration['position']) for configuration in manifest['panel']},
        'verdict': set(_VERDICTS), 'direction': {'asc', 'desc'},
        'sort': {column['id'] for column in manifest['columns']},
        'obligation': {obligation['id'] + ':' + state for obligation in manifest['obligations']
                       for state in obligation['states']},
    }
    if any(key not in allowed or value not in allowed[key] for key, value in query.items()):
        raise Invalid('Filtre ou tri hors contrat')
    return query


def _selected(rows, query):
    selected = []
    for row in rows:
        if 'case' in query and int(query['case']) != row['case_position']:
            continue
        if 'configuration' in query and int(query['configuration']) != row['panel_position']:
            continue
        if 'verdict' in query:
            wanted = None if query['verdict'] == 'A_REPRENDRE' else query['verdict']
            if wanted != row['verdict']:
                continue
        if 'obligation' in query:
            criterion_id, state = query['obligation'].split(':')
            if row['states'][criterion_id] != state:
                continue
        selected.append(row)
    return selected


def _ordered(rows, query, cases):
    if 'sort' not in query:
        return rows
    column = query['sort']
    ordered = []
    for case in cases:
        group = [row for row in rows if row['case_position'] == case['position']]
        known = [row for row in group if row['metrics'][column]['key'] is not None]
        unknown = [row for row in group if row['metrics'][column]['key'] is None]
        known.sort(key=lambda row: Decimal(row['metrics'][column]['key']),
                   reverse=query.get('direction') == 'desc')
        ordered.extend(known + unknown)
    return ordered


def _view(destination, task_id, version_id, campaign_id, query, actor):
    try:
        state = _trial(destination, task_id, version_id, actor)
    except (LookupError, ValueError):
        raise NotFound() from None
    if state['withdrawn']:
        return dict(kind='results', task_id=task_id, version_id=version_id, campaign_id=campaign_id,
                    withdrawn=False, unavailable='trial-withdrawn')
    record = _load(destination, task_id, version_id, campaign_id, actor,
                   _ROOT if actor == _PRODUCTION else _FIXTURE_ROOT)
    if record['manifest']['task']['package_sha256'] != state['package_sha256']:
        raise ValueError('Rattachement divergent')
    if record['withdrawn']:
        return dict(kind='results', task_id=task_id, version_id=version_id, campaign_id=campaign_id,
                    withdrawn=True, unavailable=None, title=record['manifest']['title'])
    manifest = deepcopy(record['manifest'])
    query = _queries(query, manifest)
    manifest.update(kind='results', withdrawn=False, unavailable=None, task_id=task_id, version_id=version_id,
                    rows=_ordered(_selected(manifest['rows'], query), query, manifest['cases']),
                    population=len(manifest['rows']), filter_scope=query,
                    campaigns=_published(destination, task_id, version_id, actor))
    return manifest


def view(destination, task_id, version_id, campaign_id, query=None):
    """Projection publique de production, ou état de retrait sans contenu"""
    identifier(task_id)
    identifier(version_id)
    identifier(campaign_id)
    return _view(destination, task_id, version_id, campaign_id, {} if query is None else query, _PRODUCTION)


def fixture_view(destination, task_id, version_id, campaign_id, query=None):
    """Projection publique de fixture, hors du lecteur de production"""
    identifier(task_id)
    identifier(version_id)
    identifier(campaign_id)
    return _view(destination, task_id, version_id, campaign_id, {} if query is None else query, _FIXTURE)


def _piece(destination, task_id, version_id, campaign_id, piece_id, actor):
    identifier(piece_id)
    try:
        state = _trial(destination, task_id, version_id, actor)
    except (LookupError, ValueError):
        raise NotFound() from None
    if state['withdrawn']:
        raise NotFound()
    record = _load(destination, task_id, version_id, campaign_id, actor,
                   _ROOT if actor == _PRODUCTION else _FIXTURE_ROOT)
    if record['manifest']['task']['package_sha256'] != state['package_sha256'] or record['withdrawn']:
        raise NotFound()
    piece = record['manifest']['pieces'].get(piece_id)
    if piece is None:
        raise NotFound()
    return record['pieces'][piece_id]


def piece_bytes(destination, task_id, version_id, campaign_id, piece_id):
    """Octets d’une pièce de production encore disponible"""
    return _piece(destination, task_id, version_id, campaign_id, piece_id, _PRODUCTION)


def fixture_piece(destination, task_id, version_id, campaign_id, piece_id):
    """Octets d’une pièce de fixture, hors du lecteur de production"""
    return _piece(destination, task_id, version_id, campaign_id, piece_id, _FIXTURE)
