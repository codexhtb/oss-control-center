"""Transport boundary for confirmed Telegram Bot API delivery."""

import http.client
import json
import re
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


def completion_prompt_text(username: str | None, context: str | None = None) -> str:
    text = "Что было сделано?"
    if context:
        text += f"\n{context}"
    return f"{text}\n@{username}" if username else text


def reschedule_prompt_text(username: str | None, context: str | None = None) -> str:
    base = (
        "Укажите перенос одним ответом на это сообщение:\n"
        "Причина: <почему переносится выезд>\n"
        "Дата: ДД.ММ.ГГГГ ЧЧ:ММ (время Абхазии)\n"
        "Если дату не согласовали: Дата: не согласована\n"
        "Для отмены ответьте /cancel."
    )
    if context:
        base += f"\n{context}"
    return f"{base}\n@{username}" if username else base


@dataclass(frozen=True)
class TelegramGatewayConfig:
    token_file: Path
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
            # A failed TCP/TLS handshake cannot have delivered an HTTP request.
            # Once request() starts, a transport error has an unknown outcome.
            try:
                connection.connect()
            except (OSError, TimeoutError, http.client.HTTPException):
                raise DispatchRejected("telegram_connect_failed") from None
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
            description = decoded.get("description")
            if error_code == 400 and isinstance(description, str):
                if method == "editMessageText" and "message is not modified" in description.lower():
                    raise DispatchRejected("telegram_message_not_modified")
                if (
                    method == "deleteMessage"
                    and "message to delete not found" in description.lower()
                ):
                    raise DispatchRejected("telegram_message_not_found")
            raise DispatchRejected(
                f"telegram_http_{error_code if isinstance(error_code, int) else response.status}"
            )
        return decoded.get("result")

    def send_message(
        self, chat_id: str, text: str, *, completion_callback_data: str | None = None
    ) -> DispatchReceipt:
        _validate_chat_id(chat_id)
        parameters: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if completion_callback_data is not None:
            reschedule_data = completion_callback_data.replace(
                "field_done:", "field_reschedule:", 1
            )
            parameters["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {"text": "✅ Выполнено", "callback_data": completion_callback_data},
                        {"text": "📅 Перенести", "callback_data": reschedule_data},
                    ]
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
                "allowed_updates": ["message", "callback_query", "my_chat_member"],
            },
        )
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise DispatchUncertain from None
        return result

    def get_me(self) -> dict[str, Any]:
        result = self._call("getMe", {})
        if not isinstance(result, dict) or not isinstance(result.get("id"), int):
            raise DispatchUncertain from None
        return result

    def get_chat_member(self, chat_id: str, user_id: int) -> dict[str, Any]:
        _validate_chat_id(chat_id)
        result = self._call("getChatMember", {"chat_id": chat_id, "user_id": user_id})
        if not isinstance(result, dict) or not isinstance(result.get("status"), str):
            raise DispatchUncertain from None
        return result

    def acknowledge_callback(
        self, callback_query_id: str, text: str, *, show_alert: bool = False
    ) -> None:
        result = self._call(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert},
        )
        if result is not True:
            raise DispatchUncertain from None

    def send_completion_prompt(
        self, chat_id: str, handoff_message_id: int | None, *,
        username: str | None = None, context: str | None = None,
    ) -> DispatchReceipt:
        """Ask for the report and bind the next reply to this exact prompt."""
        _validate_chat_id(chat_id)
        parameters = {
            "chat_id": chat_id,
            "text": completion_prompt_text(username, context),
            "reply_markup": {
                "force_reply": True,
                "input_field_placeholder": "Опишите выполненные работы",
            },
        }
        if handoff_message_id is not None:
            parameters["reply_parameters"] = {
                "message_id": handoff_message_id,
                "allow_sending_without_reply": False,
            }
        if username:
            parameters["reply_markup"]["selective"] = True
        result = self._call("sendMessage", parameters)
        if not isinstance(result, dict):
            raise DispatchUncertain from None
        result_chat = result.get("chat")
        message_id = result.get("message_id")
        confirmed_chat = result_chat.get("id") if isinstance(result_chat, dict) else None
        if str(confirmed_chat) != chat_id or not isinstance(message_id, int):
            raise DispatchUncertain from None
        return DispatchReceipt(chat_id=chat_id, message_id=str(message_id))

    def send_reschedule_prompt(
        self, chat_id: str, handoff_message_id: int | None, *,
        username: str | None = None, context: str | None = None,
    ) -> DispatchReceipt:
        _validate_chat_id(chat_id)
        parameters: dict[str, Any] = {
            "chat_id": chat_id,
            "text": reschedule_prompt_text(username, context),
            "reply_markup": {
                "force_reply": True,
                "input_field_placeholder": "Причина и новая дата или «не согласована»",
            },
        }
        if handoff_message_id is not None:
            parameters["reply_parameters"] = {
                "message_id": handoff_message_id,
                "allow_sending_without_reply": False,
            }
        if username:
            parameters["reply_markup"]["selective"] = True
        result = self._call("sendMessage", parameters)
        if not isinstance(result, dict):
            raise DispatchUncertain from None
        result_chat = result.get("chat")
        message_id = result.get("message_id")
        confirmed_chat = result_chat.get("id") if isinstance(result_chat, dict) else None
        if str(confirmed_chat) != chat_id or not isinstance(message_id, int):
            raise DispatchUncertain from None
        return DispatchReceipt(chat_id=chat_id, message_id=str(message_id))

    def delete_message(self, chat_id: str, message_id: int) -> None:
        """Delete one confirmed bot prompt; caller owns exact message identity."""
        _validate_chat_id(chat_id)
        if message_id <= 0:
            raise ValueError("telegram_message_id_invalid")
        if self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id}) is not True:
            raise DispatchUncertain from None

    def edit_handoff_card(self, chat_id: str, message_id: int, text: str) -> None:
        """Replace the original handoff card with its completed state and no button."""
        _validate_chat_id(chat_id)
        if message_id <= 0 or not text or len(text) > 4096:
            raise ValueError("telegram_card_invalid")
        result = self._call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": {"inline_keyboard": []},
                "link_preview_options": {"is_disabled": True},
            },
        )
        if not isinstance(result, dict):
            raise DispatchUncertain from None
        response_chat = result.get("chat")
        response_message_id = result.get("message_id")
        response_chat_id = response_chat.get("id") if isinstance(response_chat, dict) else None
        reply_markup = result.get("reply_markup")
        if (
            str(response_chat_id) != chat_id
            or response_message_id != message_id
            or result.get("text") != text
            or reply_markup not in (None, {"inline_keyboard": []})
        ):
            raise DispatchUncertain from None

    def edit_reschedule_card(
        self, chat_id: str, message_id: int, text: str, reply_markup: dict[str, Any]
    ) -> None:
        """Replace only the identified original card; safe to retry after an uncertain response."""
        _validate_chat_id(chat_id)
        if message_id <= 0 or not text or len(text) > 4096:
            raise ValueError("telegram_card_invalid")
        result = self._call("editMessageText", {
            "chat_id": chat_id, "message_id": message_id, "text": text,
            "reply_markup": reply_markup,
            "link_preview_options": {"is_disabled": True},
        })
        if not isinstance(result, dict):
            raise DispatchUncertain from None
        response_chat = result.get("chat")
        if (
            not isinstance(response_chat, dict)
            or str(response_chat.get("id")) != chat_id
            or result.get("message_id") != message_id
            or result.get("text") != text
        ):
            raise DispatchUncertain from None


def _validate_chat_id(chat_id: str) -> None:
    if not isinstance(chat_id, str) or re.fullmatch(r"-?[1-9][0-9]{0,31}", chat_id) is None:
        raise DispatchRejected("telegram_chat_invalid")
