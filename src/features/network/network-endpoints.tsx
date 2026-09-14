"use client";

import { useState } from "react";
import type { NetworkEndpointCheck, NetworkEndpointSettings } from "../../shared/types/control-plane";
import { checkNetworkEndpoint, type NetworkRequest } from "./network-api";

export function NetworkEndpoints({
  request, draft, busy, dirty, onChange, onSave,
}: {
  request: NetworkRequest;
  draft: NetworkEndpointSettings;
  busy: boolean;
  dirty: boolean;
  onChange: (key: keyof NetworkEndpointSettings, value: string) => void;
  onSave: () => void;
}) {
  const [checking, setChecking] = useState<string | null>(null);
  const [checks, setChecks] = useState<Partial<Record<keyof NetworkEndpointSettings, NetworkEndpointCheck>>>({});
  const fields: Array<{ key: keyof NetworkEndpointSettings; kind: NetworkEndpointCheck["kind"]; label: string; short: string; placeholder: string; help: string }> = [
    { key: "cdn_domain", kind: "cdn", label: "CDN / ECH", short: "Скрывает origin", placeholder: "cdn.example.com", help: "DNS-имя CDN. Для ECH нужен домен; IP здесь не подойдёт." },
    { key: "tls_relay_domain", kind: "tls_relay", label: "TLS relay", short: "TCP через внешний сервер", placeholder: "relay.example.com или 203.0.113.10", help: "Домен или IP внешнего relay. Порт берётся из выбранного протокола." },
    { key: "udp_relay_domain", kind: "udp_relay", label: "UDP relay", short: "UDP через внешний сервер", placeholder: "relay.example.com или 203.0.113.10", help: "Домен или IP внешнего relay. Сам relay должен пересылать нужный UDP-порт." },
  ];
  async function check(field: typeof fields[number]) {
    const domain = draft[field.key].trim();
    if (!domain) return;
    setChecking(field.key);
    try { const result = await checkNetworkEndpoint(request, field.kind, domain); setChecks((current) => ({ ...current, [field.key]: result })); }
    catch { setChecks((current) => ({ ...current, [field.key]: { kind: field.kind, domain, resolved: [], matches_origin: false, route: "unresolved", status: "unresolved", ready: false, message: "Не удалось выполнить проверку домена" } })); }
    finally { setChecking(null); }
  }
  return <section className="networkPanel networkEndpointPanel" aria-label="Внешние адреса">
    <header className="networkSectionHeading"><div><span className="networkKicker">ВНЕШНИЕ АДРЕСА</span><h2>Куда направлять подключения</h2><p>Укажите адреса внешних точек входа. После сохранения они появятся в новых конфигурациях клиентов.</p></div><span className={`networkEndpointStatus ${dirty ? "is-dirty" : ""}`}>{dirty ? "Есть изменения" : "Сохранено"}</span></header>
    <div className="networkEndpointGrid">
      {fields.map((field) => { const result = checks[field.key]; const value = draft[field.key].trim(); return <article className={`networkEndpointCard ${result?.status || ""}`} key={field.key}><div className="networkEndpointCardHead"><div><h3>{field.label}</h3><span>{field.short}</span></div><span className="networkEndpointState">{result ? result.status === "ready" ? "Готово" : result.status === "warning" ? "Проверить" : "Ошибка" : value ? "Не проверен" : "Не задан"}</span></div><div className="networkEndpointInput"><input aria-label={field.label} type="text" value={draft[field.key]} onChange={(event) => { setChecks((current) => { const next = { ...current }; delete next[field.key]; return next; }); onChange(field.key, event.target.value); }} placeholder={field.placeholder} autoComplete="off" /><button type="button" onClick={() => void check(field)} disabled={busy || checking === field.key || !value}>{checking === field.key ? "Проверяем…" : "Проверить"}</button></div><small>{field.help}</small>{result && <em className={`networkEndpointCheck ${result.status}`}>{result.message}{result.resolved.length ? ` · ${result.resolved.join(", ")}` : ""}</em>}</article>; })}
    </div>
    <div className="networkEndpointGuide"><strong>Как это работает</strong><div><b>1 · Внешний сервер</b><span>Настройте reverse proxy/L4 TCP или UDP forwarding на IP панели. Для relay укажите его IP или домен, не origin VPS.</span></div><div><b>2 · Эта страница</b><span>Сохраните адрес и проверьте DNS. Проверка выполняется с VPS панели и не перенастраивает удалённый сервер.</span></div><div><b>3 · Протокол</b><span>В карточке протокола выберите relay-режим. Порт берётся из протокола; адрес подписки обновится после обновления клиентом.</span></div></div>
    <div className="networkEndpointFooter"><p>CDN/ECH требует домен. Для отдельного relay достаточно IP:порт, но текущий профиль сохраняет порт самого протокола.</p><button type="button" className="networkPrimaryButton" onClick={onSave} disabled={busy || !dirty}>{busy ? "Сохраняем…" : "Сохранить адреса"}</button></div>
  </section>;
}
