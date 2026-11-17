"""Verify Telegram Mini App initData without trusting client-supplied identity."""

import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl

from app.core.errors import ApiError
from app.modules.field_dispatch.gateway import DispatchRejected, TelegramGatewayConfig

MAX_AGE_SECONDS = 3600
MAX_FUTURE_SECONDS = 30


def verify_init_data(raw: str, token: str, *, now: int | None = None) -> int:
    """Return the signed Telegram user ID, or reject the entire payload."""
    if not raw or len(raw) > 8192:
        raise ApiError(401, "invalid_telegram_auth", "Invalid Telegram authentication")
    try:
        pairs = parse_qsl(
            raw, keep_blank_values=True, strict_parsing=True, max_num_fields=40, errors="strict"
        )
        fields = dict(pairs)
        if len(fields) != len(pairs) or not {"hash", "auth_date", "user"} <= fields.keys():
            raise ValueError("missing or duplicate fields")
        received_hash = fields.pop("hash")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", received_hash):
            raise ValueError("invalid hash")
        data_check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash.lower()):
            raise ValueError("signature mismatch")
        auth_date = int(fields["auth_date"])
        current = int(time.time()) if now is None else now
        if auth_date > current + MAX_FUTURE_SECONDS or current - auth_date > MAX_AGE_SECONDS:
            raise ValueError("expired")
        user = json.loads(fields["user"])
        user_id = user["id"]
        if type(user_id) is not int or not 0 < user_id < 2**52:
            raise ValueError("invalid user ID")
        return user_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ApiError(401, "invalid_telegram_auth", "Invalid Telegram authentication") from exc


def configured_bot_token() -> str:
    """Reuse the existing protected token file; never emit its contents."""
    import os

    path = os.environ.get("TELEGRAM_BOT_TOKEN_FILE")
    if not path:
        raise ApiError(503, "telegram_not_configured", "Telegram authentication unavailable")
    try:
        return TelegramGatewayConfig(Path(path)).read_token()
    except DispatchRejected as exc:
        raise ApiError(
            503, "telegram_not_configured", "Telegram authentication unavailable"
        ) from exc
