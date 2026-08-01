"""
seren_meninges.auth
========================================================================

The bearer-auth middleware every service mounts. Security-sensitive AND
identical across the family - exactly the thing you want ONE copy of, not
six subtly-different ones. Centralizing the constant-time compare and the
public-paths policy here means a fix lands everywhere at once.

POLICY
  - An EMPTY configured token => no auth. Open service, trusted-LAN default.
    (The service still mounts the middleware; it just no-ops.)
  - public_paths bypass auth so health checks and the viewer shell load
    without a token: {"/", "/health", "/viewer"} by default. Matching is
    EXACT, plus a subtree rule so "/viewer/ui/app.css" loads with the shell.
    "/" is excluded from the subtree rule for the obvious reason.
  - Everything else needs ``Authorization: Bearer <token>`` and is compared
    with hmac.compare_digest (constant-time - no early-exit timing leak).

DEP NOTE (a real choice for the build pass): this uses Starlette's
BaseHTTPMiddleware for readability. Starlette is already in every leaf's tree
via FastAPI, so depending on it adds nothing to the actual install. The
alternative is a pure-ASGI class (scope/receive/send) to keep Meninges
formally dep-free. Pick one when wiring; the POLICY above is what matters and
doesn't change either way.

Covered by tests/test_auth.py - policy-level, not implementation-level.
"""
from __future__ import annotations

import hmac
from typing import Iterable, Optional


DEFAULT_PUBLIC_PATHS = frozenset({"/", "/health", "/viewer"})


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


def bearer_auth_middleware(
    token: str,
    *,
    public_paths: Optional[Iterable[str]] = None,
):
    """Build a Starlette middleware enforcing Bearer auth except on
    public_paths. Returns a class you hand to ``app.add_middleware(...)``.

    Usage in a leaf::

        from seren_meninges.auth import bearer_auth_middleware
        tok = cfg.server.resolve_bearer()
        app.add_middleware(bearer_auth_middleware(tok))

    To publish extra routes, EXTEND the default rather than replacing it::

        app.add_middleware(bearer_auth_middleware(
            tok, public_paths=DEFAULT_PUBLIC_PATHS | {"/api/v1/system/ping"},
        ))
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    public = frozenset(public_paths) if public_paths is not None else DEFAULT_PUBLIC_PATHS
    expected = token or ""

    class _BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            # No token configured => auth disabled entirely.
            if not expected:
                return await call_next(request)

            path = request.url.path
            # Allow the viewer subtree + public paths through untouched.
            if path in public or any(path.startswith(p + "/") for p in public if p != "/"):
                return await call_next(request)

            auth = request.headers.get("authorization", "")
            presented = auth[7:] if auth.lower().startswith("bearer ") else ""
            if not presented or not _tokens_match(presented, expected):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)

            return await call_next(request)

    return _BearerAuth
