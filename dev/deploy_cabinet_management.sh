#!/bin/sh
# Promotion of the exact DEV-tested images with owner-approved management transport.
set -eu
umask 077
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
expected=${1:?Pass the tested and pushed commit}
test "$(git rev-parse HEAD)" = "$expected"
test "$(git rev-parse origin/main)" = "$expected"
git diff --quiet
git diff --cached --quiet
export SUBSCRIBER_CABINET_MODE=disabled
export SUBSCRIBER_CABINET_MEMO_VERIFIED=false
base() {
  docker compose --env-file .env.dev -p oss-control-dev \
    -f compose.dev.yaml -f compose.telegram.dev.yaml -f compose.telegram-t2.dev.yaml \
    -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml "$@"
}
release() {
  base -f compose.subscriber-cabinet-management.yaml "$@"
}
unchanged() {
  docker inspect oss-control-dev-postgres-1 oss-control-dev-bdcom_tunnel_proxy-1 \
    oss-control-center-api-1 oss-control-center-postgres-1 --format '{{.Id}}'
}
helper() {
  docker run --rm -i --network oss-control-dev_backend --env-file .env.dev \
    -e POSTGRES_HOST=postgres -e POSTGRES_DB="$1" \
    -v "$PWD/dev/cabinet_permissions_release.py:/app/dev/cabinet_permissions_release.py:ro" \
    oss-cabinet-management-api:tested python -m dev.cabinet_permissions_release "$2"
}
restored_grants() {
  docker exec -i oss-control-dev-postgres-1 sh -c \
    'psql -U "$POSTGRES_USER" -d oss_test_cabinet_management_restore -Atq' <<'SQL'
SELECT md5(coalesce(string_agg(user_id::text || ':' || permission, ',' ORDER BY user_id,permission),''))
FROM app_user_permissions;
SELECT md5(coalesce(string_agg(id::text || ':' || permissions_customized::text, ',' ORDER BY id),''))
FROM app_users;
SQL
}
before=$(unchanged)
offset_before=$(docker exec oss-control-dev-api-1 python -c \
  'from sqlalchemy.orm import Session; from app.db.engine import get_engine; from app.modules.field_dispatch.poller import _offset; db=Session(get_engine()); print(_offset(db)); db.close()')
live_db=$(docker exec oss-control-dev-postgres-1 sh -c 'printf %s "$POSTGRES_DB"')
test -n "$live_db"
test "$(docker inspect oss-cabinet-dev-cabinet_api-1 --format '{{.Image}}')" = \
     "$(docker image inspect oss-cabinet-management-api:tested --format '{{.Id}}')"
test "$(docker inspect oss-cabinet-dev-cabinet_web-1 --format '{{.Image}}')" = \
     "$(docker image inspect oss-cabinet-management-web:tested --format '{{.Id}}')"
docker image tag "$(docker inspect oss-control-dev-api-1 --format '{{.Image}}')" \
  oss-control-dev-api:pre-cabinet-management-20261125
docker image tag "$(docker inspect oss-control-dev-web-1 --format '{{.Image}}')" \
  oss-control-dev-web:pre-cabinet-management-20261125
docker image tag "$(docker inspect oss-control-dev-telegram_poller-1 --format '{{.Image}}')" \
  oss-cabinet-worker:pre-management-20261125
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dump=".dev-secrets/backups/cabinet-management-$stamp.dump"
grants=".dev-secrets/backups/cabinet-management-grants-$stamp.json"
rehearsal=".dev-secrets/backups/cabinet-management-rehearsal-$stamp.json"
docker exec oss-control-dev-postgres-1 sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$dump"
test -s "$dump"
restored=0
promoting=0
cleanup() {
  result=$?
  trap - EXIT
  if [ "$result" -ne 0 ] && [ "$promoting" -eq 1 ]; then
    echo 'Release verification failed; restoring API/WEB and operator grants' >&2
    release --profile telegram-t2 stop telegram_poller || exit 1
    if [ -s "$grants" ]; then helper "$live_db" rollback < "$grants" || exit 1; fi
    docker image tag oss-control-dev-api:pre-cabinet-management-20261125 oss-control-dev-api:local
    docker image tag oss-control-dev-web:pre-cabinet-management-20261125 oss-control-dev-web:local
    base up -d --no-deps --wait api web || exit 1
    # The previous cabinet API already understands the new Permission enum.
    # Keep the worker compatible with any concurrent authorized grant changes.
    base --profile telegram-t2 up -d --no-deps --wait telegram_poller || exit 1
    curl --fail --silent --show-error --resolve sd.oss-ab.net:443:127.0.0.1 \
      https://sd.oss-ab.net/api/v1/health/ready || exit 1
  fi
  if [ "$restored" -eq 1 ]; then
    docker exec oss-control-dev-postgres-1 sh -c \
      'dropdb -U "$POSTGRES_USER" --force oss_test_cabinet_management_restore' || exit 1
  fi
  exit "$result"
}
trap cleanup EXIT
docker exec oss-control-dev-postgres-1 sh -c \
  'createdb -U "$POSTGRES_USER" oss_test_cabinet_management_restore'
restored=1
docker exec -i oss-control-dev-postgres-1 sh -c \
  'pg_restore -U "$POSTGRES_USER" --no-owner -d oss_test_cabinet_management_restore' < "$dump"
original_grants=$(restored_grants)
helper oss_test_cabinet_management_restore apply > "$rehearsal"
helper oss_test_cabinet_management_restore check
helper oss_test_cabinet_management_restore rollback < "$rehearsal"
test "$original_grants" = "$(restored_grants)"
echo 'PASS: PostgreSQL backup restored and grant rollback rehearsed'
promoting=1
docker image tag oss-cabinet-management-api:tested oss-control-dev-api:local
docker image tag oss-cabinet-management-web:tested oss-control-dev-web:local
# Update the enum-compatible worker before any new permission can be saved.
release --profile telegram-t2 up -d --no-deps --wait telegram_poller
release up -d --no-deps --wait api web
helper "$live_db" apply > "$grants"
helper "$live_db" check
docker exec oss-control-dev-api-1 python -c \
  'from app.core.config import get_settings; from app.modules.billing.netup.config import NetupConfig; s=get_settings(); c=NetupConfig.from_env(app_env=s.app_env); assert s.subscriber_cabinet_mode=="management" and c.allow_plaintext_management and c.host=="172.17.7.1" and c.port==3307; print("PASS: approved management endpoint and cabinet mode active")'
curl --fail --silent --show-error --resolve sd.oss-ab.net:443:127.0.0.1 \
  https://sd.oss-ab.net/api/v1/health/ready
for path in /tickets /api/v1/subscriber-lookups/search /api/v1/users; do
  code=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --resolve sd.oss-ab.net:8443:127.0.0.1 "https://sd.oss-ab.net:8443$path")
  test "$code" = 403
done
.venv/bin/python dev/run_cabinet_operator_live.py
test "$before" = "$(unchanged)"
docker exec oss-control-dev-telegram_poller-1 python -c \
  'import sys; from sqlalchemy import select; from sqlalchemy.orm import Session; from app.db.engine import get_engine; from app.modules.identity.models import User; from app.modules.identity.access import effective_permissions; from app.modules.field_dispatch.poller import _offset; db=Session(get_engine()); [effective_permissions(db,u) for u in db.scalars(select(User))]; assert _offset(db)>=int(sys.argv[1]); db.close(); print("PASS: Telegram worker parses saved grants and retains persisted offset")' "$offset_before"
test "$(docker inspect oss-control-dev-telegram_poller-1 --format '{{.State.Running}} {{.RestartCount}}')" = 'true 0'
echo "PASS: production operator acceptance; rollback backup: $dump"
