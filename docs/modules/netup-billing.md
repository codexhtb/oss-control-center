# NetUP BillingProvider

**Назначение.** Read-only adapter для получения subscriber/account, services, tariffs и financial observations. Реализация находится в `app/modules/billing/netup/`; provider interface и MOCK — `app/modules/billing/`.

**Источник.** Отдельный MariaDB/UTM5 endpoint, ожидаемо replica. Upstream replication config и DBA grants не находятся в коде. Включение происходит только при конфигурации `BILLING_PROVIDER=NETUP` и наличии защищённых реквизитов.

**API пользователя:** через `/api/v1/subscriber-lookups`; внутренний provider protocol включает lookup/search/service/tariff/finance reads. Это не публичный SQL API.

**Безопасность.** Параметризованные `SELECT`, consistent read-only transaction для subscriber lookup, лимиты ожидания/результатов/размеров. Credential берётся из environment/protected runtime, не пишется в логи. Изменений NetUP этот модуль не делает.

**Хранение и права.** Нормализованные bounded observations фиксируются как PostgreSQL snapshots; permission — `subscribers.read`, finance read дополнительно `subscribers.finance.read`.

**Ошибки и ограничения.** Уплотнение данных UTM5 может быть неоднозначным, набор услуг/валют и историческая глубина зависят от реальных source rows. Статус adapter в текущем runtime **UNVERIFIED**; безопасно подтвердить через конфигурационный инвентарь без вывода значений секретов, API readiness и контролируемый lookup без записи в NetUP.

## Граница repository

Связи `users` → `users_accounts` → `accounts` и service/finance rows читаются параметризованным SQL с bounded result, timeout и read-only transaction. Архивный discovery SQL нельзя переносить в production-код без сверки schema. Normalizer не смешивает billing ID, network device ID и LibreNMS ID.

Amount/currency передаются без float-округления и без подстановки валюты. При сбое проверяют read-only probe/grants/lag у владельца replica; DSN не помещают в runbook. См. [NetUP operations](../operations/netup-replica.md) и [индекс NetUP](../netup/README.md).

| Слой | Ответственность |
| --- | --- |
| `BillingProvider` | стабильная доменная граница и ошибки |
| NetUP repository | parameterized SELECT, timeout, transaction |
| normalizer | source fields → typed snapshot без догадок |
| billing API | permission, ownership lookup, audit outcome |

Регрессия обязана покрывать точный ЛС, неоднозначный numeric login, несколько accounts, whitespace/case ФИО, недоступную replica и запрет любого write SQL.
