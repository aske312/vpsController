"use client";

import type { Dispatch, SetStateAction } from "react";
import type { ApplicationAction } from "../../types/control-plane";

type LogSource = "ssh" | "firewall" | "system";
type FirewallView = { active?: boolean; rules?: string[]; forwarding_enabled?: boolean; stateful_return?: boolean; vpn_policy_healthy?: boolean };
type SshView = { active?: boolean; password_authentication?: string; permit_root_login?: string; publicly_allowed?: boolean; max_auth_tries?: string; x11_forwarding?: string; tcp_forwarding?: string };
type UpdatesView = { available?: number; security?: number; reboot_required?: boolean; automatic?: boolean };
type AppVersionView = { branch?: string; current_commit?: string; latest_commit?: string; outdated?: boolean | null; error?: string; refreshing?: boolean };
type SystemView = { kernel?: string; ipv4_forwarding?: boolean; syn_cookies?: boolean; rp_filter_mode?: number; rp_filter_valid?: boolean; redirects_disabled?: boolean; source_route_disabled?: boolean; dmesg_restricted?: boolean; auditd_active?: boolean; sudo_users?: string[]; login_users?: string[]; apparmor?: { active?: boolean; profiles?: number } };
type Fail2banView = { active?: boolean; jail_active?: boolean; currently_banned?: number; total_banned?: number };
type ListenerSummary = { tcp?: number; udp?: number; local_only?: number };
type AppSecurity = { admin_password_strong?: boolean; cors_restricted?: boolean; secrets_protected?: boolean; secrets_mode?: string; api_local_only?: boolean; control_command_protected?: boolean; control_command_mode?: string };
type PanelSecurity = { port?: number; publicly_accessible?: boolean; allowed_interfaces?: string[] };

type SecurityViewProps = {
  securityLoading: boolean; securityScore: number; securityChecks: boolean[]; securityPerimeterChecks: boolean[]; securitySystemChecks: boolean[]; securityApplicationChecks: boolean[];
  firewall?: FirewallView; ssh?: SshView; updates?: UpdatesView; applicationVersion?: AppVersionView; securitySystem?: SystemView; fail2ban?: Fail2banView; listeners: string[]; listenerSummary?: ListenerSummary;
  legacy: Record<string, { active?: boolean; enabled?: string }>; applicationSecurity?: AppSecurity; panelSecurity?: PanelSecurity; panelAccessHealthy: boolean; sshProtected: boolean; failedSshRecords24h: string;
  autoRefresh: boolean; securityLogsOpen: boolean; securityLogSource: LogSource; securityLogs: string[]; securityLogsUpdatedAt: Date | null; securityNewLogCount: number;
  fixSecurity: (action: "secure" | "kernel-update" | "vpn-firewall") => Promise<void> | void; runApplicationAction: (action: ApplicationAction) => Promise<void> | void;
  setPasswordDialog: Dispatch<SetStateAction<boolean>>; setSecurityLogsOpen: Dispatch<SetStateAction<boolean>>; setSecurityLogSource: Dispatch<SetStateAction<LogSource>>; setSecurityLogs: Dispatch<SetStateAction<string[]>>;
  loadSecurityLogs: () => Promise<void> | void; downloadLogs: (filename: string, lines: string[]) => void;
};

export function SecurityView(props: SecurityViewProps) {
  const { securityLoading, securityScore, securityChecks, securityPerimeterChecks, securitySystemChecks, securityApplicationChecks, firewall, ssh, updates, applicationVersion, securitySystem, fail2ban, listeners, listenerSummary, legacy, applicationSecurity, panelSecurity, panelAccessHealthy, sshProtected, failedSshRecords24h, autoRefresh, securityLogsOpen, securityLogSource, securityLogs, securityLogsUpdatedAt, securityNewLogCount, fixSecurity, runApplicationAction, setPasswordDialog, setSecurityLogsOpen, setSecurityLogSource, setSecurityLogs, loadSecurityLogs, downloadLogs } = props;
  return <section className="securityWorkspace">
        <article className="securityOverview">
          <div className="securityOverviewLead">
            <div className={`securityPostureRing ${securityScore >= 85 ? "ok" : securityScore >= 60 ? "warn" : "bad"}`}>
              <strong>{securityScore}%</strong>
              <small>POSTURE</small>
            </div>
            <div className="securityOverviewCopy">
              <p className="eyebrow">312.NET / SECURITY CONTROL</p>
              <h1>{securityLoading ? "Проверяем защиту…" : securityScore >= 85 ? "Контур безопасности стабилен" : securityScore >= 60 ? "Требуется внимание" : "Обнаружены критичные риски"}</h1>
              <p>{securityLoading ? "Получаем фактическое состояние узла и политик защиты." : `${securityChecks.filter(Boolean).length} из ${securityChecks.length} проверок соответствуют текущей политике.`}</p>
            </div>
          </div>

          <div className="securityOverviewDomains">
            <section className={securityPerimeterChecks.every(Boolean) ? "ok" : "attention"}>
              <small>PERIMETER</small><strong>{securityPerimeterChecks.filter(Boolean).length}/{securityPerimeterChecks.length}</strong><span>Сеть и административный доступ</span>
            </section>
            <section className={securitySystemChecks.every(Boolean) ? "ok" : "attention"}>
              <small>SYSTEM</small><strong>{securitySystemChecks.filter(Boolean).length}/{securitySystemChecks.length}</strong><span>ОС и hardening</span>
            </section>
            <section className={securityApplicationChecks.every(Boolean) ? "ok" : "attention"}>
              <small>APPLICATION</small><strong>{securityApplicationChecks.filter(Boolean).length}/{securityApplicationChecks.length}</strong><span>Панель и права доступа</span>
            </section>
          </div>

          <div className="securityTelemetry">
            <span><small>SSH REJECTED</small><strong>{failedSshRecords24h}</strong><em>за 24 часа</em></span>
            <span><small>LISTENERS</small><strong>{listeners.length}</strong><em>TCP {listenerSummary?.tcp ?? "—"} · UDP {listenerSummary?.udp ?? "—"}</em></span>
            <span className={Number(updates?.security || 0) > 0 ? "attention" : ""}><small>UPDATES</small><strong>{String(updates?.available ?? "—")}</strong><em>{updates?.security || 0} security</em></span>
            <span className={firewall?.active ? "" : "bad"}><small>UFW RULES</small><strong>{firewall?.rules?.length || 0}</strong><em>{firewall?.active ? "firewall active" : "firewall inactive"}</em></span>
          </div>
        </article>

        <article className="securityMatrix">
          <img className="securityMatrixArt" src="/gate-art/alternatives/security-alt.webp" alt="" aria-hidden="true" />
          <div className="securityMatrixShade" />
          <header className="securityMatrixHead">
            <div>
              <p className="eyebrow">PROTECTION MATRIX</p>
              <h2>Защитные политики узла</h2>
              <span>Фактическое состояние системных и прикладных механизмов защиты.</span>
            </div>
            <div className="securityMatrixTotals">
              <span className="ok"><b>{securityChecks.filter(Boolean).length}</b> healthy</span>
              <span className={securityChecks.length - securityChecks.filter(Boolean).length ? "bad" : "ok"}><b>{securityChecks.length - securityChecks.filter(Boolean).length}</b> attention</span>
            </div>
          </header>

          <div className="securityMatrixContent">
            <section className="securityDomain perimeter">
              <header><div><p className="eyebrow">PERIMETER</p><h3>Сеть и административный доступ</h3></div><strong>{securityPerimeterChecks.filter(Boolean).length}/{securityPerimeterChecks.length}</strong></header>
              <div className="securityRows">
                <SecurityActionRow ok={Boolean(firewall?.active)} title="Firewall" text={`UFW · ${firewall?.rules?.length || 0} правил`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(firewall?.vpn_policy_healthy)} title="VPN firewall policy" text={`Forwarding ${firewall?.forwarding_enabled ? "ON" : "OFF"} · stateful ${firewall?.stateful_return ? "ON" : "OFF"}`} onAction={() => void fixSecurity("vpn-firewall")} />
                <SecurityActionRow ok={panelAccessHealthy} title="Доступ к панели" text={panelSecurity?.publicly_accessible ? `Публичный TCP ${panelSecurity.port || 80} разрешён UFW` : `Из интернета закрыт · ${(panelSecurity?.allowed_interfaces || []).join(" / ") || "WG / AWG"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(fail2ban?.active && fail2ban?.jail_active)} title="Fail2ban · SSH" text={`В бане ${fail2ban?.currently_banned || 0} · всего ${fail2ban?.total_banned || 0}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={sshProtected} title="SSH · административный доступ" text={ssh?.active === false ? "SSH остановлен" : `Internet ${ssh?.publicly_allowed ? "allowed" : "closed"} · Password ${String(ssh?.password_authentication || "unknown")} · Root ${String(ssh?.permit_root_login || "unknown")}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={ssh?.active === false || ssh?.x11_forwarding === "no"} title="SSH-туннели" text={ssh?.active === false ? "SSH остановлен" : `X11 ${ssh?.x11_forwarding || "unknown"} · TCP ${ssh?.tcp_forwarding || "unknown"} · MaxAuthTries ${ssh?.max_auth_tries || "unknown"}`} onAction={() => void fixSecurity("secure")} />
              </div>
            </section>

            <section className="securityDomain system">
              <header><div><p className="eyebrow">SYSTEM HARDENING</p><h3>Ядро и операционная система</h3></div><strong>{securitySystemChecks.filter(Boolean).length}/{securitySystemChecks.length}</strong></header>
              <div className="securityRows">
                <SecurityActionRow ok={securityLoading || (Number(updates?.available || 0) === 0 && !updates?.reboot_required)} title="Обновления Ubuntu" text={`${String(updates?.available ?? "—")} пакетов${updates?.reboot_required ? " · нужен reboot" : ""}`} onAction={() => void fixSecurity("kernel-update")} actionLabel="Обновить" />
                <SecurityActionRow ok={Boolean(updates?.automatic)} title="Автоматические обновления" text={updates?.automatic ? "Unattended upgrades · ON" : "Unattended upgrades · OFF"} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(securitySystem?.apparmor?.active)} title="AppArmor" text={`${securitySystem?.apparmor?.profiles || 0} профилей · ${securitySystem?.apparmor?.active ? "активен" : "выключен"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(securitySystem?.auditd_active)} title="Аудит действий" text={`auditd · ${securitySystem?.auditd_active ? "активен" : "остановлен"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(securitySystem?.syn_cookies)} title="Защита TCP" text={`SYN ${securitySystem?.syn_cookies ? "ON" : "OFF"} · Forwarding ${securitySystem?.ipv4_forwarding ? "ON" : "OFF"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(securitySystem?.rp_filter_valid && securitySystem?.dmesg_restricted)} title="Защита ядра" text={`RP ${securitySystem?.rp_filter_mode === 1 ? "strict" : securitySystem?.rp_filter_mode === 2 ? "loose" : securitySystem?.rp_filter_valid ? "VPN-safe" : "OFF"} · dmesg ${securitySystem?.dmesg_restricted ? "restricted" : "open"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(securitySystem?.redirects_disabled && securitySystem?.source_route_disabled)} title="Kernel routing" text={`Redirects ${securitySystem?.redirects_disabled ? "blocked" : "allowed"} · Source route ${securitySystem?.source_route_disabled ? "blocked" : "allowed"}`} onAction={() => void fixSecurity("secure")} />
              </div>
            </section>

            <section className="securityDomain application">
              <header><div><p className="eyebrow">APPLICATION SECURITY</p><h3>Панель и права доступа</h3></div><strong>{securityApplicationChecks.filter(Boolean).length}/{securityApplicationChecks.length}</strong></header>
              <div className="securityRows">
                <SecurityActionRow ok={applicationVersion?.outdated === false} title="Версия приложения" text={applicationVersion?.refreshing && applicationVersion?.outdated == null ? `Проверяется ${applicationVersion?.branch || "main"}…` : applicationVersion?.error ? applicationVersion.error : applicationVersion?.outdated ? `Устарела: ${applicationVersion.current_commit || "unknown"} · latest ${applicationVersion.latest_commit || "unknown"}` : `Актуальна: ${applicationVersion?.current_commit || "unknown"} · ${applicationVersion?.branch || "main"}`} onAction={() => void runApplicationAction(applicationVersion?.branch === "main" ? "test-update" : "update")} actionLabel="Обновить" />
                <SecurityActionRow ok title="Учётные записи" text={`sudo ${securitySystem?.sudo_users?.length || 0} · login ${securitySystem?.login_users?.length || 0}`} onAction={() => void runApplicationAction("integrity-check")} actionLabel="Проверить" alwaysAction />
                <SecurityActionRow ok title="Дополнительные VPN-службы" text={Object.values(legacy).some((service) => service.active) ? `Активно ${Object.values(legacy).filter((service) => service.active).length} · вне управления панели` : "Не обнаружены"} onAction={() => void runApplicationAction("network-check")} actionLabel="Проверить" alwaysAction />
                <SecurityActionRow ok={Boolean(applicationSecurity?.admin_password_strong)} title="Пароль администратора" text={applicationSecurity?.admin_password_strong ? "Пароль соответствует требованиям" : "Стандартный пароль считается небезопасным"} onAction={() => setPasswordDialog(true)} actionLabel="Изменить пароль" alwaysAction />
                <SecurityActionRow ok={Boolean(applicationSecurity?.secrets_protected)} title="Секреты приложения" text={`/etc/vps-control.env · ${applicationSecurity?.secrets_mode || "не определены"} · root`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(applicationSecurity?.api_local_only)} title="Локальный API" text={applicationSecurity?.api_local_only ? "API слушает только 127.0.0.1:8000" : "API доступен не только локально или не найден"} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(applicationSecurity?.control_command_protected)} title="Команда управления" text={`vps-control · права ${applicationSecurity?.control_command_mode || "не определены"}`} onAction={() => void fixSecurity("secure")} />
                <SecurityActionRow ok={Boolean(applicationSecurity?.cors_restricted)} title="Доверенные источники" text={applicationSecurity?.cors_restricted ? "CORS ограничен адресами панели" : "CORS разрешает произвольные источники"} onAction={() => void fixSecurity("secure")} />
              </div>
            </section>
          </div>
        </article>

        <article className="securityDiagnostics">
          <header className="securityDiagnosticsHead">
            <div><p className="eyebrow">LIVE DIAGNOSTICS</p><h2>Сетевая поверхность и журналы</h2><span>Диагностика вынесена из основной матрицы, чтобы не перегружать security workspace.</span></div>
            <div className="securityDiagnosticsStats">
              <span><small>TCP</small><strong>{listenerSummary?.tcp ?? "—"}</strong></span>
              <span><small>UDP</small><strong>{listenerSummary?.udp ?? "—"}</strong></span>
              <span><small>LOCAL</small><strong>{listenerSummary?.local_only ?? "—"}</strong></span>
            </div>
          </header>
          <div className="securityDiagnosticsGrid">
            <section className="securityListeners">
              <div className="securityDiagTitle"><div><p className="eyebrow">LIVE NETWORK</p><h3>Открытые порты</h3></div><span>{listeners.length} listeners · kernel {securitySystem?.kernel || "—"}</span></div>
              <pre>{listeners.join("\n") || "Нет данных"}</pre>
            </section>
            <section className={`securityLogs ${securityLogsOpen ? "open" : ""}`}>
              <button className="securityLogsToggle" onClick={() => setSecurityLogsOpen((value) => !value)}>
                <span><p className="eyebrow">JOURNALCTL</p><strong>Журналы безопасности</strong><small>SSH · Firewall · System</small></span>
                <em>{securityLogsOpen ? "Скрыть" : "Открыть"}</em>
              </button>
              {securityLogsOpen && <div className="securityLogsBody"><div className="logTools"><div className="logTabs">{(["ssh", "firewall", "system"] as const).map((source) => <button key={source} className={securityLogSource === source ? "active" : ""} onClick={() => { setSecurityLogSource(source); setSecurityLogs([]); }}>{source === "ssh" ? "SSH" : source === "firewall" ? "Firewall" : "Система"}</button>)}</div><div className="logActions"><span>{securityNewLogCount ? `${securityNewLogCount} новых · ` : ""}{autoRefresh ? `auto${securityLogsUpdatedAt ? ` · ${securityLogsUpdatedAt.toLocaleTimeString("ru-RU")}` : ""}` : "paused"}</span><button className="miniButton" onClick={() => void loadSecurityLogs()}>Обновить</button><button className="miniButton" disabled={!securityLogs.length} onClick={() => downloadLogs(`security-${securityLogSource}-${new Date().toISOString().slice(0, 10)}.log`, securityLogs)}>Выгрузить</button></div></div><pre>{securityLogs.join("\n") || "В журнале нет записей"}</pre></div>}
            </section>
          </div>
        </article>
      </section>;
}

function SecurityActionRow({ ok, title, text, onAction, actionLabel = "Исправить", alwaysAction = false }: { ok: boolean; title: string; text: string; onAction: () => void; actionLabel?: string; alwaysAction?: boolean }) {
  return <div><span className={ok ? "check" : "warning"}>{ok ? "✓" : "!"}</span><p><strong>{title}</strong><small>{text}</small></p>{ok && !alwaysAction ? <em className="onlinePill">Готово</em> : <button className="securityFixButton" onClick={onAction}>{actionLabel}</button>}</div>;
}
