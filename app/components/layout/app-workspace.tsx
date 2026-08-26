"use client";

import type { ReactNode } from "react";
import { GateNavigation } from "../gate-navigation";
import { OperationDock } from "../operation-dock";

type ProtocolImage = {
  id: string;
  name: string;
  installed: boolean;
};

type ServerInfo = {
  city?: string;
  name?: string;
  country?: string;
  public_endpoint?: string;
  public_ip?: string;
};

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

type AppWorkspaceProps = {
  activeTab: string;
  visualArt: string;
  protocolImages: ProtocolImage[];
  showConnections: boolean;
  nodeState: string;
  nodeStateLabel: string;
  server?: ServerInfo;
  onNavigate: (tab: string) => void;
  operationAction?: SystemAction | null;
  operationLabel?: string;
  operationActive: boolean;
  applicationStateTitle: string;
  uptimeLabel: string;
  loadLabel: string;
  cpuLabel: string;
  ramLabel: string;
  networkLabel: string;
  autoRefresh: boolean;
  autoRefreshLabel: string;
  busy: boolean;
  lastUpdated?: Date | null;
  onToggleAutoRefresh: () => void;
  onRefresh: () => void;
  onLogout: () => void;
  children: ReactNode;
};

export function AppWorkspace({
  activeTab,
  visualArt,
  protocolImages,
  showConnections,
  nodeState,
  nodeStateLabel,
  server,
  onNavigate,
  operationAction,
  operationLabel,
  operationActive,
  applicationStateTitle,
  uptimeLabel,
  loadLabel,
  cpuLabel,
  ramLabel,
  networkLabel,
  autoRefresh,
  autoRefreshLabel,
  busy,
  lastUpdated,
  onToggleAutoRefresh,
  onRefresh,
  onLogout,
  children,
}: AppWorkspaceProps) {
  const country = String(server?.country || "").toLowerCase();
  const countryCode = country === "nl" || country.includes("netherlands") || country.includes("нидерланд")
    ? "nl"
    : country === "lv" || country.includes("latvia") || country.includes("латви")
      ? "lv"
      : country === "ru" || country.includes("russia") || country.includes("росси")
        ? "ru"
        : "unknown";
  return (
    <main className={`shell gateShell visualShell art-${visualArt}`}>
      <GateNavigation
        activeTab={activeTab}
        protocolImages={protocolImages}
        showConnections={showConnections}
        nodeState={nodeState}
        nodeStateLabel={nodeStateLabel}
        server={server}
        onNavigate={onNavigate}
      />

      <OperationDock action={operationAction} label={operationLabel} active={operationActive} />

      <section className="content">
        <header className="gateMasthead" aria-label="Состояние сервера">
          <div className="gateMastNode">
            <CountryFlag code={countryCode} label={server?.country || "Страна не определена"} />
            <div className="gateMastIdentity">
              <span>PRIMARY NODE</span>
              <h2>{server?.city || server?.name || "Primary Node"}</h2>
              <p>{server?.country || "—"} <span className="mono">{server?.public_endpoint || server?.public_ip || "—"}</span></p>
            </div>
            <div className={`gateMastState ${nodeState}`}><i />{applicationStateTitle}</div>
          </div>

          <div className="gateMastFacts" aria-label="Метрики сервера">
            <div className="gateMastMetric"><span>UPTIME</span><strong>{uptimeLabel}</strong><i /></div>
            <div className="gateMastMetric"><span>LOAD</span><strong>{loadLabel}</strong><i /></div>
            <div className="gateMastMetric"><span>CPU</span><strong>{cpuLabel}</strong><i /></div>
            <div className="gateMastMetric"><span>RAM</span><strong>{ramLabel}</strong><i /></div>
            <div className="gateMastMetric network"><span>NETWORK</span><strong>{networkLabel}</strong><i /></div>
          </div>

          <div className="gateMastActions">
            <button className={`autoButton ${autoRefresh ? "active" : ""}`} disabled={busy} onClick={onToggleAutoRefresh}>
              <i /><span>{autoRefresh ? autoRefreshLabel : "Ⅱ"}</span>
            </button>
            {lastUpdated && <span className="updatedAt">{lastUpdated.toLocaleTimeString("ru-RU")}</span>}
            <button className="iconButton" onClick={onRefresh} aria-label="Обновить текущий модуль">↻</button>
            <button className="ghostButton" onClick={onLogout}>Выйти</button>
          </div>
        </header>

        {children}
      </section>
    </main>
  );
}

function CountryFlag({ code, label }: { code: "nl" | "lv" | "ru" | "unknown"; label: string }) {
  if (code === "unknown") return <span className="gateCountryFlag unknown" role="img" aria-label={label}>◎</span>;
  const stripes = code === "nl"
    ? ["#ae1c28", "#ffffff", "#21468b"]
    : code === "lv"
      ? ["#9e3039", "#ffffff", "#9e3039"]
      : ["#ffffff", "#1c57a7", "#d52b1e"];
  return <span className="gateCountryFlag" role="img" aria-label={label}><svg viewBox="0 0 27 18" aria-hidden="true"><rect width="27" height="18" rx="2" fill={stripes[0]} />{code === "lv" ? <rect y="8" width="27" height="2" fill={stripes[1]} /> : <><rect y="6" width="27" height="6" fill={stripes[1]} /><rect y="12" width="27" height="6" fill={stripes[2]} /></>}</svg></span>;
}
