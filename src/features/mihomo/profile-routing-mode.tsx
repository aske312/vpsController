import catalog from "../../../protocol-images/mihomo/vpn-services.json" with { type: "json" };
import { profileConnectionMode, type ProfileRouting } from "./profile-modes";

export function ProfileRoutingMode({ routing, onChange }: { routing: ProfileRouting; onChange: (key: string, value: string) => void }) {
  const selectedOnly = routing.routing_scope === "selected";
  const services = new Set(String(routing.vpn_services || "").split(",").filter(Boolean));
  const proxy = profileConnectionMode(routing) === "proxy";
  return <section className="mihomoRoutingMode">
    <header><b>Что направлять через VPN</b><small>{proxy ? "Правила действуют для трафика приложений, использующих локальный прокси." : "TUN принимает трафик устройства, затем выбирает прямой маршрут или VPN."}</small></header>
    <div className="mihomoModeChoices" role="group" aria-label="Область VPN">
      {([
        ["all", "Всё через VPN", "Кроме настроенных исключений с прямым маршрутом."],
        ["selected", "Только выбранное", "Выбранные сервисы и правила — через VPN, остальное напрямую."],
      ] as const).map(([value, title, note]) => <button type="button" key={value} aria-pressed={selectedOnly === (value === "selected")} onClick={() => onChange("routing_scope", value)}><b>{title}</b><small>{note}</small></button>)}
    </div>
    {selectedOnly && <>
      <div className="mihomoModeChoices" role="group" aria-label="Сервисы через VPN">{catalog.services.map(service => <button key={service.id} type="button" aria-pressed={services.has(service.id)} onClick={() => {
        const next = new Set(services);
        if (next.has(service.id)) next.delete(service.id); else next.add(service.id);
        onChange("vpn_services", [...next].join(","));
      }}><b>{service.name}</b><small>{service.description}</small></button>)}</div>
      <p className="mihomoHint">Свои сайты{proxy ? "" : " и приложения"} добавляйте персональными правилами ниже с маршрутом «Через VPN». Без выбранных сервисов и таких правил трафик идёт напрямую. Прямые персональные исключения имеют приоритет. Готовые прямые пресеты из режима «Всё через VPN» здесь не применяются.</p>
    </>}
    {proxy && <p className="mihomoHint">Укажите локальный прокси Mihomo в приложении или включите системный прокси в клиенте. Приложения, не использующие прокси, идут напрямую. Поддержка UDP зависит от приложения и клиента.</p>}
  </section>;
}
