"""
SerenMeninges — the connective tissue of the Seren brain.

The shared core every Seren service wears - the memory hemispheres, the
control plane, the tools, the viewers. It holds CONTRACTS and MECHANISMS
only - the things that must be identical across the family and are stable
enough to be boring:

  - resolve_token()         credentials: config holds a pointer, not a secret
  - ServerConfig/TlsConfig   the shared config blocks + lenient loader
  - get_version()            the one version-getter
  - UpdateChecker            "is there a newer me" - get_version's far half
  - bearer_auth_middleware   one constant-time auth implementation
  - render_shell()           the viewer's shared shell + design tokens

Anything redesign-prone (per-service routes, schemas, storage, RRF, viewer
TAB content) stays in the leaf repos. The rationale for each piece lives in
that module's own docstring.

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
    #   from seren_meninges.updates import UpdateChecker
]

try:
    __version__ = get_version("seren-meninges")
except Exception:  # pragma: no cover
    __version__ = "0.0.0"
