export function formatModuleVersion(value?: string | null, fallback = "—") {
  const raw = String(value || "").trim();
  const match = raw.match(/\d+(?:\.\d+){1,3}/);
  if (!match) return fallback;
  return `v${match[0].split(".").slice(0, 3).join(".")}`;
}
