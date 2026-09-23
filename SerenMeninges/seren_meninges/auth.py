"""
seren_meninges.auth
========================================================================

The bearer-auth middleware every service mounts. Security-sensitive AND
identical across the family - exactly the thing you want ONE copy of, not
six subtly-different ones. Centralizing the constant-time compare and the
public-paths policy here means a fix lands everywhere at once.

POLICY
  - An EMPTY configured token => no auth. Open service, loopback default.
    (The service still mounts the middleware; it just no-ops.)
  - public_paths bypass auth so health checks and the viewer shell load
    without a token: {"/", "/health", "/viewer"} by default. Matching is
    EXACT, plus a subtree rule so "/viewer/ui/app.css" loads with the shell.
    "/" is excluded from the subtree rule for the obvious reason.
  - Everything else needs ``Authorization: Bearer <token>`` and is compared
    with hmac.compare_digest (constant-time - no early-exit timing leak).
  - A 401 carries ``WWW-Authenticate: Bearer`` so a client that speaks HTTP
    knows what it was refused for, and the body is ``{"detail":
    "unauthorized"}`` because every leaf asserts that shape.

PURE ASGI, AND WHY THAT IS NOT A STYLE CHOICE. The first version subclassed
Starlette's BaseHTTPMiddleware for readability. BaseHTTPMiddleware only ever
sees the ``http`` scope - it forwards ``websocket`` and everything else
untouched - so a leaf that added a socket route would have shipped it with no
auth at all, and nothing would have looked broken, because the HTTP half of
the service still 401'd correctly. No leaf has a socket today; this is the
shared auth for a voice-and-chat companion stack, so the day one does is not
far off. The middleware now handles both scopes explicitly: a websocket to a
non-public path without a valid bearer is closed with 1008 (policy violation)
before it is ever accepted. Lifespan and any scope type this code does not
know pass straight through - refusing them would break startup, not protect
anything.

Starlette is still used for ``HTTPConnection`` (one path/header parser for
both scope types) and ``JSONResponse``. It is already in every leaf via
FastAPI, so the dependency costs nothing at install.

Covered by tests/test_auth.py - policy-level, not implementation-level.
"""
from __future__ import annotations

import hmac
from typing import Iterable, Optional


DEFAULT_PUBLIC_PATHS = frozenset({"/", "/health", "/viewer"})

#: The close code a websocket gets when it presents no valid bearer. 1008 is
#: "policy violation" in RFC 6455 - the honest one; 1002 (protocol error) and
#: 1011 (server error) would both be lies.
WEBSOCKET_POLICY_VIOLATION = 1008


def _tokens_match(presented: str, expected: str) -> bool:
    """Constant-time token compare, done on BYTES.

    THE BYTES MATTER, and this is not a style preference.
    ``hmac.compare_digest`` accepts str only when BOTH sides are pure ASCII;
    hand it anything else and it raises TypeError. Starlette decodes request
    headers as latin-1, so a single high byte in an Authorization header used
    to turn a clean 401 into an unhandled exception - an unauthenticated,
    remote 500 in the one middleware every service in the family mounts. Not
    an auth bypass, but a crash anyone could trigger from off-box.

    Encoding the presented value back to latin-1 recovers the exact bytes that
    arrived on the wire. The configured token is encoded utf-8, which is what
    a client sending a non-ASCII token would have put on the wire in the first
    place - so a genuine unicode token still authenticates, and garbage gets a
    clean 401 instead of a stack trace.
    """
    try:
        presented_bytes = presented.encode("latin-1")
    except UnicodeEncodeError:
        # Unreachable from a real HTTP header (latin-1 by spec), but a direct
        # caller can pass anything and shouldn't be able to raise in here.
        presented_bytes = presented.encode("utf-8")
    return hmac.compare_digest(presented_bytes, expected.encode("utf-8"))


def _is_public(path: str, public: frozenset) -> bool:
    return path in public or any(path.startswith(p + "/") for p in public if p != "/")


def _presented_token(authorization: str) -> str:
    return authorization[7:] if authorization.lower().startswith("bearer ") else ""


def bearer_auth_middleware(
    token: str,
    *,
    public_paths: Optional[Iterable[str]] = None,
):
    """Build an ASGI middleware enforcing Bearer auth except on public_paths.
    Returns a class you hand to ``app.add_middleware(...)``.

    Usage in a leaf::

        from seren_meninges.auth import bearer_auth_middleware
        tok = cfg.server.resolve_bearer()
        app.add_middleware(bearer_auth_middleware(tok))

    To publish extra routes, EXTEND the default rather than replacing it::

        app.add_middleware(bearer_auth_middleware(
            tok, public_paths=DEFAULT_PUBLIC_PATHS | {"/api/v1/system/ping"},
        ))

    Covers ``http`` AND ``websocket`` scopes. Everything else (lifespan) passes
    through untouched.
    """
    from starlette.requests import HTTPConnection
    from starlette.responses import JSONResponse

    public = frozenset(public_paths) if public_paths is not None else DEFAULT_PUBLIC_PATHS
    expected = token or ""

    class _BearerAuth:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            kind = scope.get("type")
            # No token configured => auth disabled entirely. Not-HTTP-ish
            # scopes (lifespan) are never ours to refuse.
            if not expected or kind not in ("http", "websocket"):
                await self.app(scope, receive, send)
                return

            conn = HTTPConnection(scope)
            if _is_public(conn.url.path, public):
                await self.app(scope, receive, send)
                return

            presented = _presented_token(conn.headers.get("authorization", ""))
            if presented and _tokens_match(presented, expected):
                await self.app(scope, receive, send)
                return

            if kind == "websocket":
                # The client's `websocket.connect` is the first message on the
                # channel; take it, then close without ever accepting. The
                # app underneath never sees the socket.
                await receive()
                await send({"type": "websocket.close",
                            "code": WEBSOCKET_POLICY_VIOLATION})
                return

            response = JSONResponse({"detail": "unauthorized"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
            await response(scope, receive, send)

    return _BearerAuth
