"""Recognize legacy panel installations without granting ownership to arbitrary units."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess

from component_registry import ComponentRegistry, inventory, fingerprint
import application_operation as operations


def service_contract(image_id: str, install_dir: Path) -> tuple[str, dict]:
    if image_id == "mihomo":
        return "vps-control-mihomo-manager.service", {
            "Description": "GATE.312 Mihomo connection manager",
            "WorkingDirectory": f"{install_dir}/protocol-images/mihomo",
            "ExecStart": f"{install_dir}/venv/bin/uvicorn manager:app --host 127.0.0.1 --port 8791 --no-server-header",
        }
    if image_id == "shadowsocks":
        return "vps-control-shadowsocks@.service", {
            "Description": "312.net Shadowsocks connection %i",
            "ExecStart": "/usr/bin/ss-server -c /etc/vps-control/shadowsocks/clients/%i.json",
            "ExecStopPost": "/usr/local/sbin/vps-control-shadowsocks-firewall delete %i",
        }
    commands = {
        "tuic": ("TUIC v5", "/usr/local/lib/vps-control-tuic/sing-box run -c /etc/vps-control/tuic/config.json"),
        "trojan": ("Trojan", "/usr/local/lib/vps-control-trojan/sing-box run -c /etc/vps-control/trojan/config.json"),
        "openvpn": ("OpenVPN", "/usr/sbin/openvpn --config /etc/vps-control/openvpn/server.conf"),
        "ikev2": ("IKEv2", "/usr/sbin/charon-systemd"),
        "hysteria2": ("Hysteria2", "/usr/local/lib/vps-control-hysteria2/hysteria server -c /etc/vps-control/hysteria2/config.yaml"),
    }
    if image_id == "vless-reality-xhttp":
        return "vps-control-vless-reality-xhttp.service", {
            "Description": "312.net VLESS REALITY XHTTP",
            "ExecStart": "/usr/local/lib/vps-control-vless-reality-xhttp/xray run -config /etc/vps-control/vless-reality-xhttp/config.json",
        }
    if image_id not in commands:
        return "", {}
    label, command = commands[image_id]
    expected = {"Description": f"312.net {label} server", "ExecStart": command,
                "ExecStopPost": f"/usr/local/lib/vps-control-{image_id}/firewall.sh delete"}
    if image_id == "ikev2":
        expected["ExecStartPost"] = "/usr/sbin/swanctl --load-all --file /etc/swanctl/swanctl.conf --noprompt"
    return f"vps-control-{image_id}.service", expected


def provenance_paths(image_id: str, interface: str, install_dir: Path, *, root=Path("/"), run=subprocess.run) -> list[Path]:
    """Return validated evidence files, or nothing when provenance is ambiguous.

    File presence/name alone is insufficient: inspect the installed unit's command
    and panel-specific configuration contract. Modified/overridden units stay manual.
    Only systemctl show is used; no services or configurations are changed.
    """
    if image_id in {"wg", "awg"}:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", interface) or interface.startswith("mh-"):
            return []
        folder = "wireguard" if image_id == "wg" else "amnezia/amneziawg"
        name = "wireguard" if image_id == "wg" else "amneziawg"
        config = root / f"etc/{folder}/{interface}.conf"
        marker = root / f"etc/sysctl.d/99-vps-control-{name}.conf"
        try:
            text = config.read_text(encoding="utf-8")
            sysctl = marker.read_text(encoding="utf-8")
        except OSError:
            return []
        # Generic wg-quick units are shared with external installations. Require
        # the panel installer's complete characteristic firewall lifecycle too.
        lines = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        values = {key.strip(): value.strip() for key, value in lines.items()}
        up, down = values.get("PostUp", ""), values.get("PostDown", "")
        if not ("net.ipv4.ip_forward=1" in sysctl and
                up.startswith("iptables -C INPUT -p udp --dport ") and
                "iptables -I FORWARD 1 -i %i -j ACCEPT" in up and
                "iptables -t nat -A POSTROUTING -s " in up and
                down.startswith("while iptables -C INPUT -p udp --dport ") and
                "while iptables -C FORWARD -i %i -j ACCEPT" in down and
                "iptables -t nat -D POSTROUTING -s " in down):
            return []
        unit = f"{'wg' if image_id == 'wg' else 'awg'}-quick@{interface}.service"
        evidence = [config, marker]
    else:
        unit, expected = service_contract(image_id, install_dir)
        if not unit:
            return []
        path = root / "etc/systemd/system" / unit
        if path.is_symlink():
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        entries: dict[str, list[str]] = {}
        for line in text.splitlines():
            if "=" in line and not line.lstrip().startswith(("#", ";")):
                key, value = line.split("=", 1)
                entries.setdefault(key.strip(), []).append(value.strip())
        for key, value in expected.items():
            allowed = [value]
            if image_id == "mihomo" and key == "ExecStart":
                allowed.append(value.removesuffix(" --no-server-header"))
            if entries.get(key) not in [[option] for option in allowed]:
                return []
        evidence = [path]
    observed_unit = unit.replace("@.service", "@migration-probe.service")
    result = run(["systemctl", "show", observed_unit, "--property=FragmentPath,DropInPaths,LoadState,NeedDaemonReload"],
                 capture_output=True, text=True, timeout=8, check=False)
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or props.get("LoadState") != "loaded" or props.get("DropInPaths") or props.get("NeedDaemonReload") == "yes":
        return []
    if image_id not in {"wg", "awg"} and props.get("FragmentPath") != str(evidence[0]):
        return []
    if image_id in {"wg", "awg"}:
        fragment = Path(props.get("FragmentPath", ""))
        if fragment.name != f"{image_id}-quick@.service" or fragment.parent.as_posix() not in {"/lib/systemd/system", "/usr/lib/systemd/system"}:
            return []
    return evidence


def migrate(api) -> bool:
    """Run once after workers/recovery are clear. GET endpoints remain read-only."""
    try:
        with operations.short_mutation(api.DATA_DIR):
            registry = ComponentRegistry(api.DATA_DIR)
            for image_id, image in api.protocol_image_manifests().items():
                if image.get("management", {}).get("state") != "unmanaged":
                    continue
                try:
                    evidence = provenance_paths(image_id, image.get("interface", ""), api.INSTALL_DIR)
                    if not evidence:
                        continue
                    plan, paths, context = api.adoption_plan(image_id)
                    if not plan["compatible"]:
                        continue
                    # An environment override must not make us recognize the
                    # default WG interface but authorize a different config.
                    if image_id in {"wg", "awg"} and evidence[0] not in paths:
                        continue
                    # Evidence participates in both fingerprint and private backup.
                    paths = list(dict.fromkeys([*paths, *evidence]))
                    context = {**context, "migration": "legacy-panel-v1"}
                    checksum = fingerprint(inventory(paths), context)
                    registry.adopt(image_id, paths, context, checksum, migrated=True,
                                   verify_provenance=lambda: provenance_paths(image_id, image.get("interface", ""), api.INSTALL_DIR) == evidence)
                    api.logger.info("Legacy panel component migrated: %s", image_id)
                except (OSError, ValueError, subprocess.SubprocessError, api.HTTPException):
                    # An invalid component must not prevent unrelated migrations.
                    api.logger.warning("Legacy migration unavailable for %s; manual preview remains available", image_id)
        return True
    except operations.OperationConflict:
        return False
