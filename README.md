# 312.net VPS Controller

312.net — self-hosted панель управления Linux VPS. Продукт устанавливается на сервер пользователя и доступен в двух редакциях.

## Редакции

- **Light** — бесплатная компактная панель для мониторинга сервера и управления подключениями WireGuard и AmneziaWG.
- **PRO** — расширенная панель с дополнительными протоколами, Mihomo, DNS, relay-маршрутами и средствами защиты.

Редакция выбирается во время установки. Обновления сохраняют выбранную редакцию и не переключают Light на PRO или PRO на Light.

## Требования

- Light: Debian 13 или Ubuntu Server 22.04/24.04;
- PRO: Debian 12/13 или Ubuntu Server 22.04/24.04/26.04;
- архитектура `amd64` или `arm64`;
- от 1 ГБ оперативной памяти и 5 ГБ свободного места;
- root-доступ или пользователь с `sudo`;
- доступ сервера к GitHub и системным репозиториям.

Для новой установки рекомендуется Debian 13.

## Установка

Интерактивный выбор редакции:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh | sudo bash
```

Установка Light:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | sudo bash -s -- --edition light
```

Установка PRO:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | sudo bash -s -- --edition pro
```

Установщик проверит совместимость сервера, установит выбранную редакцию и покажет адрес панели и данные для входа.

## Лицензия

Проект распространяется по лицензии [MIT](LICENSE).
