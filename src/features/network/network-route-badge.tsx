import type { ReactNode } from "react";

export type NetworkRouteTone = "active" | "ready" | "unchecked" | "warning" | "obsolete" | "error";
export function NetworkRouteBadge({ tone, children }: { tone: NetworkRouteTone; children: ReactNode }) {
  return <span className={`networkRouteStatus ${tone}`}>{children}</span>;
}
