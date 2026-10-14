"""Bounded, read-only HTTP transport for monitoring sources."""

import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


class SourceUnavailable(Exception):
    """A monitoring result could not be obtained or safely interpreted."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class JsonTransport:
    def __init__(self, base_url: str, headers: dict[str, str], timeout: float, ca_file: str | None):
        self.base_url = base_url
        self.headers = headers
        self.timeout = timeout
        context = ssl.create_default_context(cafile=ca_file)
        self.opener = build_opener(ProxyHandler({}), _NoRedirect, HTTPSHandler(context=context))

    def get(self, path: str, params: dict[str, str | list[str]] | None = None) -> dict:
        if not path.startswith("/") or ".." in path or "?" in path:
            raise ValueError("invalid monitoring API path")
        query = "?" + urlencode(params, doseq=True) if params else ""
        request = Request(
            self.base_url + path + query,
            headers={**self.headers, "Accept": "application/json"},
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise SourceUnavailable("unexpected_http_status")
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise SourceUnavailable("response_too_large")
        except (HTTPError, URLError, TimeoutError, OSError):
            # Never propagate source URLs, headers, credentials or response bodies.
            raise SourceUnavailable("monitoring_http_unavailable") from None
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise SourceUnavailable("invalid_json") from None
        if not isinstance(result, dict):
            raise SourceUnavailable("invalid_response")
        return result
