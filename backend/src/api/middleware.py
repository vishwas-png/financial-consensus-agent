"""Security headers on every API response.

Written as plain ASGI middleware (not @app.middleware("http")), so streaming responses
like the SSE endpoint pass through untouched instead of being buffered.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
