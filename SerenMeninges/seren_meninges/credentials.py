"""
seren_meninges.credentials
========================================================================

THE FLAGSHIP. One pure function, `resolve_token`, that every Seren service
shares so the rule "config holds a POINTER to a secret, never the secret" is
true by construction in all three repos instead of three times by hand.

The same call serves BOTH directions of auth:
  - inbound  (the service deciding what token it REQUIRES of callers) ->
             feeds the bearer-auth middleware.
  - outbound (the callosum deciding what token to PRESENT to a store it
             fans) -> feeds the adapter's Authorization header.
Same resolution, same precedence, one place to get it right.

THREAT MODEL (why this shape): on a trusted-LAN homelab the bearer token
isn't holding off a network attacker - everything binds localhost. It's
keeping the secret out of a readable file so a backup, a synced dotfile, a
screen-share, a commit, or a `cat *.yaml` can't leak it. So we don't need a
vault; we need "the secret isn't sitting in plaintext next to the code." Env
and keyring both satisfy that; inline is the deliberate escape hatch.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("seren_meninges.credentials")


def resolve_token(
    *,
    inline: Optional[str] = None,
    keyring_ref: Optional[str] = None,
    env_var: Optional[str] = None,
    default: str = "",
) -> str:
    """Resolve a bearer token from the first configured source that yields one.

    Polymorphic by KEYWORD, not by a `type:` discriminator - the caller wires
    whichever config keys are present (``bearer_token`` -> inline,
    ``bearer_token_keyring`` -> keyring_ref, ``bearer_token_env`` -> env_var).
    Configure exactly one per deployment; if several are set, this is the
    documented tie-break order:

        1. inline       literal value (tests / localhost / Nano-floor escape
                        hatch). Plaintext on disk - that's the trade you opt
                        into by using it.
        2. keyring_ref  "service/username" into the OS keychain via the
                        `keyring` lib: Windows Credential Manager, macOS
                        Keychain, Linux Secret Service. The secure default on
                        a desktop. Opt-in [keyring] extra; needs a backend
                        present (headless Jetsons often have none -> falls
                        through, which is *why* env exists).
        3. env_var      NAME of an environment variable holding the token.
                        Byte-identical across all three OSes, injected by the
                        launcher (NSSM AppEnvironmentExtra / systemd
                        Environment= / launchd EnvironmentVariables). The
                        cross-platform baseline.
        4. default      "" means "no auth" - an empty configured token is the
                        family norm for open, trusted-LAN services.

    NEVER raises on a missing/unavailable source. A typo'd env var, an absent
    keyring backend, the extra not installed - each degrades to the next
    source and finally to `default`. Leniency is the point: a misconfigured
    secret should leave the service OPEN-or-DOWN by its own config, never
    crash-on-boot from the resolver. The winning source is logged at DEBUG so
    you can always answer "why is auth on/off right now".
    """
    # 1. inline — the deliberate escape hatch.
    if inline:
        log.debug("token source: inline literal")
        return inline

    # 2. keyring — the secure cross-platform option (opt-in, may be absent).
    if keyring_ref:
        tok = _from_keyring(keyring_ref)
        if tok:
            log.debug("token source: keyring (%s)", keyring_ref)
            return tok
        log.warning(
            "keyring ref %r is set but yielded nothing - falling through to "
            "env/default", keyring_ref)

    # 3. env var — the baseline that works identically everywhere.
    if env_var:
        tok = os.environ.get(env_var)
        if tok:
            log.debug("token source: env var (%s)", env_var)
            return tok
        log.warning(
            "env var %r is named in config but not present in the environment "
            "- falling through to default", env_var)

    # 4. nothing resolved — empty == no auth (family norm).
    return default


def _from_keyring(ref: str) -> Optional[str]:
    """Look a token up in the OS keychain. `ref` is "service/username".

    Returns None - never raises - if `keyring` isn't installed, has no usable
    backend (the headless-Jetson case), or simply has no entry. That way a
    desktop keychain is an *upgrade* over env, never a hard dependency: the
    same config resolves on your workstation (keychain) and on the Nano (falls
    through to env) with no per-box code.
    """
    try:
        import keyring  # optional: the [keyring] extra
    except ImportError:
        log.warning(
            "bearer_token_keyring is set but the [keyring] extra isn't "
            "installed - `pip install seren-meninges[keyring]`")
        return None

    service, sep, username = ref.partition("/")
    if not sep or not service or not username:
        log.warning("keyring ref %r must be 'service/username'", ref)
        return None

    try:
        return keyring.get_password(service, username)
    except Exception as e:  # no backend, locked store, D-Bus missing, etc.
        log.warning("keyring lookup failed for %r: %s", ref, e)
        return None


def store_token(keyring_ref: str, secret: str) -> bool:
    """The WRITE half of resolve_token's keyring branch: stash a secret in the
    OS keychain under "service/username" (same ref shape as resolve_token reads).

    Returns True if the secret landed in the keychain; False if there's no
    usable backend (the headless-Jetson/Xavier case), the [keyring] extra isn't
    installed, or the ref is malformed. NEVER raises - the caller uses the False
    to fall back to an inline (plaintext) pointer, mirroring resolve_token's
    read-side degrade-never-crash rule. The platform decides the security tier:
    a desktop with Credential Manager / Keychain / Secret Service gets real
    protection; a bare node with no backend degrades to trusted-LAN plaintext.
    """
    service, sep, username = keyring_ref.partition("/")
    if not sep or not service or not username:
        log.warning("store_token: keyring ref %r must be 'service/username'", keyring_ref)
        return False
    try:
        import keyring  # optional: the [keyring] extra
    except ImportError:
        log.warning("store_token: the [keyring] extra isn't installed - "
                    "`pip install seren-meninges[keyring]` (no keychain on this node)")
        return False
    try:
        keyring.set_password(service, username, secret)
        log.debug("store_token: wrote secret to keychain (%s)", keyring_ref)
        return True
    except Exception as e:  # no backend, locked store, D-Bus missing, etc.
        log.warning("store_token: keychain write failed for %r: %s", keyring_ref, e)
        return False


def delete_token(keyring_ref: str) -> bool:
    """Remove a secret from the OS keychain (the cleanup half - call it when a
    store/resource that owned a keychain entry is deleted, so removing it never
    orphans a secret).

    Returns True if an entry was deleted, False if it was absent, the ref is
    malformed, or there's no usable backend. NEVER raises: a removal must not
    fail just because the keychain entry was already gone (or was an inline
    token that never touched the keychain in the first place).
    """
    service, sep, username = keyring_ref.partition("/")
    if not sep or not service or not username:
        return False
    try:
        import keyring  # optional: the [keyring] extra
    except ImportError:
        return False
    try:
        keyring.delete_password(service, username)
        log.debug("delete_token: removed keychain entry (%s)", keyring_ref)
        return True
    except Exception:  # PasswordDeleteError (not found), no backend, etc.
        return False
