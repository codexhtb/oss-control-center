# API contracts — Phase 1

**Phase 5.7:** `GET /api/v1/tickets?callback_queue=true&page=1&size=20`
возвращает серверную очередь `SUBSCRIBER_LINKED` + `RESOLVED`, отсортированную
по `resolved_at` от старых к новым. `total` — точное число после RBAC и
фильтров; элементы содержат `resolved_by_name`, `caller_phone`, `category_name`,
`callback_count`, `last_callback_outcome`, `last_callback_at`.
`GET /api/v1/tickets/{id}/callbacks` возвращает пагинированные неизменяемые
попытки. `POST /api/v1/tickets/{id}/callbacks` требует CSRF, Origin,
`tickets.callback`, доступ к заявке и `If-Match` и принимает
`{client_request_id, outcome, comment?}`. `STILL_BROKEN` требует комментарий.
Повтор того же запроса идемпотентен; иной payload с тем же ID даёт `409`.
`POST /close` для связанной заявки возвращает `409 callback_required`; для
`MANUAL`/`INTERNAL` требует `tickets.close`, актуальный `decision_id`, `If-Match`
и непустую `reason` (до 1000 символов). Ответы с данными имеют
`Cache-Control: no-store`. См. [workflow](phase-5.7-callback.md).

**Phase 5.6:** `TicketView` и `TicketDetail` дополнены nullable
`resolved_by`, `resolved_at`, `resolved_decision_id`; для прежних закрытых
заявок они могут быть `null`. Фильтр `status=RESOLVED` работает в общем реестре.
`active_only=true` остаётся фильтром **инженерной работы** (`OPEN` и
`IN_PROGRESS`), тогда как поиск активных связанных обращений в
`/subscriber-lookups/{id}/active-tickets` включает `RESOLVED` до закрытия.
`POST /resolve` требует CSRF/Origin и `If-Match`; текст вердикта и действий
строго валидируется, но не попадает в общий access log.

## Phase 5.5 — инженерные очереди и взятие заявки

`GET /api/v1/tickets?active_only=true&unassigned=true` возвращает очередь «Ожидают инженера», а `active_only=true&assignee=<current_user_id>` — «Взяты мной». Обе выборки используют текущие `OPEN` и `IN_PROGRESS`, исключают soft-deleted заявки, применяют существующие права чтения, поиск, остальные фильтры и серверную пагинацию. `total` используется для счётчиков; отдельной таблицы очередей нет. Для строк в работе `work_started_at` добавляется только если последним событием назначения/начала/освобождения является `engineer_claimed` или `ticket_started` от нынешнего исполнителя. Если это нельзя доказать, поле равно `null`.

`POST /api/v1/tickets/{id}/claim` и `/release` требуют сессию, Origin, CSRF, `If-Match: "<version>"`, видимость заявки, активную роль `engineer` и индивидуальное разрешение `tickets.start`; право чтения или редактирования само по себе недостаточно. Claim атомарно назначает текущего инженера и переводит `OPEN` в `IN_PROGRESS`; свободная заявка уже в `IN_PROGRESS` остаётся в этом статусе. Release снимает только собственное назначение, не откатывает статус и не удаляет историю. Оба действия увеличивают версию и атомарно пишут `ticket_history` и `audit_events`. Повторный claim уже закреплённой за тем же инженером заявки в `IN_PROGRESS` возвращает её без новой записи; чужое назначение даёт `409`, закрытая заявка — `409`, удалённая — `404`. Конкуренция сериализуется блокировкой строки PostgreSQL. Явное назначение через `/assignment` сохраняется; обычный инженер не может переназначить занятую заявку другому, администратор с `tickets.assign` может сделать это с записью истории и аудита.

`GET /api/v1/tickets/assignable-engineers` с минимальными `id,display_name` доступен обладателю `tickets.read.all`, `tickets.read.own` или `tickets.assign`, чтобы реестр показывал имена вместо технических UUID. Это не предоставляет право назначения. Ответы не кешируются.

## Phase 5.4 — поиск и фильтры реестра

`GET /api/v1/tickets` сохраняет старые параметры и добавляет необязательные `q` (до 160 символов), `category` (поиск по сохранённому названию категории), `category_id` (точное совпадение), `created_from` (включительно), `created_before` (исключительно) и `active_only`. Даты принимаются только с явным часовым поясом в ISO 8601; `created_before` должен быть позже `created_from`. Web UI переводит выбранные локальные календарные дни в точные границы UTC и сохраняет их в URL. `active_only=true` означает только `OPEN` и `IN_PROGRESS`, без новых статусов. Пустой/состоящий из пробелов `q` не ограничивает результаты.

`q` ищет без учёта регистра по номеру заявки (точное числовое совпадение), историческому ЛС, ФИО/организации из ticket snapshot, `caller_phone`, сохранённой категории, теме, описанию и тексту/действиям инженерного решения. Для телефона дополнительно сравниваются цифры без пробелов и обычных разделителей. Символы SQL LIKE экранируются, значения параметризуются. Все условия применяются в PostgreSQL до пагинации, сортировка остаётся `(created_at DESC, id DESC)`, мягко удалённые заявки исключены. `tickets.read.own` ограничивает и строки, и `total`; BillingProvider, NetUP и мониторинг при поиске не вызываются. Ответы имеют `Cache-Control: no-store`.

## Phase 5.3 — абонент в реестре заявок

`GET /api/v1/tickets` и `GET /api/v1/tickets/trash` сохраняют фильтры, сортировку, пагинацию и область видимости. В каждой сводке добавлено необязательное `subscriber_name: string | null`. Для `SUBSCRIBER_LINKED` имя или название организации и `account_no` читаются пакетно из неизменяемого `subscriber_snapshots` в PostgreSQL. Для `MANUAL` и `INTERNAL` оба значения равны `null`. Старые клиенты могут игнорировать новое поле; запросов к BillingProvider/NetUP при чтении списка нет. Если имя отсутствует в snapshot, API возвращает `null`, а интерфейс показывает нейтральное обозначение и подтверждённый ЛС.

## Текущий контракт удаления заявок (`0014`)

Все изменяющие операции требуют сессию, Origin, CSRF и `If-Match: "<version>"`; ответ содержит `Cache-Control: no-store`. Детали: [удаление заявок](ticket-deletion.md).

| Метод и путь | Право | Результат |
| --- | --- | --- |
| `DELETE /api/v1/tickets/{id}` | `tickets.delete.own` при `created_by` либо `tickets.delete.all`, плюс право чтения | Необязательное тело `{reason?}`; soft delete, версия/история/аудит; `200` |
| `GET /api/v1/tickets/trash` | `tickets.read.all` и `tickets.restore` либо `tickets.purge` | Пагинация `page,size`, только удалённые заявки |
| `POST /api/v1/tickets/{id}/restore` | `tickets.restore` и `tickets.read.all` | Восстановление с прежней историей; `200` |
| `DELETE /api/v1/tickets/{id}/purge` | `tickets.purge` и `tickets.read.all` | Тело `{confirm_number}`, только soft-deleted; `204`, минимальный аудит |


## Текущий контракт: персональные права и редактирование

Секция ниже заменяет исторические значения `ticket.*`, `subscriber.*` и
`identity.manage` в таблицах прежних фаз. Актуальные ключи перечислены в
[RBAC matrix](rbac.md). Все приватные ответы имеют `Cache-Control: no-store`.
Пароль сотрудника принимается только в защищённом теле HTTPS-запроса и никогда
не попадает в журналы или публичные ответы.

| Метод и путь | Право | Контракт |
| --- | --- | --- |
| `GET /auth/me`, `GET /auth/permissions` | Сессия | `user.must_change_password`, актуальные индивидуальные `permissions` |
| `POST /auth/password` | Сессия + Origin + CSRF | `{current_password,new_password}`; новый пароль непустой, до 1024 символов; отзыв всех сессий и cookies; повторный вход |
| `GET /users` | `users.read` | `page` 1+, `size` 1–100, список `id, login, display_name, role, is_active, must_change_password, permissions` |
| `GET /users/{id}` | `users.read` | Те же публичные поля одного сотрудника |
| `POST /users` | `users.manage` + CSRF | `{login,display_name,role,permissions?,password?,must_change_password?}`; явный непустой пароль сохраняется через Argon2id, флаг смены по умолчанию `false`; старый запрос без `password` возвращает однократный `temporary_password` с обязательной сменой |
| `PATCH /users/{id}` | `users.manage` + CSRF | `{login?,display_name?}`; до/после в identity audit, конфликт логина `409` |
| `PUT /users/{id}/permissions` | `users.manage` + CSRF | `{permissions:[...]}`; полная замена, действие в текущей сессии, аудит до/после; нельзя отозвать последний `users.manage` активного admin |
| `POST /users/{id}/reset-password` | `users.manage` + CSRF | Однократный `temporary_password`, отзыв сессий цели, обязательная смена |
| `PUT /users/{id}/password` | `users.manage` + CSRF | `{new_password,must_change_password=false}`; Argon2id, отзыв всех сессий цели, аудит без значения пароля |
| `POST /users/{id}/deactivate`, `/reactivate` | `users.manage` + CSRF | Состояние, отзыв сессий при отключении, last-admin guard |
| `GET /users/audit` | `users.manage` + `audit.read` | Страница событий с актором, целью, временем и несекретными деталями |
| `PATCH /tickets/{id}` | `tickets.edit.own` или `.all` + право чтения + CSRF | `{subject?,description?}`; `If-Match: "<version>"`; только незакрытая заявка; `200` с новым ETag, `428` без версии, `409` при конфликте; событие с before/after и аудит атомарны |

Для `tickets.read.own` все ticket GET и лента событий ограничены заявками с
`created_by` текущего сотрудника; чужая карточка и факты дают `404`.
`tickets.read.all` снимает это ограничение в едином контуре OSS. Поля `created_by_name`,
`assigned_engineer_name`, `author_name` и `actor_name` добавлены к ответам
для отображения сотрудника без технического логина; существующие ID сохранены.
Исторические абзацы далее описывают контракты на момент их принятия.

Утверждённое дополнение Phase 1.4: [контракт трёх типов создания заявок](phase-1.4-ticket-types.md). Stage 2 поддерживает создание `SUBSCRIBER_LINKED`, `MANUAL` и `INTERNAL` через общий `POST /tickets`. Прежний запрос с `lookup_id` без `ticket_type` остаётся совместимым.

Версия HTTP API: `/api/v1`. Это контракт OSS Control Center, а не описание API NetUP. Все записи сохраняются в PostgreSQL. JSON использует UTF-8, идентификаторы ресурсов — UUID, лицевой счёт — строка (ведущие нули сохраняются), время — UTC ISO 8601. Поля без достоверного значения возвращаются как `null` или отсутствуют согласно схеме, а не заполняются догадкой.

## Phase 1.1 — служебные health endpoints

| Метод и путь | Проверка | Ответ |
| --- | --- | --- |
| `GET /api/v1/health/live` | Процесс FastAPI отвечает; обращений к БД нет | `200 {"status":"alive"}` |
| `GET /api/v1/health/ready` | Ограниченный по времени `SELECT 1` в PostgreSQL | `200 {"status":"ready","dependencies":{"postgres":"up"}}` либо `503 {"status":"not_ready","dependencies":{"postgres":"down"}}` |

Health endpoints не требуют пользовательской сессии и не возвращают строку подключения, пароль или текст ошибки БД. Остальные методы этой страницы вводятся позднее в Phase 1.

## Идентификация и ошибки

Phase 1.2A использует локальные учётные записи ролей `operator`, `engineer`, `admin`. `admin` управляет только идентификацией и не получает права на заявки. Сессия хранится в PostgreSQL, а браузер получает непрозрачный `oss_session` cookie (`HttpOnly`, `SameSite=Strict`, `Secure` при HTTPS). Отдельный `oss_csrf` cookie и заголовок `X-CSRF-Token` нужны для изменяющих запросов; сервер сверяет хэш CSRF-токена в сессии, `Origin` и `Host` с `AUTH_PUBLIC_ORIGIN`. Исключение для HTTP допускается только в dev при `AUTH_ALLOW_HTTP_LOOPBACK=true` и точном origin `http://127.0.0.1:<port>`. Кабинета монтажников нет.

### Identity endpoints — Phase 1.2A

| Метод и путь | Доступ | Запрос / ответ |
| --- | --- | --- |
| `POST /api/v1/auth/login` | Без сессии, с проверкой `Origin` и `Host` | `{login, password}` → `200 {user, permissions, expires_at}` и cookies; неверные данные `401`, блокировка `429` |
| `POST /api/v1/auth/logout` | Сессия + CSRF | `200 {status:"logged_out"}`; серверный отзыв и удаление cookies |
| `GET /api/v1/auth/me` | Сессия | `200 {user, permissions}` |
| `GET /api/v1/auth/permissions` | Сессия | `200 {permissions}` |
| `POST /api/v1/users` | Исторически `identity.manage`; актуально `users.manage` + CSRF | Текущий контракт описан в таблице выше; конфликт логина `409` |
| `POST /api/v1/users/{user_id}/deactivate` | `identity.manage` + CSRF | `200 User`; сессии цели отзываются; последнего активного администратора деактивировать нельзя (`409`) |
| `PATCH /api/v1/users/{user_id}/role` | `identity.manage` + CSRF | `{role}` → `200 User`; сессии цели отзываются; последнего активного администратора понизить нельзя (`409`) |

`User` содержит только `id`, `login`, `display_name`, `role`, `is_active`, `created_at`, `updated_at`. Хэш пароля, токены сессии и CSRF-токен отсутствуют в JSON. Самостоятельной регистрации и API удаления пользователя нет. Первый `admin` создаётся интерактивной CLI-командой только при пустой таблице пользователей. Login ограничен PostgreSQL-счётчиками по каноническому логину и непосредственному адресу источника; произвольные proxy-заголовки игнорируются. Никакого доверенного reverse-proxy режима эта фаза не включает.

Ошибки имеют форму `{ "code": "...", "message": "...", "request_id": "..." }` без персональных данных и секретов. Для identity: `401` неверные учётные данные или нет сессии, `403` нет права либо нарушен CSRF/Origin, `404` объект не найден, `409` конфликт состояния, `422` неверный запрос, `429` ограничение входа. Будущий Service Desk использует также `400` для формата и `503` при недоступном BillingProvider. Отсутствующий абонент возвращает `404`, недоступный провайдер — `503`; это разные случаи.

## Внутренний контракт BillingProvider

```text
lookupByAccount(account_no: string) -> Subscriber360 | NotFound | Unavailable
```

`Subscriber360` содержит `subscriber_id`, `billing_account`, доступные ФИО или название организации и контакт, договоры, услуги, подключения и метаданные `source`/`fetched_at`. Подключение содержит тип `ETHERNET | EPON | GPON | UNKNOWN` и только доступные адрес, устройство, порт, VLAN, IP-группу, ONU ID. У каждого существенного поля есть `field_meta` с источником, временем наблюдения и качеством `KNOWN | UNKNOWN | STALE | CONFLICT | UNAVAILABLE`. Phase 1 поставляет только синтетический mock; Phase 2 заменяет адаптер на read-only NetUP без изменения Ticket Engine. Финансовые поля не участвуют в вычислении технического состояния.

## HTTP операции Phase 1

### Темы обращений и номер звонящего (ревизия `0015`)

`GET /api/v1/ticket-categories` возвращает активные темы оператору и инженеру с правом `tickets.create`; администратор с `users.manage` может запросить также отключённые темы через `?include_inactive=true`. Ответ не кешируется. `POST /api/v1/ticket-categories` (`{name, sort_order}`) и `PATCH /api/v1/ticket-categories/{id}` (`name?`, `sort_order?`, `is_active?`, обязательный `If-Match` с версией) доступны только обладателю `users.manage` с CSRF и проверкой Origin. Переименование меняет справочник, но не сохранённое название темы в старых заявках. Удаление темы через API отсутствует.

`caller_phone` — необязательный номер, введённый/выбранный при звонке, до 50 символов без управляющих символов. Сохраняется в заявке в исходном представлении. Это не подтверждённый Caller ID и не замена контактам NetUP. В истории/общих журналах номер не дублируется. Поля `category_id`, `category_name` и `caller_phone` доступны только по действующим правам чтения заявки.

| Метод и путь | Роль | Запрос | Результат и эффект |
| --- | --- | --- | --- |
| `POST /subscriber-lookups` | Оператор, инженер + CSRF | `{account_no}` | `200` Subscriber 360 + `lookup_id`; сохраняет immutable snapshot с `source=MOCK`, временем, автором поиска, сроком действия 15 минут и внутренними ID подключений; аудит чтения. Не найдено — `404`, источник недоступен — `503` без успешной карточки |
| `GET /subscriber-lookups/{lookup_id}` | Автор поиска, оператор или инженер | — | Сохранённая карточка без повторного обращения к провайдеру; истечение срока отражается `is_expired` |
| `GET /subscriber-lookups/{lookup_id}/tickets` | Автор поиска с `subscriber.read` и `ticket.read` | `page` (≥1), `size` (1–50) | Страница связанных заявок того же `subscriber_id` и источника; при отсутствии ID — точное совпадение ЛС. История заявок хранится бессрочно. |
| `GET /subscriber-lookups/{lookup_id}/active-tickets` | Автор поиска с `subscribers.read` | `page` (1–1000), `size` (1–50) | `items` с номером, темой, статусом, временем, исполнителем и кратким описанием доступных активных заявок; `total_visible`, `has_hidden_active`. Недоступные заявки не раскрываются. `Cache-Control: no-store`. |
| `POST /tickets` | Оператор, инженер + CSRF | Linked: `{ticket_type?:"SUBSCRIBER_LINKED", lookup_id, selected_service_id?, connection_snapshot_id?, category_id?, caller_phone?, description?, subject?, client_request_id, priority?}`; manual/internal: `{ticket_type:"MANUAL"|"INTERNAL", category_id?, caller_phone?, description?, subject?, client_request_id, priority?}` | `201` заявка `OPEN`; повтор идентичного запроса `200`, другой запрос с тем же ID `409`. Без `category_id` прежний контракт требует `description`. С активной категорией комментарий необязателен; в `description` сохраняется тема, если комментария нет. Для категории «Другое» обязательна короткая `subject`. Неактивная или неизвестная категория — `422`. Ответ содержит `category_id`, неизменяемую `category_name` и необязательный `caller_phone`. Для linked сохраняются проверки владельца, срока, выбора услуги/подключения и предупреждение о дубликате |
| `GET /tickets` | Оператор, инженер | Фильтры `status`, `priority`, `number`, `assignee`, `unassigned`, `account_no`, `ticket_type`; `page`, `size` | Страница сводок; `total`, `page`, `size`. `unassigned=true` означает `assigned_engineer_id IS NULL`; вместе с `assignee` даёт `422` |
| `GET /tickets/assignable-engineers` | Инженер | `page`, `size` (1–100) | Только активные пользователи с ролью `engineer`: `id`, `display_name`; `total`, `page`, `size`. Логин, хэш и контакты не выдаются |
| `GET /tickets/events/recent` | Оператор, инженер | `page` (1–1000), `size` (1–50) | Последние факты из `ticket_history` по убыванию `(occurred_at, id)`; `id`, `ticket_id`, `ticket_number`, `event_type`, `actor_id`, `ticket_version`, `occurred_at`. Без `event_data` и текстов обращения |
| `GET /tickets/{ticket_id}` | Оператор, инженер | — | Заявка; для manual/internal `subscriber` и `selected_connection` равны `null`, `technical_state=UNKNOWN`; комментарии, решения и ручные наблюдения читаются отдельно |
| `POST /tickets/{ticket_id}/start` | Инженер | `If-Match` с версией заявки | `OPEN → IN_PROGRESS`, история и аудит; повтор или конфликт версии — `409` |
| `POST /tickets/{ticket_id}/assignment` | Инженер | `{engineer_id}` + `If-Match` | Назначение активного инженера, история и аудит; статус не меняется |
| `POST /tickets/{ticket_id}/field-work-request` | `tickets.field_work` | `{reason}` + `If-Match` | Атомарно сохраняет причину, историю, аудит и `FieldWorkHandoff` со статусом `DRAFT`; статус заявки не меняется, Telegram не вызывается |
| `GET /tickets/{ticket_id}/field-work-handoffs/latest` | `tickets.field_work` | — | Последняя сохранённая передача, точный preview из ticket snapshot, недостающие поля и состояние доставки |
| `POST /tickets/{ticket_id}/field-work-handoffs/{handoff_id}/send` | `tickets.field_work` + CSRF | `{client_request_id}` | Только LAB: отдельная подтверждённая отправка в фиксированный чат. `SENT` и `message_id` сохраняются лишь после ответа Bot API; `FAILED` допускает явный повтор, `UNCERTAIN` блокирует автоматический повтор |
| `GET /tickets/{ticket_id}/field-work-handoffs/{handoff_id}/messages` | `tickets.field_work` | `page`, `size` | Пагинированные проверенные текстовые ответы и отметка выполнения без Telegram ID отправителя |
| `POST /tickets/{ticket_id}/field-work-handoffs/{handoff_id}/verify` | `tickets.field_work` + CSRF | — | Идемпотентно подтверждает только `REPORTED`; не меняет статус заявки или решение |
| `POST /tickets/{ticket_id}/comments` | Оператор, инженер | `{body}` | `201` комментарий, история и аудит; закрытая заявка в Phase 1 не изменяется |
| `GET /tickets/{ticket_id}/comments`, `/manual-diagnostics`, `/decisions` | Оператор, инженер | `page`, `size` | Страницы сохранённых записей |
| `POST /tickets/{ticket_id}/manual-diagnostics` | Инженер | `{finding, observed_at, connection_snapshot_id?}` | `201` snapshot с `source_kind=MANUAL`, история и аудит; требуется `IN_PROGRESS` |
| `POST /subscriber-lookups/{lookup_id}/diagnostics/check` | Инженер, автор lookup | `{connection_snapshot_id}` | Preview без записи в заявку |
| `POST /tickets/{ticket_id}/diagnostics/check` | Инженер | `If-Match` | Preview и `check_id`, действующий 15 минут |
| `POST /tickets/{ticket_id}/diagnostics/confirm` | Инженер | `{check_id}` + `If-Match` | `201` автоматический snapshot, история и аудит при `IN_PROGRESS` |
| `GET /subscriber-lookups/{lookup_id}/connections/{connection_id}/onu` | Инженер, автор lookup | UUID в пути | Read-only BDCOM ONU; составной ключ OLT+PON+VLAN или неподтверждённые кандидаты |
| `GET /tickets/{ticket_id}/onu` | Инженер | UUID в пути | Read-only BDCOM ONU кандидаты для подтверждённого connection snapshot |
| `POST /tickets/{ticket_id}/decisions` | `tickets.resolve` | `{decision_text, reason?, performed_actions?}` | `201` новая неизменяемая версия вердикта, история и аудит; допускается `IN_PROGRESS` или `RESOLVED`. `reason` — утверждённый код причины; при отсутствии у прежнего клиента сохраняется `NOT_ESTABLISHED`. `performed_actions` остаётся только совместимым необязательным полем. В `RESOLVED` текущая ссылка `resolved_decision_id` переходит на новую версию без изменения `resolved_at` |
| `POST /tickets/{ticket_id}/resolve` | `tickets.resolve`, назначенный инженер или admin с правом | `{decision_text, reason?, performed_actions?}` + `If-Match` | Атомарно создаёт неизменяемый вердикт и переводит `IN_PROGRESS → RESOLVED`; возвращает `resolved_by`, `resolved_at`, `resolved_decision_id`, историю и аудит. Пустой вердикт, неизвестная причина, повтор и устаревшая версия отклоняются |
| `POST /tickets/{ticket_id}/close` | `tickets.close` | `{decision_id}` + `If-Match` | `RESOLVED → CLOSED` только с текущим `resolved_decision_id`; `closed_by`, `closed_at`, история и аудит в транзакции. Старый прямой переход из `IN_PROGRESS` запрещён |
| `GET /tickets/{ticket_id}/history` | Оператор, инженер | Пагинация | Хронология фактов с источником и временем; внешних Telegram сообщений в Phase 1 нет |
| `GET /tickets/{ticket_id}/audit` | Инженер | Пагинация | Аудит действий по заявке без секретов и копирования полного содержания PII |

**Phase 5.1, linked-заявки:** `duplicate_reason?: string` (непустая, до 500
символов) требуется, если для точного абонентского ключа уже есть `OPEN` или
`IN_PROGRESS` заявка. Иначе `409 active_ticket_exists` без сведений о чужих
обращениях. Причина хранится в `ticket_created.event_data`; для `MANUAL` и
`INTERNAL` поле запрещено. Проверка выполняется под транзакционной блокировкой
и сохраняет прежнюю идемпотентность `client_request_id`.

Ответ ONU GET содержит `source=BDCOM`, `retrieved_at`, `availability`,
`association=UNCONFIRMED | MATCHED_BY_RULE`, необязательный `match`
с портом NetUP, вычисленным PON-портом, VLAN, основанием OLT mapping,
источниками и временем NetUP, `time_basis=SOURCE_LOCAL_UNVERIFIED`, `limitations`
и не более 20 `candidates`. Каждый кандидат содержит OLT, интерфейс,
`pon_type`, физический `mac` (nullable), `serial` (nullable), `state`,
`reason`, три nullable оптических значения и отдельные
`inventory_polled_at`/`optical_measured_at`, а также необязательные
`disconnection_at` и `disconnection_time_basis`. Для EPON basis
`EPON_DEREGISTRATION` означает проверенное SNMP `last_deregister` на
отключённой ONU; для GPON `FIRST_OBSERVED_OFFLINE` означает лишь первое
обнаружение уже отключённой ONU. Когда время из текущей строки не доказано,
оба поля `null`. У Online ONU они всегда `null`. GPON surrogate в DB `mac`
не считается физическим MAC. `UNAVAILABLE` не подменяется пустым успешным
списком. Ответы имеют `Cache-Control: no-store`; путь содержит только UUID,
а не ЛС или MAC. `MATCHED_BY_RULE` возможен только при утверждённом
сопоставлении OLT, одной полной тройке NetUP switch/port/VLAN с provenance
NETUP, преобразовании порта по утверждённому правилу GPON `−13`/EPON `−6`
и ровно одной действующей ONU по OLT+PON+VLAN при единственной такой
сервисной связке в snapshot абонента. Повторы, неполные значения
и неутверждённые OLT остаются `UNCONFIRMED`; результат не доказывает
физическое состояние линии вне времени опроса.

Пути в таблице относительны к `/api/v1`. Для linked `lookup_id` равен ID сохранённого subscriber snapshot; создать заявку можно только из ещё действующего snapshot, созданного текущим оператором или инженером. `connection_snapshot_id` должен принадлежать ему. Для manual/internal billing поля запрещены даже при `null`; `account_no` и произвольный клиентский snapshot не принимаются. Закрытие `OPEN` сначала требует явного начала работы; это проверяется сервером. В Phase 1 не предусмотрены API для ServiceDesk Plus и Telegram. Диагностические наблюдения содержат `source`, `measured_object`, `result`, `observed_at`, `retrieved_at` и `quality`; подробности — в [OSS Diagnostics](oss-diagnostics.md).

Phase 1.3 ввела `POST /subscriber-lookups` и `GET /subscriber-lookups/{lookup_id}`. Только linked заявка создаётся из действующего сохранённого снимка, принадлежащего автору запроса; клиент не отправляет ФИО, телефон, адрес или сетевые идентификаторы в `POST /tickets`. Один доступный сервис/подключение выбирается автоматически, несколько требуют явного ID. Успешный `GET /tickets/{id}` и изменяющие ответы выставляют `ETag` с `tickets.version`; отсутствующий `If-Match` для операций с версией даёт `428`, устаревший — `409`. Повтор создания с тем же содержимым даёт `200` вместо второго `201`.

Дополнение перед Stage 3B: `subject` — необязательный свободный текст до 160 символов, без управляющих символов, пустой строки и пробелов по краям. В старых заявках и ответах старых клиентов он равен `null`; он участвует в сравнении повторного `client_request_id`. Новые формы Web UI предлагают отдельный ввод темы без обязательного шаблона. Все три типа заявок используют одинаковое поле. Приватные ответы не кешируются; доступ к ним проверяется на сервере.

## Ответ поиска абонента

Stage 3C добавляет в ответ необязательные `services[].tariff_name`,
`billing_active`, `billing_blocked` и `financial_accounts[]`. Для старых и
mock snapshots значения отсутствуют или равны `null`; миграция `0010`
заполняет `financial_accounts=[]` без реконструкции прошлого. Баланс и кредит
передаются десятичными строками в единицах источника без предположения о
валюте. Массив `financial_accounts[]` видит только роль с
`subscriber.finance.read` (`operator`). `engineer` получает пустой массив,
`admin` не видит карточку. Биллинговые признаки не определяют состояние линии.
Сырые числовые поля IP-групп сохраняются без неподтверждённого IPv4-формата.

```json
{
  "lookup_id": "<uuid>",
  "source": "MOCK",
  "fetched_at": "<UTC timestamp>",
  "expires_at": "<UTC timestamp>",
  "is_expired": false,
  "subscriber": {
    "subscriber_id": "<synthetic ID or null>",
    "billing_account": "<string>",
    "full_name": "<string or null>",
    "organization_name": null,
    "phone": null,
    "contacts": [],
    "email": null,
    "field_meta": {"billing_account": {"source": "MOCK", "observed_at": "<UTC timestamp>", "quality": "KNOWN"}}
  },
  "contracts": [{"contract_id": "<synthetic ID>", "label": null, "field_meta": {}}],
  "services": [{"service_id": "<synthetic ID>", "name": null, "field_meta": {}}],
  "connections": [
    {
      "connection_snapshot_id": "<uuid>",
      "access_type": "ETHERNET",
      "address": null,
      "device": null,
      "port": null,
      "vlan": null,
      "ip_group": null,
      "onu_id": null,
      "field_meta": {"onu_id": {"source": "MOCK", "observed_at": "<UTC timestamp>", "quality": "UNKNOWN"}}
    }
  ],
  "technical_state": "UNKNOWN"
}
```

Это форма контракта, не пример реального абонента и не схема NetUP. В реальном ответе `field_meta` присутствует для каждого возвращаемого существенного поля, включая поля договора, услуги и подключения. При нескольких услугах или подключениях `POST /tickets` требует явного выбора; если подключения нет, привязка остаётся неизвестной. `UNKNOWN` и `CONFLICT` не заменяются угаданными значениями; `STALE` сохраняет наблюдаемое значение вместе с предупреждением. `UNAVAILABLE` для всего источника означает ошибку `503`, без создания snapshot. Техническое состояние остаётся `UNKNOWN` до независимой диагностики.

## Повторы и согласованность

- `client_request_id` — UUID клиента, уникальный для создателя заявки. Повтор того же запроса возвращает ранее созданную заявку; тот же ID с другим содержимым даёт `409`.
- `If-Match` содержит `tickets.version`; конкурентное изменение статуса даёт `409`, не теряя комментарии или историю.
- Любая изменяющая операция пишет доменную запись, `ticket_history` и `audit_events` согласованно. Запрет по RBAC тоже аудируется безопасными метаданными.
- Lookup snapshot с данными mock не должен обозначаться как `NETUP`. В Phase 2 реальный адаптер соблюдает тот же контракт, но его поля и сопоставление утверждаются отдельно.

## Будущие границы API

Phase 3 добавит операции `field_dispatch`, Phase 4 — автоматические диагностические snapshots. Их точные HTTP контракты утверждаются перед соответствующей фазой; Phase 1 не содержит заглушек, вызывающих реальные внешние системы.

## NetUP provider 1.0 — добавочный контракт

`BILLING_PROVIDER=MOCK` остаётся dev-поведением по умолчанию. Для явно
сконфигурированного read-only NetUP provider ответ lookup дополняется
`accounts[]`, `services[].account_id`, `connections[].service_id`,
`connections[].ip_groups[]` с отдельными исходными строками и
`subscriber.actual_address`. Старые ключи и mock snapshots сохраняются.
Новые nullable поля `subscriber.house_number` и строки
`configured_ipv4/configured_prefix_length/address_assignment` внутри
`ip_groups[].rows[]` сохраняют исходные `users.building` и настроенную
адресацию NetUP. Пул остаётся отдельным от конкретного IPv4; конфигурация
группы не объявляется активной DHCP/RADIUS-сессией. Старые snapshots читаются
с `house_number=null` и без новых JSON-ключей.
Новые ошибки: `422 account_format_unsupported`, `409 billing_ambiguous`,
`409 billing_incomplete`; `404` и `503` сохраняют прежнюю семантику.
Выбранные услуга и подключение должны относиться к запрошенному ЛС и друг
к другу. Полная спецификация —
[`provider-api-contract.md`](../netup/provider-api-contract.md).
