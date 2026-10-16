#!/bin/sh
# Run destructive browser acceptance only against a disposable PostgreSQL database.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
browser_db="oss_test_phase3_browser_$(date -u +%Y%m%d%H%M%S)"
browser_override="$project_dir/.dev-secrets/phase3-browser.compose.yaml"

compose() {
  docker compose --env-file .env.dev -f compose.dev.yaml -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml "$@"
}

cleanup() {
  # The running dev API must return to its own persistent database first.
  compose up -d --no-deps --wait --force-recreate api
  docker exec oss-control-dev-postgres-1 sh -c 'dropdb -U "$POSTGRES_USER" "$1"' sh "$browser_db"
  if test -f "$browser_override"; then unlink "$browser_override"; fi
}

docker exec oss-control-dev-postgres-1 sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$browser_db"
trap cleanup EXIT

docker run --rm --network oss-control-dev_backend --env-file .env.dev \
  -e POSTGRES_HOST=postgres -e "POSTGRES_DB=$browser_db" \
  oss-control-dev-api:local alembic upgrade head

docker run --rm --user 1001:1001 --network oss-control-dev_backend --env-file .env.dev \
  -e POSTGRES_HOST=postgres -e "POSTGRES_DB=$browser_db" \
  -v "$project_dir/dev/seed_phase3_e2e.py:/app/dev/seed_phase3_e2e.py:ro" \
  -v "$project_dir/.dev-secrets/users.json:/run/dev-users.json:ro" \
  oss-control-dev-api:local python dev/seed_phase3_e2e.py

cat > "$browser_override" <<YAML
services:
  api:
    environment:
      POSTGRES_DB: $browser_db
YAML
if [ "${OSS_DISPOSABLE_BILLING_PROVIDER:-}" = "MOCK" ]; then
  printf '      BILLING_PROVIDER: MOCK\n' >> "$browser_override"
fi
chmod 600 "$browser_override"
compose -f "$browser_override" config --quiet
compose -f "$browser_override" up -d --no-deps --wait --force-recreate api

OSS_BROWSER_SCRIPT="${OSS_BROWSER_SCRIPT:-$project_dir/dev/browser_phase3.cjs}" OSS_BILLING_MODE=MOCK OSS_DISPOSABLE_DB="$browser_db" \
  sh "$project_dir/dev/run_browser_e2e.sh"
