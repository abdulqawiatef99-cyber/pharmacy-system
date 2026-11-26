// Pharmacy platform PWA — no build step, no external libraries.
// All text inserted into HTML goes through esc() to prevent XSS.

const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
let ME = null;

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (p) => (p == null ? "" : (p / 100).toFixed(2));
const unitName = (it) => (!it.base_unit_name || it.base_unit_name === "unit" ? "وحدة" : it.base_unit_name);
// stock in base units -> "15 علبة + 2 وحدة"
const fmtStock = (q, it) => {
  if (q == null) return "";
  if (q === 0) return "0";
  const p = it.pack_size || 1, packs = Math.trunc(q / p), rest = q % p;
  return [packs ? `${packs} علبة` : "", rest ? `${rest} ${unitName(it)}` : ""].filter(Boolean).join(" + ");
};
const toPiasters = (v) => (v === "" || v == null ? null : Math.round(parseFloat(v) * 100));
// exact piasters from the typed text, never rounded: "475.5" -> 47550, "-20" -> -2000; "475.005" or "1e3" -> null
// (Codex P1 on #394: a fraction of a piaster is refused, not rounded)
function exactPiasters(v) {
  const m = /^\s*(-?)(\d+)(?:[.,](\d{1,2}))?\s*$/.exec(String(v ?? ""));
  if (!m) return null;
  const n = Number(m[2]) * 100 + Number((m[3] || "").padEnd(2, "0"));
  return Number.isSafeInteger(n) ? (m[1] ? -n : n) : null;
}
// a typed whole number; "2.5" goes to the server as typed and is refused there, never cut to 2 by parseInt
const typedWhole = (v) => { const t = String(v ?? "").trim(); if (t === "") return null; const n = Number(t); return Number.isInteger(n) ? n : t; };
const pct = (bp) => (bp == null ? "" : (bp / 100).toString());
// pharmacists type expiry as 629 / 0629 / 6-29 / 6.29 / 6 29: "629" -> "6-29" (month-year)
const normExpiry = (v) => {
  const t = String(v || "").trim().replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d)).replace(/\s+/g, "-");
  const m = t.match(/^(\d{1,2})(\d{2}|\d{4})$/);              // 628, 1128, 112028 (U2-2)
  return m && t.length >= 3 ? `${m[1]}-${m[2]}` : t;
};
// ------------------------------------------------------------------ person's screen preferences
const PREFS_DEFAULT = { theme: "light", accent: null, font: "m", lang: "ar", names: "both", help: "on", logout_confirm: "on" };
const FONT_STEPS = ["s", "m", "l", "xl"];
let PREFS = { ...PREFS_DEFAULT };
try { PREFS = { ...PREFS_DEFAULT, ...JSON.parse(localStorage.getItem("prefs") || "{}") }; } catch { /* storage blocked */ }
function applyPrefs(p) {
  PREFS = { ...PREFS_DEFAULT, ...(p || {}) };
  const h = document.documentElement;
  h.dataset.theme = PREFS.theme; h.dataset.font = PREFS.font;
  if (PREFS.accent && PREFS.theme !== "dark") h.style.setProperty("--brand", PREFS.accent);
  else h.style.removeProperty("--brand");
  try { localStorage.setItem("prefs", JSON.stringify(PREFS)); } catch { /* storage blocked */ }
}
applyPrefs(PREFS);

// ------------------------------------------------------------------ English screen (لغة البرنامج)
// The screens are written in Arabic; when a person chooses English, every Arabic text on the page
// (and placeholders / titles) is swapped for its English from /static/i18n-en.js as it appears.
// Whole texts match exactly; inside longer texts only multi-word phrases are swapped, so a
// customer or item name is never "translated". Item names follow the separate names setting.
const I18N = { on: false, map: null, re: null, obs: null };
const AR = /[\u0600-\u06FF]/;
function trString(s) {
  if (!s || !AR.test(s)) return null;
  const t = s.replace(/\s+/g, " ").trim();
  let out = I18N.map.get(t);
  if (out === undefined) {
    out = t;
    for (const [re, rep] of I18N_FULL_TEXT) out = out.replace(re, rep);
    if (I18N.re) out = out.replace(I18N.re, (m) => I18N.map.get(m) ?? m);
    // then each piece between separators, only when the whole piece is a known phrase
    out = out.replace(/[^—·():/،|\n]+/g, (seg) => { const k = seg.trim(); const v = k && I18N.map.get(k); return v ? seg.replace(k, v) : seg; });
    for (const [re, rep] of I18N_PATTERNS) out = out.replace(re, rep);
  }
  if (out == null || out === t) return null;
  const lead = s.match(/^\s*/)[0], trail = s.match(/\s*$/)[0];
  return lead + out + trail;
}
const I18N_BEFORE = { "ملف": "File", "سطر": "Line", "العبوة": "Pack", "ربح": "profit", "كود": "Code", "حركة": "Movements", "اليوم": "Today",
  "رقم": "No.", "فواتير": "Invoices", "طُبع": "Printed", "صرف": "Pay", "شهر": "Month", "إلى": "to", "العميل": "Customer", "رصيده": "Balance",
  "عجز": "Shortage", "زيادة": "Surplus", "لم يُجرد": "Not counted", "سيُصفَّر": "Will be set to zero:", "الإجمالي": "Total",
  "(المُرتجع": "(returned", "(الفرق": "(difference", "(المتوقع": "(expected", "معروض أول": "Showing the first", "يظهر أول": "Showing the first",
  "أُرسل": "Sent", "حد الآجل": "credit limit" };
const I18N_AFTER = { "صورة": "pictures", "اختلاف": "differences", "تعديل": "edits", "جنيه": "EGP", "عجز": "short", "ملصق": "labels",
  "يومًا": "days", "فقط": "only", "والتكلفة": "and the cost" };
// words that come next to numbers ("3 فاتورة", "76.45 ج", "5 يوم") and a few fixed bits
const NUM = "(\\d[\\d.,]*)";
const I18N_FULL_TEXT = [
  [/السعر اتغير من ([\d.,]+) لـ ([\d.,]+): تغيّر سعر الرصيد الحالي \(([^)]*)\) للسعر الجديد؟/g,
    "The price changed from $1 to $2. Reprice the current stock ($3)?"],
  [/سعر الجمهور اتغير من ([\d.,]+) لـ ([\d.,]+)/g, "The public price changed from $1 to $2"],
  [/مبيعات ([\d.,]+) · مرتجع ([\d.,]+)/g, "Sales $1 · Refunds $2"],
];
const I18N_PATTERNS = [
  [new RegExp(`${NUM}\\s*فواتير|${NUM}\\s*فاتورة`, "g"), (m, a, b) => `${a || b} invoices`],
  [new RegExp(`${NUM}\\s*(?:يوم|أيام)(?![\\u0600-\\u06FF])`, "g"), "$1 days"],
  [new RegExp(`${NUM}\\s*(?:وحدة|وحدات)(?![\\u0600-\\u06FF])`, "g"), "$1 units"],
  [new RegExp(`${NUM}\\s*(?:علبة|علب)(?![\\u0600-\\u06FF])`, "g"), "$1 packs"],
  [new RegExp(`${NUM}\\s*(?:صنف|أصناف)(?![\\u0600-\\u06FF])`, "g"), "$1 items"],
  [new RegExp(`${NUM}\\s*(?:شهر|شهور)(?![\\u0600-\\u06FF])`, "g"), "$1 months"],
  [new RegExp(`${NUM}\\s*(?:ساعة|ساعات)(?![\\u0600-\\u06FF])`, "g"), "$1 hours"],
  [new RegExp(`${NUM}\\s*دقيقة`, "g"), "$1 min"],
  [new RegExp(`${NUM}\\s*(?:عميل|عملاء)(?![\\u0600-\\u06FF])`, "g"), "$1 customers"],
  [new RegExp(`${NUM}\\s*ك\\.ب`, "g"), "$1 KB"], [new RegExp(`${NUM}\\s*ميجا`, "g"), "$1 MB"], [new RegExp(`${NUM}\\s*مم`, "g"), "$1 mm"],
  [new RegExp(`${NUM}\\s*ج(?![\\u0600-\\u06FF])`, "g"), "$1 EGP"], [/(?<![\u0600-\u06FF])ج(?![\u0600-\u06FF])/g, "EGP"],
  [/(?<![\u0600-\u06FF])خصم(?![\u0600-\u06FF])/g, "discount"], [/\(الافتراضي\)/g, "(default)"],
  [/(?<![\u0600-\u06FF])من (\S+) إلى (\S+)/g, "from $1 to $2"], [/(?<![\u0600-\u06FF])يوم (\d+)/g, "day $1"],
  [/(?<![\u0600-\u06FF])بعد (\d+ days)/g, "in $1"],
  [/(?<![\u0600-\u06FF])فاتورة (\d+)/g, "Invoice $1"],
  [/(?<![\u0600-\u06FF])Hello يا /g, "Hello, "],
  [/(\d{1,2}(?::\d\d)?)\s*ص(?![\u0600-\u06FF])/g, "$1 AM"], [/(\d{1,2}(?::\d\d)?)\s*م(?![\u0600-\u06FF])/g, "$1 PM"],
  [/(?<![\u0600-\u06FF])(\d[\d.,]*) من (\d[\d.,]*)/g, "$1 of $2"], [/(?<![\u0600-\u06FF])من (?=\d)/g, "from "],
  // a short word right before or after a number ("ملف 2", "3 صورة"): one table each, so a name is never touched
  [new RegExp(`(?<![\\u0600-\\u06FF])(${Object.keys(I18N_BEFORE).map((k) => k.replace(/[()]/g, "\\$&")).join("|")})(?=\\s*\\d)`, "g"), (m) => I18N_BEFORE[m]],
  [new RegExp(`(\\d[\\d.,]*\\s*)(${Object.keys(I18N_AFTER).join("|")})(?![\\u0600-\\u06FF])`, "g"), (m, n, w) => n + I18N_AFTER[w]],
];
const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "INPUT"]);
function trNode(root) {
  if (!I18N.on || !root) return;
  if (root.nodeType === 3) { const v = trString(root.nodeValue); if (v != null) root.nodeValue = v; return; }
  const attrs = (el) => { for (const a of ["placeholder", "title", "aria-label"]) { const v = el.getAttribute?.(a); if (v) { const x = trString(v); if (x != null) el.setAttribute(a, x); } } };
  if (root.nodeType !== 1 || root.closest?.("[data-raw], .iname-ar")) return;
  attrs(root);
  if (SKIP.has(root.tagName)) return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
    acceptNode: (n) => {
      if (n.nodeType !== 1) return NodeFilter.FILTER_ACCEPT;
      if (n.hasAttribute("data-raw") || n.classList.contains("iname-ar")) return NodeFilter.FILTER_REJECT;
      if (SKIP.has(n.tagName)) { attrs(n); return NodeFilter.FILTER_REJECT; }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    if (n.nodeType === 3) { const v = trString(n.nodeValue); if (v != null) n.nodeValue = v; } else attrs(n);
  }
}
function loadScript(src) {
  return new Promise((ok, bad) => { const sc = document.createElement("script"); sc.src = src; sc.onload = ok; sc.onerror = bad; document.head.append(sc); });
}
async function setLanguage(lang) {
  const h = document.documentElement;
  I18N.want = lang;                       // the latest choice wins, even if the dictionary is still loading
  if (lang !== "en") {
    if (I18N.on) { I18N.on = false; I18N.obs?.disconnect(); location.reload(); }
    h.lang = "ar"; h.dir = "rtl"; return;
  }
  if (!I18N.map) {
    try { await loadScript(document.querySelector('meta[name="i18n-src"]')?.content || "/static/i18n-en.js"); } catch { toast("ملف اللغة الإنجليزية غير موجود", true); return; }
    if (I18N.want !== "en") return;
    const dict = window.I18N_EN || {};
    I18N.map = new Map(Object.entries(dict));
    for (const [k, v] of Object.entries(dict)) {           // "⚠ نص" also known as "نص"
      const m = k.match(/^([⚠✓✕ℹ⛔✅❗❔]\uFE0F?)\s+(.+)$/u);
      if (m && !I18N.map.has(m[2])) I18N.map.set(m[2], v.replace(/^([⚠✓✕ℹ⛔✅❗❔]\uFE0F?)\s+/u, ""));
    }
    const multi = Object.keys(dict).filter((k) => k.includes(" ") && k.length > 4).sort((a, b) => b.length - a.length)
      .map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    I18N.re = multi.length ? new RegExp(`(?<![\\u0600-\\u06FF])(?:${multi.join("|")})(?![\\u0600-\\u06FF])`, "g") : null;
  }
  I18N.on = true; h.lang = "en"; h.dir = "ltr";
  trNode(document.body); document.title = trString(document.title) ?? document.title;
  if (!I18N.obs) {
    I18N.obs = new MutationObserver((muts) => {
      if (!I18N.on) return;
      for (const m of muts) {
        if (m.type === "characterData") trNode(m.target);
        else if (m.type === "attributes") trNode(m.target);
        else m.addedNodes.forEach(trNode);
      }
    });
  }
  I18N.obs.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["placeholder", "title", "aria-label"] });
}
if (PREFS.lang === "en") document.addEventListener("DOMContentLoaded", () => setLanguage("en"));
async function savePrefs(changes) {
  const p = await api("/api/me/prefs", { method: "PUT", body: changes });
  if (ME) ME.prefs = p;
  applyPrefs(p);
  if ("lang" in changes || changes.reset) await setLanguage(p.lang);
  return p;
}
document.addEventListener("keydown", (e) => {        // Ctrl + / Ctrl - : font size for this person
  if (!(e.ctrlKey || e.metaKey) || !ME) return;
  const d = e.key === "=" || e.key === "+" ? 1 : e.key === "-" ? -1 : 0;
  if (!d) return;
  e.preventDefault();
  const i = Math.min(FONT_STEPS.length - 1, Math.max(0, FONT_STEPS.indexOf(PREFS.font) + d));
  savePrefs({ font: FONT_STEPS[i] }).catch(() => {});
});
// launch day 21/11: a number field showing 0 / 0.00 is selected on focus, so typing replaces it (typing 1185
// in front of the shift's "0" gave 11850). Every screen, one place.
const isZeroField = (el) => el instanceof HTMLInputElement && !el.readOnly && !el.disabled &&
  (el.type === "number" || /^(numeric|decimal)$/.test(el.inputMode || "")) && el.type !== "password" &&
  el.value.trim() !== "" && Number(el.value) === 0;
let zeroPick = null;
document.addEventListener("focusin", (e) => {
  if (!isZeroField(e.target)) return;
  zeroPick = e.target;
  e.target.select();
});
document.addEventListener("mouseup", (e) => {   // the click that focused it would otherwise drop the selection
  if (zeroPick && e.target === zeroPick) { e.preventDefault(); zeroPick.select(); }
  zeroPick = null;
});
// item names as the person chose: both (English + Arabic under it), English only, Arabic only
const itemText = (it) => (PREFS.names === "ar" ? it.name_ar || it.name_en : it.name_en || it.name_ar) || "";
const itemHtml = (it) => (PREFS.names === "both" && it.name_en && it.name_ar
  ? `${esc(it.name_en)}<span class="iname-ar">${esc(it.name_ar)}</span>` : esc(itemText(it)));
// search: English or Arabic name, code, barcode, pieces, %, wrong keyboard language
async function findItems(q, limit = 8, stock = "", settleAfterNavigation = false) {
  const sale = stock && location.hash === "#/pos" ? SALE?.id : null;
  const r = await api(`/api/items?q=${encodeURIComponent(q)}&limit=${limit}&meta=1${stock ? `&stock=${stock}` : ""}${sale ? `&sale=${encodeURIComponent(sale)}` : ""}`, { settleAfterNavigation });
  return r;
}
const hintHtml = (h) => (!h ? "" : h.kind === "layout"
  ? `<p class="badge b-warn search-hint">⚠ لوحة المفاتيح كانت على اللغة الأخرى — جارٍ البحث عن: <bdi>${esc(h.query)}</bdi></p>`
  : `<p class="badge b-info search-hint">ℹ لا يوجد اسم مطابق تمامًا — هذه أقرب الأصناف</p>`);

const toBp = (v) => (v === "" || v == null ? null : Math.round(parseFloat(v) * 100));
// the server's discount_of: integer basis points, half up, never through a float percentage (#348)
const discountOf = (gross, bp) => (bp ? Math.floor((gross * bp + 5000) / 10000) : 0);

function toast(msg, isError = false, ms = 0) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = isError ? "error" : "";
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), ms || (isError ? 7000 : 3000));
}

const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());

// ------------------------------------------------------------------ on-screen approval
// The owner or a manager types his PIN on this screen; the server checks it once and gives back a
// token for that one action (permission + API path) for 90 seconds, on this screen only (cleared when
// leaving it). The PIN itself is never kept (#137).
let APPROVAL = null;
const approvalHeaders = (action, operation) => {
  if (APPROVAL && APPROVAL.until > Date.now()) return APPROVAL.action === action && APPROVAL.operation === operation ? { "X-Approval": APPROVAL.token } : {};
  APPROVAL = null; return {};
};
window.addEventListener("hashchange", () => { APPROVAL = null; });

async function askApproval(details, message, method, path, operation) {
  let list = [];
  try { list = (await api("/api/approvers")).approvers; } catch { /* offline */ }
  if (!list.length) {
    toast(`${message}. لا يوجد مالك أو مدير لديه رقم سري للموافقة — يضبط المالك رقمه من صفحة "حسابي"`, true);
    return false;
  }
  let dlg = $("#approveDlg");
  if (!dlg) { dlg = document.createElement("dialog"); dlg.id = "approveDlg"; document.body.append(dlg); }
  const summary = details?.summary;
  const facts = summary ? [
    summary.amount == null ? null : `المبلغ: ${money(summary.amount)} ج`,
    summary.public_price == null ? null : `السعر: ${money(summary.public_price)} ج`,
    summary.qty == null ? null : `الكمية: ${summary.qty}`,
  ].filter(Boolean) : [];
  dlg.innerHTML = `<form method="dialog" class="approve">
    <h2>يحتاج إلى موافقة</h2>
    <p class="badge b-warn">⚠ ${esc(details?.label || message)}</p>
    <p class="muted">${esc(message)}</p>
    ${summary ? `<p class="small"><bdi>${esc(summary.target)}</bdi>${facts.map((fact) => ` · <span>${esc(fact)}</span>`).join("")}</p>` : ""}
    <label class="f">من سيوافق <select name="who">${list.map((a) => `<option value="${esc(a.id)}">${esc(a.full_name)} — ${esc(a.role_name)}</option>`).join("")}</select></label>
    <label class="f">رقمه السري <input name="pin" type="password" inputmode="numeric" autocomplete="off" maxlength="6" required></label>
    <p class="badge b-err" data-err hidden></p>
    <div class="row mt"><button class="btn" value="ok">موافقة</button><button class="btn secondary" value="cancel" formnovalidate>إلغاء</button></div>
  </form>`;
  dlg.showModal();
  dlg.querySelector("[name=pin]").focus();
  return await new Promise((resolve) => {
    const form = dlg.querySelector("form");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (e.submitter?.value === "cancel") { dlg.close(); resolve(false); return; }
      const who = form.who.value, pin = form.pin.value.trim(), err = dlg.querySelector("[data-err]");
      const r = await fetch("/api/approvals/check", { method: "POST", credentials: "same-origin",
        headers: { "X-Requested-With": "pharm", "Content-Type": "application/json", "X-Approver": who, "X-Approver-Pin": pin },
        body: JSON.stringify({ permission: details?.permission || "", method, path, challenge: details?.challenge,
          previous: APPROVAL && APPROVAL.id === who && APPROVAL.action === `${method} ${path}` && APPROVAL.operation === operation && APPROVAL.until > Date.now() ? APPROVAL.token : null }) });
      const d = await r.json().catch(() => ({}));
      const enough = r.ok && d.ok && d.token && (details?.permission !== "discount" || d.discount_limit_bp >= (details.requested_bp || 0));
      if (!enough) {
        err.hidden = false;
        err.textContent = !r.ok ? `✕ ${d?.error?.message || "الرقم السري غير صحيح"}` : "✕ الشخص الذي اخترته لا يملك هذه الصلاحية";
        form.pin.value = ""; form.pin.focus(); return;
      }
      APPROVAL = { id: who, action: `${method} ${path}`, operation, token: d.token, until: Date.now() + Math.max(0, (d.expires_in || 90) - 5) * 1000 };
      dlg.close(); resolve(true);
    });
    dlg.addEventListener("cancel", () => resolve(false), { once: true });
  });
}

const API = { busy: 0, last: 0 };
// set by wireRange() while a period change starts its reads: a GET answered after a newer choice is dropped (#324 R25)
let RANGE_CALL = null;
const RANGE_SEQ = {};
const API_MAX_APPROVALS = 8;                    // more than any action needs; stops a loop
// Money forms: the same POST (path + body) sent again within 4 seconds is a double click or a double
// Enter, so it carries the SAME Idempotency-Key and the server replays or refuses it instead of posting
// twice. Only these routes: repeating a cashier action on purpose (the same item scanned twice) stays two.
const ONCE_MS = 4000, ONCE_SENT = new Map();
// A money form whose every attempt lost its answer has an UNKNOWN outcome: sent again unchanged, however much
// later, it carries the same key, so the server replays it or says it is unconfirmed instead of posting a second
// time. The key is dropped once any answer comes back (#324 M2-10).
const UNSURE = new Map();
const ONCE_PATHS = [/^\/api\/customers\/[^/]+\/payments$/, /^\/api\/expenses$/, /^\/api\/expense-schedules\/[^/]+$/,
  /^\/api\/staff$/, /^\/api\/suppliers\/[^/]+\/pay$/, /^\/api\/cash\/movements$/, /^\/api\/estock\/sales\/[^/]+\/return$/,
  // money accounts (F105/F108): a transfer, the owner's money in or out, a count, an opening fix, a reconciliation
  /^\/api\/money\/transfer$/, /^\/api\/money\/owner$/, /^\/api\/money\/accounts\/[^/]+\/(count|adjust)$/, /^\/api\/reconcile$/,
  // part 3 item 2: an opening balance corrected after the move
  /^\/api\/opening-balances\/(supplier|customer)\/[^/]+$/,
  // part 3 item 6a: a return with its replacement sale
  /^\/api\/sales\/[^/]+\/(exchange|return)$/,
  /^\/api\/purchases\/[^/]+\/approve$/,
  // a return to a supplier (by item or from its invoice): an unanswered save sent again replays (Codex P1 on #396)
  /^\/api\/supplier-returns$/,
  // U4-2: an order recorded as sent: an unanswered «اتبعتت» pressed again replays, never a second order (Codex P2 on #433)
  /^\/api\/procurement\/orders$/,
  /^\/api\/items\/[^/]+\/pack-fix$/, /^\/api\/estock\/(pack-review|customer-name-review)\/[^/]+$/];
function onceKey(method, apiPath, bodyText) {
  if (!onceKey.applies(method, apiPath, bodyText)) return null;
  const now = Date.now(), sig = `${method} ${apiPath} ${bodyText}`;
  if (UNSURE.has(sig)) return UNSURE.get(sig);
  for (const [k, v] of ONCE_SENT) if (now - v.at >= ONCE_MS) ONCE_SENT.delete(k);
  const hit = ONCE_SENT.get(sig);
  if (hit) return hit.key;
  const key = newKey();
  ONCE_SENT.set(sig, { key, at: now });
  return key;
}
onceKey.applies = (method, apiPath, bodyText) => method === "POST" && bodyText !== undefined && ONCE_PATHS.some((re) => re.test(apiPath));
let navigation = 0;
const routeHash = () => location.hash || "#/home";
function onThisScreen() {
  const id = navigation, hash = routeHash();
  return () => id === navigation && hash === routeHash();
}

async function api(path, { method = "GET", body, form, settleAfterNavigation = false, _approvals = 0, _key = null, _asked = [] } = {}) {
  const apiPath = path.split("?")[0], range = method === "GET" ? RANGE_CALL : null;
  const current = method === "GET" && !settleAfterNavigation ? onThisScreen() : () => true;
  let sig = null;
  const opts = { method, headers: { "X-Requested-With": "pharm" }, credentials: "same-origin" };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
    _key = _key || onceKey(method, apiPath, opts.body);
    if (onceKey.applies(method, apiPath, opts.body)) sig = `${method} ${apiPath} ${opts.body}`;
  }
  // A write approval uses the same operation key as the request and its retries.
  if (!["GET", "HEAD"].includes(method) && !path.startsWith("/api/auth/") && !path.startsWith("/api/setup")) opts.headers["Idempotency-Key"] = _key || newKey();
  Object.assign(opts.headers, approvalHeaders(`${method} ${apiPath}`, opts.headers["Idempotency-Key"]));
  let r, data;
  API.busy++;                                     // a screen with a request in flight is never reloaded by itself (#153)
  try {
    for (let attempt = 0; ; attempt++) {
      // Leave the abandoned view suspended, including its catch/final render paths.
      if (!current()) return new Promise(() => {});
      try { r = await fetch(path, opts); break; }
      catch (netErr) {
        if (!current()) return new Promise(() => {});
        if (method === "GET" && attempt >= 1) throw new Error("لا يوجد اتصال بالخادم");
        if (attempt >= 2) {
          if (sig && opts.headers["Idempotency-Key"]) UNSURE.set(sig, opts.headers["Idempotency-Key"]);
          throw new Error("لا يوجد اتصال بالخادم — لم يتأكد حفظ العملية، راجعها قبل التكرار");
        }
        await new Promise((res) => setTimeout(res, 800 * (attempt + 1)));
      }
    }
    data = await r.json().catch(() => ({}));
    if (!current() || (range && RANGE_SEQ[range.id] !== range.seq)) return new Promise(() => {});   // a newer period was chosen
    // an answer came: the outcome is known, unless the server itself says the first attempt is unconfirmed
    if (sig && data?.error?.code !== "idempotency_unconfirmed") UNSURE.delete(sig);
    // An acknowledged partial return can be followed by another of the same quantity.
    if (sig && r.ok && /^\/api\/sales\/[^/]+\/return$/.test(apiPath)) ONCE_SENT.delete(sig);
  } finally { API.busy--; API.last = Date.now(); }
  // one approval per distinct permission the action needs (a late restricted return of someone else's
  // invoice needs four), each added to the same token; a permission asked twice means it was not granted
  const perm = data?.error?.details?.permission;
  if (!r.ok && r.status === 403 && data?.error?.code === "approval_required" && _approvals < API_MAX_APPROVALS && !_asked.includes(perm)
      && (method === "GET" || data.error.details?.challenge)) {
    if (await askApproval(data.error.details, data.error.message, method, apiPath, opts.headers["Idempotency-Key"])) return api(path, { method, body, form, settleAfterNavigation, _approvals: _approvals + 1, _key: opts.headers["Idempotency-Key"], _asked: [..._asked, perm] });
  }
  if (!r.ok && opts.headers["X-Approval"] && ["approval_expired", "approval_mismatch", "approval_used", "approver_invalid"].includes(data?.error?.code)) {
    APPROVAL = null;
    // same Idempotency-Key: if the first attempt did post, the server replays it instead of posting again
    if (_approvals < API_MAX_APPROVALS) return api(path, { method, body, form, settleAfterNavigation, _approvals: _approvals + 1, _key: opts.headers["Idempotency-Key"], _asked: [] });
  }
  if (!r.ok) {
    if (r.status === 401 && !path.startsWith("/api/auth")) { ME = null; location.hash = "#/login"; }
    const err = new Error(data?.error?.message || "خطأ في الاتصال");
    err.code = data?.error?.code; err.details = data?.error?.details;
    throw err;
  }
  return data;
}

// One request per press: the button is off while its request runs (a double click or a second Enter does nothing).
async function withBusy(btn, fn) {
  if (btn?.disabled) return;
  if (btn) btn.disabled = true;
  try { return await fn(); } finally { if (btn) btn.disabled = false; }
}

// A hidden frame prints the page (print.js prints on load): no new tab, so a pop-up blocker cannot stop it
// even after an await. The frame stays until the next print, so the print dialog keeps its document.
function printInFrame(url) {
  document.getElementById("printFrame")?.remove();
  const f = document.createElement("iframe");
  f.id = "printFrame";
  f.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
  f.src = url;
  document.body.appendChild(f);
}

// ------------------------------------------------------------------ dhikr ticker and work hours (U3-7)
// The ticker is a passive strip at the very bottom (pointer-events: none, never over a button, hidden in print);
// the work-hours reminder is one toast WARN_MIN minutes before the end of the person's hours. Neither stops work.
let STAFF_UI = null, STAFF_TIMERS = [], STAFF_LOADING = null;
function staffUiStop() {                 // on sign-out and before every reload of the settings (review P1)
  STAFF_TIMERS.forEach(clearInterval); STAFF_TIMERS = [];
  document.getElementById("dhikrBar")?.remove();
  document.body.style.paddingBottom = "";
}
// minutes left until a shift end «HH:MM» seen from `now` (the end after midnight when it is still ahead)
function shiftEndLeft(now, endHHMM) {
  const [eh, em] = endHHMM.split(":").map(Number), end = new Date(now);
  end.setHours(eh, em, 0, 0);
  if (end - now < -12 * 3600000) end.setDate(end.getDate() + 1);
  return (end - now) / 60000;
}
async function staffUi(force = false) {
  const uid = ME?.user?.id;
  if (!uid || (!force && STAFF_UI?.uid === uid && STAFF_TIMERS.length) || STAFF_LOADING === uid) return;
  STAFF_LOADING = uid;
  let cfg;
  // a failed reload keeps the running strip, reminder and refresh (Codex P2 on #421): nothing is stopped before
  try { cfg = { uid, ...(await api("/api/settings/staff-ui", { settleAfterNavigation: true })) }; } catch { STAFF_LOADING = null; return; }
  STAFF_LOADING = null;
  if (ME?.user?.id !== uid) return;      // someone else signed in meanwhile
  staffUiStop();
  STAFF_UI = cfg;
  // the owner's changes reach open sessions within 10 minutes (Codex P2 on #421)
  STAFF_TIMERS.push(setInterval(() => { staffUi(true); }, 600000));
  if (cfg.dhikr.length) {
    const bar = Object.assign(document.createElement("div"), { id: "dhikrBar", className: "dhikr noprint" });
    bar.setAttribute("aria-hidden", "true");
    bar.style.cssText = "position:fixed;inset-inline:0;bottom:0;height:1.4em;line-height:1.4em;font-size:.8em;opacity:.75;"
      + "pointer-events:none;overflow:hidden;white-space:nowrap;text-align:center;z-index:1;background:var(--panel,transparent)";
    let i = 0;
    const show = () => { bar.textContent = cfg.dhikr[i++ % cfg.dhikr.length]; };
    show(); STAFF_TIMERS.push(setInterval(show, 20000));
    document.body.append(bar);
    document.body.style.paddingBottom = "1.6em";
  }
  const h = cfg.hours;
  if (h?.end) {
    const tick = () => {
      const now = new Date(), left = shiftEndLeft(now, h.end), key = `endWarn:${uid}:${h.end}:${new Date(now.getTime() + left * 60000).toDateString()}`;
      if (left > 0 && left <= cfg.warn_min && !sessionStorageGet(key)) {
        sessionStorageSet(key, "1");
        toast(`⏰ شيفتك بيخلص الساعة ${h.end} (فاضل ${Math.ceil(left)} دقيقة): لو هتمشي اقفل الشيفت وسجّل انصرافك`);
      }
    };
    tick(); STAFF_TIMERS.push(setInterval(tick, 60000));
  }
}
function sessionStorageGet(k) { try { return sessionStorage.getItem(k); } catch { return null; } }
function sessionStorageSet(k, v) { try { sessionStorage.setItem(k, v); } catch { /* blocked storage: warn again later */ } }

// ------------------------------------------------------------------ routing
const routes = [
  [/^#\/login$/, viewLogin],
  [/^#\/setup$/, viewSetup],
  [/^#\/purchases$/, async () => { await viewPurchases(); estockPanel("list", "purchase"); }],
  [/^#\/purchases\/([\w-]+)$/, viewInvoice],
  [/^#\/items$/, viewItems],
  [/^#\/items\/([\w-]+)\/edit$/, viewItemEdit],
  [/^#\/items\/([\w-]+)$/, async (id) => { await viewItem(id); estockPanel("item", id); }],
  [/^#\/categories$/, viewCategories],
  [/^#\/groups$/, viewGroups],
  [/^#\/shift$/, viewShift],
  [/^#\/money$/, viewMoney],
  [/^#\/money\/([\w-]+)$/, viewMoneyAccount],
  [/^#\/reconcile$/, viewReconcile],
  [/^#\/exchange$/, viewExchange],
  [/^#\/opening-fix$/, viewOpeningFix],
  [/^#\/suppliers$/, viewSuppliers],
  [/^#\/suppliers\/([\w-]+)$/, async (id) => { await viewSupplier(id); estockPanel("supplier", id); }],
  [/^#\/estock(?:\?.*)?$/, viewEstock],
  [/^#\/estock\/(sale|sale_return|purchase)\/([^/?]+)$/, viewEstockInvoice],
  [/^#\/estock-statement\/(customer|supplier)\/([\w-]+)$/, viewEstockStatement],
  [/^#\/estock-item\/([\w-]+)$/, viewEstockItem],
  [/^#\/shortages$/, viewShortages],
  [/^#\/transfers$/, viewTransfers],
  [/^#\/audit$/, viewAudit],
  [/^#\/supplier-returns\/invoice$/, viewSupplierReturnInvoice],
  [/^#\/supplier-returns(?:\/(new|[\w-]+))?(?:\?.*)?$/, viewSupplierReturns],
  [/^#\/customers$/, viewCustomers],
  [/^#\/expenses$/, viewExpenses],
  [/^#\/home$/, viewHome],
  [/^#\/staff$/, viewStaff],
  [/^#\/attendance$/, viewAttendance],
  [/^#\/feedback$/, viewFeedback],
  [/^#\/reports$/, viewReports],
  [/^#\/owner-reports$/, viewOwnerReports],
  [/^#\/stock-check$/, viewStockCheck],
  [/^#\/profit$/, viewProfit],
  [/^#\/day-review$/, viewDayReview],
  [/^#\/hikma-claim$/, viewHikmaClaim],
  [/^#\/dispensing-notes$/, viewDispensingNotes],
  [/^#\/item-health$/, viewItemHealth],
  [/^#\/pack-review$/, viewPackReview],
  [/^#\/customer-name-review$/, viewCustomerNameReview],
  [/^#\/customers\/([\w-]+)$/, async (id) => { await viewCustomer(id); estockPanel("customer", id); }],
  [/^#\/import$/, viewImport],
  [/^#\/import\/([\w-]+)$/, viewImportRun],
  [/^#\/estock-move$/, viewEstockMove],
  [/^#\/recalls$/, viewRecalls],
  [/^#\/counts$/, viewCounts],
  [/^#\/procurement$/, viewProcurement],
  [/^#\/deadstock$/, viewDeadstock],
  [/^#\/sales$/, async () => { await viewSales(); estockPanel("list", "sale"); }],
  [/^#\/system$/, viewSystem],
  [/^#\/sales\/([\w-]+)$/, viewSale],
  [/^#\/pos$/, viewPos],
  [/^#\/counts\/([\w-]+)$/, viewCountSession],
  [/^#\/users$/, viewUsers],
  [/^#\/users\/([\w-]+)$/, viewUser],
  [/^#\/notifications$/, viewNotifications],
  [/^#\/me$/, viewMe],
  [/^#\/customize$/, viewCustomize],
  [/^#\/setup-printers$/, viewSetupPrinters],
];

async function route() {
  navigation++;
  const hash = routeHash(), current = onThisScreen();
  if (!ME && hash !== "#/setup") {
    try { if ((await api("/api/setup")).needed) { location.hash = "#/setup"; return; } } catch { /* offline: fall through */ }
  }
  if (!ME && hash !== "#/login" && hash !== "#/setup") {
    try { ME = await api("/api/me"); } catch { location.hash = "#/login"; return; }
  }
  $("#topbar").hidden = !ME;
  if (ME) {
    if (ME.prefs) { applyPrefs(ME.prefs); if ((ME.prefs.lang === "en") !== I18N.on || (I18N.want && I18N.want !== ME.prefs.lang)) setLanguage(ME.prefs.lang); }
    $("#who").textContent = `${ME.user.full_name} (${ME.role_name || ""}) — ${ME.organization.name}`;
    staffUi();
    $("#navUsers").hidden = !(ME.permissions.includes("users.manage") || ME.permissions.includes("users.permissions"));
    $("#navProfit").hidden = !ME.permissions.includes("costs.read");
    $("#navStaff").hidden = !ME.permissions.includes("payroll.manage");
    $("#navTransfers").hidden = !ME.permissions.includes("sales.totals");
    $("#navAudit").hidden = !ME.permissions.includes("audit.read");
    $("#navSupRet").hidden = !ME.permissions.includes("purchases.read");
    $("#navOwnerRep").hidden = !ME.permissions.includes("sales.totals");
    $("#navCustomize").hidden = !ME.permissions.includes("settings.manage");
    if (!ME.ui) { try { ME.ui = await api("/api/settings/ui"); } catch { ME.ui = null; } }   // login answers without it
    applyNav();
    $("#bell").hidden = !ME.permissions.includes("notifications.read");
    if (ME.notifications_unread === undefined) { ME.notifications_unread = 0; refreshBell(); } else setBell(ME.notifications_unread);
    if (!route.bellTimer) route.bellTimer = setInterval(refreshBell, 60000);
  }
  if (!ME) showStockAlert(null);
  else if (ME.stock_alert !== undefined) { showStockAlert(ME.stock_alert); delete ME.stock_alert; }
  else refreshStockAlert();
  if (ME && !route.alertTimer) route.alertTimer = setInterval(refreshStockAlert, 60000);
  // per signed-in person: after a logout and another login in the same tab, the new person's view is read now
  if (!ME) { showBackupAlert(null); clearInterval(route.backupTimer); route.backupTimer = null; route.backupUser = null; }
  else if (route.backupUser !== ME.user.id) {
    route.backupUser = ME.user.id; clearInterval(route.backupTimer);
    refreshBackupAlert(); route.backupTimer = setInterval(refreshBackupAlert, 300000);
  }
  if (ME && !route.badgeChecked) { route.badgeChecked = true; checkUpdateBadge(); }
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", hash.startsWith("#/" + a.dataset.nav)));
  $("#navMore")?.classList.toggle("active", !!$("#navMore .menu a.active"));
  for (const [re, fn] of routes) {
    const m = hash.match(re);
    if (m) {
      try { await fn(...m.slice(1)); } catch (e) { if (current()) toast(e.message, true); }
      if (current()) setTimeout(() => { if (current()) tourAuto(); }, 400);
      return;
    }
  }
  location.hash = "#/home";
}
// the last good automatic backup in the top bar; red under it when the last run failed or it is late (two intervals)
async function refreshBackupAlert() {
  if (!ME?.permissions?.includes("settings.manage") || document.hidden) { if (!ME) showBackupAlert(null); return; }
  try { showBackupAlert(await api("/api/backup/health")); } catch { /* offline: keep what is shown */ }
}
function backupText(h) {
  if (h.failed) return `✕ آخر نسخة احتياطية تلقائية فشلت${h.last_good_at ? ` — آخر نسخة سليمة: ${stamp(h.last_good_at)}` : ""}. افتح «البرنامج والنسخ الاحتياطي».`;
  return `⚠ النسخة الاحتياطية التلقائية متأخرة — آخر نسخة سليمة: ${stamp(h.last_good_at)}. افتح «البرنامج والنسخ الاحتياطي».`;
}
function showBackupAlert(h) {
  const bar = $("#backupAlert"), when = $("#backupWhen");
  if (!bar || !when) return;
  bar.hidden = !(h && h.warn);
  when.hidden = !h || !("ok" in h);
  if (h && "ok" in h) when.textContent = `آخر نسخة احتياطية: ${h.last_good_at ? stamp(h.last_good_at) : "لا توجد بعد"}`;
  if (h && h.warn) bar.textContent = backupText(h);
  // the same warning on «المزيد» and on «البرنامج والنسخ الاحتياطي», so it is seen from any screen
  for (const id of ["#bkBadge", "#bkBadgeSys"]) { const x = $(id); if (x) x.hidden = !(h && h.warn); }
  const bk = $("#bkBadge");
  if (bk) bk.title = h && h.warn ? (h.failed ? "النسخة الاحتياطية فشلت" : "النسخة الاحتياطية متأخرة") : "";
}
// #202: red banner while the last stock integrity check found balances that do not match the stock ledger
async function refreshStockAlert() {
  if (!ME?.permissions?.includes("audit.read") || document.hidden) { if (!ME) showStockAlert(null); return; }
  try { showStockAlert((await api("/api/stock/alert")).alert); } catch { /* offline: keep what is shown */ }
}
function showStockAlert(a) {
  const bar = $("#stockAlert");
  if (!bar) return;
  bar.hidden = !a;
  if (!a) return;
  const n = Object.values(a.counts || {}).reduce((x, y) => x + y, 0);
  bar.textContent = `⚠ تنبيه: أرصدة المخزون لا تطابق حركات المخزون (${n} اختلاف، آخر فحص ${stamp(a.at)}). لم يُصلَّح شيء تلقائيًا — اضغط لفتح «مطابقة الأرصدة».`;
}
// ------------------------------------------------------------------ help, top bar, shortcuts, columns (Task 8)
const SCREENS = {
  pos: ["شاشة البيع", "sales.create"], sales: ["فواتير البيع", "sales.read"], exchange: ["مرتجع واستبدال", "sales.return"], purchases: ["الشراء", "purchases.read"],
  items: ["الأصناف", "items.read"], counts: ["الجرد", "inventory.count"], shift: ["الشيفت والخزينة", "sales.create"],
  customers: ["العملاء والآجل", "sales.read"], reports: ["التقارير", null], "owner-reports": ["تقارير المالك", "sales.totals"],
  expenses: ["المصروفات", "expenses.record"], transfers: ["مراجعة التحويلات", "sales.totals"], profit: ["الربح", "costs.read"], "day-review": ["مراجعة اليوم", "cash.see_expected"], "hikma-claim": ["مطالبة الحكمة", "costs.read"], "dispensing-notes": ["ملاحظات الصرف", "settings.manage"], "item-health": ["فحص بيانات الأصناف", "items.price"],
  shortages: ["النواقص", "items.read"], suppliers: ["الموردون وحساباتهم", "suppliers.read"], "supplier-returns": ["مرتجعات للموردين", "purchases.read"],
  procurement: ["الطلبية", "purchases.read"], deadstock: ["الرواكد", "purchases.read"], recalls: ["تنبيهات السحب", "items.read"],
  import: ["نقل البيانات", "items.import"], users: ["المستخدمون", "users.permissions"], audit: ["سجل العمليات", "audit.read"],
  staff: ["الحضور والرواتب", "payroll.manage"], system: ["البرنامج والنسخ الاحتياطي", null], me: ["المظهر واللغة ورقمي السري", null],
  feedback: ["الشكاوى والمقترحات", null], groups: ["المجموعات العلاجية", "items.read"], notifications: ["الإشعارات", "notifications.read"],
  customize: ["تخصيص الواجهة", "settings.manage"], money: ["الخزنة والحسابات", "cash.see_expected"],
  "pack-review": ["مراجعة العبوات بعد النقل", "items.write"],
  "customer-name-review": ["مراجعة أسماء العملاء بعد النقل", "customers.manage"],
  reconcile: ["مطابقة الأرصدة", "cash.see_expected"], "opening-fix": ["تصحيح الأرصدة بعد النقل", "balances.correct"], estock: ["تاريخ إي ستوك", "sales.read"],
};
const canScreen = (k) => !!SCREENS[k] && (!SCREENS[k][1] || ME.permissions.includes(SCREENS[k][1]));
const HELP = {
  "pack-review": ["راجع العبوات المنقولة اللي اسمها فيه عدد والبرنامج مسجلها علبة واحدة.", ["راجع العلبة نفسها، واكتب عدد الوحدات اللي بتتباع وسبب اختيارك."]],
  "customer-name-review": ["راجع أسماء نفس الحساب في إي ستوك وتاريخ البيع.", ["اختار الاسم اللي هيظهر في البرنامج واضغط اعتمد الاسم."]],
  home: ["الصفحة الرئيسية: ملخص اليوم وما يحتاج إلى انتباهك.", ["اضغط على أي تنبيه لفتح الشاشة الخاصة به.", "يسجّل الموظف حضوره وانصرافه من هنا."]],
  pos: ["امسح الباركود أو اكتب أول حروف الصنف، ثم اختر طريقة الدفع وأتمم البيع.", ["Enter يضيف علبة، وShift+Enter يضيف وحدة. F1 البحث، و↑ ↓ تختار صنف من الفاتورة: F2 رصيده في الفروع، F3 كارته، F6 كميته، Delete يشيله. F10 يروح للدفع والمرة التانية يحفظ (وF9 يحفظ على طول)، F4 الإيصال، F11 (أو F8) يفتح الدرج بسبب لمن له صلاحية الخزينة، F7 مرتجع واستبدال، Insert فاتورة جديدة، Ctrl+Tab الفاتورة اللي بعدها، Ctrl+Delete إلغاء الفاتورة.", "الدرج يُفتح تلقائيًا مع أي مبلغ نقدي حتى بدون إيصال (مع الطابعة المباشرة فقط). \"اطبع إيصال آخر فاتورة\" يعيد الطباعة بدون فتح الدرج.", "أسرع بيع: امسح الأصناف، ثم Enter على البحث الفاضي يوديك لخانة «العميل دفع»، وEnter تاني يتمم البيع بالمبلغ بالظبط (أو اكتب المبلغ اللي دفعه الأول). المسح من أي خانة في الشاشة بيضيف الصنف ومبيكتبش في الخانة.", "البيع السريع صف واحد تحت الفاتورة: بيصغر بعد أول صنف، وزرار «افتح ▾» يفتحه.", "إذا كان الصنف ناقصًا اضغط \"↔ مثيل / بديل\".", "مسح الصنف نفسه مرة أخرى يزيد كميته في السطر نفسه.", "تحذيرات الأصناف يجب تأكيدها قبل البيع، ويُسجَّل التأكيد باسمك."]],
  sales: ["فواتير البيع: ابحث وصفِّ واطبع، واضغط على أي فاتورة لعرض أصنافها.", ["يمكن اختيار أكثر من نوع فاتورة معًا.", "الرقم القصير يُبحث به كرقم فاتورة، والأطول كرقم هاتف أيضًا.", "اختر فواتير بعلامة ✓ ثم \"🖨 المحدد\" لطباعتها وحدها."]],
  purchases: ["فواتير الشراء: ارفع ملف الفاتورة أو أدخلها يدويًا، وراجعها ثم اعتمدها.", ["لا يتغير المخزون إلا بعد الاعتماد.", "الخانات المنقطة تحتاج إلى مراجعة قبل الاعتماد."]],
  items: ["الأصناف: ابحث بالاسم العربي أو الإنجليزي أو الكود أو الباركود.", ["حدد أصنافًا بعلامة ✓ لنقلها إلى قسم أو وضع علامة عليها أو تثبيتها في البيع السريع.", "اضغط على الصنف لفتح بطاقته."]],
  "items/1": ["بطاقة الصنف: الرصيد والتشغيلات والأسعار والحركة والمثيل والبديل.", ["التسوية تحتاج إلى سبب، وتظهر في تقرير خسائر المخزون.", "المثيل يظهر عند كتابة المادة الفعالة والتركيز والشكل في تعديل الصنف."]],
  categories: ["الأقسام تنظّم الأصناف وتحدد ما لا يُقبل إرجاعه.", ["\"اقتراح\" يقترح قسمًا للأصناف التي بدون قسم، وأنت من يعتمد."]],
  groups: ["المجموعات العلاجية: أنت من يحدد ما هو بديل لما.", ["أضف مواد فعالة أو أصنافًا بعينها إلى المجموعة.", "البرنامج لا يخمّن أي بديل من تلقاء نفسه."]],
  shift: ["الشيفت والخزينة: افتح الشيفت برصيد البداية، وأغلقه بعدّ النقدية فئةً فئة.", ["لا يظهر المتوقع للموظف إلا بعد الإغلاق.", "أي فرق يحتاج إلى سبب مكتوب."]],
  suppliers: ["الموردون: الحسابات والدفعات وكشف الحساب.", ["اضغط على المورد لعرض كشف حسابه وتسجيل دفعة."]],
  shortages: ["النواقص: ما سُجّل ناقصًا، أو وصل إلى حد الطلب، أو نفد وكان يُباع.", ["تُزال العلامة تلقائيًا عند اعتماد فاتورة شراء الصنف."]],
  transfers: ["مراجعة التحويلات: قارن كل تحويل برسائل البنك أو المحفظة.", ["حدد \"وصل المبلغ\" أو \"بها مشكلة\" من الفاتورة."]],
  audit: ["سجل العمليات: كل عملية باسم من قام بها ووقتها، ولا يمكن تعديله.", []],
  "supplier-returns": ["مرتجعات للموردين: تخرج الكمية من المخزون وتُخصم قيمتها من حساب المورد.", ["اطبع الإذن ليوقّعه مندوب المورد."]],
  customers: ["العملاء والآجل: الأرصدة والتحصيل والطلب الشهري والتعاقدات.", ["ربط العميل بتعاقد يطبّق الخصم تلقائيًا في شاشة البيع."]],
  expenses: ["المصروفات: سجّل المصروف واختر مصدره (الخزينة أو خارجها).", ["المصروفات المجدولة تذكّرك قبل موعدها."]],
  staff: ["الحضور والرواتب: الأيام والغياب والسلف والمكافآت.", []],
  attendance: ["حضوري: أيام حضورك وساعاتها.", []],
  feedback: ["الشكاوى والمقترحات: اكتب أي ملاحظة عن البرنامج.", []],
  reports: ["التقارير: المبيعات، وتحذيرات الأصناف، وخسائر المخزون، والصلاحية.", ["كل تقرير له زر Excel وزر طباعة."]],
  "owner-reports": ["تقارير المالك: الموظفون، والعملاء، والخزينة، والمرتجعات، والخصومات، والأسعار.", ["اختر التقرير من الأزرار ثم الفترة."]],
  profit: ["الربح: المبيعات ناقص التكلفة والمصروفات لفترة.", []],
  "hikma-claim": ["مطالبة مستشفى نور الحكمة لأي فترة، لكل فرع لوحده: مخزن الصيدلية (التكلفة + نص الربح) ومخزن المستشفى (نص الربح).", ["ملف «Excel للمستشفى» من غير تكلفة: ده اللي يتبعت.", "«Excel داخلي» فيه التكلفة والربح: للمالك والمحاسب بس."]],
  "dispensing-notes": ["ملاحظات الصرف: تذكير بيظهر للبائع مرة واحدة في الفاتورة لما يكون فيها صنف بالمادة الفعالة دي، ومبيوقفش البيع.", ["الخمس مواد من دليل قرار هيئة الدواء 375 لسنة 2026 شغالة من الأول.", "أي مادة تانية بتبدأ مقفولة «محتاج تأكيد» لحد ما تشغلها."]],
  "item-health": ["فحص بيانات الأصناف: الأخطاء في عدد الوحدات والأسعار والأسماء والصلاحية والرصيد، صنف صنف.", ["البرنامج مبيغيّرش حاجة لوحده: «صحّح» بيفتح بطاقة الصنف.", "«صحّح العبوة» بيحوّل الرصيد معاه (نفس عدد العلب)."]],
  "day-review": ["مراجعة اليوم: كل حركة يوم في صفحة واحدة: الشراء ومرتجعه، والبيع ومرتجعه، والخصومات، والآجل، وكل طريقة دفع، والخزينة.", ["اختار التاريخ من فوق.", "المتوقع في الخزينة من الشيفتات اللي اتقفلت في اليوم ده."]],
  import: ["نقل البيانات من برنامج آخر: ارفع الملف، وراجع التقرير، ثم طبّق.", ["لا يُطبَّق شيء قبل موافقتك."]],
  "estock-move": ["جرّب نقل إي ستوك على نسخة من قاعدة البرنامج الجديدة وراجع تقرير المطابقة.", ["افتح الشاشة من الكمبيوتر اللي عليه إي ستوك. لو الجهاز فصل، ارجع للشاشة واضغط «كمّل التجربة»."]],
  recalls: ["تنبيهات السحب: الصق منشور هيئة الدواء وراجع التشغيلات المطابقة.", []],
  counts: ["الجرد: ابدأ جردًا شاملًا أو جزئيًا، وسجّل الكميات، ثم راجع الفروق واعتمد.", ["لكل عجز أو زيادة سبب."]],
  procurement: ["الطلبية: الأصناف المنتظمة اللي هتخلص، والكشكول وطلبات العملاء، مقسمة على الموردين بأعلى خصم حقيقي.", ["اختار المدة: أسبوع (الافتراضي) أو أسبوعين أو شهر؛ الأعمدة التلاتة ظاهرة جنب بعض.", "التنبيهات (غالي، ثلاجة، خصم أقل، مطابقة مش مؤكدة) محتاجة موافقتك قبل الطلب.", "بعد ما تبعت الطلبية بنفسك اضغط «اتبعتت: تابعها»، وبعدين علّم كل صنف: وصل، وصل جزء، أو مش موجود عنده.", "لا يطلب البرنامج ولا يبعت شيئًا من تلقاء نفسه."]],
  deadstock: ["الرواكد: من تقارير البرنامج القديم.", []],
  system: ["البرنامج: التحديث، والنسخ الاحتياطي، وشكل الإيصال، وأجهزة البطاقات.", []],
  users: ["المستخدمون: الأدوار والصلاحيات وحدود الخصم.", []],
  notifications: ["الإشعارات: كل ما حدث ويحتاج إلى علمك.", []],
  me: ["المظهر واللغة ورقمي السري: إعدادات خاصة بك وحدك.", []],
  "setup-printers": ["أول تشغيل: اختار طابعة الإيصال وطابعة الملصقات واطبع تجربة قبل نقل البيانات.", ["لو الصفحة في ويندوز فيها ملصقين فوق بعض اختار 2.", "تقدر تغيّر الطابعات بعدين من «البرنامج والنسخ الاحتياطي»."]],
  customize: ["تخصيص الواجهة لهذه الصيدلية: الشريط العلوي، وأزرار الصفحة الرئيسية، والاختصارات.", ["التغيير يظهر لكل المستخدمين.", "الأعمدة تُضبط من زر \"⚙ الأعمدة\" فوق الجدول."]],
  // update 1 (owner 21/11): every screen explains itself; the screens below had no text
  "purchases/1": ["فاتورة شراء: اختار المورد واكتب رقم فاتورته وتاريخها، ضيف الأصناف، وبعدين اعتمدها.", ["المخزون وحساب المورد بيتغيروا بعد الاعتماد بس.", "المسلسل اللي جنب العنوان رقم الفاتورة في البرنامج، وتاريخ الإدخال بيتسجل لوحده."]],
  money: ["الخزنة والحسابات: رصيد كل خزنة ومحفظة وحساب بنك، وحركتها.", ["اضغط على الحساب علشان تشوف كشفه.", "التحويل بين الحسابات بيتسجل باسمك."]],
  "money/1": ["كشف حساب الخزنة أو المحفظة: كل حركة دخلت أو خرجت بوقتها وسببها.", ["اختار الفترة من فوق، وتقدر تطبع أو تطلّع Excel."]],
  reconcile: ["مطابقة الأرصدة: قارن الرصيد اللي في البرنامج باللي في إيدك فعلًا.", ["أي فرق لازم يتكتب سببه، وبيتسجل باسمك."]],
  exchange: ["مرتجع واستبدال: هات الفاتورة برقمها أو بالصنف أو بالعميل، وحدد اللي راجع واللي هياخده بداله.", ["البرنامج بيحسب الفرق: يا ترجّع فلوس يا تحصّل.", "F7 من شاشة البيع بيفتح الشاشة دي."]],
  "opening-fix": ["تصحيح الأرصدة بعد النقل: لو رصيد مورد أو عميل جه من إي ستوك غلط، صححه هنا.", ["لازم تكتب السبب، والتصحيح بيتسجل تسوية افتتاحية باسمك."]],
  estock: ["تاريخ إي ستوك: الفواتير القديمة اللي اتنقلت من إي ستوك، للاطلاع بس.", ["دوّر برقم الفاتورة أو بالتاريخ."]],
  "estock-statement": ["كشف حساب قديم من إي ستوك للعميل أو المورد، للاطلاع بس.", []],
  "estock-item": ["حركة الصنف القديمة في إي ستوك، للاطلاع بس.", []],
  "stock-check": ["مطابقة المخزون: البرنامج بيقارن أرصدة الأصناف بدفتر الحركات.", ["أي اختلاف بيظهر هنا، ومفيش حاجة بتتصلح لوحدها."]],
};
const helpKey = () => { const p = (location.hash || "#/home").slice(2).split(/[/?]/); const k = p[1] && HELP[`${p[0]}/1`] && p[1] !== "new" ? `${p[0]}/1` : p[0]; return HELP[k] ? k : null; };
function addHelpLine() {
  if (!ME || PREFS.help === "off" || $("#app .helpline")) return;
  const k = helpKey(), h1 = $("#app h1");
  if (!k || !h1) return;
  h1.insertAdjacentHTML("afterend", `<p class="helpline noprint">ℹ <span>${esc(HELP[k][0])}</span> <button type="button" class="link" data-help>المزيد ؟</button></p>`);
  $("#app [data-help]").addEventListener("click", showHelp);
}
new MutationObserver(addHelpLine).observe(app, { childList: true });
function showHelp() {
  if (!ME) return;
  const k = helpKey(), [line, tips] = HELP[k] || ["", []];
  let dlg = $("#helpDlg");
  if (!dlg) { dlg = document.createElement("dialog"); dlg.id = "helpDlg"; document.body.append(dlg); }
  const sc = Object.entries(ME.ui?.shortcuts || {}).filter(([, s]) => canScreen(s));
  dlg.innerHTML = `<form method="dialog" class="helpdlg"><h2>مساعدة: ${esc((SCREENS[(k || "").split("/")[0]] || [k ? "" : "هذه الشاشة"])[0] || "هذه الشاشة")}</h2>
    ${line ? `<p>${esc(line)}</p>` : ""}${tips.length ? `<ul>${tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
    <h3>الاختصارات</h3><ul class="plain">${sc.map(([n, s]) => `<li><kbd>Alt+${n}</kbd> ${esc(SCREENS[s][0])}</li>`).join("")}<li><kbd>Alt+0</kbd> هذه المساعدة</li></ul>
    <label class="chk mt"><input type="checkbox" id="helpLineTgl" ${PREFS.help === "off" ? "" : "checked"}> إظهار سطر المساعدة أعلى كل شاشة</label>
    <div class="row mt"><button type="button" class="btn secondary" data-tour-dlg="main">جولة في القوائم</button>${tourScreen() ? `<button type="button" class="btn secondary" data-tour-dlg="${tourScreen()}">اشرح الشاشة دي</button>` : ""}
      ${ME.permissions.includes("settings.manage") ? `<a class="btn secondary" href="#/customize">تخصيص الواجهة</a>` : ""}<button class="btn">إغلاق</button></div></form>`;
  dlg.querySelectorAll("[data-tour-dlg]").forEach((b) => b.addEventListener("click", () => { dlg.close(); tourStart(b.dataset.tourDlg); }));
  dlg.querySelector("#helpLineTgl").addEventListener("change", async (e) => {
    try { await savePrefs({ help: e.target.checked ? "on" : "off" }); $("#app .helpline")?.remove(); addHelpLine(); } catch (er) { toast(er.message, true); }
  });
  dlg.querySelector("a")?.addEventListener("click", () => dlg.close());
  dlg.showModal();
}
$("#helpBtn")?.addEventListener("click", showHelp);
document.addEventListener("keydown", (e) => {
  if (!ME || !e.altKey || e.ctrlKey || e.metaKey || !/^Digit\d$/.test(e.code) || $("dialog[open]")) return;
  const n = e.code.slice(5);
  if (n === "0") { e.preventDefault(); showHelp(); return; }
  const s = ME.ui?.shortcuts?.[n];
  if (s && canScreen(s)) { e.preventDefault(); location.hash = `#/${s}`; }
});
// ------------------------------------------------------------------ F36 guided tour (#247) — BEGIN
// First sign-in of every user (after first-run setup too): a bubble with an arrow walks through the top bar,
// then each main screen explains itself the first time it opens. «اشرح الشاشة دي» replays a screen.
// Never blocks a sale: no backdrop, the ring ignores the mouse, and on the sale screen the focus stays in the
// search box (a scan still lands there). Esc closes. "Seen" is kept per pharmacy + user in this browser
// (the server's per-user prefs accept only known keys, and a migration is not worth it for this).
// A step is [selectors, title, text, permission?]; "a|b" means: a if it is on screen, else b.
const TOUR_MORE = "#navMore > summary";
const TOUR = {
  main: [
    [".brand", "الصفحة الرئيسية", "من هنا ترجع للصفحة الرئيسية: ملخص اليوم والتنبيهات اللي محتاجة تتشاف."],
    ["a[data-nav=pos]|" + TOUR_MORE, "شاشة البيع", "هنا بتبيع للعميل: تمسح الباركود أو تكتب اسم الصنف، تختار طريقة الدفع وتقفل الفاتورة.", "sales.create"],
    ["a[data-nav=sales]|" + TOUR_MORE, "فواتير البيع", "كل الفواتير اللي اتباعت: تدور على فاتورة، تطبعها تاني، أو تعمل منها مرتجع.", "sales.read"],
    ["a[data-nav=purchases]|" + TOUR_MORE, "الشراء", "لما بضاعة توصل من المورد: ترفع فاتورته أو تدخلها بإيدك، تراجعها وتعتمدها. المخزون يزيد بعد الاعتماد بس.", "purchases.read"],
    ["a[data-nav=items]|" + TOUR_MORE, "الأصناف", "كل الأصناف: تدور على صنف، تشوف رصيده وصلاحيته، وتعدّل سعره.", "items.read"],
    ["a[data-nav=counts]|" + TOUR_MORE, "الجرد", "لما تعد البضاعة اللي على الرف وتقارنها برصيد البرنامج.", "inventory.count"],
    ["a[data-nav=shift]|" + TOUR_MORE, "الشيفت والخزينة", "أول اليوم تفتح الشيفت بالفلوس اللي في الدرج، وآخره تقفلها وتعد الفلوس فئة فئة.", "sales.create"],
    ["a[data-nav=reports]|" + TOUR_MORE, "التقارير", "مبيعات اليوم والشهر، والصلاحية، والخسائر. كل تقرير له زرار Excel وزرار طباعة."],
    ["a[data-nav=system]|" + TOUR_MORE, "البرنامج والنسخ الاحتياطي", "النسخة الاحتياطية بتحفظ بياناتك لو الجهاز باظ، ومن هنا كمان زرار تحديث البرنامج.", "settings.manage"],
    [TOUR_MORE, "المزيد", "باقي الشاشات هنا: العملاء والآجل، والمصروفات، والموردين، والطلبية، وغيرهم."],
    ["#helpBtn", "المساعدة", "لو تهت في أي شاشة دوس هنا (أو Alt+0) تلاقي شرحها واختصاراتها، وتقدر تعيد الجولة دي."],
    ["#fbBtn", "ملاحظة", "لقيت مشكلة أو عندك اقتراح للبرنامج؟ اكتبه من هنا."],
    ["#logout", "خروج", "لما تسيب الجهاز اعمل خروج، عشان محدش يشتغل باسمك."],
  ],
  pos: [
    ["#posQ", "البحث", "امسح الباركود هنا، أو اكتب أول حروف الصنف بالعربي أو الإنجليزي. Enter يضيف علبة، وEnter على البحث الفاضي يوديك للدفع."],
    ["#posRes [data-add]|#posQ", "علبة ولا شريط؟", "تحت البحث بيظهر زرار «علبة» وزرار الوحدة (شريط أو قرص). Shift+Enter يضيف وحدة بدل العلبة."],
    ["#posCart", "الفاتورة", "الأصناف اللي اتضافت. + و − يغيّروا الكمية، و✕ يشيل الصنف."],
    ["#posSide .paym|#posSide", "طريقة الدفع", "اختار نقدي أو بطاقة أو تحويل أو آجل (الآجل محتاج عميل). لو العميل دفع بطريقتين دوس «تقسيم الدفع»."],
    ["#posSide .rcpt", "الإيصال", "علّم هنا لو العميل عايز إيصال مطبوع. F4 بيعمل نفس الحاجة."],
    ["#posDone", "إتمام البيع", "لما تخلص دوس هنا أو Enter في خانة «العميل دفع» أو F10 (أول مرة يروح للدفع، والتانية يحفظ) أو F9: الفاتورة تتسجل والمخزون يتخصم."],
    ["#posKick", "فتح الدرج (F8)", "محتاج تفتح الدرج من غير بيع (فكة مثلًا)؟ دوس F8 واختار السبب، وده بيتسجل باسمك.", "cash.manage"],
    ["#tabNew", "فاتورة جديدة", "عميل تاني جه وانت لسه ماخلصتش؟ افتح فاتورة جديدة (F2)، والأولى تفضل محفوظة."],
  ],
  purchases: [
    ["#upForm", "قراءة فاتورة المورد", "ارفع ملف الفاتورة (Excel أو PDF أو صورة) والبرنامج يقراها، وانت تراجعها قبل الاعتماد."],
    ["#manForm", "فاتورة بإيدك", "مفيش ملف؟ اختار المورد واكتب رقم فاتورة المورد، وابدأ تدخل الأصناف بإيدك."],
    ["#puRep", "الفواتير", "كل فواتير الشراء. المسودة لسه ماغيّرتش المخزون؛ المعتمدة بس هي اللي زوّدته."],
  ],
  purchase: [
    ["[data-h=supplier_id]", "المورد", "اختار المورد اللي جت منه الفاتورة."],
    ["[data-h=invoice_no]", "رقم فاتورة المورد", "الرقم المطبوع على فاتورة المورد. المسلسل اللي جنب العنوان رقم الفاتورة في البرنامج."],
    ["#addLine", "الأصناف", "ضيف كل صنف بكميته وسعره وتاريخ الصلاحية، والبونص لو فيه."],
    ["#approve", "الاعتماد", "بعد المراجعة اعتمد الفاتورة: ساعتها بس المخزون يزيد وحساب المورد يتسجل."],
  ],
  items: [
    ["#q", "البحث", "دوّر بالاسم العربي أو الإنجليزي أو الكود أو الباركود."],
    ["a[href='#/items/new']", "صنف جديد", "لو صنف مش موجود، ضيفه من هنا بسعره وعدد الوحدات في العلبة.", "items.write"],
    ["#app table|#itemsHint", "بطاقة الصنف", "اضغط على أي صنف تفتح بطاقته: الرصيد والتشغيلات والسعر والحركة."],
  ],
  shift: [
    ["#shOpen", "فتح الشيفت", "اكتب الفلوس اللي في الدرج دلوقتي وافتح الشيفت. من غيره البيع النقدي واقف."],
    ["#shPay", "مصروف من الخزينة", "لو طلّعت فلوس من الدرج لمصروف، سجّلها هنا بالسبب عشان الحساب يطابق."],
    ["#shClose", "قفل الشيفت", "آخر الشيفت عد الفلوس فئة فئة واكتب العدد. أي فرق محتاج سبب مكتوب."],
    ["#shHist", "آخر الشيفتات", "الشيفتات اللي فاتت: المتوقع والمعدود والفرق، وتقرير كل شيفت."],
  ],
  reports: [
    ["#rRange", "الفترة", "اختار النهارده أو الأسبوع أو الشهر، أو حدد من تاريخ لتاريخ."],
    ["#rSales", "المبيعات", "إجمالي المبيعات والمرتجع والخصومات، يوم بيوم وحسب الموظف."],
    ["#app [data-xl]", "Excel والطباعة", "كل تقرير ليه زرار يطلّعه ملف Excel وزرار يطبعه أو يحفظه PDF."],
    ["#app a[href='#/owner-reports']", "تقارير المالك", "تقارير أعمق: الموظفين، والعملاء، والخزينة، والمرتجعات، والأسعار.", "sales.totals"],
  ],
  system: [
    ["#bkBox", "النسخة الاحتياطية", "البرنامج بيعمل نسخة لوحده. تقدر تعمل نسخة دلوقتي، وخلّي نسخة على فلاشة برّه الجهاز."],
    ["#rcBox", "شكل الإيصال", "اسم الصيدلية وهاتفها على الإيصال، ومقاس الورق، والملصقات."],
    ["#chBox", "البطاقات والتحويل", "أجهزة الفيزا والمحافظ اللي بتستلم عليها، عشان تظهر في شاشة البيع."],
    ["#sysBox", "تحديث البرنامج", "زرار التحديث بياخد نسخة احتياطية الأول، وبعدين يحدّث من غير ما يلمس بياناتك."],
  ],
};
const TOUR_ANCHOR = { pos: "#app p.keys" };              // screens without a heading: where the replay button goes
const tourScreen = () => {
  const h = location.hash || "#/home";
  if (/^#\/purchases\/[\w-]+$/.test(h)) return "purchase";                // one purchase invoice (update 1)
  const m = h.match(/^#\/([\w-]+)$/); return m && TOUR[m[1]] && m[1] !== "main" ? m[1] : null;
};
const TOUR_MEM = {};                                      // when the browser storage is blocked: seen for this tab only
const tourStoreKey = () => `tour:${ME?.organization?.id || ""}:${ME?.user?.id || ""}`;
function tourSeen() {
  let s = TOUR_MEM[tourStoreKey()] || {};
  try { s = { ...JSON.parse(localStorage.getItem(tourStoreKey()) || "{}"), ...s }; } catch { /* storage blocked */ }
  return s;
}
function tourMark(part) {
  const s = { ...tourSeen(), [part]: 1 };
  TOUR_MEM[tourStoreKey()] = s;
  try { localStorage.setItem(tourStoreKey(), JSON.stringify(s)); } catch { /* storage blocked: kept for this tab */ }
}
const tourVisible = (el) => !!el && el.getClientRects().length > 0 && !el.closest("[hidden]");
function tourTarget(step) {
  for (const sel of step[0].split("|")) { const el = [...document.querySelectorAll(sel)].find(tourVisible); if (el) return el; }
  return null;
}
const tourAllowed = (step) => !step[3] || !!ME?.permissions?.includes(step[3]);
let TOUR_RUN = null;   // { part, steps, i, then }
function tourStart(part, then = null) {
  tourClose(false);
  const steps = (TOUR[part] || []).filter(tourAllowed);
  if (!ME || !steps.some((s) => tourTarget(s))) return false;
  TOUR_RUN = { part, steps, i: -1, then };
  tourGo(1);
  return true;
}
function tourGo(d) {
  const r = TOUR_RUN;
  if (!r) return;
  let i = r.i + d;
  while (i >= 0 && i < r.steps.length && !tourTarget(r.steps[i])) i += d;   // a control not on screen: skip it
  if (i >= r.steps.length) { const then = r.then; tourClose(true); if (then) tourStart(then); return; }
  if (i < 0) return;
  r.i = i;
  tourDraw();
}
function tourClose(markSeen = true) {
  const r = TOUR_RUN;
  TOUR_RUN = null;
  $("#tourBub")?.remove(); $("#tourRing")?.remove();
  if (r && markSeen) tourMark(r.part);
}
function tourSkip() {            // «تخطّي»: no more automatic parts for this person; the buttons still replay them
  if (!TOUR_RUN) return;
  tourClose(true); tourMark("all");
  toast("تقدر تشوف الشرح تاني من زرار «اشرح الشاشة دي» أو من «؟»");
}
function tourDraw() {
  const r = TOUR_RUN, step = r.steps[r.i], el = tourTarget(step);
  if (!el) { tourGo(1); return; }
  let bub = $("#tourBub"), ring = $("#tourRing");
  if (!bub) {
    ring = document.createElement("div"); ring.id = "tourRing"; ring.className = "tour-ring noprint";
    bub = document.createElement("div"); bub.id = "tourBub"; bub.className = "tour-bub noprint";
    bub.setAttribute("role", "dialog"); bub.setAttribute("aria-modal", "false"); bub.setAttribute("aria-labelledby", "tourT");
    // sale screen: pressing a tour button never takes the focus from the search box (the next scan lands there)
    bub.addEventListener("mousedown", (e) => { if (location.hash.startsWith("#/pos")) e.preventDefault(); });
    bub.addEventListener("click", (e) => {
      const b = e.target.closest("[data-tour-go]");
      if (!b) return;
      if (b.dataset.tourGo === "skip") tourSkip(); else tourGo(+b.dataset.tourGo);
    });
    document.body.append(ring, bub);
  }
  const n = r.steps.filter((s) => tourTarget(s)).length, at = r.steps.slice(0, r.i + 1).filter((s) => tourTarget(s)).length;
  const last = !r.steps.slice(r.i + 1).some((s) => tourTarget(s));
  const more = el.matches(TOUR_MORE) && step[0] !== TOUR_MORE ? "موجودة تحت «المزيد». " : "";
  bub.innerHTML = `<span class="tour-arrow" aria-hidden="true"></span>
    <p class="tour-n muted small">${at} من ${n}</p><h3 id="tourT">${esc(step[1])}</h3><p>${esc(more + step[2])}</p>
    <div class="row tour-btns"><button type="button" class="btn small" data-tour-go="1">${last ? (r.then ? "التالي" : "تمام، فهمت") : "التالي"}</button>
      <button type="button" class="btn small secondary" data-tour-go="-1" ${at > 1 ? "" : "disabled"}>السابق</button>
      <button type="button" class="link" data-tour-go="skip">تخطّي</button></div>`;
  el.scrollIntoView({ block: "nearest", inline: "nearest" });
  tourPlace();
  // keyboard: on the sale screen the focus stays where it is (the scanner types into the search box)
  if (!location.hash.startsWith("#/pos")) bub.querySelector("[data-tour-go='1']").focus({ preventScroll: true });
}
function tourPlace() {
  const r = TOUR_RUN, bub = $("#tourBub"), ring = $("#tourRing");
  if (!r || !bub) return;
  const el = tourTarget(r.steps[r.i]);
  if (!el) { tourGo(1); return; }
  const t = el.getBoundingClientRect(), W = innerWidth, H = innerHeight, gap = 12;
  Object.assign(ring.style, { top: `${t.top - 4}px`, left: `${t.left - 4}px`, width: `${t.width + 8}px`, height: `${t.height + 8}px` });
  const bw = bub.offsetWidth, bh = bub.offsetHeight;
  let top, side;
  if (t.bottom + gap + bh <= H - 8) { top = t.bottom + gap; side = "up"; }               // under the control, arrow up
  else if (t.top - gap - bh >= 8) { top = t.top - gap - bh; side = "down"; }             // over it, arrow down
  else { top = Math.max(8, H - bh - 8); side = "none"; }                                   // a tall control: bottom of the window
  const left = Math.min(Math.max(8, t.left + t.width / 2 - bw / 2), Math.max(8, W - bw - 8));
  Object.assign(bub.style, { top: `${top}px`, left: `${left}px` });
  bub.dataset.side = side;
  bub.style.setProperty("--ax", `${Math.min(Math.max(16, t.left + t.width / 2 - left), bw - 16)}px`);
}
addEventListener("resize", () => tourPlace());
addEventListener("scroll", () => tourPlace(), true);
addEventListener("hashchange", () => tourClose(false));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && TOUR_RUN && !$("dialog[open]")) { e.preventDefault(); tourClose(true); }
}, true);
// after a screen opens: the top-bar part on a person's first sign-in, then each screen's part once
function tourAuto() {
  if (!ME || TOUR_RUN || $("dialog[open]")) return;
  const seen = tourSeen(), k = tourScreen();
  if (seen.all) return;
  const pending = k && !seen[k] && !(k === "pos" && SALE?.lines?.length) ? k : null;   // never over a sale in progress
  if (!seen.main) tourStart("main", pending);
  else if (pending) tourStart(pending);
}
function addTourBtn() {
  // update 1 (owner 21/11): «اشرح الشاشة دي» on every screen: its guided tour where there is one, else its help
  const k = tourScreen() || helpKey();
  if (!ME || !k || $("#app [data-tour-replay]")) return;
  const at = $(TOUR_ANCHOR[k] || "#app h1") || $("#app h2");
  const html = `<button type="button" class="btn small secondary tourbtn noprint" data-tour-replay="${k}">❓ اشرح الشاشة دي</button>`;
  if (!at) app.insertAdjacentHTML("afterbegin", html);
  else if (TOUR_ANCHOR[k]) at.insertAdjacentHTML("beforeend", " " + html); else at.insertAdjacentHTML("afterend", html);
  const btn = $("#app [data-tour-replay]");
  // sale screen: the button never keeps the focus, so the next scan (and its Enter) lands in the search box
  btn.addEventListener("mousedown", (e) => { if (k === "pos") e.preventDefault(); });
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!(TOUR[k] && tourStart(k))) showHelp();
    if (k === "pos") $("#posQ")?.focus();
  });
}
new MutationObserver(addTourBtn).observe(app, { childList: true });
// ------------------------------------------------------------------ F36 guided tour — END
// top bar: the pharmacy's chosen screens first, in its order; the rest stay under "المزيد"
let NAV_ORDER = null;
function applyNav() {
  const nav = $("#topbar nav"), more = $("#navMore"), menu = $("#navMore .menu");
  if (!nav || !more) return;
  const all = [...nav.querySelectorAll("a[data-nav]")];
  NAV_ORDER ||= all.map((a) => a.dataset.nav);
  const byKey = Object.fromEntries(all.map((a) => [a.dataset.nav, a]));
  const main = (ME.ui?.nav_main || []).filter((k) => byKey[k]);
  for (const k of main) nav.insertBefore(byKey[k], more);
  for (const k of NAV_ORDER) if (!main.includes(k) && byKey[k]) menu.append(byKey[k]);
}
// columns the pharmacy hid in a big table (by column key, so a missing column never shifts the others)
const COLS = {
  invoices: ["sel", "no", "time", "customer", "seller", "lines", "gross", "item_disc", "cust_disc", "total", "returns", "net", "cash", "card", "transfer", "credit", "profit"],
  items: ["sel", "name", "category", "pack", "price", "stock", "code"],
};
function applyCols(table, key) {
  if (!table) return;
  const hide = new Set((ME.ui?.columns?.[key] || []).map((i) => COLS[key][i]));
  const ths = [...table.querySelectorAll("thead th")];
  const hiddenIdx = new Set(ths.map((th, i) => (hide.has(th.dataset.c) ? i : -1)).filter((i) => i >= 0));
  table.querySelectorAll("tr").forEach((tr) => {
    let col = 0;
    for (const cell of tr.children) {
      const span = +(cell.dataset.span || cell.colSpan || 1);
      cell.dataset.span = span;
      const covered = [...Array(span).keys()].map((j) => col + j), off = covered.filter((c) => hiddenIdx.has(c)).length;
      if (off === span) cell.hidden = true; else { cell.hidden = false; cell.colSpan = span - off; }
      col += span;
    }
  });
}
function colTools(table, key) {
  if (!table || !ME.permissions.includes("settings.manage")) return "";
  return `<details class="cols noprint"><summary class="btn small secondary">⚙ الأعمدة</summary><div class="menu">
    ${[...table.querySelectorAll("thead th[data-c]")].filter((th) => !["sel", "no", "name"].includes(th.dataset.c)).map((th) => {
      const i = COLS[key].indexOf(th.dataset.c), off = (ME.ui?.columns?.[key] || []).includes(i);
      return `<label class="chk"><input type="checkbox" data-ci="${i}" ${off ? "" : "checked"}> ${esc(th.textContent.trim())}</label>`; }).join("")}
    <p class="muted small">يسري على كل المستخدمين في هذه الصيدلية.</p></div></details>`;
}
function wireCols(box, table, key) {
  box.querySelectorAll("[data-ci]").forEach((cb) => cb.addEventListener("change", async () => {
    const off = [...box.querySelectorAll("[data-ci]")].filter((x) => !x.checked).map((x) => +x.dataset.ci);
    try { ME.ui = await api("/api/settings/ui", { method: "PUT", body: { columns: { [key]: off } } }); applyCols(table, key); }
    catch (er) { toast(er.message, true); cb.checked = !cb.checked; }
  }));
}

async function viewCustomize() {
  if (!ME.permissions.includes("settings.manage")) { app.innerHTML = `<section class="panel"><p>هذه الصفحة للمالك فقط.</p></section>`; return; }
  let ui = await api("/api/settings/ui");
  const keys = Object.keys(SCREENS);
  const draw = () => {
    const pick = (field, max, title, note) => `<section class="panel"><h2>${title}</h2><p class="muted">${note}</p>
      <ol class="plain order">${ui[field].map((k, i) => `<li><b>${esc(SCREENS[k]?.[0] || k)}</b> <span class="row">
        <button type="button" class="btn small secondary" data-up="${field}:${i}" ${i ? "" : "disabled"} aria-label="لأعلى">↑</button>
        <button type="button" class="btn small secondary" data-dn="${field}:${i}" ${i < ui[field].length - 1 ? "" : "disabled"} aria-label="لأسفل">↓</button>
        <button type="button" class="btn small danger" data-rm="${field}:${i}" aria-label="إزالة">✕</button></span></li>`).join("")}</ol>
      ${ui[field].length < max ? `<div class="row"><select data-add="${field}"><option value="">أضف شاشة…</option>${keys.filter((k) => !ui[field].includes(k)).map((k) => `<option value="${k}">${esc(SCREENS[k][0])}</option>`).join("")}</select></div>` : `<p class="muted">الحد الأقصى ${max}.</p>`}</section>`;
    app.innerHTML = `<section class="panel"><div class="row between"><div><a href="#/system">← البرنامج</a><h1>تخصيص الواجهة</h1></div>
        <button class="btn secondary" id="cuReset">رجوع للافتراضي</button></div>
      <p class="muted">هذه الإعدادات لهذه الصيدلية وتظهر لكل المستخدمين. كل شخص يرى فقط الشاشات المسموحة له.</p></section>
      ${pick("nav_main", 8, "الشريط العلوي", "الشاشات الظاهرة مباشرة في الشريط العلوي بالترتيب. الباقي تحت \"المزيد\".")}
      ${pick("home", 8, "أزرار الصفحة الرئيسية", "الأزرار السريعة في الصفحة الرئيسية بالترتيب.")}
      <section class="panel"><h2>الاختصارات</h2><p class="muted">اضغط Alt مع الرقم من أي مكان للانتقال إلى الشاشة. Alt+0 يفتح المساعدة.</p>
        <div class="formgrid">${"123456789".split("").map((n) => `<label class="f"><kbd>Alt+${n}</kbd><select data-sc="${n}"><option value="">—</option>
          ${keys.map((k) => `<option value="${k}" ${ui.shortcuts[n] === k ? "selected" : ""}>${esc(SCREENS[k][0])}</option>`).join("")}</select></label>`).join("")}</div></section>
      <section class="panel"><div class="row between"><h2>زراير شاشة البيع</h2><button class="btn small secondary" id="cuPkReset">رجوع للافتراضي</button></div>
        <p class="muted">كل زرار لحاجة واحدة بس. الأسهم وDelete وEsc وF8 (فتح الدرج) وF12 (الباركود) ثابتين.</p>
        <div class="formgrid">${Object.keys(POS_KEY_DEFAULTS).map((a) => `<label class="f">${esc(POS_KEY_NAMES[a])}<select data-pk="${a}">
          ${POS_KEY_CHOICES.map((k) => { const used = Object.keys(POS_KEY_DEFAULTS).find((b) => b !== a && ui.pos_keys[b] === k);
            return `<option value="${k}" ${ui.pos_keys[a] === k ? "selected" : ""}>${k}${used ? ` (${esc(POS_KEY_NAMES[used])})` : ""}</option>`; }).join("")}</select></label>`).join("")}</div></section>
      <section class="panel"><h2>الأعمدة</h2><p class="muted">تُضبط من زر "⚙ الأعمدة" فوق جدول فواتير البيع وجدول الأصناف.</p>
        ${Object.keys(ui.columns).some((t) => ui.columns[t].length) ? `<button class="btn small secondary" id="cuCols">إظهار كل الأعمدة</button>` : `<p class="muted">كل الأعمدة ظاهرة.</p>`}</section>`;
    const save = async (changes) => {
      try { ui = ME.ui = await api("/api/settings/ui", { method: "PUT", body: changes }); applyNav(); toast("✓ تم الحفظ"); draw(); }
      catch (er) { toast(er.message, true); }
    };
    const mv = (spec, d) => { const [f, i] = spec.split(":"), a = [...ui[f]], j = +i + d; [a[i], a[j]] = [a[j], a[i]]; save({ [f]: a }); };
    app.querySelectorAll("[data-up]").forEach((b) => b.addEventListener("click", () => mv(b.dataset.up, -1)));
    app.querySelectorAll("[data-dn]").forEach((b) => b.addEventListener("click", () => mv(b.dataset.dn, 1)));
    app.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => { const [f, i] = b.dataset.rm.split(":"); save({ [f]: ui[f].filter((_, j) => j !== +i) }); }));
    app.querySelectorAll("[data-add]").forEach((s) => s.addEventListener("change", () => { if (s.value) save({ [s.dataset.add]: [...ui[s.dataset.add], s.value] }); }));
    app.querySelectorAll("[data-sc]").forEach((s) => s.addEventListener("change", () => {
      const sc = { ...ui.shortcuts }; if (s.value) sc[s.dataset.sc] = s.value; else delete sc[s.dataset.sc]; save({ shortcuts: sc });
    }));
    app.querySelectorAll("[data-pk]").forEach((s) => s.addEventListener("change", () => {
      const a = s.dataset.pk, k = s.value, other = Object.keys(POS_KEY_DEFAULTS).find((b) => b !== a && ui.pos_keys[b] === k);
      if (other) { toast("الزرار ده متاخد لحاجة تانية — غيّرها هناك الأول", true); s.value = ui.pos_keys[a]; return; }
      save({ pos_keys: { [a]: k } });
    }));
    $("#cuPkReset")?.addEventListener("click", (e) => { if (confirmInline(e.target)) save({ pos_keys: {} }); });
    $("#cuCols")?.addEventListener("click", () => save({ columns: Object.fromEntries(Object.keys(ui.columns).map((t) => [t, []])) }));
    $("#cuReset").addEventListener("click", (e) => { if (confirmInline(e.target)) save({ reset: true }); });
  };
  draw();
}

window.addEventListener("hashchange", route);
window.addEventListener("hashchange", () => { const m = $("#navMore"); if (m) m.open = false; });
document.addEventListener("click", (e) => { const m = $("#navMore"); if (m?.open && !m.contains(e.target)) m.open = false; });
$("#fbBtn")?.addEventListener("click", (e) => { e.preventDefault(); openFeedback(); });
$("#logout").addEventListener("click", async () => {
  if (PREFS.logout_confirm !== "off") {
    const warn = [];
    if (SALE && SALE.status === "draft" && SALE.lines?.length) warn.push("فيه فاتورة بيع مفتوحة لسه ماخلصتش (هتفضل معلقة).");
    try { if (ME?.permissions?.includes("sales.read") && await api("/api/cash/current")) warn.push("الشيفت لسه مفتوح: اقفله لو هتمشي."); } catch { /* no open shift */ }
    if (!confirm(["تخرج من البرنامج؟", ...warn].join("\n"))) return;
  }
  staffUiStop(); STAFF_UI = null;
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  ME = null; location.hash = "#/login";
});

// ------------------------------------------------------------------ login
function viewSetup() {
  ME = null;                       // a fresh install: any old session in this tab is meaningless
  $("#topbar").hidden = true;
  app.innerHTML = `
  <form class="panel login" id="setupForm">
    <h1>أهلًا بك</h1>
    <p class="muted">هذه أول مرة يعمل فيها البرنامج على هذا الجهاز. أدخل هذه البيانات مرة واحدة فقط.</p>
    <label class="f">اسم الصيدلية <input name="pharmacy" required minlength="2" placeholder="مثال: صيدلية الشفاء"></label><br>
    <label class="f">اسمك <input name="full_name" placeholder="مثال: د. أحمد محمود"></label><br>
    <label class="f">اسم المستخدم (للدخول) <input name="username" required minlength="3" autocomplete="username" value="owner"></label><br>
    <label class="f">كلمة المرور (10 حروف أو أرقام على الأقل) <input name="password" type="password" required minlength="10" autocomplete="new-password"></label><br>
    <label class="f">أعد كتابة كلمة المرور <input name="password2" type="password" required minlength="10" autocomplete="new-password"></label><br>
    <label class="f">تقريب إجمالي الفاتورة (لأقرب) <select name="rounding" required><option value="">— اختار —</option>
      <option value="0">من غير تقريب</option><option value="25">ربع جنيه</option><option value="50">نص جنيه</option><option value="100">جنيه</option></select>
      <span class="muted">التقريب بيظهر سطر لوحده في الفاتورة، وتقدر تغيّره بعدين من الإعدادات.</span></label><br>
    <button class="btn" type="submit">ابدأ</button>
    <p class="muted">احفظ كلمة المرور في مكان آمن، فبدونها لن تستطيع الدخول.</p>
  </form>
  <form class="panel login" id="restoreForm">
    <h2>أو: هل لديك بيانات سابقة؟</h2>
    <p class="muted">إذا كان البرنامج يعمل من قبل (على هذا الجهاز أو جهاز آخر)، فاختر آخر نسخة احتياطية، وهي ملف يبدأ اسمه بـ pharmacy-backup. ستدخل بنفس اسم المستخدم وكلمة المرور القديمة.</p>
    <input type="file" name="file" accept=".zip" required><br>
    <button class="btn secondary" type="submit">استرجع بياناتي</button>
  </form>`;
  $("#restoreForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector("button"); btn.disabled = true; btn.textContent = "جارٍ فحص الملف…";
    try {
      await api("/api/setup/restore", { method: "POST", form: new FormData(e.target) });
      btn.textContent = "جارٍ الاسترجاع وإعادة التشغيل…";
      const wait = async () => { try { const r = await fetch("/api/setup"); if (r.ok && !(await r.json()).needed) { location.hash = "#/login"; location.reload(); return; } } catch { /* restarting */ } setTimeout(wait, 1500); };
      setTimeout(wait, 3000);
    } catch (err) { btn.disabled = false; btn.textContent = "استرجع بياناتي"; toast(err.message, true); }
  });
  $("#setupForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    if (f.get("password") !== f.get("password2")) { toast("كلمتا السر غير متطابقتين", true); return; }
    try {
      const r = await api("/api/setup", { method: "POST", body: Object.fromEntries(["pharmacy", "full_name", "username", "password", "rounding"].map((k) => [k, f.get(k)])) });
      ME = r.me; SETUP_CARRIED = r.carried || null; location.hash = "#/setup-printers";
      toast("البرنامج جاهز. الخطوة التالية: نقل بياناتك من البرنامج القديم");
    } catch (err) {
      if (err.code === "already_setup") { location.hash = "#/login"; return; }
      toast(err.message, true);
    }
  });
}

// U4-0 (c): right after the first-run setup, the receipt and label printers and a test label, before the data move.
// A clean start brought the old settings along (SETUP_CARRIED): then this only confirms them with a test label.
let SETUP_CARRIED = null;
async function viewSetupPrinters() {
  if (!ME) { location.hash = "#/login"; return; }
  const [c, pr] = await Promise.all([api("/api/settings/receipt"), api("/api/print/printers").catch(() => ({ printers: [] }))]);
  const carried = SETUP_CARRIED && SETUP_CARRIED.applied;
  const opts = pr.printers.map((n) => `<option value="win:${esc(n)}">`).join("");
  app.innerHTML = `<section class="panel login"><h1>الطابعات</h1>
    ${carried ? `<p class="badge b-ok">✓ اترجعت إعدادات الطباعة والأجهزة من البيانات القديمة. اطبع ملصق تجربة واتأكد إنه طالع على ملصق واحد.</p>`
      : `<p class="muted">اختار طابعة الإيصال وطابعة الملصقات، واطبع ملصق تجربة. تقدر تغيّرها بعدين من «البرنامج والنسخ الاحتياطي».</p>`}
    <form id="spForm">
      <datalist id="spList"><option value="browser" label="من المتصفح (بدون طباعة مباشرة)">${opts}</datalist>
      <label class="f">طابعة الإيصال والدرج <input name="receipt_target" list="spList" dir="ltr" value="${esc(c.receipt_target || "")}" placeholder="browser"></label>
      <label class="f">طابعة الملصقات (تعريف ويندوز) <input name="label_target" list="spList" dir="ltr" value="${esc(c.label_target || "")}" placeholder="win:Xprinter XP-365B"></label>
      <label class="f">عدد الملصقات في صفحة ويندوز (إذا كانت الصفحة تضم ملصقين فوق بعضهما فاختر 2) <select name="label_rows">${[1, 2, 3, 4].map((n) => `<option value="${n}" ${(c.label_rows || 1) === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      <div class="row"><button type="button" class="btn secondary" id="spSave">احفظ</button>
        <button type="button" class="btn secondary" id="spLabel">اطبع ملصق تجربة</button>
        <button type="button" class="btn secondary" id="spRcpt">اطبع إيصال تجربة</button></div>
    </form>
    ${carried ? "" : `<p class="muted">كان البرنامج شغال قبل كده؟ ممكن ترجع الإعدادات القديمة كلها من «البرنامج والنسخ الاحتياطي» ← «رجّع إعدادات الأجهزة والطباعة من نسخة قديمة».</p>`}
    <button type="button" class="btn" id="spNext">كمّل ← نقل البيانات</button></section>`;
  const f = $("#spForm");
  const vals = () => {
    const lt = f.label_target.value.trim();
    return { receipt_target: f.receipt_target.value.trim(), label_target: lt, label_rows: +f.label_rows.value,
             ...(lt.startsWith("win:") ? { label_lang: "driver" } : {}) };
  };
  $("#spSave").addEventListener("click", async () => {
    try { await api("/api/settings/receipt", { method: "PUT", body: vals() }); toast("✓ اتحفظت"); } catch (e) { toast(e.message, true); }
  });
  $("#spLabel").addEventListener("click", async () => {
    const v = vals();
    if (!v.label_target) { toast("اختار طابعة الملصقات الأول", true); return; }
    try { const r = await api("/api/print/label-test", { method: "POST", body: v }); toast(`أُرسل ${r.labels} ملصق تجربة — لو طلعوا سليمين اضغط «احفظ»`); }
    catch (e) { toast(e.message, true); }
  });
  $("#spRcpt").addEventListener("click", async () => {
    try { await api("/api/print/test", { method: "POST", body: { target: f.receipt_target.value.trim() } }); toast("تم الإرسال للطابعة"); }
    catch (e) { toast(e.message, true); }
  });
  $("#spNext").addEventListener("click", () => { location.hash = "#/import"; toast("الخطوة التالية: نقل بياناتك من البرنامج القديم"); });
}

async function viewLogin() {
  $("#topbar").hidden = true;
  let quick = [];
  try { quick = (await api("/api/auth/quick-users")).users; } catch { /* not on the pharmacy PC */ }
  app.innerHTML = `
  ${quick.length ? `<section class="panel login" id="quick">
    <h1>دخول سريع</h1>
    <div class="tiles">${quick.map((u) => `<button type="button" class="tile" data-u="${esc(u.id)}">${esc(u.full_name)}</button>`).join("")}</div>
    <form id="pinForm" hidden class="mt"><label class="f"><span id="pinWho"></span> — الرقم السري
      <input name="pin" type="password" inputmode="numeric" autocomplete="off" maxlength="6" required></label>
      <button class="btn mt" type="submit">دخول</button></form>
  </section>` : ""}
  <form class="panel login" id="loginForm">
    <h1>تسجيل الدخول</h1>
    <label class="f">اسم المستخدم <input name="username" autocomplete="username" required></label><br>
    <label class="f">كلمة المرور <input name="password" type="password" autocomplete="current-password" required></label><br>
    <button class="btn" type="submit">دخول</button>
  </form>`;
  let quickUser = null;
  document.querySelectorAll("[data-u]").forEach((b) => b.addEventListener("click", () => {
    quickUser = b.dataset.u;
    document.querySelectorAll("[data-u]").forEach((x) => x.classList.toggle("on", x === b));
    $("#pinForm").hidden = false; $("#pinWho").textContent = b.textContent;
    $("#pinForm").pin.value = ""; $("#pinForm").pin.focus();
  }));
  $("#pinForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/auth/pin", { method: "POST", body: { user_id: quickUser, pin: e.target.pin.value } });
      ME = r.me; location.hash = "#/pos";
    } catch (err) { e.target.pin.value = ""; toast(err.message, true); }
  });
  $("#loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const r = await api("/api/auth/login", { method: "POST",
        body: { username: f.get("username"), password: f.get("password") } });
      ME = r.me; location.hash = "#/home";
    } catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ purchases list
// update 1 (owner): a time the program recorded, in Cairo time, as "YYYY-MM-DD hh:mm" (12/24 h as the setting)
const cairoWhen = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Cairo", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
  const t = d.toLocaleTimeString(LOC(), { timeZone: "Africa/Cairo", hour: "2-digit", minute: "2-digit", hour12: H12() });
  return `${day} ${t}`;
};
let PU_SORT = { k: null, desc: true };               // the purchase list's column sort (this tab only)
const STATUS = { draft: ["مسودة", "b-warn"], approved: ["معتمدة", "b-ok"], cancelled: ["ملغاة", "b-err"] };

const PURCHASE_ENTRY_FIELDS = {
  qty: "الكمية", expiry: "الصلاحية", discount: "الخصم / سعر الصيدلي", bonus: "البونص",
  batch: "التشغيلة", public_price: "سعر الجمهور", tax_margin: "هامش الموزع / الضريبة",
};
const PURCHASE_ENTRY_LIMITS = {
  qty_multiple: ["مضاعف الكمية المعتادة", 100],
  qty_min: ["أقل كمية للمراجعة (علبة)", 1000000],
  public_delta_pct: ["فرق السعر عن سعر الجمهور %", 1000],
  last_cost_delta_pct: ["فرق السعر عن آخر تكلفة %", 1000],
};

function purchaseEntrySettingsHtml(setting) {
  return `<details class="panel"><summary>إعدادات إدخال الشراء</summary>
    <form id="puEntrySettings">
      <p>الصنف أول خانة. حرّك باقي الخانات لتغيير ترتيب الإدخال بزر Enter.</p>
      <ol id="puEntryOrder">${setting.field_order.map((field) => `<li data-order-field="${field}">
        <span id="puEntryField-${field}">${PURCHASE_ENTRY_FIELDS[field]}</span>
        <button type="button" class="btn small secondary" data-move="-1" aria-label="قدّم" aria-describedby="puEntryField-${field}">↑</button>
        <button type="button" class="btn small secondary" data-move="1" aria-label="أخّر" aria-describedby="puEntryField-${field}">↓</button>
      </li>`).join("")}</ol>
      <div class="row">${Object.entries(PURCHASE_ENTRY_LIMITS).map(([key, [label, max]]) => `<label class="f">${label}
        <input name="${key}" type="number" min="1" max="${max}" step="1" required autocomplete="off" value="${esc(setting[key])}">
        </label>`).join("")}
        <button class="btn" type="submit">حفظ إعدادات الشراء</button></div>
    </form></details>`;
}

function bindPurchaseEntrySettings() {
  const form = $("#puEntrySettings");
  const list = $("#puEntryOrder", form);
  const updateButtons = () => {
    for (const row of list.children) {
      $('[data-move="-1"]', row).disabled = !row.previousElementSibling;
      $('[data-move="1"]', row).disabled = !row.nextElementSibling;
    }
  };
  updateButtons();
  list.addEventListener("click", (e) => {
    const button = e.target.closest("[data-move]");
    if (!button) return;
    const row = button.closest("li");
    if (button.dataset.move === "-1") row.previousElementSibling?.before(row);
    else row.nextElementSibling?.after(row);
    updateButtons();
    (button.disabled ? $('button:not(:disabled)', row) : button)?.focus();
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    withBusy($('button[type="submit"]', form), async () => {
      const changes = { field_order: Array.from(list.children, row => row.dataset.orderField) };
      for (const key of Object.keys(PURCHASE_ENTRY_LIMITS)) changes[key] = form.elements[key].valueAsNumber;
      try {
        await api("/api/settings/purchase-entry", { method: "PUT", body: changes });
        toast("تم حفظ إعدادات الشراء");
      } catch (err) { toast(err.message, true); }
    });
  });
}

async function viewPurchases() {
  const canSetPriceDefault = ME.permissions.includes("settings.manage");
  const [list, suppliers, priceSetting, entrySetting] = await Promise.all([api("/api/purchases"), api("/api/suppliers"),
    canSetPriceDefault ? api("/api/settings/purchase-price") : null,
    canSetPriceDefault ? api("/api/settings/purchase-entry") : null]);
  app.innerHTML = `
  <section class="panel">
    <h1>إدخال فاتورة شراء</h1>
    <form id="upForm" class="row">
      <label class="f">المورد (اختياري — يُتعرَّف عليه من الفاتورة)
        <select name="supplier_id"><option value="">— تلقائي —</option>
          ${suppliers.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}
        </select></label>
      <label class="f">ملف الفاتورة (Excel / CSV / PDF / صورة)
        <input type="file" name="file" required accept=".xlsx,.csv,.pdf,image/*" capture="environment"></label>
      <button class="btn" type="submit">قراءة الفاتورة</button>
    </form>
    <p class="muted">لا يتم تعديل المخزون إلا بعد مراجعة الفاتورة واعتمادها.</p>
    ${priceSetting ? `<label class="f">الاختيار الافتراضي عند تغيّر سعر الجمهور<select id="puPriceDefault">
      ${purchasePriceOptions(priceSetting.default)}</select></label>` : ""}
  </section>
  <section class="panel">
    <h2>أو: فاتورة يدوية</h2>
    <form id="manForm" class="row">
      <label class="f">المورد<select name="supplier_id" required><option value="">— اختر —</option>
        ${suppliers.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select></label>
      <label class="f">رقم فاتورة المورد<input name="invoice_no" autocomplete="off" required></label>
      <label class="f">تاريخ فاتورة المورد<input name="invoice_date" type="date" value="${localDay()}"></label>
      <button class="btn" type="submit">ابدأ الفاتورة</button>
    </form>
    <p class="muted">${suppliers.length ? "المورد غير موجود في القائمة؟" : "لا يوجد موردون مسجلون بعد."} اكتب اسمه:
      <input id="newSup" placeholder="اسم المورد"> <button class="btn small secondary" id="addSup" type="button">إضافة مورد</button></p>
  </section>
  ${entrySetting ? purchaseEntrySettingsHtml(entrySetting) : ""}
  <section class="panel" id="puRep">
    <div class="row between"><h2>الفواتير</h2>${reportTools("pur")}</div>
    <div class="row noprint">${rangeBar("puRange")}
      <label class="f">بحث <input id="puQ" autocomplete="off" placeholder="مورد، رقم، مسلسل أو تاريخ"></label>
      <label class="f">المورد <select id="puSup"><option value="">الكل</option>${suppliers.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</select></label>
      <label class="f">الحالة <select id="puSt"><option value="">الكل</option>${Object.entries(STATUS).map(([k, [t]]) => `<option value="${k}">${t}</option>`).join("")}</select></label></div>
    <div id="puBox"></div>
  </section>`;
  let savedPriceDefault = priceSetting?.default;
  $("#puPriceDefault")?.addEventListener("change", async (e) => {
    const select = e.currentTarget;
    select.disabled = true;
    try {
      savedPriceDefault = (await api("/api/settings/purchase-price", { method: "PUT", body: { default: select.value } })).default;
      toast("تم حفظ الاختيار الافتراضي");
    } catch (err) { select.value = savedPriceDefault; toast(err.message, true); }
    finally { select.disabled = false; }
  });
  if (entrySetting) bindPurchaseEntrySettings();
  let rows = list, sub = "";
  const puSorted = (xs) => {
    const k = PU_SORT.k; if (!k) return xs;
    const val = (i) => (k === "total" ? amt(i) ?? 0 : k === "serial_no" ? i.serial_no ?? 0 : String(i[k] ?? ""));
    return [...xs].sort((a, b) => { const x = val(a), y = val(b); const c = x < y ? -1 : x > y ? 1 : 0; return PU_SORT.desc ? -c : c; });
  };
  // an approved invoice counts with what was posted to the supplier (computed); a draft shows the paper total
  const amt = (i) => (i.status === "approved" ? i.computed_total ?? i.declared_total : i.declared_total ?? i.computed_total);
  const purchaseQuery = () => ($("#puQ")?.value || "").trim().toLowerCase();
  const visiblePurchases = () => {
    const q = purchaseQuery();
    return puSorted(rows.filter((i) => !q || [i.serial_no, i.supplier_name, i.invoice_no, i.invoice_date, cairoWhen(i.created_at)]
      .some((v) => String(v ?? "").toLowerCase().includes(q))));
  };
  const drawList = () => {
    const shown = visiblePurchases();
    const ok = shown.filter((i) => i.status === "approved");
    const th = (k, t) => `<th class="clickable" data-sk="${k}" title="رتّب">${t}${PU_SORT.k === k ? (PU_SORT.desc ? " ▼" : " ▲") : ""}</th>`;
    $("#puBox").innerHTML = `<div class="tablewrap"><table class="list">
      <thead><tr>${th("serial_no", "مسلسل")}${th("supplier_name", "المورد")}${th("invoice_no", "رقم فاتورة المورد")}${th("invoice_date", "تاريخ فاتورة المورد")}${th("created_at", "تاريخ الإدخال على السيستم")}<th>الأصناف</th>
        ${th("total", "الإجمالي")}<th>الحالة</th><th>المصدر</th></tr></thead>
      <tbody>${shown.map((i) => `<tr><td class="num">${esc(i.serial_no ?? "")}</td>
        <td><a href="#/purchases/${esc(i.id)}">${esc(i.supplier_name || "— غير محدد —")}</a></td>
        <td>${esc(i.invoice_no || "")}</td><td>${esc(i.invoice_date || "")}</td><td>${esc(cairoWhen(i.created_at))}</td><td>${i.lines}</td>
        <td>${money(amt(i))}</td>
        <td><span class="badge ${STATUS[i.status][1]}">${STATUS[i.status][0]}</span></td>
        <td>${esc(i.source_type)}</td></tr>`).join("") ||
        `<tr><td colspan="9" class="muted">لا توجد فواتير بعد</td></tr>`}</tbody>
    </table></div>${ok.length ? `<p><b>المعتمد: ${ok.length} فاتورة — ${money(ok.reduce((a, i) => a + (amt(i) || 0), 0))} ج</b></p>` : ""}`;
    $("#puBox").querySelectorAll("[data-sk]").forEach((h) => h.addEventListener("click", () => {
      PU_SORT = { k: h.dataset.sk, desc: PU_SORT.k === h.dataset.sk ? !PU_SORT.desc : true }; drawList();
    }));
    // U4: E-Stock's purchases of the same period under ours; only with all / approved shown, no text search, and a
    // full (uncapped) program list is added up
    if (asked && !purchaseQuery() && (!asked.st || asked.st === "approved")) {
      estockPurchasesBelow($("#puBox"), asked.f, asked.t, asked.sup, rows.length >= 5000 ? null : ok.reduce((a, i) => a + (amt(i) || 0), 0)).catch(() => {});
    } else { ES_PSEQ++; document.querySelector("#esPur")?.remove(); }
  };
  let asked = null, lseq = 0;                          // the request the list answers (not the live controls)
  const loadList = async (f, t) => {
    const qs = new URLSearchParams({ from: f, to: t }); if ($("#puSup").value) qs.set("supplier", $("#puSup").value); if ($("#puSt").value) qs.set("status", $("#puSt").value);
    const a = { f, t, sup: $("#puSup").value, st: $("#puSt").value }, n = ++lseq;
    const got = await api(`/api/purchases?${qs}`);
    if (n !== lseq) return;                            // a late answer never replaces a newer choice (review P2)
    rows = got; sub = rangeText(f, t); asked = a; drawList();
  };
  const getRange = wireRange("puRange", (f, t) => loadList(f, t).catch((e) => toast(e.message, true)));
  ["#puSup", "#puSt"].forEach((id) => $(id).addEventListener("change", () => loadList(...getRange()).catch((e) => toast(e.message, true))));
  wireTools("pur", () => ({ title: "فواتير الشراء", subtitle: sub + ($("#puSup").value ? ` — ${$("#puSup").selectedOptions[0].text}` : ""), el: $("#puRep"), sheets: [
    { name: "فواتير الشراء", columns: [N("مسلسل"), Tx("المورد"), Tx("رقم فاتورة المورد"), Dt("تاريخ فاتورة المورد"), Tx("تاريخ الإدخال على السيستم"), N("الأصناف"), M("الإجمالي"), Tx("الحالة")],
      rows: visiblePurchases().map((i) => [i.serial_no ?? "", i.supplier_name || "", i.invoice_no || "", i.invoice_date || "", cairoWhen(i.created_at), i.lines, amt(i), STATUS[i.status][0]]) }] }));
  $("#puQ").addEventListener("input", drawList);
  $("#addSup").addEventListener("click", async () => {
    const name = $("#newSup").value.trim(); if (!name) return;
    try { await api("/api/suppliers", { method: "POST", body: { name } }); toast("تمت إضافة المورد"); viewPurchases(); }
    catch (err) { toast(err.message, true); }
  });
  $("#manForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const d = await api("/api/purchases", { method: "POST", body: { supplier_id: f.get("supplier_id"),
        invoice_no: f.get("invoice_no"), invoice_date: f.get("invoice_date") || null } });
      location.hash = `#/purchases/${d.invoice.id}`;
    } catch (err) { toast(err.message, true); }
  });
  $("#upForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector("button");
    btn.disabled = true; btn.textContent = "جارٍ القراءة…";
    try {
      const d = await api("/api/purchases/import", { method: "POST", form: new FormData(e.target) });
      location.hash = `#/purchases/${d.invoice.id}`;
    } catch (err) {
      toast(err.message, true);
      if (err.details?.invoice_id) location.hash = `#/purchases/${err.details.invoice_id}`;
    } finally { btn.disabled = false; btn.textContent = "قراءة الفاتورة"; }
  });
}

// ------------------------------------------------------------------ invoice review
let CUR = null;

const FIELD_AR = { raw_name: "اسم الصنف", raw_code: "الكود", raw_barcode: "الباركود", qty: "الكمية",
  bonus_qty: "البونص", public_price: "سعر الجمهور", discount_bp: "الخصم", discount_pct: "الخصم",
  unit_net_price: "الصافي", tax_amount: "الضريبة", line_total: "الإجمالي", batch_no: "التشغيلة",
  expiry_date: "الصلاحية", distributor_margin: "هامش الموزع" };
const SRC_LABEL = { read: "مقروء", inferred: "محسوب", ai: "قراءة آلية", user: "أدخله المستخدم", db: "من قاعدة البيانات" };

function fieldInput(line, field, value, { type = "number", step = "0.01", describedBy } = {}) {
  const src = line.field_sources[field];
  const conf = line.field_confidence[field];
  const low = src !== "user" && conf !== undefined && conf < 0.8;
  const cls = [low ? "low" : "", src === "inferred" ? "inferred" : "", src === "ai" ? "ai" : ""].join(" ");
  const title = src ? `${SRC_LABEL[src] || src}${conf !== undefined ? ` — ثقة ${Math.round(conf * 100)}%` : ""}` : "";
  const dis = CUR.invoice.status !== "draft" ? "disabled" : "";
  return `<input class="${cls}" data-line="${esc(line.id)}" data-field="${field}" type="${type}"
    ${type === "number" ? `step="${step}" min="0"` : ""} value="${esc(value)}" title="${esc(title)}"
    autocomplete="off" aria-label="${esc(FIELD_AR[field] || field)}" ${describedBy ? `aria-describedby="${esc(describedBy)}"` : ""} ${dis}>
    ${low && !dis ? `<button class="btn small secondary" data-confirm="${esc(line.id)}" data-cfield="${field}">تأكيد</button>` : ""}`;
}

function itemLabel(it) {
  if (!it) return "";
  return [it.name_en || it.name_ar, it.strength, it.dosage_form].filter(Boolean).join(" · ");
}

function purchasePriceWarning(current, value) {
  const typed = toPiasters(value);
  return current == null || !Number.isSafeInteger(typed) || typed < 0 || typed === current
    ? "" : `سعر الجمهور اتغير من ${money(current)} لـ ${money(typed)}`;
}

function showPurchasePriceWarning(output, current, value) {
  const message = purchasePriceWarning(current, value);
  output.hidden = !message;
  output.textContent = message;
}

const purchaseCardKey = () => {
  const key = posKey("item_card");
  return /^F\d+$/.test(key) ? key : "F2";  // Ctrl+Enter already adds a purchase line
};
const invoicePack = (it) => `${it.pack_unit_name === "pack" ? "علبة" : it.pack_unit_name} · ${it.pack_size} ${unitName(it)}`;
const purchaseItemInfo = (it) => it ? [it.code, invoicePack(it)].filter(Boolean).join(" · ") : "";
function invoiceCardButton(attribute, value, key) {
  return `<button type="button" class="btn small secondary" ${attribute}="${esc(value)}"
    aria-keyshortcuts="${esc(key)}" title="كارت الصنف (${esc(key)})">كارت الصنف</button>`;
}

function lineIssues(lineId) { return CUR.issues.filter((i) => i.line_id === lineId); }

function renderInvoice(keepPicker = true) {
  const { invoice: inv, lines, issues } = CUR;
  const draft = inv.status === "draft";
  const picker = $("#qa");
  const keep = draft && keepPicker && picker?.dataset.invoice === inv.id
    && picker.dataset.order === CUR.entry_order.join(",");
  const errs = issues.filter((i) => i.level === "error" && !i.acknowledged);
  const warns = issues.filter((i) => (i.level === "warning" || i.level === "info") && (!draft || i.code !== "public_price_changed"));
  const lineNo = Object.fromEntries(lines.map((l) => [l.id, l.line_no]));
  const fileUrl = inv.source_file_id ? `/api/files/${encodeURIComponent(inv.source_file_id)}` : null;
  let doc = `<p class="muted">لا يوجد ملف مرفق (إدخال يدوي)</p>`;
  if (fileUrl && inv.source_type === "image") doc = `<img src="${fileUrl}" alt="صورة الفاتورة">`;
  else if (fileUrl && inv.source_type === "pdf") doc = `<iframe src="${fileUrl}" title="ملف الفاتورة"></iframe>`;
  else if (fileUrl) doc = `<p>ملف ${esc(inv.source_type)} — <a href="${fileUrl}">تحميل الملف الأصلي</a></p>`;

  const diff = inv.declared_total != null && inv.computed_total != null ? inv.declared_total - inv.computed_total : null;
  const totalErr = issues.some((i) => i.code === "total_mismatch" && !i.acknowledged);
  app.innerHTML = `
  <nav class="tabs" id="pTabs" aria-label="فواتير الشراء المفتوحة"></nav>
  <section class="panel">
    <div class="row between">
      <h1>فاتورة شراء ${inv.serial_no ? `<span class="muted" title="المسلسل في البرنامج">مسلسل ${esc(inv.serial_no)}</span> ` : ""}<span class="badge ${STATUS[inv.status][1]}">${STATUS[inv.status][0]}</span>
        ${inv.extraction_method === "ai_vision" ? `<span class="badge b-ai">قراءة آلية — راجع كل القيم</span>` : ""}</h1>
      <a href="#/purchases">رجوع للفواتير</a>
    </div>
    <div class="row">
      <label class="f">المورد <select data-h="supplier_id" ${draft ? "" : "disabled"}>
        <option value="">— اختر —</option></select></label>
      <label class="f">نوع المورد <span class="seg2 small" id="supType"></span></label>
      <label class="f">رقم فاتورة المورد <input data-h="invoice_no" autocomplete="off" value="${esc(inv.invoice_no || "")}" ${draft ? "" : "disabled"}></label>
      <label class="f">تاريخ فاتورة المورد <input data-h="invoice_date" autocomplete="off" dir="ltr"
        placeholder="YYYY-MM-DD" value="${esc(inv.invoice_date || "")}" ${draft ? "" : "disabled"}></label>
      ${draft ? '<input type="date" id="puDatePicker" hidden><button class="btn small secondary" type="button" id="puDateOpen">التقويم</button>' : ""}
      <label class="f">تاريخ الإدخال على السيستم <input id="pinEntered" value="${esc(cairoWhen(inv.created_at))}" readonly disabled></label>
      ${inv.approved_at ? `<label class="f">تاريخ الاعتماد <input id="pinApproved" value="${esc(cairoWhen(inv.approved_at))}" readonly disabled></label>` : ""}
      <label class="f">إجمالي فاتورة المورد (من الورقة) (ج) <input data-h="declared_total" autocomplete="off" type="number" step="0.01" min="0" value="${money(inv.declared_total)}" ${draft ? "" : "disabled"}>
        <small class="muted">اكتب الإجمالي المكتوب في آخر ورقة المورد، والبرنامج يقارنه بمجموع الأصناف</small>
        ${diff == null ? "" : diff === 0 ? `<small class="badge b-ok">✓ مطابق بالقرش</small>` : `<small class="badge ${totalErr ? "b-err" : "b-warn"}">الفرق ${Math.abs(diff)} قرش (${money(diff)} ج)</small>`}</label>
      <label class="f">خصم إضافي على الفاتورة (ج) <input data-h="extra_discount" autocomplete="off" type="number" step="0.01" value="${money(inv.extra_discount)}" ${draft ? "" : "disabled"}></label>
      <label class="f">طريقة الدفع <select data-h="pay_source" id="paySrc" ${draft ? "" : "disabled"}></select>
        <small class="muted">${inv.pay?.default ? "الافتراضي حسب نوع المورد" : ""}</small></label>
      ${inv.pay && inv.pay.source !== "credit" ? `<label class="f" title="فاضي = الفاتورة كلها؛ الباقي يفضل على حساب المورد">المدفوع دلوقتي (ج)<input data-h="pay_amount" autocomplete="off" type="number" min="0" step="0.01" class="narrow" value="${inv.pay_amount == null ? "" : money(inv.pay_amount)}" placeholder="كلها" ${draft ? "" : "disabled"}></label>
        <label class="f">مرجع الدفع (رقم التحويل)<input data-h="pay_ref" autocomplete="off" value="${esc(inv.pay_ref || "")}" ${draft ? "" : "disabled"}></label>` : ""}
    </div>
    ${inv.supplier_name_raw && !inv.supplier_id ? `<p class="muted">اسم المورد في الملف: ${esc(inv.supplier_name_raw)}</p>` : ""}
  </section>
  <section class="panel">
    <div class="totals">
      <div>إجمالي فاتورة المورد<b>${money(inv.declared_total) || "—"}</b></div>
      <div>الإجمالي المحسوب<b>${money(inv.computed_total) || "—"}</b></div>
      <div class="${diff == null ? "" : diff === 0 ? "t-ok" : totalErr ? "t-err" : "t-warn"}">${diff == null ? "الفرق" : diff === 0 ? "✓ الإجماليان متطابقان" : totalErr ? "✕ الفرق" : "⚠ الفرق (مقبول)"}<b>${diff == null ? "—" : money(diff)}</b>
        ${totalErr ? `<span>سطر زائد أو سطر ناقص؟ راجع قبل الاعتماد</span>` : ""}</div>
      ${lines.some((l) => l.distributor_margin) ? `<div title="مجموع (الكمية × هامش الموزع)، بيتزوّد على مجموع الأسطر زي آخر ورقة الموزع">هامش الموزع<b>${money(lines.reduce((a, l) => a + (l.qty || 0) * (l.distributor_margin || 0), 0))}</b></div>` : ""}
      <div>عدد الأصناف<b>${lines.length}</b></div>
    </div>
    <h2 class="mt">${errs.length ? `⛔ ${errs.length} مشكلة يجب حلها قبل الاعتماد` : "✅ لا توجد مشكلات تمنع الاعتماد"}</h2>
    <ul class="issues">
      ${errs.map((i) => `<li class="${i.code.startsWith("unusual_") ? "warn" : "err"}">${i.line_id ? `سطر ${lineNo[i.line_id]}: ` : ""}${esc(i.message)}
        ${i.details?.difference != null ? ` (الفرق ${money(i.details.difference)} ج)` : ""}
        ${i.details?.expected != null ? ` (المتوقع ${money(i.details.expected)})` : ""}
        ${i.code === "low_confidence" && draft ? ` [${esc(FIELD_AR[i.field] || i.field)}] <button class="btn small secondary" data-confirm="${esc(i.line_id)}" data-cfield="${esc(i.field)}">القيمة صحيحة</button>` : ""}
        ${i.code === "unreadable" ? ` [${esc(FIELD_AR[i.field] || i.field)}]` : ""}
        ${(["total_mismatch", "line_total_mismatch", "net_price_mismatch", "short_expiry", "slow_before_expiry"].includes(i.code) || i.code.startsWith("unusual_")) && draft && ME.permissions.includes("purchases.approve")
          ? `<button class="btn small secondary" data-ack="${esc(i.code)}" data-ackline="${esc(i.line_id || "")}">${i.code.startsWith("unusual_") ? "تأكيد القيمة مع ذكر السبب" : "قبول الفرق مع ذكر السبب"}</button>` : ""}</li>`).join("")}
      ${warns.map((i) => `<li class="${i.level === "info" ? "muted" : "warn"}">${i.line_id ? `سطر ${lineNo[i.line_id]}: ` : ""}${esc(i.message)}${i.details?.other_line ? ` (سطر ${lineNo[i.details.other_line]})` : ""}
        ${i.details?.mergeable && draft ? `<button class="btn small secondary" data-merge="${esc(i.details.other_line)}" data-mergewith="${esc(i.line_id)}">دمج السطرين</button>` : ""}
        ${i.code === "public_price_changed" ? ` (${money(i.details.old)} ← ${money(i.details.new)})` : ""}
</li>`).join("")}
    </ul>
    <div class="row">
      ${draft ? `<button class="btn" id="approve" ${CUR.can_approve && (ME.permissions.includes("purchases.approve") || ME.permissions.includes("purchases.edit")) ? "" : "disabled"}>اعتماد الفاتورة وإضافتها للمخزون</button>
        ${fileUrl ? `<button class="btn secondary" id="addLine">إضافة سطر ناقص من الملف</button>` : ""}` : ""}
      ${inv.status === "approved" ? `<button class="btn secondary" id="invLabels">اطبع ملصقات الفاتورة</button>` : ""}
      ${inv.status !== "cancelled" ? `<button class="btn danger" id="cancelInv">إلغاء الفاتورة</button>` : ""}
    </div>
  </section>
  ${draft ? (keep ? `<div id="qaSlot"></div>` : quickAddHtml()) : ""}
  <div class="${fileUrl ? "review" : ""}">
    ${fileUrl ? `<section class="panel doc ${["image","pdf"].includes(inv.source_type) ? "" : "compact"}">${doc}</section>` : ""}
    <section class="panel">
      <div class="legend"><span>الحقول المنقطة: قيمة غير مؤكدة تحتاج إلى مراجعة</span>
        <span>المائل: قيمة محسوبة</span><span>مرّر المؤشر على أي خانة لمعرفة مصدرها</span></div>
      <div class="tablewrap"><table class="lines">
        <thead><tr><th>#</th><th>الصنف</th><th>كمية</th><th>الرصيد</th><th data-purchase-bonus ${inv.supplier_type === "warehouse" ? "hidden" : ""}>بونص</th><th>سعر الجمهور</th><th>خصم %</th>
          <th title="هامش ثابت للعلبة زي «4+4» في ورقة الموزع: بيتشال مرتين من سعر الجمهور قبل الخصم ويتزوّد مرة بعده">هامش الموزع</th><th>الصافي</th><th>ضريبة السطر</th><th>الإجمالي</th><th>التشغيلة</th><th>الصلاحية</th><th></th></tr></thead>
        <tbody>${lines.map(renderLine).join("")}</tbody>
      </table></div>
    </section>
  </div>`;
  fillSuppliers(inv.supplier_id, draft);
  fillPaySources(inv);
  const invoiceDate = $('[data-h="invoice_date"]'), calendar = $("#puDatePicker");
  if (calendar) {
    $("#puDateOpen").addEventListener("click", () => {
      calendar.value = invoiceDate.value;
      calendar.showPicker();
    });
    calendar.addEventListener("change", () => {
      invoiceDate.value = calendar.value;
      invoiceDate.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }
  if (keep) $("#qaSlot").replaceWith(picker);
  else if (draft) bindQuickAdd();
  updatePurchaseBonus(inv.supplier_type);
  if (keep) picker.dispatchEvent(new Event("invoice-lines-changed"));
  purchaseTabs(inv.id);
}

// U2-5: labels after approval (or from any approved invoice). Nothing ticked except the items labelled the last
// time they were bought (remembered on this PC); counts editable; Enter never prints; one confirm with the totals.
const LBL_KEY = "lblItems";
const lblRemembered = () => { try { return new Set(JSON.parse(localStorage.getItem(LBL_KEY) || "[]")); } catch { return new Set(); } };
function purchaseLabels(cur) {
  const seen = new Set(), rows = [];
  for (const l of cur.lines || []) {
    if (!l.item_id || seen.has(l.item_id)) continue;
    seen.add(l.item_id);
    const boxes = (cur.lines || []).filter((x) => x.item_id === l.item_id).reduce((n, x) => n + (x.qty || 0) + (x.bonus_qty || 0), 0);
    rows.push({ id: l.item_id, name: l.item && typeof l.item === "object" ? itemText(l.item) : (l.item || l.raw_name), boxes });
  }
  if (!rows.length) return;
  const mem = lblRemembered(), d = document.createElement("dialog");
  d.innerHTML = `<form method="dialog" class="formgrid"><h2>ملصقات الفاتورة</h2>
    <p class="muted">متعلّم بس الأصناف اللي اتعملها ملصقات آخر مرة اتشرت.</p>
    <div class="row"><button type="button" class="btn small secondary" data-all="1">علّم الكل</button><button type="button" class="btn small secondary" data-all="0">شيل الكل</button></div>
    <table><tbody>${rows.map((r, i) => `<tr><td><label class="chk"><input type="checkbox" data-i="${i}" ${mem.has(r.id) ? "checked" : ""}> ${esc(r.name || "")}</label></td>
      <td><input type="number" min="1" max="500" step="1" class="narrow" data-n="${i}" value="${r.boxes || 1}"></td></tr>`).join("")}</tbody></table>
    <div class="row"><button type="button" class="btn" data-go="1">اطبع</button><button type="button" class="btn secondary" data-go="0">مش دلوقتي</button></div></form>`;
  document.body.append(d);
  d.querySelector("form").addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
  d.addEventListener("close", () => d.remove());
  d.addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.all) { d.querySelectorAll("[data-i]").forEach((c) => { c.checked = b.dataset.all === "1"; }); return; }
    if (b.dataset.go === "0") { d.close(); return; }
    if (b.dataset.go !== "1") return;
    const pick = [...d.querySelectorAll("[data-i]")].filter((c) => c.checked)
      .map((c) => ({ id: rows[+c.dataset.i].id, count: Math.max(1, Math.min(500, parseInt(d.querySelector(`[data-n="${c.dataset.i}"]`).value, 10) || 1)) }));
    if (!pick.length) { toast("علّم صنف واحد على الأقل", true); return; }
    const n = pick.reduce((a, x) => a + x.count, 0);
    // the label printer takes 500 labels a job: say so here, the dialog stays open (Codex P2 on #406)
    if (n > 500) { toast(`أقصى عدد في المرة 500 ملصق؛ المختار ${n}. قلّل العدد أو اطبع على دفعات`, true); return; }
    if (!confirm(`هتطبع ${n} ملصق لـ ${pick.length} صنف؟`)) return;
    const keep = lblRemembered(); rows.forEach((r2) => keep.delete(r2.id)); pick.forEach((x) => keep.add(x.id));
    try { localStorage.setItem(LBL_KEY, JSON.stringify([...keep].slice(-5000))); } catch { /* this PC only */ }
    d.close();
    // the program's own label path: label printer, or the browser / A4 page (Codex P2 on #406)
    await printLabels(pick.map((x) => [x.id, x.count]));
  });
  d.showModal();
}

// The picker displays the same order that Enter and Tab use.
function quickAddHtml() {
  const warehouse = CUR.invoice.supplier_type === "warehouse";
  const fields = {
    qty: `<label class="f">كمية (علبة)<input id="qaQty" autocomplete="off" type="number" min="1" step="1" value="1" class="narrow"></label>`,
    expiry: `<label class="f">الصلاحية (شهر/سنة)<input id="qaExp" autocomplete="off" placeholder="1128" class="narrow"><small class="muted" id="qaExpSee"></small></label>`,
    discount: `<label class="f">خصم %<input id="qaDisc" autocomplete="off" type="number" min="0" max="100" step="0.01" class="narrow"></label>
      <label class="f">سعر الصيدلي<input id="qaNet" autocomplete="off" type="number" min="0.01" step="0.01" class="narrow"></label>`,
    bonus: `<label class="f" id="qaBonusField" ${warehouse ? "hidden" : ""}>بونص<input id="qaBonus" ${warehouse ? "disabled" : ""} autocomplete="off" type="number" min="0" step="1" value="0" class="narrow"></label>`,
    batch: `<label class="f">التشغيلة<input id="qaBatch" autocomplete="off" class="narrow"></label>`,
    public_price: `<label class="f">سعر الجمهور<input id="qaPrice" autocomplete="off" type="number" min="0.01" step="0.01" class="narrow" aria-describedby="qaPriceWarning">
      <small class="badge b-warn" id="qaPriceWarning" role="status" hidden></small></label>`,
    tax_margin: `<label class="f" title="الرقم اللي في ورقة الموزع زي «4+4» (اكتب 4)">هامش الموزع للعلبة<input id="qaMargin" autocomplete="off" type="number" min="0" step="0.01" class="narrow"></label>
      <label class="f">ضريبة السطر<input id="qaTax" autocomplete="off" type="number" min="0" step="0.01" class="narrow"></label>
      <label class="f">معاملة الضريبة<select id="qaTaxMode"><option value="0">مضافة على الصافي</option><option value="1">مشمولة في الصافي</option></select></label>`,
  };
  const order = CUR.entry_order;
  return `<section class="panel" id="qa" data-invoice="${esc(CUR.invoice.id)}" data-order="${esc(order.join(","))}"><h2>إضافة صنف للفاتورة</h2>
    <div class="row">
      <label class="f grow">الصنف (اسم أو كود أو باركود)<input id="qaQ" autocomplete="off" placeholder="اكتب الكود أو امسح الباركود"></label>
      ${invoiceCardButton("data-qa-card", "picked", purchaseCardKey())}
      ${order.map(key => `<div class="row" data-entry-field="${key}" ${key === "bonus" && warehouse ? "hidden" : ""}>${fields[key]}</div>`).join("")}
      <button class="btn" id="qaAdd" type="button">إضافة</button>
    </div>
    <div id="qaRes" class="cands"></div><div id="qaPicked" class="muted"></div></section>`;
}

function bindQuickAdd() {
  const q = $("#qaQ");
  if (!q) return;
  const picker = $("#qa"), price = $("#qaPrice");
  let item = null, entryItemId;
  const warnPrice = () => showPurchasePriceWarning($("#qaPriceWarning", picker), item?.public_price, price.value);
  const showPicked = (it) => {
    $("#qaPicked").textContent = `العبوة ${it.pack_size} ${unitName(it)} · الرصيد الحالي ${fmtStock(it.stock_qty, it) || "0"}`;
    // U2-3: said at once, not after saving: the item is already on the invoice
    const twins = (CUR?.lines || []).filter((l) => l.item_id === it.id);
    if (twins.length) $("#qaPicked").textContent += ` · ⚠ الصنف ده موجود في ${twins.map((l) => `سطر ${l.line_no}${l.expiry_date ? ` (صلاحية ${l.expiry_date.slice(5, 7)}/${l.expiry_date.slice(0, 4)}${l.batch_no ? `، تشغيلة ${l.batch_no}` : "، من غير تشغيلة"})` : ""}`).join("، ")}: لو نفس الصلاحية والتشغيلة زوّد الكمية هناك`;
    warnPrice();
  };
  const pick = (it) => {
    if (entryItemId && entryItemId !== it.id) {
      for (const [id, value] of Object.entries({
        qaQty: "1", qaExp: "", qaNet: "", qaBonus: "0", qaBatch: "", qaMargin: "", qaTax: "", qaTaxMode: "0",
      })) $(`#${id}`, picker).value = value;
      $("#qaExpSee", picker).textContent = "";
    }
    entryItemId = it.id;
    item = it;
    $("#qaRes").innerHTML = "";
    q.value = itemText(it);
    price.value = money(it.public_price);
    showPicked(it);
    entryFields()[0]?.focus();
  };
  const entryFields = () => [...picker.querySelectorAll("[data-entry-field] :is(input, select):enabled")];
  picker.addEventListener("invoice-lines-changed", () => { if (item) showPicked(item); });
  picker.addEventListener("invoice-item-saved", ({ detail }) => {
    res = res.map((r) => r.id === detail.id ? { ...r, ...detail } : r);
    res.forEach((r, i) => {
      const button = $(`[data-qa="${i}"]`, picker);
      if (r.id === detail.id && button) button.innerHTML = resultLabel(r);
    });
    if (item?.id !== detail.id) return;
    item = { ...item, ...detail };
    q.value = itemText(item);
    showPicked(item);
  });
  price.addEventListener("input", warnPrice);
  let timer, seq = 0, res = [], resultsFor = "";
  const resultLabel = (r) => `${itemHtml(r)} <span class="muted">${esc(r.strength_norm || "")} · ${money(r.public_price)} ج</span>`;
  const current = onThisScreen();
  const search = async (v, select, scan) => {
    const request = ++seq;
    if (!v || (!scan && v.length < 2) || !current() || !q.isConnected) return;
    const found = scan ? await api(`/api/purchases/${picker.dataset.invoice}/scan?barcode=${encodeURIComponent(v)}`)
      : await findItems(v);
    if (request !== seq || !q.isConnected || v !== q.value.trim()) return;
    res = found.items;
    resultsFor = scan ? "" : v;
    const code = found.hint?.kind === "gs1" ? found.hint : null;
    const pickResult = (it) => {
      pick(it);
      if (code?.expiry) {
        $("#qaExp").value = code.expiry;
        $("#qaExpSee").textContent = "الصلاحية من الباركود";
      }
      if (code?.batch) $("#qaBatch").value = code.batch;
    };
    if (scan || code) {
      if (res.length === 1) { pickResult(res[0]); return; }
      if (!res.length) {
        $("#qaRes").innerHTML = '<p class="badge b-warn">الباركود مش مسجل على أي صنف. سجّله من كارت الصنف.</p>';
        return;
      }
    } else if (res.length === 1 && (/^\d{6,}$/.test(v) || (/^\d{3,5}$/.test(v) && res[0].code === v))) {
      pick(res[0]);
      return;
    } else if (select && res.length && !/^\d+$/.test(v)) {
      pick(res[0]);
      return;
    }
    $("#qaRes").innerHTML = (code ? "" : hintHtml(found.hint)) + (res.map((r, i) => `<span class="row nowrap"><button type="button"
      class="btn small secondary" data-qa="${i}">${resultLabel(r)}</button>${invoiceCardButton("data-qa-card", i, purchaseCardKey())}</span>`).join("") || '<p class="muted">لا توجد نتائج</p>');
    $("#qaRes").querySelectorAll("[data-qa]").forEach((b) => b.addEventListener("click", () => pickResult(res[+b.dataset.qa])));
  };
  const runSearch = (select, scan = false) => search(q.value.trim(), select, scan)
    .catch((err) => { if (current()) toast(err.message, true); });
  const resetResults = () => {
    item = null; resultsFor = ""; seq++; clearTimeout(timer); warnPrice();
    $("#qaRes").innerHTML = ""; $("#qaPicked").textContent = "";
  };
  q.addEventListener("input", () => {
    resetResults();
    timer = setTimeout(() => runSearch(false), 250);
  });
  picker.addEventListener("purchase-scan", ({ detail }) => {
    q.focus();
    q.value = detail;
    $("#qaQty").value = "1";
    resetResults();
    runSearch(true, true);
  });
  q.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (item) { entryFields()[0]?.focus(); return; }
    clearTimeout(timer);
    const v = q.value.trim();
    if (resultsFor === v && res.length && !/^\d+$/.test(v)) pick(res[0]);
    else runSearch(true);
  });
  const openCard = (trigger) => {
    clearTimeout(timer); seq++;
    const candidate = trigger.dataset.qaCard === "picked" ? item : res[+trigger.dataset.qaCard];
    showInvoiceItemCard(candidate?.id, {
      purchase: picker.dataset.invoice, query: q.value.trim(), onPick: pick, onSaved: refreshPurchaseItem,
    });
  };
  picker.addEventListener("click", (e) => {
    const button = e.target.closest("[data-qa-card]");
    if (button) openCard(button);
  });
  picker.addEventListener("keydown", (e) => {
    if (!purchaseCardShortcut(e)) return;
    e.preventDefault();
    const result = e.target.closest("[data-qa]")?.dataset.qa;
    openCard(e.target.closest("[data-qa-card]") || $(`[data-qa-card="${result ?? "picked"}"]`, picker));
  });
  const add = async () => {
    if (!item) { toast("اختر الصنف أولًا", true); q.focus(); return; }
    const bad = entryFields().find(field => field.validity.badInput);
    if (bad) {
      toast(`اكتب رقم صحيح في خانة ${bad.labels[0].firstChild.textContent.trim()}`, true);
      bad.focus();
      return;
    }
    const qty = typedWhole($("#qaQty").value);
    if (!(Number(qty) > 0)) { toast("اكتب الكمية", true); $("#qaQty").focus(); return; }
    const line = { item_id: item.id, qty, bonus_qty: $("#qaBonus").disabled ? 0 : typedWhole($("#qaBonus").value) ?? 0 };
    if ($("#qaPrice").value !== "") line.public_price = CONVERT.public_price($("#qaPrice").value);
    if ($("#qaNet").value !== "") line.unit_net_price = CONVERT.unit_net_price($("#qaNet").value);
    else if ($("#qaDisc").value !== "") line.discount_bp = CONVERT.discount_bp($("#qaDisc").value);
    if ($("#qaMargin").value.trim() !== "") line.distributor_margin = exactPiasters($("#qaMargin").value) ?? $("#qaMargin").value;
    if ($("#qaTax").value !== "" || $("#qaTaxMode").value === "1") {
      line.tax_amount = CONVERT.tax_amount($("#qaTax").value);
      line.tax_included = +$("#qaTaxMode").value;
    }
    if ($("#qaExp").value.trim()) line.expiry_date = normExpiry($("#qaExp").value);
    if ($("#qaBatch").value.trim()) line.batch_no = $("#qaBatch").value.trim();
    const keepDisc = $("#qaDisc").value;
    const button = $("#qaAdd", picker);
    await withBusy(button, async () => {
      await waitForPurchaseSave(picker.dataset.invoice);
      if (!current() || !picker.isConnected) return;
      if (await save(`/api/purchases/${CUR.invoice.id}/lines`, { line, picked: true }, "POST", false)) {
        $("#qaDisc").value = keepDisc;  // same supplier discount usually repeats
        $("#qaQ").focus();
      }
    });
  };
  $("#qaAdd").addEventListener("click", add);
  // U2-1: Enter moves to the next box, the last one adds the line and goes back to the item search; the arrows
  // move between the boxes; U2-2: a number box is selected on focus, so typing replaces it
  picker.querySelectorAll("[data-entry-field] :is(input, select)").forEach((el) => {
    el.addEventListener("focus", () => { try { el.select(); } catch { /* not selectable */ } });
    el.addEventListener("keydown", (e) => {
      const fields = entryFields();
      const i = fields.indexOf(el);
      if (e.key === "Enter") { e.preventDefault(); if (i === fields.length - 1 || e.ctrlKey) add(); else fields[i + 1].focus(); return; }
      const back = e.key === (document.dir === "rtl" ? "ArrowRight" : "ArrowLeft"), fwd = e.key === (document.dir === "rtl" ? "ArrowLeft" : "ArrowRight");
      if ((back || fwd) && (el.type === "number" || el.selectionStart === (back ? 0 : el.value.length))) {
        const j = i + (fwd ? 1 : -1);
        if (j >= 0 && j < fields.length) { e.preventDefault(); fields[j].focus(); }
      }
    });
  });
  $("#qaExp").addEventListener("input", (e) => {
    const m = normExpiry(e.target.value).match(/^(\d{1,2})-(\d{2}|\d{4})$/);
    $("#qaExpSee").textContent = m && +m[1] >= 1 && +m[1] <= 12 ? `← ${String(+m[1]).padStart(2, "0")}/${m[2].length === 2 ? `20${m[2]}` : m[2]}` : "";
  });
}

// Buffer a keyboard-wedge burst before it can edit the focused control.
// The pharmacy scanner's F12 prefix is definitive. Unprefixed scanners send a fast burst followed by Enter.
const PURCHASE_SCAN_GAP = 55;
let purchaseKeys, purchaseKeyTimer;
function flushPurchaseKeys() {
  clearTimeout(purchaseKeyTimer);
  const keys = purchaseKeys;
  purchaseKeys = null;
  if (keys && !keys.prefixed && keys.text && keys.target === document.activeElement &&
      keys.target.matches("input, textarea, [contenteditable=true]")) {
    // Use the browser's edit so selection, input events and undo also work for numeric inputs.
    document.execCommand("insertText", false, keys.text);
  }
}

document.addEventListener("pointerdown", flushPurchaseKeys, true);
document.addEventListener("focusout", flushPurchaseKeys, true);
document.addEventListener("keydown", (e) => {
  const picker = $("#qa");
  if (!picker || !routeHash().startsWith("#/purchases/")) return;
  const capture = () => { e.preventDefault(); e.stopPropagation(); };
  if (e.key === "F12") {
    flushPurchaseKeys();
    purchaseKeys = { target: e.target, prefixed: true, text: "" };
    purchaseKeyTimer = setTimeout(flushPurchaseKeys, 1000);
    capture();
    return;
  }
  if (e.key === "Shift") return;
  const separator = e.ctrlKey && e.key === "]" && purchaseKeys;
  if (e.isComposing || e.repeat || e.altKey || e.metaKey || (e.ctrlKey && !separator)) {
    flushPurchaseKeys();
    return;
  }
  if (e.key === "Enter" && purchaseKeys &&
      (purchaseKeys.prefixed || /^\p{Nd}{8,}$/u.test(purchaseKeys.text) ||
        /^(?:\](?:d2|Q3|C1)|\(01\)|01\d{14}|https?:\/\/[^/]+\/.*01\/\d{8,14})/i.test(purchaseKeys.text))) {
    const barcode = purchaseKeys.text;
    purchaseKeys = null;
    clearTimeout(purchaseKeyTimer);
    capture();
    if (barcode) {
      const send = () => picker.dispatchEvent(new CustomEvent("purchase-scan", { detail: barcode }));
      const dialog = $("dialog[open]");
      if (dialog) dialog.addEventListener("close", () => { if (picker.isConnected) send(); }, { once: true });
      else send();
    }
    return;
  }
  if (e.key.length !== 1 || e.key === " ") {
    flushPurchaseKeys();
    return;
  }
  // Start buffering on a digit or a GS1 introducer. Other text and keyboard shortcuts stay native.
  if (!purchaseKeys && !/^[\p{Nd}(\]hH]$/u.test(e.key)) return;
  purchaseKeys ||= { target: e.target, prefixed: false, text: "" };
  purchaseKeys.text += separator ? "\x1d" : e.key;
  capture();
  clearTimeout(purchaseKeyTimer);
  purchaseKeyTimer = setTimeout(flushPurchaseKeys, purchaseKeys.prefixed ? 1000 : PURCHASE_SCAN_GAP);
}, true);

function renderLine(l) {
  const draft = CUR.invoice.status === "draft";
  const iss = lineIssues(l.id).filter((i) => !draft || i.code !== "public_price_changed");
  const hasErr = iss.some((i) => i.level === "error" && !i.acknowledged);
  let match;
  if (l.item_id && l.match_status !== "suggested") {
    match = `<div><span data-line-item>${esc(itemLabel(l.item))}</span> <span class="badge b-ok">${l.match_status === "confirmed" ? "مؤكد" : "تلقائي"}</span></div>
      <div class="muted small" data-line-item-info>${esc(purchaseItemInfo(l.item))}</div>`;
  } else {
    const c = l.candidates || [];
    match = `<div class="cands">${c.length ? `<select data-pick="${esc(l.id)}" ${draft ? "" : "disabled"}>
      <option value="">— اختر الصنف الصحيح —</option>
      ${c.map((x) => `<option value="${esc(x.item_id)}" ${x.item_id === l.item_id ? "selected" : ""}>${esc(x.label)} (${Math.round(x.score * 100)}%)</option>`).join("")}
    </select>` : `<span class="badge b-err">غير موجود</span>`}
    </div>`;
  }
  const warning = draft ? purchasePriceWarning(l.item?.public_price, money(l.public_price)) : "";
  const warningId = draft ? `puPriceWarning-${l.id}` : undefined;
  return `<tr data-purchase-line="${esc(l.id)}" class="${hasErr ? "has-err" : ""}">
    <td>${l.line_no}</td>
    <td class="name"><div class="raw">${esc(l.raw_name)}</div>${match}
      ${invoiceCardButton("data-purchase-card", l.id, purchaseCardKey())}
      ${iss.length ? `<div class="muted">${iss.map((i) => (i.level === "error" ? "⛔ " : "⚠️ ") + esc(i.message)).join("<br>")}</div>` : ""}</td>
    <td data-label="كمية">${fieldInput(l, "qty", l.qty ?? "", { step: "1" })}</td>
    <td data-label="الرصيد">${l.item && l.stock_qty != null ? esc(fmtStock(l.stock_qty, l.item)) : "—"}</td>
    <td data-label="بونص" data-purchase-bonus ${CUR.invoice.supplier_type === "warehouse" ? "hidden" : ""}>${fieldInput(l, "bonus_qty", l.bonus_qty ?? 0, { step: "1" })}</td>
    <td data-label="سعر الجمهور">${fieldInput(l, "public_price", money(l.public_price), { describedBy: warningId })}
      ${draft ? `<small class="badge b-warn" id="${esc(warningId)}" data-price-warning role="status" ${warning ? "" : "hidden"}>${esc(warning)}</small>` : ""}</td>
    <td data-label="خصم %">${fieldInput(l, "discount_bp", pct(l.discount_bp))}</td>
    <td data-label="هامش الموزع">${fieldInput(l, "distributor_margin", money(l.distributor_margin || 0))}</td>
    <td data-label="الصافي">${fieldInput(l, "unit_net_price", money(l.unit_net_price))}</td>
    <td data-label="ضريبة السطر">${fieldInput(l, "tax_amount", money(l.tax_amount))}
      <select aria-label="معاملة الضريبة" data-taxmode="${esc(l.id)}" ${draft ? "" : "disabled"}>
        ${l.tax_included == null && (CUR.invoice.source_type !== "manual" || l.tax_amount != null)
          ? '<option value="">تلقائي (حسب إجمالي السطر)</option>' : ""}
        <option value="0">مضافة</option><option value="1" ${l.tax_included ? "selected" : ""}>مشمولة</option>
      </select></td>
    <td data-label="الإجمالي">${fieldInput(l, "line_total", money(l.line_total))}</td>
    <td data-label="التشغيلة">${fieldInput(l, "batch_no", l.batch_no ?? "", { type: "text" })}</td>
    <td data-label="الصلاحية">${fieldInput(l, "expiry_date", l.expiry_precision === "month" && l.expiry_date
      ? l.expiry_date.slice(5, 7) + "/" + l.expiry_date.slice(0, 4) : l.expiry_date || "", { type: "text" })}</td>
    <td>${draft ? `<button class="btn small secondary" data-del="${esc(l.id)}" aria-label="حذف السطر">حذف</button>
      ${l.item_id && l.qty > 1 ? `<button class="btn small secondary" data-split="${esc(l.id)}" title="جزء من الكمية بصلاحية تانية">+ صلاحية</button>` : ""}` : ""}</td>
  </tr>`;
}

// «طريقة الدفع» (owner 22/11): on the account, cash from the drawer, the treasury, or another money account
async function fillPaySources(inv) {
  const sel = $("#paySrc"); if (!sel) return;
  const opts = [["credit", "آجل (على حساب المورد)"], ["drawer", "نقدي من الدرج"]];
  if (ME.permissions.includes("cash.see_expected")) {
    try {
      for (const a of await api("/api/money/accounts")) {
        if (a.kind === "drawer" || !a.is_active) continue;
        opts.push([a.kind === "treasury" ? "treasury" : a.id, a.kind === "treasury" ? "من الخزنة" : `${a.kind_label || ""}: ${a.name}`]);
      }
    } catch { /* the list needs the right; the drawer and the account stay */ }
  }
  const cur = inv.pay?.source || "credit";
  if (!opts.some(([v]) => v === cur)) opts.push([cur, "حساب محفوظ"]);
  sel.innerHTML = opts.map(([v, t]) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(t)}</option>`).join("");
}

const SUP_TYPE = { company: "شركة", warehouse: "مخزن" };
function updatePurchaseBonus(type) {
  const hidden = type === "warehouse";
  const bonus = $("#qaBonus");
  if (bonus) {
    bonus.disabled = hidden;
    $("#qaBonusField").hidden = hidden;
    bonus.closest("[data-entry-field]").hidden = hidden;
    if (hidden) bonus.value = "0";
  }
  for (const cell of app.querySelectorAll("[data-purchase-bonus]")) cell.hidden = hidden;
}

async function fillSuppliers(selected, draft) {
  const sel = $('[data-h="supplier_id"]');
  const list = await api("/api/suppliers");
  if (!sel.isConnected) return;
  sel.innerHTML = `<option value="">— اختر —</option>` + list.map((s) =>
    `<option value="${esc(s.id)}" ${s.id === selected ? "selected" : ""}>${esc(s.name)}${SUP_TYPE[s.supplier_type] ? ` (${SUP_TYPE[s.supplier_type]})` : ""}</option>`).join("");
  sel.disabled = !draft;
  const cur = list.find((x) => x.id === selected), box = $("#supType");
  updatePurchaseBonus(cur?.supplier_type);
  if (!box) return;
  if (!cur) { box.innerHTML = `<span class="muted">اختر المورد</span>`; return; }
  const canEdit = ME.permissions.includes("suppliers.write");
  box.innerHTML = Object.entries(SUP_TYPE).map(([v, t]) => `<button type="button" class="${cur.supplier_type === v ? "on" : ""}" data-st="${v}" ${canEdit ? "" : "disabled"}>${cur.supplier_type === v ? "✓ " : ""}${t}</button>`).join("");
  box.querySelectorAll("[data-st]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/suppliers/${cur.id}`, { method: "PATCH", body: { supplier_type: b.dataset.st } }); fillSuppliers(selected, draft); toast("تم حفظ نوع المورد"); }
    catch (err) { toast(err.message, true); }
  }));
}

async function purchaseTabs(curId) {
  const box = $("#pTabs"); if (!box) return;
  const drafts = (await api("/api/purchases?status=draft")).slice(0, 12);
  box.innerHTML = drafts.map((d) => `<a class="tab ${d.id === curId ? "on" : ""}" href="#/purchases/${esc(d.id)}">${esc(d.supplier_name || "مورد؟")}${d.invoice_no ? ` · ${esc(d.invoice_no)}` : ""}</a>`).join("")
    + `<button class="tab add" id="pNew">+ فاتورة شراء</button>`;
  $("#pNew").addEventListener("click", async () => {
    try { const r = await api("/api/purchases", { method: "POST", body: {} }); location.hash = `#/purchases/${r.invoice.id}`; }
    catch (err) { toast(err.message, true); }
  });
}

function purchaseDecimal(value) {
  const text = value.trim().replace(/^(-?)\./, (_, sign) => `${sign}0.`);
  return text === "" ? null : exactPiasters(text) ?? { invalid: value };
}

const CONVERT = {
  public_price: purchaseDecimal, unit_net_price: purchaseDecimal, line_total: purchaseDecimal,
  tax_amount: purchaseDecimal,
  // exact piasters, never a float round: 4.005 goes to the server as typed and is refused there (review P2)
  distributor_margin: (v) => (String(v).trim() === "" ? 0 : exactPiasters(v) ?? String(v)),
  discount_bp: purchaseDecimal, qty: (v) => typedWhole(v),
  bonus_qty: (v) => typedWhole(v) ?? 0, batch_no: (v) => v, expiry_date: (v) => normExpiry(v),
};

// U2-1 / U2-10: keyboard moves inside the purchase line grid. Enter = next box (the last box of a row goes to the
// next row), arrows up/down = same box in the row above/below, left/right = the box beside (RTL aware). The grid
// is redrawn after each saved change, so the box to land on is kept by line and field and focused again after it.
const PG_FIELDS = ["qty", "bonus_qty", "public_price", "discount_bp", "distributor_margin", "unit_net_price", "tax_amount", "line_total", "batch_no", "expiry_date"];
let PG_FOCUS = null, PG_SAVING = 0;
function pgFocus() {
  if (!PG_FOCUS) return;
  const el = app.querySelector(`input[data-line="${CSS.escape(PG_FOCUS.line)}"][data-field="${PG_FOCUS.field}"]`);
  PG_FOCUS = null;
  if (el && !el.disabled) { el.focus(); try { el.select(); } catch { /* not selectable */ } }
}
function purchaseCardShortcut(e) {
  return !e.repeat && !e.ctrlKey && !e.altKey && !e.metaKey && !e.shiftKey && e.key === purchaseCardKey();
}

function refreshPurchaseItem(item) {
  for (const line of CUR.lines) {
    if (line.item_id !== item.id) continue;
    line.item = item;
    const row = $(`[data-purchase-line="${CSS.escape(line.id)}"]`, app);
    if (!row) continue;
    const label = $("[data-line-item]", row);
    if (label) label.textContent = itemLabel(item);
    const info = $("[data-line-item-info]", row);
    if (info) info.textContent = purchaseItemInfo(item);
    const warning = $("[data-price-warning]", row);
    if (warning) showPurchasePriceWarning(warning, item.public_price, $('[data-field="public_price"]', row).value);
  }
  $("#qa")?.dispatchEvent(new CustomEvent("invoice-item-saved", { detail: item }));
}

function openPurchaseLineCard(row) {
  const line = CUR.lines.find((l) => l.id === row.dataset.purchaseLine);
  const invoice = CUR.invoice;
  const current = onThisScreen();
  const path = `/api/purchases/${invoice.id}/lines/${line.id}`;
  const draft = invoice.status === "draft";
  const active = document.activeElement;
  const activeInput = active.dataset.line && active.dataset.field;
  const restore = async () => {
    await waitForPurchaseSave(invoice.id);
    if (!current()) return;
    return active.isConnected ? active : activeInput
      ? $(`[data-line="${CSS.escape(active.dataset.line)}"][data-field="${activeInput}"]`, app)
      : $(`[data-purchase-card="${CSS.escape(line.id)}"]`, app);
  };
  const update = async (suffix, method, body) => {
    await waitForPurchaseSave(invoice.id);
    if (!current()) return;
    const updated = await api(path + suffix, { method, body: { ...body, version: CUR.invoice.version } });
    if (!current()) return;
    CUR = updated;
    renderInvoice();
  };
  showInvoiceItemCard(line.match_status === "suggested" ? null : line.item_id, {
    purchase: invoice.id,
    query: line.raw_name,
    newName: line.raw_name,
    onPick: draft ? (item) => update("", "PATCH", { changes: { item_id: item.id } }) : null,
    onCreate: draft ? (item, force) => update("/new-item", "POST", { item, force }) : null,
    onSaved: async (item) => {
      await waitForPurchaseSave(invoice.id);
      if (current()) refreshPurchaseItem(item);
    },
    restore,
  });
}

app.addEventListener("input", (e) => {
  const input = e.target;
  if (input.dataset.field !== "public_price" || !CUR) return;
  const warning = $("[data-price-warning]", input.parentElement);
  const line = CUR.lines.find((l) => l.id === input.dataset.line);
  if (warning) showPurchasePriceWarning(warning, line?.item?.public_price, input.value);
});

function pgTarget(el, key) {
  const rows = [...new Set([...app.querySelectorAll("input[data-line][data-field]")].map((x) => x.dataset.line))];
  const fields = app.querySelector("[data-purchase-bonus]")?.hidden
    ? PG_FIELDS.filter(field => field !== "bonus_qty") : PG_FIELDS;
  const r = rows.indexOf(el.dataset.line), c = fields.indexOf(el.dataset.field);
  if (r < 0 || c < 0) return null;
  const rtl = document.dir === "rtl";
  let [nr, nc] = [r, c];
  if (key === "Enter") { nc = c + 1; if (nc >= fields.length) { nc = 0; nr = r + 1; } }
  else if (key === "ArrowDown") nr = r + 1;
  else if (key === "ArrowUp") nr = r - 1;
  else if (key === (rtl ? "ArrowLeft" : "ArrowRight")) nc = c + 1;
  else if (key === (rtl ? "ArrowRight" : "ArrowLeft")) nc = c - 1;
  if (nr < 0 || nr >= rows.length || nc < 0 || nc >= fields.length) return null;
  return { line: rows[nr], field: fields[nc] };
}
app.addEventListener("focusin", (e) => {       // U2-2 in the grid too: typing replaces the box
  const el = e.target;
  if (CUR && el instanceof HTMLInputElement && el.dataset.field && el.dataset.line) { try { el.select(); } catch { /* not selectable */ } }
});
app.addEventListener("keydown", (e) => {
  const el = e.target;
  const row = el.closest?.("[data-purchase-line]");
  if (row && purchaseCardShortcut(e)) {
    e.preventDefault();
    openPurchaseLineCard(row);
    return;
  }
  if (!CUR || !(el instanceof HTMLInputElement) || !el.dataset.field || !el.dataset.line) return;
  if (!["Enter", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) return;
  const side = e.key === "ArrowLeft" || e.key === "ArrowRight";
  // in a text box the side arrows move the caret until it reaches the edge
  if (side && el.type !== "number" && el.selectionStart !== (e.key === (document.dir === "rtl" ? "ArrowRight" : "ArrowLeft") ? 0 : el.value.length)) return;
  e.preventDefault();                       // never a form submit or a number spin: that was the Enter stall
  if (PG_SAVING) return;                    // one save at a time, for the arrows too (review P2)
  const to = pgTarget(el, e.key);
  if (!to) return;
  PG_FOCUS = to;
  if (el.value !== el.defaultValue) el.blur();  // the change saves, the redraw then lands on the target
  else pgFocus();
});

const purchaseSaves = new Map();
function waitForPurchaseSave(invoiceId) {
  return Promise.allSettled([...purchaseSaves].filter(([, id]) => id === invoiceId).map(([job]) => job));
}
function save(path, body, method = "PATCH", keepPicker = true) {
  const job = savePurchase(path, body, method, keepPicker);
  purchaseSaves.set(job, CUR.invoice.id);
  job.then(() => purchaseSaves.delete(job), () => purchaseSaves.delete(job));
  return job;
}

async function savePurchase(path, body, method, keepPicker) {
  const current = onThisScreen();
  PG_SAVING++;
  try {
    const updated = await api(path, { method, body: { ...body, version: CUR.invoice.version } });
    if (!current() || updated.invoice.version < CUR.invoice.version) return;
    CUR = updated;
    renderInvoice(keepPicker);
    pgFocus();
    return true;
  } catch (e) {
    if (!current()) return;
    toast(e.message, true);
    PG_FOCUS = null;                       // a failed save keeps no pending move (review P2)
    if (e.code === "stale_version") await viewInvoice(CUR.invoice.id);
  } finally { PG_SAVING--; if (PG_FOCUS && !CUR) PG_FOCUS = null; }
}

async function viewInvoice(id) {
  const updated = await api(`/api/purchases/${encodeURIComponent(id)}`);
  if (CUR?.invoice.id === id && updated.invoice.version < CUR.invoice.version) return;
  CUR = updated;
  renderInvoice();
}

app.addEventListener("change", async (e) => {
  const t = e.target;
  if (!CUR) return;
  if ((t.dataset.field || t.dataset.h) && t.validity?.badInput) {
    toast("اكتب قيمة رقمية صحيحة", true);
    return;
  }
  const base = `/api/purchases/${CUR.invoice.id}`;
  if (t.dataset.taxmode) {
    await save(`${base}/lines/${t.dataset.taxmode}`, { changes: { tax_included: t.value === "" ? null : +t.value } });
  } else if (t.dataset.field) {
    const changes = { [t.dataset.field]: CONVERT[t.dataset.field](t.value) };
    if (t.dataset.field === "tax_amount") {
      const mode = t.closest("td").querySelector("[data-taxmode]").value;
      changes.tax_included = mode === "" ? null : +mode;
    }
    await save(`${base}/lines/${t.dataset.line}`, { changes });
  } else if (t.dataset.h) {
    const f = t.dataset.h;
    const v = ["declared_total", "extra_discount"].includes(f) ? purchaseDecimal(t.value)
      : f === "pay_amount" ? (t.value.trim() === "" ? null : exactPiasters(t.value) ?? t.value) : (t.value || null);
    await save(base, { changes: { [f]: v } });
  } else if (t.dataset.pick && t.value) {
    await save(`${base}/lines/${t.dataset.pick}`, { changes: { item_id: t.value } });
  }
});

const PURCHASE_PRICE_MODES = { keep: "خلي التشغيلات الحالية بسعرها", all: "غيّر سعر كل التشغيلات الحالية", per_batch: "اختار لكل تشغيلة" };
const purchasePriceOptions = (selected) => Object.entries(PURCHASE_PRICE_MODES).map(([value, label]) =>
  `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("");
function choosePurchasePrices(review) {
  if (!review.changes.length) return Promise.resolve({});
  const d = document.createElement("dialog");
  d.innerHTML = `<form method="dialog"><h2>سعر الرصيد الحالي</h2>
    ${review.changes.map((c, i) => {
      const item = { pack_size: c.pack_size };
      const stock = fmtStock(c.batches.reduce((n, b) => n + b.qty, 0), item);
      return `<fieldset><legend>${esc(c.name)}</legend>
        <label class="f">السعر اتغير من ${money(c.old)} لـ ${money(c.new)}: تغيّر سعر الرصيد الحالي (${esc(stock)}) للسعر الجديد؟
          <select data-price-item="${i}">${purchasePriceOptions(review.default)}</select>
        </label><div data-price-batches ${review.default === "per_batch" ? "" : "hidden"}>
          ${c.batches.map((b) => `<label class="chk"><input type="checkbox" value="${esc(b.id)}">
            ${esc(b.batch_no || (b.id ? "تشغيلة بدون رقم" : "رصيد من غير تشغيلة"))} · ${esc(b.expiry_date || "—")}
            · ${esc(fmtStock(b.qty, item))} · ${money(b.public_price ?? c.old)} ج</label>`).join("")}
        </div></fieldset>`;
    }).join("")}
    <div class="row"><button type="submit" class="btn" value="approve">تأكيد الأسعار</button>
      <button type="submit" class="btn secondary" value="cancel">رجوع</button></div></form>`;
  const fields = [...d.querySelectorAll("[data-price-item]")];
  for (const select of fields) {
    select.addEventListener("change", () => { select.closest("fieldset").querySelector("[data-price-batches]").hidden = select.value !== "per_batch"; });
  }
  app.appendChild(d);
  return new Promise((resolve) => {
    d.addEventListener("close", () => {
      const decisions = Object.fromEntries(fields.map((select, i) => [review.changes[i].item_id, {
        mode: select.value,
        batches: [...select.closest("fieldset").querySelectorAll("input:checked")].map((b) => b.value),
      }]));
      resolve(d.returnValue === "approve" ? decisions : null);
      d.remove();
    }, { once: true });
    d.showModal();
  });
}

app.addEventListener("mousedown", (e) => {
  if (e.button === 0 && e.target.closest("[data-purchase-card], [data-qa-card]") &&
      document.activeElement.matches("#qa input, table.lines input")) e.preventDefault();
});

app.addEventListener("click", async (e) => {
  const t = e.target.closest("button");
  if (!t || !CUR) return;
  const base = `/api/purchases/${CUR.invoice.id}`;
  if (t.dataset.purchaseCard) {
    openPurchaseLineCard(t.closest("[data-purchase-line]"));
  } else if (t.id === "approve") {
    const pay = CUR.invoice.pay || {}, payNow = pay.source === "credit" ? 0 : pay.amount ?? CUR.invoice.computed_total ?? 0;
    if (!confirm(["اعتماد الفاتورة؟ سيتم إضافة الكميات للمخزون وتحديث التكلفة.",
      payNow ? `هيتدفع للمورد دلوقتي ${money(payNow)} ج: ${$("#paySrc")?.selectedOptions[0]?.textContent || ""}` + (payNow < (CUR.invoice.computed_total || 0) ? "، والباقي على حسابه." : ".")
        : "الفاتورة كلها على حساب المورد (آجل)."].join("\n"))) return;
    const invoice = CUR.invoice;
    t.disabled = true;
    try {
      const review = await api(`${base}/approve`);
      if (!t.isConnected || review.version !== invoice.version) {
        if (t.isConnected) await viewInvoice(invoice.id);
        return;
      }
      const decisions = await choosePurchasePrices(review);
      if (!decisions || !t.isConnected) return;
      const approved = await api(`${base}/approve`, { method: "POST", body: {
        version: review.version, price_token: review.token, price_decisions: decisions,
      } });
      if (!t.isConnected) return;
      CUR = approved;
      renderInvoice();
      toast("تم اعتماد الفاتورة وإضافتها للمخزون");
      purchaseLabels(CUR);
    } catch (err) {
      if (!t.isConnected) return;
      toast(err.message, true);
      if (["stale_version", "has_blocking_issues"].includes(err.code)) await viewInvoice(invoice.id);
    } finally { t.disabled = false; }
  } else if (t.id === "invLabels") {
    purchaseLabels(CUR);
  } else if (t.id === "cancelInv") {
    const reason = prompt("سبب الإلغاء:");
    if (reason) await save(`${base}/cancel`, { reason }, "POST");
  } else if (t.id === "addLine") {
    const name = prompt("اسم الصنف كما في الفاتورة:");
    if (name) await save(`${base}/lines`, { line: { raw_name: name } }, "POST");
  } else if (t.dataset.confirm) {
    await save(`${base}/lines/${t.dataset.confirm}`, { changes: { confirm_fields: [t.dataset.cfield] } });
  } else if (t.dataset.del) {
    if (confirm("حذف السطر؟")) await save(`${base}/lines/${t.dataset.del}`, {}, "DELETE");
  } else if (t.dataset.split) {
    // U2-9: part of this line has another expiry -> its own lot; the quantity of the line is the sum
    const q = typedWhole(prompt("كام علبة من السطر ده صلاحيتها مختلفة؟") || "");
    if (!(Number(q) > 0)) return;
    const exp = prompt("الصلاحية التانية (مثال 1128 أو 11/2028):");
    if (!exp) return;
    const batch = prompt("التشغيلة (اختياري):") || null;
    await save(`${base}/lines/${t.dataset.split}/split`, { qty: Number(q), expiry_date: normExpiry(exp), batch_no: batch }, "POST");
  } else if (t.dataset.merge) {
    if (confirm("دمج السطرين في سطر واحد؟ الكميات والبونص بيتجمعوا.")) await save(`${base}/lines/${t.dataset.merge}/merge`, { other_line: t.dataset.mergewith }, "POST");
  } else if (t.dataset.ack) {
    const reason = prompt("اكتب سبب قبول الفرق (يُسجَّل باسمك):");
    if (reason) await save(`${base}/acknowledge`, { code: t.dataset.ack, line_id: t.dataset.ackline || null, reason }, "POST");
  }
});

// ------------------------------------------------------------------ items
const MARK = { fridge: ["mk-fridge", "❄ ثلاجة"], injection: ["mk-inj", "💉 حقن"] };
const markChip = (m) => (MARK[m] ? `<span class="chip ${MARK[m][0]}">${MARK[m][1]}</span>` : "");
let CATS = null;
async function loadCats(force = false) {
  if (!CATS || force) CATS = (await api("/api/categories")).categories;
  return CATS;
}
const canW = () => ME.permissions.includes("items.write");
const canFixPack = () => canW() && ME.permissions.includes("inventory.adjust");
const PACK_QUESTION = "العلبة فيها كام شريط؟";
const PACK_REVIEW_TITLE = "مراجعة العبوات بعد النقل";
const NAME_REVIEW_TITLE = "مراجعة أسماء العملاء بعد النقل";

const FORMS = [["tab", "أقراص"], ["cap", "كبسول"], ["eff", "فوار"], ["chew", "أقراص مضغ"], ["lozenge", "استحلاب"], ["syrup", "شراب"],
  ["susp", "معلق"], ["solution", "محلول"], ["drops", "نقط"], ["eyedrops", "قطرة عين"], ["amp", "أمبول / حقن"], ["vial", "فيال"],
  ["cream", "كريم"], ["oint", "مرهم"], ["gel", "جل"], ["lotion", "لوشن"], ["shampoo", "شامبو"], ["supp", "لبوس"],
  ["sachet", "أكياس"], ["powder", "بودرة"], ["spray", "بخاخ"], ["inhaler", "بخاخ استنشاق"]];
const formName = (c) => (FORMS.find((f) => f[0] === c) || [c, c || ""])[1];

// المثيل والبديل: one panel for the cashier and the item card. onAdd(item, packs, units) adds to the sale.
async function altPanel(itemId, box, onAdd = null) {
  box.innerHTML = `<p class="muted">جارٍ البحث…</p>`;
  let r;
  try { r = await api(`/api/items/${itemId}/alternatives`); } catch (er) { box.innerHTML = ""; toast(er.message, true); return; }
  const row = (x) => {
    const can = x.stock_qty > 0 || x.is_service || x.stock_qty == null;
    return `<div class="res ${can ? "" : "out"}"><div class="grow"><b>${itemHtml(x)}</b>
      <div class="muted">${esc(x.strength_norm || "")}${x.dosage_form ? ` · ${esc(formName(x.dosage_form))}` : ""}${x.manufacturer ? ` · ${esc(x.manufacturer)}` : ""}
        ${x.stock_qty != null ? ` · <span>رصيد:</span> <b>${esc(fmtStock(x.stock_qty, x) || "0")}</b>` : ""}</div>
      <div class="row tags">${x.expiry_soon ? `<span class="badge b-warn" title="تنتهي صلاحيته خلال ${Math.round(r.soon_days / 30)} أشهر: يُباع أولًا">⚠ صلاحيته ${esc(x.next_expiry.slice(0, 7))}</span>` : ""}
        ${x.margin_bp != null ? `<span class="badge ${x.margin_bp >= 0 ? "b-ok" : "b-err"}">ربح ${pct(x.margin_bp)}%</span>` : ""}
        ${(x.groups || []).map((g) => `<span class="chip">${esc(g)}</span>`).join("")}</div></div>
      <b class="price">${money(x.public_price)} ج</b>
      ${onAdd && can ? `<button class="btn small" data-aa="${esc(x.id)}" data-k="pack">علبة</button>${x.pack_size > 1 ? `<button class="btn small secondary" data-aa="${esc(x.id)}" data-k="unit">${esc(unitName(x))}</button>` : ""}`
        : onAdd ? `<span class="badge b-err">✕ نفد</span>` : `<a class="btn small secondary" href="#/items/${esc(x.id)}">البطاقة</a>`}</div>`;
  };
  const all = [...r.similar, ...r.other_strength, ...r.alternatives];
  const sec = (title, list, empty) => `<h3 class="mt">${title} <span class="muted">(${list.length})</span></h3>${list.map(row).join("") || `<p class="muted">${empty}</p>`}`;
  box.innerHTML = `<div class="panel alts"><div class="row between"><h2>المثيل والبديل لـ ${esc(r.item.name_en || r.item.name_ar)}</h2>
      <button class="btn small secondary" data-aclose>✕ إغلاق</button></div>
    ${r.missing.includes("ingredient") ? `<p class="badge b-warn">⚠ المادة الفعالة غير مكتوبة في بطاقة هذا الصنف، لذلك لا يمكن العثور على المثيل. ${canW() ? `<a href="#/items/${esc(r.item.id)}/edit">أضِفها</a>` : "اطلب من المدير إضافتها."}</p>` : ""}
    ${r.missing.includes("strength") ? `<p class="badge b-warn">⚠ التركيز غير مكتوب، لذلك يُقارَن المثيل دون تركيز.</p>` : ""}
    <p class="muted"><span>الترتيب: ما له رصيد أولًا، ثم الأقرب انتهاءً للصلاحية، ثم الأغلى</span>${ME.permissions.includes("costs.read") ? "<span>، ثم الأعلى ربحًا</span>" : ""}.</p>
    ${sec("المثيل: نفس المادة والتركيز والشكل", r.similar, r.missing.includes("ingredient") ? "—" : "لا يوجد مثيل مسجل")}
    ${r.other_strength.length ? sec("نفس المادة بتركيز أو شكل آخر", r.other_strength, "") : ""}
    ${sec("البديل: من المجموعات العلاجية", r.alternatives, r.groups.length ? "لا توجد أصناف أخرى في المجموعة" : `هذا الصنف ليس في أي مجموعة علاجية.${ME.permissions.includes("items.groups") ? ` <a href="#/groups">حدد المجموعات</a>` : ""}`)}
    </div>`;
  box.querySelector("[data-aclose]").addEventListener("click", () => { box.innerHTML = ""; });
  box.querySelectorAll("[data-aa]").forEach((b) => b.addEventListener("click", () => {
    const x = all.find((y) => y.id === b.dataset.aa); onAdd(x, b.dataset.k === "pack" ? 1 : 0, b.dataset.k === "unit" ? 1 : 0);
  }));
}

// barcode labels: straight to the label printer when one is set up, else the browser page
let LABEL_CFG = null;
const labelSize = (f) => (f.label_preset.value === "custom" ? `${+f.label_w.value}x${+f.label_h.value}` : f.label_preset.value);
const labelFields = (f) => Object.fromEntries([...f.querySelectorAll("[data-lf]")].map((x) => [x.dataset.lf, x.checked]));
async function printLabels(list) {
  const page = () => printInFrame(`/labels?items=${list.map(([id, n]) => `${encodeURIComponent(id)}:${n}`).join(",")}`);
  try { LABEL_CFG ||= await api("/api/settings/receipt"); } catch { LABEL_CFG = { label_lang: "browser" }; }
  if (LABEL_CFG.label_lang === "browser" || LABEL_CFG.label_lang === "a4") return page();
  try { const r = await api("/api/print/labels", { method: "POST", body: { items: list.map(([id, n]) => ({ id, count: n })) } }); toast(`✓ أُرسل ${r.labels} ملصق إلى الطابعة`); }
  catch (er) {
    // the owner switched labels to the browser after this tab read the setting: print there, read it again next time (#324 R16)
    if (er.code === "labels_in_browser") { LABEL_CFG = null; return page(); }
    toast(er.message, true); if (er.code === "print_failed") page();
  }
}

async function viewItems() {
  const cats = await loadCats(true);
  app.innerHTML = `
  <section class="panel">
    <div class="row between"><h1>الأصناف</h1>
      <div class="row">${canW() ? `<a class="btn" href="#/items/new">+ صنف جديد</a><a class="btn secondary" href="#/categories">الأقسام</a>` : ""}${ME.permissions.includes("settings.manage") ? `<a class="btn secondary" href="#/dispensing-notes">ملاحظات الصرف</a>` : ""}${ME.permissions.includes("items.price") ? `<a class="btn secondary" href="#/item-health">فحص بيانات الأصناف</a>` : ""}
      ${ME.role === "owner" ? `<a class="btn secondary" href="#/pack-review">${PACK_REVIEW_TITLE}</a>` : ""}
      <a class="btn secondary" href="#/groups">المجموعات العلاجية (البدائل)</a>
      <a href="/api/export/items.xlsx">تصدير Excel</a></div></div>
    <div class="row"><label class="f grow">بحث بالاسم (عربي أو إنجليزي) أو الكود أو الباركود
      <input id="q" placeholder="مثال: panadol أو بنادول أو asp%cid أو 622..." autocomplete="off"></label>
      <label class="f">القسم <select id="qCat"><option value="">كل الأقسام</option><option value="none">بدون قسم</option>
        ${cats.filter((c) => c.is_active).map((c) => `<option value="${esc(c.id)}">${esc(c.name)} (${c.items})</option>`).join("")}</select></label></div>
  </section>
  <section class="panel"><div id="itemsHint"></div>
    ${canW() ? `<div class="row bulk" id="bulk" hidden><b id="bulkN"></b> <select id="bulkCat"><option value="">— اختر قسمًا —</option>
      ${cats.filter((c) => c.is_active).map((c) => `<option value="${esc(c.id)}">${esc(c.name)}${c.no_return ? " (لا يُقبل إرجاعه)" : ""}</option>`).join("")}<option value="none">أزِلها من أي قسم</option></select>
      <button class="btn small" id="bulkGo">انقلها إلى القسم</button> <button class="btn small secondary" id="bulkLabels">اطبع ملصقًا لكل صنف</button>
      <button class="btn small secondary" data-flag="rx_required">📋 ضع علامة "بوصفة طبية فقط"</button> <button class="btn small secondary" data-flag="misuse_risk">⚠ ضع علامة "يُساء استخدامه"</button>
      <button class="btn small secondary" data-flag="quick_pin">📌 ثبّتها في البيع السريع</button> <button class="btn small secondary" data-flag="quick_pin" data-off="1">أزِلها من البيع السريع</button></div>` : ""}
    <div class="row end" id="itColsBox"></div>
    <div class="tablewrap"><table id="itTable">
    <thead><tr>${canW() ? `<th data-c="sel"><input type="checkbox" id="selAll" aria-label="اختر الكل"></th>` : ""}<th data-c="name">الاسم</th><th data-c="category">القسم</th><th data-c="pack">العبوة</th><th data-c="price">سعر الجمهور</th><th data-c="stock">الرصيد</th><th data-c="code">الكود</th></tr></thead>
    <tbody id="itemsBody"></tbody></table></div></section>`;
  let rows = [];
  const sel = new Set();
  $("#itColsBox").innerHTML = colTools($("#itTable"), "items"); wireCols($("#itColsBox"), $("#itTable"), "items");
  const bulkUi = () => { if (!$("#bulk")) return; $("#bulk").hidden = !sel.size; $("#bulkN").textContent = `${sel.size} صنف مختار`; };
  const load = async () => {
    const cat = $("#qCat").value;
    const r = await api(`/api/items?q=${encodeURIComponent($("#q").value)}&limit=100&meta=1${cat ? `&category=${encodeURIComponent(cat)}` : ""}`);
    rows = r.items; sel.clear(); bulkUi();
    $("#itemsHint").innerHTML = hintHtml(r.hint);
    $("#itemsBody").innerHTML = rows.map((it, i) => `<tr class="clickable" data-i="${i}">
      ${canW() ? `<td><input type="checkbox" data-sel="${i}" aria-label="اختر"></td>` : ""}
      <td>${itemHtml(it)}${it.shortage_at ? ` <span class="badge b-warn">⚠ في النواقص</span>` : ""}</td>
      <td>${esc(it.category || "—")} ${markChip(it.mark)}${it.no_return ? ` <span class="chip mk-nr">لا يُقبل إرجاعه</span>` : ""}</td>
      <td>${it.pack_size} ${esc(unitName(it))}</td><td>${money(it.public_price)}</td>
      <td><b>${esc(fmtStock(it.stock_qty, it)) || "0"}</b>${it.min_stock && it.stock_qty != null && it.stock_qty <= it.min_stock ? ` <span class="badge b-warn">⚠ تحت حد الطلب</span>` : ""}</td>
      <td>${esc(it.code || "")}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">لا توجد نتائج</td></tr>`;
    applyCols($("#itTable"), "items");
    $("#itemsBody").querySelectorAll("tr.clickable").forEach((tr) => tr.addEventListener("click", (e) => {
      if (e.target.matches("input[type=checkbox]")) return;
      location.hash = `#/items/${rows[+tr.dataset.i].id}`;
    }));
    $("#itemsBody").querySelectorAll("[data-sel]").forEach((cb) => cb.addEventListener("change", () => {
      const id = rows[+cb.dataset.sel].id; cb.checked ? sel.add(id) : sel.delete(id); bulkUi();
    }));
  };
  $("#selAll")?.addEventListener("change", (e) => {
    document.querySelectorAll("[data-sel]").forEach((cb) => { cb.checked = e.target.checked; const id = rows[+cb.dataset.sel].id; cb.checked ? sel.add(id) : sel.delete(id); });
    bulkUi();
  });
  $("#bulkLabels")?.addEventListener("click", () => printLabels([...sel].map((x) => [x, 1])));
  document.querySelectorAll("[data-flag]").forEach((b) => b.addEventListener("click", async () => {
    if (!sel.size) return toast("اختر الأصناف أولًا", true);
    try { const r = await api("/api/items/flags", { method: "POST", body: { item_ids: [...sel], [b.dataset.flag]: !b.dataset.off } }); toast(`✓ تم تعديل ${r.updated} صنف`); load(); }
    catch (err) { toast(err.message, true); }
  }));
  $("#bulkGo")?.addEventListener("click", async () => {
    const v = $("#bulkCat").value; if (!v) return toast("اختر القسم", true);
    try { const r = await api("/api/categories/assign", { method: "POST", body: { item_ids: [...sel], category_id: v === "none" ? null : v } });
      toast(`تم نقل ${r.updated} صنف`); load(); }
    catch (err) { toast(err.message, true); }
  });
  let t;
  $("#q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 250); });
  $("#qCat").addEventListener("change", load);
  load();
}

// ------------------------------------------------------------------ item card
const packs = (q, it) => (it.pack_size > 1 ? fmtStock(q, it) : `${q} ${esc(unitName(it))}`);
async function viewItem(id) {
  if (id === "new") return viewItemEdit(null);
  const c = await api(`/api/items/${id}/card`), cats = await loadCats();
  const low = c.min_stock && c.stock_qty != null && c.stock_qty <= c.min_stock;
  const maxM = Math.max(1, ...c.monthly.map((m) => m.qty));
  const today = isoDay(new Date()), soon = isoDay(new Date(Date.now() + 90 * 864e5));
  const expCls = (d) => (!d ? "b-plain" : d < today ? "b-err" : d < soon ? "b-warn" : "b-ok");
  app.innerHTML = `
  <section class="panel"><div class="row between">
    <div><a href="#/items">← الأصناف</a>
      <h1 class="ltr-name">${esc(c.name_en || c.name_ar)}</h1>
      ${c.name_en && c.name_ar ? `<p class="muted">${esc(c.name_ar)}</p>` : ""}
      <div class="row tags">${c.category ? `<span class="chip">${esc(c.category)}</span>` : `<span class="chip">بدون قسم</span>`}${markChip(c.mark)}${c.is_service ? `<span class="badge b-info">🛎 خدمة</span>` : ""}${c.hidden ? `<span class="badge b-warn">🔒 مخفي</span>` : ""}${c.hide_on_receipt ? `<span class="badge">🙈 لا يظهر في الإيصال</span>` : ""}${c.rx_required ? `<span class="badge b-err">📋 بوصفة طبية فقط</span>` : ""}${c.misuse_risk ? `<span class="badge b-warn">⚠ يُساء استخدامه</span>` : ""}${c.sale_note ? `<span class="badge b-info">📝 ${esc(c.sale_note)}</span>` : ""}
        ${c.no_return ? `<span class="chip mk-nr">✕ لا يُقبل إرجاعه ولا استبداله</span>` : ""}
        ${c.manufacturer ? `<span class="chip">${esc(c.manufacturer)}</span>` : ""}${c.code ? `<span class="chip">كود ${esc(c.code)}</span>` : ""}
        ${c.active_ingredient ? `<span class="chip"><bdi dir="ltr">${esc(c.active_ingredient)}</bdi></span>` : ""}${c.strength_norm || c.dosage_form ? `<span class="chip"><bdi dir="ltr">${esc(c.strength_norm || "")}</bdi> ${esc(formName(c.dosage_form))}</span>` : ""}${c.quick_pin ? `<span class="chip">📌 بيع سريع</span>` : ""}
        ${c.shortage_at ? `<span class="badge b-warn">⚠ مسجل ناقصًا منذ ${esc(c.shortage_at)}${c.shortage_by_name ? ` — ${esc(c.shortage_by_name)}` : ""}</span>` : ""}</div></div>
    ${canW() ? `<div class="row"><a class="btn" href="#/items/${esc(id)}/edit">تعديل</a>
      <span class="row"><input id="lbN" type="number" min="1" max="500" value="1" class="narrow" title="عدد الملصقات"><button class="btn secondary" id="lbGo">اطبع ملصق باركود</button></span>
      <button class="btn secondary" id="shortBtn">${c.shortage_at ? "إزالة علامة النقص" : "سجّله ناقصًا"}</button></div>` : ""}
  </div></section>
  <div class="totals">
    <div>الرصيد<b>${c.stock_qty != null ? esc(packs(c.stock_qty, c)) || "0" : "—"}</b>
      ${low ? `<span class="badge b-warn">⚠ تحت حد الطلب (${esc(packs(c.min_stock, c))})</span>` : c.min_stock ? `<span class="muted">حد الطلب ${esc(packs(c.min_stock, c))}</span>` : `<span class="muted">لا يوجد حد طلب</span>`}</div>
    <div>سعر الجمهور<b>${money(c.public_price)} ج</b>${c.pack_size > 1 && c.public_price ? `<span class="muted">${esc(unitName(c))}: ${money(Math.round(c.public_price / c.pack_size))} ج</span>` : ""}</div>
    ${c.avg_cost_pack != null ? `<div>متوسط التكلفة<b>${money(c.avg_cost_pack)} ج</b><span class="badge ${c.margin_pct >= 0 ? "b-ok" : "b-err"}">ربح ${c.margin_pct}%</span></div>` : ""}
    <div>المكان<b>${esc(c.location || "—")}</b></div>
  </div>
  <div class="grid2 mt">
    <section class="panel"><h2>الوحدات والبيع</h2>
      <div class="totals"><div>العبوة<b>${esc(c.pack_unit_name === "pack" ? "علبة" : c.pack_unit_name)}</b></div>
        <div>تُباع بـ<b>${esc(unitName(c))}</b></div><div>العبوة تحتوي على<b>${c.pack_size} ${esc(unitName(c))}</b></div>
        <div>الحد الأقصى<b>${c.max_stock ? esc(packs(c.max_stock, c)) : "—"}</b></div></div>
      ${c.pack_size === 1 && !c.is_service && canFixPack() ? `<button type="button" class="btn secondary" id="cardPackFix">${PACK_QUESTION}</button>` : ""}
      <h2 class="mt">الباركود والكود الدولي</h2>
      <ul class="plain">${c.barcodes.map((b) => `<li><bdi dir="ltr">${esc(b.code)}</bdi> ${b.gtin ? `<span class="badge b-ok">✓ دولي</span>` : ""}</li>`).join("") || `<li class="muted">لا يوجد باركود</li>`}</ul>
      <h2 class="mt">البيع آخر 12 شهر</h2>
      <div class="bars" role="img" aria-label="البيع الشهري">${c.monthly.map((m) => `<div title="${esc(m.month)}: ${m.qty}"><span class="${m.source === "import" ? "imp" : ""}" data-h="${Math.round((m.qty / maxM) * 100)}"></span><small>${esc(m.month.slice(5))}</small></div>`).join("")}</div>
      <p class="muted">${c.monthly.some((m) => m.source === "import") ? "الأعمدة الفاتحة من مبيعات البرنامج القديم." : ""} الأرقام بالـ ${esc(unitName(c))}.</p>
    </section>
    <section class="panel"><h2>التشغيلات والصلاحية</h2>
      ${c.batches ? `<table><thead><tr><th>الصلاحية</th><th>التشغيلة</th><th>الكمية</th><th>السعر</th><th>وردت من</th></tr></thead><tbody>
        ${c.batches.map((b) => `<tr><td><span class="badge ${expCls(b.expiry_date)}">${b.expiry_date && b.expiry_date < today ? "✕ " : b.expiry_date && b.expiry_date < soon ? "⚠ " : ""}${esc(b.expiry_date ? b.expiry_date.slice(0, 7) : "غير مسجلة")}</span></td>
          <td>${esc(b.batch_no || "—")}</td><td>${esc(packs(b.qty, c))}</td><td>${b.public_price != null ? money(b.public_price) : money(c.public_price)}</td><td>${esc(b.supplier || "—")}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">لا يوجد رصيد</td></tr>`}</tbody></table>
        <p class="badge b-info mt">ℹ كل تشغيلة بسعرها: إذا زاد السعر، يُباع القديم بسعره حتى ينفد.</p>` : ""}
      ${c.suppliers ? `<h2 class="mt">الموردون وآخر أسعار الشراء</h2><table><thead><tr><th>المورد</th><th>آخر مرة</th><th>الخصم</th><th>سعر الشراء</th></tr></thead><tbody>
        ${c.suppliers.map((sp, i) => `<tr${i === 0 && c.suppliers.length > 1 ? ` class="best"` : ""}><td>${esc(sp.supplier)}${i === 0 && c.suppliers.length > 1 ? ` <span class="badge b-ok">✓ الأرخص</span>` : ""}</td>
          <td>${esc(sp.last_date || "")}</td><td>${sp.discount_bp != null ? pct(sp.discount_bp) + "%" : "—"}</td><td>${money(sp.unit_net_price)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">لا يوجد شراء مسجل بعد</td></tr>`}</tbody></table>` : ""}
      ${c.price_history?.length ? `<h2 class="mt">آخر تغييرات السعر</h2><ul class="plain">${c.price_history.map((h) => `<li>${esc(dayOf(h.observed_at))}: ${h.previous_price != null ? `${money(h.previous_price)} ← ` : ""}<b>${money(h.public_price)}</b> <span class="muted">${h.source === "manual" ? "يدوي" : h.source === "purchase_invoice" ? "من فاتورة شراء" : "استيراد"}</span></li>`).join("")}</ul>` : ""}
    </section></div>
  ${c.is_service ? "" : `<div id="altBox"></div>`}
  ${ME.permissions.includes("inventory.adjust") && c.batches ? `<section class="panel" id="adjBox"><h2>تسوية الرصيد (تصفير، تالف، منتهي، مفقود…)</h2>
    <form id="adjForm" class="row"><label class="f">التشغيلة <select name="b"><option value="">كل التشغيلات (الأقرب صلاحية الأول)</option>${c.batches.filter((b) => b.qty > 0).map((b) => `<option value="${esc(b.batch_id || "")}">${esc(b.batch_no || "بدون تشغيلة")} — ${esc((b.expiry_date || "").slice(0, 7))} — ${esc(packs(b.qty, c))}</option>`).join("")}</select></label>
      <label class="f">العملية <select name="mode"><option value="out">نقص كمية</option><option value="zero">تصفير</option><option value="in">زيادة كمية</option></select></label>
      <label class="f">الكمية (وحدة) <input name="q" type="number" min="1" step="1" class="narrow"></label>
      <label class="f">السبب <select name="why"></select></label><label class="f grow">ملاحظة <input name="note"></label>
      <button class="btn small">سجّل</button>${ME.permissions.includes("purchases.approve") ? ` <a class="btn small secondary" href="#/supplier-returns/new?item=${esc(id)}">مرتجع للمورد</a>` : ""}</form>
    <p class="muted">يُسجَّل المنتهي والتالف "إعدام". تُسجَّل كل تسوية بسببها واسمك، وتظهر في تقرير خسائر المخزون.</p></section>` : ""}
  ${ME.permissions.includes("inventory.read") ? `<section class="panel" id="mvRep"><div class="row between"><h2>حركة الصنف لفترة</h2>${reportTools("mv")}</div>${rangeBar("mvRange")}<div id="mvBox"></div></section>` : ""}`;
  document.querySelectorAll(".bars [data-h]").forEach((el) => { el.style.height = `${el.dataset.h}%`; });
  if ($("#altBox")) altPanel(id, $("#altBox"));
  if ($("#adjForm")) {
    const f = $("#adjForm"), R = await stockReasons();
    const sync = () => { const up = f.mode.value === "in"; f.why.innerHTML = reasonOpts(R.adjust, up ? "found" : "expired", up); f.q.disabled = f.mode.value === "zero"; };
    f.mode.addEventListener("change", sync); sync();
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = { batch_id: f.b.value || null, reason: f.why.value, note: f.note.value || null };
      if (f.mode.value === "zero") body.set_to = 0; else body.qty_delta = (f.mode.value === "in" ? 1 : -1) * (+f.q.value || 0);
      if (!confirmInline(f.querySelector("button"))) return;
      try { const r = await api(`/api/items/${id}/adjust`, { method: "POST", body }); toast(`✓ أصبح الرصيد ${r.after} وحدة`); viewItem(id); }
      catch (err) { toast(err.message, true); }
    });
  }
  $("#cardPackFix")?.addEventListener("click", async (e) => {
    const button = e.currentTarget;
    if (await withBusy(button, () => packFix(id, null, true)) && button.isConnected) {
      await viewItem(id);
      estockPanel("item", id);
    }
  });
  if ($("#mvRep")) {
    let mv = null;
    const q = (n) => `${n < 0 ? "−" : ""}${fmtStock(Math.abs(n), { pack_size: c.pack_size, base_unit_name: c.base_unit_name }) || 0}`;
    wireRange("mvRange", async (f, t) => {
      try {
        mv = await api(`/api/items/${id}/movements?from=${f}&to=${t}`);
        $("#mvBox").innerHTML = `<div class="totals"><div>الرصيد أول المدة<b>${esc(q(mv.opening))}</b></div>
          ${mv.totals.map((x) => `<div>${esc(x.label)}<b>${x.qty < 0 ? "" : "+ "}${esc(q(x.qty))}</b></div>`).join("")}
          <div>الرصيد آخر المدة<b>${esc(q(mv.closing))}</b></div></div>
          <div class="tablewrap mt"><table><thead><tr><th>اليوم والوقت</th><th>الحركة</th><th>المرجع</th><th>التشغيلة</th><th>وارد</th><th>صادر</th><th>الرصيد</th><th>بواسطة</th></tr></thead><tbody>
          ${mv.rows.map((r) => `<tr><td>${esc(dayOf(r.created_at))} ${hhmm(r.created_at)}</td><td>${esc(r.label)}</td>
            <td>${r.sale_no ? `فاتورة بيع ${r.sale_no}` : r.invoice_no ? `فاتورة ${esc(r.invoice_no)} — ${esc(r.supplier || "")}` : esc(r.note || "")}</td>
            <td>${esc(r.batch_no || "")}${r.expiry_date ? ` <span class="muted">${esc(r.expiry_date.slice(0, 7))}</span>` : ""}</td>
            <td>${r.qty_delta > 0 ? esc(q(r.qty_delta)) : ""}</td><td>${r.qty_delta < 0 ? esc(q(-r.qty_delta)) : ""}</td><td>${esc(q(r.balance))}</td><td>${esc(r.by_name || "")}</td></tr>`).join("")
            || `<tr><td colspan="8" class="muted">لا توجد حركة في هذه الفترة</td></tr>`}</tbody></table></div>`;
      } catch (err) { toast(err.message, true); }
    });
    wireTools("mv", () => ({ title: `حركة ${c.name_en || c.name_ar}`, subtitle: rangeText(mv.from, mv.to), el: $("#mvRep"), sheets: [
      { name: "حركة الصنف", columns: [Tx("اليوم والوقت"), Tx("الحركة"), Tx("المرجع"), Tx("التشغيلة"), Dt("الصلاحية"), N("الكمية بالوحدة"), N("الرصيد بالوحدة"), Tx("بواسطة")],
        rows: [["", "الرصيد أول المدة", "", "", "", null, mv.opening, ""], ...mv.rows.map((r) => [`${dayOf(r.created_at)} ${hhmm(r.created_at)}`, r.label,
          r.sale_no ? `فاتورة بيع ${r.sale_no}` : r.invoice_no ? `فاتورة ${r.invoice_no} — ${r.supplier || ""}` : r.note || "", r.batch_no || "", r.expiry_date || "", r.qty_delta, r.balance, r.by_name || ""])],
        total: ["", "الرصيد آخر المدة", "", "", "", null, mv.closing, ""] }] }));
  }
  $("#lbGo")?.addEventListener("click", () => printLabels([[id, Math.min(500, Math.max(1, +$("#lbN").value || 1))]]));
  $("#shortBtn")?.addEventListener("click", async () => {
    try { await api(`/api/items/${id}/shortage`, { method: "POST", body: { on: !c.shortage_at } }); viewItem(id); }
    catch (err) { toast(err.message, true); }
  });
}

function invoiceCardDetails(c) {
  const cost = c.avg_cost_pack;
  return `<h3 class="ltr-name">${esc(itemText(c))}</h3>
    ${c.name_en && c.name_ar ? `<p>${esc(PREFS.names === "ar" ? c.name_en : c.name_ar)}</p>` : ""}
    <dl class="invoice-card-fields">
      <div><dt>الكود الداخلي</dt><dd><bdi>${esc(c.code || "—")}</bdi></dd></div>
      <div><dt>الباركود</dt><dd>${c.barcodes.map((b) => `<bdi dir="ltr">${esc(b.code)}</bdi>`).join("<br>") || "—"}</dd></div>
      <div><dt>العبوة</dt><dd>${esc(invoicePack(c))}</dd></div>
      <div><dt>سعر الجمهور (ج)</dt><dd>${money(c.public_price) || "—"}</dd></div>
      ${c.recent_purchases ? `<div><dt>متوسط التكلفة</dt><dd>${money(cost) || "—"}</dd></div>` : ""}
    </dl>
    ${c.batches ? `<h3>التشغيلات والصلاحية</h3><div class="tablewrap"><table>
      <thead><tr><th>التشغيلة</th><th>الصلاحية</th><th>الرصيد</th><th>سعر الجمهور (ج)</th></tr></thead>
      <tbody>${c.batches.map((b) => `<tr><td>${esc(b.batch_no || (b.batch_id ? "—" : "من غير تشغيلة"))}</td><td>${esc(b.expiry_date || "—")}</td>
        <td>${esc(fmtStock(b.qty, c))}</td><td>${money((b.batch_id ? b.public_price : c.untracked_public_price) ?? c.public_price) || "—"}</td></tr>`).join("") ||
        '<tr><td colspan="4" class="muted">لا يوجد رصيد</td></tr>'}</tbody></table></div>` : ""}
    ${c.recent_purchases ? `<h3>آخر المشتريات</h3><div class="tablewrap"><table>
      <thead><tr><th>المورد</th><th>التاريخ</th><th>الكمية (علبة)</th><th>سعر الشراء (ج)</th></tr></thead>
      <tbody>${c.recent_purchases.map((p) => `<tr><td>${esc(p.supplier || "—")}</td><td>${esc(p.invoice_date || "—")}</td>
        <td>${p.qty}</td><td>${money(p.unit_net_price) || "—"}</td></tr>`).join("") ||
        '<tr><td colspan="4" class="muted">لا يوجد شراء مسجل بعد</td></tr>'}</tbody></table></div>` : ""}`;
}

function showInvoiceItemCard(id, { purchase, query = "", newName, onPick, onCreate, onSaved, restore } = {}) {
  if ($("dialog[open]")) return;
  const focused = document.activeElement, current = onThisScreen();
  const d = document.createElement("dialog");
  d.className = "invoice-item-card";
  d.setAttribute("aria-labelledby", "invoiceCardTitle");
  d.innerHTML = `<div class="row between"><h2 id="invoiceCardTitle">كارت الصنف</h2>
    <button type="button" class="btn small secondary" data-card-close autofocus>قفل</button></div>
    <p class="err" role="alert" hidden></p><div data-card-body></div>`;
  const box = $("[data-card-body]", d), close = $("[data-card-close]", d), error = $("[role=alert]", d);
  const lifecycle = new AbortController();
  let busy = false;
  const showError = (err) => { error.textContent = err.message; error.hidden = false; };
  const run = async (action) => {
    if (busy || !d.open) return;
    busy = true;
    error.hidden = true;
    try { await action(); } catch (err) { if (d.open) showError(err); }
    finally { busy = false; }
  };
  const dismiss = async () => {
    if (!d.open) return;
    d.close();
    lifecycle.abort();
    d.remove();
    if (!current()) return;
    const target = restore ? await restore() : focused;
    if (current() && !$("dialog[open]") &&
        [document.body, focused, target].includes(document.activeElement)) target?.focus({ preventScroll: true });
  };
  const finish = async (callback, ...args) => {
    if (!current() || !d.open) return;
    await callback?.(...args);
    await dismiss();
  };
  close.addEventListener("click", () => { if (!busy) dismiss(); });
  d.addEventListener("cancel", (e) => {
    e.preventDefault();
    if (!busy) dismiss();
  });
  window.addEventListener("hashchange", () => { if (!current()) dismiss(); }, { signal: lifecycle.signal });

  const path = (itemId) => `/api/items/${encodeURIComponent(itemId)}/card?recent_purchases=1${purchase ? `&purchase=${encodeURIComponent(purchase)}` : ""}`;
  const show = async (itemId) => {
    box.innerHTML = '<p role="status">بنعرض كارت الصنف…</p>';
    const item = await api(path(itemId));
    if (!d.open) return;
    box.innerHTML = invoiceCardDetails(item) + (canW() ? '<button type="button" class="btn mt" data-card-edit>تعديل</button>' : "");
    $("[data-card-edit]", box)?.addEventListener("click", () => run(async () => {
      const cats = await loadCats(true);
      if (!d.open) return;
      renderItemEdit(box, itemId, item, cats, { run, onSave: (saved) => finish(onSaved, saved) });
      $("[name=name_en]", box).focus();
    }));
  };
  const newItem = () => {
    box.innerHTML = `<form><h3>صنف جديد</h3>
      <label class="f">اسم الصنف<input name="name_en" required value="${esc(newName || "")}"></label>
      <label class="f">عدد الوحدات في العبوة<input name="pack_size" type="number" min="1" step="1" value="1" required></label>
      <div class="row"><button class="btn">احفظ</button><button type="button" class="btn secondary" data-card-back>رجوع</button></div></form>`;
    const form = $("form", box);
    form.name_en.focus();
    $("[data-card-back]", box).addEventListener("click", () => { if (!busy) searchForm(); });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      run(async () => {
        const item = { name_en: form.name_en.value.trim(), pack_size: form.pack_size.valueAsNumber };
        try { await finish(onCreate, item, false); }
        catch (err) {
          if (err.code !== "possible_duplicate") throw err;
          const labels = (err.details?.duplicates || []).map((entry) => entry.label).join("، ");
          if (confirm(`يوجد صنف مشابه: ${labels}\nهل أنت متأكد أنه صنف مختلف؟`)) await finish(onCreate, item, true);
        }
      });
    });
  };
  const searchForm = () => {
    box.innerHTML = `<form><label class="f">دَوّر على الصنف بالاسم أو الكود أو الباركود
      <input type="search" autocomplete="off" value="${esc(query)}"></label>
      <div class="row"><button class="btn secondary">بحث</button>
      ${onCreate ? '<button type="button" class="btn secondary" data-card-new>صنف جديد</button>' : ""}</div></form>
      <div data-card-results></div>`;
    const input = $("input", box), results = $("[data-card-results]", box);
    let request = 0, timer;
    const search = async () => {
      clearTimeout(timer);
      const n = ++request, value = input.value.trim();
      if (value.length < 2) return;
      try {
        const { items } = await findItems(value);
        if (!d.open || !input.isConnected || n !== request) return;
        results.innerHTML = items.map((item, i) => `<div class="row between"><span>${itemHtml(item)}</span>
          <button type="button" class="btn small secondary" data-card-result="${i}">${onPick ? (newName === undefined ? "اختار الصنف" : "ربط الصنف") : "كارت الصنف"}</button></div>`).join("") ||
          '<p class="muted">مفيش أصناف مطابقة</p>';
        results.querySelectorAll("[data-card-result]").forEach((button) => button.addEventListener("click", () => {
          const item = items[+button.dataset.cardResult];
          run(() => onPick ? finish(onPick, item) : show(item.id));
        }));
      } catch (err) { if (d.open && input.isConnected && n === request) showError(err); }
    };
    input.addEventListener("input", () => {
      query = input.value;
      request++;
      results.replaceChildren();
      error.hidden = true;
      clearTimeout(timer);
      timer = setTimeout(search, 200);
    });
    $("form", box).addEventListener("submit", (e) => { e.preventDefault(); search(); });
    $("[data-card-new]", box)?.addEventListener("click", () => { if (!busy) newItem(); });
    input.focus();
    search();
  };

  document.body.append(d);
  d.showModal();
  if (id) show(id).catch((err) => { if (d.open) showError(err); });
  else searchForm();
}

async function viewItemEdit(id) {
  if (!canW()) { location.hash = id ? `#/items/${id}` : "#/items"; return; }
  const it = id ? await api(`/api/items/${id}/card`) : { pack_size: 1, base_unit_name: "unit", pack_unit_name: "pack", barcodes: [] };
  const cats = await loadCats(true);
  renderItemEdit(app, id, it, cats);
}

function renderItemEdit(root, id, it, categories, modal = null) {
  const cats = categories.filter((c) => c.is_active || c.id === it.category_id);
  const $ = (s) => root.querySelector(s);
  const canPrice = ME.permissions.includes("items.price") || !id;
  const unitVal = (v, def) => (v === "unit" ? "" : v === "pack" ? "" : v) || def;
  root.innerHTML = `<form class="panel" id="itemForm">${modal
    ? '<h3>تعديل الصنف</h3>'
    : `<div class="row between"><div><a href="${id ? `#/items/${esc(id)}` : "#/items"}">← رجوع</a>
      <h1>${id ? "تعديل صنف" : "صنف جديد"}</h1></div></div>`}
    <div class="formgrid">
      <label class="f">الاسم بالإنجليزي <input name="name_en" dir="ltr" value="${esc(it.name_en || "")}"></label>
      <label class="f">الاسم بالعربي <input name="name_ar" value="${esc(it.name_ar || "")}"></label>
      <label class="f">الشركة <input name="manufacturer" value="${esc(it.manufacturer || "")}"></label>
      <label class="f">المادة الفعالة <input name="active_ingredient" dir="ltr" value="${esc(it.active_ingredient || "")}" placeholder="Amoxicillin + Clavulanic acid">
        <span class="muted">بالإنجليزية كما على العلبة، ومنها يُعثر على المثيل.</span></label>
      <label class="f">التركيز <input name="strength" dir="ltr" value="${esc(it.strength || it.strength_norm || "")}" placeholder="500 mg"></label>
      <label class="f">الشكل <select name="dosage_form"><option value="">—</option>${(it.dosage_form && !FORMS.some((x) => x[0] === it.dosage_form) ? [[it.dosage_form, it.dosage_form], ...FORMS] : FORMS).map(([c, t]) => `<option value="${esc(c)}" ${c === it.dosage_form ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
      <label class="f">الكود الداخلي <input name="code" value="${esc(it.code || "")}"></label>
      <label class="f">القسم <select name="category_id"><option value="">— بدون قسم —</option>${cats.map((c) => `<option value="${esc(c.id)}" ${c.id === it.category_id ? "selected" : ""}>${esc(c.name)}${c.no_return ? " (لا يُقبل إرجاعه)" : ""}</option>`).join("")}</select></label>
      <label class="f">سعر الجمهور للعبوة (ج) <input name="public_price" type="number" step="0.01" min="0" value="${it.public_price != null ? money(it.public_price) : ""}" ${canPrice ? "" : "disabled"}>
        ${canPrice ? "" : `<span class="muted">تحديث السعر للمالك والمدير فقط</span>`}</label>
      <label class="f">اسم العبوة <input name="pack_unit_name" value="${esc(unitVal(it.pack_unit_name, "علبة"))}"></label>
      <label class="f">اسم الوحدة الصغيرة <input name="base_unit_name" value="${esc(unitVal(it.base_unit_name, modal ? "وحدة" : "شريط"))}" placeholder="شريط / أمبول / قرص"></label>
      <label class="f">عدد الوحدات في العبوة <input name="pack_size" type="number" min="1" step="1" value="${it.pack_size}" ${id && it.stock_qty ? "disabled" : ""}>
        ${id && it.stock_qty ? `<span class="muted">مقفلة لأن للصنف رصيدًا</span>` : ""}
        ${!modal && id && !it.is_service && canFixPack() ? `<button type="button" class="btn small secondary" id="packFixBtn">${it.pack_size === 1 ? PACK_QUESTION : "تصحيح عدد الوحدات (الرصيد يتحوّل معاه)"}</button>` : ""}</label>
      <label class="f">حد الطلب (بالعبوة) <input name="min_packs" type="number" min="0" step="1" value="${it.min_stock ? Math.round(it.min_stock / it.pack_size) : ""}"></label>
      <label class="f">الحد الأقصى (بالعبوة) <input name="max_packs" type="number" min="0" step="1" value="${it.max_stock ? Math.round(it.max_stock / it.pack_size) : ""}"></label>
      <label class="f">المكان (الرف) <input name="location" value="${esc(it.location || "")}" placeholder="مثال: أقراص — A"></label>
    </div>
    <h2 class="mt">نوع الصنف</h2>
    <div class="row"><label class="chk"><input type="checkbox" name="is_service" ${it.is_service ? "checked" : ""}> 🛎 خدمة (تُباع دون رصيد: توصيل، قياس ضغط، حقن…)</label>
      <label class="chk"><input type="checkbox" name="hide_on_receipt" ${it.hide_on_receipt ? "checked" : ""}> 🙈 لا يظهر اسمه في الإيصال المطبوع</label>
      ${ME.permissions.includes("items.hidden") ? `<label class="chk"><input type="checkbox" name="hidden" ${it.hidden ? "checked" : ""}> 🔒 صنف مخفي (يظهر ويُباع لمن لديه صلاحية "الأصناف المخفية" فقط)</label>` : ""}
      <label class="chk"><input type="checkbox" name="quick_pin" ${it.quick_pin ? "checked" : ""}> 📌 زر ثابت في البيع السريع</label></div>
    <h2 class="mt">تحذير للصيدلي وقت البيع</h2>
    <div class="row"><label class="chk"><input type="checkbox" name="rx_required" ${it.rx_required ? "checked" : ""}> 📋 يُصرف بوصفة طبية فقط (عليه غرامة)</label>
      <label class="chk"><input type="checkbox" name="misuse_risk" ${it.misuse_risk ? "checked" : ""}> ⚠ يُساء استخدامه</label>
      <label class="f grow">ملاحظة تظهر للصيدلي <input name="sale_note" value="${esc(it.sale_note || "")}" placeholder="مثال: اسأل عن الحمل / ممنوع للأطفال تحت سنتين"></label></div>
    <p class="muted">أي علامة هنا تظهر للصيدلي في شاشة البيع ويجب أن يؤكدها قبل البيع، ويُسجَّل التأكيد باسمه (تقرير "تحذيرات الأصناف").</p>
    <h2 class="mt">الباركود والكود الدولي</h2>
    <p class="muted">امسح هنا الباركود أو الكود المربع الموجود على العلبة. يُحفظ الكود المربع بالكود الدولي الذي بداخله.</p>
    <ul class="plain" id="bcList">${(it.barcodes || []).map((b) => `<li><bdi dir="ltr">${esc(b.code)}</bdi> ${b.gtin ? `<span class="badge b-ok">✓ دولي</span>` : ""}
      ${id ? `<button type="button" class="btn small secondary" data-rm="${esc(b.code)}">إزالة</button>` : ""}</li>`).join("")}</ul>
    <div class="row"><input id="bcNew" dir="ltr" placeholder="امسح هنا" autocomplete="off"><button type="button" class="btn small secondary" id="bcAdd">أضف</button></div>
    <div class="row mt"><button class="btn">احفظ</button></div></form>`;
  const newCodes = new Set(), removedCodes = new Set();
  const perform = modal ? modal.run : async (action) => {
    try { await action(); } catch (err) { toast(err.message, true); }
  };
  const readCode = (raw) => {     // a 2D code -> its GTIN (as the EAN-13 printed on most packs when it starts with 0)
    const m = raw.trim().replace(/^\](d2|Q3|C1)/, "").match(/^(?:\(01\)|01)(\d{14})/) || raw.match(/\/01\/(\d{8,14})/);
    if (!m) return raw.trim();
    const g = m[1].padStart(14, "0");
    return g.startsWith("0") ? g.slice(1) : g;
  };
  $("#bcNew").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#bcAdd").click(); } });
  const appendCode = (code) => $("#bcList").insertAdjacentHTML("beforeend", `<li><bdi dir="ltr">${esc(code)}</bdi>
    ${id ? `<button type="button" class="btn small secondary" data-rm="${esc(code)}">إزالة</button>` : ""}</li>`);
  $("#bcAdd").addEventListener("click", () => {
    const input = $("#bcNew"), code = readCode(input.value);
    if (!code) return;
    if (id && !modal) perform(async () => {
      await api(`/api/items/${id}/barcode`, { method: "POST", body: { barcode: code } });
      return viewItemEdit(id);
    });
    else {
      if (!$(`[data-rm="${CSS.escape(code)}"]`) && !newCodes.has(code)) {
        if (!removedCodes.delete(code)) newCodes.add(code);
        appendCode(code);
      }
      input.value = "";
    }
  });
  $("#bcList").addEventListener("click", (e) => {
    const button = e.target.closest("[data-rm]");
    if (!button) return;
    const barcode = button.dataset.rm;
    if (modal) {
      if (!newCodes.delete(barcode)) removedCodes.add(barcode);
      button.closest("li").remove();
    } else if (confirmInline(button)) perform(async () => {
      await api(`/api/items/${id}/barcode`, { method: "POST", body: { barcode, remove: true } });
      await viewItemEdit(id);
    });
  });
  $("#packFixBtn")?.addEventListener("click", async (e) => {
    const button = e.currentTarget;
    if (await withBusy(button, () => packFix(id, null, it.pack_size === 1)) && button.isConnected) viewItemEdit(id);
  });
  const form = $("#itemForm");
  const readFields = () => {
    const f = form, v = (n) => f[n] && !f[n].disabled ? f[n].value.trim() : undefined;
    const pack = +(v("pack_size") ?? it.pack_size) || 1;
    const data = { name_en: v("name_en") || null, name_ar: v("name_ar") || null, manufacturer: v("manufacturer") || null,
      active_ingredient: v("active_ingredient") || null, code: v("code") || null, location: v("location") || null,
      strength: v("strength") || null, dosage_form: f.dosage_form.value || null, quick_pin: f.quick_pin.checked,
      pack_unit_name: v("pack_unit_name") || "علبة", base_unit_name: v("base_unit_name") || "وحدة",
      min_stock: v("min_packs") ? +v("min_packs") * pack : 0, max_stock: v("max_packs") ? +v("max_packs") * pack : null,
      rx_required: f.rx_required.checked, misuse_risk: f.misuse_risk.checked, sale_note: v("sale_note") || null,
      is_service: f.is_service.checked, hide_on_receipt: f.hide_on_receipt.checked, ...(f.hidden ? { hidden: f.hidden.checked } : {}) };
    if (data.is_service && !id && ["شريط", "وحدة"].includes(data.base_unit_name)) { data.base_unit_name = "مرة"; data.pack_unit_name = "خدمة"; }
    if (v("pack_size") !== undefined) data.pack_size = pack;
    if (v("public_price") !== undefined) data.public_price = toPiasters(v("public_price"));
    return data;
  };
  const original = readFields();
  let category = it.category_id || null;
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const data = readFields();
    if (modal) {
      for (const key of Object.keys(data)) if (data[key] === original[key]) delete data[key];
    }
    if (id && data.public_price === it.public_price) delete data.public_price;
    if (id && data.pack_size === it.pack_size) delete data.pack_size;
    if (id && (data.strength || "") === (it.strength || it.strength_norm || "")) delete data.strength;     // untouched: don't rewrite
    if (id && (data.dosage_form || "") === (it.dosage_form || "")) delete data.dosage_form;
    perform(async () => {
      let iid = id, saved = it;
      if (id && (!modal || Object.keys(data).length)) {
        saved = await api(`/api/items/${id}`, { method: "PATCH", body: data });
        if (modal) {
          Object.assign(original, data);
          const { barcodes, ...fields } = saved;
          Object.assign(it, fields);
          saved = it;
        }
      } else if (!id) {
        try { iid = (await api("/api/items", { method: "POST", body: { item: data, barcodes: [...newCodes] } })).id; }
        catch (err) {
          if (err.code !== "possible_duplicate") throw err;
          const d = (err.details?.duplicates || []).map((x) => x.name || x.name_en).join("، ");
          if (!confirm(`يوجد صنف مشابه: ${d}\nهل أنت متأكد أنه صنف مختلف؟`)) return;
          iid = (await api("/api/items", { method: "POST", body: { item: data, barcodes: [...newCodes], force: true } })).id;
        }
      }
      const cat = form.category_id.value || null;
      if (cat !== category) {
        await api(`/api/items/${iid}/category`, { method: "POST", body: { category_id: cat } });
        category = cat;
      }
      if (modal) {
        const changeBarcode = (barcode, remove) => api(`/api/items/${iid}/barcode`, {
          method: "POST", body: { barcode, remove },
        });
        for (const barcode of newCodes) {
          await changeBarcode(barcode, false);
          newCodes.delete(barcode);
        }
        for (const barcode of removedCodes) {
          await changeBarcode(barcode, true);
          removedCodes.delete(barcode);
        }
      }
      toast("تم الحفظ");
      if (modal) {
        Object.assign(it, saved, { category_id: category });
        await modal.onSave(it);
      } else location.hash = `#/items/${iid}`;
    });
  });
}

// ------------------------------------------------------------------ sections (أقسام)
// المجموعات العلاجية: الصاحب بيحدد إيه بديل لإيه (مفيش تخمين)
async function viewGroups() {
  const can = ME.permissions.includes("items.groups");
  const [{ groups }, ing] = await Promise.all([api("/api/therapeutic-groups"), api("/api/ingredients")]);
  app.innerHTML = `<section class="panel"><div class="row between"><div><a href="#/items">← الأصناف</a><h1>المجموعات العلاجية (البدائل)</h1></div>
      ${can ? `<button class="btn" id="gNew">+ مجموعة جديدة</button>` : ""}</div>
    <p class="muted">كل مجموعة تضم مواد فعالة أو أصنافًا تعتبرها بدائل لبعضها (مثال: Omeprazole وEsomeprazole وPantoprazole).
      إذا كانت مادة الصنف في مجموعة، يعرض البرنامج بقية أعضائها بدائلَ في شاشة البيع وفي بطاقة الصنف. ولا يخمّن البرنامج أي بديل من تلقاء نفسه.</p>
    ${ing.without_ingredient ? `<p class="badge b-warn">⚠ ${ing.without_ingredient} صنف دواء بلا مادة فعالة مكتوبة، لذلك لن يظهر لها مثيل ولا بديل بالمادة. أضِفها من "تعديل الصنف".</p>` : ""}
    <div id="gForm"></div></section>
  <section class="panel"><div class="tablewrap"><table><thead><tr><th>المجموعة</th><th>المواد والأصناف</th><th>الحالة</th>${can ? "<th></th>" : ""}</tr></thead><tbody>
    ${groups.map((g, i) => `<tr><td><b>${esc(g.name)}</b>${g.note ? `<div class="muted">${esc(g.note)}</div>` : ""}</td>
      <td>${g.members.map((m) => m.kind === "ingredient" ? `<span class="chip"><bdi dir="ltr">${esc(m.label)}</bdi> <span class="muted">(${m.items} صنف)</span></span>`
        : `<span class="chip">📦 ${itemHtml({ name_en: m.name_en, name_ar: m.name_ar })}</span>`).join(" ")}</td>
      <td>${g.is_active ? `<span class="badge b-ok">✓ مفعّلة</span>` : `<span class="badge">متوقفة</span>`}</td>
      ${can ? `<td><button class="btn small secondary" data-ge="${i}">تعديل</button> <button class="btn small danger" data-gd="${esc(g.id)}">حذف</button></td>` : ""}</tr>`).join("")
      || `<tr><td colspan="4" class="muted">لا توجد مجموعات بعد</td></tr>`}</tbody></table></div></section>`;
  const form = (g) => {
    let mem = (g?.members || []).map((m) => ({ kind: m.kind, ref: m.ref, label: m.kind === "ingredient" ? m.label : (m.name_en || m.name_ar) }));
    const draw = () => {
      $("#gForm").innerHTML = `<form class="panel mt" id="gF"><h2>${g ? "تعديل مجموعة" : "مجموعة جديدة"}</h2>
        <div class="formgrid"><label class="f">اسم المجموعة <input name="name" value="${esc(g?.name || "")}" placeholder="مثال: مثبطات مضخة البروتون"></label>
          <label class="f">ملاحظة <input name="note" value="${esc(g?.note || "")}"></label>
          <label class="chk"><input type="checkbox" name="active" ${!g || g.is_active ? "checked" : ""}> مفعّلة</label></div>
        <div class="row mt"><label class="f grow">أضف مادة فعالة <input id="gIng" dir="ltr" list="gIngL" autocomplete="off" placeholder="Omeprazole"></label>
          <datalist id="gIngL">${ing.ingredients.map((x) => `<option value="${esc(x.label)}">${x.items} صنف</option>`).join("")}</datalist>
          <button type="button" class="btn small secondary" id="gIngAdd">أضف المادة</button></div>
        <div class="row"><label class="f grow">أو أضف صنفًا بعينه <input id="gItem" autocomplete="off" placeholder="اسم الصنف"></label></div><div id="gItemRes" class="cands"></div>
        <div class="row tags mt">${mem.map((m, i) => `<span class="chip">${m.kind === "item" ? "📦 " : ""}<bdi dir="ltr">${esc(m.label)}</bdi> <button type="button" class="x" data-mr="${i}" aria-label="إزالة">✕</button></span>`).join("") || `<span class="muted">أضف مادتين أو صنفين على الأقل</span>`}</div>
        <div class="row mt"><button class="btn">احفظ</button> <button type="button" class="btn secondary" id="gCancel">إلغاء</button></div></form>`;
      const f = $("#gF");
      const addIng = () => { const v = $("#gIng").value.trim(); if (!v) return; keep(); mem.push({ kind: "ingredient", label: v }); draw(); $("#gIng").focus(); };
      let vals = null;
      const keep = () => { vals = { name: f.name.value, note: f.note.value, active: f.active.checked }; g = { ...(g || {}), name: vals.name, note: vals.note, is_active: vals.active ? 1 : 0, members: g?.members || [] }; };
      $("#gIngAdd").addEventListener("click", addIng);
      $("#gIng").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addIng(); } });
      $("#gCancel").addEventListener("click", () => { $("#gForm").innerHTML = ""; });
      f.querySelectorAll("[data-mr]").forEach((b) => b.addEventListener("click", () => { keep(); mem.splice(+b.dataset.mr, 1); draw(); }));
      let t;
      $("#gItem").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(async () => {
        const v = e.target.value.trim(); if (v.length < 2) { $("#gItemRes").innerHTML = ""; return; }
        const r = (await findItems(v, 8)).items;
        $("#gItemRes").innerHTML = r.map((x, i) => `<button type="button" class="btn small secondary" data-gi="${i}">${itemHtml(x)}</button>`).join("") || `<p class="muted">لا توجد نتائج</p>`;
        $("#gItemRes").querySelectorAll("[data-gi]").forEach((b) => b.addEventListener("click", () => {
          const x = r[+b.dataset.gi]; keep(); if (!mem.some((m) => m.kind === "item" && m.ref === x.id)) mem.push({ kind: "item", ref: x.id, label: x.name_en || x.name_ar }); draw(); }));
      }, 250); });
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        const body = { name: f.name.value, note: f.note.value || null, is_active: f.active.checked,
          members: mem.map((m) => (m.kind === "item" ? { kind: "item", ref: m.ref } : { kind: "ingredient", label: m.label })) };
        try { await api(g?.id ? `/api/therapeutic-groups/${g.id}` : "/api/therapeutic-groups", { method: g?.id ? "PUT" : "POST", body }); toast("✓ تم الحفظ"); viewGroups(); }
        catch (err) { toast(err.message, true); }
      });
    };
    draw();
  };
  $("#gNew")?.addEventListener("click", () => form(null));
  document.querySelectorAll("[data-ge]").forEach((b) => b.addEventListener("click", () => form(groups[+b.dataset.ge])));
  document.querySelectorAll("[data-gd]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirmInline(b)) return;
    try { await api(`/api/therapeutic-groups/${b.dataset.gd}`, { method: "DELETE" }); toast("تم الحذف"); viewGroups(); }
    catch (err) { toast(err.message, true); }
  }));
}

async function viewCategories() {
  const cats = await loadCats(true), owner = ME.permissions.includes("items.no_return");
  app.innerHTML = `<section class="panel"><div class="row between"><div><a href="#/items">← الأصناف</a><h1>الأقسام</h1></div></div>
    <p class="muted">الأقسام تنظّم الأصناف. القسم الذي "لا يُقبل إرجاعه" لا يستطيع أحد إرجاع أصنافه إلا المالك (أو من منحه المالك هذه الصلاحية). تظهر العلامة بجوار الصنف في الفواتير.</p>
    <table><thead><tr><th>القسم</th><th>عدد الأصناف</th><th>مرتجع</th><th>علامة في الفاتورة</th><th>مفعّل</th><th></th></tr></thead>
    <tbody>${cats.map((c) => `<tr data-id="${esc(c.id)}"><td><input name="name" value="${esc(c.name)}" ${canW() ? "" : "disabled"}></td><td>${c.items}</td>
      <td><select name="no_return" ${owner ? "" : "disabled"}><option value="0" ${!c.no_return ? "selected" : ""}>✓ يُقبل إرجاعه</option><option value="1" ${c.no_return ? "selected" : ""}>✕ لا يُقبل إرجاعه</option></select></td>
      <td><select name="mark" ${canW() ? "" : "disabled"}><option value="">بدون</option><option value="fridge" ${c.mark === "fridge" ? "selected" : ""}>❄ ثلاجة</option><option value="injection" ${c.mark === "injection" ? "selected" : ""}>💉 حقن</option></select></td>
      <td><input type="checkbox" name="is_active" ${c.is_active ? "checked" : ""} ${canW() ? "" : "disabled"}></td>
      <td>${canW() ? `<button class="btn small secondary" data-save>احفظ</button>` : ""}</td></tr>`).join("")}</tbody></table>
    ${owner ? "" : `<p class="muted">تغيير "يُقبل إرجاعه / لا يُقبل إرجاعه" للمالك فقط.</p>`}</section>
    ${canW() ? `<form class="panel row" id="catNew"><label class="f">قسم جديد <input name="name" required></label><button class="btn">أضف</button></form>
    <section class="panel"><h2>توزيع الأصناف التي بدون قسم</h2>
      <p class="muted">يقترح البرنامج قسمًا لكل صنف من كلمات اسمه وشكله الدوائي (مثال: AMP / VIAL ← حقن، INSULIN ← ثلاجة، SHAMPOO ← عناية). <b>هذا اقتراح فقط:</b> راجع كل مجموعة وأزِل العلامة عن الخطأ، ثم "انقلها إلى القسم". ما ليس واضحًا يبقى بدون قسم.</p>
      <button class="btn secondary" id="sugGo">اقتراح</button><div id="sugBox" class="mt"></div></section>` : ""}`;
  document.querySelectorAll("[data-save]").forEach((b) => b.addEventListener("click", async () => {
    const tr = b.closest("tr"), g = (n) => tr.querySelector(`[name=${n}]`);
    try {
      await api(`/api/categories/${tr.dataset.id}`, { method: "PATCH", body: { name: g("name").value, no_return: g("no_return").value === "1",
        mark: g("mark").value || null, is_active: g("is_active").checked } });
      toast("تم الحفظ"); viewCategories();
    } catch (err) { toast(err.message, true); }
  }));
  $("#sugGo")?.addEventListener("click", async () => {
    $("#sugBox").innerHTML = `<p class="muted">جارٍ الحساب…</p>`;
    try {
      const r = await api("/api/categories/suggest");
      $("#sugBox").innerHTML = `${r.groups.map((g, gi) => `<details class="panel" data-g="${gi}"><summary><b>${esc(g.category)}</b> ${markChip(g.mark)} — ${g.count} صنف${g.no_return ? ` <span class="badge b-warn">لا يُقبل إرجاعه</span>` : ""}</summary>
          <p class="row"><label class="chk"><input type="checkbox" data-all="${gi}" checked> اختر الكل</label>
            <button class="btn small" data-apply="${gi}">انقلها إلى القسم</button></p>
          <ul class="plain cols">${g.items.map((it) => `<li><label class="chk"><input type="checkbox" data-it="${gi}" value="${esc(it.id)}" checked> ${itemHtml(it)}</label></li>`).join("")}</ul>
          ${g.count > g.items.length ? `<p class="muted">يظهر أول ${g.items.length} من ${g.count}. إذا كان خيار "اختر الكل" محددًا، ستُنقل البقية معها؛ أزِل علامة "اختر الكل" لنقل المحدد فقط.</p>` : ""}</details>`).join("")
        || `<p class="muted">لا توجد اقتراحات</p>`}<p class="muted">${r.unmatched} صنف غير واضح من اسمه — يبقى بدون قسم (يمكنك توزيعها من شاشة الأصناف باستخدام التصفية).</p>`;
      document.querySelectorAll("[data-all]").forEach((c) => c.addEventListener("change", () => document.querySelectorAll(`[data-it="${c.dataset.all}"]`).forEach((x) => { x.checked = c.checked; })));
      document.querySelectorAll("[data-apply]").forEach((b) => b.addEventListener("click", async () => {
        const gi = b.dataset.apply, g = r.groups[gi], all = document.querySelector(`[data-all="${gi}"]`).checked;
        const off = new Set([...document.querySelectorAll(`[data-it="${gi}"]:not(:checked)`)].map((x) => x.value));
        const ids = all ? g.ids.filter((x) => !off.has(x)) : [...document.querySelectorAll(`[data-it="${gi}"]:checked`)].map((x) => x.value);
        if (!ids.length) { toast("لا توجد أصناف محددة", true); return; }
        b.disabled = true;
        try {
          for (let i = 0; i < ids.length; i += 500) {
            b.textContent = `جارٍ النقل… ${Math.min(i + 500, ids.length)} / ${ids.length}`;
            await api("/api/categories/assign", { method: "POST", body: { item_ids: ids.slice(i, i + 500), category_id: g.category_id } });
          }
          toast(`✓ ${ids.length} صنف نُقلت إلى ${g.category}`); viewCategories();
        } catch (err) { b.disabled = false; b.textContent = "انقلها إلى القسم"; toast(err.message, true); }
      }));
    } catch (err) { $("#sugBox").innerHTML = ""; toast(err.message, true); }
  });
  $("#catNew")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/categories", { method: "POST", body: { name: e.target.name.value } }); viewCategories(); }
    catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ data import
const ISSUE_AR = {
  missing_name: "بدون اسم", invalid_barcode: "باركود غير صحيح", invalid_public_price: "سعر غير صحيح",
  invalid_cost: "تكلفة غير صحيحة", invalid_qty: "كمية غير صحيحة", invalid_expiry: "صلاحية غير مفهومة",
  invalid_pack_size: "عدد وحدات غير صحيح", duplicate_code_in_file: "كود مكرر في الملف",
  duplicate_barcode_in_file: "باركود مكرر في الملف", duplicate_item_in_file: "صنف مكرر في الملف",
  same_item_exists: "الصنف موجود بالفعل", similar_item_exists: "صنف مشابه موجود",
  code_barcode_conflict: "الكود والباركود لصنفين مختلفين", price_missing: "بدون سعر",
  missing_item: "الصنف غير موجود — استورد الأصناف أولًا", missing_qty: "بدون كمية", zero_qty: "رصيد صفر",
  item_has_history: "الصنف له حركات سابقة", no_batch_or_expiry: "بدون تشغيلة/صلاحية (سيُسجَّل بدونها)",
  cost_missing: "بدون تكلفة", matched_by_name_only: "طوبق بالاسم فقط — أكّد",
  price_below_cost: "سعر البيع أقل من التكلفة", party_has_history: "له حركات سابقة — قد يتكرر الرصيد الافتتاحي",
  same_name_exists: "عميل بنفس الاسم موجود", duplicate_in_file: "مكرر في الملف", invalid_phone: "رقم هاتف غير صحيح",
  invalid_balance: "رصيد غير صحيح", invalid_credit_limit: "حد ائتمان غير صحيح",
  unknown_is_imported: "محلي/مستورد غير مفهوم", unknown_is_drug: "دواء/غير دواء غير مفهوم",
};
const ACTION_AR = { create: "إضافة", update: "تحديث", link: "ربط", skip: "تجاهل", review: "مراجعة", error: "خطأ" };

function viewImport() {
  app.innerHTML = `
  <section class="panel">
    <h1>نقل البيانات من برنامج آخر</h1>
    <p class="muted">الخطوات: رفع الملف ← تقرير مسبق (بدون أي تعديل) ← مراجعة ← تطبيق مرة واحدة. الترتيب: الأصناف ← الأرصدة الافتتاحية ← الموردون ← العملاء.</p>
    <p>قوالب جاهزة: <a href="/api/imports/templates/items.xlsx">الأصناف</a> · <a href="/api/imports/templates/opening_stock.xlsx">الأرصدة</a> ·
      <a href="/api/imports/templates/suppliers.xlsx">الموردون</a> · <a href="/api/imports/templates/customers.xlsx">العملاء</a></p>
    <form id="impForm" class="row">
      <label class="f">النوع <select name="kind"><option value="items">الأصناف</option>
        <option value="opening_stock">الأرصدة الافتتاحية</option><option value="suppliers">الموردين وأرصدتهم</option>
        <option value="customers">العملاء وأرصدتهم</option></select></label>
      <label class="f">الكمية في الملف بـ <select name="qty_unit"><option value="pack">العلبة</option>
        <option value="base">الوحدة الصغرى (شريط)</option></select></label>
      <label class="f">البرنامج القديم <select name="source_system"><option value="estock">E-Stock</option>
        <option value="generic">Excel عام</option></select></label>
      <label class="f">الملف (Excel / CSV) <input type="file" name="file" required accept=".xlsx,.csv"></label>
      <button class="btn" type="submit">تحليل الملف</button>
    </form>
  </section>`;
  $("#impForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/imports", { method: "POST", form: new FormData(e.target) });
      location.hash = `#/import/${r.id}`;
    } catch (err) {
      toast(err.message, true);
      if (err.code === "missing_columns") {
        const c = err.details.columns;
        app.insertAdjacentHTML("beforeend", `<section class="panel"><h2>أعمدة ناقصة</h2>
          <p>إلزامي ناقص: <b>${c.missing_required.map(esc).join("، ")}</b></p>
          <p class="muted">أعمدة غير معروفة في الملف: ${c.unmapped_headers.map(esc).join("، ") || "—"}</p></section>`);
      }
      if (err.code === "mapping_required") {
        app.insertAdjacentHTML("beforeend", `<section class="panel"><h2>أول أسطر في الملف</h2><div class="tablewrap"><table>
          ${err.details.preview.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</table></div>
          <p class="muted">أسماء الأعمدة غير معروفة. أرسل هذا الملف إلى المطوّر ليضيف هذه الأسماء.</p></section>`);
      }
    }
  });
}

async function viewImportRun(id) {
  const r = await api(`/api/imports/${encodeURIComponent(id)}`);
  const c = r.report.counts || {};
  const open = r.status === "analyzed";
  const attention = r.rows.filter((x) => ["review", "error"].includes(x.action));
  app.innerHTML = `
  <section class="panel">
    <div class="row between"><h1>${({ items: "استيراد أصناف", opening_stock: "أرصدة افتتاحية", suppliers: "استيراد موردين", customers: "استيراد عملاء" })[r.kind]} — ${esc(r.filename)}</h1>
      <span class="badge ${open ? "b-warn" : "b-ok"}">${open ? "تقرير مسبق (لم يُطبّق)" : ({ applied: "تم التطبيق", discarded: "ملغاة" }[r.status] || esc(r.status))}</span></div>
    <div class="totals">
      ${Object.entries(ACTION_AR).map(([k, v]) => `<div>${v}<b>${c[k] || 0}</b></div>`).join("")}
      ${r.kind === "opening_stock" ? `<div>إجمالي الكميات في الملف<b>${esc(r.report.source_qty_total)}</b></div>
        <div>قيمة المخزون في الملف<b>${esc(r.report.source_value_total)}</b></div>` : ""}
    </div>
    <h2 class="mt">المشكلات المكتشفة</h2>
    <ul class="issues">${Object.entries(r.report.issues || {}).map(([k, n]) => `<li class="warn">${esc(ISSUE_AR[k] || k)}: ${n}</li>`).join("") || "<li>لا يوجد</li>"}</ul>
    ${r.report.columns ? `<p class="muted">أعمدة مقترح إضافتها: ${r.report.columns.missing_recommended.map(esc).join("، ") || "—"}
      · أعمدة تم تجاهلها: ${r.report.columns.unmapped_headers.map(esc).join("، ") || "—"}${r.report.profile_used ? " · تم استخدام ربط أعمدة محفوظ" : ""}</p>` : ""}
    ${r.report.applied ? `<p class="badge b-ok">تم التطبيق: ${esc(JSON.stringify(r.report.applied))}</p>` : ""}
    ${r.report.reconciliation ? `<p>المطابقة بعد النقل: ${r.report.reconciliation.ok ? "✅ مطابقة" : "⛔ غير مطابقة"}</p>` : ""}
    <div class="row">${open ? `<button class="btn" id="impApply">تطبيق الاستيراد</button>
      <button class="btn secondary" id="impDiscard">إلغاء</button>` : ""}</div>
  </section>
  <section class="panel"><h2>أسطر تحتاج إلى قرار (${attention.length})</h2><div class="tablewrap"><table>
    <thead><tr><th>سطر</th><th>البيانات</th><th>الحالة</th><th>المشكلة</th><th>القرار</th></tr></thead>
    <tbody>${attention.map((x) => `<tr><td>${x.row_no}</td>
      <td>${esc([x.data.code, x.data.name_en || x.data.name_ar, x.data.qty].filter(Boolean).join(" · "))}</td>
      <td>${ACTION_AR[x.action]}</td>
      <td>${x.issues.map((i) => esc(ISSUE_AR[i.code] || i.code) + (i.label ? `: ${esc(i.label)}` : "")).join("<br>")}</td>
      <td>${open ? `<select data-dec="${esc(x.id)}">
        <option value="">${x.decision ? esc(x.decision) : "— اختر —"}</option>
        <option value="skip">تجاهل السطر</option>
        ${x.action === "review" ? `<option value="create">${r.kind === "items" ? "إضافة كصنف جديد" : "تسجيل الرصيد"}</option>` : ""}
        ${x.target_id && r.kind === "items" ? `<option value="link:${esc(x.target_id)}">ربط بالصنف الموجود</option>` : ""}
      </select>` : esc(x.decision || "")}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">لا يوجد</td></tr>`}</tbody>
  </table></div></section>`;
  app.querySelectorAll("[data-dec]").forEach((s) => s.addEventListener("change", async () => {
    if (!s.value) return;
    try { await api(`/api/imports/${id}/decide`, { method: "POST", body: { row_id: s.dataset.dec, decision: s.value } }); toast("تم الحفظ"); }
    catch (e) { toast(e.message, true); }
  }));
  $("#impApply")?.addEventListener("click", async () => {
    if (!confirm("تطبيق الاستيراد؟ العملية تتم مرة واحدة بالكامل أو لا شيء.")) return;
    try { await api(`/api/imports/${id}/apply`, { method: "POST" }); toast("تم الاستيراد بنجاح"); viewImportRun(id); }
    catch (e) { toast(e.message, true); }
  });
  $("#impDiscard")?.addEventListener("click", async () => {
    await api(`/api/imports/${id}/discard`, { method: "POST" }).catch((e) => toast(e.message, true));
    location.hash = "#/import";
  });
}

// ------------------------------------------------------------------ recalls
async function viewRecalls() {
  const alerts = await api("/api/recalls/alerts");
  app.innerHTML = `
  <section class="panel">
    <h1>تنبيهات سحب هيئة الدواء</h1>
    <p class="muted">الصق نص المنشور، وراجع أرقام التشغيلات المقترحة قبل الحفظ. يقارنها البرنامج بالتشغيلات المسجلة لديك من فواتير الشراء، ولا يحرّك المخزون من تلقاء نفسه.</p>
    <form id="rcForm">
      <div class="row">
        <label class="f grow">عنوان المنشور <input name="title" required></label>
        <label class="f">رقم المنشور <input name="notice_no"></label>
        <label class="f">التاريخ <input name="notice_date" type="date"></label>
      </div>
      <label class="f">اسم المستحضر <input name="product_name" required></label>
      <label class="f">نص المنشور (اختياري — لاستخراج التشغيلات) <textarea name="raw_text" rows="3"></textarea></label>
      <div class="row"><button class="btn secondary" type="button" id="rcExtract">استخراج التشغيلات</button></div>
      <label class="f">أرقام التشغيلات (افصل بينها بمسافة أو فاصلة) <input name="batches" required></label>
      <button class="btn" type="submit">حفظ ومطابقة</button>
    </form>
  </section>
  <section class="panel"><h2>تشغيلات مطابقة تحتاج إلى قرار (${alerts.length})</h2><div class="tablewrap"><table>
    <thead><tr><th>الصنف لديك</th><th>التشغيلة</th><th>الكمية في المخزون</th><th>المنشور</th><th>أساس المطابقة</th><th>القرار</th></tr></thead>
    <tbody>${alerts.map((a) => `<tr class="${a.qty_in_stock > 0 ? "has-err" : ""}">
      <td>${esc(a.item_label)}</td><td>${esc(a.batch_no)}</td><td>${a.qty_in_stock}</td>
      <td>${esc(a.title)}${a.notice_no ? ` (${esc(a.notice_no)})` : ""}<div class="raw">${esc(a.product_name)}</div></td>
      <td>${a.match_basis === "batch+name" ? `<span class="badge b-err">تشغيلة + اسم</span>` : `<span class="badge b-warn">رقم التشغيلة فقط — تأكد</span>`}</td>
      <td><button class="btn small" data-rc="${esc(a.id)}" data-st="confirmed">مؤكد — سحب الكمية</button>
        <button class="btn small secondary" data-rc="${esc(a.id)}" data-st="not_ours">ليست نفس المستحضر</button></td></tr>`).join("")
      || `<tr><td colspan="6" class="muted">لا توجد تشغيلات مطابقة</td></tr>`}</tbody></table></div></section>`;
  $("#rcExtract").addEventListener("click", async () => {
    const f = $("#rcForm");
    const r = await api("/api/recalls/extract", { method: "POST", body: { text: f.raw_text.value } });
    f.batches.value = r.batches.join(" ");
    toast(r.batches.length ? "راجع الأرقام المقترحة قبل الحفظ" : "لم يتم العثور على أرقام تشغيلات", !r.batches.length);
  });
  $("#rcForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/api/recalls", { method: "POST", body: { title: f.title.value, notice_no: f.notice_no.value || null,
        notice_date: f.notice_date.value || null, raw_text: f.raw_text.value || null,
        items: [{ product_name: f.product_name.value, batch_numbers: f.batches.value.split(/[\s,،]+/).filter(Boolean) }] } });
      toast("تم الحفظ والمطابقة"); viewRecalls();
    } catch (err) { toast(err.message, true); }
  });
  app.querySelectorAll("[data-rc]").forEach((b) => b.addEventListener("click", async () => {
    const note = b.dataset.st === "not_ours" ? prompt("سبب أنها ليست نفس المستحضر:") : null;
    if (b.dataset.st === "not_ours" && !note) return;
    try { await api(`/api/recalls/alerts/${b.dataset.rc}`, { method: "POST", body: { status: b.dataset.st, note } }); viewRecalls(); }
    catch (err) { toast(err.message, true); }
  }));
}

// ------------------------------------------------------------------ inventory count
async function viewCounts() {
  const list = await api("/api/counts");
  const canManage = ME.permissions.includes("inventory.adjust");
  app.innerHTML = `
  <section class="panel">
    <h1>الجرد</h1>
    ${canManage ? `<form id="cntForm" class="row">
      <label class="f grow">اسم الجرد <input name="title" required placeholder="مثال: جرد رف A"></label>
      <label class="f">النطاق <select name="scope"><option value="full">شامل</option><option value="category">قسم واحد (جرد جزئي)</option><option value="location">مواقع محددة</option></select></label>
      <label class="f">القسم <select name="cat"><option value="">—</option>${(await loadCats()).map((c) => `<option value="${esc(c.id)}">${esc(c.name)} (${c.items})</option>`).join("")}</select></label>
      <label class="f">المواقع (إذا كان النطاق مواقع) <input name="locations" placeholder="A1, A2"></label>
      <button class="btn" type="submit">بدء جرد</button></form>` : ""}
    ${canManage ? `<div class="row mt"><button class="btn secondary" id="cntDaily">جرد يومي صغير (10 أصناف)</button>
      <span class="muted">البرنامج بيختار أصناف قيمتها عالية أو بتتباع كتير أو كان فيها فرق قبل كده. البيع شغال عادي أثناء الجرد: الكمية المتوقعة بتتحسب بعد اللي اتباع.</span></div>
      <div id="cntPick" class="muted small"></div>` : ""}
  </section>
  <section class="panel"><table><thead><tr><th>الجرد</th><th>النطاق</th><th>الحالة</th><th>التاريخ</th></tr></thead>
    <tbody>${list.map((c) => `<tr><td><a href="#/counts/${esc(c.id)}">${esc(c.title)}</a></td><td>${{ full: "شامل", location: "مواقع", items: "أصناف / قسم" }[c.scope] || esc(c.scope)}</td>
      <td>${{ open: "مفتوح", approved: "معتمد", cancelled: "ملغي" }[c.status]}</td><td>${esc(dayOf(c.created_at))}</td></tr>`).join("")
      || `<tr><td colspan="4" class="muted">لا يوجد</td></tr>`}</tbody></table></section>`;
  if (canManage) api("/api/counts/daily-pick").then(({ items }) => {
    if ($("#cntPick")) $("#cntPick").innerHTML = items.length ? `هيتجرد: ${items.map((x) => `${esc(x.label)} (${esc(x.why)})`).join("، ")}` : "مفيش أصناف محتاجة جرد النهارده";
  }).catch(() => {});
  $("#cntDaily")?.addEventListener("click", async () => {
    try { const r = await api("/api/counts", { method: "POST", body: { daily: true } }); location.hash = `#/counts/${r.id}`; }
    catch (err) { toast(err.message, true); }
  });
  $("#cntForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      const r = await api("/api/counts", { method: "POST", body: { title: f.title.value, scope: f.scope.value, category_id: f.cat.value || null,
        locations: f.locations.value.split(/[,،]/).map((x) => x.trim()).filter(Boolean) } });
      location.hash = `#/counts/${r.id}`;
    } catch (err) { toast(err.message, true); }
  });
}

let COUNT_ITEM = null;

async function viewCountSession(id) {
  const canManage = ME.permissions.includes("inventory.adjust");
  setTimeout(() => api(`/api/counts/${id}/items`).then(({ items }) => {     // the items this count covers
    if ($("#cntScope") && items.length) $("#cntScope").textContent = "الأصناف المطلوب جردها: " + items.map((x) => x.name + (x.location ? ` (${x.location})` : "")).join("، ");
  }).catch(() => {}), 0);
  app.innerHTML = `
  <section class="panel">
    <div class="row between"><h1>تسجيل الجرد</h1><a href="#/counts">رجوع</a></div>
    <p class="muted">امسح الباركود بالقارئ أو اكتب الاسم. الكميات بالوحدة الصغرى (شريط/وحدة).</p>
    <div class="row">
      <label class="f grow">باركود أو اسم الصنف <input id="cntQ" autocomplete="off" autofocus></label>
      ${"BarcodeDetector" in window ? `<button class="btn secondary" id="cntCam" type="button">كاميرا</button>` : ""}
    </div>
    <div id="cntScope" class="muted small"></div>
    <video id="cntVideo" playsinline hidden class="scanvideo"></video>
    <div id="cntPick"></div>
    <div id="cntItem" class="mt"></div>
  </section>
  ${canManage ? `<section class="panel"><div class="row between"><h2>الفروق</h2>
    <button class="btn secondary" id="cntVar">تحديث الفروق</button></div><div id="cntVarBox" class="muted">اضغط «تحديث الفروق» لعرضها</div></section>` : ""}`;
  const q = $("#cntQ");
  const chooseItem = (it) => {
    COUNT_ITEM = it;
    $("#cntPick").innerHTML = "";
    $("#cntItem").innerHTML = `<div class="panel"><b>${esc(it.name_en || it.name_ar)}</b>
      <div class="muted">${esc(it.strength_norm || "")} ${esc(it.dosage_form || "")} — العبوة ${it.pack_size} ${esc(unitName(it))} — موقع ${esc(it.location || "—")}</div>
      <div class="row mt"><label class="f">الكمية <input id="cntQty" type="number" min="0" step="1" inputmode="numeric"></label>
      <label class="f">الموقع <input id="cntLoc" value="${esc(it.location || "")}"></label>
      <label class="f"><span>إضافة على السابق</span><input id="cntAdd" type="checkbox"></label>
      <button class="btn" id="cntSave">حفظ</button></div></div>`;
    $("#cntQty").focus();
    $("#cntSave").addEventListener("click", () => save(false));
    $("#cntQty").addEventListener("keydown", (e) => { if (e.key === "Enter") save(false); });
  };
  const save = async (recount) => {
    const qty = typedWhole($("#cntQty").value);
    if (qty === null || Number.isNaN(Number(qty)) || Number(qty) < 0) return toast("اكتب كمية صحيحة", true);
    try {
      await api(`/api/counts/${id}/entries`, { method: "POST", body: { item_id: COUNT_ITEM.id, qty,
        location: $("#cntLoc").value, mode: $("#cntAdd").checked ? "add" : "set", recount } });
      toast(`تم: ${COUNT_ITEM.name_en || COUNT_ITEM.name_ar} = ${qty}`);
      $("#cntItem").innerHTML = ""; q.value = ""; q.focus();
    } catch (err) {
      if (err.code === "already_counted" && confirm(`سبق جرد الصنف بكمية ${err.details.qty}. تسجيل جرد جديد بدلًا منه؟`)) return save(true);
      toast(err.message, true);
    }
  };
  const lookup = async () => {
    const v = q.value.trim();
    if (!v) return;
    const found = await findItems(v), res = found.items;
    if (res.length === 1 && !found.hint) return chooseItem(res[0]);
    if (!res.length) return toast("الصنف غير موجود", true);
    $("#cntPick").innerHTML = hintHtml(found.hint) + `<div class="cands">${res.map((r, i) => `<button class="btn secondary small" data-pk="${i}">${itemHtml(r)}</button>`).join("")}</div>`;
    $("#cntPick").querySelectorAll("[data-pk]").forEach((b) => b.addEventListener("click", () => chooseItem(res[+b.dataset.pk])));
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); lookup(); } });
  $("#cntCam")?.addEventListener("click", async () => {
    const video = $("#cntVideo");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      video.srcObject = stream; video.hidden = false; await video.play();
      const det = new BarcodeDetector({ formats: ["ean_13", "ean_8", "upc_a", "code_128", "qr_code"] });
      const tick = async () => {
        if (video.hidden) return;
        const codes = await det.detect(video).catch(() => []);
        if (codes.length) {
          stream.getTracks().forEach((t) => t.stop()); video.hidden = true;
          q.value = codes[0].rawValue; lookup(); return;
        }
        requestAnimationFrame(tick);
      };
      tick();
    } catch { toast("تعذر فتح الكاميرا (تحتاج إلى HTTPS أو تصريح الكاميرا)", true); }
  });
  $("#cntVar")?.addEventListener("click", async () => {
    try {
      const [v, R] = await Promise.all([api(`/api/counts/${id}/variances`), stockReasons()]);
      const box = $("#cntVarBox");
      box.className = "";
      box.innerHTML = `<div class="row between"><h2>تقرير الجرد والفروق</h2>${reportTools("count")}</div><div class="totals"><div>أصناف مجرودة<b>${v.summary.counted_lines}</b></div>
        <div>عجز (وحدات)<b>${v.summary.shortage_units}</b></div><div>زيادة (وحدات)<b>${v.summary.excess_units}</b></div>
        <div>أصناف لم تُجرد ولها رصيد<b>${v.summary.uncounted_items}</b></div></div>
        <div class="tablewrap"><table><thead><tr><th>الصنف</th><th>الرصيد في البرنامج</th><th>المجرود</th><th>مباع أثناء الجرد</th><th>الفرق</th><th>القيمة</th><th></th></tr></thead>
        <tbody>${v.lines.map((l) => `<tr class="${l.difference ? "has-err" : ""}"><td>${esc(l.label)}</td><td>${l.system_qty}</td>
          <td>${l.counted}</td><td>${l.moves_after_count}</td><td>${l.difference}</td><td>${esc(l.value ?? "")}</td>
          <td>${l.problem ? `<span class="badge b-err">${l.problem === "mixed_levels" ? "جرد بالتشغيلة وبدونها معًا" : l.problem === "adjusted_since_count" ? "اتعدّل بعد العدّ: أعد عدّه" : l.problem === "moved_between_counts" ? "اتباع بين عدّتين: أعد عدّه" : "رصيد سالب"}</span>`
            : l.difference < 0 ? `<select data-why="${esc(l.item_id)}|${esc(l.batch_key || "")}" class="noprint"><option value="">السبب: مثل الافتراضي</option>${reasonOpts(R.adjust, null, false)}</select>` : l.difference > 0 ? "زيادة وُجدت" : ""}</td></tr>`).join("")}</tbody></table></div>
        <div class="row mt noprint"><label class="f">سبب العجز (الافتراضي لكل الأصناف) <select id="cntWhy">${reasonOpts(R.adjust, "lost", false)}</select></label>
          ${v.summary.uncounted_items ? `<label class="f">الأصناف التي لم تُجرد (${v.summary.uncounted_items}) <select id="cntZero"><option value="">اتركها كما هي</option>${reasonOpts(R.adjust, null, false).replace(/<option value="/g, '<option value="zero:')}</select></label>` : ""}
          <button class="btn" id="cntApprove">اعتماد التسويات</button></div>`;
      wireTools("count", () => ({ title: "تقرير الجرد", subtitle: `عجز ${v.summary.shortage_units} وحدة · زيادة ${v.summary.excess_units} وحدة · لم يُجرد ${v.summary.uncounted_items} صنف`, el: box, sheets: [
        { name: "الجرد", columns: [Tx("الصنف"), N("الرصيد في البرنامج"), N("المجرود"), N("مباع أثناء الجرد"), N("الفرق"), { label: "القيمة (ج)", type: "num" }, Tx("ملاحظة")],
          rows: v.lines.map((l) => [l.label, l.system_qty, l.counted, l.moves_after_count, l.difference, l.value ?? "", l.problem || ""]) }] }));
      $("#cntApprove").addEventListener("click", async () => {
        const reasons = {};
        box.querySelectorAll("[data-why]").forEach((sel) => { if (sel.value) reasons[sel.dataset.why] = sel.value; });
        const z = $("#cntZero")?.value || "", zero = z.startsWith("zero:");
        let reason = null;
        if (zero) { reason = prompt(`سيُصفَّر ${v.summary.uncounted_items} صنف لم يُجرد. اكتب ملاحظة (5 حروف على الأقل):`); if (!reason) return; }
        if (!confirm("اعتماد الجرد وتسجيل التسويات في المخزون؟")) return;
        try {
          await api(`/api/counts/${id}/approve`, { method: "POST", body: { zero_uncounted: zero, reason, reasons, shortage_reason: $("#cntWhy").value, zero_reason: zero ? z.slice(5) : "lost",
            uncounted_item_ids: zero ? v.uncounted_with_stock.map((u) => u.item_id) : undefined } });
          toast("تم اعتماد الجرد"); location.hash = "#/counts";
        } catch (err) { toast(err.message, true); }
      });
    } catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ stock reasons (أسباب تغيير الرصيد)
let STOCK_REASONS = null;
async function stockReasons() { return (STOCK_REASONS ||= await api("/api/stock/reasons")); }
// options for a reason <select>; up=true shows only reasons that can add stock, false only the ones that remove
function reasonOpts(map, selected, up) {
  return Object.entries(map).filter(([k]) => up == null || (up ? ["found", "entry_error", "other"].includes(k) : k !== "found"))
    .map(([k, t]) => `<option value="${k}" ${k === selected ? "selected" : ""}>${esc(t)}</option>`).join("");
}

// ------------------------------------------------------------------ program / update
// U4-0: the printing / label / drawer / card machine / screen settings from an older copy of the data
// (a renamed data folder after a clean start, or a backup file). Nothing else is copied.
async function renderDeviceRestore() {
  const box = $("#dvBox");
  if (!box) return;
  let src = [];
  try { src = (await api("/api/settings/device-restore/sources")).sources; } catch (e) { box.innerHTML = `<p class="badge b-err">✕ ${esc(e.message)}</p>`; return; }
  if (!src.length) { box.innerHTML = `<p class="muted">مفيش نسخة قديمة على الجهاز ده (لا فولدر بيانات قديم ولا نسخة احتياطية).</p>`; return; }
  box.innerHTML = `<p class="muted">لو الإعدادات رجعت للأصل (بعد بداية من الصفر مثلًا): اختار النسخة القديمة، والبرنامج يوريك الإعدادات اللي هيرجعها قبل ما يغيّر أي حاجة. مفيش أي بيع أو مخزون أو عملاء أو مستخدمين أو كلمات سر بيتنقلوا.</p>
    <label class="f">النسخة القديمة <select id="dvSrc">${src.map((r) => `<option value="${esc(r.id)}">${r.kind === "folder" ? "فولدر بيانات قديم" : "نسخة احتياطية"}: ${esc(r.name)} — ${esc(stamp(r.when))}</option>`).join("")}</select></label>
    <button type="button" class="btn small secondary" id="dvLook">اعرض الإعدادات اللي فيها</button>
    <div id="dvPrev" class="mt"></div>`;
  // the restore always uses the copy that was previewed; another choice clears the preview (Codex P2 on #430)
  $("#dvSrc").addEventListener("change", () => { $("#dvPrev").innerHTML = ""; });
  $("#dvLook").addEventListener("click", async () => {
    const b = $("#dvLook"); b.disabled = true; const out = $("#dvPrev"); out.innerHTML = `<p class="muted">جارٍ قراءة النسخة… (النسخة الاحتياطية الكبيرة بتاخد دقيقة)</p>`;
    try {
      const p = await api("/api/settings/device-restore/preview", { method: "POST", body: { source: $("#dvSrc").value } });
      out.innerHTML = `<p>الصيدلية في النسخة دي: <b>${esc(p.pharmacy)}</b></p>
        ${p.groups.map((g) => `<fieldset class="lblp"><legend><label class="chk"><input type="checkbox" data-dvg="${esc(g.key)}" ${g.default ? "checked" : ""} ${g.available ? "" : "disabled"}> ${esc(g.title)}</label></legend>
          ${!g.available ? `<p class="muted">مش موجودة في النسخة دي</p>` : g.same ? `<p class="muted">زي اللي في البرنامج دلوقتي</p>` : ""}
          ${g.lines.length ? `<ul class="small">${g.lines.map((l) => `<li dir="auto">${esc(l)}</li>`).join("")}</ul>` : ""}</fieldset>`).join("")}
        <p class="muted">${esc(p.note)}</p>
        <button type="button" class="btn" id="dvGo">رجّع الإعدادات المختارة</button>`;
      $("#dvGo").addEventListener("click", async () => {
        const groups = [...out.querySelectorAll("[data-dvg]:checked")].map((x) => x.dataset.dvg);
        if (!groups.length) { toast("اختار الإعدادات اللي عايز ترجعها", true); return; }
        if (!confirm("هترجع الإعدادات المختارة من النسخة القديمة مكان الإعدادات الحالية. متأكد؟")) return;
        const g = $("#dvGo"); g.disabled = true;
        try {
          await api("/api/settings/device-restore/apply", { method: "POST", body: { source: p.source.id, groups } });
          toast("✓ رجعت الإعدادات. اطبع ملصق تجربة واتأكد إنه طالع على ملصق واحد");
          renderReceiptCfg(await api("/api/settings/receipt"));
          renderChannels((await api("/api/payment-channels")).channels);
          if (groups.includes("screen")) { ME.ui = await api("/api/settings/ui"); applyNav(); }   // Codex P2 on #430
          out.innerHTML = `<p class="badge b-ok">✓ رجعت الإعدادات المختارة (متسجلة في سجل العمليات)</p>`;
        } catch (e) { g.disabled = false; toast(e.message, true); }
      });
    } catch (e) { out.innerHTML = `<p class="badge b-err">✕ ${esc(e.message)}</p>`; } finally { b.disabled = false; }
  });
}
async function viewEstockMove() {
  if (!isOwnerMe()) {
    app.innerHTML = `<section class="panel"><h1>نقل من إي ستوك</h1><p>الشاشة دي للمالك بس.</p></section>`;
    return;
  }
  app.innerHTML = `<section class="panel" id="estockMove">
    <h1>نقل من إي ستوك</h1>
    <p>ابدأ من الكمبيوتر اللي عليه إي ستوك. البرنامج هيقرا البيانات ويجرّب النقل على نسخة، وبعدها يعرض تقرير المطابقة.</p>
    <p class="muted">يفضّل توقف التسجيل في إي ستوك وقت القراية عشان التقرير يعكس البيانات في نفس الوقت.</p>
    <p id="estockMoveStatus" role="status"></p>
    <progress aria-label="تقدّم تجربة النقل" hidden></progress>
    <div class="row"><button class="btn" type="button" hidden></button><a class="btn secondary" href="#/system">ارجع للبرنامج</a></div>
    <pre id="estockMoveReport" dir="rtl" data-raw hidden></pre>
  </section>`;
  const panel = $("#estockMove"), button = panel.querySelector("button"), bar = panel.querySelector("progress");
  const status = $("#estockMoveStatus"), report = $("#estockMoveReport");
  const path = "/api/estock/move";
  const isCurrent = () => panel.isConnected && location.hash === "#/estock-move";
  let state, lastMessage;

  async function refresh() {
    if (!isCurrent()) return;
    try {
      const next = await api(path);
      if (!isCurrent()) return;
      state = next;
      const labels = {
        idle: "جاهز للبحث عن إي ستوك.",
        finding: "بندور على قاعدة إي ستوك على الجهاز…",
        reading: `لقينا قاعدة إي ستوك. بنقرا البيانات… الجداول اللي اتقرت: ${next.tables}`,
        rehearsing: "القراية خلصت. بنجرّب النقل على نسخة وبنجهّز تقرير المطابقة…",
        interrupted: "التجربة وقفت. اضغط «كمّل التجربة» عشان تتابع.",
        failed: next.message,
        done: next.reconciled ? "التجربة خلصت والمطابقة نجحت. راجع التقرير." : "التجربة خلصت. راجع الملاحظات والفروق في التقرير.",
      };
      const actions = { idle: "ابدأ تجربة النقل", failed: "جرّب تاني", interrupted: "كمّل التجربة", done: "اعمل تجربة جديدة" };
      const working = ["finding", "reading", "rehearsing"].includes(next.phase);
      const message = next.supported ? labels[next.phase] : "تجربة نقل إي ستوك متاحة على ويندوز ١٠ و١١.";
      if (lastMessage !== message) status.textContent = lastMessage = message;
      bar.hidden = !working;
      button.hidden = !next.supported || working;
      button.textContent = actions[next.phase] || "";
      report.hidden = next.phase !== "done";
      if (next.report) {
        const content = document.createElement("span");
        content.textContent = next.report;
        report.replaceChildren(content);
      } else report.replaceChildren();
      if (working) setTimeout(refresh, 1000);
    } catch (err) {
      if (!isCurrent()) return;
      status.textContent = err.message;
      lastMessage = undefined;
      // A running job can outlive a brief disconnect or browser reload.
      if (!err.code) setTimeout(refresh, 2000);
      else { bar.hidden = true; button.hidden = true; report.hidden = true; report.textContent = ""; }
    }
  }
  button.addEventListener("click", () => withBusy(button, async () => {
    try {
      await api(path, { method: "POST", body: state.phase === "done" ? { replace_id: state.id } : {} });
      await refresh();
    } catch (err) {
      if (isCurrent()) toast(err.message, true);
    }
  }));
  await refresh();
}

async function viewSystem() {
  if (!ME.permissions.includes("settings.manage")) { app.innerHTML = `<section class="panel"><p>هذه الصفحة للمالك فقط.</p></section>`; return; }
  app.innerHTML = `<section class="panel"><h1>النسخ الاحتياطي</h1><div id="bkBox"><p class="muted">…</p></div></section>
    <section class="panel"><h1>الهاتف المحمول على شبكة الصيدلية</h1><div id="lanBox"></div></section>
    <section class="panel"><h1>شكل الفاتورة المطبوعة والملصقات</h1><div id="rcBox"></div></section>
    <section class="panel" id="dvPanel"><h1>رجّع إعدادات الأجهزة والطباعة من نسخة قديمة</h1><div id="dvBox"><p class="muted">…</p></div></section>
    <section class="panel"><h1>مساحة البيانات ومدة الاحتفاظ بها</h1><div id="stoBox"><p class="muted">جارٍ الحساب…</p></div></section>
    <section class="panel"><h1>أجهزة البطاقات وطرق التحويل</h1><div id="chBox"></div></section>
    <section class="panel"><h1>البرنامج</h1>
      ${isOwnerMe() ? `<p><a class="btn secondary" href="#/estock-move">نقل من إي ستوك</a></p>` : ""}
      <div id="sysBox"><p class="muted">جارٍ البحث عن تحديثات…</p></div></section>`;
  renderBackup(await api("/api/backup"));
  renderChannels((await api("/api/payment-channels")).channels);
  renderReceiptCfg(await api("/api/settings/receipt"));
  renderDeviceRestore();
  renderLan(await api("/api/system/network"));
  const st = await api("/api/system/update");
  $("#sysBox").innerHTML = `
    <div class="totals"><div>الإصدار<b>${esc(st.version)}</b></div><div>النسخة المثبتة<b>${st.installed ? esc(st.installed.slice(0, 7)) : "غير معروفة"}</b></div>
      <div>آخر نسخة<b>${esc((st.latest || "—").slice(0, 7))}</b></div>
      <div>قناة التحديث<b>${st.channel === "develop" ? "develop (تجربة)" : st.channel === "main" ? "main (الصيدلية)" : `لا توجد (فرع ${esc(st.candidate || "?")})`}</b></div></div>
    ${st.parties_reactivated && (st.parties_reactivated.customers || st.parties_reactivated.suppliers) ? `<p class="badge b-ok">✓ بعد التحديث رجع يظهر في البحث ${st.parties_reactivated.customers} عميل و${st.parties_reactivated.suppliers} مورد كانوا مستخبيين من نقل إي ستوك</p>` : ""}
    ${updatePanelHtml(st)}`;
  bindSmartUpdate();
  api("/api/system/storage").then((x) => {
    const mb = (n) => `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0)} ميجا`;
    $("#stoBox").innerHTML = `<div class="totals"><div>قاعدة البيانات<b>${mb(x.db)}</b><span>${x.since ? `تحتوي على مبيعات منذ ${esc(x.since)}` : ""}</span></div>
      <div>صور الفواتير والتحويلات<b>${mb(x.uploads)}</b></div><div>النسخ الاحتياطية<b>${mb(x.backups)}</b></div></div>
      <p class="mt"><b>ما لا يُحذف أبدًا:</b> فواتير البيع والشراء، وحسابات العملاء والموردين، والخزينة والمصروفات، وحركة المخزون، وسجل العمليات. يُلزم القانون بحفظ الدفاتر سنوات، وحجمها صغير (تزيد الصيدلية النشطة نحو 20–40 ميجا في السنة).</p>
      <p><b>ما يُنظَّف تلقائيًا كل يوم:</b> الإشعارات الأقدم من ${x.policy.notifications_days} يومًا، وجلسات الدخول المنتهية بعد ${x.policy.sessions_days} يومًا، والنسخ الاحتياطية التلقائية الزائدة (تبقى آخر 10 نسخ + نسخة لكل يوم لمدة أسبوع + نسخة لكل أسبوع لمدة شهر).</p>
      <p class="muted">النسخ الاحتياطية مضغوطة (تشغل نحو عُشر حجم قاعدة البيانات). إذا قلّت المساحة يومًا، يمكن عمل أرشيف مضغوط للسنوات القديمة — وهذا يحتاج إلى قرارك.</p>
      <p class="muted">عدد الفواتير: ${x.rows.sales} · سطور البيع: ${x.rows.sale_lines} · فواتير الشراء: ${x.rows.purchase_invoices} · حركات المخزون: ${x.rows.stock_movements}</p>`;
  }).catch((e) => { $("#stoBox").innerHTML = `<p class="badge b-err">✕ ${esc(e.message)}</p>`; });
}
function updatePanelHtml(st) {
  const signed = st.smart || {};
  return smartLastHtml(signed) + (smartUpdateHtml(signed) || (signed.error
    ? `<p class="badge b-err">${esc(signed.error)}</p>`
    : signed.enabled ? '<p class="badge b-ok">البرنامج محدّث</p>'
    : '<p class="badge b-warn">مفيش تحديث موقّع متاح للجهاز ده</p>'));
}
function smartLastHtml(u) {
  const res = u.result || {};
  return res.state === "rolled_back" ? `<p class="badge b-warn">آخر تحديث ما اشتغلش، فالبرنامج رجع لوحده للنسخة اللي قبله وبياناتك زي ما هي.</p>`
    : res.state === "aborted" ? `<p class="badge b-warn">آخر تحديث اتلغى قبل ما يغيّر حاجة: ${esc(res.reason || "")}</p>` : "";
}
function smartUpdateHtml(u) {
  const last = "";
  if (u.request) {
    return `${last}<p class="badge b-ok">✓ التحديث جاهز ومستني <b>${u.request.when === "after_close" ? "قفل الوردية" : "أول دقيقة هادية"}</b></p>
      ${u.waiting ? `<p class="muted">مستني: ${esc(u.waiting)}</p>` : ""}
      <p class="muted">البرنامج هيقفل ويفتح لوحده (دقيقة تقريبًا)، ومش هيحصل وانت بتبيع.</p>
      <button class="btn secondary" id="updCancel">إلغاء الميعاد</button>`;
  }
  if (!u.enabled || !u.available) return last;
  const notes = (u.notes || []).length ? `<ul>${u.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : `<p class="muted">تحسينات وتصليحات صغيرة.</p>`;
  if (u.needs_installer) return `${last}<p>فيه تحديث محتاج المثبّت الجديد (نسخة بايثون جديدة).</p>${notes}`;
  return `${last}<p><b>فيه تحديث جديد، اللي اتغيّر:</b></p>${notes}
    ${u.database ? `<p class="muted">فيه تعديل على قاعدة البيانات: هيتجرّب الأول على نسخة منها، وبياناتك الأصلية مش بتتلمس إلا لو كل الفحوص نجحت.</p>` : ""}
    <p class="muted">بياخد نسخة احتياطية الأول. لو أي حاجة فشلت، البرنامج بيرجع لوحده للنسخة القديمة.</p>
    <div class="row"><button class="btn" id="updNow">حدّث دلوقتي</button>
      <button class="btn secondary" id="updClose">حدّث بعد قفل الوردية</button></div>`;
}
function bindSmartUpdate() {
  const schedule = async (when, b) => {
    const label = b.textContent; b.disabled = true; b.textContent = "جارٍ تنزيل التحديث وفحصه… (البرنامج شغال عادي)";
    try {
      await api("/api/system/update", { method: "POST", body: { action: "prepare" } });
      await api("/api/system/update", { method: "POST", body: { action: "schedule", when } });
      viewSystem();
    } catch (e) { b.disabled = false; b.textContent = label; toast(e.message, true); }
  };
  $("#updNow")?.addEventListener("click", (e) => schedule("now", e.target));
  $("#updClose")?.addEventListener("click", (e) => schedule("after_close", e.target));
  $("#updCancel")?.addEventListener("click", async () => {
    try { await api("/api/system/update", { method: "POST", body: { action: "cancel" } }); viewSystem(); } catch (e) { toast(e.message, true); }
  });
}
function renderLan(n) {
  $("#lanBox").innerHTML = `
    <p>عند تفعيلها، يستطيع أي هاتف متصل بشبكة الصيدلية نفسها فتح البرنامج والبيع أو الجرد، ويُسجَّل كل شيء على البيانات نفسها الموجودة على الكمبيوتر — دون إنترنت ودون خادم.</p>
    <p class="badge ${n.lan ? "b-ok" : "b-plain"}">${n.lan ? "✓ مفعّلة" : "متوقفة (الافتراضي)"}</p>
    ${n.lan && n.addresses.length ? `<p class="mt">افتح المتصفح على الهاتف واكتب:</p>${n.addresses.map((a) => `<p class="lan-url" dir="ltr">${esc(a)}</p>`).join("")}
      <p class="muted">إذا تغيّر عنوان الكمبيوتر (بعد إعادة تشغيل الراوتر) ستجد العنوان الجديد هنا.</p>` : ""}
    <ul class="muted">
      <li>في أول مرة سيسألك ويندوز عن السماح للبرنامج على الشبكة: اختر "Private networks" ثم اضغط Allow.</li>
      <li>اجعل كلمة سر الشبكة قوية، ولا تفعّلها على شبكة مفتوحة للعملاء.</li>
      <li>لن تمسح كاميرا الهاتف الباركود عبر الشبكة (يمنعها المتصفح دون HTTPS)؛ اكتب الاسم أو استخدم قارئ باركود بلوتوث.</li>
      <li>الدخول السريع بالرقم السري على كمبيوتر الصيدلية فقط؛ ويدخل الهاتف باسم المستخدم وكلمة المرور.</li></ul>
    <button class="btn ${n.lan ? "secondary" : ""}" id="lanGo">${n.lan ? "أوقفها" : "فعّلها"}</button>
    ${!n.launcher ? `<p class="muted">يسري التغيير بعد إغلاق البرنامج وفتحه مرة أخرى.</p>` : ""}`;
  $("#lanGo").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const r = await api("/api/system/network", { method: "PUT", body: { lan: !n.lan } });
      if (r.restarting) {
        e.target.textContent = "يعيد البرنامج تشغيل نفسه…";
        const wait = async () => { try { await fetch("/api/health"); location.reload(); } catch { setTimeout(wait, 1500); } };
        setTimeout(wait, 4000);
      } else renderLan(await api("/api/system/network"));
    } catch (err) { e.target.disabled = false; toast(err.message, true); }
  });
}
let RC_LOGO;                         // F27: undefined = keep, "" = remove, data URL = new logo
// shrink a picked logo in the browser (≤ 576×240, white behind transparency) so the upload stays small
function logoDataUrl(file) {
  return new Promise((ok, fail) => {
    const url = URL.createObjectURL(file), img = new Image();
    img.onload = () => {
      const k = Math.min(1, 576 / img.width, 240 / img.height);
      const cv = document.createElement("canvas");
      cv.width = Math.max(1, Math.round(img.width * k)); cv.height = Math.max(1, Math.round(img.height * k));
      const g = cv.getContext("2d"); g.fillStyle = "#fff"; g.fillRect(0, 0, cv.width, cv.height);
      g.drawImage(img, 0, 0, cv.width, cv.height); URL.revokeObjectURL(url); ok(cv.toDataURL("image/png"));
    };
    img.onerror = () => { URL.revokeObjectURL(url); fail(new Error("bad image")); };
    img.src = url;
  });
}
// the logo as the thermal printer will print it: grey above 160 → white, else black (same rule as the server)
function printLook(dataUrl) {
  return new Promise((ok) => {
    const img = new Image();
    img.onload = () => {
      const cv = document.createElement("canvas"); cv.width = img.width; cv.height = img.height;
      const g = cv.getContext("2d"); g.drawImage(img, 0, 0);
      const d = g.getImageData(0, 0, cv.width, cv.height), p = d.data;
      for (let i = 0; i < p.length; i += 4) {
        const v = (p[i] * 299 + p[i + 1] * 587 + p[i + 2] * 114) / 1000 > 160 ? 255 : 0;
        p[i] = p[i + 1] = p[i + 2] = v; p[i + 3] = 255;
      }
      g.putImageData(d, 0, 0); ok(cv.toDataURL("image/png"));
    };
    img.onerror = () => ok(dataUrl);
    img.src = dataUrl;
  });
}
async function renderReceiptCfg(c) {
  const order = (await api("/api/settings/sales")).line_order;
  const rstep = ME.permissions.includes("settings.manage") ? (await api("/api/settings/rounding")).step : 0;
  const pr = await api("/api/print/printers").catch(() => ({ printers: [], shaping: true }));
  const chk = (k, t) => `<label class="chk"><input type="checkbox" name="${k}" ${c[k] ? "checked" : ""}> ${t}</label>`;
  $("#rcBox").innerHTML = `<form id="rcForm" class="formgrid">
    <label class="f">مقاس ورق الإيصال <select name="paper"><option value="80" ${c.paper === 80 ? "selected" : ""}>80 مم (الافتراضي)</option><option value="58" ${c.paper === 58 ? "selected" : ""}>58 مم</option></select></label>
    <label class="f">قالب الإيصال <select name="template">${[["detailed", "مفصّل"], ["compact", "مختصر"], ["large", "خط كبير"]].map(([v, label]) => `<option value="${v}" ${(c.template || "detailed") === v ? "selected" : ""}>${label}</option>`).join("")}</select></label>
    <label class="f">مقدمة الفاتورة (اختياري) <input name="header" value="${esc(c.header || "")}" placeholder="مثال: يوجد خدمة توصيل للمنازل"></label>
    <label class="f">اسم الصيدلية على الفاتورة (فارغ = الاسم المسجل) <input name="title" value="${esc(c.title)}"></label>
    <label class="f">سطر 1 (العنوان) <input name="l0" value="${esc(c.lines[0] || "")}"></label>
    <label class="f">سطر 2 (الهاتف) <input name="l1" value="${esc(c.lines[1] || "")}"></label>
    <label class="f">آخر سطر <input name="footer" value="${esc(c.footer)}"></label>
    <label class="f">رقم التسجيل الضريبي (اختياري) <input name="tax_no" dir="ltr" value="${esc(c.tax_no || "")}"></label>
    <label class="f">رقم السجل التجاري (اختياري) <input name="cr_no" dir="ltr" value="${esc(c.cr_no || "")}"></label>
    <label class="f">شكل الوقت (في كل الشاشات والإيصال) <select name="time_format"><option value="12" ${c.time_format !== "24" ? "selected" : ""}>12 ساعة (10:06 م) — الافتراضي</option><option value="24" ${c.time_format === "24" ? "selected" : ""}>24 ساعة (22:06)</option></select></label>
    <fieldset class="lblp"><legend>الإيصال والدرج عند إتمام البيع</legend>
      <p class="muted">الافتراضي: بدون إيصال، والبائع يختار «🧾 إيصال» (F4) بجانب زر الإتمام. فاتورة التوصيل تطبع إيصالًا واحدًا تلقائيًا.</p>
      ${chk("auto_receipt", "اختيار الإيصال تلقائيًا حسب قيمة الفاتورة")}
      <label class="f">من مبلغ (ج) — صفر = كل الفواتير <input name="auto_receipt_min" type="number" min="0" step="0.01" value="${money(c.auto_receipt_min || 0)}"></label>
      <label class="f">عدد النسخ (الطباعة المباشرة) <select name="copies">${[1, 2, 3].map((n) => `<option value="${n}" ${c.copies === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      ${chk("drawer_on_cash", "افتح الدرج عند تحصيل نقدي (حتى بدون إيصال)")}
      <label class="f">توقيت فتح الدرج مع الإيصال <select name="drawer_timing"><option value="before" ${c.drawer_timing === "before" ? "selected" : ""}>قبل الإيصال</option><option value="with" ${c.drawer_timing === "with" ? "selected" : ""}>مع الإيصال</option><option value="after" ${(c.drawer_timing || "after") === "after" ? "selected" : ""}>بعد الإيصال (الافتراضي)</option></select></label>
      <p class="muted">الدرج يُفتح فقط مع الطابعة المباشرة (ESC/POS). مع «browser» لا يمكن فتح الدرج إطلاقًا، والشاشة تقول ذلك للكاشير.</p></fieldset>
    <label class="f">أسماء الأصناف <select name="names"><option value="en" ${c.names === "en" ? "selected" : ""}>إنجليزي</option><option value="ar" ${c.names === "ar" ? "selected" : ""}>عربي</option><option value="both" ${c.names === "both" ? "selected" : ""}>الاثنان</option></select></label>
    <label class="f">ترتيب الأصناف في الفاتورة (على الشاشة والحفظ والطباعة) <select name="line_order"><option value="alpha" ${order === "alpha" ? "selected" : ""}>أبجدي A→Z (الافتراضي — أسرع في التحضير)</option><option value="entry" ${order === "entry" ? "selected" : ""}>كما أُدخلت</option></select></label>
    <label class="f">تقريب إجمالي الفاتورة (لأقرب) <select name="rounding">${[[0, "من غير تقريب"], [25, "ربع جنيه"], [50, "نص جنيه"], [100, "جنيه"]].map(([v, t]) => `<option value="${v}" ${rstep === v ? "selected" : ""}>${t}</option>`).join("")}</select></label>
    <fieldset class="lblp"><legend>طابعة الإيصال والدرج</legend>
      <label class="f">الطابعة <input name="receipt_target" list="prnList" dir="ltr" value="${esc(c.receipt_target || "")}" placeholder="${pr.env !== "none" ? esc(pr.env) : "browser"}"></label>
      <datalist id="prnList"><option value="browser" label="من المتصفح (بدون طباعة مباشرة)">${pr.printers.map((n) => `<option value="win:${esc(n)}">`).join("")}</datalist>
      <p class="muted">اختر طابعة الإيصال من القائمة (طابعات ويندوز على هذا الجهاز) أو اكتب tcp:IP:9100 لطابعة شبكة. مع الطباعة المباشرة يُفتح الدرج تلقائيًا مع أي تحصيل نقدي (حسب الإعداد فوق). browser = الإيصال يُطبع من المتصفح والدرج لا يُفتح.${pr.env !== "none" ? ` فارغ = إعداد الجهاز القديم (${esc(pr.env)}).` : " فارغ = من المتصفح."}</p>
      ${pr.shaping ? "" : `<p><b>⚠ تشكيل الحروف العربية (raqm) غير متاح على هذا الجهاز: سيُطبع الإيصال من المتصفح، ولن يُفتح الدرج إلا إذا اختيرت الطابعة.</b></p>`}
      <div class="row"><button type="button" class="btn small secondary" id="rcTest">اطبع صفحة تجربة</button><button type="button" class="btn small secondary" id="rcKick">افتح الدرج تجربة</button></div></fieldset>
    <fieldset class="lblp"><legend>شكل الملصق</legend>
      <label class="f">الباركود على الملصق <select name="label_barcode_source">
        <option value="internal" ${c.label_barcode_source === "internal" ? "selected" : ""}>كود الصنف</option>
        <option value="international" ${c.label_barcode_source === "international" ? "selected" : ""}>الكود الدولي</option></select></label>
      <label class="f">المقاس <select name="label_preset">${["38x25", "50x30", "58x40"].map((v) => `<option value="${v}" ${c.label_size === v ? "selected" : ""}>${v.replace("x", " × ")} مم</option>`).join("")}<option value="custom" ${["38x25", "50x30", "58x40"].includes(c.label_size) ? "" : "selected"}>مقاس آخر (اكتبه)</option></select></label>
      <div class="row" id="lbCustom"><label class="f">العرض (مم) <input name="label_w" type="number" min="20" max="110" step="0.1" value="${esc(c.label_size.split("x")[0])}"></label>
        <label class="f">الارتفاع (مم) <input name="label_h" type="number" min="10" max="80" step="0.1" value="${esc(c.label_size.split("x")[1])}"></label></div>
      <p class="muted">قِس ملصقًا واحدًا من البكرة (دون المسافة بينه وبين الملصق التالي). تُضبط المسافة بين الملصقات في «طابعة الملصقات» أدناه.</p>
      <div>${[["pharmacy", "اسم الصيدلية"], ["phone", "الهاتف"], ["name", "اسم الصنف"], ["barcode", "الباركود"], ["digits", "أرقام الباركود"], ["code", "كود الصنف الداخلي"], ["expiry", "الصلاحية (أقرب تشغيلة)"], ["price", "السعر"]]
        .map(([k, t]) => `<label class="chk"><input type="checkbox" data-lf="${k}" ${c.label_fields[k] ? "checked" : ""}> ${t}</label>`).join("")}</div>
      <label class="f">اسم الصيدلية على الملصق (فارغ = اسمها في الإيصال) <input name="label_pharmacy" value="${esc(c.label_pharmacy || "")}"></label>
      <label class="f">الهاتف على الملصق (فارغ = سطر الهاتف في الإيصال) <input name="label_phone" dir="ltr" value="${esc(c.label_phone || "")}"></label>
      <label class="f">شبكة ورق A4 (أعمدة × صفوف) <input name="label_a4" dir="ltr" value="${esc(c.label_a4 || "6x24")}" placeholder="6x24"></label>
      <p class="muted">إذا كان المقاس أصغر من الحقول المختارة، يُحذف الهاتف أولًا ثم اسم الصيدلية؛ ولا يصغر الباركود عن الحد المقروء.</p>
      <div class="lbprev"><img id="lbPrev" alt="معاينة الملصق"><p id="lbPrevMsg" class="muted small"></p></div></fieldset>
    <fieldset class="lblp"><legend>طابعة الملصقات</legend>
      <label class="f">النوع <select name="label_lang"><option value="browser" ${c.label_lang === "browser" ? "selected" : ""}>من المتصفح (أي طابعة)</option><option value="a4" ${c.label_lang === "a4" ? "selected" : ""}>من المتصفح على ورق A4 (شبكة)</option>
        <option value="driver" ${c.label_lang === "driver" ? "selected" : ""}>عن طريق تعريف الطابعة في ويندوز (مثل إي ستوك) — الأنسب</option>
        <option value="tspl" ${c.label_lang === "tspl" ? "selected" : ""}>Xprinter / TSC (TSPL)</option><option value="zpl" ${c.label_lang === "zpl" ? "selected" : ""}>Zebra (ZPL)</option></select></label>
      <label class="f">مكان الطابعة <input name="label_target" list="prnList" dir="ltr" value="${esc(c.label_target || "")}" placeholder="win:Xprinter XP-365B  أو  tcp:192.168.1.50:9100"></label>
      <label class="chk lbDrv"><input type="checkbox" name="label_flip" ${c.label_flip ? "checked" : ""}> اقلب الملصق (إذا طُبع مقلوبًا)</label>
      <label class="f">لغة بيانات الملصق <select name="label_text_lang"><option value="en" ${(c.label_text_lang || "en") === "en" ? "selected" : ""}>إنجليزي</option><option value="ar" ${c.label_text_lang === "ar" ? "selected" : ""}>عربي</option></select></label>
      <label class="f">اسم الصيدلية بالإنجليزي (للملصق) <input name="label_pharmacy_en" dir="ltr" maxlength="40" value="${esc(c.label_pharmacy_en || "")}" placeholder="Abdul Qawi Pharmacy"></label>
      <label class="f lbDrv">عدد الملصقات في صفحة ويندوز (إذا كانت الصفحة تضم ملصقين فوق بعضهما فاختر 2) <select name="label_rows">${[1, 2, 3, 4].map((n) => `<option value="${n}" ${(c.label_rows || 1) === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      <p class="muted lbDrv">يطبع البرنامج عن طريق تعريف الطابعة في ويندوز، تمامًا مثل إي ستوك: يُؤخذ مقاس الملصق والمسافة بين الملصقات والاتجاه من «تفضيلات الطباعة» للطابعة في ويندوز (وهي نفسها التي يطبع بها إي ستوك). ولا يُستخدم المقاس أعلاه إلا عند الطباعة من المتصفح.</p>
      <label class="f lbRaw">دقة الطابعة <select name="label_dpi"><option value="203" ${c.label_dpi === 203 ? "selected" : ""}>203 (الأشهر)</option><option value="300" ${c.label_dpi === 300 ? "selected" : ""}>300</option></select></label>
      <label class="f">المسافة بين الملصقات (مم) <input name="label_gap" type="number" min="0" max="10" step="1" value="${c.label_gap ?? 2}"></label>
      <label class="f lbRaw">تحريك الطباعة لأعلى/لأسفل (مم): سالب = لأعلى، موجب = لأسفل <input name="label_shift" type="number" min="-10" max="10" step="0.5" value="${c.label_shift ?? 0}"></label>
      <p class="muted lbRaw">إذا نزلت الطباعة لأسفل أو ظهر ملصق فارغ بين كل ملصقين: اكتب رقمًا سالبًا (ابدأ بـ -2)، واضغط «اطبع 3 ملصقات تجربة»، فتُطبع بالأرقام الظاهرة على الشاشة دون حفظ. عندما تخرج الثلاثة كاملة ومتتالية اضغط «احفظ».</p>
      <label class="f lbRaw">طريقة إرسال الملصق (للتجربة؛ الافتراضي current) <select name="label_variant" dir="ltr">${["current", "tear", "clamp", "tear_clamp", "per_label", "tear_clamp_per_label", "home", "all"]
        .map((v) => `<option value="${v}" ${(c.label_variant || "current") === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <div class="row"><button type="button" class="btn small secondary lbRaw" id="lbCal">معايرة بكرة الملصقات</button>
        <button type="button" class="btn small secondary" id="lbTest">اطبع 3 ملصقات تجربة</button></div>
      <p class="muted">اسم طابعة ويندوز كما يظهر في "الأجهزة والطابعات". عند أي مشكلة في الطباعة المباشرة تُفتح صفحة الطباعة من المتصفح.</p></fieldset>
    <fieldset class="lblp"><legend>اللوجو والأكواد المربعة (QR)</legend>
      <label class="f">صورة اللوجو (PNG أو JPG — الأفضل أبيض وأسود) <input type="file" id="rcLogoFile" accept="image/png,image/jpeg,image/webp"></label>
      <div class="lbprev"><img id="rcLogoPrev" alt="معاينة اللوجو" ${c.has_logo ? `src="/api/settings/receipt/logo?t=${Date.now()}"` : "hidden"}>
        <p class="muted small" id="rcLogoMsg">${c.has_logo ? "" : "لا يوجد لوجو"}</p>
        <button type="button" class="btn small secondary" id="rcLogoDel" ${c.has_logo ? "" : "hidden"}>احذف اللوجو</button></div>
      <p class="muted">في كل كود: اكتب <b>رقم الهاتف المحمول للصيدلية</b> (للواتساب — يحوّله البرنامج تلقائيًا)، أو الصق رابط صفحة الفيسبوك، أو رابط الموقع من خرائط جوجل (مشاركة ← نسخ الرابط)، أو رابط الدفع من تطبيق إنستاباي. تُكتب الكلمة التي تحت الكود تلقائيًا حسب الرابط، أو اكتبها بنفسك.</p>
      <label class="f">الكود الأول أسفل الإيصال <input name="qr_text" dir="ltr" maxlength="213" value="${esc(c.qr_text || "")}" placeholder="01xxxxxxxxx"></label>
      <label class="f">الكلمة تحته (فارغ = تلقائي) <input name="qr_caption" maxlength="40" value="${esc(c.qr_caption || "")}" placeholder="اطلب على واتساب"></label>
      <label class="f">كود ثانٍ أسفل الإيصال (اختياري) <input name="qr2_text" dir="ltr" maxlength="213" value="${esc(c.qr2_text || "")}" placeholder="https://maps.app.goo.gl/..."></label>
      <label class="f">الكلمة تحته (فارغ = تلقائي) <input name="qr2_caption" maxlength="40" value="${esc(c.qr2_caption || "")}" placeholder="موقعنا على الخريطة"></label>
      <p class="muted">تطبع الطابعة الحرارية بالأبيض والأسود فقط: يختفي الرمادي الفاتح ويصير الغامق أسود. بعد الحفظ اضغط «اطبع آخر فاتورة كتجربة» وجرّب قراءة الكود بالهاتف.</p></fieldset>
    <div>${chk("show_cashier", "اسم البائع")}${chk("show_customer", "اسم العميل")}${chk("show_expiry", "صلاحية كل صنف")}${chk("show_return_policy", "سياسة المرتجع (والأصناف التي لا يُقبل إرجاعها)")}${chk("show_item_count", "عدد الأصناف")}${chk("show_change", "المدفوع والباقي للعميل (وقت البيع)")}</div>
    <fieldset class="lblp lbprev"><legend>معاينة الإيصال</legend>
      <p id="rcPreviewMessage" class="muted" role="status"></p>
      <img id="rcPreview" hidden alt="معاينة الإيصال ببيانات تجريبية">
    </fieldset>
    <div class="row"><button class="btn">احفظ</button><a href="#" id="rcTry">اطبع آخر فاتورة كتجربة</a></div></form>`;
  LABEL_CFG = c;
  RC_LOGO = undefined;
  $("#rcLogoFile").addEventListener("change", async (e) => {
    const file = e.target.files[0]; if (!file) return;
    try { RC_LOGO = await logoDataUrl(file); } catch { return toast("تعذر قراءة الصورة", true); }
    $("#rcLogoPrev").src = await printLook(RC_LOGO); $("#rcLogoPrev").hidden = false; $("#rcLogoDel").hidden = false;
    $("#rcLogoMsg").textContent = "هذا شكله على الورق تمامًا (أبيض وأسود). إذا اختفى جزء منه أو صار أسود، فارفع نسخة أبيض وأسود من اللوجو. يُحفظ عند الضغط على «احفظ»";
    previewReceipt();
  });
  $("#rcLogoDel").addEventListener("click", () => {
    RC_LOGO = ""; $("#rcLogoPrev").hidden = true; $("#rcLogoDel").hidden = true; $("#rcLogoFile").value = "";
    $("#rcLogoMsg").textContent = "سيُحذف عند الضغط على «احفظ»";
    previewReceipt();
  });
  const lf = $("#rcForm");
  const showCustom = () => { $("#lbCustom").hidden = lf.label_preset.value !== "custom"; };
  let prevT = null, prevUrl = null;
  const previewCurrent = onThisScreen();
  const appearanceFields = ["title", "header", "footer", "tax_no", "cr_no", "time_format", "names",
    "template", "qr_text", "qr_caption", "qr2_text", "qr2_caption"];
  const appearanceChecks = ["show_cashier", "show_customer", "show_expiry", "show_return_policy",
    "show_item_count", "show_change"];
  const appearance = () => ({
    paper: +lf.paper.value, lines: [lf.l0.value, lf.l1.value, ...c.lines.slice(2)],
    ...Object.fromEntries(appearanceFields.map((key) => [key, lf.elements[key].value])),
    ...Object.fromEntries(appearanceChecks.map((key) => [key, lf.elements[key].checked])),
    ...(RC_LOGO !== undefined ? { logo: RC_LOGO } : {}),
  });
  const receiptImage = $("#rcPreview"), receiptMessage = $("#rcPreviewMessage");
  let receiptTimer, receiptRequest = 0;
  const previewReceipt = () => {
    clearTimeout(receiptTimer);
    const seq = ++receiptRequest;
    receiptTimer = setTimeout(async () => {
      const current = () => previewCurrent() && lf.isConnected && seq === receiptRequest;
      if (!current()) return;
      try {
        const res = await fetch("/api/settings/receipt", { method: "PUT",
          headers: { "Content-Type": "application/json", "X-Requested-With": "pharm" },
          body: JSON.stringify({ ...appearance(), preview: true }) });
        const result = await res.json();
        if (!res.ok) throw new Error(result.error?.message || "تعذرت المعاينة");
        if (!current()) return;
        receiptImage.src = "data:image/png;base64," + result.png;
        receiptImage.hidden = false;
        receiptMessage.textContent = "بيانات تجريبية";
      } catch (error) {
        if (!current()) return;
        receiptImage.hidden = true;
        receiptMessage.textContent = "تعذرت المعاينة" + (error.message ? " · " + error.message : "");
      }
    }, 300);
  };
  for (const key of ["paper", "l0", "l1", ...appearanceFields, ...appearanceChecks]) {
    lf.elements[key].addEventListener("input", previewReceipt);
  }
  previewReceipt();
  const preview = () => { clearTimeout(prevT); prevT = setTimeout(async () => {
    if (!previewCurrent() || !lf.isConnected) return;
    try {
      const r = await fetch("/api/print/label-preview", { method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "pharm" },
        body: JSON.stringify({ label_barcode_source: lf.label_barcode_source.value, label_size: labelSize(lf), label_fields: labelFields(lf), label_pharmacy: lf.label_pharmacy.value, label_phone: lf.label_phone.value,
          label_lang: lf.label_lang.value, label_a4: lf.label_a4.value.trim(), label_dpi: +lf.label_dpi.value, label_target: lf.label_target.value.trim(), label_rows: +lf.label_rows.value, label_gap: +lf.label_gap.value || 0, label_text_lang: lf.label_text_lang.value, label_pharmacy_en: lf.label_pharmacy_en.value.trim() }) });
      if (!r.ok) {
        const error = (await r.json()).error?.message || "تعذرت المعاينة";
        if (previewCurrent() && lf.isConnected) { $("#lbPrev").hidden = true; $("#lbPrevMsg").textContent = "⚠ " + error; }
        return;
      }
      const png = await r.blob();
      if (!previewCurrent() || !lf.isConnected) return;
      const dropped = (r.headers.get("X-Label-Dropped") || "").split(",").filter(Boolean);
      if (prevUrl) URL.revokeObjectURL(prevUrl);
      prevUrl = URL.createObjectURL(png);
      $("#lbPrev").src = prevUrl; $("#lbPrev").hidden = false;
      const fromWin = r.headers.get("X-Label-Source") === "windows" ? ` على مقاس ويندوز ${(r.headers.get("X-Label-Size") || "").replace("x", " × ")} مم` : "";
      $("#lbPrevMsg").textContent = dropped.length ? `⚠ المقاس لا يتسع: حُذف ${dropped.map((k) => ({ phone: "الهاتف", pharmacy: "اسم الصيدلية" }[k] || k)).join(" و")}${fromWin}` : `المعاينة بنفس شكل الطباعة المباشرة${fromWin} (بيانات تجربة).`;
    } catch { /* preview only */ }
  }, 300); };
  // #100: through the Windows driver, size, gap and direction come from Windows: the raw-printer fields hide
  const lbMode = () => { const drv = lf.label_lang.value === "driver";
    lf.querySelectorAll(".lbRaw").forEach((x) => { x.hidden = drv; }); lf.querySelectorAll(".lbDrv").forEach((x) => { x.hidden = !drv; }); };
  lbMode(); lf.label_lang.addEventListener("change", lbMode); lf.label_target.addEventListener("change", preview); lf.label_rows.addEventListener("change", preview); lf.label_gap.addEventListener("change", preview); lf.label_text_lang.addEventListener("change", preview); lf.label_pharmacy_en.addEventListener("change", preview);
  showCustom(); preview();
  lf.label_preset.addEventListener("change", () => { if (lf.label_preset.value !== "custom") [lf.label_w.value, lf.label_h.value] = lf.label_preset.value.split("x"); showCustom(); preview(); });
  ["label_w", "label_h", "label_pharmacy", "label_phone", "label_a4", "label_lang", "label_dpi", "label_barcode_source"].forEach((k) => lf[k].addEventListener("change", preview));
  lf.querySelectorAll("[data-lf]").forEach((x) => x.addEventListener("change", preview));
  $("#rcForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try { await api("/api/settings/sales", { method: "PUT", body: { line_order: f.line_order.value } });
      if (f.rounding && +f.rounding.value !== rstep) await api("/api/settings/rounding", { method: "PUT", body: { step: +f.rounding.value } });
      renderReceiptCfg(await api("/api/settings/receipt", { method: "PUT", body: { ...appearance(), auto_receipt: f.auto_receipt.checked,
      auto_receipt_min: toPiasters(f.auto_receipt_min.value) || 0, copies: +f.copies.value, drawer_on_cash: f.drawer_on_cash.checked, drawer_timing: f.drawer_timing.value,
      label_barcode_source: f.label_barcode_source.value, label_size: labelSize(f), label_fields: labelFields(f), label_pharmacy: f.label_pharmacy.value, label_phone: f.label_phone.value, label_a4: f.label_a4.value.trim(), label_lang: f.label_lang.value, label_target: f.label_target.value.trim(), receipt_target: f.receipt_target.value.trim(),
      label_dpi: +f.label_dpi.value, label_gap: +f.label_gap.value || 0, label_shift: +f.label_shift.value || 0, label_variant: f.label_variant.value, label_flip: f.label_flip.checked, label_rows: +f.label_rows.value, label_text_lang: f.label_text_lang.value, label_pharmacy_en: f.label_pharmacy_en.value.trim() } })); RC_LOGO = undefined; ME.time_format = f.time_format.value; toast("تم الحفظ"); }
    catch (err) { toast(err.message, true); }
  });
  $("#rcTry").addEventListener("click", async (e) => {
    e.preventDefault();
    const last = (await api("/api/sales"))[0];
    if (!last) return toast("لا توجد فواتير اليوم", true);
    printInFrame(`/receipt/${last.id}`);
  });
  $("#lbTest").addEventListener("click", async () => {
    try {
      const r = await api("/api/print/label-test", { method: "POST", body: { label_barcode_source: lf.label_barcode_source.value, label_size: labelSize(lf), label_fields: labelFields(lf), label_lang: lf.label_lang.value,
        label_target: lf.label_target.value.trim(), label_dpi: +lf.label_dpi.value, label_gap: +lf.label_gap.value || 0, label_shift: +lf.label_shift.value || 0,
        label_variant: lf.label_variant.value, label_flip: lf.label_flip.checked, label_rows: +lf.label_rows.value, label_text_lang: lf.label_text_lang.value, label_pharmacy_en: lf.label_pharmacy_en.value.trim() } });
      toast(`أُرسل ${r.labels} ملصق تجربة${r.size ? ` على مقاس ${r.size.replace("x", " × ")} مم` : ""} — إذا خرجت سليمة فاضغط «احفظ»`);
    } catch (err) { toast(err.message, true); }
  });
  $("#lbCal").addEventListener("click", async () => {
    // #100: the printer feeds a few blank labels while its gap sensor learns the roll on the screen
    try {
      await api("/api/print/label-calibrate", { method: "POST", body: { label_size: labelSize(lf), label_lang: lf.label_lang.value,
        label_target: lf.label_target.value.trim(), label_dpi: +lf.label_dpi.value, label_gap: +lf.label_gap.value || 0 } });
      toast("أُرسل أمر المعايرة — ستسحب الطابعة بضعة ملصقات فارغة ثم تتوقف تلقائيًا");
    } catch (err) { toast(err.message, true); }
  });
  $("#rcTest").addEventListener("click", async () => {
    try { await api("/api/print/test", { method: "POST", body: { target: $("#rcForm").receipt_target.value.trim() } }); toast("تم الإرسال للطابعة"); }
    catch (err) { toast(err.message, true); }
  });
  $("#rcKick").addEventListener("click", async () => {
    // the drawer opens on the SAVED printer (the one the cashier will use)
    try { await api("/api/print/drawer", { method: "POST", body: { reason: "تجربة الدرج من صفحة البرنامج" } }); toast("تم إرسال أمر فتح الدرج"); }
    catch (err) { toast(err.message, true); }
  });
}
const CH_GROUP = { card: "فيزا", transfer: "محفظة إلكترونية" };
async function renderChannels(list) {
  CHANNELS = null;
  const accs = await moneyAccounts(true), seeAcc = ME.permissions.includes("cash.see_expected");
  $("#chBox").innerHTML = `<p class="muted">الكاشير بيختار المجموعة (فيزا أو محفظة إلكترونية) وبعدين الطريقة. كل طريقة فلوسها بتنزل في حسابها، وبيتراجع كل حساب لوحده.</p>
    <table><thead><tr><th>المجموعة</th><th>الاسم</th>${seeAcc ? "<th>الفلوس بتنزل في</th>" : ""}<th>مفعّل</th><th></th></tr></thead><tbody>
    ${list.map((c) => `<tr data-id="${esc(c.id)}"><td><select name="kind">${Object.entries(CH_GROUP).map(([k, t]) => `<option value="${k}" ${c.kind === k ? "selected" : ""}>${t}</option>`).join("")}</select></td><td><input name="name" value="${esc(c.name)}"></td>
      ${seeAcc ? `<td><select name="acc"><option value="">— مش محدد —</option>${srcOptions(accs, c.account_id, { drawer: false, outside: false })}</select></td>` : ""}
      <td><input type="checkbox" name="is_active" ${c.is_active ? "checked" : ""}></td><td><button class="btn small secondary" data-chsave>احفظ</button></td></tr>`).join("")}</tbody></table>
    <form class="row mt" id="chNew"><label class="f">المجموعة <select name="kind"><option value="card">فيزا</option><option value="transfer">محفظة إلكترونية</option></select></label>
      <label class="f">الاسم <input name="name" required placeholder="مثال: جهاز البنك الأهلي"></label><button class="btn small">أضف</button></form>`;
  document.querySelectorAll("[data-chsave]").forEach((b) => b.addEventListener("click", async () => {
    const tr = b.closest("tr");
    const body = { name: tr.querySelector("[name=name]").value, is_active: tr.querySelector("[name=is_active]").checked, kind: tr.querySelector("[name=kind]").value };
    if (tr.querySelector("[name=acc]")) body.account_id = tr.querySelector("[name=acc]").value || null;
    try { renderChannels((await api(`/api/payment-channels/${tr.dataset.id}`, { method: "PATCH", body })).channels); toast("تم الحفظ"); }
    catch (err) { toast(err.message, true); }
  }));
  $("#chNew").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { renderChannels((await api("/api/payment-channels", { method: "POST", body: { kind: e.target.kind.value, name: e.target.name.value } })).channels); }
    catch (err) { toast(err.message, true); }
  });
}
const MB = (b) => (b < 1048576 ? `${Math.max(1, Math.round(b / 1024))} ك.ب` : `${(b / 1048576).toFixed(1)} ميجا`);
const BK_INT = [[15, "ربع ساعة"], [30, "نصف ساعة"], [60, "ساعة"], [360, "6 ساعات"]];
const BK_REASON = { schedule: "في موعدها", manual: "يدوي", shift_close: "إغلاق الشيفت", shutdown: "إغلاق البرنامج" };
function renderBackup(b) {
  const c = b.settings, st = b.state, ago = st.last_at ? Math.round((Date.now() - new Date(st.last_at)) / 60000) : null;
  $("#bkBox").innerHTML = `
    ${st.last_ok === false ? `<p class="badge b-err">✕ آخر محاولة فشلت: ${esc(st.last_error || "")}</p>` : ""}
    ${st.last_at ? `<p class="badge b-ok">✓ آخر نسخة من ${ago < 60 ? `${ago} دقيقة` : `${Math.round(ago / 60)} ساعة`} — ${MB(st.last_bytes || 0)} — ${esc(BK_REASON[st.last_reason] || "")}</p>`
      : `<p class="badge b-warn">⚠ لم تُعمل أي نسخة تلقائية بعد</p>`}
    <form id="bkForm" class="bk">
      <div><b>كل كم من الوقت</b><div class="seg" role="radiogroup">${BK_INT.map(([v, t]) => `<label><input type="radio" name="interval_min" value="${v}" ${c.interval_min === v ? "checked" : ""}> ${t}${v === 60 ? " (الافتراضي)" : ""}</label>`).join("")}</div>
        <label class="chk"><input type="checkbox" name="on_shift_close" ${c.on_shift_close ? "checked" : ""}> ونسخة أيضًا عند إغلاق الشيفت</label>
        <p class="muted">إذا لم يحدث أي تغيير منذ آخر نسخة، لا تُعمل نسخة جديدة.</p></div>
      <div><b>عدد النسخ المحفوظة</b>
        <label class="f">آخر <input class="narrow" type="number" name="keep_last" min="1" max="100" value="${c.keep_last}"> نسخ — تُحذف الأقدم تلقائيًا</label>
        <label class="f">+ نسخة من كل يوم لآخر <input class="narrow" type="number" name="keep_daily" min="0" max="60" value="${c.keep_daily}"> يوم</label>
        <label class="f">+ نسخة من كل أسبوع لآخر <input class="narrow" type="number" name="keep_weekly" min="0" max="52" value="${c.keep_weekly}"> أسابيع</label>
        <p class="muted">النسخ اليومية والأسبوعية حتى إذا اكتُشف خطأ بعد أيام، تجد نسخة سابقة له.</p></div>
      <div><b>نسخة خارج الجهاز</b>
        <label class="f">ذاكرة فلاش أو قرص خارجي أو مجلد Google Drive على الجهاز (المسار كامل)
          <input name="offsite_dir" value="${esc(c.offsite_dir)}" placeholder="E:\\نسخ الصيدلية" dir="auto"></label>
        ${!c.offsite_dir ? `<p class="badge b-warn">⚠ لا توجد نسخة خارج الجهاز — إذا تعطل الجهاز ستضيع النسخ معه</p>`
          : b.offsite_stale ? `<p class="badge b-warn">⚠ آخر نسخة خارج الجهاز ${st.offsite_at ? esc(st.offsite_at.slice(0, 10)) : "لا يوجد"}${st.offsite_error ? ` — ${esc(st.offsite_error)}` : ""}</p>`
          : `<p class="badge b-ok">✓ آخر نسخة خارج الجهاز ${esc(stamp(st.offsite_at))}</p>`}</div>
      <div class="row"><button class="btn">احفظ</button><button class="btn secondary" type="button" id="bkNow">اعمل نسخة الآن</button></div>
    </form>
    <details class="mt"><summary>النسخ الموجودة (${b.files.length}) — المساحة ${MB(b.total_bytes)} — المجلد ${esc(b.folder)}</summary>
      <table><tbody>${b.files.map((f) => `<tr><td dir="ltr">${esc(f.name)}</td><td>${MB(f.bytes)}</td><td>${f.auto ? "تلقائي" : "يدوي / قبل تحديث"}</td></tr>`).join("")}</tbody></table></details>`;
  $("#bkForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      renderBackup(await api("/api/backup", { method: "PUT", body: {
        interval_min: +f.querySelector("[name=interval_min]:checked").value, on_shift_close: f.on_shift_close.checked,
        keep_last: +f.keep_last.value, keep_daily: +f.keep_daily.value, keep_weekly: +f.keep_weekly.value,
        offsite_dir: f.offsite_dir.value } }));
      toast("تم الحفظ");
    } catch (err) { toast(err.message, true); }
  });
  $("#bkNow").addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "جارٍ عمل نسخة…";
    try { renderBackup(await api("/api/backup/run", { method: "POST", body: {} })); toast("عُملت النسخة وتم التحقق منها"); }
    catch (err) { e.target.disabled = false; e.target.textContent = "اعمل نسخة الآن"; toast(err.message, true); }
  });
}
async function checkUpdateBadge() {
  if (!ME?.permissions?.includes("settings.manage")) { $("#navSystem")?.setAttribute("hidden", ""); return; }
  const user = ME.user.id;
  try {
    const st = await api("/api/system/update", { settleAfterNavigation: true });
    if (ME?.user.id === user) $("#updBadge").hidden = !st.available;
  } catch { /* offline */ }
}

// An open tab keeps its old app.js after an update (#153): compare the shell it was loaded with to the server's.
const BUILD = document.querySelector('meta[name="pharm-build"]')?.content || null;
function screenIdle() {
  // reloading by itself is safe only where nothing typed can be lost: sign-in, home, an empty sale screen,
  // and only while no field holds text the person typed but the screen has not saved yet
  if ($("dialog[open]")) return false;
  if (API.busy || Date.now() - API.last < 3000) return false;     // a scan or a save may still be on its way
  const typed = [...document.querySelectorAll("input, textarea, select")].some((el) =>
    el.type === "checkbox" || el.type === "radio" ? el.checked !== el.defaultChecked
      : el.tagName === "SELECT" ? [...el.options].some((o) => o.selected !== o.defaultSelected)
        : el.value !== el.defaultValue);
  if (typed) return false;
  const h = location.hash || "#/home";
  if (h === "#/login" || h === "#/home") return true;
  return h.startsWith("#/pos") && !(SALE?.lines?.length);
}
async function checkBuild() {
  if (!BUILD || checkBuild.stale) return;
  let h;
  try { h = await (await fetch("/api/health", { cache: "no-store" })).json(); } catch { return; }   // restarting
  if (!h?.web || h.web === BUILD) return;
  if (screenIdle()) { location.reload(); return; }
  checkBuild.stale = true;
  $("#staleBar").hidden = false;
}
if (BUILD) {
  $("#staleBar")?.addEventListener("click", () => location.reload());
  setInterval(checkBuild, 60000);
  window.addEventListener("focus", checkBuild);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkBuild(); });
}

// ------------------------------------------------------------------ sales history / returns
const SALE_ST = { completed: "مكتملة", voided: "ملغاة" };
const localDay = (d = new Date()) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const LOC = () => (I18N.on ? "en-GB" : "ar-EG");   // dates and times follow the screen language
// #206: every time shown follows the pharmacy's setting (12-hour with ص/م by default, or 24-hour); stored values unchanged
const H12 = () => (ME?.time_format ?? "12") !== "24";
const hhmm = (iso) => (iso ? new Date(iso).toLocaleTimeString(LOC(), { hour: "2-digit", minute: "2-digit", hour12: H12() }) : "");
const hourLabel = (h) => (H12() ? `${h % 12 || 12}${h < 12 ? "ص" : "م"}` : String(h));
const dayOf = (iso) => (iso ? isoDay(new Date(iso)) : "");   // local day of a UTC timestamp

async function viewSales() {
  if (ME.permissions.includes("sales.totals")) return viewSalesAdvanced();
  return viewSalesSimple();
}

// تقرير فواتير المبيعات المتقدم: فلاتر بالنوع والساعة والصنف، ألوان، إجماليات، تفاصيل تحت الجدول، طباعة مختصر/كامل/المحدد
const INV_TYPES = [["cash", "نقدي"], ["card", "بطاقة"], ["transfer", "تحويل"], ["credit", "آجل"], ["delivery", "توصيل"], ["contract", "تعاقد"],
  ["returned", "عليها مرتجع"], ["discounted", "عليها خصم"], ["voided", "ملغاة"], ["draft", "معلقة (ما زالت مفتوحة)"]];
async function viewSalesAdvanced() {
  const can = (p) => ME.permissions.includes(p);
  const now = new Date(), dt = (d, h) => `${isoDay(d)}T${h}`;
  const sel = new Set(), types = new Set();
  let R = null, item = null, open = null, loadSeq = 0;
  app.innerHTML = `<section class="panel noprint"><div class="row between"><h1>فواتير البيع</h1><a href="#/owner-reports">تقارير المالك ←</a></div>
    <div class="row range"><div class="seg2 small">${Object.entries(periodPresets()).map(([k, [t]]) => `<button type="button" data-ip="${k}" class="${k === "today" ? "on" : ""}">${t}</button>`).join("")}</div>
      <label class="f">من <input type="datetime-local" id="iF" value="${dt(now, "00:00")}"></label><label class="f">إلى <input type="datetime-local" id="iT" value="${dt(now, "23:59")}"></label></div>
    <div class="row tags mt" role="group" aria-label="نوع الفاتورة">${INV_TYPES.map(([k, t]) => `<button type="button" class="chip tgl" data-it="${k}" aria-pressed="false">${t}</button>`).join("")}</div>
    <p class="muted small">بدون اختيار = كل الفواتير المكتملة. إذا اخترت أكثر من نوع تظهر الفواتير من أي منها. "ملغاة" و"معلقة" كل منهما بمفردها.</p>
    <div class="row"><label class="f grow">رقم الفاتورة أو اسم العميل أو هاتفه <input id="iQ" autocomplete="off" placeholder="مثال: 125 أو منى أو 0101"></label>
      <label class="f grow">فيها صنف <input id="iItem" autocomplete="off" placeholder="اكتب اسم الصنف"></label>
      <label class="f">الموظف <select id="iUser"><option value="">الكل</option></select></label>
      <button class="btn" id="iGo">اعرض</button></div><div id="iItemRes" class="cands"></div><div id="iItemChip"></div></section>
  <section class="panel" id="iRep"><div id="iBox"><p class="muted">جارٍ التحميل…</p></div></section><div id="iDetail"></div>`;
  const reportBox = $("#iBox");
  const load = async () => {
    const seq = ++loadSeq;
    const current = () => seq === loadSeq && reportBox.isConnected;
    const f = $("#iF").value, t = $("#iT").value;
    if (!f || !t) return toast("اختر الفترة", true);
    $("#iBox").innerHTML = `<p class="muted">جارٍ التحميل…</p>`; $("#iDetail").innerHTML = ""; sel.clear(); open = null;
    const qs = new URLSearchParams({ from: f, to: t, types: [...types].join(","), q: $("#iQ").value.trim(), item: item?.id || "", user: $("#iUser").value, lines: "1" });
    const asked = { f, t, q: $("#iQ").value.trim(), user: $("#iUser").value, types: types.size, item: item ? item.id : "" };
    let result;
    try { result = await api(`/api/reports/invoices?${qs}`); }
    catch (er) { if (current()) { reportBox.innerHTML = ""; toast(er.message, true); } return; }
    if (!current()) return;
    R = result;
    R.asked = asked;               // the E-Stock side follows the request that was answered, not the live controls (Codex P2)
    if ($("#iUser").options.length === 1) $("#iUser").insertAdjacentHTML("beforeend", R.sellers.map((u) => `<option value="${esc(u.id)}">${esc(u.name)}</option>`).join(""));
    draw();
  };
  const sub = () => `${stamp(R.from)} ← ${stamp(R.to)}`;
  // a file or a printout of a capped list says so, as the screen does (#324 R22)
  const subOut = () => sub() + (R.truncated ? " — ⚠ أول 3000 فاتورة فقط من الفترة" : "");
  const cls = (r) => (r.status === "voided" ? "inv-void" : r.status === "draft" ? "inv-draft" : r.returned_total ? "inv-ret" : r.discount_total ? "inv-disc" : "");
  const draw = () => {
    const T = R.totals, cost = R.show_cost;
    $("#iBox").innerHTML = `<div class="row between"><h2><span>فواتير البيع</span> <span class="muted">${esc(sub())}</span></h2>
      <span class="row noprint"><button class="btn small secondary" id="iXl">⬇ Excel</button><button class="btn small secondary" data-pr="short">🖨 مختصر</button>
        <button class="btn small secondary" data-pr="full">🖨 كامل بالأصناف</button><button class="btn small secondary" data-pr="sel" disabled>🖨 المحدد</button>
        ${can("items.write") ? `<button class="btn small secondary" id="iShort" disabled>📝 أضف أصناف الفواتير المحددة إلى النواقص</button>` : ""}</span></div>
      ${R.truncated ? `<p class="badge b-warn">⚠ أول 3000 فاتورة فقط — قلّل الفترة</p>` : ""}
      <div class="totals"><div>الفواتير<b>${T.n}</b><span>${T.returned_n} <span>عليها مرتجع</span> · ${T.discounted_n} <span>عليها خصم</span></span></div><div>قبل الخصم<b>${money(T.subtotal)}</b></div>
        <div>خصم الأصناف<b>${money(T.item_discount)}</b></div><div>خصم العميل (تعاقد)<b>${money(T.customer_discount)}</b></div><div>بعد الخصم<b>${money(T.total)}</b>${T.delivery_fee ? `<span>منها توصيل ${money(T.delivery_fee)}</span>` : ""}</div>
        <div>المرتجع<b>${money(T.returned_total)}</b></div><div class="t-ok">الصافي<b>${money(T.net)}</b></div><div>النقدي<b>${money(T.paid_cash)}</b></div>
        <div>بطاقة<b>${money(T.paid_card)}</b></div><div>تحويل<b>${money(T.paid_transfer)}</b></div><div>آجل<b>${money(T.on_credit)}</b></div>
        ${cost ? `<div>ربح الأصناف<b>${money(T.profit)}</b>${T.unknown_cost_n ? `<span>${T.unknown_cost_n} فاتورة بها صنف بدون تكلفة</span>` : ""}</div>` : ""}</div>
      <p class="legend noprint"><span class="lg inv-ret">عليها مرتجع</span><span class="lg inv-disc">عليها خصم</span><span class="lg inv-void">ملغاة</span><span class="lg inv-draft">معلقة</span> — <span>اضغط على أي فاتورة لعرض أصنافها أسفل الجدول.</span></p>
      <div class="row end" id="iColsBox"></div>
      <div class="tablewrap mt"><table class="inv" id="iTable"><thead><tr><th class="noprint" data-c="sel"><input type="checkbox" id="iAll" aria-label="اختر الكل"></th><th data-c="no">رقم</th><th data-c="time">الوقت</th><th data-c="customer">العميل</th><th data-c="seller">الموظف</th><th data-c="lines">أصناف</th>
        <th data-c="gross">قبل الخصم</th><th data-c="item_disc">خصم الأصناف</th><th data-c="cust_disc">خصم العميل</th><th data-c="total">بعد الخصم</th><th data-c="returns">المرتجع</th><th data-c="net">الصافي</th><th data-c="cash">نقدي</th><th data-c="card">بطاقة</th><th data-c="transfer">تحويل</th><th data-c="credit">آجل</th>${cost ? `<th data-c="profit">الربح</th>` : ""}</tr></thead><tbody>
        ${R.rows.map((r, i) => `<tr class="clickable ${cls(r)}" data-i="${i}"><td class="noprint"><input type="checkbox" data-s="${i}" aria-label="اختر"></td>
          <td><b>${r.sale_no ?? "—"}</b>${r.kind === "delivery" ? ` <span class="badge b-info" title="توصيل">🛵</span>` : ""}${r.contract ? ` <span class="chip" title="تعاقد">🤝 ${esc(r.contract)}</span>` : ""}${r.status === "voided" ? ` <span class="badge b-err">ملغاة</span>` : r.status === "draft" ? ` <span class="badge b-warn">معلقة</span>` : ""}</td>
          <td>${esc(dayOf(r.completed_at || r.created_at))} ${hhmm(r.completed_at || r.created_at)}</td><td>${esc(r.customer || "—")}</td><td>${esc(r.seller || "")}</td><td>${r.lines}</td>
          <td class="num">${money(r.subtotal)}</td><td class="num">${r.item_discount ? money(r.item_discount) : ""}</td><td class="num">${r.customer_discount ? money(r.customer_discount) : ""}</td>
          <td class="num"><b>${money(r.total)}</b></td><td class="num">${r.returned_total ? money(r.returned_total) : ""}</td><td class="num"><b>${money(r.net)}</b></td>
          <td class="num">${r.paid_cash ? money(r.paid_cash) : ""}</td><td class="num">${r.paid_card ? money(r.paid_card) : ""}</td><td class="num">${r.paid_transfer ? money(r.paid_transfer) : ""}</td><td class="num">${r.on_credit ? money(r.on_credit) : ""}</td>
          ${cost ? `<td class="num">${r.status === "completed" ? `${money(r.profit)}${r.unknown_cost ? " ⚠" : ""}` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="17" class="muted">لا توجد فواتير بهذه التصفية</td></tr>`}</tbody>
        ${R.rows.length ? `<tfoot><tr><td class="noprint"></td><td colspan="5"><b>الإجمالي (${T.n})</b></td><td class="num">${money(T.subtotal)}</td><td class="num">${money(T.item_discount)}</td><td class="num">${money(T.customer_discount)}</td>
          <td class="num"><b>${money(T.total)}</b></td><td class="num">${money(T.returned_total)}</td><td class="num"><b>${money(T.net)}</b></td><td class="num">${money(T.paid_cash)}</td><td class="num">${money(T.paid_card)}</td>
          <td class="num">${money(T.paid_transfer)}</td><td class="num">${money(T.on_credit)}</td>${cost ? `<td class="num"><b>${money(T.profit)}</b></td>` : ""}</tr></tfoot>` : ""}</table></div>
      <p class="muted"><span>الفواتير الملغاة والمعلقة غير داخلة في الإجماليات. الصافي = بعد الخصم ناقص المرتجع.</span> ${cost ? "<span>الربح = صافي الأصناف التي بقيت مع العميل ناقص تكلفتها (دون رسوم التوصيل).</span>" : ""} <span>المرتجع هنا هو ما أُرجع من فواتير هذه الفترة؛ أما تقرير المرتجعات في تقارير المالك فيعرض كل مرتجع بتاريخ إرجاعه.</span></p>`;
    const selUi = () => { document.querySelectorAll("[data-pr=sel], #iShort").forEach((b) => { b.disabled = !sel.size; }); };
    $("#iColsBox").innerHTML = colTools($("#iTable"), "invoices"); wireCols($("#iColsBox"), $("#iTable"), "invoices"); applyCols($("#iTable"), "invoices");
    $("#iBox").querySelectorAll("tr.clickable").forEach((tr) => tr.addEventListener("click", (e) => {
      if (e.target.matches("input")) return;
      $("#iBox").querySelectorAll("tr.open").forEach((x) => x.classList.remove("open")); tr.classList.add("open");
      detail(R.rows[+tr.dataset.i]);
    }));
    $("#iBox").querySelectorAll("[data-s]").forEach((cb) => cb.addEventListener("change", () => { cb.checked ? sel.add(+cb.dataset.s) : sel.delete(+cb.dataset.s); selUi(); }));
    $("#iAll")?.addEventListener("change", (e) => { $("#iBox").querySelectorAll("[data-s]").forEach((cb) => { cb.checked = e.target.checked; cb.checked ? sel.add(+cb.dataset.s) : sel.delete(+cb.dataset.s); }); selUi(); });
    $("#iXl").addEventListener("click", () => exportXlsx("فواتير البيع", subOut(), sheets()));
    // E-Stock has no payment types or sellers here: with those filters on, its rows would not answer the question
    // E-Stock counts whole days: part of a day would add sales outside the window; a capped program total (first
    // 3000 invoices) is never added up (U4 review)
    const A = R.asked, fullDays = A.f.slice(11) === "00:00" && A.t.slice(11) === "23:59";
    if (!A.types && !A.user && fullDays) {
      estockSalesBelow($("#iBox"), A.f.slice(0, 10), A.t.slice(0, 10), A.q,
        A.item || R.truncated ? null : T.total, A.item).catch(() => {});
    } else { ES_SEQ++; document.querySelector("#esBelow")?.remove(); }   // a pending answer is dropped too (Codex P2)
    $("#iBox").querySelectorAll("[data-pr]").forEach((b) => b.addEventListener("click", () => printInv(b.dataset.pr)));
    $("#iShort")?.addEventListener("click", async () => {
      const ids = [...new Set([...sel].flatMap((i) => (R.rows[i].items || []).map((x) => x.item_id)))];
      if (!ids.length || !confirmInline($("#iShort"))) return;
      let n = 0;
      for (const id of ids) { try { await api(`/api/items/${id}/shortage`, { method: "POST", body: { on: true } }); n++; } catch { /* keep going */ } }
      toast(`✓ سُجِّل ${n} صنف ناقص`);
    });
  };
  const detail = (r) => {
    open = r;
    $("#iDetail").innerHTML = `<section class="panel" id="iDet"><div class="row between"><h2>فاتورة ${r.sale_no ?? "معلقة"} — ${esc(r.customer || "بدون عميل")}</h2>
      <span class="row noprint">${r.status !== "draft" ? `<a class="btn small secondary" href="#/sales/${esc(r.id)}">افتح الفاتورة</a>` : ""}<button class="btn small secondary" id="iDetClose">✕ إغلاق</button></span></div>
      <p class="muted">${esc(dayOf(r.completed_at || r.created_at))} ${hhmm(r.completed_at || r.created_at)} · ${esc(r.seller || "")}${r.void_reason ? ` · سبب الإلغاء: ${esc(r.void_reason)}` : ""}</p>
      <div class="tablewrap"><table><thead><tr><th>الصنف</th><th>الكمية</th><th>المُرتجع منها</th><th>سعر الجمهور</th><th>قبل الخصم</th><th>الخصم</th><th>بعد الخصم</th>${can("items.write") ? `<th class="noprint"></th>` : ""}</tr></thead><tbody>
      ${(r.items || []).map((x) => `<tr><td>${itemHtml(x)}</td><td>${esc(fmtStock(x.qty, x))}</td><td>${x.returned ? esc(fmtStock(x.returned, x)) : ""}</td>
        <td>${(x.official_prices || []).map((p) => `${salePriceQty(p, { pack_unit: x.pack_unit_name, base_unit: x.base_unit_name })} × ${money(p.price)}`).join("<br>")}</td>
        <td class="num">${money(x.gross)}</td>
        <td>${money(x.gross - x.net)}${x.invoice_share ? `<div class="muted small">من خصم الفاتورة: ${money(x.invoice_share)}</div>` : ""}</td><td class="num"><b>${money(x.net)}</b></td>
        ${can("items.write") ? `<td class="noprint">${x.name_en === "—" ? "" : `<button class="btn small secondary" data-sh="${esc(x.item_id)}">📝 أضف إلى النواقص</button>`}</td>` : ""}</tr>`).join("")}</tbody></table></div></section>`;
    $("#iDetClose").addEventListener("click", () => { $("#iDetail").innerHTML = ""; });
    $("#iDetail").querySelectorAll("[data-sh]").forEach((b) => b.addEventListener("click", async () => {
      try { await api(`/api/items/${b.dataset.sh}/shortage`, { method: "POST", body: { on: true } }); b.disabled = true; b.textContent = "✓ تمت الإضافة"; }
      catch (er) { toast(er.message, true); }
    }));
    $("#iDet").scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const sheets = () => {
    const cost = R.show_cost, T = R.totals;
    return [{ name: "الفواتير", columns: [N("رقم"), Tx("الوقت"), Tx("الحالة"), Tx("العميل"), Tx("الموظف"), N("أصناف"), M("قبل الخصم"), M("خصم الأصناف"), M("خصم العميل"), M("بعد الخصم"),
        M("المرتجع"), M("الصافي"), M("نقدي"), M("بطاقة"), M("تحويل"), M("آجل"), ...(cost ? [M("الربح")] : [])],
      rows: R.rows.map((r) => [r.sale_no, `${dayOf(r.completed_at || r.created_at)} ${hhmm(r.completed_at || r.created_at)}`, r.status === "voided" ? "ملغاة" : r.status === "draft" ? "معلقة" : r.returned_total ? "عليها مرتجع" : "مكتملة",
        r.customer || "", r.seller || "", r.lines, r.subtotal, r.item_discount, r.customer_discount, r.total, r.returned_total, r.net, r.paid_cash, r.paid_card, r.paid_transfer, r.on_credit, ...(cost ? [r.status === "completed" ? r.profit : null] : [])]),
      total: ["الإجمالي", "", "", "", "", null, T.subtotal, T.item_discount, T.customer_discount, T.total, T.returned_total, T.net, T.paid_cash, T.paid_card, T.paid_transfer, T.on_credit, ...(cost ? [T.profit] : [])] },
    { name: "الأصناف", columns: [N("رقم الفاتورة"), Tx("الصنف"), N("الكمية بالوحدة"), N("المُرتجع منها"), M("قبل الخصم"), Tx("الخصم"), M("بعد الخصم")],
      rows: R.rows.flatMap((r) => (r.items || []).map((x) => [r.sale_no, x.name_en || x.name_ar, x.qty, x.returned, x.gross, x.discount_bp ? `${pct(x.discount_bp)}%` : "", x.net])) }];
  };
  const printInv = (mode) => {
    const rows = mode === "sel" ? [...sel].sort((a, b) => a - b).map((i) => R.rows[i]) : R.rows;
    const box = document.createElement("div");
    if (mode === "full") {
      box.innerHTML = rows.map((r) => `<section class="inv-print"><h3>فاتورة ${r.sale_no ?? "معلقة"} — ${esc(dayOf(r.completed_at || r.created_at))} ${hhmm(r.completed_at || r.created_at)} — ${esc(r.customer || "بدون عميل")} — ${esc(r.seller || "")}</h3>
        <table><thead><tr><th>الصنف</th><th>الكمية</th><th>المُرتجع</th><th>بعد الخصم</th></tr></thead><tbody>${(r.items || []).map((x) => `<tr><td>${esc(x.name_en || x.name_ar)}</td><td>${esc(fmtStock(x.qty, x))}</td><td>${x.returned ? esc(fmtStock(x.returned, x)) : ""}</td><td class="num">${money(x.net)}</td></tr>`).join("")}</tbody>
        <tfoot><tr><td colspan="3">الإجمالي${r.returned_total ? ` · مرتجع ${money(r.returned_total)} · الصافي ${money(r.net)}` : ""}</td><td class="num"><b>${money(r.total)}</b></td></tr></tfoot></table></section>`).join("");
    } else {
      const copy = $("#iBox").cloneNode(true);
      if (mode === "sel") { const keep = new Set([...sel].map(String)); copy.querySelectorAll("tbody tr[data-i]").forEach((tr) => { if (!keep.has(tr.dataset.i)) tr.remove(); }); copy.querySelector("tfoot")?.remove(); copy.querySelector(".totals")?.remove(); copy.querySelector("#esBelow")?.remove(); }
      box.append(copy);
    }
    printPreview(box, mode === "full" ? "فواتير البيع بالأصناف" : mode === "sel" ? "الفواتير المحددة" : "فواتير البيع", subOut());
  };
  document.querySelectorAll("[data-ip]").forEach((b) => b.addEventListener("click", () => {
    const [, a, z] = periodPresets()[b.dataset.ip];
    document.querySelectorAll("[data-ip]").forEach((x) => x.classList.toggle("on", x === b));
    $("#iF").value = dt(a, "00:00"); $("#iT").value = dt(z, "23:59"); load();
  }));
  ["#iF", "#iT"].forEach((k) => $(k).addEventListener("change", () => document.querySelectorAll("[data-ip]").forEach((x) => x.classList.remove("on"))));
  document.querySelectorAll("[data-it]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.it, solo = ["voided", "draft"];
    if (types.has(k)) types.delete(k);
    else { if (solo.includes(k)) types.clear(); else solo.forEach((x) => types.delete(x)); types.add(k); }
    document.querySelectorAll("[data-it]").forEach((x) => { const on = types.has(x.dataset.it); x.classList.toggle("on", on); x.setAttribute("aria-pressed", on); });
    load();
  }));
  $("#iGo").addEventListener("click", load);
  $("#iQ").addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
  $("#iUser").addEventListener("change", load);
  let tm;
  $("#iItem").addEventListener("input", (e) => { clearTimeout(tm); tm = setTimeout(async () => {
    const v = e.target.value.trim(); if (v.length < 2) { $("#iItemRes").innerHTML = ""; return; }
    const r = (await findItems(v, 8)).items;
    $("#iItemRes").innerHTML = r.map((x, i) => `<button type="button" class="btn small secondary" data-ii="${i}">${itemHtml(x)}</button>`).join("") || `<p class="muted">لا توجد نتائج</p>`;
    $("#iItemRes").querySelectorAll("[data-ii]").forEach((b) => b.addEventListener("click", () => {
      item = r[+b.dataset.ii]; $("#iItemRes").innerHTML = ""; $("#iItem").value = "";
      $("#iItemChip").innerHTML = `<span class="chip"><span>تحتوي على:</span> ${itemHtml(item)} <button type="button" class="x" id="iItemX" aria-label="إزالة">✕</button></span>`;
      $("#iItemX").addEventListener("click", () => { item = null; $("#iItemChip").innerHTML = ""; load(); });
      load();
    }));
  }, 250); });
  load();
}

async function viewSalesSimple() {
  const all = ME.permissions.includes("sales.totals");
  app.innerHTML = `
  <section class="panel"><h1>فواتير البيع</h1>
    ${all ? rangeBar("sRange", "today") : `<div class="row"><label class="f">اليوم<input type="date" id="sDay" value="${localDay()}" disabled></label></div>`}
    <div class="row"><label class="f">أو رقم الفاتورة<input id="sNo" inputmode="numeric" placeholder="مثال: 125"></label>
      <button class="btn secondary" id="sGo">عرض</button></div></section>
  <section class="panel" id="sRep"><div class="row between"><h2 id="sTitle"></h2>${reportTools("sales-list")}</div>
    <div class="tablewrap"><table><thead><tr><th>رقم</th><th>اليوم</th><th>الوقت</th><th>العميل</th><th>الأصناف</th>
    <th>الإجمالي</th><th>نقدي/بطاقة/تحويل/آجل</th><th>مرتجع</th><th>الحالة</th></tr></thead><tbody id="sBody"></tbody></table></div><p id="sSum"></p></section>`;
  let rows = [], sub = "";
  const load = async (f, t) => {
    const no = $("#sNo").value.trim();
    rows = await api(`/api/sales?${no ? `no=${encodeURIComponent(no)}` : all ? `from=${f}&to=${t}` : `day=${localDay()}`}`);
    sub = no ? `فاتورة رقم ${no}` : all ? rangeText(f, t) : `اليوم ${localDay()}`;
    $("#sTitle").textContent = sub;
    $("#sBody").innerHTML = rows.map((r) => `<tr class="clickable" data-id="${r.id}"><td><b>${r.sale_no ?? ""}</b></td>
      <td>${esc(dayOf(r.completed_at))}</td><td>${hhmm(r.completed_at)}</td><td>${esc(r.customer || "—")}</td><td>${r.lines}</td><td><b>${money(r.total)}</b></td>
      <td>${money(r.paid_cash)} / ${money(r.paid_card)} / ${money(r.paid_transfer || 0)} / ${money(r.on_credit)}</td><td>${r.returned_total ? money(r.returned_total) : ""}</td>
      <td>${r.status === "voided" ? `<span class="badge b-err">ملغاة</span>` : SALE_ST[r.status]}</td></tr>`).join("")
      || `<tr><td colspan="9" class="muted">لا توجد فواتير</td></tr>`;
    const ok = rows.filter((r) => r.status !== "voided");
    const tot = ok.reduce((a, r) => a + r.total, 0), ret = ok.reduce((a, r) => a + (r.returned_total || 0), 0);
    $("#sSum").innerHTML = rows.length ? `<b>${ok.length} فاتورة — ${money(tot)} ج${ret ? ` · مرتجع ${money(ret)} · الصافي ${money(tot - ret)} ج` : ""}</b>` : "";
    $("#sBody").querySelectorAll("tr.clickable").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/sales/${tr.dataset.id}`; }));
  };
  wireTools("sales-list", () => ({ title: "فواتير البيع", subtitle: sub, el: $("#sRep"), sheets: [
    { name: "الفواتير", columns: [N("رقم"), Tx("الوقت"), Tx("العميل"), N("الأصناف"), M("الإجمالي"), M("نقدي"), M("بطاقة"), M("تحويل"), M("آجل"), M("مرتجع"), Tx("الحالة")],
      rows: rows.map((r) => [r.sale_no, `${dayOf(r.completed_at)} ${hhmm(r.completed_at)}`, r.customer || "", r.lines, r.total, r.paid_cash, r.paid_card, r.paid_transfer || 0, r.on_credit, r.returned_total || 0, r.status === "voided" ? "ملغاة" : "مكتملة"]) }] }));
  if (all) {
    const get = wireRange("sRange", (f, t) => { $("#sNo").value = ""; load(f, t).catch((e) => toast(e.message, true)); });
    $("#sGo").addEventListener("click", () => load(...get()).catch((e) => toast(e.message, true)));
  } else {
    $("#sGo").addEventListener("click", () => load().catch((e) => toast(e.message, true)));
    load();
  }
}

// #114: the refund goes back the way the sale was paid (credit sale -> the customer's account, card only -> card);
// the cashier can still pick another method
function refundDefault(s) {
  if (s.on_credit > 0 && !s.paid_cash && s.customer_id) return "credit";
  if (s.paid_card > 0 && !s.paid_cash && !s.on_credit && !s.paid_transfer) return "card";
  if (s.paid_transfer > 0 && !s.paid_cash && !s.on_credit && !s.paid_card) return "transfer";
  return "cash";
}

// #142 (owner, 11/11): cash goes back only for money the sale actually brought in (cash, card or transfer; a
// courier's cash once handed in) minus earlier cash or card refunds; the credit part goes back to the account.
// A wrong choice is shown disabled with the reason; every other choice stays available.
function cashRefundRoom(s) {
  const cashIn = s.kind === "delivery" && s.delivery?.status !== "settled" ? 0 : (s.paid_cash || 0);
  const back = (s.returns || []).filter((r) => ["cash", "card", "transfer"].includes(r.refund_method))
    .reduce((t, r) => t + (r.total || 0), 0);
  return Math.max(0, cashIn + (s.paid_card || 0) + (s.paid_transfer || 0) - back);
}

function refundOptions(s, includeWallet = false) {
  const room = cashRefundRoom(s), left = (s.total || 0) + (s.rounding || 0) - (s.returned_total || 0);
  const cash = !room ? "نقدًا من الخزينة — غير متاح: الفاتورة آجل" : room < left ? `نقدًا من الخزينة (حتى ${money(room)} ج)` : "نقدًا من الخزينة";
  const opts = [["cash", cash, !room], ...(s.paid_card ? [["card", "بطاقة", false]] : []),
    ...(includeWallet && s.paid_transfer ? [["transfer", "رجّع للمحفظة الأصلية", false]] : []),
    ...(s.customer_id ? [["credit", "على حساب العميل", false]] : [])];
  const want = refundDefault(s), ok = opts.filter((o) => !o[2]);
  const pick = ok.some((o) => o[0] === want) ? want : (ok[0] || opts[0])[0];
  return opts.map(([v, t, off]) => ({ value: v, label: t, disabled: off, selected: v === pick }));
}

// ------------------------------------------------------------------ return + replacement in one operation (part 3 item 6a)
// Search the invoice by its number, an item code / barcode, or the customer's phone or name; type the quantities
// coming back; add the replacement items; the three totals (returned, new, net) come from the server before saving.
// Only the net is paid or given back. The returned goods go back to stock as a sale return, never a purchase.
// after an exchange: a receipt the printer did not take (or browser printing) prints in the page, as at checkout
function exchangeDevices(r) {
  const dv = (r && r.devices) || {};
  if (dv.receipt === "browser" || dv.receipt === "failed") printInFrame(`/receipt/${r.sale_id}`);
  if (dv.message) toast(dv.message, dv.receipt === "failed");
  return dv.receipt || "none";
}
async function viewExchange() {
  app.innerHTML = `<section class="panel"><h1>مرتجع واستبدال</h1>
    <form class="row" id="exF"><input name="q" class="grow" placeholder="رقم الفاتورة أو كود الصنف أو الباركود أو تليفون العميل أو اسمه" autofocus><button class="btn small">ابحث</button></form>
    <div id="exList" class="mt"></div></section><div id="exSale"></div>`;
  let sale = null, draft = null, findings = [];
  CHANNELS = CHANNELS || (await api("/api/payment-channels")).channels.filter((c) => c.is_active);   // card devices (Codex P2 on #396)
  const H = (t) => `<span class="muted small">${t}</span>`;
  const totals = async () => {
    const lines = {};
    document.querySelectorAll("[data-xr]").forEach((i) => { const v = typedWhole(i.value); if (Number(v) > 0) lines[i.dataset.xr] = v; });
    const box = $("#exTot");
    if (!box) return null;
    if (!Object.keys(lines).length || !draft) { box.innerHTML = H("اكتب كمية المرتجع وضيف صنف الاستبدال"); return null; }
    try {
      const t = await api(`/api/sales/${sale.id}/exchange`, { method: "POST", body: { preview: true, lines, new_sale_id: draft.id, draft_version: draft.version, refund_method: $("#exRm").value } });
      box.innerHTML = `<div class="totals"><div>قيمة المرتجع<b>${money(t.returned)}</b></div><div>فاتورة الاستبدال<b>${money(t.new_total)}</b></div>
        <div class="${t.net >= 0 ? "" : "t-ok"}">${t.net >= 0 ? "العميل يدفع" : "يرجع للعميل"}<b>${money(Math.abs(t.net))}</b></div></div>`;
      return { lines, t };
    } catch (e) { box.innerHTML = `<p class="badge b-err">${esc(e.message)}</p>`; return null; }
  };
  const drawDraft = () => {
    // the replacement changed: decisions typed for the old findings never carry over (Codex P1 on #396)
    findings = []; if ($("#exSafe")) $("#exSafe").innerHTML = "";
    $("#exNew").innerHTML = draft && draft.lines.length ? `<table><tbody>${draft.lines.map((l) => `<tr><td>${esc(l.item.name)}</td><td>${l.qty_base}</td><td class="num">${money(l.net)}</td>
      <td><button class="btn small secondary" data-xdel="${esc(l.id)}">✕</button></td></tr>`).join("")}</tbody></table>` : H("مفيش أصناف استبدال لسه");
    $("#exNew").querySelectorAll("[data-xdel]").forEach((b) => b.addEventListener("click", async () => {
      try { draft = await api(`/api/sales/${draft.id}/lines/${b.dataset.xdel}`, { method: "DELETE" }); drawDraft(); totals(); } catch (e) { toast(e.message, true); }
    }));
  };
  const open = async (id) => {
    sale = (await api(`/api/returns/find?q=${encodeURIComponent($("#exF").q.value)}`)).sales.find((x) => x.id === id);
    const full = await api(`/api/sales/${id}`);
    $("#exSale").innerHTML = `<section class="panel"><div class="row between"><h2>فاتورة ${sale.sale_no} — ${esc(sale.customer || "بدون عميل")}</h2>
      ${sale.in_window ? `<span class="badge b-ok">في مدة المرتجع (المدة: ${sale.window_days})</span>` : `<span class="badge b-warn">⚠ بعد مدة المرتجع (${sale.age_days} يوم) — محتاج موافقة</span>`}</div>
      <table><thead><tr><th>الصنف</th><th>الكود</th><th>اتباع</th><th>اترجع قبل كده</th><th>متاح للمرتجع</th><th>الصافي بعد الخصم</th><th>كمية المرتجع</th></tr></thead><tbody>
      ${sale.lines.map((l) => `<tr><td>${esc(l.name)}</td><td>${esc(l.code || "")}</td><td>${l.sold}</td><td>${l.returned}</td><td>${l.returnable}</td><td class="num">${money(l.net)}</td>
        <td>${l.returnable ? `<input class="narrow" type="number" min="0" max="${l.returnable}" step="1" inputmode="numeric" data-xr="${esc(l.id)}">` : H("اترجع كله")}</td></tr>`).join("")}</tbody></table>
      <div class="row mt"><label class="f">المرتجع يرجع<select id="exRm">${refundOptions(full).filter((o) => o.value !== "card").map((o) => `<option value="${o.value}" ${o.selected ? "selected" : ""} ${o.disabled ? "disabled" : ""}>${esc(o.label)}</option>`).join("")}</select></label>
        <label class="f grow">السبب<input id="exWhy" placeholder="مثال: العميل غيّر الصنف"></label></div>
      <h3 class="mt">أصناف الاستبدال</h3><form class="row" id="exAdd"><input name="q" class="grow" placeholder="امسح الباركود أو اكتب الصنف"><input name="n" class="narrow" type="number" min="1" step="1" value="1"><button class="btn small secondary">ضيف</button></form>
      <div id="exNew" class="mt"></div><div id="exTot" class="mt"></div><div id="exSafe" class="mt"></div>
      <div class="row mt"><label class="f">الفرق يتدفع<select id="exPm"><option value="cash">نقدي</option><option value="card">بطاقة</option>${sale.customer_id ? `<option value="credit">على حساب العميل</option>` : ""}</select></label>
        <label class="f" id="exChW" hidden>الجهاز<select id="exCh">${(CHANNELS || []).filter((c) => c.kind === "card").map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select></label>
        <button class="btn big" id="exGo">سجّل المرتجع والاستبدال</button></div></section>`;
    $("#exPm").addEventListener("change", () => { $("#exChW").hidden = $("#exPm").value !== "card" || !$("#exCh").options.length; });
    draft = null; drawDraft(); totals();
    // safety findings of the replacement items: shown here, decided here, sent with the exchange (Codex P2 on #396)
    const showFindings = (fs) => {
      findings = fs || [];
      $("#exSafe").innerHTML = findings.length ? `<div class="panel"><h3>فحص السلامة الدوائية</h3>${findings.map((f, i) => `<div class="mt"><b>${esc(f.message || f.title || "")}</b>
        ${["override", "block", "acknowledge"].includes(f.requires) ? `<input data-xdec="${i}" placeholder="${f.requires === "acknowledge" ? "ملاحظة (اختياري) ثم تأكيد" : "اكتب سبب الاستمرار (يُسجَّل باسمك)"}">` : ""}</div>`).join("")}</div>` : "";
    };
    const decisions = () => {
      const d = {};
      document.querySelectorAll("[data-xdec]").forEach((inp) => {
        const f = findings[+inp.dataset.xdec];
        if (!f) return;
        if (f.requires === "acknowledge") d[inp.dataset.xdec] = inp.value || "تمت المراجعة";
        else if (inp.value.trim()) d[inp.dataset.xdec] = inp.value.trim();
      });
      return d;
    };
    document.querySelectorAll("[data-xr]").forEach((i) => i.addEventListener("input", () => totals()));
    $("#exRm").addEventListener("change", () => totals());
    $("#exAdd").addEventListener("submit", async (e) => {
      e.preventDefault();
      const q = e.target.q.value.trim(), n = typedWhole(e.target.n.value) || 1;
      if (!q) return;
      try {
        const r = await findItems(q, 1), it = (r.items || r)[0];
        if (!it) { toast("الصنف مش موجود", true); return; }
        if (!draft) draft = await api("/api/sales", { method: "POST", body: { customer_id: sale.customer_id || null } });
        draft = await api(`/api/sales/${draft.id}/lines`, { method: "POST", body: { item_id: it.id, units: n, client_ref: newKey() } });
        e.target.q.value = ""; drawDraft(); totals();
      } catch (err) { toast(err.message, true); }
    });
    $("#exGo").addEventListener("click", async (ev) => {
      const btn = ev.currentTarget, got = await totals();
      if (!got) { toast("اكتب كمية المرتجع وضيف صنف الاستبدال", true); return; }
      await withBusy(btn, async () => {
        try {
          const r = await api(`/api/sales/${sale.id}/exchange`, { method: "POST", body: { lines: got.lines, new_sale_id: draft.id,
            refund_method: $("#exRm").value, pay_method: $("#exPm").value, channel_id: $("#exPm").value === "card" ? $("#exCh").value || null : null, reason: $("#exWhy").value, version: draft.version, request_id: (draft.request_id ||= newKey()), safety_decisions: decisions(), expected_new_total: got.t.new_total, cost_reason: draft.cost_reason || null, receipt: true } });
          exchangeDevices(r);
          toast(r.net >= 0 ? `✓ اتسجل — العميل يدفع ${money(r.net)} ج` : `✓ اتسجل — يرجع للعميل ${money(-r.net)} ج`);
          location.hash = `#/sales/${r.sale_id}`;
        } catch (err) {
          if (err.code === "at_cost_reason") {              // U2-33: down to cost only, with a written reason
            const why = (prompt(`${err.message}\n${(err.details?.lines || []).map((l) => l.name).join("، ")}`, "") || "").trim();
            if (why.length >= 3) { draft.cost_reason = why; setTimeout(() => btn.click(), 0); }
            return;
          }
          toast(err.message, true);
          if (err.code === "safety_unresolved") showFindings(err.details?.findings);
          if (["price_changed", "stale_version"].includes(err.code)) {   // the server repriced it: show the new draft
            try { draft = await api(`/api/sales/${draft.id}`); drawDraft(); totals(); } catch { /* the toast says why */ }
          }
        }
      });
    });
  };
  $("#exF").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api(`/api/returns/find?q=${encodeURIComponent(e.target.q.value)}`);
      $("#exList").innerHTML = r.sales.length ? `<table><thead><tr><th>الفاتورة</th><th>اليوم</th><th>العميل</th><th>الإجمالي</th><th></th></tr></thead><tbody>
        ${r.sales.map((x) => `<tr class="clickable" data-xs="${esc(x.id)}"><td>${x.sale_no}</td><td>${esc(dayOf(x.completed_at))}</td><td>${esc(x.customer || "")}</td><td class="num">${money(x.total)}</td>
          <td>${x.in_window ? "" : `<span class="badge b-warn">بعد المدة</span>`}</td></tr>`).join("")}</tbody></table>` : H("مفيش فواتير بالبحث ده");
      $("#exList").querySelectorAll("[data-xs]").forEach((tr) => tr.addEventListener("click", () => open(tr.dataset.xs).catch((err) => toast(err.message, true))));
    } catch (err) { toast(err.message, true); }
  });
}

async function viewSale(id) {
  const s = await api(`/api/sales/${id}`);
  const can = (p) => ME?.permissions?.includes(p);
  const wallets = [...new Map(s.payments.filter((p) => p.method === "transfer").map((p) => [p.channel_id, p.channel])).entries()];
  const returnable = (l) => l.allocations.reduce((t, a) => t + a.qty - (a.returned_qty || 0), 0);
  const fq = (q, l) => fmtStock(q, { pack_size: l.item.pack_size, base_unit_name: l.item.base_unit });
  app.innerHTML = `
  <section class="panel"><div class="row between"><h1>فاتورة بيع ${s.sale_no ?? ""} ${s.status === "voided" ? `<span class="badge b-err">ملغاة</span>` : ""}</h1>
    <div class="row"><a class="btn secondary" href="/receipt/${s.id}" target="_blank">طباعة</a><a href="#/sales">رجوع للفواتير</a></div></div>

    <div class="totals"><div>الإجمالي<b>${money(s.subtotal)}</b></div><div>الخصم<b>${money(s.discount_total)}</b></div>
      ${s.delivery_fee ? `<div>رسوم التوصيل<b>${money(s.delivery_fee)}</b></div>` : ""}
      <div>المطلوب<b>${money(s.total + (s.rounding || 0))}</b>${s.rounding ? `<span>تقريب النقدي ${s.rounding > 0 ? "+" : "−"}${money(Math.abs(s.rounding))} (مش مبيعات)</span>` : ""}</div><div>نقدي<b>${money(s.paid_cash)}</b></div>
      <div>بطاقة<b>${money(s.paid_card)}</b></div>${s.paid_transfer ? `<div>تحويل<b>${money(s.paid_transfer)}</b></div>` : ""}<div>آجل<b>${money(s.on_credit)}</b></div><div>مرتجع<b>${money(s.returned_total)}</b></div>
      <div>العميل<b>${esc(s.customer?.name || "—")}</b></div></div>
    ${s.void_reason ? `<p class="muted">سبب الإلغاء: ${esc(s.void_reason)}</p>` : ""}</section>
  ${s.returns.length ? `<section class="panel"><h2>المرتجعات</h2><table><thead><tr><th>اليوم</th><th>المبلغ</th><th>رُدّ</th><th>السبب</th><th>سجّله</th><th></th></tr></thead><tbody>
    ${s.returns.map((r) => `<tr><td>${esc(dayOf(r.created_at))} ${hhmm(r.created_at)}</td><td class="num">${money(r.total)}</td><td>${{ cash: "نقدي", card: "بطاقة", credit: "على الحساب", transfer: "المحفظة الأصلية" }[r.refund_method]}${r.channel ? ` · ${esc(r.channel)}` : ""}</td>
      <td>${esc(r.reason)}</td><td>${esc(r.by_name || "")}</td><td><a class="btn small secondary" href="/return-receipt/${esc(r.id)}" target="_blank">🖨 إيصال المرتجع</a></td></tr>`).join("")}</tbody></table></section>` : ""}
  ${s.paid_transfer || s.attachments.length ? `<section class="panel"><h2>صورة التحويل ${s.paid_transfer && !s.attachments.length ? `<span class="badge b-warn">⚠ لا توجد صورة</span>` : ""}</h2>
    <div class="proofs">${s.attachments.map((a) => `<figure>${a.mime_type === "application/pdf" ? `<a href="/api/attachments/${esc(a.id)}" target="_blank">📄 PDF</a>`
      : `<a href="/api/attachments/${esc(a.id)}" target="_blank"><img src="/api/attachments/${esc(a.id)}" alt="صورة التحويل" loading="lazy"></a>`}
      <figcaption><span class="badge ${{ ok: "b-ok", problem: "b-err", pending: "b-warn" }[a.review]}">${{ ok: "✓ تمت مراجعتها", problem: "✕ بها مشكلة", pending: "⚠ بانتظار المراجعة" }[a.review]}</span>
        ${a.review === "ok" && a.confirmed_amount != null ? `<div><span>المبلغ الذي وصل:</span> ${money(a.confirmed_amount)} ج</div>` : ""}${a.review_note ? `<div>${esc(a.review_note)}</div>` : ""}<div class="muted small">${esc(a.by_name || "")}${a.reviewed_name ? ` · راجعها ${esc(a.reviewed_name)}` : ""}</div>
        ${can("sales.totals") && a.review !== "ok" ? `<button class="btn small" data-rvok="${esc(a.id)}">✓ وصل المبلغ</button>
          <button class="btn small secondary" data-rvpart="${esc(a.id)}">وصل جزء منه</button>` : ""}
        ${can("sales.totals") && a.review !== "problem" ? `<button class="btn small secondary" data-rvbad="${esc(a.id)}">✕ بها مشكلة</button>` : ""}</figcaption></figure>`).join("")}</div>
    ${s.status !== "voided" && can("sales.create") ? `<label class="f mt">أضف صورة التحويل <input type="file" id="proofUp" accept="image/*,application/pdf"></label>` : ""}</section>` : ""}
  <section class="panel"><h2>الأصناف</h2><div class="tablewrap"><table><thead><tr><th>الصنف</th><th>الكمية</th><th>سعر الجمهور</th><th>قبل الخصم</th><th>الخصم</th><th>الصافي</th>
    <th>التشغيلات</th>${s.status === "completed" && can("sales.return") ? "<th>كمية المرتجع (بالوحدة)</th>" : ""}</tr></thead><tbody>
    ${s.lines.map((l) => `<tr><td>${esc(l.item.name)}</td>
      <td>${l.official_prices.map((p) => salePriceQty(p, l.item)).join("<br>") || esc(fq(l.qty_base, l))}</td>
      <td class="num">${l.official_prices.map((p) => money(p.price)).join("<br>")}</td>
      <td class="num">${l.official_prices.map((p) => money(p.gross)).join("<br>")}</td>
      <td class="num">${money(l.discount)}${l.invoice_share ? `<div class="muted small">من خصم الفاتورة: ${money(l.invoice_share)}</div>` : ""}</td>
      <td class="num">${money(l.line_total ?? l.net)}</td>
      <td class="muted">${l.allocations.map((a) => `${esc(a.expiry_date ? a.expiry_date.slice(0, 7) : "—")}: ${a.qty}${a.returned_qty ? ` (المُرتجع ${a.returned_qty})` : ""}`).join("، ")}</td>
      ${s.status === "completed" && can("sales.return") ? `<td>${returnable(l) ? `<input type="number" min="0" max="${returnable(l)}" step="1" value="" placeholder="0" inputmode="numeric" data-ret="${l.id}" class="narrow">
        <span class="muted">من ${returnable(l)}</span>` : `<span class="muted">إرجاع الكل</span>`}</td>` : ""}</tr>`).join("")}
    </tbody></table></div>
    ${s.status === "completed" && can("sales.return") ? `<div class="row mt"><label class="f">طريقة الاسترداد<select id="rMethod">
      ${refundOptions(s, true).map((o) => `<option value="${o.value}" ${o.selected ? "selected" : ""} ${o.disabled ? "disabled" : ""}>${esc(o.label)}</option>`).join("")}</select></label>
      ${wallets.length ? `<label id="rWalletChoice" class="f" hidden>المحفظة الأصلية<select id="rWallet">
        ${wallets.map(([id, name]) => `<option value="${esc(id || "")}">${esc(name || "تحويل من غير قناة مسجلة")}</option>`).join("")}
      </select><small class="muted">الرد في حدود التحويل المؤكد وصوله</small></label>` : ""}
      <label class="f grow">سبب المرتجع<input id="rReason" placeholder="مثال: العميل غيّر رأيه"></label>
      <button class="btn" id="rGo">تسجيل المرتجع</button></div>` : ""}
    ${s.status === "completed" && can("sales.void") && !s.returned_total && !(s.returns || []).length ? `<div class="row mt"><label class="f grow">إلغاء الفاتورة (في اليوم نفسه فقط) — السبب
      <input id="vReason"></label><button class="btn danger" id="vGo">إلغاء الفاتورة</button></div>` : ""}
  </section>`;
  // owner 18/11: the field started at 0 and typing 3 gave 30; it starts empty and a click selects what is there
  app.querySelectorAll("[data-ret]").forEach((i) => i.addEventListener("focus", () => i.select()));
  const showWallet = () => {
    const choice = $("#rWalletChoice");
    if (choice) choice.hidden = $("#rMethod").value !== "transfer";
  };
  $("#rMethod")?.addEventListener("change", showWallet);
  showWallet();
  $("#rGo")?.addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    if (btn.disabled) return;                 // a second click while the first return is saving (#114)
    const lines = {};
    app.querySelectorAll("[data-ret]").forEach((i) => { const v = typedWhole(i.value); if (Number(v) > 0) lines[i.dataset.ret] = v; });
    if (!Object.keys(lines).length) { toast("اكتب كمية المرتجع", true); return; }
    btn.disabled = true;
    try {
      const refund_method = $("#rMethod").value;
      const channel_id = refund_method === "transfer" ? $("#rWallet")?.value || null : null;
      const r = await api(`/api/sales/${id}/return`, { method: "POST", body: { lines, refund_method, channel_id, reason: $("#rReason").value } });
      toast(`تم تسجيل المرتجع: ${money(r.refund)} ج${r.change_back ? ` + رجّع له نقدي ${money(r.change_back)} ج باقي كان سايبه رصيد (المجموع ${money(r.refund + r.change_back)} ج)` : ""} — اطبع إيصاله من "المرتجعات" أسفل`); viewSale(id);
    } catch (e) { btn.disabled = false; toast(e.message, true); }
  });
  $("#proofUp")?.addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api(`/api/sales/${id}/attachments`, { method: "POST", form: fd }); toast(r.duplicate_of ? `⚠ هذه الصورة نفسها مسجلة مع الفاتورة ${r.duplicate_of}` : "✓ تم حفظ الصورة", !!r.duplicate_of); viewSale(id); }
    catch (err) { toast(err.message, true); }
  });
  app.querySelectorAll("[data-rvok]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/attachments/${b.dataset.rvok}/review`, { method: "POST", body: { ok: true } }); viewSale(id); } catch (err) { toast(err.message, true); }
  }));
  // only part of the transfer arrived: the amount confirmed is what counts as received (#324 M2-01)
  app.querySelectorAll("[data-rvpart]").forEach((b) => b.addEventListener("click", async () => {
    const amount = toPiasters(prompt("كم وصل من التحويل؟ (ج)") || "");
    if (!(amount > 0)) return;
    try { await api(`/api/attachments/${b.dataset.rvpart}/review`, { method: "POST", body: { ok: true, amount } }); viewSale(id); } catch (err) { toast(err.message, true); }
  }));
  app.querySelectorAll("[data-rvbad]").forEach((b) => b.addEventListener("click", async () => {
    const note = prompt("ما المشكلة؟ (مثال: المبلغ لم يصل / المبلغ أقل)"); if (!note) return;
    try { await api(`/api/attachments/${b.dataset.rvbad}/review`, { method: "POST", body: { ok: false, note } }); viewSale(id); } catch (err) { toast(err.message, true); }
  }));
  $("#vGo")?.addEventListener("click", async () => {
    if (!confirmInline($("#vGo"))) return;
    try { await api(`/api/sales/${id}/void`, { method: "POST", body: { reason: $("#vReason").value } }).then((v) => { toast(v.change_back ? `تم إلغاء الفاتورة — رجّع للعميل كمان ${money(v.change_back)} ج نقدي (باقي كان سايبه رصيد)` : "تم إلغاء الفاتورة"); }); viewSale(id); }
    catch (e) { toast(e.message, true); }
  });
}
// two-click confirmation without browser dialogs: 10 s to confirm; the confirmed click disarms at once,
// so a third quick click is a new first click (never the same action twice)
function confirmInline(btn, ms = 10000) {
  clearTimeout(btn._confirmT);
  const restore = () => { delete btn.dataset.armed; btn.textContent = btn.dataset.label; };
  if (btn.dataset.armed) { restore(); return true; }
  btn.dataset.armed = "1"; btn.dataset.label = btn.textContent; btn.textContent = "متأكد؟ اضغط مرة أخرى";
  btn._confirmT = setTimeout(restore, ms);
  return false;
}

// ------------------------------------------------------------------ dead stock
async function viewDeadstock() {
  app.innerHTML = `
  <section class="panel">
    <h1>الرواكد</h1>
    <p class="muted">إلى أن ينتقل البيع إلى هذا البرنامج، تكون المبيعات على E-Stock. صدّر هذه التقارير الثلاثة (Excel - Data Only) وارفعها هنا. لا يُحفظ أي شيء.</p>
    <form id="dsForm" class="row">
      <label class="f">1) كميات الأصناف طبقًا لتواريخ الصلاحية<input type="file" name="stock" accept=".xlsx" required></label>
      <label class="f">2) حركة بيع الأصناف — آخر 12 شهر (عدد الأصناف 99999)<input type="file" name="sales" accept=".xlsx" required></label>
      <label class="f">3) فواتير المشتريات بالأصناف — نفس الفترة<input type="file" name="purchases" accept=".xlsx" required></label>
      <label class="f">المخزن<input name="store" value="الصيدلية"></label>
      <div class="row"><button class="btn" type="submit">احسب</button>
        <button class="btn secondary" type="button" id="dsXlsx" disabled>تنزيل Excel</button></div>
    </form>
  </section>
  <div id="dsOut"></div>`;
  const form = $("#dsForm");
  const SEG = { dead: "ديد ستوك", stagnant: "راكد", slow: "بطيء", new: "جديد" };
  const HINT = { dead: "لم يُبع خلال الفترة، وآخر شراء منذ 6 أشهر أو أكثر", stagnant: "لم يُبع، واشتُري منذ 3 إلى 6 أشهر",
    slow: "يُباع، لكن الرصيد يكفي أكثر من سنة", new: "اشتُري في آخر 3 أشهر وما زال يأخذ فرصته" };
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#dsOut").innerHTML = `<section class="panel"><p class="muted">جارٍ الحساب…</p></section>`;
    try {
      const r = await api("/api/procurement/deadstock", { method: "POST", form: new FormData(form) });
      $("#dsXlsx").disabled = false;
      const tbl = (rows) => `<div class="tablewrap"><table><thead><tr><th>الصنف</th><th>الرصيد</th><th>التكلفة المحبوسة</th>
        <th>بيع الفترة</th><th>يكفي (شهر)</th><th>أقرب صلاحية</th><th>آخر شراء</th><th>المورد</th></tr></thead><tbody>
        ${rows.map((x) => `<tr><td>${esc(x.name)}<div class="raw">${esc(x.company || "")} · ${esc(x.code)}</div></td><td>${x.qty}</td>
        <td><b>${x.value.toFixed(2)}</b></td><td>${x.sold_in_period || 0}</td><td>${x.months_of_cover ?? "—"}</td>
        <td>${esc(x.nearest_expiry || "غير مسجلة")}</td><td>${esc(x.last_purchase || "قبل الفترة")}</td><td>${esc(x.supplier || x.note || "")}</td></tr>`).join("")}
        </tbody></table></div>`;
      $("#dsOut").innerHTML = `
      <section class="panel"><h2>الملخص — بتاريخ ${esc(r.as_of)} (قيمة المخزون ${r.stock_value.toFixed(2)} ج)</h2>
        <div class="totals">${r.summary.map((s) => `<div>${esc(s.label)}<b>${s.value.toFixed(0)} ج</b><span class="muted">${s.items} صنف · ${s.share}%</span></div>`).join("")}
        <div>أخطاء مشتريات<b>${r.purchase_mistakes.value.toFixed(0)} ج</b><span class="muted">${r.purchase_mistakes.items} صنف اشتُري ولم يُبع</span></div>
        <div>منتهي<b>${r.expiry.expired_value.toFixed(0)} ج</b><span class="muted">وخلال 6 أشهر ${r.expiry.within_6_months_value.toFixed(0)} ج</span></div></div>
        <p class="muted">الموسمي (برد، حساسية، شمس) والأدوية المزمنة غير مستبعدة: راجعها قبل البيع.</p></section>
      ${Object.keys(SEG).map((k) => `<section class="panel"><details ${k === "dead" ? "open" : ""}><summary><b>${SEG[k]}</b> — ${r.segments[k].length} صنف
        <span class="muted">(${HINT[k]})</span></summary>${tbl(r.segments[k])}</details></section>`).join("")}`;
    } catch (err) { $("#dsOut").innerHTML = ""; toast(err.message, true); }
  });
  $("#dsXlsx").addEventListener("click", async () => {
    const res = await fetch("/api/procurement/deadstock?format=xlsx", { method: "POST", body: new FormData(form),
      headers: { "X-Requested-With": "pharm" }, credentials: "same-origin" });
    if (!res.ok) { toast("التنزيل فشل", true); return; }
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: "الرواكد.xlsx" });
    document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  });
}

// ------------------------------------------------------------------ Hikma claim (U3-3)
async function viewHikmaClaim() {
  const t = new Date(), first = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-01`;
  app.innerHTML = `<section class="panel"><h1>مطالبة مستشفى نور الحكمة</h1>
    <div class="row"><label class="f">الفرع <select id="hkB"><option value="shibin">شبين</option><option value="mitkhalaf">ميت خلف</option></select></label>
      <label class="f">من <input type="date" id="hkF" value="${first}"></label><label class="f">إلى <input type="date" id="hkT" value="${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}"></label>
      <button class="btn" id="hkGo">اعرض</button><button class="btn secondary" id="hkXH" disabled>Excel للمستشفى</button><button class="btn secondary" id="hkXI" disabled>Excel داخلي</button></div>
    <p class="muted">مخزن الصيدلية: التكلفة + نص الربح. مخزن المستشفى: نص الربح بس. نصيب المستشفى من الربح بيتقرّب مرة واحدة على الإجمالي. ملف «المستشفى» بس هو اللي يتبعت (من غير تكلفة).</p>
    <details id="hkSet"><summary>عميل كل فرع والدفعة المقدمة</summary><div id="hkSetBox" class="muted">…</div></details>
    <div id="hkOut"></div></section>`;
  let R = null;
  // review P1: the owner picks each branch's customer (E-Stock codes 50 / 10 are only the default) and the advance
  const drawSet = async () => {
    const st = await api("/api/settings/hikma-claim");
    $("#hkSetBox").className = "";
    $("#hkSetBox").innerHTML = Object.entries(st).map(([k, v]) => `<div class="row" data-b="${esc(k)}"><b>${esc(v.title)}</b>
      <input data-cq placeholder="دوّر على العميل" autocomplete="off"><span data-cn class="muted">${v.customer_id ? "✓ متحدد" : "⚠ مش متحدد"}</span>
      <label class="f">الدفعة المقدمة (ج) <input data-adv type="number" min="0" step="0.01" value="${money(v.advance)}"></label>
      <button class="btn small" data-save>حفظ</button><div data-cr class="cands"></div></div>`).join("");
    $("#hkSetBox").querySelectorAll("[data-b]").forEach((row) => {
      let cid = st[row.dataset.b].customer_id;
      row.querySelector("[data-cq]").addEventListener("input", async (e) => {
        const v = e.target.value.trim(); if (v.length < 2) return;
        const cs = await api(`/api/customers?q=${encodeURIComponent(v)}`);
        row.querySelector("[data-cr]").innerHTML = cs.slice(0, 8).map((c) => `<button class="btn small secondary" data-c="${esc(c.id)}">${esc(c.name)}</button>`).join("");
        row.querySelectorAll("[data-c]").forEach((b) => b.addEventListener("click", () => { cid = b.dataset.c; row.querySelector("[data-cn]").textContent = `✓ ${b.textContent}`; row.querySelector("[data-cr]").innerHTML = ""; }));
      });
      row.querySelector("[data-save]").addEventListener("click", async () => {
        try { await api("/api/settings/hikma-claim", { method: "PUT", body: { branch: row.dataset.b, customer_id: cid, advance: toPiasters(row.querySelector("[data-adv]").value) || 0 } }); toast("اتحفظ"); }
        catch (er) { toast(er.message, true); }
      });
    });
  };
  $("#hkSet").addEventListener("toggle", () => { if ($("#hkSet").open) drawSet().catch((er) => toast(er.message, true)); });
  const rowsOf = (part) => part.lines.map((l) => `<tr class="${l.below_cost || l.no_cost ? "has-err" : ""}"><td>${esc(l.name || "")}${l.no_cost ? ` <span class="badge b-warn">⚠ من غير تكلفة</span>` : ""}</td><td class="num">${l.qty}</td><td class="num">${money(l.value)}</td><td class="num">${money(l.cost)}</td><td class="num">${money(l.margin)}</td><td class="num">${pct(l.margin_bp)}%</td></tr>`).join("");
  const sec = (title, part, dueLabel) => `<h2>${title}</h2>${part.lines.length ? `<table><thead><tr><th>الصنف</th><th>الكمية</th><th>القيمة</th><th>التكلفة</th><th>الربح</th><th>%</th></tr></thead><tbody>${rowsOf(part)}</tbody>
    <tfoot><tr><th>الإجمالي (${part.lines.length} صنف)</th><th></th><th class="num">${money(part.value)}</th><th class="num">${money(part.cost)}</th><th class="num">${money(part.margin)}</th><th></th></tr></tfoot></table>
    <p>نصيب المستشفى من الربح ${money(part.hospital_half)} · <b>${dueLabel} ${money(part.due)}</b></p>` : `<p class="muted">مفيش</p>`}`;
  const load = async () => {
    R = await api(`/api/reports/hikma-claim?branch=${$("#hkB").value}&from=${$("#hkF").value}&to=${$("#hkT").value}`);
    const a = R.account;
    $("#hkOut").innerHTML = `<h2>${esc(R.title)} — ${esc(R.from)} إلى ${esc(R.to)} (${R.invoices} فاتورة)</h2>
      ${R.no_cost_lines ? `<p class="t-err">⚠ ${R.no_cost_lines} صنف من غير تكلفة متسجلة: التكلفة اتحسبت صفر. راجعها قبل ما تبعت المطالبة.</p>` : ""}
      <p class="muted small">المطالبة صافية من أي مرتجع لحد النهارده. لو طلعت مطالبة فترة قديمة تاني، مرتجعاتها اللي اتخصمت في فترة بعدها هتتخصم مرتين.</p>
      ${sec("مخزن الصيدلية", R.pharmacy, "المطلوب")}${sec("مخزن مستشفيات الحكمة", R.hospital, "نصيبنا")}
      ${R.previous_returns.rows.length ? `<p class="t-warn">يُخصم: مرتجعات عن مطالبات سابقة (${R.previous_returns.rows.length}): ${money(R.previous_returns.due)}</p>` : ""}
      <div class="totals"><div>إجمالي مستحقات الفترة<b>${money(R.due)}</b></div><div>− الدفعة المقدمة<b>${money(R.advance)}</b></div>
        <div>صافي الفترة<b>${money(R.net)}</b></div><div>+ دفعة الفترة الجاية<b>${money(R.next_advance)}</b></div><div>إجمالي المطلوب<b>${money(R.total)}</b></div></div>
      <p class="${a.matched_bp === 10000 ? "t-ok" : "t-warn"}">مطابقة حساب العميل: الفواتير على الحساب ${money(a.debit)} − مرتجع ${money(a.returns)} = ${money(a.net)} · قيمة المطالبة ${money(R.pharmacy.value + R.hospital.value)} · التطابق ${pct(a.matched_bp)}%${a.paid_not_on_account ? ` · اتدفع نقدي/بطاقة ومش على الحساب ${money(a.paid_not_on_account)}` : ""}</p>`;
    $("#hkXH").disabled = $("#hkXI").disabled = false;
  };
  const sheets = (internal) => {
    const sh = [];
    for (const [k, title] of [["pharmacy", "مخزن الصيدلية"], ["hospital", "مخزن المستشفى"]]) {
      const p = R[k];
      if (!p.lines.length) continue;
      sh.push(internal
        ? { name: title, columns: [Tx("الصنف"), N("الكمية"), M("القيمة"), M("التكلفة"), M("الربح"), Tx("ملاحظة")], rows: p.lines.map((l) => [l.name, l.qty, l.value, l.cost, l.margin, l.no_cost ? "من غير تكلفة" : ""]), total: ["الإجمالي", null, p.value, p.cost, p.margin, ""] }
        : { name: title, columns: [Tx("الصنف"), N("الكمية"), M("الإجمالي")], rows: p.lines.map((l) => [l.name, l.qty, l.value]), total: ["الإجمالي", null, p.value] });
    }
    const sum = [["مخزن الصيدلية: المطلوب", R.pharmacy.due], ["مخزن المستشفى: نصيب الصيدلية", R.hospital.due]];
    if (R.previous_returns.rows.length) sum.push(["يُخصم: مرتجعات عن مطالبات سابقة", -R.previous_returns.due]);
    sum.push(["إجمالي مستحقات الفترة", R.due], ["الدفعة المقدمة", -R.advance], ["صافي الفترة", R.net], ["دفعة الفترة الجاية", R.next_advance], ["إجمالي المطلوب", R.total]);
    sh.push({ name: "الملخص", columns: [Tx("البند"), M("المبلغ")], rows: sum });
    return sh;
  };
  $("#hkGo").addEventListener("click", () => load().catch((er) => toast(er.message, true)));
  $("#hkXH").addEventListener("click", () => exportXlsx(`مطالبة ${R.title} ${R.from} - ${R.to}`, `${R.from} إلى ${R.to}`, sheets(false)));
  $("#hkXI").addEventListener("click", () => exportXlsx(`مطالبة داخلي ${R.title} ${R.from} - ${R.to}`, `${R.from} إلى ${R.to} — داخلي، ممنوع يتبعت للمستشفى`, sheets(true)));
}

// ------------------------------------------------------------------ dispensing notes (U2-21)
async function viewDispensingNotes() {
  const draw = (notes) => {
    app.innerHTML = `<section class="panel"><h1>ملاحظات الصرف</h1>
      <p class="muted">ملاحظة بتظهر للبائع مرة واحدة في الفاتورة لما يكون فيها صنف بالمادة الفعالة دي. مبتوقفش البيع.</p>
      <table><thead><tr><th>المادة الفعالة</th><th>شغالة</th><th>اللي بيظهر (سطر لكل قاعدة)</th><th></th></tr></thead><tbody>
      ${Object.entries(notes).map(([k, v]) => `<tr data-k="${esc(k)}"><td><b>${esc(k)}</b>${v.confirmed ? "" : ` <span class="badge b-warn">محتاج تأكيد</span>`}${v.source ? `<div class="muted small">${esc(v.source)}</div>` : ""}</td>
        <td><input type="checkbox" data-on ${v.enabled ? "checked" : ""}></td>
        <td><textarea data-rules rows="3">${esc((v.rules || []).join("\n"))}</textarea></td>
        <td><button class="btn small" data-save>حفظ</button></td></tr>`).join("")}</tbody></table>
      <div class="row"><input id="dnNew" placeholder="مادة فعالة جديدة (بالإنجليزي)"><button class="btn secondary" id="dnAdd">إضافة (هتبدأ مقفولة)</button></div></section>`;
    document.querySelectorAll("[data-save]").forEach((b) => b.addEventListener("click", async () => {
      const tr = b.closest("tr");
      try {
        draw(await api("/api/settings/dispensing-notes", { method: "PUT", body: { notes: { [tr.dataset.k]: {
          enabled: tr.querySelector("[data-on]").checked, rules: tr.querySelector("[data-rules]").value.split("\n") } } } }));
        toast("اتحفظ");
      } catch (er) { toast(er.message, true); }
    }));
    $("#dnAdd").addEventListener("click", async () => {
      const k = $("#dnNew").value.trim();
      if (!k) return;
      try { draw(await api("/api/settings/dispensing-notes", { method: "PUT", body: { notes: { [k]: { enabled: false } } } })); } catch (er) { toast(er.message, true); }
    });
  };
  draw(await api("/api/settings/dispensing-notes"));
}

// ------------------------------------------------------------------ item data health (U3-1)
// Correcting a wrong pack size of an item with stock: the server converts every lot (boxes stay boxes) and refuses
// an open box, a draft sale or an open count. Nothing changes until the person confirms with a reason.
async function packFix(id, suggest, strips = false) {
  const current = onThisScreen();
  const v = typedWhole(prompt(strips ? PACK_QUESTION : "عدد الوحدات الصحيح في العلبة:", suggest ? String(suggest) : "") || "");
  if (!(Number(v) > 0)) return false;
  try {
    const p = await api(`/api/items/${id}/pack-fix?pack=${encodeURIComponent(v)}`);
    if (strips && p.drafts) {
      toast("شيل الصنف من الفاتورة المفتوحة، وبعدها صحّح العبوة من نتيجة البحث أو بطاقة الصنف وأضفه تاني.", true);
      return false;
    }
    const unit = strips ? (prompt("اسم الوحدة اللي بتتباع من العلبة", p.unit === "unit" ? "شريط" : p.unit) || "").trim() : p.unit;
    if (!unit) return false;
    const lines = [`العبوة هتتغير من ${p.old_pack} لـ ${p.new_pack} ${unit}.`,
      ...p.lots.map((l) => `رصيد ${l.qty} ← ${l.new_qty} ${unit} (نفس عدد العلب)`)];
    if (p.not_whole.length) lines.push("⚠ فيه علبة مفتوحة مش هتتحول لوحدات كاملة: اعمل جرد للصنف الأول.");
    if (p.drafts) lines.push(`⚠ الصنف في ${p.drafts} فاتورة بيع مفتوحة: اقفلها الأول.`);
    if (p.counting) lines.push("⚠ الصنف في جرد مفتوح: اعتمده أو الغيه الأول.");
    if (p.open_credit) lines.push(`⚠ فيه ${p.open_credit} فاتورة آجل لعملاء عليهم رصيد اتباع فيها الصنف بالعبوة القديمة: راجع مبالغها مع العملاء (البرنامج مش هيغيّرها).`);
    lines.push("فواتير البيع اللي قبل التصحيح مش هتترجع ولا تتلغي، والتشغيلات القديمة مش هترجع للمورد؛ أي فرق يتعمل بتسوية أو جرد.");
    if (!confirm(lines.join("\n"))) return false;
    const reason = (prompt("سبب التصحيح (إجباري):", "العبوة متسجلة غلط") || "").trim();
    if (reason.length < 3) { toast("اكتب السبب", true); return false; }
    const body = { pack_size: Number(v), reason, expected_pack: p.old_pack };
    if (strips) { body.expected_pack = 1; body.base_unit_name = unit; }
    await api(`/api/items/${id}/pack-fix`, { method: "POST", body });
    if (current()) toast("اتصححت العبوة والرصيد اتحوّل");
    return current();
  } catch (er) { if (current()) toast(er.message, true); return false; }
}

async function viewPackReview() {
  if (ME.role !== "owner") { app.textContent = "المراجعة دي للمالك فقط"; return; }
  const { items } = await api("/api/estock/pack-review");
  const current = onThisScreen();
  app.innerHTML = `<section class="panel"><a href="#/items">← الأصناف</a><h1>${PACK_REVIEW_TITLE}</h1>
    <p class="muted">القائمة مرتبة بقيمة الرصيد بسعر التكلفة. العدد في الاسم يساعدك تلاقي الصنف. راجع العلبة نفسها قبل اعتماد الوحدة اللي بتتباع.</p>
    <div id="packReviewRows">${items.map((it) => `<form class="row" data-item="${esc(it.id)}">
      <div class="grow"><a href="#/items/${esc(it.id)}">${itemHtml(it)}</a> <bdi>${esc(it.code || "")}</bdi>
        <div class="muted">العدد في الاسم: ${it.printed_count} · الرصيد: ${it.stock_qty} · قيمة الرصيد: ${it.stock_value == null ? "التكلفة غير متاحة" : `${money(it.stock_value)} ج`}</div></div>
      <label class="f">عدد الوحدات المباعة في العلبة<input name="size" type="number" min="1" max="10000" step="1" placeholder="1 للعلبة الكاملة" required></label>
      <label class="f">اسم الوحدة الصغيرة<input name="unit" maxlength="30" placeholder="شريط / أمبول / كيس"
        value="${esc(it.base_unit_name === "unit" ? "" : it.base_unit_name)}"></label>
      <label class="f">السبب<input name="reason" minlength="3" maxlength="200" required></label>
      <button class="btn small">اعتمد العبوة</button><span class="t-err small" role="status"></span>
    </form>`).join("")}</div><p id="packReviewEmpty" ${items.length ? "hidden" : ""}>مفيش عبوات محتاجة مراجعة.</p>
    <p class="muted">لو العدد اتغيّر، الرصيد بيتحوّل مع الحفاظ على عدد العلب. مرتجعات الفواتير والتشغيلات القديمة بتحتاج تسوية أو جرد.</p></section>`;
  for (const form of $("#packReviewRows").querySelectorAll("form")) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const [button, status] = [form.querySelector("button"), form.querySelector("[role=status]")];
      withBusy(button, async () => {
        const id = form.dataset.item, size = Number(form.elements.size.value);
        const reason = form.elements.reason.value.trim(), unit = form.elements.unit.value.trim();
        status.textContent = "";
        if (size > 1 && !unit) { status.textContent = "اكتب اسم الوحدة الصغيرة"; return; }
        try {
          await api(`/api/estock/pack-review/${id}`, { method: "POST",
            body: { reason, pack_size: size, base_unit_name: unit || null } });
          if (!current() || !form.isConnected) return;
          form.remove();
          $("#packReviewEmpty").hidden = !!$("#packReviewRows").children.length;
          toast("تم اعتماد العبوة");
        } catch (err) { if (current()) status.textContent = err.message; }
      });
    });
  }
}

async function viewCustomerNameReview() {
  if (ME.role !== "owner") { app.textContent = "المراجعة دي للمالك فقط"; return; }
  const { customers } = await api("/api/estock/customer-name-review");
  const current = onThisScreen();
  app.innerHTML = `<section class="panel"><a href="#/customers">← العملاء</a><h1>${NAME_REVIEW_TITLE}</h1>
    <div id="nameReviewRows">${customers.map((row) => `<form class="row">
      <div class="grow"><a href="#/customers/${esc(row.customer_id)}">${esc(row.current_name)}</a> <bdi>${esc(row.estock_code || "")}</bdi>
        ${row.name_ar ? `<div>إي ستوك (عربي): <bdi>${esc(row.name_ar)}</bdi></div>` : ""}
        ${row.name_en ? `<div>إي ستوك (إنجليزي): <bdi>${esc(row.name_en)}</bdi></div>` : ""}
        ${row.history.length ? `<div>في فواتير البيع: ${row.history.map((n) => `<bdi>${esc(n)}</bdi>`).join(" · ")}</div>` : ""}</div>
      <label class="f grow">الاسم المعتمد<select name="picked" required>
        <option value="">اختر الاسم</option>${row.options.map((name) =>
          `<option value="${esc(name)}">${esc(name)}${name === row.current_name ? " (الاسم الحالي)" : ""}</option>`).join("")}</select></label>
      <button class="btn small">اعتمد الاسم</button><span class="t-err small" role="status"></span>
    </form>`).join("")}</div><p id="nameReviewEmpty" ${customers.length ? "hidden" : ""}>مفيش أسماء محتاجة مراجعة في الأرشيف المتاح.</p></section>`;
  [...$("#nameReviewRows").querySelectorAll("form")].forEach((form, i) => {
    const row = customers[i];
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const button = form.querySelector("button");
      withBusy(button, async () => {
        try {
          await api(`/api/estock/customer-name-review/${row.customer_id}`, { method: "POST",
            body: { name: form.elements.picked.value, current_name: row.current_name } });
          if (!current() || !form.isConnected) return;
          form.remove();
          $("#nameReviewEmpty").hidden = !!$("#nameReviewRows").children.length;
          toast("تم اعتماد اسم العميل");
        } catch (err) { if (current()) form.querySelector("[role=status]").textContent = err.message; }
      });
    });
  });
}

const IH_KEYS = { pack_vs_name: "العبوة مش زي الاسم", unit_price_split: "سعر الوحدة × العدد ≠ سعر العلبة", no_public_price: "مفيش سعر جمهور",
  price_vs_purchase: "السعر بعيد عن آخر شراء", cost_above_public: "التكلفة أعلى من السعر", duplicate_name: "اسم مكرر",
  barcode_is_other_code: "باركود = كود صنف تاني", stock_no_expiry: "رصيد من غير صلاحية", negative_stock: "رصيد بالسالب" };
async function viewItemHealth() {
  app.innerHTML = `<section class="panel"><div class="row between"><h1>فحص بيانات الأصناف</h1><button class="btn secondary" id="ihRe">افحص تاني</button></div>
    <p class="muted">البرنامج بيدوّر على الأخطاء في بيانات الأصناف ومبيغيّرش حاجة لوحده. «صحّح» بيفتح بطاقة الصنف وانت اللي بتقرر.</p>
    <div id="ihChips" class="row"></div><div id="ihOut" class="muted">بيفحص…</div></section>`;
  let only = "";
  const load = async () => {
    const r = await api("/api/reports/item-health");
    const draw = () => {
      $("#ihChips").innerHTML = `<button class="btn small ${only ? "secondary" : ""}" data-k="">الكل (${r.rows.length})</button>`
        + Object.entries(r.counts).map(([k, n]) => `<button class="btn small ${only === k ? "" : "secondary"}" data-k="${esc(k)}">${esc(IH_KEYS[k] || k)} (${n})</button>`).join("");
      const rows = r.rows.filter((x) => !only || x.key === only);
      $("#ihOut").className = "";
      $("#ihOut").innerHTML = rows.length ? `<table><thead><tr><th>الصنف</th><th>المشكلة</th><th>التفاصيل</th><th></th></tr></thead><tbody>
        ${rows.map((x) => `<tr><td>${esc(x.name)}${x.code ? ` <span class="muted small">${esc(x.code)}</span>` : ""}</td>
          <td><span class="badge ${x.status === "bad" ? "b-err" : "b-warn"}">${esc(IH_KEYS[x.key] || x.key)}</span></td>
          <td>${esc(x.text)}${x.open_credit ? `<div class="t-warn small">⚠ ${x.open_credit} فاتورة آجل لعملاء عليهم رصيد اتباع فيها بالعبوة دي</div>` : ""}</td>
          <td class="row"><a class="btn small" href="#/items/${esc(x.item_id)}/edit">صحّح</a>${x.key === "pack_vs_name" && ME.permissions.includes("inventory.adjust") && ME.permissions.includes("items.write")
            ? `<button class="btn small secondary" data-fix="${esc(x.item_id)}" data-s="${esc((x.suggest || [])[0] || "")}">صحّح العبوة</button>` : ""}</td></tr>`).join("")}</tbody></table>`
        : `<p class="t-ok">مفيش مشاكل${only ? " من النوع ده" : ""} في ${r.items_checked} صنف.</p>`;
      document.querySelectorAll("#ihChips [data-k]").forEach((b) => b.addEventListener("click", () => { only = b.dataset.k; draw(); }));
      document.querySelectorAll("#ihOut [data-fix]").forEach((b) => b.addEventListener("click", async () => {
        if (await packFix(b.dataset.fix, b.dataset.s)) load().catch((er) => toast(er.message, true));
      }));
    };
    draw();
  };
  $("#ihRe").addEventListener("click", () => load().catch((er) => toast(er.message, true)));
  load().catch((er) => { $("#ihOut").textContent = er.message; });
}

// ------------------------------------------------------------------ day review (U2-8)
async function viewDayReview() {
  app.innerHTML = `<section class="panel"><div class="row between"><h1>مراجعة اليوم</h1>
    <label class="f">اليوم <input type="date" id="drDay"></label></div><div id="drOut" class="muted">…</div></section>`;
  const load = async () => {
    const v = $("#drDay").value, r = await api(`/api/reports/day-review${v ? `?day=${encodeURIComponent(v)}` : ""}`);
    if (!v) $("#drDay").value = r.day;
    const row = (t, m) => `<tr><td>${t}</td><td class="num">${m == null ? "—" : money(m)}</td></tr>`, s = r.sales, mth = r.by_method;
    $("#drOut").className = "";
    $("#drOut").innerHTML = `<div class="totals"><div>صافي المبيعات<b>${money(r.net_sales)}</b></div><div>عدد الفواتير<b>${s.n}</b></div>
        <div>الشراء<b>${r.purchases.total == null ? "—" : money(r.purchases.total)}</b></div><div class="${r.drawer.difference ? (r.drawer.difference < 0 ? "t-err" : "t-warn") : "t-ok"}">فرق الخزينة<b>${money(r.drawer.difference)}</b></div></div>
      <div class="grid2"><table><tbody>
        ${row("المبيعات", s.total)}${row("الخصومات", s.discount)}${s.rounding ? row("تقريب النقدي (مش مبيعات ولا خصم)", s.rounding) : ""}${row("المرتجعات", r.returns.total)}
        ${row(`ملغاة النهارده (${s.voided_count})`, s.voided_total)}${row("نقدي (بعد المرتجع والإلغاء)", mth.cash)}${row("بطاقة (بعد المرتجع والإلغاء)", mth.card)}
        ${row("تحويل ومحافظ", mth.transfer)}${row("آجل (بعد المرتجع والإلغاء)", mth.credit)}${row("نقدي توصيل (بيدخل الدرج لما المندوب يسلّم)", mth.delivery_cash)}${row("تحصيل آجل", r.credit_collected)}</tbody></table>
      <table><tbody>${row(`فواتير شراء معتمدة (${r.purchases.n})`, r.purchases.total)}
        ${r.purchases.by_supplier.map((x) => row(`— ${esc(x.supplier || "")} (${x.n})`, x.total)).join("")}
        ${row(`مرتجع للموردين (${r.supplier_returns.n})`, r.supplier_returns.total)}
        ${row("المتوقع في الخزينة", r.drawer.expected)}${row("المعدود", r.drawer.counted)}</tbody></table></div>
      ${r.drawer.shifts.map((x) => `<p class="muted small">شيفت اتقفل ${esc(stamp(x.closed_at))} — ${esc(x.closed_by || "")}: متوقع ${money(x.expected_cash)}، معدود ${money(x.counted_cash)}، فرق ${money(x.difference)}</p>`).join("")}`;
  };
  $("#drDay").addEventListener("change", () => load().catch((er) => toast(er.message, true)));
  load().catch((er) => { $("#drOut").textContent = er.message; });
}

// ------------------------------------------------------------------ procurement
const CLASS_AR = { regular: ["منتظم", "b-ok"], semi: ["شبه منتظم", "b-warn"], irregular: ["متقطع", "b-err"], dead: ["راكد", "b-err"] };
const FLAG_AR = { expensive: "غالي: موافقتك", fridge: "ثلاجة: موافقتك", below_official: "خصم أقل من الرسمي",
  below_last: "خصم أقل من آخر مرة", unsure_match: "المطابقة مش مؤكدة" };
const PR_PERIOD_AR = { 7: "أسبوع", 14: "أسبوعين", 30: "شهر" };
const PR_LINE_AR = { sent: "اتبعتت", arrived: "وصل", partial: "وصل جزء", na: "مش موجود عنده" };
const PR_CHIPS = [["asked", "مطلوبة (كشكول / عميل)"], ["ok", "محتاجة موافقتك"], ["zero", "رصيدها صفر"]];
const prChip = { asked: (r) => r.asked, ok: (r) => r.flags.length > 0, zero: (r) => r.on_hand <= 0 };
const prPct = (bp) => (bp === null || bp === undefined ? "—" : `${(bp / 100).toFixed(2)}%`);
// a whole number of boxes, the whole text (parseInt read "1.9" and "1e2" as 1, Codex P1 on #402)
const prPacks = (v) => { const t = String(v ?? "").trim(); return /^[0-9]{1,6}$/.test(t) && +t >= 1 ? +t : null; };
// kept on this PC per pharmacy and branch until exported or cleared (private window: screen only)
const prKey = (k) => `${k}:${(ME && ME.organization && ME.organization.id) || ""}:${(ME && ME.branch_id) || ""}`;
const prStore = (k) => ({
  get() { try { return JSON.parse(localStorage.getItem(prKey(k)) || "{}") || {}; } catch { return {}; } },
  set(v) { try { localStorage.setItem(prKey(k), JSON.stringify(v)); } catch { /* private window */ } },
});
const PR_ORDER = prStore("purchasePlan"), PR_PRICES = prStore("purchasePlanPrices");

async function viewProcurement() {
  app.innerHTML = `
  <section class="panel">
    <h1>أداة المشتريات</h1>
    <p class="muted">المنتظم: اتباع 6 شهور أو أكتر من آخر 12، وشهرين من آخر 3. شبه المنتظم: 4 أو 5 شهور. المتوسط الشهري هو الأكبر بين متوسط آخر 3 شهور وآخر 6.
      الكمية = المتوسط × أيام المدة ÷ 30 − الرصيد − اللي وصل ومش متسجل − اللي متطلب ولسه موصلش. الكشكول وطلبات العملاء داخلين دايمًا. البرنامج ما بيبعتش أي حاجة لأي مورد.</p>
    <form id="prForm" class="row">
      <label class="f">المدة <select id="prPeriod"><option value="7">أسبوع</option><option value="14">أسبوعين</option><option value="30">شهر</option></select></label>
      <label class="f">الصنف الغالي من (ج) <input name="expensive_from" type="number" value="300" min="0" step="0.01"></label>
      <label class="f"><input type="checkbox" id="prSupplies"> المستلزمات كمان</label>
      <label class="f"><input type="checkbox" id="prAll"> اعرض المتقطع والمخفي</label>
      <button class="btn" type="submit">احسب</button>
    </form>
    <div class="row" id="prChips">${PR_CHIPS.map(([k, t]) => `<button type="button" class="chip tgl" data-chip="${k}">${t}</button>`).join("")}
      <label class="f">المورد <select id="prSupSel"><option value="">كل الموردين</option></select></label></div>
    <p class="muted" id="prCounts"></p>
  </section>
  <section class="panel"><div class="tablewrap"><table>
    <thead><tr><th>الصنف</th><th>الحالة</th><th>الرصيد</th><th>وصل ومش متسجل</th><th>متطلب</th>
      <th class="prP" data-p="7">أسبوع</th><th class="prP" data-p="14">أسبوعين</th><th class="prP" data-p="30">شهر</th>
      <th>خصم كل مورد (آخر شراء)</th><th>تنبيه</th><th>الكمية (علبة)</th><th>المورد</th><th></th></tr></thead>
    <tbody id="prBody"><tr><td colspan="13" class="muted">جارٍ الحساب…</td></tr></tbody></table></div>
    <div class="row"><button class="btn secondary" type="button" id="prAddAll">ضيف كل الظاهر للطلبية</button></div></section>
  <section class="panel"><h2>الطلبية مقسمة على الموردين</h2>
    <p class="muted">كل صنف عند المورد صاحب أعلى خصم حقيقي (البونص والسعر الصافي محسوبين) من آخر شراء عند كل مورد في آخر سنة. اسم الصنف هو اسمه عند المورد: صحّحه والبرنامج يفتكره للمرة الجاية.</p>
    <div id="prOrder" class="muted">لسه مفيش أصناف في الطلبية.</div>
    <div class="row"><button class="btn" type="button" id="prOrderXlsx">تحميل Excel لكل مورد</button>
      <button class="btn secondary" type="button" id="prOrderClear">امسح الطلبية</button></div>
    <div id="prMsg"></div></section>
  <section class="panel"><h2>متابعة الطلبيات</h2><div id="prTrack" class="muted">…</div></section>
  <section class="panel"><h2>اسأل عليها</h2><div id="prAsk" class="muted">…</div></section>
  <section class="panel"><h2>أسماء وعبوات محتاجة مراجعة</h2><div id="prCheck" class="muted">…</div></section>
  <section class="panel"><h2>أسعار محتاجة تحديث</h2>
    <p class="muted">لو سعر الجمهور عند المورد اتغيّر، اضغط «السعر اتغيّر؟» جنب الصنف واكتب السعر الجديد، واتأكد من العبوة قبل التحديث.</p>
    <div id="prPrices" class="muted">مفيش.</div></section>
  <section class="panel"><h2>صحة المخزون</h2><div id="prHealth" class="muted">…</div></section>
  <section class="panel"><h2>رفع مبيعات شهر من البرنامج القديم</h2>
    <p class="muted">المطابقة بالكود أو الباركود فقط. الأصناف غير المتطابقة تظهر في تقرير ولا تُخمَّن.</p>
    <form id="consForm" class="row">
      <label class="f">من <input name="period_start" type="date" required></label>
      <label class="f">إلى <input name="period_end" type="date" required></label>
      <label class="f">الكمية بـ <select name="qty_unit"><option value="pack">العلبة</option><option value="base">الوحدة</option></select></label>
      <label class="f">الملف <input type="file" name="file" required accept=".xlsx,.csv"></label>
      <button class="btn secondary" type="submit">تجربة بدون حفظ</button>
    </form><div id="consRes"></div></section>`;
  const st = { rows: [], chips: new Set(), sup: "", period: "7", sups: {}, all: [], qty: {}, pick: {} };
  const params = () => new URLSearchParams({ expensive_from: $("#prForm").expensive_from.value || "300",
    ...($("#prAll").checked ? { scope: "all" } : {}), ...($("#prSupplies").checked ? { supplies: "1" } : {}) }).toString();
  // the owner's choice: picked now, else the one saved in the order on this PC, else the recommendation (Codex P2)
  const supOf = (r) => { const o = st.order || {}; return st.pick[r.item_id] ?? (o[r.item_id] ? (o[r.item_id].supplier_id || "") : (r.best ? r.best.supplier_id : "")); };
  const visible = () => st.rows.filter((r) => (r.qty[st.period] > 0 || r.asked || !r.shown)
    && [...st.chips].every((c) => prChip[c](r)) && (!st.sup || supOf(r) === st.sup));
  const sideCell = (r) => r.suppliers.length ? r.suppliers.map((s) => `<div class="${s.supplier_id === supOf(r) ? "" : "muted"}">${s.supplier_id === supOf(r) ? "<b>" : ""}${esc(s.supplier || "—")}: ${prPct(s.discount_bp)}${s.prev_bp !== null && s.prev_bp !== undefined ? ` (قبلها ${prPct(s.prev_bp)})` : ""}${s.active ? "" : " · موقوف"}${s.supplier_id === supOf(r) ? "</b>" : ""}</div>`).join("") : "—";
  const supSel = (r) => `<select class="prSup" data-id="${esc(r.item_id)}"><option value="">بدون مورد</option>${st.all.map((s) => `<option value="${esc(s.id)}" ${s.id === supOf(r) ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>`;
  const draw = () => {
    st.order = PR_ORDER.get();
    const v = visible(), order = st.order;
    document.querySelectorAll(".prP").forEach((th) => th.classList.toggle("on", th.dataset.p === st.period));
    $("#prBody").innerHTML = v.map((r) => `<tr class="${r.shown ? "" : "muted"}"><td>${esc(r.label)}${r.asked ? ` <span class="badge b-warn">مطلوب</span>` : ""}${r.supply ? ` <span class="badge">مستلزمات</span>` : ""}
      <div class="muted">${r.reasons.map(esc).join("<br>")}</div>
      <button type="button" class="chip tgl prPrice" data-id="${esc(r.item_id)}">السعر اتغيّر؟</button></td>
      <td><span class="badge ${CLASS_AR[r.class][1]}">${CLASS_AR[r.class][0]}</span></td>
      <td>${r.on_hand}</td><td>${r.arrived || "—"}</td><td>${r.on_order || "—"}</td>
      ${["7", "14", "30"].map((p) => `<td class="${p === st.period ? "on" : "muted"}">${r.qty[p]}</td>`).join("")}
      <td>${sideCell(r)}</td>
      <td>${r.flags.map((f) => `<span class="badge b-warn">${FLAG_AR[f]}</span>`).join(" ")}</td>
      <td><input type="number" min="1" step="1" class="prQty" data-id="${esc(r.item_id)}" value="${order[r.item_id] ? order[r.item_id].packs : (st.qty[r.item_id] ?? r.qty[st.period])}" style="width:5em;min-width:0"></td>
      <td>${supSel(r)}</td>
      <td><button type="button" class="btn small ${order[r.item_id] ? "secondary" : ""} prAdd" data-id="${esc(r.item_id)}">${order[r.item_id] ? "في الطلبية ✓" : "ضيف للطلبية"}</button></td></tr>`).join("")
      || `<tr><td colspan="13" class="muted">مفيش أصناف ناقصة للمدة دي (جرّب مدة أطول، أو ارفع مبيعات شهر من تحت لو مفيش بيانات بيع)</td></tr>`;
    document.querySelectorAll("#prChips [data-chip]").forEach((b) => b.classList.toggle("on", st.chips.has(b.dataset.chip)));
    drawOrder(); drawPrices();
  };
  const groups = () => {
    const g = {};
    Object.entries(PR_ORDER.get()).forEach(([id, ln]) => { (g[ln.supplier_id || ""] = g[ln.supplier_id || ""] || []).push({ item_id: id, ...ln }); });
    return g;
  };
  const supName = (sid) => (sid ? (st.sups[sid] || "مورد") : "بدون مورد سابق");
  const drawOrder = () => {
    const g = groups(), ks = Object.keys(g);
    $("#prOrder").className = ks.length ? "" : "muted";
    $("#prOrder").innerHTML = ks.length ? ks.map((sid) => `<div class="prGroup" data-sid="${esc(sid)}"><h3>${esc(supName(sid))} — ${g[sid].length} صنف</h3>
      <ul>${g[sid].map((l) => `<li>${esc(l.label)} × <b>${l.packs}</b> علبة
        ${sid ? `<label class="f">اسمه عند المورد <input class="prName" data-id="${esc(l.item_id)}" value="${esc(l.supplier_name || "")}" placeholder="${esc(l.label)}"></label>` : ""}
        <button type="button" class="chip tgl prDel" data-id="${esc(l.item_id)}">شيل</button></li>`).join("")}</ul>
      <div class="row">${sid ? `<button type="button" class="btn small secondary prMsgBtn">نص الرسالة (للنسخ)</button>
        <button type="button" class="btn small prSent">اتبعتت: تابعها</button>` : `<span class="muted">اختار مورد للأصناف دي من الجدول.</span>`}</div></div>`).join("")
      : "لسه مفيش أصناف في الطلبية.";
  };
  const drawPrices = () => {
    const p = PR_PRICES.get(), ks = Object.keys(p);
    $("#prPrices").className = ks.length ? "" : "muted";
    $("#prPrices").innerHTML = ks.length ? `<ul>${ks.map((id) => `<li>${esc(p[id].label)}: ${money(p[id].old)} ← <b>${money(p[id].price)}</b> ج
      <button type="button" class="btn small prPriceSave" data-id="${esc(id)}">حدّث السعر</button>
      <button type="button" class="chip tgl prPriceDel" data-id="${esc(id)}">شيل</button></li>`).join("")}</ul>` : "مفيش.";
  };
  const add = (r, quiet) => {
    const o = PR_ORDER.get(), inp = $(`.prQty[data-id="${CSS.escape(r.item_id)}"]`);
    const packs = prPacks(inp ? inp.value : r.qty[st.period]);
    if (!packs) { if (!quiet) toast("اكتب الكمية (علبة واحدة على الأقل)", true); return; }
    const sid = supOf(r) || null;
    o[r.item_id] = { label: r.label, packs, supplier_id: sid, supplier_name: null };
    PR_ORDER.set(o);
  };
  const lines = (sid) => (groups()[sid] || []).map((l) => ({ item_id: l.item_id, packs: l.packs }));
  const drawTrack = async () => {
    const out = await api("/api/procurement/orders");
    const open = out.orders.filter((o) => o.status !== "done" && o.status !== "arrived").concat(out.orders.filter((o) => o.status === "arrived").slice(0, 5));
    $("#prTrack").className = open.length ? "" : "muted";
    $("#prTrack").innerHTML = open.length ? open.map((o) => `<div><h3>${esc(o.supplier)} · ${esc(String(o.created_at).slice(0, 10))}</h3><ul>
      ${o.lines.map((l) => `<li>${esc(l.label)} × ${l.packs} — <b>${PR_LINE_AR[l.status]}${l.status === "partial" ? ` ${l.got}` : ""}</b>
        ${["arrived", "partial", "na", "sent"].filter((s) => s !== l.status || s === "partial").map((s) => `<button type="button" class="chip tgl prLine" data-o="${esc(o.id)}" data-i="${esc(l.item_id)}" data-s="${s}" data-n="${l.packs}">${PR_LINE_AR[s]}</button>`).join(" ")}</li>`).join("")}</ul></div>`).join("")
      : "مفيش طلبيات مفتوحة. بعد ما تبعت طلبية لمورد اضغط «اتبعتت: تابعها».";
  };
  const load = async () => {
    const out = await api(`/api/procurement/plan?${params()}`);
    st.rows = out.rows;
    st.all = out.all_suppliers;
    st.sups = Object.fromEntries(out.all_suppliers.map((s) => [s.id, s.name]));
    $("#prSupSel").innerHTML = `<option value="">كل الموردين</option>${out.all_suppliers.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}`;
    if (st.sup && !st.sups[st.sup]) st.sup = "";
    $("#prSupSel").value = st.sup;
    const c = out.counts;
    $("#prCounts").textContent = `منتظم ${c.regular} · شبه منتظم ${c.semi} · متقطع ${c.irregular} · مخفي من العرض الأساسي ${c.hidden}`;
    $("#prAsk").className = out.ask_about.length ? "" : "muted";
    $("#prAsk").innerHTML = out.ask_about.length ? `<ul>${out.ask_about.map((a) => `<li>${esc(a.label)} — ${a.why === "no_supplier" ? "مفيش مورد اشترينا منه في آخر سنة"
      : a.why === "na" ? `مش موجود عند ${esc(a.supplier || "")} (${a.missing_packs} علبة)` : `ناقص ${a.missing_packs} علبة من ${esc(a.supplier || "")}`}</li>`).join("")}</ul>` : "مفيش.";
    $("#prCheck").className = out.check_names.length ? "" : "muted";
    $("#prCheck").innerHTML = out.check_names.length ? `<ul>${out.check_names.map((c) => `<li>${esc(c.label)} ← اسمه في آخر فاتورة: «${esc(c.raw_name)}» · العبوة ${c.pack_size}</li>`).join("")}</ul>` : "مفيش.";
    draw();
  };
  $("#prPeriod").addEventListener("change", (e) => { st.period = e.target.value; st.qty = {}; draw(); });
  $("#prChips").addEventListener("click", (e) => {
    const b = e.target.closest("[data-chip]"); if (!b) return;
    st.chips.has(b.dataset.chip) ? st.chips.delete(b.dataset.chip) : st.chips.add(b.dataset.chip); draw();
  });
  $("#prSupSel").addEventListener("change", (e) => { st.sup = e.target.value; draw(); });
  $("#prBody").addEventListener("click", (e) => {
    const pb = e.target.closest(".prPrice");
    if (pb) {
      const r = st.rows.find((x) => x.item_id === pb.dataset.id); if (!r) return;
      const t = (prompt(`سعر الجمهور الجديد لـ ${r.label} (ج)؟ السعر الحالي ${money(r.public_price)}`) || "").trim();
      if (!t) return;
      if (!/^[0-9]{1,7}(\.[0-9]{1,2})?$/.test(t)) { toast("اكتب السعر رقم بقرشين بالكتير", true); return; }
      const p = PR_PRICES.get(); p[r.item_id] = { label: r.label, old: r.public_price, price: toPiasters(t) }; PR_PRICES.set(p); drawPrices(); return;
    }
    const b = e.target.closest(".prAdd"); if (!b) return;
    const r = st.rows.find((x) => x.item_id === b.dataset.id); if (!r) return;
    const o = PR_ORDER.get();
    if (o[r.item_id]) { delete o[r.item_id]; PR_ORDER.set(o); } else add(r);
    draw();
  });
  $("#prBody").addEventListener("change", (e) => {
    const s = e.target.closest(".prSup");
    if (s) {
      st.pick[s.dataset.id] = s.value;
      const o = PR_ORDER.get();
      if (o[s.dataset.id]) { o[s.dataset.id].supplier_id = s.value || null; o[s.dataset.id].supplier_name = null; PR_ORDER.set(o); }
      draw(); return;
    }
    const i = e.target.closest(".prQty"); if (!i) return;
    const o = PR_ORDER.get(), n = prPacks(i.value);
    st.qty[i.dataset.id] = i.value;
    if (!n) { toast("اكتب الكمية رقم صحيح (علبة واحدة على الأقل)", true); return; }
    if (o[i.dataset.id]) { o[i.dataset.id].packs = n; PR_ORDER.set(o); drawOrder(); }
  });
  $("#prAddAll").addEventListener("click", () => { visible().filter((r) => r.shown).forEach((r) => add(r, true)); draw(); });
  $("#prOrder").addEventListener("change", async (e) => {
    const i = e.target.closest(".prName"); if (!i) return;
    const sid = i.closest(".prGroup").dataset.sid, name = i.value.trim();
    if (!name) return;
    try {
      await api("/api/procurement/supplier-name", { method: "POST", body: { supplier_id: sid, item_id: i.dataset.id, name } });
      const o = PR_ORDER.get(); if (o[i.dataset.id]) { o[i.dataset.id].supplier_name = name; PR_ORDER.set(o); }
      toast("اتحفظ اسمه عند المورد");
    } catch (er) { toast(er.message, true); }
  });
  $("#prOrder").addEventListener("click", async (e) => {
    const d = e.target.closest(".prDel");
    if (d) { const o = PR_ORDER.get(); delete o[d.dataset.id]; PR_ORDER.set(o); draw(); return; }
    const g = e.target.closest(".prGroup"); if (!g) return;
    const sid = g.dataset.sid;
    if (e.target.closest(".prMsgBtn")) {
      try {
        const r = await api("/api/procurement/message", { method: "POST", body: { supplier_id: sid, lines: lines(sid) } });
        $("#prMsg").innerHTML = `<h3>نص الرسالة لـ ${esc(supName(sid))} (البرنامج ما بيبعتهاش: انسخها وابعتها بنفسك)</h3>
          <textarea id="prMsgText" rows="10" style="width:100%">${esc(r.text)}</textarea>
          <button type="button" class="btn small" id="prMsgCopy">انسخ</button>`;
        $("#prMsgCopy").addEventListener("click", () => { $("#prMsgText").select(); try { navigator.clipboard.writeText($("#prMsgText").value); } catch { /* old browser */ } toast("اتنسخت"); });
      } catch (er) { toast(er.message, true); }
      return;
    }
    const sentBtn = e.target.closest(".prSent");
    if (sentBtn) {
      if (sentBtn.disabled) return;
      if (!confirm(`سجّل إن طلبية ${supName(sid)} اتبعتت؟ (البرنامج ما بيبعتش حاجة؛ ده تسجيل للمتابعة بس)`)) return;
      sentBtn.disabled = true;               // one press at a time; a retry reuses the same key (ONCE_PATHS)
      try {
        await api("/api/procurement/orders", { method: "POST", body: { supplier_id: sid, lines: lines(sid) } });
        const o = PR_ORDER.get(); Object.keys(o).forEach((k) => { if ((o[k].supplier_id || "") === sid) delete o[k]; }); PR_ORDER.set(o);
        toast("اتسجلت للمتابعة"); await load(); drawTrack().catch(() => {});
      } catch (er) { toast(er.message, true); } finally { sentBtn.disabled = false; }
    }
  });
  $("#prTrack").addEventListener("click", async (e) => {
    const b = e.target.closest(".prLine"); if (!b) return;
    const body = { item_id: b.dataset.i, status: b.dataset.s };
    if (b.dataset.s === "partial") {
      const t = (prompt(`وصل كام علبة من ${b.dataset.n}؟`) || "").trim();
      if (!t) return;
      body.got = t;
    }
    try { await api(`/api/procurement/orders/${encodeURIComponent(b.dataset.o)}/line`, { method: "POST", body }); await drawTrack(); load().catch(() => {}); }
    catch (er) { toast(er.message, true); }
  });
  $("#prPrices").addEventListener("click", async (e) => {
    const p = PR_PRICES.get(), d = e.target.closest(".prPriceDel"), s = e.target.closest(".prPriceSave");
    if (d) { delete p[d.dataset.id]; PR_PRICES.set(p); drawPrices(); return; }
    if (!s) return;
    try {
      await api(`/api/items/${encodeURIComponent(s.dataset.id)}`, { method: "PATCH", body: { public_price: p[s.dataset.id].price } });
      delete p[s.dataset.id]; PR_PRICES.set(p); toast("اتحدّث السعر"); load().catch(() => {});
    } catch (er) { toast(er.message, true); }
  });
  $("#prOrderClear").addEventListener("click", () => { PR_ORDER.set({}); $("#prMsg").innerHTML = ""; draw(); });
  $("#prOrderXlsx").addEventListener("click", async () => {
    const g = groups();
    const body = { groups: Object.keys(g).map((sid) => ({ supplier_id: sid || null, lines: lines(sid) })) };
    if (!body.groups.length) { toast("الطلبية فاضية", true); return; }
    const res = await fetch("/api/procurement/plan.xlsx", { method: "POST", body: JSON.stringify(body), credentials: "same-origin",
      headers: { "X-Requested-With": "pharm", "Content-Type": "application/json", "Idempotency-Key": newKey() } });
    if (!res.ok) {
      let msg = "التنزيل فشل"; try { msg = (await res.json()).error.message || msg; } catch { /* not JSON */ }
      toast(msg, true); return;
    }
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: "الطلبية.xlsx" });
    document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  });
  $("#prForm").addEventListener("submit", (e) => { e.preventDefault(); load().catch((er) => toast(er.message, true)); });
  load().catch((er) => toast(er.message, true));
  drawTrack().catch(() => { $("#prTrack").textContent = "—"; });
  api("/api/procurement/health").then((h) => {
    $("#prHealth").className = "";
    $("#prHealth").innerHTML = `
      <h3>قرب انتهاء الصلاحية (${h.near_expiry.length})</h3>
      <div class="tablewrap"><table><thead><tr><th>الصنف</th><th>التشغيلة</th><th>الصلاحية</th><th>الكمية</th><th>المتوقع بيعه</th><th>معرض للانتهاء</th><th>الإجراء</th></tr></thead>
      <tbody>${h.near_expiry.map((e) => `<tr class="${e.units_at_risk ? "has-err" : ""}"><td>${esc(e.label)}</td><td>${esc(e.batch_no)}</td><td>${esc(e.expiry_date)}</td>
        <td>${e.qty}</td><td>${e.expected_to_sell}</td><td>${e.units_at_risk}</td><td>${esc(e.action)}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">لا يوجد</td></tr>`}</tbody></table></div>
      <h3>راكد (${h.slow_movers.length})</h3><p>${h.slow_movers.map((s) => `${esc(s.label)} (${s.on_hand})`).join("، ") || "لا يوجد"}</p>
      <h3>مخزون زائد (${h.overstock.length})</h3><p>${h.overstock.map((s) => `${esc(s.label)} (${s.days_of_cover} يوم)`).join("، ") || "لا يوجد"}</p>`;
  }).catch(() => {});
  $("#consForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const r = await api("/api/procurement/consumption", { method: "POST", form: fd });
      $("#consRes").innerHTML = `<p>أسطر متطابقة: <b>${r.matched_rows}</b> — إجمالي ${r.total_base_units} وحدة — غير متطابقة: <b>${r.unmatched.length}</b> — مشكلات: ${r.problems.length}</p>
        ${r.unmatched.length ? `<p class="muted">غير متطابقة: ${r.unmatched.slice(0, 30).map((u) => esc(u.name || u.code)).join("، ")}</p>` : ""}
        <button class="btn" id="consApply">حفظ المبيعات</button>`;
      $("#consApply").addEventListener("click", async () => {
        fd.set("apply", "1");
        try { await api("/api/procurement/consumption", { method: "POST", form: fd }); toast("تم الحفظ"); load(); }
        catch (er) { toast(er.message, true); }
      });
    } catch (er) { toast(er.message, true); }
  });
}

// ------------------------------------------------------------------ POS
const SEV = {
  critical: ["⛔ حرجة", "b-err"], high: ["❗ مرتفعة", "b-err"], moderate: ["⚠️ متوسطة", "b-warn"],
  low: ["ℹ️ منخفضة", "b-warn"], info: ["ℹ️ معلومة", "b-ok"], insufficient: ["❔ لا توجد بيانات كافية", "b-ai"],
};
let SALE = null;
let CHANNELS = null;
const POS = { method: "cash", channel: null, split: false, parts: [], rcpt: {}, saving: null,
              adding: new Map(), completing: false };
// #81: no receipt unless the cashier asks (F4) or the owner's amount rule picks one. An explicit choice wins
// over the rule for that sale only (POS.rcpt is keyed by sale id, so the next sale starts fresh).
// A delivery sale always prints one receipt on completion.
function receiptWanted(sale, cfg, override) {
  if (sale.kind === "delivery") return true;
  if (override === true || override === false) return override;
  return !!(cfg && cfg.auto_receipt && sale.total >= (cfg.auto_receipt_min || 0));
}
let RCFG = null;                      // receipt settings, read once per POS visit
const lastSale = () => { try { return JSON.parse(sessionStorage.getItem("posLast") || "null"); } catch { return null; } };
// owner's design 19/11: the kind first («فيزا», «محفظة إلكترونية»), then which one; each one lands in its own account
const METHODS = [["cash", "نقدي"], ["card", "فيزا"], ["transfer", "محفظة إلكترونية"], ["credit", "آجل"]];
// screenshot of the transfer, saved with the invoice for the owner to check (optional)
const proofInput = (s) => `<label class="f mt proof">📎 صورة التحويل ${(s.attachments || []).length ? `<span class="badge b-ok">✓ ${(s.attachments || []).length} صورة</span>` : `<span class="muted">(اختياري — للمراجعة)</span>`}
  <input type="file" id="posProof" accept="image/*,application/pdf"></label>`;
const qtyText = (q, it) => (it.pack_size > 1 && q % it.pack_size === 0 ? `${q / it.pack_size} ${esc(it.pack_unit && it.pack_unit !== "pack" ? it.pack_unit : "علبة")}`
  : it.pack_size > 1 && q > it.pack_size ? `${Math.floor(q / it.pack_size)} علبة + ${q % it.pack_size} ${esc(unitName({ base_unit_name: it.base_unit }))}`
  : `${q} ${esc(unitName({ base_unit_name: it.base_unit }))}`);

// launch day 21/11: the price shown is the price of the unit the line is sold in (1 box of a 3-strip item showed
// the strip's 32.00 while the total was the box's); a line of boxes + strips shows both
function salePriceQty(p, it) {
  const label = p.pack ? (it.pack_unit && it.pack_unit !== "pack" ? it.pack_unit : "علبة")
    : unitName({ base_unit_name: it.base_unit });
  return `${p.qty} ${esc(label)}`;
}
function linePrice(l) {
  const prices = l.official_prices || [];
  return prices.length === 1 ? money(prices[0].price) :
    prices.map((p) => `${salePriceQty(p, l.item)} × ${money(p.price)}`).join("<br>");
}
async function openDraft(id) {
  SALE = id ? await api(`/api/sales/${id}`) : await api("/api/sales", { method: "POST", body: {} });
  POS.method = "cash"; POS.channel = null; POS.split = false; POS.parts = [];
}

async function viewPos(afterCheckout = false) {
  QUICK = null;      // stock may have changed since the last visit (a purchase approved): no stale "نفد" (11/01; «ناقص» = not in the market, update 1)
  CHANNELS = CHANNELS || (await api("/api/payment-channels")).channels.filter((c) => c.is_active);
  if (!afterCheckout) {
    // Another PC may have changed the settings since this screen was opened.
    [RCFG, ME.ui] = await Promise.all([
      api("/api/settings/receipt").catch(() => RCFG), api("/api/settings/ui").catch(() => ME.ui),
    ]);
  }
  const tabs = (await api("/api/sales/drafts")).drafts;
  if (!SALE || SALE.status !== "draft" || !tabs.some((d) => d.id === SALE.id)) {
    await openDraft(tabs.at(-1)?.id);
    if (!tabs.length) tabs.push({ id: SALE.id, kind: SALE.kind, lines: SALE.lines.length });
  }
  app.innerHTML = `
  <nav class="tabs" aria-label="الفواتير المفتوحة">${tabs.map((d, i) => `<button class="tab ${d.id === SALE.id ? "on" : ""}" data-tab="${esc(d.id)}" data-n="${i + 1}">
      فاتورة ${i + 1}${d.kind === "delivery" ? " · توصيل" : ""} · ${d.lines ? `${d.lines} صنف` : "فارغة"}
      <span class="x" data-close="${esc(d.id)}" title="أغلق هذه الفاتورة" aria-label="إغلاق">✕</span></button>`).join("")}
    <button class="tab add" id="tabNew" title="فاتورة جديدة (${esc(posKey("new_sale"))})">+ فاتورة <kbd>${esc(posKey("new_sale"))}</kbd></button></nav>
  <div class="pos">
    <section class="pos-main">
      <label class="f">امسح الباركود أو الكود المربع، أو اكتب أول حروف الصنف
        <input id="posQ" class="big" autocomplete="off" autofocus></label>
      <label class="small muted"><input type="checkbox" id="posAll" ${posShowAll() ? "checked" : ""}> اعرض كمان الأصناف اللي رصيدها صفر</label>
      <div id="posRes" class="results"></div>
      <section class="panel" id="posCart"></section>
      <div id="posQuick"></div>
      <p class="keys"><kbd>Enter</kbd> يضيف علبة · <kbd>Shift+Enter</kbd> يضيف وحدة · <kbd>Enter</kbd> على البحث الفاضي: للدفع، و<kbd>Enter</kbd> تاني: إتمام البيع · ${posKeysHtml()}</p>
    </section>
    <aside class="pos-side" id="posSide"></aside>
  </div>
  <p class="muted mt">${ME.permissions.includes("sales.totals") ? `<a href="#" id="posSummary">ملخص اليوم</a> · ` : ""}<a href="#" id="posShift">الخزينة</a> · <a href="#" id="posDeliv">التوصيلات المفتوحة</a>${lastSale() ? ` · <a href="#" id="posLast">🧾 اطبع إيصال آخر فاتورة (رقم ${esc(lastSale().no)})</a>` : ""}${ME.permissions.includes("cash.manage") ? ` · <a href="#" id="posKick">فتح الدرج (F8)</a>` : ""}</p><div id="posSum"></div>`;
  renderCart(); renderSide();                    // renderCart draws the quick row too (syncQuick)
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", async (e) => {
    if (e.target.dataset.close) return;
    await openDraft(b.dataset.tab); viewPos();
  }));
  document.querySelectorAll("[data-close]").forEach((x) => x.addEventListener("click", async (e) => {
    e.stopPropagation();
    const d = tabs.find((t) => t.id === x.dataset.close);
    if (d?.lines && !confirm("هذه الفاتورة بها أصناف. هل تغلقها دون بيع؟")) return;
    try { await api(`/api/sales/${x.dataset.close}/discard`, { method: "POST", body: {} }); if (SALE.id === x.dataset.close) SALE = null; viewPos(); }
    catch (er) { toast(er.message, true); }
  }));
  $("#tabNew").addEventListener("click", newTab);
  const q = $("#posQ");
  q.value = "";                                   // update 1: never the old text after a reload
  $("#posAll").addEventListener("change", (e) => {
    try { localStorage.setItem("posAll", e.target.checked ? "1" : "0"); } catch { /* per-viewer only */ }
    if (q.value.trim()) search(false, true).catch(() => {});
    q.focus();
  });
  let res = [], liveSeq = 0, liveT = null;
  // live = while typing: only lists results, never adds; a live answer that arrives after newer typing or an Enter
  // is dropped. A search submitted with Enter (a scan) always completes: rapid scans must all reach the sale.
  const search = async (unit, live = false, given = null) => {
    const v = given ?? q.value.trim(); if (!v) return;
    const mine = live ? ++liveSeq : null, saleId = SALE.id;
    const isScan = /^\d{3,}$/.test(v) || /^01\d{14}/.test(v);
    const short = /^\d{3,5}$/.test(v), current = onThisScreen();
    // update 1 (owner): items with stock first; typed names leave out the ones with no stock unless «اعرض كمان»
    const found = await findItems(v, 8, isScan || posShowAll() ? "first" : "only", true);
    if (!current()) return;
    if (SALE?.id !== saleId || (live && mine !== liveSeq)) return;
    res = found.items;
    const scanned = /^\d{6,}$/.test(v) || found.hint?.kind === "gs1";
    const sellable = (r) => r.is_service || r.stock_qty == null || r.stock_qty > 0;     // out of stock: show it, with its similar items
    const exactShort = !short || res[0]?.code === v || res[0]?.code === `E${v}`;
    if (!live && res.length === 1 && sellable(res[0]) && exactShort && (scanned || !found.hint)) {
      return addItem(res[0], unit ? 0 : 1, unit ? 1 : 0, given != null);
    }
    $("#posRes").innerHTML = (found.hint?.kind === "gs1" && !res.length
      ? `<p class="badge b-warn">⚠ هذا الكود المربع غير مسجل على أي صنف. افتح بطاقة الصنف وامسحه في خانة الباركود.</p>` : hintHtml(found.hint))
      + res.map((r, i) => `<div class="res ${r.stock_qty ? "" : "out"}"><div class="grow"><b>${itemHtml(r)}</b> ${markChip(r.mark)}
        <div class="muted">${esc(r.strength_norm || "")} · ${r.pack_size} ${esc(unitName(r))} في العلبة · ${r.location ? `${esc(r.location)} · ` : ""}رصيد: <b>${esc(fmtStock(r.stock_qty, r) || "0")}</b>${r.nearest_expiry ? ` · أقرب صلاحية <b>${esc(r.nearest_expiry.slice(5, 7))}/${esc(r.nearest_expiry.slice(0, 4))}</b>` : ""}</div></div>
        <div><b class="price">${money(r.next_pack_price ?? r.next_unit_price) || "—"} ج</b>
          ${r.pack_size > 1 && r.next_unit_price != null && r.next_pack_price != null ? `<div class="muted small">${money(r.next_unit_price)} ج / ${esc(unitName(r))}</div>` : ""}
          ${r.price_stock?.length > 1 ? `<div class="muted small">${r.price_stock.map((p) => `${money(p.price) || "بدون سعر"} ج · ${esc(fmtStock(p.qty, r))}`).join(" / ")}</div>` : ""}</div>
        ${r.is_service || r.available_qty >= r.pack_size ? `<button class="btn small" data-add="${i}" data-kind="pack">علبة${i === 0 ? " ↵" : ""}</button>` : ""}
        ${r.pack_size > 1 && !r.is_service && r.available_qty > 0 ? `<button class="btn small secondary" data-add="${i}" data-kind="unit">${esc(unitName(r))}</button>` : ""}
        ${!r.is_service && !r.available_qty ? `<span class="badge b-err">✕ نفد</span>` : ""}
        ${r.pack_size === 1 && !r.is_service && canFixPack() ? `<button class="btn small secondary" data-search-pack="${i}">${PACK_QUESTION}</button>` : ""}
        ${r.is_service ? "" : `<button class="btn small ${r.stock_qty ? "secondary" : ""}" data-alt="${i}" title="المثيل والبديل">↔ مثيل / بديل</button>`}</div>`).join("")
      + (found.hidden_no_stock ? `<p class="muted small">${found.hidden_no_stock} صنف رصيده صفر مش ظاهر — <a href="#" id="posShowZero">اعرضهم</a></p>` : "")
      || `<p class="muted">لا توجد نتائج</p>`;
    $("#posShowZero")?.addEventListener("click", (e) => { e.preventDefault(); $("#posAll").checked = true; $("#posAll").dispatchEvent(new Event("change")); });
    $("#posRes").querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () =>
      addItem(res[+b.dataset.add], b.dataset.kind === "pack" ? 1 : 0, b.dataset.kind === "unit" ? 1 : 0)));
    $("#posRes").querySelectorAll("[data-search-pack]").forEach((button) => button.addEventListener("click", async () => {
      const item = res[+button.dataset.searchPack];
      if (await withBusy(button, () => packFix(item.id, null, true)) && q.isConnected) await search(false, true);
    }));
    $("#posRes").querySelectorAll("[data-alt]").forEach((b) => b.addEventListener("click", () => altPanel(res[+b.dataset.alt].id, $("#posRes"), addItem)));
    if (live) q.dataset.searched = v;                                   // Enter now adds the first result shown
    if (!live && (isScan || scanned)) return false;       // an unresolved scan needs the cashier's attention
  };
  q.addEventListener("input", () => {
    clearTimeout(liveT);
    liveSeq++;                                                          // any suggestion still on its way is for other text
    const v = q.value.trim();
    if (v.length < 2 || /^\d+$/.test(v)) {                             // digits: a barcode scanner's Enter follows
      $("#posRes").innerHTML = ""; delete q.dataset.searched; return;
    }
    liveT = setTimeout(() => search(false, true).catch(() => {}), 250);
  });
  let lastEnter = -Infinity;
  q.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.ctrlKey) return;          // Ctrl+Enter is a sale-screen key (U2-19), not a search
    e.preventDefault();
    const prevEnter = lastEnter; lastEnter = e.timeStamp;
    clearTimeout(liveT); liveSeq++;
    const first = $("#posRes [data-add]");
    if (first && !/^\d{3,5}$/.test(q.value.trim()) && q.value.trim() === q.dataset.searched) {           // second Enter on the same text: add the first result
      const r = res[+first.dataset.add]; if (r) addItem(r, e.shiftKey ? 0 : 1, e.shiftKey ? 1 : 0); return;
    }
    const v = q.value.trim();
    // U4-17: Enter on the empty search line goes to the payment («العميل دفع», or the done button);
    // Enter there finishes the sale. Scan, Enter, Enter = sold at the exact amount.
    // Not on a held key, nor on the second Enter of a scanner that sends two (CR+LF) right after the code.
    if (!v) {
      if (e.repeat || e.timeStamp - prevEnter < 150 || e.timeStamp - (POS.scanAt || -Infinity) < 150) return;
      if (SALE?.lines.length || pendingPosAdds(SALE?.id).length) ($("#payGiven:not([disabled])") || $("#posDone:not([disabled])"))?.focus();
      return;
    }
    if (/^\d{3,}$/.test(v) || /^01\d{14}/.test(v)) {                  // a scan: free the field at once for the next one
      q.value = ""; delete q.dataset.searched;
      trackPosAdd(search(e.shiftKey, false, v));
      return;
    }
    q.dataset.searched = v;
    trackPosAdd(search(e.shiftKey));
  });
  POS.scan = (code) => {
    if (!q.isConnected || !SALE) return;
    // a discount or amount typed in the focused field is saved first (its change event), then the item is added
    const at = document.activeElement;
    if (at && at !== q && at.matches?.("input, select, textarea")) at.blur();
    trackPosAdd(Promise.resolve(POS.saving).then(() => search(false, false, code)).then((r) => {
      // a code nobody has: show it in the search box with the results, so the cashier sees what was read
      if (r === false && q.isConnected && !q.value) { q.value = code; q.focus(); q.select(); }
      return r;
    }));
  };
  bindPosExtras();
}


const posShowAll = () => { try { return localStorage.getItem("posAll") === "1"; } catch { return false; } };
// a page restored from the browser's back/forward cache keeps typed text: search boxes start empty (update 1)
window.addEventListener("pageshow", (e) => {
  if (!e.persisted) return;
  ["#posQ", "#q"].forEach((sel) => { const el = $(sel); if (el) el.value = ""; });
  const r = $("#posRes"); if (r) r.innerHTML = "";
});
// أزرار البيع السريع: اللي الصاحب ثبّتها، وبعدها الأكتر بيعًا آخر 30 يوم
let QUICK = null;
const quickOpen = () => { try { return localStorage.getItem("posQuick") !== "0"; } catch { return true; } };
// U4-17 (owner 17/11): one compact sideways row under the invoice. Open while the invoice is empty, shrunk to its
// title after the first item; its button opens or shrinks it for this invoice. On an empty invoice the button is
// also remembered on this PC, as the old «إخفاء» was.
const quickWide = () => {
  const mine = SALE && POS.quickWide?.[SALE.id];
  return mine ?? (!(SALE?.lines.length) && quickOpen());
};
function syncQuick() {
  const box = $("#posQuick");
  if (box && box.dataset.wide !== String(quickWide())) renderQuick();
}
async function renderQuick() {
  const box = $("#posQuick"); if (!box) return;
  const open = quickWide();
  box.dataset.wide = String(open);
  if (open && !QUICK) { try { QUICK = await api("/api/pos/quick"); } catch { QUICK = { items: [] }; } }
  if (!$("#posQuick") || box.dataset.wide !== String(open) || quickWide() !== open) return;   // a newer state draws itself
  const list = QUICK?.items || [];
  box.innerHTML = `<section class="panel qsale ${open ? "" : "shrunk"}"><div class="row between"><h2>⚡ البيع السريع</h2>
      <span class="row">${open ? `<button class="btn small secondary" id="qkRef" title="حدّث">↻</button>` : ""}<button class="btn small secondary" id="qkTgl" aria-expanded="${open}">${open ? "صغّر ▴" : "افتح ▾"}</button></span></div>
    ${open ? (list.length || (QUICK?.services || []).length ? `<div class="qgrid qrow">${list.map((x, i) => {
      const can = x.is_service || x.stock_qty == null || x.stock_qty > 0;
      return `<button class="qbtn ${can ? "" : "out"}" data-q="${i}" ${can ? "" : "disabled"} title="${esc(x.name_en || x.name_ar)}">
        ${x.pinned ? `<span class="pin" aria-label="مثبّت">📌</span>` : ""}<b class="qn">${esc(x.name_en || x.name_ar)}</b><span class="qp">${money(x.public_price)} ج</span>${can ? "" : `<span class="badge b-err">نفد</span>`}</button>`; }).join("")}${(QUICK?.services || []).map((x, i) => `<button class="qbtn svc" data-svc="${i}" title="${esc(x.name_ar || x.name_en)}">
        <b class="qn">🛎 ${esc(x.name_ar || x.name_en)}</b><span class="qp">${x.public_price != null ? `${money(x.public_price)} ج` : "بدون سعر"}</span></button>`).join("")}</div>
      <p class="muted small">كل ضغطة تضيف علبة واحدة (أو مرة واحدة للخدمة). ${canW() ? `ثبّت أي صنف هنا من "تعديل الصنف" أو من شاشة الأصناف.` : ""}</p>`
      : `<p class="muted">لم تُبع أصناف بعد في آخر ${QUICK?.days || 14} يومًا.${canW() ? ` ثبّت الأصناف التي تريدها من "تعديل الصنف".` : ""}</p>`) : ""}
    ${open && canW() && (QUICK?.suggested_services || []).length ? `<p class="muted small">🛎 خدمات مقترحة تعملها من شاشة الأصناف (علّم «خدمة» وحط السعر): ${QUICK.suggested_services.map((x) => `<span class="chip">${esc(x)}</span>`).join(" ")}</p>` : ""}</section>`;
  $("#qkTgl").addEventListener("click", () => {
    if (!SALE) return;
    (POS.quickWide ||= {})[SALE.id] = !open;
    if (!SALE.lines.length) { try { localStorage.setItem("posQuick", open ? "0" : "1"); } catch { /* per-viewer only */ } }
    renderQuick();
  });
  $("#qkRef")?.addEventListener("click", () => { QUICK = null; renderQuick(); });
  box.querySelectorAll("[data-svc]").forEach((b) => b.addEventListener("click", () => addItem(QUICK.services[+b.dataset.svc], 0, 1)));
  box.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => {
    const x = list[+b.dataset.q]; addItem(x, x.is_service || x.pack_size === 1 ? 0 : 1, x.is_service || x.pack_size === 1 ? 1 : 0);
  }));
}

async function newTab() {
  try { await openDraft(null); viewPos(); } catch (er) { toast(er.message, true); }
}
// U4-22: a scan while the focus is in any other field of the sale screen (discount, customer, payment, a button)
// adds the item and never types into that field. The search box reads scans itself; dialogs keep their keys
// (the item card's barcode field is scanned on purpose).
const posScanStop = globalThis.PharmScan?.installScanDetector({
  active: () => location.hash.startsWith("#/pos") && !!POS.scan && !POS.completing && !document.querySelector("dialog[open]"),
  skip: (el) => el?.id === "posQ",
  onScan: (code, el, at) => { POS.scanAt = at; POS.scan(code); },
});
document.addEventListener("keydown", (e) => {
  // The pharmacy scanner (HH360) sends F12 with each barcode (#81): never open the browser's tools, and on the
  // sales screen move to the search box so the code lands there wherever the focus was. Capture phase: first.
  if (e.key === "F12") {
    e.preventDefault();
    if (location.hash.startsWith("#/pos") && !$("dialog[open]")) $("#posQ")?.focus();
  }
}, true);
// Part 3 (owner C): selling by keyboard only. Fixed defaults in this table; changing them from Settings comes later
// (the same table, so nothing else moves). F1 on the empty line searches, F2/F3/F6/Delete act on the chosen line
// (↑ ↓ choose it), F10 goes to the payment first and saves on the second press, F11 opens the drawer.
// U2-19: the keys come from «تخصيص الواجهة» (ME.ui.pos_keys, the server checks conflicts); these are the defaults.
const POS_KEY_DEFAULTS = { search: "F1", branch_stock: "F2", item_card: "F3", receipt: "F4", qty: "F6", exchange: "F7",
  done: "F9", pay: "F10", drawer: "F11", new_sale: "Insert", next_tab: "Ctrl+Tab", cancel_sale: "Ctrl+Delete" };
const POS_KEY_NAMES = { search: "بحث عن صنف", branch_stock: "رصيد الصنف في الفروع", item_card: "كارت الصنف", receipt: "إيصال",
  qty: "الكمية", exchange: "مرتجع واستبدال", done: "إتمام البيع", pay: "الدفع ثم الحفظ", drawer: "فتح الدرج",
  new_sale: "فاتورة جديدة", next_tab: "الفاتورة اللي بعدها", cancel_sale: "إلغاء الفاتورة" };
// never a key used while typing (Home/End/PageUp/PageDown, the number pad without NumLock) or Ctrl+Insert (copy)
const POS_KEY_CHOICES = ["F1", "F2", "F3", "F4", "F6", "F7", "F9", "F10", "F11", "Insert", "Ctrl+Tab", "Ctrl+Delete", "Ctrl+Enter"];
const posKey = (a) => ME?.ui?.pos_keys?.[a] || POS_KEY_DEFAULTS[a];
const posAction = (k) => Object.keys(POS_KEY_DEFAULTS).find((a) => posKey(a) === k);
const POS_KEYS_ORDER = ["search", "↑ ↓", "branch_stock", "item_card", "qty", "Delete", "receipt", "pay", "drawer", "exchange",
  "new_sale", "next_tab", "cancel_sale", "Esc"];
let POS_SEL = -1;                 // the chosen cart line; -1 = the empty search line
function posKeysHtml() {
  const fixed = { "↑ ↓": "اختار صنف", Delete: "شيل الصنف", Esc: "رجوع للبحث" };
  return POS_KEYS_ORDER.filter((a) => a !== "drawer" || ME.permissions.includes("cash.manage"))
    .map((a) => fixed[a] ? `<kbd>${esc(a)}</kbd> ${esc(fixed[a])}` : `<kbd>${esc(posKey(a))}</kbd> ${esc(POS_KEY_NAMES[a])}`).join(" · ");
}
function posMark() {
  const rows = [...document.querySelectorAll("#posCart tbody tr")].filter((r) => !r.querySelector("td[colspan]"));
  if (POS_SEL >= rows.length) POS_SEL = rows.length - 1;
  rows.forEach((r, i) => r.classList.toggle("sel", i === POS_SEL));
  if (POS_SEL >= 0) rows[POS_SEL]?.scrollIntoView({ block: "nearest" });
}
function posLine() {
  if (!SALE || !SALE.lines.length) return null;
  const rows = [...document.querySelectorAll("#posCart tbody tr")];
  const id = rows[POS_SEL]?.querySelector("[data-rm]")?.dataset.rm;
  return SALE.lines.find((l) => l.id === id) || (POS_SEL < 0 ? SALE.lines[SALE.lines.length - 1] : null);
}
function posInvoiceCard(line) {
  const sid = SALE.id, active = document.activeElement;
  const attr = ["data-pos-card", "data-disc", "data-dunit", "data-unit", "data-plus", "data-minus", "data-rm"]
    .find((name) => active.hasAttribute(name));
  showInvoiceItemCard(line.item_id, {
    restore: async () => {
      await POS.saving;
      await Promise.allSettled(pendingPosAdds(sid));
      if (SALE?.id !== sid) return;
      if (active.isConnected) return active;
      return (attr && $(`#posCart [${attr}="${CSS.escape(active.getAttribute(attr))}"]`)) || $("#posQ");
    },
  });
}
async function posBranchStock(l) {
  const r = await api(`/api/items/${l.item_id || l.item.id}/branch-stock`);
  const d = document.createElement("dialog");
  d.innerHTML = `<form method="dialog"><h2>${esc(itemText(r.item))}</h2><table><tbody>${r.branches.map((b) => `<tr><td>${esc(b.name)}${b.here ? " (هنا)" : ""}</td>
    <td class="num"><b>${esc(fmtStock(b.qty, { pack_size: r.item.pack_size }))}</b></td></tr>`).join("")}</tbody></table>
    <p class="muted small">كل أجهزة الفرع بتشوف نفس الرصيد.</p><button class="btn">تمام</button></form>`;
  document.body.appendChild(d); d.addEventListener("close", () => { d.remove(); $("#posQ")?.focus(); }); d.showModal();
}
document.addEventListener("keydown", async (e) => {
  if (!location.hash.startsWith("#/pos") || $("dialog[open]")) return;
  const k = (e.ctrlKey ? "Ctrl+" : "") + e.key, typing = e.target.matches?.("input, select, textarea") && e.target.id !== "posQ";
  const go = (fn) => { e.preventDefault(); Promise.resolve().then(fn).catch((er) => toast(er.message, true)); };
  const act = posAction(k);
  if (POS.completing) { if (act) e.preventDefault(); return; }
  if (act === "search") return go(() => { POS_SEL = -1; posMark(); $("#posQ")?.focus(); $("#posQ")?.select(); });
  if (act === "item_card") {
    const id = e.target.closest?.("#posCart tr")?.querySelector("[data-rm]")?.dataset.rm;
    const line = SALE?.lines.find((l) => l.id === id) || posLine();
    return go(() => line ? posInvoiceCard(line) : toast("مفيش صنف في الفاتورة", true));
  }
  if (act === "branch_stock" || act === "qty") {
    const l = posLine();
    if (!l) return go(() => toast("مفيش صنف في الفاتورة", true));
    if (act === "branch_stock") return go(() => posBranchStock(l));
    return go(() => {
      const packs = l.item.pack_size > 1 && l.qty_base % l.item.pack_size === 0;
      const v = typedWhole(prompt(packs ? "عدد العلب:" : "عدد الوحدات:", String(packs ? l.qty_base / l.item.pack_size : l.qty_base)) || "");
      if (Number(v) > 0) patchLine(l.id, { qty_base: packs ? Number(v) * l.item.pack_size : Number(v) });
    });
  }
  if (act === "receipt") return go(() => $("#posRcpt")?.click());
  const finish = () => {
    if (e.repeat) return;
    const confirmButton = $("#posConfirm");
    if (confirmButton) confirmButton.click();
    else complete();
  };
  if (act === "done") return go(finish);
  if (act === "pay") return go(() => {
    if ($("#posSide")?.contains(document.activeElement)) { finish(); return; }
    ($("#posSide select, #posSide input:not([type=hidden]), #posSide button") || $("#posDone"))?.focus();
  });
  if ((act === "drawer" || k === "F8") && ME.permissions.includes("cash.manage")) return go(() => drawerDialog());
  if (act === "exchange") return go(() => { location.hash = "#/exchange"; });
  if (act === "new_sale") return go(() => newTab());
  if (act === "next_tab") return go(() => {
    const tabs = [...document.querySelectorAll("[data-tab]")], i = tabs.findIndex((t) => t.classList.contains("on"));
    if (tabs.length > 1) tabs[(i + 1) % tabs.length].click();
  });
  if (act === "cancel_sale") return go(() => document.querySelector(`[data-close="${SALE?.id}"]`)?.click());
  if (typing) return;
  const q = $("#posQ"), onEmptySearch = !q || document.activeElement !== q || !q.value;
  if ((k === "ArrowUp" || k === "ArrowDown") && onEmptySearch && SALE?.lines.length) {
    return go(() => {
      const n = SALE.lines.length;
      POS_SEL = k === "ArrowUp" ? (POS_SEL < 0 ? n - 1 : Math.max(0, POS_SEL - 1)) : (POS_SEL < 0 || POS_SEL >= n - 1 ? -1 : POS_SEL + 1);
      posMark(); if (POS_SEL < 0) q?.focus(); else q?.blur();
    });
  }
  if (k === "Delete" && POS_SEL >= 0) return go(() => document.querySelectorAll("#posCart tbody tr")[POS_SEL]?.querySelector("[data-rm]")?.click());
  if (k === "Escape" && POS_SEL >= 0) return go(() => { POS_SEL = -1; posMark(); q?.focus(); });
});

// Manual drawer opening (#81): cash.manage (checked by the server too), a reason, recorded with the shift.
function drawerDialog() {
  const d = document.createElement("dialog");
  d.innerHTML = `<form method="dialog" class="formgrid"><h2>فتح الدرج بدون بيع</h2>
    <label class="f">السبب <select name="reason"><option value="change">فكة</option><option value="cash_check">مراجعة النقدية</option><option value="other">أخرى</option></select></label>
    <label class="f">التوضيح <input name="note" maxlength="200" placeholder="مطلوب مع «أخرى»" autocomplete="off"></label>
    <p class="muted small">يُسجَّل باسمك مع الشيفت والوقت والنتيجة. الطابعة لا تؤكد فتح الدرج فعليًا: المسجَّل هو «أمر فتح».</p>
    <div class="row"><button class="btn" value="ok">إرسال أمر الفتح</button><button class="btn secondary" value="cancel" formnovalidate>إلغاء</button></div></form>`;
  document.body.append(d);
  const f = d.querySelector("form");
  f.reason.addEventListener("change", () => { f.note.required = f.reason.value === "other"; });
  d.addEventListener("close", async () => {
    const ok = d.returnValue === "ok", body = { reason: f.reason.value, note: f.note.value.trim() };
    d.remove(); $("#posQ")?.focus();
    if (!ok) return;
    try { await api("/api/print/drawer", { method: "POST", body }); toast("✓ أُرسل أمر فتح الدرج للطابعة"); }
    catch (er) { toast(er.message, true); }
  });
  d.showModal();
  f.reason.focus();
}

function pendingPosAdds(sid) {
  return [...POS.adding].filter(([, saleId]) => saleId === sid).map(([work]) => work);
}

function trackPosAdd(work) {
  const sid = SALE.id;
  const pending = work.catch((er) => {
    if (SALE?.id === sid) toast(er.message, true);
    return false;
  }).finally(() => {
    POS.adding.delete(pending);
    if (SALE?.id === sid && $("#posDone") && !SALE.lines.length && !pendingPosAdds(sid).length) {
      $("#posDone").disabled = true;
    }
  });
  POS.adding.set(pending, sid);
  if ($("#posDone")) $("#posDone").disabled = false;
  return pending;
}

// scanned = the field was already freed for the next scan: clearing it now would cut a barcode being typed
function addItem(item, packs, units, scanned = false) {
  return trackPosAdd(addPosLine(item, packs, units, scanned));
}

async function addPosLine(item, packs, units, scanned) {
  const sid = SALE.id;
  const update = await api(`/api/sales/${sid}/lines`, { method: "POST", body: { item_id: item.id, packs, units, client_ref: newKey() } });
  if (SALE?.id !== sid || !$("#posCart") || update.version < SALE.version) return;
  SALE = update;
  const added = SALE.lines.find((l) => l.item_id === item.id);
  if (added && (added.item.rx_required || added.item.misuse_risk || added.item.sale_note))
    toast([added.item.rx_required && "📋 بوصفة طبية فقط", added.item.misuse_risk && "⚠ يُساء استخدامه", added.item.sale_note && `📝 ${added.item.sale_note}`].filter(Boolean).join(" · "), true);
  $("#posRes").innerHTML = "";
  if (!scanned) { $("#posQ").value = ""; delete $("#posQ").dataset.searched; }
  $("#posQ").focus();
  renderCart(); renderSide();
}

// a typed discount: "5" / "5%" = percent, "3ج" / "3 ج" / "3egp" = EGP (update 1); null when not a number
function discAsk(v) {
  const t = String(v ?? "").trim();
  if (!t) return null;
  const egp = /(ج|جنيه|egp)\s*$/i.test(t), num = t.replace(/(ج|جنيه|egp|%)\s*$/i, "").trim();
  if (egp) { const p = exactPiasters(num); return p == null || p < 0 ? (toast("مبلغ الخصم غير صحيح", true), null) : { discount_amount: p }; }
  const bp = toBp(num);
  return bp == null || Number.isNaN(bp) ? (toast("نسبة الخصم غير صحيحة", true), null) : { discount_bp: bp };
}

async function patchLine(lineId, body) {
  POS.saving = (async () => {            // complete() waits for it (a discount typed, then F9); false = not saved
    try { SALE = await api(`/api/sales/${SALE.id}/lines/${lineId}`, { method: "PATCH", body }); renderCart(); renderSide(); return true; }
    catch (er) { toast(er.message, true); renderCart(); return false; }     // the controls show what is saved
  })();
  await POS.saving;
}

function renderCart(findings = null) {
  const s = SALE, canDisc = ME.max_discount_bp > 0 || ME.role === "owner";
  refreshTabLabel();
  const lastNo = Math.max(0, ...s.lines.map((l) => l.line_no));
  // U2-21: dispensing notes, shown once per invoice (a toast the first time) and kept as one line above the items
  const dn = s.dispensing_notes || [];
  POS.dnSeen ||= {};
  const seen = (POS.dnSeen[s.id] ||= new Set());
  dn.filter((n) => !seen.has(n.ingredient)).forEach((n) => { seen.add(n.ingredient); toast(`📋 ${n.item}: ${n.rules.join(" · ")}`); });
  $("#posCart").innerHTML = `
    ${dn.length ? `<div class="badge b-info dnotes" role="note">${dn.map((n) => `📋 <b>${esc(n.item || n.ingredient)}</b>: ${esc(n.rules.join(" · "))}${n.source ? ` <span class="muted small">(${esc(n.source)})</span>` : ""}`).join("<br>")}</div>` : ""}
    ${s.lines.length > 1 ? `<p class="muted small">${s.line_order === "entry" ? "الأصناف بترتيب إدخالها" : "الأصناف مرتبة أبجديًا A→Z (أسرع في التحضير)"} — آخر صنف مُضاف عليه علامة ★ ورقم كل صنف هو ترتيب إدخاله</p>` : ""}
    <div class="tablewrap"><table class="cart">
      <thead><tr><th>الصنف</th><th>الكمية</th><th>السعر</th><th>الصلاحية</th><th>خصم</th><th>الإجمالي</th><th><span class="sr">حذف</span></th></tr></thead>
      <tbody>${s.lines.map((l) => `<tr class="${l.problem ? "has-err" : ""} ${l.item.mark ? `m-${l.item.mark}` : ""} ${l.line_no === lastNo && s.lines.length > 1 ? "just-added" : ""}">
        <td><span class="lno" title="ترتيب الإدخال">${l.line_no === lastNo && s.lines.length > 1 ? "★" : ""}${l.line_no}</span> <b>${itemHtml({ name_en: l.item.name_en, name_ar: l.item.name_ar })}</b> ${markChip(l.item.mark)}
          ${l.item.is_service ? `<span class="badge b-info">🛎 خدمة</span>` : ""}${l.item.hide_on_receipt ? `<span class="badge" title="لن يظهر اسمه في الإيصال">🙈</span>` : ""}${l.item.rx_required ? `<span class="badge b-err" title="يُصرف بوصفة طبية فقط">📋 وصفة طبية</span>` : ""}${l.item.misuse_risk ? `<span class="badge b-warn" title="يُساء استخدامه">⚠ سوء استخدام</span>` : ""}${l.item.sale_note ? `<span class="badge b-info" title="${esc(l.item.sale_note)}">📝 ${esc(l.item.sale_note.slice(0, 30))}</span>` : ""}
          ${l.problem ? `<span class="badge b-err">✕ ${l.problem === "no_price" ? "بدون سعر" : "الكمية غير متاحة"}</span>` : ""}
          ${invoiceCardButton("data-pos-card", l.id, posKey("item_card"))}
          ${l.item.stock != null ? `<div class="muted small">الرصيد: ${esc(fmtStock(l.item.stock, { pack_size: l.item.pack_size, base_unit_name: l.item.base_unit }))}</div>` : ""}</td>
        <td><div class="qty"><button class="btn small secondary" data-minus="${esc(l.id)}" aria-label="نقص">−</button>
          <b>${qtyText(l.qty_base, l.item)}</b><button class="btn small secondary" data-plus="${esc(l.id)}" aria-label="زيادة">+</button></div>
          ${l.item.pack_size > 1 && !l.item.is_service ? `<select class="small" data-unit="${esc(l.id)}" aria-label="الوحدة" title="غيّر الوحدة">
            <option value="pack" ${l.qty_base % l.item.pack_size === 0 ? "selected" : ""}>${esc(l.item.pack_unit && l.item.pack_unit !== "pack" ? l.item.pack_unit : "علبة")}</option>
            <option value="unit" ${l.qty_base % l.item.pack_size ? "selected" : ""}>${esc(unitName({ base_unit_name: l.item.base_unit }))}</option></select>` : ""}
          ${l.item.pack_size === 1 && !l.item.is_service && canFixPack()
            ? `<button type="button" class="btn small secondary" data-cart-pack="${esc(l.id)}">${PACK_QUESTION}</button>` : ""}</td>
        <td class="num">${linePrice(l)}</td>
        <td>${l.allocations.map((a) => `<span class="badge ${l.near_expiry ? "b-warn" : "b-ok"}">${l.near_expiry ? "⚠ " : ""}${esc((a.expiry_date || "—").slice(0, 7))}</span>`).join(" ")}</td>
        <td>${l.discount_source === "contract" ? `<span class="chip" title="خصم التعاقد">🤝 ${pct(l.discount_bp)}%</span>` : canDisc ? `<span class="row nowrap"><input class="narrow" type="number" min="0" step="0.01" value="${l.discount_amount ? money(l.discount_amount) : l.discount_bp ? pct(l.discount_bp) : ""}" data-disc="${esc(l.id)}" placeholder="خصم" autocomplete="off"><select data-dunit="${esc(l.id)}" title="نسبة أو مبلغ"><option value="%" ${l.discount_amount ? "" : "selected"}>%</option><option value="egp" ${l.discount_amount ? "selected" : ""}>ج</option></select></span>`
          : l.discount_amount ? `${money(l.discount_amount)} ج` : l.discount_bp ? `${pct(l.discount_bp)}%` : `<button class="btn small secondary" data-askdisc="${esc(l.id)}">خصم</button>`}${l.invoice_share ? `<div class="muted small" title="نصيبه من الخصم على الفاتورة">+ ${money(l.invoice_share)}</div>` : ""}</td>
        <td class="num"><b>${money(l.net)}</b></td>
        <td><button class="btn small danger" data-rm="${esc(l.id)}" aria-label="إزالة الصنف">✕</button></td></tr>`).join("")
        || `<tr><td colspan="7" class="muted">امسح أول صنف</td></tr>`}</tbody>
    </table></div><div id="posSafety"></div>`;
  posMark();
  syncQuick();
  const line = (id) => s.lines.find((l) => l.id === id);
  $("#posCart").querySelectorAll("[data-pos-card]").forEach((button) => button.addEventListener("click", () =>
    posInvoiceCard(line(button.dataset.posCard))));
  $("#posCart").querySelectorAll("[data-cart-pack]").forEach((button) => button.addEventListener("click", () =>
    withBusy(button, () => packFix(line(button.dataset.cartPack).item_id, null, true))));
  const step = (l) => (l.item.pack_size > 1 && l.qty_base % l.item.pack_size === 0 ? l.item.pack_size : 1);
  // U2-13: the same count in the other unit (1 box -> 1 strip, 2 strips -> 2 boxes); the server re-checks the stock
  $("#posCart").querySelectorAll("[data-unit]").forEach((sel) => sel.addEventListener("change", () => {
    const l = line(sel.dataset.unit), ps = l.item.pack_size, q = l.qty_base, isPack = q % ps === 0;
    let want = q;
    if (sel.value === "unit" && isPack) want = q / ps;                 // 2 boxes -> 2 strips
    else if (sel.value === "pack" && !isPack) {
      // a few strips -> the same number of boxes; a mixed line ("2 boxes + 1 strip") -> its whole boxes, never
      // multiplied (5 strips became 5 boxes in the independent review)
      want = q < ps ? q * ps : Math.floor(q / ps) * ps;
      if (q > ps && !confirm(`السطر هيبقى ${want / ps} علبة بدل ${qtyText(q, l.item)}؟`)) { renderCart(); return; }
    }
    if (want !== q) patchLine(l.id, { qty_base: want });
  }));
  $("#posCart").querySelectorAll("[data-plus]").forEach((b) => b.addEventListener("click", () => { const l = line(b.dataset.plus); patchLine(l.id, { qty_base: l.qty_base + step(l) }); }));
  $("#posCart").querySelectorAll("[data-minus]").forEach((b) => b.addEventListener("click", async () => {
    const l = line(b.dataset.minus), n = l.qty_base - step(l);
    if (n <= 0) { SALE = await api(`/api/sales/${SALE.id}/lines/${l.id}`, { method: "DELETE" }); renderCart(); renderSide(); }
    else patchLine(l.id, { qty_base: n });
  }));
  const sendDisc = (id) => {
    const inp = $(`[data-disc="${CSS.escape(id)}"]`, $("#posCart")), unit = $(`[data-dunit="${CSS.escape(id)}"]`, $("#posCart")).value;
    const v = inp.value.trim();
    if (unit === "egp") {
      const p = v === "" ? 0 : exactPiasters(v);
      if (p == null || p < 0) { POS.saving = Promise.resolve(false); return toast("مبلغ الخصم غير صحيح", true); }
      return patchLine(id, { discount_amount: p });
    }
    patchLine(id, { discount_bp: toBp(v) || 0 });
  };
  $("#posCart").querySelectorAll("[data-disc]").forEach((inp) => inp.addEventListener("change", () => sendDisc(inp.dataset.disc)));
  $("#posCart").querySelectorAll("[data-dunit]").forEach((sel) => sel.addEventListener("change", () => {
    if ($(`[data-disc="${CSS.escape(sel.dataset.dunit)}"]`, $("#posCart")).value.trim() !== "") sendDisc(sel.dataset.dunit);
  }));
  $("#posCart").querySelectorAll("[data-askdisc]").forEach((b) => b.addEventListener("click", () => {
    const v = discAsk(prompt("الخصم: نسبة (مثال 5) أو مبلغ بالجنيه (مثال 3ج):"));
    if (v) patchLine(b.dataset.askdisc, v);
  }));
  $("#posCart").querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", async () => {
    SALE = await api(`/api/sales/${SALE.id}/lines/${b.dataset.rm}`, { method: "DELETE" }); renderCart(); renderSide();
  }));
  if (findings) { app.inert = false; renderFindings(findings); }
}

function chanOptions(kind, sel) {
  const list = CHANNELS.filter((c) => c.kind === kind);
  return list.length ? `<select data-ch>${list.map((c) => `<option value="${esc(c.id)}" ${c.id === sel ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>` : "";
}

// U3-6: the chosen customer's usual items (last 6 months), one click adds his usual quantity
async function posUsual() {
  const box = $("#posUsual"), cid = SALE?.customer_id;
  if (!box || !cid) return;
  const sale = SALE.id;
  // one request per customer and invoice, even while renderSide runs again before it answers (review of #419)
  if (POS.usual?.cid !== cid || POS.usual?.sale !== sale) {
    POS.usual = { cid, sale, items: api(`/api/customers/${cid}/usual`).then((r) => r.items) };
  }
  const mine = POS.usual;
  let all;
  try { all = await mine.items; } catch { if (POS.usual === mine) POS.usual = null; return; }
  if (SALE?.customer_id !== cid || SALE?.id !== sale || !$("#posUsual")) return;
  const items = all.slice(0, 8);
  $("#posUsual").innerHTML = items.length ? `<p class="muted small">أدويته المعتادة (آخر 6 شهور):</p>${items.map((x, i) =>
    `<button class="btn small secondary" data-usual="${i}" ${x.in_stock ? "" : "disabled title=\"مش متوفر\""}>＋ ${esc(x.name_en || x.name_ar || "")}
      <span class="muted">${esc(fmtStock(x.last_qty || 1, x))}${x.in_stock && x.add_qty < (x.last_qty || 1) ? ` · المتاح ${esc(fmtStock(x.add_qty, x))}` : ""}${x.refill_days_left != null ? ` · ميعاده ${x.refill_days_left <= 0 ? "دلوقتي" : `بعد ${x.refill_days_left} يوم`}` : ""}</span></button>`).join("")}` : "";
  $("#posUsual").querySelectorAll("[data-usual]").forEach((b) => b.addEventListener("click", () => {
    const x = items[+b.dataset.usual], q = x.add_qty || x.last_qty || 1, ps = x.pack_size || 1;   // no more than in stock
    addItem({ ...x, id: x.item_id }, q % ps === 0 ? q / ps : 0, q % ps === 0 ? 0 : q);
  }));
}
function renderSide() {
  const s = SALE, d = s.delivery || {};
  if (!POS.split) POS.parts = [];
  const remaining = dueNow(s) - POS.parts.reduce((a, p) => a + (p.amount || 0), 0);
  const rnd = dueNow(s) - s.total;                       // U2-14: the cash rounding of this payment
  $("#posSide").innerHTML = `
    <section class="panel">
      <div class="seg2" role="radiogroup" aria-label="نوع الفاتورة">
        <button class="${s.kind !== "delivery" ? "on" : ""}" data-kind="store">${s.kind !== "delivery" ? "✓ " : ""}في الصيدلية</button>
        <button class="${s.kind === "delivery" ? "on" : ""}" data-kind="delivery">${s.kind === "delivery" ? "✓ " : ""}توصيل للمنزل</button></div>
      <label class="f mt">العميل ${s.kind === "delivery" ? "" : "(اختياري)"}
        <input id="posCust" placeholder="الاسم أو الموبايل أو الكود" value="${s.customer ? esc(s.customer.name) : ""}" autocomplete="off"></label>
      <div id="posCustRes" class="cands"></div>
      ${s.customer ? `<p class="muted">رصيده ${money(s.customer.balance)} / حد الآجل ${money(s.customer.credit_limit)} <a href="#" id="custClear">إزالة العميل</a></p>
        ${s.customer.balance < 0 && s.lines.length && s.kind !== "delivery" ? `<p><button class="btn small" id="useCredit" type="button">💰 له رصيد ${money(-s.customer.balance)} ج — استخدمه في الفاتورة</button></p>` : ""}
        ${s.contract ? `<p><span class="chip">🤝 تعاقد ${esc(s.contract.name)}: محلي ${pct(s.contract.discount_local_bp)}% / مستورد ${pct(s.contract.discount_imported_bp)}%</span></p>` : ""}
        <div id="posUsual" class="usual"></div>` : `<p><a href="#" id="posCustNew">+ عميل جديد</a></p>`}
      ${s.kind === "delivery" ? `<div class="deliv">
        <label class="f">العنوان <input data-dl="address" value="${esc(d.address || "")}" autocomplete="off"></label>
        <label class="f">الهاتف <input data-dl="phone" inputmode="tel" value="${esc(d.phone || "")}" autocomplete="off"></label>
        <label class="f">مندوب التوصيل <input data-dl="courier" value="${esc(d.courier || "")}" autocomplete="off"></label>
        <label class="f">رسوم التوصيل (ج) <input data-dl="fee" type="number" min="0" step="0.5" value="${s.delivery_fee ? money(s.delivery_fee) : ""}" autocomplete="off"></label></div>` : ""}
      <div class="sum"><span>الإجمالي</span><b>${money(s.subtotal)}</b></div>
      ${s.lines.length ? `<div class="row nowrap mt" id="posInvDiscRow"><label class="f">خصم على الفاتورة
        <input id="posInvDisc" class="narrow" type="number" min="0" step="0.01" value="${s.invoice_discount_amount ? money(s.invoice_discount_amount) : s.invoice_discount_bp ? pct(s.invoice_discount_bp) : ""}" placeholder="0" autocomplete="off"></label>
        <select id="posInvDiscU" title="نسبة أو مبلغ"><option value="%" ${s.invoice_discount_amount ? "" : "selected"}>%</option><option value="egp" ${s.invoice_discount_amount ? "selected" : ""}>ج</option></select></div>` : ""}
      ${s.discount_total ? `<div class="sum ok"><span>خصم</span><b>${money(s.discount_total)}</b></div>` : ""}
      ${s.delivery_fee ? `<div class="sum"><span>رسوم التوصيل</span><b>${money(s.delivery_fee)}</b></div>` : ""}
      ${rnd ? `<div class="sum"><span>تقريب النقدي</span><b>${rnd > 0 ? "+" : "−"}${money(Math.abs(rnd))}</b></div>` : ""}
      <div class="sum due"><span>المطلوب</span><b>${money(dueNow(s))}</b></div>
    </section>
    <section class="panel">
      ${POS.split ? `<h2>تقسيم الدفع</h2>${POS.parts.map((p, i) => `<div class="row part">
          <select data-pm="${i}">${METHODS.map(([m, t]) => `<option value="${m}" ${p.method === m ? "selected" : ""}>${t}</option>`).join("")}</select>
          ${["card", "transfer"].includes(p.method) ? chanOptions(p.method, p.channel_id).replace("data-ch", `data-pc="${i}"`) : ""}
          <input class="narrow" type="number" step="0.01" min="0" data-pa="${i}" value="${money(p.amount)}" autocomplete="off">
          <button class="btn small secondary" data-prm="${i}" aria-label="إزالة">✕</button></div>`).join("")}
        ${POS.parts.some((p) => p.method === "transfer") ? proofInput(s) : ""}
        <button class="btn small secondary" id="partAdd">+ طريقة دفع</button>
        <p class="badge ${remaining === 0 ? "b-ok" : "b-warn"} mt">${remaining === 0 ? "✓ الدفع كامل" : `⚠ المتبقي ${money(remaining)}`}</p>
        <p><a href="#" id="splitOff">رجوع لطريقة دفع واحدة</a></p>`
      : `<div class="paym">${METHODS.map(([m, t]) => `<button class="${POS.method === m ? "on" : ""}" data-m="${m}" ${m === "credit" && !s.customer ? "disabled title=\"اختر العميل أولًا\"" : ""}>${t}</button>`).join("")}</div>
        ${["card", "transfer"].includes(POS.method) ? `<label class="f mt">${POS.method === "card" ? "فيزا عن طريق" : "أي محفظة"} ${chanOptions(POS.method, POS.channel)}</label>` : ""}
        ${POS.method === "transfer" ? proofInput(s) : ""}
        ${POS.method === "cash" ? `<label class="f mt">العميل دفع <input id="payGiven" type="number" step="0.01" min="0" class="big" placeholder="${money(dueNow(s))}" ${s.lines.length ? "" : 'disabled title="ضيف صنف الأول"'} autocomplete="off"></label>
          <div class="sum ok" id="changeBox" hidden><span>الباقي للعميل</span><b id="changeV"></b></div>
          ${s.customer ? `<label class="f" id="keepBox" hidden><input type="checkbox" id="keepChange"> خليها رصيد للعميل (الباقي يتسجل له في حسابه)</label>` : ""}
          <div id="noChange" hidden><button class="btn small secondary" id="noChangeBtn" type="button">🪙 مفيش فكة</button>
            <span class="muted small" id="noChangeHint" hidden>آخر تقفيل مفيهوش جنيهات فكة</span><div id="noChangeRes"></div></div>` : ""}
        <p><a href="#" id="splitOn">تقسيم الدفع (جزء نقدًا وجزء فيزا أو محفظة)</a></p>`}
      <div class="row rcpt">
        <label class="chk" title="${s.kind === "delivery" ? "التوصيل يطبع إيصالًا واحدًا تلقائيًا" : `اطبع إيصال لهذا البيع (${esc(posKey("receipt"))})`}"><input type="checkbox" id="posRcpt" ${receiptWanted(s, RCFG, POS.rcpt[s.id]) ? "checked" : ""} ${s.kind === "delivery" ? "disabled" : ""}> 🧾 إيصال <kbd>${esc(posKey("receipt"))}</kbd></label>
        ${s.kind !== "delivery" && POS.rcpt[s.id] == null && RCFG?.auto_receipt ? `<span class="muted small">تلقائي من ${money(RCFG.auto_receipt_min)} ج</span>` : ""}</div>
      <button class="btn big" id="posDone" ${s.lines.length || pendingPosAdds(s.id).length ? "" : "disabled"}>إتمام البيع <kbd>${esc(posKey("done"))}</kbd></button>
    </section>`;
  $("#posProof")?.addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api(`/api/sales/${SALE.id}/attachments`, { method: "POST", form: fd }); SALE.attachments = r.attachments;
      toast(r.duplicate_of ? `⚠ هذه الصورة نفسها مسجلة مع الفاتورة ${r.duplicate_of}` : "✓ تم حفظ صورة التحويل مع الفاتورة", !!r.duplicate_of); renderSide(); }
    catch (err) { toast(err.message, true); }
  });
  // U2-10 (owner: the screen froze on customer → «توصيل للمنزل»): one change at a time (the buttons are off while
  // it saves, so a second click cannot race the first), the chosen customer's phone and address fill the delivery,
  // and any error is shown instead of leaving the screen waiting
  const fillFromCustomer = async () => {
    const fill = {};
    if (SALE.kind === "delivery" && SALE.customer?.phone && !SALE.delivery?.phone) fill.phone = SALE.customer.phone;
    if (SALE.kind === "delivery" && SALE.customer?.address1 && !SALE.delivery?.address) fill.address = SALE.customer.address1;
    if (Object.keys(fill).length) SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { delivery: fill } });
  };
  document.querySelectorAll("#posSide [data-kind]").forEach((b) => b.addEventListener("click", async () => {
    if (POS.kindBusy || (b.dataset.kind === "delivery") === (SALE.kind === "delivery")) return;
    POS.kindBusy = true;
    document.querySelectorAll("#posSide [data-kind]").forEach((x) => { x.disabled = true; });
    try { SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { kind: b.dataset.kind } }); await fillFromCustomer(); }
    catch (er) { toast(er.message, true); }
    finally { POS.kindBusy = false; renderSide(); refreshTabLabel(); }
  }));
  document.querySelectorAll("[data-dl]").forEach((inp) => inp.addEventListener("change", async () => {
    const k = inp.dataset.dl, v = k === "fee" ? toPiasters(inp.value) || 0 : inp.value;
    POS.saving = (async () => {        // complete() waits for it: F9 typed in the address field (11/01)
      try { SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { delivery: { [k]: v } } }); if (k === "fee") renderSide(); return true; }
      catch (er) { toast(er.message, true); return false; }
    })();
    await POS.saving;
  }));
  $("#posCust").addEventListener("input", async (e) => {
    const v = e.target.value.trim(); if (v.length < 2) { $("#posCustRes").innerHTML = ""; return; }
    const cs = await api(`/api/customers?q=${encodeURIComponent(v)}`);
    $("#posCustRes").innerHTML = cs.map((c) => `<button class="btn small secondary" data-cust="${esc(c.id)}">${esc(c.name)}${c.name_alt ? ` / ${esc(c.name_alt)}` : ""}${c.phone ? ` — ${esc(c.phone)}` : ""}${c.estock_code != null ? ` <span class="muted">كود ${esc(c.estock_code)}</span>` : ""}</button>`).join("") || `<p class="muted">لا يوجد عميل بهذا الاسم</p>`;
    $("#posCustRes").querySelectorAll("[data-cust]").forEach((b) => b.addEventListener("click", async () => {
      try { SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { customer_id: b.dataset.cust } }); await fillFromCustomer(); }
      catch (er) { toast(er.message, true); }
      renderSide();
    }));
  });
  $("#custClear")?.addEventListener("click", async (e) => { e.preventDefault(); SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { customer_id: null } }); if (POS.method === "credit") POS.method = "cash"; renderSide(); });
  $("#posCustNew")?.addEventListener("click", (e) => { e.preventDefault(); newCustomerDialog(); });
  const invDisc = async () => {         // one discount on the whole invoice, spread over the lines (update 1)
    const v = $("#posInvDisc").value.trim(), egp = $("#posInvDiscU").value === "egp";
    const body = egp ? { amount: v === "" ? 0 : exactPiasters(v) } : { bp: toBp(v) || 0 };
    if ((egp && (body.amount == null || body.amount < 0)) || (!egp && (Number.isNaN(body.bp) || body.bp < 0))) {
      POS.saving = Promise.resolve(false);          // an invalid discount stops F9 too (Codex #400)
      return toast("الخصم غير صحيح", true);
    }
    POS.saving = (async () => {          // complete() waits for it; false = not saved, the sale waits (Codex #400)
      try { SALE = await api(`/api/sales/${SALE.id}/invoice-discount`, { method: "PUT", body }); renderCart(); renderSide(); return true; }
      catch (er) { toast(er.message, true); renderSide(); return false; }
    })();
    await POS.saving;
  };
  $("#posInvDisc")?.addEventListener("change", invDisc);
  $("#posInvDiscU")?.addEventListener("change", () => { if ($("#posInvDisc").value.trim() !== "") invDisc(); });
  document.querySelectorAll("[data-m]").forEach((b) => b.addEventListener("click", () => {
    POS.method = b.dataset.m; POS.channel = CHANNELS.find((c) => c.kind === POS.method)?.id || null; renderSide();
  }));
  $("#posSide [data-ch]")?.addEventListener("change", (e) => { POS.channel = e.target.value; });
  $("#payGiven")?.addEventListener("keydown", (e) => {      // U4-17: Enter here finishes the sale, like F9
    if (e.key !== "Enter" || e.ctrlKey) return;
    e.preventDefault();
    if (e.repeat || e.timeStamp - (POS.scanAt || -Infinity) < 150) return;
    if (e.target.validity?.badInput) {                       // «50..» reads as empty: never the exact amount
      toast("المبلغ اللي العميل دفعه مش رقم صحيح. صحّحه أو امسحه", true);
      e.target.select();
      return;
    }
    const confirmButton = $("#posConfirm");
    if (confirmButton) confirmButton.focus();
    else complete();
  });
  $("#payGiven")?.addEventListener("input", (e) => {
    const g = toPiasters(e.target.value), ch = g - dueNow(s);
    $("#changeBox").hidden = !(g >= dueNow(s)); $("#changeV").textContent = money(ch);
    if ($("#keepBox")) $("#keepBox").hidden = !(g > dueNow(s) && g - dueNow(s) <= 5000);   // real change only (50 EGP)
    $("#noChange").hidden = !(ch > 0);
    $("#noChangeRes").innerHTML = "";
    if (!$("#noChange").hidden && !POS.noCoinsAsked) {          // the hint: from the last close, once per invoice
      POS.noCoinsAsked = true;
      api(`/api/sales/change-fill?change=0`).then((r) => { POS.noCoins = r.no_pound_coins; if ($("#noChangeHint")) $("#noChangeHint").hidden = !r.no_pound_coins; }).catch(() => {});
    } else if ($("#noChangeHint")) $("#noChangeHint").hidden = !POS.noCoins;
  });
  $("#noChangeBtn")?.addEventListener("click", async () => {       // U2-20: an item instead of the coins
    const given = toPiasters($("#payGiven").value), ch = given - dueNow(SALE);
    if (!(ch > 0)) return;
    try {
      const r = await api(`/api/sales/change-fill?change=${ch}&sale=${encodeURIComponent(SALE.id)}`);
      $("#noChangeRes").innerHTML = `${r.items.length ? `<p class="muted small">أصناف بسعر يناسب الباقي (${money(ch)} ج):</p>${r.items.map((it) => `<button class="btn small secondary" type="button" data-cf="${esc(it.id)}">${esc(it.name)} — ${money(it.price)} ج</button>`).join(" ")}` : `<p class="muted small">مفيش صنف متاح بسعر يناسب الباقي</p>`}`;
      $("#noChangeRes").querySelectorAll("[data-cf]").forEach((x) => x.addEventListener("click", () => {
        const it = r.items.find((i) => i.id === x.dataset.cf);
        if (!it) return;
        addItem({ id: it.id }, 1, 0).then(() => {      // the customer's money stays typed: the new change shows
          const g = $("#payGiven"); if (g) { g.value = money(given); g.dispatchEvent(new Event("input")); }
        });
      }));
    } catch (er) { toast(er.message, true); }
  });
  // manager 22/11: the customer's credit (a change he left) in one click, never more than his balance or the invoice;
  // the rest is cash, so the drawer expects only the cash really paid
  $("#useCredit")?.addEventListener("click", () => {
    const use = Math.min(-s.customer.balance, s.total), rest = s.total - use;
    POS.split = true;
    POS.parts = [{ method: "credit", amount: use }, ...(rest > 0 ? [{ method: "cash", amount: rest + roundNear(rest, s.rounding_step || 0) }] : [])];
    renderSide();
  });
  $("#splitOn")?.addEventListener("click", (e) => { e.preventDefault(); POS.split = true; POS.parts = [{ method: "cash", amount: s.total + roundNear(s.total, s.rounding_step || 0) }]; renderSide(); });
  $("#splitOff")?.addEventListener("click", (e) => { e.preventDefault(); POS.split = false; renderSide(); });
  $("#partAdd")?.addEventListener("click", () => { POS.parts.push({ method: "card", channel_id: CHANNELS.find((c) => c.kind === "card")?.id, amount: Math.max(0, remaining) }); renderSide(); });
  document.querySelectorAll("[data-pm]").forEach((x) => x.addEventListener("change", () => { const p = POS.parts[+x.dataset.pm]; p.method = x.value; p.channel_id = CHANNELS.find((c) => c.kind === x.value)?.id || null; renderSide(); }));
  document.querySelectorAll("[data-pc]").forEach((x) => x.addEventListener("change", () => { POS.parts[+x.dataset.pc].channel_id = x.value; }));
  document.querySelectorAll("[data-pa]").forEach((x) => x.addEventListener("change", () => { POS.parts[+x.dataset.pa].amount = toPiasters(x.value) || 0; renderSide(); }));
  document.querySelectorAll("[data-prm]").forEach((x) => x.addEventListener("click", () => { POS.parts.splice(+x.dataset.prm, 1); renderSide(); }));
  $("#posRcpt").addEventListener("click", (e) => {
    if (s.kind === "delivery") { e.preventDefault(); return; }
    POS.rcpt[s.id] = e.target.checked;              // an explicit choice for this sale, kept when the total changes
    e.target.closest(".rcpt").querySelector(".muted")?.remove();
  });
  $("#posDone").addEventListener("keydown", (e) => {          // a held Enter (key repeat) never presses it (U4-17 review)
    if ((e.key === "Enter" || e.key === " ") && (e.repeat || e.timeStamp - (POS.scanAt || -Infinity) < 150)) e.preventDefault();
  });
  $("#posDone").addEventListener("click", () => {
    const confirmButton = $("#posConfirm");
    if (confirmButton) confirmButton.focus();
    else complete();
  });
  posUsual();
}

// A real form instead of two prompt() boxes (device test 11/01): what was typed in the customer search
// fills the name or the phone, a refusal (a phone already used) stays on the form, and one click saves once.
function newCustomerDialog() {
  const typed = ($("#posCust")?.value || "").trim(), isPhone = /^[+\d\s-]{5,}$/.test(typed);
  const saleId = SALE.id;                          // the invoice this form belongs to, even if the save is slow
  const d = document.createElement("dialog"); d.id = "custNewDlg";
  d.innerHTML = `<form method="dialog" class="formgrid"><h2>عميل جديد</h2>
    <label class="f">اسم العميل <input name="name" maxlength="120" required autocomplete="off" value="${isPhone ? "" : esc(typed)}"></label>
    <label class="f">الموبايل (اختياري) <input name="phone" inputmode="tel" maxlength="20" autocomplete="off" value="${isPhone ? esc(typed) : ""}"></label>
    <label class="f">رقم تاني (اختياري) <input name="phone2" inputmode="tel" maxlength="20" autocomplete="off"></label>
    <label class="f">العنوان (اختياري) <input name="address1" maxlength="200" autocomplete="off" value="${esc(SALE?.kind === "delivery" ? SALE.delivery?.address || "" : "")}"></label>
    <p class="badge b-err" data-err hidden></p>
    <div class="row"><button class="btn" value="ok">حفظ واختيار العميل</button><button class="btn secondary" value="cancel" formnovalidate>إلغاء</button></div></form>`;
  document.body.append(d);
  const f = d.querySelector("form"), err = d.querySelector("[data-err]"), ok = f.querySelector("[value=ok]");
  const [nameIn, phoneIn, addrIn, phone2In] = ["name", "phone", "address1", "phone2"].map((n) => f.elements.namedItem(n));
  const done = () => { d.close(); d.remove(); $("#posQ")?.focus(); };
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (ok.disabled) return;                          // saving: no second save, and no closing mid-save
    if (ev.submitter?.value === "cancel") { done(); return; }
    const body = { name: nameIn.value.trim(), phone: phoneIn.value.trim() || null, credit_limit: 0 };
    if (addrIn?.value.trim()) body.address1 = addrIn.value.trim();
    if (phone2In?.value.trim()) body.phone2 = phone2In.value.trim();
    if (!body.name) { nameIn.focus(); return; }
    ok.disabled = true;
    try {
      const c = await api("/api/customers", { method: "POST", body });
      SALE = await api(`/api/sales/${saleId}`, { method: "PATCH", body: { customer_id: c.id } });
      const fill = {};                                // like choosing an existing customer: fill only what is empty
      if (SALE.kind === "delivery" && body.phone && !SALE.delivery?.phone) fill.phone = body.phone;
      if (SALE.kind === "delivery" && body.address1 && !SALE.delivery?.address) fill.address = body.address1;
      if (Object.keys(fill).length) SALE = await api(`/api/sales/${saleId}`, { method: "PATCH", body: { delivery: fill } });
      done(); renderSide();
    } catch (er) { err.hidden = false; err.textContent = `✕ ${er.message}`; ok.disabled = false; }
  });
  d.addEventListener("cancel", (ev) => { ev.preventDefault(); if (!ok.disabled) done(); });
  d.showModal();
  (isPhone || !typed ? nameIn : phoneIn).focus();
}

function refreshTabLabel() {
  const t = document.querySelector(`.tab.on`);
  if (!t || !SALE) return;
  const n = (t.dataset.n ||= (t.firstChild.textContent.match(/\d+/) || ["1"])[0]);
  t.firstChild.textContent = `\n      فاتورة ${n}${SALE.kind === "delivery" ? " · توصيل" : ""} · ${SALE.lines.length ? `${SALE.lines.length} صنف` : "فارغة"}\n      `;
  if (I18N.on) trNode(t);
}

function renderFindings(findings) {
  const needs = (f) => ["override", "block", "acknowledge"].includes(f.requires);
  $("#posSafety").innerHTML = `<div class="panel mt"><h2>فحص السلامة الدوائية</h2>
    ${findings.map((f, i) => `<div class="mt"><span class="badge ${SEV[f.severity][1]}">${{ item_rx: "📋 بوصفة طبية فقط", item_misuse: "⚠ يُساء استخدامه", item_note: "📝 ملاحظة", item_recall_pending: "⚠ منشور سحب لم يُراجَع" }[f.code] || SEV[f.severity][0]}</span> ${esc(f.message)}
      ${f.source ? `<div class="muted">المرجع: ${esc(f.source.name)} ${esc(f.source.version)} ${esc(f.source.ref || "")}</div>` : ""}
      ${f.basis === "heuristic" ? `<div class="muted">تنبيه مبني على تشابه الأسماء</div>` : ""}
      ${needs(f) ? `<input class="mt" data-dec="${i}" placeholder="${f.requires === "acknowledge" ? "ملاحظة (اختياري) ثم تأكيد" : "اكتب سبب الاستمرار (يُسجَّل باسمك)"}" autocomplete="off">` : ""}</div>`).join("") || "<p>لا توجد تنبيهات</p>"}
    <button class="btn mt" id="posConfirm">تأكيد وإتمام البيع</button></div>`;
  $("#posConfirm").addEventListener("click", () => complete(findings));
  // Heikal PC 17/11 (S1): the panel opens under the cart; on a short window «إتمام البيع» seemed to do nothing.
  // Bring it into view and put the cursor where the cashier must act (first reason box, else the confirm button).
  $("#posSafety").scrollIntoView({ block: "center" });
  ($("#posSafety [data-dec]") || $("#posConfirm")).focus({ preventScroll: true });
}

// U2-14 (owner 22/11): card, wallet, transfer and credit are exact; only what is left for cash is rounded to the
// nearest step (ties up), as the server does
function roundNear(v, st) {
  if (!st || v <= 0) return 0;
  const r = v % st, d = 2 * r >= st ? st - r : -r;
  return v + d > 0 ? d : 0;                              // never rounds the cash part away
}
function cashRoundingOf(s, parts) {
  if (!parts.some((p) => p.method === "cash")) return 0;  // no cash, no rounding: card and credit are exact
  const nonCash = parts.filter((p) => p.method !== "cash").reduce((t, p) => t + (p.amount || 0), 0);
  return roundNear(s.total - nonCash, s.rounding_step || 0);
}
// what the customer is asked for with the chosen payment (a single cash payment: the rounded total)
function dueNow(s) {
  if (POS.split) return s.total + cashRoundingOf(s, POS.parts);
  return POS.method === "cash" ? s.total + roundNear(s.total, s.rounding_step || 0) : s.total;
}
function currentPayments() {
  if (POS.split) return POS.parts.filter((p) => p.amount > 0).map((p) => ({ method: p.method, amount: p.amount, channel_id: p.channel_id || null }));
  return [{ method: POS.method, amount: dueNow(SALE), channel_id: ["card", "transfer"].includes(POS.method) ? ($("#posSide [data-ch]")?.value || POS.channel) : null }];
}

// a discount still being typed (line or invoice): saved before completion like a delivery field (Codex #400)
const isDiscField = (el) => !!(el?.dataset?.disc || el?.id === "posInvDisc");
async function complete(findings = null) {
  if (POS.completing || !SALE || !$("#posDone")) return;
  POS.completing = true;
  const sid = SALE.id;
  try {
    // A delivery field or discount still being typed must dispatch its change before the till is frozen.
    posScanStop?.flush?.();                       // typing held back by the scan detector goes back first
    const active = document.activeElement;
    if (active?.dataset?.dl || active?.dataset?.pa || isDiscField(active)) active.blur();
    app.inert = true;
    // A barcode can be resolving while the cart is still empty. Wait for work started before F9.
    const added = await Promise.all(pendingPosAdds(sid));
    if (added.includes(false) || SALE?.id !== sid || !SALE.lines.length || !$("#posDone")) return;
    while (await finishSale(findings, sid)) { /* retry with the reason the cashier entered */ }
  } catch (er) { toast(er.message, true); }
  finally {
    app.inert = false;
    POS.completing = false;
    if (!document.activeElement?.closest(".pos") && !$("dialog[open]")) $("#posQ")?.focus();
  }
}

async function finishSale(findings, sid) {
  app.inert = true;
  const saved = await POS.saving;
  if (SALE?.id !== sid || !$("#posDone")) return;
  if (saved === false) { POS.saving = null; return; }   // a field not saved: no sale at the old total (Codex #400)
  // The customer box is a search field: a name typed there and not chosen from the list changes nothing. The
  // invoice (its credit, its contract discount) would go to the customer chosen before while the screen shows
  // another name, so the sale waits until the box and the invoice say the same customer.
  const custBox = $("#posCust"), bare = (v) => String(v ?? "").replace(/\s+/g, "");
  if (SALE.customer && custBox && bare(custBox.value) !== bare(SALE.customer.name)) {
    toast(`الفاتورة مسجلة على العميل «${SALE.customer.name}» والمكتوب في خانة العميل غيره. اختر العميل من القائمة أو اضغط «إزالة العميل»، ثم أتمم البيع`, true);
    app.inert = false;
    custBox.focus();
    return;
  }
  const payments = currentPayments();
  if (!findings) {
    const r = await api(`/api/sales/${sid}/check`, { method: "POST", body: {} });
    if (SALE?.id !== sid || !$("#posDone")) return;
    const blocking = r.findings.filter((f) => ["override", "block", "acknowledge"].includes(f.requires));
    // launch day 21/11 (owner): only a real risk stops the sale; «no data» notes (no active ingredient, no
    // interaction list) and look-alike hints never ask for a click
    if (blocking.length || r.findings.some((f) => f.code === "item_recall_pending")) {   // a recall: always shown
      renderCart(r.findings); return;
    }
    findings = r.findings;
  }
  const decisions = {};
  document.querySelectorAll("input[data-dec]").forEach((inp) => {
    const f = findings[+inp.dataset.dec];
    if (!f) return;
    if (f.requires === "acknowledge") decisions[inp.dataset.dec] = inp.value || "تمت المراجعة";
    else if (inp.value.trim()) decisions[inp.dataset.dec] = inp.value.trim();
  });
  // F27: what the customer handed over — printed with the change due, never stored
  const gv = toPiasters($("#payGiven")?.value), given = Number.isFinite(gv) && gv > 0 ? gv : 0;
  // a typed amount below the cash part is a short tender, not a full payment (#324 M2-03)
  const cashPart = payments.filter((p) => p.method === "cash").reduce((t, p) => t + (p.amount || 0), 0);
  if (given && given < cashPart) {
    toast(`المبلغ الذي دفعه العميل أقل من المطلوب نقدًا. صحّح المبلغ أو قسّم الدفع (${money(given)} / ${money(cashPart)})`, true);
    app.inert = false;
    $("#payGiven")?.focus();
    return;
  }
  SALE.request_id = SALE.request_id || newKey();
  // «خليها رصيد للعميل»: the change he leaves is his credit, so no receipt says it is still due (Codex P2 on #424)
  const keepIt = !!$("#keepChange")?.checked && given > cashPart, shown = keepIt ? cashPart : given;
  try {
    const done = await api(`/api/sales/${sid}/complete`, { method: "POST",
      body: { payments, safety_decisions: decisions, version: SALE.version, request_id: SALE.request_id, cash_given: given, keep_change: keepIt,
              cost_reason: SALE.cost_reason || null, receipt: receiptWanted(SALE, RCFG, POS.rcpt[sid]), defer_devices: true } });
    delete POS.rcpt[sid]; POS.noCoinsAsked = false;
    try { sessionStorage.setItem("posLast", JSON.stringify({ id: done.id, no: done.sale_no })); } catch { /* this tab only */ }
    toast(`✓ تم البيع — فاتورة رقم ${done.sale_no}${done.kind === "delivery" ? (done.delivery?.cash_due ? ` (توصيل: ${money(done.delivery.cash_due)} ج نقدي مع مندوب التوصيل حتى يسلّمها)` : " (توصيل: مدفوعة من غير نقدي، مفيش فلوس مع المندوب)") : ""}`);
    if (SALE?.id === sid) SALE = null;
    try {
      if (location.hash.startsWith("#/pos")) await viewPos(true);
    } finally {
      app.inert = false;
      $("#posQ")?.focus();
      showCheckoutDevices(done, shown).catch(() => checkoutDeviceNotice(done, "unknown", "unknown"));
    }
  } catch (er) {
    toast(er.message, true);
    app.inert = false;
    if (SALE?.id !== sid || !$("#posDone")) return;
    if (er.code === "safety_unresolved") renderCart(er.details.findings);
    if (er.code === "not_draft") { SALE = null; viewPos(); }
    // the invoice on the server is not the one on the screen (a price, a contract discount or the stock
    // moved, or it was edited elsewhere): show it again, the amount due included, before the next F9.
    // The side panel was left as it was, so the sale could complete at a total the cashier never saw.
    if (["price_changed", "payment_mismatch", "stale_version"].includes(er.code)) {
      try {
        const was = SALE.total;
        SALE = await api(`/api/sales/${SALE.id}`); renderCart(); renderSide();
        if (er.code === "payment_mismatch" && SALE.total !== was) toast("تغيّر إجمالي الفاتورة، راجع الإجمالي الجديد ثم أتمم البيع", true);
      } catch { /* the message is already shown */ }
    }
    if (er.code === "no_open_shift") $("#posShift").click();
    if (er.code === "at_cost_reason") {        // U2-33: down to cost only, by the owner or a manager, with a reason
      const why = (prompt(`${er.message}\n${(er.details?.lines || []).map((l) => l.name).join("، ")}`, "") || "").trim();
      if (why.length >= 3) { SALE.cost_reason = why; return true; }
    }
  }
}

const checkoutPrinted = new Set();
async function showCheckoutDevices(sale, given) {
  let dv = sale.devices;
  if (sale.devices_pending) {
    const end = Date.now() + 60000;
    while (!dv && Date.now() < end) {
      ({ devices: dv } = await api(`/api/sales/${sale.id}/devices`));
      if (!dv) await new Promise((resolve) => setTimeout(resolve, 300));
    }
  }
  if (!dv && !sale.devices_pending) return;
  if ((dv?.receipt === "browser" || dv?.receipt === "failed") && !checkoutPrinted.has(sale.id)) {
    if (checkoutPrinted.size >= 512) checkoutPrinted.delete(checkoutPrinted.values().next().value);
    checkoutPrinted.add(sale.id);
    printInFrame(`/receipt/${sale.id}${given ? `?given=${given}` : ""}`);
  }
  checkoutDeviceNotice(sale, dv?.receipt || "unknown", dv?.drawer || "unknown", dv?.message);
}

function checkoutDeviceNotice(sale, receipt, drawer, message) {
  const warn = [];
  if (receipt === "failed") warn.push(`⚠ الإيصال لم يُطبع مباشرة: ${message || ""} — فُتحت الطباعة من المتصفح، وتقدر تعيد من «اطبع إيصال آخر فاتورة»`);
  if (drawer === "failed") warn.push("⚠ أمر فتح الدرج لم يصل للطابعة — افتحه يدويًا (F8) أو بالمفتاح");
  if (drawer === "unsupported") warn.push("⚠ الطباعة من المتصفح لا تفتح الدرج — افتحه يدويًا أو اختر طابعة مباشرة من «البرنامج ← طابعة الإيصال والدرج»");
  if (receipt === "unknown" || drawer === "unknown") warn.push("حفظ البيع تم، لكن نتيجة أمر الطباعة أو فتح الدرج غير متاحة");
  if (warn.length) toast(`فاتورة ${sale.sale_no}
${warn.join("\n")}`, true, 15000);
  else if (receipt === "sent" || drawer === "sent") toast(`فاتورة ${sale.sale_no}`
    + (receipt === "sent" ? " · أُرسل الإيصال للطابعة" : "") + (drawer === "sent" ? " · أُرسل أمر فتح الدرج" : ""));
}

function bindPosExtras() {
  $("#posShift").addEventListener("click", (e) => { e.preventDefault(); location.hash = "#/shift"; });
  $("#posKick")?.addEventListener("click", (e) => { e.preventDefault(); drawerDialog(); });
  // reprint the last sale of THIS tab (this till); never opens the drawer; one request at a time
  $("#posLast")?.addEventListener("click", async (e) => {
    e.preventDefault();
    const a = e.currentTarget, last = lastSale();
    if (!last || a.dataset.busy) return;
    a.dataset.busy = "1";
    try { await api(`/api/print/receipt/${last.id}`, { method: "POST", body: {} }); toast("✓ أُرسل الإيصال للطابعة"); }
    catch (er) { printInFrame(`/receipt/${last.id}`); if (er.code === "print_failed") toast(er.message, true); }
    finally { delete a.dataset.busy; }
  });
  $("#posDeliv").addEventListener("click", async (e) => {
    e.preventDefault();
    const ds = (await api("/api/deliveries")).deliveries;
    const ST = { out: ["b-warn", "⚠ خرجت"], delivered: ["b-info", "ℹ تم التسليم — المبلغ لم يصل بعد"] };
    // B2 (owner 22/11): the courier carries CASH only. A delivery paid without cash is never in his hand-in list:
    // it is shown apart, on the road, closed with one press (nothing enters the drawer)
    const cashRows = ds.filter((d) => d.cash_due > 0), noCash = ds.filter((d) => !(d.cash_due > 0));
    const who = (d) => `<td><a href="#/sales/${esc(d.sale_id)}">${d.sale_no}</a></td><td>${esc(d.customer || "")} ${esc(d.address || "")} ${esc(d.phone || "")}</td><td>${esc(d.courier || "—")}</td>`;
    $("#posSum").innerHTML = `<section class="panel"><h2>التوصيلات المفتوحة: فلوس مع المندوب</h2><table id="dlvCash"><thead><tr><th>فاتورة</th><th>العميل / العنوان</th><th>مندوب التوصيل</th><th>المبلغ معه</th><th>الحالة</th><th></th></tr></thead><tbody>
      ${cashRows.map((d) => `<tr>${who(d)}<td class="num">${money(d.cash_due)}</td><td><span class="badge ${ST[d.status][0]}">${ST[d.status][1]}</span></td>
        <td>${d.status === "out" ? `<button class="btn small secondary" data-dst="delivered" data-id="${esc(d.sale_id)}">تم التسليم</button>` : ""}
          <button class="btn small" data-dst="settled" data-id="${esc(d.sale_id)}">وصل المبلغ إلى الخزينة</button></td></tr>`).join("") || `<tr><td colspan="6" class="muted">مفيش فلوس مع المندوب</td></tr>`}</tbody></table>
      ${noCash.length ? `<h2 class="mt">في الطريق، مدفوعة من غير نقدي (مفيش فلوس مع المندوب)</h2><table id="dlvNoCash"><thead><tr><th>فاتورة</th><th>العميل / العنوان</th><th>مندوب التوصيل</th><th></th></tr></thead><tbody>
      ${noCash.map((d) => `<tr>${who(d)}<td><button class="btn small" data-dst="settled" data-id="${esc(d.sale_id)}">اتسلّمت — اقفلها</button></td></tr>`).join("")}</tbody></table>` : ""}</section>`;
    document.querySelectorAll("[data-dst]").forEach((b) => b.addEventListener("click", async () => {
      try { await api(`/api/sales/${b.dataset.id}/delivery`, { method: "POST", body: { status: b.dataset.dst } }); toast("تم التسجيل"); $("#posDeliv").click(); }
      catch (er) { toast(er.message, true); }
    }));
  });
  $("#posSummary")?.addEventListener("click", async (e) => {
    e.preventDefault();
    const s = await api("/api/sales/summary");
    $("#posSum").innerHTML = `<div class="totals"><div>فواتير<b>${s.n}</b></div><div>مبيعات<b>${money(s.total)}</b></div>
      <div>نقدي<b>${money(s.cash)}</b></div><div>بطاقة<b>${money(s.card)}</b></div><div>تحويل<b>${money(s.transfer || 0)}</b></div><div>آجل<b>${money(s.credit)}</b></div>
      <div>مرتجع<b>${money(s.returns)}</b></div>${s.gross_profit_egp != null ? `<div>مجمل الربح<b>${esc(s.gross_profit_egp)}</b></div>` : ""}</div>`;
  });
}

// ------------------------------------------------------------------ shift: open, count the drawer, close, report
const DENOMS = [20000, 10000, 5000, 2000, 1000, 500, 100, 50, 25];
const denomLabel = (d) => (d >= 100 ? `${d / 100} جنيه` : d === 50 ? "نصف جنيه" : "ربع جنيه");
// Part 3 item 4: the day's checks at shift close (backup, stock, open drafts, transfers, expenses, returns,
// alerts) and the PC check (disk, database, clock, Windows). Read only; worst first.
const HEALTH_ICON = { ok: "✓", warn: "⚠", bad: "✕" }, HEALTH_BADGE = { ok: "b-ok", warn: "b-warn", bad: "b-err" };
async function dayHealth(el) {
  const r = await api("/api/health/day");
  const rows = (list) => list.map((c) => `<tr><td><span class="badge ${HEALTH_BADGE[c.status]}">${HEALTH_ICON[c.status]}</span></td><td><b>${esc(c.title)}</b></td><td>${esc(c.text)}</td></tr>`).join("");
  const bad = [...r.day, ...r.pc].filter((c) => c.status !== "ok").length;
  el.innerHTML = `<div class="row between"><h2>صحة اليوم والجهاز</h2><span class="badge ${bad ? "b-warn" : "b-ok"}">${bad ? `⚠ ${bad} محتاجين نظرة` : "✓ كله تمام"}</span></div>
    <table><tbody>${rows(r.day)}</tbody></table><h3 class="mt">الجهاز</h3><table><tbody>${rows(r.pc)}</tbody></table>`;
}
// U2-29: where a drawer difference usually comes from (the shift history, open or not)
function wireHunt() {
  $("#shHist")?.addEventListener("click", async (e) => {
    const sm = e.target.closest(".shSum");        // U3-8: the owner's summary, ready to copy (nothing is sent)
    if (sm) {
      try {
        const r = await api(`/api/cash/shifts/${sm.dataset.id}/owner-summary`);
        $("#shHuntOut").innerHTML = `<section class="panel"><h3>ملخص للمالك</h3><textarea id="shSumT" rows="10" readonly>${esc(r.text)}</textarea>
          <div class="row"><button class="btn" id="shSumCopy">انسخ</button><span class="muted">البرنامج مبيبعتش حاجة: انسخه وابعته بنفسك.</span></div></section>`;
        $("#shSumCopy").addEventListener("click", async () => {
          try { await navigator.clipboard.writeText(r.text); toast("اتنسخ"); } catch { $("#shSumT").select(); document.execCommand("copy"); toast("اتنسخ"); }
        });
      } catch (er) { toast(er.message, true); }
      return;
    }
    const b = e.target.closest(".shHunt"); if (!b) return;
    try {
      const h = await api(`/api/cash/shifts/${b.dataset.id}/hunt`), row = (x) => `<li>فاتورة ${x.sale_no}: الإجمالي ${money(x.total)}، نقدي ${money(x.paid_cash)}</li>`;
      $("#shHuntOut").innerHTML = `<section class="panel"><h3>دوّر على الفرق (${money(h.difference || 0)} ج)</h3><ul>${h.hints.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        ${h.fractions.length ? `<h4>فواتير فيها قروش (${money(h.fractions_piasters)} ج قروش)</h4><ul>${h.fractions.slice(0, 50).map(row).join("")}</ul>` : ""}
        ${h.noncash_drawer_open.length ? `<h4>دفع غير نقدي والدرج اتفتح</h4><ul>${h.noncash_drawer_open.map(row).join("")}</ul>` : ""}
        ${h.mismatch.length ? `<h4>النقدي مش زي اللي دخل الدرج</h4><ul>${h.mismatch.map(row).join("")}</ul>` : ""}
        ${h.edited_after_payment.length ? `<h4>اتعدلت بعد الدفع</h4><ul>${h.edited_after_payment.map(row).join("")}</ul>` : ""}
        ${h.manual_openings.length ? `<h4>فتح الدرج من غير بيع</h4><ul>${h.manual_openings.map((m) => `<li>${esc(stamp(m.at))} ${esc(m.by_name || "")} — ${esc(m.reason || "")}</li>`).join("")}</ul>` : ""}
        ${h.movements.length ? `<h4>مرتجعات وإلغاء وصرف وإيداع</h4><ul>${h.movements.map((m) => `<li>${esc(stamp(m.created_at))} ${esc(m.by_name || "")}: ${money(m.amount)} ${esc(m.note || "")}</li>`).join("")}</ul>` : ""}</section>`;
    } catch (err) { toast(err.message, true); }
  });
}

async function viewShift(opts = {}) {
  let sh = null;
  try { sh = await api("/api/cash/current"); } catch { /* none open */ }
  const seeAll = ME.permissions.includes("cash.see_expected"), keeps = ME.permissions.includes("cash.manage");
  const recent = seeAll ? (await api("/api/cash/shifts")).shifts : [];
  const accs = await moneyAccounts(true), lastFloat = opts.float ?? (accs.find((a) => a.id === "drawer") || {}).balance;
  const hist = recent.length ? `<section class="panel" id="shHist"><div class="row between"><h2>آخر الشيفتات</h2>${reportTools("shifts")}</div><table><thead><tr><th>من</th><th>إلى</th><th>أغلقها</th><th>المتوقع</th><th>المعدود</th><th>النتيجة</th><th></th></tr></thead><tbody>
    ${recent.map((r) => `<tr><td>${esc(stamp(r.opened_at))}</td><td>${r.closed_at ? esc(stamp(r.closed_at)) : `<span class="badge b-info">مفتوح</span>`}</td>
      <td>${esc(r.closed_by_name || "")}</td><td class="num">${r.status === "closed" ? money(r.expected_cash) : ""}</td><td class="num">${r.status === "closed" ? money(r.counted_cash) : ""}</td>
      <td>${r.status !== "closed" ? "" : !r.difference ? `<span class="badge b-ok">✓ مطابق</span>` : r.difference < 0 ? `<span class="badge b-err">✕ عجز ${money(-r.difference)}</span>` : `<span class="badge b-warn">⚠ زيادة ${money(r.difference)}</span>`}</td>
      <td>${r.status === "closed" ? `<a href="/shift-report/${esc(r.id)}" target="_blank">التقرير</a>${r.difference ? ` <button type="button" class="btn small secondary shHunt" data-id="${esc(r.id)}">دوّر على الفرق</button>` : ""} <button type="button" class="btn small secondary shSum" data-id="${esc(r.id)}">ملخص للمالك</button>` : ""}</td></tr>`).join("")}</tbody></table><div id="shHuntOut"></div></section>` : "";
  if (!sh) {
    app.innerHTML = `<section class="panel narrowpanel"><h1>الشيفت</h1>${seeAll ? `<p class="noprint"><a href="#/money">الخزنة والحسابات ←</a></p>` : ""}<p class="badge b-warn">⚠ لا يوجد شيفت مفتوح — البيع النقدي متوقف</p>
      <form id="shOpen" class="row mt"><label class="f">النقدية في الخزينة الآن (ج) <input name="v" type="number" step="0.25" min="0" value="${lastFloat != null ? money(lastFloat) : "0"}" class="big"></label>
      <button class="btn">افتح الشيفت</button></form></section>${hist}`;
    wireHist();
    wireHunt();
    $("#shOpen").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api("/api/cash/open", { method: "POST", body: { opening_cash: toPiasters(e.target.v.value) || 0 } }); toast("✓ تم فتح الشيفت"); viewShift(); }
      catch (err) { toast(err.message, true); }
    });
    return;
  }
  const k = sh.cash_by_kind || {};
  const moneyLink = seeAll ? `<p class="noprint"><a href="#/money">الخزنة والحسابات ←</a></p>` : "";
  app.innerHTML = `<section class="panel"><div class="row between"><h1>الشيفت المفتوح</h1><span class="muted">من ${esc(stamp(sh.opened_at))}</span></div>${moneyLink}
    ${sh.blind ? `<p class="badge b-info">ℹ عُدّ النقدية في الخزينة فئةً فئة واكتب العدد. تظهر النتيجة بعد الإغلاق.</p>`
    : `<div class="totals"><div>البداية<b>${money(sh.opening_cash)}</b></div><div>مبيعات نقدي<b>${money(k.sale || 0)}</b></div>
      <div>مرتجع وإلغاء<b>${money((k.refund || 0) + (k.void || 0))}</b></div><div>تحصيل آجل<b>${money(k.customer_payment || 0)}</b></div>
      <div>مصروفات<b>${money(k.payout || 0)}</b></div>${sh.rounding_total ? `<div>منها تقريب النقدي<b>${money(sh.rounding_total)}</b></div>` : ""}<div>المتوقع في الخزينة<b>${money(sh.expected_cash)}</b></div>
      ${(sh.by_channel || []).map((c) => `<div>${c.method === "card" ? "بطاقة" : "تحويل"} — ${esc(c.channel)}
        <b>${money(c.amount - (c.refunded || 0))}</b>${c.refunded ? `<span>مبيعات ${money(c.amount)} · مرتجع ${money(c.refunded)}</span>` : ""}</div>`).join("")}
      ${sh.with_courier ? `<div>مع مندوب التوصيل<b>${money(sh.with_courier)}</b><span class="badge b-warn">⚠ لم تُسلَّم بعد</span></div>` : ""}</div>`}
    ${ME.permissions.includes("cash.manage") ? `<form id="shPay" class="row mt"><label class="f">مصروف من الخزينة (ج) <input name="a" type="number" step="0.25" min="0"></label>
      <label class="f grow">السبب <input name="n"></label><button class="btn secondary small">سجّل</button></form>` : ""}
  </section>
  ${ME.permissions.includes("cash.close") ? `<section class="panel" id="shHealth"><h2>صحة اليوم والجهاز</h2><p class="muted">بيتحسب…</p></section>` : ""}
  ${ME.permissions.includes("cash.close") ? `<form class="panel" id="shClose"><h2>إغلاق الشيفت — عدّ الخزينة</h2>
    <table class="denoms"><thead><tr><th>الفئة</th><th>العدد</th><th>المبلغ</th></tr></thead><tbody>
    ${DENOMS.map((d) => `<tr><td>${denomLabel(d)}</td><td><input type="number" min="0" step="1" inputmode="numeric" data-d="${d}" class="narrow"></td><td class="num" id="dt${d}">0.00</td></tr>`).join("")}</tbody>
    <tfoot><tr><th>الإجمالي المعدود</th><th></th><th class="num big" id="dTotal">0.00</th></tr></tfoot></table>
    <label class="f mt">ملاحظة (اختياري) <input name="note"></label>
    <fieldset class="mt"><legend>الفلوس بعد التقفيل</legend>
      <label class="chk"><input type="checkbox" name="ho" ${keeps ? "" : "checked"}> ${keeps ? "هسلّم الفلوس لحد تاني (هيكتب الرقم السري بتاعه)" : "سلّم النقدية لمدير أو للمالك (هيكتب الرقم السري بتاعه)"}</label>
      <div class="row" id="shMove" ${keeps ? "" : ""}><label class="f">الفكة اللي هتفضل في الدرج للشيفت الجاي (ج) <input name="fl" type="number" step="0.25" min="0" value="${money(sh.opening_cash)}"></label>
      ${accs.length ? `<label class="f">الباقي يتحول إلى <select name="to">${srcOptions(accs, "treasury", { drawer: false, outside: false })}</select></label>` : ""}</div>
      <p class="muted small">${keeps ? "لو مش هتسلّم لحد، الفلوس بتتحول باسمك." : "لو محدش استلم، الفلوس كلها بتفضل في الدرج للشيفت الجاي."}</p>
      <div id="shSplit" class="muted small"></div></fieldset>
    <button class="btn big">إغلاق الشيفت وطباعة التقرير</button></form>` : ""}
  ${keeps ? `<details class="panel" id="shTarget"><summary><b>الفكة اللي تفضل في الدرج (لكل فئة)</b></summary>
    <p class="muted">عند التقفيل البرنامج بيقولك تسيب إيه من كل فئة وتحوّل الباقي للخزنة.</p>
    <form class="row" id="shTargetF">${[["10000", "فئة 100"], ["5000", "فئة 50"], ["2000", "فئة 20"], ["1000", "فئة 10"], ["500", "فئة 5"], ["coins", "عملات (جنيه وأقل)"]]
      .map(([k, t]) => `<label class="f">${t} (ج) <input type="number" min="0" step="0.25" data-t="${k}" class="narrow"></label>`).join("")}
      <span id="shTargetSum" class="muted"></span><button class="btn secondary">احفظ</button></form></details>` : ""}
  ${hist}`;
  const recalc = () => {
    let t = 0;
    document.querySelectorAll("[data-d]").forEach((inp) => { const v = (+inp.value || 0) * +inp.dataset.d; t += v; $(`#dt${inp.dataset.d}`).textContent = money(v); });
    $("#dTotal").textContent = money(t);
    splitSoon();
  };
  // U2-30: which notes stay for the next shift and which go to the treasury, by the owner's target
  let splitT = null;
  const splitSoon = () => { clearTimeout(splitT); splitT = setTimeout(showSplit, 250); };
  async function showSplit() {
    const out = $("#shSplit"); if (!out) return;
    const den = {};
    document.querySelectorAll("[data-d]").forEach((inp) => { if (+inp.value) den[inp.dataset.d] = +inp.value; });
    if (!Object.keys(den).length) { out.innerHTML = ""; return; }
    const fc = $("#shClose");
    if (!keeps && fc?.ho && !fc.ho.checked) {       // nobody receives it: the close keeps everything in the drawer
      out.innerHTML = "<b>كل الفلوس هتفضل في الدرج للشيفت الجاي</b> (محدش استلم)"; return;
    }
    try {
      const f0 = $("#shClose"), own = f0?.fl?.dataset.touched ? toPiasters(f0.fl.value) : null;
      const sp = await api("/api/cash/float-preview", { method: "POST", body: { denominations: den, ...(own != null ? { float_left: own } : {}) } });
      const lbl = (d) => `${+d / 100 >= 1 ? +d / 100 : `${+d} قرش`}`;
      const list = (o) => Object.entries(o).sort((a, b) => b[0] - a[0]).map(([d, n]) => `${lbl(d)} × ${n}`).join("، ") || "—";
      out.innerHTML = sp.exact === false ? `<b>سيب في الدرج:</b> ${money(sp.float_left)} ج — مفيش فئات بتعمل المبلغ ده بالظبط، عدّها بإيدك<br><b>حوّل للخزنة:</b> ${money(sp.to_treasury)} ج`
        : `<b>سيب في الدرج:</b> ${list(sp.keep)} = ${money(sp.float_left)} ج<br><b>حوّل للخزنة:</b> ${list(sp.move)} = ${money(sp.to_treasury)} ج${sp.short ? `<br>⚠ الفكة ناقصة ${money(sp.short)} ج عن المطلوب` : ""}`;
      const f = $("#shClose"); if (f && f.fl && !f.fl.dataset.touched) f.fl.value = money(sp.float_left);
    } catch { out.innerHTML = ""; }
  }
  $("#shClose")?.fl?.addEventListener("input", (e) => { e.target.dataset.touched = "1"; splitSoon(); });
  $("#shClose")?.ho?.addEventListener("change", splitSoon);
  if ($("#shTargetF")) {
    const tf = $("#shTargetF"), sum = () => { let t = 0; tf.querySelectorAll("[data-t]").forEach((i) => { t += toPiasters(i.value) || 0; }); $("#shTargetSum").textContent = `الإجمالي ${money(t)} ج`; };
    api("/api/cash/float-target").then((t) => { tf.querySelectorAll("[data-t]").forEach((i) => { i.value = money(t[i.dataset.t] || 0); }); sum(); }).catch(() => {});
    tf.addEventListener("input", sum);
    tf.addEventListener("submit", async (e) => {
      e.preventDefault();
      const target = {}; tf.querySelectorAll("[data-t]").forEach((i) => { target[i.dataset.t] = toPiasters(i.value) || 0; });
      try { await api("/api/cash/float-target", { method: "POST", body: { target } }); toast("✓ اتحفظت"); splitSoon(); }
      catch (err) { toast(err.message, true); }
    });
  }
  wireHunt();
  document.querySelectorAll("[data-d]").forEach((inp) => inp.addEventListener("input", recalc));
  wireHist();
  if ($("#shHealth")) dayHealth($("#shHealth")).catch(() => { $("#shHealth").remove(); });
  function wireHist() { if (recent.length) wireTools("shifts", () => ({ title: "آخر الشيفتات", subtitle: "", el: $("#shHist"), sheets: [
    { name: "الشيفتات", columns: [Tx("من"), Tx("إلى"), Tx("أغلقها"), M("المتوقع"), M("المعدود"), M("الفرق"), Tx("ملاحظة")],
      rows: recent.map((r) => [stamp(r.opened_at), r.closed_at ? stamp(r.closed_at) : "مفتوح", r.closed_by_name || "",
        r.expected_cash, r.counted_cash, r.difference, r.close_note || ""]) }] })); }
  $("#shPay")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/cash/movements", { method: "POST", body: { kind: "payout", amount: toPiasters(e.target.a.value), note: e.target.n.value } }); toast("تم التسجيل"); viewShift(); }
    catch (err) { toast(err.message, true); }
  });
  $("#shClose")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const den = {};
    document.querySelectorAll("[data-d]").forEach((inp) => { if (+inp.value) den[inp.dataset.d] = +inp.value; });
    if (!Object.keys(den).length && !confirm("لم تكتب أي فئة — أي أن الخزينة فارغة. هل تتابع؟")) return;
    try {
      const f = e.target, body = { shift_id: sh.id, denominations: den, note: f.note.value || null };
      if (keeps || f.ho.checked) Object.assign(body, { float_left: toPiasters(f.fl.value) ?? null, to_account: f.to?.value || null, handover: f.ho.checked });
      const r = await api("/api/cash/close", { method: "POST", body });
      const d = r.difference, moved = r.float_left != null ? r.counted_cash - r.float_left : null;
      app.innerHTML = `<section class="panel narrowpanel"><h1>تم إغلاق الشيفت</h1>
        <p class="result ${!d ? "b-ok" : d < 0 ? "b-err" : "b-warn"}">${!d ? "✓ الخزينة مطابقة" : d < 0 ? `✕ عجز ${money(-d)} ج` : `⚠ زيادة ${money(d)} ج`}</p>
        <div class="totals"><div>المتوقع<b>${money(r.expected_cash)}</b></div><div>المعدود<b>${money(r.counted_cash)}</b></div>
          ${moved != null ? `<div>اتحوّل من الدرج<b>${money(moved)}</b></div><div>الفكة للشيفت الجاي<b>${money(r.float_left)}</b></div>` : ""}</div>
        <div class="row mt"><a class="btn" href="/shift-report/${esc(r.id)}" target="_blank">اطبع التقرير</a><button type="button" class="btn secondary" id="shNew">افتح شيفت جديدًا</button></div></section>`;
      // U2-26: the link pointed at this same screen and did nothing; open the new-shift form with the kept float
      $("#shNew").addEventListener("click", () => viewShift({ float: r.float_left ?? undefined }));
      printInFrame(`/shift-report/${r.id}`);
    } catch (err) {
      toast(err.code === "difference_note_required" ? `${err.message} (الفرق ${money(err.details.difference)})` : err.message, true);
      if (err.code === "difference_note_required") e.target.note.focus();
    }
  });
}

// ------------------------------------------------------------------ money accounts (F105): where the money is
let MONEY = null;                                   // {accounts, kinds, movement_kinds}, read once per screen
async function moneyAccounts(force = false) {
  if (!ME.permissions.includes("cash.see_expected")) return [];
  if (!MONEY || force) MONEY = await api("/api/money/accounts");
  return MONEY.accounts;
}
const isOwnerMe = () => ME.role === "owner";
// a payout source: the drawer, a money account (with its balance), or outside the program's accounts
function srcOptions(accs, sel, { drawer = true, outside = true, ownerOutside = false } = {}) {
  const o = [];
  if (sel === "treasury") sel = (accs.find((a) => a.kind === "treasury") || {}).id || (drawer ? "drawer" : sel);
  if (drawer) o.push(`<option value="drawer" ${sel === "drawer" ? "selected" : ""}>من خزينة الشيفت</option>`);
  for (const a of accs) {
    if (a.id === "drawer" || !a.is_active) continue;
    o.push(`<option value="${esc(a.id)}" ${sel === a.id ? "selected" : ""}>${esc(a.name)} — ${money(a.balance)} ج</option>`);
  }
  if (outside && (!ownerOutside || isOwnerMe())) {
    o.push(`<option value="outside" ${sel === "outside" ? "selected" : ""}>${isOwnerMe() ? "من خارج حسابات البرنامج (من جيبي)" : "من جيبي (ينتظر موافقة المالك)"}</option>`);
  }
  return o.join("");
}
const srcName = (e) => e.account || (e.paid_from === "drawer" ? "درج الشيفت" : "من خارج الحسابات");
const MV_SIGN = (a) => (a < 0 ? `<span class="err">− ${money(-a)}</span>` : `<span class="ok">+ ${money(a)}</span>`);

async function viewMoney() {
  const accs = await moneyAccounts(true), owner = isOwnerMe(), canM = ME.permissions.includes("cash.manage");
  const total = accs.reduce((x, a) => x + (a.balance || 0), 0);
  app.innerHTML = `<section class="panel"><div class="row between"><h1>الخزنة والحسابات</h1><a href="#/reconcile">مطابقة أرصدة الموردين والعملاء ←</a>${ME.permissions.includes("balances.correct") ? ` · <a href="#/opening-fix">تصحيح الأرصدة بعد النقل ←</a>` : ""}</div>
    <div class="totals"><div>كل الفلوس في البرنامج<b>${money(total)} ج</b><span>عدد الأماكن: ${accs.length}</span></div></div>
    <table><thead><tr><th>المكان</th><th>النوع</th><th>الرصيد</th><th></th></tr></thead><tbody>
    ${accs.map((a) => `<tr><td><b>${esc(a.name)}</b>${a.is_active ? "" : ` <span class="badge b-plain">موقوف</span>`}${a.allow_negative ? ` <span class="chip">مسموح بالسالب</span>` : ""}</td>
      <td>${a.id === "drawer" ? (a.shift_open ? "الشيفت مفتوح" : "الفكة من آخر شيفت") : esc(a.kind_label || "")}</td>
      <td class="num"><b>${money(a.balance)}</b></td>
      <td class="row">${a.id === "drawer" ? `<a href="#/shift">الشيفت</a>` : `<a class="btn small secondary" href="#/money/${esc(a.id)}">كشف الحساب</a>
        ${canM && a.is_active ? `<button class="btn small secondary" data-count="${esc(a.id)}" data-name="${esc(a.name)}" data-bal="${a.balance}">جرد الحساب</button>` : ""}`}</td></tr>`).join("")}</tbody></table>
    <div id="mCount"></div></section>
  ${canM ? `<div class="grid2"><form class="panel" id="mTr"><h2>تحويل بين الحسابات</h2>
      <label class="f">من <select name="from">${srcOptions(accs, "drawer", { outside: false })}</select></label>
      <label class="f">إلى <select name="to">${srcOptions(accs, "treasury", { outside: false })}</select></label>
      <label class="f">المبلغ (ج) <input name="a" type="number" step="0.01" min="0" required></label>
      <label class="f">ملاحظة <input name="n" placeholder="مثال: توريد من الدرج للخزنة"></label><button class="btn mt">حوّل</button></form>
    <form class="panel" id="mOwn"><h2>مسحوبات وإيداع المالك</h2>
      <div class="seg2 small"><button type="button" class="on" data-ok="owner_withdrawal">✓ مسحوبات</button><button type="button" data-ok="owner_deposit">إيداع</button></div>
      <label class="f mt">الحساب <select name="acc">${srcOptions(accs, "treasury", { outside: false })}</select></label>
      <label class="f">المبلغ (ج) <input name="a" type="number" step="0.01" min="0" required></label>
      <label class="f">ملاحظة <input name="n"></label><button class="btn mt">سجّل</button>
      <p class="muted">المسحوبات والإيداع ليست مصروفات: لا تدخل في حساب الربح.</p></form></div>` : ""}
  ${owner ? `<form class="panel row" id="mNew"><h2 class="grow">حساب جديد</h2>
      <label class="f">الاسم <input name="name" required placeholder="مثال: اتصالات كاش"></label>
      <label class="f">النوع <select name="kind">${Object.entries(MONEY.kinds).filter(([k]) => k !== "treasury").map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join("")}</select></label>
      <button class="btn small">أضف</button></form>` : ""}
  <section class="panel" id="mSum"><div class="row between"><h2>الفلوس راحت فين</h2>${reportTools("msum")}</div>
    <div class="row noprint">${rangeBar("mRange", "month")}<div class="seg2 small" id="mBy"><button type="button" class="on" data-by="day">✓ يومي</button><button type="button" data-by="month">شهري</button></div></div>
    <div id="mSumBox"></div></section>`;
  let ok = "owner_withdrawal", by = "day", sum = null, range = null;
  document.querySelectorAll("[data-ok]").forEach((b) => b.addEventListener("click", () => {
    ok = b.dataset.ok;
    document.querySelectorAll("[data-ok]").forEach((x) => { x.classList.toggle("on", x === b); x.textContent = (x === b ? "✓ " : "") + (x.dataset.ok === "owner_withdrawal" ? "مسحوبات" : "إيداع"); });
  }));
  document.querySelectorAll("[data-count]").forEach((b) => b.addEventListener("click", () => {
    $("#mCount").innerHTML = `<form class="panel mt" id="mCountF"><h2>جرد: ${esc(b.dataset.name)}</h2>
      <p class="muted">اكتب المبلغ الحقيقي (المعدود في الخزنة، أو رصيد البنك أو المحفظة). — رصيد البرنامج: ${money(+b.dataset.bal)} ج</p>
      <div class="row"><label class="f">المبلغ الحقيقي (ج) <input name="c" type="number" step="0.01" required></label>
      <label class="f grow">سبب الفرق (لازم لو فيه فرق) <input name="r"></label><button class="btn">سجّل الجرد</button></div></form>`;
    $("#mCountF").addEventListener("submit", async (e) => {
      e.preventDefault();
      await withBusy(e.target.querySelector("button"), async () => {
        try {
          const r = await api(`/api/money/accounts/${b.dataset.count}/count`, { method: "POST", body: { counted: toPiasters(e.target.c.value), reason: e.target.r.value || null } });
          toast(!r.difference ? "✓ الحساب مطابق" : r.against_old_program ? `✓ فرق عن البرنامج القديم: ${money(r.difference)} ج` : `✓ تم تسجيل الفرق: ${money(r.difference)} ج`);
          viewMoney();
        } catch (err) { toast(err.message, true); if (err.code === "reason_required") e.target.r.focus(); }
      });
    });
  }));
  $("#mTr")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    await withBusy(f.querySelector("button"), async () => {
      try { await api("/api/money/transfer", { method: "POST", body: { from: f.from.value, to: f.to.value, amount: toPiasters(f.a.value), note: f.n.value || null } }); toast("✓ تم التحويل"); viewMoney(); }
      catch (err) { toast(err.message, true); }
    });
  });
  $("#mOwn")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    await withBusy(f.querySelector("button:not([type=button])"), async () => {
      try { await api("/api/money/owner", { method: "POST", body: { account: f.acc.value, kind: ok, amount: toPiasters(f.a.value), note: f.n.value || null } }); toast("✓ تم التسجيل"); viewMoney(); }
      catch (err) { toast(err.message, true); }
    });
  });
  $("#mNew")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/money/accounts", { method: "POST", body: { name: e.target.name.value, kind: e.target.kind.value } }); toast("✓ تمت الإضافة"); viewMoney(); }
    catch (err) { toast(err.message, true); }
  });
  const load = async () => {
    sum = await api(`/api/money/summary?from=${range[0]}&to=${range[1]}&by=${by}`);
    $("#mSumBox").innerHTML = `<p><b>${esc(rangeText(sum.from, sum.to))}</b> — الدخل: ${money(sum.total_in)} ج · الخرج: ${money(sum.total_out)} ج</p>
      ${sum.periods.map((p) => `<div class="panel sub"><div class="row between"><h3>${esc(p.period)}</h3><span>الدخل: <b class="ok">${money(p.in)}</b> · الخرج: <b>${money(p.out)}</b> · الصافي: <b>${money(p.net)}</b></span></div>
        <table><thead><tr><th>البيان</th><th>دخل</th><th>خرج</th></tr></thead><tbody>
        ${p.by_kind.map((k) => `<tr class="${k.flow ? "" : "muted"}"><td>${esc(k.label)}${k.flow ? "" : " <small>(بين أماكن الصيدلية أو رصيد قديم، مش دخل ولا مصروف)</small>"}</td><td class="num">${k.in ? money(k.in) : ""}</td><td class="num">${k.out ? money(k.out) : ""}</td></tr>`).join("")}</tbody></table>
        <p class="muted small">${p.by_place.map((x) => `${esc(x.place)}: +${money(x.in)} / −${money(x.out)}`).join(" · ")}</p></div>`).join("") || `<p class="muted">لا توجد حركة في هذه الفترة</p>`}`;
  };
  const get = wireRange("mRange", (f, t) => { range = [f, t]; load().catch((e) => toast(e.message, true)); });
  range = get();
  document.querySelectorAll("[data-by]").forEach((b) => b.addEventListener("click", () => {
    by = b.dataset.by;
    document.querySelectorAll("[data-by]").forEach((x) => { x.classList.toggle("on", x === b); x.textContent = (x === b ? "✓ " : "") + (x.dataset.by === "day" ? "يومي" : "شهري"); });
    load().catch((e) => toast(e.message, true));
  }));
  wireTools("msum", () => ({ title: "الفلوس راحت فين", subtitle: rangeText(sum.from, sum.to), el: $("#mSum"), sheets: [
    { name: "الملخص", columns: [Tx("الفترة"), Tx("البيان"), M("دخل"), M("خرج")],
      rows: sum.periods.flatMap((p) => p.by_kind.map((k) => [p.period, k.label, k.in, k.out])) }] }));
}

async function viewMoneyAccount(id) {
  const owner = isOwnerMe();
  app.innerHTML = `<section class="panel"><a href="#/money">← الخزنة والحسابات</a><div id="maHead"></div></section>
    <section class="panel" id="maRep"><div class="row between"><h2>كشف الحساب</h2>${reportTools("mast")}</div>${rangeBar("maRange", "month")}<div id="maBox"></div></section>`;
  let st = null;
  const head = (a) => {
    $("#maHead").innerHTML = `<div class="row between"><h1>${esc(a.name)}</h1><span>${a.is_active ? "" : `<span class="badge b-plain">موقوف</span>`}</span></div>
      ${owner && a.id !== "treasury" ? `<form class="row" id="maEdit"><label class="f">الاسم <input name="name" value="${esc(a.name)}"></label>
        <label class="chk"><input type="checkbox" name="neg" ${a.allow_negative ? "checked" : ""}> مسموح بالسالب</label>
        ${a.kind !== "treasury" ? `<label class="chk"><input type="checkbox" name="act" ${a.is_active ? "checked" : ""}> مفعّل</label>` : ""}
        <button class="btn small secondary">احفظ</button></form>` : ""}
      ${owner ? `<form class="row mt" id="maAdj"><label class="f">الرصيد الصحيح (ج) <input name="v" type="number" step="0.01" required></label>
        <label class="f grow">السبب <input name="r" required placeholder="مثال: رقم البرنامج القديم كان غلط"></label>
        <button class="btn small secondary">تسوية الرصيد بدون جرد</button></form>` : ""}`;
    $("#maEdit")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target, body = { name: f.name.value, allow_negative: f.neg.checked };
      if (f.act) body.is_active = f.act.checked;
      try { await api(`/api/money/accounts/${id}`, { method: "PATCH", body }); toast("تم الحفظ"); viewMoneyAccount(id); } catch (err) { toast(err.message, true); }
    });
    $("#maAdj")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!confirmInline(e.target.querySelector("button"))) return;
      try { const r = await api(`/api/money/accounts/${id}/adjust`, { method: "POST", body: { correct_balance: toPiasters(e.target.v.value), reason: e.target.r.value } });
        toast(`✓ الفرق: ${money(r.difference)} ج`); viewMoneyAccount(id); } catch (err) { toast(err.message, true); }
    });
  };
  wireRange("maRange", async (f, t) => {
    try {
      st = await api(`/api/money/accounts/${id}/statement?from=${f}&to=${t}`);
      head(st.account);
      $("#maBox").innerHTML = `<div class="totals"><div>الرصيد أول الفترة<b>${money(st.opening)}</b></div><div class="t-ok">دخل<b>${money(st.total_in)}</b></div>
        <div>خرج<b>${money(st.total_out)}</b></div><div>الرصيد آخر الفترة<b>${money(st.closing)}</b></div></div>
        <div class="tablewrap mt"><table><thead><tr><th>التاريخ</th><th>البيان</th><th>المبلغ</th><th>الرصيد</th><th>بواسطة</th></tr></thead><tbody>
        ${st.lines.map((l) => `<tr><td>${esc(stamp(l.created_at))}</td><td>${esc(l.kind_label)}${l.note ? ` <small class="muted">${esc(l.note)}</small>` : ""}</td>
          <td class="num">${MV_SIGN(l.amount)}</td><td class="num">${money(l.balance)}</td><td>${esc(l.by_name || "")}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">لا توجد حركة في هذه الفترة</td></tr>`}</tbody></table></div>`;
    } catch (err) { toast(err.message, true); }
  });
  wireTools("mast", () => ({ title: `كشف حساب ${st.account.name}`, subtitle: rangeText(st.from, st.to), el: $("#maRep"), sheets: [
    { name: "كشف الحساب", columns: [Tx("التاريخ"), Tx("البيان"), Tx("ملاحظة"), M("المبلغ"), M("الرصيد")],
      rows: [["", "الرصيد أول الفترة", "", null, st.opening], ...st.lines.map((l) => [stamp(l.created_at), l.kind_label, l.note || "", l.amount, l.balance])] }] }));
}

// ------------------------------------------------------------------ balance reconciliation (F108)
const PARTY_T = { supplier: "مورد", customer: "عميل" };
async function viewReconcile() {
  app.innerHTML = `<section class="panel"><h1>مطابقة الأرصدة</h1>
    <p class="muted">اكتب الرصيد اللي في كشف حساب المورد أو العميل. البرنامج يبيّن الفرق، وتسجّله بنوعه. الصفحة دي برضه بتتكتب فيها أرصدة أول المدة وقت التشغيل.</p>
    <form class="row" id="rcF"><div class="seg2 small" id="rcP"><button type="button" class="on" data-p="">✓ الكل</button><button type="button" data-p="supplier">الموردين</button><button type="button" data-p="customer">العملاء</button></div>
      <input name="q" class="grow" placeholder="ابحث بالاسم"><button class="btn small secondary">ابحث</button></form>
    <div id="rcBox" class="mt"></div></section>`;
  let party = "", types = {};
  const load = async () => {
    const r = await api(`/api/reconcile/parties?party=${party}&q=${encodeURIComponent($("#rcF").q.value)}`);
    types = r.types;
    $("#rcBox").innerHTML = `<div class="tablewrap"><table><thead><tr><th>الاسم</th><th>رصيد البرنامج</th><th>رصيد الكشف (ج)</th><th>الفرق</th><th>نوع الفرق</th><th>ملاحظة</th><th></th><th>آخر مطابقة</th></tr></thead><tbody>
      ${r.parties.map((p) => `<tr data-party="${p.party}" data-id="${esc(p.id)}" data-bal="${p.balance}"><td><b>${esc(p.name)}</b> <span class="chip">${PARTY_T[p.party]}</span></td>
        <td class="num">${money(p.balance)}</td><td><input class="narrow" type="number" step="0.01" data-st></td><td class="num" data-diff></td>
        <td><select data-type><option value="">—</option>${Object.entries(types).filter(([k]) => !(k === "early_discount" && p.party !== "supplier") && !(k === "claim_discount" && p.party !== "customer")).map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join("")}</select></td>
        <td><input data-note></td><td><button class="btn small" data-save>احفظ</button></td>
        <td class="muted small">${p.last ? `${esc(dayOf(p.last.at))}${p.last.difference ? ` · الفرق: ${money(p.last.difference)}` : " · مطابق"}` : ""}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">لا يوجد</td></tr>`}</tbody></table></div>`;
    $("#rcBox").querySelectorAll("[data-st]").forEach((inp) => inp.addEventListener("input", () => {
      const tr = inp.closest("tr"), v = toPiasters(inp.value);
      tr.querySelector("[data-diff]").textContent = v == null ? "" : money(v - +tr.dataset.bal);
    }));
    $("#rcBox").querySelectorAll("[data-save]").forEach((b) => b.addEventListener("click", async () => {
      const tr = b.closest("tr"), st = toPiasters(tr.querySelector("[data-st]").value);
      if (st == null) { tr.querySelector("[data-st]").focus(); toast("اكتب رصيد الكشف", true); return; }
      await withBusy(b, async () => {
        try {
          const r2 = await api("/api/reconcile", { method: "POST", body: { party: tr.dataset.party, party_id: tr.dataset.id, statement_balance: st,
            program_balance: +tr.dataset.bal, adj_type: tr.querySelector("[data-type]").value || null, note: tr.querySelector("[data-note]").value || null } });
          toast(r2.difference ? `✓ اتسجل الفرق: ${money(r2.difference)} ج` : "✓ مطابق");
          tr.dataset.bal = r2.balance; tr.children[1].textContent = money(r2.balance); tr.querySelector("[data-diff]").textContent = "";
        } catch (err) {
          toast(err.message, true);
          if (err.code === "type_required") tr.querySelector("[data-type]").focus();
          if (err.code === "reason_required") tr.querySelector("[data-note]").focus();
          if (err.code === "balance_changed") load();
        }
      });
    }));
  };
  document.querySelectorAll("#rcP [data-p]").forEach((b) => b.addEventListener("click", () => {
    party = b.dataset.p;
    document.querySelectorAll("#rcP [data-p]").forEach((x) => { x.classList.toggle("on", x === b); x.textContent = (x === b ? "✓ " : "") + { "": "الكل", supplier: "الموردين", customer: "العملاء" }[x.dataset.p]; });
    load().catch((e) => toast(e.message, true));
  }));
  $("#rcF").addEventListener("submit", (e) => { e.preventDefault(); load().catch((err) => toast(err.message, true)); });
  await load();
}

// ------------------------------------------------------------------ balance correction after the move (part 3 item 2)
// Owner/manager (balances.correct): the opening balance of a moved supplier or customer is corrected with a written
// reason. The difference is an opening adjustment (never a payment), in the audit log and the reconciliation history.
async function viewOpeningFix() {
  app.innerHTML = `<section class="panel"><h1>تصحيح الأرصدة بعد النقل</h1>
    <p class="muted">الرصيد الافتتاحي لكل مورد أو عميل اتنقل من إي ستوك. لو الرقم غلط اكتب الصحيح وسبب التصحيح؛ الفرق بيتسجل تسوية افتتاحية (مش دفع ولا مصروف) وبيظهر في سجل العمليات وفي كشف الحساب.</p>
    <form class="row" id="ofF"><div class="seg2 small" id="ofK"><button type="button" class="on" data-k="supplier">✓ الموردين</button><button type="button" data-k="customer">العملاء</button></div>
      <input name="q" class="grow" placeholder="ابحث بالاسم أو كود إي ستوك"><button class="btn small secondary">ابحث</button></form>
    <div id="ofBox" class="mt"></div></section>`;
  let kind = "supplier";
  const load = async () => {
    const r = await api(`/api/opening-balances/${kind}?q=${encodeURIComponent($("#ofF").q.value)}`);
    $("#ofBox").innerHTML = `<div class="tablewrap"><table><thead><tr><th>الاسم</th><th>كود إي ستوك</th><th>رصيد إي ستوك</th><th>الافتتاحي الحالي</th><th>الرصيد النهارده</th><th>الصحيح (ج)</th><th>سبب التصحيح</th><th></th><th>تصحيحات</th></tr></thead><tbody>
      ${r.parties.map((p) => `<tr data-id="${esc(p.party_id)}" data-open="${p.opening}" data-rev="${p.corrections || 0}"><td><b>${esc(p.name)}</b></td><td>${esc(p.estock_code || "")}</td>
        <td class="num">${money(p.estock_opening)}</td><td class="num" data-o>${money(p.opening)}</td><td class="num" data-now>${money(p.balance_now)}</td>
        <td><input class="narrow" inputmode="decimal" data-amt></td><td><input data-why placeholder="مثلًا: كشف حساب المورد"></td>
        <td><button class="btn small" data-save>صحّح</button></td><td class="num" data-n>${p.corrections || ""}</td></tr>`).join("") || `<tr><td colspan="9" class="muted">مفيش أرصدة منقولة</td></tr>`}</tbody></table></div>
      <p class="muted small">+ للمورد = علينا له، + للعميل = عليه لنا.</p>`;
    $("#ofBox").querySelectorAll("[data-save]").forEach((b) => b.addEventListener("click", async () => {
      const tr = b.closest("tr"), amt = exactPiasters(tr.querySelector("[data-amt]").value), why = tr.querySelector("[data-why]").value.trim();
      if (amt == null) { tr.querySelector("[data-amt]").focus(); toast("اكتب الرصيد الصحيح بالجنيه (قرشين بعد العلامة بالكتير)", true); return; }
      if (why.length < 3) { tr.querySelector("[data-why]").focus(); toast("اكتب سبب التصحيح", true); return; }
      await withBusy(b, async () => {
        try {
          const r2 = await api(`/api/opening-balances/${kind}/${encodeURIComponent(tr.dataset.id)}`, { method: "POST", body: { amount: amt, seen_opening: +tr.dataset.open, seen_revision: +tr.dataset.rev, reason: why } });
          toast(`✓ اتصحح: الفرق ${money(r2.opening - +tr.dataset.open)} ج`);
          tr.dataset.open = r2.opening; tr.querySelector("[data-o]").textContent = money(r2.opening); tr.querySelector("[data-now]").textContent = money(r2.balance_now);
          tr.dataset.rev = r2.corrections; tr.querySelector("[data-n]").textContent = r2.corrections; tr.querySelector("[data-amt]").value = ""; tr.querySelector("[data-why]").value = "";
        } catch (err) {
          toast(err.message, true);
          if (err.code === "balance_changed") load();
        }
      });
    }));
  };
  document.querySelectorAll("#ofK [data-k]").forEach((b) => b.addEventListener("click", () => {
    kind = b.dataset.k;
    document.querySelectorAll("#ofK [data-k]").forEach((x) => { x.classList.toggle("on", x === b); x.textContent = (x === b ? "✓ " : "") + { supplier: "الموردين", customer: "العملاء" }[x.dataset.k]; });
    load().catch((e) => toast(e.message, true));
  }));
  $("#ofF").addEventListener("submit", (e) => { e.preventDefault(); load().catch((err) => toast(err.message, true)); });
  await load();
}

// ------------------------------------------------------------------ E-Stock history (read only) and old-invoice returns
// Owner 18/11: the old invoices are usable inside the program, not converted. Every row says it is E-Stock history.
let ESTOCK_ON = null;
async function estockOn() {
  if (ESTOCK_ON === null) { try { ESTOCK_ON = (await api("/api/estock/available")).available; } catch { ESTOCK_ON = false; } }
  return ESTOCK_ON;
}
const ES_KIND = { sale: "مبيعات", sale_return: "مرتجعات", purchase: "مشتريات" };
const ES_BADGE = `<span class="badge b-info">من إي ستوك</span>`;
const esMoney = (v) => (v == null ? "" : esc(v));          // E-Stock's amounts come as EGP text, shown as they are
function hashQuery() {
  const i = location.hash.indexOf("?");
  return new URLSearchParams(i < 0 ? "" : location.hash.slice(i + 1));
}

async function viewEstock() {
  if (!(await estockOn())) { app.innerHTML = `<section class="panel"><h1>تاريخ إي ستوك</h1><p class="muted">مفيش تاريخ إي ستوك في البرنامج ده (النقل ماتعملش).</p></section>`; return; }
  const qp = hashQuery(), can = (p) => ME.permissions.includes(p);
  const kinds = Object.keys(ES_KIND).filter((k) => can(k === "purchase" ? "purchases.read" : "sales.totals"));
  let kind = kinds.includes(qp.get("kind")) ? qp.get("kind") : kinds[0];
  if (!kind) { app.innerHTML = `<section class="panel"><h1>تاريخ إي ستوك</h1><p class="muted">مش مسموح لك تشوف الفواتير القديمة.</p></section>`; return; }
  const party = qp.get("party") || "", item = qp.get("item") || "";
  app.innerHTML = `<section class="panel"><div class="row between"><h1>تاريخ إي ستوك</h1>${ES_BADGE}</div>
    <p class="muted">فواتير البرنامج القديم كما هي، للبحث والفتح والمرتجع. مبالغها لا تدخل في حسابات البرنامج.</p>
    <div class="seg2 small" id="esK">${kinds.map((k) => `<button type="button" data-k="${k}" class="${k === kind ? "on" : ""}">${k === kind ? "✓ " : ""}${ES_KIND[k]}</button>`).join("")}</div>
    <form class="row mt" id="esF"><label class="f">رقم الفاتورة <input name="q"></label>
      <label class="f">من <input type="date" name="from"></label><label class="f">إلى <input type="date" name="to"></label>
      <label class="f" id="esTicket">رقم التذكرة <input name="ticket"></label><button class="btn small">ابحث</button></form>
    ${party || item ? `<p class="muted">${party ? "الفواتير دي لعميل أو مورد واحد" : "الفواتير دي لصنف واحد"} — <a href="#/estock?kind=${kind}">اعرض الكل</a></p>` : ""}
    <div id="esBox" class="mt"></div></section>`;
  const f = $("#esF");
  const load = async () => {
    $("#esTicket").hidden = kind !== "sale";
    const p = new URLSearchParams({ kind });
    for (const k of ["q", "from", "to"]) if (f[k].value) p.set(k, f[k].value);
    if (kind === "sale" && f.ticket.value) p.set("ticket", f.ticket.value);
    if (party) p.set("party", party);
    if (item) p.set("item", item);
    const rows = (await api(`/api/estock/invoices?${p}`)).invoices;
    $("#esBox").innerHTML = `<div class="tablewrap"><table><thead><tr><th>التاريخ</th><th>الرقم</th><th>${kind === "purchase" ? "المورد" : "العميل"}</th><th>الإجمالي</th>${kind === "purchase" ? "" : "<th>نقدي</th><th>فيزا ومحفظة</th>"}${kind === "sale" ? "<th>التذكرة</th>" : ""}<th></th></tr></thead><tbody>
      ${rows.map((r) => `<tr class="clickable" data-go="${esc(r.estock_id)}"><td>${esc(r.day)}</td><td>${esc(r.bill_number || r.estock_id)}</td><td>${esc(r.party || "—")}</td>
        <td class="num">${esMoney(r.total)}</td>${kind === "purchase" ? "" : `<td class="num">${esMoney(r.cash)}</td><td class="num">${esMoney(r.network)}</td>`}
        ${kind === "sale" ? `<td>${esc([r.ticket_name, r.ticket_no].filter(Boolean).join(" "))}</td>` : ""}<td>${ES_BADGE}</td></tr>`).join("")
        || `<tr><td colspan="8" class="muted">مفيش فواتير بالبحث ده</td></tr>`}</tbody></table></div>
      ${rows.length >= 200 ? `<p class="muted">معروض أول 200 — ضيّق البحث بالتاريخ أو الرقم.</p>` : ""}`;
    $("#esBox").querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/estock/${kind}/${tr.dataset.go}`; }));
  };
  document.querySelectorAll("#esK [data-k]").forEach((b) => b.addEventListener("click", () => {
    kind = b.dataset.k;
    document.querySelectorAll("#esK [data-k]").forEach((x) => { x.classList.toggle("on", x === b); x.textContent = (x === b ? "✓ " : "") + ES_KIND[x.dataset.k]; });
    load().catch((e) => toast(e.message, true));
  }));
  f.addEventListener("submit", (e) => { e.preventDefault(); load().catch((err) => toast(err.message, true)); });
  await load();
}

async function viewEstockInvoice(kind, id) {
  const inv = await api(`/api/estock/invoices/${kind}/${id}`), canRet = ME.permissions.includes("sales.return");
  const cost = inv.lines.some((l) => "cost" in l);
  app.innerHTML = `<section class="panel"><a href="#/estock?kind=${kind}">← تاريخ إي ستوك</a>
    <div class="row between"><h1>فاتورة قديمة: ${ES_KIND[kind]} — رقم ${esc(inv.bill_number || inv.estock_id)}</h1>${ES_BADGE}</div>
    <div class="totals"><div>التاريخ<b>${esc(inv.day)}</b></div><div>${kind === "purchase" ? "المورد" : "العميل"}<b>${esc(inv.party || "—")}</b></div>
      <div>الإجمالي<b>${esMoney(inv.total)}</b></div>${kind === "purchase" ? "" : `<div>نقدي<b>${esMoney(inv.cash)}</b></div><div>فيزا ومحفظة<b>${esMoney(inv.network)}</b></div>`}
      ${inv.ticket_no || inv.ticket_name ? `<div>التذكرة<b>${esc([inv.ticket_name, inv.ticket_no].filter(Boolean).join(" "))}</b></div>` : ""}</div>
    <div class="tablewrap mt"><table><thead><tr><th>الصنف</th><th>الكمية</th><th>المرتجع منها</th><th>السعر</th><th>الخصم</th><th>الإجمالي</th>${cost ? "<th>التكلفة</th>" : ""}<th>الصلاحية</th></tr></thead><tbody>
    ${inv.lines.map((l) => `<tr><td>${l.item_id ? `<a href="#/items/${esc(l.item_id)}">${esc(l.item)}</a>` : `<span class="badge b-warn">⚠ صنف مش مربوط</span>`}</td>
      <td class="num">${esc(l.qty ?? "")}</td><td class="num">${esc(l.returned ?? "")}</td><td class="num">${esMoney(l.price)}</td><td class="num">${esMoney(l.discount)}</td>
      <td class="num">${esMoney(l.total)}</td>${cost ? `<td class="num">${esMoney(l.cost)}</td>` : ""}<td>${esc(l.expiry || "")}</td></tr>`).join("")}</tbody></table></div>
    <p class="muted">الكميات بوحدات إي ستوك زي ما هي. فاتورة من البرنامج القديم: مبالغها لا تدخل في حسابات البرنامج.</p>
    ${kind === "sale" && canRet ? `<button class="btn mt" id="esRet">مرتجع من فاتورة إي ستوك قديمة</button>` : ""}
    <div id="esRetBox"></div></section>`;
  $("#esRet")?.addEventListener("click", () => estockReturnForm(id).catch((e) => toast(e.message, true)));
}

async function estockReturnForm(id) {
  const lines = (await api(`/api/estock/sales/${id}/return-lines`)).lines;
  $("#esRet").hidden = true;
  $("#esRetBox").innerHTML = `<form class="panel mt" id="esRetF"><h2>مرتجع من فاتورة إي ستوك قديمة</h2>
    <p class="muted">الفلوس بترجع بنفس الطريقة اللي اتدفعت بيها وبحد أقصى اللي اتدفع فعلًا: الآجل يقل من حساب العميل الأول، وبعدين النقدي من الدرج، وبعدين الفيزا والمحفظة.</p>
    <table><thead><tr><th>الصنف</th><th>المباع (وحدة)</th><th>رجع قبل كده</th><th>المرتجع الآن (وحدة)</th></tr></thead><tbody>
    ${lines.map((l) => { const left = (l.sold_units ?? 0) - l.returned_units; return `<tr><td>${esc(l.item || "—")}</td><td class="num">${l.sold_units ?? "—"}</td><td class="num">${l.returned_units}</td>
      <td>${l.item_id && l.sold_units != null && left > 0 ? `<input class="narrow" type="number" min="0" max="${left}" step="1" data-line="${esc(l.line_key)}" value="0">` : `<span class="muted">—</span>`}</td></tr>`; }).join("")}</tbody></table>
    <label class="f mt">سبب المرتجع <input name="reason" required></label>
    <div class="row mt"><button class="btn">سجّل المرتجع</button><button type="button" class="btn secondary" id="esRetNo">إلغاء</button></div></form>`;
  $("#esRetNo").addEventListener("click", () => { $("#esRetBox").innerHTML = ""; $("#esRet").hidden = false; });
  $("#esRetF").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, body = { lines: {}, reason: f.reason.value };
    f.querySelectorAll("[data-line]").forEach((inp) => { if (+inp.value > 0) body.lines[inp.dataset.line] = +inp.value; });
    if (!Object.keys(body.lines).length) { toast("اختر الكمية المرتجعة", true); return; }
    await withBusy(f.querySelector("button:not([type=button])"), async () => {
      try {
        const r = await api(`/api/estock/sales/${id}/return`, { method: "POST", body });
        const p = r.parts;
        $("#esRetBox").innerHTML = "";             // gone at once: a second press cannot send it again (#358 Codex P1)
        toast(`✓ اتسجل المرتجع: ${money(r.refund)} ج · من الحساب: ${money(p.account)} ج · نقدي: ${money(p.cash)} ج · فيزا ومحفظة: ${money(p.card)} ج`);
        await viewEstockInvoice("sale", id);
      } catch (err) { toast(err.message, true); }
    });
  });
}

async function viewEstockStatement(kind, id) {
  const st = await api(`/api/estock/statement/${kind}/${id}`);
  const items = kind === "customer" ? (await api(`/api/estock/customers/${id}/items`)).items : [];
  const back = kind === "customer" ? `#/customers/${id}` : `#/suppliers/${id}`;
  app.innerHTML = `<section class="panel"><a href="${back}">← ${kind === "customer" ? "العميل" : "المورد"}</a>
    <div class="row between"><h1>كشف الحساب القديم</h1>${ES_BADGE}</div>
    <p class="muted">كشف الحساب زي ما كان في إي ستوك قبل سطر رصيد أول المدة في البرنامج.</p>
    <div class="totals"><div>آخر رصيد في إي ستوك<b>${esMoney(st.estock_last_total) || "—"}</b></div>
      <div>رصيد أول المدة في البرنامج<b>${money(st.program_opening_total)}</b></div></div>
    <div class="tablewrap mt"><table><thead><tr><th>التاريخ</th><th>النوع</th><th>له</th><th>عليه</th><th>الرصيد</th><th>ملاحظات</th><th></th></tr></thead><tbody>
    ${st.lines.map((l) => `<tr><td>${esc(l.day)}</td><td>${esc(l.type || "")}</td><td class="num">${esMoney(l.for_him)}</td><td class="num">${esMoney(l.for_me)}</td>
      <td class="num">${esMoney(l.total)}</td><td>${esc(l.notes || "")}</td><td>${ES_BADGE}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">مفيش حركات قديمة</td></tr>`}</tbody></table></div>
    ${kind === "customer" ? `<h2 class="mt">اشترى إيه في إي ستوك</h2><table><thead><tr><th>الصنف</th><th>الكمية</th><th>عدد الفواتير</th><th>أول مرة</th><th>آخر مرة</th></tr></thead><tbody>
      ${items.map((i) => `<tr><td>${i.item_id ? `<a href="#/items/${esc(i.item_id)}">${esc(i.item)}</a>` : "—"}</td><td class="num">${esc(i.qty)}</td><td class="num">${i.invoices}</td><td>${esc(i.first_day)}</td><td>${esc(i.last_day)}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">مفيش</td></tr>`}</tbody></table>` : ""}
    <p class="mt"><a href="#/estock?kind=${kind === "customer" ? "sale" : "purchase"}&party=${esc(id)}">فواتيره القديمة في إي ستوك ←</a></p></section>`;
}

async function viewEstockItem(id) {
  const rows = (await api(`/api/estock/items/${id}/history`)).history, cost = rows.some((r) => "cost" in r);
  app.innerHTML = `<section class="panel"><a href="#/items/${esc(id)}">← الصنف</a><div class="row between"><h1>حركة الصنف في إي ستوك</h1>${ES_BADGE}</div>
    <div class="tablewrap"><table><thead><tr><th>التاريخ</th><th>النوع</th><th>الفاتورة</th><th>العميل أو المورد</th><th>الكمية</th><th>السعر</th>${cost ? "<th>التكلفة</th>" : ""}<th>الصلاحية</th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="clickable" data-go="${esc(r.kind)}/${esc(r.estock_id)}"><td>${esc(r.day)}</td><td>${ES_KIND[r.kind]}</td><td>${esc(r.bill_number || r.estock_id)}</td><td>${esc(r.party || "—")}</td>
      <td class="num">${esc(r.qty ?? "")}</td><td class="num">${esMoney(r.price)}</td>${cost ? `<td class="num">${esMoney(r.cost)}</td>` : ""}<td>${esc(r.expiry || "")}</td></tr>`).join("")
      || `<tr><td colspan="8" class="muted">مفيش حركة قديمة للصنف ده</td></tr>`}</tbody></table></div></section>`;
  app.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/estock/${tr.dataset.go}`; }));
}

// a small «تاريخ إي ستوك» panel at the end of a customer, supplier or item page (only when there is history)
async function estockPanel(kind, id) {
  if (!id || id === "new" || !(await estockOn())) return;
  const can = (p) => ME.permissions.includes(p), links = [];
  if (kind === "list") {                    // the sales and purchases lists: the way into the old invoices
    if (can(id === "purchase" ? "purchases.read" : "sales.totals")) links.push(`<a class="btn small secondary" href="#/estock?kind=${id}">فواتير إي ستوك القديمة</a>`);
  } else if (kind === "customer") {
    if (can("sales.totals")) links.push(`<a class="btn small secondary" href="#/estock?kind=sale&party=${esc(id)}">فواتيره القديمة</a>`);
    if (can("sales.read")) links.push(`<a class="btn small secondary" href="#/estock-statement/customer/${esc(id)}">كشف حسابه القديم واللي اشتراه</a>`);
  } else if (kind === "supplier") {
    if (can("purchases.read")) links.push(`<a class="btn small secondary" href="#/estock?kind=purchase&party=${esc(id)}">مشترياته القديمة</a>`);
    if (can("suppliers.read")) links.push(`<a class="btn small secondary" href="#/estock-statement/supplier/${esc(id)}">كشف حسابه القديم</a>`);
  } else if (kind === "item" && can("items.read")) {
    links.push(`<a class="btn small secondary" href="#/estock-item/${esc(id)}">حركته في إي ستوك</a>`);
  }
  if (!links.length || !app.isConnected) return;
  const sec = document.createElement("section");
  sec.className = "panel"; sec.id = "esPanel";
  sec.innerHTML = `<div class="row between"><h2>تاريخ إي ستوك</h2>${ES_BADGE}</div><div class="row">${links.join("")}</div><div id="esInline" class="mt"></div>`;
  app.appendChild(sec);
  // U4 (owner 17:39): the history shows right here, as if the program had run for years; read only, never posted
  try {
    let rows = null;
    if (kind === "customer" && can("sales.totals")) rows = (await api(`/api/estock/invoices?kind=sale&party=${encodeURIComponent(id)}&limit=30`)).invoices;
    else if (kind === "supplier" && can("purchases.read")) rows = (await api(`/api/estock/invoices?kind=purchase&party=${encodeURIComponent(id)}&limit=30`)).invoices;
    if (rows && rows.length && sec.isConnected) {
      $("#esInline").innerHTML = `<p class="muted">آخر ${rows.length} فاتورة من إي ستوك (للاطلاع، مش داخلة في أرصدة البرنامج)</p>
        <div class="tablewrap"><table><thead><tr><th>التاريخ</th><th>الفاتورة</th><th>الإجمالي</th><th>نقدي</th></tr></thead><tbody>
        ${rows.map((r) => `<tr class="clickable" data-go="${esc(r.kind)}/${esc(r.estock_id)}"><td>${esc(r.day)} ${ES_BADGE}</td><td>${esc(r.bill_number || r.estock_id)}</td>
          <td class="num">${esMoney(r.total)}</td><td class="num">${esMoney(r.cash)}</td></tr>`).join("")}</tbody></table></div>`;
    }
    if (kind === "item" && can("items.read")) {
      const h = (await api(`/api/estock/items/${encodeURIComponent(id)}/history`)).history.slice(0, 30);
      if (h.length && sec.isConnected) {
        $("#esInline").innerHTML = `<p class="muted">آخر ${h.length} حركة من إي ستوك (للاطلاع، مش داخلة في رصيد البرنامج)</p>
          <div class="tablewrap"><table><thead><tr><th>التاريخ</th><th>النوع</th><th>الفاتورة</th><th>العميل أو المورد</th><th>الكمية</th><th>السعر</th></tr></thead><tbody>
          ${h.map((r) => `<tr class="clickable" data-go="${esc(r.kind)}/${esc(r.estock_id)}"><td>${esc(r.day)} ${ES_BADGE}</td><td>${ES_KIND[r.kind]}</td><td>${esc(r.bill_number || r.estock_id)}</td>
            <td>${esc(r.party || "—")}</td><td class="num">${esc(r.qty ?? "")}</td><td class="num">${esMoney(r.price)}</td></tr>`).join("")}</tbody></table></div>`;
      }
    }
    if (kind === "customer" && can("sales.read")) {          // U4: his medicines from E-Stock, right on his page
      // its own try: a failed read here never hides the invoices drawn above (review P2)
      const all = await api(`/api/estock/customers/${encodeURIComponent(id)}/items`).then((r) => r.items).catch(() => []);
      const its = all.slice(0, 30);
      if (its.length && sec.isConnected) {
        const d = document.createElement("div");
        d.id = "esItems"; d.className = "mt";
        d.innerHTML = `<h3>أدويته في إي ستوك ${ES_BADGE}</h3>${all.length > 30 ? `<p class="muted">ظاهر أكتر 30 صنف بس؛ الباقي في «كشف حسابه القديم واللي اشتراه».</p>` : ""}<div class="tablewrap"><table><thead><tr><th>الصنف</th><th>الكمية</th><th>عدد الفواتير</th><th>أول مرة</th><th>آخر مرة</th></tr></thead><tbody>
          ${its.map((i) => `<tr><td>${i.item_id ? `<a href="#/items/${esc(i.item_id)}">${esc(i.item || "")}</a>` : `<span class="muted">صنف إي ستوك ${esc(i.estock_product_id || "")}</span>`}</td><td class="num">${esc(i.qty)}</td><td class="num">${i.invoices}</td><td>${esc(i.first_day)}</td><td>${esc(i.last_day)}</td></tr>`).join("")}</tbody></table></div>`;
        sec.appendChild(d);
      }
    }
    sec.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/estock/${tr.dataset.go}`; }));
  } catch { /* the links above still lead to the history */ }
}

// E-Stock amounts come as EGP text ("12.50"): exact piasters for sums, never a float (null when not a number)
const esP = (v) => {
  const m = /^(-?)(\d+)(?:\.(\d{1,2})\d*)?$/.exec(String(v ?? "").trim());
  return m ? (m[1] ? -1 : 1) * (Number(m[2]) * 100 + Number((m[3] || "0").padEnd(2, "0"))) : null;
};

// the sales list: the same period's E-Stock invoices right under the program's own, with both totals and the sum
let ES_SEQ = 0;
async function estockSalesBelow(box, f, t, q, ours, itemId) {
  const seq = ++ES_SEQ;                                // a late answer never lands under a newer search (U4 review)
  document.querySelector("#esBelow")?.remove();
  if (!(await estockOn()) || !ME.permissions.includes("sales.totals") || !box.isConnected) return;
  const p = new URLSearchParams({ kind: "sale", from: f, to: t, limit: "500" });
  if (/^\d+$/.test(q || "")) p.set("q", q);
  else if (q) return;                                  // a name search is the program's own (E-Stock keeps no names here)
  if (itemId) p.set("item", itemId);
  const [rows, per] = await Promise.all([api(`/api/estock/invoices?${p}`).then((r) => r.invoices),
    q || itemId ? Promise.resolve(null) : api(`/api/estock/period?${new URLSearchParams({ from: f, to: t, by: "month" })}`)]);
  if (!rows.length || !box.isConnected || seq !== ES_SEQ) return;
  // both sides on the same basis: sales of the period before returns (the program's returns follow their invoice,
  // E-Stock's follow their own date, so the two nets would not add up)
  const T = per ? per.total : null, sales = T ? esP(T.sales || "0") : null;
  const bad = T ? (T.sales_unreadable || 0) : 0;
  const sec = document.createElement("div");
  sec.id = "esBelow"; sec.className = "mt";
  sec.innerHTML = `<div class="row between"><h2>فواتير إي ستوك في نفس الفترة</h2>${ES_BADGE}</div>
    ${T ? `<p><b>${T.sales_count || 0} فاتورة — ${esMoney(T.sales || "0.00")} ج</b>${T.returns ? ` · <span>مرتجعات بتاريخها في الفترة</span> ${esMoney(T.returns)}` : ""}
      ${ours !== null && sales !== null && !bad ? ` · <b>مبيعات الفترة قبل المرتجع، البرنامج + إي ستوك: ${money(ours + sales)} ج</b>` : ""}</p>
      ${bad ? `<p class="badge b-warn">⚠ ${bad} فاتورة إي ستوك إجماليها مش مقروء: محسوبة في العدد ومش في المبلغ</p>` : ""}` : ""}
    <p class="muted"><span>أرقام إي ستوك زي ما هي، للاطلاع: مش داخلة في الخزينة ولا الأرصدة ولا تقارير البرنامج.</span> ${rows.length >= 500 ? "<span>ظاهر أول 500 فاتورة بس.</span>" : ""}</p>
    <div class="tablewrap"><table><thead><tr><th>الفاتورة</th><th>التاريخ</th><th>العميل</th><th>الإجمالي</th><th>نقدي</th><th>شبكة</th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="clickable" data-go="sale/${esc(r.estock_id)}"><td><b>${esc(r.bill_number || r.estock_id)}</b> ${ES_BADGE}</td><td>${esc(r.day)}</td>
      <td>${esc(r.party || "—")}</td><td class="num"><b>${esMoney(r.total)}</b></td><td class="num">${esMoney(r.cash)}</td><td class="num">${esMoney(r.network)}</td></tr>`).join("")}</tbody></table></div>`;
  box.appendChild(sec);
  sec.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/estock/${tr.dataset.go}`; }));
}

// U4 history step 2: the sales report and the purchases list show E-Stock's figures of the same period under the
// program's own, tagged, read only; added to ours only in one labelled line, on the same basis, when nothing is unreadable
let ES_RSEQ = 0, ES_REP = null;
async function estockReportBelow(box, f, t, ours) {
  const seq = ++ES_RSEQ;                               // a late answer never lands under a newer period
  ES_REP = null; document.querySelector("#esRep")?.remove();
  if (!(await estockOn()) || !ME.permissions.includes("sales.totals") || !box.isConnected) return;
  const by = (Date.parse(t) - Date.parse(f)) / 864e5 + 1 <= 62 ? "day" : "month";   // inclusive days
  const per = await api(`/api/estock/period?${new URLSearchParams({ from: f, to: t, by })}`);
  if (!box.isConnected || seq !== ES_RSEQ || !per.rows.length) return;
  // any unreadable E-Stock total, or no sales table at all, and nothing is added up (review P2)
  const T = per.total, buy = "purchases" in T, sales = "sales" in T ? esP(T.sales) : null;
  const bad = (T.sales_unreadable || 0) + (T.returns_unreadable || 0) + (T.purchases_unreadable || 0);
  const warn = (n) => (n ? ` <span class="badge b-warn" title="إجماليها مش مقروء">⚠ ${n}</span>` : "");
  const sec = document.createElement("div");
  sec.id = "esRep"; sec.className = "mt";
  sec.innerHTML = `<div class="row between"><h2>إي ستوك في نفس الفترة (${by === "day" ? "يوم بيوم" : "شهر بشهر"})</h2>${ES_BADGE}</div>
    ${ours != null && sales !== null && !bad ? `<p><b>مبيعات الفترة قبل المرتجع، البرنامج + إي ستوك: ${money(ours + sales)} ج</b></p>` : ""}
    ${bad ? `<p class="badge b-warn">⚠ ${bad} فاتورة إي ستوك إجماليها مش مقروء: محسوبة في العدد ومش في المبلغ، ومفيش جمع مع البرنامج</p>` : ""}
    <div class="tablewrap"><table><thead><tr><th>${by === "day" ? "اليوم" : "الشهر"}</th><th>فواتير</th><th>المبيعات</th><th>المرتجع بتاريخه</th>${buy ? "<th>المشتريات</th>" : ""}</tr></thead><tbody>
    ${per.rows.map((r) => `<tr><td>${esc(r.period)} ${ES_BADGE}</td><td class="num">${r.sales_count || 0}</td><td class="num">${esMoney(r.sales || "0.00")}${warn(r.sales_unreadable)}</td>
      <td class="num">${esMoney(r.returns || "0.00")}${warn(r.returns_unreadable)}</td>${buy ? `<td class="num">${esMoney(r.purchases || "0.00")}${warn(r.purchases_unreadable)}</td>` : ""}</tr>`).join("")}</tbody>
    <tfoot><tr><th>الإجمالي</th><th class="num">${T.sales_count || 0}</th><th class="num">${esMoney(T.sales || "0.00")}</th><th class="num">${esMoney(T.returns || "0.00")}</th>${buy ? `<th class="num">${esMoney(T.purchases || "0.00")}</th>` : ""}</tr></tfoot></table></div>
    <p class="muted"><span>أرقام إي ستوك زي ما هي، للاطلاع: مش داخلة في الخزينة ولا الأرصدة ولا أرقام البرنامج فوق.</span> <span>مرتجع إي ستوك محسوب بتاريخ رجوعه، وربح إي ستوك مش محسوب هنا.</span></p>`;
  box.appendChild(sec);
  ES_REP = { f, t, by, per };
}
const estockRepSheet = (f, t) => {                     // the E-Stock sheet of the sales report's Excel, text as it is
  const E = ES_REP; if (!E || E.f !== f || E.t !== t) return [];
  const buy = "purchases" in E.per.total;
  // an unreadable total is counted, never hidden: the file says the amount is incomplete (Codex P2 on #437)
  const bad = (r) => (r.sales_unreadable || 0) + (r.returns_unreadable || 0) + (r.purchases_unreadable || 0);
  return [{ name: "إي ستوك", columns: [Tx(E.by === "day" ? "اليوم" : "الشهر"), N("فواتير"), Tx("المبيعات (إي ستوك)"), Tx("المرتجع بتاريخه"), ...(buy ? [Tx("المشتريات")] : []), N("فواتير إجماليها مش مقروء (المبلغ ناقص)")],
    rows: E.per.rows.map((r) => [r.period, r.sales_count || 0, r.sales || "0.00", r.returns || "0.00", ...(buy ? [r.purchases || "0.00"] : []), bad(r)]),
    total: ["الإجمالي", E.per.total.sales_count || 0, E.per.total.sales || "0.00", E.per.total.returns || "0.00", ...(buy ? [E.per.total.purchases || "0.00"] : []), bad(E.per.total)] }];
};

let ES_PSEQ = 0;
let ES_PKEY = "";
async function estockPurchasesBelow(box, f, t, supplier, ours) {
  const key = [f, t, supplier, ours].join("|"), cur = document.querySelector("#esPur");
  if (cur && cur.isConnected && key === ES_PKEY && box.contains(cur)) return;   // a sort or a cleared search: same answer
  const seq = ++ES_PSEQ;
  ES_PKEY = key; cur?.remove();
  if (!(await estockOn()) || !ME.permissions.includes("purchases.read") || !box.isConnected) return;
  const p = new URLSearchParams({ kind: "purchase", from: f, to: t, limit: "500" }), q = new URLSearchParams({ from: f, to: t, by: "month" });
  if (supplier) { p.set("party", supplier); q.set("supplier", supplier); }
  const [rows, per] = await Promise.all([api(`/api/estock/invoices?${p}`).then((r) => r.invoices),
    // the period total needs sales.totals for the whole pharmacy; one supplier's total needs purchases.read only
    supplier || ME.permissions.includes("sales.totals") ? api(`/api/estock/period?${q}`) : Promise.resolve(null)]);
  if (!rows.length || !box.isConnected || seq !== ES_PSEQ) return;
  const T = per ? per.total : null, bad = T ? (T.purchases_unreadable || 0) : 0, es = T && T.purchases != null ? esP(T.purchases) : null;
  const sec = document.createElement("div");
  sec.id = "esPur"; sec.className = "mt";
  sec.innerHTML = `<div class="row between"><h2>مشتريات إي ستوك في نفس الفترة</h2>${ES_BADGE}</div>
    ${T ? `<p><b>${T.purchases_count || 0} فاتورة — ${esMoney(T.purchases || "0.00")} ج</b>
      ${ours != null && es !== null && !bad ? ` · <b>مشتريات الفترة، المعتمد في البرنامج + إي ستوك: ${money(ours + es)} ج</b> <span class="muted">(إي ستوك بتاريخ الإدخال)</span>` : ""}</p>
      ${bad ? `<p class="badge b-warn">⚠ ${bad} فاتورة إي ستوك إجماليها مش مقروء: محسوبة في العدد ومش في المبلغ</p>` : ""}` : ""}
    <p class="muted"><span>فواتير إي ستوك بتاريخ إدخالها فيه، للاطلاع: مش داخلة في المخزون ولا حساب المورد ولا الخزينة (الأرصدة جت مرة واحدة وقت النقل).</span> ${rows.length >= 500 ? "<span>ظاهر أول 500 فاتورة بس.</span>" : ""}</p>
    <div class="tablewrap"><table><thead><tr><th>الفاتورة</th><th>التاريخ</th><th>المورد</th><th>الإجمالي</th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="clickable" data-go="purchase/${esc(r.estock_id)}"><td><b>${esc(r.bill_number || r.estock_id)}</b> ${ES_BADGE}</td><td>${esc(r.day)}</td>
      <td>${esc(r.party || "—")}</td><td class="num"><b>${esMoney(r.total)}</b></td></tr>`).join("")}</tbody></table></div>`;
  box.appendChild(sec);
  sec.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/estock/${tr.dataset.go}`; }));
}

// ------------------------------------------------------------------ suppliers and their accounts
const PAY_M = { drawer: "نقدًا من الخزينة", cash: "نقدًا من خارج الخزينة", bank: "تحويل بنكي", cheque: "شيك", wallet: "إنستاباي / محفظة" };
const LEDGER_KIND = { opening: "رصيد أول المدة", purchase: "فاتورة شراء", payment: "دفعة", return: "مرتجع", adjustment: "تسوية / إلغاء", discount: "خصم" };
async function viewSuppliers() {
  const list = (await api("/api/suppliers/all")).suppliers, showBal = list.length && "balance" in list[0];
  app.innerHTML = `<section class="panel"><div class="row between"><h1>الموردون</h1>
    ${ME.permissions.includes("suppliers.write") ? `<form id="supNew" class="row"><input name="name" placeholder="اسم المورد" required>
      <select name="t"><option value="company">شركة</option><option value="warehouse" selected>مخزن</option></select><button class="btn small">أضف موردًا</button></form>` : ""}</div>
    <table><thead><tr><th>المورد</th><th>النوع</th><th>آخر فاتورة</th>${showBal ? "<th>مستحق له علينا</th>" : ""}</tr></thead><tbody>
    ${list.map((x) => `<tr class="clickable" data-go="${esc(x.id)}"><td><b>${esc(x.name)}</b>${x.is_active ? "" : ` <span class="badge b-plain">موقوف</span>`}</td>
      <td>${SUP_TYPE[x.supplier_type] ? `<span class="chip">${SUP_TYPE[x.supplier_type]}</span>` : `<span class="badge b-warn">⚠ حدد النوع</span>`}</td>
      <td>${esc(x.last_invoice || "—")}</td>${showBal ? `<td class="num">${x.balance > 0 ? `<b>${money(x.balance)}</b>` : x.balance < 0 ? `<span class="ok">مستحق لنا عنده ${money(-x.balance)}</span>` : "—"}</td>` : ""}</tr>`).join("")
      || `<tr><td colspan="4" class="muted">لا يوجد موردون</td></tr>`}</tbody></table>
    <p class="muted">نوع المورد (شركة أو مخزن) يؤثر في الضرائب، ويُحفظ مع كل فاتورة شراء.</p>
    ${ME.permissions.includes("cash.see_expected") ? `<p><a href="#/reconcile">مطابقة أرصدة الموردين والعملاء ←</a></p>` : ""}</section>
  ${showBal ? `<section class="panel" id="stRep"><div class="row between"><h2>مطالبات الموردين (تقفيل الحسابات)</h2>${reportTools("stall")}</div>
    <div class="row noprint">${rangeBar("stRange", "last")}<label class="f">النوع <select id="stType"><option value="">الكل</option>${Object.entries(SUP_TYPE).map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select></label></div>
    <div id="stBox"></div></section>` : ""}`;
  document.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/suppliers/${tr.dataset.go}`; }));
  if (showBal) {
    let st = null;
    const load = async (f, t) => {
      st = await api(`/api/suppliers/statements?from=${f}&to=${t}${$("#stType").value ? `&type=${$("#stType").value}` : ""}`);
      const T = st.totals;
      $("#stBox").innerHTML = `<p><b>${esc(rangeText(st.from, st.to))}</b></p><div class="tablewrap"><table><thead><tr><th>المورد</th><th>النوع</th><th>رصيد أول المدة</th><th>مسحوبات (${T.invoices} فاتورة)</th><th>مرتجع</th><th>المدفوع</th><th>تسويات</th><th>رصيد آخر المدة</th></tr></thead><tbody>
        ${st.rows.map((r) => `<tr class="clickable" data-sgo="${esc(r.supplier_id)}"><td><b>${esc(r.name)}</b></td><td>${esc(SUP_TYPE[r.type] || "—")}</td><td class="num">${money(r.opening)}</td>
          <td class="num">${money(r.purchases)} <small class="muted">(${r.invoices})</small></td><td class="num">${money(r.returns)}</td><td class="num ok">${money(r.payments)}</td>
          <td class="num">${r.adjustments ? money(r.adjustments) : ""}</td><td class="num"><b>${money(r.closing)}</b></td></tr>`).join("") || `<tr><td colspan="8" class="muted">لا توجد حركة في هذه الفترة</td></tr>`}</tbody>
        ${st.rows.length ? `<tfoot><tr><th>الإجمالي</th><th></th><th class="num">${money(T.opening)}</th><th class="num">${money(T.purchases)}</th><th class="num">${money(T.returns)}</th><th class="num">${money(T.payments)}</th><th class="num">${money(T.adjustments)}</th><th class="num">${money(T.closing)}</th></tr></tfoot>` : ""}</table></div>
        <p class="muted">اضغط على المورد لعرض كشف حسابه بالتفصيل وطريقة كل دفعة. تُحسب فواتير الشراء بتاريخ الفاتورة.</p>`;
      document.querySelectorAll("[data-sgo]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/suppliers/${tr.dataset.sgo}`; }));
    };
    const get = wireRange("stRange", (f, t) => load(f, t).catch((e) => toast(e.message, true)));
    $("#stType").addEventListener("change", () => load(...get()).catch((e) => toast(e.message, true)));
    wireTools("stall", () => ({ title: "مطالبات الموردين", subtitle: rangeText(st.from, st.to) + ($("#stType").value ? ` — ${SUP_TYPE[$("#stType").value]}` : ""), el: $("#stRep"), sheets: [
      { name: "المطالبات", columns: [Tx("المورد"), Tx("النوع"), M("رصيد أول المدة"), N("عدد الفواتير"), M("مسحوبات"), M("مرتجع"), M("المدفوع"), M("تسويات"), M("رصيد آخر المدة")],
        rows: st.rows.map((r) => [r.name, SUP_TYPE[r.type] || "", r.opening, r.invoices, r.purchases, r.returns, r.payments, r.adjustments, r.closing]),
        total: ["الإجمالي", "", st.totals.opening, st.totals.invoices, st.totals.purchases, st.totals.returns, st.totals.payments, st.totals.adjustments, st.totals.closing] }] }));
  }
  $("#supNew")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/suppliers", { method: "POST", body: { name: e.target.name.value, supplier_type: e.target.t.value } }); viewSuppliers(); }
    catch (err) { toast(err.message, true); }
  });
}

async function viewSupplier(id) {
  const [a, accs] = await Promise.all([api(`/api/suppliers/${id}`), moneyAccounts(true)]), canW = ME.permissions.includes("suppliers.write"), canPay = ME.permissions.includes("suppliers.pay");
  app.innerHTML = `<section class="panel"><a href="#/suppliers">← الموردون</a>
    <div class="row between"><h1>${esc(a.name)}</h1>
      <span class="seg2 small" id="supType">${Object.entries(SUP_TYPE).map(([v, t]) => `<button type="button" class="${a.supplier_type === v ? "on" : ""}" data-st="${v}" ${canW ? "" : "disabled"}>${a.supplier_type === v ? "✓ " : ""}${t}</button>`).join("")}</span></div>
    <div class="row"><label class="f">الهاتف <input id="supPhone" value="${esc(a.phone || "")}" ${canW ? "" : "disabled"}></label>
      <label class="f grow">شروط الدفع <input id="supTerms" value="${esc(a.payment_terms || "")}" placeholder="مثال: آجل 30 يومًا" ${canW ? "" : "disabled"}></label>
      ${canW ? `<button class="btn small secondary" id="supSave">احفظ</button>` : ""}</div></section>
  ${a.balance != null ? `<div class="totals"><div class="${a.balance > 0 ? "t-warn" : "t-ok"}">${a.balance > 0 ? "مستحق له علينا" : a.balance < 0 ? "مستحق لنا عنده" : "✓ لا يوجد حساب"}<b>${money(Math.abs(a.balance))} ج</b></div></div>` : ""}
  ${canPay ? `<form class="panel" id="supPay"><h2>تسجيل دفعة للمورد</h2><div class="formgrid">
    <label class="f">اتدفع من <select name="src">${srcOptions(accs, "treasury", { ownerOutside: true })}</select></label>
    <label class="f">المبلغ المدفوع (ج) <input name="a" type="number" step="0.01" min="0" required></label>
    <label class="f">خصم تعجيل الدفع <select name="dm"><option value="">مفيش</option><option value="amt">مبلغ</option><option value="pct">نسبة من المبلغ المسدد</option></select></label>
    <label class="f" id="supDisc" hidden><span id="supDiscL">الخصم (ج)</span> <input name="d" type="number" step="0.01" min="0"></label>
    <label class="chk"><input type="checkbox" name="chq"> بشيك</label>
    <label class="f">رقم الشيك / التحويل <input name="r" placeholder="مطلوب للشيك"></label>
    <label class="f grow">ملاحظة <input name="n"></label></div>
    <p class="muted" id="supPayHint"></p><button class="btn">سجّل الدفعة</button></form>` : ""}
  ${a.balance != null ? `<section class="panel" id="ssRep"><div class="row between"><h2>كشف حساب لفترة (للتقفيل مع المورد)</h2>${reportTools("sst")}</div>${rangeBar("ssRange", "last")}<div id="ssBox"></div></section>` : ""}
  <div class="grid2">
    <section class="panel"><h2>الفواتير</h2><table><thead><tr><th>الرقم</th><th>التاريخ</th><th>الحالة</th><th>الإجمالي</th></tr></thead><tbody>
      ${a.invoices.map((i) => `<tr class="clickable" data-inv="${esc(i.id)}"><td>${esc(i.invoice_no || "—")}</td><td>${esc(i.invoice_date || "")}</td>
        <td><span class="badge ${STATUS[i.status][1]}">${STATUS[i.status][0]}</span></td><td class="num">${money(i.computed_total ?? i.declared_total)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">لا توجد فواتير</td></tr>`}</tbody></table></section>
    ${a.ledger ? `<section class="panel"><h2>الحساب</h2><table><thead><tr><th>التاريخ</th><th>البيان</th><th>المبلغ</th><th>بواسطة</th></tr></thead><tbody>
      ${a.ledger.map((l) => `<tr><td>${esc(dayOf(l.created_at))}</td><td>${esc(LEDGER_KIND[l.kind] || l.kind)}${l.note ? ` — ${esc(l.note)}` : ""}</td>
        <td class="num ${l.amount < 0 ? "ok" : ""}">${l.amount < 0 ? "− " : "+ "}${money(Math.abs(l.amount))}</td><td>${esc(l.by_name || "")}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">لا توجد حركات</td></tr>`}</tbody></table></section>` : ""}
  </div>`;
  document.querySelectorAll("[data-inv]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/purchases/${tr.dataset.inv}`; }));
  const sp = $("#supPay");
  const hint = () => {
    const pct = sp.dm.value === "pct", a = toPiasters(sp.a.value) || 0, d = sp.d.value;
    $("#supDisc").hidden = !sp.dm.value;
    $("#supDiscL").textContent = pct ? "النسبة %" : "الخصم (ج)";
    if (!sp.dm.value || !a || !d) { $("#supPayHint").textContent = ""; return; }
    const disc = pct ? discountOf(a, toBp(d)) : toPiasters(d);
    const [paid, off] = pct ? [a - disc, a] : [a, a + disc];
    $("#supPayHint").textContent = `المدفوع: ${money(paid)} ج · الخصم: ${money(disc)} ج · يقل من حساب المورد: ${money(off)} ج`;
  };
  sp?.querySelectorAll("input, select").forEach((x) => x.addEventListener("input", hint));
  document.querySelectorAll("[data-st]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/suppliers/${id}`, { method: "PATCH", body: { supplier_type: b.dataset.st } }); viewSupplier(id); } catch (err) { toast(err.message, true); }
  }));
  $("#supSave")?.addEventListener("click", async () => {
    try { await api(`/api/suppliers/${id}`, { method: "PATCH", body: { phone: $("#supPhone").value, payment_terms: $("#supTerms").value } }); toast("تم الحفظ"); } catch (err) { toast(err.message, true); }
  });
  $("#supPay")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, body = { source: f.src.value, method: f.chq.checked ? "cheque" : "", ref: f.r.value || null, note: f.n.value || null };
    // percent: the amount typed is what is cleared from the supplier's balance; the program works out the discount
    if (f.dm.value === "pct") Object.assign(body, { settle: toPiasters(f.a.value), discount_bp: toBp(f.d.value) || 0 });
    else Object.assign(body, { amount: toPiasters(f.a.value), discount: f.dm.value === "amt" ? toPiasters(f.d.value) || 0 : 0 });
    await withBusy(f.querySelector("button:not([type=button])"), async () => {
      try { const r = await api(`/api/suppliers/${id}/pay`, { method: "POST", body });
        toast(r.discount ? `✓ اتدفع: ${money(r.paid)} ج · الخصم: ${money(r.discount)} ج` : "✓ تم تسجيل الدفعة"); viewSupplier(id); }
      catch (err) { toast(err.message, true); }
    });
  });
  if (a.balance == null) return;
  let st = null;
  wireRange("ssRange", async (f, t) => {
    try {
      st = await api(`/api/suppliers/${id}/statement?from=${f}&to=${t}`);
      $("#ssBox").innerHTML = `<div class="totals"><div>رصيد أول المدة<b>${money(st.opening)}</b></div><div>مسحوبات<b>${money(st.purchases)}</b><span>${st.invoices} فاتورة</span></div>
        <div>مرتجع<b>${money(st.returns)}</b></div>${st.adjustments ? `<div>تسويات / إلغاء<b>${money(st.adjustments)}</b></div>` : ""}<div class="t-ok">المدفوع<b>${money(st.payments)}</b><span>${st.by_method.map((m) => `${esc(m.label)} ${money(m.amount)}`).join(" · ")}</span></div>
        <div class="${st.closing > 0 ? "t-warn" : "t-ok"}">رصيد آخر المدة<b>${money(st.closing)}</b><span>${st.closing > 0 ? "مستحق له علينا" : st.closing < 0 ? "مستحق لنا عنده" : "خالص"}</span></div></div>
        <div class="tablewrap mt"><table><thead><tr><th>التاريخ</th><th>البيان</th><th>عليه (مسحوبات)</th><th>له (دفع/مرتجع)</th><th>الرصيد</th></tr></thead><tbody>
        <tr><td>${esc(st.from)}</td><td>رصيد أول المدة</td><td></td><td></td><td class="num"><b>${money(st.opening)}</b></td></tr>
        ${st.lines.map((l) => `<tr><td>${esc(l.day)}</td><td>${esc(LEDGER_KIND[l.kind] || l.kind)}${l.invoice_no ? ` رقم ${esc(l.invoice_no)}` : ""}${l.pay_method ? ` — ${esc(PAY_M[l.pay_method] || l.pay_method)}` : ""}${l.pay_ref ? ` ${esc(l.pay_ref)}` : ""}${l.note && !["payment", "purchase"].includes(l.kind) ? ` <small class="muted">${esc(l.note)}</small>` : ""}</td>
          <td class="num">${l.amount > 0 ? money(l.amount) : ""}</td><td class="num ok">${l.amount < 0 ? money(-l.amount) : ""}</td><td class="num">${money(l.balance)}</td></tr>`).join("")}</tbody></table></div>`;
    } catch (err) { toast(err.message, true); }
  });
  wireTools("sst", () => ({ title: `كشف حساب ${a.name}`, subtitle: rangeText(st.from, st.to), el: $("#ssRep"), sheets: [
    { name: "كشف الحساب", columns: [Dt("التاريخ"), Tx("البيان"), Tx("رقم الفاتورة"), Tx("طريقة الدفع"), Tx("رقم الشيك/التحويل"), M("عليه"), M("له"), M("الرصيد")],
      rows: [[st.from, "رصيد أول المدة", "", "", "", null, null, st.opening], ...st.lines.map((l) => [l.day, LEDGER_KIND[l.kind] || l.kind, l.invoice_no || "", l.pay_method ? PAY_M[l.pay_method] || l.pay_method : "", l.pay_ref || "",
        l.amount > 0 ? l.amount : null, l.amount < 0 ? -l.amount : null, l.balance])],
      total: ["", "رصيد آخر المدة", "", "", "", st.lines.reduce((x, l) => x + Math.max(0, l.amount), 0), st.lines.reduce((x, l) => x + Math.max(0, -l.amount), 0), st.closing] },
    { name: "طرق الدفع", columns: [Tx("الطريقة"), M("المبلغ")], rows: st.by_method.map((m) => [m.label, m.amount]) }] }));
}

// ------------------------------------------------------------------ returns to suppliers (مرتجعات للموردين)
// supplier-return proposals: each answer is for the supplier (and the line's batch) asked about; one that comes back
// after the supplier or the batch changed is dropped, so an older supplier's price never lands on the line (#368).
// A proposal is only shown: the return sends a value only when the person typed it, and the server prices the
// others at this supplier's own purchase price. A failed ask clears the old figure instead of keeping it.
const SR = { gen: 0, busy: false };
async function srPropose(l, supplier) {
  if (l.typed) return;                                       // a typed value is the person's, never replaced
  const gen = SR.gen, ask = (l.ask = (l.ask || 0) + 1);
  let v = null;
  try {
    v = (await api(`/api/supplier-returns/suggest?item=${l.item.id}&batch=${l.batch_id || ""}&supplier=${encodeURIComponent(supplier || "")}`)).unit_value;
  } catch { /* unknown here: the server prices it */ }
  if (gen === SR.gen && ask === l.ask && !l.typed) l.unit_value = v;   // typed meanwhile: the person's value stays
}
// Part 3 item 6b: a purchase return from its invoice. Supplier + invoice number load the lines with paid and bonus
// units; paid units are credited at the net invoice price plus their tax, bonus units go back on their own line at 0;
// settlement on the supplier's account; a line that came with bonus goes back whole (owner #310) unless the person
// may return part of it and writes why.
async function viewSupplierReturnInvoice() {
  const [sups, R] = await Promise.all([api("/api/suppliers"), stockReasons()]);
  const money_ = ME.permissions.includes("suppliers.pay") || ME.permissions.includes("costs.read");
  app.innerHTML = `<section class="panel"><a href="#/supplier-returns">← المرتجعات</a><h1>مرتجع من فاتورة شراء</h1>
    <form class="row" id="siF"><label class="f">المورد <select name="sup" required><option value="">— اختر —</option>${sups.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</select></label>
      <label class="f">رقم الفاتورة <input name="no" required></label><button class="btn small">هات الفاتورة</button></form><div id="siBox" class="mt"></div></section>`;
  $("#siF").addEventListener("submit", async (e) => {
    e.preventDefault();
    const sup = e.target.sup.value;
    try {
      const d = await api(`/api/supplier-returns/invoice?supplier=${encodeURIComponent(sup)}&no=${encodeURIComponent(e.target.no.value.trim())}`);
      $("#siBox").innerHTML = `<p>فاتورة ${esc(d.invoice_no)} — ${esc(d.invoice_date || "")}</p><div class="tablewrap"><table><thead><tr><th>الصنف</th><th>التشغيلة</th><th>المدفوع (وحدة)</th><th>البونص (وحدة)</th><th>اترجع قبل كده</th>${money_ ? "<th>صافي العلبة</th><th>الضريبة</th>" : ""}<th>الموجود</th><th>يرجع مدفوع</th><th>يرجع بونص</th><th>السبب</th></tr></thead><tbody>
        ${d.lines.map((l) => `<tr data-pl="${esc(l.id)}"><td>${esc(l.name)}${l.bonus_units ? ` <span class="chip">عليه بونص: بيرجع كله</span>` : ""}</td><td>${esc(l.batch_no || "")}</td><td>${l.paid_units}</td><td>${l.bonus_units}</td><td>${l.paid_returned + l.bonus_returned}</td>
          ${money_ ? `<td class="num">${money(l.unit_net_price || 0)}</td><td class="num">${money(l.tax_amount || 0)}</td>` : ""}<td>${l.on_hand}</td>
          <td><input class="narrow" type="number" min="0" max="${l.paid_units - l.paid_returned}" step="1" data-pu value="${l.bonus_units ? l.paid_units - l.paid_returned : ""}"></td>
          <td><input class="narrow" type="number" min="0" max="${l.bonus_units - l.bonus_returned}" step="1" data-bu value="${l.bonus_units ? l.bonus_units - l.bonus_returned : ""}" ${l.bonus_units ? "" : "disabled"}></td>
          <td><select data-rs>${Object.entries(R.supplier_return).map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join("")}</select></td></tr>`).join("")}</tbody></table></div>
        <div class="row mt"><label class="f">رقم إشعار المورد <input id="siCn"></label><label class="f grow">ملاحظة <input id="siNote"></label></div>
        <label class="f mt">سبب المرتجع الجزئي من سطر عليه بونص (لو فيه) <input id="siWhy"></label>
        <p class="muted">التسوية: على حساب المورد. المدفوع بسعر الفاتورة الصافي ومعاه الضريبة، والبونص بيرجع لوحده بصفر.</p>
        <button class="btn" id="siGo">سجّل المرتجع واطبع الإذن</button>`;
      $("#siGo").addEventListener("click", async (ev) => {
        const lines = [...document.querySelectorAll("[data-pl]")].map((tr) => ({ purchase_line_id: tr.dataset.pl,
          paid_units: +typedWhole(tr.querySelector("[data-pu]").value) || 0, bonus_units: +typedWhole(tr.querySelector("[data-bu]").value) || 0,
          reason: tr.querySelector("[data-rs]").value })).filter((l) => l.paid_units || l.bonus_units);
        if (!lines.length) { toast("اكتب كمية المرتجع", true); return; }
        await withBusy(ev.currentTarget, async () => {
          try {
            const r = await api("/api/supplier-returns", { method: "POST", body: { supplier_id: sup, invoice_id: d.invoice_id, lines,
              credit_note_no: $("#siCn").value || null, note: $("#siNote").value || null, partial_bonus_reason: $("#siWhy").value || null } });
            toast(`✓ مرتجع رقم ${r.return_no}`);
            location.hash = `#/supplier-returns/${r.id}`;
          } catch (err) { toast(err.message, true); if (err.code === "bonus_line_whole") $("#siWhy").focus(); }
        });
      });
    } catch (err) { toast(err.message, true); }
  });
}

async function viewSupplierReturns(arg) {
  if (arg && arg !== "new") return viewSupplierReturn(arg);
  SR.gen++; SR.busy = false;                                   // a new screen: nothing asked before counts
  const canNew = ME.permissions.includes("purchases.approve"), money_ = ME.permissions.includes("suppliers.pay") || ME.permissions.includes("costs.read");
  const [sups, R] = await Promise.all([api("/api/suppliers"), stockReasons()]);
  app.innerHTML = `${canNew ? `<section class="panel" id="srNew"><div class="row between"><h1>مرتجع جديد للمورد</h1><a class="btn secondary" href="#/supplier-returns/invoice">مرتجع من فاتورة شراء ←</a></div>
      <div class="row"><label class="f">المورد <select id="srSup"><option value="">— اختر —</option>${sups.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</select></label>
        <label class="f grow">الصنف <input id="srQ" placeholder="اسم أو باركود" autocomplete="off"></label></div>
      <div id="srRes" class="cands"></div>
      <div class="tablewrap"><table><thead><tr><th>الصنف</th><th>التشغيلة</th><th>الكمية (وحدة)</th><th>السبب</th>${money_ ? "<th>قيمة الوحدة (ج)</th>" : ""}<th></th></tr></thead><tbody id="srLines"></tbody></table></div>
      <div class="row mt"><label class="f grow">ملاحظة (اسم المندوب مثلًا) <input id="srNote"></label><b id="srTot"></b><button class="btn" id="srGo">سجّل المرتجع واطبع الإذن</button></div>
      <p class="muted">تخرج الكمية من المخزون، وتُخصم قيمتها من حساب المورد. القيمة المقترحة هي سعر شراء هذه التشغيلة، ويمكنك تغييرها إذا كان المورد سيحسبها بسعر آخر.</p></section>` : ""}
    <section class="panel" id="srRep"><div class="row between"><h2>المرتجعات للموردين</h2>${reportTools("sr")}</div>${rangeBar("srRange")}<div id="srBox"></div></section>`;
  // ---- new return
  const lines = [];
  const draw = () => {
    if (!$("#srLines")) return;
    // a redraw while a value field is focused (an answer arriving) puts the focus back, so typing goes on there
    const focused = document.activeElement?.dataset?.sv;
    $("#srLines").innerHTML = lines.map((l, i) => `<tr><td>${itemHtml(l.item)}</td>
      <td><select data-sb="${i}">${l.batches.map((b) => `<option value="${esc(b.batch_id || "")}" ${b.batch_id === l.batch_id ? "selected" : ""}>${esc(b.batch_no || "بدون تشغيلة")} — ${esc((b.expiry_date || "").slice(0, 7))} — متاح ${b.qty}</option>`).join("")}</select></td>
      <td><input class="narrow" type="number" min="1" step="1" data-sq="${i}" value="${l.qty_base}"></td>
      <td><select data-sw="${i}">${Object.entries(R.supplier_return).map(([k, t]) => `<option value="${k}" ${k === l.reason ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></td>
      ${money_ ? `<td><input class="narrow" type="number" min="0" step="0.01" data-sv="${i}" value="${l.unit_value == null ? "" : money(l.unit_value)}" placeholder="بسعر الشراء"></td>` : ""}
      <td><button class="btn small danger" data-sx="${i}" aria-label="إزالة">✕</button></td></tr>`).join("") || `<tr><td colspan="6" class="muted">ابحث عن الصنف وأضفه</td></tr>`;
    if (focused != null) { const x = $(`#srLines [data-sv="${focused}"]`); if (x) x.focus(); }
    if ($("#srTot")) $("#srTot").textContent = money_ && lines.length ? `الإجمالي: ${money(lines.reduce((a, l) => a + l.qty_base * (l.unit_value || 0), 0))} ج` : "";
    const val = async (l) => { await srPropose(l, $("#srSup")?.value); draw(); };
    document.querySelectorAll("[data-sb]").forEach((x) => x.addEventListener("change", () => { const l = lines[x.dataset.sb]; l.batch_id = x.value || null; val(l); }));
    document.querySelectorAll("[data-sq]").forEach((x) => x.addEventListener("change", () => { lines[x.dataset.sq].qty_base = +x.value || 1; draw(); }));
    document.querySelectorAll("[data-sw]").forEach((x) => x.addEventListener("change", () => { lines[x.dataset.sw].reason = x.value; }));
    // each keystroke is the person's value at once: an answer arriving while the field is still focused never
    // replaces it, whatever the browser does with the field it redraws (#368)
    document.querySelectorAll("[data-sv]").forEach((x) => x.addEventListener("input", () => {
      const l = lines[x.dataset.sv];
      if (x.value.trim() === "") return;
      l.unit_value = toPiasters(x.value) || 0; l.typed = true;
    }));
    document.querySelectorAll("[data-sv]").forEach((x) => x.addEventListener("change", () => {
      const l = lines[x.dataset.sv];
      if (x.value.trim() === "") { l.typed = false; val(l); return; }     // emptied: back to the purchase price
      l.unit_value = toPiasters(x.value) || 0; l.typed = true; draw();
    }));
    document.querySelectorAll("[data-sx]").forEach((x) => x.addEventListener("click", () => { lines.splice(+x.dataset.sx, 1); draw(); }));
  };
  const addItem = async (itemId) => {
    const c = await api(`/api/items/${itemId}/card`);
    const batches = (c.batches || []).filter((b) => b.qty > 0);
    if (!batches.length) { toast("هذا الصنف ليس له رصيد", true); return; }
    const b = batches[0], l = { item: c, batches, batch_id: b.batch_id || null, qty_base: Math.min(b.qty, c.pack_size || 1), reason: (b.expiry_date || "9") < isoDay(new Date()) ? "expired" : "near_expiry", unit_value: 0 };
    lines.push(l); draw();
    await srPropose(l, $("#srSup")?.value);
    draw();
  };
  // the proposed value is that supplier's price: another supplier, every line asked again (#324 S30)
  $("#srSup")?.addEventListener("change", async () => {
    const gen = ++SR.gen;
    SR.busy = true; $("#srGo").disabled = true;               // the return waits for the new supplier's prices
    await Promise.all(lines.map((l) => srPropose(l, $("#srSup").value)));
    if (gen !== SR.gen) return;                                // a newer change is still asking
    SR.busy = false; $("#srGo").disabled = false;
    draw();
  });
  if ($("#srQ")) {
    let t;
    $("#srQ").addEventListener("input", () => { clearTimeout(t); t = setTimeout(async () => {
      const v = $("#srQ").value.trim(); if (v.length < 2) { $("#srRes").innerHTML = ""; return; }
      const r = await findItems(v, 8);
      $("#srRes").innerHTML = r.items.map((it) => `<button class="btn small secondary" data-pick="${esc(it.id)}">${itemHtml(it)}</button>`).join("") || `<p class="muted">لا يوجد</p>`;
      document.querySelectorAll("[data-pick]").forEach((x) => x.addEventListener("click", () => { $("#srRes").innerHTML = ""; $("#srQ").value = ""; addItem(x.dataset.pick).catch((e) => toast(e.message, true)); }));
    }, 250); });
    $("#srGo").addEventListener("click", async () => {
      if (SR.busy) return;
      if (!$("#srSup").value) { toast("اختر المورد", true); return; }
      if (!lines.length) { toast("أضف صنفًا واحدًا على الأقل", true); return; }
      const w = window.open("", "_blank");
      try {
        const r = await api("/api/supplier-returns", { method: "POST", body: { supplier_id: $("#srSup").value, note: $("#srNote").value || null,
          lines: lines.map((l) => ({ item_id: l.item.id, batch_id: l.batch_id, qty_base: l.qty_base, reason: l.reason, ...(money_ && l.typed ? { unit_value: l.unit_value } : {}) })) } });
        toast(`✓ مرتجع رقم ${r.return_no} تم التسجيل`);
        if (w) w.location = `/supplier-return/${r.id}`;
        location.hash = "#/supplier-returns";
      } catch (err) { w?.close(); toast(err.message, true); }
    });
    const pre = new URLSearchParams(location.hash.split("?")[1] || "").get("item");
    if (pre) addItem(pre).catch((e) => toast(e.message, true));
    draw();
  }
  // ---- list
  let data = null;
  wireRange("srRange", async (f, t) => {
    try {
      data = await api(`/api/supplier-returns?from=${f}&to=${t}`);
      $("#srBox").innerHTML = `<div class="tablewrap"><table><thead><tr><th>رقم</th><th>اليوم</th><th>المورد</th><th>أصناف</th>${data.total != null ? "<th>القيمة</th>" : ""}<th>سجّله</th><th></th></tr></thead><tbody>
        ${data.rows.map((r) => `<tr><td><b>${r.return_no}</b></td><td>${esc(dayOf(r.created_at))}</td><td>${esc(r.supplier)}</td><td>${r.lines}</td>${data.total != null ? `<td class="num">${money(r.total)}</td>` : ""}
          <td>${esc(r.by_name || "")}</td><td><a href="#/supplier-returns/${esc(r.id)}">افتح</a> · <a href="/supplier-return/${esc(r.id)}" target="_blank">🖨 الإذن</a></td></tr>`).join("") || `<tr><td colspan="7" class="muted">لا توجد مرتجعات في هذه الفترة</td></tr>`}</tbody></table></div>
        ${data.total != null && data.rows.length ? `<p><b>الإجمالي: ${money(data.total)} ج</b></p>` : ""}`;
    } catch (err) { toast(err.message, true); }
  });
  wireTools("sr", () => ({ title: "المرتجعات للموردين", subtitle: rangeText(data.from, data.to), el: $("#srRep"), sheets: [
    { name: "المرتجعات", columns: [N("رقم"), Dt("اليوم"), Tx("المورد"), N("أصناف"), ...(data.total != null ? [M("القيمة")] : []), Tx("سجّله"), Tx("ملاحظة")],
      rows: data.rows.map((r) => [r.return_no, dayOf(r.created_at), r.supplier, r.lines, ...(data.total != null ? [r.total] : []), r.by_name || "", r.note || ""]) }] }));
}
async function viewSupplierReturn(id) {
  const r = await api(`/api/supplier-returns/${id}`);
  app.innerHTML = `<section class="panel"><a href="#/supplier-returns">← المرتجعات</a><div class="row between"><h1>مرتجع رقم ${r.return_no} — ${esc(r.supplier)}</h1>
    <a class="btn secondary" href="/supplier-return/${esc(r.id)}" target="_blank">🖨 اطبع الإذن</a></div>
    <p class="muted">${esc(dayOf(r.created_at))} ${hhmm(r.created_at)} · سجّله ${esc(r.by_name || "")}${r.note ? ` · ${esc(r.note)}` : ""}</p>
    ${r.credit_note_no ? `<p>إشعار المورد: <b>${esc(r.credit_note_no)}</b></p>` : ""}
    <table><thead><tr><th>الصنف</th><th>التشغيلة</th><th>الصلاحية</th><th>الكمية</th><th>السبب</th>${r.total != null ? `<th>قيمة الوحدة</th>${r.tax_total ? "<th>الضريبة</th>" : ""}<th>القيمة</th>` : ""}</tr></thead><tbody>
    ${r.lines.map((l) => `<tr><td>${itemHtml(l)}</td><td>${esc(l.batch_no || "")}</td><td>${esc((l.expiry_date || "").slice(0, 7))}</td><td>${esc(fmtStock(l.qty_base, l))}${l.is_bonus ? ` <span class="chip">بونص</span>` : ""}</td><td>${esc(l.reason_label)}</td>
      ${r.total != null ? `<td class="num">${money(l.unit_value)}</td>${r.tax_total ? `<td class="num">${money(l.tax_value || 0)}</td>` : ""}<td class="num">${money(l.unit_value * l.qty_base + (l.tax_value || 0))}</td>` : ""}</tr>`).join("")}</tbody></table>
    ${r.total != null ? `<p><b>الإجمالي المخصوم من حساب المورد: ${money(r.total)} ج</b></p>` : ""}</section>`;
}

// ------------------------------------------------------------------ transfers review (مراجعة التحويلات)
const TR_ST = { missing: ["b-warn", "⚠ لا توجد صورة"], pending: ["b-info", "ℹ بانتظار المراجعة"], ok: ["b-ok", "✓ تمت مراجعتها"], problem: ["b-err", "✕ بها مشكلة"], voided: ["b-plain", "ملغاة"] };
async function viewTransfers() {
  app.innerHTML = `<section class="panel" id="trRep"><div class="row between"><h1>مراجعة التحويلات (إنستاباي / فودافون كاش)</h1>${reportTools("tr")}</div>
    ${rangeBar("trRange", "week")}<div id="trBox"></div>
    <p class="muted noprint">قارن كل سطر برسائل البنك أو المحفظة، وافتح الفاتورة وحدد "وصل المبلغ" أو "بها مشكلة". يرفع البائع الصورة من شاشة البيع أو من الفاتورة.</p></section>`;
  let t = null;
  wireRange("trRange", async (f, to) => {
    try {
      t = await api(`/api/reports/transfers?from=${f}&to=${to}`);
      const T = t.totals;
      $("#trBox").innerHTML = `<div class="totals"><div>إجمالي التحويلات<b>${money(T.all)}</b></div><div class="t-ok">تمت مراجعتها<b>${money(T.ok)}</b></div>
        <div>بانتظار المراجعة<b>${money(T.pending)}</b></div><div class="${T.missing ? "t-warn" : ""}">بدون صورة<b>${money(T.missing)}</b></div><div class="${T.problem ? "t-err" : ""}">بها مشكلة<b>${money(T.problem)}</b></div></div>
        <div class="tablewrap mt"><table><thead><tr><th>الفاتورة</th><th>اليوم والوقت</th><th>المحفظة</th><th>مبلغ التحويل</th><th>البائع</th><th>الحالة</th></tr></thead><tbody>
        ${t.rows.map((r) => `<tr class="clickable" data-sale="${esc(r.id)}"><td><b>${r.sale_no}</b></td><td>${esc(dayOf(r.completed_at))} ${hhmm(r.completed_at)}</td><td>${esc(r.channel || "—")}</td>
          <td class="num"><b>${money(r.paid_transfer)}</b></td><td>${esc(r.cashier || "")}</td><td><span class="badge ${TR_ST[r.state][0]}">${TR_ST[r.state][1]}</span></td></tr>`).join("") || `<tr><td colspan="6" class="muted">لا توجد تحويلات في هذه الفترة</td></tr>`}</tbody></table></div>`;
      document.querySelectorAll("[data-sale]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/sales/${tr.dataset.sale}`; }));
    } catch (err) { toast(err.message, true); }
  });
  wireTools("tr", () => ({ title: "مراجعة التحويلات", subtitle: rangeText(t.from, t.to), el: $("#trRep"), sheets: [
    { name: "التحويلات", columns: [N("الفاتورة"), Tx("اليوم والوقت"), Tx("المحفظة"), M("مبلغ التحويل"), Tx("البائع"), Tx("الحالة")],
      rows: t.rows.map((r) => [r.sale_no, `${dayOf(r.completed_at)} ${hhmm(r.completed_at)}`, r.channel || "", r.paid_transfer, r.cashier || "", TR_ST[r.state][1].replace(/^[⚠ℹ✓✕] /, "")]),
      total: ["الإجمالي", "", "", t.totals.all, "", ""] }] }));
}

// ------------------------------------------------------------------ activity log (سجل العمليات)
function auditDetail(j) {
  if (!j) return "";
  try {
    const o = JSON.parse(j);
    return Object.entries(o).filter(([k]) => !["approved_by"].includes(k)).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ").slice(0, 160);
  } catch { return ""; }
}
async function viewAudit() {
  app.innerHTML = `<section class="panel" id="auRep"><div class="row between"><h1>سجل العمليات</h1>${reportTools("audit")}</div>
    <p class="muted noprint">تُسجَّل هنا كل عملية في البرنامج باسم من قام بها ووقتها، ولا يستطيع أحد تعديلها أو حذفها.</p>
    <div class="row noprint">${rangeBar("auRange", "today")}<label class="f">النوع <select id="auG"><option value="">الكل</option></select></label>
      <label class="f">الشخص <select id="auU"><option value="">الكل</option></select></label></div><div id="auBox"></div></section>`;
  let data = null;
  const draw = () => {
    const u = $("#auU").value, rows = data.rows.filter((r) => !u || r.user_name === u);
    $("#auBox").innerHTML = `${data.truncated ? `<p class="badge b-warn">⚠ أول 5000 عملية فقط — قلّل الفترة</p>` : ""}<div class="tablewrap"><table><thead><tr><th>الوقت</th><th>بواسطة</th><th>العملية</th><th>التفاصيل</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${esc(dayOf(r.at))} ${hhmm(r.at)}</td><td>${esc(r.user_name || "—")}</td><td>${esc(r.label)}${r.after_json?.includes("approved_by") ? ` <span class="badge b-info">بموافقة</span>` : ""}</td>
        <td class="muted small" data-raw>${esc(auditDetail(r.after_json))}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">لا توجد عمليات</td></tr>`}</tbody></table></div>`;
    return rows;
  };
  const load = async (f, t) => {
    data = await api(`/api/audit?from=${f}&to=${t}${$("#auG").value ? `&group=${$("#auG").value}` : ""}`);
    if ($("#auG").options.length === 1) $("#auG").insertAdjacentHTML("beforeend", Object.entries(data.groups).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join(""));
    const cur = $("#auU").value, names = [...new Set(data.rows.map((r) => r.user_name).filter(Boolean))];
    $("#auU").innerHTML = `<option value="">الكل</option>${names.map((n) => `<option ${n === cur ? "selected" : ""}>${esc(n)}</option>`).join("")}`;
    draw();
  };
  const get = wireRange("auRange", (f, t) => load(f, t).catch((e) => toast(e.message, true)));
  $("#auG").addEventListener("change", () => load(...get()).catch((e) => toast(e.message, true)));
  $("#auU").addEventListener("change", draw);
  wireTools("audit", () => { const rows = draw(); return { title: "سجل العمليات", subtitle: rangeText(data.from, data.to), el: $("#auRep"), sheets: [
    { name: "العمليات", columns: [Tx("الوقت"), Tx("بواسطة"), Tx("العملية"), Tx("التفاصيل")],
      rows: rows.map((r) => [`${dayOf(r.at)} ${hhmm(r.at)}`, r.user_name || "", r.label, auditDetail(r.after_json)]) }] }; });
}

// ------------------------------------------------------------------ shortages (النواقص)
const SH_REASON = { marked: ["b-warn", "⚠ مسجل ناقصًا"], below_min: ["b-warn", "⚠ تحت حد الطلب"], sold_out: ["b-err", "✕ نفد وكان يُباع"] };
async function viewShortages() {
  const items = (await api("/api/shortages")).items;
  const it = (r) => ({ pack_size: r.pack_size, base_unit_name: r.base_unit_name });
  app.innerHTML = `<section class="panel" id="shRep"><div class="row between"><h1>النواقص (${items.length})</h1>${reportTools("short")}</div>
    <p class="muted">أصناف سجّلها أحد ناقصة، أو وصلت إلى حد الطلب، أو نفدت وكانت تُباع في آخر 60 يومًا. تُزال العلامة تلقائيًا عند اعتماد فاتورة شراء الصنف.</p>
    <div class="tablewrap"><table><thead><tr><th>الصنف</th><th>السبب</th><th>الرصيد</th><th>حد الطلب</th><th>المبيع في 60 يومًا</th><th>المقترح</th><th>آخر مورد</th></tr></thead><tbody>
    ${items.map((r) => `<tr class="clickable" data-go="${esc(r.id)}"><td>${itemHtml(r)}</td><td>${r.reasons.map((x) => `<span class="badge ${SH_REASON[x][0]}">${SH_REASON[x][1]}</span>`).join(" ")}</td>
      <td>${esc(fmtStock(r.stock, it(r)) || "0")}</td><td>${r.min_stock ? esc(fmtStock(r.min_stock, it(r))) : "—"}</td><td>${esc(fmtStock(r.sold_60d, it(r)) || "0")}</td>
      <td><b>${r.suggest_base ? esc(fmtStock(r.suggest_base, it(r))) : "—"}</b></td><td>${esc(r.last_supplier || "—")}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">✓ لا توجد نواقص</td></tr>`}</tbody></table></div></section>`;
  document.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/items/${tr.dataset.go}`; }));
  wireTools("short", () => ({ title: "النواقص", subtitle: isoDay(new Date()), el: $("#shRep"), sheets: [
    { name: "النواقص", columns: [Tx("الصنف"), Tx("السبب"), Tx("الرصيد"), Tx("حد الطلب"), Tx("المبيع في 60 يومًا"), Tx("المقترح"), N("المقترح بالوحدة"), Tx("آخر مورد")],
      rows: items.map((r) => [r.name_en || r.name_ar, r.reasons.map((x) => SH_REASON[x][1].replace(/^[⚠✕] /, "")).join("، "), fmtStock(r.stock, it(r)) || "0",
        r.min_stock ? fmtStock(r.min_stock, it(r)) : "", fmtStock(r.sold_60d, it(r)) || "0", r.suggest_base ? fmtStock(r.suggest_base, it(r)) : "", r.suggest_base || "", r.last_supplier || ""]) }] }));
}

// ------------------------------------------------------------------ customers
const CL_KIND = { credit_sale: "فاتورة آجل", payment: "دفعة", return: "مرتجع", void: "إلغاء", opening: "رصيد أول المدة", adjustment: "تسوية", discount: "خصم مطالبة" };
async function viewCustomers(filter = "") {
  const canM = ME.permissions.includes("customers.manage");
  app.innerHTML = `<section class="panel" id="cuRep"><div class="row between"><h1>العملاء</h1>${ME.role === "owner" ? `<a class="btn secondary" href="#/customer-name-review">${NAME_REVIEW_TITLE}</a>` : ""}${reportTools("cust")}</div>
    <div class="row"><input id="cuQ" class="grow" placeholder="الاسم أو الموبايل أو الكود" autocomplete="off">
      <div class="seg2 small" id="cuF"><button data-f="" class="${!filter ? "on" : ""}">الكل</button><button data-f="debt" class="${filter === "debt" ? "on" : ""}">عليهم مستحقات</button><button data-f="refill" class="${filter === "refill" ? "on" : ""}">اقترب موعد طلبهم الشهري</button></div></div>
    <label class="f small">الترتيب <select id="cuSort"><option value="code">بالكود</option><option value="name">بالاسم</option><option value="balance">بالرصيد (الأكبر أولًا)</option></select></label>
    <table class="mt"><thead><tr><th>الكود</th><th>العميل</th><th>الهاتف</th><th>عليه</th><th>الطلب الشهري</th></tr></thead><tbody id="cuBody"></tbody></table><p id="cuSum"></p></section>
  ${canM ? `<section class="panel" id="cuDup" hidden></section>` : ""}
  ${ME.permissions.includes("contracts.manage") || ME.permissions.includes("sales.totals") ? `<section class="panel" id="ktBox"></section>` : ""}
  ${canM ? `<form class="panel row" id="cuNew"><h2 class="grow">عميل جديد</h2><label class="f">الاسم بالعربي <input name="name" required></label>
    <label class="f">اسم آخر (بالإنجليزية أو اسم الزوجة) <input name="name_alt"></label>
    <label class="f">الهاتف <input name="phone" inputmode="tel"></label><label class="f">هاتف 2 <input name="phone2" inputmode="tel"></label>
    <label class="f">العنوان <input name="address1"></label><label class="f">حد الآجل (ج) <input name="lim" type="number" min="0" value="0" class="narrow"></label>
    <button class="btn">أضف</button></form>` : ""}`;
  const today = isoDay(new Date());
  let cur = { list: [], f: "" };
  const load = async () => {
    const f = $("#cuF .on").dataset.f;
    const list = (await api(`/api/customers/all?q=${encodeURIComponent($("#cuQ").value)}${f ? `&only=${f}` : ""}&sort=${$("#cuSort").value}`)).customers;
    const debt = list.reduce((a, c) => a + Math.max(0, c.balance), 0);
    $("#cuSum").innerHTML = `<b>${list.length} عميل${debt ? ` — عليهم ${money(debt)} ج` : ""}</b>${list.length >= 300 ? ` <span class="muted">(أول 300 — ابحث بالاسم عن الباقي)</span>` : ""}`;
    cur = { list, f };
    $("#cuBody").innerHTML = list.map((c) => `<tr class="clickable" data-go="${esc(c.id)}"><td class="num">${esc(c.estock_code ?? "")}</td><td><b>${esc(c.name)}</b>${c.name_alt ? `<div class="muted small">${esc(c.name_alt)}</div>` : ""}</td><td dir="ltr">${[c.phone, c.phone2, c.phone3].filter(Boolean).map(esc).join("<br>")}</td>
      <td>${c.balance > 0 ? `<span class="badge b-err">✕ ${money(c.balance)}</span>` : `<span class="ok">✓ لا يوجد</span>`}</td>
      <td>${c.next_refill ? `<span class="badge ${c.next_refill <= today ? "b-err" : "b-warn"}">${c.next_refill <= today ? "✕ فات موعده" : "⚠"} ${esc(c.next_refill)}</span>` : "—"}</td></tr>`).join("")
      || `<tr><td colspan="5" class="muted">لا يوجد عملاء</td></tr>`;
    document.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/customers/${tr.dataset.go}`; }));

  };
  if ($("#ktBox")) renderContracts();
  // one number saved in two forms (010… and +20 10…) before the program kept one form (#324 M2-20)
  if ($("#cuDup")) api("/api/customers/phone-doubles").then((r) => {
    if (!r.doubles.length || !$("#cuDup")) return;
    $("#cuDup").innerHTML = `<h2>⚠ رقم واحد مسجل لأكثر من عميل</h2><p class="muted">الرقم نفسه مكتوب بصيغتين مختلفتين. راجع العملاء واترك الرقم لعميل واحد حتى لا يكون للمشتري الواحد حسابان آجلان.</p>
      <table><thead><tr><th>الرقم</th><th>العملاء</th></tr></thead><tbody>${r.doubles.map((d) => `<tr><td dir="ltr">${esc(d.phone)}</td>
      <td>${d.customers.map((c) => `<a href="#/customers/${esc(c.id)}">${esc(c.name)}</a>`).join("، ")}</td></tr>`).join("")}</tbody></table>`;
    $("#cuDup").hidden = false;
  }).catch(() => { /* the list stays without the notice */ });
  let t; $("#cuQ").addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 250); });
  $("#cuSort").addEventListener("change", load);
  const FT = { "": "كل العملاء", debt: "العملاء الذين عليهم مستحقات", refill: "اقترب موعد طلبهم الشهري" };
  wireTools("cust", () => { const { list, f } = cur; return { title: "تقرير العملاء", subtitle: FT[f] + ($("#cuQ").value ? ` — بحث: ${$("#cuQ").value}` : ""), el: $("#cuRep"), sheets: [
    { name: "العملاء", columns: [Tx("الاسم"), Tx("اسم آخر"), Tx("هاتف"), Tx("هاتف 2"), Tx("هاتف 3"), Tx("العنوان"), Tx("العنوان 2"), M("عليه"), M("حد الآجل"), Dt("الطلب الشهري"), Tx("واتساب")],
      rows: list.map((c) => [c.name, c.name_alt || "", c.phone || "", c.phone2 || "", c.phone3 || "", c.address1 || "", c.address2 || "", Math.max(0, c.balance), c.credit_limit, c.next_refill || "", c.whatsapp_ok ? "موافق" : ""]) }] }; });
  document.querySelectorAll("#cuF [data-f]").forEach((b) => b.addEventListener("click", () => { document.querySelectorAll("#cuF button").forEach((x) => x.classList.toggle("on", x === b)); load(); }));
  $("#cuNew")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const r = await api("/api/customers", { method: "POST", body: { name: e.target.name.value, name_alt: e.target.name_alt.value || null, phone: e.target.phone.value || null, phone2: e.target.phone2.value || null, address1: e.target.address1.value || null, credit_limit: toPiasters(e.target.lim.value) || 0 } }); location.hash = `#/customers/${r.id}`; }
    catch (err) { toast(err.message, true); }
  });
  load();
}

// contracts (التعاقدات): agreed discount for a company's / union's members
async function renderContracts(edit = null) {
  const box = $("#ktBox"); if (!box) return;
  const list = (await api("/api/contracts")).contracts, canK = ME.permissions.includes("contracts.manage"), e = edit ? list.find((x) => x.id === edit) : null;
  box.innerHTML = `<div class="row between"><h2>التعاقدات</h2></div>
    <p class="muted">شركة أو نقابة أو نادٍ له خصم متفق عليه: نسبة للمحلي ونسبة للمستورد. اربط العميل بالتعاقد من ملفه، ويُطبَّق الخصم تلقائيًا في شاشة البيع (ولا ينزل السعر عن التكلفة دون موافقة المالك).</p>
    <table><thead><tr><th>الجهة</th><th>خصم المحلي</th><th>خصم المستورد</th><th>عملاء</th><th></th></tr></thead><tbody>
    ${list.map((x) => `<tr class="${x.is_active ? "" : "muted"}"><td><b>${esc(x.name)}</b>${x.note ? `<div class="muted small">${esc(x.note)}</div>` : ""}${x.is_active ? "" : " <span class='badge b-plain'>موقوف</span>"}</td>
      <td>${pct(x.discount_local_bp)}%</td><td>${pct(x.discount_imported_bp)}%</td><td>${x.customers}</td>
      <td class="row">${ME.permissions.includes("sales.totals") ? `<button class="btn small secondary" data-ktrep="${esc(x.id)}">فواتيرهم</button>` : ""}${canK ? `<button class="btn small secondary" data-ktedit="${esc(x.id)}">تعديل</button>` : ""}</td></tr>`).join("")
      || `<tr><td colspan="5" class="muted">لا توجد تعاقدات</td></tr>`}</tbody></table>
    ${canK ? `<form id="ktForm" class="row mt"><label class="f grow">اسم الجهة <input name="n" required value="${esc(e?.name || "")}" placeholder="مثال: نقابة المهندسين"></label>
      <label class="f">خصم المحلي % <input class="narrow" name="l" type="number" min="0" max="50" step="0.5" value="${e ? pct(e.discount_local_bp) : ""}"></label>
      <label class="f">خصم المستورد % <input class="narrow" name="i" type="number" min="0" max="50" step="0.5" value="${e ? pct(e.discount_imported_bp) : ""}"></label>
      <label class="f grow">ملاحظة <input name="note" value="${esc(e?.note || "")}"></label>
      ${e ? `<label class="chk"><input type="checkbox" name="act" ${e.is_active ? "checked" : ""}> مفعّل</label>` : ""}
      <button class="btn small">${e ? "احفظ التعديل" : "أضف تعاقد"}</button>${e ? `<button type="button" class="btn small secondary" id="ktCancel">إلغاء</button>` : ""}</form>` : ""}
    <div id="ktRep" class="mt"></div>`;
  $("#ktForm")?.addEventListener("submit", async (ev) => {
    ev.preventDefault(); const f = ev.target;
    const body = { name: f.n.value, discount_local_bp: toBp(f.l.value) ?? 0, discount_imported_bp: toBp(f.i.value) ?? 0, note: f.note.value || null };
    if (e) body.is_active = f.act.checked;
    try { await api(`/api/contracts${e ? `/${e.id}` : ""}`, { method: "POST", body }); toast("تم الحفظ"); renderContracts(); } catch (err) { toast(err.message, true); }
  });
  $("#ktCancel")?.addEventListener("click", () => renderContracts());
  box.querySelectorAll("[data-ktedit]").forEach((b) => b.addEventListener("click", () => renderContracts(b.dataset.ktedit)));
  box.querySelectorAll("[data-ktrep]").forEach((b) => b.addEventListener("click", () => {
    const x = list.find((y) => y.id === b.dataset.ktrep);
    $("#ktRep").innerHTML = `<section class="panel" id="ktRepBox"><div class="row between"><h3>فواتير ${esc(x.name)}</h3>${reportTools("kt")}</div>${rangeBar("ktRange", "last")}<div id="ktRepOut"></div></section>`;
    let r = null;
    wireRange("ktRange", async (f, t) => {
      try {
        r = await api(`/api/contracts/${x.id}/report?from=${f}&to=${t}`);
        $("#ktRepOut").innerHTML = `<div class="totals"><div>الفواتير<b>${r.rows.length}</b></div><div>الإجمالي<b>${money(r.totals.total)}</b></div><div>الخصم<b>${money(r.totals.discount_total)}</b></div><div>آجل<b>${money(r.totals.on_credit)}</b></div><div>مرتجع<b>${money(r.totals.returned_total)}</b></div></div>
          <table class="mt"><thead><tr><th>الفاتورة</th><th>اليوم</th><th>العميل</th><th>الإجمالي</th><th>الخصم</th><th>آجل</th></tr></thead><tbody>
          ${r.rows.map((y) => `<tr class="clickable" data-sale="${esc(y.id)}"><td>${y.sale_no}</td><td>${esc(dayOf(y.completed_at))}</td><td>${esc(y.customer)}</td><td class="num">${money(y.total)}</td><td class="num">${money(y.discount_total)}</td><td class="num">${money(y.on_credit)}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">لا توجد فواتير</td></tr>`}</tbody></table>`;
        document.querySelectorAll("#ktRepOut [data-sale]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/sales/${tr.dataset.sale}`; }));
      } catch (err) { toast(err.message, true); }
    });
    wireTools("kt", () => ({ title: `فواتير تعاقد ${x.name}`, subtitle: rangeText(r.from, r.to), el: $("#ktRepBox"), sheets: [
      { name: "الفواتير", columns: [N("الفاتورة"), Dt("اليوم"), Tx("العميل"), M("الإجمالي"), M("الخصم"), M("آجل"), M("مرتجع")],
        rows: r.rows.map((y) => [y.sale_no, dayOf(y.completed_at), y.customer, y.total, y.discount_total, y.on_credit, y.returned_total]),
        total: ["الإجمالي", "", "", r.totals.total, r.totals.discount_total, r.totals.on_credit, r.totals.returned_total] }] }));
  }));
}

async function viewCustomer(id) {
  const c = await api(`/api/customers/${id}`), canM = ME.permissions.includes("customers.manage");
  // the payment methods list needs the sale permission: without it the collection is still offered, with no method
  // choice (#348 Codex P2), and nothing asks for an approval PIN just to open the customer
  const chans = canM && c.balance > 0 && ME.permissions.includes("sales.create")
    ? (await api("/api/payment-channels")).channels.filter((x) => x.is_active) : [];
  const it = (r) => ({ pack_size: r.pack_size, base_unit_name: r.base_unit_name });
  app.innerHTML = `<section class="panel"><a href="#/customers">← العملاء</a><div class="row between"><h1>${esc(c.name)}</h1>
      ${ME.permissions.includes("sales.create") ? `<button class="btn" id="cuSale">فاتورة له</button>` : ""}</div>
    <div class="formgrid"><label class="f">الاسم بالعربي <input id="cName" value="${esc(c.name)}" ${canM ? "" : "disabled"}></label>
      <label class="f">اسم آخر (بالإنجليزية أو اسم الزوجة) <input id="cName2" value="${esc(c.name_alt || "")}" ${canM ? "" : "disabled"}></label>
      <label class="f">الهاتف <input id="cPhone" value="${esc(c.phone || "")}" inputmode="tel" ${canM ? "" : "disabled"}></label>
      <label class="f">هاتف 2 <input id="cPhone2" value="${esc(c.phone2 || "")}" inputmode="tel" ${canM ? "" : "disabled"}></label>
      <label class="f">هاتف 3 <input id="cPhone3" value="${esc(c.phone3 || "")}" inputmode="tel" ${canM ? "" : "disabled"}></label>
      <label class="f">العنوان 1 <input id="cAddr1" value="${esc(c.address1 || "")}" ${canM ? "" : "disabled"}></label>
      <label class="f">العنوان 2 (اختياري) <input id="cAddr2" value="${esc(c.address2 || "")}" ${canM ? "" : "disabled"}></label>
      <label class="f">حد الآجل (ج) <input id="cLim" type="number" min="0" value="${money(c.credit_limit)}" ${canM ? "" : "disabled"}></label>
      <label class="f">ملاحظات <input id="cNotes" value="${esc(c.notes || "")}" ${canM ? "" : "disabled"}></label></div>
    <label class="chk mt"><input type="checkbox" id="cWa" ${c.whatsapp_ok ? "checked" : ""} ${canM ? "" : "disabled"}> يوافق على تلقي رسائل واتساب منا (تذكير الطلب الشهري)</label>
    <div class="row mt" id="cKt">${c.contract ? `<span class="chip">🤝 تعاقد: ${esc(c.contract.name)} — محلي ${pct(c.contract.discount_local_bp)}% / مستورد ${pct(c.contract.discount_imported_bp)}%</span>` : `<span class="muted">ليس على تعاقد</span>`}
      ${ME.permissions.includes("contracts.manage") ? `<select id="cKtSel"><option value="">— بدون تعاقد —</option></select><button class="btn small secondary" id="cKtSave">اربط</button>` : ""}</div>
    ${canM ? `<button class="btn small secondary mt" id="cSave">احفظ</button>` : ""}</section>
  <div class="totals"><div class="${c.balance > 0 ? "t-err" : "t-ok"}">${c.balance > 0 ? "✕ عليه" : "✓ لا شيء عليه"}<b>${money(Math.max(0, c.balance))} ج</b><span>الحد المسموح ${money(c.credit_limit)} ج</span></div>
    <div class="${c.whatsapp_ok ? "t-ok" : ""}">${c.whatsapp_ok ? "✓ موافق على واتساب" : "لا يوافق على الرسائل"}<b>${esc(c.phone || "—")}</b></div></div>
  ${canM && c.balance > 0 ? `<form class="panel row" id="cPay"><h2 class="grow">تحصيل من الحساب</h2><label class="f">المبلغ (ج) <input name="a" type="number" step="0.01" min="0" required></label>
    <label class="f">المصدر <select name="m"><option value="cash">نقدًا في الخزينة</option><option value="card">فيزا</option><option value="transfer">محفظة إلكترونية</option></select></label>
    <label class="f" id="cPayCh" hidden>عن طريق <select name="ch"></select></label><button class="btn">سجّل</button></form>` : ""}
  <div class="grid2">
    <section class="panel" id="cUsual"><h2>أدوية العميل</h2><p class="muted">بيتحمل…</p></section>
    <section class="panel"><div class="row between"><h2>الطلب الشهري</h2>${c.refills.length && ME.permissions.includes("sales.create") ? `<span class="row"><button class="btn small" id="cPrep">جهّز الطلب في شاشة البيع</button>${c.whatsapp_ok && c.wa.length && canM ? `<button class="btn small secondary" id="cWaSend">📲 ذكّره عبر واتساب</button>` : ""}</span>` : ""}</div>
      ${c.reminded_at ? `<p class="muted small">آخر تذكير أُرسل: ${esc(dayOf(c.reminded_at))}</p>` : ""}
      <table><thead><tr><th>الصنف</th><th>الكمية</th><th>كل</th><th>ميعاده</th><th>متوفر لدينا؟</th><th></th></tr></thead><tbody>
      ${c.refills.map((r) => `<tr><td>${itemHtml(r)}</td><td>${esc(fmtStock(r.qty_base, it(r)))}</td><td>${r.every_days} يوم</td>
        <td><span class="badge ${r.days_left < 0 ? "b-err" : r.days_left <= 5 ? "b-warn" : "b-ok"}">${r.days_left < 0 ? `✕ متأخر ${-r.days_left} يوم` : r.days_left <= 5 ? `⚠ بعد ${r.days_left} يوم` : `✓ ${esc(r.due)}`}</span></td>
        <td>${r.in_stock ? `<span class="ok">✓ موجود</span>` : `<span class="badge b-warn">⚠ غير كافٍ — اطلب</span>`}</td>
        <td>${canM ? `<button class="btn small secondary" data-rrm="${esc(r.item_id)}">إزالة</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">لا يوجد طلب شهري</td></tr>`}</tbody></table>
      ${c.suggest_refills.length && canM ? `<p class="badge b-info mt">ℹ اشترى هذه الأصناف في 3 أشهر أو أكثر — غالبًا علاج شهري:</p>
        <ul class="plain">${c.suggest_refills.map((r) => `<li>${itemHtml(r)} (${r.months} شهور) <button class="btn small secondary" data-radd="${esc(r.item_id)}" data-rq="${r.qty_base}">أضفه إلى الطلب الشهري</button></li>`).join("")}</ul>` : ""}
      ${canM ? `<form class="row mt" id="rAdd"><input id="rQ" class="grow" placeholder="أضف صنفًا إلى الطلب الشهري (اسم أو باركود)" autocomplete="off">
        <input id="rQty" type="number" min="1" value="1" class="narrow" title="الكمية بالعلبة"><select id="rEvery"><option value="30">كل شهر</option><option value="15">كل 15 يومًا</option><option value="60">كل شهرين</option></select>
        <button class="btn small">أضف</button></form><div id="rRes" class="cands"></div>` : ""}
      <p class="muted">البرنامج يقترح وأنت تؤكد. لا تُرسل أي رسالة تلقائيًا: زر واتساب يفتح المحادثة والرسالة جاهزة، وأنت من يضغط إرسال. ولا تحتوي الرسالة على اسم الدواء.</p></section>
    <section class="panel"><h2>الحساب</h2><table><thead><tr><th>التاريخ</th><th>البيان</th><th>المبلغ</th></tr></thead><tbody>
      ${c.ledger.map((l) => `<tr><td>${esc(dayOf(l.created_at))}</td><td>${esc(CL_KIND[l.kind] || l.kind)}${l.note ? ` — ${esc(l.note)}` : ""}</td>
        <td class="num ${l.amount < 0 ? "ok" : ""}">${l.amount < 0 ? "− " : "+ "}${money(Math.abs(l.amount))}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">لا توجد حركات</td></tr>`}</tbody></table>
      <h2 class="mt">آخر الفواتير</h2><ul class="plain">${c.sales.map((x) => `<li><a href="#/sales/${esc(x.id)}">فاتورة ${x.sale_no}</a> — ${money(x.total)} ج — ${esc(dayOf(x.completed_at))}</li>`).join("") || `<li class="muted">لا يوجد</li>`}</ul></section>
  </div>`;
  $("#cSave")?.addEventListener("click", async () => {
    try { await api(`/api/customers/${id}`, { method: "PATCH", body: { name: $("#cName").value, name_alt: $("#cName2").value || null, phone: $("#cPhone").value || null, phone2: $("#cPhone2").value || null, phone3: $("#cPhone3").value || null, address1: $("#cAddr1").value || null, address2: $("#cAddr2").value || null, notes: $("#cNotes").value || null, credit_limit: toPiasters($("#cLim").value) || 0, whatsapp_ok: $("#cWa").checked } }); toast("تم الحفظ"); viewCustomer(id); }
    catch (err) { toast(err.message, true); }
  });
  const cp = $("#cPay");
  cp?.m.addEventListener("change", () => {
    const list = chans.filter((x) => x.kind === cp.m.value);
    $("#cPayCh").hidden = cp.m.value === "cash" || !list.length;
    cp.ch.innerHTML = list.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("");
  });
  $("#cPay")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    await withBusy(f.querySelector("button:not([type=button])"), async () => {
      try { await api(`/api/customers/${id}/payments`, { method: "POST", body: { amount: toPiasters(f.a.value), method: f.m.value, channel_id: f.ch?.value || null } }); toast("✓ تم التسجيل"); viewCustomer(id); }
      catch (err) { toast(err.message, true); }
    });
  });
  // openDraft, not a bare SALE: the payment choice (credit, a split) left on another open invoice must not
  // become this invoice's payment
  const toPos = async (sid) => { await openDraft(sid); location.hash = "#/pos"; };
  $("#cuSale")?.addEventListener("click", async () => {
    try { const d = await api("/api/sales", { method: "POST", body: { customer_id: id } }); await toPos(d.id); } catch (err) { toast(err.message, true); }
  });
  if ($("#cKtSel")) {
    api("/api/contracts").then((r) => { $("#cKtSel").insertAdjacentHTML("beforeend", r.contracts.filter((x) => x.is_active).map((x) => `<option value="${esc(x.id)}" ${c.contract?.id === x.id ? "selected" : ""}>${esc(x.name)}</option>`).join("")); });
    $("#cKtSave").addEventListener("click", async () => {
      try { await api(`/api/customers/${id}/contract`, { method: "POST", body: { contract_id: $("#cKtSel").value || null } }); toast("تم الحفظ"); viewCustomer(id); } catch (err) { toast(err.message, true); }
    });
  }
  $("#cWaSend")?.addEventListener("click", async () => {
    const w = window.open("", "_blank");
    try { const r = await api(`/api/customers/${id}/refills`, { method: "POST", body: { whatsapp: true } }); if (w) { w.opener = null; w.location = r.url; } else location.href = r.url; }
    catch (err) { w?.close(); toast(err.message, true); }
  });
  const usualBox = $("#cUsual");            // this customer's own panel (Codex P2 on #419: a late answer for A never fills B)
  api(`/api/customers/${id}/usual`).then(({ items }) => {       // U3-6 «أدوية العميل»: last 6 months
    if (!usualBox?.isConnected) return;
    usualBox.innerHTML = `<h2>أدوية العميل <span class="muted small">(آخر 6 شهور)</span></h2>${items.length ? `<table><thead><tr><th>الصنف</th><th>مرات الشراء</th><th>آخر مرة</th><th>الكمية المعتادة</th><th>ميعاد الطلب الشهري</th><th>متوفر؟</th></tr></thead><tbody>
      ${items.map((x) => `<tr><td>${itemHtml(x)}</td><td class="num">${x.times}</td><td>${esc(dayOf(x.last_at))}</td><td>${esc(fmtStock(x.last_qty || 1, x))}</td>
        <td>${x.refill_due ? `${esc(x.refill_due)}${x.refill_days_left <= 0 ? ` <span class="badge b-warn">جه ميعاده</span>` : ""}` : "—"}</td><td>${x.in_stock ? "✓" : `<span class="badge b-err">لأ</span>`}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">مفيش مشتريات في آخر 6 شهور</p>`}`;
  }).catch(() => { if (usualBox?.isConnected) usualBox.remove(); });
  $("#cPrep")?.addEventListener("click", async () => {
    try { const r = await api(`/api/customers/${id}/refills`, { method: "POST", body: { prepare: true } }); await toPos(r.sale_id); } catch (err) { toast(err.message, true); }
  });
  document.querySelectorAll("[data-rrm]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirmInline(b)) return;
    try { await api(`/api/customers/${id}/refills`, { method: "POST", body: { remove: true, item_id: b.dataset.rrm } }); viewCustomer(id); } catch (err) { toast(err.message, true); }
  }));
  document.querySelectorAll("[data-radd]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/customers/${id}/refills`, { method: "POST", body: { item_id: b.dataset.radd, qty_base: +b.dataset.rq, every_days: 30 } }); viewCustomer(id); } catch (err) { toast(err.message, true); }
  }));
  let picked = null;
  $("#rQ")?.addEventListener("input", async (e) => {
    picked = null;
    const v = e.target.value.trim(); if (v.length < 2) return;
    const f = await findItems(v, 6);
    $("#rRes").innerHTML = hintHtml(f.hint) + f.items.map((r, i) => `<button type="button" class="btn small secondary" data-rp="${i}">${itemHtml(r)}</button>`).join("");
    $("#rRes").querySelectorAll("[data-rp]").forEach((b) => b.addEventListener("click", () => { picked = f.items[+b.dataset.rp]; $("#rQ").value = itemText(picked); $("#rRes").innerHTML = ""; }));
  });
  $("#rAdd")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!picked) return toast("اختر الصنف من القائمة", true);
    try { await api(`/api/customers/${id}/refills`, { method: "POST", body: { item_id: picked.id, qty_base: (+$("#rQty").value || 1) * picked.pack_size, every_days: +$("#rEvery").value } }); viewCustomer(id); }
    catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ expenses
const EX_ST = { pending: ["b-warn", "⚠ بانتظار الموافقة"], approved: ["b-ok", "✓ معتمد"], rejected: ["b-err", "✕ مرفوض"] };
async function viewExpenses(period) {
  const pr = periodPresets().month, [pf, pt] = period || [isoDay(pr[1]), isoDay(pr[2])];
  const [r, accs] = await Promise.all([api(`/api/expenses?from=${pf}&to=${pt}`), moneyAccounts(true)]), canA = ME.permissions.includes("expenses.approve");
  const pending = r.expenses.filter((e) => e.status === "pending");
  app.innerHTML = `<form class="panel" id="exNew"><h1>تسجيل مصروف</h1>
    <div class="formgrid"><label class="f">البند <select name="cat">${r.categories.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select></label>
      <label class="f">المبلغ (ج) <input name="a" type="number" step="0.25" min="0" required></label>
      <label class="f">اتدفع من <select name="from">${srcOptions(accs, "drawer")}</select></label>
      <label class="f">التاريخ <input name="d" type="date" value="${isoDay(new Date())}"></label>
      <label class="f">ملاحظة <input name="n" placeholder="مثال: أكياس + بكرة إيصالات"></label></div>
    <button class="btn mt">سجّل</button>
    <p class="muted">${ME.role === "owner" ? "مصروفاتك تُعتمد مباشرة." : `ينتظر المصروف موافقة ${canA ? "المالك إذا كان أكبر من " + money(r.manager_limit) + " ج" : "المدير أو المالك"} قبل احتسابه.`}</p></form>
  ${canA && pending.length ? `<section class="panel"><h2>⚠ بانتظار موافقتك (${pending.length})</h2>${exTable(pending, true)}</section>` : ""}
  ${canA ? `<section class="panel" id="scBox"></section>` : ""}
  <section class="panel" id="exRep"><div class="row between"><h2>المصروفات — ${esc(rangeText(pf, pt))}</h2>${reportTools("exp")}</div>
    <div class="row between noprint"><div id="exRangeBox"></div>${ME.permissions.includes("costs.read") ? `<a href="#/profit">تقرير الربح ←</a>` : ""}</div>
    ${exTable(r.expenses, false)}
    <p><b>الإجمالي المعتمد: ${money(r.totals ? r.totals.approved : r.expenses.filter((e) => e.status === "approved").reduce((a, e) => a + e.amount, 0))} ج</b></p>
    ${canA ? `<form class="row mt" id="exCat"><input name="n" placeholder="بند جديد" required><button class="btn small secondary">أضف بند</button></form>` : ""}</section>`;
  if (canA) renderSchedules(r.categories, null, accs);
  $("#exRangeBox").innerHTML = rangeBar("exRange");
  $("#exRange [data-rf]").value = pf; $("#exRange [data-rt]").value = pt;
  $("#exRange").querySelectorAll("[data-rp]").forEach((x) => x.classList.toggle("on", false));
  let first = true;
  wireRange("exRange", (f, t) => { if (first) { first = false; return; } viewExpenses([f, t]); });
  wireTools("exp", () => ({ title: "المصروفات", subtitle: rangeText(pf, pt), el: $("#exRep"), sheets: [
    { name: "المصروفات", columns: [Dt("التاريخ"), Tx("البند"), Tx("ملاحظة"), M("المبلغ"), Tx("المصدر"), Tx("سجّله"), Tx("الحالة")],
      rows: r.expenses.map((e) => [e.spent_on, e.category, e.note || "", e.amount, srcName(e), e.by_name || "", EX_ST[e.status][1]]) }] }));
  $("#exNew").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    await withBusy(f.querySelector("button:not([type=button])"), async () => {
      try { const x = await api("/api/expenses", { method: "POST", body: { category_id: f.cat.value, amount: toPiasters(f.a.value), paid_from: f.from.value, spent_on: f.d.value, note: f.n.value || null } });
        toast(x.status === "approved" ? "✓ سُجّل واعتُمد" : "سُجّل — بانتظار الموافقة"); viewExpenses(); }
      catch (err) { toast(err.message, true); }
    });
  });
  $("#exCat")?.addEventListener("submit", async (e) => { e.preventDefault(); try { await api("/api/expenses", { method: "POST", body: { new_category: e.target.n.value } }); viewExpenses(); } catch (err) { toast(err.message, true); } });
  document.querySelectorAll("[data-exok]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/expenses/${b.dataset.exok}/decide`, { method: "POST", body: { approve: true } }); viewExpenses(); } catch (err) { toast(err.message, true); }
  }));
  document.querySelectorAll("[data-exno]").forEach((b) => b.addEventListener("click", async () => {
    const reason = prompt("سبب الرفض:"); if (!reason) return;
    try { await api(`/api/expenses/${b.dataset.exno}/decide`, { method: "POST", body: { approve: false, reason } }); viewExpenses(); } catch (err) { toast(err.message, true); }
  }));
}
const EVERY_T = { 1: "كل شهر", 2: "كل شهرين", 3: "كل 3 أشهر", 6: "كل 6 أشهر", 12: "كل سنة" };
const SC_ST = { late: ["b-err", "✕ فات موعده"], soon: ["b-warn", "⚠ اقترب"], later: ["b-ok", "✓"] };
async function renderSchedules(cats, edit = null, accs = null) {
  accs = accs || await moneyAccounts();
  const list = (await api("/api/expense-schedules")).schedules, box = $("#scBox");
  if (!box) return;
  const e = edit ? list.find((x) => x.id === edit) : null, v = (k, d = "") => esc(e ? e[k] ?? d : d);
  box.innerHTML = `<h2>المصروفات المجدولة (الإيجار، الكهرباء، الإنترنت…)</h2>
    <p class="muted">يصلك تذكير قبل موعد كل بند بعدد الأيام الذي تختاره، ويستمر التذكير حتى يُسجَّل. ويخضع التسجيل لقواعد الموافقة والخزينة نفسها.</p>
    <div class="tablewrap"><table><thead><tr><th>البند</th><th>المبلغ</th><th>الموعد</th><th>التذكير قبلها</th><th>القادم</th><th></th></tr></thead><tbody>
    ${list.map((x) => `<tr class="${x.is_active ? "" : "muted"}"><td><b>${esc(x.name)}</b><div class="muted small">${esc(x.category)}${x.note ? ` · ${esc(x.note)}` : ""}</div></td>
      <td class="num">${x.amount ? money(x.amount) : "<span class='muted'>متغير</span>"}</td><td>يوم ${x.day_of_month} · ${EVERY_T[x.every_months]}</td><td>${x.remind_days} يوم</td>
      <td>${!x.is_active ? "موقوف" : x.next ? `<span class="badge ${SC_ST[x.next.state][0]}">${SC_ST[x.next.state][1]} ${esc(x.next.due)}</span>` : "—"}${x.last_paid ? `<div class="muted small">آخر مرة: ${esc(x.last_paid)}</div>` : ""}</td>
      <td class="row">${x.is_active && x.next ? `${x.amount ? "" : `<input class="narrow" type="number" min="0" step="0.01" placeholder="المبلغ" data-scamt="${esc(x.id)}">`}
        <button class="btn small" data-scpay="${esc(x.id)}" data-p="${esc(x.next.period)}">سجّل الدفع</button><button class="btn small secondary" data-scskip="${esc(x.id)}" data-p="${esc(x.next.period)}">ليس هذه المرة</button>` : ""}
        <button class="btn small secondary" data-scedit="${esc(x.id)}">تعديل</button></td></tr>`).join("") || `<tr><td colspan="6" class="muted">لا توجد بنود مجدولة بعد</td></tr>`}</tbody></table></div>
    <form id="scForm" class="mt"><h3>${e ? `تعديل: ${esc(e.name)}` : "بند مجدول جديد"}</h3><div class="formgrid">
      <label class="f">الاسم <input name="name" required value="${v("name")}" placeholder="مثال: إيجار الصيدلية"></label>
      <label class="f">بند المصروف <select name="cat">${cats.map((c) => `<option value="${esc(c.id)}" ${e && e.category_id === c.id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></label>
      <label class="f">المبلغ (ج) — اتركه فارغًا إذا كان متغيرًا <input name="a" type="number" min="0" step="0.01" value="${e && e.amount ? money(e.amount).replace(/,/g, "") : ""}"></label>
      <label class="f">يوم الاستحقاق في الشهر <input name="day" type="number" min="1" max="31" required value="${v("day_of_month", 1)}"></label>
      <label class="f">يتكرر <select name="every">${Object.entries(EVERY_T).map(([k, t]) => `<option value="${k}" ${e && e.every_months === +k ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      <label class="f">ذكّرني قبله بكم يوم <input name="rd" type="number" min="0" max="60" required value="${v("remind_days", 3)}"></label>
      <label class="f">يتدفع من <select name="from">${srcOptions(accs, e ? e.account_id || e.paid_from : "treasury")}</select></label>
      <label class="f">أول شهر <input name="start" type="month" value="${v("start_month", isoDay(new Date()).slice(0, 7))}"></label>
      <label class="f">ملاحظة <input name="note" value="${v("note")}"></label></div>
      ${e ? `<label class="chk"><input type="checkbox" name="active" ${e.is_active ? "checked" : ""}> مفعّل</label>` : ""}
      <div class="row mt"><button class="btn">${e ? "احفظ التعديل" : "أضف"}</button>${e ? `<button type="button" class="btn secondary" id="scCancel">إلغاء</button>` : ""}</div></form>`;
  $("#scForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target, body = { name: f.name.value, category_id: f.cat.value, amount: f.a.value ? toPiasters(f.a.value) : null, day_of_month: +f.day.value,
      every_months: +f.every.value, remind_days: +f.rd.value, paid_from: f.from.value, start_month: f.start.value, note: f.note.value || null };
    if (e) body.is_active = f.active.checked;
    try { await api(`/api/expense-schedules${e ? `/${e.id}` : ""}`, { method: "POST", body }); toast("تم الحفظ"); renderSchedules(cats, null, accs); }
    catch (err) { toast(err.message, true); }
  });
  $("#scCancel")?.addEventListener("click", () => renderSchedules(cats, null, accs));
  box.querySelectorAll("[data-scedit]").forEach((b) => b.addEventListener("click", () => { renderSchedules(cats, b.dataset.scedit, accs).then(() => $("#scForm").scrollIntoView()); }));
  box.querySelectorAll("[data-scpay]").forEach((b) => b.addEventListener("click", async () => {
    const amt = box.querySelector(`[data-scamt="${b.dataset.scpay}"]`);
    if (amt && !amt.value) { amt.focus(); toast("اكتب المبلغ هذه المرة", true); return; }
    await withBusy(b, async () => {
      const shown = list.find((x) => x.id === b.dataset.scpay)?.amount ?? null;      // the amount this screen showed (M2-11)
      try { const x = await api(`/api/expense-schedules/${b.dataset.scpay}`, { method: "POST", body: { action: "pay", period: b.dataset.p, amount: amt ? toPiasters(amt.value) : null, expected_amount: amt ? null : shown } });
        toast(x.status === "approved" ? "✓ سُجّل واعتُمد" : "سُجّل — بانتظار الموافقة"); viewExpenses(); }
      catch (err) { toast(err.message, true); }
    });
  }));
  box.querySelectorAll("[data-scskip]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirmInline(b)) return;
    try { await api(`/api/expense-schedules/${b.dataset.scskip}`, { method: "POST", body: { action: "skip", period: b.dataset.p } }); renderSchedules(cats, null, accs); }
    catch (err) { toast(err.message, true); }
  }));
}
function exTable(list, actions) {
  return `<table><thead><tr><th>التاريخ</th><th>البند</th><th>المبلغ</th><th>المصدر</th><th>سجّله</th><th>الحالة</th>${actions ? "<th></th>" : ""}</tr></thead><tbody>
    ${list.map((e) => `<tr><td>${esc(e.spent_on)}</td><td>${esc(e.category)}${e.note ? `<div class="muted">${esc(e.note)}</div>` : ""}</td><td class="num">${money(e.amount)}</td>
      <td>${esc(srcName(e))}</td><td>${esc(e.by_name || "")}</td>
      <td><span class="badge ${EX_ST[e.status][0]}">${EX_ST[e.status][1]}</span>${e.reject_reason ? `<div class="muted">${esc(e.reject_reason)}</div>` : ""}</td>
      ${actions ? `<td><button class="btn small" data-exok="${esc(e.id)}">اعتمد</button> <button class="btn small secondary" data-exno="${esc(e.id)}">ارفض</button></td>` : ""}</tr>`).join("")
      || `<tr><td colspan="7" class="muted">لا توجد مصروفات</td></tr>`}</tbody></table>`;
}

// ------------------------------------------------------------------ profit (owner)
async function viewProfit() {
  app.innerHTML = `<section class="panel"><h1>الربح</h1>${rangeBar("prRange")}</section><div id="prBox"></div>`;
  wireRange("prRange", async (f, t) => {
    try {
      const r = await api(`/api/reports/profit?from=${f}&to=${t}`), sub = rangeText(f, t);
      $("#prBox").innerHTML = `<section class="panel" id="prRep"><div class="row between"><h2>${esc(sub)}</h2>${reportTools("profit")}</div><div class="totals">
        <div>المبيعات (بعد المرتجع)<b>${money(r.revenue)}</b><span>${r.invoices} فاتورة · خصومات ${money(r.discount)}${r.claim_discounts ? ` · خصم المطالبات: ${money(r.claim_discounts)}` : ""}</span></div>
        <div>تكلفة البضاعة المباعة<b>${money(r.cost)}</b></div>
        <div class="${r.gross_profit >= 0 ? "t-ok" : "t-err"}">مجمل الربح<b>${money(r.gross_profit)}</b>${r.gross_margin_pct != null ? `<span>${r.gross_margin_pct}%</span>` : ""}</div>
        <div>المصروفات المعتمدة<b>${money(r.expenses_total)}</b></div>
        ${r.supplier_discounts ? `<div class="t-ok">خصومات مكتسبة من الموردين<b>${money(r.supplier_discounts)}</b></div>` : ""}
        ${r.cash_rounding ? `<div>تقريب النقدي (مش مبيعات ولا ربح)<b>${money(r.cash_rounding)}</b></div>` : ""}
        <div class="${r.net_profit >= 0 ? "t-ok" : "t-err"}">${r.net_profit >= 0 ? "✓ صافي الربح" : "✕ صافي خسارة"}<b>${money(Math.abs(r.net_profit))}</b></div></div>
        ${r.pending_expenses.n ? `<p class="badge b-warn mt">⚠ يوجد ${r.pending_expenses.n} مصروف (${money(r.pending_expenses.a)} ج) بانتظار الموافقة وغير محتسب — <a href="#/expenses">راجعها</a></p>` : ""}
        ${r.unknown_cost_lines ? `<p class="badge b-warn mt">⚠ ${r.unknown_cost_lines} سطر بيع بدون تكلفة مسجلة — قد يكون الربح الحقيقي أقل</p>` : ""}
        ${r.delivery_fees ? `<p class="muted">رسوم توصيل: ${money(r.delivery_fees)} ج (محسوبة في مجمل الربح)</p>` : ""}
        <h2 class="mt">المصروفات حسب البند</h2><table><tbody>${r.expenses.map((e) => `<tr><td>${esc(e.category)}</td><td class="num">${money(e.amount)}</td></tr>`).join("") || `<tr><td class="muted">لا يوجد</td></tr>`}</tbody></table>
        <p class="muted">الرواتب المصروفة من شاشة الرواتب تُسجَّل هنا مصروفًا "رواتب وأجور".</p></section>`;
      wireTools("profit", () => ({ title: "تقرير الربح", subtitle: sub, el: $("#prRep"), sheets: [
        { name: "الملخص", columns: [Tx("البيان"), M("المبلغ")], rows: [["المبيعات بعد المرتجع", r.revenue], ["الخصومات", r.discount], ["تكلفة البضاعة", r.cost],
          ["مجمل الربح", r.gross_profit], ["المصروفات المعتمدة", r.expenses_total], ["خصومات مكتسبة من الموردين", r.supplier_discounts || 0],
          ["خصم المطالبات", r.claim_discounts || 0], ["صافي الربح", r.net_profit], ["رسوم التوصيل", r.delivery_fees || 0]] },
        { name: "المصروفات", columns: [Tx("البند"), M("المبلغ")], rows: r.expenses.map((e) => [e.category, e.amount]), total: ["الإجمالي", r.expenses_total] }] }));
    } catch (err) { $("#prBox").innerHTML = `<p class="badge b-err">✕ ${esc(err.message)}</p>`; }
  });
}

// ------------------------------------------------------------------ home
async function viewHome() {
  const [h, att] = await Promise.all([api("/api/home"), api("/api/attendance")]);
  const lvl = { error: ["b-err", "✕"], warning: ["b-warn", "⚠"], info: ["b-info", "ℹ"] };
  setTimeout(() => $("#attBtn")?.addEventListener("click", async () => {
    try { await api("/api/attendance", { method: "POST", body: { action: att.open ? "out" : "in" } }); toast(att.open ? "✓ تم تسجيل الانصراف" : "✓ تم تسجيل الحضور"); viewHome(); }
    catch (err) { toast(err.message, true); }
  }), 0);
  const can = (p) => ME.permissions.includes(p);
  const diff = h.sales && h.sales_week_ago ? Math.round((h.sales.total - h.sales_week_ago) / h.sales_week_ago * 100) : null;
  app.innerHTML = `<section class="panel"><h1>السلام عليكم يا ${esc(ME.user.full_name)}</h1>
    <div class="quick">
      ${(ME.ui?.home || []).filter(canScreen).map((k, i) => `<a class="btn ${i === 0 ? "big" : "secondary"}" href="#/${k}">${esc(SCREENS[k][0])}</a>`).join("")}
      ${ME.role !== "owner" ? `<button class="btn ${att.open ? "danger" : ""}" id="attBtn">${att.open ? "سجّل انصراف" : "سجّل حضور"}</button>
        <span class="muted">${att.open ? `حاضر من ${esc(hhmm(att.open.check_in))}` : ""} · <a href="#/attendance">حضوري هذا الشهر (${att.days_this_month} يوم)</a></span>` : ""}</div></section>
  <div class="totals">
    ${h.sales ? `<div>مبيعات اليوم<b>${money(h.sales.total)}</b><span>${h.sales.n} فاتورة${diff != null ? ` · ${diff >= 0 ? "▲" : "▼"} ${Math.abs(diff)}% عن اليوم نفسه من الأسبوع الماضي` : ""}</span></div>` : ""}
    <div>فواتيرك اليوم<b>${h.mine.n}</b><span>${money(h.mine.t)} ج</span></div>
    <div class="${h.shift_open ? "t-ok" : "t-warn"}">${h.shift_open ? "✓ الشيفت مفتوح" : "⚠ الشيفت مغلق"}<b>${h.shift_open ? "شغّال" : "افتحه"}</b></div>
    ${h.backup ? `<div class="${!h.backup.last_at && h.backup.ok == null ? "t-warn" : h.backup.warn ? "t-err" : "t-ok"}">${!h.backup.last_at && h.backup.ok == null ? "⚠ لا توجد نسخة تلقائية بعد" : h.backup.failed ? "✕ النسخة الاحتياطية فشلت" : h.backup.late ? "⚠ النسخة الاحتياطية متأخرة" : "✓ آخر نسخة احتياطية"}<b>${h.backup.last_good_at ? esc(stamp(h.backup.last_good_at)) : "—"}</b><span>${h.backup.offsite_error ? "⚠ النسخة خارج الجهاز بها مشكلة" : h.backup.warn ? `<a href="#/system">افتح «البرنامج والنسخ الاحتياطي»</a>` : ""}</span></div>` : ""}
  </div>
  <section class="panel mt"><h2>يحتاج إلى انتباهك</h2>
    <ul class="notes">${h.alerts.map((a) => `<li><span class="badge ${lvl[a.level][0]}">${lvl[a.level][1]}</span><div>${esc(a.text)} ${a.link ? `<a href="${a.link}">افتح</a>` : ""}</div></li>`).join("")
      || `<li><span class="badge b-ok">✓</span><div>كل شيء سليم</div></li>`}</ul>
    ${h.notifications_unread ? `<p class="mt"><a href="#/notifications">🔔 ${h.notifications_unread} إشعار جديد</a></p>` : ""}</section>`;
}

// ------------------------------------------------------------------ reports
// ------------------------------------------------------------------ periods, Excel export, print preview
const isoDay = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
// the pharmacy's calendar day (Cairo), whatever the device clock's zone; days are counted on the calendar, never as
// 24 hours, so the night the clocks go back has a yesterday (#324 R24). Noon local keeps isoDay() on that date.
function cairoDay() {
  try {
    const [y, m, d] = new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Cairo", year: "numeric", month: "2-digit", day: "2-digit" })
      .format(new Date()).split("-").map(Number);
    return [y, m - 1, d];
  } catch { const n = new Date(); return [n.getFullYear(), n.getMonth(), n.getDate()]; }
}
function periodPresets() {
  const [y, m, d] = cairoDay(), at = (yy, mm, dd) => new Date(yy, mm, dd, 12), now = at(y, m, d);
  return {
    today: ["اليوم", now, now], yesterday: ["أمس", at(y, m, d - 1), at(y, m, d - 1)],
    week: ["آخر 7 أيام", at(y, m, d - 6), now], month: ["هذا الشهر", at(y, m, 1), now],
    last: ["الشهر الماضي", at(y, m - 1, 1), at(y, m, 0)], year: ["هذه السنة", at(y, 0, 1), now],
  };
}
// a "from — to" bar with quick choices; onChange(from, to) runs on every change
function rangeBar(id, preset = "month") {
  const p = periodPresets(), [, a, z] = p[preset];
  return `<div class="row range noprint" id="${id}"><div class="seg2 small">${Object.entries(p).map(([k, [t]]) => `<button type="button" data-rp="${k}" class="${k === preset ? "on" : ""}">${t}</button>`).join("")}</div>
    <label class="f">من <input type="date" data-rf value="${isoDay(a)}"></label><label class="f">إلى <input type="date" data-rt value="${isoDay(z)}"></label>
    <button type="button" class="btn small secondary" data-rgo>اعرض</button></div>`;
}
function wireRange(id, onChange) {
  const box = $(`#${id}`), f = box.querySelector("[data-rf]"), t = box.querySelector("[data-rt]");
  // each choice is numbered; the reads it starts carry the number, and an answer for an older choice is dropped (#324 R25)
  const go = () => {
    if (!(f.value && t.value)) return;
    const seq = (RANGE_SEQ[id] = (RANGE_SEQ[id] || 0) + 1);
    RANGE_CALL = { id, seq };
    try { onChange(f.value, t.value); } finally { RANGE_CALL = null; }
  };
  box.querySelectorAll("[data-rp]").forEach((b) => b.addEventListener("click", () => {
    const [, a, z] = periodPresets()[b.dataset.rp];
    box.querySelectorAll("[data-rp]").forEach((x) => x.classList.toggle("on", x === b));
    f.value = isoDay(a); t.value = isoDay(z); go();
  }));
  box.querySelector("[data-rgo]").addEventListener("click", () => { box.querySelectorAll("[data-rp]").forEach((x) => x.classList.remove("on")); go(); });
  go();
  return () => [f.value, t.value];
}
// two buttons for any report: Excel file and print preview (the print dialog also saves PDF)
function reportTools(key) {
  return `<span class="row noprint"><button type="button" class="btn small secondary" data-xl="${key}">⬇ استخراج ملف Excel</button>
    <button type="button" class="btn small secondary" data-pv="${key}">🖨 معاينة الطباعة / PDF</button></span>`;
}
// sheets: [{name, columns: [{label, type: text|money|int|num|date}], rows: [[...]], total?: [...]}]
async function exportXlsx(filename, subtitle, sheets) {
  if (I18N.on) {                                   // English screen -> English file headings
    const T = (x) => (typeof x === "string" ? trString(x) ?? x : x);
    filename = T(filename); subtitle = T(subtitle);
    sheets = sheets.map((sh) => ({ ...sh, name: T(sh.name), columns: sh.columns.map((c) => ({ ...c, label: T(c.label) })),
      rows: sh.rows.map((r) => r.map(T)), total: sh.total?.map(T) }));
  }
  try {
    const r = await fetch("/api/export/xlsx", { method: "POST", credentials: "same-origin",
      headers: { "X-Requested-With": "pharm", "Content-Type": "application/json" },
      body: JSON.stringify({ filename, title: `${ME.organization.name} — ${filename}`, subtitle, sheets }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.error?.message || "فشل التصدير");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob()); a.download = `${filename.replace(/[\\/:*?"<>|]/g, " ")}.xlsx`;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 3000);
  } catch (err) { toast(err.message, true); }
}
// prints only `el` (a copy) with a header: pharmacy, report, period, who and when
function printPreview(el, title, subtitle = "") {
  let area = $("#printArea");
  if (!area) { area = document.createElement("div"); area.id = "printArea"; document.body.append(area); }
  const now = new Date().toLocaleString(LOC(), { dateStyle: "medium", timeStyle: "short", hour12: H12() });
  area.innerHTML = `<header class="print-head"><b>${esc(ME.organization.name)}</b><h1>${esc(title)}</h1>${subtitle ? `<p>${esc(subtitle)}</p>` : ""}
    <small>طُبع ${esc(now)} — ${esc(ME.user.full_name)}</small></header>`;
  const copy = el.cloneNode(true);
  copy.querySelectorAll(".noprint, button, input[type=checkbox], form").forEach((x) => x.remove());
  area.append(copy);
  document.body.classList.add("printing");
  const done = () => { document.body.classList.remove("printing"); area.innerHTML = ""; window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  window.print();
}
// wire both buttons of a reportTools(key): spec() returns {title, subtitle, el, sheets}
function wireTools(key, spec) {
  const get = () => { try { return spec(); } catch { toast("انتظر حتى يكتمل تحميل التقرير", true); return null; } };
  document.querySelectorAll(`[data-xl="${key}"]`).forEach((b) => b.addEventListener("click", () => { const s = get(); if (s) exportXlsx(s.title, s.subtitle, s.sheets); }));
  document.querySelectorAll(`[data-pv="${key}"]`).forEach((b) => b.addEventListener("click", () => { const s = get(); if (s) printPreview(s.el, s.title, s.subtitle); }));
}
const M = (label) => ({ label, type: "money" }), N = (label) => ({ label, type: "int" }), Tx = (label) => ({ label }), Dt = (label) => ({ label, type: "date" });
const rangeText = (f, t) => (f === t ? `يوم ${f}` : `من ${f} إلى ${t}`);

// ------------------------------------------------------------------ owner reports (من تاريخ لتاريخ)
const Pc = (label) => ({ label, type: "num" });
const pctTxt = (bp) => (bp == null ? "—" : `${(bp / 100).toFixed(1)}%`);
const stamp = (iso) => (iso ? `${dayOf(iso)} ${hhmm(iso)}` : "");
const itq = (r) => ({ pack_size: r.pack_size, base_unit_name: r.base_unit_name });
const OWNER_REPORTS = [
  { key: "staff", title: "أداء الموظفين", perm: "sales.totals",
    html: (r) => `<div class="tablewrap"><table><thead><tr><th>الموظف</th><th>الفواتير</th><th>المبيعات</th><th>متوسط الفاتورة</th><th>أصناف/فاتورة</th>
      <th>الخصم</th><th>نسبة الخصم</th><th>مرتجعات سجّلها</th><th>مرتجعات من فواتيره</th><th>فواتير ألغاها</th><th>شيفتات أغلقها</th>${r.see_cash ? "<th>فرق الخزينة</th>" : ""}
      <th>تحذيرات أكّدها</th><th>تجاوز سلامة</th><th>ساعات الحضور</th><th>مبيعات/ساعة</th></tr></thead><tbody>
      ${r.rows.map((x) => `<tr><td><b>${esc(x.name)}</b> <span class="muted">${esc(x.role || "")}</span></td><td>${x.n}</td><td class="num"><b>${money(x.total)}</b></td>
        <td class="num">${money(x.avg)}</td><td>${x.items_per_sale}</td><td class="num">${money(x.discount)}</td><td>${pctTxt(x.discount_bp)}</td>
        <td class="num">${x.returns_n ? `${x.returns_n} · ${money(x.returns)}` : "—"}</td><td class="num">${x.returned_on_his ? money(x.returned_on_his) : "—"}</td>
        <td class="num">${x.voids_n ? `${x.voids_n} · ${money(x.voids)}` : "—"}</td><td>${x.shifts || "—"}</td>
        ${r.see_cash ? `<td class="num">${x.difference ? `<span class="badge ${x.difference < 0 ? "b-err" : "b-warn"}">${money(x.difference)}</span>${x.short_n ? ` <span class="muted">(${x.short_n} عجز)</span>` : ""}` : "—"}</td>` : ""}
        <td>${x.warnings || "—"}</td><td>${x.overrides || "—"}</td><td>${x.hours ?? "—"}</td><td class="num">${x.per_hour != null ? money(x.per_hour) : "—"}</td></tr>`).join("")
        || `<tr><td colspan="16" class="muted">لا يوجد عمل في هذه الفترة</td></tr>`}</tbody></table></div>
      <p class="muted">"مرتجعات من فواتيره" = ما أُرجع من فواتير باعها هو (أيًّا كان من سجّل الإرجاع). ساعات الحضور من شاشة الحضور. الفرق السالب = عجز في الخزينة.</p>`,
    sheets: (r) => [{ name: "أداء الموظفين", columns: [Tx("الموظف"), N("الفواتير"), M("المبيعات"), M("متوسط الفاتورة"), Pc("أصناف/فاتورة"), M("الخصم"), Pc("نسبة الخصم %"),
      N("مرتجعات سجّلها"), M("قيمتها"), M("مرتجعات من فواتيره"), N("فواتير ألغاها"), M("قيمتها"), N("شيفتات أغلقها"), ...(r.see_cash ? [M("فرق الخزينة")] : []), N("تحذيرات أكّدها"), N("تجاوز سلامة"), Pc("ساعات الحضور")],
      rows: r.rows.map((x) => [x.name, x.n, x.total, x.avg, x.items_per_sale, x.discount, x.discount_bp / 100, x.returns_n, x.returns, x.returned_on_his, x.voids_n, x.voids, x.shifts, ...(r.see_cash ? [x.difference] : []), x.warnings, x.overrides, x.hours]) }] },
  { key: "customers", title: "مبيعات العملاء", perm: "sales.totals",
    html: (r) => `<div class="totals"><div>عملاء اشتروا<b>${r.totals.customers}</b></div><div>صافي المبيعات<b>${money(r.totals.net)}</b></div>
      <div>بدون عميل<b>${money(r.walk_in ? r.walk_in.total - r.walk_in.returns : 0)}</b><span>${r.walk_in ? `${r.walk_in.n} فاتورة` : ""}</span></div><div>آجل في الفترة<b>${money(r.totals.credit)}</b></div></div>
      <div class="tablewrap mt"><table><thead><tr><th>العميل</th><th>الفواتير</th><th>قبل الخصم</th><th>الخصم</th><th>المرتجع</th><th>الصافي</th><th>متوسط الفاتورة</th><th>آجل</th><th>آخر شراء</th><th>رصيده الآن</th></tr></thead><tbody>
      ${r.rows.map((x) => `<tr><td><a href="#/customers/${esc(x.customer_id)}">${esc(x.name)}</a> <span class="muted">${esc(x.phone || "")}</span></td><td>${x.n}</td><td class="num">${money(x.gross)}</td>
        <td class="num">${money(x.discount)}</td><td class="num">${money(x.returns)}</td><td class="num"><b>${money(x.net)}</b></td><td class="num">${money(x.avg)}</td>
        <td class="num">${money(x.credit)}</td><td>${esc(dayOf(x.last_at))}</td><td class="num">${x.balance ? `<span class="badge b-warn">${money(x.balance)}</span>` : "0.00"}</td></tr>`).join("")
        || `<tr><td colspan="10" class="muted">لا توجد فواتير على عملاء في هذه الفترة</td></tr>`}</tbody></table></div>`,
    sheets: (r) => [{ name: "مبيعات العملاء", columns: [Tx("العميل"), Tx("الهاتف"), N("الفواتير"), M("قبل الخصم"), M("الخصم"), M("المرتجع"), M("الصافي"), M("آجل"), Dt("آخر شراء"), M("رصيده الآن")],
      rows: [...r.rows.map((x) => [x.name, x.phone || "", x.n, x.gross, x.discount, x.returns, x.net, x.credit, dayOf(x.last_at), x.balance]),
        ...(r.walk_in ? [["بدون عميل", "", r.walk_in.n, r.walk_in.gross, r.walk_in.discount, r.walk_in.returns, r.walk_in.net, 0, "", null]] : [])],
      total: ["الإجمالي", "", r.totals.n, r.totals.gross, r.totals.discount, r.totals.returns, r.totals.net, r.totals.credit, "", null] }] },
  { key: "shifts", title: "إغلاقات الخزينة", perm: "cash.see_expected",
    html: (r) => `<div class="totals"><div>شيفتات أُغلقت<b>${r.totals.n}</b></div><div class="${r.totals.short ? "t-err" : ""}">العجز<b>${money(r.totals.short)}</b><span>${r.totals.short_n} <span>شيفت</span></span></div>
      <div>الزيادة<b>${money(r.totals.over)}</b><span>${r.totals.over_n} <span>شيفت</span></span></div><div>الصافي<b>${money(r.totals.net)}</b></div></div>
      <div class="tablewrap mt"><table><thead><tr><th>فُتحت</th><th>أُغلقت</th><th>فتحها</th><th>أغلقها</th><th>البداية</th><th>فواتير</th><th>بيع نقدي</th><th>مرتجع/إلغاء</th><th>مصروف من الخزينة</th><th>المتوقع</th><th>المعدود</th><th>الفرق</th><th>السبب</th></tr></thead><tbody>
      ${r.rows.map((x) => `<tr><td>${esc(stamp(x.opened_at))}</td><td>${esc(stamp(x.closed_at))}</td><td>${esc(x.opened_by_name || "")}</td><td>${esc(x.closed_by_name || "")}</td>
        <td class="num">${money(x.opening_cash)}</td><td>${x.sales_n}</td><td class="num">${money(x.cash_sales)}</td><td class="num">${money(x.refunds)}</td><td class="num">${money(x.payouts)}</td>
        <td class="num">${money(x.expected_cash)}</td><td class="num">${money(x.counted_cash)}</td>
        <td class="num">${x.difference ? `<span class="badge ${x.difference < 0 ? "b-err" : "b-warn"}">${money(x.difference)}</span>` : `<span class="badge b-ok">✓ مطابق</span>`}</td><td>${esc(x.close_note || "")}</td></tr>`).join("")
        || `<tr><td colspan="13" class="muted">لا توجد شيفتات أُغلقت في هذه الفترة</td></tr>`}</tbody></table></div>`,
    sheets: (r) => [{ name: "إغلاقات الخزينة", columns: [Tx("فُتحت"), Tx("أُغلقت"), Tx("فتحها"), Tx("أغلقها"), M("البداية"), N("فواتير"), M("بيع نقدي"), M("مرتجع/إلغاء"), M("مصروف من الخزينة"), M("المتوقع"), M("المعدود"), M("الفرق"), Tx("السبب")],
      rows: r.rows.map((x) => [stamp(x.opened_at), stamp(x.closed_at), x.opened_by_name || "", x.closed_by_name || "", x.opening_cash, x.sales_n, x.cash_sales, x.refunds, x.payouts, x.expected_cash, x.counted_cash, x.difference, x.close_note || ""]),
      total: ["الإجمالي", "", "", "", null, null, null, null, null, null, null, r.totals.net, ""] }] },
  { key: "returns", title: "المرتجعات", perm: "sales.totals",
    html: (r) => `<div class="totals"><div>المرتجعات<b>${money(r.totals.total)}</b><span>${r.totals.n} <span>مرة</span></span></div><div>من المبيعات<b>${pctTxt(r.totals.pct_bp)}</b></div>
      ${Object.entries(r.totals.by_method).map(([m, v]) => `<div>${esc({ cash: "نقدي", card: "بطاقة", credit: "على الحساب", transfer: "المحفظة الأصلية" }[m] || m)}<b>${money(v)}</b></div>`).join("")}
      <div class="${r.totals.late_n ? "t-err" : ""}">بعد مدة المرتجع<b>${r.totals.late_n}</b><span><span>المدة</span> ${r.window_days} <span>يوم</span></span></div><div>فاتورة شخص آخر<b>${r.totals.other_n}</b></div></div>
      <div class="tablewrap mt"><table><thead><tr><th>الوقت</th><th>الفاتورة</th><th>العميل</th><th>الأصناف</th><th>المبلغ</th><th>طريقة الاسترداد</th><th>السبب</th><th>سجّل الإرجاع</th><th>باعه</th><th></th></tr></thead><tbody>
      ${r.rows.map((x) => `<tr><td>${esc(stamp(x.at))}</td><td><a href="#/sales/${esc(x.sale_id)}">${x.sale_no}</a></td><td>${esc(x.customer || "—")}</td>
        <td>${x.items.map((i) => `${itemHtml(i)} × ${esc(fmtStock(i.qty, itq(i)))}`).join("<br>")}</td><td class="num"><b>${money(x.total)}</b></td><td>${esc(x.method_label)}</td><td>${esc(x.reason)}</td>
        <td>${esc(x.by_name || "")}</td><td>${esc(x.sold_by_name || "")}</td><td>${x.late ? `<span class="badge b-err">بعد ${x.age_days} يوم</span>` : ""}${x.other_seller ? ` <span class="badge b-warn">فاتورة شخص آخر</span>` : ""}</td></tr>`).join("")
        || `<tr><td colspan="10" class="muted">✓ لا توجد مرتجعات في هذه الفترة</td></tr>`}</tbody></table></div>
      <p class="muted">كل مرتجع بتاريخ إرجاعه، حتى لو كانت الفاتورة من فترة سابقة.</p>`,
    sheets: (r) => [{ name: "المرتجعات", columns: [Tx("الوقت"), N("الفاتورة"), Tx("العميل"), Tx("الأصناف"), M("المبلغ"), Tx("طريقة الاسترداد"), Tx("السبب"), Tx("سجّل الإرجاع"), Tx("باعه"), N("بعد كم يوم")],
      rows: r.rows.map((x) => [stamp(x.at), x.sale_no, x.customer || "", x.items.map((i) => `${i.name_en || i.name_ar} × ${i.qty}`).join(" / "), x.total, x.method_label, x.reason, x.by_name || "", x.sold_by_name || "", x.age_days]),
      total: ["الإجمالي", null, "", "", r.totals.total, "", "", "", "", null] }] },
  { key: "discounts", title: "الخصومات", perm: "sales.totals",
    html: (r) => `<div class="totals"><div>نسبة الخصم من المبيعات<b>${pctTxt(r.totals.discount_bp)}</b></div><div>الخصم<b>${money(r.totals.discount)}</b></div>
      <div>قبل الخصم<b>${money(r.totals.gross)}</b></div><div>فواتير عليها خصم<b>${r.totals.n_disc}</b><span><span>من أصل</span> ${r.totals.n}</span></div></div>
      <div class="grid2 mt"><section class="panel"><h2>من منح الخصم</h2><table><thead><tr><th></th><th>أسطر</th><th>قبل الخصم</th><th>الخصم</th><th>النسبة</th><th>بموافقة</th></tr></thead><tbody>
        ${r.by_who.map((g) => `<tr><td>${esc(g.name)}</td><td>${g.lines}</td><td class="num">${money(g.gross)}</td><td class="num"><b>${money(g.discount)}</b></td><td>${pctTxt(g.discount_bp)}</td><td>${g.approved_lines || "—"}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">لا يوجد</td></tr>`}</tbody></table></section>
      <section class="panel"><h2>يوم بيوم</h2><table><thead><tr><th>اليوم</th><th>فواتير</th><th>قبل الخصم</th><th>الخصم</th><th>النسبة</th></tr></thead><tbody>
        ${r.daily.map((d) => `<tr><td>${esc(d.day)}</td><td>${d.n}</td><td class="num">${money(d.gross)}</td><td class="num">${money(d.discount)}</td><td>${pctTxt(d.discount_bp)}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">لا يوجد</td></tr>`}</tbody></table></section></div>
      <h2 class="mt">الفواتير التي عليها خصم (الأكبر الأول)</h2><div class="tablewrap"><table><thead><tr><th>الفاتورة</th><th>الوقت</th><th>العميل</th><th>باعها</th><th>قبل الخصم</th><th>الخصم</th><th>النسبة</th><th>الصافي</th></tr></thead><tbody>
        ${r.rows.map((x) => `<tr><td><a href="#/sales/${esc(x.id)}">${x.sale_no}</a></td><td>${esc(stamp(x.completed_at))}</td><td>${esc(x.customer || "—")}</td><td>${esc(x.by_name || "")}</td>
          <td class="num">${money(x.gross)}</td><td class="num"><b>${money(x.discount)}</b></td><td>${pctTxt(x.discount_bp)}</td><td class="num">${money(x.total)}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">لا يوجد</td></tr>`}</tbody></table></div>`,
    sheets: (r) => [
      { name: "من منح الخصم", columns: [Tx(""), N("أسطر"), M("قبل الخصم"), M("الخصم"), Pc("النسبة %"), N("بموافقة")], rows: r.by_who.map((g) => [g.name, g.lines, g.gross, g.discount, g.discount_bp / 100, g.approved_lines]) },
      { name: "يوم بيوم", columns: [Dt("اليوم"), N("فواتير"), M("قبل الخصم"), M("الخصم"), Pc("النسبة %")], rows: r.daily.map((d) => [d.day, d.n, d.gross, d.discount, d.discount_bp / 100]),
        total: ["الإجمالي", r.totals.n, r.totals.gross, r.totals.discount, r.totals.discount_bp / 100] },
      { name: "الفواتير", columns: [N("الفاتورة"), Tx("الوقت"), Tx("العميل"), Tx("باعها"), M("قبل الخصم"), M("الخصم"), Pc("النسبة %"), M("الصافي")],
        rows: r.rows.map((x) => [x.sale_no, stamp(x.completed_at), x.customer || "", x.by_name || "", x.gross, x.discount, x.discount_bp / 100, x.total]) }] },
  { key: "prices", title: "تغيير الأسعار", perm: "sales.totals",
    html: (r) => `<div class="totals"><div>تغييرات<b>${r.totals.n}</b><span>${r.totals.items} صنف</span></div><div>زيادة<b>${r.totals.up}</b></div><div>نقص<b>${r.totals.down}</b></div></div>
      <div class="tablewrap mt"><table><thead><tr><th>الوقت</th><th>الصنف</th><th>القديم</th><th>الجديد</th><th>التغيير</th><th>المصدر</th><th>بواسطة</th></tr></thead><tbody>
      ${r.rows.map((x) => `<tr><td>${esc(stamp(x.at))}</td><td><a href="#/items/${esc(x.item_id)}">${itemHtml(x)}</a></td><td class="num">${money(x.old)}</td><td class="num"><b>${money(x.new)}</b></td>
        <td><span class="badge ${x.change > 0 ? "b-warn" : "b-ok"}">${x.change > 0 ? "▲" : "▼"} ${pctTxt(Math.abs(x.change_bp))}</span></td>
        <td>${esc(x.source_label)}${x.invoice_no ? ` — فاتورة ${esc(x.invoice_no)} ${esc(x.supplier || "")}` : ""}</td><td>${esc(x.by_name || "—")}</td></tr>`).join("")
        || `<tr><td colspan="7" class="muted">لا يوجد تغيير أسعار في هذه الفترة</td></tr>`}</tbody></table></div>
      <p class="muted">سعر الجمهور للعبوة. التغييرات السابقة لهذا التحديث غير مسجل من قام بها.</p>`,
    sheets: (r) => [{ name: "تغيير الأسعار", columns: [Tx("الوقت"), Tx("الصنف"), M("القديم"), M("الجديد"), Pc("التغيير %"), Tx("المصدر"), Tx("بواسطة")],
      rows: r.rows.map((x) => [stamp(x.at), x.name_en || x.name_ar, x.old, x.new, x.change_bp / 100, `${x.source_label}${x.invoice_no ? ` — فاتورة ${x.invoice_no} ${x.supplier || ""}` : ""}`, x.by_name || ""]) }] },
  { key: "units", title: "تغيير وحدات البيع", perm: "sales.totals",
    html: (r) => `<div class="tablewrap"><table><thead><tr><th>الوقت</th><th>الصنف</th><th>ما تغيّر</th><th>بواسطة</th></tr></thead><tbody>
      ${r.rows.map((x) => `<tr><td>${esc(stamp(x.at))}</td><td><a href="#/items/${esc(x.item_id)}">${itemHtml(x)}</a></td>
        <td>${x.changes.map((c) => `${esc(c.label)}: <bdi>${esc(c.before ?? "—")}</bdi> ← <b><bdi>${esc(c.after ?? "—")}</bdi></b>`).join("<br>")}</td><td>${esc(x.by_name || "")}</td></tr>`).join("")
        || `<tr><td colspan="4" class="muted">لا يوجد تغيير في وحدات البيع في هذه الفترة</td></tr>`}</tbody></table></div>`,
    sheets: (r) => [{ name: "تغيير الوحدات", columns: [Tx("الوقت"), Tx("الصنف"), Tx("ما تغيّر"), Tx("بواسطة")],
      rows: r.rows.map((x) => [stamp(x.at), x.name_en || x.name_ar, x.changes.map((c) => `${c.label}: ${c.before ?? "—"} ← ${c.after ?? "—"}`).join(" / "), x.by_name || ""]) }] },
  { key: "unsold", title: "أصناف لم تُبع", perm: "sales.totals",
    html: (r) => `<div class="totals"><div>أصناف لها رصيد ولم تُبع<b>${r.totals.n}</b></div>${r.totals.cost_value != null ? `<div>قيمتها بالتكلفة<b>${money(r.totals.cost_value)}</b>${r.totals.unknown_cost ? `<span>${r.totals.unknown_cost} بدون تكلفة</span>` : ""}</div>` : ""}
      <div>قيمتها بسعر الجمهور<b>${money(r.totals.price_value)}</b></div></div>
      <div class="tablewrap mt"><table><thead><tr><th>الصنف</th><th>القسم</th><th>الرصيد</th>${r.totals.cost_value != null ? "<th>بالتكلفة</th>" : ""}<th>بسعر الجمهور</th><th>أقرب صلاحية</th><th>آخر بيع (أي وقت)</th><th>آخر دخول</th></tr></thead><tbody>
      ${r.rows.map((x) => `<tr><td><a href="#/items/${esc(x.item_id)}">${itemHtml(x)}</a></td><td>${esc(x.category || "—")}</td><td>${esc(fmtStock(x.qty, itq(x)))}</td>
        ${r.totals.cost_value != null ? `<td class="num">${x.cost_value != null ? money(x.cost_value) : "—"}</td>` : ""}<td class="num">${money(x.price_value)}</td>
        <td>${esc((x.expiry || "—").slice(0, 7))}</td><td>${x.last_sold ? esc(dayOf(x.last_sold)) : `<span class="badge b-warn">لم يُبع مطلقًا</span>`}</td><td>${esc(dayOf(x.last_in) || "—")}</td></tr>`).join("")
        || `<tr><td colspan="8" class="muted">✓ كل الأصناف التي لها رصيد بيعت في هذه الفترة</td></tr>`}</tbody></table></div>
      <p class="muted">الأصناف التي لها رصيد الآن ولم يُبع منها شيء في الفترة. الأعلى قيمة أولًا.</p>`,
    sheets: (r) => [{ name: "لم تُبع", columns: [Tx("الصنف"), Tx("القسم"), N("الرصيد بالوحدة"), ...(r.totals.cost_value != null ? [M("بالتكلفة")] : []), M("بسعر الجمهور"), Dt("أقرب صلاحية"), Dt("آخر بيع"), Dt("آخر دخول")],
      rows: r.rows.map((x) => [x.name_en || x.name_ar, x.category || "", x.qty, ...(r.totals.cost_value != null ? [x.cost_value] : []), x.price_value, x.expiry || "", dayOf(x.last_sold) || "", dayOf(x.last_in) || ""]),
      total: ["الإجمالي", "", null, ...(r.totals.cost_value != null ? [r.totals.cost_value] : []), r.totals.price_value, "", "", ""] }] },
];

// #202 «مطابقة الأرصدة»: every balance recomputed from the stock ledger, now; nothing is changed by this screen
const STOCK_KINDS = {
  batches: ["رصيد تشغيلة لا يساوي مجموع حركاتها", (r) => [r.batch_no || "بدون تشغيلة", r.ledger_qty ?? "لا توجد حركات", r.balance_qty ?? "لا يوجد رصيد"]],
  items: ["رصيد صنف لا يساوي مجموع حركاته", (r) => ["", r.ledger_qty, r.balance_qty]],
  negative: ["رصيد بالسالب", (r) => [r.batch_no || "بدون تشغيلة", "", r.qty]],
  foreign_batch: ["حركة على تشغيلة صنف آخر", (r) => [r.batch_no || "", "", ""]],
  sales: ["فاتورة بيع حركتها لا تساوي الفاتورة", (r) => [`فاتورة ${r.sale_no}`, r.moved, r.expected]],
};
const CHECK_REASONS = { startup: "تشغيل البرنامج", backup: "بعد النسخة الاحتياطية", manual: "من هذا التقرير" };
async function viewStockCheck() {
  if (!ME.permissions.includes("audit.read")) { app.innerHTML = `<section class="panel"><p>هذا التقرير للمالك والمدير.</p></section>`; return; }
  app.innerHTML = `<section class="panel"><div class="row between"><div><a href="#/reports">← التقارير</a><h1>مطابقة الأرصدة</h1></div>
    <span class="row noprint"><button type="button" class="btn" id="scRun">افحص الآن</button><button type="button" class="btn small secondary" id="scPrint">🖨 طباعة</button></span></div>
    <p class="muted">يحسب البرنامج رصيد كل صنف وكل تشغيلة من جديد من سجل حركات المخزون ويقارنه بالرصيد المعروض. يجري هذا الفحص تلقائيًا عند تشغيل البرنامج وبعد كل نسخة احتياطية. ولا يغيّر هذا التقرير أي رصيد.</p>
    <div id="scBox"><p class="muted">جارٍ الفحص…</p></div></section>`;
  const load = async () => {
    $("#scBox").innerHTML = `<p class="muted">جارٍ الفحص…</p>`;
    const r = await api("/api/reports/stock-check", { method: "POST", body: {} });
    const n = Object.values(r.counts).reduce((a, b) => a + b, 0);
    const when = (x) => stamp(x);                       // local day + the 12/24-hour setting (#206)
    let html = `<div class="totals"><div>الحالة<b><span class="badge ${r.ok ? "b-ok" : "b-err"}">${r.ok ? "✔ كل الأرصدة مطابقة" : `✖ ${n} اختلاف`}</span></b></div>
      <div>أصناف تم فحصها<b>${r.items_checked}</b></div><div>حركات مخزون<b>${r.movements}</b></div><div>وقت الفحص<b>${esc(when(r.checked_at))}</b></div></div>`;
    if (!r.ok) {
      html += `<p class="mt"><b>لم يُصلَّح أي رصيد تلقائيًا.</b> اطبع هذا التقرير واتبع الخطوات في «ماذا أفعل عند ظهور اختلاف» أدناه.</p>`;
      for (const [k, [title, cols]] of Object.entries(STOCK_KINDS)) {
        if (!r.counts[k]) continue;
        const ack = k === "sales" && ME.permissions.includes("settings.manage");
        html += `<h2 class="mt">${title} (${r.counts[k]})</h2><table><thead><tr><th>الصنف</th><th>الكود</th><th>التشغيلة / الفاتورة</th><th>${k === "sales" ? "خرج من المخزون" : "حسب الحركات"}</th><th>${k === "sales" ? "حسب الفاتورة" : "المعروض"}</th>${ack ? "<th class=\"noprint\">بعد الجرد</th>" : ""}</tr></thead><tbody>
          ${r[k].map((x) => { const [a, b, c] = cols(x); return `<tr class="has-err"><td>${esc(x.name || x.item_id)}</td><td>${esc(x.code || "")}</td><td>${esc(a)}</td><td>${esc(b)}</td><td>${esc(c)}</td>${ack ? `<td class="noprint"><input data-ackn="${esc(x.sale_id)}|${esc(x.item_id)}" placeholder="ملاحظة: جرد الصنف بتاريخ…" maxlength="300"> <button type="button" class="btn small" data-ack="${esc(x.sale_id)}|${esc(x.item_id)}" data-moved="${x.moved}" data-expected="${x.expected}">تمت المراجعة</button></td>` : ""}</tr>`; }).join("")}</tbody></table>
          ${ack ? `<p class="muted noprint">فاتورة البيع التي تختلف حركتها عن الفاتورة تبقى مختلفة في السجل دائمًا. صحّح الرف بجرد الصنف أولًا، ثم اضغط «تمت المراجعة» مع ملاحظة: يختفي هذا السطر وحده، ويعود إذا تغيّرت أرقامه.</p>` : ""}
          ${r.counts[k] > r[k].length ? `<p class="muted">معروض أول ${r[k].length} فقط.</p>` : ""}`;
      }
      html += `<section class="panel mt"><h2>ماذا أفعل عند ظهور اختلاف</h2><ol>
        <li>لا تعدّل أي رصيد يدويًا ولا تحذف البرنامج. سجل الحركات هو الأصل، وهو محفوظ.</li>
        <li>اطبع هذا التقرير وأرسله إلى الدعم، ومعه آخر نسخة احتياطية سليمة.</li>
        ${r.counts.batches ? `<li>تتوقف النسخ الاحتياطية حتى يُحل اختلاف أرصدة التشغيلات، حتى لا تُحفظ بيانات بها مشكلة. والنسخ القديمة السليمة موجودة.</li>` : `<li>النسخ الاحتياطية تعمل كالمعتاد، لأن هذا الاختلاف ليس في أرصدة التشغيلات.</li>`}
        <li>يجري الإصلاح بأمر مسجّل من الدعم بعد مراجعة السبب، ثم افتح التقرير مرة أخرى: يجب أن يظهر «كل الأرصدة مطابقة».</li></ol></section>`;
    }
    if (r.history.length) {
      html += `<h2 class="mt">آخر الفحوص التي وجدت اختلافًا أو عادت سليمة</h2><table><thead><tr><th>الوقت</th><th>النتيجة</th><th>الفحص</th><th>بواسطة</th></tr></thead><tbody>
        ${r.history.map((h) => `<tr${h.ok || h.ack ? "" : ` class="has-err"`}><td>${esc(when(h.at))}</td><td>${h.ack ? `مراجعة فاتورة ${esc(h.sale_no)}: ${esc(h.note)}` : h.ok ? "مطابقة" : `${Object.values(h.counts || {}).reduce((a, b) => a + b, 0)} اختلاف`}</td><td>${h.ack ? "" : esc(CHECK_REASONS[h.reason] || h.reason || "")}</td><td>${esc(h.who)}</td></tr>`).join("")}</tbody></table>`;
    }
    $("#scBox").innerHTML = html;
    $("#scBox").querySelectorAll("[data-ack]").forEach((btn) => btn.addEventListener("click", async () => {
      const [sale_id, item_id] = btn.dataset.ack.split("|");
      const note = $("#scBox").querySelector(`[data-ackn="${CSS.escape(btn.dataset.ack)}"]`).value;
      btn.disabled = true;
      try { await api("/api/reports/stock-check/ack", { method: "POST", body: { sale_id, item_id, note, moved: Number(btn.dataset.moved), expected: Number(btn.dataset.expected) } }); await load(); }
      catch (e) { btn.disabled = false; toast(e.message, true); }
    }));
    refreshStockAlert();
  };
  $("#scRun").onclick = () => load().catch((e) => toast(e.message, true));
  $("#scPrint").onclick = () => window.print();
  await load();
}
async function viewOwnerReports() {
  const list = OWNER_REPORTS.filter((x) => ME.permissions.includes(x.perm));
  if (!list.length) { app.innerHTML = `<section class="panel"><p>هذه التقارير للمالك والمدير.</p></section>`; return; }
  let cur = (() => { try { return localStorage.getItem("ownerRep"); } catch { return null; } })();
  if (!list.some((x) => x.key === cur)) cur = list[0].key;
  app.innerHTML = `<section class="panel"><div class="row between"><div><a href="#/reports">← التقارير</a><h1>تقارير المالك</h1></div>${reportTools("own")}</div>
    <nav class="seg2 wrap noprint" aria-label="التقرير">${list.map((x) => `<button type="button" data-or="${x.key}" class="${x.key === cur ? "on" : ""}">${x.title}</button>`).join("")}</nav>
    ${rangeBar("orRange")}</section><section class="panel" id="orBox"></section>`;
  let R = null, F = null, T = null;
  const load = async () => {
    if (!F) return;
    const rep = list.find((x) => x.key === cur);
    $("#orBox").innerHTML = `<p class="muted">جارٍ التحميل…</p>`; R = null;
    try {
      const r = await api(`/api/reports/owner/${cur}?from=${F}&to=${T}`);
      if (rep.key !== cur) return;
      R = r;
      $("#orBox").innerHTML = `<h2><span>${rep.title}</span> — <span>${esc(rangeText(r.from, r.to))}</span></h2>${r.truncated ? `<p class="badge b-warn">⚠ أول 5000 سطر فقط — قلّل الفترة</p>` : ""}${rep.html(r)}`;
    } catch (err) { $("#orBox").innerHTML = ""; toast(err.message, true); }
  };
  document.querySelectorAll("[data-or]").forEach((b) => b.addEventListener("click", () => {
    cur = b.dataset.or; try { localStorage.setItem("ownerRep", cur); } catch { /* per-viewer only */ }
    document.querySelectorAll("[data-or]").forEach((x) => x.classList.toggle("on", x === b)); load();
  }));
  wireRange("orRange", (f, t) => { F = f; T = t; load(); });
  wireTools("own", () => { const rep = list.find((x) => x.key === cur); if (!R) throw new Error("loading");
    return { title: rep.title, subtitle: rangeText(R.from, R.to), el: $("#orBox"), sheets: rep.sheets(R) }; });
}

async function viewReports() {
  const can = (p) => ME.permissions.includes(p);
  app.innerHTML = `<section class="panel"><div class="row between"><h1>التقارير</h1><span class="row">${ME.permissions.includes("sales.totals") ? `<a class="btn" href="#/owner-reports">تقارير المالك: الموظفون، العملاء، الخزينة، المرتجعات، الخصومات، الأسعار…</a>` : ""}${ME.permissions.includes("cash.see_expected") && ME.permissions.includes("sales.totals") ? `<a class="btn secondary" href="#/day-review">مراجعة اليوم</a>` : ""}${ME.permissions.includes("costs.read") && ME.permissions.includes("sales.totals") ? `<a class="btn secondary" href="#/hikma-claim">مطالبة الحكمة</a>` : ""}${ME.permissions.includes("items.price") ? `<a class="btn secondary" href="#/item-health">فحص بيانات الأصناف</a>` : ""}${ME.permissions.includes("costs.read") ? `<a href="#/profit">تقرير الربح ←</a>` : ""}</span></div>
    ${can("sales.totals") ? rangeBar("rRange") : `<p class="muted">تقارير المبيعات للمدير والمالك.</p>`}
    <p class="muted noprint">تقارير أخرى: ${can("audit.read") ? `<a href="#/stock-check">مطابقة الأرصدة</a> · ` : ""}<a href="#/customers">العملاء</a> · <a href="#/sales">فواتير البيع</a> · <a href="#/expenses">المصروفات</a> · <a href="#/suppliers">مطالبات الموردين</a> · <a href="#/transfers">مراجعة التحويلات</a> · <a href="#/supplier-returns">مرتجعات للموردين</a> · <a href="#/counts">الجرد</a> · <a href="#/deadstock">الرواكد</a> · <a href="#/shortages">النواقص</a></p></section>
  <div id="rSales"></div>${can("sales.totals") ? `<section class="panel mt" id="rWarn"><div class="row between"><h2>تحذيرات الأصناف المؤكدة وقت البيع</h2>${reportTools("warn")}</div>${rangeBar("warnRange", "week")}<div id="warnBox"></div></section>` : ""}${can("costs.read") ? `<section class="panel mt" id="rLoss"><div class="row between"><h2>خسائر المخزون (منتهي، تالف، مفقود، تسويات الجرد) والمرتجع للموردين</h2>${reportTools("loss")}</div>${rangeBar("lossRange")}<div id="lossBox"></div></section>` : ""}<div id="rStock"></div>`;
  const it = (r) => ({ pack_size: r.pack_size, base_unit_name: r.base_unit_name });
  const sales = async (f, t) => {
    const r = await api(`/api/reports/sales?from=${f}&to=${t}`);
    const maxH = Math.max(1, ...r.by_hour.map((x) => x.total)), sub = rangeText(r.from, r.to);
    $("#rSales").innerHTML = `<section class="panel" id="rSalesBox"><div class="row between"><h2>المبيعات — ${esc(sub)}</h2>${reportTools("sales")}</div>
    <div class="totals"><div>الفواتير<b>${r.totals.n}</b></div><div>المبيعات<b>${money(r.totals.total)}</b></div>
      <div>المرتجع<b>${money(r.totals.returns)}</b></div><div>الخصومات<b>${money(r.totals.discount)}</b></div>${r.totals.rounding ? `<div>تقريب النقدي (مش مبيعات)<b>${money(r.totals.rounding)}</b></div>` : ""}</div>
    <div class="grid2 mt"><section class="panel"><h2>يوم بيوم</h2>
      <table><thead><tr><th>اليوم</th><th>فواتير</th><th>الإجمالي</th><th>نقدي</th><th>بطاقة</th><th>تحويل</th><th>آجل</th><th>مرتجع</th><th>تقريب النقدي</th></tr></thead><tbody>
      ${r.daily.map((d) => `<tr><td>${esc(d.day)}</td><td>${d.n}</td><td class="num"><b>${money(d.total)}</b></td><td class="num">${money(d.cash)}</td><td class="num">${money(d.card)}</td><td class="num">${money(d.transfer)}</td><td class="num">${money(d.credit)}</td><td class="num">${money(d.returns)}</td><td class="num">${money(d.rounding || 0)}</td></tr>`).join("") || `<tr><td colspan="9" class="muted">لا يوجد</td></tr>`}</tbody></table></section>
    <section class="panel"><h2>حسب الموظف</h2><table><tbody>${r.by_user.map((u) => `<tr><td>${esc(u.name)}</td><td>${u.n} فاتورة</td><td class="num">${money(u.total)}</td><td class="num">${u.discount ? `خصم ${money(u.discount)}` : ""}</td></tr>`).join("") || `<tr><td class="muted">لا يوجد</td></tr>`}</tbody></table>
      <h2 class="mt">ساعات العمل</h2><div class="hbars">${Array.from({ length: 24 }, (_, h) => { const x = r.by_hour.find((y) => y.hour === h); return `<div title="${hourLabel(h)} — ${x ? x.n : 0} فاتورة"><span data-h="${Math.round(((x?.total || 0) / maxH) * 100)}"></span><small>${hourLabel(h)}</small></div>`; }).join("")}</div></section></div>
    <section class="panel mt"><h2>أكثر الأصناف مبيعًا</h2><table><thead><tr><th>الصنف</th><th>الكمية</th><th>المبيعات</th>${can("costs.read") ? "<th>الربح</th>" : ""}</tr></thead><tbody>
      ${r.top_items.map((x) => `<tr><td>${itemHtml(x)}</td><td>${esc(fmtStock(x.qty, it(x)))}</td><td class="num">${money(x.revenue)}</td>${can("costs.read") ? `<td class="num">${x.profit != null ? money(x.profit) : "—"}</td>` : ""}</tr>`).join("") || `<tr><td colspan="4" class="muted">لا يوجد</td></tr>`}</tbody></table></section></section>`;
    document.querySelectorAll(".hbars [data-h]").forEach((el) => { el.style.height = `${el.dataset.h}%`; });
    estockReportBelow($("#rSalesBox"), r.from, r.to, r.totals.total).catch(() => {});   // U4: same basis, before returns
    wireTools("sales", () => ({ title: "تقرير المبيعات", subtitle: sub, el: $("#rSalesBox"), sheets: [
      { name: "يوم بيوم", columns: [Dt("اليوم"), N("فواتير"), M("الإجمالي"), M("نقدي"), M("بطاقة"), M("تحويل"), M("آجل"), M("مرتجع"), M("تقريب النقدي")],
        rows: r.daily.map((d) => [d.day, d.n, d.total, d.cash, d.card, d.transfer, d.credit, d.returns, d.rounding || 0]),
        total: ["الإجمالي", r.totals.n, r.totals.total, null, null, null, null, r.totals.returns, r.totals.rounding || 0] },
      { name: "حسب الموظف", columns: [Tx("الموظف"), N("فواتير"), M("الإجمالي"), M("الخصم")], rows: r.by_user.map((u) => [u.name, u.n, u.total, u.discount || 0]) },
      { name: "أكثر الأصناف", columns: [Tx("الصنف"), N("الكمية بالوحدة"), M("المبيعات"), ...(can("costs.read") ? [M("الربح")] : [])],
        rows: r.top_items.map((x) => [x.name_en || x.name_ar, x.qty, x.revenue, ...(can("costs.read") ? [x.profit] : [])]) },
      ...estockRepSheet(r.from, r.to)] }));
  };
  if (can("sales.totals")) wireRange("rRange", (f, t) => sales(f, t).catch((e) => toast(e.message, true)));
  if (can("sales.totals")) {
    let W = null;
    const WL = { item_rx: "📋 بوصفة طبية فقط", item_misuse: "⚠ يُساء استخدامه", item_note: "📝 ملاحظة" };
    wireRange("warnRange", async (f, t) => {
      try {
        W = await api(`/api/reports/item-warnings?from=${f}&to=${t}`);
        $("#warnBox").innerHTML = `<div class="totals">${Object.entries(WL).map(([k, v]) => `<div>${v}<b>${W.counts[k] || 0}</b></div>`).join("")}</div>
          <div class="tablewrap mt"><table><thead><tr><th>الوقت</th><th>التحذير</th><th>الصيدلي</th><th>ما كتبه</th><th>الفاتورة</th></tr></thead><tbody>
          ${W.rows.map((r) => `<tr><td>${esc(dayOf(r.at))} ${hhmm(r.at)}</td><td><span class="badge">${WL[r.finding_code]}</span> ${esc(r.message)}</td><td>${esc(r.by_name || "")}</td><td>${esc(r.reason || "")}</td>
            <td>${r.sale_no ? `<a href="#/sales/${esc(r.sale_id)}">${r.sale_no}</a>` : r.status === "draft" ? "ما زالت مفتوحة" : "—"}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">لا يوجد</td></tr>`}</tbody></table></div>`;
      } catch (err) { toast(err.message, true); }
    });
    wireTools("warn", () => ({ title: "تحذيرات الأصناف", subtitle: rangeText(W.from, W.to), el: $("#rWarn"), sheets: [
      { name: "التحذيرات", columns: [Tx("الوقت"), Tx("النوع"), Tx("التحذير"), Tx("الصيدلي"), Tx("ما كتبه"), N("الفاتورة")],
        rows: W.rows.map((r) => [`${dayOf(r.at)} ${hhmm(r.at)}`, WL[r.finding_code].slice(2), r.message, r.by_name || "", r.reason || "", r.sale_no]) }] }));
  }
  if (can("costs.read")) {
    let L = null;
    wireRange("lossRange", async (f, t) => {
      try {
        L = await api(`/api/reports/losses?from=${f}&to=${t}`);
        const it = (r) => ({ pack_size: r.pack_size, base_unit_name: r.base_unit_name });
        $("#lossBox").innerHTML = `<div class="totals"><div class="${L.total < 0 ? "t-err" : "t-ok"}">صافي الخسارة بالتكلفة<b>${money(-L.total)}</b></div>
          ${L.groups.map((g) => `<div>${esc(g.label)}<b>${money(-g.value)}</b><span>${g.qty_out ? `<span>صادر</span> ${g.qty_out} وحدة` : ""}${g.qty_in ? ` · <span>وارد</span> ${g.qty_in}` : ""}${g.unknown_cost ? ` · ${g.unknown_cost} بدون تكلفة` : ""}</span></div>`).join("")}</div>
          <div class="tablewrap mt"><table><thead><tr><th>اليوم</th><th>الصنف</th><th>التشغيلة</th><th>السبب</th><th>الكمية</th><th>القيمة</th><th>بواسطة</th></tr></thead><tbody>
          ${L.rows.map((r) => `<tr><td>${esc(dayOf(r.created_at))}</td><td>${itemHtml(r)}</td><td>${esc(r.batch_no || "")}${r.expiry_date ? ` <span class="muted">${esc(r.expiry_date.slice(0, 7))}</span>` : ""}</td>
            <td>${esc(r.reason_label)}${r.supplier ? ` — ${esc(r.supplier)}` : ""}</td><td>${r.qty_delta > 0 ? "+" : "−"}${esc(fmtStock(Math.abs(r.qty_delta), it(r)))}</td><td class="num">${r.value != null ? money(-r.value) : "—"}</td><td>${esc(r.by_name || "")}</td></tr>`).join("")
            || `<tr><td colspan="7" class="muted">✓ لا توجد خسائر في هذه الفترة</td></tr>`}</tbody></table></div>
          <p class="muted">القيمة بمتوسط التكلفة وقت التسوية. المرتجع للمورد ليس خسارة (تُخصم قيمته من حسابه)، ويظهر هنا لاكتمال الصورة.</p>`;
      } catch (err) { toast(err.message, true); }
    });
    wireTools("loss", () => ({ title: "خسائر المخزون", subtitle: rangeText(L.from, L.to), el: $("#rLoss"), sheets: [
      { name: "الملخص", columns: [Tx("السبب"), N("صادر (وحدة)"), N("وارد (وحدة)"), M("القيمة بالتكلفة")], rows: L.groups.map((g) => [g.label, g.qty_out, g.qty_in, -g.value]), total: ["صافي الخسارة", null, null, -L.total] },
      { name: "التفاصيل", columns: [Dt("اليوم"), Tx("الصنف"), Tx("التشغيلة"), Dt("الصلاحية"), Tx("السبب"), Tx("المورد"), N("الكمية بالوحدة"), M("القيمة"), Tx("بواسطة"), Tx("ملاحظة")],
        rows: L.rows.map((r) => [dayOf(r.created_at), r.name_en || r.name_ar, r.batch_no || "", r.expiry_date || "", r.reason_label, r.supplier || "", r.qty_delta, r.value != null ? -r.value : null, r.by_name || "", r.note || ""]) }] }));
  }
  if (can("inventory.read")) {
    const s = await api("/api/reports/stock");
    $("#rStock").innerHTML = `${s.value_by_category ? `<section class="panel mt" id="rValBox"><div class="row between"><h2>قيمة المخزون</h2>${reportTools("value")}</div><div class="totals"><div>بالتكلفة<b>${money(s.value_cost)}</b></div><div>بسعر الجمهور<b>${money(s.value_retail)}</b></div></div>
      <table class="mt"><thead><tr><th>القسم</th><th>أصناف</th><th>بالتكلفة</th><th>بسعر الجمهور</th></tr></thead><tbody>${s.value_by_category.map((c) => `<tr><td>${esc(c.category)}</td><td>${c.items}</td><td class="num">${money(c.cost)}</td><td class="num">${money(c.retail)}</td></tr>`).join("")}</tbody></table></section>` : ""}
    <section class="panel mt" id="rExpBox"><div class="row between"><h2>الصلاحية خلال 90 يومًا (${s.expiring.length})</h2>${reportTools("expiry")}</div>
      <table><thead><tr><th>الصنف</th><th>التشغيلة</th><th>الصلاحية</th><th>الكمية</th><th>القيمة (جمهور)</th></tr></thead><tbody>
      ${s.expiring.map((e) => `<tr class="clickable" data-go="${esc(e.item_id)}"><td>${itemHtml(e)}</td><td>${esc(e.batch_no || "—")}</td>
        <td><span class="badge ${e.days_left < 0 ? "b-err" : e.days_left <= 30 ? "b-warn" : "b-info"}">${e.days_left < 0 ? "✕ انتهت" : e.days_left <= 30 ? "⚠" : "ℹ"} ${esc(e.expiry_date.slice(0, 7))} (${e.days_left} يوم)</span></td>
        <td>${esc(fmtStock(e.qty, it(e)))}</td><td class="num">${money(e.value)}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">✓ لا يوجد</td></tr>`}</tbody></table>
      <p class="muted">ابدأ ببيع الأقرب، أو أرجعه للمورد قبل انتهائه إذا كان يقبل المرتجع.</p></section>`;
    document.querySelectorAll("#rStock [data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/items/${tr.dataset.go}`; }));
    const today = isoDay(new Date());
    wireTools("expiry", () => ({ title: "تقرير الصلاحية (90 يوم)", subtitle: `إلى ${today}`, el: $("#rExpBox"), sheets: [
      { name: "الصلاحية", columns: [Tx("الصنف"), Tx("التشغيلة"), Dt("الصلاحية"), N("باقي (يوم)"), N("الكمية بالوحدة"), Tx("الكمية"), M("القيمة (جمهور)")],
        rows: s.expiring.map((e) => [e.name_en || e.name_ar, e.batch_no || "", e.expiry_date, e.days_left, e.qty, fmtStock(e.qty, it(e)), e.value]) }] }));
    if (s.value_by_category) wireTools("value", () => ({ title: "قيمة المخزون", subtitle: today, el: $("#rValBox"), sheets: [
      { name: "قيمة المخزون", columns: [Tx("القسم"), N("أصناف"), M("بالتكلفة"), M("بسعر الجمهور")],
        rows: s.value_by_category.map((c) => [c.category, c.items, c.cost, c.retail]), total: ["الإجمالي", null, s.value_cost, s.value_retail] }] }));
  }
}

// ------------------------------------------------------------------ complaints and suggestions
const FB_KIND = { suggestion: "💡 اقتراح", complaint: "😕 شكوى", bug: "🐞 مشكلة في البرنامج" };
const FB_ST = { new: ["b-warn", "جديدة"], seen: ["b-info", "شوهدت"], done: ["b-ok", "✓ نُفّذت"] };
function openFeedback() {
  let dlg = $("#fbDlg");
  if (!dlg) { dlg = document.createElement("dialog"); dlg.id = "fbDlg"; document.body.append(dlg); }
  const screen = location.hash;
  dlg.innerHTML = `<form method="dialog" class="approve"><h2>ملاحظة على البرنامج</h2>
    <div class="seg">${Object.entries(FB_KIND).map(([k, t], i) => `<label><input type="radio" name="k" value="${k}" ${i === 0 ? "checked" : ""}> ${t}</label>`).join("")}</div>
    <label class="f mt">اكتب ما تريد <textarea name="t" rows="5" required minlength="5"></textarea></label>
    <p class="muted">تُسجَّل معها الشاشة التي كنت فيها. تُحفظ الملاحظات على الجهاز ويقرؤها المالك، وتُراجَع في جلسة التطوير.</p>
    <div class="row mt"><button class="btn" value="ok">إرسال</button><button class="btn secondary" value="cancel" formnovalidate>إلغاء</button></div></form>`;
  dlg.showModal();
  dlg.querySelector("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (e.submitter?.value === "cancel") { dlg.close(); return; }
    try { await api("/api/feedback", { method: "POST", body: { kind: e.target.k.value, text: e.target.t.value, screen } }); dlg.close(); toast("✓ تم التسجيل — شكرًا"); }
    catch (err) { toast(err.message, true); }
  });
}
async function viewFeedback() {
  const items = (await api("/api/feedback")).items, owner = ME.permissions.includes("users.manage");
  app.innerHTML = `<section class="panel"><div class="row between"><h1>الشكاوى والمقترحات</h1>
      <div class="row"><button class="btn" id="fbNew">+ ملاحظة جديدة</button>${owner && items.length ? `<button class="btn secondary" id="fbDl">نزّلها (لإرسالها إلى التطوير)</button>` : ""}</div></div>
    <ul class="notes">${items.map((f) => `<li><span class="badge ${FB_ST[f.status][0]}">${FB_ST[f.status][1]}</span><div class="grow"><b>${FB_KIND[f.kind]}</b> — ${esc(f.by_name || "")} · ${esc(stamp(f.created_at))}${f.screen ? ` · <span class="muted">${esc(f.screen)}</span>` : ""}
      <div>${esc(f.text)}</div>${f.reply ? `<div class="muted">الرد: ${esc(f.reply)}</div>` : ""}</div>
      ${owner ? `<select data-fb="${esc(f.id)}">${Object.entries(FB_ST).map(([k, [, t]]) => `<option value="${k}" ${f.status === k ? "selected" : ""}>${t}</option>`).join("")}</select>` : ""}</li>`).join("")
      || `<li class="muted">لا توجد ملاحظات بعد</li>`}</ul></section>`;
  $("#fbNew").addEventListener("click", () => { openFeedback(); $("#fbDlg").addEventListener("close", () => viewFeedback(), { once: true }); });
  document.querySelectorAll("[data-fb]").forEach((sel) => sel.addEventListener("change", async () => {
    try { await api("/api/feedback", { method: "POST", body: { id: sel.dataset.fb, status: sel.value } }); toast("تم الحفظ"); } catch (err) { toast(err.message, true); }
  }));
  $("#fbDl")?.addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(items.map((f) => ({ kind: f.kind, text: f.text, screen: f.screen, by: f.by_name, at: f.created_at, status: f.status })), null, 1)], { type: "application/json" }));
    a.download = `ملاحظات-البرنامج-${isoDay(new Date())}.json`; a.click();
  });
}

// ------------------------------------------------------------------ attendance and payroll
const tm = (iso) => (iso ? hhmm(iso) : "—");
async function viewAttendance() {
  const a = await api("/api/attendance");
  app.innerHTML = `<section class="panel narrowpanel"><h1>حضوري هذا الشهر</h1><p>${a.days_this_month} يوم</p>
    <table><thead><tr><th>اليوم</th><th>حضور</th><th>انصراف</th><th>المصدر</th></tr></thead><tbody>
    ${a.rows.map((r) => `<tr><td>${esc(r.day)}</td><td>${tm(r.check_in)}</td><td>${tm(r.check_out)}</td><td>${r.source === "phone" ? "هاتف" : r.source === "owner" ? "تعديل المالك" : "الكمبيوتر"}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">لا يوجد</td></tr>`}</tbody></table></section>`;
}
async function viewStaff(month) {
  month = month || isoDay(new Date()).slice(0, 7);
  const [r, accs] = await Promise.all([api(`/api/staff?month=${month}`), moneyAccounts(true)]);
  const post = (body, btn) => withBusy(btn, async () => { try { await api("/api/staff", { method: "POST", body: { month, ...body } }); toast("تم الحفظ"); viewStaff(month); } catch (err) { toast(err.message, true); } });
  app.innerHTML = `<section class="panel"><div class="row between"><h1>الحضور والرواتب</h1><span class="row"><label class="f">الشهر <input type="month" id="stM" value="${month}"></label>${reportTools("pay")}</span></div>
    <p class="muted">الراتب = الأساسي − الغياب (الأساسي ÷ أيام العمل × الأيام الناقصة) − السلف + المكافأة − الخصم. تراجع كل راتب وتعتمده، ويُسجَّل الصرف مصروفًا "رواتب وأجور".</p></section>
  ${r.people.map((p) => { const pr = p.payroll, sg = p.suggested; return `<section class="panel staff" data-u="${esc(p.id)}">
    <div class="row between"><h2>${esc(p.full_name)} <span class="chip">${esc(p.role_name)}</span></h2>
      ${pr ? `<span class="badge ${pr.status === "paid" ? "b-ok" : "b-info"}">${pr.status === "paid" ? "✓ صُرف" : "✓ معتمد — لم يُصرف بعد"}: ${money(pr.net)} ج</span>` : ""}</div>
    <div class="row"><label class="f">الراتب الأساسي (ج) <input class="narrow" data-f="sal" type="number" min="0" value="${money(p.monthly_salary)}"></label>
      <label class="f">أيام العمل في الشهر <input class="narrow" data-f="wd" type="number" min="1" max="31" value="${p.work_days}"></label>
      <button class="btn small secondary" data-a="pay_settings">احفظ</button></div>
    <div class="totals mt"><div>حضر<b>${p.days_present} يوم</b><span>${p.hours} ساعة</span></div><div>غياب<b>${sg.missing_days} يوم</b><span>− ${money(sg.absence_deduction)}</span></div>
      <div>سلف الشهر<b>${money(p.advances)}</b></div><div class="t-ok">المقترح<b>${money(sg.net)}</b></div></div>
    ${pr?.status === "paid" ? "" : `<div class="row mt"><label class="f">مكافأة (ج) <input class="narrow" data-f="bonus" type="number" min="0" value="${pr ? money(pr.bonus) : ""}"></label>
      <label class="f">خصم (ج) <input class="narrow" data-f="ded" type="number" min="0" value="${pr ? money(pr.deduction) : ""}"></label>
      <label class="f grow">ملاحظة <input data-f="note" value="${esc(pr?.note || "")}"></label><button class="btn small" data-a="approve">اعتمد الراتب</button>
      ${pr ? `<select data-f="from">${srcOptions(accs, "treasury", { ownerOutside: true })}</select><button class="btn small" data-a="pay">صرف ${money(pr.net)} ج</button>` : ""}</div>`}
    <details class="mt"><summary>سلفة / تعديل حضور / تفاصيل الأيام (${p.attendance.length})</summary>
      <div class="row mt"><label class="f">سلفة (ج) <input class="narrow" data-f="adv" type="number" min="0"></label><select data-f="advfrom">${srcOptions(accs, "drawer", { ownerOutside: true })}</select>
        <button class="btn small secondary" data-a="advance">سجّل السلفة</button></div>
      <div class="row mt"><label class="f">اليوم <input type="date" data-f="day"></label><label class="f">حضور <input type="time" data-f="in" value="10:00"></label>
        <label class="f">انصراف <input type="time" data-f="out" value="18:00"></label><label class="f grow">السبب <input data-f="why" placeholder="مثال: نسي التسجيل"></label><button class="btn small secondary" data-a="fix">سجّل اليوم</button></div>
      <table class="mt"><tbody>${p.attendance.map((x) => `<tr><td>${esc(x.day)}</td><td>${tm(x.check_in)} ← ${tm(x.check_out)}</td><td>${x.source === "owner" ? `<span class="badge b-warn">⚠ تعديل: ${esc(x.note || "")}</span>` : x.source === "phone" ? "هاتف" : "الكمبيوتر"}</td></tr>`).join("") || `<tr><td class="muted">لا يوجد</td></tr>`}</tbody></table></details>
  </section>`; }).join("") || `<section class="panel"><p class="muted">لا يوجد موظفون — أضفهم من «المستخدمون».</p></section>`}`;
  $("#stM").addEventListener("change", (e) => viewStaff(e.target.value));
  wireTools("pay", () => ({ title: "الحضور والرواتب", subtitle: `شهر ${month}`, el: app, sheets: [
    { name: "الرواتب", columns: [Tx("الموظف"), Tx("النوع"), M("الأساسي"), N("أيام العمل"), N("حضر (يوم)"), { label: "ساعات", type: "num" }, N("غياب (يوم)"), M("خصم الغياب"), M("السلف"), M("المكافأة"), M("الخصم"), M("الصافي"), Tx("الحالة")],
      rows: r.people.map((p) => [p.full_name, p.role_name, p.monthly_salary, p.work_days, p.days_present, p.hours, p.suggested.missing_days, p.suggested.absence_deduction, p.advances,
        p.payroll?.bonus || 0, p.payroll?.deduction || 0, p.payroll ? p.payroll.net : p.suggested.net, p.payroll ? (p.payroll.status === "paid" ? "صُرف" : "معتمد") : "مقترح"]) },
    { name: "الحضور", columns: [Tx("الموظف"), Dt("اليوم"), Tx("حضور"), Tx("انصراف"), Tx("ملاحظة")],
      rows: r.people.flatMap((p) => p.attendance.map((x) => [p.full_name, x.day, tm(x.check_in), tm(x.check_out), x.source === "owner" ? `تعديل: ${x.note || ""}` : ""])) }] }));
  document.querySelectorAll(".staff [data-a]").forEach((b) => b.addEventListener("click", () => {
    const box = b.closest(".staff"), f = (n) => box.querySelector(`[data-f="${n}"]`)?.value, uid = box.dataset.u, a = b.dataset.a;
    if (a === "pay_settings") post({ action: a, user_id: uid, monthly_salary: toPiasters(f("sal")) || 0, work_days: +f("wd") || 26 }, b);
    if (a === "approve") post({ action: a, user_id: uid, bonus: toPiasters(f("bonus")) || 0, deduction: toPiasters(f("ded")) || 0, note: f("note") || null }, b);
    if (a === "pay" && confirmInline(b)) post({ action: a, user_id: uid, paid_from: f("from"), expected_net: r.people.find((x) => x.id === uid)?.payroll?.net ?? null }, b);
    if (a === "advance") post({ action: a, user_id: uid, amount: toPiasters(f("adv")) || 0, paid_from: f("advfrom"), note: "سلفة" }, b);
    if (a === "fix") post({ action: a, user_id: uid, day: f("day"), check_in: f("in"), check_out: f("out") || null, note: f("why") }, b);
  }));
}

// ------------------------------------------------------------------ notifications (owner + managers)
function setBell(n) {
  const b = $("#bellN"); if (!b) return;
  b.hidden = !n; b.textContent = n > 99 ? "99+" : String(n);
}
async function refreshBell() {
  if (!ME?.permissions?.includes("notifications.read") || document.hidden) return;
  try { setBell((await api("/api/notifications?unread=1&limit=1")).unread); } catch { /* offline */ }
}
const NSEV = { error: ["b-err", "✕"], warning: ["b-warn", "⚠"], info: ["b-info", "ℹ"] };
async function viewNotifications() {
  const r = await api("/api/notifications?limit=100");
  app.innerHTML = `<section class="panel"><div class="row between"><h1>الإشعارات</h1>
    <button class="btn small secondary" id="nRead" ${r.unread ? "" : "disabled"}>تعليم الكل كمقروء</button></div>
    <ul class="notes">${r.items.map((n) => {
      const [cls, ic] = NSEV[n.severity] || NSEV.info;
      return `<li class="${n.seen ? "" : "new"}"><span class="badge ${cls}">${ic}</span>
        <div><b>${esc(n.title)}</b>${n.body ? `<div>${esc(n.body)}</div>` : ""}
        <div class="muted">${esc(n.actor || "")}${n.approver ? ` · بموافقة ${esc(n.approver)}` : ""} · ${esc(stamp(n.created_at))}
        ${n.entity_type === "sale" ? ` · <a href="#/sales/${esc(n.entity_id)}">الفاتورة</a>` : ""}
        ${n.entity_type === "purchase_invoice" ? ` · <a href="#/purchases/${esc(n.entity_id)}">الفاتورة</a>` : ""}
        ${n.entity_type === "item" ? ` · <a href="#/items/${esc(n.entity_id)}">بطاقة الصنف</a>` : ""}
        ${n.entity_type === "report" ? ` · <a href="#/reports">التقارير</a>` : ""}
        ${n.entity_type === "expense_schedule" || n.entity_type === "expense" ? ` · <a href="#/expenses">المصروفات</a>` : ""}</div></div></li>`;
    }).join("") || `<li class="muted">لا توجد إشعارات</li>`}</ul></section>`;
  $("#nRead").addEventListener("click", async () => {
    const x = await api("/api/notifications/read", { method: "POST", body: {} });
    setBell(x.unread); viewNotifications();
  });
}

// ------------------------------------------------------------------ my account: appearance + PIN
const ACCENTS = [["#0F5E54", "أخضر بترولي (الافتراضي)"], ["#1F4E8C", "أزرق"], ["#5B3E96", "بنفسجي"], ["#7A4B21", "بني"], ["#9F1D48", "نبيتي"]];
function contrastWhite(hex) {
  const ch = (v) => { const c = v / 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const [r, g, b] = [1, 3, 5].map((k) => parseInt(hex.slice(k, k + 2), 16));
  return 1.05 / (0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b) + 0.05);
}
function viewMe() {
  const p = PREFS, cur = (p.accent || "#0F5E54").toUpperCase();
  const radio = (name, v, label, extra = "") => `<label><input type="radio" name="${name}" value="${v}" ${p[name] === v ? "checked" : ""} ${extra}> ${label}</label>`;
  app.innerHTML = `<section class="panel narrowpanel"><div class="row between"><h1>الشكل واللغة</h1>
      <button class="btn small secondary" id="prReset">↺ أعد كل شيء إلى الافتراضي</button></div>
    <p class="muted">هذه الإعدادات خاصة بك وحدك، وتُحفظ على حسابك. الافتراضي هو الأريح للعمل طوال اليوم.</p>
    <h2 class="mt">الألوان</h2>
    <div class="themes">
      <label><span class="swatch sw-light"><i></i><i></i></span><span><input type="radio" name="theme" value="light" ${p.theme === "light" ? "checked" : ""}> فاتح دافئ (الافتراضي)</span></label>
      <label><span class="swatch sw-dark"><i></i><i></i></span><span><input type="radio" name="theme" value="dark" ${p.theme === "dark" ? "checked" : ""}> ليلي</span></label>
      <label><span class="swatch sw-contrast"><i></i><i></i></span><span><input type="radio" name="theme" value="contrast" ${p.theme === "contrast" ? "checked" : ""}> تباين عالي</span></label>
    </div>
    <h2 class="mt">لون البرنامج</h2>
    <div class="accents">${ACCENTS.map(([c, n]) => `<button type="button" class="acc-${c.slice(1).toLowerCase()} ${cur === c ? "on" : ""}" data-acc="${c}" title="${n}" aria-label="${n}"></button>`).join("")}
      <label class="f">لون آخر <input type="color" id="accPick" value="${cur.toLowerCase()}"></label></div>
    <p class="badge b-info mt">ℹ ألوان التنبيه ثابتة: أحمر = خطأ، أخضر = سليم، برتقالي = تنبيه، أزرق = معلومة.</p>
    <h2 class="mt">حجم الخط</h2>
    <div class="seg">${radio("font", "s", "صغير")}${radio("font", "m", "عادي (الافتراضي)")}${radio("font", "l", "كبير")}${radio("font", "xl", "كبير جدًا")}</div>
    <p class="muted">اختصار: Ctrl و + أو Ctrl و −</p>
    <h2 class="mt">أسماء الأصناف</h2>
    <div class="seg">${radio("names", "both", "الاثنان (الافتراضي)")}${radio("names", "en", "إنجليزي فقط")}${radio("names", "ar", "عربي فقط")}</div>
    <h2 class="mt">تأكيد الخروج</h2>
    <div class="seg">${radio("logout_confirm", "on", "اسألني قبل الخروج (الافتراضي)")}${radio("logout_confirm", "off", "اخرج على طول")}</div>
    <h2 class="mt">لغة البرنامج</h2>
    <div class="seg">${radio("lang", "ar", "العربية (الافتراضي)")}${radio("lang", "en", "English")}</div>
      <p class="muted small">English يغيّر نصوص الشاشات. أسماء الأصناف تتبع اختيار "أسماء الأصناف". إذا بقيت كلمة بالعربية، أرسلها من 💬 ملاحظة.</p>
  </section>
  <section class="panel narrowpanel"><h1>الرقم السري</h1>
    <p>${esc(ME.user.full_name)} — <span class="badge b-info">${esc(ME.role_name || "")}</span>
    <span class="badge ${ME.has_pin ? "b-ok" : "b-warn"}">${ME.has_pin ? "✓ لديك رقم سري" : "⚠ ليس لديك رقم سري بعد"}</span></p>
    <p class="muted">الرقم السري (4 لـ 6 أرقام) للدخول السريع على جهاز الصيدلية${["owner", "manager"].includes(ME.role) ? "، وللموافقة على شاشة شخص آخر" : ""}.</p>
    <form id="pinSet" class="row">
      <label class="f">كلمة المرور الحالية <input name="password" type="password" autocomplete="current-password" required></label>
      <label class="f">الرقم السري الجديد <input name="pin" type="password" inputmode="numeric" maxlength="6" autocomplete="off" required></label>
      <button class="btn">احفظ</button></form></section>`;
  const save = async (ch) => { try { await savePrefs(ch); viewMe(); } catch (err) { toast(err.message, true); viewMe(); } };
  document.querySelectorAll("input[type=radio][name]").forEach((r) => r.addEventListener("change", () => save({ [r.name]: r.value })));
  document.querySelectorAll("[data-acc]").forEach((b) => b.addEventListener("click", () => save({ accent: b.dataset.acc === "#0F5E54" ? null : b.dataset.acc })));
  $("#accPick").addEventListener("change", (e) => {
    const c = e.target.value.toUpperCase(), cr = contrastWhite(c);
    if (cr < 4.5) { toast(`هذا اللون فاتح: لن يُقرأ النص الأبيض عليه (التباين ${cr.toFixed(1)} والمطلوب 4.5). اختر لونًا أغمق`, true); e.target.value = cur.toLowerCase(); return; }
    save({ accent: c });
  });
  $("#prReset").addEventListener("click", (e) => { if (confirmInline(e.target)) save({ reset: true }); });
  $("#pinSet").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/me/pin", { method: "POST", body: { password: e.target.password.value, pin: e.target.pin.value } });
      ME = await api("/api/me"); toast("تم حفظ الرقم السري"); viewMe();
    } catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ users & permissions (owner)
const ROLE_CLS = { owner: "b-brand", manager: "b-ok", pharmacist: "b-info", assistant: "b-plain" };
const pctOf = (bp) => (bp == null ? "" : String(bp / 100));
async function viewUsers() {
  // part 3 A: the manager (users.permissions) opens the page to switch staff permissions; the rest stays the owner's
  if (!ME.permissions.includes("users.manage") && !ME.permissions.includes("users.permissions")) { app.innerHTML = `<section class="panel"><p>هذه الصفحة للمالك فقط.</p></section>`; return; }
  const [{ users, roles }, sales] = await Promise.all([api("/api/users"), api("/api/settings/sales")]);
  // U3-7: the dhikr strip (on for all, off for some) and each employee's hours (a reminder before the end)
  if (ME.permissions.includes("users.manage")) api("/api/settings/staff-ui?all=1").then((su) => {
    const box = $("#suPanel"); if (!box) return;
    box.innerHTML = `<h2>الذِكر ومواعيد الشغل</h2>
      <label class="f"><input type="checkbox" id="suOn" ${su.dhikr_on ? "checked" : ""}> شريط ذِكر صغير تحت الشاشة (مبيغطيش أي زرار ومبيوقفش البيع)</label>
      <table><thead><tr><th>الموظف</th><th>الذِكر</th><th>من</th><th>لحد</th></tr></thead><tbody>${users.filter((u) => u.role !== "owner").map((u) => `<tr data-u="${esc(u.id)}"><td>${esc(u.full_name)}</td>
        <td><input type="checkbox" data-dk ${su.dhikr_off_users.includes(u.id) ? "" : "checked"}></td>
        <td><input type="time" data-st value="${esc(su.hours[u.id]?.start || "")}"></td><td><input type="time" data-en value="${esc(su.hours[u.id]?.end || "")}"></td></tr>`).join("")}</tbody></table>
      <p class="muted">قبل آخر الميعاد بربع ساعة البرنامج بيفكّر الموظف يقفل الشيفت ويسجل انصرافه. تذكير بس.</p><button class="btn" id="suSave">حفظ</button>`;
    $("#suSave").addEventListener("click", async () => {
      const rows = [...box.querySelectorAll("tr[data-u]")], hours = {};
      let half = false;
      rows.forEach((tr) => { const st = tr.querySelector("[data-st]").value, en = tr.querySelector("[data-en]").value; half ||= !!st !== !!en; hours[tr.dataset.u] = st && en ? { start: st, end: en } : null; });
      if (half) { toast("اكتب الميعادين (من ولحد) أو سيبهم فاضيين", true); return; }
      try {
        await api("/api/settings/staff-ui", { method: "PUT", body: { dhikr_on: $("#suOn").checked, hours,
          dhikr_off_users: rows.filter((tr) => !tr.querySelector("[data-dk]").checked).map((tr) => tr.dataset.u) } });
        STAFF_UI = null; staffUiStop(); staffUi(); toast("اتحفظ");
      } catch (er) { toast(er.message, true); }
    });
  }).catch(() => {});
  app.innerHTML = `<section class="panel"><div class="row between"><h1>المستخدمون</h1></div>
    <table><thead><tr><th>الاسم</th><th>النوع</th><th>الحالة</th><th>رقم سري</th><th>تعديلات على الافتراضي</th></tr></thead>
    <tbody>${users.map((u) => `<tr class="clickable" data-go="${esc(u.id)}"><td><b>${esc(u.full_name)}</b><div class="muted">${esc(u.username)}</div></td>
      <td><span class="badge ${ROLE_CLS[u.role] || ""}">${esc(u.role_name)}</span></td>
      <td>${u.active ? `<span class="badge b-ok">✓ مفعّل</span>` : `<span class="badge b-err">✕ موقوف</span>`}</td>
      <td>${u.has_pin ? "✓" : `<span class="badge b-warn">⚠ لا يوجد</span>`}</td>
      <td>${u.role === "owner" ? "كل الصلاحيات" : u.changes ? `${u.changes} تعديل` : "على الافتراضي"}</td></tr>`).join("")}</tbody></table></section>
  ${ME.permissions.includes("users.manage") ? `<section class="panel" id="suPanel"><h2>الذِكر ومواعيد الشغل</h2><p class="muted">بيتحمل…</p></section>` : ""}
  <section class="panel"><h2>مستخدم جديد</h2>
    <form id="uNew" class="row">
      <label class="f">الاسم <input name="full_name" required></label>
      <label class="f">اسم الدخول <input name="username" required minlength="3" autocomplete="off"></label>
      <label class="f">كلمة المرور (10 على الأقل) <input name="password" type="password" required minlength="10" autocomplete="new-password"></label>
      <label class="f">الرقم السري (اختياري) <input name="pin" type="password" inputmode="numeric" maxlength="6" autocomplete="off"></label>
      <label class="f">النوع <select name="role">${roles.filter((r) => r.code !== "owner").map((r) => `<option value="${r.code}" ${r.code === "assistant" ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select></label>
      <button class="btn">أضف</button></form></section>
  <section class="panel"><h2>حدود الخصم لكل نوع</h2>
    <p class="muted">الافتراضي: لا خصم لأحد غير المالك. ومهما كان الحد، لا يستطيع أحد غير المالك أن يخصم حتى ينزل السعر عن التكلفة.</p>
    <div class="row">${roles.filter((r) => r.code !== "owner").map((r) => `<label class="f">${esc(r.name)} — أقصى خصم %
      <span class="row"><input class="narrow" type="number" min="0" max="100" step="0.5" value="${pctOf(r.max_discount_bp)}" data-rdisc="${r.code}">
      <button class="btn small secondary" data-rsave="${r.code}">احفظ</button></span></label>`).join("")}</div></section>
  <section class="panel"><h2>مدة المرتجع</h2>
    <form id="retWin" class="row"><label class="f">عدد الأيام <input class="narrow" type="number" min="0" max="365" name="days" value="${sales.return_window_days}"></label>
    <button class="btn small secondary">احفظ</button></form>
    <p class="muted">بعد هذه المدة يحتاج المرتجع إلى المالك (أو من منحه المالك هذه الصلاحية "مرتجع بعد مدة المرتجع").</p></section>`;
  document.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/users/${tr.dataset.go}`; }));
  $("#uNew").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (!f.pin) delete f.pin;
    try { const r = await api("/api/users", { method: "POST", body: f }); toast("تمت الإضافة"); location.hash = `#/users/${r.user.id}`; }
    catch (err) { toast(err.message, true); }
  });
  document.querySelectorAll("[data-rsave]").forEach((b) => b.addEventListener("click", async () => {
    const v = document.querySelector(`[data-rdisc="${b.dataset.rsave}"]`).value;
    try { await api(`/api/roles/${b.dataset.rsave}/discount`, { method: "POST", body: { max_discount_bp: toBp(v) ?? 0 } }); toast("تم الحفظ"); }
    catch (err) { toast(err.message, true); }
  }));
  $("#retWin").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/settings/sales", { method: "PUT", body: { return_window_days: +e.target.days.value } }); toast("تم الحفظ"); }
    catch (err) { toast(err.message, true); }
  });
}

async function viewUser(id) {
  if (!ME.permissions.includes("users.manage") && !ME.permissions.includes("users.permissions")) { location.hash = "#/users"; return; }
  const [m, { roles }] = await Promise.all([api(`/api/users/${id}`), api("/api/users")]);
  const u = m.user, owner = u.role === "owner", mine = String(u.id) === String(ME.user.id);
  // #131: changing one's own sign-in needs the current password (an open session alone is not enough)
  const cur = mine ? `<label class="f">كلمة المرور الحالية <input name="current" type="password" autocomplete="current-password" required></label>` : "";
  const roleName = Object.fromEntries(roles.map((r) => [r.code, r.name]));
  const cell = (v) => (v ? `<span class="ok">✓ مسموح</span>` : `<span class="muted">✕ ممنوع</span>`);
  const pick = (it) => {
    if (owner) return `<span class="ok">✓ مسموح</span>`;
    if (it.code === "users.manage" || (!ME.permissions.includes("users.manage") && it.code === "users.permissions")) return `<span class="muted">للمالك فقط</span>`;
    const def = it.roles[u.role];
    return `<select data-perm="${it.code}" class="${it.exception === "grant" ? "ex-grant" : it.exception === "deny" ? "ex-deny" : ""}">
      <option value="default" ${!it.exception ? "selected" : ""}>${def ? "✓ مسموح" : "✕ ممنوع"} (الافتراضي)</option>
      ${def ? `<option value="deny" ${it.exception === "deny" ? "selected" : ""}>✕ ممنوعة عنه</option>`
            : `<option value="grant" ${it.exception === "grant" ? "selected" : ""}>✓ مسموحة له</option>`}
    </select>`;
  };
  app.innerHTML = `<section class="panel"><div class="row between">
      <div><a href="#/users">← المستخدمون</a><h1>${esc(u.full_name)} <span class="badge ${ROLE_CLS[u.role] || ""}">${esc(u.role_name)}</span></h1>
      <p class="muted">${owner ? "للمالك كل الصلاحيات، ولا يستطيع أحد منعه من شيء." : "صلاحياته مأخوذة من نوعه. أي تعديل هنا يخصه وحده."}</p></div>
      ${owner ? "" : `<button class="btn secondary" id="uReset">↺ أعده إلى الافتراضي</button>`}</div>
    ${owner ? "" : `<div class="row">
      <label class="f">النوع <select id="uRole">${roles.filter((r) => r.code !== "owner").map((r) => `<option value="${r.code}" ${r.code === u.role ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select></label>
      <label class="f">أقصى خصم % (فارغ = حد نوعه ${pctOf(m.role_discount_bp[u.role])}%)
        <span class="row"><input id="uDisc" class="narrow" type="number" min="0" max="100" step="0.5" value="${pctOf(m.max_discount_bp)}">
        <button class="btn small secondary" id="uDiscSave">احفظ</button></span></label>
      <button class="btn small ${u.active ? "danger" : ""}" id="uActive">${u.active ? "إيقاف المستخدم" : "تشغيل المستخدم"}</button></div>`}
    <div class="row mt">
      <form id="uPin" class="row">${cur}<label class="f">رقم سري جديد <input name="pin" type="password" inputmode="numeric" maxlength="6" autocomplete="off" required></label><button class="btn small secondary">احفظ الرقم</button></form>
      ${owner ? "" : `<form id="uPw" class="row">${cur}<label class="f">كلمة مرور جديدة <input name="password" type="password" minlength="10" autocomplete="new-password" required></label><button class="btn small secondary">غيّر كلمة المرور</button></form>`}
    </div></section>
  <section class="panel tablewrap"><h2>الصلاحيات</h2>
    <div class="legend"><span class="badge b-info">✓ مسموحة له = مسموحة لهذا الشخص فقط</span><span class="badge b-err">✕ ممنوعة عنه = ممنوعة عليه وحده</span><span>الباقي = افتراضي نوعه</span></div>
    <table class="perms"><thead><tr><th>الصلاحية</th>${["owner", "manager", "pharmacist", "assistant"].map((c) => `<th>${esc(roleName[c] || c)}</th>`).join("")}<th>${esc(u.full_name)}</th></tr></thead>
    <tbody>${m.groups.map((g) => `<tr class="grp"><td colspan="6">${esc(g.title)}</td></tr>` + g.items.map((it) => `<tr class="${it.exception ? "ex" : ""}">
      <td>${esc(it.label)}</td>${["owner", "manager", "pharmacist", "assistant"].map((c) => `<td>${cell(it.roles[c])}</td>`).join("")}
      <td>${pick(it)}</td></tr>`).join("")).join("")}</tbody></table></section>`;
  const post = async (action, body) => {
    try { await api(`/api/users/${id}/${action}`, { method: "POST", body }); toast("تم الحفظ"); viewUser(id); }
    catch (err) { toast(err.message, true); }
  };
  document.querySelectorAll("[data-perm]").forEach((sel) => sel.addEventListener("change", () =>
    post("permission", { permission: sel.dataset.perm, mode: sel.value })));
  $("#uReset")?.addEventListener("click", (e) => { if (confirmInline(e.target)) post("reset", {}); });
  $("#uRole")?.addEventListener("change", (e) => post("role", { role: e.target.value }));
  $("#uDiscSave")?.addEventListener("click", () => post("discount", { max_discount_bp: toBp($("#uDisc").value) }));
  $("#uActive")?.addEventListener("click", (e) => { if (confirmInline(e.target)) post("active", { active: !u.active }); });
  $("#uPin").addEventListener("submit", (e) => { e.preventDefault(); post("pin", { pin: e.target.pin.value, ...(mine ? { current_password: e.target.current.value } : {}) }); });
  $("#uPw")?.addEventListener("submit", (e) => { e.preventDefault(); post("password", { password: e.target.password.value, ...(mine ? { current_password: e.target.current.value } : {}) }); });
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
route();
