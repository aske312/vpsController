"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, basicCredentials, LightApi } from "./api";
import { edition, type Client, type Overview, type Protocol, type ProtocolImage, type ProtocolStatus, type View } from "./contracts";
import { bytes, duration, percent } from "./format";

const labels: Record<View, string> = { overview: "Обзор", connections: "Подключения", wg: "WireGuard", awg: "AmneziaWG" };

export function LightApplication() {
  const [credentials, setCredentials] = useState("");
  const [view, setView] = useState<View>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [clients, setClients] = useState<Client[]>([]);
  const [images, setImages] = useState<ProtocolImage[]>([]);
  const [statuses, setStatuses] = useState<Partial<Record<Protocol, ProtocolStatus>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => setCredentials(sessionStorage.getItem("312-light-session") || ""), 0);
    return () => window.clearTimeout(timer);
  }, []);
  const api = useMemo(() => new LightApi(credentials), [credentials]);
  const signOut = useCallback(() => { sessionStorage.removeItem("312-light-session"); setCredentials(""); setOverview(null); setClients([]); setStatuses({}); }, []);

  const load = useCallback(async (showBusy = false) => {
    if (!credentials) return;
    if (showBusy) setBusy(true);
    try {
      const [nextOverview, nextClients, nextImages] = await Promise.all([
        api.request<Overview>("/overview"), api.request<{ items: Client[] }>("/clients"), api.request<{ items: ProtocolImage[] }>("/protocol-images"),
      ]);
      setOverview(nextOverview);
      setClients(nextClients.items.filter((item) => item.protocol === "wg" || item.protocol === "awg"));
      setImages(nextImages.items.filter((item): item is ProtocolImage => item.id === "wg" || item.id === "awg"));
      setUpdatedAt(new Date()); setError("");
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) signOut();
      setError(cause instanceof Error ? cause.message : "Не удалось обновить данные");
    } finally { setBusy(false); }
  }, [api, credentials, signOut]);

  const loadProtocol = useCallback(async (protocol: Protocol) => {
    try { const status = await api.request<ProtocolStatus>(`/protocols/${protocol}/status`); setStatuses((current) => ({ ...current, [protocol]: status })); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось загрузить протокол"); }
  }, [api]);
  useEffect(() => { if (!credentials) return; const timer = window.setTimeout(() => void load(true), 0); return () => window.clearTimeout(timer); }, [credentials, load]);
  useEffect(() => { if (!credentials || (view !== "wg" && view !== "awg")) return; const timer = window.setTimeout(() => void loadProtocol(view), 0); return () => window.clearTimeout(timer); }, [credentials, loadProtocol, view]);

  if (!credentials) return <Login onAuthenticated={(next) => { sessionStorage.setItem("312-light-session", next); setCredentials(next); }} />;
  const installed = new Set(images.filter((item) => item.installed).map((item) => item.id));
  return <div className="lightShell">
    <aside className="lightSidebar"><Brand /><nav aria-label="Основная навигация">{edition.views.map((item) => <button key={item} className={`navItem ${view === item ? "active" : ""}`} onClick={() => setView(item)}><span>{item === "overview" ? "◇" : item === "connections" ? "⇄" : item === "wg" ? "W" : "A"}</span><b>{labels[item]}</b>{(item === "wg" || item === "awg") && <i className={installed.has(item) ? "ready" : ""} />}</button>)}</nav><div className="serverCard"><span className={overview ? "statusDot ready" : "statusDot"} /><div><small>LIGHT NODE</small><strong>{overview?.server.name || "Сервер"}</strong><p>{overview?.server.public_ip || "Нет данных"}</p></div></div><button className="logoutButton" onClick={signOut}>Выйти</button></aside>
    <main className="lightContent"><header className="topbar"><div><p className="eyebrow">312.NET · LIGHT EDITION</p><h1>{labels[view]}</h1><p className="subtitle">{subtitle(view)}</p></div><div className="topActions"><span>{updatedAt ? `Обновлено ${updatedAt.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}` : "Обновление…"}</span><button onClick={() => void load(true)} disabled={busy}>{busy ? "Обновление…" : "Обновить"}</button></div></header>
      {error && <div className="errorBanner" role="alert">{error}<button onClick={() => setError("")}>×</button></div>}
      {view === "overview" && <OverviewView overview={overview} clients={clients} />}
      {view === "connections" && <ConnectionsView api={api} clients={clients} installed={installed} onChanged={() => load()} setError={setError} />}
      {(view === "wg" || view === "awg") && <ProtocolView api={api} protocol={view} image={images.find((item) => item.id === view)} status={statuses[view]} onChanged={async () => { await load(); await loadProtocol(view); }} setError={setError} />}
    </main>
  </div>;
}

function Login({ onAuthenticated }: { onAuthenticated: (credentials: string) => void }) {
  const [username, setUsername] = useState("admin"); const [password, setPassword] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); setError(""); const candidate = basicCredentials(username, password); try { await new LightApi(candidate).request("/overview"); onAuthenticated(candidate); } catch { setError("Неверный логин или пароль"); } finally { setBusy(false); } }
  return <main className="loginPage"><div className="loginBackdrop" /><form className="loginCard" onSubmit={submit}><Brand /><div><p className="eyebrow">LIGHT CONTROL PLANE</p><h1>Управление вашим сервером.</h1><p>Компактная панель WireGuard и AmneziaWG.</p></div><label>Логин<input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" autoFocus /></label><label>Пароль<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" /></label><button className="primaryButton" disabled={busy}>{busy ? "Проверка…" : "Открыть панель"}<span>→</span></button>{error && <div className="errorBanner">{error}</div>}</form></main>;
}

function OverviewView({ overview, clients }: { overview: Overview | null; clients: Client[] }) {
  if (!overview) return <Empty title="Получаем состояние сервера" />;
  const resources = overview.resources;
  const cards = [["CPU", resources.cpu_percent, `${resources.cpu_count} ядер`], ["Память", percent(resources.memory_available, resources.memory_total), `${bytes(resources.memory_total - resources.memory_available)} / ${bytes(resources.memory_total)}`], ["Диск", percent(resources.disk_available, resources.disk_total), `${bytes(resources.disk_total - resources.disk_available)} / ${bytes(resources.disk_total)}`]] as const;
  return <div className="viewStack"><section className="heroPanel"><div><span className="statusPill"><i />Сервер доступен</span><h2>{overview.server.city || overview.server.country || "Ваш VPS"}</h2><p>{overview.server.public_ip} · работает {duration(overview.server.uptime_s)}</p></div><div className="heroMetric"><strong>{clients.length}</strong><span>подключений</span></div></section><section className="metricGrid">{cards.map(([label, value, detail]) => <article className="metricCard" key={label}><div><span>{label}</span><strong>{value.toFixed(0)}%</strong></div><div className="meter"><i style={{ width: `${value}%` }} /></div><small>{detail}</small></article>)}</section><section className="protocolGrid">{edition.protocols.map((protocol) => { const item = overview.protocols[protocol]; return <article className="protocolCard" key={protocol}><span className={`protocolGlyph ${protocol}`}>{protocol === "wg" ? "W" : "A"}</span><div><small>{protocol === "wg" ? "WIREGUARD" : "AMNEZIAWG"}</small><h3>{item.active ? "Работает" : "Остановлен"}</h3><p>{item.interface} · UDP {item.port}</p></div><span className={`statusDot ${item.active ? "ready" : ""}`} /></article>; })}</section></div>;
}

function ConnectionsView({ api, clients, installed, onChanged, setError }: { api: LightApi; clients: Client[]; installed: Set<Protocol>; onChanged: () => Promise<void>; setError: (value: string) => void }) {
  const [name, setName] = useState(""); const [protocol, setProtocol] = useState<Protocol>("wg"); const [busy, setBusy] = useState(false);
  async function create(event: FormEvent) { event.preventDefault(); setBusy(true); try { const result = await api.request<{ config: string; filename: string }>("/clients", { method: "POST", body: JSON.stringify({ name, protocol }) }); const url = URL.createObjectURL(new Blob([result.config], { type: "text/plain" })); const link = document.createElement("a"); link.href = url; link.download = result.filename; link.click(); URL.revokeObjectURL(url); setName(""); await onChanged(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось создать подключение"); } finally { setBusy(false); } }
  async function remove(id: string) { if (!confirm("Отозвать доступ этого подключения?")) return; try { await api.request(`/clients/${id}`, { method: "DELETE" }); await onChanged(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось удалить подключение"); } }
  return <div className="twoColumn"><section className="panel"><div className="sectionHead"><div><p className="eyebrow">ACCESS</p><h2>Новое подключение</h2></div></div><form className="formGrid" onSubmit={create}><label>Название<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Например, ноутбук" required /></label><label>Протокол<select value={protocol} onChange={(event) => setProtocol(event.target.value as Protocol)}><option value="wg">WireGuard</option><option value="awg">AmneziaWG</option></select></label><button className="primaryButton" disabled={busy || !installed.has(protocol)}>{installed.has(protocol) ? (busy ? "Создание…" : "Создать и скачать") : "Сначала установите протокол"}</button></form></section><section className="panel"><div className="sectionHead"><div><p className="eyebrow">DEVICES</p><h2>Подключения</h2></div><span className="countPill">{clients.length}</span></div><div className="list">{clients.length ? clients.map((client) => <article className="listItem" key={client.id}><span className={`protocolGlyph small ${client.protocol}`}>{client.protocol === "wg" ? "W" : "A"}</span><div><strong>{client.name}</strong><p>{client.address} · {bytes(client.rx_bytes + client.tx_bytes)}</p></div><span className={`quality ${client.quality || "offline"}`}>{client.quality || "offline"}</span><button className="iconButton danger" onClick={() => void remove(client.id)} aria-label={`Удалить ${client.name}`}>×</button></article>) : <Empty title="Подключений пока нет" />}</div></section></div>;
}

function ProtocolView({ api, protocol, image, status, onChanged, setError }: { api: LightApi; protocol: Protocol; image?: ProtocolImage; status?: ProtocolStatus; onChanged: () => Promise<void>; setError: (value: string) => void }) {
  const [busy, setBusy] = useState(false);
  async function action(kind: "install" | "remove" | "restart" | "diagnostics/check") { setBusy(true); try { const path = kind === "install" ? `/protocol-images/${protocol}/install` : kind === "remove" ? `/protocol-images/${protocol}` : `/protocols/${protocol}/${kind}`; await api.request(path, { method: kind === "remove" ? "DELETE" : "POST" }); await onChanged(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Команда не выполнена"); } finally { setBusy(false); } }
  const installed = Boolean(image?.installed);
  return <div className="viewStack"><section className="heroPanel protocolHero"><span className={`protocolGlyph large ${protocol}`}>{protocol === "wg" ? "W" : "A"}</span><div><p className="eyebrow">{protocol === "wg" ? "WIREGUARD" : "AMNEZIAWG"}</p><h2>{image?.name || labels[protocol]}</h2><p>{image?.description || "Защищённый туннель для ваших устройств."}</p></div><span className={`statusPill ${status?.active ? "" : "muted"}`}><i />{status?.active ? "Работает" : installed ? "Установлен" : "Не установлен"}</span></section>{installed ? <><section className="metricGrid"><Metric label="Интерфейс" value={status?.interface || image?.interface || "—"} /><Metric label="Порт" value={status?.listen_port ? String(status.listen_port) : "—"} /><Metric label="Онлайн" value={`${status?.online_peers || 0} / ${status?.peers || 0}`} /></section><section className="panel actionsPanel"><div><h2>Управление</h2><p>Диагностика проверяет доступность ресурсов и сетевой маршрут.</p></div><div><button onClick={() => void action("diagnostics/check")} disabled={busy}>Проверить</button><button onClick={() => void action("restart")} disabled={busy}>Перезапустить</button><button className="dangerButton" onClick={() => void action("remove")} disabled={busy}>Удалить</button></div></section></> : <section className="emptyInstall"><h2>Протокол готов к установке</h2><p>Панель установит модуль и создаст системную службу.</p><button className="primaryButton" onClick={() => void action("install")} disabled={busy}>{busy ? "Установка…" : `Установить ${labels[protocol]}`}</button></section>}</div>;
}

function Metric({ label, value }: { label: string; value: string }) { return <article className="metricCard compact"><span>{label}</span><strong>{value}</strong></article>; }
function Empty({ title }: { title: string }) { return <div className="emptyState"><span>◇</span><p>{title}</p></div>; }
function Brand() { return <div className="brand"><span className="brandMark">312</span><div><strong>312<span>.net</span></strong><small>LIGHT CONTROL</small></div></div>; }
function subtitle(view: View) { return view === "overview" ? "Состояние сервера и защищённых туннелей." : view === "connections" ? "Доступы ваших устройств к серверу." : "Состояние, диагностика и управление протоколом."; }
