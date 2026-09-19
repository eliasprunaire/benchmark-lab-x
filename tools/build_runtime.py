"""Construire les octets suivis d'un commit produit, sans dépendance de build."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

SEMVER_IDENTIFIER = r'(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)'
SEMVER = re.compile(
    rf'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
    rf'(?:-{SEMVER_IDENTIFIER}(?:\.{SEMVER_IDENTIFIER})*)?'
    rf'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$')


def git(repo, *arguments):
    return subprocess.check_output(['git', '-C', str(repo), *arguments])


def git_blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def runtime_version(raw, requested):
    match = re.search(rb"^VERSION = '([^']+)'$", raw, re.MULTILINE)
    if match is None:
        raise ValueError('Version produit absente')
    version = match[1].decode() if requested is None else requested
    if SEMVER.fullmatch(version) is None:
        raise ValueError('Version produit invalide')
    if requested is not None:
        raw = re.sub(rb"^VERSION = '[^']+'$",
                     ("VERSION = '" + version + "'").encode(), raw, count=1, flags=re.MULTILINE)
    return raw, version


def build(repo, source, destination, version=None):
    if not re.fullmatch('[0-9a-f]{40}', source):
        raise ValueError('Commit complet requis')
    if git(repo, 'rev-parse', source + '^{commit}').decode().strip() != source:
        raise ValueError('Identité source divergente')
    recipe = git(repo, 'show', source + ':tools/build_runtime.py')
    if recipe != Path(__file__).read_bytes():
        raise ValueError('Configuration de build différente du commit demandé')
    files = {}
    modes = {}
    blobs = {}
    for entry in git(repo, 'ls-tree', '-rz', source, '--', 'benchmark', 'benchmark_web').split(b'\0'):
        if not entry:
            continue
        metadata, raw_name = entry.split(b'\t', 1)
        mode, kind, blob = metadata.decode().split()
        name = raw_name.decode('utf-8')
        if kind != 'blob' or mode not in {'100644', '100755'}:
            raise ValueError('Source non régulière')
        if not re.fullmatch(r'benchmark(?:_web)?/[A-Za-z0-9_./-]+', name) or '..' in Path(name).parts or any(part.startswith('.') for part in Path(name).parts):
            raise ValueError('Chemin source interdit')
        files[name] = git(repo, 'cat-file', 'blob', blob)
        blobs[name] = blob
        modes[name] = 0o755 if mode == '100755' else 0o644
    if not {'benchmark/storage.py', 'benchmark/runtime.py', 'benchmark/service.py', 'benchmark/benchmark-runtime', 'benchmark/__init__.py',
            'benchmark/model_catalogue.py', 'benchmark/models.toml',
            'benchmark_web/__init__.py', 'benchmark_web/server.py', 'benchmark_web/views.py', 'benchmark_web/projection.py'} <= files.keys():
        raise ValueError('Interfaces runtime absentes du commit')
    if 'benchmark/__init__.py' not in files:
        raise ValueError('Version produit absente')
    files['benchmark/__init__.py'], product_version = runtime_version(files['benchmark/__init__.py'], version)
    if version is not None:
        blobs['benchmark/__init__.py'] = git_blob(files['benchmark/__init__.py'])
    # Le schéma livré vient du commit construit, sans importer du code non approuvé
    match = re.search(rb'^SCHEMA_VERSION = ([0-9]+)$', files['benchmark/storage.py'], re.MULTILINE)
    if match is None:
        raise ValueError('Version de stockage absente')
    manifest = {'source_sha': source, 'version': product_version,
                'schema_version': int(match[1]),
                'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}
    files['release.json'] = (json.dumps(manifest, sort_keys=True) + '\n').encode()
    with Path(destination).open('xb') as target:
        with gzip.GzipFile(filename='', mode='wb', fileobj=target, mtime=0) as compressed:
            with tarfile.open(mode='w', fileobj=compressed, format=tarfile.USTAR_FORMAT) as archive:
                for name, raw in sorted(files.items()):
                    info = tarfile.TarInfo(name)
                    info.size = len(raw)
                    info.mode = modes.get(name, 0o644)
                    archive.addfile(info, io.BytesIO(raw))
    return {'source_sha': source, 'version': product_version,
            'recipe_sha256': hashlib.sha256(recipe).hexdigest(),
            'tree_sha': git(repo, 'rev-parse', source + '^{tree}').decode().strip(),
            'source_blobs': blobs,
            'artifact_sha256': hashlib.sha256(Path(destination).read_bytes()).hexdigest(),
            'schema_version': manifest['schema_version']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(build(args.repo, args.source, args.output, args.version), sort_keys=True))


if __name__ == '__main__':
    main()
