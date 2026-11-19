# Интеграции

Актуальные границы интеграций описаны в модульных и эксплуатационных документах:

- [NetUP BillingProvider](../modules/netup-billing.md) и [операции replica](../operations/netup-replica.md);
- [LibreNMS, Icinga и BDCOM](../modules/monitoring.md) и [операции LibreNMS](../operations/librenms-integration.md);
- [Telegram Field Dispatch](../modules/field-dispatch.md), [Mini App](../modules/field-work-miniapp.md) и [операции Telegram](../operations/telegram-operations.md).

Прежние подробные обзоры сохранены как HISTORICAL: [LibreNMS/BDCOM](../archive/integrations/librenms-bdcom.md), [NetUP](../archive/integrations/netup.md). Они содержат полезное происхождение решений, но могут описывать старые API, mappings и окружение. Не используйте их как runbook без сверки с кодом и текущей конфигурацией.

Общее правило: OCC пишет только в PostgreSQL приложения. NetUP, LibreNMS и BDCOM используются read-only; Telegram Bot API является транспортом, а не источником состояния workflow.
