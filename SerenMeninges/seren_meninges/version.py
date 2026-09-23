"""
seren_meninges.version
========================================================================

The six lines every service was copy-pasting. Resolve a package's installed
version once, the same way, with a graceful fallback for source checkouts.
"""
from __future__ import annotations


def get_version(distribution: str, *, fallback: str = "0.0.0") -> str:
    """Return the installed version of `distribution` (e.g. "seren-loci").

    Three sources, in order:

    1. The metadata recorded in the installed wheel, via importlib.metadata.
       This is the answer on every real install, editable ones included.
    2. The package's own setuptools-scm ``_version.py``, for a source checkout
       that was never installed. Every Seren package writes one at
       ``<pkg>/_version.py`` with a ``version`` attribute, and the import
       name is the distribution name with dashes swapped for underscores -
       that is the family convention, and this leans on it. Without this
       step a checkout reported "0.0.0", and the update checker then compared
       every published release against 0.0.0 and told every dev box it was
       out of date.
    3. `fallback`.

    NOTE this takes the *leaf's* distribution name, not Meninges'. Each
    service calls ``get_version("seren-<thing>")`` for its OWN version; the
    helper is shared, the answer is per-package.
    """
    # An empty / None name is garbage input, and importlib.metadata's behaviour
    # for it is environment-dependent: Python <= 3.11 may loosely match an
    # editable-install finder and return *some* dist's version, while 3.12+
    # raises ValueError. Short-circuit to a deterministic answer.
    if not distribution:
        return fallback
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version(distribution)
    except PackageNotFoundError:
        pass
    except Exception:
        # Never let a version read crash startup - it's cosmetic.
        return fallback
    scm = _scm_version(distribution)
    return scm if scm else fallback


def _scm_version(distribution: str) -> str:
    """The setuptools-scm stamp inside the package, or "" if there is none."""
    module_name = distribution.replace("-", "_") + "._version"
    try:
        import importlib
        mod = importlib.import_module(module_name)
    except Exception:
        return ""
    value = getattr(mod, "version", None) or getattr(mod, "__version__", None)
    return str(value) if value else ""
