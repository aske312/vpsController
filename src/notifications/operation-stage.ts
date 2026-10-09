import type { NotificationState } from "./store";

/** Summarize only the reported server step; percentages do not imply a stage. */
export function operationStage(message: string, state: NotificationState) {
  if (state === "unknown") return "Ожидание результата";
  if (state === "success") return "Готово";
  if (state === "error") return message || "Ошибка";
  if (/рендер/i.test(message)) return "Рендеринг";
  if (/сборк|компиляц/i.test(message)) return "Сборка";
  if (/зависимост/i.test(message) && /не измен|проверк/i.test(message)) return "Проверка зависимостей";
  if (/загруз|скачив/i.test(message)) return "Загрузка";
  if (/распаков/i.test(message)) return "Распаковка";
  if (/контрольн|checksum|целостност/i.test(message)) return "Проверка файлов";
  if (/проверк|проверяем/i.test(message)) return "Проверка";
  if (/установ/i.test(message)) return "Установка";
  if (/перезапуск|запуск/i.test(message)) return "Запуск";
  if (/очистк|удаление временных/i.test(message)) return "Очистка";
  return message || "В работе";
}
