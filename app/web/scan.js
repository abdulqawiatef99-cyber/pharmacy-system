// U4-22: one shared barcode-scan detector for the sale screen (and later the purchase screen).
// A keyboard-wedge scanner types a whole code in a burst (a few ms between keys) and ends with Enter.
// A person cannot keep that pace, so the gap between keys tells the two apart. When a burst is found
// while the focus is in some other field (discount, customer, payment, a button), the characters are
// taken back out of that field, its Enter never acts there, and the code goes to onScan instead.
// A short fast burst that ends without Enter, or with too few characters, is given back to the field.
//
//   const stop = PharmScan.installScanDetector({ active: () => onSaleScreen, onScan: (code) => addByCode(code),
//                                      skip: (el) => el.id === "posQ" });
//
// Timings are in ms. GAP: the longest pause between two keys of a long code (8+ characters). A shorter code
// (the shop's own labels carry 3-5 digit item codes) must come at SHORT_GAP, faster than any hand. MIN: the
// shortest code. Loaded as a classic script before app.js.
(() => {
const SCAN_GAP_MS = 35;
const SCAN_MIN_LENGTH = 3;
const SCAN_SHORT_GAP_MS = 12;      // codes shorter than SCAN_LONG_LENGTH: a hand cannot keep this pace
const SCAN_LONG_LENGTH = 8;

const isText = (el) => !!el && (el.tagName === "TEXTAREA" || (el.tagName === "INPUT" &&
  !["checkbox", "radio", "button", "submit", "reset", "file", "range", "color"].includes((el.type || "text").toLowerCase())));

function snapshot(el) {
  if (!el) return null;
  if (isText(el)) {
    let start = null, end = null;
    try { start = el.selectionStart; end = el.selectionEnd; } catch { /* number inputs have no selection */ }
    return { el, kind: "text", value: el.value, start, end };
  }
  if (el.tagName === "SELECT") return { el, kind: "select", index: el.selectedIndex };
  return { el, kind: "none" };
}

function restore(snap) {
  if (!snap) return;
  if (snap.kind === "text" && snap.el.value !== snap.value) {
    snap.el.value = snap.value;
    try { if (snap.start != null) snap.el.setSelectionRange(snap.start, snap.end); } catch { /* not selectable */ }
  } else if (snap.kind === "select" && snap.el.selectedIndex !== snap.index) {
    snap.el.selectedIndex = snap.index;
  }
}

// puts characters that were held back into the field, as if they had been typed there
function giveBack(snap, text) {
  if (!snap || !text || snap.kind !== "text" || !snap.el.isConnected) return;
  const el = snap.el;
  try {
    const s = el.selectionStart ?? el.value.length, e = el.selectionEnd ?? s;
    el.setRangeText(text, s, e, "end");
  } catch { el.value = (snap.value ?? el.value) + text; }
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function createScanDetector({ active = () => true, onScan, skip = () => false,
  gap = SCAN_GAP_MS, shortGap = SCAN_SHORT_GAP_MS, longLength = SCAN_LONG_LENGTH, minLength = SCAN_MIN_LENGTH,
  setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  let buf = "", last = -Infinity, worst = 0, snap = null, holding = false, timer = null, scanEnd = -Infinity;
  const reset = () => { buf = ""; worst = 0; holding = false; snap = null; if (timer) clearTimer(timer); timer = null; };
  const flush = () => { if (holding) giveBack(snap, buf); reset(); };
  const swallow = (e) => { e.preventDefault(); e.stopImmediatePropagation?.(); };
  // a long code (product barcode, GS1) may come at the normal scanner pace; a short one (the shop's 3-5 digit
  // labels) only at a pace no hand reaches, so an amount rolled fast on the number pad stays an amount
  const isScan = () => buf.length >= minLength && worst <= (buf.length >= longLength ? gap : shortGap);
  const arm = () => { if (timer) clearTimer(timer); timer = setTimer(() => { timer = null; flush(); }, gap * 3); };

  function keydown(e) {
    const now = e.timeStamp ?? Date.now(), el = e.target, fast = now - last <= gap;
    if (!active() || e.isComposing) { flush(); return; }
    // while a scan is held it keeps going even if the focus moves (an added item focuses the search box), and the
    // modifier a scanner sends for the GS1 separator (Ctrl+], Alt codes) does not end it
    if (holding && fast) {
      if (e.key === "Enter") {
        worst = Math.max(worst, now - last);
        if (isScan()) {
          const code = buf;
          swallow(e); restore(snap); reset(); last = -Infinity; scanEnd = now;
          onScan(code, el, now);
          return;
        }
        flush(); last = -Infinity;          // not a scan after all: the characters go back, and Enter acts as usual
        return;
      }
      if (e.ctrlKey || e.altKey || e.metaKey || e.key == null || e.key.length !== 1) {
        if (e.key?.length === 1 || /^(Control|Alt|Shift|AltGraph)$/.test(e.key || "")) { swallow(e); last = now; arm(); }
        return;
      }
      if (e.repeat) { flush(); return; }
      worst = Math.max(worst, now - last); buf += e.key; last = now; swallow(e); arm();
      return;
    }
    if (e.ctrlKey || e.altKey || e.metaKey || e.repeat || (el && skip(el))) { flush(); last = -Infinity; return; }
    if (e.key === "Enter" && now - scanEnd < 150) { swallow(e); return; }   // the LF of a scanner sending CR+LF
    if (e.key === "Enter") { flush(); last = -Infinity; return; }      // a person's Enter: it acts in the field as usual
    if (e.key == null || e.key.length !== 1) return;        // Shift (scanners send it for capitals), F12, arrows
    if (buf && fast && snap?.el === el) {
      holding = true;
      restore(snap);                        // the first character already landed: take it back, and tell the field
      if (snap?.kind === "text") snap.el.dispatchEvent(new Event("input", { bubbles: true }));
      worst = Math.max(worst, now - last); buf += e.key; last = now; swallow(e);
    } else {
      flush();
      buf = e.key; last = now; snap = snapshot(el);
      // a list box would jump to an option on the first letter: never let a character reach it
      if (snap?.kind === "select") swallow(e);
    }
    arm();                                  // a burst that stops without Enter is someone typing fast: give it back
  }
  return { keydown, reset, get pending() { return buf; } };
}

function installScanDetector(opts, target = window) {
  const d = createScanDetector(opts);
  const h = (e) => d.keydown(e);
  target.addEventListener("keydown", h, true);          // capture: before any field or screen key handler
  return () => { target.removeEventListener("keydown", h, true); d.reset(); };
}

// a classic script (the screen's CSP allows no inline import map): app.js reads it from here
globalThis.PharmScan = { SCAN_GAP_MS, SCAN_SHORT_GAP_MS, SCAN_LONG_LENGTH, SCAN_MIN_LENGTH, createScanDetector, installScanDetector };
})();
