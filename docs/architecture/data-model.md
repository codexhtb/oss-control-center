# Модель данных и ERD

**Приветствие на главной:** схема PostgreSQL и миграции не меняются. Показ
приветствия оператора не зависит от флага первого входа в `sessionStorage`;
анимация имени и цветочный эффект не сохраняются.

**Дополнение Phase 5.7:** `ticket_callbacks` — неизменяемые попытки контрольного
обзвона с FK на Ticket, текущую версию инженерного решения и автора. Поля:
`id`, `ticket_id`, `decision_id`, `client_request_id`, `actor_id`, `outcome`,
`comment`, `occurred_at`. Уникальность `(ticket_id, client_request_id)`;
результат `STILL_BROKEN` требует комментарий. См.
[правила обзвона](phase-5.7-callback.md).

**Phase 5.6 — `0016_ticket_resolved`:** `tickets.status` дополнен `RESOLVED`;
nullable `resolved_by`, `resolved_at`, `resolved_decision_id` фиксируют первый
инженерный переход и текущую неизменяемую версию вердикта. Составной FK
`(resolved_decision_id, id) → ticket_decisions(id, ticket_id)` не допускает
чужого решения. CHECK требует полного набора полей у `RESOLVED` и отсутствия
их у `OPEN`/`IN_PROGRESS`. Для исторических `CLOSED` допускается полный NULL,
так как прежний workflow не фиксировал отдельное инженерное решение; новые
закрытия возможны только после `RESOLVED`. Переход и аудит атомарны. Активная
инженерная очередь по прежнему ограничена `OPEN`/`IN_PROGRESS`, а проверка
дублей включает и `RESOLVED`. Новых таблиц решений не вводится.

**Phase 5.5:** новая миграция и таблица не требуются. Очереди вычисляются по `tickets.status`, `assigned_engineer_id`, `deleted_at` с существующим индексом `(assigned_engineer_id,status,created_at)`. Claim/release обновляют только назначение, существующий статус и версию заявки; append-only `ticket_history`/`audit_events` фиксируют действие в той же транзакции. Время начала работы для реестра выводится из последнего относящегося к назначению события на странице выдачи; исторические данные не заполняются догадкой.

**Phase 5.4:** миграция не требуется. Поиск использует существующие `tickets`, неизменяемый `subscriber_snapshots` и `ticket_decisions`. `EXISTS` по решениям не размножает строки заявок; источник имени остаётся историческим snapshot. Имеющиеся B-tree индексы помогают точным фильтрам; поиск по подстроке на текущей малой dev-БД выполняется последовательным чтением. Новые индексы рассматриваются после измерений на существенно большем объёме данных.

**Phase 5.3:** схема PostgreSQL не меняется. `tickets.subscriber_snapshot_id` ссылается на неизменяемый `subscriber_snapshots`; связанный `tickets.account_no` защищён составным FK с `subscriber_snapshots.account_no`. Имя для страницы реестра читается из `full_name`, затем из `organization_name` сохранённого snapshot. Никакой отдельной копии имени в `tickets` и синхронизации с NetUP нет. У ручных и внутренних заявок эти поля остаются `NULL`.

**Дополнение `0015_ticket_categories`:** `ticket_categories` — управляемый справочник (`id`, уникальное без учёта регистра `name`, `sort_order`, `is_active`, `is_other`, `version`, времена). В `tickets` добавлены nullable `category_id` (FK с запретом удаления), `category_name` (неизменяемое название при создании) и `caller_phone` (неизменяемое представление номера конкретного звонка). В старых заявках все три поля остаются `NULL`: исторические тему и номер не восстанавливают догадкой. Триггер запрещает UPDATE этих исходных полей; категория может быть переименована или отключена без изменения истории. Телефон звонящего не равен контактному номеру из Billing snapshot, даже если оператор выбрал его из предложенного списка. Категория не заменяет `ticket_type` и не влияет на workflow.

**Текущее дополнение `0014_ticket_deletion`:** `tickets.deleted_at`, `deleted_by`, `delete_reason` поддерживают обратимое удаление; `ticket_purge_audit` хранит минимальный обезличенный след окончательного удаления. Политика бессрочного хранения ниже относится к заявкам до явного административного purge; исключение и порядок FK описаны в [удалении заявок](ticket-deletion.md).

Утверждённое дополнение Phase 1.4: [схема трёх типов заявок](phase-1.4-ticket-types.md). ERD и таблица прежних этапов ниже отражают subscriber-linked схему `0003`–`0007`; Stage 1 добавила ревизию `0008_ticket_types` с nullable связями, дискриминатором, CHECK/FK и защитой типа. Stage 2 использует эту схему для HTTP создания manual/internal без фиктивного Subscriber Snapshot. Исторические заявки и вся их история сохраняются бессрочно.

PostgreSQL — единственный source of truth для обращений, комментариев, решений, истории, аудита и диагностических snapshots. Данные BillingProvider сохраняются как исторический снимок, увиденный при создании заявки; актуальным источником биллинга остаётся сам провайдер. Схема ниже логическая: имена таблиц и ключи фиксируют контракт, конкретные SQL-типы и индексы оформляются версионированной миграцией Phase 1.

**Граница Phase 1.2A:** ревизия `0001_foundation` создаёт только `alembic_version`; `0002_identity_rbac` вводит четыре таблицы идентификации. Таблицы заявок и Subscriber 360 остаются для следующих шагов Phase 1.

**Phase 1.2B:** ревизия `0003_ticket_engine` ввела восемь таблиц Service Desk из ERD: `subscriber_snapshots`, `connection_snapshots`, `tickets`, `ticket_comments`, `ticket_decisions`, `ticket_history`, `audit_events`, `diagnostic_snapshots`. Снимки и дочерние факты защищены от UPDATE/DELETE триггерами; заявка допускает обновления, но не удаление. HTTP создание снимка через mock BillingProvider добавлено в Phase 1.3.

**Phase 1.3:** ревизия `0004_subscriber_360` расширяет существующие таблицы без изменения или удаления прежних данных. Новые nullable поля сохраняют совместимость старых заявок; `field_meta` для прежних снимков получает `{}`. Новые снимки создаются только через `POST /subscriber-lookups` из нормализованного mock-ответа, а не из произвольного JSON клиента.

**Stage 3C:** ревизия `0010_subscriber_finance` добавляет `financial_accounts JSONB NOT NULL DEFAULT []` к immutable `subscriber_snapshots`; старые записи остаются без искусственно восстановленного баланса. `0011_subscriber_history_index` добавляет частичный индекс `(source, subscriber_id, id)` для истории связанных заявок по нескольким счетам. Данные тарифа и биллингового состояния услуги хранятся в уже существующем `services JSONB` с собственным `field_meta`. Финансовые наблюдения никогда не определяют `technical_state` и не выдаются инженеру через Subscriber 360. Заявки и их история остаются бессрочными.

**NetUP address/IP extension:** ревизия `0019_subscriber_address_ip` добавляет
nullable `subscriber_snapshots.house_number` для исходного `users.building`.
Старые snapshots остаются `NULL` и не переписываются. Настроенные IPv4 и
префиксы сохраняются в существующем immutable `connection_snapshots.ip_groups`
JSONB вместе с raw числами каждой строки; DHCP-пул и текущая сессия остаются
отдельными понятиями.

**OSS Diagnostics:** `0012_oss_diagnostics` добавляет immutable `diagnostic_checks` для неподтверждённого server-side preview связанной заявки. `diagnostic_snapshots.check_id` уникально связывает подтверждённый `AUTOMATED` snapshot с check; исторические `MANUAL` snapshots имеют `NULL`. Сохранение события истории и аудита выполняется вместе со snapshot. Кратность IP-групп и исходные строки NetUP не изменяются; см. [границу диагностики](oss-diagnostics.md).

**BDCOM ONU read-only:** `disconnection_at` и `disconnection_time_basis` —
вычисляемые nullable поля ответа из `bdcom_onus`, а не новые таблицы или
поля PostgreSQL. Исторические diagnostic snapshots не меняются; подробнее
в [контракте диагностики](oss-diagnostics.md).

**Identity и редактирование, `0013_identity_permissions`:** новые поля `app_users.must_change_password` и `permissions_customized`, таблица `app_user_permissions` с составным PK `(user_id, permission)` и FK на пользователя, `identity_audit_events.details JSONB`. Исторические пользователи получают явные гранты по прежней роли, их пароли и сессии сохраняются. Новые заявки и snapshots не мигрируются. Редактирование темы и описания использует существующие `tickets.version`, `ticket_history`, `audit_events`; новых таблиц заявок нет. История перед/после хранится в защищённом `ticket_history.event_data`, удаление истории запрещено. Последний активный admin с `users.manage` защищён серверной блокировкой и отложенным trigger при прямом SQL удалении гранта.

## ERD

```mermaid
erDiagram
  app_users ||--o{ tickets : creates
  app_users ||--o{ auth_sessions : opens
  app_users ||--o{ app_user_permissions : granted
  app_users ||--o{ identity_audit_events : acts
  app_users ||--o{ subscriber_snapshots : requests
  subscriber_snapshots ||--o{ connection_snapshots : contains
  subscriber_snapshots ||--o{ tickets : captured_for
  connection_snapshots o|--o{ tickets : selected_for
  tickets ||--o{ ticket_comments : has
  tickets ||--o{ ticket_decisions : has
  tickets ||--o{ diagnostic_snapshots : has
  tickets ||--o{ ticket_history : has
  tickets o|--o{ audit_events : targets
  tickets ||--o{ field_work_handoffs : may_have
  field_work_handoffs ||--o{ field_work_delivery_attempts : attempts
  field_work_handoffs ||--o{ field_external_messages : receives
  field_work_handoffs ||--o{ field_work_completion_prompts : prompts
  app_users ||--o{ ticket_comments : writes
  app_users ||--o{ ticket_decisions : records

  app_users {
    uuid id PK
    text login UK
    text display_name
    text role
    boolean is_active
    text password_hash
    timestamp created_at
    timestamp updated_at
  }
  auth_sessions {
    uuid id PK
    uuid user_id FK
    text token_hash UK
    text csrf_hash
    timestamp created_at
    timestamp expires_at
    timestamp revoked_at_nullable
  }
  auth_login_buckets {
    text kind PK
    text key_digest PK
    int failures
    timestamp window_started_at
    timestamp blocked_until_nullable
  }
  identity_audit_events {
    uuid id PK
    uuid actor_user_id FK
    uuid target_user_id FK
    text action
    text outcome
    text login_digest_nullable
    text source_digest_nullable
    timestamp occurred_at
  }
  subscriber_snapshots {
    uuid id PK
    text account_no
    text subscriber_id_nullable
    text source
    timestamp fetched_at
    uuid requested_by FK
    timestamp expires_at
    text full_name_nullable
    text organization_name_nullable
    text phone_nullable
    text email_nullable
    jsonb field_meta
    jsonb contracts
    jsonb services
  }
  connection_snapshots {
    uuid id PK
    uuid subscriber_snapshot_id FK
    text access_type
    text device_nullable
    text port_nullable
    text vlan_nullable
    text address_nullable
    text ip_group_nullable
    text onu_id_nullable
    jsonb field_meta
  }
  tickets {
    uuid id PK
    bigint number UK
    text ticket_type
    uuid subscriber_snapshot_id_nullable FK
    uuid connection_snapshot_id_nullable FK
    text selected_service_id_nullable
    text account_no_nullable
    text status
    text priority
    text subject_nullable
    text description
    uuid created_by FK
    uuid assigned_engineer_id FK
    uuid closed_by FK
    uuid closed_decision_id FK
    uuid client_request_id
    int version
    timestamp created_at
    timestamp updated_at
    timestamp closed_at_nullable
  }
  ticket_comments {
    uuid id PK
    uuid ticket_id FK
    uuid author_id FK
    text body
  }
  ticket_decisions {
    uuid id PK
    uuid ticket_id FK
    uuid engineer_id FK
    text decision_text
    varchar reason nullable
    text performed_actions nullable
  }
  diagnostic_snapshots {
    uuid id PK
    uuid ticket_id FK
    text source_kind
    text availability
    jsonb observation
  }
  ticket_history {
    uuid id PK
    uuid ticket_id FK
    text event_type
    jsonb event_data
  }
  audit_events {
    uuid id PK
    uuid ticket_id FK
    text action
    text outcome
    text origin
  }
  field_work_handoffs {
    uuid id PK
    uuid ticket_id FK
    int ticket_version
    text reason
    text delivery_status
    text work_status
    text chat_id
    bigint telegram_message_id_nullable
    text message_text_nullable
    uuid created_by FK
    uuid sent_by_nullable FK
    text last_error_code_nullable
  }
  field_work_delivery_attempts {
    uuid id PK
    uuid handoff_id FK
    uuid client_request_id
    uuid actor_id FK
    text status
    text error_code_nullable
    bigint telegram_message_id_nullable
  }
  field_external_messages {
    uuid id PK
    uuid handoff_id FK
    text chat_id
    text message_id
    text bot_identity
    text update_id
    text body
  }
  field_dispatch_updates {
    uuid id PK
    text bot_identity
    bigint update_id
    text kind
    text outcome
    uuid handoff_id_nullable FK
    text sync_status
  }
  field_work_completion_prompts {
    uuid id PK
    uuid handoff_id FK
    uuid callback_update_id FK
    text chat_id
    bigint sender_telegram_id
    text status
    bigint telegram_message_id_nullable
    text error_code_nullable
  }
```

`field_work_handoffs` и `field_work_delivery_attempts` введены в Telegram T1.
`field_external_messages` и `field_dispatch_updates` введены в Telegram T2. Автоматические
источники в `diagnostic_snapshots` вводятся в Phase 4; Phase 1 может сохранять
ручное наблюдение инженера.

## Таблицы Phase 1

| Таблица | Обязательные поля и правила |
| --- | --- |
| `app_users` — 1.2A | UUID `id`, уникальный канонический `login`, `display_name`, `role ∈ {operator, engineer, admin}`, `is_active`, Argon2id `password_hash`, `created_at`, `updated_at`. `admin` управляет идентификацией, без прав на заявки. Первый администратор создаётся только интерактивной CLI-командой на пустой таблице. |
| `auth_sessions` — 1.2A | UUID `id`, `user_id`, SHA-256 хэши случайного токена сессии и CSRF-токена, `created_at`, `expires_at`, nullable `revoked_at`. В cookie не помещаются права или персональные данные. |
| `auth_login_buckets` — 1.2A | Составной ключ `(kind, key_digest)` для нормализованного логина и прямого адреса источника, счётчик отказов, окно и блокировка. Значения HMAC с секретом окружения, без исходного логина/IP. |
| `identity_audit_events` — 1.2A | Append-only UUID `id`, nullable актор и цель, действие, результат, HMAC логина/источника при входе, время. Без паролей, токенов и полных персональных данных. |
| `subscriber_snapshots` | `id` (= `lookup_id` в API), `account_no`, nullable `subscriber_id`, `source ∈ {MOCK, NETUP}`, `fetched_at`, `requested_by`, `expires_at`, nullable ФИО, название организации, телефон, email, договоры и услуги в JSONB, `field_meta` с источником, временем и качеством существенных полей. Phase 1 пишет только `MOCK`. Финансовые данные не входят в техническое состояние. |
| `connection_snapshots` | `id`, `subscriber_snapshot_id`, `access_type ∈ {ETHERNET, EPON, GPON, UNKNOWN}`, nullable адрес, устройство, порт, VLAN, IP-группа, ONU ID и `field_meta`. ONU не требуется и не создаётся для Ethernet или неизвестного типа. Несколько подключений сохраняются раздельно. |
| `tickets` | `id`, уникальный `number`, nullable `account_no`, `subscriber_snapshot_id`, `connection_snapshot_id`, `selected_service_id`, `subject VARCHAR(160)`, обязательное `description`, `status ∈ {OPEN, IN_PROGRESS, RESOLVED, CLOSED}`, `priority ∈ {LOW, NORMAL, HIGH, URGENT}`, `created_by`, nullable `assigned_engineer_id`, timestamps, nullable `resolved_by`/`resolved_at`/`resolved_decision_id` и `closed_by`/`closed_at`/`closed_decision_id`, `version`, `client_request_id`. `subject` добавлен в `0009` без заполнения исторических строк; CHECK запрещает пустое значение и управляющие символы. Для linked выбранные услуга и подключение проверяются по неизменяемому snapshot; при неоднозначности выбор явный. Уникальность `(created_by, client_request_id)` защищает создание от повторного запроса. |
| `ticket_comments` | `id`, `ticket_id`, `author_id`, `body`, `created_at`. Записи не подменяют решение или автоматические данные. |
| `ticket_decisions` | `id`, `ticket_id`, `engineer_id`, `decision_text`, nullable структурированная `reason`, nullable совместимое `performed_actions`, `created_at`. Неизменяемые версии решения; старым строкам причина не приписывается, закрытие ссылается на выбранную версию той же заявки. |
| `diagnostic_snapshots` | `id`, `ticket_id`, nullable `connection_snapshot_id`, `source_kind`, `availability ∈ {KNOWN, UNKNOWN}`, `observation`, `observed_at`, `recorded_at`, обязательный `recorded_by` для ручной проверки. В Phase 1 допускается `source_kind=MANUAL`; автоматические значения появляются только в Phase 4. |
| `ticket_history` | `id`, `ticket_id`, `event_type`, nullable `actor_id`, `event_data`, `occurred_at`. Append-only история доменных фактов: создание, начало работы, комментарий, решение, закрытие. Индекс `0009` по `(occurred_at, id)` поддерживает глобальную ленту; публичная сводка не выдаёт `event_data`. |
| `audit_events` | `id`, nullable `ticket_id`, nullable `actor_id`, `action`, `outcome`, `origin`, `occurred_at`. Append-only след изменений; полные ФИО, телефон, адрес, секреты и текст жалобы в технический аудит не копируются. Отказы доступа также отражаются в `identity_audit_events` по существующему механизму identity. |

Содержимое обращения, комментария, решения и snapshot защищено правами доступа и политикой хранения. Поиск Subscriber 360 создаёт snapshot в PostgreSQL. Начиная с ревизии `0006_lookup_cleanup`, snapshot без заявки может быть удалён контролируемой процедурой через 24 часа от `fetched_at`; это отдельный срок от `expires_at` (15 минут для создания заявки). Запись, связанная с заявкой, и история заявки сохраняются бессрочно. Дочерние connection snapshots удаляются в той же транзакции. Обезличенный `lookup_cleanup_audit` хранит время, cutoff и число удалённых записей, без ЛС и персональных данных. FK и блокировки защищают от гонки с созданием заявки. Ревизия `0007_contacts` добавляет `contacts JSONB NOT NULL DEFAULT []`: старые snapshots остаются читаемыми, новые сохраняют альтернативные телефоны с типом, источником, временем и качеством без эвристического выбора.

## Таблицы Phase 3 и независимость состояний

- `field_work_handoffs`: T1 хранит `ticket_id`, версию заявки, причину и
  сформированный snapshot сообщения, фиксированный `chat_id`, подтверждённый
  `telegram_message_id`, автора, отправителя, времена и технический код ошибки.
  `delivery_status ∈ {DRAFT,SENDING,SENT,FAILED,UNCERTAIN}` и
  `work_status=AWAITING_REPORT` независимы от `tickets.status`.
- `field_work_delivery_attempts`: каждая явная попытка имеет уникальный
  `(handoff_id, client_request_id)`, автора, состояние, технический код ошибки,
  подтверждённый Telegram message ID и времена. Это защищает от двойного
  нажатия; при `UNCERTAIN` автоматический повтор запрещён.
- `field_external_messages`: `id`, `handoff_id`, `update_record_id`, `chat_id`,
  nullable Telegram message ID, проверенный отправитель, вид, текст и времена.
  Уникальность входящего `(chat_id, message_id)` и одной completion-записи на
  handoff защищает доменную историю; транспортный `update_id` хранится в receipt.
- `field_dispatch_updates`: минимальный обезличенный receipt каждого принятого,
  отклонённого или повторного update. Уникальность `(bot_identity, update_id)`
  обеспечивает транспортную идемпотентность; raw Telegram payload не хранится.
  Статус синхронизации callback с Telegram отделён от доменного результата.
- `field_work_completion_prompts` (`0022`): один callback receipt на prompt,
  числовой ID запросившего монтажника, подтверждённый Telegram message ID
  вопроса и состояние `REQUESTING`, `WAITING`, `FAILED`, `UNCERTAIN` или
  `COMPLETED`. Частичный уникальный индекс допускает только один активный
  prompt на handoff. При `UNCERTAIN` автоматическая повторная отправка
  запрещена. Исторические T2 сообщения и факты `REPORTED` сохраняются.
- Закрытие заявки требует отдельного решения инженера в `ticket_decisions` и обновления `tickets.status=CLOSED`. Получение сообщения или изменение `work_state` не вызывает закрытия.

## Транзакционные инварианты

Phase 5.1 не добавляет таблиц или столбцов: точная связь активных обращений
читается через `tickets.subscriber_snapshot_id` и immutable поля
`subscriber_snapshots.source/subscriber_id/account_no`. Причина явного создания
другой заявки сохраняется в `ticket_history.event_data` события
`ticket_created`; `audit_events` получает факт создания без текста причины.
`CLOSED` и soft-deleted обращения остаются в истории, но не в активной проверке.

1. Создание заявки одновременно записывает `tickets`, `ticket_history` и `audit_events` либо не записывает ничего.
2. Комментарий, ручное наблюдение, решение и закрытие сопровождаются историей и аудитом в той же транзакции.
3. `closed_by` — активный инженер OSS; `closed_at` и ссылка на решение обязательны для `CLOSED`. Закрытая заявка в Phase 1 не редактируется без отдельного будущего правила переоткрытия.
4. Данные billing snapshot, мониторинга и ручной проверки различаются по источнику. `UNKNOWN` не хранится как доказательство `DOWN`.
5. Значения `chat_id` и `message_id` появляются только после подтверждённой отправки Bot API; ручной fallback не подделывает их.

## Добавочные snapshots NetUP provider 1.0

Миграция `0005_netup_groups` расширяет immutable `subscriber_snapshots`
полями `accounts JSONB DEFAULT []` и nullable `actual_address`, а
`connection_snapshots` — nullable `service_id` и `ip_groups JSONB DEFAULT []`.
Новый JSON сохраняет `ServiceLink → IpGroupKey → IpGroupRow[]` без смешения
назначенной IP-адресации, DHCP-пула и заявленной биллинговой топологии.
Старые snapshots читаются с пустыми списками; PostgreSQL остаётся source of
truth для зафиксированного в заявке наблюдения. См.
[`ADR-001`](../netup/adr-001-provider-boundary.md).
