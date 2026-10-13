# OSS Control Center — утверждённая архитектура Phase 0

Архитектурное ревью проведено. Phase 1 можно разрабатывать на mock BillingProvider без production API NetUP. Настоящие интеграции вводятся по [утверждённому roadmap](roadmap.md).

- [Процесс AS-IS / TO-BE](process.md)
- [Доменная модель](domain-model.md) и [модель данных / ERD](data-model.md)
- [Service Desk workflow](service-desk-workflow.md)
- [Системная архитектура](system-architecture.md)
- [API contracts](api-contracts.md) и [RBAC matrix](rbac.md)
- [Модуль field_dispatch](field-dispatch.md) — проект Phase 3
- [Phase 1 implementation plan](phase-1-plan.md)
- [Phase 1.4: три типа заявок — архитектура и план](phase-1.4-ticket-types.md)
- [Acceptance criteria и тестирование](acceptance-and-testing.md)
- [Решения ревью и остающиеся вопросы](review.md)

**Source of truth:** PostgreSQL хранит заявки, комментарии, решения, историю, аудит и диагностические snapshots. ServiceDesk Plus остаётся только в AS-IS и временном миграционном процессе; синхронизация с ним не разрабатывается. Нет необходимости знать production API NetUP, чтобы начать Phase 1.

**Утверждённое дополнение Phase 1.4:** Stage 1 вводит RBAC и схему для `SUBSCRIBER_LINKED`, `MANUAL`, `INTERNAL`. Оператор и инженер могут создавать linked заявки через прежний API; HTTP создание manual/internal и Web UI последуют позже. `admin` не получает доступа к заявкам. Описания обязательного snapshot в документах прежних этапов относятся к контракту до `0008`.
