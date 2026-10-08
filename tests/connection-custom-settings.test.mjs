import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

const require = createRequire(import.meta.url);
function dialogHarness(protocol) {
  const states = [];
  let cursor = 0;
  const react = {
    useEffect() {},
    useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
      return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
    },
  };
  const source = readFileSync(new URL("../app/connection-dialog.tsx", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const loaded = { exports: {} };
  const delivery = Object.fromEntries(["awg", "hysteria2", "tuic", "xray"].map(key => [key, { title: key, methods: [] }]));
  new Function("require", "module", "exports", compiled)(name => {
    if (name === "react") return react;
    if (name === "./connection-profile") return { protocolDelivery: delivery, ConnectionProfileResult() {} };
    if (name === "./protocol-icon") return { ProtocolIcon() {} };
    if (name === "./awg-domain-presets") return { awgDefaultDomain: "example.ru", awgDomainGroups: [], awgDomainPresets: [] };
    if (name === "./transport-fields") return { TransportFields() {} };
    return require(name);
  }, loaded, loaded.exports);
  const props = {
    protocols: [protocol],
    serverOptions: { connection_tuning: { [protocol]: [{ id: "balanced", label: "Balanced", settings: { quic_idle: 30 } }] } },
  };
  return () => { cursor = 0; return loaded.exports.ConnectionDialog(props); };
}

function elements(node) {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!node || typeof node !== "object" || !node.props) return [];
  return [node, ...elements(node.props.children)];
}

for (const protocol of ["hysteria2", "tuic", "xray"]) {
  test(`${protocol}: custom selection opens editable settings without resetting values`, () => {
    const render = dialogHarness(protocol);
    let nodes = elements(render());
    const selector = () => nodes.find(node => node.props["aria-label"] === "Готовый вариант подключения");
    const advanced = () => nodes.find(node => node.type === "details");
    const transport = () => nodes.find(node => node.props.settings && node.props.update);
    assert.equal(advanced().props.open, false);
    assert.ok(!elements(selector()).find(node => node.type === "option" && node.props.value === "custom").props.disabled);
    selector().props.onChange({ target: { value: "balanced" } });
    nodes = elements(render());
    const before = transport().props.settings;
    selector().props.onChange({ target: { value: "custom" } });
    nodes = elements(render());
    assert.equal(selector().props.value, "custom");
    assert.equal(advanced().props.open, true);
    assert.deepEqual(transport().props.settings, before);
    transport().props.update({ quic_idle: 45 });
    nodes = elements(render());
    assert.equal(transport().props.settings.quic_idle, 45);
    assert.equal(selector().props.value, "custom");
    advanced().props.onToggle({ currentTarget: { open: false } });
    nodes = elements(render());
    assert.equal(advanced().props.open, false);
    selector().props.onChange({ target: { value: "balanced" } });
    nodes = elements(render());
    assert.equal(selector().props.value, "balanced");
    assert.equal(transport().props.settings.quic_idle, 30);
  });
}
