import assert from "node:assert/strict";
import test from "node:test";
import { clientExport } from "../../src/shared/lib/client-export.ts";

test("native connections keep a valid configuration and optional route exports", () => {
  const result = { config: "wireguard-config", filename: "phone.conf", profiles: [{ id: "cdn", name: "CDN", filename: "phone-cdn.txt", config: "vless://fixture" }] };
  assert.equal(clientExport(result), result);
});

test("incomplete or unsafe export responses cannot produce a configuration download", () => {
  for (const result of [{}, { config: "" }, { config: {} }, { config: "ok", filename: "../phone.conf" }, { config: "ok", profiles: {} }, { config: "ok", profiles: [{ id: "cdn", name: "CDN", filename: "phone.conf" }] }, { config: "ok", profiles: Array(2).fill({ id: "same", name: "CDN", filename: "phone.conf", config: "ok" }) }]) {
    assert.throws(() => clientExport(result), /Обновите список подключений/);
  }
});
