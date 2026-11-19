# Security boundaries

- PostgreSQL OCC, MariaDB NetUP, LibreNMS API и BDCOM observations — отдельные trust/data boundaries.
- Полная OCC UI/API доступна на обычном TLS site; Caddy Mini App site содержит только `/field-work`, assets и разрешённый `/api/v1/field-work/miniapp/*`, остальные paths возвращают 403. Контракт виден в `frontend/Caddyfile.dev`; проверьте deploy artifact соответствует этому файлу.
- Источник IP allowlist — Proxmox Firewall. Не копируйте/дублируйте список в Caddy или приложение. Точные правила — инфраструктурная конфигурация вне Git и **UNVERIFIED** здесь.
- Telegram `initData` проверяет backend; направления, installer/staff activity и permission проверяются сервером.
- Cookie/Origin/CSRF, trusted proxy и rate limiting требуют сохранения тестов `tests/test_security_hardening.py` и identity tests.
- Secrets хранятся вне tracked source. Runtime file permissions/owner проверяются локальным администратором; значения не публикуются.
- Защита INTERNAL и экспортная фильтрация должны проходить через общие backend predicates, включая поиск, counts и детализацию.

Не публикуйте внутренние IP, адреса оборудования, credentials, Telegram IDs/chat IDs, mapping registry, PII, базы/backup, env files или дампы. Наличие private GitHub repository не делает такие данные безопасными для коммита.
