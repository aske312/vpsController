"use client";

import Image from "next/image";
import type { Dispatch, SetStateAction } from "react";
import { bytes, duration, labels, safeDateTime } from "../../lib/control-plane-ui";
import type { EditableProtocolSetting, Protocol, ProtocolImage, ProtocolStatus, Tab } from "../../types/control-plane";

type ProtocolViewProps = {
  protocolTab: Protocol; activeProtocol: ProtocolStatus; activeProtocolRate: { rx: number; tx: number }; activeProtocolImage?: ProtocolImage; protocolCode: string; protocolIsTunnel: boolean; protocolOperational: boolean; protocolAvailability: number; protocolDiagnosticsLabel: string; protocolResourceAvailable: number; protocolResourceTotal: number; installedProtocols: Protocol[];
  setTab: Dispatch<SetStateAction<Tab>>; protocolSettingsDraft: Partial<Record<Protocol, Record<string, string | number | boolean>>>; diagnosticsOpen: Partial<Record<Protocol, boolean>>; resourcesOpen: Partial<Record<Protocol, boolean>>; checkingDiagnostics: Protocol | null; checkingResources: Protocol | null; installingProtocol: string; busy: boolean;
  restartProtocol: (protocol: Protocol) => Promise<void> | void; updateProtocol: (image: ProtocolImage) => Promise<void> | void; removeProtocol: (image: ProtocolImage) => Promise<void> | void; changeProtocolSetting: (protocol: Protocol, key: string, value: string | number | boolean) => void; saveProtocolSettings: (protocol: Protocol) => Promise<void> | void;
  toggleNetworkDiagnostics: (protocol: Protocol) => void; checkNetworkDiagnostics: (protocol: Protocol) => Promise<void> | void; toggleProtocolResources: (protocol: Protocol) => void; checkProtocolResources: (protocol: Protocol) => Promise<void> | void;
};

export function ProtocolView(props: ProtocolViewProps) {
  const { protocolTab, activeProtocol, activeProtocolRate, activeProtocolImage, protocolCode, protocolIsTunnel, protocolOperational, protocolAvailability, protocolDiagnosticsLabel, protocolResourceAvailable, protocolResourceTotal, installedProtocols, setTab, protocolSettingsDraft, diagnosticsOpen, resourcesOpen, checkingDiagnostics, checkingResources, installingProtocol, busy, restartProtocol, updateProtocol, removeProtocol, changeProtocolSetting, saveProtocolSettings, toggleNetworkDiagnostics, checkNetworkDiagnostics, toggleProtocolResources, checkProtocolResources } = props;
  return <section className={`protocolWorkspace protocolWorkspace-${protocolCode.toLowerCase()}`}>
        <header className="protocolHeader">
          <div className="protocolHeaderCopy">
            <p className="eyebrow">312.NET / PROTOCOL CONTROL</p>
            <div className="protocolHeaderTitle">
              <div>
                <h1>{labels[protocolTab]}</h1>
                <p>Runtime, конфигурация, диагностика и подключения протокола.</p>
              </div>
              <div className="protocolHeaderState">
                <span className={protocolOperational ? "online" : "offline"}>{protocolOperational ? "ACTIVE" : "STOPPED"}</span>
                <span className={activeProtocol.service_enabled ? "online" : "muted"}>{activeProtocol.service_enabled ? "AUTOSTART" : "MANUAL"}</span>
                <span className="version">{activeProtocolImage?.installed_version ? `v${activeProtocolImage.installed_version}` : "version n/a"}</span>
                {activeProtocolImage?.update_available && (
                  <span className={`version update${activeProtocolImage.update_breaking ? " breaking" : ""}`}>
                    обновление → {activeProtocolImage.available_version}{activeProtocolImage.update_breaking ? " · major" : ""}
                  </span>
                )}
              </div>
            </div>
          </div>
          {installedProtocols.length > 1 && <nav className="protocolSwitcher" aria-label="Установленные протоколы">
            {installedProtocols.map((protocol) => <button key={protocol} className={protocol === protocolTab ? "active" : ""} onClick={() => setTab(protocol)}>{protocol === "wg" ? "WG" : protocol === "awg" ? "AWG" : protocol === "shadowsocks" ? "SS" : "VRX"}</button>)}
          </nav>}
        </header>

        <div className="protocolStage">
          <div className="protocolMain">
            <div className="protocolSummaryGrid">
              <article className="protocolRuntime">
                <header>
                  <div><p className="eyebrow">PROTOCOL RUNTIME</p><h2>Состояние протокола</h2></div>
                  <span className={protocolOperational ? "state online" : "state offline"}>{protocolOperational ? "Работает" : "Остановлен"}</span>
                </header>
                <dl className="protocolRuntimeFacts">
                  <div><dt>ИНТЕРФЕЙС</dt><dd>{activeProtocol.interface || "—"}</dd></div>
                  <div><dt>ТИП</dt><dd>{labels[protocolTab]}</dd></div>
                  <div><dt>АДРЕС</dt><dd>{activeProtocol.address || "—"}</dd></div>
                  <div><dt>ПОРТ</dt><dd>{activeProtocol.listen_port || "—"}</dd></div>
                  <div><dt>TRANSPORT</dt><dd>{activeProtocol.transport || (protocolIsTunnel ? "UDP" : "—")}</dd></div>
                  {!protocolIsTunnel && <div><dt>SECURITY</dt><dd>{activeProtocol.security || "—"}</dd></div>}
                  {!protocolIsTunnel && <div><dt>TARGET</dt><dd>{activeProtocol.target || "Прямой выход"}</dd></div>}
                  <div><dt>КЛИЕНТЫ</dt><dd>{activeProtocol.online_peers}/{activeProtocol.peers} online</dd></div>
                  <div><dt>ЗАПУЩЕН С</dt><dd>{safeDateTime(activeProtocol.active_since)}</dd></div>
                  <div><dt>SYSTEMD</dt><dd>{activeProtocol.unit || (activeProtocol.service_active ? "active" : "inactive")}</dd></div>
                </dl>
                <div className="protocolRuntimeActions">
                  <button onClick={() => void restartProtocol(protocolTab)} disabled={busy}>Перезапустить</button>
                  {activeProtocolImage?.update_available && (
                    <button className={activeProtocolImage.update_breaking ? "warning" : "accent"} onClick={() => void updateProtocol(activeProtocolImage)} disabled={busy}>
                      {installingProtocol === `update-${activeProtocolImage.id}` ? "Обновление…" : `Обновить до ${activeProtocolImage.available_version}`}
                    </button>
                  )}
                  {activeProtocolImage?.removable && <button className="danger" onClick={() => void removeProtocol(activeProtocolImage)} disabled={busy}>Удалить протокол</button>}
                </div>
              </article>

              <article className="protocolHealth">
                <header><div><p className="eyebrow">TRAFFIC & HEALTH</p><h2>Трафик и здоровье</h2></div><span>{activeProtocol.history.samples} замеров / {activeProtocol.history.period_hours || 24}ч</span></header>
                <div className="protocolHealthMetrics">
                  <div className="rx"><small>RX NOW</small><strong>{bytes(activeProtocolRate.rx)}<em>/с</em></strong><span>{bytes(activeProtocol.history.received_bytes)} за период</span></div>
                  <div className="tx"><small>TX NOW</small><strong>{bytes(activeProtocolRate.tx)}<em>/с</em></strong><span>{bytes(activeProtocol.history.transmitted_bytes)} за период</span></div>
                  <div><small>ДОСТУПНОСТЬ</small><strong>{protocolAvailability != null ? `${protocolAvailability}%` : "—"}</strong><span>{activeProtocol.history.service_interruptions} остановок</span><i className="availability"><b style={{ width: `${Math.max(0, Math.min(100, Number(protocolAvailability) || 0))}%` }} /></i></div>
                  <div><small>LATENCY</small><strong>{activeProtocol.history.latency_avg_ms != null ? `${activeProtocol.history.latency_avg_ms.toFixed(1)} мс` : "—"}</strong><span>max {activeProtocol.history.latency_max_ms != null ? `${activeProtocol.history.latency_max_ms.toFixed(1)} мс` : "—"}</span></div>
                  <div><small>LOSS / CLIENTS</small><strong>{activeProtocol.history.external_loss_percent != null ? `${activeProtocol.history.external_loss_percent}%` : "—"}</strong><span>{activeProtocol.online_peers}/{activeProtocol.peers} online · {duration(activeProtocol.last_handshake_age_s)}</span></div>
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
                    <span><small>{protocolIsTunnel ? "NETWORK" : "RUNTIME"}</small><strong>{protocolIsTunnel ? "Причины нестабильности" : "Состояние runtime"}</strong><em>{protocolDiagnosticsLabel}{activeProtocol.diagnostics?.score != null ? ` · ${activeProtocol.diagnostics.score}/100` : ""}</em></span><b>{diagnosticsOpen[protocolTab] ? "Скрыть" : "Открыть"}</b>
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
                  <div className="protocolEventRows">{activeProtocol.history.events.length ? activeProtocol.history.events.slice(0, 4).map((event, index) => <div key={`${event.at}-${index}`}><i className={event.type === "service_down" ? "critical" : "warning"} /><p><strong>{event.type === "service_down" ? "Служба протокола остановилась" : event.type === "monitor_gap" ? "Пропуск мониторинга" : "Нет активных соединений"}</strong><small>{safeDateTime(event.at)}{event.seconds ? ` · ${event.seconds} сек` : ""}</small></p></div>) : <p className="protocolDiagnosticEmpty">За выбранный период разрывов и остановок не зафиксировано.</p>}</div>
                </section>
              </div>
            </article>
          </div>

          <aside className="protocolArtRail" aria-hidden="true">
            <span className="protocolArtCode">{protocolCode}</span>
            <div className="protocolArtGlow" />
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
  return <article className="panel protocolConfiguration">
    <header>
      <div><p className="eyebrow">CHANNEL CONFIGURATION</p><h3>Настройки {labels[protocol]}</h3>
        <span>{tunnel ? "MTU применяется сразу; DNS и keepalive — к новым профилям." : "Перед применением конфигурация проверяется, службы перезапускаются автоматически."}</span></div>
      <div className="configurationSafety"><i aria-hidden="true" /><p><strong>Безопасное применение</strong><small>валидация и автоматический откат</small></p></div>
    </header>
    <ProtocolSettingsEditor fields={fields} draft={draft} busy={busy} onChange={onChange} onSave={onSave} />
  </article>;
}
function ProtocolSettingsEditor({
  fields, draft, busy, onChange, onSave,
}: {
  fields: EditableProtocolSetting[];
  draft: Record<string, string | number | boolean>;
  busy: boolean;
  onChange: (key: string, value: string | number | boolean) => void;
  onSave: () => void;
}) {
  if (!fields.length) return <p className="protocolSettingsEmpty">Для этого протокола нет изменяемых параметров.</p>;
  return <div className="protocolSettingsEditor">
    <div className="protocolSettingsFields">
      {fields.map((field) => <label key={field.key}>
        <span>{field.label}</span>
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
