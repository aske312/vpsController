const active = new Set(["queued", "running", "active", "activating", "rebooting", "powering-off"]);
const failures = new Set(["failed", "failure", "exit-code", "timeout", "process-failed", "interrupted", "reboot-timeout", "signal", "dependency"]);
/**
 * Lifecycle owns completion; a stage result never finishes its parent.
 * @param {{state?: string, result?: string}} [value]
 * @returns {"active" | "success" | "error" | "unknown" | "cancelled" | "skipped"}
 */
export function operationOutcome(value) {
  if (value?.state === "cancelled") return "cancelled";
  if (value?.state === "failed") return "error";
  if (active.has(value?.state || "")) return "active";
  if (value?.state === "unknown") return "unknown";
  if (failures.has(value?.result || "")) return "error";
  if (["succeeded", "finished", "done"].includes(value?.state || "")) {
    if (value?.result === "skipped") return "skipped";
    return !value?.result || ["success", "passed"].includes(value.result) ? "success" : "unknown";
  }
  return "unknown";
}
