"""Broker staff credentials to Chromium through a private pipe, never a file/CLI argument."""

import json
import subprocess
import sys
from pathlib import Path


def run(args, *, input=None):
    result = subprocess.run(args, input=input, text=True, capture_output=True)
    if result.returncode:
        # Never print Playwright/httpx payloads, stack traces or arguments.
        raise RuntimeError("Cabinet live acceptance subprocess failed")
    return result.stdout


def main():
    root = Path(__file__).resolve().parents[1]
    dev = sys.argv[1:] == ["--dev"]
    assert dev or sys.argv[1:] == []
    origin = "https://172.17.7.2:9443" if dev else "https://sd.oss-ab.net"
    web = "oss-cabinet-dev-cabinet_web-1" if dev else "oss-control-dev-web-1"
    network = "oss-cabinet-dev_isolated" if dev else "oss-control-dev_backend"
    address = run(
        [
            "docker",
            "inspect",
            web,
            "--format",
            '{{with index .NetworkSettings.Networks "' + network + '"}}{{.IPAddress}}{{end}}',
        ]
    ).strip()
    assert address
    helper = [
        "docker",
        "run",
        "--rm",
        "--user",
        "1001:1001",
        "--network",
        "oss-control-dev_backend",
    ]
    if dev:
        helper += ["--network", "oss-cabinet-dev_isolated"]
    helper += [
        "--add-host",
        "sd.oss-ab.net:" + address,
        "--env-file",
        str(root / ".env.dev"),
        "-e",
        "POSTGRES_HOST=postgres",
        "-e",
        "OSS_CABINET_LIVE_SMOKE=1",
        "-e",
        "AUTH_PUBLIC_ORIGIN=" + origin,
        "-e",
        "SUBSCRIBER_CABINET_MODE=management",
        "-v",
        f"{root}/dev/cabinet_management_live.py:/app/dev/cabinet_management_live.py:ro",
        "-v",
        f"{root}/.dev-secrets/credentials/notification-center-smoke.json:/run/staff-smoke.json:ro",
    ]
    if dev:
        helper += [
            "-e",
            "POSTGRES_DB=oss_test_subscriber_cabinet",
            "-v",
            f"{root}/.dev-secrets/certs/ca.crt:/run/dev-ca.crt:ro",
        ]
    helper += ["oss-cabinet-management-api:tested", "python", "-m", "dev.cabinet_management_live"]
    metadata = json.loads(run(helper + ["prepare"]))
    try:
        manifest = json.loads(
            (root / ".dev-secrets/credentials/notification-center-smoke.json").read_text()
        )
        password = manifest[next(key for key in manifest if "operator" in key)]
        browser = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--user",
            "1001:1001",
            "--network",
            network,
            "--add-host",
            "sd.oss-ab.net:" + address,
            "--shm-size=1g",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs",
            "/tmp:rw,exec,uid=1001,gid=1001,size=1g",
            "-e",
            "HOME=/tmp/browser-home",
            "-e",
            "NODE_PATH=/opt/browser-runtime/node_modules",
            "-v",
            "/tmp/oss-notification-browser-runtime:/opt/browser-runtime:ro",
            "-v",
            f"{root}/dev/browser_cabinet_operator_live.cjs:/run/browser.cjs:ro",
        ]
        if dev:
            browser += ["-v", f"{root}/.dev-secrets/certs/ca.crt:/run/dev-ca.crt:ro"]
        command = (
            'mkdir -p "$HOME/.pki/nssdb" && certutil -N -d "sql:$HOME/.pki/nssdb" --empty-password'
        )
        if dev:
            command += (
                ' && certutil -A -d "sql:$HOME/.pki/nssdb" -n oss-dev -t "C,," -i /run/dev-ca.crt'
            )
        browser += [
            "oss-control-dev-browser:local",
            "sh",
            "-c",
            command + " && node /run/browser.cjs",
        ]
        output = run(
            browser,
            input=json.dumps(
                {
                    "origin": origin,
                    "password": password,
                    "lookup_id": metadata["lookup_id"],
                }
            ),
        )
        assert output.strip() == (
            "PASS operator: HTTPS login, lazy reveal, copy login/password, hide; "
            "no secret artifacts"
        )
        print(("DEV " if dev else "PROD ") + output.strip())
        print("PASS: API CSRF/RBAC, no-store, audit and snapshot storage checks")
    finally:
        cleanup = run(helper + ["cleanup", metadata["user_id"]])
        assert cleanup.strip() == "PASS: temporary operator removed and sessions revoked"
        print(cleanup.strip())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Cabinet live acceptance failed; sensitive details suppressed") from None
