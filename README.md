# Relay Agent

Передаёт TCP/UDP-трафик по схеме **клиент → Relay → VPS**. Устанавливается на отдельный сервер, маршруты задаются из панели.

## Установка

Нужны Debian/Ubuntu с systemd, публичный IPv4, `curl` и права root.

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/agent/install.sh -o /tmp/relay-agent-install.sh
sudo bash /tmp/relay-agent-install.sh
```

Установщик настроит и запустит Agent, затем покажет JSON для добавления Relay в панели. Сохраните эти данные: они содержат токен доступа.

Повторно показать данные:

```bash
sudo python3 /usr/local/lib/vps-control-relay-agent/credentials.py
```

Транспортные домены берутся из настроек Network/CDN установленной панели или
явных параметров установки `--domain NAME` и `--cdn-domain NAME`. Обычный домен
должен разрешаться на IP Relay; CDN-домен — в поддерживаемый Cloudflare proxy
с IPv4. Имена панели, hostname и произвольные сайты не добавляются автоматически.
Непроверенные и устаревшие имена не передаются; DNS-проверка не подтверждает
работоспособность конкретного транспорта.

Обновления проверяются автоматически раз в сутки. Повторная установка сохраняет настройки и маршруты. При обновлении соединения могут прерваться.

## Удаление

Сначала уберите Relay из маршрутов в панели, затем выполните на его сервере:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/agent/protocol-images/relay-agent/uninstall.sh -o /tmp/relay-agent-uninstall.sh
sudo bash /tmp/relay-agent-uninstall.sh
```

Команда остановит Agent, удалит его файлы, настройки, маршруты и созданные им правила UFW.
