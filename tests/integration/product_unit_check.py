"""Run only inside the disposable network namespace on authorized vps-1."""
import os
import json
from pathlib import Path
import subprocess
import time
import uuid
import relay_private_transport as private


def check(root, profile, binary):
    root = root/'unit-generation';root.mkdir()
    for name,field in [('origin.crt','origin_certificate'),('origin.key','ingress_private_key'),('ingress.crt','ingress_certificate'),('ingress.key','ingress_private_key')]: (root/name).write_text(profile[field]);(root/name).chmod(0o600)
    value = dict(profile, generation=uuid.uuid4().hex, origin_port=19444)
    unit = private.unit_name(value['generation'])
    path = Path('/etc/systemd/system')/unit
    assert not path.exists()
    original = private.command
    def command(args, **kw):
        if args == ['systemctl', 'daemon-reload'] and path.exists():
            text = path.read_text().replace('ExecStart='+str(binary), 'ExecStart=/run/relay-private-test-runtime').replace('StandardError=null','StandardError=journal')
            config=json.loads((root/'origin.json').read_text());config['log']={'level':'error'};(root/'origin.json').write_text(json.dumps(config))
            text = text.replace('[Service]\n', '[Service]\nNetworkNamespacePath=/proc/'+str(os.getpid())+'/ns/net\nBindReadOnlyPaths='+str(binary)+':/run/relay-private-test-runtime\n')
            path.write_text(text)
        original(args, **kw)
    private.command = command
    agent = None
    try:
        # Origin credential loader runs as root; binary is bind-mounted read-only.
        binary.chmod(0o755)
        private.install_origin(root, value, binary)
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            result = subprocess.run(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID'],capture_output=True,text=True,check=True).stdout
            if 'SubState=running' in result and 'MainPID=0' not in result:break
            time.sleep(.2)
        assert 'SubState=running' in result,result
        time.sleep(1)
        original(['systemctl','is-active','--quiet',unit])
        firewall = subprocess.check_output(['iptables','-S','INPUT'],text=True)
        assert ('vps-control-relay-private:'+value['generation']) in firewall
        os.link(binary,root/'sing-box')
        private.atomic_json(root/'request.json',value)
        agent=subprocess.Popen(['python3',str(root.parent/'relay_private_worker.py'),str(root/'request.json')],cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(.8)
        assert agent.poll() is None
        private.probe(root,value,binary,34000)
    except BaseException:
        print(subprocess.run(['systemctl','show',unit,'-p','Result','-p','ExecMainStatus'],capture_output=True,text=True).stdout,flush=True)
        print(subprocess.run(['journalctl','-u',unit,'--no-pager','-n','12'],capture_output=True,text=True).stdout,flush=True)
        raise
    finally:
        private.command = original
        if agent is not None:
            agent.terminate()
            try:agent.wait(timeout=6)
            except subprocess.TimeoutExpired:agent.kill();agent.wait()
        subprocess.run(['systemctl','disable','--now',unit],capture_output=True,check=True)
        path.unlink(missing_ok=True)
        original(['systemctl','daemon-reload'])
        firewall = subprocess.check_output(['iptables','-S','INPUT'],text=True)
        assert ('vps-control-relay-private:'+value['generation']) not in firewall
