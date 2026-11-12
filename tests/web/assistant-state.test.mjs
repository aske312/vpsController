import assert from "node:assert/strict";
import test from "node:test";
import { changed, chatModels, makeDraft, moduleState, settingsPayload, enabledTelegramTools } from "../../src/features/assistant/assistant-state.ts";

const settings = {
  revision: "a".repeat(32), modules: { "ai-agent": true, "nodera-bot": false },
  mcp: { "telegram-mcp": true, "vps-controller-mcp": false }, provider_url: "https://provider.example/v1", model: "model",
  telegram_api_id: 1, api_key_configured: true, bot_token_configured: false, telegram_api_hash_configured: true,
  accounts: [{ telegram_id: 11, name: "One", bot_access: true, telegram_access: true, vps_access: false,
    session_configured: true, excluded_dialogs: [-1001] }],
};

test("Telegram tools remain opt-in, explicit denial persists, and target editing survives save", () => {
  const draft = makeDraft(settings);
  assert.equal(enabledTelegramTools(draft.accounts[0]).length, 5);
  draft.accounts[0].telegram_tools = [];
  assert.deepEqual(enabledTelegramTools(draft.accounts[0]), []);
  assert.deepEqual(settingsPayload(draft).accounts[0].telegram_tools, []);
  draft.accounts[0].telegram_tools = ['tg_send_message', 'tg_get_contacts'];
  draft.accounts[0].telegram_action_targets_text = '42, @example';
  assert.deepEqual(settingsPayload(draft).accounts[0].telegram_action_targets, ['42', '@example']);
  assert.deepEqual(settingsPayload(draft).accounts[0].telegram_tools, ['tg_send_message', 'tg_get_contacts']);
});

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

test("a saved default model remains usable when discovery is unavailable", () => {
  assert.deepEqual(chatModels({...settings, model_mode: 'catalog'}, []), ['model']);
  assert.deepEqual(chatModels({...settings, model_mode: 'catalog'}, ['other']), ['model', 'other']);
});

test('legacy settings remain single; catalog mode exposes the provider catalog without a configured model', () => {
  assert.equal(makeDraft(settings).model_mode, 'single');
  assert.equal(changed(makeDraft(settings), settings), false);
  assert.deepEqual(chatModels(settings, ['other-model']), ['model']);
  assert.deepEqual(chatModels({ ...settings, model: '', model_mode: 'catalog' }, ['other-model', 'model', 'model']), ['other-model', 'model']);
});

test('MCP migration preserves the old subscription access while explicit scopes toggle independently', () => {
  const legacy = { ...settings, mcp: { 'telegram-mcp': false, 'vps-controller-mcp': true }, mcp_installed: ['vps-controller-mcp'] };
  const migrated = makeDraft(legacy);
  assert.equal(migrated.mcp['mihomo-mcp'], true);
  assert.equal(migrated.mcp_installed.includes('mihomo-mcp'), true);
  assert.equal(changed(migrated, legacy), false);
  const split = { ...legacy, mcp: { ...legacy.mcp, 'mihomo-mcp': false } };
  const draft = makeDraft(split);
  assert.equal(draft.mcp['mihomo-mcp'], false);
  draft.mcp['vps-controller-mcp'] = false;
  assert.equal(settingsPayload(draft).mcp['mihomo-mcp'], false);
  draft.mcp['mihomo-mcp'] = true;
  assert.equal(settingsPayload(draft).mcp['vps-controller-mcp'], false);
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
