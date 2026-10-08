"use client";

import { FormEvent, useState } from "react";
import { ConnectionProfileResult, protocolDelivery, type ConnectionProfile } from "./connection-profile";
import { ProtocolIcon } from "./protocol-icon";
import type { Protocol } from "./page";

export type ConnectionSettings = {
  dns: string;
  mtu: number;
  keepalive: number;
  route_mode: "ipv4" | "all" | "custom";
  allowed_ips: string;
  awg_jc: number;
  awg_jmin: number;
  awg_jmax: number;
  proxy_bind: "loopback" | "lan";
  local_auth_enabled: boolean;
  local_username: string;
  local_password: string;
  local_socks_port: number;
  local_http_port: number;
  http_proxy_enabled: boolean;
  disable_udp: boolean;
  fast_open: boolean;
  lazy: boolean;
  hysteria_congestion: "bbr" | "reno";
  bbr_profile: "standard" | "conservative" | "aggressive";
  up_mbps: number;
  down_mbps: number;
  disable_loss_compensation: boolean;
  congestion_control: "bbr" | "cubic" | "new_reno";
  heartbeat: "5s" | "10s" | "15s" | "30s";
  udp_relay_mode: "native" | "quic";
  network: "all" | "tcp" | "udp";
  tcp_fast_open: boolean;
  set_system_proxy: boolean;
  udp_fragment: boolean;
  udp_timeout: "1m" | "3m" | "5m" | "10m";
  initial_packet_size: number;
  disable_path_mtu_discovery: boolean;
  fingerprint: "chrome" | "firefox" | "safari";
  xray_sni: string;
  mux_enabled: boolean;
  mux_concurrency: number;
  xudp_concurrency: number;
  xudp_proxy_udp443: "reject" | "allow" | "skip";
  sniffing: boolean;
  route_only: boolean;
  routing_domain_strategy: "AsIs" | "IPIfNonMatch" | "IPOnDemand";
  log_level: "none" | "error" | "warning" | "info";
  xray_dns: string;
  block_bittorrent: boolean;
};

export type ConnectionServerOptions = {
  awg?: { jc: number; jmin: number; jmax: number; s1: number; s2: number; h1: number; h2: number; h3: number; h4: number };
  xray?: { server_names: string[]; default_sni: string };
};

const settingsFor = (protocol: Protocol, serverOptions: ConnectionServerOptions): ConnectionSettings => ({
  dns: "1.1.1.1, 1.0.0.1",
  mtu: 1280,
  keepalive: 25,
  route_mode: "ipv4",
  allowed_ips: "0.0.0.0/0",
  awg_jc: serverOptions.awg?.jc ?? 6,
  awg_jmin: serverOptions.awg?.jmin ?? 8,
  awg_jmax: serverOptions.awg?.jmax ?? 80,
  proxy_bind: "loopback",
  local_auth_enabled: false,
  local_username: "proxy",
  local_password: "",
  local_socks_port: protocol === "tuic" ? 2080 : protocol === "xray" ? 10808 : 1080,
  local_http_port: protocol === "hysteria2" ? 8080 : 10809,
  http_proxy_enabled: false,
  disable_udp: false,
  fast_open: false,
  lazy: false,
  hysteria_congestion: "bbr",
  bbr_profile: "standard",
  up_mbps: 0,
  down_mbps: 0,
  disable_loss_compensation: false,
  congestion_control: "bbr",
  heartbeat: "10s",
  udp_relay_mode: "native",
  network: "all",
  tcp_fast_open: false,
  set_system_proxy: false,
  udp_fragment: false,
  udp_timeout: "5m",
  initial_packet_size: 0,
  disable_path_mtu_discovery: false,
  fingerprint: "chrome",
  xray_sni: serverOptions.xray?.default_sni ?? "",
  mux_enabled: false,
  mux_concurrency: 8,
  xudp_concurrency: 16,
  xudp_proxy_udp443: "reject",
  sniffing: true,
  route_only: false,
  routing_domain_strategy: "AsIs",
  log_level: "warning",
  xray_dns: "",
  block_bittorrent: false,
});

type Props = {
  protocols: Protocol[];
  serverOptions: ConnectionServerOptions;
  onClose(): void;
  onCreate(payload: { name: string; protocol: Protocol; settings: ConnectionSettings }): Promise<ConnectionProfile>;
  onCreated(): Promise<void> | void;
  onError(message: string): void;
  onDownload(filename: string, content: string, mimeType?: string): void;
};

export function ConnectionDialog({ protocols, serverOptions, onClose, onCreate, onCreated, onError, onDownload }: Props) {
  const initialProtocol = protocols[0] || "awg";
  const [name, setName] = useState("");
  const [protocol, setProtocol] = useState<Protocol>(initialProtocol);
  const [settings, setSettings] = useState<ConnectionSettings>(() => settingsFor(initialProtocol, serverOptions));
  const [profile, setProfile] = useState<ConnectionProfile | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  function selectProtocol(next: Protocol) {
    setProtocol(next);
    setSettings(settingsFor(next, serverOptions));
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
    setSettings(settingsFor(protocol, serverOptions));
  }

  const meta = protocolDelivery[protocol];
  const proxyProtocol = protocol === "hysteria2" || protocol === "tuic" || protocol === "xray";
  const localAccessInvalid = proxyProtocol && settings.local_auth_enabled && settings.local_password.length < 8;
  return <div className="confirmBackdrop connectionDialogBackdrop" role="presentation" onMouseDown={() => { if (!submitting && !profile) onClose(); }}>
    <form className={`connectionDialog${profile ? " generated" : ""}`} role="dialog" aria-modal="true" aria-labelledby="connection-dialog-title" onMouseDown={(event) => event.stopPropagation()} onSubmit={submit}>
      <header className="connectionDialogHead"><div><p className="eyebrow">PERSONAL ACCESS</p><h2 id="connection-dialog-title">{profile ? "Подключение создано" : "Новое подключение"}</h2><span>{profile ? "Передайте профиль владельцу устройства и сохраните его сейчас." : "Настройте отдельный профиль для конкретного пользователя или устройства."}</span></div><button type="button" aria-label="Закрыть" onClick={onClose} disabled={submitting}>×</button></header>
      {!profile ? <>
        <div className="connectionDialogBody">
          <label className="connectionNameField"><span>Пользователь или устройство</span><input autoFocus required minLength={2} maxLength={48} pattern="[\\p{L}\\p{N}_. -]{2,48}" title="От 2 до 48 символов: буквы, цифры, пробел, точка, дефис или _" value={name} onChange={(event) => setName(event.target.value)} placeholder="Например: Анна · iPhone" /><small>Имя используется в панели и в экспортируемом профиле.</small></label>
          <fieldset className="connectionProtocolPicker"><legend>Тип подключения</legend><div>{protocols.map((item) => { const itemMeta = protocolDelivery[item]; return <button type="button" key={item} className={protocol === item ? "active" : ""} onClick={() => selectProtocol(item)}><span className={`protocol ${item}`}><ProtocolIcon protocol={item} /></span><span><strong>{itemMeta.title}</strong><small>{itemMeta.summary}</small></span><i /></button>; })}</div></fieldset>
          <fieldset className="connectionSettings"><legend>Параметры профиля</legend><header><span className={`protocol ${protocol}`}><ProtocolIcon protocol={protocol} /></span><div><strong>{meta.title}</strong><small>{meta.transport} · {meta.methods.join(" · ")}</small></div></header>
            <div className="connectionSettingsFields">
              {protocol === "awg" && <>
                <label><span>MTU</span><input type="number" min={1280} max={1500} value={settings.mtu} onChange={(event) => update({ mtu: Number(event.target.value) })} /></label>
                <label><span>Keepalive, сек.</span><input type="number" min={0} max={300} value={settings.keepalive} onChange={(event) => update({ keepalive: Number(event.target.value) })} /></label>
                <label><span>Маршрутизация</span><select value={settings.route_mode} onChange={(event) => update({ route_mode: event.target.value as ConnectionSettings["route_mode"] })}><option value="ipv4">Весь IPv4-трафик</option><option value="all">IPv4 + IPv6</option><option value="custom">Собственные сети</option></select></label>
                {settings.route_mode === "custom" && <label><span>Allowed IPs</span><input value={settings.allowed_ips} onChange={(event) => update({ allowed_ips: event.target.value })} placeholder="10.0.0.0/8, 192.168.0.0/16" /></label>}
              </>}
              {protocol === "hysteria2" && <>
                <label><span>Локальный SOCKS5-порт</span><input type="number" min={1024} max={65535} value={settings.local_socks_port} onChange={(event) => update({ local_socks_port: Number(event.target.value) })} /></label>
                <label className="connectionCheckbox"><span><strong>Отключить UDP в SOCKS5</strong><small>Используйте только при ограничениях клиента</small></span><input type="checkbox" checked={settings.disable_udp} onChange={(event) => update({ disable_udp: event.target.checked })} /></label>
                <label className="connectionCheckbox"><span><strong>HTTP-прокси</strong><small>Дополнительный локальный порт</small></span><input type="checkbox" checked={settings.http_proxy_enabled} onChange={(event) => update({ http_proxy_enabled: event.target.checked })} /></label>
                {settings.http_proxy_enabled && <label><span>Локальный HTTP-порт</span><input type="number" min={1024} max={65535} value={settings.local_http_port} onChange={(event) => update({ local_http_port: Number(event.target.value) })} /></label>}
              </>}
              {protocol === "tuic" && <>
                <label><span>Локальный mixed-порт</span><input type="number" min={1024} max={65535} value={settings.local_socks_port} onChange={(event) => update({ local_socks_port: Number(event.target.value) })} /></label>
                <label><span>Congestion control</span><select value={settings.congestion_control} onChange={(event) => update({ congestion_control: event.target.value as ConnectionSettings["congestion_control"] })}><option value="bbr">BBR</option><option value="cubic">CUBIC</option><option value="new_reno">New Reno</option></select></label>
                <label><span>Heartbeat</span><select value={settings.heartbeat} onChange={(event) => update({ heartbeat: event.target.value as ConnectionSettings["heartbeat"] })}><option value="5s">5 секунд</option><option value="10s">10 секунд</option><option value="15s">15 секунд</option><option value="30s">30 секунд</option></select></label>
              </>}
              {protocol === "xray" && <>
                <label><span>Локальный SOCKS-порт</span><input type="number" min={1024} max={65535} value={settings.local_socks_port} onChange={(event) => update({ local_socks_port: Number(event.target.value) })} /></label>
                <label><span>Локальный HTTP-порт</span><input type="number" min={1024} max={65535} value={settings.local_http_port} onChange={(event) => update({ local_http_port: Number(event.target.value) })} /></label>
                <label><span>SNI для REALITY</span><select value={settings.xray_sni} onChange={(event) => update({ xray_sni: event.target.value })}>{serverOptions.xray?.server_names.length ? serverOptions.xray.server_names.map((name) => <option key={name} value={name}>{name}</option>) : <option value="">Берётся из конфигурации сервера</option>}</select><small>Можно выбрать только имя, разрешённое сервером</small></label>
                <label><span>TLS fingerprint</span><select value={settings.fingerprint} onChange={(event) => update({ fingerprint: event.target.value as ConnectionSettings["fingerprint"] })}><option value="chrome">Chrome — рекомендуется</option><option value="firefox">Firefox</option><option value="safari">Safari</option></select></label>
                <label className="connectionCheckbox"><span><strong>Отключить UDP</strong><small>Оставьте выключенным для обычной работы</small></span><input type="checkbox" checked={settings.disable_udp} onChange={(event) => update({ disable_udp: event.target.checked })} /></label>
              </>}
            </div>
            {proxyProtocol && <section className="connectionSettingsGroup"><header><strong>Локальный прокси</strong><small>Общие настройки доступа для proxy-протоколов</small></header><div className="connectionSettingsFields">
              <label><span>Доступ к локальному прокси</span><select value={settings.proxy_bind} onChange={(event) => update({ proxy_bind: event.target.value as ConnectionSettings["proxy_bind"], ...(event.target.value === "lan" ? { local_auth_enabled: true } : {}) })}><option value="loopback">Только это устройство</option><option value="lan">Локальная сеть — с авторизацией</option></select></label>
              <label className="connectionCheckbox"><span><strong>Логин и пароль</strong><small>Защищает локальный SOCKS/HTTP/mixed-порт</small></span><input type="checkbox" checked={settings.local_auth_enabled} disabled={settings.proxy_bind === "lan"} onChange={(event) => update({ local_auth_enabled: event.target.checked })} /></label>
              {settings.local_auth_enabled && <><label><span>Локальный логин</span><input value={settings.local_username} maxLength={64} onChange={(event) => update({ local_username: event.target.value })} /></label><label><span>Локальный пароль</span><input type="password" minLength={8} maxLength={128} value={settings.local_password} onChange={(event) => update({ local_password: event.target.value })} placeholder="Минимум 8 символов" /><small>Хранится только в экспортируемом профиле</small></label></>}
            </div></section>}
            <details className="connectionAdvanced"><summary>Расширенные настройки <span>⌄</span></summary><div className="connectionSettingsFields">
              {protocol === "awg" && <>
                <label><span>DNS-серверы</span><input value={settings.dns} onChange={(event) => update({ dns: event.target.value })} placeholder="1.1.1.1, 1.0.0.1" /></label>
                {protocol === "awg" && <><label><span>Jc · пакеты мусора</span><input type="number" min={0} max={128} value={settings.awg_jc} onChange={(event) => update({ awg_jc: Number(event.target.value) })} /></label><label><span>Jmin · минимум</span><input type="number" min={0} max={1280} value={settings.awg_jmin} onChange={(event) => update({ awg_jmin: Number(event.target.value) })} /></label><label><span>Jmax · максимум</span><input type="number" min={0} max={1280} value={settings.awg_jmax} onChange={(event) => update({ awg_jmax: Number(event.target.value) })} /></label><div className="connectionServerValues"><strong>Параметры сервера — подставляются автоматически</strong><dl><div><dt>S1</dt><dd>{serverOptions.awg?.s1 ?? "—"}</dd></div><div><dt>S2</dt><dd>{serverOptions.awg?.s2 ?? "—"}</dd></div><div><dt>H1</dt><dd>{serverOptions.awg?.h1 ?? "—"}</dd></div><div><dt>H2</dt><dd>{serverOptions.awg?.h2 ?? "—"}</dd></div><div><dt>H3</dt><dd>{serverOptions.awg?.h3 ?? "—"}</dd></div><div><dt>H4</dt><dd>{serverOptions.awg?.h4 ?? "—"}</dd></div></dl><small>S/H должны совпадать на клиенте и сервере. Индивидуально меняются только Jc, Jmin и Jmax.</small></div></>}
              </>}
              {protocol === "hysteria2" && <>
                <label><span>Congestion control</span><select value={settings.hysteria_congestion} onChange={(event) => update({ hysteria_congestion: event.target.value as ConnectionSettings["hysteria_congestion"] })}><option value="bbr">BBR</option><option value="reno">New Reno</option></select></label>
                {settings.hysteria_congestion === "bbr" && <label><span>Профиль BBR</span><select value={settings.bbr_profile} onChange={(event) => update({ bbr_profile: event.target.value as ConnectionSettings["bbr_profile"] })}><option value="standard">Стандартный</option><option value="conservative">Консервативный</option><option value="aggressive">Агрессивный</option></select></label>}
                <label><span>Upload, Мбит/с</span><input type="number" min={0} max={10000} value={settings.up_mbps} onChange={(event) => update({ up_mbps: Number(event.target.value) })} /><small>0 — автоматический режим</small></label>
                <label><span>Download, Мбит/с</span><input type="number" min={0} max={10000} value={settings.down_mbps} onChange={(event) => update({ down_mbps: Number(event.target.value) })} /><small>0 — автоматический режим</small></label>
                <label className="connectionCheckbox"><span><strong>Без компенсации потерь</strong><small>Не превышать заданный upload при потерях</small></span><input type="checkbox" disabled={!settings.up_mbps && !settings.down_mbps} checked={settings.disable_loss_compensation} onChange={(event) => update({ disable_loss_compensation: event.target.checked })} /></label>
                <label className="connectionCheckbox"><span><strong>Fast Open</strong><small>Быстрее старт, менее строгая семантика прокси</small></span><input type="checkbox" checked={settings.fast_open} onChange={(event) => update({ fast_open: event.target.checked })} /></label>
                <label className="connectionCheckbox"><span><strong>Lazy connect</strong><small>Подключаться только при первом запросе</small></span><input type="checkbox" checked={settings.lazy} onChange={(event) => update({ lazy: event.target.checked })} /></label>
              </>}
              {protocol === "tuic" && <>
                <label><span>UDP relay</span><select value={settings.udp_relay_mode} onChange={(event) => update({ udp_relay_mode: event.target.value as ConnectionSettings["udp_relay_mode"] })}><option value="native">Native — рекомендуется</option><option value="quic">QUIC stream — без потерь</option></select></label>
                <label><span>Разрешённый трафик</span><select value={settings.network} onChange={(event) => update({ network: event.target.value as ConnectionSettings["network"] })}><option value="all">TCP + UDP</option><option value="tcp">Только TCP</option><option value="udp">Только UDP</option></select></label>
                <label className="connectionCheckbox"><span><strong>TCP Fast Open</strong><small>Для локального mixed-прокси</small></span><input type="checkbox" checked={settings.tcp_fast_open} onChange={(event) => update({ tcp_fast_open: event.target.checked })} /></label>
                <label className="connectionCheckbox"><span><strong>Системный прокси</strong><small>sing-box установит и очистит настройки ОС</small></span><input type="checkbox" checked={settings.set_system_proxy} onChange={(event) => update({ set_system_proxy: event.target.checked })} /></label>
                <label><span>UDP timeout</span><select value={settings.udp_timeout} onChange={(event) => update({ udp_timeout: event.target.value as ConnectionSettings["udp_timeout"] })}><option value="1m">1 минута</option><option value="3m">3 минуты</option><option value="5m">5 минут</option><option value="10m">10 минут</option></select></label>
                <label><span>Начальный QUIC-пакет</span><select value={settings.initial_packet_size} onChange={(event) => update({ initial_packet_size: Number(event.target.value) })}><option value={0}>Автоматически</option><option value={1200}>1200 B</option><option value={1300}>1300 B</option><option value={1400}>1400 B</option><option value={1500}>1500 B</option></select></label>
                <label className="connectionCheckbox"><span><strong>UDP fragmentation</strong><small>Разрешить фрагментацию локального UDP</small></span><input type="checkbox" checked={settings.udp_fragment} onChange={(event) => update({ udp_fragment: event.target.checked })} /></label>
                <label className="connectionCheckbox"><span><strong>Отключить Path MTU discovery</strong><small>Только для проблемных сетей</small></span><input type="checkbox" checked={settings.disable_path_mtu_discovery} onChange={(event) => update({ disable_path_mtu_discovery: event.target.checked })} /></label>
              </>}
              {protocol === "xray" && <>
                <label><span>Маршрутизация доменов</span><select value={settings.routing_domain_strategy} onChange={(event) => update({ routing_domain_strategy: event.target.value as ConnectionSettings["routing_domain_strategy"] })}><option value="AsIs">AsIs — без доп. DNS</option><option value="IPIfNonMatch">IPIfNonMatch</option><option value="IPOnDemand">IPOnDemand</option></select></label>
                <label><span>Уровень журнала</span><select value={settings.log_level} onChange={(event) => update({ log_level: event.target.value as ConnectionSettings["log_level"] })}><option value="none">Выключен</option><option value="error">Только ошибки</option><option value="warning">Предупреждения</option><option value="info">Информация</option></select></label>
                <label><span>Встроенные DNS</span><input value={settings.xray_dns} onChange={(event) => update({ xray_dns: event.target.value })} placeholder="1.1.1.1, 8.8.8.8" /><small>Пусто — использовать DNS системы</small></label>
                <label className="connectionCheckbox"><span><strong>Sniffing доменов</strong><small>Определять HTTP, TLS и QUIC назначения</small></span><input type="checkbox" checked={settings.sniffing} onChange={(event) => update({ sniffing: event.target.checked, ...(!event.target.checked ? { route_only: false } : {}) })} /></label>
                <label className="connectionCheckbox"><span><strong>Только для маршрутизации</strong><small>Не подменять исходное назначение</small></span><input type="checkbox" disabled={!settings.sniffing} checked={settings.route_only} onChange={(event) => update({ route_only: event.target.checked })} /></label>
                <label className="connectionCheckbox"><span><strong>Блокировать BitTorrent</strong><small>Локальное правило blackhole в профиле</small></span><input type="checkbox" checked={settings.block_bittorrent} onChange={(event) => update({ block_bittorrent: event.target.checked })} /></label>
                <label className="connectionCheckbox"><span><strong>Mux</strong><small>Снижает число TCP-handshake; для скорости загрузки обычно не нужен</small></span><input type="checkbox" checked={settings.mux_enabled} onChange={(event) => update({ mux_enabled: event.target.checked })} /></label>
                {settings.mux_enabled && <><label><span>Mux concurrency</span><input type="number" min={1} max={128} value={settings.mux_concurrency} onChange={(event) => update({ mux_concurrency: Number(event.target.value) })} /></label><label><span>XUDP concurrency</span><input type="number" min={1} max={1024} value={settings.xudp_concurrency} onChange={(event) => update({ xudp_concurrency: Number(event.target.value) })} /></label><label><span>UDP/443 через XUDP</span><select value={settings.xudp_proxy_udp443} onChange={(event) => update({ xudp_proxy_udp443: event.target.value as ConnectionSettings["xudp_proxy_udp443"] })}><option value="reject">Reject — рекомендуется</option><option value="allow">Allow</option><option value="skip">Skip Mux</option></select></label></>}
              </>}
            </div></details>
            {(protocol === "hysteria2" || protocol === "xray") && <p className="connectionSettingsNote">Локальные расширенные параметры полностью сохраняются в файле. QR и ссылка используют стандартный переносимый URI протокола, поэтому приложение клиента может применить собственные локальные значения.</p>}
            {(protocol === "awg" || protocol === "hysteria2" || protocol === "xray") && <p className="connectionSettingsNote">Криптографические параметры сервера подставляются автоматически и не редактируются: несовпадение сделало бы профиль нерабочим.</p>}
          </fieldset>
          {formError && <div className="connectionDialogError" role="alert">{formError}</div>}
        </div>
        <footer className="connectionDialogActions"><button type="button" onClick={onClose} disabled={submitting}>Отмена</button><button className="primaryButton" disabled={submitting || name.trim().length < 2 || localAccessInvalid}>{submitting ? "Создаём…" : localAccessInvalid ? "Укажите пароль" : "Создать подключение"}</button></footer>
      </> : <>
        <div className="connectionDialogResult"><ConnectionProfileResult profile={profile} onDownload={onDownload} /></div>
        <footer className="connectionDialogActions"><button type="button" onClick={createAnother}>Создать ещё</button><button type="button" className="primaryButton" onClick={onClose}>Готово</button></footer>
      </>}
    </form>
  </div>;
}
