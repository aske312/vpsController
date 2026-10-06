import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const bash = process.env.BASH_BIN || (process.platform === "win32"
  ? resolve(execFileSync("git", ["--exec-path"], { encoding: "utf8" }).trim(), "../../../bin/bash.exe")
  : "bash");
const manager = readFileSync(new URL("../scripts/vps-control.sh", import.meta.url), "utf8");
const bootstrap = readFileSync(new URL("../scripts/install-panel.sh", import.meta.url), "utf8");
const amnezia = readFileSync(new URL("../protocol-images/amneziawg/install.sh", import.meta.url), "utf8");
const checks = [
  ["manager", manager.match(/check_os\(\) \{([\s\S]*?)\n\}/)[1]],
  ["bootstrap", bootstrap.slice(bootstrap.indexOf('ID="" VERSION_ID='), bootstrap.indexOf("export DEBIAN_FRONTEND"))],
  ["amneziawg", amnezia.slice(amnezia.indexOf('ID="" VERSION_ID='), amnezia.indexOf("export DEBIAN_FRONTEND"))],
];

for (const [entry, check] of checks) {
  test(`${entry}: OS detection warns without rejecting unknown distributions`, () => {
    for (const [release, warning] of [
      ['ID=debian\nVERSION_ID="13"', false],
      ['ID=debian\nVERSION_ID="12"', true],
      ['ID=ubuntu\nVERSION_ID="26.04"', true],
      ['ID="ubuntu"\nVERSION_ID="24.04"', false],
      ['ID=ubuntu\nVERSION_ID="22.04"', false],
      ['ID=ubuntu\nVERSION_ID="99"', true],
      ['ID=custom\nPRETTY_NAME="Custom Linux"', true],
      ['ID=debian', true],
      ['', true],
      [null, true],
    ]) {
      const setup = release === null ? 'rm -f "$release"' : `cat >"$release" <<'OS_RELEASE'\n${release}\nOS_RELEASE`;
      const script = `set -Eeuo pipefail
release=$(mktemp)
trap 'rm -f "$release"' EXIT
${setup}
warn() { printf 'WARNING: %s\\n' "$*"; }
apt-get() { :; }
dpkg() { :; }
check() {
${check.replaceAll("/etc/os-release", '"$release"')}
}

check
printf 'CONTINUED\\n'
`;
      const result = spawnSync(bash, ["-c", script], { encoding: "utf8" });
      assert.equal(result.status, 0, result.stderr);
      assert.match(result.stdout, /CONTINUED/);
      assert.equal(/WARNING:|Предупреждение:/.test(result.stdout + result.stderr), warning, String(release));
    }
  });
}

test("kernel update keeps the installed Debian/Ubuntu kernel flavor", () => {
  const body = manager.match(/installed_kernel_packages\(\) \{([\s\S]*?)\n\}/)[1];
  for (const [packages, expected] of [
    ["linux-image-amd64 ii \nlinux-headers-amd64 ii \nlinux-image-6.12.85+deb13-amd64 ii ", ["linux-image-amd64", "linux-headers-amd64"]],
    ["linux-image-cloud-arm64 ii \nlinux-headers-cloud-arm64 ii ", ["linux-image-cloud-arm64", "linux-headers-cloud-arm64"]],
    ["linux-generic-hwe-22.04 ii \nlinux-image-generic-hwe-22.04 ii \nlinux-headers-generic-hwe-22.04 ii ", ["linux-generic-hwe-22.04", "linux-image-generic-hwe-22.04", "linux-headers-generic-hwe-22.04"]],
    ["linux-virtual ii \nlinux-generic rc \nlinux-image-6.8.0-1-generic ii ", ["linux-virtual"]],
    ["linux-image-custom ii \nlinux-libc-dev:amd64 ii ", []],
  ]) {
    const script = `set -Eeuo pipefail\ndpkg-query() { cat <<'PACKAGES'\n${packages}\nPACKAGES\n}\n${body}`;
    const result = spawnSync(bash, ["-c", script], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(result.stdout.trim().split("\n").filter(Boolean), expected);
  }
});

test("Node runtime minimum matches the application requirement", () => {
  const expression = manager.match(/node_runtime_supported\(\) \{\s+node -e '([^']+)'/)[1];
  for (const [version, supported] of [["20.20.0", false], ["22.12.0", false], ["22.13.0", true], ["22.23.3", true], ["24.0.0", true]]) {
    const result = spawnSync(process.execPath, ["-e", expression.replace("process.versions.node", JSON.stringify(version))]);
    assert.equal(result.status, supported ? 0 : 1, version);
  }
});

test("SSH management starts the service directly and stops an installed socket", () => {
  const body = manager.match(/ssh_units_action\(\) \{([\s\S]*?)\n\}/)[1];
  for (const state of ["loaded", "not-found", ""]) {
    for (const action of ["start", "stop"]) {
    const script = `set -Eeuo pipefail
systemctl() {
  if [[ "$1" == show ]]; then printf '%s' '${state}'; else printf '%s\\n' "$*"; fi
}
ssh_units_action() {${body}
}
ssh_units_action ${action}
`;
    const result = spawnSync(bash, ["-c", script], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    const expected = action === "start"
      ? "start ssh.service"
      : state === "loaded" ? "stop ssh.socket ssh.service" : "stop ssh.service";
    assert.equal(result.stdout.trim(), expected);
    }
  }
});

test("kernel update selects a distribution meta-package when one is missing", () => {
  const body = manager.match(/fallback_kernel_package\(\) \{([\s\S]*?)\n\}/)[1]
    .replaceAll("/etc/os-release", '"$release"');
  for (const [osRelease, kernel, architecture, expected] of [
    ["ID=debian", "6.12.111+deb13-amd64", "amd64", "linux-image-amd64"],
    ["ID=debian", "6.12.0-1-cloud-amd64", "amd64", "linux-image-cloud-amd64"],
    ["ID=ubuntu", "6.8.0-1018-azure", "amd64", "linux-azure"],
    ["ID=ubuntu", "6.8.0-90-generic", "amd64", "linux-generic"],
  ]) {
    const script = `set -Eeuo pipefail
release=$(mktemp)
trap 'rm -f "$release"' EXIT
printf '%s\\n' '${osRelease}' >"$release"
dpkg() { printf '%s\\n' '${architecture}'; }
uname() { printf '%s\\n' '${kernel}'; }
die() { printf '%s\\n' "$*" >&2; exit 1; }
fallback_kernel_package() {${body}
}
fallback_kernel_package
`;
    const result = spawnSync(bash, ["-c", script], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), expected);
  }
});

test("kernel update adds matching headers for every registered DKMS module", () => {
  const body = manager.match(/kernel_update_packages\(\) \{([\s\S]*?)\n\}/)[1];
  for (const [dkmsOutput, expected] of [
    ["amneziawg", ["linux-image-amd64", "linux-headers-amd64"]],
    ["", ["linux-image-amd64"]],
  ]) {
    const script = `set -Eeuo pipefail
installed_kernel_packages() { printf '%s\\n' linux-image-amd64; }
fallback_kernel_package() { printf '%s\\n' linux-image-amd64; }
registered_dkms_modules() { printf '%s\\n' '${dkmsOutput}'; }
apt-cache() { return 0; }
kernel_update_packages() {${body}
}
kernel_update_packages
`;
    const result = spawnSync(bash, ["-c", script], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(result.stdout.trim().split("\n").filter(Boolean), expected);
  }
});
