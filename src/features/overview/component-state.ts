import type { ProtocolImage } from "../../shared/types/control-plane";

export function componentPresentation(image: ProtocolImage) {
  const observed = image.component_state;
  const installation = observed?.installation.state;
  const labels = {
    installed: "Installed", not_installed: "Not installed", incomplete: "Incomplete",
    running: "Running", stopped: "Stopped", error: "Error", unknown: "Unknown",
    healthy: "Healthy", possible_issues: "Possible issues", unchecked: "Unchecked",
    installing: "Installing", updating: "Updating", removing: "Removing",
  };
  // Older API booleans cannot prove an installation or a stopped runtime.
  const canInstall = installation === "not_installed" && image.installable && image.management?.state !== "unknown" && !observed?.operation;
  const readOnly = image.management?.state !== "managed" || Boolean(image.management?.retained);
  return {
    installation: installation ? labels[installation] : "Unknown",
    runtime: observed ? labels[observed.runtime.state] : "Unknown",
    health: observed ? labels[observed.health.state] : "Unknown",
    operation: observed?.operation ? labels[observed.operation.kind || "unknown"] : null,
    canInstall,
    canAdopt: image.installed && image.management?.state === "unmanaged" && !observed?.operation,
    canUpdate: !readOnly && installation === "installed" && !observed?.operation && Boolean(image.update_available),
    readOnly,
  };
}
