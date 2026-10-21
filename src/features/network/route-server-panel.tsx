"use client";

import { useState } from "react";
import type { useNetworkAddresses } from "./network-addresses";
import { addressRoleLabels, type ServerAddress, type ServerAddressRole } from "./server-address-state";

const roles: Array<{ role: ServerAddressRole; description: string }> = [
  { role: "primary", description: "Для новых прямых подключений этого семейства IP" },
  { role: "reserve", description: "Сохранить в резерве без автопереключения" },
  { role: "additional", description: "Оставить доступным для отдельного назначения" },
];

export function ServerAddressPanel({ row, control }: { row: ServerAddress; control: ReturnType<typeof useNetworkAddresses> }) {
  const [selection, setSelection] = useState<ServerAddressRole | null>(null);
  const chosen = selection || row.role;
  const locked = control.locked || row.presence !== "present";
  return <>
    <section className="routeTaskCard"><header><h3>Как использовать этот IP</h3><p>Выберите назначение адреса для {row.family === "ipv4" ? "IPv4" : "IPv6"}.</p></header>
      <fieldset className="routeRoleChoices"><legend className="networkSrOnly">Роль IP-адреса</legend>{roles.map(({ role, description }) => <label key={role} className={chosen === role ? "is-selected" : ""}>
        <input type="radio" name="server-address-role" value={role} checked={chosen === role} disabled={locked} onChange={() => setSelection(role)} />
        <span><strong>{addressRoleLabels[role]}</strong><small>{description}</small></span>{row.role === role && <span className="routeCurrentRole">Сейчас</span>}
      </label>)}</fieldset>
      <div className="routeRoleApply"><small>Существующие подключения сохранят свои адреса.</small><button type="button" className="routeActionPrimary" disabled={locked || chosen === row.role} onClick={() => void control.setRole(row.address, chosen)}>Применить роль</button></div>
    </section>
    {control.pending ? <div className="routeLibraryCallout" role="status"><p>Проверяем результат назначения. Команда повторно не отправляется.</p><button type="button" disabled={control.reading} onClick={() => void control.load()}>Проверить состояние</button></div>
      : locked && <p className="routeLibraryCallout" role="status">{row.presence === "missing" ? "Адрес больше не обнаружен на сервере. Назначение недоступно." : row.presence === "unknown" ? "Наличие адреса на сервере не подтверждено." : "Изменение временно недоступно. Дождитесь актуальных данных сервера."}</p>}
  </>;
}
