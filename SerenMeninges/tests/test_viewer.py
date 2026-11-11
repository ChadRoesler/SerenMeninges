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
const listeners = {};
globalThis.document = {
  hidden: false,
  getElementById: () => node(),
  addEventListener(ev, fn){ (listeners[ev] ||= []).push(fn); },
  removeEventListener(ev, fn){ listeners[ev] = (listeners[ev] || []).filter(f => f !== fn); },
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

  // opts.signature: a field that only ticks is left out, so it never repaints;
  // opts.each still hears every answer, and is told whether it changed
  painted = []; const heard = [];
  answers = [{ n: 1, age: 1 }, { n: 1, age: 2 }, { n: 1, age: 3 }, { n: 2, age: 4 }];
  stop = watch("/e", 10, d => painted.push(d.age), {
    signature: d => String(d.n), each: (d, changed) => heard.push([d.age, changed]) });
  await sleep(120); stop();
  out.sig_painted = painted;
  out.each_first4 = heard.slice(0, 4);
  out.each_before_paint = heard.length >= painted.length;

  // opts.fetch: the page's own fetcher, not api()
  painted = []; const asked = []; const before2 = calls.length;
  stop = watch("/f", 1000, d => painted.push(d.mine), { fetch: async p => { asked.push(p); return { mine: 9 }; } });
  await stop.check(); stop();
  out.own_fetch = [asked, painted, calls.length === before2];

  // opts.whileVisible: nothing while hidden, one round the moment it is shown
  answers = [{ n: 5 }]; painted = [];
  document.hidden = true;
  stop = watch("/g", 10, d => painted.push(d.n), { whileVisible: true });
  await sleep(50);
  out.hidden_quiet = painted.length === 0;
  document.hidden = false; (listeners.visibilitychange || []).forEach(f => f());
  await sleep(5);
  out.shown = painted;
  stop();
  out.unhooked = (listeners.visibilitychange || []).length === 0;

  // stop.every: the rate changes
  answers = [{ n: 1 }]; let n0 = calls.length;
  stop = watch("/h", 100000, () => {});
  await sleep(40); out.slow = calls.length - n0;          // just the first round
  stop.every(10); await sleep(80); stop();
  out.faster = calls.length - n0 > 3;

  // check() during a slow round waits for a round asked AFTER it, not the one in flight
  let served = 0; painted = [];
  stop = watch("/i", 100000, d => painted.push(d.v), { fetch: async () => { const v = ++served; await sleep(30); return { v }; } });
  await sleep(5);                                          // round 1 is in flight
  const [a, b] = [stop.check(), stop.check()];
  await Promise.all([a, b]); stop();
  out.fresh_after_check = [served, painted, a === b];

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


def test_watch_signature_and_each(shell_js_result):
    r = shell_js_result
    assert r["sig_painted"] == [1, 4]        # ages 2 and 3 changed only a ticking field: no repaint
    assert r["each_first4"] == [[1, True], [2, False], [3, False], [4, True]]
    assert r["each_before_paint"]


def test_watch_takes_the_pages_own_fetcher(shell_js_result):
    asked, painted, api_untouched = shell_js_result["own_fetch"]
    # asked twice: the first round, and the one check() ran after it
    assert set(asked) == {"/f"} and painted == [9] and api_untouched


def test_watch_while_visible(shell_js_result):
    r = shell_js_result
    assert r["hidden_quiet"]                 # a hidden tab asks nothing
    assert r["shown"] == [5]                 # and catches up the moment it is shown
    assert r["unhooked"]                     # stop() takes its listener back


def test_watch_rate_can_change(shell_js_result):
    r = shell_js_result
    assert r["slow"] == 1
    assert r["faster"]


def test_check_during_a_round_gets_a_fresh_answer(shell_js_result):
    served, painted, shared = shell_js_result["fresh_after_check"]
    assert served == 2                       # one more round, not one per caller
    assert painted == [1, 2]
    assert shared                            # both callers wait on the same follow-up


def test_keeping_scroll_puts_it_back(shell_js_result):
    r = shell_js_result
    assert r["ret"] == "r"
    assert r["el"] == [120, 5, 3, 40]        # the element and the window
    assert r["replaced"] == 77               # a replaced node, found again by selector
    assert r["async"] == 120                 # waits for an async repaint
    assert r["thrown"] == 120                # restored even when the repaint throws
    assert r["none"] == 1                    # no element is not an error
