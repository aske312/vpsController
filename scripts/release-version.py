"""Use the same prospective version for a tested tree and its production merge."""
import json
import os
from pathlib import Path
import re
import subprocess


def release_version(root: Path, revision: str = 'HEAD') -> str:
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, text=True).strip()

    base = json.loads((root / 'package.json').read_text(encoding='utf-8'))['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+', base):
        raise ValueError('Invalid application version')
    major_minor = base.rsplit('.', 1)[0]
    tags = git('tag', '--list', f'pro-v{major_minor}.*', '--sort=-v:refname').splitlines()
    if not tags:
        return 'v' + base
    latest = tags[0]
    if not re.fullmatch(r'pro-v\d+\.\d+\.\d+', latest):
        raise ValueError('Invalid production version tag')
    # A protected PR may add a merge commit without changing the tested files.
    if git('rev-parse', '--verify', latest + '^{tree}') == git('rev-parse', '--verify', revision + '^{tree}'):
        return latest.removeprefix('pro-')
    return f'v{major_minor}.{int(latest.rsplit(".", 1)[1]) + 1}'


if __name__ == '__main__':
    print(release_version(Path(__file__).resolve().parents[1], os.getenv('GITHUB_SHA') or 'HEAD'))
