"""BillingProvider adapter, intentionally not selected by default HTTP routing."""

from app.modules.billing.netup.config import NetupConfig
from app.modules.billing.netup.normalizer import normalize_netup
from app.modules.billing.netup.repository import NetupRepository
from app.modules.billing.provider import (
    BillingAmbiguous,
    BillingIncomplete,
    BillingInvalidAccount,
    BillingUnavailable,
    Subscriber360,
)


class NetUPBillingProvider:
    def __init__(self, repository: NetupRepository) -> None:
        self.repository = repository

    @classmethod
    def from_config(cls, config: NetupConfig) -> "NetUPBillingProvider":
        return cls(NetupRepository(config.connect, config.max_execution_ms, config.lab_as_of_utc))

    def lookup_by_account(self, account_no: str) -> Subscriber360 | None:
        try:
            raw = self.repository.lookup_by_account(account_no)
            return normalize_netup(raw) if raw is not None else None
        except (BillingInvalidAccount, BillingAmbiguous, BillingIncomplete):
            raise
        except Exception:
            # DBAPI exceptions may include SQL parameters or credentials.
            raise BillingUnavailable from None
