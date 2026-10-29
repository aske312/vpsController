"use client";

import { useState } from "react";
import type { RelayHost, RelayProfile, RelayStatus } from "./relay-state";
import { routeNeedsAttention } from "./route-library-state";

const routeStates = { listening: "Порт слушает", failed: "Ошибка", missing: "Пересылка отсутствует", drift: "Настройки расходятся", unknown: "Нет данных" };
const PAGE_SIZE = 5;

export function compactPorts(values: number[]) {
  const ports = [...new Set(values)].sort((a, b) => a - b);
  const ranges: string[] = [];
  for (let i = 0; i < ports.length; i++) {
    const start = ports[i];
    while (i + 1 < ports.length && ports[i + 1] === ports[i] + 1) i++;
    ranges.push(start === ports[i] ? String(start) : `${start}–${ports[i]}`);
  }
  return ranges.join(", ") || "—";
}

export function RelayProfilePicker({ host, profiles, selected, onChange }: { host: RelayHost; profiles: RelayProfile[]; selected: string; onChange: (id: string) => void }) {
  const choices = useChoices(profiles, selected, profile => `${profile.name} ${profile.channels.flatMap(channel => channel.route_ids).map(id => host.routes.find(route => route.id === id)?.listen_port || "").join(" ")}`);
  return <div className="relayProfilePicker">
    <div className="relayConsumerHeading"><h3>Профили <small>{profiles.length}</small></h3><small>Каналов через этот Relay / всего</small></div>
    <small className="relayProfilePortsLabel">Под счётчиками — входные порты Relay</small>
    {(profiles.length > PAGE_SIZE || choices.query) && <ChoiceSearch query={choices.query} onChange={choices.search} label="Поиск профиля Relay" placeholder="Профиль или порт" />}
    <div className="relayProfileChoices" role="group" aria-label="Профили Relay">{choices.visible.map(profile => {
      const configured = profile.channels.filter(channel => channel.host_id === host.id);
      const ids = new Set(configured.flatMap(channel => channel.route_ids));
      const routes = host.routes.filter(route => ids.has(route.id));
      const attention = profile.relay_failures?.some(error => error.host_id === host.id) || routes.some(routeNeedsAttention) || configured.some(channel => !channel.route_ids.length);
      return <button type="button" className="relayProfileChoice" key={profile.id} aria-pressed={selected === profile.id} onClick={() => onChange(profile.id)}>
        <span className="relayProfileName"><strong>{profile.name}</strong><small data-state={host.state !== "connected" ? "unknown" : attention ? "warning" : "listening"}>{host.state !== "connected" ? "Нет связи с Agent" : attention ? "Требует внимания" : "Пересылки настроены"}</small></span>
        <span className="relayProfileTransports">{(["tcp", "udp"] as const).map(transport => <span key={transport}>
          <span className="relayProfileCount">{transport.toUpperCase()} <strong>{configured.filter(channel => channel.transport === transport).length}/{profile.channels.filter(channel => channel.transport === transport).length}</strong></span>
          <code title="Входные порты Relay">{compactPorts(routes.filter(route => route.transport === transport).map(route => route.listen_port))}</code>
        </span>)}</span>
      </button>;
    })}</div>
    {!choices.count && <p>Профили не найдены.</p>}
    <ChoicePages {...choices} label="Профили Relay" onChange={choices.setPage} />
    {choices.selection && !choices.selectedVisible && <ChoiceSelection label={choices.selection.name} hidden onReveal={choices.reveal} />}
  </div>;
}

function useChoices<T extends { id: string }>(items: T[], selected: string, describe: (item: T) => string) {
  const [query, setQuery] = useState("");
  const [requestedPage, setPage] = useState(() => Math.max(1, Math.ceil((items.findIndex(item => item.id === selected) + 1) / PAGE_SIZE)));
  const words = query.trim().toLocaleLowerCase("ru-RU").split(/\s+/).filter(Boolean);
  const filtered = items.filter(item => words.every(word => describe(item).toLocaleLowerCase("ru-RU").includes(word)));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const page = Math.min(requestedPage, pages);
  const visible = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const selection = items.find(item => item.id === selected);
  return { query, page, pages, visible, selection, count: filtered.length,
    selectedVisible: visible.some(item => item.id === selected), setPage,
    search: (value: string) => { setQuery(value); setPage(1); },
    reveal: () => { setQuery(""); setPage(Math.max(1, Math.ceil((items.findIndex(item => item.id === selected) + 1) / PAGE_SIZE))); },
  };
}

function ChoiceSearch({ query, onChange, label, placeholder }: { query: string; onChange: (value: string) => void; label: string; placeholder: string }) {
  return <div className="relayPickerSearch"><input type="search" aria-label={label} placeholder={placeholder} value={query} onChange={event => onChange(event.target.value)} />{query && <button type="button" aria-label={`Очистить: ${label}`} onClick={() => onChange("")}>×</button>}</div>;
}

function ChoicePages({ page, pages, count, label, onChange }: { page: number; pages: number; count: number; label: string; onChange: (page: number) => void }) {
  if (pages === 1) return null;
  return <nav className="relayPickerPages" aria-label={`Страницы: ${label}`}><span aria-live="polite">{(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, count)} из {count}</span><div>
    <button type="button" aria-label={`${label}: предыдущая страница`} disabled={page === 1} onClick={() => onChange(page - 1)}>‹</button>
    <button type="button" aria-label={`${label}: следующая страница`} disabled={page === pages} onClick={() => onChange(page + 1)}>›</button>
  </div></nav>;
}

function ChoiceSelection({ label, hidden, onReveal }: { label: string; hidden: boolean; onReveal: () => void }) {
  return <div className="relayPickerSelection"><span title={label}><small>Выбрано</small><strong>{label}</strong></span>{hidden && <button type="button" onClick={onReveal}>К выбранному</button>}</div>;
}

export function RelayRoutePicker({ host, selected, onChange }: { host: RelayHost; selected: string; onChange: (id: string) => void }) {
  const choices = useChoices(host.routes, selected, route => `${route.transport} ${route.listen_port} ${route.target_port} ${routeStates[route.state]}`);
  return <div className="relayRoutePicker">
    <div className="relayConsumerHeading"><h4>Маршруты Relay <span>{host.routes.length}</span></h4>{choices.query && <small aria-live="polite">Найдено: {choices.count}</small>}</div>
    {(host.routes.length > PAGE_SIZE || choices.query) && <ChoiceSearch query={choices.query} onChange={choices.search} label="Поиск маршрута Relay" placeholder="Порт или протокол" />}
    <div className="relayRouteChoices" role="group" aria-label="Маршрут Relay">
    {choices.visible.map(route => {
      const current = host.state === "connected";
      const attention = current && routeNeedsAttention(route);
      const sessions = current && route.sessions !== undefined ? route.sessions.toLocaleString("ru-RU") : "—";
      return <button type="button" key={route.id} className="relayRouteChoice" aria-pressed={selected === route.id} onClick={() => onChange(route.id)}>
        <span className="relayRouteChoiceIdentity"><strong>{route.transport.toUpperCase()} · {route.listen_port}</strong><small>VPS · {route.target_port}</small></span>
        <span className="relayRouteChoiceStatus" data-state={!current ? "unknown" : attention ? "warning" : route.state}>{!current ? "Нет связи с Agent" : attention ? "Требует внимания" : routeStates[route.state]}</span>
        <span className="relayRouteChoiceSessions"><strong>{sessions}</strong><small>Сессии</small></span>
      </button>;
    })}
    {!choices.count && <p>Маршруты не найдены.</p>}
    </div>
    <ChoicePages {...choices} label="Маршруты Relay" onChange={choices.setPage} />
    {choices.selection && !choices.selectedVisible && <ChoiceSelection label={`${choices.selection.transport.toUpperCase()} · ${choices.selection.listen_port} → VPS · ${choices.selection.target_port}`} hidden onReveal={choices.reveal} />}
  </div>;
}

export function RelayConsumerPicker({ consumers, selected, disabled, onChange }: { consumers: NonNullable<RelayStatus["consumers"]>; selected: string; disabled: boolean; onChange: (id: string) => void }) {
  const choices = useChoices(consumers, selected, item => `${item.label} ${item.id}`);
  return <div className="relayConsumerPicker">
    <div className="relayConsumerHeading"><h4>Подключение для проверки <span>{consumers.length}</span></h4>{choices.query && <small aria-live="polite">Найдено: {choices.count}</small>}</div>
    {(consumers.length > PAGE_SIZE || choices.query) && <ChoiceSearch query={choices.query} onChange={choices.search} label="Поиск подключения для проверки" placeholder="Найти подключение" />}
    {consumers.length ? <>
      <div className="relayConsumerChoices" role="group" aria-label="Подключение для проверки">{choices.visible.map(item => <button type="button" key={item.id} title={item.label} aria-pressed={selected === item.id} disabled={disabled} onClick={() => onChange(item.id)}><span>{item.label}</span><i aria-hidden="true">{selected === item.id ? "✓" : ""}</i></button>)}</div>
      {!choices.count && <p>Подключения не найдены.</p>}
      <ChoicePages {...choices} label="Подключения" onChange={choices.setPage} />
      {choices.selection && <ChoiceSelection label={choices.selection.label} hidden={!choices.selectedVisible} onReveal={choices.reveal} />}
    </> : <p>Нет доступных подключений для проверки.</p>}
  </div>;
}
