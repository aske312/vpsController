# Проверки

`npm test` запускает web/contracts без сборки. Для изменений интерфейса также
выполнить `npm run typecheck` и `npm run lint`. Linux-архив готовит
`bash scripts/build-release.sh /путь/к/release.tar.gz`.

## Python

В отдельной копии репозитория создать окружение:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tests/requirements.txt
.venv/bin/python -m unittest discover -s tests/api -t . -v
```

Обычные API-тесты используют временные данные и подмену системных команд.
Интеграционные проверки включаются переменными с абсолютными путями к ядрам:
`PRIVACY_CADDY_BIN`, `PRIVACY_OPENSSL_BIN`, `PRIVACY_XRAY_BIN`,
`PRIVACY_MIHOMO_BIN`, `PRIVACY_SINGBOX_BIN`, `PRIVACY_SINGBOX_LX_BIN`.
Отсутствующие ядра означают пропуск соответствующих проверок, а не успех.
Трафик интеграционных сценариев проходит через временные loopback-сервисы.

## Linux/systemd

Выполнять на выделенном тестовом сервере под root. Проверки создают временные
unit с уникальными именами и затем удаляют их. Применяется настоящий `systemctl`;
установленные службы панели и протоколов не используются как тестовые экземпляры.

Пример защищённого запуска двух наборов из отдельной копии репозитория:

```bash
mkdir -p .runtime/test-tmp
systemd-run --wait --pipe --collect \
  --property=Type=exec \
  --property=RuntimeMaxSec=180 \
  --property=ProtectSystem=strict \
  --property="ReadWritePaths=$PWD /run/systemd/system" \
  --property="WorkingDirectory=$PWD" \
  --setenv=PRIVACY_SYSTEMD_TESTS=1 \
  --setenv=PYTHONDONTWRITEBYTECODE=1 \
  --setenv="TMPDIR=$PWD/.runtime/test-tmp" \
  "$PWD/.venv/bin/python" -m unittest -v \
  tests.api.test_application_operation_systemd \
  tests.api.test_shadowsocks_systemd
```

`TMPDIR` должен быть доступен и процессам тестов, и тестовым systemd-unit.
`PrivateTmp=yes` здесь не подходит: созданные через systemd экземпляры не увидят
файлы из приватного `/tmp` родительского теста.

Покрытие этих наборов:

- допуск операции и блокировка между независимыми процессами;
- повтор с тем же ID без второго запуска;
- подтверждение выхода worker после записи успешного результата;
- прерванный worker без ложного успеха;
- явное принятие старой установки с приватной копией и без перезапуска;
- сохранение открытого Shadowsocks-соединения при принятии и откате;
- восстановление active/enabled/runtime/disabled состояний экземпляров.

Для проверки реального Shadowsocks-трафика нужны `ss-server` и `ss-local`.
Тесты прочих платформ пропускают systemd-сценарии. Успех на одной ОС/архитектуре
не заменяет матрицу чистой установки, обновления и восстановления поддерживаемых
Debian/Ubuntu и amd64/arm64.
