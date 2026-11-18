# Subscriber 360

**Назначение.** Контекст абонента для сотрудника Service Desk: поиск, карточка, услуги/тарифы, финансовые сведения с разрешением, и previous tickets.

**Источники.** `BillingProvider` в `app/modules/billing/`; результат lookup валидируется и сохраняется в PostgreSQL `subscriber_snapshots` и `connection_snapshots`. UI использует снимок, а не повторные обращения к NetUP при рендере ранее полученной карточки.

**API:** `/api/v1/subscriber-lookups` (создать lookup по account), `/search`, `/{lookup_id}`, `/{lookup_id}/services`, `/{lookup_id}/tariff-history`, `/{lookup_id}/finance`, `/{lookup_id}/tickets`, `/{lookup_id}/active-tickets`.

**Права:** `subscribers.read`; финансовые маршруты дополнительно защищены `subscribers.finance.read`. Каждое чтение lookup ограничено автором согласно service policy; не обходить это требование endpoint-ом другого lookup.

**Workflow:** сотрудник ищет абонента, сохраняется provider snapshot и audit outcome; карточка показывает источник, время получения и качество/ограничения. Создание заявки из карточки переносит подтверждённый snapshot в контекст заявки.

**Ошибки и ограничения:** `BillingUnavailable`, ambiguity, incomplete/invalid data, unsupported account и not found не превращаются в нули или выдуманные значения. Данные snapshot имеют срок действия; текущая доступность и актуальность source всегда проверяются отдельно.
