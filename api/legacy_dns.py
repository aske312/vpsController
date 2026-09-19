"""Preserve legacy panel DNS across boot without restarting live services.

Older installations bind Unbound to WG/AWG addresses that appear after Unbound
starts. Current releases no longer install Unbound, but must migrate that state.
Do not add After=awg-quick: awg-quick itself is ordered after nss-lookup.target,
which Unbound must precede.
"""
import ipaddress
import re
import shutil
import subprocess
from pathlib import Path


FRAGMENT = "# Allow DNS to start before the VPN address appears during boot.\nserver:\n  ip-freebind: yes\n"


def migrate(config_dir: Path = Path("/etc/unbound")) -> bool:
    """Add a validated fragment for panel-owned DNS; return whether it changed.

    No packages, addresses, ACLs, forwarding, or service state are changed. The
    setting takes effect at the next Unbound start. Refuse to replace existing
    files, and remove our new fragment if validation or effective-value checks
    fail. An existing server-side fix is therefore an idempotent no-op.
    """
    managed = config_dir / "unbound.conf.d/vps-control.conf"
    if not managed.is_file():
        return False
    # Loopback-only DNS does not depend on the tunnel startup order.
    addresses = re.findall(r"^\s*interface:\s*[\"']?([^\s\"'#]+)", managed.read_text(), re.M)
    tunnel_addresses = set()
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("@", 1)[0])
        except ValueError:
            continue
        if not ip.is_loopback and not ip.is_unspecified:
            tunnel_addresses.add(address)
    if not tunnel_addresses:
        return False

    checker = shutil.which("unbound-checkconf")
    if not checker:
        raise RuntimeError("Legacy panel DNS found, but unbound-checkconf is unavailable")
    config = config_dir / "unbound.conf"

    def checked(*options: str) -> str:
        result = subprocess.run(
            [checker, *options, str(config)], capture_output=True, text=True, timeout=15,
        )
        if result.returncode:
            raise RuntimeError("Unbound configuration validation failed; DNS migration not applied")
        return result.stdout.strip()

    checked()
    if checked("-o", "ip-freebind") == "yes":
        return False
    # Ensure this is an active legacy listener, not an unused leftover file.
    active_addresses = set(checked("-o", "interface").split())
    if not active_addresses.intersection(tunnel_addresses):
        return False

    fragment = managed.with_name("vps-control-freebind.conf")
    # Exclusive creation protects existing user content and symlinks.
    created = False
    try:
        with fragment.open("x", encoding="utf-8", newline="\n") as handle:
            created = True
            handle.write(FRAGMENT)
        fragment.chmod(0o644)
        checked()
        if checked("-o", "ip-freebind") != "yes":
            raise RuntimeError("Unbound did not enable ip-freebind; DNS migration not applied")
    except BaseException:
        if created:
            fragment.unlink()
        raise
    return True


if __name__ == "__main__":
    import sys

    try:
        if migrate():
            print("Legacy DNS: boot ordering fixed; running services were not restarted.")
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Legacy DNS migration: {error}", file=sys.stderr)
        sys.exit(1)
