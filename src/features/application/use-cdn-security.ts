"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { createApiClient } from "../../shared/lib/api-request";
import { CDN_PENDING_CHANGED, CdnResultUnknown, connectionInterrupted, followCdnSecurity, newCdnOperation, persistPendingCdnOperation, readPendingCdnOperation, restorePendingCdnOperation, submitCdnSecurity } from "../../shared/lib/cdn-security-operation";
import type { CdnOperation, CdnSecurityStatus } from "../../shared/lib/cdn-security-operation";

export function useCdnSecurity(request: ReturnType<typeof createApiClient>, token: string, serverOperation: CdnOperation | null | undefined, apply: (status: CdnSecurityStatus) => void) {
  const [operation, setOperation] = useState<CdnOperation | null>(readPendingCdnOperation);
  const operationRef = useRef(operation);
  const inFlight = useRef<AbortController | null>(null);
  const seen = useRef("");
  const [seenId, setSeenId] = useState("");
  const publish = useCallback((value: CdnOperation | null) => { operationRef.current = value; setOperation(value); }, []);

  const execute = useCallback(async (initial: CdnOperation, resume = false) => {
    if (inFlight.current) return;
    const stored = readPendingCdnOperation();
    if (stored && stored.id !== initial.id) return;
    const controller = new AbortController();
    inFlight.current = controller;
    seen.current = initial.id;
    setSeenId(initial.id);
    publish({ ...initial, state: "running", message: resume ? "Проверяем состояние команды…" : initial.message });
    persistPendingCdnOperation(initial);
    try {
      const status = await (resume ? followCdnSecurity : submitCdnSecurity)(request, initial, publish, { signal: controller.signal });
      controller.signal.throwIfAborted();
      apply(status);
      persistPendingCdnOperation(null, initial.id);
    } catch (error) {
      const unknown = error instanceof CdnResultUnknown || connectionInterrupted(error);
      const rejected = error instanceof Error && "status" in error && Number(error.status) >= 400 && Number(error.status) < 500;
      if (!controller.signal.aborted) {
        publish({
          ...(operationRef.current?.id === initial.id ? operationRef.current : initial),
          state: unknown ? "unknown" : "failed",
          message: error instanceof Error ? error.message : "Не удалось проверить результат команды",
        });
      }
      if (rejected || (!controller.signal.aborted && !unknown)) persistPendingCdnOperation(null, initial.id);
    } finally {
      if (inFlight.current === controller) inFlight.current = null;
    }
  }, [apply, publish, request]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const stored = readPendingCdnOperation();
      if (token && stored) void execute(stored, true);
      else { setSeenId(""); if (!token) publish(null); }
    }, 0);
    return () => { window.clearTimeout(timer); inFlight.current?.abort(); inFlight.current = null; seen.current = ""; };
  }, [execute, publish, token]);

  useEffect(() => {
    if (!token) return;
    const changed = (event: Event) => {
      const stored = restorePendingCdnOperation((event as CustomEvent).detail);
      const current = operationRef.current;
      if (stored && stored.id !== current?.id) {
        inFlight.current?.abort(); inFlight.current = null;
        publish(stored);
      } else if (!stored && current && ["queued", "running", "unknown"].includes(current.state)) {
        inFlight.current?.abort(); inFlight.current = null;
        publish(null);
      }
    };
    window.addEventListener(CDN_PENDING_CHANGED, changed);
    return () => window.removeEventListener(CDN_PENDING_CHANGED, changed);
  }, [publish, token]);

  // Resume an operation after reopening the page, including with auto-refresh off.
  useEffect(() => {
    if (!token || !serverOperation || seen.current === serverOperation.id || !["queued", "running"].includes(serverOperation.state)) return;
    const timer = window.setTimeout(() => void execute(serverOperation, true), 0);
    return () => window.clearTimeout(timer);
  }, [execute, serverOperation, token]);

  useEffect(() => {
    if (operation?.state !== "succeeded") return;
    const timer = window.setTimeout(() => publish(null), 5000);
    return () => window.clearTimeout(timer);
  }, [operation, publish]);

  const pending = Boolean((operation && ["queued", "running", "unknown"].includes(operation.state)) ||
    (serverOperation && seenId !== serverOperation.id && ["queued", "running"].includes(serverOperation.state)));
  const blocked = () => pending || Boolean(operationRef.current && ["queued", "running", "unknown"].includes(operationRef.current.state)) || Boolean(readPendingCdnOperation());
  return {
    operation, pending,
    change: (enabled: boolean) => blocked() ? Promise.resolve() : execute(newCdnOperation(enabled)),
    prepareEch: (domain: string) => blocked() ? Promise.resolve() : execute({ ...newCdnOperation(true), kind: "ech", domain, message: "Подготовка ECH…" }),
    recheck: () => { if (operation) void execute(operation, true); },
    dismiss: () => { if (!pending && operationRef.current?.id === operation?.id) publish(null); },
  };
}
