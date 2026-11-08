"""Accepted device selections must survive a manager process restart."""
import json
import os
import subprocess
import sys
import unittest

import yaml
from tests.api.support import manager, ROOT


class SubscriptionRenderStabilityTests(unittest.TestCase):
    def test_accepted_channels_survive_restart_with_unchanged_process_routing(self):
        script = '''
import json
from tests.api.support import manager
catalog = {'alpha':['alpha.exe'], 'beta':['beta.exe'], 'gamma':['gamma.exe']}
rules = manager.process_rules(ROUTING, 'selected', 'custom', catalog, 'DIRECT', ('alpha','beta','gamma'))
print(json.dumps({'proxies':[{'name':'working'}, {'name':'rejected'}],
    'proxy-groups':[{'name':'vpn', 'proxies':['working','rejected']}],
    'rules':[*rules, 'MATCH,vpn']}))
'''
        for routing in ({'selected':'alpha,beta,gamma,alpha'}, {'selected':'@default'}):
            record = None
            for seed in ('1', '2', '3'):
                with self.subTest(routing=routing, seed=seed):
                    completed = subprocess.run([sys.executable, '-c', script.replace('ROUTING', repr(routing))],
                        cwd=ROOT, env={**os.environ, 'PYTHONHASHSEED':seed},
                        capture_output=True, text=True, check=True, timeout=30,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    config = json.loads(completed.stdout)
                    if record is None:
                        record = {'state':'accepted', 'source_digest':manager.client_channels.digest(config),
                                  'names':['working']}
                    restored = yaml.safe_load(manager.client_channels.render(yaml.safe_dump(config), record))
                    self.assertEqual(restored['proxies'], [{'name':'working'}])
                    self.assertEqual(restored['proxy-groups'][0]['proxies'], ['working'])
