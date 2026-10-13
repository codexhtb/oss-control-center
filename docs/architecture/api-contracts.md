# API contracts — Phase 1

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
| `POST /api/v1/users` | `identity.manage` + CSRF | `{login, display_name, role, password}` → `201 User`; конфликт логина `409` |
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

| Метод и путь | Роль | Запрос | Результат и эффект |
| --- | --- | --- | --- |
| `POST /subscriber-lookups` | Оператор, инженер + CSRF | `{account_no}` | `200` Subscriber 360 + `lookup_id`; сохраняет immutable snapshot с `source=MOCK`, временем, автором поиска, сроком действия 15 минут и внутренними ID подключений; аудит чтения. Не найдено — `404`, источник недоступен — `503` без успешной карточки |
| `GET /subscriber-lookups/{lookup_id}` | Автор поиска, оператор или инженер | — | Сохранённая карточка без повторного обращения к провайдеру; истечение срока отражается `is_expired` |
| `POST /tickets` | Оператор, инженер | Linked: `{ticket_type?:"SUBSCRIBER_LINKED", lookup_id, selected_service_id?, connection_snapshot_id?, description, client_request_id, priority?}`; manual/internal: `{ticket_type:"MANUAL"|"INTERNAL", description, client_request_id, priority?}` | `201` заявка `OPEN`; повтор идентичного запроса `200`, другой запрос с тем же ID `409`; заявка, история и аудит атомарны. Для linked сохраняются проверки владельца, срока и выбора услуги/подключения |
| `GET /tickets` | Оператор, инженер | Фильтры `status`, `priority`, `number`, `assignee`, `account_no`, `ticket_type`; `page`, `size` | Страница сводок; `total`, `page`, `size` |
| `GET /tickets/{ticket_id}` | Оператор, инженер | — | Заявка; для manual/internal `subscriber` и `selected_connection` равны `null`, `technical_state=UNKNOWN`; комментарии, решения и ручные наблюдения читаются отдельно |
| `POST /tickets/{ticket_id}/start` | Инженер | `If-Match` с версией заявки | `OPEN → IN_PROGRESS`, история и аудит; повтор или конфликт версии — `409` |
| `POST /tickets/{ticket_id}/assignment` | Инженер | `{engineer_id}` + `If-Match` | Назначение активного инженера, история и аудит; статус не меняется |
| `POST /tickets/{ticket_id}/field-work-request` | Инженер | `{reason}` + `If-Match` | Событие с причиной из `IN_PROGRESS`; статус не меняется, Telegram не вызывается |
| `POST /tickets/{ticket_id}/comments` | Оператор, инженер | `{body}` | `201` комментарий, история и аудит; закрытая заявка в Phase 1 не изменяется |
| `GET /tickets/{ticket_id}/comments`, `/manual-diagnostics`, `/decisions` | Оператор, инженер | `page`, `size` | Страницы сохранённых записей |
| `POST /tickets/{ticket_id}/manual-diagnostics` | Инженер | `{finding, observed_at, connection_snapshot_id?}` | `201` snapshot с `source_kind=MANUAL`, история и аудит; требуется `IN_PROGRESS` |
| `POST /tickets/{ticket_id}/decisions` | Инженер | `{decision_text, performed_actions}` | `201` новая неизменяемая версия решения, история и аудит; требуется `IN_PROGRESS` |
| `POST /tickets/{ticket_id}/close` | Инженер | `{decision_id}` + `If-Match` | `IN_PROGRESS → CLOSED` только с решением той же заявки; `closed_by`, `closed_at`, история и аудит в транзакции |
| `GET /tickets/{ticket_id}/history` | Оператор, инженер | Пагинация | Хронология фактов с источником и временем; внешних Telegram сообщений в Phase 1 нет |
| `GET /tickets/{ticket_id}/audit` | Инженер | Пагинация | Аудит действий по заявке без секретов и копирования полного содержания PII |

Пути в таблице относительны к `/api/v1`. Для linked `lookup_id` равен ID сохранённого subscriber snapshot; создать заявку можно только из ещё действующего snapshot, созданного текущим оператором или инженером. `connection_snapshot_id` должен принадлежать ему. Для manual/internal billing поля запрещены даже при `null`; `account_no` и произвольный клиентский snapshot не принимаются. Закрытие `OPEN` сначала требует явного начала работы; это проверяется сервером. В Phase 1 не предусмотрены API для ServiceDesk Plus, Telegram и мониторинга.

Phase 1.3 ввела `POST /subscriber-lookups` и `GET /subscriber-lookups/{lookup_id}`. Только linked заявка создаётся из действующего сохранённого снимка, принадлежащего автору запроса; клиент не отправляет ФИО, телефон, адрес или сетевые идентификаторы в `POST /tickets`. Один доступный сервис/подключение выбирается автоматически, несколько требуют явного ID. Успешный `GET /tickets/{id}` и изменяющие ответы выставляют `ETag` с `tickets.version`; отсутствующий `If-Match` для операций с версией даёт `428`, устаревший — `409`. Повтор создания с тем же содержимым даёт `200` вместо второго `201`.

## Ответ поиска абонента

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
Новые ошибки: `422 account_format_unsupported`, `409 billing_ambiguous`,
`409 billing_incomplete`; `404` и `503` сохраняют прежнюю семантику.
Выбранные услуга и подключение должны относиться к запрошенному ЛС и друг
к другу. Полная спецификация —
[`provider-api-contract.md`](../netup/provider-api-contract.md).
