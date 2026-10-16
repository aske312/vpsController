import type { ProtocolImage } from "../../shared/types/control-plane";
import { ProtocolIcon } from "../../shared/components/protocol-icon";
import { componentInstalledVersion, formatModuleVersion } from "../../shared/lib/format-version";
import { componentPresentation } from "./component-state";

type Props = {
  image: ProtocolImage;
  healthReason?: string;
  coreVersion?: string;
  busy: boolean;
  installingProtocol: string;
  onInstall: (image: ProtocolImage) => void;
  onUpdate: (image: ProtocolImage) => void;
  onOpen: (image: ProtocolImage) => void;
  onAdopt: (image: ProtocolImage) => void;
  onPurge: (image: ProtocolImage) => void;
};

export function NodeComponentCard({ image, healthReason, coreVersion, busy, installingProtocol, onInstall, onUpdate, onOpen, onAdopt, onPurge }: Props) {
  const state = componentPresentation(image, healthReason);
  const observed = image.component_state;
  const versionLabel = componentInstalledVersion(image, coreVersion);
  const attention = ["warning", "error"].includes(state.summary.tone);

  return <article className={`nodeComponentCard protocol-${image.id}`} aria-label={image.name}>
    <div className="nodeComponentCardLogo"><ProtocolIcon protocol={image.id} /></div>
    <div className="nodeComponentCardContent">
      <h3>{image.name}</h3>
      <div className="nodeComponentCardStatus" data-tone={state.summary.tone}>
        <div className="nodeComponentCardTags">
          <span title={`Установка: ${state.installation}`} aria-label={`Установка: ${state.installation}`}>{observed?.installation.state === "installed" ? "УСТ" : observed?.installation.state === "not_installed" ? "НЕТ" : observed?.installation.state === "incomplete" ? "НЕПОЛН" : "УСТ ?"}</span>
          <span title={`Служба: ${state.runtime}`} aria-label={`Служба: ${state.runtime}`}>{observed?.runtime.state === "running" ? "RUN" : observed?.runtime.state === "stopped" ? "STOP" : observed?.runtime.state === "error" ? "ERR" : "RUN ?"}</span>
          <span title={`Проверка: ${state.health}. ${state.summary.description}`} aria-label={`Проверка: ${state.health}`}>{observed?.health.state === "healthy" ? "OK" : observed?.health.state === "possible_issues" ? "WARN" : observed?.health.state === "error" ? "ERR" : observed?.health.state === "unchecked" ? "НЕ ПРОВ" : "CHECK ?"}</span>
          {state.operation && <span>{state.operation}</span>}
          {image.management?.retained && <span title="Настройки и подключения сохранены">ДАННЫЕ</span>}
          {image.installed && state.readOnly && <span title={image.management?.reason}>READ ONLY</span>}
        </div>
        {attention && <p>{state.summary.description}</p>}
      </div>
      <footer>
        <div className="nodeComponentCardVersion">
          <span title={`Установленная версия: ${versionLabel}`}>{versionLabel}</span>
          {(image.update_available || image.update_via_release) && <small title="Доступна новая версия">↑ {image.available_version ? formatModuleVersion(image.available_version) : "Обновление"}</small>}
        </div>
        <div className="nodeComponentCardActions">
          {image.management?.retained && <button type="button" title="Очистить сохранённые данные" disabled={busy || Boolean(installingProtocol) || Boolean(observed?.operation)} onClick={() => onPurge(image)}>Очистить</button>}
          {image.installed && image.management && image.management.state !== "managed" ? (
            <button type="button" disabled={busy || !state.canAdopt} onClick={() => onAdopt(image)}>Принять</button>
          ) : !image.installed ? (
            <button type="button" disabled={!state.canInstall || busy || Boolean(installingProtocol)} onClick={() => onInstall(image)}>{!image.installable ? "Недоступен" : installingProtocol === image.id ? "Установка…" : "Установить"}</button>
          ) : image.update_available && image.id !== "mihomo" ? (
            <button type="button" title="Открыть компонент: доступно обновление" onClick={() => onOpen(image)}>Обновление</button>
          ) : image.update_available ? (
            <button type="button" data-tone={image.update_breaking ? "warning" : undefined} disabled={busy || !state.canUpdate || Boolean(installingProtocol)} onClick={() => onUpdate(image)}>{installingProtocol === `update-${image.id}` ? "Обновление…" : "Обновить"}</button>
          ) : image.id !== "mihomo" && attention ? <button type="button" onClick={() => onOpen(image)}>Проверить</button> : null}
        </div>
      </footer>
    </div>
  </article>;
}
