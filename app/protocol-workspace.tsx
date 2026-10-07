"use client";

import { useState } from "react";
import { ProtocolIcon } from "./protocol-icon";
import type { Protocol, ProtocolImage, ProtocolStatus } from "./page";

type Rate = { rx: number; tx: number };

type Props = {
  protocol: Protocol;
  status: ProtocolStatus;
  image?: ProtocolImage;
  rate: Rate;
  installed: Protocol[];
  busy: boolean;
  checkingConnection: boolean;
  checkingDiagnostics: boolean;
  checkingVersion: boolean;
  updating: boolean;
  onNavigate(protocol: Protocol): void;
  onRestart(): void;
  onCheckVersion(): void;
  onUpdate(): void;
  onRemove(): void;
  onCheckConnection(): void;
  onCheckDiagnostics(): void;
};

const protocolNames: Record<Protocol, string> = {
  wg: "WireGuard", awg: "AmneziaWG", hysteria2: "Hysteria2", tuic: "TUIC v5", xray: "Xray",
};

const formatBytes = (value = 0) => {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
};

const formatAge = (seconds?: number) => {
  if (seconds === undefined || seconds === null) return "нет handshake";
  if (seconds < 60) return `${seconds} сек назад`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`;
  return `${Math.floor(seconds / 3600)} ч назад`;
};

const fact = (status: ProtocolStatus, label: string, fallback = "—") =>
  status.profile?.facts.find((item) => item.label === label)?.value || fallback;

function EvidenceCard({ status, checking, onCheck }: { status: ProtocolStatus; checking: boolean; onCheck(): void }) {
  const evidence = status.connection_test;
  const state = evidence?.state || "unverified";
  return <article className={`protocolNewCard protocolProof protocolState-${state}`}>
    <header><span>DATA PLANE PROOF</span><b>{state === "confirmed" ? "ПОДТВЕРЖДЕНО" : state === "failed" ? "ОШИБКА" : "НЕ ПРОВЕРЕНО"}</b></header>
    <div className="protocolProofLead"><i>{state === "confirmed" ? "✓" : state === "failed" ? "!" : "?"}</i><div><h3>{evidence?.title || "Передача данных не проверялась"}</h3><p>{evidence?.detail || "Запустите проверку протокола."}</p></div></div>
    <dl className="protocolProofMetrics">
      <div><dt>Метод</dt><dd>{evidence?.method === "observed-client-traffic" ? "реальный клиент" : "локальный protocol client"}</dd></div>
      <div><dt>Request / response</dt><dd>{evidence?.latency_ms != null ? `${evidence.latency_ms} мс` : "—"}</dd></div>
      <div><dt>Передано</dt><dd>{state === "confirmed" ? `↑ ${formatBytes(evidence?.bytes_sent)} · ↓ ${formatBytes(evidence?.bytes_received)}` : "—"}</dd></div>
      <div><dt>Последняя проверка</dt><dd>{evidence?.checked_at ? new Date(evidence.checked_at).toLocaleString("ru-RU") : "никогда"}</dd></div>
    </dl>
    <p className="protocolProofScope">{evidence?.scope}</p>
    <button onClick={onCheck} disabled={checking || !status.service_active}>{checking ? "Выполняется handshake и запрос…" : evidence?.method === "observed-client-traffic" ? "Сверить активность клиентов" : "Запустить protocol roundtrip"}</button>
  </article>;
}

function TruthChain({ status }: { status: ProtocolStatus }) {
  const listenerPassed = status.profile?.kind === "encrypted-tunnel" ? status.active : Boolean(status.profile?.listener?.listening);
  const identityPassed = status.profile?.kind === "encrypted-tunnel" ? status.peers > 0 : (status.profile?.accounts || 0) > 0;
  const dataState = status.connection_test?.state || "unverified";
  const steps = [
    { label: "Служба", detail: status.service_active ? "active" : "inactive", state: status.service_active ? "passed" : "failed" },
    { label: status.profile?.kind === "encrypted-tunnel" ? "Интерфейс" : "Listener", detail: listenerPassed ? `${status.listen_port}` : "не найден", state: listenerPassed ? "passed" : "failed" },
    { label: status.profile?.kind === "encrypted-tunnel" ? "Peer identity" : "Доступы", detail: identityPassed ? `${status.profile?.accounts ?? status.peers}` : "нет", state: identityPassed ? "passed" : "unknown" },
    { label: "Данные", detail: dataState === "confirmed" ? "request/response" : dataState === "failed" ? "ошибка" : "не проверено", state: dataState === "confirmed" ? "passed" : dataState },
  ];
  return <div className="protocolTruthChain" aria-label="Уровни подтверждения работы протокола">
    {steps.map((step, index) => <div className={`protocolTruthStep ${step.state}`} key={step.label}><span>{index + 1}</span><p><strong>{step.label}</strong><small>{step.detail}</small></p></div>)}
  </div>;
}

function Diagnostics({ status, checking, onCheck }: { status: ProtocolStatus; checking: boolean; onCheck(): void }) {
  const [open, setOpen] = useState(false);
  return <article className={`protocolNewCard protocolRuntime ${status.diagnostics?.status || "pending"}`}>
    <button className="protocolRuntimeToggle" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      <span><small>RUNTIME DIAGNOSTICS</small><strong>{status.profile?.kind === "proxy" ? "Служба, listener и протокольный путь" : "Интерфейс, маршрут и условия VPS"}</strong></span>
      <b>{open ? "Скрыть" : "Развернуть"}</b>
    </button>
    {open && <div className="protocolRuntimeBody">
      <div className="protocolRuntimeChecks">{(status.diagnostics?.checks || []).map((check) => <div className={check.state || (check.ok ? "passed" : "failed")} key={check.id}><i /><span><strong>{check.name}</strong><small>{check.value}</small></span></div>)}</div>
      <div className="protocolRuntimeFindings">
        {(status.diagnostics?.findings || []).map((item) => <div className={item.severity} key={item.code}><b>{item.severity === "critical" ? "!" : "i"}</b><p><strong>{item.title}</strong><span>{item.detail}</span><small>{item.action}</small></p></div>)}
        {!status.diagnostics?.findings?.length && <p className="protocolRuntimeClear">В проверяемом контуре проблем не найдено.</p>}
      </div>
      <button className="protocolRuntimeCheck" onClick={onCheck} disabled={checking}>{checking ? "Проверяем…" : "Повторить диагностику"}</button>
    </div>}
  </article>;
}

function TrafficCard({ status, rate, direct }: { status: ProtocolStatus; rate: Rate; direct?: boolean }) {
  return <article className="protocolNewCard protocolTraffic">
    <header><span>TRAFFIC</span><b>{direct ? "SYSTEMD ACCOUNTING" : "24 HOURS"}</b></header>
    <div className="protocolTrafficNow">
      <div><small>RX NOW</small><strong>↓ {formatBytes(rate.rx)}<em>/с</em></strong><span>{direct ? `всего ${formatBytes(status.interface_rx_bytes)}` : `24ч ${formatBytes(status.history.received_bytes)}`}</span></div>
      <div><small>TX NOW</small><strong>↑ {formatBytes(rate.tx)}<em>/с</em></strong><span>{direct ? `всего ${formatBytes(status.interface_tx_bytes)}` : `24ч ${formatBytes(status.history.transmitted_bytes)}`}</span></div>
    </div>
  </article>;
}

function AwgPage({ status, rate, checkingConnection, onCheckConnection }: Pick<Props, "status" | "rate" | "checkingConnection" | "onCheckConnection">) {
  return <div className="protocolEdition protocolEditionAwg">
    <section className="protocolPeerStrip">
      <article><small>PEERS</small><strong>{status.peers}</strong><span>зарегистрировано</span></article>
      <article><small>ONLINE</small><strong>{status.online_peers}</strong><span>handshake &lt; 180 сек</span></article>
      <article><small>LAST HANDSHAKE</small><strong>{formatAge(status.last_handshake_age_s)}</strong><span>криптографическая связь</span></article>
      <article><small>INTERFACE</small><strong>{status.interface}</strong><span>{status.address} · MTU {status.mtu}</span></article>
    </section>
    <div className="protocolEditionGrid"><TrafficCard status={status} rate={rate} /><EvidenceCard status={status} checking={checkingConnection} onCheck={onCheckConnection} /></div>
    <article className="protocolNewCard awgParameters"><header><span>AMNEZIA PARAMETERS</span><b>LIVE CONFIG</b></header><div><p><small>Транспорт</small><strong>{fact(status, "Транспорт")}</strong></p><p><small>Обфускация</small><strong>{fact(status, "Обфускация")}</strong></p><p><small>Порт</small><strong>{status.listen_port}</strong></p><p><small>Peer traffic</small><strong>↓ {formatBytes(status.peer_rx_bytes)} · ↑ {formatBytes(status.peer_tx_bytes)}</strong></p></div></article>
  </div>;
}

function HysteriaPage({ status, rate, checkingConnection, onCheckConnection }: Pick<Props, "status" | "rate" | "checkingConnection" | "onCheckConnection">) {
  return <div className="protocolEdition protocolEditionHysteria">
    <article className="protocolNewCard protocolRoute"><header><span>HYSTERIA2 REQUEST PATH</span><b>QUIC / UDP</b></header><div className="protocolRouteLine"><p><i>01</i><strong>UDP {status.listen_port}</strong><small>входящий listener</small></p><p><i>02</i><strong>QUIC + TLS</strong><small>{fact(status, "TLS identity")}</small></p><p><i>03</i><strong>HTTP auth</strong><small>{fact(status, "Учётные записи")} доступов</small></p><p><i>04</i><strong>SOCKS request</strong><small>ответ API через outbound</small></p></div></article>
    <div className="protocolEditionGrid"><article className="protocolNewCard hysteriaIdentity"><header><span>IDENTITY & ACCESS</span><b>PINNED TLS</b></header><dl><div><dt>TLS identity</dt><dd>{fact(status, "TLS identity")}</dd></div><div><dt>Аутентификация</dt><dd>{fact(status, "Аутентификация")}</dd></div><div><dt>Учётные записи</dt><dd>{fact(status, "Учётные записи")}</dd></div><div><dt>Live sessions</dt><dd>не экспортируются ядром</dd></div></dl></article><EvidenceCard status={status} checking={checkingConnection} onCheck={onCheckConnection} /></div>
    <TrafficCard status={status} rate={rate} direct />
  </div>;
}

function TuicPage({ status, rate, checkingConnection, onCheckConnection }: Pick<Props, "status" | "rate" | "checkingConnection" | "onCheckConnection">) {
  return <div className="protocolEdition protocolEditionTuic">
    <section className="tuicTuning">
      <article><small>CONGESTION</small><strong>{fact(status, "Congestion control")}</strong><span>QUIC controller</span></article>
      <article><small>HEARTBEAT</small><strong>{fact(status, "Heartbeat")}</strong><span>проверка сессии</span></article>
      <article><small>IDENTITY</small><strong>{fact(status, "Учётные записи")}</strong><span>UUID / password</span></article>
      <article><small>TLS</small><strong>{fact(status, "TLS identity")}</strong><span>server identity</span></article>
    </section>
    <article className="protocolNewCard protocolRoute"><header><span>TUIC V5 REQUEST PATH</span><b>QUIC / UDP {status.listen_port}</b></header><div className="protocolRouteLine compact"><p><i>01</i><strong>Mixed SOCKS</strong><small>временный клиент</small></p><p><i>02</i><strong>TUIC v5</strong><small>UUID + password</small></p><p><i>03</i><strong>TLS / QUIC</strong><small>{fact(status, "Congestion control")}</small></p><p><i>04</i><strong>API response</strong><small>двусторонний пакет</small></p></div></article>
    <div className="protocolEditionGrid"><TrafficCard status={status} rate={rate} direct /><EvidenceCard status={status} checking={checkingConnection} onCheck={onCheckConnection} /></div>
  </div>;
}

function XrayPage({ status, rate, checkingConnection, onCheckConnection }: Pick<Props, "status" | "rate" | "checkingConnection" | "onCheckConnection">) {
  return <div className="protocolEdition protocolEditionXray">
    <section className="xrayStack" aria-label="Стек Xray">
      <article><span>APPLICATION</span><strong>SOCKS request</strong><small>локальный probe client</small></article>
      <b>→</b><article><span>PROTOCOL</span><strong>VLESS</strong><small>UUID identity</small></article>
      <b>→</b><article><span>TRANSPORT</span><strong>XHTTP</strong><small>TCP {status.listen_port}</small></article>
      <b>→</b><article><span>SECURITY</span><strong>REALITY</strong><small>{fact(status, "Server name")}</small></article>
    </section>
    <div className="protocolEditionGrid"><article className="protocolNewCard xrayReality"><header><span>REALITY IDENTITY</span><b>SERVER SIDE</b></header><dl><div><dt>Server name</dt><dd>{fact(status, "Server name")}</dd></div><div><dt>Target</dt><dd>{fact(status, "Reality target")}</dd></div><div><dt>Транспорт</dt><dd>{fact(status, "Транспорт")}</dd></div><div><dt>VLESS users</dt><dd>{fact(status, "Учётные записи")}</dd></div></dl></article><EvidenceCard status={status} checking={checkingConnection} onCheck={onCheckConnection} /></div>
    <TrafficCard status={status} rate={rate} direct />
  </div>;
}

export function ProtocolWorkspace(props: Props) {
  const { protocol, status, image } = props;
  const state = !status.service_active ? "failed" : status.connection_test?.state || "unverified";
  return <section className={`protocolWorkspace protocol-${protocol}`}>
    {props.installed.length > 1 && <nav className="protocolWorkspaceRail" aria-label="Установленные протоколы">{props.installed.map((item) => <button type="button" key={item} className={protocol === item ? "active" : ""} onClick={() => props.onNavigate(item)}><ProtocolIcon protocol={item} /><span>{protocolNames[item]}</span></button>)}</nav>}
    <header className={`protocolWorkspaceHero protocolState-${state}`}>
      <div className="protocolWorkspaceIdentity"><ProtocolIcon protocol={protocol} /><div><small>{status.profile?.kind === "proxy" ? "PROXY PROTOCOL" : "ENCRYPTED NETWORK"}</small><h1>{protocolNames[protocol]}</h1><p>{status.profile?.summary}</p></div></div>
      <div className="protocolWorkspaceEndpoint"><small>ENDPOINT</small><strong>{status.address || "адрес не назначен"}</strong><span>{status.transport || fact(status, "Транспорт")} · port {status.listen_port || "—"} · {image?.installed_version || "версия неизвестна"}</span></div>
      <div className="protocolWorkspaceVerdict"><i /> <p><strong>{!status.service_active ? "Служба остановлена" : status.connection_test?.title || "Подключение не проверено"}</strong><small>{status.service_active ? "systemd active" : "systemd inactive"} · {status.service_enabled ? "autostart on" : "autostart off"}</small></p></div>
      <div className="protocolWorkspaceActions"><button onClick={props.onRestart} disabled={props.busy}>{status.service_active ? "Перезапустить" : "Запустить"}</button><button onClick={props.onCheckVersion} disabled={props.busy || props.checkingVersion}>{props.checkingVersion ? "Проверяем…" : "Проверить версию"}</button><button onClick={props.onUpdate} disabled={props.busy || !image?.update_available}>{props.updating ? "Обновляем…" : image?.update_available ? `Обновить до ${image.available_version}` : "Обновлений нет"}</button>{image?.removable && <button className="danger" onClick={props.onRemove} disabled={props.busy}>Удалить</button>}</div>
    </header>
    <TruthChain status={status} />
    {(protocol === "awg" || protocol === "wg") && <AwgPage {...props} />}
    {protocol === "hysteria2" && <HysteriaPage {...props} />}
    {protocol === "tuic" && <TuicPage {...props} />}
    {protocol === "xray" && <XrayPage {...props} />}
    <Diagnostics status={status} checking={props.checkingDiagnostics} onCheck={props.onCheckDiagnostics} />
  </section>;
}
