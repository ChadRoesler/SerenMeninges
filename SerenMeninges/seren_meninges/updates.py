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

NEVER IN MIDDLEWARE, NEVER IN THE HOT PATH
    A check is a network call. It happens on a TTL, behind a lock, and a
    stale answer is served instantly rather than making a caller wait. If
    the index is down, the service does not care.

THE STATUS IS ALWAYS EXPLICIT
    Every path returns an UpdateStatus with a `status` field, never a bare
    None. "I could not check" and "you are up to date" are different facts
    and callers must be able to tell them apart. This is deliberate: the
    family has been bitten by a graceful fallback that was indistinguishable
    from a feature being switched off, and a silent degradation is the worst
    possible failure mode because it looks like it is working.

OPT-IN
    Needs ``pip install seren-meninges[updates]`` (httpx + packaging). The
    imports are LAZY and inside the fetch, so importing this module always
    works - a missing extra reports status="unavailable" rather than blowing
    up at import time.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, asdict
from typing import Any, Awaitable, Callable, Optional

from .version import get_version

DEFAULT_INDEX_URL = "https://pypi.org/pypi/{distribution}/json"
DEFAULT_TTL_SECONDS = 6 * 60 * 60
DEFAULT_TIMEOUT_SECONDS = 3.0

STATUS_OK = "ok"                    # we asked and got an answer
STATUS_DISABLED = "disabled"        # operator turned it off
STATUS_UNAVAILABLE = "unavailable"  # the [updates] extra isn't installed
STATUS_ERROR = "error"              # we asked and it didn't work


@dataclass(frozen=True)
class UpdateStatus:
    """One answer. `status` is always set; the rest depends on it."""
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
        ...
        @app.get("/")
        async def info():
            return {"version": ..., "updates": (await checker.get()).as_dict()}
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
    ) -> None:
        self.distribution = distribution
        self.enabled = enabled
        self.index_url = index_url
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.allow_prerelease = allow_prerelease
        self._fallback_version = fallback_version
        # Injectable so tests never touch the network. Production default is
        # the httpx fetch below.
        self._fetch: Fetcher = fetcher or self._fetch_from_index
        self._cached: Optional[UpdateStatus] = None
        self._lock = asyncio.Lock()

    @property
    def installed(self) -> str:
        return get_version(self.distribution, fallback=self._fallback_version)

    async def get(self, *, force: bool = False) -> UpdateStatus:
        """Return the current status, refreshing only if the TTL has expired.

        Never raises. Never blocks longer than `timeout_seconds` on the wire.
        """
        if not self.enabled:
            return UpdateStatus(
                status=STATUS_DISABLED,
                distribution=self.distribution,
                installed=self.installed,
                detail="update checking is switched off in config",
            )

        if not force and self._is_fresh(self._cached):
            return self._cached  # type: ignore[return-value]

        # One refresh at a time. Everyone else waits for that one, then takes
        # the cache - so a burst of dashboard hits is one request, not N.
        async with self._lock:
            if not force and self._is_fresh(self._cached):
                return self._cached  # type: ignore[return-value]
            self._cached = await self._refresh()
            return self._cached

    def _is_fresh(self, s: Optional[UpdateStatus]) -> bool:
        if s is None or s.checked_at is None:
            return False
        return (time.monotonic() - s.checked_at) < self.ttl_seconds

    async def _refresh(self) -> UpdateStatus:
        installed = self.installed
        now = time.monotonic()
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
            # NOT an error - nothing is broken, the feature just isn't installed.
            # Distinct status so a dashboard can say so instead of showing green.
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
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            return resp.json()


class _ExtraMissing(RuntimeError):
    """The [updates] extra isn't installed. Reported, not raised at the caller."""


def _load_httpx():
    try:
        import httpx
        return httpx
    except ImportError as ex:
        raise _ExtraMissing(
            "update checking needs httpx - pip install 'seren-meninges[updates]'"
        ) from ex


def _load_packaging():
    try:
        from packaging.version import InvalidVersion, Version
        return Version, InvalidVersion
    except ImportError as ex:
        raise _ExtraMissing(
            "update checking needs packaging - pip install 'seren-meninges[updates]'"
        ) from ex
