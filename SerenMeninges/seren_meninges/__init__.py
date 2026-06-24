"""
SerenMeninges — the connective tissue of the Seren brain.

The shared core for the three hemisphere services (SerenMemory, SerenLoci,
SerenCorpusCallosum). It holds CONTRACTS and MECHANISMS only - the things
that must be identical across all three and are stable enough to be boring:

  - resolve_token()         credentials: config holds a pointer, not a secret
  - ServerConfig/TlsConfig   the shared config blocks + lenient loader
  - get_version()            the one version-getter
  - bearer_auth_middleware   one constant-time auth implementation
  - render_shell()           the viewer's shared shell + design tokens

Anything redesign-prone (per-service routes, schemas, storage, RRF, viewer
TAB content) stays in the leaf repos. See SPEC.md for the full rationale and
the version-coupling contract.

Light imports only at package load (version/credentials/config). `auth` and
`viewer` are imported from their submodules on demand so the core stays
dependency-light for callers that don't need them.
"""
from __future__ import annotations

from .config import ServerConfig, TlsConfig, apply_env_overrides, read_yaml
from .credentials import resolve_token, store_token, delete_token
from .version import get_version

__all__ = [
    "resolve_token",
    "store_token",
    "delete_token",
    "ServerConfig",
    "TlsConfig",
    "read_yaml",
    "apply_env_overrides",
    "get_version",
    # imported on demand from submodules:
    #   from seren_meninges.auth import bearer_auth_middleware
    #   from seren_meninges.viewer import render_shell
]

try:
    __version__ = get_version("seren-meninges")
except Exception:  # pragma: no cover
    __version__ = "0.0.0"
