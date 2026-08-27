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
    lead: "Составной канал на Xray: VLESS отвечает за протокол, REALITY — за защиту, выбранный transport — за доставку.",
    signature: "VLESS / REALITY / XRAY", features: ["XHTTP · RAW · gRPC", "REALITY", "Reusable UUID"],
    runtimeLabel: "Состав и runtime", healthLabel: "Потоки Xray",
  },
};

export function ProtocolView(props: ProtocolViewProps) {
  const { protocolTab, activeProtocol, activeProtocolRate, activeProtocolImage, protocolCode, protocolIsTunnel, protocolOperational, protocolAvailability, protocolDiagnosticsLabel, protocolResourceAvailable, protocolResourceTotal, installedProtocols, setTab, onSelectProtocol, protocolSettingsDraft, diagnosticsOpen, resourcesOpen, checkingDiagnostics, checkingResources, installingProtocol, busy, restartProtocol, updateProtocol, removeProtocol, changeProtocolSetting, saveProtocolSettings, toggleNetworkDiagnostics, checkNetworkDiagnostics, toggleProtocolResources, checkProtocolResources } = props;
  const isVless = protocolTab === "vless-reality-xhttp";
  const profile = channelProfiles[protocolTab];
  return <section className={`protocolWorkspace protocolWorkspace-${protocolCode.toLowerCase()}`}>
        {installedProtocols.length > 1 && <nav className="protocolSwitcher" aria-label="Установленные защищённые протоколы">
          {installedProtocols.map((protocol) => <button type="button" key={protocol} className={protocol === protocolTab ? "active" : ""} aria-current={protocol === protocolTab ? "page" : undefined} onClick={() => onSelectProtocol ? onSelectProtocol(protocol) : setTab(protocol)}>{protocol === "wg" ? "WG" : protocol === "awg" ? "AWG" : protocol === "shadowsocks" ? "SS" : "VLESS"}</button>)}
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
        <span>{tunnel ? "MTU применяется сразу; DNS и keepalive — к новым профилям." : isVless ? "REALITY отвечает за защиту и маскировку, XHTTP — за транспорт. Ядро Xray обновляется отдельно от конфигурации." : "Перед применением конфигурация проверяется, службы перезапускаются автоматически."}</span></div>
      <div className="configurationSafety"><i aria-hidden="true" /><p><strong>Безопасное применение</strong><small>валидация и автоматический откат</small></p></div>
    </header>
    <ProtocolSettingsEditor fields={fields} draft={draft} busy={busy} vless={isVless} onChange={onChange} onSave={onSave} />
  </article>;
}
function ProtocolSettingsEditor({
  fields, draft, busy, vless = false, onChange, onSave,
}: {
  fields: EditableProtocolSetting[];
  draft: Record<string, string | number | boolean>;
  busy: boolean;
  vless?: boolean;
  onChange: (key: string, value: string | number | boolean) => void;
  onSave: () => void;
}) {
  if (!fields.length) return <p className="protocolSettingsEmpty">Для этого протокола нет изменяемых параметров.</p>;
  const selectedTransport = String(draft.transport || fields.find((field) => field.key === "transport")?.value || "xhttp");
  const visibleFields = vless && selectedTransport !== "xhttp"
    ? fields.filter((field) => !["xhttp_mode", "xpadding", "xmux_concurrency"].includes(field.key))
    : fields;
  const fieldLayer = (key: string) => key === "sni" ? "REALITY" : ["xhttp_mode", "xpadding", "xmux_concurrency"].includes(key) ? "XHTTP" : "XRAY";
  return <div className={`protocolSettingsEditor${vless ? " vlessSettingsEditor" : ""}`}>
    {vless && <div className="vlessStack" aria-label="Состав VLESS"><span><b>VLESS</b><small>протокол</small></span><i>+</i><span><b>REALITY</b><small>защита</small></span><i>+</i><span><b>{selectedTransport.toUpperCase()}</b><small>транспорт</small></span><i>·</i><span><b>XRAY</b><small>ядро</small></span></div>}
    <div className="protocolSettingsFields">
      {visibleFields.map((field) => <label key={field.key}>
        <span>{field.label}{vless && <em>{fieldLayer(field.key)}</em>}</span>
        {field.type === "boolean" ? <input type="checkbox" checked={Boolean(draft[field.key] ?? field.value)} onChange={(event) => onChange(field.key, event.target.checked)} />
          : field.type === "select" ? <select value={String(draft[field.key] ?? field.value)} onChange={(event) => onChange(field.key, event.target.value)}>
            {(field.options || []).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
            : field.type === "number" ? <input type="number" min={field.min} max={field.max} value={Number(draft[field.key] ?? field.value)} onChange={(event) => onChange(field.key, Number(event.target.value))} />
              : <input type="text" value={String(draft[field.key] ?? field.value)} onChange={(event) => onChange(field.key, event.target.value)} />}
        {field.help && <small>{field.help}</small>}
      </label>)}
    </div>
    <div className="protocolSettingsActions">
      <span>Изменения проверяются перед применением.</span>
      <button type="button" className="protocolSettingsSave" onClick={onSave} disabled={busy}>{busy ? "Применяем…" : "Применить настройки"}</button>
    </div>
  </div>;
}
