"use client";


import { useCallback, useEffect, useRef, useState } from "react";
import { ApiRequestError } from "../../shared/lib/api-request";
import { useNotifier } from "../../shared/notifications/notification-center";
import type { NetworkRequest } from "./network-api";
import { confirmsAddressChange, serverAddressesSnapshot, type AddressChange, type ServerAddressInventory, type ServerAddressRole } from "./server-address-state";

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
  const hasSnapshot = useRef(false);
  const generation = useRef(0);
  const active = useRef(true);
  const attempt = useRef<Pending | null>(null);
  const notifier = useNotifier("server-addresses", "Публичные IP");
  const remember = useCallback((value: Pending | null) => {
    attempt.current = value;
    if (active.current) setPending(value);
    try { if (value) sessionStorage.setItem(storageKey, JSON.stringify(value)); else sessionStorage.removeItem(storageKey); } catch { /* Memory still prevents replay during this session. */ }
  }, []);
  const accept = useCallback((value: ServerAddressInventory) => {
    if (active.current) { hasSnapshot.current = true; setStatus(value); setFailed(false); }
    const current = attempt.current;
    if (current && confirmsAddressChange(value, current)) {
      remember(null);
      notifier.operation(current.id, "Назначение IP", "success", "Изменение подтверждено. Основной IP применяется к новым конфигурациям; резерв сохраняется для дальнейшего использования.");
    }
  }, [notifier, remember]);
  const load = useCallback(async (background = false) => {
    if (sending.current || readingNow.current) return;
    readingNow.current = true;
    const run = ++generation.current;
    if (active.current) setReading(!background || !hasSnapshot.current || Boolean(attempt.current));
    const current = attempt.current;
    try {
      const value = serverAddressesSnapshot(await request(`/network/addresses${current ? `?change_id=${current.id}` : ""}`, { cache: "no-store" }));
      if (run === generation.current) accept(value);
    } catch { if (active.current && run === generation.current) setFailed(true); }
    finally { readingNow.current = false; if (active.current) setReading(false); }
  }, [accept, request]);
  useEffect(() => {
    active.current = true;
    remember(readPending());
    const timer = window.setTimeout(() => void load(true), 0);
    return () => { active.current = false; window.clearTimeout(timer); };
  }, [load, remember, refreshKey]);
  const setRole = async (address: string, role: ServerAddressRole) => {
    if (!status || busy || failed || reading || sending.current || attempt.current || status.discovery.state !== "ready") return;
    const current: Pending = { id: crypto.randomUUID().replaceAll("-", ""), address, role, previous_revision: status.revision };
    generation.current++; sending.current = true; remember(current);
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
