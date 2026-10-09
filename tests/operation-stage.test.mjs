import assert from "node:assert/strict";
import test from "node:test";
import { operationStage } from "../src/notifications/operation-stage.ts";

test("server steps are summarized without inventing progress stages", () => {
  for (const [message, stage] of [
    ["Загрузка подготовленного релиза ветки test-light · 163 МБ", "Загрузка"],
    ["Распаковка подготовленного релиза", "Распаковка"],
    ["Проверка контрольных сумм", "Проверка файлов"],
    ["Проверка установленных компонентов", "Проверка"],
    ["Python-зависимости не изменились.", "Проверка зависимостей"],
    ["Установка протокола выполняется", "Установка"],
    ["Сборка веб-интерфейса", "Сборка"],
    ["Рендеринг страниц", "Рендеринг"],
    ["Запуск обновлённой версии", "Запуск"],
    ["Ожидание блокировки пакетного менеджера", "Ожидание блокировки пакетного менеджера"],
  ]) assert.equal(operationStage(message, "running"), stage);
});

test("unknown and terminal states take precedence and errors keep their reason", () => {
  assert.equal(operationStage("Загрузка релиза", "unknown"), "Ожидание результата");
  assert.equal(operationStage("Установка завершена", "success"), "Готово");
  assert.equal(operationStage("Не совпала контрольная сумма", "error"), "Не совпала контрольная сумма");
});
