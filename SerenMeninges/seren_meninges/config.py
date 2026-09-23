"""
seren_meninges.config
========================================================================

The config blocks every service shares - the `server` and `tls` sections -
plus the lenient load discipline (defaults -> yaml -> env, never crash on a
missing/malformed key). A leaf composes its OWN full config from these PLUS
its service-specific blocks (federation for the callosum, storage for
loci/memory). Meninges owns the shared shape; the leaf owns the rest.

Plain dataclasses, no pydantic - matches the family's existing config style
and keeps the core dependency-light (pyyaml is the only runtime need here).

ON THE HOST DEFAULT (2.3.0): loopback. The bind address used to be a literal
"0.0.0.0" inside `from_dict` with no way for a leaf to say otherwise, while
`default_port` was a parameter precisely because ports are leaf-owned. Hosts
are leaf-owned too, and the safe direction to be wrong in is inward: a leaf
that forgets to pass `default_host` now gets 127.0.0.1, not the LAN. The one
service that genuinely wants every interface (Observatory, the per-node
plane) says so in its own file, where a reader can see it was chosen. An
explicit `host:` in yaml is honoured either way - widening should be a thing
you did, not a thing that happened.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from .credentials import resolve_token

log = logging.getLogger("seren_meninges.config")

#: The bind address a leaf gets when neither it nor the operator said. Loopback,
#: because every Seren service is reachable by the person in front of the box
#: before it is reachable by anyone else - and a service that should be on the
#: LAN (Observatory) passes ``default_host="0.0.0.0"`` on its own line.
DEFAULT_HOST = "127.0.0.1"


# ── the shared blocks ────────────────────────────────────────────────────
@dataclass
class TlsConfig:
    """Outbound-TLS posture. `trust_system_store` routes verification through
    the OS trust store (corp proxy boxes) - the [corp]/truststore path."""
    trust_system_store: bool = False

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> "TlsConfig":
        d = d or {}
        return cls(trust_system_store=bool(d.get("trust_system_store", False)))


@dataclass
class ServerConfig:
    """host/port + the bearer-token POINTERS (never the secret inline unless
    you deliberately use the escape hatch).

    The three token keys are mutually-exclusive-by-convention; `resolve_bearer`
    applies the precedence (see credentials.resolve_token). A leaf passes its
    own `default_port` to `from_dict` so 7420/7422/7423 stay leaf-owned, and
    its own `default_host` if loopback is the wrong answer for it.
    """
    host: str = DEFAULT_HOST
    port: int = 0

    # token pointers — at most one in practice (see resolve_bearer).
    # The inline literal is the one that IS the secret, so it is kept out of
    # the dataclass repr: a `log.info("%s", cfg)` or a traceback that dumps
    # locals must never print it. The other two are pointers and stay visible.
    bearer_token: str = field(default="", repr=False)   # inline literal (escape hatch / Nano-floor)
    bearer_token_env: str = ""      # NAME of an env var holding the token
    bearer_token_keyring: str = ""  # "service/username" into the OS keychain

    def resolve_bearer(self) -> str:
        """The single token this service requires of callers (or "" = open).
        Same resolver the callosum uses outbound - inbound/outbound symmetry."""
        return resolve_token(
            inline=self.bearer_token or None,
            keyring_ref=self.bearer_token_keyring or None,
            env_var=self.bearer_token_env or None,
        )

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]], *,
                  default_port: int = 0,
                  default_host: str = DEFAULT_HOST) -> "ServerConfig":
        """Build the block from a yaml mapping, leniently.

        `default_host` / `default_port` are what the LEAF wants when the
        operator said nothing. A `host:` that is present but empty or null
        counts as "said nothing" - the old code turned yaml `host:` into the
        string "None" and then failed to bind. A port that is not an int is
        logged and falls back to the default rather than crashing boot, which
        is the promise the module docstring makes and the env path already
        kept.
        """
        d = d if isinstance(d, dict) else {}
        host = d.get("host")
        raw_port = d.get("port", default_port)
        try:
            port = int(raw_port) if raw_port not in (None, "") else default_port
        except (TypeError, ValueError):
            log.warning("server.port=%r isn't an int - using %d", raw_port, default_port)
            port = default_port
        return cls(
            host=str(host) if host else default_host,
            port=port or default_port,
            bearer_token=str(d.get("bearer_token", "") or ""),
            bearer_token_env=str(d.get("bearer_token_env", "") or ""),
            bearer_token_keyring=str(d.get("bearer_token_keyring", "") or ""),
        )


# ── the lenient loader primitives (leaves build on these) ────────────────
def read_yaml(path: str) -> dict[str, Any]:
    """Load a yaml config to a dict, leniently. Missing file or empty doc ->
    {} (run on defaults). A genuinely malformed doc is logged and treated as
    {} rather than crashing boot - the Nano-floor 'degrade, never crash' rule.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover - yaml is a core dep, guard anyway
        log.warning("pyyaml not installed; config read returns {}")
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if data and not isinstance(data, dict):
            # A document that is a bare scalar or a list parses fine and is
            # still not a config. Returning it would hand the leaf something
            # `.get("server")` raises on, which is a crash from a file that
            # merely looks odd.
            log.warning("config at %s is not a mapping (%s) - using defaults",
                        path, type(data).__name__)
            return {}
        return data or {}
    except FileNotFoundError:
        log.info("no config at %s - using defaults", path)
        return {}
    except Exception as e:
        log.warning("config at %s is malformed (%s) - using defaults", path, e)
        return {}


def apply_env_overrides(cfg: ServerConfig, *, prefix: str) -> ServerConfig:
    """Let env vars override the server block last (defaults -> yaml -> ENV).

    Reads ``{prefix}_HOST`` / ``{prefix}_PORT`` (e.g. SEREN_LOCI_HOST). Token
    values are NOT overridden here on purpose - the token comes from its
    pointer (env_var/keyring), not from a second env channel, so there's one
    obvious place the secret lives. A bad PORT is ignored with a warning.
    """
    host = os.environ.get(f"{prefix}_HOST")
    if host:
        cfg.host = host
    port = os.environ.get(f"{prefix}_PORT")
    if port:
        try:
            cfg.port = int(port)
        except ValueError:
            log.warning("%s_PORT=%r isn't an int - ignoring", prefix, port)
    return cfg
