import { deviceSystemLabel } from "./profile-utils";
import { SubscriptionLimitsFields, SubscriptionUsage } from "./subscription-limits";
import type { ProfileDevice, SubscriptionLimits, SubscriptionLimitsStatus } from "./types";

type Props = {
  device?: ProfileDevice;
  profileName: string;
  limits: SubscriptionLimits;
  status?: SubscriptionLimitsStatus;
  disabled: boolean;
  onProfileNameChange: (name: string) => void;
  onDeviceNameChange: (name: string) => void;
  onLimitsChange: (limits: SubscriptionLimits) => void;
  onOpenSubscription?: () => void;
};

export function ProfileGeneral({ device, profileName, limits, status, disabled, onProfileNameChange, onDeviceNameChange, onLimitsChange, onOpenSubscription }: Props) {
  if (!device) return <section className="mihomoProfileName mihomoProfileGeneral"><p role="status">Выберите конфигурацию.</p></section>;
  const common = device.scope === "common";
  const binding = device.manual || device.scope === "manual" ? "Без HWID" : device.scope === "hwid" || device.hwid_hash ? "HWID" : "Не определена";
  const client = [device.client_name, device.client_version].filter(Boolean).join(" ") || "Не определено";
  const core = [device.core_name, device.core_version].filter(Boolean).join(" ") || "Не определено";
  return <section className="mihomoProfileName mihomoProfileGeneral" aria-label={common ? "Общие настройки подписки" : "Общие настройки устройства"}>
    <header className="mihomoGeneralHeading"><b>{common ? "Подписка" : "Устройство"}</b><span>{common ? "Для всех устройств" : binding}</span></header>
    <label className="mihomoGeneralIdentity"><span>{common ? "Название подписки" : "Название устройства"}</span><input value={common ? profileName : device.name} required maxLength={80} disabled={disabled} placeholder={common ? "Например, Основная" : "Например, Ноутбук"} onChange={event => common ? onProfileNameChange(event.target.value) : onDeviceNameChange(event.target.value)} /></label>
    {common ? <SubscriptionLimitsFields value={limits} status={status} disabled={disabled} onChange={onLimitsChange} /> : <>
      <dl className="mihomoGeneralDeviceFacts">
        <div><dt>Система</dt><dd>{deviceSystemLabel(device)}</dd></div>
        <div><dt>Приложение</dt><dd>{client}</dd></div>
        <div><dt>Ядро</dt><dd>{core}</dd></div>
      </dl>
      <section className="mihomoGeneralSharedLimits" aria-label="Лимиты общей подписки">
        <header><b>Лимиты подписки</b>{onOpenSubscription && <button type="button" disabled={disabled} onClick={onOpenSubscription}>Изменить в подписке</button>}</header>
        <dl>
          <div><dt>Скорость</dt><dd>{limits.speed_mbps === null ? "Без ограничений" : `${limits.speed_mbps} Мбит/с`}</dd></div>
          <div><dt>Трафик</dt><dd>{limits.traffic_gb === null ? "Без ограничений" : `${limits.traffic_gb} GB`}</dd></div>
          <div><dt>Срок</dt><dd>{limits.days === null ? "Без ограничений" : `${limits.days} дней`}</dd></div>
        </dl>
        <small>Общие для всей подписки, включая это устройство.</small>
        <SubscriptionUsage status={status} />
      </section>
    </>}
  </section>;
}
