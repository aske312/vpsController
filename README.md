# 312node.net PRO

312node.net PRO — self-hosted панель управления Linux VPS для мониторинга сервера, управления защищёнными подключениями и настройки сетевых маршрутов.

## Возможности

- мониторинг ресурсов, сети, служб и событий безопасности;
- WireGuard, AmneziaWG, Shadowsocks, VLESS, Hysteria2, TUIC, Trojan, OpenVPN и IKEv2;
- создание конфигураций, ссылок и QR-кодов для клиентских устройств;
- Mihomo, DNS-политики и маршрутизация трафика;
- relay-маршруты и дополнительные средства защиты;
- диагностика, обновление и восстановление приложения.

## Требования

- Ubuntu Server 22.04, 24.04, 26.04 или Debian 12/13;
- архитектура `amd64` или `arm64` и systemd;
- от 1 ГБ оперативной памяти и 5 ГБ свободного места;
- root-доступ или пользователь с `sudo`;
- доступ сервера к GitHub и системным репозиториям.

Для новой установки рекомендуется Debian 13. Доступность отдельных протоколов зависит от ОС, архитектуры и ядра сервера и проверяется перед установкой модуля.

## Установка

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | sudo bash -s -- --edition pro
```

Для работы панели по доменному имени заранее направьте A-запись на IPv4 сервера и откройте TCP-порты 80 и 443:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | sudo bash -s -- --edition pro --domain vpn.example.com
```

После завершения установки в терминале будут показаны адрес панели и данные для входа. Повторно вывести их можно командой:

```bash
sudo vps-control credentials
```

## Управление

```bash
sudo vps-control status
sudo vps-control restart
sudo vps-control verify
sudo vps-control update
```

## Удаление

Команда удаляет панель, её настройки и данные. Установленные системные пакеты сохраняются.

```bash
sudo vps-control uninstall --yes
```

## Документы

- [Подключение клиентских устройств](docs/CONNECTION_GUIDE.md)
- [Уведомление о приватности](docs/PRIVACY_POLICY.md)
- [Условия использования](docs/TERMS_OF_USE.md)

## Лицензия

312node.net PRO распространяется по лицензии [MIT](LICENSE).
