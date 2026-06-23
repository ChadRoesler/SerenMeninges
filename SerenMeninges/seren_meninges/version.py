"""
seren_meninges.version
========================================================================

The six lines every service was copy-pasting. Resolve a package's installed
version once, the same way, with a graceful fallback for source checkouts.
"""
from __future__ import annotations


def get_version(distribution: str, *, fallback: str = "0.0.0") -> str:
    """Return the installed version of `distribution` (e.g. "seren-loci").

    Reads the metadata recorded in the installed wheel via
    importlib.metadata. In an un-installed source checkout there's no
    metadata, so it returns `fallback` - pass the setuptools-scm `_version`
    string there if you have one, else accept "0.0.0".

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
        return fallback
    except Exception:
        # Never let a version read crash startup - it's cosmetic.
        return fallback
