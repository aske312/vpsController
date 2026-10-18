import type { Protocol } from "../types/control-plane";

export type ClientExport = { config: string; filename?: string; protocol?: Protocol; profiles?: { id: string; name: string; filename: string; config: string }[] };

export function clientExport(value: unknown): ClientExport {
  const object = (item: unknown): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item));
  const config = (item: unknown) => typeof item === "string" && item.length > 0 && item.length <= 262144;
  const filename = (item: unknown) => typeof item === "string" && /^[A-Za-z0-9_.-]{1,128}$/.test(item) && item !== "." && item !== "..";
  if (!object(value) || !config(value.config) || value.filename !== undefined && !filename(value.filename)
    || value.protocol !== undefined && (typeof value.protocol !== "string" || !["wg", "awg", "shadowsocks", "vless-reality-xhttp", "hysteria2", "tuic", "trojan", "openvpn", "ikev2"].includes(value.protocol))
    || value.profiles !== undefined && (!Array.isArray(value.profiles) || value.profiles.length > 3 || value.profiles.some(item => !object(item) || typeof item.id !== "string" || !item.id || typeof item.name !== "string" || !filename(item.filename) || !config(item.config))
      || new Set(value.profiles.map(item => item.id)).size !== value.profiles.length)) {
    throw new Error("Не удалось подтвердить конфигурацию подключения. Обновите список подключений перед повторным созданием.");
  }
  return value as ClientExport;
}
