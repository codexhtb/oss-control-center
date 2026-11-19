# Пользователи, RBAC и аудит

**Назначение.** Сессии сотрудников, пароли, пользователи, персональные grants и аудит административных/доменный действий.

**Код/API:** `app/modules/identity/`, `app/api/v1/identity.py`; `/api/v1/auth/*`, `/api/v1/users`, user permissions/password/deactivate/reactivate endpoints. UI `/users`, `/account`.

**Права.** `Permission` определены в `app/modules/identity/roles.py`; стандартный набор зависит от role, но при `permissions_customized` effective grants читаются из `user_permissions` PostgreSQL. `users.manage` управляет пользователями/монтажниками; `settings.manage`, `reports.read`, `tasks.*`, ticket/diagnostic permissions раздельны. Не выводите модель к «admin всегда может всё»: проверяйте сохранённый grant и конкретное endpoint правило.

**Хранение:** users, user_permissions, sessions/security data and identity audit — PostgreSQL. Пароли обрабатываются Argon2; cookie/Origin/CSRF/proxy rules находятся в `app/core/security.py` и identity API.

**Ошибки/ограничения:** неизвестная/отозванная сессия не должна сохранять cached permissions. Прямой backend вызов обязан повторно проверить активность и право. Не записывайте логины/PII и credential payloads в общий лог.

## Effective permissions

Role (`operator`, `engineer`, `admin`) задаёт preset для новых/мигрированных accounts. Runtime использует persisted grants; `permissions_customized` означает, что роль не должна молча вернуть снятое право. `settings.manage` намеренно opt-in даже для legacy admin. Деактивация немедленно лишает доступа.

Управление пользователем, grants, password reset и installer lifecycle требует серверной проверки. Удаление installer запрещено при исторических FK; предлагается deactivate. Audit хранит actor/action/target без credential payload. Reverse-proxy client IP доверяется только от явно настроенного proxy.

| Данные | Правило сопровождения |
| --- | --- |
| `app_users` | active/role/profile; деактивация безопаснее удаления |
| `user_permissions` | персональный effective grant |
| session/security state | отзывается при logout/deactivate согласно service |
| identity audit | actor/action/target, без пароля и токена |

Ключевые проверки: два client IP имеют независимые rate buckets; forged forwarding header не принимается; custom grants переживают login; operator не получает INTERNAL; password reset не выводит пароль в лог.
