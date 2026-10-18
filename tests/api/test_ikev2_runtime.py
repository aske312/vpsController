"""Update-time EAP repair preserves foreign runtimes and stopped services."""
import subprocess
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from tests.api.support import api
import ikev2_runtime as runtime
from component_registry import RegistryError


class Ikev2RuntimeTests(unittest.TestCase):
    def test_managed_repair_preserves_present_foreign_and_failed_states(self):
        for owned, present, failure in ((False,False,False),(True,True,False),(True,False,False),(True,False,True)):
            with self.subTest(owned=owned,present=present,failure=failure):
                state = {'installed':present}
                calls = []
                def command(arguments,**kwargs):
                    calls.append(arguments)
                    if arguments[0] == 'dpkg-query':
                        return subprocess.CompletedProcess(arguments,0,'installed' if state['installed'] else '')
                    if arguments[0] == 'apt-get':
                        self.assertEqual(kwargs['env']['DEBIAN_FRONTEND'],'noninteractive')
                        if failure:
                            raise subprocess.CalledProcessError(1,arguments)
                        state['installed'] = True
                    return subprocess.CompletedProcess(arguments,0)
                with (patch.object(runtime,'SERVICE_FILE',Mock(is_file=lambda:True)),
                        patch.object(runtime,'CONFIG_FILE',Mock(is_file=lambda:False)),
                        patch.object(runtime,'ComponentRegistry') as registry,
                        patch.object(runtime.subprocess,'run',side_effect=command)):
                    if not owned:
                        registry.return_value.require_managed.side_effect = RegistryError('foreign')
                    if failure:
                        with self.assertRaises(subprocess.CalledProcessError):
                            runtime.repair()
                    else:
                        self.assertEqual(runtime.repair(),owned and not present)
                    if owned and not present and not failure:
                        self.assertFalse(runtime.repair())
                packages = [call for call in calls if call[0]=='apt-get']
                services = [call for call in calls if call[0]=='systemctl']
                if not owned or present:
                    self.assertEqual(packages,[])
                    self.assertEqual(services,[])
                elif failure:
                    self.assertEqual(services,[])
                else:
                    self.assertEqual(len(packages),1)
                    self.assertIn('--no-remove',packages[0])
                    self.assertEqual(packages[0][-1],runtime.PACKAGE)
                    self.assertEqual(services,[['systemctl','try-restart','vps-control-ikev2.service']])

    def test_existing_managed_auth_migrates_only_legacy_remote_and_is_idempotent(self):
        original = '''connections {
  other {
    remote {
      auth = eap-dynamic
    }
  }
  ikev2-eap {
    local {
      auth = pubkey
    }
    remote {
      auth = eap-dynamic # preserve comment
      eap_id = %any
    }
  }
}
include users.conf
'''
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder)/'swanctl.conf'
            config.write_text(original)
            config.chmod(0o600)
            with (patch.object(runtime,'SERVICE_FILE',Mock(is_file=lambda:True)),
                  patch.object(runtime,'CONFIG_FILE',config),
                  patch.object(runtime,'ComponentRegistry') as registry,
                  patch.object(runtime,'installed',return_value=True),
                  patch.object(runtime.subprocess,'run') as command):
                registry.return_value.require_managed.side_effect = RegistryError('foreign')
                self.assertFalse(runtime.repair())
                self.assertEqual(config.read_text(), original)
                registry.return_value.require_managed.side_effect = None
                self.assertTrue(runtime.repair())
                self.assertEqual(config.read_text(), original.replace('eap-dynamic #','eap-mschapv2 #'))
                self.assertFalse(runtime.repair())
                command.assert_called_once_with(['systemctl','try-restart','vps-control-ikev2.service'],check=True,timeout=60)
                self.assertEqual(list(Path(folder).glob('.eap-repair-*')),[])
