export type ProviderQuota = {
  unlimited?: boolean; remaining?: number; account_remaining?: number; used?: number; granted?: number;
  expires_at?: number; model_limits_enabled?: boolean; allowed_models?: string[];
  allowed_models_count?: number; allowed_models_truncated?: boolean;
};
export type ProviderInfo = { checked_at?: number; catalog_latency_ms?: number; models_count?: number; catalog_http_status?: number };
export type ProviderObservation = { provider_info?: ProviderInfo; quota?: ProviderQuota | null; quota_checked_at?: number };
export type ProviderCatalog = ProviderObservation & { models: string[] };

let catalogTail: Promise<void> = Promise.resolve();
const catalogs = new Map<string, Promise<ProviderCatalog>>();
/** Serialize discovery in this tab; concurrent saved-connection checks share their read. */
export function queueProviderCatalog(key: string, fetch: () => Promise<ProviderCatalog>, signal?: AbortSignal): Promise<ProviderCatalog> {
  const existing = catalogs.get(key);
  if (existing) return existing;
  const pending = catalogTail.then(async () => {
    if (!signal) return fetch();
    if (signal.aborted) throw new Error("Проверка отменена");
    let onAbort: () => void = () => {};
    try {
      return await Promise.race([fetch(), new Promise<never>((_, reject) => {
        onAbort = () => reject(new Error("Проверка отменена"));
        signal.addEventListener("abort", onAbort, { once: true });
      })]);
    } finally { signal.removeEventListener("abort", onAbort); }
  });
  catalogs.set(key, pending);
  catalogTail = pending.then(() => undefined, () => undefined);
  void pending.finally(() => { if (catalogs.get(key) === pending) catalogs.delete(key); }).catch(() => undefined);
  return pending;
}

const number = (value: number) => value.toLocaleString("ru-RU");
export function quotaText(quota?: ProviderQuota | null) {
  return quota?.unlimited === true ? "Без лимита" : typeof quota?.remaining === "number" ? number(quota.remaining) : "—";
}
export function quotaFacts(observation?: ProviderObservation, now = Date.now()) {
  const quota = observation?.quota;
  const facts: { label: string; value: string; title?: string }[] = [];
  if (!quota) return facts;
  for (const [key, label] of [["used", "Использовано"], ["granted", "Выделено"], ["account_remaining", "Аккаунт"]] as const) {
    if (key === "granted" && quota.unlimited === true) continue;
    if (typeof quota[key] === "number") facts.push({ label, value: number(quota[key]) });
  }
  if (quota.expires_at === 0) facts.push({ label: "Ключ", value: "Без срока" });
  else if (typeof quota.expires_at === "number") {
    const date = new Date(quota.expires_at * 1000);
    if (!Number.isNaN(date.getTime())) facts.push({ label: "Ключ", value: `${date.getTime() <= now ? "Истёк" : "До"} ${date.toLocaleDateString("ru-RU")}` });
  }
  if (quota.model_limits_enabled === false) facts.push({ label: "Лимит моделей", value: "Выключен" });
  if (quota.model_limits_enabled === true) facts.push({ label: "Лимит моделей", value: typeof quota.allowed_models_count === "number" ? `${number(quota.allowed_models_count)} разрешено` : "Включён", title: quota.allowed_models ? `${quota.allowed_models_truncated ? "Первые 100: " : ""}${quota.allowed_models.join(", ")}` : undefined });
  return facts;
}
export function latestObservation(...values: (ProviderObservation | undefined)[]): ProviderObservation | undefined {
  const available = values.filter((value): value is ProviderObservation => !!value);
  if (!available.length) return undefined;
  const newest = [...available].sort((a, b) => (b.provider_info?.checked_at ?? 0) - (a.provider_info?.checked_at ?? 0))[0];
  const quota = [...available].filter(value => "quota" in value).sort((a, b) => (b.quota_checked_at ?? b.provider_info?.checked_at ?? 0) - (a.quota_checked_at ?? a.provider_info?.checked_at ?? 0))[0];
  return { provider_info: newest.provider_info, ...(quota ? { quota: quota.quota, quota_checked_at: quota.quota_checked_at } : {}) };
}
