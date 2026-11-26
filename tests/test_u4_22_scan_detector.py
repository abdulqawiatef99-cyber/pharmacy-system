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
  const scans = [], passed = [], valueAtKey = {};
  const d = createScanDetector({ onScan: (c) => scans.push(c), setTimer: (f) => { timers.push(f); return timers.length; },
    clearTimer: (id) => { timers[id - 1] = null; }, ...opts });
  for (const [key, t, extra = {}] of keys) {
    let prevented = false;
    const target = extra.target || field;
    const e = { key, timeStamp: t, target, ctrlKey: !!extra.ctrl, repeat: !!extra.repeat,
      preventDefault() { prevented = true; }, stopImmediatePropagation() {} };
    d.keydown(e);
    if (!prevented && key.length > 1) valueAtKey[key] = target.value;
    if (!prevented) { passed.push(key); if (key.length === 1 && !extra.ctrl) target.setRangeText(key, target.selectionStart, target.selectionEnd); }
  }
  timers.splice(0).forEach((f) => f && f());           // let any pending "burst ended" timer fire
  return { scans, passed, value: field.value, events: field.events, valueAtKey };
};
const burst = (code, start = 1000, step = 5) => [...[...code].map((c, i) => [c, start + i * step]), ["Enter", start + code.length * step]];
const typed = (text, start = 1000, step = 150) => [...[...text].map((c, i) => [c, start + i * step]), ["Enter", start + text.length * step]];
// Codex P1 on #462: a mouse click (pointerdown) or the focus leaving gives held typing back at once
const viaInstall = (() => {
  const L = {}, target = { addEventListener: (t, f) => { (L[t] ||= []).push(f); }, removeEventListener() {} };
  const f = new Field("x");
  const stop = PharmScan.installScanDetector({ onScan: () => {}, setTimer: () => 1, clearTimer: () => {} }, target);
  for (const [key, t] of [["a", 1000], ["b", 1005]]) {
    let prevented = false;
    L.keydown.forEach((h) => h({ key, timeStamp: t, target: f, preventDefault() { prevented = true; }, stopImmediatePropagation() {} }));
    if (!prevented) f.setRangeText(key, f.selectionStart, f.selectionEnd);
  }
  const held = f.value;
  (L.pointerdown || []).forEach((h) => h({ target: {} }));
  const afterClick = f.value;
  L.keydown.forEach((h) => h({ key: "c", timeStamp: 2000, target: f, preventDefault() {}, stopImmediatePropagation() {} }));
  f.setRangeText("c", f.selectionStart, f.selectionEnd);
  L.keydown.forEach((h) => h({ key: "d", timeStamp: 2004, target: f, preventDefault() {}, stopImmediatePropagation() {} }));
  const held2 = f.value;
  // Codex P2 on #462: the focus moving by script (an added item focuses the search box) does not end a scan
  (L.focusout || []).forEach((h) => h({ target: f }));
  const afterBlur = f.value;
  L.keydown.forEach((h) => h({ key: "e", timeStamp: 2008, target: {}, preventDefault() {}, stopImmediatePropagation() {} }));
  const held3 = f.value;
  stop.flush?.();
  return { held, afterClick, held2, afterBlur, held3, afterFlush: f.value };
})();
// text that reaches the field without a key event (an on-screen keyboard, an input method) is never taken back
const insertedBetween = (() => {
  const f = new Field("شارع");
  const d = createScanDetector({ onScan: () => {}, setTimer: () => 1, clearTimer: () => {} });
  const key = (k, t) => { let prevented = false; d.keydown({ key: k, timeStamp: t, target: f, preventDefault() { prevented = true; }, stopImmediatePropagation() {} });
    if (!prevented) f.setRangeText(k, f.selectionStart, f.selectionEnd); };
  key(" ", 1000);
  f.setRangeText("التجربة", f.selectionStart, f.selectionEnd);   // no keydown for these
  key(" ", 1004); key("7", 1008);
  d.release();
  return f.value;
})();
// Codex P2 on #462: a scan over a selected amount in a number box (the browser shows no selection there)
const overSelected = (() => {
  const f = new Field("5", "number"); f.selectionStart = f.selectionEnd = null;
  f.setSelectionRange = () => {}; f.setRangeText = () => { throw new Error("number box"); };
  const scans = [];
  const d = createScanDetector({ onScan: (c) => scans.push(c), setTimer: () => 1, clearTimer: () => {} });
  [..."6221043010366"].forEach((k, i) => { let prevented = false;
    d.keydown({ key: k, timeStamp: 1000 + i * 4, target: f, preventDefault() { prevented = true; }, stopImmediatePropagation() {} });
    if (!prevented) f.value = i === 0 ? k : f.value + k; });       // the first key replaces the selected «5»
  d.keydown({ key: "Enter", timeStamp: 1060, target: f, preventDefault() {}, stopImmediatePropagation() {} });
  return { scans, value: f.value };
})();
// Codex P2 on #462 (8d714e0): a person replacing a selected amount fast gets the replacement back, not appended
const replacedFast = (() => {
  const f = new Field("5", "number"); f.selectionStart = f.selectionEnd = null;
  f.setSelectionRange = () => {}; f.setRangeText = () => { throw new Error("number box"); };
  let fire = null;
  const d = createScanDetector({ onScan: () => {}, setTimer: (fn) => { fire = fn; return 1; }, clearTimer: () => {} });
  [["1", 1000], ["5", 1025]].forEach(([k, t], i) => { let prevented = false;
    d.keydown({ key: k, timeStamp: t, target: f, preventDefault() { prevented = true; }, stopImmediatePropagation() {} });
    if (!prevented) f.value = i === 0 ? k : f.value + k; });
  fire?.();                                                       // the pause: the burst was a person
  return f.value;
})();
// Codex P2 on #462 (d8d3e13): the caret before or inside an amount in a number box
const numberBox = (value, keys, place, gapMs, enter) => {
  const f = new Field(value, "number"); f.selectionStart = f.selectionEnd = null;
  f.setSelectionRange = () => {}; f.setRangeText = () => { throw new Error("number box"); };
  let fire = null; const scans = [];
  const d = createScanDetector({ onScan: (c) => scans.push(c), setTimer: (fn) => { fire = fn; return 1; }, clearTimer: () => {} });
  let at = place;
  [...keys].forEach((k, i) => { let prevented = false;
    d.keydown({ key: k, timeStamp: 1000 + i * gapMs, target: f, preventDefault() { prevented = true; }, stopImmediatePropagation() {} });
    if (!prevented) { f.value = f.value.slice(0, at) + k + f.value.slice(at); at += 1; } });
  if (enter) d.keydown({ key: "Enter", timeStamp: 1000 + keys.length * gapMs, target: f, preventDefault() {}, stopImmediatePropagation() {} });
  else fire?.();
  return { scans, value: f.value };
};
const caretBefore = numberBox("5", "6221043010366", 0, 4, true);
const caretInside = numberBox("15", "6221043010366", 1, 4, true);
const typedBefore = numberBox("5", "12", 0, 25, false);
// Codex P2 on #462 (e0bfcad): a list box keeps its own first-letter choice; a scan puts the old option back
const listBox = (keys, gapMs, enter) => {
  const sel = { tagName: "SELECT", selectedIndex: 0, isConnected: true, events: [], dispatchEvent(ev) { this.events.push(ev.type); } };
  const scans = []; let fire = null;
  const d = createScanDetector({ onScan: (c) => scans.push(c), setTimer: (fn) => { fire = fn; return 1; }, clearTimer: () => {} });
  const passed = [];
  [...keys].forEach((k, i) => { let prevented = false;
    d.keydown({ key: k, timeStamp: 1000 + i * gapMs, target: sel, preventDefault() { prevented = true; }, stopImmediatePropagation() {} });
    if (!prevented) { passed.push(k); sel.selectedIndex = 2; sel.events.push("change"); } });   // type-ahead moved it
  if (enter) d.keydown({ key: "Enter", timeStamp: 1000 + keys.length * gapMs, target: sel, preventDefault() {}, stopImmediatePropagation() {} });
  else fire?.();
  return { scans, passed, index: sel.selectedIndex, events: sel.events };
};
const listTyped = listBox("ف", 0, false);
const listScanned = listBox("6221043010366", 4, true);
const out = { viaInstall, insertedBetween, overSelected, replacedFast, caretBefore, caretInside, typedBefore, listTyped, listScanned,
  scanInDiscount: run(burst("6223001234567"), new Field("5")),
  scanShortCode: run(burst("1234"), new Field("")),
  personTyping: run(typed("100"), new Field("")),
  fastPairThenSlow: run([["1", 1000], ["0", 1020], ["0", 1300], ["Enter", 1500]], new Field("")),
  fastButNoEnter: run([["4", 1000], ["5", 1005], ["6", 1010]], new Field("ab")),
  tooShort: run([["4", 1000], ["5", 1005], ["Enter", 1010]], new Field("")),
  skipped: run(burst("6223001234567"), new Field(""), { skip: () => true }),
  numpadRoll: run([["1", 1000], ["5", 1028], ["0", 1055], ["Enter", 1080]], new Field("")),
  focusMoves: (() => { const q = new Field(""); q.id = "posQ"; const f = new Field("");
    const keys = [...burst("6221043010366")].map(([k, t], i) => [k, t, i >= 4 ? { target: q } : {}]);
    return run(keys, f, { skip: (el) => el.id === "posQ" }); })(),
  gs1Separator: run([..."0106221043010359"].map((c, i) => [c, 1000 + i * 4]).concat([["Control", 1066], ["]", 1067, { ctrl: true }],
    ..."10ABC".split("").map((c, i) => [c, 1070 + i * 4]), ["Enter", 1092]]), new Field("5", "number")),
  keyRepeat: run([["1", 1000], ["1", 1033, { repeat: true }], ["1", 1066, { repeat: true }], ["Enter", 1090]], new Field("")),
  fastThenF9: run([["a", 1000], ["b", 1005], ["c", 1010], ["F9", 1015]], new Field("x")),
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

    def test_a_fast_numpad_amount_is_never_a_shop_code(self):
        o = self.out["numpadRoll"]                              # review P1: 28 ms rolls on the numpad
        self.assertEqual(o["scans"], [])
        self.assertEqual(o["value"], "150")
        self.assertIn("Enter", o["passed"])

    def test_focus_moving_to_the_search_mid_scan_keeps_the_whole_code(self):
        o = self.out["focusMoves"]
        self.assertEqual(o["scans"], ["6221043010366"])

    def test_gs1_group_separator_does_not_break_the_scan(self):
        o = self.out["gs1Separator"]
        self.assertEqual(len(o["scans"]), 1)
        self.assertTrue(o["scans"][0].startswith("0106221043010359"))
        self.assertEqual(o["value"], "5")                       # the discount box is untouched

    def test_a_held_key_repeating_is_not_a_scan(self):
        o = self.out["keyRepeat"]
        self.assertEqual(o["scans"], [])
        self.assertIn("Enter", o["passed"])

    def test_a_function_key_gives_the_held_text_back_before_it_acts(self):
        o = self.out["fastThenF9"]                              # F9 saves the field's value: it must be all there
        self.assertEqual(o["scans"], [])
        self.assertIn("F9", o["passed"])
        self.assertEqual(o["valueAtKey"]["F9"], "xabc")

    def test_a_click_blur_or_checkout_gives_held_typing_back_at_once(self):
        o = self.out["viaInstall"]
        self.assertEqual((o["held"], o["afterClick"]), ("x", "xab"))      # pointerdown: before the field blurs
        self.assertEqual((o["held2"], o["afterBlur"]), ("xab", "xab"))    # focus moved by script: still held
        self.assertEqual((o["held3"], o["afterFlush"]), ("xab", "xabcde"))  # the sale screen asks before checkout

    def test_scan_over_a_selected_amount_keeps_the_amount_and_the_whole_code(self):
        o = self.out["overSelected"]
        self.assertEqual(o["scans"], ["6221043010366"])
        self.assertEqual(o["value"], "5")

    def test_a_fast_replacement_of_a_selected_amount_is_given_back_as_typed(self):
        self.assertEqual(self.out["replacedFast"], "15")

    def test_scan_with_the_caret_before_or_inside_an_amount(self):
        for case in ("caretBefore", "caretInside"):
            o = self.out[case]
            self.assertEqual(o["scans"], ["6221043010366"], case)
        self.assertEqual(self.out["caretBefore"]["value"], "5")
        self.assertEqual(self.out["caretInside"]["value"], "15")
        self.assertEqual(self.out["typedBefore"]["value"], "125")   # a person's fast typing replayed where it went

    def test_list_box_keeps_its_first_letter_choice_and_a_scan_puts_it_back(self):
        t, sc = self.out["listTyped"], self.out["listScanned"]
        self.assertEqual((t["passed"], t["index"]), (["ف"], 2))      # a person's letter chooses as usual
        self.assertEqual(sc["scans"], ["6221043010366"])
        self.assertEqual(sc["index"], 0)                             # the option chosen before the scan
        self.assertEqual(sc["events"][-1], "change")                 # and the screen is told it is back

    def test_text_inserted_without_keys_is_never_taken(self):
        self.assertEqual(self.out["insertedBetween"], "شارع التجربة 7")

    def test_skipped_field_and_inactive_screen_are_left_alone(self):
        for case in ("skipped", "inactive"):
            o = self.out[case]
            self.assertEqual(o["scans"], [], case)
            self.assertEqual(o["value"], "6223001234567", case)
