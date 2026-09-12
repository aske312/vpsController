import type { Dispatch, SetStateAction } from "react";
import type { DnsSettings, DnsStatus } from "../../shared/types/control-plane";

export function SystemDnsControl({ dns, dnsDraft, setDnsDraft }: { dns: DnsStatus | null; dnsDraft: DnsSettings | null; setDnsDraft: Dispatch<SetStateAction<DnsSettings | null>> }) {
  const scopes = [["system", "VPS", "DNS самого сервера"], ["wg", "WG", "WireGuard"], ["awg", "AWG", "AmneziaWG"], ["shadowsocks", "SS", "Shadowsocks"], ["vless-reality-xhttp", "VLESS", "Xray VLESS"]] as const;
  const value = (scope: string) => dnsDraft?.profiles?.[scope] || dnsDraft?.selected_id || "";
  const update = (scope: string, selected_id: string) => setDnsDraft((current) => current ? { ...current, profiles: { ...(current.profiles || {}), [scope]: selected_id } } : current);
  return <section className="networkSystemControl"><header><div><span className="networkCaption">DNS CONTROL MATRIX</span><h3>DNS для каждого компонента</h3><p>Каждая строка имеет собственный профиль. Сохранение применяет выбранные resolver’ы к соответствующим компонентам.</p></div></header><div className="networkDnsMatrix">{scopes.map(([scope, code, title]) => <label key={scope}><span><b>{code}</b><small>{title}{scope === "system" ? ` · ${dns?.system?.source || "системный resolver"}` : ""}</small></span><select value={value(scope)} onChange={(event) => update(scope, event.target.value)}>{(dns?.providers || []).map((provider) => <option value={provider.id} key={provider.id}>{provider.name}</option>)}</select>{scope === "system" && <input type="checkbox" checked={dnsDraft?.apply_system ?? false} onChange={(event) => setDnsDraft((current) => current ? { ...current, apply_system: event.target.checked } : current)} />}</label>)}</div></section>;
}
