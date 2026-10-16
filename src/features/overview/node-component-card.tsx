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
  const runtime = !image.installed ? observed?.installation.state === "not_installed" ? image.installable ? "AVAILABLE" : "UNAVAILABLE" : "UNKNOWN" : observed?.runtime.state === "running" ? "ACTIVE" : observed?.runtime.state === "stopped" ? "STOPPED" : observed?.runtime.state === "error" ? "ERROR" : "UNKNOWN";

  return <article className={`nodeComponentCard protocol-${image.id}`} aria-label={image.name}>
    <header>
      <button type="button" className="nodeComponentCardOpen" disabled={!image.installed || image.id === "mihomo"} onClick={() => onOpen(image)} aria-label={`Открыть ${image.name}`}>
        <span className="nodeComponentCardLogo"><ProtocolIcon protocol={image.id} /></span>
        <strong>{image.name}</strong>
      </button>
      <em data-state={runtime.toLowerCase()}>{runtime}</em>
    </header>
    <dl className="nodeComponentCardFacts">
      <div><dt>Версия</dt><dd className="nodeComponentCardVersion">{versionLabel}{(image.update_available || image.update_via_release) && <small title="Доступна новая версия"> → {image.available_version ? formatModuleVersion(image.available_version) : "Обновление"}</small>}</dd></div>
      {image.id !== "mihomo" && <div><dt>Порт</dt><dd>{port || "—"}</dd></div>}
    </dl>
      <div className="nodeComponentCardStatus" data-tone={state.summary.tone}>
        <div className="nodeComponentCardTags">
          {observed?.installation.state !== "installed" && <span title={`Установка: ${state.installation}`} aria-label={`Установка: ${state.installation}`}>{observed?.installation.state === "not_installed" ? "НЕТ" : observed?.installation.state === "incomplete" ? "НЕПОЛН" : "УСТ ?"}</span>}
          <span title={`Служба: ${state.runtime}`} aria-label={`Служба: ${state.runtime}`}>{observed?.runtime.state === "running" ? "RUN" : observed?.runtime.state === "stopped" ? "STOP" : observed?.runtime.state === "error" ? "ERR" : "RUN ?"}</span>
          <span title={`Проверка: ${state.health}. ${state.summary.description}`} aria-label={`Проверка: ${state.health}`}>{observed?.health.state === "healthy" ? "OK" : observed?.health.state === "possible_issues" ? "WARN" : observed?.health.state === "error" ? "ERR" : observed?.health.state === "unchecked" ? "НЕ ПРОВ" : "CHECK ?"}</span>
          {state.operation && <span>{state.operation}</span>}
          {image.management?.retained && <span title="Настройки и подключения сохранены">ДАННЫЕ</span>}
          {image.installed && state.readOnly && <span title={image.management?.reason}>READ ONLY</span>}
        </div>
        {attention && <p>{state.summary.description}</p>}
      </div>
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
