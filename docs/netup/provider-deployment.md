# Развёртывание NetUP provider: отдельный read-only канал

**Не включено в текущем dev stack.** Исследовательский `netup-sql` wrapper
используется только для безопасных Discovery SQL и не является runtime
подключением API. `compose.yaml` не передаёт реквизиты NetUP и оставляет
`BILLING_PROVIDER=MOCK`. Отдельное включение требует согласования с владельцем
UTM5 и сетевой политики, без административных учётных записей.

## Требования к production каналу

- Отдельный SQL user с минимальными **column-level `SELECT`** на подтверждённых таблицах
  `users`, `accounts`, `users_accounts`, `service_links`, `services_data`,
  `periodic_service_links`, `iptraffic_service_links`, `ip_groups`, `switches`,
  `dhcp_pool_info`, `dhcp_pool_ranges`; без DDL/DML, журналов и столбцов паролей.
  Команды для lab администратора — [`lab-access-admin.md`](lab-access-admin.md).
- Для production TLS с проверкой сертификата и имени сервера. В отдельной
  изолированной лаборатории допускается plaintext только при
  `NETUP_PURPOSE=lab`, `NETUP_ALLOW_PLAINTEXT_LAB=true` и private IP endpoint.
  Этот режим никогда не является production конфигурацией. Firewall/маршрут
  к согласованному lab endpoint создаются отдельно администратором.
- Секреты только через runtime secret environment/secret store; не через Git,
  Compose labels, параметры CLI или HTTP. Выделенный канал должен быть
  проверен на SELECT grants и ограничение соединений.

Переменные runtime: `BILLING_PROVIDER=NETUP`, `NETUP_HOST`, `NETUP_PORT`
(3306), `NETUP_DATABASE` (`UTM5`), `NETUP_USER`, `NETUP_PASSWORD`,
`NETUP_TLS_CA`, `NETUP_CONNECT_TIMEOUT` (3 с), `NETUP_READ_TIMEOUT` (5 с),
`NETUP_MAX_EXECUTION_MS` (3000). Не выводить их значения в логах и отчётах.
Опциональная зависимость `PyMySQL` включена в Dockerfile, но соединение
создаётся только при NetUP lookup после явного выбора провайдера.

Для отдельной лаборатории тесты используют **другой** префикс
`NETUP_LAB_*` и флаг `RUN_NETUP_LAB_TESTS=1`; при отсутствии lab CA задать
`NETUP_LAB_ALLOW_PLAINTEXT=true` только для private IP. Переменные не задаются в
обычном dev `.env`. Тесты не пишут реальные строки в stdout, snapshots или
fixtures. Исследовательский wrapper не используется в runtime. Отдельный TCP
канал и лабораторная учётная запись доступны; 6 integration tests прошли.
Лабораторная MariaDB не поддерживает TLS; тесты явно выбирают `purpose=lab`
и plaintext на private IP. Docker socket остаётся недоступен `codexdev`;
проект эти системные права не меняет.

Для копии от 2026-10-10 установить `NETUP_LAB_AS_OF_UTC` внутри этого дня
(например, 12:00 UTC) только в lab тестах. Фильтр действующих услуг тогда
проверяет дату копии; метаданные возвращённых полей получают `STALE`, а
`fetched_at` фиксирует фактическое время чтения. Production режим не
принимает исторический `as_of`.

Порядок будущего включения: проверить миграцию 0005 на копии PostgreSQL,
grants/TLS/EXPLAIN для production канала, повторить lab тесты
и тесты без вывода PII, затем согласовать production реквизиты и трафик.
Никакого автоматического fallback на mock для недоступного NetUP нет:
выдаётся `503`, чтобы не создать ложную карточку.

Повторяемые планы чтения: [`80_provider_explain.sql`](sql/80_provider_explain.sql).
На копии `accounts.id` использует `PRIMARY/const`, услуги — индекс
`service_links.account_id` и PK каталога/периодической связки, IP-строки —
индекс `ip_groups.ip_group_id`. Поиск owner по ЛС не имеет индекса с ведущим
`users_accounts.account_id`; с `ORDER BY/LIMIT 2` план идёт по PK `users`
и `eq_ref` составного индекса связи, с временной дедупликацией `DISTINCT`.
Оценка EXPLAIN `rows=2` не доказывает
короткий проход, когда у счёта один owner; худший случай — просмотр около
5 тыс. пользователей. Отдельный lab индекс можно исследовать после
измерения, но provider не изменяет схему UTM5.
