"""Build an approved, exact-name NetUP Lab to LibreNMS mapping without printing identifiers.

Requires NETUP_LAB_* environment variables and a protected LibreNMS token file.
Never maps ports. Only exact names unique in both source lists are accepted.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path

from app.modules.billing.netup.config import NetupConfig
from app.modules.diagnostics.config import MappingRegistry, MonitoringConfig, protected_text
from app.modules.diagnostics.http import JsonTransport
from app.modules.diagnostics.mapping import unique_exact_mappings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-unique-exact-sysname", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.approve_unique_exact_sysname:
        parser.error("explicit owner approval flag is required")
    output: Path = args.output
    if not output.exists() or MappingRegistry.from_file(str(output)).devices:
        raise RuntimeError("mapping target must exist as a protected, empty registry")

    netup = NetupConfig.model_validate(
        {
            "app_env": "dev",
            "purpose": "lab",
            "host": os.environ.get("NETUP_LAB_HOST"),
            "port": os.environ.get("NETUP_LAB_PORT", "3306"),
            "database": os.environ.get("NETUP_LAB_DATABASE", "UTM5"),
            "user": os.environ.get("NETUP_LAB_USER"),
            "password": os.environ.get("NETUP_LAB_PASSWORD"),
            "allow_plaintext_lab": os.environ.get("NETUP_LAB_ALLOW_PLAINTEXT", "false"),
        }
    )
    with netup.connect() as db:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT id, name FROM switches WHERE is_deleted=0 "
                "AND name IS NOT NULL AND name<>'' LIMIT 10001"
            )
            netup_rows = list(cursor.fetchall())
    if len(netup_rows) > 10000:
        raise RuntimeError("NetUP switch result exceeds the configured limit")

    config = MonitoringConfig.from_env()
    url = config.validate_endpoint(config.librenms_url)
    if not url or not config.librenms_token_file:
        raise RuntimeError("LibreNMS endpoint and protected token file are required")
    transport = JsonTransport(
        url,
        {"Authorization": "Bearer " + protected_text(config.librenms_token_file)},
        config.timeout_seconds,
        config.ca_file,
    )
    response = transport.get("/api/v0/devices")
    libre_rows = response.get("devices")
    if response.get("status") != "ok" or not isinstance(libre_rows, list) or len(libre_rows) > 4096:
        raise RuntimeError("LibreNMS device list is unavailable or exceeds the limit")
    registry = unique_exact_mappings(netup_rows, libre_rows)
    if not registry.devices:
        raise RuntimeError("no unique exact device names match")

    descriptor, temporary = tempfile.mkstemp(prefix=".monitoring-mappings-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(registry.model_dump(mode="json"), file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"unique_exact_device_mappings={len(registry.devices)}; port_mappings=0")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"mapping build failed: {type(error).__name__}") from None
