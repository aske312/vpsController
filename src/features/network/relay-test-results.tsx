import type { RelayDeviceTest, RelayMeasurement } from "./relay-state";

const reasons: Record<string, string> = {
  udp_verified: "Все UDP-пакеты вернулись", packet_loss: "Часть UDP-пакетов потеряна",
  client_to_relay_no_packets: "Relay не получил тестовые пакеты от отправителя",
  relay_to_vps_no_reply: "Relay отправил пакеты к VPS, но не получил ответа",
  relay_to_client_no_reply: "Relay получил ответ VPS, но отправитель ответа не получил",
  measurement_unavailable: "UDP-измерение недоступно: проверьте версию Agent и настройки теста",
  measurement_inconsistent: "Наблюдения сторон не совпали; результат не подтверждён",
};

export function RelayLossResult({ result, source, stale = false }: { result?: RelayMeasurement; source: "VPS" | "устройство"; stale?: boolean }) {
  if (!result) return null;
  return <section className="routeDetailsCheck" aria-label={`UDP-тест: ${source}`}>
    <strong>UDP-путь: {source} → Relay → VPS → Relay → {source}</strong>
    {stale && <p>Сохранённый результат. Его актуальность не подтверждена.</p>}
    <p>{reasons[result.reason] || "Результат не подтверждён"}</p>
    {result.sent !== undefined && <p>Отправлено: {result.sent}, получено: {result.received}. Потери: {result.loss_percent}% · RTT: {result.rtt_ms ?? "—"} мс.</p>}
    {result.scope && <dl className="routeDetailsFacts">
      <div><dt>Потери {source} → Relay</dt><dd>{result.client_to_relay_lost}</dd></div>
      <div><dt>Потери Relay → VPS</dt><dd>{result.relay_to_vps_lost}</dd></div>
      <div><dt>Потери VPS → Relay</dt><dd>{result.vps_to_relay_lost}</dd></div>
      <div><dt>Потери Relay → {source}</dt><dd>{result.relay_to_client_lost}</dd></div>
      <div><dt>Локальные отбрасывания Agent / VPS</dt><dd>{result.agent_local_drops} / {result.origin_local_drops}</dd></div>
      <div><dt>RTT Relay ↔ VPS</dt><dd>{result.relay_vps_rtt_ms ?? "—"} мс</dd></div>
    </dl>}
    <p>100 пакетов по 1200 байт без повторных отправок. Это отдельный диагностический UDP-порт между теми же узлами; медиатест протокола показан отдельно.</p>
  </section>;
}

export function RelayDeviceResult({ test, disabled, onDownload, onUpload, onCancel }: { test?: RelayDeviceTest; disabled: boolean; onDownload?: () => void; onUpload?: (file: File) => void; onCancel?: () => void }) {
  if (!test) return null;
  const active = ["preparing", "awaiting_client", "processing"].includes(test.state);
  const labels: Record<RelayDeviceTest["state"], string> = { preparing: "Подготовка", awaiting_client: "Ожидается запуск на устройстве", processing: "Проверяем отчёт и очищаем ресурсы", ready: "Проверка пройдена", warning: "Есть замечания", failed: "Проверка не пройдена", unknown: "Результат не подтверждён", expired: "Время теста истекло", cancelled: "Тест отменён" };
  const failures: Record<string, string> = { export_unavailable: "Подключение не соответствует выбранному порту Relay или его экспорт недоступен.", temporary_peer_unavailable: "Не удалось безопасно подготовить временный WG/AWG peer. Рабочий peer не используется.", measurement_unavailable: "Не удалось подготовить UDP-тест. Проверьте Agent 1.3 и возможность открыть временный UDP-порт на VPS.", kit_unavailable: "Не удалось подготовить комплект с адресом Relay без раскрытия origin VPS.", report_invalid: "Отчёт или наблюдения сторон не прошли проверку.", configuration_changed: "Настройки изменились во время теста.", configuration_changed_or_stale: "Настройки изменились, данные Relay недоступны или результат старше часа." };
  return <section className="routeDetailsCheck" aria-label="Тест с устройства">
    <strong>Тест с устройства · {labels[test.state]}</strong>
    {test.state === "unknown" && <p>{failures[test.reason] || "Не удалось подтвердить результат теста."}</p>}
    <p>Создан: {new Date(test.created_at * 1000).toLocaleString("ru-RU")}. Срок запуска: до {new Date(test.expires_at * 1000).toLocaleTimeString("ru-RU")}.</p>
    {test.state === "awaiting_client" && <>
      <p>Скачайте и распакуйте комплект. Запустите <code>python runner.py --core ПУТЬ_К_ЯДРУ</code>, затем загрузите <code>result.json</code>. Нужны Python, curl и ядро указанного в README формата. Системные VPN и маршруты тест не переключает.</p>
      <button type="button" disabled={disabled || !onDownload} onClick={onDownload}>Скачать тест</button>
      <label>Загрузить результат<input type="file" accept=".json,application/json" disabled={disabled || !onUpload} onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) onUpload?.(file); }} /></label>
    </>}
    {active && <button type="button" disabled={disabled || !onCancel} onClick={onCancel}>Отменить тест</button>}
    {test.media && <p>Видео: {test.media.state === "ready" ? "получено и проверено" : test.media.state === "failed" ? "не прошло проверку" : "результат не подтверждён"}{test.media.verified_bytes ? ` · ${test.media.verified_bytes} байт` : ""}. Медиарезультат сообщён клиентской утилитой; UDP-путь сопоставлен с наблюдениями Relay и VPS.</p>}
    <RelayLossResult result={test.measurement} source="устройство" stale={test.state === "unknown"} />
    {test.cleanup_state && <p role={test.cleanup_state === "failed" ? "alert" : undefined}>{test.cleanup_state === "cleaned" ? "Временные ресурсы теста удалены." : test.cleanup_state === "pending" ? "Удаление диагностического порта Relay ещё не подтверждено; Agent закроет его по сроку." : "Не удалось подтвердить полную очистку временных ресурсов; требуется проверка на сервере."}</p>}
  </section>;
}
