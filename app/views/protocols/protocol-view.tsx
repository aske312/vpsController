"use client";

import { formatModuleVersion } from "../../lib/format-version";

import Image from "next/image";
import type { Dispatch, SetStateAction } from "react";
import { bytes, duration, labels, safeDateTime } from "../../lib/control-plane-ui";
import type { EditableProtocolSetting, Protocol, ProtocolImage, ProtocolStatus, Tab } from "../../types/control-plane";

type ProtocolViewProps = {
  protocolTab: Protocol; activeProtocol: ProtocolStatus; activeProtocolRate: { rx: number; tx: number }; activeProtocolImage?: ProtocolImage; protocolCode: string; protocolIsTunnel: boolean; protocolOperational: boolean; protocolAvailability: number; protocolDiagnosticsLabel: string; protocolResourceAvailable: number; protocolResourceTotal: number; installedProtocols: Protocol[];
  setTab: Dispatch<SetStateAction<Tab>>; onSelectProtocol?: (protocol: Protocol) => void; protocolSettingsDraft: Partial<Record<Protocol, Record<string, string | number | boolean>>>; diagnosticsOpen: Partial<Record<Protocol, boolean>>; resourcesOpen: Partial<Record<Protocol, boolean>>; checkingDiagnostics: Protocol | null; checkingResources: Protocol | null; installingProtocol: string; busy: boolean;
  restartProtocol: (protocol: Protocol) => Promise<void> | void; updateProtocol: (image: ProtocolImage) => Promise<void> | void; removeProtocol: (image: ProtocolImage) => Promise<void> | void; changeProtocolSetting: (protocol: Protocol, key: string, value: string | number | boolean) => void; saveProtocolSettings: (protocol: Protocol) => Promise<void> | void;
  toggleNetworkDiagnostics: (protocol: Protocol) => void; checkNetworkDiagnostics: (protocol: Protocol) => Promise<void> | void; toggleProtocolResources: (protocol: Protocol) => void; checkProtocolResources: (protocol: Protocol) => Promise<void> | void;
};

const channelProfiles: Record<Protocol, {
  index: string; family: string; title: string; lead: string; signature: string;
  features: string[]; runtimeLabel: string; healthLabel: string;
}> = {
  wg: {
    index: "01", family: "KERNEL TUNNEL", title: "WireGuard",
    lead: "Прямой минималистичный туннель. Здесь важны интерфейс, маршрут, handshake и предсказуемая скорость.",
    signature: "LEAN / NATIVE / UDP", features: ["UDP tunnel", "Kernel interface", "Public key"],
    runtimeLabel: "Туннель и маршрутизация", healthLabel: "Handshake и трафик",
  },
  awg: {
    index: "02", family: "STEALTH TUNNEL", title: "AmneziaWG",
    lead: "Управляемый защищённый контур с обфускацией WireGuard-трафика и собственным интерфейсом.",
    signature: "OBFUSCATED / CONTROLLED", features: ["UDP tunnel", "Obfuscation", "Independent keys"],
    runtimeLabel: "Защищённый контур", healthLabel: "Канал и доступность",
  },
  shadowsocks: {
    index: "03", family: "ENCRYPTED PROXY", title: "Shadowsocks",
    lead: "Лёгкий шифрованный proxy для TCP и UDP. Каждый профиль работает как отдельный управляемый канал.",
    signature: "STREAM / TCP + UDP", features: ["AEAD cipher", "TCP + UDP", "Per-client port"],
    runtimeLabel: "Proxy runtime", healthLabel: "Потоки и соединения",
  },
  "vless-reality-xhttp": {
    index: "04", family: "MODULAR TRANSPORT", title: "VLESS",
    lead: "Два независимых маршрута: прямой VLESS через REALITY и дополнительная точка входа через CDN.",
    signature: "VLESS / REALITY + TLS / XRAY", features: ["Direct: XHTTP · RAW · gRPC", "CDN: XHTTP · WS · gRPC", "Shared UUID"],
    runtimeLabel: "Состав и runtime", healthLabel: "Потоки Xray",
  },
  hysteria2: {
    index: "05", family: "QUIC PROXY", title: "Hysteria2",
    lead: "Высокопроизводительный прокси поверх QUIC с индивидуальной аутентификацией и управляемым TLS-контуром.",
    signature: "QUIC / UDP / TLS 1.3", features: ["TCP + UDP", "Per-client auth", "Traffic Stats API"],
    runtimeLabel: "QUIC runtime", healthLabel: "Сессии и трафик",
  },
  tuic: {
    index: "06", family: "QUIC PROXY", title: "TUIC v5",
    lead: "Независимый TUIC v5 runtime с индивидуальными UUID и паролями, нативным UDP relay и отключённым 0-RTT.",
    signature: "TUIC V5 / QUIC / TLS 1.3", features: ["TCP + UDP", "Per-client UUID", "Replay-safe handshake"],
    runtimeLabel: "TUIC runtime", healthLabel: "Служба и общий трафик",
  },
  trojan: { index:"07", family:"TLS PROXY", title:"Trojan", lead:"Независимый TLS-прокси с индивидуальными паролями и встроенным доверием к серверному сертификату.", signature:"TROJAN / TCP / TLS 1.3", features:["TCP proxy","Per-client password","Pinned certificate"], runtimeLabel:"Trojan runtime", healthLabel:"Служба и общий трафик" },
  openvpn: { index:"08", family:"CERTIFICATE VPN", title:"OpenVPN", lead:"Классический VPN-туннель с отдельным сертификатом для каждого устройства и отзывом через CRL.", signature:"OPENVPN / TLS / TUN", features:["Client certificate","tls-crypt","CRL revocation"], runtimeLabel:"OpenVPN runtime", healthLabel:"Служба и трафик клиентов" },
  ikev2: { index:"09", family:"SYSTEM VPN", title:"IKEv2", lead:"Нативный системный VPN на strongSwan с сертификатом сервера и отдельной EAP-учётной записью на устройство.", signature:"IKEV2 / IPSEC / X.509", features:["Native OS clients","EAP-MSCHAPv2","NAT traversal"], runtimeLabel:"strongSwan runtime", healthLabel:"IKE SA и общий трафик" },
};

export function ProtocolView(props: ProtocolViewProps) {
  const { protocolTab, activeProtocol, activeProtocolRate, activeProtocolImage, protocolCode, protocolIsTunnel, protocolOperational, protocolAvailability, protocolDiagnosticsLabel, protocolResourceAvailable, protocolResourceTotal, installedProtocols, setTab, onSelectProtocol, protocolSettingsDraft, diagnosticsOpen, resourcesOpen, checkingDiagnostics, checkingResources, installingProtocol, busy, restartProtocol, updateProtocol, removeProtocol, changeProtocolSetting, saveProtocolSettings, toggleNetworkDiagnostics, checkNetworkDiagnostics, toggleProtocolResources, checkProtocolResources } = props;
  const isVless = protocolTab === "vless-reality-xhttp";
  const profile = channelProfiles[protocolTab];
  if (["wg", "awg", "shadowsocks", "vless-reality-xhttp", "hysteria2", "tuic", "trojan", "openvpn", "ikev2"].includes(protocolTab)) return <ProtocolCommandCenter props={props} />;
  return <section className={`protocolWorkspace protocolWorkspace-${protocolCode.toLowerCase()} protocol-${protocolTab}`}>
        {installedProtocols.length > 1 && <nav className="protocolSwitcher" aria-label="Установленные защищённые протоколы">
          {installedProtocols.map((protocol) => <button type="button" key={protocol} className={`protocol-${protocol}${protocol === protocolTab ? " active" : ""}`} aria-current={protocol === protocolTab ? "page" : undefined} onClick={() => onSelectProtocol ? onSelectProtocol(protocol) : setTab(protocol)}>{protocol === "wg" ? "WG" : protocol === "awg" ? "AWG" : protocol === "shadowsocks" ? "SS" : protocol === "hysteria2" ? "HY2" : protocol === "tuic" ? "TUIC" : protocol === "trojan" ? "TRJ" : protocol === "openvpn" ? "OVPN" : protocol === "ikev2" ? "IKE" : "VLESS"}</button>)}
        </nav>}
        <header className="protocolHeader">
          <div className="protocolIdentityMark" aria-hidden="true"><span>{profile.index}</span><b>{protocolCode}</b></div>
          <div className="protocolHeaderCopy">
            <p className="eyebrow">{profile.family} · {profile.signature}</p>
            <h1>{profile.title}</h1>
            <p className="protocolLead">{profile.lead}</p>
            <div className="protocolFeatureLine">{profile.features.map((feature) => <span key={feature}>{feature}</span>)}</div>
          </div>
          <div className="protocolHeaderMeta">
            <div className="protocolHeaderState">
              <span className={protocolOperational ? "online" : "offline"}>{protocolOperational ? "ACTIVE" : "STOPPED"}</span>
              <span className={activeProtocol.service_enabled ? "online" : "muted"}>{activeProtocol.service_enabled ? "AUTOSTART" : "MANUAL"}</span>
              <span className="version">{formatModuleVersion(activeProtocolImage?.installed_version, "version n/a")}</span>
              {activeProtocolImage?.update_available && <span className={`version update${activeProtocolImage.update_breaking ? " breaking" : ""}`}>UPDATE → {formatModuleVersion(activeProtocolImage.available_version)}</span>}
            </div>
            <small>CHANNEL {profile.index} / {installedProtocols.length.toString().padStart(2, "0")}</small>
          </div>
        </header>

        <div className="protocolStage">
          <div className="protocolMain">
            <div className="protocolSummaryGrid">
              <article className="protocolRuntime">
                <header>
                  <div><p className="eyebrow">{profile.family}</p><h2>{profile.runtimeLabel}</h2></div>
                  <span className={protocolOperational ? "state online" : "state offline"}>{protocolOperational ? "Работает" : "Остановлен"}</span>
                </header>
                <dl className="protocolRuntimeFacts">
                  <div><dt>{isVless ? "ЯДРО" : "ИНТЕРФЕЙС"}</dt><dd>{isVless ? `Xray ${formatModuleVersion(activeProtocolImage?.installed_version, "—")}` : activeProtocol.interface || "—"}</dd></div>
                  <div><dt>ПРОТОКОЛ</dt><dd>{labels[protocolTab]}</dd></div>
                  <div><dt>{isVless ? "ВХОД" : "АДРЕС"}</dt><dd>{isVless ? `${activeProtocol.address || "—"}:${activeProtocol.listen_port || "—"}` : activeProtocol.address || "—"}</dd></div>
                  {!isVless && <div><dt>ПОРТ</dt><dd>{activeProtocol.listen_port || "—"}</dd></div>}
                  <div><dt>TRANSPORT</dt><dd>{activeProtocol.transport || (protocolIsTunnel ? "UDP" : "—")}</dd></div>
                  {!protocolIsTunnel && <div><dt>SECURITY</dt><dd>{activeProtocol.security || "—"}</dd></div>}
                  {!protocolIsTunnel && <div><dt>{isVless ? "МАСКИРОВКА" : "TARGET"}</dt><dd>{activeProtocol.target || "Прямой выход"}</dd></div>}
                  <div><dt>КЛИЕНТЫ</dt><dd>{activeProtocol.online_peers}/{activeProtocol.peers} online</dd></div>
                  <div><dt>ЗАПУЩЕН С</dt><dd>{safeDateTime(activeProtocol.active_since)}</dd></div>
                  <div><dt>SYSTEMD</dt><dd>{activeProtocol.unit || (activeProtocol.service_active ? "active" : "inactive")}</dd></div>
                </dl>
                <div className="protocolRuntimeActions">
                  <button onClick={() => void restartProtocol(protocolTab)} disabled={busy}>Перезапустить</button>
                  {activeProtocolImage?.update_available && (
                    <button className={activeProtocolImage.update_breaking ? "warning" : "accent"} onClick={() => void updateProtocol(activeProtocolImage)} disabled={busy}>
                      {installingProtocol === `update-${activeProtocolImage.id}` ? "Обновление…" : activeProtocolImage.id === "awg" ? "Обновить из репозитория" : `Обновить до ${activeProtocolImage.available_version}`}
                    </button>
                  )}
                  {activeProtocolImage?.removable && <button className="danger" onClick={() => void removeProtocol(activeProtocolImage)} disabled={busy}>Удалить протокол</button>}
                </div>
              </article>

              <article className="protocolHealth">
                <header><div><p className="eyebrow">TRAFFIC & HEALTH</p><h2>{profile.healthLabel}</h2></div><span>{activeProtocol.history.samples} замеров / {activeProtocol.history.period_hours || 24}ч</span></header>
                <div className="protocolHealthMetrics">
                  <div className="rx"><small>RX NOW</small><strong>{bytes(activeProtocolRate.rx)}<em>/с</em></strong><span>{bytes(activeProtocol.history.received_bytes)} за период</span></div>
                  <div className="tx"><small>TX NOW</small><strong>{bytes(activeProtocolRate.tx)}<em>/с</em></strong><span>{bytes(activeProtocol.history.transmitted_bytes)} за период</span></div>
                  <div><small>ДОСТУПНОСТЬ</small><strong>{protocolAvailability != null ? `${protocolAvailability}%` : "—"}</strong><span>{activeProtocol.history.service_interruptions} остановок</span><i className="availability"><b style={{ width: `${Math.max(0, Math.min(100, Number(protocolAvailability) || 0))}%` }} /></i></div>
                  <div><small>LATENCY</small><strong>{activeProtocol.history.latency_avg_ms != null ? `${activeProtocol.history.latency_avg_ms.toFixed(1)} мс` : "—"}</strong><span>max {activeProtocol.history.latency_max_ms != null ? `${activeProtocol.history.latency_max_ms.toFixed(1)} мс` : "—"}</span></div>
                  <div><small>LOSS / CLIENTS</small><strong>{activeProtocol.history.external_loss_percent != null ? `${activeProtocol.history.external_loss_percent}%` : "—"}</strong><span>{activeProtocol.online_peers}/{activeProtocol.peers} online  {duration(activeProtocol.last_handshake_age_s)}</span></div>
                </div>
              </article>
            </div>

            <ProtocolSettingsPanel protocol={protocolTab} fields={activeProtocol.editable_settings || []} draft={protocolSettingsDraft[protocolTab] || {}} busy={busy}
              onChange={(key, value) => changeProtocolSetting(protocolTab, key, value)} onSave={() => void saveProtocolSettings(protocolTab)} />

            <article className="protocolDiagnostics">
              <header className="protocolDiagnosticsHead">
                <div><p className="eyebrow">DIAGNOSTICS</p><h2>Диагностика и события</h2><span>Runtime, внешние ресурсы и события за последние {activeProtocol.history.period_hours || 24} часов.</span></div>
                <div className="protocolDiagnosticsSummary"><span className={activeProtocol.diagnostics?.status || "pending"}>{protocolDiagnosticsLabel}</span><span>{protocolResourceAvailable}/{protocolResourceTotal || 0} ресурсов</span></div>
              </header>
              <div className="protocolDiagnosticsGrid">
                <section className={`protocolDiagnosticUnit ${activeProtocol.diagnostics?.status || "pending"} ${diagnosticsOpen[protocolTab] ? "open" : ""}`}>
                  <button className="protocolDiagnosticToggle" onClick={() => toggleNetworkDiagnostics(protocolTab)} aria-expanded={Boolean(diagnosticsOpen[protocolTab])}>
                    <span><small>{protocolIsTunnel ? "NETWORK" : "RUNTIME"}</small><strong>{protocolIsTunnel ? "Причины нестабильности" : "Состояние runtime"}</strong><em>{protocolDiagnosticsLabel}{activeProtocol.diagnostics?.score != null ? `  ${activeProtocol.diagnostics.score}/100` : ""}</em></span><b>{diagnosticsOpen[protocolTab] ? "Скрыть" : "Открыть"}</b>
                  </button>
                  {diagnosticsOpen[protocolTab] && <div className="protocolDiagnosticBody">
                    <div className="protocolDiagnosticActions"><span>{activeProtocol.diagnostics?.checked_at ? `Проверено ${safeDateTime(activeProtocol.diagnostics.checked_at)}` : "Ожидание проверки"}</span>{protocolIsTunnel && <button onClick={() => void checkNetworkDiagnostics(protocolTab)} disabled={checkingDiagnostics === protocolTab}>{checkingDiagnostics === protocolTab ? "Проверяем…" : "Проверить"}</button>}</div>
                    <div className="protocolDiagnosticChecks">{(activeProtocol.diagnostics?.checks || []).map((check) => <div className={check.ok ? "ok" : "failed"} key={check.id}><i /><p><strong>{check.name}</strong><small>{check.value}</small></p></div>)}{!activeProtocol.diagnostics?.checks?.length && <p className="protocolDiagnosticEmpty">Данные появятся после проверки.</p>}</div>
                    {(activeProtocol.diagnostics?.findings || []).length > 0 && <div className="protocolDiagnosticFindings">{activeProtocol.diagnostics.findings.map((finding) => <div className={finding.severity} key={finding.code}><strong>{finding.title}</strong><span>{finding.detail}</span><small>{finding.action}</small></div>)}</div>}
                  </div>}
                </section>

                <section className={`protocolDiagnosticUnit resources ${resourcesOpen[protocolTab] ? "open" : ""}`}>
                  <button className="protocolDiagnosticToggle" onClick={() => toggleProtocolResources(protocolTab)} aria-expanded={Boolean(resourcesOpen[protocolTab])}>
                    <span><small>RESOURCES</small><strong>Внешние сервисы VPS</strong><em>{protocolResourceTotal ? `${protocolResourceAvailable} из ${protocolResourceTotal} доступны` : "Не проверено"}</em></span><b>{resourcesOpen[protocolTab] ? "Скрыть" : "Открыть"}</b>
                  </button>
                  {resourcesOpen[protocolTab] && <div className="protocolDiagnosticBody">
                    <div className="protocolDiagnosticActions"><span>{activeProtocol.resources?.checked_at ? `Проверено ${safeDateTime(activeProtocol.resources.checked_at)}` : "Ожидание проверки"}</span><button onClick={() => void checkProtocolResources(protocolTab)} disabled={checkingResources === protocolTab}>{checkingResources === protocolTab ? "Проверяем…" : "Проверить"}</button></div>
                    <div className="protocolResourceList">{(activeProtocol.resources?.items || []).map((resource) => <div className={resource.available ? "online" : "offline"} key={resource.name}><span>{resource.name}</span><strong>{resource.available ? `${resource.latency_ms} мс` : "Недоступен"}</strong></div>)}{!activeProtocol.resources?.items?.length && <p className="protocolDiagnosticEmpty">Данные появятся после проверки.</p>}</div>
                  </div>}
                </section>

                <section className="protocolDiagnosticUnit events">
                  <div className="protocolEventHead"><span><small>STABILITY LOG</small><strong>Последние события</strong><em>агрегация за {activeProtocol.history.period_hours || 24}ч</em></span><b>{activeProtocol.history.events.length}</b></div>
                  <div className="protocolEventRows">{activeProtocol.history.events.length ? activeProtocol.history.events.slice(0, 4).map((event, index) => <div key={`${event.at}-${index}`}><i className={event.type === "service_down" ? "critical" : "warning"} /><p><strong>{event.type === "service_down" ? "Служба протокола остановилась" : event.type === "monitor_gap" ? "Пропуск мониторинга" : "Нет активных соединений"}</strong><small>{safeDateTime(event.at)}{event.seconds ? `  ${event.seconds} сек` : ""}</small></p></div>) : <p className="protocolDiagnosticEmpty">За выбранный период разрывов и остановок не зафиксировано.</p>}</div>
                </section>
              </div>
            </article>
          </div>

          <aside className="protocolArtRail" aria-hidden="true">
            <Image className="protocolArtImage" src="/gate-art/new-operator/operator_prt_1.webp" alt="" width={941} height={1672} priority />
          </aside>
        </div>
      </section>;
}

function ProtocolCommandCenter({ props }: { props: ProtocolViewProps }) {
  const {
    protocolTab, activeProtocol, activeProtocolRate, activeProtocolImage, protocolAvailability,
    protocolDiagnosticsLabel, protocolResourceAvailable, protocolResourceTotal,
    installedProtocols, setTab, onSelectProtocol, protocolSettingsDraft,
    diagnosticsOpen, resourcesOpen, checkingDiagnostics, checkingResources, installingProtocol, busy,
    restartProtocol, updateProtocol, removeProtocol, changeProtocolSetting,
    saveProtocolSettings, toggleNetworkDiagnostics, toggleProtocolResources,
    checkNetworkDiagnostics, checkProtocolResources,
  } = props;
  const protocol = protocolTab;
  const profile = channelProfiles[protocol];
  if (protocol === "vless-reality-xhttp") return <VlessControlCenter props={props} />;
  const protocolCode = protocol === "wg" ? "WG" : protocol === "awg" ? "AWG" : protocol === "shadowsocks" ? "SS" : protocol === "hysteria2" ? "HY2" : protocol === "tuic" ? "TUIC" : protocol === "trojan" ? "TRJ" : protocol === "openvpn" ? "OVPN" : protocol === "ikev2" ? "IKE" : "VLESS";
  const draft = protocolSettingsDraft[protocol] || {};
  const fields = activeProtocol.editable_settings || [];
  const events = activeProtocol.history.events || [];
  const routeKind = protocol === "shadowsocks" ? "TCP + UDP PROXY" : protocol === "trojan" ? "TCP + TLS PROXY" : protocol === "openvpn" ? "CERTIFICATE VPN" : protocol === "ikev2" ? "SYSTEM IPSEC VPN" : protocol === "hysteria2" || protocol === "tuic" ? "QUIC + UDP PROXY" : protocol === "awg" ? "OBFUSCATED UDP" : "NATIVE UDP";
  const runtimeName = activeProtocol.interface || profile.title;
  const routeAddress = ["shadowsocks","hysteria2","tuic","trojan","openvpn","ikev2"].includes(protocol) ? `${activeProtocol.address || "—"}:${activeProtocol.listen_port || "—"}` : activeProtocol.address || "—";
  const routeDetailLabel = ["shadowsocks","hysteria2","tuic","trojan","openvpn"].includes(protocol) ? "SECURITY" : "INTERFACE";
  const routeDetail = protocol === "shadowsocks" ? activeProtocol.security || "AEAD" : ["hysteria2","tuic","trojan","openvpn"].includes(protocol) ? activeProtocol.security || "TLS 1.3" : activeProtocol.interface || "—";

  return <section className={`protocolWorkspace protocolWorkspace-${protocolCode.toLowerCase()} protocol-${protocol} protocolCommandCenter vlessWorkspaceNew`}>
    {installedProtocols.length > 1 && <nav className="protocolSwitcher" aria-label="Установленные защищённые протоколы">
      {installedProtocols.map((item) => <button type="button" key={item} className={`protocol-${item}${item === protocol ? " active" : ""}`} aria-current={item === protocol ? "page" : undefined} onClick={() => onSelectProtocol ? onSelectProtocol(item) : setTab(item)}>{item === "wg" ? "WG" : item === "awg" ? "AWG" : item === "shadowsocks" ? "SS" : item === "hysteria2" ? "HY2" : item === "tuic" ? "TUIC" : item === "trojan" ? "TRJ" : item === "openvpn" ? "OVPN" : item === "ikev2" ? "IKE" : "VLESS"}</button>)}
    </nav>}

    <header className="vlessOverviewHead">
      <div className="vlessOverviewIdentity"><span>{protocolCode.slice(0, 1)}</span><div><p className="eyebrow">{profile.family}</p><h1>{profile.title}</h1><p>{profile.lead}</p></div></div>
      <div className="vlessOverviewState"><span className={activeProtocol.service_active ? "online" : "offline"}><i />{activeProtocol.service_active ? `${runtimeName} работает` : `${runtimeName} остановлен`}</span><small>{formatModuleVersion(activeProtocolImage?.installed_version, "версия не определена")}</small></div>
      <div className="vlessOverviewActions"><button onClick={() => void restartProtocol(protocol)} disabled={busy}>Перезапустить</button>{activeProtocolImage?.update_available && <button className="accent" onClick={() => void updateProtocol(activeProtocolImage)} disabled={busy}>{installingProtocol === `update-${activeProtocolImage.id}` ? "Обновляем…" : "Обновить"}</button>}{activeProtocolImage?.removable && <button className="danger" onClick={() => void removeProtocol(activeProtocolImage)} disabled={busy}>Удалить</button>}</div>
    </header>

    <section className="vlessOverviewMetrics" aria-label={`Состояние ${profile.title}`}>
      <div><small>КЛИЕНТЫ</small><strong>{activeProtocol.online_peers}<em>/{activeProtocol.peers}</em></strong><span>сейчас активны</span></div>
      <div><small>ПРИЁМ</small><strong>{bytes(activeProtocolRate.rx)}<em>/с</em></strong><span>{bytes(activeProtocol.history.received_bytes)} за период</span></div>
      <div><small>ПЕРЕДАЧА</small><strong>{bytes(activeProtocolRate.tx)}<em>/с</em></strong><span>{bytes(activeProtocol.history.transmitted_bytes)} за период</span></div>
      <div><small>ДОСТУПНОСТЬ</small><strong>{protocolAvailability != null ? `${protocolAvailability}%` : "—"}</strong><span>{protocolDiagnosticsLabel}</span></div>
      <div><small>ЗАДЕРЖКА</small><strong>{activeProtocol.history.latency_avg_ms != null ? `${activeProtocol.history.latency_avg_ms.toFixed(1)} мс` : "—"}</strong><span>потери {activeProtocol.history.external_loss_percent != null ? `${activeProtocol.history.external_loss_percent}%` : "—"}</span></div>
    </section>

    <section className="vlessContourSection protocolContourSection">
      <header><div><p className="eyebrow">CONNECTION MAP</p><h2>Сетевой контур</h2></div><p>Рабочие параметры канала и его публичная точка подключения.</p></header>
      <div className="vlessContourGrid single"><article className="vlessContourCard enabled">
        <div className="vlessContourTop"><span>01</span><b>{activeProtocol.service_active ? "ГОТОВ" : "ОСТАНОВЛЕН"}</b></div>
        <p>{profile.signature}</p><h3>{routeKind}</h3><small>{profile.runtimeLabel}</small>
        <dl><div><dt>{protocol === "shadowsocks" ? "ENDPOINT" : "АДРЕС"}</dt><dd>{routeAddress}</dd></div><div><dt>{routeDetailLabel}</dt><dd>{routeDetail}</dd></div><div><dt>ТРАНСПОРТ</dt><dd>{activeProtocol.transport || (protocol === "shadowsocks" ? "TCP + UDP" : "UDP")}</dd></div></dl>
      </article></div>
    </section>

    <ProtocolSettingsPanel protocol={protocol} fields={fields} draft={draft} busy={busy}
      onChange={(key, value) => changeProtocolSetting(protocol, key, value)} onSave={() => void saveProtocolSettings(protocol)} />

    <section className="vlessOperations">
      <article className={`vlessOperationCard diagnostics ${diagnosticsOpen[protocol] ? "open" : ""}`}>
        <button className="vlessOperationHead" onClick={() => toggleNetworkDiagnostics(protocol)} aria-expanded={Boolean(diagnosticsOpen[protocol])}>
          <span><small>RUNTIME HEALTH</small><strong>Состояние {runtimeName}</strong><em>{protocolDiagnosticsLabel}{activeProtocol.diagnostics?.score != null ? ` · ${activeProtocol.diagnostics.score}/100` : ""}</em></span><b>{diagnosticsOpen[protocol] ? "Скрыть" : "Открыть"}</b>
        </button>
        {diagnosticsOpen[protocol] && <div className="vlessOperationBody"><div className="vlessOperationTools"><span>{activeProtocol.diagnostics?.checked_at ? `Проверено ${safeDateTime(activeProtocol.diagnostics.checked_at)}` : "Ожидание проверки"}</span><button onClick={() => void checkNetworkDiagnostics(protocol)} disabled={checkingDiagnostics === protocol}>{checkingDiagnostics === protocol ? "Проверяем…" : "Проверить"}</button></div><div className="protocolDiagnosticChecks">{(activeProtocol.diagnostics?.checks || []).map((check) => <div className={check.ok ? "ok" : "failed"} key={check.id}><i /><p><strong>{check.name}</strong><small>{check.value}</small></p></div>)}{!activeProtocol.diagnostics?.checks?.length && <p className="protocolDiagnosticEmpty">Данные появятся после проверки.</p>}</div></div>}
      </article>

      <article className={`vlessOperationCard resources ${resourcesOpen[protocol] ? "open" : ""}`}>
        <button className="vlessOperationHead" onClick={() => toggleProtocolResources(protocol)} aria-expanded={Boolean(resourcesOpen[protocol])}>
          <span><small>EXTERNAL PATH</small><strong>Ресурсы VPS</strong><em>{protocolResourceTotal ? `${protocolResourceAvailable} из ${protocolResourceTotal} доступны` : "Не проверено"}</em></span><b>{resourcesOpen[protocol] ? "Скрыть" : "Открыть"}</b>
        </button>
        {resourcesOpen[protocol] && <div className="vlessOperationBody"><div className="vlessOperationTools"><span>{activeProtocol.resources?.checked_at ? `Проверено ${safeDateTime(activeProtocol.resources.checked_at)}` : "Ожидание проверки"}</span><button onClick={() => void checkProtocolResources(protocol)} disabled={checkingResources === protocol}>{checkingResources === protocol ? "Проверяем…" : "Проверить"}</button></div><div className="protocolResourceList">{(activeProtocol.resources?.items || []).map((resource) => <div className={resource.available ? "online" : "offline"} key={resource.name}><span>{resource.name}</span><strong>{resource.available ? `${resource.latency_ms} мс` : "Недоступен"}</strong></div>)}{!activeProtocol.resources?.items?.length && <p className="protocolDiagnosticEmpty">Данные появятся после проверки.</p>}</div></div>}
      </article>

      <article className="vlessOperationCard events">
        <div className="vlessOperationHead static"><span><small>EVENT STREAM</small><strong>Последние события</strong><em>за {activeProtocol.history.period_hours || 24} часов</em></span><b>{events.length}</b></div>
        <div className="vlessEventStream">{events.length ? events.slice(0, 5).map((event, index) => <div key={`${event.at}-${index}`}><i className={event.type === "service_down" ? "critical" : "warning"} /><p><strong>{event.type === "service_down" ? `${runtimeName} был остановлен` : event.type === "monitor_gap" ? "Пропуск мониторинга" : "Нет активных соединений"}</strong><small>{safeDateTime(event.at)}{event.seconds ? ` · ${event.seconds} сек` : ""}</small></p></div>) : <p className="protocolDiagnosticEmpty">Разрывов и остановок не зафиксировано.</p>}</div>
      </article>
    </section>
  </section>;
}

function ProtocolSettingsPanel({
  protocol, fields, draft, busy, onChange, onSave,
}: {
  protocol: Protocol; fields: EditableProtocolSetting[]; draft: Record<string, string | number | boolean>;
  busy: boolean; onChange: (key: string, value: string | number | boolean) => void; onSave: () => void;
}) {
  const tunnel = protocol === "wg" || protocol === "awg";
  const isVless = protocol === "vless-reality-xhttp";
  return <article className="panel protocolConfiguration">
    <header>
      <div><p className="eyebrow">{isVless ? "VLESS CONFIGURATION" : "CHANNEL CONFIGURATION"}</p><h3>{isVless ? "Конфигурация VLESS" : `Настройки ${labels[protocol]}`}</h3>
        <span>{tunnel ? "MTU применяется сразу; DNS и keepalive — к новым профилям." : isVless ? "Direct и CDN настраиваются отдельно." : "Перед применением конфигурация проверяется, службы перезапускаются автоматически."}</span></div>
      <div className="configurationSafety"><i aria-hidden="true" /><p><strong>Безопасное применение</strong><small>валидация и автоматический откат</small></p></div>
    </header>
    <ProtocolSettingsEditor fields={fields} draft={draft} busy={busy} vless={isVless} onChange={onChange} onSave={onSave} />
  </article>;
}

function VlessControlCenter({ props }: { props: ProtocolViewProps }) {
  const {
    activeProtocol, activeProtocolRate, activeProtocolImage, protocolAvailability,
    protocolDiagnosticsLabel, protocolResourceAvailable, protocolResourceTotal,
    installedProtocols, setTab, onSelectProtocol, protocolSettingsDraft,
    diagnosticsOpen, resourcesOpen, checkingResources, installingProtocol, busy,
    restartProtocol, updateProtocol, removeProtocol, changeProtocolSetting,
    saveProtocolSettings, toggleNetworkDiagnostics, toggleProtocolResources, checkProtocolResources,
  } = props;
  const protocol: Protocol = "vless-reality-xhttp";
  const draft = protocolSettingsDraft[protocol] || {};
  const fields = activeProtocol.editable_settings || [];
  const setting = (key: string, fallback: string | number | boolean = "—") => draft[key] ?? fields.find((field) => field.key === key)?.value ?? fallback;
  type Route = { enabled: boolean; security: string; transport: string; endpoint: string; server_name: string; path: string };
  const routes = (activeProtocol as ProtocolStatus & { routes?: Partial<Record<"direct" | "tls" | "cdn", Route>> }).routes || {};
  const tlsEnabled = Boolean(setting("tls_enabled", false));
  const cdnEnabled = Boolean(setting("cdn_enabled", false));
  const contours = [
    { id: "01", kind: "REALITY", title: "VLESS", enabled: true, route: routes.direct, domain: String(setting("sni", activeProtocol.target?.replace(/:443$/, "") || "—")), note: "Прямой вход без домена сервера" },
    { id: "02", kind: "TLS", title: "VLESS TLS", enabled: routes.tls?.enabled ?? tlsEnabled, route: routes.tls, domain: String(setting("tls_domain", "")), note: "Прямой домен, DNS only" },
    { id: "03", kind: "CDN", title: "VLESS CDN", enabled: routes.cdn?.enabled ?? cdnEnabled, route: routes.cdn, domain: String(setting("cdn_domain", "")), note: "Проксируемый CDN-домен" },
  ];
  return <section className="protocolWorkspace protocolWorkspace-vless protocol-vless-reality-xhttp vlessWorkspaceNew">
    {installedProtocols.length > 1 && <nav className="protocolSwitcher" aria-label="Установленные защищённые протоколы">{installedProtocols.map((item) => <button type="button" key={item} className={`protocol-${item}${item === protocol ? " active" : ""}`} onClick={() => onSelectProtocol ? onSelectProtocol(item) : setTab(item)}>{item === "wg" ? "WG" : item === "awg" ? "AWG" : item === "shadowsocks" ? "SS" : item === "hysteria2" ? "HY2" : item === "tuic" ? "TUIC" : item === "trojan" ? "TRJ" : item === "openvpn" ? "OVPN" : item === "ikev2" ? "IKE" : "VLESS"}</button>)}</nav>}
    <header className="vlessOverviewHead">
      <div className="vlessOverviewIdentity"><span>V</span><div><p className="eyebrow">XRAY ACCESS PLATFORM</p><h1>VLESS</h1><p>Три независимых способа подключения к одному защищённому узлу.</p></div></div>
      <div className="vlessOverviewState"><span className={activeProtocol.service_active ? "online" : "offline"}><i />{activeProtocol.service_active ? "Xray работает" : "Xray остановлен"}</span><small>{formatModuleVersion(activeProtocolImage?.installed_version, "версия не определена")}</small></div>
      <div className="vlessOverviewActions"><button onClick={() => void restartProtocol(protocol)} disabled={busy}>Перезапустить</button>{activeProtocolImage?.update_available && <button className="accent" onClick={() => void updateProtocol(activeProtocolImage)} disabled={busy}>{installingProtocol ? "Обновляем…" : "Обновить"}</button>}{activeProtocolImage?.removable && <button className="danger" onClick={() => void removeProtocol(activeProtocolImage)} disabled={busy}>Удалить</button>}</div>
    </header>
    <section className="vlessOverviewMetrics" aria-label="Состояние VLESS">
      <div><small>КОНТУРЫ</small><strong>{contours.filter((item) => item.enabled).length}<em>/3</em></strong><span>доступны клиентам</span></div>
      <div><small>КЛИЕНТЫ</small><strong>{activeProtocol.online_peers}<em>/{activeProtocol.peers}</em></strong><span>сейчас активны</span></div>
      <div><small>ПРИЁМ</small><strong>{bytes(activeProtocolRate.rx)}<em>/с</em></strong><span>{bytes(activeProtocol.history.received_bytes)} за период</span></div>
      <div><small>ПЕРЕДАЧА</small><strong>{bytes(activeProtocolRate.tx)}<em>/с</em></strong><span>{bytes(activeProtocol.history.transmitted_bytes)} за период</span></div>
      <div><small>ДОСТУПНОСТЬ</small><strong>{protocolAvailability != null ? `${protocolAvailability}%` : "—"}</strong><span>{protocolDiagnosticsLabel}</span></div>
    </section>
    <section className="vlessContourSection">
      <header><div><p className="eyebrow">CONNECTION MAP</p><h2>Контуры подключения</h2></div><p>Домены и транспорт каждого контура настраиваются ниже.</p></header>
      <div className="vlessContourGrid">{contours.map((item) => <article key={item.id} className={`vlessContourCard ${item.enabled ? "enabled" : "disabled"}`}>
        <div className="vlessContourTop"><span>{item.id}</span><b>{item.enabled ? "ГОТОВ" : "НЕ НАСТРОЕН"}</b></div>
        <p>{item.kind}</p><h3>{item.title}</h3><small>{item.note}</small>
        <dl><div><dt>АДРЕС</dt><dd>{item.route?.endpoint || item.domain || "—"}</dd></div><div><dt>ЗАЩИТА</dt><dd>{item.route?.security || item.kind}</dd></div><div><dt>ТРАНСПОРТ</dt><dd>{String(item.route?.transport || setting(item.kind === "REALITY" ? "transport" : item.kind === "TLS" ? "tls_transport" : "cdn_transport", "—")).toUpperCase()}</dd></div></dl>
        {!item.enabled && <span className="vlessContourHint">Включается в настройках VLESS</span>}
      </article>)}</div>
    </section>
    <ProtocolSettingsPanel protocol={protocol} fields={fields} draft={draft} busy={busy} onChange={(key, value) => changeProtocolSetting(protocol, key, value)} onSave={() => void saveProtocolSettings(protocol)} />
    <section className="vlessHealthRow">
      <article><button onClick={() => toggleNetworkDiagnostics(protocol)}><span><small>XRAY HEALTH</small><strong>Диагностика runtime</strong></span><b>{protocolDiagnosticsLabel}</b></button>{diagnosticsOpen[protocol] && <div className="vlessHealthBody">{(activeProtocol.diagnostics?.checks || []).map((check) => <p className={check.ok ? "ok" : "failed"} key={check.id}><i /> <span><strong>{check.name}</strong><small>{check.value}</small></span></p>)}</div>}</article>
      <article><button onClick={() => toggleProtocolResources(protocol)}><span><small>EXTERNAL PATH</small><strong>Внешние ресурсы</strong></span><b>{protocolResourceAvailable}/{protocolResourceTotal}</b></button>{resourcesOpen[protocol] && <div className="vlessHealthBody"><button className="vlessCheckButton" onClick={() => void checkProtocolResources(protocol)} disabled={checkingResources === protocol}>Проверить</button>{(activeProtocol.resources?.items || []).map((item) => <p className={item.available ? "ok" : "failed"} key={item.name}><i /> <span><strong>{item.name}</strong><small>{item.available ? `${item.latency_ms} мс` : "Недоступен"}</small></span></p>)}</div>}</article>
    </section>
  </section>;
}

function ProtocolSettingsEditor({
  fields, draft, busy, vless = false, vlessScope = "all", onChange, onSave,
}: {
  fields: EditableProtocolSetting[];
  draft: Record<string, string | number | boolean>;
  busy: boolean;
  vless?: boolean;
  vlessScope?: "all" | "server" | "connections";
  onChange: (key: string, value: string | number | boolean) => void;
  onSave: () => void;
}) {
  if (!fields.length) return <p className="protocolSettingsEmpty">Для этого протокола нет изменяемых параметров.</p>;
  const selectedTransport = String(draft.transport || fields.find((field) => field.key === "transport")?.value || "xhttp");
  const cdnEnabled = Boolean(draft.cdn_enabled ?? fields.find((field) => field.key === "cdn_enabled")?.value);
  const tlsEnabled = Boolean(draft.tls_enabled ?? fields.find((field) => field.key === "tls_enabled")?.value);
  const selectedTlsTransport = String(draft.tls_transport || fields.find((field) => field.key === "tls_transport")?.value || "xhttp");
  const selectedCdnTransport = String(draft.cdn_transport || fields.find((field) => field.key === "cdn_transport")?.value || "websocket");
  let visibleFields = vless && selectedTransport !== "xhttp"
    ? fields.filter((field) => !["xhttp_mode", "xpadding", "xmux_concurrency"].includes(field.key))
    : fields;
  if (vless) visibleFields = visibleFields.filter((field) => field.key !== "cdn_xhttp_mode" || (cdnEnabled && selectedCdnTransport === "xhttp"));
  if (vless) visibleFields = visibleFields.filter((field) => !["cdn_domain", "cdn_transport"].includes(field.key) || cdnEnabled);
  if (vless) visibleFields = visibleFields.filter((field) => !["tls_domain", "tls_transport"].includes(field.key) || tlsEnabled);
  if (vless) visibleFields = visibleFields.filter((field) => field.key !== "tls_xhttp_mode" || (tlsEnabled && selectedTlsTransport === "xhttp"));
  const directKeys = new Set(["transport", "transport_path", "sni", "xhttp_mode", "xpadding", "xmux_concurrency"]);
  const cdnKeys = new Set(["cdn_enabled", "cdn_domain", "cdn_transport", "cdn_xhttp_mode"]);
  const tlsKeys = new Set(["tls_enabled", "tls_domain", "tls_transport", "tls_xhttp_mode"]);
  const connectionKeys = new Set(["transport", "transport_path", "xhttp_mode", "xpadding", "xmux_concurrency", "tls_transport", "tls_xhttp_mode", "cdn_transport", "cdn_xhttp_mode"]);
  if (vless && vlessScope === "server") visibleFields = visibleFields.filter((field) => !connectionKeys.has(field.key));
  if (vless && vlessScope === "connections") visibleFields = visibleFields.filter((field) => connectionKeys.has(field.key));
  const fieldLayer = (key: string) => key.startsWith("cdn_") ? "CDN" : key === "sni" ? "REALITY" : ["xhttp_mode", "xpadding", "xmux_concurrency"].includes(key) ? "XHTTP" : directKeys.has(key) ? "DIRECT" : "XRAY";
  const fieldLabel = (field: EditableProtocolSetting) => field.key === "transport_path"
    ? selectedTransport === "grpc" ? "Service name прямого gRPC" : selectedTransport === "raw" ? "Путь (не используется в RAW)" : "Путь прямого XHTTP"
    : field.label;
  const renderFields = (items: EditableProtocolSetting[]) => <div className="protocolSettingsFields">
    {items.map((field) => <label key={field.key} className={field.type === "boolean" ? "protocolBooleanField" : undefined}>
      <span>{fieldLabel(field)}{vless && vlessScope === "connections" && <em>{fieldLayer(field.key)}</em>}</span>
      {field.type === "boolean" ? <input type="checkbox" checked={Boolean(draft[field.key] ?? field.value)} onChange={(event) => onChange(field.key, event.target.checked)} />
        : field.type === "select" ? <select value={String(draft[field.key] ?? field.value)} onChange={(event) => onChange(field.key, event.target.value)}>
          {(field.options || []).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
          : field.type === "number" ? <input type="number" min={field.min} max={field.max} value={Number(draft[field.key] ?? field.value)} onChange={(event) => onChange(field.key, Number(event.target.value))} />
            : <input type="text" value={String(draft[field.key] ?? field.value)} onChange={(event) => onChange(field.key, event.target.value)} />}
      {field.help && (!vless || ["transport", "cdn_enabled"].includes(field.key)) && <div className="protocolFieldHelp"><i aria-hidden="true">i</i><span>{field.help}</span></div>}
    </label>)}
  </div>;
  const directFields = visibleFields.filter((field) => directKeys.has(field.key));
  const cdnFields = visibleFields.filter((field) => cdnKeys.has(field.key));
  const tlsFields = visibleFields.filter((field) => tlsKeys.has(field.key));
  const commonFields = visibleFields.filter((field) => !directKeys.has(field.key) && !cdnKeys.has(field.key) && !tlsKeys.has(field.key));
  return <div className={`protocolSettingsEditor${vless ? " vlessSettingsEditor" : ""}`}>
    {vless && vlessScope === "server" ? <>
      <div className="vlessServerSettingsGrid">
        <section className="vlessServerSettingCard reality">
          <header><span>REALITY</span><div><strong>Маскировка соединения</strong><p>Публичный HTTPS-сайт, под который маскируется прямой вход.</p></div></header>
          {renderFields(directFields)}
        </section>
        <section className={`vlessServerSettingCard tls ${tlsEnabled ? "enabled" : "disabled"}`}>
          <header><span>TLS</span><div><strong>Прямой TLS-домен</strong><p>DNS-запись без проксирования, сертификат выпускается автоматически.</p></div></header>
          {renderFields(tlsFields)}
        </section>
        <section className={`vlessServerSettingCard cdn ${cdnEnabled ? "enabled" : "disabled"}`}>
          <header><span>CDN</span><div><strong>Домен через CDN</strong><p>Проксируемая точка входа Cloudflare или другого провайдера.</p></div></header>
          {renderFields(cdnFields)}
        </section>
      </div>
      <section className="vlessRuntimeSettings">
        <header><div><strong>Параметры Xray</strong><p>Серверный DNS и детализация журнала.</p></div></header>
        {renderFields(commonFields)}
      </section>
    </> : vless && vlessScope === "connections" ? <>
      <div className="vlessRouteSummary" aria-label="Транспортные контуры VLESS">
        <span className="direct"><em>01 · VLESS</em><b>REALITY / {selectedTransport.toUpperCase()}</b><small>Прямое подключение к серверу.</small><i>REALITY</i></span>
        {tlsEnabled && <span className="direct"><em>02 · VLESS TLS</em><b>TLS / {selectedTlsTransport.toUpperCase()}</b><small>Прямой TLS-домен без Cloudflare Proxy.</small><i>ACME</i></span>}
        {cdnEnabled && <span className="cdn enabled"><em>03 · VLESS CDN</em><b>TLS / {selectedCdnTransport.toUpperCase()}</b><small>Подключение через проксируемый CDN-домен.</small><i>CDN</i></span>}
      </div>
      <section className="vlessSettingsGroup direct"><header><div><em>01 · REALITY</em><strong>VLESS</strong></div><p>Транспорт прямого подключения с маскировкой REALITY.</p></header>{renderFields(visibleFields.filter((field) => ["transport", "transport_path", "xhttp_mode", "xpadding", "xmux_concurrency"].includes(field.key)))}</section>
      {tlsEnabled && <section className="vlessSettingsGroup direct"><header><div><em>02 · ПРЯМОЙ TLS</em><strong>VLESS TLS</strong></div><p>Транспорт независимого TLS-контура.</p></header>{renderFields(visibleFields.filter((field) => ["tls_transport", "tls_xhttp_mode"].includes(field.key)))}</section>}
      {cdnEnabled && <section className="vlessSettingsGroup cdn"><header><div><em>03 · CDN</em><strong>VLESS CDN</strong></div><p>Транспорт подключения через Cloudflare или другой CDN.</p></header>{renderFields(visibleFields.filter((field) => ["cdn_transport", "cdn_xhttp_mode"].includes(field.key)))}</section>}
    </> : vless ? <>
      <section className="vlessSettingsGroup direct">
        <header><div><em>01 · REALITY</em><strong>Подключение по REALITY</strong></div><p>Транспорт, путь и маскировочный SNI.</p></header>
        {renderFields(directFields)}
      </section>
      <section className="vlessSettingsGroup direct"><header><div><em>02 · TLS</em><strong>Подключение через TLS-домен</strong></div><p>Независимое подключение с автоматическим сертификатом.</p></header>{renderFields(tlsFields)}</section>
      <section className="vlessSettingsGroup cdn">
        <header><div><em>03 · CDN</em><strong>Подключение через CDN-домен</strong></div><p>Проксируемый домен и совместимый транспорт.</p></header>
        {renderFields(cdnFields)}
      </section>
      <section className="vlessSettingsGroup common">
        <header><div><em>XRAY</em><strong>Общие параметры</strong></div><p>DNS и журнал.</p></header>
        {renderFields(commonFields)}
      </section>
    </> : renderFields(visibleFields)}
    <div className="protocolSettingsActions">
      <span>Проверка и откат выполняются автоматически.</span>
      <button type="button" className="protocolSettingsSave" onClick={onSave} disabled={busy}>{busy ? "Применяем…" : "Применить настройки"}</button>
    </div>
  </div>;
}
