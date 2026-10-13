# `lookup_by_account(account_no)`: SQL-контракт discovery

> Историческая валидация схемы. Реализованный read-only контракт и пакетные
> SQL-запросы см. в `app/modules/billing/netup/repository.py`; этот документ
> сохраняет доказательства Discovery и не описывает текущий статус кода.

Статус: **проект read-only адаптера**, production-код не изменён. Операторский
поиск по ЛС — основной обязательный сценарий. В единственном сверенном
примере GUI основной ЛС равен одновременно `users.basic_account` и
`accounts.id`; неудалённая строка `users_accounts` связывает этот счёт с
пользователем. Это **VERIFIED для примера и структуры копии**, а формат всех
возможных внешних ЛС, в частности ведущие нули, ещё **UNKNOWN**. SQL-проверки:
[`60_account_lookup_validation.sql`](sql/60_account_lookup_validation.sql).

Для повторной сверки разрешённого примера GUI использовать только агрегатный
запрос ниже с параметрами, переданными драйвером. Значения параметров и
результирующие ID не писать в файлы или общие логи:

```sql
SELECT COUNT(*) AS user_matches,
       COALESCE(SUM(u.basic_account=:gui_account_id),0) AS basic_matches,
       COALESCE(SUM(a.id IS NOT NULL AND a.is_deleted=0),0) AS live_account_matches,
       COALESCE(SUM(ua.uid IS NOT NULL AND ua.is_deleted=0),0) AS live_association_matches
FROM users u
LEFT JOIN accounts a ON a.id=:gui_account_id
LEFT JOIN users_accounts ua ON ua.uid=u.id AND ua.account_id=:gui_account_id
WHERE u.id=:gui_user_id;
```

На копии все четыре счётчика равны 1. Это подтверждает ровно сверенный
пример, но не неизвестные правила форматирования других ЛС.

## Вход и результаты

- `account_no`: строка канонического десятичного положительного `accounts.id`
  без пробелов, знака и ведущих нулей; диапазон signed `INT` MariaDB. Другой
  формат отклоняется как `INVALID_ACCOUNT_FORMAT`, а не преобразуется
  молча. Если интерфейс использует иное форматирование, этот контракт надо
  пересмотреть до production включения.
- Единственный live `accounts.id` с единственным live пользователем через
  `users_accounts` → загрузить `Subscriber360`. `users.basic_account` —
  признак основного счёта, а не условие отбора: поиск по дополнительному
  счёту должен работать. Список всех live счетов найденного пользователя
  загружается отдельно с ограничением числа строк.
- Счёт отсутствует или помечен удалённым → `NOT_FOUND` (`None` в нынешнем
  `BillingProvider`). Счёт существует, но не имеет live связи с live
  пользователем → `DATA_INCOMPLETE`, без фиктивной карточки. Более одного
  различного live пользователя для одного счёта → `AMBIGUOUS_ACCOUNT`, без
  выбора первого. Сбой/таймаут источника → `UNAVAILABLE`.
- Конфликт `users.basic_account` с существованием/связью основного счёта
  отмечается как качество данных; он не превращает дополнительный счёт в
  другого абонента. Ограничения текущего provider-протокола (`None` или
  `BillingUnavailable`) не позволяют выразить `DATA_INCOMPLETE` и
  `AMBIGUOUS_ACCOUNT`; перед реализацией адаптера нужен типизированный
  результат/исключения и стабильное API-отображение.

## Параметризованные чтения

`:account_id` — связанный параметр типа `INT`, полученный после строгой
валидации `account_no`. `:user_id` также связан драйвером. Не подставлять
значения строковой конкатенацией. Запросы выполнять в одном согласованном
read-only снимке с таймаутом и без логирования параметров/строк.

```sql
SELECT a.id
FROM accounts a
WHERE a.id=:account_id AND a.is_deleted=0;
```

```sql
SELECT ua.uid AS user_id
FROM users_accounts ua
JOIN users u ON u.id=ua.uid AND u.is_deleted=0
WHERE ua.account_id=:account_id AND ua.is_deleted=0
ORDER BY ua.uid LIMIT 2;
```

`LIMIT 2` служит только для обнаружения неоднозначности. После единственного
совпадения нужно проверить неизменность этой пары внутри того же снимка и
загрузить пользователя, все его live счета и услуги. Основной счёт проверяется
по `users.basic_account`; нельзя считать выбранный счёт основным автоматически.

```sql
SELECT a.id AS account_id,
       CASE WHEN a.id=u.basic_account THEN 1 ELSE 0 END AS is_primary
FROM users u
JOIN users_accounts ua ON ua.uid=u.id AND ua.is_deleted=0
JOIN accounts a ON a.id=ua.account_id AND a.is_deleted=0
WHERE u.id=:user_id AND u.is_deleted=0
ORDER BY a.id LIMIT 51;
```

Предел списка должен давать явную ошибку при превышении, а не обрезать
данные. Для каждой пары `(user_id, account_id)` услуги извлекаются по
проверенной цепочке из [`integration-plan.md`](integration-plan.md); строки
`ip_groups` загружаются отдельно по каждому `ip_group_id`, без выбора одного
`MIN()` как подключения. Если сервисных связок или строк группы больше
договорённого предела, нужен явный отказ/пагинация.

## Проверка на копии

**VERIFIED:** все 4 955 live связи счёта и пользователя совпадают с
`users.basic_account`; live вторичных счетов и счетов с несколькими live
пользователями в копии нет. У десяти пользователей встречались несколько
счетов за всю историю, но одновременно live нескольких нет. Поэтому
обработка множественных счетов и неоднозначных владельцев — обязательная
защита для будущей базы, хотя happy path копии их не показывает.

`EXPLAIN` подтверждает PK lookup `accounts.id` (`const`, 1 строка), затем
скан `users` примерно на 5 тыс. строк и `eq_ref` по составному индексу
`users_accounts(uid,account_id,is_deleted)`. Индекса с ведущим `account_id`
нет. Перед production нагрузкой согласовать поддерживаемый индекс или
официальный read-only интерфейс с владельцем UTM5; здесь DDL не выполнялось.
