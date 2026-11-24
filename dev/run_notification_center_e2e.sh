#!/bin/sh
# Disposable DEV acceptance. Never redirect the live API to a test database.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
created=0
compose() {
  docker compose --env-file .env.dev -f dev/compose.notification-center.yaml "$@"
}
cleanup() {
  status=$?
  trap - EXIT
  if [ "$created" -eq 1 ]; then
    if ! compose down; then
      echo 'Acceptance stack retained; database was not dropped' >&2
      exit 1
    fi
    docker exec oss-control-dev-postgres-1 sh -c 'dropdb -U "$POSTGRES_USER" --force oss_test_notification_center'
  fi
  exit "$status"
}
# Fail rather than replacing an existing acceptance database.
docker exec oss-control-dev-postgres-1 sh -c 'createdb -U "$POSTGRES_USER" oss_test_notification_center'
created=1
trap cleanup EXIT
compose run --rm --no-deps acceptance_api alembic upgrade head
compose run --rm --no-deps --user 1001:1001 \
  -v "$project_dir/dev/seed_notification_center_e2e.py:/app/dev/seed_notification_center_e2e.py:ro" \
  -v "$project_dir/.dev-secrets/users.json:/run/dev-users.json:ro" \
  acceptance_api python dev/seed_notification_center_e2e.py
compose up -d --wait
install -d -m 0700 "$project_dir/.dev-secrets/screenshots"
browser_runtime=/tmp/oss-notification-browser-runtime
npm install --prefix "$browser_runtime" --no-audit --no-fund --ignore-scripts playwright@1.64.0 >/dev/null
docker run --rm --user 1001:1001 --network oss-notification-acceptance_isolated \
  --shm-size=1g --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,exec,uid=1001,gid=1001,size=1g \
  -e HOME=/tmp/browser-home -e NODE_PATH=/opt/browser-runtime/node_modules \
  -e OSS_DISPOSABLE_DB=oss_test_notification_center -e OSS_SCREENSHOTS=1 \
  -v "$browser_runtime:/opt/browser-runtime:ro" \
  -v "$project_dir/dev/browser_notification_center.cjs:/run/browser.cjs:ro" \
  -v "$project_dir/dev/browser_notification_center_readonly.cjs:/run/browser-readonly.cjs:ro" \
  -v "$project_dir/.dev-secrets/users.json:/run/dev-users.json:ro" \
  -v "$project_dir/.dev-secrets/screenshots:/run/screenshots" \
  oss-control-dev-browser:local sh -c 'node /run/browser.cjs && OSS_READ_ONLY=1 node /run/browser-readonly.cjs'
