"use client";

import { useEffect, useState } from "react";
import type { NetworkRequest } from "./network-api";
import { relaySnapshot, type RelayStatus } from "./relay-state";

/** The connection chooses a host; its producer owns sockets and port allocation. */
export function RelayEndpointSelect({ request, value, onChange, disabled = false, unavailable, transport }: {
  request: NetworkRequest; value: string; onChange: (value: string) => void; disabled?: boolean; unavailable?: string; transport?: string;
}) {
  const [status, setStatus] = useState<RelayStatus | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void request("/network/relays", { cache: "no-store" }).then(relaySnapshot).then(data => {
      if (!cancelled) { setStatus(data); setFailed(false); }
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [request]);
  const known = status?.hosts.some(host => host.id === value);
  return <label className="relayEndpointSelector"><span>Адрес подключения</span>
    <select value={value} disabled={disabled || failed || Boolean(unavailable) || !status || status.pending} onChange={event => onChange(event.target.value)}>
      <option value="">Напрямую к этому VPS</option>
      {value && !known && <option value={value}>{status && !failed ? "Relay недоступен · узел удалён" : "Сохранённый Relay · состояние неизвестно"}</option>}
      {status?.hosts.map(host => <option key={host.id} value={host.id} disabled={host.state !== "connected"}>{host.client_address || new URL(host.agent_url).hostname} · {host.name}{host.state !== "connected" ? " · UNKNOWN" : ""}</option>)}
    </select>
    <small>{unavailable || (failed ? "Не удалось получить Relay. Сохранённый выбор не изменён; повторно откройте настройки после восстановления связи." : !status ? "Получаем подключённые Relay…" : status.pending ? "Сначала подтвердите результат предыдущего изменения Relay в ROUTES." : value && !known ? "Узел удалён. Настройки сохранены; выберите доступный Relay для восстановления подключения." : value ? `${transport ? transport+" · " : ""}Пересылка и внешний порт будут настроены автоматически при сохранении.` : "Подключённые в Сеть → ROUTES узлы доступны здесь по IP/домену.")}</small>
  </label>;
}
