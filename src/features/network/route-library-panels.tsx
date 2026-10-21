"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import type { NetworkEndpointCheck } from "../../shared/types/control-plane";
import { useNotifier } from "../../shared/notifications/notification-center";
import { checkNetworkEndpoint, type NetworkRequest } from "./network-api";
import { endpointCheckForConsumer } from "../../shared/lib/network-readiness";
import type { RelayControl } from "./network-relays";
import type { RelayHost, RelayRoute } from "./relay-state";
import { relayAddresses } from "./relay-state";
import { RelayDeviceResult, RelayLossResult } from "./relay-test-results";
import type { RouteEntry } from "./route-library-state";
import { routeNeedsAttention } from "./route-library-state";

export function RouteDisclosure({ title, children }: { title: string; children: ReactNode }) {
  return <details className="routeLibraryDisclosure"><summary>{title}<span aria-hidden="true">+</span></summary><div>{children}</div></details>;
}

export function RelayEntryPanel({ host, relay, close }: { host: RelayHost; relay: RelayControl; close: () => void }) {
  const [selected, setSelected] = useState("");
  const [consumer, setConsumer] = useState("");
  const route = host.routes.find(item => item.id === selected) || host.routes.find(item => ["preparing", "awaiting_client", "processing"].includes(item.device_test?.state || "")) || host.routes.find(routeNeedsAttention) || host.routes[0];
  const consumers = (relay.status?.consumers || []).filter(item => !item.host_id || item.host_id === host.id);
  const locked = relay.locked || host.state !== "connected" || Boolean(relay.status?.pending);
  const active = host.routes.some(item => ["preparing", "awaiting_client", "processing"].includes(item.device_test?.state || ""));
  const version = host.agent_release;
  const updating = host.agent_update && ["sending", "queued", "running", "unknown"].includes(host.agent_update.state);
  const submit = (path: string) => void relay.submit(path, { method: "POST", headers: { "If-Match": relay.status?.revision || "" } });
  return <>
    <section className="routeAgentCard"><div><h3>Agent {host.version || "· версия неизвестна"}</h3><p>Автообновление {host.updates ? host.updates.enabled ? "раз в сутки" : "выключено" : "· нет данных"}</p></div><button type="button" onClick={() => { close(); relay.openHost(host); }} disabled={relay.locked}>Настроить Relay</button></section>
    {version?.state === "outdated" && <div className="routeLibraryCallout"><p>Доступно обновление Agent {version.version}</p>{host.capabilities?.includes("manual-update-v1") ? <><button type="button" disabled={locked || Boolean(updating) || active} onClick={() => void relay.submit(`/network/relays/${host.id}/update`, { method: "POST", body: JSON.stringify({ version: version.version, digest: version.digest, expected_revision: relay.status?.revision }) })}>Обновить Agent</button><small>Agent перезапустится. Ключи и настройки сохранятся.</small></> : <small>Для команды из PRO сначала обновите Agent до 1.4 штатным установщиком или суточным updater.</small>}</div>}
    {host.agent_update && <p role="status">Обновление Agent: {({ sending: "команда отправляется", queued: "в очереди", running: "выполняется", updated: "завершено", failed: "не удалось выполнить", unknown: "результат не подтверждён, команда не повторяется" })[host.agent_update.state]}.</p>}
    {host.updates?.state === "failed" && <p className="routeLibraryCallout" role="alert">Последняя попытка автообновления не удалась.</p>}
    <section className="routeTaskCard"><header><h3>Проверка передачи</h3><p>Выберите маршрут и подключение для теста.</p></header>
      {route ? <>
        <div className="routeFieldPair">
        <label>Маршрут<select value={route.id} onChange={event => { setSelected(event.target.value); setConsumer(""); }}>{host.routes.map(item => <option key={item.id} value={item.id}>{consumers.find(candidate => candidate.id === item.check?.consumer_id)?.label || `${item.transport.toUpperCase()} · ${item.listen_port}`}{routeNeedsAttention(item) ? " · ошибка" : ""}</option>)}</select></label>
        <label>Подключение<select value={consumer} onChange={event => setConsumer(event.target.value)} disabled={locked || active}><option value="">Выберите подключение</option>{consumers.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
        </div>
        <div className="routeTestOptions"><div><h4>Со стороны сервера</h4><p>Передача видео через выбранный протокол и Relay.</p><button type="button" className="routeActionPrimary" disabled={locked || active || route.state !== "listening" || !consumer} onClick={() => submit(`/network/relays/${host.id}/routes/${route.id}/check?mode=media&consumer_id=${encodeURIComponent(consumer)}`)}>Проверить с VPS</button></div>{host.capabilities?.includes("udp-measurement-v1") && <div><h4>Из вашей сети</h4><p>Комплект для запуска на устройстве и загрузки отчёта.</p><button type="button" disabled={locked || active || route.state !== "listening" || !consumer} onClick={() => submit(`/network/relays/${host.id}/routes/${route.id}/device-tests?consumer_id=${encodeURIComponent(consumer)}`)}>Тест с устройства</button></div>}</div>
        {active && <p role="status">Тест с устройства уже запущен. Завершите или отмените его перед новой проверкой.</p>}
        <div className="routeTestResults" aria-label="Результаты проверок">
        <RelayCheckResult route={route} stale={host.state !== "connected"} />
        <RelayDeviceResult test={route.device_test} disabled={locked} onDownload={() => void relay.downloadTest(host.id, route.id, route.device_test!.id)} onUpload={file => void relay.uploadTest(host.id, route.id, route.device_test!.id, file)} onCancel={() => void relay.submit(`/network/relays/${host.id}/routes/${route.id}/device-tests/${route.device_test!.id}`, { method: "DELETE", headers: { "If-Match": relay.status?.revision || "" } })} />
        </div>
      </> : <p>Подключения через этот Relay ещё не созданы.</p>}
    </section>
    <RouteDisclosure title={`Адреса узла · ${relayAddresses(host).length}`}><ul className="routeLibraryAddresses">{relayAddresses(host).map(address => <li key={address}><code>{address}</code><button type="button" disabled={relay.locked || relay.status?.pending} onClick={() => relay.setAddressConfirmation({ host, address })}>Исключить</button></li>)}</ul><small>Исключение убирает адрес из выбора для новых подключений.</small></RouteDisclosure>
    {host.address_checks?.filter(item => ["unresolved", "mismatch"].includes(item.state)).map(item => <p className="routeLibraryCallout" key={item.domain}>{item.domain}: {item.state === "mismatch" ? "DNS указывает на другой сервер" : "DNS не определён"}.</p>)}
    <footer className="routeControlFooter"><span>Удаление отключит пересылки этого узла.</span><button type="button" disabled={relay.locked || relay.status?.pending} className="routeLibraryDanger" onClick={() => relay.setConfirmation(host)}>Удалить Relay</button></footer>
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
  const [results, setResults] = useState<Partial<Record<NetworkEndpointCheck["kind"], NetworkEndpointCheck>>>({});
  const [consumers, setConsumers] = useState<Record<string, string>>({});
  const { error } = useNotifier("route-check", "Проверка адреса");
  const inferred = ([['cdn', /cdn/i], ['tls_relay', /tls/i], ['udp_relay', /udp/i]] as const).filter(([, pattern]) => pattern.test(entry.domain.role)).map(([kind]) => kind);
  const kinds = [...new Set([...entry.checks.map(check => check.kind), ...(entry.domain.endpoint_kind ? [entry.domain.endpoint_kind] : []), ...inferred])];
  const kind = kinds.includes(selected as NetworkEndpointCheck["kind"]) ? selected as NetworkEndpointCheck["kind"] : kinds[0];
  const saved = entry.checks.find(check => check.kind === kind);
  const raw = results[kind] || saved;
  const consumer = consumers[kind] || "";
  const check = endpointCheckForConsumer(raw, consumer);
  const presentation = kind === "cdn" ? { title: "Подключение через CDN", note: "Проверьте домен и передачу через CDN, затем настройте защиту соединения с сервером.", scope: "Проверка CDN не подтверждает доступность из сети вашего устройства." }
    : kind === "tls_relay" ? { title: "Прямое TLS-подключение", note: "Домен используется для защищённого подключения к серверу.", scope: "Сертификат и SNI задаются в настройках протокола. Этот адрес сам по себе не создаёт TLS-вход." }
    : { title: kind ? "Подключение по UDP" : "Домен сервера", note: kind ? "Проверьте передачу через существующее UDP-подключение." : "Сведения о домене и его DNS-адресах.", scope: "Порт и ключи задаются в настройках протокола." };
  async function runCheck() {
    if (!kind || disabled || checking) return;
    setChecking(true);
    try { const next = await checkNetworkEndpoint(request, kind, entry.address, consumer || undefined); setResults(current => ({ ...current, [kind]: next })); onChecked?.(); }
    catch (cause) { error(cause instanceof Error ? cause.message : "Проверка не выполнена"); }
    finally { setChecking(false); }
  }
  return <>
    {kinds.length > 1 && <div className="routeKindSwitch" role="group" aria-label="Тип подключения">{kinds.map(item => <button type="button" key={item} aria-pressed={kind === item} disabled={checking} onClick={() => setSelected(item)}>{({ cdn: "CDN", tls_relay: "TLS", udp_relay: "UDP" })[item]}</button>)}</div>}
    <section className="routeTaskCard" data-address-kind={kind || "domain"}><header><h3>{presentation.title}</h3><p>{presentation.note}</p></header>
      <div className="routeDnsOverview"><span>DNS</span><div><strong>{entry.summary.connection}</strong><div className="routeDnsChips">{entry.domain.resolved.map(address => <code key={address}>{address}</code>)}</div></div></div>
      {kind && <>
        {!!raw?.consumers?.length && <label className="routeControlField">Подключение для проверки<select value={consumer} disabled={disabled || checking} onChange={event => setConsumers(current => ({ ...current, [kind]: event.target.value }))}><option value="">Только проверка адреса</option>{raw.consumers.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}
        <div className="routeLibraryQuickActions"><button type="button" className="routeActionPrimary" disabled={disabled || checking || entry.domain.status === "stale"} onClick={() => void runCheck()}>{checking ? "Проверяем…" : consumer ? "Проверить подключение" : "Проверить адрес"}</button></div>
        <div className="routeTestResults" role="status"><p>{disabled ? "Сохранённые сведения; актуальность не подтверждена." : check?.message || "Проверка ещё не выполнена."}</p>{check?.checked_at && <small>{new Date(check.checked_at).toLocaleString("ru-RU")}</small>}</div>
        <small>{presentation.scope}</small>
      </>}
    </section>
    {!!entry.domain.stale_usages?.length && <p>Адрес сохранён в подключениях: {entry.domain.stale_usages.join(", ")}.</p>}
    {kind === "cdn" && children}
    {kind && <footer className="routeControlFooter"><span>{kinds.length > 1 ? `Удаление из ${({ cdn: "CDN", tls_relay: "TLS", udp_relay: "UDP" })[kind]}` : "Убрать адрес из новых подключений"}</span><button type="button" className="routeLibraryDanger" disabled={disabled || checking} onClick={() => onRemove(kind, entry.address)}>Удалить адрес</button></footer>}
  </>;
}
