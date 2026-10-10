import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import test from "node:test";

test("Relay Agent: authorization, revisions, persistence, TCP/UDP and TLS pinning", () => {
  const result = spawnSync(process.platform === "win32" ? "python" : "python3", [fileURLToPath(new URL("relay_agent_test.py", import.meta.url))], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
