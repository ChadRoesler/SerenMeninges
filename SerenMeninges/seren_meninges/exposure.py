"""
seren_meninges.exposure
========================================================================

The one question every service must answer before it listens: am I about to
put this on the network with nothing in front of it?

WHY IT STOPS YOU INSTEAD OF WARNING. A warning scrolls past. Every service in
the family used to print "auth: DISABLED (no token)" and then bind whatever
host the yaml said, and on a default install that was every interface. A LAN
full of your own memories, or of the control plane that holds every node's
token, does not scroll past. So an open bind with no token is a refusal, at
startup, before anything expensive is built, with the whole situation and all
three ways out printed in plain words.

WHY THERE IS AN OVERRIDE. The point of the stack is to lower the bar, not to
add a bouncer. A trusted LAN, a lab, a demo on a laptop that never leaves the
room - those are real, and the person in front of the box gets to decide. So
`allow_open_lan: true` in the server block (or `<PREFIX>_ALLOW_OPEN_LAN=1`)
lets it start, and it says so, loudly, every single boot. Widening is a thing
you did, not a thing that happened.

THE THREE VERDICTS a bind can get:
    loopback     - the default. Nothing to say.
    lan+token    - reachable, but a bearer is required. One line, so a reader
                   of the log knows the service is on the network on purpose.
    lan+open     - reachable with no auth, and the operator said allow. A
                   banner every boot; it never becomes background noise.
And the fourth outcome, which is not a verdict: REFUSED, exit code 78
(EX_CONFIG - "configuration error" - the sysexits code a supervisor can read
and choose not to restart-loop on).

A service that has NO bearer support at all (Margin, by design; Symposium's
loopback shim) passes `supports_token=False` and gets a message that does not
tell the operator to configure a token that does not exist.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

#: sysexits.h EX_CONFIG. A supervisor (systemd, NSSM) that sees this can tell
#: "the config is wrong" from "the process crashed", and stop restarting.
EXIT_REFUSED = 78

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

VERDICT_LOOPBACK = "loopback"
VERDICT_LAN_WITH_TOKEN = "lan+token"
VERDICT_LAN_OPEN_ALLOWED = "lan+open"
VERDICT_REFUSED = "refused"

_TRUTHY = {"1", "true", "yes", "on"}


def is_loopback(host: str) -> bool:
    """True for the addresses that only this box can reach."""
    h = (host or "").strip().lower()
    if h in LOOPBACK_HOSTS:
        return True
    # The whole 127/8 block is loopback, not just .0.0.1.
    return h.startswith("127.")


@dataclass(frozen=True)
class Exposure:
    """What a bind amounts to, and what to tell the operator about it."""
    verdict: str
    message: str

    @property
    def refused(self) -> bool:
        return self.verdict == VERDICT_REFUSED


def check_exposure(
    host: str,
    port: int,
    *,
    service: str,
    token: str = "",
    allow_open_lan: bool = False,
    env_prefix: str = "SEREN",
    supports_token: bool = True,
) -> Exposure:
    """Decide, without side effects, what binding `host:port` means.

    `token` is the RESOLVED bearer (after inline / keyring / env), not the
    config field - a pointer to an env var that is empty is still no token.
    `allow_open_lan` is the config field; `<env_prefix>_ALLOW_OPEN_LAN` in
    the environment counts as well, so a systemd unit can grant it without
    editing a yaml. `env_prefix` is the leaf's usual one (SEREN_LOCI,
    SEREN_MEMORY, ...), which keeps the override next to the leaf's other
    knobs and out of a shared name somebody could set once for everything.
    """
    tag = f"[{service}]"
    if is_loopback(host):
        return Exposure(VERDICT_LOOPBACK, f"{tag} listening on {host}:{port} (this box only)")

    if supports_token and token:
        return Exposure(
            VERDICT_LAN_WITH_TOKEN,
            f"{tag} listening on {host}:{port} - reachable from the network; "
            f"a bearer token is required on every request that is not public.")

    env_name = f"{env_prefix}_ALLOW_OPEN_LAN"
    env_allow = os.environ.get(env_name, "").strip().lower() in _TRUTHY
    if allow_open_lan or env_allow:
        how = f"{env_name} is set" if env_allow and not allow_open_lan else "allow_open_lan: true"
        return Exposure(
            VERDICT_LAN_OPEN_ALLOWED,
            "\n".join([
                f"{tag} ***  OPEN ON THE NETWORK  ***",
                f"{tag} listening on {host}:{port} with NO authentication, because {how}.",
                f"{tag} Anyone who can reach this machine can use this service as you.",
                f"{tag} This line prints on every boot until that changes.",
            ]))

    return Exposure(VERDICT_REFUSED, _refusal(tag, service, host, port, env_name, supports_token))


def _refusal(tag: str, service: str, host: str, port: int, env_name: str,
             supports_token: bool) -> str:
    # The env prefix's token variable is the leaf's convention already
    # (SEREN_LOCI_TOKEN and so on); derive a plausible one for the example.
    token_env = env_name.replace("_ALLOW_OPEN_LAN", "_TOKEN")
    lines = [
        f"{tag} REFUSING TO START.",
        "",
        f"  {service} was told to listen on {host}:{port}. That is every network",
        f"  interface on this box, not just this box - and there is no token on it.",
        f"  Anyone who can reach this machine could read and write everything this",
        f"  service holds, with nothing in the way.",
        "",
        "  Pick one and start again:",
        "",
        "    1. Keep it on this box (the default, and what most people want):",
        "         server:",
        "           host: 127.0.0.1",
        "",
    ]
    if supports_token:
        lines += [
            "    2. Put a token on it, THEN widen it:",
            "         server:",
            f"           host: {host}",
            f"           bearer_token_env: {token_env}     # or bearer_token / bearer_token_keyring",
            f"       and in the environment:  {token_env}=<something long and random>",
            "       Every client then sends  Authorization: Bearer <that value>.",
            "",
            "    3. You really do want it open - a trusted LAN, a lab, a demo:",
        ]
    else:
        lines += [
            f"    2. {service} has no bearer auth of its own, by design. If it must be",
            "       reachable from other machines, put something that authenticates in",
            "       front of it (a reverse proxy, an SSH tunnel), and then:",
            "",
            "    3. Say so, out loud:",
        ]
    lines += [
        "         server:",
        f"           host: {host}",
        "           allow_open_lan: true",
        f"       or set  {env_name}=1  in the environment.",
        "       It will start, and it will print a banner saying it is open, every boot.",
        "",
        "  Why this stops you instead of warning: a warning scrolls past. A network",
        "  full of your own data does not.",
    ]
    return "\n".join(lines)


def enforce_exposure(
    host: str,
    port: int,
    *,
    service: str,
    token: str = "",
    allow_open_lan: bool = False,
    env_prefix: str = "SEREN",
    supports_token: bool = True,
    log: Callable[[str], None] = print,
) -> str:
    """Print the verdict and, if the bind is refused, exit with EX_CONFIG.

    Call it BEFORE building the app: a refusal should cost nothing and
    arrive instantly, not after a model has been loaded. Returns the verdict
    name so a caller that wants to know can branch on it.
    """
    ex = check_exposure(host, port, service=service, token=token,
                        allow_open_lan=allow_open_lan, env_prefix=env_prefix,
                        supports_token=supports_token)
    log(ex.message)
    if ex.refused:
        raise SystemExit(EXIT_REFUSED)
    return ex.verdict


def enforce_server(server, *, service: str, env_prefix: str,
                   log: Callable[[str], None] = print) -> str:
    """The one-liner for a leaf that uses the shared ServerConfig.

    Pulls host, port, the RESOLVED bearer and allow_open_lan off the block.
    Works on anything with those attributes and a resolve_bearer() method,
    which includes the pydantic twins in Memory and Loci.
    """
    resolver: Optional[Callable[[], str]] = getattr(server, "resolve_bearer", None)
    token = resolver() if callable(resolver) else str(getattr(server, "bearer_token", "") or "")
    return enforce_exposure(
        str(server.host), int(server.port),
        service=service, token=token,
        allow_open_lan=bool(getattr(server, "allow_open_lan", False)),
        env_prefix=env_prefix, log=log,
    )
