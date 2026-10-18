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
export function RelayEndpointSelect({ request, inventory, value, address = "", onChange, disabled = false, unavailable, transport, cdn }: {
  request: NetworkRequest; inventory?: ReturnType<typeof useRelayInventory>; value: string; address?: string; onChange: (value: string, address?: string) => void; disabled?: boolean; unavailable?: string; transport?: string;
  cdn?: { domains: string[]; value: string; disabled?: boolean; onChange: (domain: string) => void };
}) {
  const ownInventory = useRelayInventory(request, inventory === undefined);
  const { status, failed } = inventory || ownInventory;
  const known = status?.hosts.some(host => host.id === value);
  const choices = status?.hosts.flatMap(host => relayAddresses(host).map((address, index) => ({ host, address, value: index ? JSON.stringify([host.id, address]) : host.id }))) || [];
  const selected = value ? address ? choices.find(choice => choice.host.id === value && choice.address === address)?.value || JSON.stringify([value, address]) : value : cdn?.value ? JSON.stringify(["cdn", cdn.value]) : "";
  const cdnDomains = [...new Set([...(cdn?.value ? [cdn.value] : []), ...(cdn?.domains || [])])];
  const remoteCdn = choices.filter(choice => choice.host.addresses?.cdn_domains?.includes(choice.address));
  const relays = choices.filter(choice => !choice.host.addresses?.cdn_domains?.includes(choice.address));
  return <label className="relayEndpointSelector"><span>Адрес подключения</span>
    <select value={selected} disabled={disabled || Boolean(unavailable) || (failed || !status) && !cdnDomains.length} onChange={event => {
      const choice = choices.find(item => item.value === event.target.value);
      if (choice) onChange(choice.host.id, choice.address);
      else if (cdn && cdnDomains.some(domain => JSON.stringify(["cdn", domain]) === event.target.value)) cdn.onChange(JSON.parse(event.target.value)[1]);
      else if (!event.target.value) onChange("", "");
    }}>
      <option value="">Напрямую к этому VPS</option>
      {value && (!known || !choices.some(choice => choice.value === selected)) && <option value={selected}>{address || "Сохранённый Relay"} · сохранённый адрес</option>}
      {(cdnDomains.length > 0 || remoteCdn.length > 0) && <optgroup label="CDN">
        {cdnDomains.map(domain => <option key={domain} value={JSON.stringify(["cdn", domain])} disabled={cdn?.disabled} title={cdn?.disabled ? "CDN-домен используется с VLESS CDN" : undefined}>{domain} · этот VPS</option>)}
        {remoteCdn.map(choice => <option key={choice.value} value={choice.value} disabled title="Для этого PRO ещё не настроен CDN-вход на Relay">{choice.address} · {choice.host.name} · требуется настройка CDN-входа</option>)}
      </optgroup>}
      {relays.length > 0 && <optgroup label="Relay">{relays.map(choice => <option key={choice.value} value={choice.value}>{choice.address} · {choice.host.name}</option>)}</optgroup>}
    </select>
    <small>{unavailable || (failed ? "Не удалось получить список Relay. Сохранённый выбор не изменён; откройте экран заново для повторной загрузки." : !status ? "Получаем список Relay…" : value && !known ? "Узел удалён. Настройки сохранены; выберите Relay для восстановления подключения." : value ? `${transport ? transport+" · " : ""}Пересылка и внешний порт будут настроены автоматически при сохранении.` : cdn?.value ? "VLESS через выбранный CDN-домен." : "Подключённые в Сеть → ROUTES узлы доступны здесь по IP/домену.")}</small>
  </label>;
}
