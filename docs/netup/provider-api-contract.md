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
| `connections[].ip_groups[]` | Ключи группы и **все** исходные live строки. Одна строка содержит `row_id`, `ip_type_code`, raw числовые `ip/mask/ip_ext/mask_ext`, `pool_id_raw`, `switch_id_raw`, `port_id`, `vlan_id`, отдельно пригодные для отображения `configured_ipv4`, `configured_prefix_length`, `address_assignment`, `dhcp_pool_id`, `switch_id`, `device`, DHCP-диапазоны и provenance. Нулевые raw значения сохраняются. `configured_ipv4/prefix` — конфигурация IP-группы NetUP, не доказательство активной DHCP/RADIUS-сессии. |
| `subscriber.actual_address`, `subscriber.house_number` | Исходные `users.actual_address` и `users.building`. UI добавляет `house_number` как `д. …`, если номер ещё не присутствует в адресной строке. Это адресные данные пользователя в биллинге, не подтверждённый адрес конкретного подключения. |

Текущий динамически выданный IPv4 не входит в контракт: `dhcp_leases` не содержит
прямого account ID, журналы lease/session являются отдельными историческими
источниками и в этой версии не запрашиваются. При наличии `[Авто]` UI показывает
пул отдельно и сообщает, что текущий IP не определён.
| `subscriber.contacts[]` | Добавочный список телефонных наблюдений с `type ∈ {mobile,work,home}`, `value`, точным `source`, `observed_at` и `quality`. Оператор и инженер с `subscriber.read` видят все варианты. `subscriber.phone` остаётся прежним полем; при разных номерах или единственном номере длиннее 50 символов оно `null` с `CONFLICT`, без выбора «основного». |

Прежние `connections[].device/port/vlan/ip_group` остаются. Это удобная
проекция только при единственной полной непротиворечивой тройке/группе.
Несколько разных троек дают `null` и `field_meta.quality=CONFLICT`; исходные
строки сохраняются. `access_type=UNKNOWN`, `onu_id=null`, техническое
состояние `UNKNOWN`, пока отдельный источник не подтвердит иное.

Дополнительные значения сохраняются в immutable PostgreSQL snapshot.
Миграции `0005_netup_groups` и `0007_contacts` добавляют JSONB с `[]` по умолчанию и nullable
поля, поэтому старые карточки/заявки читаются без преобразования. Клиенты,
жёстко запрещающие новые ключи JSON-ответа, должны обновить парсер.

## Stage 3C — тарифы, финансовые признаки и история

`services[]` дополнены nullable `tariff_name`, `billing_active` и
`billing_blocked`, каждое с `field_meta`. `tariff_name` появляется только
после проверенного JOIN одного `service_links.tariff_link_id` с
`account_tariff_link` того же счёта и каталожного тарифа; для услуг типа 2
в этой копии он `UNKNOWN`. `billing_active=true` означает прохождение
фильтров действия связки на дату источника; `billing_blocked` относится
к `periodic_service_links.is_blocked`, не к работоспособности сети.

`financial_accounts[]` сохраняет баланс, кредит и сырой признак блокировки
для каждого связанного счёта в immutable snapshot. Баланс и кредит
сериализуются десятичными строками в единицах UTM5; валюту или сумму
доступного остатка адаптер не выводит. Только `operator` получает массив
через HTTP (`subscriber.finance.read`); `engineer` получает `[]`, `admin`
не имеет `subscriber.read`. Старые и mock snapshots остаются валидны с
пустым массивом. `technical_state` остаётся `UNKNOWN`.

`GET /api/v1/subscriber-lookups/{lookup_id}/tickets?page=1&size=20`
возвращает страницу связанных заявок. Требуются автор lookup,
`subscriber.read` и `ticket.read`; совпадение — `(source, subscriber_id)`
или точный `(source, account_no)`, если ID неизвестен. `size` 1–50,
ответ `Cache-Control: no-store`. Browser URL карточки содержит только
`lookup_id`, не номер ЛС.

Ревизии `0010` и `0011` добавляют JSONB финансовых наблюдений и частичный
индекс истории; ранее применённые миграции не изменяются.

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
