# Runbook: отказ и восстановление NetUP lookup

1. Проверить `/api/v1/health/ready`: это PostgreSQL readiness, он не
   подтверждает доступность NetUP. `503 billing_unavailable` при lookup
   означает отдельный отказ источника; не создавать успешную пустую карточку.
2. По безопасным метрикам проверить число `billing_unavailable`, таймауты,
   TLS-handshake и доступность согласованного endpoint. Не включать в
   incident ticket ЛС, SQL-параметры, контакт или raw DBAPI traceback.
3. Проверить срок действия секрета/CA и права выделенного read-only user
   через согласованный канал. Не использовать root MariaDB, прямой обход
   сегментации или исследовательский wrapper в runtime API.
4. После восстановления выполнить synthetic/unit tests, проверку grants,
   `EXPLAIN` для lookup и одну авторизованную lab проверку без вывода PII.
   Убедиться, что отказ не создал snapshot и повторный lookup получает
   `source=NETUP` со свежим временем.
5. Если структура UTM5 изменилась, не подменять отсутствие полей значениями
   из баланса, `users.remote_switch_id` или DHCP leases. Отключить NetUP
   режим и провести новое Discovery/ревью mapping. PostgreSQL ticket history
   и snapshots сохранять; откат миграции не является обычным восстановлением.

## Очистка lookup snapshots без заявки

После применения `0006_lookup_cleanup` оператор БД вызывает
`SELECT purge_expired_lookup_snapshots(500);` отдельным регламентным заданием.
Процедура удаляет партиями только записи старше 24 часов от `fetched_at`,
не связанные с tickets, вместе с дочерними connection snapshots. `expires_at`
не используется для очистки. Вызов возвращает число удалённых родителей;
запись в `lookup_cleanup_audit` содержит только время, cutoff и количество.
Повторять вызов до возвращения 0; ошибки не подавлять. Не запускать процедуру
автоматически из API и не применять её к рабочей `oss_control` до отдельного
deployment review. Функция не доступна через `PUBLIC`; в production роль API
должна быть владельцем **ни одной** из таблиц snapshots и не иметь прямого
`DELETE`. DBA отдельно предоставляет `EXECUTE` только роли maintenance.
Разделение ролей обязательно: владелец таблиц PostgreSQL может обойти
триггеры, а session-local признак процедуры сам по себе не является правом.
Новые credentials на этапе 1.1 не создаются.
