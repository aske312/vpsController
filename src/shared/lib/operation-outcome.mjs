const active = new Set(["queued", "running", "active", "activating", "rebooting", "powering-off"]);
const failures = new Set(["failed", "failure", "exit-code", "timeout", "process-failed", "interrupted", "reboot-timeout", "signal", "dependency"]);
const warnings = new Set(["warn", "warning", "warnings", "passed_with_warnings"]);
/**
 * Lifecycle owns completion; a stage result never finishes its parent.
 * @param {{state?: string, result?: string}} [value]
 * @returns {"active" | "success" | "warning" | "error" | "unknown" | "cancelled" | "skipped"}
 */
export function operationOutcome(value) {
  if (value?.state === "cancelled") return "cancelled";
  if (value?.state === "failed") return "error";
  if (active.has(value?.state || "")) return "active";
  if (value?.state === "unknown") return "unknown";
  if (failures.has(value?.result || "")) return "error";
  if (warnings.has(value?.state || "")) return "warning";
  if (["succeeded", "finished", "done"].includes(value?.state || "")) {
    if (warnings.has(value?.result || "")) return "warning";
    if (value?.result === "skipped") return "skipped";
    return !value?.result || ["success", "passed"].includes(value.result) ? "success" : "unknown";
  }
  return "unknown";
}
