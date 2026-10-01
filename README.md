# 312.net VPS Controller

Единая точка установки двух редакций панели управления Linux VPS.

## Статус веток

- `installer` — основная ветка репозитория и единая точка установки;
- `light` / `test-light` — production и test-разработка редакции Light;
- `pro` / `test-pro` — production и test-разработка редакции PRO;
- `main` и `stabl` — устаревшие ветки предыдущей структуры, оставленные только на период наблюдения. Для новых установок и разработки их использовать не следует.

## Редакции

- **Light** — бесплатная компактная панель с WireGuard и AmneziaWG.
- **PRO** — расширенная редакция с Mihomo, DNS, relay-маршрутами, дополнительными протоколами и средствами защиты.

Редакции устанавливаются и обновляются независимо. Обычное обновление никогда не заменяет Light на PRO или PRO на Light.

## Требования

- Debian 13 — основная поддерживаемая ОС;
- Ubuntu Server 22.04/24.04 — дополнительная совместимость;
- `amd64` или `arm64`;
- root или `sudo`;
- systemd, apt/dpkg, curl и доступ к GitHub.

## Интерактивная установка

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh | sudo bash
```

## Прямая установка

Light:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | sudo bash -s -- --edition light
```

PRO:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | sudo bash -s -- --edition pro
```

Установщик проверяет ОС и архитектуру, читает разрешённый маршрут из `editions.json`, а затем запускает установщик выбранной production-редакции.

Production и test-каналы редакций разделены и проверяются независимо. Test-ветки создают только временные CI artifacts и не публикуют GitHub Releases.

## Документация проверки

- [Что осталось сделать](REMAINING_WORK.md)
- [Полный стендовый тест-план](TEST_PLAN.md)

## Лицензия

Проект распространяется по лицензии [MIT](LICENSE).
