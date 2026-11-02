import { deviceSystemLabel } from "./profile-utils";
import { SubscriptionLimitsFields, SubscriptionUsage, type SubscriptionTraffic } from "./subscription-limits";
import type { ProfileDevice, SubscriptionLimits, SubscriptionLimitsStatus } from "./types";
import styles from "./profile-general.module.css";

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
  if (!device) return <section className={`mihomoProfileName ${styles.root}`}><p className={styles.empty} role="status">Выберите конфигурацию.</p></section>;
  const common = device.scope === "common";
  const binding = device.manual || device.scope === "manual" ? "Без HWID" : device.scope === "hwid" || device.hwid_hash ? "HWID" : "Не определена";
  const client = [device.client_name, device.client_version].filter(Boolean).join(" ") || "Не определено";
  const core = [device.core_name, device.core_version].filter(Boolean).join(" ") || "Не определено";
  return <section className={`mihomoProfileName ${styles.root}`} aria-label={common ? "Общие настройки подписки" : "Общие настройки устройства"}>
    <section className={styles.panel}>
      <header className={styles.heading}><h3>{common ? "Подписка" : "Устройство"}</h3>{!common && <span className={styles.binding}>{binding}</span>}</header>
      <label className={styles.nameRow}><span>Название</span><input className={styles.nameInput} value={common ? profileName : device.name} required maxLength={80} disabled={disabled} onChange={event => common ? onProfileNameChange(event.target.value) : onDeviceNameChange(event.target.value)} /></label>
      {!common && <><dl className={styles.facts}>
        <div><dt>Система</dt><dd>{deviceSystemLabel(device)}</dd></div>
        <div><dt>Приложение</dt><dd>{client}</dd></div>
        <div><dt>Ядро</dt><dd>{core}</dd></div>
      </dl>
      <SubscriptionUsage label="Трафик устройства" status={status} traffic={traffic} /></>}
    </section>
    {common ? <SubscriptionLimitsFields value={limits} status={status} traffic={traffic} disabled={disabled} onChange={onLimitsChange} /> :
      <section className={styles.panel} aria-label="Лимиты общей подписки">
        <header className={styles.heading}><h3>Лимиты подписки</h3>{onOpenSubscription && <button type="button" className={styles.editLink} disabled={disabled} onClick={onOpenSubscription}>Изменить в подписке</button>}</header>
        <dl className={styles.sharedValues}>
          <div><dt>Скорость</dt><dd>{`${limits.speed_mbps ?? 0} Мбит/с`}</dd></div>
          <div><dt>Трафик</dt><dd>{`${limits.traffic_gb ?? 0} GB`}</dd></div>
          <div><dt>Срок</dt><dd>{`${limits.days ?? 0} дней`}</dd></div>
        </dl>
      </section>
    }
  </section>;
}
