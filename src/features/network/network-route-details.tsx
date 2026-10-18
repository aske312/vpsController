import type { ReactNode } from "react";
import type { NetworkEndpointCheck, NetworkIpIdentity, NetworkStatus } from "../../shared/types/control-plane";
import type { RelayHost } from "./relay-state";
import type { ServerAddress } from "./server-address-state";
import { addressRoleLabels } from "./server-address-state";
import { domainRouteSummary, type RouteSummary } from "./route-summary";
import { NetworkRouteBadge, NetworkRouteSummaryBadge } from "./network-route-badge";

type Domain = NetworkStatus["domains"][number];
const sourceLabels: Record<string, string> = { environment: "Настройки сервера", settings: "Настройки маршрутов", gateway: "Шлюз подключений", connection: "Сохранённые подключения" };
const kindLabels = { cdn: "CDN", tls_relay: "TLS", udp_relay: "UDP" };
const known = (value?: string): value is string => Boolean(value?.trim() && !/^(unknown|—)$/i.test(value.trim()));
const shown = (value?: string) => known(value) ? value : "Не получено";
function time(value?: string) {
  if (!value) return "Время не получено";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Время не получено" : date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function NetworkRouteDetails({ address, summary, observedAt, children }: { address: string; summary: RouteSummary; observedAt?: string; children: ReactNode }) {
  return <section className="networkRouteCascade networkRouteDetails" aria-label={`Сведения об адресе ${address}`}>
    <header className="routeDetailsHeading"><h3>{summary.type}</h3><div className="routeDetailsHeadingMeta"><NetworkRouteSummaryBadge summary={summary} />{observedAt && <small>Снимок: {time(observedAt)}</small>}</div></header>
    <p className="routeDetailsEvidence">{summary.detail}</p>
    <div className="routeDetailsSections">{children}</div>
  </section>;
}

export function RouteDetailsSection({ title, children, wide = false, collapsible = false, hint }: { title: string; children: ReactNode; wide?: boolean; collapsible?: boolean; hint?: string }) {
  if (collapsible) return <details className={`routeDetailsSection routeDetailsCollapsible${wide ? " wide" : ""}`} aria-label={title}><summary><span>{title}</span>{hint && <small>{hint}</small>}</summary><div className="routeDetailsDisclosure">{children}</div></details>;
  return <section className={`routeDetailsSection${wide ? " wide" : ""}`} aria-label={title}><h4>{title}</h4>{children}</section>;
}
function Field({ label, children, mono = false }: { label: string; children: ReactNode; mono?: boolean }) {
  return <div className="routeDetailsField"><dt>{label}</dt><dd className={mono ? "routeDetailsValue" : undefined}>{children}</dd></div>;
}
function Values({ values, empty = "Не получено" }: { values: string[]; empty?: string }) {
  return values.length ? <ul className="routeDetailsValues">{[...new Set(values)].map(value => <li key={value}>{value}</li>)}</ul> : <span className="routeDetailsMissing">{empty}</span>;
}

function IpIdentity({ address, info, compact = false }: { address: string; info?: NetworkIpIdentity; compact?: boolean }) {
  if (!info || ![info.hoster, info.provider, info.asn, info.network, info.ptr].some(known)) return <div className="routeDetailsIp"><strong>{address}</strong><p>Сведения о сети и провайдере не получены.</p></div>;
  return <div className="routeDetailsIp"><strong>{address}</strong><dl className="routeDetailsFacts">
    <Field label="Сеть / провайдер">{shown(known(info.hoster) ? info.hoster : info.provider)}</Field>
    {!compact && known(info.provider) && known(info.hoster) && info.provider !== info.hoster && <Field label="Владелец диапазона">{info.provider}</Field>}
    <Field label="ASN" mono>{shown(info?.asn)}</Field>
    {info?.network && <Field label="Диапазон" mono>{info.network}</Field>}
    {!compact && <Field label="Обратная DNS-запись" mono>{shown(info?.ptr)}</Field>}
  </dl>{compact && <details className="routeDetailsTechnical"><summary>Реквизиты IP</summary><dl className="routeDetailsFacts"><Field label="Владелец диапазона">{shown(info.provider)}</Field><Field label="Обратная DNS-запись" mono>{shown(info.ptr)}</Field></dl></details>}</div>;
}

export function ServerAddressDetails({ row, summary, observedAt, identity }: { row: ServerAddress; summary: RouteSummary; observedAt?: string; identity?: NetworkIpIdentity }) {
  const roleNote = row.role === "primary" ? "Для новых прямых конфигураций. Существующие подключения не меняются."
    : row.role === "reserve" ? "Резерв сохранён. Автопереключение не включено."
    : "Можно назначить основным или резервным.";
  return <NetworkRouteDetails address={row.address} summary={summary} observedAt={observedAt}>
    <RouteDetailsSection title="Адрес на сервере"><dl className="routeDetailsFacts">
      <Field label="Назначение">{addressRoleLabels[row.role]}</Field>
      <Field label="Интерфейсы" mono><Values values={row.interfaces} empty="Не подтверждены" /></Field>
      <Field label="Источник">{({ interface: "Обнаружен на интерфейсе", configured: "Настройки сервера", saved: "Сохранённое назначение" })[row.source]}</Field>
    </dl><p>{roleNote}</p>
      {row.source === "configured" && <p>IP задан в настройках, но не подтверждён на интерфейсе. Возможен доступ через NAT.</p>}
      {row.source === "saved" && <p>Адрес сохранён ранее. Его наличие на активном интерфейсе сейчас не подтверждено.</p>}
    </RouteDetailsSection>
    <RouteDetailsSection title="Сеть / провайдер"><IpIdentity address={row.address} info={identity} compact /></RouteDetailsSection>
  </NetworkRouteDetails>;
}

export function DomainRouteDetails({ domain, checks, summary, observedAt, failed, children }: { domain: Domain; checks: NetworkEndpointCheck[]; summary: RouteSummary; observedAt?: string; failed: boolean; children?: ReactNode }) {
  const records = domain.dns?.records;
  const hasCdn = domain.endpoint_kind === "cdn" || checks.some(check => check.kind === "cdn") || /cdn/i.test(domain.role);
  const addresses = domain.resolved.length ? domain.resolved : (domain.ip_info || []).map(info => info.address);
  return <NetworkRouteDetails address={domain.value} summary={summary} observedAt={observedAt}>
    <RouteDetailsSection title="Использование"><dl className="routeDetailsFacts">
      <Field label="Источник">{sourceLabels[domain.source] || "Сведения сервера"}</Field>
      <Field label="Направление DNS">{summary.connection}</Field>
      {(hasCdn || domain.edge?.confidence === "high") && <Field label="Провайдер CDN">{domain.edge?.confidence === "high" ? shown(domain.edge.provider) : "Не подтверждён"}</Field>}
    </dl>
      {domain.status === "stale" && <p className="routeDetailsWarning">Адрес больше не предлагается для новых подключений.</p>}
      {domain.stale_usages?.length ? <div className="routeDetailsUsage"><strong>Сохранён в подключениях</strong><Values values={domain.stale_usages} /></div> : null}
    </RouteDetailsSection>
    <RouteDetailsSection title="DNS"><dl className="routeDetailsFacts">
      <Field label="Адреса" mono><Values values={domain.resolved.slice(0, 2)} empty="Не получены" />{domain.resolved.length > 2 && <span className="routeDetailsMissing">Ещё {domain.resolved.length - 2} · в DNS-записях</span>}</Field>
      <Field label="DNS-провайдер">{shown(domain.dns?.provider)}</Field>
    </dl></RouteDetailsSection>
    <RouteDetailsSection title="DNS-записи" wide collapsible hint={`${domain.resolved.length} IP`}><dl className="routeDetailsFacts">
      <Field label="DNS-провайдер">{shown(domain.dns?.provider)}</Field>
      <Field label="NS" mono><Values values={domain.dns?.nameservers || []} /></Field>
      <Field label="A · IPv4" mono><Values values={records?.a || []} empty={records ? "Нет записей" : "Не получено"} /></Field>
      <Field label="AAAA · IPv6" mono><Values values={records?.aaaa || []} empty={records ? "Нет записей" : "Не получено"} /></Field>
      <Field label="CNAME" mono><Values values={records?.cname || []} empty={records ? "Нет записей" : "Не получено"} /></Field>
    </dl></RouteDetailsSection>
    {Boolean(domain.ip_info?.length) && <RouteDetailsSection title="Сведения об IP-адресах" wide collapsible hint={`${addresses.length} IP`}>
      {addresses.length ? <div className="routeDetailsIpGrid">{addresses.map(address => <IpIdentity key={address} address={address} info={domain.ip_info?.find(info => info.address === address)} />)}</div> : <p>IP-адреса не получены.</p>}
    </RouteDetailsSection>}
    <RouteDetailsSection title="Проверки подключения" wide collapsible hint={checks.length ? `Типов: ${checks.length}` : "Нет результатов"}>
      {checks.length ? <div className="routeDetailsChecks">{checks.map(check => {
        const result = domainRouteSummary({ ...domain, role: "", endpoint_kind: check.kind }, [check], failed);
        return <article className="routeDetailsCheck" key={check.kind}>
          <header><strong>{kindLabels[check.kind]}</strong><NetworkRouteSummaryBadge summary={result} /></header>
          <p>{failed ? "Показан сохранённый результат. Актуальное состояние не подтверждено." : ["unchecked", "unknown", "stale"].includes(result.state) ? result.detail : check.message || result.detail}</p>
          <dl className="routeDetailsFacts">
            <Field label="Последняя проверка">{time(check.checked_at || undefined)}</Field>
            {check.failed_checks !== undefined && <Field label="Неудач подряд">{failed ? "Не подтверждено" : check.failed_checks}</Field>}
            <Field label="Связанные подключения"><Values values={check.consumers?.map(item => item.label) || []} empty={check.consumers ? "Нет привязанных подключений" : "Сведения не получены"} /></Field>
          </dl>
        </article>;
      })}</div> : <p>{domain.role.toLowerCase().includes("panel") ? "Для домена панели здесь показаны сведения DNS. Они не подтверждают доступность HTTPS и действительность сертификата." : "Результат проверки подключения не получен."}</p>}
    </RouteDetailsSection>
    {children}
  </NetworkRouteDetails>;
}

export function RelayRouteDetails({ host, address, summary, unknown }: { host: RelayHost; address: string; summary: RouteSummary; unknown: boolean }) {
  return <NetworkRouteDetails address={address} summary={summary}>
    <RouteDetailsSection title="Подключение к агенту"><dl className="routeDetailsFacts">
      <Field label="Имя узла">{shown(host.name)}</Field>
      <Field label="Адрес агента" mono>{host.agent_url}</Field>
      {host.addresses && <Field label="Адреса для клиентов"><Values values={[...host.addresses.ipv4, ...host.addresses.ipv6, ...host.addresses.domains]} /></Field>}
      <Field label="Версия агента">{unknown ? "Не подтверждена" : shown(host.version)}</Field>
      <Field label="Перенаправления приложения">{unknown && !host.routes.length ? "Не получено" : `${host.routes.length}${unknown ? " · сохранённые сведения" : ""}`}</Field>
      <Field label="Другие правила агента">{unknown ? "Не подтверждены" : host.foreign_routes}</Field>
    </dl>
      {host.error && <p className="routeDetailsWarning" role="status">{host.error}</p>}
      <details className="routeDetailsTechnical"><summary>Сохранённый отпечаток сертификата</summary><code>{host.certificate_sha256}</code></details>
    </RouteDetailsSection>
    <RouteDetailsSection title="Состояние перенаправлений"><dl className="routeDetailsFacts">
      <Field label="Слушающие порты">{unknown ? "Не подтверждены" : host.routes.length ? `${host.routes.filter(route => route.state === "listening").length} из ${host.routes.length}` : "Нет перенаправлений"}</Field>
      <Field label="Проверки с VPS">{unknown ? "Не подтверждены" : host.routes.length ? `${host.routes.filter(route => route.state === "listening" && route.check?.state === "ready").length} из ${host.routes.length}` : "Нет результатов"}</Field>
      <Field label="Правила с ошибками">{unknown ? "Не подтверждены" : host.routes.filter(route => ["failed", "missing", "drift"].includes(route.state) || route.check?.state === "failed").length}</Field>
    </dl><p>{unknown ? "Сохранённые правила не доказывают, что порты сейчас слушают." : "Слушающий порт и проверка с VPS показаны отдельно. Полный путь с внешнего клиента здесь не проверяется."}</p></RouteDetailsSection>
    <RouteDetailsSection title="Перенаправления Relay" wide collapsible hint={unknown ? "Сохранённые сведения" : `Правил: ${host.routes.length}`}>
      {host.routes.length ? <div className="routeDetailsForwardings" aria-label="Перенаправления Relay">{host.routes.map(route => <article key={route.id} className="routeDetailsForwarding">
        <header><strong>{route.transport.toUpperCase()} · порт {route.listen_port}</strong><NetworkRouteBadge tone={unknown || route.state === "unknown" ? "unchecked" : route.state === "listening" ? "active" : "error"}>{unknown ? "Не подтверждён" : ({ listening: "Слушает", failed: "Ошибка", missing: "Отсутствует", drift: "Изменён", unknown: "Неизвестно" })[route.state]}</NetworkRouteBadge></header>
        <dl className="routeDetailsFacts">
          <Field label="Адрес назначения" mono>{route.target_ip.includes(":") ? `[${route.target_ip}]` : route.target_ip}:{route.target_port}</Field>
          <Field label="Проверка подключения">{unknown || !route.check || route.check.state === "unknown" ? "Не подтверждена" : route.check.state === "ready" ? "Пройдена с VPS" : "Ошибка проверки"}</Field>
          {route.check?.checked_at && <Field label="Время проверки">{time(route.check.checked_at)}</Field>}
        </dl>
      </article>)}</div> : <p>{unknown ? "Сведения о портах не получены." : "Перенаправления приложения не настроены."}</p>}
    </RouteDetailsSection>
  </NetworkRouteDetails>;
}
