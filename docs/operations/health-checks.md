# Health checks

Все HTTP проверки ниже read-only и не требуют логина; не добавляйте query, token или account number.

```bash
curl --fail --silent --show-error https://sd.oss-ab.net/api/v1/health/live
curl --fail --silent --show-error https://sd.oss-ab.net/api/v1/health/ready
```

Ожидаемые semantics определяются `app/api/v1/health.py`: `live` проверяет процесс, `ready` — готовность зависимости PostgreSQL с ограниченным timeout. HTTP 200 не доказывает доступность NetUP, LibreNMS, Telegram или BDCOM.

До Docker-команд убедитесь в правильном host/project и получите список Compose files из label существующего контейнера (не из случайного shell cwd). Для уже подтверждённого deployment команда `docker compose ... ps` — read-only. Не печатайте `docker inspect .Config.Env`, полный `compose config`, `.env` или защищённые volume paths: там могут быть секреты.

Schema revision допустимо прочитать командой `alembic current` в API-контейнере, если цель контейнера подтверждена. Не запускайте `upgrade`, `downgrade`, seed или application write smoke-test в рамках health check.

WEB/Mini App boundary проверяйте только на разрешённом окружении: обычный OCC URL, `/field-work`, и попытку доступа к одному административному route на Mini App port должны дать ожидаемые ответы согласно Caddy config. Не указывайте IP в документации или issue.
