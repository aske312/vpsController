import assert from "node:assert/strict";
import test from "node:test";

import { createNotificationStore } from "../src/notifications/store.ts";

test("operation notifications keep one card and finish it in place", () => {
  let now = 1_000;
  const store = createNotificationStore(() => now);

  store.upsert({
    id: "operation:system:update-1",
    source: "system",
    title: "Обновление",
    message: "Запуск",
    state: "running",
    kind: "operation",
    progress: 12,
  });
  store.upsert({
    id: "operation:system:update-1",
    source: "system",
    title: "Обновление",
    message: "Установка",
    state: "running",
    kind: "operation",
    progress: 64,
  });

  assert.equal(store.getSnapshot().length, 1);
  assert.equal(store.getSnapshot()[0].progress, 64);
  assert.equal(store.getSnapshot()[0].message, "Установка");

  store.finishOperation({
    id: "operation:system:update-1",
    source: "system",
    title: "Обновление",
    message: "Готово",
    state: "success",
    kind: "operation",
  });

  assert.equal(store.getSnapshot()[0].state, "success");
  assert.equal(store.getSnapshot()[0].message, "Готово");

  now += 8_001;
  store.tick();
  assert.deepEqual(store.getSnapshot(), []);
});

test("operation stage changes remain visible even when progress has not advanced", () => {
  const store = createNotificationStore();
  const operation = { id: "download", source: "system", title: "Обновление", state: "running", kind: "operation", progress: 12 };
  store.upsert({ ...operation, message: "Загрузка релиза" });
  store.upsert({ ...operation, message: "Проверка контрольных сумм" });
  assert.equal(store.getSnapshot().length, 1);
  assert.equal(store.getSnapshot()[0].message, "Проверка контрольных сумм");
  assert.equal(store.getSnapshot()[0].progress, 12);
  store.upsert({ ...operation, message: "", state: "unknown" });
  assert.equal(store.getSnapshot()[0].message, "Ожидаем подтверждения результата.");
});

test("an active operation cannot be dismissed, but a completed one can", () => {
  const store = createNotificationStore(() => 1_000);
  const operation = {
    id: "operation:system:restart-1",
    source: "system",
    title: "Перезапуск",
    message: "",
    kind: "operation",
  };

  store.upsert({ ...operation, state: "running" });
  store.dismiss(operation.id);
  assert.equal(store.getSnapshot().length, 1);

  store.finishOperation({ ...operation, state: "error", message: "Команда завершилась с ошибкой." });
  store.dismiss(operation.id);
  assert.deepEqual(store.getSnapshot(), []);
});
