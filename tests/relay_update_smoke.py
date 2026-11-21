"""Exercise the real systemd.path -> root updater bridge on a CI-only fixture."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode: print(result.stderr[-2500:])
    result.check_returncode()
    return result


def main():
    if os.geteuid() != 0: raise SystemExit('Linux root CI fixture required')
    source = Path(__file__).resolve().parents[1]/'protocol-images/relay-agent'
    unit = 'vps-relay-update-test-'+uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='relay-update-smoke-') as folder:
        root = Path(folder); root.chmod(0o755)
        runtime = root/'runtime'; runtime.mkdir(mode=0o755)
        for name in ('agent.py','addresses.py'): shutil.copy(source/name, runtime/name)
        config = root/'config'; config.mkdir(mode=0o755)
        state = root/'state'; state.mkdir(mode=0o700)
        user = pwd.getpwnam('nobody'); os.chown(state, user.pw_uid, user.pw_gid)
        (config/'config.json').write_text(json.dumps({'token_sha256':hashlib.sha256(b'fixture-token').hexdigest(),'own_ips':['8.8.8.8']}))
        os.chown(config/'config.json',0,user.pw_gid)
        (config/'updates.json').write_text('{"enabled":false}')
        library = root/'library'; library.mkdir()
        shutil.copy(source/'update.py', library/'update.py')
        shutil.copy(source/'manifest.json', library/'manifest.json')
        stage = root/'stage'; stage.mkdir()
        for name in ('agent.py','addresses.py','credentials.py','manifest.json','install.sh','uninstall.sh','update.py'):
            shutil.copy(source/name,stage/name)
        (stage/'manifest.json').write_text(json.dumps({'id':'relay-agent','source_branch':'agent','version':'9.0.0'}))
        # The real installer is covered by the separate isolated installer smoke.
        # This fixture must never install to the CI host.
        marker = root/'installed'
        (stage/'install.sh').write_text('#!/bin/bash\nset -eu\necho installed >> '+str(marker)+'\n')
        archive = root/'release.tar.gz'
        with tarfile.open(archive,'w:gz') as stream:
            for item in stage.iterdir(): stream.add(item,arcname=item.name)
        digest = 'sha256:'+hashlib.sha256(archive.read_bytes()).hexdigest()
        release = {'tag_name':'agent-latest','assets':[{'name':'relay-agent.tar.gz','browser_download_url':'https://github.com/aske312/vpsController/releases/download/agent-latest/relay-agent.tar.gz','digest':digest}]}
        (root/'release.json').write_text(json.dumps(release))
        wrapper = root/'worker.py'
        wrapper.write_text('import sys,json\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(library))+')\nimport update\nupdate.ROOT=Path('+repr(str(config))+')\nupdate.STATE=Path('+repr(str(state))+')\nupdate.fetch=lambda url,limit: Path('+repr(str(root))+',"release.json" if url==update.RELEASE else "release.tar.gz").read_bytes()\nupdate.main()\n')
        submit = root/'submit.py'
        submit.write_text('import sys,asyncio\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(runtime))+')\nfrom agent import Agent\nagent=Agent(Path('+repr(str(config))+'),Path('+repr(str(state))+'),"127.0.0.1")\nasyncio.run(agent.dispatch("PUT","/v1/updates/'+('a'*32)+'",{"authorization":"Bearer fixture-token"},'+repr({'version':'9.0.0','digest':digest})+'))\n')
        service = Path('/run/systemd/system')/(unit+'.service')
        watcher = service.with_suffix('.path')
        try:
            service.write_text('[Service]\nType=oneshot\nExecStart=/usr/bin/python3 '+str(wrapper)+'\n')
            watcher.write_text('[Path]\nPathExists='+str(state/'update-request.json')+'\nUnit='+unit+'.service\n')
            run('systemctl','daemon-reload'); run('systemctl','start',unit+'.path')
            run('runuser','-u','nobody','--','python3',str(submit))
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                status = config/'manual-update-status.json'
                if status.exists() and json.loads(status.read_text()).get('state') in ('updated','failed'): break
                time.sleep(0.1)
            assert json.loads(status.read_text())['state']=='updated'
            assert marker.read_text().splitlines()==['installed']
            assert not (state/'update-request.json').exists()
            # An acknowledged request cannot install again, including after the API restarts.
            run('runuser','-u','nobody','--','python3',str(submit))
            assert not (state/'update-request.json').exists()
            assert marker.read_text().splitlines()==['installed']
            assert json.loads((config/'updates.json').read_text())=={'enabled':False}
            print('Authenticated unprivileged request -> real systemd.path -> root updater: OK; replay: no install')
        finally:
            subprocess.run(['systemctl','stop',unit+'.path',unit+'.service'],capture_output=True)
            service.unlink(missing_ok=True); watcher.unlink(missing_ok=True)
            subprocess.run(['systemctl','daemon-reload'],capture_output=True)


if __name__=='__main__': main()
