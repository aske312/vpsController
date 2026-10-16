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
  const [addresses, setAddresses] = useState(false);
  const open = opened && (addresses || relay.dialog && relay.embedded);
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
      label: "TLS relay",
      short: "TCP через внешний сервер",
      placeholder: "relay.example.com или 203.0.113.10",
      help: "Укажите домен или IP внешнего relay. Порт берётся из выбранного протокола.",
    },
    {
      key: "udp_relay_domain",
      kind: "udp_relay",
      label: "UDP relay",
      short: "UDP через внешний сервер",
      placeholder: "relay.example.com или 203.0.113.10",
      help: "Укажите домен или IP внешнего relay. Relay должен пересылать нужный UDP-порт.",
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
        onClick={() => { generation.current++; setChecks(initialChecks || {}); setConsumerIds({}); setAddresses(hasChanges); if (!hasChanges) relay.openHost(undefined, true); setOpen(true); }}
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
            className="networkModal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="network-endpoints-title"
          >
            <header className="networkSectionHeading networkModalHeader">
              <div>
                <p className="eyebrow networkSectionEyebrow">ROUTES</p>
                <h2 id="network-endpoints-title">Добавить маршрут</h2>
                <p>
                  {addresses ? "CDN / ECH: домен для новых клиентских конфигураций." : "Relay: внешний узел с IP или доменом для клиентов."}
                </p>
                {addresses && knownCdnDomains.length > 0 && <div className="networkKnownRoutes"><span>Обнаруженные CDN адреса</span><div>{knownCdnDomains.map((domain) => <code key={domain}>{domain}</code>)}</div><small>Активным общим адресом для новых конфигураций остаётся значение в поле CDN / ECH.</small></div>}
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
            <div className="networkRouteChoices" aria-label="Тип маршрута">
              <button type="button" aria-pressed={!addresses} disabled={addresses && hasChanges} onClick={() => { if (!addresses) return; relay.openHost(undefined, true); setAddresses(false); }}><strong>Relay · IP / домен</strong><span>Relay Agent · TCP и UDP</span></button>
              <button type="button" aria-pressed={addresses} disabled={!addresses && relay.changed} onClick={() => { setAddresses(true); relay.resetDialog(); }}><strong>CDN / ECH · домен</strong><span>Домен и proxy у CDN-провайдера</span></button>
            </div>
            {(addresses ? hasChanges : relay.changed) && <p className="networkRouteTypeHint">Сохраните или отмените правки перед сменой типа маршрута.</p>}
            {!addresses && <NetworkRelayForm relay={relay} />}
            {addresses && <>
            <div className="networkEndpointGrid">
              {fields.filter((field) => field.kind === "cdn").map((field) => {
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
                      <input
                        aria-label={field.label}
                        type="text"
                        value={draft[field.key]}
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
                    <small className="networkEndpointDnsHint">DNS: A/AAAA на IP этого VPS, proxy включён у CDN-провайдера. IP вместо домена не поддерживается.</small>
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
                    <div className="networkEndpointRouteList">
                      <span>Адреса этого типа</span>
                      {values.slice(1).map((route) => <div key={route}><code>{route}</code></div>)}
                      <label>Дополнительный адрес · {field.label}
                        <input type="text" value={extra[field.key] || ""} placeholder={field.placeholder} autoComplete="off" disabled={busy} onChange={(event) => setExtra((current) => ({ ...current, [field.key]: event.target.value }))} />
                      </label>
                      <button type="button" disabled={busy || !extra[field.key]?.trim() || values.includes(extra[field.key].trim().toLowerCase())} onClick={() => {
                        const route = extra[field.key].trim().toLowerCase();
                        onRouteListChange(field.key, [...values, route]);
                        setExtra((current) => ({ ...current, [field.key]: "" }));
                      }}>Добавить адрес</button>
                    </div>
                  </article>
                );
              })}
            </div>
            {pendingAddress && <p role="status">Нажмите «Добавить адрес» для заполненного дополнительного поля или очистите его перед сохранением.</p>}
            <div className="networkEndpointFooter">
              <p>
                CDN/ECH требует домен. Relay подключается отдельно через Agent;
                его адрес выбирается в настройках протокола.
              </p>
              <button type="button" onClick={close}>Отмена</button>
              <button
                type="button"
                className="networkPrimaryButton"
                onClick={onSave}
                disabled={busy || readFailed || !dirty || pendingAddress}
              >
                Сохранить адреса
              </button>
            </div>
            </>}
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
