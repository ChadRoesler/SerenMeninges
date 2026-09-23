"""
Tests for seren_meninges.config — the shared server/tls shapes and the
lenient loader primitives. The leaf-owned default_port, the resolve_bearer
wiring into resolve_token, and the degrade-never-crash promise of read_yaml.
"""
from seren_meninges.config import (
    DEFAULT_HOST,
    ServerConfig,
    TlsConfig,
    apply_env_overrides,
    read_yaml,
)


# ── ServerConfig ─────────────────────────────────────────────────────────
def test_server_defaults_with_leaf_port():
    cfg = ServerConfig.from_dict({}, default_port=7422)
    assert cfg.host == "127.0.0.1"     # loopback unless somebody says otherwise
    assert cfg.port == 7422            # leaf supplies its own default
    assert cfg.bearer_token == ""
    assert cfg.resolve_bearer() == ""  # no token configured -> open


def test_the_library_default_is_loopback():
    """THE GUARD. Every leaf that forgets to pass default_host must land on
    loopback, not the LAN - the safe direction to be wrong in. Theatre used to
    carry a whole function to undo the old 0.0.0.0 literal; this is what
    makes that function unnecessary."""
    assert DEFAULT_HOST == "127.0.0.1"
    assert ServerConfig().host == "127.0.0.1"
    assert ServerConfig.from_dict(None).host == "127.0.0.1"


def test_a_leaf_can_choose_the_lan():
    """Observatory is the per-node plane and wants every interface. It says so
    on its own line, the same way a leaf says its port."""
    cfg = ServerConfig.from_dict({}, default_port=7777, default_host="0.0.0.0")
    assert cfg.host == "0.0.0.0"


def test_an_explicit_host_beats_the_leaf_default():
    """Widening is a thing you did. An operator who wrote host: 0.0.0.0 gets
    0.0.0.0 whatever the leaf would have preferred, and vice versa."""
    assert ServerConfig.from_dict({"host": "0.0.0.0"}).host == "0.0.0.0"
    assert ServerConfig.from_dict({"host": "127.0.0.1"},
                                  default_host="0.0.0.0").host == "127.0.0.1"


def test_a_null_host_counts_as_unset():
    """yaml `host:` with nothing after it used to become the string "None"
    and fail to bind. Present-but-empty is the operator saying nothing."""
    assert ServerConfig.from_dict({"host": None}).host == "127.0.0.1"
    assert ServerConfig.from_dict({"host": ""}).host == "127.0.0.1"


def test_a_bad_port_falls_to_the_default_instead_of_crashing(caplog):
    """The module promises never to crash boot on a malformed key, and the env
    path already kept that promise; the yaml path did not."""
    cfg = ServerConfig.from_dict({"port": "7422a"}, default_port=7422)
    assert cfg.port == 7422
    assert "isn't an int" in caplog.text
    assert ServerConfig.from_dict({"port": None}, default_port=5).port == 5


def test_a_non_mapping_server_block_is_ignored():
    # `server: hello` is not a block; it is also not a reason to crash.
    cfg = ServerConfig.from_dict("hello", default_port=9)  # type: ignore[arg-type]
    assert (cfg.host, cfg.port) == ("127.0.0.1", 9)


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


def test_read_yaml_non_mapping_document_returns_empty(tmp_path):
    """A file holding a bare word or a list parses, and the annotation says
    dict. It has to BE a dict, or the leaf's `.get("server")` is the crash."""
    for text in ("foo\n", "- a\n- b\n", "42\n"):
        p = tmp_path / "odd.yaml"
        p.write_text(text)
        assert read_yaml(str(p)) == {}, text


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
