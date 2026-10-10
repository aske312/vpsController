export type ConnectionState = "stable" | "attention" | "offline" | "issued";

export function connectionState(client: { protocol: string; update_state?: string; quality?: string }): ConnectionState {
  if (client.update_state || client.quality === "warning" || client.quality === "error") return "attention";
  if (client.protocol !== "awg") return "issued";
  if (client.quality === "stable") return "stable";
  return "offline";
}
