"""Transport-level checks for outbound Telegram dispatch."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.modules.field_dispatch.gateway import (
    DispatchRejected,
    DispatchUncertain,
    TelegramBotGateway,
    TelegramGatewayConfig,
)


def test_poller_registers_identity_metadata_in_fresh_process() -> None:
    """A standalone poller must be able to flush its ticket audit event."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.modules.field_dispatch.poller import _send_prompt; "
            "from app.modules.tickets.models import TicketAudit; "
            "assert 'app_users' in TicketAudit.metadata.tables; "
            "assert TicketAudit.__mapper__._sorted_tables",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


class FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self.payload = json.dumps(payload).encode()

    def read(self, _limit: int) -> bytes:
        return self.payload


class FakeConnection:
    response = FakeResponse(
        200, {"ok": True, "result": {"message_id": 71, "chat": {"id": -5319791765}}}
    )
    request_args: tuple | None = None

    def __init__(self, _host: str, timeout: int) -> None:
        self.timeout = timeout

    def request(self, *args, **kwargs) -> None:
        type(self).request_args = (args, kwargs)

    def getresponse(self) -> FakeResponse:
        return type(self).response

    def close(self) -> None:
        pass


def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "telegram-token"
    path.write_text("synthetic-token", encoding="utf-8")
    path.chmod(0o600)
    return path


def test_gateway_accepts_dynamic_group_and_requires_confirmed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = token_file(tmp_path)
    monkeypatch.setattr("http.client.HTTPSConnection", FakeConnection)
    gateway = TelegramBotGateway(TelegramGatewayConfig(path))

    receipt = gateway.send_message(
        "-5319791765",
        "Synthetic field work",
        completion_callback_data="field_done:00000000-0000-4000-8000-000000000001",
    )

    assert receipt.chat_id == "-5319791765"
    assert receipt.message_id == "71"
    args, kwargs = FakeConnection.request_args or ((), {})
    assert args[0] == "POST"
    request_body = json.loads(kwargs["body"])
    assert request_body["chat_id"] == "-5319791765"
    assert request_body["reply_markup"]["inline_keyboard"][0][0] == {
        "text": "✅ Выполнено",
        "callback_data": "field_done:00000000-0000-4000-8000-000000000001",
    }
    assert request_body["reply_markup"]["inline_keyboard"][0][1] == {
        "text": "📅 Перенести",
        "callback_data": "field_reschedule:00000000-0000-4000-8000-000000000001",
    }
    FakeConnection.response = FakeResponse(
        200, {"ok": True, "result": {"message_id": 72, "chat": {"id": -7654321}}}
    )
    assert gateway.send_message("-7654321", "synthetic").chat_id == "-7654321"
    with pytest.raises(DispatchRejected, match="telegram_chat_invalid"):
        gateway.send_message("not-a-chat", "must not be sent")


def test_gateway_treats_receipt_mismatch_as_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = token_file(tmp_path)
    FakeConnection.response = FakeResponse(
        200, {"ok": True, "result": {"message_id": 72, "chat": {"id": -1}}}
    )
    monkeypatch.setattr("http.client.HTTPSConnection", FakeConnection)
    with pytest.raises(DispatchUncertain):
        TelegramBotGateway(TelegramGatewayConfig(path)).send_message(
            "-5319791765", "Synthetic field work"
        )


def test_gateway_rejects_overbroad_secret_permissions(tmp_path: Path) -> None:
    path = token_file(tmp_path)
    path.chmod(0o644)
    with pytest.raises(DispatchRejected, match="telegram_secret_permissions"):
        TelegramBotGateway(TelegramGatewayConfig(path)).send_message(
            "-5319791765", "Synthetic field work"
        )


def test_gateway_acknowledges_callback_and_edits_card_without_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = token_file(tmp_path)
    monkeypatch.setattr("http.client.HTTPSConnection", FakeConnection)
    gateway = TelegramBotGateway(TelegramGatewayConfig(path))
    FakeConnection.response = FakeResponse(200, {"ok": True, "result": True})
    gateway.acknowledge_callback("synthetic-callback", "Сохранено")
    args, _ = FakeConnection.request_args or ((), {})
    assert args[1].endswith("/answerCallbackQuery")

    completed_text = "Заявка #1\n\n✅ Выполнено\nМонтажник: Тест"
    FakeConnection.response = FakeResponse(
        200,
        {
            "ok": True,
            "result": {"message_id": 71, "chat": {"id": -5319791765}, "text": completed_text},
        },
    )
    gateway.edit_handoff_card("-5319791765", 71, completed_text)
    args, kwargs = FakeConnection.request_args or ((), {})
    assert args[1].endswith("/editMessageText")
    assert json.loads(kwargs["body"])["reply_markup"] == {"inline_keyboard": []}
    FakeConnection.response = FakeResponse(
        200,
        {
            "ok": True,
            "result": {
                "message_id": 71,
                "chat": {"id": -5319791765},
                "text": completed_text,
                "reply_markup": {"inline_keyboard": [[{"text": "✅ Выполнено"}]]},
            },
        },
    )
    with pytest.raises(DispatchUncertain):
        gateway.edit_handoff_card("-5319791765", 71, completed_text)


def test_gateway_asks_for_completion_description_with_force_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = token_file(tmp_path)
    monkeypatch.setattr("http.client.HTTPSConnection", FakeConnection)
    FakeConnection.response = FakeResponse(
        200, {"ok": True, "result": {"message_id": 704, "chat": {"id": -5319791765}}}
    )

    receipt = TelegramBotGateway(TelegramGatewayConfig(path)).send_completion_prompt(
        "-5319791765", 701
    )

    assert receipt.message_id == "704"
    args, kwargs = FakeConnection.request_args or ((), {})
    assert args[1].endswith("/sendMessage")
    request_body = json.loads(kwargs["body"])
    assert request_body["text"] == "Что было сделано?"
    assert request_body["reply_parameters"]["message_id"] == 701
    assert request_body["reply_markup"] == {
        "force_reply": True,
        "input_field_placeholder": "Опишите выполненные работы",
    }

    gateway = TelegramBotGateway(TelegramGatewayConfig(path))
    gateway.send_completion_prompt("-5319791765", 702, username="installer_1")
    args, kwargs = FakeConnection.request_args or ((), {})
    addressed = json.loads(kwargs["body"])
    assert addressed["text"] == "Что было сделано?\n@installer_1"
    assert addressed["reply_parameters"]["message_id"] == 702
    assert addressed["reply_markup"]["selective"] is True


def test_gateway_deletes_only_requested_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = token_file(tmp_path)
    monkeypatch.setattr("http.client.HTTPSConnection", FakeConnection)
    FakeConnection.response = FakeResponse(200, {"ok": True, "result": True})
    TelegramBotGateway(TelegramGatewayConfig(path)).delete_message("-5319791765", 704)
    args, kwargs = FakeConnection.request_args or ((), {})
    assert args[1].endswith("/deleteMessage")
    assert json.loads(kwargs["body"]) == {"chat_id": "-5319791765", "message_id": 704}

    FakeConnection.response = FakeResponse(
        400,
        {"ok": False, "error_code": 400, "description": "Bad Request: message to delete not found"},
    )
    with pytest.raises(DispatchRejected, match="telegram_message_not_found"):
        TelegramBotGateway(TelegramGatewayConfig(path)).delete_message("-5319791765", 704)

    FakeConnection.response = FakeResponse(
        400, {"ok": False, "error_code": 400, "description": "Bad Request: message is not modified"}
    )
    with pytest.raises(DispatchRejected, match="telegram_message_not_modified"):
        TelegramBotGateway(TelegramGatewayConfig(path)).edit_handoff_card(
            "-5319791765", 701, "✅ Выполнено"
        )


def test_gateway_asks_reschedule_only_for_confirmed_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = token_file(tmp_path)
    monkeypatch.setattr("http.client.HTTPSConnection", FakeConnection)
    FakeConnection.response = FakeResponse(
        200, {"ok": True, "result": {"message_id": 704, "chat": {"id": -5319791765}}}
    )
    receipt = TelegramBotGateway(TelegramGatewayConfig(path)).send_reschedule_prompt(
        "-5319791765", 701, username="installer_1"
    )
    assert receipt.message_id == "704"
    args, kwargs = FakeConnection.request_args or ((), {})
    assert args[1].endswith("/sendMessage")
    body = json.loads(kwargs["body"])
    assert body["reply_parameters"]["message_id"] == 701
    assert body["reply_markup"]["force_reply"] is True
    assert body["reply_markup"]["selective"] is True
    assert "Дата: не согласована" in body["text"]


@pytest.mark.integration
def test_real_lab_gateway_confirms_synthetic_message() -> None:
    if os.environ.get("RUN_TELEGRAM_LAB_TEST") != "1":
        pytest.skip("explicit Telegram LAB test is not enabled")
    token_path = os.environ.get("TELEGRAM_BOT_TOKEN_FILE")
    if not token_path:
        pytest.fail("Telegram LAB token file is not configured")
    receipt = TelegramBotGateway(TelegramGatewayConfig(Path(token_path))).send_message(
        "-5319791765",
        "OSS Control Center · проверка Telegram T1\n"
        "Синтетическая заявка, персональные данные отсутствуют.",
    )
    assert receipt.chat_id == "-5319791765"
    assert int(receipt.message_id) > 0
