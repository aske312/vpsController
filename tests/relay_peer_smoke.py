"""Real kernel WireGuard lease/cleanup check, entirely inside a network namespace."""
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'api'))


def inside():
    import relay_media
    import relay_test_peer
    import relay_test_cleanup
    from network_probe import ProbePlan
    run = subprocess.run
    def command(*args, input=None):
        return run(list(args), input=input, capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    command('ip','link','set','lo','up')
    command('ip','link','add','mh-wg0','type','wireguard')
    server_private=command('wg','genkey')
    command('wg','set','mh-wg0','private-key','/dev/stdin','listen-port','51831',input=server_private+'\n')
    server_public=command('wg','show','mh-wg0','public-key')
    original_private=command('wg','genkey'); original_public=command('wg','pubkey',input=original_private+'\n')
    command('ip','address','add','10.83.0.1/24','dev','mh-wg0')
    command('ip','link','set','mh-wg0','up')
    command('wg','set','mh-wg0','peer',original_public,'allowed-ips','10.83.0.2/32')
    before=command('wg','show','mh-wg0','allowed-ips')
    with tempfile.TemporaryDirectory(prefix='relay-wg-smoke-') as folder:
        root=Path(folder); (root/'mihomo'/'settings').mkdir(parents=True)
        profile=[{'id':'profile','connections':[{'id':'connection','component':'transport-wg','credential':{'ip':'10.83.0.2/24'}}]}]
        profiles=root/'mihomo'/'profiles.json';profiles.write_text(json.dumps(profile)); saved=profiles.read_bytes()
        (root/'mihomo'/'settings'/'transport-wg.json').write_text('{"port":51831}')
        api=SimpleNamespace(DATA_DIR=root)
        plan=ProbePlan('route',1,'mihomo:profile:connection','1.1.1.1',20001,'',51831,'udp','fp',
            {'type':'wireguard','server':'1.1.1.1','port':20001,'ip':'10.83.0.2','private-key':original_private,'public-key':server_public},engine='mihomo')
        scheduled=[]
        native_expiry=False
        def checked_run(args, **kwargs):
            if args[0]=='systemd-run':
                scheduled.append(args)
                if native_expiry:
                    args=[item if not item.startswith('--on-active=') else '--on-active=4s' for item in args]
                    args.insert(1,'--property=NetworkNamespacePath=/proc/'+str(os.getpid())+'/ns/net')
                    return run(args,**kwargs)
                return SimpleNamespace(stdout='',returncode=0)
            if args[:3]==['wg','set','mh-wg0'] and args[-2:] == ['allowed-ips','10.83.0.254/32']:
                assert scheduled, 'Expiry must be scheduled before mutation'
            return run(args,**kwargs)
        route={'id':'route'};host={'id':'host'}
        with patch.object(relay_media,'mihomo_plan',return_value=plan), patch.object(relay_test_peer.subprocess,'run',side_effect=checked_run):
            for index in range(2):
                directory=root/'relay-tests'/format(index+1,'032x')
                try:
                    with relay_test_peer.disposable_plan(api,route,host,plan.consumer_id,directory) as fresh:
                        assert fresh.client['private-key']!=original_private
                        assert fresh.client['ip']=='10.83.0.254'
                        peers=command('wg','show','mh-wg0','allowed-ips')
                        assert original_public+'\t10.83.0.2/32' in peers
                        assert len(peers.splitlines())==2
                        if index: raise RuntimeError('Synthetic cancelled/failed media test')
                except RuntimeError as exc:
                    assert str(exc)=='Synthetic cancelled/failed media test'
                assert command('wg','show','mh-wg0','allowed-ips')==before
                assert profiles.read_bytes()==saved
                assert json.loads((directory/'cleanup.json').read_text())['peer_removed']
            native_expiry=True
            directory=root/'relay-tests'/format(3,'032x')
            with relay_test_peer.disposable_plan(api,route,host,plan.consumer_id,directory):
                assert len(command('wg','show','mh-wg0','allowed-ips').splitlines())==2
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if command('wg','show','mh-wg0','allowed-ips')==before: break
                    time.sleep(.2)
                else: raise AssertionError('Independent systemd expiry did not remove temporary peer')
            assert profiles.read_bytes()==saved
    print(json.dumps({'kernel_wireguard':True,'isolated_network_namespace':True,'original_peer_preserved':True,
                      'profiles_unchanged':True,'expiry_before_mutation':True,'cleanup_on_success_and_failure':True,'independent_systemd_expiry':True}))


if __name__=='__main__':
    if os.geteuid()!=0: raise SystemExit('Run as root; test creates an isolated network namespace')
    if '--inside' in sys.argv: inside()
    else:
        result=subprocess.run(['unshare','--net','--',sys.executable,str(Path(__file__).resolve()),'--inside'],capture_output=True,text=True,timeout=60)
        if result.returncode:
            # Commands never put private keys in argv. Only bounded failure details are exposed.
            sys.stderr.write(result.stderr[-5000:]);raise SystemExit(result.returncode)
        print(result.stdout.strip())
