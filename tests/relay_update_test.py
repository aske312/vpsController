import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
import sys
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'protocol-images/relay-agent'
spec = importlib.util.spec_from_file_location('relay_update', SOURCE / 'update.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class UpdateTests(unittest.TestCase):
    def test_manual_update_uses_verified_package_even_when_daily_updates_are_disabled(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            root = Path(folder); (root/'updates.json').write_text('{"enabled":false}')
            stage_source = root/'source'; stage_source.mkdir()
            for name in update.FILES:
                data = (SOURCE/name).read_bytes()
                if name == 'manifest.json': data = json.dumps({'id':'relay-agent','source_branch':'agent','version':'9.0.0'}).encode()
                (stage_source/name).write_bytes(data)
            archive = root/'archive.tar.gz'
            with tarfile.open(archive, 'w:gz') as stream:
                for name in update.FILES: stream.add(stage_source/name, arcname=name)
            data = archive.read_bytes(); digest = 'sha256:'+hashlib.sha256(data).hexdigest()
            request = {'id':'a'*32,'version':'9.0.0','digest':digest,'requested_at':__import__('time').time()}
            (root/'update-request.json').write_text(json.dumps(request))
            release = {'tag_name':'agent-latest','assets':[{'name':'relay-agent.tar.gz','browser_download_url':update.ASSET_PREFIX+'relay-agent.tar.gz','digest':digest}]}
            for attribute, value in [('ROOT',root),('STATE',root)]: stack.enter_context(patch.object(update,attribute,value))
            stack.enter_context(patch.object(update.os,'geteuid',return_value=0,create=True))
            stack.enter_context(patch.object(update,'open',side_effect=lambda path,*args,**kwargs: open(root/'lock' if str(path)=='/run/lock/vps-control-relay-update.lock' else path,*args,**kwargs),create=True))
            stack.enter_context(patch.dict(sys.modules,{'fcntl':SimpleNamespace(flock=lambda *_:None,LOCK_EX=2,LOCK_NB=4)}))
            stack.enter_context(patch.object(update,'fetch',side_effect=[json.dumps(release).encode(),data]))
            metadata = stack.enter_context(patch.object(update,'write_metadata'))
            run = stack.enter_context(patch.object(update.subprocess,'run'))
            update.main()
            self.assertFalse((root/'update-request.json').exists())
            self.assertEqual([call.args[1]['state'] for call in metadata.call_args_list if call.args[0]=='manual-update-status.json'], ['running','updated'])
            self.assertEqual(sum(call.args[0][0]=='bash' and call.args[0][1].endswith('install.sh') and '-n' not in call.args[0] for call in run.call_args_list),1)

    def test_manual_request_rejects_expired_and_arbitrary_commands(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(update,'STATE',Path(folder)):
            path = Path(folder)/'update-request.json'
            for value in ({'command':'reboot'}, {'id':'a'*32,'version':'9.0.0','digest':'sha256:'+'b'*64,'requested_at':0}):
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError): update.read_request()

    def test_root_worker_does_not_replay_a_request_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); request = {'id':'a'*32,'version':'9.0.0','digest':'sha256:'+'b'*64,'requested_at':__import__('time').time()}
            (root/'update-request.json').write_text(json.dumps(request))
            (root/'manual-update-status.json').write_text(json.dumps({**request,'state':'running'}))
            with patch.object(update,'ROOT',root),patch.object(update,'STATE',root),patch.object(update.os,'geteuid',return_value=0,create=True),patch.object(update,'open',side_effect=lambda path,*args,**kwargs: open(root/'lock',*args,**kwargs),create=True),patch.dict(sys.modules,{'fcntl':SimpleNamespace(flock=lambda *_:None,LOCK_EX=2,LOCK_NB=4)}),patch.object(update,'fetch') as fetch:
                update.main()
            self.assertFalse((root/'update-request.json').exists())
            fetch.assert_not_called()

    def test_disabled_updater_does_not_contact_github(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'updates.json').write_text('{"enabled":false}')
            with patch.object(update, 'ROOT', root), patch.object(update.os, 'geteuid', return_value=0, create=True), patch('builtins.open', unittest.mock.mock_open()), patch.dict(sys.modules, {'fcntl': SimpleNamespace(flock=lambda *_: None, LOCK_EX=2, LOCK_NB=4)}), patch.object(update, 'fetch') as fetch, patch.object(update, 'publish_status') as status:
                update.main()
            fetch.assert_not_called()
            status.assert_called_once_with({'state': 'disabled'})

    def test_checksum_failure_never_runs_installer_or_marks_release_installed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'updates.json').write_text('{"enabled":true}')
            release = {'tag_name': 'agent-latest', 'assets': [{'name': 'relay-agent.tar.gz', 'browser_download_url': update.ASSET_PREFIX+'relay-agent.tar.gz', 'digest': 'sha256:'+'0'*64}]}
            with patch.object(update, 'ROOT', root), patch.object(update.os, 'geteuid', return_value=0, create=True), patch('builtins.open', unittest.mock.mock_open()), patch.dict(sys.modules, {'fcntl': SimpleNamespace(flock=lambda *_: None, LOCK_EX=2, LOCK_NB=4)}), patch.object(update, 'fetch', side_effect=[json.dumps(release).encode(), b'corrupted release']), patch.object(update, 'publish_status') as status, patch.object(update.subprocess, 'run') as run, self.assertRaises(SystemExit):
                update.main()
            run.assert_not_called()
            status.assert_called_once_with({'state': 'failed'})
            self.assertFalse((root/'installed-release.json').exists())

    def test_release_requires_exact_source_and_github_digest(self):
        asset = {'name': 'relay-agent.tar.gz', 'browser_download_url': update.ASSET_PREFIX + 'relay-agent.tar.gz', 'digest': 'sha256:' + 'a' * 64}
        release = {'tag_name': 'agent-latest', 'assets': [asset]}
        self.assertEqual(update.release_asset(release), asset)
        for change in ({'browser_download_url': 'https://evil.example/archive'}, {'digest': ''}, {'digest': 'sha256:bad'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                update.release_asset({**release, 'assets': [{**asset, **change}]})
        for change in ({'draft': True}, {'tag_name': 'pro-latest'}, {'assets': [asset, asset]}):
            with self.assertRaises(ValueError): update.release_asset({**release, **change})

    def test_package_rejects_links_traversal_and_extra_members_before_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / 'package.tar.gz'
            for name, kind in (('../agent.py', tarfile.REGTYPE), ('agent.py', tarfile.SYMTYPE), ('extra', tarfile.REGTYPE)):
                with tarfile.open(archive, 'w:gz') as stream:
                    for entry in update.FILES:
                        info = tarfile.TarInfo(name if entry == 'agent.py' else entry)
                        info.type = kind if entry == 'agent.py' else tarfile.REGTYPE
                        stream.addfile(info, io.BytesIO())
                with self.subTest(name=name, kind=kind), self.assertRaises(ValueError):
                    update.unpack(archive, root)

    def test_actual_package_passes_preflight_without_executing_installer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / 'package.tar.gz'
            with tarfile.open(archive, 'w:gz') as stream:
                for name in update.FILES: stream.add(SOURCE / name, arcname=name)
            stage = root / 'stage'; stage.mkdir()
            if __import__('os').name == 'nt':
                with patch.object(update.subprocess, 'run') as run:
                    update.unpack(archive, stage)
                self.assertEqual(run.call_count, 2)
            else:
                update.unpack(archive, stage)
            self.assertEqual(json.loads((stage/'manifest.json').read_text())['id'], 'relay-agent')


if __name__ == '__main__': unittest.main()
