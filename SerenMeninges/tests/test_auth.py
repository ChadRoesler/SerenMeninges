"""
Tests for seren_meninges.auth.bearer_auth_middleware.

This is the security control every service in the family mounts, so the tests
are about POLICY, not implementation: who gets through, who gets 401, and what
a 401 looks like on the wire (leaves assert the {"detail": ...} shape).

Constant-time comparison is not asserted directly - a timing test would be
flaky and prove little. What IS asserted is that the compare happens on bytes,
because the str form of hmac.compare_digest raises on non-ASCII input.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from seren_meninges.auth import DEFAULT_PUBLIC_PATHS, bearer_auth_middleware

TOKEN = "sekret"


def build(token=TOKEN, public_paths=None):
    app = FastAPI()
    app.add_middleware(bearer_auth_middleware(token, public_paths=public_paths))

    @app.get("/")
    async def root(): return {"where": "root"}

    @app.get("/health")
    async def health(): return {"ok": True}

    @app.get("/viewer")
    async def viewer(): return {"where": "viewer"}

    @app.get("/viewer/ui/app.js")
    async def viewer_asset(): return {"where": "asset"}

    @app.get("/private")
    async def private(): return {"where": "private"}

    @app.get("/healthy")
    async def healthy(): return {"where": "healthy-is-not-health"}

    @app.get("/api/v1/system/ping")
    async def ping(): return {"ok": True}

    # raise_server_exceptions=False so an unhandled error surfaces as a 500 we
    # can assert on, instead of exploding the test.
    return TestClient(app, raise_server_exceptions=False)


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


# ── no token configured => auth is off ─────────────────────────────────

@pytest.mark.parametrize("path", ["/", "/health", "/viewer", "/private"])
def test_empty_token_disables_auth_entirely(path):
    """Trusted-LAN default. The middleware still mounts; it just no-ops."""
    assert build(token="").get(path).status_code == 200


def test_none_token_is_treated_as_empty():
    assert build(token=None).get("/private").status_code == 200


# ── the public allowlist ───────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/", "/health", "/viewer"])
def test_default_public_paths_need_no_token(path):
    assert build().get(path).status_code == 200


def test_viewer_subtree_is_public_so_assets_load():
    """The shell is public, so the css/js it pulls must be too, or the page
    renders naked for anyone without a token in their browser."""
    assert build().get("/viewer/ui/app.js").status_code == 200


def test_public_paths_are_exact_not_prefixes():
    """`/healthy` must NOT inherit `/health`'s exemption. A substring match
    here would quietly publish any route sharing a prefix with a public one."""
    assert build().get("/healthy").status_code == 401


def test_root_being_public_does_not_make_everything_public():
    """"/" is in the allowlist and every path starts with "/" - the prefix rule
    skips it explicitly. If that guard is ever dropped, this fails loudly."""
    assert build().get("/private").status_code == 401


def test_custom_public_paths_replace_the_default():
    c = build(public_paths={"/open"})
    assert c.get("/health").status_code == 401     # no longer exempt
    assert c.get("/private").status_code == 401


def test_extending_the_default_is_the_documented_pattern():
    """What the leaves actually do: DEFAULT_PUBLIC_PATHS | {extra routes}."""
    c = build(public_paths=DEFAULT_PUBLIC_PATHS | {"/api/v1/system/ping"})
    assert c.get("/api/v1/system/ping").status_code == 200
    assert c.get("/health").status_code == 200      # default still honoured
    assert c.get("/private").status_code == 401


# ── the actual gate ────────────────────────────────────────────────────

def test_correct_token_passes():
    assert build().get("/private", headers=bearer(TOKEN)).status_code == 200


def test_wrong_token_is_401():
    assert build().get("/private", headers=bearer("nope")).status_code == 401


def test_missing_header_is_401():
    assert build().get("/private").status_code == 401


def test_401_body_is_the_family_shape():
    """Leaves assert this shape; changing it is a breaking change for them."""
    r = build().get("/private")
    assert r.status_code == 401
    assert r.json() == {"detail": "unauthorized"}


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "BeArEr"])
def test_scheme_is_case_insensitive(scheme):
    c = build()
    r = c.get("/private", headers={"Authorization": f"{scheme} {TOKEN}"})
    assert r.status_code == 200


@pytest.mark.parametrize("header", [
    "Basic c2VrcmV0",          # wrong scheme
    "Bearer",                  # scheme only, no space
    "Bearer ",                 # empty token
    "sekret",                  # bare token, no scheme
    "",                        # empty header
    "Token sekret",            # some other scheme
])
def test_malformed_authorization_headers_are_401(header):
    r = build().get("/private", headers={"Authorization": header})
    assert r.status_code == 401


def test_extra_whitespace_is_not_stripped_into_a_match():
    """`auth[7:]` takes everything after "Bearer ", so a second space is part
    of the token. That's correct - be strict about what you accept."""
    r = build().get("/private", headers={"Authorization": f"Bearer  {TOKEN}"})
    assert r.status_code == 401


def test_prefix_of_the_real_token_does_not_pass():
    assert build().get("/private", headers=bearer("sek")).status_code == 401


def test_token_with_trailing_content_does_not_pass():
    assert build().get("/private", headers=bearer(TOKEN + "x")).status_code == 401


# ── the non-ASCII regression ───────────────────────────────────────────

def test_non_ascii_token_is_401_not_500():
    """REGRESSION: hmac.compare_digest raises TypeError when handed str with
    non-ASCII characters. Starlette decodes headers as latin-1, so a single
    high byte in the Authorization header turned an unauthenticated 401 into
    an unhandled exception - a remote 500 in the middleware every service in
    the family mounts. The compare must happen on BYTES."""
    r = build().get("/private",
                    headers={"Authorization": "Bearer töken".encode("latin-1")})
    assert r.status_code == 401, "non-ASCII token must be rejected, not crash"


def test_a_genuine_unicode_token_still_works():
    """Rejecting non-ASCII outright would be the lazy fix. An operator who
    configures a unicode token must still be able to authenticate with it."""
    tok = "pässwörd-ünïcode"
    c = build(token=tok)
    r = c.get("/private",
              headers={"Authorization": ("Bearer " + tok).encode("utf-8")})
    assert r.status_code == 200


def test_non_ascii_mismatch_is_still_rejected():
    c = build(token="pässwörd")
    r = c.get("/private",
              headers={"Authorization": "Bearer wröng".encode("utf-8")})
    assert r.status_code == 401
