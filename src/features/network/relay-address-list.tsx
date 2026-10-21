"use client";

import { useState } from "react";
import { useNotifier } from "../../shared/notifications/notification-center";
import type { RelayControl } from "./network-relays";
import { relayAddresses, type RelayHost } from "./relay-state";
import { resourceIp } from "./resource-country";
import { RouteTypeIcon } from "./route-type-icon";

export function RelayAddressList({ host, relay }: { host: RelayHost; relay: RelayControl }) {
  const addresses = relayAddresses(host);
  const problems = host.address_checks?.filter(item => item.state === "unresolved" || item.state === "mismatch").length || 0;
  const [copied, setCopied] = useState("");
  const { error } = useNotifier("relay-address-copy", "Адрес Relay");
  return <details className="routeLibraryDisclosure routeAddressDisclosure" open={problems > 0 || undefined}><summary><span className="routeDisclosureLabel"><RouteTypeIcon kind="ip" />Адреса узла <small>{addresses.length}</small>{problems > 0 && <small className="routeAddressIssue">Замечаний: {problems}</small>}</span><span className="routeDisclosureToggle" aria-hidden="true">+</span></summary><div>
    <ul className="routeNodeAddresses">{addresses.map((address, index) => {
      const ip = resourceIp(address), check = host.address_checks?.find(item => item.domain === address);
      const type = ip ? ip.includes(":") ? "IPv6" : "IPv4" : "Домен";
      const dns = check ? ({ verified: "DNS подтверждён", dns_only: "DNS определён", unresolved: "Нет DNS", mismatch: "DNS указывает на другой сервер" })[check.state] : "";
      return <li key={address}><div className="routeNodeAddress"><RouteTypeIcon kind={ip ? "ip" : "domain"} /><div><code>{address}</code><small>{type}{index === 0 ? " · Для клиентов" : ""}{dns ? ` · ${dns}` : ""}</small></div></div><div className="routeAddressActions"><button type="button" aria-label={`Копировать ${address}`} onClick={async () => { try { await navigator.clipboard.writeText(address); setCopied(address); } catch { error("Не удалось скопировать адрес. Выделите его и скопируйте вручную."); } }}>{copied === address ? "Скопировано" : "Копировать"}</button><button type="button" disabled={relay.locked || relay.status?.pending} onClick={() => relay.setAddressConfirmation({ host, address })}>Исключить</button></div></li>;
    })}</ul><small>Исключение убирает адрес из выбора для новых подключений. Существующие подключения сохраняются.</small>
  </div></details>;
}
