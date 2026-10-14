#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
install -d -m 700 "$project_dir/.dev-secrets/screenshots"
if [ -f "$project_dir/.dev-secrets/netup-runtime.env" ]; then
  billing_mode=${OSS_BILLING_MODE:-NETUP}
else
  billing_mode=${OSS_BILLING_MODE:-MOCK}
fi
if [ "$billing_mode" = NETUP ]; then
  smoke_env_file=${OSS_SMOKE_ENV_FILE:-$project_dir/.dev-secrets/netup-smoke.env}
  test -r "$smoke_env_file"
  set -- --env-file "$smoke_env_file"
else
  set --
fi

docker build --quiet -f "$project_dir/dev/Dockerfile.browser" \
  -t oss-control-dev-browser:local "$project_dir/dev" >/dev/null

docker run --rm --user 1001:1001 --shm-size=1g --cap-drop=ALL \
  --security-opt=no-new-privileges --tmpfs /tmp:rw,exec,uid=1001,gid=1001,size=1g \
  --env HOME=/tmp/oss-browser-home \
  --env OSS_SCREENSHOTS \
  --env OSS_BILLING_MODE="$billing_mode" \
  --env OSS_STAGE3C_LINKED \
  --env OSS_READ_ONLY \
  --env OSS_MONITORING_ONLY \
  --env OSS_BDCOM_EXPECT_UNAVAILABLE \
  "$@" \
  --mount "type=bind,src=$project_dir/.dev-secrets/certs/ca.crt,dst=/run/dev-ca.crt,readonly" \
  --mount "type=bind,src=$project_dir/.dev-secrets/users.json,dst=/run/dev-users.json,readonly" \
  --mount "type=bind,src=$project_dir/.dev-secrets/credentials/bezhan-password,dst=/run/bezhan-password,readonly" \
  --mount "type=bind,src=$project_dir/.dev-secrets/screenshots,dst=/run/screenshots" \
  --mount "type=bind,src=$project_dir/dev/browser_e2e.cjs,dst=/run/browser_e2e.cjs,readonly" \
  oss-control-dev-browser:local bash -euc '
    mkdir -p "$HOME/.pki/nssdb"
    certutil -N -d "sql:$HOME/.pki/nssdb" --empty-password
    certutil -A -d "sql:$HOME/.pki/nssdb" -n oss-control-dev -t "C,," -i /run/dev-ca.crt
    npm install --prefix /tmp/oss-browser-test --no-audit --no-fund playwright@1.64.0 >/dev/null
    NODE_EXTRA_CA_CERTS=/run/dev-ca.crt NODE_PATH=/tmp/oss-browser-test/node_modules \
      node /run/browser_e2e.cjs
  '
