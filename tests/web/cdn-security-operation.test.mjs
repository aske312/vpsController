import assert from "node:assert/strict";
import { test, afterEach } from "node:test";
import { createApiClient } from "../../src/shared/lib/api-request.ts";
import { submitCdnSecurity, followCdnSecurity, CdnResultUnknown } from "../../src/shared/lib/cdn-security-operation.ts";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const request = () => createApiClient("test", { retryDelaysMs: [] });
const initial = { id: "a".repeat(32), enabled: true, state: "queued", progress: 0, message: "Starting" };
const result = (state, enabled = true) => ({ authenticated_origin_pulls: enabled, operation: { ...initial, state, progress: state === "succeeded" ? 100 : 45 } });

test("lost acknowledgement is reconciled by ID without repeating the command", async () => {
  const calls = [];
  globalThis.fetch = async (path, init) => {
    calls.push(init.method);
    if (init.method === "PUT") {
      assert.equal(JSON.parse(init.body).operation_id, initial.id);
      throw new TypeError("disconnected during reload");
    }
    assert.match(path, new RegExp(`operation_id=${initial.id}$`));
    if (calls.length === 2) throw new TypeError("reconnecting");
    return Response.json(result(calls.length === 3 ? "running" : "succeeded"));
  };
  const progress = [];
  const final = await submitCdnSecurity(request(), initial, (value) => progress.push(value), { pollDelayMs: 0 });
  assert.equal(final.operation.state, "succeeded");
  assert.deepEqual(calls, ["PUT", "GET", "GET", "GET"]);
  assert.ok(progress.some((value) => /связ/.test(value.message || "")));
  assert.ok(progress.every((value) => value.state !== "failed"));
});

test("an unchanged setting is not treated as successful until this command finishes", async () => {
  let calls = 0;
  globalThis.fetch = async () => Response.json(result(++calls < 3 ? "running" : "failed"));
  const final = await submitCdnSecurity(request(), initial, () => {}, { pollDelayMs: 0 });
  assert.equal(calls, 3);
  assert.equal(final.operation.state, "failed");
});

test("rejected commands report their error without polling or replaying", async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; return Response.json({ detail: "Missing certificate" }, { status: 409 }); };
  await assert.rejects(submitCdnSecurity(request(), initial, () => {}), { status: 409, message: "Missing certificate" });
  assert.equal(calls, 1);
});

test("resuming an operation only reads its result", async () => {
  globalThis.fetch = async (_path, init) => { assert.equal(init.method, "GET"); return Response.json(result("succeeded")); };
  assert.equal((await followCdnSecurity(request(), initial, () => {})).operation.state, "succeeded");
});

test("another operation cannot accidentally confirm this command", async () => {
  globalThis.fetch = async () => Response.json({ ...result("succeeded"), operation: { ...initial, id: "b".repeat(32), state: "succeeded" } });
  await assert.rejects(followCdnSecurity(request(), initial, () => {}), CdnResultUnknown);
});

test("a missing record immediately after launch is allowed to become visible", async () => {
  let calls = 0;
  globalThis.fetch = async () => ++calls === 1 ? Response.json({ detail: "Not found" }, { status: 404 }) : Response.json(result("succeeded"));
  assert.equal((await followCdnSecurity(request(), initial, () => {}, { pollDelayMs: 0 })).operation.state, "succeeded");
});

test("a prolonged outage stays unconfirmed rather than reporting success or replaying", async () => {
  globalThis.fetch = async () => { throw new TypeError("offline"); };
  await assert.rejects(followCdnSecurity(request(), initial, () => {}, { pollDelayMs: 1, timeoutMs: 5 }), CdnResultUnknown);
});

test("logout cancels polling", async () => {
  const controller = new AbortController();
  globalThis.fetch = async () => Response.json(result("running"));
  const pending = followCdnSecurity(request(), initial, () => controller.abort(), { signal: controller.signal, pollDelayMs: 1000 });
  await assert.rejects(pending, { name: "AbortError" });
});
