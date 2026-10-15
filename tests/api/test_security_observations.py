import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api


class SecurityObservationTests(unittest.TestCase):
    def test_host_path_mapping_keeps_unix_and_windows_rooted_paths_inside_fixture(self):
        for value in ("/sys/module", "/etc/ufw/ufw.conf", "C:/sys/module", "relative/path"):
            with self.subTest(value=value):
                self.host_path(value).resolve().relative_to(self.root.resolve())

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.failure = None
        self.units = {}
        self.unit_files = {}
        self.commands = []
        self.journal = ""
        self.jail_status = "Currently banned: 0\nTotal banned: 7"
        self.kernel_settings = {"net.ipv4.ip_forward": "1", "net.ipv4.tcp_syncookies": "1"}
        self.kernel_output = None
        self.ssh_config = "passwordauthentication no\npermitrootlogin no\nx11forwarding no\nallowtcpforwarding yes\nmaxauthtries 3\nlistenaddress 0.0.0.0:22\nlistenaddress [::]:22"
        self.ssh_connections = ""
        self.listeners = "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*"
        self.accounts = "root:x:0:0:root:/root:/bin/bash\nfixture:x:1000:1000::/home/fixture:/bin/bash"
        self.groups = "root:x:0:\nsudo:x:27:fixture"
        self.apparmor_status = "12\n"
        self.apparmor_code = 0
        self.route_output = {"default": "default via 192.0.2.1 dev fixture0", "10.74.0.0/24": ""}
        self.firewall_code = 1
        self.firewall_error = "Bad rule (does a matching rule exist in that chain?)."
        self.chain_listing_code = None
        for name, value in list(vars(api).items()):
            if name.isupper() and isinstance(value, Path):
                self.enterContext(patch.object(api, name, self.root if name == "DATA_DIR" else self.root / name))
        self.enterContext(patch.object(api, "Path", self.host_path))
        for path, content in (("/proc/sys/net/ipv4/ip_forward", "1"), ("/proc/sys/net/ipv4/tcp_syncookies", "1"),
                              ("/etc/ufw/ufw.conf", "ENABLED=yes\n")):
            target = self.host_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self.host_path("/sys/module").mkdir(parents=True, exist_ok=True)
        self.enterContext(patch.object(api, "PUBLIC_DOMAIN", ""))
        self.enterContext(patch.object(api, "run", side_effect=self.command))
        self.enterContext(patch.object(api, "command_succeeds", return_value=False))
        self.process = self.enterContext(patch("service_state.subprocess.run", side_effect=self.systemd))
        self.enterContext(patch.object(api, "system_resources", return_value={"cpu_percent": 12}))
        self.enterContext(patch.object(api, "all_client_dump", return_value=[]))
        # No diagnostic/network refresh threads in an isolated observation test.
        self.enterContext(api.updates_refresh_lock)
        self.enterContext(api.app_version_refresh_lock)

    def host_path(self, value):
        path = Path(value)
        return self.root / "host" / str(path).replace(":", "").lstrip("/\\") if path.is_absolute() or path.root else self.root / path

    def systemd(self, arguments, **_):
        if arguments[0] == "iptables":
            code = self.chain_listing_code if "-S" in arguments and self.chain_listing_code is not None else self.firewall_code
            return subprocess.CompletedProcess(arguments, 4 if self.failure == "firewall" else code, "", self.firewall_error)
        if arguments[0] == "aa-status":
            return subprocess.CompletedProcess(arguments, 4 if self.failure == "apparmor" else self.apparmor_code, self.apparmor_status, "private fixture detail")
        unit = arguments[2]
        state = self.units.get(unit, "active")
        if state is None:
            return subprocess.CompletedProcess(arguments, 1, "", "unavailable")
        if state == "not-found":
            return subprocess.CompletedProcess(arguments, 4, "LoadState=not-found\nActiveState=inactive", "")
        text = f"LoadState=loaded\nActiveState={state}\nSubState=running\nNRestarts=0"
        if (enabled := self.unit_files.get(unit, "enabled")) is not None:
            text += f"\nUnitFileState={enabled}"
        return subprocess.CompletedProcess(arguments, 0, text, "")

    def command(self, *arguments, **options):
        self.commands.append((arguments, options))
        name = arguments[0]
        if (self.failure == "journal" and name == "journalctl") or (self.failure == "listeners" and name == "ss") \
                or (self.failure == "ssh-config" and name == "/usr/sbin/sshd") \
                or (self.failure == "kernel" and name == "sysctl") \
                or (self.failure == "ssh-connections" and arguments[:2] == ("ss", "-Htn")) \
                or (self.failure == "accounts" and arguments[:2] == ("getent", "passwd")) \
                or (self.failure == "groups" and arguments[:2] == ("getent", "group")) \
                or (self.failure == "apparmor" and name == "aa-status") \
                or (self.failure == "routes" and name == "ip") \
                or (self.failure == "jail" and name == "fail2ban-client"):
            if options.get("check"):
                raise api.HTTPException(status_code=503, detail="Fixture observation unavailable")
            return ""
        if name == "journalctl":
            return self.journal
        if name == "fail2ban-client":
            return self.jail_status
        if name == "/usr/sbin/sshd":
            return self.ssh_config
        if name == "ss":
            if arguments[1] == "-Htn":
                return self.ssh_connections
            return self.listeners
        if name == "sysctl":
            return self.kernel_output if self.kernel_output is not None else "\n".join(
                self.kernel_settings.get(key, "0") for key in arguments[2:]
            )
        if arguments[:2] == ("getent", "passwd"):
            return self.accounts
        if arguments[:2] == ("getent", "group"):
            return self.groups
        if name == "aa-status":
            return self.apparmor_status
        if name == "ip":
            return self.route_output[arguments[-1]]
        return ""

    def test_empty_journal_is_zero_but_failed_journal_is_unavailable(self):
        result = api.security()
        self.assertEqual(result["failed_ssh_records_24h"], 0)
        self.assertEqual(result["accepted_ssh_24h"], 0)
        self.journal = "Failed password and Invalid user\nauthentication failure\nAccepted publickey\nother"
        result = api.security()
        self.assertEqual(result["failed_ssh_records_24h"], 2)
        self.assertEqual(result["accepted_ssh_24h"], 1)
        self.failure = "journal"
        with self.assertRaises(api.HTTPException):
            api.security()

    def test_failed_service_read_is_null_and_confirmed_inactive_is_false(self):
        for state, expected in ((None, None), ("inactive", False), ("active", True)):
            with self.subTest(state=state):
                self.units = {unit: state for unit in ("ufw.service", "fail2ban.service", "ssh.service", "ssh.socket")}
                result = api.security()
                self.assertIs(result["firewall"]["active"], expected)
                self.assertIs(result["fail2ban"]["active"], expected)
                self.assertIs(result["ssh"]["active"], expected)
                if state is None:
                    self.assertIsNone(result["ssh"]["publicly_allowed"])
                    self.assertIsNone(result["fail2ban"]["jail_active"])
                    self.assertIsNone(result["fail2ban"]["currently_banned"])

    def test_failed_jail_status_keeps_service_active_and_counters_unknown(self):
        self.failure = "jail"
        result = api.security()
        self.assertTrue(result["fail2ban"]["active"])
        self.assertIsNone(result["fail2ban"]["jail_active"])
        self.assertIsNone(result["fail2ban"]["currently_banned"])
        self.assertIsNone(result["fail2ban"]["total_banned"])
        self.failure = None
        result = api.security()
        self.assertTrue(result["fail2ban"]["jail_active"])
        self.assertEqual(result["fail2ban"]["currently_banned"], 0)
        self.assertEqual(result["fail2ban"]["total_banned"], 7)
        self.jail_status = "Unrecognized successful output"
        result = api.security()
        self.assertIsNone(result["fail2ban"]["jail_active"])
        self.assertIsNone(result["fail2ban"]["currently_banned"])

    def test_failed_listener_or_ssh_config_read_cannot_confirm_full_security(self):
        for failure in ("listeners", "ssh-config"):
            with self.subTest(failure=failure):
                self.failure = failure
                with self.assertRaises(api.HTTPException):
                    api.security()

    def test_failed_live_listener_does_not_hide_other_live_sources(self):
        self.failure = "listeners"
        result = api.live_status()
        self.assertIsNone(result["security"])
        self.assertIn("security", result["unavailable"])
        self.assertEqual(result["resources"]["cpu_percent"], 12)
        self.assertEqual(result["clients"], [])
        self.failure = None
        self.assertTrue(api.live_status()["security"]["ssh_listening"])

    def test_kernel_settings_use_one_complete_checked_observation(self):
        self.kernel_settings.update({"net.ipv4.conf.all.rp_filter": "2", "kernel.dmesg_restrict": "1"})
        result = api.security()
        self.assertTrue(result["firewall"]["forwarding_enabled"])
        self.assertTrue(result["system"]["syn_cookies"])
        self.assertFalse(result["system"]["rp_filter"])
        self.assertEqual(result["system"]["rp_filter_mode"], 2)
        self.assertTrue(result["system"]["rp_filter_valid"])
        self.assertTrue(result["system"]["dmesg_restricted"])
        calls = [(args, options) for args, options in self.commands if args[0] == "sysctl"]
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1]["check"])
        self.assertEqual(len(calls[0][0][2:]), 10)

    def test_kernel_failure_or_partial_invalid_output_cannot_confirm_full_security(self):
        self.failure = "kernel"
        with self.assertRaises(api.HTTPException):
            api.security()
        self.failure = None
        for output in ("", "0", "\n".join(["0"] * 11), "\n".join(["0"] * 9 + ["unknown"]),
                       "\n".join(["0"] * 9 + ["2"]), "\n".join(["-1"] + ["0"] * 9)):
            with self.subTest(output=output):
                self.kernel_output = output
                with self.assertRaises(api.HTTPException):
                    api.security()

    def test_insecure_policy_does_not_skip_observing_later_kernel_parameters(self):
        self.kernel_settings["net.ipv4.conf.all.accept_redirects"] = "1"
        self.kernel_settings["net.ipv4.conf.default.accept_source_route"] = "unknown"
        with self.assertRaises(api.HTTPException):
            api.security()
        self.kernel_settings["net.ipv4.conf.default.accept_source_route"] = "1"
        result = api.security()
        self.assertFalse(result["system"]["redirects_disabled"])
        self.assertFalse(result["system"]["source_route_disabled"])
        self.kernel_settings["net.ipv4.tcp_syncookies"] = "2"
        self.assertTrue(api.security()["system"]["syn_cookies"])

    def test_ssh_config_requires_complete_recognized_policy_and_preserves_listeners(self):
        result = api.security()
        self.assertEqual(result["ssh"]["listen_addresses"], ["0.0.0.0:22", "[::]:22"])
        valid = self.ssh_config
        for value in ("", "passwordauthentication no", valid.replace("permitrootlogin no", "permitrootlogin unknown"),
                      valid.replace("maxauthtries 3", "maxauthtries -1"), valid + "\npasswordauthentication yes",
                      valid + "\nmalformed"):
            with self.subTest(value=value):
                self.ssh_config = value
                with self.assertRaises(api.HTTPException):
                    api.security()
        self.ssh_config = valid.replace("permitrootlogin no", "permitrootlogin prohibit-password")
        self.ssh_config += "\nhostkey /etc/ssh/ssh_host_rsa_key\nhostkey /etc/ssh/ssh_host_ed25519_key"
        self.assertEqual(api.security()["ssh"]["permit_root_login"], "prohibit-password")

    def test_empty_ssh_connections_are_zero_but_failure_is_unavailable(self):
        self.assertEqual(api.security()["ssh"]["active_connections"], 0)
        self.ssh_connections = "first connection\nsecond connection"
        self.assertEqual(api.security()["ssh"]["active_connections"], 2)
        self.failure = "ssh-connections"
        with self.assertRaises(api.HTTPException):
            api.security()
        self.assertTrue(api.live_status()["security"]["ssh_listening"])

    def test_ssh_listener_uses_local_tcp_port_and_loopback_binding(self):
        for line, expected in (("", (False, False)), ("udp UNCONN 0 0 0.0.0.0:22 0.0.0.0:*", (False, False)),
                               ("tcp LISTEN 0 128 0.0.0.0:443 0.0.0.0:22", (False, False)),
                               ("tcp LISTEN 0 128 127.0.0.1:22 0.0.0.0:*", (True, False)),
                               ("tcp LISTEN 0 128 [::1]:22 [::]:*", (True, False)),
                               ("LISTEN 0 128 [::ffff:127.0.0.1]:22 *:*", (True, False)),
                               ("LISTEN 0 128 [::]:22 [::]:*", (True, True)),
                               ("tcp LISTEN 0 128 *:22 *:*", (True, True))):
            with self.subTest(line=line):
                self.assertEqual(api.ssh_listener_state(line.splitlines()), expected)
                self.listeners = line
                self.assertEqual(api.live_status()["security"]["ssh_listening"], expected[0])

    def test_public_ssh_requires_positive_rule_and_no_rule_never_means_closed(self):
        rules_file = self.host_path("/etc/ufw/user.rules")
        rules_file.parent.mkdir(parents=True, exist_ok=True)
        for rule, expected in (("", None), ("deny tcp 22 0.0.0.0/0 any 0.0.0.0/0 in", None),
                               ("allow udp 22 0.0.0.0/0 any 0.0.0.0/0 in", None),
                               ("allow tcp 22 0.0.0.0/0 any 0.0.0.0/0 in_wg0", None),
                               ("allow tcp 22 0.0.0.0/0 any 0.0.0.0/0 in", True)):
            with self.subTest(rule=rule):
                rules_file.write_text("### tuple ### " + rule if rule else "")
                self.assertIs(api.security()["ssh"]["publicly_allowed"], expected)
        self.host_path("/etc/ufw/ufw.conf").write_text("ENABLED=no\n")
        self.assertIsNone(api.security()["ssh"]["publicly_allowed"])
        self.listeners = "tcp LISTEN 0 128 127.0.0.1:22 0.0.0.0:*"
        self.assertIs(api.security()["ssh"]["publicly_allowed"], False)

    def test_malformed_tcp_listener_is_unavailable_in_full_and_cheap_reads(self):
        for line in ("tcp LISTEN malformed", "tcp ESTAB 0 0 0.0.0.0:22 *:*",
                     "tcp LISTEN 0 128 unknown:22 *:*", "tcp LISTEN 0 128 0.0.0.0:invalid *:*"):
            with self.subTest(line=line):
                self.listeners = line
                with self.assertRaises(api.HTTPException):
                    api.security()
                live = api.live_status()
                self.assertIsNone(live["security"])
                self.assertIn("security", live["unavailable"])
                self.assertEqual(live["resources"]["cpu_percent"], 12)

    def test_audit_and_legacy_services_keep_failed_reads_unknown_and_missing_distinct(self):
        for state, expected in ((None, None), ("inactive", False), ("active", True), ("not-found", False)):
            with self.subTest(state=state):
                self.units = {name: state for name in ("auditd.service", "openvpn.service", "strongswan-starter.service", "xl2tpd.service")}
                result = api.security()
                self.assertIs(result["system"]["auditd_active"], expected)
                for value in result["legacy_services"].values():
                    self.assertIs(value["active"], expected)
                    self.assertEqual(value["enabled"], "unknown" if state is None else "not-found" if state == "not-found" else "enabled")

    def test_automatic_updates_require_observed_runtime_or_autostart(self):
        unit = "unattended-upgrades.service"
        for state, enabled, expected in ((None, None, None), ("inactive", None, None),
                                         ("inactive", "disabled", False), ("inactive", "enabled", True),
                                         ("active", None, True), ("not-found", None, False)):
            with self.subTest(state=state, enabled=enabled):
                self.units[unit] = state
                self.unit_files[unit] = enabled
                self.assertIs(api.security()["updates"]["automatic"], expected)

    def test_user_observation_includes_primary_sudo_group_and_excludes_nonlogin_shells(self):
        self.accounts += "\nprimary:x:1001:27::/home/primary:/bin/sh\nservice:x:1002:1002::/:/usr/sbin/nologin\ndisabled:x:1003:1003::/:/bin/false"
        self.groups += ",primary"
        result = api.security()["system"]
        self.assertEqual(result["sudo_users"], ["fixture", "primary"])
        self.assertEqual(result["login_users"], ["root", "fixture", "primary"])
        self.groups = "root:x:0:"
        self.assertEqual(api.security()["system"]["sudo_users"], [])

    def test_failed_empty_or_malformed_account_sources_do_not_confirm_empty_lists(self):
        for failure in ("accounts", "groups"):
            self.failure = failure
            with self.assertRaises(api.HTTPException):
                api.security()
        self.failure = None
        for attribute, value in (("accounts", ""), ("groups", ""), ("accounts", "malformed"),
                                 ("groups", "sudo:x:unknown:fixture"), ("groups", "sudo:x:27:fixture,,other"),
                                 ("groups", "sudo:x:27:fixture\nsudo:x:28:other")):
            with self.subTest(attribute=attribute, value=value):
                previous = getattr(self, attribute)
                setattr(self, attribute, value)
                with self.assertRaises(api.HTTPException):
                    api.security()
                setattr(self, attribute, previous)

    def test_apparmor_absence_inactive_loaded_and_failed_profile_observations_are_distinct(self):
        self.assertEqual(api.security()["system"]["apparmor"], {"active": False, "profiles": 0})
        enabled = self.host_path("/sys/module/apparmor/parameters/enabled")
        enabled.parent.mkdir(parents=True, exist_ok=True)
        enabled.write_text("N\n")
        self.assertEqual(api.security()["system"]["apparmor"], {"active": False, "profiles": 0})
        enabled.write_text("Y\n")
        self.assertEqual(api.security()["system"]["apparmor"], {"active": True, "profiles": 12})
        self.apparmor_status = "0\n"
        self.apparmor_code = 2
        self.assertEqual(api.security()["system"]["apparmor"], {"active": True, "profiles": 0})
        self.failure = "apparmor"
        with self.assertRaises(api.HTTPException):
            api.security()
        self.failure = None
        self.apparmor_status = "Unrecognized result"
        with self.assertRaises(api.HTTPException):
            api.security()
        for code, output in ((3, "0"), (4, "0"), (42, "12"), (2, "12"), (0, "-1"), (0, "unknown")):
            with self.subTest(code=code, output=output):
                self.apparmor_code = code
                self.apparmor_status = output
                with self.assertRaises(api.HTTPException):
                    api.security_apparmor()
        enabled.write_text("unknown")
        with self.assertRaises(api.HTTPException):
            api.security()

    def test_unavailable_kernel_module_tree_is_not_apparmor_absence(self):
        self.host_path("/sys/module").rmdir()
        with self.assertRaises(api.HTTPException):
            api.security()

    def test_route_observation_parses_device_without_gateway_and_requires_checked_source(self):
        for output, expected in (("default via 192.0.2.1 dev eth0 proto static", "eth0"),
                                 ("default dev ppp0 scope link", "ppp0"), ("", ""),
                                 ("unreachable default", "")):
            with self.subTest(output=output):
                self.route_output["default"] = output
                self.assertEqual(api.security()["firewall"]["uplink_interface"], expected)
        self.assertTrue(all(options["check"] for arguments, options in self.commands if arguments[0] == "ip"))
        for output in ("default via 192.0.2.1", "default dev", "default dev invalid/name"):
            self.route_output["default"] = output
            with self.assertRaises(api.HTTPException):
                api.security()
        self.failure = "routes"
        with self.assertRaises(api.HTTPException):
            api.security()

    def test_firewall_rule_checks_distinguish_present_absent_and_source_errors(self):
        self.host_path(f"/sys/class/net/{api.WG_INTERFACE}").mkdir(parents=True, exist_ok=True)
        self.firewall_code = 0
        self.assertTrue(api.security()["firewall"]["protocol_policies"]["wg"]["healthy"])
        self.firewall_code = 1
        self.assertFalse(api.security()["firewall"]["protocol_policies"]["wg"]["healthy"])
        for code, error in ((1, "private permission problem"), (1, ""), (2, "Invalid argument"),
                            (3, "Kernel incompatibility"), (4, "Lock unavailable"), (111, "Unexpected error")):
            with self.subTest(code=code, error=error):
                self.firewall_code, self.firewall_error = code, error
                with self.assertRaises(api.HTTPException) as failure:
                    api.security()
                self.assertNotIn(error or "private", failure.exception.detail)
        firewall_calls = [call.args[0] for call in self.process.call_args_list if call.args[0][0] == "iptables"]
        self.assertTrue(firewall_calls)
        self.assertTrue(all("-C" in arguments or "-S" in arguments for arguments in firewall_calls))

    def test_missing_chain_check_is_read_only_and_does_not_hide_target_or_match_failure(self):
        self.firewall_code = 1
        self.firewall_error = "iptables: No chain/target/match by that name."
        self.assertFalse(api.security_iptables_rule("iptables", "-t", "nat", "-C", "missing", "-j", "MASQUERADE"))
        self.assertEqual(self.process.call_args.args[0], ["iptables", "-t", "nat", "-S", "missing"])
        self.chain_listing_code = 0
        with self.assertRaises(api.HTTPException):
            api.security_iptables_rule("iptables", "-C", "FORWARD", "-j", "ACCEPT")

    def test_missing_or_timed_out_firewall_command_is_unavailable(self):
        for error in (FileNotFoundError(), OSError("permission"), subprocess.TimeoutExpired("iptables", 8)):
            with self.subTest(error=error), patch.object(api.subprocess, "run", side_effect=error):
                with self.assertRaises(api.HTTPException):
                    api.security_iptables_rule("iptables", "-C", "FORWARD", "-j", "ACCEPT")
