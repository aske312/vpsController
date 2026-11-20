# vpsController Light

[Русский](#ru) · [English](#en)

## RU

Self-hosted панель Linux VPS: мониторинг, независимые модули протоколов,
персональные подключения, готовые конфигурации и диагностика.

### Установка

Debian 13 / Ubuntu Server 22.04/24.04, `amd64`/`arm64`, systemd, root,
от 1 ГБ RAM и 5 ГБ диска. Нужен доступ к GitHub и репозиториям ОС.

Выполните под root:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | bash -s -- --edition light
```

Устанавливается стабильная версия Light. Адрес панели и данные входа появятся в терминале.
Домен можно передать установщику: `--domain panel.example.com`.
Для HTTPS направьте DNS на VPS и откройте TCP 80/443, затем задайте домен:

```bash
vps-control domain panel.example.com
```

### Управление

```bash
vps-control status
vps-control credentials
vps-control verify
vps-control restart
vps-control update
```

При ошибках проверьте DNS, доступ к репозиториям и системное время.
`vps-control uninstall --yes` удаляет панель, её настройки и данные.

[AmneziaWG](docs/CONNECTION_GUIDE.md#ru) · [Приватность](docs/PRIVACY_POLICY.md#ru) · [Условия](docs/TERMS_OF_USE.md#ru) · [MIT](LICENSE)

## EN

A self-hosted Linux VPS panel for monitoring, independent protocol modules,
personal connections, ready-to-use configurations, and diagnostics.

### Installation

Debian 13 / Ubuntu Server 22.04/24.04, `amd64`/`arm64`, systemd, root,
at least 1 GB RAM and 5 GB disk space. Requires access to GitHub and OS repositories.

Run as root:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | bash -s -- --edition light
```

Installs the stable Light release. The terminal displays the panel address and credentials.
You can pass the domain to the installer: `--domain panel.example.com`.
For HTTPS, point DNS to the VPS and open TCP ports 80/443, then set the domain:

```bash
vps-control domain panel.example.com
```

### Management

```bash
vps-control status
vps-control credentials
vps-control verify
vps-control restart
vps-control update
```

If installation fails, check DNS, repository access, and the system clock.
`vps-control uninstall --yes` removes the panel, its settings, and data.

[AmneziaWG](docs/CONNECTION_GUIDE.md#en) · [Privacy](docs/PRIVACY_POLICY.md#en) · [Terms](docs/TERMS_OF_USE.md#en) · [MIT](LICENSE)
