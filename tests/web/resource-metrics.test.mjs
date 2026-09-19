import assert from "node:assert/strict";
import test from "node:test";
import { appendResources, graphSegments, networkSample, usedPercent } from "../../src/shared/lib/resource-metrics.ts";

test("first sample, missing data, counter reset and polling gaps are not zero traffic", () => {
  const resources = {network_rx: 10, network_tx: 20};
  const first = networkSample(resources, null, 1000);
  assert.deepEqual(first.rate, {rx: null, tx: null});
  assert.deepEqual(networkSample({network_rx: 20, network_tx: 40}, first.sample, 2000).rate, {rx: 10, tx: 20});
  assert.deepEqual(networkSample(resources, first.sample, 2000).rate, {rx: 0, tx: 0});
  assert.equal(networkSample({network_rx: 1, network_tx: 1}, first.sample, 2000).rate.rx, null);
  assert.equal(networkSample(resources, first.sample, 32000).rate.rx, null);
  assert.equal(networkSample({network_rx: null, network_tx: 20}, first.sample, 2000).sample, null);
});

test("unknown metrics remain gaps and zero remains a real sample", () => {
  const empty = {load: [], memory: [], disk: [], rx: [], tx: []};
  const resources = {cpu_percent: null, memory_total: null, memory_available: null, disk_total: 100, disk_available: 100};
  const history = appendResources(empty, resources, {rx: null, tx: 0}, 48);
  assert.deepEqual(history, {load: [null], memory: [null], disk: [0], rx: [null], tx: [0]});
  assert.equal(usedPercent(0, 0), null);
  assert.equal(usedPercent(10, 11), null);
  assert.equal(usedPercent(10, 0), 100);
});

test("charts do not draw a zero baseline for missing data or connect across gaps", () => {
  assert.deepEqual(graphSegments([], 100), []);
  assert.deepEqual(graphSegments([null, null], 100), []);
  assert.deepEqual(graphSegments([0, 10, null, 20, 30], 100), ["0.00,50.00 25.00,45.00", "75.00,40.00 100.00,35.00"]);
});
