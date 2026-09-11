import type { Dispatch, SetStateAction } from "react";
import type { DnsSettings, DnsStatus } from "../../shared/types/control-plane";

export function SystemDnsControl({ dns, dnsDraft, setDnsDraft }: { dns: DnsStatus | null; dnsDraft: DnsSettings | null; setDnsDraft: Dispatch<SetStateAction<DnsSettings | null>> }) {
  return <section className="networkSystemControl"><header><div><span className="networkCaption">VPS RESOLVER</span><h3>DNS самого сервера</h3><p>Управляет системным resolver’ом VPS. Изменение применяется после общей политики.</p></div><label><input type="checkbox" checked={dnsDraft?.apply_system ?? false} onChange={(event) => setDnsDraft((current) => current ? { ...current, apply_system: event.target.checked } : current)} /><span>Применять профиль к VPS</span></label></header><div><strong>{dns?.system?.addresses?.join(", ") || "Не определён"}</strong><small>{dns?.system?.source || "Источник не определён"} · {dns?.system?.managed ? "управляется 312.net" : "внешнее управление"}</small></div></section>;
}
