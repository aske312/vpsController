# vpsController

[Русский](#ru) · [English](#en)

## RU

Установщик панели управления Linux VPS: **Light**, **PRO** или автономный **Relay Agent**.

### Требования и установка

Debian 13 рекомендуется. Light: Debian 13 / Ubuntu 22.04/24.04;
PRO: Debian 12/13 / Ubuntu 22.04/24.04/26.04. Нужны `amd64`/`arm64`, systemd,
root и доступ к GitHub/репозиториям. Для панели — от 1 ГБ RAM и 5 ГБ диска.

Выполняйте в root-сессии; на минимальной Debian `sudo` может отсутствовать:

```bash
apt-get update && apt-get install -y ca-certificates curl
curl -fL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh -o /root/vps-control-install.sh
bash /root/vps-control-install.sh --edition light
```

Выберите одну редакцию: `light`, `pro` или `agent`. Без `--edition` появится меню.
Установщик покажет адрес панели и данные входа либо параметры Agent.
Для домена заранее настройте DNS на VPS и откройте TCP 80/443.

### Управление панелью

```bash
vps-control status
vps-control credentials
vps-control domain panel.example.com
vps-control identity
vps-control update
```

Данные входа содержат секреты. Команды панели не относятся к автономному Agent.

[Light](https://github.com/aske312/vpsController/tree/light) · [PRO](https://github.com/aske312/vpsController/tree/pro) · [Agent](https://github.com/aske312/vpsController/tree/agent) · [MIT](LICENSE)

## EN

Installer for a Linux VPS control panel: **Light**, **PRO**, or a standalone **Relay Agent**.

### Requirements and installation

Debian 13 is recommended. Light: Debian 13 / Ubuntu 22.04/24.04;
PRO: Debian 12/13 / Ubuntu 22.04/24.04/26.04. Requires `amd64`/`arm64`, systemd,
root, and access to GitHub/package repositories. The panel needs at least 1 GB RAM and 5 GB disk space.

Run in a root session; minimal Debian installations may not include `sudo`:

```bash
apt-get update && apt-get install -y ca-certificates curl
curl -fL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh -o /root/vps-control-install.sh
bash /root/vps-control-install.sh --edition light
```

Choose one edition: `light`, `pro`, or `agent`. Omit `--edition` for an interactive menu.
The installer displays the panel address and credentials, or Agent connection details.
For a domain, point DNS to the VPS and open TCP ports 80/443 beforehand.

### Panel management

```bash
vps-control status
vps-control credentials
vps-control domain panel.example.com
vps-control identity
vps-control update
```

Credentials contain secrets. Panel commands do not apply to the standalone Agent.

[Light](https://github.com/aske312/vpsController/tree/light) · [PRO](https://github.com/aske312/vpsController/tree/pro) · [Agent](https://github.com/aske312/vpsController/tree/agent) · [MIT](LICENSE)
