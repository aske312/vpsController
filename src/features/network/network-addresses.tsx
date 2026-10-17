"use client";

import { NetworkRouteBadge } from "./network-route-badge";

import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiRequestError } from "../../shared/lib/api-request";
import { useNotifier } from "../../shared/notifications/notification-center";
import type { NetworkRequest } from "./network-api";
import { addressMatches, addressRoleLabels, confirmsAddressChange, serverAddressesSnapshot, type AddressChange, type ServerAddressInventory, type ServerAddressRole } from "./server-address-state";

type Pending = Omit<AddressChange, "revision">;
const storageKey = "312-server-address-role-pending";

function readPending(): Pending | null {
  try {
    const item = JSON.parse(sessionStorage.getItem(storageKey) || "null");
    return item && /^[0-9a-f]{32}$/.test(item.id) && /^[0-9a-f]{32}$/.test(item.previous_revision) && typeof item.address === "string" && ["primary", "reserve", "additional"].includes(item.role) ? item : null;
  } catch { return null; }
}

export function useNetworkAddresses({ request, busy, refreshKey }: { request: NetworkRequest; busy: boolean; refreshKey: number }) {
  const [status, setStatus] = useState<ServerAddressInventory | null>(null);
  const [failed, setFailed] = useState(false);
  const [reading, setReading] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const sending = useRef(false);
  const readingNow = useRef(false);
  const active = useRef(true);
  const attempt = useRef<Pending | null>(null);
  const notifier = useNotifier("server-addresses", "Публичные IP");
  const remember = useCallback((value: Pending | null) => {
    attempt.current = value;
    if (active.current) setPending(value);
    try { if (value) sessionStorage.setItem(storageKey, JSON.stringify(value)); else sessionStorage.removeItem(storageKey); } catch { /* Memory still prevents replay during this session. */ }
  }, []);
  const accept = useCallback((value: ServerAddressInventory) => {
    if (active.current) { setStatus(value); setFailed(false); }
    const current = attempt.current;
    if (current && confirmsAddressChange(value, current)) {
      remember(null);
      notifier.operation(current.id, "Назначение IP", "success", "Изменение подтверждено. Основной IP применяется к новым конфигурациям; резерв сохраняется для дальнейшего использования.");
    }
  }, [notifier, remember]);
  const load = useCallback(async () => {
    if (sending.current || readingNow.current) return;
    readingNow.current = true;
    if (active.current) setReading(true);
    const current = attempt.current;
    try {
      accept(serverAddressesSnapshot(await request(`/network/addresses${current ? `?change_id=${current.id}` : ""}`, { cache: "no-store" })));
    } catch { if (active.current) setFailed(true); }
    finally { readingNow.current = false; if (active.current) setReading(false); }
  }, [accept, request]);
  useEffect(() => {
    active.current = true;
    remember(readPending());
    const timer = window.setTimeout(() => void load(), 0);
    return () => { active.current = false; window.clearTimeout(timer); };
  }, [load, remember, refreshKey]);
  const setRole = async (address: string, role: ServerAddressRole) => {
    if (!status || busy || failed || readingNow.current || sending.current || attempt.current || status.discovery.state !== "ready") return;
    const current: Pending = { id: crypto.randomUUID().replaceAll("-", ""), address, role, previous_revision: status.revision };
    sending.current = true; remember(current);
    notifier.operation(current.id, "Назначение IP", "running", "Сохраняем назначение адреса…");
    try {
      const value = serverAddressesSnapshot(await request("/network/addresses", { method: "PUT", body: JSON.stringify({ address, role, expected_revision: current.previous_revision, operation_id: current.id }) }));
      if (!confirmsAddressChange(value, current)) throw new ApiRequestError("Ответ не подтверждает отправленное изменение", "response");
      accept(value);
    } catch (error) {
      let confirmed = false;
      try {
        const value = serverAddressesSnapshot(await request(`/network/addresses?change_id=${current.id}`, { cache: "no-store" }));
        confirmed = confirmsAddressChange(value, current); accept(value);
      } catch { if (active.current) setFailed(true); }
      if (!confirmed) {
        const state = error instanceof ApiRequestError && error.kind === "http" && error.status !== undefined && error.status < 500 ? "error" : "unknown";
        if (state === "error") remember(null);
        notifier.operation(current.id, "Назначение IP", state, error instanceof Error ? error.message : "Результат сохранения неизвестен", state === "unknown" ? () => void load() : undefined);
      }
    } finally { sending.current = false; }
  };
  return { status, failed, reading, pending, locked: busy || failed || reading || Boolean(pending) || status?.discovery.state !== "ready", load, setRole };
}

export function NetworkServerAddressRows({ control, query, expanded, onToggle, renderIdentity }: { control: ReturnType<typeof useNetworkAddresses>; query: string; expanded: string[]; onToggle: (key: string) => void; renderIdentity?: (address: string) => ReactNode }) {
  const { status } = control;
  return <>
    {(control.failed || !status || status.discovery.state === "error" || control.pending) && <tr><td colSpan={5}><div className="networkAddressNotice" role="status">
      <span>{control.pending ? "Проверяем результат назначения IP. Команда повторно не отправляется." : control.failed ? "Не удалось обновить публичные IP. Сохранённые строки оставлены для просмотра." : status?.discovery.message || "Определяем публичные IP сервера…"}</span>
      <button type="button" disabled={control.reading} onClick={() => void control.load()}>Проверить результат</button>
    </div></td></tr>}
    {status?.items.filter(row => addressMatches(row, query)).map(row => {
      const key = `SERVER-${row.address}`; const open = expanded.includes(key);
      return <Fragment key={key}><tr className={`networkServerRouteRow${open ? " is-expanded" : ""}`}>
        <td><button type="button" className="networkRouteExpand" aria-expanded={open} onClick={() => onToggle(key)}><span className="networkRouteExpandMark" aria-hidden="true"/><span><strong>{row.address}</strong><small>{row.family.toUpperCase()} · SERVER</small></span></button></td>
        <td><NetworkRouteBadge tone={row.presence === "present" ? "active" : row.presence === "missing" ? "error" : "unchecked"}>{row.presence === "present" ? "Назначен" : row.presence === "missing" ? "Отсутствует" : "Не подтверждён"}</NetworkRouteBadge></td>
        <td><strong>{addressRoleLabels[row.role]}</strong><small>{row.interfaces.join(", ") || "Назначение сохранено"}</small></td>
        <td><span className="networkRouteTags"><span className="networkRouteTag direct">DIRECT</span></span></td>
        <td><label className="networkAddressRole"><span className="networkSrOnly">Назначение {row.address}</span><select value={row.role} disabled={control.locked || row.presence !== "present"} onChange={event => void control.setRole(row.address, event.target.value as ServerAddressRole)}>
          <option value="primary">Основной</option><option value="reserve" disabled={row.role === "primary"}>Резервный</option><option value="additional" disabled={row.role === "primary"}>Дополнительный</option>
        </select></label></td>
      </tr>{open && <tr className="networkRouteDetailRow"><td colSpan={5}><div className="networkRouteCascade">
        <strong>{row.address} · {addressRoleLabels[row.role]}</strong>
        <p>{row.role === "primary" ? "Используется в новых прямых конфигурациях. Для смены основного выберите другой IP этого семейства; прежний станет резервным." : row.role === "reserve" ? "Резервный адрес сохранён. Автоматическое переключение подключений пока не включено." : "Публичный адрес сервера доступен для назначения основным или резервным."}</p>
        <small>{row.source === "interface" ? `Обнаружен на интерфейсах: ${row.interfaces.join(", ")}` : row.source === "configured" ? "Указан в настройках сервера; назначение интерфейсу не подтверждено (возможен NAT)." : "Сохранён ранее; сейчас адрес не найден на активном интерфейсе."}</small>
        {renderIdentity?.(row.address)}
      </div></td></tr>}</Fragment>;
    })}
  </>;
}
