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


# ── the shell's polling pair, run for real ───────────────────────────────
# watch() and keepingScroll() are behaviour, not markup, so a string check
# proves nothing. The shell's own <script> is lifted out of a rendered page
# and run under node against a stub browser. No node on the box -> skipped.
import json
import re
import shutil
import subprocess

import pytest

_HARNESS = r"""
const calls = [];
let answers = [];                       // queue: a value, or an Error to throw
const node = () => ({ addEventListener(){}, classList: { add(){}, remove(){}, toggle(){} },
                      scrollTop: 0, scrollLeft: 0 });
const nodes = {};
globalThis.document = {
  getElementById: () => node(), addEventListener(){},
  querySelectorAll: () => [], querySelector: sel => nodes[sel] || null,
};
globalThis.localStorage = { getItem: () => "", setItem(){}, removeItem(){} };
globalThis.window = { scrollX: 3, scrollY: 40, scrollTo(x, y){ this.scrollX = x; this.scrollY = y; } };
globalThis.fetch = async path => {
  calls.push(path);
  const a = answers.length > 1 ? answers.shift() : answers[0];
  if (a instanceof Error) throw a;
  return { ok: true, headers: { get: () => "application/json" }, json: async () => JSON.parse(JSON.stringify(a)) };
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
//SHELL//
(async () => {
  const out = {};

  // same answer three times, then a new one: painted twice
  let painted = [];
  answers = [{ n: 1 }, { n: 1 }, { n: 1 }, { n: 2 }];
  let stop = watch("/a", 10, d => painted.push(d.n));
  await sleep(120); stop();
  out.painted = painted;
  const polled = calls.length;
  await sleep(40);
  out.stopped = calls.length === polled;
  out.polled_more_than_painted = polled > painted.length;

  // an error is reported, and the same answer is painted again after it
  painted = []; const errors = [];
  answers = [{ n: 1 }, new Error("down"), { n: 1 }];
  stop = watch("/b", 10, d => painted.push(d.n), e => errors.push(e.message));
  await sleep(80); stop();
  out.after_error = painted; out.errors = errors;

  // a url function: falsy sits the round out, a moved url repaints
  painted = []; let where = null; const before = calls.length;
  answers = [{ n: 7 }];
  stop = watch(() => where, 10, d => painted.push(where));
  await sleep(30);
  out.sat_out = calls.length === before && painted.length === 0;
  where = "/c"; await stop.check(); where = "/d"; await stop.check(); await stop.check();
  stop();
  out.moved = painted;

  // keepingScroll: an element, a replaced node found by selector, the window, async, a throw
  const el = { scrollTop: 120, scrollLeft: 5 };
  out.ret = keepingScroll(el, () => { el.scrollTop = 0; window.scrollTo(0, 0); return "r"; });
  out.el = [el.scrollTop, el.scrollLeft, window.scrollX, window.scrollY];
  nodes[".paper"] = { scrollTop: 77, scrollLeft: 0 };
  keepingScroll(".paper", () => { nodes[".paper"] = { scrollTop: 0, scrollLeft: 0 }; });
  out.replaced = nodes[".paper"].scrollTop;
  await keepingScroll(el, async () => { await sleep(5); el.scrollTop = 0; });
  out.async = el.scrollTop;
  try { keepingScroll(el, () => { el.scrollTop = 0; throw new Error("x"); }); } catch (_) {}
  out.thrown = el.scrollTop;
  out.none = keepingScroll(null, () => 1);

  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def shell_js_result(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    html = render_shell(brand="X")
    js = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    script = tmp_path_factory.mktemp("shelljs") / "run.js"
    script.write_text(_HARNESS.replace("//SHELL//", js), encoding="utf-8")
    done = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_watch_paints_only_when_the_answer_changed(shell_js_result):
    r = shell_js_result
    assert r["painted"] == [1, 2]
    assert r["polled_more_than_painted"]
    assert r["stopped"]                      # stop() really stops the timer


def test_watch_reports_an_error_and_repaints_after_it(shell_js_result):
    r = shell_js_result
    assert r["errors"] == ["down"]
    assert r["after_error"] == [1, 1]        # same answer, painted again: the page showed the error


def test_watch_follows_a_url_function(shell_js_result):
    r = shell_js_result
    assert r["sat_out"]                      # falsy url: no fetch, no paint
    assert r["moved"] == ["/c", "/d"]        # same JSON at a new url is a new answer; a third check is quiet


def test_keeping_scroll_puts_it_back(shell_js_result):
    r = shell_js_result
    assert r["ret"] == "r"
    assert r["el"] == [120, 5, 3, 40]        # the element and the window
    assert r["replaced"] == 77               # a replaced node, found again by selector
    assert r["async"] == 120                 # waits for an async repaint
    assert r["thrown"] == 120                # restored even when the repaint throws
    assert r["none"] == 1                    # no element is not an error
