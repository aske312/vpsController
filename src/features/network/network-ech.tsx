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

  const throughCdn = route === "proxy_or_cdn";
  return <>
    {throughCdn && <div className="routeCdnEchProvider"><span><RouteTypeIcon kind="tls" />ECH</span><span>У CDN-провайдера</span></div>}
    <details className="networkEch routeLibraryDisclosure routeEchDisclosure" open={open} onToggle={event => { const next = event.currentTarget.open; if (next !== open) { setOpen(next); if (next) void load(); } }}>
    <summary><span className="routeDisclosureLabel">{throughCdn ? "Дополнительно: запись VPS" : <><RouteTypeIcon kind="tls" />ECH на VPS</>}</span><span className="routeDisclosureToggle" aria-hidden="true">+</span></summary>
    {open && <section aria-label={`ECH для ${domain}`}>
      {throughCdn && <p className="routeCdnHint">Только для прямого TLS-соединения с VPS. Для CDN эту запись публиковать не нужно.</p>}
      <div className="routeEchActions">
        {!throughCdn && <button type="button" className="routeActionPrimary" disabled={loading || command.pending || route === "unresolved" || !capability?.supported} onClick={() => void command.prepareEch(domain)}>{command.pending ? "Подготовка ECH…" : record ? "Проверить ECH" : "Подготовить ECH"}</button>}
        <button type="button" disabled={loading || command.pending} onClick={() => void load()}>Обновить</button>
      </div>
      {!throughCdn && capability?.message && <p className="routeCdnHint" role="status">{capability.message}</p>}
      {loading && <p role="status">Загрузка…</p>}
      {record && <>
        <div className="routeEchRecord"><div className="routeEchRecordTitle"><strong>{route === "proxy_or_cdn" ? "HTTPS-запись VPS" : "HTTPS-запись для DNS"}</strong><span>{record.type}</span></div>
          <dl className="routeEchFields">{([["Имя", record.domain], ["Значение", record.content]] as [string, string][]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd><code>{value}</code><button type="button" onClick={() => void copy(label, value)}>{copied === label ? "Скопировано" : "Копировать"}</button></dd></div>)}</dl>
        </div>
        <details className="routeCdnHelp"><summary>Отдельные поля DNS<span aria-hidden="true">+</span></summary><div><dl className="routeEchFields">{([["Приоритет", String(record.priority)], ["Target", record.target], ["TTL", String(record.ttl)], ["Параметры", record.parameters]] as [string, string][]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd><code>{value}</code><button type="button" onClick={() => void copy(label, value)}>{copied === label ? "Скопировано" : "Копировать"}</button></dd></div>)}</dl></div></details>
        <dl className="routeCdnHelpMeta"><div><dt>Внешнее имя ECH</dt><dd><code>{record.public_name}</code></dd></div></dl>
      </>}
    </section>}
    </details><OperationNotifications active={false} command={command.operation} onRecheck={command.recheck} onDismiss={command.dismiss} />
  </>;
}
