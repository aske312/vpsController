import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

const loader = await createServer({ configFile: false, cacheDir: ".runtime/vite-auth-login-tests", plugins: [react()], server: { middlewareMode: true, hmr: false }, appType: "custom", optimizeDeps: { noDiscovery: true } });
after(() => loader.close());
const { LoginView } = await loader.ssrLoadModule("/src/features/auth/login-view.tsx");

test("server-rendered login cannot submit or accept input before its handlers are ready", () => {
  const html = renderToStaticMarkup(createElement(LoginView, {
    loginUser: "admin", loginPassword: "", loginPasswordVisible: false,
    busy: false, version: "test", channel: "test", commit: "fixture",
    setLoginUser() {}, setLoginPassword() {}, setLoginPasswordVisible() {}, login() {},
  }));
  const inputs = html.match(/<input\b[^>]*>/g);
  assert.equal(inputs.length, 2);
  for (const input of inputs) assert.match(input, /disabled=""/);
  assert.match(html, /<button[^>]*type="submit"[^>]*disabled=""/);
  assert.match(html, /<form[^>]*aria-busy="true"/);
});
