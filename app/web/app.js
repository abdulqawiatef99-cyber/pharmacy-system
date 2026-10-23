// Pharmacy platform PWA — no build step, no external libraries.
// All text inserted into HTML goes through esc() to prevent XSS.

const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
let ME = null;

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (p) => (p == null ? "" : (p / 100).toFixed(2));
const toPiasters = (v) => (v === "" || v == null ? null : Math.round(parseFloat(v) * 100));
const pct = (bp) => (bp == null ? "" : (bp / 100).toString());
const toBp = (v) => (v === "" || v == null ? null : Math.round(parseFloat(v) * 100));

function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = isError ? "error" : "";
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), isError ? 7000 : 3000);
}

async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: { "X-Requested-With": "pharm" }, credentials: "same-origin" };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
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
  [/^#\/purchases$/, viewPurchases],
  [/^#\/purchases\/([\w-]+)$/, viewInvoice],
  [/^#\/items$/, viewItems],
];

async function route() {
  const hash = location.hash || "#/purchases";
  if (!ME && hash !== "#/login") {
    try { ME = await api("/api/me"); } catch { location.hash = "#/login"; return; }
  }
  $("#topbar").hidden = !ME;
  if (ME) $("#who").textContent = `${ME.user.full_name} — ${ME.organization.name}`;
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
        ${["total_mismatch", "line_total_mismatch", "net_price_mismatch"].includes(i.code) && draft && ME.permissions.includes("purchases.approve")
          ? `<button class="btn small secondary" data-ack="${esc(i.code)}" data-ackline="${esc(i.line_id || "")}">قبول الفرق مع ذكر السبب</button>` : ""}</li>`).join("")}
      ${warns.map((i) => `<li class="warn">${i.line_id ? `سطر ${lineNo[i.line_id]}: ` : ""}${esc(i.message)}
        ${i.code === "public_price_changed" ? ` (${money(i.details.old)} ← ${money(i.details.new)})` : ""}
        ${i.code === "short_expiry" ? ` (${i.details.days} يوم)` : ""}</li>`).join("")}
    </ul>
    <div class="row">
      ${draft ? `<button class="btn" id="approve" ${CUR.can_approve && ME.permissions.includes("purchases.approve") ? "" : "disabled"}>اعتماد الفاتورة وإضافتها للمخزون</button>
        <button class="btn secondary" id="addLine">إضافة صنف</button>` : ""}
      ${inv.status !== "cancelled" ? `<button class="btn danger" id="cancelInv">إلغاء الفاتورة</button>` : ""}
    </div>
  </section>
  <div class="review">
    <section class="panel doc ${["image","pdf"].includes(inv.source_type) && fileUrl ? "" : "compact"}">${doc}</section>
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
  bonus_qty: (v) => (v === "" ? 0 : parseInt(v, 10)), batch_no: (v) => v, expiry_date: (v) => v,
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
    <h1>الأصناف</h1>
    <div class="row"><label class="f grow">بحث بالاسم أو الباركود
      <input id="q" placeholder="مثال: panadol أو بانادول أو 622..." autocomplete="off"></label></div>
  </section>
  <section class="panel"><div class="tablewrap"><table>
    <thead><tr><th>الاسم</th><th>التركيز</th><th>الشكل</th><th>العبوة</th><th>سعر الجمهور</th><th>الكود</th></tr></thead>
    <tbody id="itemsBody"></tbody></table></div></section>`;
  const load = async () => {
    const rows = await api(`/api/items?q=${encodeURIComponent($("#q").value)}&limit=50`);
    $("#itemsBody").innerHTML = rows.map((r) => `<tr><td>${esc(r.name_en || "")}<div class="raw">${esc(r.name_ar || "")}</div></td>
      <td>${esc(r.strength_norm || "")}</td><td>${esc(r.dosage_form || "")}</td>
      <td>${r.pack_size} ${esc(r.base_unit_name)}</td><td>${money(r.public_price)}</td><td>${esc(r.code || "")}</td></tr>`).join("")
      || `<tr><td colspan="6" class="muted">لا توجد نتائج</td></tr>`;
  };
  let t;
  $("#q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 250); });
  load();
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
route();
