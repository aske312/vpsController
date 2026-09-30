# 312.net

312.net — панель управления Linux-сервером. Она показывает состояние ресурсов, служб, сети и безопасности, а также позволяет выполнять диагностику, обновление и обслуживание приложения.
## Требования

- Debian 13 или Ubuntu Server 22.04/24.04 с systemd;
- amd64 или arm64;
- от 1 ГБ RAM и 5 ГБ свободного места;
- доступ к системным репозиториям, NodeSource и GitHub;
- root или пользователь с `sudo`.

## Установка на новый сервер

Установка рассчитана на чистую Debian 13 или Ubuntu Server 22.04/24.04 с systemd. Она добавляет Caddy, Node.js 22, Python 3 с venv, Git, UFW, OpenSSH, Fail2ban, auditd, unattended-upgrades и остальные необходимые системные пакеты. Docker не используется.

Неизвестная ОС или версия вызывает предупреждение, но сама по себе не блокирует установку. Установщик проверяет фактические требования: apt/dpkg, systemd, архитектуру, память, место и DNS. При отсутствии обязательной зависимости установка останавливается с объяснением причины. Совместимость дополнительных VPN-модулей проверяется отдельно.

Самый короткий вариант запускает единый установщик и сразу выбирает Light:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh | sudo bash -s -- --edition light
```

Установка из Git-клона удобна, если перед запуском нужно проверить или изменить `install.conf`:

```bash
git clone --branch light https://github.com/aske312/vpsController.git
cd vpsController
sudo bash scripts/install-panel.sh
```

`scripts/install-panel.sh` является установщиком редакции Light. При запуске из клона он использует локальные файлы; единая публичная команда из ветки `installer` выбирает редакцию и направляет установку в `light` или `pro`. Цветной мастер показывает понятные этапы, прогресс и результат. Технический вывод скрывается и появляется лишь при ошибке.

Пакетные сообщения записываются в `/var/log/vps-control-install.log`. После успешной установки откройте показанный HTTP-адрес и войдите с параметрами из `install.conf`: логин `admin`, пароль `VPSControl.312`. Так как соединение работает по HTTP, используйте пароль только в доверенной сети или поверх WG/AWG и смените его перед публикацией панели в недоверенной сети.

Проверка после установки:

```bash
sudo vps-control status
sudo vps-control verify
sudo systemctl --no-pager --full status vps-control-api vps-control-web caddy
```

### Возможные ошибки установки

- `dpkg was interrupted` или незавершённая настройка пакетов: выполните `sudo dpkg --configure -a`, затем повторите установку.
- `Could not get lock /var/lib/dpkg/lock`: дождитесь завершения `apt`, `unattended-upgrades` или первичной настройки cloud-init. Установщик ожидает блокировку до пяти минут; не удаляйте lock-файлы вручную.
- GitHub, NodeSource или репозитории Ubuntu недоступны: проверьте `getent hosts github.com`, DNS, исходящий HTTPS и системное время командой `timedatectl`.
- Недостаточно памяти или места: требуется не менее 1 ГБ RAM и 5 ГБ свободного места. Проверьте `free -h` и `df -h`.
- Порт 80 занят: проверьте `sudo ss -ltnp | grep ':80 '`. Остановите конфликтующую службу либо задайте другой `HTTP_PORT` в `install.conf` до установки.
- Caddy или приложение не запустились: выполните `sudo vps-control verify`, затем посмотрите `sudo journalctl -u vps-control-api -u vps-control-web -u caddy -n 200 --no-pager` и `/var/log/vps-control-install.log`.
- Установка оборвалась из-за разрыва SSH: повторно запустите ту же команду. Конфигурация и уже установленные пакеты используются повторно; WG/AWG устанавливаются отдельно как модули.
- Bootstrap не может скачать ветку: клонируйте репозиторий командой из примера и запустите `sudo bash scripts/install-panel.sh` локально.

Для ZIP-архива или скопированного каталога наличие `.git` не требуется: установщик автоматически использует HTTPS-репозиторий проекта для проверки стабильной версии.

## Ручное обновление без сборки на VPS

Релиз собирается заранее на Linux-машине или в CI. На VPS не запускаются Docker, `npm ci`, `npm build`, `apt upgrade` или очистка системных пакетов.

```bash
bash scripts/build-release.sh outputs/vps-control-release.tar.gz
scp outputs/vps-control-release.tar.gz root@SERVER:/root/
ssh root@SERVER 'vps-control install-release /root/vps-control-release.tar.gz'
```

Архив содержит готовые `dist` и Linux runtime-зависимости, metadata редакции и канала, а также файл контрольных сумм. Перед переключением сервер проверяет архив полностью. При ошибке запуска выполняется откат к предыдущему каталогу приложения. `/etc/wireguard`, `/etc/amnezia`, клиенты, ключи, сетевые интерфейсы и системные пакеты не изменяются. Кнопка обновления и команда `vps-control update` используют `light-latest` и отклоняют архив PRO, test-канал или другую архитектуру.

## Управление

```bash
sudo vps-control start
sudo vps-control stop
sudo vps-control restart
sudo vps-control status
sudo vps-control verify
sudo vps-control integrity-check
sudo vps-control install-release /root/vps-control-release.tar.gz
sudo vps-control credentials
```

## Лицензия

312.net — свободное программное обеспечение под лицензией [MIT](LICENSE). Использование, изменение и распространение разрешены при сохранении текста лицензии и уведомления об авторских правах. Self-hosted экземпляр не передаёт свои конфигурации, ключи и журналы участникам проекта.

## Полное удаление

Команда удаляет приложение, данные, конфигурацию и его системные службы. Системные пакеты сохраняются.

```bash
sudo vps-control uninstall --yes
```

## Ветки

- `test-light` — разработка и непубличные test-артефакты;
- `light` — production-версия Light;
- `installer` — единая публичная точка установки Light и PRO.
