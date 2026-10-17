"""Transport-level checks for outbound Telegram dispatch."""

import json
import os
from pathlib import Path

import pytest

from app.modules.field_dispatch.gateway import (
    DispatchRejected,
    DispatchUncertain,
    TelegramBotGateway,
    TelegramGatewayConfig,
)
from app.modules.field_dispatch.inbound import SenderRegistryError, TelegramSenderRegistry


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


def test_gateway_accepts_only_lab_chat_and_requires_confirmed_receipt(
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
    with pytest.raises(DispatchRejected, match="telegram_chat_not_allowed"):
        gateway.send_message("-1", "must not be sent")


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


def test_sender_registry_requires_protected_numeric_allowlist(tmp_path: Path) -> None:
    path = tmp_path / "allowed-senders.json"
    path.write_text('{"allowed_user_ids":[501,502]}', encoding="utf-8")
    path.chmod(0o600)
    assert TelegramSenderRegistry(path).allowed_ids() == frozenset({501, 502})
    path.chmod(0o644)
    with pytest.raises(SenderRegistryError, match="telegram_sender_registry_permissions"):
        TelegramSenderRegistry(path).allowed_ids()


def test_gateway_acknowledges_callback_and_disables_completion_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = token_file(tmp_path)
    monkeypatch.setattr("http.client.HTTPSConnection", FakeConnection)
    gateway = TelegramBotGateway(TelegramGatewayConfig(path))
    FakeConnection.response = FakeResponse(200, {"ok": True, "result": True})
    gateway.acknowledge_callback("synthetic-callback", "Сохранено")
    args, _ = FakeConnection.request_args or ((), {})
    assert args[1].endswith("/answerCallbackQuery")

    FakeConnection.response = FakeResponse(200, {"ok": True, "result": {"message_id": 71}})
    gateway.mark_message_completed("-5319791765", 71)
    args, kwargs = FakeConnection.request_args or ((), {})
    assert args[1].endswith("/editMessageReplyMarkup")
    assert json.loads(kwargs["body"])["reply_markup"] == {
        "inline_keyboard": [[{"text": "✅ Выполнено", "disabled": True}]]
    }


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
