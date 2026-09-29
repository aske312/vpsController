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
const checks = [
  ["manager", manager.match(/check_os\(\) \{([\s\S]*?)\n\}/)[1]],
  ["bootstrap", bootstrap.slice(bootstrap.indexOf('ID="" VERSION_ID='), bootstrap.indexOf("export DEBIAN_FRONTEND"))],
];

for (const [entry, check] of checks) {
  test(`${entry}: OS detection warns without rejecting unknown distributions`, () => {
    for (const [release, warning] of [
      ['ID=debian\nVERSION_ID="13"', false],
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
