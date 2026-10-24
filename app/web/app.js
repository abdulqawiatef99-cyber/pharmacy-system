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
const PREFS_DEFAULT = { theme: "light", accent: null, font: "m", lang: "ar", names: "both" };
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
async function savePrefs(changes) {
  const p = await api("/api/me/prefs", { method: "PUT", body: changes });
  if (ME) ME.prefs = p;
  applyPrefs(p);
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
  ? `<p class="badge b-warn search-hint">⚠ الكيبورد كان على اللغة التانية — بدوّر على: <bdi>${esc(h.query)}</bdi></p>`
  : `<p class="badge b-info search-hint">ℹ مفيش اسم مطابق بالظبط — دي أقرب أصناف</p>`);

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
    toast(`${message}. مفيش صاحب أو مدير عنده رقم سري للموافقة — الصاحب يحط رقمه من صفحة "حسابي"`, true);
    return false;
  }
  let dlg = $("#approveDlg");
  if (!dlg) { dlg = document.createElement("dialog"); dlg.id = "approveDlg"; document.body.append(dlg); }
  dlg.innerHTML = `<form method="dialog" class="approve">
    <h2>محتاج موافقة</h2>
    <p class="badge b-warn">⚠ ${esc(details?.label || message)}</p>
    <p class="muted">${esc(message)}</p>
    <label class="f">مين هيوافق <select name="who">${list.map((a) => `<option value="${esc(a.id)}">${esc(a.full_name)} — ${esc(a.role_name)}</option>`).join("")}</select></label>
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
        err.textContent = !r.ok ? `✕ ${d?.error?.message || "الرقم السري غلط"}` : "✕ اللي اخترته مش عنده الصلاحية دي";
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
  [/^#\/shift$/, viewShift],
  [/^#\/suppliers$/, viewSuppliers],
  [/^#\/suppliers\/([\w-]+)$/, viewSupplier],
  [/^#\/shortages$/, viewShortages],
  [/^#\/customers$/, viewCustomers],
  [/^#\/expenses$/, viewExpenses],
  [/^#\/home$/, viewHome],
  [/^#\/reports$/, viewReports],
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
    if (ME.prefs) applyPrefs(ME.prefs);
    $("#who").textContent = `${ME.user.full_name} (${ME.role_name || ""}) — ${ME.organization.name}`;
    $("#navUsers").hidden = !ME.permissions.includes("users.manage");
    $("#navProfit").hidden = !ME.permissions.includes("costs.read");
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
window.addEventListener("hashchange", route);
window.addEventListener("hashchange", () => { const m = $("#navMore"); if (m) m.open = false; });
document.addEventListener("click", (e) => { const m = $("#navMore"); if (m?.open && !m.contains(e.target)) m.open = false; });
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
    <h1>أهلًا بيك</h1>
    <p class="muted">دي أول مرة البرنامج يشتغل على الجهاز ده. اكتب البيانات دي مرة واحدة بس.</p>
    <label class="f">اسم الصيدلية <input name="pharmacy" required minlength="2" placeholder="مثال: صيدلية عبد القوي"></label><br>
    <label class="f">اسمك <input name="full_name" placeholder="مثال: د. عبد القوي"></label><br>
    <label class="f">اسم المستخدم (للدخول) <input name="username" required minlength="3" autocomplete="username" value="owner"></label><br>
    <label class="f">كلمة السر (10 حروف أو أرقام على الأقل) <input name="password" type="password" required minlength="10" autocomplete="new-password"></label><br>
    <label class="f">اكتب كلمة السر تاني <input name="password2" type="password" required minlength="10" autocomplete="new-password"></label><br>
    <button class="btn" type="submit">ابدأ</button>
    <p class="muted">احفظ كلمة السر في مكان آمن. من غيرها مش هتقدر تدخل.</p>
  </form>
  <form class="panel login" id="restoreForm">
    <h2>أو: عندك بيانات من قبل؟</h2>
    <p class="muted">لو البرنامج كان شغال قبل كده (على الجهاز ده أو جهاز تاني)، اختار آخر نسخة احتياطية، وهي ملف بيبدأ اسمه بـ pharmacy-backup. هتدخل بنفس اسم المستخدم وكلمة السر القديمة.</p>
    <input type="file" name="file" accept=".zip" required><br>
    <button class="btn secondary" type="submit">استرجع بياناتي</button>
  </form>`;
  $("#restoreForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector("button"); btn.disabled = true; btn.textContent = "بيراجع الملف…";
    try {
      await api("/api/setup/restore", { method: "POST", form: new FormData(e.target) });
      btn.textContent = "بيسترجع ويعيد التشغيل…";
      const wait = async () => { try { const r = await fetch("/api/setup"); if (r.ok && !(await r.json()).needed) { location.hash = "#/login"; location.reload(); return; } } catch { /* restarting */ } setTimeout(wait, 1500); };
      setTimeout(wait, 3000);
    } catch (err) { btn.disabled = false; btn.textContent = "استرجع بياناتي"; toast(err.message, true); }
  });
  $("#setupForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    if (f.get("password") !== f.get("password2")) { toast("كلمتين السر مش زي بعض", true); return; }
    try {
      const r = await api("/api/setup", { method: "POST", body: Object.fromEntries(["pharmacy", "full_name", "username", "password"].map((k) => [k, f.get(k)])) });
      ME = r.me; location.hash = "#/import";
      toast("البرنامج جاهز. الخطوة الجاية: نقل بياناتك من البرنامج القديم");
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
      <label class="f">المورد (اختياري — بيتعرف من الفاتورة)
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
    <h2>أو: فاتورة يدوي</h2>
    <form id="manForm" class="row">
      <label class="f">المورد<select name="supplier_id" required><option value="">— اختار —</option>
        ${suppliers.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select></label>
      <label class="f">رقم الفاتورة<input name="invoice_no" required></label>
      <label class="f">التاريخ<input name="invoice_date" type="date" value="${localDay()}"></label>
      <button class="btn" type="submit">ابدأ الفاتورة</button>
    </form>
    <p class="muted">${suppliers.length ? "المورد مش في القايمة؟" : "مفيش موردين متسجلين لسه."} اكتب اسمه:
      <input id="newSup" placeholder="اسم المورد"> <button class="btn small secondary" id="addSup" type="button">إضافة مورد</button></p>
  </section>
  <section class="panel">
    <h2>الفواتير</h2>
    <div class="tablewrap"><table class="list">
      <thead><tr><th>المورد</th><th>رقم الفاتورة</th><th>التاريخ</th><th>الأصناف</th>
        <th>الإجمالي</th><th>الحالة</th><th>المصدر</th></tr></thead>
      <tbody>${list.map((i) => `<tr>
        <td><a href="#/purchases/${esc(i.id)}">${esc(i.supplier_name || "— غير محدد —")}</a></td>
        <td>${esc(i.invoice_no || "")}</td><td>${esc(i.invoice_date || "")}</td><td>${i.lines}</td>
        <td>${money(i.declared_total ?? i.computed_total)}</td>
        <td><span class="badge ${STATUS[i.status][1]}">${STATUS[i.status][0]}</span></td>
        <td>${esc(i.source_type)}</td></tr>`).join("") ||
        `<tr><td colspan="7" class="muted">لا توجد فواتير بعد</td></tr>`}</tbody>
    </table></div>
  </section>`;
  $("#addSup").addEventListener("click", async () => {
    const name = $("#newSup").value.trim(); if (!name) return;
    try { await api("/api/suppliers", { method: "POST", body: { name } }); toast("المورد اتضاف"); viewPurchases(); }
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
      <div class="${diff == null ? "" : diff === 0 ? "t-ok" : totalErr ? "t-err" : "t-warn"}">${diff == null ? "الفرق" : diff === 0 ? "✓ الإجماليين زي بعض" : totalErr ? "✕ الفرق" : "⚠ الفرق (مقبول)"}<b>${diff == null ? "—" : money(diff)}</b>
        ${totalErr ? `<span>سطر زيادة أو ناقص سطر؟ راجع قبل الاعتماد</span>` : ""}</div>
      <div>عدد الأصناف<b>${lines.length}</b></div>
    </div>
    <h2 class="mt">${errs.length ? `⛔ ${errs.length} مشكلة لازم تتحل قبل الاعتماد` : "✅ لا توجد مشكلات تمنع الاعتماد"}</h2>
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
        if (!res.length) { $("#qaRes").innerHTML = `<p class="badge b-warn">⚠ الكود المربع ده مش متسجل على صنف. سجّله من كارت الصنف.</p>`; return; }
        pick(res[0]);
        if (found.hint.expiry) { const [y, m] = found.hint.expiry.split("-"); $("#qaExp").value = `${+m}-${y.slice(2)}`; }
        if (found.hint.batch) $("#qaBatch").value = found.hint.batch;
        $("#qaPicked").textContent += found.hint.expiry ? ` · الصلاحية والتشغيلة اتقروا من الكود` : "";
        return;
      }
      if (res.length === 1 && /^\d{6,}$/.test(v)) { pick(res[0]); return; }
      $("#qaRes").innerHTML = hintHtml(found.hint) + (res.map((r, i) => `<button type="button" class="btn small secondary" data-qa="${i}">${itemHtml(r)}
        <span class="muted">${esc(r.strength_norm || "")} · ${money(r.public_price)} ج</span></button>`).join("") || `<p class="muted">مفيش نتائج</p>`);
      $("#qaRes").querySelectorAll("[data-qa]").forEach((b) => b.addEventListener("click", () => pick(res[+b.dataset.qa])));
    }, 250);
  });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter" && res.length && !QA_ITEM) { e.preventDefault(); pick(res[0]); } });
  const add = async () => {
    if (!QA_ITEM) { toast("اختار الصنف الأول", true); q.focus(); return; }
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
  if (!cur) { box.innerHTML = `<span class="muted">اختار المورد</span>`; return; }
  const canEdit = ME.permissions.includes("suppliers.write");
  box.innerHTML = Object.entries(SUP_TYPE).map(([v, t]) => `<button type="button" class="${cur.supplier_type === v ? "on" : ""}" data-st="${v}" ${canEdit ? "" : "disabled"}>${cur.supplier_type === v ? "✓ " : ""}${t}</button>`).join("");
  box.querySelectorAll("[data-st]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/suppliers/${cur.id}`, { method: "PATCH", body: { supplier_type: b.dataset.st } }); fillSuppliers(selected, draft); toast("اتحفظ نوع المورد"); }
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
    const reason = prompt("اكتب سبب قبول الفرق (يتسجل باسمك):");
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
        if (confirm(`يوجد صنف مشابه: ${d}\nهل أنت متأكد إنه صنف مختلف؟`)) {
          CUR = await api(`${base}/lines/${line.id}/new-item`, { method: "POST",
            body: { item: { name_en: name, pack_size: pack }, force: true, version: CUR.invoice.version } });
          renderInvoice();
        }
      } else toast(err.message, true);
    }
  }
});

// ------------------------------------------------------------------ items
const MARK = { fridge: ["mk-fridge", "❄ تلاجة"], injection: ["mk-inj", "💉 حقن"] };
const markChip = (m) => (MARK[m] ? `<span class="chip ${MARK[m][0]}">${MARK[m][1]}</span>` : "");
let CATS = null;
async function loadCats(force = false) {
  if (!CATS || force) CATS = (await api("/api/categories")).categories;
  return CATS;
}
const canW = () => ME.permissions.includes("items.write");

async function viewItems() {
  const cats = await loadCats(true);
  app.innerHTML = `
  <section class="panel">
    <div class="row between"><h1>الأصناف</h1>
      <div class="row">${canW() ? `<a class="btn" href="#/items/new">+ صنف جديد</a><a class="btn secondary" href="#/categories">الأقسام</a>` : ""}
      <a href="/api/export/items.xlsx">تصدير Excel</a></div></div>
    <div class="row"><label class="f grow">بحث بالاسم (عربي أو إنجليزي) أو الكود أو الباركود
      <input id="q" placeholder="مثال: panadol أو بنادول أو asp%cid أو 622..." autocomplete="off"></label>
      <label class="f">القسم <select id="qCat"><option value="">كل الأقسام</option><option value="none">من غير قسم</option>
        ${cats.filter((c) => c.is_active).map((c) => `<option value="${esc(c.id)}">${esc(c.name)} (${c.items})</option>`).join("")}</select></label></div>
  </section>
  <section class="panel"><div id="itemsHint"></div>
    ${canW() ? `<div class="row bulk" id="bulk" hidden><b id="bulkN"></b> <select id="bulkCat"><option value="">— اختار قسم —</option>
      ${cats.filter((c) => c.is_active).map((c) => `<option value="${esc(c.id)}">${esc(c.name)}${c.no_return ? " (مالوش مرتجع)" : ""}</option>`).join("")}<option value="none">شيلهم من أي قسم</option></select>
      <button class="btn small" id="bulkGo">حطهم في القسم</button> <button class="btn small secondary" id="bulkLabels">اطبع ملصق لكل واحد</button></div>` : ""}
    <div class="tablewrap"><table>
    <thead><tr>${canW() ? `<th><input type="checkbox" id="selAll" aria-label="اختار الكل"></th>` : ""}<th>الاسم</th><th>القسم</th><th>العبوة</th><th>سعر الجمهور</th><th>الرصيد</th><th>الكود</th></tr></thead>
    <tbody id="itemsBody"></tbody></table></div></section>`;
  let rows = [];
  const sel = new Set();
  const bulkUi = () => { if (!$("#bulk")) return; $("#bulk").hidden = !sel.size; $("#bulkN").textContent = `${sel.size} صنف مختار`; };
  const load = async () => {
    const cat = $("#qCat").value;
    const r = await api(`/api/items?q=${encodeURIComponent($("#q").value)}&limit=100&meta=1${cat ? `&category=${encodeURIComponent(cat)}` : ""}`);
    rows = r.items; sel.clear(); bulkUi();
    $("#itemsHint").innerHTML = hintHtml(r.hint);
    $("#itemsBody").innerHTML = rows.map((it, i) => `<tr class="clickable" data-i="${i}">
      ${canW() ? `<td><input type="checkbox" data-sel="${i}" aria-label="اختار"></td>` : ""}
      <td>${itemHtml(it)}${it.shortage_at ? ` <span class="badge b-warn">⚠ ناقص</span>` : ""}</td>
      <td>${esc(it.category || "—")} ${markChip(it.mark)}${it.no_return ? ` <span class="chip mk-nr">مالوش مرتجع</span>` : ""}</td>
      <td>${it.pack_size} ${esc(unitName(it))}</td><td>${money(it.public_price)}</td>
      <td><b>${esc(fmtStock(it.stock_qty, it)) || "0"}</b>${it.min_stock && it.stock_qty != null && it.stock_qty <= it.min_stock ? ` <span class="badge b-warn">⚠ تحت حد الطلب</span>` : ""}</td>
      <td>${esc(it.code || "")}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">لا توجد نتائج</td></tr>`;
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
  $("#bulkLabels")?.addEventListener("click", () => window.open(`/labels?items=${[...sel].map((x) => `${encodeURIComponent(x)}:1`).join(",")}`, "_blank"));
  $("#bulkGo")?.addEventListener("click", async () => {
    const v = $("#bulkCat").value; if (!v) return toast("اختار القسم", true);
    try { const r = await api("/api/categories/assign", { method: "POST", body: { item_ids: [...sel], category_id: v === "none" ? null : v } });
      toast(`اتحط ${r.updated} صنف`); load(); }
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
  const today = new Date().toISOString().slice(0, 10), soon = new Date(Date.now() + 90 * 864e5).toISOString().slice(0, 10);
  const expCls = (d) => (!d ? "b-plain" : d < today ? "b-err" : d < soon ? "b-warn" : "b-ok");
  app.innerHTML = `
  <section class="panel"><div class="row between">
    <div><a href="#/items">← الأصناف</a>
      <h1 class="ltr-name">${esc(c.name_en || c.name_ar)}</h1>
      ${c.name_en && c.name_ar ? `<p class="muted">${esc(c.name_ar)}</p>` : ""}
      <div class="row tags">${c.category ? `<span class="chip">${esc(c.category)}</span>` : `<span class="chip">من غير قسم</span>`}${markChip(c.mark)}
        ${c.no_return ? `<span class="chip mk-nr">✕ مالوش مرتجع ولا استبدال</span>` : ""}
        ${c.manufacturer ? `<span class="chip">${esc(c.manufacturer)}</span>` : ""}${c.code ? `<span class="chip">كود ${esc(c.code)}</span>` : ""}
        ${c.shortage_at ? `<span class="badge b-warn">⚠ متعلّم ناقص من ${esc(c.shortage_at)}${c.shortage_by_name ? ` — ${esc(c.shortage_by_name)}` : ""}</span>` : ""}</div></div>
    ${canW() ? `<div class="row"><a class="btn" href="#/items/${esc(id)}/edit">تعديل</a>
      <span class="row"><input id="lbN" type="number" min="1" max="500" value="1" class="narrow" title="عدد الملصقات"><button class="btn secondary" id="lbGo">اطبع ملصق باركود</button></span>
      <button class="btn secondary" id="shortBtn">${c.shortage_at ? "شيل علامة ناقص" : "علّمه ناقص"}</button></div>` : ""}
  </div></section>
  <div class="totals">
    <div>الرصيد<b>${c.stock_qty != null ? esc(packs(c.stock_qty, c)) || "0" : "—"}</b>
      ${low ? `<span class="badge b-warn">⚠ تحت حد الطلب (${esc(packs(c.min_stock, c))})</span>` : c.min_stock ? `<span class="muted">حد الطلب ${esc(packs(c.min_stock, c))}</span>` : `<span class="muted">مفيش حد طلب</span>`}</div>
    <div>سعر الجمهور<b>${money(c.public_price)} ج</b>${c.pack_size > 1 && c.public_price ? `<span class="muted">${esc(unitName(c))}: ${money(Math.round(c.public_price / c.pack_size))} ج</span>` : ""}</div>
    ${c.avg_cost_pack != null ? `<div>متوسط التكلفة<b>${money(c.avg_cost_pack)} ج</b><span class="badge ${c.margin_pct >= 0 ? "b-ok" : "b-err"}">ربح ${c.margin_pct}%</span></div>` : ""}
    <div>المكان<b>${esc(c.location || "—")}</b></div>
  </div>
  <div class="grid2 mt">
    <section class="panel"><h2>الوحدات والبيع</h2>
      <div class="totals"><div>العبوة<b>${esc(c.pack_unit_name === "pack" ? "علبة" : c.pack_unit_name)}</b></div>
        <div>بتتباع بـ<b>${esc(unitName(c))}</b></div><div>العبوة فيها<b>${c.pack_size} ${esc(unitName(c))}</b></div>
        <div>الحد الأقصى<b>${c.max_stock ? esc(packs(c.max_stock, c)) : "—"}</b></div></div>
      <h2 class="mt">الباركود والكود الدولي</h2>
      <ul class="plain">${c.barcodes.map((b) => `<li><bdi dir="ltr">${esc(b.code)}</bdi> ${b.gtin ? `<span class="badge b-ok">✓ دولي</span>` : ""}</li>`).join("") || `<li class="muted">مفيش باركود</li>`}</ul>
      <h2 class="mt">البيع آخر 12 شهر</h2>
      <div class="bars" role="img" aria-label="البيع الشهري">${c.monthly.map((m) => `<div title="${esc(m.month)}: ${m.qty}"><span class="${m.source === "import" ? "imp" : ""}" data-h="${Math.round((m.qty / maxM) * 100)}"></span><small>${esc(m.month.slice(5))}</small></div>`).join("")}</div>
      <p class="muted">${c.monthly.some((m) => m.source === "import") ? "الأعمدة الفاتحة من مبيعات البرنامج القديم." : ""} الأرقام بالـ ${esc(unitName(c))}.</p>
    </section>
    <section class="panel"><h2>التشغيلات والصلاحية</h2>
      ${c.batches ? `<table><thead><tr><th>الصلاحية</th><th>التشغيلة</th><th>الكمية</th><th>السعر</th><th>دخلت من</th></tr></thead><tbody>
        ${c.batches.map((b) => `<tr><td><span class="badge ${expCls(b.expiry_date)}">${b.expiry_date && b.expiry_date < today ? "✕ " : b.expiry_date && b.expiry_date < soon ? "⚠ " : ""}${esc(b.expiry_date ? b.expiry_date.slice(0, 7) : "غير مسجلة")}</span></td>
          <td>${esc(b.batch_no || "—")}</td><td>${esc(packs(b.qty, c))}</td><td>${b.public_price != null ? money(b.public_price) : money(c.public_price)}</td><td>${esc(b.supplier || "—")}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">مفيش رصيد</td></tr>`}</tbody></table>
        <p class="badge b-info mt">ℹ كل تشغيلة بسعرها: لو السعر زاد، القديم بيتباع بسعره لحد ما يخلص.</p>` : ""}
      ${c.suppliers ? `<h2 class="mt">الموردين وآخر أسعار شراء</h2><table><thead><tr><th>المورد</th><th>آخر مرة</th><th>الخصم</th><th>سعر الشراء</th></tr></thead><tbody>
        ${c.suppliers.map((sp, i) => `<tr${i === 0 && c.suppliers.length > 1 ? ` class="best"` : ""}><td>${esc(sp.supplier)}${i === 0 && c.suppliers.length > 1 ? ` <span class="badge b-ok">✓ الأرخص</span>` : ""}</td>
          <td>${esc(sp.last_date || "")}</td><td>${sp.discount_bp != null ? pct(sp.discount_bp) + "%" : "—"}</td><td>${money(sp.unit_net_price)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">لسه مفيش شراء متسجل</td></tr>`}</tbody></table>` : ""}
      ${c.price_history?.length ? `<h2 class="mt">آخر تغييرات السعر</h2><ul class="plain">${c.price_history.map((h) => `<li>${esc(h.observed_at.slice(0, 10))}: ${h.previous_price != null ? `${money(h.previous_price)} ← ` : ""}<b>${money(h.public_price)}</b> <span class="muted">${h.source === "manual" ? "يدوي" : h.source === "purchase_invoice" ? "من فاتورة شراء" : "استيراد"}</span></li>`).join("")}</ul>` : ""}
    </section></div>`;
  document.querySelectorAll(".bars [data-h]").forEach((el) => { el.style.height = `${el.dataset.h}%`; });
  $("#lbGo")?.addEventListener("click", () => window.open(`/labels?items=${encodeURIComponent(id)}:${+$("#lbN").value || 1}`, "_blank"));
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
      <label class="f">المادة الفعالة <input name="active_ingredient" dir="ltr" value="${esc(it.active_ingredient || "")}"></label>
      <label class="f">الكود الداخلي <input name="code" value="${esc(it.code || "")}"></label>
      <label class="f">القسم <select name="category_id"><option value="">— من غير قسم —</option>${cats.map((c) => `<option value="${esc(c.id)}" ${c.id === it.category_id ? "selected" : ""}>${esc(c.name)}${c.no_return ? " (مالوش مرتجع)" : ""}</option>`).join("")}</select></label>
      <label class="f">سعر الجمهور للعبوة (ج) <input name="public_price" type="number" step="0.01" min="0" value="${it.public_price != null ? money(it.public_price) : ""}" ${canPrice ? "" : "disabled"}>
        ${canPrice ? "" : `<span class="muted">تحديث السعر للصاحب والمدير</span>`}</label>
      <label class="f">اسم العبوة <input name="pack_unit_name" value="${esc(unitVal(it.pack_unit_name, "علبة"))}"></label>
      <label class="f">اسم الوحدة الصغيرة <input name="base_unit_name" value="${esc(unitVal(it.base_unit_name, "شريط"))}" placeholder="شريط / أمبول / قرص"></label>
      <label class="f">العبوة فيها كام وحدة <input name="pack_size" type="number" min="1" step="1" value="${it.pack_size}" ${id && it.stock_qty ? "disabled" : ""}>
        ${id && it.stock_qty ? `<span class="muted">مقفولة لأن الصنف ليه رصيد؛ صححها بالجرد</span>` : ""}</label>
      <label class="f">حد الطلب (بالعبوة) <input name="min_packs" type="number" min="0" step="1" value="${it.min_stock ? Math.round(it.min_stock / it.pack_size) : ""}"></label>
      <label class="f">الحد الأقصى (بالعبوة) <input name="max_packs" type="number" min="0" step="1" value="${it.max_stock ? Math.round(it.max_stock / it.pack_size) : ""}"></label>
      <label class="f">المكان (الرف) <input name="location" value="${esc(it.location || "")}" placeholder="مثال: أقراص — A"></label>
    </div>
    <h2 class="mt">الباركود والكود الدولي</h2>
    <p class="muted">امسح الباركود أو الكود المربع اللي على العلبة هنا. الكود المربع بيتحفظ بالكود الدولي اللي جواه.</p>
    <ul class="plain" id="bcList">${(it.barcodes || []).map((b) => `<li><bdi dir="ltr">${esc(b.code)}</bdi> ${b.gtin ? `<span class="badge b-ok">✓ دولي</span>` : ""}
      ${id ? `<button type="button" class="btn small secondary" data-rm="${esc(b.code)}">شيل</button>` : ""}</li>`).join("")}</ul>
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
      pack_unit_name: v("pack_unit_name") || "علبة", base_unit_name: v("base_unit_name") || "وحدة",
      min_stock: v("min_packs") ? +v("min_packs") * pack : 0, max_stock: v("max_packs") ? +v("max_packs") * pack : null };
    if (v("pack_size") !== undefined) data.pack_size = pack;
    if (v("public_price") !== undefined) data.public_price = toPiasters(v("public_price"));
    if (id && data.public_price === it.public_price) delete data.public_price;
    if (id && data.pack_size === it.pack_size) delete data.pack_size;
    try {
      let iid = id;
      if (id) await api(`/api/items/${id}`, { method: "PATCH", body: data });
      else {
        try { iid = (await api("/api/items", { method: "POST", body: { item: data, barcodes: newCodes } })).id; }
        catch (err) {
          if (err.code !== "possible_duplicate") throw err;
          const d = (err.details?.duplicates || []).map((x) => x.name || x.name_en).join("، ");
          if (!confirm(`فيه صنف شبهه: ${d}\nمتأكد إنه صنف مختلف؟`)) return;
          iid = (await api("/api/items", { method: "POST", body: { item: data, barcodes: newCodes, force: true } })).id;
        }
      }
      const cat = f.category_id.value || null;
      if (cat !== (it.category_id || null)) await api(`/api/items/${iid}/category`, { method: "POST", body: { category_id: cat } });
      toast("اتحفظ"); location.hash = `#/items/${iid}`;
    } catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ sections (أقسام)
async function viewCategories() {
  const cats = await loadCats(true), owner = ME.permissions.includes("items.no_return");
  app.innerHTML = `<section class="panel"><div class="row between"><div><a href="#/items">← الأصناف</a><h1>الأقسام</h1></div></div>
    <p class="muted">الأقسام بتنظم الأصناف. القسم اللي "مالوش مرتجع" محدش يقدر يرجّع أصنافه غير الصاحب (أو حد الصاحب فتحله). العلامة بتظهر جنب الصنف في الفواتير.</p>
    <table><thead><tr><th>القسم</th><th>عدد الأصناف</th><th>مرتجع</th><th>علامة في الفاتورة</th><th>شغال</th><th></th></tr></thead>
    <tbody>${cats.map((c) => `<tr data-id="${esc(c.id)}"><td><input name="name" value="${esc(c.name)}" ${canW() ? "" : "disabled"}></td><td>${c.items}</td>
      <td><select name="no_return" ${owner ? "" : "disabled"}><option value="0" ${!c.no_return ? "selected" : ""}>✓ ليه مرتجع</option><option value="1" ${c.no_return ? "selected" : ""}>✕ مالوش مرتجع</option></select></td>
      <td><select name="mark" ${canW() ? "" : "disabled"}><option value="">بدون</option><option value="fridge" ${c.mark === "fridge" ? "selected" : ""}>❄ تلاجة</option><option value="injection" ${c.mark === "injection" ? "selected" : ""}>💉 حقن</option></select></td>
      <td><input type="checkbox" name="is_active" ${c.is_active ? "checked" : ""} ${canW() ? "" : "disabled"}></td>
      <td>${canW() ? `<button class="btn small secondary" data-save>احفظ</button>` : ""}</td></tr>`).join("")}</tbody></table>
    ${owner ? "" : `<p class="muted">تغيير "ليه مرتجع / مالوش مرتجع" للصاحب بس.</p>`}</section>
    ${canW() ? `<form class="panel row" id="catNew"><label class="f">قسم جديد <input name="name" required></label><button class="btn">أضف</button></form>` : ""}`;
  document.querySelectorAll("[data-save]").forEach((b) => b.addEventListener("click", async () => {
    const tr = b.closest("tr"), g = (n) => tr.querySelector(`[name=${n}]`);
    try {
      await api(`/api/categories/${tr.dataset.id}`, { method: "PATCH", body: { name: g("name").value, no_return: g("no_return").value === "1",
        mark: g("mark").value || null, is_active: g("is_active").checked } });
      toast("اتحفظ"); viewCategories();
    } catch (err) { toast(err.message, true); }
  }));
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
  item_has_history: "الصنف له حركات سابقة", no_batch_or_expiry: "بدون تشغيلة/صلاحية (هيتسجل بدونها)",
  cost_missing: "بدون تكلفة", matched_by_name_only: "اتطابق بالاسم فقط — أكّد",
  price_below_cost: "سعر البيع أقل من التكلفة", party_has_history: "له حركات سابقة — الرصيد الافتتاحي ممكن يتكرر",
  same_name_exists: "عميل بنفس الاسم موجود", duplicate_in_file: "مكرر في الملف", invalid_phone: "رقم تليفون غير صحيح",
  invalid_balance: "رصيد غير صحيح", invalid_credit_limit: "حد ائتمان غير صحيح",
  unknown_is_imported: "محلي/مستورد غير مفهوم", unknown_is_drug: "دواء/غير دواء غير مفهوم",
};
const ACTION_AR = { create: "إضافة", update: "تحديث", link: "ربط", skip: "تجاهل", review: "مراجعة", error: "خطأ" };

function viewImport() {
  app.innerHTML = `
  <section class="panel">
    <h1>نقل البيانات من برنامج آخر</h1>
    <p class="muted">الخطوات: رفع الملف ← تقرير مسبق (بدون أي تعديل) ← مراجعة ← تطبيق مرة واحدة. الترتيب: الأصناف ← الأرصدة الافتتاحية ← الموردين ← العملاء.</p>
    <p>قوالب جاهزة: <a href="/api/imports/templates/items.xlsx">الأصناف</a> · <a href="/api/imports/templates/opening_stock.xlsx">الأرصدة</a> ·
      <a href="/api/imports/templates/suppliers.xlsx">الموردين</a> · <a href="/api/imports/templates/customers.xlsx">العملاء</a></p>
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
          <p class="muted">أسماء الأعمدة مش معروفة. ابعت الملف ده للمطور عشان يضيف الأسماء دي.</p></section>`);
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
    <p class="muted">الصق نص المنشور، وراجع أرقام التشغيلات المقترحة قبل الحفظ. النظام بيقارنها بالتشغيلات المسجلة عندك من فواتير الشراء، ومش بيحرك المخزون لوحده.</p>
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
    <thead><tr><th>الصنف عندك</th><th>التشغيلة</th><th>الكمية في المخزون</th><th>المنشور</th><th>أساس المطابقة</th><th>القرار</th></tr></thead>
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
    const note = b.dataset.st === "not_ours" ? prompt("سبب إنها ليست نفس المستحضر:") : null;
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
      <label class="f">النطاق <select name="scope"><option value="full">شامل</option><option value="location">مواقع محددة</option></select></label>
      <label class="f">المواقع (لو النطاق مواقع) <input name="locations" placeholder="A1, A2"></label>
      <button class="btn" type="submit">بدء جرد</button></form>` : ""}
  </section>
  <section class="panel"><table><thead><tr><th>الجرد</th><th>النطاق</th><th>الحالة</th><th>التاريخ</th></tr></thead>
    <tbody>${list.map((c) => `<tr><td><a href="#/counts/${esc(c.id)}">${esc(c.title)}</a></td><td>${esc(c.scope)}</td>
      <td>${{ open: "مفتوح", approved: "معتمد", cancelled: "ملغي" }[c.status]}</td><td>${esc(c.created_at.slice(0, 10))}</td></tr>`).join("")
      || `<tr><td colspan="4" class="muted">لا يوجد</td></tr>`}</tbody></table></section>`;
  $("#cntForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      const r = await api("/api/counts", { method: "POST", body: { title: f.title.value, scope: f.scope.value,
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
      if (err.code === "already_counted" && confirm(`الصنف اتجرد قبل كده بكمية ${err.details.qty}. تسجيل جرد جديد بدلًا منه؟`)) return save(true);
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
      const v = await api(`/api/counts/${id}/variances`);
      const box = $("#cntVarBox");
      box.className = "";
      box.innerHTML = `<div class="totals"><div>أصناف مجرودة<b>${v.summary.counted_lines}</b></div>
        <div>عجز (وحدات)<b>${v.summary.shortage_units}</b></div><div>زيادة (وحدات)<b>${v.summary.excess_units}</b></div>
        <div>أصناف لم تُجرد ولها رصيد<b>${v.summary.uncounted_items}</b></div></div>
        <div class="tablewrap"><table><thead><tr><th>الصنف</th><th>الرصيد بالنظام</th><th>المجرود</th><th>مباع أثناء الجرد</th><th>الفرق</th><th>القيمة</th><th></th></tr></thead>
        <tbody>${v.lines.map((l) => `<tr class="${l.difference ? "has-err" : ""}"><td>${esc(l.label)}</td><td>${l.system_qty}</td>
          <td>${l.counted}</td><td>${l.moves_after_count}</td><td>${l.difference}</td><td>${esc(l.value ?? "")}</td>
          <td>${l.problem ? `<span class="badge b-err">${l.problem === "mixed_levels" ? "جرد بالتشغيلة وبدونها معًا" : "رصيد سالب"}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>
        <div class="row mt"><button class="btn" id="cntApprove">اعتماد التسويات</button></div>`;
      $("#cntApprove").addEventListener("click", async () => {
        let zero = false, reason = null;
        if (v.summary.uncounted_items && confirm(`${v.summary.uncounted_items} صنف لم يُجرد ولهم رصيد. هل تريد تصفيرهم؟ (إلغاء = تركهم كما هم)`)) {
          zero = true; reason = prompt("سبب التصفير:"); if (!reason) return;
        }
        if (!confirm("اعتماد الجرد وتسجيل التسويات في المخزون؟")) return;
        try { await api(`/api/counts/${id}/approve`, { method: "POST", body: { zero_uncounted: zero, reason } }); toast("تم اعتماد الجرد"); location.hash = "#/counts"; }
        catch (err) { toast(err.message, true); }
      });
    } catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ program / update
async function viewSystem() {
  if (!ME.permissions.includes("settings.manage")) { app.innerHTML = `<section class="panel"><p>الصفحة دي للمالك بس.</p></section>`; return; }
  app.innerHTML = `<section class="panel"><h1>النسخ الاحتياطي</h1><div id="bkBox"><p class="muted">…</p></div></section>
    <section class="panel"><h1>شكل الفاتورة المطبوعة والملصقات</h1><div id="rcBox"></div></section>
    <section class="panel"><h1>مكن الفيزا وطرق التحويل</h1><div id="chBox"></div></section>
    <section class="panel"><h1>البرنامج</h1><div id="sysBox"><p class="muted">بيتأكد من التحديثات…</p></div></section>`;
  renderBackup(await api("/api/backup"));
  renderChannels((await api("/api/payment-channels")).channels);
  renderReceiptCfg(await api("/api/settings/receipt"));
  const st = await api("/api/system/update");
  $("#sysBox").innerHTML = `
    <div class="totals"><div>الإصدار<b>${esc(st.version)}</b></div><div>النسخة المتثبتة<b>${esc((st.installed || "غير معروفة").slice(0, 7))}</b></div>
      <div>آخر نسخة<b>${esc((st.latest || "—").slice(0, 7))}</b></div></div>
    ${st.error ? `<p class="badge b-err">${esc(st.error)}</p>` : st.available
      ? `<p>فيه تحديث جديد. التحديث بياخد نسخة احتياطية من بياناتك الأول، وبياناتك مش بتتلمس.</p>
         <button class="btn" id="updGo">حدّث البرنامج دلوقتي</button>`
      : `<p class="badge b-ok">البرنامج محدّث</p>`}
    <pre id="updOut" class="muted" hidden></pre>`;
  $("#updGo")?.addEventListener("click", async () => {
    const b = $("#updGo"); b.disabled = true; b.textContent = "بيحدّث… (دقيقة أو اتنين)";
    try {
      const r = await api("/api/system/update", { method: "POST", body: {} });
      $("#updOut").hidden = false; $("#updOut").textContent = r.output;
      if (r.restarting) {
        b.textContent = "البرنامج بيعيد تشغيل نفسه…";
        const wait = async () => { try { await fetch("/api/health"); location.reload(true); } catch { setTimeout(wait, 1500); } };
        setTimeout(wait, 4000);
      } else { b.textContent = "خلص. اقفل البرنامج وافتحه تاني"; }
    } catch (e) { b.disabled = false; b.textContent = "حدّث البرنامج دلوقتي"; toast(e.message, true); }
  });
}
function renderReceiptCfg(c) {
  const chk = (k, t) => `<label class="chk"><input type="checkbox" name="${k}" ${c[k] ? "checked" : ""}> ${t}</label>`;
  $("#rcBox").innerHTML = `<form id="rcForm" class="formgrid">
    <label class="f">مقاس ورق الكاشير <select name="paper"><option value="80" ${c.paper === 80 ? "selected" : ""}>80 مم (الافتراضي)</option><option value="58" ${c.paper === 58 ? "selected" : ""}>58 مم</option></select></label>
    <label class="f">اسم الصيدلية على الفاتورة (فاضي = الاسم المسجل) <input name="title" value="${esc(c.title)}"></label>
    <label class="f">سطر 1 (العنوان) <input name="l0" value="${esc(c.lines[0] || "")}"></label>
    <label class="f">سطر 2 (التليفون) <input name="l1" value="${esc(c.lines[1] || "")}"></label>
    <label class="f">آخر سطر <input name="footer" value="${esc(c.footer)}"></label>
    <label class="f">أسماء الأصناف <select name="names"><option value="en" ${c.names === "en" ? "selected" : ""}>إنجليزي</option><option value="ar" ${c.names === "ar" ? "selected" : ""}>عربي</option><option value="both" ${c.names === "both" ? "selected" : ""}>الاتنين</option></select></label>
    <label class="f">مقاس ملصق الباركود <select name="label_size">${["38x25", "50x30", "58x40"].map((v) => `<option value="${v}" ${c.label_size === v ? "selected" : ""}>${v.replace("x", " × ")} مم</option>`).join("")}</select></label>
    <div>${chk("show_cashier", "اسم الكاشير")}${chk("show_customer", "اسم العميل")}${chk("show_expiry", "صلاحية كل صنف")}${chk("show_return_policy", "سياسة المرتجع (والأصناف اللي مالهاش مرتجع)")}</div>
    <div class="row"><button class="btn">احفظ</button><a href="#" id="rcTry">اطبع آخر فاتورة كتجربة</a></div></form>`;
  $("#rcForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try { renderReceiptCfg(await api("/api/settings/receipt", { method: "PUT", body: { paper: +f.paper.value, title: f.title.value, lines: [f.l0.value, f.l1.value],
      footer: f.footer.value, names: f.names.value, label_size: f.label_size.value, show_cashier: f.show_cashier.checked, show_customer: f.show_customer.checked,
      show_expiry: f.show_expiry.checked, show_return_policy: f.show_return_policy.checked } })); toast("اتحفظ"); }
    catch (err) { toast(err.message, true); }
  });
  $("#rcTry").addEventListener("click", async (e) => {
    e.preventDefault();
    const last = (await api("/api/sales"))[0];
    if (!last) return toast("مفيش فواتير النهارده", true);
    window.open(`/receipt/${last.id}`, "_blank");
  });
}
function renderChannels(list) {
  CHANNELS = null;
  $("#chBox").innerHTML = `<p class="muted">اللي هنا بيظهر في الكاشير لما تختار فيزا أو تحويل، وبيتجمع لوحده في قفل الشيفت.</p>
    <table><thead><tr><th>النوع</th><th>الاسم</th><th>شغال</th><th></th></tr></thead><tbody>
    ${list.map((c) => `<tr data-id="${esc(c.id)}"><td>${c.kind === "card" ? "مكنة فيزا" : "تحويل / محفظة"}</td><td><input name="name" value="${esc(c.name)}"></td>
      <td><input type="checkbox" name="is_active" ${c.is_active ? "checked" : ""}></td><td><button class="btn small secondary" data-chsave>احفظ</button></td></tr>`).join("")}</tbody></table>
    <form class="row mt" id="chNew"><label class="f">النوع <select name="kind"><option value="card">مكنة فيزا</option><option value="transfer">تحويل / محفظة</option></select></label>
      <label class="f">الاسم <input name="name" required placeholder="مثال: مكنة البنك الأهلي"></label><button class="btn small">أضف</button></form>`;
  document.querySelectorAll("[data-chsave]").forEach((b) => b.addEventListener("click", async () => {
    const tr = b.closest("tr");
    try { renderChannels((await api(`/api/payment-channels/${tr.dataset.id}`, { method: "PATCH", body: { name: tr.querySelector("[name=name]").value, is_active: tr.querySelector("[name=is_active]").checked } })).channels); toast("اتحفظ"); }
    catch (err) { toast(err.message, true); }
  }));
  $("#chNew").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { renderChannels((await api("/api/payment-channels", { method: "POST", body: { kind: e.target.kind.value, name: e.target.name.value } })).channels); }
    catch (err) { toast(err.message, true); }
  });
}
const MB = (b) => (b < 1048576 ? `${Math.max(1, Math.round(b / 1024))} ك.ب` : `${(b / 1048576).toFixed(1)} م.ب`);
const BK_INT = [[15, "ربع ساعة"], [30, "نص ساعة"], [60, "ساعة"], [360, "6 ساعات"]];
const BK_REASON = { schedule: "في ميعادها", manual: "يدوي", shift_close: "قفل الشيفت", shutdown: "قفل البرنامج" };
function renderBackup(b) {
  const c = b.settings, st = b.state, ago = st.last_at ? Math.round((Date.now() - new Date(st.last_at)) / 60000) : null;
  $("#bkBox").innerHTML = `
    ${st.last_ok === false ? `<p class="badge b-err">✕ آخر محاولة فشلت: ${esc(st.last_error || "")}</p>` : ""}
    ${st.last_at ? `<p class="badge b-ok">✓ آخر نسخة من ${ago < 60 ? `${ago} دقيقة` : `${Math.round(ago / 60)} ساعة`} — ${MB(st.last_bytes || 0)} — ${esc(BK_REASON[st.last_reason] || "")}</p>`
      : `<p class="badge b-warn">⚠ لسه معملتش ولا نسخة أوتوماتيك</p>`}
    <form id="bkForm" class="bk">
      <div><b>كل قد إيه</b><div class="seg" role="radiogroup">${BK_INT.map(([v, t]) => `<label><input type="radio" name="interval_min" value="${v}" ${c.interval_min === v ? "checked" : ""}> ${t}${v === 60 ? " (الافتراضي)" : ""}</label>`).join("")}</div>
        <label class="chk"><input type="checkbox" name="on_shift_close" ${c.on_shift_close ? "checked" : ""}> وكمان نسخة لما الشيفت يتقفل</label>
        <p class="muted">لو مفيش أي تغيير من آخر نسخة، مش بيعمل نسخة جديدة.</p></div>
      <div><b>نحتفظ بكام نسخة</b>
        <label class="f">آخر <input class="narrow" type="number" name="keep_last" min="1" max="100" value="${c.keep_last}"> نسخ — الأقدم بيتمسح لوحده</label>
        <label class="f">+ نسخة من كل يوم لآخر <input class="narrow" type="number" name="keep_daily" min="0" max="60" value="${c.keep_daily}"> يوم</label>
        <label class="f">+ نسخة من كل أسبوع لآخر <input class="narrow" type="number" name="keep_weekly" min="0" max="52" value="${c.keep_weekly}"> أسابيع</label>
        <p class="muted">اليومي والأسبوعي عشان لو غلطة اتكشفت بعد كام يوم، تلاقي نسخة قبلها.</p></div>
      <div><b>نسخة برّه الجهاز</b>
        <label class="f">فلاشة أو هارد خارجي أو فولدر Google Drive على الجهاز (المسار كامل)
          <input name="offsite_dir" value="${esc(c.offsite_dir)}" placeholder="E:\\نسخ الصيدلية" dir="auto"></label>
        ${!c.offsite_dir ? `<p class="badge b-warn">⚠ مفيش نسخة برّه الجهاز — لو الجهاز باظ النسخ هتضيع معاه</p>`
          : b.offsite_stale ? `<p class="badge b-warn">⚠ آخر نسخة برّه الجهاز ${st.offsite_at ? esc(st.offsite_at.slice(0, 10)) : "مفيش"}${st.offsite_error ? ` — ${esc(st.offsite_error)}` : ""}</p>`
          : `<p class="badge b-ok">✓ آخر نسخة برّه الجهاز ${esc((st.offsite_at || "").replace("T", " ").slice(0, 16))}</p>`}</div>
      <div class="row"><button class="btn">احفظ</button><button class="btn secondary" type="button" id="bkNow">اعمل نسخة دلوقتي</button></div>
    </form>
    <details class="mt"><summary>النسخ الموجودة (${b.files.length}) — المساحة ${MB(b.total_bytes)} — الفولدر ${esc(b.folder)}</summary>
      <table><tbody>${b.files.map((f) => `<tr><td dir="ltr">${esc(f.name)}</td><td>${MB(f.bytes)}</td><td>${f.auto ? "أوتوماتيك" : "يدوي / قبل تحديث"}</td></tr>`).join("")}</tbody></table></details>`;
  $("#bkForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      renderBackup(await api("/api/backup", { method: "PUT", body: {
        interval_min: +f.querySelector("[name=interval_min]:checked").value, on_shift_close: f.on_shift_close.checked,
        keep_last: +f.keep_last.value, keep_daily: +f.keep_daily.value, keep_weekly: +f.keep_weekly.value,
        offsite_dir: f.offsite_dir.value } }));
      toast("اتحفظ");
    } catch (err) { toast(err.message, true); }
  });
  $("#bkNow").addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "بيعمل نسخة…";
    try { renderBackup(await api("/api/backup/run", { method: "POST", body: {} })); toast("النسخة اتعملت واتراجعت"); }
    catch (err) { e.target.disabled = false; e.target.textContent = "اعمل نسخة دلوقتي"; toast(err.message, true); }
  });
}
async function checkUpdateBadge() {
  if (!ME?.permissions?.includes("settings.manage")) { $("#navSystem")?.setAttribute("hidden", ""); return; }
  try { const st = await api("/api/system/update"); $("#updBadge").hidden = !st.available; } catch { /* offline */ }
}

// ------------------------------------------------------------------ sales history / returns
const SALE_ST = { completed: "مكتملة", voided: "ملغاة" };
const localDay = (d = new Date()) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const hhmm = (iso) => (iso ? new Date(iso).toLocaleTimeString("ar-EG", { hour: "2-digit", minute: "2-digit" }) : "");

async function viewSales() {
  app.innerHTML = `
  <section class="panel"><h1>فواتير البيع</h1>
    <div class="row"><label class="f">اليوم<input type="date" id="sDay" value="${localDay()}"></label>
      <label class="f">أو رقم الفاتورة<input id="sNo" inputmode="numeric" placeholder="مثال: 125"></label>
      <button class="btn secondary" id="sGo">عرض</button></div></section>
  <section class="panel"><div class="tablewrap"><table><thead><tr><th>رقم</th><th>الوقت</th><th>العميل</th><th>الأصناف</th>
    <th>الإجمالي</th><th>نقدي/فيزا/آجل</th><th>مرتجع</th><th>الحالة</th></tr></thead><tbody id="sBody"></tbody></table></div></section>`;
  const load = async () => {
    const no = $("#sNo").value.trim();
    const rows = await api(`/api/sales?${no ? `no=${encodeURIComponent(no)}` : `day=${$("#sDay").value}`}`);
    $("#sBody").innerHTML = rows.map((r) => `<tr class="clickable" data-id="${r.id}"><td><b>${r.sale_no ?? ""}</b></td>
      <td>${hhmm(r.completed_at)}</td><td>${esc(r.customer || "—")}</td><td>${r.lines}</td><td><b>${money(r.total)}</b></td>
      <td>${money(r.paid_cash)} / ${money(r.paid_card)} / ${money(r.on_credit)}</td><td>${r.returned_total ? money(r.returned_total) : ""}</td>
      <td>${r.status === "voided" ? `<span class="badge b-err">ملغاة</span>` : SALE_ST[r.status]}</td></tr>`).join("")
      || `<tr><td colspan="8" class="muted">مفيش فواتير</td></tr>`;
    $("#sBody").querySelectorAll("tr.clickable").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/sales/${tr.dataset.id}`; }));
  };
  $("#sGo").addEventListener("click", load);
  $("#sDay").addEventListener("change", () => { $("#sNo").value = ""; load(); });
  load();
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
      <div>فيزا<b>${money(s.paid_card)}</b></div><div>آجل<b>${money(s.on_credit)}</b></div><div>مرتجع<b>${money(s.returned_total)}</b></div>
      <div>العميل<b>${esc(s.customer?.name || "—")}</b></div></div>
    ${s.void_reason ? `<p class="muted">سبب الإلغاء: ${esc(s.void_reason)}</p>` : ""}</section>
  <section class="panel"><h2>الأصناف</h2><div class="tablewrap"><table><thead><tr><th>الصنف</th><th>الكمية</th><th>الصافي</th>
    <th>التشغيلات</th>${s.status === "completed" && can("sales.return") ? "<th>كمية المرتجع (بالوحدة)</th>" : ""}</tr></thead><tbody>
    ${s.lines.map((l) => `<tr><td>${esc(l.item.name)}</td><td>${esc(fq(l.qty_base, l))}</td><td>${money(l.net)}</td>
      <td class="muted">${l.allocations.map((a) => `${esc(a.expiry_date ? a.expiry_date.slice(0, 7) : "—")}: ${a.qty}${a.returned_qty ? ` (رجع ${a.returned_qty})` : ""}`).join("، ")}</td>
      ${s.status === "completed" && can("sales.return") ? `<td>${returnable(l) ? `<input type="number" min="0" max="${returnable(l)}" step="1" value="0" data-ret="${l.id}" class="narrow">
        <span class="muted">من ${returnable(l)}</span>` : `<span class="muted">رجع كله</span>`}</td>` : ""}</tr>`).join("")}
    </tbody></table></div>
    ${s.status === "completed" && can("sales.return") ? `<div class="row mt"><label class="f">طريقة الاسترداد<select id="rMethod">
      <option value="cash">نقدي من الخزنة</option><option value="card">فيزا</option>${s.customer_id ? `<option value="credit">على حساب العميل</option>` : ""}</select></label>
      <label class="f grow">سبب المرتجع<input id="rReason" placeholder="مثال: العميل غيّر رأيه"></label>
      <button class="btn" id="rGo">تسجيل المرتجع</button></div>` : ""}
    ${s.status === "completed" && can("sales.void") && !s.returned_total ? `<div class="row mt"><label class="f grow">إلغاء الفاتورة (نفس اليوم بس) — السبب
      <input id="vReason"></label><button class="btn danger" id="vGo">إلغاء الفاتورة</button></div>` : ""}
  </section>`;
  $("#rGo")?.addEventListener("click", async () => {
    const lines = {};
    app.querySelectorAll("[data-ret]").forEach((i) => { const v = parseInt(i.value, 10); if (v > 0) lines[i.dataset.ret] = v; });
    if (!Object.keys(lines).length) { toast("اكتب كمية المرتجع", true); return; }
    try {
      const r = await api(`/api/sales/${id}/return`, { method: "POST", body: { lines, refund_method: $("#rMethod").value, reason: $("#rReason").value } });
      toast(`اتسجل المرتجع: ${money(r.refund)} ج`); viewSale(id);
    } catch (e) { toast(e.message, true); }
  });
  $("#vGo")?.addEventListener("click", async () => {
    if (!confirmInline($("#vGo"))) return;
    try { await api(`/api/sales/${id}/void`, { method: "POST", body: { reason: $("#vReason").value } }); toast("الفاتورة اتلغت"); viewSale(id); }
    catch (e) { toast(e.message, true); }
  });
}
// two-click confirmation without browser dialogs
function confirmInline(btn) {
  if (btn.dataset.armed) return true;
  btn.dataset.armed = "1"; const t = btn.textContent; btn.textContent = "متأكد؟ اضغط تاني";
  setTimeout(() => { delete btn.dataset.armed; btn.textContent = t; }, 4000);
  return false;
}

// ------------------------------------------------------------------ dead stock
async function viewDeadstock() {
  app.innerHTML = `
  <section class="panel">
    <h1>الرواكد</h1>
    <p class="muted">لحد ما الكاشير ينتقل للبرنامج، المبيعات على E-Stock. صدّر الـ 3 تقارير دول (Excel - Data Only) وارفعهم هنا. مفيش حاجة بتتحفظ.</p>
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
  const HINT = { dead: "ما اتباعش خلال الفترة، وآخر شراء قبل 6 شهور أو أقدم", stagnant: "ما اتباعش، واتشرى من 3 لـ 6 شهور",
    slow: "بيتباع، بس الرصيد يكفي أكتر من سنة", new: "اتشرى آخر 3 شهور ولسه بياخد فرصته" };
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#dsOut").innerHTML = `<section class="panel"><p class="muted">بيحسب…</p></section>`;
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
        <div>أخطاء مشتريات<b>${r.purchase_mistakes.value.toFixed(0)} ج</b><span class="muted">${r.purchase_mistakes.items} صنف اتشروا وما اتباعوش</span></div>
        <div>منتهي<b>${r.expiry.expired_value.toFixed(0)} ج</b><span class="muted">وخلال 6 شهور ${r.expiry.within_6_months_value.toFixed(0)} ج</span></div></div>
        <p class="muted">الموسمي (برد، حساسية، شمس) والأدوية المزمنة مش متشالين: راجعهم قبل البيع.</p></section>
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
    <p class="muted">الحساب: متوسط الاستهلاك × (أيام التوريد + أيام التغطية + أيام الأمان) − الرصيد − اللي في الطريق. كل سطر مكتوب معاه سببه. النظام مش بيطلب حاجة لوحده.</p>
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
    <p class="muted">المطابقة بالكود أو الباركود بس. الأصناف اللي مش متطابقة بتظهر في تقرير ومش بتتخمن.</p>
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
      || `<tr><td colspan="7" class="muted">لا توجد أصناف محتاجة طلب (أو لا توجد بيانات استهلاك بعد — ارفع مبيعات شهر من الأسفل)</td></tr>`;
  };
  $("#prForm").addEventListener("submit", (e) => { e.preventDefault(); load().catch((er) => toast(er.message, true)); });
  load().catch((er) => toast(er.message, true));
  api("/api/procurement/health").then((h) => {
    $("#prHealth").className = "";
    $("#prHealth").innerHTML = `
      <h3>قرب انتهاء الصلاحية (${h.near_expiry.length})</h3>
      <div class="tablewrap"><table><thead><tr><th>الصنف</th><th>التشغيلة</th><th>الصلاحية</th><th>الكمية</th><th>متوقع يتباع</th><th>معرض للانتهاء</th><th>الإجراء</th></tr></thead>
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
const METHODS = [["cash", "نقدي"], ["card", "فيزا"], ["transfer", "تحويل"], ["credit", "آجل"]];
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
  <nav class="tabs" aria-label="الفواتير المفتوحة">${tabs.map((d, i) => `<button class="tab ${d.id === SALE.id ? "on" : ""}" data-tab="${esc(d.id)}">
      فاتورة ${i + 1}${d.kind === "delivery" ? " · توصيل" : ""} · ${d.lines ? `${d.lines} صنف` : "فاضية"}
      <span class="x" data-close="${esc(d.id)}" title="اقفل الفاتورة دي" aria-label="اقفل">✕</span></button>`).join("")}
    <button class="tab add" id="tabNew" title="فاتورة جديدة (F2)">+ فاتورة <kbd>F2</kbd></button></nav>
  <div class="pos">
    <section class="pos-main">
      <label class="f">امسح الباركود أو الكود المربع، أو اكتب أول حروف الصنف
        <input id="posQ" class="big" autocomplete="off" autofocus></label>
      <div id="posRes" class="results"></div>
      <section class="panel" id="posCart"></section>
      <p class="keys"><kbd>Enter</kbd> يضيف علبة · <kbd>Shift+Enter</kbd> يضيف وحدة · <kbd>F9</kbd> دفع · <kbd>F2</kbd> فاتورة جديدة</p>
    </section>
    <aside class="pos-side" id="posSide"></aside>
  </div>
  <p class="muted mt">${ME.permissions.includes("sales.totals") ? `<a href="#" id="posSummary">ملخص اليوم</a> · ` : ""}<a href="#" id="posShift">الخزنة</a> · <a href="#" id="posDeliv">التوصيلات المفتوحة</a></p><div id="posSum"></div>`;
  renderCart(); renderSide();
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", async (e) => {
    if (e.target.dataset.close) return;
    await openDraft(b.dataset.tab); viewPos();
  }));
  document.querySelectorAll("[data-close]").forEach((x) => x.addEventListener("click", async (e) => {
    e.stopPropagation();
    const d = tabs.find((t) => t.id === x.dataset.close);
    if (d?.lines && !confirm("الفاتورة دي فيها أصناف. تقفلها من غير بيع؟")) return;
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
    if (res.length === 1 && scanned) { await addItem(res[0], unit ? 0 : 1, unit ? 1 : 0); return; }
    if (res.length === 1 && !found.hint) { await addItem(res[0], unit ? 0 : 1, unit ? 1 : 0); return; }
    $("#posRes").innerHTML = (found.hint?.kind === "gs1" && !res.length
      ? `<p class="badge b-warn">⚠ الكود المربع ده مش متسجل على أي صنف. افتح كارت الصنف وامسحه في خانة الباركود.</p>` : hintHtml(found.hint))
      + res.map((r, i) => `<div class="res ${r.stock_qty ? "" : "out"}"><div class="grow"><b>${itemHtml(r)}</b> ${markChip(r.mark)}
        <div class="muted">${esc(r.strength_norm || "")} · ${r.pack_size} ${esc(unitName(r))} في العلبة · ${r.location ? `${esc(r.location)} · ` : ""}رصيد: <b>${esc(fmtStock(r.stock_qty, r) || "0")}</b></div></div>
        <b class="price">${money(r.public_price)} ج</b>
        ${r.stock_qty ? `<button class="btn small" data-add="${i}" data-kind="pack">علبة${i === 0 ? " ↵" : ""}</button>
        ${r.pack_size > 1 ? `<button class="btn small secondary" data-add="${i}" data-kind="unit">${esc(unitName(r))}</button>` : ""}`
        : `<span class="badge b-err">✕ ناقص</span>`}</div>`).join("") || `<p class="muted">لا توجد نتائج</p>`;
    $("#posRes").querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () =>
      addItem(res[+b.dataset.add], b.dataset.kind === "pack" ? 1 : 0, b.dataset.kind === "unit" ? 1 : 0)));
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
  $("#posCart").innerHTML = `
    <div class="tablewrap"><table class="cart">
      <thead><tr><th>الصنف</th><th>الكمية</th><th>السعر</th><th>الصلاحية</th><th>خصم</th><th>الإجمالي</th><th><span class="sr">حذف</span></th></tr></thead>
      <tbody>${s.lines.map((l) => `<tr class="${l.problem ? "has-err" : ""} ${l.item.mark ? `m-${l.item.mark}` : ""}">
        <td><b>${itemHtml({ name_en: l.item.name_en, name_ar: l.item.name_ar })}</b> ${markChip(l.item.mark)}
          ${l.problem ? `<span class="badge b-err">✕ ${l.problem === "no_price" ? "بدون سعر" : "الكمية مش متاحة"}</span>` : ""}</td>
        <td><div class="qty"><button class="btn small secondary" data-minus="${esc(l.id)}" aria-label="نقص">−</button>
          <b>${qtyText(l.qty_base, l.item)}</b><button class="btn small secondary" data-plus="${esc(l.id)}" aria-label="زود">+</button></div></td>
        <td class="num">${l.allocations.map((a) => money(a.unit_price)).filter((v, i, arr) => arr.indexOf(v) === i).join(" / ")}</td>
        <td>${l.allocations.map((a) => `<span class="badge ${l.near_expiry ? "b-warn" : "b-ok"}">${l.near_expiry ? "⚠ " : ""}${esc((a.expiry_date || "—").slice(0, 7))}</span>`).join(" ")}</td>
        <td>${canDisc ? `<input class="narrow" type="number" min="0" max="100" step="0.5" value="${l.discount_bp ? pct(l.discount_bp) : ""}" data-disc="${esc(l.id)}" placeholder="%">`
          : l.discount_bp ? `${pct(l.discount_bp)}%` : `<button class="btn small secondary" data-askdisc="${esc(l.id)}">خصم</button>`}</td>
        <td class="num"><b>${money(l.net)}</b></td>
        <td><button class="btn small danger" data-rm="${esc(l.id)}" aria-label="شيل الصنف">✕</button></td></tr>`).join("")
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
        <button class="${s.kind === "delivery" ? "on" : ""}" data-kind="delivery">${s.kind === "delivery" ? "✓ " : ""}توصيل للبيت</button></div>
      <label class="f mt">العميل ${s.kind === "delivery" ? "" : "(اختياري)"}
        <input id="posCust" placeholder="اسم أو رقم موبايل" value="${s.customer ? esc(s.customer.name) : ""}" autocomplete="off"></label>
      <div id="posCustRes" class="cands"></div>
      ${s.customer ? `<p class="muted">رصيده ${money(s.customer.balance)} / حد الآجل ${money(s.customer.credit_limit)} <a href="#" id="custClear">شيل العميل</a></p>` : `<p><a href="#" id="posCustNew">+ عميل جديد</a></p>`}
      ${s.kind === "delivery" ? `<div class="deliv">
        <label class="f">العنوان <input data-dl="address" value="${esc(d.address || "")}"></label>
        <label class="f">الموبايل <input data-dl="phone" inputmode="tel" value="${esc(d.phone || "")}"></label>
        <label class="f">مين هيوصّل <input data-dl="courier" value="${esc(d.courier || "")}"></label>
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
          <button class="btn small secondary" data-prm="${i}" aria-label="شيل">✕</button></div>`).join("")}
        <button class="btn small secondary" id="partAdd">+ طريقة دفع</button>
        <p class="badge ${remaining === 0 ? "b-ok" : "b-warn"} mt">${remaining === 0 ? "✓ الدفع كامل" : `⚠ فاضل ${money(remaining)}`}</p>
        <p><a href="#" id="splitOff">رجوع لطريقة دفع واحدة</a></p>`
      : `<div class="paym">${METHODS.map(([m, t]) => `<button class="${POS.method === m ? "on" : ""}" data-m="${m}" ${m === "credit" && !s.customer ? "disabled title=\"اختار العميل الأول\"" : ""}>${t}</button>`).join("")}</div>
        ${["card", "transfer"].includes(POS.method) ? `<label class="f mt">${POS.method === "card" ? "المكنة" : "المحفظة / طريقة التحويل"} ${chanOptions(POS.method, POS.channel)}</label>` : ""}
        ${POS.method === "cash" ? `<label class="f mt">العميل دفع <input id="payGiven" type="number" step="0.01" min="0" class="big" placeholder="${money(s.total)}"></label>
          <div class="sum ok" id="changeBox" hidden><span>الباقي للعميل</span><b id="changeV"></b></div>` : ""}
        <p><a href="#" id="splitOn">تقسيم الدفع (جزء كاش وجزء فيزا أو تحويل)</a></p>`}
      <button class="btn big" id="posDone" ${s.lines.length ? "" : "disabled"}>إتمام البيع وطباعة <kbd>F9</kbd></button>
    </section>`;
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
    $("#posCustRes").innerHTML = cs.map((c) => `<button class="btn small secondary" data-cust="${esc(c.id)}">${esc(c.name)}${c.phone ? ` — ${esc(c.phone)}` : ""}</button>`).join("") || `<p class="muted">مفيش عميل بالاسم ده</p>`;
    $("#posCustRes").querySelectorAll("[data-cust]").forEach((b) => b.addEventListener("click", async () => {
      SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { customer_id: b.dataset.cust } });
      if (SALE.kind === "delivery" && SALE.customer?.phone && !SALE.delivery?.phone)
        SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { delivery: { phone: SALE.customer.phone } } });
      renderSide();
    }));
  });
  $("#custClear")?.addEventListener("click", async (e) => { e.preventDefault(); SALE = await api(`/api/sales/${SALE.id}`, { method: "PATCH", body: { customer_id: null } }); if (POS.method === "credit") POS.method = "cash"; renderSide(); });
  $("#posCustNew")?.addEventListener("click", async (e) => {
    e.preventDefault();
    const name = prompt("اسم العميل:"); if (!name) return;
    const phone = prompt("الموبايل (اختياري):") || null;
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
  if (t && SALE) t.firstChild.textContent = t.firstChild.textContent.replace(/ · توصيل/, "") .replace(/^(\s*فاتورة \d+)/, `$1${SALE.kind === "delivery" ? " · توصيل" : ""}`);
}

function renderFindings(findings) {
  const needs = (f) => ["override", "block", "acknowledge"].includes(f.requires);
  $("#posSafety").innerHTML = `<div class="panel mt"><h2>فحص السلامة الدوائية</h2>
    ${findings.map((f, i) => `<div class="mt"><span class="badge ${SEV[f.severity][1]}">${SEV[f.severity][0]}</span> ${esc(f.message)}
      ${f.source ? `<div class="muted">المرجع: ${esc(f.source.name)} ${esc(f.source.version)} ${esc(f.source.ref || "")}</div>` : ""}
      ${f.basis === "heuristic" ? `<div class="muted">تنبيه مبني على تشابه الأسماء</div>` : ""}
      ${needs(f) ? `<input class="mt" data-dec="${i}" placeholder="${f.requires === "acknowledge" ? "ملاحظة (اختياري) ثم تأكيد" : "اكتب سبب الاستمرار (يتسجل باسمك)"}">` : ""}</div>`).join("") || "<p>لا توجد تنبيهات</p>"}
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
    catch (pe) { window.open(`/receipt/${done.id}`, "_blank"); if (pe.code === "print_failed") toast(pe.message, true); }
    toast(`✓ تم البيع — فاتورة رقم ${done.sale_no}${done.kind === "delivery" ? " (توصيل: الفلوس مع اللي بيوصّل لحد ما يسلمها)" : ""}`);
    SALE = null; viewPos();
  } catch (er) {
    toast(er.message, true);
    btn.disabled = false;
    if (er.code === "safety_unresolved") renderCart(er.details.findings);
    if (er.code === "not_draft") { SALE = null; viewPos(); }
    if (er.code === "no_open_shift") $("#posShift").click();
    if (er.code === "below_cost_confirm") {
      const lines = (er.details?.lines || []).map((l) => `${l.name}: ${money(l.net)} والتكلفة ${money(l.cost)}`).join("\n");
      if (confirm(`⛔ البيع ده تحت التكلفة (بخسارة):\n${lines}\n\nمتأكد إنك عايز تكمل؟`)) { SALE.confirm_below_cost = true; return complete(findings); }
    }
  }
}

function bindPosExtras() {
  $("#posShift").addEventListener("click", (e) => { e.preventDefault(); location.hash = "#/shift"; });
  $("#posDeliv").addEventListener("click", async (e) => {
    e.preventDefault();
    const ds = (await api("/api/deliveries")).deliveries;
    const ST = { out: ["b-warn", "⚠ خرجت"], delivered: ["b-info", "ℹ اتسلمت — الفلوس لسه"] };
    $("#posSum").innerHTML = `<section class="panel"><h2>التوصيلات المفتوحة</h2><table><thead><tr><th>فاتورة</th><th>العميل / العنوان</th><th>مين بيوصّل</th><th>فلوس معاه</th><th>الحالة</th><th></th></tr></thead><tbody>
      ${ds.map((d) => `<tr><td><a href="#/sales/${esc(d.sale_id)}">${d.sale_no}</a></td><td>${esc(d.customer || "")} ${esc(d.address || "")} ${esc(d.phone || "")}</td>
        <td>${esc(d.courier || "—")}</td><td class="num">${money(d.cash_due)}</td><td><span class="badge ${ST[d.status][0]}">${ST[d.status][1]}</span></td>
        <td>${d.status === "out" ? `<button class="btn small secondary" data-dst="delivered" data-id="${esc(d.sale_id)}">اتسلمت</button>` : ""}
          <button class="btn small" data-dst="settled" data-id="${esc(d.sale_id)}">الفلوس وصلت الدرج</button></td></tr>`).join("") || `<tr><td colspan="6" class="muted">مفيش توصيلات مفتوحة</td></tr>`}</tbody></table></section>`;
    document.querySelectorAll("[data-dst]").forEach((b) => b.addEventListener("click", async () => {
      try { await api(`/api/sales/${b.dataset.id}/delivery`, { method: "POST", body: { status: b.dataset.dst } }); toast("اتسجل"); $("#posDeliv").click(); }
      catch (er) { toast(er.message, true); }
    }));
  });
  $("#posSummary")?.addEventListener("click", async (e) => {
    e.preventDefault();
    const s = await api("/api/sales/summary");
    $("#posSum").innerHTML = `<div class="totals"><div>فواتير<b>${s.n}</b></div><div>مبيعات<b>${money(s.total)}</b></div>
      <div>نقدي<b>${money(s.cash)}</b></div><div>فيزا<b>${money(s.card)}</b></div><div>تحويل<b>${money(s.transfer || 0)}</b></div><div>آجل<b>${money(s.credit)}</b></div>
      <div>مرتجع<b>${money(s.returns)}</b></div>${s.gross_profit_egp != null ? `<div>مجمل الربح<b>${esc(s.gross_profit_egp)}</b></div>` : ""}</div>`;
  });
}

// ------------------------------------------------------------------ shift: open, count the drawer, close, report
const DENOMS = [20000, 10000, 5000, 2000, 1000, 500, 100, 50, 25];
const denomLabel = (d) => (d >= 100 ? `${d / 100} جنيه` : d === 50 ? "نص جنيه" : "ربع جنيه");
async function viewShift() {
  let sh = null;
  try { sh = await api("/api/cash/current"); } catch { /* none open */ }
  const seeAll = ME.permissions.includes("cash.see_expected");
  const recent = seeAll ? (await api("/api/cash/shifts")).shifts : [];
  const hist = recent.length ? `<section class="panel"><h2>آخر الشيفتات</h2><table><thead><tr><th>من</th><th>لحد</th><th>قفله</th><th>المفروض</th><th>المعدود</th><th>النتيجة</th><th></th></tr></thead><tbody>
    ${recent.map((r) => `<tr><td>${esc(new Date(r.opened_at).toLocaleString("ar-EG"))}</td><td>${r.closed_at ? esc(new Date(r.closed_at).toLocaleString("ar-EG")) : `<span class="badge b-info">مفتوح</span>`}</td>
      <td>${esc(r.closed_by_name || "")}</td><td class="num">${r.status === "closed" ? money(r.expected_cash) : ""}</td><td class="num">${r.status === "closed" ? money(r.counted_cash) : ""}</td>
      <td>${r.status !== "closed" ? "" : !r.difference ? `<span class="badge b-ok">✓ مظبوط</span>` : r.difference < 0 ? `<span class="badge b-err">✕ عجز ${money(-r.difference)}</span>` : `<span class="badge b-warn">⚠ زيادة ${money(r.difference)}</span>`}</td>
      <td>${r.status === "closed" ? `<a href="/shift-report/${esc(r.id)}" target="_blank">التقرير</a>` : ""}</td></tr>`).join("")}</tbody></table></section>` : "";
  if (!sh) {
    app.innerHTML = `<section class="panel narrowpanel"><h1>الشيفت</h1><p class="badge b-warn">⚠ مفيش شيفت مفتوح — البيع النقدي مقفول</p>
      <form id="shOpen" class="row mt"><label class="f">الفلوس اللي في الدرج دلوقتي (ج) <input name="v" type="number" step="0.25" min="0" value="0" class="big"></label>
      <button class="btn">افتح الشيفت</button></form></section>${hist}`;
    $("#shOpen").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api("/api/cash/open", { method: "POST", body: { opening_cash: toPiasters(e.target.v.value) || 0 } }); toast("✓ الشيفت اتفتح"); viewShift(); }
      catch (err) { toast(err.message, true); }
    });
    return;
  }
  const k = sh.cash_by_kind || {};
  app.innerHTML = `<section class="panel"><div class="row between"><h1>الشيفت المفتوح</h1><span class="muted">من ${esc(new Date(sh.opened_at).toLocaleString("ar-EG"))}</span></div>
    ${sh.blind ? `<p class="badge b-info">ℹ عدّ الفلوس اللي في الدرج فئة فئة واكتب العدد. النتيجة بتظهر بعد ما تقفل.</p>`
    : `<div class="totals"><div>البداية<b>${money(sh.opening_cash)}</b></div><div>مبيعات نقدي<b>${money(k.sale || 0)}</b></div>
      <div>مرتجع وإلغاء<b>${money((k.refund || 0) + (k.void || 0))}</b></div><div>تحصيل آجل<b>${money(k.customer_payment || 0)}</b></div>
      <div>مصروفات<b>${money(k.payout || 0)}</b></div><div>المفروض في الدرج<b>${money(sh.expected_cash)}</b></div>
      ${(sh.by_channel || []).map((c) => `<div>${c.method === "card" ? "فيزا" : "تحويل"} — ${esc(c.channel)}<b>${money(c.amount)}</b></div>`).join("")}
      ${sh.with_courier ? `<div>مع الدليفري<b>${money(sh.with_courier)}</b><span class="badge b-warn">⚠ لسه ماتسلمتش</span></div>` : ""}</div>`}
    ${ME.permissions.includes("cash.manage") ? `<form id="shPay" class="row mt"><label class="f">مصروف من الدرج (ج) <input name="a" type="number" step="0.25" min="0"></label>
      <label class="f grow">السبب <input name="n"></label><button class="btn secondary small">سجّل</button></form>` : ""}
  </section>
  ${ME.permissions.includes("cash.close") ? `<form class="panel" id="shClose"><h2>قفل الشيفت — عدّ الدرج</h2>
    <table class="denoms"><thead><tr><th>الفئة</th><th>العدد</th><th>المبلغ</th></tr></thead><tbody>
    ${DENOMS.map((d) => `<tr><td>${denomLabel(d)}</td><td><input type="number" min="0" step="1" inputmode="numeric" data-d="${d}" class="narrow"></td><td class="num" id="dt${d}">0.00</td></tr>`).join("")}</tbody>
    <tfoot><tr><th>الإجمالي المعدود</th><th></th><th class="num big" id="dTotal">0.00</th></tr></tfoot></table>
    <label class="f mt">ملاحظة (اختياري) <input name="note"></label>
    <button class="btn big">قفل الشيفت وطباعة التقرير</button></form>` : ""}
  ${hist}`;
  const recalc = () => {
    let t = 0;
    document.querySelectorAll("[data-d]").forEach((inp) => { const v = (+inp.value || 0) * +inp.dataset.d; t += v; $(`#dt${inp.dataset.d}`).textContent = money(v); });
    $("#dTotal").textContent = money(t);
  };
  document.querySelectorAll("[data-d]").forEach((inp) => inp.addEventListener("input", recalc));
  $("#shPay")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/cash/movements", { method: "POST", body: { kind: "payout", amount: toPiasters(e.target.a.value), note: e.target.n.value } }); toast("اتسجل"); viewShift(); }
    catch (err) { toast(err.message, true); }
  });
  $("#shClose")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const den = {};
    document.querySelectorAll("[data-d]").forEach((inp) => { if (+inp.value) den[inp.dataset.d] = +inp.value; });
    if (!Object.keys(den).length && !confirm("مكتبتش أي فئة — يعني الدرج فاضي. أكمل؟")) return;
    try {
      const r = await api("/api/cash/close", { method: "POST", body: { denominations: den, note: e.target.note.value || null } });
      const d = r.difference;
      app.innerHTML = `<section class="panel narrowpanel"><h1>الشيفت اتقفل</h1>
        <p class="result ${!d ? "b-ok" : d < 0 ? "b-err" : "b-warn"}">${!d ? "✓ الدرج مظبوط" : d < 0 ? `✕ عجز ${money(-d)} ج` : `⚠ زيادة ${money(d)} ج`}</p>
        <div class="totals"><div>المفروض<b>${money(r.expected_cash)}</b></div><div>المعدود<b>${money(r.counted_cash)}</b></div></div>
        <div class="row mt"><a class="btn" href="/shift-report/${esc(r.id)}" target="_blank">اطبع التقرير</a><a class="btn secondary" href="#/shift">افتح شيفت جديد</a></div></section>`;
      window.open(`/shift-report/${r.id}`, "_blank");
    } catch (err) {
      toast(err.code === "difference_note_required" ? `${err.message} (الفرق ${money(err.details.difference)})` : err.message, true);
      if (err.code === "difference_note_required") e.target.note.focus();
    }
  });
}

// ------------------------------------------------------------------ suppliers and their accounts
const LEDGER_KIND = { opening: "رصيد أول المدة", purchase: "فاتورة شراء", payment: "دفعة", return: "مرتجع", adjustment: "تسوية / إلغاء" };
async function viewSuppliers() {
  const list = (await api("/api/suppliers/all")).suppliers, showBal = list.length && "balance" in list[0];
  app.innerHTML = `<section class="panel"><div class="row between"><h1>الموردين</h1>
    ${ME.permissions.includes("suppliers.write") ? `<form id="supNew" class="row"><input name="name" placeholder="اسم المورد" required>
      <select name="t"><option value="company">شركة</option><option value="warehouse" selected>مخزن</option></select><button class="btn small">أضف مورد</button></form>` : ""}</div>
    <table><thead><tr><th>المورد</th><th>النوع</th><th>آخر فاتورة</th>${showBal ? "<th>علينا له</th>" : ""}</tr></thead><tbody>
    ${list.map((x) => `<tr class="clickable" data-go="${esc(x.id)}"><td><b>${esc(x.name)}</b>${x.is_active ? "" : ` <span class="badge b-plain">موقوف</span>`}</td>
      <td>${SUP_TYPE[x.supplier_type] ? `<span class="chip">${SUP_TYPE[x.supplier_type]}</span>` : `<span class="badge b-warn">⚠ حدد النوع</span>`}</td>
      <td>${esc(x.last_invoice || "—")}</td>${showBal ? `<td class="num">${x.balance > 0 ? `<b>${money(x.balance)}</b>` : x.balance < 0 ? `<span class="ok">ليه عندنا ${money(-x.balance)}</span>` : "—"}</td>` : ""}</tr>`).join("")
      || `<tr><td colspan="4" class="muted">مفيش موردين</td></tr>`}</tbody></table>
    <p class="muted">نوع المورد (شركة أو مخزن) بيفرق في الضرايب، وبيتحفظ مع كل فاتورة شراء.</p></section>`;
  document.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/suppliers/${tr.dataset.go}`; }));
  $("#supNew")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/suppliers", { method: "POST", body: { name: e.target.name.value, supplier_type: e.target.t.value } }); viewSuppliers(); }
    catch (err) { toast(err.message, true); }
  });
}

async function viewSupplier(id) {
  const a = await api(`/api/suppliers/${id}`), canW = ME.permissions.includes("suppliers.write"), canPay = ME.permissions.includes("suppliers.pay");
  app.innerHTML = `<section class="panel"><a href="#/suppliers">← الموردين</a>
    <div class="row between"><h1>${esc(a.name)}</h1>
      <span class="seg2 small" id="supType">${Object.entries(SUP_TYPE).map(([v, t]) => `<button type="button" class="${a.supplier_type === v ? "on" : ""}" data-st="${v}" ${canW ? "" : "disabled"}>${a.supplier_type === v ? "✓ " : ""}${t}</button>`).join("")}</span></div>
    <div class="row"><label class="f">التليفون <input id="supPhone" value="${esc(a.phone || "")}" ${canW ? "" : "disabled"}></label>
      <label class="f grow">شروط الدفع <input id="supTerms" value="${esc(a.payment_terms || "")}" placeholder="مثال: آجل 30 يوم" ${canW ? "" : "disabled"}></label>
      ${canW ? `<button class="btn small secondary" id="supSave">احفظ</button>` : ""}</div></section>
  ${a.balance != null ? `<div class="totals"><div class="${a.balance > 0 ? "t-warn" : "t-ok"}">${a.balance > 0 ? "علينا له" : a.balance < 0 ? "ليه عندنا" : "✓ مفيش حساب"}<b>${money(Math.abs(a.balance))} ج</b></div></div>` : ""}
  ${canPay ? `<form class="panel row" id="supPay"><h2 class="grow">تسجيل دفعة للمورد</h2>
    <label class="f">المبلغ (ج) <input name="a" type="number" step="0.01" min="0" required></label>
    <label class="f">منين <select name="m"><option value="drawer">من درج الشيفت</option><option value="outside">من برّه الدرج (بنك / من جيبي)</option></select></label>
    <label class="f grow">ملاحظة <input name="n"></label><button class="btn">سجّل الدفعة</button></form>` : ""}
  <div class="grid2">
    <section class="panel"><h2>الفواتير</h2><table><thead><tr><th>الرقم</th><th>التاريخ</th><th>الحالة</th><th>الإجمالي</th></tr></thead><tbody>
      ${a.invoices.map((i) => `<tr class="clickable" data-inv="${esc(i.id)}"><td>${esc(i.invoice_no || "—")}</td><td>${esc(i.invoice_date || "")}</td>
        <td><span class="badge ${STATUS[i.status][1]}">${STATUS[i.status][0]}</span></td><td class="num">${money(i.computed_total ?? i.declared_total)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">مفيش فواتير</td></tr>`}</tbody></table></section>
    ${a.ledger ? `<section class="panel"><h2>الحساب</h2><table><thead><tr><th>التاريخ</th><th>البيان</th><th>المبلغ</th><th>مين</th></tr></thead><tbody>
      ${a.ledger.map((l) => `<tr><td>${esc(l.created_at.slice(0, 10))}</td><td>${esc(LEDGER_KIND[l.kind] || l.kind)}${l.note ? ` — ${esc(l.note)}` : ""}</td>
        <td class="num ${l.amount < 0 ? "ok" : ""}">${l.amount < 0 ? "− " : "+ "}${money(Math.abs(l.amount))}</td><td>${esc(l.by_name || "")}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">مفيش حركات</td></tr>`}</tbody></table></section>` : ""}
  </div>`;
  document.querySelectorAll("[data-inv]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/purchases/${tr.dataset.inv}`; }));
  document.querySelectorAll("[data-st]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/suppliers/${id}`, { method: "PATCH", body: { supplier_type: b.dataset.st } }); viewSupplier(id); } catch (err) { toast(err.message, true); }
  }));
  $("#supSave")?.addEventListener("click", async () => {
    try { await api(`/api/suppliers/${id}`, { method: "PATCH", body: { phone: $("#supPhone").value, payment_terms: $("#supTerms").value } }); toast("اتحفظ"); } catch (err) { toast(err.message, true); }
  });
  $("#supPay")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api(`/api/suppliers/${id}/pay`, { method: "POST", body: { amount: toPiasters(e.target.a.value), method: e.target.m.value, note: e.target.n.value || null } }); toast("✓ الدفعة اتسجلت"); viewSupplier(id); }
    catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ shortages (النواقص)
const SH_REASON = { marked: ["b-warn", "⚠ متعلّم ناقص"], below_min: ["b-warn", "⚠ تحت حد الطلب"], sold_out: ["b-err", "✕ خلص وبيتباع"] };
async function viewShortages() {
  const items = (await api("/api/shortages")).items;
  const it = (r) => ({ pack_size: r.pack_size, base_unit_name: r.base_unit_name });
  app.innerHTML = `<section class="panel"><div class="row between"><h1>النواقص (${items.length})</h1><button class="btn secondary" id="shPrint">اطبع القايمة</button></div>
    <p class="muted">أصناف حد علّمها ناقص، أو وصلت حد الطلب، أو خلصت وكانت بتتباع آخر 60 يوم. العلامة بتتشال لوحدها لما فاتورة شراء الصنف تتعتمد.</p>
    <div class="tablewrap"><table><thead><tr><th>الصنف</th><th>السبب</th><th>الرصيد</th><th>حد الطلب</th><th>اتباع 60 يوم</th><th>المقترح</th><th>آخر مورد</th></tr></thead><tbody>
    ${items.map((r) => `<tr class="clickable" data-go="${esc(r.id)}"><td>${itemHtml(r)}</td><td>${r.reasons.map((x) => `<span class="badge ${SH_REASON[x][0]}">${SH_REASON[x][1]}</span>`).join(" ")}</td>
      <td>${esc(fmtStock(r.stock, it(r)) || "0")}</td><td>${r.min_stock ? esc(fmtStock(r.min_stock, it(r))) : "—"}</td><td>${esc(fmtStock(r.sold_60d, it(r)) || "0")}</td>
      <td><b>${r.suggest_base ? esc(fmtStock(r.suggest_base, it(r))) : "—"}</b></td><td>${esc(r.last_supplier || "—")}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">✓ مفيش نواقص</td></tr>`}</tbody></table></div></section>`;
  document.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/items/${tr.dataset.go}`; }));
  $("#shPrint").addEventListener("click", () => window.print());
}

// ------------------------------------------------------------------ customers
const CL_KIND = { credit_sale: "فاتورة آجل", payment: "دفعة", return: "مرتجع", void: "إلغاء", opening: "رصيد أول المدة", adjustment: "تسوية" };
async function viewCustomers(filter = "") {
  const canM = ME.permissions.includes("customers.manage");
  app.innerHTML = `<section class="panel"><div class="row between"><h1>العملاء</h1></div>
    <div class="row"><input id="cuQ" class="grow" placeholder="اسم أو رقم موبايل" autocomplete="off">
      <div class="seg2 small" id="cuF"><button data-f="" class="${!filter ? "on" : ""}">الكل</button><button data-f="debt" class="${filter === "debt" ? "on" : ""}">عليهم فلوس</button><button data-f="refill" class="${filter === "refill" ? "on" : ""}">طلبهم الشهري قرّب</button></div></div>
    <table class="mt"><thead><tr><th>العميل</th><th>الموبايل</th><th>عليه</th><th>الطلب الشهري</th></tr></thead><tbody id="cuBody"></tbody></table></section>
  ${canM ? `<form class="panel row" id="cuNew"><h2 class="grow">عميل جديد</h2><label class="f">الاسم <input name="name" required></label>
    <label class="f">الموبايل <input name="phone" inputmode="tel"></label><label class="f">حد الآجل (ج) <input name="lim" type="number" min="0" value="0" class="narrow"></label>
    <button class="btn">أضف</button></form>` : ""}`;
  const today = new Date().toISOString().slice(0, 10);
  const load = async () => {
    const f = $("#cuF .on").dataset.f;
    const list = (await api(`/api/customers/all?q=${encodeURIComponent($("#cuQ").value)}${f ? `&only=${f}` : ""}`)).customers;
    $("#cuBody").innerHTML = list.map((c) => `<tr class="clickable" data-go="${esc(c.id)}"><td><b>${esc(c.name)}</b></td><td dir="ltr">${esc(c.phone || "")}</td>
      <td>${c.balance > 0 ? `<span class="badge b-err">✕ ${money(c.balance)}</span>` : `<span class="ok">✓ مفيش</span>`}</td>
      <td>${c.next_refill ? `<span class="badge ${c.next_refill <= today ? "b-err" : "b-warn"}">${c.next_refill <= today ? "✕ ميعاده عدى" : "⚠"} ${esc(c.next_refill)}</span>` : "—"}</td></tr>`).join("")
      || `<tr><td colspan="4" class="muted">مفيش عملاء</td></tr>`;
    document.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/customers/${tr.dataset.go}`; }));
  };
  let t; $("#cuQ").addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 250); });
  document.querySelectorAll("#cuF [data-f]").forEach((b) => b.addEventListener("click", () => { document.querySelectorAll("#cuF button").forEach((x) => x.classList.toggle("on", x === b)); load(); }));
  $("#cuNew")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const r = await api("/api/customers", { method: "POST", body: { name: e.target.name.value, phone: e.target.phone.value || null, credit_limit: toPiasters(e.target.lim.value) || 0 } }); location.hash = `#/customers/${r.id}`; }
    catch (err) { toast(err.message, true); }
  });
  load();
}

async function viewCustomer(id) {
  const c = await api(`/api/customers/${id}`), canM = ME.permissions.includes("customers.manage");
  const it = (r) => ({ pack_size: r.pack_size, base_unit_name: r.base_unit_name });
  app.innerHTML = `<section class="panel"><a href="#/customers">← العملاء</a><div class="row between"><h1>${esc(c.name)}</h1>
      ${ME.permissions.includes("sales.create") ? `<button class="btn" id="cuSale">فاتورة له</button>` : ""}</div>
    <div class="formgrid"><label class="f">الاسم <input id="cName" value="${esc(c.name)}" ${canM ? "" : "disabled"}></label>
      <label class="f">الموبايل <input id="cPhone" value="${esc(c.phone || "")}" inputmode="tel" ${canM ? "" : "disabled"}></label>
      <label class="f">حد الآجل (ج) <input id="cLim" type="number" min="0" value="${money(c.credit_limit)}" ${canM ? "" : "disabled"}></label>
      <label class="f">ملاحظات <input id="cNotes" value="${esc(c.notes || "")}" ${canM ? "" : "disabled"}></label></div>
    <label class="chk mt"><input type="checkbox" id="cWa" ${c.whatsapp_ok ? "checked" : ""} ${canM ? "" : "disabled"}> موافق إننا نبعتله رسايل واتساب (تذكير الطلب الشهري)</label>
    ${canM ? `<button class="btn small secondary mt" id="cSave">احفظ</button>` : ""}</section>
  <div class="totals"><div class="${c.balance > 0 ? "t-err" : "t-ok"}">${c.balance > 0 ? "✕ عليه" : "✓ مفيش عليه"}<b>${money(Math.max(0, c.balance))} ج</b><span>الحد المسموح ${money(c.credit_limit)} ج</span></div>
    <div class="${c.whatsapp_ok ? "t-ok" : ""}">${c.whatsapp_ok ? "✓ موافق على واتساب" : "مش موافق على رسايل"}<b>${esc(c.phone || "—")}</b></div></div>
  ${canM && c.balance > 0 ? `<form class="panel row" id="cPay"><h2 class="grow">تحصيل من الحساب</h2><label class="f">المبلغ (ج) <input name="a" type="number" step="0.01" min="0" required></label>
    <label class="f">منين <select name="m"><option value="cash">نقدي في الدرج</option><option value="card">فيزا</option></select></label><button class="btn">سجّل</button></form>` : ""}
  <div class="grid2">
    <section class="panel"><div class="row between"><h2>الطلب الشهري</h2>${c.refills.length && ME.permissions.includes("sales.create") ? `<button class="btn small" id="cPrep">جهّز الطلب في الكاشير</button>` : ""}</div>
      <table><thead><tr><th>الصنف</th><th>الكمية</th><th>كل</th><th>ميعاده</th><th>عندنا؟</th><th></th></tr></thead><tbody>
      ${c.refills.map((r) => `<tr><td>${itemHtml(r)}</td><td>${esc(fmtStock(r.qty_base, it(r)))}</td><td>${r.every_days} يوم</td>
        <td><span class="badge ${r.days_left < 0 ? "b-err" : r.days_left <= 5 ? "b-warn" : "b-ok"}">${r.days_left < 0 ? `✕ متأخر ${-r.days_left} يوم` : r.days_left <= 5 ? `⚠ بعد ${r.days_left} يوم` : `✓ ${esc(r.due)}`}</span></td>
        <td>${r.in_stock ? `<span class="ok">✓ موجود</span>` : `<span class="badge b-warn">⚠ مش كفاية — اطلب</span>`}</td>
        <td>${canM ? `<button class="btn small secondary" data-rrm="${esc(r.item_id)}">شيل</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">مفيش طلب شهري</td></tr>`}</tbody></table>
      ${c.suggest_refills.length && canM ? `<p class="badge b-info mt">ℹ اشترى الأصناف دي في 3 شهور أو أكتر — غالبًا علاج شهري:</p>
        <ul class="plain">${c.suggest_refills.map((r) => `<li>${itemHtml(r)} (${r.months} شهور) <button class="btn small secondary" data-radd="${esc(r.item_id)}" data-rq="${r.qty_base}">ضيفه للطلب الشهري</button></li>`).join("")}</ul>` : ""}
      ${canM ? `<form class="row mt" id="rAdd"><input id="rQ" class="grow" placeholder="ضيف صنف للطلب الشهري (اسم أو باركود)" autocomplete="off">
        <input id="rQty" type="number" min="1" value="1" class="narrow" title="الكمية بالعلبة"><select id="rEvery"><option value="30">كل شهر</option><option value="15">كل 15 يوم</option><option value="60">كل شهرين</option></select>
        <button class="btn small">أضف</button></form><div id="rRes" class="cands"></div>` : ""}
      <p class="muted">البرنامج بيقترح، وانت اللي بتأكد. مفيش رسالة بتطلع لوحدها.</p></section>
    <section class="panel"><h2>الحساب</h2><table><thead><tr><th>التاريخ</th><th>البيان</th><th>المبلغ</th></tr></thead><tbody>
      ${c.ledger.map((l) => `<tr><td>${esc(l.created_at.slice(0, 10))}</td><td>${esc(CL_KIND[l.kind] || l.kind)}${l.note ? ` — ${esc(l.note)}` : ""}</td>
        <td class="num ${l.amount < 0 ? "ok" : ""}">${l.amount < 0 ? "− " : "+ "}${money(Math.abs(l.amount))}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">مفيش حركات</td></tr>`}</tbody></table>
      <h2 class="mt">آخر الفواتير</h2><ul class="plain">${c.sales.map((x) => `<li><a href="#/sales/${esc(x.id)}">فاتورة ${x.sale_no}</a> — ${money(x.total)} ج — ${esc((x.completed_at || "").slice(0, 10))}</li>`).join("") || `<li class="muted">مفيش</li>`}</ul></section>
  </div>`;
  $("#cSave")?.addEventListener("click", async () => {
    try { await api(`/api/customers/${id}`, { method: "PATCH", body: { name: $("#cName").value, phone: $("#cPhone").value || null, notes: $("#cNotes").value || null, credit_limit: toPiasters($("#cLim").value) || 0, whatsapp_ok: $("#cWa").checked } }); toast("اتحفظ"); viewCustomer(id); }
    catch (err) { toast(err.message, true); }
  });
  $("#cPay")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api(`/api/customers/${id}/payments`, { method: "POST", body: { amount: toPiasters(e.target.a.value), method: e.target.m.value } }); toast("✓ اتسجل"); viewCustomer(id); }
    catch (err) { toast(err.message, true); }
  });
  const toPos = async (sid) => { SALE = await api(`/api/sales/${sid}`); location.hash = "#/pos"; };
  $("#cuSale")?.addEventListener("click", async () => {
    try { const d = await api("/api/sales", { method: "POST", body: { customer_id: id } }); await toPos(d.id); } catch (err) { toast(err.message, true); }
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
    if (!picked) return toast("اختار الصنف من القايمة", true);
    try { await api(`/api/customers/${id}/refills`, { method: "POST", body: { item_id: picked.id, qty_base: (+$("#rQty").value || 1) * picked.pack_size, every_days: +$("#rEvery").value } }); viewCustomer(id); }
    catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ expenses
const EX_ST = { pending: ["b-warn", "⚠ مستني موافقة"], approved: ["b-ok", "✓ معتمد"], rejected: ["b-err", "✕ مرفوض"] };
async function viewExpenses() {
  const month = new Date().toISOString().slice(0, 7);
  const r = await api(`/api/expenses?month=${month}`), canA = ME.permissions.includes("expenses.approve");
  const pending = r.expenses.filter((e) => e.status === "pending");
  app.innerHTML = `<form class="panel" id="exNew"><h1>تسجيل مصروف</h1>
    <div class="formgrid"><label class="f">البند <select name="cat">${r.categories.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select></label>
      <label class="f">المبلغ (ج) <input name="a" type="number" step="0.25" min="0" required></label>
      <label class="f">الفلوس طلعت منين <select name="from"><option value="drawer">من درج الشيفت</option><option value="outside">من برّه الدرج</option></select></label>
      <label class="f">التاريخ <input name="d" type="date" value="${new Date().toISOString().slice(0, 10)}"></label>
      <label class="f">ملاحظة <input name="n" placeholder="مثال: أكياس + بكرة إيصالات"></label></div>
    <button class="btn mt">سجّل</button>
    <p class="muted">${ME.role === "owner" ? "مصاريفك بتتعتمد على طول." : `المصروف بيستنى موافقة ${canA ? "الصاحب لو أكبر من " + money(r.manager_limit) + " ج" : "المدير أو الصاحب"} قبل ما يتحسب.`}</p></form>
  ${canA && pending.length ? `<section class="panel"><h2>⚠ مستني موافقتك (${pending.length})</h2>${exTable(pending, true)}</section>` : ""}
  <section class="panel"><div class="row between"><h2>مصاريف الشهر ده</h2>${ME.permissions.includes("costs.read") ? `<a href="#/profit">تقرير الربح ←</a>` : ""}</div>${exTable(r.expenses, false)}
    ${canA ? `<form class="row mt" id="exCat"><input name="n" placeholder="بند جديد" required><button class="btn small secondary">أضف بند</button></form>` : ""}</section>`;
  $("#exNew").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try { const x = await api("/api/expenses", { method: "POST", body: { category_id: f.cat.value, amount: toPiasters(f.a.value), paid_from: f.from.value, spent_on: f.d.value, note: f.n.value || null } });
      toast(x.status === "approved" ? "✓ اتسجل واتعتمد" : "اتسجل — مستني الموافقة"); viewExpenses(); }
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
function exTable(list, actions) {
  return `<table><thead><tr><th>التاريخ</th><th>البند</th><th>المبلغ</th><th>منين</th><th>سجّله</th><th>الحالة</th>${actions ? "<th></th>" : ""}</tr></thead><tbody>
    ${list.map((e) => `<tr><td>${esc(e.spent_on)}</td><td>${esc(e.category)}${e.note ? `<div class="muted">${esc(e.note)}</div>` : ""}</td><td class="num">${money(e.amount)}</td>
      <td>${e.paid_from === "drawer" ? "الدرج" : "برّه"}</td><td>${esc(e.by_name || "")}</td>
      <td><span class="badge ${EX_ST[e.status][0]}">${EX_ST[e.status][1]}</span>${e.reject_reason ? `<div class="muted">${esc(e.reject_reason)}</div>` : ""}</td>
      ${actions ? `<td><button class="btn small" data-exok="${esc(e.id)}">اعتمد</button> <button class="btn small secondary" data-exno="${esc(e.id)}">ارفض</button></td>` : ""}</tr>`).join("")
      || `<tr><td colspan="7" class="muted">مفيش مصاريف</td></tr>`}</tbody></table>`;
}

// ------------------------------------------------------------------ profit (owner)
async function viewProfit() {
  const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const now = new Date(), first = new Date(now.getFullYear(), now.getMonth(), 1);
  const lastFirst = new Date(now.getFullYear(), now.getMonth() - 1, 1), lastEnd = new Date(now.getFullYear(), now.getMonth(), 0);
  const ranges = { today: [now, now], month: [first, now], last: [lastFirst, lastEnd] };
  app.innerHTML = `<section class="panel"><h1>الربح</h1><div class="row">
    <div class="seg2 small" id="prR"><button data-r="today">النهارده</button><button data-r="month" class="on">الشهر ده</button><button data-r="last">الشهر اللي فات</button></div>
    <label class="f">من <input type="date" id="prF"></label><label class="f">لحد <input type="date" id="prT"></label><button class="btn small secondary" id="prGo">اعرض</button></div></section>
    <div id="prBox"></div>`;
  const show = async (f, t) => {
    $("#prF").value = f; $("#prT").value = t;
    try {
      const r = await api(`/api/reports/profit?from=${f}&to=${t}`);
      $("#prBox").innerHTML = `<div class="totals">
        <div>المبيعات (بعد المرتجع)<b>${money(r.revenue)}</b><span>${r.invoices} فاتورة · خصومات ${money(r.discount)}</span></div>
        <div>تكلفة البضاعة اللي اتباعت<b>${money(r.cost)}</b></div>
        <div class="${r.gross_profit >= 0 ? "t-ok" : "t-err"}">مجمل الربح<b>${money(r.gross_profit)}</b>${r.gross_margin_pct != null ? `<span>${r.gross_margin_pct}%</span>` : ""}</div>
        <div>المصاريف المعتمدة<b>${money(r.expenses_total)}</b></div>
        <div class="${r.net_profit >= 0 ? "t-ok" : "t-err"}">${r.net_profit >= 0 ? "✓ صافي الربح" : "✕ صافي خسارة"}<b>${money(Math.abs(r.net_profit))}</b></div></div>
        ${r.pending_expenses.n ? `<p class="badge b-warn mt">⚠ فيه ${r.pending_expenses.n} مصروف (${money(r.pending_expenses.a)} ج) مستني موافقة ومش محسوب — <a href="#/expenses">راجعهم</a></p>` : ""}
        ${r.unknown_cost_lines ? `<p class="badge b-warn mt">⚠ ${r.unknown_cost_lines} سطر بيع من غير تكلفة مسجلة — الربح الحقيقي ممكن يبقى أقل</p>` : ""}
        ${r.delivery_fees ? `<p class="muted">رسوم توصيل: ${money(r.delivery_fees)} ج (محسوبة في مجمل الربح)</p>` : ""}
        <section class="panel mt"><h2>المصاريف حسب البند</h2><table><tbody>${r.expenses.map((e) => `<tr><td>${esc(e.category)}</td><td class="num">${money(e.amount)}</td></tr>`).join("") || `<tr><td class="muted">مفيش</td></tr>`}</tbody></table>
        <p class="muted">الرواتب هتتحسب لوحدها لما نعمل الرواتب (المرحلة 2)؛ لحد دلوقتي سجّلها كمصروف "رواتب وأجور".</p></section>`;
    } catch (err) { $("#prBox").innerHTML = `<p class="badge b-err">✕ ${esc(err.message)}</p>`; }
  };
  document.querySelectorAll("#prR [data-r]").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#prR button").forEach((x) => x.classList.toggle("on", x === b));
    const [a, z] = ranges[b.dataset.r]; show(iso(a), iso(z));
  }));
  $("#prGo").addEventListener("click", () => show($("#prF").value, $("#prT").value));
  show(iso(first), iso(now));
}

// ------------------------------------------------------------------ home
async function viewHome() {
  const h = await api("/api/home");
  const hour = new Date().getHours(), greet = hour < 12 ? "صباح الخير" : "مساء الخير";
  const lvl = { error: ["b-err", "✕"], warning: ["b-warn", "⚠"], info: ["b-info", "ℹ"] };
  const can = (p) => ME.permissions.includes(p);
  const diff = h.sales && h.sales_week_ago ? Math.round((h.sales.total - h.sales_week_ago) / h.sales_week_ago * 100) : null;
  app.innerHTML = `<section class="panel"><h1>${greet} يا ${esc(ME.user.full_name)}</h1>
    <div class="quick">
      ${can("sales.create") ? `<a class="btn big" href="#/pos">الكاشير</a>` : ""}
      ${can("purchases.create") ? `<a class="btn secondary" href="#/purchases">فاتورة شراء</a>` : ""}
      <a class="btn secondary" href="#/items">الأصناف</a>
      ${can("inventory.count") ? `<a class="btn secondary" href="#/counts">الجرد</a>` : ""}
      ${can("sales.create") ? `<a class="btn secondary" href="#/shift">الشيفت</a>` : ""}</div></section>
  <div class="totals">
    ${h.sales ? `<div>مبيعات النهارده<b>${money(h.sales.total)}</b><span>${h.sales.n} فاتورة${diff != null ? ` · ${diff >= 0 ? "▲" : "▼"} ${Math.abs(diff)}% عن نفس اليوم الأسبوع اللي فات` : ""}</span></div>` : ""}
    <div>فواتيرك النهارده<b>${h.mine.n}</b><span>${money(h.mine.t)} ج</span></div>
    <div class="${h.shift_open ? "t-ok" : "t-warn"}">${h.shift_open ? "✓ الشيفت مفتوح" : "⚠ الشيفت مقفول"}<b>${h.shift_open ? "شغال" : "افتحه"}</b></div>
    ${h.backup ? `<div class="${!h.backup.last_at && h.backup.ok == null ? "t-warn" : h.backup.ok ? "t-ok" : "t-err"}">${!h.backup.last_at && h.backup.ok == null ? "⚠ لسه مفيش نسخة أوتوماتيك" : h.backup.ok ? "✓ آخر نسخة احتياطية" : "✕ النسخة الاحتياطية فشلت"}<b>${h.backup.last_at ? esc(h.backup.last_at.slice(11, 16)) : "—"}</b><span>${h.backup.offsite_error ? "⚠ النسخة برّه الجهاز فيها مشكلة" : ""}</span></div>` : ""}
  </div>
  <section class="panel mt"><h2>محتاج انتباهك</h2>
    <ul class="notes">${h.alerts.map((a) => `<li><span class="badge ${lvl[a.level][0]}">${lvl[a.level][1]}</span><div>${esc(a.text)} ${a.link ? `<a href="${a.link}">افتح</a>` : ""}</div></li>`).join("")
      || `<li><span class="badge b-ok">✓</span><div>كله تمام</div></li>`}</ul>
    ${h.notifications_unread ? `<p class="mt"><a href="#/notifications">🔔 ${h.notifications_unread} إشعار جديد</a></p>` : ""}</section>`;
}

// ------------------------------------------------------------------ reports
function csvDownload(name, rows) {
  const text = "\ufeff" + rows.map((r) => r.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" })); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
async function viewReports() {
  const can = (p) => ME.permissions.includes(p);
  const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const now = new Date(), first = new Date(now.getFullYear(), now.getMonth(), 1);
  app.innerHTML = `<section class="panel"><div class="row between"><h1>التقارير</h1>${can("costs.read") ? `<a href="#/profit">تقرير الربح ←</a>` : ""}</div>
    ${can("sales.totals") ? `<div class="row"><label class="f">من <input type="date" id="rF" value="${iso(first)}"></label><label class="f">لحد <input type="date" id="rT" value="${iso(now)}"></label>
      <button class="btn small" id="rGo">اعرض المبيعات</button></div>` : `<p class="muted">تقارير المبيعات للمدير والصاحب.</p>`}</section>
  <div id="rSales"></div><div id="rStock"></div>`;
  const it = (r) => ({ pack_size: r.pack_size, base_unit_name: r.base_unit_name });
  const sales = async () => {
    const r = await api(`/api/reports/sales?from=${$("#rF").value}&to=${$("#rT").value}`);
    const maxH = Math.max(1, ...r.by_hour.map((x) => x.total));
    $("#rSales").innerHTML = `<div class="totals"><div>الفواتير<b>${r.totals.n}</b></div><div>المبيعات<b>${money(r.totals.total)}</b></div>
      <div>المرتجع<b>${money(r.totals.returns)}</b></div><div>الخصومات<b>${money(r.totals.discount)}</b></div></div>
    <div class="grid2 mt"><section class="panel"><div class="row between"><h2>يوم بيوم</h2><button class="btn small secondary" id="csvD">تنزيل Excel (CSV)</button></div>
      <table><thead><tr><th>اليوم</th><th>فواتير</th><th>الإجمالي</th><th>نقدي</th><th>فيزا</th><th>تحويل</th><th>آجل</th><th>مرتجع</th></tr></thead><tbody>
      ${r.daily.map((d) => `<tr><td>${esc(d.day)}</td><td>${d.n}</td><td class="num"><b>${money(d.total)}</b></td><td class="num">${money(d.cash)}</td><td class="num">${money(d.card)}</td><td class="num">${money(d.transfer)}</td><td class="num">${money(d.credit)}</td><td class="num">${money(d.returns)}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">مفيش</td></tr>`}</tbody></table></section>
    <section class="panel"><h2>حسب الموظف</h2><table><tbody>${r.by_user.map((u) => `<tr><td>${esc(u.name)}</td><td>${u.n} فاتورة</td><td class="num">${money(u.total)}</td><td class="num">${u.discount ? `خصم ${money(u.discount)}` : ""}</td></tr>`).join("") || `<tr><td class="muted">مفيش</td></tr>`}</tbody></table>
      <h2 class="mt">ساعات الشغل</h2><div class="hbars">${Array.from({ length: 24 }, (_, h) => { const x = r.by_hour.find((y) => y.hour === h); return `<div title="${h}:00 — ${x ? x.n : 0} فاتورة"><span data-h="${Math.round(((x?.total || 0) / maxH) * 100)}"></span><small>${h}</small></div>`; }).join("")}</div></section></div>
    <section class="panel mt"><div class="row between"><h2>أكتر الأصناف بيعًا</h2><button class="btn small secondary" id="csvT">تنزيل CSV</button></div><table><thead><tr><th>الصنف</th><th>الكمية</th><th>المبيعات</th>${can("costs.read") ? "<th>الربح</th>" : ""}</tr></thead><tbody>
      ${r.top_items.map((t) => `<tr><td>${itemHtml(t)}</td><td>${esc(fmtStock(t.qty, it(t)))}</td><td class="num">${money(t.revenue)}</td>${can("costs.read") ? `<td class="num">${t.profit != null ? money(t.profit) : "—"}</td>` : ""}</tr>`).join("") || `<tr><td colspan="4" class="muted">مفيش</td></tr>`}</tbody></table></section>`;
    document.querySelectorAll(".hbars [data-h]").forEach((el) => { el.style.height = `${el.dataset.h}%`; });
    $("#csvD").addEventListener("click", () => csvDownload(`مبيعات ${r.from} — ${r.to}.csv`, [["اليوم", "فواتير", "الإجمالي", "نقدي", "فيزا", "تحويل", "آجل", "مرتجع"],
      ...r.daily.map((d) => [d.day, d.n, money(d.total), money(d.cash), money(d.card), money(d.transfer), money(d.credit), money(d.returns)])]));
    $("#csvT").addEventListener("click", () => csvDownload(`أكتر الأصناف ${r.from}.csv`, [["الصنف", "الكمية بالوحدة", "المبيعات", "الربح"],
      ...r.top_items.map((t) => [t.name_en || t.name_ar, t.qty, money(t.revenue), t.profit != null ? money(t.profit) : ""])]));
  };
  if (can("sales.totals")) { $("#rGo").addEventListener("click", () => sales().catch((e) => toast(e.message, true))); sales().catch((e) => toast(e.message, true)); }
  if (can("inventory.read")) {
    const s = await api("/api/reports/stock");
    $("#rStock").innerHTML = `${s.value_by_category ? `<section class="panel mt"><h2>قيمة المخزون</h2><div class="totals"><div>بالتكلفة<b>${money(s.value_cost)}</b></div><div>بسعر الجمهور<b>${money(s.value_retail)}</b></div></div>
      <table class="mt"><thead><tr><th>القسم</th><th>أصناف</th><th>بالتكلفة</th><th>بسعر الجمهور</th></tr></thead><tbody>${s.value_by_category.map((c) => `<tr><td>${esc(c.category)}</td><td>${c.items}</td><td class="num">${money(c.cost)}</td><td class="num">${money(c.retail)}</td></tr>`).join("")}</tbody></table></section>` : ""}
    <section class="panel mt"><div class="row between"><h2>الصلاحية خلال 90 يوم (${s.expiring.length})</h2><button class="btn small secondary" id="csvE">تنزيل CSV</button></div>
      <table><thead><tr><th>الصنف</th><th>التشغيلة</th><th>الصلاحية</th><th>الكمية</th><th>القيمة (جمهور)</th></tr></thead><tbody>
      ${s.expiring.map((e) => `<tr class="clickable" data-go="${esc(e.item_id)}"><td>${itemHtml(e)}</td><td>${esc(e.batch_no || "—")}</td>
        <td><span class="badge ${e.days_left < 0 ? "b-err" : e.days_left <= 30 ? "b-warn" : "b-info"}">${e.days_left < 0 ? "✕ خلصت" : e.days_left <= 30 ? "⚠" : "ℹ"} ${esc(e.expiry_date.slice(0, 7))} (${e.days_left} يوم)</span></td>
        <td>${esc(fmtStock(e.qty, it(e)))}</td><td class="num">${money(e.value)}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">✓ مفيش</td></tr>`}</tbody></table>
      <p class="muted">ابدأ بيع الأقرب، أو رجّعه للمورد قبل ما يخلص لو بيقبل مرتجع.</p></section>`;
    document.querySelectorAll("#rStock [data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/items/${tr.dataset.go}`; }));
    $("#csvE").addEventListener("click", () => csvDownload("صلاحية قريبة.csv", [["الصنف", "التشغيلة", "الصلاحية", "الكمية بالوحدة", "القيمة"],
      ...s.expiring.map((e) => [e.name_en || e.name_ar, e.batch_no || "", e.expiry_date, e.qty, money(e.value)])]));
  }
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
    <button class="btn small secondary" id="nRead" ${r.unread ? "" : "disabled"}>علّم الكل اتقرا</button></div>
    <ul class="notes">${r.items.map((n) => {
      const [cls, ic] = NSEV[n.severity] || NSEV.info;
      return `<li class="${n.seen ? "" : "new"}"><span class="badge ${cls}">${ic}</span>
        <div><b>${esc(n.title)}</b>${n.body ? `<div>${esc(n.body)}</div>` : ""}
        <div class="muted">${esc(n.actor || "")}${n.approver ? ` · بموافقة ${esc(n.approver)}` : ""} · ${new Date(n.created_at).toLocaleString("ar-EG")}
        ${n.entity_type === "sale" ? ` · <a href="#/sales/${esc(n.entity_id)}">الفاتورة</a>` : ""}
        ${n.entity_type === "purchase_invoice" ? ` · <a href="#/purchases/${esc(n.entity_id)}">الفاتورة</a>` : ""}</div></div></li>`;
    }).join("") || `<li class="muted">مفيش إشعارات</li>`}</ul></section>`;
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
      <button class="btn small secondary" id="prReset">↺ رجّع كل حاجة للافتراضي</button></div>
    <p class="muted">الإعدادات دي ليك انت بس، وبتتحفظ على حسابك. الافتراضي هو الأريح لشغل اليوم كله.</p>
    <h2 class="mt">الألوان</h2>
    <div class="themes">
      <label><span class="swatch sw-light"><i></i><i></i></span><span><input type="radio" name="theme" value="light" ${p.theme === "light" ? "checked" : ""}> فاتح دافي (الافتراضي)</span></label>
      <label><span class="swatch sw-dark"><i></i><i></i></span><span><input type="radio" name="theme" value="dark" ${p.theme === "dark" ? "checked" : ""}> ليلي</span></label>
      <label><span class="swatch sw-contrast"><i></i><i></i></span><span><input type="radio" name="theme" value="contrast" ${p.theme === "contrast" ? "checked" : ""}> تباين عالي</span></label>
    </div>
    <h2 class="mt">لون البرنامج</h2>
    <div class="accents">${ACCENTS.map(([c, n]) => `<button type="button" class="acc-${c.slice(1).toLowerCase()} ${cur === c ? "on" : ""}" data-acc="${c}" title="${n}" aria-label="${n}"></button>`).join("")}
      <label class="f">لون تاني <input type="color" id="accPick" value="${cur.toLowerCase()}"></label></div>
    <p class="badge b-info mt">ℹ ألوان التنبيه ثابتة: أحمر = غلط، أخضر = تمام، برتقالي = تنبيه، أزرق = معلومة.</p>
    <h2 class="mt">حجم الخط</h2>
    <div class="seg">${radio("font", "s", "صغير")}${radio("font", "m", "عادي (الافتراضي)")}${radio("font", "l", "كبير")}${radio("font", "xl", "كبير جدًا")}</div>
    <p class="muted">اختصار: Ctrl و + أو Ctrl و −</p>
    <h2 class="mt">أسماء الأصناف</h2>
    <div class="seg">${radio("names", "both", "الاتنين (الافتراضي)")}${radio("names", "en", "إنجليزي بس")}${radio("names", "ar", "عربي بس")}</div>
    <h2 class="mt">لغة البرنامج</h2>
    <div class="seg">${radio("lang", "ar", "العربية (الافتراضي)")}<label class="muted"><input type="radio" disabled> English — جاية في الخطوة 15</label></div>
  </section>
  <section class="panel narrowpanel"><h1>الرقم السري</h1>
    <p>${esc(ME.user.full_name)} — <span class="badge b-info">${esc(ME.role_name || "")}</span>
    <span class="badge ${ME.has_pin ? "b-ok" : "b-warn"}">${ME.has_pin ? "✓ عندك رقم سري" : "⚠ لسه معندكش رقم سري"}</span></p>
    <p class="muted">الرقم السري (4 لـ 6 أرقام) للدخول السريع على جهاز الصيدلية${["owner", "manager"].includes(ME.role) ? "، وللموافقة على شاشة حد تاني" : ""}.</p>
    <form id="pinSet" class="row">
      <label class="f">كلمة المرور الحالية <input name="password" type="password" autocomplete="current-password" required></label>
      <label class="f">الرقم السري الجديد <input name="pin" type="password" inputmode="numeric" maxlength="6" autocomplete="off" required></label>
      <button class="btn">احفظ</button></form></section>`;
  const save = async (ch) => { try { await savePrefs(ch); viewMe(); } catch (err) { toast(err.message, true); viewMe(); } };
  document.querySelectorAll("input[type=radio][name]").forEach((r) => r.addEventListener("change", () => save({ [r.name]: r.value })));
  document.querySelectorAll("[data-acc]").forEach((b) => b.addEventListener("click", () => save({ accent: b.dataset.acc === "#0F5E54" ? null : b.dataset.acc })));
  $("#accPick").addEventListener("change", (e) => {
    const c = e.target.value.toUpperCase(), cr = contrastWhite(c);
    if (cr < 4.5) { toast(`اللون ده فاتح: الكلام الأبيض عليه مش هيتقري (التباين ${cr.toFixed(1)} والمطلوب 4.5). اختار أغمق`, true); e.target.value = cur.toLowerCase(); return; }
    save({ accent: c });
  });
  $("#prReset").addEventListener("click", (e) => { if (confirmInline(e.target)) save({ reset: true }); });
  $("#pinSet").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/me/pin", { method: "POST", body: { password: e.target.password.value, pin: e.target.pin.value } });
      ME = await api("/api/me"); toast("اتحفظ الرقم السري"); viewMe();
    } catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ users & permissions (owner)
const ROLE_CLS = { owner: "b-brand", manager: "b-ok", pharmacist: "b-info", assistant: "b-plain" };
const pctOf = (bp) => (bp == null ? "" : String(bp / 100));
async function viewUsers() {
  if (!ME.permissions.includes("users.manage")) { app.innerHTML = `<section class="panel"><p>الصفحة دي للصاحب بس.</p></section>`; return; }
  const [{ users, roles }, sales] = await Promise.all([api("/api/users"), api("/api/settings/sales")]);
  app.innerHTML = `<section class="panel"><div class="row between"><h1>المستخدمين</h1></div>
    <table><thead><tr><th>الاسم</th><th>النوع</th><th>الحالة</th><th>رقم سري</th><th>تعديلات على الافتراضي</th></tr></thead>
    <tbody>${users.map((u) => `<tr class="clickable" data-go="${esc(u.id)}"><td><b>${esc(u.full_name)}</b><div class="muted">${esc(u.username)}</div></td>
      <td><span class="badge ${ROLE_CLS[u.role] || ""}">${esc(u.role_name)}</span></td>
      <td>${u.active ? `<span class="badge b-ok">✓ شغال</span>` : `<span class="badge b-err">✕ موقوف</span>`}</td>
      <td>${u.has_pin ? "✓" : `<span class="badge b-warn">⚠ مفيش</span>`}</td>
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
    <p class="muted">الافتراضي: مفيش خصم لحد غير الصاحب. ومهما كان الحد، محدش يقدر يخصم لحد ما السعر ينزل تحت التكلفة غير الصاحب.</p>
    <div class="row">${roles.filter((r) => r.code !== "owner").map((r) => `<label class="f">${esc(r.name)} — أقصى خصم %
      <span class="row"><input class="narrow" type="number" min="0" max="100" step="0.5" value="${pctOf(r.max_discount_bp)}" data-rdisc="${r.code}">
      <button class="btn small secondary" data-rsave="${r.code}">احفظ</button></span></label>`).join("")}</div></section>
  <section class="panel"><h2>مدة المرتجع</h2>
    <form id="retWin" class="row"><label class="f">عدد الأيام <input class="narrow" type="number" min="0" max="365" name="days" value="${sales.return_window_days}"></label>
    <button class="btn small secondary">احفظ</button></form>
    <p class="muted">بعد المدة دي المرتجع بيحتاج الصاحب (أو حد الصاحب فتحله "مرتجع بعد مدة المرتجع").</p></section>`;
  document.querySelectorAll("[data-go]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/users/${tr.dataset.go}`; }));
  $("#uNew").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (!f.pin) delete f.pin;
    try { const r = await api("/api/users", { method: "POST", body: f }); toast("اتضاف"); location.hash = `#/users/${r.user.id}`; }
    catch (err) { toast(err.message, true); }
  });
  document.querySelectorAll("[data-rsave]").forEach((b) => b.addEventListener("click", async () => {
    const v = document.querySelector(`[data-rdisc="${b.dataset.rsave}"]`).value;
    try { await api(`/api/roles/${b.dataset.rsave}/discount`, { method: "POST", body: { max_discount_bp: toBp(v) ?? 0 } }); toast("اتحفظ"); }
    catch (err) { toast(err.message, true); }
  }));
  $("#retWin").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("/api/settings/sales", { method: "PUT", body: { return_window_days: +e.target.days.value } }); toast("اتحفظ"); }
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
    if (it.code === "users.manage") return `<span class="muted">للصاحب بس</span>`;
    const def = it.roles[u.role];
    return `<select data-perm="${it.code}" class="${it.exception === "grant" ? "ex-grant" : it.exception === "deny" ? "ex-deny" : ""}">
      <option value="default" ${!it.exception ? "selected" : ""}>${def ? "✓ مسموح" : "✕ ممنوع"} (الافتراضي)</option>
      ${def ? `<option value="deny" ${it.exception === "deny" ? "selected" : ""}>✕ مقفولة عليه</option>`
            : `<option value="grant" ${it.exception === "grant" ? "selected" : ""}>✓ مفتوحة له</option>`}
    </select>`;
  };
  app.innerHTML = `<section class="panel"><div class="row between">
      <div><a href="#/users">← المستخدمين</a><h1>${esc(u.full_name)} <span class="badge ${ROLE_CLS[u.role] || ""}">${esc(u.role_name)}</span></h1>
      <p class="muted">${owner ? "الصاحب صلاحياته كاملة ومحدش يقدر يقفل عليه حاجة." : "صلاحياته جاية من نوعه. أي تعديل هنا بيخصه هو بس."}</p></div>
      ${owner ? "" : `<button class="btn secondary" id="uReset">↺ رجّعه للافتراضي</button>`}</div>
    ${owner ? "" : `<div class="row">
      <label class="f">النوع <select id="uRole">${roles.filter((r) => r.code !== "owner").map((r) => `<option value="${r.code}" ${r.code === u.role ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select></label>
      <label class="f">أقصى خصم % (فاضي = حد نوعه ${pctOf(m.role_discount_bp[u.role])}%)
        <span class="row"><input id="uDisc" class="narrow" type="number" min="0" max="100" step="0.5" value="${pctOf(m.max_discount_bp)}">
        <button class="btn small secondary" id="uDiscSave">احفظ</button></span></label>
      <button class="btn small ${u.active ? "danger" : ""}" id="uActive">${u.active ? "إيقاف المستخدم" : "تشغيل المستخدم"}</button></div>`}
    <div class="row mt">
      <form id="uPin" class="row"><label class="f">رقم سري جديد <input name="pin" type="password" inputmode="numeric" maxlength="6" autocomplete="off" required></label><button class="btn small secondary">احفظ الرقم</button></form>
      ${owner ? "" : `<form id="uPw" class="row"><label class="f">كلمة مرور جديدة <input name="password" type="password" minlength="10" autocomplete="new-password" required></label><button class="btn small secondary">غيّر كلمة المرور</button></form>`}
    </div></section>
  <section class="panel tablewrap"><h2>الصلاحيات</h2>
    <div class="legend"><span class="badge b-info">✓ مفتوحة له = اتفتحت للشخص ده بس</span><span class="badge b-err">✕ مقفولة عليه = اتقفلت عليه بس</span><span>الباقي = افتراضي نوعه</span></div>
    <table class="perms"><thead><tr><th>الصلاحية</th>${["owner", "manager", "pharmacist", "assistant"].map((c) => `<th>${esc(roleName[c] || c)}</th>`).join("")}<th>${esc(u.full_name)}</th></tr></thead>
    <tbody>${m.groups.map((g) => `<tr class="grp"><td colspan="6">${esc(g.title)}</td></tr>` + g.items.map((it) => `<tr class="${it.exception ? "ex" : ""}">
      <td>${esc(it.label)}</td>${["owner", "manager", "pharmacist", "assistant"].map((c) => `<td>${cell(it.roles[c])}</td>`).join("")}
      <td>${pick(it)}</td></tr>`).join("")).join("")}</tbody></table></section>`;
  const post = async (action, body) => {
    try { await api(`/api/users/${id}/${action}`, { method: "POST", body }); toast("اتحفظ"); viewUser(id); }
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
