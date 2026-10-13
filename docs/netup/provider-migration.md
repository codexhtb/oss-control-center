# Миграция и откат NetUP provider 1.0

`0005_netup_groups` следует после `0004_subscriber_360`; старые миграции не
изменены. Новые поля: `subscriber_snapshots.actual_address` nullable,
`accounts JSONB NOT NULL DEFAULT []`; `connection_snapshots.service_id`
nullable, `ip_groups JSONB NOT NULL DEFAULT []`. Индексы UTM5 не создаются.

Порядок: резервная копия PostgreSQL штатным способом → проверка `alembic
current` (ожидается 0004) → `alembic upgrade head` → проверка 0005 и чтения
существующих snapshots → тест создания/чтения mock lookup → отдельные lab
проверки NetUP → только потом явный выбор `BILLING_PROVIDER=NETUP`.
Миграция добавочная; старые строки получают `[]`/`NULL`, без фиктивных
значений. Immutable trigger защищает и новые колонки.

При отказе NetUP вернуть `BILLING_PROVIDER=MOCK` можно только как явное
операционное решение для dev/тестовой среды; в production это изменяет
источник данных и требует ревью. Простой откат режима работы — оставить
схему 0005 и отключить NetUP. `alembic downgrade 0004_subscriber_360`
удаляет новые поля и приведёт к потере новых snapshot сведений; разрешать
его лишь после отдельного экспорта/анализа и при отсутствии новых заявок,
ссылающихся на них. Старые snapshots и заявки не переписываются.
