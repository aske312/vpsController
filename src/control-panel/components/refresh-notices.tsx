"use client";

import { useEffect, useState } from "react";
import { connectionInterrupted } from "../../shared/lib/cdn-security-operation";

export type RefreshFailure = { message: string; network: boolean };
export function refreshFailure(cause: unknown, fallback: string): RefreshFailure {
  return { message: cause instanceof Error ? cause.message : fallback, network: connectionInterrupted(cause) };
}

export function RefreshNotices({ errors, reconnecting }: { errors: Record<string, RefreshFailure>; reconnecting: boolean }) {
  const networkFailure = Object.values(errors).some((error) => error.network);
  const [persistent, setPersistent] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setPersistent(networkFailure), networkFailure ? 15000 : 0);
    return () => window.clearTimeout(timer);
  }, [networkFailure]);
  return <>
    {networkFailure && persistent && !reconnecting && <div className="refreshNotice" role="status">Связь с панелью временно недоступна. Отображаются последние полученные данные; обновите их после восстановления связи.</div>}
    {Object.entries(errors).filter(([, error]) => !error.network).map(([source, error]) => <div className="errorBox" role="status" key={source}>{error.message} Отображаются последние полученные данные.</div>)}
  </>;
}
