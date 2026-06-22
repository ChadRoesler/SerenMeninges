"""
seren_meninges.auth
========================================================================

The bearer-auth middleware every service mounts. Security-sensitive AND
identical across the three - exactly the thing you want ONE copy of, not
three subtly-different ones. Centralizing the constant-time compare and the
public-paths policy here means a fix lands everywhere at once.

POLICY
  - An EMPTY configured token => no auth. Open service, trusted-LAN default.
    (The service still mounts the middleware; it just no-ops.)
  - public_paths bypass auth so health checks and the viewer shell load
    without a token: {"/", "/health", "/viewer"} by default.
  - Everything else needs ``Authorization: Bearer <token>`` and is compared
    with hmac.compare_digest (constant-time - no early-exit timing leak).

DEP NOTE (a real choice for the build pass): this skeleton uses Starlette's
BaseHTTPMiddleware for readability. Starlette is already in every leaf's tree
via FastAPI, so depending on it adds nothing to the actual install. The
alternative is a pure-ASGI class (scope/receive/send) to keep Meninges
formally dep-free. Pick one when wiring; the POLICY above is what matters and
doesn't change either way.

=== SKELETON === logic is real; confirm the import surface against the leaves'
FastAPI version and add tests before release.
"""
from __future__ import annotations

import hmac
from typing import Iterable, Optional


DEFAULT_PUBLIC_PATHS = frozenset({"/", "/health", "/viewer"})


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
            # Constant-time compare; both args str -> compare as utf-8 bytes.
            if not presented or not hmac.compare_digest(presented, expected):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)

            return await call_next(request)

    return _BearerAuth
