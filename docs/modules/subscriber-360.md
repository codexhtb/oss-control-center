# Subscriber 360

**Назначение.** Контекст абонента для сотрудника Service Desk: поиск, карточка, услуги/тарифы, финансовые сведения с разрешением, и previous tickets.

**Источники.** `BillingProvider` в `app/modules/billing/`; результат lookup валидируется и сохраняется в PostgreSQL `subscriber_snapshots` и `connection_snapshots`. UI использует снимок, а не повторные обращения к NetUP при рендере ранее полученной карточки.

**API:** `/api/v1/subscriber-lookups` (создать lookup по account), `/search`, `/{lookup_id}`, `/{lookup_id}/services`, `/{lookup_id}/tariff-history`, `/{lookup_id}/finance`, `/{lookup_id}/tickets`, `/{lookup_id}/active-tickets`.

**Права:** `subscribers.read`; финансовые маршруты дополнительно защищены `subscribers.finance.read`. Каждое чтение lookup ограничено автором согласно service policy; не обходить это требование endpoint-ом другого lookup.

**Workflow:** сотрудник ищет абонента, сохраняется provider snapshot и audit outcome; карточка показывает источник, время получения и качество/ограничения. Создание заявки из карточки переносит подтверждённый snapshot в контекст заявки.

**Ошибки и ограничения:** `BillingUnavailable`, ambiguity, incomplete/invalid data, unsupported account и not found не превращаются в нули или выдуманные значения. Данные snapshot имеют срок действия; текущая доступность и актуальность source всегда проверяются отдельно.

## Lookup и представление

Поиск возвращает ограниченных кандидатов по ФИО, логину и ЛС; числовой login не считается автоматически account ID. После выбора ЛС создаётся lookup/snapshot. UUID lookup другого сотрудника не даёт доступа.

Карточка разделяет контакты, services/tariffs, finance, billing access и monitoring. `int_status`/`is_blocked` сохраняются с source quality; неизвестный код не доказывает отсутствие блокировки. Связанные заявки ищутся по subscriber ID/source или account snapshot, не по имени/телефону, и проходят ticket RBAC.

| Endpoint | Назначение |
| --- | --- |
| `GET /search` | bounded candidates по введённому запросу |
| `POST /subscriber-lookups` | provider lookup и snapshot |
| `GET /{id}` | карточка сохранённого lookup |
| `GET /{id}/services`, `/tariff-history`, `/finance` | тематические блоки |
| `GET /{id}/tickets`, `/active-tickets` | обращения с ticket visibility |

Тестируйте ownership lookup, несколько accounts, expired snapshot, finance permission, source unavailable и отсутствие лишнего запроса NetUP при рендере уже сохранённой карточки.
