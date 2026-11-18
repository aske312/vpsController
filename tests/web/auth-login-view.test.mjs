import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createTestLoader } from "./vite-test-loader.mjs";
import react from "@vitejs/plugin-react";

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { LoginView } = await loader.ssrLoadModule("/src/features/auth/login-view.tsx");

function renderLogin(domain) {
  return renderToStaticMarkup(createElement(LoginView, {
    domain,
    loginUser: "admin", loginPassword: "", loginPasswordVisible: false,
    busy: false, version: "test", channel: "test", commit: "fixture",
    setLoginUser() {}, setLoginPassword() {}, setLoginPasswordVisible() {}, login() {},
  }));
}

test("server-rendered login cannot submit or accept input before its handlers are ready", () => {
  const html = renderLogin();
  const inputs = html.match(/<input\b[^>]*>/g);
  assert.equal(inputs.length, 2);
  for (const input of inputs) assert.match(input, /disabled=""/);
  assert.match(html, /<button[^>]*type="submit"[^>]*disabled=""/);
  assert.match(html, /<form[^>]*aria-busy="true"/);
});

test("login branding highlights the configured domain suffix or falls back to 312.net", () => {
  assert.match(renderLogin(" panel.example.com "), /<strong title="panel\.example\.com">panel\.example<em>\.com<\/em><\/strong>/);
  assert.match(renderLogin("  "), /<strong title="312\.net">312<em>\.net<\/em><\/strong>/);
});
