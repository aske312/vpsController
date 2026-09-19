"""Pinned application dependencies, staged and checked before a release swap."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import urllib.request

import legacy_dns

ROOT = Path(__file__).resolve().parent
CADDY_CONFIG = Path('/etc/caddy/Caddyfile')


def specification() -> dict:
    return json.loads((ROOT / 'runtime-dependencies.json').read_text())['caddy']


def version(text: str) -> tuple[int, int, int]:
    match = re.match(r'v?(\d+)\.(\d+)\.(\d+)', text.strip())
    if not match:
        raise ValueError('Не удалось определить версию Caddy')
    return tuple(map(int, match.groups()))


def caddy_is_current() -> bool:
    try:
        result = subprocess.run(['caddy', 'version'], capture_output=True, text=True, check=True, timeout=5)
        return version(result.stdout) >= version(specification()['version'])
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def package(directory: Path) -> Path:
    spec = specification()
    arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine().lower())
    if arch not in spec['assets']:
        raise ValueError('Архитектура сервера не поддерживается пакетом Caddy')
    name = f"caddy_{spec['version']}_linux_{arch}.deb"
    bundled = ROOT / 'packages' / name
    path = bundled if bundled.is_file() else directory / name
    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix('.download')
        try:
            with urllib.request.urlopen(f"https://github.com/caddyserver/caddy/releases/download/v{spec['version']}/{name}", timeout=60) as response, partial.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    checksum = digest.hexdigest()
    if checksum != spec['assets'][arch]:
        raise ValueError('Контрольная сумма пакета Caddy не совпала. Работающее приложение сохранено.')
    return path


@contextmanager
def candidate_caddy():
    """The target release can be validated by an older installed updater."""
    if caddy_is_current():
        yield 'caddy'
        return
    with tempfile.TemporaryDirectory(prefix='vps-caddy-check-') as temp:
        root = Path(temp)
        archive = package(root)
        subprocess.run(['dpkg-deb', '-x', str(archive), str(root / 'payload')], check=True, capture_output=True, timeout=30)
        yield str(root / 'payload/usr/bin/caddy')


def install() -> None:
    # Old installed updaters invoke this entrypoint from the new payload before
    # swapping releases. Run migrations even when Caddy is already current.
    if legacy_dns.migrate():
        print("Legacy DNS: boot ordering fixed; running services were not restarted.", flush=True)
    if caddy_is_current():
        return
    with tempfile.TemporaryDirectory(prefix='vps-caddy-install-') as temp:
        root = Path(temp)
        archive = package(root)
        subprocess.run(['dpkg-deb', '-x', str(archive), str(root / 'payload')], check=True, capture_output=True, timeout=30)
        binary = str(root / 'payload/usr/bin/caddy')
        detected = subprocess.run([binary, 'version'], check=True, capture_output=True, text=True, timeout=5)
        if version(detected.stdout) < version(specification()['version']):
            raise ValueError('В пакете обнаружена неподходящая версия Caddy')
        config = CADDY_CONFIG
        if config.exists():
            subprocess.run([binary, 'adapt', '--config', str(config), '--adapter', 'caddyfile'], check=True, capture_output=True, timeout=30)
        print(f"Обновление зависимости приложения: Caddy {specification()['version']}", flush=True)
        active = subprocess.run(['systemctl', 'is-active', '--quiet', 'caddy.service'], check=False).returncode == 0
        # Keep local routes, prohibit removals, and upgrade only the pinned package.
        subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=300', '-o', 'Dpkg::Options::=--force-confold',
                        'install', '-y', '--no-remove', str(archive)], env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'}, check=True, timeout=600)
        if not caddy_is_current():
            raise ValueError('После установки версия Caddy не соответствует требованиям релиза')
        if active:
            subprocess.run(['systemctl', 'restart', 'caddy.service'], check=True, timeout=30)
            subprocess.run(['systemctl', 'is-active', '--quiet', 'caddy.service'], check=True, timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['download', 'install'])
    parser.add_argument('--directory', type=Path, default=ROOT / 'packages')
    args = parser.parse_args()
    if args.action == 'download':
        print(package(args.directory))
    else:
        install()
