import type { ProfileConnection } from "./types";

type Rule = { kind: string; value: string; target: string };
type Group = { name: string; type: string; members: string[]; strategy: string };
type Policy = { rules: Rule[]; groups: Group[] };
const kinds = [["domain", "Домен"], ["domain-suffix", "Домен и поддомены"], ["ip-cidr", "IP / CIDR · IPv4 или IPv6"], ["geoip", "GeoIP"], ["geosite", "GeoSite"], ["process", "Процесс · TUN"]];
const groupTypes = [["select", "Ручной выбор"], ["fallback", "Резерв по порядку"], ["url-test", "Минимальная задержка"], ["load-balance", "Балансировка"]];
const algorithms = [["consistent-hashing", "По адресу назначения"], ["round-robin", "По очереди"], ["sticky-sessions", "Закреплять сессии"]];
export function parsePolicy(raw: unknown): Policy {
  try { const value = JSON.parse(String(raw || "{}")); if (!Array.isArray(value.rules ?? []) || !Array.isArray(value.groups ?? [])) throw new Error(); return { rules: value.rules ?? [], groups: value.groups ?? [] }; }
  catch { return { rules: [], groups: [] }; }
}
function move<T>(items: T[], from: number, direction: number) {
  const target = from + direction;
  if (target < 0 || target >= items.length) return items;
  const result = [...items]; [result[from], result[target]] = [result[target], result[from]]; return result;
}
export function BalanceStrategy({ value, onChange }: { value: unknown; onChange: (value: string) => void }) {
  return <label>Алгоритм балансировки<select value={String(value || "consistent-hashing")} onChange={event => onChange(event.target.value)}>{algorithms.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>;
}

export function AdvancedRouting({ value, onChange, connections = [], inherited = false, reset }: {
  value: unknown; onChange: (value: string) => void; connections?: ProfileConnection[]; inherited?: boolean; reset?: () => void;
}) {
  const policy = parsePolicy(value);
  const update = (next: Policy) => onChange(JSON.stringify(next));
  const targets = [["GATE.312", "Основная группа VPN"], ["DIRECT", "DIRECT · напрямую"], ["REJECT", "REJECT · запрет"], ...policy.groups.map(group => [group.name, group.name])];
  const patchRule = (index: number, patch: Partial<Rule>) => update({ ...policy, rules: policy.rules.map((rule, at) => at === index ? { ...rule, ...patch } : rule) });
  const patchGroup = (index: number, patch: Partial<Group>) => {
    const old = policy.groups[index].name;
    update({ rules: patch.name === undefined ? policy.rules : policy.rules.map(rule => rule.target === old ? { ...rule, target: patch.name! } : rule),
      groups: policy.groups.map((group, at) => ({ ...(at === index ? { ...group, ...patch } : group),
        members: (at === index ? patch.members ?? group.members : group.members).map(member => patch.name !== undefined && member === `group:${old}` ? `group:${patch.name}` : member) })) });
  };
  const deleteGroup = (index: number) => update({ ...policy, groups: policy.groups.filter((_, at) => at !== index) });
  return <section className="mihomoStructuredPolicy"><header><div><b>Правила и группы маршрутизации</b></div><div className="mihomoPolicyHeaderActions">{inherited && <span className="mihomoPolicyInherited">Общая политика</span>}{reset && <button type="button" onClick={reset}>Наследовать</button>}</div></header>

    <h4>Группы</h4>
    {policy.groups.map((group, index) => {
      const members = [["*", "Все подключения устройства"], ...connections.map(connection => [`connection:${connection.id}`, connection.name || connection.component]), ...policy.groups.filter((_, at) => at !== index).map(other => [`group:${other.name}`, `Группа: ${other.name}`])];
      return <article key={index} className="mihomoPolicyGroup"><div className="mihomoPolicyFields"><label>Имя группы<input value={group.name} maxLength={40} placeholder="Media" onChange={event => patchGroup(index, { name: event.target.value })}/></label><label>Выбор канала<select value={group.type} onChange={event => patchGroup(index, { type: event.target.value })}>{groupTypes.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>{group.type === "load-balance" && <BalanceStrategy value={group.strategy} onChange={strategy => patchGroup(index, { strategy })}/>}<button type="button" onClick={() => deleteGroup(index)}>Удалить группу</button></div>
        <div className="mihomoPolicyMembers">{members.map(([key, text]) => <label key={key}><input type="checkbox" role="switch" checked={group.members.includes(key)} onChange={event => patchGroup(index, { members: event.target.checked ? [...group.members, key] : group.members.filter(member => member !== key) })}/>{text}</label>)}</div>
        <ol className="mihomoPolicyOrder">{group.members.map((member, at) => <li key={member}><span>{members.find(([key]) => key === member)?.[1] || `Недоступен: ${member}`}</span><button type="button" aria-label="Повысить приоритет участника" disabled={!at} onClick={() => patchGroup(index, { members: move(group.members, at, -1) })}>↑</button><button type="button" aria-label="Понизить приоритет участника" disabled={at === group.members.length - 1} onClick={() => patchGroup(index, { members: move(group.members, at, 1) })}>↓</button><button type="button" aria-label="Убрать участника" onClick={() => patchGroup(index, { members: group.members.filter((_, row) => row !== at) })}>×</button></li>)}</ol>
      </article>;
    })}
    <button type="button" disabled={policy.groups.length >= 20} onClick={() => { let number = policy.groups.length + 1; while (policy.groups.some(group => group.name === `Group${number}`)) number++; update({ ...policy, groups: [...policy.groups, { name: `Group${number}`, type: "fallback", members: ["*"], strategy: "consistent-hashing" }] }); }}>Добавить группу</button>
    <h4>Упорядоченные правила</h4>
    {policy.rules.map((rule, index) => <article key={index} className="mihomoPolicyRule"><span>{index + 1}</span><label>Условие<select value={rule.kind} onChange={event => patchRule(index, { kind: event.target.value, value: ["geoip", "geosite"].includes(event.target.value) ? "cn" : "" })}>{kinds.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>
      <label>Значение{["geoip", "geosite"].includes(rule.kind) ? <select value={rule.value} onChange={event => patchRule(index, { value: event.target.value })}>{(rule.kind === "geoip" ? ["cn", "ru"] : ["cn", "youtube", "telegram", "github"]).map(name => <option key={name}>{name}</option>)}</select> : <input value={rule.value} maxLength={253} placeholder={rule.kind === "ip-cidr" ? "203.0.113.0/24" : rule.kind === "process" ? "browser.exe" : "example.com"} onChange={event => patchRule(index, { value: event.target.value })}/>}</label>
      <label>Маршрут<select value={rule.target} onChange={event => patchRule(index, { target: event.target.value })}>{!targets.some(([key]) => key === rule.target) && <option value={rule.target}>Недоступен: {rule.target}</option>}{targets.map(([key, text], at) => <option key={`${key}:${at}`} value={key}>{text}</option>)}</select></label>
      <div className="mihomoPolicyActions"><button type="button" aria-label="Поднять правило" disabled={!index} onClick={() => update({ ...policy, rules: move(policy.rules, index, -1) })}>↑</button><button type="button" aria-label="Опустить правило" disabled={index === policy.rules.length - 1} onClick={() => update({ ...policy, rules: move(policy.rules, index, 1) })}>↓</button><button type="button" aria-label="Удалить правило" onClick={() => update({ ...policy, rules: policy.rules.filter((_, at) => at !== index) })}>×</button></div>
    </article>)}
    <button type="button" disabled={policy.rules.length >= 200} onClick={() => update({ ...policy, rules: [...policy.rules, { kind: "domain-suffix", value: "", target: "GATE.312" }] })}>Добавить правило</button>

  </section>;
}
