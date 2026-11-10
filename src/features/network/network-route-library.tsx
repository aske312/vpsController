"use client";

import { useEffect, useState, type ReactNode } from "react";
import { ConfirmationDialog } from "../../shared/components/confirmation-dialog";
import { useDialogFocus } from "../../shared/components/use-dialog-focus";
import { useNotifier } from "../../shared/notifications/notification-center";
import type { NetworkStatus, NetworkEndpointCheck } from "../../shared/types/control-plane";
import type { NetworkRequest } from "./network-api";
import type { NetworkListState } from "./network-view";
import type { useNetworkAddresses } from "./network-addresses";
import { addressMatches, addressRoleLabels } from "./server-address-state";
import type { RelayControl } from "./network-relays";
import { NetworkRelayNotice } from "./network-relays";
import { NetworkRouteSummaryBadge } from "./network-route-badge";
import { serverAddressSummary } from "./route-summary";
import { domainRouteKinds, filterEntries, needsAttention, routeEntries, type RouteEntry, type RouteFilter } from "./route-library-state";
import { DomainEntryPanel, RelayEntryPanel } from "./route-library-panels";
import type { NetworkCloudflareControl } from "./network-cloudflare";
import type { RouteIconKind } from "./route-type-icon";
import { RoutePagination, routePage } from "./route-pagination";
import { RouteResourceIcon } from "./route-resource-icon";
import { useResourceCountries } from "./use-resource-countries";
import { entryCountryAddresses, entryCountry, resourceCountry } from "./resource-country";
import { ServerAddressPanel } from "./route-server-panel";
import { NetworkCdnSettings } from "./network-cdn-settings";
import { RouteStatusCard } from "./route-status-card";
import { relayAddresses } from "./relay-state";
import { useResourceUsage } from "./use-resource-usage";
import { ipProvider, resourceConsumers, resourceProvider, resourceUsageLabel } from "./resource-usage";
import { ResourceDetails, ServerResourceDetails } from "./resource-details";

type Props = { status: NetworkStatus | null; relay: RelayControl; addresses: ReturnType<typeof useNetworkAddresses>; request: NetworkRequest; listState: NetworkListState; onListStateChange: (state: NetworkListState) => void; busy: boolean; failed: boolean; endpointDirty: boolean; onRemove: (kind: NetworkEndpointCheck["kind"], address: string) => void; add: ReactNode; onRefresh?: () => void; cloudflare?: NetworkCloudflareControl; refreshKey: number };

export function NetworkRouteLibrary({ status, relay, addresses, request, listState, onListStateChange, busy, failed, endpointDirty, onRemove, add, onRefresh, cloudflare, refreshKey }: Props) {
  const [entryPage, setEntryPage] = useState(1);
  const [ipPage, setIpPage] = useState(1);
  const [filter, setFilter] = useState<RouteFilter | "server">("all");
  const [deletion, setDeletion] = useState<{ kind: NetworkEndpointCheck["kind"]; address: string } | null>(null);
  const entries = routeEntries(status, relay.status?.hosts || [], failed, relay.failed, Boolean(relay.status?.pending), relay.reading);
  const usage = useResourceUsage(request, refreshKey, status?.detected_at, busy);
  const consumersFor = (entry: RouteEntry) => resourceConsumers(entry, failed ? null : usage.bindings, relay.failed || relay.status?.pending ? null : relay.status);
  const searchable = entries.map(entry => ({ ...entry, search: `${entry.search} ${resourceProvider(entry)} ${entry.kind === "domain" ? entry.domain.dns?.provider || "" : ""} ${(consumersFor(entry) || []).map(item => item.label).join(" ")}`.toLowerCase() }));
  const filtered = filter === "server" ? [] : filterEntries(searchable, listState.query, filter);
  const locations = useResourceCountries(request, entries.flatMap(entryCountryAddresses));
  const selected = entries.find(entry => listState.expanded.includes(entry.id));
  const serverRow = addresses.status?.items.find(row => listState.expanded.includes(`SERVER-${row.address}`));
  const serverItems = addresses.status?.items || [];
  const serverProblem = addresses.failed || addresses.status?.discovery.state === "error" || Boolean(addresses.pending);
  const serverIpInfo = (address: string) => status?.server.ip_info?.find(item => item.address === address);
  const serverAttention = serverItems.filter(row => needsAttention(serverAddressSummary(row, Boolean(serverProblem), Boolean(addresses.pending))));
  const visibleServer = (filter === "all" || filter === "server" ? serverItems : filter === "attention" ? serverAttention : []).filter(row => addressMatches(row, listState.query) || Boolean(listState.query.trim() && [serverIpInfo(row.address)?.hoster, serverIpInfo(row.address)?.provider].some(value => value?.toLowerCase().includes(listState.query.trim().toLowerCase()))));
  const entryPagination = routePage(filtered, entryPage);
  const ipPagination = routePage(visibleServer, ipPage);
  const close = () => onListStateChange({ ...listState, expanded: [] });
  const open = (id: string) => onListStateChange({ ...listState, expanded: [id] });
  return <section className="routeLibrary" aria-label="Точки входа">
    <header className="routeLibraryHeader"><div><p className="eyebrow">ДОМЕНЫ · IP · RELAY</p><h2>Ресурсы сети <small>{entries.length + serverItems.length}</small></h2><p>Назначение, провайдер и связанные подключения.</p></div><div className="routeLibraryHeaderActions">{add}</div></header>
    <div className="routeResourceToolbar"><label className="routeResourceSearch"><span className="networkSrOnly">Поиск ресурсов</span><input type="search" value={listState.query} placeholder="Адрес, провайдер или профиль" onChange={event => { setEntryPage(1); setIpPage(1); onListStateChange({ ...listState, query: event.target.value }); }} /></label>
      <div className="routeResourceFilters" role="group" aria-label="Фильтр ресурсов">{([['all', 'Все'], ['domain', 'Адреса входа'], ['relay', 'Relay'], ['server', 'IP сервера'], ['attention', 'Требуют внимания']] as const).map(([value, label]) => <button type="button" key={value} aria-pressed={filter === value} onClick={() => { setFilter(value); setEntryPage(1); setIpPage(1); }}>{label}<span>{value === "all" ? entries.length + serverItems.length : value === "server" ? serverItems.length : value === "attention" ? entries.filter(entry => needsAttention(entry.summary)).length + serverAttention.length : entries.filter(entry => entry.kind === value).length}</span></button>)}</div>
    </div>

    <NetworkRelayNotice relay={relay} />
    <div className="routeLibraryList">{entryPagination.items.map(entry => <RouteStatusCard key={entry.id} title={entry.title} address={entry.address} types={entryTypes(entry)} icon={entryIcon(entry)} country={entryCountry(entry, locations)} cdn={entry.kind === "domain" && domainRouteKinds(entry).includes("cdn")} summary={entry.summary} onOpen={() => open(entry.id)} details={entry.kind === "relay" ? [
      { label: "Использование", value: resourceUsageLabel(entry, consumersFor(entry)) },
      { label: "Пересылки", value: entry.summary.connection },
      { label: "Адреса", value: String(relayAddresses(entry.host).length) },
    ] : [
      { label: "Использование", value: resourceUsageLabel(entry, consumersFor(entry)) },
      { label: "Провайдер", value: resourceProvider(entry) },
      { label: "DNS / маршрут", value: entry.summary.connection },
    ]} />)}</div>
    <RoutePagination {...entryPagination} label="Точки входа" onChange={setEntryPage} />
    {!filtered.length && !visibleServer.length && <div className="routeLibraryEmpty"><strong>{entries.length || serverItems.length ? "Ресурсы не найдены" : relay.reading && !relay.status ? "Загружаем адреса…" : "Добавьте первую точку входа"}</strong><p>{entries.length || serverItems.length ? "Измените запрос или выбранный фильтр." : "Подключите Relay или добавьте домен."}</p></div>}
    {usage.failed && <p className="routeUsageNotice" role="status">Связанные подключения не удалось загрузить. Состояние адресов показано отдельно.</p>}
    {(visibleServer.length > 0 || !addresses.status && (filter === "all" || filter === "server")) && <section className="routeLibraryServer" aria-label="Адреса этого сервера"><header><h3>Адреса этого сервера <small>{visibleServer.length}</small></h3></header>
      {serverProblem && <div className="routeLibraryCallout" role="status"><p>{addresses.pending ? "Проверяем результат назначения IP. Команда не повторяется." : "Актуальность адресов не подтверждена."}</p><button type="button" disabled={addresses.reading} onClick={() => void addresses.load()}>Проверить состояние</button></div>}
      {!addresses.status && <p>Определяем адреса…</p>}
      <div className="routeServerGrid">{ipPagination.items.map(row => <RouteStatusCard key={row.address} title={row.address} types={[row.family === "ipv4" ? "IPv4" : "IPv6", addressRoleLabels[row.role]]} icon="ip" summary={serverAddressSummary(row, Boolean(serverProblem), Boolean(addresses.pending))} onOpen={() => open(`SERVER-${row.address}`)} details={[
        { label: "Провайдер IP", value: ipProvider(serverIpInfo(row.address)) || "Не определён" },
        { label: "Интерфейс", value: row.interfaces.join(", ") || "Не подтверждён" },
        { label: "Назначение", value: row.role === "primary" ? "Для новых подключений" : row.role === "reserve" ? "Резервный адрес" : row.presence === "present" && !serverProblem ? "Можно назначить основным" : "Назначение недоступно" },
      ]} />)}
      </div>
      <RoutePagination {...ipPagination} label="Адреса этого сервера" onChange={setIpPage} />
    </section>}
    {selected && <RouteControlDialog title={selected.title} address={selected.address} subtitle={selected.kind === "relay" ? "Relay Agent" : selected.subtitle} type={selected.kind} icon={entryIcon(selected)} country={entryCountry(selected, locations)} cdn={selected.kind === "domain" && domainRouteKinds(selected).includes("cdn")} badge={<NetworkRouteSummaryBadge summary={selected.summary} />} close={close}>
      {needsAttention(selected.summary) && <p className="routeLibraryCallout" role="status">{selected.summary.detail}</p>}
      <ResourceDetails entry={selected} consumers={consumersFor(selected)} />
      {selected.kind === "relay" ? <RelayEntryPanel key={selected.id} host={selected.host} relay={relay} close={close} /> : <DomainEntryPanel key={selected.id} entry={selected} request={request} disabled={busy || failed || endpointDirty} onChecked={onRefresh} onRemove={(kind, address) => setDeletion({ kind, address })}>
        <NetworkCdnSettings domain={selected.address} route={selected.domain.route} detected={selected.domain.edge?.provider.toLowerCase().includes("cloudflare") === true} request={request} control={cloudflare && { ...cloudflare, blocked: cloudflare.blocked || busy || failed }} refreshKey={refreshKey} />
      </DomainEntryPanel>}
    </RouteControlDialog>}
    {serverRow && <RouteControlDialog title={serverRow.address} address={serverRow.address} subtitle={`IP сервера · ${serverRow.family === "ipv4" ? "IPv4" : "IPv6"}`} type="server" icon="ip" badge={<NetworkRouteSummaryBadge summary={serverAddressSummary(serverRow, Boolean(serverProblem), Boolean(addresses.pending))} />} close={close}>
      <ServerResourceDetails row={serverRow} info={serverIpInfo(serverRow.address)} />
      <ServerAddressPanel key={serverRow.address} row={serverRow} control={addresses} />
    </RouteControlDialog>}
    {deletion && <ConfirmationDialog request={{ title: "Удалить адрес?", message: "Адрес перестанет предлагаться для новых подключений. Существующие подключения сохранят свои настройки.", confirmLabel: "Удалить", danger: true, phrase: deletion.address }} onClose={confirmed => { const item = deletion; setDeletion(null); if (confirmed) { close(); onRemove(item.kind, item.address); } }} />}
  </section>;
}

function entryIcon(entry: RouteEntry): RouteIconKind {
  if (entry.kind === "relay") return "relay";
  const kinds = domainRouteKinds(entry);
  return kinds.length === 1 ? ({ cdn: "cdn", tls_relay: "tls", udp_relay: "udp" } as const)[kinds[0]] : "domain";
}

function entryTypes(entry: RouteEntry): string[] {
  if (entry.kind === "relay") return ["Relay"];
  const kinds = domainRouteKinds(entry);
  return [...(/\bpanel\b/i.test(entry.domain.role) ? ["Панель"] : []), ...(kinds.length ? kinds.map(kind => ({ cdn: "CDN", tls_relay: "TLS", udp_relay: "UDP" })[kind]) : ["Домен"])];
}

function RouteControlDialog({ title, address, subtitle, type, icon, country, cdn, badge, close, children }: { title: string; address: string; subtitle: string; icon: RouteIconKind; country?: ReturnType<typeof resourceCountry>; cdn?: boolean; type: "relay" | "domain" | "server"; badge: ReactNode; close: () => void; children: ReactNode }) {
  const dialogRef = useDialogFocus(true, close);
  useEffect(() => {
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = overflow; };
  }, []);
  return <div className="networkModalBackdrop" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section ref={dialogRef} tabIndex={-1} className={`networkModal routeControlModal routeControl-${type}`} role="dialog" aria-modal="true" aria-labelledby="route-control-title">
      <header className="networkModalHeader"><div className="routeModalHeading"><span className="routeModalSymbol" aria-hidden="true"><RouteResourceIcon kind={icon} country={country} cdn={cdn} /></span><div className="routeDetailsIdentity"><div><small>{subtitle}</small><h2 id="route-control-title">{title}</h2></div>{badge}</div></div><button type="button" className="networkModalClose" onClick={close} aria-label="Закрыть настройки адреса">×</button></header>
      <div className="routeModalAddress">{title !== address && <code>{address}</code>}<CopyAddress key={address} address={address} /></div>
      <div className="routeDetailsBody">{children}</div>
    </section>
  </div>;
}

function CopyAddress({ address }: { address: string }) {
  const [copied, setCopied] = useState(false);
  const { error } = useNotifier("copy-route", "Адрес");
  return <button className="routeLibraryCopy" type="button" onClick={async () => { try { await navigator.clipboard.writeText(address); setCopied(true); } catch { error("Копирование недоступно. Выделите адрес и скопируйте его вручную."); } }}>{copied ? "Адрес скопирован" : "Скопировать адрес"}</button>;
}
