"""
Two small promises that each cost a real incident to learn.

The inline bearer token stays out of every repr, and a source checkout that
was never installed still knows its own version.
"""
from __future__ import annotations

import sys
import types

from seren_meninges.config import ServerConfig
from seren_meninges.version import get_version


def test_the_inline_token_never_appears_in_a_repr():
    cfg = ServerConfig.from_dict({"bearer_token": "hunter2", "bearer_token_env": "TOK"})
    text = repr(cfg) + str(cfg)
    assert "hunter2" not in text
    assert "TOK" in text, "the POINTER is not a secret and stays visible"
    assert cfg.resolve_bearer() == "hunter2"   # still works, just not printed


def test_a_source_checkout_reads_its_scm_stamp(monkeypatch):
    """No installed metadata, but a setuptools-scm _version.py in the package:
    that is what every dev checkout in the family looks like, and it used to
    report 0.0.0 - after which the update checker told every dev box it was
    out of date."""
    pkg = types.ModuleType("seren_ghostleaf")
    ver = types.ModuleType("seren_ghostleaf._version")
    ver.version = "4.2.0.dev3+gabc"
    monkeypatch.setitem(sys.modules, "seren_ghostleaf", pkg)
    monkeypatch.setitem(sys.modules, "seren_ghostleaf._version", ver)
    assert get_version("seren-ghostleaf") == "4.2.0.dev3+gabc"


def test_the_fallback_still_wins_when_there_is_no_stamp_either():
    assert get_version("seren-never-existed-xyz", fallback="f") == "f"


def test_installed_metadata_beats_the_stamp(monkeypatch):
    """pytest is installed here; a fake _version.py must not shadow the wheel."""
    import pytest as _pt
    ver = types.ModuleType("pytest._version")
    ver.version = "0.0.1-fake"
    monkeypatch.setitem(sys.modules, "pytest._version", ver)
    assert get_version("pytest") == _pt.__version__
