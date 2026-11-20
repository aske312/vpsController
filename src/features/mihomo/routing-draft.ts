import type { PolicySettings } from "./types";

export type RoutingDraft = PolicySettings["values"];

export const routingControlDefaults = {
  mode: "rule",
  strategy: "fallback",
  load_balance_strategy: "consistent-hashing",
  test_url: "",
  interval: 30,
  health_timeout: 3000,
  max_failed_times: 2,
  tolerance: 0,
} satisfies RoutingDraft;

function comparableValue(value: RoutingDraft[string] | undefined, fallback: RoutingDraft[string] | undefined) {
  const effective = value ?? fallback;
  if (effective === "" && typeof fallback === "string") return fallback;
  if (typeof fallback === "number" && typeof effective === "string" && /^[+-]?\d+$/.test(effective.trim())) {
    return Number(effective);
  }
  return effective;
}

export function changedRoutingDraftKeys(baseline: RoutingDraft, draft: RoutingDraft): string[] {
  const defaults: RoutingDraft = routingControlDefaults;
  return [...new Set([...Object.keys(baseline), ...Object.keys(draft)])].filter((key) => {
    const fallback = Object.hasOwn(defaults, key) ? defaults[key] : undefined;
    return comparableValue(baseline[key], fallback) !== comparableValue(draft[key], fallback);
  });
}

export function rebaseRoutingDraft(saved: RoutingDraft, submitted: RoutingDraft, draft: RoutingDraft): RoutingDraft {
  const changes = changedRoutingDraftKeys(submitted, draft);
  if (!changes.length) return saved;
  const next = { ...saved };
  for (const key of changes) {
    if (draft[key] === undefined) delete next[key];
    else next[key] = draft[key];
  }
  return next;
}
