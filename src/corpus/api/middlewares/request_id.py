"""Gives every HTTP request an ID: the caller's X-Request-ID header if it is well formed,
otherwise a new UUID4. While the request is handled, every corpus log record carries the
ID as `request_id`. The ID is also available to handlers as `request.state.request_id`
and is returned to the caller in the X-Request-ID response header.

The header comes from the client, so only short IDs of letters, digits and `._:-` are
accepted. Anything else is replaced with a new ID rather than logged, so a caller can't
inject text into the logs or send oversized values.

Register this middleware after RequestLoggerMiddleware, so it runs outside it and the
request log line carries the ID too.
"""

import re
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from corpus.lib.logger import request_id_var

HEADER = "x-request-id"
_HEADER_BYTES = HEADER.encode()
_VALID_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class RequestIdMiddleware:
    """Plain ASGI middleware, like RequestLoggerMiddleware: it doesn't buffer responses, and
    it sets the ID in the same task that runs the request, so code downstream sees it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_id(scope) or str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[HEADER] = request_id
            await send(message)

        token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_var.reset(token)


def _incoming_id(scope: Scope) -> str | None:
    """The caller's X-Request-ID, or None if it is missing or not a valid ID."""
    for name, value in scope["headers"]:  # ASGI header names are lowercase bytes
        if name == _HEADER_BYTES:
            candidate = value.decode("latin-1")
            return candidate if _VALID_ID.fullmatch(candidate) else None
    return None
