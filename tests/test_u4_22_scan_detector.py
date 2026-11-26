"""U4-22: the shared scan detector (app/web/scan.js) tells a scanner's burst from a person's typing by key timing.
A scan from any field reaches onScan and leaves the field as it was; a person's typing is never taken."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "app" / "web"

HARNESS = r"""
require(process.argv[1]);
const { createScanDetector } = globalThis.PharmScan;
class Field {
  constructor(value = "", type = "text") { this.tagName = "INPUT"; this.type = type; this.value = value; this.isConnected = true;
    this.selectionStart = value.length; this.selectionEnd = value.length; this.events = []; }
  setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; }
  setRangeText(t, s, e) { this.value = this.value.slice(0, s) + t + this.value.slice(e); this.selectionStart = this.selectionEnd = s + t.length; }
  dispatchEvent(ev) { this.events.push(ev.type); }
}
globalThis.Event = class { constructor(type) { this.type = type; } };
const timers = [];
const run = (keys, field, opts = {}) => {
  const scans = [], passed = [];
  const d = createScanDetector({ onScan: (c) => scans.push(c), setTimer: (f) => { timers.push(f); return timers.length; },
    clearTimer: (id) => { timers[id - 1] = null; }, ...opts });
  for (const [key, t] of keys) {
    let prevented = false;
    const e = { key, timeStamp: t, target: field, preventDefault() { prevented = true; }, stopImmediatePropagation() {} };
    d.keydown(e);
    if (!prevented) { passed.push(key); if (key.length === 1) field.setRangeText(key, field.selectionStart, field.selectionEnd); }
  }
  timers.splice(0).forEach((f) => f && f());           // let any pending "burst ended" timer fire
  return { scans, passed, value: field.value, events: field.events };
};
const burst = (code, start = 1000, step = 5) => [...[...code].map((c, i) => [c, start + i * step]), ["Enter", start + code.length * step]];
const typed = (text, start = 1000, step = 150) => [...[...text].map((c, i) => [c, start + i * step]), ["Enter", start + text.length * step]];
const out = {
  scanInDiscount: run(burst("6223001234567"), new Field("5")),
  scanShortCode: run(burst("1234"), new Field("")),
  personTyping: run(typed("100"), new Field("")),
  fastPairThenSlow: run([["1", 1000], ["0", 1020], ["0", 1300], ["Enter", 1500]], new Field("")),
  fastButNoEnter: run([["4", 1000], ["5", 1005], ["6", 1010]], new Field("ab")),
  tooShort: run([["4", 1000], ["5", 1005], ["Enter", 1010]], new Field("")),
  skipped: run(burst("6223001234567"), new Field(""), { skip: () => true }),
  inactive: run(burst("6223001234567"), new Field(""), { active: () => false }),
};
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestScanDetector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = subprocess.run([shutil.which("node"), "-e", "(async () => {" + HARNESS + "})()", str(WEB / "scan.js")],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_scan_from_another_field_never_types_into_it(self):
        o = self.out["scanInDiscount"]
        self.assertEqual(o["scans"], ["6223001234567"])
        self.assertEqual(o["value"], "5")                       # the discount typed before is untouched
        self.assertEqual(o["passed"], ["6"])                    # only the first key reached the field, and was taken back

    def test_short_shop_label_code_is_a_scan(self):
        self.assertEqual(self.out["scanShortCode"]["scans"], ["1234"])
        self.assertEqual(self.out["scanShortCode"]["value"], "")

    def test_person_typing_is_never_taken(self):
        o = self.out["personTyping"]
        self.assertEqual(o["scans"], [])
        self.assertEqual(o["value"], "100")
        self.assertIn("Enter", o["passed"])                     # the person's Enter acts in the field

    def test_a_fast_pair_inside_typing_is_given_back(self):
        o = self.out["fastPairThenSlow"]
        self.assertEqual(o["scans"], [])
        self.assertEqual(o["value"], "100")
        self.assertIn("Enter", o["passed"])

    def test_fast_burst_without_enter_is_given_back(self):
        o = self.out["fastButNoEnter"]
        self.assertEqual(o["scans"], [])
        self.assertEqual(o["value"], "ab456")
        self.assertIn("input", o["events"])                     # listeners see the text that came back

    def test_two_fast_keys_are_not_a_code(self):
        o = self.out["tooShort"]
        self.assertEqual(o["scans"], [])
        self.assertEqual(o["value"], "45")
        self.assertIn("Enter", o["passed"])

    def test_skipped_field_and_inactive_screen_are_left_alone(self):
        for case in ("skipped", "inactive"):
            o = self.out[case]
            self.assertEqual(o["scans"], [], case)
            self.assertEqual(o["value"], "6223001234567", case)
