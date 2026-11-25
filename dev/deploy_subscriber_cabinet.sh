#!/bin/sh
# Promote already-tested images only. Password issuance deliberately stays off.
set -eu
umask 077
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
expected_commit=${1:?Pass the tested, pushed Git commit}
test "$(git rev-parse HEAD)" = "$expected_commit"
test "$(git rev-parse origin/main)" = "$expected_commit"
git diff --quiet
git diff --cached --quiet
export SUBSCRIBER_CABINET_MODE=disabled
export SUBSCRIBER_CABINET_MEMO_VERIFIED=false
compose() {
  docker compose --env-file .env.dev -p oss-control-dev \
    -f compose.dev.yaml -f compose.telegram.dev.yaml -f compose.telegram-t2.dev.yaml \
    -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml "$@"
}
unchanged() {
  docker inspect oss-control-dev-postgres-1 oss-control-dev-telegram_poller-1 \
    oss-control-dev-bdcom_tunnel_proxy-1 oss-control-center-api-1 \
    oss-control-center-postgres-1 --format '{{.Id}}'
}
before_ids=$(unchanged)
docker image inspect oss-control-dev-api:pre-cabinet-20261125 >/dev/null
docker image inspect oss-control-dev-web:pre-cabinet-20261125 >/dev/null
test "$(docker inspect oss-cabinet-dev-cabinet_api-1 --format '{{.Image}}')" = \
     "$(docker image inspect oss-subscriber-cabinet-api:tested --format '{{.Id}}')"
test "$(docker inspect oss-cabinet-dev-cabinet_web-1 --format '{{.Image}}')" = \
     "$(docker image inspect oss-subscriber-cabinet-web:tested --format '{{.Id}}')"
backup=".dev-secrets/backups/subscriber-cabinet-predeploy-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker exec oss-control-dev-postgres-1 sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup"
test -s "$backup"
docker exec oss-control-dev-postgres-1 sh -c \
  'createdb -U "$POSTGRES_USER" oss_test_cabinet_restore'
restored=1
promoting=0
cleanup() {
  status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$promoting" -eq 1 ]; then
    echo 'Release check failed; returning OCC permission schema and saved API/WEB images' >&2
    docker run --rm --network oss-control-dev_backend --env-file .env.dev \
      -e POSTGRES_HOST=postgres oss-subscriber-cabinet-api:tested \
      alembic downgrade 0053_task_replies_reminders || exit 1
    docker image tag oss-control-dev-api:pre-cabinet-20261125 oss-control-dev-api:local
    docker image tag oss-control-dev-web:pre-cabinet-20261125 oss-control-dev-web:local
    compose up -d --no-deps --wait api web || exit 1
    curl --fail --silent --show-error --resolve sd.oss-ab.net:443:127.0.0.1 \
      https://sd.oss-ab.net/api/v1/health/ready || exit 1
  fi
  if [ "$restored" -eq 1 ]; then
    docker exec oss-control-dev-postgres-1 sh -c \
      'dropdb -U "$POSTGRES_USER" --force oss_test_cabinet_restore' || exit 1
  fi
  exit "$status"
}
trap cleanup EXIT
docker exec -i oss-control-dev-postgres-1 sh -c \
  'pg_restore -U "$POSTGRES_USER" --no-owner -d oss_test_cabinet_restore' < "$backup"
docker run --rm -i --network oss-control-dev_backend --env-file .env.dev \
  -e POSTGRES_HOST=postgres -e POSTGRES_DB=oss_test_cabinet_restore \
  oss-subscriber-cabinet-api:tested python - <<'PY'
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from app.core.config import Settings
engine = create_engine(Settings.from_env().sqlalchemy_url)
with engine.connect() as c:
    assert c.scalar(text('SELECT version_num FROM alembic_version')) == '0053_task_replies_reminders'
    grants = c.scalar(text('SELECT count(*) FROM app_user_permissions'))
    tickets = c.scalar(text('SELECT count(*) FROM tickets'))
cfg = Config('alembic.ini')
command.upgrade(cfg, 'head')
command.downgrade(cfg, '0053_task_replies_reminders')
with engine.connect() as c:
    assert c.scalar(text('SELECT count(*) FROM app_user_permissions')) == grants
    assert c.scalar(text('SELECT count(*) FROM tickets')) == tickets
engine.dispose()
print('PASS: protected backup restored; OCC upgrade/rollback preserves grants and tickets')
PY
promoting=1
docker run --rm --network oss-control-dev_backend --env-file .env.dev \
  -e POSTGRES_HOST=postgres oss-subscriber-cabinet-api:tested alembic upgrade head
docker image tag oss-subscriber-cabinet-api:tested oss-control-dev-api:local
docker image tag oss-subscriber-cabinet-web:tested oss-control-dev-web:local
compose up -d --no-deps --wait api web
docker exec oss-control-dev-api-1 python -c \
  'from app.core.config import get_settings; s=get_settings(); assert s.subscriber_cabinet_mode=="disabled" and not s.subscriber_cabinet_memo_verified; print("PASS: live password issuance disabled")'
curl --fail --silent --show-error --resolve sd.oss-ab.net:443:127.0.0.1 \
  https://sd.oss-ab.net/api/v1/health/ready
for route in / /api/v1/health/live; do
  curl --fail --silent --show-error --output /dev/null \
    --resolve sd.oss-ab.net:443:127.0.0.1 "https://sd.oss-ab.net$route"
done
for route in /tickets /api/v1/subscriber-lookups/search /api/v1/users; do
  code=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --resolve sd.oss-ab.net:8443:127.0.0.1 "https://sd.oss-ab.net:8443$route")
  test "$code" = 403
done
web_ip=$(docker inspect oss-control-dev-web-1 --format '{{with index .NetworkSettings.Networks "oss-control-dev_backend"}}{{.IPAddress}}{{end}}')
test -n "$web_ip"
docker run --rm --user 1001:1001 --network oss-control-dev_backend \
  --add-host "sd.oss-ab.net:$web_ip" \
  --env-file .env.dev -e POSTGRES_HOST=postgres -e OSS_LIVE_SMOKE=1 \
  -e SUBSCRIBER_CABINET_MODE=disabled -e SUBSCRIBER_CABINET_MEMO_VERIFIED=false \
  -v "$PWD/dev/smoke_subscriber_cabinet_live.py:/app/dev/smoke_subscriber_cabinet_live.py:ro" \
  -v "$PWD/.dev-secrets/credentials/notification-center-smoke.json:/run/staff-smoke.json:ro" \
  oss-subscriber-cabinet-api:tested python -m dev.smoke_subscriber_cabinet_live
test "$before_ids" = "$(unchanged)"
echo 'PASS: PROD health, HTTPS boundaries, staff RBAC; other services unchanged'
echo "Protected PostgreSQL backup: $backup"
