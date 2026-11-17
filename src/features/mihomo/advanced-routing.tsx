import type { ProfileConnection } from "./types";
import { parsePolicySelection } from "./profile-modes";

type Rule = { kind: string; value: string; target: string };
type Group = { id?: string; name: string; type: string; members: string[]; strategy: string; url?: string; interval?: number; timeout?: number; max_failed_times?: number; tolerance?: number };
type Policy = { rules: Rule[]; groups: Group[] };
export type RoutingLibraryItem = { id: string; title: string; group: string; defaultTarget?: string };
const kinds = [["domain", "Домен"], ["domain-suffix", "Домен и поддомены"], ["ip-cidr", "IP / CIDR · IPv4 или IPv6"], ["geoip", "GeoIP"], ["geosite", "GeoSite"], ["process", "Процесс · TUN"]];
const groupTypes = [["select", "Ручной выбор"], ["fallback", "Резерв по порядку"], ["url-test", "Минимальная задержка"], ["load-balance", "Балансировка"]];
const algorithms = [["consistent-hashing", "По адресу назначения"], ["round-robin", "По очереди"], ["sticky-sessions", "Закреплять сессии"]];
const healthFields: { key: 'interval' | 'timeout' | 'max_failed_times' | 'tolerance'; label: string; min: number; max: number; shared: string; fallback: number }[] = [
  { key: 'interval', label: 'Интервал, сек.', min: 10, max: 3600, shared: 'interval', fallback: 30 },
  { key: 'timeout', label: 'Таймаут, мс', min: 1000, max: 10000, shared: 'health_timeout', fallback: 3000 },
  { key: 'max_failed_times', label: 'Порог ошибок', min: 1, max: 10, shared: 'max_failed_times', fallback: 2 },
  { key: 'tolerance', label: 'Погрешность выбора, мс', min: 0, max: 1000, shared: 'tolerance', fallback: 0 },
];
const groupHints: Record<string, string> = {
  select: "Канал выбирается вручную в клиенте Mihomo.",
  fallback: "Первый доступный канал в списке — основной, следующие — резервные.",
  "url-test": "Mihomo выбирает доступный канал с минимальной задержкой.",
  "load-balance": "Соединения распределяются между каналами по выбранному алгоритму.",
};
export function parsePolicy(raw: unknown): Policy {
  try { const value = JSON.parse(String(raw || "{}")); if (!Array.isArray(value.rules ?? []) || !Array.isArray(value.groups ?? [])) throw new Error(); return { rules: value.rules ?? [], groups: value.groups ?? [] }; }
  catch { return { rules: [], groups: [] }; }
}
export function libraryRoute(value: unknown, selection: unknown, source: string) {
  const policy = parsePolicy(value), choices = parsePolicySelection(selection);
  const rule = policy.rules.find(rule => rule.kind === "library" && rule.value === source);
  if (!rule) return undefined;
  const group = policy.groups.find(group => group.name === rule.target);
  if (choices[group ? group.id || group.name : "@rules"] === false) return undefined;
  return rule.target === "GATE.312" ? "Основной VPN" : rule.target === "DIRECT" ? "Напрямую" : rule.target === "REJECT" ? "Блокировать" : rule.target;
}
function move<T>(items: T[], from: number, direction: number) {
  const target = from + direction;
  if (target < 0 || target >= items.length) return items;
  const result = [...items]; [result[from], result[target]] = [result[target], result[from]]; return result;
}
export function BalanceStrategy({ value, onChange }: { value: unknown; onChange: (value: string) => void }) {
  return <label>Алгоритм балансировки<select value={String(value || "consistent-hashing")} onChange={event => onChange(event.target.value)}>{algorithms.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>;
}

export function ProfilePolicyGroups({ value, selection, ownSelection, onChange, onReset, onEdit, legacy = false, onUseShared, disabled = false }: {
  value: unknown; selection: unknown; ownSelection: unknown; onChange: (value: string) => void;
  onReset: () => void; onEdit: () => void; legacy?: boolean; onUseShared: () => void; disabled?: boolean;
}) {
  const policy = parsePolicy(value);
  const choices = parsePolicySelection(selection), own = parsePolicySelection(ownSelection);
  const cards = policy.groups.map(group => ({ id: group.id || group.name, name: group.name,
    count: policy.rules.filter(rule => rule.target === group.name).length,
    description: groupTypes.find(([key]) => key === group.type)?.[1] || group.type }));
  const ungrouped = policy.rules.filter(rule => ["GATE.312", "DIRECT", "REJECT"].includes(rule.target)).length;
  if (ungrouped) cards.unshift({ id: "@rules", name: "Правила без отдельной группы", count: ungrouped, description: "Основной VPN · напрямую · запрет" });
  return <section className="mihomoPolicyPicker">
    <header><div><b>Группы правил</b><small>{cards.filter(card => choices[card.id] !== false).length} / {cards.length}</small></div><button type="button" disabled={disabled} onClick={onEdit} aria-label="Настроить на странице «Правила»">Настроить</button></header>
    {legacy && <div className="mihomoPolicyLegacy"><p>Сохранён индивидуальный набор. Он продолжает действовать; общие группы можно подключить вместо него.</p><button type="button" disabled={disabled} onClick={onUseShared}>Использовать общие группы Mihomo</button></div>}
    <div className="mihomoPolicyPickerCards">{cards.map(card => {
      const enabled = choices[card.id] !== false;
      return <button key={card.id} type="button" disabled={disabled} aria-pressed={enabled} className={`mihomoProfileRuleButton${enabled ? " is-enabled" : ""}`} onClick={() => onChange(JSON.stringify({ ...own, [card.id]: !enabled }))}>
        <i aria-hidden="true"/><span><b>{card.name}</b><small>{card.description}</small></span><em title="Правил в группе" aria-label={`Правил: ${card.count}`}>{card.count}</em>
      </button>;
    })}</div>
    {!cards.length && <p>Группы ещё не настроены. Создайте правила и группы на странице «Правила».</p>}
    {cards.length > 0 && <details className="mihomoPolicyInfo"><summary>Применение групп</summary><p>Правила включаются выше в настройках профиля. Выключение группы отменяет назначенные ей маршруты наборов; отдельные условия группы исключаются. После сохранения обновите подписку в клиенте.</p></details>}
    <footer><span>{Object.keys(own).length ? "Свой выбор для этой конфигурации" : "Выбор наследуется"}</span>{Object.keys(own).length > 0 && <button type="button" disabled={disabled} onClick={onReset}>Наследовать выбор</button>}</footer>
  </section>;
}

export function AdvancedRouting({ value, onChange, connections = [], inherited = false, reset, defaults = {}, library = [], onEditSource }: {
  value: unknown; onChange: (value: string) => void; connections?: ProfileConnection[]; inherited?: boolean; reset?: () => void; defaults?: Record<string, string | number | boolean>;
  library?: RoutingLibraryItem[]; onEditSource?: (id: string) => void;
}) {
  const policy = parsePolicy(value);
  const update = (next: Policy) => onChange(JSON.stringify({ ...next, groups: next.groups.map(group => ({ ...group, id: group.id || group.name })) }));
  const targets = [["GATE.312", "Основная группа VPN"], ["DIRECT", "DIRECT · напрямую"], ["REJECT", "REJECT · запрет"], ...policy.groups.map(group => [group.name, group.name])];
  const patchRule = (index: number, patch: Partial<Rule>) => update({ ...policy, rules: policy.rules.map((rule, at) => at === index ? { ...rule, ...patch } : rule) });
  const patchGroup = (index: number, patch: Partial<Group>) => {
    const old = policy.groups[index].name;
    update({ rules: patch.name === undefined ? policy.rules : policy.rules.map(rule => rule.target === old ? { ...rule, target: patch.name! } : rule),
      groups: policy.groups.map((group, at) => ({ ...(at === index ? { ...group, id: group.id || old, ...patch } : group),
        members: (at === index ? patch.members ?? group.members : group.members).map(member => patch.name !== undefined && member === `group:${old}` ? `group:${patch.name}` : member) })) });
  };
  const groupUsed = (name: string) => policy.rules.some(rule => rule.target === name)
    || policy.groups.some(group => group.members.includes(`group:${name}`));
  const deleteGroup = (index: number) => {
    if (groupUsed(policy.groups[index].name)) return;
    update({ ...policy, groups: policy.groups.filter((_, at) => at !== index) });
  };
  const references = policy.rules.map((rule, index) => ({ rule, index })).filter(({ rule }) => rule.kind === "library");
  const standalone = policy.rules.map((rule, index) => ({ rule, index })).filter(({ rule }) => rule.kind !== "library");
  const available = library.filter(item => !references.some(({ rule }) => rule.value === item.id));
  return <section className="mihomoStructuredPolicy"><header><div><b>Маршруты правил</b></div><div className="mihomoPolicyHeaderActions">{inherited && <span className="mihomoPolicyInherited">Общая политика</span>}{reset && <button type="button" onClick={reset}>Наследовать</button>}</div></header>
    <p className="mihomoPolicyHint">Выберите набор из библиотеки и назначьте ему маршрут. Состав набора редактируется в библиотеке, включение — в профиле или устройстве.</p>
    <div className="mihomoPolicySectionHead"><h4>Наборы <span>{references.length}</span></h4><button type="button" disabled={!available.length || policy.rules.length >= 200} onClick={() => { if (available[0]) update({ ...policy, rules: [...policy.rules, { kind: "library", value: available[0].id, target: available[0].defaultTarget || "GATE.312" }] }); }}>Назначить маршрут</button></div>
    {!references.length && <p className="mihomoPolicyEmpty">Для существующих правил действуют их обычные маршруты.</p>}
    <div className="mihomoPolicyBindings">{references.map(({ rule, index }) => <article key={index} className="mihomoPolicyBinding">
      <label>Набор правил<select aria-label="Набор правил" value={rule.value} onChange={event => patchRule(index, { value: event.target.value })}>
        {!library.some(item => item.id === rule.value) && <option value={rule.value}>Недоступный набор: {rule.value}</option>}
        {[...new Set(library.map(item => item.group))].map(group => <optgroup label={group} key={group}>{library.filter(item => item.group === group && (item.id === rule.value || available.some(source => source.id === item.id))).map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</optgroup>)}
      </select></label>
      <label>Маршрут<select aria-label="Маршрут набора" value={rule.target} onChange={event => patchRule(index, { target: event.target.value })}>{!targets.some(([key]) => key === rule.target) && <option value={rule.target}>Недоступен: {rule.target}</option>}{targets.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>
      <div className="mihomoPolicyActions">{onEditSource && library.some(item => item.id === rule.value) && <button type="button" aria-label="Открыть набор в библиотеке" onClick={() => onEditSource(rule.value)}>Изменить набор</button>}<button type="button" aria-label="Убрать назначение маршрута" onClick={() => update({ ...policy, rules: policy.rules.filter((_, at) => at !== index) })}>×</button></div>
    </article>)}</div>
    <details className="mihomoPolicyStandalone"><summary>Отдельные условия <span>{standalone.length}</span></summary>
    <details className="mihomoPolicyInfo"><summary>Порядок и наследование</summary>
      <p className="mihomoPolicyHint">Проверяются сверху вниз до первого совпадения. Встроенные и персональные правила могут сработать раньше; полный порядок виден в «Итоговом порядке правил» после сохранения.</p>
      {reset && <p className="mihomoPolicyHint">{inherited ? "Используются унаследованные правила и группы. Первая правка создаст отдельный набор для выбранной конфигурации." : "Этот набор заменяет унаследованные правила и группы для выбранной конфигурации. «Наследовать» вернёт общий набор."} После сохранения обновите подписку в клиенте.</p>}
    </details>

    <div className="mihomoPolicySectionHead"><h4>Условия <span>{standalone.length} / 200</span></h4><button type="button" disabled={policy.rules.length >= 200} onClick={() => update({ ...policy, rules: [...policy.rules, { kind: "domain-suffix", value: "", target: "GATE.312" }] })}>Добавить условие</button></div>
    {standalone.map(({ rule, index }) => <article key={index} className="mihomoPolicyRule"><span className="mihomoPolicyIndex">{index + 1}</span><label className="mihomoPolicyRuleKind">Условие<select value={rule.kind} onChange={event => patchRule(index, { kind: event.target.value, value: ["geoip", "geosite"].includes(event.target.value) ? "cn" : "" })}>{kinds.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>
      <label className="mihomoPolicyRuleValue">{rule.kind === "ip-cidr" ? "IP-адрес или подсеть" : rule.kind === "process" ? "Имя процесса · TUN на ПК" : ["geoip", "geosite"].includes(rule.kind) ? "Набор из каталога" : "Домен без https:// и пути"}{["geoip", "geosite"].includes(rule.kind) ? <select value={rule.value} onChange={event => patchRule(index, { value: event.target.value })}>{(rule.kind === "geoip" ? ["cn", "ru"] : ["cn", "youtube", "telegram", "github"]).map(name => <option key={name}>{name}</option>)}</select> : <input value={rule.value} required maxLength={rule.kind === "process" ? 160 : 253} placeholder={rule.kind === "ip-cidr" ? "203.0.113.0/24" : rule.kind === "process" ? "browser.exe" : "example.com"} onChange={event => patchRule(index, { value: event.target.value })}/>}</label>
      <label className="mihomoPolicyRuleTarget">Маршрут<select value={rule.target} onChange={event => patchRule(index, { target: event.target.value })}>{!targets.some(([key]) => key === rule.target) && <option value={rule.target}>Недоступен: {rule.target}</option>}{targets.map(([key, text], at) => <option key={`${key}:${at}`} value={key}>{text}</option>)}</select></label>
      <div className="mihomoPolicyActions"><button type="button" aria-label="Поднять правило" disabled={!index} onClick={() => update({ ...policy, rules: move(policy.rules, index, -1) })}>↑</button><button type="button" aria-label="Опустить правило" disabled={index === policy.rules.length - 1} onClick={() => update({ ...policy, rules: move(policy.rules, index, 1) })}>↓</button><button type="button" aria-label="Удалить правило" onClick={() => update({ ...policy, rules: policy.rules.filter((_, at) => at !== index) })}>×</button></div>
    </article>)}
    </details>

    <div className="mihomoPolicyGroups">
    <div className="mihomoPolicySectionHead"><h4>Группы каналов <span>{policy.groups.length} / 20</span></h4><button type="button" disabled={policy.groups.length >= 20} onClick={() => { let number = policy.groups.length + 1; while (policy.groups.some(group => group.name === `Group${number}`)) number++; update({ ...policy, groups: [...policy.groups, { id: crypto.randomUUID(), name: `Group${number}`, type: "fallback", members: ["*"], strategy: "consistent-hashing" }] }); }}>Добавить группу</button></div>
    {!policy.groups.length && <p className="mihomoPolicyEmpty">Для маршрутов «Основная группа VPN», «Напрямую» и «Запретить» создавать группу не нужно.</p>}
    {policy.groups.map((group, index) => {
      const members = [["*", "Все подключения устройства"], ...connections.map(connection => [`connection:${connection.id}`, connection.name || connection.component]), ...policy.groups.filter((_, at) => at !== index).map(other => [`group:${other.name}`, `Группа: ${other.name}`])];
      return <details key={index} className="mihomoPolicyGroup">
        <summary><span className="mihomoPolicyGroupName"><b>{group.name || "Без названия"}</b><small>{groupTypes.find(([key]) => key === group.type)?.[1] || group.type}</small></span><span className="mihomoPolicyGroupMeta">{group.members.includes("*") ? "Все подключения" : `Участники: ${group.members.length}`} · Правила: {policy.rules.filter(rule => rule.target === group.name).length}</span><i aria-hidden="true"/></summary>
        <div className="mihomoPolicyGroupBody"><div className="mihomoPolicyFields"><label>Имя группы<input value={group.name} maxLength={40} pattern={"[A-Za-z][A-Za-z0-9_\\-]{0,39}"} title="Латинская буква, затем латиница, цифры, дефис или подчёркивание" required placeholder="Media" onChange={event => patchGroup(index, { name: event.target.value })}/></label><label>Выбор канала<select value={group.type} onChange={event => patchGroup(index, { type: event.target.value })}>{groupTypes.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>{group.type === "load-balance" && <BalanceStrategy value={group.strategy} onChange={strategy => patchGroup(index, { strategy })}/>}<button type="button" disabled={groupUsed(group.name)} title={groupUsed(group.name) ? "Сначала смените маршрут в правилах и уберите ссылки из других групп" : undefined} onClick={() => deleteGroup(index)}>Удалить группу</button></div>
        <p className="mihomoPolicyHint">{groupHints[group.type]}</p>
        {group.type !== "select" && <details className="mihomoPolicyMemberPicker"><summary>Проверка каналов · параметры группы</summary><div className="mihomoPolicyHealth">
          <p className="mihomoPolicyHint">Пустые поля наследуют общие настройки. Порог ошибок запускает проверку работоспособности, а не задаёт число повторных попыток.</p>
          <div className="mihomoPolicyFields"><label>URL проверки<input type="url" value={group.url ?? ""} placeholder={String(defaults.test_url || "https://www.gstatic.com/generate_204")} onChange={event => patchGroup(index, { url: event.target.value || undefined })}/></label>
          {healthFields.filter(field => field.key !== 'tolerance' || group.type === 'url-test').map(({ key, label, min, max, shared, fallback }) => <label key={key}>{label}<input type="number" min={min} max={max} step={1} value={group[key] ?? ""} placeholder={String(defaults[shared] ?? fallback)} onChange={event => patchGroup(index, { [key]: event.target.value === "" ? undefined : Number(event.target.value) })}/></label>)}</div>
          <button type="button" onClick={() => patchGroup(index, { url: undefined, interval: undefined, timeout: undefined, max_failed_times: undefined, tolerance: undefined })}>Наследовать общие параметры проверки</button>
        </div></details>}
        {groupUsed(group.name) && <p className="mihomoPolicyHint">Группа используется в правилах или других группах. Для удаления сначала уберите ссылки на неё.</p>}
        <details className="mihomoPolicyMemberPicker"><summary>Выбрать подключения · {group.members.length}</summary><div className="mihomoPolicyMembers">{members.map(([key, text]) => <label key={key}><input type="checkbox" role="switch" checked={group.members.includes(key)} disabled={group.members.length === 1 && group.members.includes(key)} onChange={event => patchGroup(index, { members: event.target.checked ? [...group.members, key] : group.members.filter(member => member !== key) })}/>{text}</label>)}</div></details>
        <ol className="mihomoPolicyOrder">{group.members.map((member, at) => <li key={member}><span>{members.find(([key]) => key === member)?.[1] || `Недоступен: ${member}`}</span><button type="button" aria-label="Повысить приоритет участника" disabled={!at} onClick={() => patchGroup(index, { members: move(group.members, at, -1) })}>↑</button><button type="button" aria-label="Понизить приоритет участника" disabled={at === group.members.length - 1} onClick={() => patchGroup(index, { members: move(group.members, at, 1) })}>↓</button><button type="button" aria-label="Убрать участника" disabled={group.members.length === 1} onClick={() => patchGroup(index, { members: group.members.filter((_, row) => row !== at) })}>×</button></li>)}</ol>
      </div></details>;
    })}
    </div>

  </section>;
}
