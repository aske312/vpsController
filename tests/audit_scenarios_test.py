import tempfile
import shlex
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from tests.api_portability_test import api


class AuditScenariosTests(unittest.TestCase):
    def test_password_readonly_failure_returns_structured_error(self):
        original='OriginalPassword123!'
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'panel.env';file.write_text('ADMIN_PASSWORD='+original+'\n')
            with patch.object(api,'ENV_FILE',file),patch.object(api,'ADMIN_PASSWORD',original),patch.object(api.os,'open',side_effect=OSError('readonly')),patch.object(api.Path,'unlink',side_effect=OSError('readonly')):
                with self.assertRaises(api.HTTPException) as error:
                    api.change_admin_password(api.AdminPasswordChange(current_password=original,new_password='NewPassword123456!',confirm_password='NewPassword123456!'))
                self.assertEqual(error.exception.status_code,500)
                self.assertEqual(api.ADMIN_PASSWORD,original)

    def test_password_special_characters_survive_env_parsing(self):
        original='OriginalPassword123!'
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'panel.env'
            for password in ('QaPassword$HOME123!', 'QaPassword`id`123!', "QaPassword'Quote123!", 'QaPassword"Quote123!', 'QaPassword\\Back123!'):
                file.write_text('ADMIN_PASSWORD='+original+'\nOTHER=keep\n')
                with patch.object(api,'ENV_FILE',file),patch.object(api,'ADMIN_PASSWORD',original),patch.dict(os.environ):
                    api.change_admin_password(api.AdminPasswordChange(current_password=original,new_password=password,confirm_password=password))
                    self.assertEqual(api.ADMIN_PASSWORD,password)
                value=file.read_text().splitlines()[0].split('=',1)[1]
                self.assertEqual(shlex.split(value),[password])
                self.assertIn('OTHER=keep',file.read_text())

    def test_password_persistence_failure_keeps_old_credentials_and_env(self):
        original='OriginalPassword123!'
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'panel.env';file.write_text('ADMIN_PASSWORD='+original+'\n')
            saved=file.read_bytes()
            with patch.object(api,'ENV_FILE',file),patch.object(api,'ADMIN_PASSWORD',original),patch.object(api.Path,'replace',side_effect=OSError('rename denied')),patch.dict(os.environ):
                with self.assertRaises(api.HTTPException):api.change_admin_password(api.AdminPasswordChange(current_password=original,new_password='NewPassword123456!',confirm_password='NewPassword123456!'))
                self.assertEqual(api.ADMIN_PASSWORD,original)
            self.assertEqual(file.read_bytes(),saved)
            self.assertEqual(list(Path(directory).iterdir()),[file])

    def test_probe_waits_for_listener_after_systemd_reports_active(self):
        with patch.object(api, 'protocol_listener', side_effect=[('unit',8445,'tcp',False),('unit',8445,'tcp',False),('unit',8445,'tcp',True)]), patch.object(api, 'run',return_value='active'), patch.object(api.time,'sleep'):
            self.assertTrue(api.wait_protocol_listener('xray')[3])
        with patch.object(api,'protocol_listener',return_value=('unit',8445,'tcp',False)), patch.object(api,'run',return_value='failed'), patch.object(api.time,'sleep') as sleep:
            self.assertFalse(api.wait_protocol_listener('xray')[3]);sleep.assert_not_called()

    def test_invalid_dns_is_rejected_before_provisioning(self):
        for field in ('dns', 'xray_dns'):
            for value in ('999.1.1.1', '1.1.1.1,', '1.1.1.1,,8.8.8.8', '::::'):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    api.ClientSettings(**{field: value})
            self.assertEqual(getattr(api.ClientSettings(**{field:'1.1.1.1, 2606:4700:4700::1111'}), field),'1.1.1.1, 2606:4700:4700::1111')

    def test_blank_client_name_is_rejected(self):
        for name in ('  ', ' a '):
            with self.assertRaises(ValueError): api.ClientCreate(name=name,protocol='awg')
        self.assertEqual(api.ClientCreate(name='  My device  ', protocol='awg').name,'My device')

    def test_corrupt_inventory_blocks_creation_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'clients.json'
            for content in ('{broken', '{}', '[null]', '[{"id":"a","protocol":"invalid"}]'):
                file.write_text(content)
                with patch.object(api,'CLIENTS_FILE',file), patch.object(api,'run') as run, patch.object(api,'write_clients') as write:
                    with self.assertRaises(api.HTTPException) as error:
                        api.create_client(api.ClientCreate(name='QA device',protocol='awg'))
                    self.assertEqual(error.exception.status_code,503)
                    run.assert_not_called(); write.assert_not_called()
                self.assertEqual(file.read_text(),content)
            file.unlink()
            with patch.object(api,'CLIENTS_FILE',file): self.assertEqual(api.read_clients(),[])
