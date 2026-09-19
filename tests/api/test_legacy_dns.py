"""Legacy DNS migration must preserve live services and existing configuration."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("legacy_dns", ROOT / "api/legacy_dns.py")
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)


class LegacyDnsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.managed = self.root / "unbound.conf.d/vps-control.conf"
        self.managed.parent.mkdir()
        self.original = "server:\n  interface: 127.0.0.1@5335\n  interface: 10.73.0.1@53\n  access-control: 0.0.0.0/0 refuse\n"
        self.managed.write_text(self.original)
        self.config = self.root / "unbound.conf"
        self.config.write_text('include-toplevel: "unbound.conf.d/*.conf"\n')
        self.fragment = self.managed.with_name("vps-control-freebind.conf")

    def checker(self, args, **kwargs):
        self.assertEqual(args[0], "unbound-checkconf")  # No service mutations.
        self.assertEqual(args[-1], str(self.config))
        output = ""
        if args[1:3] == ["-o", "ip-freebind"]:
            output = "yes\n" if self.fragment.exists() else "no\n"
        elif args[1:3] == ["-o", "interface"]:
            output = "127.0.0.1@5335\n10.73.0.1@53\n"
        return subprocess.CompletedProcess(args, 0, output, "")

    @patch.object(dns.shutil, "which", return_value="unbound-checkconf")
    def test_migrates_old_dns_once_preserving_addresses_and_acl(self, _):
        with patch.object(dns.subprocess, "run", side_effect=self.checker):
            self.assertTrue(dns.migrate(self.root))
            written = self.fragment.stat().st_mtime_ns
            self.assertFalse(dns.migrate(self.root))
        self.assertEqual(self.fragment.stat().st_mtime_ns, written)
        self.assertEqual(self.managed.read_text(), self.original)
        self.assertIn("ip-freebind: yes", self.fragment.read_text())

    def test_new_installs_and_unrelated_dns_are_untouched(self):
        for content in (None, "server:\n  interface: 127.0.0.1@5335\n  interface: ::1\n", "server:\n  interface: 0.0.0.0\n"):
            with self.subTest(content=content):
                if content is None:
                    self.managed.unlink(missing_ok=True)
                else:
                    self.managed.write_text(content)
                with patch.object(dns.subprocess, "run") as run:
                    self.assertFalse(dns.migrate(self.root))
                run.assert_not_called()
                self.assertFalse(self.fragment.exists())

    @patch.object(dns.shutil, "which", return_value=None)
    def test_missing_validator_does_not_change_files(self, _):
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            dns.migrate(self.root)
        self.assertFalse(self.fragment.exists())
        self.assertEqual(self.managed.read_text(), self.original)

    @patch.object(dns.shutil, "which", return_value="unbound-checkconf")
    def test_validation_failure_or_ignored_fragment_rolls_back(self, _):
        for failure in ("before", "after", "ignored", "timeout"):
            with self.subTest(failure=failure):
                def check(args, **kwargs):
                    result = self.checker(args, **kwargs)
                    if failure == "before" or (failure == "after" and self.fragment.exists()):
                        result.returncode = 1
                    if failure == "ignored" and args[1:3] == ["-o", "ip-freebind"]:
                        result.stdout = "no"
                    if failure == "timeout" and self.fragment.exists():
                        raise subprocess.TimeoutExpired(args, 15)
                    return result
                with patch.object(dns.subprocess, "run", side_effect=check):
                    with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                        dns.migrate(self.root)
                self.assertFalse(self.fragment.exists())
                self.assertEqual(self.managed.read_text(), self.original)

    @patch.object(dns.shutil, "which", return_value="unbound-checkconf")
    def test_unused_legacy_file_is_not_activated(self, _):
        def check(args, **kwargs):
            result = self.checker(args, **kwargs)
            if args[1:3] == ["-o", "interface"]:
                result.stdout = "127.0.0.1@5335\n192.0.2.1@53\n"
            return result
        with patch.object(dns.subprocess, "run", side_effect=check):
            self.assertFalse(dns.migrate(self.root))
        self.assertFalse(self.fragment.exists())

    @patch.object(dns.shutil, "which", return_value="unbound-checkconf")
    def test_existing_fragment_is_never_overwritten(self, _):
        for effective in ("yes", "no"):
            with self.subTest(effective=effective):
                self.fragment.write_text("# Existing administrator content\n")
                def check(args, **kwargs):
                    result = self.checker(args, **kwargs)
                    if args[1:3] == ["-o", "ip-freebind"]:
                        result.stdout = effective
                    return result
                with patch.object(dns.subprocess, "run", side_effect=check):
                    if effective == "yes":
                        self.assertFalse(dns.migrate(self.root))
                    else:
                        with self.assertRaises(FileExistsError):
                            dns.migrate(self.root)
                self.assertEqual(self.fragment.read_text(), "# Existing administrator content\n")


if __name__ == "__main__":
    unittest.main()
