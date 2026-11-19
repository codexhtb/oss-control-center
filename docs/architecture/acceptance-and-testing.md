# Приёмка и тестирование

Текущая минимальная матрица. Архивная phase acceptance сохранена [здесь](../archive/architecture/acceptance-and-testing.md).

## Уровни

1. Ruff, TypeScript typecheck, production build, `git diff --check`.
2. Целевые service/API/component tests и негативные RBAC/validation cases.
3. Регрессия workflow, snapshots, audit/history и concurrency.
4. Migration upgrade на изолированной восстановленной PostgreSQL с counts/FK/CHECK.
5. После deployment: health/readiness, route, logs и безопасный smoke. Рабочие Telegram/NetUP/оборудование не являются тестовым стендом.

```bash
python -m ruff check app tests
python -m pytest tests/<target>.py -q
cd frontend && npm test -- --run
cd frontend && npm run typecheck
cd frontend && npm run build
git diff --check
```

Полный backend прогон требует изолированную test DB. Не направляйте pytest на рабочий DSN.

| Область | Положительный сценарий | Обязательный отрицательный |
| --- | --- | --- |
| Identity | login/logout, persisted grants | disabled user, forged forwarded IP |
| Tickets | пять типов, list/detail/edit | invalid fields, stale `If-Match`, INTERNAL leakage |
| Workflow | start → resolve → callback | чужое решение, неверный transition |
| Snapshots | lookup создаёт связанную заявку | ambiguity, expired/foreign lookup |
| Diagnostics | независимые observations | unavailable/missing mapping остаётся UNKNOWN |
| Tasks | author → assignee → complete | third-party access |
| Chat | reply/read/SSE recovery | unauthorized delete/reply target |
| Field Work | publish/send/report/verify | duplicate, wrong chat, blind UNCERTAIN retry |
| Mini App | valid initData/direction | forged/expired auth, inactive installer |
| Reports | UTC+3, unique vs events | deleted/INTERNAL leakage, formula injection |

## Ключевые критерии

- Один `client_request_id` не удваивает заявку.
- Subscriber type хранит подтверждённый snapshot; manual types его не получают.
- Отказ чужому инженеру не создаёт decision/event/audit.
- Три callback результата выполняют документированные переходы.
- Актуальное решение читается по `resolved_decision_id`.
- Soft-deleted ticket отсутствует в lists/reports.
- Оператор не обнаруживает INTERNAL через totals или связанный endpoint.

NetUP проверяется fake/mock reader или изолированным read-only endpoint: parameterization, timeout, bounded result, отсутствие write SQL. LibreNMS/BDCOM — fixtures/mock; device mapping не подтверждает port/ONU. Telegram — mock Bot API с 429, lost response, concurrency, stale callback и wrong chat. Реальные группы не используются.

Перед merge: проверить scope diff, secrets/PII, линейный migration head, документацию, backup/restore plan и точный Compose project. Отсутствующую browser/device проверку указывать как ограничение, а не успех.
