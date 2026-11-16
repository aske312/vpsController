import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createElement, Children, isValidElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import react from "@vitejs/plugin-react";
import { createTestLoader } from "./vite-test-loader.mjs";

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { AdvancedRouting, ProfilePolicyGroups } = await loader.ssrLoadModule("/src/features/mihomo/advanced-routing.tsx");
function nodes(node, predicate) {
  if (!isValidElement(node)) return [];
  return [...(predicate(node) ? [node] : []), ...Children.toArray(node.props.children).flatMap(child => nodes(child, predicate))];
}
test("structured constructor preserves rule order and changes targets without raw YAML", () => {
  const value = { groups: [{ name: "Media", type: "fallback", members: ["*"], strategy: "consistent-hashing" }], rules: [
    { kind: "domain", value: "first.test", target: "Media" }, { kind: "ip-cidr", value: "2001:db8::/32", target: "REJECT" }] };
  let next;
  const tree = AdvancedRouting({ value: JSON.stringify(value), onChange: raw => { next = JSON.parse(raw); } });
  const down = nodes(tree, node => node.props["aria-label"] === "Опустить правило")[0];
  down.props.onClick();
  assert.equal(next.rules[0].value, "2001:db8::/32");
  const name = nodes(tree, node => node.type === "input" && node.props.value === "Media")[0];
  name.props.onChange({ target: { value: "Video" } });
  assert.equal(next.rules[0].target, "Video");
  assert.deepEqual(next.groups[0].members, ["*"]);
  const html = renderToStaticMarkup(createElement(AdvancedRouting, { value: JSON.stringify(value), onChange() {} }));
  assert.doesNotMatch(html, /<textarea/);
  assert.match(html, /role="switch"/);
});
test("fallback member order remains explicit and connection IDs survive display labels", () => {
  let next;
  const tree = AdvancedRouting({ value: JSON.stringify({ rules: [], groups: [{ name: "Reserve", type: "fallback", members: ["connection:a", "connection:b"], strategy: "consistent-hashing" }] }),
    connections: [{ id: "a", name: "First", component: "transport-shadowsocks" }, { id: "b", name: "Second", component: "transport-hysteria2" }], onChange: raw => { next = JSON.parse(raw); } });
  nodes(tree, node => node.props["aria-label"] === "Повысить приоритет участника")[1].props.onClick();
  assert.deepEqual(next.groups[0].members, ["connection:b", "connection:a"]);
});

test("a referenced group cannot be deleted until rules and parent groups release it", () => {
  const group = name => ({ name, type: "fallback", members: ["*"], strategy: "consistent-hashing" });
  for (const policy of [
    { groups: [group("Media")], rules: [{ kind: "domain", value: "example.com", target: "Media" }] },
    { groups: [group("Media"), { ...group("Parent"), members: ["group:Media"] }], rules: [] },
  ]) {
    let next;
    const tree = AdvancedRouting({ value: JSON.stringify(policy), onChange: raw => { next = JSON.parse(raw); } });
    const remove = nodes(tree, node => node.type === "button" && node.props.children === "Удалить группу")[0];
    assert.equal(remove.props.disabled, true);
    remove.props.onClick();
    assert.equal(next, undefined);
  }
  let next;
  const tree = AdvancedRouting({ value: JSON.stringify({ groups: [group("Unused")], rules: [] }), onChange: raw => { next = JSON.parse(raw); } });
  nodes(tree, node => node.type === "button" && node.props.children === "Удалить группу")[0].props.onClick();
  assert.deepEqual(next, { groups: [], rules: [] });
});

test("group rename updates references in parent groups as well as routing rules", () => {
  let next;
  const tree = AdvancedRouting({ value: JSON.stringify({ groups: [
    { name: "Media", type: "fallback", members: ["*"], strategy: "consistent-hashing" },
    { name: "Parent", type: "select", members: ["group:Media"], strategy: "consistent-hashing" },
  ], rules: [{ kind: "domain", value: "example.com", target: "Media" }] }), onChange: raw => { next = JSON.parse(raw); } });
  nodes(tree, node => node.type === "input" && node.props.value === "Media")[0].props.onChange({ target: { value: "Video" } });
  assert.equal(next.rules[0].target, "Video");
  assert.equal(next.groups[0].id, "Media");
  assert.deepEqual(next.groups[1].members, ["group:Video"]);
});

test("profile cards toggle one reference without copying or editing definitions", () => {
  const value = JSON.stringify({ groups: [{ id: "media-id", name: "Media", type: "fallback", members: ["*"], strategy: "consistent-hashing" }],
    rules: [{ kind: "domain", value: "media.test", target: "Media" }, { kind: "domain", value: "direct.test", target: "DIRECT" }] });
  let next, edited = 0;
  const props = { value, selection: '{"media-id":false,"@rules":false}', ownSelection: '{}',
    onChange: raw => { next = JSON.parse(raw); }, onReset() {}, onEdit() { edited++; }, onUseShared() {} };
  const tree = ProfilePolicyGroups(props);
  const cards = nodes(tree, node => node.type === "button" && typeof node.props["aria-pressed"] === "boolean");
  assert.equal(cards.length, 2);
  assert.equal(cards[1].props["aria-pressed"], false);
  cards[1].props.onClick();
  assert.deepEqual(next, { "media-id": true });
  nodes(tree, node => node.type === "button" && node.props.children === "Настроить на странице «Правила»")[0].props.onClick();
  assert.equal(edited, 1);
  const html = renderToStaticMarkup(createElement(ProfilePolicyGroups, props));
  assert.doesNotMatch(html, /<(input|select|textarea)\b/);
  assert.doesNotMatch(html, /media\.test|direct\.test/);
  assert.match(html, /Media/);
});

test("group health fields edit overrides and can restore inheritance without changing members", () => {
  const value = JSON.stringify({ groups: [{ id: "media", name: "Media", type: "url-test", members: ["*"], strategy: "consistent-hashing", timeout: 5000, tolerance: 0 }], rules: [] });
  let next;
  const tree = AdvancedRouting({ value, defaults: { interval: 60, health_timeout: 3000 }, onChange: raw => { next = JSON.parse(raw); } });
  const timeout = nodes(tree, node => node.type === "input" && node.props.type === "number" && node.props.value === 5000)[0];
  timeout.props.onChange({ target: { value: "7000" } });
  assert.equal(next.groups[0].timeout, 7000);
  nodes(tree, node => node.type === "button" && node.props.children === "Наследовать общие параметры проверки")[0].props.onClick();
  assert.equal(next.groups[0].timeout, undefined);
  assert.equal(next.groups[0].tolerance, undefined);
  assert.deepEqual(next.groups[0].members, ["*"]);
  assert.equal(next.groups[0].id, "media");
});
