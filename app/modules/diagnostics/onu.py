"""Bounded read-only BDCOM inventory access and conservative ONU candidates."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import pymysql
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.diagnostics.config import protected_text
from app.modules.diagnostics.http import SourceUnavailable


class OnuCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    olt: str | None = Field(max_length=200)
    interface: str = Field(max_length=100)
    pon_type: Literal["EPON", "GPON"]
    mac: str | None = Field(max_length=100)
    serial: str | None = Field(max_length=100)
    state: Literal["ONLINE", "OFFLINE", "DEREGISTERED", "UNKNOWN"]
    reason: str | None = Field(max_length=300)
    onu_rx_dbm: str | None = None
    olt_rx_dbm: str | None = None
    onu_tx_dbm: str | None = None
    inventory_polled_at: str | None = None
    optical_measured_at: str | None = None


class OnuResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["BDCOM"] = "BDCOM"
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    availability: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN", "CONFLICT"]
    association: Literal["UNCONFIRMED"] = "UNCONFIRMED"
    candidates: list[OnuCandidate] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=10)
    time_basis: Literal["SOURCE_LOCAL_UNVERIFIED"] = "SOURCE_LOCAL_UNVERIFIED"


@dataclass(frozen=True)
class OnuDbConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    tls_ca: str | None
    allow_plaintext_dev: bool
    app_env: str
    unix_socket: str | None = None

    @classmethod
    def from_tunnel_password_file(cls, path: str, socket_path: str, app_env: str) -> "OnuDbConfig":
        """Use the protected local socket proxy only in the isolated dev stack."""
        if app_env != "dev":
            raise ValueError("BDCOM tunnel plaintext is dev-only")
        if socket_path != "/run/bdcom/bdcom.sock":
            raise ValueError("BDCOM tunnel socket path is invalid")
        password = protected_text(path)
        if not password:
            raise ValueError("BDCOM password file is empty")
        return cls(
            host="127.0.0.1",
            port=13306,
            database="librenms",
            user="oss_diagnostics",
            password=password,
            tls_ca=None,
            allow_plaintext_dev=True,
            app_env=app_env,
            unix_socket=socket_path,
        )

    @classmethod
    def from_file(cls, path: str, app_env: str) -> "OnuDbConfig":
        import json

        try:
            values = json.loads(protected_text(path))
        except (OSError, ValueError, TypeError):
            raise ValueError("invalid BDCOM credential file") from None
        if not isinstance(values, dict) or set(values) != {
            "host",
            "port",
            "database",
            "user",
            "password",
            "tls_ca",
            "allow_plaintext_dev",
        }:
            raise ValueError("invalid BDCOM credential fields")
        if not all(
            isinstance(values[key], str) and values[key]
            for key in ("host", "database", "user", "password")
        ):
            raise ValueError("invalid BDCOM connection identity")
        if not isinstance(values["port"], int) or not 1 <= values["port"] <= 65535:
            raise ValueError("invalid BDCOM port")
        if values["database"] != "librenms":
            raise ValueError("BDCOM reader must use librenms database")
        if values["tls_ca"] is not None and not isinstance(values["tls_ca"], str):
            raise ValueError("invalid BDCOM CA path")
        if not isinstance(values["allow_plaintext_dev"], bool):
            raise ValueError("invalid BDCOM TLS policy")
        if values["tls_ca"] is None and not (app_env == "dev" and values["allow_plaintext_dev"]):
            raise ValueError("BDCOM plaintext is dev-only and requires opt-in")
        if values["tls_ca"] is not None and not Path(values["tls_ca"]).is_file():
            raise ValueError("BDCOM CA file unavailable")
        return cls(**values, app_env=app_env)


def _source_time(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return None


def _optical(value: object) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return str(number) if number.is_finite() and -100 <= number <= 100 else None


def normalize_candidate(row: dict, olt: dict) -> OnuCandidate | None:
    pon_type = str(row.get("pon_type") or "").upper()
    interface = row.get("interface")
    if pon_type not in {"EPON", "GPON"} or not isinstance(interface, str) or not interface:
        return None
    serial = row.get("serial") or None
    # The GPON collector stores lower(serial) in the `mac` unique-key column.
    mac = row.get("mac") if pon_type == "EPON" else None
    if not mac and not serial:
        return None
    raw_state = str(row.get("state") or "").lower()
    state = {
        "online": "ONLINE",
        "offline": "OFFLINE",
        "lost": "OFFLINE",
        "deregistered": "DEREGISTERED",
    }.get(raw_state, "UNKNOWN")
    try:
        return OnuCandidate(
            olt=olt.get("sys_name") or olt.get("hostname"),
            interface=interface,
            pon_type=pon_type,
            mac=mac,
            serial=serial,
            state=state,
            reason=row.get("reason") or None,
            onu_rx_dbm=_optical(row.get("onu_rx_power")),
            olt_rx_dbm=_optical(row.get("olt_rx_power")),
            onu_tx_dbm=_optical(row.get("onu_tx_power")),
            inventory_polled_at=_source_time(row.get("last_seen")),
            optical_measured_at=_source_time(row.get("signal_updated_at")),
        )
    except ValidationError:
        return None


class BdcomOnuReader:
    """A SELECT-only account should have access to exactly two custom tables."""

    def __init__(self, config: OnuDbConfig):
        self.config = config

    def candidates(self, device_id: int, vlan: int) -> tuple[dict, list[dict], bool]:
        connect_options = {
            "user": self.config.user,
            "password": self.config.password,
            "database": self.config.database,
            "connect_timeout": 3,
            "read_timeout": 3,
            "write_timeout": 3,
            "cursorclass": pymysql.cursors.DictCursor,
            "autocommit": True,
        }
        if self.config.unix_socket:
            connect_options["unix_socket"] = self.config.unix_socket
            connect_options["ssl_disabled"] = True
        else:
            connect_options["host"] = self.config.host
            connect_options["port"] = self.config.port
        if self.config.tls_ca:
            connect_options["ssl_ca"] = self.config.tls_ca
            connect_options["ssl_verify_cert"] = True
            connect_options["ssl_verify_identity"] = True
        try:
            with pymysql.connect(**connect_options) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT device_id, hostname, sys_name, last_success "
                    "FROM bdcom_onu_olts WHERE device_id = %s LIMIT 2",
                    (device_id,),
                )
                olts = cursor.fetchall()
                if len(olts) != 1:
                    return {}, [], len(olts) > 1
                cursor.execute(
                    "SELECT pon_type, interface, mac, serial, state, reason, present, last_seen, "
                    "onu_rx_power, onu_tx_power, olt_rx_power, signal_updated_at "
                    "FROM bdcom_onus WHERE device_id = %s AND vlan = %s AND present = 1 "
                    "ORDER BY interface, mac LIMIT 21",
                    (device_id, vlan),
                )
                rows = cursor.fetchall()
                return olts[0], rows[:20], len(rows) > 20
        except (pymysql.MySQLError, OSError):
            # Driver errors may embed host, user, query or other source details.
            raise SourceUnavailable("bdcom_source_unavailable") from None
