"use client";

import type { ReactNode } from "react";
import { CountryFlag } from "../../shared/components/country-flag";
import { GateNavigation } from "./gate-navigation";
import { OperationNotifications } from "./operation-notifications";
import type { CdnOperation } from "../../shared/lib/cdn-security-operation";

type ProtocolImage = {
  id: string;
  name: string;
  installed: boolean;
};

type ServerInfo = {
  city?: string;
  name?: string;
  country?: string;
  country_code?: string;
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
  commandOperation?: CdnOperation | null;
  onRecheckCommand?: () => void;
  onDismissCommand?: () => void;
  onCancelOperation?: () => void | Promise<void>;
  onRecheckOperation?: () => void | Promise<void>;
  applicationStateTitle: string;
  uptimeLabel: string;
  loadLabel: string;
  cpuLabel: string;
  ramLabel: string;
  networkLabel: string;
  autoRefresh: boolean;
  busy: boolean;
  viewLoading: boolean;
  lastUpdated?: Date | null;
  onToggleAutoRefresh: () => void;
  onRefresh: () => void;
  onLogout: () => void;
  operationHistory?: ReactNode;
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
  commandOperation,
  onRecheckCommand,
  onDismissCommand,
  onCancelOperation,
  onRecheckOperation,
  applicationStateTitle,
  uptimeLabel,
  loadLabel,
  cpuLabel,
  ramLabel,
  networkLabel,
  autoRefresh,
  busy,
  viewLoading,
  lastUpdated,
  onToggleAutoRefresh,
  onRefresh,
  onLogout,
  operationHistory,
  children,
}: AppWorkspaceProps) {
  const countryCode = resolveCountryCode(server?.country_code, server?.country);
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

      <OperationNotifications action={operationAction} label={operationLabel} active={operationActive} command={commandOperation} onRecheck={onRecheckCommand} onSystemRecheck={onRecheckOperation} onDismiss={onDismissCommand} onCancel={onCancelOperation} />

      <section className="content">
        <header className="gateMasthead" aria-label="Состояние сервера">
          <div className="gateMastNode">
            <CountryFlag code={countryCode} label={server?.country || "Страна не определена"} />
            <div className="gateMastIdentity">
              <span className="gateMastEndpoint mono" title={server?.public_endpoint || server?.public_ip}>{server?.public_endpoint || server?.public_ip || "—"}</span>
            </div>
            <div className={`gateMastState ${nodeState}`}>{applicationStateTitle}</div>
          </div>

          <div className="gateMastFacts" aria-label="Метрики сервера">
            <div className="gateMastMetric"><span>UPTIME</span><strong>{uptimeLabel}</strong></div>
            <div className="gateMastMetric"><span>LOAD</span><strong>{loadLabel}</strong></div>
            <div className="gateMastMetric"><span>CPU</span><strong>{cpuLabel}</strong></div>
            <div className="gateMastMetric"><span>RAM</span><strong>{ramLabel}</strong></div>
            <div className="gateMastMetric network"><span>NETWORK</span><strong>{networkLabel}</strong></div>
          </div>

          <div className="gateMastActions">
            {operationHistory}
            <div className={`refreshControl ${autoRefresh ? "active" : ""}`} aria-label="Управление обновлением данных">
              <button className="autoButton" disabled={busy} onClick={onToggleAutoRefresh} aria-label={autoRefresh ? "Остановить автообновление" : "Включить автообновление"}><i /></button>
              <button className="iconButton" onClick={onRefresh} aria-label="Обновить текущий модуль">↻</button>
            </div>
            {lastUpdated && <span className="updatedAt">{lastUpdated.toLocaleTimeString("ru-RU")}</span>}
            <button className="ghostButton" onClick={onLogout}>Выйти</button>
          </div>
        </header>

        {children}
        {viewLoading && (
          <div className="contentLoadingVeil" aria-busy="true" aria-live="polite">
            <span className="contentLoadingIndicator"><i aria-hidden="true" />Обновляем раздел</span>
          </div>
        )}
      </section>
    </main>
  );
}

const countryAliases: Record<string, string[]> = {
  ae: ["united arab emirates", "uae", "оаэ", "эмират"], at: ["austria", "австри"], au: ["australia", "австрал"],
  be: ["belgium", "бельг"], bg: ["bulgaria", "болгар"], br: ["brazil", "бразил"], by: ["belarus", "беларус", "белорус"],
  ca: ["canada", "канад"], ch: ["switzerland", "швейцар"], cz: ["czech", "czechia", "чех"], de: ["germany", "deutschland", "герман"],
  dk: ["denmark", "дани"], ee: ["estonia", "эстон"], es: ["spain", "испан"], fi: ["finland", "финлян"],
  fr: ["france", "франц"], gb: ["united kingdom", "great britain", "britain", "великобритан", "англи"], hu: ["hungary", "венгр"],
  ie: ["ireland", "ирланд"], il: ["israel", "израил"], in: ["india", "инди"], is: ["iceland", "исланд"], it: ["italy", "итал"],
  jp: ["japan", "япон"], kr: ["south korea", "korea", "южная корея", "корея"], kz: ["kazakhstan", "казахстан"], lt: ["lithuania", "литв"],
  lu: ["luxembourg", "люксембург"], lv: ["latvia", "латви"], nl: ["netherlands", "holland", "нидерланд", "голланд"],
  no: ["norway", "норвег"], pl: ["poland", "польш"], pt: ["portugal", "португал"], ro: ["romania", "румын"],
  ru: ["russia", "russian federation", "росси"], se: ["sweden", "швец"], sg: ["singapore", "сингапур"], tr: ["turkey", "türkiye", "турц"],
  ua: ["ukraine", "украин"], us: ["united states", "usa", "сша", "соединенные штаты"],
};

function resolveCountryCode(code?: string, country?: string) {
  const normalizedCode = String(code || "").trim().toLowerCase();
  if (/^[a-z]{2}$/.test(normalizedCode)) return normalizedCode;
  const normalizedCountry = String(country || "").trim().toLowerCase();
  if (/^[a-z]{2}$/.test(normalizedCountry)) return normalizedCountry;
  return Object.entries(countryAliases).find(([, names]) => names.some((name) => normalizedCountry.includes(name)))?.[0] || "unknown";
}
