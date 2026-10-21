export type RouteIconKind = "relay" | "cdn" | "ip" | "udp" | "tls" | "domain";

/** One stroke, size and coordinate system across address cards and editors. */
export function RouteTypeIcon({ kind }: { kind: RouteIconKind }) {
  return <svg className="routeTypeIcon" data-kind={kind} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {kind === "relay" ? <><rect x="3" y="3" width="7" height="6" rx="1.5" /><rect x="14" y="15" width="7" height="6" rx="1.5" /><path d="M6.5 9v7a2 2 0 0 0 2 2H14M17.5 15V8a2 2 0 0 0-2-2H10" /></>
      : kind === "cdn" ? <><path d="M7 17a4.5 4.5 0 0 1-1-8.9 6 6 0 0 1 11.4-1A5 5 0 0 1 18 17" /><path d="M8 21v-4m4 4v-6m4 6v-4" /></>
      : kind === "ip" ? <><rect x="3" y="4" width="18" height="7" rx="2" /><rect x="3" y="13" width="18" height="7" rx="2" /><path d="M7 7.5h.01M7 16.5h.01M11 7.5h6M11 16.5h6" /></>
      : kind === "tls" ? <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2" /></>
      : kind === "udp" ? <><path d="M4 8h16m-4-4 4 4-4 4M20 16H4m4-4-4 4 4 4" /></>
      : <><circle cx="12" cy="12" r="9" /><ellipse cx="12" cy="12" rx="4" ry="9" /><path d="M3 12h18" /></>}
  </svg>;
}
