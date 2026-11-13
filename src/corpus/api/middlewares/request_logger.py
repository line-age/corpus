"""Logs every HTTP request with its status and how long it took, in milliseconds. The
message reads `GET /healthz 200 2.4ms`; the same values are also separate JSON fields
(method, path, status, duration_ms) for filtering.

Only the path is logged, not the query string, which can carry values that don't belong in
logs. 5xx responses are logged at ERROR, everything else at INFO. Uvicorn's own access log
would repeat each line without the timing, so the Dockerfile turns it off.
"""

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from corpus.lib.logger import logger


class RequestLoggerMiddleware:
    """Plain ASGI middleware rather than BaseHTTPMiddleware: it doesn't buffer responses,
    and the time it logs covers the whole response, including a streamed body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500  # what the client gets if the app raises before responding

        async def send_and_record_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_and_record_status)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            method, path = scope["method"], scope["path"]
            logger.log(
                logging.ERROR if status_code >= 500 else logging.INFO,
                "%s %s %d %.1fms",
                method,
                path,
                status_code,
                duration_ms,
                extra={
                    "method": method,
                    "path": path,
                    "status": status_code,
                    "duration_ms": duration_ms,
                },
            )
