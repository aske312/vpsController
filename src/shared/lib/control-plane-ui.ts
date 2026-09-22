import type { Protocol, Tab } from "../types/control-plane";
export function applicationActionState(action?: { state?: string; result?: string }) {
  if (action?.state === "failed" || action?.result === "failed" || action?.result === "interrupted") return "FAILED";
  if (["queued", "active", "activating", "running", "rebooting", "powering-off"].includes(action?.state || "")) return "RUNNING";
  if (["succeeded", "finished"].includes(action?.state || "")) return "DONE";
  return "UNKNOWN";
}
export const labels: Record<Tab | Protocol, string> = { overview: "Обзор", channels: "Tunnels", dns: "DNS", network: "Сеть", security: "Безопасность", application: "Приложение", services: "Службы", wg: "WireGuard", awg: "AmneziaWG", shadowsocks: "Shadowsocks", "vless-reality-xhttp": "VLESS", hysteria2: "Hysteria2", tuic: "TUIC v5", trojan: "Trojan", openvpn: "OpenVPN", ikev2: "IKEv2", clients: "Подключения", mihomo: "Mihomo" };
export const directProtocolOrder: Protocol[] = ["awg", "wg", "vless-reality-xhttp", "hysteria2", "tuic", "trojan", "openvpn", "ikev2", "shadowsocks"];
export const navigationLabels: Record<Tab, string> = { overview: "OVERVIEW", channels: "TUNNELS", dns: "DNS", network: "Сеть", security: "SECURITY", application: "APPLICATION", services: "SERVICES", wg: "WIREGUARD", awg: "AMNEZIAWG", shadowsocks: "SHADOWSOCKS", "vless-reality-xhttp": "VLESS", hysteria2: "HYSTERIA2", tuic: "TUIC V5", trojan: "TROJAN", openvpn: "OPENVPN", ikev2: "IKEV2", clients: "CONNECTIONS", mihomo: "MIHOMO" };
export const actionLabels: Record<string, string> = { "mihomo-profile-module-settings": "Настройки модуля Mihomo", "mihomo-profile-create": "Создание профиля Mihomo", "mihomo-profile-update": "Изменение профиля Mihomo", "mihomo-profile-delete": "Удаление профиля Mihomo", "mihomo-profile-device-delete": "Удаление устройства Mihomo", "mihomo-profile-reconcile": "Восстановление конфигурации Mihomo", "mihomo-module-recover": "Восстановление профилей Mihomo", "mihomo-module-install": "Установка модуля Mihomo", "mihomo-module-update": "Обновление модуля Mihomo", "mihomo-module-remove": "Удаление модуля Mihomo", "ssh-key-add": "Добавление SSH-ключа", "ssh-key-reset": "Сброс SSH-ключа", "ssh-key-delete": "Удаление SSH-ключа", "ssh-access-begin": "Настройка SSH-доступа", "ssh-access-confirm": "Подтверждение SSH-доступа", "ssh-access-rollback": "Восстановление SSH-доступа", "ssh-access-disable": "Открытие SSH-доступа", "dns-recover": "Восстановление DNS", "dns-settings": "Применение DNS", "network-settings": "Настройка маршрутов", "network-delete": "Удаление маршрута", "automation-recover": "Восстановление расписаний", "automation-config": "Применение расписаний", "service-action": "Управление службой", "logging-config": "Настройка журналов", "logs-clear": "Очистка журналов", "protocol-purge": "Очистка данных компонента", "cdn-security": "Cloudflare", "ech": "ECH", install: "Установка 312.net", start: "Запуск приложения", stop: "Остановка приложения", restart: "Перезапуск приложения", update: "Обновление приложения", "test-update": "Переход на тестовую версию", "test-rollback": "Возврат к рабочей версии", "network-check": "Проверка сети и туннелей", identity: "Обновление данных сервера", "integrity-check": "Проверка целостности", secure: "Настройка защиты", "safe-update": "Полное обновление сервера", "kernel-update": "Обновление ядра", "vpn-firewall": "Восстановление VPN firewall", optimize: "Оптимизация ресурсов", "service-mode": "Переключение режима и ветки", "access-mode": "Переключение доступа к панели", reboot: "Перезагрузка сервера", poweroff: "Выключение сервера", "protocol-install": "Установка протокола", "protocol-remove": "Удаление протокола", "protocol-update": "Обновление протокола" };
export const bytes = (value = 0) => { if (!Number.isFinite(value) || value <= 0) return "0 B"; const units = ["B", "KB", "MB", "GB", "TB"]; const index = Math.max(0, Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)); return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`; };
export const duration = (seconds?: number) => { if (seconds === undefined || seconds === null) return "никогда"; if (seconds < 60) return `${seconds} сек назад`; if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`; return `${Math.floor(seconds / 3600)} ч назад`; };
export const safeDateTime = (value?: string) => { if (!value) return "—"; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleString("ru-RU"); };
export const uptime = (seconds?: number | null) => seconds == null ? "—" : `${Math.floor(seconds / 86400)}д ${Math.floor((seconds % 86400) / 3600)}ч`;
export const LIVE_SAMPLE_SECONDS = 3;
export const HISTORY_SAMPLES = 100;
export const CLIENTS_PER_PAGE = 10;

type Traffic = { stats_available?: boolean; stats_partial?: boolean; rx_bytes?: number | null; tx_bytes?: number | null };
export function aggregateTraffic(rows: (Traffic | null | undefined)[]) {
  const known = rows.filter((row): row is Traffic => Boolean(row && row.stats_available !== false && row.rx_bytes != null && row.tx_bytes != null));
  return {
    stats_available: rows.length === 0 || known.length > 0,
    stats_partial: known.length !== rows.length || known.some((row) => row.stats_partial),
    rx_bytes: known.reduce((sum, row) => sum + Number(row.rx_bytes), 0),
    tx_bytes: known.reduce((sum, row) => sum + Number(row.tx_bytes), 0),
  };
}
export function trafficBytes(stats: Traffic | null | undefined, direction: "rx_bytes" | "tx_bytes"): string {
  const value = stats?.[direction];
  return stats?.stats_available === false || value == null ? "—" : `${stats?.stats_partial ? "≥ " : ""}${bytes(value)}`;
}

export function connectionOnline(stats: { active?: boolean | null } | null | undefined): boolean {
  return stats?.active === true;
}
