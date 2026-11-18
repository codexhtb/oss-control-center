# NetUP BillingProvider

**Назначение.** Read-only adapter для получения subscriber/account, services, tariffs и financial observations. Реализация находится в `app/modules/billing/netup/`; provider interface и MOCK — `app/modules/billing/`.

**Источник.** Отдельный MariaDB/UTM5 endpoint, ожидаемо replica. Upstream replication config и DBA grants не находятся в коде. Включение происходит только при конфигурации `BILLING_PROVIDER=NETUP` и наличии защищённых реквизитов.

**API пользователя:** через `/api/v1/subscriber-lookups`; внутренний provider protocol включает lookup/search/service/tariff/finance reads. Это не публичный SQL API.

**Безопасность.** Параметризованные `SELECT`, consistent read-only transaction для subscriber lookup, лимиты ожидания/результатов/размеров. Credential берётся из environment/protected runtime, не пишется в логи. Изменений NetUP этот модуль не делает.

**Хранение и права.** Нормализованные bounded observations фиксируются как PostgreSQL snapshots; permission — `subscribers.read`, finance read дополнительно `subscribers.finance.read`.

**Ошибки и ограничения.** Уплотнение данных UTM5 может быть неоднозначным, набор услуг/валют и историческая глубина зависят от реальных source rows. Статус adapter в текущем runtime **UNVERIFIED**; безопасно подтвердить через конфигурационный инвентарь без вывода значений секретов, API readiness и контролируемый lookup без записи в NetUP.
