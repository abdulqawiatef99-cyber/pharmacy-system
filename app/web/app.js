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
const pct = (bp) => (bp == null ? "" : (bp / 100).toString());
// pharmacists type expiry as 629 / 0629 / 6-29 / 6.29 / 6 29: "629" -> "6-29" (month-year)
const normExpiry = (v) => {
  const t = String(v || "").trim().replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d)).replace(/\s+/g, "-");
  const m = t.match(/^(\d{1,2})(\d{2})$/);
  return m && t.length >= 3 ? `${m[1]}-${m[2]}` : t;
};
// ------------------------------------------------------------------ person's screen preferences
const PREFS_DEFAULT = { theme: "light", accent: null, font: "m", lang: "ar", names: "both", help: "on" };
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
    out = I18N.re ? t.replace(I18N.re, (m) => I18N.map.get(m) ?? m) : t;
    // then each piece between separators, only when the whole piece is a known phrase
    out = out.replace(/[^—·():/،|\n]+/g, (seg) => { const k = seg.trim(); const v = k && I18N.map.get(k); return v ? seg.replace(k, v) : seg; });
    for (const [re, rep] of I18N_PATTERNS) out = out.replace(re, rep);
  }
  if (out == null || out === t) return null;
  const lead = s.match(/^\s*/)[0], trail = s.match(/\s*$/)[0];
  return lead + out + trail;
}
// words that come next to numbers ("3 فاتورة", "76.45 ج", "5 يوم") and a few fixed bits
const NUM = "(\\d[\\d.,]*)";
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
    try { await loadScript("/static/i18n-en.js"); } catch { toast("ملف اللغة الإنجليزية غير موجود", true); return; }
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
// item names as the person chose: both (English + Arabic under it), English only, Arabic only
const itemText = (it) => (PREFS.names === "ar" ? it.name_ar || it.name_en : it.name_en || it.name_ar) || "";
const itemHtml = (it) => (PREFS.names === "both" && it.name_en && it.name_ar
  ? `${esc(it.name_en)}<span class="iname-ar">${esc(it.name_ar)}</span>` : esc(itemText(it)));
// search: English or Arabic name, code, barcode, pieces, %, wrong keyboard language
async function findItems(q, limit = 8) {
  const r = await api(`/api/items?q=${encodeURIComponent(q)}&limit=${limit}&meta=1`);
  return r;
}
const hintHtml = (h) => (!h ? "" : h.kind === "layout"
  ? `<p class="badge b-warn search-hint">⚠ لوحة المفاتيح كانت على اللغة الأخرى — جارٍ البحث عن: <bdi>${esc(h.query)}</bdi></p>`
  : `<p class="badge b-info search-hint">ℹ لا يوجد اسم مطابق تمامًا — هذه أقرب الأصناف</p>`);

const toBp = (v) => (v === "" || v == null ? null : Math.round(parseFloat(v) * 100));

function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = isError ? "error" : "";
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), isError ? 7000 : 3000);
}

const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());

// ------------------------------------------------------------------ on-screen approval
// The owner or a manager types his PIN on this screen; the approval is kept for
// 90 seconds on this screen only (cleared when leaving it), then asked again.
let APPROVAL = null;
const approvalHeaders = () => {
  if (APPROVAL && APPROVAL.until > Date.now()) return { "X-Approver": APPROVAL.id, "X-Approver-Pin": APPROVAL.pin };
  APPROVAL = null; return {};
};
window.addEventListener("hashchange", () => { APPROVAL = null; });

async function askApproval(details, message) {
  let list = [];
  try { list = (await api("/api/approvers")).approvers; } catch { /* offline */ }
  if (!list.length) {
    toast(`${message}. لا يوجد مالك أو مدير لديه رقم سري للموافقة — يضبط المالك رقمه من صفحة "حسابي"`, true);
    return false;
  }
  let dlg = $("#approveDlg");
  if (!dlg) { dlg = document.createElement("dialog"); dlg.id = "approveDlg"; document.body.append(dlg); }
  dlg.innerHTML = `<form method="dialog" class="approve">
    <h2>يحتاج إلى موافقة</h2>
    <p class="badge b-warn">⚠ ${esc(details?.label || message)}</p>
    <p class="muted">${esc(message)}</p>
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
        body: JSON.stringify({ permission: details?.permission || "" }) });
      const d = await r.json().catch(() => ({}));
      const enough = r.ok && d.ok && (details?.permission !== "discount" || d.discount_limit_bp >= (details.requested_bp || 0));
      if (!enough) {
        err.hidden = false;
        err.textContent = !r.ok ? `✕ ${d?.error?.message || "الرقم السري غير صحيح"}` : "✕ الشخص الذي اخترته لا يملك هذه الصلاحية";
        form.pin.value = ""; form.pin.focus(); return;
      }
      APPROVAL = { id: who, pin, until: Date.now() + 90000 };
      dlg.close(); resolve(true);
    });
    dlg.addEventListener("cancel", () => resolve(false), { once: true });
  });
}

async function api(path, { method = "GET", body, form, _retried = false } = {}) {
  const opts = { method, headers: { "X-Requested-With": "pharm", ...approvalHeaders() }, credentials: "same-origin" };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  // every POST carries an idempotency key; on a network failure it is retried with
  // the SAME key, so the server either replays the first result or runs it once.
  if (method === "POST" && !path.startsWith("/api/auth/") && !path.startsWith("/api/setup")) opts.headers["Idempotency-Key"] = newKey();
  let r;
  for (let attempt = 0; ; attempt++) {
    try { r = await fetch(path, opts); break; }
    catch (netErr) {
      if (method === "GET" && attempt >= 1) throw new Error("لا يوجد اتصال بالخادم");
      if (attempt >= 2) throw new Error("لا يوجد اتصال بالخادم — لم يتأكد حفظ العملية، راجعها قبل التكرار");
      await new Promise((res) => setTimeout(res, 800 * (attempt + 1)));
    }
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok && r.status === 403 && data?.error?.code === "approval_required" && !_retried) {
    if (await askApproval(data.error.details, data.error.message)) return api(path, { method, body, form, _retried: true });
  }
  if (!r.ok && opts.headers["X-Approver"] && ["invalid_pin", "pin_locked"].includes(data?.error?.code)) APPROVAL = null;
  if (!r.ok) {
    if (r.status === 401 && !path.startsWith("/api/auth") && !opts.headers["X-Approver"]) { ME = null; location.hash = "#/login"; }
    const err = new Error(data?.error?.message || "خطأ في الاتصال");
    err.code = data?.error?.code; err.details = data?.error?.details;
    throw err;
  }
  return data;
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

// ------------------------------------------------------------------ routing
const routes = [
  [/^#\/login$/, viewLogin],
  [/^#\/setup$/, viewSetup],
  [/^#\/purchases$/, viewPurchases],
  [/^#\/purchases\/([\w-]+)$/, viewInvoice],
  [/^#\/items$/, viewItems],
  [/^#\/items\/([\w-]+)\/edit$/, viewItemEdit],
  [/^#\/items\/([\w-]+)$/, viewItem],
  [/^#\/categories$/, viewCategories],
  [/^#\/groups$/, viewGroups],
  [/^#\/shift$/, viewShift],
  [/^#\/suppliers$/, viewSuppliers],
  [/^#\/suppliers\/([\w-]+)$/, viewSupplier],
  [/^#\/shortages$/, viewShortages],
  [/^#\/transfers$/, viewTransfers],
  [/^#\/audit$/, viewAudit],
  [/^#\/supplier-returns(?:\/(new|[\w-]+))?(?:\?.*)?$/, viewSupplierReturns],
  [/^#\/customers$/, viewCustomers],
  [/^#\/expenses$/, viewExpenses],
  [/^#\/home$/, viewHome],
  [/^#\/staff$/, viewStaff],
  [/^#\/attendance$/, viewAttendance],
  [/^#\/feedback$/, viewFeedback],
  [/^#\/reports$/, viewReports],
  [/^#\/owner-reports$/, viewOwnerReports],
  [/^#\/profit$/, viewProfit],
  [/^#\/customers\/([\w-]+)$/, viewCustomer],
  [/^#\/import$/, viewImport],
  [/^#\/import\/([\w-]+)$/, viewImportRun],
  [/^#\/recalls$/, viewRecalls],
  [/^#\/counts$/, viewCounts],
  [/^#\/procurement$/, viewProcurement],
  [/^#\/deadstock$/, viewDeadstock],
  [/^#\/sales$/, viewSales],
  [/^#\/system$/, viewSystem],
  [/^#\/sales\/([\w-]+)$/, viewSale],
  [/^#\/pos$/, viewPos],
  [/^#\/counts\/([\w-]+)$/, viewCountSession],
  [/^#\/users$/, viewUsers],
  [/^#\/users\/([\w-]+)$/, viewUser],
  [/^#\/notifications$/, viewNotifications],
  [/^#\/me$/, viewMe],
  [/^#\/customize$/, viewCustomize],
];

async function route() {
  const hash = location.hash || "#/home";
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
    $("#navUsers").hidden = !ME.permissions.includes("users.manage");
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
  if (ME && !route.badgeChecked) { route.badgeChecked = true; checkUpdateBadge(); }
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", hash.startsWith("#/" + a.dataset.nav)));
  $("#navMore")?.classList.toggle("active", !!$("#navMore .menu a.active"));
  for (const [re, fn] of routes) {
    const m = hash.match(re);
    if (m) { try { await fn(...m.slice(1)); } catch (e) { toast(e.message, true); } return; }
  }
  location.hash = "#/home";
}
// ------------------------------------------------------------------ help, top bar, shortcuts, columns (Task 8)
const SCREENS = {
  pos: ["شاشة البيع", "sales.create"], sales: ["فواتير البيع", "sales.read"], purchases: ["الشراء", "purchases.read"],
  items: ["الأصناف", "items.read"], counts: ["الجرد", "inventory.count"], shift: ["الوردية والخزينة", "sales.create"],
  customers: ["العملاء والآجل", "sales.read"], reports: ["التقارير", null], "owner-reports": ["تقارير المالك", "sales.totals"],
  expenses: ["المصروفات", "expenses.record"], transfers: ["مراجعة التحويلات", "sales.totals"], profit: ["الربح", "costs.read"],
  shortages: ["النواقص", "items.read"], suppliers: ["الموردون وحساباتهم", "suppliers.read"], "supplier-returns": ["مرتجعات للموردين", "purchases.read"],
  procurement: ["الطلبية", "purchases.read"], deadstock: ["الرواكد", "purchases.read"], recalls: ["تنبيهات السحب", "items.read"],
  import: ["نقل البيانات", "items.import"], users: ["المستخدمون", "users.manage"], audit: ["سجل العمليات", "audit.read"],
  staff: ["الحضور والرواتب", "payroll.manage"], system: ["البرنامج والنسخ الاحتياطي", null], me: ["المظهر واللغة ورقمي السري", null],
  feedback: ["الشكاوى والمقترحات", null], groups: ["المجموعات العلاجية", "items.read"], notifications: ["الإشعارات", "notifications.read"],
  customize: ["تخصيص الواجهة", "settings.manage"],
};
const canScreen = (k) => !!SCREENS[k] && (!SCREENS[k][1] || ME.permissions.includes(SCREENS[k][1]));
const HELP = {
  home: ["الصفحة الرئيسية: ملخص اليوم وما يحتاج إلى انتباهك.", ["اضغط على أي تنبيه لفتح الشاشة الخاصة به.", "يسجّل الموظف حضوره وانصرافه من هنا."]],
  pos: ["امسح الباركود أو اكتب أول حروف الصنف، ثم اختر طريقة الدفع وأتمم البيع.", ["Enter يضيف علبة، وShift+Enter يضيف وحدة، وF9 يتمم البيع، وF2 يفتح فاتورة جديدة.", "إذا كان الصنف ناقصًا اضغط \"↔ مثيل / بديل\".", "مسح الصنف نفسه مرة أخرى يزيد كميته في السطر نفسه.", "تحذيرات الأصناف يجب تأكيدها قبل البيع، ويُسجَّل التأكيد باسمك."]],
  sales: ["فواتير البيع: ابحث وصفِّ واطبع، واضغط على أي فاتورة لعرض أصنافها.", ["يمكن اختيار أكثر من نوع فاتورة معًا.", "الرقم القصير يُبحث به كرقم فاتورة، والأطول كرقم هاتف أيضًا.", "اختر فواتير بعلامة ✓ ثم \"🖨 المحدد\" لطباعتها وحدها."]],
  purchases: ["فواتير الشراء: ارفع ملف الفاتورة أو أدخلها يدويًا، وراجعها ثم اعتمدها.", ["لا يتغير المخزون إلا بعد الاعتماد.", "الخانات المنقطة تحتاج إلى مراجعة قبل الاعتماد."]],
  items: ["الأصناف: ابحث بالاسم العربي أو الإنجليزي أو الكود أو الباركود.", ["حدد أصنافًا بعلامة ✓ لنقلها إلى قسم أو وضع علامة عليها أو تثبيتها في البيع السريع.", "اضغط على الصنف لفتح بطاقته."]],
  "items/1": ["بطاقة الصنف: الرصيد والتشغيلات والأسعار والحركة والمثيل والبديل.", ["التسوية تحتاج إلى سبب، وتظهر في تقرير خسائر المخزون.", "المثيل يظهر عند كتابة المادة الفعالة والتركيز والشكل في تعديل الصنف."]],
  categories: ["الأقسام تنظّم الأصناف وتحدد ما لا يُقبل إرجاعه.", ["\"اقتراح\" يقترح قسمًا للأصناف التي بدون قسم، وأنت من يعتمد."]],
  groups: ["المجموعات العلاجية: أنت من يحدد ما هو بديل لما.", ["أضف مواد فعالة أو أصنافًا بعينها إلى المجموعة.", "البرنامج لا يخمّن أي بديل من تلقاء نفسه."]],
  shift: ["الوردية والخزينة: افتح الوردية برصيد البداية، وأغلقها بعدّ النقدية فئةً فئة.", ["لا يظهر المتوقع للموظف إلا بعد الإغلاق.", "أي فرق يحتاج إلى سبب مكتوب."]],
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
  import: ["نقل البيانات من برنامج آخر: ارفع الملف، وراجع التقرير، ثم طبّق.", ["لا يُطبَّق شيء قبل موافقتك."]],
  recalls: ["تنبيهات السحب: الصق منشور هيئة الدواء وراجع التشغيلات المطابقة.", []],
  counts: ["الجرد: ابدأ جردًا شاملًا أو جزئيًا، وسجّل الكميات، ثم راجع الفروق واعتمد.", ["لكل عجز أو زيادة سبب."]],
  procurement: ["الطلبية: الكمية المقترحة لكل صنف مع سببها.", ["لا يطلب البرنامج شيئًا من تلقاء نفسه."]],
  deadstock: ["الرواكد: من تقارير البرنامج القديم.", []],
  system: ["البرنامج: التحديث، والنسخ الاحتياطي، وشكل الإيصال، وأجهزة البطاقات.", []],
  users: ["المستخدمون: الأدوار والصلاحيات وحدود الخصم.", []],
  notifications: ["الإشعارات: كل ما حدث ويحتاج إلى علمك.", []],
  me: ["المظهر واللغة ورقمي السري: إعدادات خاصة بك وحدك.", []],
  customize: ["تخصيص الواجهة لهذه الصيدلية: الشريط العلوي، وأزرار الصفحة الرئيسية، والاختصارات.", ["التغيير يظهر لكل المستخدمين.", "الأعمدة تُضبط من زر \"⚙ الأعمدة\" فوق الجدول."]],
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
    <div class="row mt">${ME.permissions.includes("settings.manage") ? `<a class="btn secondary" href="#/customize">تخصيص الواجهة</a>` : ""}<button class="btn">إغلاق</button></div></form>`;
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
    <label class="f">اسم الصيدلية <input name="pharmacy" required minlength="2" placeholder="مثال: صيدلية عبد القوي"></label><br>
    <label class="f">اسمك <input name="full_name" placeholder="مثال: د. عبد القوي"></label><br>
    <label class="f">اسم المستخدم (للدخول) <input name="username" required minlength="3" autocomplete="username" value="owner"></label><br>
    <label class="f">كلمة السر (10 حروف أو أرقام على الأقل) <input name="password" type="password" required minlength="10" autocomplete="new-password"></label><br>
    <label class="f">أعد كتابة كلمة السر <input name="password2" type="password" required minlength="10" autocomplete="new-password"></label><br>
    <button class="btn" type="submit">ابدأ</button>
    <p class="muted">احفظ كلمة السر في مكان آمن، فبدونها لن تستطيع الدخول.</p>
  </form>
  <form class="panel login" id="restoreForm">
    <h2>أو: هل لديك بيانات سابقة؟</h2>
    <p class="muted">إذا كان البرنامج يعمل من قبل (على هذا الجهاز أو جهاز آخر)، فاختر آخر نسخة احتياطية، وهي ملف يبدأ اسمه بـ pharmacy-backup. ستدخل بنفس اسم المستخدم وكلمة السر القديمة.</p>
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
      const r = await api("/api/setup", { method: "POST", body: Object.fromEntries(["pharmacy", "full_name", "username", "password"].map((k) => [k, f.get(k)])) });
      ME = r.me; location.hash = "#/import";
      toast("البرنامج جاهز. الخطوة التالية: نقل بياناتك من البرنامج القديم");
    } catch (err) {
      if (err.code === "already_setup") { location.hash = "#/login"; return; }
      toast(err.message, true);
    }
  });
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
const STATUS = { draft: ["مسودة", "b-warn"], approved: ["معتمدة", "b-ok"], cancelled: ["ملغاة", "b-err"] };

async function viewPurchases() {
  const [list, suppliers] = await Promise.all([api("/api/purchases"), api("/api/suppliers")]);
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
  </section>
  <section class="panel">
    <h2>أو: فاتورة يدوية</h2>
    <form id="manForm" class="row">
      <label class="f">المورد<select name="supplier_id" required><option value="">— اختر —</option>
        ${suppliers.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select></label>
      <label class="f">رقم الفاتورة<input name="invoice_no" required></label>
      <label class="f">التاريخ<input name="invoice_date" type="date" value="${localDay()}"></label>
      <button class="btn" type="submit">ابدأ الفاتورة</button>
    </form>
    <p class="muted">${suppliers.length ? "المورد غير موجود في القائمة؟" : "لا يوجد موردون مسجلون بعد."} اكتب اسمه:
      <input id="newSup" placeholder="اسم المورد"> <button class="btn small secondary" id="addSup" type="button">إضافة مورد</button></p>
  </section>
  <section class="panel" id="puRep">
    <div class="row between"><h2>الفواتير</h2>${reportTools("pur")}</div>
    <div class="row noprint">${rangeBar("puRange")}
      <label class="f">المورد <select id="puSup"><option value="">الكل</option>${suppliers.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</select></label>
      <label class="f">الحالة <select id="puSt"><option value="">الكل</option>${Object.entries(STATUS).map(([k, [t]]) => `<option value="${k}">${t}</option>`).join("")}</select></label></div>
    <div id="puBox"></div>
  </section>`;
  let rows = list, sub = "";
  // an approved invoice counts with what was posted to the supplier (computed); a draft shows the paper total
  const amt = (i) => (i.status === "approved" ? i.computed_total ?? i.declared_total : i.declared_total ?? i.computed_total);
  const drawList = () => {
    const ok = rows.filter((i) => i.status === "approved");
    $("#puBox").innerHTML = `<div class="tablewrap"><table class="list">
      <thead><tr><th>المورد</th><th>رقم الفاتورة</th><th>التاريخ</th><th>الأصناف</th>
        <th>الإجمالي</th><th>الحالة</th><th>المصدر</th></tr></thead>
      <tbody>${rows.map((i) => `<tr>
        <td><a href="#/purchases/${esc(i.id)}">${esc(i.supplier_name || "— غير محدد —")}</a></td>
        <td>${esc(i.invoice_no || "")}</td><td>${esc(i.invoice_date || dayOf(i.created_at))}</td><td>${i.lines}</td>
        <td>${money(amt(i))}</td>
        <td><span class="badge ${STATUS[i.status][1]}">${STATUS[i.status][0]}</span></td>
        <td>${esc(i.source_type)}</td></tr>`).join("") ||
        `<tr><td colspan="7" class="muted">لا توجد فواتير بعد</td></tr>`}</tbody>
    </table></div>${ok.length ? `<p><b>المعتمد: ${ok.length} فاتورة — ${money(ok.reduce((a, i) => a + (amt(i) || 0), 0))} ج</b></p>` : ""}`;
  };
  const loadList = async (f, t) => {
    const qs = new URLSearchParams({ from: f, to: t }); if ($("#puSup").value) qs.set("supplier", $("#puSup").value); if ($("#puSt").value) qs.set("status", $("#puSt").value);
    rows = await api(`/api/purchases?${qs}`); sub = rangeText(f, t); drawList();
  };
  const getRange = wireRange("puRange", (f, t) => loadList(f, t).catch((e) => toast(e.message, true)));
  ["#puSup", "#puSt"].forEach((id) => $(id).addEventListener("change", () => loadList(...getRange()).catch((e) => toast(e.message, true))));
  wireTools("pur", () => ({ title: "فواتير الشراء", subtitle: sub + ($("#puSup").value ? ` — ${$("#puSup").selectedOptions[0].text}` : ""), el: $("#puRep"), sheets: [
    { name: "فواتير الشراء", columns: [Tx("المورد"), Tx("رقم الفاتورة"), Dt("التاريخ"), N("الأصناف"), M("الإجمالي"), Tx("الحالة")],
      rows: rows.map((i) => [i.supplier_name || "", i.invoice_no || "", i.invoice_date || dayOf(i.created_at), i.lines, amt(i), STATUS[i.status][0]]) }] }));
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
    btn.disabled = true; btn.textContent = "جاري القراءة…";
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
  expiry_date: "الصلاحية" };
const SRC_LABEL = { read: "مقروء", inferred: "محسوب", ai: "قراءة آلية", user: "أدخله المستخدم", db: "من قاعدة البيانات" };

function fieldInput(line, field, value, { type = "number", step = "0.01" } = {}) {
  const src = line.field_sources[field];
  const conf = line.field_confidence[field];
  const low = src !== "user" && conf !== undefined && conf < 0.8;
  const cls = [low ? "low" : "", src === "inferred" ? "inferred" : "", src === "ai" ? "ai" : ""].join(" ");
  const title = src ? `${SRC_LABEL[src] || src}${conf !== undefined ? ` — ثقة ${Math.round(conf * 100)}%` : ""}` : "";
  const dis = CUR.invoice.status !== "draft" ? "disabled" : "";
  return `<input class="${cls}" data-line="${esc(line.id)}" data-field="${field}" type="${type}"
    ${type === "number" ? `step="${step}" min="0"` : ""} value="${esc(value)}" title="${esc(title)}"
    aria-label="${esc(field)}" ${dis}>
    ${low && !dis ? `<button class="btn small secondary" data-confirm="${esc(line.id)}" data-cfield="${field}">تأكيد</button>` : ""}`;
}

function itemLabel(it) {
  if (!it) return "";
  return [it.name_en || it.name_ar, it.strength, it.dosage_form].filter(Boolean).join(" · ");
}

function lineIssues(lineId) { return CUR.issues.filter((i) => i.line_id === lineId); }

function renderInvoice() {
  const { invoice: inv, lines, issues } = CUR;
  const draft = inv.status === "draft";
  const errs = issues.filter((i) => i.level === "error" && !i.acknowledged);
  const warns = issues.filter((i) => i.level === "warning");
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
      <h1>فاتورة شراء <span class="badge ${STATUS[inv.status][1]}">${STATUS[inv.status][0]}</span>
        ${inv.extraction_method === "ai_vision" ? `<span class="badge b-ai">قراءة آلية — راجع كل القيم</span>` : ""}</h1>
      <a href="#/purchases">رجوع للفواتير</a>
    </div>
    <div class="row">
      <label class="f">المورد <select data-h="supplier_id" ${draft ? "" : "disabled"}>
        <option value="">— اختر —</option></select></label>
      <label class="f">نوع المورد <span class="seg2 small" id="supType"></span></label>
      <label class="f">رقم الفاتورة <input data-h="invoice_no" value="${esc(inv.invoice_no || "")}" ${draft ? "" : "disabled"}></label>
      <label class="f">التاريخ <input data-h="invoice_date" type="date" value="${esc(inv.invoice_date || "")}" ${draft ? "" : "disabled"}></label>
      <label class="f">الإجمالي المطبوع (ج) <input data-h="declared_total" type="number" step="0.01" value="${money(inv.declared_total)}" ${draft ? "" : "disabled"}></label>
      <label class="f">خصم إضافي على الفاتورة (ج) <input data-h="extra_discount" type="number" step="0.01" value="${money(inv.extra_discount)}" ${draft ? "" : "disabled"}></label>
    </div>
    ${inv.supplier_name_raw && !inv.supplier_id ? `<p class="muted">اسم المورد في الملف: ${esc(inv.supplier_name_raw)}</p>` : ""}
  </section>
  <section class="panel">
    <div class="totals">
      <div>الإجمالي المطبوع<b>${money(inv.declared_total) || "—"}</b></div>
      <div>الإجمالي المحسوب<b>${money(inv.computed_total) || "—"}</b></div>
      <div class="${diff == null ? "" : diff === 0 ? "t-ok" : totalErr ? "t-err" : "t-warn"}">${diff == null ? "الفرق" : diff === 0 ? "✓ الإجماليان متطابقان" : totalErr ? "✕ الفرق" : "⚠ الفرق (مقبول)"}<b>${diff == null ? "—" : money(diff)}</b>
        ${totalErr ? `<span>سطر زائد أو سطر ناقص؟ راجع قبل الاعتماد</span>` : ""}</div>
      <div>عدد الأصناف<b>${lines.length}</b></div>
    </div>
    <h2 class="mt">${errs.length ? `⛔ ${errs.length} مشكلة يجب حلها قبل الاعتماد` : "✅ لا توجد مشكلات تمنع الاعتماد"}</h2>
    <ul class="issues">
      ${errs.map((i) => `<li class="err">${i.line_id ? `سطر ${lineNo[i.line_id]}: ` : ""}${esc(i.message)}
        ${i.details?.difference != null ? ` (الفرق ${money(i.details.difference)} ج)` : ""}
        ${i.details?.expected != null ? ` (المتوقع ${money(i.details.expected)})` : ""}
        ${i.code === "low_confidence" && draft ? ` [${esc(FIELD_AR[i.field] || i.field)}] <button class="btn small secondary" data-confirm="${esc(i.line_id)}" data-cfield="${esc(i.field)}">القيمة صحيحة</button>` : ""}
        ${i.code === "unreadable" ? ` [${esc(FIELD_AR[i.field] || i.field)}]` : ""}
        ${["total_mismatch", "line_total_mismatch", "net_price_mismatch"].includes(i.code) && draft && ME.permissions.includes("purchases.approve")
          ? `<button class="btn small secondary" data-ack="${esc(i.code)}" data-ackline="${esc(i.line_id || "")}">قبول الفرق مع ذكر السبب</button>` : ""}</li>`).join("")}
      ${warns.map((i) => `<li class="warn">${i.line_id ? `سطر ${lineNo[i.line_id]}: ` : ""}${esc(i.message)}
        ${i.code === "public_price_changed" ? ` (${money(i.details.old)} ← ${money(i.details.new)})` : ""}
        ${i.code === "short_expiry" ? ` (${i.details.days} يوم)` : ""}</li>`).join("")}
    </ul>
    <div class="row">
      ${draft ? `<button class="btn" id="approve" ${CUR.can_approve && ME.permissions.includes("purchases.approve") ? "" : "disabled"}>اعتماد الفاتورة وإضافتها للمخزون</button>
        ${fileUrl ? `<button class="btn secondary" id="addLine">إضافة سطر ناقص من الملف</button>` : ""}` : ""}
      ${inv.status !== "cancelled" ? `<button class="btn danger" id="cancelInv">إلغاء الفاتورة</button>` : ""}
    </div>
  </section>
  ${draft ? quickAddHtml() : ""}
  <div class="${fileUrl ? "review" : ""}">
    ${fileUrl ? `<section class="panel doc ${["image","pdf"].includes(inv.source_type) ? "" : "compact"}">${doc}</section>` : ""}
    <section class="panel">
      <div class="legend"><span>الحقول المنقطة: قيمة غير مؤكدة تحتاج مراجعة</span>
        <span>المائل: قيمة محسوبة</span><span>مرّر المؤشر على أي خانة لمعرفة مصدرها</span></div>
      <div class="tablewrap"><table class="lines">
        <thead><tr><th>#</th><th>الصنف</th><th>كمية</th><th>بونص</th><th>سعر الجمهور</th><th>خصم %</th>
          <th>الصافي</th><th>الإجمالي</th><th>التشغيلة</th><th>الصلاحية</th><th></th></tr></thead>
        <tbody>${lines.map(renderLine).join("")}</tbody>
      </table></div>
    </section>
  </div>`;
  fillSuppliers(inv.supplier_id, draft);
  if (draft) bindQuickAdd();
  purchaseTabs(inv.id);
}

// fast manual entry: search/scan -> qty, bonus, discount, expiry -> Enter
function quickAddHtml() {
  return `<section class="panel" id="qa"><h2>إضافة صنف للفاتورة</h2>
    <div class="row">
      <label class="f grow">الصنف (اسم أو باركود)<input id="qaQ" autocomplete="off" placeholder="اكتب أو امسح الباركود"></label>
      <label class="f">كمية (علبة)<input id="qaQty" type="number" min="1" step="1" class="narrow"></label>
      <label class="f">بونص<input id="qaBonus" type="number" min="0" step="1" value="0" class="narrow"></label>
      <label class="f">سعر الجمهور<input id="qaPrice" type="number" min="0" step="0.01" class="narrow"></label>
      <label class="f">خصم %<input id="qaDisc" type="number" min="0" max="100" step="0.01" class="narrow"></label>
      <label class="f">الصلاحية (شهر/سنة)<input id="qaExp" placeholder="6-29" class="narrow"></label>
      <label class="f">التشغيلة<input id="qaBatch" class="narrow"></label>
      <button class="btn" id="qaAdd" type="button">إضافة</button>
    </div>
    <div id="qaRes" class="cands"></div><div id="qaPicked" class="muted"></div></section>`;
}

let QA_ITEM = null;
function bindQuickAdd() {
  const q = $("#qaQ");
  if (!q) return;
  const pick = (it) => {
    QA_ITEM = it;
    $("#qaRes").innerHTML = "";
    q.value = itemText(it);
    $("#qaPicked").textContent = `العبوة ${it.pack_size} ${unitName(it)} · الرصيد الحالي ${fmtStock(it.stock_qty, it) || "0"}`;
    if (it.public_price != null) $("#qaPrice").value = (it.public_price / 100).toFixed(2);
    $("#qaQty").focus();
  };
  let t, res = [];
  q.addEventListener("input", () => {
    QA_ITEM = null; clearTimeout(t);
    t = setTimeout(async () => {
      const v = q.value.trim(); if (v.length < 2) { $("#qaRes").innerHTML = ""; return; }
      const found = await findItems(v); res = found.items;
      if (found.hint?.kind === "gs1") {             // 2D code on the pack: item + expiry + batch in one scan
        if (!res.length) { $("#qaRes").innerHTML = `<p class="badge b-warn">⚠ هذا الكود المربع غير مسجل على أي صنف. سجّله من بطاقة الصنف.</p>`; return; }
        pick(res[0]);
        if (found.hint.expiry) { const [y, m] = found.hint.expiry.split("-"); $("#qaExp").value = `${+m}-${y.slice(2)}`; }
        if (found.hint.batch) $("#qaBatch").value = found.hint.batch;
        $("#qaPicked").textContent += found.hint.expiry ? ` · قُرئت الصلاحية والتشغيلة من الكود` : "";
        return;
      }
      if (res.length === 1 && /^\d{6,}$/.test(v)) { pick(res[0]); return; }
      $("#qaRes").innerHTML = hintHtml(found.hint) + (res.map((r, i) => `<button type="button" class="btn small secondary" data-qa="${i}">${itemHtml(r)}
        <span class="muted">${esc(r.strength_norm || "")} · ${money(r.public_price)} ج</span></button>`).join("") || `<p class="muted">لا توجد نتائج</p>`);
      $("#qaRes").querySelectorAll("[data-qa]").forEach((b) => b.addEventListener("click", () => pick(res[+b.dataset.qa])));
    }, 250);
  });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter" && res.length && !QA_ITEM) { e.preventDefault(); pick(res[0]); } });
  const add = async () => {
    if (!QA_ITEM) { toast("اختر الصنف أولًا", true); q.focus(); return; }
    const qty = parseInt($("#qaQty").value, 10);
    if (!(qty > 0)) { toast("اكتب الكمية", true); $("#qaQty").focus(); return; }
    const line = { item_id: QA_ITEM.id, qty, bonus_qty: parseInt($("#qaBonus").value || "0", 10) };
    if ($("#qaPrice").value !== "") line.public_price = toPiasters($("#qaPrice").value);
    if ($("#qaDisc").value !== "") line.discount_bp = toBp($("#qaDisc").value);
    if ($("#qaExp").value.trim()) line.expiry_date = normExpiry($("#qaExp").value);
    if ($("#qaBatch").value.trim()) line.batch_no = $("#qaBatch").value.trim();
    const keepDisc = $("#qaDisc").value;
    QA_ITEM = null;
    await save(`/api/purchases/${CUR.invoice.id}/lines`, { line }, "POST");
    if ($("#qaDisc")) $("#qaDisc").value = keepDisc;     // same supplier discount usually repeats
    $("#qaQ")?.focus();
  };
  $("#qaAdd").addEventListener("click", add);
  ["#qaQty", "#qaBonus", "#qaPrice", "#qaDisc", "#qaExp", "#qaBatch"].forEach((s) =>
    $(s).addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }));
}

function renderLine(l) {
  const iss = lineIssues(l.id);
  const hasErr = iss.some((i) => i.level === "error" && !i.acknowledged);
  const draft = CUR.invoice.status === "draft";
  let match;
  if (l.item_id && l.match_status !== "suggested") {
    match = `<div>${esc(itemLabel(l.item))} <span class="badge b-ok">${l.match_status === "confirmed" ? "مؤكد" : "تلقائي"}</span></div>`;
  } else {
    const c = l.candidates || [];
    match = `<div class="cands">${c.length ? `<select data-pick="${esc(l.id)}" ${draft ? "" : "disabled"}>
      <option value="">— اختر الصنف الصحيح —</option>
      ${c.map((x) => `<option value="${esc(x.item_id)}" ${x.item_id === l.item_id ? "selected" : ""}>${esc(x.label)} (${Math.round(x.score * 100)}%)</option>`).join("")}
    </select>` : `<span class="badge b-err">غير موجود</span>`}
    ${draft ? `<span><button class="btn small secondary" data-search="${esc(l.id)}">بحث</button>
      <button class="btn small secondary" data-newitem="${esc(l.id)}">صنف جديد</button></span>` : ""}</div>`;
  }
  return `<tr class="${hasErr ? "has-err" : ""}">
    <td>${l.line_no}</td>
    <td class="name"><div class="raw">${esc(l.raw_name)}</div>${match}
      ${iss.length ? `<div class="muted">${iss.map((i) => (i.level === "error" ? "⛔ " : "⚠️ ") + esc(i.message)).join("<br>")}</div>` : ""}</td>
    <td data-label="كمية">${fieldInput(l, "qty", l.qty ?? "", { step: "1" })}</td>
    <td data-label="بونص">${fieldInput(l, "bonus_qty", l.bonus_qty ?? 0, { step: "1" })}</td>
    <td data-label="سعر الجمهور">${fieldInput(l, "public_price", money(l.public_price))}</td>
    <td data-label="خصم %">${fieldInput(l, "discount_bp", pct(l.discount_bp))}</td>
    <td data-label="الصافي">${fieldInput(l, "unit_net_price", money(l.unit_net_price))}</td>
    <td data-label="الإجمالي">${fieldInput(l, "line_total", money(l.line_total))}</td>
    <td data-label="التشغيلة">${fieldInput(l, "batch_no", l.batch_no ?? "", { type: "text" })}</td>
    <td data-label="الصلاحية">${fieldInput(l, "expiry_date", l.expiry_date ? l.expiry_date.slice(5, 7) + "/" + l.expiry_date.slice(0, 4) : "", { type: "text" })}</td>
    <td>${draft ? `<button class="btn small secondary" data-del="${esc(l.id)}" aria-label="حذف السطر">حذف</button>` : ""}</td>
  </tr>`;
}

const SUP_TYPE = { company: "شركة", warehouse: "مخزن" };
async function fillSuppliers(selected, draft) {
  const sel = $('[data-h="supplier_id"]');
  const list = await api("/api/suppliers");
  sel.innerHTML = `<option value="">— اختر —</option>` + list.map((s) =>
    `<option value="${esc(s.id)}" ${s.id === selected ? "selected" : ""}>${esc(s.name)}${SUP_TYPE[s.supplier_type] ? ` (${SUP_TYPE[s.supplier_type]})` : ""}</option>`).join("");
  sel.disabled = !draft;
  const cur = list.find((x) => x.id === selected), box = $("#supType");
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

const CONVERT = {
  public_price: toPiasters, unit_net_price: toPiasters, line_total: toPiasters, tax_amount: toPiasters,
  discount_bp: toBp, qty: (v) => (v === "" ? null : parseInt(v, 10)),
  bonus_qty: (v) => (v === "" ? 0 : parseInt(v, 10)), batch_no: (v) => v, expiry_date: (v) => normExpiry(v),
};

async function save(path, body, method = "PATCH") {
  try {
    CUR = await api(path, { method, body: { ...body, version: CUR.invoice.version } });
    renderInvoice();
  } catch (e) {
    toast(e.message, true);
    if (e.code === "stale_version") await viewInvoice(CUR.invoice.id);
  }
}

async function viewInvoice(id) {
  CUR = await api(`/api/purchases/${encodeURIComponent(id)}`);
  renderInvoice();
}

app.addEventListener("change", async (e) => {
  const t = e.target;
  if (!CUR) return;
  const base = `/api/purchases/${CUR.invoice.id}`;
  if (t.dataset.field) {
    await save(`${base}/lines/${t.dataset.line}`, { changes: { [t.dataset.field]: CONVERT[t.dataset.field](t.value) } });
  } else if (t.dataset.h) {
    const f = t.dataset.h;
    const v = ["declared_total", "extra_discount"].includes(f) ? toPiasters(t.value) : (t.value || null);
    await save(base, { changes: { [f]: v } });
  } else if (t.dataset.pick && t.value) {
    await save(`${base}/lines/${t.dataset.pick}`, { changes: { item_id: t.value } });
  }
});

app.addEventListener("click", async (e) => {
  const t = e.target.closest("button");
  if (!t || !CUR) return;
  const base = `/api/purchases/${CUR.invoice.id}`;
  if (t.id === "approve") {
    if (!confirm("اعتماد الفاتورة؟ سيتم إضافة الكميات للمخزون وتحديث التكلفة.")) return;
    await save(`${base}/approve`, {}, "POST");
    toast("تم اعتماد الفاتورة وإضافتها للمخزون");
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
  } else if (t.dataset.ack) {
    const reason = prompt("اكتب سبب قبول الفرق (يُسجَّل باسمك):");
    if (reason) await save(`${base}/acknowledge`, { code: t.dataset.ack, line_id: t.dataset.ackline || null, reason }, "POST");
  } else if (t.dataset.search) {
    const q = prompt("ابحث عن الصنف (اسم أو باركود):");
    if (!q) return;
    const res = await api(`/api/items?q=${encodeURIComponent(q)}`);
    if (!res.length) return toast("لا توجد نتائج", true);
    const pick = prompt(res.slice(0, 9).map((r, i) => `${i + 1}) ${r.name_en || r.name_ar}`).join("\n") + "\n\nاكتب رقم الصنف:");
    const it = res[parseInt(pick, 10) - 1];
    if (it) await save(`${base}/lines/${t.dataset.search}`, { changes: { item_id: it.id } });
  } else if (t.dataset.newitem) {
    const line = CUR.lines.find((l) => l.id === t.dataset.newitem);
    const name = prompt("اسم الصنف الجديد:", line.raw_name);
    if (!name) return;
    const pack = parseInt(prompt("عدد الشرائط/الوحدات في العلبة:", "1"), 10) || 1;
    try {
      CUR = await api(`${base}/lines/${line.id}/new-item`, { method: "POST",
        body: { item: { name_en: name, pack_size: pack }, version: CUR.invoice.version } });
      renderInvoice();
    } catch (err) {
      if (err.code === "possible_duplicate") {
        const d = err.details.duplicates.map((x) => x.label).join("، ");
        if (confirm(`يوجد صنف مشابه: ${d}\nهل أنت متأكد أنه صنف مختلف؟`)) {
          CUR = await api(`${base}/lines/${line.id}/new-item`, { method: "POST",
            body: { item: { name_en: name, pack_size: pack }, force: true, version: CUR.invoice.version } });
          renderInvoice();
        }
      } else toast(err.message, true);
    }
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
        : onAdd ? `<span class="badge b-err">✕ ناقص</span>` : `<a class="btn small secondary" href="#/items/${esc(x.id)}">البطاقة</a>`}</div>`;
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
async function printLabels(list) {
  const page = () => printInFrame(`/labels?items=${list.map(([id, n]) => `${encodeURIComponent(id)}:${n}`).join(",")}`);
  try { LABEL_CFG ||= await api("/api/settings/receipt"); } catch { LABEL_CFG = { label_lang: "browser" }; }
  if (LABEL_CFG.label_lang === "browser") return page();
  try { const r = await api("/api/print/labels", { method: "POST", body: { items: list.map(([id, n]) => ({ id, count: n })) } }); toast(`✓ أُرسل ${r.labels} ملصق إلى الطابعة`); }
  catch (er) { toast(er.message, true); if (er.code === "print_failed") page(); }
}

async function viewItems() {
  const cats = await loadCats(true);
  app.innerHTML = `
  <section class="panel">
    <div class="row between"><h1>الأصناف</h1>
      <div class="row">${canW() ? `<a class="btn" href="#/items/new">+ صنف جديد</a><a class="btn secondary" href="#/categories">الأقسام</a>` : ""}
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
      <td>${itemHtml(it)}${it.shortage_at ? ` <span class="badge b-warn">⚠ ناقص</span>` : ""}</td>
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

async function viewItemEdit(id) {
  if (!canW()) { location.hash = id ? `#/items/${id}` : "#/items"; return; }
  const it = id ? await api(`/api/items/${id}/card`) : { pack_size: 1, base_unit_name: "unit", pack_unit_name: "pack", barcodes: [] };
  const cats = (await loadCats(true)).filter((c) => c.is_active || c.id === it.category_id);
  const canPrice = ME.permissions.includes("items.price") || !id;
  const unitVal = (v, def) => (v === "unit" ? "" : v === "pack" ? "" : v) || def;
  app.innerHTML = `<form class="panel" id="itemForm"><div class="row between"><div><a href="${id ? `#/items/${esc(id)}` : "#/items"}">← رجوع</a>
      <h1>${id ? "تعديل صنف" : "صنف جديد"}</h1></div></div>
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
      <label class="f">اسم الوحدة الصغيرة <input name="base_unit_name" value="${esc(unitVal(it.base_unit_name, "شريط"))}" placeholder="شريط / أمبول / قرص"></label>
      <label class="f">عدد الوحدات في العبوة <input name="pack_size" type="number" min="1" step="1" value="${it.pack_size}" ${id && it.stock_qty ? "disabled" : ""}>
        ${id && it.stock_qty ? `<span class="muted">مقفلة لأن للصنف رصيدًا؛ صحّحها بالجرد</span>` : ""}</label>
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
  const newCodes = [];
  const readCode = (raw) => {     // a 2D code -> its GTIN (as the EAN-13 printed on most packs when it starts with 0)
    const m = raw.trim().replace(/^\](d2|Q3|C1)/, "").match(/^(?:\(01\)|01)(\d{14})/) || raw.match(/\/01\/(\d{8,14})/);
    if (!m) return raw.trim();
    const g = m[1].padStart(14, "0");
    return g.startsWith("0") ? g.slice(1) : g;
  };
  $("#bcNew").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#bcAdd").click(); } });
  $("#bcAdd").addEventListener("click", async () => {
    const code = readCode($("#bcNew").value); if (!code) return;
    if (id) {
      try { await api(`/api/items/${id}/barcode`, { method: "POST", body: { barcode: code } }); viewItemEdit(id); }
      catch (err) { toast(err.message, true); }
    } else { newCodes.push(code); $("#bcList").insertAdjacentHTML("beforeend", `<li><bdi dir="ltr">${esc(code)}</bdi></li>`); $("#bcNew").value = ""; }
  });
  document.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirmInline(b)) return;
    try { await api(`/api/items/${id}/barcode`, { method: "POST", body: { barcode: b.dataset.rm, remove: true } }); viewItemEdit(id); }
    catch (err) { toast(err.message, true); }
  }));
  $("#itemForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, v = (n) => f[n] && !f[n].disabled ? f[n].value.trim() : undefined;
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
    if (id && data.public_price === it.public_price) delete data.public_price;
    if (id && data.pack_size === it.pack_size) delete data.pack_size;
    if (id && (data.strength || "") === (it.strength || it.strength_norm || "")) delete data.strength;     // untouched: don't rewrite
    if (id && (data.dosage_form || "") === (it.dosage_form || "")) delete data.dosage_form;
    try {
      let iid = id;
      if (id) await api(`/api/items/${id}`, { method: "PATCH", body: data });
      else {
        try { iid = (await api("/api/items", { method: "POST", body: { item: data, barcodes: newCodes } })).id; }
        catch (err) {
          if (err.code !== "possible_duplicate") throw err;
          const d = (err.details?.duplicates || []).map((x) => x.name || x.name_en).join("، ");
          if (!confirm(`يوجد صنف مشابه: ${d}\nهل أنت متأكد أنه صنف مختلف؟`)) return;
          iid = (await api("/api/items", { method: "POST", body: { item: data, barcodes: newCodes, force: true } })).id;
        }
      }
      const cat = f.category_id.value || null;
      if (cat !== (it.category_id || null)) await api(`/api/items/${iid}/category`, { method: "POST", body: { category_id: cat } });
      toast("تم الحفظ"); location.hash = `#/items/${iid}`;
    } catch (err) { toast(err.message, true); }
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
  same_name_exists: "عميل بنفس الاسم موجود", duplicate_in_file: "مكرر في الملف", invalid_phone: "رقم تليفون غير صحيح",
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
  <section class="panel"><h2>أسطر تحتاج قرار (${attention.length})</h2><div class="tablewrap"><table>
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
    <p class="muted">الصق نص المنشور، وراجع أرقام التشغيلات المقترحة قبل الحفظ. يقارنها النظام بالتشغيلات المسجلة لديك من فواتير الشراء، ولا يحرّك المخزون من تلقاء نفسه.</p>
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
  <section class="panel"><h2>تشغيلات مطابقة تحتاج قرار (${alerts.length})</h2><div class="tablewrap"><table>
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
  </section>
  <section class="panel"><table><thead><tr><th>الجرد</th><th>النطاق</th><th>الحالة</th><th>التاريخ</th></tr></thead>
    <tbody>${list.map((c) => `<tr><td><a href="#/counts/${esc(c.id)}">${esc(c.title)}</a></td><td>${{ full: "شامل", location: "مواقع", items: "أصناف / قسم" }[c.scope] || esc(c.scope)}</td>
      <td>${{ open: "مفتوح", approved: "معتمد", cancelled: "ملغي" }[c.status]}</td><td>${esc(dayOf(c.created_at))}</td></tr>`).join("")
      || `<tr><td colspan="4" class="muted">لا يوجد</td></tr>`}</tbody></table></section>`;
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
  app.innerHTML = `
  <section class="panel">
    <div class="row between"><h1>تسجيل الجرد</h1><a href="#/counts">رجوع</a></div>
    <p class="muted">امسح الباركود بالقارئ أو اكتب الاسم. الكميات بالوحدة الصغرى (شريط/وحدة).</p>
    <div class="row">
      <label class="f grow">باركود أو اسم الصنف <input id="cntQ" autocomplete="off" autofocus></label>
      ${"BarcodeDetector" in window ? `<button class="btn secondary" id="cntCam" type="button">كاميرا</button>` : ""}
    </div>
    <video id="cntVideo" playsinline hidden class="scanvideo"></video>
    <div id="cntPick"></div>
    <div id="cntItem" class="mt"></div>
  </section>
  ${canManage ? `<section class="panel"><div class="row between"><h2>الفروقات</h2>
    <button class="btn secondary" id="cntVar">تحديث الفروقات</button></div><div id="cntVarBox" class="muted">اضغط تحديث لعرض الفروقات</div></section>` : ""}`;
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
    const qty = parseInt($("#cntQty").value, 10);
    if (Number.isNaN(qty) || qty < 0) return toast("اكتب كمية صحيحة", true);
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
    } catch { toast("تعذر فتح الكاميرا (تحتاج HTTPS أو تصريح الكاميرا)", true); }
  });
  $("#cntVar")?.addEventListener("click", async () => {
    try {
      const [v, R] = await Promise.all([api(`/api/counts/${id}/variances`), stockReasons()]);
      const box = $("#cntVarBox");
      box.className = "";
      box.innerHTML = `<div class="row between"><h2>تقرير الجرد والفروق</h2>${reportTools("count")}</div><div class="totals"><div>أصناف مجرودة<b>${v.summary.counted_lines}</b></div>
        <div>عجز (وحدات)<b>${v.summary.shortage_units}</b></div><div>زيادة (وحدات)<b>${v.summary.excess_units}</b></div>
        <div>أصناف لم تُجرد ولها رصيد<b>${v.summary.uncounted_items}</b></div></div>
        <div class="tablewrap"><table><thead><tr><th>الصنف</th><th>الرصيد بالنظام</th><th>المجرود</th><th>مباع أثناء الجرد</th><th>الفرق</th><th>القيمة</th><th></th></tr></thead>
        <tbody>${v.lines.map((l) => `<tr class="${l.difference ? "has-err" : ""}"><td>${esc(l.label)}</td><td>${l.system_qty}</td>
          <td>${l.counted}</td><td>${l.moves_after_count}</td><td>${l.difference}</td><td>${esc(l.value ?? "")}</td>
          <td>${l.problem ? `<span class="badge b-err">${l.problem === "mixed_levels" ? "جرد بالتشغيلة وبدونها معًا" : "رصيد سالب"}</span>`
            : l.difference < 0 ? `<select data-why="${esc(l.item_id)}|${esc(l.batch_key || "")}" class="noprint"><option value="">السبب: مثل الافتراضي</option>${reasonOpts(R.adjust, null, false)}</select>` : l.difference > 0 ? "زيادة وُجدت" : ""}</td></tr>`).join("")}</tbody></table></div>
        <div class="row mt noprint"><label class="f">سبب العجز (الافتراضي لكل الأصناف) <select id="cntWhy">${reasonOpts(R.adjust, "lost", false)}</select></label>
          ${v.summary.uncounted_items ? `<label class="f">الأصناف التي لم تُجرد (${v.summary.uncounted_items}) <select id="cntZero"><option value="">اتركها كما هي</option>${reasonOpts(R.adjust, null, false).replace(/<option value="/g, '<option value="zero:')}</select></label>` : ""}
          <button class="btn" id="cntApprove">اعتماد التسويات</button></div>`;
      wireTools("count", () => ({ title: "تقرير الجرد", subtitle: `عجز ${v.summary.shortage_units} وحدة · زيادة ${v.summary.excess_units} وحدة · لم يُجرد ${v.summary.uncounted_items} صنف`, el: box, sheets: [
        { name: "الجرد", columns: [Tx("الصنف"), N("الرصيد بالنظام"), N("المجرود"), N("مباع أثناء الجرد"), N("الفرق"), { label: "القيمة (ج)", type: "num" }, Tx("ملاحظة")],
          rows: v.lines.map((l) => [l.label, l.system_qty, l.counted, l.moves_after_count, l.difference, l.value ?? "", l.problem || ""]) }] }));
      $("#cntApprove").addEventListener("click", async () => {
        const reasons = {};
        box.querySelectorAll("[data-why]").forEach((sel) => { if (sel.value) reasons[sel.dataset.why] = sel.value; });
        const z = $("#cntZero")?.value || "", zero = z.startsWith("zero:");
        let reason = null;
        if (zero) { reason = prompt(`سيُصفَّر ${v.summary.uncounted_items} صنف لم يُجرد. اكتب ملاحظة (5 حروف على الأقل):`); if (!reason) return; }
        if (!confirm("اعتماد الجرد وتسجيل التسويات في المخزون؟")) return;
        try {
          await api(`/api/counts/${id}/approve`, { method: "POST", body: { zero_uncounted: zero, reason, reasons, shortage_reason: $("#cntWhy").value, zero_reason: zero ? z.slice(5) : "lost" } });
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
async function viewSystem() {
  if (!ME.permissions.includes("settings.manage")) { app.innerHTML = `<section class="panel"><p>هذه الصفحة للمالك فقط.</p></section>`; return; }
  app.innerHTML = `<section class="panel"><h1>النسخ الاحتياطي</h1><div id="bkBox"><p class="muted">…</p></div></section>
    <section class="panel"><h1>الهاتف المحمول على شبكة الصيدلية</h1><div id="lanBox"></div></section>
    <section class="panel"><h1>شكل الفاتورة المطبوعة والملصقات</h1><div id="rcBox"></div></section>
    <section class="panel"><h1>مساحة البيانات ومدة الاحتفاظ بها</h1><div id="stoBox"><p class="muted">جارٍ الحساب…</p></div></section>
    <section class="panel"><h1>أجهزة البطاقات وطرق التحويل</h1><div id="chBox"></div></section>
    <section class="panel"><h1>البرنامج</h1><div id="sysBox"><p class="muted">جارٍ البحث عن تحديثات…</p></div></section>`;
  renderBackup(await api("/api/backup"));
  renderChannels((await api("/api/payment-channels")).channels);
  renderReceiptCfg(await api("/api/settings/receipt"));
  renderLan(await api("/api/system/network"));
  const st = await api("/api/system/update");
  $("#sysBox").innerHTML = `
    <div class="totals"><div>الإصدار<b>${esc(st.version)}</b></div><div>النسخة المثبتة<b>${st.installed ? esc(st.installed.slice(0, 7)) : "غير معروفة"}</b></div>
      <div>آخر نسخة<b>${esc((st.latest || "—").slice(0, 7))}</b></div></div>
    ${st.error ? `<p class="badge b-err">${esc(st.error)}</p>` : st.available
      ? `<p>يوجد تحديث جديد. يأخذ التحديث نسخة احتياطية من بياناتك أولًا، ولا تُمَس بياناتك.</p>
         <button class="btn" id="updGo">حدّث البرنامج الآن</button>`
      : `<p class="badge b-ok">البرنامج محدّث</p>`}
    <pre id="updOut" class="muted" hidden></pre>`;
  api("/api/system/storage").then((x) => {
    const mb = (n) => `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0)} ميجا`;
    $("#stoBox").innerHTML = `<div class="totals"><div>قاعدة البيانات<b>${mb(x.db)}</b><span>${x.since ? `تحتوي على مبيعات منذ ${esc(x.since)}` : ""}</span></div>
      <div>صور الفواتير والتحويلات<b>${mb(x.uploads)}</b></div><div>النسخ الاحتياطية<b>${mb(x.backups)}</b></div></div>
      <p class="mt"><b>ما لا يُحذف أبدًا:</b> فواتير البيع والشراء، وحسابات العملاء والموردين، والخزينة والمصروفات، وحركة المخزون، وسجل العمليات. يُلزم القانون بحفظ الدفاتر سنوات، وحجمها صغير (تزيد الصيدلية النشطة نحو 20–40 ميجا في السنة).</p>
      <p><b>ما يُنظَّف تلقائيًا كل يوم:</b> الإشعارات الأقدم من ${x.policy.notifications_days} يومًا، وجلسات الدخول المنتهية بعد ${x.policy.sessions_days} يومًا، والنسخ الاحتياطية التلقائية الزائدة (تبقى آخر 10 نسخ + نسخة لكل يوم لمدة أسبوع + نسخة لكل أسبوع لمدة شهر).</p>
      <p class="muted">النسخ الاحتياطية مضغوطة (تشغل نحو عُشر حجم قاعدة البيانات). إذا قلّت المساحة يومًا، يمكن عمل أرشيف مضغوط للسنوات القديمة — وهذا يحتاج إلى قرارك.</p>
      <p class="muted">عدد الفواتير: ${x.rows.sales} · سطور البيع: ${x.rows.sale_lines} · فواتير الشراء: ${x.rows.purchase_invoices} · حركات المخزون: ${x.rows.stock_movements}</p>`;
  }).catch((e) => { $("#stoBox").innerHTML = `<p class="badge b-err">✕ ${esc(e.message)}</p>`; });
  $("#updGo")?.addEventListener("click", async () => {
    const b = $("#updGo"); b.disabled = true; b.textContent = "جارٍ التحديث… (دقيقة أو دقيقتان)";
    try {
      const r = await api("/api/system/update", { method: "POST", body: {} });
      $("#updOut").hidden = false; $("#updOut").textContent = r.output;
      if (r.restarting) {
        b.textContent = "يعيد البرنامج تشغيل نفسه…";
        const wait = async () => { try { await fetch("/api/health"); location.reload(true); } catch { setTimeout(wait, 1500); } };
        setTimeout(wait, 4000);
      } else { b.textContent = "اكتمل. أغلق البرنامج وافتحه مرة أخرى"; }
    } catch (e) { b.disabled = false; b.textContent = "حدّث البرنامج الآن"; toast(e.message, true); }
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
async function renderReceiptCfg(c) {
  const order = (await api("/api/settings/sales")).line_order;
  const pr = await api("/api/print/printers").catch(() => ({ printers: [], shaping: true }));
  const chk = (k, t) => `<label class="chk"><input type="checkbox" name="${k}" ${c[k] ? "checked" : ""}> ${t}</label>`;
  $("#rcBox").innerHTML = `<form id="rcForm" class="formgrid">
    <label class="f">مقاس ورق الإيصال <select name="paper"><option value="80" ${c.paper === 80 ? "selected" : ""}>80 مم (الافتراضي)</option><option value="58" ${c.paper === 58 ? "selected" : ""}>58 مم</option></select></label>
    <label class="f">اسم الصيدلية على الفاتورة (فارغ = الاسم المسجل) <input name="title" value="${esc(c.title)}"></label>
    <label class="f">سطر 1 (العنوان) <input name="l0" value="${esc(c.lines[0] || "")}"></label>
    <label class="f">سطر 2 (التليفون) <input name="l1" value="${esc(c.lines[1] || "")}"></label>
    <label class="f">آخر سطر <input name="footer" value="${esc(c.footer)}"></label>
    <label class="f">أسماء الأصناف <select name="names"><option value="en" ${c.names === "en" ? "selected" : ""}>إنجليزي</option><option value="ar" ${c.names === "ar" ? "selected" : ""}>عربي</option><option value="both" ${c.names === "both" ? "selected" : ""}>الاثنان</option></select></label>
    <label class="f">ترتيب الأصناف في الفاتورة (على الشاشة والحفظ والطباعة) <select name="line_order"><option value="alpha" ${order === "alpha" ? "selected" : ""}>أبجدي A→Z (الافتراضي — أسرع في التحضير)</option><option value="entry" ${order === "entry" ? "selected" : ""}>كما أُدخلت</option></select></label>
    <fieldset class="lblp"><legend>طابعة الإيصال والدرج</legend>
      <label class="f">الطابعة <input name="receipt_target" list="prnList" dir="ltr" value="${esc(c.receipt_target || "")}" placeholder="فارغ = الطباعة من المتصفح"></label>
      <datalist id="prnList">${pr.printers.map((n) => `<option value="win:${esc(n)}">`).join("")}</datalist>
      <p class="muted">اختر طابعة الإيصال من القائمة (طابعات ويندوز على هذا الجهاز) أو اكتب tcp:IP:9100 لطابعة شبكة. مع الطباعة المباشرة يُفتح الدرج تلقائيًا مع كل بيع نقدي. فارغ = الإيصال يُطبع من المتصفح والدرج لا يُفتح.</p>
      ${pr.shaping ? "" : `<p><b>⚠ تشكيل الحروف العربية (raqm) غير متاح على هذا الجهاز: الإيصال سيُطبع من المتصفح، والدرج سيُفتح فقط لو الطابعة مختارة. الحل: إعادة التثبيت بآخر مثبّت.</b></p>`}
      <div class="row"><button type="button" class="btn small secondary" id="rcTest">اطبع صفحة تجربة</button><button type="button" class="btn small secondary" id="rcKick">افتح الدرج تجربة</button></div></fieldset>
    <label class="f">مقاس ملصق الباركود <select name="label_size">${["38x25", "50x30", "58x40"].map((v) => `<option value="${v}" ${c.label_size === v ? "selected" : ""}>${v.replace("x", " × ")} مم</option>`).join("")}</select></label>
    <fieldset class="lblp"><legend>طابعة الملصقات</legend>
      <label class="f">النوع <select name="label_lang"><option value="browser" ${c.label_lang === "browser" ? "selected" : ""}>من المتصفح (أي طابعة)</option>
        <option value="tspl" ${c.label_lang === "tspl" ? "selected" : ""}>Xprinter / TSC (TSPL)</option><option value="zpl" ${c.label_lang === "zpl" ? "selected" : ""}>Zebra (ZPL)</option></select></label>
      <label class="f">مكان الطابعة <input name="label_target" list="prnList" dir="ltr" value="${esc(c.label_target || "")}" placeholder="win:Xprinter XP-365B  أو  tcp:192.168.1.50:9100"></label>
      <label class="f">دقة الطابعة <select name="label_dpi"><option value="203" ${c.label_dpi === 203 ? "selected" : ""}>203 (الأشهر)</option><option value="300" ${c.label_dpi === 300 ? "selected" : ""}>300</option></select></label>
      <label class="f">المسافة بين الملصقات (مم) <input name="label_gap" type="number" min="0" max="10" step="1" value="${c.label_gap ?? 2}"></label>
      <p class="muted">اسم طابعة ويندوز كما يظهر في "الأجهزة والطابعات". الطباعة المباشرة لم تُجرَّب بعد على طابعة حقيقية؛ وعند أي مشكلة تُفتح صفحة الطباعة من المتصفح.</p></fieldset>
    <div>${chk("show_cashier", "اسم البائع")}${chk("show_customer", "اسم العميل")}${chk("show_expiry", "صلاحية كل صنف")}${chk("show_return_policy", "سياسة المرتجع (والأصناف التي لا يُقبل إرجاعها)")}</div>
    <div class="row"><button class="btn">احفظ</button><a href="#" id="rcTry">اطبع آخر فاتورة كتجربة</a></div></form>`;
  LABEL_CFG = c;
  $("#rcForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try { await api("/api/settings/sales", { method: "PUT", body: { line_order: f.line_order.value } });
      renderReceiptCfg(await api("/api/settings/receipt", { method: "PUT", body: { paper: +f.paper.value, title: f.title.value, lines: [f.l0.value, f.l1.value],
      footer: f.footer.value, names: f.names.value, label_size: f.label_size.value, label_lang: f.label_lang.value, label_target: f.label_target.value.trim(), receipt_target: f.receipt_target.value.trim(),
      label_dpi: +f.label_dpi.value, label_gap: +f.label_gap.value || 0, show_cashier: f.show_cashier.checked, show_customer: f.show_customer.checked,
      show_expiry: f.show_expiry.checked, show_return_policy: f.show_return_policy.checked } })); toast("تم الحفظ"); }
    catch (err) { toast(err.message, true); }
  });
  $("#rcTry").addEventListener("click", async (e) => {
    e.preventDefault();
    const last = (await api("/api/sales"))[0];
    if (!last) return toast("لا توجد فواتير اليوم", true);
    printInFrame(`/receipt/${last.id}`);
  });
  $("#rcTest").addEventListener("click", async () => {
    try { await api("/api/print/test", { method: "POST", body: { target: $("#rcForm").receipt_target.value.trim() } }); toast("تم الإرسال للطابعة"); }
    catch (err) { toast(err.message, true); }
  });
  $("#rcKick").addEventListener("click", async () => {
    // the drawer opens on the SAVED printer (the one the cashier will use)
    try { await api("/api/print/drawer", { method: "POST", body: { reason: "تجربة الدرج من الإعدادات" } }); toast("تم إرسال أمر فتح الدرج"); }
    catch (err) { toast(err.message, true); }
  });
}
function renderChannels(list) {
  CHANNELS = null;
  $("#chBox").innerHTML = `<p class="muted">ما يُضاف هنا يظهر في شاشة البيع عند اختيار بطاقة أو تحويل، ويُجمَع تلقائيًا عند إغلاق الوردية.</p>
    <table><thead><tr><th>النوع</th><th>الاسم</th><th>مفعّل</th><th></th></tr></thead><tbody>
    ${list.map((c) => `<tr data-id="${esc(c.id)}"><td>${c.kind === "card" ? "جهاز بطاقات" : "تحويل / محفظة"}</td><td><input name="name" value="${esc(c.name)}"></td>
      <td><input type="checkbox" name="is_active" ${c.is_active ? "checked" : ""}></td><td><button class="btn small secondary" data-chsave>احفظ</button></td></tr>`).join("")}</tbody></table>
    <form class="row mt" id="chNew"><label class="f">النوع <select name="kind"><option value="card">جهاز بطاقات</option><option value="transfer">تحويل / محفظة</option></select></label>
      <label class="f">الاسم <input name="name" required placeholder="مثال: جهاز البنك الأهلي"></label><button class="btn small">أضف</button></form>`;
  document.querySelectorAll("[data-chsave]").forEach((b) => b.addEventListener("click", async () => {
    const tr = b.closest("tr");
    try { renderChannels((await api(`/api/payment-channels/${tr.dataset.id}`, { method: "PATCH", body: { name: tr.querySelector("[name=name]").value, is_active: tr.querySelector("[name=is_active]").checked } })).channels); toast("تم الحفظ"); }
    catch (err) { toast(err.message, true); }
  }));
  $("#chNew").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { renderChannels((await api("/api/payment-channels", { method: "POST", body: { kind: e.target.kind.value, name: e.target.name.value } })).channels); }
    catch (err) { toast(err.message, true); }
  });
}
const MB = (b) => (b < 1048576 ? `${Math.max(1, Math.round(b / 1024))} ك.ب` : `${(b / 1048576).toFixed(1)} م.ب`);
const BK_INT = [[15, "ربع ساعة"], [30, "نصف ساعة"], [60, "ساعة"], [360, "6 ساعات"]];
const BK_REASON = { schedule: "في موعدها", manual: "يدوي", shift_close: "إغلاق الوردية", shutdown: "إغلاق البرنامج" };
function renderBackup(b) {
  const c = b.settings, st = b.state, ago = st.last_at ? Math.round((Date.now() - new Date(st.last_at)) / 60000) : null;
  $("#bkBox").innerHTML = `
    ${st.last_ok === false ? `<p class="badge b-err">✕ آخر محاولة فشلت: ${esc(st.last_error || "")}</p>` : ""}
    ${st.last_at ? `<p class="badge b-ok">✓ آخر نسخة من ${ago < 60 ? `${ago} دقيقة` : `${Math.round(ago / 60)} ساعة`} — ${MB(st.last_bytes || 0)} — ${esc(BK_REASON[st.last_reason] || "")}</p>`
      : `<p class="badge b-warn">⚠ لم تُعمل أي نسخة تلقائية بعد</p>`}
    <form id="bkForm" class="bk">
      <div><b>كل كم من الوقت</b><div class="seg" role="radiogroup">${BK_INT.map(([v, t]) => `<label><input type="radio" name="interval_min" value="${v}" ${c.interval_min === v ? "checked" : ""}> ${t}${v === 60 ? " (الافتراضي)" : ""}</label>`).join("")}</div>
        <label class="chk"><input type="checkbox" name="on_shift_close" ${c.on_shift_close ? "checked" : ""}> ونسخة أيضًا عند إغلاق الوردية</label>
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
          : `<p class="badge b-ok">✓ آخر نسخة خارج الجهاز ${esc((st.offsite_at || "").replace("T", " ").slice(0, 16))}</p>`}</div>
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
  try { const st = await api("/api/system/update"); $("#updBadge").hidden = !st.available; } catch { /* offline */ }
}

// ------------------------------------------------------------------ sales history / returns
const SALE_ST = { completed: "مكتملة", voided: "ملغاة" };
const localDay = (d = new Date()) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const LOC = () => (I18N.on ? "en-GB" : "ar-EG");   // dates and times follow the screen language
const hhmm = (iso) => (iso ? new Date(iso).toLocaleTimeString(LOC(), { hour: "2-digit", minute: "2-digit" }) : "");
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
  let R = null, item = null, open = null;
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
  const load = async () => {
    const f = $("#iF").value, t = $("#iT").value;
    if (!f || !t) return toast("اختر الفترة", true);
    $("#iBox").innerHTML = `<p class="muted">جارٍ التحميل…</p>`; $("#iDetail").innerHTML = ""; sel.clear(); open = null;
    const qs = new URLSearchParams({ from: f, to: t, types: [...types].join(","), q: $("#iQ").value.trim(), item: item?.id || "", user: $("#iUser").value, lines: "1" });
    try { R = await api(`/api/reports/invoices?${qs}`); } catch (er) { $("#iBox").innerHTML = ""; return toast(er.message, true); }
    if ($("#iUser").options.length === 1) $("#iUser").insertAdjacentHTML("beforeend", R.sellers.map((u) => `<option value="${esc(u.id)}">${esc(u.name)}</option>`).join(""));
    draw();
  };
  const sub = () => `${R.from.replace("T", " ")} ← ${R.to.replace("T", " ")}`;
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
    $("#iXl").addEventListener("click", () => exportXlsx("فواتير البيع", sub(), sheets()));
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
      <div class="tablewrap"><table><thead><tr><th>الصنف</th><th>الكمية</th><th>المُرتجع منها</th><th>قبل الخصم</th><th>الخصم</th><th>بعد الخصم</th>${can("items.write") ? `<th class="noprint"></th>` : ""}</tr></thead><tbody>
      ${(r.items || []).map((x) => `<tr><td>${itemHtml(x)}</td><td>${esc(fmtStock(x.qty, x))}</td><td>${x.returned ? esc(fmtStock(x.returned, x)) : ""}</td><td class="num">${money(x.gross)}</td>
        <td>${x.discount_bp ? `${pct(x.discount_bp)}%${x.contract ? " (تعاقد)" : ""}` : ""}</td><td class="num"><b>${money(x.net)}</b></td>
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
      if (mode === "sel") { const keep = new Set([...sel].map(String)); copy.querySelectorAll("tbody tr[data-i]").forEach((tr) => { if (!keep.has(tr.dataset.i)) tr.remove(); }); copy.querySelector("tfoot")?.remove(); copy.querySelector(".totals")?.remove(); }
      box.append(copy);
    }
    printPreview(box, mode === "full" ? "فواتير البيع بالأصناف" : mode === "sel" ? "الفواتير المحددة" : "فواتير البيع", sub());
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

async function viewSale(id) {
  const s = await api(`/api/sales/${id}`);
  const can = (p) => ME?.permissions?.includes(p);
  const returnable = (l) => l.allocations.reduce((t, a) => t + a.qty - (a.returned_qty || 0), 0);
  const fq = (q, l) => fmtStock(q, { pack_size: l.item.pack_size, base_unit_name: l.item.base_unit });
  app.innerHTML = `
  <section class="panel"><div class="row between"><h1>فاتورة بيع ${s.sale_no ?? ""} ${s.status === "voided" ? `<span class="badge b-err">ملغاة</span>` : ""}</h1>
    <div class="row"><a class="btn secondary" href="/receipt/${s.id}" target="_blank">طباعة</a><a href="#/sales">رجوع للفواتير</a></div></div>
    <div class="totals"><div>الإجمالي<b>${money(s.total)}</b></div><div>نقدي<b>${money(s.paid_cash)}</b></div>
      <div>بطاقة<b>${money(s.paid_card)}</b></div>${s.paid_transfer ? `<div>تحويل<b>${money(s.paid_transfer)}</b></div>` : ""}<div>آجل<b>${money(s.on_credit)}</b></div><div>مرتجع<b>${money(s.returned_total)}</b></div>
      <div>العميل<b>${esc(s.customer?.name || "—")}</b></div></div>
    ${s.void_reason ? `<p class="muted">سبب الإلغاء: ${esc(s.void_reason)}</p>` : ""}</section>
  ${s.returns.length ? `<section class="panel"><h2>المرتجعات</h2><table><thead><tr><th>اليوم</th><th>المبلغ</th><th>رُدّ</th><th>السبب</th><th>سجّله</th><th></th></tr></thead><tbody>
    ${s.returns.map((r) => `<tr><td>${esc(dayOf(r.created_at))} ${hhmm(r.created_at)}</td><td class="num">${money(r.total)}</td><td>${{ cash: "نقدي", card: "بطاقة", credit: "على الحساب" }[r.refund_method]}</td>
      <td>${esc(r.reason)}</td><td>${esc(r.by_name || "")}</td><td><a class="btn small secondary" href="/return-receipt/${esc(r.id)}" target="_blank">🖨 إيصال المرتجع</a></td></tr>`).join("")}</tbody></table></section>` : ""}
  ${s.paid_transfer || s.attachments.length ? `<section class="panel"><h2>صورة التحويل ${s.paid_transfer && !s.attachments.length ? `<span class="badge b-warn">⚠ لا توجد صورة</span>` : ""}</h2>
    <div class="proofs">${s.attachments.map((a) => `<figure>${a.mime_type === "application/pdf" ? `<a href="/api/attachments/${esc(a.id)}" target="_blank">📄 PDF</a>`
      : `<a href="/api/attachments/${esc(a.id)}" target="_blank"><img src="/api/attachments/${esc(a.id)}" alt="صورة التحويل" loading="lazy"></a>`}
      <figcaption><span class="badge ${{ ok: "b-ok", problem: "b-err", pending: "b-warn" }[a.review]}">${{ ok: "✓ تمت مراجعتها", problem: "✕ بها مشكلة", pending: "⚠ بانتظار المراجعة" }[a.review]}</span>
        ${a.review_note ? `<div>${esc(a.review_note)}</div>` : ""}<div class="muted small">${esc(a.by_name || "")}${a.reviewed_name ? ` · راجعها ${esc(a.reviewed_name)}` : ""}</div>
        ${can("sales.totals") && a.review !== "ok" ? `<button class="btn small" data-rvok="${esc(a.id)}">✓ وصل المبلغ</button>` : ""}
        ${can("sales.totals") && a.review !== "problem" ? `<button class="btn small secondary" data-rvbad="${esc(a.id)}">✕ بها مشكلة</button>` : ""}</figcaption></figure>`).join("")}</div>
    ${s.status !== "voided" && can("sales.create") ? `<label class="f mt">أضف صورة التحويل <input type="file" id="proofUp" accept="image/*,application/pdf"></label>` : ""}</section>` : ""}
  <section class="panel"><h2>الأصناف</h2><div class="tablewrap"><table><thead><tr><th>الصنف</th><th>الكمية</th><th>الصافي</th>
    <th>التشغيلات</th>${s.status === "completed" && can("sales.return") ? "<th>كمية المرتجع (بالوحدة)</th>" : ""}</tr></thead><tbody>
    ${s.lines.map((l) => `<tr><td>${esc(l.item.name)}</td><td>${esc(fq(l.qty_base, l))}</td><td>${money(l.net)}</td>
      <td class="muted">${l.allocations.map((a) => `${esc(a.expiry_date ? a.expiry_date.slice(0, 7) : "—")}: ${a.qty}${a.returned_qty ? ` (المُرتجع ${a.returned_qty})` : ""}`).join("، ")}</td>
      ${s.status === "completed" && can("sales.return") ? `<td>${returnable(l) ? `<input type="number" min="0" max="${returnable(l)}" step="1" value="0" data-ret="${l.id}" class="narrow">
        <span class="muted">من ${returnable(l)}</span>` : `<span class="muted">إرجاع الكل</span>`}</td>` : ""}</tr>`).join("")}
    </tbody></table></div>
    ${s.status === "completed" && can("sales.return") ? `<div class="row mt"><label class="f">طريقة الاسترداد<select id="rMethod">
      <option value="cash">نقدًا من الخزينة</option><option value="card">بطاقة</option>${s.customer_id ? `<option value="credit">على حساب العميل</option>` : ""}</select></label>
      <label class="f grow">سبب المرتجع<input id="rReason" placeholder="مثال: العميل غيّر رأيه"></label>
      <button class="btn" id="rGo">تسجيل المرتجع</button></div>` : ""}
    ${s.status === "completed" && can("sales.void") && !s.returned_total ? `<div class="row mt"><label class="f grow">إلغاء الفاتورة (في اليوم نفسه فقط) — السبب
      <input id="vReason"></label><button class="btn danger" id="vGo">إلغاء الفاتورة</button></div>` : ""}
  </section>`;
  $("#rGo")?.addEventListener("click", async () => {
    const lines = {};
    app.querySelectorAll("[data-ret]").forEach((i) => { const v = parseInt(i.value, 10); if (v > 0) lines[i.dataset.ret] = v; });
    if (!Object.keys(lines).length) { toast("اكتب كمية المرتجع", true); return; }
    try {
      const r = await api(`/api/sales/${id}/return`, { method: "POST", body: { lines, refund_method: $("#rMethod").value, reason: $("#rReason").value } });
      toast(`تم تسجيل المرتجع: ${money(r.refund)} ج — اطبع إيصاله من "المرتجعات" أسفل`); viewSale(id);
    } catch (e) { toast(e.message, true); }
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
  app.querySelectorAll("[data-rvbad]").forEach((b) => b.addEventListener("click", async () => {
    const note = prompt("ما المشكلة؟ (مثال: المبلغ لم يصل / المبلغ أقل)"); if (!note) return;
    try { await api(`/api/attachments/${b.dataset.rvbad}/review`, { method: "POST", body: { ok: false, note } }); viewSale(id); } catch (err) { toast(err.message, true); }
  }));
  $("#vGo")?.addEventListener("click", async () => {
    if (!confirmInline($("#vGo"))) return;
    try { await api(`/api/sales/${id}/void`, { method: "POST", body: { reason: $("#vReason").value } }); toast("تم إلغاء الفاتورة"); viewSale(id); }
    catch (e) { toast(e.message, true); }
  });
}
// two-click confirmation without browser dialogs
function confirmInline(btn) {
  if (btn.dataset.armed) return true;
  btn.dataset.armed = "1"; const t = btn.textContent; btn.textContent = "متأكد؟ اضغط مرة أخرى";
  setTimeout(() => { delete btn.dataset.armed; btn.textContent = t; }, 4000);
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
        ${rows.map((x) => `<tr><td>${esc(x.name)}<div class="raw">${esc(x.company || "")} · ${x.code}</div></td><td>${x.qty}</td>
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

// ------------------------------------------------------------------ procurement
const CONF_AR = { high: ["عالية", "b-ok"], medium: ["متوسطة", "b-warn"], low: ["منخفضة", "b-err"] };

async function viewProcurement() {
  app.innerHTML = `
  <section class="panel">
    <h1>اقتراح الطلبية</h1>
    <p class="muted">الحساب: متوسط الاستهلاك × (أيام التوريد + أيام التغطية + أيام الأمان) − الرصيد − ما هو في الطريق. كل سطر مكتوب معه سببه. ولا يطلب النظام أي شيء من تلقاء نفسه.</p>
    <form id="prForm" class="row">
      <label class="f">تغطية (يوم) <input name="cover_days" type="number" value="30" min="1"></label>
      <label class="f">مدة التوريد (يوم) <input name="lead_days" type="number" value="2" min="0"></label>
      <label class="f">أمان (يوم) <input name="safety_days" type="number" value="5" min="0"></label>
      <label class="f">فترة الاستهلاك (يوم) <input name="window_days" type="number" value="90" min="7"></label>
      <button class="btn" type="submit">احسب</button>
      <a class="btn secondary" id="prXlsx" href="#">تحميل Excel للطلبية</a>
    </form>
  </section>
  <section class="panel"><div class="tablewrap"><table>
    <thead><tr><th>الصنف</th><th>الرصيد</th><th>استهلاك/يوم</th><th>يكفي (يوم)</th><th>المقترح (علبة)</th><th>الثقة</th><th>الأسباب</th></tr></thead>
    <tbody id="prBody"><tr><td colspan="7" class="muted">جاري الحساب…</td></tr></tbody></table></div></section>
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
  const params = () => new URLSearchParams(new FormData($("#prForm"))).toString();
  const load = async () => {
    $("#prXlsx").href = `/api/procurement/suggestions.xlsx?${params()}`;
    const rows = await api(`/api/procurement/suggestions?${params()}`);
    $("#prBody").innerHTML = rows.map((r) => `<tr><td>${esc(r.label)}</td><td>${r.on_hand}</td><td>${r.daily_usage}</td>
      <td>${r.days_of_cover ?? "—"}</td><td><b>${r.suggested_packs}</b></td>
      <td><span class="badge ${CONF_AR[r.confidence][1]}">${CONF_AR[r.confidence][0]}</span></td>
      <td class="muted">${r.reasons.map(esc).join("<br>")}</td></tr>`).join("")
      || `<tr><td colspan="7" class="muted">لا توجد أصناف تحتاج إلى طلب (أو لا توجد بيانات استهلاك بعد — ارفع مبيعات شهر من الأسفل)</td></tr>`;
  };
  $("#prForm").addEventListener("submit", (e) => { e.preventDefault(); load().catch((er) => toast(er.message, true)); });
  load().catch((er) => toast(er.message, true));
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
const POS = { method: "cash", channel: null, split: false, parts: [] };
const METHODS = [["cash", "نقدي"], ["card", "بطاقة"], ["transfer", "تحويل"], ["credit", "آجل"]];
// screenshot of the transfer, saved with the invoice for the owner to check (optional)
const proofInput = (s) => `<label class="f mt proof">📎 صورة التحويل ${(s.attachments || []).length ? `<span class="badge b-ok">✓ ${(s.attachments || []).length} صورة</span>` : `<span class="muted">(اختياري — للمراجعة)</span>`}
  <input type="file" id="posProof" accept="image/*,application/pdf"></label>`;
const qtyText = (q, it) => (it.pack_size > 1 && q % it.pack_size === 0 ? `${q / it.pack_size} ${esc(it.pack_unit && it.pack_unit !== "pack" ? it.pack_unit : "علبة")}`
  : it.pack_size > 1 && q > it.pack_size ? `${Math.floor(q / it.pack_size)} علبة + ${q % it.pack_size} ${esc(unitName({ base_unit_name: it.base_unit }))}`
  : `${q} ${esc(unitName({ base_unit_name: it.base_unit }))}`);

async function openDraft(id) {
  SALE = id ? await api(`/api/sales/${id}`) : await api("/api/sales", { method: "POST", body: {} });
  POS.method = "cash"; POS.channel = null; POS.split = false; POS.parts = [];
}

async function viewPos() {
  CHANNELS = CHANNELS || (await api("/api/payment-channels")).channels.filter((c) => c.is_active);
  const drafts = (await api("/api/sales/drafts")).drafts;
  if (!SALE || SALE.status !== "draft" || !drafts.some((d) => d.id === SALE.id)) {
    await openDraft(drafts.length ? drafts[drafts.length - 1].id : null);
  }
  const tabs = (await api("/api/sales/drafts")).drafts;
  app.innerHTML = `
  <nav class="tabs" aria-label="الفواتير المفتوحة">${tabs.map((d, i) => `<button class="tab ${d.id === SALE.id ? "on" : ""}" data-tab="${esc(d.id)}" data-n="${i + 1}">
      فاتورة ${i + 1}${d.kind === "delivery" ? " · توصيل" : ""} · ${d.lines ? `${d.lines} صنف` : "فارغة"}
      <span class="x" data-close="${esc(d.id)}" title="أغلق هذه الفاتورة" aria-label="إغلاق">✕</span></button>`).join("")}
    <button class="tab add" id="tabNew" title="فاتورة جديدة (F2)">+ فاتورة <kbd>F2</kbd></button></nav>
  <div class="pos">
    <section class="pos-main">
      <label class="f">امسح الباركود أو الكود المربع، أو اكتب أول حروف الصنف
        <input id="posQ" class="big" autocomplete="off" autofocus></label>
      <div id="posRes" class="results"></div>
      <div id="posQuick"></div>
      <section class="panel" id="posCart"></section>
      <p class="keys"><kbd>Enter</kbd> يضيف علبة · <kbd>Shift+Enter</kbd> يضيف وحدة · <kbd>F9</kbd> دفع · <kbd>F2</kbd> فاتورة جديدة</p>
    </section>
    <aside class="pos-side" id="posSide"></aside>
  </div>
  <p class="muted mt">${ME.permissions.includes("sales.totals") ? `<a href="#" id="posSummary">ملخص اليوم</a> · ` : ""}<a href="#" id="posShift">الخزينة</a> · <a href="#" id="posDeliv">التوصيلات المفتوحة</a></p><div id="posSum"></div>`;
  renderCart(); renderSide(); renderQuick();
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
  let res = [];
  const search = async (unit) => {
    const v = q.value.trim(); if (!v) return;
    const found = await findItems(v); res = found.items;
    const scanned = /^\d{6,}$/.test(v) || found.hint?.kind === "gs1";
    const sellable = (r) => r.is_service || r.stock_qty == null || r.stock_qty > 0;     // out of stock: show it, with its similar items
    if (res.length === 1 && sellable(res[0]) && (scanned || !found.hint)) { await addItem(res[0], unit ? 0 : 1, unit ? 1 : 0); return; }
    $("#posRes").innerHTML = (found.hint?.kind === "gs1" && !res.length
      ? `<p class="badge b-warn">⚠ هذا الكود المربع غير مسجل على أي صنف. افتح بطاقة الصنف وامسحه في خانة الباركود.</p>` : hintHtml(found.hint))
      + res.map((r, i) => `<div class="res ${r.stock_qty ? "" : "out"}"><div class="grow"><b>${itemHtml(r)}</b> ${markChip(r.mark)}
        <div class="muted">${esc(r.strength_norm || "")} · ${r.pack_size} ${esc(unitName(r))} في العلبة · ${r.location ? `${esc(r.location)} · ` : ""}رصيد: <b>${esc(fmtStock(r.stock_qty, r) || "0")}</b></div></div>
        <b class="price">${money(r.public_price)} ج</b>
        ${r.stock_qty ? `<button class="btn small" data-add="${i}" data-kind="pack">علبة${i === 0 ? " ↵" : ""}</button>
        ${r.pack_size > 1 ? `<button class="btn small secondary" data-add="${i}" data-kind="unit">${esc(unitName(r))}</button>` : ""}`
        : `<span class="badge b-err">✕ ناقص</span>`}
        ${r.is_service ? "" : `<button class="btn small ${r.stock_qty ? "secondary" : ""}" data-alt="${i}" title="المثيل والبديل">↔ مثيل / بديل</button>`}</div>`).join("") || `<p class="muted">لا توجد نتائج</p>`;
    $("#posRes").querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () =>
      addItem(res[+b.dataset.add], b.dataset.kind === "pack" ? 1 : 0, b.dataset.kind === "unit" ? 1 : 0)));
    $("#posRes").querySelectorAll("[data-alt]").forEach((b) => b.addEventListener("click", () => altPanel(res[+b.dataset.alt].id, $("#posRes"), addItem)));
  };
  q.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const first = $("#posRes [data-add]");
    if (first && q.value.trim() === q.dataset.searched) {           // second Enter on the same text: add the first result
      const r = res[+first.dataset.add]; if (r) addItem(r, e.shiftKey ? 0 : 1, e.shiftKey ? 1 : 0); return;
    }
    q.dataset.searched = q.value.trim();
    search(e.shiftKey).catch((er) => toast(er.message, true));
  });
  bindPosExtras();
}


// أزرار البيع السريع: اللي الصاحب ثبّتها، وبعدها الأكتر بيعًا آخر 30 يوم
let QUICK = null;
const quickOpen = () => { try { return localStorage.getItem("posQuick") !== "0"; } catch { return true; } };
async function renderQuick() {
  const box = $("#posQuick"); if (!box) return;
  const open = quickOpen();
  if (open && !QUICK) { try { QUICK = await api("/api/pos/quick"); } catch { QUICK = { items: [] }; } }
  if (!$("#posQuick")) return;
  const list = QUICK?.items || [];
  box.innerHTML = `<section class="panel qsale"><div class="row between"><h2>⚡ البيع السريع</h2>
      <span class="row">${open ? `<button class="btn small secondary" id="qkRef" title="حدّث">↻</button>` : ""}<button class="btn small secondary" id="qkTgl">${open ? "إخفاء" : "إظهار"}</button></span></div>
    ${open ? (list.length ? `<div class="qgrid">${list.map((x, i) => {
      const can = x.is_service || x.stock_qty == null || x.stock_qty > 0;
      return `<button class="qbtn ${can ? "" : "out"}" data-q="${i}" ${can ? "" : "disabled"} title="${esc(x.name_en || x.name_ar)}">
        ${x.pinned ? `<span class="pin" aria-label="مثبّت">📌</span>` : ""}<b class="qn">${esc(x.name_en || x.name_ar)}</b><span class="qp">${money(x.public_price)} ج</span>${can ? "" : `<span class="badge b-err">ناقص</span>`}</button>`; }).join("")}</div>
      <p class="muted small">كل ضغطة تضيف علبة واحدة (أو مرة واحدة للخدمة). ${canW() ? `ثبّت أي صنف هنا من "تعديل الصنف" أو من شاشة الأصناف.` : ""}</p>`
      : `<p class="muted">لم تُبع أصناف بعد في آخر ${QUICK?.days || 30} يومًا.${canW() ? ` ثبّت الأصناف التي تريدها من "تعديل الصنف".` : ""}</p>`) : ""}</section>`;
  $("#qkTgl").addEventListener("click", () => { try { localStorage.setItem("posQuick", open ? "0" : "1"); } catch { /* per-viewer only */ } renderQuick(); });
  $("#qkRef")?.addEventListener("click", () => { QUICK = null; renderQuick(); });
  box.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => {
    const x = list[+b.dataset.q]; addItem(x, x.is_service || x.pack_size === 1 ? 0 : 1, x.is_service || x.pack_size === 1 ? 1 : 0);
  }));
}

async function newTab() {
  try { await openDraft(null); viewPos(); } catch (er) { toast(er.message, true); }
}
document.addEventListener("keydown", (e) => {
  if (!location.hash.startsWith("#/pos") || $("dialog[open]")) return;
  if (e.key === "F2") { e.preventDefault(); newTab(); }
  if (e.key === "F9") { e.preventDefault(); $("#posDone")?.click(); }
});

async function addItem(item, packs, units) {
  try {
    SALE = await api(`/api/sales/${SALE.id}/lines`, { method: "POST", body: { item_id: item.id, packs, units, client_ref: newKey() } });
    const added = SALE.lines.find((l) => l.item_id === item.id);
    if (added && (added.item.rx_required || added.item.misuse_risk || added.item.sale_note))
      toast([added.item.rx_required && "📋 بوصفة طبية فقط", added.item.misuse_risk && "⚠ يُساء استخدامه", added.item.sale_note && `📝 ${added.item.sale_note}`].filter(Boolean).join(" · "), true);
    $("#posRes").innerHTML = ""; $("#posQ").value = ""; delete $("#posQ").dataset.searched; $("#posQ").focus();
    renderCart(); renderSide();
  } catch (er) { toast(er.message, true); }
}

async function patchLine(lineId, body) {
  try { SALE = await api(`/api/sales/${SALE.id}/lines/${lineId}`, { method: "PATCH", body }); renderCart(); renderSide(); }
  catch (er) { toast(er.message, true); }
}

function renderCart(findings = null) {
  const s = SALE, canDisc = ME.max_discount_bp > 0 || ME.role === "owner";
  refreshTabLabel();
  const lastNo = Math.max(0, ...s.lines.map((l) => l.line_no));
  $("#posCart").innerHTML = `
    ${s.lines.length > 1 ? `<p class="muted small">${s.line_order === "entry" ? "الأصناف بترتيب إدخالها" : "الأصناف مرتبة أبجديًا A→Z (أسرع في التحضير)"} — آخر صنف مُضاف عليه علامة ★ ورقم كل صنف هو ترتيب إدخاله</p>` : ""}
    <div class="tablewrap"><table class="cart">
      <thead><tr><th>الصنف</th><th>الكمية</th><th>السعر</th><th>الصلاحية</th><th>خصم</th><th>الإجمالي</th><th><span class="sr">حذف</span></th></tr></thead>
      <tbody>${s.lines.map((l) => `<tr class="${l.problem ? "has-err" : ""} ${l.item.mark ? `m-${l.item.mark}` : ""} ${l.line_no === lastNo && s.lines.length > 1 ? "just-added" : ""}">
        <td><span class="lno" title="ترتيب الإدخال">${l.line_no === lastNo && s.lines.length > 1 ? "★" : ""}${l.line_no}</span> <b>${itemHtml({ name_en: l.item.name_en, name_ar: l.item.name_ar })}</b> ${markChip(l.item.mark)}
          ${l.item.is_service ? `<span class="badge b-info">🛎 خدمة</span>` : ""}${l.item.hide_on_receipt ? `<span class="badge" title="لن يظهر اسمه في الإيصال">🙈</span>` : ""}${l.item.rx_required ? `<span class="badge b-err" title="يُصرف بوصفة طبية فقط">📋 وصفة طبية</span>` : ""}${l.item.misuse_risk ? `<span class="badge b-warn" title="يُساء استخدامه">⚠ سوء استخدام</span>` : ""}${l.item.sale_note ? `<span class="badge b-info" title="${esc(l.item.sale_note)}">📝 ${esc(l.item.sale_note.slice(0, 30))}</span>` : ""}
          ${l.problem ? `<span class="badge b-err">✕ ${l.problem === "no_price" ? "بدون سعر" : "الكمية غير متاحة"}</span>` : ""}</td>
        <td><div class="qty"><button class="btn small secondary" data-minus="${esc(l.id)}" aria-label="نقص">−</button>
          <b>${qtyText(l.qty_base, l.item)}</b><button class="btn small secondary" data-plus="${esc(l.id)}" aria-label="زيادة">+</button></div></td>
        <td class="num">${l.allocations.map((a) => money(a.unit_price)).filter((v, i, arr) => arr.indexOf(v) === i).join(" / ")}</td>
        <td>${l.allocations.map((a) => `<span class="badge ${l.near_expiry ? "b-warn" : "b-ok"}">${l.near_expiry ? "⚠ " : ""}${esc((a.expiry_date || "—").slice(0, 7))}</span>`).join(" ")}</td>
        <td>${l.discount_source === "contract" ? `<span class="chip" title="خصم التعاقد">🤝 ${pct(l.discount_bp)}%</span>` : canDisc ? `<input class="narrow" type="number" min="0" max="100" step="0.5" value="${l.discount_bp ? pct(l.discount_bp) : ""}" data-disc="${esc(l.id)}" placeholder="%">`
          : l.discount_bp ? `${pct(l.discount_bp)}%` : `<button class="btn small secondary" data-askdisc="${esc(l.id)}">خصم</button>`}</td>
        <td class="num"><b>${money(l.net)}</b></td>
        <td><button class="btn small danger" data-rm="${esc(l.id)}" aria-label="إزالة الصنف">✕</button></td></tr>`).join("")
        || `<tr><td colspan="7" class="muted">امسح أول صنف</td></tr>`}</tbody>
    </table></div><div id="posSafety"></div>`;
  const line = (id) => s.lines.find((l) => l.id === id);
  const step = (l) => (l.item.pack_size > 1 && l.qty_base % l.item.pack_size === 0 ? l.item.pack_size : 1);
  $("#posCart").querySelectorAll("[data-plus]").forEach((b) => b.addEventListener("click", () => { const l = line(b.dataset.plus); patchLine(l.id, { qty_base: l.qty_base + step(l) }); }));
  $("#posCart").querySelectorAll("[data-minus]").forEach((b) => b.addEventListener("click", async () => {
    const l = line(b.dataset.minus), n = l.qty_base - step(l);
    if (n <= 0) { SALE = await api(`/api/sales/${SALE.id}/lines/${l.id}`, { method: "DELETE" }); renderCart(); renderSide(); }
    else patchLine(l.id, { qty_base: n });
  }));
  $("#posCart").querySelectorAll("[data-disc]").forEach((inp) => inp.addEventListener("change", () => patchLine(inp.dataset.disc, { discount_bp: toBp(inp.value) || 0 })));
  $("#posCart").querySelectorAll("[data-askdisc]").forEach((b) => b.addEventListener("click", () => {
    const v = prompt("نسبة الخصم %:"); if (v) patchLine(b.dataset.askdisc, { discount_bp: toBp(v) });
  }));
  $("#posCart").querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", async () => {
    SALE = await api(`/api/sales/${SALE.id}/lines/${b.dataset.rm}`, { method: "DELETE" }); renderCart(); renderSide();
  }));
  if (findings) renderFindings(findings);
}

function chanOptions(kind, sel) {
  const list = CHANNELS.filter((c) => c.kind === kind);
  return list.length ? `<select data-ch>${list.map((c) => `<option value="${esc(c.id)}" ${c.id === sel ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>` : "";
}

function renderSide() {
  const s = SALE, d = s.delivery || {};
  if (!POS.split) POS.parts = [];
  const remaining = s.total - POS.parts.reduce((a, p) => a + (p.amount || 0), 0);
  $("#posSide").innerHTML = `
    <section class="panel">
      <div class="seg2" role="radiogroup" aria-label="نوع الفاتورة">
        <button class="${s.kind !== "delivery" ? "on" : ""}" data-kind="store">${s.kind !== "delivery" ? "✓ " : ""}في الصيدلية</button>
        <button class="${s.kind === "delivery" ? "on" : ""}" data-kind="delivery">${s.kind === "delivery" ? "✓ " : ""}توصيل للمنزل</button></div>
      <label class="f mt">العميل ${s.kind === "delivery" ? "" : "(اختياري)"}
        <input id="posCust" placeholder="اسم أو رقم هاتف" value="${s.customer ? esc(s.customer.name) : ""}" autocomplete="off"></label>
      <div id="posCustRes" class="cands"></div>
      ${s.customer ? `<p class="muted">رصيده ${money(s.customer.balance)} / حد الآجل ${money(s.customer.credit_limit)} <a href="#" id="custClear">إزالة العميل</a></p>
        ${s.contract ? `<p><span class="chip">🤝 تعاقد ${esc(s.contract.name)}: محلي ${pct(s.contract.discount_local_bp)}% / مستورد ${pct(s.contract.discount_imported_bp)}%</span></p>` : ""}` : `<p><a href="#" id="posCustNew">+ عميل جديد</a></p>`}
      ${s.kind === "delivery" ? `<div class="deliv">
        <label class="f">العنوان <input data-dl="address" value="${esc(d.address || "")}"></label>
        <label class="f">الهاتف <input data-dl="phone" inputmode="tel" value="${esc(d.phone || "")}"></label>
        <label class="f">مندوب التوصيل <input data-dl="courier" value="${esc(d.courier || "")}"></label>
        <label class="f">رسوم التوصيل (ج) <input data-dl="fee" type="number" min="0" step="0.5" value="${s.delivery_fee ? money(s.delivery_fee) : ""}"></label></div>` : ""}
      <div class="sum"><span>الإجمالي</span><b>${money(s.subtotal)}</b></div>
      ${s.discount_total ? `<div class="sum ok"><span>خصم</span><b>${money(s.discount_total)}</b></div>` : ""}
      ${s.delivery_fee ? `<div class="sum"><span>رسوم التوصيل</span><b>${money(s.delivery_fee)}</b></div>` : ""}
      <div class="sum due"><span>المطلوب</span><b>${money(s.total)}</b></div>
    </section>
    <section class="panel">
      ${POS.split ? `<h2>تقسيم الدفع</h2>${POS.parts.map((p, i) => `<div class="row part">
          <select data-pm="${i}">${METHODS.map(([m, t]) => `<option value="${m}" ${p.method === m ? "selected" : ""}>${t}</option>`).join("")}</select>
          ${["card", "transfer"].includes(p.method) ? chanOptions(p.method, p.channel_id).replace("data-ch", `data-pc="${i}"`) : ""}
          <input class="narrow" type="number" step="0.01" min="0" data-pa="${i}" value="${money(p.amount)}">
          <button class="btn small secondary" data-prm="${i}" aria-label="إزالة">✕</button></div>`).join("")}
        ${POS.parts.some((p) => p.method === "transfer") ? proofInput(s) : ""}
        <button class="btn small secondary" id="partAdd">+ طريقة دفع</button>
        <p class="badge ${remaining === 0 ? "b-ok" : "b-warn"} mt">${remaining === 0 ? "✓ الدفع كامل" : `⚠ المتبقي ${money(remaining)}`}</p>
        <p><a href="#" id="splitOff">رجوع لطريقة دفع واحدة</a></p>`
      : `<div class="paym">${METHODS.map(([m, t]) => `<button class="${POS.method === m ? "on" : ""}" data-m="${m}" ${m === "credit" && !s.customer ? "disabled title=\"اختر العميل أولًا\"" : ""}>${t}</button>`).join("")}</div>
        ${["card", "transfer"].includes(POS.method) ? `<label class="f mt">${POS.method === "card" ? "الجهاز" : "المحفظة / طريقة التحويل"} ${chanOptions(POS.method, POS.channel)}</label>` : ""}
        ${POS.method === "transfer" ? proofInput(s) : ""}
        ${POS.method === "cash" ? `<label class="f mt">العميل دفع <input id="payGiven" type="number" step="0.01" min="0" class="big" placeholder="${money(s.total)}"></label>
          <div class="sum ok" id="changeBox" hidden><span>الباقي للعميل</span><b id="changeV"></b></div>` : ""}
        <p><a href="#" id="splitOn">تقسيم الدفع (جزء نقدًا وجزء ببطاقة أو تحويل)</a></p>`}
      <button class="btn big" id="posDone" ${s.lines.length ? "" : "disabled"}>إتمام البيع وطباعة <kbd>F9</kbd></button>
    </section>`;
  $("#posProof")?.addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api(`/api/sales/${SALE.id}/attachments`, { method: "POST", form: fd }); SALE.attachments = r.attachments;
      toast(r.duplicate_of ? `⚠ هذه الصورة نفسها مسجلة مع الفاتورة ${r.duplicate_of}` : "✓ تم حفظ صورة التحويل مع الفاتورة", !!r.duplicate_of); renderSide(); }
    catch (err) { toast(err.message, true); }
  });
  document.querySelectorAll("[data-kind]").forEach((b) => b.addEventListener("click", async () => {
    try { SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { kind: b.dataset.kind } }); renderSide(); refreshTabLabel(); }
    catch (er) { toast(er.message, true); }
  }));
  document.querySelectorAll("[data-dl]").forEach((inp) => inp.addEventListener("change", async () => {
    const k = inp.dataset.dl, v = k === "fee" ? toPiasters(inp.value) || 0 : inp.value;
    try { SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { delivery: { [k]: v } } }); if (k === "fee") renderSide(); }
    catch (er) { toast(er.message, true); }
  }));
  $("#posCust").addEventListener("input", async (e) => {
    const v = e.target.value.trim(); if (v.length < 2) { $("#posCustRes").innerHTML = ""; return; }
    const cs = await api(`/api/customers?q=${encodeURIComponent(v)}`);
    $("#posCustRes").innerHTML = cs.map((c) => `<button class="btn small secondary" data-cust="${esc(c.id)}">${esc(c.name)}${c.name_alt ? ` / ${esc(c.name_alt)}` : ""}${c.phone ? ` — ${esc(c.phone)}` : ""}</button>`).join("") || `<p class="muted">لا يوجد عميل بهذا الاسم</p>`;
    $("#posCustRes").querySelectorAll("[data-cust]").forEach((b) => b.addEventListener("click", async () => {
      SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { customer_id: b.dataset.cust } });
      const fill = {};
      if (SALE.kind === "delivery" && SALE.customer?.phone && !SALE.delivery?.phone) fill.phone = SALE.customer.phone;
      if (SALE.kind === "delivery" && SALE.customer?.address1 && !SALE.delivery?.address) fill.address = SALE.customer.address1;
      if (Object.keys(fill).length) SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { delivery: fill } });
      renderSide();
    }));
  });
  $("#custClear")?.addEventListener("click", async (e) => { e.preventDefault(); SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { customer_id: null } }); if (POS.method === "credit") POS.method = "cash"; renderSide(); });
  $("#posCustNew")?.addEventListener("click", async (e) => {
    e.preventDefault();
    const name = prompt("اسم العميل:"); if (!name) return;
    const phone = prompt("الهاتف (اختياري):") || null;
    try {
      const c = await api("/api/customers", { method: "POST", body: { name, phone, credit_limit: 0 } });
      SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { customer_id: c.id } }); renderSide();
    } catch (er) { toast(er.message, true); }
  });
  document.querySelectorAll("[data-m]").forEach((b) => b.addEventListener("click", () => {
    POS.method = b.dataset.m; POS.channel = CHANNELS.find((c) => c.kind === POS.method)?.id || null; renderSide();
  }));
  $("#posSide [data-ch]")?.addEventListener("change", (e) => { POS.channel = e.target.value; });
  $("#payGiven")?.addEventListener("input", (e) => {
    const g = toPiasters(e.target.value), ch = g - s.total;
    $("#changeBox").hidden = !(g >= s.total); $("#changeV").textContent = money(ch);
  });
  $("#splitOn")?.addEventListener("click", (e) => { e.preventDefault(); POS.split = true; POS.parts = [{ method: "cash", amount: s.total }]; renderSide(); });
  $("#splitOff")?.addEventListener("click", (e) => { e.preventDefault(); POS.split = false; renderSide(); });
  $("#partAdd")?.addEventListener("click", () => { POS.parts.push({ method: "card", channel_id: CHANNELS.find((c) => c.kind === "card")?.id, amount: Math.max(0, remaining) }); renderSide(); });
  document.querySelectorAll("[data-pm]").forEach((x) => x.addEventListener("change", () => { const p = POS.parts[+x.dataset.pm]; p.method = x.value; p.channel_id = CHANNELS.find((c) => c.kind === x.value)?.id || null; renderSide(); }));
  document.querySelectorAll("[data-pc]").forEach((x) => x.addEventListener("change", () => { POS.parts[+x.dataset.pc].channel_id = x.value; }));
  document.querySelectorAll("[data-pa]").forEach((x) => x.addEventListener("change", () => { POS.parts[+x.dataset.pa].amount = toPiasters(x.value) || 0; renderSide(); }));
  document.querySelectorAll("[data-prm]").forEach((x) => x.addEventListener("click", () => { POS.parts.splice(+x.dataset.prm, 1); renderSide(); }));
  $("#posDone").addEventListener("click", () => complete(null));
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
    ${findings.map((f, i) => `<div class="mt"><span class="badge ${SEV[f.severity][1]}">${{ item_rx: "📋 بوصفة طبية فقط", item_misuse: "⚠ يُساء استخدامه", item_note: "📝 ملاحظة" }[f.code] || SEV[f.severity][0]}</span> ${esc(f.message)}
      ${f.source ? `<div class="muted">المرجع: ${esc(f.source.name)} ${esc(f.source.version)} ${esc(f.source.ref || "")}</div>` : ""}
      ${f.basis === "heuristic" ? `<div class="muted">تنبيه مبني على تشابه الأسماء</div>` : ""}
      ${needs(f) ? `<input class="mt" data-dec="${i}" placeholder="${f.requires === "acknowledge" ? "ملاحظة (اختياري) ثم تأكيد" : "اكتب سبب الاستمرار (يُسجَّل باسمك)"}">` : ""}</div>`).join("") || "<p>لا توجد تنبيهات</p>"}
    <button class="btn mt" id="posConfirm">تأكيد وإتمام البيع</button></div>`;
  $("#posConfirm").addEventListener("click", () => complete(findings));
}

function currentPayments() {
  if (POS.split) return POS.parts.filter((p) => p.amount > 0).map((p) => ({ method: p.method, amount: p.amount, channel_id: p.channel_id || null }));
  return [{ method: POS.method, amount: SALE.total, channel_id: ["card", "transfer"].includes(POS.method) ? ($("#posSide [data-ch]")?.value || POS.channel) : null }];
}

async function complete(findings) {
  const payments = currentPayments();
  const cash = payments.filter((p) => p.method === "cash").reduce((a, p) => a + p.amount, 0);
  if (!findings) {
    const r = await api(`/api/sales/${SALE.id}/check`, { method: "POST", body: {} });
    const blocking = r.findings.filter((f) => ["override", "block", "acknowledge"].includes(f.requires));
    if (r.findings.length && (blocking.length || r.findings.some((f) => f.severity !== "insufficient"))) {
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
  const btn = $("#posDone");
  if (btn.disabled) return;
  btn.disabled = true;
  SALE.request_id = SALE.request_id || newKey();
  try {
    const done = await api(`/api/sales/${SALE.id}/complete`, { method: "POST",
      body: { payments, safety_decisions: decisions, version: SALE.version, request_id: SALE.request_id,
              confirm_below_cost: !!SALE.confirm_below_cost } });
    try { await api(`/api/print/receipt/${done.id}`, { method: "POST", body: { open_drawer: cash > 0 && done.kind !== "delivery" } }); }
    catch (pe) { printInFrame(`/receipt/${done.id}`); if (pe.code === "print_failed") toast(pe.message, true); }
    QUICK = null;
    toast(`✓ تم البيع — فاتورة رقم ${done.sale_no}${done.kind === "delivery" ? " (توصيل: المبلغ مع مندوب التوصيل حتى يسلّمه)" : ""}`);
    SALE = null; viewPos();
  } catch (er) {
    toast(er.message, true);
    btn.disabled = false;
    if (er.code === "safety_unresolved") renderCart(er.details.findings);
    if (er.code === "not_draft") { SALE = null; viewPos(); }
    if (er.code === "no_open_shift") $("#posShift").click();
    if (er.code === "below_cost_confirm") {
      const lines = (er.details?.lines || []).map((l) => `${l.name}: ${money(l.net)} والتكلفة ${money(l.cost)}`).join("\n");
      if (confirm(`⛔ هذا البيع أقل من التكلفة (بخسارة):\n${lines}\n\nهل أنت متأكد أنك تريد المتابعة؟`)) { SALE.confirm_below_cost = true; return complete(findings); }
    }
  }
}

function bindPosExtras() {
  $("#posShift").addEventListener("click", (e) => { e.preventDefault(); location.hash = "#/shift"; });
  $("#posDeliv").addEventListener("click", async (e) => {
    e.preventDefault();
    const ds = (await api("/api/deliveries")).deliveries;
    const ST = { out: ["b-warn", "⚠ خرجت"], delivered: ["b-info", "ℹ تم التسليم — المبلغ لم يصل بعد"] };
    $("#posSum").innerHTML = `<section class="panel"><h2>التوصيلات المفتوحة</h2><table><thead><tr><th>فاتورة</th><th>العميل / العنوان</th><th>مندوب التوصيل</th><th>المبلغ معه</th><th>الحالة</th><th></th></tr></thead><tbody>
      ${ds.map((d) => `<tr><td><a href="#/sales/${esc(d.sale_id)}">${d.sale_no}</a></td><td>${esc(d.customer || "")} ${esc(d.address || "")} ${esc(d.phone || "")}</td>
        <td>${esc(d.courier || "—")}</td><td class="num">${money(d.cash_due)}</td><td><span class="badge ${ST[d.status][0]}">${ST[d.status][1]}</span></td>
        <td>${d.status === "out" ? `<button class="btn small secondary" data-dst="delivered" data-id="${esc(d.sale_id)}">تم التسليم</button>` : ""}
          <button class="btn small" data-dst="settled" data-id="${esc(d.sale_id)}">وصل المبلغ إلى الخزينة</button></td></tr>`).join("") || `<tr><td colspan="6" class="muted">لا توجد توصيلات مفتوحة</td></tr>`}</tbody></table></section>`;
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
async function viewShift() {
  let sh = null;
  try { sh = await api("/api/cash/current"); } catch { /* none open */ }
  const seeAll = ME.permissions.includes("cash.see_expected");
  const recent = seeAll ? (await api("/api/cash/shifts")).shifts : [];
  const hist = recent.length ? `<section class="panel" id="shHist"><div class="row between"><h2>آخر الورديات</h2>${reportTools("shifts")}</div><table><thead><tr><th>من</th><th>إلى</th><th>أغلقها</th><th>المفروض</th><th>المعدود</th><th>النتيجة</th><th></th></tr></thead><tbody>
    ${recent.map((r) => `<tr><td>${esc(new Date(r.opened_at).toLocaleString(LOC()))}</td><td>${r.closed_at ? esc(new Date(r.closed_at).toLocaleString(LOC())) : `<span class="badge b-info">مفتوح</span>`}</td>
      <td>${esc(r.closed_by_name || "")}</td><td class="num">${r.status === "closed" ? money(r.expected_cash) : ""}</td><td class="num">${r.status === "closed" ? money(r.counted_cash) : ""}</td>
      <td>${r.status !== "closed" ? "" : !r.difference ? `<span class="badge b-ok">✓ مطابق</span>` : r.difference < 0 ? `<span class="badge b-err">✕ عجز ${money(-r.difference)}</span>` : `<span class="badge b-warn">⚠ زيادة ${money(r.difference)}</span>`}</td>
      <td>${r.status === "closed" ? `<a href="/shift-report/${esc(r.id)}" target="_blank">التقرير</a>` : ""}</td></tr>`).join("")}</tbody></table></section>` : "";
  if (!sh) {
    app.innerHTML = `<section class="panel narrowpanel"><h1>الوردية</h1><p class="badge b-warn">⚠ لا توجد وردية مفتوحة — البيع النقدي متوقف</p>
      <form id="shOpen" class="row mt"><label class="f">النقدية في الخزينة الآن (ج) <input name="v" type="number" step="0.25" min="0" value="0" class="big"></label>
      <button class="btn">افتح الوردية</button></form></section>${hist}`;
    wireHist();
    $("#shOpen").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api("/api/cash/open", { method: "POST", body: { opening_cash: toPiasters(e.target.v.value) || 0 } }); toast("✓ تم فتح الوردية"); viewShift(); }
      catch (err) { toast(err.message, true); }
    });
    return;
  }
  const k = sh.cash_by_kind || {};
  app.innerHTML = `<section class="panel"><div class="row between"><h1>الوردية المفتوحة</h1><span class="muted">من ${esc(new Date(sh.opened_at).toLocaleString(LOC()))}</span></div>
    ${sh.blind ? `<p class="badge b-info">ℹ عُدّ النقدية في الخزينة فئةً فئة واكتب العدد. تظهر النتيجة بعد الإغلاق.</p>`
    : `<div class="totals"><div>البداية<b>${money(sh.opening_cash)}</b></div><div>مبيعات نقدي<b>${money(k.sale || 0)}</b></div>
      <div>مرتجع وإلغاء<b>${money((k.refund || 0) + (k.void || 0))}</b></div><div>تحصيل آجل<b>${money(k.customer_payment || 0)}</b></div>
      <div>مصروفات<b>${money(k.payout || 0)}</b></div><div>المتوقع في الخزينة<b>${money(sh.expected_cash)}</b></div>
      ${(sh.by_channel || []).map((c) => `<div>${c.method === "card" ? "بطاقة" : "تحويل"} — ${esc(c.channel)}<b>${money(c.amount)}</b></div>`).join("")}
      ${sh.with_courier ? `<div>مع مندوب التوصيل<b>${money(sh.with_courier)}</b><span class="badge b-warn">⚠ لم تُسلَّم بعد</span></div>` : ""}</div>`}
    ${ME.permissions.includes("cash.manage") ? `<form id="shPay" class="row mt"><label class="f">مصروف من الخزينة (ج) <input name="a" type="number" step="0.25" min="0"></label>
      <label class="f grow">السبب <input name="n"></label><button class="btn secondary small">سجّل</button></form>` : ""}
  </section>
  ${ME.permissions.includes("cash.close") ? `<form class="panel" id="shClose"><h2>إغلاق الوردية — عدّ الخزينة</h2>
    <table class="denoms"><thead><tr><th>الفئة</th><th>العدد</th><th>المبلغ</th></tr></thead><tbody>
    ${DENOMS.map((d) => `<tr><td>${denomLabel(d)}</td><td><input type="number" min="0" step="1" inputmode="numeric" data-d="${d}" class="narrow"></td><td class="num" id="dt${d}">0.00</td></tr>`).join("")}</tbody>
    <tfoot><tr><th>الإجمالي المعدود</th><th></th><th class="num big" id="dTotal">0.00</th></tr></tfoot></table>
    <label class="f mt">ملاحظة (اختياري) <input name="note"></label>
    <button class="btn big">إغلاق الوردية وطباعة التقرير</button></form>` : ""}
  ${hist}`;
  const recalc = () => {
    let t = 0;
    document.querySelectorAll("[data-d]").forEach((inp) => { const v = (+inp.value || 0) * +inp.dataset.d; t += v; $(`#dt${inp.dataset.d}`).textContent = money(v); });
    $("#dTotal").textContent = money(t);
  };
  document.querySelectorAll("[data-d]").forEach((inp) => inp.addEventListener("input", recalc));
  wireHist();
  function wireHist() { if (recent.length) wireTools("shifts", () => ({ title: "آخر الورديات", subtitle: "", el: $("#shHist"), sheets: [
    { name: "الورديات", columns: [Tx("من"), Tx("إلى"), Tx("أغلقها"), M("المفروض"), M("المعدود"), M("الفرق"), Tx("ملاحظة")],
      rows: recent.map((r) => [new Date(r.opened_at).toLocaleString(LOC()), r.closed_at ? new Date(r.closed_at).toLocaleString(LOC()) : "مفتوح", r.closed_by_name || "",
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
      const r = await api("/api/cash/close", { method: "POST", body: { denominations: den, note: e.target.note.value || null } });
      const d = r.difference;
      app.innerHTML = `<section class="panel narrowpanel"><h1>تم إغلاق الوردية</h1>
        <p class="result ${!d ? "b-ok" : d < 0 ? "b-err" : "b-warn"}">${!d ? "✓ الخزينة مطابقة" : d < 0 ? `✕ عجز ${money(-d)} ج` : `⚠ زيادة ${money(d)} ج`}</p>
        <div class="totals"><div>المفروض<b>${money(r.expected_cash)}</b></div><div>المعدود<b>${money(r.counted_cash)}</b></div></div>
        <div class="row mt"><a class="btn" href="/shift-report/${esc(r.id)}" target="_blank">اطبع التقرير</a><a class="btn secondary" href="#/shift">افتح وردية جديدة</a></div></section>`;
      printInFrame(`/shift-report/${r.id}`);
    } catch (err) {
      toast(err.code === "difference_note_required" ? `${err.message} (الفرق ${money(err.details.difference)})` : err.message, true);
      if (err.code === "difference_note_required") e.target.note.focus();
    }
  });
}

// ------------------------------------------------------------------ suppliers and their accounts
const PAY_M = { drawer: "نقدًا من الخزينة", cash: "نقدًا من خارج الخزينة", bank: "تحويل بنكي", cheque: "شيك", wallet: "إنستاباي / محفظة" };
const LEDGER_KIND = { opening: "رصيد أول المدة", purchase: "فاتورة شراء", payment: "دفعة", return: "مرتجع", adjustment: "تسوية / إلغاء" };
async function viewSuppliers() {
  const list = (await api("/api/suppliers/all")).suppliers, showBal = list.length && "balance" in list[0];
  app.innerHTML = `<section class="panel"><div class="row between"><h1>الموردون</h1>
    ${ME.permissions.includes("suppliers.write") ? `<form id="supNew" class="row"><input name="name" placeholder="اسم المورد" required>
      <select name="t"><option value="company">شركة</option><option value="warehouse" selected>مخزن</option></select><button class="btn small">أضف مورد</button></form>` : ""}</div>
    <table><thead><tr><th>المورد</th><th>النوع</th><th>آخر فاتورة</th>${showBal ? "<th>مستحق له علينا</th>" : ""}</tr></thead><tbody>
    ${list.map((x) => `<tr class="clickable" data-go="${esc(x.id)}"><td><b>${esc(x.name)}</b>${x.is_active ? "" : ` <span class="badge b-plain">موقوف</span>`}</td>
      <td>${SUP_TYPE[x.supplier_type] ? `<span class="chip">${SUP_TYPE[x.supplier_type]}</span>` : `<span class="badge b-warn">⚠ حدد النوع</span>`}</td>
      <td>${esc(x.last_invoice || "—")}</td>${showBal ? `<td class="num">${x.balance > 0 ? `<b>${money(x.balance)}</b>` : x.balance < 0 ? `<span class="ok">مستحق لنا عنده ${money(-x.balance)}</span>` : "—"}</td>` : ""}</tr>`).join("")
      || `<tr><td colspan="4" class="muted">لا يوجد موردون</td></tr>`}</tbody></table>
    <p class="muted">نوع المورد (شركة أو مخزن) يؤثر في الضرائب، ويُحفظ مع كل فاتورة شراء.</p></section>
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
  const a = await api(`/api/suppliers/${id}`), canW = ME.permissions.includes("suppliers.write"), canPay = ME.permissions.includes("suppliers.pay");
  app.innerHTML = `<section class="panel"><a href="#/suppliers">← الموردون</a>
    <div class="row between"><h1>${esc(a.name)}</h1>
      <span class="seg2 small" id="supType">${Object.entries(SUP_TYPE).map(([v, t]) => `<button type="button" class="${a.supplier_type === v ? "on" : ""}" data-st="${v}" ${canW ? "" : "disabled"}>${a.supplier_type === v ? "✓ " : ""}${t}</button>`).join("")}</span></div>
    <div class="row"><label class="f">التليفون <input id="supPhone" value="${esc(a.phone || "")}" ${canW ? "" : "disabled"}></label>
      <label class="f grow">شروط الدفع <input id="supTerms" value="${esc(a.payment_terms || "")}" placeholder="مثال: آجل 30 يوم" ${canW ? "" : "disabled"}></label>
      ${canW ? `<button class="btn small secondary" id="supSave">احفظ</button>` : ""}</div></section>
  ${a.balance != null ? `<div class="totals"><div class="${a.balance > 0 ? "t-warn" : "t-ok"}">${a.balance > 0 ? "مستحق له علينا" : a.balance < 0 ? "مستحق لنا عنده" : "✓ لا يوجد حساب"}<b>${money(Math.abs(a.balance))} ج</b></div></div>` : ""}
  ${canPay ? `<form class="panel row" id="supPay"><h2 class="grow">تسجيل دفعة للمورد</h2>
    <label class="f">المبلغ (ج) <input name="a" type="number" step="0.01" min="0" required></label>
    <label class="f">طريقة الدفع <select name="m">${Object.entries(PAY_M).map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select></label>
    <label class="f">رقم الشيك / التحويل <input name="r" placeholder="مطلوب للشيك"></label>
    <label class="f grow">ملاحظة <input name="n"></label><button class="btn">سجّل الدفعة</button></form>` : ""}
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
  document.querySelectorAll("[data-st]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/suppliers/${id}`, { method: "PATCH", body: { supplier_type: b.dataset.st } }); viewSupplier(id); } catch (err) { toast(err.message, true); }
  }));
  $("#supSave")?.addEventListener("click", async () => {
    try { await api(`/api/suppliers/${id}`, { method: "PATCH", body: { phone: $("#supPhone").value, payment_terms: $("#supTerms").value } }); toast("تم الحفظ"); } catch (err) { toast(err.message, true); }
  });
  $("#supPay")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api(`/api/suppliers/${id}/pay`, { method: "POST", body: { amount: toPiasters(e.target.a.value), method: e.target.m.value, ref: e.target.r.value || null, note: e.target.n.value || null } }); toast("✓ تم تسجيل الدفعة"); viewSupplier(id); }
    catch (err) { toast(err.message, true); }
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
async function viewSupplierReturns(arg) {
  if (arg && arg !== "new") return viewSupplierReturn(arg);
  const canNew = ME.permissions.includes("purchases.approve"), money_ = ME.permissions.includes("suppliers.pay") || ME.permissions.includes("costs.read");
  const [sups, R] = await Promise.all([api("/api/suppliers"), stockReasons()]);
  app.innerHTML = `${canNew ? `<section class="panel" id="srNew"><h1>مرتجع جديد للمورد</h1>
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
    $("#srLines").innerHTML = lines.map((l, i) => `<tr><td>${itemHtml(l.item)}</td>
      <td><select data-sb="${i}">${l.batches.map((b) => `<option value="${esc(b.batch_id || "")}" ${b.batch_id === l.batch_id ? "selected" : ""}>${esc(b.batch_no || "بدون تشغيلة")} — ${esc((b.expiry_date || "").slice(0, 7))} — متاح ${b.qty}</option>`).join("")}</select></td>
      <td><input class="narrow" type="number" min="1" step="1" data-sq="${i}" value="${l.qty_base}"></td>
      <td><select data-sw="${i}">${Object.entries(R.supplier_return).map(([k, t]) => `<option value="${k}" ${k === l.reason ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></td>
      ${money_ ? `<td><input class="narrow" type="number" min="0" step="0.01" data-sv="${i}" value="${money(l.unit_value)}"></td>` : ""}
      <td><button class="btn small danger" data-sx="${i}" aria-label="إزالة">✕</button></td></tr>`).join("") || `<tr><td colspan="6" class="muted">ابحث عن الصنف وأضفه</td></tr>`;
    if ($("#srTot")) $("#srTot").textContent = money_ && lines.length ? `الإجمالي: ${money(lines.reduce((a, l) => a + l.qty_base * (l.unit_value || 0), 0))} ج` : "";
    const val = async (l) => { try { l.unit_value = (await api(`/api/supplier-returns/suggest?item=${l.item.id}&batch=${l.batch_id || ""}`)).unit_value; } catch { /* keep */ } draw(); };
    document.querySelectorAll("[data-sb]").forEach((x) => x.addEventListener("change", () => { const l = lines[x.dataset.sb]; l.batch_id = x.value || null; val(l); }));
    document.querySelectorAll("[data-sq]").forEach((x) => x.addEventListener("change", () => { lines[x.dataset.sq].qty_base = +x.value || 1; draw(); }));
    document.querySelectorAll("[data-sw]").forEach((x) => x.addEventListener("change", () => { lines[x.dataset.sw].reason = x.value; }));
    document.querySelectorAll("[data-sv]").forEach((x) => x.addEventListener("change", () => { lines[x.dataset.sv].unit_value = toPiasters(x.value) || 0; draw(); }));
    document.querySelectorAll("[data-sx]").forEach((x) => x.addEventListener("click", () => { lines.splice(+x.dataset.sx, 1); draw(); }));
  };
  const addItem = async (itemId) => {
    const c = await api(`/api/items/${itemId}/card`);
    const batches = (c.batches || []).filter((b) => b.qty > 0);
    if (!batches.length) { toast("هذا الصنف ليس له رصيد", true); return; }
    const b = batches[0], l = { item: c, batches, batch_id: b.batch_id || null, qty_base: Math.min(b.qty, c.pack_size || 1), reason: (b.expiry_date || "9") < isoDay(new Date()) ? "expired" : "near_expiry", unit_value: 0 };
    lines.push(l); draw();
    try { l.unit_value = (await api(`/api/supplier-returns/suggest?item=${c.id}&batch=${l.batch_id || ""}`)).unit_value; } catch { /* zero */ }
    draw();
  };
  if ($("#srQ")) {
    let t;
    $("#srQ").addEventListener("input", () => { clearTimeout(t); t = setTimeout(async () => {
      const v = $("#srQ").value.trim(); if (v.length < 2) { $("#srRes").innerHTML = ""; return; }
      const r = await findItems(v, 8);
      $("#srRes").innerHTML = r.items.map((it) => `<button class="btn small secondary" data-pick="${esc(it.id)}">${itemHtml(it)}</button>`).join("") || `<p class="muted">لا يوجد</p>`;
      document.querySelectorAll("[data-pick]").forEach((x) => x.addEventListener("click", () => { $("#srRes").innerHTML = ""; $("#srQ").value = ""; addItem(x.dataset.pick).catch((e) => toast(e.message, true)); }));
    }, 250); });
    $("#srGo").addEventListener("click", async () => {
      if (!$("#srSup").value) { toast("اختر المورد", true); return; }
      if (!lines.length) { toast("أضف صنفًا واحدًا على الأقل", true); return; }
      const w = window.open("", "_blank");
      try {
        const r = await api("/api/supplier-returns", { method: "POST", body: { supplier_id: $("#srSup").value, note: $("#srNote").value || null,
          lines: lines.map((l) => ({ item_id: l.item.id, batch_id: l.batch_id, qty_base: l.qty_base, reason: l.reason, ...(money_ ? { unit_value: l.unit_value } : {}) })) } });
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
    <table><thead><tr><th>الصنف</th><th>التشغيلة</th><th>الصلاحية</th><th>الكمية</th><th>السبب</th>${r.total != null ? "<th>قيمة الوحدة</th><th>القيمة</th>" : ""}</tr></thead><tbody>
    ${r.lines.map((l) => `<tr><td>${itemHtml(l)}</td><td>${esc(l.batch_no || "")}</td><td>${esc((l.expiry_date || "").slice(0, 7))}</td><td>${esc(fmtStock(l.qty_base, l))}</td><td>${esc(l.reason_label)}</td>
      ${r.total != null ? `<td class="num">${money(l.unit_value)}</td><td class="num">${money(l.unit_value * l.qty_base)}</td>` : ""}</tr>`).join("")}</tbody></table>
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
const CL_KIND = { credit_sale: "فاتورة آجل", payment: "دفعة", return: "مرتجع", void: "إلغاء", opening: "رصيد أول المدة", adjustment: "تسوية" };
async function viewCustomers(filter = "") {
  const canM = ME.permissions.includes("customers.manage");
  app.innerHTML = `<section class="panel" id="cuRep"><div class="row between"><h1>العملاء</h1>${reportTools("cust")}</div>
    <div class="row"><input id="cuQ" class="grow" placeholder="اسم أو رقم هاتف" autocomplete="off">
      <div class="seg2 small" id="cuF"><button data-f="" class="${!filter ? "on" : ""}">الكل</button><button data-f="debt" class="${filter === "debt" ? "on" : ""}">عليهم مستحقات</button><button data-f="refill" class="${filter === "refill" ? "on" : ""}">اقترب موعد طلبهم الشهري</button></div></div>
    <table class="mt"><thead><tr><th>العميل</th><th>الهاتف</th><th>عليه</th><th>الطلب الشهري</th></tr></thead><tbody id="cuBody"></tbody></table><p id="cuSum"></p></section>
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
    const list = (await api(`/api/customers/all?q=${encodeURIComponent($("#cuQ").value)}${f ? `&only=${f}` : ""}`)).customers;
    const debt = list.reduce((a, c) => a + Math.max(0, c.balance), 0);
    $("#cuSum").innerHTML = `<b>${list.length} عميل${debt ? ` — عليهم ${money(debt)} ج` : ""}</b>${list.length >= 300 ? ` <span class="muted">(أول 300 — ابحث بالاسم عن الباقي)</span>` : ""}`;
    cur = { list, f };
    $("#cuBody").innerHTML = list.map((c) => `<tr class="clickable" data-go="${esc(c.id)}"><td><b>${esc(c.name)}</b>${c.name_alt ? `<div class="muted small">${esc(c.name_alt)}</div>` : ""}</td><td dir="ltr">${[c.phone, c.phone2, c.phone3].filter(Boolean).map(esc).join("<br>")}</td>
      <td>${c.balance > 0 ? `<span class="badge b-err">✕ ${money(c.balance)}</span>` : `<span class="ok">✓ لا يوجد</span>`}</td>
      <td>${c.next_refill ? `<span class="badge ${c.next_refill <= today ? "b-err" : "b-warn"}">${c.next_refill <= today ? "✕ فات موعده" : "⚠"} ${esc(c.next_refill)}</span>` : "—"}</td></tr>`).join("")
      || `<tr><td colspan="4" class="muted">لا يوجد عملاء</td></tr>`;
    document.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/customers/${tr.dataset.go}`; }));
  };
  if ($("#ktBox")) renderContracts();
  let t; $("#cuQ").addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 250); });
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
    <label class="f">المصدر <select name="m"><option value="cash">نقدًا في الخزينة</option><option value="card">بطاقة</option></select></label><button class="btn">سجّل</button></form>` : ""}
  <div class="grid2">
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
  $("#cPay")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api(`/api/customers/${id}/payments`, { method: "POST", body: { amount: toPiasters(e.target.a.value), method: e.target.m.value } }); toast("✓ تم التسجيل"); viewCustomer(id); }
    catch (err) { toast(err.message, true); }
  });
  const toPos = async (sid) => { SALE = await api(`/api/sales/${sid}`); location.hash = "#/pos"; };
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
  const r = await api(`/api/expenses?from=${pf}&to=${pt}`), canA = ME.permissions.includes("expenses.approve");
  const pending = r.expenses.filter((e) => e.status === "pending");
  app.innerHTML = `<form class="panel" id="exNew"><h1>تسجيل مصروف</h1>
    <div class="formgrid"><label class="f">البند <select name="cat">${r.categories.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select></label>
      <label class="f">المبلغ (ج) <input name="a" type="number" step="0.25" min="0" required></label>
      <label class="f">مصدر المبلغ <select name="from"><option value="drawer">من خزينة الوردية</option><option value="outside">من خارج الخزينة</option></select></label>
      <label class="f">التاريخ <input name="d" type="date" value="${isoDay(new Date())}"></label>
      <label class="f">ملاحظة <input name="n" placeholder="مثال: أكياس + بكرة إيصالات"></label></div>
    <button class="btn mt">سجّل</button>
    <p class="muted">${ME.role === "owner" ? "مصروفاتك تُعتمد مباشرة." : `ينتظر المصروف موافقة ${canA ? "المالك إذا كان أكبر من " + money(r.manager_limit) + " ج" : "المدير أو المالك"} قبل احتسابه.`}</p></form>
  ${canA && pending.length ? `<section class="panel"><h2>⚠ بانتظار موافقتك (${pending.length})</h2>${exTable(pending, true)}</section>` : ""}
  ${canA ? `<section class="panel" id="scBox"></section>` : ""}
  <section class="panel" id="exRep"><div class="row between"><h2>المصروفات — ${esc(rangeText(pf, pt))}</h2>${reportTools("exp")}</div>
    <div class="row between noprint"><div id="exRangeBox"></div>${ME.permissions.includes("costs.read") ? `<a href="#/profit">تقرير الربح ←</a>` : ""}</div>
    ${exTable(r.expenses, false)}
    <p><b>الإجمالي المعتمد: ${money(r.expenses.filter((e) => e.status === "approved").reduce((a, e) => a + e.amount, 0))} ج</b></p>
    ${canA ? `<form class="row mt" id="exCat"><input name="n" placeholder="بند جديد" required><button class="btn small secondary">أضف بند</button></form>` : ""}</section>`;
  if (canA) renderSchedules(r.categories);
  $("#exRangeBox").innerHTML = rangeBar("exRange");
  $("#exRange [data-rf]").value = pf; $("#exRange [data-rt]").value = pt;
  $("#exRange").querySelectorAll("[data-rp]").forEach((x) => x.classList.toggle("on", false));
  let first = true;
  wireRange("exRange", (f, t) => { if (first) { first = false; return; } viewExpenses([f, t]); });
  wireTools("exp", () => ({ title: "المصروفات", subtitle: rangeText(pf, pt), el: $("#exRep"), sheets: [
    { name: "المصروفات", columns: [Dt("التاريخ"), Tx("البند"), Tx("ملاحظة"), M("المبلغ"), Tx("المصدر"), Tx("سجّله"), Tx("الحالة")],
      rows: r.expenses.map((e) => [e.spent_on, e.category, e.note || "", e.amount, e.paid_from === "drawer" ? "الخزينة" : "خارجها", e.by_name || "", EX_ST[e.status][1]]) }] }));
  $("#exNew").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try { const x = await api("/api/expenses", { method: "POST", body: { category_id: f.cat.value, amount: toPiasters(f.a.value), paid_from: f.from.value, spent_on: f.d.value, note: f.n.value || null } });
      toast(x.status === "approved" ? "✓ سُجّل واعتُمد" : "سُجّل — بانتظار الموافقة"); viewExpenses(); }
    catch (err) { toast(err.message, true); }
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
async function renderSchedules(cats, edit = null) {
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
        <button class="btn small" data-scpay="${esc(x.id)}">سجّل الدفع</button><button class="btn small secondary" data-scskip="${esc(x.id)}" data-p="${esc(x.next.period)}">ليس هذه المرة</button>` : ""}
        <button class="btn small secondary" data-scedit="${esc(x.id)}">تعديل</button></td></tr>`).join("") || `<tr><td colspan="6" class="muted">لا توجد بنود مجدولة بعد</td></tr>`}</tbody></table></div>
    <form id="scForm" class="mt"><h3>${e ? `تعديل: ${esc(e.name)}` : "بند مجدول جديد"}</h3><div class="formgrid">
      <label class="f">الاسم <input name="name" required value="${v("name")}" placeholder="مثال: إيجار الصيدلية"></label>
      <label class="f">بند المصروف <select name="cat">${cats.map((c) => `<option value="${esc(c.id)}" ${e && e.category_id === c.id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></label>
      <label class="f">المبلغ (ج) — اتركه فارغًا إذا كان متغيرًا <input name="a" type="number" min="0" step="0.01" value="${e && e.amount ? money(e.amount).replace(/,/g, "") : ""}"></label>
      <label class="f">يوم الاستحقاق في الشهر <input name="day" type="number" min="1" max="31" required value="${v("day_of_month", 1)}"></label>
      <label class="f">يتكرر <select name="every">${Object.entries(EVERY_T).map(([k, t]) => `<option value="${k}" ${e && e.every_months === +k ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      <label class="f">ذكّرني قبله بكم يوم <input name="rd" type="number" min="0" max="60" required value="${v("remind_days", 3)}"></label>
      <label class="f">يُدفع من <select name="from"><option value="outside">من خارج الخزينة</option><option value="drawer" ${e && e.paid_from === "drawer" ? "selected" : ""}>من الخزينة</option></select></label>
      <label class="f">أول شهر <input name="start" type="month" value="${v("start_month", isoDay(new Date()).slice(0, 7))}"></label>
      <label class="f">ملاحظة <input name="note" value="${v("note")}"></label></div>
      ${e ? `<label class="chk"><input type="checkbox" name="active" ${e.is_active ? "checked" : ""}> مفعّل</label>` : ""}
      <div class="row mt"><button class="btn">${e ? "احفظ التعديل" : "أضف"}</button>${e ? `<button type="button" class="btn secondary" id="scCancel">إلغاء</button>` : ""}</div></form>`;
  $("#scForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target, body = { name: f.name.value, category_id: f.cat.value, amount: f.a.value ? toPiasters(f.a.value) : null, day_of_month: +f.day.value,
      every_months: +f.every.value, remind_days: +f.rd.value, paid_from: f.from.value, start_month: f.start.value, note: f.note.value || null };
    if (e) body.is_active = f.active.checked;
    try { await api(`/api/expense-schedules${e ? `/${e.id}` : ""}`, { method: "POST", body }); toast("تم الحفظ"); renderSchedules(cats); }
    catch (err) { toast(err.message, true); }
  });
  $("#scCancel")?.addEventListener("click", () => renderSchedules(cats));
  box.querySelectorAll("[data-scedit]").forEach((b) => b.addEventListener("click", () => { renderSchedules(cats, b.dataset.scedit).then(() => $("#scForm").scrollIntoView()); }));
  box.querySelectorAll("[data-scpay]").forEach((b) => b.addEventListener("click", async () => {
    const amt = box.querySelector(`[data-scamt="${b.dataset.scpay}"]`);
    if (amt && !amt.value) { amt.focus(); toast("اكتب المبلغ هذه المرة", true); return; }
    try { const x = await api(`/api/expense-schedules/${b.dataset.scpay}`, { method: "POST", body: { action: "pay", amount: amt ? toPiasters(amt.value) : null } });
      toast(x.status === "approved" ? "✓ سُجّل واعتُمد" : "سُجّل — بانتظار الموافقة"); viewExpenses(); }
    catch (err) { toast(err.message, true); }
  }));
  box.querySelectorAll("[data-scskip]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirmInline(b)) return;
    try { await api(`/api/expense-schedules/${b.dataset.scskip}`, { method: "POST", body: { action: "skip", period: b.dataset.p } }); renderSchedules(cats); }
    catch (err) { toast(err.message, true); }
  }));
}
function exTable(list, actions) {
  return `<table><thead><tr><th>التاريخ</th><th>البند</th><th>المبلغ</th><th>المصدر</th><th>سجّله</th><th>الحالة</th>${actions ? "<th></th>" : ""}</tr></thead><tbody>
    ${list.map((e) => `<tr><td>${esc(e.spent_on)}</td><td>${esc(e.category)}${e.note ? `<div class="muted">${esc(e.note)}</div>` : ""}</td><td class="num">${money(e.amount)}</td>
      <td>${e.paid_from === "drawer" ? "الخزينة" : "خارجها"}</td><td>${esc(e.by_name || "")}</td>
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
        <div>المبيعات (بعد المرتجع)<b>${money(r.revenue)}</b><span>${r.invoices} فاتورة · خصومات ${money(r.discount)}</span></div>
        <div>تكلفة البضاعة المباعة<b>${money(r.cost)}</b></div>
        <div class="${r.gross_profit >= 0 ? "t-ok" : "t-err"}">مجمل الربح<b>${money(r.gross_profit)}</b>${r.gross_margin_pct != null ? `<span>${r.gross_margin_pct}%</span>` : ""}</div>
        <div>المصروفات المعتمدة<b>${money(r.expenses_total)}</b></div>
        <div class="${r.net_profit >= 0 ? "t-ok" : "t-err"}">${r.net_profit >= 0 ? "✓ صافي الربح" : "✕ صافي خسارة"}<b>${money(Math.abs(r.net_profit))}</b></div></div>
        ${r.pending_expenses.n ? `<p class="badge b-warn mt">⚠ يوجد ${r.pending_expenses.n} مصروف (${money(r.pending_expenses.a)} ج) بانتظار الموافقة وغير محتسب — <a href="#/expenses">راجعها</a></p>` : ""}
        ${r.unknown_cost_lines ? `<p class="badge b-warn mt">⚠ ${r.unknown_cost_lines} سطر بيع بدون تكلفة مسجلة — قد يكون الربح الحقيقي أقل</p>` : ""}
        ${r.delivery_fees ? `<p class="muted">رسوم توصيل: ${money(r.delivery_fees)} ج (محسوبة في مجمل الربح)</p>` : ""}
        <h2 class="mt">المصروفات حسب البند</h2><table><tbody>${r.expenses.map((e) => `<tr><td>${esc(e.category)}</td><td class="num">${money(e.amount)}</td></tr>`).join("") || `<tr><td class="muted">لا يوجد</td></tr>`}</tbody></table>
        <p class="muted">الرواتب المصروفة من شاشة الرواتب تُسجَّل هنا مصروفًا "رواتب وأجور".</p></section>`;
      wireTools("profit", () => ({ title: "تقرير الربح", subtitle: sub, el: $("#prRep"), sheets: [
        { name: "الملخص", columns: [Tx("البيان"), M("المبلغ")], rows: [["المبيعات بعد المرتجع", r.revenue], ["الخصومات", r.discount], ["تكلفة البضاعة", r.cost],
          ["مجمل الربح", r.gross_profit], ["المصروفات المعتمدة", r.expenses_total], ["صافي الربح", r.net_profit], ["رسوم التوصيل", r.delivery_fees || 0]] },
        { name: "المصروفات", columns: [Tx("البند"), M("المبلغ")], rows: r.expenses.map((e) => [e.category, e.amount]), total: ["الإجمالي", r.expenses_total] }] }));
    } catch (err) { $("#prBox").innerHTML = `<p class="badge b-err">✕ ${esc(err.message)}</p>`; }
  });
}

// ------------------------------------------------------------------ home
async function viewHome() {
  const [h, att] = await Promise.all([api("/api/home"), api("/api/attendance")]);
  const hour = new Date().getHours(), greet = hour < 12 ? "صباح الخير" : "مساء الخير";
  const lvl = { error: ["b-err", "✕"], warning: ["b-warn", "⚠"], info: ["b-info", "ℹ"] };
  setTimeout(() => $("#attBtn")?.addEventListener("click", async () => {
    try { await api("/api/attendance", { method: "POST", body: { action: att.open ? "out" : "in" } }); toast(att.open ? "✓ تم تسجيل الانصراف" : "✓ تم تسجيل الحضور"); viewHome(); }
    catch (err) { toast(err.message, true); }
  }), 0);
  const can = (p) => ME.permissions.includes(p);
  const diff = h.sales && h.sales_week_ago ? Math.round((h.sales.total - h.sales_week_ago) / h.sales_week_ago * 100) : null;
  app.innerHTML = `<section class="panel"><h1>${greet} يا ${esc(ME.user.full_name)}</h1>
    <div class="quick">
      ${(ME.ui?.home || []).filter(canScreen).map((k, i) => `<a class="btn ${i === 0 ? "big" : "secondary"}" href="#/${k}">${esc(SCREENS[k][0])}</a>`).join("")}
      ${ME.role !== "owner" ? `<button class="btn ${att.open ? "danger" : ""}" id="attBtn">${att.open ? "سجّل انصراف" : "سجّل حضور"}</button>
        <span class="muted">${att.open ? `حاضر من ${esc(new Date(att.open.check_in).toLocaleTimeString(LOC(), { hour: "2-digit", minute: "2-digit" }))}` : ""} · <a href="#/attendance">حضوري هذا الشهر (${att.days_this_month} يوم)</a></span>` : ""}</div></section>
  <div class="totals">
    ${h.sales ? `<div>مبيعات اليوم<b>${money(h.sales.total)}</b><span>${h.sales.n} فاتورة${diff != null ? ` · ${diff >= 0 ? "▲" : "▼"} ${Math.abs(diff)}% عن اليوم نفسه من الأسبوع الماضي` : ""}</span></div>` : ""}
    <div>فواتيرك اليوم<b>${h.mine.n}</b><span>${money(h.mine.t)} ج</span></div>
    <div class="${h.shift_open ? "t-ok" : "t-warn"}">${h.shift_open ? "✓ الوردية مفتوحة" : "⚠ الوردية مغلقة"}<b>${h.shift_open ? "جارية" : "افتحها"}</b></div>
    ${h.backup ? `<div class="${!h.backup.last_at && h.backup.ok == null ? "t-warn" : h.backup.ok ? "t-ok" : "t-err"}">${!h.backup.last_at && h.backup.ok == null ? "⚠ لا توجد نسخة تلقائية بعد" : h.backup.ok ? "✓ آخر نسخة احتياطية" : "✕ النسخة الاحتياطية فشلت"}<b>${h.backup.last_at ? esc(h.backup.last_at.slice(11, 16)) : "—"}</b><span>${h.backup.offsite_error ? "⚠ النسخة خارج الجهاز بها مشكلة" : ""}</span></div>` : ""}
  </div>
  <section class="panel mt"><h2>يحتاج إلى انتباهك</h2>
    <ul class="notes">${h.alerts.map((a) => `<li><span class="badge ${lvl[a.level][0]}">${lvl[a.level][1]}</span><div>${esc(a.text)} ${a.link ? `<a href="${a.link}">افتح</a>` : ""}</div></li>`).join("")
      || `<li><span class="badge b-ok">✓</span><div>كل شيء سليم</div></li>`}</ul>
    ${h.notifications_unread ? `<p class="mt"><a href="#/notifications">🔔 ${h.notifications_unread} إشعار جديد</a></p>` : ""}</section>`;
}

// ------------------------------------------------------------------ reports
// ------------------------------------------------------------------ periods, Excel export, print preview
const isoDay = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
function periodPresets() {
  const now = new Date(), y = now.getFullYear(), m = now.getMonth(), day = 86400000;
  return {
    today: ["اليوم", now, now], yesterday: ["أمس", new Date(now - day), new Date(now - day)],
    week: ["آخر 7 أيام", new Date(now - 6 * day), now], month: ["هذا الشهر", new Date(y, m, 1), now],
    last: ["الشهر الماضي", new Date(y, m - 1, 1), new Date(y, m, 0)], year: ["هذه السنة", new Date(y, 0, 1), now],
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
  const go = () => { if (f.value && t.value) onChange(f.value, t.value); };
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
  const now = new Date().toLocaleString(LOC(), { dateStyle: "medium", timeStyle: "short" });
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
      <th>الخصم</th><th>نسبة الخصم</th><th>مرتجعات سجّلها</th><th>مرتجعات من فواتيره</th><th>فواتير ألغاها</th><th>ورديات أغلقها</th>${r.see_cash ? "<th>فرق الخزينة</th>" : ""}
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
      N("مرتجعات سجّلها"), M("قيمتها"), M("مرتجعات من فواتيره"), N("فواتير ألغاها"), M("قيمتها"), N("ورديات أغلقها"), ...(r.see_cash ? [M("فرق الخزينة")] : []), N("تحذيرات أكّدها"), N("تجاوز سلامة"), Pc("ساعات الحضور")],
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
    html: (r) => `<div class="totals"><div>ورديات أُغلقت<b>${r.totals.n}</b></div><div class="${r.totals.short ? "t-err" : ""}">العجز<b>${money(r.totals.short)}</b><span>${r.totals.short_n} <span>وردية</span></span></div>
      <div>الزيادة<b>${money(r.totals.over)}</b><span>${r.totals.over_n} <span>وردية</span></span></div><div>الصافي<b>${money(r.totals.net)}</b></div></div>
      <div class="tablewrap mt"><table><thead><tr><th>فُتحت</th><th>أُغلقت</th><th>فتحها</th><th>أغلقها</th><th>البداية</th><th>فواتير</th><th>بيع نقدي</th><th>مرتجع/إلغاء</th><th>مصروف من الخزينة</th><th>المفروض</th><th>المعدود</th><th>الفرق</th><th>السبب</th></tr></thead><tbody>
      ${r.rows.map((x) => `<tr><td>${esc(stamp(x.opened_at))}</td><td>${esc(stamp(x.closed_at))}</td><td>${esc(x.opened_by_name || "")}</td><td>${esc(x.closed_by_name || "")}</td>
        <td class="num">${money(x.opening_cash)}</td><td>${x.sales_n}</td><td class="num">${money(x.cash_sales)}</td><td class="num">${money(x.refunds)}</td><td class="num">${money(x.payouts)}</td>
        <td class="num">${money(x.expected_cash)}</td><td class="num">${money(x.counted_cash)}</td>
        <td class="num">${x.difference ? `<span class="badge ${x.difference < 0 ? "b-err" : "b-warn"}">${money(x.difference)}</span>` : `<span class="badge b-ok">✓ مطابق</span>`}</td><td>${esc(x.close_note || "")}</td></tr>`).join("")
        || `<tr><td colspan="13" class="muted">لا توجد ورديات أُغلقت في هذه الفترة</td></tr>`}</tbody></table></div>`,
    sheets: (r) => [{ name: "إغلاقات الخزينة", columns: [Tx("فُتحت"), Tx("أُغلقت"), Tx("فتحها"), Tx("أغلقها"), M("البداية"), N("فواتير"), M("بيع نقدي"), M("مرتجع/إلغاء"), M("مصروف من الخزينة"), M("المفروض"), M("المعدود"), M("الفرق"), Tx("السبب")],
      rows: r.rows.map((x) => [stamp(x.opened_at), stamp(x.closed_at), x.opened_by_name || "", x.closed_by_name || "", x.opening_cash, x.sales_n, x.cash_sales, x.refunds, x.payouts, x.expected_cash, x.counted_cash, x.difference, x.close_note || ""]),
      total: ["الإجمالي", "", "", "", null, null, null, null, null, null, null, r.totals.net, ""] }] },
  { key: "returns", title: "المرتجعات", perm: "sales.totals",
    html: (r) => `<div class="totals"><div>المرتجعات<b>${money(r.totals.total)}</b><span>${r.totals.n} <span>مرة</span></span></div><div>من المبيعات<b>${pctTxt(r.totals.pct_bp)}</b></div>
      ${Object.entries(r.totals.by_method).map(([m, v]) => `<div>${esc({ cash: "نقدي", card: "بطاقة", credit: "على الحساب" }[m] || m)}<b>${money(v)}</b></div>`).join("")}
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
  app.innerHTML = `<section class="panel"><div class="row between"><h1>التقارير</h1><span class="row">${can("sales.totals") ? `<a class="btn" href="#/owner-reports">تقارير المالك: الموظفون، العملاء، الخزينة، المرتجعات، الخصومات، الأسعار…</a>` : ""}${can("costs.read") ? `<a href="#/profit">تقرير الربح ←</a>` : ""}</span></div>
    ${can("sales.totals") ? rangeBar("rRange") : `<p class="muted">تقارير المبيعات للمدير والمالك.</p>`}
    <p class="muted noprint">تقارير أخرى: <a href="#/customers">العملاء</a> · <a href="#/sales">فواتير البيع</a> · <a href="#/expenses">المصروفات</a> · <a href="#/suppliers">مطالبات الموردين</a> · <a href="#/transfers">مراجعة التحويلات</a> · <a href="#/supplier-returns">مرتجعات للموردين</a> · <a href="#/counts">الجرد</a> · <a href="#/deadstock">الرواكد</a> · <a href="#/shortages">النواقص</a></p></section>
  <div id="rSales"></div>${can("sales.totals") ? `<section class="panel mt" id="rWarn"><div class="row between"><h2>تحذيرات الأصناف المؤكدة وقت البيع</h2>${reportTools("warn")}</div>${rangeBar("warnRange", "week")}<div id="warnBox"></div></section>` : ""}${can("costs.read") ? `<section class="panel mt" id="rLoss"><div class="row between"><h2>خسائر المخزون (منتهي، تالف، مفقود، تسويات الجرد) والمرتجع للموردين</h2>${reportTools("loss")}</div>${rangeBar("lossRange")}<div id="lossBox"></div></section>` : ""}<div id="rStock"></div>`;
  const it = (r) => ({ pack_size: r.pack_size, base_unit_name: r.base_unit_name });
  const sales = async (f, t) => {
    const r = await api(`/api/reports/sales?from=${f}&to=${t}`);
    const maxH = Math.max(1, ...r.by_hour.map((x) => x.total)), sub = rangeText(r.from, r.to);
    $("#rSales").innerHTML = `<section class="panel" id="rSalesBox"><div class="row between"><h2>المبيعات — ${esc(sub)}</h2>${reportTools("sales")}</div>
    <div class="totals"><div>الفواتير<b>${r.totals.n}</b></div><div>المبيعات<b>${money(r.totals.total)}</b></div>
      <div>المرتجع<b>${money(r.totals.returns)}</b></div><div>الخصومات<b>${money(r.totals.discount)}</b></div></div>
    <div class="grid2 mt"><section class="panel"><h2>يوم بيوم</h2>
      <table><thead><tr><th>اليوم</th><th>فواتير</th><th>الإجمالي</th><th>نقدي</th><th>بطاقة</th><th>تحويل</th><th>آجل</th><th>مرتجع</th></tr></thead><tbody>
      ${r.daily.map((d) => `<tr><td>${esc(d.day)}</td><td>${d.n}</td><td class="num"><b>${money(d.total)}</b></td><td class="num">${money(d.cash)}</td><td class="num">${money(d.card)}</td><td class="num">${money(d.transfer)}</td><td class="num">${money(d.credit)}</td><td class="num">${money(d.returns)}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">لا يوجد</td></tr>`}</tbody></table></section>
    <section class="panel"><h2>حسب الموظف</h2><table><tbody>${r.by_user.map((u) => `<tr><td>${esc(u.name)}</td><td>${u.n} فاتورة</td><td class="num">${money(u.total)}</td><td class="num">${u.discount ? `خصم ${money(u.discount)}` : ""}</td></tr>`).join("") || `<tr><td class="muted">لا يوجد</td></tr>`}</tbody></table>
      <h2 class="mt">ساعات العمل</h2><div class="hbars">${Array.from({ length: 24 }, (_, h) => { const x = r.by_hour.find((y) => y.hour === h); return `<div title="${h}:00 — ${x ? x.n : 0} فاتورة"><span data-h="${Math.round(((x?.total || 0) / maxH) * 100)}"></span><small>${h}</small></div>`; }).join("")}</div></section></div>
    <section class="panel mt"><h2>أكثر الأصناف مبيعًا</h2><table><thead><tr><th>الصنف</th><th>الكمية</th><th>المبيعات</th>${can("costs.read") ? "<th>الربح</th>" : ""}</tr></thead><tbody>
      ${r.top_items.map((x) => `<tr><td>${itemHtml(x)}</td><td>${esc(fmtStock(x.qty, it(x)))}</td><td class="num">${money(x.revenue)}</td>${can("costs.read") ? `<td class="num">${x.profit != null ? money(x.profit) : "—"}</td>` : ""}</tr>`).join("") || `<tr><td colspan="4" class="muted">لا يوجد</td></tr>`}</tbody></table></section></section>`;
    document.querySelectorAll(".hbars [data-h]").forEach((el) => { el.style.height = `${el.dataset.h}%`; });
    wireTools("sales", () => ({ title: "تقرير المبيعات", subtitle: sub, el: $("#rSalesBox"), sheets: [
      { name: "يوم بيوم", columns: [Dt("اليوم"), N("فواتير"), M("الإجمالي"), M("نقدي"), M("بطاقة"), M("تحويل"), M("آجل"), M("مرتجع")],
        rows: r.daily.map((d) => [d.day, d.n, d.total, d.cash, d.card, d.transfer, d.credit, d.returns]),
        total: ["الإجمالي", r.totals.n, r.totals.total, null, null, null, null, r.totals.returns] },
      { name: "حسب الموظف", columns: [Tx("الموظف"), N("فواتير"), M("الإجمالي"), M("الخصم")], rows: r.by_user.map((u) => [u.name, u.n, u.total, u.discount || 0]) },
      { name: "أكثر الأصناف", columns: [Tx("الصنف"), N("الكمية بالوحدة"), M("المبيعات"), ...(can("costs.read") ? [M("الربح")] : [])],
        rows: r.top_items.map((x) => [x.name_en || x.name_ar, x.qty, x.revenue, ...(can("costs.read") ? [x.profit] : [])]) }] }));
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
  app.innerHTML = `<section class="panel"><div class="row between"><h1>الشكاوي والمقترحات</h1>
      <div class="row"><button class="btn" id="fbNew">+ ملاحظة جديدة</button>${owner && items.length ? `<button class="btn secondary" id="fbDl">نزّلها (لإرسالها إلى التطوير)</button>` : ""}</div></div>
    <ul class="notes">${items.map((f) => `<li><span class="badge ${FB_ST[f.status][0]}">${FB_ST[f.status][1]}</span><div class="grow"><b>${FB_KIND[f.kind]}</b> — ${esc(f.by_name || "")} · ${esc(f.created_at.slice(0, 16).replace("T", " "))}${f.screen ? ` · <span class="muted">${esc(f.screen)}</span>` : ""}
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
const tm = (iso) => (iso ? new Date(iso).toLocaleTimeString(LOC(), { hour: "2-digit", minute: "2-digit" }) : "—");
async function viewAttendance() {
  const a = await api("/api/attendance");
  app.innerHTML = `<section class="panel narrowpanel"><h1>حضوري هذا الشهر</h1><p>${a.days_this_month} يوم</p>
    <table><thead><tr><th>اليوم</th><th>حضور</th><th>انصراف</th><th>المصدر</th></tr></thead><tbody>
    ${a.rows.map((r) => `<tr><td>${esc(r.day)}</td><td>${tm(r.check_in)}</td><td>${tm(r.check_out)}</td><td>${r.source === "phone" ? "هاتف" : r.source === "owner" ? "تعديل المالك" : "الكمبيوتر"}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">لا يوجد</td></tr>`}</tbody></table></section>`;
}
async function viewStaff(month) {
  month = month || isoDay(new Date()).slice(0, 7);
  const r = await api(`/api/staff?month=${month}`);
  const post = async (body) => { try { await api("/api/staff", { method: "POST", body: { month, ...body } }); toast("تم الحفظ"); viewStaff(month); } catch (err) { toast(err.message, true); } };
  app.innerHTML = `<section class="panel"><div class="row between"><h1>الحضور والرواتب</h1><span class="row"><label class="f">الشهر <input type="month" id="stM" value="${month}"></label>${reportTools("pay")}</span></div>
    <p class="muted">المرتب = الأساسي − الغياب (الأساسي ÷ أيام العمل × الأيام الناقصة) − السلف + المكافأة − الخصم. تراجع كل راتب وتعتمده، ويُسجَّل الصرف مصروفًا "رواتب وأجور".</p></section>
  ${r.people.map((p) => { const pr = p.payroll, sg = p.suggested; return `<section class="panel staff" data-u="${esc(p.id)}">
    <div class="row between"><h2>${esc(p.full_name)} <span class="chip">${esc(p.role_name)}</span></h2>
      ${pr ? `<span class="badge ${pr.status === "paid" ? "b-ok" : "b-info"}">${pr.status === "paid" ? "✓ صُرف" : "✓ معتمد — لم يُصرف بعد"}: ${money(pr.net)} ج</span>` : ""}</div>
    <div class="row"><label class="f">المرتب الأساسي (ج) <input class="narrow" data-f="sal" type="number" min="0" value="${money(p.monthly_salary)}"></label>
      <label class="f">أيام العمل في الشهر <input class="narrow" data-f="wd" type="number" min="1" max="31" value="${p.work_days}"></label>
      <button class="btn small secondary" data-a="pay_settings">احفظ</button></div>
    <div class="totals mt"><div>حضر<b>${p.days_present} يوم</b><span>${p.hours} ساعة</span></div><div>غياب<b>${sg.missing_days} يوم</b><span>− ${money(sg.absence_deduction)}</span></div>
      <div>سلف الشهر<b>${money(p.advances)}</b></div><div class="t-ok">المقترح<b>${money(sg.net)}</b></div></div>
    ${pr?.status === "paid" ? "" : `<div class="row mt"><label class="f">مكافأة (ج) <input class="narrow" data-f="bonus" type="number" min="0" value="${pr ? money(pr.bonus) : ""}"></label>
      <label class="f">خصم (ج) <input class="narrow" data-f="ded" type="number" min="0" value="${pr ? money(pr.deduction) : ""}"></label>
      <label class="f grow">ملاحظة <input data-f="note" value="${esc(pr?.note || "")}"></label><button class="btn small" data-a="approve">اعتمد المرتب</button>
      ${pr ? `<select data-f="from"><option value="outside">من خارج الخزينة</option><option value="drawer">من خزينة الوردية</option></select><button class="btn small" data-a="pay">صرف ${money(pr.net)} ج</button>` : ""}</div>`}
    <details class="mt"><summary>سلفة / تعديل حضور / تفاصيل الأيام (${p.attendance.length})</summary>
      <div class="row mt"><label class="f">سلفة (ج) <input class="narrow" data-f="adv" type="number" min="0"></label><select data-f="advfrom"><option value="drawer">من الخزينة</option><option value="outside">من الخارج</option></select>
        <button class="btn small secondary" data-a="advance">سجّل السلفة</button></div>
      <div class="row mt"><label class="f">اليوم <input type="date" data-f="day"></label><label class="f">حضور <input type="time" data-f="in" value="10:00"></label>
        <label class="f">انصراف <input type="time" data-f="out" value="18:00"></label><label class="f grow">السبب <input data-f="why" placeholder="مثال: نسي التسجيل"></label><button class="btn small secondary" data-a="fix">سجّل اليوم</button></div>
      <table class="mt"><tbody>${p.attendance.map((x) => `<tr><td>${esc(x.day)}</td><td>${tm(x.check_in)} ← ${tm(x.check_out)}</td><td>${x.source === "owner" ? `<span class="badge b-warn">⚠ تعديل: ${esc(x.note || "")}</span>` : x.source === "phone" ? "هاتف" : "الكمبيوتر"}</td></tr>`).join("") || `<tr><td class="muted">لا يوجد</td></tr>`}</tbody></table></details>
  </section>`; }).join("") || `<section class="panel"><p class="muted">لا يوجد موظفون — أضفهم من "المستخدمين".</p></section>`}`;
  $("#stM").addEventListener("change", (e) => viewStaff(e.target.value));
  wireTools("pay", () => ({ title: "الحضور والرواتب", subtitle: `شهر ${month}`, el: app, sheets: [
    { name: "الرواتب", columns: [Tx("الموظف"), Tx("النوع"), M("الأساسي"), N("أيام العمل"), N("حضر (يوم)"), { label: "ساعات", type: "num" }, N("غياب (يوم)"), M("خصم الغياب"), M("السلف"), M("المكافأة"), M("الخصم"), M("الصافي"), Tx("الحالة")],
      rows: r.people.map((p) => [p.full_name, p.role_name, p.monthly_salary, p.work_days, p.days_present, p.hours, p.suggested.missing_days, p.suggested.absence_deduction, p.advances,
        p.payroll?.bonus || 0, p.payroll?.deduction || 0, p.payroll ? p.payroll.net : p.suggested.net, p.payroll ? (p.payroll.status === "paid" ? "صُرف" : "معتمد") : "مقترح"]) },
    { name: "الحضور", columns: [Tx("الموظف"), Dt("اليوم"), Tx("حضور"), Tx("انصراف"), Tx("ملاحظة")],
      rows: r.people.flatMap((p) => p.attendance.map((x) => [p.full_name, x.day, tm(x.check_in), tm(x.check_out), x.source === "owner" ? `تعديل: ${x.note || ""}` : ""])) }] }));
  document.querySelectorAll(".staff [data-a]").forEach((b) => b.addEventListener("click", () => {
    const box = b.closest(".staff"), f = (n) => box.querySelector(`[data-f="${n}"]`)?.value, uid = box.dataset.u, a = b.dataset.a;
    if (a === "pay_settings") post({ action: a, user_id: uid, monthly_salary: toPiasters(f("sal")) || 0, work_days: +f("wd") || 26 });
    if (a === "approve") post({ action: a, user_id: uid, bonus: toPiasters(f("bonus")) || 0, deduction: toPiasters(f("ded")) || 0, note: f("note") || null });
    if (a === "pay" && confirmInline(b)) post({ action: a, user_id: uid, paid_from: f("from") });
    if (a === "advance") post({ action: a, user_id: uid, amount: toPiasters(f("adv")) || 0, paid_from: f("advfrom"), note: "سلفة" });
    if (a === "fix") post({ action: a, user_id: uid, day: f("day"), check_in: f("in"), check_out: f("out") || null, note: f("why") });
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
        <div class="muted">${esc(n.actor || "")}${n.approver ? ` · بموافقة ${esc(n.approver)}` : ""} · ${new Date(n.created_at).toLocaleString(LOC())}
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
  if (!ME.permissions.includes("users.manage")) { app.innerHTML = `<section class="panel"><p>هذه الصفحة للمالك فقط.</p></section>`; return; }
  const [{ users, roles }, sales] = await Promise.all([api("/api/users"), api("/api/settings/sales")]);
  app.innerHTML = `<section class="panel"><div class="row between"><h1>المستخدمين</h1></div>
    <table><thead><tr><th>الاسم</th><th>النوع</th><th>الحالة</th><th>رقم سري</th><th>تعديلات على الافتراضي</th></tr></thead>
    <tbody>${users.map((u) => `<tr class="clickable" data-go="${esc(u.id)}"><td><b>${esc(u.full_name)}</b><div class="muted">${esc(u.username)}</div></td>
      <td><span class="badge ${ROLE_CLS[u.role] || ""}">${esc(u.role_name)}</span></td>
      <td>${u.active ? `<span class="badge b-ok">✓ مفعّل</span>` : `<span class="badge b-err">✕ موقوف</span>`}</td>
      <td>${u.has_pin ? "✓" : `<span class="badge b-warn">⚠ لا يوجد</span>`}</td>
      <td>${u.role === "owner" ? "كل الصلاحيات" : u.changes ? `${u.changes} تعديل` : "على الافتراضي"}</td></tr>`).join("")}</tbody></table></section>
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
  if (!ME.permissions.includes("users.manage")) { location.hash = "#/users"; return; }
  const [m, { roles }] = await Promise.all([api(`/api/users/${id}`), api("/api/users")]);
  const u = m.user, owner = u.role === "owner";
  const roleName = Object.fromEntries(roles.map((r) => [r.code, r.name]));
  const cell = (v) => (v ? `<span class="ok">✓ مسموح</span>` : `<span class="muted">✕ ممنوع</span>`);
  const pick = (it) => {
    if (owner) return `<span class="ok">✓ مسموح</span>`;
    if (it.code === "users.manage") return `<span class="muted">للمالك فقط</span>`;
    const def = it.roles[u.role];
    return `<select data-perm="${it.code}" class="${it.exception === "grant" ? "ex-grant" : it.exception === "deny" ? "ex-deny" : ""}">
      <option value="default" ${!it.exception ? "selected" : ""}>${def ? "✓ مسموح" : "✕ ممنوع"} (الافتراضي)</option>
      ${def ? `<option value="deny" ${it.exception === "deny" ? "selected" : ""}>✕ ممنوعة عنه</option>`
            : `<option value="grant" ${it.exception === "grant" ? "selected" : ""}>✓ مسموحة له</option>`}
    </select>`;
  };
  app.innerHTML = `<section class="panel"><div class="row between">
      <div><a href="#/users">← المستخدمين</a><h1>${esc(u.full_name)} <span class="badge ${ROLE_CLS[u.role] || ""}">${esc(u.role_name)}</span></h1>
      <p class="muted">${owner ? "للمالك كل الصلاحيات، ولا يستطيع أحد منعه من شيء." : "صلاحياته مأخوذة من نوعه. أي تعديل هنا يخصه وحده."}</p></div>
      ${owner ? "" : `<button class="btn secondary" id="uReset">↺ أعده إلى الافتراضي</button>`}</div>
    ${owner ? "" : `<div class="row">
      <label class="f">النوع <select id="uRole">${roles.filter((r) => r.code !== "owner").map((r) => `<option value="${r.code}" ${r.code === u.role ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select></label>
      <label class="f">أقصى خصم % (فارغ = حد نوعه ${pctOf(m.role_discount_bp[u.role])}%)
        <span class="row"><input id="uDisc" class="narrow" type="number" min="0" max="100" step="0.5" value="${pctOf(m.max_discount_bp)}">
        <button class="btn small secondary" id="uDiscSave">احفظ</button></span></label>
      <button class="btn small ${u.active ? "danger" : ""}" id="uActive">${u.active ? "إيقاف المستخدم" : "تشغيل المستخدم"}</button></div>`}
    <div class="row mt">
      <form id="uPin" class="row"><label class="f">رقم سري جديد <input name="pin" type="password" inputmode="numeric" maxlength="6" autocomplete="off" required></label><button class="btn small secondary">احفظ الرقم</button></form>
      ${owner ? "" : `<form id="uPw" class="row"><label class="f">كلمة مرور جديدة <input name="password" type="password" minlength="10" autocomplete="new-password" required></label><button class="btn small secondary">غيّر كلمة المرور</button></form>`}
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
  $("#uPin").addEventListener("submit", (e) => { e.preventDefault(); post("pin", { pin: e.target.pin.value }); });
  $("#uPw")?.addEventListener("submit", (e) => { e.preventDefault(); post("password", { password: e.target.password.value }); });
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
route();
