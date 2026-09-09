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

export function deviceSystemLabel(device: Pick<ProfileDevice, "os" | "os_version">) {
  const platform = devicePlatform[device.os || "unknown"] || devicePlatform.unknown;
  return `${platform.label}${device.os_version ? ` ${device.os_version}` : ""}`;
}

export function registeredProfileDevices(profile: Profile) {
  return (profile.devices || []).filter((device) => device.scope !== "common" && device.id !== profile.common_device_id);
}

export function selectedGameIds(value: unknown, defaults: Set<string>) {
  const raw = String(value ?? "").trim();
  return !raw || raw === "@default" ? new Set(defaults) : new Set(raw.split(",").map((item) => item.trim()).filter(Boolean));
}
