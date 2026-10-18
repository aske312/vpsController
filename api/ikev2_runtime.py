"""Repair the EAP password dependency of explicitly managed IKEv2 instances."""
import os
from pathlib import Path
import subprocess

from component_registry import ComponentRegistry, RegistryError

PACKAGE = 'libcharon-extauth-plugins'
SERVICE_FILE = Path('/etc/systemd/system/vps-control-ikev2.service')


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
    if installed():
        return False
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
    # Loading a new plugin requires a restart. Preserve intentionally stopped services.
    subprocess.run(['systemctl','try-restart','vps-control-ikev2.service'],check=True,timeout=60)
    return True


if __name__ == '__main__':
    if repair():
        print('IKEv2: EAP-MSCHAPv2 dependency restored; existing keys and users preserved.',flush=True)
