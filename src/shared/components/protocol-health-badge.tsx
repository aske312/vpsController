export type ProtocolHealth = {
  state: "READY" | "WORKS" | "WARN" | "ERROR";
  base_state: "READY" | "WARN" | "ERROR";
  reason: string;
  checked_at?: string;
  checking: boolean;
  clients: number;
  traffic_now: boolean;
};

export function ProtocolHealthBadge({ health }: { health?: ProtocolHealth }) {
  const state = health?.state || "WARN";
  return <span className="protocolHealthBadge" data-state={state} title={health?.reason || "Ожидаем диагностику протокола"}>{state}</span>;
}
