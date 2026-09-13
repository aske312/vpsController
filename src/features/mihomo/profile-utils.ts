import type { Profile, ProfileDevice } from "./types";
import { devicePlatform } from "./catalog";

export function clientUuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const value = Array.from(bytes, (item) => item.toString(16).padStart(2, "0")).join("");
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
}

export function devicePlatformMeta(device: ProfileDevice) {
  return devicePlatform[device.os || "unknown"] || devicePlatform.unknown;
}

export function deviceClientShortName(device: Pick<ProfileDevice, "client_name">) {
  const name = device.client_name?.trim();
  if (!name) return "Undefined";
  const shortNames: Record<string, string> = {
    "Koala Clash": "Koala", "Clash Verge Rev": "Verge", "Clash Nyanpasu": "Nyanpasu",
    "Clash Party": "Party", "Clash Meta for Android": "CMFA", "Prizrak-Box": "Prizrak",
    "Sing-Box Launcher": "SB Launcher", "sing-box for Android": "SFA",
    "sing-box for Apple": "SB Apple", "sing-box for Desktop": "SB Desktop",
    "sing-box MT": "SB MT", "Mihomo / Clash": "Mihomo",
  };
  return shortNames[name] || (name.length > 14 ? `${name.slice(0, 13)}…` : name);
}

export function deviceSystemLabel(device: Pick<ProfileDevice, "os" | "os_version">) {
  const platform = devicePlatform[device.os || "unknown"] || devicePlatform.unknown;
  return `${platform.label}${device.os_version ? ` ${device.os_version}` : ""}`;
}

export function registeredProfileDevices(profile: Profile) {
  return (profile.devices || []).filter((device) => device.scope !== "common" && device.id !== profile.common_device_id);
}

export function clientConfigFormat(profile: Profile, device?: ProfileDevice) {
  const selected = device || profile.devices?.find((item) => item.id === profile.common_device_id || item.scope === "common");
  const format = (selected?.routing || profile.routing)?.client_config_format;
  return format === "xray" || format === "singbox" ? format : "mihomo";
}

export function clientImportUrl(url: string, name: string, format: string, client = "") {
  if (/karing/i.test(client)) return `karing://install-config?url=${encodeURIComponent(url)}&name=${encodeURIComponent(name)}&x-hwid=true`;
  if (/hiddify/i.test(client)) return `hiddify://import?url=${encodeURIComponent(url)}&name=${encodeURIComponent(name)}`;
  return format === "singbox" ? `sing-box://import-remote-profile?url=${encodeURIComponent(url)}#${encodeURIComponent(name)}` : url;
}

export function selectedGameIds(value: unknown, defaults: Set<string>) {
  const raw = String(value ?? "").trim();
  return !raw || raw === "@default" ? new Set(defaults) : new Set(raw.split(",").map((item) => item.trim()).filter(Boolean));
}
