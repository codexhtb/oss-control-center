"""Read-only LibreNMS and Icinga2 API adapters with bounded responses."""

import base64
from dataclasses import dataclass
from datetime import UTC, datetime

from app.modules.diagnostics.http import JsonTransport, SourceUnavailable


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not str(value).isdecimal() or int(str(value)) < 1:
        raise SourceUnavailable("invalid_identifier")
    return int(str(value))


def _measurement(value: object) -> datetime | None:
    """Only timezone-aware timestamps or Unix epochs are safe to treat as instants."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        try:
            return datetime.fromtimestamp(value, UTC)
        except (OverflowError, ValueError):
            return None
    if isinstance(value, str):
        if value.isdecimal() and 1_000_000_000 <= int(value) <= 20_000_000_000:
            try:
                return datetime.fromtimestamp(int(value), UTC)
            except (OverflowError, ValueError):
                return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(UTC) if parsed.tzinfo else None
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class LibreDevice:
    device_id: int
    hostname: str
    state: str
    observed_at: datetime | None


@dataclass(frozen=True)
class LibrePort:
    port_id: int
    device_id: int
    if_name: str | None
    if_index: int | None
    oper_status: str
    admin_status: str
    observed_at: datetime | None


class LibreNMSProvider:
    def __init__(self, transport: JsonTransport):
        self.transport = transport

    def probe(self) -> None:
        result = self.transport.get("/api/v0")
        if not result:
            raise SourceUnavailable("invalid_api_root")

    def device(self, device_id: int) -> LibreDevice:
        result = self.transport.get(f"/api/v0/devices/{_positive_int(device_id)}")
        devices = result.get("devices")
        if result.get("status") != "ok" or not isinstance(devices, list) or len(devices) != 1:
            raise SourceUnavailable("device_not_unique_or_missing")
        item = devices[0]
        if not isinstance(item, dict) or _positive_int(item.get("device_id")) != device_id:
            raise SourceUnavailable("device_id_mismatch")
        raw_state = item.get("status")
        state = (
            "UP"
            if raw_state in (1, "1", True)
            else "DOWN"
            if raw_state in (0, "0", False)
            else "UNKNOWN"
        )
        return LibreDevice(
            device_id=device_id,
            hostname=str(item.get("hostname") or "")[:200],
            state=state,
            observed_at=_measurement(item.get("last_polled")),
        )

    def ports(self, device_id: int) -> list[LibrePort]:
        result = self.transport.get(
            f"/api/v0/devices/{_positive_int(device_id)}/ports",
            {"columns": "port_id,device_id,ifIndex,ifName,ifOperStatus,ifAdminStatus,poll_prev"},
        )
        ports = result.get("ports")
        if result.get("status") != "ok" or not isinstance(ports, list) or len(ports) > 4096:
            raise SourceUnavailable("ports_unavailable_or_unbounded")
        answer = []
        seen: set[int] = set()
        for item in ports:
            if not isinstance(item, dict):
                raise SourceUnavailable("invalid_port")
            port_id = _positive_int(item.get("port_id"))
            if port_id in seen or _positive_int(item.get("device_id")) != device_id:
                raise SourceUnavailable("duplicate_or_foreign_port")
            seen.add(port_id)
            if_index = item.get("ifIndex")
            answer.append(
                LibrePort(
                    port_id=port_id,
                    device_id=device_id,
                    if_name=str(item["ifName"])[:200] if item.get("ifName") else None,
                    if_index=int(if_index) if str(if_index).isdecimal() else None,
                    oper_status=str(item.get("ifOperStatus") or "unknown").lower(),
                    admin_status=str(item.get("ifAdminStatus") or "unknown").lower(),
                    observed_at=_measurement(item.get("poll_prev")),
                )
            )
        return answer


@dataclass(frozen=True)
class IcingaCheck:
    name: str
    state: str
    observed_at: datetime | None


class IcingaProvider:
    def __init__(self, transport: JsonTransport):
        self.transport = transport

    @staticmethod
    def authorization(user: str, password: str) -> str:
        return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")

    def host(self, name: str) -> IcingaCheck:
        result = self.transport.get(
            "/v1/objects/hosts",
            {"host": name, "attrs": ["name", "state", "last_check", "last_reachable"]},
        )
        rows = result.get("results")
        if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("name") != name:
            raise SourceUnavailable("host_not_unique_or_missing")
        attrs = rows[0].get("attrs") or {}
        state = attrs.get("state")
        if attrs.get("last_reachable") is False:
            label = "UNREACHABLE"
        else:
            label = "UP" if state in (0, 0.0) else "DOWN" if state in (1, 1.0) else "UNKNOWN"
        return IcingaCheck(
            name=name, state=label, observed_at=_measurement(attrs.get("last_check"))
        )

    def services(self, host: str) -> list[IcingaCheck]:
        # Host is validated by the mapping schema; quote its filter literal anyway.
        escaped = host.replace("\\", "\\\\").replace('"', '\\"')
        result = self.transport.get(
            "/v1/objects/services",
            {
                "filter": f'host.name=="{escaped}"',
                "attrs": ["name", "state", "last_check", "last_reachable"],
            },
        )
        rows = result.get("results")
        if not isinstance(rows, list) or len(rows) > 100:
            raise SourceUnavailable("services_unavailable_or_unbounded")
        answer = []
        for item in rows:
            if not isinstance(item, dict) or not str(item.get("name", "")).startswith(host + "!"):
                raise SourceUnavailable("foreign_service")
            attrs = item.get("attrs") or {}
            state = attrs.get("state")
            label = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}.get(state, "UNKNOWN")
            answer.append(
                IcingaCheck(
                    name=str(item["name"])[len(host) + 1 :][:200],
                    state=label,
                    observed_at=_measurement(attrs.get("last_check")),
                )
            )
        return answer
