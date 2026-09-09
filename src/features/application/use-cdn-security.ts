"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { createApiClient } from "../../shared/lib/api-request";
import { CdnResultUnknown, connectionInterrupted, followCdnSecurity, newCdnOperation, submitCdnSecurity } from "../../shared/lib/cdn-security-operation";
import type { CdnOperation, CdnSecurityStatus } from "../../shared/lib/cdn-security-operation";

export function useCdnSecurity(request: ReturnType<typeof createApiClient>, token: string, serverOperation: CdnOperation | null | undefined, apply: (status: CdnSecurityStatus) => void) {
  const [operation, setOperation] = useState<CdnOperation | null>(null);
  const inFlight = useRef<AbortController | null>(null);
  const seen = useRef("");

  const execute = useCallback(async (initial: CdnOperation, resume = false) => {
    if (inFlight.current) return;
    const controller = new AbortController();
    inFlight.current = controller;
    seen.current = initial.id;
    setOperation({ ...initial, state: "running", message: resume ? "Проверяем состояние команды…" : initial.message });
    try {
      const status = await (resume ? followCdnSecurity : submitCdnSecurity)(request, initial, setOperation, { signal: controller.signal });
      apply(status);
    } catch (error) {
      if (!controller.signal.aborted) {
        setOperation((current) => ({
          ...(current || initial),
          state: error instanceof CdnResultUnknown || connectionInterrupted(error) ? "unknown" : "failed",
          message: error instanceof Error ? error.message : "Не удалось проверить результат команды",
        }));
      }
    } finally {
      if (inFlight.current === controller) inFlight.current = null;
    }
  }, [apply, request]);

  useEffect(() => {
    const timer = window.setTimeout(() => setOperation(null), 0);
    return () => { window.clearTimeout(timer); inFlight.current?.abort(); inFlight.current = null; seen.current = ""; };
  }, [token]);

  // Resume an operation after reopening the page, including with auto-refresh off.
  useEffect(() => {
    if (!token || !serverOperation || seen.current === serverOperation.id || !["queued", "running"].includes(serverOperation.state)) return;
    const timer = window.setTimeout(() => void execute(serverOperation, true), 0);
    return () => window.clearTimeout(timer);
  }, [execute, serverOperation, token]);

  useEffect(() => {
    if (operation?.state !== "succeeded") return;
    const timer = window.setTimeout(() => setOperation(null), 5000);
    return () => window.clearTimeout(timer);
  }, [operation]);

  const pending = Boolean((operation && ["queued", "running", "unknown"].includes(operation.state)) ||
    (serverOperation && seen.current !== serverOperation.id && ["queued", "running"].includes(serverOperation.state)));
  return {
    operation, pending,
    change: (enabled: boolean) => pending ? Promise.resolve() : execute(newCdnOperation(enabled)),
    recheck: () => { if (operation) void execute(operation, true); },
    dismiss: () => { if (!pending) setOperation((current) => current?.id === operation?.id ? null : current); },
  };
}
