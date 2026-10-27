import type { PolicySettings, RuleIconGroup } from "./types";

type RuleList = NonNullable<PolicySettings["rule_lists"]>[number];

function lines(value: string) {
  return [...new Set(value.split("\n").map((line) => line.trim()).filter(Boolean))];
}

export function partitionRuleGroups(ruleList: RuleList, groups: RuleIconGroup[]) {
  const result: Record<string, string[]> = Object.fromEntries(groups.map((group) => [group.id, []]));
  const fallback = groups.find((group) => !group.tokens.length);
  for (const line of lines(ruleList.available_rules || ruleList.default_rules)) {
    const normalized = line.toLowerCase();
    let owner = fallback;
    let specificity = 0;
    for (const group of groups) {
      for (const token of group.tokens) {
        // The most specific match owns the entry. Catalog order breaks ties,
        // so overlapping tokens can never couple two group switches.
        if (token.length > specificity && normalized.includes(token.toLowerCase())) {
          owner = group;
          specificity = token.length;
        }
      }
    }
    if (owner) result[owner.id].push(line);
  }
  return result;
}

export function toggleRuleGroup(ruleList: RuleList, groups: RuleIconGroup[], value: string, groupId: string) {
  const defaults = lines(ruleList.default_rules);
  const available = lines(ruleList.available_rules || ruleList.default_rules);
  const current = value.trim() === "@default" ? defaults : lines(value);
  const groupLines = partitionRuleGroups(ruleList, groups)[groupId] || [];
  if (!groupLines.length) return value;
  const selected = new Set(current);
  const enabled = groupLines.every((line) => selected.has(line));
  for (const line of groupLines) {
    if (enabled) selected.delete(line);
    else selected.add(line);
  }
  const custom = current.filter((line) => !available.includes(line) && selected.has(line));
  const selectedAvailable = available.filter((line) => selected.has(line));
  const isDefault = selectedAvailable.length === defaults.length && defaults.every((line) => selected.has(line)) && !custom.length;
  return isDefault ? "@default" : [...selectedAvailable, ...custom].join("\n");
}
