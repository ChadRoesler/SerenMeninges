"""
seren_meninges.viewer
========================================================================

THE BASEPLATE. Meninges owns ONE stable shell (shell.html) with a fixed set
of named studs. A leaf never opens the shell - it just drops conventionally
named fragment FILES into its own viewer folder, and `render_from_dir` snaps
whatever's present onto the baseplate. Missing brick -> empty stud, no error.

Lego, not copy-paste:

    leaf/ui/
      tabs.html      the tab buttons      (your section headers)
      body.html      the tab panels       (the content)
      styles.css     leaf CSS             (dropped into a <style> block)
      scripts.js     leaf tab logic       (dropped into the page <script>)
      head.html      extra <link>/<meta>  (rare; optional)

    # the leaf's GET /viewer route, in full:
    from seren_meninges.viewer import render_from_dir
    HTML = render_from_dir(Path(__file__).parent / "ui",
                           brand="The Bridge", accent="#9d7cff")

The baseplate provides (so the bricks don't have to): the page skeleton, the
header with brand/subtitle, the 🔑 token modal, the shared design tokens, and
the shell JS the bricks call - `api(path, opts)` (auto-attaches the saved
bearer token), `escapeHtml(s)`, and `showTab(id)`.

=== SKELETON === render path is real + tested (see tests/test_viewer.py). The
shipped shell/tokens are a starting skin to refine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional, Union

_HERE = Path(__file__).parent

# ── the studs: conventional fragment filename -> slot name ───────────────
# A leaf drops any SUBSET of these into its viewer dir. This mapping is the
# whole contract; expose it so a leaf can introspect / scaffold from it.
SLOT_FILES: dict[str, str] = {
    "head":    "head.html",     # extra <link>/<meta> for <head> (rare)
    "styles":  "styles.css",    # leaf CSS, dropped into a <style> block
    "tabs":    "tabs.html",     # the tab buttons (your section headers)
    "body":    "body.html",     # the tab panels (the content)
    "scripts": "scripts.js",    # leaf tab logic, dropped into the <script>
}

# slot name -> the marker it replaces in shell.html / tokens.css
_MARKERS: dict[str, str] = {
    "tokens":   "/*{{TOKENS_CSS}}*/",   # baseplate-owned (shared design tokens)
    "accent":   "{{ACCENT}}",
    "brand":    "{{BRAND}}",
    "subtitle": "{{SUBTITLE}}",
    "head":     "<!--{{EXTRA_HEAD}}-->",
    "styles":   "/*{{LEAF_STYLES}}*/",
    "tabs":     "<!--{{TABS}}-->",
    "body":     "<!--{{VIEWS}}-->",
    "scripts":  "//{{EXTRA_JS}}",
}


def render_shell(
    *,
    brand: str,
    accent: str = "#9d7cff",
    subtitle: str = "",
    head: str = "",
    styles: str = "",
    tabs: str = "",
    body: str = "",
    scripts: str = "",
) -> str:
    """Low-level filler: drop STRINGS into the baseplate's studs. Each arg is a
    fragment of markup/CSS/JS; an empty arg leaves that stud empty (no leftover
    marker). Most leaves use `render_from_dir` and never call this directly -
    it's the escape hatch for programmatically-built or inline content.
    """
    out = (_HERE / "shell.html").read_text(encoding="utf-8")
    out = out.replace(_MARKERS["tokens"], (_HERE / "tokens.css").read_text(encoding="utf-8"))
    out = out.replace(_MARKERS["accent"], accent)
    out = out.replace(_MARKERS["brand"], brand)
    out = out.replace(_MARKERS["subtitle"], subtitle)
    out = out.replace(_MARKERS["head"], head)
    out = out.replace(_MARKERS["styles"], styles)
    out = out.replace(_MARKERS["tabs"], tabs)
    out = out.replace(_MARKERS["body"], body)
    out = out.replace(_MARKERS["scripts"], scripts)
    return out


def render_from_dir(
    viewer_dir: Union[str, Path],
    *,
    brand: str,
    accent: str = "#9d7cff",
    subtitle: str = "",
    overrides: Optional[Mapping[str, str]] = None,
) -> str:
    """THE BASEPLATE. Point it at a folder of fragment files; it snaps whatever
    conventional bricks are present onto the shell and returns the full page.

    Convention (any subset; a missing file -> empty stud, never an error):

        tabs.html     the tab buttons  (your section headers)
        body.html     the tab panels   (the content)
        styles.css    leaf CSS         (into a <style> block)
        scripts.js    leaf tab logic   (into the page <script>)
        head.html     extra <link>/<meta> for <head> (rare)

    The leaf NEVER edits shell.html - it just maintains these files.

    `overrides` (optional): {slot: path-or-rawtext} to point a slot at a
    differently-named file, or to inject inline text for one slot. An existing
    file path is read; anything else is used verbatim. Lenient by design.
    """
    d = Path(viewer_dir)
    overrides = dict(overrides or {})
    filled: dict[str, str] = {}
    for slot, fname in SLOT_FILES.items():
        if slot in overrides:
            val = overrides[slot]
            p = Path(val)
            filled[slot] = p.read_text(encoding="utf-8") if p.is_file() else str(val)
        else:
            f = d / fname
            filled[slot] = f.read_text(encoding="utf-8") if f.is_file() else ""
    return render_shell(brand=brand, accent=accent, subtitle=subtitle, **filled)
