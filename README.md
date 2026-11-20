# vpsController Relay Agent

[Русский](#ru) · [English](#en)

## RU

Передаёт TCP/UDP по схеме **клиент → Relay → VPS**. Устанавливается на отдельный
сервер; маршруты задаются из панели. PRO поддерживает дополнительный зашифрованный
транспорт при использовании обновлённой подписки Mihomo.

### Установка

Debian/Ubuntu, systemd, публичный IPv4, `curl` и root. Выполните под root:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/agent/install.sh -o /tmp/relay-agent-install.sh
bash /tmp/relay-agent-install.sh
```

Добавьте выданный JSON в панель. Он содержит секретный токен.
Повторный вывод: `python3 /usr/local/lib/vps-control-relay-agent/credentials.py`.
Необязательные параметры: `--public-ip IPv4`, `--api-port PORT`, `--domain NAME`,
`--cdn-domain NAME`. Обычный домен должен указывать на Relay; CDN требует
поддерживаемого Cloudflare proxy с IPv4. Доступность транспорта проверяется отдельно.

Обновления проверяются ежедневно; настройки и маршруты сохраняются.
Обновление может прервать соединения. Лимиты ресурсов рассчитываются автоматически;
индивидуальные настройки — объект `limits` в `/etc/vps-control-relay-agent/config.json`.

### Удаление

Сначала исключите Relay из маршрутов панели. Команда удалит Agent, настройки и маршруты:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/agent/protocol-images/relay-agent/uninstall.sh -o /tmp/relay-agent-uninstall.sh
bash /tmp/relay-agent-uninstall.sh
```

Лицензия [MIT](LICENSE).

## EN

Forwards TCP/UDP along **client → Relay → VPS**. Install it on a separate server;
manage routes from the panel. PRO supports an additional encrypted transport
when clients use an updated Mihomo subscription.

### Installation

Requires Debian/Ubuntu, systemd, a public IPv4 address, `curl`, and root. Run as root:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/agent/install.sh -o /tmp/relay-agent-install.sh
bash /tmp/relay-agent-install.sh
```

Add the generated JSON to the panel. It contains a secret access token.
Show it again: `python3 /usr/local/lib/vps-control-relay-agent/credentials.py`.
Optional flags: `--public-ip IPv4`, `--api-port PORT`, `--domain NAME`,
`--cdn-domain NAME`. A regular domain must point to the Relay; CDN requires
supported Cloudflare proxying with IPv4. Transport connectivity is checked separately.

Updates are checked daily and preserve settings and routes.
An update may interrupt connections. Resource limits are calculated automatically;
custom settings belong in `limits` in `/etc/vps-control-relay-agent/config.json`.

### Removal

Remove the Relay from panel routes first. This removes the Agent, settings, and routes:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/agent/protocol-images/relay-agent/uninstall.sh -o /tmp/relay-agent-uninstall.sh
bash /tmp/relay-agent-uninstall.sh
```

Licensed under [MIT](LICENSE).