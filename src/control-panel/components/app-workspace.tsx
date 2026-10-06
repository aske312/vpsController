"use client";

import type { ReactNode } from "react";
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

      <OperationNotifications action={operationAction} label={operationLabel} active={operationActive} command={commandOperation} onRecheck={onRecheckCommand} onDismiss={onDismissCommand} onCancel={onCancelOperation} />

      <section className="content">
        <header className="gateMasthead" aria-label="Состояние сервера">
          <div className="gateMastNode">
            <CountryFlag code={countryCode} label={server?.country || "Страна не определена"} />
            <div className="gateMastIdentity">
              <span>PRIMARY NODE</span>
              <h2>{server?.city || server?.name || "Primary Node"}</h2>
              <p><span>{server?.country || "—"}</span><span className="mono">{server?.public_endpoint || server?.public_ip || "—"}</span></p>
            </div>
            <div className={`gateMastState ${nodeState}`}>{applicationStateTitle}</div>
          </div>

          <div className="gateMastFacts" aria-label="Метрики сервера">
            <div className="gateMastMetric"><span>UPTIME</span><strong>{uptimeLabel}</strong><i /></div>
            <div className="gateMastMetric"><span>LOAD</span><strong>{loadLabel}</strong><i /></div>
            <div className="gateMastMetric"><span>CPU</span><strong>{cpuLabel}</strong><i /></div>
            <div className="gateMastMetric"><span>RAM</span><strong>{ramLabel}</strong><i /></div>
            <div className="gateMastMetric network"><span>NETWORK</span><strong>{networkLabel}</strong><i /></div>
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

function CountryFlag({ code, label }: { code: string; label: string }) {
  const normalized = code.trim().toLowerCase();
  const horizontal: Record<string, [string, string, string]> = {
    at: ["#ed2939", "#ffffff", "#ed2939"], bg: ["#ffffff", "#00966e", "#d62612"], de: ["#000000", "#dd0000", "#ffce00"],
    ee: ["#4891d9", "#000000", "#ffffff"], es: ["#aa151b", "#f1bf00", "#aa151b"], hu: ["#ce2939", "#ffffff", "#477050"],
    lt: ["#fdb913", "#006a44", "#c1272d"], lu: ["#ef3340", "#ffffff", "#00a3e0"], lv: ["#9e3039", "#ffffff", "#9e3039"],
    nl: ["#ae1c28", "#ffffff", "#21468b"], ru: ["#ffffff", "#1c57a7", "#d52b1e"],
  };
  const vertical: Record<string, [string, string, string]> = {
    be: ["#2d2926", "#ffcd00", "#c8102e"], fr: ["#0055a4", "#ffffff", "#ef4135"], ie: ["#169b62", "#ffffff", "#ff883e"],
    it: ["#009246", "#ffffff", "#ce2b37"], ro: ["#002b7f", "#fcd116", "#ce1126"],
  };
  let flag: ReactNode = null;
  if (horizontal[normalized]) {
    const stripes = horizontal[normalized];
    flag = <><rect width="27" height="18" fill={stripes[0]} />{normalized === "lv" ? <rect y="8" width="27" height="2" fill={stripes[1]} /> : normalized === "es" ? <rect y="4.5" width="27" height="9" fill={stripes[1]} /> : <><rect y="6" width="27" height="6" fill={stripes[1]} /><rect y="12" width="27" height="6" fill={stripes[2]} /></>}</>;
  } else if (vertical[normalized]) {
    const stripes = vertical[normalized];
    flag = <><rect width="9" height="18" fill={stripes[0]} /><rect x="9" width="9" height="18" fill={stripes[1]} /><rect x="18" width="9" height="18" fill={stripes[2]} /></>;
  } else if (["fi", "se", "dk", "no", "is"].includes(normalized)) {
    const colors: Record<string, [string, string, string?]> = { fi: ["#ffffff", "#003580"], se: ["#006aa7", "#fecc00"], dk: ["#c8102e", "#ffffff"], no: ["#ba0c2f", "#ffffff", "#00205b"], is: ["#02529c", "#ffffff", "#dc1e35"] };
    const [background, cross, inner] = colors[normalized];
    flag = <><rect width="27" height="18" fill={background} /><rect x="8" width="3" height="18" fill={cross} /><rect y="7.5" width="27" height="3" fill={cross} />{inner && <><rect x="8.8" width="1.4" height="18" fill={inner} /><rect y="8.3" width="27" height="1.4" fill={inner} /></>}</>;
  } else if (normalized === "jp") {
    flag = <><rect width="27" height="18" fill="#ffffff" /><circle cx="13.5" cy="9" r="4.5" fill="#bc002d" /></>;
  } else if (normalized === "sg") {
    flag = <><rect width="27" height="9" fill="#ef3340" /><rect y="9" width="27" height="9" fill="#ffffff" /><circle cx="7" cy="4.7" r="3.1" fill="#ffffff" /><circle cx="8.2" cy="4.7" r="2.6" fill="#ef3340" />{[[10.6, 2.2], [12, 4], [11.5, 6.2], [9.5, 6.8], [9.2, 3.4]].map(([x, y]) => <circle key={`${x}-${y}`} cx={x} cy={y} r=".45" fill="#ffffff" />)}</>;
  } else if (normalized === "kz") {
    flag = <><rect width="27" height="18" fill="#00afca" /><path d="M3 1v16M5 1v16" stroke="#f6c600" strokeWidth=".7" strokeDasharray="1 1" /><circle cx="16" cy="6.5" r="2.2" fill="#f6c600" /><path d="M10 11.5q6 4 12 0-6 2-12 0Z" fill="#f6c600" /></>;
  } else if (normalized === "by") {
    flag = <><rect width="27" height="12" fill="#ce1720" /><rect y="12" width="27" height="6" fill="#007c30" /><rect width="4" height="18" fill="#ffffff" /><path d="M.5 1.5 3.5 4.5.5 7.5l3 3-3 3 3 3" stroke="#ce1720" strokeWidth="1" fill="none" /></>;
  } else if (normalized === "us") {
    flag = <><rect width="27" height="18" fill="#ffffff" />{[0, 4, 8, 12, 16].map((y) => <rect key={y} y={y} width="27" height="2" fill="#b22234" />)}<rect width="12" height="9.8" fill="#3c3b6e" />{[[2, 2], [5, 2], [8, 2], [3.5, 5], [6.5, 5], [9.5, 5], [2, 8], [5, 8], [8, 8]].map(([x, y]) => <circle key={`${x}-${y}`} cx={x} cy={y} r=".45" fill="#ffffff" />)}</>;
  } else if (normalized === "pl" || normalized === "ua") {
    const colors = normalized === "pl" ? ["#ffffff", "#dc143c"] : ["#0057b7", "#ffd700"];
    flag = <><rect width="27" height="9" fill={colors[0]} /><rect y="9" width="27" height="9" fill={colors[1]} /></>;
  } else if (normalized === "ch") {
    flag = <><rect width="27" height="18" fill="#da291c" /><path d="M11 3h5v4h4v4h-4v4h-5v-4H7V7h4Z" fill="#ffffff" /></>;
  } else if (normalized === "cz") {
    flag = <><rect width="27" height="9" fill="#ffffff" /><rect y="9" width="27" height="9" fill="#d7141a" /><path d="M0 0 12 9 0 18Z" fill="#11457e" /></>;
  } else if (normalized === "ae") {
    flag = <><rect width="27" height="6" fill="#00732f" /><rect y="6" width="27" height="6" fill="#ffffff" /><rect y="12" width="27" height="6" fill="#000000" /><rect width="7" height="18" fill="#ff0000" /></>;
  } else if (normalized === "tr") {
    flag = <><rect width="27" height="18" fill="#e30a17" /><circle cx="11" cy="9" r="5" fill="#ffffff" /><circle cx="12.6" cy="9" r="4" fill="#e30a17" /><path d="m17 6.7.7 1.5 1.7.2-1.2 1.2.3 1.7-1.5-.8-1.5.8.3-1.7-1.2-1.2 1.7-.2Z" fill="#ffffff" /></>;
  } else if (normalized === "in") {
    flag = <><rect width="27" height="6" fill="#ff9933" /><rect y="6" width="27" height="6" fill="#ffffff" /><rect y="12" width="27" height="6" fill="#138808" /><circle cx="13.5" cy="9" r="2.2" fill="none" stroke="#000080" strokeWidth=".7" /></>;
  } else if (normalized === "il") {
    flag = <><rect width="27" height="18" fill="#ffffff" /><rect y="2" width="27" height="2" fill="#0038b8" /><rect y="14" width="27" height="2" fill="#0038b8" /><path d="m13.5 5 3.4 6h-6.8Zm0 8-3.4-6h6.8Z" fill="none" stroke="#0038b8" strokeWidth=".7" /></>;
  } else if (normalized === "ca") {
    flag = <><rect width="27" height="18" fill="#ffffff" /><rect width="6" height="18" fill="#d80621" /><rect x="21" width="6" height="18" fill="#d80621" /><path d="m13.5 3 1.2 3 2-.8-.7 2 2 .9-2.8 2.2.6 3-2.3-1.2-2.3 1.2.6-3L9 8.1l2-.9-.7-2 2 .8Z" fill="#d80621" /></>;
  } else if (normalized === "gb") {
    flag = <><rect width="27" height="18" fill="#012169" /><path d="M0 0 27 18M27 0 0 18" stroke="#ffffff" strokeWidth="4" /><path d="M0 0 27 18M27 0 0 18" stroke="#c8102e" strokeWidth="1.7" /><path d="M11 0h5v18h-5zM0 6.5h27v5H0z" fill="#ffffff" /><path d="M12 0h3v18h-3zM0 7.5h27v3H0z" fill="#c8102e" /></>;
  } else if (normalized === "pt") {
    flag = <><rect width="10" height="18" fill="#046a38" /><rect x="10" width="17" height="18" fill="#da291c" /><circle cx="10" cy="9" r="3" fill="#ffcc00" /><circle cx="10" cy="9" r="1.7" fill="#ffffff" /></>;
  } else if (normalized === "br") {
    flag = <><rect width="27" height="18" fill="#009c3b" /><path d="m13.5 2 10 7-10 7-10-7Z" fill="#ffdf00" /><circle cx="13.5" cy="9" r="3.7" fill="#002776" /></>;
  } else if (normalized === "kr") {
    flag = <><rect width="27" height="18" fill="#ffffff" /><path d="M13.5 5a4 4 0 0 1 0 8 2 2 0 0 0 0-4 2 2 0 0 1 0-4Z" fill="#cd2e3a" /><path d="M13.5 13a4 4 0 0 1 0-8 2 2 0 0 0 0 4 2 2 0 0 1 0 4Z" fill="#0047a0" /></>;
  } else if (normalized === "au") {
    flag = <><rect width="27" height="18" fill="#012169" /><path d="M0 0 12 8M12 0 0 8" stroke="#ffffff" strokeWidth="2" /><path d="M5 0h2v8H5zM0 3h12v2H0z" fill="#ffffff" /><path d="M5.5 0h1v8h-1zM0 3.5h12v1H0z" fill="#c8102e" />{[[19, 4], [22, 9], [17, 13], [23, 15]].map(([x, y]) => <circle key={`${x}-${y}`} cx={x} cy={y} r=".8" fill="#ffffff" />)}</>;
  }
  if (!flag) return <span className="gateCountryFlag unknown" role="img" aria-label={label}>◎</span>;
  return <span className="gateCountryFlag" role="img" aria-label={label}><svg viewBox="0 0 27 18" aria-hidden="true"><g clipPath="url(#country-flag-clip)">{flag}</g><defs><clipPath id="country-flag-clip"><rect width="27" height="18" rx="2" /></clipPath></defs></svg></span>;
}
