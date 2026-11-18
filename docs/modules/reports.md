# Отчётность и экспорт

**Назначение.** SQL-агрегированные показатели Service Desk и отдельных модулей, выгрузка PDF/XLSX для выбранного периода.

**Код/API:** `app/modules/reports/`, route `app/api/v1/reports.py`; `/general`, `/employees`, `/callbacks`, `/field-work`, `/reasons`, `/export.xlsx`, `/export.pdf`.

**Источник:** PostgreSQL tickets/events/decisions/callbacks/field handoffs/reschedules/users. SQL агрегирует на сервере; детали имеют pagination в экранных API. Экспорт формируется отдельными safe queries и не ограничивается страницей UI.

**Период:** календарные границы UTC+3. Метрики событий (created/decision/callback/handoff/reschedule) отделены от текущих состояний и уникальных заявок. Категория ticket и причина decision не смешиваются.

**Доступ:** `reports.read` проверяется backend; visibility predicate исключает INTERNAL для тех, кому он недоступен, в агрегатах и выгрузках.

**Ошибки и ограничения:** не выводите отсутствующие timestamps как ноль/факт. Тесты исходников — подтверждение расчётов, не подтверждение бизнес-классификации вне схемы. Экспорт защищён от Excel formula injection; персональные данные в PDF минимизированы.
