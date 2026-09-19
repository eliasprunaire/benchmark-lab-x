"""Calculer la prochaine version produit depuis les commits Conventional Commits."""
import argparse
import json
import re
import subprocess
from pathlib import Path


SEMVER = re.compile(r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')
COMMIT = re.compile(r'^(?P<kind>[a-z]+)(?:\([^)]*\))?(?P<breaking>!)?:\s')


def git(repo, *arguments):
    return subprocess.check_output(['git', '-C', str(repo), *arguments], text=True).strip()


def classify(subject, body=''):
    match = COMMIT.match(subject)
    if match is None:
        return None
    if match['breaking'] or re.search(r'^BREAKING CHANGE:', body, re.MULTILINE):
        return 'breaking'
    return {'feat': 'minor', 'fix': 'patch'}.get(match['kind'])


def bump(version, level):
    major, minor, patch = (int(part) for part in version.split('.'))
    if level == 'breaking':
        return f'{major + 1}.0.0' if major else f'0.{minor + 1}.0'
    if level == 'minor':
        return f'{major}.{minor + 1}.0'
    if level == 'patch':
        return f'{major}.{minor}.{patch + 1}'
    raise ValueError('Niveau SemVer inconnu')


def next_level(messages):
    levels = {classify(subject, body) for subject, body in messages}
    if 'breaking' in levels:
        return 'breaking'
    if 'minor' in levels:
        return 'minor'
    if 'patch' in levels:
        return 'patch'
    return None


def messages(repo, base, head):
    raw = subprocess.check_output(
        ['git', '-C', str(repo), 'log', '--no-merges', '--format=%s%x00%b%x1e', f'{base}..{head}'],
        text=True)
    result = []
    for entry in raw.split('\x1e'):
        fields = entry.rstrip('\n').split('\x00', 1)
        if len(fields) == 2 and fields[0]:
            result.append((fields[0], fields[1]))
    return result


def source_version(repo, head):
    raw = subprocess.check_output(
        ['git', '-C', str(repo), 'show', f'{head}:benchmark/__init__.py'], text=True)
    match = re.search(r"^VERSION = '([^']+)'$", raw, re.MULTILINE)
    if match is None or SEMVER.fullmatch(match[1]) is None:
        raise ValueError('Version source absente ou invalide')
    return match[1]


def latest_tag(repo, head):
    candidates = []
    for tag in git(repo, 'tag', '--list', 'v*').splitlines():
        if not tag.startswith('v') or SEMVER.fullmatch(tag[1:]) is None:
            continue
        if subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', tag, head]).returncode == 0:
            candidates.append((tuple(int(part) for part in tag[1:].split('.')), tag))
    return max(candidates)[1] if candidates else None


def decision(repo, head, base=None):
    repo = Path(repo).resolve()
    head = git(repo, 'rev-parse', head + '^{commit}')
    tag = latest_tag(repo, head)
    if tag:
        base_commit, current = tag, tag[1:]
    else:
        base_commit = base or git(repo, 'rev-parse', head + '^')
        base_commit = git(repo, 'rev-parse', base_commit + '^{commit}')
        current = source_version(repo, head)
    level = next_level(messages(repo, base_commit, head))
    return {'base': base_commit, 'head': head, 'level': level,
            'version': None if level is None else bump(current, level)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--head', required=True)
    parser.add_argument('--base')
    args = parser.parse_args()
    print(json.dumps(decision(args.repo, args.head, args.base), sort_keys=True))


if __name__ == '__main__':
    main()
