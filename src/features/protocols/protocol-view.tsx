"use client";

import type { Dispatch, SetStateAction } from "react";
import { formatModuleVersion } from "../../shared/lib/format-version";
import { bytes, duration, safeDateTime } from "../../shared/lib/control-plane-ui";
import type { EditableProtocolSetting, Protocol, ProtocolImage, ProtocolStatus, Tab } from "../../shared/types/control-plane";

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
  protocolSettingsDraft: Partial<Record<Protocol, Record<string, string | number | boolean>>>;
  diagnosticsOpen: Partial<Record<Protocol, boolean>>;
  resourcesOpen: Partial<Record<Protocol, boolean>>;
  checkingDiagnostics: Protocol | null;
  checkingResources: Protocol | null;
  installingProtocol: string;
  busy: boolean;
  restartProtocol: (protocol: Protocol) => Promise<void> | void;
  updateProtocol: (image: ProtocolImage) => Promise<void> | void;
  removeProtocol: (image: ProtocolImage) => Promise<void> | void;
  changeProtocolSetting: (protocol: Protocol, key: string, value: string | number | boolean) => void;
  saveProtocolSettings: (protocol: Protocol) => Promise<void> | void;
  toggleNetworkDiagnostics: (protocol: Protocol) => void;
  checkNetworkDiagnostics: (protocol: Protocol) => Promise<void> | void;
  toggleProtocolResources: (protocol: Protocol) => void;
  checkProtocolResources: (protocol: Protocol) => Promise<void> | void;
};

type Capability = { name: string; detail: string; beta?: boolean };
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
    planned: [
      { name: "Adaptive MTU", detail: "Автоподбор MTU по Path MTU", beta: true },
      { name: "Relay route", detail: "Переключаемый внешний UDP relay", beta: true },
    ],
  },
  awg: {
    short: "AWG", family: "STEALTH VPN", title: "AmneziaWG", accent: "mint",
    description: "WireGuard-совместимый защищённый канал с независимой обфускацией и собственным runtime.",
    capabilities: [
      { name: "Obfuscation", detail: "Параметры маскировки AWG" },
      { name: "Independent keys", detail: "Собственный набор peer-ключей" },
      { name: "UDP tunnel", detail: "Отдельный сетевой интерфейс" },
    ],
    planned: [
      { name: "Obfuscation presets", detail: "Профили под разные сети", beta: true },
      { name: "Fallback endpoint", detail: "Резервный endpoint/relay", beta: true },
    ],
  },
  shadowsocks: {
    short: "SS", family: "ENCRYPTED PROXY", title: "Shadowsocks", accent: "blue",
    description: "Лёгкий шифрованный proxy-runtime для TCP/UDP с отдельными клиентскими профилями.",
    capabilities: [
      { name: "AEAD cipher", detail: "Современное симметричное шифрование" },
      { name: "TCP + UDP", detail: "Два класса трафика" },
      { name: "Client ports", detail: "Изолированные точки доступа" },
    ],
    planned: [
      { name: "Port rotation", detail: "Управляемая смена внешнего порта", beta: true },
      { name: "Relay chain", detail: "Промежуточный TCP/UDP relay", beta: true },
    ],
  },
  "vless-reality-xhttp": {
    short: "VLESS", family: "XRAY TRANSPORT", title: "VLESS", accent: "violet",
    description: "Модуль Xray с независимыми входами REALITY, прямым TLS и CDN-маршрутом.",
    capabilities: [
      { name: "REALITY", detail: "Прямой маскируемый вход" },
      { name: "TLS route", detail: "Независимый TLS-домен" },
      { name: "CDN route", detail: "XHTTP / WS / gRPC через CDN" },
    ],
    planned: [
      { name: "ECH profile", detail: "Подготовка ECH/CDN-профиля", beta: true },
      { name: "Route failover", detail: "Автовыбор живого входа", beta: true },
    ],
  },
  hysteria2: {
    short: "HY2", family: "QUIC PROXY", title: "Hysteria2", accent: "amber",
    description: "Высокопроизводительный QUIC-runtime с TLS и поддержкой TCP/UDP поверх UDP-транспорта.",
    capabilities: [
      { name: "QUIC", detail: "UDP-транспорт с congestion control" },
      { name: "TLS 1.3", detail: "Защищённая точка входа" },
      { name: "Per-client auth", detail: "Раздельная аутентификация" },
    ],
    planned: [
      { name: "Bandwidth profiles", detail: "Пресеты uplink/downlink", beta: true },
      { name: "UDP relay", detail: "Внешняя relay-точка", beta: true },
    ],
  },
  tuic: {
    short: "TUIC", family: "QUIC PROXY", title: "TUIC v5", accent: "amber",
    description: "Независимый QUIC-прокси с UUID/password-аутентификацией и нативной поддержкой UDP relay.",
    capabilities: [
      { name: "TUIC v5", detail: "Современный QUIC transport" },
      { name: "UUID auth", detail: "Изолированные учётные данные" },
      { name: "UDP relay", detail: "Нативный UDP-трафик" },
    ],
    planned: [
      { name: "Congestion preset", detail: "Управление алгоритмом congestion", beta: true },
      { name: "Fallback port", detail: "Резервная точка входа", beta: true },
    ],
  },
  trojan: {
    short: "TRJ", family: "TLS PROXY", title: "Trojan", accent: "rose",
    description: "TLS-прокси с индивидуальными паролями и независимым серверным сертификатом.",
    capabilities: [
      { name: "TLS tunnel", detail: "TCP поверх TLS" },
      { name: "Client password", detail: "Раздельная аутентификация" },
      { name: "Certificate", detail: "Контролируемое доверие" },
    ],
    planned: [
      { name: "SNI rotation", detail: "Управляемая смена домена", beta: true },
      { name: "TLS relay", detail: "Внешняя промежуточная точка", beta: true },
    ],
  },
  openvpn: {
    short: "OVPN", family: "CERTIFICATE VPN", title: "OpenVPN", accent: "green",
    description: "Классический TUN-VPN с отдельным сертификатом на устройство, tls-crypt и отзывом через CRL.",
    capabilities: [
      { name: "Client certificate", detail: "X.509 на каждое устройство" },
      { name: "tls-crypt", detail: "Защита control channel" },
      { name: "CRL", detail: "Отзыв клиентского доступа" },
    ],
    planned: [
      { name: "TCP fallback", detail: "Резервный TCP-listener", beta: true },
      { name: "Profile policy", detail: "Раздельные route-профили", beta: true },
    ],
  },
  ikev2: {
    short: "IKE", family: "SYSTEM VPN", title: "IKEv2", accent: "green",
    description: "Системный IPsec VPN на strongSwan для клиентов ОС без отдельного VPN-приложения.",
    capabilities: [
      { name: "Native clients", detail: "Windows / iOS / macOS" },
      { name: "IPsec", detail: "IKEv2 + ESP/NAT-T" },
      { name: "EAP auth", detail: "Учётная запись на устройство" },
    ],
    planned: [
      { name: "Split policy", detail: "Профили split-routing", beta: true },
      { name: "Certificate rotation", detail: "Контролируемая ротация X.509", beta: true },
    ],
  },
};

export function ProtocolView(props: ProtocolViewProps) {
  const {
    protocolTab, activeProtocol, activeProtocolRate, activeProtocolImage,
    protocolIsTunnel, protocolOperational, protocolAvailability, protocolDiagnosticsLabel,
    protocolResourceAvailable, protocolResourceTotal, installedProtocols, setTab, onSelectProtocol,
    protocolSettingsDraft, diagnosticsOpen, resourcesOpen, checkingDiagnostics, checkingResources,
    installingProtocol, busy, restartProtocol, updateProtocol, removeProtocol, changeProtocolSetting,
    saveProtocolSettings, toggleNetworkDiagnostics, checkNetworkDiagnostics,
    toggleProtocolResources, checkProtocolResources,
  } = props;
  const profile = profiles[protocolTab];
  const draft = protocolSettingsDraft[protocolTab] || {};
  const fields = activeProtocol.editable_settings || [];
  const availability = Number.isFinite(Number(protocolAvailability)) ? Math.max(0, Math.min(100, Number(protocolAvailability))) : 0;
  const version = formatModuleVersion(activeProtocolImage?.installed_version, "version n/a");
  const endpoint = activeProtocol.listen_port ? `${activeProtocol.address || "—"}:${activeProtocol.listen_port}` : activeProtocol.address || "—";
  const health = activeProtocol.diagnostics?.status || "pending";
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
                <b>{item.short}</b>
              </button>
            );
          })}
        </nav>
      )}

      <header className="tunnelHero">
        <div className="tunnelHeroCopy">
          <p className="eyebrow">TUNNELS / {profile.family}</p>
          <div className="tunnelTitleRow">
            <h1>{profile.title}</h1>
            <span className={protocolOperational ? "tunnelState online" : "tunnelState offline"}>{protocolOperational ? "ACTIVE" : "STOPPED"}</span>
          </div>
          <p className="tunnelLead">{profile.description}</p>
          <div className="tunnelHeroMeta">
            <span>{version}</span>
            <span>{activeProtocol.service_enabled ? "AUTOSTART" : "MANUAL START"}</span>
            <span>{protocolIsTunnel ? "L3 TUNNEL" : activeProtocol.transport || profile.family}</span>
            {activeProtocolImage?.update_available && <span className="warning">UPDATE AVAILABLE</span>}
          </div>
        </div>
        <div className="tunnelOperatorPlaceholder" data-asset="operator_prt_1.webp" aria-label="Заглушка изображения operator_prt_1.webp">
          <span>VISUAL SLOT</span><strong>operator_prt_1.webp</strong><small>Изображение намеренно не рендерится</small>
        </div>
      </header>

      <div className="tunnelCommandBar">
        <div>
          <span className={`healthDot ${health}`} />
          <p><small>RUNTIME</small><strong>{activeProtocol.unit || activeProtocol.interface || profile.title}</strong></p>
        </div>
        <div className="tunnelCommandActions">
          <button type="button" onClick={() => void restartProtocol(protocolTab)} disabled={busy}>Перезапустить</button>
          {activeProtocolImage?.update_available && <button type="button" className="accent" onClick={() => void updateProtocol(activeProtocolImage)} disabled={busy}>{updateBusy ? "Обновляем…" : "Обновить"}</button>}
          {activeProtocolImage?.removable && <button type="button" className="danger" onClick={() => void removeProtocol(activeProtocolImage)} disabled={busy}>Удалить модуль</button>}
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
          <PanelTitle eyebrow="CAPABILITIES" title="Возможности модуля" note="Реализованные и запланированные функции разделены" />
          <div className="capabilityList">
            {profile.capabilities.map((item) => <CapabilityRow key={item.name} item={item} />)}
            {profile.planned.map((item) => <CapabilityRow key={item.name} item={item} />)}
          </div>
        </article>
      </div>

      <article className="tunnelPanel settingsPanel">
        <PanelTitle eyebrow="CONFIGURATION" title="Настройки модуля" note="Сохраняется существующая backend-логика применения и отката" />
        <ProtocolSettingsEditor
          protocol={protocolTab}
          fields={fields}
          draft={draft}
          busy={busy}
          onChange={(key, value) => changeProtocolSetting(protocolTab, key, value)}
          onSave={() => void saveProtocolSettings(protocolTab)}
        />
      </article>

      <div className="tunnelDiagnosticsGrid">
        <article className={`tunnelPanel diagnosticPanel ${health}`}>
          <button type="button" className="diagnosticHeader" onClick={() => toggleNetworkDiagnostics(protocolTab)} aria-expanded={Boolean(diagnosticsOpen[protocolTab])}>
            <span><small>DIAGNOSTICS</small><strong>Диагностика и события · {protocolDiagnosticsLabel}</strong><em>{activeProtocol.diagnostics?.score != null ? `${activeProtocol.diagnostics.score}/100` : "Проверка не запускалась"}</em></span>
            <b>{diagnosticsOpen[protocolTab] ? "Скрыть" : "Открыть"}</b>
          </button>
          {diagnosticsOpen[protocolTab] && <div className="diagnosticBody">
            <div className="diagnosticActionRow"><span>{activeProtocol.diagnostics?.checked_at ? `Проверено ${safeDateTime(activeProtocol.diagnostics.checked_at)}` : "Ожидание проверки"}</span>{protocolIsTunnel && <button type="button" onClick={() => void checkNetworkDiagnostics(protocolTab)} disabled={checkingDiagnostics === protocolTab}>{checkingDiagnostics === protocolTab ? "Проверяем…" : "Проверить сеть"}</button>}</div>
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

      <aside className="betaNotice">
        <span>BETA</span>
        <p><strong>Экспериментальные функции</strong><small>Жёлтые элементы — проектируемые возможности. Пока backend не подключён, они не являются рабочими настройками и не меняют конфигурацию сервера.</small></p>
      </aside>
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

function ProtocolSettingsEditor({
  protocol, fields, draft, busy, onChange, onSave,
}: {
  protocol: Protocol;
  fields: EditableProtocolSetting[];
  draft: Record<string, string | number | boolean>;
  busy: boolean;
  onChange: (key: string, value: string | number | boolean) => void;
  onSave: () => void;
}) {
  if (!fields.length) return <p className="emptyState settingsEmpty">Для этого модуля backend пока не предоставляет изменяемые параметры.</p>;

  const valueOf = (key: string) => draft[key] ?? fields.find((field) => field.key === key)?.value;
  const transport = String(valueOf("transport") || "xhttp");
  const cdnEnabled = Boolean(valueOf("cdn_enabled"));
  const tlsEnabled = Boolean(valueOf("tls_enabled"));
  const tlsTransport = String(valueOf("tls_transport") || "xhttp");
  const cdnTransport = String(valueOf("cdn_transport") || "websocket");

  let visible = fields;
  if (protocol === "vless-reality-xhttp") {
    visible = visible.filter((field) => transport === "xhttp" || !["xhttp_mode", "xpadding", "xmux_concurrency"].includes(field.key));
    visible = visible.filter((field) => cdnEnabled || !field.key.startsWith("cdn_") || field.key === "cdn_enabled");
    visible = visible.filter((field) => tlsEnabled || !field.key.startsWith("tls_") || field.key === "tls_enabled");
    visible = visible.filter((field) => field.key !== "cdn_xhttp_mode" || (cdnEnabled && cdnTransport === "xhttp"));
    visible = visible.filter((field) => field.key !== "tls_xhttp_mode" || (tlsEnabled && tlsTransport === "xhttp"));
  }

  return <div className="tunnelSettingsEditor">
    <div className="tunnelSettingsFields">
      {visible.map((field) => <label key={field.key} className={field.type === "boolean" ? "booleanField" : ""}>
        <span><strong>{field.label}</strong>{field.help && <small>{field.help}</small>}</span>
        {field.type === "boolean" ? <input type="checkbox" checked={Boolean(draft[field.key] ?? field.value)} onChange={(event) => onChange(field.key, event.target.checked)} />
          : field.type === "select" ? <select value={String(draft[field.key] ?? field.value)} onChange={(event) => onChange(field.key, event.target.value)}>{(field.options || []).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
            : field.type === "number" ? <input type="number" min={field.min} max={field.max} value={Number(draft[field.key] ?? field.value)} onChange={(event) => onChange(field.key, Number(event.target.value))} />
              : <input type="text" value={String(draft[field.key] ?? field.value)} onChange={(event) => onChange(field.key, event.target.value)} />}
      </label>)}
    </div>
    <div className="tunnelSettingsActions"><span>Изменяются только параметры, которые уже предоставляет backend этого модуля.</span><button type="button" onClick={onSave} disabled={busy}>{busy ? "Применяем…" : "Применить настройки"}</button></div>
  </div>;
}
