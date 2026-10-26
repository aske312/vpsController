"""Shared subscription limits must survive protocol/device edits and restarts."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from tests.api.support import manager

limits = manager.subscription_limits


def profile(identity="first"):
    return {"id": identity, "limits_controlled": True, "limits": {"speed_mbps": 50, "traffic_gb": 10, "days": 30}, "limits_started_at": 1000,
            "devices": [{"id": "common"}, {"id": "phone"}], "connections": [
                {"id": "xray", "component": "transport-reality", "device_id": "common", "credential": {"email": f"mihomo-{identity}-xray"}},
                {"id": "hy", "component": "transport-hysteria2", "device_id": "phone", "credential": {"instance_id": f"{identity}-hy"}},
                {"id": "tuic", "component": "transport-tuic", "device_id": "common", "credential": {"instance_id": f"{identity}-tuic"}},
                {"id": "ss", "component": "transport-shadowsocks", "device_id": "phone", "credential": {"port": 31000, "instance_id": f"{identity}-ss"}},
                {"id": "wg", "component": "transport-wg", "device_id": "phone", "credential": {"ip": "10.90.0.2/24"}},
                {"id": "awg", "component": "transport-awg", "device_id": "common", "credential": {"ip": "10.91.0.2/24"}},
            ]}


class SubscriptionLimitsTests(unittest.TestCase):
    def test_systemd_escaped_cgroup_path_remains_literal_in_nft_syntax(self):
        item = profile()
        unit = limits.plan([item])["profiles"][0]["shadowsocks"][0]["unit"]
        path = r"/system.slice/system-vps\x2dcontrol.slice/instance.service"
        rules = limits.nft_rules(limits.plan([item]), {}, {unit: {"level": 3, "path": path}})
        self.assertIn('"' + path.lstrip('/') + '"', rules)
        self.assertNotIn(r"\\x2d", rules)

    def test_old_updater_missing_runtime_is_bootstrapped_and_verified(self):
        with patch.object(manager.shutil, "which", side_effect=[None, "/usr/sbin/nft"]), \
                patch.object(manager, "systemctl_active", return_value=True), \
                patch.object(limits.subprocess, "run") as run:
            limits.ensure_runtime(manager)
        self.assertEqual(run.call_args.args[0], ["bash", str(manager.MODULE_ROOT / "install-subscription-limits.sh")])
        self.assertTrue(run.call_args.kwargs["check"])

    def test_ready_runtime_is_not_reinstalled_at_manager_restart(self):
        with patch.object(manager.shutil, "which", return_value="/usr/sbin/nft"), \
                patch.object(manager, "systemctl_active", return_value=True), \
                patch.object(limits.subprocess, "run") as run:
            limits.ensure_runtime(manager)
        run.assert_not_called()

    def test_runtime_install_failure_prevents_false_readiness(self):
        with patch.object(manager.shutil, "which", return_value=None), \
                patch.object(limits.subprocess, "run"), self.assertRaises(RuntimeError):
            limits.ensure_runtime(manager)

    def test_each_limit_is_nullable_but_never_zero_negative_boolean_or_unbounded(self):
        self.assertEqual(limits.normalized(None), limits.UNLIMIT)
        for key, maximum in (("speed_mbps", 100000), ("traffic_gb", 1000000), ("days", 36500)):
            self.assertEqual(limits.normalized({key: maximum})[key], maximum)
            for bad in (0, -1, True, 1.5, "100", maximum + 1):
                with self.subTest(key=key, bad=bad), self.assertRaises(ValidationError):
                    limits.normalized({key: bad})
        with self.assertRaises(ValidationError):
            limits.normalized({"ignore": 100})

    def test_editing_or_extending_days_does_not_silently_renew_subscription(self):
        item = {"id": "first"}
        limits.set_limits(item, {"days": 30}, now=1000)
        limits.set_limits(item, {"days": 60}, now=2000)
        self.assertEqual(item["limits_started_at"], 1000)
        limits.set_limits(item, {}, now=3000)
        self.assertIsNone(item["limits_started_at"])
        limits.set_limits(item, {"days": 7}, now=4000)
        self.assertEqual(item["limits_started_at"], 4000)

    def test_one_identity_includes_all_transports_devices_and_retiring_credentials(self):
        item = profile()
        retired = deepcopy(item["connections"][0])
        retired["credential"]["email"] = "old-encrypted-user"
        item["retiring_connections"] = [retired]
        row = limits.plan([item])["profiles"][0]
        self.assertEqual(len(row["xray_users"]), 2)
        self.assertEqual(set(row["quic_users"]), {"tuic", "hysteria2"})
        self.assertEqual(len(row["shadowsocks"]), 1)
        self.assertEqual({p["interface"] for p in row["peers"]}, {"mh-wg0", "mh-awg0"})
        self.assertNotEqual(row["mark"], limits.plan([profile("second")])["profiles"][0]["mark"])
        self.assertEqual(limits.plan([{**item, "limits_controlled": False}])["profiles"], [])

    def test_shared_kernel_quota_and_rate_objects_are_not_allocated_per_device(self):
        policy = limits.plan([profile()])
        mark = policy["profiles"][0]["mark"]
        rules = limits.nft_rules(policy, {"first": {"rx_bytes": 123, "tx_bytes": 456}}, {})
        self.assertEqual(rules.count(f"quota p{mark}_quota {{"), 1)
        self.assertIn("over 10000000000 bytes used 579 bytes", rules)
        self.assertEqual(rules.count("rate over 6250000 bytes/second"), 2)
        self.assertIn('iifname "mh-wg0" ip saddr 10.90.0.2', rules)
        self.assertIn('oifname "mh-awg0" ip daddr 10.91.0.2', rules)
        self.assertNotIn("flush ruleset", rules)

    def test_unlimit_removes_restrictions_without_discarding_accounting(self):
        item = profile()
        limits.set_limits(item, {}, now=2000)
        rules = limits.nft_rules(limits.plan([item]), {"first": {"rx_bytes": 500, "tx_bytes": 700}}, {})
        self.assertNotIn("quota ", rules)
        self.assertNotIn("limit name", rules)
        self.assertNotIn("meta time", rules)
        self.assertIn("bytes 500", rules)
        self.assertIn("bytes 700", rules)

    def test_authenticated_shared_quic_users_get_the_same_profile_mark(self):
        policy = limits.plan([profile()])
        original = {"inbounds": [{"tag": "hysteria2-in", "up_mbps": 100, "down_mbps": 100, "users": [{"name": "first-hy", "password": "preserved"}]}],
                    "outbounds": [{"type": "direct", "tag": "original"}], "services": [{"tag": "panel-telemetry"}]}
        config = limits.decorate_quic(original, policy, "hysteria2")
        self.assertEqual(config["route"]["rules"][0]["auth_user"], ["first-hy"])
        self.assertEqual(config["outbounds"][-1]["routing_mark"], policy["profiles"][0]["mark"])
        self.assertEqual(config["inbounds"][0]["users"], original["inbounds"][0]["users"])
        self.assertEqual(config["services"], original["services"])
        self.assertNotIn("up_mbps", config["inbounds"][0])
        self.assertTrue(config["inbounds"][0]["ignore_client_bandwidth"])
        self.assertEqual(limits.decorate_quic(config, policy, "hysteria2"), config)

    def test_xray_keeps_api_rule_credentials_and_other_outbounds(self):
        policy = limits.plan([profile()])
        original = {"inbounds": [{"tag": "native", "settings": {"clients": [{"id": "preserved"}]}}],
                    "outbounds": [{"tag": "direct", "protocol": "freedom"}], "routing": {"rules": [{"inboundTag": ["api"], "outboundTag": "api"}]}}
        config = limits.decorate_xray(original, policy)
        self.assertEqual(config["inbounds"], original["inbounds"])
        self.assertEqual(config["routing"]["rules"][0], original["routing"]["rules"][0])
        self.assertEqual(config["outbounds"][-1]["streamSettings"]["sockopt"]["mark"], policy["profiles"][0]["mark"])
        self.assertEqual(limits.decorate_xray(config, policy), config)

    def test_xray_preserves_lan_blocks_and_marks_panel_redirect_instead_of_bypassing_it(self):
        policy = limits.plan([profile()])
        blocked = {"type": "field", "ip": ["10.0.0.0/8"], "outboundTag": "blocked"}
        panel = {"type": "field", "domain": ["full:admin.312.net"], "outboundTag": "panel-local"}
        original = {"outbounds": [{"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIPv4"}},
                                  {"tag": "panel-local", "protocol": "freedom", "settings": {"redirect": "127.0.0.1:80"}},
                                  {"tag": "blocked", "protocol": "blackhole"}], "routing": {"rules": [panel, blocked]}}
        config = limits.decorate_xray(original, policy)
        rules = config["routing"]["rules"]
        self.assertEqual(rules[0]["domain"], panel["domain"])
        marked_panel = next(row for row in config["outbounds"] if row.get("tag") == rules[0]["outboundTag"])
        self.assertEqual(marked_panel["settings"]["redirect"], "127.0.0.1:80")
        self.assertEqual(rules[-2], blocked)
        self.assertEqual(config["outbounds"][-1]["settings"]["domainStrategy"], "UseIPv4")
        self.assertEqual(limits.decorate_xray(config, policy), config)

    def test_client_limits_cannot_override_central_policy(self):
        item = profile()
        item["connections"][1]["settings"] = {"up_mbps": 100, "down_mbps": 100}
        result = limits.client_connections(item, item["connections"])
        self.assertEqual(result[1]["settings"], {"up_mbps": 0, "down_mbps": 0})
        self.assertEqual(item["connections"][1]["settings"]["up_mbps"], 100)

    def test_accounting_never_decreases_on_core_restart_or_failed_edit(self):
        saved = {"first": {"rx_bytes": 1000, "tx_bytes": 2000}}
        self.assertEqual(limits.merge_usage(saved, {"first": {"rx_bytes": 0, "tx_bytes": 100}}), saved)
        result = limits.merge_usage(saved, {"first": {"rx_bytes": 1200, "tx_bytes": 2300}})
        self.assertEqual(result["first"], {"rx_bytes": 1200, "tx_bytes": 2300})

    def test_day_expiry_and_shared_byte_quota_are_independent(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            item = profile()
            limits.atomic(root / "usage.json", {"first": {"rx_bytes": 7000000000, "tx_bytes": 3000000000}})
            limits.atomic(root / "health.json", {"ready": True, "updated_at": 1001})
            self.assertEqual(limits.status(item, root, now=1002)["state"], "exhausted")
            self.assertEqual(limits.status(item, root, now=1000 + 30 * 86400)["state"], "expired")
            limits.set_limits(item, {}, now=2000)
            self.assertEqual(limits.status(item, root, now=2000)["state"], "active")
            self.assertEqual(limits.status(item, root, now=2000)["used_bytes"], 10000000000)

    def test_limit_save_rejects_missing_runtime_instead_of_claiming_enforcement(self):
        with tempfile.TemporaryDirectory() as name, patch.object(manager, "DATA_ROOT", Path(name)), patch.object(manager.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "не установлен"):
                limits.sync(manager, [profile()])
            self.assertFalse((Path(name) / "subscription-limits/policy.json").exists())

    def test_limits_participate_in_optimistic_edit_revision(self):
        item = profile()
        before = manager.profile_revision(item)
        item["limits"]["speed_mbps"] = 75
        self.assertNotEqual(manager.profile_revision(item), before)

    def test_public_subscription_is_denied_before_export_when_expired_or_exhausted(self):
        from starlette.requests import Request
        from fastapi import HTTPException
        for state in ("expired", "exhausted"):
            item = {**profile(), "subscription_token": "fixture-subscription"}
            request = Request({"type": "http", "method": "GET", "path": "/s/fixture-subscription", "headers": []})
            with (patch.object(manager, "profiles", return_value=[item]),
                  patch.object(limits, "status", return_value={"state": state}),
                  patch.object(manager, "render_client_profile") as export,
                  self.assertRaises(HTTPException) as failure):
                manager.public_profile_subscription("fixture-subscription", request, allow_changes=False)
            self.assertEqual(failure.exception.status_code, 403)
            export.assert_not_called()

    def test_crash_recovery_reconciles_restored_desired_policy_with_kernel_policy(self):
        first = limits.plan([profile()])
        second = deepcopy(first)
        second["profiles"][0]["limits"]["speed_mbps"] = 75
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            limits.atomic(root / "policy.json", first)  # restored by profile recovery
            limits.atomic(root / "applied.json", second)  # was last applied to kernel
            limits.atomic(root / "usage.json", {"first": {"rx_bytes": 100, "tx_bytes": 200}})
            with (patch.object(limits, "kernel_state", return_value=({"first": {"rx_bytes": 150, "tx_bytes": 250}}, True)),
                  patch.object(limits, "cgroups", return_value={}), patch.object(limits, "command") as command):
                limits.update(first, root)
            self.assertEqual(limits.read(root / "applied.json", {}), first)
            self.assertEqual(limits.read(root / "usage.json", {})["first"], {"rx_bytes": 150, "tx_bytes": 250})
            self.assertTrue(any(call.args == ("nft", "-f", "-") for call in command.call_args_list))

    def test_standard_subscription_header_reports_bytes_and_expiry(self):
        item = profile()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            limits.atomic(root / "usage.json", {"first": {"rx_bytes": 123, "tx_bytes": 456}})
            header = limits.userinfo(item, root)
        self.assertIn("upload=456; download=123; total=10000000000", header)
        self.assertIn(f"expire={1000 + 30 * 86400}", header)

    def test_legacy_users_are_resolved_from_owned_runtime_not_guessed(self):
        from types import SimpleNamespace
        item = profile()
        item["connections"] = [
            {"id": "legacy-xray", "component": "transport-reality", "credential": {"uuid": "fixture-uuid"}},
            {"id": "legacy-quic", "component": "transport-tuic", "credential": {"uuid": "tuic-uuid", "password": "tuic-secret"}},
            {"id": "legacy-ss", "component": "transport-shadowsocks", "credential": {"port": 31000, "password": "ss-secret"}},
        ]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            limits.atomic(root / "reality/config.json", {"inbounds": [{"settings": {"clients": [{"id": "fixture-uuid", "email": "legacy-user"}]}}]})
            limits.atomic(root / "quic/tuic/config.json", {"inbounds": [{"users": [{"uuid": "tuic-uuid", "password": "tuic-secret", "name": "legacy-quic"}]}]})
            limits.atomic(root / "shadowsocks/old-instance.json", {"server_port": 31000, "password": "ss-secret"})
            resolved = limits.resolve_profiles(SimpleNamespace(CONFIG_ROOT=root), [item])
            row = limits.plan(resolved)["profiles"][0]
            self.assertEqual(row["xray_users"], ["legacy-user"])
            self.assertEqual(row["quic_users"]["tuic"], ["legacy-quic"])
            self.assertEqual(row["shadowsocks"][0]["unit"], "vps-control-mihomo-ss@old-instance.service")
            self.assertNotIn("email", item["connections"][0]["credential"])
            item["connections"][0]["credential"]["uuid"] = "missing"
            with self.assertRaisesRegex(RuntimeError, "однозначно"):
                limits.resolve_profiles(SimpleNamespace(CONFIG_ROOT=root), [item])

    def test_adding_devices_does_not_recreate_existing_kernel_accounting_objects(self):
        first = limits.plan([profile()])
        next_policy = deepcopy(first)
        next_policy["profiles"][0]["quic_users"]["tuic"].append("new-phone")
        self.assertEqual(limits.nft_update_rules(first, next_policy, {}, {}, {}), "")
        next_policy["profiles"][0]["peers"].append({"interface": "mh-wg0", "ip": "10.90.0.3"})
        rules = limits.nft_update_rules(first, next_policy, {}, {}, {})
        self.assertIn("10.90.0.3", rules)
        for kind in ("counter", "quota", "limit", "table"):
            self.assertNotIn(f"delete {kind} ", rules)
            self.assertNotIn(f"add {kind} ", rules)
        self.assertIn('counter name "p', rules)

    def test_exhausted_last_packet_remains_exhausted_after_kernel_restore(self):
        item = profile()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {"rx_bytes": 9999999990, "tx_bytes": 0, "quota_limit_bytes": 10000000000, "quota_used_bytes": 10000000000}
            limits.atomic(root / "usage.json", {"first": state})
            self.assertEqual(limits.status(item, root, now=1001)["state"], "exhausted")
            self.assertIn("used 10000000000 bytes", limits.nft_rules(limits.plan([item]), {"first": state}, {}))
            item["limits"]["traffic_gb"] = 20
            self.assertNotEqual(limits.status(item, root, now=1001)["state"], "exhausted")


if __name__ == "__main__":
    unittest.main()
