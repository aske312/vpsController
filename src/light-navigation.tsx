"use client";

import type { ReactNode } from "react";
import { ProtocolIcon } from "../app/protocol-icon";

type ProtocolImage = { id: string; name: string; installed: boolean; kind?: "tunnel" | "agent" };
type ServerInfo = { city?: string; country?: string; public_ip?: string };

type Props = {
  activeTab: string;
  protocolImages: ProtocolImage[];
  clientsCount: number;
  nodeState: "gray" | "green" | "yellow" | "red" | "blue";
  nodeStateLabel: string;
  server?: ServerInfo;
  children?: ReactNode;
  onNavigate: (tab: string) => void;
};

export function LightNavigation({ activeTab, protocolImages, clientsCount, nodeState, nodeStateLabel, server, children, onNavigate }: Props) {
  const protocols = protocolImages.filter((item) => item.installed && item.kind !== "agent");
  const selectedProtocol = protocols.find((item) => item.id === activeTab) || protocols[0];
  return <aside className="gateSidebar">
    <button className="gateBrand" type="button" onClick={() => onNavigate("overview")} aria-label="Открыть обзор">
      <span className="gateBrandMark"><BrandGlyph /></span>
      <span><strong>312node<span>.net</span></strong><small>LIGHT / VPS CONTROL</small></span>
    </button>
    <nav className="gateNav" aria-label="Основная навигация">
      <NavGroup label="РАБОЧАЯ ОБЛАСТЬ">
        <NavButton active={activeTab === "overview"} icon="overview" label="Обзор" onClick={() => onNavigate("overview")} />
        <NavButton active={activeTab === "clients"} icon="connections" label="Подключения" badge={String(clientsCount)} onClick={() => onNavigate("clients")} />
      </NavGroup>
      {protocols.length > 0 && <NavGroup label="ПРОТОКОЛЫ">
        {protocols.length === 1
          ? <button type="button" aria-current={activeTab === protocols[0].id ? "page" : undefined} aria-label={protocols[0].name} title={protocols[0].name} className={`gateNavButton cyan compact ${activeTab === protocols[0].id ? "active" : ""}`} onClick={() => onNavigate(protocols[0].id)}>
              <span className="gateNavGlyph protocolGlyph"><ProtocolIcon protocol={protocols[0].id} /></span><b>{protocols[0].name}</b>
            </button>
          : <NavButton active={protocols.some((protocol) => activeTab === protocol.id)} icon="transport" label="Протоколы" badge={String(protocols.length)} onClick={() => selectedProtocol && onNavigate(selectedProtocol.id)} />}
      </NavGroup>}
      <NavGroup label="СИСТЕМА">
        <NavButton active={activeTab === "security"} icon="security" label="Безопасность" onClick={() => onNavigate("security")} />
        <NavButton active={activeTab === "application"} icon="application" label="Приложение" onClick={() => onNavigate("application")} />
        <NavButton active={activeTab === "services"} icon="services" label="Службы" onClick={() => onNavigate("services")} />
      </NavGroup>
    </nav>
    {children || <div className={`gateSidebarNode ${nodeState}`}><span className="gateNodePulse" /><div><small>{nodeStateLabel}</small><strong>{server?.city || "VPS"}</strong><span className="gateNodeLocation">{server?.country || "—"}</span><span className="gateNodeAddress">{server?.public_ip || "—"}</span></div></div>}
  </aside>;
}

function BrandGlyph() {
  return <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M4.5 5.5h23L16 27 4.5 5.5Z" /><path d="m16 10 6 11H10l6-11Z" /></svg>;
}

function NavGroup({ label, children }: { label: string; children: ReactNode }) {
  return <section className="gateNavGroup"><p>{label}</p><div>{children}</div></section>;
}

function NavButton({ active, icon, label, badge, onClick }: { active: boolean; icon: string; label: string; badge?: string; onClick: () => void }) {
  return <button type="button" aria-current={active ? "page" : undefined} aria-label={label} title={label} className={`gateNavButton ${active ? "active" : ""} ${badge ? "hasBadge" : ""}`} onClick={onClick}><span className="gateNavGlyph"><NavGlyph name={icon} /></span><b>{label}</b>{badge && <span className="gateNavBeta">{badge}</span>}</button>;
}

function NavGlyph({ name }: { name: string }) {
  if (name === "overview") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 11.5 12 5l8 6.5V20a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1v-8.5Z" /></svg>;
  if (name === "connections") return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="7" r="2.5"/><circle cx="18" cy="17" r="2.5"/><path d="m8.3 10.9 7.4-3M8.3 13.1l7.4 3"/></svg>;
  if (name === "transport") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h11M12 4l3 3-3 3M20 17H9M12 14l-3 3 3 3"/></svg>;
  if (name === "security") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 20 6v5c0 5.2-3.2 8.5-8 10-4.8-1.5-8-4.8-8-10V6l8-3Z"/><path d="m9 12 2 2 4-5"/></svg>;
  if (name === "application") return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 9h16M8 7h.01M11 7h.01"/></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14v5H5zM5 15h14v5H5z"/><path d="M8 6.5h.01M8 17.5h.01M11 6.5h5M11 17.5h5"/></svg>;
}
