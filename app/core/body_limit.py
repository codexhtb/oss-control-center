"""Small request size cap for the account lookup JSON endpoint."""

from app.core.errors import error_response

MAX_LOOKUP_BODY_BYTES = 2048


class LookupBodyLimit:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/v1/subscriber-lookups"
        ):
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > MAX_LOOKUP_BODY_BYTES:
            await error_response(413, "request_too_large", "Request body is too large")(
                scope, receive, send
            )
            return
        received = 0
        buffered = []
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.disconnect":
                return
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_LOOKUP_BODY_BYTES:
                    await error_response(413, "request_too_large", "Request body is too large")(
                        scope, receive, send
                    )
                    return
                if not message.get("more_body", False):
                    break

        async def replay_receive():
            if buffered:
                return buffered.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)
