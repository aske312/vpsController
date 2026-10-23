"use client";

import { useState } from "react";
import type { RelayHost, RelayStatus } from "./relay-state";
import { routeNeedsAttention } from "./route-library-state";

const routeStates = { listening: "Порт слушает", failed: "Ошибка", missing: "Пересылка отсутствует", drift: "Настройки расходятся", unknown: "Нет данных" };

export function RelayRoutePicker({ host, selected, onChange }: { host: RelayHost; selected: string; onChange: (id: string) => void }) {
  return <div className="relayRouteChoices" role="group" aria-label="Маршрут Relay">
    {host.routes.map(route => {
      const current = host.state === "connected";
      const attention = current && routeNeedsAttention(route);
      const sessions = current && route.sessions !== undefined ? route.sessions.toLocaleString("ru-RU") : "—";
      return <button type="button" key={route.id} className="relayRouteChoice" aria-pressed={selected === route.id} onClick={() => onChange(route.id)}>
        <span className="relayRouteChoiceIdentity"><strong>{route.transport.toUpperCase()} · {route.listen_port}</strong><small>VPS · {route.target_port}</small></span>
        <span className="relayRouteChoiceStatus" data-state={!current ? "unknown" : attention ? "warning" : route.state}>{!current ? "Нет связи с Agent" : attention ? "Требует внимания" : routeStates[route.state]}</span>
        <span className="relayRouteChoiceSessions"><strong>{sessions}</strong><small>Сессии</small></span>
      </button>;
    })}
  </div>;
}

export function RelayConsumerPicker({ consumers, selected, disabled, onChange }: { consumers: NonNullable<RelayStatus["consumers"]>; selected: string; disabled: boolean; onChange: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const filtered = consumers.filter(item => item.label.toLocaleLowerCase("ru-RU").includes(query.trim().toLocaleLowerCase("ru-RU")));
  return <div className="relayConsumerPicker">
    <div className="relayConsumerHeading"><h4>Подключение для проверки <span>{consumers.length}</span></h4>{selected && <span>Выбрано: {consumers.find(item => item.id === selected)?.label}</span>}</div>
    {consumers.length > 6 && <input type="search" aria-label="Поиск подключения для проверки" placeholder="Найти подключение" value={query} onChange={event => setQuery(event.target.value)} />}
    {consumers.length ? <>
      <div className="relayConsumerChoices" role="group" aria-label="Подключение для проверки">{filtered.map(item => <button type="button" key={item.id} aria-pressed={selected === item.id} disabled={disabled} onClick={() => onChange(item.id)}>{item.label}</button>)}</div>
      {!filtered.length && <p>Подключения не найдены.</p>}
    </> : <p>Нет доступных подключений для проверки.</p>}
  </div>;
}
