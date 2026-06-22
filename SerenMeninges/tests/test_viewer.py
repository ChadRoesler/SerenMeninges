"""
Tests for the viewer baseplate — render_from_dir snaps conventional fragment
files onto the shared shell. Missing fragments degrade to empty slots (never
errors), no leftover markers survive, and the baseplate JS stays intact.
"""
from seren_meninges.viewer import SLOT_FILES, render_from_dir, render_shell


def _write(d, name, text):
    (d / name).write_text(text, encoding="utf-8")


def test_render_from_dir_snaps_present_bricks(tmp_path):
    _write(tmp_path, "tabs.html", '<button class="tab" data-tab="t1">T1</button>')
    _write(tmp_path, "body.html", '<section class="view" id="t1">hi</section>')
    _write(tmp_path, "scripts.js", 'function go(){ api("/x"); }')
    html = render_from_dir(tmp_path, brand="Brand", accent="#abcdef")
    assert 'data-tab="t1"' in html      # tabs snapped in
    assert 'id="t1"' in html            # body snapped in
    assert "function go(" in html       # scripts snapped in
    assert "Brand" in html
    assert "#abcdef" in html
    assert "function api(" in html      # baseplate JS intact
    assert "{{" not in html             # no leftover markers


def test_missing_fragments_are_empty_not_errors(tmp_path):
    # only tabs present; no body/styles/scripts/head -> empty studs, no raise
    _write(tmp_path, "tabs.html", "<button>only</button>")
    html = render_from_dir(tmp_path, brand="B")
    assert "<button>only</button>" in html
    assert "{{" not in html


def test_empty_dir_renders_bare_baseplate(tmp_path):
    # no bricks at all -> still a valid page (just the shell)
    html = render_from_dir(tmp_path, brand="Empty")
    assert "Empty" in html
    assert "function showTab(" in html
    assert "{{" not in html


def test_overrides_inline_text(tmp_path):
    html = render_from_dir(tmp_path, brand="B", overrides={"tabs": "<i>inline</i>"})
    assert "<i>inline</i>" in html


def test_overrides_alternate_filename(tmp_path):
    (tmp_path / "weird-name.html").write_text("<p>alt</p>", encoding="utf-8")
    html = render_from_dir(
        tmp_path, brand="B", overrides={"body": str(tmp_path / "weird-name.html")}
    )
    assert "<p>alt</p>" in html


def test_render_shell_strings_directly():
    html = render_shell(brand="X", tabs="<b>tab</b>", body="<i>body</i>")
    assert "<b>tab</b>" in html
    assert "<i>body</i>" in html
    assert "{{" not in html


def test_slot_files_contract_is_stable():
    # Leaves rely on these names; a rename or removal is breaking. Additions
    # are fine (a new optional slot is backwards-compatible), so we check that
    # each known slot is PRESENT, not that the set is exactly equal.
    expected = {
        "tabs": "tabs.html",
        "body": "body.html",
        "styles": "styles.css",
        "scripts": "scripts.js",
        "head": "head.html",
    }
    for slot, fname in expected.items():
        assert SLOT_FILES.get(slot) == fname
