# ADR-003: Независимость заявки и выездной работы

**Статус:** принято по моделям и API. **Дата фиксации:** 2026-11-18.

## Решение

Ticket status, Telegram delivery status и Field Work status — независимые поля/состояния. Отчёт монтажника фиксирует работу по конкретному handoff; engineer verification отдельна. Изменение Field Work не закрывает ticket автоматически.

## Основание

`TicketStatus`, `FieldWorkHandoff.delivery_status` и `work_status` заданы раздельно в backend; API, callbacks и tests соблюдают эти переходы. Историческая мотивация отдельно не записана.
