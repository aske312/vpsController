# План проверки единого установщика и редакций

План выполняется после публикации локально подготовленных веток. До начала проверки сохранить commit SHA всех пяти веток и не назначать `installer` default-веткой, пока обязательные сценарии не пройдены.

## 1. Стенды

Обязательные чистые VPS:

| Стенд | ОС | Архитектура | Редакция |
|---|---|---|---|
| L-A | Debian 13 | amd64 | Light |
| L-R | Debian 13 | arm64 | Light |
| P-A | Debian 13 | amd64 | PRO |
| P-R | Debian 13 | arm64 | PRO |

Дополнительно выполнить smoke-test Light и PRO хотя бы на одном Ubuntu Server 22.04/24.04 `amd64`.

Для каждого стенда заранее сохранить:

```bash
uname -a
dpkg --print-architecture
cat /etc/os-release
free -h
df -h /
```

## 2. Проверка веток и GitHub Actions

1. Опубликовать `pro`, `test-pro`, `light`, `test-light`, `installer` без удаления старых веток.
2. Убедиться, что push в `pro` создаёт `pro-latest` с двумя assets:
   - `vps-control-pro-linux-amd64.tar.gz`;
   - `vps-control-pro-linux-arm64.tar.gz`.
3. Убедиться, что push в `light` создаёт `light-latest` с двумя assets:
   - `vps-control-light-linux-amd64.tar.gz`;
   - `vps-control-light-linux-arm64.tar.gz`.
4. Проверить независимые immutable-теги `pro-v*` и `light-v*`.
5. Убедиться, что workflows `test-pro` и `test-light` создают только временные CI artifacts.
6. Убедиться, что test-workflows не имеют `contents: write` и не создают GitHub Releases.

Для каждого архива распаковать только metadata:

```bash
tar -xOf ARCHIVE.tar.gz vps-control-release/.prebuilt-release
```

Ожидается:

- `schema=1`;
- правильные `edition` и `channel`;
- полный 40-символьный `commit`;
- правильная `architecture`;
- `platform=linux`.

## 3. Проверка единого установщика

На отдельном временном Debian 13 проверить справку и ошибки параметров:

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh -o /root/install.sh
bash /root/install.sh --help
bash /root/install.sh --edition unknown
```

Ожидается: справка завершается с кодом 0, неизвестная редакция — с ненулевым кодом до изменения системы.

На стендах выполнить прямые команды:

```bash
# Light
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | sudo bash -s -- --edition light

# PRO
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh \
  | sudo bash -s -- --edition pro
```

Отдельно один раз запустить установщик без `--edition` и проверить интерактивный выбор через `/dev/tty`.

## 4. Проверка после чистой установки

На каждом стенде:

```bash
sudo vps-control status
sudo vps-control verify
sudo vps-control integrity-check
sudo systemctl --no-pager --full status vps-control-api vps-control-web caddy
sudo cat /var/lib/vps-control/product.json
```

Ожидается:

- все обязательные службы активны;
- `verify` и `integrity-check` успешны;
- Light содержит `"edition": "light"`;
- PRO содержит `"edition": "pro"`;
- файл `product.json` принадлежит root и имеет права `0600`;
- Light показывает только WG/AWG;
- PRO сохраняет Mihomo, DNS, relay и остальные production-возможности ветки `pro`.

Дополнительно проверить вход в панель, смену административного пароля и сохранение настроек после перезапуска.

## 5. Production-обновление

1. Зафиксировать текущий commit:

```bash
sudo cat /opt/vps-control/.build-commit
```

2. Опубликовать следующий тестовый production-commit соответствующей редакции.
3. Выполнить:

```bash
sudo vps-control update
sudo vps-control verify
sudo vps-control integrity-check
```

4. Убедиться, что установлен полный SHA нового commit, пользовательские VPN-конфигурации и ключи не изменились.
5. Повторный `vps-control update` должен сообщить, что установлена актуальная версия, и не переустанавливать приложение.

## 6. Запрет смешивания редакций

На Light передать production-архив PRO:

```bash
sudo vps-control install-release /root/vps-control-pro-linux-ARCH.tar.gz
```

На PRO передать production-архив Light. В обоих случаях ожидается отказ до остановки рабочих служб. После отказа выполнить `vps-control verify` и подтвердить, что установленный commit не изменился.

Также проверить отказ от:

- архива другой архитектуры;
- test-архива через `install-release`;
- архива с изменённым `edition`;
- архива с сокращённым или неверным commit;
- архива с повреждённым файлом после формирования `release.sha256`.

## 7. Локальная test-сборка и rollback

Скачать CI artifact соответствующей редакции, передать его по SCP и выполнить:

```bash
sudo vps-control service-mode enable
sudo vps-control test-update /root/TEST_ARCHIVE.tar.gz
sudo vps-control verify
sudo vps-control test-rollback
sudo vps-control verify
```

Ожидается:

- без сервисного режима `test-update` отклоняется;
- test-архив другой редакции отклоняется;
- production-версия сохраняется до переключения;
- после test-update работает test commit той же редакции;
- `test-rollback` возвращает исходный production commit;
- настройки, клиенты, ключи и сетевые интерфейсы сохраняются.

## 8. Проверка автоматического отката

Подготовить test-архив с намеренно неработающим health-check или сервисом и установить через `test-update`.

Ожидается:

- установка завершается ошибкой;
- предыдущий каталог приложения восстанавливается;
- API, web и Caddy снова активны;
- панель доступна;
- `product.json` не меняет редакцию.

Повторить аналогичный сценарий для production-архива через `install-release` на одном стенде каждой редакции.

## 9. Устойчивость установки

Проверить:

- повторный запуск после разрыва SSH;
- занятый HTTP-порт;
- временную недоступность GitHub/DNS;
- блокировку apt/dpkg;
- нехватку диска;
- повторный запуск установщика поверх уже установленной той же редакции;
- попытку запустить установщик другой редакции поверх существующей.

После каждого отказа рабочая версия и VPN-соединения должны оставаться доступными либо должна выводиться однозначная инструкция восстановления.

## 10. Критерий завершения

Миграция готова к включению, если:

- все четыре обязательных Debian-стенда прошли чистую установку;
- оба production-релиза содержат корректные assets;
- обновление работает внутри каждой редакции и архитектуры;
- cross-edition, cross-channel и cross-architecture архивы отклоняются;
- локальная test-сборка и rollback работают для Light и PRO;
- неудачная установка автоматически восстанавливает рабочую версию;
- test-ветки не публикуют Releases;
- после проверок не обнаружено потери VPN-конфигураций, клиентов или ключей.

После этого можно назначить `installer` default-веткой и отдельно удалить старые `main`, `stabl` и устаревшие release-теги.
