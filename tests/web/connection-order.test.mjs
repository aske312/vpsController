import assert from "node:assert/strict";
import test from "node:test";
import { reorderDeviceConnections } from "../../src/features/mihomo/connection-order.ts";

const connection = (id, device_id = "phone") => ({ id, device_id, name: id, component: "transport-wg", settings: { mtu: 1420 }, credential: { port: 1234 } });

test("priority moves in both directions, keeping other devices in their slots and connection data intact", () => {
  const source = [connection("a"), connection("other", "laptop"), connection("b"), connection("c")];
  for (const [from, to, expected] of [["a", "c", ["b", "other", "c", "a"]], ["c", "a", ["c", "other", "a", "b"]], ["a", "b", ["b", "other", "a", "c"]]]) {
    const moved = reorderDeviceConnections(source, from, to);
    assert.deepEqual(moved.map((item) => item.id), expected);
    for (const item of moved) assert.equal(item, source.find((original) => original.id === item.id));
    assert.deepEqual(source.map((item) => item.id), ["a", "other", "b", "c"]);
    assert.deepEqual(reorderDeviceConnections(moved, from, from), moved);
  }
});

test("invalid and cross-device moves leave the draft untouched", () => {
  const source = [connection("a"), connection("other", "laptop")];
  for (const [from, to] of [["a", "other"], ["other", "a"], ["missing", "a"], ["a", "missing"], ["a", "a"]]) {
    assert.equal(reorderDeviceConnections(source, from, to), source);
  }
});
