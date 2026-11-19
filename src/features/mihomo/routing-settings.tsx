import { useState } from "react";
import { BalanceStrategy } from "./advanced-routing";
import { matchesPresetComponent, presetOptionGroups, profileStrategies } from "./catalog";
import type { ProfilePreset } from "./types";

type RoutingValues = Record<string, string | number | boolean>;
type UpdateRouting = (key: string, value: string | number | boolean) => void;

const strategies = profileStrategies.filter(({ value }) => value);
const strategyDescriptions: Record<string, string> = {
  fallback: "Первый доступный по списку",
  "url-test": "Канал с меньшей задержкой",
  select: "Выбор канала в клиенте",
  "load-balance": "Распределение соединений",
};
const presetStrategies = strategies.filter(({ value }) => value !== "load-balance");

function RoutingNumberField({ label, value, min, max, onChange }: {
  label: string;
  value: string | number;
  min: number;
  max: number;
  onChange: (value: number | "") => void;
}) {
  return <label className="mihomoSettingsField">{label}
    <input type="number" required min={min} max={max} value={value}
      onChange={(event) => onChange(event.target.value === "" ? "" : event.target.valueAsNumber)} />
  </label>;
}

export function RoutingSettings({ draft, presets, disabled, onChange, onEditPreset }: {
  draft: RoutingValues;
  presets: ProfilePreset[];
  disabled: boolean;
  onChange: UpdateRouting;
  onEditPreset: (id?: string) => void;
}) {
  const strategy = String(draft.strategy || "fallback");
  const tolerance = <RoutingNumberField label={strategy === "url-test" ? "Допуск переключения, мс" : "Допуск автовыбора, мс"}
    value={String(draft.tolerance ?? 0)} min={0} max={1000} onChange={(value) => onChange("tolerance", value)} />;
  return <div className="mihomoSettingsLayout">
    <fieldset className="mihomoSettingsRouting" disabled={disabled} aria-label="Общие настройки маршрутизации">
      <div className="mihomoSettingsMode">
        <label className="mihomoSettingsField">Режим трафика
          <select value={String(draft.mode || "rule")} onChange={(event) => onChange("mode", event.target.value)}>
            <option value="rule">По правилам</option>
            <option value="global">Весь трафик через VPN</option>
          </select>
        </label>
      </div>
      <fieldset className="mihomoSettingsStrategy">
        <legend>Стратегия по умолчанию</legend>
        <p>Используется, когда профиль или устройство наследует общие настройки.</p>
        <div>
          {strategies.map(({ value, title }) => <label key={value}>
            <input type="radio" name="mihomo-routing-strategy" value={value} checked={strategy === value}
              aria-label={title} aria-describedby={`mihomo-strategy-${value}`} onChange={() => onChange("strategy", value)} />
            <span><b>{title}</b><small id={`mihomo-strategy-${value}`}>{strategyDescriptions[value]}</small></span>
          </label>)}
        </div>
      </fieldset>
      {strategy === "url-test" && <div className="mihomoSettingsStrategyOptions">
        {tolerance}
        <p>Пока разница в задержке укладывается в допуск, Mihomo сохраняет текущий доступный канал.</p>
      </div>}
      {strategy === "load-balance" && <div className="mihomoSettingsStrategyOptions">
        <BalanceStrategy value={draft.load_balance_strategy} onChange={(value) => onChange("load_balance_strategy", value)} />
      </div>}
      <details className="mihomoSettingsHealth" onInvalidCapture={(event) => { event.currentTarget.open = true; }}>
        <summary>Проверка доступности <small>Общие параметры для автоматических стратегий</small></summary>
        <div className="mihomoSettingsHealthFields">
          <label className="mihomoSettingsField mihomoSettingsHealthUrl">Адрес проверки
            <input value={String(draft.test_url ?? "")} onChange={(event) => onChange("test_url", event.target.value)} />
          </label>
          <RoutingNumberField label="Интервал, сек." value={String(draft.interval ?? 30)} min={10} max={3600}
            onChange={(value) => onChange("interval", value)} />
          <RoutingNumberField label="Таймаут, мс" value={String(draft.health_timeout ?? 3000)} min={1000} max={10000}
            onChange={(value) => onChange("health_timeout", value)} />
          <RoutingNumberField label="Ошибок до перепроверки" value={String(draft.max_failed_times ?? 2)} min={1} max={10}
            onChange={(value) => onChange("max_failed_times", value)} />
          {strategy !== "url-test" && tolerance}
        </div>
      </details>
    </fieldset>

    <section className="mihomoSettingsPresets">
      <header>
        <h3>Шаблоны профилей</h3>
        <p>Задают каналы и стратегию при создании профиля.</p>
      </header>
      {presets.length > 0 && <ul>
        {presets.map((preset) => <li key={preset.id}>
          <button type="button" disabled={disabled} onClick={() => onEditPreset(preset.id)}>
            <span><b>{preset.name}</b><small>{presetStrategies.find(({ value }) => value === preset.strategy)?.title} · Каналов: {preset.components.length}</small></span>
            <span aria-hidden="true">›</span>
          </button>
        </li>)}
      </ul>}
      <button type="button" className="ghostButton" disabled={disabled} onClick={() => onEditPreset()}>Редактировать шаблоны</button>
    </section>
  </div>;
}

export function PresetEditor({ presets, activeIndex, onSelect, onAdd, onDelete, onChange, onToggleChannel }: {
  presets: ProfilePreset[];
  activeIndex: number;
  onSelect: (index: number) => void;
  onAdd: () => void;
  onDelete: (index: number) => void;
  onChange: (changes: Partial<Pick<ProfilePreset, "name" | "description" | "strategy">>) => void;
  onToggleChannel: (index: number, option: ProfilePreset["components"][number], checked: boolean) => void;
}) {
  const preset = presets[activeIndex];
  return <div className="mihomoPresetEditor">
    <div className="mihomoPresetToolbar">
      <label className="mihomoSettingsField">Редактируемый шаблон
        <select value={preset?.id ?? ""} disabled={!preset} onChange={(event) => onSelect(presets.findIndex((item) => item.id === event.target.value))}>
          {!preset && <option value="">Шаблонов пока нет</option>}
          {presets.map((item) => <option key={item.id} value={item.id}>{item.name.trim() || "Без названия"}</option>)}
        </select>
      </label>
      <button type="button" className="ghostButton" onClick={onAdd} disabled={presets.length >= 12}>Добавить шаблон</button>
    </div>
    {preset && <article className="mihomoPresetContent" key={preset.id}>
      <div className="mihomoPresetFields">
        <label className="mihomoSettingsField">Название
          <input value={preset.name} onChange={(event) => onChange({ name: event.target.value })} />
        </label>
        <label className="mihomoSettingsField">Стратегия
          <select value={preset.strategy} onChange={(event) => onChange({ strategy: event.target.value as ProfilePreset["strategy"] })}>
            {presetStrategies.map(({ value, title }) => <option key={value} value={value}>{title}</option>)}
          </select>
        </label>
        <label className="mihomoSettingsField mihomoPresetDescription">Описание
          <input value={preset.description} onChange={(event) => onChange({ description: event.target.value })} />
        </label>
      </div>
      <PresetChannels components={preset.components} onToggle={(option, checked) => onToggleChannel(activeIndex, option, checked)} />
      <button type="button" className="dangerButton mihomoPresetDelete" disabled={presets.length <= 1}
        title={presets.length <= 1 ? "Нужен хотя бы один шаблон" : undefined} onClick={() => onDelete(activeIndex)}>Удалить шаблон</button>
    </article>}
  </div>;
}

function PresetChannels({ components, onToggle }: {
  components: ProfilePreset["components"];
  onToggle: (option: ProfilePreset["components"][number], checked: boolean) => void;
}) {
  const groups = presetOptionGroups.map((group) => ({
    ...group,
    options: group.options.map((option) => ({ option, checked: components.some((item) => matchesPresetComponent(item, option)) })),
  }));
  const [groupId, setGroupId] = useState(() => groups.find(({ options }) => options.some(({ checked }) => checked))?.id ?? groups[0].id);
  const active = groups.find(({ id }) => id === groupId) ?? groups[0];
  return <section className="mihomoPresetChannels">
    <header><h3>Каналы</h3><span>Выбрано: {components.length}</span></header>
    <div className="mihomoPresetGroups" role="group" aria-label="Тип канала">
      {groups.map(({ id, title, options }) => <button key={id} type="button" aria-pressed={id === active.id} onClick={() => setGroupId(id)}>
        {title}<span aria-label="Выбрано в группе">{options.filter(({ checked }) => checked).length}</span>
      </button>)}
    </div>
    <div className="mihomoPresetChannelOptions" role="group" aria-label={active.title}>
      {active.options.map(({ option, checked }) => <label key={`${option.id}-${option.xray_protocol || "vless"}-${option.transport || "default"}`}>
        <input type="checkbox" checked={checked} onChange={(event) => onToggle(option, event.target.checked)} />
        <span>{option.label}</span>
      </label>)}
    </div>
  </section>;
}
