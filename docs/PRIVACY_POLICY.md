# Уведомление о приватности 312.net / Privacy Notice

Редакция / Effective date: 02.08.2026

312.net — свободное self-hosted программное обеспечение под лицензией MIT. Проект не предоставляет централизованный облачный сервис и не требует указания имени, адреса или иных юридических реквизитов автора в экземпляре приложения.

## RU

### 1. Self-hosted модель

Приложение устанавливается на VPS пользователя и работает под его управлением. Проект 312.net не имеет встроенного административного доступа к экземпляру, не получает его команды, конфигурации, ключи, список клиентов, журналы или трафик.

### 2. Данные внутри экземпляра

В зависимости от настроек приложение может локально обрабатывать:

- IP, имя, местоположение и характеристики VPS;
- учётные данные администратора и события входа;
- состояние служб, метрики, сетевые сведения и журналы безопасности;
- имена профилей и клиентов, адреса, публичные ключи, endpoints, handshake и статистику трафика;
- команды, результаты диагностики и настройки автоматизации.

Эти данные хранятся на VPS и в браузере администратора. Учётные данные панели сохраняются в `sessionStorage` вкладки. Закрытые ключи и пароли не следует публиковать или передавать в открытых обращениях.

### 3. Внешние соединения

Для установки и обновлений приложение может обращаться к GitHub и репозиториям пакетов. Функции GeoIP и диагностики могут обращаться к внешним проверочным адресам. Такие сервисы получают IP сервера и обычные технические данные запроса и применяют собственные политики. В продукте нет рекламных SDK, аналитических трекеров или встроенной отправки содержимого экземпляра участникам проекта.

### 4. Ответственность администратора

Пользователь, администратор или организация, управляющие экземпляром, самостоятельно определяют цели и способы локальной обработки. Они отвечают за законное основание, уведомления, сроки хранения, доступ, безопасность, резервные копии, получателей, международные передачи и обработку запросов субъектов, если такие обязанности применимы.

Это уведомление описывает продукт, но не заменяет индивидуальную политику администратора. Если экземпляр используется для обработки персональных данных других лиц, администратор должен подготовить собственное уведомление с необходимыми контактами и сведениями по применимому праву.

### 5. Добровольные обращения

Если пользователь самостоятельно публикует issue, discussion, pull request или иной материал на внешней платформе, данные обрабатываются этой платформой по её правилам. Не следует включать в обращения пароли, закрытые ключи, конфигурации клиентов, журналы с персональными данными или иные секреты.

### 6. Запросы и изменения

Запросы о данных конкретного self-hosted экземпляра направляются его администратору: участники проекта технически не имеют к ним доступа. Обновления этого уведомления публикуются вместе с исходным кодом. История Git позволяет определить текст, действовавший в конкретной версии.

## EN

### 1. Self-hosted model

312.net is free self-hosted software licensed under the MIT License. It does not provide a central cloud service and does not require an author’s legal name, address or other legal particulars to be displayed in an instance.

The application is installed on and controlled by the user’s VPS. The 312.net project has no built-in administrative access and does not receive instance commands, configurations, keys, client lists, logs or traffic.

### 2. Data inside an instance

Depending on configuration, the application may locally process:

- VPS IP, name, location and technical characteristics;
- administrator credentials and login events;
- service state, metrics, network information and security logs;
- profile and client names, addresses, public keys, endpoints, handshakes and traffic statistics;
- commands, diagnostic results and automation settings.

This data remains on the VPS and in the administrator’s browser. Panel credentials are stored in the tab’s `sessionStorage`. Private keys and passwords should never be included in public requests.

### 3. External connections

Installation and updates may contact GitHub and package repositories. GeoIP and diagnostic functions may contact external test endpoints. Those services receive the server IP and ordinary request metadata and apply their own policies. The product contains no advertising SDK, analytics tracker or built-in transmission of instance content to project contributors.

### 4. Administrator responsibility

The user, administrator or organisation controlling an instance determines the purposes and means of local processing. It is responsible for legal bases, notices, retention, access, security, backups, recipients, international transfers and data-subject requests where applicable.

This notice describes the product and does not replace the administrator’s own notice. An administrator processing other persons’ data must provide the identity, contact and other information required by applicable law in its own documentation.

### 5. Voluntary submissions

If a user submits an issue, discussion, pull request or other material through an external platform, that platform processes the submission under its own rules. Passwords, private keys, client configurations, personal-data logs and other secrets must not be submitted.

### 6. Requests and changes

Requests concerning a particular self-hosted instance must be sent to its administrator because project contributors have no technical access to that data. Updates to this notice are published with the source code, and Git history identifies the text applicable to each version.
