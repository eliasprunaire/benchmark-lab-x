"""Local qualification of exact S2 contracts by a trusted, injected controller.

No controller loader, assistant transport or public approval authority is provided.
The private operator is the trust boundary, not a role string submitted over HTTP.
"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import re
import secrets

from . import storage
from .storage import (IntegrityError, SchemaError, ConflictError, _transaction,
                      _strict_json as encode, _fields, _text, _identity, _unique_object)

FORMAT_IDENTITY = 'benchmark-lab-x/qualification/v1'
_TABLES = {
    's3_control': """CREATE TABLE s3_control (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        format_identity TEXT NOT NULL CHECK(format_identity = 'benchmark-lab-x/qualification/v1')
    )""",
    's3_contracts': """CREATE TABLE s3_contracts (
        contract_sha256 TEXT PRIMARY KEY NOT NULL CHECK(length(contract_sha256) = 64),
        dossier_id TEXT NOT NULL REFERENCES s2_dossiers(dossier_id),
        revision INTEGER NOT NULL,
        version INTEGER NOT NULL CHECK(version > 0),
        contract_json TEXT NOT NULL,
        UNIQUE(dossier_id, version),
        FOREIGN KEY(dossier_id, revision) REFERENCES s2_revisions(dossier_id, revision)
    )""",
    's3_qualifications': """CREATE TABLE s3_qualifications (
        qualification_id TEXT PRIMARY KEY NOT NULL,
        contract_sha256 TEXT NOT NULL REFERENCES s3_contracts(contract_sha256),
        receipt_json TEXT NOT NULL,
        receipt_sha256 TEXT NOT NULL CHECK(length(receipt_sha256) = 64),
        UNIQUE(contract_sha256, qualification_id)
    )""",
    's3_approvals': """CREATE TABLE s3_approvals (
        contract_sha256 TEXT PRIMARY KEY NOT NULL REFERENCES s3_contracts(contract_sha256),
        qualification_id TEXT NOT NULL,
        approval_json TEXT NOT NULL,
        approval_sha256 TEXT NOT NULL CHECK(length(approval_sha256) = 64),
        FOREIGN KEY(contract_sha256, qualification_id)
            REFERENCES s3_qualifications(contract_sha256, qualification_id)
    )""",
}
_TRIGGERS = {
    f'{table}_{action.lower()}': (
        f'CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} '
        "BEGIN SELECT RAISE(ABORT, 'immutable S3 record'); END")
    for table in _TABLES for action in ('UPDATE', 'DELETE')
}
for _table, _condition in (
        ('s3_control', 'singleton=NEW.singleton'),
        ('s3_contracts', 'contract_sha256=NEW.contract_sha256 OR (dossier_id=NEW.dossier_id AND version=NEW.version)'),
        ('s3_qualifications', 'qualification_id=NEW.qualification_id'),
        ('s3_approvals', 'contract_sha256=NEW.contract_sha256')):
    _TRIGGERS[_table + '_insert'] = (
        f'CREATE TRIGGER {_table}_insert BEFORE INSERT ON {_table} '
        f'WHEN EXISTS (SELECT 1 FROM {_table} WHERE {_condition}) '
        "BEGIN SELECT RAISE(ABORT, 'immutable S3 identity'); END")
_SPEC_FIELDS = ('result_expected', 'obligations', 'eliminatory_errors',
                'reference_piece_ids', 'method', 'witnesses', 'secondary_criteria',
                'aggregation', 'cost_basis', 'exposure', 'professional_review', 'limits')
_CONTRACT_FIELDS = ('dossier_id', 'revision', 'version', 'package', 'package_sha256',
                    'reference_pieces', 'specification')
_REVIEW_FIELDS = ('checks', 'limits', 'professional_review', 'assistance', 'disagreements')


def schema_objects():
    """Exact schema objects, included only when the extension is present"""
    return ([("table", name, name, sql) for name, sql in _TABLES.items()]
            + [("index", f'sqlite_autoindex_{name}_{i}', name, None)
               for name, count in (('s3_contracts', 2), ('s3_qualifications', 2), ('s3_approvals', 1))
               for i in range(1, count + 1)]
            + [("trigger", name, name.rsplit('_', 1)[0], sql) for name, sql in _TRIGGERS.items()])


def initialize(data):
    """Explicit additive extension of a recognized S2 fixture, including its history"""
    with closing(storage.Store(data)) as store:
        proof = store.verify_storage()
        if not proof['integrity_ok'] or proof['orphan_files']:
            raise IntegrityError('Stockage incomplet')
        connection = store._connection_checked()
        with _transaction(connection, write=True):
            layout = storage._check_schema(connection)
            if layout in ('s3', 's4', 's5'):
                return
            if layout != 's2':
                raise SchemaError('Extension explicite sur une base S2 requise')
            for sql in (*_TABLES.values(), *_TRIGGERS.values()):
                connection.execute(sql)
            connection.execute('INSERT INTO s3_control VALUES (1, ?)', (FORMAT_IDENTITY,))
            storage._check_schema(connection)


def connection_for(store):
    connection = store._connection_checked()
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s3_control'").fetchone():
        raise SchemaError('Initialisation explicite S3 requise')
    return connection


def digest(value):
    return sha256(encode(value).encode('utf-8')).hexdigest()


def _hash(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('Empreinte invalide')


def _texts(values, label, *, required=False, unique=False):
    if type(values) is not list or (required and not values):
        raise ValueError('Liste requise : ' + label)
    for value in values:
        _text(value, label)
        if not value.strip():
            raise ValueError('Texte vide : ' + label)
    if unique and len(values) != len(set(values)):
        raise ValueError('Identité répétée : ' + label)


def _professional(value):
    if value == 'ABSENTE':
        return
    _fields(value, ('author', 'phase', 'scope', 'proof'), 'professional_review')
    for key in value:
        if not value[key]:
            raise ValueError('Revue professionnelle sans preuve ou attribution')


def _aggregation(value):
    if value is None:
        return
    _fields(value, ('scope', 'cases', 'attempts', 'denominator', 'missing', 'incidents', 'indeterminate', 'rule'), 'aggregation')
    if any(not child for child in value.values()):
        raise ValueError('Agrégation incomplète')


def _specification(spec, *, legacy=False):
    if 'local_criterion_ids' not in spec and not legacy:
        raise ValueError('Critères de preuve locale explicites requis')
    extra = ('local_criterion_ids',) if 'local_criterion_ids' in spec else ()
    _fields(spec, _SPEC_FIELDS + extra, 'specification')
    encode(spec)
    _text(spec['result_expected'], 'result_expected')
    _text(spec['exposure'], 'exposure')
    _professional(spec['professional_review'])
    _texts(spec['limits'], 'limits')
    _texts(spec['reference_piece_ids'], 'reference_piece_ids', required=True, unique=True)
    method = spec['method']
    _fields(method, ('id', 'version', 'control_ids', 'expected_evidence', 'responsible_role'), 'method')
    for key in ('id', 'version', 'expected_evidence', 'responsible_role'):
        _text(method[key], key)
    _texts(method['control_ids'], 'control_ids', required=True, unique=True)
    controls = set(method['control_ids'])
    ids = set()
    for kind in ('obligations', 'eliminatory_errors'):
        entries = spec[kind]
        if type(entries) is not list or not entries:
            raise ValueError('Obligations et erreurs requises')
        for entry in entries:
            keys = ('id', 'description', 'control_ids')
            if kind == 'obligations':
                keys += ('use', 'tolerance')
            _fields(entry, keys, kind)
            for key in keys:
                if key != 'control_ids':
                    _text(entry[key], key)
            if entry['id'] in ids:
                raise ValueError('Critère répété')
            ids.add(entry['id'])
            _texts(entry['control_ids'], 'control_ids', required=True, unique=True)
            if not set(entry['control_ids']) <= controls:
                raise ValueError('Contrôle non déclaré')
    if 'local_criterion_ids' in spec:
        _texts(spec['local_criterion_ids'], 'local_criterion_ids', unique=True)
        if not set(spec['local_criterion_ids']) <= ids:
            raise ValueError('Critère local non déclaré')
    witnesses = spec['witnesses']
    if type(witnesses) is not dict or not witnesses or not set(witnesses) <= controls:
        raise ValueError('Témoins reliés aux contrôles requis')
    measures = spec['secondary_criteria']
    if type(measures) is not list or len(measures) > 2:
        raise ValueError('Zéro à deux critères secondaires')
    for measure in measures:
        _fields(measure, ('id', 'measure', 'proof', 'unit', 'favorable', 'aggregation'), 'secondary criterion')
        for key in ('id', 'measure', 'proof', 'unit', 'favorable'):
            _text(measure[key], key)
        if measure['id'] in ids or measure['favorable'] not in ('lower', 'higher', 'yes'):
            raise ValueError('Mesure répétée ou sens favorable inconnu')
        ids.add(measure['id'])
        _aggregation(measure['aggregation'])
    _aggregation(spec['aggregation'])
    cost = spec['cost_basis']
    _fields(cost, ('scope', 'attempts', 'unit', 'conversion'), 'cost_basis')
    for key in ('scope', 'attempts', 'unit'):
        _text(cost[key], key)
    if cost['conversion'] is not None:
        _fields(cost['conversion'], ('source', 'date', 'formula'), 'conversion')
        for key in cost['conversion']:
            _text(cost['conversion'][key], key)


def _decode(raw, expected):
    _hash(expected)
    value = json.loads(raw, object_pairs_hook=_unique_object)
    if encode(value) != raw or digest(value) != expected:
        raise IntegrityError('Octets S3 divergents')
    return value


def _package(store, connection, dossier_id, revision):
    from .preparation import package_check
    row = connection.execute('SELECT stage, package_json, package_sha256 FROM s2_revisions '
                             'WHERE dossier_id=? AND revision=?', (dossier_id, revision)).fetchone()
    if row is None or row[0] != 'preview' or row[1] is None:
        raise ValueError('Paquet S2 consultable requis')
    package = _decode(row[1], row[2])
    package_check(store, dossier_id, revision, package, row[2])
    return package, row[2]


def _references(store, dossier_id, revision, ids):
    pieces = []
    for piece_id in ids:
        meta = store.get_piece(piece_id)
        if (meta['dossier_id'], meta['revision'], meta['role']) != (dossier_id, revision, 'judge'):
            raise IntegrityError('Référence étrangère ou non réservée')
        store.read_piece(piece_id)
        pieces.append({'id': piece_id, **{key: meta[key] for key in ('name', 'sha256', 'size_bytes')}})
    return pieces


def _contract(store, connection, fingerprint):
    _hash(fingerprint)
    row = connection.execute('SELECT dossier_id, revision, version, contract_json FROM s3_contracts '
                             'WHERE contract_sha256=?', (fingerprint,)).fetchone()
    if row is None:
        raise KeyError(fingerprint)
    contract = _decode(row[3], fingerprint)
    _fields(contract, _CONTRACT_FIELDS, 'contract')
    if tuple(contract[k] for k in ('dossier_id', 'revision', 'version')) != row[:3]:
        raise IntegrityError('Identité contractuelle divergente')
    _identity(contract['dossier_id'], contract['revision'])
    if type(contract['version']) is not int or contract['version'] < 1:
        raise IntegrityError('Version invalide')
    _specification(contract['specification'], legacy=True)
    package, package_hash = _package(store, connection, *row[:2])
    if (contract['package'], contract['package_sha256']) != (package, package_hash):
        raise IntegrityError('Contrat sans paquet exact')
    references = _references(store, *row[:2], contract['specification']['reference_piece_ids'])
    if contract['reference_pieces'] != references:
        raise IntegrityError('Références contractuelles divergentes')
    return contract


def _current(store, connection, dossier_id, revision, fingerprint=None):
    if os.path.lexists(store._root / 'restore.json'):
        raise ValueError('Restauration à rapprocher')
    row = connection.execute('SELECT current_revision FROM s2_dossiers WHERE dossier_id=?', (dossier_id,)).fetchone()
    if row != (revision,):
        raise ConflictError('Révision périmée')
    if connection.execute("SELECT 1 FROM s2_actions a JOIN operations o USING(operation_id) "
                          "WHERE a.dossier_id=? AND o.state!='RECEIVED'", (dossier_id,)).fetchone():
        raise ConflictError('Préparation inachevée')
    if fingerprint is not None:
        latest = connection.execute('SELECT contract_sha256 FROM s3_contracts WHERE dossier_id=? '
                                    'ORDER BY version DESC LIMIT 1', (dossier_id,)).fetchone()
        if latest != (fingerprint,):
            raise ConflictError('Version contractuelle périmée')


def draft(store, dossier_id, revision, specification):
    _identity(dossier_id, revision)
    spec = deepcopy(specification)
    _specification(spec)
    connection = connection_for(store)
    with _transaction(connection, write=True):
        _current(store, connection, dossier_id, revision)
        verify_qualification(store, connection)
        package, package_hash = _package(store, connection, dossier_id, revision)
        references = _references(store, dossier_id, revision, spec['reference_piece_ids'])
        version = connection.execute('SELECT COALESCE(MAX(version), 0)+1 FROM s3_contracts '
                                     'WHERE dossier_id=?', (dossier_id,)).fetchone()[0]
        contract = dict(dossier_id=dossier_id, revision=revision, version=version,
                        package=package, package_sha256=package_hash,
                        reference_pieces=references, specification=spec)
        fingerprint = digest(contract)
        connection.execute('INSERT INTO s3_contracts VALUES (?,?,?,?,?)',
                           (fingerprint, dossier_id, revision, version, encode(contract)))
        return {'contract': contract, 'contract_sha256': fingerprint}


def _review_status(review, spec):
    _fields(review, _REVIEW_FIELDS, 'review')
    _texts(review['limits'], 'limits')
    _professional(review['professional_review'])
    if review['assistance'] is not None:
        # This local boundary has no authority, budget or transport for AI judgment
        raise ValueError('Assistance de jugement non admise dans cette interface locale')
    if type(review['disagreements']) is not list or type(review['checks']) is not list:
        raise ValueError('Constats de revue requis')
    seen = []
    passed = (review['professional_review'] == spec['professional_review']
              and set(spec['limits']) <= set(review['limits']))
    for check in review['checks']:
        _fields(check, ('control_id', 'status', 'finding', 'proof'), 'check')
        _text(check['control_id'], 'control_id')
        _text(check['finding'], 'finding')
        if check['status'] not in ('PASS', 'FAIL', 'INDETERMINE'):
            raise ValueError('État de contrôle inconnu')
        seen.append(check['control_id'])
        passed = (passed and check['status'] == 'PASS'
                  and type(check['proof']) is dict and bool(check['proof']))
    for disagreement in review['disagreements']:
        if type(disagreement) is not dict or not disagreement.get('arbitration') or not disagreement.get('proof'):
            passed = False
    return ('QUALIFIED' if passed and len(seen) == len(set(seen))
            and set(seen) == set(spec['method']['control_ids']) else 'BLOCKED')


def _qualification(raw, expected, qualification_id, fingerprint, spec):
    receipt = _decode(raw, expected)
    _fields(receipt, (*_REVIEW_FIELDS, 'qualification_id', 'contract_sha256', 'reviewer', 'created_at', 'status'), 'qualification')
    _text(receipt['reviewer'], 'reviewer')
    _text(receipt['created_at'], 'created_at')
    if (receipt['qualification_id'], receipt['contract_sha256']) != (qualification_id, fingerprint):
        raise IntegrityError('Qualification étrangère')
    review = {key: receipt[key] for key in _REVIEW_FIELDS}
    if receipt['status'] != _review_status(review, spec):
        raise IntegrityError('État de qualification divergent')
    return receipt


def _authority(actor, authority):
    _fields(authority, ('authority_id', 'actor'), 'authority')
    _text(actor, 'actor')
    _text(authority['authority_id'], 'authority_id')
    if authority['actor'] != actor:
        raise ValueError('Autorité étrangère')
    # Test identities are isolated from the locally designated real operator
    if actor == 'responsable-fictif-S3':
        if authority['authority_id'] != 'TEST_ONLY_APPROVAL_S3':
            raise ValueError('Autorité fictive requise')
    elif actor != 'Ayo' or authority['authority_id'].startswith('TEST_ONLY'):
        raise ValueError('Approbateur local non désigné')


def _validated(connection, contract):
    row = connection.execute('SELECT 1 FROM s2_validations v JOIN s2_dossiers d USING(dossier_id) '
                             'WHERE v.dossier_id=? AND v.revision=? AND v.package_sha256=? '
                             'AND v.session_id=d.session_id',
                             tuple(contract[k] for k in ('dossier_id', 'revision', 'package_sha256'))).fetchone()
    if row is None:
        raise ValueError('Validation du besoin requise pour ce paquet')


def _inspect(store, connection, fingerprint):
    contract = _contract(store, connection, fingerprint)
    qualifications = [_qualification(raw, expected, qid, fingerprint, contract['specification'])
                      for qid, raw, expected in connection.execute(
                          'SELECT qualification_id, receipt_json, receipt_sha256 FROM s3_qualifications '
                          'WHERE contract_sha256=? ORDER BY rowid', (fingerprint,))]
    approval = None
    row = connection.execute('SELECT qualification_id, approval_json, approval_sha256 FROM s3_approvals '
                             'WHERE contract_sha256=?', (fingerprint,)).fetchone()
    if row:
        approval = _decode(row[1], row[2])
        _fields(approval, ('contract_sha256', 'qualification_id', 'actor', 'authority_id', 'approved_at'), 'approval')
        _authority(approval['actor'], {key: approval[key] for key in ('actor', 'authority_id')})
        _text(approval['approved_at'], 'approved_at')
        if (approval['contract_sha256'], approval['qualification_id']) != (fingerprint, row[0]):
            raise IntegrityError('Approbation étrangère')
        if not qualifications or qualifications[-1]['qualification_id'] != row[0] or qualifications[-1]['status'] != 'QUALIFIED':
            raise IntegrityError('Approbation sans qualification complète')
        _validated(connection, contract)
    return dict(contract=contract, contract_sha256=fingerprint, qualifications=qualifications, approval=approval)


def inspect_contract(store, contract_sha256):
    connection = connection_for(store)
    with _transaction(connection):
        return _inspect(store, connection, contract_sha256)


def qualify(store, contract_sha256, *, reviewer, check):
    _text(reviewer, 'reviewer')
    if not callable(check):
        raise ValueError('Contrôleur local injecté requis')
    connection = connection_for(store)
    with _transaction(connection, write=True):
        snapshot = _inspect(store, connection, contract_sha256)
        contract = snapshot['contract']
        _current(store, connection, contract['dossier_id'], contract['revision'], contract_sha256)
        if snapshot['approval'] is not None:
            raise ConflictError('Contrat approuvé : nouvelle version requise')
        resources = {p['id']: store.read_piece(p['id'])
                     for p in contract['package']['pieces'] + contract['reference_pieces']}
        review = deepcopy(check(deepcopy(contract), resources))
        encode(review)
        status = _review_status(review, contract['specification'])
        # Recheck after the trusted callback as well, before persisting evidence
        if _contract(store, connection, contract_sha256) != contract:
            raise IntegrityError('Contrat changé pendant le contrôle')
        _current(store, connection, contract['dossier_id'], contract['revision'], contract_sha256)
        receipt = dict(review, status=status, qualification_id=secrets.token_hex(16),
                       contract_sha256=contract_sha256, reviewer=reviewer,
                       created_at=datetime.now(timezone.utc).isoformat())
        connection.execute('INSERT INTO s3_qualifications VALUES (?,?,?,?)',
                           (receipt['qualification_id'], contract_sha256, encode(receipt), digest(receipt)))
        return receipt


def approve(store, contract_sha256, qualification_id, *, actor, authority):
    _authority(actor, authority)
    _text(qualification_id, 'qualification_id')
    connection = connection_for(store)
    with _transaction(connection, write=True):
        snapshot = _inspect(store, connection, contract_sha256)
        contract = snapshot['contract']
        _current(store, connection, contract['dossier_id'], contract['revision'], contract_sha256)
        _validated(connection, contract)
        qualifications = snapshot['qualifications']
        if not qualifications or qualifications[-1]['qualification_id'] != qualification_id or qualifications[-1]['status'] != 'QUALIFIED':
            raise ValueError('Dernière qualification complète de ces octets requise')
        if snapshot['approval'] is not None:
            previous = snapshot['approval']
            if (previous['actor'], previous['authority_id']) != (actor, authority['authority_id']):
                raise ConflictError('Approbation déjà conservée sous une autre autorité')
            return previous
        approval = dict(contract_sha256=contract_sha256, qualification_id=qualification_id,
                        actor=actor, authority_id=authority['authority_id'],
                        approved_at=datetime.now(timezone.utc).isoformat())
        connection.execute('INSERT INTO s3_approvals VALUES (?,?,?,?)',
                           (contract_sha256, qualification_id, encode(approval), digest(approval)))
        return approval


def verify_qualification(store, connection):
    """Verify history inside the storage caller's coherent snapshot"""
    for (fingerprint,) in connection.execute('SELECT contract_sha256 FROM s3_contracts').fetchall():
        _inspect(store, connection, fingerprint)


def projection(store, connection, dossier_id, revision, *, eligible):
    """Only statuses and the contract identity cross the requester boundary"""
    result = dict(status='PENDING', contract_sha256=None, qualification_status='PENDING',
                  approval_status='PENDING')
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s3_control'").fetchone():
        return result
    row = connection.execute('SELECT contract_sha256 FROM s3_contracts WHERE dossier_id=? AND revision=? '
                             'ORDER BY version DESC LIMIT 1', (dossier_id, revision)).fetchone()
    if row is None:
        return result
    result['contract_sha256'] = row[0]
    try:
        snapshot = _inspect(store, connection, row[0])
        receipts = snapshot['qualifications']
        state = receipts[-1]['status'] if receipts else 'PENDING'
        result['qualification_status'] = state
        result['status'] = state
        if eligible and snapshot['approval'] is not None:
            result.update(status='APPROVED', approval_status='APPROVED')
        elif not eligible:
            result['status'] = 'VALIDATION_REQUIRED'
    except (ValueError, KeyError):
        result.update(status='BLOCKED', qualification_status='BLOCKED')
    return result
