"use client";

import { formatModuleVersion } from "../../shared/lib/format-version";
import type { View, Module } from "./types";
import { channelShort, moduleCapabilities } from "./catalog";

export function Tab({ id, current, onSelect, badge, children }: { id: View; current: View; onSelect: (id: View) => void; badge?: number; children: React.ReactNode }) {
  return <button type="button" className={current === id ? "active" : ""} aria-current={current === id ? "page" : undefined} onClick={() => onSelect(id)}><b>{children}</b>{badge !== undefined && <em>{badge}</em>}</button>;
}

export function HeroFact({ label, value, note, wide = false }: { label: string; value: string; note: string; wide?: boolean }) {
  return <div className={wide ? "wide" : ""}><small>{label}</small><strong>{value}</strong><span>{note}</span></div>;
}

export function ModuleCatalog({ title, description, modules, busy, onToggle, onUpdate, onSettings }: { title: string; description: string; modules: Module[]; busy: string; onToggle: (module: Module) => void; onUpdate: (module: Module) => void; onSettings: (module: Module) => void }) {
  return (
    <article className="mihomoWorkspacePanel mihomoCatalogPanel">
      <header className="mihomoSectionHead">
        <div><p className="eyebrow">MIHOMO SUB-MODULES</p><h2>{title}</h2><p>{description}</p></div>
        <span className="mihomoCatalogCount">{modules.filter((item) => item.installed).length} / {modules.length} установлено</span>
      </header>
      <div className="mihomoModuleCatalog mihomoModuleCatalogV2">
        {modules.map((module) => (
          <article key={module.id} className={module.installed ? "is-installed" : ""}>
            <header><span className={`mihomoModuleCode protocol-${module.id}`}>{channelShort[module.id] || (module.category === "dns" ? "DNS" : "RT")}</span><div><b>{module.name}</b><small>{module.description}</small></div><i className={module.active ? "is-online" : module.installed ? "is-ready" : ""} /></header>
            <div className="mihomoModuleCapabilities">{(moduleCapabilities[module.id] || []).map((capability) => <span key={capability}>{capability}</span>)}</div>
            <dl><div><dt>Состояние</dt><dd>{module.active ? "Работает" : module.installed ? "Готов" : "Не установлен"}</dd></div><div><dt>Версия</dt><dd>{module.installed_version ? formatModuleVersion(module.installed_version) : "—"}</dd></div><div><dt>Сервис</dt><dd>{module.service || "Внутренний"}</dd></div></dl>
            <footer className="mihomoModuleActions">
              <button className="ghostButton" onClick={() => onSettings(module)}>Настройки</button>
              {module.installed && module.update_available && (
                <button className={`ghostButton${module.update_breaking ? " breaking" : ""}`} disabled={Boolean(busy)} onClick={() => void onUpdate(module)}>{busy === `update:${module.id}` ? "Обновление…" : "Обновить"}</button>
              )}
              <button className={module.installed ? "dangerButton" : "primaryButton"} disabled={module.installable === false || Boolean(busy)} onClick={() => void onToggle(module)}>{module.installable === false ? "В разработке" : busy === module.id ? "Выполняется…" : module.installed ? "Удалить" : "Установить"}</button>
            </footer>
          </article>
        ))}
        {!modules.length && <Empty title="Каталог пуст" text="Mihomo Manager не получил manifest внутренних модулей." />}
      </div>
    </article>
  );
}

export function Empty({ title, text }: { title: string; text: string }) {
  return <div className="mihomoEmpty"><p><b>{title}</b><small>{text}</small></p></div>;
}
