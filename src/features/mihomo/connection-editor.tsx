import { useId, type ReactNode } from "react";
import { ProtocolIcon } from "../../shared/components/protocol-icon";
import type { ProfileConnection, SettingField } from "./types";

const advancedKeys = new Set(["xpadding", "xmux_concurrency", "fingerprint", "ip_version", "tfo", "mptcp"]);
const compactLabels: Record<string, string> = {
  target: "SNI", transport: "Транспорт", transport_path: "Путь", xhttp_mode: "XHTTP",
  tls_transport: "Транспорт", cdn_transport: "Транспорт", tls_xhttp_mode: "XHTTP", cdn_xhttp_mode: "XHTTP",
  fingerprint: "Fingerprint", ip_version: "IP-версия", xmux_concurrency: "Параллелизм", mptcp: "Multipath TCP",
};

export function ConnectionEditor({ connection, title, fields, targets, disabled, onNameChange, onSettingChange, onDelete, address, footer }: {
  connection: ProfileConnection;
  title: string;
  fields: SettingField[];
  targets: string[];
  disabled: boolean;
  onNameChange: (name: string) => void;
  onSettingChange: (key: string, value: string | number | boolean) => void;
  onDelete: () => void;
  address: ReactNode;
  footer: ReactNode;
}) {
  const id = useId();
  const primary = fields.filter(field => !advancedKeys.has(field.key));
  const advanced = fields.filter(field => advancedKeys.has(field.key));
  const renderFields = (items: SettingField[]) => <div className="mihomoConnectionParameters">
      {items.map(field => <label key={field.key} className={`mihomoConnectionParameter${field.type === "boolean" ? " is-toggle" : ""}`} title={[field.label, field.help].filter(Boolean).join(" · ")}>
        <span>{compactLabels[field.key] || field.label}</span>
        {["target", "sni"].includes(field.key) ? <>
          <input aria-label={`${field.label}: свой адрес`} list={`${id}-${field.key}`} type="text" maxLength={field.key === "target" ? 259 : 253} spellCheck={false} autoCapitalize="none" placeholder={field.key === "target" ? "Автоматически" : "Домен без порта"} value={String(connection.settings[field.key] ?? field.default)} disabled={disabled} onChange={event => onSettingChange(field.key, event.target.value)} />
          <datalist id={`${id}-${field.key}`}>{(field.key === "sni" && connection.settings.skip_cert_verify === false ? [String(connection.settings.sni || "")].filter(host => host && !targets.includes(host)) : targets).map(host => <option key={host} value={field.key === "target" ? `${host}:443` : host} />)}</datalist>
        </> : field.type === "select" ? <select aria-label={field.label} value={String(connection.settings[field.key] ?? field.default)} disabled={disabled} onChange={event => onSettingChange(field.key, event.target.value)}>
          {(field.options || []).map(option => { const value = typeof option === "string" ? option : option.value; return <option key={value} value={value}>{typeof option === "string" ? option : option.label}</option>; })}
        </select> : field.type === "boolean" ? <input aria-label={field.label} type="checkbox" checked={Boolean(connection.settings[field.key] ?? field.default)} disabled={disabled} onChange={event => onSettingChange(field.key, event.target.checked)} />
          : <input aria-label={field.label} type={field.type === "number" ? "number" : "text"} min={field.min} max={field.max} value={String(connection.settings[field.key] ?? field.default)} disabled={disabled} onChange={event => onSettingChange(field.key, field.type === "number" ? Number(event.target.value) : event.target.value)} />}
      </label>)}
    </div>;
  return <article className={`mihomoConnectionEditor protocol-${connection.component}`} aria-label={title}>
    <header className="mihomoConnectionHeading">
      <span className="mihomoConnectionBadge" title={title}><ProtocolIcon protocol={connection.component} /></span>
      <label className="mihomoConnectionName"><span>Название</span><input aria-label="Название в профиле" value={connection.name} maxLength={80} disabled={disabled} onChange={event => onNameChange(event.target.value)} /></label>
      <span className="mihomoConnectionKind">{title}</span>
      <button type="button" className="mihomoConnectionQuickDelete" title="Удалить подключение" aria-label={`Удалить ${connection.name}`} disabled={disabled} onClick={onDelete}>×</button>
    </header>
    {address}
    {connection.component === "transport-reality" && <p className="mihomoHint">{String(connection.settings.xray_protocol || "vless").toUpperCase()} · {String(connection.settings.transport || "xhttp").toUpperCase()}{connection.settings.transport === "xhttp" && ` · ${String(connection.settings.xhttp_mode || "auto")}`}. XHTTP доступен для VLESS; SplitHTTP — прежнее название транспорта. Параметры TLS/CDN показаны отдельно для выбранного маршрута. Совместимость зависит от ядра клиента; базовая версия Mihomo — 1.19.31.</p>}
    {connection.component === "transport-hysteria2" && <p className="mihomoHint">Hysteria2 · QUIC/UDP. Salamander: {connection.credential?.obfs === true ? "включён в сохранённом подключении" : connection.credential?.obfs === false ? "выключен в сохранённом подключении" : "определится при сохранении по настройке серверного канала"}. Переключатель доступен в настройках Hysteria2. Клиент получает соответствующие параметры из подписки. Изменение серверной обфускации использует штатную операцию с проверкой и откатом.</p>}
    {primary.length > 0 && renderFields(primary)}
    {advanced.length > 0 && <details className="mihomoConnectionAdvanced"><summary>Дополнительно <span>{advanced.length}</span></summary>{renderFields(advanced)}</details>}
    {footer && <footer className="mihomoConnectionFooter">{footer}</footer>}
  </article>;
}
