# Field Dispatch и Telegram bot

**Назначение.** Инженер готовит выезд в карточке заявки и передаёт его в одну из настроенных групп либо публикует в Mini App. Telegram inbound обрабатывает отчёты/переносы; подтверждение работы делает инженер.

**Код:** `app/modules/field_dispatch/`; основные API под `/api/v1/tickets/{ticket_id}/field-work-request` и `/api/v1/field-work`; poller `python -m app.modules.field_dispatch.poller --forever` в отдельном container.

**Хранение:** `field_work_handoffs` содержит delivery/work states, direction, text/note and message bindings. Attempts, callbacks/updates, external messages, completion prompts, reschedules and notification outbox have separate rows and histories.

**Права:** подготовка/отправка ограничена ticket permissions (включая `tickets.field_work`); callback/poller дополнительно валидирует chat ID, message ID, author/update binding и status. Mini App identity/path описан отдельно.

**Состояния:** delivery `DRAFT/SENDING/SENT/FAILED/UNCERTAIN/PUBLISHED` отделён от work `AWAITING_REPORT/REPORTED/VERIFIED`. Переносы — отдельная история. `UNCERTAIN` требует явного безопасного reconciliation, не слепого повторного send.

**Poller:** единственный `getUpdates` consumer; он также drains personal notification outbox и synchronizes Telegram card. Не запускайте второй consumer и не отправляйте реальные сообщения в тестах. Подробности [операции Telegram](../operations/telegram-operations.md).
