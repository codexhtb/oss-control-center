# Disaster Recovery

**Статус: UNVERIFIED.** Git и Docker images сами по себе не восстанавливают сервис. Git clone НЕ восстанавливает PostgreSQL volume, секреты, кастомные LibreNMS collectors, systemd units и настройки инфраструктуры.

Для восстановления нужны отдельно доступные и проверенные: PostgreSQL backup; секреты/ключи и сертификаты по процедуре владельца; совместимые image artifacts/source SHA; Compose overrides и runtime config; TLS/DNS/firewall; NetUP replica access; LibreNMS/BDCOM collector infrastructure; Telegram bot ownership/config. Полный состав и RTO/RPO в репозитории не утверждены.

Подтверждение DR: провести restore PostgreSQL в изолированную среду, запустить совместимый application build на копии, проверить login/API, ticket/history/audit, internal-task/chat/field-work states, интеграционные health, затем получить sign-off владельца. Не проводить упражнение на рабочей БД.
