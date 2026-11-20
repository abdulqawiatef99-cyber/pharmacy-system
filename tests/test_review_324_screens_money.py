"""#324 screen items handed to the money session: R16 (a tab that cached the label printer mode), R22 (exports of
a capped invoice list say so), R24 (period presets by the Cairo calendar), R25 (an older report answer never
replaces a newer choice). The browser code is run in node with the rest stubbed. Each test failed first."""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "app" / "web" / "app.js").read_text(encoding="utf-8")


def cut(start, end_after):
    a = JS.index(start)
    return JS[a:JS.index(end_after, a) + len(end_after)]


class NodeCase(unittest.TestCase):
    def node(self, code, tz="UTC"):
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30,
                             env={**os.environ, "TZ": tz})
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)


class TestR16LabelModeChanged(NodeCase):
    def test_a_tab_told_labels_go_through_the_browser_prints_there(self):
        fn = cut("let LABEL_CFG = null;", "\n}\n")
        out = self.node("""
const pages = [], toasts = []; let asked = 0;
function printInFrame(u) { pages.push(u); }
function toast(m, bad) { toasts.push([m, !!bad]); }
async function api(path) {
  if (path === "/api/settings/receipt") { asked++; return { label_lang: asked === 1 ? "ar" : "browser" }; }
  const e = new Error("browser"); e.code = "labels_in_browser"; throw e;
}
""" + fn + """
(async () => { await printLabels([["i1", 2]]); await printLabels([["i1", 1]]);
  console.log(JSON.stringify({ pages, toasts, asked })); })();
""")
        self.assertEqual(len(out["pages"]), 2)                # the browser page, both times
        self.assertEqual([t for t in out["toasts"] if t[1]], [])  # no error shown for a mode change
        self.assertEqual(out["asked"], 2)                     # the settings read again after the refusal


class TestR22CappedExportsSaySo(unittest.TestCase):
    def test_the_file_and_the_print_carry_the_cap(self):
        start = JS.index('const sub = () => `${stamp(R.from)} ← ${stamp(R.to)}`;')
        body = JS[start:JS.index("\nasync function ", start)]
        self.assertIn("R.truncated", body[:400])
        self.assertIn('exportXlsx("فواتير البيع", subOut()', body)
        self.assertIn("subOut())", body[body.index("printPreview(box"):][:200])


class TestR24PresetsByTheCairoDay(NodeCase):
    def presets(self, iso, tz="UTC"):
        fn = cut("const isoDay = (d) =>", "\nfunction rangeBar(")
        return self.node(f"""
const RealDate = Date; const fixed = new RealDate("{iso}").getTime();
globalThis.Date = class extends RealDate {{ constructor(...a) {{ if (!a.length) super(fixed); else super(...a); }}
  static now() {{ return fixed; }} }};
""" + fn.replace("\nfunction rangeBar(", "") + """
const p = periodPresets(), o = {};
for (const [k, [, a, z]] of Object.entries(p)) o[k] = [isoDay(a), isoDay(z)];
console.log(JSON.stringify(o));
""", tz)

    def test_the_night_the_clocks_go_back(self):
        p = self.presets("2026-10-29T21:30:00Z", "Africa/Cairo")   # the second 23:30 in Cairo on 29 October
        self.assertEqual(p["today"], ["2026-10-29", "2026-10-29"])
        self.assertEqual(p["yesterday"], ["2026-10-28", "2026-10-28"])
        self.assertEqual(p["week"], ["2026-10-23", "2026-10-29"])

    def test_a_phone_on_utc_follows_the_pharmacy_day(self):
        p = self.presets("2026-11-18T22:30:00Z")             # 00:30 on 19 November in Cairo
        self.assertEqual(p["today"], ["2026-11-19", "2026-11-19"])
        self.assertEqual(p["month"], ["2026-11-01", "2026-11-19"])
        self.assertEqual(p["last"], ["2026-10-01", "2026-10-31"])
        self.assertEqual(self.presets("2026-11-18T22:30:00Z", "America/New_York")["today"], ["2026-11-19"] * 2)


class TestR25OlderAnswerNeverWins(NodeCase):
    def test_a_slow_answer_for_an_older_choice_is_dropped(self):
        start = JS.index("const API = { busy: 0, last: 0 };")
        end = JS.index("\n}\n", JS.index("async function api(")) + 3
        out = self.node("""
let APPROVAL = null, ME = {}, n = 0; const location = { hash: "" };
function newKey() { return "k" + (++n); }
function approvalHeaders() { return {}; }
async function askApproval() { return false; }
async function fetch(path) { const slow = path.includes("year");
  await new Promise((r) => setTimeout(r, slow ? 120 : 10));
  return { ok: true, status: 200, json: async () => ({ path }) }; }
""" + JS[start:end] + """
const shown = [];
RANGE_SEQ.pr = 1; RANGE_CALL = { id: "pr", seq: 1 }; api("/api/reports/profit?year").then((r) => shown.push(r.path)); RANGE_CALL = null;
RANGE_SEQ.pr = 2; RANGE_CALL = { id: "pr", seq: 2 }; api("/api/reports/profit?today").then((r) => shown.push(r.path)); RANGE_CALL = null;
api("/api/other").then((r) => shown.push(r.path));
setTimeout(() => console.log(JSON.stringify(shown)), 300);
""")
        self.assertEqual(out, ["/api/reports/profit?today", "/api/other"])

    def test_every_period_change_is_tagged(self):
        fn = cut("function wireRange(id, onChange) {", "\n}\n")
        self.assertIn("RANGE_CALL = { id, seq }", fn)
        self.assertIn("RANGE_CALL = null", fn)
