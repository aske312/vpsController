"""Exercise the real release swap with synthetic services and Python installers."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

project = Path(__file__).resolve().parents[1]
source = (project / 'scripts/vps-control.sh').read_text()
functions = '\n'.join(re.search(r'^' + name + r'\(\) \{.*?^\}', source, re.M | re.S).group()
                      for name in ('prepare_release_python', 'install_prebuilt_release'))
functions = functions.replace('mktemp -d /opt/vps-control.release.XXXXXX', 'mktemp -d "${FIXTURE_ROOT}/release.XXXXXX"')
stubs = r'''
info() { :; }; ok() { :; }; warn() { :; }; die() { echo "$*" >&2; exit 9; }
release_metadata_value() { awk -F= -v key="$2" '$1==key {print $2}' "$1"; }
system_architecture() { echo amd64; }
ensure_product_identity() { return 0; }
validate_caddy_template() { return 0; }
docker() { return 1; }
systemctl() { echo "$*" >>"${FIXTURE_ROOT}/service-actions"; }
curl() { [[ "$FAIL_READINESS" == 0 || ! -f "${INSTALL_DIR}/new-release" ]]; }
install_api() {
  if [[ -f "${INSTALL_DIR}/new-release" ]]; then
    [[ "$(cat "${INSTALL_DIR}/venv/kind")" == "$EXPECTED_KIND" ]] || return 1
    [[ "$FAIL_APPLY" == 0 ]] || return 1
  fi
}
install_web() { return 0; }
ensure_api_write_access() { return 0; }
write_integrity_manifest() { return 0; }
restart_caddy_service() { return 0; }
restore_caddy_config() { return 0; }
cleanup_legacy_runtime() { return 0; }
run_with_status() { shift; "$@"; }
python3() {
  local destination="$3"
  mkdir -p "$destination/bin"
  printf '%s\n' new >"$destination/kind"
  cat >"$destination/bin/python" <<'PYTHON'
#!/bin/bash
if [[ "$1" == -m && "$2" == pip && "$FAIL_PREPARE" == 1 ]]; then exit 17; fi
if [[ "$1" == -c && "$FAIL_IMPORT" == 1 ]]; then exit 18; fi
exit 0
PYTHON
  chmod +x "$destination/bin/python"
}
'''

for scenario in ('unchanged', 'changed', 'prepare-fails', 'import-fails', 'activation-fails', 'readiness-fails'):
    with tempfile.TemporaryDirectory(prefix='312node-release-swap-') as directory:
        root = Path(directory)
        installed = root/'app'; old_venv = installed/'venv'
        old_venv.mkdir(parents=True)
        (old_venv/'kind').write_text('old\n')
        (installed/'original-release').write_text('old application')
        (installed/'api').mkdir()
        (installed/'api/requirements.txt').write_text('old-dependency\n')
        old_hash = hashlib.sha256(b'old-dependency\n').hexdigest()
        (old_venv/'.requirements.sha256').write_text(old_hash+'\n')
        backup = root/'data/test-app-backup'
        (backup/'api').mkdir(parents=True)
        (backup/'api/requirements.txt').write_text('old-dependency\n')
        payload = root/'archive/vps-control-release'
        files = {'api/main.py': 'pass\n', 'api/requirements.txt': 'old-dependency\n' if scenario=='unchanged' else 'new-dependency\n',
                 'dist/server/index.js': '', 'node_modules/.bin/vinext': '#!/bin/bash\n', 'Caddyfile': '', 'scripts/vps-control.sh': '',
                 'new-release': '', '.prebuilt-release': 'schema=1\nedition=light\nchannel=test\narchitecture=amd64\ncommit='+('a'*40)+'\n'}
        for name, content in files.items():
            file = payload/name; file.parent.mkdir(parents=True, exist_ok=True); file.write_text(content)
        (payload/'node_modules/.bin/vinext').chmod(0o755)
        manifest = ''.join(hashlib.sha256((payload/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in files)
        (payload/'release.sha256').write_text(manifest)
        archive = root/'release.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar: tar.add(payload, arcname='vps-control-release')
        env = dict(os.environ, FIXTURE_ROOT=str(root), INSTALL_DIR=str(installed), DATA_DIR=str(root/'data'),
                   TEST_BACKUP_DIR=str(backup), PRODUCT_EDITION='light', APP_NAME='fixture', HTTP_PORT='80', COMMAND_PATH=str(root/'control-command'),
                   EXPECTED_KIND='old' if scenario=='unchanged' else 'new',
                   FAIL_PREPARE='1' if scenario=='prepare-fails' else '0', FAIL_APPLY='1' if scenario=='activation-fails' else '0',
                   FAIL_IMPORT='1' if scenario=='import-fails' else '0', FAIL_READINESS='1' if scenario=='readiness-fails' else '0')
        command = stubs+'\n'+functions+'\ninstall_prebuilt_release install-release "$1" no test\n'
        result = subprocess.run(['bash', '-euc', command, 'fixture', str(archive)], env=env, capture_output=True, text=True)
        if scenario.endswith('-fails'):
            assert result.returncode != 0, (scenario, result.stdout, result.stderr)
            assert (installed/'original-release').exists()
            assert (installed/'venv/kind').read_text().strip() == 'old'
            if scenario in ('prepare-fails', 'import-fails'): assert not (root/'service-actions').exists(), 'stopped services before preparing Python'
        else:
            assert result.returncode == 0, (scenario, result.stdout, result.stderr)
            assert (installed/'new-release').exists()
            assert (installed/'venv/kind').read_text().strip() == env['EXPECTED_KIND']
            if scenario=='changed': assert (backup/'venv/kind').read_text().strip() == 'old'
        assert not list(root.glob('app.venv.*')), 'temporary Python environment leaked'
        print(scenario, 'release and Python environment state verified')
