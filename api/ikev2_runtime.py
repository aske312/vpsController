"""Repair the EAP password dependency of explicitly managed IKEv2 instances."""
import os
from pathlib import Path
import re
import subprocess
import tempfile

from component_registry import ComponentRegistry, RegistryError

PACKAGE = 'libcharon-extauth-plugins'
SERVICE_FILE = Path('/etc/systemd/system/vps-control-ikev2.service')
CONFIG_FILE = Path('/etc/swanctl/swanctl.conf')


def repair_auth_method():
    """Change only our legacy connection's remote auth, preserving other settings."""
    if not CONFIG_FILE.is_file():
        return False
    original = CONFIG_FILE.read_text()
    stack = []
    lines = []
    for line in original.splitlines(keepends=True):
        stripped = line.split('#', 1)[0].strip()
        if stack == ['connections', 'ikev2-eap', 'remote']:
            line = re.sub(r'^(\s*auth\s*=\s*)eap-dynamic(?=\s*(?:#.*)?$)',
                          r'\1eap-mschapv2', line)
        opened = re.fullmatch(r'([\w-]+)\s*\{', stripped)
        if opened:
            stack.append(opened[1])
        elif stripped == '}' and stack:
            stack.pop()
        lines.append(line)
    updated = ''.join(lines)
    if updated == original:
        return False
    metadata = CONFIG_FILE.stat()
    descriptor, temporary = tempfile.mkstemp(prefix='.eap-repair-', dir=CONFIG_FILE.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            os.chmod(temporary, metadata.st_mode & 0o777)
            if hasattr(os, 'chown'):
                os.chown(temporary, metadata.st_uid, metadata.st_gid)
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, CONFIG_FILE)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def installed():
    result = subprocess.run(['dpkg-query','-W','-f=${db:Status-Status}',PACKAGE],
        capture_output=True,text=True,check=False,timeout=10)
    return result.returncode == 0 and result.stdout.strip() == 'installed'


def repair():
    if not SERVICE_FILE.is_file():
        return False
    try:
        ComponentRegistry(Path(os.environ.get('DATA_DIR','/var/lib/vps-control'))).require_managed('ikev2')
    except RegistryError:
        return False
    package_missing = not installed()
    if not package_missing:
        changed = repair_auth_method()
        if changed:
            subprocess.run(['systemctl','try-restart','vps-control-ikev2.service'],check=True,timeout=60)
        return changed
    command = ['apt-get','-o','DPkg::Lock::Timeout=300','-o','Dpkg::Options::=--force-confold']
    environment = {**os.environ,'DEBIAN_FRONTEND':'noninteractive'}
    arguments = ['install','-y','--no-remove','--no-install-recommends',PACKAGE]
    try:
        subprocess.run(command+arguments,env=environment,check=True,timeout=600)
    except subprocess.CalledProcessError:
        subprocess.run(command+['update'],env=environment,check=True,timeout=600)
        subprocess.run(command+arguments,env=environment,check=True,timeout=600)
    if not installed():
        raise RuntimeError('Не удалось установить EAP-MSCHAPv2 для IKEv2')
    repair_auth_method()
    # Loading a new plugin requires a restart. Preserve intentionally stopped services.
    subprocess.run(['systemctl','try-restart','vps-control-ikev2.service'],check=True,timeout=60)
    return True


if __name__ == '__main__':
    if repair():
        print('IKEv2: EAP-MSCHAPv2 dependency restored; existing keys and users preserved.',flush=True)
