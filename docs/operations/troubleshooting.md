# Troubleshooting

## Общий порядок

1. Не меняйте состояние заявки/поручения вручную в БД.
2. Сначала снимите read-only health, текущую Alembic revision, project labels и последние ограниченные по времени логи нужного сервиса.
3. Сверьте точный `request_id`/handoff ID с API response; не включайте имена абонентов, адреса, телефоны, токены или полные Telegram payloads в отчёт.
4. Разделите ошибку: OSS API/PostgreSQL, внешняя интеграция, TLS/network, RBAC или frontend/cache.
5. Соберите request timestamp UTC и безопасный error code; передайте владельцу. Не повторяйте операции с состоянием `UNCERTAIN` до reconciliation.

## Типовые случаи

- `401`: сессия/token истёк или Origin/cookie policy отклонены; перепройдите авторизацию, не меняя CSRF guard.
- `403`: проверьте конкретный persisted permission и объектную visibility; не расширяйте роль для обхода.
- `503` billing/monitoring: источник может быть недоступен; сохраните явный source state, не заменяйте на ноль/UP.
- Telegram `UNCERTAIN`: доставка могла состояться; сначала найдите подтверждённое сообщение/attempt через штатную процедуру, без автоматического повторного отправления.
- Миграция/health расходятся: остановите deployment до выяснения целевого Compose проекта и revision.

Безопасные read-only команды перечислены в [health checks](health-checks.md) и [Telegram operations](telegram-operations.md).
