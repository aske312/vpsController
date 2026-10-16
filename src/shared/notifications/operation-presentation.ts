import type { Notification, NotificationState } from "./store";

const labels: Record<NotificationState, string> = { running: "RUNNING", unknown: "UNKNOWN", success: "DONE", error: "ERROR", cancelled: "CANCELLED", info: "INFO" };
const defaults: Record<NotificationState, string> = { running: "Команда выполняется", unknown: "Результат не подтверждён", success: "Команда выполнена", error: "Команда завершилась с ошибкой", cancelled: "Команда отменена", info: "Ожидает выполнения" };

/** Messages are stage descriptions, never command output or a percentage-derived estimate. */
export function notificationText(message: string) {
  const escape = String.fromCharCode(27);
  return Array.from(message.replace(new RegExp(`${escape}\\[[0-?]*[ -/]*[@-~]`, "g"), "").replace(/\r\n?/g, "\n"))
    .filter(char => char === "\n" || char === "\t" || char.charCodeAt(0) >= 32 && char.charCodeAt(0) !== 127).join("").trim();
}

export function notificationPresentation(item: Pick<Notification, "kind" | "message" | "state" | "progress">) {
  const message = notificationText(item.message);
  let summary = message.split("\n")[0] || defaults[item.state];
  if (item.kind === "operation") {
    if (item.state === "unknown") summary = defaults.unknown;
    else if (item.state === "running" && !/ошиб|не удалось|отказ|неуспеш/i.test(message)) {
      const stages: Array<[RegExp, string]> = [
        [/откат|восстановлени/i, "Восстановление состояния"],
        [/ожидан|ожидаем|готовит релиз|команда принята|передана серверу/i, "Ожидание выполнения"],
        [/перезапуск/i, "Перезапуск служб"],
        [/распаков/i, "Распаковка файлов"],
        [/зависимост.*(?:не измен|провер)|проверк.*зависимост/i, "Проверка зависимостей"],
        [/загруз|скачив/i, "Загрузка файлов"],
        [/рендер|сборк|компиляц/i, "Сборка приложения"],
        [/контрольн|checksum|целостност/i, "Проверка файлов"],
        [/провер/i, "Проверка результата"],
        [/примен/i, "Применение настроек"],
        [/установ/i, "Установка компонентов"],
        [/запуск/i, "Запуск служб"],
        [/очистк|удаление времен/i, "Очистка временных файлов"],
      ];
      summary = stages.find(([pattern]) => pattern.test(message))?.[1] || summary;
    }
  }
  const progress = item.kind === "operation" && item.state === "running" && Number.isFinite(item.progress)
    ? Math.max(0, Math.min(100, item.progress!)) : undefined;
  return { label: labels[item.state], summary, details: message && message !== summary ? message : "", progress };
}

export function notificationDuration(item: Pick<Notification, "createdAt" | "startedAt" | "finishedAt">, now: number) {
  const seconds = Math.max(0, Math.floor(((item.finishedAt ?? now) - (item.startedAt ?? item.createdAt)) / 1000));
  return seconds < 60 ? `${seconds} с` : seconds < 3600 ? `${Math.floor(seconds / 60)} мин ${seconds % 60} с` : `${Math.floor(seconds / 3600)} ч ${Math.floor(seconds % 3600 / 60)} мин`;
}

export function orderedNotifications(items: Notification[]) {
  const pending = (item: Notification) => Number(item.state === "running" || item.state === "unknown");
  return [...items].reverse().sort((left, right) => pending(right) - pending(left));
}
