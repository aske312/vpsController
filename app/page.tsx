"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import QRCode from "qrcode";
import { ConnectionGuide } from "./connection-guide";
import { LegalFooter } from "./legal";

type Protocol = "wg" | "awg" | "shadowsocks" | "vless-reality-xhttp";
type Tab = "overview" | "security" | "application" | "services" | "clients" | Protocol;
type TunnelProtocol = "wg" | "awg";
type ResourceHistory = { load: number[]; memory: number[]; disk: number[]; rx: number[]; tx: number[] };
type ApplicationAction = "restart" | "update" | "test-update" | "test-rollback" | "network-check" | "integrity-check" | "identity" | "secure" | "kernel-update" | "vpn-firewall" | "optimize" | "reboot" | "poweroff";
type Client = {
  id: string; name: string; protocol: Protocol; public_key: string; endpoint?: string;
  address: string; handshake_age_s?: number; rx_bytes: number; tx_bytes: number;
  rx_bps?: number; tx_bps?: number; active_connections?: number; active_sources?: string[];
  quality?: "stable" | "warning" | "error" | "offline"; latency_ms?: number; jitter_ms?: number; packet_loss_percent?: number; quality_reason?: string;
};
type Overview = {
  server: { name: string; public_ip: string; city: string; country: string; country_code: string; uptime_s: number };
  resources: { load1: number; cpu_percent: number; cpu_count: number; memory_total: number; memory_available: number; disk_total: number; disk_available: number; network_rx: number; network_tx: number };
  protocols: Record<"wg" | "awg", { interface: string; port: number; active: boolean }>;
};
type ApplicationStatus = {
  api: { active: boolean; enabled: boolean };
  containers: Array<{
    Name?: string; Service?: string; State?: string; Status?: string; Health?: string;
    component_name?: string; purpose?: string; healthy?: boolean; status_text?: string;
  }>;
  action: {
    unit?: string; action?: string; state?: string; result?: string; started_at?: string; updated_at?: string;
    progress?: number; message?: string;
  };
  service_mode?: { active: boolean; rollback_available?: boolean };
  runtime?: { mode: "systemd" | "legacy-docker" | "incomplete"; migration_required: boolean };
};
type ProtocolImage = {
  id: string; name: string; version: string; description: string; category: string; category_name: string;
  interface: string; installed: boolean; active?: boolean; removable: boolean;
};
type AutomationSchedule = {
  enabled: boolean; cadence: "daily" | "weekly" | "monthly"; weekday: string; hour: number; minute: number;
};
type ServicesStatus = {
  items: Array<{
    id: string; name: string; unit: string; installed: boolean; active: boolean; state: string; substate: string;
    enabled: boolean; unit_file_state: string; restarts: number; active_since: string; description: string;
    controls: string[]; disabled_controls?: string[];
  }>;
  failed_units: number;
  reboot_required: boolean;
  automation: { reboot: AutomationSchedule; cleanup: AutomationSchedule; update: AutomationSchedule };
  timers: Record<"reboot" | "cleanup" | "update", { installed: boolean; active: boolean; last_trigger: string; next_run: string }>;
  panel_access?: { mode: "external" | "vpn"; public: boolean; vpn_urls: string[] };
  service_mode?: { active: boolean };
  logging?: { persistent: boolean; retention_days: number; automatic_cleanup: boolean; disk_usage: string };
};
type LiveStatus = {
  resources: Overview["resources"];
  protocols: Record<"wg" | "awg", {
    active: boolean; peers: number; online_peers: number; interface_rx_bytes: number; interface_tx_bytes: number;
  }>;
  clients: Client[];
  security: { firewall_active: boolean; fail2ban_active: boolean; ssh_listening: boolean };
};
type LoggingSettings = { persistent: boolean; retention_days: number };
type ConfirmationRequest = {
  title: string; message: string; confirmLabel: string; phrase?: string; danger?: boolean;
  resolve: (confirmed: boolean) => void;
};
const appVersion = process.env.NEXT_PUBLIC_APP_VERSION || "v1.0.0";
const buildCommit = process.env.NEXT_PUBLIC_BUILD_COMMIT || "unknown";
type ProtocolStatus = {
  protocol: Protocol; interface: string; active: boolean; service_active: boolean; service_enabled: boolean;
  active_since: string; address: string; listen_port: number; mtu: number; peers: number; online_peers: number;
  endpoints: number; last_handshake_age_s?: number; peer_rx_bytes: number; peer_tx_bytes: number;
  interface_rx_bytes: number; interface_tx_bytes: number; rx_errors: number; tx_errors: number;
  rx_dropped: number; tx_dropped: number;
  unit?: string; transport?: string; security?: string; target?: string;
  resources: {
    checked_at?: string;
    items: Array<{ name: string; available: boolean; status_code?: number; latency_ms: number }>;
  };
  history: {
    period_hours: number; samples: number; availability_percent?: number; monitoring_gaps: number;
    service_interruptions: number; inactive_connection_periods: number; external_loss_percent?: number;
    latency_avg_ms?: number; latency_max_ms?: number; jitter_avg_ms?: number;
    interface_errors: number; interface_dropped: number; uplink_errors: number; uplink_dropped: number; conntrack_peak_percent?: number;
    received_bytes: number; transmitted_bytes: number;
    average_rx_bps: number; average_tx_bps: number; peak_rx_bps: number; peak_tx_bps: number;
    events: Array<{ at?: string; type: "monitor_gap" | "service_down" | "peers_offline"; seconds?: number }>;
  };
  diagnostics: {
    checked_at?: string; status: "healthy" | "warning" | "critical" | "pending"; score?: number;
    live?: { loss_percent?: number; latency_ms?: number; jitter_ms?: number; dns_ms?: number; https_connect_ms?: number; https_total_ms?: number };
    network?: { uplink?: string; gateway?: string; uplink_mtu?: number; tunnel_mtu?: number; conntrack_count?: number; conntrack_max?: number; conntrack_percent?: number };
    checks: Array<{ id: string; name: string; ok: boolean; value: string }>;
    findings: Array<{ severity: "warning" | "critical"; code: string; title: string; detail: string; action: string }>;
  };
};

const labels: Record<Tab | Protocol, string> = {
  overview: "Обзор", security: "Безопасность", application: "Приложение", services: "Службы", wg: "WireGuard", awg: "AmneziaWG", shadowsocks: "Shadowsocks", "vless-reality-xhttp": "VLESS + REALITY + XHTTP", clients: "Подключения",
};
const navigationLabels: Record<Tab, string> = {
  overview: "OVERVIEW", security: "SECURITY", application: "APPLICATION", services: "SERVICES",
  wg: "WIREGUARD", awg: "AMNEZIAWG", shadowsocks: "SHADOWSOCKS", "vless-reality-xhttp": "VLESS", clients: "CONNECTIONS",
};
const actionLabels: Record<string, string> = {
  install: "Установка 312.net", start: "Запуск приложения", stop: "Остановка приложения",
  restart: "Перезапуск приложения", update: "Обновление приложения", "test-update": "Переход на тестовую версию", "test-rollback": "Возврат к рабочей версии", "network-check": "Проверка сети и туннелей", identity: "Обновление данных сервера",
  "integrity-check": "Проверка целостности",
  secure: "Настройка защиты", "kernel-update": "Обновление ядра", "vpn-firewall": "Восстановление VPN firewall", optimize: "Оптимизация ресурсов",
  "service-mode": "Переключение режима и ветки",
  reboot: "Перезагрузка сервера", poweroff: "Выключение сервера",
  "protocol-install": "Установка протокола", "protocol-remove": "Удаление протокола",
};

const bytes = (value = 0) => {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
};

function reloadWithoutCache(message: string) {
  sessionStorage.setItem("312-notice", message);
  const target = new URL(window.location.href);
  target.searchParams.set("_refresh", Date.now().toString());
  window.location.replace(target.toString());
}
const duration = (seconds?: number) => {
  if (seconds === undefined || seconds === null) return "никогда";
  if (seconds < 60) return `${seconds} сек назад`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`;
  return `${Math.floor(seconds / 3600)} ч назад`;
};
const uptime = (seconds = 0) => `${Math.floor(seconds / 86400)}д ${Math.floor((seconds % 86400) / 3600)}ч`;
const LIVE_SAMPLE_SECONDS = 3;
const HISTORY_SAMPLES = 100;
const CLIENTS_PER_PAGE = 10;
const appendSample = (values: number[], value: number) => [...values, Math.max(0, value)].slice(-HISTORY_SAMPLES);
export default function Home() {
  const [tab, setTab] = useState<Tab>("overview");
  const [token, setToken] = useState("");
  const [loginUser, setLoginUser] = useState("admin");
  const [loginPassword, setLoginPassword] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [clients, setClients] = useState<Client[]>([]);
  const [security, setSecurity] = useState<Record<string, unknown> | null>(null);
  const [securityLoading, setSecurityLoading] = useState(false);
  const [securityLogSource, setSecurityLogSource] = useState<"ssh" | "firewall" | "system">("ssh");
  const [securityLogs, setSecurityLogs] = useState<string[]>([]);
  const [securityLogsUpdatedAt, setSecurityLogsUpdatedAt] = useState<Date | null>(null);
  const [securityNewLogCount, setSecurityNewLogCount] = useState(0);
  const [application, setApplication] = useState<ApplicationStatus | null>(null);
  const [services, setServices] = useState<ServicesStatus | null>(null);
  const [automationDraft, setAutomationDraft] = useState<ServicesStatus["automation"] | null>(null);
  const [loggingDraft, setLoggingDraft] = useState<LoggingSettings | null>(null);
  const [notice, setNotice] = useState("");
  const [applicationLogs, setApplicationLogs] = useState<string[]>([]);
  const [securityLogsOpen, setSecurityLogsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [moduleMenuOpen, setModuleMenuOpen] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [networkRate, setNetworkRate] = useState({ rx: 0, tx: 0 });
  const [resourceHistory, setResourceHistory] = useState<ResourceHistory>({ load: [], memory: [], disk: [], rx: [], tx: [] });
  const [protocolImages, setProtocolImages] = useState<ProtocolImage[]>([]);
  const [protocolStatuses, setProtocolStatuses] = useState<Partial<Record<Protocol, ProtocolStatus>>>({});
  const [protocolRates, setProtocolRates] = useState<Partial<Record<Protocol, { rx: number; tx: number }>>>({});
  const [installingProtocol, setInstallingProtocol] = useState("");
  const [checkingResources, setCheckingResources] = useState<Protocol | null>(null);
  const [checkingDiagnostics, setCheckingDiagnostics] = useState<Protocol | null>(null);
  const [resourcesOpen, setResourcesOpen] = useState<Partial<Record<Protocol, boolean>>>({});
  const [diagnosticsOpen, setDiagnosticsOpen] = useState<Partial<Record<Protocol, boolean>>>({});
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [passwordDialog, setPasswordDialog] = useState(false);
  const [clientDialog, setClientDialog] = useState(false);
  const [clientPage, setClientPage] = useState(1);
  const [currentAdminPassword, setCurrentAdminPassword] = useState("");
  const [newAdminPassword, setNewAdminPassword] = useState("");
  const [confirmAdminPassword, setConfirmAdminPassword] = useState("");
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null);
  const [confirmationInput, setConfirmationInput] = useState("");
  const [newClient, setNewClient] = useState({ name: "", protocol: "wg" as Protocol });
  const [generated, setGenerated] = useState("");
  const [generatedName, setGeneratedName] = useState("client.conf");
  const [generatedQr, setGeneratedQr] = useState("");
  const [generatedQrError, setGeneratedQrError] = useState("");
  const settingsRef = useRef<HTMLDivElement>(null);
  const networkSample = useRef<{ rx: number; tx: number; at: number } | null>(null);
  const protocolSamples = useRef<Partial<Record<Protocol, { rx: number; tx: number; at: number }>>>({});
  const securityLogHeads = useRef<Partial<Record<"ssh" | "firewall" | "system", string>>>({});
  const automationDirty = useRef(false);
  const loggingDirty = useRef(false);
  const trackedActionUnit = useRef("");
  const liveRequestInFlight = useRef(false);

  useEffect(() => {
    // Restore browser-only credentials after hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setToken(sessionStorage.getItem("312-token") || "");
    const savedNotice = sessionStorage.getItem("312-notice");
    if (savedNotice) {
      setNotice(savedNotice);
      sessionStorage.removeItem("312-notice");
    }
  }, []);

  useEffect(() => {
    function closeSettings(event: PointerEvent) {
      if (settingsOpen && settingsRef.current && !settingsRef.current.contains(event.target as Node)) setSettingsOpen(false);
      if (moduleMenuOpen && !(event.target as Element).closest(".moduleMenuWrap")) setModuleMenuOpen("");
    }
    document.addEventListener("pointerdown", closeSettings);
    return () => document.removeEventListener("pointerdown", closeSettings);
  }, [moduleMenuOpen, settingsOpen]);

  const request = useCallback(async (path: string, init?: RequestInit) => {
    const response = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", Authorization: `Basic ${token}`, ...(init?.headers || {}) },
    });
    if (!response.ok) {
      const raw = await response.text();
      let detail = raw;
      try { detail = (JSON.parse(raw) as { detail?: string }).detail || raw; } catch { /* Plain-text API error. */ }
      throw new Error(response.status === 401 ? "Неверный токен администратора" : detail || `Ошибка ${response.status}`);
    }
    return response.json();
  }, [token]);


  function askConfirmation(options: Omit<ConfirmationRequest, "resolve">): Promise<boolean> {
    setConfirmationInput("");
    return new Promise((resolve) => setConfirmation({ ...options, resolve }));
  }

  function closeConfirmation(confirmed: boolean) {
    const current = confirmation;
    if (!current) return;
    setConfirmation(null);
    setConfirmationInput("");
    current.resolve(confirmed);
  }

  const loadOverview = useCallback(async () => {
    if (!token) return;
    try {
      const [next, imageData] = await Promise.all([
        request("/overview") as Promise<Overview>,
        request("/protocol-images") as Promise<{ items: ProtocolImage[] }>,
      ]);
      setOverview(next);
      setProtocolImages(imageData.items || []);
      if (installingProtocol && imageData.items.some((image) => image.id === installingProtocol && image.installed)) {
        setInstallingProtocol("");
      }
      setLastUpdated(new Date());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Ошибка соединения"); }
  }, [installingProtocol, request, token]);

  const loadClients = useCallback(async () => {
    if (!token) return;
    try {
      const data = await request("/clients");
      setClients(data.items); setLastUpdated(new Date());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось обновить клиентов"); }
  }, [request, token]);

  const loadSecurity = useCallback(async () => {
    if (!token) return;
    setSecurityLoading(true);
    try {
      setSecurity(await request("/security")); setLastUpdated(new Date());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось обновить состояние безопасности"); }
    finally { setSecurityLoading(false); }
  }, [request, token]);

  const loadApplication = useCallback(async () => {
    if (!token) return;
    try {
      setApplication(await request("/application/status")); setLastUpdated(new Date());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось обновить приложение"); }
  }, [request, token]);

  const loadServices = useCallback(async () => {
    if (!token) return;
    try {
      const next = await request("/services") as ServicesStatus;
      setServices(next);
      if (!automationDirty.current) setAutomationDraft(next.automation);
      if (!loggingDirty.current) setLoggingDraft({
        persistent: next.logging?.persistent ?? true,
        retention_days: next.logging?.retention_days ?? 30,
      });
      setLastUpdated(new Date());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось обновить состояние служб"); }
  }, [request, token]);

  const loadProtocolStatus = useCallback(async (protocol: Protocol) => {
    if (!token) return;
    try {
      const next = await request(`/protocols/${protocol}/status`) as ProtocolStatus;
      const now = Date.now();
      const previous = protocolSamples.current[protocol];
      if (previous) {
        const seconds = Math.max((now - previous.at) / 1000, 0.1);
        setProtocolRates((rates) => ({ ...rates, [protocol]: {
          rx: Math.max(0, (next.interface_rx_bytes - previous.rx) / seconds),
          tx: Math.max(0, (next.interface_tx_bytes - previous.tx) / seconds),
        } }));
      }
      protocolSamples.current[protocol] = { rx: next.interface_rx_bytes, tx: next.interface_tx_bytes, at: now };
      setProtocolStatuses((statuses) => ({ ...statuses, [protocol]: next }));
      setLastUpdated(new Date());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось обновить состояние протокола"); }
  }, [request, token]);

  const refreshCurrent = useCallback(async (showBusy = false) => {
    if (!token) return;
    if (showBusy) setBusy(true);
    setError("");
    try {
      if (tab === "overview") await Promise.all([loadOverview(), loadClients(), loadApplication(), loadServices()]);
      else if (tab === "security") await Promise.all([loadSecurity(), loadServices()]);
      else if (tab === "application") await loadApplication();
      else if (tab === "services") await loadServices();
      else if (["wg", "awg", "shadowsocks", "vless-reality-xhttp"].includes(tab)) await Promise.all([loadClients(), loadProtocolStatus(tab as Protocol)]);
      else await loadClients();
    } finally {
      if (showBusy) setBusy(false);
    }
  }, [loadApplication, loadClients, loadOverview, loadProtocolStatus, loadSecurity, loadServices, tab, token]);

  useEffect(() => {
    if (!token) return;
    sessionStorage.setItem("312-token", token);
    // Load the minimum shared data required to construct the first screen and navigation.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void Promise.all([loadOverview(), loadClients(), loadApplication(), loadServices()]);
  }, [loadApplication, loadClients, loadOverview, loadServices, token]);

  useEffect(() => {
    if (!token || !autoRefresh) return;
    // Security data is intentionally refreshed only when entering the tab;
    // its checks are expensive and the page displays the last known snapshot.
    if (tab === "security") return;
    const actionRunning = ["active", "activating", "running"].includes(application?.action?.state || "");
    const updateRunning = actionRunning && ["update", "test-update", "test-rollback", "kernel-update"].includes(application?.action?.action || "");
    const delay = updateRunning ? 3000
      : tab === "overview" ? 30000
        : ["wg", "awg", "shadowsocks", "vless-reality-xhttp", "clients"].includes(tab) ? 15000
          : 10000;
    const timer = window.setInterval(() => void refreshCurrent(false), delay);
    return () => window.clearInterval(timer);
  }, [application?.action?.action, application?.action?.state, autoRefresh, refreshCurrent, tab, token]);

  useEffect(() => {
    const actionRunning = ["active", "activating", "running"].includes(application?.action?.state || "");
    if (!token || !actionRunning) return;
    const timer = window.setInterval(() => void loadApplication(), 2000);
    return () => window.clearInterval(timer);
  }, [application?.action?.state, autoRefresh, loadApplication, token]);

  useEffect(() => {
    const action = application?.action;
    if (!action?.unit) return;
    if (["active", "activating", "running"].includes(action.state || "")) {
      trackedActionUnit.current = action.unit;
      return;
    }
    if (trackedActionUnit.current !== action.unit || !["succeeded", "finished", "failed"].includes(action.state || "")) return;
    trackedActionUnit.current = "";
    const label = actionLabels[(action.action || "").split(":")[0]] || "Операция";
    const timer = window.setTimeout(() => {
      if (action.state === "failed" || action.result === "failed") {
        setError(`${label}: выполнение завершилось с ошибкой`);
        return;
      }
      const message = `${label}: успешно завершено`;
      if (["update", "test-update", "test-rollback", "kernel-update"].includes(action.action || "")) {
        window.setTimeout(() => reloadWithoutCache(`${message}. Кэш интерфейса сброшен`), 600);
        return;
      }
      setNotice(message);
      void refreshCurrent(false);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [application?.action, refreshCurrent]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 8000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!token || tab === "overview") return;
    // Synchronize only the newly opened module.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refreshCurrent(false);
  }, [refreshCurrent, tab, token]);

  const loadSecurityLogs = useCallback(async () => {
    if (!token) return;
    try {
      const data = await request(`/security/logs?source=${securityLogSource}&lines=160`);
      const nextLines = (data.lines || []) as string[];
      const previousHead = securityLogHeads.current[securityLogSource];
      const previousPosition = previousHead ? nextLines.indexOf(previousHead) : 0;
      setSecurityNewLogCount(previousHead && previousPosition !== 0 ? (previousPosition > 0 ? previousPosition : nextLines.length) : 0);
      securityLogHeads.current[securityLogSource] = nextLines[0] || "";
      setSecurityLogs(nextLines);
      setSecurityLogsUpdatedAt(new Date());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось загрузить журнал"); }
  }, [request, securityLogSource, token]);

  const loadApplicationLogs = useCallback(async () => {
    if (!token) return;
    try {
      const data = await request("/application/logs?lines=180");
      setApplicationLogs(data.lines || []);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось загрузить журнал приложения"); }
  }, [request, token]);

  const loadLiveStatus = useCallback(async () => {
    if (!token || liveRequestInFlight.current || document.visibilityState !== "visible") return;
    liveRequestInFlight.current = true;
    try {
      const next = await request("/live-status") as LiveStatus;
      setError("");
      const now = Date.now();
      const previous = networkSample.current;
      let nextRxRate = 0;
      let nextTxRate = 0;
      if (previous) {
        const seconds = Math.max((now - previous.at) / 1000, 0.1);
        nextRxRate = Math.max(0, (next.resources.network_rx - previous.rx) / seconds);
        nextTxRate = Math.max(0, (next.resources.network_tx - previous.tx) / seconds);
        setNetworkRate({ rx: nextRxRate, tx: nextTxRate });
      }
      const memoryUsed = next.resources.memory_total ? 100 - next.resources.memory_available / next.resources.memory_total * 100 : 0;
      const diskUsed = next.resources.disk_total ? 100 - next.resources.disk_available / next.resources.disk_total * 100 : 0;
      setResourceHistory((history) => ({
        load: appendSample(history.load, next.resources.cpu_percent || 0),
        memory: appendSample(history.memory, memoryUsed),
        disk: appendSample(history.disk, diskUsed),
        rx: previous ? appendSample(history.rx, nextRxRate) : history.rx,
        tx: previous ? appendSample(history.tx, nextTxRate) : history.tx,
      }));
      networkSample.current = { rx: next.resources.network_rx, tx: next.resources.network_tx, at: now };
      setOverview((current) => current ? {
        ...current,
        resources: next.resources,
        protocols: {
          wg: { ...current.protocols.wg, active: next.protocols.wg.active },
          awg: { ...current.protocols.awg, active: next.protocols.awg.active },
        },
      } : current);
      setClients((current) => next.clients.map((client) => ({
        ...current.find((existing) => existing.id === client.id && existing.protocol === client.protocol),
        ...client,
      })));
      setProtocolStatuses((current) => {
        const updated = { ...current };
        (["wg", "awg"] as TunnelProtocol[]).forEach((protocol) => {
          if (updated[protocol]) updated[protocol] = { ...updated[protocol]!, ...next.protocols[protocol] };
        });
        return updated;
      });
      setSecurity((current) => current ? {
        ...current,
        firewall: { ...((current.firewall as Record<string, unknown> | undefined) || {}), active: next.security.firewall_active },
        fail2ban: { ...((current.fail2ban as Record<string, unknown> | undefined) || {}), active: next.security.fail2ban_active },
        ssh: { ...((current.ssh as Record<string, unknown> | undefined) || {}), active: next.security.ssh_listening },
      } : current);
      setLastUpdated(new Date());
    } catch {
      // Full module refresh reports persistent errors; live telemetry stays silent.
    } finally {
      liveRequestInFlight.current = false;
    }
  }, [request, token]);

  useEffect(() => {
    if (!token || !autoRefresh || !["overview", "clients", "wg", "awg", "security"].includes(tab)) return;
    const initial = window.setTimeout(() => void loadLiveStatus(), 0);
    const timer = window.setInterval(() => void loadLiveStatus(), LIVE_SAMPLE_SECONDS * 1000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [autoRefresh, loadLiveStatus, tab, token]);

  useEffect(() => {
    if (!token || tab !== "security" || !securityLogsOpen) return;
    const timer = window.setTimeout(() => void loadSecurityLogs(), 0);
    return () => window.clearTimeout(timer);
  }, [loadSecurityLogs, securityLogsOpen, tab, token]);

  useEffect(() => {
    if (!token || tab !== "application") return;
    const timer = window.setTimeout(() => void loadApplicationLogs(), 0);
    return () => window.clearTimeout(timer);
  }, [loadApplicationLogs, tab, token]);

  useEffect(() => {
    if (!autoRefresh || tab !== "security" || !securityLogsOpen) return;
    const timer = window.setInterval(() => void loadSecurityLogs(), 5000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, tab, securityLogsOpen, loadSecurityLogs]);

  useEffect(() => {
    if (!autoRefresh || tab !== "application") return;
    const timer = window.setInterval(() => void loadApplicationLogs(), 5000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, tab, loadApplicationLogs]);

  async function runApplicationAction(action: ApplicationAction, automatic = false) {
    if (action === "poweroff" && !await askConfirmation({
      title: "Выключить сервер?",
      message: "Сервер, VPN-каналы и панель станут недоступны до запуска через кабинет провайдера.",
      confirmLabel: "Выключить сервер", phrase: "ВЫКЛЮЧИТЬ", danger: true,
    })) return;
    const risky = action === "restart" || action === "update" || action === "test-update" || action === "test-rollback" || action === "identity" || action === "kernel-update" || action === "reboot";
    if (!automatic && risky && !await askConfirmation({
      title: actionLabels[action] || "Выполнить команду?",
      message: `Будет выполнена команда «vps-control ${action}». Во время операции возможен кратковременный перерыв в работе.`,
      confirmLabel: "Выполнить", danger: action === "reboot",
    })) return;
    setBusy(true); setError("");
    try {
      const started = await request("/application/action", { method: "POST", body: JSON.stringify({ action }) });
      setApplication((current) => ({
        api: current?.api || { active: true, enabled: true },
        containers: current?.containers || [],
        action: started,
        service_mode: current?.service_mode,
        runtime: current?.runtime,
      }));
      if (action === "reboot" || action === "poweroff") return;
      await loadApplication(); await loadApplicationLogs();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Команда не запущена"); }
    finally { setBusy(false); }
  }

  async function runServiceAction(serviceId: string, serviceName: string, action: "start" | "stop" | "restart") {
    if (action === "stop" && serviceId === "ssh") {
      if (!await askConfirmation({
        title: "Остановить SSH?",
        message: "Все SSH-соединения будут разорваны. Восстановление возможно через эту панель или после перезагрузки сервера.",
        confirmLabel: "Остановить SSH", phrase: "ОТКЛЮЧИТЬ SSH", danger: true,
      })) return;
    } else if (action === "stop") {
      if (!await askConfirmation({
        title: `Остановить «${serviceName}»?`,
        message: "Связанный функционал станет недоступен до повторного запуска службы.",
        confirmLabel: "Остановить", danger: true,
      })) return;
    } else if (action === "restart" && !await askConfirmation({
      title: `Перезапустить «${serviceName}»?`,
      message: "Во время перезапуска возможен кратковременный перерыв в работе.",
      confirmLabel: "Перезапустить",
    })) return;
    setBusy(true); setError("");
    try {
      await request(`/services/${serviceId}/action`, { method: "POST", body: JSON.stringify({ action }) });
      await Promise.all([loadServices(), loadSecurity()]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось выполнить действие со службой"); }
    finally { setBusy(false); }
  }

  async function fixSecurity(action: "secure" | "kernel-update" | "vpn-firewall") {
    await runApplicationAction(action);
    await loadSecurity();
  }

  function closePasswordDialog() {
    setPasswordDialog(false);
    setCurrentAdminPassword("");
    setNewAdminPassword("");
    setConfirmAdminPassword("");
  }

  function openClientDialog() {
    setGenerated(""); setGeneratedQr(""); setGeneratedQrError(""); setError(""); setClientDialog(true);
  }

  function closeClientDialog() {
    if (busy) return;
    setClientDialog(false); setGenerated(""); setGeneratedQr(""); setGeneratedQrError("");
  }

  function resetClientDialog() {
    setGenerated(""); setGeneratedQr(""); setGeneratedQrError(""); setError("");
  }

  async function changeAdminPassword(event: FormEvent) {
    event.preventDefault();
    if (!currentAdminPassword) { setError("Введите текущий пароль"); return; }
    if (newAdminPassword.length < 16 || newAdminPassword.length > 128) { setError("Новый пароль должен содержать от 16 до 128 символов"); return; }
    if (!/^[!-~]+$/.test(newAdminPassword)) { setError("Используйте печатные латинские символы без пробелов"); return; }
    const passwordCategories = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((pattern) => pattern.test(newAdminPassword)).length;
    if (passwordCategories < 3) { setError("Добавьте минимум три группы: строчные, заглавные, цифры и спецсимволы"); return; }
    if (newAdminPassword === currentAdminPassword) { setError("Новый пароль должен отличаться от текущего"); return; }
    if (newAdminPassword !== confirmAdminPassword) { setError("Новые пароли не совпадают"); return; }
    setBusy(true); setError("");
    try {
      await request("/security/admin-password", { method: "PUT", body: JSON.stringify({ current_password: currentAdminPassword, new_password: newAdminPassword, confirm_password: confirmAdminPassword }) });
      sessionStorage.removeItem("312-token");
      closePasswordDialog(); setToken(""); setLoginPassword("");
      setNotice("Пароль изменён. Войдите заново с новым паролем.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось изменить пароль"); }
    finally { setBusy(false); }
  }

  function updateAutomation(kind: "reboot" | "cleanup", patch: Partial<AutomationSchedule>) {
    automationDirty.current = true;
    setAutomationDraft((current) => current ? {
      ...current, [kind]: { ...current[kind], ...patch },
    } : current);
  }

  async function saveAutomation() {
    if (!automationDraft) return;
    setBusy(true); setError("");
    try {
      await request("/services/automation", { method: "PUT", body: JSON.stringify(automationDraft) });
      automationDirty.current = false;
      await Promise.all([loadServices(), loadApplication()]);
      setNotice("Расписание обслуживания сохранено и применено");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось сохранить расписания"); }
    finally { setBusy(false); }
  }

  async function changePanelAccess(mode: "external" | "vpn") {
    const message = mode === "vpn"
      ? "Закрыть публичный доступ? Панель останется доступна только через защищённый туннель по локальным адресам. Текущее подключение может завершиться."
      : "Открыть публичный доступ к панели из интернета?";
    if (!await askConfirmation({
      title: mode === "vpn" ? "Ограничить доступ к панели?" : "Открыть публичный доступ?",
      message, confirmLabel: mode === "vpn" ? "Оставить защищённый доступ" : "Открыть доступ",
      danger: mode === "external",
    })) return;
    setBusy(true); setError("");
    try {
      await request("/services/panel-access", { method: "PUT", body: JSON.stringify({ mode }) });
      setServices((current) => current ? {
        ...current, panel_access: { vpn_urls: current.panel_access?.vpn_urls || [], mode, public: mode === "external" },
      } : current);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось изменить доступ к панели"); }
    finally { setBusy(false); }
  }

  async function changeServiceMode(active: boolean) {
    if (!await askConfirmation({
      title: active ? "Включить сервисный режим?" : "Завершить сервисный режим?",
      message: active
        ? "Будет безопасно развёрнута ветка service. Панель станет публичной, SSH будет запущен, фоновые проверки и автоматические задачи будут приостановлены."
        : "Будет восстановлена стабильная версия stabl, сохранённая перед переходом на main. После успешной проверки восстановятся доступ, SSH и автоматические задачи.",
      confirmLabel: active ? "Включить режим" : "Завершить обслуживание",
      danger: active,
    })) return;
    const autoRefreshAfterChange = autoRefresh;
    setBusy(true); setError("");
    try {
      setAutoRefresh(false);
      await request("/services/service-mode", { method: "PUT", body: JSON.stringify({ active }) });
      let confirmed = false;
      for (let attempt = 0; attempt < 36; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 5000));
        try {
          const [nextServices, nextApplication] = await Promise.all([
            request("/services"), request("/application/status"),
          ]);
          setServices(nextServices);
          setApplication(nextApplication);
          const modeActionFinished = nextApplication?.action?.action === "service-mode"
            && ["succeeded", "finished"].includes(nextApplication?.action?.state || "");
          if (Boolean(nextServices?.service_mode?.active) === active && modeActionFinished) {
            confirmed = true;
            break;
          }
        } catch {
          // API and gateway may briefly restart while the selected branch is deployed.
        }
      }
      if (!confirmed) throw new Error("Сервер не подтвердил завершение переключения режима");
      setAutoRefresh(autoRefreshAfterChange);
      reloadWithoutCache(`Сервисный режим ${active ? "включён" : "выключен"}. Кэш интерфейса сброшен`);
    } catch (cause) {
      setAutoRefresh(autoRefreshAfterChange);
      setError(cause instanceof Error ? cause.message : "Не удалось изменить сервисный режим");
    } finally { setBusy(false); }
  }

  async function waitForProtocolState(image: ProtocolImage, installed: boolean) {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 5000));
      try {
        const [imageData, status] = await Promise.all([
          request("/protocol-images") as Promise<{ items: ProtocolImage[] }>,
          request("/application/status") as Promise<ApplicationStatus>,
        ]);
        setProtocolImages(imageData.items || []);
        setApplication(status);
        const current = imageData.items?.find((item) => item.id === image.id);
        const actionState = status.action?.state || "";
        // The observed module state is authoritative. A transient systemd unit
        // can disappear immediately after completing and briefly look failed.
        if (Boolean(current?.installed) === installed) return;
        if (actionState === "failed" || status.action?.result === "failed") {
          throw new Error(`${installed ? "Установка" : "Удаление"} ${image.name} завершилось с ошибкой`);
        }
      } catch (cause) {
        if (cause instanceof Error && cause.message.includes("завершилось с ошибкой")) throw cause;
        // API may restart briefly after installing or removing a module.
      }
    }
    throw new Error(`Сервер не подтвердил ${installed ? "установку" : "удаление"} ${image.name} за 10 минут`);
  }

  async function installProtocol(image: ProtocolImage) {
    if (!await askConfirmation({
      title: `Установить ${image.name}?`,
      message: `На сервер будет установлен модуль ${image.name} ${image.version}.`,
      confirmLabel: "Установить",
    })) return;
    setBusy(true); setError(""); setInstallingProtocol(image.id);
    try {
      const started = await request(`/protocol-images/${image.id}/install`, { method: "POST" });
      setApplication((current) => ({
        api: current?.api || { active: true, enabled: true },
        containers: current?.containers || [],
        action: started,
      }));
      await waitForProtocolState(image, true);
      await Promise.all([loadOverview(), loadClients(), loadProtocolStatus(image.id as Protocol)]);
      setNotice(`${image.name} установлен и готов к работе`);
    } catch (cause) {
      setInstallingProtocol("");
      setError(cause instanceof Error ? cause.message : "Не удалось запустить установку протокола");
    } finally { setInstallingProtocol(""); setBusy(false); }
  }

  async function restartProtocol(protocol: Protocol) {
    if (!await askConfirmation({
      title: `Перезапустить ${labels[protocol]}?`,
      message: "Активные VPN-соединения кратковременно прервутся.",
      confirmLabel: "Перезапустить",
    })) return;
    setBusy(true); setError("");
    try {
      await request(`/protocols/${protocol}/restart`, { method: "POST" });
      await Promise.all([loadProtocolStatus(protocol), loadOverview()]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось перезапустить протокол"); }
    finally { setBusy(false); }
  }

  async function checkProtocolResources(protocol: Protocol) {
    setCheckingResources(protocol); setError("");
    try {
      const resources = await request(`/protocols/${protocol}/resources/check`, { method: "POST" });
      setProtocolStatuses((statuses) => {
        const current = statuses[protocol];
        return current ? { ...statuses, [protocol]: { ...current, resources } } : statuses;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось проверить доступность ресурсов");
    } finally {
      setCheckingResources(null);
    }
  }

  function updateLoggingDraft(patch: Partial<LoggingSettings>) {
    loggingDirty.current = true;
    setLoggingDraft((current) => ({ ...(current || { persistent: true, retention_days: 30 }), ...patch }));
  }

  async function saveLoggingSettings() {
    if (!loggingDraft) return;
    if (!loggingDraft.persistent && !await askConfirmation({
      title: "Отключить постоянную запись логов?",
      message: "Новые системные журналы будут храниться только в оперативной памяти и исчезнут после перезагрузки. Диагностика прошлых событий станет ограниченной.",
      confirmLabel: "Отключить запись", danger: true,
    })) return;
    setBusy(true); setError("");
    try {
      await request("/services/logging", {
        method: "PUT",
        body: JSON.stringify(loggingDraft),
      });
      loggingDirty.current = false;
      await loadServices();
      setNotice("Настройки записи и хранения журналов сохранены");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось сохранить настройки журналов"); }
    finally { setBusy(false); }
  }

  async function clearManagedLogs() {
    if (!await askConfirmation({
      title: "Очистить все управляемые журналы?",
      message: "Будут удалены системные журналы, логи контейнеров и история мониторинга WG/AWG. Действие нельзя отменить.",
      confirmLabel: "Очистить журналы", phrase: "ОЧИСТИТЬ ЛОГИ", danger: true,
    })) return;
    setBusy(true); setError("");
    try {
      await request("/services/logging/clear", { method: "POST" });
      setSecurityLogs([]); setApplicationLogs([]);
      await loadServices();
      setNotice("Управляемые журналы очищены");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось очистить журналы"); }
    finally { setBusy(false); }
  }

  async function checkNetworkDiagnostics(protocol: Protocol) {
    setCheckingDiagnostics(protocol); setError("");
    try {
      const diagnostics = await request(`/protocols/${protocol}/diagnostics/check`, { method: "POST" });
      setProtocolStatuses((statuses) => {
        const current = statuses[protocol];
        return current ? { ...statuses, [protocol]: { ...current, diagnostics } } : statuses;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось выполнить полную диагностику сети");
    } finally {
      setCheckingDiagnostics(null);
    }
  }

  function toggleNetworkDiagnostics(protocol: Protocol) {
    const opening = !diagnosticsOpen[protocol];
    setDiagnosticsOpen((values) => ({ ...values, [protocol]: opening }));
    if (opening) void checkNetworkDiagnostics(protocol);
  }

  function toggleProtocolResources(protocol: Protocol) {
    const opening = !resourcesOpen[protocol];
    setResourcesOpen((values) => ({ ...values, [protocol]: opening }));
    if (opening) void checkProtocolResources(protocol);
  }

  async function removeProtocol(image: ProtocolImage) {
    if (!await askConfirmation({
      title: `Удалить ${image.name}?`,
      message: "Будут удалены модуль, его конфигурация и все подключения. Образ останется доступен для повторной установки.",
      confirmLabel: "Удалить модуль", phrase: "УДАЛИТЬ", danger: true,
    })) return;
    setBusy(true); setError(""); setInstallingProtocol(`remove-${image.id}`);
    try {
      const started = await request(`/protocol-images/${image.id}`, { method: "DELETE" });
      setApplication((current) => ({
        api: current?.api || { active: true, enabled: true },
        containers: current?.containers || [],
        action: started,
      }));
      setTab("overview");
      await waitForProtocolState(image, false);
      await Promise.all([loadOverview(), loadClients(), loadServices()]);
      setNotice(`${image.name} удалён`);
    } catch (cause) {
      setInstallingProtocol("");
      setError(cause instanceof Error ? cause.message : "Не удалось запустить удаление протокола");
    } finally { setInstallingProtocol(""); setBusy(false); }
  }

  const protocolClients = useMemo(
    () => tab === "wg" || tab === "awg" ? clients.filter((client) => client.protocol === tab) : clients,
    [clients, tab],
  );
  const clientPageCount = Math.max(1, Math.ceil(protocolClients.length / CLIENTS_PER_PAGE));
  const currentClientPage = Math.min(clientPage, clientPageCount);
  const visibleClients = protocolClients.slice((currentClientPage - 1) * CLIENTS_PER_PAGE, currentClientPage * CLIENTS_PER_PAGE);
  const visibleClientStart = protocolClients.length ? (currentClientPage - 1) * CLIENTS_PER_PAGE + 1 : 0;
  const visibleClientEnd = Math.min(currentClientPage * CLIENTS_PER_PAGE, protocolClients.length);
  const installedProtocols = useMemo(
    () => protocolImages.filter((image) => image.installed && (["wg", "awg", "shadowsocks", "vless-reality-xhttp"] as string[]).includes(image.id)).map((image) => image.id as Protocol),
    [protocolImages],
  );
  const protocolCategories = useMemo(() => {
    const groups = new Map<string, { id: string; name: string; images: ProtocolImage[] }>();
    for (const image of protocolImages) {
      const id = image.category || "network";
      const group = groups.get(id) || { id, name: image.category_name || "Сетевые модули", images: [] };
      group.images.push(image);
      groups.set(id, group);
    }
    return [...groups.values()];
  }, [protocolImages]);
  const navigation = useMemo(
    () => (["overview", "clients"] as Tab[]).filter((id) => id !== "clients" || installedProtocols.length > 0),
    [installedProtocols],
  );
  const selectedClientProtocol = installedProtocols.includes(newClient.protocol) ? newClient.protocol : installedProtocols[0] || "wg";

  useEffect(() => {
    let cancelled = false;
    if (!generated) return () => { cancelled = true; };
    void QRCode.toDataURL(generated, { errorCorrectionLevel: "L", margin: 4, width: 768 })
      .then((dataUrl) => { if (!cancelled) setGeneratedQr(dataUrl); })
      .catch(() => { if (!cancelled) setGeneratedQrError("Не удалось создать QR-код для этой конфигурации"); });
    return () => { cancelled = true; };
  }, [generated]);

  async function addClient(event: FormEvent) {
    event.preventDefault(); setBusy(true); setGenerated(""); setGeneratedQr(""); setGeneratedQrError(""); setError("");
    if (!installedProtocols.includes(selectedClientProtocol)) {
      setBusy(false); setError("Сначала установите выбранный протокол"); return;
    }
    try {
      const payload = { ...newClient, protocol: selectedClientProtocol };
      const result = await request("/clients", { method: "POST", body: JSON.stringify(payload) });
      setGenerated(result.config); setGeneratedName(result.filename || `${newClient.name}-${selectedClientProtocol}.conf`);
      downloadConfig(result.filename || `${newClient.name}-${selectedClientProtocol}.conf`, result.config);
      setNewClient({ ...newClient, name: "" });
      await loadClients();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось создать клиента"); }
    finally { setBusy(false); }
  }

  function downloadConfig(filename: string, config: string) {
    const blob = new Blob([config], { type: "application/x-wireguard-profile;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = filename; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function downloadLogs(filename: string, lines: string[]) {
    downloadConfig(filename, `${lines.join("\n")}\n`);
  }

  async function removeClient(id: string) {
    if (!await askConfirmation({
      title: "Отозвать доступ клиента?",
      message: "Клиент будет удалён, а его VPN-доступ прекратится немедленно.",
      confirmLabel: "Отозвать доступ", danger: true,
    })) return;
    setBusy(true);
    try { await request(`/clients/${id}`, { method: "DELETE" }); await loadClients(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось удалить клиента"); }
    finally { setBusy(false); }
  }

  async function login(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    const candidateToken = btoa(`${loginUser}:${loginPassword}`);
    try {
      const response = await fetch("/api/overview", {
        headers: { Authorization: `Basic ${candidateToken}` },
      });
      if (!response.ok) {
        throw new Error(response.status === 401
          ? "Неверный логин или пароль"
          : `Не удалось проверить учётные данные (ошибка ${response.status})`);
      }
      setToken(candidateToken);
      setLoginPassword("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось войти в панель");
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return <main className="loginPage">
      <div className="loginGlow" />
      <form className="loginCard" onSubmit={login}>
        <Logo />
        <div><p className="eyebrow">INFRASTRUCTURE CONTROL</p><h1>Вход в панель управления сервером.</h1></div>
        <p className="loginCopy">Введите учётные данные администратора для доступа к панели.</p>
        <label>Логин<input type="text" value={loginUser} onChange={(event) => setLoginUser(event.target.value)} autoFocus required /></label>
        <label>Пароль<input type="password" value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} required /></label>
        <button className="primaryButton" type="submit" disabled={busy}>{busy ? "Проверка…" : "Открыть панель"} <span>→</span></button>
        {error && <div className="errorBox">{error}</div>}
      </form>
      <VersionFooter />
    </main>;
  }

  const memUsed = overview ? 100 - overview.resources.memory_available / overview.resources.memory_total * 100 : 0;
  const diskUsed = overview ? 100 - overview.resources.disk_available / overview.resources.disk_total * 100 : 0;
  const memoryUsedBytes = overview ? Math.max(0, overview.resources.memory_total - overview.resources.memory_available) : 0;
  const diskUsedBytes = overview ? Math.max(0, overview.resources.disk_total - overview.resources.disk_available) : 0;
  const firewall = security?.firewall as {
    active?: boolean; rules?: string[]; forwarding_enabled?: boolean; stateful_return?: boolean;
    uplink_interface?: string; vpn_policy_healthy?: boolean;
    panel_access?: {
      mode?: "external" | "vpn"; port?: number; listening?: boolean; public_rule?: boolean;
      publicly_accessible?: boolean; vpn_only?: boolean; allowed_interfaces?: string[]; consistent?: boolean;
    };
    protocol_policies?: Record<string, { installed?: boolean; route_allowed?: boolean; nat_enabled?: boolean; healthy?: boolean }>;
  } | undefined;
  const ssh = security?.ssh as { active?: boolean; password_authentication?: string; permit_root_login?: string; publicly_allowed?: boolean; active_connections?: number; max_auth_tries?: string; x11_forwarding?: string; tcp_forwarding?: string } | undefined;
  const updates = security?.updates as { available?: number; security?: number; kernel_available?: boolean; reboot_required?: boolean; automatic?: boolean; source?: string; checked_at?: string; refreshing?: boolean } | undefined;
  const applicationVersion = security?.application_version as { branch?: string; current_commit?: string; latest_commit?: string; outdated?: boolean | null; checked_at?: string; error?: string; refreshing?: boolean } | undefined;
  const securitySystem = security?.system as { kernel?: string; ipv4_forwarding?: boolean; syn_cookies?: boolean; rp_filter?: boolean; rp_filter_mode?: number; rp_filter_valid?: boolean; redirects_disabled?: boolean; source_route_disabled?: boolean; dmesg_restricted?: boolean; auditd_active?: boolean; sudo_users?: string[]; login_users?: string[]; apparmor?: { active?: boolean; profiles?: number } } | undefined;
  const fail2ban = security?.fail2ban as { active?: boolean; jail_active?: boolean; currently_banned?: number; total_banned?: number } | undefined;
  const listeners = (security?.listeners as string[]) || [];
  const listenerSummary = security?.listener_summary as { tcp?: number; udp?: number; local_only?: number } | undefined;
  const legacy = (security?.legacy_services as Record<string, { active?: boolean; enabled?: string }>) || {};
  const applicationSecurity = security?.application_security as {
    admin_password_strong?: boolean; cors_restricted?: boolean; secrets_protected?: boolean;
    secrets_mode?: string; api_local_only?: boolean; control_command_protected?: boolean; control_command_mode?: string;
  } | undefined;
  const panelSecurity = firewall?.panel_access;
  const panelAccessHealthy = Boolean(panelSecurity?.consistent && (panelSecurity?.vpn_only || panelSecurity?.publicly_accessible));
  const sshProtected = Boolean(
    ssh?.active === false
    || (ssh?.active && fail2ban?.active && fail2ban?.jail_active)
  );
  const securityChecks = [
    Boolean(firewall?.active),
    Boolean(firewall?.vpn_policy_healthy),
    panelAccessHealthy,
    Boolean(fail2ban?.active && fail2ban?.jail_active),
    sshProtected,
    ssh?.active === false || ssh?.x11_forwarding === "no",
    Boolean(security) && Number(updates?.available || 0) === 0,
    Boolean(security) && !updates?.reboot_required,
    Boolean(updates?.automatic),
    applicationVersion?.outdated === false,
    Boolean(securitySystem?.syn_cookies),
    Boolean(securitySystem?.apparmor?.active),
    Boolean(securitySystem?.auditd_active),
    Boolean(securitySystem?.rp_filter_valid),
    Boolean(securitySystem?.redirects_disabled),
    Boolean(securitySystem?.source_route_disabled),
    Boolean(securitySystem?.dmesg_restricted),
    Boolean(applicationSecurity?.admin_password_strong),
    Boolean(applicationSecurity?.cors_restricted),
    Boolean(applicationSecurity?.secrets_protected),
    Boolean(applicationSecurity?.api_local_only),
    Boolean(applicationSecurity?.control_command_protected),
  ];
  const securityScore = Math.round(securityChecks.filter(Boolean).length / securityChecks.length * 100);
  const protocolTab = (["wg", "awg", "shadowsocks", "vless-reality-xhttp"] as string[]).includes(tab) ? tab as Protocol : undefined;
  const activeProtocol = protocolTab ? protocolStatuses[protocolTab] : undefined;
  const activeProtocolRate = protocolTab ? protocolRates[protocolTab] || { rx: 0, tx: 0 } : { rx: 0, tx: 0 };
  const activeProtocolImage = protocolTab ? protocolImages.find((image) => image.id === protocolTab) : undefined;
  const operationActive = ["queued", "running", "active", "activating", "rebooting", "powering-off"].includes(application?.action?.state || "");
  const operationProgress = Math.max(0, Math.min(100, application?.action?.progress || (operationActive ? 5 : 100)));
  const operationName = application?.action?.action || "";
  const operationLabel = actionLabels[operationName.split(":")[0]] || operationName;
  const nodeHasError = Boolean(error) || application?.action?.state === "failed" || application?.action?.result === "failed";
  const nodeDegraded = application?.api.active === false
    || Boolean(application?.containers.some((container) => container.healthy === false || (container.State || "").toLowerCase() !== "running"));
  const serviceModeActive = Boolean(services?.service_mode?.active || application?.service_mode?.active);
  const nodeState = nodeHasError ? "error" : operationActive || nodeDegraded || serviceModeActive ? "working" : "healthy";
  const nodeStateLabel = nodeState === "error" ? "УЗЕЛ С ОШИБКОЙ" : nodeState === "working" ? "ТРЕБУЕТ ВНИМАНИЯ" : "УЗЕЛ В СЕТИ";
  const applicationStateTitle = nodeState === "error"
    ? "Есть ошибки"
    : operationActive ? operationLabel
    : serviceModeActive ? "Сервисный режим"
    : nodeDegraded ? "Нарушение работы"
    : "В сети";

  return <main className="shell">
    <aside className="sidebar">
      <Logo />
      <nav>{navigation.map((id) =>
        <button key={id} onClick={() => setTab(id)} className={`navItem ${tab === id ? "active" : ""}`}>
          <b>{labels[id]}</b>{id === "clients" && <em>{clients.length}</em>}
        </button>
      )}
        {protocolCategories.filter((category) => category.images.some((image) => image.installed)).map((category) => {
          const available = category.images.filter((image) => image.installed);
          if (available.length === 1) {
            const image = available[0];
            return <button key={category.id} onClick={() => setTab(image.id as Protocol)} className={`navItem ${tab === image.id ? "active" : ""}`}>
              <b>{image.name}</b>
            </button>;
          }
          return <div className="settingsWrap moduleMenuWrap" key={category.id}>
            <button onClick={() => {
              setSettingsOpen(false);
              setModuleMenuOpen((value) => value === category.id ? "" : category.id);
            }} className={`navItem moduleToggle ${available.some((image) => image.id === tab) ? "active" : ""}`}>
              <b>{category.name}</b>
            </button>
            {moduleMenuOpen === category.id && <div className="settingsMenu moduleMenu">
              {available.map((image) => <button key={image.id} onClick={() => {
                setTab(image.id as Protocol);
                setModuleMenuOpen("");
              }} className={`navItem ${tab === image.id ? "active" : ""}`}><b>{image.name}</b></button>)}
            </div>}
          </div>;
        })}
        <div className="settingsWrap" ref={settingsRef}><button onClick={() => {
          setModuleMenuOpen("");
          setSettingsOpen((value) => !value);
        }} className={`navItem settingsToggle ${tab === "security" || tab === "application" || tab === "services" ? "active" : ""}`}>
          <b>Настройки</b>
        </button>
        {settingsOpen && <div className="settingsMenu">
          {(["security", "application", "services"] as Tab[]).map((id) =>
            <button key={id} onClick={() => { setTab(id); setSettingsOpen(false); }} className={`navItem ${tab === id ? "active" : ""}`}>
              <b>{labels[id]}</b>
            </button>
          )}
        </div>}
        </div>
      </nav>
      <div className={`locationCard ${nodeState}`}><span className="healthDot" /><div><small>{nodeStateLabel}</small><strong>{overview?.server.city}</strong><p>{overview?.server.country} · {overview?.server.public_ip}</p></div></div>
    </aside>

    <section className="content">
      {operationActive && <aside className="operationBanner" aria-live="polite">
        <span className="operationSpinner" />
        <div>
          <strong>{operationLabel || "Выполняется системная операция"}</strong>
          <small>{application?.action?.message || "Сервер выполняет команду…"} · {operationProgress}%</small>
          <i><b style={{ width: `${operationProgress}%` }} /></i>
        </div>
      </aside>}
      <header className="topbar">
        <div><p className="eyebrow">312.NET / {navigationLabels[tab]}</p><h1>{labels[tab]}</h1><p className="subtitle">{overview?.server.city}, {overview?.server.country} · управление инфраструктурой</p></div>
        <div className="topActions">
          <button className={`autoButton ${autoRefresh ? "active" : ""}`} disabled={busy} onClick={() => setAutoRefresh((value) => !value)}><i />{autoRefresh ? `Авто · ${["overview", "clients", "wg", "awg", "security"].includes(tab) ? LIVE_SAMPLE_SECONDS : tab === "application" || tab === "services" ? "10" : "30"}с` : "Пауза"}</button>
          {lastUpdated && <span className="updatedAt">{lastUpdated.toLocaleTimeString("ru-RU")}</span>}
          <button className="iconButton" onClick={() => void refreshCurrent(true)} aria-label="Обновить текущий модуль">↻</button>
          <button className="ghostButton" onClick={() => { sessionStorage.removeItem("312-token"); setToken(""); }}>Выйти</button>
        </div>
      </header>
      {error && <div className="errorBox">{error}</div>}
      {notice && <div className="successNotice" role="status"><span>✓</span>{notice}<button onClick={() => setNotice("")} aria-label="Закрыть уведомление">×</button></div>}
      {busy && <div className="loadingLine" />}

      {tab === "overview" && <section className="overview">
        <article className="heroPanel panel">
          <div><p className="eyebrow">PRIMARY NODE</p><h2>{overview?.server.city}</h2><p className="mono">{overview?.server.country} · {overview?.server.public_ip}</p></div>
          <div className={`nodeStatus ${nodeState}`}><span className="pulse" /><div><strong>{applicationStateTitle}</strong><small>{operationActive ? application?.action?.message || "Команда выполняется" : `Uptime ${uptime(overview?.server.uptime_s)}`}</small></div></div>
        </article>
        <div className="metrics">
          <Metric title="CPU · СЕРВЕР" value={`${(overview?.resources.cpu_percent || 0).toFixed(0)}%`} percent={overview?.resources.cpu_percent || 0} detail={`Нагрузка ${overview?.resources.load1.toFixed(2) || "—"} · ядер ${overview?.resources.cpu_count || "—"} · свободно ${Math.max(0, 100 - (overview?.resources.cpu_percent || 0)).toFixed(0)}%`} history={resourceHistory.load} />
          <Metric title="RAM · СЕРВЕР" value={bytes(memoryUsedBytes)} percent={memUsed} detail={`Использовано ${memUsed.toFixed(0)}% · свободно ${bytes(overview?.resources.memory_available)} · всего ${bytes(overview?.resources.memory_total)}`} history={resourceHistory.memory} />
          <Metric title="ДИСК · СЕРВЕР" value={bytes(diskUsedBytes)} percent={diskUsed} detail={`Использовано ${diskUsed.toFixed(0)}% · свободно ${bytes(overview?.resources.disk_available)} · всего ${bytes(overview?.resources.disk_total)}`} history={resourceHistory.disk} />
          <article className="panel metricCard networkMetric">
            <div><p className="eyebrow">NETWORK</p><h2>{bytes(networkRate.rx)}<small>/с</small></h2></div>
            <TrendGraph values={resourceHistory.rx} secondary={resourceHistory.tx} relative formatValue={(value) => `${bytes(value)}/с`} ariaLabel="История сетевой нагрузки" />
              <div className="networkDirections">
                <span>↓ Входящая <strong>{bytes(networkRate.rx)}/с</strong><i><b style={{ width: `${networkRate.rx || networkRate.tx ? Math.max(4, networkRate.rx / Math.max(networkRate.rx, networkRate.tx) * 100) : 4}%` }} /></i></span>
                <span>↑ Исходящая <strong>{bytes(networkRate.tx)}/с</strong><i><b style={{ width: `${networkRate.rx || networkRate.tx ? Math.max(4, networkRate.tx / Math.max(networkRate.rx, networkRate.tx) * 100) : 4}%` }} /></i></span>
              </div>
              <div className="networkTotals"><span>Всего получено <strong>{bytes(overview?.resources.network_rx)}</strong></span><span>Всего отправлено <strong>{bytes(overview?.resources.network_tx)}</strong></span></div>
            </article>
          </div>
        <article className="panel protocolSummary">
          <div className="panelHead"><div><p className="eyebrow">ADDITIONAL MODULES</p><h2>Дополнительные модули</h2></div></div>
          {(["wg", "awg"] as TunnelProtocol[]).filter((protocol) => overview?.protocols[protocol].active).map((protocol) => <button key={protocol} onClick={() => setTab(protocol)}>
            <span className={`protocol ${protocol}`}>{protocol === "wg" ? "WG" : "AW"}</span>
            <p><strong>{protocol === "wg" ? "WireGuard" : "AmneziaWG"}</strong><small>{protocolImages.find((image) => image.id === protocol)?.description || `${overview?.protocols[protocol].interface} · UDP ${overview?.protocols[protocol].port}`}</small></p>
            <em className="onlinePill">Активен</em><b>›</b>
          </button>)}
          {protocolImages.filter((image) => image.installed && image.id !== "wg" && image.id !== "awg").map((image) => <button key={image.id} onClick={() => setTab(image.id as Protocol)}>
            <span className={`protocol ${image.id}`}>{image.id === "shadowsocks" ? "SS" : "VHR"}</span>
            <p><strong>{image.name}</strong><small>{image.description}</small></p>
            <em className={image.active ? "onlinePill" : "offlinePill"}>{image.active ? "Активен" : "Остановлен"}</em>
            <b>›</b>
          </button>)}
          {protocolImages.filter((image) => !image.installed).map((image) =>
            <div className="protocolInstaller" key={image.id}>
              <span className={`protocol ${image.id}`}>{image.id === "wg" ? "WG" : image.id === "awg" ? "AW" : image.id === "shadowsocks" ? "SS" : "VHR"}</span>
              <p><strong>{image.name}</strong><small>{image.description}</small></p>
              <button onClick={() => void installProtocol(image)} disabled={busy || Boolean(installingProtocol)}>
                {installingProtocol === image.id ? "Устанавливается…" : "Установить"}
              </button>
            </div>
          )}
          {!overview?.protocols.wg.active && !overview?.protocols.awg.active && !protocolImages.length && <div className="protocolEmpty"><span>—</span><p><strong>Нет доступных образов</strong><small>Добавьте manifest.json в каталог protocol-images</small></p></div>}
        </article>
      </section>}

      {tab === "security" && <section className="securityGrid">
        <article className="panel securityHero">
          <div className="scoreRing" style={{ "--score": `${securityScore * 3.6}deg` } as React.CSSProperties}><strong>{securityScore}%</strong></div>
          <div><p className="eyebrow">SECURITY POSTURE</p><h2>{securityLoading ? "Обновляем информацию…" : securityScore >= 85 ? "Стабильно" : securityScore >= 60 ? "Требует внимания" : "Безопасность не гарантирована"}</h2><span>{securityLoading ? "Идёт проверка системы и служб" : `${securityChecks.filter(Boolean).length} из ${securityChecks.length} проверок пройдено`}</span></div>
        </article>
        <div className="securityStats">
          <article className="panel"><h3>SSH</h3><small>ОТКЛОНЕНО ЗА 24Ч</small><strong>{String(security?.failed_ssh_records_24h ?? "—")}</strong><span>{String(ssh?.active_connections ?? "—")} активных подключений · {String(security?.accepted_ssh_24h ?? "—")} успешных входов за 24ч</span></article>
          <article className="panel"><h3>NETWORK LISTENERS</h3><small>АКТИВНЫЕ СЕТЕВЫЕ СЛУЖБЫ</small><strong>{listeners.length}</strong><span>TCP {listenerSummary?.tcp ?? "—"} · UDP {listenerSummary?.udp ?? "—"} · локально {listenerSummary?.local_only ?? "—"}<br />UFW контролирует {firewall?.rules?.length || 0} правил доступа</span></article>
          <article className="panel"><h3>CORE UPDATES</h3><small>KERNEL &amp; PACKAGES</small><strong>{String(updates?.available ?? "—")}</strong><span>{updates?.security || 0} обновлений безопасности</span><time>{updates?.refreshing ? "Идёт точная проверка…" : updates?.checked_at ? `Проверено: ${new Date(updates.checked_at).toLocaleString("ru-RU")}` : "Дата проверки пока недоступна"}</time></article>
        </div>
        <article className="panel systemControls">
          <div><p className="eyebrow">SYSTEM POWER & KERNEL</p><h2>Системные действия</h2><span>Команды выполняются вне процесса панели через systemd</span></div>
          <div className="systemButtons">
            <button onClick={() => void runApplicationAction("kernel-update")} disabled={busy}><strong>Обновить сервер</strong><small>{updates?.kernel_available ? "ядро и пакеты · доступно обновление" : "ядро и системные пакеты"}</small></button>
            <button onClick={() => void runApplicationAction("reboot")} disabled={busy}><strong>Перезагрузить сервер</strong><small>Корректно завершает службы и запускает VPS заново</small></button>
            <button className="poweroffButton" onClick={() => void runApplicationAction("poweroff")} disabled={busy}><strong>Выключить сервер</strong><small>потребуется запуск у провайдера</small></button>
          </div>
        </article>
        <article className="panel securityList compactSecurity">
          <SecurityActionRow ok={Boolean(firewall?.active)} title="Firewall" text={`UFW · ${firewall?.rules?.length || 0} правил`} onAction={() => void fixSecurity("secure")} />
          <SecurityActionRow
            ok={Boolean(firewall?.vpn_policy_healthy)}
            title="VPN FIREWALL POLICY"
            text={`Forwarding: ${firewall?.forwarding_enabled ? "ON" : "OFF"} · Stateful return: ${firewall?.stateful_return ? "ON" : "OFF"} · NAT/route: ${firewall?.vpn_policy_healthy ? "confirmed" : "invalid"}`}
            onAction={() => void fixSecurity("vpn-firewall")}
            actionLabel="Исправить"
          />
          <SecurityActionRow
            ok={panelAccessHealthy}
            title="Доступ к панели"
            text={panelSecurity?.publicly_accessible
              ? `Публичный TCP ${panelSecurity.port || 80} разрешён правилами UFW`
              : `Из интернета закрыт · доступ только через ${(panelSecurity?.allowed_interfaces || []).join(" / ") || "WG / AWG"}`}
            onAction={() => void fixSecurity("secure")}
          />
          <SecurityActionRow ok={Boolean(fail2ban?.active && fail2ban?.jail_active)} title="Fail2ban · SSH" text={`В бане ${fail2ban?.currently_banned || 0} · всего ${fail2ban?.total_banned || 0}`} onAction={() => void fixSecurity("secure")} />
          <SecurityActionRow
            ok={sshProtected}
            title="SSH · административный доступ"
            text={ssh?.active === false
              ? "Служба остановлена · входящие SSH-подключения не принимаются"
              : `Из интернета: ${ssh?.publicly_allowed ? "открыт по согласованной политике" : "закрыт"} · Fail2ban: ${fail2ban?.active && fail2ban?.jail_active ? "защищает" : "не защищает"} · Password: ${String(ssh?.password_authentication || "unknown")} · Root: ${String(ssh?.permit_root_login || "unknown")}`}
            onAction={() => void fixSecurity("secure")}
          />
          <SecurityActionRow ok={securityLoading || (Number(updates?.available || 0) === 0 && !updates?.reboot_required)} title="Обновления Ubuntu" text={`${String(updates?.available ?? "—")} пакетов${updates?.reboot_required ? " · нужен reboot" : ""}`} onAction={() => void fixSecurity("kernel-update")} actionLabel="Обновить" />
          <SecurityActionRow ok={Boolean(updates?.automatic)} title="Автоматические обновления" text={updates?.automatic ? "Unattended upgrades · ON" : "Unattended upgrades · OFF"} onAction={() => void fixSecurity("secure")} />
          <SecurityActionRow
            ok={applicationVersion?.outdated === false}
            title="Версия приложения"
            text={applicationVersion?.refreshing && applicationVersion?.outdated == null
              ? `Проверяется ветка ${applicationVersion?.branch || "main"}…`
              : applicationVersion?.error
                ? applicationVersion.error
                : applicationVersion?.outdated
                  ? `Устарела: ${applicationVersion.current_commit || "unknown"} · ${applicationVersion.branch || "stabl"}: ${applicationVersion.latest_commit || "unknown"}`
                  : `Актуальна: ${applicationVersion?.current_commit || "unknown"} · ветка ${applicationVersion?.branch || "main"}`}
            onAction={() => void runApplicationAction(applicationVersion?.branch === "main" ? "test-update" : "update")}
            actionLabel="Обновить"
          />
          <SecurityActionRow ok={Boolean(securitySystem?.apparmor?.active)} title="AppArmor" text={`${securitySystem?.apparmor?.profiles || 0} профилей · ${securitySystem?.apparmor?.active ? "активен" : "выключен"}`} onAction={() => void fixSecurity("secure")} />
          <SecurityActionRow ok={Boolean(securitySystem?.auditd_active)} title="Аудит действий" text={`auditd · ${securitySystem?.auditd_active ? "активен" : "остановлен"}`} onAction={() => void fixSecurity("secure")} />
          <SecurityActionRow ok={Boolean(securitySystem?.syn_cookies)} title="Защита TCP" text={`SYN ${securitySystem?.syn_cookies ? "ON" : "OFF"} · Forwarding ${securitySystem?.ipv4_forwarding ? "ON" : "OFF"}`} onAction={() => void fixSecurity("secure")} />
          <SecurityActionRow ok={Boolean(securitySystem?.rp_filter_valid && securitySystem?.dmesg_restricted)} title="Защита ядра" text={`RP ${securitySystem?.rp_filter_mode === 1 ? "strict" : securitySystem?.rp_filter_mode === 2 ? "loose" : securitySystem?.rp_filter_valid ? "VPN-safe" : "OFF"} · dmesg ${securitySystem?.dmesg_restricted ? "restricted" : "open"}`} onAction={() => void fixSecurity("secure")} />
          <SecurityActionRow
            ok={Boolean(securitySystem?.redirects_disabled && securitySystem?.source_route_disabled)}
            title="KERNEL ROUTING"
            text={`Redirects: ${securitySystem?.redirects_disabled ? "blocked" : "allowed"} · Source route: ${securitySystem?.source_route_disabled ? "blocked" : "allowed"}`}
            onAction={() => void fixSecurity("secure")}
          />
          <SecurityActionRow
            ok={ssh?.active === false || ssh?.x11_forwarding === "no"}
            title="SSH-туннели"
            text={ssh?.active === false ? "Служба остановлена · настройки туннелей не применяются" : `X11: ${ssh?.x11_forwarding || "unknown"} · TCP forwarding: ${ssh?.tcp_forwarding || "unknown"} (оставлен для административного контроля) · MaxAuthTries: ${ssh?.max_auth_tries || "unknown"}`}
            onAction={() => void fixSecurity("secure")}
          />
          <SecurityActionRow ok title="Учётные записи" text={`sudo ${securitySystem?.sudo_users?.length || 0} · login ${securitySystem?.login_users?.length || 0}`} onAction={() => void runApplicationAction("integrity-check")} actionLabel="Проверить" alwaysAction />
          <SecurityActionRow
            ok
            title="Дополнительные VPN-службы"
            text={Object.values(legacy).some((service) => service.active)
              ? `Активно ${Object.values(legacy).filter((service) => service.active).length} · установлены отдельно и не управляются приложением`
              : "Не обнаружены"}
            onAction={() => void runApplicationAction("network-check")}
            actionLabel="Проверить"
            alwaysAction
          />
          <SecurityActionRow ok={Boolean(applicationSecurity?.admin_password_strong)} title="Пароль администратора" text={applicationSecurity?.admin_password_strong ? "Достаточная длина и стойкость пароля панели" : "Стандартный пароль считается небезопасным"} onAction={() => setPasswordDialog(true)} actionLabel="Изменить пароль" alwaysAction />
          <SecurityActionRow ok={Boolean(applicationSecurity?.secrets_protected)} title="Секреты приложения" text={`/etc/vps-control.env · права ${applicationSecurity?.secrets_mode || "не определены"} · владелец root`} onAction={() => void fixSecurity("secure")} />
          <SecurityActionRow ok={Boolean(applicationSecurity?.api_local_only)} title="Локальный API" text={applicationSecurity?.api_local_only ? "API слушает только 127.0.0.1:8000" : "API не найден локально или доступен на внешнем интерфейсе"} onAction={() => void fixSecurity("secure")} />
          <SecurityActionRow ok={Boolean(applicationSecurity?.control_command_protected)} title="Команда управления" text={`vps-control · права ${applicationSecurity?.control_command_mode || "не определены"} · запись ограничена`} onAction={() => void fixSecurity("secure")} />
          <SecurityActionRow ok={Boolean(applicationSecurity?.cors_restricted)} title="Доверенные источники" text={applicationSecurity?.cors_restricted ? "CORS ограничен заданными адресами панели" : "CORS разрешает запросы с произвольных источников"} onAction={() => void fixSecurity("secure")} />
        </article>
        <article className="panel listeners"><div className="panelHead"><div><p className="eyebrow">LIVE NETWORK</p><h2>Открытые порты</h2></div><span>{listeners.length} listeners · kernel {securitySystem?.kernel || "—"}</span></div><pre>{listeners.join("\n") || "Нет данных"}</pre></article>
        <article className={`panel logDrawer ${securityLogsOpen ? "open" : ""}`}>
          <button className="logDrawerToggle" onClick={() => setSecurityLogsOpen((value) => !value)}>
            <span><strong>JOURNALCTL</strong><small>ПО ЗАПРОСУ · ЖУРНАЛЫ БЕЗОПАСНОСТИ</small></span><em>{securityLogsOpen ? "Скрыть ↑" : "Открыть ↓"}</em>
          </button>
          {securityLogsOpen && <div className="logDrawerBody"><div className="logTools"><div className="logTabs">
            {(["ssh", "firewall", "system"] as const).map((source) =>
              <button key={source} className={securityLogSource === source ? "active" : ""} onClick={() => { setSecurityLogSource(source); setSecurityLogs([]); }}>
                {source === "ssh" ? "SSH" : source === "firewall" ? "Firewall" : "Система"}
              </button>
            )}
          </div><div className="logActions"><span>{securityNewLogCount ? `${securityNewLogCount} новых · ` : ""}{autoRefresh ? `автообновление${securityLogsUpdatedAt ? ` · ${securityLogsUpdatedAt.toLocaleTimeString("ru-RU")}` : ""}` : "автообновление выключено"}</span><button className="miniButton" onClick={() => void loadSecurityLogs()}>Обновить</button><button className="miniButton" disabled={!securityLogs.length} onClick={() => downloadLogs(`security-${securityLogSource}-${new Date().toISOString().slice(0, 10)}.log`, securityLogs)}>Выгрузить</button></div></div>
          <pre>{securityLogs.join("\n") || "В журнале нет записей"}</pre></div>}
        </article>
      </section>}

      {tab === "application" && <section className="applicationGrid">
        <article className="panel applicationHero">
          <div><p className="eyebrow">VPS-CONTROL</p><h2>Управление приложением</h2><p>Команды запускаются на сервере как отдельные системные задачи. Вы не потеряете интерфейс во время обновления или перезапуска.</p></div>
          <span className={application?.api.active ? "onlinePill" : "offlinePill"}>{application?.api.active ? "API работает" : "API остановлен"}</span>
        </article>
        <article className="panel actionPanel">
          <div className="panelHead"><div><p className="eyebrow">SUDO VPS-CONTROL</p><h2>Доступные действия</h2></div></div>
          <div className="actionButtons">
            <button onClick={() => void runApplicationAction("restart")} disabled={busy}><strong>Перезапустить приложение</strong><small>Перезапускает панель и API без перезагрузки VPS</small></button>
            <button onClick={() => void runApplicationAction("update")} disabled={busy}><strong>Обновить приложение</strong><small>Устанавливает проверенный релиз из основной ветки stabl</small></button>
            {serviceModeActive && <button onClick={() => void runApplicationAction("test-update")} disabled={busy}><strong>Переход на тестовую версию</strong><small>Собирает и устанавливает тестовую ветку main без публикации Release</small></button>}
            {serviceModeActive && application?.service_mode?.rollback_available && <button onClick={() => void runApplicationAction("test-rollback")} disabled={busy}><strong>Вернуться к рабочей версии</strong><small>Восстанавливает приложение, сохранённое перед переходом на main</small></button>}
            <button onClick={() => void runApplicationAction("network-check")} disabled={busy}><strong>Проверить подключения</strong><small>Проверяет интернет, WG, AWG и доступность портов</small></button>
            <button onClick={() => void runApplicationAction("integrity-check")} disabled={busy}><strong>Проверить целостность</strong><small>Проверяет файлы, права доступа и настройки компонентов</small></button>
            <button onClick={() => void runApplicationAction("identity")} disabled={busy}><strong>Обновить данные сервера</strong><small>Повторно определяет публичный IP и географические данные VPS</small></button>
            <button onClick={() => void runApplicationAction("optimize")} disabled={busy}><strong>Освободить ресурсы</strong><small>Удаляет безопасные временные данные и освобождает место</small></button>
          </div>
        </article>
        <article className="panel systemControls">
          <div><p className="eyebrow">SYSTEM POWER &amp; KERNEL</p><h2>Системные действия</h2><span>Команды выполняются через systemd и не блокируют интерфейс панели.</span></div>
          <div className="systemButtons">
            <button onClick={() => void runApplicationAction("kernel-update")} disabled={busy}><strong>Обновить сервер</strong><small>{updates?.kernel_available ? "Ядро и пакеты · доступно обновление" : "Ядро и системные пакеты"}</small></button>
            <button onClick={() => void runApplicationAction("reboot")} disabled={busy}><strong>Перезагрузить сервер</strong><small>Корректно завершает службы и запускает VPS заново</small></button>
            <button className="poweroffButton" onClick={() => void runApplicationAction("poweroff")} disabled={busy}><strong>Выключить сервер</strong><small>Потребуется запуск у провайдера</small></button>
          </div>
        </article>
        <article className="panel panelAccess">
          <div>
            <p className="eyebrow">APPLICATION MODE</p>
            <h3>{services?.panel_access?.public ? "Публичный доступ открыт" : "Доступ через защищённую сеть"}</h3>
            <small>{services?.panel_access?.public ? "Панель доступна по публичному адресу сервера." : `Локальные адреса: ${services?.panel_access?.vpn_urls.join(" · ") || "недоступны"}`}</small>
          </div>
          <div className="panelAccessActions">
            <label className="serviceModeSwitch">
              <span><strong>Сервисный режим</strong><small>{serviceModeActive ? "обслуживание выполняется" : "обычная работа"}</small></span>
              <input type="checkbox" checked={serviceModeActive} onChange={(event) => void changeServiceMode(event.target.checked)} disabled={busy} /><i />
            </label>
            <label className="serviceModeSwitch protectedAccessSwitch">
              <span><strong>Защищённый доступ</strong><small>{services?.panel_access?.public ? "публичный адрес открыт" : "только локальная сеть"}</small></span>
              <input type="checkbox" checked={!services?.panel_access?.public} onChange={(event) => void changePanelAccess(event.target.checked ? "vpn" : "external")} disabled={busy || !services || serviceModeActive} /><i />
            </label>
          </div>
        </article>
        <article className="panel statusPanel">
          <div className="panelHead"><div><p className="eyebrow">RUNTIME</p><h2>Состояние компонентов</h2></div><span>{application?.containers.length || 0} службы</span></div>
          <div className="runtimeRows">
            <SecurityRow ok={Boolean(application?.api.active)} title="API панели" text={application?.api.active ? `Принимает команды интерфейса · автозапуск ${application.api.enabled ? "включён" : "отключён"}` : "Не принимает команды интерфейса"} okLabel="Работает" badLabel="Остановлен" />
            {(application?.containers || []).map((container, index) =>
              <SecurityRow
                key={`${container.Name || container.Service}-${index}`}
                ok={Boolean(container.healthy)}
                title={container.component_name || container.Service || `Компонент ${index + 1}`}
                text={`${container.purpose || "Компонент приложения"} · ${container.status_text || container.Status || container.State || "состояние неизвестно"}`}
                okLabel="Работает"
                badLabel="Остановлен"
              />
            )}
            {application?.action?.action && <SecurityRow ok={application.action.state !== "failed" && application.action.result !== "failed"} title={`Последняя команда: ${actionLabels[application.action.action.split(":")[0]] || application.action.action}`} text={application.action.state === "running" ? "Команда выполняется системной службой" : application.action.result === "success" ? "Команда завершена без ошибок" : application.action.message || "Результат выполнения уточняется"} okLabel={application.action.state === "running" ? "Выполняется" : "Выполнена"} badLabel="Ошибка" />}
          </div>
        </article>
        <article className="panel logPanel applicationLogs">
          <div className="panelHead"><div><p className="eyebrow">SYSTEMD JOURNAL · СВЕЖИЕ СНАЧАЛА</p><h2>Журнал приложения</h2></div><div className="logActions"><button className="miniButton" onClick={() => void loadApplicationLogs()}>Обновить</button><button className="miniButton" disabled={!applicationLogs.length} onClick={() => downloadLogs(`application-${new Date().toISOString().slice(0, 10)}.log`, applicationLogs)}>Выгрузить</button></div></div>
          <pre>{applicationLogs.join("\n") || "В журнале нет записей"}</pre>
        </article>
      </section>}

      {tab === "services" && <section className="servicesGrid">
        <article className="panel servicesHero">
          <div><p className="eyebrow">SYSTEMD CONTROL</p><h2>Службы и обслуживание</h2></div>
          <div className="serviceSummary">
            <span className={services?.failed_units ? "offlinePill" : "onlinePill"}>{services?.failed_units || 0} аварийных служб</span>
            {services?.reboot_required && <span className="warningPill">Требуется перезагрузка</span>}
          </div>
        </article>

        <article className="panel panelAccess">
          <div>
            <p className="eyebrow">APPLICATION MODE</p>
            <h3>{services?.panel_access?.public ? "Публичный доступ открыт" : "Доступ через защищённый туннель"}</h3>
            <small>{services?.panel_access?.public
              ? "Панель доступна по публичному IP сервера."
              : `Локальные адреса: ${services?.panel_access?.vpn_urls.join(" · ") || "недоступны"}`}</small>
          </div>
          <div className="panelAccessActions">
            <label className="serviceModeSwitch">
              <span><strong>Сервисный режим</strong><small>{serviceModeActive ? "обслуживание выполняется" : "обычная работа"}</small></span>
              <input type="checkbox" checked={serviceModeActive} onChange={(event) => void changeServiceMode(event.target.checked)} disabled={busy} />
              <i />
            </label>
            <label className="serviceModeSwitch protectedAccessSwitch">
              <span><strong>Защищённый доступ</strong><small>{services?.panel_access?.public ? "публичный адрес открыт" : "только локальная сеть"}</small></span>
              <input
                type="checkbox"
                checked={!services?.panel_access?.public}
                onChange={(event) => void changePanelAccess(event.target.checked ? "vpn" : "external")}
                disabled={busy || !services || serviceModeActive}
              />
              <i />
            </label>
          </div>
        </article>

        <article className="panel servicesPanel">
          <div className="panelHead"><div><p className="eyebrow">MANAGED SERVICES</p><h2>Системные службы</h2></div><span>{services?.items.filter((item) => item.active).length || 0} активных</span></div>
          <div className="serviceRows">
            {(services?.items || []).map((service) => <div className="serviceRow" key={service.id}>
              <i className={service.active ? "serviceOnline" : "serviceOffline"} />
              <div><strong>{service.name}</strong><small>{service.unit} · {service.substate} · автозапуск: {service.enabled ? "да" : "нет"}</small></div>
              <dl><div><dt>Перезапуски</dt><dd>{service.restarts}</dd></div><div><dt>Активна с</dt><dd>{service.active_since || "—"}</dd></div></dl>
              <div className="serviceActions">
                {service.controls.includes(service.active ? "restart" : "start") && <button onClick={() => void runServiceAction(service.id, service.name, service.active ? "restart" : "start")} disabled={busy}>{service.active ? "Перезапустить" : "Запустить"}</button>}
                {service.active && (service.controls.includes("stop") || service.disabled_controls?.includes("stop")) && <button
                  className="serviceStop"
                  onClick={() => void runServiceAction(service.id, service.name, "stop")}
                  disabled={busy || service.disabled_controls?.includes("stop")}
                  title={service.disabled_controls?.includes("stop") ? "Остановка отключит панель управления и доступ к восстановлению" : undefined}
                >Остановить</button>}
              </div>
            </div>)}
          </div>
        </article>

        <article className="panel loggingControl">
          <div>
            <p className="eyebrow">LOG MANAGEMENT</p>
            <h2>Запись и хранение журналов</h2>
            <small>Системные службы, приложение, контейнеры и история мониторинга · {services?.logging?.disk_usage || "объём уточняется"}</small>
          </div>
          <div className="loggingSettings">
            <label className="serviceModeSwitch protectedAccessSwitch">
              <span><strong>Запись логов</strong><small>{loggingDraft?.persistent ? "сохраняются после перезагрузки" : "только временно, до перезагрузки"}</small></span>
              <input
                type="checkbox"
                checked={loggingDraft?.persistent ?? true}
                onChange={(event) => updateLoggingDraft({ persistent: event.target.checked })}
                disabled={busy || !services}
              />
              <i />
            </label>
            <label className="loggingRetention">
              <span><strong>Автоматическая очистка</strong><small>Срок хранения системных журналов</small></span>
              <select
                value={loggingDraft?.retention_days ?? 30}
                onChange={(event) => updateLoggingDraft({ retention_days: Number(event.target.value) })}
                disabled={busy || !services}
              >
                <option value={1}>1 день</option>
                <option value={7}>7 дней</option>
                <option value={14}>14 дней</option>
                <option value={30}>30 дней</option>
                <option value={90}>90 дней</option>
                <option value={0}>Не очищать автоматически</option>
              </select>
            </label>
            <button className="primaryButton logSaveButton" onClick={() => void saveLoggingSettings()} disabled={busy || !loggingDraft}>Сохранить</button>
            <button className="dangerButton" onClick={() => void clearManagedLogs()} disabled={busy}>Очистить журналы</button>
          </div>
        </article>

        <article className="panel automationCenter">
          <div className="automationCenterHead">
            <div><p className="eyebrow">MAINTENANCE SCHEDULE</p><h2>Плановое обслуживание</h2><small>Перезагрузка и безопасная очистка по расписанию</small></div>
            <button onClick={() => void saveAutomation()} disabled={busy || !services}>Сохранить изменения</button>
          </div>
          <div className="automationRows"><AutomationEditor
            title="Перезагрузка"
            description="Плановая перезагрузка VPS. Используйте ночное окно с минимальной активностью."
            value={automationDraft?.reboot}
            timer={services?.timers.reboot}
            onChange={(patch) => updateAutomation("reboot", patch)}
          />
          <AutomationEditor
            title="Очистка"
            description="Очистка apt-кэша, временных файлов и старых системных журналов."
            value={automationDraft?.cleanup}
            timer={services?.timers.cleanup}
            onChange={(patch) => updateAutomation("cleanup", patch)}
          />
          </div>
          <div className="automationNote">Persistent=true · пропущенная задача будет выполнена после следующего запуска сервера</div>
        </article>
      </section>}

      {(tab === "shadowsocks" || tab === "vless-reality-xhttp") && activeProtocol && <section className="protocolMonitor">
        <article className="panel protocolLiveHero">
          <div>
            <p className="eyebrow">LIVE TUNNEL</p>
            <h2>{labels[tab]}</h2>
            <p className="mono">{activeProtocol.address || "адрес не назначен"} · TCP {activeProtocol.listen_port || "—"} · {activeProtocol.peers} подключений</p>
          </div>
          <div className="protocolControlStack">
            <div className={activeProtocol.service_active ? "protocolHealth online" : "protocolHealth"}>
              <span className="pulse" />
              <div>
                <strong>{activeProtocol.service_active ? "Протокол работает" : "Протокол остановлен"}</strong>
                <small>{activeProtocol.service_enabled ? "Автозапуск включён" : "Автозапуск отключён"} · активно {activeProtocol.online_peers}</small>
              </div>
            </div>
            <div className="protocolActions">
              <button onClick={() => void restartProtocol(tab)} disabled={busy}>Перезапустить</button>
              {activeProtocolImage?.removable && <button className="removeProtocolButton" onClick={() => void removeProtocol(activeProtocolImage)} disabled={busy}>Удалить протокол</button>}
            </div>
          </div>
        </article>
        <div className="protocolMonitorGrid streamProtocolDetails">
          <article className="panel protocolTelemetry protocolQuality">
            <p className="eyebrow">CONNECTIONS</p>
            <div className="telemetryMain"><strong>{activeProtocol.online_peers}/{activeProtocol.peers}</strong><span>активно сейчас</span></div>
            <dl><div><dt>Всего подключений</dt><dd>{activeProtocol.peers}</dd></div><div><dt>Активные службы</dt><dd>{activeProtocol.online_peers}</dd></div></dl>
          </article>
          <article className="panel protocolTelemetry">
            <p className="eyebrow">TRANSPORT &amp; SECURITY</p>
            <div className="telemetryMain"><strong>{tab === "shadowsocks" ? "SS" : "VHR"}</strong><span>{activeProtocol.security || "—"}</span></div>
            <dl><div><dt>Транспорт</dt><dd>{activeProtocol.transport || "—"}</dd></div><div><dt>Целевой узел</dt><dd>{activeProtocol.target || "Прямой выход"}</dd></div></dl>
          </article>
          <article className="panel protocolTelemetry">
            <p className="eyebrow">SYSTEM SERVICE</p>
            <div className="telemetryMain"><strong>{activeProtocol.service_active ? "ON" : "OFF"}</strong><span>systemd</span></div>
            <dl><div><dt>Служба</dt><dd>{activeProtocol.unit || "—"}</dd></div><div><dt>Запущена с</dt><dd>{activeProtocol.active_since ? new Date(activeProtocol.active_since).toLocaleString("ru-RU") : "—"}</dd></div><div><dt>Версия образа</dt><dd>{activeProtocolImage?.version || "—"}</dd></div></dl>
          </article>
        </div>
      </section>}

      {(tab === "wg" || tab === "awg") && activeProtocol && <section className="protocolMonitor">
        <article className="panel protocolLiveHero">
          <div>
            <p className="eyebrow">LIVE TUNNEL</p>
            <h2>{tab === "wg" ? "WireGuard" : "AmneziaWG"}</h2>
            <p className="mono">{activeProtocol.interface} · {activeProtocol.address || "адрес не назначен"} · UDP {activeProtocol.listen_port || "—"}</p>
          </div>
          <div className="protocolControlStack">
            <div className={activeProtocol.active && activeProtocol.service_active ? "protocolHealth online" : "protocolHealth"}>
              <span className="pulse" />
              <div>
                <strong>{activeProtocol.active && activeProtocol.service_active ? "Туннель работает" : "Туннель остановлен"}</strong>
                <small>{activeProtocol.service_enabled ? "Автозапуск включён" : "Автозапуск отключён"}</small>
              </div>
            </div>
            <div className="protocolActions">
              <button onClick={() => void restartProtocol(tab)} disabled={busy}>Перезапустить</button>
              {activeProtocolImage?.removable && <button className="removeProtocolButton" onClick={() => void removeProtocol(activeProtocolImage)} disabled={busy}>Удалить протокол</button>}
            </div>
          </div>
        </article>
        <div className="protocolMonitorGrid">
          <article className="panel protocolFlow">
            <div className="panelHead"><div><p className="eyebrow">PROTOCOL TRAFFIC</p><h3>Текущий поток и статистика за 24 часа</h3></div><span>{activeProtocol.history.samples} замеров</span></div>
            <div className="flowValues">
              <div><small>↓ RX NOW</small><strong>{bytes(activeProtocolRate.rx)}<em>/s</em></strong><span>24H · {bytes(activeProtocol.history.received_bytes)} · MAX {bytes(activeProtocol.history.peak_rx_bps)}/s</span></div>
              <div><small>↑ TX NOW</small><strong>{bytes(activeProtocolRate.tx)}<em>/s</em></strong><span>24H · {bytes(activeProtocol.history.transmitted_bytes)} · MAX {bytes(activeProtocol.history.peak_tx_bps)}/s</span></div>
            </div>
          </article>
          <article className="panel protocolTelemetry protocolQuality">
            <p className="eyebrow">AVAILABILITY · 24 HOURS</p>
            <div className="telemetryMain"><strong>{activeProtocol.history.availability_percent != null ? `${activeProtocol.history.availability_percent}%` : "—"}</strong><span>служба протокола работала</span></div>
            <dl>
              <div><dt>Остановки службы</dt><dd>{activeProtocol.history.service_interruptions}</dd></div>
              <div><dt>Разрывы мониторинга</dt><dd>{activeProtocol.history.monitoring_gaps}</dd></div>
              <div><dt>Периоды без активных связей</dt><dd>{activeProtocol.history.inactive_connection_periods}</dd></div>
            </dl>
          </article>
          <article className="panel protocolTelemetry">
            <p className="eyebrow">VPS CONNECTIVITY · 24 HOURS</p>
            <div className="telemetryMain"><strong>{activeProtocol.history.latency_avg_ms != null ? activeProtocol.history.latency_avg_ms.toFixed(1) : "—"}</strong><span>мс в среднем</span></div>
            <dl>
              <div><dt>Потери контрольных пакетов</dt><dd>{activeProtocol.history.external_loss_percent != null ? `${activeProtocol.history.external_loss_percent}%` : "—"}</dd></div>
              <div><dt>Средний jitter</dt><dd>{activeProtocol.history.jitter_avg_ms != null ? `${activeProtocol.history.jitter_avg_ms.toFixed(1)} мс` : "—"}</dd></div>
              <div><dt>Максимальная задержка</dt><dd>{activeProtocol.history.latency_max_ms != null ? `${activeProtocol.history.latency_max_ms.toFixed(1)} мс` : "—"}</dd></div>
              <div><dt>Текущие соединения</dt><dd>{activeProtocol.online_peers} из {activeProtocol.peers} · {duration(activeProtocol.last_handshake_age_s)}</dd></div>
            </dl>
          </article>
        </div>
        <article className={`panel networkDiagnostics ${activeProtocol.diagnostics?.status || "pending"} ${diagnosticsOpen[tab] ? "open" : ""}`}>
          <button className="resourceToggle diagnosticToggle" onClick={() => toggleNetworkDiagnostics(tab)} aria-expanded={Boolean(diagnosticsOpen[tab])}>
            <div><p className="eyebrow">NETWORK DIAGNOSTICS</p><h3>Причины нестабильности сети и подключений</h3></div>
            <span>{checkingDiagnostics === tab ? "Диагностика…" : diagnosticsOpen[tab] ? "Скрыть" : "Развернуть"}</span>
          </button>
          {diagnosticsOpen[tab] && <div className="diagnosticBody">
            <div className="diagnosticHead">
              <p>Проверка внешнего канала, DNS, HTTPS, UDP, маршрутизации, MTU, drops и conntrack.</p>
              <div className="diagnosticScore">
                <span>{activeProtocol.diagnostics?.score != null ? activeProtocol.diagnostics.score : "—"}</span>
                <small>{activeProtocol.diagnostics?.status === "healthy" ? "СТАБИЛЬНО" : activeProtocol.diagnostics?.status === "critical" ? "КРИТИЧНО" : activeProtocol.diagnostics?.status === "warning" ? "ТРЕБУЕТ ВНИМАНИЯ" : "ПРОВЕРКА"}</small>
              </div>
              <button onClick={() => void checkNetworkDiagnostics(tab)} disabled={checkingDiagnostics === tab}>
                {checkingDiagnostics === tab ? "Диагностика…" : "Проверить сейчас"}
              </button>
            </div>
            <div className="diagnosticChecks">
              {(activeProtocol.diagnostics?.checks || []).map((check) =>
                <div className={check.ok ? "ok" : "failed"} key={check.id}><i /><span><strong>{check.name}</strong><small>{check.value}</small></span></div>
              )}
            </div>
            <div className="diagnosticFindings">
              {(activeProtocol.diagnostics?.findings || []).map((finding) =>
                <div className={finding.severity} key={finding.code}>
                  <span>{finding.severity === "critical" ? "!" : "i"}</span>
                  <p><strong>{finding.title}</strong><small>{finding.detail}</small><em>{finding.action}</em></p>
                </div>
              )}
              {activeProtocol.diagnostics?.status === "healthy" && <div className="diagnosticHealthy"><span>✓</span><p><strong>Критичных проблем не обнаружено</strong><small>Маршрут, DNS, внешний HTTPS, UDP, MTU и маршрутизация прошли проверку.</small></p></div>}
              {!activeProtocol.diagnostics?.checks?.length && <div className="eventEmpty">Диагностика выполняется…</div>}
            </div>
            <div className="diagnosticFooter">
              <span>Uplink: {activeProtocol.diagnostics?.network?.uplink || "—"} · MTU {activeProtocol.diagnostics?.network?.uplink_mtu || "—"}</span>
              <span>Conntrack: {activeProtocol.diagnostics?.network?.conntrack_percent != null ? `${activeProtocol.diagnostics.network.conntrack_percent}%` : "—"}</span>
              <span>Drops 24h: uplink {activeProtocol.history.uplink_dropped || 0} · tunnel {activeProtocol.history.interface_dropped || 0}</span>
              <span>{activeProtocol.diagnostics?.checked_at ? `Проверено ${new Date(activeProtocol.diagnostics.checked_at).toLocaleString("ru-RU")}` : "Ожидание первого замера"}</span>
            </div>
          </div>}
        </article>
        <article className={`panel resourceAvailability ${resourcesOpen[tab] ? "open" : ""}`}>
          <button className="resourceToggle" onClick={() => toggleProtocolResources(tab)} aria-expanded={Boolean(resourcesOpen[tab])}>
            <div><p className="eyebrow">RESOURCE AVAILABILITY</p><h3>Проверка внешних сервисов с VPS</h3></div>
            <span>{checkingResources === tab ? "Проверяем…" : resourcesOpen[tab] ? "Скрыть" : "Развернуть"}</span>
          </button>
          {resourcesOpen[tab] && <div className="resourceBody">
            <div className="resourceTools">
              <span>{activeProtocol.resources?.checked_at ? `проверено ${new Date(activeProtocol.resources.checked_at).toLocaleTimeString("ru-RU")}` : "выполняется первая проверка"}</span>
              <button onClick={() => void checkProtocolResources(tab)} disabled={checkingResources === tab}>
                {checkingResources === tab ? "Проверяем…" : "Обновить результат"}
              </button>
            </div>
            <div className="resourceIndicators">
              {(activeProtocol.resources?.items || []).map((resource) =>
                <div className={resource.available ? "resourceItem online" : "resourceItem offline"} key={resource.name} title={resource.status_code ? `HTTP ${resource.status_code}` : undefined}>
                  <i /><strong>{resource.name}</strong><span>{resource.available ? `${resource.latency_ms} мс` : "недоступен"}</span>
                </div>
              )}
              {!activeProtocol.resources?.items?.length && <div className="eventEmpty">Проверка выполняется…</div>}
            </div>
          </div>}
        </article>
        <article className="panel protocolEvents">
          <div className="panelHead"><div><p className="eyebrow">STABILITY LOG</p><h3>Последние события протокола</h3></div><span>агрегация за 24 часа</span></div>
          <div className="eventRows">
            {activeProtocol.history.events.length ? activeProtocol.history.events.map((event, index) =>
              <div key={`${event.at}-${index}`}><i className={event.type === "service_down" ? "eventCritical" : "eventWarning"} />
                <p><strong>{event.type === "service_down" ? "Служба протокола остановилась" : event.type === "monitor_gap" ? "Пропуск данных мониторинга" : "Не осталось активных соединений"}</strong>
                <small>{event.at ? new Date(event.at).toLocaleString("ru-RU") : "—"}{event.seconds ? ` · ${event.seconds} сек` : ""}</small></p>
              </div>
            ) : <div className="eventEmpty">За выбранный период разрывов и остановок не зафиксировано</div>}
          </div>
        </article>
      </section>}

      {tab === "clients" && installedProtocols.length > 0 && <section className="clientsLayout">
        <article className="panel clientsPanel"><div className="panelHead"><div><p className="eyebrow">ACCESS</p><h2>{tab === "clients" ? "Все подключения" : labels[tab]}</h2></div><div className="clientPanelActions"><span>{protocolClients.length} подключений</span><a className="guideAction" href="/connection-guide-wg-awg.pdf" download aria-label="Скачать руководство по подключению" data-tooltip="Пошаговая инструкция для владельца устройства: установка приложения и импорт конфигурации"><span aria-hidden="true">↓</span><div><strong>Скачать гайд</strong><small>PDF · ДЛЯ ПОЛЬЗОВАТЕЛЯ</small></div></a><button className="primaryButton" onClick={openClientDialog}>Новое подключение <span>＋</span></button></div></div>
          <div className="clientTable">{protocolClients.length ? visibleClients.map((client) =>
            <div className={`clientRow quality-${client.quality || "offline"}`} key={client.id}><div className="clientIdentity"><span className={`protocol ${client.protocol}`}>{client.protocol === "wg" ? "WG" : client.protocol === "awg" ? "AW" : client.protocol === "shadowsocks" ? "SS" : "VHR"}</span><p><strong><i className={`clientQuality ${client.quality || "offline"}`} />{client.name}</strong><small>{client.address}{client.active_sources?.length ? ` · источник: ${client.active_sources.join(", ")}` : ""}</small></p></div>
              <div className="clientState"><small>СТАТУС</small><strong>{client.quality === "stable" ? "ОНЛАЙН" : client.quality === "offline" ? "ОФЛАЙН" : "НЕСТАБИЛЬНО"}</strong><span>{client.quality_reason || "состояние уточняется"}</span></div>
              <div className="traffic"><small>ТРАФИК · ПОЛУЧЕНО<b>↓ {bytes(client.rx_bytes)} · {bytes(client.rx_bps)}/с</b></small><small>ТРАФИК · ОТПРАВЛЕНО<b>↑ {bytes(client.tx_bytes)} · {bytes(client.tx_bps)}/с</b></small></div><span className="handshake"><small>{client.protocol === "wg" || client.protocol === "awg" ? "ПОСЛЕДНИЙ HANDSHAKE" : "ПОСЛЕДНЯЯ АКТИВНОСТЬ"}</small><strong>{duration(client.handshake_age_s)}</strong><span>{client.active_connections ? `${client.active_connections} активн.` : "нет активных"}</span></span>
              <span className="clientLink"><small>КАЧЕСТВО КАНАЛА</small><strong>{client.latency_ms !== undefined && client.latency_ms !== null ? `${client.latency_ms} ms` : "—"}{client.packet_loss_percent !== undefined && client.packet_loss_percent !== null ? ` · loss ${client.packet_loss_percent}%` : ""}</strong></span>
              <button className="dangerButton" onClick={() => void removeClient(client.id)}>Отозвать</button></div>
          ) : <div className="emptyState"><span>◎</span><p>Подключений пока нет</p></div>}</div>
          {protocolClients.length > CLIENTS_PER_PAGE && <nav className="clientPagination" aria-label="Страницы подключений">
            <span>Показаны {visibleClientStart}–{visibleClientEnd} из {protocolClients.length}</span>
            <div><button onClick={() => setClientPage(1)} disabled={currentClientPage === 1} aria-label="Первая страница">«</button><button onClick={() => setClientPage(Math.max(1, currentClientPage - 1))} disabled={currentClientPage === 1}>Назад</button><strong>{currentClientPage} / {clientPageCount}</strong><button onClick={() => setClientPage(Math.min(clientPageCount, currentClientPage + 1))} disabled={currentClientPage === clientPageCount}>Дальше</button><button onClick={() => setClientPage(clientPageCount)} disabled={currentClientPage === clientPageCount} aria-label="Последняя страница">»</button></div>
          </nav>}
        </article>
        <ConnectionGuide />
      </section>}
      {clientDialog && <div className="confirmBackdrop" role="presentation" onMouseDown={closeClientDialog}>
        <form className="connectionDialog addClient" role="dialog" aria-modal="true" aria-labelledby="connection-dialog-title" onMouseDown={(event) => event.stopPropagation()} onSubmit={addClient}>
          <header><div><p className="eyebrow">NEW ACCESS</p><h2 id="connection-dialog-title">{generated ? "Подключение создано" : "Новое подключение"}</h2></div><button className="dialogClose" type="button" onClick={closeClientDialog} aria-label="Закрыть">×</button></header>
          {!generated ? <>
            <p className="connectionDialogIntro">Создайте отдельную конфигурацию для конкретного устройства. Один ключ нельзя использовать на нескольких устройствах.</p>
            <div className="connectionForm">
              <label>Название устройства<input autoFocus required minLength={2} maxLength={48} pattern="[\\p{L}\\p{N}_. -]{2,48}" title="От 2 до 48 символов: буквы, цифры, пробел, точка, дефис или _" value={newClient.name} onChange={(event) => setNewClient({ ...newClient, name: event.target.value })} placeholder="Например: iPhone 15" /><small className="fieldHint">2–48 символов · это имя будет видно только администратору панели</small></label>
              <label>Протокол<select value={selectedClientProtocol} onChange={(event) => setNewClient({ ...newClient, protocol: event.target.value as Protocol })}>{installedProtocols.map((protocol) => <option key={protocol} value={protocol}>{labels[protocol]}</option>)}</select><small className="fieldHint">Выберите протокол, который будет использовать устройство</small></label>
            </div>
            <div className="connectionDialogActions"><button type="button" onClick={closeClientDialog}>Отмена</button><button className="primaryButton" disabled={busy}>{busy ? "Создаём…" : "Создать конфигурацию"}<span>→</span></button></div>
          </> : <>
            <div className="generated compactGenerated">
              <div className="generatedHead"><span>✓</span><div><small>КОНФИГУРАЦИЯ ГОТОВА</small><strong>{generatedName}</strong><p>Передайте владельцу скачанный файл или покажите QR-код. Один ключ предназначен только для одного устройства.</p></div></div>
              <div className="generatedResult">
                {generatedQr ? <div className="generatedQr"><Image src={generatedQr} width={300} height={300} unoptimized alt={`QR-код конфигурации ${generatedName}`} /><small>Отсканируйте код в приложении на устройстве владельца</small></div> : <div className="generatedQr pending"><span>{generatedQrError || "Создаём QR-код…"}</span></div>}
                <div className="generatedTransfer">
                  <div><strong>Передача подключения</strong><p>Выберите один способ: передайте файл по защищённому каналу или покажите QR-код. Не публикуйте их — внутри находится приватный ключ.</p></div>
                  <button type="button" className="downloadButton" onClick={() => downloadConfig(generatedName, generated)}><span>↓</span><div><strong>Скачать файл</strong><small>{labels[selectedClientProtocol].toUpperCase()} · {generatedName.split(".").pop()?.toUpperCase()}</small></div></button>
                  {generatedQr && <a className="qrDownload" href={generatedQr} download={`${generatedName.replace(/\.conf$/i, "")}-qr.png`}>Скачать QR в полном размере</a>}
                </div>
              </div>
            </div>
            <div className="connectionDialogActions"><button type="button" onClick={resetClientDialog}>Создать ещё</button><button type="button" className="primaryButton" onClick={closeClientDialog}>Готово <span>✓</span></button></div>
          </>}
        </form>
      </div>}
      {passwordDialog && <div className="confirmBackdrop" role="presentation" onMouseDown={closePasswordDialog}>
        <form className="confirmDialog" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()} onSubmit={changeAdminPassword}>
          <p className="eyebrow">ADMINISTRATOR ACCESS</p><h2>Изменить пароль администратора</h2>
          <p>Новый пароль сохраняется на сервере с закрытыми правами доступа. После смены потребуется войти заново.</p>
          <label>Текущий пароль<input autoFocus type="password" autoComplete="current-password" maxLength={256} value={currentAdminPassword} onChange={(event) => setCurrentAdminPassword(event.target.value)} required /></label>
          <label>Новый пароль<input type="password" autoComplete="new-password" minLength={16} maxLength={128} value={newAdminPassword} onChange={(event) => setNewAdminPassword(event.target.value)} required /></label>
          <label>Повторите новый пароль<input type="password" autoComplete="new-password" minLength={16} maxLength={128} value={confirmAdminPassword} onChange={(event) => setConfirmAdminPassword(event.target.value)} required /></label>
          <div className="confirmActions"><button type="button" onClick={closePasswordDialog}>Отмена</button><button className="confirmPrimary" type="submit" disabled={busy || !currentAdminPassword || newAdminPassword.length < 16 || newAdminPassword !== confirmAdminPassword}>Сохранить пароль</button></div>
        </form>
      </div>}
      {confirmation && <div className="confirmBackdrop" role="presentation" onMouseDown={() => closeConfirmation(false)}>
        <form className={`confirmDialog ${confirmation.danger ? "danger" : ""}`} role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" onMouseDown={(event) => event.stopPropagation()} onSubmit={(event) => {
          event.preventDefault();
          if (!confirmation.phrase || confirmationInput === confirmation.phrase) closeConfirmation(true);
        }}>
          <div className="confirmMark">{confirmation.danger ? "!" : "✓"}</div>
          <p className="eyebrow">ACTION CONFIRMATION</p>
          <h2 id="confirm-title">{confirmation.title}</h2>
          <p>{confirmation.message}</p>
          {confirmation.phrase && <label>Для подтверждения введите <strong>{confirmation.phrase}</strong>
            <input autoFocus value={confirmationInput} onChange={(event) => setConfirmationInput(event.target.value)} autoComplete="off" />
          </label>}
          <div className="confirmActions">
            <button type="button" onClick={() => closeConfirmation(false)}>Отмена</button>
            <button className="confirmPrimary" type="submit" disabled={Boolean(confirmation.phrase && confirmationInput !== confirmation.phrase)}>{confirmation.confirmLabel}</button>
          </div>
        </form>
      </div>}
      <VersionFooter />
    </section>
  </main>;
}

function Logo() {
  return <div className="brand"><span className="brandMark"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M7 7h12l6 6v12H13l-6-6V7Z" /><path d="M11 12h8l2 2v6h-8l-2-2v-6Z" /></svg></span><div><strong>312<span>.net</span></strong><small>INFRASTRUCTURE</small></div></div>;
}
function AutomationEditor({
  title, description, value, timer, onChange,
}: {
  title: string;
  description: string;
  value?: AutomationSchedule;
  timer?: { installed: boolean; active: boolean; last_trigger: string; next_run: string };
  onChange: (patch: Partial<AutomationSchedule>) => void;
}) {
  const weekdays = [
    ["Mon", "Понедельник"], ["Tue", "Вторник"], ["Wed", "Среда"], ["Thu", "Четверг"],
    ["Fri", "Пятница"], ["Sat", "Суббота"], ["Sun", "Воскресенье"],
  ];
  if (!value) return <div className="automationRow"><div><h3>{title}</h3><small>Загрузка параметров…</small></div></div>;
  const time = `${String(value.hour).padStart(2, "0")}:${String(value.minute).padStart(2, "0")}`;
  return <div className={`automationRow ${value.enabled ? "enabled" : ""}`}>
    <div className="automationIdentity"><h3>{title}</h3><small>{description}</small></div>
    <div className="automationFields">
      <label>Период<select value={value.cadence} onChange={(event) => onChange({ cadence: event.target.value as AutomationSchedule["cadence"] })}>
        <option value="daily">Ежедневно</option><option value="weekly">Еженедельно</option><option value="monthly">Ежемесячно, 1-го числа</option>
      </select></label>
      {value.cadence === "weekly" && <label>День недели<select value={value.weekday} onChange={(event) => onChange({ weekday: event.target.value })}>
        {weekdays.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
      </select></label>}
      <label>Время сервера<input type="time" value={time} onChange={(event) => {
        const [hour, minute] = event.target.value.split(":").map(Number);
        if (Number.isInteger(hour) && Number.isInteger(minute)) onChange({ hour, minute });
      }} /></label>
    </div>
    <div className="automationRun"><small>Следующий запуск</small><strong>{timer?.next_run || "—"}</strong><span>{timer?.last_trigger ? `последний: ${timer.last_trigger}` : "ещё не запускалось"}</span></div>
    <label className="automationSwitch"><input type="checkbox" checked={value.enabled} onChange={(event) => onChange({ enabled: event.target.checked })} /><span /><em>{value.enabled ? "Вкл" : "Выкл"}</em></label>
  </div>;
}
function TrendGraph({ values, secondary, relative = false, sampleIntervalSeconds = LIVE_SAMPLE_SECONDS, formatValue = (value) => `${Math.round(value)}%`, ariaLabel }: {
  values: number[]; secondary?: number[]; relative?: boolean; sampleIntervalSeconds?: number; formatValue?: (value: number) => string; ariaLabel: string;
}) {
  const width = 240;
  const height = 72;
  const all = secondary ? [...values, ...secondary] : values;
  const ceiling = relative ? Math.max(1, ...all) : 100;
  const coordinates = (series: number[]) => series.map((value, index) => {
    const x = series.length > 1 ? index / (series.length - 1) * width : width;
    const y = height - Math.min(value / ceiling, 1) * height;
    return { x, y };
  });
  const primaryCoordinates = coordinates(values);
  const secondaryCoordinates = secondary ? coordinates(secondary) : [];
  const stepPath = (coordinatesList: Array<{ x: number; y: number }>) => coordinatesList.reduce((path, point, index) => {
    if (!index) return `M ${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
    return `${path} H ${point.x.toFixed(1)} V ${point.y.toFixed(1)}`;
  }, "");
  const primaryPath = stepPath(primaryCoordinates);
  const secondaryPath = stepPath(secondaryCoordinates);
  const primaryLast = primaryCoordinates.at(-1);
  const secondaryLast = secondaryCoordinates.at(-1);
  const primaryPeak = values.length ? Math.max(...values) : 0;
  const secondaryPeak = secondary?.length ? Math.max(...secondary) : 0;
  const primaryAverage = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
  const secondaryAverage = secondary?.length ? secondary.reduce((sum, value) => sum + value, 0) / secondary.length : 0;
  const elapsedSeconds = Math.max(0, (values.length - 1) * sampleIntervalSeconds);
  const elapsedLabel = elapsedSeconds >= 60 ? `${Math.round(elapsedSeconds / 60)} мин` : `${elapsedSeconds} сек`;
  return <div className={`trendGraph ${secondary ? "dual" : ""}`} role="img" aria-label={ariaLabel}>
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      {values.length > 1 && <path className="primaryArea" d={`${primaryPath} V ${height} H 0 Z`} />}
      {values.length > 1 && <path className="primaryTrend" d={primaryPath} />}
      {secondary && secondary.length > 1 && <path className="secondaryTrend" d={secondaryPath} />}
      {primaryLast && values.length > 1 && <circle className="primaryPoint" cx={primaryLast.x} cy={primaryLast.y} r="2.8" />}
      {secondaryLast && secondary && secondary.length > 1 && <circle className="secondaryPoint" cx={secondaryLast.x} cy={secondaryLast.y} r="2.4" />}
    </svg>
    <span className="trendYAxis"><b>{formatValue(ceiling)}</b><b>{formatValue(0)}</b></span>
    <span className="trendXAxis"><b>−{elapsedLabel}</b><b>сейчас</b></span>
    <span className="trendSummary">
      <b>Сейчас {formatValue(values.at(-1) || 0)}</b>
      <b>Среднее {formatValue(primaryAverage)}</b>
      <b>Пик {formatValue(primaryPeak)}</b>
      {secondary && <b>TX среднее {formatValue(secondaryAverage)}</b>}
      {secondary && <b>TX пик {formatValue(secondaryPeak)}</b>}
    </span>
    {secondary && <span className="trendLegend"><i /> RX <i /> TX</span>}
    <small>{values.length < 2 ? "Сбор данных…" : `${values.length} замеров · интервал ${sampleIntervalSeconds} сек`}</small>
  </div>;
}
function Metric({ title, value, percent, detail, history }: { title: string; value: string; percent: number; detail: string; history: number[] }) {
  const normalized = Math.max(0, Math.min(100, percent));
  return <article className="panel metricCard">
    <div className="metricCopy"><p className="eyebrow">{title.toUpperCase()}</p><h2>{value}</h2><small>{detail}</small></div>
    <TrendGraph values={history} ariaLabel={`${title}: ${value}, ${Math.round(normalized)} процентов`} />
  </article>;
}
function SecurityRow({ ok, title, text, okLabel = "Confirmed", badLabel = "Attention" }: { ok: boolean; title: string; text: string; okLabel?: string; badLabel?: string }) {
  return <div><span className={ok ? "check" : "warning"}>{ok ? "✓" : "!"}</span><p><strong>{title}</strong><small>{text}</small></p><em className={ok ? "onlinePill" : "offlinePill"}>{ok ? okLabel : badLabel}</em></div>;
}
function SecurityActionRow({ ok, title, text, onAction, actionLabel = "Исправить", alwaysAction = false }: { ok: boolean; title: string; text: string; onAction: () => void; actionLabel?: string; alwaysAction?: boolean }) {
  return <div><span className={ok ? "check" : "warning"}>{ok ? "✓" : "!"}</span><p><strong>{title}</strong><small>{text}</small></p>{ok && !alwaysAction ? <em className="onlinePill">Готово</em> : <button className="securityFixButton" onClick={onAction}>{actionLabel}</button>}</div>;
}
function VersionFooter() {
  return <LegalFooter version={appVersion} commit={buildCommit} />;
}
