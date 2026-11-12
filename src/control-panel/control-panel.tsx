"use client";

import { useNotifications, useNotifier, useNotificationList } from "../shared/notifications/notification-center";
import { createApiClient, invalidateApiReads, invalidateApiSession, onApiSessionExpired } from "../shared/lib/api-request";
import { refreshResource } from "../shared/lib/refresh-resource";
import { useCdnSecurity } from "../features/application/use-cdn-security";
import { cdnSecuritySnapshot, type CdnSecurityStatus } from "../shared/lib/cdn-security-operation";
import { RefreshNotices, refreshFailure } from "./components/refresh-notices";
import type { RefreshFailure } from "./components/refresh-notices";
import type { ApplicationMetadata } from "../shared/types/control-plane";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ConfirmationDialog } from "../shared/components/confirmation-dialog";
import { LegalFooter } from "../shared/components/legal-footer";
import { basicCredentials } from "../features/auth/basic-credentials";
import { MihomoPage } from "../features/mihomo/mihomo-view";
import type { SectionLeaveGuard } from "../features/mihomo/rule-leave-guard";
import { OverviewDashboard } from "../features/overview/overview-view";
import { AppWorkspace } from "./components/app-workspace";
import { OperationHistory } from "./components/operation-history";
import { OperationHistorySettings, useOperationHistorySettings } from "../features/services/operation-history-settings";
import { ServicesDashboard } from "../features/services/services-view";
import { ObservabilityPanel } from "../features/services/observability-panel";
import { NetworkView, type NetworkListState } from "../features/network/network-view";
import { SecurityView } from "../features/security/security-view";
import { ApplicationView } from "../features/application/application-view";
import { LoginView } from "../features/auth/login-view";
import { AssistantView } from "../features/assistant/assistant-view";
import type { ApplicationAction, ApplicationStatus, AutomationSchedule, ConfirmationRequest, LoggingSettings, Overview, ProtocolImage, ResourceHistory, ServicesStatus, Tab } from "../shared/types/control-plane";
import { actionLabels, bytes, HISTORY_SAMPLES, labels, LIVE_SAMPLE_SECONDS, navigationLabels, uptime } from "../shared/lib/control-plane-ui";
import { systemActionSnapshot, systemAdmissionSnapshot, systemActionSucceeded, createSystemActionCompletionTracker, protocolOperationOutcome, systemActionNeedsReload, systemActionNeedsPolling, systemOperationNotification, type SystemAction } from "./system-operation";
import { useSystemAdmission } from "./use-system-admission";
import { liveStatusSnapshot, overviewSnapshot, overviewUnavailable, securityLiveObservation } from "./live-snapshot";
import { applicationStatusSnapshot, applicationStatusUnavailable, observedApplicationRequest } from "./application-snapshot";
import { observedServicesRequest, servicesUnavailable } from "./services-snapshot";
import { securitySnapshot, securityUnavailable } from "./security-snapshot";
import { applicationMetadataSnapshot, applicationMetadataUnavailable } from "./application-metadata-snapshot";
import { applicationVersionState } from "../features/application/version-state";
import { appendResources, networkSample as sampleNetwork, usedBytes, usedPercent } from "../shared/lib/resource-metrics";
import type { AdoptionPlan, ComponentManagement, MetricsSettings } from "../shared/types/control-plane";

const appVersion = process.env.NEXT_PUBLIC_APP_VERSION || "v1.0.0";
const buildCommit = process.env.NEXT_PUBLIC_BUILD_COMMIT || "unknown";
const releaseChannel = process.env.NEXT_PUBLIC_RELEASE_CHANNEL || "production";

function reloadWithoutCache(message: string) {
  if (message) sessionStorage.setItem("312-notice", message);
  const target = new URL(window.location.href);
  target.searchParams.set("_refresh", Date.now().toString());
  window.location.replace(target.toString());
}

const TECHNICAL_ERROR = /(?:\n|traceback|systemctl|journalctl|apt(?:-get)?|dpkg|stderr|stdout|exit status|failed to start|reading package lists|building dependency tree)/i;
function publicError(message: string, status: number) {
  const value = message.trim();
  if (status >= 500 || TECHNICAL_ERROR.test(value) || /^(?:bad gateway|internal server error)$/i.test(value)) {
    return "Команда завершилась с ошибкой. Технические сведения сохранены в журнале.";
  }
  return value || "Команда не выполнена.";
}
type SshAuthorizedKey = { fingerprint: string; type: string; comment?: string; managed?: boolean };
type SshAccessState = { phase: "password" | "key-installed" | "awaiting-confirmation" | "hardened" | "rolled-back" | "open"; fingerprint: string; rollback_deadline?: string | null; message: string; key_login_observed?: boolean; keys?: SshAuthorizedKey[] };
export function ControlPanel() {
  const [tab, setTab] = useState<Tab>("overview");
  const [sectionDirty, setSectionDirty] = useState(false);
  const mihomoLeaveGuard = useRef<SectionLeaveGuard | null>(null);
  const registerMihomoLeaveGuard = useCallback((guard: SectionLeaveGuard | null) => { mihomoLeaveGuard.current = guard; }, []);
  const [networkListState, setNetworkListState] = useState<NetworkListState>({ query: "", expanded: [] });
  const [networkRefreshKey, setNetworkRefreshKey] = useState(0);
  const [mcpRefreshKey, setMcpRefreshKey] = useState(0);
  const [mihomoRefreshKey, setMihomoRefreshKey] = useState(0);
  const [token, setToken] = useState("");
  const loginInFlight = useRef(false);
  const [loginUser, setLoginUser] = useState("admin");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginPasswordVisible, setLoginPasswordVisible] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [security, setSecurity] = useState<Record<string, unknown> | null>(null);
  const [securityLoading, setSecurityLoading] = useState(false);
  const [securityLogSource, setSecurityLogSource] = useState<"ssh" | "firewall" | "system">("ssh");
  const [securityLogs, setSecurityLogs] = useState<string[]>([]);
  const [securityLogsUpdatedAt, setSecurityLogsUpdatedAt] = useState<Date | null>(null);
  const [securityNewLogCount, setSecurityNewLogCount] = useState(0);
  const [application, setApplication] = useState<ApplicationStatus | null>(null);
  const [applicationMetadata, setApplicationMetadata] = useState<ApplicationMetadata | null>(null);
  const [services, setServices] = useState<ServicesStatus | null>(null);
  const [automationDraft, setAutomationDraft] = useState<ServicesStatus["automation"] | null>(null);
  const [loggingDraft, setLoggingDraft] = useState<LoggingSettings | null>(null);
  const [metricsSettings, setMetricsSettings] = useState<MetricsSettings | null>(null);
  const [metricsDraft, setMetricsDraft] = useState<MetricsSettings | null>(null);
  const notifications = useNotifications();
  const notificationItems = useNotificationList();
  const { error: notifyError, success: notifySuccess } = useNotifier("panel", "Панель");
  const [applicationLogs, setApplicationLogs] = useState<string[]>([]);
  const [securityLogsOpen, setSecurityLogsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [moduleMenuOpen, setModuleMenuOpen] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [viewLoading, setViewLoading] = useState(false);
  const viewLoadingTimer = useRef<number | null>(null);
  const viewLoadingRun = useRef(0);
  const [networkRate, setNetworkRate] = useState<{ rx: number | null; tx: number | null }>({ rx: null, tx: null });
  const [resourceHistory, setResourceHistory] = useState<ResourceHistory>({ load: [], memory: [], disk: [], rx: [], tx: [] });
  const [protocolImages, setProtocolImages] = useState<ProtocolImage[]>([]);
  const [installingProtocol, setInstallingProtocol] = useState("");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshErrors, setRefreshErrors] = useState<Record<string, RefreshFailure>>({});
  const [localBusy, setBusy] = useState(false);
  const [reloadRequested, setReloadRequested] = useState(false);
  const requestCommandReload = useCallback(() => setReloadRequested(true), []);
  const [trackActionCompletion] = useState(createSystemActionCompletionTracker);
  const applicationObservationVersion = useRef(0);
  const overviewReadVersion = useRef(0);
  const acceptedOperation = useRef<{ id?: string; state?: string } | null>(null);
  const [passwordDialog, setPasswordDialog] = useState(false);
  const [sshAdminDialog, setSshAdminDialog] = useState(false);
  const [sshPublicKey, setSshPublicKey] = useState("");
  const [sshAccessState, setSshAccessState] = useState<SshAccessState | null>(null);
  const [sshKeyTested, setSshKeyTested] = useState(false);
  const pendingSsh = useRef<{ id?: string; key?: string } | null>(null);
  const [sshCountdownClock, setSshCountdownClock] = useState(() => Date.now());
  const [mihomoProfilesPage, setMihomoProfilesPage] = useState(1);
  const [currentAdminPassword, setCurrentAdminPassword] = useState("");
  const [newAdminPassword, setNewAdminPassword] = useState("");
  const [confirmAdminPassword, setConfirmAdminPassword] = useState("");
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null);
  const settingsRef = useRef<HTMLDivElement>(null);
  const networkSample = useRef<{ rx: number; tx: number; at: number } | null>(null);
  const servicesLoading = useRef(false);
  const applicationMetadataLoading = useRef(false);
  const securityStatusLoading = useRef(false);
  const securityObservationVersion = useRef(0);
  const metricsObservationVersion = useRef(0);
  const securityLogHeads = useRef<Partial<Record<"ssh" | "firewall" | "system", string>>>({});
  const sshAccessLoading = useRef(false);
  const automationDirty = useRef(false);
  const pendingAutomation = useRef<{ id?: string; draft: ServicesStatus["automation"] } | null>(null);
  const pendingAutomationRecovery = useRef<string | null>(null);
  const loggingDirty = useRef(false);
  const pendingLogging = useRef<{ id?: string; draft: LoggingSettings } | null>(null);
  const metricsDirty = useRef(false);
  const liveRequestInFlight = useRef(false);
  const pendingConfirmation = useRef<ConfirmationRequest | null>(null);
  useEffect(() => { pendingConfirmation.current = confirmation; }, [confirmation]);
  const clearSession = useCallback(() => {
    applicationObservationVersion.current += 1;
    sessionStorage.removeItem("312-token");
    pendingConfirmation.current?.resolve(false);
    pendingConfirmation.current = null;
    setConfirmation(null);
    setToken(""); setLoginPassword(""); setLoginPasswordVisible(false);
    setCurrentAdminPassword(""); setNewAdminPassword(""); setConfirmAdminPassword("");
    setPasswordDialog(false); setSshAdminDialog(false);
    setSshPublicKey(""); setSshAccessState(null); setSshKeyTested(false);
    setApplicationLogs([]); setSecurityLogs([]);
    setBusy(false); setInstallingProtocol(""); setReloadRequested(false);
    notifications.reset();
  }, [notifications]);
  useEffect(() => {
    if (!token) return;
    return onApiSessionExpired(token, () => {
      clearSession();
      notifyError("Сессия панели завершена. Войдите заново.");
    });
  }, [token, clearSession, notifyError]);

  useEffect(() => {
    const target = new URL(window.location.href);
    if (target.searchParams.has("_refresh")) {
      target.searchParams.delete("_refresh");
      window.history.replaceState(window.history.state, "", `${target.pathname}${target.search}${target.hash}`);
    }

    // Restore browser-only credentials after hydration.
    setToken(sessionStorage.getItem("312-token") || "");
    const savedTab = sessionStorage.getItem("312-reload-tab");
    if (savedTab && ["overview", "mihomo", "dns", "network", "security", "application", "services", "nodera", "ai-agent", "telegram-bot"].includes(savedTab)) setTab((savedTab === "dns" ? "network" : ["ai-agent", "telegram-bot"].includes(savedTab) ? "nodera" : savedTab) as Tab);
    sessionStorage.removeItem("312-reload-tab");
    sessionStorage.removeItem("312-reload-channel");
    const savedNotice = sessionStorage.getItem("312-notice");
    if (savedNotice) {
      notifySuccess(savedNotice);
      sessionStorage.removeItem("312-notice");
    }
  }, [notifySuccess]);

  useEffect(() => {
    function closeSettings(event: PointerEvent) {
      if (settingsOpen && settingsRef.current && !settingsRef.current.contains(event.target as Node)) setSettingsOpen(false);
      if (moduleMenuOpen && !(event.target as Element).closest(".moduleMenuWrap")) setModuleMenuOpen("");
    }
    document.addEventListener("pointerdown", closeSettings);
    return () => document.removeEventListener("pointerdown", closeSettings);
  }, [moduleMenuOpen, settingsOpen]);

  const request = useMemo(() => observedServicesRequest(observedApplicationRequest(createApiClient(token, {
    formatHttpError: (detail, status) => status === 401 ? "Сессия панели завершена. Войдите заново." : publicError(detail, status),
  }), (value) => applicationStatusSnapshot(value, systemActionSnapshot, cdnSecuritySnapshot))), [token]);

  const acceptSystemOperation = useCallback((operation: SystemAction) => {
    if (acceptedOperation.current?.id !== operation.id || acceptedOperation.current?.state !== operation.state) {
      applicationObservationVersion.current += 1;
      invalidateApiReads(token);
      setOverview(overviewUnavailable);
      setServices(servicesUnavailable);
      setSecurity(securityUnavailable);
      setApplicationMetadata(applicationMetadataUnavailable);
      networkSample.current = null;
      setNetworkRate({ rx: null, tx: null });
    }
    acceptedOperation.current = { id: operation.id, state: operation.state };
    setApplication((current) => ({ ...current, api: current?.api || { active: null, enabled: null }, containers: current?.containers || [], action: operation }));
  }, [token]);
  const { pending: admissionPending, submit: submitSystemOperation, retain: retainAdmission, recheck: recheckAdmission, cancel: cancelSystemOperation } = useSystemAdmission(request, token, acceptSystemOperation);
  const busy = localBusy || admissionPending || systemActionNeedsPolling(application?.action);
  const admitNetworkOperation = useCallback((path: string, options: RequestInit) => submitSystemOperation(request, path, options), [request, submitSystemOperation]);

  const operationHistoryPolicy = useOperationHistorySettings(request, Boolean(token));

  function askConfirmation(options: Omit<ConfirmationRequest, "resolve">): Promise<boolean> {

    pendingConfirmation.current?.resolve(false);
    return new Promise((resolve) => {
      const next = { ...options, resolve };
      pendingConfirmation.current = next;
      setConfirmation(next);
    });
  }

  const settingsDirty = sectionDirty || Boolean(loggingDirty.current || automationDirty.current || metricsDirty.current)
    || Boolean(operationHistoryPolicy.draft && operationHistoryPolicy.saved && JSON.stringify(operationHistoryPolicy.draft) !== JSON.stringify(operationHistoryPolicy.saved));
  useEffect(() => {
    if (!settingsDirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [settingsDirty]);

  async function canLeaveSection() {
    let dirty = sectionDirty;
    if (tab === "mihomo" && mihomoLeaveGuard.current) {
      const result = await mihomoLeaveGuard.current();
      if (!result.allowed) return false;
      dirty = result.dirty;
    }
    if (dirty && !await askConfirmation({ title: "Уйти без сохранения?", message: "Несохранённые настройки текущего раздела будут потеряны. Серверные настройки не изменятся.", cancelLabel: "Остаться", confirmLabel: "Уйти без сохранения" })) return false;
    return true;
  }

  async function navigateTo(next: Tab) {
    if (next === tab || !await canLeaveSection()) return;
    setTab(next);
  }

  function closeConfirmation(confirmed: boolean) {
    const current = pendingConfirmation.current;
    if (!current) return;
    pendingConfirmation.current = null;
    setConfirmation(null);

    current.resolve(confirmed);
  }

  const applyCdnSecurity = useCallback((status: CdnSecurityStatus) => {
    setApplication((current) => current ? { ...current, cdn_security: status } : current);
  }, []);
  const cdnCommand = useCdnSecurity(request, token, application?.cdn_security?.operation, applyCdnSecurity);

  const loadOverview = useCallback(async (force = false) => {
    if (!token) return;
    const observationVersion = applicationObservationVersion.current;
    const readVersion = ++overviewReadVersion.current;
    const isCurrent = () => observationVersion === applicationObservationVersion.current && readVersion === overviewReadVersion.current;
    const report = (key: string, cause?: unknown) => {
      setRefreshErrors((current) => {
        const errors = { ...current };
        if (cause === undefined) delete errors[key];
        else errors[key] = refreshFailure(cause, key === "loadOverview" ? "Не удалось обновить телеметрию VPS" : "Не удалось обновить каталог компонентов");
        return errors;
      });
    };
    await Promise.all([
      refreshResource(() => request("/overview", { cache: "no-store" }).then(overviewSnapshot), (next) => {
        setOverview(next);
        setLastUpdated(new Date());
        report("loadOverview");
      }, (cause) => { setOverview(overviewUnavailable); report("loadOverview", cause); }, isCurrent),
      refreshResource<{ items: ProtocolImage[] }>((updates) => request("/protocol-images", { ...updates, ...(force ? { cache: "no-store" } : {}) }), (data) => {
        if (!Array.isArray(data?.items)) throw new Error("Некорректный каталог компонентов");
        setProtocolImages(data.items);
        if (installingProtocol && data.items.some((image) => image.id === installingProtocol && image.installed)) setInstallingProtocol("");
        setLastUpdated(new Date());
        report("loadProtocolImages");
      }, (cause) => { report("loadProtocolImages", cause); }, isCurrent),
    ]);
  }, [installingProtocol, request, token]);

  useEffect(() => {
    // A restored section also needs the server and module inventory used by
    // navigation. Previously these were loaded only by opening Overview.
    if (token && !overview && tab !== "overview") void loadOverview();
  }, [loadOverview, overview, tab, token]);

  useEffect(() => {
    if (!token || !autoRefresh || tab !== "overview" || !protocolImages.some((image) => image.version_checking)) return;
    const timer = window.setTimeout(() => void loadOverview(true), 3000);
    return () => window.clearTimeout(timer);
  }, [autoRefresh, loadOverview, protocolImages, tab, token]);

  const loadSecurity = useCallback(async (force = false) => {
    if (!token || securityStatusLoading.current) return;
    securityStatusLoading.current = true;
    const observationVersion = applicationObservationVersion.current;
    const securityVersion = ++securityObservationVersion.current;
    void force;
    setSecurityLoading(true);
    try {
      const snapshot = securitySnapshot(await request("/security", { cache: "no-store" }));
      if (observationVersion !== applicationObservationVersion.current || securityVersion !== securityObservationVersion.current) return;
      securityObservationVersion.current += 1;
      setSecurity(snapshot); setLastUpdated(new Date());
      setRefreshErrors((current) => { const next = { ...current }; delete next.loadSecurity; return next; });
    } catch (cause) {
      if (observationVersion !== applicationObservationVersion.current || securityVersion !== securityObservationVersion.current) return;
      securityObservationVersion.current += 1;
      setSecurity(securityUnavailable);
      setRefreshErrors((current) => ({ ...current, loadSecurity: refreshFailure(cause, "Не удалось обновить состояние безопасности") }));
    }
    finally { securityStatusLoading.current = false; setSecurityLoading(false); }
  }, [request, token]);

  const loadApplication = useCallback(async (force = false) => {
    if (!token) return;
    const observationVersion = applicationObservationVersion.current;
    try {
      const snapshot = await request<ApplicationStatus>("/application/status", force ? { cache: "no-store" } : undefined);
      if (observationVersion !== applicationObservationVersion.current) return;
      setApplication(retainAdmission(snapshot)); setLastUpdated(new Date());
      setRefreshErrors((current) => { const next = { ...current }; delete next.loadApplication; return next; });
    } catch (cause) {
      if (observationVersion !== applicationObservationVersion.current) return;
      setApplication((current) => applicationStatusUnavailable(current, systemActionNeedsPolling(current?.action)));
      setRefreshErrors((current) => ({ ...current, loadApplication: refreshFailure(cause, "Не удалось обновить приложение") }));
    }
  }, [request, retainAdmission, token]);

  const loadServices = useCallback(async (force = false) => {
    if (!token || servicesLoading.current) return;
    servicesLoading.current = true;
    const observationVersion = applicationObservationVersion.current;
    const metricVersion = ++metricsObservationVersion.current;
    try {
      const isCurrent = () => observationVersion === applicationObservationVersion.current;
      const report = (key: string, cause?: unknown) => setRefreshErrors((current) => {
        const next = { ...current };
        if (cause === undefined) delete next[key];
        else next[key] = refreshFailure(cause, key === "loadServices" ? "Не удалось обновить состояние служб" : "Не удалось обновить настройки хранения метрик");
        return next;
      });
      await Promise.all([
        refreshResource(() => request<ServicesStatus>("/services", force ? { cache: "no-store" } : undefined), (next) => {
          setServices(next);
          if (!automationDirty.current) setAutomationDraft(next.automation);
          if (!loggingDirty.current) setLoggingDraft({ persistent: next.logging?.persistent ?? true, retention_days: next.logging?.retention_days ?? 30 });
          setLastUpdated(new Date());
          report("loadServices");
        }, (cause) => { setServices(servicesUnavailable); report("loadServices", cause); }, isCurrent),
        refreshResource(() => request<MetricsSettings>("/services/metrics", force ? { cache: "no-store" } : undefined), (next) => {
          setMetricsSettings(next);
          if (!metricsDirty.current) setMetricsDraft(next);
          report("loadMetricsSettings");
        }, (cause) => { setMetricsSettings(null); report("loadMetricsSettings", cause); },
        () => isCurrent() && metricVersion === metricsObservationVersion.current),
      ]);
    } finally { servicesLoading.current = false; }
  }, [request, token]);

  const loadApplicationMetadata = useCallback(async (force = false) => {
    if (!token || applicationMetadataLoading.current) return;
    applicationMetadataLoading.current = true;
    const observationVersion = applicationObservationVersion.current;
    void force;
    try {
      const snapshot = applicationMetadataSnapshot(await request("/application/metadata", { cache: "no-store" }));
      if (observationVersion !== applicationObservationVersion.current) return;
      setApplicationMetadata(snapshot);
      setRefreshErrors((current) => { const next = { ...current }; delete next.loadApplicationMetadata; return next; });
    } catch (cause) {
      if (observationVersion !== applicationObservationVersion.current) return;
      setApplicationMetadata(applicationMetadataUnavailable);
      setRefreshErrors((current) => ({ ...current, loadApplicationMetadata: refreshFailure(cause, "Не удалось обновить сведения о версии") }));
    } finally { applicationMetadataLoading.current = false; }
  }, [request, token]);

  const beginViewLoading = useCallback(() => {
    const run = ++viewLoadingRun.current;
    if (viewLoadingTimer.current !== null) {
      window.clearTimeout(viewLoadingTimer.current);
    }
    viewLoadingTimer.current = window.setTimeout(() => {
      if (run === viewLoadingRun.current) setViewLoading(true);
    }, 220);
    return run;
  }, []);

  const endViewLoading = useCallback((run?: number) => {
    if (run !== undefined && run !== viewLoadingRun.current) return;
    viewLoadingRun.current += 1;
    if (viewLoadingTimer.current !== null) {
      window.clearTimeout(viewLoadingTimer.current);
      viewLoadingTimer.current = null;
    }
    setViewLoading(false);
  }, []);

  const handleNetworkLoadingChange = useCallback(() => undefined, []);
  const handleMihomoLoadingChange = useCallback(() => undefined, []);

  useEffect(() => () => {
    if (viewLoadingTimer.current !== null) window.clearTimeout(viewLoadingTimer.current);
  }, []);

  useEffect(() => {
    if (!token) endViewLoading();
  }, [endViewLoading, token]);

  const refreshCurrent = useCallback(async (showBusy = false, showTransition = false) => {
    if (!token) return;
    const transitionRun = showTransition ? beginViewLoading() : undefined;
    if (showBusy) { setBusy(true); }
    try {
      if (tab === "overview") await Promise.all([loadOverview(showBusy), loadApplication(showBusy), loadServices(showBusy)]);
      else if (tab === "security") await Promise.all([loadSecurity(showBusy), loadServices(showBusy)]);
      else if (tab === "application") await Promise.all([loadApplication(showBusy), loadApplicationMetadata(showBusy), loadServices(showBusy)]);
      else if (tab === "services") await loadServices(showBusy);
      else if (tab === "dns") { setTab("network"); setNetworkRefreshKey((value) => value + 1); }
      else if (tab === "network") setNetworkRefreshKey((value) => value + 1);
      else if (tab === "nodera") setMcpRefreshKey((value) => value + 1);
      else if (tab === "mihomo") {
        if (showBusy) setMihomoRefreshKey((value) => value + 1);
        await loadOverview(showBusy);
      }
    } finally {
      if (showTransition) endViewLoading(transitionRun);
      if (showBusy) setBusy(false);
    }
  }, [beginViewLoading, endViewLoading, loadApplication, loadApplicationMetadata, loadOverview, loadSecurity, loadServices, tab, token]);

  useEffect(() => {
    if (!token) return;
    sessionStorage.setItem("312-token", token);
  }, [token]);

  const actionNeedsPolling = systemActionNeedsPolling(application?.action);

  useEffect(() => {
    if (!token || !autoRefresh) return;
    // Security data is intentionally refreshed only when entering the tab;
    // its checks are expensive and the page displays the last known snapshot.
    if (tab === "security") return;
    const updateRunning = actionNeedsPolling && ["update", "test-update", "test-rollback", "safe-update", "kernel-update"].includes(application?.action?.action || "");
    const delay = updateRunning ? 3000
      : tab === "overview" ? 30000
        : ["channels", "wg", "awg", "shadowsocks", "vless-reality-xhttp", "clients"].includes(tab) ? 15000
          : 10000;
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void refreshCurrent(false); }, delay);
    return () => window.clearInterval(timer);
  }, [application?.action?.action, actionNeedsPolling, autoRefresh, refreshCurrent, tab, token]);

  useEffect(() => {
    if (!token || !actionNeedsPolling) return;
    const timer = window.setInterval(() => void loadApplication(), 2000);
    return () => window.clearInterval(timer);
  }, [actionNeedsPolling, loadApplication, token]);

  useEffect(() => {
    // Finish the package observation started by GET /security even when regular
    // full security polling is disabled. Stop as soon as it has a result.
    if (!token || tab !== "security" || (security?.updates as ApplicationMetadata["updates"] | undefined)?.refreshing !== true) return;
    const timer = window.setTimeout(() => void loadSecurity(), 3000);
    return () => window.clearTimeout(timer);
  }, [loadSecurity, security, tab, token]);

  useEffect(() => {
    const completed = trackActionCompletion(application?.action);
    if (!completed) return;
    // React to a newly confirmed server result, never the historical status on mount.
    if (systemActionNeedsReload(completed)) requestCommandReload();
    else void refreshCurrent(false);
  }, [application?.action, refreshCurrent, requestCommandReload, trackActionCompletion]);

  useEffect(() => {
    const action = application?.action;
    const pending = pendingLogging.current;
    if (!pending?.id || action?.id !== pending.id) return;
    if (systemActionSucceeded(action)) {
      pendingLogging.current = null;
      // An edit made while the worker runs must remain a local draft.
      if (JSON.stringify(loggingDraft) === JSON.stringify(pending.draft)) loggingDirty.current = false;
      void loadServices(true);
    } else if (action.state === "failed") pendingLogging.current = null;
  }, [application?.action, loggingDraft, loadServices]);

  useEffect(() => {
    const action = application?.action;
    const pending = pendingAutomation.current;
    if (!pending?.id || action?.id !== pending.id) return;
    if (systemActionSucceeded(action)) {
      pendingAutomation.current = null;
      if (JSON.stringify(automationDraft) === JSON.stringify(pending.draft)) automationDirty.current = false;
      void loadServices(true);
    } else if (action.state === "failed") {
      pendingAutomation.current = null;
      void loadServices(true);
    }
  }, [application?.action, automationDraft, loadServices]);

  useEffect(() => {
    const action = application?.action;
    if (!pendingAutomationRecovery.current || action?.id !== pendingAutomationRecovery.current) return;
    if (action.state === "succeeded" || action.state === "failed" || action.state === "cancelled") {
      pendingAutomationRecovery.current = null;
      void loadServices(true);
    }
  }, [application?.action, loadServices]);

  // Historical errors and unknown notification cards must not hold a completed
  // command's reload forever. Only the current mutation owns this boundary.
  const reloadBlocked = busy || Boolean(installingProtocol);
  useEffect(() => {
    if (!token || !reloadRequested || reloadBlocked) return;
    const timer = window.setTimeout(() => {
      sessionStorage.setItem("312-reload-tab", tab);
      reloadWithoutCache("");
    }, 600);
    return () => window.clearTimeout(timer);
  }, [reloadBlocked, reloadRequested, tab, token]);

  useEffect(() => {
    if (!token) return;
    // Load tab data in the background; each screen owns its local loading state.
    void refreshCurrent(false, false);
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
      setRefreshErrors((current) => { const next = { ...current }; delete next.loadSecurityLogs; return next; });
    } catch (cause) { setRefreshErrors((current) => ({ ...current, loadSecurityLogs: refreshFailure(cause, "Не удалось загрузить журнал") })); }
  }, [request, securityLogSource, token]);

  const loadApplicationLogs = useCallback(async () => {
    if (!token) return;
    try {
      const data = await request("/application/logs?lines=180");
      setApplicationLogs(data.lines || []);
      setRefreshErrors((current) => { const next = { ...current }; delete next.loadApplicationLogs; return next; });
    } catch (cause) { setRefreshErrors((current) => ({ ...current, loadApplicationLogs: refreshFailure(cause, "Не удалось загрузить журнал приложения") })); }
  }, [request, token]);

  const loadLiveStatus = useCallback(async () => {
    if (!token || liveRequestInFlight.current || document.visibilityState !== "visible") return;
    liveRequestInFlight.current = true;
    const observationVersion = applicationObservationVersion.current;
    const securityVersion = securityObservationVersion.current;
    try {
      const next = liveStatusSnapshot(await request("/live-status", { cache: "no-store" }));
      if (observationVersion !== applicationObservationVersion.current) return;
      const now = Date.now();
      const sampled = sampleNetwork(next.resources, networkSample.current, now);
      setNetworkRate(sampled.rate);
      setResourceHistory((history) => appendResources(history, next.resources, sampled.rate, HISTORY_SAMPLES));
      networkSample.current = sampled.sample;
      setOverview((current) => current ? {
        ...current,
        server: { ...current.server, uptime_s: next.resources.uptime_s === undefined ? current.server.uptime_s : next.resources.uptime_s },
        resources: next.resources,
        protocols: {
          wg: { ...current.protocols.wg, active: next.protocols.wg?.active ?? null },
          awg: { ...current.protocols.awg, active: next.protocols.awg?.active ?? null },
        },
      } : current);
      if (securityVersion === securityObservationVersion.current) setSecurity((current) => securityLiveObservation(current, next.security));
      setRefreshErrors((current) => {
        const errors = { ...current };
        const unavailable = [...(next.unavailable || []), ...(next.resources.stale ? ["resources"] : [])];
        if (unavailable.length) errors.liveSources = { message: `Stale — не обновились: ${unavailable.join(", ")}`, network: false };
        else delete errors.liveSources;
        return errors;
      });
      setLastUpdated(new Date());
    } catch (cause) {
      if (observationVersion !== applicationObservationVersion.current) return;
      networkSample.current = null;
      setNetworkRate({ rx: null, tx: null });
      setOverview(overviewUnavailable);
      if (securityVersion === securityObservationVersion.current) setSecurity((current) => securityLiveObservation(current, null));
      setResourceHistory((history) => Object.fromEntries(Object.entries(history).map(([key, values]) => [key, [...values, null].slice(-HISTORY_SAMPLES)])) as ResourceHistory);
      setRefreshErrors((current) => ({ ...current, liveSources: refreshFailure(cause, "Не удалось обновить live-телеметрию") }));
    } finally {
      liveRequestInFlight.current = false;
    }
  }, [request, token]);

  useEffect(() => {
    if (!token || !autoRefresh || !["overview", "security"].includes(tab)) return;
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
    const risky = action === "restart" || action === "update" || action === "test-update" || action === "test-rollback" || action === "identity" || action === "safe-update" || action === "kernel-update" || action === "reboot";
    if (!automatic && risky && !await askConfirmation({
      title: actionLabels[action] || "Выполнить команду?",
      message: action === "safe-update" || action === "kernel-update"
        ? "Будут установлены все доступные обновления Debian/Ubuntu, включая ядро. Перед установкой создаётся и проверяется зашифрованная копия приложения, личных данных и конфигурации. После успешной проверки сервер автоматически перезагрузится; соединения временно прервутся. Копия не является снимком диска и не восстанавливает старые пакеты ОС или загрузку VPS. Удаление пакетов запрещено."
        : `Будет выполнена команда «vps-control ${action}». Во время операции возможен кратковременный перерыв в работе.`,
      confirmLabel: action === "safe-update" || action === "kernel-update" ? "Создать точку и обновить" : "Выполнить", danger: action === "reboot",
    })) return;
    setBusy(true);
    try {
      const started = await submitSystemOperation(request, "/application/action", { method: "POST", body: JSON.stringify({ action }) });
      trackActionCompletion(started);
      setApplication((current) => ({
        ...current,
        api: current?.api || { active: null, enabled: null },
        containers: current?.containers || [],
        action: started,
        service_mode: current?.service_mode,
        cdn_security: current?.cdn_security,
        runtime: current?.runtime,
      }));
      if (action === "reboot" || action === "poweroff") return;
      await loadApplication(); await loadApplicationLogs();
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Команда не запущена"); }
    finally { setBusy(false); }
  }

  async function cancelApplicationAction() {
    const initial = application?.action;
    if (!initial) return;
    await cancelSystemOperation(initial);
    await loadApplicationLogs();
  }

  async function recheckApplicationAction() {
    if (admissionPending) { await recheckAdmission(); return; }
    const initial = application?.action;
    if (!initial?.id || !/^[a-f0-9]{32}$/.test(initial.id)) { await loadApplication(true); return; }
    try {
      acceptSystemOperation(systemAdmissionSnapshot(await request(`/application/operations/${initial.id}`, { cache: "no-store" }), initial.id, initial));
    } catch {
      acceptSystemOperation({ ...initial, state: "unknown", progress: undefined, message: "Результат команды не подтверждён. Проверяем без повторной отправки." });
    }
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
    setBusy(true);
    try {
      const started = await submitSystemOperation(request, `/services/${serviceId}/action`, { method: "POST", body: JSON.stringify({ action }) });
      setApplication((current) => ({ ...current, api: current?.api || { active: null, enabled: null }, containers: current?.containers || [], action: started }));
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось выполнить действие со службой"); }
    finally { setBusy(false); }
  }

  async function fixSecurity(action: "secure" | "safe-update" | "vpn-firewall") {
    await runApplicationAction(action);
    await loadSecurity();
  }

  async function downloadUpdateReport() {
    setBusy(true);
    try {
      const report = await request("/application/update-report") as { filename: string; lines: string[] };
      downloadLogs(report.filename, report.lines);
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось получить отчёт обновления"); }
    finally { setBusy(false); }
  }

  const loadSshAccess = useCallback(async () => {
    if (!token || sshAccessLoading.current) return;
    sshAccessLoading.current = true;
    try { setSshAccessState(await request<SshAccessState>("/security/ssh-access")); setSshCountdownClock(Date.now()); }
    catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось получить состояние SSH-доступа"); }
    finally { sshAccessLoading.current = false; }
  }, [token, request, notifyError]);

  useEffect(() => {
    const action = application?.action;
    const pending = pendingSsh.current;
    if (!pending?.id || action?.id !== pending.id) return;
    if (!systemActionSucceeded(action) && action.state !== "failed") return;
    pendingSsh.current = null;
    if (systemActionSucceeded(action)) {
      if (pending.key !== undefined && sshPublicKey === pending.key) setSshPublicKey("");
      setSshKeyTested(false);
    }
    void loadSshAccess();
  }, [application?.action, loadSshAccess, sshPublicKey]);

  async function installSshKey() {
    setBusy(true);
    try {
      const started = await submitSystemOperation(request, "/security/ssh-access/key", { method: "POST", body: JSON.stringify({ public_key: sshPublicKey.trim() }) });
      pendingSsh.current = { id: started.id, key: sshPublicKey };
      setApplication((current) => ({ ...current, api: current?.api || { active: null, enabled: null }, containers: current?.containers || [], action: started }));
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось установить публичный ключ"); }
    finally { setBusy(false); }
  }

  async function deleteSshKey(fingerprint: string) {
    if (!await askConfirmation({ title: "Удалить SSH-ключ?", message: `Публичный ключ ${fingerprint} будет удалён из root/.ssh/authorized_keys. Приватный ключ на компьютере не удаляется.`, confirmLabel: "Удалить ключ", danger: true })) return;
    setBusy(true);
    try {
      const started = await submitSystemOperation(request, "/security/ssh-access/key/delete", { method: "POST", body: JSON.stringify({ fingerprint }) });
      pendingSsh.current = { id: started.id, key: sshPublicKey };
      setApplication((current) => ({ ...current, api: current?.api || { active: null, enabled: null }, containers: current?.containers || [], action: started }));
      await loadSecurity();
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось удалить SSH-ключ"); }
    finally { setBusy(false); }
  }

  async function changeSshAccess(action: "begin" | "confirm" | "rollback" | "disable") {
    if (action === "disable" && !await askConfirmation({
      title: "Отключить защищённый SSH-доступ?",
      message: "Будет открыт публичный SSH/22, вход root по паролю и ключу. Это снизит защиту сервера; Fail2ban останется активным.",
      confirmLabel: "Открыть SSH/22",
      danger: true,
    })) return;
    setBusy(true);
    try {
      const started = await submitSystemOperation(request, `/security/ssh-access/${action}`, { method: "POST" });
      pendingSsh.current = { id: started.id };
      setApplication((current) => ({ ...current, api: current?.api || { active: null, enabled: null }, containers: current?.containers || [], action: started }));
      if (action !== "rollback") setSshKeyTested(false);
      await loadSecurity();
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось изменить SSH-доступ"); }
    finally { setBusy(false); }
  }

  function openSshAdminDialog() {
    setSshAdminDialog(true);
    setSshKeyTested(false);
    void loadSshAccess();
  }

  useEffect(() => {
    if (!sshAdminDialog || sshAccessState?.phase !== "awaiting-confirmation" || !sshAccessState.rollback_deadline) return;
    const deadline = new Date(sshAccessState.rollback_deadline).getTime();
    const interval = window.setInterval(() => {
      const now = Date.now();
      setSshCountdownClock(now);
      if (now >= deadline) void loadSshAccess();
    }, 1000);
    return () => window.clearInterval(interval);
  }, [sshAdminDialog, sshAccessState?.phase, sshAccessState?.rollback_deadline, loadSshAccess]);

  const sshRollbackSeconds = sshAccessState?.rollback_deadline
    ? Math.max(0, Math.ceil((new Date(sshAccessState.rollback_deadline).getTime() - sshCountdownClock) / 1000))
    : 0;
  const sshRollbackCountdown = `${String(Math.floor(sshRollbackSeconds / 60)).padStart(2, "0")}:${String(sshRollbackSeconds % 60).padStart(2, "0")}`;

  function closePasswordDialog() {
    setPasswordDialog(false);
    setCurrentAdminPassword("");
    setNewAdminPassword("");
    setConfirmAdminPassword("");
  }

  async function changeAdminPassword(event: FormEvent) {
    event.preventDefault();
    if (!currentAdminPassword) { notifyError("Введите текущий пароль"); return; }
    if (newAdminPassword.length < 16 || newAdminPassword.length > 128) { notifyError("Новый пароль должен содержать от 16 до 128 символов"); return; }
    if (!/^[!-~]+$/.test(newAdminPassword)) { notifyError("Используйте печатные латинские символы без пробелов"); return; }
    const passwordCategories = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((pattern) => pattern.test(newAdminPassword)).length;
    if (passwordCategories < 3) { notifyError("Добавьте минимум три группы: строчные, заглавные, цифры и спецсимволы"); return; }
    if (newAdminPassword === currentAdminPassword) { notifyError("Новый пароль должен отличаться от текущего"); return; }
    if (newAdminPassword !== confirmAdminPassword) { notifyError("Новые пароли не совпадают"); return; }
    setBusy(true);
    try {
      await request("/security/admin-password", { method: "PUT", body: JSON.stringify({ current_password: currentAdminPassword, new_password: newAdminPassword, confirm_password: confirmAdminPassword }) });
      invalidateApiSession(token, false);
      clearSession();
      notifySuccess("Пароль изменён. Войдите заново с новым паролем.");
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось изменить пароль"); }
    finally { setBusy(false); }
  }

  function updateAutomation(kind: keyof ServicesStatus["automation"], patch: Partial<AutomationSchedule>) {
    automationDirty.current = true;
    setAutomationDraft((current) => current ? {
      ...current, [kind]: { ...current[kind], ...patch },
    } : current);
  }

  async function saveAutomation() {
    if (!automationDraft) return;
    setBusy(true);
    try {
      const draft = structuredClone(automationDraft);
      const started = await submitSystemOperation(request, "/services/automation", { method: "PUT", body: JSON.stringify(draft) });
      pendingAutomation.current = { id: started.id, draft };
      setApplication((current) => ({ ...current, api: current?.api || { active: null, enabled: null }, containers: current?.containers || [], action: started }));
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось сохранить расписания"); }
    finally { setBusy(false); }
  }

  async function changePanelAccess(mode: "external" | "vpn") {
    const protectedUrl = services?.panel_access?.internal_url || "http://admin.312.net";
    const externalUrl = services?.panel_access?.external_url
      || (overview?.server.public_domain ? `https://${overview.server.public_domain}` : `http://${overview?.server.public_ip || ""}`);
    const channels = services?.panel_access?.available_channels || [];
    if (mode === "vpn" && !channels.length) {
      notifyError("Сначала создайте действующую подписку Mihomo с работающим каналом");
      return;
    }
    const message = mode === "vpn"
      ? `Панель будет доступна по адресу ${protectedUrl} через: ${channels.join(" / ")}. Убедитесь, что подписка обновлена и подключена в клиенте Mihomo; текущее публичное подключение может завершиться.`
      : "Открыть публичный доступ к панели из интернета?";
    if (!await askConfirmation({
      title: mode === "vpn" ? "Ограничить доступ к панели?" : "Открыть публичный доступ?",
      message, confirmLabel: mode === "vpn" ? "Оставить защищённый доступ" : "Открыть доступ",
      danger: mode === "external",
    })) return;
    setBusy(true);
    try {
      const started = await submitSystemOperation(request, "/services/panel-access", { method: "PUT", body: JSON.stringify({ mode }) }) as SystemAction;
      const operation = { ...started, action: "access-mode" };
      notifications.upsert(systemOperationNotification(operation, actionLabels["access-mode"], true));
      setApplication((current) => current ? { ...current, action: { ...current.action, ...operation } } : current);
      setServices((current) => current ? { ...current, panel_access: { ...(current.panel_access || { vpn_urls: [] }), mode, public: mode !== "vpn" } } : current);
      // The command restarts the API. Reading immediately here can return the
      // old EnvironmentFile value and overwrite the optimistic switch state.
      window.setTimeout(() => { void loadServices(); void loadSecurity(); }, 5000);
      const targetUrl = mode === "vpn" ? protectedUrl : externalUrl;
      window.setTimeout(() => window.location.assign(targetUrl), 1800);
      // The operation card remains the source of truth until the target page loads.
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось изменить доступ к панели"); }
    finally { setBusy(false); }
  }

  async function changeServiceMode(active: boolean) {
    if (!await askConfirmation({
      title: active ? "Включить сервисный режим?" : "Завершить сервисный режим?",
      message: active
        ? "Будет безопасно развёрнута ветка service. Панель станет публичной, SSH будет запущен, фоновые проверки и автоматические задачи будут приостановлены."
        : "Будет восстановлена production-версия PRO, сохранённая перед локальной test-сборкой. После успешной проверки восстановятся доступ, SSH и автоматические задачи.",
      confirmLabel: active ? "Включить режим" : "Завершить обслуживание",
      danger: active,
    })) return;
    const autoRefreshAfterChange = autoRefresh;
    setBusy(true);
    try {
      setAutoRefresh(false);
      const started = await submitSystemOperation(request, "/services/service-mode", { method: "PUT", body: JSON.stringify({ active }) }) as SystemAction;
      const operation = { ...started, action: "service-mode" };
      notifications.upsert(systemOperationNotification(operation, actionLabels["service-mode"], true));
      setApplication((current) => current ? { ...current, action: { ...current.action, ...operation } } : current);
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
          if (Boolean(nextServices?.service_mode?.active) === active
            && (modeActionFinished || nextApplication?.service_mode?.active === active)) {
            confirmed = true;
            break;
          }
        } catch {
          // API and gateway may briefly restart while the selected branch is deployed.
        }
      }
      if (!confirmed) throw new Error("Сервер не подтвердил завершение переключения режима");
      setAutoRefresh(autoRefreshAfterChange);
      requestCommandReload();
    } catch (cause) {
      setAutoRefresh(autoRefreshAfterChange);
      notifyError(cause instanceof Error ? cause.message : "Не удалось изменить сервисный режим");
    } finally { setBusy(false); }
  }

  function finishSystemCommand(action: SystemAction, error?: string) {
    const [kind, component] = (action.action || "").split(":", 2);
    const target = protocolImages.find(image => image.id === component)?.name;
    const title = [actionLabels[kind] || "Операция", target].filter(Boolean).join(" · ");
    notifications.finishOperation({ ...systemOperationNotification(action, title, false),
      state: error !== undefined ? "error" : "success", message: error === undefined ? action.message || "Команда выполнена." : error || "Команда завершилась с ошибкой.", progress: undefined });
    if (error === undefined) requestCommandReload();
  }

  async function waitForProtocolState(image: ProtocolImage, installed: boolean, started: SystemAction) {
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
        const outcome = protocolOperationOutcome(started, status.action, Boolean(current?.installed) === installed);
        if (outcome === "success") return status.action!;
        if (outcome === "failed") {
          throw new Error(`${installed ? "Установка" : "Удаление"} ${image.name} завершилось с ошибкой`);
        }
      } catch (cause) {
        if (cause instanceof Error && cause.message.includes("завершилось с ошибкой")) throw cause;
        // API may restart briefly after installing or removing a module.
      }
    }
    throw new Error(`Сервер не подтвердил ${installed ? "установку" : "удаление"} ${image.name} за 10 минут`);
  }

  async function adoptProtocol(image: ProtocolImage) {
    let plan: AdoptionPlan;
    setBusy(true);
    try {
      plan = await request<AdoptionPlan>(`/protocol-images/${image.id}/adoption`, { cache: "no-store" });
      if (!plan.compatible) { notifyError(plan.blockers.join(". ")); return; }
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Request failed"); return; }
    finally { setBusy(false); }
    if (!await askConfirmation({ title: `Принять ${image.name} под управление?`,
      message: `${plan.effects.join(". ")}. Резервная копия: ${plan.files} файлов/каталогов, ${(plan.backup_bytes / 1024 / 1024).toFixed(1)} МБ.`,
      confirmLabel: "Принять под управление", danger: true,
    })) return;
    setBusy(true);
    try {
      await request<ComponentManagement>(`/protocol-images/${image.id}/adoption`, { method: "POST", body: JSON.stringify({ fingerprint: plan.fingerprint, confirmed: true }) });
      notifySuccess(`${image.name} принят под управление. Конфигурация сохранена.`);
    } catch (cause) {
      // Never retry adoption after a lost response; first read its result.
      try {
        const current = await request<AdoptionPlan>(`/protocol-images/${image.id}/adoption`, { cache: "no-store" });
        if (current.management.state === "managed") notifySuccess(`${image.name} принят под управление. Результат подтверждён сервером.`);
        else notifyError(cause instanceof Error ? cause.message : "Request failed");
      } catch { notifyError("Результат принятия пока неизвестен. Обновите каталог перед следующим действием."); }
    } finally { setBusy(false); await loadOverview(true); await loadServices(true); }
  }

  async function purgeProtocol(image: ProtocolImage) {
    if (!await askConfirmation({
      title: `Очистить данные ${image.name}?`,
      message: "Настройки, ключи, подключения и резервные копии этого компонента будут удалены без возможности восстановления. Следующая установка начнётся с настроек образа.",
      confirmLabel: "Очистить данные", phrase: image.id, danger: true,
    })) return;
    setBusy(true); setInstallingProtocol(`purge-${image.id}`);
    let started: SystemAction | undefined;
    try {
      started = await submitSystemOperation(request, `/protocol-images/${image.id}/purge`, {method: "POST", body: JSON.stringify({confirmation: image.id})});
      setApplication((current) => ({api: current?.api || {active: null, enabled: null}, containers: current?.containers || [], action: started!}));
      finishSystemCommand(await waitForProtocolState(image, false, started));
      await loadOverview(true);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Не удалось очистить данные";
      if (started) finishSystemCommand(started, message); else notifyError(message);
    } finally { setInstallingProtocol(""); setBusy(false); }
  }

  async function installProtocol(image: ProtocolImage) {
    if (!await askConfirmation({
      title: `Установить ${image.name}?`,
      message: `На сервер будет установлена доступная версия компонента ${image.name}.`,
      confirmLabel: "Установить",
    })) return;
    setBusy(true); setInstallingProtocol(image.id);
    let started: SystemAction | undefined;
    try {
      started = await submitSystemOperation(request, `/protocol-images/${image.id}/install`, { method: "POST" });
      setApplication((current) => ({
        api: current?.api || { active: null, enabled: null },
        containers: current?.containers || [],
        action: started!,
      }));
      const completed = await waitForProtocolState(image, true, started!);
      if (image.id === "mihomo") {
        setTab("mihomo");
      }
      finishSystemCommand(completed);
    } catch (cause) {
      setInstallingProtocol("");
      const message = cause instanceof Error ? cause.message : "Не удалось запустить установку компонента";
      if (started) finishSystemCommand(started, message); else notifyError(message);
    } finally { setInstallingProtocol(""); setBusy(false); }
  }

  async function waitForProtocolUpdate(image: ProtocolImage, started: SystemAction) {
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
        const outcome = protocolOperationOutcome(started, status.action, Boolean(current && !current.update_available));
        if (outcome === "success") return status.action!;
        if (outcome === "failed") {
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
        : image.id === "nodera"
          ? "Будут обновлены среда чата и Telegram-служба Nodera. Работающий бот кратковременно перезапустится."
          : image.id === "awg"
          ? `В репозитории Amnezia доступна новая сборка AWG. Пакеты и модуль ядра будут обновлены без изменения существующей конфигурации.`
        : image.id === "vless-reality-xhttp"
          ? `Доступна версия Xray ${image.available_version} (сейчас ${image.installed_version || "—"}). После проверки бинарника служба VLESS будет кратковременно перезапущена, конфигурация и подключения сохранятся.`
          : `Доступна версия ${image.available_version} (сейчас ${image.installed_version || "—"}). Активные подключения не будут разорваны.`,
      confirmLabel: "Обновить",
      danger: image.update_breaking,
    })) return;
    setBusy(true); setInstallingProtocol(`update-${image.id}`);
    let started: SystemAction | undefined;
    try {
      started = await submitSystemOperation(request, `/protocol-images/${image.id}/update`, { method: "POST" });
      setApplication((current) => ({
        api: current?.api || { active: null, enabled: null },
        containers: current?.containers || [],
        action: started!,
      }));
      const completed = await waitForProtocolUpdate(image, started!);
      finishSystemCommand(completed);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Не удалось запустить обновление протокола";
      if (started) finishSystemCommand(started, message); else notifyError(message);
    } finally { setInstallingProtocol(""); setBusy(false); }
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
    setBusy(true);
    try {
      const draft = { ...loggingDraft };
      const started = await submitSystemOperation(request, "/services/logging", {
        method: "PUT", body: JSON.stringify(draft),
      });
      pendingLogging.current = { id: started.id, draft };
      setApplication((current) => ({ ...current, api: current?.api || { active: null, enabled: null }, containers: current?.containers || [], action: started }));
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось сохранить настройки журналов"); }
    finally { setBusy(false); }
  }

  async function saveMetricsSettings() {
    if (!metricsDraft) return;
    if (metricsSettings && (["raw_hours", "minute_days", "hour_days", "disk_limit_mb"] as const).some((key) => metricsDraft[key] < metricsSettings[key]) && !await askConfirmation({
      title: "Сократить историю метрик?", message: "При следующей очистке данные за пределами новых сроков и лимита места будут удалены. Восстановить удалённые измерения нельзя.", confirmLabel: "Сохранить и сократить", danger: true,
    })) return;
    metricsObservationVersion.current += 1;
    setBusy(true);
    try {
      const { enabled, raw_hours, minute_days, hour_days, disk_limit_mb, revision } = metricsDraft;
      const saved = await request<MetricsSettings>("/services/metrics", { method: "PUT", body: JSON.stringify({ enabled, raw_hours, minute_days, hour_days, disk_limit_mb, expected_revision: revision }) });
      metricsObservationVersion.current += 1;
      setMetricsSettings(saved);
      setMetricsDraft(saved);
      metricsDirty.current = false;
      notifySuccess("Настройки хранения метрик сохранены");
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Не удалось сохранить настройки хранения метрик");
      await loadServices(true); // Reconcile only; never repeat a write after a lost response.
    } finally { setBusy(false); }
  }

  async function recoverAutomation() {
    if (!await askConfirmation({ title: "Восстановить расписания?", message: "Файлы расписаний и состояние таймеров будут восстановлены из снимка перед прерванной операцией. Текущие расписания будут заменены.", confirmLabel: "Восстановить расписания", danger: true })) return;
    setBusy(true);
    try {
      const started = await submitSystemOperation(request, "/services/automation/recover", { method: "POST" });
      pendingAutomationRecovery.current = started.id || null;
      setApplication((current) => ({ ...current, api: current?.api || { active: null, enabled: null }, containers: current?.containers || [], action: started }));
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось начать восстановление расписаний"); }
    finally { setBusy(false); }
  }

  async function clearManagedLogs() {
    if (!await askConfirmation({
      title: "Очистить все управляемые журналы?",
      message: "Будут удалены системные журналы, логи контейнеров и история мониторинга WG/AWG. Действие нельзя отменить.",
      confirmLabel: "Очистить журналы", phrase: "ОЧИСТИТЬ ЛОГИ", danger: true,
    })) return;
    setBusy(true);
    try {
      const started = await submitSystemOperation(request, "/services/logging/clear", { method: "POST" });
      setApplication((current) => ({ ...current, api: current?.api || { active: null, enabled: null }, containers: current?.containers || [], action: started }));
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось очистить журналы"); }
    finally { setBusy(false); }
  }

  async function removeProtocol(image: ProtocolImage, alreadyConfirmed = false) {
    if (!alreadyConfirmed && !await askConfirmation({
      title: image.id === "mihomo" ? `Удалить ${image.name}?` : "Удалить протокол?",
      message: image.id === "mihomo"
        ? "Mihomo Core и внутренние службы будут остановлены и удалены. Профили, настройки, ключи и подключения сохранятся для повторной установки. Сохранённые подключения не будут работать до восстановления компонента."
        : `Исполняемая часть ${image.name} будет удалена. Настройки, ключи и подключения сохранятся для повторной установки; до неё подключения не работают.`,
      confirmLabel: image.id === "mihomo" ? "Удалить модуль" : "Удалить", phrase: image.id === "mihomo" ? "УДАЛИТЬ" : undefined, danger: true,
    })) return;
    setBusy(true); setInstallingProtocol(`remove-${image.id}`);
    let started: SystemAction | undefined;
    try {
      started = await submitSystemOperation(request, `/protocol-images/${image.id}`, { method: "DELETE" });
      setApplication((current) => ({
        api: current?.api || { active: null, enabled: null },
        containers: current?.containers || [],
        action: started!,
      }));
      const completed = await waitForProtocolState(image, false, started!);
      setTab("overview");
      finishSystemCommand(completed);
    } catch (cause) {
      setInstallingProtocol("");
      const message = cause instanceof Error ? cause.message : "Не удалось запустить удаление протокола";
      if (started) finishSystemCommand(started, message); else notifyError(message);
    } finally { setInstallingProtocol(""); setBusy(false); }
  }

  function downloadConfig(filename: string, config: string) {
    const blob = new Blob([config], {
      type: "application/octet-stream",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = filename; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function downloadLogs(filename: string, lines: string[]) {
    downloadConfig(filename, `${lines.join("\n")}\n`);
  }

  async function login(event: FormEvent) {
    event.preventDefault();
    if (loginInFlight.current) return;
    loginInFlight.current = true;
    setBusy(true);
    try {
      const candidateToken = basicCredentials(loginUser, loginPassword);
      await createApiClient(candidateToken, {
        retryDelaysMs: [],
        formatHttpError: (_detail, status) => status === 401
          ? "Неверный логин или пароль"
          : `Не удалось проверить учётные данные (ошибка ${status})`,
      })("/overview", { cache: "no-store" });
      notifications.reset();
      setReloadRequested(false);
      setToken(candidateToken);
      setLoginPassword("");
      setLoginPasswordVisible(false);
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Не удалось войти в панель");
    } finally {
      loginInFlight.current = false;
      setBusy(false);
    }
  }

  if (!token) {
    return <LoginView
      loginUser={loginUser}
      loginPassword={loginPassword}
      loginPasswordVisible={loginPasswordVisible}
      busy={busy}
      version={appVersion}
      channel={releaseChannel}
      commit={buildCommit}
      setLoginUser={setLoginUser}
      setLoginPassword={setLoginPassword}
      setLoginPasswordVisible={setLoginPasswordVisible}
      login={login}
    />;
  }

  const memUsed = usedPercent(overview?.resources.memory_total, overview?.resources.memory_available);
  const diskUsed = usedPercent(overview?.resources.disk_total, overview?.resources.disk_available);
  const memoryUsedBytes = usedBytes(overview?.resources.memory_total, overview?.resources.memory_available);
  const diskUsedBytes = usedBytes(overview?.resources.disk_total, overview?.resources.disk_available);
  const firewall = security?.firewall as {
    active?: boolean | null; rules?: string[]; forwarding_enabled?: boolean; stateful_return?: boolean;
    uplink_interface?: string; vpn_policy_healthy?: boolean;
    panel_access?: {
      mode?: "external" | "vpn"; port?: number; listening?: boolean; public_rule?: boolean; internal_url?: string;
      publicly_accessible?: boolean; vpn_only?: boolean; allowed_interfaces?: string[]; allowed_channels?: string[]; consistent?: boolean;
    };
    protocol_policies?: Record<string, { installed?: boolean; route_allowed?: boolean; nat_enabled?: boolean; healthy?: boolean }>;
  } | undefined;
  const ssh = security?.ssh as { active?: boolean | null; password_authentication?: string; permit_root_login?: string; publicly_allowed?: boolean | null; active_connections?: number; max_auth_tries?: string; x11_forwarding?: string; tcp_forwarding?: string } | undefined;
  const updates = (tab === "application" ? applicationMetadata?.updates : security?.updates) as ApplicationMetadata["updates"] | undefined;
  const applicationVersion = (tab === "application" ? applicationMetadata?.application_version : security?.application_version) as ApplicationMetadata["application_version"] | undefined;
  const securitySystem = security?.system as { kernel?: string; ipv4_forwarding?: boolean; syn_cookies?: boolean; rp_filter?: boolean; rp_filter_mode?: number; rp_filter_valid?: boolean; redirects_disabled?: boolean; source_route_disabled?: boolean; dmesg_restricted?: boolean; auditd_active?: boolean | null; sudo_users?: string[]; login_users?: string[]; apparmor?: { active?: boolean; profiles?: number } } | undefined;
  const fail2ban = security?.fail2ban as { active?: boolean | null; jail_active?: boolean | null; currently_banned?: number | null; total_banned?: number | null } | undefined;
  const listeners = (security?.listeners as string[]) || [];
  const listenerSummary = security?.listener_summary as { tcp?: number; udp?: number; local_only?: number } | undefined;
  const legacy = (security?.legacy_services as Record<string, { active?: boolean | null; enabled?: string }>) || {};
  const applicationSecurity = security?.application_security as {
    admin_password_strong?: boolean; cors_restricted?: boolean; secrets_protected?: boolean;
    secrets_mode?: string; api_local_only?: boolean; control_command_protected?: boolean; control_command_mode?: string;
  } | undefined;
  const panelSecurity = firewall?.panel_access;
  const panelAccessHealthy = Boolean(panelSecurity?.consistent && (panelSecurity?.vpn_only || panelSecurity?.publicly_accessible));
  const sshPublicAccessOpen = Boolean(ssh?.publicly_allowed && ssh?.password_authentication === "yes" && ssh?.permit_root_login === "yes");
  const sshExposureUnknown = ssh?.publicly_allowed === null;
  const sshKeyOnlyActive = Boolean(ssh?.active && ssh?.password_authentication === "no" && ssh?.permit_root_login !== "yes");
  const sshKeyOnlyState = Boolean(sshAccessState?.phase !== "awaiting-confirmation" && (sshKeyOnlyActive || sshAccessState?.phase === "hardened"));
  const sshProtected = Boolean(
    ssh?.active === false
    || (ssh?.active && ssh?.publicly_allowed === false)
    || (
      ssh?.active
      && fail2ban?.active
      && fail2ban?.jail_active
      && ssh?.password_authentication === "no"
      && ssh?.permit_root_login !== "yes"
    )
  );
  const securityChecks = [
    Boolean(firewall?.active),
    Boolean(firewall?.vpn_policy_healthy),
    panelAccessHealthy,
    Boolean(fail2ban?.active && fail2ban?.jail_active),
    sshProtected,
    ssh?.active === false || ssh?.x11_forwarding === "no",
    Boolean(security) && updates?.available === 0 && updates.refreshing !== true && !updates.error,
    Boolean(security) && updates?.reboot_required === false,
    Boolean(updates?.automatic),
    applicationVersionState(applicationVersion) === "current",
    Boolean(securitySystem?.syn_cookies),
    Boolean(securitySystem?.apparmor?.active && (securitySystem.apparmor.profiles ?? 0) > 0),
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
    Boolean(security) && updates?.available === 0 && updates.refreshing !== true && !updates.error && updates.reboot_required === false,
    Boolean(updates?.automatic),
    Boolean(securitySystem?.apparmor?.active && (securitySystem.apparmor.profiles ?? 0) > 0),
    Boolean(securitySystem?.auditd_active),
    Boolean(securitySystem?.syn_cookies),
    Boolean(securitySystem?.rp_filter_valid && securitySystem?.dmesg_restricted),
    Boolean(securitySystem?.redirects_disabled && securitySystem?.source_route_disabled),
  ];
  const securityApplicationChecks = [
    applicationVersionState(applicationVersion) === "current",
    true,
    true,
    Boolean(applicationSecurity?.admin_password_strong),
    Boolean(applicationSecurity?.secrets_protected),
    Boolean(applicationSecurity?.api_local_only),
    Boolean(applicationSecurity?.control_command_protected),
    Boolean(applicationSecurity?.cors_restricted),
  ];
  const operationActive = systemActionNeedsPolling(application?.action);
  const operationName = application?.action?.action || "";
  const operationTarget = protocolImages.find(image => image.id === operationName.split(":")[1])?.name;
  const operationLabel = [actionLabels[operationName.split(":")[0]] || operationName, operationTarget].filter(Boolean).join(" · ");
  const nodeHasError = notificationItems.some((item) => item.state === "error") || application?.api.active === false || application?.action?.state === "failed" || application?.action?.result === "failed";
  const nodeDegraded = Boolean(application?.containers.some((container) => container.healthy === false || (container.State || "").toLowerCase() !== "running"));
  const serviceModeActive = Boolean(services?.service_mode?.active || application?.service_mode?.active);
  const nodeState = !application ? "gray" : nodeHasError ? "red" : serviceModeActive ? "blue" : operationActive || nodeDegraded ? "yellow" : "green";
  const nodeStateLabel = nodeState === "gray" ? "ПРОВЕРКА УЗЛА"
    : nodeState === "red" ? "УЗЕЛ С ОШИБКОЙ"
    : nodeState === "blue" ? "СЕРВИСНЫЙ РЕЖИМ"
    : nodeState === "yellow" ? "ТРЕБУЕТ ВНИМАНИЯ"
    : "УЗЕЛ В СЕТИ";
  const applicationStateTitle = nodeState === "gray"
    ? "Проверка состояния"
    : nodeState === "red"
    ? "Есть ошибки"
    : nodeState === "blue" ? "Сервисный режим"
    : operationActive ? operationLabel
    : nodeState === "yellow" ? "Нарушение работы"
    : "В сети";

  const visualArt =
    tab === "overview" ? "overview"
    : tab === "mihomo" ? "mihomo"
    : tab === "dns" ? "network"
    : tab === "security" || tab === "services" ? "security"
    : tab === "application" ? "modules"
    : "modules";

  return <AppWorkspace
    activeTab={tab}
    visualArt={visualArt}
    protocolImages={protocolImages}
    nodeState={nodeState}
    nodeStateLabel={nodeStateLabel}
    server={overview?.server}
    onNavigate={(id) => {
      void navigateTo(id as Tab);
    }}
    commandOperation={cdnCommand.operation}
    onRecheckCommand={cdnCommand.recheck}
    onDismissCommand={cdnCommand.dismiss}
    onCancelOperation={cancelApplicationAction}
    onRecheckOperation={recheckApplicationAction}
    operationAction={application?.action}
    operationLabel={operationLabel}
    operationActive={operationActive}
    applicationStateTitle={applicationStateTitle}
    uptimeLabel={uptime(overview?.server.uptime_s)}
    loadLabel={overview?.resources.load1?.toFixed(2) || "—"}
    cpuLabel={`${overview?.resources.cpu_percent?.toFixed(0) ?? "—"}%  ${overview?.resources.cpu_count ?? "—"}c${overview?.resources.stale ? " · Stale" : ""}`}
    ramLabel={`${memUsed?.toFixed(0) ?? "—"}%${overview?.resources.stale ? " · Stale" : ""}`}
    networkLabel={networkRate.rx === null ? "—" : `↓ ${bytes(networkRate.rx)}/с`}
    autoRefresh={autoRefresh}
    busy={busy}
    lastUpdated={lastUpdated}
    onToggleAutoRefresh={() => setAutoRefresh((value) => !value)}
    viewLoading={viewLoading}
    onRefresh={() => void refreshCurrent(true, true)}
    onLogout={() => { void (async () => { if (await canLeaveSection()) { invalidateApiSession(token, false); clearSession(); } })(); }}
    operationHistory={<OperationHistory token={token} />}
  >
      {tab !== "overview" && tab !== "network" && tab !== "nodera" && <div className="gateSectionIntro"><div><p className="eyebrow">312.NET / {navigationLabels[tab]}</p><h1>{labels[tab]}</h1><p>{overview?.server.city || "Город не определён"}, {overview?.server.country || "Страна не определена"} · управление инфраструктурой</p></div></div>}
      <RefreshNotices errors={refreshErrors} reconnecting={cdnCommand.pending} />
      {tab === "overview" && (
        <OverviewDashboard
          token={token}
          observationVersion={applicationObservationVersion.current}
          overview={overview}
          memUsed={memUsed}
          diskUsed={diskUsed}
          memoryUsedBytes={memoryUsedBytes}
          diskUsedBytes={diskUsedBytes}
          networkRate={networkRate}
          resourceHistory={resourceHistory}
          protocolImages={protocolImages}
          installingProtocol={installingProtocol}
          busy={busy}
          onInstallProtocol={(image) => void installProtocol(image)}
          onUpdateProtocol={(image) => void updateProtocol(image)}
          onAdoptProtocol={(image) => void adoptProtocol(image)}
          onPurgeProtocol={(image) => void purgeProtocol(image)}
          onRemoveProtocol={(image) => void removeProtocol(image)}
          onOpenComponent={(image) => { if (image.id === "nodera") void navigateTo("nodera"); }}
        />
      )}

      {tab === "mihomo" && (
        <MihomoPage
          refreshKey={mihomoRefreshKey}
          onSystemOperation={acceptSystemOperation}
          systemOperation={application?.action}
          onDirtyChange={setSectionDirty}
          onBeforeLeaveChange={registerMihomoLeaveGuard}
          profilesPage={mihomoProfilesPage}
          setProfilesPage={setMihomoProfilesPage}
          token={token}
          readOnly={protocolImages.find((image) => image.id === "mihomo")?.management?.state !== "managed"}
          confirmAction={askConfirmation}
          coreBusy={installingProtocol === "remove-mihomo"}
          onLoadingChange={handleMihomoLoadingChange}
          onRemoveCore={async () => {
            const image = protocolImages.find((item) => item.id === "mihomo" && item.installed);
            if (image) await removeProtocol(image, true);
          }}
        />
      )}

      {tab === "network" && <NetworkView operation={application?.action} admitOperation={admitNetworkOperation} onOperation={acceptSystemOperation} listState={networkListState} onListStateChange={setNetworkListState} onDirtyChange={setSectionDirty} request={request} refreshKey={networkRefreshKey} onLoadingChange={handleNetworkLoadingChange} cloudflare={{ change: cdnCommand.change, pending: cdnCommand.pending, blocked: busy || application?.recovery?.application_required === true }} />}

      {tab === "nodera" && <AssistantView request={request} refreshKey={mcpRefreshKey} busy={busy || actionNeedsPolling} onDirtyChange={setSectionDirty} confirmAction={askConfirmation} admitOperation={admitNetworkOperation} onOperation={acceptSystemOperation} />}

      {tab === "security" && <SecurityView
        securityLoading={securityLoading}
        telemetryUnknown={security?.live_observation_failed === true || security?.full_observation_failed === true || !security}
        fullObservationUnknown={security?.full_observation_failed === true || !security}
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
        onOpenApplication={() => setTab("application")}
        setPasswordDialog={setPasswordDialog}
        openSshAdminDialog={openSshAdminDialog}
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
        applicationDependencies={applicationMetadata?.dependencies}
        updates={updates}
        serviceModeActive={serviceModeActive}
        busy={busy || cdnCommand.pending}
        applicationLogs={applicationLogs}
        runApplicationAction={runApplicationAction}
        changeServiceMode={changeServiceMode}
        changePanelAccess={changePanelAccess}
        loadApplicationLogs={loadApplicationLogs}
        downloadLogs={downloadLogs}
        downloadUpdateReport={downloadUpdateReport}
      />}

      {tab === "services" && <ServicesDashboard
        operationHistorySettings={<><OperationHistorySettings policy={operationHistoryPolicy} confirm={askConfirmation} busy={busy} /><ObservabilityPanel key={token} request={request} busy={busy} services={services?.items} /></>}
        services={services}
        busy={busy}
        serviceModeActive={serviceModeActive}
        loggingDraft={loggingDraft}
        metricsSettings={metricsSettings}
        metricsDraft={metricsDraft}
        onMetricsChange={(patch) => { metricsDirty.current = true; setMetricsDraft((current) => current ? { ...current, ...patch } : current); }}
        onSaveMetrics={() => void saveMetricsSettings()}
        onResetMetrics={() => { metricsDirty.current = false; setMetricsDraft(metricsSettings); }}
        automationDraft={automationDraft}
        onServiceAction={(serviceId, serviceName, action) => void runServiceAction(serviceId, serviceName, action)}
        onServiceModeChange={(active) => void changeServiceMode(active)}
        onPanelAccessChange={(mode) => void changePanelAccess(mode)}
        onLoggingChange={updateLoggingDraft}
        onSaveLogging={() => void saveLoggingSettings()}
        onClearLogs={() => void clearManagedLogs()}
        onAutomationChange={updateAutomation}
        onSaveAutomation={() => void saveAutomation()}
        onRecoverAutomation={() => void recoverAutomation()}
      />}

      {sshAdminDialog && <div className="confirmBackdrop" role="presentation" onMouseDown={() => setSshAdminDialog(false)}>
        <section className="confirmDialog sshAdminDialog" role="dialog" aria-modal="true" aria-labelledby="ssh-admin-dialog-title" onMouseDown={(event) => event.stopPropagation()}>
          <header className="sshAdminHead">
            <div className="sshAdminIcon" aria-hidden="true">SSH</div>
            <div><p className="eyebrow">ADMINISTRATOR ACCESS</p><h2 id="ssh-admin-dialog-title">Безопасный доступ по SSH</h2><span>Переход с пароля на персональный ключ без риска потерять сервер.</span></div>
            <button type="button" className="sshAdminClose" aria-label="Закрыть" onClick={() => setSshAdminDialog(false)}>×</button>
          </header>
          <div className="sshAdminBody">
            <section className="sshAdminStatus">
              <span className="sshAdminStatusMark">!</span>
              <div><strong>{sshKeyOnlyState ? "Доступ по ключу подтверждён" : sshAccessState?.phase === "awaiting-confirmation" ? "Работает страховочный таймер" : sshPublicAccessOpen || sshAccessState?.phase === "open" ? "SSH/22 и root открыты" : sshAccessState?.phase === "key-installed" ? "Публичный ключ установлен" : sshExposureUnknown ? "Публичный доступ SSH не подтверждён" : "Парольный вход остаётся активным"}</strong><p>{sshAccessState?.message || "Панель ничего не закроет автоматически. Сначала создайте ключ и подтвердите вход в новой сессии."}</p></div>
              <em>{sshKeyOnlyState ? "HARDENED" : sshAccessState?.phase === "awaiting-confirmation" ? "ROLLBACK ON" : sshPublicAccessOpen || sshAccessState?.phase === "open" ? "SSH OPEN" : sshExposureUnknown ? "UNKNOWN" : "SAFE MODE"}</em>
            </section>
            <section className="sshAdminKeys">
              <div className="sshAdminSectionTitle"><span>Ключи root на сервере</span><small>{sshAccessState?.keys?.length || 0} записей</small></div>
              {sshAccessState?.keys?.length ? <div className="sshKeyList">{sshAccessState.keys.map((key) => <div className="sshKeyItem" key={key.fingerprint}><div><strong>{key.type}{key.comment ? ` · ${key.comment}` : ""}</strong><code>{key.fingerprint}{key.managed ? " · ключ мастера" : ""}</code></div><button className="sshResetKeyButton" type="button" disabled={busy || sshAccessState.phase === "awaiting-confirmation"} onClick={() => void deleteSshKey(key.fingerprint)}>Удалить</button></div>)}</div> : <p className="sshKeysEmpty">На сервере нет распознанных публичных ключей root.</p>}
            </section>
            <div className="sshAdminSectionTitle"><span>Порядок настройки</span><small>4 шага</small></div>
            <ol className="sshAdminSteps">
              <li><div className="sshAdminStepNumber">01</div><div className="sshAdminStepContent"><strong>Создайте ключ на компьютере администратора</strong><p>Команда создаст современную пару ED25519. Приватная часть остаётся только на вашем ПК.</p><code>ssh-keygen -t ed25519 -a 64</code></div></li>
              <li className={sshAccessState?.fingerprint ? "sshAdminStepComplete" : ""}><div className="sshAdminStepNumber">02</div><div className="sshAdminStepContent"><strong>Добавьте публичную часть</strong><p>Вставьте одну строку из файла <b>.pub</b>. Приватный ключ сюда вводить нельзя.</p>{sshAccessState?.fingerprint ? <><div className="sshFingerprint"><span>Установлен</span><code>{sshAccessState.fingerprint}</code></div>{sshAccessState.phase !== "awaiting-confirmation" && <button className="sshResetKeyButton" type="button" disabled={busy} onClick={() => void deleteSshKey(sshAccessState.fingerprint)}>Удалить ключ и начать заново</button>}</> : <>{sshAccessState?.phase === "hardened" && <small>Для ротации ключа вставьте новый публичный ключ ниже. Защищённый режим сохранится.</small>}<textarea className="sshPublicKeyInput" rows={3} value={sshPublicKey} onChange={(event) => setSshPublicKey(event.target.value)} placeholder="ssh-ed25519 AAAAC3... admin-pc" spellCheck={false} /><button className="sshStepButton" type="button" disabled={busy || sshPublicKey.trim().length < 80} onClick={() => void installSshKey()}>Проверить и установить ключ</button></>}</div></li>
              <li className={sshKeyTested || sshAccessState?.key_login_observed ? "sshAdminStepComplete" : ""}><div className="sshAdminStepNumber">03</div><div className="sshAdminStepContent"><strong>Проверьте вход в отдельном окне</strong><p>Не закрывайте текущую сессию. Откройте новый терминал и подключитесь с созданным ключом.</p><code>ssh root@SERVER</code>{sshAccessState?.fingerprint && sshAccessState.phase !== "awaiting-confirmation" && sshAccessState.phase !== "hardened" && <label className="sshTestCheck"><input type="checkbox" checked={sshKeyTested} onChange={(event) => setSshKeyTested(event.target.checked)} /><span>Я успешно вошёл по ключу в новой SSH-сессии</span></label>}</div></li>
              <li className={`sshAdminStepFinal ${sshKeyOnlyState ? "sshAdminStepComplete" : ""}`}><div className="sshAdminStepNumber">04</div><div className="sshAdminStepContent"><strong>{sshKeyOnlyState ? "Управление защищённым доступом" : "Безопасно отключите парольный вход"}</strong><p>{sshKeyOnlyState ? "Защищённый режим можно отключить в любой момент. Это откроет SSH/22 и вход root по паролю." : "Сначала включится автоматический откат на 5 минут. Если подтверждения не будет, прежние настройки восстановятся сами."}</p>{sshAccessState?.phase === "awaiting-confirmation" ? <div className="sshRollbackPanel"><div className="sshRollbackHead"><div><strong>Подтвердите повторный вход по ключу</strong><span>До автоматического восстановления настроек</span></div><time className={sshRollbackSeconds <= 60 ? "critical" : ""} dateTime={`PT${sshRollbackSeconds}S`}>{sshRollbackCountdown}</time></div><small>Откат запланирован на {new Date(sshAccessState.rollback_deadline || "").toLocaleTimeString("ru-RU")}</small><label className="sshTestCheck"><input type="checkbox" checked={sshKeyTested} onChange={(event) => setSshKeyTested(event.target.checked)} /><span>После отключения пароля я снова вошёл по ключу</span></label><div className="sshRollbackActions"><button type="button" disabled={busy} onClick={() => void changeSshAccess("rollback")}>Откатить сейчас</button><button className="sshStepButton" type="button" disabled={busy || !sshKeyTested} onClick={() => void changeSshAccess("confirm")}>Подтвердить доступ</button></div></div> : sshKeyOnlyState ? <div className="sshAccessControls"><div className="sshFingerprint"><span>Защищено</span><code>PasswordAuthentication no · Root только по ключу</code></div><button className="sshStepButton sshOpenButton" type="button" disabled={busy} onClick={() => void changeSshAccess("disable")}>Отключить защищённый доступ</button></div> : sshPublicAccessOpen || sshAccessState?.phase === "open" ? <div className="sshAccessControls"><div className="sshFingerprint"><span className="sshOpenBadge">Открыто</span><code>SSH/22 · root по паролю и ключу</code></div><button className="sshStepButton sshHardeningButton" type="button" disabled={busy || !sshAccessState?.fingerprint || !(sshKeyTested || sshAccessState?.key_login_observed)} onClick={() => void changeSshAccess("begin")}>Включить защищённый доступ</button></div> : <div className="sshAccessOptions"><button className="sshStepButton sshHardeningButton" type="button" disabled={busy || !sshAccessState?.fingerprint || !(sshKeyTested || sshAccessState?.key_login_observed)} onClick={() => void changeSshAccess("begin")}>Запустить безопасное применение</button><button className="sshStepButton sshOpenButton" type="button" disabled={busy} onClick={() => void changeSshAccess("disable")}>Открыть SSH/22 и root</button></div>}</div></li>
            </ol>
            <div className="sshAdminNotice"><span>i</span><div><strong>Текущий режим тоже допустим</strong><p>Длинный уникальный пароль вместе с активным Fail2ban — предупреждение, а не неисправность приложения.</p></div></div>
          </div>
          <footer className="sshAdminActions"><span>{sshAccessState?.phase === "awaiting-confirmation" ? "Не закрывайте окно до проверки — активен автоматический откат" : "Все опасные изменения требуют отдельной проверки"}</span><button className="confirmPrimary" type="button" onClick={() => setSshAdminDialog(false)}>Закрыть</button></footer>
        </section>
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
      {confirmation && <ConfirmationDialog request={confirmation} onClose={closeConfirmation} />}
      <VersionFooter />
  </AppWorkspace>;
}

function VersionFooter() {
  return <LegalFooter version={appVersion} channel={releaseChannel} commit={buildCommit} />;
}
