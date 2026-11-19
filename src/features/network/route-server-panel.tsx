"use client";

import { NotificationNotice } from "../../shared/notifications/notification-center";
import { useState } from "react";
import type { useNetworkAddresses } from "./network-addresses";
import { addressRoleLabels, type ServerAddress, type ServerAddressRole } from "./server-address-state";

const roles: Array<{ role: ServerAddressRole; description: string }> = [
  { role: "primary", description: "Для новых подключений" },
  { role: "reserve", description: "Без автопереключения" },
  { role: "additional", description: "Для отдельных подключений" },
];

export function ServerAddressPanel({ row, control }: { row: ServerAddress; control: ReturnType<typeof useNetworkAddresses> }) {
  const [selection, setSelection] = useState<ServerAddressRole | null>(null);
  const chosen = selection || row.role;
  const locked = control.locked || row.presence !== "present";
  return <>
    <section className="routeTaskCard"><header><h3>Назначение IP</h3></header>
      <fieldset className="routeRoleChoices"><legend className="networkSrOnly">Роль IP-адреса</legend>{roles.map(({ role }) => <label key={role} className={chosen === role ? "is-selected" : ""}>
        <input type="radio" name="server-address-role" value={role} checked={chosen === role} disabled={locked} onChange={() => setSelection(role)} />
        <span><strong>{addressRoleLabels[role]}</strong></span>{row.role === role && <span className="routeCurrentRole">Сейчас</span>}
      </label>)}</fieldset>
      <div className="routeRoleApply"><small>Существующие подключения сохранят свои адреса.</small><button type="button" className="routeActionPrimary" disabled={locked || chosen === row.role} onClick={() => void control.setRole(row.address, chosen)}>Применить роль</button></div>
    </section>
    {!control.pending && locked && <NotificationNotice id={`network:ip:${row.address}`} title={row.address} state="info" message={row.presence === "missing" ? "Адрес больше не обнаружен на сервере. Назначение недоступно." : row.presence === "unknown" ? "Наличие адреса на сервере не подтверждено." : "Изменение временно недоступно. Дождитесь актуальных данных сервера."} action={{ label: "Проверить состояние", disabled: control.reading, run: () => control.load() }} />}
  </>;
}
