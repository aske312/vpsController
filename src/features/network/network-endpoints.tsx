"use client";

import { useEffect, useRef, useState } from "react";
import { ConfirmationDialog } from "../../shared/components/confirmation-dialog";
import { useDialogFocus } from "../../shared/components/use-dialog-focus";
import type {
  NetworkEndpointCheck,
  NetworkEndpointSettings,
} from "../../shared/types/control-plane";
import { checkNetworkEndpoint, type NetworkRequest } from "./network-api";
import { emptyEndpointSettings, endpointAddress, endpointValues, type EndpointField } from "./endpoint-additions";
import { endpointCheckForConsumer } from "../../shared/lib/network-readiness";
import { NetworkRelayForm, type RelayControl } from "./network-relays";

import { RouteTypeIcon } from "./route-type-icon";

type AddressMode = false | "cdn" | "tls" | "udp";
const routeTypes = [
  { mode: false, title: "Relay Agent", description: "Вход через другой сервер", tag: "TCP + UDP" },
  { mode: "cdn", title: "CDN", description: "Домен за CDN-провайдером", tag: "PROXY" },
  { mode: "tls", title: "TLS", description: "Прямое подключение по домену", tag: "DNS ONLY" },
  { mode: "udp", title: "UDP", description: "Прямой домен или IP", tag: "DNS ONLY" },
] as const;

export function NetworkEndpoints({
  request,
  existing,
  busy,
  readFailed,
  onDirtyChange,
  onSave,
  onReset,
  relay,
}: {
  request: NetworkRequest;
  existing: NetworkEndpointSettings;
  busy: boolean;
  readFailed: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onSave: (additions: NetworkEndpointSettings) => void;
  onReset: () => void;
  relay: RelayControl;
}) {
  const [opened, setOpen] = useState(false);
  const [addresses, setAddresses] = useState<AddressMode>(false);
  const open = opened;
  const generation = useRef(0);
  const inFlight = useRef(false);
  useEffect(() => () => { generation.current++; }, []);
  const [discard, setDiscard] = useState(false);
  const [draft, setDraft] = useState(emptyEndpointSettings);
  const [extra, setExtra] = useState<Record<string, string>>({});
  const pendingAddress = Object.values(extra).some((value) => value.trim());
  const dirty = (Object.keys(emptyEndpointSettings()) as EndpointField[]).some(key => {
    const saved = new Set(endpointValues(existing, key).map(endpointAddress));
    return endpointValues(draft, key).some(value => endpointAddress(value) && !saved.has(endpointAddress(value)));
  });
  const hasChanges = dirty || pendingAddress;
  useEffect(() => { onDirtyChange(hasChanges); }, [hasChanges, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  useEffect(() => {
    if (!open || !hasChanges) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [open, hasChanges]);
  const close = () => {
    if (hasChanges || relay.changed) setDiscard(true);
    else { generation.current++; relay.resetDialog(); setOpen(false); }
  };
  const dialogRef = useDialogFocus(open, close);
  const [checking, setChecking] = useState<string | null>(null);
  const [consumerIds, setConsumerIds] = useState<Record<string, string>>({});
  const [checks, setChecks] = useState<
    Partial<Record<keyof NetworkEndpointSettings, NetworkEndpointCheck>>
  >({});
  const fields: Array<{
    key: EndpointField;
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
  const routeValues = (key: EndpointField) => endpointValues(draft, key);
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
        onClick={() => { generation.current++; setDraft(emptyEndpointSettings()); setExtra({}); setChecks({}); setConsumerIds({}); setAddresses(false); relay.openHost(undefined, true); setOpen(true); }}
      >
        Добавить
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
          >
            <header className="networkSectionHeading networkModalHeader">
              <div>
                <h2 id="network-endpoints-title">Добавить точку входа</h2>
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
              <nav className="networkRouteChoices" aria-label="Тип маршрута">
                {routeTypes.map((type) => <button key={String(type.mode)} type="button" aria-pressed={addresses === type.mode} data-dialog-initial={addresses === type.mode || undefined} onClick={() => {
                  if (addresses === type.mode) return;
                  generation.current++;
                  if (type.mode === false && !relay.dialog) relay.openHost(undefined, true);
                  setAddresses(type.mode);
                }}><i><RouteTypeIcon kind={type.mode === false ? "relay" : type.mode} /></i><span><strong>{type.title}</strong><small>{type.description}</small></span><b aria-hidden="true">›</b></button>)}
              </nav>
              {(hasChanges || relay.changed) && <p className="networkRouteSidebarNote">Есть несохранённые изменения.</p>}
            </aside>
            <div className="networkRouteContent">
              <header className="networkRouteContentHeader"><h3>{addresses === false ? "Подключить Relay" : addresses === "cdn" ? "Домен для CDN" : addresses === "tls" ? "Домен для TLS" : "Адрес для UDP"}</h3></header>
            {!addresses && <NetworkRelayForm relay={{ ...relay, close, saveHost: async () => {
              const saveGeneration = generation.current;
              const saved = await relay.saveHost();
              if (saved && !hasChanges && generation.current === saveGeneration) { generation.current++; setOpen(false); }
              return saved;
            } }} />}
            {addresses && <>
            <div className="networkEndpointGrid">
              {fields.filter((field) => field.kind === ({ cdn: "cdn", tls: "tls_relay", udp: "udp_relay" } as const)[addresses]).map((field) => {
                const checked = checks[field.key];
                const result = endpointCheckForConsumer(checked?.domain.toLowerCase() === String(draft[field.key] || "").trim().toLowerCase() ? checked : undefined, consumerIds[field.key] || "");
                const value = String(draft[field.key] || "").trim();
                const values = routeValues(field.key);
                const duplicate = Boolean(value && endpointValues(existing, field.key).some(saved => endpointAddress(saved) === endpointAddress(value)));
                return (
                  <article
                    className={`networkEndpointCard ${result?.status || ""}`}
                    key={field.key}
                  >
                    <div className="networkEndpointInput">
                      <label className="networkEndpointPrimaryField"><span>Новый адрес</span>
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
                          const value = event.target.value;
                          setDraft(current => ({ ...current, [field.key]: value }));
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
                    {duplicate && <small className="networkRouteDraftHint">Этот адрес уже добавлен для выбранного типа маршрута.</small>}
                    {!!result?.consumers?.length && <label><span>Подключение для проверки</span><select aria-label={`Подключение ${field.label}`} value={consumerIds[field.key] || ""} disabled={Boolean(checking)} onChange={(event) => setConsumerIds((current) => ({ ...current, [field.key]: event.target.value }))}><option value="">Первое совместимое подключение</option>{result.consumers.map((consumer) => <option key={consumer.id} value={consumer.id}>{consumer.label} · {consumer.id}</option>)}</select></label>}
                    <details className="networkEndpointDnsHint"><summary>Как настроить DNS</summary><p>{field.kind === "cdn" ? "A/AAAA на IP VPS, proxy включён у CDN-провайдера. Нужен домен." : "Для домена: A/AAAA на IP VPS. В Cloudflare выберите DNS only. Порт в DNS не указывается."}</p><p>{field.help}</p></details>
                    {result && (
                      <em className={`networkEndpointCheck ${result.status}`}>
                        {result.message}
                        {result.checked_at
                          ? ` · ${new Date(result.checked_at).toLocaleString("ru-RU")}`
                          : ""}
                      </em>
                    )}
                    <details className="networkEndpointRouteList">
                      <summary>Дополнительные адреса <span>{values.filter(route => route !== draft[field.key]).length}</span></summary>
                      {values.filter(route => route !== draft[field.key]).map((route) => <div key={route}><code>{route}</code></div>)}
                      <label>Дополнительный адрес · {field.label}
                        <input type="text" value={extra[field.key] || ""} placeholder={field.placeholder} autoComplete="off" disabled={busy} onChange={(event) => setExtra((current) => ({ ...current, [field.key]: event.target.value }))} />
                      </label>
                      <button type="button" disabled={busy || readFailed || !extra[field.key]?.trim() || [...values, ...endpointValues(existing, field.key)].some(value => endpointAddress(value) === endpointAddress(extra[field.key]))} onClick={() => {
                        const route = endpointAddress(extra[field.key]);
                        const listKey = ({ cdn_domain: "cdn_domains", tls_relay_domain: "tls_relay_domains", udp_relay_domain: "udp_relay_domains" } as const)[field.key];
                        setDraft(current => ({ ...current, [listKey]: [...(current[listKey] || []), route] }));
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
                Существующие подключения не изменятся.
              </p>
              <div className="networkRouteFooterActions"><button type="button" onClick={close}>Отмена</button>
              <button
                type="button"
                className="networkPrimaryButton"
                onClick={() => onSave(draft)}
                disabled={busy || readFailed || !dirty || pendingAddress}
              >
                Добавить адреса
              </button>
              </div>
            </div>
            </>}
            </div>
            </div>
          </section>
        </div>
      )}
      {discard && <ConfirmationDialog request={{ title: "Закрыть без сохранения?", message: "Несохранённые изменения адресов и подключения Relay будут потеряны. Уже сохранённые настройки останутся на сервере.", cancelLabel: "Остаться", confirmLabel: "Уйти без сохранения" }} onClose={(confirmed) => {
        setDiscard(false);
        if (confirmed) { generation.current++; onReset(); setDraft(emptyEndpointSettings()); setExtra({}); relay.resetDialog(); setOpen(false); }
      }} />}
    </>
  );
}
