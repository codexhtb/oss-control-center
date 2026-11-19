# ADR-001: PostgreSQL OCC — источник истины

**Статус:** принято по текущему коду. **Дата фиксации:** 2026-11-18.

## Решение

Заявки, решения, events, audit, comments, tasks, staff chat, snapshots и Field Dispatch workflow хранятся в PostgreSQL OCC. История версий и изменения состояния пишутся транзакционно в приложении.

## Наблюдаемое следствие

NetUP/LibreNMS/BDCOM/Telegram используются как input/transport; они не являются master storage заявок. Внешний snapshot может устареть и содержит source/time/quality метаданные.

## История мотивации

Старая Phase 0 архитектурная запись сохранена в `docs/archive/architecture/`. Отдельный первичный decision record с мотивацией этого решения не найден; настоящая запись фиксирует только кодовый контракт.
