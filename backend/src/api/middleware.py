"""Security headers on every API response, and a 500 that browsers can read.

Written as plain ASGI middleware (not @app.middleware("http")), so streaming responses
like the SSE endpoint pass through untouched instead of being buffered.
"""

import json
import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

SECURITY_HEADERS: dict[bytes, bytes] = {
    b"x-content-type-options": b"nosniff",            # don't guess content types
    b"x-frame-options": b"DENY",                      # the API is never framed
    b"referrer-policy": b"no-referrer",
    b"content-security-policy": b"default-src 'none'; frame-ancestors 'none'",  # JSON/SSE only
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {name.lower() for name, _ in message.get("headers", [])}
                extra = [(name, value) for name, value in SECURITY_HEADERS.items() if name not in existing]
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        await self.app(scope, receive, send_with_headers)


class UnhandledErrorMiddleware:
    """Turn an unexpected exception into a JSON 500 *inside* the CORS middleware.

    Starlette builds its own 500 in the outermost layer, outside CORSMiddleware, so that
    response has no Access-Control-Allow-Origin header. The browser then hides it and
    reports a misleading "blocked by CORS policy" error. Added before CORSMiddleware
    (so it runs inside it), this middleware's 500 gets the CORS headers like any response.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def track_start(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, track_start)
        except Exception:
            logger.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if response_started:
                raise  # headers already sent (e.g. mid-stream); nothing better to do
            body = json.dumps({"detail": "Internal server error. Please try again later."}).encode()
            await send({"type": "http.response.start", "status": 500,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
