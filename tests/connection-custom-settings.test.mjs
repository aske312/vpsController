import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

const require = createRequire(import.meta.url);
function dialogHarness(protocol, presets = [{ id: "balanced", label: "Balanced", settings: { quic_idle: 30 } }]) {
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
    serverOptions: { connection_tuning: { [protocol]: presets }, server_ports: { [protocol]: { default_port: 8443, suggestions: [39761, 443] } } },
  };
  return () => { cursor = 0; return loaded.exports.ConnectionDialog(props); };
}

test("preset synchronizes actual selects, numeric controls, toggles and transport values", () => {
  const render = dialogHarness("tuic", [
    { id: "balanced", settings: { heartbeat: "10s", congestion_control: "bbr", initial_packet_size: 0, disable_path_mtu_discovery: false } },
    { id: "mobile", settings: { heartbeat: "5s", congestion_control: "cubic", initial_packet_size: 1200, disable_path_mtu_discovery: true } },
  ]);
  let nodes = elements(render());
  nodes.find(node => node.props["aria-label"] === "Готовый вариант подключения").props.onChange({ target: { value: "mobile" } });
  nodes = elements(render());
  assert.equal(nodes.find(node => node.type === "details").props.open, true);
  assert.ok(nodes.some(node => node.type === "select" && node.props.value === "cubic"));
  assert.ok(nodes.some(node => node.type === "input" && node.props.type === "number" && node.props.value === 5));
  assert.ok(nodes.some(node => node.type === "select" && node.props.value === 1200));
  assert.ok(nodes.some(node => node.type === "input" && node.props.type === "checkbox" && node.props.checked));
  nodes.find(node => node.props["aria-label"] === "Готовый вариант подключения").props.onChange({ target: { value: "balanced" } });
  nodes = elements(render());
  assert.ok(nodes.some(node => node.type === "input" && node.props.type === "number" && node.props.value === 10));
  assert.ok(nodes.some(node => node.type === "select" && node.props.value === 0));
});

test("individual server port supports suggestions, manual input and random without changing tuning", () => {
  const render = dialogHarness("tuic");
  let nodes = elements(render());
  const choice = () => nodes.find(node => node.props["aria-label"] === "Вариант входного порта");
  choice().props.onChange({ target: { value: "39761" } });
  nodes = elements(render());
  assert.equal(nodes.find(node => node.props["aria-label"] === "Порт подключения").props.value, 39761);
  assert.equal(nodes.find(node => node.props["aria-label"] === "Готовый вариант подключения").props.value, "balanced");
  choice().props.onChange({ target: { value: "custom" } });
  nodes = elements(render());
  nodes.find(node => node.props["aria-label"] === "Порт подключения").props.onChange({ target: { value: "53123" } });
  nodes = elements(render());
  assert.equal(nodes.find(node => node.props["aria-label"] === "Порт подключения").props.value, 53123);
  choice().props.onChange({ target: { value: "random" } });
  nodes = elements(render());
  const settings = nodes.find(node => node.props.settings && node.props.update).props.settings;
  assert.equal(settings.server_port_random, true);
  assert.equal(settings.server_port, null);
  assert.ok(!nodes.some(node => node.props["aria-label"] === "Порт подключения"));
});

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
