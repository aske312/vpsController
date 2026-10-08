"use client";

import { FormEvent, useState } from "react";
import { ConnectionProfileResult, protocolDelivery, type ConnectionProfile } from "./connection-profile";
import { ProtocolIcon } from "./protocol-icon";
import type { Protocol } from "./page";

export type ConnectionSettings = {
  dns: string;
  mtu: number;
  keepalive: number;
  route_mode: "ipv4" | "all";
  local_socks_port: number;
  local_http_port: number;
  disable_udp: boolean;
  congestion_control: "bbr" | "cubic" | "new_reno";
  heartbeat: "5s" | "10s" | "15s" | "30s";
  fingerprint: "chrome" | "firefox" | "safari";
};

const settingsFor = (protocol: Protocol): ConnectionSettings => ({
  dns: "1.1.1.1, 1.0.0.1",
  mtu: protocol === "wg" ? 1380 : 1280,
  keepalive: 25,
  route_mode: "ipv4",
  local_socks_port: protocol === "tuic" ? 2080 : protocol === "xray" ? 10808 : 1080,
  local_http_port: 10809,
  disable_udp: false,
  congestion_control: "bbr",
  heartbeat: "10s",
  fingerprint: "chrome",
});

type Props = {
  protocols: Protocol[];
  onClose(): void;
  onCreate(payload: { name: string; protocol: Protocol; settings: ConnectionSettings }): Promise<ConnectionProfile>;
  onCreated(): Promise<void> | void;
  onError(message: string): void;
  onDownload(filename: string, content: string, mimeType?: string): void;
};

export function ConnectionDialog({ protocols, onClose, onCreate, onCreated, onError, onDownload }: Props) {
  const initialProtocol = protocols[0] || "awg";
  const [name, setName] = useState("");
  const [protocol, setProtocol] = useState<Protocol>(initialProtocol);
  const [settings, setSettings] = useState<ConnectionSettings>(() => settingsFor(initialProtocol));
  const [profile, setProfile] = useState<ConnectionProfile | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  function selectProtocol(next: Protocol) {
    setProtocol(next);
    setSettings(settingsFor(next));
    setFormError("");
  }

  function update(patch: Partial<ConnectionSettings>) {
    setSettings((current) => ({ ...current, ...patch }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setFormError("");
    try {
      const created = await onCreate({ name, protocol, settings });
      setProfile(created);
      setName("");
      await onCreated();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Не удалось создать подключение";
      setFormError(message);
      onError(message);
    } finally {
      setSubmitting(false);
    }
  }

  function createAnother() {
    setProfile(null);
    setSettings(settingsFor(protocol));
  }

  const meta = protocolDelivery[protocol];
  return <div className="confirmBackdrop connectionDialogBackdrop" role="presentation" onMouseDown={() => { if (!submitting && !profile) onClose(); }}>
    <form className={`connectionDialog${profile ? " generated" : ""}`} role="dialog" aria-modal="true" aria-labelledby="connection-dialog-title" onMouseDown={(event) => event.stopPropagation()} onSubmit={submit}>
      <header className="connectionDialogHead"><div><p className="eyebrow">PERSONAL ACCESS</p><h2 id="connection-dialog-title">{profile ? "Подключение создано" : "Новое подключение"}</h2><span>{profile ? "Передайте профиль владельцу устройства и сохраните его сейчас." : "Настройте отдельный профиль для конкретного пользователя или устройства."}</span></div><button type="button" aria-label="Закрыть" onClick={onClose} disabled={submitting}>×</button></header>
      {!profile ? <>
        <div className="connectionDialogBody">
          <label className="connectionNameField"><span>Пользователь или устройство</span><input autoFocus required minLength={2} maxLength={48} pattern="[\\p{L}\\p{N}_. -]{2,48}" title="От 2 до 48 символов: буквы, цифры, пробел, точка, дефис или _" value={name} onChange={(event) => setName(event.target.value)} placeholder="Например: Анна · iPhone" /><small>Имя используется в панели и в экспортируемом профиле.</small></label>
          <fieldset className="connectionProtocolPicker"><legend>Тип подключения</legend><div>{protocols.map((item) => { const itemMeta = protocolDelivery[item]; return <button type="button" key={item} className={protocol === item ? "active" : ""} onClick={() => selectProtocol(item)}><span className={`protocol ${item}`}><ProtocolIcon protocol={item} /></span><span><strong>{itemMeta.title}</strong><small>{itemMeta.summary}</small></span><i /></button>; })}</div></fieldset>
          <fieldset className="connectionSettings"><legend>Параметры профиля</legend><header><span className={`protocol ${protocol}`}><ProtocolIcon protocol={protocol} /></span><div><strong>{meta.title}</strong><small>{meta.transport} · {meta.methods.join(" · ")}</small></div></header>
            <div className="connectionSettingsFields">
              {(protocol === "wg" || protocol === "awg") && <>
                <label><span>DNS-серверы</span><input value={settings.dns} onChange={(event) => update({ dns: event.target.value })} placeholder="1.1.1.1, 1.0.0.1" /></label>
                <label><span>MTU</span><input type="number" min={protocol === "awg" ? 1280 : 576} max={1500} value={settings.mtu} onChange={(event) => update({ mtu: Number(event.target.value) })} /></label>
                <label><span>Keepalive, сек.</span><input type="number" min={0} max={300} value={settings.keepalive} onChange={(event) => update({ keepalive: Number(event.target.value) })} /></label>
                <label><span>Маршрутизация</span><select value={settings.route_mode} onChange={(event) => update({ route_mode: event.target.value as ConnectionSettings["route_mode"] })}><option value="ipv4">Весь IPv4-трафик</option><option value="all">IPv4 + IPv6</option></select></label>
              </>}
              {protocol === "hysteria2" && <>
                <label><span>Локальный SOCKS5-порт</span><input type="number" min={1024} max={65535} value={settings.local_socks_port} onChange={(event) => update({ local_socks_port: Number(event.target.value) })} /></label>
                <label className="connectionCheckbox"><span><strong>Отключить UDP в SOCKS5</strong><small>Используйте только при ограничениях клиента</small></span><input type="checkbox" checked={settings.disable_udp} onChange={(event) => update({ disable_udp: event.target.checked })} /></label>
              </>}
              {protocol === "tuic" && <>
                <label><span>Локальный mixed-порт</span><input type="number" min={1024} max={65535} value={settings.local_socks_port} onChange={(event) => update({ local_socks_port: Number(event.target.value) })} /></label>
                <label><span>Congestion control</span><select value={settings.congestion_control} onChange={(event) => update({ congestion_control: event.target.value as ConnectionSettings["congestion_control"] })}><option value="bbr">BBR</option><option value="cubic">CUBIC</option><option value="new_reno">New Reno</option></select></label>
                <label><span>Heartbeat</span><select value={settings.heartbeat} onChange={(event) => update({ heartbeat: event.target.value as ConnectionSettings["heartbeat"] })}><option value="5s">5 секунд</option><option value="10s">10 секунд</option><option value="15s">15 секунд</option><option value="30s">30 секунд</option></select></label>
              </>}
              {protocol === "xray" && <>
                <label><span>Локальный SOCKS-порт</span><input type="number" min={1024} max={65535} value={settings.local_socks_port} onChange={(event) => update({ local_socks_port: Number(event.target.value) })} /></label>
                <label><span>Локальный HTTP-порт</span><input type="number" min={1024} max={65535} value={settings.local_http_port} onChange={(event) => update({ local_http_port: Number(event.target.value) })} /></label>
                <label><span>TLS fingerprint</span><select value={settings.fingerprint} onChange={(event) => update({ fingerprint: event.target.value as ConnectionSettings["fingerprint"] })}><option value="chrome">Chrome — рекомендуется</option><option value="firefox">Firefox</option><option value="safari">Safari</option></select></label>
                <label className="connectionCheckbox"><span><strong>Отключить UDP</strong><small>Оставьте выключенным для обычной работы</small></span><input type="checkbox" checked={settings.disable_udp} onChange={(event) => update({ disable_udp: event.target.checked })} /></label>
              </>}
            </div>
            {(protocol === "awg" || protocol === "hysteria2" || protocol === "xray") && <p className="connectionSettingsNote">Криптографические параметры сервера подставляются автоматически и не редактируются: несовпадение сделало бы профиль нерабочим.</p>}
          </fieldset>
          {formError && <div className="connectionDialogError" role="alert">{formError}</div>}
        </div>
        <footer className="connectionDialogActions"><button type="button" onClick={onClose} disabled={submitting}>Отмена</button><button className="primaryButton" disabled={submitting || name.trim().length < 2}>{submitting ? "Создаём…" : "Создать подключение"}</button></footer>
      </> : <>
        <div className="connectionDialogResult"><ConnectionProfileResult profile={profile} onDownload={onDownload} /></div>
        <footer className="connectionDialogActions"><button type="button" onClick={createAnother}>Создать ещё</button><button type="button" className="primaryButton" onClick={onClose}>Готово</button></footer>
      </>}
    </form>
  </div>;
}
