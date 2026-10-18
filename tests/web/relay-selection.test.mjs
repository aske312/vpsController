import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import reactPlugin from "@vitejs/plugin-react";

const loader = await createServer({ configFile: false, plugins: [reactPlugin()], server: { middlewareMode: true, hmr: false }, appType: "custom", optimizeDeps: { noDiscovery: true } });
after(() => loader.close());
const { RelayEndpointSelect } = await loader.ssrLoadModule("/src/features/network/relay-endpoint-select.tsx");
const host = { id: "b".repeat(32), name: "Amsterdam", client_address: "relay.example", agent_url: "https://85.209.155.229:9443", state: "unknown", routes: [] };

test("saved Relay addresses remain selectable regardless of liveness and pending diagnostics", () => {
  for (const state of ["unknown", "connected"]) for (const pending of [false, true]) {
    const html = renderToStaticMarkup(createElement(RelayEndpointSelect, {
      request: async () => { throw Error("Selection must use the screen inventory"); },
      inventory: { status: { hosts: [{ ...host, state }], pending }, failed: false },
      value: host.id, onChange: () => {},
    }));
    assert.doesNotMatch(html, /disabled=/);
    assert.match(html, /value="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" selected=""/);
    assert.match(html, /relay\.example/);
  }
});

test("an explicit editing lock still disables Relay selection", () => {
  const html = renderToStaticMarkup(createElement(RelayEndpointSelect, {
    request: async () => ({}), inventory: { status: { hosts: [host] }, failed: false },
    value: host.id, onChange: () => {}, disabled: true,
  }));
  assert.match(html, /<select[^>]*disabled=""/);
});
