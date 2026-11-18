# Service Desk

**Назначение.** Система обращений и инженерного решения. Основной код: `app/modules/tickets/`, API `app/api/v1/tickets.py`, UI `TicketListPage`, `TicketDetailPage`, `TicketCreatePage`.

**Типы:** `SUBSCRIBER_LINKED`, `MANUAL`, `NEW_CONNECTION`, `INTERNAL`, `ERRONEOUS_PAYMENT`. Типы имеют отдельные обязательные поля и правила связи. Не выводите наличие абонентской привязки по имени или телефону.

**Хранение.** `tickets` и связанные PostgreSQL-модели: snapshots, `ticket_events`, `ticket_audit`, версии решений, comments, callbacks, assignments, internal tasks и field handoffs. Alembic revisions расположены в `app/db/migrations/versions/`.

**Основной API:** `/api/v1/tickets` (список, detail, create/edit, assignment/claim, comments, decisions, callbacks, history/audit, trash/restore/purge); предыдущие связанные заявки — `/api/v1/tickets/{id}/previous-tickets`.

**Права.** `tickets.read.all` / `tickets.read.own`, `tickets.create`, `tickets.edit.*`, `tickets.assign`, `tickets.start`, `tickets.resolve`, `tickets.close`, `tickets.callback`, `tickets.comment`, `tickets.field_work`, `tickets.diagnose`, `audit.read`, delete/restore permissions. Backend применяется к каждой операции и связанным endpoint. Внутренние заявки закрыты от пользователей без разрешённого доступа, включая агрегаты и связанные данные.

**Workflow.** Заявка проходит `OPEN → IN_PROGRESS → RESOLVED → CLOSED` с допустимыми возвратами по callback workflow. Назначение и решение — отдельные действия. История версий и аудит сохраняются. Field Work/Telegram не закрывает заявку.

**Ошибки и ограничения.** API различает отсутствие, конфликт состояния, недопустимые данные и отсутствие права. Проверяйте response contract в тестах. Не меняйте старые migration revisions; новый schema change — следующая migration.
