"""Exercise the isolated HTTPS dev stack without disclosing credentials or cookies."""

import http.cookiejar
import json
import re
import secrets
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ORIGIN = "https://172.17.7.2:8443"
BASE = Path(__file__).resolve().parents[1]


def client() -> tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
    jar = http.cookiejar.CookieJar()
    context = ssl.create_default_context(cafile=str(BASE / ".dev-secrets/certs/ca.crt"))
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=context),
    )
    return opener, jar


def request(
    opener: urllib.request.OpenerDirector,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, str] | None = None,
    origin: str | None = None,
    csrf: str | None = None,
) -> tuple[int, dict | None]:
    headers = {"Accept": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(ORIGIN + path, data=body, method=method, headers=headers)
    try:
        response = opener.open(req, timeout=10)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        content_type = response.headers.get("Content-Type", "")
        data = json.load(response) if "application/json" in content_type else None
        return response.status, data


def expect(actual: int, expected: int, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: HTTP {actual}, expected {expected}")
    print(f"{label}: HTTP {actual}")


def main() -> None:
    credentials = json.loads((BASE / ".dev-secrets/users.json").read_text(encoding="utf-8"))
    anonymous, _ = client()
    expect(request(anonymous, "/api/v1/health/live")[0], 200, "liveness")
    expect(request(anonymous, "/api/v1/health/ready")[0], 200, "readiness")
    expect(request(anonymous, "/api/v1/auth/me")[0], 401, "anonymous session")
    with anonymous.open(ORIGIN + "/", timeout=10) as response:
        page = response.read(200_000).decode("utf-8")
        assert response.status == 200 and "OSS Control Center" in page
    print("web entrypoint: HTTP 200")
    for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"', page):
        with anonymous.open(ORIGIN + asset, timeout=10) as response:
            assert response.status == 200
            assert "text/html" not in response.headers.get("Content-Type", "")
        print("web asset: HTTP 200")

    roles = (
        ("dev-operator", "operator"),
        ("dev-engineer", "engineer"),
        ("dev-admin", "admin"),
    )
    for login, role in roles:
        opener, jar = client()
        status, login_body = request(
            opener,
            "/api/v1/auth/login",
            method="POST",
            payload={"login": login, "password": credentials[login]},
            origin=ORIGIN,
        )
        expect(status, 200, f"{role} login")
        assert login_body and login_body["user"]["role"] == role
        cookies = {cookie.name: cookie for cookie in jar}
        assert cookies["oss_session"].secure
        assert cookies["oss_session"].has_nonstandard_attr("HttpOnly")
        assert cookies["oss_session"].get_nonstandard_attr("SameSite") == "strict"
        assert cookies["oss_session"].path == "/api/v1"
        assert cookies["oss_csrf"].secure
        assert not cookies["oss_csrf"].has_nonstandard_attr("HttpOnly")
        csrf = cookies["oss_csrf"].value
        status, me = request(opener, "/api/v1/auth/me")
        expect(status, 200, f"{role} session")
        assert me and me["user"]["role"] == role
        ticket_status = request(opener, "/api/v1/tickets")[0]
        expect(ticket_status, 403 if role == "admin" else 200, f"{role} tickets")
        logout_path = "/api/v1/auth/logout"
        rejected_csrf = request(opener, logout_path, method="POST", origin=ORIGIN)[0]
        expect(rejected_csrf, 403, f"{role} CSRF rejection")
        rejected_origin = request(
            opener, logout_path, method="POST", origin="https://invalid.example", csrf=csrf
        )[0]
        expect(rejected_origin, 403, f"{role} Origin rejection")
        accepted_logout = request(opener, logout_path, method="POST", origin=ORIGIN, csrf=csrf)[0]
        expect(accepted_logout, 200, f"{role} logout")
        expect(request(opener, "/api/v1/auth/me")[0], 401, f"{role} revoked session")

    if "--skip-rate" not in sys.argv[1:]:
        rate_client, _ = client()
        rate_login = f"synthetic-rate-{secrets.token_hex(4)}"
        for attempt in range(1, 7):
            status, _ = request(
                rate_client,
                "/api/v1/auth/login",
                method="POST",
                payload={"login": rate_login, "password": "invalid-synthetic-password"},
                origin=ORIGIN,
            )
            expect(status, 429 if attempt == 6 else 401, f"rate limit attempt {attempt}")


if __name__ == "__main__":
    main()
