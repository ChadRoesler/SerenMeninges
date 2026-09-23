"""
Once warm, the update checker never makes a caller wait.

The first version awaited the index inline whenever the cache was stale, so
the first dashboard hit after six hours - from a PUBLIC route - stalled on a
DNS lookup and a round trip to PyPI. Now a stale cache is served instantly
while a refresh runs behind it; only a cold checker waits, and `start()`
exists so that wait happens in the lifespan rather than on a request.
"""
from __future__ import annotations

import asyncio
import time

from seren_meninges.updates import STATUS_OK, UpdateChecker

GHOST = "this-dist-does-not-exist-xyz"


def payload(v):
    return {"info": {"version": v}, "releases": {}}


class _Index:
    """A fetcher that can be made slow, and counts calls."""
    def __init__(self, version, delay=0.0):
        self.version, self.delay, self.calls = version, delay, 0

    async def __call__(self, distribution):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return payload(self.version)


async def test_a_stale_cache_is_served_instantly_and_refreshed_behind():
    idx = _Index("2.0.0")
    c = UpdateChecker(GHOST, fallback_version="1.0.0", ttl_seconds=0, fetcher=idx)
    first = await c.get()                      # cold: waits once
    assert first.latest == "2.0.0" and idx.calls == 1

    idx.version, idx.delay = "3.0.0", 0.2      # index now slow AND newer
    t0 = time.perf_counter()
    second = await c.get()                     # stale: must not wait 0.2s
    assert time.perf_counter() - t0 < 0.1
    assert second.latest == "2.0.0", "the stale answer is the instant answer"

    await asyncio.sleep(0.3)                   # let the background refresh land
    assert idx.calls == 2
    assert c.cached.latest == "3.0.0"


async def test_only_one_background_refresh_runs_at_a_time():
    idx = _Index("2.0.0", delay=0.1)
    c = UpdateChecker(GHOST, fallback_version="1.0.0", ttl_seconds=0, fetcher=idx)
    await c.get()
    for _ in range(10):
        await c.get()                          # ten stale hits
    await asyncio.sleep(0.2)
    assert idx.calls == 2, "ten stale hits are one refresh, not ten"


async def test_start_warms_the_cache_so_the_first_get_does_not_wait():
    idx = _Index("2.0.0", delay=0.1)
    c = UpdateChecker(GHOST, fallback_version="1.0.0", ttl_seconds=3600, fetcher=idx)
    await c.start()
    t0 = time.perf_counter()
    s = await c.get()
    assert time.perf_counter() - t0 < 0.05
    assert s.status == STATUS_OK and idx.calls == 1


async def test_start_is_a_noop_when_disabled_or_already_warm():
    idx = _Index("2.0.0")
    off = UpdateChecker(GHOST, enabled=False, fallback_version="1.0.0", fetcher=idx)
    await off.start()
    assert idx.calls == 0
    on = UpdateChecker(GHOST, fallback_version="1.0.0", fetcher=idx)
    await on.start()
    await on.start()
    assert idx.calls == 1


async def test_force_still_waits_for_a_fresh_answer():
    idx = _Index("2.0.0")
    c = UpdateChecker(GHOST, fallback_version="1.0.0", ttl_seconds=3600, fetcher=idx)
    await c.get()
    idx.version = "3.0.0"
    assert (await c.get(force=True)).latest == "3.0.0"


async def test_checked_at_is_wall_clock():
    """A dashboard renders 'checked N minutes ago' from this; a monotonic
    number meant nothing to anyone outside the process."""
    c = UpdateChecker(GHOST, fallback_version="1.0.0", fetcher=_Index("2.0.0"))
    s = await c.get()
    assert abs(s.checked_at - time.time()) < 5


async def test_a_checker_built_outside_any_loop_still_works():
    """The lock is created on first use, inside the running loop. Building
    the checker at import time - which is what a module-level
    `app.state.updates = UpdateChecker(...)` amounts to - must not bind it to
    a loop that later goes away."""
    c = UpdateChecker(GHOST, fallback_version="1.0.0", fetcher=_Index("2.0.0"))
    assert c._lock is None
    assert (await c.get()).status == STATUS_OK
