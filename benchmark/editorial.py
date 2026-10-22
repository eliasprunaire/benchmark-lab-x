"""Publication, lecture et retrait d’une épreuve éditoriale.

Ce module ne connaît ni campagne, ni stockage privé, ni qualification réelle.
Il vérifie une autorité et des octets déjà soumis. L’admission et le lecteur
de production n’ouvrent que le répertoire public, et seulement pour l’autorité
désignée. L’activation de fixture réutilise la même validation dans un
répertoire séparé : elle ne devient pas une consultation de production.
Les projections fictives historiques restent dans `publications`.
"""
from copy import deepcopy
from hashlib import sha256
import os
import secrets

from .publications import _decode, _directory, _read, _write
from .storage import _strict_json as encode, _text
from .validation import identifier, _hash, _texts

SCHEMA = 'benchmark-lab-x/editorial-trial/v1'
_ROOT = 'editorial-trials'
_FIXTURE_ROOT = 'editorial-fixture'
_FIXTURE = ('approbateur-fictif-epreuve', 'FIXTURE_EDITORIAL_TRIAL')
_PRODUCTION = ('Ayo', 'EDITORIAL_TRIAL')
_QUALIFICATION = ('ABSENT', 'QUALIFIED', 'BLOCKED')
_MANIFEST = {'schema_version', 'task_id', 'version_id', 'title', 'work', 'cases', 'limits',
             'qualification_status', 'results_published', 'pieces'}
_PIECE = {'file', 'sha256', 'label'}
_FIXED = {'manifest.json', 'approval.json'}


class NotFound(LookupError):
    """Aucune épreuve publiée à cette adresse"""


def fixture_approval(package_sha256):
    """Autorité de fixture, distincte de l’approbation de production et de S6"""
    _hash(package_sha256)
    return dict(actor=_FIXTURE[0], authority_id=_FIXTURE[1], package_sha256=package_sha256, scope='editorial-trial')


def fixture_withdrawal(package_sha256, task_id, version_id):
    """Décision de retrait de fixture pour ces octets et cette version"""
    _hash(package_sha256)
    identifier(task_id)
    identifier(version_id)
    return dict(actor=_FIXTURE[0], authority_id=_FIXTURE[1], package_sha256=package_sha256,
                scope='editorial-withdrawal', task_id=task_id, version_id=version_id)


def _document(actor, identity):
    return dict(actor=actor[0], authority_id=actor[1], package_sha256=identity, scope='editorial-trial')


def _publish_approval(approval, identity, actor):
    if type(approval) is not dict or not approval:
        raise ValueError('Autorité absente')
    if type(approval.get('package_sha256')) is str and approval['package_sha256'] != identity:
        raise ValueError('Approbation visant d’autres octets')
    if approval != _document(actor, identity):
        raise ValueError('Autorité éditoriale refusée')


def _withdrawal_document(publication, identity, task_id, version_id):
    return dict(actor=publication['actor'], authority_id=publication['authority_id'], package_sha256=identity,
                scope='editorial-withdrawal', task_id=task_id, version_id=version_id)


def _manifest(raw):
    if type(raw) is not bytes:
        raise ValueError('Paquet incomplet')
    try:
        manifest = _decode(raw)
    except ValueError:
        raise ValueError('Paquet incomplet') from None
    if type(manifest) is not dict:
        raise ValueError('Paquet incomplet')
    if set(manifest) - _MANIFEST:
        raise ValueError('Sélection publique fermée')
    if set(manifest) != _MANIFEST or manifest['schema_version'] != SCHEMA:
        raise ValueError('Paquet incomplet')
    identifier(manifest['task_id'])
    identifier(manifest['version_id'])
    _text(manifest['title'], 'title')
    _text(manifest['work'], 'work')
    _texts(manifest['cases'], 'cases', required=True)
    _texts(manifest['limits'], 'limits', required=True)
    if manifest['qualification_status'] not in _QUALIFICATION:
        raise ValueError('État de qualification invalide')
    if manifest['results_published'] is not False:
        raise ValueError('Sélection publique fermée')
    pieces = manifest['pieces']
    if type(pieces) is not dict or not pieces:
        raise ValueError('Paquet incomplet')
    names = set()
    for pid, piece in pieces.items():
        identifier(pid)
        if type(piece) is not dict:
            raise ValueError('Paquet incomplet')
        if set(piece) - _PIECE:
            raise ValueError('Sélection publique fermée')
        if set(piece) != _PIECE:
            raise ValueError('Paquet incomplet')
        if piece['file'] != 'piece-' + pid + '.txt' or piece['file'] in names:
            raise ValueError('Sélection publique fermée')
        names.add(piece['file'])
        _hash(piece['sha256'])
        _text(piece['label'], 'label')
    return manifest


def _validated(bundle, approval, actor):
    if type(bundle) is not dict or set(bundle) != {'manifest', 'files', 'package_sha256'}:
        raise ValueError('Paquet incomplet')
    raw, identity, files = bundle['manifest'], bundle['package_sha256'], bundle['files']
    if type(raw) is not bytes or type(identity) is not str or sha256(raw).hexdigest() != identity:
        raise ValueError('Manifeste divergent')
    _publish_approval(approval, identity, actor)
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


def _names(fd, pieces):
    names = set(os.listdir(fd))
    allowed = set(_FIXED)
    allowed.update(piece['file'] for piece in pieces.values())
    if 'withdrawal.json' in names:
        allowed.add('withdrawal.json')
    if names != allowed:
        raise ValueError('Sélection publique fermée')


def _withdrawal(fd, publication, identity, task_id, version_id):
    try:
        raw = _read(fd, 'withdrawal.json')
    except FileNotFoundError:
        return False
    found = _decode(raw)
    if found != _withdrawal_document(publication, identity, task_id, version_id):
        raise ValueError('Retrait divergent')
    return True


def _bytes(fd, name):
    raw = _read(fd, name)
    if type(raw) is not bytes:
        raise ValueError('Octets divergents')
    return raw


def _read_record(fd, task_id, version_id, actor):
    manifest_raw = _bytes(fd, 'manifest.json')
    identity = sha256(manifest_raw).hexdigest()
    publication = _decode(_bytes(fd, 'approval.json'))
    _publish_approval(publication, identity, actor)
    manifest = _manifest(manifest_raw)
    if (manifest['task_id'], manifest['version_id']) != (task_id, version_id):
        raise ValueError('Identité divergente')
    _names(fd, manifest['pieces'])
    pieces = []
    for pid, piece in manifest['pieces'].items():
        raw = _bytes(fd, piece['file'])
        if sha256(raw).hexdigest() != piece['sha256']:
            raise ValueError('Octets divergents')
        pieces.append(dict(id=pid, label=piece['label'], file=piece['file'], raw=raw, text=raw.decode('utf-8')))
    withdrawn = _withdrawal(fd, publication, identity, task_id, version_id)
    return dict(identity=identity, manifest_raw=manifest_raw, approval=publication, manifest=manifest,
                pieces=pieces, withdrawn=withdrawn)


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


def _open_version(root, task_id, version_id, root_name):
    identifier(task_id)
    identifier(version_id)
    try:
        trials = _open_child(root, root_name)
    except FileNotFoundError:
        raise NotFound() from None
    try:
        try:
            task = _open_child(trials, task_id)
        except FileNotFoundError:
            raise NotFound() from None
        try:
            try:
                return _open_child(task, version_id)
            except FileNotFoundError:
                raise NotFound() from None
        finally:
            os.close(task)
    finally:
        os.close(trials)


def _load(destination, task_id, version_id, actor, root_name):
    root = _directory(destination)
    try:
        folder = _open_version(root, task_id, version_id, root_name)
        try:
            return _read_record(folder, task_id, version_id, actor)
        finally:
            os.close(folder)
    finally:
        os.close(root)


def _same(record, bundle, approval):
    found = {piece['file']: piece['raw'] for piece in record['pieces']}
    return record['manifest_raw'] == bundle['manifest'] and record['approval'] == approval and found == bundle['files']


def _activate(parent, manifest, bundle, approval, actor):
    task_id, version_id = manifest['task_id'], manifest['version_id']
    try:
        existing = _open_child(parent, version_id)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        try:
            record = _read_record(existing, task_id, version_id, actor)
            if record['withdrawn']:
                raise ValueError('Version retirée')
            if not _same(record, bundle, approval):
                raise ValueError('Version existante divergente')
            return
        finally:
            os.close(existing)
    stage = '.e-' + secrets.token_hex(16)
    folder = None
    staged = False
    try:
        os.mkdir(stage, 0o700, dir_fd=parent)
        staged = True
        folder = _open_child(parent, stage)
        _write(folder, 'manifest.json', bundle['manifest'])
        _write(folder, 'approval.json', encode(approval).encode())
        for name, raw in bundle['files'].items():
            _write(folder, name, raw)
        os.fsync(folder)
        record = _read_record(folder, task_id, version_id, actor)
        if record['withdrawn'] or not _same(record, bundle, approval):
            raise ValueError('Paquet incomplet')
        os.rename(stage, version_id, src_dir_fd=parent, dst_dir_fd=parent)
        staged = False
        os.fsync(parent)
    finally:
        if folder is not None:
            if staged:
                for name in os.listdir(folder):
                    os.unlink(name, dir_fd=folder)
            os.close(folder)
        if staged:
            os.rmdir(stage, dir_fd=parent)


def _publish(destination, bundle, approval, actor, root_name):
    bundle, approval = deepcopy(bundle), deepcopy(approval)
    manifest = _validated(bundle, approval, actor)
    root = _directory(destination)
    try:
        trials = _ensure_dir(root, root_name)
        try:
            task = _ensure_dir(trials, manifest['task_id'])
            try:
                _activate(task, manifest, bundle, approval, actor)
            finally:
                os.close(task)
        finally:
            os.close(trials)
    finally:
        os.close(root)


def publish(destination, bundle, approval):
    """Active un paquet complet sous l’autorité de production, ou refuse avant toute écriture"""
    _publish(destination, bundle, approval, _PRODUCTION, _ROOT)


def fixture_publish(destination, bundle, approval):
    """Active un paquet de fixture hors du répertoire lu par la production"""
    _publish(destination, bundle, approval, _FIXTURE, _FIXTURE_ROOT)


def _withdraw(destination, task_id, version_id, approval, actor, root_name):
    """Conserve la trace du retrait sans modifier les octets approuvés"""
    approval = deepcopy(approval)
    root = _directory(destination)
    try:
        folder = _open_version(root, task_id, version_id, root_name)
        try:
            record = _read_record(folder, task_id, version_id, actor)
            expected = _withdrawal_document(record['approval'], record['identity'], task_id, version_id)
            if approval != expected:
                if type(approval) is dict and type(approval.get('package_sha256')) is str and approval['package_sha256'] != record['identity']:
                    raise ValueError('Approbation visant d’autres octets')
                raise ValueError('Autorité éditoriale refusée')
            raw = encode(approval).encode()
            try:
                current = _read(folder, 'withdrawal.json')
            except FileNotFoundError:
                current = None
            if current is not None:
                if current != raw:
                    raise ValueError('Retrait déjà conservé')
                return
            before = record['manifest_raw']
            pieces = {piece['file']: piece['raw'] for piece in record['pieces']}
            pointer = '.w-' + secrets.token_hex(16)
            written = False
            try:
                _write(folder, pointer, raw)
                written = True
                os.rename(pointer, 'withdrawal.json', src_dir_fd=folder, dst_dir_fd=folder)
                written = False
                os.fsync(folder)
                again = _read_record(folder, task_id, version_id, actor)
                if again['manifest_raw'] != before or {piece['file']: piece['raw'] for piece in again['pieces']} != pieces or not again['withdrawn']:
                    raise ValueError('Octets approuvés modifiés')
            finally:
                if written:
                    try:
                        os.unlink(pointer, dir_fd=folder)
                    except FileNotFoundError:
                        pass
        finally:
            os.close(folder)
    finally:
        os.close(root)


def withdraw(destination, task_id, version_id, approval):
    """Retire une version de production sans modifier ses octets approuvés"""
    _withdraw(destination, task_id, version_id, approval, _PRODUCTION, _ROOT)


def fixture_withdraw(destination, task_id, version_id, approval):
    """Retire une version de fixture sans modifier ses octets approuvés"""
    _withdraw(destination, task_id, version_id, approval, _FIXTURE, _FIXTURE_ROOT)


def _reprise(destination, task_id, version_id, actor, root_name):
    try:
        record = _load(destination, task_id, version_id, actor, root_name)
    except NotFound:
        raise ValueError('Version publiée requise') from None
    if record['withdrawn']:
        raise ValueError('Nouvelle reprise refusée')
    return dict(task_id=task_id, version_id=version_id, package_sha256=record['identity'])


def reprise_admission(destination, task_id, version_id):
    """Identité de production admissible pour une reprise future, sans copier le paquet"""
    return _reprise(destination, task_id, version_id, _PRODUCTION, _ROOT)


def fixture_reprise(destination, task_id, version_id):
    """Identité de fixture admissible pour une reprise future, sans copier le paquet"""
    return _reprise(destination, task_id, version_id, _FIXTURE, _FIXTURE_ROOT)


def _is_id(name):
    try:
        identifier(name)
    except ValueError:
        return False
    return True


def _catalog(destination, actor, root_name):
    """Versions encore disponibles. Une version retirée, illisible ou d’une autre autorité n’y figure pas."""
    entries = []
    root = _directory(destination)
    try:
        try:
            trials = _open_child(root, root_name)
        except FileNotFoundError:
            return dict(kind='editorial', versions=[])
        try:
            for task_id in sorted(name for name in os.listdir(trials) if _is_id(name)):
                try:
                    task = _open_child(trials, task_id)
                except OSError:
                    continue
                try:
                    for version_id in sorted(name for name in os.listdir(task) if _is_id(name)):
                        try:
                            folder = _open_child(task, version_id)
                        except OSError:
                            continue
                        record = None
                        try:
                            record = _read_record(folder, task_id, version_id, actor)
                        except (OSError, ValueError):
                            record = None
                        finally:
                            os.close(folder)
                        if record is None or record['withdrawn']:
                            continue
                        entries.append(dict(task_id=task_id, version_id=version_id, title=record['manifest']['title']))
                finally:
                    os.close(task)
        finally:
            os.close(trials)
    finally:
        os.close(root)
    return dict(kind='editorial', versions=entries)


def _version(destination, task_id, version_id, actor, root_name):
    record = _load(destination, task_id, version_id, actor, root_name)
    if record['withdrawn']:
        return dict(kind='editorial', withdrawn=True, task_id=task_id, version_id=version_id)
    manifest = record['manifest']
    return dict(kind='editorial', withdrawn=False, task_id=task_id, version_id=version_id, title=manifest['title'],
                work=manifest['work'], cases=list(manifest['cases']), limits=list(manifest['limits']),
                qualification_status=manifest['qualification_status'],
                pieces=[dict(id=piece['id'], label=piece['label'], text=piece['text']) for piece in record['pieces']])


def _piece(destination, task_id, version_id, piece_id, actor, root_name):
    identifier(piece_id)
    record = _load(destination, task_id, version_id, actor, root_name)
    if record['withdrawn']:
        raise NotFound()
    for piece in record['pieces']:
        if piece['id'] == piece_id:
            return piece['raw']
    raise NotFound()


def catalog_view(destination):
    """Versions de production encore disponibles"""
    return _catalog(destination, _PRODUCTION, _ROOT)


def version_view(destination, task_id, version_id):
    """Notice de production, ou version retirée sans son contenu"""
    return _version(destination, task_id, version_id, _PRODUCTION, _ROOT)


def piece_bytes(destination, task_id, version_id, piece_id):
    """Octets d’une pièce de production encore disponible"""
    return _piece(destination, task_id, version_id, piece_id, _PRODUCTION, _ROOT)


def fixture_catalog(destination):
    """Versions de fixture encore disponibles, hors du lecteur de production"""
    return _catalog(destination, _FIXTURE, _FIXTURE_ROOT)


def fixture_version(destination, task_id, version_id):
    """Notice de fixture, hors du lecteur de production"""
    return _version(destination, task_id, version_id, _FIXTURE, _FIXTURE_ROOT)


def fixture_piece(destination, task_id, version_id, piece_id):
    """Octets d’une pièce de fixture, hors du lecteur de production"""
    return _piece(destination, task_id, version_id, piece_id, _FIXTURE, _FIXTURE_ROOT)
