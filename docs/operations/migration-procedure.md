# Alembic migration procedure

## До изменения

- Подтвердить целевой Compose project/service labels и рабочую DB; локальный branch name или DNS не являются достаточным доказательством.
- Read-only проверить `alembic current`, `alembic heads`, migration graph, backup integrity и успешное восстановление на изолированной копии.
- Проверить `down_revision`, upgrade/downgrade semantics, locks, data volume, check constraints, runtime compatibility и код, требующий новую схему.
- Выполнить upgrade на disposable PostgreSQL, тесты приложения, затем подготовить окно и владельца отката.

## Применение

Только отдельное явное разрешение владельца допускает запись в рабочую БД. Развёртывание API, ожидающего неприменённую migration, запрещено. Не запускайте миграцию из случайного локального контейнера. Выполняйте только выбранную последовательность через утверждённый release procedure; exact production command **UNVERIFIED** до проверки deployment wrapper.

## После

Read-only сверить revision, базовые counts без PII, health, API compatibility и журналы. При ошибке не запускать автоматический downgrade; остановить release и согласовать восстановление. Никогда не удалять volume или старые rows.
