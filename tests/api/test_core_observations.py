import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api


class CoreObservationsTests(unittest.TestCase):
    def test_application_observation_never_invents_health_or_stopped_state(self):
        for state in ("unknown", "running", "stopped", "error"):
            observation = {"unit_present": None if state == "unknown" else True,
                "runtime": {"state": state, "reason": "Observed", "checked_at": "now"},
                "active": state == "running", "enabled": False, "unit_file_state": "unknown"}
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary, \
                 patch.object(api, "DATA_DIR", Path(temporary)), \
                 patch.object(api, "ACTION_FILE", Path(temporary) / "action.json"), \
                 patch.object(api, "observe_service", return_value=observation), \
                 patch.object(api, "run", return_value=""), \
                 patch.object(api.cdn_security, "settings", return_value={}), \
                 patch.object(api.cdn_operation, "status", return_value=None):
                result = api.application_status(None)
                self.assertEqual(result["api"]["runtime"]["state"], state)
                self.assertIsNone(result["api"]["enabled"])
                if state == "unknown":
                    self.assertIsNone(result["api"]["active"])
                    self.assertEqual(result["runtime"]["mode"], "unknown")
                for item in result["containers"]:
                    self.assertEqual(item["State"], state)
                    self.assertIsNone(item["healthy"])
