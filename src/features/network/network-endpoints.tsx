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
  const fields: Array<{ key: keyof NetworkEndpointSettings; kind: NetworkEndpointCheck["kind"]; label: string; placeholder: string; help: string }> = [
    { key: "cdn_domain", kind: "cdn", label: "CDN / ECH домен", placeholder: "cdn.example.com", help: "Для CDN/ECH. Если VLESS установлен на VPS панели, origin-маршрут подключится автоматически." },
    { key: "tls_relay_domain", kind: "tls_relay", label: "TLS relay домен", placeholder: "tls-relay.example.com", help: "Для TCP/TLS relay; порт исходного протокола сохраняется." },
    { key: "udp_relay_domain", kind: "udp_relay", label: "UDP relay домен", placeholder: "udp-relay.example.com", help: "Для UDP relay; порт исходного протокола сохраняется." },
  ];
  async function check(field: typeof fields[number]) {
    const domain = draft[field.key].trim();
    if (!domain) return;
    setChecking(field.key);
    try { const result = await checkNetworkEndpoint(request, field.kind, domain); setChecks((current) => ({ ...current, [field.key]: result })); }
    catch { setChecks((current) => ({ ...current, [field.key]: { kind: field.kind, domain, resolved: [], matches_origin: false, route: "unresolved", status: "unresolved", ready: false, message: "Не удалось выполнить проверку домена" } })); }
    finally { setChecking(null); }
  }
  return <section className="networkPanel networkEndpointPanel" aria-label="Домены защищённых каналов">
    <header className="networkSectionHeading"><div><h2>Домены защищённых каналов</h2><p>Общие точки входа для будущих CDN/ECH и relay-маршрутов.</p></div><span className="networkEndpointStatus">{dirty ? "Есть изменения" : "Сохранено"}</span></header>
    <div className="networkEndpointGrid">
      {fields.map((field) => { const result = checks[field.key]; return <label key={field.key}><span>{field.label}</span><div className="networkEndpointInput"><input type="text" value={draft[field.key]} onChange={(event) => { setChecks((current) => { const next = { ...current }; delete next[field.key]; return next; }); onChange(field.key, event.target.value); }} placeholder={field.placeholder} autoComplete="off" /><button type="button" onClick={() => void check(field)} disabled={busy || checking === field.key || !draft[field.key].trim()}>{checking === field.key ? "Проверяем…" : "Проверить"}</button></div><small>{field.help}</small>{result && <em className={`networkEndpointCheck ${result.status}`}>{result.message}{result.resolved.length ? ` · ${result.resolved.join(", ")}` : ""}</em>}</label>; })}
    </div>
    <div className="networkEndpointGuide"><strong>Что настроить на двух сторонах</strong><div><b>DNS</b><span>A/AAAA или CNAME должны вести на CDN либо на внешний VPS домена/relay. Для relay не указывайте IP VPS панели.</span></div><div><b>Сервер домена</b><span>Настройте reverse proxy/L4 TCP relay или UDP forwarding на IP панели и нужные порты протоколов.</span></div><div><b>VPS панели</b><span>Для CDN origin-маршрут VLESS подключается автоматически. Для relay после проверки нажмите «Сохранить и подключить», затем выберите relay-режим у протокола.</span></div></div>
    <div className="networkEndpointFooter"><p>Проверка выполняется с VPS панели и подтверждает DNS-маршрут. Удалённый VPS домена приложение без его доступа не перенастраивает.</p><button type="button" className="networkPrimaryButton" onClick={onSave} disabled={busy || !dirty}>{busy ? "Сохраняем…" : "Сохранить и подключить"}</button></div>
  </section>;
}
