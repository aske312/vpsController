"use client";

import { useCallback, useState } from "react";
import { RouteTypeIcon } from "./route-type-icon";
import { useCdnSecurity } from "../application/use-cdn-security";
import { OperationNotifications } from "../../control-panel/components/operation-notifications";
import { useNotifier } from "../../shared/notifications/notification-center";
import type { CdnOperation, CdnSecurityStatus, EchRecord } from "../../shared/lib/cdn-security-operation";
import type { NetworkRequest } from "./network-api";

export function NetworkEch({ domain, route, request }: { domain: string; route: string; request: NetworkRequest }) {
  const [open, setOpen] = useState(false);
  const [record, setRecord] = useState<EchRecord | null>(null);
  const [serverOperation, setServerOperation] = useState<CdnOperation | null>(null);
  const { error: notifyError } = useNotifier("network-ech", `ECH · ${domain}`);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState("");
  const [capability, setCapability] = useState<{ supported: boolean; message: string } | null>(null);
  const apply = useCallback((status: CdnSecurityStatus) => {
    if (status.operation?.result) setRecord(status.operation.result);
  }, []);
  const command = useCdnSecurity(request, domain, serverOperation, apply);

  async function load() {
    setLoading(true);
    try {
      const status = await request<{ record: EchRecord | null; operation: CdnOperation | null; capability: { supported: boolean; message: string } }>(`/application/ech?domain=${encodeURIComponent(domain)}`);
      setRecord(status.record);
      setServerOperation(status.operation);
      setCapability(status.capability);
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Не удалось прочитать настройку ECH");
    } finally { setLoading(false); }
  }

  async function copy(label: string, value: string) {
    try { await navigator.clipboard.writeText(value); setCopied(label); }
    catch { notifyError("Не удалось скопировать. Выделите значение и скопируйте вручную."); }
  }

  return <><details className="networkEch routeLibraryDisclosure routeEchDisclosure" open={open} onToggle={event => { const next = event.currentTarget.open; if (next !== open) { setOpen(next); if (next) void load(); } }}>
    <summary><span className="routeDisclosureLabel"><RouteTypeIcon kind="tls" />ECH / DNS-запись</span><span className="routeDisclosureToggle" aria-hidden="true">+</span></summary>
    {open && <section aria-label={`ECH для ${domain}`}>
      <header><h4>Защита имени подключения</h4><p>Настройка ECH и запись HTTPS для DNS-провайдера.</p></header>
      <p>Создаёт ключи ECH на сервере и готовую HTTPS-запись для вашего DNS-провайдера. Обычный TLS-сертификат выпускается отдельно и автоматически.</p>
      {route === "proxy_or_cdn" ? <p>Домен направлен через внешний прокси/CDN. Включите ECH у этого провайдера: ключи нашего VPS не подходят для его TLS-соединения.</p> : <button type="button" className="primaryButton" disabled={loading || command.pending || route === "unresolved" || !capability?.supported} onClick={() => void command.prepareEch(domain)}>{command.pending ? "Подготовка ECH…" : record ? "Проверить настройку сервера" : "Подготовить ECH"}</button>}
      {capability?.message && <p role="status">{capability.message}</p>}
      <button type="button" className="ghostButton" disabled={loading || command.pending} onClick={() => void load()}>Обновить данные</button>
      {loading && <p role="status">Загрузка…</p>}
      {record && <>
        <p>Добавьте запись в зоне домена. Если DNS-панель сама дописывает зону, укажите только относительное имя: например, <code>cdn</code> для зоны <code>example.com</code>.</p>
        <div className="routeEchRecord"><div className="routeEchRecordTitle"><strong>Запись для DNS</strong><span>{record.type}</span></div>
          <dl className="routeEchFields">{([["Имя", record.domain], ["Значение", record.content]] as [string, string][]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd><code>{value}</code><button type="button" onClick={() => void copy(label, value)}>{copied === label ? "Скопировано" : "Копировать"}</button></dd></div>)}</dl>
          <small>Если провайдер сам дописывает зону, используйте относительное имя, например cdn.</small>
        </div>
        <details className="routeEchAdvanced"><summary>Для DNS-панелей с отдельными полями</summary><dl className="routeEchFields">{([["Приоритет", String(record.priority)], ["Target", record.target], ["TTL", String(record.ttl)], ["Параметры", record.parameters]] as [string, string][]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd><code>{value}</code><button type="button" onClick={() => void copy(label, value)}>{copied === label ? "Скопировано" : "Копировать"}</button></dd></div>)}</dl></details>
        <p>TTL можно увеличить до минимального значения провайдера. A/AAAA-запись сохраняется. После публикации и обновления DNS включите ECH в настройках устройства и обновите подписку.</p>
        <p>Внешнее имя ECH: <code>{record.public_name}</code>. Ключи управляются сервером; после их ротации обновите HTTPS-запись. Здесь всегда можно получить текущие параметры. Данные DNS автоматически не изменяются.</p>
      </>}
    </section>}
    </details><OperationNotifications active={false} command={command.operation} onRecheck={command.recheck} onDismiss={command.dismiss} />
  </>;
}
