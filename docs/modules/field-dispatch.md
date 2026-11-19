# Field Dispatch и Telegram bot

**Назначение.** Инженер готовит выезд в карточке заявки и передаёт его в одну из настроенных групп либо публикует в Mini App. Telegram inbound обрабатывает отчёты/переносы; подтверждение работы делает инженер.

**Код:** `app/modules/field_dispatch/`; основные API под `/api/v1/tickets/{ticket_id}/field-work-request` и `/api/v1/field-work`; poller `python -m app.modules.field_dispatch.poller --forever` в отдельном container.

**Хранение:** `field_work_handoffs` содержит delivery/work states, direction, text/note and message bindings. Attempts, callbacks/updates, external messages, completion prompts, reschedules and notification outbox have separate rows and histories.

**Права:** подготовка/отправка ограничена ticket permissions (включая `tickets.field_work`); callback/poller дополнительно валидирует chat ID, message ID, author/update binding и status. Mini App identity/path описан отдельно.

**Состояния:** delivery `DRAFT/SENDING/SENT/FAILED/UNCERTAIN/PUBLISHED` отделён от work `AWAITING_REPORT/REPORTED/VERIFIED`. Переносы — отдельная история. `UNCERTAIN` требует явного безопасного reconciliation, не слепого повторного send.

**Poller:** единственный `getUpdates` consumer; он также drains personal notification outbox и synchronizes Telegram card. Не запускайте второй consumer и не отправляйте реальные сообщения в тестах. Подробности [операции Telegram](../operations/telegram-operations.md).

## Транзакции и восстановление

Draft хранит причину, note и утверждённый `message_text`. Send создаёт delivery attempt, переводит запись через `SENDING` и сохраняет Telegram message ID только после подтверждения. Timeout после запроса даёт `UNCERTAIN`: безопасное восстановление требует доказать отсутствие/наличие карточки, а не повторить вслепую.

Inbound связывает callback/reply с chat ID, message ID, handoff и автором. Completion prompt можно безопасно заменить; старый reply не должен завершить другой handoff. Report переводит только работу в `REPORTED`; verify — в `VERIFIED`. Group migration учитывается конфигурацией, не client payload.

Tests используют mock Bot API для 429, timeout, lost response, duplicate update, concurrent users и обеих directions. Рабочая группа не является test target.
