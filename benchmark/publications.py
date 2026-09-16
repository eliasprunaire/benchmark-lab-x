"""Format, vérification, lecture et activation des projections fictives locales S6.

Ce module ne connaît ni campagne, ni évaluation, ni préparation, ni qualification :
il valide des octets déjà approuvés et les sert depuis le seul répertoire public.
Il n'ouvre aucun stockage et n'instancie aucun Store.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import stat

from .storage import _strict_json as encode, _unique_object
from .validation import identifier, _hash, _texts

SCHEMA = 'benchmark-lab-x/restitution-fictional/v1'
PRESENTATION_VERSION = '4'
PRESENTATION_VERSIONS = ('1', '2', '3', '4')
_FILES = re.compile(r'[A-Za-z0-9_-]+\.(?:html|css|txt)\Z')


def _decode(raw):
    def invalid(value):
        raise ValueError('Constante JSON invalide')
    return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=invalid)


def _approval(approval, identity):
    expected = dict(actor='approbateur-fictif-S6', authority_id='TEST_ONLY_PUBLICATION_S6',
                    projection_sha256=identity, catalogue=False)
    if type(approval) is not dict or approval != expected or approval.get('catalogue') is not False:
        raise ValueError('Approbation fictive exacte requise')


def _manifest(raw, identity):
    if type(raw) is not bytes or type(identity) is not str or not re.fullmatch('[0-9a-f]{64}', identity):
        raise ValueError('Identité de projection invalide')
    if sha256(raw).hexdigest() != identity:
        raise ValueError('Manifeste divergent')
    m = _decode(raw)
    keys = {'schema_version', 'presentation_version', 'conclusion_version', 'campaign_id',
            'contract_sha256', 'task', 'evaluation_ids', 'limits', 'pieces', 'files'}
    if type(m) is not dict or set(m) != keys or m['schema_version'] != SCHEMA:
        raise ValueError('Schéma de projection fictive inconnu')
    if m['presentation_version'] not in PRESENTATION_VERSIONS or m['conclusion_version'] != '1':
        raise ValueError('Version de restitution inconnue')
    identifier(m['campaign_id'])
    _hash(m['contract_sha256'])
    if type(m['task']) is not dict or set(m['task']) != {'dossier_id', 'version', 'revision', 'package_sha256'}:
        raise ValueError('Identité de tâche requise')
    identifier(m['task']['dossier_id'])
    _hash(m['task']['package_sha256'])
    if any(type(m['task'][k]) is not int or m['task'][k] < 1 for k in ('revision', 'version')):
        raise ValueError('Version de tâche invalide')
    for key in ('evaluation_ids', 'limits'):
        _texts(m[key], key, unique=True)
    for eid in m['evaluation_ids']:
        identifier(eid)
    if type(m['files']) is not dict or not {'index.html', 'style.css'} <= m['files'].keys():
        raise ValueError('Fichiers de projection requis')
    for name, digest in m['files'].items():
        if not _FILES.fullmatch(name):
            raise ValueError('Nom de pièce publique invalide')
        _hash(digest)
    if type(m['pieces']) is not dict:
        raise ValueError('Pièces sélectionnées requises')
    for pid, piece in m['pieces'].items():
        identifier(pid)
        if (type(piece) is not dict or set(piece) != {'file', 'sha256'}
                or piece['file'] != 'piece-' + pid + '.txt'
                or m['files'].get(piece['file']) != piece['sha256']):
            raise ValueError('Pièce sélectionnée divergente')
    if set(m['files']) != {'index.html', 'style.css'} | {v['file'] for v in m['pieces'].values()}:
        raise ValueError('Fichier public non sélectionné')
    return m


def _bundle(bundle, approval):
    if type(bundle) is not dict or set(bundle) != {'manifest', 'files', 'projection_sha256'}:
        raise ValueError('Paquet de projection invalide')
    m = _manifest(bundle['manifest'], bundle['projection_sha256'])
    _approval(approval, bundle['projection_sha256'])
    if type(bundle['files']) is not dict or set(bundle['files']) != set(m['files']):
        raise ValueError('Sélection de fichiers divergente')
    for name, raw in bundle['files'].items():
        if type(raw) is not bytes or sha256(raw).hexdigest() != m['files'][name]:
            raise ValueError('Octets de projection divergents')
    return m


def _directory(path):
    """Open each directory without following a symbolic link, including ancestors"""
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(fd, name):
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(file_fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Fichier ordinaire requis')
        return stream.read()


def _read_bundle(fd, identity):
    raw = _read(fd, 'publication.json')
    m = _manifest(raw, identity)
    approval = _decode(_read(fd, 'approval.json'))
    _approval(approval, identity)
    bundle = dict(manifest=raw, projection_sha256=identity,
                  files={name: _read(fd, name) for name in m['files']})
    _bundle(bundle, approval)
    return bundle


def public_bytes(destination, identity, name):
    """Verify an S6 identity using only the public directory, independent of active.json"""
    if type(identity) is not str or not re.fullmatch('[0-9a-f]{64}', identity) or not _FILES.fullmatch(name):
        raise ValueError('Chemin public invalide')
    root = _directory(destination)
    try:
        folder = os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        try:
            bundle = _read_bundle(folder, identity)
            if name not in bundle['files']:
                raise ValueError('Pièce publique non déclarée')
            return bundle['files'][name]
        finally:
            os.close(folder)
    finally:
        os.close(root)


def _write(fd, name, raw):
    file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(file_fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def materialize(bundle, approval, destination):
    """Validate all bytes before atomically selecting a complete fictional local bundle"""
    bundle, approval = deepcopy(bundle), deepcopy(approval)
    _bundle(bundle, approval)
    identity = bundle['projection_sha256']
    root = _directory(destination)
    stage = '.s6-' + secrets.token_hex(16)
    pointer = '.active-' + secrets.token_hex(16)
    folder = None
    staged = False
    try:
        os.mkdir(stage, mode=0o700, dir_fd=root)
        staged = True
        folder = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        for name, raw in dict(bundle['files'], **{'publication.json': bundle['manifest'],
                                                'approval.json': encode(approval).encode('utf-8')}).items():
            _write(folder, name, raw)
        _read_bundle(folder, identity)
        os.fsync(folder)
        try:
            existing = os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        except FileNotFoundError:
            os.rename(stage, identity, src_dir_fd=root, dst_dir_fd=root)
            staged = False
        else:
            try:
                if _read_bundle(existing, identity) != bundle:
                    raise ValueError('Projection existante divergente')
            finally:
                os.close(existing)
        os.fsync(root)
        _write(root, pointer, encode({'directory': identity}).encode('utf-8'))
        os.replace(pointer, 'active.json', src_dir_fd=root, dst_dir_fd=root)
        os.fsync(root)
    finally:
        if folder is not None:
            if staged:
                for name in os.listdir(folder):
                    os.unlink(name, dir_fd=folder)
            os.close(folder)
        if staged:
            os.rmdir(stage, dir_fd=root)
        try:
            os.unlink(pointer, dir_fd=root)
        except FileNotFoundError:
            pass
        os.close(root)
