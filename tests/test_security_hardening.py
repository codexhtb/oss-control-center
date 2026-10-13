"""Production transport, private HTTP caching and actual server access logs."""

import os
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.modules.billing.netup.config import NetupConfig

TEST_SETTINGS = {
    "APP_ENV": "test",
    "BILLING_PROVIDER": "MOCK",
    "POSTGRES_HOST": "postgres",
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "oss_test_config_only",
    "POSTGRES_USER": "synthetic",
    "POSTGRES_PASSWORD": "synthetic-only",
    "READINESS_TIMEOUT_SECONDS": "2",
    "AUTH_PUBLIC_ORIGIN": "https://oss.example.test",
    "AUTH_ALLOW_HTTP_LOOPBACK": "false",
    "AUTH_HASH_KEY": "synthetic-test-key-with-at-least-32-bytes",
}


@pytest.mark.parametrize("purpose", ["lab", "production"])
def test_production_cannot_enable_plaintext_lab(purpose: str) -> None:
    with pytest.raises(ValueError):
        NetupConfig.model_validate(
            {
                "app_env": "production",
                "purpose": purpose,
                "host": "172.17.7.1",
                "user": "synthetic_ro",
                "password": "synthetic-only",
                "allow_plaintext_lab": True,
            }
        )


def _test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in TEST_SETTINGS.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def _startup_reason(output: str, return_code: int | None) -> str:
    """Classify stderr without copying configuration, credentials or request data."""
    if "AUTH_ALLOW_HTTP_LOOPBACK is dev-only" in output:
        return "invalid HTTP loopback setting for test environment"
    if "ValidationError" in output:
        return "application configuration validation failed"
    if "Address already in use" in output:
        return "local test port was already in use"
    if "ModuleNotFoundError" in output:
        return "HTTP server module could not be imported"
    if return_code is None:
        return "startup timed out"
    return f"HTTP server exited with code {return_code}"


def test_private_api_errors_are_not_cacheable(monkeypatch: pytest.MonkeyPatch) -> None:
    _test_environment(monkeypatch)
    try:
        with TestClient(create_app(), base_url="https://oss.example.test") as client:
            denied = client.get("/api/v1/tickets")
            assert denied.status_code == 401
            assert denied.headers["Cache-Control"] == "no-store"
            invalid = client.get("/api/v1/subscriber-lookups/not-a-uuid")
            assert invalid.headers["Cache-Control"] == "no-store"
            assert "no-store" not in client.get("/api/v1/health/live").headers.get(
                "Cache-Control", ""
            )
    finally:
        get_settings.cache_clear()


def test_real_uvicorn_access_log_omits_query_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _test_environment(monkeypatch)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    marker = "SYNTHETIC-ACCOUNT-DO-NOT-LOG-7319"
    env = {key: os.environ[key] for key in ("PATH", "PYTHONPATH") if key in os.environ}
    env.update(TEST_SETTINGS)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-proxy-headers",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    startup_failed = False
    try:
        until = time.monotonic() + 8
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/v1/health/live", timeout=1):
                    break
            except (URLError, HTTPError):
                if time.monotonic() >= until or process.poll() is not None:
                    startup_failed = True
                    break
                time.sleep(0.1)
        if not startup_failed:
            try:
                urlopen(
                    f"http://127.0.0.1:{port}/api/v1/tickets?account_no={marker}",
                    timeout=2,
                )
            except HTTPError as exc:
                assert exc.code == 401
    finally:
        return_code = process.poll()
        if return_code is None:
            process.terminate()
        try:
            output, _ = process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=8)
        get_settings.cache_clear()
    if startup_failed:
        raise AssertionError(
            f"Synthetic HTTP server did not start: {_startup_reason(output, return_code)}"
        )
    assert marker not in output
    assert "GET /api/v1/tickets HTTP/1.1" in output
    get_settings.cache_clear()
