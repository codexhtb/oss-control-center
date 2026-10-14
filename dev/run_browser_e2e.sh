#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

docker build --quiet -f "$project_dir/dev/Dockerfile.browser" \
  -t oss-control-dev-browser:local "$project_dir/dev" >/dev/null

docker run --rm --user 1001:1001 --shm-size=1g --cap-drop=ALL \
  --security-opt=no-new-privileges --tmpfs /tmp:rw,exec,uid=1001,gid=1001,size=1g \
  --env HOME=/tmp/oss-browser-home \
  --mount "type=bind,src=$project_dir/.dev-secrets/certs/ca.crt,dst=/run/dev-ca.crt,readonly" \
  --mount "type=bind,src=$project_dir/.dev-secrets/users.json,dst=/run/dev-users.json,readonly" \
  --mount "type=bind,src=$project_dir/.dev-secrets/credentials/bezhan-password,dst=/run/bezhan-password,readonly" \
  --mount "type=bind,src=$project_dir/dev/browser_e2e.cjs,dst=/run/browser_e2e.cjs,readonly" \
  oss-control-dev-browser:local bash -euc '
    mkdir -p "$HOME/.pki/nssdb"
    certutil -N -d "sql:$HOME/.pki/nssdb" --empty-password
    certutil -A -d "sql:$HOME/.pki/nssdb" -n oss-control-dev -t "C,," -i /run/dev-ca.crt
    npm install --prefix /tmp/oss-browser-test --no-audit --no-fund playwright@1.64.0 >/dev/null
    NODE_EXTRA_CA_CERTS=/run/dev-ca.crt NODE_PATH=/tmp/oss-browser-test/node_modules \
      node /run/browser_e2e.cjs
  '
