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
