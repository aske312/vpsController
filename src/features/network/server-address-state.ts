export type ServerAddressRole = "primary" | "reserve" | "additional";
export type ServerAddress = { address: string; family: "ipv4" | "ipv6"; interfaces: string[]; source: "interface" | "configured" | "saved"; presence: "present" | "missing" | "unknown"; role: ServerAddressRole };
export type AddressChange = { id: string; address: string; role: ServerAddressRole; previous_revision: string; revision: string };
export type ServerAddressInventory = { revision: string; detected_at: string; items: ServerAddress[]; discovery: { state: "ready" | "error"; message: string }; change: AddressChange | null };
export const addressRoleLabels: Record<ServerAddressRole, string> = { primary: "Основной", reserve: "Резервный", additional: "Дополнительный" };
const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
const revision = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{32}$/.test(value);
const role = (value: unknown): value is ServerAddressRole => typeof value === "string" && ["primary", "reserve", "additional"].includes(value);
function family(value: unknown) {
  if (typeof value !== "string") return null;
  if (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(value) && value.split(".").every(part => String(Number(part)) === part && Number(part) <= 255)) return "ipv4";
  if (!/^[0-9a-f:]+$/.test(value) || !value.includes(":")) return null;
  try { return new URL(`http://[${value}]/`).hostname ? "ipv6" : null; } catch { return null; }
}
export function serverAddressesSnapshot(value: unknown): ServerAddressInventory {
  const fail = () => { throw new Error("Некорректные данные публичных IP сервера"); };
  if (!record(value) || !revision(value.revision) || typeof value.detected_at !== "string" || !Number.isFinite(Date.parse(value.detected_at))
    || !record(value.discovery) || !["ready", "error"].includes(String(value.discovery.state)) || typeof value.discovery.message !== "string" || !Array.isArray(value.items)) return fail();
  const seen = new Set<string>(); const primaries = new Set<string>();
  for (const row of value.items) {
    if (!record(row) || family(row.address) !== row.family || !role(row.role) || !["present", "missing", "unknown"].includes(String(row.presence)) || !["interface", "configured", "saved"].includes(String(row.source))
      || !Array.isArray(row.interfaces) || !row.interfaces.every(item => typeof item === "string" && item.length > 0) || seen.has(String(row.address))
      || (row.presence === "present" && (!row.interfaces.length || row.source !== "interface"))
      || (row.role === "primary" && primaries.has(String(row.family)))) return fail();
    seen.add(String(row.address)); if (row.role === "primary") primaries.add(String(row.family));
  }
  const change = value.change;
  if (change !== null && (!record(change) || !revision(change.id) || !revision(change.revision) || !revision(change.previous_revision) || !family(change.address) || !role(change.role))) return fail();
  return value as ServerAddressInventory;
}
export function addressMatches(row: ServerAddress, query: string) {
  return `${row.address} ${row.family} ${row.interfaces.join(" ")} ${addressRoleLabels[row.role]} SERVER`.toLowerCase().includes(query.trim().toLowerCase());
}
export function confirmsAddressChange(value: ServerAddressInventory, change: Omit<AddressChange, "revision">) {
  return value.change !== null && ["id", "address", "role", "previous_revision"].every(key => value.change![key as keyof AddressChange] === change[key as keyof typeof change]);
}
