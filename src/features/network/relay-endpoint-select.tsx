"use client";

import { useEffect, useState } from "react";
import type { NetworkRequest } from "./network-api";
import { relaySnapshot, type RelayStatus } from "./relay-state";

export function useRelayInventory(request: NetworkRequest, enabled = true) {
  const [status, setStatus] = useState<RelayStatus | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const controller = new AbortController();
    void request("/network/relays?view=inventory", { cache: "no-store", signal: controller.signal }).then(relaySnapshot).then(data => {
      if (!cancelled) { setStatus(data); setFailed(false); }
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; controller.abort(); };
  }, [request, enabled]);
  return { status, failed };
}

/** Selection uses the saved inventory; liveness belongs to ROUTES diagnostics. */
export function RelayEndpointSelect({ request, inventory, value, onChange, disabled = false, unavailable, transport }: {
  request: NetworkRequest; inventory?: ReturnType<typeof useRelayInventory>; value: string; onChange: (value: string) => void; disabled?: boolean; unavailable?: string; transport?: string;
}) {
  const ownInventory = useRelayInventory(request, inventory === undefined);
  const { status, failed } = inventory || ownInventory;
  const known = status?.hosts.some(host => host.id === value);
  return <label className="relayEndpointSelector"><span>Адрес подключения</span>
    <select value={value} disabled={disabled || failed || Boolean(unavailable) || !status} onChange={event => onChange(event.target.value)}>
      <option value="">Напрямую к этому VPS</option>
      {value && !known && <option value={value}>{status && !failed ? "Relay недоступен · узел удалён" : "Сохранённый Relay · состояние неизвестно"}</option>}
      {status?.hosts.map(host => <option key={host.id} value={host.id}>{host.client_address || new URL(host.agent_url).hostname} · {host.name}</option>)}
    </select>
    <small>{unavailable || (failed ? "Не удалось получить список Relay. Сохранённый выбор не изменён; откройте экран заново для повторной загрузки." : !status ? "Получаем список Relay…" : value && !known ? "Узел удалён. Настройки сохранены; выберите Relay для восстановления подключения." : value ? `${transport ? transport+" · " : ""}Пересылка и внешний порт будут настроены автоматически при сохранении.` : "Подключённые в Сеть → ROUTES узлы доступны здесь по IP/домену.")}</small>
  </label>;
}
