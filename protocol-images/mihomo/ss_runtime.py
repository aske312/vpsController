"""Snapshot and restore systemd state of Mihomo Shadowsocks instances."""
import re


PREFIX = "vps-control-mihomo-ss@"
UNIT = re.compile(r"vps-control-mihomo-ss@[A-Za-z0-9_-]+\.service")


def snapshot(run, config_root, *, strict=True, prefix=None, directory=None):
    prefix = prefix or PREFIX
    directory = directory or config_root / "shadowsocks"
    pattern = re.compile(re.escape(prefix) + r"[A-Za-z0-9_-]+\.service")
    names = {f"{prefix}{path.stem}.service" for path in directory.glob("*.json") if not path.name.startswith(".")}
    for command in ("list-units", "list-unit-files"):
        result = run("systemctl", command, "--all", "--plain", "--no-legend", f"{prefix}*.service", check=True)
        names.update(line.split()[0] for line in result.stdout.splitlines() if line.strip())
    result = {}
    for unit in sorted(names):
        if not pattern.fullmatch(unit):
            continue
        response = run("systemctl", "show", unit, "--property=ActiveState,UnitFileState", check=True)
        properties = dict(line.split("=", 1) for line in response.stdout.splitlines() if "=" in line)
        active, enabled = properties.get("ActiveState"), properties.get("UnitFileState")
        # Do not start a mutation while systemd is still transitioning or its
        # state cannot be restored precisely by this manager.
        if strict and (active not in {"active", "inactive", "failed"} or enabled not in {"enabled", "enabled-runtime", "disabled", "masked", "masked-runtime"}):
            raise RuntimeError(f"Cannot snapshot Shadowsocks instance {unit}: {active}/{enabled}")
        path = directory / f"{unit[len(prefix):-len('.service')]}.json"
        result[unit] = (active == "active", enabled, path.read_bytes() if path.exists() else None)
    return result


def remove_created(run, before, current):
    errors = []
    for unit in sorted(current.keys() - before.keys()):
        try:
            run("systemctl", "disable", "--now", unit, check=True)
            run("systemctl", "disable", "--runtime", unit, check=True)
        except Exception as exc:
            errors.append(f"{unit}: {exc}")
    if errors:
        raise RuntimeError("Shadowsocks cleanup incomplete: " + "; ".join(errors))


def restore(run, before, current):
    """Call after restoring configuration files and the target's running state."""
    errors = []
    for unit, (active, enabled, config) in before.items():
        try:
            previous = current.get(unit)
            if previous is None or previous[1] != enabled:
                run("systemctl", "unmask", unit, check=True)
                run("systemctl", "unmask", "--runtime", unit, check=True)
                run("systemctl", "disable", unit, check=True)
                run("systemctl", "disable", "--runtime", unit, check=True)
                if enabled != "disabled":
                    command = "mask" if enabled.startswith("masked") else "enable"
                    args = ("--runtime",) if enabled.endswith("-runtime") else ()
                    run("systemctl", command, *args, unit, check=True)
            # Query again: restoring the target may have started enabled units
            # that were intentionally stopped before the transaction.
            state = run("systemctl", "is-active", unit).stdout.strip()
            if active and (state != "active" or previous is None or previous[2] != config):
                run("systemctl", "restart", unit, check=True)
            elif not active and state not in {"inactive", "failed"}:
                run("systemctl", "stop", unit, check=True)
            response = run("systemctl", "show", unit, "--property=ActiveState,UnitFileState", check=True)
            restored = dict(line.split("=", 1) for line in response.stdout.splitlines() if "=" in line)
            restored_active = restored.get("ActiveState")
            if restored.get("UnitFileState") != enabled or (restored_active != "active" if active else restored_active not in {"inactive", "failed"}):
                raise RuntimeError("Instance state was not restored")
        except Exception as exc:
            errors.append(f"{unit}: {exc}")
    if errors:
        raise RuntimeError("Shadowsocks rollback incomplete: " + "; ".join(errors))


def main():
    import argparse
    import json
    import os
    import subprocess
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("capture", "restore"))
    parser.add_argument("--direct", action="store_true")
    args = parser.parse_args()
    directory = Path("/etc/vps-control/shadowsocks/clients" if args.direct else "/etc/vps-control/mihomo/shadowsocks")
    prefix = "vps-control-shadowsocks@" if args.direct else PREFIX
    path = directory / ".retained-services.json"
    def run(*command, check=False):
        return subprocess.run(command, capture_output=True, text=True, timeout=30, check=check)
    if args.action == "capture":
        if path.exists():
            return  # Retrying a removal must keep the original runtime state.
        state = snapshot(run, directory.parent, prefix=prefix, directory=directory)
        if not state:
            return
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as target:
                os.chmod(temporary, 0o600)
                json.dump({unit: {"active": active, "enabled": enabled} for unit, (active, enabled, _) in state.items()}, target)
                target.flush()
                os.fsync(target.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    elif path.exists():
        stored = json.loads(path.read_text(encoding="utf-8"))
        before = {}
        for unit, state in stored.items():
            if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_-]+\.service", unit) or type(state.get("active")) is not bool or state.get("enabled") not in {"enabled", "enabled-runtime", "disabled", "masked", "masked-runtime"}:
                raise RuntimeError("Invalid retained instance state")
            config = directory / f"{unit[len(prefix):-len('.service')]}.json"
            before[unit] = (state["active"], state["enabled"], config.read_bytes())
        restore(run, before, snapshot(run, directory.parent, prefix=prefix, directory=directory, strict=False))
        path.unlink()


if __name__ == "__main__":
    main()
