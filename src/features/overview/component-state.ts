import type { ProtocolImage } from "../../shared/types/control-plane";

type Tone = "online" | "running" | "warning" | "error" | "unknown" | "available" | "progress";
const summary = (label: string, tone: Tone, description: string) => ({ label, tone, description });

export function componentPresentation(image: ProtocolImage, healthReason?: string) {
  const observed = image.component_state;
  const installation = observed?.installation.state;
  const labels = {
    installed: "Установлен", not_installed: "Не установлен", incomplete: "Неполная установка",
    running: "Запущена", stopped: "Остановлена", error: "Ошибка", unknown: "Нет данных",
    healthy: "Работоспособность подтверждена", possible_issues: "Есть замечания", unchecked: "Не выполнялась",
    installing: "Установка", updating: "Обновление", removing: "Удаление",
  };
  // Older API booleans cannot prove an installation or a stopped runtime.
  const canInstall = installation === "not_installed" && image.installable && image.management?.state !== "unknown" && !observed?.operation;
  const readOnly = image.management?.state !== "managed" || Boolean(image.management?.retained);
  const status = (() => {
    if (observed?.operation?.state === "unknown") return summary("Результат операции неизвестен", "unknown", "Проверьте её результат в центре операций.");
    if (observed?.operation) return summary(observed.operation.kind ? labels[observed.operation.kind] : "Выполняется операция", "progress", "Дождитесь завершения операции.");
    if (!installation) return summary("Состояние неизвестно", "unknown", observed?.installation.reason || "Не удалось подтвердить установку компонента.");
    if (installation === "not_installed") return summary("Не установлен", "available", image.management?.retained ? "Настройки и подключения сохранены." : canInstall ? "Доступен для установки." : image.management?.reason || "Установка сейчас недоступна.");
    if (installation === "incomplete") return summary("Установка неполная", "error", observed?.installation.reason || "Требуется восстановление файлов компонента.");
    if (observed?.runtime.state === "error") return summary("Ошибка службы", "error", observed.runtime.reason || "Не удалось запустить службу компонента.");
    if (observed?.runtime.state === "stopped") return summary("Остановлен", "warning", "Компонент установлен, служба не запущена.");
    if (observed?.runtime.state !== "running") return summary("Служба не проверена", "unknown", observed?.runtime.reason || "Текущее состояние службы недоступно.");
    if (observed.health.state === "error") return summary("Проверка не пройдена", "error", healthReason || observed.health.reason || "Служба запущена, но обнаружена ошибка работы.");
    if (observed.health.state === "possible_issues") return summary("Требует внимания", "warning", healthReason || observed.health.reason || "В работе компонента обнаружены замечания.");
    if (observed.health.state === "healthy") return summary("Работает", "online", observed.health.reason || "Служба запущена, проверка пройдена.");
    return summary("Запущен", "running", observed.health.state === "unchecked" ? "Работоспособность ещё не проверялась." : "Результат проверки работоспособности недоступен.");
  })();
  return {
    installation: installation ? labels[installation] : labels.unknown,
    runtime: observed ? labels[observed.runtime.state] : labels.unknown,
    health: observed ? labels[observed.health.state] : labels.unknown,
    operation: observed?.operation ? labels[observed.operation.kind || "unknown"] : null,
    summary: status,
    canInstall,
    canAdopt: image.installed && image.management?.state === "unmanaged" && !observed?.operation,
    canUpdate: !readOnly && installation === "installed" && !observed?.operation && Boolean(image.update_available),
    canRemove: Boolean(image.installed && image.removable && !readOnly && ["installed", "incomplete"].includes(installation || "") && !observed?.operation),
    readOnly,
  };
}
