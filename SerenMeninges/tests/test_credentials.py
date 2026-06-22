"""
Tests for seren_meninges.credentials.resolve_token — the flagship.

Covers the four sources, the precedence tie-break, and the load-bearing
promise: NEVER raises on a missing/unavailable source. Also locks the
keyword-only signature so future sources can be ADDED without breaking any
existing call site (the backwards-compat contract, in test form).

Keyring is exercised by injecting a fake module via monkeypatch.setitem
(managed, auto-undone) rather than mutating sys.modules by hand. The
"not installed" path needs no injection - keyring genuinely isn't a test dep,
so the real ImportError branch runs for free.
"""
import sys

import pytest

from seren_meninges import credentials
from seren_meninges.credentials import resolve_token


# ── precedence: inline > keyring > env > default ─────────────────────────
def test_inline_wins_over_everything(monkeypatch):
    monkeypatch.setattr(credentials, "_from_keyring", lambda ref: "KR")
    monkeypatch.setenv("TOK_ENV", "ENV")
    assert resolve_token(inline="INLINE", keyring_ref="s/u", env_var="TOK_ENV") == "INLINE"


def test_keyring_beats_env(monkeypatch):
    monkeypatch.setattr(credentials, "_from_keyring", lambda ref: "KR")
    monkeypatch.setenv("TOK_ENV", "ENV")
    assert resolve_token(keyring_ref="s/u", env_var="TOK_ENV") == "KR"


def test_env_used_when_no_inline_or_keyring(monkeypatch):
    monkeypatch.setenv("TOK_ENV", "ENV")
    assert resolve_token(env_var="TOK_ENV") == "ENV"


def test_keyring_miss_falls_through_to_env(monkeypatch):
    # keyring ref present but yields nothing -> env is the next source
    monkeypatch.setattr(credentials, "_from_keyring", lambda ref: None)
    monkeypatch.setenv("TOK_ENV", "ENV")
    assert resolve_token(keyring_ref="s/u", env_var="TOK_ENV") == "ENV"


# ── default + leniency: NEVER raises ─────────────────────────────────────
def test_nothing_set_returns_default():
    assert resolve_token() == ""
    assert resolve_token(default="open") == "open"


def test_env_named_but_absent_returns_default(monkeypatch):
    monkeypatch.delenv("DEFINITELY_NOT_SET", raising=False)
    assert resolve_token(env_var="DEFINITELY_NOT_SET", default="fallback") == "fallback"


def test_empty_inline_is_not_a_token(monkeypatch):
    # "" is falsy -> treated as unset -> falls through to env
    monkeypatch.setenv("TOK_ENV", "ENV")
    assert resolve_token(inline="", env_var="TOK_ENV") == "ENV"


def test_never_raises_when_all_sources_fail(monkeypatch):
    # keyring not installed (real ImportError) + env absent -> default, no raise
    monkeypatch.delenv("NOPE", raising=False)
    assert resolve_token(keyring_ref="s/u", env_var="NOPE", default="d") == "d"


# ── the keyword-only contract (the backwards-compat lock) ────────────────
def test_signature_is_keyword_only():
    # A positional call MUST fail. That guarantees new sources can be added as
    # new keyword params later without breaking any existing call site - the
    # "backwards-compatible by default" rule, enforced by the signature itself.
    with pytest.raises(TypeError):
        resolve_token("positional")  # type: ignore[misc]


# ── _from_keyring directly ───────────────────────────────────────────────
def test_from_keyring_not_installed_returns_none():
    # keyring isn't a test dep -> ImportError branch -> None, never raises
    assert credentials._from_keyring("svc/user") is None


def test_from_keyring_parses_service_username(monkeypatch):
    captured = {}

    class FakeKeyring:
        @staticmethod
        def get_password(service, username):
            captured["service"] = service
            captured["username"] = username
            return "SECRET"

    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring)
    assert credentials._from_keyring("seren-loci/bearer") == "SECRET"
    assert captured == {"service": "seren-loci", "username": "bearer"}


def test_from_keyring_bad_ref_returns_none(monkeypatch):
    called = {"hit": False}

    class FakeKeyring:
        @staticmethod
        def get_password(service, username):
            called["hit"] = True
            return "X"

    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring)
    # no slash -> malformed -> None, and keyring is never consulted
    assert credentials._from_keyring("noslash") is None
    assert called["hit"] is False


def test_from_keyring_backend_error_returns_none(monkeypatch):
    class FakeKeyring:
        @staticmethod
        def get_password(service, username):
            raise RuntimeError("no backend (the headless-Jetson case)")

    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring)
    assert credentials._from_keyring("svc/user") is None
