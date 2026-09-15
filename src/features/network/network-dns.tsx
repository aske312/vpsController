"use client";

import { useState, type Dispatch, type SetStateAction } from "react";
import type {
  DnsCheck,
  DnsSettings,
  DnsStatus,
} from "../../shared/types/control-plane";
import { installedDnsComponents } from "./system-dns-control";
import { recommendDns } from "./dns-recommendation";

type Props = {
  dns: DnsStatus | null;
  dnsDraft: DnsSettings | null;
  dnsChecks: Record<string, DnsCheck>;
  checkingDns: boolean;
  busy: boolean;
  loading: boolean;
  dirty: boolean;
  setDnsDraft: Dispatch<SetStateAction<DnsSettings | null>>;
  checkDnsProviders: () => Promise<void>;
  saveDnsSettings: () => Promise<void>;
};

export function DnsView({
  dns,
  dnsDraft,
  dnsChecks,
  checkingDns,
  busy,
  loading,
  dirty,
  setDnsDraft,
  checkDnsProviders,
  saveDnsSettings,
}: Props) {
  const [showRecommendation, setShowRecommendation] = useState(false);
  const [search, setSearch] = useState("");
  const [onlyDoh, setOnlyDoh] = useState(false);
  const [sortByLatency, setSortByLatency] = useState(false);
  if (!dns || !dnsDraft)
    return (
      <p className="networkEmpty">
        Настройки DNS недоступны. Обновите страницу.
      </p>
    );
  const installedComponents = installedDnsComponents(dns);
  const components = installedComponents.filter(
    (item) => item.id !== "system" && item.key !== "apply_system",
  );
  const hasVless = components.some((item) => item.id === "vless-reality-xhttp");
  const reserve = !dnsDraft.fallback_enabled
    ? "off"
    : dnsDraft.fallback_id || "auto";
  const providers = dns.providers.filter(
    (provider) => provider.id !== "custom",
  );
  const customUsed =
    dnsDraft.selected_id === "custom" ||
    (dnsDraft.fallback_enabled && dnsDraft.fallback_id === "custom");
  const customValid =
    !customUsed ||
    Boolean(
      dnsDraft.custom?.addresses.length &&
      dnsDraft.custom.addresses.every((address) => address.trim()),
    );
  const update = (patch: Partial<DnsSettings>) =>
    setDnsDraft((current) => (current ? { ...current, ...patch } : current));
  const providerFor = (id: string) =>
    id === "custom"
      ? dnsDraft.custom
      : dns.providers.find((item) => item.id === id);
  const encryptedIds = [
    dnsDraft.selected_id,
    ...(dnsDraft.fallback_enabled && dnsDraft.fallback_id
      ? [dnsDraft.fallback_id]
      : []),
  ];
  const encryptionValid =
    !hasVless ||
    !dnsDraft.apply_vrx ||
    !dnsDraft.prefer_encrypted ||
    encryptedIds.every((id) =>
      providerFor(id)?.doh_url?.startsWith("https://"),
    );
  const canSave = customValid && encryptionValid;
  const selectedComponents = components.filter((item) => dnsDraft[item.key]);
  const recommendation = recommendDns(dnsDraft, dns.providers, dnsChecks, {
    udp:
      selectedComponents.some((item) => item.id !== "vless-reality-xhttp") ||
      (hasVless && Boolean(dnsDraft.apply_vrx) && !dnsDraft.prefer_encrypted),
    doh: hasVless && Boolean(dnsDraft.apply_vrx) && dnsDraft.prefer_encrypted,
  });
  const updateCustom = (patch: Partial<NonNullable<DnsSettings["custom"]>>) =>
    setDnsDraft((current) =>
      current
        ? {
            ...current,
            custom: {
              name: "Сторонний DNS",
              addresses: [],
              doh_url: "",
              ...current.custom,
              ...patch,
            },
          }
        : current,
    );
  const latency = (id: string) => {
    const check = dnsChecks[id];
    if (!check) return Infinity;
    return onlyDoh
      ? check.doh_ok
        ? (check.doh_ms ?? Infinity)
        : Infinity
      : check.udp_ok
        ? (check.udp_ms ?? Infinity)
        : Infinity;
  };
  const checkLabel = (id: string) => {
    const check = dnsChecks[id];
    if (!check) return "";
    const format = (ok: boolean, ms: number | null | undefined) =>
      ok ? (ms == null ? "доступен" : `${ms} мс`) : "ошибка";
    return `UDP: ${format(check.udp_ok, check.udp_ms)}${providerFor(id)?.doh_url ? ` · DoH: ${format(check.doh_ok, check.doh_ms)}` : ""}`;
  };
  const selectAll = (enabled: boolean) =>
    update(
      Object.fromEntries(
        components.map((item) => [item.key, enabled]),
      ) as Partial<DnsSettings>,
    );
  return (
    <form
      className="networkDnsForm"
      onSubmit={(event) => {
        event.preventDefault();
        if (dirty && canSave) void saveDnsSettings();
      }}
    >
      <fieldset disabled={busy} className="networkDnsFields">
        <section className="networkDnsEditor" aria-label="Общий профиль DNS">
          <header className="networkSectionHeading">
            <div>
              <h2>Общий профиль DNS</h2>
              <p>
                Основной и резервный резолвер для выбранных компонентов. DNS
                Mihomo настраивается отдельно.
              </p>
            </div>
          </header>
          <div className="networkProviderTools">
            <label className="networkProviderSearch">
              <span className="networkSrOnly">Найти DNS</span>
              <input
                type="search"
                placeholder="Провайдер, IP или фильтрация"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
            <span>{providers.length} профилей DNS</span>
            <button
              type="button"
              disabled={checkingDns || loading || !selectedComponents.length}
              onClick={() => {
                setShowRecommendation(true);
                void checkDnsProviders();
              }}
            >
              {checkingDns && showRecommendation
                ? "Подбираем…"
                : "Подобрать по задержке"}
            </button>
            <button
              type="button"
              disabled={checkingDns || loading}
              onClick={() => void checkDnsProviders()}
            >
              {checkingDns ? "Проверяем с VPS…" : "Проверить доступность"}
            </button>
          </div>
          {showRecommendation && !checkingDns && (
            <div className="networkDnsSuggestion" role="status">
              {recommendation ? (
                <>
                  <div>
                    <strong>
                      {recommendation.primary.name} ·{" "}
                      {recommendation.primary_ms} мс
                      {recommendation.backup
                        ? " / резерв: " + recommendation.backup.name
                        : " / без резерва"}
                    </strong>
                    <p>
                      Измерение с VPS, не пинг VPN. Тип фильтрации и требование
                      DoH сохранены; исключения не меняются. Применение только
                      после сохранения.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      update({
                        selected_id: recommendation.primary.id,
                        fallback_id: recommendation.backup?.id ?? null,
                      });
                      setShowRecommendation(false);
                    }}
                  >
                    Выбрать в черновике
                  </button>
                </>
              ) : (
                <p>
                  Нет подходящего набора с текущей фильтрацией и защитой.
                  Настройки не изменены. Для стороннего DNS используйте ручной
                  выбор.
                </p>
              )}
            </div>
          )}
          <div className="networkResolverWorkspace">
            {(["primary", "fallback"] as const).map((role) => {
              const selectedId =
                role === "primary" ? dnsDraft.selected_id : reserve;
              const choices = [
                ...(role === "fallback"
                  ? [
                      {
                        id: "off",
                        name: "Без резерва",
                        note: "Только основной DNS",
                        code: "OFF",
                        addresses: "",
                        doh: false,
                      },
                      {
                        id: "auto",
                        name: "Тот же провайдер",
                        note: "Второй IP, не отдельный DoH",
                        code: "AUTO",
                        addresses: "",
                        doh: false,
                      },
                    ]
                  : []),
                ...providers.map((provider) => ({
                  id: provider.id,
                  name: provider.name,
                  note: provider.filter,
                  code: provider.country,
                  addresses: provider.addresses.join(", "),
                  doh: Boolean(provider.doh_url),
                })),
                {
                  id: "custom",
                  name: dnsDraft.custom?.name || "Сторонний DNS",
                  note: "Свои адреса и HTTPS URL",
                  code: "DNS",
                  addresses: dnsDraft.custom?.addresses.join(", ") || "",
                  doh: Boolean(dnsDraft.custom?.doh_url),
                },
              ];
              const visible = choices.filter(
                (item) =>
                  item.id === selectedId ||
                  ["off", "auto", "custom"].includes(item.id) ||
                  ((!onlyDoh || item.doh) &&
                    `${item.name} ${item.note} ${item.addresses}`
                      .toLowerCase()
                      .includes(search.toLowerCase().trim())),
              );
              if (sortByLatency)
                visible.sort((a, b) => {
                  const left = latency(a.id),
                    right = latency(b.id);
                  return left === right ? 0 : left < right ? -1 : 1;
                });
              return (
                <section className="networkResolverColumn" key={role}>
                  <header>
                    <div>
                      <small>
                        {role === "primary" ? "ОСНОВНОЙ" : "РЕЗЕРВНЫЙ"}
                      </small>
                      <strong>
                        {choices.find((item) => item.id === selectedId)?.name ||
                          "Не выбран"}
                      </strong>
                    </div>
                    <span className="networkResolverMark">
                      {role === "primary" ? "01" : "02"}
                    </span>
                  </header>
                  <div className="networkProviderGrid">
                    {visible.map((provider) => (
                      <button
                        type="button"
                        key={provider.id}
                        className={
                          selectedId === provider.id
                            ? "networkProvider is-selected"
                            : "networkProvider"
                        }
                        aria-pressed={selectedId === provider.id}
                        onClick={() =>
                          role === "primary"
                            ? update({ selected_id: provider.id })
                            : update({
                                fallback_enabled: provider.id !== "off",
                                fallback_id: ["off", "auto"].includes(
                                  provider.id,
                                )
                                  ? null
                                  : provider.id,
                              })
                        }
                      >
                        <span className="networkProviderCode">
                          {provider.code}
                        </span>
                        <span className="networkProviderText">
                          <strong>{provider.name}</strong>
                          <small>
                            {provider.note}
                            {provider.doh ? " · DoH" : ""}
                          </small>
                          {provider.addresses && (
                            <small className="networkResolverAddresses">
                              {provider.addresses}
                            </small>
                          )}
                          {checkLabel(provider.id) && (
                            <small>{checkLabel(provider.id)}</small>
                          )}
                        </span>
                        <span
                          className="networkProviderIndicator"
                          aria-hidden="true"
                        />
                      </button>
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        </section>
        <section className="networkApplySection">
          <header className="networkSectionHeading">
            <div>
              <h2>Применять к</h2>
            </div>
            <div className="networkBatchActions">
              <button type="button" onClick={() => selectAll(true)}>
                Выбрать все
              </button>
              <button type="button" onClick={() => selectAll(false)}>
                Снять выбор
              </button>
            </div>
          </header>
          <div className="networkApplyGrid">
            {components.map((component) => (
              <button
                type="button"
                role="checkbox"
                aria-checked={Boolean(dnsDraft[component.key])}
                key={component.id}
                className={
                  dnsDraft[component.key]
                    ? "networkProvider is-selected"
                    : "networkProvider"
                }
                onClick={() =>
                  update({ [component.key]: !dnsDraft[component.key] })
                }
              >
                <span className="networkProviderCode">{component.code}</span>
                <span className="networkProviderText">
                  <strong>{component.title}</strong>
                  <small>
                    DNS: {dns.protocol_effect_details?.[component.id]?.value || "undefined"}
                  </small>
                </span>
                <span className="networkProviderIndicator" aria-hidden="true" />
              </button>
            ))}
          </div>
        </section>
        {!encryptionValid && (
          <p className="networkValidation" role="alert">
            Основной и резервный DNS Vless должны поддерживать HTTPS. Выберите
            DoH-провайдеров или измените настройку в дополнительных функциях.
          </p>
        )}
        {customUsed && (
          <section className="networkPanel networkCustom">
            <header className="networkSectionHeading">
              <div>
                <h2>Сторонний DNS</h2>
              </div>
            </header>
            <div className="networkCustomFields">
              <label>
                Название
                <input
                  value={dnsDraft.custom?.name || ""}
                  placeholder="Мой DNS"
                  onChange={(event) =>
                    updateCustom({ name: event.target.value })
                  }
                />
              </label>
              <label>
                IP-адреса через запятую
                <input
                  required
                  value={dnsDraft.custom?.addresses.join(",") || ""}
                  placeholder="1.1.1.1,1.0.0.1"
                  onChange={(event) =>
                    updateCustom({ addresses: event.target.value.split(",") })
                  }
                />
              </label>
              <label>
                DoH URL
                <input
                  type="url"
                  value={dnsDraft.custom?.doh_url || ""}
                  placeholder="https://dns.example/dns-query"
                  onChange={(event) =>
                    updateCustom({ doh_url: event.target.value })
                  }
                />
              </label>
            </div>
            {!customValid && (
              <p className="networkValidation">
                Укажите IP-адреса без пустых значений.
              </p>
            )}
          </section>
        )}
        <details className="networkAdditional">
          <summary>
            <span>Дополнительные функции</span>
            <small>Фильтры выбора и защита запросов</small>
          </summary>
          <div className="networkAdditionalBody">
            <section>
              <h3>Подбор резолвера</h3>
              <div className="networkUtilityOptions">
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={onlyDoh}
                  className={`networkOptionRow ${onlyDoh ? "is-selected" : ""}`}
                  onClick={() => setOnlyDoh(!onlyDoh)}
                >
                  <span>
                    <strong>Показывать только DNS с DoH</strong>
                    <small>Оставить только зашифрованные резолверы</small>
                  </span>
                  <span className="networkOptionMark" aria-hidden="true">
                    {onlyDoh ? "✓" : ""}
                  </span>
                </button>
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={sortByLatency}
                  className={`networkOptionRow ${sortByLatency ? "is-selected" : ""}`}
                  onClick={() => setSortByLatency(!sortByLatency)}
                >
                  <span>
                    <strong>Сортировать по измеренной задержке</strong>
                    <small>Порядок меняется только в этом списке</small>
                  </span>
                  <span className="networkOptionMark" aria-hidden="true">
                    {sortByLatency ? "✓" : ""}
                  </span>
                </button>
              </div>
              <p>
                Фильтры не меняют сохранённый выбор. Задержка измеряется с VPS.
              </p>
            </section>
            {hasVless && (
              <section>
                <h3>Защита запросов VLESS</h3>
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={dnsDraft.prefer_encrypted}
                  className={`networkOptionRow ${dnsDraft.prefer_encrypted ? "is-selected" : ""}`}
                  onClick={() =>
                    update({ prefer_encrypted: !dnsDraft.prefer_encrypted })
                  }
                >
                  <span>
                    <strong>Только DoH для основного DNS и резерва</strong>
                    <small>Не переходить на обычный DNS при ошибке DoH</small>
                  </span>
                  <span className="networkOptionMark" aria-hidden="true">
                    {dnsDraft.prefer_encrypted ? "✓" : ""}
                  </span>
                </button>
                <p>
                  Bootstrap используется только для разрешения имён
                  DoH-серверов.
                </p>
                {dnsDraft.prefer_encrypted && (
                  <label className="networkBootstrapField">
                    <span>Bootstrap DNS</span>
                    <select
                      value={dnsDraft.bootstrap_id || "cloudflare"}
                      onChange={(event) =>
                        update({
                          bootstrap_id: event.target
                            .value as DnsSettings["bootstrap_id"],
                        })
                      }
                    >
                      <option value="cloudflare">Cloudflare · DoH по IP</option>
                      <option value="google">Google · DoH по IP</option>
                      <option value="dns-sb">DNS.SB · DoH по IP</option>
                    </select>
                    <small>
                      Запросы сайтов не используют bootstrap как резерв.
                    </small>
                  </label>
                )}
              </section>
            )}

          </div>
        </details>
      </fieldset>
      <footer className={`networkSaveBar ${dirty ? "dirty" : ""}`}>
        <div role="status">
          <strong>
            {busy
              ? "Применяем…"
              : dirty
                ? "Есть несохранённые изменения"
                : "Настройки сохранены"}
          </strong>
          <small>Изменения применяются только к выбранным компонентам.</small>
        </div>
        <div className="networkSaveActions">
          <button
            type="button"
            disabled={!dirty || busy || loading}
            onClick={() => setDnsDraft(dns.settings)}
          >
            Сбросить
          </button>
          <button
            type="submit"
            className="networkPrimaryButton"
            disabled={!dirty || busy || loading || !canSave}
          >
            {busy ? "Применяем…" : "Применить настройки"}
          </button>
        </div>
      </footer>
    </form>
  );
}
