import { readFile, readdir } from "node:fs/promises";

export const ROOT = new URL("../../", import.meta.url);
const fileCache = new Map();
export const readFileText = (path) => {
  if (!fileCache.has(path)) fileCache.set(path, readFile(new URL(path, ROOT), "utf8"));
  return fileCache.get(path);
};
export const read = readFileText;
const sourceCache = new Map();
const readSourceTree = (directory) => {
  if (!sourceCache.has(directory)) sourceCache.set(directory, (async () => {
    const files = await readdir(new URL(directory, ROOT), { recursive: true });
    const sources = files.map((path) => String(path).replaceAll("\\", "/"))
      .filter((path) => /\.(?:ts|tsx)$/.test(path))
      .sort().map((path) => readFileText(directory + "/" + path));
    return (await Promise.all(sources)).join("\n");
  })());
  return sourceCache.get(directory);
};
export const readUiSources = async () => (await Promise.all([readSourceTree("app"), readSourceTree("src")])).join("\n");
export const readMihomoSources = () => readSourceTree("src/features/mihomo");
export const readApiSources = async () => (await Promise.all([read("api/main.py"), read("api/schemas.py")])).join("\n");
const STYLE_FILES = [
  "app/globals.css",
  "src/shared/styles/theme.css",
  "src/shared/styles/base.css",
  "src/shared/styles/app.css",
  "src/features/auth/auth.css",
  "src/features/overview/overview.css",
  "src/features/network/dns-policy.css",
  "src/features/security/security.css",
  "src/features/application/application.css",
  "src/features/services/services.css",
  "src/features/connections/connections.css",
  "src/features/protocols/protocols.css",
  "src/features/mihomo/mihomo.css",
  "src/shared/styles/polish.css",
  "src/shared/styles/control-center.css",
  "src/shared/notifications/notifications.css"
];
export const readStyles = async () => (await Promise.all(STYLE_FILES.map(read))).join("\n");
