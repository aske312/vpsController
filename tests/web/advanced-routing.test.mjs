import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createElement, Children, isValidElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import react from "@vitejs/plugin-react";
import { createTestLoader } from "./vite-test-loader.mjs";

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { AdvancedRouting } = await loader.ssrLoadModule("/src/features/mihomo/advanced-routing.tsx");
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
