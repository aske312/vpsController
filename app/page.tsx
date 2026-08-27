"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import QRCode from "qrcode";
import { LegalFooter } from "./legal";
import { MihomoPage } from "./views/mihomo/mihomo-view";
import { OverviewDashboard } from "./views/overview/overview-view";
import { AppWorkspace } from "./components/layout/app-workspace";
import { ServicesDashboard } from "./views/services/services-view";
import { AccessProfilesBeta } from "./views/users/users-view";
import { DnsView } from "./views/dns/dns-view";
import { SecurityView } from "./views/security/security-view";
import { ApplicationView } from "./views/application/application-view";
import { ConnectionsView } from "./views/connections/connections-view";
import { ProtocolView } from "./views/protocols/protocol-view";
import { LoginView } from "./views/auth/login-view";
import type { ApplicationAction, ApplicationStatus, AutomationSchedule, Client, ConfirmationRequest, DeviceProbe, DnsCheck, DnsSettings, DnsStatus, LiveStatus, LoggingSettings, Overview, Protocol, ProtocolImage, ProtocolStatus, ResourceHistory, ServicesStatus, Tab, TunnelProtocol } from "./types/control-plane";
import { actionLabels, bytes, CLIENTS_PER_PAGE, directProtocolOrder, HISTORY_SAMPLES, labels, LIVE_SAMPLE_SECONDS, navigationLabels, uptime } from "./lib/control-plane-ui";

const appVersion = process.env.NEXT_PUBLIC_APP_VERSION || "v1.0.0";
const buildCommit = process.env.NEXT_PUBLIC_BUILD_COMMIT || "unknown";

function reloadWithoutCache(message: string) {
  sessionStorage.setItem("312-notice", message);
  const target = new URL(window.location.href);
  target.searchParams.set("_refresh", Date.now().toString());
  window.location.replace(target.toString());
}
const appendSample = (values: number[], value: number) => [...values, Math.max(0, value)].slice(-HISTORY_SAMPLES);
export default function Home() {
  const [tab, setTab] = useState<Tab>("overview");
  const [selectedChannel, setSelectedChannel] = useState<Protocol>("awg");
  const [token, setToken] = useState("");
  const [loginUser, setLoginUser] = useState("admin");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginPasswordVisible, setLoginPasswordVisible] = useState(false);
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
  const [dns, setDns] = useState<DnsStatus | null>(null);
  const [dnsDraft, setDnsDraft] = useState<DnsSettings | null>(null);
  const [dnsChecks, setDnsChecks] = useState<Record<string, DnsCheck>>({});
  const [deviceProbe, setDeviceProbe] = useState<DeviceProbe | null>(null);
  const [probingDevice, setProbingDevice] = useState(false);
  const deviceProbeAt = useRef(0);
  const [checkingDns, setCheckingDns] = useState(false);
  const [clientSearch, setClientSearch] = useState("");
  const [clientProtocolFilter, setClientProtocolFilter] = useState<"all" | Protocol>("all");
  const [clientStateFilter, setClientStateFilter] = useState<"all" | "online" | "attention" | "offline">("all");
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
  const [protocolSettingsDraft, setProtocolSettingsDraft] = useState<Partial<Record<Protocol, Record<string, string | number | boolean>>>>({});
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
  const protocolSettingsDirty = useRef<Partial<Record<Protocol, boolean>>>({});
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
      const raw = await request(`/protocols/${protocol}/status`) as ProtocolStatus;
      // WG/AWG already return full monitoring data. Stream protocols (VRX/SS)
      // may omit history/resources/diagnostics, so normalize the response before
      // rendering the shared Protocol Workspace. This keeps one UI contract for
      // every installed protocol and prevents a blank page on VRX/SS.
      const next: ProtocolStatus = {
        ...raw,
        resources: {
          checked_at: raw.resources?.checked_at,
          items: raw.resources?.items || [],
        },
        history: {
          period_hours: 24,
          samples: 0,
          availability_percent: raw.service_active ? 100 : 0,
          monitoring_gaps: 0,
          service_interruptions: 0,
          inactive_connection_periods: 0,
          external_loss_percent: undefined,
          latency_avg_ms: undefined,
          latency_max_ms: undefined,
          jitter_avg_ms: undefined,
          interface_errors: 0,
          interface_dropped: 0,
          uplink_errors: 0,
          uplink_dropped: 0,
          conntrack_peak_percent: undefined,
          received_bytes: raw.peer_rx_bytes || raw.interface_rx_bytes || 0,
          transmitted_bytes: raw.peer_tx_bytes || raw.interface_tx_bytes || 0,
          average_rx_bps: 0,
          average_tx_bps: 0,
          peak_rx_bps: 0,
          peak_tx_bps: 0,
          ...(raw.history || {}),
          events: raw.history?.events || [],
        },
        diagnostics: {
          checked_at: raw.diagnostics?.checked_at,
          status: raw.diagnostics?.status || "pending",
          score: raw.diagnostics?.score,
          live: raw.diagnostics?.live,
          network: raw.diagnostics?.network,
          checks: raw.diagnostics?.checks || [],
          findings: raw.diagnostics?.findings || [],
        },
      };
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
      if (!protocolSettingsDirty.current[protocol]) {
        setProtocolSettingsDraft((drafts) => ({
          ...drafts,
          [protocol]: Object.fromEntries((next.editable_settings || []).map((setting) => [setting.key, setting.value])),
        }));
      }
      setLastUpdated(new Date());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось обновить состояние протокола"); }
  }, [request, token]);

  const loadDns = useCallback(async () => {
    if (!token) return;
    try {
      const next = await request("/dns") as DnsStatus;
      setDns(next); setDnsDraft((current) => current || next.settings); setLastUpdated(new Date());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось загрузить DNS"); }
  }, [request, token]);

  async function saveDnsSettings() {
    if (!dnsDraft) return;
    setBusy(true); setError("");
    try {
      const next = await request("/dns/settings", { method: "PUT", body: JSON.stringify(dnsDraft) }) as DnsStatus;
      setDns(next); setDnsDraft(next.settings); setNotice("DNS-профиль сохранён и применён к выбранным протоколам");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось сохранить DNS"); }
    finally { setBusy(false); }
  }

  const measureDeviceRoute = useCallback(async (force = false) => {
    if (!force && Date.now() - deviceProbeAt.current < 30000) return;
    setProbingDevice(true);
    const samples: number[] = [];
    let failed = 0;
    for (let index = 0; index < 5; index += 1) {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 4000);
      const started = performance.now();
      try {
        const response = await fetch(`/api/health?_probe=${Date.now()}-${index}`, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        await response.json();
        samples.push(performance.now() - started);
      } catch {
        failed += 1;
      } finally {
        window.clearTimeout(timeout);
      }
    }
    const host = window.location.hostname;
    const route = host === "10.72.0.1" ? "WireGuard" : host === "10.73.0.1" ? "AmneziaWG" : "текущий маршрут браузера";
    const average = samples.length ? samples.reduce((sum, value) => sum + value, 0) / samples.length : null;
    const variation = samples.length > 1 ? Math.max(...samples) - Math.min(...samples) : null;
    setDeviceProbe({
      latency_ms: average == null ? null : Math.round(average),
      variation_ms: variation == null ? null : Math.round(variation),
      loss_percent: failed / 5 * 100,
      successful: samples.length, samples: 5, measured_at: new Date().toISOString(), route,
    });
    deviceProbeAt.current = Date.now();
    setProbingDevice(false);
  }, []);

  async function checkDnsProviders(providerId?: string) {
    setCheckingDns(true); setError("");
    try {
      const result = await request("/dns/check", { method: "POST", body: JSON.stringify({ provider_id: providerId || null }) }) as { items: DnsCheck[] };
      setDnsChecks((current) => ({ ...current, ...Object.fromEntries(result.items.map((item) => [item.id, item])) }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Проверка DNS не выполнена"); }
    finally { setCheckingDns(false); }
  }

  function changeProtocolSetting(protocol: Protocol, key: string, value: string | number | boolean) {
    protocolSettingsDirty.current[protocol] = true;
    setProtocolSettingsDraft((drafts) => ({ ...drafts, [protocol]: { ...(drafts[protocol] || {}), [key]: value } }));
  }

  async function saveProtocolSettings(protocol: Protocol) {
    const fields = protocolStatuses[protocol]?.editable_settings || [];
    const draft = protocolSettingsDraft[protocol] || {};
    const body = Object.fromEntries(fields.map((field) => [field.key, draft[field.key] ?? field.value]));
    setBusy(true); setError("");
    try {
      const next = await request(`/protocols/${protocol}/settings`, { method: "PATCH", body: JSON.stringify(body) }) as ProtocolStatus;
      protocolSettingsDirty.current[protocol] = false;
      setProtocolStatuses((statuses) => ({ ...statuses, [protocol]: next }));
      setProtocolSettingsDraft((drafts) => ({
        ...drafts,
        [protocol]: Object.fromEntries((next.editable_settings || []).map((setting) => [setting.key, setting.value])),
      }));
      setNotice(`Настройки ${labels[protocol]} применены`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось применить настройки протокола"); }
    finally { setBusy(false); }
  }

  const refreshCurrent = useCallback(async (showBusy = false) => {
    if (!token) return;
    if (showBusy) setBusy(true);
    setError("");
    try {
      if (tab === "overview") await Promise.all([loadOverview(), loadClients(), loadApplication(), loadServices()]);
      else if (tab === "security") await Promise.all([loadSecurity(), loadServices()]);
      else if (tab === "application") await loadApplication();
      else if (tab === "services") await loadServices();
      else if (tab === "dns") await loadDns();
      else if (tab === "mihomo") await loadOverview();
      else if (tab === "channels") await Promise.all([loadClients(), loadProtocolStatus(selectedChannel)]);
      else if (["wg", "awg", "shadowsocks", "vless-reality-xhttp"].includes(tab)) await Promise.all([loadClients(), loadProtocolStatus(tab as Protocol)]);
      else {
        await loadClients();
        if (tab === "clients") await measureDeviceRoute(showBusy);
      }
    } finally {
      if (showBusy) setBusy(false);
    }
  }, [loadApplication, loadClients, loadDns, loadOverview, loadProtocolStatus, loadSecurity, loadServices, measureDeviceRoute, selectedChannel, tab, token]);

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
    const actionRunning = ["queued", "active", "activating", "running", "rebooting", "powering-off"].includes(application?.action?.state || "");
    const updateRunning = actionRunning && ["update", "test-update", "test-rollback", "kernel-update"].includes(application?.action?.action || "");
    const delay = updateRunning ? 3000
      : tab === "overview" ? 30000
        : ["channels", "wg", "awg", "shadowsocks", "vless-reality-xhttp", "clients"].includes(tab) ? 15000
          : 10000;
    const timer = window.setInterval(() => void refreshCurrent(false), delay);
    return () => window.clearInterval(timer);
  }, [application?.action?.action, application?.action?.state, autoRefresh, refreshCurrent, tab, token]);

  useEffect(() => {
    const actionRunning = ["queued", "active", "activating", "running", "rebooting", "powering-off"].includes(application?.action?.state || "");
    if (!token || !actionRunning) return;
    const timer = window.setInterval(() => void loadApplication(), 2000);
    return () => window.clearInterval(timer);
  }, [application?.action?.state, autoRefresh, loadApplication, token]);

  useEffect(() => {
    const action = application?.action;
    if (!action?.unit) return;
    if (["queued", "active", "activating", "running", "rebooting", "powering-off"].includes(action.state || "")) {
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
      message: `На сервер будет установлена последняя доступная версия модуля ${image.name}.`,
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
      if (image.id === "mihomo") {
        await Promise.all([loadOverview(), loadClients()]);
        setTab("mihomo");
      } else {
        await Promise.all([
          loadOverview(),
          loadClients(),
          loadProtocolStatus(image.id as Protocol),
        ]);
      }
      setNotice(`${image.name} установлен и готов к работе`);
    } catch (cause) {
      setInstallingProtocol("");
      setError(cause instanceof Error ? cause.message : "Не удалось запустить установку протокола");
    } finally { setInstallingProtocol(""); setBusy(false); }
  }

  async function waitForProtocolUpdate(image: ProtocolImage) {
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
        if (current && !current.update_available) return;
        if (actionState === "failed" || status.action?.result === "failed") {
          throw new Error(`Обновление ${image.name} завершилось с ошибкой`);
        }
      } catch (cause) {
        if (cause instanceof Error && cause.message.includes("завершилось с ошибкой")) throw cause;
        // API may restart briefly while the update installs.
      }
    }
    throw new Error(`Сервер не подтвердил обновление ${image.name} за 10 минут`);
  }

  async function updateProtocol(image: ProtocolImage) {
    if (!await askConfirmation({
      title: `Обновить ${image.name}?`,
      message: image.update_breaking
        ? `Доступна версия ${image.available_version} (сейчас ${image.installed_version || "—"}). Это смена старшей версии протокола — после обновления может понадобиться пересоздать подключения, использующие этот протокол.`
        : image.id === "awg"
          ? `В репозитории Amnezia доступна новая сборка AWG. Пакеты и модуль ядра будут обновлены без изменения существующей конфигурации.`
        : image.id === "vless-reality-xhttp"
          ? `Доступна версия Xray ${image.available_version} (сейчас ${image.installed_version || "—"}). После проверки бинарника служба VLESS будет кратковременно перезапущена, конфигурация и подключения сохранятся.`
          : `Доступна версия ${image.available_version} (сейчас ${image.installed_version || "—"}). Активные подключения не будут разорваны.`,
      confirmLabel: "Обновить",
      danger: image.update_breaking,
    })) return;
    setBusy(true); setError(""); setInstallingProtocol(`update-${image.id}`);
    try {
      const started = await request(`/protocol-images/${image.id}/update`, { method: "POST" });
      setApplication((current) => ({
        api: current?.api || { active: true, enabled: true },
        containers: current?.containers || [],
        action: started,
      }));
      await waitForProtocolUpdate(image);
      await Promise.all([loadOverview(), loadProtocolStatus(image.id as Protocol)]);
      setNotice(`${image.name} обновлён до ${image.available_version}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось запустить обновление протокола");
    } finally { setInstallingProtocol(""); setBusy(false); }
  }

  async function restartProtocol(protocol: Protocol) {
    if (!await askConfirmation({
      title: "Перезапустить протокол?",
      message: `${labels[protocol]} будет перезапущен. Активные соединения могут кратковременно прерваться.`,
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
    if (!opening) return;
    if (protocol === "wg" || protocol === "awg") {
      void checkNetworkDiagnostics(protocol);
      return;
    }
    // VRX/SS have no tunnel-specific /diagnostics/check endpoint. Build a
    // runtime diagnostic snapshot locally so the shared protocol page stays
    // functional instead of sending an unsupported request.
    setProtocolStatuses((statuses) => {
      const current = statuses[protocol];
      if (!current) return statuses;
      const serviceOk = Boolean(current.service_active);
      const checks = [
        { id: "service", name: "Служба протокола", ok: serviceOk, value: serviceOk ? "active" : "не запущена" },
        { id: "autostart", name: "Автозапуск", ok: Boolean(current.service_enabled), value: current.service_enabled ? "включён" : "выключен" },
        { id: "listener", name: "Порт протокола", ok: Boolean(current.listen_port), value: current.listen_port ? String(current.listen_port) : "не определён" },
      ];
      return {
        ...statuses,
        [protocol]: {
          ...current,
          diagnostics: {
            checked_at: new Date().toISOString(),
            status: serviceOk ? "healthy" : "critical",
            score: serviceOk ? 100 : 40,
            checks,
            findings: serviceOk ? [] : [{ severity: "critical", code: "service", title: "Служба протокола остановлена", detail: "Runtime протокола не активен.", action: "Перезапустить протокол и проверить systemd journal." }],
          },
        },
      };
    });
  }

  function toggleProtocolResources(protocol: Protocol) {
    const opening = !resourcesOpen[protocol];
    setResourcesOpen((values) => ({ ...values, [protocol]: opening }));
    if (opening) void checkProtocolResources(protocol);
  }

  async function removeProtocol(image: ProtocolImage) {
    if (!await askConfirmation({
      title: image.id === "mihomo" ? `Удалить ${image.name}?` : "Удалить протокол?",
      message: image.id === "mihomo"
        ? "Будут удалены Mihomo Core, его профили, внутренние каналы, DNS и маршрутизация. Direct-подключения GATE.312 не изменятся."
        : `${image.name} будет удалён из панели. Его конфигурация и связанные подключения станут недоступны; модуль можно установить повторно.`,
      confirmLabel: image.id === "mihomo" ? "Удалить модуль" : "Удалить", phrase: image.id === "mihomo" ? "УДАЛИТЬ" : undefined, danger: true,
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

  const protocolClients = useMemo(() => clients.filter((client) => {
    if ((tab === "wg" || tab === "awg") && client.protocol !== tab) return false;
    if (clientProtocolFilter !== "all" && client.protocol !== clientProtocolFilter) return false;
    const stateMatches = clientStateFilter === "all"
      || (clientStateFilter === "online" && client.quality === "stable")
      || (clientStateFilter === "attention" && (client.quality === "warning" || client.quality === "error"))
      || (clientStateFilter === "offline" && (!client.quality || client.quality === "offline"));
    if (!stateMatches) return false;
    const query = clientSearch.trim().toLocaleLowerCase("ru-RU");
    return !query || `${client.name} ${client.address || ""} ${client.protocol}`.toLocaleLowerCase("ru-RU").includes(query);
  }), [clients, tab, clientProtocolFilter, clientStateFilter, clientSearch]);
  const clientSummary = useMemo(() => ({
    total: clients.length,
    online: clients.filter((client) => client.quality === "stable").length,
    attention: clients.filter((client) => client.quality === "warning" || client.quality === "error").length,
    offline: clients.filter((client) => !client.quality || client.quality === "offline").length,
  }), [clients]);
  const clientPageCount = Math.max(1, Math.ceil(protocolClients.length / CLIENTS_PER_PAGE));
  const currentClientPage = Math.min(clientPage, clientPageCount);
  const visibleClients = protocolClients.slice((currentClientPage - 1) * CLIENTS_PER_PAGE, currentClientPage * CLIENTS_PER_PAGE);
  const visibleClientStart = protocolClients.length ? (currentClientPage - 1) * CLIENTS_PER_PAGE + 1 : 0;
  const visibleClientEnd = Math.min(currentClientPage * CLIENTS_PER_PAGE, protocolClients.length);
  const installedProtocols = useMemo(
    () => directProtocolOrder.filter((protocol) => protocolImages.some((image) => image.id === protocol && image.installed)),
    [protocolImages],
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
      title: "Отозвать доступ",
      message: "Подключение будет удалено, а доступ этого устройства к VPN прекратится сразу.",
      confirmLabel: "Отозвать", danger: true,
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
      setLoginPasswordVisible(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось войти в панель");
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return <LoginView
      loginUser={loginUser}
      loginPassword={loginPassword}
      loginPasswordVisible={loginPasswordVisible}
      error={error}
      busy={busy}
      version={appVersion}
      commit={buildCommit}
      setLoginUser={setLoginUser}
      setLoginPassword={setLoginPassword}
      setLoginPasswordVisible={setLoginPasswordVisible}
      login={login}
    />;
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
  const securityPerimeterChecks = [
    Boolean(firewall?.active),
    Boolean(firewall?.vpn_policy_healthy),
    panelAccessHealthy,
    Boolean(fail2ban?.active && fail2ban?.jail_active),
    sshProtected,
    ssh?.active === false || ssh?.x11_forwarding === "no",
  ];
  const securitySystemChecks = [
    Boolean(security) && Number(updates?.available || 0) === 0 && !updates?.reboot_required,
    Boolean(updates?.automatic),
    Boolean(securitySystem?.apparmor?.active),
    Boolean(securitySystem?.auditd_active),
    Boolean(securitySystem?.syn_cookies),
    Boolean(securitySystem?.rp_filter_valid && securitySystem?.dmesg_restricted),
    Boolean(securitySystem?.redirects_disabled && securitySystem?.source_route_disabled),
  ];
  const securityApplicationChecks = [
    applicationVersion?.outdated === false,
    true,
    true,
    Boolean(applicationSecurity?.admin_password_strong),
    Boolean(applicationSecurity?.secrets_protected),
    Boolean(applicationSecurity?.api_local_only),
    Boolean(applicationSecurity?.control_command_protected),
    Boolean(applicationSecurity?.cors_restricted),
  ];
  const protocolTab = tab === "channels"
    ? (installedProtocols.includes(selectedChannel) ? selectedChannel : installedProtocols[0])
    : (["wg", "awg", "shadowsocks", "vless-reality-xhttp"] as string[]).includes(tab) ? tab as Protocol : undefined;
  const activeProtocol = protocolTab ? protocolStatuses[protocolTab] : undefined;
  const activeProtocolRate = protocolTab ? protocolRates[protocolTab] || { rx: 0, tx: 0 } : { rx: 0, tx: 0 };
  const activeProtocolImage = protocolTab ? protocolImages.find((image) => image.id === protocolTab) : undefined;
  const protocolCode = protocolTab === "wg" ? "WG" : protocolTab === "awg" ? "AWG" : protocolTab === "shadowsocks" ? "SS" : protocolTab === "vless-reality-xhttp" ? "VLESS" : "";
  const protocolIsTunnel = protocolTab === "wg" || protocolTab === "awg";
  const protocolOperational = Boolean(activeProtocol && (protocolIsTunnel ? activeProtocol.active && activeProtocol.service_active : activeProtocol.service_active));
  const protocolAvailability = activeProtocol?.history.availability_percent ?? (protocolOperational ? 100 : 0);
  const protocolDiagnosticsLabel = activeProtocol?.diagnostics?.status === "healthy" ? "Стабильно" : activeProtocol?.diagnostics?.status === "critical" ? "Критично" : activeProtocol?.diagnostics?.status === "warning" ? "Требует внимания" : "Не проверено";
  const protocolResourceAvailable = activeProtocol?.resources?.items?.filter((item) => item.available).length || 0;
  const protocolResourceTotal = activeProtocol?.resources?.items?.length || 0;
  const operationActive = ["queued", "running", "active", "activating", "rebooting", "powering-off"].includes(application?.action?.state || "");
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

  const visualArt =
    tab === "overview" ? "overview"
    : tab === "mihomo" ? "mihomo"
    : tab === "clients" || tab === "access-beta" ? "direct"
    : tab === "dns" ? "network"
    : tab === "security" || tab === "services" ? "security"
    : tab === "application" ? "modules"
    : "modules";

  return <AppWorkspace
    activeTab={tab}
    visualArt={visualArt}
    protocolImages={protocolImages}
    showConnections={installedProtocols.length > 0}
    nodeState={nodeState}
    nodeStateLabel={nodeStateLabel}
    server={overview?.server}
    onNavigate={(id) => {
      if (id === "channels" && !installedProtocols.includes(selectedChannel) && installedProtocols[0]) setSelectedChannel(installedProtocols[0]);
      setTab(id as Tab);
    }}
    operationAction={application?.action}
    operationLabel={operationLabel}
    operationActive={operationActive}
    applicationStateTitle={applicationStateTitle}
    uptimeLabel={uptime(overview?.server.uptime_s)}
    loadLabel={overview?.resources.load1?.toFixed(2) || "—"}
    cpuLabel={`${(overview?.resources.cpu_percent || 0).toFixed(0)}%  ${overview?.resources.cpu_count || "—"}c`}
    ramLabel={`${memUsed.toFixed(0)}%`}
    networkLabel={`↓ ${bytes(networkRate.rx)}/с`}
    autoRefresh={autoRefresh}
    busy={busy}
    lastUpdated={lastUpdated}
    onToggleAutoRefresh={() => setAutoRefresh((value) => !value)}
    onRefresh={() => void refreshCurrent(true)}
    onLogout={() => { sessionStorage.removeItem("312-token"); setToken(""); }}
  >
      {tab !== "overview" && tab !== "access-beta" && <div className="gateSectionIntro"><div><p className="eyebrow">312.NET / {navigationLabels[tab]}</p><h1>{labels[tab]}</h1><p>{overview?.server.city}, {overview?.server.country}  управление инфраструктурой</p></div></div>}
      {error && <div className="errorBox">{error}</div>}
      {notice && <div className="successNotice" role="status"><span>✓</span>{notice}<button onClick={() => setNotice("")} aria-label="Закрыть уведомление">×</button></div>}
      {tab === "overview" && (
        <OverviewDashboard
          token={token}
          overview={overview}
          memUsed={memUsed}
          diskUsed={diskUsed}
          memoryUsedBytes={memoryUsedBytes}
          diskUsedBytes={diskUsedBytes}
          networkRate={networkRate}
          resourceHistory={resourceHistory}
          clients={clients}
          protocolImages={protocolImages}
          installingProtocol={installingProtocol}
          busy={busy}
          onInstallProtocol={(image) => void installProtocol(image)}
          onUpdateProtocol={(image) => void updateProtocol(image)}
        />
      )}

      {tab === "access-beta" && <AccessProfilesBeta token={token} />}

      {tab === "mihomo" && (
        <MihomoPage
          token={token}
          confirmAction={askConfirmation}
          coreBusy={installingProtocol === "remove-mihomo"}
          onRemoveCore={async () => {
            const image = protocolImages.find((item) => item.id === "mihomo" && item.installed);
            if (image) await removeProtocol(image);
          }}
        />
      )}

      {tab === "dns" && <DnsView
        dns={dns}
        dnsDraft={dnsDraft}
        dnsChecks={dnsChecks}
        checkingDns={checkingDns}
        busy={busy}
        setDnsDraft={setDnsDraft}
        checkDnsProviders={checkDnsProviders}
        saveDnsSettings={saveDnsSettings}
      />}

      {tab === "security" && <SecurityView
        securityLoading={securityLoading}
        securityScore={securityScore}
        securityChecks={securityChecks}
        securityPerimeterChecks={securityPerimeterChecks}
        securitySystemChecks={securitySystemChecks}
        securityApplicationChecks={securityApplicationChecks}
        firewall={firewall}
        ssh={ssh}
        updates={updates}
        applicationVersion={applicationVersion}
        securitySystem={securitySystem}
        fail2ban={fail2ban}
        listeners={listeners}
        listenerSummary={listenerSummary}
        legacy={legacy}
        applicationSecurity={applicationSecurity}
        panelSecurity={panelSecurity}
        panelAccessHealthy={panelAccessHealthy}
        sshProtected={sshProtected}
        failedSshRecords24h={String(security?.failed_ssh_records_24h ?? "—")}
        autoRefresh={autoRefresh}
        securityLogsOpen={securityLogsOpen}
        securityLogSource={securityLogSource}
        securityLogs={securityLogs}
        securityLogsUpdatedAt={securityLogsUpdatedAt}
        securityNewLogCount={securityNewLogCount}
        fixSecurity={fixSecurity}
        runApplicationAction={runApplicationAction}
        setPasswordDialog={setPasswordDialog}
        setSecurityLogsOpen={setSecurityLogsOpen}
        setSecurityLogSource={setSecurityLogSource}
        setSecurityLogs={setSecurityLogs}
        loadSecurityLogs={loadSecurityLogs}
        downloadLogs={downloadLogs}
      />}

      {tab === "application" && <ApplicationView
        application={application}
        services={services}
        applicationVersion={applicationVersion}
        updates={updates}
        serviceModeActive={serviceModeActive}
        busy={busy}
        applicationLogs={applicationLogs}
        runApplicationAction={runApplicationAction}
        changeServiceMode={changeServiceMode}
        changePanelAccess={changePanelAccess}
        loadApplicationLogs={loadApplicationLogs}
        downloadLogs={downloadLogs}
      />}

      {tab === "services" && <ServicesDashboard
        services={services}
        busy={busy}
        serviceModeActive={serviceModeActive}
        loggingDraft={loggingDraft}
        automationDraft={automationDraft}
        onServiceAction={(serviceId, serviceName, action) => void runServiceAction(serviceId, serviceName, action)}
        onServiceModeChange={(active) => void changeServiceMode(active)}
        onPanelAccessChange={(mode) => void changePanelAccess(mode)}
        onLoggingChange={updateLoggingDraft}
        onSaveLogging={() => void saveLoggingSettings()}
        onClearLogs={() => void clearManagedLogs()}
        onAutomationChange={updateAutomation}
        onSaveAutomation={() => void saveAutomation()}
      />}

      {protocolTab && activeProtocol && <ProtocolView
        protocolTab={protocolTab}
        activeProtocol={activeProtocol}
        activeProtocolRate={activeProtocolRate}
        activeProtocolImage={activeProtocolImage}
        protocolCode={protocolCode}
        protocolIsTunnel={protocolIsTunnel}
        protocolOperational={protocolOperational}
        protocolAvailability={protocolAvailability}
        protocolDiagnosticsLabel={protocolDiagnosticsLabel}
        protocolResourceAvailable={protocolResourceAvailable}
        protocolResourceTotal={protocolResourceTotal}
        installedProtocols={installedProtocols}
        setTab={setTab}
        onSelectProtocol={(protocol) => { setSelectedChannel(protocol); void loadProtocolStatus(protocol); }}
        protocolSettingsDraft={protocolSettingsDraft}
        diagnosticsOpen={diagnosticsOpen}
        resourcesOpen={resourcesOpen}
        checkingDiagnostics={checkingDiagnostics}
        checkingResources={checkingResources}
        installingProtocol={installingProtocol}
        busy={busy}
        restartProtocol={restartProtocol}
        updateProtocol={updateProtocol}
        removeProtocol={removeProtocol}
        changeProtocolSetting={changeProtocolSetting}
        saveProtocolSettings={saveProtocolSettings}
        toggleNetworkDiagnostics={toggleNetworkDiagnostics}
        checkNetworkDiagnostics={checkNetworkDiagnostics}
        toggleProtocolResources={toggleProtocolResources}
        checkProtocolResources={checkProtocolResources}
      />}

      {tab === "clients" && installedProtocols.length > 0 && <ConnectionsView
        installedProtocols={installedProtocols}
        clientStateFilter={clientStateFilter}
        setClientStateFilter={setClientStateFilter}
        setClientPage={setClientPage}
        clientSummary={clientSummary}
        deviceProbe={deviceProbe}
        probingDevice={probingDevice}
        measureDeviceRoute={measureDeviceRoute}
        openClientDialog={openClientDialog}
        protocolClients={protocolClients}
        clientSearch={clientSearch}
        setClientSearch={setClientSearch}
        clientProtocolFilter={clientProtocolFilter}
        setClientProtocolFilter={setClientProtocolFilter}
        currentClientPage={currentClientPage}
        clientPageCount={clientPageCount}
        visibleClientStart={visibleClientStart}
        visibleClientEnd={visibleClientEnd}
        visibleClients={visibleClients}
        removeClient={removeClient}
      />}
      {clientDialog && <div className="confirmBackdrop" role="presentation" onMouseDown={closeClientDialog}>
        <form className="connectionDialog addClient" role="dialog" aria-modal="true" aria-labelledby="connection-dialog-title" onMouseDown={(event) => event.stopPropagation()} onSubmit={addClient}>
          <header className="connectionDialogHead"><div><h2 id="connection-dialog-title">{generated ? "Подключение создано" : "Новое подключение"}</h2><p>{generated ? "Конфигурация готова к передаче на устройство." : "Укажите устройство и выберите протокол доступа."}</p></div><button className="connectionDialogClose" type="button" onClick={closeClientDialog}>Закрыть</button></header>
          {!generated ? <>
            <div className="connectionCreateBody">
              <div className="connectionCreateLead">
                <span>ACCESS PROFILE</span>
                <strong>Отдельный ключ для одного устройства</strong>
                <p>Имя используется только в панели. После создания конфигурацию можно передать файлом или показать QR-код владельцу устройства.</p>
              </div>
              <div className="connectionForm">
                <label>Название устройства<input autoFocus required minLength={2} maxLength={48} pattern="[\\p{L}\\p{N}_. -]{2,48}" title="От 2 до 48 символов: буквы, цифры, пробел, точка, дефис или _" value={newClient.name} onChange={(event) => setNewClient({ ...newClient, name: event.target.value })} placeholder="Например: iPhone 15" /><small className="fieldHint">2–48 символов</small></label>
                <label>Протокол<select value={selectedClientProtocol} onChange={(event) => setNewClient({ ...newClient, protocol: event.target.value as Protocol })}>{installedProtocols.map((protocol) => <option key={protocol} value={protocol}>{labels[protocol]}</option>)}</select><small className="fieldHint">Только установленные протоколы</small></label>
              </div>
            </div>
            <div className="connectionDialogActions"><button type="button" onClick={closeClientDialog}>Отмена</button><button className="primaryButton" disabled={busy}>{busy ? "Создаём…" : "Создать подключение"}</button></div>
          </> : <>
            <div className="connectionGenerated">
              <div className="connectionGeneratedSummary">
                <div><span>CONFIGURATION</span><strong>{generatedName}</strong></div>
                <div><span>PROTOCOL</span><strong>{labels[selectedClientProtocol]}</strong></div>
                <p>Конфигурация содержит приватный ключ. Передавайте её только владельцу устройства.</p>
              </div>
              <div className="connectionGeneratedGrid">
                <section className="connectionQrPanel">
                  <header><span>QR CODE</span><strong>Сканирование на устройстве</strong></header>
                  {generatedQr ? <div className="connectionQrCanvas"><Image src={generatedQr} width={284} height={284} unoptimized alt={`QR-код конфигурации ${generatedName}`} /></div> : <div className="connectionQrCanvas pending"><span>{generatedQrError || "Создаём QR-код…"}</span></div>}
                  <p>Откройте клиент протокола на устройстве и отсканируйте код.</p>
                </section>
                <section className="connectionTransfer">
                  <div className="connectionTransferIntro"><span>TRANSFER</span><strong>Передача конфигурации</strong><p>Используйте один из вариантов ниже. Файл и QR содержат одинаковую конфигурацию подключения.</p></div>
                  <button type="button" className="connectionDownloadPrimary" onClick={() => downloadConfig(generatedName, generated)}><span>Конфигурация</span><strong>Скачать файл</strong><small>{generatedName}</small></button>
                  {generatedQr && <a className="connectionDownloadSecondary" href={generatedQr} download={`${generatedName.replace(/\.conf$/i, "")}-qr.png`}><span>QR-код</span><strong>Скачать изображение</strong></a>}
                  <div className="connectionTransferNote"><strong>После передачи</strong><p>Закройте окно или создайте отдельное подключение для следующего устройства. Не используйте один ключ на нескольких устройствах.</p></div>
                </section>
              </div>
            </div>
            <div className="connectionDialogActions generatedActions"><button type="button" onClick={resetClientDialog}>Создать ещё</button><button type="button" className="primaryButton" onClick={closeClientDialog}>Готово</button></div>
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
      {confirmation && <div className={`confirmBackdrop ${tab === "application" ? "applicationConfirmBackdrop" : tab === "clients" ? "connectionConfirmBackdrop" : protocolTab ? "protocolConfirmBackdrop" : ""}`} role="presentation" onMouseDown={() => closeConfirmation(false)}>
        <form className={`confirmDialog ${confirmation.danger ? "danger" : ""} ${tab === "application" ? "applicationConfirmDialog" : tab === "clients" ? "connectionConfirmDialog" : protocolTab ? "protocolConfirmDialog" : "standardConfirmDialog"}`} role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" onMouseDown={(event) => event.stopPropagation()} onSubmit={(event) => {
          event.preventDefault();
          if (tab === "application" || !confirmation.phrase || confirmationInput === confirmation.phrase) closeConfirmation(true);
        }}>
          {tab === "application" ? <>
            <div className="applicationConfirmCopy">
              <h2 id="confirm-title">{confirmation.title}</h2>
              <p>{confirmation.message}</p>
            </div>
            <div className="applicationConfirmActions">
              <button type="button" onClick={() => closeConfirmation(false)}>Отмена</button>
              <button className={`applicationConfirmPrimary ${confirmation.danger ? "danger" : ""}`} type="submit">Выполнить</button>
            </div>
          </> : tab === "clients" ? <>
            <div className="connectionConfirmCopy">
              <h2 id="confirm-title">{confirmation.title}</h2>
              <p>{confirmation.message}</p>
            </div>
            <div className="connectionConfirmActions">
              <button type="button" onClick={() => closeConfirmation(false)}>Отмена</button>
              <button className="connectionConfirmPrimary danger" type="submit">Отозвать</button>
            </div>
          </> : protocolTab ? <>
            <div className="protocolConfirmCopy">
              <p className="eyebrow">ПОДТВЕРЖДЕНИЕ ДЕЙСТВИЯ</p>
              <h2 id="confirm-title">{confirmation.title}</h2>
              <p>{confirmation.message}</p>
              <div className="protocolConfirmObject"><span>Текущий протокол</span><strong>{labels[protocolTab]}</strong></div>
            </div>
            {confirmation.phrase && <label className="protocolConfirmPhrase">Для подтверждения введите <strong>{confirmation.phrase}</strong>
              <input autoFocus value={confirmationInput} onChange={(event) => setConfirmationInput(event.target.value)} autoComplete="off" />
            </label>}
            <div className="protocolConfirmActions">
              <button type="button" onClick={() => closeConfirmation(false)}>Отмена</button>
              <button className={confirmation.danger ? "danger" : "primary"} type="submit" disabled={Boolean(confirmation.phrase && confirmationInput !== confirmation.phrase)}>{confirmation.confirmLabel}</button>
            </div>
          </> : <>
            <header className="standardConfirmHead">
              <div className="confirmMark" aria-hidden="true">{confirmation.danger ? "!" : "✓"}</div>
              <div><p className="eyebrow">ПОДТВЕРЖДЕНИЕ ДЕЙСТВИЯ</p><h2 id="confirm-title">{confirmation.title}</h2></div>
            </header>
            <div className="standardConfirmBody">
              <p>{confirmation.message}</p>
              {confirmation.phrase && <label>Для подтверждения введите <strong>{confirmation.phrase}</strong>
                <input autoFocus value={confirmationInput} onChange={(event) => setConfirmationInput(event.target.value)} autoComplete="off" />
              </label>}
            </div>
            <div className="confirmActions">
              <button type="button" onClick={() => closeConfirmation(false)}>Отмена</button>
              <button className="confirmPrimary" type="submit" disabled={Boolean(confirmation.phrase && confirmationInput !== confirmation.phrase)}>{confirmation.confirmLabel}</button>
            </div>
          </>}
        </form>
      </div>}
      <VersionFooter />
  </AppWorkspace>;
}

function VersionFooter() {
  return <LegalFooter version={appVersion} commit={buildCommit} />;
}
