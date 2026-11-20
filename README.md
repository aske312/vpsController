# vpsController PRO

[Русский](#ru) · [English](#en)

## RU

Панель управления Linux VPS: мониторинг, защищённые подключения, DNS,
маршрутизация и подписки Mihomo с модулями WireGuard, AmneziaWG,
Shadowsocks, Xray, Hysteria2 и TUIC.

### Требования

- Ubuntu Server 22.04, 24.04, 26.04 или Debian 12/13;
- `amd64` или `arm64`, systemd, от 1 ГБ RAM и 5 ГБ свободного места;
- root или `sudo`, доступ к GitHub и системным репозиториям.

### Установка

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | sudo bash -s -- --edition pro
```

Для доступа по домену добавьте `--domain vpn.example.com`. Заранее направьте
A-запись на IP сервера и откройте TCP-порты 80 и 443.
Адрес панели и данные для входа появятся после установки.

### Управление

```bash
sudo vps-control credentials  # данные для входа
sudo vps-control status       # состояние
sudo vps-control verify       # проверка установки
sudo vps-control update       # обновление
sudo vps-control restart      # перезапуск
```

Удаление панели, настроек и данных:

```bash
sudo vps-control uninstall --yes
```

### Документация

- [Приватность](docs/PRIVACY_POLICY.md#ru) · [Условия использования](docs/TERMS_OF_USE.md#ru)

Лицензия [MIT](LICENSE).

## EN

A control panel for Linux VPS servers: monitoring, secure connections, DNS,
routing, and Mihomo subscriptions with WireGuard, AmneziaWG, Shadowsocks,
Xray, Hysteria2, and TUIC modules.

### Requirements

- Ubuntu Server 22.04, 24.04, 26.04, or Debian 12/13;
- `amd64` or `arm64`, systemd, at least 1 GB of RAM and 5 GB of free disk space;
- root or `sudo`, with access to GitHub and system package repositories.

### Installation

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | sudo bash -s -- --edition pro
```

To access the panel through a domain, add `--domain vpn.example.com`. Point
the domain's A record to the server IP and open TCP ports 80 and 443 beforehand.
The panel address and login credentials are displayed after installation.

### Management

```bash
sudo vps-control credentials  # show login credentials
sudo vps-control status       # check service status
sudo vps-control verify       # verify the installation
sudo vps-control update       # update the application
sudo vps-control restart      # restart the application
```

Remove the panel, settings, and data:

```bash
sudo vps-control uninstall --yes
```

### Documentation

- [Privacy notice](docs/PRIVACY_POLICY.md#en) · [Terms of use](docs/TERMS_OF_USE.md#en)

Licensed under [MIT](LICENSE).
