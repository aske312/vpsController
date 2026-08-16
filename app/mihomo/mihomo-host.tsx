"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

type View = "overview" | "profiles" | "channels" | "dns" | "routing";

type Status = {
  active: boolean;
  core_version: string;
  profiles: number;
  channels_installed: number;
  modules_installed: number;
  modules_total: number;
  endpoint: string;
};

type SettingField = {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "textarea";
  default: string | number;
  min?: number;
  max?: number;
  options?: string[];
};

type Module = {
  id: string;
  name: string;
  description: string;
  category: "transport" | "dns" | "routing";
  category_name: string;
  installed: boolean;
  active: boolean;
  service?: string;
  settings?: SettingField[];
  settings_values: Record<string, string | number>;
};

type Profile = {
  id: string;
  name: string;
  channels: string[];
  created_at: string;
  updated_at: string;
};

const channelShort: Record<string, string> = {
  "transport-awg": "AW",
  "transport-wg": "WG",
  "transport-reality": "VX",
  "transport-shadowsocks": "SS",
};

export function MihomoPage({
  token,
  onRemoved,
}: {
  token: string;
  onRemoved: () => void;
}) {
  const [view, setView] = useState<View>("overview");
  const [status, setStatus] = useState<Status | null>(null);
  const [modules, setModules] = useState<Module[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Module | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string | number>>({});
  const [profileDialog, setProfileDialog] = useState<Profile | "new" | null>(null);
  const [profileName, setProfileName] = useState("");
  const [profileChannels, setProfileChannels] = useState<string[]>([]);

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
      const [nextStatus, nextModules, nextProfiles] = await Promise.all([
        request("/mihomo/status"),
        request("/mihomo/modules"),
        request("/mihomo/profiles"),
      ]);
      setStatus(nextStatus as Status);
      setModules((nextModules as { items: Module[] }).items || []);
      setProfiles((nextProfiles as { items: Profile[] }).items || []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить Mihomo Manager");
    }
  }, [request]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function removeCore() {
    if (!window.confirm(
      "Удалить Mihomo Manager и ВСЕ его внутренние профили/sub-modules?\n\nDirect WG/AWG/SS/Reality и страница «Подключения» не изменятся.",
    )) return;
    setBusy("remove-core");
    setError("");
    try {
      await request("/protocol-images/mihomo", { method: "DELETE" });
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        try {
          const data = (await request("/protocol-images")) as { items?: Array<{ id: string; installed: boolean }> };
          const current = data.items?.find((item) => item.id === "mihomo");
          if (!current?.installed) {
            onRemoved();
            return;
          }
        } catch {
          // API can briefly restart during protocol removal.
        }
      }
      throw new Error("Сервер не подтвердил удаление Mihomo.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось удалить Mihomo");
      setBusy("");
    }
  }

  async function toggleModule(module: Module) {
    const install = !module.installed;
    const prompt =
      `${install ? "Установить" : "Удалить"} «${module.name}» внутри Mihomo?\n\n` +
      "Это отдельный Mihomo sub-module. Прямые подключения и одноимённые server modules GATE.312 не изменяются.";
    if (!window.confirm(prompt)) return;
    setBusy(module.id);
    setError("");
    setNotice("");
    try {
      await request(`/mihomo/modules/${module.id}${install ? "/install" : ""}`, {
        method: install ? "POST" : "DELETE",
      });
      await refresh();
      setNotice(`${module.name}: ${install ? "установлен внутри Mihomo" : "удалён из Mihomo"}.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Операция Mihomo не выполнена");
    } finally {
      setBusy("");
    }
  }

  function openSettings(module: Module) {
    setEditing(module);
    setSettingsDraft({ ...module.settings_values });
  }

  async function saveSettings(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    setBusy(`settings:${editing.id}`);
    setError("");
    try {
      await request(`/mihomo/modules/${editing.id}/settings`, {
        method: "PATCH",
        body: JSON.stringify({ values: settingsDraft }),
      });
      setEditing(null);
      await refresh();
      setNotice(`Настройки ${editing.name} сохранены.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Настройки не сохранены");
    } finally {
      setBusy("");
    }
  }

  function newProfile() {
    setProfileDialog("new");
    setProfileName("");
    setProfileChannels([]);
  }

  function editProfile(profile: Profile) {
    setProfileDialog(profile);
    setProfileName(profile.name);
    setProfileChannels([...profile.channels]);
  }

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    setBusy("profile");
    setError("");
    try {
      if (profileDialog === "new") {
        await request("/mihomo/profiles", {
          method: "POST",
          body: JSON.stringify({ name: profileName, channels: profileChannels }),
        });
      } else if (profileDialog) {
        await request(`/mihomo/profiles/${profileDialog.id}`, {
          method: "PATCH",
          body: JSON.stringify({ name: profileName, channels: profileChannels }),
        });
      }
      setProfileDialog(null);
      await refresh();
      setNotice("Mihomo-профиль сохранён.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Профиль не сохранён");
    } finally {
      setBusy("");
    }
  }

  async function removeProfile(profile: Profile) {
    if (!window.confirm(`Удалить Mihomo-профиль «${profile.name}» и его независимые credentials?`)) return;
    setBusy(`profile:${profile.id}`);
    setError("");
    try {
      await request(`/mihomo/profiles/${profile.id}`, { method: "DELETE" });
      await refresh();
      setNotice(`Профиль «${profile.name}» удалён.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Профиль не удалён");
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
  const dnsModule = modules.find((item) => item.category === "dns");
  const routingModule = modules.find((item) => item.category === "routing");
  const installedChannels = transportModules.filter((item) => item.installed);

  return (
    <section className="mihomoPage" aria-label="Mihomo Manager">
      <article className="panel mihomoPage__hero">
        <div>
          <p className="eyebrow">MIHOMO MANAGER</p>
          <h2>Менеджер подключений</h2>
          <p>Профили устройств, собственные каналы, DNS и маршрутизация Mihomo.</p>
        </div>
        <div className="mihomoPage__heroActions">
          <span className={status?.active ? "mihomoPill is-online" : "mihomoPill"}>
            <i /> {status?.active ? "Mihomo работает" : "Проверка статуса"}
          </span>
          <button className="ghostButton" type="button" onClick={() => void refresh()}>
            Обновить
          </button>
          <button className="dangerButton" type="button" onClick={() => void removeCore()} disabled={busy === "remove-core"}>
            {busy === "remove-core" ? "Удаляется…" : "Удалить Mihomo"}
          </button>
        </div>
      </article>

        <nav className="mihomoHost__tabs">
          <Tab id="overview" current={view} onSelect={setView}>Обзор</Tab>
          <Tab id="profiles" current={view} onSelect={setView} badge={profiles.length}>Профили</Tab>
          <Tab id="channels" current={view} onSelect={setView} badge={installedChannels.length}>Каналы</Tab>
          <Tab id="dns" current={view} onSelect={setView} badge={dnsModule?.installed ? 1 : 0}>DNS</Tab>
          <Tab id="routing" current={view} onSelect={setView} badge={routingModule?.installed ? 1 : 0}>Маршрутизация</Tab>
        </nav>

        {error && <div className="mihomoMessage is-error">{error}</div>}
        {notice && <div className="mihomoMessage is-ok">{notice}</div>}

        {view === "overview" && (
          <div className="mihomoOverview">
            <article className="panel mihomoCore">
              <div>
                <p className="eyebrow">MIHOMO CORE</p>
                <h2>Самостоятельный модуль приложения</h2>
                <p>
                  Core установлен отдельно от VPN-протоколов. После установки его каталог каналов
                  пуст: AWG, WG, Reality и Shadowsocks включаются только здесь как sub-modules.
                </p>
              </div>
              <dl>
                <Metric label="Версия Mihomo" value={status?.core_version || "определяется"} />
                <Metric label="Профили" value={String(status?.profiles ?? profiles.length)} />
                <Metric label="Каналы" value={`${status?.channels_installed ?? installedChannels.length}/4`} />
                <Metric label="Endpoint" value={status?.endpoint || "—"} />
              </dl>
            </article>

            

            <article className="panel">
              <div className="panelHead">
                <div>
                  <p className="eyebrow">CHANNELS</p>
                  <h2>Каналы Mihomo</h2>
                </div>
                <button className="ghostButton" onClick={() => setView("channels")}>Открыть каталог</button>
              </div>
              <div className="mihomoMiniModules">
                {transportModules.map((module) => (
                  <div key={module.id} className={module.installed ? "is-installed" : ""}>
                    <span>{channelShort[module.id] || "CH"}</span>
                    <p><b>{module.name}</b><small>{module.installed ? "установлен внутри Mihomo" : "не установлен"}</small></p>
                    <em>{module.active ? "ONLINE" : module.installed ? "READY" : "EMPTY"}</em>
                  </div>
                ))}
              </div>
            </article>
          </div>
        )}

        {view === "profiles" && (
          <article className="panel">
            <div className="panelHead">
              <div>
                <p className="eyebrow">MIHOMO PROFILES</p>
                <h2>Профили устройств</h2>
                <p>Профиль может использовать только те каналы, которые отдельно установлены внутри Mihomo.</p>
              </div>
              <button className="primaryButton" onClick={newProfile} disabled={!installedChannels.length}>
                Новый профиль <span>＋</span>
              </button>
            </div>
            {!installedChannels.length && (
              <div className="mihomoHint">
                Сначала установите хотя бы один канал в разделе «Каналы». Direct AWG/WG/SS/Reality здесь не используются.
              </div>
            )}
            <div className="mihomoProfiles">
              {profiles.map((profile) => (
                <div key={profile.id}>
                  <span className="mihomoProfileIcon">M</span>
                  <p>
                    <b>{profile.name}</b>
                    <small>{profile.id}</small>
                  </p>
                  <div className="mihomoProfileChannels">
                    {profile.channels.length
                      ? profile.channels.map((id) => <span key={id}>{channelShort[id] || id}</span>)
                      : <em>Нет каналов</em>}
                  </div>
                  <small>{new Date(profile.updated_at || profile.created_at).toLocaleString("ru-RU")}</small>
                  <div className="mihomoRowActions">
                    <button onClick={() => editProfile(profile)}>Настроить</button>
                    <button onClick={() => void copyConfig(profile)} disabled={busy === `config:${profile.id}`}>config.yaml</button>
                    <button className="dangerButton" onClick={() => void removeProfile(profile)} disabled={busy === `profile:${profile.id}`}>Удалить</button>
                  </div>
                </div>
              ))}
              {!profiles.length && <Empty title="Профилей пока нет" text="Создайте первый профиль после установки хотя бы одного внутреннего канала Mihomo." />}
            </div>
          </article>
        )}

        {view === "channels" && (
          <ModuleCatalog
            title="Каналы подключения Mihomo"
            description="Каждый канал устанавливается отдельно и имеет собственный server instance, конфигурацию, порт и credentials."
            modules={transportModules}
            busy={busy}
            onToggle={toggleModule}
            onSettings={openSettings}
          />
        )}

        {view === "dns" && (
          <ModuleCatalog
            title="DNS внутри Mihomo"
            description="Настройки попадают только в создаваемые config.yaml Mihomo. Системный DNS VPS и direct-подключения не меняются."
            modules={dnsModule ? [dnsModule] : []}
            busy={busy}
            onToggle={toggleModule}
            onSettings={openSettings}
          />
        )}

        {view === "routing" && (
          <ModuleCatalog
            title="Маршрутизация Mihomo"
            description="Fallback / url-test / select определяют поведение только между внутренними каналами Mihomo."
            modules={routingModule ? [routingModule] : []}
            busy={busy}
            onToggle={toggleModule}
            onSettings={openSettings}
          />
      )}

      {editing && (
        <div className="mihomoDialogBackdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setEditing(null);
        }}>
          <form className="mihomoDialog" onSubmit={saveSettings}>
            <header>
              <div><p className="eyebrow">MODULE SETTINGS</p><h2>{editing.name}</h2></div>
              <button type="button" className="iconButton" onClick={() => setEditing(null)}>×</button>
            </header>
            <p>{editing.description}</p>
            <div className="mihomoFields">
              {(editing.settings || []).map((field) => (
                <label key={field.key}>
                  <span>{field.label}</span>
                  {field.type === "select" ? (
                    <select
                      value={String(settingsDraft[field.key] ?? field.default)}
                      onChange={(event) => setSettingsDraft((current) => ({ ...current, [field.key]: event.target.value }))}
                    >
                      {(field.options || []).map((option) => <option key={option} value={option}>{option}</option>)}
                    </select>
                  ) : field.type === "textarea" ? (
                    <textarea
                      rows={6}
                      value={String(settingsDraft[field.key] ?? field.default)}
                      placeholder={"DOMAIN-SUFFIX,example.com,DIRECT\nGEOIP,PRIVATE,DIRECT"}
                      onChange={(event) => setSettingsDraft((current) => ({ ...current, [field.key]: event.target.value }))}
                    />
                  ) : (
                    <input
                      type={field.type === "number" ? "number" : "text"}
                      min={field.min}
                      max={field.max}
                      value={String(settingsDraft[field.key] ?? field.default)}
                      onChange={(event) => setSettingsDraft((current) => ({
                        ...current,
                        [field.key]: field.type === "number" ? Number(event.target.value) : event.target.value,
                      }))}
                    />
                  )}
                </label>
              ))}
            </div>
            <aside>
              {editing.installed
                ? "Сохранение настроек перезапустит только этот Mihomo sub-module. Одноимённый direct-модуль не затрагивается."
                : "Настройки сохраняются заранее и будут применены при установке sub-module."}
            </aside>
            <footer>
              <button type="button" className="ghostButton" onClick={() => setEditing(null)}>Отмена</button>
              <button type="submit" className="primaryButton" disabled={busy === `settings:${editing.id}`}>Сохранить</button>
            </footer>
          </form>
        </div>
      )}

      {profileDialog && (
        <div className="mihomoDialogBackdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setProfileDialog(null);
        }}>
          <form className="mihomoDialog" onSubmit={saveProfile}>
            <header>
              <div>
                <p className="eyebrow">MIHOMO PROFILE</p>
                <h2>{profileDialog === "new" ? "Новый профиль" : "Настройка профиля"}</h2>
              </div>
              <button type="button" className="iconButton" onClick={() => setProfileDialog(null)}>×</button>
            </header>
            <label className="mihomoProfileName">
              <span>Название устройства</span>
              <input value={profileName} onChange={(event) => setProfileName(event.target.value)} required maxLength={80} />
            </label>
            <fieldset>
              <legend>Внутренние каналы</legend>
              {installedChannels.map((module) => (
                <label key={module.id} className={profileChannels.includes(module.id) ? "is-selected" : ""}>
                  <input
                    type="checkbox"
                    checked={profileChannels.includes(module.id)}
                    onChange={(event) => setProfileChannels((current) =>
                      event.target.checked
                        ? [...current, module.id]
                        : current.filter((id) => id !== module.id)
                    )}
                  />
                  <span>{channelShort[module.id] || "CH"}</span>
                  <p><b>{module.name}</b><small>{module.description}</small></p>
                </label>
              ))}
            </fieldset>
            <aside>
              Для каждого выбранного канала создаётся отдельный Mihomo credential. Он не появляется на странице «Подключения».
            </aside>
            <footer>
              <button type="button" className="ghostButton" onClick={() => setProfileDialog(null)}>Отмена</button>
              <button type="submit" className="primaryButton" disabled={busy === "profile" || !profileName.trim()}>
                {profileDialog === "new" ? "Создать профиль" : "Сохранить профиль"}
              </button>
            </footer>
          </form>
        </div>
      )}
    </section>
  );
}

function Tab({
  id, current, onSelect, badge, children,
}: {
  id: View;
  current: View;
  onSelect: (id: View) => void;
  badge?: number;
  children: React.ReactNode;
}) {
  return (
    <button className={current === id ? "active" : ""} onClick={() => onSelect(id)}>
      <b>{children}</b>{badge !== undefined && <em>{badge}</em>}
    </button>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function ModuleCatalog({
  title, description, modules, busy, onToggle, onSettings,
}: {
  title: string;
  description: string;
  modules: Module[];
  busy: string;
  onToggle: (module: Module) => void;
  onSettings: (module: Module) => void;
}) {
  return (
    <article className="panel">
      <div className="panelHead">
        <div><p className="eyebrow">MIHOMO SUB-MODULES</p><h2>{title}</h2><p>{description}</p></div>
        <span>{modules.filter((item) => item.installed).length} / {modules.length} установлено</span>
      </div>
      <div className="mihomoModuleCatalog">
        {modules.map((module) => (
          <div key={module.id} className={module.installed ? "is-installed" : ""}>
            <span className="mihomoModuleCode">{channelShort[module.id] || (module.category === "dns" ? "DNS" : "RT")}</span>
            <p><b>{module.name}</b><small>{module.description}</small><code>{module.service || "config-only module"}</code></p>
            <span className={module.active ? "onlinePill" : module.installed ? "warningPill" : "disabledPill"}>
              {module.active ? "Активен" : module.installed ? "Установлен" : "Не установлен"}
            </span>
            <button className="ghostButton" onClick={() => onSettings(module)}>Настройки</button>
            <button
              className={module.installed ? "dangerButton" : "primaryButton"}
              disabled={Boolean(busy)}
              onClick={() => void onToggle(module)}
            >
              {busy === module.id ? "Выполняется…" : module.installed ? "Удалить" : "Установить"}
            </button>
          </div>
        ))}
        {!modules.length && <Empty title="Каталог пуст" text="Mihomo Manager не получил manifest внутренних модулей." />}
      </div>
    </article>
  );
}

function Empty({ title, text }: { title: string; text: string }) {
  return <div className="mihomoEmpty"><span>—</span><p><b>{title}</b><small>{text}</small></p></div>;
}
