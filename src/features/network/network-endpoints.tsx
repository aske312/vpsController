"use client";

import { useEffect, useRef, useState } from "react";
import { ConfirmationDialog } from "../../shared/components/confirmation-dialog";
import { useDialogFocus } from "../../shared/components/use-dialog-focus";
import type {
  NetworkEndpointCheck,
  NetworkEndpointSettings,
} from "../../shared/types/control-plane";
import { checkNetworkEndpoint, type NetworkRequest } from "./network-api";
import { endpointCheckForConsumer } from "../../shared/lib/network-readiness";
import { NetworkRelayForm, type RelayControl } from "./network-relays";

type AddressMode = false | "cdn" | "tls" | "udp";
const routeTypes = [
  { mode: false, title: "Relay Agent", description: "Вход через другой сервер", tag: "TCP + UDP" },
  { mode: "cdn", title: "CDN", description: "Домен за CDN-провайдером", tag: "PROXY" },
  { mode: "tls", title: "TLS", description: "Прямое подключение по домену", tag: "DNS ONLY" },
  { mode: "udp", title: "UDP", description: "Прямой домен или IP", tag: "DNS ONLY" },
] as const;

function RouteTypeIcon({ mode }: { mode: AddressMode }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {mode === false ? <><rect x="3" y="3" width="7" height="6" rx="1.5" /><rect x="14" y="15" width="7" height="6" rx="1.5" /><path d="M6.5 9v7a2 2 0 0 0 2 2H14M17.5 15V8a2 2 0 0 0-2-2H10" /></> : mode === "cdn" ? <><path d="M7 18a5 5 0 0 1-1-9.9 6.5 6.5 0 0 1 12.3-1A5.5 5.5 0 0 1 18 18" /><path d="m9 16 3-3 3 3m-3-3v8" /></> : mode === "tls" ? <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2" /></> : <><path d="M4 8h16m-4-4 4 4-4 4M20 16H4m4-4-4 4 4 4" /></>}
  </svg>;
}

export function NetworkEndpoints({
  request,
  draft,
  initialChecks,
  knownCdnDomains = [],
  busy,
  readFailed,
  dirty,
  onChange,
  onRouteListChange,
  onSave,
  onReset,
  relay,
}: {
  request: NetworkRequest;
  draft: NetworkEndpointSettings;
  initialChecks?: Partial<Record<keyof NetworkEndpointSettings, NetworkEndpointCheck>>;
  knownCdnDomains?: string[];
  busy: boolean;
  readFailed: boolean;
  dirty: boolean;
  onChange: (key: keyof NetworkEndpointSettings, value: string) => void;
  onRouteListChange: (key: keyof NetworkEndpointSettings, values: string[]) => void;
  onSave: () => void;
  onReset: () => void;
  relay: RelayControl;
}) {
  const [opened, setOpen] = useState(false);
  const [addresses, setAddresses] = useState<AddressMode>(false);
  const open = opened && Boolean(addresses || relay.dialog && relay.embedded);
  const generation = useRef(0);
  const inFlight = useRef(false);
  useEffect(() => () => { generation.current++; }, []);
  const [discard, setDiscard] = useState(false);
  const [extra, setExtra] = useState<Record<string, string>>({});
  const pendingAddress = Object.values(extra).some((value) => value.trim());
  const hasChanges = dirty || pendingAddress;
  useEffect(() => {
    if (!open || !hasChanges) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [open, hasChanges]);
  const close = () => { if (!addresses) relay.close(); else if (hasChanges) setDiscard(true); else { generation.current++; setOpen(false); } };
  const dialogRef = useDialogFocus(open, close);
  const [checking, setChecking] = useState<string | null>(null);
  const [consumerIds, setConsumerIds] = useState<Record<string, string>>({});
  const [checks, setChecks] = useState<
    Partial<Record<keyof NetworkEndpointSettings, NetworkEndpointCheck>>
  >(initialChecks || {});
  const fields: Array<{
    key: keyof NetworkEndpointSettings;
    kind: NetworkEndpointCheck["kind"];
    label: string;
    short: string;
    placeholder: string;
    help: string;
  }> = [
    {
      key: "cdn_domain",
      kind: "cdn",
      label: "CDN / ECH",
      short: "Скрывает origin",
      placeholder: "cdn.example.com",
      help: "Для CDN/ECH нужен домен, IP здесь не подходит.",
    },
    {
      key: "tls_relay_domain",
      kind: "tls_relay",
      label: "TLS-домен",
      short: "Прямое TLS-подключение",
      placeholder: "tls.example.com",
      help: "Адрес сервера для TLS-подключений. Сертификат и SNI настраиваются в протоколе; сохранение адреса не создаёт TLS-вход.",
    },
    {
      key: "udp_relay_domain",
      kind: "udp_relay",
      label: "UDP-домен или IP",
      short: "Прямое UDP-подключение",
      placeholder: "tls.example.com или IP сервера",
      help: "Адрес для существующих UDP-протоколов. Можно использовать тот же домен, что и для TLS. Порт задаётся протоколом; сохранение адреса не запускает UDP-службу.",
    },
  ];
  const routeValues = (key: keyof NetworkEndpointSettings): string[] => {
    const listKey = ({ cdn_domain: "cdn_domains", tls_relay_domain: "tls_relay_domains", udp_relay_domain: "udp_relay_domains" } as const)[key as "cdn_domain" | "tls_relay_domain" | "udp_relay_domain"];
    const values = draft[listKey];
    return values?.length ? values : (draft[key] ? [String(draft[key])] : []);
  };
  async function check(field: (typeof fields)[number]) {
    const domain = String(draft[field.key] || "").trim();
    if (!domain || busy || readFailed || inFlight.current) return;
    const requestGeneration = generation.current;
    const consumerId = consumerIds[field.key];
    inFlight.current = true;
    setChecking(field.key);
    try {
      const result = await checkNetworkEndpoint(request, field.kind, domain, consumerId);
      if (generation.current !== requestGeneration) return;
      setChecks((current) => ({ ...current, [field.key]: { ...result, consumers: result.consumers || current[field.key]?.consumers } }));
    } catch {
      if (generation.current !== requestGeneration) return;
      setChecks((current) => ({
        ...current,
        [field.key]: {
          ...current[field.key],
          kind: field.kind,
          domain,
          consumer_id: consumerId,
          resolved: current[field.key]?.resolved || [],
          matches_origin: current[field.key]?.matches_origin || false,
          route: current[field.key]?.route || "unresolved",
          status: "unchecked",
          ready: false,
          verification: "dns_only",
          checked_at: null, check_id: null, checked_revision: null, failed_checks: 0,
          verified_connection: undefined,
          message: "Не удалось получить результат проверки. Обновите сведения о маршруте перед новой проверкой",
        },
      }));
    } finally {
      inFlight.current = false;
      setChecking(null);
    }
  }
  return (
    <>
      <button
        type="button"
        className="networkAddRouteButton"
        onClick={() => { generation.current++; setChecks(initialChecks || {}); setConsumerIds({}); setAddresses(current => hasChanges ? current || "cdn" : false); if (!hasChanges) relay.openHost(undefined, true); setOpen(true); }}
      >
        Добавить маршрут
      </button>
      {open && (
        <div
          className="networkModalBackdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) close();
          }}
        >
          <section
            ref={dialogRef}
            tabIndex={-1}
            className="networkModal networkRouteEditor"
            role="dialog"
            aria-modal="true"
            aria-labelledby="network-endpoints-title"
            aria-describedby="network-endpoints-description"
          >
            <header className="networkSectionHeading networkModalHeader">
              <div>
                <p className="eyebrow networkSectionEyebrow">ROUTES</p>
                <h2 id="network-endpoints-title">Новый маршрут</h2>
                <p id="network-endpoints-description">Выберите способ подключения и добавьте адрес для ваших протоколов.</p>
              </div>
              <button
                type="button"
                className="networkModalClose"
                onClick={close}
                aria-label="Закрыть"
              >
                ×
              </button>
            </header>
            <div className="networkRouteEditorBody">
            <aside className="networkRouteSidebar">
              <p className="networkRouteStep">01 <span>Способ подключения</span></p>
              <nav className="networkRouteChoices" aria-label="Тип маршрута">
                {routeTypes.map((type) => <button key={String(type.mode)} type="button" aria-pressed={addresses === type.mode} data-dialog-initial={addresses === type.mode || undefined} disabled={addresses !== type.mode && (hasChanges || relay.changed)} onClick={() => {
                  if (addresses === type.mode) return;
                  generation.current++;
                  if (type.mode === false) relay.openHost(undefined, true); else relay.resetDialog();
                  setAddresses(type.mode);
                }}><i><RouteTypeIcon mode={type.mode} /></i><span><strong>{type.title}</strong><small>{type.description}</small></span><b aria-hidden="true">›</b></button>)}
              </nav>
              <p className="networkRouteSidebarNote">{(addresses ? hasChanges : relay.changed) ? "Сохраните или отмените правки перед сменой типа маршрута." : "Адрес будет доступен при настройке подключений Direct и Mihomo."}</p>
            </aside>
            <div className="networkRouteContent">
              <header className="networkRouteContentHeader"><div><p className="networkRouteStep">02 <span>Настройка маршрута</span></p><h3>{addresses === false ? "Подключение Relay Agent" : addresses === "cdn" ? "Домен для CDN" : addresses === "tls" ? "Домен для TLS" : "Адрес для UDP"}</h3></div><span className="networkRouteKindBadge">{routeTypes.find((type) => type.mode === addresses)?.tag}</span></header>
            {!addresses && <NetworkRelayForm relay={relay} />}
            {addresses && <>
            {addresses === "cdn" && knownCdnDomains.length > 0 && <details className="networkKnownRoutes"><summary>Обнаруженные CDN-адреса · {knownCdnDomains.length}</summary><div>{knownCdnDomains.map((domain) => <code key={domain}>{domain}</code>)}</div><small>Общий адрес для новых конфигураций задаётся в основном поле ниже.</small></details>}
            <div className="networkEndpointGrid">
              {fields.filter((field) => field.kind === ({ cdn: "cdn", tls: "tls_relay", udp: "udp_relay" } as const)[addresses]).map((field) => {
                const checked = checks[field.key];
                const result = endpointCheckForConsumer(checked?.domain.toLowerCase() === String(draft[field.key] || "").trim().toLowerCase() ? checked : undefined, consumerIds[field.key] || "");
                const value = String(draft[field.key] || "").trim();
                const values = routeValues(field.key);
                return (
                  <article
                    className={`networkEndpointCard ${result?.status || ""}`}
                    key={field.key}
                  >
                    <div className="networkEndpointCardHead">
                      <div>
                        <h3>{field.label}</h3>
                        <span>{field.short}</span>
                      </div>
                      <span className="networkEndpointState">
                        {result
                          ? result.status === "ready"
                            ? "READY"
                            : result.status === "unchecked"
                              ? "UNCHECKED"
                            : result.status === "warning"
                              ? "WARN"
                              : "ERROR"
                          : value
                            ? "Не проверен"
                            : "Не задан"}
                      </span>
                    </div>
                    <div className="networkEndpointInput">
                      <label className="networkEndpointPrimaryField"><span>Основной адрес</span>
                      <input
                        aria-label={field.label}
                        type="text"
                        value={draft[field.key]}
                        disabled={busy || readFailed}
                        onChange={(event) => {
                          generation.current++;
                          setConsumerIds((current) => ({ ...current, [field.key]: "" }));
                          setChecks((current) => {
                            const next = { ...current };
                            delete next[field.key];
                            return next;
                          });
                          onChange(field.key, event.target.value);
                        }}
                        placeholder={field.placeholder}
                        autoComplete="off"
                      />
                      </label>
                      <button
                        type="button"
                        onClick={() => void check(field)}
                        disabled={busy || readFailed || Boolean(checking) || !value}
                      >
                        {checking === field.key ? "Проверяем…" : "Проверить"}
                      </button>
                    </div>
                    {!!result?.consumers?.length && <label><span>Подключение для проверки</span><select aria-label={`Подключение ${field.label}`} value={consumerIds[field.key] || ""} disabled={Boolean(checking)} onChange={(event) => setConsumerIds((current) => ({ ...current, [field.key]: event.target.value }))}><option value="">Первое совместимое подключение</option>{result.consumers.map((consumer) => <option key={consumer.id} value={consumer.id}>{consumer.label} · {consumer.id}</option>)}</select></label>}
                    <small>{field.help}</small>
                    <small className="networkEndpointDnsHint">{field.kind === "cdn" ? "DNS: A/AAAA на IP этого VPS, proxy включён у CDN-провайдера. IP вместо домена не поддерживается." : "DNS: A/AAAA на IP этого VPS. В Cloudflare выберите DNS only — серое облако. Порт в DNS-записи не указывается."}</small>
                    {result && (
                      <em className={`networkEndpointCheck ${result.status}`}>
                        {result.message}
                        {result.resolved.length
                          ? ` · ${result.resolved.join(", ")}`
                          : ""}
                        {result.failed_checks
                          ? ` · неудач подряд: ${result.failed_checks}`
                          : ""}
                        {result.checked_at
                          ? ` · ${new Date(result.checked_at).toLocaleString("ru-RU")}`
                          : ""}
                      </em>
                    )}
                    <details className="networkEndpointRouteList">
                      <summary>Дополнительные адреса <span>{Math.max(0, values.length - 1)}</span></summary>
                      {values.slice(1).map((route) => <div key={route}><code>{route}</code></div>)}
                      <label>Дополнительный адрес · {field.label}
                        <input type="text" value={extra[field.key] || ""} placeholder={field.placeholder} autoComplete="off" disabled={busy} onChange={(event) => setExtra((current) => ({ ...current, [field.key]: event.target.value }))} />
                      </label>
                      <button type="button" disabled={busy || !extra[field.key]?.trim() || values.includes(extra[field.key].trim().toLowerCase())} onClick={() => {
                        const route = extra[field.key].trim().toLowerCase();
                        onRouteListChange(field.key, [...values, route]);
                        setExtra((current) => ({ ...current, [field.key]: "" }));
                      }}>Добавить адрес</button>
                    </details>
                  </article>
                );
              })}
            </div>
            {pendingAddress && <p className="networkRouteDraftHint" role="status">Нажмите «Добавить адрес» для заполненного дополнительного поля или очистите его перед сохранением.</p>}
            <div className="networkEndpointFooter">
              <p>
                {addresses === "cdn" ? "CDN/ECH требует домен. Relay подключается отдельно через Agent; его адрес выбирается в настройках протокола." : "Сохраняется адрес и его назначение. Подключения, порты и сертификаты настраиваются отдельно в протоколах; существующие конфигурации клиентов не меняются."}
              </p>
              <div className="networkRouteFooterActions"><button type="button" onClick={close}>Отмена</button>
              <button
                type="button"
                className="networkPrimaryButton"
                onClick={onSave}
                disabled={busy || readFailed || !dirty || pendingAddress}
              >
                Сохранить маршрут
              </button>
              </div>
            </div>
            </>}
            </div>
            </div>
          </section>
        </div>
      )}
      {discard && <ConfirmationDialog request={{ title: "Закрыть без сохранения?", message: "Изменения внешних адресов будут потеряны. Настройки на сервере не изменятся.", cancelLabel: "Остаться", confirmLabel: "Уйти без сохранения" }} onClose={(confirmed) => {
        setDiscard(false);
        if (confirmed) { generation.current++; onReset(); setExtra({}); setOpen(false); }
      }} />}
    </>
  );
}
