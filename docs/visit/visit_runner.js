/* Visit runner for device visits (#18, #19, #20 and every manual update until #96).
 *
 * Paste this whole file into the browser tab that is logged in to the app at http://127.0.0.1:8000
 * (or fetch it and eval it). It defines window.visit; nothing runs on load.
 *
 *   await visit.pre()                 before an install/update: health, snapshot, backup  → keep r.snapshot
 *   await visit.post(snap, {version}) after it: new boot_id, every snapshot value unchanged
 *   await visit.flows()               API-checkable checklist rows with TEST- data (B9 #114, L2/L3 #116)
 *   await visit.print("test")         printer/drawer steps: returns NEEDS-OWNER with the question to ask
 *   await visit.printAudit(since)     B8(b): reads the audit for print/drawer outcomes since a time
 *   await visit.day19()               #19 end-of-day figures (DAY19_SCRIPT.md) against the app
 *   visit.help()                      this list
 *
 * Every phase returns {md, rows, ...}: md is a Markdown table (id | result | evidence) with results
 * PASS / FAIL / SKIP / NEEDS-OWNER / INFO. Each call is bounded (~25 s); a request never waits more than
 * its own timeout. Every request is same-origin with the header X-Requested-With: pharm.
 * Safety: no settings are saved, nothing is deleted, real records are only read; the only writes are
 * TEST- records (an item, a customer, credit sales and their returns) and, in visit.print, the printer
 * action asked for. Contract test: tests/test_visit_runner.py.
 */
(() => {
  "use strict";
  const VERSION = "1";
  const KEY = "visit.snapshot";
  const TAG = "TEST-VISIT";
  const PHASE_MS = 25000;

  // ------------------------------------------------------------------ plumbing
  async function call(method, path, body, opt = {}) {
    const headers = { ...(opt.headers || {}), "X-Requested-With": "pharm" };
    let payload;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), opt.timeout || 12000);
    const t0 = Date.now();
    try {
      const r = await fetch(path, { method, headers, body: payload, credentials: "same-origin", cache: "no-store",
                                    signal: ctl.signal });
      const text = await r.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
      const err = data && data.error ? data.error : null;
      return { ok: r.ok, status: r.status, data, text, code: err ? err.code : null, cache: r.headers.get("cache-control"),
               message: err ? err.message : null, ms: Date.now() - t0 };
    } catch (e) {
      return { ok: false, status: 0, data: null, text: "", code: e.name === "AbortError" ? "timeout" : "network",
               message: String(e && e.message || e), ms: Date.now() - t0 };
    } finally {
      clearTimeout(timer);
    }
  }

  const sleep = (ms) => new Promise((res) => setTimeout(res, ms));
  const today = () => { const d = new Date(); return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
  const money = (p) => (p == null ? "—" : (p / 100).toFixed(2));
  const cell = (v) => String(v == null ? "" : v).replace(/\|/g, "\\|").replace(/\n/g, " ");
  const fail = (r) => `HTTP ${r.status}${r.code ? " " + r.code : ""}${r.message ? ": " + r.message : ""}`;

  function hash(str) {            // cyrb53: a short, stable fingerprint (not security)
    let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
    for (let i = 0; i < str.length; i++) {
      const c = str.charCodeAt(i);
      h1 = Math.imul(h1 ^ c, 2654435761); h2 = Math.imul(h2 ^ c, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16).padStart(14, "0");
  }
  function stable(v) {            // JSON with sorted keys, so the fingerprint ignores key order
    if (Array.isArray(v)) return "[" + v.map(stable).join(",") + "]";
    if (v && typeof v === "object") return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + stable(v[k])).join(",") + "}";
    return JSON.stringify(v === undefined ? null : v);
  }
  const fp = (v) => hash(stable(v));
  const pick = (o, keys) => Object.fromEntries(keys.map((k) => [k, o ? o[k] : undefined]));

  function report(title, rows, extra = {}) {
    const md = [`**${title}** (visit runner v${VERSION}, ${new Date().toLocaleString()})`, "",
                "| id | result | evidence |", "|---|---|---|",
                ...rows.map((r) => `| ${cell(r.id)} | ${cell(r.result)} | ${cell(r.evidence)} |`)].join("\n");
    const counts = {};
    rows.forEach((r) => { counts[r.result] = (counts[r.result] || 0) + 1; });
    return { md, rows, counts, ...extra };
  }
  const row = (id, result, evidence) => ({ id, result, evidence });

  function deadline(ms = PHASE_MS) {
    const end = Date.now() + ms;
    return { left: () => end - Date.now(), over: () => Date.now() > end };
  }

  function save(snap) {
    try { localStorage.setItem(KEY, JSON.stringify(snap)); return true; } catch (e) { return false; }
  }
  function load() {
    try { const t = localStorage.getItem(KEY); return t ? JSON.parse(t) : null; } catch (e) { return null; }
  }

  // ------------------------------------------------------------------ snapshot (B2 / B5 / B13 / B14)
  const RECEIPT_KEYS = ["receipt_target", "paper", "copies", "auto_receipt", "auto_receipt_min", "drawer_on_cash",
                        "drawer_timing", "time_format", "label_target", "label_lang", "label_size", "label_dpi",
                        "label_gap", "label_shift", "label_fields", "label_a4"];
  // values an install or update must not change; audit/notifications/backups are expected to grow
  const MUST = ["schema", "channel", "rows.sales", "rows.sale_lines", "rows.purchase_invoices", "rows.stock_movements",
                "items.n", "items.fp", "customers.n", "customers.fp", "suppliers.n", "suppliers.fp", "purchases.n",
                "purchases.fp", "stock.value_cost", "stock.value_retail", "stock.items", "stock.expiring",
                "cash.open", "cash.shift_id", "cash.expected_cash", "cash.sales_count", "today.fp",
                "receipt.fp", ...RECEIPT_KEYS.map((k) => "receipt." + k), "backup_settings.fp", "sales_settings.fp",
                "ui_settings.fp"];
  const INFO = ["version", "installed", "boot_id", "rows.audit_log", "rows.notifications"];

  async function snapshot() {
    const [health, upd, storage, items, customers, suppliers, purchases, stock, cash, summ, receipt, backup, salesSet, ui, page] =
      await Promise.all([
        call("GET", "/api/health"), call("GET", "/api/system/update", undefined, { timeout: 15000 }),
        call("GET", "/api/system/storage"), call("GET", "/api/items?limit=200"), call("GET", "/api/customers/all"),
        call("GET", "/api/suppliers/all"), call("GET", "/api/purchases"), call("GET", "/api/reports/stock"),
        call("GET", "/api/cash/current"), call("GET", "/api/sales/summary"), call("GET", "/api/settings/receipt"),
        call("GET", "/api/backup"), call("GET", "/api/settings/sales"), call("GET", "/api/settings/ui"), call("GET", "/")]);
    const errors = {};
    const need = { health, storage, items, customers, suppliers, purchases, stock, receipt, backup };
    Object.entries(need).forEach(([k, r]) => { if (!r.ok) errors[k] = fail(r); });
    if (!upd.ok) errors.update = fail(upd);
    if (!cash.ok && cash.status !== 404) errors.cash = fail(cash);
    const h = health.data || {}, u = upd.data || {}, rows = (storage.data || {}).rows || {};
    const itemList = Array.isArray(items.data) ? items.data : [];
    const custList = (customers.data || {}).customers || [];
    const supList = (suppliers.data || {}).suppliers || [];
    const purList = Array.isArray(purchases.data) ? purchases.data : ((purchases.data || {}).invoices || []);
    const st = stock.data || {}, c = cash.ok ? cash.data || {} : {}, rc = receipt.data || {}, bk = backup.data || {};
    const snap = {
      runner: VERSION, at: new Date().toISOString(), version: h.version, schema: h.schema, boot_id: h.boot_id,
      installed: u.installed || null, channel: u.channel === undefined ? null : u.channel,
      update_available: u.available === undefined ? null : u.available,
      rows: pick(rows, ["sales", "sale_lines", "purchase_invoices", "stock_movements", "audit_log", "notifications"]),
      items: { n: itemList.length, capped: itemList.length >= 200,
               fp: fp(itemList.map((i) => [i.id, i.public_price, i.pack_size, i.stock_qty, i.status]).sort()) },
      customers: { n: custList.length, fp: fp(custList.map((x) => [x.id, x.name, x.balance, x.credit_limit]).sort()) },
      suppliers: { n: supList.length, fp: fp(supList.map((x) => [x.id, x.name, x.balance === undefined ? null : x.balance]).sort()) },
      purchases: { n: purList.length, capped: purList.length >= 50,
                   fp: fp(purList.map((x) => [x.id, x.status, x.declared_total, x.computed_total, x.lines]).sort()) },
      stock: { value_cost: st.value_cost === undefined ? null : st.value_cost, value_retail: st.value_retail === undefined ? null : st.value_retail,
               items: (st.value_by_category || []).reduce((t, r) => t + (r.items || 0), 0), expiring: (st.expiring || []).length },
      cash: { open: cash.ok, shift_id: c.id || c.shift_id || null, expected_cash: c.expected_cash === undefined ? null : c.expected_cash,
              sales_count: c.sales_count === undefined ? null : c.sales_count },
      today: { fp: fp(summ.data) },
      receipt: { fp: fp(rc), ...pick(rc, RECEIPT_KEYS) },
      backup_settings: { fp: fp(bk.settings || null), folder: bk.folder || null },
      sales_settings: { fp: fp(salesSet.data) },
      ui_settings: { fp: fp(ui.data) },
      page: { cache: page.cache || null, app_js: ((page.text || "").match(/app\.js\?v=([\w.-]+)/) || [])[1] || null },
      errors,
    };
    return snap;
  }

  function get(o, path) { return path.split(".").reduce((v, k) => (v == null ? undefined : v[k]), o); }

  // ------------------------------------------------------------------ phases
  async function pre(opt = {}) {
    const dl = deadline();
    const rows = [];
    const snap = await snapshot();
    rows.push(row("B2 health", snap.version ? "PASS" : "FAIL",
                  `version ${snap.version}, schema ${snap.schema}, boot_id ${snap.boot_id}, installed ${snap.installed}, channel ${snap.channel}`));
    Object.entries(snap.errors).forEach(([k, e]) => rows.push(row("B2 " + k, "FAIL", e)));
    rows.push(row("B2 counts", Object.keys(snap.errors).length ? "FAIL" : "PASS",
                  `sales ${snap.rows.sales}, lines ${snap.rows.sale_lines}, purchases ${snap.rows.purchase_invoices}, stock moves ${snap.rows.stock_movements}, ` +
                  `audit ${snap.rows.audit_log}; items ${snap.items.n}${snap.items.capped ? "+ (first 200)" : ""}, customers ${snap.customers.n}, suppliers ${snap.suppliers.n}`));
    rows.push(row("B2 stock", "INFO", `cost ${money(snap.stock.value_cost)}, retail ${money(snap.stock.value_retail)}, items in stock ${snap.stock.items}, expiring ${snap.stock.expiring}`));
    rows.push(row("B2 cash", "INFO", snap.cash.open ? `shift open, expected ${money(snap.cash.expected_cash)}, invoices ${snap.cash.sales_count}` : "no open shift"));
    rows.push(row("B2 settings", "INFO", `receipt ${snap.receipt.receipt_target || "—"}, labels ${snap.receipt.label_lang}/${snap.receipt.label_target || "—"} ` +
                  `${snap.receipt.label_size} gap ${snap.receipt.label_gap} shift ${snap.receipt.label_shift}, drawer ${snap.receipt.drawer_timing}, update channel ${snap.channel}`));
    if (opt.backup !== false) {
      const b = await backup(Math.max(3000, dl.left() - 1000));
      snap.backup = b.backup;
      rows.push(...b.rows);
    }
    snap.stored = save(snap);
    rows.push(row("snapshot", snap.stored ? "PASS" : "INFO", snap.stored ? `saved in localStorage "${KEY}"; also returned as r.snapshot` : "localStorage unavailable: keep r.snapshot"));
    return report("visit.pre", rows, { snapshot: snap, json: JSON.stringify(snap) });
  }

  async function backup(timeout = 24000) {
    const before = await call("GET", "/api/backup");
    const known = new Set(((before.data || {}).files || []).map((f) => f.name));
    const r = await call("POST", "/api/backup/run", {}, { timeout });
    if (!r.ok) {
      const why = r.code === "timeout" ? "still running after the time limit: call visit.backupStatus() in a minute" : fail(r);
      return { backup: { ok: false, error: why }, rows: [row("B1 backup", r.code === "timeout" ? "SKIP" : "FAIL", why)] };
    }
    const d = r.data || {};
    const files = d.files || [];
    const f = files.find((x) => !known.has(x.name)) || files[0] || {};
    const ok = !!(d.state && d.state.last_ok) && !known.has(f.name);
    const b = { ok, name: f.name || null, bytes: f.bytes || null, folder: d.folder || null };
    return { backup: b, rows: [row("B1 backup", ok ? "PASS" : "FAIL",
      `${b.name} ${b.bytes} bytes, last_ok ${d.state && d.state.last_ok}, folder ${b.folder}; sha256: Get-FileHash on the PC; USB copy: owner`)] };
  }

  async function backupStatus() {
    const r = await call("GET", "/api/backup");
    const d = r.data || {}, f = (d.files || [])[0] || {};
    return report("visit.backupStatus", [row("B1 backup", r.ok && d.state && d.state.last_ok ? "PASS" : "FAIL",
      r.ok ? `newest ${f.name} ${f.bytes} bytes at ${f.at}, last_ok ${d.state && d.state.last_ok}, error ${d.state && d.state.last_error || "—"}` : fail(r))]);
  }

  async function post(snap, opt = {}) {
    if (typeof snap === "string") { try { snap = JSON.parse(snap); } catch (e) { snap = null; } }
    snap = snap || load();
    if (!snap) return report("visit.post", [row("B5", "FAIL", "no snapshot: pass the r.snapshot from visit.pre()")]);
    const now = await snapshot();
    const rows = [];
    const sameBoot = now.boot_id && now.boot_id === snap.boot_id;
    rows.push(row("B5 boot_id", opt.sameBoot ? (sameBoot ? "PASS" : "FAIL") : (now.boot_id && !sameBoot ? "PASS" : "FAIL"),
                  `${snap.boot_id} → ${now.boot_id}${opt.sameBoot ? " (expected the same)" : " (expected a new one)"}`));
    rows.push(row("B5 version", opt.version ? (now.version === opt.version ? "PASS" : "FAIL") : "INFO",
                  `${snap.version} → ${now.version}${opt.version ? ", expected " + opt.version : ""}`));
    rows.push(row("B5 installed", opt.installed ? (String(now.installed || "").startsWith(opt.installed) ? "PASS" : "FAIL") : "INFO",
                  `${snap.installed} → ${now.installed}${opt.installed ? ", expected " + opt.installed : ""}`));
    rows.push(row("B6 channel", now.channel === snap.channel && (!opt.channel || now.channel === opt.channel) ? "PASS" : "FAIL",
                  `${snap.channel} → ${now.channel}${opt.channel ? ", expected " + opt.channel : ""}`));
    rows.push(row("B5 page", now.page.cache === "no-cache" && now.page.app_js ? "PASS" : "FAIL",
                  `/ Cache-Control ${now.page.cache}; app.js?v= ${snap.page ? snap.page.app_js : "?"} → ${now.page.app_js}`));
    if (opt.audit !== undefined) {
      const d = (now.rows.audit_log || 0) - (snap.rows.audit_log || 0);
      rows.push(row("B13 audit", d === opt.audit ? "PASS" : "FAIL", `audit rows +${d}, expected +${opt.audit}`));
    }
    if (opt.noUpdate) rows.push(row("B6 no update", now.update_available === false ? "PASS" : "FAIL", `available ${now.update_available}`));
    Object.entries(now.errors).forEach(([k, e]) => rows.push(row("B5 " + k, "FAIL", e)));
    let diffs = 0;
    MUST.filter((k) => k !== "channel").forEach((k) => {
      const a = get(snap, k), b = get(now, k);
      if (stable(a) !== stable(b)) { diffs++; rows.push(row("B5 " + k, "FAIL", `${stable(a)} → ${stable(b)}`)); }
    });
    rows.push(row("B5 data", diffs ? "FAIL" : "PASS", diffs ? `${diffs} value(s) changed (rows above)` : `all ${MUST.length - 1} values unchanged (counts, totals, cash, settings)`));
    rows.push(row("B14 settings", RECEIPT_KEYS.every((k) => stable(snap.receipt[k]) === stable(now.receipt[k])) && snap.receipt.fp === now.receipt.fp ? "PASS" : "FAIL",
                  `receipt ${now.receipt.receipt_target || "—"}, labels ${now.receipt.label_target || "—"} shift ${now.receipt.label_shift}, drawer ${now.receipt.drawer_timing}`));
    INFO.slice(3).forEach((k) => rows.push(row("info " + k, "INFO", `${get(snap, k)} → ${get(now, k)}`)));
    return report("visit.post", rows, { snapshot: now, json: JSON.stringify(now) });
  }

  // ------------------------------------------------------------------ TEST- fixtures
  async function testItem() {
    const q = await call("GET", "/api/items?limit=20&q=" + encodeURIComponent(TAG));
    let it = (Array.isArray(q.data) ? q.data : []).find((i) => (i.name_en || "").startsWith(TAG + " item"));
    if (!it) {
      const c = await call("POST", "/api/items", { item: { name_en: TAG + " item (synthetic)", pack_size: 3, public_price: 3000 } });
      if (!c.ok) throw new Error("TEST item: " + fail(c));
      it = { ...c.data, stock_qty: 0 };
    }
    if ((it.stock_qty || 0) < 6) {
      const a = await call("POST", `/api/items/${it.id}/adjust`, { qty_delta: 30, reason: "found", note: "TEST visit runner (synthetic)" });
      if (!a.ok) throw new Error("TEST stock: " + fail(a));
    }
    return it;
  }

  async function testCustomer() {
    const q = await call("GET", "/api/customers?q=" + encodeURIComponent(TAG));
    let c = (Array.isArray(q.data) ? q.data : []).find((x) => (x.name || "").startsWith(TAG));
    if (!c) {
      const r = await call("POST", "/api/customers", { name: TAG + " عميل تجربة", credit_limit: 500000, notes: "visit runner, synthetic" });
      if (!r.ok) throw new Error("TEST customer: " + fail(r));
      c = { id: r.data.id };
    }
    return c;
  }

  async function creditSale(item, cust, tag) {
    const s = await call("POST", "/api/sales", { customer_id: cust.id });
    if (!s.ok) throw new Error("sale: " + fail(s));
    const l = await call("POST", `/api/sales/${s.data.id}/lines`, { item_id: item.id, units: 3 });
    if (!l.ok) throw new Error("sale line: " + fail(l));
    const done = await call("POST", `/api/sales/${s.data.id}/complete`,
                            { on_credit: l.data.total, version: l.data.version, request_id: `${TAG}-${tag}-${Date.now()}` });
    if (!done.ok) throw new Error("sale complete: " + fail(done));
    return done.data;
  }

  // ------------------------------------------------------------------ flows (B9 #114, L2/L3 #116)
  async function flows(opt = {}) {
    const dl = deadline();
    const rows = [];
    let item, cust;
    try {
      item = await testItem();
      cust = await testCustomer();
    } catch (e) {
      return report("visit.flows", [row("setup", "FAIL", e.message)]);
    }
    if (opt.ui !== false) rows.push(...await flowReturnUI(item, cust, dl));
    if (!dl.over()) rows.push(...await flowReturnRetry(item, cust));
    else rows.push(row("B9 retry", "SKIP", "time limit: run visit.flows({ui:false})"));
    if (!dl.over()) rows.push(...await flowEstock());
    else rows.push(row("L2/L3", "SKIP", "time limit: run visit.estock()"));
    return report("visit.flows", rows);
  }

  // #114 on the real invoice screen: default refund = the sale's own payment; a double click posts one return
  async function flowReturnUI(item, cust, dl) {
    const rows = [];
    if (typeof location === "undefined" || !document.getElementById("app") && !document.querySelector("main")) {
      return [row("B9 UI", "SKIP", "not in the app page")];
    }
    let sale;
    try { sale = await creditSale(item, cust, "ui"); } catch (e) { return [row("B9 UI", "FAIL", e.message)]; }
    const back = location.hash;
    location.hash = `#/sales/${sale.id}`;
    let sel = null;
    for (let i = 0; i < 60 && !sel; i++) { await sleep(100); sel = document.getElementById("rMethod"); }
    if (!sel) { location.hash = back; return [row("B9 UI", "FAIL", `invoice ${sale.sale_no} screen did not show the return form`)]; }
    rows.push(row("B9 default", sel.value === "credit" ? "PASS" : "FAIL",
                  `TEST credit sale no. ${sale.sale_no} (${money(sale.total)} آجل): refund method preselected "${sel.value}", expected "credit"`));
    const qty = document.querySelector("[data-ret]");
    const reason = document.getElementById("rReason");
    const go = document.getElementById("rGo");
    qty.value = "1";
    reason.value = "TEST visit runner";
    go.click(); go.click();                      // a fast double click
    await sleep(60); go.click();                 // and a late third click while the first is saving
    let s = null;
    for (let i = 0; i < 30; i++) {
      await sleep(200);
      const r = await call("GET", `/api/sales/${sale.id}`);
      if (r.ok && r.data.returns.length) { s = r.data; if (i > 4) break; }
      if (dl.over()) break;
    }
    const n = s ? s.returns.length : 0;
    rows.push(row("B9 double click", n === 1 ? "PASS" : "FAIL",
                  `returns recorded: ${n}${s ? `, method ${s.returns.map((r) => r.refund_method).join(",")}, total ${money(s.returned_total)}` : ""}`));
    location.hash = back || "#/";
    return rows;
  }

  // the same return sent twice with one Idempotency-Key (a retry after a dropped reply) is recorded once
  async function flowReturnRetry(item, cust) {
    let sale;
    try { sale = await creditSale(item, cust, "retry"); } catch (e) { return [row("B9 retry", "FAIL", e.message)]; }
    const key = `${TAG}-ret-${Date.now()}`;
    const body = { lines: { [sale.lines[0].id]: 1 }, refund_method: "credit", reason: "TEST visit runner retry" };
    const [a, b] = await Promise.all([1, 2].map(() => call("POST", `/api/sales/${sale.id}/return`, body, { headers: { "Idempotency-Key": key } })));
    const g = await call("GET", `/api/sales/${sale.id}`);
    const n = g.ok ? g.data.returns.length : -1;
    return [row("B9 retry", n === 1 ? "PASS" : "FAIL",
                `TEST sale no. ${sale.sale_no}: two posts with one key → HTTP ${a.status}/${b.status}, returns recorded ${n}`)];
  }

  // #116: a cut-off E-Stock stock export is refused; the same file complete is accepted (single store)
  async function flowEstock() {
    const rows = [];
    const header = ["TEST-مخزن", "اسم المخزن", 1, "كود المخزن", "نسبة الربح", "إجمالى البيع", "إجمالى التكلفة",
                    "البيع", "التكلفة", "الكمية", "تاريخ الصلاحية", "الوحدة", "الشركة", "اسم الصنف", "الكود"];
    const data = [[10, 80, 64, 40, 32, 2, null, "علبة", "TEST", TAG + " estock item", 990001],
                  [5, 30, 24, 30, 24, 1, null, "علبة", "TEST", TAG + " estock item 2", 990002]];
    const total = [110, "إجمالى البيع", 88, "إجمالى التكلفة", 2, "عدد الاصناف"];
    const grand = ["إجمالى جميع المخازن", 110, "إجمالى البيع", 88, "إجمالى التكلفة", 2, "عدد الاصناف"];
    const send = (sheet, name) => {
      const fd = new FormData();
      fd.append("file", new Blob([xlsx(sheet)], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }), name);
      fd.append("kind", "opening_stock");
      fd.append("source_system", "estock");
      return call("POST", "/api/imports", fd, { timeout: 15000 });
    };
    const cut = await send([header, ...data], "TEST-estock-cut.xlsx");
    rows.push(row("L2 cut-off file", !cut.ok && cut.code === "estock_totals_mismatch" ? "PASS" : "FAIL",
                  cut.ok ? "accepted (expected a refusal)" : `${cut.status} ${cut.code}: ${cut.message}`));
    const full = await send([header, ...data, total, grand], "TEST-estock-full.xlsx");
    const run = full.data || {};
    const runId = run.id || (run.run && run.run.id);
    rows.push(row("L3 complete single-store file", full.ok ? "PASS" : "FAIL",
                  full.ok ? `accepted for review (import ${runId}, not applied)` : `${full.status} ${full.code}: ${full.message}`));
    if (full.ok && runId) {        // the analysis is only a draft; mark it discarded so nobody applies it
      const d = await call("POST", `/api/imports/${runId}/discard`, {});
      rows.push(row("L3 cleanup", d.ok ? "INFO" : "FAIL", d.ok ? "the TEST import draft was marked discarded (nothing was imported)" : fail(d)));
    }
    return rows;
  }

  // ------------------------------------------------------------------ minimal .xlsx writer (stored zip)
  function xlsx(rows) {
    const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const col = (i) => { let s = ""; i++; while (i) { const m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = (i - m - 1) / 26; } return s; };
    const sheet = rows.map((r, ri) => `<row r="${ri + 1}">` + r.map((v, ci) => {
      if (v == null) return "";
      const ref = col(ci) + (ri + 1);
      return typeof v === "number" ? `<c r="${ref}"><v>${v}</v></c>` : `<c r="${ref}" t="inlineStr"><is><t>${esc(v)}</t></is></c>`;
    }).join("") + "</row>").join("");
    const files = {
      "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' +
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
      "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
      "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' +
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
      "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
      "xl/worksheets/sheet1.xml": '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
        `<sheetData>${sheet}</sheetData></worksheet>`,
    };
    return zip(files);
  }

  function crc32(bytes) {
    let c, crc = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) {
      c = (crc ^ bytes[i]) & 0xff;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crc = (crc >>> 8) ^ c;
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  function zip(files) {
    const enc = new TextEncoder();
    const parts = [], central = [];
    let offset = 0;
    const u16 = (v) => [v & 0xff, (v >>> 8) & 0xff];
    const u32 = (v) => [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff];
    Object.entries(files).forEach(([name, text]) => {
      const nm = enc.encode(name), data = enc.encode(text), crc = crc32(data);
      const common = [...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(0x21), ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(nm.length), ...u16(0)];
      const local = new Uint8Array([...u32(0x04034b50), ...common, ...nm]);
      parts.push(local, data);
      central.push(new Uint8Array([...u32(0x02014b50), ...u16(20), ...common, ...u16(0), ...u16(0), ...u16(0), ...u32(0), ...u32(offset), ...nm]));
      offset += local.length + data.length;
    });
    const size = central.reduce((t, c) => t + c.length, 0);
    const end = new Uint8Array([...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(central.length), ...u16(central.length), ...u32(size), ...u32(offset), ...u16(0)]);
    const all = [...parts, ...central, end];
    const out = new Uint8Array(all.reduce((t, p) => t + p.length, 0));
    let o = 0;
    all.forEach((p) => { out.set(p, o); o += p.length; });
    return out;
  }

  async function estock() { return report("visit.estock", await flowEstock()); }

  // ------------------------------------------------------------------ printer and drawer (NEEDS-OWNER)
  const PRINT = {
    test: { id: "B8a test page", ask: "طلعت ورقة تجربة من طابعة الإيصالات؟ والكلام العربي مقروء؟",
            run: (o) => call("POST", "/api/print/test", o.target ? { target: o.target } : {}, { timeout: 20000 }) },
    drawer: { id: "B8d drawer", ask: "الدرج اتفتح؟",
              run: () => call("POST", "/api/print/drawer", { reason: "other", note: "TEST visit runner: drawer check" }, { timeout: 20000 }) },
    labels: { id: "B7 labels", ask: "كام ملصق طلع؟ وكل ملصق كامل ومفيش ملصق فاضي بينهم؟ والطباعة في نص الملصق ولا نازلة لتحت أو طالعة لفوق؟",
              run: (o) => call("POST", "/api/print/label-test", pick(o, ["label_shift", "label_gap", "label_size", "label_dpi", "label_lang", "label_target"]), { timeout: 20000 }) },
    reprint: { id: "B8c reprint", ask: "طلع إيصال واحد بس؟ والدرج فضل مقفول؟",
               run: async (o) => {
                 let id = o.sale_id;
                 if (!id) {
                   const l = await call("GET", "/api/sales?day=" + today());
                   const rowsL = Array.isArray(l.data) ? l.data : ((l.data || {}).sales || []);
                   id = rowsL.length ? rowsL[0].id : null;
                 }
                 return id ? call("POST", `/api/print/receipt/${id}`, {}, { timeout: 20000 })
                           : { ok: false, status: 0, code: "no_sale", message: "no sale today to reprint" };
               } },
    sale: { id: "B8a cash sale", ask: "طلع إيصال واحد كامل؟ والدرج اتفتح مرة واحدة بعد الإيصال؟",
            run: async (o) => {
              if (!o.confirm) return { ok: false, status: 0, code: "confirm", message: "adds a 30.00 TEST cash sale to the open shift: call visit.print('sale', {confirm: true})" };
              const item = await testItem();
              const s = await call("POST", "/api/sales", {});
              if (!s.ok) return s;
              const l = await call("POST", `/api/sales/${s.data.id}/lines`, { item_id: item.id, units: 3 });
              if (!l.ok) return l;
              return call("POST", `/api/sales/${s.data.id}/complete`, { paid_cash: l.data.total, version: l.data.version, receipt: true,
                                                                       request_id: `${TAG}-cash-${Date.now()}` }, { timeout: 20000 });
            } },
  };

  async function print(step, opt = {}) {
    const p = PRINT[step];
    if (!p) {
      return report("visit.print", Object.entries(PRINT).map(([k, v]) => row(v.id, "SKIP", `visit.print("${k}") then ask: ${v.ask}`)));
    }
    let r;
    try { r = await p.run(opt); } catch (e) { r = { ok: false, status: 0, message: e.message }; }
    const dev = r.ok && r.data && r.data.devices ? ` devices ${JSON.stringify(r.data.devices)}` : "";
    const res = r.ok ? row(p.id, "NEEDS-OWNER", `sent (HTTP ${r.status}${dev}). اسأل المالك: «${p.ask}»`)
      : row(p.id, r.code === "confirm" ? "SKIP" : "FAIL", `${fail(r)}${r.status ? ". اسأل المالك: «إيه اللي ظهر على الشاشة؟»" : ""}`);
    return report("visit.print " + step, [res], { response: r.data });
  }

  // B8(b): after the owner sells with the printer switched off, the audit must show both failures
  async function printAudit(since) {
    const d = today();
    const r = await call("GET", `/api/audit?from=${d}&to=${d}`);
    if (!r.ok) return report("visit.printAudit", [row("B8b audit", "FAIL", fail(r))]);
    const t0 = since ? new Date(since).getTime() : 0;
    const hits = ((r.data || {}).rows || []).filter((x) => /^(print\.|cash\.drawer)/.test(x.action) && new Date(x.at).getTime() >= t0);
    const out = (x) => { try { return JSON.parse(x.after_json || "{}").outcome || ""; } catch (e) { return ""; } };
    const notPrinted = hits.some((x) => x.action === "print.receipt" && out(x) === "not_printed");
    const drawerFailed = hits.some((x) => x.action === "cash.drawer_auto" && out(x) === "failed");
    const rows = [row("B8b audit", notPrinted && drawerFailed ? "PASS" : "FAIL",
                      `print.receipt not_printed: ${notPrinted}; cash.drawer_auto failed: ${drawerFailed}`)];
    hits.slice(0, 12).forEach((x) => rows.push(row("audit", "INFO", `${x.at} ${x.action} ${out(x)} ${x.user_name || ""}`)));
    rows.push(row("B8b queue", "NEEDS-OWNER", "Get-PrintJob -PrinterName POS-80C must be empty; اسأل المالك بعد ما يشغّل الطابعة: «طبعت حاجة لوحدها؟ والدرج اتفتح لوحده؟»"));
    return report("visit.printAudit", rows);
  }

  // ------------------------------------------------------------------ #19 end-of-day figures (DAY19_SCRIPT.md)
  async function day19(opt = {}) {
    const d = opt.day || today();
    const names = { a: "صنف تجربة أ", b: "صنف تجربة ب", cust: "عميل تجربة", sup: "مورد تجربة" };
    const [cash, rep, ia, ib, cu, su] = await Promise.all([
      call("GET", "/api/cash/current"), call("GET", `/api/reports/sales?from=${d}&to=${d}`),
      call("GET", "/api/items?limit=20&q=" + encodeURIComponent(names.a)), call("GET", "/api/items?limit=20&q=" + encodeURIComponent(names.b)),
      call("GET", "/api/customers?q=" + encodeURIComponent(names.cust)), call("GET", "/api/suppliers/all")]);
    const rows = [];
    const eq = (id, got, want, what, isMoney = true) => rows.push(row(id, got === want ? "PASS" : "FAIL",
      `${what}: ${isMoney ? money(got) : got}, expected ${isMoney ? money(want) : want}`));
    let shift = cash.ok ? cash.data : null;
    if (!shift) {
      const list = await call("GET", "/api/cash/shifts");
      const last = ((list.data || {}).shifts || [])[0];
      if (last) { const rr = await call("GET", `/api/cash/shifts/${last.id}`); shift = rr.ok ? rr.data : null; }
    }
    if (shift) {
      eq("D-expected", shift.expected_cash, 17066, "cash expected at close");
      const k = shift.cash_by_kind || {};
      eq("D-cash-sales", k.sale, 7600, "cash sales");
      eq("D-refunds", -(k.refund || 0), 1034, "cash refunds");
      eq("D-collections", k.customer_payment, 2000, "collections");
      eq("D-expenses", -(k.payout || 0), 1500, "expenses from the drawer");
      if (shift.credit_total !== undefined) eq("D-credit", shift.credit_total, 5000, "credit");
      if (shift.sales_count !== undefined) eq("D-shift-count", shift.sales_count, 3, "invoices in the shift", false);
      if (shift.status === "closed") rows.push(row("D-close", shift.difference === 0 ? "PASS" : "FAIL", `difference ${money(shift.difference)}`));
    } else rows.push(row("D-shift", "FAIL", "no open or recent shift"));
    const one = (r, n) => (Array.isArray(r.data) ? r.data : []).find((x) => (x.name_ar || x.name || "") === n);
    const a = one(ia, names.a), b = one(ib, names.b), c = (Array.isArray(cu.data) ? cu.data : []).find((x) => x.name === names.cust);
    const s = ((su.data || {}).suppliers || []).find((x) => x.name === names.sup);
    a ? eq("D-stock-a", a.stock_qty, 33, "stock of A (strips)", false) : rows.push(row("D-stock-a", "FAIL", `item "${names.a}" not found`));
    b ? eq("D-stock-b", b.stock_qty, 3, "stock of B", false) : rows.push(row("D-stock-b", "FAIL", `item "${names.b}" not found`));
    c ? eq("D-customer", c.balance, 3000, "customer balance") : rows.push(row("D-customer", "FAIL", `customer "${names.cust}" not found`));
    s ? eq("D-supplier", s.balance, 30000, "supplier balance") : rows.push(row("D-supplier", "FAIL", `supplier "${names.sup}" not found`));
    const t = (rep.data || {}).totals;
    if (t) {
      eq("D-count", t.n, 3, "sales report invoices", false);
      eq("D-total", t.total, 12600, "sales report total");
      eq("D-returns", t.returns, 1034, "sales report returns");
      eq("D-discount", t.discount, 500, "sales report discounts");
    } else rows.push(row("D-report", "FAIL", rep.ok ? "no totals" : fail(rep)));
    return report("visit.day19 " + d, rows);
  }

  function help() {
    return ["visit.pre() → keep r.snapshot (also in localStorage)", "visit.post(snapshot, {version, installed, channel: 'develop', noUpdate: true, audit: 1 (B13), sameBoot})",
            "visit.flows()  (or visit.flows({ui:false}), visit.estock())", "visit.print() lists the printer steps; visit.print('test'|'drawer'|'labels'|'reprint'|'sale')",
            "visit.printAudit(sinceIso)", "visit.backupStatus()", "visit.day19()", "print a phase's table: (await visit.pre()).md"].join("\n");
  }

  window.visit = { version: VERSION, pre, post, flows, estock, print, printAudit, backup: async () => report("visit.backup", (await backup()).rows),
                   backupStatus, day19, snapshot, help, load };
  return "visit runner v" + VERSION + " ready: " + help();
})();
