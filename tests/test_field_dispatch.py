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

    receipt = gateway.send_message("-5319791765", "Synthetic field work")

    assert receipt.chat_id == "-5319791765"
    assert receipt.message_id == "71"
    args, kwargs = FakeConnection.request_args or ((), {})
    assert args[0] == "POST"
    assert json.loads(kwargs["body"])["chat_id"] == "-5319791765"
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
