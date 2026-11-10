import assert from "node:assert/strict";
import test from "node:test";
import { changed, makeDraft, moduleState, settingsPayload } from "../../src/features/assistant/assistant-state.ts";

const settings = {
  revision: "a".repeat(32), modules: { "ai-agent": true, "nodera-bot": false },
  mcp: { "telegram-mcp": true, "vps-controller-mcp": false }, provider_url: "https://provider.example/v1", model: "model",
  telegram_api_id: 1, api_key_configured: true, bot_token_configured: false, telegram_api_hash_configured: true,
  accounts: [{ telegram_id: 11, name: "One", bot_access: true, telegram_access: true, vps_access: false,
    session_configured: true, excluded_dialogs: [-1001] }],
};

test("drafts preserve saved secrets without reading them back and detect actual changes", () => {
  const draft = makeDraft(settings);
  assert.equal(changed(draft, settings), false);
  const payload = settingsPayload(draft);
  assert.equal(payload.api_key, null);
  assert.equal(payload.accounts[0].session, null);
  assert.equal("session_configured" in payload.accounts[0], false);
  draft.accounts[0].excluded_dialogs_text = "-1001, -1002";
  assert.equal(changed(draft, settings), true);
  assert.deepEqual(settingsPayload(draft).accounts[0].excluded_dialogs, [-1001, -1002]);
  draft.api_key = "";
  assert.equal(settingsPayload(draft).api_key, "");
});

test("configured credentials do not claim that the provider or an on-demand MCP worker is running", () => {
  const state = { settings, modules: [{ id: "ai-agent", enabled: true, configured: true, installed: false }],
    mcp_modules: [{ id: "telegram-mcp", enabled: true, configured: true }], bot: {}, runtime_installed: false };
  assert.equal(moduleState(state, "ai-agent"), "Компонент не установлен");
  state.modules[0].installed = true;
  assert.equal(moduleState(state, "ai-agent"), "Запуск по запросу");
  assert.equal(moduleState(state, "telegram-mcp"), "Запуск по запросу");
  state.modules.push({ id: "nodera-bot", enabled: true, configured: true, installed: true });
  assert.equal(moduleState(state, "nodera-bot"), "Состояние службы неизвестно");
});
