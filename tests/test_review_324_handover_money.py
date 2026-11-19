"""#324 items handed to the money session: R10 (reports through off_loop), X25 (login only from the program's own
screen), X26 sales half (a transfer proof is read only from the current upload folder), B8 (the transfers report
narrows its sub-queries by org), S30 on the screen (the return proposal knows the supplier). Each test failed on
the code before it. Synthetic data only."""
import asyncio
from pathlib import Path

from app.core import audit
from app.core.errors import NotFound
from app.procurement.service import Procurement
from app.sales import attachments as att
from app.sales import contracts as k
from tests.test_253_no_full_reads import plan
from tests.test_api import APICase, H
from tests.test_attachments import PNG
from tests.test_cashier_acceptance import Base

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app" / "web" / "app.js").read_text(encoding="utf-8")


class TestR10ReportsOffTheLoop(APICase):
    def spy(self, owner, name):
        real, seen = getattr(owner, name), {}

        def wrapped(*a, **kw):
            try:
                asyncio.get_running_loop()
                seen["on_loop"] = True
            except RuntimeError:
                seen["on_loop"] = False
            return real(*a, **kw)

        setattr(owner, name, wrapped)
        self.addCleanup(setattr, owner, name, real)
        return seen

    def test_each_report_runs_in_a_worker_thread(self):
        self.http_login()
        cid = k.save(self.db, self.ctx, {"name": "نقابة تجربة"})[0]["id"]
        d = "2026-01-01"
        for owner, name, path in ((k, "report", f"/api/contracts/{cid}/report?from={d}&to={d}"),
                                  (Procurement, "suggest", "/api/procurement/suggestions"),
                                  (Procurement, "stock_health", "/api/procurement/health"),
                                  (Procurement, "supplier_costs", f"/api/procurement/suppliers/{self.pan}"),  # an item's suppliers
                                  (audit, "listing", f"/api/audit?from={d}&to={d}")):
            with self.subTest(path=path):
                seen = self.spy(owner, name)
                r = self.c.get(path, headers=H)
                self.assertEqual(r.status_code, 200, r.text[:300])
                self.assertIs(seen.get("on_loop"), False)


class TestX25LoginFromTheProgramOnly(APICase):
    def test_a_form_post_from_another_site_cannot_sign_in(self):
        body = '{"username": "owner", "password": "x"}'
        r = self.c.post("/api/auth/login", content=body, headers={"Content-Type": "text/plain"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "csrf"))
        self.assertNotIn("set-cookie", r.headers)

    def test_the_program_and_json_clients_still_sign_in(self):
        self.http_login()                                                  # the screen's header
        from tests.helpers import OWNER_PW
        r = self.c.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW})
        self.assertEqual(r.status_code, 200, r.text)                       # a JSON client (needs a preflight)


class TestX26ProofFromTheUploadFolderOnly(Base):
    def test_a_stored_path_outside_the_folder_is_never_read(self):
        sid = self.s.create(self.ctx)
        up = self.tmp / "up"
        a = att.attach(self.db, up, self.ctx, sid, PNG)["attachments"][0]["id"]
        outside = self.tmp / "elsewhere.png"
        outside.write_bytes(PNG + b"secret")
        with self.db.transaction():
            self.db.execute("UPDATE source_files SET storage_path=? WHERE id=(SELECT file_id FROM sale_attachments"
                            " WHERE id=?)", (str(outside), a))
        with self.assertRaises(NotFound):                               # no file of that name in the folder
            att.file(self.db, up, self.ctx, a)
        (up / self.ctx.org_id / "transfers" / "elsewhere.png").write_bytes(PNG)
        data, _ = att.file(self.db, up, self.ctx, a)                    # the one in the current folder
        self.assertEqual(data, PNG)


class TestB8TransfersReportByOrg(Base):
    def test_the_sub_queries_use_the_org_index(self):
        real, seen = self.db.all, []
        self.db.all = lambda sql, *a, **kw: (seen.append((sql, a[0] if a else ())), real(sql, *a, **kw))[1]
        try:
            att.transfers(self.db, self.ctx, "2026-01-01", "2026-01-02")
        finally:
            del self.db.all
        sql, params = next(q for q in seen if "sale_attachments" in q[0])
        p = plan(self.db, sql, params)
        self.assertNotIn("SCAN a", p, p)
        self.assertIn("ix_sale_attachments", p, p)


class TestS30ReturnProposalKnowsTheSupplier(APICase):
    def test_the_route_passes_the_supplier(self):
        from app.procurement import returns
        got = {}
        real = returns.suggest_value

        def spy(db, ctx, item, batch, supplier_id=None):
            got["supplier"] = supplier_id
            return 0
        returns.suggest_value = spy
        self.addCleanup(setattr, returns, "suggest_value", real)
        self.http_login()
        r = self.c.get(f"/api/supplier-returns/suggest?item={self.pan}&batch=&supplier=s-1", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(got["supplier"], "s-1")

    def test_the_screen_sends_it_and_asks_again_when_it_changes(self):
        start = JS.index("async function viewSupplierReturns(")
        body = JS[start:JS.index("\nasync function ", start + 10)]
        self.assertEqual(body.count("/api/supplier-returns/suggest?"), body.count("&supplier="))
        self.assertIn('$("#srSup")?.addEventListener("change"', body)


class TestCodexReview368(APICase):
    """#368 Codex P1: an answer for the supplier chosen before must not land on the lines after the supplier
    changed, and the return waits until the new prices are in."""

    def test_an_older_supplier_answer_is_dropped(self):
        import json
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        start = JS.index("// supplier-return proposals")
        fn = JS[start:JS.index("\n}\n", JS.index("async function srPropose(")) + 3]
        out = subprocess.run(["node", "-e", """
const waits = {};
async function api(path) { const s = new URL("http://x" + path).searchParams.get("supplier");
  return new Promise((r) => { waits[s] = () => r({ unit_value: s === "A" ? 100 : 200 }); }); }
""" + fn + """
(async () => {
  const l = { item: { id: "i" }, batch_id: null, unit_value: 0 };
  const a = srPropose(l, "A");
  SR.gen++;
  const b = srPropose(l, "B");
  waits.B(); await b; waits.A(); await a;
  console.log(JSON.stringify(l.unit_value));
})();
"""], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), 200)

    def test_the_return_waits_for_the_new_prices(self):
        start = JS.index("async function viewSupplierReturns(")
        body = JS[start:JS.index("\nasync function ", start + 10)]
        at = body.index('$("#srSup")?.addEventListener("change"')
        self.assertIn('$("#srGo").disabled = true', body[at:at + 600])
        self.assertIn("if (SR.busy) return;", body[body.index('$("#srGo").addEventListener("click"'):][:200])


class TestCodexReview368b(APICase):
    """#368 Codex P1 (second): a proposal that fails for the new supplier must not leave the old supplier's price
    to be sent. Only a value the person typed is sent; any proposed value is left to the server, which prices
    the line at this supplier's own purchase price."""

    def run_js(self, script):
        import json
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        start = JS.index("// supplier-return proposals")
        fn = JS[start:JS.index("\n}\n", JS.index("async function srPropose(")) + 3]
        out = subprocess.run(["node", "-e", fn + script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_failed_proposal_leaves_no_old_price(self):
        out = self.run_js("""
let fail = false;
async function api() { if (fail) throw new Error("offline"); return { unit_value: 1200 }; }
(async () => {
  const l = { item: { id: "i" }, batch_id: null, unit_value: 0 }, typed = { item: { id: "j" }, unit_value: 1500, typed: true };
  await srPropose(l, "A"); const a = l.unit_value;
  SR.gen++; fail = true; await srPropose(l, "B"); await srPropose(typed, "B");
  console.log(JSON.stringify([a, l.unit_value, typed.unit_value]));
})();
""")
        self.assertEqual(out, [1200, None, 1500])            # A's 1200 is gone; a typed value stays

    def test_only_a_typed_value_is_sent(self):
        start = JS.index("async function viewSupplierReturns(")
        body = JS[start:JS.index("\nasync function ", start + 10)]
        self.assertIn("...(money_ && l.typed ? { unit_value: l.unit_value } : {})", body)
        self.assertIn("l.typed = true", body)


class TestCodexReview368c(TestCodexReview368b):
    """#368 Codex P1 (third): a value typed while a proposal is still on its way is kept."""

    test_a_failed_proposal_leaves_no_old_price = None
    test_only_a_typed_value_is_sent = None

    def test_a_value_typed_during_the_ask_is_kept(self):
        out = self.run_js("""
let done;
async function api() { return new Promise((r) => { done = () => r({ unit_value: 1800 }); }); }
(async () => {
  const l = { item: { id: "i" }, batch_id: null, unit_value: 0 };
  const p = srPropose(l, "B");
  l.unit_value = 1500; l.typed = true;                      // typed before the answer came
  done(); await p;
  console.log(JSON.stringify(l.unit_value));
})();
""")
        self.assertEqual(out, 1500)


TYPING = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, item, supB] = process.argv.slice(1);
""" + __import__("tests.pos_browser", fromlist=["LOGIN"]).LOGIN + r"""
  let release, sent = null;
  const held = new Promise((r) => { release = r; });
  await p.route('**/api/supplier-returns/suggest*', async (route) => {
    if (route.request().url().includes('supplier=' + supB)) { await held; return route.fulfill({ json: { unit_value: 1800 } }); }
    return route.fulfill({ json: { unit_value: 1200 } });
  });
  await p.route('**/api/supplier-returns', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    sent = JSON.parse(route.request().postData());
    return route.fulfill({ status: 422, json: { error: { code: 'x', message: 'stop' } } });
  });
  p.on('popup', (w) => w.close().catch(() => {}));
  await p.goto(base + '/#/supplier-returns?item=' + item);
  await p.waitForSelector('#srLines [data-sv="0"]');
  await p.waitForFunction(() => document.querySelector('#srLines [data-sv="0"]').value === '12.00');   // the first proposal is in
  await p.selectOption('#srSup', supB);                         // B's price is now on its way
  await p.click('#srLines [data-sv="0"]');
  await p.keyboard.press('Control+A');
  await p.keyboard.type('15');                                  // typed, the field still focused
  release();
  await p.waitForFunction(() => !document.querySelector('#srGo').disabled);
  await p.click('#srGo');
  await p.waitForFunction(() => true);
  for (let i = 0; i < 50 && !sent; i++) await p.waitForTimeout(100);
  console.log(JSON.stringify({ value: await p.inputValue('#srLines [data-sv="0"]'), sent }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


from tests.helpers import needs_browser  # noqa: E402


@needs_browser
class TestCodexReview368d(APICase):
    """#368 Codex P1 (fourth): a value typed while the proposal is pending, the field still focused, is the one
    shown and sent. Run in the browser with the proposal answer held back."""

    def test_typing_during_the_ask_wins(self):
        from datetime import date, timedelta
        from decimal import Decimal

        from app.catalog import service as cat
        from app.inventory import service as inv
        from tests.helpers import OWNER_PW
        from tests.pos_browser import run_node
        with self.db.transaction():
            bt = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B1", (date.today() + timedelta(days=60)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=10, batch_id=bt, movement_type="purchase",
                              unit_cost=Decimal("10"))
        sup_b = cat.create_supplier(self.db, self.ctx, name="مورد تجربة ب")
        out = run_node(self, TYPING, "owner", OWNER_PW, self.pan, sup_b)
        self.assertIn(out["value"], ("15", "15.00"))
        self.assertEqual(out["sent"]["lines"][0].get("unit_value"), 1500)


class TestCodexReview368e(APICase):
    def test_each_keystroke_is_kept(self):
        start = JS.index("async function viewSupplierReturns(")
        body = JS[start:JS.index("\nasync function ", start + 10)]
        at = body.index('x.addEventListener("input"')
        self.assertIn("l.typed = true", body[at:at + 250])

    def test_a_redraw_keeps_the_focus_in_the_value_field(self):
        start = JS.index("async function viewSupplierReturns(")
        body = JS[start:JS.index("\nasync function ", start + 10)]
        self.assertIn("const focused = document.activeElement?.dataset?.sv;", body)
        self.assertIn("x.focus()", body)
