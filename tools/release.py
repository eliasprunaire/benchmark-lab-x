"""Calculer la prochaine version produit depuis les commits Conventional Commits."""
import argparse
import json
import re
import subprocess
from pathlib import Path


SEMVER_IDENTIFIER = r'(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)'
SEMVER = re.compile(
    rf'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
    rf'(?:-{SEMVER_IDENTIFIER}(?:\.{SEMVER_IDENTIFIER})*)?'
    rf'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$')
PRE_RELEASE = re.compile(rf'^{SEMVER_IDENTIFIER}(?:\.{SEMVER_IDENTIFIER})*$')
COMMIT = re.compile(r'^(?P<kind>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<breaking>!)?:\s')


def git(repo, *arguments):
    return subprocess.check_output(['git', '-C', str(repo), *arguments], text=True).strip()


def classify(subject, body=''):
    match = COMMIT.match(subject)
    if match is None:
        return None
    if match['scope'] == 'ci':
        return None
    if match['breaking'] or re.search(r'^BREAKING CHANGE:', body, re.MULTILINE):
        return 'breaking'
    return {'feat': 'minor', 'fix': 'patch'}.get(match['kind'])


def bump(version, level):
    core = version.split('+', 1)[0].split('-', 1)[0]
    major, minor, patch = (int(part) for part in core.split('.'))
    if level == 'breaking':
        return f'{major + 1}.0.0' if major else f'0.{minor + 1}.0'
    if level == 'minor':
        return f'{major}.{minor + 1}.0'
    if level == 'patch':
        return f'{major}.{minor}.{patch + 1}'
    raise ValueError('Niveau SemVer inconnu')


def with_pre_release(version, suffix):
    if not suffix:
        return version
    if PRE_RELEASE.fullmatch(suffix) is None:
        raise ValueError('Suffixe de préversion invalide')
    return f'{version}-{suffix}'


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
            core = tag[1:].split('+', 1)[0].split('-', 1)[0]
            candidates.append((tuple(int(part) for part in core.split('.')), tag))
    return max(candidates)[1] if candidates else None


def tag_at(repo, head):
    tags = []
    for tag in git(repo, 'tag', '--points-at', head).splitlines():
        if tag.startswith('v') and SEMVER.fullmatch(tag[1:]):
            tags.append(tag)
    if len(tags) > 1:
        raise ValueError('Plusieurs tags SemVer sur le même commit')
    return tags[0] if tags else None


def decision(repo, head, base=None, pre_release=None, bootstrap_pre_release=False):
    repo = Path(repo).resolve()
    if bootstrap_pre_release and not pre_release:
        raise ValueError('L’amorçage de préversion exige un suffixe explicite')
    head = git(repo, 'rev-parse', head + '^{commit}')
    exact_tag = tag_at(repo, head)
    if exact_tag:
        return {'base': exact_tag, 'head': head, 'level': 'existing', 'version': exact_tag[1:]}
    tag = latest_tag(repo, head)
    if tag:
        base_commit, current = tag, tag[1:]
    else:
        base_commit = base or git(repo, 'rev-parse', head + '^')
        base_commit = git(repo, 'rev-parse', base_commit + '^{commit}')
        current = source_version(repo, head)
    level = next_level(messages(repo, base_commit, head))
    if level is None and bootstrap_pre_release:
        level = 'minor'
    version = None if level is None else with_pre_release(bump(current, level), pre_release)
    return {'base': base_commit, 'head': head, 'level': level, 'version': version}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--head', required=True)
    parser.add_argument('--base')
    parser.add_argument('--pre-release', default='')
    parser.add_argument('--bootstrap-pre-release', action='store_true')
    args = parser.parse_args()
    print(json.dumps(decision(
        args.repo, args.head, args.base, args.pre_release,
        args.bootstrap_pre_release), sort_keys=True))


if __name__ == '__main__':
    main()
