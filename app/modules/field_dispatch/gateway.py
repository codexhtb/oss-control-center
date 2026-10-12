"""Transport boundary only; no Telegram implementation or credentials."""

from typing import NamedTuple, Protocol


class DispatchReceipt(NamedTuple):
    chat_id: str
    message_id: str


class FieldDispatchGateway(Protocol):
    def send_message(self, chat_id: str, text: str) -> DispatchReceipt: ...
