import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { ruleIconGroups } from "../../src/features/mihomo/catalog.ts";
import { partitionRuleGroups, ruleGroupStatus, toggleRuleGroup } from "../../src/features/mihomo/rule-groups.ts";

const manager = fs.readFileSync(new URL("../../protocol-images/mihomo/advertising_rules.py", import.meta.url), "utf8");
const adsBlock = manager.split('AD_RULES = [', 2)[1].split(/^\]/m, 1)[0];
const adsLines = [...adsBlock.matchAll(/^\s*"((?:DOMAIN|DOMAIN-SUFFIX|GEOSITE),[^"\n]+)"/gm)].map((match) => match[1]);
const list = { id: "block_ads", key: "block_ads_rules", default_rules: adsLines.join("\n") };
const groups = ruleIconGroups.block_ads;

test("every built-in advertising entry belongs to exactly one group", () => {
  assert.ok(adsLines.length > 150);
  const partition = partitionRuleGroups(list, groups);
  const assigned = Object.values(partition).flat();
  assert.equal(assigned.length, new Set(assigned).size);
  assert.deepEqual(new Set(assigned), new Set(adsLines));
  assert.ok(partition.global_rtb.includes("DOMAIN-SUFFIX,advertising.com,REJECT"));
  assert.ok(partition.global_rtb.includes("DOMAIN-SUFFIX,adform.com,REJECT"));
  assert.ok(partition.adult_video.includes("DOMAIN-SUFFIX,ero-advertising.com,REJECT"));
  assert.ok(partition.adult_video.includes("DOMAIN-SUFFIX,eroadvertising.com,REJECT"));
  assert.ok(partition.mobile.includes("DOMAIN-SUFFIX,bidmachine.io,REJECT"));
});

test("disabling any advertising group preserves all other groups and custom rules", () => {
  const custom = "DOMAIN,custom.example,REJECT";
  const partition = partitionRuleGroups(list, groups);
  for (const group of groups) {
    assert.ok(partition[group.id].length, `Empty group: ${group.id}`);
    const value = toggleRuleGroup(list, groups, `${list.default_rules}\n${custom}`, group.id);
    const remaining = new Set(value.split("\n"));
    assert.ok(remaining.has(custom));
    for (const [id, entries] of Object.entries(partition)) {
      for (const entry of entries) assert.equal(remaining.has(entry), id !== group.id, `${group.id}: ${entry}`);
    }
    const disabled = toggleRuleGroup(list, groups, "@default", group.id);
    assert.equal(toggleRuleGroup(list, groups, disabled, group.id), "@default");
  }
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
    assert.equal(ruleGroupStatus(id, false), "Через VPN");
  }
  assert.equal(ruleGroupStatus("udp_tunnel_exclusions", true), "Через VPN");
  assert.equal(ruleGroupStatus("udp_tunnel_exclusions", false), "Напрямую");
  for (const id of ["block_ads", "block_privacy", "block_malware"]) {
    assert.equal(ruleGroupStatus(id, true), "Блокируется");
    assert.equal(ruleGroupStatus(id, false), "Без блокировки");
  }
  assert.equal(ruleGroupStatus("quic_compatibility", true), "Без QUIC");
  assert.equal(ruleGroupStatus("quic_compatibility", false), "QUIC разрешён");
  assert.equal(ruleGroupStatus("direct_ru_sites", false, true), "Частично");
});
