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
import { filterEntries, needsAttention, routeEntries, type RouteFilter } from "./route-library-state";
import { DomainEntryPanel, RelayEntryPanel, RouteDisclosure } from "./route-library-panels";
import { NetworkCloudflare, type NetworkCloudflareControl } from "./network-cloudflare";
import { NetworkEch } from "./network-ech";

type Props = { status: NetworkStatus | null; relay: RelayControl; addresses: ReturnType<typeof useNetworkAddresses>; request: NetworkRequest; listState: NetworkListState; onListStateChange: (state: NetworkListState) => void; busy: boolean; failed: boolean; endpointDirty: boolean; onRemove: (kind: NetworkEndpointCheck["kind"], address: string) => void; add: ReactNode; onRefresh?: () => void; cloudflare?: NetworkCloudflareControl; refreshKey: number };

export function NetworkRouteLibrary({ status, relay, addresses, request, listState, onListStateChange, busy, failed, endpointDirty, onRemove, add, onRefresh, cloudflare, refreshKey }: Props) {
  const [serverExpanded, setServerExpanded] = useState(false);
  const [filter, setFilter] = useState<RouteFilter>("all");
  const [deletion, setDeletion] = useState<{ kind: NetworkEndpointCheck["kind"]; address: string } | null>(null);
  const entries = routeEntries(status, relay.status?.hosts || [], failed, relay.failed, Boolean(relay.status?.pending), relay.reading);
  const visible = filterEntries(entries, listState.query, filter);
  const selected = entries.find(entry => listState.expanded.includes(entry.id));
  const serverRow = addresses.status?.items.find(row => listState.expanded.includes(`SERVER-${row.address}`));
  const serverItems = addresses.status?.items.filter(row => addressMatches(row, listState.query)) || [];
  const attention = entries.filter(entry => needsAttention(entry.summary)).length;
  const close = () => onListStateChange({ ...listState, expanded: [] });
  const open = (id: string) => onListStateChange({ ...listState, expanded: [id] });
  const serverProblem = addresses.failed || addresses.status?.discovery.state === "error" || Boolean(addresses.pending);
  return <section className="routeLibrary" aria-label="Точки входа">
    <header className="routeLibraryHeader"><div><p className="eyebrow">ROUTES</p><h2>Точки входа</h2><p>Адреса для подключения устройств</p></div>{add}</header>
    <div className="routeLibraryToolbar"><nav aria-label="Фильтр адресов">{([["all", "Все"], ["relay", "Relay"], ["domain", "Домены"], ["attention", "Требуют внимания"]] as const).map(([value, label]) => <button type="button" key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}{value === "attention" && attention > 0 && <span>{attention}</span>}</button>)}</nav><label className="routeLibrarySearch"><span className="networkSrOnly">Найти адрес или Relay</span><input type="search" placeholder="Адрес или название" value={listState.query} onChange={event => onListStateChange({ ...listState, query: event.target.value })} /></label></div>
    <NetworkRelayNotice relay={relay} />
    <div className="networkProviderGrid routeLibraryList">{visible.map(entry => <button type="button" className="networkProvider routeLibraryRow" data-state={entry.summary.state} aria-haspopup="dialog" key={entry.id} onClick={() => open(entry.id)} aria-label={`Открыть ${entry.title}`}><span className="networkProviderCode" aria-hidden="true">{entry.kind === "relay" ? "RELAY" : "DNS"}</span><span className="networkProviderText"><strong>{entry.title}</strong><small>{entry.kind === "relay" ? entry.address : entry.subtitle}</small><NetworkRouteSummaryBadge summary={entry.summary} /></span><span className="networkProviderIndicator" aria-hidden="true" /></button>)}</div>
    {!visible.length && <div className="routeLibraryEmpty"><strong>{relay.reading && !relay.status ? "Загружаем адреса…" : listState.query || filter !== "all" ? "Адресов по этому фильтру нет" : "Добавьте первую точку входа"}</strong><p>{listState.query || filter !== "all" ? "Измените поиск или выберите «Все»." : "Подключите Relay или добавьте домен."}</p></div>}
    <details className="routeLibraryServer" open={serverExpanded || Boolean(listState.query && serverItems.length) || serverProblem} onToggle={event => setServerExpanded(event.currentTarget.open)}><summary><span>Адреса этого сервера<small>{addresses.status?.items.length ?? "…"}</small></span><span aria-hidden="true">⌄</span></summary><p>Для прямого подключения к VPS.</p>
      {serverProblem && <div className="routeLibraryCallout" role="status"><p>{addresses.pending ? "Проверяем результат назначения IP. Команда не повторяется." : "Актуальность адресов не подтверждена."}</p><button type="button" disabled={addresses.reading} onClick={() => void addresses.load()}>Проверить состояние</button></div>}
      {!addresses.status && <p>Определяем адреса…</p>}
      <div className="networkProviderGrid routeServerGrid">{serverItems.map(row => <button type="button" className="networkProvider routeLibraryRow" data-state={serverAddressSummary(row, Boolean(serverProblem), Boolean(addresses.pending)).state} aria-haspopup="dialog" key={row.address} onClick={() => open(`SERVER-${row.address}`)} aria-label={`Открыть ${row.address}`}><span className="networkProviderCode" aria-hidden="true">{row.family === "ipv4" ? "4" : "6"}</span><span className="networkProviderText"><strong>{row.address}</strong><small>{addressRoleLabels[row.role]}</small><NetworkRouteSummaryBadge summary={serverAddressSummary(row, Boolean(serverProblem), Boolean(addresses.pending))} /></span><span className="networkProviderIndicator" aria-hidden="true" /></button>)}
      </div>
    </details>
    {selected && <RouteControlDialog title={selected.title} address={selected.address} subtitle={selected.kind === "relay" ? selected.address : selected.subtitle} badge={<NetworkRouteSummaryBadge summary={selected.summary} />} close={close}>
      {needsAttention(selected.summary) && <p className="routeLibraryCallout" role="status">{selected.summary.detail}</p>}
      {selected.kind === "relay" ? <RelayEntryPanel key={selected.id} host={selected.host} relay={relay} close={close} /> : <DomainEntryPanel key={selected.id} entry={selected} request={request} disabled={busy || failed || endpointDirty} onChecked={onRefresh} onRemove={(kind, address) => setDeletion({ kind, address })}>
        {(selected.checks.some(check => check.kind === "cdn") || selected.domain.endpoint_kind === "cdn" || /cdn/i.test(selected.domain.role)) && <RouteDisclosure title="Защита CDN">{cloudflare && <NetworkCloudflare domain={selected.address} detected={selected.domain.edge?.provider.toLowerCase().includes("cloudflare") === true} request={request} control={{ ...cloudflare, blocked: cloudflare.blocked || busy || failed }} refreshKey={refreshKey} />}<NetworkEch domain={selected.address} route={selected.domain.route} request={request} /></RouteDisclosure>}
      </DomainEntryPanel>}
    </RouteControlDialog>}
    {serverRow && <RouteControlDialog title={serverRow.address} address={serverRow.address} subtitle="IP этого сервера" badge={<NetworkRouteSummaryBadge summary={serverAddressSummary(serverRow, Boolean(serverProblem), Boolean(addresses.pending))} />} close={close}>
      <section className="routeLibrarySection"><h3>{addressRoleLabels[serverRow.role]}</h3><p>{serverRow.role === "primary" ? "Используется в новых прямых конфигурациях." : serverRow.role === "reserve" ? "Сохранён как резервный. Автопереключение не включено." : "Можно назначить основным или сохранить в резерв."}</p><div className="routeLibraryQuickActions">{serverRow.role !== "primary" && <button type="button" disabled={addresses.locked || serverRow.presence !== "present"} onClick={() => void addresses.setRole(serverRow.address, "primary")}>Сделать основным</button>}{serverRow.role === "additional" && <button type="button" disabled={addresses.locked || serverRow.presence !== "present"} onClick={() => void addresses.setRole(serverRow.address, "reserve")}>В резерв</button>}{serverRow.role === "reserve" && <button type="button" disabled={addresses.locked || serverRow.presence !== "present"} onClick={() => void addresses.setRole(serverRow.address, "additional")}>Убрать из резерва</button>}</div><small>Существующие подключения не изменятся.</small></section>
      {serverRow.presence !== "present" && <p className="routeLibraryCallout">{serverAddressSummary(serverRow, Boolean(serverProblem)).detail}</p>}
    </RouteControlDialog>}
    {deletion && <ConfirmationDialog request={{ title: "Удалить адрес?", message: "Адрес перестанет предлагаться для новых подключений. Существующие подключения сохранят свои настройки.", confirmLabel: "Удалить", danger: true, phrase: deletion.address }} onClose={confirmed => { const item = deletion; setDeletion(null); if (confirmed) { close(); onRemove(item.kind, item.address); } }} />}
  </section>;
}

function RouteControlDialog({ title, address, subtitle, badge, close, children }: { title: string; address: string; subtitle: string; badge: ReactNode; close: () => void; children: ReactNode }) {
  const dialogRef = useDialogFocus(true, close);
  useEffect(() => {
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = overflow; };
  }, []);
  return <div className="networkModalBackdrop" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section ref={dialogRef} tabIndex={-1} className="networkModal routeControlModal" role="dialog" aria-modal="true" aria-labelledby="route-control-title">
      <header className="networkModalHeader"><div className="routeDetailsIdentity"><div><small>{subtitle}</small><h2 id="route-control-title">{title}</h2></div>{badge}</div><button type="button" className="networkModalClose" onClick={close} aria-label="Закрыть настройки адреса">×</button></header>
      <div className="routeDetailsBody"><CopyAddress address={address} />{children}</div>
    </section>
  </div>;
}

function CopyAddress({ address }: { address: string }) {
  const [copied, setCopied] = useState(false);
  const { error } = useNotifier("copy-route", "Адрес");
  return <button className="routeLibraryCopy" type="button" onClick={async () => { try { await navigator.clipboard.writeText(address); setCopied(true); } catch { error("Копирование недоступно. Выделите адрес и скопируйте его вручную."); } }}>{copied ? "Адрес скопирован" : "Скопировать адрес"}</button>;
}
