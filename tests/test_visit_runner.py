"""docs/visit/visit_runner.js: the snippet the coordinator evaluates in the app tab on device visits (#18).

Static contract (always runs): every METHOD + /api/ path the runner calls exists in the route table of
app/api/app.py, so renaming a route breaks CI instead of a visit; every request goes through one fetch that
sends X-Requested-With: pharm with same-origin cookies; no step deletes data or saves settings.
With Node: a syntax check. With a browser (CI): the phases run against a real server on synthetic data.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from tests.helpers import OWNER_PW, needs_browser
from tests.test_api import APICase, H

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "docs" / "visit" / "visit_runner.js"
SRC = RUNNER.read_text(encoding="utf-8")
APP = (ROOT / "app" / "api" / "app.py").read_text(encoding="utf-8")

CALL = re.compile(r"""\bcall\(\s*"([A-Z]+)"\s*,\s*(["`])(.*?)\2""", re.S)
ROUTE = re.compile(r"""Route\("([^"]+)",\s*\w+(?:,\s*methods=\[([^\]]*)\])?\)""")


def route_table() -> list[tuple[str, re.Pattern]]:
    out = []
    for path, methods in ROUTE.findall(APP):
        rx = re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$")
        for m in (re.findall(r'"([A-Z]+)"', methods) if methods else ["GET"]):
            out.append((m, rx))
    return out


def runner_calls() -> list[tuple[str, str]]:
    calls = []
    for method, _, path in CALL.findall(SRC):
        path = re.sub(r"\$\{[^}]*\}", "x1", path.split("?")[0])     # ${id} -> one path segment
        calls.append((method, path))
    return calls


class TestRunnerContract(unittest.TestCase):
    def test_every_call_matches_a_route(self):
        routes = route_table()
        self.assertGreater(len(routes), 150)          # the table parsed
        calls = runner_calls()
        self.assertGreater(len(calls), 25)
        missing = [(m, p) for m, p in calls if not any(m == rm and rx.match(p) for rm, rx in routes)]
        self.assertEqual(missing, [], "runner calls a route that app/api/app.py does not have")

    def test_the_route_parser_sees_known_routes(self):
        routes = route_table()
        for m, p in (("POST", "/api/backup/run"), ("GET", "/api/health"), ("POST", "/api/sales/x1/return"),
                     ("POST", "/api/imports"), ("POST", "/api/print/drawer")):
            self.assertTrue(any(m == rm and rx.match(p) for rm, rx in routes), (m, p))
        self.assertFalse(any(m == "GET" and rx.match("/api/backup/run") for m, rx in routes))

    def test_every_call_is_to_the_api(self):
        for m, p in runner_calls():
            self.assertTrue(p.startswith("/api/") or (m, p) == ("GET", "/"), p)     # "/" only for its cache header

    def test_one_fetch_with_the_csrf_header_and_same_origin_cookies(self):
        self.assertEqual(len(re.findall(r"\bfetch\(", SRC)), 1, "every request must go through call()")
        self.assertNotIn("XMLHttpRequest", SRC)
        self.assertNotIn("sendBeacon", SRC)
        body = SRC[SRC.index("async function call("):SRC.index("const sleep")]
        self.assertIn('"X-Requested-With": "pharm"', body)
        self.assertIn('credentials: "same-origin"', body)
        self.assertIn("fetch(path", body)
        # the CSRF header is set after the caller's headers, so no caller can drop or change it
        self.assertRegex(body, r'headers = \{ \.\.\.\(opt\.headers \|\| \{\}\), "X-Requested-With": "pharm" \}')
        self.assertEqual(set(re.findall(r'"X-Requested-With"\s*:\s*"([^"]*)"', SRC)), {"pharm"})
        self.assertNotRegex(SRC, r'opt\.headers\s*=')

    def test_no_step_deletes_data_or_saves_settings(self):
        calls = runner_calls()
        self.assertEqual(sorted({m for m, _ in calls}), ["GET", "POST"], "no DELETE, PUT or PATCH")
        forbidden = re.compile(r"/(void|cancel|restore|logout)$|^/api/system/|^/api/setup|^/api/users|^/api/settings/"
                               r"|/lines/x1$|^/api/backup$|^/api/cash/(open|close|movements)$|/apply$|/payments$")
        posts = [p for m, p in calls if m == "POST"]
        bad = [p for p in posts if forbidden.search(p)]
        self.assertEqual(bad, [])
        # the only status change is discarding the runner's own TEST import draft (it never touched stock)
        self.assertEqual({p for p in posts if p.endswith("/discard")}, {"/api/imports/x1/discard"})
        self.assertNotIn(".removeItem(", SRC)
        self.assertNotIn("localStorage.clear", SRC)

    def test_storage_is_wrapped(self):
        for m in re.finditer(r"localStorage\.\w+\(", SRC):
            line = SRC[SRC.rfind("\n", 0, m.start()):SRC.find("\n", m.start())]
            self.assertIn("try", line, line)

    def test_test_records_are_prefixed(self):
        self.assertIn('const TAG = "TEST-VISIT";', SRC)
        for m in re.finditer(r'call\("POST", "/api/(items|customers)", \{(.*?)\}\)', SRC, re.S):
            self.assertIn("TAG +", m.group(2))

    def test_checklist_names_the_runner_and_its_rows(self):
        doc = (ROOT / "docs" / "VISIT_18_CHECKLIST.md").read_text(encoding="utf-8")
        self.assertIn("docs/visit/visit_runner.js", doc)
        ids = set(re.findall(r'(?:row\(|id: )"([A-Z]\d+)[a-z]?[ "]', SRC))
        self.assertTrue({"B1", "B2", "B5", "B6", "B7", "B9", "B14", "L2", "L3"} <= ids, ids)
        for i in ids:
            self.assertRegex(doc, rf"\| {i} \|", f"row {i} is not in the checklist")


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestRunnerSyntax(unittest.TestCase):
    def test_node_parses_it(self):
        r = subprocess.run([shutil.which("node"), "--check", str(RUNNER)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_xlsx_writer_output_is_read_by_the_estock_importer(self):
        """The cut-off / complete E-Stock files the runner builds in the browser are real .xlsx files."""
        import io

        import openpyxl

        from app.core.errors import ValidationFailed
        from app.dataimport import estock
        js = SRC[SRC.index("  function xlsx(rows)"):SRC.index("  async function estock()")]
        flow = SRC[SRC.index("    const header = ["):SRC.index("    const send = ")]
        prog = ("const TAG='TEST-VISIT';\n" + js + "\n(function(){\n" + flow +
                "process.stdout.write(JSON.stringify({cut: Array.from(xlsx([header, ...data])),"
                " full: Array.from(xlsx([header, ...data, total, grand]))}));})();")
        r = subprocess.run([shutil.which("node"), "-e", prog], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)

        def rows(b):
            wb = openpyxl.load_workbook(io.BytesIO(bytes(b)))
            return [list(x) for x in wb.active.iter_rows(values_only=True)]
        full = rows(out["full"])
        self.assertEqual(estock.detect(full), "stock_by_expiry")
        table, summary = estock.parse_stock(full)
        self.assertEqual(len(table), 3)
        self.assertEqual(summary["stores"]["TEST-مخزن"]["sale"], "110.00")
        with self.assertRaises(ValidationFailed) as e:
            estock.parse_stock(rows(out["cut"]))
        self.assertEqual(e.exception.code, "estock_totals_mismatch")


DRIVER = r"""
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const [base, user, pw, runnerPath, plan] = process.argv.slice(1);
  const src = fs.readFileSync(runnerPath, 'utf8');
  const b = await chromium.launch();
  const p = await b.newPage();
  const errors = [];
  p.on('pageerror', (e) => errors.push(String(e)));
  await p.goto(base + '/');
  await p.waitForSelector('#loginForm');
  await p.evaluate(async ([u, w]) => fetch('/api/auth/login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ username: u, password: w }) }), [user, pw]);
  await p.goto(base + '/#/');
  await p.waitForFunction(() => !document.querySelector('#loginForm') && document.querySelector('#app').children.length > 0);
  const ready = await p.evaluate(src);
  const out = { ready };
  for (const step of JSON.parse(plan)) {
    if (step.hash) {                       // the coordinator's tab may show any screen when a phase starts
      await p.evaluate((h) => { location.hash = h; }, step.hash);
      await p.waitForSelector(step.wait);
      continue;
    }
    if (step.slow) {                       // a slow device: delay matching requests made by the page
      await p.route(step.slow.glob, async (route) => {
        const req = route.request();
        if (req.method() === step.slow.method && !(step.slow.except && req.url().includes(step.slow.except))) {
          await new Promise((res) => setTimeout(res, step.slow.ms));
        }
        try { await route.continue(); } catch (e) { /* the page aborted it */ }
      });
      continue;
    }
    const t0 = Date.now();
    out[step.as || step.fn] = await p.evaluate(async ([fn, args]) => {
      const a = args.map((x) => (x === '$snap' ? window.__snap : x));
      const r = await window.visit[fn](...a);
      if (r.snapshot && fn === 'pre') window.__snap = r.snapshot;
      return { md: r.md, rows: r.rows, snapshot: r.snapshot || null };
    }, [step.fn, step.args || []]);
    out[step.as || step.fn].ms = Date.now() - t0;
  }
  out.errors = errors;
  out.stored = await p.evaluate(() => { try { return !!localStorage.getItem('visit.snapshot'); } catch (e) { return null; } });
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestRunnerInBrowser(APICase):
    """The phases on a real server and a real page: the coordinator's only way in on visit day."""

    def run_plan(self, plan):
        import uvicorn
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error"))
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            r = subprocess.run([shutil.which("node"), "-e", DRIVER, f"http://127.0.0.1:{port}", "owner", OWNER_PW,
                                str(RUNNER), json.dumps(plan)],
                               capture_output=True, text=True, timeout=240, cwd=str(Path(__file__).parent))
        finally:
            server.should_exit = True
            t.join(10)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    @staticmethod
    def results(phase):
        return {r["id"]: r["result"] for r in phase["rows"]}

    def setUp(self):
        super().setUp()
        p = mock.patch("app.core.updater.latest_sha", return_value="f" * 40)
        p.start()
        self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {"PHARM_BOOT_ID": "boot-before"})
        env.start()
        self.addCleanup(env.stop)

    def open_shift(self):
        from app.sales.cash import CashDrawer
        CashDrawer(self.db).open(self.ctx, 0)

    def test_pre_post_flows_and_print(self):
        self.open_shift()  # every store sale needs an open shift (final review P2-2)
        from app.sales.service import Sales
        cust = Sales(self.db).create_customer(self.ctx, name="عميل حقيقي", credit_limit=1000)
        before_customers = self.db.one("SELECT count(*) c FROM customers")["c"]
        out = self.run_plan([{"fn": "pre"}, {"fn": "post", "args": ["$snap", {"sameBoot": True}], "as": "post_same"},
                             {"fn": "flows"}, {"fn": "print"}, {"fn": "print", "args": ["sale"], "as": "print_sale"},
                             {"fn": "post", "args": ["$snap", {"sameBoot": True}], "as": "post_after_flows"}])
        self.assertEqual(out["errors"], [])
        self.assertTrue(out["stored"])
        pre = self.results(out["pre"])
        self.assertEqual(pre["B1 backup"], "PASS", out["pre"]["md"])
        self.assertEqual(pre["B2 health"], "PASS")
        self.assertEqual(pre["B2 counts"], "PASS", out["pre"]["md"])
        snap = out["pre"]["snapshot"]
        self.assertEqual((snap["boot_id"], snap["customers"]["n"], snap["items"]["n"]), ("boot-before", 1, 1))
        self.assertTrue(snap["backup"]["name"].startswith("pharmacy-backup-"))
        self.assertTrue(list((self.tmp / "backups").glob(snap["backup"]["name"])))
        post = self.results(out["post_same"])
        self.assertEqual(post["B5 data"], "PASS", out["post_same"]["md"])
        self.assertEqual((post["B5 boot_id"], post["B6 channel"], post["B14 settings"]), ("PASS", "PASS", "PASS"))
        self.assertFalse([k for k, v in post.items() if v == "FAIL"], out["post_same"]["md"])

        fl = self.results(out["flows"])
        self.assertEqual(fl, {"B9 default": "PASS", "B9 double click": "PASS", "B9 retry": "PASS",
                              "L2 cut-off file": "PASS", "L3 complete single-store file": "PASS",
                              "L3 cleanup": "INFO"}, out["flows"]["md"])
        for phase in ("pre", "post_same", "flows"):
            self.assertLess(out[phase]["ms"], 25000, phase)
        rets = self.db.all("SELECT r.refund_method, r.total FROM sale_returns r JOIN sales s ON s.id=r.sale_id")
        self.assertEqual([(x["refund_method"], x["total"]) for x in rets], [("credit", 1000), ("credit", 1000)])
        self.assertEqual(self.db.one("SELECT status FROM import_runs")["status"], "discarded")
        self.assertFalse(self.db.one("SELECT count(*) c FROM cash_movements")["c"], "no money moved")
        # synthetic records only: one TEST customer and one TEST item were added, the real ones untouched
        names = [r["name"] for r in self.db.all("SELECT name FROM customers ORDER BY created_at")]
        self.assertEqual(len(names), before_customers + 1)
        self.assertTrue(names[-1].startswith("TEST-VISIT"))
        self.assertEqual(Sales(self.db).balance(self.ctx, cust), 0)
        self.assertEqual(self.db.one("SELECT count(*) c FROM items WHERE name_en LIKE 'TEST-VISIT%'")["c"], 1)

        menu = self.results(out["print"])
        self.assertEqual(set(menu.values()), {"SKIP"})
        self.assertEqual(self.results(out["print_sale"]), {"B8a cash sale": "SKIP"})
        # after the flows the same snapshot shows exactly what they added (TEST sales and stock)
        after = self.results(out["post_after_flows"])
        self.assertEqual(after["B5 data"], "FAIL")
        self.assertEqual(after["B5 rows.sales"], "FAIL")
        self.assertEqual(after["B14 settings"], "PASS")

    def test_return_flow_never_touches_the_invoice_already_on_screen(self):
        """Codex P1 on #141: the previous invoice stays in the DOM while the TEST one loads; a slow load
        must not make the runner fill and click the real invoice's return form."""
        self.open_shift()  # every store sale needs an open shift (final review P2-2)
        from app.sales.service import Sales
        from app.inventory import service as inv
        from decimal import Decimal
        from app.catalog import service as cat
        it = cat.create_item(self.db, self.ctx, {"name_en": "Real Item", "pack_size": 3, "public_price": 3000})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "R1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=30, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5"))
        s = Sales(self.db)
        cust = s.create_customer(self.ctx, name="عميل حقيقي", credit_limit=100000)
        real = s.create(self.ctx, customer_id=cust)
        d = s.add_line(self.ctx, real, it, units=3)
        s.complete(self.ctx, real, on_credit=d["total"])
        out = self.run_plan([{"hash": f"#/sales/{real}", "wait": "#rMethod"},
                             {"slow": {"glob": "**/api/sales/*", "method": "GET", "except": real, "ms": 1500}},
                             {"fn": "flows", "args": [{"ui": True}]}])
        self.assertFalse(self.db.all("SELECT id FROM sale_returns WHERE sale_id=?", (real,)), "a real invoice got a return")
        res = self.results(out["flows"])
        self.assertEqual((res["B9 default"], res["B9 double click"]), ("PASS", "PASS"), out["flows"]["md"])
        self.assertEqual(s.balance(self.ctx, cust), 3000)

    def test_a_slow_device_stays_inside_the_phase_budget(self):
        """Codex P2 on #141: nested requests share the phase budget, so a hung upload cannot push a call
        past ~25 s (the coordinator's limit is ~40 s) and the result still comes back."""
        self.open_shift()  # every store sale needs an open shift (final review P2-2)
        out = self.run_plan([{"slow": {"glob": "**/api/imports", "method": "POST", "ms": 60000}},
                             {"fn": "flows", "args": [{"ui": False}]}])
        self.assertLess(out["flows"]["ms"], 26500)
        res = self.results(out["flows"])
        self.assertEqual(res["B9 retry"], "PASS", out["flows"]["md"])
        self.assertEqual(res["L2 cut-off file"], "FAIL")
        self.assertIn("timeout", out["flows"]["md"])
        self.assertNotIn("PASS", [res.get("L3 complete single-store file")])

    def test_discard_import_only_takes_the_runners_own_draft(self):
        from tests.test_estock import stock_rows, xlsx
        self.http_login()
        r = self.c.post("/api/imports", headers=H, files={"file": ("stock.xlsx", xlsx(stock_rows()))},
                        data={"kind": "opening_stock", "source_system": "estock"})
        self.assertEqual(r.status_code, 201, r.text)
        run = r.json()["id"]
        out = self.run_plan([{"fn": "discardImport", "args": [run]}])
        self.assertEqual(self.results(out["discardImport"]), {"L3 cleanup": "FAIL"})
        self.assertIn("not a TEST draft", out["discardImport"]["md"])
        self.assertEqual(self.db.one("SELECT status FROM import_runs WHERE id=?", (run,))["status"], "analyzed")

    def test_post_reports_a_new_boot_and_each_difference(self):
        self.http_login()
        out = self.run_plan([{"fn": "pre", "args": [{"backup": False}]}])
        snap = out["pre"]["snapshot"]
        os.environ["PHARM_BOOT_ID"] = "boot-after"           # the app restarted (install)
        self.c.put("/api/settings/receipt", json={"label_shift": -2}, headers=H)
        out = self.run_plan([{"fn": "post", "args": [snap, {"channel": "main"}]}])
        post = self.results(out["post"])
        self.assertEqual(post["B5 boot_id"], "PASS", out["post"]["md"])
        self.assertEqual(post["B6 channel"], "PASS")
        self.assertEqual(post["B5 receipt.label_shift"], "FAIL")
        self.assertEqual(post["B14 settings"], "FAIL")
        self.assertIn("0 → -2", out["post"]["md"])
        self.assertEqual(post["B5 page"], "PASS", out["post"]["md"])
        same = self.run_plan([{"fn": "post", "args": [snap, {"sameBoot": True, "audit": 3}]}])
        self.assertEqual(self.results(same["post"])["B5 boot_id"], "FAIL")
        self.assertEqual(self.results(same["post"])["B13 audit"], "PASS", same["post"]["md"])   # settings save + 2 logins

    def req(self, method, url, expected=200, **kw):
        r = self.c.request(method, url, headers=H, **kw)
        self.assertEqual(r.status_code, expected, r.text)
        return r.json()

    def sale(self, lines, *, customer=None, cash=0, credit=0, tag):
        """As tests/test_day19_script.py: one unit at a time, as the cashier enters them."""
        sid = self.req("POST", "/api/sales", expected=201, json={"customer_id": customer})["id"]
        draft = None
        for item, units, disc in lines:
            for _ in range(units):
                draft = self.req("POST", f"/api/sales/{sid}/lines", json={"item_id": item, "units": 1, "discount_bp": disc})
        findings = self.req("POST", f"/api/sales/{sid}/check", json={})["findings"]
        return self.req("POST", f"/api/sales/{sid}/complete",
                        json={"paid_cash": cash, "on_credit": credit, "version": draft["version"],
                              "safety_decisions": {str(i): "ok" for i in range(len(findings))},
                              "request_id": "day19-" + tag})

    def test_day19_figures(self):
        """The #19 end-of-day reconciliation: the same entries as DAY19_SCRIPT.md give all PASS."""
        from app.catalog import service as cat
        a = cat.create_item(self.db, self.ctx, {"name_ar": "صنف تجربة أ", "pack_size": 3, "public_price": 3100})
        b = cat.create_item(self.db, self.ctx, {"name_ar": "صنف تجربة ب", "pack_size": 1, "public_price": 5000})
        sup = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        self.http_login()
        req = self.req
        expiry = (date.today() + timedelta(days=700)).isoformat()
        iid = req("POST", "/api/purchases", expected=201, json={"supplier_id": sup, "invoice_no": "TEST-19"})["invoice"]["id"]
        for item, qty, bonus, net, public, batch in ((a, 10, 2, 2000, 3100, "T19A"), (b, 5, 0, 4000, 5000, "T19B")):
            v = req("GET", f"/api/purchases/{iid}")["invoice"]["version"]
            req("POST", f"/api/purchases/{iid}/lines", json={"version": v, "line": {
                "raw_name": "x", "item_id": item, "qty": qty, "bonus_qty": bonus, "unit_net_price": net,
                "public_price": public, "line_total": qty * net, "batch_no": batch, "expiry_date": expiry}})
        req("POST", f"/api/purchases/{iid}/approve", json={"version": req("GET", f"/api/purchases/{iid}")["invoice"]["version"]})
        req("POST", "/api/cash/open", expected=201, json={"opening_cash": 10000})
        sale = self.sale
        s1 = sale([(a, 3, 0)], cash=3100, tag="s1")
        sale([(b, 1, 1000)], cash=4500, tag="s2")
        req("POST", f"/api/sales/{s1['id']}/return", json={"lines": {s1["lines"][0]["id"]: 1}, "refund_method": "cash", "reason": "تجربة"})
        cust = req("POST", "/api/customers", expected=201, json={"name": "عميل تجربة", "credit_limit": 20000})["id"]
        sale([(b, 1, 0)], customer=cust, credit=5000, tag="s3")
        req("POST", f"/api/customers/{cust}/payments", json={"amount": 2000, "method": "cash"})
        cats = req("GET", "/api/expenses")
        req("POST", "/api/expenses", expected=201, json={"category_id": cats["categories"][0]["id"], "amount": 1500,
                                                        "paid_from": "drawer", "note": "تجربة"})
        req("POST", f"/api/items/{a}/adjust", json={"qty_delta": -1, "reason": "damaged", "note": "تجربة"})
        req("POST", f"/api/suppliers/{sup}/pay", json={"amount": 10000, "method": "cheque", "ref": "123456"})
        out = self.run_plan([{"fn": "day19", "as": "open"}])
        res = self.results(out["open"])
        self.assertFalse([k for k, v in res.items() if v != "PASS"], out["open"]["md"])
        self.assertGreaterEqual(len(res), 13)
        # the close as the screen does it: notes and coins, smallest coin 0.25, so 170.50 and a note (-0.16)
        closed = req("POST", "/api/cash/close", json={"denominations": {"10000": 1, "5000": 1, "2000": 1, "50": 1},
                                                      "note": "تجربة: كسور القروش"})
        self.assertEqual((closed["counted_cash"], closed["difference"]), (17050, -16))
        out = self.run_plan([{"fn": "day19", "as": "closed"}])
        res = self.results(out["closed"])
        self.assertEqual(res.get("D-close"), "PASS", out["closed"]["md"])
        self.assertFalse([k for k, v in res.items() if v != "PASS"], out["closed"]["md"])
        # a close with any other difference is a real one: reported, not hidden
        req("POST", "/api/cash/open", expected=201, json={"opening_cash": 0})
        req("POST", "/api/cash/close", json={"denominations": {"100": 1}, "note": "تجربة: فرق حقيقي"})
        out = self.run_plan([{"fn": "day19", "as": "short"}])
        self.assertEqual(self.results(out["short"]).get("D-close"), "FAIL", out["short"]["md"])
        # a wrong figure is reported, not hidden
        req("POST", f"/api/items/{b}/adjust", json={"qty_delta": -1, "reason": "damaged", "note": "تجربة"})
        out = self.run_plan([{"fn": "day19", "as": "wrong"}])
        self.assertEqual(self.results(out["wrong"])["D-stock-b"], "FAIL")
