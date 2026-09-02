"use client";

import type { ApplicationAction, ApplicationStatus, ServicesStatus } from "../../types/control-plane";
import { actionLabels } from "../../lib/control-plane-ui";

type ApplicationVersion = { branch?: string; current_commit?: string; latest_commit?: string; outdated?: boolean | null; checked_at?: string; error?: string; refreshing?: boolean };
type UpdateStatus = { available?: number; security?: number; kernel_available?: boolean; reboot_required?: boolean; automatic?: boolean; source?: string; checked_at?: string; refreshing?: boolean };

type ApplicationViewProps = {
  application: ApplicationStatus | null; services: ServicesStatus | null; applicationVersion?: ApplicationVersion; updates?: UpdateStatus;
  serviceModeActive: boolean; busy: boolean; applicationLogs: string[];
  runApplicationAction: (action: ApplicationAction) => Promise<void> | void;
  changeServiceMode: (enabled: boolean) => Promise<void> | void;
  changePanelAccess: (mode: "vpn" | "external") => Promise<void> | void;
  loadApplicationLogs: () => Promise<void> | void; downloadLogs: (filename: string, lines: string[]) => void;
};

export function ApplicationView({ application, services, applicationVersion, updates, serviceModeActive, busy, applicationLogs, runApplicationAction, changeServiceMode, changePanelAccess, loadApplicationLogs, downloadLogs }: ApplicationViewProps) {
  return <section className="applicationWorkspace">
        <article className="applicationSummary">
          <div className="applicationSummaryCopy">
            <p className="eyebrow">APPLICATION CONTROL</p>
            <h1>Приложение</h1>
            <p>Фактическое состояние API и web-службы, установленная ветка, доступ панели и безопасные операции обновления.</p>
          </div>
          <div className="applicationSummaryStats">
            <span className={application?.api.active ? "ok" : "bad"}><small>API</small><strong>{application?.api.active ? "ONLINE" : "STOPPED"}</strong><em>autostart {application?.api.enabled ? "ON" : "OFF"}</em></span>
            <span><small>RUNTIME</small><strong>{(application?.containers || []).filter((container) => container.healthy).length}/{application?.containers.length || 0}</strong><em>healthy components</em></span>
            <span><small>BRANCH</small><strong>{applicationVersion?.branch || "—"}</strong><em>{applicationVersion?.current_commit?.slice(0, 12) || "commit unknown"}</em></span>
            <span className={serviceModeActive ? "warn" : ""}><small>MODE</small><strong>{serviceModeActive ? "SERVICE" : "NORMAL"}</strong><em>{services?.panel_access?.public ? "public access" : "protected access"}</em></span>
          </div>
        </article>

        <article className="applicationControlPlane">
          <div className="applicationControlShade" />
          <div className="applicationControlContent">
            <header className="applicationControlHead">
              <div>
                <p className="eyebrow">APPLICATION CONTROL PLANE</p>
                <h2>Runtime и управление</h2>
                <span>Основные операции выполняются через системный task runner и отображаются в общем progress dock.</span>
              </div>
              <div className="applicationControlBadges">
                <span className={application?.api.active ? "ok" : "bad"}>API {application?.api.active ? "READY" : "DOWN"}</span>
                <span>{(application?.containers || []).filter((container) => container.healthy).length}/{application?.containers.length || 0} runtime</span>
              </div>
            </header>

            <div className="applicationRuntimeList">
              <div className={application?.api.active ? "healthy" : "failed"}>
                <i />
                <span><strong>API панели</strong><small>{application?.api.active ? `Принимает команды  autostart ${application.api.enabled ? "ON" : "OFF"}` : "API не принимает команды"}</small></span>
                <b>{application?.api.active ? "RUNNING" : "STOPPED"}</b>
              </div>
              {(application?.containers || []).map((container, index) => <div className={container.healthy ? "healthy" : "failed"} key={`${container.Name || container.Service}-${index}`}>
                <i />
                <span><strong>{container.component_name || container.Service || `Компонент ${index + 1}`}</strong><small>{container.purpose || container.status_text || container.Status || container.State || "Компонент приложения"}</small></span>
                <b>{container.healthy ? "RUNNING" : "STOPPED"}</b>
              </div>)}
              {application?.action?.action && <div className={application.action.state !== "failed" && application.action.result !== "failed" ? "healthy" : "failed"}>
                <i />
                <span><strong>Последняя команда  {actionLabels[application.action.action.split(":")[0]] || application.action.action}</strong><small>{application.action.state === "running" ? "Команда выполняется системной службой" : application.action.result === "success" ? "Завершена без ошибок" : application.action.message || "Результат уточняется"}</small></span>
                <b>{application.action.state === "running" ? "RUNNING" : application.action.result === "failed" ? "FAILED" : "DONE"}</b>
              </div>}
            </div>

            <div className="applicationPrimaryActions">
              <button onClick={() => void runApplicationAction("restart")} disabled={busy}><span>RESTART</span><strong>Перезапустить</strong><small>Панель и API без reboot VPS</small></button>
              <button onClick={() => void runApplicationAction("update")} disabled={busy}><span>UPDATE</span><strong>Обновить</strong><small>Установить проверенный релиз</small></button>
              <button onClick={() => void runApplicationAction("network-check")} disabled={busy}><span>NETWORK</span><strong>Проверить сеть</strong><small>Internet и все установленные протоколы</small></button>
              <button onClick={() => void runApplicationAction("integrity-check")} disabled={busy}><span>VERIFY</span><strong>Целостность</strong><small>Файлы, права и конфигурация</small></button>
            </div>

            <div className="applicationSecondaryActions">
              <button onClick={() => void runApplicationAction("identity")} disabled={busy}>Обновить данные VPS</button>
              <button onClick={() => void runApplicationAction("optimize")} disabled={busy}>Освободить ресурсы</button>
              {serviceModeActive && <button onClick={() => void runApplicationAction("test-update")} disabled={busy}>Тестовая версия</button>}
              {serviceModeActive && application?.service_mode?.rollback_available && <button onClick={() => void runApplicationAction("test-rollback")} disabled={busy}>Rollback</button>}
            </div>
          </div>
        </article>

        <article className="applicationManagement">
          <section className="applicationEnvironment">
            <header><div><p className="eyebrow">DEPLOYMENT & ACCESS</p><h2>Режим и публикация</h2></div><span>{applicationVersion?.branch || "main"}  {applicationVersion?.current_commit?.slice(0, 12) || "unknown"}</span></header>
            <div className="applicationEnvironmentRows">
              <label>
                <span><strong>Сервисный режим</strong><small>{serviceModeActive ? "Разрешены test-update, rollback и операции обслуживания" : "Обычная production-работа"}</small></span>
                <span className="applicationSwitch"><input type="checkbox" checked={serviceModeActive} onChange={(event) => void changeServiceMode(event.target.checked)} disabled={busy} /><i /></span>
              </label>
              <label>
                <span><strong>Защищённый доступ</strong><small>{services?.panel_access?.public ? `Выключен · адрес после включения ${services?.panel_access?.internal_url || "http://admin.312.net:8080"}` : `Доступ через ${services?.panel_access?.internal_url || (services?.panel_access?.vpn_urls || []).join("  ") || "защищённое подключение"}`}</small></span>
                <span className="applicationSwitch"><input type="checkbox" checked={!services?.panel_access?.public} onChange={(event) => void changePanelAccess(event.target.checked ? "vpn" : "external")} disabled={busy || !services || serviceModeActive || (services.panel_access?.public !== false && services.panel_access?.can_enable === false)} /><i /></span>
              </label>
            </div>
            <div className="applicationRelease">
              <span><small>CURRENT</small><strong>{applicationVersion?.current_commit?.slice(0, 12) || "unknown"}</strong></span>
              <span><small>LATEST</small><strong>{applicationVersion?.latest_commit?.slice(0, 12) || "unknown"}</strong></span>
              <span className={applicationVersion?.outdated ? "bad" : "ok"}><small>STATUS</small><strong>{applicationVersion?.refreshing ? "CHECKING" : applicationVersion?.outdated ? "UPDATE" : "CURRENT"}</strong></span>
            </div>
          </section>

          <section className="applicationLifecycle">
            <header><div><p className="eyebrow">SYSTEM LIFECYCLE</p><h2>VPS и системные пакеты</h2></div></header>
            <div className="applicationLifecycleActions">
              <button onClick={() => void runApplicationAction("kernel-update")} disabled={busy}><strong>Обновить сервер</strong><small>{updates?.kernel_available ? "Доступны обновления ядра или пакетов" : "Ядро и системные пакеты"}</small></button>
              <button onClick={() => void runApplicationAction("reboot")} disabled={busy}><strong>Перезагрузить VPS</strong><small>Корректно завершить службы и reboot</small></button>
              <button className="danger" onClick={() => void runApplicationAction("poweroff")} disabled={busy}><strong>Выключить VPS</strong><small>Повторный запуск потребуется у провайдера</small></button>
            </div>
          </section>
        </article>

        <article className="applicationJournal">
          <header>
            <div><p className="eyebrow">SYSTEMD JOURNAL</p><h2>Журнал приложения</h2><span>Последние сообщения API и web-runtime. Область журнала ограничена по высоте и прокручивается отдельно.</span></div>
            <div><button onClick={() => void loadApplicationLogs()}>Обновить</button><button disabled={!applicationLogs.length} onClick={() => downloadLogs(`application-${new Date().toISOString().slice(0, 10)}.log`, applicationLogs)}>Выгрузить</button></div>
          </header>
          <pre>{applicationLogs.join("\n") || "В журнале нет записей"}</pre>
        </article>
      </section>;
}
