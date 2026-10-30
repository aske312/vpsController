# 312node.net PRO

Панель управления Linux VPS: мониторинг, защищённые подключения, DNS,
маршрутизация и подписки Mihomo. Поддерживает WireGuard, AmneziaWG, Shadowsocks,
Xray, Hysteria2, TUIC, OpenVPN и IKEv2.

## Требования

- Ubuntu Server 22.04, 24.04, 26.04 или Debian 12/13;
- `amd64` или `arm64`, systemd, от 1 ГБ RAM и 5 ГБ свободного места;
- root или `sudo`, доступ к GitHub и системным репозиториям.

## Установка

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | sudo bash -s -- --edition pro
```

Для доступа по домену добавьте `--domain vpn.example.com`. Заранее направьте
A-запись на IP сервера и откройте TCP-порты 80 и 443.
Адрес панели и данные для входа появятся после установки.

## Управление

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

## Документация

- [Подключение клиентских устройств](docs/CONNECTION_GUIDE.md)
- [Лимиты подписок](docs/SUBSCRIPTION_LIMITS.md)
- [Диагностика Relay](docs/RELAY_DIAGNOSTICS.md)
- [Приватность](docs/PRIVACY_POLICY.md) · [Условия использования](docs/TERMS_OF_USE.md)

Лицензия [MIT](LICENSE).
