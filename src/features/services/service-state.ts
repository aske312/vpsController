import type { RuntimeObservation, ServiceItem, ServicesStatus } from "../../shared/types/control-plane";

export function runtimeState(service: ServiceItem): RuntimeObservation["state"] {
  if (service.runtime) return service.runtime.state;
  // Compatibility with a server that has not received the observation contract yet.
  if (service.state === "failed" || service.substate === "failed") return "error";
  if (service.active) return "running";
  return service.state === "inactive" ? "stopped" : "unknown";
}

export function servicesSummary(services: ServicesStatus | null) {
  const items = services?.items || [];
  const unknownItems = !services || items.some((item) => runtimeState(item) === "unknown" || item.unit_present === null);
  const unknown = unknownItems || services?.failed_units == null;
  const warning = Boolean(services?.reboot_required || services?.failed_units || items.some((item) => runtimeState(item) === "error"));
  return {
    tone: warning || unknown ? "attention" : "healthy",
    title: services?.reboot_required ? "Требуется перезагрузка" : warning ? "Требует внимания" : unknown ? "Unknown" : "Нет аварий systemd",
    hint: services?.reboot_required ? "Обновления ожидают reboot" : services?.failed_units ? `${services.failed_units} аварийных unit` : unknown ? "Часть данных о службах недоступна" : "Работоспособность компонентов проверяется отдельно",
    active: unknownItems ? "—" : `${items.filter((item) => runtimeState(item) === "running").length}/${items.length}`,
    enabled: !services || items.some((item) => item.unit_present === null || item.unit_file_state === "unknown") ? "—" : `${items.filter((item) => item.enabled).length}/${items.length}`,
    restarts: !services || items.some((item) => item.restarts == null) ? "—" : String(items.reduce((sum, item) => sum + (item.restarts ?? 0), 0)),
    failed: services?.failed_units == null ? "—" : String(services.failed_units),
  };
}
