# Внутренние поручения

**Назначение.** Вопрос/поручение сотруднику, привязанное к существующей заявке. Не меняет назначение и статус Service Desk заявки.

**Код и API:** `app/modules/internal_tasks/`, `app/api/v1/internal_tasks.py`; `/api/v1/tasks` для assignees, list/create/detail, read markers, events/conversation. UI — `/tasks` и блок поручений в карточке.

**Хранение.** PostgreSQL `internal_tasks`, `internal_task_events`, `internal_task_notifications`; FK к заявке ограничивает удаление истории. Прочтение уведомления сохраняется в БД.

**Права:** `tasks.create` и `tasks.execute` плюс право видеть заявку. Backend использует существующий ticket visibility predicate, в том числе ограничение INTERNAL.

**Workflow:** автор создаёт поручение → исполнитель отвечает/завершает → события и уведомления сохраняются. Текущий упрощённый workflow следует API/UI коду; прежнее создание intermediate/reopen behavior описано только в архивных фазовых текстах.

**Ограничения:** задачи не закрывают заявку, не меняют инженерное решение и не должны раскрывать недоступный ticket snapshot. Сверяйте права в API, не полагайтесь на визуальное скрытие.

## Данные и read state

`internal_tasks` хранит ticket/author/assignee/status, `internal_task_events` — переписку и результат, notification rows — адресата и read state. Создание и завершение с ответом выполняются backend-операциями; третье лицо не получает task/event/ticket details.

Списки «Мне поручили»/«Я поручил» и active/completed фильтруются сервером. Открытие отмечает адресованное уведомление прочитанным; UI-индикатор выводится из сохранённого unread state. Тестируйте, что завершение не меняет ticket status.

| Маршрут | Доступ/результат |
| --- | --- |
| `GET /tasks/assignees` | кандидаты, допустимые по RBAC |
| `POST /tasks` | `tasks.create`, автор и ticket scope |
| `GET /tasks/{id}` | только участник с разрешённым ticket context |
| `POST /tasks/{id}/events` | ответ/завершение исполнителем |
| `POST /tasks/{id}/read` | persisted read state |

Исторические events не переписываются при упрощении UI. Уже завершённое поручение остаётся читаемым; повторная мутация проверяет текущее состояние.
