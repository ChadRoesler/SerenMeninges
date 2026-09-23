"""
The auth middleware covers EVERY scope a client can open, not just HTTP.

The first version subclassed Starlette's BaseHTTPMiddleware, which forwards
`websocket` scopes untouched - so the day a leaf added a socket route it
would have shipped unauthenticated while every HTTP route still 401'd, and
nothing would have looked broken. These pin the pure-ASGI rewrite: a socket
to a private path is closed with 1008 before it is accepted, a socket with
the bearer works, and an empty token still means "open" for sockets too.
Plus the two small HTTP-side promises the rewrite added: the 401 names its
scheme, and lifespan passes through.
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from seren_meninges.auth import (
    WEBSOCKET_POLICY_VIOLATION, bearer_auth_middleware,
)

TOKEN = "sekret"


async def _echo(request):
    return JSONResponse({"where": request.url.path})


async def _socket(ws):
    await ws.accept()
    await ws.send_text("hello")
    await ws.close()


def build(token=TOKEN, public_paths=None):
    return TestClient(
        Starlette(
            routes=[
                Route("/private", _echo),
                WebSocketRoute("/ws", _socket),
                WebSocketRoute("/viewer/live", _socket),
            ],
            middleware=[Middleware(bearer_auth_middleware(token, public_paths=public_paths))],
        ),
        raise_server_exceptions=False,
    )


def test_a_websocket_without_a_token_is_refused_before_accept():
    with pytest.raises(WebSocketDisconnect) as exc:
        with build().websocket_connect("/ws"):
            pass
    assert exc.value.code == WEBSOCKET_POLICY_VIOLATION


def test_a_websocket_with_the_wrong_token_is_refused():
    with pytest.raises(WebSocketDisconnect):
        with build().websocket_connect("/ws", headers={"Authorization": "Bearer nope"}):
            pass


def test_a_websocket_with_the_token_is_accepted():
    with build().websocket_connect("/ws", headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
        assert ws.receive_text() == "hello"


def test_a_public_subtree_socket_needs_no_token():
    """The viewer subtree is public for HTTP; the same rule applies to a
    socket under it, or the shell could not open a live feed."""
    with build().websocket_connect("/viewer/live") as ws:
        assert ws.receive_text() == "hello"


def test_an_empty_token_leaves_sockets_open_too():
    with build(token="").websocket_connect("/ws") as ws:
        assert ws.receive_text() == "hello"


def test_the_401_names_its_scheme():
    r = build().get("/private")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"
    assert r.json() == {"detail": "unauthorized"}   # the family shape, unchanged


def test_lifespan_is_not_ours_to_refuse():
    """The app must start with a token configured. A middleware that 401'd
    the lifespan scope would be a service that cannot boot."""
    with build() as client:
        assert client.get("/private", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
