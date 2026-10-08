"use client";

import { useMemo, useState } from "react";
import { protocolDelivery } from "./connection-profile";
import { ProtocolIcon } from "./protocol-icon";
import type { Client, Protocol } from "./page";

type Filter = "all" | "stable" | "attention" | "offline";

type Props = {
  clients: Client[];
  protocols: Protocol[];
  busy: boolean;
  onNew(): void;
  onRemove(id: string): void;
};

const bytes = (value: number) => {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const order = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** order).toFixed(order ? 1 : 0)} ${units[order]}`;
};

const activity = (seconds?: number) => {
  if (seconds === undefined || seconds === null) return "нет данных";
  if (seconds < 60) return `${seconds} сек. назад`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин. назад`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} ч. назад`;
  return `${Math.floor(seconds / 86400)} дн. назад`;
};

function clientState(client: Client): Exclude<Filter, "all"> {
  if (client.update_state || client.quality === "warning" || client.quality === "error") return "attention";
  if (client.quality === "stable") return "stable";
  return "offline";
}

export function ConnectionsView({ clients, protocols, busy, onNew, onRemove }: Props) {
  const [query, setQuery] = useState("");
  const [protocol, setProtocol] = useState<"all" | Protocol>("all");
  const [filter, setFilter] = useState<Filter>("all");
  const normalized = query.trim().toLocaleLowerCase("ru");
  const counts = useMemo(() => ({
    stable: clients.filter((client) => clientState(client) === "stable").length,
    attention: clients.filter((client) => clientState(client) === "attention").length,
    offline: clients.filter((client) => clientState(client) === "offline").length,
  }), [clients]);
  const visible = useMemo(() => clients.filter((client) => {
    const matchesText = !normalized || [client.name, client.address, client.endpoint, client.public_key, protocolDelivery[client.protocol].title]
      .some((value) => value?.toLocaleLowerCase("ru").includes(normalized));
    return matchesText && (protocol === "all" || client.protocol === protocol) && (filter === "all" || clientState(client) === filter);
  }), [clients, filter, normalized, protocol]);

  return <section className="connectionsWorkspace">
    <article className="connectionsHero">
      <div className="connectionsHeroCopy"><p className="eyebrow">ACCESS INVENTORY</p><h2>Подключения</h2><p>Персональные профили пользователей и устройств. Создание, параметры и выдача доступа собраны в одном диалоге.</p></div>
      <div className="connectionsHeroStats" aria-label="Состояние подключений">
        <button type="button" className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}><strong>{clients.length}</strong><span>всего</span></button>
        <button type="button" className={filter === "stable" ? "active" : ""} onClick={() => setFilter("stable")}><strong>{counts.stable}</strong><span>активны</span></button>
        <button type="button" className={filter === "attention" ? "active" : ""} onClick={() => setFilter("attention")}><strong>{counts.attention}</strong><span>внимание</span></button>
        <button type="button" className={filter === "offline" ? "active" : ""} onClick={() => setFilter("offline")}><strong>{counts.offline}</strong><span>без связи</span></button>
      </div>
      <button type="button" className="primaryButton connectionsNewButton" onClick={onNew} disabled={busy}>Новое подключение <span>＋</span></button>
    </article>

    <article className="panel connectionsInventory">
      <div className="connectionsInventoryHead"><div><p className="eyebrow">PERSONAL PROFILES</p><h2>Выданный доступ</h2></div><span>{visible.length} из {clients.length}</span></div>
      <div className="connectionsFilters">
        <label className="connectionSearch"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Имя, адрес или идентификатор" aria-label="Поиск подключений" /></label>
        <label><span>Протокол</span><select value={protocol} onChange={(event) => setProtocol(event.target.value as "all" | Protocol)}><option value="all">Все протоколы</option>{protocols.map((item) => <option value={item} key={item}>{protocolDelivery[item].title}</option>)}</select></label>
      </div>

      <div className="connectionsRows">
        {visible.map((client) => { const meta = protocolDelivery[client.protocol]; const tunnel = client.protocol === "wg" || client.protocol === "awg"; const state = clientState(client); return <div className={`connectionRowFlat state-${state}`} key={client.id}>
          <div className="connectionRowIdentity"><span className={`protocol ${client.protocol}`}><ProtocolIcon protocol={client.protocol} /></span><div><strong>{client.name}</strong><small>{client.endpoint || client.address || "Персональный профиль"}</small></div></div>
          <div className="connectionRowProtocol"><small>ПРОТОКОЛ</small><strong>{meta.title}</strong><span>{meta.transport}</span></div>
          <div className="connectionRowData"><small>{tunnel ? "ТРАФИК" : "ИДЕНТИФИКАТОР"}</small><strong>{tunnel ? `↓ ${bytes(client.rx_bytes)} · ↑ ${bytes(client.tx_bytes)}` : `${client.public_key.slice(0, 18)}${client.public_key.length > 18 ? "…" : ""}`}</strong><span>{tunnel ? activity(client.handshake_age_s) : "профиль выдан"}</span></div>
          <div className="connectionRowState"><i className={state} /><div><strong>{state === "stable" ? "Активно" : state === "attention" ? "Требует внимания" : tunnel ? "Нет связи" : "Выдано"}</strong><small>{client.update_message || client.quality_reason || (tunnel ? "ожидаем подключение" : "учётная запись создана")}</small></div></div>
          <button type="button" className="dangerButton" onClick={() => onRemove(client.id)} disabled={busy}>Отозвать</button>
        </div>; })}
        {!visible.length && <div className="connectionsEmpty"><span>⌕</span><strong>{clients.length ? "Ничего не найдено" : "Подключений пока нет"}</strong><p>{clients.length ? "Измените поиск или фильтры." : "Создайте отдельный профиль для первого пользователя или устройства."}</p>{!clients.length && <button type="button" className="primaryButton" onClick={onNew}>Создать подключение <span>→</span></button>}</div>}
      </div>
    </article>
  </section>;
}
