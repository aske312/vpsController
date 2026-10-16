import type { ProtocolImage } from "../../shared/types/control-plane";
import { ProtocolIcon } from "../../shared/components/protocol-icon";
import { componentInstalledVersion, formatModuleVersion } from "../../shared/lib/format-version";
import { componentPresentation } from "./component-state";

type Props = {
  image: ProtocolImage;
  healthReason?: string;
  coreVersion?: string;
  port?: number;
  busy: boolean;
  installingProtocol: string;
  onInstall: (image: ProtocolImage) => void;
  onUpdate: (image: ProtocolImage) => void;
  onOpen: (image: ProtocolImage) => void;
  onAdopt: (image: ProtocolImage) => void;
  onPurge: (image: ProtocolImage) => void;
  onRemove: (image: ProtocolImage) => void;
};

export function NodeComponentCard({ image, healthReason, coreVersion, port, busy, installingProtocol, onInstall, onUpdate, onOpen, onAdopt, onPurge, onRemove }: Props) {
  const state = componentPresentation(image, healthReason);
  const observed = image.component_state;
  const versionLabel = componentInstalledVersion(image, coreVersion);
  const attention = ["warning", "error"].includes(state.summary.tone);

  return <article className={`nodeComponentCard protocol-${image.id}`} aria-label={image.name}>
    <header>
      <button type="button" className="nodeComponentCardOpen" disabled={!image.installed || image.id === "mihomo"} onClick={() => onOpen(image)} aria-label={`Открыть ${image.name}`}>
        <span className="nodeComponentCardLogo"><ProtocolIcon protocol={image.id} /></span>
        <span className="nodeComponentCardIdentity"><strong>{image.name}</strong><small>{image.category_name || image.category || "Без категории"}</small></span>
      </button>
      <em className="nodeComponentCardState" data-tone={state.badge.tone} aria-label={`Состояние ${image.name}: ${state.badge.label}`} title={`${state.summary.description}\nУстановка: ${state.installation}\nСлужба: ${state.runtime}\nПроверка: ${state.health}${image.management?.reason ? `\n${image.management.reason}` : ""}`}>{state.badge.label}</em>
    </header>
    <dl className="nodeComponentCardFacts">
      <div><dt>Версия</dt><dd className="nodeComponentCardVersion">{versionLabel}{(image.update_available || image.update_via_release) && <small title="Доступна новая версия"> → {image.available_version ? formatModuleVersion(image.available_version) : "Обновление"}</small>}</dd></div>
      {image.id !== "mihomo" && <div><dt>Порт</dt><dd>{port || "—"}</dd></div>}
    </dl>
    {attention && <p className="nodeComponentCardIssue">{state.summary.description}</p>}
      <footer className="nodeComponentCardActions">
          {image.management?.retained && <button type="button" title="Очистить сохранённые данные" disabled={busy || Boolean(installingProtocol) || Boolean(observed?.operation)} onClick={() => onPurge(image)}>Очистить</button>}
          {image.installed && image.management && image.management.state !== "managed" ? (
            <button type="button" disabled={busy || !state.canAdopt} onClick={() => onAdopt(image)}>Принять</button>
          ) : !image.installed ? (
            <button type="button" disabled={!state.canInstall || busy || Boolean(installingProtocol)} onClick={() => onInstall(image)}>{!image.installable ? "Недоступен" : installingProtocol === image.id ? "Установка…" : "Установить"}</button>
          ) : image.update_available && image.id !== "mihomo" ? (
            <button type="button" aria-label={`Обновить ${image.name}`} title="Открыть компонент: доступно обновление" onClick={() => onOpen(image)}>Обновить</button>
          ) : image.update_available ? (
            <button type="button" aria-label={`Обновить ${image.name}`} title="Обновить компонент" data-tone={image.update_breaking ? "warning" : undefined} disabled={busy || !state.canUpdate || Boolean(installingProtocol)} onClick={() => onUpdate(image)}>{installingProtocol === `update-${image.id}` ? "Обновление…" : "Обновить"}</button>
          ) : image.id !== "mihomo" && attention ? <button type="button" onClick={() => onOpen(image)}>Проверить</button> : null}
          {image.installed && image.removable && <button type="button" data-tone="danger" aria-label={`Удалить ${image.name}`} title="Удалить компонент с сохранением настроек" disabled={busy || Boolean(installingProtocol) || !state.canRemove} onClick={() => onRemove(image)}>{installingProtocol === `remove-${image.id}` ? "Удаление…" : "Удалить"}</button>}
      </footer>
  </article>;
}
