import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { ruleIconGroups } from "../../src/features/mihomo/catalog.ts";
import { partitionRuleGroups, ruleGroupStatus, toggleRuleGroup } from "../../src/features/mihomo/rule-groups.ts";

test("reviewed RU domains belong to the matching service switch", () => {
  const catalog = JSON.parse(fs.readFileSync(new URL("../../protocol-images/mihomo/ru-service-domains.json", import.meta.url), "utf8"));
  for (const entry of catalog.groups) {
    const rules = [...entry.domains.map(domain => `DOMAIN-SUFFIX,${domain},DIRECT`), ...entry.hosts.map(host => `DOMAIN,${host},DIRECT`)];
    for (const preset of entry.presets) {
      const list = { id: preset, default_rules: rules.join("\n") };
      const groups = ruleIconGroups[preset];
      assert.deepEqual(partitionRuleGroups(list, groups)[entry.id], rules);
      const disabled = toggleRuleGroup(list, groups, "@default", entry.id);
      for (const rule of rules) assert.equal(disabled.includes(rule), false);
    }
  }
});

test("generic group toggles preserve custom rules and round trip", () => {
  const list = { default_rules: "DOMAIN-SUFFIX,example.com,REJECT\nDOMAIN-SUFFIX,other.test,REJECT" };
  const groups = [{ id: "one", tokens: ["example.com"] }, { id: "other", tokens: [] }];
  const disabled = toggleRuleGroup(list, groups, "@default", "one");
  assert.equal(disabled.includes("example.com"), false);
  assert.equal(disabled.includes("other.test"), true);
  assert.equal(toggleRuleGroup(list, groups, disabled, "one"), "@default");
});

test("overlapping future tokens have one owner and unknown entries use the fallback", () => {
  const candidates = [
    { id: "generic", tokens: ["example.com"] },
    { id: "specific", tokens: ["video.example.com"] },
    { id: "tie", tokens: ["video.example.com"] },
    { id: "extra", tokens: [] },
  ];
  const fixture = { default_rules: "DOMAIN-SUFFIX,example.com,REJECT", available_rules: "DOMAIN-SUFFIX,example.com,REJECT\nDOMAIN,video.example.com,REJECT\nDOMAIN,new.example.net,REJECT" };
  const result = partitionRuleGroups(fixture, candidates);
  assert.deepEqual(result.generic, ["DOMAIN-SUFFIX,example.com,REJECT"]);
  assert.deepEqual(result.specific, ["DOMAIN,video.example.com,REJECT"]);
  assert.deepEqual(result.tie, []);
  assert.deepEqual(result.extra, ["DOMAIN,new.example.net,REJECT"]);
  assert.equal(toggleRuleGroup(fixture, candidates, "@default", "specific"), fixture.available_rules.split("\n").slice(0, 2).join("\n"));
  assert.equal(toggleRuleGroup(fixture, candidates, "@default", "tie"), "@default");
});

test("Mail.ru selection changes its own entries and preserves Russian zones", () => {
  const domains = ["mail.ru", "imgsmail.ru", "attachmail.ru", "cdnmail.ru", "bk.ru", "inbox.ru", "list.ru"];
  const defaults = ["DOMAIN-SUFFIX,ru,DIRECT", ...domains.map((d) => `DOMAIN-SUFFIX,${d},DIRECT`)];
  const ruList = { id: "direct_ru_sites", default_rules: defaults.join("\n") };
  const ruGroups = ruleIconGroups.direct_ru_sites;
  const partition = partitionRuleGroups(ruList, ruGroups);
  assert.equal(partition.mail.length, domains.length);
  assert.deepEqual(partition.zones, [defaults[0]]);
  const vpn = toggleRuleGroup(ruList, ruGroups, "@default", "mail");
  assert.ok(vpn.includes(defaults[0]));
  for (const domain of domains) {
    assert.equal(vpn.includes(`DOMAIN-SUFFIX,${domain},GATE.312`), false);
    assert.equal(vpn.includes(`DOMAIN-SUFFIX,${domain},DIRECT`), false);
  }
  assert.equal(vpn.includes("mail.com"), false);
  assert.equal(toggleRuleGroup(ruList, ruGroups, vpn, "mail"), "@default");
});

test("VK portal CDN routes belong to the VK switch", () => {
  const rule = "DOMAIN-SUFFIX,vk-portal.net,DIRECT";
  const list = { id: "direct_ru_sites", default_rules: ["DOMAIN-SUFFIX,ru,DIRECT", rule].join("\n") };
  const groups = ruleIconGroups.direct_ru_sites;
  assert.deepEqual(partitionRuleGroups(list, groups).vk, [rule]);
  const disabled = toggleRuleGroup(list, groups, "@default", "vk");
  assert.equal(disabled.includes(rule), false);
  assert.ok(disabled.includes("DOMAIN-SUFFIX,ru,DIRECT"));
});

test("group captions describe routes, protection, QUIC and partial selections", () => {
  for (const id of ["direct_ru_sites", "direct_ru_services", "direct_ru_banks", "direct_ru_marketplaces", "direct_local_network", "direct_downloads", "windows_geolocation"]) {
    assert.equal(ruleGroupStatus(id, true), "Напрямую");
    assert.equal(ruleGroupStatus(id, false), "По общим правилам");
  }
  assert.equal(ruleGroupStatus("udp_tunnel_exclusions", true), "Через VPN");
  assert.equal(ruleGroupStatus("udp_tunnel_exclusions", false), "Напрямую");
  for (const id of ["block_privacy", "block_malware"]) {
    assert.equal(ruleGroupStatus(id, true), "Блокируется");
    assert.equal(ruleGroupStatus(id, false), "Без блокировки");
  }
  assert.equal(ruleGroupStatus("quic_compatibility", true), "Без QUIC");
  assert.equal(ruleGroupStatus("quic_compatibility", false), "QUIC разрешён");
  assert.equal(ruleGroupStatus("direct_ru_sites", false, true), "Частично");
});
