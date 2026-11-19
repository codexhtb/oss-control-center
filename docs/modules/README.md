# Каталог модулей

Краткие контракты сверены с кодом `main` и текущими API route modules. Ссылки на исходники намеренно указывают владельцу изменений на код; фактическое включение внешнего integration определяется runtime.

| Модуль | Описание |
| --- | --- |
| [Service Desk](service-desk.md) | Типы заявок, workflow, решения и аудит |
| [Subscriber 360](subscriber-360.md) | Billing lookup и карточка абонента |
| [NetUP BillingProvider](netup-billing.md) | Read-only источник биллинга |
| [LibreNMS и BDCOM](monitoring.md) | Технические observations и correlation |
| [Внутренние поручения](internal-tasks.md) | Задача и переписка по заявке |
| [Общий чат](chat.md) | Сотруднические сообщения и replies |
| [Field Dispatch и бот](field-dispatch.md) | Передача выезда, Telegram inbound/outbound |
| [Field Work Mini App](field-work-miniapp.md) | Общая очередь для монтажников и staff access |
| [Отчёты и экспорт](reports.md) | Аналитические SQL-агрегации, PDF/XLSX |
| [Пользователи, RBAC и аудит](identity-rbac-audit.md) | Учётные записи и персональные grants |
| [Уведомления](notifications.md) | Колокольчик, прочтение и Telegram outbox |

Каждая страница фиксирует границу модуля, источники/хранение, API, permissions, workflow и отказ. Полный перечень полей находится в Pydantic/SQLAlchemy, а текущие межмодульные инварианты — в [API-контрактах](../architecture/api-contracts.md) и [модели данных](../architecture/data-model.md). Документ не заменяет backend policy или migration constraint.
