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
- вход на сервер под `root`;
- доступ сервера к GitHub и системным репозиториям.

Для новой установки рекомендуется Debian 13.

## Установка

Команды выполняются в shell пользователя `root`; `sudo` не требуется и может
отсутствовать в минимальном образе VPS.

Установщик: [install.sh](https://github.com/aske312/vpsController/blob/installer/install.sh)

Прямая ссылка для `curl`: `https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh`

Интерактивный выбор редакции:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh | bash
```

Установка Light:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | bash -s -- --edition light
```

Установка PRO:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | bash -s -- --edition pro
```

Установка сразу с доменом:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | bash -s -- --edition light --domain panel.example.com
```

До запуска создайте DNS-запись `A` или `AAAA`, направленную на VPS, и разрешите
входящие TCP-порты `80` и `443` во внешнем firewall провайдера. Если DNS ещё не
обновился, Light сохранит домен и продолжит работать по IP; повторная проверка
выполняется командой `vps-control identity`.

## Параметры установки

Параметры указываются после `bash -s --` и могут объединяться в одной команде.

| Параметр | Редакция | Назначение |
| --- | --- | --- |
| `--edition light` | Light | установить Light без интерактивного выбора |
| `--edition pro` | PRO | установить PRO без интерактивного выбора |
| `--domain DOMAIN` | Light, PRO | сохранить домен панели и настроить HTTPS после проверки DNS |
| `--location-city CITY` | PRO | явно указать физический город сервера |
| `--location-country COUNTRY` | PRO | явно указать физическую страну сервера |
| `--location-country-code CODE` | PRO | указать двухбуквенный код страны, например `NL` |
| `--manual` | PRO | разрешить интерактивное восстановление `dpkg`/GRUB |
| `--no-os-update` | PRO | не обновлять уже установленные пакеты ОС |
| `--no-apt` | PRO | не использовать `apt`/`dpkg`; зависимости должны быть установлены заранее |

Пример PRO с доменом и подтверждённой локацией:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | bash -s -- --edition pro --domain panel.example.com \
    --location-city Helsinki --location-country Finland --location-country-code FI
```

Установщик проверит совместимость сервера, установит выбранную редакцию и покажет адрес панели и данные для входа.

## Лицензия

Проект распространяется по лицензии [MIT](LICENSE).
