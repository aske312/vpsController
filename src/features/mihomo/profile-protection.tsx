import type { Module, ProfileConnection } from "./types";

type Routing = Record<string, string | number | boolean>;

export function ProfileFormatSelect({ routing, onChange }: { routing: Routing; onChange: (key: string, value: string | boolean) => void }) {
  return <label><span>Файл для клиента</span><select aria-label="Файл для клиента" value={String(routing.client_config_format || "mihomo")} onChange={(event) => onChange("client_config_format", event.target.value)}><option value="mihomo">Mihomo YAML</option><option value="singbox">sing-box JSON</option></select><small>Формат привязан к выбранному общему или HWID-профилю и используется подпиской и скачиванием.</small></label>;
}

export function ProfileProtection({ routing, connections, modules, onChange }: { routing: Routing; connections: ProfileConnection[]; modules: Module[]; onChange: (key: string, value: string | boolean) => void }) {
  const fragment = Boolean(routing.tunnel_fragment);
  const singbox = routing.client_config_format === "singbox";
  const compatible = connections.some((connection) => connection.component === "transport-reality" && modules.some((module) => module.id === connection.component && module.installed));
  return <section className="mihomoProfileRules">
    <header><div><b>Защита соединений</b><small>Настройки выбранного устройства. После сохранения обновите подписку в клиенте.</small></div><span>Выбрано {[routing.tunnel_privacy, routing.tunnel_ech, singbox && fragment].filter(Boolean).length} из {singbox ? 3 : 2}</span></header>
    <div>
      <button type="button" className={`mihomoProfileRuleButton${routing.tunnel_privacy ? " is-enabled" : ""}`} aria-pressed={Boolean(routing.tunnel_privacy)} onClick={() => onChange("tunnel_privacy", !routing.tunnel_privacy)}><i>VPS</i><span><b>Шифрование до VPS</b><small>VLESS · Mihomo ≥ 1.19.30; старый конфиг доступен до обновления подписки</small></span></button>
      <button type="button" className={`mihomoProfileRuleButton${routing.tunnel_ech ? " is-enabled" : ""}`} aria-pressed={Boolean(routing.tunnel_ech)} onClick={() => onChange("tunnel_ech", !routing.tunnel_ech)}><i>ECH</i><span><b>Скрытие имени сервера (ECH)</b><small>Зависит от домена, клиента и сети; может замедлять подключение</small></span></button>
      {singbox && <button type="button" className={`mihomoProfileRuleButton${fragment ? " is-enabled" : ""}`} aria-pressed={fragment} disabled={!compatible && !fragment} onClick={() => onChange("tunnel_fragment", !fragment)} aria-describedby="mihomo-fragment-status"><i>TLS</i><span><b>Фрагментация TLS</b></span></button>}
    </div>
    {singbox && <p id="mihomo-fragment-status" className="mihomoFragmentStatus">{!compatible ? "Нет VLESS-подключений из установленных модулей для фрагментации." : "Фрагментация применяется к VLESS поверх TLS/REALITY, включая CDN. WireGuard и Shadowsocks работают без неё."}</p>}
    {singbox && fragment && <div className="mihomoFragmentFields">
      <label><span>Размер фрагментов, байт</span><input aria-label="Размер фрагментов, байт" value={String(routing.tunnel_fragment_size ?? "100-200")} onChange={(event) => onChange("tunnel_fragment_size", event.target.value)} required maxLength={16} pattern="[0-9]{1,5}([ ]*\x2d[ ]*[0-9]{1,5})?" title="Число или диапазон от 1 до 16384, например 100-200" /><small>От 1 до 16384. Число или диапазон, например 100-200.</small></label>
      <label><span>Задержка между фрагментами, мс</span><input aria-label="Задержка между фрагментами, мс" value={String(routing.tunnel_fragment_interval ?? "10-20")} onChange={(event) => onChange("tunnel_fragment_interval", event.target.value)} required maxLength={16} pattern="[0-9]{1,5}([ ]*\x2d[ ]*[0-9]{1,5})?" title="Число или диапазон от 0 до 1000, например 10-20" /><small>От 0 до 1000. Ноль — без дополнительной задержки.</small></label>
    </div>}
  </section>;
}
