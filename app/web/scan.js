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
// Timings are in ms. GAP: the longest pause between two keys of one scan. MIN: the shortest code
// (the shop's own labels carry 3-5 digit item codes). Loaded as a classic script before app.js.
(() => {
const SCAN_GAP_MS = 35;
const SCAN_MIN_LENGTH = 3;

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
  } catch { el.value += text; }
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function createScanDetector({ active = () => true, onScan, skip = () => false,
  gap = SCAN_GAP_MS, minLength = SCAN_MIN_LENGTH, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  let buf = "", last = -Infinity, snap = null, holding = false, timer = null;
  const reset = () => { buf = ""; holding = false; snap = null; if (timer) clearTimer(timer); timer = null; };
  const flush = () => { if (holding) giveBack(snap, buf); reset(); };
  const swallow = (e) => { e.preventDefault(); e.stopImmediatePropagation?.(); };

  function keydown(e) {
    const now = e.timeStamp ?? Date.now(), el = e.target;
    if (!active() || e.ctrlKey || e.altKey || e.metaKey || e.isComposing) { flush(); return; }
    if (el && skip(el)) { flush(); return; }
    if (e.key === "Enter") {
      const fast = now - last <= gap;
      if (holding && fast && buf.length >= minLength) {
        const code = buf;
        swallow(e); restore(snap); reset(); last = -Infinity;
        onScan(code, el);
        return;
      }
      flush(); last = -Infinity;          // a person's Enter: it acts in the field as usual
      return;
    }
    if (e.key == null || e.key.length !== 1) return;        // Shift (scanners send it for capitals), F12, arrows
    if (buf && now - last <= gap && snap?.el === el) {
      if (!holding) { holding = true; restore(snap); }      // the first character already landed: take it back
      buf += e.key; last = now; swallow(e);
    } else {
      flush();
      buf = e.key; last = now; snap = snapshot(el);
    }
    if (timer) clearTimer(timer);
    // a burst that stops without Enter is someone typing fast: give the characters back
    timer = setTimer(() => { timer = null; flush(); }, gap * 3);
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
globalThis.PharmScan = { SCAN_GAP_MS, SCAN_MIN_LENGTH, createScanDetector, installScanDetector };
})();
