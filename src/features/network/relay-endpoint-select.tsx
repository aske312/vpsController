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
export function RelayEndpointSelect({ request, inventory, value, address = "", onChange, disabled = false, unavailable, transport, cdn, modeControls = false }: {
  request: NetworkRequest; inventory?: ReturnType<typeof useRelayInventory>; value: string; address?: string; onChange: (value: string, address?: string) => void; disabled?: boolean; unavailable?: string; transport?: string;
  cdn?: { domains: string[]; value: string; disabled?: boolean; onChange: (domain: string) => void };
  modeControls?: boolean;
}) {
  const [query, setQuery] = useState("");
  const ownInventory = useRelayInventory(request, inventory === undefined);
  const { status, failed } = inventory || ownInventory;
  const known = status?.hosts.some(host => host.id === value);
  const choices = status?.hosts.flatMap(host => relayAddresses(host).map((address, index) => ({ host, address, value: index ? JSON.stringify([host.id, address]) : host.id }))) || [];
  const selected = value ? address ? choices.find(choice => choice.host.id === value && choice.address === address)?.value || JSON.stringify([value, address]) : value : cdn?.value ? JSON.stringify(["cdn", cdn.value]) : "";
  const cdnDomains = [...new Set([...(cdn?.value ? [cdn.value] : []), ...(cdn?.domains || [])])];
  const remoteCdn = choices.filter(choice => choice.host.addresses?.cdn_domains?.includes(choice.address));
  const relays = choices.filter(choice => !choice.host.addresses?.cdn_domains?.includes(choice.address));
  const mode = value ? "relay" : cdn?.value ? "cdn" : "direct";
  const matches = (text: string) => text.toLowerCase().includes(query.trim().toLowerCase());
  return <div className="relayEndpointSelector"><span>Адрес подключения</span>
    {modeControls && <>
      <span className="mihomoAddressModes" role="group" aria-label="Способ подключения">
        <button type="button" aria-pressed={mode === "direct"} disabled={disabled || Boolean(unavailable)} onClick={() => { setQuery(""); onChange("", ""); }}>Прямой</button>
        <button type="button" aria-pressed={mode === "cdn"} disabled={disabled || Boolean(unavailable) || cdn?.disabled || !cdnDomains.length} onClick={() => { setQuery(""); if (cdn) cdn.onChange(cdn.value || cdnDomains[0]); }}>CDN</button>
        <button type="button" aria-pressed={mode === "relay"} disabled={disabled || Boolean(unavailable) || !relays.length} onClick={() => { setQuery(""); if (!value && relays[0]) onChange(relays[0].host.id, relays[0].address); }}>Relay</button>
      </span>
      {mode !== "direct" && <input type="search" aria-label="Поиск адреса подключения" placeholder="Найти IP, домен или узел…" value={query} onChange={event => setQuery(event.target.value)} disabled={disabled} />}
    </>}
    {(!modeControls || mode !== "direct") && <>
    <select aria-label="Адрес подключения" value={selected} disabled={disabled || Boolean(unavailable) || (failed || !status) && !cdnDomains.length} onChange={event => {
      const choice = choices.find(item => item.value === event.target.value);
      if (choice) onChange(choice.host.id, choice.address);
      else if (cdn && cdnDomains.some(domain => JSON.stringify(["cdn", domain]) === event.target.value)) cdn.onChange(JSON.parse(event.target.value)[1]);
      else if (!event.target.value) onChange("", "");
    }}>
      {!modeControls && <option value="">Напрямую к этому VPS</option>}
      {value && (!known || !choices.some(choice => choice.value === selected)) && <option value={selected}>{address || "Сохранённый Relay"} · сохранённый адрес</option>}
      {(!modeControls || mode === "cdn") && (cdnDomains.length > 0 || remoteCdn.length > 0) && <optgroup label="CDN">
        {cdnDomains.filter(domain => domain === cdn?.value || matches(domain)).map(domain => <option key={domain} value={JSON.stringify(["cdn", domain])} disabled={cdn?.disabled} title={cdn?.disabled ? "CDN-домен используется с VLESS CDN" : undefined}>{domain} · этот VPS</option>)}
        {remoteCdn.filter(choice => matches(`${choice.address} ${choice.host.name}`)).map(choice => <option key={choice.value} value={choice.value} disabled title="Для этого PRO ещё не настроен CDN-вход на Relay">{choice.address} · {choice.host.name} · требуется настройка CDN-входа</option>)}
      </optgroup>}
      {(!modeControls || mode === "relay") && relays.length > 0 && <optgroup label="Relay">{relays.filter(choice => choice.value === selected || matches(`${choice.address} ${choice.host.name}`)).map(choice => <option key={choice.value} value={choice.value}>{choice.address} · {choice.host.name}</option>)}</optgroup>}
    </select>
    </>}
    <small>{unavailable || (failed ? "Не удалось получить список Relay. Сохранённый выбор не изменён; откройте экран заново для повторной загрузки." : !status ? "Получаем список Relay…" : value && !known ? "Узел удалён. Настройки сохранены; выберите Relay для восстановления подключения." : value ? `${transport ? transport+" · " : ""}Пересылка и внешний порт будут настроены автоматически при сохранении.` : cdn?.value ? "VLESS через выбранный CDN-домен." : "Подключённые в Сеть → ROUTES узлы доступны здесь по IP/домену.")}</small>
  </div>;
}
