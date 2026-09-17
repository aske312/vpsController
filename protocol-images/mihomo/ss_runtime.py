"""Snapshot and restore systemd state of Mihomo Shadowsocks instances."""
import re


PREFIX = "vps-control-mihomo-ss@"
UNIT = re.compile(r"vps-control-mihomo-ss@[A-Za-z0-9_-]+\.service")


def snapshot(run, config_root, *, strict=True):
    names = {f"{PREFIX}{path.stem}.service" for path in (config_root / "shadowsocks").glob("*.json")}
    for command in ("list-units", "list-unit-files"):
        result = run("systemctl", command, "--all", "--plain", "--no-legend", f"{PREFIX}*.service", check=True)
        names.update(line.split()[0] for line in result.stdout.splitlines() if line.strip())
    result = {}
    for unit in sorted(names):
        if not UNIT.fullmatch(unit):
            continue
        response = run("systemctl", "show", unit, "--property=ActiveState,UnitFileState", check=True)
        properties = dict(line.split("=", 1) for line in response.stdout.splitlines() if "=" in line)
        active, enabled = properties.get("ActiveState"), properties.get("UnitFileState")
        # Do not start a mutation while systemd is still transitioning or its
        # state cannot be restored precisely by this manager.
        if strict and (active not in {"active", "inactive", "failed"} or enabled not in {"enabled", "enabled-runtime", "disabled", "masked", "masked-runtime"}):
            raise RuntimeError(f"Cannot snapshot Shadowsocks instance {unit}: {active}/{enabled}")
        path = config_root / "shadowsocks" / f"{unit[len(PREFIX):-len('.service')]}.json"
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
