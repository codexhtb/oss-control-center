# NetUPBillingProvider 1.0: API и обратная совместимость

Статус: реализация подготовлена, `BILLING_PROVIDER=MOCK` остаётся значением
по умолчанию. Публичный HTTP путь прежний: `POST /api/v1/subscriber-lookups`
с `{"account_no":"..."}` и `GET /api/v1/subscriber-lookups/{lookup_id}`.
ЛС — строка; ведущие нули, пробелы и иной формат не приводятся к числу
незаметно. Для NetUP допустим только канонический положительный десятичный
`accounts.id`; иной формат даёт `422 account_format_unsupported`.

## Добавочные поля ответа

| Поле | Содержание |
|---|---|
| `accounts[]` | Все live счета найденного пользователя: `account_id`, `is_primary`, `field_meta`. `subscriber.billing_account` — запрошенный ЛС. |
| `services[].account_id` | Счёт конкретной сервисной связки. Старые mock записи могут иметь `null`. |
| `connections[].service_id` | ID сервисной связки, породившей подключение. Для старых snapshots `null`. |
| `connections[].ip_groups[]` | Ключи группы и **все** исходные live строки. Одна строка содержит `row_id`, `ip_type_code`, raw числовые `ip/mask/ip_ext/mask_ext`, `pool_id_raw`, `switch_id_raw`, `port_id`, `vlan_id`, отдельно пригодные для отображения `dhcp_pool_id`, `switch_id`, `device`, DHCP-диапазоны и происхождение. Нулевые исходные значения остаются нулями; коды и raw адреса не представляются как декодированный IPv4. |
| `subscriber.actual_address` | Адрес пользователя в биллинге; это не подтверждённый адрес подключения. |

Прежние `connections[].device/port/vlan/ip_group` остаются. Это удобная
проекция только при единственной полной непротиворечивой тройке/группе.
Несколько разных троек дают `null` и `field_meta.quality=CONFLICT`; исходные
строки сохраняются. `access_type=UNKNOWN`, `onu_id=null`, техническое
состояние `UNKNOWN`, пока отдельный источник не подтвердит иное.

Дополнительные значения сохраняются в immutable PostgreSQL snapshot.
Миграция `0005_netup_groups` добавляет JSONB с `[]` по умолчанию и nullable
поля, поэтому старые карточки/заявки читаются без преобразования. Клиенты,
жёстко запрещающие новые ключи JSON-ответа, должны обновить парсер.

## Результаты и ошибки

| Случай | Поведение |
|---|---|
| Нет live `accounts.id` | `404 subscriber_not_found`, snapshot не создаётся. |
| Неканонический ЛС, включая ведущий ноль | `422 account_format_unsupported`, без SQL-запроса. |
| Более одного live пользователя у счёта | `409 billing_ambiguous`, без выбора первого. |
| Счёт есть, но нет допустимого owner/основного счёта/группы; превышен лимит | `409 billing_incomplete`, без неполного snapshot. |
| DB/TLS/таймаут/ошибка чтения | `503 billing_unavailable`, без автоматической подмены mock. |

`POST /tickets` принимает прежний payload. Если `services[].account_id`
заполнен, выбранная услуга должна относиться к запрошенному ЛС; иначе
`422 service_account_mismatch` — оператор ищет нужный счёт отдельно.
Если `connections[].service_id` заполнен, подключение должно принадлежать
выбранной услуге; иначе `422 service_connection_mismatch`. Это устраняет
перекрёстный выбор между счетами/услугами, не меняя прежние mock snapshots.

`BillingProvider` сохраняет метод `lookup_by_account(str)`; новые
типизированные ошибки `BillingAmbiguous`, `BillingIncomplete`,
`BillingInvalidAccount` дополняют `None` и `BillingUnavailable`.
Лимиты одного lookup: до 50 счетов, 50 услуг, 200 IP-групп и 2000 исходных
строк групп, 5000 DHCP-диапазонов и 2 000 000 байт сериализованной карточки.
Превышение даёт явную ошибку без усечения карточки. Максимум восемь
одновременных NetUP lookup на процесс API; перегрузка возвращает
`503 billing_unavailable`.
