"""Telegram Mini App signature and freshness checks."""

import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from app.core.errors import ApiError
from app.modules.field_dispatch.miniapp_auth import verify_init_data

TOKEN = "synthetic-bot-token"
NOW = 1_800_000_000


def signed_data(user_id: int = 12345, auth_date: int = NOW) -> str:
    fields = {
        "user": json.dumps({"id": user_id}, separators=(",", ":")),
        "auth_date": str(auth_date),
        "query_id": "synthetic-query",
    }
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_signed_data() -> None:
    assert verify_init_data(signed_data(), TOKEN, now=NOW) == 12345


@pytest.mark.parametrize(
    "raw",
    [
        lambda: signed_data().replace("12345", "99999"),
        lambda: signed_data(auth_date=NOW - 3601),
        lambda: signed_data(auth_date=NOW + 31),
        lambda: signed_data() + "&user=%7B%22id%22%3A1%7D",
        lambda: "user=%7B%22id%22%3A12345%7D&auth_date=1800000000",
    ],
)
def test_invalid_or_expired_data(raw) -> None:
    with pytest.raises(ApiError) as error:
        verify_init_data(raw(), TOKEN, now=NOW)
    assert error.value.status_code == 401
