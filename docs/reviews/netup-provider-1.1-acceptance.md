# NetUP Provider 1.1 — итог реализации и проверок

Дата: 2026-10-13. Ветка `codex/netup-provider-1.1-hardening` от Provider 1.0.
Решения владельца A1/B1/C приняты. Merge, push, deployment и изменение
рабочей PostgreSQL `oss_control` не выполнялись.

## R-01–R-11

| ID | Состояние | Доказательство и остаток |
|---|---|---|
| R-01 | Реализовано; PostgreSQL gate открыт | `0006_lookup_cleanup` вводит 24-часовой cutoff от `fetched_at`, ограниченный batch, `FOR UPDATE SKIP LOCKED`, повторный anti-join к tickets, FK и обезличенный append-only audit. `tests/test_lookup_lifecycle.py` проверяет старую схему, гонку и запрет прямого DELETE, но новый тест требует Docker PostgreSQL. Production требует выделенной maintenance-роли и невладеющей таблицами runtime-роли. |
| R-02 | Проверено локально | `NetupConfig` отвергает `lab`/plaintext при production APP_ENV; `tests/test_security_hardening.py` содержит оба отрицательных сценария. |
| R-03 | Проверено на локальном Uvicorn | `AccessPathFilter` удаляет query string до форматирования access log; реальный HTTP-тест с синтетическим ЛС прошёл. Внешние proxy logs вне контроля приложения. |
| R-04 | Реализовано; PostgreSQL gate открыт | `create_lookup` освобождает транзакцию перед provider и повторно блокирует/проверяет actor перед записью. Конкурентный тест смены роли с `lock_timeout` добавлен в `tests/test_billing.py`; ожидает PostgreSQL прогон. |
| R-05 | Проверено unit и UTM5 lab | Repository выполняет пакетные параметризованные запросы, лимиты строк, пулов, раскрытых диапазонов и response bytes; semaphore ограничивает 8 lookup на процесс. Unit test считает 9 data SELECT + 3 setup. Lab 6/6; на сценариях `multirow` 9→9, `multiple_links` 7→7 SELECT, форма данных совпала. На этих сценариях N+1 не проявлялся; p95/p99 не измерены. |
| R-06 | Реализовано; PostgreSQL gate открыт | `contacts[]` с типом, исходной колонкой, временем и quality; `0007_contacts` с `[]` для старых snapshots. Unit tests на конфликт, длинное значение, STALE и Mock прошли. HTTP/PG round-trip и RBAC тесты ждут PostgreSQL. |
| R-07 | Реализовано; полный HTTP gate открыт | Middleware ставит `Cache-Control: no-store` всем `/api/v1/`, кроме health. Негативные HTTP-тесты прошли; авторизованные list/detail/comments проверяются в новом PostgreSQL сквозном тесте. |
| R-08 | Отложено по решению владельца | Первый Web UI не отображает сырые DHCP ranges. До поэлементной provenance `IpGroupRow.field_meta['dhcp_ranges']` относится к массиву целиком; значения в snapshot сохраняются. |
| R-09 | Тест добавлен; PostgreSQL gate открыт | `test_synthetic_netup_http_snapshot_ticket_round_trip` проверяет HTTP lookup → snapshot → ticket → повторное чтение, контакты и связь service/group на вымышленных данных. Пока не выполнен из-за недоступного Docker socket. |
| R-10 | Проверено unit; структурный риск остаётся | `validate_snapshot_structure` вызывается единственным application writer до INSERT и отвергает чужую услугу, дубликат группы/строки и неверную связь service→account. Прямой SQL writer способен обойти JSONB-инварианты; такой путь запрещён контрактом разработки. |
| R-11 | Документы обновлены | `ADR-002`, API contract, migration/deployment/runbook/security/data limitations и acceptance criteria актуализированы; Discovery plan и прежний lab report отмечены как исторические. |

## Выполненные проверки

- Локально: Ruff check и format — успешно; `pip check` — успешно;
  `docker compose --env-file .env config --quiet` — успешно без вывода
  секретов; `pytest -q` — **46 passed, 48 skipped, 1 warning**. Пропуски:
  PostgreSQL и шесть lab tests без переменных lab в общем запуске.
- Отдельный read-only lab прогон с защищённым локальным `.env.netup-lab` и
  явным plaintext только для private lab: **6 passed**. Ни ЛС, ни PII, ни
  реквизиты не выводились.
- Реальное сравнение количества `SELECT` старого и нового repository на двух
  обезличенных lab сценариях: **9→9** и **7→7**. Набор счетов, услуг, групп и
  строк совпал. Крупного multi-account/multi-pool сценария в этих замерах
  не было; ограниченная выборка до 50 multi-account кандидатов не дала
  пригодного для сравнения случая. Улучшение на нём требует отдельного
  синтетического нагрузочного набора.
- `EXPLAIN` пакетного чтения услуг на лабораторной UTM5 использовал индекс
  `service_links.account_id` (`ref`, оценка 6 строк), затем PK каталога и
  периодической связки (`eq_ref`). Пакет IP-строк использовал индекс
  `ip_groups.ip_group_id` (`range`, оценка 2 строки), затем PK switches;
  оба плана содержали filesort для ORDER BY. Оценки не равны нагрузочному
  измерению; новые индексы UTM5 не создавались.
- Docker socket для `codexdev` отвечает `permission denied`; PostgreSQL
  интеграционные тесты 1.1 не выполнялись. Последний подтверждённый владельцем
  PostgreSQL результат Provider 1.0 — 39 passed, 6 skipped, 0 failed — не
  распространяется на новые миграции.

## Изолированный PostgreSQL прогон для администратора

В `/opt/oss-control-center` после проверки, что Compose project — именно
`oss-control-center` и файл `.env` защищён, выполнить без вывода `config`
или окружения:

```bash
docker compose --env-file .env config --quiet
docker compose --env-file .env build api
docker compose --env-file .env up -d postgres
docker compose --env-file .env run --rm --no-deps api ruff check app tests
docker compose --env-file .env run --rm --no-deps api ruff format --check app tests
docker compose --env-file .env run --rm --no-deps api python -m pip check
docker compose --env-file .env run --rm --no-deps -e RUN_DB_TESTS=1 api pytest -q --disable-warnings
```

`isolated_postgres_url` создаёт и удаляет только временные `oss_test_*` БД.
Команды не вызывают `alembic upgrade` для `oss_control` и не удаляют volume.
Нужно отдельно проверить `test_cleanup_migration_preserves_ticket_and_limits_deletion`,
`test_lookup_rechecks_role_after_remote_wait_without_holding_actor_lock`,
`test_synthetic_netup_http_snapshot_ticket_round_trip`, чистую БД до `head`
и обновление исторических snapshots до `0007`.

## Вердикт

**NOT READY для Web UI с реальным NetUP до повторного PostgreSQL прогона 1.1
и production review разделения DB-ролей, proxy logs и TLS-канала.**
Mock-based Web UI этим не блокируется. Новые секреты не создавались и не
менялись; реестр владельца и резервная копия секретов не затронуты.
