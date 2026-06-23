"""
Tests for seren_meninges.version.get_version — installed-metadata read with a
graceful fallback, and the never-raises promise (version reads are cosmetic;
a bad one must never crash startup).
"""
from seren_meninges.version import get_version


def test_installed_dist_returns_real_version():
    # pytest is always present in the test env -> real metadata, not the fallback
    v = get_version("pytest")
    assert isinstance(v, str)
    assert v
    assert v != "0.0.0"


def test_missing_dist_returns_default_fallback():
    assert get_version("this-dist-does-not-exist-xyz") == "0.0.0"


def test_missing_dist_returns_custom_fallback():
    # the editable/source-checkout case: pass the scm version as fallback
    assert get_version("nope-not-real-xyz", fallback="9.9.9") == "9.9.9"


def test_never_raises_on_garbage_name():
    # an empty/invalid name still returns the fallback rather than raising
    assert get_version("", fallback="f") == "f"
