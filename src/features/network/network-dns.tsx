"use client";

import { type Dispatch, type SetStateAction } from "react";
import type { DnsCheck, DnsSettings, DnsStatus } from "../../shared/types/control-plane";
import { SystemDnsControl, dnsComponents } from "./system-dns-control";

type Props = { dns: DnsStatus | null; dnsDraft: DnsSettings | null; dnsChecks: Record<string, DnsCheck>; checkingDns: boolean; busy: boolean; loading: boolean; dirty: boolean; setDnsDraft: Dispatch<SetStateAction<DnsSettings | null>>; checkDnsProviders: () => Promise<void>; saveDnsSettings: () => Promise<void> };

export function DnsView({ dns, dnsDraft, dnsChecks, checkingDns, busy, loading, dirty, setDnsDraft, checkDnsProviders, saveDnsSettings }: Props) {
  if (!dns || !dnsDraft) return <p className="networkEmpty">Настройки DNS недоступны. Обновите страницу.</p>;
  const reserve = !dnsDraft.fallback_enabled ? "off" : dnsDraft.fallback_id || "auto";
  const providers = dns.providers.filter((provider) => provider.id !== "custom");
  const customUsed = dnsDraft.selected_id === "custom" || Object.values(dnsDraft.profiles || {}).includes("custom") || (dnsDraft.fallback_enabled && dnsDraft.fallback_id === "custom");
  const customValid = !customUsed || Boolean(dnsDraft.custom?.addresses.length && dnsDraft.custom.addresses.every((address) => address.trim()));
  const update = (patch: Partial<DnsSettings>) => setDnsDraft((current) => current ? { ...current, ...patch } : current);
  const chooseException = (scope: string, id: string) => setDnsDraft((current) => {
    if (!current) return current;
    const profiles = { ...current.profiles };
    if (id) profiles[scope] = id;
    else delete profiles[scope];
    return { ...current, profiles };
  });
  const providerFor = (id: string) => id === "custom" ? dnsDraft.custom : dns.providers.find((item) => item.id === id);
  const encryptedIds = [dnsDraft.profiles?.["vless-reality-xhttp"] || dnsDraft.selected_id, ...(dnsDraft.fallback_enabled && dnsDraft.fallback_id ? [dnsDraft.fallback_id] : [])];
  const encryptionValid = !dnsDraft.apply_vrx || !dnsDraft.prefer_encrypted || encryptedIds.every((id) => providerFor(id)?.doh_url?.startsWith("https://"));
  const canSave = customValid && encryptionValid;
  const exceptionCount = Object.keys(dnsDraft.profiles || {}).length;
  const updateCustom = (patch: Partial<NonNullable<DnsSettings["custom"]>>) => setDnsDraft((current) => current ? { ...current, custom: { name: "Сторонний DNS", addresses: [], doh_url: "", ...current.custom, ...patch } } : current);
  const options = <>{providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}<option value="custom">{dnsDraft.custom?.name || "Сторонний DNS"}</option></>;
  const probeLabel = (check: DnsCheck | undefined, transport: "udp" | "doh") => !check ? "Не проверен" : !check[`${transport}_ok`] ? "Ошибка" : check[`${transport}_ms`] != null ? `${check[`${transport}_ms`]} мс` : "Доступен";
  return <form className="networkDnsForm" onSubmit={(event) => { event.preventDefault(); if (dirty && canSave) void saveDnsSettings(); }}>
    <fieldset disabled={busy} className="networkDnsFields">
      <section className="networkDnsEditor" aria-label="Общий профиль DNS">
        <header className="networkSectionHeading"><div><h2>Общий профиль DNS</h2><p>Для системы и прямых протоколов. Выберите провайдеров; индивидуальные профили доступны в исключениях.</p></div></header>
        <div className="networkResolverWorkspace">
          {(["primary", "fallback"] as const).map((role) => {
            const selectedId = role === "primary" ? dnsDraft.selected_id : reserve;
            const choices = [
              ...(role === "fallback" ? [{ id: "off", name: "Без резерва", note: "Только основной DNS", code: "OFF" }, { id: "auto", name: "Тот же провайдер", note: "Второй IP, не отдельный DoH", code: "AUTO" }] : []),
              ...providers.map((provider) => ({ id: provider.id, name: provider.name, note: provider.filter, code: provider.country })),
              { id: "custom", name: dnsDraft.custom?.name || "Сторонний DNS", note: "Свои адреса и HTTPS URL", code: "DNS" },
            ];
            return <section className="networkResolverColumn" key={role}>
              <header><div><small>{role === "primary" ? "ОСНОВНОЙ" : "РЕЗЕРВНЫЙ"}</small><strong>{choices.find((item) => item.id === selectedId)?.name || "Не выбран"}</strong></div><span className="networkResolverMark">{role === "primary" ? "01" : "02"}</span></header>
              <div className="networkProviderGrid">{choices.map((provider) => <button type="button" key={provider.id} className={selectedId === provider.id ? "networkProvider is-selected" : "networkProvider"} aria-pressed={selectedId === provider.id} onClick={() => role === "primary" ? update({ selected_id: provider.id }) : update({ fallback_enabled: provider.id !== "off", fallback_id: ["off", "auto"].includes(provider.id) ? null : provider.id })}><span className="networkProviderCode">{provider.code}</span><span className="networkProviderText"><strong>{provider.name}</strong><small>{provider.note}</small></span><span className="networkProviderIndicator" aria-hidden="true" /></button>)}</div>
            </section>;
          })}
        </div>
        <div className="networkEditorOptions"><span className="networkApplyLabel">Применять к</span>
          {dnsComponents.map((component) => {
            const available = component.id === "system" || Boolean(dns.protocol_effect_details?.[component.id]?.installed);
            return <label className="networkSwitch" key={component.id} title={available ? component.hint : "Протокол не установлен"}><input type="checkbox" disabled={!available} checked={available && Boolean(dnsDraft[component.key])} onChange={(event) => update({ [component.key]: event.target.checked })} /><span>{component.title}</span></label>;
          })}
          <small>Управление применением только здесь. Hysteria2, TUIC и Trojan используют DNS системы. Shadowsocks: сервер использует DNS системы, отдельное значение является рекомендацией клиенту.</small>
        </div>
      </section>
      <details className="networkCatalog networkExceptions">
        <summary><span>Исключения по компонентам</span><small>{exceptionCount ? "Индивидуальных профилей: " + exceptionCount : "Все наследуют общий профиль"}</small></summary>
        <div className="networkExceptionList">{dnsComponents.map((component) => {
          const available = component.id === "system" || Boolean(dns.protocol_effect_details?.[component.id]?.installed);
          return <label className="networkExceptionRow" key={component.id}><span><strong>{component.title}</strong><small>{component.hint}</small></span><select disabled={!available || !dnsDraft[component.key]} value={dnsDraft.profiles?.[component.id] || ""} onChange={(event) => chooseException(component.id, event.target.value)}><option value="">Общий профиль</option>{options}</select></label>;
        })}</div>
        <p className="networkPolicyNote">Исключение заменяет только основной DNS компонента. Резерв остаётся общим; автоматический резерв берётся у выбранного для компонента провайдера.</p>
      </details>
      <details className="networkDnsProtection"><summary>Защита DNS и ограничения</summary>
        <label className="networkSwitch"><input type="checkbox" checked={dnsDraft.prefer_encrypted} onChange={(event) => update({ prefer_encrypted: event.target.checked })} /><span>Зашифрованный DNS для VLESS: только DoH, включая резерв</span></label>
        <p>При отказе DoH обычный DNS для запрашиваемых сайтов не подставляется. Имя самого DoH-сервера первоначально разрешается системным DNS. Для независимого DoH-резерва выберите другого провайдера: второй IP того же провайдера не создаёт второй DoH-адрес.</p>
        {!encryptionValid && <p className="networkValidation" role="alert">У основного или резервного DNS VLESS нет HTTPS-адреса. Выберите провайдера с DoH либо укажите HTTPS URL стороннего DNS.</p>}
        <small>Это не глобальное шифрование DNS: VPS использует системный резолвер. WG/AWG, OpenVPN и IKEv2 передают DNS через VPN при соблюдении маршрутов клиентом; участок VPS → DNS не шифруется этой настройкой. Защита от утечек при отключении VPN требует настроек клиента.</small>
      </details>
      {customUsed && <section className="networkPanel networkCustom"><header className="networkSectionHeading"><div><h2>Сторонний DNS</h2><p>Один пользовательский профиль для основного или резервного DNS.</p></div></header><div className="networkCustomFields"><label>Название<input value={dnsDraft.custom?.name || ""} placeholder="Мой DNS" onChange={(event) => updateCustom({ name: event.target.value })} /></label><label>IP-адреса через запятую<input required value={dnsDraft.custom?.addresses.join(",") || ""} placeholder="1.1.1.1,1.0.0.1" onChange={(event) => updateCustom({ addresses: event.target.value.split(",") })} /></label><label>DoH URL, необязательно<input type="url" value={dnsDraft.custom?.doh_url || ""} placeholder="https://dns.example/dns-query" onChange={(event) => updateCustom({ doh_url: event.target.value })} /></label></div>{!customValid && <p className="networkValidation">Укажите IP-адреса без пустых значений.</p>}</section>}
      <SystemDnsControl dns={dns} />
      <details className="networkCatalog"><summary><span>Доступные DNS</span><small>{providers.length} провайдеров · адреса и проверка доступности</small></summary><div className="networkCatalogToolbar"><p>Проверка выполняется с VPS.</p><button type="button" disabled={checkingDns || loading} onClick={() => void checkDnsProviders()}>{checkingDns ? "Проверяем…" : "Проверить все"}</button></div><div className="networkTableWrap"><table><thead><tr><th>Провайдер</th><th>Адреса</th><th>Фильтрация</th><th>UDP</th><th>DoH</th></tr></thead><tbody>{providers.map((provider) => <tr key={provider.id}><td><strong>{provider.name}</strong><small>{provider.country}</small></td><td><code>{provider.addresses.join(", ")}</code></td><td>{provider.filter}</td><td>{probeLabel(dnsChecks[provider.id], "udp")}</td><td>{provider.doh_url ? probeLabel(dnsChecks[provider.id], "doh") : "Нет"}</td></tr>)}</tbody></table></div></details>
    </fieldset>
    <p className="networkImpact">VLESS перезапустит Xray, OpenVPN перезапустит службу и прервёт подключения, IKEv2 применит DNS при переподключении. WG/AWG получат DNS только в новых конфигурациях. Порядок использования резерва зависит от компонента. DNS Mihomo настраивается только в его разделе; DNS-записи доменов здесь не изменяются.</p>
    <footer className={`networkSaveBar ${dirty ? "dirty" : ""}`}><div role="status"><strong>{busy ? "Применяем…" : dirty ? "Есть несохранённые изменения" : "Настройки сохранены"}</strong><small>Изменения применяются только к отмеченным компонентам.</small></div><div className="networkSaveActions"><button type="button" disabled={!dirty || busy || loading} onClick={() => setDnsDraft(dns.settings)}>Сбросить</button><button type="submit" className="networkPrimaryButton" disabled={!dirty || busy || loading || !canSave}>{busy ? "Применяем…" : "Применить настройки"}</button></div></footer>
  </form>;
}
