"""Ephemeral cabinet reads. Secrets never enter BillingProvider/snapshot DTOs."""

from dataclasses import dataclass

from pydantic import SecretStr

from app.core.config import Settings
from app.modules.billing.netup.config import NetupConfig


class CabinetUnavailable(Exception):
    """Deliberately carries no source values or DBAPI error details."""


@dataclass(frozen=True, repr=False)
class CabinetCredentials:
    login: str
    password: SecretStr | None = None


def password_enabled(settings: Settings, config: NetupConfig) -> bool:
    if settings.subscriber_cabinet_mode == "management":
        return (
            getattr(config, "allow_plaintext_management", False)
            and config.host == "172.17.7.1" and config.port == 3307
            and config.purpose == "production"
        )
    if settings.subscriber_cabinet_mode == "verified":
        return (
            settings.subscriber_cabinet_memo_verified
            and config.purpose == "production" and config.tls_ca is not None
        )
    return (
        settings.subscriber_cabinet_mode == "dev_preview"
        and settings.app_env in {"dev", "test"}
        and config.purpose == "lab"
    )


def load_cabinet(
    config: NetupConfig, account_id: int, user_id: int, *, password: bool = False,
    require_tls: bool = True,
) -> CabinetCredentials:
    connection = config.connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION max_statement_time=%s", (config.max_execution_ms / 1000,))
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            if password and getattr(config, "allow_plaintext_management", False):
                cursor.execute("SELECT @@server_id AS server_id, @@read_only AS read_only")
                replica = cursor.fetchone()
                if not replica or replica["server_id"] != 2 or replica["read_only"] != 1:
                    raise CabinetUnavailable from None
            if password and require_tls:
                cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                tls = cursor.fetchone()
                if config.tls_ca is None or not tls or not tls.get("Value"):
                    raise CabinetUnavailable from None
            # Resolve ALL owners before comparing with the stored lookup identity.
            # Never filter by expected user_id here: that would hide ambiguity.
            cursor.execute(
                "SELECT DISTINCT u.id FROM accounts a "
                "JOIN users_accounts ua ON ua.account_id=a.id AND ua.is_deleted=0 "
                "JOIN users u ON u.id=ua.uid AND u.is_deleted=0 "
                "WHERE a.id=%s AND a.is_deleted=0 ORDER BY u.id LIMIT 2", (account_id,),
            )
            owners = cursor.fetchall()
            if len(owners) != 1 or owners[0]["id"] != user_id:
                raise CabinetUnavailable from None
            # Login requests never select, transfer or inspect the password column.
            fields = "login,password,encrypted" if password else "login"
            cursor.execute(
                f"SELECT {fields} FROM users WHERE id=%s AND is_deleted=0 LIMIT 1", (user_id,),
            )
            row = cursor.fetchone()
            if not row or not isinstance(row["login"], str) or not row["login"]:
                raise CabinetUnavailable from None
            secret = None
            if password:
                value = row.get("password")
                if row.get("encrypted") != 0 or not isinstance(value, str) or not value:
                    raise CabinetUnavailable from None
                secret = SecretStr(value)
            return CabinetCredentials(row["login"], secret)
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()
