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
import { serverAddressSummary, type RouteSummary } from "./route-summary";
import { domainRouteKinds, filterEntries, needsAttention, routeEntries, type RouteEntry } from "./route-library-state";
import { DomainEntryPanel, RelayEntryPanel } from "./route-library-panels";
import { NetworkCloudflare, type NetworkCloudflareControl } from "./network-cloudflare";
import { RouteTypeIcon, type RouteIconKind } from "./route-type-icon";
import { ServerAddressPanel } from "./route-server-panel";
import { NetworkEch } from "./network-ech";

type Props = { status: NetworkStatus | null; relay: RelayControl; addresses: ReturnType<typeof useNetworkAddresses>; request: NetworkRequest; listState: NetworkListState; onListStateChange: (state: NetworkListState) => void; busy: boolean; failed: boolean; endpointDirty: boolean; onRemove: (kind: NetworkEndpointCheck["kind"], address: string) => void; add: ReactNode; onRefresh?: () => void; cloudflare?: NetworkCloudflareControl; refreshKey: number };

export function NetworkRouteLibrary({ status, relay, addresses, request, listState, onListStateChange, busy, failed, endpointDirty, onRemove, add, onRefresh, cloudflare, refreshKey }: Props) {
  const [deletion, setDeletion] = useState<{ kind: NetworkEndpointCheck["kind"]; address: string } | null>(null);
  const entries = routeEntries(status, relay.status?.hosts || [], failed, relay.failed, Boolean(relay.status?.pending), relay.reading);
  const visible = filterEntries(entries, listState.query, "all");
  const selected = entries.find(entry => listState.expanded.includes(entry.id));
  const serverRow = addresses.status?.items.find(row => listState.expanded.includes(`SERVER-${row.address}`));
  const serverItems = addresses.status?.items.filter(row => addressMatches(row, listState.query)) || [];
  const close = () => onListStateChange({ ...listState, expanded: [] });
  const open = (id: string) => onListStateChange({ ...listState, expanded: [id] });
  const serverProblem = addresses.failed || addresses.status?.discovery.state === "error" || Boolean(addresses.pending);
  return <section className="routeLibrary" aria-label="Точки входа">
    <header className="routeLibraryHeader"><div><p className="eyebrow">ROUTES</p><h2>Точки входа</h2><p>Адреса для подключения устройств</p></div><div className="routeLibraryHeaderActions"><label className="routeLibrarySearch"><span className="networkSrOnly">Найти адрес или Relay</span><input type="search" placeholder="Адрес или название" value={listState.query} onChange={event => onListStateChange({ ...listState, query: event.target.value })} /></label>{add}</div></header>

    <NetworkRelayNotice relay={relay} />
    <div className="networkProviderGrid routeLibraryList">{visible.map(entry => <RouteStatusCard key={entry.id} title={entry.title} subtitle={entry.kind === "relay" ? entry.address : entry.subtitle} icon={entryIcon(entry)} summary={entry.summary} onOpen={() => open(entry.id)} facts={entry.kind === "relay" ? <><span>Маршрутов: {entry.host.routes.length}</span><span>{entry.host.version ? `Agent ${entry.host.version}` : "Версия Agent неизвестна"}</span></> : <span>{entry.summary.connection}</span>} />)}</div>
    {!visible.length && (!listState.query || !serverItems.length) && <div className="routeLibraryEmpty"><strong>{relay.reading && !relay.status ? "Загружаем адреса…" : listState.query ? "Адресов по этому запросу нет" : "Добавьте первую точку входа"}</strong><p>{listState.query ? "Измените поисковый запрос." : "Подключите Relay или добавьте домен."}</p></div>}
    <section className="routeLibraryServer" aria-label="Адреса этого сервера"><header><h3>Адреса этого сервера <small>{addresses.status?.items.length ?? "…"}</small></h3><p>Для прямого подключения к VPS</p></header>
      {serverProblem && <div className="routeLibraryCallout" role="status"><p>{addresses.pending ? "Проверяем результат назначения IP. Команда не повторяется." : "Актуальность адресов не подтверждена."}</p><button type="button" disabled={addresses.reading} onClick={() => void addresses.load()}>Проверить состояние</button></div>}
      {!addresses.status && <p>Определяем адреса…</p>}
      <div className="networkProviderGrid routeServerGrid">{serverItems.map(row => <RouteStatusCard key={row.address} title={row.address} subtitle={`${row.family === "ipv4" ? "IPv4" : "IPv6"} · ${addressRoleLabels[row.role]}`} icon="ip" summary={serverAddressSummary(row, Boolean(serverProblem), Boolean(addresses.pending))} onOpen={() => open(`SERVER-${row.address}`)} facts={<span>{row.role === "primary" ? "Для новых подключений" : row.role === "reserve" ? "Резервный адрес" : row.presence === "present" && !serverProblem ? "Можно назначить основным" : "Назначение недоступно"}</span>} />)}
      </div>
    </section>
    {selected && <RouteControlDialog title={selected.title} address={selected.address} subtitle={selected.kind === "relay" ? "Relay Agent" : selected.subtitle} type={selected.kind} icon={entryIcon(selected)} badge={<NetworkRouteSummaryBadge summary={selected.summary} />} close={close}>
      {needsAttention(selected.summary) && <p className="routeLibraryCallout" role="status">{selected.summary.detail}</p>}
      {selected.kind === "relay" ? <RelayEntryPanel key={selected.id} host={selected.host} relay={relay} close={close} /> : <DomainEntryPanel key={selected.id} entry={selected} request={request} disabled={busy || failed || endpointDirty} onChecked={onRefresh} onRemove={(kind, address) => setDeletion({ kind, address })}>
        {(selected.checks.some(check => check.kind === "cdn") || selected.domain.endpoint_kind === "cdn" || /cdn/i.test(selected.domain.role)) && <section className="routeTaskCard routeCdnSettings"><header><h3>Защита CDN</h3><p>Параметры соединения CDN с сервером и публикации ECH.</p></header>{cloudflare && <NetworkCloudflare domain={selected.address} detected={selected.domain.edge?.provider.toLowerCase().includes("cloudflare") === true} request={request} control={{ ...cloudflare, blocked: cloudflare.blocked || busy || failed }} refreshKey={refreshKey} />}<NetworkEch domain={selected.address} route={selected.domain.route} request={request} /></section>}
      </DomainEntryPanel>}
    </RouteControlDialog>}
    {serverRow && <RouteControlDialog title={serverRow.address} address={serverRow.address} subtitle={`IP сервера · ${serverRow.family === "ipv4" ? "IPv4" : "IPv6"}`} type="server" icon="ip" badge={<NetworkRouteSummaryBadge summary={serverAddressSummary(serverRow, Boolean(serverProblem), Boolean(addresses.pending))} />} close={close}>
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

function RouteStatusCard({ title, subtitle, icon, summary, onOpen, facts }: { title: string; subtitle: string; icon: RouteIconKind; summary: RouteSummary; onOpen: () => void; facts: ReactNode }) {
  return <button type="button" className="networkProvider routeLibraryRow" data-state={summary.state} aria-haspopup="dialog" onClick={onOpen} aria-label={`Открыть ${title}`}>
    <span className="networkProviderCode routeCardIcon"><RouteTypeIcon kind={icon} /></span>
    <span className="networkProviderText"><strong title={title}>{title}</strong><small title={subtitle}>{subtitle}</small><NetworkRouteSummaryBadge summary={summary} /></span>
    <span className="networkProviderIndicator" aria-hidden="true" />
    <span className="routeCardFacts">{facts}</span>
  </button>;
}

function RouteControlDialog({ title, address, subtitle, type, icon, badge, close, children }: { title: string; address: string; subtitle: string; icon: RouteIconKind; type: "relay" | "domain" | "server"; badge: ReactNode; close: () => void; children: ReactNode }) {
  const dialogRef = useDialogFocus(true, close);
  useEffect(() => {
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = overflow; };
  }, []);
  return <div className="networkModalBackdrop" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section ref={dialogRef} tabIndex={-1} className={`networkModal routeControlModal routeControl-${type}`} role="dialog" aria-modal="true" aria-labelledby="route-control-title">
      <header className="networkModalHeader"><div className="routeModalHeading"><span className="routeModalSymbol" aria-hidden="true"><RouteTypeIcon kind={icon} /></span><div className="routeDetailsIdentity"><div><small>{subtitle}</small><h2 id="route-control-title">{title}</h2></div>{badge}</div></div><button type="button" className="networkModalClose" onClick={close} aria-label="Закрыть настройки адреса">×</button></header>
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
