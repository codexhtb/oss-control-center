"""Transport boundary for confirmed Telegram Bot API delivery."""

import http.client
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Protocol
from urllib.parse import quote


class DispatchReceipt(NamedTuple):
    chat_id: str
    message_id: str


class FieldDispatchGateway(Protocol):
    def send_message(
        self, chat_id: str, text: str, *, completion_callback_data: str | None = None
    ) -> DispatchReceipt: ...


class DispatchRejected(Exception):
    """Telegram returned a definite rejection; retry requires an explicit action."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DispatchUncertain(Exception):
    """Transport outcome is unknown; automatic retry could duplicate a message."""


@dataclass(frozen=True)
class TelegramGatewayConfig:
    token_file: Path
    allowed_chat_id: str = "-5319791765"
    timeout_seconds: int = 8

    def read_token(self) -> str:
        try:
            stat = self.token_file.stat()
            if not self.token_file.is_file() or stat.st_mode & 0o077:
                raise DispatchRejected("telegram_secret_permissions")
            token = self.token_file.read_text(encoding="utf-8").strip()
        except DispatchRejected:
            raise
        except OSError:
            raise DispatchRejected("telegram_secret_unavailable") from None
        if not token or len(token) > 256 or any(char.isspace() for char in token):
            raise DispatchRejected("telegram_secret_invalid")
        return token


class TelegramBotGateway:
    """Small synchronous client; token and message content never enter logs."""

    def __init__(self, config: TelegramGatewayConfig) -> None:
        self.config = config

    def _call(self, method: str, parameters: dict[str, Any]) -> Any:
        token = self.config.read_token()
        body = json.dumps(parameters, ensure_ascii=False).encode("utf-8")
        connection = http.client.HTTPSConnection(
            "api.telegram.org", timeout=self.config.timeout_seconds
        )
        try:
            connection.request(
                "POST",
                f"/bot{quote(token, safe='')}/{method}",
                body=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            response = connection.getresponse()
            payload = response.read(64_000)
        except (OSError, TimeoutError, http.client.HTTPException):
            raise DispatchUncertain from None
        finally:
            connection.close()
        try:
            decoded = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise DispatchUncertain from None
        if not isinstance(decoded, dict):
            raise DispatchUncertain
        if response.status != 200 or decoded.get("ok") is not True:
            error_code = decoded.get("error_code")
            raise DispatchRejected(
                f"telegram_http_{error_code if isinstance(error_code, int) else response.status}"
            )
        return decoded.get("result")

    def send_message(
        self, chat_id: str, text: str, *, completion_callback_data: str | None = None
    ) -> DispatchReceipt:
        if chat_id != self.config.allowed_chat_id:
            raise DispatchRejected("telegram_chat_not_allowed")
        parameters: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if completion_callback_data is not None:
            parameters["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": "✅ Выполнено", "callback_data": completion_callback_data}]
                ]
            }
        result = self._call("sendMessage", parameters)
        if not isinstance(result, dict):
            raise DispatchUncertain from None
        result_chat = result.get("chat")
        message_id = result.get("message_id")
        confirmed_chat = result_chat.get("id") if isinstance(result_chat, dict) else None
        if str(confirmed_chat) != chat_id or not isinstance(message_id, int):
            raise DispatchUncertain from None
        return DispatchReceipt(chat_id=chat_id, message_id=str(message_id))

    def get_updates(self, offset: int, *, timeout_seconds: int = 20) -> list[dict[str, Any]]:
        result = self._call(
            "getUpdates",
            {
                "offset": offset,
                "limit": 50,
                "timeout": timeout_seconds,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise DispatchUncertain from None
        return result

    def acknowledge_callback(self, callback_query_id: str, text: str) -> None:
        result = self._call(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id, "text": text, "show_alert": False},
        )
        if result is not True:
            raise DispatchUncertain from None

    def send_completion_prompt(self, chat_id: str, handoff_message_id: int) -> DispatchReceipt:
        """Ask for the report and bind the next reply to this exact prompt."""

        if chat_id != self.config.allowed_chat_id:
            raise DispatchRejected("telegram_chat_not_allowed")
        result = self._call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "Что было сделано?",
                "reply_parameters": {
                    "message_id": handoff_message_id,
                    "allow_sending_without_reply": False,
                },
                "reply_markup": {
                    "force_reply": True,
                    "input_field_placeholder": "Опишите выполненные работы",
                },
            },
        )
        if not isinstance(result, dict):
            raise DispatchUncertain from None
        result_chat = result.get("chat")
        message_id = result.get("message_id")
        confirmed_chat = result_chat.get("id") if isinstance(result_chat, dict) else None
        if str(confirmed_chat) != chat_id or not isinstance(message_id, int):
            raise DispatchUncertain from None
        return DispatchReceipt(chat_id=chat_id, message_id=str(message_id))

    def mark_message_completed(self, chat_id: str, message_id: int) -> None:
        if chat_id != self.config.allowed_chat_id:
            raise DispatchRejected("telegram_chat_not_allowed")
        result = self._call(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {"inline_keyboard": [[{"text": "✅ Выполнено", "disabled": True}]]},
            },
        )
        if not isinstance(result, dict):
            raise DispatchUncertain from None
