"""Converge a managed Mihomo core to the verified application bundle, without downgrade."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid


def version(binary):
    if not binary.is_file(): return None
    result = subprocess.run([str(binary), '-v'], capture_output=True, text=True, timeout=8, check=False)
    match = re.search(r'\bv?(\d+)\.(\d+)\.(\d+)\b', result.stdout) if result.returncode == 0 else None
    return tuple(map(int, match.groups())) if match else None


def sync(bundled, runtime):
    candidate_version, installed_version = version(bundled), version(runtime)
    if candidate_version is None: raise ValueError('Бинарник Mihomo в сборке не прошёл проверку версии')
    if runtime.exists() and installed_version is None: raise ValueError('Не удалось определить версию установленного Mihomo; замена не выполнялась')
    if installed_version is not None and installed_version >= candidate_version: return False
    runtime.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = runtime.with_name('.mihomo-' + uuid.uuid4().hex)
    try:
        shutil.copyfile(bundled, temporary)
        temporary.chmod(0o755)
        if version(temporary) != candidate_version: raise ValueError('Копия ядра Mihomo не прошла проверку')
        with temporary.open('r+b') as file: os.fsync(file.fileno())
        os.replace(temporary, runtime)
    finally: temporary.unlink(missing_ok=True)
    return True


if __name__ == '__main__':
    changed = sync(Path(sys.argv[1]), Path(sys.argv[2]))
    print('Mihomo: ядро обновлено из сборки приложения' if changed else 'Mihomo: установленное ядро актуально')
