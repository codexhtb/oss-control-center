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
- [Phase 1.4 Stage 3: Web UI — этапы и границы](phase-1.4-web-ui.md)
- [Acceptance criteria и тестирование](acceptance-and-testing.md)
- [OSS Diagnostics — read-only мониторинг](oss-diagnostics.md)
- [Текущий Identity, Permissions & Ticket Editing](phase3-identity-ticket-editing.md)
- [Удаление и корзина заявок](ticket-deletion.md)
- [Phase 5.1: предупреждение о повторных обращениях](phase-5.1-duplicate-tickets.md)
- [Категории обращений и телефон звонящего](ticket-categories-caller-phone.md)
- [Решения ревью и остающиеся вопросы](review.md)

**Source of truth:** PostgreSQL хранит заявки, комментарии, решения, историю, аудит и диагностические snapshots. ServiceDesk Plus остаётся только в AS-IS и временном миграционном процессе; синхронизация с ним не разрабатывается. Нет необходимости знать production API NetUP, чтобы начать Phase 1.

**Историческое дополнение Phase 1.4:** Stage 1 ввёл RBAC и схему для `SUBSCRIBER_LINKED`, `MANUAL`, `INTERNAL`; Stage 2 добавил HTTP создание всех трёх типов в едином Ticket Engine. Ограничение `admin` только управлением учётными записями отменено текущим этапом: admin получает полный набор персональных разрешений. Описания обязательного snapshot в документах прежних этапов относятся к контракту до `0008`.
