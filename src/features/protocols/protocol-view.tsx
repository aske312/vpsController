"use client";

import type { Dispatch, SetStateAction } from "react";
import { formatModuleVersion } from "../../shared/lib/format-version";
import { bytes, duration, safeDateTime } from "../../shared/lib/control-plane-ui";
import { ProtocolHealthBadge } from "../../shared/components/protocol-health-badge";
import { ProtocolIcon } from "../../shared/components/protocol-icon";
import type { Protocol, ProtocolImage, ProtocolStatus, Tab } from "../../shared/types/control-plane";

const checkLabels: Record<string, string> = {
  route: "Route", dns: "DNS", https: "HTTPS", protocol_service: "Service",
  service: "Service", udp: "UDP port", forwarding: "Forwarding", mtu: "MTU",
  pmtu: "Path MTU", load: "Load", services: "System", listener: "Ports",
  endpoint_dns: "Endpoint DNS", endpoint: "Endpoint", reality_target: "Target TLS",
  configuration: "Config", diagnostic: "Check",
};

type ProtocolViewProps = {
  protocolTab: Protocol;
  activeProtocol: ProtocolStatus;
  activeProtocolRate: { rx: number; tx: number };
  activeProtocolImage?: ProtocolImage;
  protocolCode: string;
  protocolIsTunnel: boolean;
  protocolOperational: boolean;
  protocolAvailability: number;
  protocolDiagnosticsLabel: string;
  protocolResourceAvailable: number;
  protocolResourceTotal: number;
  installedProtocols: Protocol[];
  setTab: Dispatch<SetStateAction<Tab>>;
  onSelectProtocol?: (protocol: Protocol) => void;
  diagnosticsOpen: Partial<Record<Protocol, boolean>>;
  resourcesOpen: Partial<Record<Protocol, boolean>>;
  checkingDiagnostics: Protocol | null;
  checkingResources: Protocol | null;
  installingProtocol: string;
  busy: boolean;
  updateProtocol: (image: ProtocolImage) => Promise<void> | void;
  removeProtocol: (image: ProtocolImage) => Promise<void> | void;
  toggleNetworkDiagnostics: (protocol: Protocol) => void;
  checkNetworkDiagnostics: (protocol: Protocol) => Promise<void> | void;
  toggleProtocolResources: (protocol: Protocol) => void;
  checkProtocolResources: (protocol: Protocol) => Promise<void> | void;
};

type Capability = { name: string; detail: string; beta?: boolean; route?: "cdn" | "tls" | "udp" };
type TunnelProfile = {
  short: string;
  family: string;
  title: string;
  description: string;
  accent: string;
  capabilities: Capability[];
  planned: Capability[];
};

const profiles: Record<Protocol, TunnelProfile> = {
  wg: {
    short: "WG", family: "KERNEL VPN", title: "WireGuard", accent: "cyan",
    description: "Минимальный UDP-туннель с нативным интерфейсом, ключами и предсказуемой маршрутизацией.",
    capabilities: [
      { name: "Kernel tunnel", detail: "Нативный интерфейс и маршруты" },
      { name: "Peer keys", detail: "Отдельные ключи клиентов" },
      { name: "UDP transport", detail: "Минимальные накладные расходы" },
    ],
    planned: [],
  },
  awg: {
    short: "AWG", family: "STEALTH VPN", title: "AmneziaWG", accent: "mint",
    description: "WireGuard-совместимый защищённый канал с независимой обфускацией и собственным runtime.",
    capabilities: [
      { name: "Obfuscation", detail: "Параметры маскировки AWG" },
      { name: "Independent keys", detail: "Собственный набор peer-ключей" },
      { name: "UDP tunnel", detail: "Отдельный сетевой интерфейс" },
    ],
    planned: [],
  },
  shadowsocks: {
    short: "SS", family: "ENCRYPTED PROXY", title: "Shadowsocks", accent: "blue",
    description: "Лёгкий шифрованный proxy-runtime для TCP/UDP с отдельными клиентскими профилями.",
    capabilities: [
      { name: "AEAD cipher", detail: "Современное симметричное шифрование" },
      { name: "TCP + UDP", detail: "Два класса трафика" },
      { name: "Client ports", detail: "Изолированные точки доступа" },
    ],
    planned: [],
  },
  "vless-reality-xhttp": {
    short: "VLESS", family: "XRAY TRANSPORT", title: "VLESS", accent: "violet",
    description: "Модуль Xray с независимыми входами REALITY, прямым TLS и CDN-маршрутом.",
    capabilities: [
      { name: "REALITY", detail: "Прямой маскируемый вход" },
      { name: "TLS route", detail: "Независимый TLS-домен", route: "tls" },
      { name: "CDN route", detail: "XHTTP / WS / gRPC через CDN", route: "cdn" },
    ],
    planned: [],
  },
  hysteria2: {
    short: "HY2", family: "QUIC PROXY", title: "Hysteria2", accent: "amber",
    description: "Высокопроизводительный QUIC-runtime с TLS и поддержкой TCP/UDP поверх UDP-транспорта.",
    capabilities: [
      { name: "QUIC", detail: "UDP-транспорт с congestion control" },
      { name: "TLS 1.3", detail: "Защищённая точка входа" },
      { name: "Per-client auth", detail: "Раздельная аутентификация" },
    ],
    planned: [],
  },
  tuic: {
    short: "TUIC", family: "QUIC PROXY", title: "TUIC v5", accent: "amber",
    description: "Независимый QUIC-прокси с UUID/password-аутентификацией и нативной поддержкой UDP relay.",
    capabilities: [
      { name: "TUIC v5", detail: "Современный QUIC transport" },
      { name: "UUID auth", detail: "Изолированные учётные данные" },
      { name: "UDP relay", detail: "Нативный UDP-трафик" },
    ],
    planned: [],
  },
  trojan: {
    short: "TRJ", family: "TLS PROXY", title: "Trojan", accent: "rose",
    description: "TLS-прокси с индивидуальными паролями и независимым серверным сертификатом.",
    capabilities: [
      { name: "TLS tunnel", detail: "TCP поверх TLS" },
      { name: "Client password", detail: "Раздельная аутентификация" },
      { name: "Certificate", detail: "Контролируемое доверие" },
    ],
    planned: [],
  },
  openvpn: {
    short: "OVPN", family: "CERTIFICATE VPN", title: "OpenVPN", accent: "green",
    description: "Классический TUN-VPN с отдельным сертификатом на устройство, tls-crypt и отзывом через CRL.",
    capabilities: [
      { name: "Client certificate", detail: "X.509 на каждое устройство" },
      { name: "tls-crypt", detail: "Защита control channel" },
      { name: "CRL", detail: "Отзыв клиентского доступа" },
    ],
    planned: [],
  },
  ikev2: {
    short: "IKE", family: "SYSTEM VPN", title: "IKEv2", accent: "green",
    description: "Системный IPsec VPN на strongSwan для клиентов ОС без отдельного VPN-приложения.",
    capabilities: [
      { name: "Native clients", detail: "Windows / iOS / macOS" },
      { name: "IPsec", detail: "IKEv2 + ESP/NAT-T" },
      { name: "EAP auth", detail: "Учётная запись на устройство" },
    ],
    planned: [],
  },
};

export function ProtocolView(props: ProtocolViewProps) {
  const {
    protocolTab, activeProtocol, activeProtocolRate, activeProtocolImage,
    protocolIsTunnel, protocolOperational, protocolAvailability, protocolDiagnosticsLabel,
    protocolResourceAvailable, protocolResourceTotal, installedProtocols, setTab, onSelectProtocol,
    diagnosticsOpen, resourcesOpen, checkingDiagnostics, checkingResources,
    installingProtocol, busy, updateProtocol, removeProtocol,
    toggleNetworkDiagnostics, checkNetworkDiagnostics,
    toggleProtocolResources, checkProtocolResources,
  } = props;
  const profile = profiles[protocolTab];
  const readOnly = activeProtocolImage?.management?.state !== "managed" || Boolean(activeProtocolImage?.management?.retained);
  const routeReady = {
    tls: Boolean(activeProtocol.routes?.tls?.enabled),
    cdn: Boolean(activeProtocol.routes?.cdn?.enabled),
    udp: false,
  };
  const availability = Number.isFinite(Number(protocolAvailability)) ? Math.max(0, Math.min(100, Number(protocolAvailability))) : 0;
  const version = formatModuleVersion(activeProtocolImage?.installed_version, "version n/a");
  const endpoint = activeProtocol.listen_port ? `${activeProtocol.address || "—"}:${activeProtocol.listen_port}` : activeProtocol.address || "—";
  const health = protocolOperational ? (activeProtocol.diagnostics?.status || "healthy") : "critical";
  const updateBusy = activeProtocolImage ? installingProtocol === `update-${activeProtocolImage.id}` : false;

  return (
    <section className={`protocolWorkspace tunnelsWorkspace tunnelAccent-${profile.accent} protocol-${protocolTab}`}>
      {installedProtocols.length > 1 && (
        <nav className="tunnelModuleRail" aria-label="Установленные Tunnels-модули">
          <span className="tunnelRailTitle">MODULES</span>
          {installedProtocols.map((protocol) => {
            const item = profiles[protocol];
            return (
              <button
                key={protocol}
                type="button"
                className={protocol === protocolTab ? "active" : ""}
                aria-label={item.title}
                onClick={() => onSelectProtocol ? onSelectProtocol(protocol) : setTab(protocol)}
              >
                <b><ProtocolIcon protocol={protocol} /></b>
              </button>
            );
          })}
        </nav>
      )}

      <header className="tunnelHero">
        <div className="tunnelHeroCopy">
          <p className="eyebrow">TUNNELS / {profile.family}</p>
          <div className="tunnelTitleRow">
            <span className="tunnelHeroIcon"><ProtocolIcon protocol={protocolTab} /></span><h1>{profile.title}</h1>
            <ProtocolHealthBadge health={activeProtocol.health} />
          </div>
          <p className="tunnelLead">{profile.description}</p>
          <div className="tunnelHeroMeta">
            <span>{version}</span>
            <span>{activeProtocol.service_enabled ? "AUTOSTART" : "MANUAL START"}</span>
            <span>{protocolIsTunnel ? "L3 TUNNEL" : activeProtocol.transport || profile.family}</span>
            {activeProtocolImage?.update_available && <span className="warning">UPDATE AVAILABLE</span>}
            {activeProtocol.diagnostics?.checks?.map((check) => <span key={check.id} className="protocolCheck" data-state={check.ok ? "READY" : check.severity === "warning" ? "WARN" : "ERROR"} title={`${check.name}: ${check.value}`} aria-label={`${check.name}: ${check.ok ? "OK" : "Проблема"}. ${check.value}`}>{checkLabels[check.id] || check.name}</span>)}
            {!activeProtocol.diagnostics?.checks?.length && <span>{activeProtocol.health?.reason || "Диагностика выполняется"}</span>}
          </div>
        </div>
        <div className="tunnelOperatorPlaceholder" data-asset="operator_prt_1.webp" aria-label="Заглушка изображения operator_prt_1.webp">
          <span>VISUAL SLOT</span><strong>operator_prt_1.webp</strong><small>Изображение намеренно не рендерится</small>
        </div>
      </header>

      {readOnly && <p role="status">Только просмотр и диагностика. Для изменения компонента примите его под управление в разделе «Обзор».</p>}
      <div className="tunnelCommandBar">
        <div>
          <span className={`healthDot ${health}`} />
          <p><small>RUNTIME</small><strong>{protocolOperational ? "ACTIVE" : "STOPPED"}</strong><span>{activeProtocol.unit || activeProtocol.interface || profile.title}</span></p>
        </div>
        <div className="tunnelCommandActions">
          <button type="button" onClick={() => setTab("services")}>Управление службой</button>
          <button type="button" onClick={() => toggleNetworkDiagnostics(protocolTab)} disabled={busy || checkingDiagnostics === protocolTab}>{checkingDiagnostics === protocolTab ? "Проверяем…" : "Проверить сеть"}</button>
          {activeProtocolImage?.update_available && <button type="button" className="accent" onClick={() => void updateProtocol(activeProtocolImage)} disabled={busy || readOnly}>{updateBusy ? "Обновляем…" : "Обновить"}</button>}
          {activeProtocolImage?.removable && <button type="button" className="danger" onClick={() => void removeProtocol(activeProtocolImage)} disabled={busy || readOnly}>Удалить модуль</button>}
        </div>
      </div>

      <div className="tunnelDashboardGrid">
        <article className="tunnelPanel runtimePanel">
          <PanelTitle eyebrow="RUNTIME" title="Состояние канала" note="Фактические данные backend" />
          <div className="runtimeFacts">
            <Fact label="Endpoint" value={endpoint} />
            <Fact label="Interface" value={activeProtocol.interface || "—"} />
            <Fact label="Transport" value={activeProtocol.transport || (protocolIsTunnel ? "UDP" : "—")} />
            <Fact label="Security" value={activeProtocol.security || "—"} />
            <Fact label="MTU" value={activeProtocol.mtu ? String(activeProtocol.mtu) : "—"} />
            <Fact label="Clients" value={`${activeProtocol.online_peers}/${activeProtocol.peers}`} />
          </div>
        </article>

        <article className="tunnelPanel telemetryPanel">
          <PanelTitle eyebrow="TELEMETRY" title="Трафик и доступность" note={`${activeProtocol.history.samples} замеров / ${activeProtocol.history.period_hours || 24}ч`} />
          <div className="telemetryGrid">
            <Metric label="RX NOW" value={`${bytes(activeProtocolRate.rx)}/с`} detail={`${bytes(activeProtocol.history.received_bytes)} за период`} />
            <Metric label="TX NOW" value={`${bytes(activeProtocolRate.tx)}/с`} detail={`${bytes(activeProtocol.history.transmitted_bytes)} за период`} />
            <Metric label="AVAILABILITY" value={`${availability}%`} detail={`${activeProtocol.history.service_interruptions} остановок`} />
            <Metric label="LATENCY" value={activeProtocol.history.latency_avg_ms != null ? `${activeProtocol.history.latency_avg_ms.toFixed(1)} мс` : "—"} detail={`last ${duration(activeProtocol.last_handshake_age_s)}`} />
          </div>
          <div className="availabilityTrack"><i style={{ width: `${availability}%` }} /></div>
        </article>

        <article className="tunnelPanel capabilitiesPanel">
          <PanelTitle eyebrow="CAPABILITIES" title="Возможности модуля" note="Доступные функции текущей конфигурации" />
          <div className="capabilityList">
            {profile.capabilities.filter((item) => !item.route || routeReady[item.route]).map((item) => <CapabilityRow key={item.name} item={item} />)}
          </div>
        </article>
      </div>

      <div className="tunnelDiagnosticsGrid">
        <article className={`tunnelPanel diagnosticPanel ${health}`}>
          <button type="button" className="diagnosticHeader" onClick={() => toggleNetworkDiagnostics(protocolTab)} aria-expanded={Boolean(diagnosticsOpen[protocolTab])}>
            <span><small>DIAGNOSTICS</small><strong>Диагностика и события · {protocolDiagnosticsLabel}</strong><em>{activeProtocol.diagnostics?.score != null ? `${activeProtocol.diagnostics.score}/100` : "Проверка не запускалась"}</em></span>
            <b>{diagnosticsOpen[protocolTab] ? "Скрыть" : "Открыть"}</b>
          </button>
          {diagnosticsOpen[protocolTab] && <div className="diagnosticBody">
            <div className="diagnosticActionRow"><span>{activeProtocol.diagnostics?.checked_at ? `Проверено ${safeDateTime(activeProtocol.diagnostics.checked_at)}` : "Ожидание проверки"}</span><button type="button" onClick={() => void checkNetworkDiagnostics(protocolTab)} disabled={checkingDiagnostics === protocolTab}>{checkingDiagnostics === protocolTab ? "Проверяем…" : "Проверить сеть"}</button></div>
            <div className="diagnosticRows">{(activeProtocol.diagnostics?.checks || []).map((check) => <div className={check.ok ? "ok" : "failed"} key={check.id}><i /><span><strong>{check.name}</strong><small>{check.value}</small></span></div>)}{!activeProtocol.diagnostics?.checks?.length && <p className="emptyState">Нет результатов диагностики.</p>}</div>
            {(activeProtocol.diagnostics?.findings || []).map((finding) => <div className={`finding ${finding.severity}`} key={finding.code}><b>{finding.title}</b><span>{finding.detail}</span><small>{finding.action}</small></div>)}
          </div>}
        </article>

        <article className="tunnelPanel diagnosticPanel resources">
          <button type="button" className="diagnosticHeader" onClick={() => toggleProtocolResources(protocolTab)} aria-expanded={Boolean(resourcesOpen[protocolTab])}>
            <span><small>DEPENDENCIES</small><strong>Внешние ресурсы</strong><em>{protocolResourceTotal ? `${protocolResourceAvailable}/${protocolResourceTotal} доступны` : "Не проверено"}</em></span>
            <b>{resourcesOpen[protocolTab] ? "Скрыть" : "Открыть"}</b>
          </button>
          {resourcesOpen[protocolTab] && <div className="diagnosticBody">
            <div className="diagnosticActionRow"><span>{activeProtocol.resources?.checked_at ? `Проверено ${safeDateTime(activeProtocol.resources.checked_at)}` : "Ожидание проверки"}</span><button type="button" onClick={() => void checkProtocolResources(protocolTab)} disabled={checkingResources === protocolTab}>{checkingResources === protocolTab ? "Проверяем…" : "Проверить"}</button></div>
            <div className="resourceRows">{(activeProtocol.resources?.items || []).map((item) => <div className={item.available ? "ok" : "failed"} key={item.name}><span>{item.name}</span><strong>{item.available ? `${item.latency_ms} мс` : "Недоступен"}</strong></div>)}{!activeProtocol.resources?.items?.length && <p className="emptyState">Нет результатов проверки.</p>}</div>
          </div>}
        </article>

        <article className="tunnelPanel eventsPanel">
          <PanelTitle eyebrow="EVENTS" title="События стабильности" note={`Последние ${activeProtocol.history.period_hours || 24}ч`} />
          <div className="eventRows">{activeProtocol.history.events?.length ? activeProtocol.history.events.slice(0, 5).map((event, index) => <div key={`${event.at}-${index}`}><i className={event.type === "service_down" ? "critical" : "warning"} /><span><strong>{event.type === "service_down" ? "Служба остановлена" : event.type === "monitor_gap" ? "Пропуск мониторинга" : "Нет активных соединений"}</strong><small>{safeDateTime(event.at)}{event.seconds ? ` · ${event.seconds} сек` : ""}</small></span></div>) : <p className="emptyState">Событий за период не зафиксировано.</p>}</div>
        </article>
      </div>

    </section>
  );
}

function PanelTitle({ eyebrow, title, note }: { eyebrow: string; title: string; note: string }) {
  return <header className="tunnelPanelTitle"><div><small>{eyebrow}</small><h2>{title}</h2></div><span>{note}</span></header>;
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div><small>{label}</small><strong>{value}</strong><span>{detail}</span></div>;
}

function CapabilityRow({ item }: { item: Capability }) {
  return <div className={item.beta ? "beta" : "ready"}><i /><span><strong>{item.name}{item.beta && <em>BETA</em>}</strong><small>{item.detail}</small></span>{item.beta ? <b>NOT CONNECTED</b> : <b>READY</b>}</div>;
}
