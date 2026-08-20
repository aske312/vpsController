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
  const countryFlag = country.includes("netherlands") || country.includes("нидерланд")
    ? "🇳🇱"
    : country.includes("latvia") || country.includes("латви")
      ? "🇱🇻"
      : country.includes("russia") || country.includes("росси")
        ? "🇷🇺"
        : "🌐";
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
            <span className="gateMastServer" aria-hidden="true"><i /><i /><i /></span>
            <div>
              <h2><span className="gateCountryFlag" role="img" aria-label={server?.country || "Страна не определена"}>{countryFlag}</span>{server?.city || server?.name || "Primary Node"}</h2>
              <p>{server?.country || "—"} · <span className="mono">{server?.public_endpoint || server?.public_ip || "—"}</span></p>
            </div>
            <span className={`gateMastState ${nodeState}`}>{applicationStateTitle}</span>
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
