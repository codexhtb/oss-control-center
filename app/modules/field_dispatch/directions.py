"""Server-owned destinations for field work dispatch."""

import os
import re
from typing import Literal

from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.field_dispatch.binding import destination_chat_id

Direction = Literal["SUKHUM", "OCHAMCHIRA"]


def destination_for_direction(db: Session, direction: Direction) -> str:
    if direction == "SUKHUM":
        return destination_chat_id(db)
    chat_id = os.environ.get("TELEGRAM_OCHAMCHIRA_CHAT_ID", "")
    if re.fullmatch(r"-[1-9][0-9]{0,31}", chat_id) is None:
        raise ApiError(409, "telegram_group_not_configured", "Telegram destination is unavailable")
    return chat_id


def direction_title(direction: Direction | None) -> str | None:
    return {"SUKHUM": "OSS Sukhum", "OCHAMCHIRA": "OSS Ochamchira"}.get(direction)
