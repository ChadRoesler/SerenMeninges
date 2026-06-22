"""
Tests for seren_meninges.config — the shared server/tls shapes and the
lenient loader primitives. The leaf-owned default_port, the resolve_bearer
wiring into resolve_token, and the degrade-never-crash promise of read_yaml.
"""
from seren_meninges.config import (
    ServerConfig,
    TlsConfig,
    apply_env_overrides,
    read_yaml,
)


# ── ServerConfig ─────────────────────────────────────────────────────────
def test_server_defaults_with_leaf_port():
    cfg = ServerConfig.from_dict({}, default_port=7422)
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 7422            # leaf supplies its own default
    assert cfg.bearer_token == ""
    assert cfg.resolve_bearer() == ""  # no token configured -> open


def test_server_reads_values():
    cfg = ServerConfig.from_dict(
        {"host": "127.0.0.1", "port": 9000, "bearer_token": "abc"},
        default_port=7422,
    )
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 9000
    assert cfg.resolve_bearer() == "abc"   # inline wins


def test_server_zero_port_falls_to_default():
    # port: 0 in yaml reads as "unset" -> the leaf default, not a literal 0
    cfg = ServerConfig.from_dict({"port": 0}, default_port=7422)
    assert cfg.port == 7422


def test_server_resolve_bearer_via_env(monkeypatch):
    monkeypatch.setenv("SEREN_X_TOKEN", "envtok")
    cfg = ServerConfig.from_dict({"bearer_token_env": "SEREN_X_TOKEN"})
    assert cfg.resolve_bearer() == "envtok"


def test_server_tolerates_unknown_keys():
    # forward-compat: a config carrying keys this version doesn't know about
    # must be ignored, not fatal (so adding keys later never breaks old configs)
    cfg = ServerConfig.from_dict({"host": "h", "future_key": "whatever"}, default_port=1)
    assert cfg.host == "h"


# ── TlsConfig ────────────────────────────────────────────────────────────
def test_tls_defaults_false():
    assert TlsConfig.from_dict({}).trust_system_store is False
    assert TlsConfig.from_dict(None).trust_system_store is False


def test_tls_reads_true():
    assert TlsConfig.from_dict({"trust_system_store": True}).trust_system_store is True


# ── read_yaml — lenient, degrade-never-crash ─────────────────────────────
def test_read_yaml_missing_file_returns_empty(tmp_path):
    assert read_yaml(str(tmp_path / "nope.yaml")) == {}


def test_read_yaml_empty_file_returns_empty(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    assert read_yaml(str(p)) == {}


def test_read_yaml_valid(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("server:\n  port: 7422\n")
    assert read_yaml(str(p)) == {"server": {"port": 7422}}


def test_read_yaml_malformed_returns_empty(tmp_path):
    # an unclosed flow sequence raises in yaml -> caught -> {}, never crashes boot
    p = tmp_path / "bad.yaml"
    p.write_text("server: [1, 2")
    assert read_yaml(str(p)) == {}


# ── apply_env_overrides ──────────────────────────────────────────────────
def test_env_overrides_host_and_port(monkeypatch):
    monkeypatch.setenv("SEREN_T_HOST", "1.2.3.4")
    monkeypatch.setenv("SEREN_T_PORT", "8123")
    cfg = ServerConfig(host="0.0.0.0", port=7422)
    apply_env_overrides(cfg, prefix="SEREN_T")
    assert cfg.host == "1.2.3.4"
    assert cfg.port == 8123


def test_env_override_bad_port_ignored(monkeypatch):
    monkeypatch.setenv("SEREN_T_PORT", "not-an-int")
    cfg = ServerConfig(host="h", port=7422)
    apply_env_overrides(cfg, prefix="SEREN_T")
    assert cfg.port == 7422   # kept; bad value ignored, no raise


def test_env_override_absent_is_noop(monkeypatch):
    monkeypatch.delenv("SEREN_Q_HOST", raising=False)
    monkeypatch.delenv("SEREN_Q_PORT", raising=False)
    cfg = ServerConfig(host="h", port=5)
    apply_env_overrides(cfg, prefix="SEREN_Q")
    assert cfg.host == "h"
    assert cfg.port == 5
