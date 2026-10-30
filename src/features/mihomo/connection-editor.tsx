import { useId, type ReactNode } from "react";
import { ProtocolIcon } from "../../shared/components/protocol-icon";
import type { ProfileConnection, SettingField } from "./types";

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
  return <article className={`mihomoConnectionEditor protocol-${connection.component}`}>
    <header className="mihomoConnectionHeading">
      <span className="mihomoConnectionBadge" title={title}><ProtocolIcon protocol={connection.component} /></span>
      <label className="mihomoConnectionName"><span>Название</span><input aria-label="Название в профиле" value={connection.name} maxLength={80} disabled={disabled} onChange={event => onNameChange(event.target.value)} /></label>
      <span className="mihomoConnectionKind">{title}</span>
      <button type="button" className="mihomoConnectionQuickDelete" title="Удалить подключение" aria-label={`Удалить ${connection.name}`} disabled={disabled} onClick={onDelete}>×</button>
    </header>
    {address}
    {fields.length > 0 && <div className="mihomoConnectionParameters">
      {fields.map(field => <label key={field.key} className={`mihomoConnectionParameter${field.type === "boolean" ? " is-toggle" : ""}`} title={field.help}>
        <span>{field.label}</span>
        {["target", "sni"].includes(field.key) ? <>
          <input aria-label={`${field.label}: свой адрес`} list={`${id}-${field.key}`} type="text" maxLength={field.key === "target" ? 259 : 253} spellCheck={false} autoCapitalize="none" placeholder={field.key === "target" ? "Автоматически" : "Домен без порта"} value={String(connection.settings[field.key] ?? field.default)} disabled={disabled} onChange={event => onSettingChange(field.key, event.target.value)} />
          <datalist id={`${id}-${field.key}`}>{targets.map(host => <option key={host} value={field.key === "target" ? `${host}:443` : host} />)}</datalist>
        </> : field.type === "select" ? <select value={String(connection.settings[field.key] ?? field.default)} disabled={disabled} onChange={event => onSettingChange(field.key, event.target.value)}>
          {(field.options || []).map(option => { const value = typeof option === "string" ? option : option.value; return <option key={value} value={value}>{typeof option === "string" ? option : option.label}</option>; })}
        </select> : field.type === "boolean" ? <input type="checkbox" checked={Boolean(connection.settings[field.key] ?? field.default)} disabled={disabled} onChange={event => onSettingChange(field.key, event.target.checked)} />
          : <input type={field.type === "number" ? "number" : "text"} min={field.min} max={field.max} value={String(connection.settings[field.key] ?? field.default)} disabled={disabled} onChange={event => onSettingChange(field.key, field.type === "number" ? Number(event.target.value) : event.target.value)} />}
      </label>)}
    </div>}
    {footer && <footer className="mihomoConnectionFooter">{footer}</footer>}
  </article>;
}
