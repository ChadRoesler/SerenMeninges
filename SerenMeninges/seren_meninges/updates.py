"""
seren_meninges.updates
========================================================================

The other half of ``get_version()``. That one answers "what am I running";
this one answers "is there something newer", by asking the index the package
was installed from.

WHY THIS LIVES IN MENINGES AND NOT SINEW
    It is the same concern - PACKAGE IDENTITY - asked about the far end of
    the wire. Meninges already owns get_version (what's installed), config
    (where the knobs live), credentials (for a private index) and the viewer
    shell (where a badge renders). Sinew is request-path plumbing; a version
    poll must never touch the request path.

NEVER IN MIDDLEWARE, AND NEVER A WAIT ON THE REQUEST PATH ONCE WARM
    A check is a network call. It happens on a TTL, behind a lock, and once
    there is ANY answer in the cache that answer is what a caller gets -
    instantly, even after the TTL has lapsed - while a refresh runs in the
    background. The first version awaited the refresh inline whenever the
    cache was stale, so the first dashboard hit after six hours paid for a
    DNS lookup and a round trip to PyPI on a Jetson, from a route that is
    public. Now only a COLD checker can wait, and only once: call ``start()``
    from the service's lifespan and even that wait happens before the first
    request arrives. ``get(force=True)`` is the operator's "check now" and
    does wait, on purpose.

THE STATUS IS ALWAYS EXPLICIT
    Every path returns an UpdateStatus with a `status` field, never a bare
    None. "I could not check" and "you are up to date" are different facts
    and callers must be able to tell them apart. This is deliberate: the
    family has been bitten by a graceful fallback that was indistinguishable
    from a feature being switched off, and a silent degradation is the worst
    possible failure mode because it looks like it is working.

CORE, NOT AN EXTRA
    httpx and packaging are core dependencies of seren-meninges (see
    pyproject.toml for the reversal and why). The imports stay lazy and
    inside the fetch so that a broken install still reports
    status="unavailable" instead of blowing up at import time. Switch the
    check off with ``enabled=False`` (every leaf wires that to
    ``updates.enabled`` in its yaml and to ``SEREN_<X>_UPDATES_ENABLED``).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, asdict
from typing import Any, Awaitable, Callable, Optional

from .version import get_version

log = logging.getLogger("seren_meninges.updates")

DEFAULT_INDEX_URL = "https://pypi.org/pypi/{distribution}/json"
DEFAULT_TTL_SECONDS = 6 * 60 * 60
DEFAULT_TIMEOUT_SECONDS = 3.0

STATUS_OK = "ok"                    # we asked and got an answer
STATUS_DISABLED = "disabled"        # operator turned it off
STATUS_UNAVAILABLE = "unavailable"  # the deps are missing (a broken install)
STATUS_ERROR = "error"              # we asked and it didn't work


@dataclass(frozen=True)
class UpdateStatus:
    """One answer. `status` is always set; the rest depends on it.

    `checked_at` is WALL-CLOCK epoch seconds (``time.time()``), so a
    dashboard can render "checked 40 minutes ago". It was monotonic once,
    which meant nothing to anyone outside the process.
    """
    status: str
    distribution: str
    installed: str = ""
    latest: Optional[str] = None
    update_available: bool = False
    detail: Optional[str] = None
    checked_at: Optional[float] = None

    def as_dict(self) -> dict:
        return asdict(self)


Fetcher = Callable[[str], Awaitable[dict]]


class UpdateChecker:
    """TTL-cached "is there a newer release of me" check for one distribution.

    Typical wiring in a leaf service::

        checker = UpdateChecker("seren-lodestar")
        app.state.updates = checker
        await checker.start()           # in the lifespan: warm before traffic
        ...
        @app.get("/")
        async def info():
            return {"version": ..., "updates": (await checker.get()).as_dict()}

    `trust_system_store` routes the index call's TLS verification through the
    OS trust store via ``truststore`` (the ``[corp]`` extra) - the thing
    ``TlsConfig.trust_system_store`` exists to say. Most leaves inject
    truststore process-wide in their ``__main__`` before anything else, which
    covers this client too; passing it here makes the checker correct on its
    own, without depending on that ordering.
    """

    def __init__(
        self,
        distribution: str,
        *,
        enabled: bool = True,
        index_url: str = DEFAULT_INDEX_URL,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        allow_prerelease: bool = False,
        fallback_version: str = "0.0.0",
        fetcher: Optional[Fetcher] = None,
        trust_system_store: bool = False,
    ) -> None:
        self.distribution = distribution
        self.enabled = enabled
        self.index_url = index_url
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.allow_prerelease = allow_prerelease
        self.trust_system_store = trust_system_store
        self._fallback_version = fallback_version
        # Injectable so tests never touch the network. Production default is
        # the httpx fetch below.
        self._fetch: Fetcher = fetcher or self._fetch_from_index
        self._cached: Optional[UpdateStatus] = None
        # Monotonic, for freshness. `checked_at` on the status is wall-clock
        # for people; this is for the TTL, which must not care about NTP.
        self._refreshed_mono: Optional[float] = None
        # Created on first use, INSIDE a running loop. An asyncio.Lock built
        # in __init__ binds to whichever loop touches it first, and a checker
        # shared across loops (tests, or a service that restarts its loop)
        # then raises from a method that promises never to.
        self._lock: Optional[asyncio.Lock] = None
        self._background: Optional[asyncio.Task] = None

    @property
    def installed(self) -> str:
        return get_version(self.distribution, fallback=self._fallback_version)

    @property
    def cached(self) -> Optional[UpdateStatus]:
        """The last answer, however old. None until the first refresh lands."""
        return self._cached

    async def start(self) -> None:
        """Warm the cache before the first request. Call it from the lifespan.

        Waits for the first answer (bounded by `timeout_seconds`), so by the
        time the service accepts traffic `get()` never has a reason to wait.
        Never raises.
        """
        if not self.enabled or self._cached is not None:
            return
        await self._refresh_locked()

    async def get(self, *, force: bool = False) -> UpdateStatus:
        """Return the current status. Never raises.

        - `force=True`: refresh now and wait for it (the operator's button).
        - Cache fresh: return it.
        - Cache stale: return it NOW and refresh in the background.
        - Cache empty: refresh and wait, once - bounded by `timeout_seconds`.
          `start()` in the lifespan is how a service makes sure this branch
          runs before the first request rather than during it.
        """
        if not self.enabled:
            return UpdateStatus(
                status=STATUS_DISABLED,
                distribution=self.distribution,
                installed=self.installed,
                detail="update checking is switched off in config",
            )

        if force:
            return await self._refresh_locked(force=True)

        if self._cached is None:
            return await self._refresh_locked()

        if not self._is_fresh():
            self._kick_background_refresh()
        return self._cached

    # ── the machinery ────────────────────────────────────────────────────

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _is_fresh(self) -> bool:
        if self._cached is None or self._refreshed_mono is None:
            return False
        return (time.monotonic() - self._refreshed_mono) < self.ttl_seconds

    async def _refresh_locked(self, *, force: bool = False) -> UpdateStatus:
        """One refresh at a time. A burst of waiters shares the one request;
        `force` is the operator asking, and skips the fresh-enough shortcut."""
        try:
            async with self._get_lock():
                if not force and self._cached is not None and self._is_fresh():
                    return self._cached
                self._cached = await self._refresh()
                self._refreshed_mono = time.monotonic()
                return self._cached
        except Exception as ex:  # pragma: no cover - the promise, belt and braces
            return UpdateStatus(
                status=STATUS_ERROR, distribution=self.distribution,
                installed=self.installed, detail=f"{type(ex).__name__}: {ex}",
                checked_at=time.time(),
            )

    def _kick_background_refresh(self) -> None:
        """Start ONE refresh task if none is running. Fire-and-forget by
        design: the caller already has an answer in hand."""
        task = self._background
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - get() is always awaited
            return
        self._background = loop.create_task(self._refresh_locked())
        # A refresh never raises (see _refresh), but a cancelled task at
        # shutdown would log an unretrieved exception; swallow that noise.
        self._background.add_done_callback(_consume_task_result)

    async def _refresh(self) -> UpdateStatus:
        installed = self.installed
        now = time.time()
        try:
            payload = await asyncio.wait_for(
                self._fetch(self.distribution), timeout=self.timeout_seconds
            )
            latest = self._pick_latest(payload)
            if latest is None:
                return UpdateStatus(
                    status=STATUS_ERROR, distribution=self.distribution,
                    installed=installed, detail="index returned no usable version",
                    checked_at=now,
                )
            newer = self._is_newer(latest, installed)
            return UpdateStatus(
                status=STATUS_OK, distribution=self.distribution,
                installed=installed, latest=latest,
                update_available=newer, checked_at=now,
            )
        except _ExtraMissing as ex:
            # NOT an error - nothing is broken in the service, its install is
            # incomplete. Distinct status so a dashboard can say so instead of
            # showing green.
            return UpdateStatus(
                status=STATUS_UNAVAILABLE, distribution=self.distribution,
                installed=installed, detail=str(ex), checked_at=now,
            )
        except asyncio.TimeoutError:
            return UpdateStatus(
                status=STATUS_ERROR, distribution=self.distribution,
                installed=installed,
                detail=f"index did not answer within {self.timeout_seconds}s",
                checked_at=now,
            )
        except Exception as ex:
            # Same promise get_version makes: this is cosmetic, it never
            # takes the service down.
            return UpdateStatus(
                status=STATUS_ERROR, distribution=self.distribution,
                installed=installed, detail=f"{type(ex).__name__}: {ex}",
                checked_at=now,
            )

    def _pick_latest(self, payload: dict) -> Optional[str]:
        """Newest usable version from a PyPI-shaped JSON payload.

        Fast path is `info.version`. We only walk `releases` when that turns
        out to be a prerelease we're not allowed to offer - which also gets
        us yank-awareness for free on the slow path.
        """
        Version, InvalidVersion = _load_packaging()
        info_v = (payload.get("info") or {}).get("version")
        if info_v:
            try:
                if self.allow_prerelease or not Version(str(info_v)).is_prerelease:
                    return str(info_v)
            except InvalidVersion:
                pass  # fall through to the scan

        best = None
        for raw, files in (payload.get("releases") or {}).items():
            try:
                v = Version(str(raw))
            except InvalidVersion:
                continue
            if v.is_prerelease and not self.allow_prerelease:
                continue
            # A release whose every file is yanked is not a release you want
            # to be told to upgrade to.
            if isinstance(files, list) and files and all(
                isinstance(f, dict) and f.get("yanked") for f in files
            ):
                continue
            if best is None or v > best:
                best = v
        return str(best) if best is not None else None

    def _is_newer(self, latest: str, installed: str) -> bool:
        """Compare as VERSIONS, never as strings. '1.10.0' < '1.9.0' lexically."""
        Version, InvalidVersion = _load_packaging()
        try:
            return Version(latest) > Version(installed)
        except InvalidVersion:
            return False

    async def _fetch_from_index(self, distribution: str) -> dict:
        httpx = _load_httpx()
        url = self.index_url.format(distribution=distribution)
        async with httpx.AsyncClient(timeout=self.timeout_seconds,
                                     verify=_verify_for(self.trust_system_store)) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            return resp.json()


def _consume_task_result(task: "asyncio.Task") -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:  # pragma: no cover
        pass


def _verify_for(trust_system_store: bool):
    """What to hand httpx as `verify`. True (certifi) unless the operator asked
    for the OS trust store, in which case a truststore SSLContext - or True
    with a logged note if truststore is not installed, so a corp box fails
    with a certificate error it can read rather than a silent downgrade."""
    if not trust_system_store:
        return True
    try:
        import ssl
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        log.warning("tls.trust_system_store is on but 'truststore' is not "
                    "installed - pip install 'seren-meninges[corp]'; using "
                    "certifi for the update check")
        return True


async def updates_payload(
    checker: Optional[UpdateChecker],
    *,
    distribution: str,
    installed: str,
) -> dict:
    """The JSON block a service hangs off its info route.

    Every service in the family reports the SAME seven keys with the same
    meanings, whether or not the lifespan managed to build a checker. Pass
    ``getattr(app.state, "updates", None)`` straight in - a None checker is a
    normal state, not an error, and it gets its own status rather than a null.

    This lives here rather than being copy-pasted into each leaf because the
    not-wired branch is exactly the bit that's easy to get subtly wrong, and a
    service that omits the key (or returns None) reads as "you're fine" to
    whatever renders it.
    """
    if checker is None:
        return UpdateStatus(
            status=STATUS_UNAVAILABLE,
            distribution=distribution,
            installed=installed,
            # The word "updates" stays in this string: the leaves' own
            # test_info_updates pins it as the marker of the not-wired branch.
            detail=f"updates: no checker was built at startup for {distribution} "
                   f"- the service's lifespan skipped it; check its startup log",
        ).as_dict()
    return (await checker.get()).as_dict()


class _ExtraMissing(RuntimeError):
    """A core dependency of the checker is missing. Reported, not raised at
    the caller - and since httpx and packaging are core, this means the
    install itself is incomplete."""


def _load_httpx():
    try:
        import httpx
        return httpx
    except ImportError as ex:
        raise _ExtraMissing(
            "update checking needs httpx, which is a core dependency of "
            "seren-meninges - this install is incomplete: "
            "pip install --force-reinstall seren-meninges"
        ) from ex


def _load_packaging():
    try:
        from packaging.version import InvalidVersion, Version
        return Version, InvalidVersion
    except ImportError as ex:
        raise _ExtraMissing(
            "update checking needs packaging, which is a core dependency of "
            "seren-meninges - this install is incomplete: "
            "pip install --force-reinstall seren-meninges"
        ) from ex
