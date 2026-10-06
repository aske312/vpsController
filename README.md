# 312.net VPS Controller

312.net — self-hosted панель управления Linux VPS. **Light** предлагает компактный набор основных функций, а **PRO** — расширенное управление, автоматизацию и дополнительные средства диагностики.

## Требования

- Debian 13 или Ubuntu Server 22.04/24.04 для Light;
- Debian 12/13 или Ubuntu Server 22.04/24.04/26.04 для PRO;
- архитектура `amd64` или `arm64`;
- от 1 ГБ оперативной памяти и 5 ГБ свободного места;
- вход на сервер под `root`;
- доступ к GitHub и системным репозиториям.

## Установка

```bash
curl -fsSL https://raw.githubusercontent.com/aske312/vpsController/installer/install.sh | bash -s -- --edition light
```

Для установки другой редакции замените `light` на `pro`. После завершения установщик покажет адрес панели и данные для входа.

## Основные команды

Назначить домен панели Light:

```bash
vps-control domain panel.example.com
```

Повторно определить адрес и расположение сервера:

```bash
vps-control identity
```

Установить актуальную стабильную версию приложения:

```bash
vps-control update
```

## Лицензия

Проект распространяется по лицензии [MIT](LICENSE).
