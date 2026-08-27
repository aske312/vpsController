# 312.net

Панель управления VPS и VPN-протоколами. Поддерживает установку и обслуживание WireGuard, AmneziaWG, Shadowsocks, Mihomo и VLESS, просмотр состояния сервера и управление подключениями.

## Требования

- Ubuntu Server 22.04, 24.04, 26.04 или Debian 12/13;
- amd64 или arm64, systemd, от 1 ГБ RAM и 5 ГБ свободного места;
- root-доступ или пользователь с `sudo`.

## Установка

Под `root`:

```bash
apt-get update && apt-get install -y ca-certificates curl
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/stabl/scripts/install-panel.sh -o /root/install-312.sh
bash /root/install-312.sh
```

Под пользователем с `sudo`:

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/stabl/scripts/install-panel.sh -o install-312.sh
sudo bash ./install-312.sh
```

Для панели с доменом заранее направьте A-запись на IPv4 сервера и откройте TCP-порты 80 и 443:

```bash
sudo bash ./install-312.sh --domain vpn.example.com
```

Caddy автоматически настроит HTTPS. Без `--domain` панель будет доступна по IP и порту 8080. Установщик сам определяет доступные адреса и местоположение сервера. Отдельно запускать `vps-control update` не требуется.

После установки адрес панели, логин и пароль выводятся в терминал. Повторно показать их можно командой:

```bash
sudo vps-control credentials
```

## VLESS и CDN

Сначала установите VLESS в панели. Прямой REALITY/XHTTP-профиль работает независимо от домена.

Для дополнительного маршрута через Cloudflare создайте отдельную proxied A-запись поддомена на IPv4 сервера. Затем откройте страницу VLESS, включите опцию CDN и укажите этот поддомен. Панель подготовит отдельные Direct и CDN профили подключения.

## Основные команды

```bash
sudo vps-control status
sudo vps-control verify
sudo vps-control integrity-check
sudo vps-control restart
sudo vps-control update
```

Полное удаление панели и её конфигурации:

```bash
sudo vps-control uninstall --yes
```

## Возможные ошибки установки

- Если установка прервалась, повторите ту же команду: завершённые настройки сохраняются.
- Если домен не открывается, проверьте A/AAAA-записи и доступность TCP 80/443.
- Для диагностики выполните `sudo vps-control verify`.
- Журнал установки: `/var/log/vps-control-install.log`.

## Ручное обновление без сборки на VPS

Подготовленный Linux-релиз устанавливается командой:

```bash
sudo vps-control install-release /root/vps-control-release.tar.gz
```

Обычным пользователям рекомендуется обновление из панели или командой `sudo vps-control update`.

## Разработка

```bash
npm test
npm run lint
```

Ветка `main` используется для разработки, `stabl` — для стабильных установок и обновлений.

## Лицензия

[MIT](LICENSE)
