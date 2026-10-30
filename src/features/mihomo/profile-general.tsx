import { deviceSystemLabel } from "./profile-utils";
import { SubscriptionLimitsFields, SubscriptionUsage, type SubscriptionTraffic } from "./subscription-limits";
import type { ProfileDevice, SubscriptionLimits, SubscriptionLimitsStatus } from "./types";

type Props = {
  device?: ProfileDevice;
  profileName: string;
  limits: SubscriptionLimits;
  status?: SubscriptionLimitsStatus;
  traffic?: SubscriptionTraffic;
  disabled: boolean;
  onProfileNameChange: (name: string) => void;
  onDeviceNameChange: (name: string) => void;
  onLimitsChange: (limits: SubscriptionLimits) => void;
  onOpenSubscription?: () => void;
};

export function ProfileGeneral({ device, profileName, limits, status, traffic, disabled, onProfileNameChange, onDeviceNameChange, onLimitsChange, onOpenSubscription }: Props) {
  if (!device) return <section className="mihomoProfileName mihomoProfileGeneral"><p role="status">Выберите конфигурацию.</p></section>;
  const common = device.scope === "common";
  const binding = device.manual || device.scope === "manual" ? "Без HWID" : device.scope === "hwid" || device.hwid_hash ? "HWID" : "Не определена";
  const client = [device.client_name, device.client_version].filter(Boolean).join(" ") || "Не определено";
  const core = [device.core_name, device.core_version].filter(Boolean).join(" ") || "Не определено";
  return <section className="mihomoProfileName mihomoProfileGeneral" aria-label={common ? "Общие настройки подписки" : "Общие настройки устройства"}>
    <header className="mihomoGeneralHeading"><b>{common ? "Подписка" : "Устройство"}</b>{!common && <span>{binding}</span>}</header>
    <label className="mihomoGeneralIdentity"><span>Название</span><input value={common ? profileName : device.name} required maxLength={80} disabled={disabled} onChange={event => common ? onProfileNameChange(event.target.value) : onDeviceNameChange(event.target.value)} /></label>
    {common ? <SubscriptionLimitsFields value={limits} status={status} traffic={traffic} disabled={disabled} onChange={onLimitsChange} /> : <>
      <dl className="mihomoGeneralDeviceFacts">
        <div><dt>Система</dt><dd>{deviceSystemLabel(device)}</dd></div>
        <div><dt>Приложение</dt><dd>{client}</dd></div>
        <div><dt>Ядро</dt><dd>{core}</dd></div>
      </dl>
      <section className="mihomoGeneralSharedLimits" aria-label="Лимиты общей подписки">
        <header><b>Лимиты подписки</b>{onOpenSubscription && <button type="button" disabled={disabled} onClick={onOpenSubscription}>Изменить в подписке</button>}</header>
        <dl>
          <div><dt>Скорость</dt><dd>{`${limits.speed_mbps ?? 0} Мбит/с`}</dd></div>
          <div><dt>Трафик</dt><dd>{`${limits.traffic_gb ?? 0} GB`}</dd></div>
          <div><dt>Срок</dt><dd>{`${limits.days ?? 0} дней`}</dd></div>
        </dl>
      </section>
      <SubscriptionUsage status={status} traffic={traffic} />
    </>}
  </section>;
}
