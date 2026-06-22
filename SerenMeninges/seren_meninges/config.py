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

=== SKELETON === shapes + the token wiring are real; a leaf will extend
`load_*` with its own sections. Wire tests in the leaf (or here) before release.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from .credentials import resolve_token

log = logging.getLogger("seren_meninges.config")


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
    own `default_port` to `from_dict` so 7420/7422/7423 stay leaf-owned.
    """
    host: str = "0.0.0.0"
    port: int = 0

    # token pointers — at most one in practice (see resolve_bearer)
    bearer_token: str = ""          # inline literal (escape hatch / Nano-floor)
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
    def from_dict(cls, d: Optional[dict[str, Any]], *, default_port: int = 0) -> "ServerConfig":
        d = d or {}
        return cls(
            host=str(d.get("host", "0.0.0.0")),
            port=int(d.get("port", default_port) or default_port),
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
