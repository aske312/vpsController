"""Owned tunnel config edits and stop/write/start ordering, without networking."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.api.support import manager
import profile_recovery
import tunnel_settings


def settings():
    return {"port": 18000, "subnet": "10.82.0.0/24", "mtu": 1280,
            **{key: index + 1 for index, key in enumerate(tunnel_settings.AWG_FIELDS)}}


def config(awg=False):
    text = "[Interface]\nAddress = 10.82.0.1/24\nListenPort = 18000\nPrivateKey = retained-key\nMTU = 1280\n"
    text += "PostUp = iptables -I INPUT -p udp --dport 18000 -j ACCEPT; iptables -t nat -A POSTROUTING -s 10.82.0.0/24 -j MASQUERADE\n"
    text += "PostDown = iptables -D INPUT -p udp --dport 18000 -j ACCEPT; iptables -t nat -D POSTROUTING -s 10.82.0.0/24 -j MASQUERADE\n"
    if awg:
        text += "".join(f"{field} = {settings()[key]}\n" for key, field in tunnel_settings.AWG_FIELDS.items())
    return text + "\n# mihomo-profile:one:peer\n[Peer]\nPublicKey = retained-peer\nAllowedIPs = 10.82.0.2/32\n"


class TunnelSettingsTests(unittest.TestCase):
    def test_edits_preserve_keys_and_peer_block(self):
        for module in ("transport-wg", "transport-awg"):
            with self.subTest(module=module):
                before = config(module.endswith("awg"))
                values = {**settings(), "mtu": 1400, "jc": 10}
                result = tunnel_settings.render(before, module, settings(), values)
                self.assertIn("MTU = 1400", result)
                self.assertIn("PrivateKey = retained-key", result)
                self.assertEqual(result.split("# mihomo-profile:")[1], before.split("# mihomo-profile:")[1])
                if module.endswith("awg"):
                    self.assertIn("Jc = 10", result)

    def test_empty_tunnel_port_subnet_change_updates_both_hooks(self):
        values = {**settings(), "port": 18001, "subnet": "10.84.0.0/24"}
        result = tunnel_settings.render(config().split("# mihomo-profile:")[0], "transport-wg", settings(), values)
        self.assertIn("ListenPort = 18001", result)
        self.assertIn("Address = 10.84.0.1/24", result)
        self.assertEqual(result.count("--dport 18001"), 2)
        self.assertEqual(result.count("-s 10.84.0.0/24"), 2)

    def test_ambiguous_config_and_unknown_hooks_are_rejected(self):
        cases = [config().replace("MTU = 1280", "MTU = 1280\nMTU = 1300"),
                 config().replace("[Interface]", "[Interface]\n[Interface]"),
                 config().replace("--dport 18000", "--dport 19000"),
                 config().replace("ListenPort = 18000", "ListenPort = 19000")]
        for value in cases:
            with self.subTest(value=value[:25]), self.assertRaises(RuntimeError):
                tunnel_settings.render(value.split("# mihomo-profile:")[0], "transport-wg", settings(), {**settings(), "port": 18001})

    def test_unregistered_peers_also_block_breaking_changes(self):
        with self.assertRaisesRegex(RuntimeError, "содержит peers"):
            tunnel_settings.render(config(), "transport-wg", settings(), {**settings(), "port": 18001})

    def test_live_tunnel_apply_and_rollback_order(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "tunnel.conf"
                path.write_text(config())
                path.chmod(0o600)
                original = path.read_bytes()
                state = ["running"]
                events = []
                def run(*args, **kwargs):
                    action = args[1]
                    events.append((action, path.read_bytes()))
                    if action == "stop":
                        state[0] = "stopped"
                    elif action == "start":
                        if fail:
                            raise RuntimeError("candidate start failed")
                        state[0] = "running"
                    elif action == "restart":
                        self.assertEqual(path.read_bytes(), original)
                        state[0] = "running"
                api = SimpleNamespace(PROFILE_FILE=root / "profiles.json", ROUTING_SETTINGS_FILE=root / "routing.json",
                    CONFIG_ROOT=root / "config", SETTINGS_ROOT=root / "settings", TRANSPORTS={"transport-wg"},
                    WG_CONFIG_BY_MODULE={"transport-wg": path}, SERVICE_BY_MODULE={"transport-wg": "fixture.service"},
                    observe_service=lambda _: {"unit_present": True, "unit_file_state": "disabled", "runtime": {"state": state[0]}},
                    get_action_payload=lambda: {}, run=run, service_stably_active=lambda _: state[0] == "running")
                candidate = tunnel_settings.prepare(api, "transport-wg", settings(), {**settings(), "mtu": 1400})
                def apply():
                    with profile_recovery.transaction(api, {"transport-wg"}, settings_modules={"transport-wg"}):
                        tunnel_settings.apply(api, "transport-wg", candidate)
                if fail:
                    with self.assertRaisesRegex(RuntimeError, "candidate start failed"):
                        apply()
                    self.assertEqual(path.read_bytes(), original)
                else:
                    apply()
                    self.assertIn(b"MTU = 1400", path.read_bytes())
                self.assertEqual(events[0], ("stop", original))
                self.assertIn(b"MTU = 1400", events[1][1])
                self.assertEqual(state[0], "running")

    def test_breaking_tunnel_edits_reject_active_and_retiring_consumers(self):
        for module, key, value in (("transport-wg", "port", 18001), ("transport-wg", "subnet", "10.84.0.0/24"),
                                    ("transport-awg", "h1", 999)):
            for kind in ("connections", "retiring_connections"):
                with (self.subTest(module=module, key=key, kind=kind),
                      patch.object(manager, "module_settings", return_value=settings()),
                      patch.object(manager, "validate_settings", return_value={**settings(), key: value}),
                      patch.object(manager, "profiles", return_value=[{kind: [{"component": module}]}]),
                      patch.object(manager, "atomic_json") as save,
                      self.assertRaises(manager.HTTPException) as blocked):
                    manager.patch_module_settings(module, manager.ModuleSettingsPatch(values={key: value}))
                self.assertEqual(blocked.exception.status_code, 409)
                save.assert_not_called()
