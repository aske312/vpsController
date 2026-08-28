"use client";

import { formatModuleVersion } from "../../lib/format-version";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

type View = "overview" | "profiles" | "channels" | "dns" | "routing";

type ConfirmOptions = {
  title: string;
  message: string;
  confirmLabel: string;
  phrase?: string;
  danger?: boolean;
};

type Status = {
  active: boolean;
  core_version: string;
  profiles: number;
  profiles_in_use: number;
  credentials: number;
  channels_in_use: string[];
  channels_installed: number;
  modules_installed: number;
  modules_total: number;
  endpoint: string;
};

type SettingField = {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "textarea" | "boolean";
  default: string | number | boolean;
  min?: number;
  max?: number;
  options?: Array<string | { value: string; label: string }>;
  help?: string;
};

type Module = {
  id: string;
  name: string;
  description: string;
  category: "transport" | "dns" | "routing";
  category_name: string;
  installed: boolean;
  installable?: boolean;
  active: boolean;
  service?: string;
  settings?: SettingField[];
  connection_settings?: SettingField[];
  settings_values: Record<string, string | number | boolean>;
  installed_version?: string;
  available_version?: string;
  update_available?: boolean;
  update_breaking?: boolean;
};

type Profile = {
  id: string;
  name: string;
  channels: string[];
  connections: ProfileConnection[];
  created_at: string;
  updated_at: string;
};

type ProfileConnection = {
  id: string;
  component: string;
  name: string;
  settings: Record<string, string | number | boolean>;
};

type PolicySettings = {
  schema: SettingField[];
  values: Record<string, string | number>;
};

const channelShort: Record<string, string> = {
  "transport-awg": "AW",
  "transport-wg": "WG",
  "transport-reality": "VL",
  "transport-shadowsocks": "SS",
};

const MIHOMO_OPERATION_EVENT = "gate312:mihomo-operation";

function publishMihomoOperation(id: string, label: string, state: "running" | "success" | "error", message?: string) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(MIHOMO_OPERATION_EVENT, { detail: { id, label, state, message } }));
}

export function MihomoPage({
  token,
  confirmAction,
  coreBusy,
  onRemoveCore,
}: {
  token: string;
  confirmAction: (options: ConfirmOptions) => Promise<boolean>;
  coreBusy: boolean;
  onRemoveCore: () => Promise<void>;
}) {
  const [view, setView] = useState<View>("overview");
  const [status, setStatus] = useState<Status | null>(null);
  const [modules, setModules] = useState<Module[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [dnsPolicy, setDnsPolicy] = useState<PolicySettings | null>(null);
  const [routingPolicy, setRoutingPolicy] = useState<PolicySettings | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Module | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string | number | boolean>>({});
  const [profileDialog, setProfileDialog] = useState<Profile | "new" | null>(null);
  const [profileName, setProfileName] = useState("");
  const [profileConnections, setProfileConnections] = useState<ProfileConnection[]>([]);

  const request = useCallback(async (path: string, init?: RequestInit) => {
    if (!token) throw new Error("Сессия панели завершена. Войдите заново.");
    const response = await fetch(`/api${path}`, {
      ...init,
      headers: {
        Authorization: `Basic ${token}`,
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(init?.headers || {}),
      },
    });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        message = body?.detail || body?.message || message;
      } catch {
        // Preserve status text for non-JSON responses.
      }
      throw new Error(message);
    }
    if ((response.headers.get("content-type") || "").includes("text/plain")) {
      return response.text();
    }
    return response.status === 204 ? null : response.json();
  }, [token]);

  const refresh = useCallback(async () => {
    setError("");
    try {
      const [nextStatus, nextModules, nextProfiles, nextDns, nextRouting] = await Promise.all([
        request("/mihomo/status"),
        request("/mihomo/modules"),
        request("/mihomo/profiles"),
        request("/mihomo/dns/settings"),
        request("/mihomo/routing/schema"),
      ]);
      setStatus(nextStatus as Status);
      setModules((nextModules as { items: Module[] }).items || []);
      setProfiles((nextProfiles as { items: Profile[] }).items || []);
      setDnsPolicy(nextDns as PolicySettings);
      setRoutingPolicy(nextRouting as PolicySettings);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить Mihomo Manager");
    }
  }, [request]);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [refresh]);

  async function requestCoreRemoval() {
    setError("");
    let current: Status;
    try {
      current = (await request("/mihomo/status")) as Status;
      setStatus(current);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? `Не удалось проверить зависимости Mihomo перед удалением: ${cause.message}`
          : "Не удалось проверить зависимости Mihomo перед удалением",
      );
      return;
    }

    const usedProfiles = current.profiles_in_use || 0;
    const credentials = current.credentials || 0;
    if (usedProfiles > 0 || credentials > 0) {
      const channels = (current.channels_in_use || [])
        .map((id) => channelShort[id] || id)
        .join(", ");
      const confirmed = await confirmAction({
        title: "Mihomo используется подключениями",
        message:
          `Найдено профилей с подключениями: ${usedProfiles}. Credentials: ${credentials}.` +
          (channels ? ` Используемые компоненты: ${channels}.` : "") +
          " Удаление каскадно отзовёт эти credentials, удалит профили, компоненты, DNS и маршрутизацию Mihomo.",
        confirmLabel: "Продолжить удаление",
        phrase: "УДАЛИТЬ MIHOMO",
        danger: true,
      });
      if (!confirmed) return;
    }

    await onRemoveCore();
  }

  async function toggleModule(module: Module) {
    const install = !module.installed;
    const confirmed = await confirmAction({
      title: `${install ? "Установить" : "Удалить"} ${module.name}?`,
      message:
        install
          ? "Будет установлено ядро протокола Mihomo. С первым компонентом активируются базовые DNS и маршрутизация."
          : "Будет удалён только компонент Mihomo. Прямые подключения GATE.312 не изменяются.",
      confirmLabel: install ? "Установить" : "Удалить модуль",
      danger: !install,
    });
    if (!confirmed) return;
    const operationId = `module:${module.id}`;
    const operationLabel = `${install ? "Установка" : "Удаление"} ${module.name}`;
    publishMihomoOperation(operationId, operationLabel, "running", "Mihomo Manager применяет изменение…");
    setBusy(module.id);
    setError("");
    setNotice("");
    try {
      await request(`/mihomo/modules/${module.id}${install ? "/install" : ""}`, {
        method: install ? "POST" : "DELETE",
      });
      await refresh();
      setNotice(`${module.name}: ${install ? "установлен; DNS и маршрутизация Mihomo готовы" : "удалён из Mihomo"}.`);
      publishMihomoOperation(operationId, operationLabel, "success", "Изменение применено");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Операция Mihomo не выполнена";
      setError(message);
      publishMihomoOperation(operationId, operationLabel, "error", message);
    } finally {
      setBusy("");
    }
  }

  async function updateModule(module: Module) {
    const confirmed = await confirmAction({
      title: `Обновить ${module.name}?`,
      message: module.update_breaking
        ? `Доступна версия ${module.available_version} (сейчас ${module.installed_version || "—"}). Это смена старшей версии протокола — после обновления может понадобиться пересоздать профили или подключения, использующие этот канал.`
        : module.id === "transport-awg"
          ? "В репозитории Amnezia доступна новая сборка AWG. Пакеты будут обновлены без изменения конфигурации канала."
        : `Доступна версия ${module.available_version} (сейчас ${module.installed_version || "—"}). Активный канал и его подключения не будут разорваны.`,
      confirmLabel: "Обновить",
      danger: module.update_breaking,
    });
    if (!confirmed) return;
    const operationId = `module-update:${module.id}`;
    const operationLabel = `Обновление ${module.name}`;
    publishMihomoOperation(operationId, operationLabel, "running", "Mihomo Manager применяет обновление…");
    setBusy(`update:${module.id}`);
    setError("");
    setNotice("");
    try {
      await request(`/mihomo/modules/${module.id}/update`, { method: "POST" });
      await refresh();
      setNotice(`${module.name}: обновлён до ${module.available_version}.`);
      publishMihomoOperation(operationId, operationLabel, "success", "Обновление применено");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Обновление Mihomo-модуля не выполнено";
      setError(message);
      publishMihomoOperation(operationId, operationLabel, "error", message);
    } finally {
      setBusy("");
    }
  }

  function openSettings(module: Module) {
    setEditing(module);
    setSettingsDraft({ ...module.settings_values });
  }

  async function openPolicySettings(kind: "dns" | "routing") {
    let policy = kind === "dns" ? dnsPolicy : routingPolicy;
    setError("");
    if (!policy) {
      const operationId = `policy:${kind}`;
      setBusy(operationId);
      try {
        policy = await request(kind === "dns" ? "/mihomo/dns/settings" : "/mihomo/routing/schema") as PolicySettings;
        if (kind === "dns") setDnsPolicy(policy);
        else setRoutingPolicy(policy);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : `Не удалось загрузить настройки ${kind === "dns" ? "DNS" : "маршрутизации"} Mihomo`);
        return;
      } finally {
        setBusy("");
      }
    }
    openSettings({
      id: kind === "dns" ? "dns-private" : "routing-policy",
      name: kind === "dns" ? "DNS Mihomo" : "Маршрутизация Mihomo",
      description: kind === "dns"
        ? "Базовая DNS-политика применяется ко всем конфигурациям Mihomo."
        : "Базовая стратегия маршрутизации применяется ко всем профилям без собственных переопределений.",
      category: kind === "dns" ? "dns" : "routing",
      category_name: kind === "dns" ? "DNS" : "Маршрутизация",
      installed: installedChannels.length > 0,
      active: installedChannels.length > 0,
      settings: policy.schema,
      settings_values: policy.values,
    });
  }

  async function saveSettings(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const operationId = `settings:${editing.id}`;
    const operationLabel = `Настройки ${editing.name}`;
    publishMihomoOperation(operationId, operationLabel, "running", "Сохраняем настройки внутреннего модуля…");
    setBusy(operationId);
    setError("");
    try {
      const settingsPath = editing.category === "dns"
        ? "/mihomo/dns/settings"
        : editing.category === "routing"
          ? "/mihomo/routing/settings"
          : `/mihomo/modules/${editing.id}/settings`;
      await request(settingsPath, {
        method: "PATCH",
        body: JSON.stringify({ values: settingsDraft }),
      });
      const editedName = editing.name;
      setEditing(null);
      await refresh();
      setNotice(`Настройки ${editedName} сохранены.`);
      publishMihomoOperation(operationId, operationLabel, "success", "Настройки сохранены");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Настройки не сохранены";
      setError(message);
      publishMihomoOperation(operationId, operationLabel, "error", message);
    } finally {
      setBusy("");
    }
  }

  function newProfile() {
    setProfileDialog("new");
    setProfileName("");
    setProfileConnections([]);
  }

  function editProfile(profile: Profile) {
    setProfileDialog(profile);
    setProfileName(profile.name);
    setProfileConnections((profile.connections || []).map((connection) => ({ ...connection, settings: { ...connection.settings } })));
  }

  function addProfileConnection(module: Module) {
    const settings = Object.fromEntries((module.connection_settings || []).map((field) => [field.key, field.default]));
    setProfileConnections((current) => [...current, {
      id: `connection-${crypto.randomUUID()}`,
      component: module.id,
      name: module.name,
      settings,
    }]);
  }

  function updateProfileConnection(id: string, patch: Partial<ProfileConnection>) {
    setProfileConnections((current) => current.map((connection) => connection.id === id ? { ...connection, ...patch } : connection));
  }

  function updateConnectionSetting(id: string, key: string, value: string | number | boolean) {
    setProfileConnections((current) => current.map((connection) => connection.id === id
      ? { ...connection, settings: { ...connection.settings, [key]: value } }
      : connection));
  }

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    const creating = profileDialog === "new";
    const operationId = creating ? "profile:new" : `profile:${profileDialog && profileDialog !== "new" ? profileDialog.id : "edit"}`;
    const operationLabel = creating ? "Создание Mihomo-профиля" : `Сохранение профиля ${profileName}`;
    publishMihomoOperation(operationId, operationLabel, "running", "Обновляем профиль и credentials…");
    setBusy("profile");
    setError("");
    try {
      if (profileDialog === "new") {
        await request("/mihomo/profiles", {
          method: "POST",
          body: JSON.stringify({ name: profileName, connections: profileConnections }),
        });
      } else if (profileDialog) {
        await request(`/mihomo/profiles/${profileDialog.id}`, {
          method: "PATCH",
          body: JSON.stringify({ name: profileName, connections: profileConnections }),
        });
      }
      setProfileDialog(null);
      await refresh();
      setNotice("Mihomo-профиль сохранён.");
      publishMihomoOperation(operationId, operationLabel, "success", "Профиль готов");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Профиль не сохранён";
      setError(message);
      publishMihomoOperation(operationId, operationLabel, "error", message);
    } finally {
      setBusy("");
    }
  }

  async function removeProfile(profile: Profile) {
    const confirmed = await confirmAction({
      title: `Удалить профиль «${profile.name}»?`,
      message: "Будут удалены профиль Mihomo и его внутренние credentials. Direct-подключения GATE.312 не изменяются.",
      confirmLabel: "Удалить профиль",
      danger: true,
    });
    if (!confirmed) return;
    const operationId = `profile:${profile.id}`;
    const operationLabel = `Удаление профиля ${profile.name}`;
    publishMihomoOperation(operationId, operationLabel, "running", "Удаляем профиль и связанные credentials…");
    setBusy(operationId);
    setError("");
    try {
      await request(`/mihomo/profiles/${profile.id}`, { method: "DELETE" });
      await refresh();
      setNotice(`Профиль «${profile.name}» удалён.`);
      publishMihomoOperation(operationId, operationLabel, "success", "Профиль удалён");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Профиль не удалён";
      setError(message);
      publishMihomoOperation(operationId, operationLabel, "error", message);
    } finally {
      setBusy("");
    }
  }

  async function copyConfig(profile: Profile) {
    setBusy(`config:${profile.id}`);
    setError("");
    try {
      const config = (await request(`/mihomo/profiles/${profile.id}/config`)) as string;
      await navigator.clipboard.writeText(config);
      setNotice(`config.yaml для «${profile.name}» скопирован в буфер обмена.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось получить config.yaml");
    } finally {
      setBusy("");
    }
  }

  const transportModules = useMemo(
    () => modules.filter((item) => item.category === "transport"),
    [modules],
  );
  const installedChannels = transportModules.filter((item) => item.installed);
  const policiesReady = installedChannels.length > 0 && Boolean(dnsPolicy && routingPolicy);

  return (
    <section className="mihomoPage mihomoWorkspace" aria-label="Mihomo Manager">
      <nav className="mihomoHost__tabs mihomoTabs" aria-label="Разделы Mihomo">
        <Tab id="overview" current={view} onSelect={setView}>Обзор</Tab>
        <Tab id="profiles" current={view} onSelect={setView} badge={profiles.length}>Профили</Tab>
        <Tab id="channels" current={view} onSelect={setView} badge={installedChannels.length}>Компоненты</Tab>
        <Tab id="dns" current={view} onSelect={setView}>DNS Mihomo</Tab>
        <Tab id="routing" current={view} onSelect={setView}>Маршрутизация соединений</Tab>
      </nav>

      <article className="mihomoCommandHero">
        <div className="mihomoHeroContent">
          <div className="mihomoHeroIntro">
            <p className="eyebrow">MIHOMO CONTROL</p>
            <div className="mihomoHeroTitleLine">
              <h1>Mihomo</h1>
              <span className={status?.active ? "mihomoCoreState online" : "mihomoCoreState"}>
                {status?.active ? "CORE ONLINE" : "CORE CHECK"}
              </span>
            </div>
            <p className="mihomoHeroLead">
              Контроль и управление профилями, компонентами подключений, DNS и политиками маршрутизации.
            </p>
          </div>

          <div className="mihomoHeroFacts">
            <HeroFact label="CORE" value={status?.core_version || "—"} note={status?.active ? "runtime active" : "status unavailable"} />
            <HeroFact label="PROFILES" value={String(status?.profiles ?? profiles.length)} note={`${status?.profiles_in_use || 0} in use`} />
            <HeroFact label="CREDENTIALS" value={String(status?.credentials || 0)} note="internal only" />
            <HeroFact label="COMPONENTS" value={`${status?.channels_installed ?? installedChannels.length}/4`} note={`${status?.channels_in_use?.length || 0} in use`} />
            <HeroFact label="ENDPOINT" value={status?.endpoint || "—"} note="Mihomo node" wide />
          </div>

          <div className="mihomoHeroActions">
            <button className="ghostButton" type="button" onClick={() => void refresh()} disabled={Boolean(busy)}>
              Обновить
            </button>
            <button
              className="dangerButton"
              type="button"
              onClick={() => void requestCoreRemoval()}
              disabled={coreBusy || Boolean(busy)}
            >
              {coreBusy ? "Удаляется…" : "Удалить Mihomo"}
            </button>
          </div>
        </div>
      </article>

      {error && <div className="mihomoMessage is-error">{error}</div>}
      {notice && <div className="mihomoMessage is-ok">{notice}</div>}

      {view === "overview" && (
        <div className="mihomoOverview">
          <article className="mihomoStatusBoard">
            <header className="mihomoSectionHead">
              <div>
                <p className="eyebrow">MIHOMO WORKSPACE</p>
                <h2>Состояние внутреннего контура</h2>
                <p>Mihomo изолирован от одноимённых Direct-модулей. Здесь отображаются только его собственные sub-modules и профили.</p>
              </div>
              <span className={status?.active ? "mihomoPill is-online" : "mihomoPill"}>
                <i /> {status?.active ? "Runtime работает" : "Нет runtime"}
              </span>
            </header>

            <div className="mihomoTopology">
              <div className="mihomoTopologyNode core">
                <small>CORE</small>
                <strong>Mihomo {status?.core_version || ""}</strong>
                <span>{status?.endpoint || "endpoint определяется"}</span>
              </div>
              <div className="mihomoTopologyArrow" aria-hidden="true" />
              <div className="mihomoTopologyNode">
                <small>PROFILES</small>
                <strong>{status?.profiles ?? profiles.length}</strong>
                <span>{status?.profiles_in_use || 0} используются</span>
              </div>
              <div className="mihomoTopologyArrow" aria-hidden="true" />
              <div className="mihomoTopologyNode">
                <small>COMPONENTS</small>
                <strong>{status?.channels_installed ?? installedChannels.length} / 4</strong>
                <span>{status?.channels_in_use?.length || 0} используются</span>
              </div>
              <div className="mihomoTopologyArrow" aria-hidden="true" />
              <div className="mihomoTopologyNode policy">
                <small>POLICY</small>
                <strong>{policiesReady ? "ACTIVE" : "WAIT"}</strong>
                <span>DNS + routing активируются с первым компонентом</span>
              </div>
            </div>
          </article>

          <article className="mihomoChannelsBoard">
            <header className="mihomoSectionHead compact">
              <div>
                <p className="eyebrow">PROTOCOL COMPONENTS</p>
                <h2>Компоненты Mihomo</h2>
                <p>Здесь устанавливаются ядра протоколов. Конкретные подключения создаются внутри профиля.</p>
              </div>
              <button className="ghostButton" type="button" onClick={() => setView("channels")}>Каталог компонентов</button>
            </header>
            <div className="mihomoChannelGrid">
              {transportModules.map((module) => (
                <div key={module.id} className={`mihomoChannelCard ${module.installed ? "installed" : "empty"} ${module.active ? "active" : ""}`}>
                  <span className="mihomoChannelCode">{channelShort[module.id] || "CH"}</span>
                  <div>
                    <strong>{module.name}</strong>
                    <small>{module.installed ? module.service || "внутренний модуль установлен" : "не установлен"}</small>
                  </div>
                  <em>{module.active ? "ACTIVE" : module.installed ? "READY" : "EMPTY"}</em>
                </div>
              ))}
            </div>
          </article>

          <article className="mihomoQuickState">
            <div>
              <small>DNS MODULE</small>
              <strong>{policiesReady ? "Активен" : "Ожидает компонент"}</strong>
              <span>Общая DNS-политика всех профилей</span>
            </div>
            <div>
              <small>ROUTING MODULE</small>
              <strong>{policiesReady ? "Активна" : "Ожидает компонент"}</strong>
              <span>Базовая стратегия всех профилей</span>
            </div>
            <div>
              <small>PROFILES IN USE</small>
              <strong>{status?.profiles_in_use || 0}</strong>
              <span>{status?.credentials || 0} credentials</span>
            </div>
          </article>
        </div>
      )}

      {view === "profiles" && (
        <article className="mihomoWorkspacePanel">
          <header className="mihomoSectionHead">
            <div>
              <p className="eyebrow">MIHOMO PROFILES</p>
              <h2>Профили устройств</h2>
              <p>Каждый профиль собирается из независимых подключений на базе установленных компонентов.</p>
            </div>
            <button className="primaryButton" onClick={newProfile} disabled={!installedChannels.length}>Новый профиль</button>
          </header>
          {!installedChannels.length && (
            <div className="mihomoHint">Сначала установите хотя бы один компонент протокола Mihomo.</div>
          )}
          <div className="mihomoProfiles">
            {profiles.map((profile) => (
              <div key={profile.id}>
                <div className="mihomoProfileIdentity">
                  <span className="mihomoProfileIcon">M</span>
                  <p><b>{profile.name}</b><small>{profile.id}</small></p>
                </div>
                <div className="mihomoProfileChannels">
                  {profile.connections?.length
                    ? profile.connections.map((connection) => <span key={connection.id}>{connection.name || channelShort[connection.component] || connection.component}</span>)
                    : <em>Нет подключений</em>}
                </div>
                <small className="mihomoProfileUpdated">{new Date(profile.updated_at || profile.created_at).toLocaleString("ru-RU")}</small>
                <div className="mihomoRowActions">
                  <button onClick={() => editProfile(profile)}>Настроить</button>
                  <button onClick={() => void copyConfig(profile)} disabled={busy === `config:${profile.id}`}>config.yaml</button>
                  <button className="dangerButton" onClick={() => void removeProfile(profile)} disabled={busy === `profile:${profile.id}`}>Удалить</button>
                </div>
              </div>
            ))}
            {!profiles.length && <Empty title="Профилей пока нет" text="Установите компонент и соберите первое подключение в профиле." />}
          </div>
        </article>
      )}

      {view === "channels" && (
        <ModuleCatalog
          title="Компоненты подключений Mihomo"
          description="Установите ядра нужных протоколов. Порты, транспорт, credentials и CDN задаются отдельно для каждого подключения в профиле."
          modules={transportModules}
          busy={busy}
          onToggle={toggleModule}
          onUpdate={updateModule}
          onSettings={openSettings}
        />
      )}

      {view === "dns" && (
        <PolicyPanel
          code="DNS"
          title="DNS-политика Mihomo"
          description="Создаётся автоматически вместе с первым каналом. Настройки применяются только к конфигурациям Mihomo и не меняют системный DNS VPS."
          ready={policiesReady}
          values={dnsPolicy?.values}
          onSettings={() => void openPolicySettings("dns")}
        />
      )}

      {view === "routing" && (
        <PolicyPanel
          code="RT"
          title="Маршрутизация Mihomo"
          description="Создаётся автоматически вместе с первым каналом. Здесь задаётся базовая стратегия выбора внутренних каналов для всех профилей."
          ready={policiesReady}
          values={routingPolicy?.values}
          onSettings={() => void openPolicySettings("routing")}
        />
      )}

      {editing && createPortal(
        <div className="mihomoDialogBackdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setEditing(null);
        }}>
          <form className="mihomoDialog" onSubmit={saveSettings}>
            <header>
              <div><p className="eyebrow">MODULE SETTINGS</p><h2>{editing.name}</h2></div>
              <button type="button" className="iconButton" onClick={() => setEditing(null)}>x</button>
            </header>
            <p>{editing.description}</p>
            <div className="mihomoFields">
              {(editing.settings || []).filter((field) => editing.id !== "transport-reality" || String(settingsDraft.transport || "xhttp") === "xhttp" || !["xhttp_mode", "xpadding", "xmux_concurrency"].includes(field.key)).map((field) => (
                <label key={field.key}>
                  <span>{field.label}</span>
                  {field.type === "select" ? (
                    <select value={String(settingsDraft[field.key] ?? field.default)} onChange={(event) => setSettingsDraft((current) => ({ ...current, [field.key]: event.target.value }))}>
                      {(field.options || []).map((option) => {
                        const value = typeof option === "string" ? option : option.value;
                        const label = typeof option === "string" ? option : option.label;
                        return <option key={value} value={value}>{label}</option>;
                      })}
                    </select>
                  ) : field.type === "textarea" ? (
                    <textarea rows={6} value={String(settingsDraft[field.key] ?? field.default)} placeholder={"DOMAIN-SUFFIX,example.com,DIRECT\nGEOIP,PRIVATE,DIRECT"} onChange={(event) => setSettingsDraft((current) => ({ ...current, [field.key]: event.target.value }))} />
                  ) : field.type === "boolean" ? (
                    <input type="checkbox" checked={Boolean(settingsDraft[field.key] ?? field.default)} onChange={(event) => setSettingsDraft((current) => ({ ...current, [field.key]: event.target.checked }))} />
                  ) : (
                    <input type={field.type === "number" ? "number" : "text"} min={field.min} max={field.max} value={String(settingsDraft[field.key] ?? field.default)} onChange={(event) => setSettingsDraft((current) => ({ ...current, [field.key]: field.type === "number" ? Number(event.target.value) : event.target.value }))} />
                  )}
                  {field.help && <small>{field.help}</small>}
                </label>
              ))}
            </div>
            <aside>{editing.installed ? "Сохранение настроек перезапустит только этот Mihomo sub-module. Одноимённый Direct-модуль не затрагивается." : "Настройки сохранятся заранее и будут применены при установке sub-module."}</aside>
            <footer><button type="button" className="ghostButton" onClick={() => setEditing(null)}>Отмена</button><button type="submit" className="primaryButton" disabled={busy === `settings:${editing.id}`}>Сохранить</button></footer>
          </form>
        </div>,
        document.body,
      )}

      {profileDialog && (
        <div className="mihomoDialogBackdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setProfileDialog(null);
        }}>
          <form className="mihomoDialog" onSubmit={saveProfile}>
            <header>
              <div><p className="eyebrow">MIHOMO PROFILE</p><h2>{profileDialog === "new" ? "Новый профиль" : "Настройка профиля"}</h2></div>
              <button type="button" className="iconButton" onClick={() => setProfileDialog(null)}>x</button>
            </header>
            <label className="mihomoProfileName"><span>Название устройства</span><input value={profileName} onChange={(event) => setProfileName(event.target.value)} required maxLength={80} /></label>
            <section className="mihomoConnectionBuilder">
              <header><div><b>Подключения профиля</b><small>Один профиль может содержать несколько VLESS с разными маршрутами и CDN.</small></div></header>
              <div className="mihomoConnectionAdd">
                {installedChannels.map((module) => {
                  const singletonUsed = module.id !== "transport-reality" && profileConnections.some((item) => item.component === module.id);
                  return <button key={module.id} type="button" disabled={singletonUsed} onClick={() => addProfileConnection(module)}>+ {module.name}</button>;
                })}
              </div>
              <div className="mihomoConnectionList">
                {profileConnections.map((connection, index) => {
                  const module = modules.find((item) => item.id === connection.component);
                  const schema = module?.connection_settings || [];
                  return <article key={connection.id} className="mihomoConnectionCard">
                    <header>
                      <span>{channelShort[connection.component] || "CH"}</span>
                      <div><b>{module?.name || connection.component}</b><small>Подключение {index + 1}</small></div>
                      <button type="button" className="dangerButton" onClick={() => setProfileConnections((current) => current.filter((item) => item.id !== connection.id))}>Удалить</button>
                    </header>
                    <label><span>Название в профиле</span><input value={connection.name} maxLength={80} onChange={(event) => updateProfileConnection(connection.id, { name: event.target.value })} /></label>
                    <div className="mihomoConnectionFields">
                      {schema.filter((field) => {
                        if (["xhttp_mode", "xpadding", "xmux_concurrency"].includes(field.key)) return connection.settings.transport === "xhttp";
                        if (field.key === "cdn_domain") return Boolean(connection.settings.cdn_enabled);
                        return true;
                      }).map((field) => <label key={field.key} className={field.type === "boolean" ? "is-toggle" : ""}>
                        <span>{field.label}</span>
                        {field.type === "select" ? <select value={String(connection.settings[field.key] ?? field.default)} onChange={(event) => updateConnectionSetting(connection.id, field.key, event.target.value)}>
                          {(field.options || []).map((option) => { const value = typeof option === "string" ? option : option.value; const label = typeof option === "string" ? option : option.label; return <option key={value} value={value}>{label}</option>; })}
                        </select> : field.type === "boolean" ? <input type="checkbox" checked={Boolean(connection.settings[field.key] ?? field.default)} onChange={(event) => updateConnectionSetting(connection.id, field.key, event.target.checked)} />
                          : <input type={field.type === "number" ? "number" : "text"} min={field.min} max={field.max} value={String(connection.settings[field.key] ?? field.default)} onChange={(event) => updateConnectionSetting(connection.id, field.key, field.type === "number" ? Number(event.target.value) : event.target.value)} />}
                        {field.help && <small>{field.help}</small>}
                      </label>)}
                    </div>
                  </article>;
                })}
                {!profileConnections.length && <p className="mihomoConnectionEmpty">Добавьте хотя бы одно подключение из установленного компонента.</p>}
              </div>
            </section>
            <aside>Компонент устанавливает ядро протокола один раз. Каждая карточка выше создаёт независимые параметры и credential только для этого профиля.</aside>
            <footer><button type="button" className="ghostButton" onClick={() => setProfileDialog(null)}>Отмена</button><button type="submit" className="primaryButton" disabled={busy === "profile" || !profileName.trim() || !profileConnections.length}>{profileDialog === "new" ? "Создать профиль" : "Сохранить профиль"}</button></footer>
          </form>
        </div>
      )}
    </section>
  );
}

function Tab({ id, current, onSelect, badge, children }: { id: View; current: View; onSelect: (id: View) => void; badge?: number; children: React.ReactNode }) {
  return <button type="button" className={current === id ? "active" : ""} aria-current={current === id ? "page" : undefined} onClick={() => onSelect(id)}><b>{children}</b>{badge !== undefined && <em>{badge}</em>}</button>;
}

function HeroFact({ label, value, note, wide = false }: { label: string; value: string; note: string; wide?: boolean }) {
  return <div className={wide ? "wide" : ""}><small>{label}</small><strong>{value}</strong><span>{note}</span></div>;
}

function ModuleCatalog({ title, description, modules, busy, onToggle, onUpdate, onSettings }: { title: string; description: string; modules: Module[]; busy: string; onToggle: (module: Module) => void; onUpdate: (module: Module) => void; onSettings: (module: Module) => void }) {
  return (
    <article className="mihomoWorkspacePanel mihomoCatalogPanel">
      <header className="mihomoSectionHead">
        <div><p className="eyebrow">MIHOMO SUB-MODULES</p><h2>{title}</h2><p>{description}</p></div>
        <span className="mihomoCatalogCount">{modules.filter((item) => item.installed).length} / {modules.length} установлено</span>
      </header>
      <div className="mihomoModuleCatalog">
        {modules.map((module) => (
          <div key={module.id} className={module.installed ? "is-installed" : ""}>
            <span className="mihomoModuleCode">{channelShort[module.id] || (module.category === "dns" ? "DNS" : "RT")}</span>
            <p>
              <b>{module.name}</b>
              <small>{module.description}</small>
              <code>{module.service || "config-only module"}</code>
              {module.installed && module.installed_version && (
                <span className="mihomoModuleVersionRow">
                  <i>{formatModuleVersion(module.installed_version)}</i>
                  {module.update_available && <em className={module.update_breaking ? "breaking" : ""}>→ {module.id === "transport-awg" ? "репозиторий" : formatModuleVersion(module.available_version)}{module.update_breaking ? "  major" : ""}</em>}
                </span>
              )}
            </p>
            <span className={module.active ? "onlinePill" : module.installed ? "warningPill" : "disabledPill"}>{module.active ? "Активен" : module.installed ? "Установлен" : "Не установлен"}</span>
            <span className="mihomoModuleActions">
              <button className="ghostButton" onClick={() => onSettings(module)}>Настройки</button>
              {module.installed && module.update_available && (
                <button className={`ghostButton${module.update_breaking ? " breaking" : ""}`} disabled={Boolean(busy)} onClick={() => void onUpdate(module)}>{busy === `update:${module.id}` ? "Обновление…" : "Обновить"}</button>
              )}
              <button className={module.installed ? "dangerButton" : "primaryButton"} disabled={module.installable === false || Boolean(busy)} onClick={() => void onToggle(module)}>{module.installable === false ? "В разработке" : busy === module.id ? "Выполняется…" : module.installed ? "Удалить" : "Установить"}</button>
            </span>
          </div>
        ))}
        {!modules.length && <Empty title="Каталог пуст" text="Mihomo Manager не получил manifest внутренних модулей." />}
      </div>
    </article>
  );
}

function PolicyPanel({ code, title, description, ready, values, onSettings }: { code: string; title: string; description: string; ready: boolean; values?: Record<string, string | number>; onSettings: () => void }) {
  return (
    <article className="mihomoPolicyPanel">
      <header>
        <span className="mihomoPolicyMark">{code}</span>
        <div><p className="eyebrow">AUTOMATIC POLICY</p><h2>{title}</h2><p>{description}</p></div>
        <span className={ready ? "mihomoPill is-online" : "mihomoPill"}><i />{ready ? "ACTIVE" : "WAITING"}</span>
      </header>
      <div className="mihomoPolicyValues">
        {Object.entries(values || {}).map(([key, value]) => <div key={key}><small>{key.replaceAll("_", " ")}</small><strong>{String(value) || "—"}</strong></div>)}
      </div>
      <footer>
        <span>{ready ? "Политика подключена ко всем новым конфигурациям Mihomo." : "Установите первый внутренний канал, чтобы активировать policy-слой."}</span>
        <button className="primaryButton" type="button" onClick={onSettings}>Настроить</button>
      </footer>
    </article>
  );
}

function Empty({ title, text }: { title: string; text: string }) {
  return <div className="mihomoEmpty"><p><b>{title}</b><small>{text}</small></p></div>;
}
