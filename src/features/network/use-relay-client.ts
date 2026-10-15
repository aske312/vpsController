"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { SystemOperationUnknown, systemActionSnapshot, systemActionNeedsPolling } from "../../control-panel/system-operation";
import { useNotifier } from "../../shared/notifications/notification-center";
import type { SystemAction } from "../../shared/types/control-plane";
import type { NetworkAdmission, NetworkRequest } from "./network-api";
import { relaySnapshot, relayClientResult, type RelayClientResult } from "./relay-state";
export type { RelayClientResult } from "./relay-state";

type Pending = { resultId: string; operationId: string };
const key = "312-relay-client-result";
function saved(): Pending | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null");
    return value && [value.resultId, value.operationId].every(id => typeof id === "string" && /^[a-f0-9]{32}$/.test(id)) ? value : null;
  } catch { return null; }
}
function persist(value: Pending | null) {
  try { if (value) sessionStorage.setItem(key, JSON.stringify(value)); else sessionStorage.removeItem(key); } catch { /* readback remains available in this session */ }
}

export function useRelayClient({ request, admit, onOperation, onResult }: {
  request: NetworkRequest; admit: NetworkAdmission; onOperation: (operation: SystemAction) => void; onResult: (result: RelayClientResult) => void;
}) {
  const [pending, setPending] = useState<Pending | null>(saved);
  const [canResume, setCanResume] = useState(false);
  const [failed, setFailed] = useState(false);
  const [sending, setSending] = useState(false);
  const [terminalFailure, setTerminalFailure] = useState(false);
  const reading = useRef(false);
  const submitting = useRef(false);
  const delivered = useRef<string | null>(null);
  const receive = useRef(onResult);
  const generation = useRef(0);
  const { error: notifyError } = useNotifier("relay-client", "Подключение через Relay");
  useEffect(() => { receive.current = onResult; }, [onResult]);
  useEffect(() => { const current = generation; return () => { current.current++; }; }, [request, pending]);
  const check = useCallback(async () => {
    if (!pending || reading.current) return;
    reading.current = true;
    const run = generation.current;
    try {
      const operation = systemActionSnapshot(await request(`/application/operations/${pending.operationId}`, { cache: "no-store" }));
      if (run !== generation.current) return;
      if (operation.id !== pending.operationId) throw new Error("Получен результат другой операции");
      if (systemActionNeedsPolling(operation)) return;
      const result = await request<{ state: string; can_resume?: boolean; result?: RelayClientResult }>(`/clients/relay/${pending.resultId}`, { cache: "no-store" });
      if (run !== generation.current) return;
      if (operation.state === "succeeded" && result.state === "ready" && result.result && typeof result.result.config === "string" && result.result.config.length > 0) {
        const validated = relayClientResult(result.result);
        if (delivered.current !== pending.resultId) { delivered.current = pending.resultId; receive.current(validated); }
        persist(null); setPending(null); setCanResume(false); setFailed(false);
      } else { setCanResume(result.can_resume === true); setTerminalFailure(["failed", "cancelled"].includes(operation.state || "") && result.can_resume !== true); setFailed(true); }
    } catch { if (run === generation.current) setFailed(true); }
    finally { reading.current = false; }
  }, [pending, request]);
  useEffect(() => {
    if (!pending) return;
    const initial = window.setTimeout(() => void check(), 0);
    const timer = window.setInterval(() => void check(), 3000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [check, pending]);
  async function submit(path: string, body: object, resultId?: string) {
    if (submitting.current || reading.current) return;
    submitting.current = true; setSending(true);
    try {
      const status = relaySnapshot(await request("/network/relays", { cache: "no-store" }));
      if (status.pending) throw new Error("Сначала подтвердите предыдущую операцию Relay в Сеть → ROUTES");
      const started = await admit(path, { method: "POST", body: JSON.stringify({ ...body, expected_revision: status.revision }) });
      if (!started.id) throw new Error("Не получен ID операции");
      const next = { resultId: resultId || started.id, operationId: started.id };
      persist(next); setPending(next); setCanResume(false); setTerminalFailure(false); setFailed(false); onOperation(started);
    } catch (cause) {
      if (cause instanceof SystemOperationUnknown) {
        const next = { resultId: resultId || cause.id, operationId: cause.id };
        persist(next); setPending(next); setCanResume(false); setTerminalFailure(false); setFailed(false);
      } else notifyError(cause instanceof Error ? cause.message : "Не удалось начать настройку подключения");
    } finally { submitting.current = false; setSending(false); }
  }
  return {
    pending: Boolean(pending), failed, canResume, terminalFailure, sending, check,
    dismiss: () => { if (terminalFailure) { persist(null); setPending(null); setFailed(false); setTerminalFailure(false); } },
    start: (body: object) => submit("/clients/relay", body),
    resume: () => pending && canResume ? submit(`/clients/relay/${pending.resultId}/resume`, {}, pending.resultId) : Promise.resolve(),
  };
}
