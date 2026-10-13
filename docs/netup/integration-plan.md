# План read-only NetUPBillingProvider после discovery

**Статус:** проект, интеграция не начата. Discovery проводился на отдельной
восстановленной копии, не на production NetUP. Нет изменений приложения,
миграций, конфигурации MariaDB, SSH или сети.

## Подтверждённые предпосылки и решения до реализации

1. **UNKNOWN / блокирует production lookup:** владелец биллинга должен
   подтвердить, что операторский «номер лицевого счёта» — десятичный
   `accounts.id`, включая формат/ведущие нули. `account_name` и `external_id`
   не дают альтернативы в этой копии. До подтверждения не превращать ввод
   пользователя в SQL-число и не утверждать готовый endpoint.
2. **UNKNOWN / блокирует отображение топологии как KNOWN:** владелец сети
   должен подтвердить смысл `ip_groups.switch_id`, `port_id`, `vlan_id`,
   формат `switches.name` и правило разрешения восьми конфликтных групп.
   Источник Ethernet/EPON/GPON и ONU ID пока отсутствует; допускается
   `UNKNOWN` без блокировки прочих данных Subscriber 360.
3. **CONFLICT с текущим контрактом:** согласовать выбор из нескольких
   телефонов и размер поля `phone` (четыре live значения >50). Согласовать
   адрес уровня абонента отдельно от адреса подключения и необходимость
   полей типа/тарифа услуги, IP/MAC и качество пустых коллекций
   `contracts[]`/`connections[]`. Не обрезать исходные значения.
4. **UNKNOWN:** бизнес-семантика блокировок и кодов `service_type=2`,
   `ip_type`, `blocks_info.block_type`. Эти поля можно не включать в первый
   адаптер; отсутствие не блокирует mock-based Phase 1.

## Разделение слоёв

`NetUP read-only query port` → типизированные source DTO → нормализатор
`Subscriber360` → неизменяемый PostgreSQL snapshot → Ticket Engine.
Только query port знает таблицы UTM5; Ticket Engine не получает SQL или
доступ к NetUP. PostgreSQL остаётся единственным source of truth для заявок.
Никакие данные из лабораторной копии не переносятся в основную dev-БД.

После утверждения интерфейса runtime использует отдельные read-only реквизиты,
параметризованные запросы, ограничение времени/числа строк, запрет логирования
SQL-параметров и PII, аудит факта поиска без значения лицевого счёта в общем
техническом логе. Несколько запросов выполняются в одном согласованном
read-only snapshot, чтобы user/service/group относились к одной версии
источника. Ошибка источника даёт `UNAVAILABLE` и не создаёт billing snapshot.

## Форма будущих запросов (НЕ выполнялась в discovery)

Ниже SQL с bind-параметрами для runtime после решения пункта 1. Он может
возвращать персональные данные; **не запускать вручную с реальным счётом и не
сохранять результат**. Проверенные безопасные агрегаты и EXPLAIN находятся
отдельно в [`sql/`](sql/). `:account_id`, `:user_id`, `:as_of` передаются
драйвером как параметры, никогда строковой конкатенацией.

```sql
-- Lookup: LIMIT 2 позволяет обнаружить неоднозначность, не выбирать первую.
SELECT a.id AS account_id, u.id AS user_id, u.full_name, u.is_juridical,
       u.mobile_telephone, u.work_telephone, u.home_telephone, u.email,
       u.actual_address, u.last_change_date
FROM accounts a
JOIN users_accounts ua ON ua.account_id=a.id AND ua.is_deleted=0
JOIN users u ON u.id=ua.uid AND u.is_deleted=0
WHERE a.id=:account_id AND a.is_deleted=0
LIMIT 2;
```

```sql
-- Экземпляры услуг, включая заблокированные: блокировка не удаляет услугу.
-- LIMIT 51 обнаруживает превышение текущего MAX_ITEMS=50 без усечения.
SELECT sl.id AS service_link_id, sd.id AS catalog_service_id,
       sd.service_name, sd.service_type, psl.is_blocked,
       psl.start_date, psl.expire_date, sl.tariff_link_id
FROM service_links sl
JOIN services_data sd ON sd.id=sl.service_id AND sd.is_deleted=0
JOIN periodic_service_links psl ON psl.id=sl.id AND psl.is_deleted=0
WHERE sl.account_id=:account_id AND sl.user_id=:user_id
  AND sl.is_deleted=0 AND psl.is_planned=0 AND psl.need_del=0
  AND psl.start_date<=:as_of AND psl.expire_date>:as_of
ORDER BY sl.id LIMIT 51;
```

```sql
-- По одной проверенной IP-услуге; MIN — лишь кандидат при distinct=1.
SELECT isl.id AS service_link_id, isl.ip_group_id,
       COUNT(g.id) AS group_rows,
       COUNT(DISTINCT NULLIF(g.switch_id,0)) AS switch_variants,
       MIN(NULLIF(g.switch_id,0)) AS switch_candidate,
       COUNT(DISTINCT NULLIF(g.port_id,0)) AS port_variants,
       MIN(NULLIF(g.port_id,0)) AS port_candidate,
       COUNT(DISTINCT NULLIF(g.vlan_id,0)) AS vlan_variants,
       MIN(NULLIF(g.vlan_id,0)) AS vlan_candidate,
       COUNT(DISTINCT CASE WHEN g.switch_id>0 AND g.port_id>0
                            AND g.vlan_id>0
                          THEN CONCAT(g.switch_id,'/',g.port_id,'/',g.vlan_id)
                     END) AS topology_variants
FROM service_links sl
JOIN services_data sd ON sd.id=sl.service_id AND sd.service_type=3
JOIN iptraffic_service_links isl ON isl.id=sl.id AND isl.is_deleted=0
LEFT JOIN ip_groups g ON g.ip_group_id=isl.ip_group_id AND g.is_deleted=0
WHERE sl.id=:service_link_id AND sl.account_id=:account_id
  AND sl.user_id=:user_id AND sl.is_deleted=0
GROUP BY isl.id, isl.ip_group_id;
```

Если `topology_variants>1`, связь конфликтна; если `group_rows=0`, она
неизвестна. Только после проверки `switch_variants=1` и согласования
семантики отдельный запрос по `switches.id=:switch_candidate` может получить
`name`, `type_id`, `is_deleted`. Никакие credentials из `switches`, `ip_groups`
или иных таблиц не выбираются. Договоры не запрашиваются: `contracts` пуста.

## Производительность и безопасность

- **VERIFIED EXPLAIN:** PK `accounts.id` даёт `const`; обратное ребро до
  пользователя требует скана примерно 5 тыс. `users`, так как индекса с
  ведущим `users_accounts.account_id` нет. На росте согласовать индекс или
  поддерживаемый read-only интерфейс с владельцем UTM5; здесь DDL запрещён.
- **VERIFIED EXPLAIN:** `service_links.account_id`, PK подтипов и индекс
  `ip_groups.ip_group_id` используются. Ограничивать количество ссылок;
  превышение лимита — явная ошибка, не тихое сокращение карточки.
- Не сканировать `dhs_sessions_log` (~13,8 млн оценочных строк), транзакции
  и журналы для Subscriber 360. Не использовать свободный текст для поиска
  ONU/договора без отдельной модели защиты и подтверждённой семантики.
- Запросы тестировать на синтетических fixtures и агрегатных инвариантах.
  Интеграционные тесты на lab должны проверять только счётчики/качество,
  не включать реальные PII в stdout, pytest assertions или snapshots.
- Перед production включением проверить read-only права, таймауты, пределы
  соединений, аудит доступа и реакцию на недоступность. Реальные реквизиты
  и production NetUP здесь не используются.
