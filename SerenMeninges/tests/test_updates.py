"""
Tests for seren_meninges.updates.

The contract under test: the checker ALWAYS returns an UpdateStatus with an
explicit `status`, never raises into the caller, never string-compares
versions, and never hits the network more than the TTL allows.

No test touches the real network - every one injects a fetcher.
"""
from __future__ import annotations

import asyncio

import pytest

from seren_meninges import updates as U
from seren_meninges.updates import (
    STATUS_DISABLED, STATUS_ERROR, STATUS_OK, STATUS_UNAVAILABLE,
    UpdateChecker, updates_payload,
)

EXPECTED_KEYS = {"status", "distribution", "installed", "latest",
                 "update_available", "detail", "checked_at"}

# A distribution name that is guaranteed not installed, so `installed` comes
# from fallback_version and every test is deterministic.
GHOST = "this-dist-does-not-exist-xyz"


def payload(info_version=None, releases=None):
    return {"info": {"version": info_version}, "releases": releases or {}}


def fetcher_returning(p, counter=None):
    async def _f(distribution):
        if counter is not None:
            counter.append(distribution)
        return p
    return _f


# ── the happy paths ────────────────────────────────────────────────────

async def test_newer_release_is_reported():
    c = UpdateChecker(GHOST, fallback_version="1.2.0",
                      fetcher=fetcher_returning(payload("1.3.0")))
    s = await c.get()
    assert s.status == STATUS_OK
    assert s.installed == "1.2.0"
    assert s.latest == "1.3.0"
    assert s.update_available is True


async def test_up_to_date_is_not_an_update():
    c = UpdateChecker(GHOST, fallback_version="1.3.0",
                      fetcher=fetcher_returning(payload("1.3.0")))
    s = await c.get()
    assert s.status == STATUS_OK
    assert s.update_available is False


async def test_installed_ahead_of_index_is_not_an_update():
    # local dev build newer than what's published
    c = UpdateChecker(GHOST, fallback_version="2.0.0",
                      fetcher=fetcher_returning(payload("1.3.0")))
    assert (await c.get()).update_available is False


async def test_versions_compare_numerically_not_lexically():
    """The whole reason `packaging` is a dependency: '1.10.0' < '1.9.0' as
    strings, which would silently hide every tenth minor release."""
    c = UpdateChecker(GHOST, fallback_version="1.9.0",
                      fetcher=fetcher_returning(payload("1.10.0")))
    s = await c.get()
    assert s.latest == "1.10.0"
    assert s.update_available is True


# ── prereleases ────────────────────────────────────────────────────────

async def test_prerelease_is_skipped_by_default():
    """mcp 2.0.0rc1 must not read as an upgrade from 1.29.0 - the exact shape
    of the break that started this feature."""
    p = payload("2.0.0rc1", {"1.29.0": [{"yanked": False}],
                             "2.0.0rc1": [{"yanked": False}]})
    c = UpdateChecker(GHOST, fallback_version="1.28.0", fetcher=fetcher_returning(p))
    s = await c.get()
    assert s.latest == "1.29.0"
    assert s.update_available is True


async def test_prerelease_is_offered_when_asked_for():
    p = payload("2.0.0rc1", {"1.29.0": [{"yanked": False}],
                             "2.0.0rc1": [{"yanked": False}]})
    c = UpdateChecker(GHOST, fallback_version="1.28.0", allow_prerelease=True,
                      fetcher=fetcher_returning(p))
    assert (await c.get()).latest == "2.0.0rc1"


async def test_fully_yanked_release_is_not_offered():
    p = payload("2.0.0rc1", {"1.29.0": [{"yanked": False}],
                             "1.30.0": [{"yanked": True}]})
    c = UpdateChecker(GHOST, fallback_version="1.0.0", fetcher=fetcher_returning(p))
    assert (await c.get()).latest == "1.29.0"


async def test_unparseable_versions_are_ignored():
    p = payload("not-a-version", {"1.2.0": [{"yanked": False}],
                                  "banana": [{"yanked": False}]})
    c = UpdateChecker(GHOST, fallback_version="1.0.0", fetcher=fetcher_returning(p))
    s = await c.get()
    assert s.status == STATUS_OK
    assert s.latest == "1.2.0"


async def test_no_usable_version_is_an_error_not_a_crash():
    c = UpdateChecker(GHOST, fallback_version="1.0.0",
                      fetcher=fetcher_returning(payload(None, {})))
    s = await c.get()
    assert s.status == STATUS_ERROR
    assert s.update_available is False


# ── the never-raises promise ───────────────────────────────────────────

async def test_network_failure_becomes_status_error():
    async def boom(distribution):
        raise ConnectionError("index unreachable")
    c = UpdateChecker(GHOST, fallback_version="1.0.0", fetcher=boom)
    s = await c.get()
    assert s.status == STATUS_ERROR
    assert "ConnectionError" in s.detail
    assert s.installed == "1.0.0"      # we still know what WE are


async def test_slow_index_times_out_into_status_error():
    async def slow(distribution):
        await asyncio.sleep(5)
        return payload("9.9.9")
    c = UpdateChecker(GHOST, fallback_version="1.0.0",
                      timeout_seconds=0.05, fetcher=slow)
    s = await c.get()
    assert s.status == STATUS_ERROR
    assert "did not answer" in s.detail


# ── disabled / unavailable are DISTINCT from error and from ok ─────────

async def test_disabled_says_disabled():
    c = UpdateChecker(GHOST, enabled=False, fallback_version="1.0.0",
                      fetcher=fetcher_returning(payload("2.0.0")))
    s = await c.get()
    assert s.status == STATUS_DISABLED
    assert s.latest is None
    assert s.update_available is False


async def test_missing_extra_says_unavailable_not_error(monkeypatch):
    """A missing extra must not look like a working check that found nothing.
    This is the whole reason status is an enum and not a bool."""
    def no_httpx():
        raise U._ExtraMissing("update checking needs httpx - pip install ...")
    monkeypatch.setattr(U, "_load_httpx", no_httpx)
    c = UpdateChecker(GHOST, fallback_version="1.0.0")   # default fetcher
    s = await c.get()
    assert s.status == STATUS_UNAVAILABLE
    assert "httpx" in s.detail


async def test_import_of_the_module_works_without_the_extra():
    """updates.py must import on a bare install - the extras are loaded lazily
    inside the fetch, never at module scope."""
    import importlib
    assert importlib.import_module("seren_meninges.updates") is U


# ── the TTL cache: this must never be a per-request network call ───────

async def test_second_call_within_ttl_does_not_refetch():
    calls = []
    c = UpdateChecker(GHOST, fallback_version="1.0.0", ttl_seconds=3600,
                      fetcher=fetcher_returning(payload("2.0.0"), calls))
    await c.get()
    await c.get()
    await c.get()
    assert len(calls) == 1


async def test_force_refetches():
    calls = []
    c = UpdateChecker(GHOST, fallback_version="1.0.0", ttl_seconds=3600,
                      fetcher=fetcher_returning(payload("2.0.0"), calls))
    await c.get()
    await c.get(force=True)
    assert len(calls) == 2


async def test_expired_ttl_refetches():
    calls = []
    c = UpdateChecker(GHOST, fallback_version="1.0.0", ttl_seconds=0,
                      fetcher=fetcher_returning(payload("2.0.0"), calls))
    await c.get()
    await c.get()
    assert len(calls) == 2


async def test_concurrent_callers_collapse_into_one_fetch():
    """A burst of dashboard hits is ONE index request, not N."""
    calls = []

    async def slowish(distribution):
        calls.append(distribution)
        await asyncio.sleep(0.05)
        return payload("2.0.0")

    c = UpdateChecker(GHOST, fallback_version="1.0.0", ttl_seconds=3600,
                      fetcher=slowish)
    results = await asyncio.gather(*(c.get() for _ in range(10)))
    assert len(calls) == 1
    assert all(r.update_available for r in results)


# ── updates_payload: the block every service hangs off its info route ──

async def test_payload_without_a_checker_says_unavailable():
    """A service whose lifespan couldn't build a checker still has to answer.
    Omitting the key, or returning null, reads as "you're fine" to a renderer -
    which is a claim we are not entitled to make."""
    d = await updates_payload(None, distribution="seren-loci", installed="1.2.0")
    assert d["status"] == STATUS_UNAVAILABLE
    assert d["installed"] == "1.2.0"
    assert d["update_available"] is False
    assert "seren-loci[updates]" in d["detail"], "tell them how to fix it"


async def test_payload_with_a_checker_reports_the_answer():
    c = UpdateChecker(GHOST, fallback_version="1.2.0",
                      fetcher=fetcher_returning(payload("1.3.0")))
    d = await updates_payload(c, distribution="seren-loci", installed="1.2.0")
    assert d["status"] == STATUS_OK
    assert d["latest"] == "1.3.0"
    assert d["update_available"] is True


async def test_payload_key_set_is_identical_across_every_branch():
    """THE contract. A dashboard badge reads these keys unconditionally, so
    wired / not-wired / disabled / errored must be the same shape."""
    async def boom(distribution):
        raise ConnectionError("no network")
    branches = [
        await updates_payload(None, distribution="x", installed="1.0.0"),
        await updates_payload(UpdateChecker(GHOST, fallback_version="1.0.0",
                              fetcher=fetcher_returning(payload("2.0.0"))),
                              distribution="x", installed="1.0.0"),
        await updates_payload(UpdateChecker(GHOST, enabled=False, fallback_version="1.0.0"),
                              distribution="x", installed="1.0.0"),
        await updates_payload(UpdateChecker(GHOST, fallback_version="1.0.0", fetcher=boom),
                              distribution="x", installed="1.0.0"),
    ]
    for d in branches:
        assert set(d) == EXPECTED_KEYS
        assert isinstance(d["update_available"], bool)
    assert {d["status"] for d in branches} == {
        STATUS_UNAVAILABLE, STATUS_OK, STATUS_DISABLED, STATUS_ERROR}


async def test_payload_never_reports_an_update_it_could_not_verify():
    """Every non-ok branch must be update_available=False. A green tick on a
    box that has no idea whether it's current is the failure this whole module
    exists to avoid."""
    async def boom(distribution):
        raise ConnectionError("no network")
    for c in (None,
              UpdateChecker(GHOST, enabled=False, fallback_version="1.0.0"),
              UpdateChecker(GHOST, fallback_version="1.0.0", fetcher=boom)):
        d = await updates_payload(c, distribution="x", installed="1.0.0")
        assert d["status"] != STATUS_OK
        assert d["update_available"] is False


async def test_as_dict_is_json_shaped():
    c = UpdateChecker(GHOST, fallback_version="1.0.0",
                      fetcher=fetcher_returning(payload("2.0.0")))
    d = (await c.get()).as_dict()
    assert d["status"] == STATUS_OK
    assert d["installed"] == "1.0.0"
    assert d["latest"] == "2.0.0"
    assert d["update_available"] is True
    assert set(d) == {"status", "distribution", "installed", "latest",
                      "update_available", "detail", "checked_at"}
