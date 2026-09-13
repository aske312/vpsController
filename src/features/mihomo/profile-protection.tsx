import { clientCapabilities } from "./client-capabilities";
import type { Module, ProfileConnection } from "./types";

type Routing = Record<string, string | number | boolean>;

export function ProfileProtection({ routing, connections, modules, common = false, onChange }: { routing: Routing; connections: ProfileConnection[]; modules: Module[]; common?: boolean; onChange: (key: string, value: string | boolean) => void }) {
  const fragment = Boolean(routing.tunnel_fragment);
  const caps = clientCapabilities(routing.client_config_format);
  const showFragment = common || caps.features.includes("tunnel_fragment");
  const compatible = connections.some((connection) => connection.component === "transport-reality" && modules.some((module) => module.id === connection.component && module.installed));
  return <section className="mihomoProfileRules">
    <header><div><b>Защита соединений</b><small>Настройки выбранного устройства. После сохранения обновите подписку в клиенте.</small></div><span>Выбрано {[...(common ? ["tunnel_privacy", "tunnel_ech", "tunnel_fragment"] : caps.features)].filter((key) => routing[key]).length} из {common ? 3 : caps.features.length}</span></header>
    <div>
      {(common || caps.features.includes("tunnel_privacy")) && <button type="button" className={`mihomoProfileRuleButton${routing.tunnel_privacy ? " is-enabled" : ""}`} aria-pressed={Boolean(routing.tunnel_privacy)} onClick={() => onChange("tunnel_privacy", !routing.tunnel_privacy)}><i>VPS</i><span><b>Шифрование до VPS</b><small>VLESS Encryption · требуется актуальное ядро клиента</small></span></button>}
      {(common || caps.features.includes("tunnel_ech")) && <button type="button" className={`mihomoProfileRuleButton${routing.tunnel_ech ? " is-enabled" : ""}`} aria-pressed={Boolean(routing.tunnel_ech)} onClick={() => onChange("tunnel_ech", !routing.tunnel_ech)}><i>ECH</i><span><b>Скрытие имени сервера (ECH)</b><small>Зависит от домена, клиента и сети; может замедлять подключение</small></span></button>}
      {showFragment && <button type="button" className={`mihomoProfileRuleButton${fragment ? " is-enabled" : ""}`} aria-pressed={fragment} disabled={!compatible && !fragment} onClick={() => onChange("tunnel_fragment", !fragment)} aria-describedby="mihomo-fragment-status"><i>TLS</i><span><b>Фрагментация TLS</b>{common && <small>Только для sing-box клиентов</small>}</span></button>}
    </div>
    {showFragment && <p id="mihomo-fragment-status" className="mihomoFragmentStatus">{!compatible ? "Нет VLESS-подключений из установленных модулей для фрагментации." : "Фрагментация применяется к VLESS поверх TLS/REALITY, включая CDN. WireGuard и Shadowsocks работают без неё."}</p>}
    {showFragment && fragment && <p>sing-box ≥ 1.12: размер и задержку фрагментов определяет ядро клиента.</p>}
  </section>;
}
