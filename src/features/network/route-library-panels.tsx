"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import type { NetworkEndpointCheck } from "../../shared/types/control-plane";
import type { NetworkRequest } from "./network-api";
import type { RelayControl } from "./network-relays";
import type { RelayHost, RelayRoute } from "./relay-state";
import { RelayDeviceResult, RelayLossResult } from "./relay-test-results";
import type { RouteEntry } from "./route-library-state";
import { domainRouteKinds, routeNeedsAttention } from "./route-library-state";
import { useEndpointSweep } from "./use-endpoint-sweep";
import { RelayAddressList } from "./relay-address-list";
import { RouteTypeIcon } from "./route-type-icon";
import { resourceIp } from "./resource-country";
import { RelayProfilePicker } from "./relay-route-picker";

export function RouteDisclosure({ title, children }: { title: string; children: ReactNode }) {
  return <details className="routeLibraryDisclosure"><summary>{title}<span className="routeDisclosureToggle" aria-hidden="true">+</span></summary><div>{children}</div></details>;
}

export function RelayEntryPanel({ host, relay, close }: { host: RelayHost; relay: RelayControl; close: () => void }) {
  const [selected, setSelected] = useState("");
  const [selectedConsumer, setConsumer] = useState("");
  const [selectedProfile, setProfile] = useState("");
  const profiles = (relay.status?.profiles || []).filter(profile => profile.channels.some(channel => channel.host_id === host.id));
  const testingRoute = host.routes.find(item => ["preparing", "awaiting_client", "processing"].includes(item.device_test?.state || ""));
  const profile = selectedProfile === "__all__" ? undefined : profiles.find(item => item.id === selectedProfile) || profiles.find(item => item.channels.some(channel => channel.route_ids.includes(testingRoute?.id || ""))) || profiles[0];
  const profileChannels = profile?.channels.filter(channel => channel.host_id === host.id);
  const routeIds = profileChannels && new Set(profileChannels.flatMap(channel => channel.route_ids));
  const routes = routeIds ? host.routes.filter(item => routeIds.has(item.id)) : host.routes;
  const route = routes.find(item => item.id === selected) || routes.find(item => item.id === testingRoute?.id) || routes.find(routeNeedsAttention) || routes[0];
  const consumers = (relay.status?.consumers || []).filter(item => (!item.host_id || item.host_id === host.id) && (!profileChannels || profileChannels.some(channel => channel.id === item.id && channel.route_ids.includes(route?.id || ""))));
  const consumer = consumers.some(item => item.id === selectedConsumer) ? selectedConsumer : "";
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
    <section className="routeTaskCard routeRelayTests" aria-label="Проверка маршрута Relay">
      {profiles.length > 0 && <RelayProfilePicker host={host} profiles={profiles} selected={profile?.id || ""} onChange={id => { setProfile(id); setSelected(""); setConsumer(""); }} />}
      {profiles.length > 0 && <button type="button" className="relayAllConnections" aria-pressed={selectedProfile === "__all__"} onClick={() => { setProfile("__all__"); setSelected(""); setConsumer(""); }}>Все порты и отдельные подключения</button>}
      {profiles.filter(item => item.relay_failures?.some(error => error.host_id === host.id)).map(item => <div key={item.id} className="routeLibraryCallout" role="alert">
        <p>{item.name}: не удалось настроить пересылку через {host.name}. Профиль сохранён.</p>
        {item.relay_failures?.some(error => error.host_id === host.id && error.reason === "recovery_required") && <p>Сначала проверьте результат прерванной операции Relay.</p>}
        <button type="button" disabled={locked || active} onClick={() => submit(`/mihomo/profiles/${encodeURIComponent(item.id)}/relays`)}>Повторить настройку пересылки</button>
      </div>)}
      {profiles.length === 0 && <p>Профили через этот Relay пока не настроены.</p>}
      <details className="relayProfileDiagnostics" open={active || undefined}><summary>Диагностика{profile ? ` · ${profile.name}` : " и отдельные подключения"}</summary>
      {route ? <>
        <div className="routeFieldPair">
          <label>Порт для проверки<select value={route.id} onChange={event => { setSelected(event.target.value); setConsumer(""); }}>{routes.map(item => <option key={item.id} value={item.id}>{item.transport.toUpperCase()} :{item.listen_port} → VPS :{item.target_port}</option>)}</select></label>
          <label>Подключение<select value={consumer} onChange={event => setConsumer(event.target.value)} disabled={locked || active}><option value="">Выберите подключение</option>{consumers.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
        </div>
        <div className="routeRelayPath"><code>{route.transport.toUpperCase()} :{route.listen_port}</code><span aria-hidden="true">→</span><code>VPS :{route.target_port}</code><span className="routeRelayPathState" data-state={route.state}>{({ listening: "Порт слушает", failed: "Ошибка", missing: "Пересылка отсутствует", drift: "Настройки расходятся", unknown: "Нет данных" })[route.state]}</span></div>
        <div className="routeTestOptions"><div><h4>Видео с VPS</h4><button type="button" className="routeActionPrimary" disabled={locked || active || route.state !== "listening" || !consumer} onClick={() => submit(`/network/relays/${host.id}/routes/${route.id}/check?mode=media&consumer_id=${encodeURIComponent(consumer)}`)}>Проверить с VPS</button><div className="routeTestResults" aria-label="Результат с VPS"><RelayCheckResult route={route} stale={host.state !== "connected"} /></div></div>{host.capabilities?.includes("udp-measurement-v1") && <div><h4>Из вашей сети</h4><button type="button" disabled={locked || active || route.state !== "listening" || !consumer} onClick={() => submit(`/network/relays/${host.id}/routes/${route.id}/device-tests?consumer_id=${encodeURIComponent(consumer)}`)}>Тест с устройства</button><small>Скачать комплект → запустить → загрузить отчёт.</small></div>}</div>
        {active && <p role="status">Тест с устройства уже запущен. Завершите или отмените его перед новой проверкой.</p>}
        {route.device_test && <div className="routeTestResults" aria-label="Тест с устройства">
        <RelayDeviceResult test={route.device_test} disabled={locked} onDownload={() => void relay.downloadTest(host.id, route.id, route.device_test!.id)} onUpload={file => void relay.uploadTest(host.id, route.id, route.device_test!.id, file)} onCancel={() => void relay.submit(`/network/relays/${host.id}/routes/${route.id}/device-tests/${route.device_test!.id}`, { method: "DELETE", headers: { "If-Match": relay.status?.revision || "" } })} />
        </div>}
      </> : <><header><h3>Маршруты Relay <small>0</small></h3></header><p>Подключения через этот Relay ещё не созданы.</p></>}
      </details>
    </section>
    <RelayAddressList host={host} relay={relay} />
    <footer className="routeControlFooter"><button type="button" disabled={relay.locked || relay.status?.pending} className="routeLibraryDanger" onClick={() => relay.setConfirmation(host)}>Удалить Relay</button></footer>
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
  const [selected, setSelected] = useState("");
  const sweep = useEndpointSweep(request, onChecked);
  const checking = sweep.checking;
  const kinds = domainRouteKinds(entry);
  const kind = kinds.includes(selected as NetworkEndpointCheck["kind"]) ? selected as NetworkEndpointCheck["kind"] : kinds[0];
  const hasSweep = sweep.scope === `${kind}:${entry.address}`;
  const check = hasSweep ? sweep.observation : entry.checks.find(check => check.kind === kind);
  const udpIp = kind === "udp_relay" && Boolean(resourceIp(entry.address));
  return <>
    {kinds.length > 1 && <div className="routeKindSwitch" role="group" aria-label="Тип подключения">{kinds.map(item => <button type="button" key={item} aria-pressed={kind === item} disabled={checking} onClick={() => setSelected(item)}><RouteTypeIcon kind={({ cdn: "cdn", tls_relay: "tls", udp_relay: "udp" } as const)[item]} />{({ cdn: "CDN", tls_relay: "TLS", udp_relay: "UDP" })[item]}</button>)}</div>}
    <section className={`routeTaskCard routeTransportPanel routeTransport-${kind || "domain"}`} data-address-kind={kind || "domain"}>
      {kind === "tls_relay" && <header><h3>TLS-домен</h3></header>}
      {kind === "udp_relay" && <header><h3>{udpIp ? "UDP по IP" : "UDP по домену"}</h3></header>}
      <div className="routeDnsOverview"><span>{udpIp ? "IP" : "DNS"}</span><div><strong>{entry.summary.connection}</strong><div className="routeDnsChips">{entry.domain.resolved.map(address => <code key={address}>{address}</code>)}</div></div></div>
      {kind && <>
        <div className="routeLibraryQuickActions"><button type="button" className="routeActionPrimary" disabled={disabled || checking || entry.domain.status === "stale"} onClick={() => void sweep.run(kind, entry.address)}>{checking ? "Проверяем компоненты…" : kind === "tls_relay" ? "Проверить TLS" : kind === "udp_relay" ? "Проверить UDP" : "Проверить адрес"}</button>{checking && <button type="button" onClick={sweep.stop}>Остановить после текущего теста</button>}</div>
        <div className="routeTestResults" role="status"><p>{disabled ? "Сохранённые сведения; актуальность не подтверждена." : check?.message || "Проверка ещё не выполнена."}</p>{check?.checked_at && <small>{new Date(check.checked_at).toLocaleString("ru-RU")}</small>}</div>
        {hasSweep && <div className="routeComponentResults">{(["direct", "mihomo"] as const).map(owner => {
          const rows = sweep.rows.filter(row => row.owner === owner);
          return rows.length > 0 && <section key={owner}><h4>{owner === "direct" ? "Direct" : "Mihomo"}<span>{rows.filter(row => row.state === "ready").length} / {rows.length}</span></h4>{rows.map(row => <details key={row.id} data-state={disabled ? "unknown" : row.state}><summary><span><strong>{row.label}</strong>{row.consumer_label && <small>{row.consumer_label}</small>}</span><span>{disabled ? "Сохранено" : ({ pending: "В очереди", running: "Проверяем…", ready: "Обмен подтверждён", failed: "Ошибка", unavailable: "Не проверено", unknown: "Нет результата" })[row.state]}</span></summary><p>{disabled ? "Актуальность результата не подтверждена." : row.message || "Ожидаем запуск проверки"}</p></details>)}</section>;
        })}{!sweep.rows.length && !checking && <p>{sweep.failure || "Установленные транспортные компоненты не найдены."}</p>}</div>}
        {kind !== "cdn" && <RouteDisclosure title={kind === "tls_relay" ? "Настройка TLS" : "Настройка UDP"}><p>{kind === "tls_relay" ? "Домен должен указывать на VPS без CDN-прокси. Сертификат и SNI задаются в подключении; добавление домена не создаёт TLS-службу." : "Используйте IP VPS или домен без CDN-прокси. UDP-порт и ключи задаются в подключении; добавление адреса не запускает UDP-службу."}</p><p>Проверяются настроенные подключения Direct и Mihomo со стороны VPS. Доступность из вашей сети проверяется отдельно.</p></RouteDisclosure>}
      </>}
    </section>
    {!!entry.domain.stale_usages?.length && <p>Адрес сохранён в подключениях: {entry.domain.stale_usages.join(", ")}.</p>}
    {kind === "cdn" && children}
    {kind && <footer className="routeControlFooter">{kinds.length > 1 && <span>Из ${({ cdn: "CDN", tls_relay: "TLS", udp_relay: "UDP" })[kind]}</span>}<button type="button" className="routeLibraryDanger" disabled={disabled || checking} onClick={() => onRemove(kind, entry.address)}>Удалить адрес</button></footer>}
  </>;
}
