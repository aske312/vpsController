"use client";

import { useState } from "react";
import type {
  NetworkEndpointCheck,
  NetworkEndpointSettings,
} from "../../shared/types/control-plane";
import { checkNetworkEndpoint, type NetworkRequest } from "./network-api";

export function NetworkEndpoints({
  request,
  draft,
  initialChecks,
  knownCdnDomains = [],
  busy,
  dirty,
  onChange,
  onRouteListChange,
  onSave,
}: {
  request: NetworkRequest;
  draft: NetworkEndpointSettings;
  initialChecks?: Partial<Record<keyof NetworkEndpointSettings, NetworkEndpointCheck>>;
  knownCdnDomains?: string[];
  busy: boolean;
  dirty: boolean;
  onChange: (key: keyof NetworkEndpointSettings, value: string) => void;
  onRouteListChange: (key: keyof NetworkEndpointSettings, values: string[]) => void;
  onSave: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [checking, setChecking] = useState<string | null>(null);
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
    if (!domain) return;
    setChecking(field.key);
    try {
      const result = await checkNetworkEndpoint(request, field.kind, domain);
      setChecks((current) => ({ ...current, [field.key]: result }));
    } catch {
      setChecks((current) => ({
        ...current,
        [field.key]: {
          kind: field.kind,
          domain,
          resolved: [],
          matches_origin: false,
          route: "unresolved",
          status: "unresolved",
          ready: false,
          message: "Не удалось выполнить проверку адреса",
        },
      }));
    } finally {
      setChecking(null);
    }
  }
  return (
    <>
      <button
        type="button"
        className="networkAddRouteButton"
        onClick={() => setOpen(true)}
      >
        Добавить маршрут
      </button>
      {open && (
        <div
          className="networkModalBackdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <section
            className="networkModal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="network-endpoints-title"
          >
            <header className="networkSectionHeading networkModalHeader">
              <div>
                <p className="eyebrow networkSectionEyebrow">ROUTES</p>
                <h2 id="network-endpoints-title">Настройка внешних адресов</h2>
                <p>
                  Адреса попадут в новые конфигурации клиентов после сохранения. Удаление маршрутов доступно прямо в таблице ROUTES.
                </p>
                {knownCdnDomains.length > 0 && <div className="networkKnownRoutes"><span>Обнаруженные CDN адреса</span><div>{knownCdnDomains.map((domain) => <code key={domain}>{domain}</code>)}</div><small>Активным общим адресом для новых конфигураций остаётся значение в поле CDN / ECH.</small></div>}
              </div>
              <button
                type="button"
                className="networkModalClose"
                onClick={() => setOpen(false)}
                aria-label="Закрыть"
              >
                ×
              </button>
            </header>
            <div className="networkEndpointGrid">
              {fields.map((field) => {
                const result = checks[field.key];
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
                        disabled={busy || checking === field.key || !value}
                      >
                        {checking === field.key ? "Проверяем…" : "Проверить"}
                      </button>
                    </div>
                    <small>{field.help}</small>
                    <small className="networkEndpointDnsHint">DNS: {field.kind === "cdn" ? "A/AAAA на origin VPS; для CDN включите proxy у DNS-провайдера." : field.kind === "tls_relay" ? "A/AAAA на внешний TLS relay; порт протокола должен быть проброшен на relay." : "A/AAAA на внешний UDP relay; нужный UDP-порт должен быть проброшен на relay."}</small>
                    {result && (
                      <em className={`networkEndpointCheck ${result.status}`}>
                        {result.message}
                        {result.resolved.length
                          ? ` · ${result.resolved.join(", ")}`
                          : ""}
                      </em>
                    )}
                    <div className="networkEndpointRouteList">
                      <span>Адреса этого типа</span>
                      {values.slice(1).map((route) => <div key={route}><code>{route}</code></div>)}
                      <button type="button" onClick={() => { const route = window.prompt("Введите домен или IP relay"); if (route?.trim() && !values.includes(route.trim())) onRouteListChange(field.key, [...values, route.trim()]); }}>Добавить адрес</button>
                    </div>
                  </article>
                );
              })}
            </div>
            <div className="networkEndpointGuide">
              <strong>Как это работает</strong>
              <div>
                <b>1 · Внешний сервер</b>
                <span>
                  Настройте reverse proxy/L4 TCP или UDP forwarding на IP
                  панели. Для relay укажите IP или домен relay, не origin VPS.
                </span>
              </div>
              <div>
                <b>2 · Проверка</b>
                <span>
                  Проверка выполняется с VPS панели и не изменяет удалённый
                  сервер.
                </span>
              </div>
              <div>
                <b>3 · Клиенты</b>
                <span>
                  Порт берётся из настроек протокола, а адрес подписки
                  обновляется после сохранения.
                </span>
              </div>
            </div>
            <div className="networkEndpointFooter">
              <p>
                CDN/ECH требует домен. Для отдельного relay достаточно IP:порт,
                но порт самого протокола должен быть открыт на relay.
              </p>
              <button
                type="button"
                className="networkPrimaryButton"
                onClick={onSave}
                disabled={busy || !dirty}
              >
                {busy ? "Сохраняем…" : "Сохранить адреса"}
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
