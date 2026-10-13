"""Origin and data quality of individual billing fields."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class DataQuality(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"
    CONFLICT = "CONFLICT"
    UNAVAILABLE = "UNAVAILABLE"


class FieldOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["MOCK", "NETUP"]
    observed_at: datetime
    quality: DataQuality

    @field_validator("observed_at")
    @classmethod
    def require_utc_instant(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Observed time must include a timezone")
        return value.astimezone(UTC)
