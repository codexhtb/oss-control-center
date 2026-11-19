# NetUP: актуальная навигация

Текущий контракт определяется кодом `app/modules/billing/`, [модульным описанием](../modules/netup-billing.md) и [runbook replica](../operations/netup-replica.md). OCC выполняет только параметризованное read-only чтение MariaDB replica и сохраняет нормализованные snapshots в PostgreSQL.

Архив содержит исследования схемы и SQL-контрактов, которые не были удалены:

- [provider API contract](../archive/netup/provider-api-contract.md) и [граница provider](../archive/netup/adr-001-provider-boundary.md);
- [schema inventory](../archive/netup/schema-inventory.md), [relationships](../archive/netup/relationships.md), [service model](../archive/netup/service-model.md);
- [data quality](../archive/netup/data-quality.md) и [ограничения данных](../archive/netup/provider-data-limitations.md);
- [Subscriber 360 mapping](../archive/netup/subscriber-360-mapping.md) и [evidence v2](../archive/netup/utm5-subscriber-360-v2-evidence.md);
- [исторические security/runbook/deployment материалы](../archive/netup/provider-security.md).

Все перечисленные страницы имеют статус HISTORICAL. SQL, имена таблиц, grants и финансовые формулы перед повторным использованием сверяются с текущей replica и тестами. Не выполняйте скрипты из архивов и не добавляйте DSN/учётные данные в Git.
