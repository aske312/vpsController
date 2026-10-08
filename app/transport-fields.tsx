import type { ConnectionSettings } from "./connection-dialog";
import type { Protocol } from "./page";

type Props = { protocol: Protocol; settings: ConnectionSettings; errors: Partial<Record<keyof ConnectionSettings, string>>; update(patch: Partial<ConnectionSettings>): void };
export function TransportFields({ protocol, settings, errors, update }: Props) {
  const numeric = (key: keyof ConnectionSettings, label: string, max: number, note: string) => <label className={errors[key] ? "fieldInvalid" : ""} key={key}><span>{label}</span><input type="number" min={0} max={max} step={1} aria-invalid={Boolean(errors[key])} value={String(settings[key])} onChange={(event) => update({ [key]: Number(event.target.value) })} /><small>{errors[key] || note}</small></label>;
  if (protocol === "hysteria2" || protocol === "tuic") return <>
    {numeric("quic_idle", "QUIC idle timeout, сек.", 600, "0 — штатное значение ядра.")}
    {numeric("quic_stream_window", "Окно потока, MiB", 64, "0 — автоматически; большое окно требует больше памяти.")}
    {numeric("quic_conn_window", "Окно соединения, MiB", 160, "Задайте вместе с окном потока, не меньше него.")}
    {protocol === "tuic" && numeric("quic_keepalive", "QUIC keepalive, сек.", 300, "0 — автоматически; меньше idle timeout.")}
    {(protocol === "tuic" || settings.hysteria_format === "sing-box") && numeric("quic_streams", "Максимум QUIC-потоков", 4096, "0 — автоматически. QUIC-расширения в JSON требуют sing-box 1.14+.")}
    {protocol === "hysteria2" && <label className="connectionCheckbox"><span><strong>QUIC-параметры Chrome</strong><small>Включено: ядро подставляет параметры Chrome, включая idle 30 с.</small></span><input type="checkbox" checked={settings.hysteria_chrome_parrot} onChange={(event) => update({ hysteria_chrome_parrot: event.target.checked })} /></label>}
    {protocol === "tuic" && <label className="connectionCheckbox"><span><strong>UDP over stream · расширение sing-box</strong><small>Альтернатива native/quic relay; не превращает транспорт TUIC в TCP.</small></span><input type="checkbox" checked={settings.tuic_udp_over_stream} onChange={(event) => update({ tuic_udp_over_stream: event.target.checked })} /></label>}
  </>;
  if (protocol !== "xray") return null;
  return <>
    <label><span>XHTTP padding, байт</span><input value={settings.xray_padding} onChange={(event) => update({ xray_padding: event.target.value })} /><small>{errors.xray_padding || "Число или диапазон внутри 100–1000 — согласовано с сервером."}</small></label>
    <label><span>Ручной XMUX</span><input type="checkbox" checked={settings.xray_xmux_profile === "custom"} onChange={(event) => update({ xray_xmux_profile: event.target.checked ? "custom" : "default" })} /></label>
    {settings.xray_xmux_profile === "custom" && <>{([
      ["xmux_concurrency", "Одновременные запросы", "0–1024"], ["xmux_connections", "Соединения", "0–1024; либо это поле, либо запросы — 0"],
      ["xmux_reuse", "Повторные использования", "0–1000000"], ["xmux_requests", "HTTP-запросы на соединение", "0–1000000"],
      ["xmux_seconds", "Переиспользование, сек.", "0–86400"],
    ] as const).map(([key, label, note]) => <label className={errors[key] ? "fieldInvalid" : ""} key={key}><span>{label}</span><input aria-invalid={Boolean(errors[key])} value={settings[key]} onChange={(event) => update({ [key]: event.target.value })} /><small>{errors[key] || `${note}; число или min-max.`}</small></label>)}{numeric("xmux_keepalive", "H2 keepalive, сек.", 300, "0 — без дополнительного keepalive.")}</>}
  </>;
}
