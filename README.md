# 312.net

312.net — панель управления Ubuntu-сервером. Она показывает состояние ресурсов, служб, сети и безопасности, а также позволяет выполнять диагностику, обновление и обслуживание приложения.
## Требования

- Ubuntu Server 22.04/24.04/26.04 или Debian 12/13 с systemd;
- amd64 или arm64;
- от 1 ГБ RAM и 5 ГБ свободного места;
- доступ к репозиториям Ubuntu и GitHub;
- root или пользователь с `sudo`.

## Установка на новый сервер

Установка рассчитана на чистую Ubuntu Server 22.04, 24.04, 26.04 или Debian 12/13 с systemd. Она добавляет Caddy, Node.js 22, Python 3 с venv, Git, UFW, OpenSSH, Fail2ban, auditd, unattended-upgrades и остальные необходимые системные пакеты. Если системный репозиторий уже содержит Node.js 22 (например, Ubuntu 26.04), используется штатный пакет. На Debian 12/13 установщик при необходимости подключает NodeSource 22 как fallback. Docker не используется.

### Быстрая установка

Минимальные образы серверов могут не содержать ни `curl`, ни `sudo`. Если вы вошли как `root`, сначала подготовьте загрузчик, затем запустите установщик:

```bash
apt-get update && apt-get install -y ca-certificates curl
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/stabl/scripts/install-panel.sh | bash
```

Если вы вошли как обычный пользователь с настроенным `sudo`:

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/stabl/scripts/install-panel.sh | sudo bash
```

Установщик сам проверит сервер, установит зависимости, определит IPv4/IPv6, настроит firewall и HTTPS, запустит службы и проверит результат. Он покажет адрес панели, логин и созданный случайный пароль, а после основной установки автоматически сверится с `stabl-latest` и перейдёт на подготовленный стабильный релиз. Отдельно запускать `vps-control update` не требуется.

### Свои логин, пароль или домен

Если стандартного автоматического режима недостаточно, используйте файл [`install.conf`](install.conf):

```bash
git clone --branch stabl https://github.com/aske312/vpsController.git
cd vpsController
sudo bash scripts/install-panel.sh
```

Обычно достаточно изменить только `ADMIN_USER`, `ADMIN_PASSWORD` и при необходимости `PUBLIC_DOMAIN`.

IPv4 и IPv6 определяются автоматически. Произвольный домен определить невозможно: установщик может использовать только домен из `install.conf` или корректную PTR-запись. Для HTTPS домен должен указывать на сервер, а входящие TCP-порты 80 и 443 должны быть разрешены во внешнем firewall хостинга. Без подтверждённого домена панель запускается по HTTP на порту 8080.

Проверка после установки:

```bash
sudo vps-control status
sudo vps-control verify
sudo systemctl --no-pager --full status vps-control-api vps-control-web caddy
```

### Возможные ошибки установки

- Установка прервалась: запустите ту же команду ещё раз. Уже выполненные этапы и настройки будут сохранены.
- `curl: command not found` или `sudo: command not found`: используйте подходящий для `root` или sudo-пользователя вариант из раздела быстрой установки выше.
- Домен не открылся по HTTPS: проверьте его A/AAAA-записи и входящие TCP 80/443 во внешнем firewall хостинга.
- Не хватает ресурсов: серверу нужны минимум 1 ГБ RAM и 5 ГБ свободного места на диске `/opt`.
- Для предварительной проверки без развёртывания добавьте `VPS_CONTROL_PREFLIGHT_ONLY=yes` перед `bash`.
- Диагностика: `sudo vps-control verify`. Журналы: `/tmp/vps-control-bootstrap.log` и `/var/log/vps-control-install.log`.

## Ручное обновление без сборки на VPS

Релиз собирается заранее на Linux-машине или в CI. На VPS не запускаются Docker, `npm ci`, `npm build`, `apt upgrade` или очистка системных пакетов.

```bash
bash scripts/build-release.sh outputs/vps-control-release.tar.gz
scp outputs/vps-control-release.tar.gz root@SERVER:/root/
ssh root@SERVER 'vps-control install-release /root/vps-control-release.tar.gz'
```

Архив содержит готовые `dist` и Linux runtime-зависимости, а также файл контрольных сумм. Перед переключением сервер проверяет архив полностью. При ошибке запуска выполняется откат к предыдущему каталогу приложения. `/etc/wireguard`, `/etc/amnezia`, клиенты, ключи, сетевые интерфейсы и системные пакеты не изменяются. Кнопка обновления и команда `vps-control update` ждут публикации подготовленного релиза `stabl-latest`, сверяют его commit с актуальной веткой `stabl` и только после этого один раз скачивают архив и запускают тот же безопасный установщик.

### Стабильный и тестовый каналы

`stabl` — основной канал эксплуатации. Его готовый архив публикуется как
`stabl-latest`, а обычное обновление выполняется командой:

```bash
sudo vps-control update
```

`main` — часто обновляемая тестовая сборка. Каждый проверенный commit публикуется
отдельно от стабильного релиза как `main-latest`. Для безопасного перехода сначала
включается сервисный режим: панель сохраняет текущую стабильную сборку, временно
обеспечивает внешний доступ к панели и SSH, затем устанавливает `main` с проверкой
API и Web. При ошибке запуска автоматически возвращается предыдущая версия.

```bash
sudo vps-control service-mode enable
sudo vps-control test-update
```

Вернуться к сохранённой стабильной сборке:

```bash
sudo vps-control test-rollback
sudo vps-control service-mode disable
```

Выключение сервисного режима само выполняет rollback, если тестовая сборка ещё
активна, и восстанавливает прежние настройки панели, SSH, firewall и таймеров.
Пароль администратора при переключении каналов не меняется. Текущие реквизиты
можно проверить локально на сервере командой `sudo vps-control credentials`.

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

Команда доступна только администратору сервера и повторно показывает фактические логин и пароль из `/etc/vps-control.env`. Значения в `install.conf` являются входными настройками установки; действующие учётные данные всегда хранятся в защищённом env-файле.

## Лицензия

312.net — свободное программное обеспечение под лицензией [MIT](LICENSE). Использование, изменение и распространение разрешены при сохранении текста лицензии и уведомления об авторских правах. Self-hosted экземпляр не передаёт свои конфигурации, ключи и журналы участникам проекта.

## Полное удаление

Команда удаляет приложение, данные, конфигурацию и его системные службы. Системные пакеты сохраняются.

```bash
sudo vps-control uninstall --yes
```

## Ветки

- `main` — разработка;
- `stabl` — стабильная версия для установки и обновлений.

## Разработка и проверка

Интерфейс разделён по рабочим областям в `app/views`. Общие типы панели находятся
в `app/types/control-plane.ts`, а форматирование, подписи и порядок навигации — в
`app/lib/control-plane-ui.ts`. `app/page.tsx` связывает экраны с API и управляет
общим состоянием приложения.

Перед отправкой изменений выполните:

```bash
npm test
npm run lint
```

`npm test` сначала собирает production-версию интерфейса, затем запускает
продуктовые проверки установщика, API, протокольных образов, документации и всех
UI-модулей. Проверки интерфейса автоматически читают TypeScript-файлы каталога
`app`, поэтому перенос экрана из `app/page.tsx` в отдельный компонент не меняет
тестовый контракт.

GitHub Actions запускает lint и продуктовые проверки до публикации обоих каналов.
Непрошедший проверку commit не заменяет ни `stabl-latest`, ни `main-latest`.
