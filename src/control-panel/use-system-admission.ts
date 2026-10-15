"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { createApiClient } from "../shared/lib/api-request";
import type { ApplicationStatus } from "../shared/types/control-plane";
import { persistSystemAdmission, readSystemAdmission, restoreSystemAdmission, submitSystemOperation, systemAdmissionSnapshot, systemActionNeedsPolling, SYSTEM_ADMISSION_CHANGED, SystemOperationUnknown, type SystemAction } from "./system-operation";

export function useSystemAdmission(request: ReturnType<typeof createApiClient>, token: string, apply: (operation: SystemAction) => void) {
  const [pending, setPending] = useState<SystemAction | null>(readSystemAdmission);
  const pendingRef = useRef(pending);
  const inFlight = useRef<AbortController | null>(null);
  const publish = useCallback((value: SystemAction | null) => { pendingRef.current = value; setPending(value); }, []);
  const release = useCallback((id: string) => {
    if (pendingRef.current?.id === id) publish(null);
    persistSystemAdmission(null, id);
  }, [publish]);

  const check = useCallback(async () => {
    const initial = pendingRef.current;
    if (!initial?.id || inFlight.current || !token) return;
    const controller = new AbortController();
    inFlight.current = controller;
    try {
      const action = systemAdmissionSnapshot(await request(`/application/operations/${initial.id}`, { cache: "no-store", signal: controller.signal }), initial.id, initial);
      if (initial.cancel_requested === true && action.cancel_requested !== true && systemActionNeedsPolling(action)) throw new SystemOperationUnknown(initial.id);
      controller.signal.throwIfAborted();
      apply(action);
      if (systemActionNeedsPolling(action)) publish(action);
      else release(initial.id);
    } catch {
      if (!controller.signal.aborted) {
        const unknown = { ...(pendingRef.current?.id === initial.id ? pendingRef.current : initial), state: "unknown", progress: undefined, message: "Результат команды временно недоступен. Проверяем прежний идентификатор без повторной отправки." };
        publish(unknown); apply(unknown);
      }
    }
    finally { if (inFlight.current === controller) inFlight.current = null; }
  }, [apply, publish, release, request, token]);

  const cancel = useCallback(async (current: SystemAction) => {
    if (!token || !current.id || !/^[a-f0-9]{32}$/.test(current.id)) return;
    const previous = pendingRef.current || readSystemAdmission();
    if (previous?.id && previous.id !== current.id) throw new SystemOperationUnknown(previous.id);
    if (previous?.cancel_requested === true || current.cancel_requested === true) return;
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    const initial = { ...current, state: "unknown", progress: undefined, cancel_requested: true, message: "Проверяем приём остановки выбранной команды без повторной отправки." };
    publish(initial); apply(initial); persistSystemAdmission(initial);
    try {
      const action = systemAdmissionSnapshot(await request("/application/action", { method: "DELETE", headers: { "X-Expected-Operation-ID": current.id }, signal: controller.signal }), current.id, current);
      controller.signal.throwIfAborted();
      if (action.cancel_requested !== true && systemActionNeedsPolling(action)) throw new SystemOperationUnknown(current.id);
      apply(action);
      if (systemActionNeedsPolling(action)) publish(action);
      else release(current.id);
    } catch (error) {
      const rejected = error && typeof error === "object" && "status" in error && typeof error.status === "number" && error.status >= 400 && error.status < 500;
      if (rejected && !controller.signal.aborted) {
        const resumed = { ...current, cancel_requested: false };
        publish(resumed); apply(resumed); persistSystemAdmission(resumed);
        throw error;
      }
      // A missing acknowledgement keeps the cancellation descriptor until its receipt or terminal worker is read.
    } finally { if (inFlight.current === controller) inFlight.current = null; }
    if (!controller.signal.aborted) await check();
  }, [apply, check, publish, release, request, token]);

  useEffect(() => {
    if (!token) return;
    const changed = (event: Event) => {
      const descriptor = restoreSystemAdmission((event as CustomEvent).detail);
      if (descriptor?.id === pendingRef.current?.id && Boolean(descriptor?.cancel_requested) === Boolean(pendingRef.current?.cancel_requested)) return;
      inFlight.current?.abort(); inFlight.current = null;
      publish(descriptor);
      if (descriptor) apply(descriptor);
    };
    window.addEventListener(SYSTEM_ADMISSION_CHANGED, changed);
    const timer = window.setTimeout(() => {
      const descriptor = readSystemAdmission();
      if (descriptor) { publish(descriptor); apply(descriptor); void check(); }
    }, 0);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener(SYSTEM_ADMISSION_CHANGED, changed);
      inFlight.current?.abort(); inFlight.current = null;
    };
  }, [apply, check, publish, token]);

  useEffect(() => {
    if (!token || !pending) return;
    const timer = window.setInterval(() => void check(), 2000);
    return () => window.clearInterval(timer);
  }, [check, pending, token]);

  const submit = useCallback(async (_request: ReturnType<typeof createApiClient>, path: string, options: RequestInit) => {
    const previous = pendingRef.current || readSystemAdmission();
    if (previous?.id) throw new SystemOperationUnknown(previous.id);
    if (!token) throw new Error("Войдите в панель перед отправкой команды");
    const id = crypto.randomUUID().replaceAll("-", "");
    const initial = restoreSystemAdmission({ id })!;
    const controller = new AbortController();
    inFlight.current = controller;
    publish(initial); apply(initial); persistSystemAdmission(initial);
    try {
      const action = await submitSystemOperation(request, path, { ...options, signal: controller.signal }, id);
      controller.signal.throwIfAborted();
      apply(action);
      if (systemActionNeedsPolling(action)) publish(action);
      else release(id);
      return action;
    } catch (error) {
      // Only a definitive rejection releases an unconfirmed admission.
      const rejected = error && typeof error === "object" && "status" in error && typeof error.status === "number" && error.status >= 400 && error.status < 500;
      if (rejected) {
        if (!controller.signal.aborted) apply({ ...initial, state: "failed", message: error instanceof Error ? error.message : "Команда отклонена" });
        release(id);
      }
      throw error;
    } finally { if (inFlight.current === controller) inFlight.current = null; }
  }, [apply, publish, release, request, token]);

  const retain = useCallback((status: ApplicationStatus): ApplicationStatus => pendingRef.current ? { ...status, action: pendingRef.current } : status, []);
  return { pending: Boolean(pending), submit, cancel, recheck: check, retain };
}
