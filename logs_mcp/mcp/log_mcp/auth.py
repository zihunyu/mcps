"""Bearer-token protection for Log MCP HTTP transports."""

from __future__ import annotations

from hmac import compare_digest

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerTokenMiddleware:
    """Require Authorization: Bearer <token> for HTTP requests."""

    def __init__(self, app: ASGIApp, token: str, exempt_path_prefixes: tuple[str, ...] = ()) -> None:
        self._app = app
        self._token = token
        self._exempt_path_prefixes = exempt_path_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self._exempt_path_prefixes):
            await self._app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        authorization = headers.get("authorization", "")
        expected = f"Bearer {self._token}"
        if not compare_digest(authorization, expected):
            response = JSONResponse(
                {"detail": "invalid MCP bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


def protect_http_app(
    app: ASGIApp,
    token: str | None,
    exempt_path_prefixes: tuple[str, ...] = ("/downloads/",),
) -> ASGIApp:
    """Wrap an ASGI app with bearer-token protection when token is set."""

    if not token:
        return app
    return BearerTokenMiddleware(app, token, exempt_path_prefixes=exempt_path_prefixes)
