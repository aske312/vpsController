"use client";

import { createContext, useContext, useState, type CSSProperties, type Dispatch, type SetStateAction } from "react";
import { applicationVersionState } from "../application/version-state";

const SecurityObservationContext = createContext(false);

type LogSource = "ssh" | "firewall" | "system";
type FirewallView = { active?: boolean | null; rules?: string[]; forwarding_enabled?: boolean; stateful_return?: boolean; vpn_policy_healthy?: boolean };
type SshView = { active?: boolean | null; password_authentication?: string; permit_root_login?: string; publicly_allowed?: boolean | null; max_auth_tries?: string; x11_forwarding?: string; tcp_forwarding?: string };
type UpdatesView = { available?: number | null; security?: number | null; reboot_required?: boolean; automatic?: boolean | null; refreshing?: boolean; error?: string };
type AppVersionView = { branch?: string; current_commit?: string; latest_commit?: string; outdated?: boolean | null; error?: string; refreshing?: boolean };
type SystemView = { kernel?: string; ipv4_forwarding?: boolean; syn_cookies?: boolean; rp_filter_mode?: number; rp_filter_valid?: boolean; redirects_disabled?: boolean; source_route_disabled?: boolean; dmesg_restricted?: boolean; auditd_active?: boolean | null; sudo_users?: string[]; login_users?: string[]; apparmor?: { active?: boolean; profiles?: number } };
type Fail2banView = { active?: boolean | null; jail_active?: boolean | null; currently_banned?: number | null; total_banned?: number | null };
type ListenerSummary = { tcp?: number; udp?: number; local_only?: number };
type AppSecurity = { admin_password_strong?: boolean; cors_restricted?: boolean; secrets_protected?: boolean; secrets_mode?: string; api_local_only?: boolean; control_command_protected?: boolean; control_command_mode?: string };
type PanelSecurity = { port?: number; publicly_accessible?: boolean; allowed_interfaces?: string[]; internal_url?: string; allowed_channels?: string[] };

type SecurityViewProps = {
  securityLoading: boolean; securityScore: number; securityChecks: boolean[]; securityPerimeterChecks: boolean[]; securitySystemChecks: boolean[]; securityApplicationChecks: boolean[];
  telemetryUnknown?: boolean;
  fullObservationUnknown?: boolean;
  firewall?: FirewallView; ssh?: SshView; updates?: UpdatesView; applicationVersion?: AppVersionView; securitySystem?: SystemView; fail2ban?: Fail2banView; listeners: string[]; listenerSummary?: ListenerSummary;
  legacy: Record<string, { active?: boolean | null; enabled?: string }>; applicationSecurity?: AppSecurity; panelSecurity?: PanelSecurity; panelAccessHealthy: boolean; sshProtected: boolean; failedSshRecords24h: string;
  autoRefresh: boolean; securityLogsOpen: boolean; securityLogSource: LogSource; securityLogs: string[]; securityLogsUpdatedAt: Date | null; securityNewLogCount: number;
  fixSecurity: (action: "secure" | "kernel-update" | "vpn-firewall") => Promise<void> | void; onOpenApplication: () => void;
  setPasswordDialog: Dispatch<SetStateAction<boolean>>; openSshAdminDialog: () => void; setSecurityLogsOpen: Dispatch<SetStateAction<boolean>>; setSecurityLogSource: Dispatch<SetStateAction<LogSource>>; setSecurityLogs: Dispatch<SetStateAction<string[]>>;
  loadSecurityLogs: () => Promise<void> | void; downloadLogs: (filename: string, lines: string[]) => void;
};

export function SecurityView(props: SecurityViewProps) {
  const [listenersOpen, setListenersOpen] = useState(false);
  const { securityLoading, securityScore, securityChecks, securityPerimeterChecks, securitySystemChecks, securityApplicationChecks, firewall, ssh, updates, applicationVersion, securitySystem, fail2ban, listeners, listenerSummary, legacy, applicationSecurity, panelSecurity, panelAccessHealthy, sshProtected, failedSshRecords24h, autoRefresh, securityLogsOpen, securityLogSource, securityLogs, securityLogsUpdatedAt, securityNewLogCount, fixSecurity, onOpenApplication, setPasswordDialog, openSshAdminDialog, setSecurityLogsOpen, setSecurityLogSource, setSecurityLogs, loadSecurityLogs, downloadLogs } = props;
  const checkCount = (checks: boolean[]) => props.fullObservationUnknown ? "—" : `${checks.filter(Boolean).length}/${checks.length}`;
  const versionState = applicationVersionState(applicationVersion);
  const updatesKnown = !securityLoading && updates?.available != null && updates.reboot_required != null && updates.refreshing !== true && !updates.error;
  return <section className="securityWorkspace">
        <article className="securityOverview">
          <div className="securityOverviewLead">
            <div className={`securityPostureRing ${props.telemetryUnknown ? "warn" : securityScore >= 85 ? "ok" : securityScore >= 60 ? "warn" : "bad"}`} style={{ "--security-score": props.telemetryUnknown ? 0 : securityScore } as CSSProperties}>
              <strong>{props.telemetryUnknown ? "—" : `${securityScore}%`}</strong>
              <small>POSTURE</small>
            </div>
            <div className="securityOverviewCopy">
              <p className="eyebrow">SECURITY CONTROL</p>
              <h1>{props.telemetryUnknown ? "Состояние служб защиты не подтверждено" : securityLoading ? "Проверяем защиту…" : securityScore >= 85 ? "Контур безопасности стабилен" : securityScore >= 60 ? "Требуется внимание" : "Обнаружены критичные риски"}</h1>
              <p role={props.telemetryUnknown ? "status" : undefined}>{props.fullObservationUnknown ? "UNKNOWN — полная проверка защиты не подтверждена. Сведения сохранены по последней проверке; обновите данные." : props.telemetryUnknown ? "UNKNOWN — активность Firewall, Fail2ban и SSH неизвестна. Остальные сведения показаны по последней проверке." : securityLoading ? "Получаем фактическое состояние узла и политик защиты." : `${securityChecks.filter(Boolean).length} из ${securityChecks.length} проверок соответствуют текущей политике.`}</p>
            </div>
          </div>

          <div className="securityOverviewDomains">
            <section className={securityPerimeterChecks.every(Boolean) ? "ok" : "attention"}>
              <small>PERIMETER</small><strong>{checkCount(securityPerimeterChecks)}</strong><span>Сеть и административный доступ</span>
            </section>
            <section className={securitySystemChecks.every(Boolean) ? "ok" : "attention"}>
              <small>SYSTEM</small><strong>{checkCount(securitySystemChecks)}</strong><span>ОС и hardening</span>
            </section>
            <section className={securityApplicationChecks.every(Boolean) ? "ok" : "attention"}>
              <small>APPLICATION</small><strong>{checkCount(securityApplicationChecks)}</strong><span>Панель и права доступа</span>
            </section>
          </div>

          <div className="securityTelemetry">
            <span><small>SSH REJECTED</small><strong>{failedSshRecords24h}</strong><em>за 24 часа</em></span>
            <span><small>LISTENERS</small><strong>{listeners.length}</strong><em>TCP {listenerSummary?.tcp ?? "—"}  UDP {listenerSummary?.udp ?? "—"}</em></span>
            <span className={Number(updates?.security || 0) > 0 ? "attention" : ""}><small>UPDATES</small><strong>{String(updates?.available ?? "—")}</strong><em>{updates?.refreshing ? "проверяется" : `${updates?.security ?? "—"} security`}</em></span>
            <span className={firewall?.active ? "" : "bad"}><small>UFW RULES</small><strong>{firewall?.rules?.length || 0}</strong><em>{props.telemetryUnknown ? "firewall UNKNOWN" : firewall?.active ? "firewall active" : "firewall inactive"}</em></span>
          </div>
        </article>

        <SecurityObservationContext.Provider value={props.fullObservationUnknown === true}><article className="securityMatrix">
          <div className="securityMatrixShade" />
          <header className="securityMatrixHead">
            <div>
              <p className="eyebrow">PROTECTION MATRIX</p>
              <h2>Защитные политики узла</h2>
              <span>Фактическое состояние системных и прикладных механизмов защиты.</span>
            </div>
            <div className="securityMatrixTotals">
              <span className="ok"><b>{props.fullObservationUnknown ? "—" : securityChecks.filter(Boolean).length}</b> healthy</span>
              <span className={securityChecks.length - securityChecks.filter(Boolean).length ? "bad" : "ok"}><b>{props.fullObservationUnknown ? "—" : securityChecks.length - securityChecks.filter(Boolean).length}</b> attention</span>
            </div>
          </header>

          <div className="securityMatrixContent">
            <section className="securityDomain perimeter">
              <header><div><p className="eyebrow">PERIMETER</p><h3>Сеть и административный доступ</h3></div><strong>{checkCount(securityPerimeterChecks)}</strong></header>
              <div className="securityRows">
                <SecurityActionRow ok={Boolean(firewall?.active)} title="Firewall" text={props.telemetryUnknown ? "UNKNOWN — активность не подтверждена" : `UFW  ${firewall?.rules?.length || 0} правил`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(firewall?.vpn_policy_healthy)} title="VPN firewall policy" text={`Forwarding ${firewall?.forwarding_enabled ? "ON" : "OFF"}  stateful ${firewall?.stateful_return ? "ON" : "OFF"}`} onAction={() => void fixSecurity("vpn-firewall")} />
                <SecurityActionRow ok={panelAccessHealthy} title="Доступ к панели" text={panelSecurity?.publicly_accessible ? `Публичный TCP ${panelSecurity.port || 80} разрешён UFW` : `Из интернета закрыт · ${panelSecurity?.internal_url || "внутренний адрес"} через ${(panelSecurity?.allowed_channels || []).join(" / ") || "защищённое подключение"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(fail2ban?.active && fail2ban?.jail_active)} title="Fail2ban  SSH" text={props.telemetryUnknown ? "UNKNOWN — активность не подтверждена" : `В бане ${fail2ban?.currently_banned ?? "—"}  всего ${fail2ban?.total_banned ?? "—"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={sshProtected} title="SSH  административный доступ" text={props.telemetryUnknown ? "UNKNOWN — активность не подтверждена" : ssh?.active === false ? "SSH остановлен" : `Internet ${ssh?.publicly_allowed === true ? "allowed" : ssh?.publicly_allowed === false ? "closed" : "UNKNOWN"}  Password ${String(ssh?.password_authentication || "unknown")}  Root ${String(ssh?.permit_root_login || "unknown")}`} onAction={openSshAdminDialog} actionLabel="Настроить" alwaysAction />
                <SecurityActionRow ok={ssh?.active === false || ssh?.x11_forwarding === "no"} title="SSH-туннели" text={ssh?.active === false ? "SSH остановлен" : `X11 ${ssh?.x11_forwarding || "unknown"}  TCP ${ssh?.tcp_forwarding || "unknown"}  MaxAuthTries ${ssh?.max_auth_tries || "unknown"}`} onAction={() => void fixSecurity("secure")} />
              </div>
            </section>

            <section className="securityDomain system">
              <header><div><p className="eyebrow">SYSTEM HARDENING</p><h3>Ядро и операционная система</h3></div><strong>{checkCount(securitySystemChecks)}</strong></header>
              <div className="securityRows">
                <SecurityActionRow ok={updatesKnown && updates?.available === 0 && updates.reboot_required === false} title="Обновления системы" text={updatesKnown ? `${updates?.available} пакетов${updates?.reboot_required ? "  нужен reboot" : ""}` : "UNKNOWN — сведения об обновлениях не подтверждены"} onAction={() => void fixSecurity("kernel-update")} actionLabel="Обновить" />
                <SecurityActionRow ok={Boolean(updates?.automatic)} title="Автоматические обновления" text={`Unattended upgrades  ${updates?.automatic === true ? "ON" : updates?.automatic === false ? "OFF" : "UNKNOWN"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(securitySystem?.apparmor?.active && (securitySystem.apparmor.profiles ?? 0) > 0)} title="AppArmor" text={`${securitySystem?.apparmor?.profiles ?? "—"} профилей  ${securitySystem?.apparmor?.active ? "активен" : "выключен"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(securitySystem?.auditd_active)} title="Аудит действий" text={`auditd  ${securitySystem?.auditd_active === true ? "активен" : securitySystem?.auditd_active === false ? "остановлен" : "UNKNOWN"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(securitySystem?.syn_cookies)} title="Защита TCP" text={`SYN ${securitySystem?.syn_cookies ? "ON" : "OFF"}  Forwarding ${securitySystem?.ipv4_forwarding ? "ON" : "OFF"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(securitySystem?.rp_filter_valid && securitySystem?.dmesg_restricted)} title="Защита ядра" text={`RP ${securitySystem?.rp_filter_mode === 1 ? "strict" : securitySystem?.rp_filter_mode === 2 ? "loose" : securitySystem?.rp_filter_valid ? "VPN-safe" : "OFF"}  dmesg ${securitySystem?.dmesg_restricted ? "restricted" : "open"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(securitySystem?.redirects_disabled && securitySystem?.source_route_disabled)} title="Kernel routing" text={`Redirects ${securitySystem?.redirects_disabled ? "blocked" : "allowed"}  Source route ${securitySystem?.source_route_disabled ? "blocked" : "allowed"}`} onAction={() => void fixSecurity("secure")} />
              </div>
            </section>

            <section className="securityDomain application">
              <header><div><p className="eyebrow">APPLICATION SECURITY</p><h3>Панель и права доступа</h3></div><strong>{checkCount(securityApplicationChecks)}</strong></header>
              <div className="securityRows">
                <SecurityActionRow ok={versionState === "current"} title="Версия приложения" text={versionState === "checking" ? `Проверяется ${applicationVersion?.branch || "main"}…` : versionState === "unknown" ? applicationVersion?.error || "UNKNOWN — версия приложения не подтверждена" : versionState === "outdated" ? `Устарела: ${applicationVersion?.current_commit || "unknown"}  latest ${applicationVersion?.latest_commit || "unknown"}` : `Актуальна: ${applicationVersion?.current_commit || "unknown"}  ${applicationVersion?.branch || "main"}`} onAction={onOpenApplication} actionLabel="К обновлению" />
                <SecurityActionRow ok title="Учётные записи" text={`sudo ${securitySystem?.sudo_users?.length || 0}  login ${securitySystem?.login_users?.length || 0}`} onAction={onOpenApplication} actionLabel="К проверкам" alwaysAction />
                <SecurityActionRow ok title="Дополнительные VPN-службы" text={Object.values(legacy).some((service) => service.active === null || service.enabled === "unknown") ? "UNKNOWN — состояние служб не подтверждено" : Object.values(legacy).some((service) => service.active) ? `Активно ${Object.values(legacy).filter((service) => service.active).length}  вне управления панели` : "Не обнаружены"} onAction={onOpenApplication} actionLabel="К проверкам" alwaysAction />
                <SecurityActionRow ok={Boolean(applicationSecurity?.admin_password_strong)} title="Пароль администратора" text={applicationSecurity?.admin_password_strong ? "Пароль соответствует требованиям" : "Стандартный пароль считается небезопасным"} onAction={() => setPasswordDialog(true)} actionLabel="Изменить пароль" alwaysAction />
                <SecurityActionRow ok={Boolean(applicationSecurity?.secrets_protected)} title="Секреты приложения" text={`/etc/vps-control/environment  ${applicationSecurity?.secrets_mode || "не определены"}  root`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(applicationSecurity?.api_local_only)} title="Локальный API" text={applicationSecurity?.api_local_only ? "API слушает только 127.0.0.1:8000" : "API доступен не только локально или не найден"} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(applicationSecurity?.control_command_protected)} title="Команда управления" text={`vps-control  права ${applicationSecurity?.control_command_mode || "не определены"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(applicationSecurity?.cors_restricted)} title="Доверенные источники" text={applicationSecurity?.cors_restricted ? "CORS ограничен адресами панели" : "CORS разрешает произвольные источники"} onAction={() => void fixSecurity("secure")} />
              </div>
            </section>
          </div>
        </article></SecurityObservationContext.Provider>

        <article className="securityDiagnostics">
          <header className="securityDiagnosticsHead">
            <div><p className="eyebrow">LIVE DIAGNOSTICS</p><h2>Журналы и открытые порты</h2><span>Сообщения служб и слушающие сокеты текущего VPS.</span></div>
            <div className="securityDiagnosticsStats">
              <span><small>TCP</small><strong>{listenerSummary?.tcp ?? "—"}</strong></span>
              <span><small>UDP</small><strong>{listenerSummary?.udp ?? "—"}</strong></span>
              <span><small>LOCAL</small><strong>{listenerSummary?.local_only ?? "—"}</strong></span>
            </div>
          </header>
          <div className="securityDiagnosticsGrid">
            <section className={`securityLogs ${securityLogsOpen ? "open" : ""}`}>
              <button className="securityLogsToggle" aria-expanded={securityLogsOpen} onClick={() => setSecurityLogsOpen((value) => !value)}>
                <span><p className="eyebrow">JOURNALCTL</p><strong>Журналы безопасности</strong><small>SSH · Firewall · Система</small></span>
                <em>{securityLogsOpen ? "Скрыть" : "Открыть"}</em>
              </button>
              {securityLogsOpen && <div className="securityLogsBody"><div className="logTools"><div className="logTabs">{(["ssh", "firewall", "system"] as const).map((source) => <button key={source} className={securityLogSource === source ? "active" : ""} onClick={() => { setSecurityLogSource(source); setSecurityLogs([]); }}>{source === "ssh" ? "SSH" : source === "firewall" ? "Firewall" : "Система"}</button>)}</div><div className="logActions"><span>{securityNewLogCount ? `${securityNewLogCount} новых  ` : ""}{autoRefresh ? `auto${securityLogsUpdatedAt ? `  ${securityLogsUpdatedAt.toLocaleTimeString("ru-RU")}` : ""}` : "paused"}</span><button className="miniButton" onClick={() => void loadSecurityLogs()}>Обновить</button><button className="miniButton" disabled={!securityLogs.length} onClick={() => downloadLogs(`security-${securityLogSource}-${new Date().toISOString().slice(0, 10)}.log`, securityLogs)}>Выгрузить</button></div></div><pre>{securityLogs.join("\n") || "В журнале нет записей"}</pre></div>}
            </section>
            <details className="securityListeners" open={listenersOpen} onToggle={event => setListenersOpen(event.currentTarget.open)}>
              <summary className="securityLogsToggle"><span><p className="eyebrow">LIVE NETWORK</p><strong>Открытые порты · {listeners.length}</strong><small>TCP и UDP · внешние и локальные сокеты</small></span><em>{listenersOpen ? "Скрыть" : "Открыть"}</em></summary>
              <pre tabIndex={0} aria-label="Слушающие сокеты">{listeners.join("\n") || "Нет данных"}</pre>
            </details>
          </div>
        </article>
      </section>;
}

function SecurityActionRow({ ok, title, text, onAction, actionLabel = "Исправить", alwaysAction = false }: { ok: boolean; title: string; text: string; onAction: () => void; actionLabel?: string; alwaysAction?: boolean }) {
  const unknown = useContext(SecurityObservationContext);
  return <div><span className={unknown ? "warning" : ok ? "check" : "warning"}>{unknown ? "?" : ok ? "✓" : "!"}</span><p><strong>{title}</strong><small>{unknown ? `UNKNOWN — последние сведения: ${text}` : text}</small></p>{!unknown && ok && !alwaysAction ? <em className="onlinePill">Готово</em> : <button className="securityFixButton" onClick={onAction} disabled={unknown}>{actionLabel}</button>}</div>;
}
