# Актуальные API-контракты

Контракт сверён с роутерами `app/api/v1/` 19.11.2026. Историческая спецификация сохранена в [архиве](../archive/architecture/api-contracts.md), но не является источником текущего поведения. Все маршруты имеют префикс `/api/v1`.

## Общие правила

- Административный API использует серверную сессию; мутации проходят действующую Origin/CSRF-проверку и backend RBAC.
- `ApiError` содержит HTTP status, стабильный машинный `code` и `message`. Клиент не определяет ошибку по тексту.
- Страничные ответы содержат `items`, `page`, `size`, `total`. Экспорт не ограничивается экранной страницей.
- Мутации заявки, где это предусмотрено, требуют `If-Match` с текущей версией. Конфликт нельзя автоматически перезаписывать.
- `client_request_id` защищает идемпотентные операции. Повтор не создаёт вторую сущность.
- `404` может скрывать существование недоступной заявки. Скрытие элемента в UI не заменяет серверную проверку.
- `/health/live` проверяет процесс, `/health/ready` — готовность приложения, но не всех внешних систем.

## Группы маршрутов

| Префикс | Основные операции | Контроль доступа |
| --- | --- | --- |
| `/auth` | login/logout, me, permissions, password | сессия; login защищён rate limit |
| `/users` | CRUD, grants, passwords, activation, audit | `users.read`; мутации — `users.manage` |
| `/tickets` | create/list/detail/edit, assignment, workflow, trash | семейство `tickets.*`, `read.all`/`read.own` |
| `/tickets/{id}` | comments, decisions, callbacks, history/audit, field work | право операции и ticket visibility |
| `/subscriber-lookups` | search, lookup, services, tariffs, finance, tickets | `subscribers.read`; finance — отдельное право |
| `/subscriber-lookups/{id}/diagnostics`, `/tickets/{id}/diagnostics` | запуск/чтение диагностики и ONU | `diagnostics.run/read` и ticket scope |
| `/tasks` | поручения, события, уведомления/read state | `tasks.create/execute` и участие |
| `/chat` | summary, messages/replies, read, SSE, delete | сессия; delete — `chat.messages.delete` |
| `/notifications` | список, read | только текущий пользователь |
| `/settings`, `/ticket-categories` | причины, шаблоны, категории | `settings.manage`/`users.manage` для мутаций |
| `/reports` | пять отчётов, PDF, XLSX | `reports.read` и ticket visibility |
| `/field-work` | installers, staff access, access requests, Mini App | `users.manage` либо Telegram/staff auth |

## Service Desk

`POST /tickets` принимает `SUBSCRIBER_LINKED`, `MANUAL`, `NEW_CONNECTION`, `INTERNAL`, `ERRONEOUS_PAYMENT`. Обязательные поля различаются; сервер и PostgreSQL проверяют согласованность. Абонентский тип требует подтверждённые snapshots. Ручные типы не получают фиктивную NetUP-привязку. `NEW_CONNECTION` требует ФИО, телефон, населённый пункт и адрес.

`GET /tickets` фильтрует и считает на сервере. INTERNAL исключается для оператора, включая totals и связанные endpoints. `GET /tickets/{id}/previous-tickets` связывает обращения только по подтверждённому subscriber identity/account snapshot.

Workflow endpoints: `assignment`, `claim`, `release`, `start`; `decisions` и `resolve`; `callbacks` (`CONFIRMED`, `STILL_BROKEN`, `NO_ANSWER`); `close`; отдельные `history` и `audit`. Актуальное решение определяется `resolved_decision_id`, а не страницей истории.

Field work endpoints создают draft, меняют note, публикуют или отправляют handoff, выполняют reconciliation `UNCERTAIN`, показывают external messages/reschedules и подтверждают отчёт. Произвольный `chat_id` клиентом не задаётся.

## Subscriber 360 и диагностика

`GET /subscriber-lookups/search?q=...` возвращает ограниченный список кандидатов. `POST /subscriber-lookups` создаёт snapshot; дальнейшие маршруты проверяют владельца lookup. Недоступные финансы не превращаются в нули.

Диагностика возвращает независимые observations LibreNMS/Icinga/BDCOM и качество сопоставления. Отсутствие ONU не скрывает device status. Device mapping не подтверждает port/ONU.

## Mini App

`/field-work/miniapp/*` проверяет Telegram `initData`, срок, активную регистрацию и направления. Staff-вариант использует отдельную связь OSS account ↔ Telegram identity. URL ID не является авторизацией.

- `GET .../handoffs` — очереди `AWAITING_REPORT`, `REPORTED`, `VERIFIED`;
- `POST .../handoffs/{id}/report` — транзакционная фиксация отчёта и исполнителей;
- `POST .../handoffs/{id}/reschedules` — неизменяемая запись переноса;
- staff `verify` требует `tickets.field_work` и видимость заявки.

Pydantic schemas, роутеры, frontend types и миграционные ограничения меняются согласованно. Не восстанавливайте архивный контракт целиком: он описывает прежние фазы.
