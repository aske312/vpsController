"use client";

import { useEffect, useState } from "react";
import type { NetworkRequest } from "./network-api";
import { relaySnapshot, relayAddresses, type RelayStatus } from "./relay-state";

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
export function RelayEndpointSelect({ request, inventory, value, address = "", onChange, disabled = false, unavailable, transport }: {
  request: NetworkRequest; inventory?: ReturnType<typeof useRelayInventory>; value: string; address?: string; onChange: (value: string, address?: string) => void; disabled?: boolean; unavailable?: string; transport?: string;
}) {
  const ownInventory = useRelayInventory(request, inventory === undefined);
  const { status, failed } = inventory || ownInventory;
  const known = status?.hosts.some(host => host.id === value);
  const choices = status?.hosts.flatMap(host => relayAddresses(host).map((address, index) => ({ host, address, value: index ? JSON.stringify([host.id, address]) : host.id }))) || [];
  const selected = address ? choices.find(choice => choice.host.id === value && choice.address === address)?.value || JSON.stringify([value, address]) : value;
  return <label className="relayEndpointSelector"><span>Адрес подключения</span>
    <select value={selected} disabled={disabled || failed || Boolean(unavailable) || !status} onChange={event => {
      const choice = choices.find(item => item.value === event.target.value);
      if (choice) onChange(choice.host.id, choice.address);
      else if (!event.target.value) onChange("", "");
    }}>
      <option value="">Напрямую к этому VPS</option>
      {value && (!known || !choices.some(choice => choice.value === selected)) && <option value={selected}>{address || "Сохранённый Relay"} · сохранённый адрес</option>}
      {choices.map(choice => <option key={choice.value} value={choice.value}>{choice.address} · {choice.host.name}</option>)}
    </select>
    <small>{unavailable || (failed ? "Не удалось получить список Relay. Сохранённый выбор не изменён; откройте экран заново для повторной загрузки." : !status ? "Получаем список Relay…" : value && !known ? "Узел удалён. Настройки сохранены; выберите Relay для восстановления подключения." : value ? `${transport ? transport+" · " : ""}Пересылка и внешний порт будут настроены автоматически при сохранении.` : "Подключённые в Сеть → ROUTES узлы доступны здесь по IP/домену.")}</small>
  </label>;
}
