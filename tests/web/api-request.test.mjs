import assert from "node:assert/strict";
import { test, afterEach } from "node:test";
import { createApiClient } from "../../src/shared/lib/api-request.ts";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const client = (options = {}) => createApiClient("test-token", { retryDelaysMs: [0, 0], ...options });
const ok = () => new Response('{"ok":true}', { headers: { "content-type": "application/json" } });

test("validation details remain HTTP errors with a text formatter", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    return Response.json({ detail: [{ msg: "Field required", input: "private-value" }] }, { status: 422 });
  };
  await assert.rejects(client({ formatHttpError: (detail) => detail.trim() })("/settings"), {
    kind: "http", status: 422, message: "Field required",
  });
  assert.equal(calls, 1);
});

test("HEAD succeeds without parsing an absent JSON body", async () => {
  globalThis.fetch = async () => new Response(null, { status: 200 });
  assert.equal(await client()("/health", { method: "HEAD" }), null);
});

test("read recovers from a dropped connection and a gateway failure", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    if (calls === 1) throw new TypeError("Failed to fetch");
    return calls === 2 ? new Response("gateway", { status: 502 }) : ok();
  };
  assert.deepEqual(await client()("/overview"), { ok: true });
  assert.equal(calls, 3);
});

test("read retries when the response body drops after successful headers", async () => {
  let calls = 0;
  globalThis.fetch = async () => ++calls === 1
    ? { ok: true, status: 200, text: async () => { throw new TypeError("terminated"); } }
    : ok();
  assert.deepEqual(await client()("/overview"), { ok: true });
  assert.equal(calls, 2);
});

test("truncated JSON is retried, plain text and empty responses are accepted", async () => {
  const responses = [new Response('{"ok":', { headers: { "content-type": "application/json" } }), ok(), new Response("config", { headers: { "content-type": "text/plain" } }), new Response(null, { status: 204 })];
  globalThis.fetch = async () => responses.shift();
  const request = client();
  assert.deepEqual(await request("/overview"), { ok: true });
  assert.equal(await request("/config"), "config");
  assert.equal(await request("/empty"), null);
});

test("authentication and application errors are not retried", async () => {
  for (const status of [401, 403, 422, 500]) {
    let calls = 0;
    globalThis.fetch = async () => { calls++; return new Response('{"detail":"denied"}', { status }); };
    await assert.rejects(client()("/overview"), { status, message: "denied" });
    assert.equal(calls, 1);
  }
});

test("a disconnected write is never replayed and its result is marked unknown", async () => {
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    let calls = 0;
    globalThis.fetch = async () => { calls++; throw new TypeError("Failed to fetch"); };
    await assert.rejects(client()("/settings", { method, body: "{}" }), /Результат команды неизвестен/);
    assert.equal(calls, 1);
  }
});

test("exhausted retries include endpoint but redact query and subscription token", async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; throw new TypeError("secret internals"); };
  await assert.rejects(client()("/mihomo/subscriptions/private-token?key=secret"), (error) => {
    assert.equal(error.kind, "network");
    assert.match(error.message, /mihomo\/subscription/);
    assert.doesNotMatch(error.message, /private-token|secret|Failed to fetch/);
    return true;
  });
  assert.equal(calls, 3);
});

test("concurrent reads coalesce, later reads and different sessions fetch fresh data", async () => {
  let calls = 0;
  globalThis.fetch = async (_path, init) => { calls++; assert.equal(init.cache, "no-store"); return ok(); };
  const request = client();
  await Promise.all([request("/overview"), request("/overview")]);
  assert.equal(calls, 1);
  await request("/overview");
  await createApiClient("another-token")("/overview");
  assert.equal(calls, 3);
});

test("read timeout is bounded and external cancellation is not retried", async () => {
  let calls = 0;
  globalThis.fetch = async (_path, init) => {
    calls++;
    return new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true }));
  };
  await assert.rejects(client({ readTimeoutMs: 5 })("/overview"), { kind: "network" });
  assert.equal(calls, 3);
  calls = 0;
  const controller = new AbortController();
  const result = client()("/overview", { signal: controller.signal });
  controller.abort();
  await assert.rejects(result, { name: "AbortError" });
  assert.equal(calls, 1);
});

test("refresh after a write cannot join a read from before the write", async () => {
  let releaseOld;
  let reads = 0;
  globalThis.fetch = async (_path, init) => {
    if (init.method === "POST") return ok();
    reads++;
    if (reads === 1) return new Promise((resolve) => { releaseOld = () => resolve(ok()); });
    return ok();
  };
  const request = client();
  const old = request("/settings");
  await request("/settings", { method: "POST", body: "{}" });
  await request("/settings");
  assert.equal(reads, 2);
  releaseOld();
  await old;
});
