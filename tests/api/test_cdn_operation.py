"""Results remain queryable when reloading the gateway drops the requesting client."""
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api

operations = api.cdn_operation
ID = "a" * 32
OTHER_ID = "b" * 32


class CdnOperationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = patch.object(operations, "DIRECTORY", Path(self.directory.name))
        self.override.start()
        self.addCleanup(self.override.stop)

    def start(self):
        with patch.object(operations.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as launch:
            result = operations.start(True, ID, "/control")
        return result, launch

    def test_launch_returns_without_waiting_and_repeated_id_does_not_reexecute(self):
        result, launch = self.start()
        self.assertEqual(result["state"], "queued")
        self.assertNotIn("--wait", launch.call_args.args[0])
        self.assertNotIn("--pipe", launch.call_args.args[0])
        with patch.object(operations.subprocess, "run") as second:
            self.assertEqual(operations.start(True, ID, "/control"), result)
            second.assert_not_called()
        with self.assertRaises(operations.OperationConflict):
            operations.start(False, ID, "/control")
        with self.assertRaises(operations.OperationConflict):
            operations.start(True, OTHER_ID, "/control")

    def test_worker_finishing_during_launch_is_not_overwritten_by_queued_state(self):
        def launch(*args, **kwargs):
            operation = operations.status(ID)
            operations.write({**operation, "state": "succeeded", "progress": 100})
            return subprocess.CompletedProcess([], 0, "", "")
        with patch.object(operations.subprocess, "run", side_effect=launch):
            self.assertEqual(operations.start(True, ID, "/control")["state"], "succeeded")
        self.assertEqual(operations.status()["state"], "succeeded")

    def test_launch_timeout_keeps_result_pending(self):
        with patch.object(operations.subprocess, "run", side_effect=subprocess.TimeoutExpired("systemd-run", 10)):
            result = operations.start(True, ID, "/control")
        self.assertEqual(result["state"], "queued")
        self.assertEqual(operations.status(ID)["state"], "queued")

    def test_collected_unit_does_not_erase_success_and_stale_worker_is_not_success(self):
        result, _ = self.start()
        operations.write({**result, "state": "running", "created_at": time.time() - 60})
        with patch.object(operations.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ActiveState=inactive\nLoadState=not-found\n", "")):
            self.assertEqual(operations.status(ID)["state"], "failed")
        operations.write({**result, "state": "succeeded", "created_at": time.time() - 60})
        with patch.object(operations.subprocess, "run") as query:
            self.assertEqual(operations.status(ID)["state"], "succeeded")
            query.assert_not_called()

    def test_systemd_query_failure_does_not_report_a_command_failure(self):
        result, _ = self.start()
        operations.write({**result, "state": "running", "created_at": time.time() - 60})
        with patch.object(operations.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "Failed to connect to bus")):
            self.assertEqual(operations.status(ID)["state"], "running")

    def test_worker_records_progress_and_confirmed_outcome(self):
        self.start()
        def configure(enabled, progress):
            self.assertTrue(enabled)
            progress(45, "Checking")
            self.assertEqual(operations.status(ID)["progress"], 45)
        with patch.object(operations.cdn_security, "configure_aop", side_effect=configure):
            operations.run(ID)
        self.assertEqual(operations.status(ID)["state"], "succeeded")
        self.assertEqual(operations.status(ID)["progress"], 100)

    def test_worker_failure_is_saved_without_exposing_traceback(self):
        self.start()
        with patch.object(operations.cdn_security, "configure_aop", side_effect=RuntimeError("private technical detail")), patch.object(operations.traceback, "print_exc") as log:
            with self.assertRaises(RuntimeError):
                operations.run(ID)
            log.assert_called_once()
        status = operations.status(ID)
        self.assertEqual(status["state"], "failed")
        self.assertNotIn("private technical detail", status["message"])

    def test_status_endpoint_checks_authentication_and_returns_current_setting(self):
        from fastapi.testclient import TestClient
        client = TestClient(api.app)
        self.start()
        with patch.object(api, "ADMIN_USER", "test"), patch.object(api, "ADMIN_PASSWORD", "test"), patch.object(api.cdn_security, "settings", return_value={"authenticated_origin_pulls": False}):
            self.assertEqual(client.get("/api/application/cdn-security").status_code, 401)
            response = client.get(f"/api/application/cdn-security?operation_id={ID}", auth=("test", "test"))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["authenticated_origin_pulls"])
            self.assertEqual(response.json()["operation"]["id"], ID)
            self.assertEqual(client.get(f"/api/application/cdn-security?operation_id={OTHER_ID}", auth=("test", "test")).status_code, 404)
            self.assertEqual(client.get("/api/application/cdn-security?operation_id=../../etc/passwd", auth=("test", "test")).status_code, 422)

    def test_api_accepts_a_background_operation_and_rejects_invalid_ids(self):
        from fastapi.testclient import TestClient
        client = TestClient(api.app)
        with patch.object(api, "ADMIN_USER", "test"), patch.object(api, "ADMIN_PASSWORD", "test"), patch.object(api.cdn_security, "settings", return_value={"authenticated_origin_pulls": False}), patch.object(operations.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as launch:
            response = client.put("/api/application/cdn-security", json={"authenticated_origin_pulls": True, "operation_id": ID}, auth=("test", "test"))
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["operation"]["state"], "queued")
            self.assertFalse(response.json()["authenticated_origin_pulls"])
            self.assertEqual(client.put("/api/application/cdn-security", json={"authenticated_origin_pulls": True, "operation_id": "invalid"}, auth=("test", "test")).status_code, 422)
            launch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
