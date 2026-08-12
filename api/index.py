"""Vercel entrypoint.

Vercel's Python runtime serves the ASGI application exported from this module as
`app`. All routing is FastAPI's own.

Why the wrapper exists — verified against the deployed function, not assumed:

`vercel.json` rewrites every path to this function. A destination of `/api/index`
*replaces* the request path, so the app received `/api/index` for every request and
matched nothing, including FastAPI's own `/openapi.json`. No `x-vercel-*` header
carries the original path, so it cannot be recovered after the fact. Using
`/api/index/$1` instead preserves it as a suffix: a request for `/health` arrives as
`/api/index/health`.

So the prefix is stripped here, in the one file that exists because of Vercel, and
`src/api/main.py` declares its routes at their real paths. If `vercel.json`'s
destination changes, `_PREFIX` must change with it — `tests/test_health.py` covers
that pairing.
"""

from typing import Any, Awaitable, Callable

from src.api.main import app as _fastapi_app

_PREFIX = "/api/index"

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Strip the Vercel rewrite prefix, then hand off to FastAPI.

    Only `http` and `websocket` scopes carry a path; `lifespan` passes through
    untouched. A path of exactly the prefix becomes `/` rather than an empty string,
    which ASGI does not permit.
    """
    if scope["type"] in ("http", "websocket"):
        path: str = scope.get("path", "")
        if path == _PREFIX or path.startswith(_PREFIX + "/"):
            stripped = path[len(_PREFIX) :] or "/"
            scope = {**scope, "path": stripped, "raw_path": stripped.encode()}
    await _fastapi_app(scope, receive, send)
