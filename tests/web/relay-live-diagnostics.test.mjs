import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createTestLoader } from "./vite-test-loader.mjs";

const loader = await createTestLoader(import.meta.url);
after(() => loader.close());
const { RelayLiveDiagnostics } = await loader.ssrLoadModule('/src/features/network/relay-live-diagnostics.tsx');
const route = { transport: "udp", listen_port: 20001, sessions: 300, errors: 900,
  diagnostics: { no_return_sessions: 5, session_budget_used: 350, session_budget_limit: 4096,
    udp_receive_budget_used_bytes: 128 * 1048576, udp_receive_budget_bytes: 512 * 1048576,
    interval: { seconds:60, errors:0, dropped_packets:0, new_sessions:123, error_counts:{} } } };

test('card separates minute errors from old totals and displays shared resource pressure', () => {
  const html = renderToStaticMarkup(createElement(RelayLiveDiagnostics, { route, stale: false }));
  assert.match(html, /За последние 60 с/);
  assert.match(html, /Ошибок: 0/);
  assert.doesNotMatch(html, /Ошибок: 900/);
  assert.match(html, /Новых сессий: 123/);
  assert.match(html, /25%/);
  assert.match(html, /Без ответа более 5 с: 5/);
});

test('unavailable and legacy observations never become healthy zero-minute statistics', () => {
  const stale = renderToStaticMarkup(createElement(RelayLiveDiagnostics, { route, stale: true }));
  assert.match(stale, /недоступна/);
  assert.doesNotMatch(stale, /Ошибок: 0|25%/);
  const legacy = renderToStaticMarkup(createElement(RelayLiveDiagnostics, { route: { ...route, diagnostics: {} }, stale: false }));
  assert.match(legacy, /после обновления Agent/);
  assert.doesNotMatch(legacy, /Ошибок: 0|За последние 60 с/);
});
