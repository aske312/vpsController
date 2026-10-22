"use client";

import { useEffect, useState } from "react";
import { cdnSecuritySnapshot, type CdnSecurityStatus } from "../../shared/lib/cdn-security-operation";
import type { NetworkRequest } from "./network-api";

export type NetworkCloudflareControl = {
  change: (enabled: boolean) => Promise<void> | void;
  pending: boolean;
  blocked: boolean;
};

export function NetworkCloudflare({ domain, detected, request, control, refreshKey }: {
  domain: string; detected: boolean; request: NetworkRequest; control: NetworkCloudflareControl; refreshKey: number;
}) {
  const [status, setStatus] = useState<CdnSecurityStatus | null>(null);
  const [error, setError] = useState("");
  const [reading, setReading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [sending, setSending] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setReading(true);
      try {
        const next = cdnSecuritySnapshot(await request("/application/cdn-security", { cache: "no-store" }));
        if (!cancelled) { setStatus(next); setError(""); }
      } catch { if (!cancelled) setError("Не удалось подтвердить настройку Cloudflare. Обновите сведения перед изменением."); }
      finally { if (!cancelled) setReading(false); }
    }, 0);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [request, control.pending, refreshKey, revision]);
  const canonicalDomain = (value: string) => value.trim().toLowerCase().replace(/\.+$/, "");
  const configured = status?.cloudflare_domains?.some(value => canonicalDomain(value) === canonicalDomain(domain));
  if (!configured && !(detected && (!status || error))) return null;
  const pending = control.pending || Boolean(status?.operation && ["queued", "running", "unknown"].includes(status.operation.state));
  const blocked = control.blocked || pending || sending || reading || Boolean(error) || !configured;
  async function change(enabled: boolean) {
    if (blocked) return;
    setSending(true);
    try { await control.change(enabled); }
    catch { setError("Не удалось подтвердить результат. Проверьте состояние операции перед новым изменением."); }
    finally { setSending(false); setRevision(value => value + 1); }
  }
  return <section className="networkCloudflare" aria-label={`Защита Cloudflare ${domain}`}>
    <div className="networkCloudflareHeading"><div><h3>Сертификат Cloudflare</h3></div>
      <label className="networkCloudflareToggle"><input type="checkbox" aria-label={`Проверять сертификат Cloudflare для ${domain}`} checked={status?.authenticated_origin_pulls ?? false} disabled={blocked} onChange={event => void change(event.target.checked)} /><span>{pending || sending ? "Проверяем операцию…" : reading ? "Проверяем…" : status?.authenticated_origin_pulls ? "Включено" : "Выключено"}</span></label>
    </div>
    <small>Для всех Cloudflare-маршрутов. {status?.authenticated_origin_pulls ? "Доступ только с сертификатом Cloudflare." : "Перед включением настройте Full (strict) и Origin Pulls; возможен краткий разрыв."}</small>
    {error && <p role="status">{error} <button type="button" disabled={reading} onClick={() => setRevision(value => value + 1)}>Обновить</button></p>}
  </section>;
}
