import type { RuntimeObservation } from "../../shared/types/control-plane";

export function applicationRuntime(value?: { runtime?: RuntimeObservation; active?: boolean | null; State?: string }) {
  const state = value?.runtime?.state || (value?.active === true || value?.State === "running" ? "running"
    : value?.active === false || value?.State === "stopped" ? "stopped"
    : value?.State === "error" ? "error" : "unknown");
  return {
    state,
    label: { running: "Running", stopped: "Stopped", error: "Error", unknown: "Unknown" }[state],
    tone: state === "running" ? "healthy" : state === "error" ? "failed" : "pending",
    reason: value?.runtime?.reason || "Работоспособность проверяется отдельно",
  };
}
