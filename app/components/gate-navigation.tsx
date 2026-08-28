"use client";

import type { ReactNode } from "react";
import { BrandGlyph } from "./brand-glyph";

type ProtocolImage = {
  id: string;
  name: string;
  installed: boolean;
};

type ServerInfo = {
  city?: string;
  country?: string;
  public_endpoint?: string;
  public_ip?: string;
};

type Props = {
  activeTab: string;
  protocolImages: ProtocolImage[];
  showConnections: boolean;
  nodeState: string;
  nodeStateLabel: string;
  server?: ServerInfo;
  onNavigate: (tab: string) => void;
};

const protocolOrder = ["awg", "wg", "vless-reality-xhttp", "shadowsocks"];
export function GateNavigation({
  activeTab,
  protocolImages,
  showConnections,
  nodeState,
  nodeStateLabel,
  server,
  onNavigate,
}: Props) {
  const mihomoInstalled = protocolImages.some((item) => item.id === "mihomo" && item.installed);
  const transports = protocolOrder
    .map((id) => protocolImages.find((item) => item.id === id && item.installed))
    .filter((item): item is ProtocolImage => Boolean(item));

  return (
    <aside className="gateSidebar">
      <button className="gateBrand" type="button" onClick={() => onNavigate("overview")} aria-label="Открыть обзор">
        <span className="gateBrandMark"><BrandGlyph /></span>
        <span><strong>312<span>.net</span></strong><small>INFRASTRUCTURE</small></span>
      </button>

      <nav className="gateNav" aria-label="Основная навигация">
        <NavGroup label="WORKSPACE">
          <NavButton active={activeTab === "overview"} icon="overview" label="Обзор" onClick={() => onNavigate("overview")} />
          {showConnections && (
            <NavButton active={activeTab === "clients"} icon="connections" label="Подключения" onClick={() => onNavigate("clients")} />
          )}
        </NavGroup>

        {(mihomoInstalled || transports.length > 0) && (
          <NavGroup label="ROUTING">
            {mihomoInstalled && (
              <NavButton active={activeTab === "mihomo"} icon="mihomo" label="Mihomo" tone="violet" onClick={() => onNavigate("mihomo")} />
            )}
            {transports.length > 0 && (
              <NavButton
                active={activeTab === "channels" || transports.some((item) => activeTab === item.id)}
                icon="transport"
                label="Защищённые каналы"
                tone="cyan"
                onClick={() => onNavigate("channels")}
              />
            )}
          </NavGroup>
        )}

        <NavGroup label="INFRASTRUCTURE">
          <NavButton active={activeTab === "dns"} icon="network" label="Сеть и DNS" badge="BETA" onClick={() => onNavigate("dns")} />
          <NavButton active={activeTab === "security"} icon="security" label="Безопасность" onClick={() => onNavigate("security")} />
        </NavGroup>

        <NavGroup label="SYSTEM">
          <NavButton active={activeTab === "application"} icon="application" label="Приложение" onClick={() => onNavigate("application")} />
          <NavButton active={activeTab === "services"} icon="services" label="Службы" onClick={() => onNavigate("services")} />
        </NavGroup>
      </nav>

      <div className={`gateSidebarNode ${nodeState}`}>
        <span className="gateNodePulse" />
        <div>
          <small>{nodeStateLabel}</small>
          <strong>{server?.city || "VPS"}</strong>
          <span className="gateNodeLocation">{server?.country || "—"}</span>
          <span className="gateNodeAddress">{server?.public_endpoint || server?.public_ip || "—"}</span>
        </div>
      </div>
    </aside>
  );
}

function NavGroup({ label, children }: { label: string; children: ReactNode }) {
  return <section className="gateNavGroup"><p>{label}</p><div>{children}</div></section>;
}

function NavButton({
  active,
  icon,
  label,
  tone = "blue",
  compact = false,
  badge,
  onClick,
}: {
  active: boolean;
  icon: "overview" | "connections" | "mihomo" | "transport" | "network" | "security" | "application" | "services";
  label: string;
  tone?: "blue" | "violet" | "cyan";
  compact?: boolean;
  badge?: string;
  onClick: () => void;
}) {
  return (
    <button type="button" className={`gateNavButton ${active ? "active" : ""} ${tone} ${compact ? "compact" : ""} ${badge ? "hasBadge" : ""}`} onClick={onClick}>
      <span className="gateNavGlyph"><NavGlyph name={icon} /></span>
      <b>{label}</b>
      {badge && <span className="gateNavBeta">{badge}</span>}
    </button>
  );
}

function NavGlyph({ name }: { name: string }) {
  if (name === "overview") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 11.5 12 5l8 6.5V20a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1v-8.5Z" /></svg>;
  if (name === "connections") return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="7" r="2.5"/><circle cx="18" cy="17" r="2.5"/><path d="m8.3 10.9 7.4-3M8.3 13.1l7.4 3"/></svg>;
  if (name === "mihomo") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2 8 4.5v11L12 22l-8-4.5v-11L12 2Z"/><path d="m8 16V8l4 4 4-4v8"/></svg>;
  if (name === "transport") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h11M12 4l3 3-3 3M20 17H9M12 14l-3 3 3 3"/></svg>;
  if (name === "network") return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.3 3 14.7 0 18M12 3c-3 3.3-3 14.7 0 18"/></svg>;
  if (name === "security") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 20 6v5c0 5.2-3.2 8.5-8 10-4.8-1.5-8-4.8-8-10V6l8-3Z"/><path d="m9 12 2 2 4-5"/></svg>;
  if (name === "application") return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 9h16M8 7h.01M11 7h.01"/></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14v5H5zM5 15h14v5H5z"/><path d="M8 6.5h.01M8 17.5h.01M11 6.5h5M11 17.5h5"/></svg>;
}
