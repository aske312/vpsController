"""Fail publication when installers cannot discover the tested release assets."""
import json
import re
import sys


def validate_release(release, tag, commit):
    if (not isinstance(release, dict) or release.get('draft') is not False
            or release.get('tag_name') != tag or release.get('target_commitish') != commit
            or not release.get('published_at')):
        raise ValueError('Release is not published at the tested commit')
    prefix = f'https://github.com/aske312/vpsController/releases/download/{tag}/'
    expected = {f'vps-control-pro-linux-{architecture}.tar.gz' for architecture in ('amd64', 'arm64')}
    assets = release.get('assets')
    if not isinstance(assets, list) or len(assets) != len(expected):
        raise ValueError('Release must contain both architecture archives')
    found = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError('Invalid published asset')
        name = asset.get('name')
        if (name not in expected or name in found or asset.get('state') != 'uploaded'
                or asset.get('browser_download_url') != prefix + name
                or type(asset.get('size')) is not int or asset['size'] <= 0
                or not isinstance(asset.get('digest'), str)
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', asset['digest'])):
            raise ValueError('Release asset is incomplete or unavailable at the published tag')
        found.add(name)


if __name__ == '__main__':
    try:
        validate_release(json.load(sys.stdin), *sys.argv[1:])
    except (ValueError, TypeError) as exc:
        raise SystemExit(str(exc)) from None
    print('Published release and both architecture archives verified')
