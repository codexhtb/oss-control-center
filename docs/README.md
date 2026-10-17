# Документация OSS Control Center

**Актуальный навигационный индекс (17.10.2026).** Документы сгруппированы по назначению. Не все материалы имеют одинаковый статус: `CURRENT` означает сверку с кодом OSS на `main` (`8b7a1ea`), `HISTORICAL` — фиксацию старой фазы, `RUNBOOK` — процедуру для конкретного окружения, которую перед выполнением сверяют с настройками этого окружения.

## С чего читать

1. [Текущая архитектура](architecture/current-system.md) — что где работает и как проходит заявка.
2. [NetUP Provider](integrations/netup.md) — чтение биллинга, snapshots, данные с конфликтами.
3. [LibreNMS / BDCOM](integrations/librenms-bdcom.md) — сбор ONU, оптика, корреляция с NetUP и доступ из OSS.
4. [Карта восстановления](operations/recovery-map.md) — граница GitHub/серверы/секреты/БД.

## Эксплуатационные runbooks

| Тема | Документ | Статус |
| --- | --- | --- |
| Изолированный Compose dev / HTTPS | [dev-lab](operations/dev-lab.md) | RUNBOOK, содержит исторические этапы |
| BDCOM SQL read-only + SSH tunnel | [bdcom-onu-readonly](operations/bdcom-onu-readonly.md) | RUNBOOK |
| Telegram field dispatch T1 | [telegram-field-dispatch](operations/telegram-field-dispatch.md) | RUNBOOK |
| Хранение секретов | [secret-management](operations/secret-management.md) | RUNBOOK |
| Backups / зависимости | [recovery-map](operations/recovery-map.md) | CURRENT: инвентаризация, не автоинсталлятор |

## Глубокие контракты

- [API](architecture/api-contracts.md); [модель данных](architecture/data-model.md).
- [RBAC](architecture/rbac.md); [контрольный обзвон Phase 5.7](architecture/phase-5.7-callback.md).
- [Упрощённая карточка и причины решения Phase 5.8](architecture/phase-5.8-simplified-ticket-ui.md).
- [Диагностика и ограничения измерений](architecture/oss-diagnostics.md).
- [NetUP relationships](netup/relationships.md), [provider-data-limitations](netup/provider-data-limitations.md), [provider deployment](netup/provider-deployment.md), [SQL discovery](netup/sql/).

## Как отличать факты от планов

- **Реализовано**: только то, что подтверждается текущим исходным кодом/тестами или предоставленным выводом реально работающего сервиса.
- **Dev/LAB**: интеграция может работать, но не является production-интеграцией.
- **Не подключено**: код или схема существует, но runtime-соединение не настроено (например, Icinga).
- **План**: Telegram T2, редактирование шаблонов, production NetUP и дополнительные аналитические возможности.
- **Исторический источник**: большой LibreNMS + BDCOM Full Documentation/Runbook от **14.09.2026** описывает старое проверенное состояние и SNMP-логику, но сам файл не импортирован в GitHub без проверки чувствительных данных и сверки с живыми скриптами.

Файлы в `docs/architecture/` с именами `phase-*`, `review`, `roadmap`, `system-architecture.md` и прежний `architecture/README.md` сохраняются как архитектурная история. Их будущие обещания и ранние workflows не отменяют состояние, описанное в `current-system.md`.

## Поддержание актуальности

При изменении API, миграций, структуры данных, collector OID, mapping rules, таймеров или recovery-процедур обновлять соответствующие страницы вместе с кодом. Для внешних компонентов фиксировать дату проверки, origin (live service / code / historical report), номер/хеш исходника и ограничения. В Git не хранить секреты и subscriber PII.
