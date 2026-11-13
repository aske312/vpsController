import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from tests.api.support import api
import assistant_control
import developer_inspection as developer


class DeveloperTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (self.root / 'api').mkdir()
        (self.root / 'api/sample.py').write_text(f"from pathlib import Path\nPath({str(self.root / 'should-not-execute')!r}).touch()\nsecret = 'fixture-secret'\ndef example(value):\n    return value + 1\n", encoding='utf-8')
        (self.root / 'package.json').write_text(json.dumps({'dependencies': {'react': '^19.0.0'}}))
        self.settings = {'accounts': [], 'providers': [{'api_key': 'fixture-secret'}]}
        self.enterContext(patch.object(assistant_control, 'read', return_value=self.settings))
        self.env = types.SimpleNamespace(INSTALL_DIR=self.root, ADMIN_PASSWORD='', installed_build_commit=lambda: 'a' * 40,
            application_dependency_versions=lambda: [{'id':'node','name':'Node','version':'22.1.0'}], installed_node_package_version=lambda name: '19.2.0')

    def call(self, **body):
        return developer.call(self.env, {'body':body}, 'panel')

    def test_read_search_and_symbols_are_installed_source_not_execution(self):
        read = self.call(command='read', path='api/sample.py', line=3, lines=3)
        self.assertEqual(read['line'],3)
        self.assertEqual(len(read['provenance']['sha256']),64)
        self.assertEqual(read['provenance']['commit'],'a'*40)
        self.assertNotIn('fixture-secret', json.dumps(read))
        self.assertIn('def example',read['text'])
        search=self.call(command='search',query='return value')
        self.assertEqual(search['matches'][0]['path'],'api/sample.py')
        self.assertEqual(search['matches'][0]['line'],5)
        symbols=self.call(command='symbols',path='api/sample.py')
        self.assertEqual(symbols['symbols'][0]['name'],'example')
        self.assertFalse((self.root / 'should-not-execute').exists())
        self.assertTrue(read['coverage']['read_only'])

    def test_paths_environment_runtime_and_dynamic_config_are_closed(self):
        for name in ('.env','../outside.py','/etc/shadow','api/../package.json','api\\sample.py','api/settings.json','api/.private.py','protocol-images/ai-agent/venv/a.py','node_modules/module/index.js'):
            with self.subTest(path=name), self.assertRaises(HTTPException): self.call(command='read',path=name)
        self.assertEqual(self.call()['files'],['api/sample.py','package.json'])

    def test_symlink_cannot_escape_source_even_with_a_source_extension(self):
        outside=self.root.parent / (self.root.name + '-outside.py')
        outside.write_text('not application source')
        self.addCleanup(lambda:outside.unlink(missing_ok=True))
        link=self.root/'api/link.py'
        try: link.symlink_to(outside)
        except OSError: self.skipTest('Symlink creation unavailable')
        with self.assertRaises(HTTPException): self.call(command='read',path='api/link.py')
        self.assertNotIn('api/link.py', self.call()['files'])

    def test_hardlink_cannot_expose_data_as_source(self):
        private=self.root/'private.txt'
        private.write_text('private fixture')
        try: os.link(private,self.root/'api/link.py')
        except OSError: self.skipTest('Hardlink creation unavailable')
        with self.assertRaises(HTTPException):self.call(command='read',path='api/link.py')
        self.assertNotIn('api/link.py',self.call()['files'])

    def test_limits_syntax_errors_and_dependency_comparison(self):
        (self.root/'api/broken.py').write_text('def broken(:')
        self.assertEqual(self.call(command='symbols',path='api/broken.py')['syntax'],'error')
        data=self.call(command='dependencies')
        self.assertEqual(data['node_dependencies']['react'],{'requested':'^19.0.0','installed':'19.2.0'})
        self.assertEqual(data['observed'][0]['version'],'22.1.0')
        calls=[]
        self.env.run=lambda *args,**kwargs: calls.append((args,kwargs)) or 'openssl\t3.0.0\n'
        self.assertEqual(self.call(command='dependencies')['os_packages'],{'openssl':'3.0.0'})
        self.assertEqual(calls[0][0][:2],('dpkg-query','-W'))
        self.assertEqual(calls[0][1],{'timeout':4})
        for body in ({'lines':10000},{'command':'shell'},{'command':'search','query':''},{'body':'extra'}):
            with self.subTest(body=body), self.assertRaises(HTTPException): self.call(**body)

    def test_missing_terminal_permission_and_mutation_methods_are_denied(self):
        self.settings['accounts']=[{'telegram_id':11,'role':'moderator','products':['assistant']}]
        self.settings['role_permissions']={'moderator':['diagnostics','server_read']}
        with self.assertRaises(HTTPException) as denied: developer.call(self.env,{},'11')
        self.assertEqual(denied.exception.status_code,403)
        with self.assertRaises(HTTPException): developer.call(self.env,{'method':'POST'},'panel')
