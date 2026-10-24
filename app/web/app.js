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

async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: { "X-Requested-With": "pharm" }, credentials: "same-origin" };
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
  if (!r.ok) {
    if (r.status === 401 && !path.startsWith("/api/auth")) { ME = null; location.hash = "#/login"; }
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
];

async function route() {
  const hash = location.hash || "#/purchases";
  if (!ME && hash !== "#/setup") {
    try { if ((await api("/api/setup")).needed) { location.hash = "#/setup"; return; } } catch { /* offline: fall through */ }
  }
  if (!ME && hash !== "#/login" && hash !== "#/setup") {
    try { ME = await api("/api/me"); } catch { location.hash = "#/login"; return; }
  }
  $("#topbar").hidden = !ME;
  if (ME) $("#who").textContent = `${ME.user.full_name} — ${ME.organization.name}`;
  if (ME && !route.badgeChecked) { route.badgeChecked = true; checkUpdateBadge(); }
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", hash.startsWith("#/" + a.dataset.nav)));
  for (const [re, fn] of routes) {
    const m = hash.match(re);
    if (m) { try { await fn(...m.slice(1)); } catch (e) { toast(e.message, true); } return; }
  }
  location.hash = "#/purchases";
}
window.addEventListener("hashchange", route);
$("#logout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  ME = null; location.hash = "#/login";
});

// ------------------------------------------------------------------ login
function viewSetup() {
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

function viewLogin() {
  $("#topbar").hidden = true;
  app.innerHTML = `
  <form class="panel login" id="loginForm">
    <h1>تسجيل الدخول</h1>
    <label class="f">اسم المستخدم <input name="username" autocomplete="username" required></label><br>
    <label class="f">كلمة المرور <input name="password" type="password" autocomplete="current-password" required></label><br>
    <button class="btn" type="submit">دخول</button>
  </form>`;
  $("#loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const r = await api("/api/auth/login", { method: "POST",
        body: { username: f.get("username"), password: f.get("password") } });
      ME = r.me; location.hash = "#/purchases";
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

  app.innerHTML = `
  <section class="panel">
    <div class="row between">
      <h1>فاتورة شراء <span class="badge ${STATUS[inv.status][1]}">${STATUS[inv.status][0]}</span>
        ${inv.extraction_method === "ai_vision" ? `<span class="badge b-ai">قراءة آلية — راجع كل القيم</span>` : ""}</h1>
      <a href="#/purchases">رجوع للفواتير</a>
    </div>
    <div class="row">
      <label class="f">المورد <select data-h="supplier_id" ${draft ? "" : "disabled"}>
        <option value="">— اختر —</option></select></label>
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
      <div>الفرق<b>${inv.declared_total != null && inv.computed_total != null ? money(inv.declared_total - inv.computed_total) : "—"}</b></div>
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
    q.value = it.name_en || it.name_ar;
    $("#qaPicked").textContent = `العبوة ${it.pack_size} ${unitName(it)} · الرصيد الحالي ${fmtStock(it.stock_qty, it) || "0"}`;
    if (it.public_price != null) $("#qaPrice").value = (it.public_price / 100).toFixed(2);
    $("#qaQty").focus();
  };
  let t, res = [];
  q.addEventListener("input", () => {
    QA_ITEM = null; clearTimeout(t);
    t = setTimeout(async () => {
      const v = q.value.trim(); if (v.length < 2) { $("#qaRes").innerHTML = ""; return; }
      res = await api(`/api/items?q=${encodeURIComponent(v)}&limit=8`);
      if (res.length === 1 && /^\d{6,}$/.test(v)) { pick(res[0]); return; }
      $("#qaRes").innerHTML = res.map((r, i) => `<button type="button" class="btn small secondary" data-qa="${i}">${esc(r.name_en || r.name_ar)}
        <span class="muted">${esc(r.strength_norm || "")} · ${money(r.public_price)} ج</span></button>`).join("") || `<p class="muted">مفيش نتائج</p>`;
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

async function fillSuppliers(selected, draft) {
  const sel = $('[data-h="supplier_id"]');
  const list = await api("/api/suppliers");
  sel.innerHTML = `<option value="">— اختر —</option>` + list.map((s) =>
    `<option value="${esc(s.id)}" ${s.id === selected ? "selected" : ""}>${esc(s.name)}</option>`).join("");
  sel.disabled = !draft;
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
async function viewItems() {
  app.innerHTML = `
  <section class="panel">
    <div class="row between"><h1>الأصناف</h1><a href="/api/export/items.xlsx">تصدير كل الأصناف Excel</a></div>
    <div class="row"><label class="f grow">بحث بالاسم أو الباركود
      <input id="q" placeholder="مثال: panadol أو بانادول أو 622..." autocomplete="off"></label></div>
  </section>
  <section class="panel"><div class="tablewrap"><table>
    <thead><tr><th>الاسم</th><th>التركيز</th><th>الشكل</th><th>العبوة</th><th>سعر الجمهور</th><th>الرصيد</th><th>الكود</th></tr></thead>
    <tbody id="itemsBody"></tbody></table></div></section>`;
  const load = async () => {
    const rows = await api(`/api/items?q=${encodeURIComponent($("#q").value)}&limit=50`);
    $("#itemsBody").innerHTML = rows.map((r, i) => `<tr class="clickable" data-i="${i}"><td>${esc(r.name_en || "")}<div class="raw">${esc(r.name_ar || "")}</div></td>
      <td>${esc(r.strength_norm || "")}</td><td>${esc(r.dosage_form || "")}</td>
      <td>${r.pack_size} ${esc(unitName(r))}</td><td>${money(r.public_price)}</td><td><b>${esc(fmtStock(r.stock_qty, r))}</b></td><td>${esc(r.code || "")}</td></tr>
      <tr class="detail" id="det${i}" hidden><td colspan="7"></td></tr>`).join("")
      || `<tr><td colspan="7" class="muted">لا توجد نتائج</td></tr>`;
    $("#itemsBody").querySelectorAll("tr.clickable").forEach((tr) => tr.addEventListener("click", async () => {
      const r = rows[+tr.dataset.i], det = $(`#det${tr.dataset.i}`);
      if (!det.hidden) { det.hidden = true; return; }
      const it = await api(`/api/items/${r.id}`), st = it.stock || { batches: [] };
      det.firstElementChild.innerHTML = st.batches.length ? `<table class="sub"><thead><tr><th>الصلاحية</th><th>التشغيلة</th><th>الكمية</th></tr></thead><tbody>
        ${st.batches.map((b) => `<tr><td>${esc(b.expiry_date ? (b.expiry_precision === "month" ? b.expiry_date.slice(0, 7) : b.expiry_date) : "غير مسجلة")}</td>
        <td>${esc(b.batch_no || "—")}</td><td>${esc(fmtStock(b.qty, r))}</td></tr>`).join("")}</tbody></table>
        ${st.avg_cost != null ? `<p class="muted">متوسط التكلفة للعلبة: ${(Number(st.avg_cost) * r.pack_size).toFixed(2)} ج</p>` : ""}`
        : `<p class="muted">مفيش رصيد</p>`;
      det.hidden = false;
    }));
  };
  let t;
  $("#q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 250); });
  load();
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
    const res = await api(`/api/items?q=${encodeURIComponent(v)}&limit=8`);
    if (res.length === 1) return chooseItem(res[0]);
    if (!res.length) return toast("الصنف غير موجود", true);
    $("#cntPick").innerHTML = `<div class="cands">${res.map((r, i) => `<button class="btn secondary small" data-pk="${i}">${esc(r.name_en || r.name_ar)}</button>`).join("")}</div>`;
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
  app.innerHTML = `<section class="panel"><h1>البرنامج</h1><div id="sysBox"><p class="muted">بيتأكد من التحديثات…</p></div></section>`;
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

async function viewPos() {
  if (!SALE || SALE.status !== "draft") SALE = await api("/api/sales", { method: "POST", body: {} });
  app.innerHTML = `
  <div class="review">
    <section class="panel">
      <h1>الكاشير</h1>
      <label class="f">امسح الباركود أو اكتب اسم الصنف <input id="posQ" autocomplete="off" autofocus></label>
      <div id="posRes" class="cands mt"></div>
      <div class="row mt"><label class="f grow">العميل (للآجل) <input id="posCust" placeholder="اسم أو تليفون"></label>
        <button class="btn secondary small" id="posCustNew" type="button">عميل جديد</button></div>
      <div id="posCustRes" class="cands"></div>
      <p class="muted mt"><a href="#" id="posSummary">ملخص اليوم</a> · <a href="#" id="posShift">الخزنة</a></p><div id="posSum"></div>
    </section>
    <section class="panel" id="posCart"></section>
  </div>`;
  renderCart();
  const q = $("#posQ");
  const search = async () => {
    const v = q.value.trim(); if (!v) return;
    const res = await api(`/api/items?q=${encodeURIComponent(v)}&limit=8`);
    if (res.length === 1 && /^\d{6,}$/.test(v)) { await addItem(res[0], 0, 1); q.value = ""; return; }
    $("#posRes").innerHTML = res.map((r, i) => `<div class="row"><span class="grow">${esc(r.name_en || r.name_ar)} <span class="muted">${esc(r.strength_norm || "")} · العبوة ${r.pack_size} ${esc(unitName(r))} · ${money(r.public_price)} ج · الرصيد ${esc(fmtStock(r.stock_qty, r) || "0")}</span></span>
      <button class="btn small" data-add="${i}" data-kind="pack">علبة</button>
      ${r.pack_size > 1 ? `<button class="btn small secondary" data-add="${i}" data-kind="unit">${esc(unitName(r))}</button>` : ""}</div>`).join("") || `<p class="muted">لا توجد نتائج</p>`;
    $("#posRes").querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () =>
      addItem(res[+b.dataset.add], b.dataset.kind === "pack" ? 1 : 0, b.dataset.kind === "unit" ? 1 : 0)));
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); search().catch((er) => toast(er.message, true)); } });
  $("#posCust").addEventListener("input", async (e) => {
    const v = e.target.value.trim(); if (v.length < 2) return;
    const cs = await api(`/api/customers?q=${encodeURIComponent(v)}`);
    $("#posCustRes").innerHTML = cs.map((c) => `<button class="btn small secondary" data-cust="${esc(c.id)}">${esc(c.name)} — رصيد ${money(c.balance)} / حد ${money(c.credit_limit)}</button>`).join("");
    $("#posCustRes").querySelectorAll("[data-cust]").forEach((b) => b.addEventListener("click", async () => {
      // customer is fixed when the sale is created: start a new draft for this customer, moving lines
      const lines = SALE.lines;
      SALE = await api("/api/sales", { method: "POST", body: { customer_id: b.dataset.cust } });
      for (const l of lines) SALE = await api(`/api/sales/${SALE.id}/lines`, { method: "POST", body: { item_id: l.item_id, units: l.qty_base, discount_bp: l.discount_bp } });
      $("#posCustRes").innerHTML = ""; renderCart();
    }));
  });
  $("#posCustNew").addEventListener("click", async () => {
    const name = prompt("اسم العميل:"); if (!name) return;
    const phone = prompt("التليفون (اختياري):") || null;
    const limit = parseFloat(prompt("حد الآجل بالجنيه (0 = بدون آجل):", "0") || "0");
    try { await api("/api/customers", { method: "POST", body: { name, phone, credit_limit: Math.round(limit * 100) } }); toast("تم إضافة العميل"); }
    catch (er) { toast(er.message, true); }
  });
  $("#posShift").addEventListener("click", async (e) => {
    e.preventDefault();
    let sh = null;
    try { sh = await api("/api/cash/current"); } catch { /* no open shift */ }
    if (!sh) {
      const v = prompt("الخزنة مقفولة. رصيد بداية الوردية بالجنيه:", "0");
      if (v === null) return;
      try { sh = await api("/api/cash/open", { method: "POST", body: { opening_cash: toPiasters(v) || 0 } }); }
      catch (er) { return toast(er.message, true); }
    }
    const k = sh.cash_by_kind || {};
    $("#posSum").innerHTML = `<div class="totals"><div>بداية<b>${money(sh.opening_cash)}</b></div>
      <div>مبيعات نقدي<b>${money(k.sale || 0)}</b></div><div>مرتجع/إلغاء<b>${money((k.refund || 0) + (k.void || 0))}</b></div>
      <div>تحصيل آجل<b>${money(k.customer_payment || 0)}</b></div><div>مصروفات<b>${money(k.payout || 0)}</b></div>
      <div>المفروض في الدرج<b>${money(sh.expected_cash)}</b></div><div>فيزا<b>${money(sh.card_total)}</b></div></div>
      ${ME.permissions.includes("cash.manage") ? `<div class="row mt"><button class="btn small secondary" id="shPay">مصروف من الدرج</button>
      <button class="btn small" id="shClose">قفل الوردية</button></div>` : ""}`;
    $("#shPay")?.addEventListener("click", async () => {
      const a = prompt("المبلغ بالجنيه:"); const n = a && prompt("السبب:"); if (!n) return;
      try { await api("/api/cash/movements", { method: "POST", body: { kind: "payout", amount: toPiasters(a), note: n } }); toast("تم"); }
      catch (er) { toast(er.message, true); }
    });
    $("#shClose")?.addEventListener("click", async () => {
      const c = prompt(`عدّ الفلوس في الدرج واكتب المبلغ (المتوقع ${money(sh.expected_cash)}):`); if (c === null) return;
      let note = null;
      if (toPiasters(c) !== sh.expected_cash) { note = prompt("فيه فرق — اكتب السبب:"); if (!note) return; }
      try { const r = await api("/api/cash/close", { method: "POST", body: { counted_cash: toPiasters(c), note } });
        toast(`تم قفل الوردية — الفرق ${money(r.difference)}`); $("#posSum").innerHTML = ""; }
      catch (er) { toast(er.message, true); }
    });
  });
  $("#posSummary").addEventListener("click", async (e) => {
    e.preventDefault();
    const s = await api("/api/sales/summary");
    $("#posSum").innerHTML = `<div class="totals"><div>فواتير<b>${s.n}</b></div><div>مبيعات<b>${money(s.total)}</b></div>
      <div>نقدي<b>${money(s.cash)}</b></div><div>فيزا<b>${money(s.card)}</b></div><div>آجل<b>${money(s.credit)}</b></div>
      <div>مرتجع<b>${money(s.returns)}</b></div>${s.gross_profit_egp != null ? `<div>مجمل الربح<b>${esc(s.gross_profit_egp)}</b></div>` : ""}</div>`;
  });
}

async function addItem(item, packs, units) {
  try {
    SALE = await api(`/api/sales/${SALE.id}/lines`, { method: "POST", body: { item_id: item.id, packs, units, client_ref: newKey() } });
    $("#posRes").innerHTML = ""; $("#posQ").value = ""; $("#posQ").focus();
    renderCart();
  } catch (er) { toast(er.message, true); }
}

function renderCart(findings = null) {
  const s = SALE;
  $("#posCart").innerHTML = `
    <h2>الفاتورة ${s.customer ? `— ${esc(s.customer.name)} (رصيد ${money(s.customer.balance)} / حد ${money(s.customer.credit_limit)})` : ""}</h2>
    <div class="tablewrap"><table>
      <thead><tr><th>الصنف</th><th>الكمية</th><th>التشغيلة/السعر</th><th>الإجمالي</th><th></th></tr></thead>
      <tbody>${s.lines.map((l) => `<tr class="${l.problem ? "has-err" : ""}">
        <td><b>${esc(l.item.name)}</b><div class="muted">${esc(l.item.strength || "")} ${esc(l.item.form || "")}${l.item.active_ingredient ? ` · ${esc(l.item.active_ingredient)}` : ""}</div>
          ${l.near_expiry ? `<span class="badge b-warn">⚠️ صلاحية قريبة</span>` : ""}${l.problem ? `<span class="badge b-err">${l.problem === "no_price" ? "بدون سعر" : "غير متاح"}</span>` : ""}</td>
        <td>${l.qty_base} ${esc(l.item.base_unit)}</td>
        <td class="muted">${l.allocations.map((a) => `${esc(a.batch_no || "—")}: ${a.qty} × ${money(a.unit_price)}`).join("<br>")}</td>
        <td>${money(l.net)}${l.discount ? `<div class="muted">خصم ${money(l.discount)}</div>` : ""}</td>
        <td><button class="btn small secondary" data-rm="${esc(l.id)}">حذف</button></td></tr>`).join("") || `<tr><td colspan="5" class="muted">امسح أول صنف</td></tr>`}</tbody>
    </table></div>
    <div class="totals mt"><div>الإجمالي<b>${money(s.subtotal)}</b></div><div>الخصم<b>${money(s.discount_total)}</b></div><div>المطلوب<b>${money(s.total)}</b></div></div>
    <div class="row mt">
      <label class="f">نقدي <input id="payCash" type="number" step="0.01" value="${money(s.total)}"></label>
      <label class="f">فيزا <input id="payCard" type="number" step="0.01" value="0"></label>
      <label class="f">آجل <input id="payCredit" type="number" step="0.01" value="0" ${s.customer ? "" : "disabled"}></label>
    </div>
    <div id="posSafety"></div>
    <div class="row mt"><button class="btn" id="posDone" ${s.lines.length ? "" : "disabled"}>${findings ? "تأكيد وإتمام البيع" : "مراجعة السلامة وإتمام البيع"}</button></div>`;
  $("#posCart").querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", async () => {
    SALE = await api(`/api/sales/${SALE.id}/lines/${b.dataset.rm}`, { method: "DELETE" }); renderCart();
  }));
  if (findings) renderFindings(findings);
  $("#posDone").addEventListener("click", () => complete(findings));
}

function renderFindings(findings) {
  const needs = (f) => ["override", "block", "acknowledge"].includes(f.requires);
  $("#posSafety").innerHTML = `<div class="panel mt"><h2>فحص السلامة الدوائية</h2>
    ${findings.map((f, i) => `<div class="mt"><span class="badge ${SEV[f.severity][1]}">${SEV[f.severity][0]}</span> ${esc(f.message)}
      ${f.source ? `<div class="muted">المرجع: ${esc(f.source.name)} ${esc(f.source.version)} ${esc(f.source.ref || "")}</div>` : ""}
      ${f.basis === "heuristic" ? `<div class="muted">تنبيه مبني على تشابه الأسماء</div>` : ""}
      ${needs(f) ? `<input class="mt" data-dec="${i}" placeholder="${f.requires === "acknowledge" ? "ملاحظة (اختياري) ثم تأكيد" : "اكتب سبب الاستمرار (يتسجل باسمك)"}">` : ""}</div>`).join("") || "<p>لا توجد تنبيهات</p>"}</div>`;
}

async function complete(findings) {
  const cash = toPiasters($("#payCash").value) || 0, card = toPiasters($("#payCard").value) || 0,
    credit = toPiasters($("#payCredit").value) || 0;
  if (!findings) {
    const r = await api(`/api/sales/${SALE.id}/check`, { method: "POST", body: {} });
    const blocking = r.findings.filter((f) => ["override", "block", "acknowledge"].includes(f.requires));
    if (r.findings.length && (blocking.length || r.findings.some((f) => f.severity !== "insufficient"))) {
      renderCart(r.findings); $("#payCash").value = money(cash); return;
    }
    findings = r.findings;
  }
  const decisions = {};
  document.querySelectorAll("[data-dec]").forEach((inp) => {
    const f = findings[+inp.dataset.dec];
    // overrides need a reason the user actually typed; acknowledgements may be blank
    if (f.requires === "acknowledge") decisions[inp.dataset.dec] = inp.value || "تمت المراجعة";
    else if (inp.value.trim()) decisions[inp.dataset.dec] = inp.value.trim();
  });
  const btn = $("#posDone");
  if (btn.disabled) return;                     // ignore double clicks
  btn.disabled = true;
  SALE.request_id = SALE.request_id || newKey(); // same id for every retry of this payment
  try {
    const done = await api(`/api/sales/${SALE.id}/complete`, { method: "POST",
      body: { paid_cash: cash, paid_card: card, on_credit: credit, safety_decisions: decisions,
              version: SALE.version, request_id: SALE.request_id } });
    // thermal printer + drawer through the PC; if not configured or offline, print from the browser
    try { await api(`/api/print/receipt/${done.id}`, { method: "POST", body: { open_drawer: cash > 0 } }); }
    catch (pe) { window.open(`/receipt/${done.id}`, "_blank"); if (pe.code === "print_failed") toast(pe.message, true); }
    toast(`تم البيع — فاتورة رقم ${done.sale_no}`);
    SALE = null; viewPos();
  } catch (er) {
    toast(er.message, true);
    btn.disabled = false;
    if (er.code === "safety_unresolved") renderCart(er.details.findings);
    if (er.code === "not_draft") { SALE = await api(`/api/sales/${SALE.id}`); renderCart(); }
    if (er.code === "no_open_shift") $("#posShift").click();
  }
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
route();
