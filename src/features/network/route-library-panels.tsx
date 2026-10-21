"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import type { NetworkEndpointCheck } from "../../shared/types/control-plane";
import { useNotifier } from "../../shared/notifications/notification-center";
import { checkNetworkEndpoint, type NetworkRequest } from "./network-api";
import type { RelayControl } from "./network-relays";
import type { RelayHost, RelayRoute } from "./relay-state";
import { relayAddresses } from "./relay-state";
import { RelayDeviceResult, RelayLossResult } from "./relay-test-results";
import type { RouteEntry } from "./route-library-state";

export function RouteDisclosure({ title, children }: { title: string; children: ReactNode }) {
  return <details className="routeLibraryDisclosure"><summary>{title}<span aria-hidden="true">+</span></summary><div>{children}</div></details>;
}

export function RelayEntryPanel({ host, relay, close }: { host: RelayHost; relay: RelayControl; close: () => void }) {
  const [selected, setSelected] = useState("");
  const [consumer, setConsumer] = useState("");
  const route = host.routes.find(item => item.id === selected) || host.routes.find(item => ["preparing", "awaiting_client", "processing"].includes(item.device_test?.state || "")) || host.routes.find(item => ["failed", "missing", "drift"].includes(item.state) || item.check?.state === "failed") || host.routes[0];
  const consumers = (relay.status?.consumers || []).filter(item => !item.host_id || item.host_id === host.id);
  const locked = relay.locked || host.state !== "connected" || Boolean(relay.status?.pending);
  const active = host.routes.some(item => ["preparing", "awaiting_client", "processing"].includes(item.device_test?.state || ""));
  const version = host.agent_release;
  const updating = host.agent_update && ["sending", "queued", "running", "unknown"].includes(host.agent_update.state);
  const submit = (path: string) => void relay.submit(path, { method: "POST", headers: { "If-Match": relay.status?.revision || "" } });
  return <>
    <div className="routeLibraryQuickActions"><button type="button" onClick={() => { close(); relay.openHost(host); }} disabled={relay.locked}>Настроить Relay</button><span>Agent {host.version || "—"}</span></div>
    {version?.state === "outdated" && <div className="routeLibraryCallout"><p>Доступно обновление Agent {version.version}</p>{host.capabilities?.includes("manual-update-v1") ? <><button type="button" disabled={locked || Boolean(updating) || active} onClick={() => void relay.submit(`/network/relays/${host.id}/update`, { method: "POST", body: JSON.stringify({ version: version.version, digest: version.digest, expected_revision: relay.status?.revision }) })}>Обновить Agent</button><small>Agent перезапустится. Ключи и настройки сохранятся.</small></> : <small>Для команды из PRO сначала обновите Agent до 1.4 штатным установщиком или суточным updater.</small>}</div>}
    {host.agent_update && <p role="status">Обновление Agent: {({ sending: "команда отправляется", queued: "в очереди", running: "выполняется", updated: "завершено", failed: "не удалось выполнить", unknown: "результат не подтверждён, команда не повторяется" })[host.agent_update.state]}.</p>}
    <section className="routeLibrarySection"><h3>Проверить подключение</h3>
      {route ? <>
        <label>Маршрут<select value={route.id} onChange={event => { setSelected(event.target.value); setConsumer(""); }}>{host.routes.map(item => <option key={item.id} value={item.id}>{consumers.find(candidate => candidate.id === item.check?.consumer_id)?.label || `${item.transport.toUpperCase()} · ${item.listen_port}`}{["failed", "missing", "drift"].includes(item.state) || item.check?.state === "failed" ? " · ошибка" : ""}</option>)}</select></label>
        <label>Подключение<select value={consumer} onChange={event => setConsumer(event.target.value)} disabled={locked || active}><option value="">Выберите подключение</option>{consumers.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
        <div className="routeLibraryQuickActions"><button type="button" disabled={locked || active || route.state !== "listening" || !consumer} onClick={() => submit(`/network/relays/${host.id}/routes/${route.id}/check?mode=media&consumer_id=${encodeURIComponent(consumer)}`)}>Проверить с VPS</button>{host.capabilities?.includes("udp-measurement-v1") && <button type="button" disabled={locked || active || route.state !== "listening" || !consumer} onClick={() => submit(`/network/relays/${host.id}/routes/${route.id}/device-tests?consumer_id=${encodeURIComponent(consumer)}`)}>Тест с устройства</button>}</div>
        <RelayCheckResult route={route} stale={host.state !== "connected"} />
        <RelayDeviceResult test={route.device_test} disabled={locked} onDownload={() => void relay.downloadTest(host.id, route.id, route.device_test!.id)} onUpload={file => void relay.uploadTest(host.id, route.id, route.device_test!.id, file)} onCancel={() => void relay.submit(`/network/relays/${host.id}/routes/${route.id}/device-tests/${route.device_test!.id}`, { method: "DELETE", headers: { "If-Match": relay.status?.revision || "" } })} />
        <small>Тест с VPS проверяет передачу видео. Для проверки своей сети запустите комплект на устройстве.</small>
      </> : <p>Подключения через этот Relay ещё не созданы.</p>}
    </section>
    <RouteDisclosure title={`Адреса узла · ${relayAddresses(host).length}`}><ul className="routeLibraryAddresses">{relayAddresses(host).map(address => <li key={address}><code>{address}</code><button type="button" disabled={relay.locked || relay.status?.pending} onClick={() => relay.setAddressConfirmation({ host, address })}>Исключить</button></li>)}</ul><small>Исключение убирает адрес из выбора для новых подключений.</small></RouteDisclosure>
    {host.address_checks?.filter(item => ["unresolved", "mismatch"].includes(item.state)).map(item => <p className="routeLibraryCallout" key={item.domain}>{item.domain}: {item.state === "mismatch" ? "DNS указывает на другой сервер" : "DNS не определён"}.</p>)}
    <RouteDisclosure title="Обновления и обслуживание"><p>Автообновление: {host.updates ? host.updates.enabled ? "раз в сутки" : "выключено" : "нет данных"}.</p>{host.updates?.state === "failed" && <p role="alert">Последняя попытка обновления не удалась.</p>}<button type="button" disabled={relay.locked || relay.status?.pending} className="routeLibraryDanger" onClick={() => relay.setConfirmation(host)}>Удалить Relay</button></RouteDisclosure>
  </>;
}

function RelayCheckResult({ route, stale }: { route: RelayRoute; stale: boolean }) {
  const check = route.check;
  return <>
    <p className="routeLibraryResult">{stale || !check || check.state === "unknown" ? "Результат с VPS не подтверждён." : check.state === "ready" ? check.mode === "media" ? "Передача видео проверена с VPS." : "Тестовый обмен с VPS подтверждён." : "Проверка с VPS не пройдена."}</p>
    {check?.checked_at && <small>{new Date(check.checked_at).toLocaleString("ru-RU")}</small>}
    {[route.check?.cleanup_state, route.device_test?.cleanup_state].includes("failed") && <p role="alert">Не удалось подтвердить очистку теста. Проверьте ресурсы на сервере.</p>}
    {[route.check?.cleanup_state, route.device_test?.cleanup_state].includes("pending") && <p role="status">Ожидается подтверждение закрытия диагностического порта Relay.</p>}
    {check?.measurement && <RouteDisclosure title="Результат измерения UDP"><RelayLossResult result={check.measurement} source="VPS" stale={stale || check.state === "unknown"} /></RouteDisclosure>}
  </>;
}

export function DomainEntryPanel({ entry, request, disabled, onRemove, onChecked, children }: { entry: Extract<RouteEntry, { kind: "domain" }>; request: NetworkRequest; disabled: boolean; onRemove: (kind: NetworkEndpointCheck["kind"], domain: string) => void; onChecked?: () => void; children?: ReactNode }) {
  const [checking, setChecking] = useState(false);
  const [selected, setSelected] = useState("");
  const [result, setResult] = useState<NetworkEndpointCheck | null>(null);
  const { error } = useNotifier("route-check", "Проверка адреса");
  const inferred = ([['cdn', /cdn/i], ['tls_relay', /tls/i], ['udp_relay', /udp/i]] as const).filter(([, pattern]) => pattern.test(entry.domain.role)).map(([kind]) => kind);
  const kinds = [...new Set([...entry.checks.map(check => check.kind), ...(entry.domain.endpoint_kind ? [entry.domain.endpoint_kind] : []), ...inferred])];
  const kind = kinds.includes(selected as NetworkEndpointCheck["kind"]) ? selected as NetworkEndpointCheck["kind"] : kinds[0];
  const saved = entry.checks.find(check => check.kind === kind);
  const check = result?.kind === kind ? result : saved;
  async function runCheck() {
    if (!kind || disabled || checking) return;
    setChecking(true);
    try { setResult(await checkNetworkEndpoint(request, kind, entry.address)); onChecked?.(); }
    catch (cause) { error(cause instanceof Error ? cause.message : "Проверка не выполнена"); }
    finally { setChecking(false); }
  }
  return <>
    <p>{entry.summary.connection}</p>
    {kind && <section className="routeLibrarySection"><h3>Проверить адрес</h3>{kinds.length > 1 && <label>Тип подключения<select value={kind} onChange={event => setSelected(event.target.value)}>{kinds.map(item => <option key={item} value={item}>{({ cdn: "CDN", tls_relay: "TLS", udp_relay: "UDP" })[item]}</option>)}</select></label>}<button type="button" disabled={disabled || checking || entry.domain.status === "stale"} onClick={() => void runCheck()}>{checking ? "Проверяем…" : "Проверить с VPS"}</button>{check && <p role="status">{disabled ? "Сохранённый результат; актуальность не подтверждена." : check.message}</p>}{check?.checked_at && <small>{new Date(check.checked_at).toLocaleString("ru-RU")}</small>}</section>}
    {!!entry.domain.stale_usages?.length && <p>Адрес сохранён в подключениях: {entry.domain.stale_usages.join(", ")}.</p>}
    <RouteDisclosure title="DNS-адреса"><ul className="routeLibraryAddresses">{entry.domain.resolved.map(address => <li key={address}><code>{address}</code></li>)}</ul>{!entry.domain.resolved.length && <p>DNS не определён.</p>}</RouteDisclosure>
    {children}
    {kind && <footer className="routeLibraryFooter"><button type="button" className="routeLibraryDanger" disabled={disabled || checking} onClick={() => onRemove(kind, entry.address)}>Удалить адрес</button></footer>}
  </>;
}
