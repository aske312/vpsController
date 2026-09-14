"use client";

import { useCallback, useState } from "react";
import { useCdnSecurity } from "../application/use-cdn-security";
import { OperationNotifications } from "../../control-panel/components/operation-notifications";
import type { CdnOperation, CdnSecurityStatus, EchRecord } from "../../shared/lib/cdn-security-operation";
import type { NetworkRequest } from "./network-api";

export function NetworkEch({ domain, route, request }: { domain: string; route: string; request: NetworkRequest }) {
  const [open, setOpen] = useState(false);
  const [record, setRecord] = useState<EchRecord | null>(null);
  const [serverOperation, setServerOperation] = useState<CdnOperation | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState("");
  const apply = useCallback((status: CdnSecurityStatus) => {
    if (status.operation?.result) setRecord(status.operation.result);
  }, []);
  const command = useCdnSecurity(request, domain, serverOperation, apply);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const status = await request<{ record: EchRecord | null; operation: CdnOperation | null }>(`/application/ech?domain=${encodeURIComponent(domain)}`);
      setRecord(status.record);
      setServerOperation(status.operation);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось прочитать настройку ECH");
    } finally { setLoading(false); }
  }

  async function copy(label: string, value: string) {
    try { await navigator.clipboard.writeText(value); setCopied(label); }
    catch { setError("Не удалось скопировать. Выделите значение и скопируйте вручную."); }
  }

  return <div className="networkEch">
    <button type="button" className="ghostButton" aria-expanded={open} onClick={() => { setOpen(!open); if (!open) void load(); }}>ECH / DNS-запись</button>
    {open && <section aria-label={`ECH для ${domain}`}>
      <strong>ECH для {domain}</strong>
      <p>Создаёт ключи ECH на сервере и готовую HTTPS-запись для вашего DNS-провайдера. Обычный TLS-сертификат выпускается отдельно и автоматически.</p>
      {route === "proxy_or_cdn" ? <p>Домен направлен через внешний прокси/CDN. Включите ECH у этого провайдера: ключи нашего VPS не подходят для его TLS-соединения.</p> : <button type="button" className="primaryButton" disabled={loading || command.pending || route === "unresolved"} onClick={() => void command.prepareEch(domain)}>{command.pending ? "Подготовка ECH…" : record ? "Проверить настройку сервера" : "Подготовить ECH"}</button>}
      <button type="button" className="ghostButton" disabled={loading || command.pending} onClick={() => void load()}>Обновить данные</button>
      {loading && <p role="status">Загрузка…</p>}
      {error && <p role="alert">{error}</p>}
      {command.operation && <p role="status">{command.operation.message}</p>}
      {record && <>
        <p>Добавьте запись в зоне домена. Если DNS-панель сама дописывает зону, укажите только относительное имя: например, <code>cdn</code> для зоны <code>example.com</code>.</p>
        <dl>{([
          ["Полное имя", record.domain], ["Type", record.type], ["Priority", String(record.priority)],
          ["Target name", record.target], ["TTL", String(record.ttl)], ["Service Parameters", record.parameters], ["Record content (если одно поле)", record.content],
        ] as [string, string][]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd><code>{value}</code><button type="button" className="ghostButton" onClick={() => void copy(label, value)}>{copied === label ? "Скопировано" : "Копировать"}</button></dd></div>)}</dl>
        <p>TTL можно увеличить до минимального значения провайдера. A/AAAA-запись сохраняется. После публикации и обновления DNS включите ECH в настройках устройства и обновите подписку.</p>
        <p>Внешнее имя ECH: <code>{record.public_name}</code>. Ключи управляются сервером; после их ротации обновите HTTPS-запись. Здесь всегда можно получить текущие параметры. Данные DNS автоматически не изменяются.</p>
      </>}
    </section>}
    <OperationNotifications active={false} command={command.operation} onRecheck={command.recheck} onDismiss={command.dismiss} />
  </div>;
}
