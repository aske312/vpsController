"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { CdnOperation } from "../../shared/lib/cdn-security-operation";

type SystemAction = {
  unit?: string;
  action?: string;
  state?: string;
  result?: string;
  started_at?: string;
  updated_at?: string;
  progress?: number;
  message?: string;
};

type MihomoOperation = {
  id: string;
  label: string;
  message?: string;
  state: "running" | "success" | "error";
};

type Props = {
  action?: SystemAction | null;
  label?: string;
  active: boolean;
  command?: CdnOperation | null;
  onRecheck?: () => void;
  onDismiss?: () => void;
};

const MIHOMO_EVENT = "gate312:mihomo-operation";

export function OperationDock({ action, label, active, command, onRecheck, onDismiss }: Props) {
  const [mihomo, setMihomo] = useState<MihomoOperation | null>(null);
  const clearTimer = useRef<number | null>(null);

  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<MihomoOperation>).detail;
      if (!detail?.id) return;
      if (clearTimer.current) window.clearTimeout(clearTimer.current);
      setMihomo(detail);
      if (detail.state !== "running") {
        clearTimer.current = window.setTimeout(() => setMihomo(null), detail.state === "error" ? 5500 : 2600);
      }
    };
    window.addEventListener(MIHOMO_EVENT, listener);
    return () => {
      window.removeEventListener(MIHOMO_EVENT, listener);
      if (clearTimer.current) window.clearTimeout(clearTimer.current);
    };
  }, []);

  const systemProgress = useMemo(() => Math.max(0, Math.min(100, action?.progress ?? (active ? 5 : 100))), [action?.progress, active]);
  const systemFailed = action?.state === "failed" || action?.result === "failed";
  const showSystem = Boolean(active);

  if (!showSystem && !mihomo && !command) return null;

  return (
    <aside className="gateOperationDock" aria-live="polite" aria-label="Выполняемые операции">
      {command && <section className={`gateOperationCard cdn ${command.state === "failed" ? "error" : command.state === "succeeded" ? "success" : command.state}`}>
        <div className="gateOperationContent">
          <span className="gateOperationIcon">CF</span>
          <div className="gateOperationText"><span>SERVER OPERATION</span><strong>Проверка сертификата Cloudflare</strong><small>{command.message}</small></div>
          <b className="gateOperationPercent">{command.state === "failed" ? "ERR" : command.state === "unknown" ? "…" : `${command.progress}%`}</b>
        </div>
        <div className={`gateOperationTrack ${command.state === "queued" || command.state === "unknown" ? "indeterminate" : ""}`}><i style={{ width: `${Math.max(5, command.progress)}%` }} /></div>
        {command.state === "unknown" && <button className="gateOperationButton" onClick={onRecheck}>Проверить результат</button>}
        {["succeeded", "failed"].includes(command.state) && <button className="gateOperationButton" onClick={onDismiss}>Закрыть</button>}
      </section>}
      {showSystem && (
        <section className={`gateOperationCard ${systemFailed ? "error" : ""}`}>
          <div className="gateOperationContent">
            <span className="gateOperationIcon">SYS</span>
            <div className="gateOperationText">
              <span>SYSTEM OPERATION</span>
              <strong>{label || action?.action || "Системная операция"}</strong>
              <small>Команда выполняется. Итог появится после завершения.</small>
            </div>
            <b className="gateOperationPercent">{systemFailed ? "ERR" : `${systemProgress}%`}</b>
          </div>
          <div className="gateOperationTrack"><i style={{ width: `${systemFailed ? 100 : systemProgress}%` }} /></div>
        </section>
      )}

      {mihomo && (
        <section className={`gateOperationCard mihomo ${mihomo.state}`}>
          <div className="gateOperationContent">
            <span className="gateOperationIcon">M</span>
            <div className="gateOperationText">
              <span>MIHOMO OPERATION</span>
              <strong>{mihomo.label}</strong>
              <small>{mihomo.message || (mihomo.state === "running" ? "Mihomo Manager выполняет команду…" : mihomo.state === "success" ? "Операция завершена" : "Операция завершилась ошибкой")}</small>
            </div>
            <b className="gateOperationPercent">{mihomo.state === "running" ? "…" : mihomo.state === "success" ? "100%" : "ERR"}</b>
          </div>
          <div className={`gateOperationTrack ${mihomo.state === "running" ? "indeterminate" : ""}`}><i style={{ width: mihomo.state === "running" ? "34%" : "100%" }} /></div>
        </section>
      )}
    </aside>
  );
}
