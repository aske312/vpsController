import subprocess
import unittest
from unittest.mock import patch

from tests.api.support import api
from service_state import observe_service, ssh_observation, failed_unit_count


def output(active="active", enabled="disabled", load="loaded", **extra):
    values = {"LoadState": load, "ActiveState": active, "SubState": "running" if active == "active" else "dead",
              "UnitFileState": enabled, "NRestarts": "0", **extra}
    return "\n".join(f"{key}={value}" for key, value in values.items())


class ServiceObservationTests(unittest.TestCase):
    def test_missing_unit_exit_code_requires_explicit_not_found_property(self):
        for text, present in (("LoadState=not-found\nActiveState=inactive", False), ("", None)):
            with patch("service_state.subprocess.run", return_value=subprocess.CompletedProcess([], 4, text, "")):
                self.assertIs(observe_service("absent.service")["unit_present"], present)

    def observe(self, text):
        with patch("service_state.subprocess.run", return_value=subprocess.CompletedProcess([], 0, text, "")) as run:
            result = observe_service("example.service")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:2], ["systemctl", "show"])
        return result

    def test_presence_runtime_and_autostart_are_independent(self):
        for state, expected in [("active", "running"), ("inactive", "stopped"), ("failed", "error"),
                                ("activating", "unknown"), ("deactivating", "unknown"), ("reloading", "unknown")]:
            with self.subTest(state=state):
                result = self.observe(output(state))
                self.assertTrue(result["unit_present"])
                self.assertTrue(result["installed"])
                self.assertFalse(result["enabled"])
                self.assertEqual(result["runtime"]["state"], expected)
                self.assertTrue(result["runtime"]["checked_at"])
                self.assertTrue(result["runtime"]["reason"])
                self.assertNotIn("health", result)

    def test_missing_unit_differs_from_failed_or_partial_observation(self):
        self.assertIs(self.observe(output(load="not-found", active="inactive"))["unit_present"], False)
        self.assertEqual(self.observe(output(load="bad-setting", active="inactive"))["runtime"]["state"], "error")
        for text in ("", "LoadState=loaded", "unparseable", output(NRestarts="invalid")):
            with self.subTest(text=text):
                result = self.observe(text)
                self.assertIsNone(result["restarts"])
                if "ActiveState" not in text:
                    self.assertEqual(result["runtime"]["state"], "unknown")
                if "LoadState" not in text:
                    self.assertIsNone(result["unit_present"])
        for failure in (OSError("private error"), subprocess.TimeoutExpired("systemctl", 8)):
            with patch("service_state.subprocess.run", side_effect=failure):
                result = observe_service("example.service")
            self.assertIsNone(result["unit_present"])
            self.assertEqual(result["runtime"]["state"], "unknown")
            self.assertNotIn("private", result["runtime"]["reason"])
        with patch("service_state.subprocess.run", return_value=subprocess.CompletedProcess([], 1, output(), "private error")):
            result = observe_service("example.service")
        self.assertIsNone(result["unit_present"])
        self.assertEqual(result["runtime"]["state"], "unknown")

    def test_ssh_socket_activation_and_partial_failure(self):
        stopped = self.observe(output("inactive"))
        socket = self.observe(output(enabled="enabled", NRestarts=""))
        unknown = self.observe("")
        missing = self.observe(output("inactive", load="not-found"))
        active = ssh_observation(stopped, socket)
        self.assertTrue(active["active"])
        self.assertTrue(active["enabled"])
        self.assertEqual(active["runtime"]["state"], "running")
        self.assertEqual(active["substate"], "socket activation")
        self.assertEqual(active["restarts"], 0)
        self.assertEqual(ssh_observation(stopped, missing)["runtime"]["state"], "stopped")
        self.assertEqual(ssh_observation(stopped, unknown)["runtime"]["state"], "unknown")
        self.assertEqual(ssh_observation(self.observe(output("failed")), socket)["runtime"]["state"], "error")
        self.assertIs(ssh_observation(missing, missing)["unit_present"], False)

    def test_failed_count_unknown_is_not_zero(self):
        for code, text, expected in [(0, "", 0), (0, "a.service failed\nb.service failed\n", 2), (1, "", None)]:
            with self.subTest(code=code, text=text), patch("service_state.subprocess.run", return_value=subprocess.CompletedProcess([], code, text, "")):
                self.assertEqual(failed_unit_count(), expected)
        with patch("service_state.subprocess.run", side_effect=subprocess.TimeoutExpired("systemctl", 8)):
            self.assertIsNone(failed_unit_count())

    def test_services_endpoint_keeps_unknown_but_hides_confirmed_absence(self):
        observed = {"unknown.service": self.observe(""), "missing.service": self.observe(output("inactive", load="not-found")),
                    "stopped.service": self.observe(output("inactive"))}
        definitions = {key: {"unit": key, "name": key, "controls": []} for key in observed}
        with patch.object(api, "managed_services", return_value=definitions), \
             patch.object(api, "observe_service", side_effect=lambda unit: observed[unit]), \
             patch.object(api, "failed_unit_count", return_value=None), \
             patch.object(api, "run", return_value=""), \
             patch.object(api, "configured_panel_channels", return_value=[]), \
             patch.object(api, "read_automation", return_value={}), \
             patch.object(api, "internal_panel_url", return_value=""), \
             patch.object(api, "external_panel_url", return_value=""):
            result = api.services_status()
        self.assertEqual([item["id"] for item in result["items"]], ["unknown.service", "stopped.service"])
        self.assertIsNone(result["failed_units"])
        self.assertEqual(result["items"][0]["runtime"]["state"], "unknown")

    def test_service_command_needs_confirmed_final_runtime(self):
        definition = {"unit": "fixture.service", "controls": ["start", "stop", "restart"]}
        with patch.object(api, "managed_services", return_value={"fixture": definition}), patch.object(api, "run", return_value=""):
            for action, state, success in [("start", "running", True), ("restart", "stopped", False),
                                           ("stop", "stopped", True), ("stop", "unknown", False)]:
                with self.subTest(action=action, state=state), patch.object(api, "service_details", return_value={"runtime": {"state": state}}):
                    if success:
                        self.assertEqual(api.perform_service_action("fixture", api.ServiceAction(action=action))["runtime"]["state"], state)
                    else:
                        with self.assertRaises(api.HTTPException) as denied:
                            api.perform_service_action("fixture", api.ServiceAction(action=action))
                        self.assertEqual(denied.exception.status_code, 409)
