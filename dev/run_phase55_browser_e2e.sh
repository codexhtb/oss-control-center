#!/bin/sh
# Browser mutations are confined to a disposable PostgreSQL database.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
browser_db="oss_test_phase55_browser_$(date -u +%Y%m%d%H%M%S)"
browser_override="$project_dir/.dev-secrets/phase55-browser.compose.yaml"
browser_script=${OSS_BROWSER_SCRIPT:-$project_dir/dev/browser_phase55.cjs}
test -r "$browser_script"
created=0
switched=0

compose() {
  docker compose --env-file .env.dev -f compose.dev.yaml -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml "$@"
}

cleanup() {
  status=$?
  trap - EXIT
  if [ "$switched" -eq 1 ]; then
    if ! compose up -d --no-deps --wait --force-recreate api; then
      echo 'Dev API could not be restored; disposable database retained for recovery' >&2
      exit 1
    fi
  fi
  if [ "$created" -eq 1 ]; then
    docker exec oss-control-dev-postgres-1 sh -c 'dropdb -U "$POSTGRES_USER" --if-exists --force "$1"' sh "$browser_db"
  fi
  if [ -f "$browser_override" ]; then unlink "$browser_override"; fi
  exit "$status"
}

test ! -e "$browser_override"
docker exec oss-control-dev-postgres-1 sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$browser_db"
created=1
trap cleanup EXIT

docker run --rm --network oss-control-dev_backend --env-file .env.dev \
  -e POSTGRES_HOST=postgres -e "POSTGRES_DB=$browser_db" \
  oss-control-dev-api:local alembic upgrade head

docker run --rm --user 1001:1001 --network oss-control-dev_backend --env-file .env.dev \
  -e POSTGRES_HOST=postgres -e "POSTGRES_DB=$browser_db" \
  -v "$project_dir/dev/seed_phase55_e2e.py:/app/dev/seed_phase55_e2e.py:ro" \
  -v "$project_dir/.dev-secrets/users.json:/run/dev-users.json:ro" \
  oss-control-dev-api:local python dev/seed_phase55_e2e.py

printf 'services:\n  api:\n    environment:\n      POSTGRES_DB: %s\n      BILLING_PROVIDER: MOCK\n' "$browser_db" > "$browser_override"
chmod 600 "$browser_override"
compose -f "$browser_override" config --quiet
switched=1
compose -f "$browser_override" up -d --no-deps --wait --force-recreate api

OSS_BROWSER_SCRIPT="$browser_script" OSS_BILLING_MODE=MOCK OSS_DISPOSABLE_DB="$browser_db" \
  sh "$project_dir/dev/run_browser_e2e.sh"
