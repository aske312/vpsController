"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { SystemOperationUnknown, systemActionSnapshot, systemActionNeedsPolling } from "../../control-panel/system-operation";
import { useNotifier } from "../../shared/notifications/notification-center";
import type { SystemAction } from "../../shared/types/control-plane";
import type { NetworkAdmission, NetworkRequest } from "./network-api";
import { relaySnapshot, relayClientResult, type RelayClientResult } from "./relay-state";
export type { RelayClientResult } from "./relay-state";

type Pending = { resultId: string; operationId: string };
function saved(key: string): Pending | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null");
    return value && [value.resultId, value.operationId].every(id => typeof id === "string" && /^[a-f0-9]{32}$/.test(id)) ? value : null;
  } catch { return null; }
}
function persist(key: string, value: Pending | null) {
  try { if (value) sessionStorage.setItem(key, JSON.stringify(value)); else sessionStorage.removeItem(key); } catch { /* readback remains available in this session */ }
}

export function useRelayClient({ request, admit, onOperation, onResult, native = false }: {
  native?: boolean; request: NetworkRequest; admit: NetworkAdmission; onOperation: (operation: SystemAction) => void; onResult: (result: RelayClientResult) => void;
}) {
  const key = native ? "312-native-client-result" : "312-relay-client-result";
  const [pending, setPending] = useState<Pending | null>(() => saved(key));
  const [canResume, setCanResume] = useState(false);
  const [failed, setFailed] = useState(false);
  const [sending, setSending] = useState(false);
  const [terminalFailure, setTerminalFailure] = useState(false);
  const reading = useRef(false);
  const submitting = useRef(false);
  const delivered = useRef<string | null>(null);
  const receive = useRef(onResult);
  const generation = useRef(0);
  const submissionGeneration = useRef(0);
  const { error: notifyError } = useNotifier(native ? "native-client" : "relay-client", native ? "Подключение" : "Подключение через Relay");
  useEffect(() => { receive.current = onResult; }, [onResult]);
  useEffect(() => { const current = generation; return () => { current.current++; }; }, [request, pending]);
  useEffect(() => { const current = submissionGeneration; return () => { current.current++; }; }, [request]);
  const check = useCallback(async () => {
    if (!pending || reading.current) return;
    reading.current = true;
    const run = generation.current;
    try {
      const resultPath = `/clients/${native ? "native" : "relay"}/${pending.resultId}`;
      let result = native ? await request<{ state: string; can_resume?: boolean; result?: RelayClientResult }>(resultPath, { cache: "no-store" }) : undefined;
      if (run !== generation.current) return;
      let operation: SystemAction;
      try {
        operation = systemActionSnapshot(await request(`/application/operations/${pending.operationId}`, { cache: "no-store" }));
      } catch (cause) {
        // The exact durable export remains authoritative after public history is pruned.
        if (!native || !["ready", "expired"].includes(result?.state || "")) throw cause;
        operation = { id: pending.operationId, state: "unknown" };
      }
      if (run !== generation.current) return;
      if (operation.id !== pending.operationId) throw new Error("Получен результат другой операции");
      if (!native && systemActionNeedsPolling(operation)) return;
      result ??= await request<{ state: string; can_resume?: boolean; result?: RelayClientResult }>(resultPath, { cache: "no-store" });
      if (run !== generation.current) return;
      if ((native || operation.state === "succeeded") && result.state === "ready" && result.result && typeof result.result.config === "string" && result.result.config.length > 0) {
        const validated = relayClientResult(result.result);
        if (delivered.current !== pending.resultId) { delivered.current = pending.resultId; receive.current(validated); }
        persist(key, null); setPending(null); setCanResume(false); setFailed(false);
      } else {
        setCanResume(result.can_resume === true);
        setTerminalFailure(result.state === "expired" || ["failed", "cancelled"].includes(operation.state || "") && result.can_resume !== true && (!native || result.state === "pending"));
        setFailed(!systemActionNeedsPolling(operation) || result.state === "expired");
      }
    } catch { if (run === generation.current) setFailed(true); }
    finally { reading.current = false; }
  }, [key, native, pending, request]);
  useEffect(() => {
    if (!pending) return;
    const initial = window.setTimeout(() => void check(), 0);
    const timer = window.setInterval(() => void check(), 3000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [check, pending]);
  async function submit(path: string, body: object, resultId?: string) {
    if (submitting.current || reading.current || (pending && !resultId)) return;
    submitting.current = true; setSending(true);
    const run = submissionGeneration.current;
    let attemptedId: string | undefined;
    try {
      let payload = body;
      if (!native) {
        const status = relaySnapshot(await request("/network/relays", { cache: "no-store" }));
        if (run !== submissionGeneration.current) return;
        if (status.pending) throw new Error("Сначала подтвердите предыдущую операцию Relay в Сеть → ROUTES");
        payload = { ...body, expected_revision: status.revision };
      }
      const id = crypto.randomUUID().replaceAll("-", "");
      attemptedId = id;
      const descriptor = { resultId: resultId || id, operationId: id };
      // Persist before admission so reload during the POST can still retrieve the export.
      persist(key, descriptor); setPending(descriptor);
      const started = await admit(path, { method: "POST", headers: { "X-Operation-ID": id }, body: JSON.stringify(payload) });
      if (run !== submissionGeneration.current) return;
      if (started.id !== id) throw new Error("Не подтверждён ID операции создания");
      const next = { resultId: resultId || started.id, operationId: started.id };
      persist(key, next); setPending(next); setCanResume(false); setTerminalFailure(false); setFailed(false); onOperation(started);
    } catch (cause) {
      if (run !== submissionGeneration.current) return;
      if (cause instanceof SystemOperationUnknown) {
        if (cause.id === attemptedId) {
          const next = { resultId: resultId || cause.id, operationId: cause.id };
          persist(key, next); setPending(next); setCanResume(false); setTerminalFailure(false); setFailed(false);
        } else {
          persist(key, pending); setPending(pending); notifyError(cause.message);
        }
      } else {
        const rejected = cause && typeof cause === "object" && "status" in cause && typeof cause.status === "number" && cause.status >= 400 && cause.status < 500;
        if (rejected) { persist(key, pending); setPending(pending); }
        notifyError(cause instanceof Error ? cause.message : "Не удалось начать настройку подключения");
      }
    } finally { submitting.current = false; if (run === submissionGeneration.current) setSending(false); }
  }
  return {
    pending: Boolean(pending), failed, canResume, terminalFailure, sending, check,
    dismiss: () => { if (terminalFailure) { persist(key, null); setPending(null); setFailed(false); setTerminalFailure(false); } },
    start: (body: object) => submit(native ? "/clients/native" : "/clients/relay", body),
    resume: () => pending && canResume ? submit(`/clients/relay/${pending.resultId}/resume`, {}, pending.resultId) : Promise.resolve(),
  };
}
