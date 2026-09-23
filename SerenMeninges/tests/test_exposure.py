"""
An open bind with no token is a refusal, not a warning - and the override is
loud, explicit, and per-leaf.
"""
from __future__ import annotations

import pytest

from seren_meninges.config import ServerConfig
from seren_meninges.exposure import (
    EXIT_REFUSED, VERDICT_LAN_OPEN_ALLOWED, VERDICT_LAN_WITH_TOKEN,
    VERDICT_LOOPBACK, VERDICT_REFUSED, check_exposure, enforce_exposure,
    enforce_server, is_loopback,
)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2", "127.255.0.9", " LOCALHOST "])
def test_loopback_is_recognised(host):
    assert is_loopback(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.0.101", "10.0.0.5", "seren.local", ""])
def test_everything_else_is_exposed(host):
    assert not is_loopback(host)


def test_loopback_has_nothing_to_say():
    ex = check_exposure("127.0.0.1", 7422, service="seren-loci")
    assert ex.verdict == VERDICT_LOOPBACK and not ex.refused
    assert "this box only" in ex.message


def test_lan_with_a_token_is_allowed_and_says_so():
    ex = check_exposure("0.0.0.0", 7422, service="seren-loci", token="sekret")
    assert ex.verdict == VERDICT_LAN_WITH_TOKEN and not ex.refused
    assert "bearer token is required" in ex.message


def test_lan_with_no_token_is_refused(monkeypatch):
    monkeypatch.delenv("SEREN_LOCI_ALLOW_OPEN_LAN", raising=False)
    ex = check_exposure("0.0.0.0", 7422, service="seren-loci", env_prefix="SEREN_LOCI")
    assert ex.refused
    for must in ("REFUSING TO START", "host: 127.0.0.1", "bearer_token_env: SEREN_LOCI_TOKEN",
                 "allow_open_lan: true", "SEREN_LOCI_ALLOW_OPEN_LAN=1"):
        assert must in ex.message, must


def test_a_pointer_to_an_empty_token_is_still_no_token():
    """The caller hands in the RESOLVED token; an env var that is unset
    resolves to "", and "" is open. The check must not be fooled by a config
    field being non-empty."""
    ex = check_exposure("0.0.0.0", 7422, service="x", token="")
    assert ex.refused


def test_the_config_override_lets_it_start_with_a_banner(monkeypatch):
    monkeypatch.delenv("SEREN_X_ALLOW_OPEN_LAN", raising=False)
    ex = check_exposure("0.0.0.0", 1, service="x", allow_open_lan=True, env_prefix="SEREN_X")
    assert ex.verdict == VERDICT_LAN_OPEN_ALLOWED and not ex.refused
    assert "OPEN ON THE NETWORK" in ex.message
    assert "allow_open_lan: true" in ex.message
    assert "every boot" in ex.message


def test_the_env_override_is_per_leaf(monkeypatch):
    """SEREN_LOCI_ALLOW_OPEN_LAN opens Loci and nothing else. A single shared
    name would be one env var away from opening the whole box."""
    monkeypatch.setenv("SEREN_LOCI_ALLOW_OPEN_LAN", "1")
    monkeypatch.delenv("SEREN_MEMORY_ALLOW_OPEN_LAN", raising=False)
    loci = check_exposure("0.0.0.0", 1, service="seren-loci", env_prefix="SEREN_LOCI")
    memory = check_exposure("0.0.0.0", 1, service="seren-memory", env_prefix="SEREN_MEMORY")
    assert loci.verdict == VERDICT_LAN_OPEN_ALLOWED
    assert "SEREN_LOCI_ALLOW_OPEN_LAN is set" in loci.message
    assert memory.refused


@pytest.mark.parametrize("value", ["0", "false", "no", "", "maybe"])
def test_only_a_truthy_env_value_counts(monkeypatch, value):
    monkeypatch.setenv("SEREN_X_ALLOW_OPEN_LAN", value)
    assert check_exposure("0.0.0.0", 1, service="x", env_prefix="SEREN_X").refused


def test_a_service_without_bearer_support_gets_honest_instructions(monkeypatch):
    """Margin has no token by design. Telling its operator to configure one
    would be a lie with a yaml snippet."""
    monkeypatch.delenv("SEREN_MARGIN_ALLOW_OPEN_LAN", raising=False)
    ex = check_exposure("0.0.0.0", 7421, service="seren-margin",
                        env_prefix="SEREN_MARGIN", supports_token=False)
    assert ex.refused
    assert "bearer_token_env" not in ex.message
    assert "no bearer auth of its own" in ex.message
    assert "reverse proxy" in ex.message
    # and a token it cannot check must not let it through
    assert check_exposure("0.0.0.0", 7421, service="seren-margin", token="sekret",
                          supports_token=False).refused


def test_enforce_prints_and_exits_with_ex_config(monkeypatch, capsys):
    monkeypatch.delenv("SEREN_X_ALLOW_OPEN_LAN", raising=False)
    with pytest.raises(SystemExit) as exc:
        enforce_exposure("0.0.0.0", 9, service="x", env_prefix="SEREN_X")
    assert exc.value.code == EXIT_REFUSED == 78
    assert "REFUSING TO START" in capsys.readouterr().out


def test_enforce_returns_the_verdict_when_it_lets_you_through(capsys):
    assert enforce_exposure("127.0.0.1", 9, service="x") == VERDICT_LOOPBACK
    assert "this box only" in capsys.readouterr().out


def test_enforce_server_reads_the_shared_block(monkeypatch):
    """The one-liner every leaf calls: host, port, the RESOLVED bearer and the
    override all come off the ServerConfig."""
    monkeypatch.delenv("SEREN_X_ALLOW_OPEN_LAN", raising=False)
    seen = []
    open_no_token = ServerConfig.from_dict({"host": "0.0.0.0", "port": 5})
    with pytest.raises(SystemExit):
        enforce_server(open_no_token, service="x", env_prefix="SEREN_X", log=seen.append)

    monkeypatch.setenv("X_TOK", "resolved-from-env")
    via_pointer = ServerConfig.from_dict({"host": "0.0.0.0", "port": 5, "bearer_token_env": "X_TOK"})
    assert enforce_server(via_pointer, service="x", env_prefix="SEREN_X", log=seen.append) == VERDICT_LAN_WITH_TOKEN

    allowed = ServerConfig.from_dict({"host": "0.0.0.0", "port": 5, "allow_open_lan": True})
    assert enforce_server(allowed, service="x", env_prefix="SEREN_X", log=seen.append) == VERDICT_LAN_OPEN_ALLOWED


def test_allow_open_lan_is_a_real_config_field():
    assert ServerConfig.from_dict({}).allow_open_lan is False
    assert ServerConfig.from_dict({"allow_open_lan": "yes"}).allow_open_lan is True
    assert ServerConfig.from_dict({"allow_open_lan": "no"}).allow_open_lan is False
    assert ServerConfig.from_dict({"allow_open_lan": True}).allow_open_lan is True
