# Отчётность и экспорт

**Назначение.** SQL-агрегированные показатели Service Desk и отдельных модулей, выгрузка PDF/XLSX для выбранного периода.

**Код/API:** `app/modules/reports/`, route `app/api/v1/reports.py`; `/general`, `/employees`, `/callbacks`, `/field-work`, `/reasons`, `/export.xlsx`, `/export.pdf`.

**Источник:** PostgreSQL tickets/events/decisions/callbacks/field handoffs/reschedules/users. SQL агрегирует на сервере; детали имеют pagination в экранных API. Экспорт формируется отдельными safe queries и не ограничивается страницей UI.

**Период:** календарные границы UTC+3. Метрики событий (created/decision/callback/handoff/reschedule) отделены от текущих состояний и уникальных заявок. Категория ticket и причина decision не смешиваются.

**Доступ:** `reports.read` проверяется backend; visibility predicate исключает INTERNAL для тех, кому он недоступен, в агрегатах и выгрузках.

**Ошибки и ограничения:** не выводите отсутствующие timestamps как ноль/факт. Тесты исходников — подтверждение расчётов, не подтверждение бизнес-классификации вне схемы. Экспорт защищён от Excel formula injection; персональные данные в PDF минимизированы.

## Метрики и детализация

General считает created по `created_at`, решения/закрытия по фактическим событиям и active как текущий snapshot. Employees группируются по устойчивому user ID и фактическим actor/engineer IDs. Callbacks разделяют три результата. Field Work различает handoffs, reports, verifications и reschedule events. Categories и decision reasons не объединяются.

PDF — краткий управленческий отчёт; XLSX содержит сводку и одну строку на ticket в основной детализации. Повторные decisions/callbacks учитываются как события, но не размножают ticket row. Текстовые Excel cells нейтрализуют formula prefixes. Пользователь без INTERNAL visibility не получает INTERNAL ни в aggregate, ни export.

| Endpoint | Смысл |
| --- | --- |
| `/general` | поступление, решение, закрытие, active и distributions |
| `/employees` | действия фактических авторов по устойчивому ID |
| `/callbacks` | результаты контрольных звонков |
| `/field-work` | handoff/report/verify/reschedule/current backlog |
| `/reasons` | категории регистрации отдельно от decision reasons |
| `/export.xlsx`, `/export.pdf` | полный разрешённый период без UI pagination |

Период задаётся календарными границами UTC+3 и преобразуется в UTC для SQL. Пустая метрика обозначается как отсутствие данных, если timestamp/смысл не подтверждён.
