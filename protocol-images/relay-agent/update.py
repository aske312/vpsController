#!/usr/bin/env python3
"""Daily installation of CI-published Agent releases; never execute branch HEAD."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import time
import stat
import urllib.request
from datetime import datetime, timezone

ROOT = Path('/etc/vps-control-relay-agent')
STATE = Path('/var/lib/vps-control-relay-agent')
RELEASE = 'https://api.github.com/repos/aske312/vpsController/releases/tags/agent-latest'
ASSET_PREFIX = 'https://github.com/aske312/vpsController/releases/download/agent-latest/'
FILES = {'agent.py', 'addresses.py', 'credentials.py', 'manifest.json', 'install.sh', 'uninstall.sh', 'update.py'}


def fetch(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'vps-control-relay-updater', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Release size limit')
    return data


def release_asset(release):
    if release.get('draft') or release.get('tag_name') != 'agent-latest':
        raise ValueError('Invalid release')
    assets = [item for item in release.get('assets', []) if item.get('name') == 'relay-agent.tar.gz']
    if len(assets) != 1:
        raise ValueError('Missing release asset')
    asset = assets[0]
    if asset.get('browser_download_url') != ASSET_PREFIX + 'relay-agent.tar.gz' or not re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest', '')):
        raise ValueError('Invalid release digest or source')
    return asset


def unpack(archive, destination):
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        if len(members) != len(FILES) or {item.name for item in members} != FILES:
            raise ValueError('Unexpected release files')
        for member in members:
            if not member.isfile() or member.size > 1024 * 1024:
                raise ValueError('Unsafe release member')
            (destination / member.name).write_bytes(source.extractfile(member).read())
    manifest = json.loads((destination / 'manifest.json').read_text())
    if manifest.get('id') != 'relay-agent' or manifest.get('source_branch') != 'agent' or not re.fullmatch(r'\d+\.\d+\.\d+', manifest.get('version', '')):
        raise ValueError('Invalid Agent manifest')
    subprocess.run(['python3', '-m', 'py_compile', *[str(destination / name) for name in FILES if name.endswith('.py')]], check=True, capture_output=True)
    subprocess.run(['bash', '-n', str(destination / 'install.sh')], check=True, capture_output=True)


def write_metadata(name, value):
    fd, temporary = tempfile.mkstemp(prefix='.update-', dir=ROOT)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, 0, (ROOT / 'config.json').stat().st_gid)
        os.chmod(temporary, 0o640)
        os.replace(temporary, ROOT / name)
    finally:
        Path(temporary).unlink(missing_ok=True)


def publish_status(value):
    write_metadata('update-status.json', {**value, 'checked_at': datetime.now(timezone.utc).isoformat()})


def read_request():
    path = STATE/'update-request.json'
    try: fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError: return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2048: raise ValueError('Invalid request file')
        value = json.loads(stream.read(2049))
    if not isinstance(value, dict) or set(value) != {'id', 'version', 'digest', 'requested_at'}:
        raise ValueError('Invalid request')
    for key, pattern in [('id', r'[0-9a-f]{32}'), ('version', r'\d{1,6}\.\d{1,6}\.\d{1,6}'), ('digest', r'sha256:[0-9a-f]{64}')]:
        if not isinstance(value[key], str) or not re.fullmatch(pattern, value[key]): raise ValueError('Invalid request metadata')
    if type(value['requested_at']) not in (float, int) or not 0 <= time.time()-value['requested_at'] <= 900:
        raise ValueError('Expired request')
    return value


def manual_status(request, state, reason=''):
    write_metadata('manual-update-status.json', {**request, 'state': state, 'reason': reason,
                   'checked_at': datetime.now(timezone.utc).isoformat()})


def main():
    import fcntl
    if os.geteuid() != 0:
        raise SystemExit('Updater requires root')
    with open('/run/lock/vps-control-relay-update.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                request = read_request()
                if request: manual_status(request, 'failed', 'update_busy')
            finally: (STATE/'update-request.json').unlink(missing_ok=True)
            return
        request = None
        request_seen = os.path.lexists(STATE/'update-request.json')
        try:
            request = read_request()
            if request:
                manual_status(request, 'running')
                version = json.loads(Path(__file__).with_name('manifest.json').read_text())['version']
                if tuple(map(int, request['version'].split('.'))) <= tuple(map(int, version.split('.'))):
                    raise ValueError('Agent is no longer outdated')
            settings = json.loads((ROOT / 'updates.json').read_text())
            if not request and settings.get('enabled') is not True:
                publish_status({'state': 'disabled'})
                return
            asset = release_asset(json.loads(fetch(RELEASE, 1024 * 1024)))
            if request and request['digest'] != asset['digest']: raise ValueError('Published release changed')
            installed = ROOT / 'installed-release.json'
            previous = json.loads(installed.read_text()) if installed.exists() else {}
            if previous.get('digest') == asset['digest']:
                if request: raise ValueError('Installed package version is inconsistent')
                publish_status({'state': 'current', 'version': previous.get('version', '')})
                return
            data = fetch(asset['browser_download_url'], 5 * 1024 * 1024)
            if 'sha256:' + hashlib.sha256(data).hexdigest() != asset['digest']:
                raise ValueError('Release checksum mismatch')
            with tempfile.TemporaryDirectory(prefix='vps-relay-update-') as folder:
                directory = Path(folder)
                archive = directory / 'release.tar.gz'
                archive.write_bytes(data)
                stage = directory / 'source'
                stage.mkdir()
                unpack(archive, stage)
                version = json.loads((stage / 'manifest.json').read_text())['version']
                if request and request['version'] != version: raise ValueError('Published version changed')
                # Existing installer preserves identity/routes and rolls back runtime files on failed health check.
                subprocess.run(['bash', str(stage / 'install.sh')], check=True, timeout=240,
                               env={**os.environ, 'RELAY_UPDATE_LOCKED': '1'}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                write_metadata('installed-release.json', {'digest': asset['digest'], 'version': version})
                publish_status({'state': 'updated', 'version': version})
                if request: manual_status(request, 'updated')
        except Exception:
            # No remote response, credential, URL query or subprocess log is reflected.
            publish_status({'state': 'failed'})
            if request: manual_status(request, 'failed', 'update_failed')
            raise SystemExit('Relay update failed; existing installation retained or restored')
        finally:
            # Consume once. A lost API response must never replay an installation.
            if request_seen or request is not None: (STATE/'update-request.json').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
