import type { ApplicationVersion } from "../../shared/types/control-plane";

/** A cached comparison awaiting a fresh read does not confirm CURRENT. */
export function applicationVersionState(value?: ApplicationVersion): "current" | "outdated" | "checking" | "unknown" {
  if (value?.refreshing === true) return "checking";
  if (!value || value.error) return "unknown";
  const current = value.current_commit?.toLowerCase();
  const latest = value.latest_commit?.toLowerCase();
  if (!current || !latest || !/^[0-9a-f]{7,64}$/.test(current) || !/^[0-9a-f]{7,64}$/.test(latest)) return "unknown";
  const sameCommit = current.startsWith(latest) || latest.startsWith(current);
  if (value.outdated === false && !sameCommit) return "unknown";
  if (value.outdated === true && sameCommit) return "unknown";
  return value.outdated === false ? "current" : value.outdated === true ? "outdated" : "unknown";
}
