"use client";

import { useMemo, type ReactNode } from "react";

import type { AutomationSchedule, LoggingSettings, MetricsSettings, ServiceItem, ServicesStatus } from "../../shared/types/control-plane";
import { runtimeState, servicesSummary } from "./service-state";

type Props = {
  operationHistorySettings?: ReactNode;
  services: ServicesStatus | null;
  busy: boolean;
  serviceModeActive: boolean;
  loggingDraft: LoggingSettings | null;
  metricsSettings: MetricsSettings | null;
  metricsDraft: MetricsSettings | null;
  onMetricsChange: (patch: Partial<MetricsSettings>) => void;
  onSaveMetrics: () => void;
  onResetMetrics: () => void;
  automationDraft: ServicesStatus["automation"] | null;
  onServiceAction: (serviceId: string, serviceName: string, action: "start" | "stop" | "restart") => void;
  onServiceModeChange: (active: boolean) => void;
  onPanelAccessChange: (mode: "external" | "vpn") => void;
  onLoggingChange: (patch: Partial<LoggingSettings>) => void;
  onSaveLogging: () => void;
  onClearLogs: () => void;
  onAutomationChange: (kind: keyof ServicesStatus["automation"], patch: Partial<AutomationSchedule>) => void;
  onSaveAutomation: () => void;
  onRecoverAutomation: () => void;
};

type ServiceGroupId = "control" | "network" | "security" | "system";

const groupMeta: Record<ServiceGroupId, { eyebrow: string; title: string; hint: string }> = {
  control: { eyebrow: "CONTROL PLANE", title: "Панель и reverse proxy", hint: "API, web-интерфейс и HTTP(S)-контур." },
  network: { eyebrow: "SECURE CHANNELS", title: "Защищённые каналы", hint: "VPN, Mihomo и transport/runtime-службы." },
  security: { eyebrow: "SECURITY & OS", title: "Защита и система", hint: "SSH, Fail2ban, мониторинг и обслуживание Ubuntu." },
  system: { eyebrow: "SYSTEM", title: "Прочие службы", hint: "Остальные управляемые systemd units." },
};

const weekdays = [
  ["Mon", "Понедельник"], ["Tue", "Вторник"], ["Wed", "Среда"], ["Thu", "Четверг"],
  ["Fri", "Пятница"], ["Sat", "Суббота"], ["Sun", "Воскресенье"],
];

function serviceGroup(service: ServiceItem): ServiceGroupId {
  const key = `${service.id} ${service.name} ${service.unit} ${service.description}`.toLowerCase();
  if (/vps-control|caddy|reverse proxy|\bweb\b|\bapi\b/.test(key)) return "control";
  if (/mihomo|amnezia|awg|wireguard|\bwg\b|xray|vless|reality|shadow|vpn|tunnel|transport/.test(key)) return "network";
  if (/ssh|fail2ban|unattended|ubuntu|firewall|monitor|security|audit/.test(key)) return "security";
  return "system";
}

function serviceBadge(service: ServiceItem) {
  const state = runtimeState(service);
  const substate = (service.substate || "").toLowerCase();
  if (state === "unknown") return { className: "stopped", label: "Unknown" };
  if (state === "error") return { className: "failed", label: "Error" };
  if (state === "running") {
    if (substate === "exited") return { className: "ready", label: "ACTIVE  EXITED" };
    if (substate === "waiting") return { className: "ready", label: "ACTIVE  WAITING" };
    return { className: "running", label: "RUNNING" };
  }
  return { className: "stopped", label: "STOPPED" };
}

function compactSince(value: string) {
  if (!value) return "—";
  return value.replace(/\s+(MSK|UTC)$/i, "");
}

function formatRetention(days: number | undefined) {
  if (!days) return "Без автоочистки";
  if (days === 1) return "1 день";
  if (days < 5) return `${days} дня`;
  return `${days} дней`;
}

function runtimeSummary(service: ServiceItem) {
  if (runtimeState(service) === "unknown") return service.runtime?.reason || "Состояние службы пока недоступно";
  const autostart = service.unit_file_state === "unknown" ? "Autostart Unknown" : service.enabled ? "Autostart ON" : "Autostart OFF";
  const restartLabel = service.restarts == null ? "— restarts" : service.restarts === 1 ? "1 restart" : `${service.restarts} restarts`;
  return `${autostart}  ${restartLabel}  ${compactSince(service.active_since)}`;
}

export function ServicesDashboard({
  operationHistorySettings,
  services,
  busy,
  loggingDraft,
  metricsSettings,
  metricsDraft,
  onMetricsChange,
  onSaveMetrics,
  onResetMetrics,
  automationDraft,
  onServiceAction,
  onLoggingChange,
  onSaveLogging,
  onClearLogs,
  onAutomationChange,
  onSaveAutomation,
  onRecoverAutomation,
}: Props) {
  const items = useMemo(() => services?.items || [], [services?.items]);
  const summary = servicesSummary(services);
  const schedulesActive = [automationDraft?.reboot?.enabled, automationDraft?.cleanup?.enabled, automationDraft?.update?.enabled].filter(Boolean).length;

  const orderedItems = useMemo(() => {
    const groupOrder: Record<ServiceGroupId, number> = { control: 0, network: 1, security: 2, system: 3 };
    return [...items].sort((left, right) => groupOrder[serviceGroup(left)] - groupOrder[serviceGroup(right)]);
  }, [items]);
  const nodeTone = summary.tone;
  const nodeTitle = summary.title;
  const nodeHint = summary.hint;

  return (
    <section className="servicesWorkspace" aria-label="Службы и обслуживание">
      <article className={`servicesHero ${nodeTone}`}>
        <div className="servicesHeroBackdrop" aria-hidden="true" />
        <div className="servicesHeroCopy">
          <p className="eyebrow">SERVICES CONTROL</p>
          <h1>Службы и обслуживание</h1>
          <p>Systemd, защищённые каналы, журналы и плановые операции узла.</p>
          <div className={`nodeStatusLine ${nodeTone}`}>
            <i aria-hidden="true" />
            <small>NODE STATE</small>
            <strong>{nodeTitle}</strong>
            <span>{nodeHint}</span>
          </div>
          <div className="servicesStats">
            <Metric label="ACTIVE" value={summary.active} tone={nodeTone === "healthy" ? "ok" : "warn"} />
            <Metric label="FAILED" value={summary.failed} tone={services?.failed_units ? "bad" : ""} />
            <Metric label="AUTOSTART" value={summary.enabled} />
            <Metric label="RESTARTS" value={summary.restarts} />
          </div>
        </div>
      </article>

      <article className="servicesManaged">
        <div className="servicesManagedBackdrop" aria-hidden="true" />
        <header className="servicesManagedHead">
          <div>
            <p className="eyebrow">MANAGED SERVICES</p>
            <h2>Контур служб узла</h2>
            <span>Фактическое состояние systemd и доступные действия.</span>
          </div>
        </header>

        <div className="servicesRegistry">
          <div className="servicesRegistryColumns" aria-hidden="true">
            <span>SERVICE</span><span>RUNTIME</span><span>ACTIONS</span>
          </div>
          <div className="serviceRows">
            {orderedItems.map((service) => (
              <ServiceRow key={service.id} service={service} groupId={serviceGroup(service)} busy={busy} onAction={onServiceAction} />
            ))}
          </div>
        </div>
      </article>

      <article className="servicesOperations">
        <header className="servicesOperationsHead">
          <div>
            <p className="eyebrow">OPERATIONS</p>
            <h2>Журналы и обслуживание</h2>
          </div>
          <div className="servicesOperationsFacts">
            <span><small>JOURNAL</small><b>{services?.logging?.disk_usage || "—"}</b></span>
            <span><small>SCHEDULES</small><b>{schedulesActive}/3</b></span>
          </div>
        </header>

        <div className="servicesOperationsGrid">
        {operationHistorySettings}
        <section className="servicesLog">
          <div className="operationsTitle">
            <p className="eyebrow">LOG MANAGEMENT</p>
            <h3>Журналы</h3>
            <small>Хранение и автоматическая очистка systemd journal.</small>
          </div>
          <div className="servicesLogControls">
            <label className="opsField toggle">
              <span>Persistent</span>
              <span className="serviceToggle compact">
                <input type="checkbox" checked={loggingDraft?.persistent ?? true} onChange={(event) => onLoggingChange({ persistent: event.target.checked })} disabled={busy || !services} />
                <span /><em>{loggingDraft?.persistent ? "ON" : "OFF"}</em>
              </span>
            </label>
            <label className="opsField retention">
              <span>Retention</span>
              <select value={loggingDraft?.retention_days ?? 30} onChange={(event) => onLoggingChange({ retention_days: Number(event.target.value) })} disabled={busy || !services}>
                <option value={1}>1 день</option>
                <option value={7}>7 дней</option>
                <option value={14}>14 дней</option>
                <option value={30}>30 дней</option>
                <option value={90}>90 дней</option>
                <option value={0}>Без автоочистки</option>
              </select>
              <small>{formatRetention(loggingDraft?.retention_days)}</small>
            </label>
            <div className="servicesLogActions">
              <button type="button" className="servicePrimary" onClick={onSaveLogging} disabled={busy || !loggingDraft}>Сохранить</button>
              <button type="button" className="serviceDanger" onClick={onClearLogs} disabled={busy}>Очистить</button>
            </div>
          </div>
        </section>

        <section className="servicesLog servicesMetrics">
          <div className="operationsTitle">
            <p className="eyebrow">METRICS HISTORY</p><h3>История метрик VPS</h3>
            <small>Собирается сервером независимо от открытой панели. Содержимое трафика и секреты не записываются.</small>
          </div>
          {metricsSettings?.error && <p role="status">{metricsSettings.error}</p>}
          {metricsSettings?.trimmed_at && <p>Часть старых измерений удалена по дисковому лимиту.</p>}
          {metricsSettings && metricsDraft && metricsSettings.revision !== metricsDraft.revision && <p role="status">Настройки изменились на сервере. Ваши правки сохранены в форме; отмените их, чтобы загрузить актуальные значения.</p>}
          <div className="servicesLogControls">
            <label className="opsField"><span>Сохранять историю</span><input type="checkbox" checked={metricsDraft?.enabled ?? false} disabled={busy || !metricsDraft} onChange={(event) => onMetricsChange({ enabled: event.target.checked })} /></label>
            {([
              ["raw_hours", "Подробные, часы", 1, 168], ["minute_days", "Минутные, дни", 1, 90],
              ["hour_days", "Часовые, дни", 1, 730], ["disk_limit_mb", "Лимит, МБ", 8, 1024],
            ] as const).map(([key, label, min, max]) => <label className="opsField" key={key}><span>{label}</span><input type="number" min={min} max={max} value={metricsDraft?.[key] ?? ""} disabled={busy || !metricsDraft} onChange={(event) => onMetricsChange({ [key]: Number(event.target.value) })} /></label>)}
            <small>{metricsSettings ? `Занято ${(metricsSettings.used_bytes / 1024 / 1024).toFixed(1)} МБ` : "Данные недоступны"}. Снижение сроков или лимита удаляет старые измерения при следующей очистке. Сводки создаются до очистки подробных данных.</small>
            <div className="servicesLogActions">
              <button type="button" className="servicePrimary" onClick={onSaveMetrics} disabled={busy || !metricsDraft || metricsSettings?.revision !== metricsDraft.revision}>Сохранить</button>
              <button type="button" onClick={onResetMetrics} disabled={busy || !metricsSettings}>Отменить правки</button>
            </div>
          </div>
        </section>

        <section className="servicesMaintenance">
          <header className="servicesMaintenanceHead">
            <div className="operationsTitle">
              <p className="eyebrow">MAINTENANCE</p>
              <h3>Плановые задачи</h3>
              <small>Перезагрузка, очистка и консервативные обновления в заданное окно.</small>
            </div>
            <button type="button" className="servicePrimary ghost" onClick={onSaveAutomation} disabled={busy || !services || services.automation_recovery_required}>Сохранить расписание</button>
          </header>
          {services?.automation_recovery_required && <p role="alert">Предыдущее применение расписаний не завершено. Снимок восстановления сохранён. Отображаемые настройки могут отличаться от состояния служб. <button type="button" disabled={busy} onClick={onRecoverAutomation}>Восстановить расписания</button></p>}
          <div className="maintenanceRows">
            <ScheduleRow title="Перезагрузка" value={automationDraft?.reboot} timer={services?.timers.reboot} busy={busy} onChange={(patch) => onAutomationChange("reboot", patch)} />
            <ScheduleRow title="Очистка" value={automationDraft?.cleanup} timer={services?.timers.cleanup} busy={busy} onChange={(patch) => onAutomationChange("cleanup", patch)} />
            <ScheduleRow title="Безопасные автообновления" value={automationDraft?.update} timer={services?.timers.update} busy={busy} onChange={(patch) => onAutomationChange("update", patch)} />
          </div>
        </section>
        </div>
      </article>
    </section>
  );
}

function Metric({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return <div className={`serviceMetric ${tone}`}><small>{label}</small><strong>{value}</strong></div>;
}

function ServiceRow({ service, groupId, busy, onAction }: { service: ServiceItem; groupId: ServiceGroupId; busy: boolean; onAction: Props["onServiceAction"] }) {
  const badge = serviceBadge(service);
  const stateUnknown = runtimeState(service) === "unknown";
  const canPrimary = service.controls.includes(service.active ? "restart" : "start");
  const canStop = service.active && (service.controls.includes("stop") || service.disabled_controls?.includes("stop"));
  const stopDisabled = Boolean(service.disabled_controls?.includes("stop"));

  return (
    <div className={`serviceRow state-${badge.className}`}>
      <div className="serviceIdentity">
        <i className={`serviceDot ${badge.className}`} />
        <div>
          <span className="serviceCategory">{groupMeta[groupId].eyebrow}</span>
          <strong>{service.name}</strong>
          <small title={service.unit}>{service.unit}</small>
        </div>
      </div>
      <div className="serviceRuntime">
        <span className={`serviceState ${badge.className}`} title={service.runtime ? `${service.runtime.reason} · ${service.runtime.checked_at}` : undefined}>{badge.label}</span>
        <small>{runtimeSummary(service)}</small>
      </div>
      <div className="serviceActions">
        {canPrimary && <button type="button" className="serviceActionPrimary" onClick={() => onAction(service.id, service.name, service.active ? "restart" : "start")} disabled={busy || stateUnknown}>{service.active ? "Перезапуск" : "Запуск"}</button>}
        {canStop && <button type="button" className={`serviceActionSecondary ${stopDisabled ? "protected" : "danger"}`} onClick={() => onAction(service.id, service.name, "stop")} disabled={busy || stopDisabled || stateUnknown} title={stopDisabled ? "Остановка этой службы отключит панель или путь восстановления" : undefined}>{stopDisabled ? "Защищено" : "Стоп"}</button>}
      </div>
    </div>
  );
}

function ScheduleRow({ title, value, timer, busy, onChange }: {
  title: string;
  value?: AutomationSchedule;
  timer?: { installed: boolean; active: boolean; last_trigger: string; next_run: string };
  busy: boolean;
  onChange: (patch: Partial<AutomationSchedule>) => void;
}) {
  if (!value) return null;
  const time = `${String(value.hour).padStart(2, "0")}:${String(value.minute).padStart(2, "0")}`;
  return (
    <div className={`maintenanceRow ${value.enabled ? "enabled" : ""}`}>
      <div className="maintenanceIdentity">
        <strong>{title}</strong>
        <small>{title === "Перезагрузка" ? "Плановый reboot VPS." : "APT-кэш, temp и старые журналы."}</small>
      </div>

      <div className={`maintenanceControls ${value.cadence === "weekly" ? "weekly" : ""}`}>
        <label className="opsField toggle">
          <span>Состояние</span>
          <span className="serviceToggle compact">
            <input type="checkbox" checked={value.enabled} onChange={(event) => onChange({ enabled: event.target.checked })} disabled={busy} />
            <span /><em>{value.enabled ? "ON" : "OFF"}</em>
          </span>
        </label>
        <label className="opsField">
          <span>Период</span>
          <select value={value.cadence} onChange={(event) => onChange({ cadence: event.target.value as AutomationSchedule["cadence"] })} disabled={busy}>
            <option value="daily">Ежедневно</option>
            <option value="weekly">Еженедельно</option>
            <option value="monthly">Ежемесячно, 1-го</option>
          </select>
        </label>
        {value.cadence === "weekly" && (
          <label className="opsField">
            <span>День</span>
            <select value={value.weekday} onChange={(event) => onChange({ weekday: event.target.value })} disabled={busy}>
              {weekdays.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
            </select>
          </label>
        )}
        <label className="opsField time">
          <span>Время</span>
          <input type="time" value={time} onChange={(event) => {
            const [hour, minute] = event.target.value.split(":").map(Number);
            if (Number.isInteger(hour) && Number.isInteger(minute)) onChange({ hour, minute });
          }} disabled={busy} />
        </label>
      </div>

      <div className="maintenanceRuntime">
        <span><small>NEXT</small><strong>{timer?.next_run || "—"}</strong></span>
        <span><small>LAST</small><strong>{timer?.last_trigger || "ещё не запускалось"}</strong></span>
      </div>
    </div>
  );
}
