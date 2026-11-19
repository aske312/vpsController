import type { ApplicationStatus, ServicesStatus } from "../shared/types/control-plane";

/** Past operation results belong to history, not current node health. */
export function observedNodeState(application: ApplicationStatus | null, services: ServicesStatus | null, operationActive: boolean) {
  if (!application) return "gray";
  const failed = application.api.active === false || application.api.runtime?.state === "error"
    || application.recovery?.application_required
    || application.component_health?.some(component => component.status === "critical")
    || services?.items.some(service => service.runtime?.state === "error" || service.state === "failed");
  if (failed) return "red";
  if (services?.service_mode?.active || application.service_mode?.active) return "blue";
  const degraded = application.api.active == null
    || services?.observation_failed
    || application.containers.some(container => container.healthy === false || (container.State || "").toLowerCase() !== "running")
    || application.component_health?.some(component => component.status !== "healthy")
    || application.runtime?.mode === "incomplete" || application.runtime?.mode === "unknown";
  return operationActive || degraded ? "yellow" : "green";
}
