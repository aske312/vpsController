import type { ReactNode } from "react";
import type { RouteSummary } from "./route-summary";

export type NetworkRouteTone = "active" | "ready" | "unchecked" | "warning" | "obsolete" | "error";
export function NetworkRouteBadge({ tone, children, title }: { tone: NetworkRouteTone; children: ReactNode; title?: string }) {
  return <span className={`networkRouteStatus ${tone}`} title={title}>{children}</span>;
}

export function NetworkRouteSummaryBadge({ summary }: { summary: RouteSummary }) {
  const tone: NetworkRouteTone = summary.state === "verified" ? "ready"
    : ["present", "connected", "listening"].includes(summary.state) ? "active"
    : summary.state === "error" ? "error" : summary.state === "stale" ? "obsolete"
    : summary.state === "warning" ? "warning" : "unchecked";
  return <NetworkRouteBadge tone={tone} title={summary.detail}>{summary.label}</NetworkRouteBadge>;
}
